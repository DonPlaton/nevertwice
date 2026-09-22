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

A number standing BESIDE a reference is checked too, not only one written inside the brackets.
That is how a document actually gets migrated - append `[[claim:id]]` to the line and leave the
figure - and the first version of this tool passed exactly that with zero complaints while the
line still read `current 1.000 / 1.000` against a stored 0.9833. A bare number must be the
claim's value or one of the forms in its `printed` field; `printed` is the register's own
rounding, so `0.067` for a stored 0.0667 is legal and `0.017` for a stored 0.0583 is not.

It fires on the two readings that are unambiguous: one reference where EVERY number on the line
disagrees, or a contiguous run of figures as long as the line's list of references, paired in
reading order. A line that also carries a date, a sample size or a section number is therefore
silent rather than noisy - measured, the naive form gave five false messages out of eight - and
`current 1.000 / 1.000 [[a]] / [[b]]`, the frozen block's own shape, is still caught.

**Two misses, chosen rather than discovered**, both silences rather than false alarms - a check
that invents a message gets switched off, one that misses a case is still worth running:

  * a wrong figure hiding behind a correct one on a one-reference line - `current 0.9833, was
    1.000 [[a]]` - because not every number disagrees;
  * a wrong figure on a paired line that also carries a stray number earlier - `Frozen 2026:
    current 1.000 [[a]] / [[b]]` - because only the last contiguous run pairs. Pairing by count
    alone reported the YEAR against the first claim and let the real defect pass against the
    second, which is worse than saying nothing.

Exit code 1 if any reference is broken OR any named document references nothing, so a document
can be put in CI. A zero-reference file is the state this tool exists to end, and reporting it
while exiting 0 is the defect, not the report.

**What it cannot catch, stated so it is not read as catching it:** the WRONG claim, correctly
written. `[[claim:supersession_implicit.nevertwice.current_rate = 1.0]]` placed under an
explicit reading passes every check here - the claim exists, is live, and its number matches.
That substitution happened in this project on 2026-09-22 and was found by a person reading.
This tool closes rewritten numbers; it does not close a claim chosen by family instead of by
the reading it belongs to.
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


#: A bare number on a gate line: `0.9833`, `411.0`, `89`, `-0.060`. Percent and unit suffixes
#: are left to `printed`, which carries the form the register itself publishes.
BARE = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)(?![\w.])")

#: A run of figures separated by nothing but a delimiter: `1.000 / 1.000`, `0.017, 0.067`.
RUN = re.compile(r"-?\d+(?:\.\d+)?(?:\s*[/,]\s*-?\d+(?:\.\d+)?)+")


def _legal_forms(claim: dict) -> set[str]:
    """Every spelling of a claim's value the register itself publishes, plus the value."""
    forms = {str(claim.get("value"))}
    for pr in (claim.get("printed") or []):
        forms.add(str(pr).strip())
        #: `printed` carries units - "30 ms", "81%" - and a gate line writes the number alone.
        m = BARE.search(str(pr))
        if m:
            forms.add(m.group(1))
    return forms


def _matches(text: str, claim: dict) -> bool:
    forms = _legal_forms(claim)
    if text in forms:
        return True
    try:
        want = float(text)
    except ValueError:
        return False
    for f in forms:
        try:
            if abs(float(f) - want) <= 1e-9:
                return True
        except ValueError:
            continue
    return False


def check_file(path: Path, claims: dict) -> tuple[int, list[str]]:
    """Returns (references found, problems)."""
    problems: list[str] = []
    found = 0
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        refs = REF.findall(line)
        #: The ADJACENT number. Two readings of a line are unambiguous and the rule fires on
        #: exactly those; anything else would be a guess, and a guessing check is worse than
        #: none. Measured on a corpus of realistic gate lines: reporting every unmatched number
        #: on a one-reference line gave 8 messages of which 5 were false - a sample size, a
        #: section number and a date, the date alone producing three (audit 2026-09-22).
        #: Numbers inside the brackets are stripped first, so a correct migration does not
        #: report its own value twice.
        #: Positions come from the ORIGINAL line, never from a substitution. The first version
        #: replaced each reference with `" | "` and split on the first `|` - which in a markdown
        #: table row is the table's own pipe, before any reference, so `head` came out empty and
        #: the paired branch went silent on every table row (audit 2026-09-22). The claim tables
        #: and the derived frozen block are all markdown tables, so that is the main form, not a
        #: rare one. Overloading a character that the text itself uses is the defect.
        marks = list(REF.finditer(line))
        stripped = REF.sub(" ", line)
        outside = BARE.findall(stripped)
        #: For the paired branch, only the LAST CONTIGUOUS RUN of figures counts -
        #: numbers separated from each other by nothing but a delimiter. A stray number
        #: earlier in the line ("Frozen 2026: current 1.000 [[a]] / [[b]]") would
        #: otherwise shift the pairing by one: the year reported against the first claim
        #: and the real defect - 1.000 against a stored 0.9833 - paired with the second
        #: and passing. Counting alone cannot see that: the counts happen to match
        #: (audit 2026-09-22).
        #: The run NEAREST the references: the last one before the first reference, or - if the
        #: line puts its figures after - the first one following the last reference. Taking the
        #: last run of the whole line closed the case where a stray number stands BEFORE the
        #: figures and opened the same hole after it: a trailing interval
        #: (`... [[a]] / [[b]] (95% CI 0.9412, 0.9954)`) or a trailing date paired the CI bounds
        #: against the claims and swallowed the real defect (audit 2026-09-22). Neither form is
        #: contrived - the register itself stores `ci: {low, high}`.
        head = line[:marks[0].start()] if marks else ""
        tail = line[marks[-1].end():] if marks else ""
        before, after = RUN.findall(head), RUN.findall(tail)
        paired = BARE.findall(before[-1] if before else after[0]) if (before or after) else []
        named = [(cid, claims[cid]) for cid, _ in refs if cid in claims]
        if outside and named and not any(c.get("declaration") for _, c in named):
            def _complain(cid, claim, bare):
                problems.append(
                    f"{path}:{n}: `{cid}` - the line writes {bare} beside the reference; "
                    f"the register holds {claim.get('value')} (printed {claim.get('printed')})")

            if len(named) == 1 and len(refs) == 1:
                #: One reference: report only when EVERY number on the line disagrees. A line
                #: mentioning a date or an `n` is then silent - those agree with nothing and
                #: the line is not all-wrong - while a line whose only figure is the stale one
                #: is caught.
                cid, claim = named[0]
                if all(not _matches(b, claim) for b in outside):
                    for bare in dict.fromkeys(outside):
                        _complain(cid, claim, bare)
            elif len(paired) == len(refs) == len(named) > 1:
                #: As many numbers as references, paired in reading order. This is the frozen
                #: block's own shape - `current 1.000 / 1.000 [[explicit]] / [[implicit]]` -
                #: and it has no other reading. Deduplication would break it: half the block's
                #: pairs repeat the figure, and collapsing them makes the counts disagree.
                for (cid, claim), bare in zip(named, paired):
                    if not _matches(bare, claim):
                        _complain(cid, claim, bare)
        for cid, printed in refs:
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
    #: A silent document fails too. Printing "names no claim" and exiting 0 let the state this
    #: tool exists to end pass CI, which reads the code and not the line (audit 2026-09-22).
    return 1 if (all_problems or silent) else 0


if __name__ == "__main__":
    raise SystemExit(main())
