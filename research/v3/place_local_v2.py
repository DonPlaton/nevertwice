#!/usr/bin/env python3
"""PREREG-V3 O2 (the auditor, after A3.j j1): the local-v2 pins placed in the polygon, once.

The v2 pins v3 reuses (S4 LoCoMo, S9 LME-S v2) live at research/data/*.json, which is gitignored: they exist only in
the main working tree, never in a clean worktree, an anchor or a restore tree - j1 stopped on exactly that. This step
copies each one to <pins_root>/local-v2/<sha256>/<basename>, where corpus_pin_v3.location() points every reader:

* the source is copied only after its bytes are the pin's - size, then sha256 (L1 no source, L2 other bytes);
* the copy goes to a .partial, is re-hashed, and only then os.replace()d into its place (L4 a copy that is not the
  pinned bytes - removed, nothing placed);
* a file already at the place is never overwritten: identical is already-placed (P12), different is L3;
* the place record goes to <runs_root>/_fetch/local-v2/<run>/place_record.json, and a used run label is refused;
* a problem names the pin and a sha256 prefix, never a file's bytes.

    python research/v3/place_local_v2.py --run <label> --source-root <the main working tree>
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CP = _load("v3_corpus_pin_place", HERE / "corpus_pin_v3.py")


class PlaceRefused(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    """Streamed in 1 MiB blocks: hashlib's file helper is 3.11+, and CI runs 3.10 (76cb0e9, class B1)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _bound(path: Path, p: dict) -> str | None:
    """None when ``path`` is the pin's bytes (size, then sha256); else what it is, by a sha256 prefix only."""
    size = path.stat().st_size
    if size != p["bytes"]:
        return f"size {size} is not the pinned {p['bytes']}"
    got = _sha256(path)
    return None if got == p["sha256"] else f"sha256 {got[:12]}... is not the pinned {p['sha256'][:12]}..."


def place(source_root: Path, pins_root: Path, runs_root: Path, run: str, *, pins: dict | None = None,
          copy=shutil.copyfile) -> dict:
    """Place every local-v2 pin of the table; returns the place record, also written beside the run."""
    table = pins if pins is not None else CP.PINS
    base = Path(runs_root) / "_fetch" / "local-v2" / run
    if base.exists():
        raise PlaceRefused(f"the run label {run!r} was used before")
    base.mkdir(parents=True)
    record: dict = {"window": "local-v2", "run": run, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "source_root": str(source_root), "problems": [], "placed": {}}
    for name in sorted(n for n, p in table.items() if p["source"] == "local-v2"):
        p = table[name]
        src = Path(source_root) / Path(*p["path"].split("/"))
        dest = CP.location(name, hf_hub=None, pins_root=pins_root, pins=table)
        if Path(pins_root) not in dest.parents:
            raise PlaceRefused(f"{name}: location() does not point into pins_root - nothing is written outside it")
        if not src.is_file():
            record["problems"].append(f"L1 {name}: no source file at {p['path']}")
            continue
        why = _bound(src, p)
        if why:
            record["problems"].append(f"L2 {name}: the source {p['path']} is not the pin - {why}; never copied")
            continue
        entry = {"sha256": p["sha256"], "bytes": p["bytes"], "source": p["path"],
                 "dest": dest.relative_to(pins_root).as_posix()}
        if dest.exists():
            why = _bound(dest, p)
            if why:
                record["problems"].append(f"L3 {name}: another file is already at its place - {why}; never overwritten")
            else:
                record["placed"][name] = {"status": "already-placed", **entry}
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".partial")
        copy(src, partial)
        why = _bound(partial, p)
        if why:
            partial.unlink()
            record["problems"].append(f"L4 {name}: the copy is not the pin - {why}; nothing placed")
            continue
        os.replace(partial, dest)
        record["placed"][name] = {"status": "placed", **entry}
    (base / "place_record.json").write_bytes((json.dumps(record, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return record


def main(argv: list[str] | None = None) -> int:
    """place_local_v2.py --run <label> --source-root <the main working tree>: the source root has no default - it is
    the main working tree, the only place the gitignored v2 files exist, and the GO names it."""
    ap = argparse.ArgumentParser(description="place the local-v2 pins in the polygon (O2)")
    ap.add_argument("--run", required=True)
    ap.add_argument("--source-root", required=True)
    args = ap.parse_args(argv)
    L = _load("v3_launch", HERE / "launch.py")
    c = L.Contract.default()
    try:
        rec = place(Path(args.source_root), c.runs_root / "_pins", c.runs_root, args.run)
    except PlaceRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(json.dumps(rec, indent=1, sort_keys=True))
    return 0 if not rec["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
