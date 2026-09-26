#!/usr/bin/env python3
"""The shipped universal guard pack, counted read-only - and stamped like every register artifact.

    python research/guards_pack.py --out research/results/guards_pack.json

The number is the product's own: `python -m nevertwice.guards pack --count --out FILE` writes it.
The product does not import research tooling, so it cannot stamp `measured_at` on what it wrote;
restore #2 had to date `guards_pack.json` by its file mtime against the campaign log. This stand
runs that same CLI in-process, then adds the one stamp every register artifact carries
(`_provenance.stamp`: {commit, utc, dirty}) - (б) b-c, the auditor's G5.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import sandbox_guard  # noqa: E402 - one store sandbox for the whole repo

sandbox_guard.isolate(prefix="nevertwice_guards_pack_")
import _provenance as prov  # noqa: E402
from nevertwice import guards  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = argv[argv.index("--out") + 1] if "--out" in argv and argv.index("--out") + 1 < len(argv) \
        else "research/results/guards_pack.json"
    saved = sys.argv
    sys.argv = [saved[0], "pack", "--count", "--out", out]
    try:
        guards.main()                        # the product's own read-only count, unchanged
    finally:
        sys.argv = saved
    p = Path(out)
    data = json.loads(p.read_text(encoding="utf-8"))
    p.write_text(json.dumps(prov.stamp(data), indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
