#!/usr/bin/env python3
"""Record, for every claim that indexes a list by position, the shape of that list.

A list index is an address only while the list keeps its shape. `remeasure.pair_mismatch` covers
the thirty-six claims whose id names the two arms it compares (`mem0_vs_naive`); the other
seventy-two carry no such signature - `recall_sweep[4].threshold`, `by_k[0].recall_at_k`,
`arms["nevertwice"].per_run[0]` - and for those the only comparable fact is the shape of the
list itself. Insert one threshold before index 4 and thirty-three abstention claims move to a
neighbouring row, resolving and matching all the way.

So the shape is stamped here, once, from the COMMITTED artifact - the same file the claims' values
were read from - and `remeasure.restore` refuses a claim whose list has changed shape since.

    python tools/stamp_shapes.py --check    # compare recorded shapes with the artifacts, exit 1 on drift
    python tools/stamp_shapes.py            # stamp claims that have no shape yet

What this does NOT cover, stated with its number rather than left to be discovered: a list that
keeps its length and its keys while its ROWS are reordered. Shape is blind to that, and so is
every other check here. Of the 108, the 36 pairs are covered by name; the rest rest on shape.

Standard library only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import remeasure as rm  # noqa: E402

INDEXED = re.compile(r"\[\d+\]")


def claims_of(manifest: dict) -> list[dict]:
    c = manifest["claims"]
    return list(c.values() if isinstance(c, dict) else c)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="compare recorded shapes with the artifacts and change nothing")
    args = ap.parse_args(argv)

    manifest = json.loads(rm.MANIFEST.read_text(encoding="utf-8"))
    claims = claims_of(manifest)
    cache: dict[str, object] = {}

    stamped, drifted, unreadable, already = 0, [], [], 0
    for c in claims:
        ptr, raw = c.get("pointer") or "", (c.get("raw") or "").replace("\\", "/")
        if not (ptr and raw and INDEXED.search(ptr)):
            continue
        if raw not in cache:
            p = ROOT / raw
            try:
                cache[raw] = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
                cache[raw] = e
        data = cache[raw]
        if isinstance(data, Exception):
            unreadable.append(f"{c['id']}: {raw} ({type(data).__name__})")
            continue
        try:
            shape = rm.list_shape(data, ptr)
        except (KeyError, IndexError, TypeError) as e:
            unreadable.append(f"{c['id']}: pointer {ptr} ({type(e).__name__})")
            continue
        if c.get("shape"):
            already += 1
            if c["shape"] != shape:
                drifted.append(f"{c['id']}: recorded {c['shape']} -> artifact {shape}")
            continue
        if not args.check:
            c["shape"] = shape
        stamped += 1

    print(f"positional claims stamped now : {stamped}")
    print(f"already carrying a shape      : {already}")
    print(f"drifted from their artifact   : {len(drifted)}")
    for d in drifted[:10]:
        print(f"    {d}")
    print(f"unreadable                    : {len(unreadable)}")
    for u in unreadable[:10]:
        print(f"    {u}")

    if args.check:
        return 1 if (drifted or unreadable) else 0
    if stamped:
        rm.save(manifest)
        print(f"wrote {rm.MANIFEST.relative_to(ROOT).as_posix()}")
    return 1 if unreadable else 0


if __name__ == "__main__":
    raise SystemExit(main())
