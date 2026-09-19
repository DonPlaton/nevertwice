#!/usr/bin/env python3
"""Register how far two draws of the SAME commit disagree - the number R1's clause 3 rests on.

Clause 3 asks for a third draw when the union of failing case ids is larger than the base's. The
alternative, "any id the base did not have", was rejected because the draws disagree so often that
it would fire on nearly every run. How often is not a matter of recollection: it is in the
artifacts, and this puts it in the register so the argument has a pointer like every other number.

    python tools/register_divergence.py --dry-run
    python tools/register_divergence.py --apply

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "research" / "evidence_manifest.json"
RAW = "research/results/draw_divergence.json"

#: artifact key -> (claim suffix, the corpus in words)
ARTIFACTS = {
    "supersession_v1": ("explicit", "the explicit corpus at the post-J2b engine"),
    "supersession_v1_implicit": ("explicit_implicit", "the implicit corpus at the same engine"),
    "supersession_baseline_ef8120d": ("baseline", "the explicit corpus at the pre-J2b baseline"),
    "supersession_baseline_ef8120d_implicit": ("baseline_implicit",
                                               "the implicit corpus at that baseline"),
}


def wilson(k: int, n: int, z: float = 1.959963985) -> dict:
    """A Wilson interval on the RATE, which is the scale `value` carries.

    The count is what reads naturally in prose ("16 of 80") and it is in the statement, but a
    claim's `ci` has to be on the same scale as its `value` or it is an interval about a different
    quantity. This project has twice shipped a threshold written on the wrong scale; it does not
    need a third.
    """
    if n <= 0:
        return {"level": 0.95, "low": 0.0, "high": 0.0, "method": "wilson"}
    ph = k / n
    d = 1 + z * z / n
    centre = (ph + z * z / (2 * n)) / d
    half = z * ((ph * (1 - ph) / n + z * z / (4 * n * n)) ** 0.5) / d
    return {"level": 0.95, "low": round(max(0.0, centre - half), 4),
            "high": round(min(1.0, centre + half), 4), "method": "wilson"}


def head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                          capture_output=True, text=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--dry-run", action="store_true")
    grp.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    raw = json.loads((ROOT / RAW).read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    claims = manifest["claims"]
    as_list = isinstance(claims, list)
    have = {c.get("id") for c in (claims if as_list else claims.values())}
    commit = head()

    added, skipped = [], []
    for key, (suffix, words) in ARTIFACTS.items():
        row = (raw.get("artifacts") or {}).get(key)
        if not row:
            print(f"no record for {key} in {RAW}")
            return 2
        cid = f"instrument.draw_divergence.{suffix}"
        if cid in have:
            skipped.append(cid)
            continue
        claim = {
            "id": cid,
            "dataset": "supersession_v1",
            "environment": "local_supersession_stand",
            "raw": RAW,
            "command": "python tools/draw_divergence.py",
            "commit": commit,
            "pointer": f"artifacts.{key}.rate",
            "value": round(row["diverging"] / row["cases"], 4) if row["cases"] else 0.0,
            "n": row["cases"],
            "unit": "rate",
            "printed": [f"{row['diverging'] / row['cases']:.3f}" if row["cases"] else "0.000"],
            "ci": wilson(row["diverging"], row["cases"]),
            "statement": (f"On {words}, {row['diverging']} of {row['cases']} cases change outcome "
                          f"between the two draws of the same commit - the instrument noise R1's "
                          f"clause 3 is calibrated against"),
            "note": ("Counted over every outcome field the stand records (current_retired, "
                     "stale_returned, current_returned, old_value_served, current_demoted, "
                     "current_absent) on the cases present in both draws. This is why clause 3 "
                     "tests the SIZE of the failing union rather than whether it contains an id "
                     "the base did not: at this divergence a set test would demand a third draw "
                     "on nearly every run, and a rule that always fires is not a signal."),
            "cited_in": [],
            "cited_in_pending": [],
        }
        added.append(claim)

    print(f"HEAD {commit[:7]}")
    for c in added:
        print(f"  + {c['id']:44} {c['value']}  ({c['printed'][0]})")
    for cid in skipped:
        print(f"  = {cid} (already registered)")
    if not added:
        print("nothing to add")
        return 0
    if args.dry_run:
        print("\n(dry run - nothing written)")
        return 0

    if as_list:
        claims.extend(added)
    else:
        for c in added:
            claims[c["id"]] = c
    MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"\nwritten: {len(added)} claim(s). Run `python tools/produced_by.py --write` next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
