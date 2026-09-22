#!/usr/bin/env python3
"""A gate that names a claim id is checkable; a gate that quotes a number is not.

Two independent findings of 2026-09-22 have the same cure. Eight of the ten lines of part 3
compare against figures the register has withdrawn, and the "taken and don't touch" block
disagrees with the register in four numbers of ten - `current 1.000 / 1.000` against a stored
0.9833, `stale after sleep 0.017` against a stored 0.0583, `PreToolUse 89 ms` against no claim
at all, `cold import 29 ms` against a stored 30. Both were found by reading, because no
machine connection between a gate and its evidence exists.

Matching them back by VALUE does not work and that was measured too: 0.8 collides with
`longmem.hybrid.recall_at_5`, 0.05 with `frontier.judge_disagreement`. The only figure that
resolved cleanly was 411.0, and it had already been found by reading.

So the connection has to be written rather than inferred. A gate names the claim:

    Gate: chars/query at or below [[claim:supersession.mem0.chars_per_query]]
    Frozen: current [[claim:supersession.nevertwice.current_rate = 0.9833]]

and this checks, for every reference:

  * the claim exists                       - a renamed claim breaks the gate loudly
  * it is live, not withdrawn or pending   - the defect that made eight gates unjudgeable
  * the value beside it, when written, is the value the register holds

    python tools/check_gate_refs.py .loop/GOAL-FINISH-C.md docs/*.md
    python tools/check_gate_refs.py --list          # every referencable live claim id

Exit code 1 if any reference is broken, so a document can be put in CI. Documents that name no
claim are reported as such rather than passing silently: a gate file with zero references is
the state this tool exists to end, not evidence that it is clean.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "research" / "evidence_manifest.json"

#: `[[claim:some.id]]` or `[[claim:some.id = 0.9833]]`. The id charset is the register's own:
#: dots and underscores, no spaces. The value is optional because a gate often states a bound
#: ("at or below X") where naming the claim is the whole point and repeating its number is not.
REF = re.compile(r"\[\[claim:\s*([A-Za-z0-9_.]+)\s*(?:=\s*([-+0-9.eE]+)\s*)?\]\]")


def load_claims() -> dict:
    blob = json.loads(MANIFEST.read_text(encoding="utf-8"))
    claims = blob["claims"]
    return dict(claims) if isinstance(claims, dict) else {c["id"]: c for c in claims}


def state(claim: dict) -> str:
    if claim.get("pending_remeasure"):
        return "pending re-measure"
    if claim.get("stale"):
        return "withdrawn"
    return "live"


def check_file(path: Path, claims: dict) -> tuple[int, list[str]]:
    """Returns (references found, problems)."""
    problems: list[str] = []
    found = 0
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        for cid, printed in REF.findall(line):
            found += 1
            claim = claims.get(cid)
            if claim is None:
                problems.append(f"{path}:{n}: no claim `{cid}` in the register")
                continue
            st = state(claim)
            if st != "live":
                problems.append(f"{path}:{n}: `{cid}` is {st} - a gate cannot be judged "
                                f"against it until the campaign restores it")
            if printed:
                #: A declared value is a decision and has no measured number to disagree with.
                if claim.get("declaration"):
                    continue
                try:
                    want, got = float(printed), float(claim.get("value"))
                except (TypeError, ValueError):
                    problems.append(f"{path}:{n}: `{cid}` value {claim.get('value')!r} "
                                    f"is not a number, but the reference writes {printed}")
                    continue
                if abs(want - got) > 1e-9:
                    problems.append(f"{path}:{n}: `{cid}` reads {printed}, register holds {got}")
    return found, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", help="documents to check")
    ap.add_argument("--list", action="store_true",
                    help="print every live claim id a gate may reference, and exit")
    args = ap.parse_args(argv)
    claims = load_claims()

    if args.list:
        live = sorted(cid for cid, c in claims.items() if state(c) == "live")
        for cid in live:
            print(f"{cid}\t{claims[cid].get('value')}")
        print(f"\n{len(live)} live claim(s) of {len(claims)}; "
              f"{sum(1 for c in claims.values() if state(c) == 'pending re-measure')} "
              f"awaiting re-measure", file=sys.stderr)
        return 0

    if not args.paths:
        ap.error("name at least one document, or pass --list")

    total, all_problems, silent = 0, [], []
    for p in (Path(x) for x in args.paths):
        if not p.exists():
            all_problems.append(f"{p}: no such file")
            continue
        found, problems = check_file(p, claims)
        total += found
        all_problems += problems
        if found == 0:
            silent.append(str(p))

    for line in all_problems:
        print(line)
    for p in silent:
        print(f"{p}: names no claim - its gates are not connected to any evidence")
    print(f"\n{total} reference(s) checked, {len(all_problems)} broken, "
          f"{len(silent)} document(s) naming no claim")
    return 1 if all_problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
