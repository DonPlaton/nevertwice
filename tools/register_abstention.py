#!/usr/bin/env python3
"""Register the recall-abstention sweep (ledger C1) from `research/results/abstention_ab.json`.

The sweep table on `research/ABSTENTION_AB.md` was typed by hand from the artifact and stayed one
campaign behind it: the page said it was "read from" the artifact while printing another run's
numbers (found 2026-09-11). The page now carries a generated region, and this tool registers the
rows the region draws - one claim per threshold and column, plus the mean recall depth and the
re-mine byte counts the page quotes - under the same freshness rules as the other registrars.

    python tools/register_abstention.py [--at COMMIT] [--dry-run]

`--at COMMIT` registers further fields of an artifact the register already carries at COMMIT:
COMMIT must be an ancestor of HEAD, a live claim on the artifact must already carry it, and no
file in the command's closure may have changed after it.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "research" / "evidence_manifest.json"
sys.path.insert(0, str(ROOT / "tools"))
import git_status  # noqa: E402  the shared reading of `git status --porcelain -z`

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                      # noqa: BLE001
    pass

RAW = "research/results/abstention_ab.json"
COMMAND = "python research/abstention_ab.py --part all"
DATASET = "supersession_v1"
ENVIRONMENT = "local_supersession_stand"
#: The thresholds the page's table prints; the artifact carries more, identical above 0.35.
THRESHOLDS = (0.0, 0.1, 0.2, 0.35, 0.5, 0.75)
COLUMNS = (
    ("mean_chars", "mean_chars", "characters", "returns {v:.1f} characters per query", ["{v:.1f}"]),
    ("mean_hits", "mean_hits", "hits", "returns {v:.2f} hits per query", ["{v:.2f}"]),
    ("current_rate", "current_rate", "rate", "returns the wanted fact on {v:.3f} of the cases", ["{v:.3f}"]),
    ("char_reduction", "char_reduction_vs_off", "fraction", "cuts the payload by {pct:.1f}% against the mechanism off",
     ["{v:.3f}", "{pct:.1f}%", "{pct:.1f}"]),
    ("current_delta", "current_delta_vs_off", "rate delta", "moves the wanted-fact rate by {v:+.3f} against off "
     "({pts:.1f} points)", ["{v:+.3f}", "{v:.3f}", "{pts:.1f}"]),
)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _dirty_files() -> set[str]:
    #: One reading of `git status` for every registrar - `tools/git_status.py`, held by
    #: `tests/_test_git_status_parsing.py`. The copy that used to sit here (in ten files, three
    #: spellings) read a rename as a single path called "old -> new", kept the quotes git puts
    #: around any path with a space or a non-ascii byte, and turned that path's octal escapes
    #: into slashes. So `git mv` on a file inside a claim's closure left this guard blind.
    return git_status.dirty_files(ROOT)


def build_claims(art: dict, *, head: str, produced_by: list[str], existing: set[str]) -> list[dict]:
    base = {"dataset": DATASET, "environment": ENVIRONMENT, "command": COMMAND, "raw": RAW,
            "cited_in": [], "commit": head, "produced_by": list(produced_by), "ci": None}
    new: list[dict] = []

    def add(cid, statement, value, printed, unit, n, pointer, note=None):
        if cid in existing:
            return
        c = {"id": cid, "statement": statement, "value": value, "printed": list(printed), "unit": unit,
             "n": n, "pointer": pointer, **base}
        if note:
            c["note"] = note
        new.append(c)

    sweep = art.get("recall_sweep") or []
    by_thr = {float(r["threshold"]): (i, r) for i, r in enumerate(sweep)}
    for t in THRESHOLDS:
        if t not in by_thr:
            continue
        i, row = by_thr[t]
        key = str(t).replace(".", "_")                 # 0.0 -> t0_0, as the renderer keys it
        n = int(row.get("cases") or 0)
        for slug, field, unit, what, forms in COLUMNS:
            v = float(row[field])
            ctx = {"v": v, "pct": v * 100, "pts": -v * 100}
            add(f"abstention.recall.sweep.t{key}.{slug}",
                f"with the recall-abstention threshold at {t:.2f} the per-turn recall path {what.format(**ctx)} "
                f"on the {n} supersession_v1 cases where retrieval returned a hit",
                v, [f.format(**ctx) for f in forms], unit, n, f"recall_sweep[{i}].{field}")
    if art.get("mean_hits_returned") is not None:
        add("abstention.recall.mean_hits_returned",
            f"recall returns {art['mean_hits_returned']:.2f} hits per query on this corpus with every abstention "
            f"switch off - the depth the mechanisms had to work with",
            float(art["mean_hits_returned"]), [f"{art['mean_hits_returned']:.2f}"], "hits",
            int(art.get("captured_cases") or 0), "mean_hits_returned")
    rm = art.get("remine") or {}
    for slug, field, unit, what in (
            ("full_read_bytes", "full_read_bytes_total", "bytes", "a full re-read of the transcript reads {v:,} bytes"),
            ("delta_read_bytes", "delta_read_bytes_total", "bytes", "the delta reader reads {v:,} bytes"),
            ("events", "events", "events", "the delta re-mine stand appends {v:,} events"),
            ("stages", "stages", "stages", "the delta re-mine stand grows the transcript in {v} stages")):
        if rm.get(field) is not None:
            v = int(rm[field])
            add(f"abstention.remine.{slug}", what.format(v=v) + " across the growth stages",
                v, [f"{v:,}", str(v)], unit, int(rm.get("stages") or 0), f"remine.{field}")
    return new


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--at", metavar="COMMIT", default="")
    ap.add_argument("--manifest", default=str(MANIFEST_PATH))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    raw = ROOT / RAW
    if not raw.is_file():
        print(f"no artifact at {RAW} - run `{COMMAND}` first")
        return 2
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    import produced_by as pb                                     # noqa: PLC0415
    closure = pb.closure(COMMAND)
    if args.at:
        head = _git("rev-parse", args.at)
        if subprocess.run(["git", "merge-base", "--is-ancestor", head, "HEAD"], cwd=ROOT).returncode != 0:
            print(f"{args.at} is not an ancestor of HEAD")
            return 2
        if not any(c.get("raw") == RAW and c.get("commit") == head and not c.get("stale") for c in manifest["claims"]):
            print(f"no live claim on {RAW} carries {args.at}")
            return 2
        moved = [p for p in closure
                 if subprocess.run(["git", "merge-base", "--is-ancestor",
                                    _git("log", "-1", "--format=%H", "HEAD", "--", p), head],
                                   cwd=ROOT).returncode != 0]
        if moved:
            print(f"{moved[0]} changed after {args.at} - the artifact no longer describes HEAD's code")
            return 2
    else:
        head = _git("rev-parse", "HEAD")
        if raw.stat().st_mtime < int(_git("log", "-1", "--format=%ct", head)):
            print(f"{RAW} predates HEAD - re-run `{COMMAND}`")
            return 2
    dirty = sorted(p for p in closure if p in _dirty_files())
    if dirty:
        print(f"working tree modifies {dirty[0]} - commit first")
        return 2
    art = json.loads(raw.read_text(encoding="utf-8"))
    new = build_claims(art, head=head, produced_by=closure, existing={c["id"] for c in manifest["claims"]})
    for c in new:
        print(f"  + {c['id']} = {c['value']}")
    if args.dry_run or not new:
        print(f"{'would register' if args.dry_run else 'registered'} {len(new)} claim(s)")
        return 0
    manifest["claims"].extend(new)
    Path(args.manifest).write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"registered {len(new)} claim(s) at {head[:7]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
