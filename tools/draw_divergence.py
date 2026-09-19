#!/usr/bin/env python3
"""How far two draws of the SAME commit disagree, over the supersession stand's artifacts.

This is a MEASUREMENT module and nothing else. It carries no argument parser, no rendering and no
verdict logic, because everything in a claim's `produced_by` closure stales that claim when it
changes - and a claim should go stale when the measurement changes, not when someone edits a help
string. `tools/r1_verdict.py` imports from here rather than the other way round, so its CLI can
move without touching these numbers. That is the eighth time this tax was paid by hand; the rule
the project already writes for renderers - "the renderer is not the measurement" - covers argument
parsers too.

    python tools/draw_divergence.py            # writes research/results/draw_divergence.json

Standard library only.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "results" / "draw_divergence.json"

#: The two readings of one mode. `nevertwice_run2` is the stand's own name for the second draw.
DRAW_KEYS = ("nevertwice", "nevertwice_run2")

#: Every per-case outcome the stand records. Divergence is counted over all of them, not over one
#: metric, because the question is how unstable the stand is - not how unstable one column is.
OUTCOME_FIELDS = ("current_retired", "stale_returned", "current_returned",
                  "old_value_served", "current_demoted", "current_absent")

#: The four committed stand artifacts the figure is measured over. Named here rather than passed
#: in, so the command that reproduces it has no argument to get wrong.
DIVERGENCE_SET = ("supersession_v1", "supersession_v1_implicit",
                  "supersession_baseline_ef8120d", "supersession_baseline_ef8120d_implicit")


def rows_of(doc: dict, arm: str) -> list:
    return list(((doc.get("arms") or {}).get(arm) or {}).get("rows") or [])


def divergence(doc: dict) -> dict:
    """Cases whose outcome changes between the two draws, among those present in both."""
    a = {r.get("id"): r for r in rows_of(doc, DRAW_KEYS[0])}
    b = {r.get("id"): r for r in rows_of(doc, DRAW_KEYS[1])}
    both = sorted(set(a) & set(b))
    if not both:
        return {"cases": 0, "diverging": 0, "rate": 0.0, "by_field": {}}
    diverging = [i for i in both
                 if any(bool(a[i].get(f)) != bool(b[i].get(f)) for f in OUTCOME_FIELDS)]
    by_field = {f: sum(1 for i in both if bool(a[i].get(f)) != bool(b[i].get(f)))
                for f in OUTCOME_FIELDS}
    return {"cases": len(both), "diverging": len(diverging),
            "rate": round(len(diverging) / len(both), 4),
            "by_field": {f: n for f, n in by_field.items() if n}}


def divergence_all(root: Path = ROOT) -> dict:
    out = {"measured_by": "python tools/draw_divergence.py",
           "what": "cases whose outcome changes between the two draws of the SAME commit",
           "artifacts": {}}
    for key in DIVERGENCE_SET:
        doc = json.loads((root / "research" / "results" / f"{key}.json").read_text(encoding="utf-8"))
        out["artifacts"][key] = divergence(doc)
    return out


def main() -> int:
    rec = divergence_all()
    for key, d in rec["artifacts"].items():
        print(f"  {key:42} {d['diverging']:3} of {d['cases']}  ({d['rate']})")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    #: newline="\n" is not cosmetic: without it this command rewrites the artifact
    #: with CRLF on Windows, so the documented reproduction step leaves a modified file in
    #: `git status` while `git diff` is empty. `tools/produced_by.py` pays the same tax.
    OUT.write_text(json.dumps(rec, indent=1) + chr(10), encoding="utf-8", newline="\n")
    print(f"artifact: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
