#!/usr/bin/env python3
"""Judge a part-3 gate by case identity across two draws - rule R1, confirmed by the owner
2026-09-19 with both of its additions.

The stand runs each mode twice. A rate at this n is not an instrument: two draws of the same
commit read 0.000 and 0.050 and neither is wrong. Case identity is - the same case failing in both
draws is a mechanism, a case failing in one is the draw. R1 re-expresses the gates on that, and it
does not lower any threshold: over-retraction still has to be zero loss; what changes is the
evidence admitted for it.

Three clauses, all enforced here:

1. **A loss is charged only on a shared failure.** A case failing in one draw is recorded and
   named, never counted.
2. **Credit is symmetric with blame.** An improvement counts only when the same case is fixed in
   BOTH draws. Charging by identity while crediting by rate is the shape of a rule that can only
   flatter a change.
3. **The union is checked against the base, and growth demands a third draw.** Two draws agreeing
   on zero shared failures can still be two draws failing on different cases each. If the union of
   failing ids is larger than the base's, the mechanism moved the SET of what fails even when it
   did not move the rate, and nothing is concluded until a third draw is in.

    python tools/r1_verdict.py research/results/supersession_v1.json --metric over_retraction
    python tools/r1_verdict.py NEW.json --base research/results/supersession_baseline.json --json OUT

Exit status is 0 when the gate is judged, 3 when a third draw is required before it can be.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: metric -> (row field, the value that means this case FAILED, only these shapes)
#: A metric is defined by what counts as a loss on ONE case, because that is the unit R1 judges.
METRICS = {
    "over_retraction": ("current_retired", True, None),
    "stale": ("stale_returned", True, None),
    "control_miss": ("current_returned", False, None),
    "old_value_served": ("old_value_served", True, None),
}

#: The two readings of one mode. `nevertwice_run2` is the stand's own name for the second draw.
DRAW_KEYS = ("nevertwice", "nevertwice_run2")


def rows_of(doc: dict, arm: str) -> list:
    return list(((doc.get("arms") or {}).get(arm) or {}).get("rows") or [])


def failing(rows: list, metric: str) -> set:
    field, bad, shapes = METRICS[metric]
    out = set()
    for r in rows:
        if shapes and r.get("shape") not in shapes:
            continue
        if field not in r:
            continue
        if bool(r.get(field)) is bool(bad):
            out.add(str(r.get("id")))
    return out


def draws(doc: dict, metric: str) -> list:
    got = []
    for key in DRAW_KEYS:
        rows = rows_of(doc, key)
        if rows:
            got.append((key, failing(rows, metric)))
    return got


def verdict(doc: dict, metric: str, base: dict | None) -> dict:
    d = draws(doc, metric)
    if len(d) < 2:
        return {"metric": metric, "ok": False,
                "detail": f"need two draws; found {[k for k, _ in d]}"}
    (k1, f1), (k2, f2) = d[0], d[1]
    shared = sorted(f1 & f2)
    only = sorted(f1 ^ f2)
    union = f1 | f2

    rec = {"metric": metric, "draws": {k1: sorted(f1), k2: sorted(f2)},
           "charged": shared, "named_not_counted": only,
           "union_size": len(union)}

    if base is not None:
        bd = draws(base, metric)
        if len(bd) >= 2:
            b_union = bd[0][1] | bd[1][1]
            b_shared = bd[0][1] & bd[1][1]
            rec["base_union_size"] = len(b_union)
            #: R1 clause 3 gates on the union being LARGER, which is what the owner confirmed.
            #: The ids that are new are reported beside it because "moved the set" is the stated
            #: rationale - but a union that is new AND smaller is a mechanism fixing more than it
            #: breaks, and demanding a third draw for that would be a stricter rule than the one
            #: on the books. Tightening a confirmed rule quietly is the thing R1 exists against.
            rec["union_new_ids"] = sorted(union - b_union)
            rec["third_draw_required"] = len(union) > len(b_union)
            # Clause 2: a fix counts only when BOTH draws fixed the same case.
            rec["credited_fixes"] = sorted(b_shared - union)
            rec["fixes_named_not_counted"] = sorted((b_shared - f1) ^ (b_shared - f2))
        else:
            rec["base_union_size"] = None
            rec["third_draw_required"] = False
    else:
        rec["third_draw_required"] = False

    rec["ok"] = not rec["charged"] and not rec.get("third_draw_required")
    return rec


def render(rec: dict) -> str:
    if rec.get("detail"):
        return f"  {rec['metric']}: {rec['detail']}"
    out = [f"  {rec['metric']}"]
    for name, ids in rec["draws"].items():
        out.append(f"    {name:18} {len(ids)} failing" + (f": {', '.join(ids)}" if ids else ""))
    out.append(f"    charged (both draws)   {len(rec['charged'])}"
               + (f": {', '.join(rec['charged'])}" if rec["charged"] else ""))
    if rec["named_not_counted"]:
        out.append(f"    the draw, not counted  {', '.join(rec['named_not_counted'])}")
    if rec.get("base_union_size") is not None:
        out.append(f"    union {rec['union_size']} vs base {rec['base_union_size']}")
        if rec.get("union_new_ids"):
            out.append(f"    new to this arm       {', '.join(rec['union_new_ids'])}")
        if rec.get("third_draw_required"):
            out.append(f"    THIRD DRAW REQUIRED - the union of failures is larger than the base's")
        if rec.get("credited_fixes"):
            out.append(f"    credited fixes (both)  {', '.join(rec['credited_fixes'])}")
        if rec.get("fixes_named_not_counted"):
            out.append(f"    fixed in one draw only {', '.join(rec['fixes_named_not_counted'])}")
    out.append(f"    -> {'judged: no charged loss' if rec['ok'] else 'NOT judged'}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("artifact")
    ap.add_argument("--base", default="", help="the artifact this one is compared against")
    ap.add_argument("--metric", action="append", default=[],
                    help=f"one of {', '.join(METRICS)}; repeatable, default all")
    ap.add_argument("--json", default="", metavar="PATH")
    args = ap.parse_args()

    doc = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
    base = json.loads(Path(args.base).read_text(encoding="utf-8")) if args.base else None
    metrics = args.metric or list(METRICS)

    recs = [verdict(doc, mt, base) for mt in metrics if mt in METRICS]
    print(f"R1 verdict for {Path(args.artifact).name}"
          + (f" against {Path(args.base).name}" if base else " (no base: union check skipped)"))
    for rec in recs:
        print(render(rec))

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps({"artifact": args.artifact, "base": args.base,
                                               "verdicts": recs}, indent=1) + "\n",
                                   encoding="utf-8")
        print(f"\nartifact: {args.json}")

    if any(r.get("third_draw_required") for r in recs):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
