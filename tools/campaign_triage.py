#!/usr/bin/env python3
"""Which pending claims a re-measure can settle, and which it can only re-draw.

The council's third point (2026-09-22): sort the restorable claims into "margin larger than the
noise" and "inside the noise" BEFORE the campaign, and run only the first. Otherwise the campaign
reproduces the problem with fresh data and the same green checks.

The noise is not a guess. Five runs of ONE commit (`46581bc`, `supersession_v1_implicit`, arm
`nevertwice`, 80 cases each, integrity verified - `.loop/AUDIT-ABSOLUTE-GATES.md`):

    over_retraction_rate   .0    .0    .0    .05   .0      spread .05
    control_miss_rate      .0    .05   .0    .2    .0      spread .2
    stale_rate             .0667 .0833 .05   .1333 .1      spread .0833
    current_rate           .9833 .9833 .9833 .9833 .9667   spread .0166
    mean_chars_returned    491.2 465.0 487.7 465.0 491.4   spread 26.4

A claim printing `stale 0.0667` prints four digits of a quantity this stand resolves to about a
twelfth. Not a rounding quibble: `over_retraction_rate` is the gate everyone called absolute, and
one run in five put it at 0.05 against a threshold of 0.000.

Three questions decide the group, each asked of the source rather than remembered:

    does the claim's closure call an engine door?      it writes notes through the extractor,
      (capture_session, process_session, ...)          which is not deterministic at temp 0
    does it call a model endpoint directly?            a judge is a model call by another route
      (/api/generate, /api/chat)                       - k8_judge_eval.py does exactly this
    is the claim's own field a clock reading?          machine-dependent by construction

    A  deterministic given committed inputs   arithmetic over cached vectors or a fixed corpus:
                                              a re-measure reproduces the number exactly
    B  noisy, printed no finer than its spread  worth re-measuring as it stands
    C  noisy, printed FINER than the spread   a re-measure re-draws it; re-print it coarser or
                                              pool more runs before believing the digits
    D  noisy, no spread measured for the field the spread is a run, not an opinion, and it must
                                              come first
    E  machine-dependent                       reproduces the machine, not the code

Group D is not a failure of this tool; it is the honest size of what nobody has measured - and
it is why B must always be read as "B of the measured". B and D are not independent: a claim
cannot reach B without first leaving D. `B = 0` alongside `C = 45` means all 45 claims on the
five measured fields print finer than their stand resolves; it says nothing about the 329 whose
field nobody has measured. Flattened to "no claim prints coarser than its spread" it would state
a property of the project where it states a property of our knowledge.

    python tools/campaign_triage.py              # the split, with fields and stands
    python tools/campaign_triage.py --detail C   # every claim in one group

Standard library only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "research" / "evidence_manifest.json"

#: Observed RANGE over five runs of one commit, not a standard error: with five draws the range
#: is where a sixth may land, and it is the number a threshold has to clear.
#: MEASURED BEFORE THE TEMPERATURE PIN, and that is not a footnote. These five runs sampled the
#: extractor at 0.2; `f405891` pinned it to 0, and three runs of the same stand on one commit
#: then gave a `chars` range of 1.30 against the 26.4 below, with 1 case in 80 differing between
#: runs instead of 80 in 80. So every number here is an UPPER BOUND on today's resolution, not
#: today's resolution. It is left un-rewritten rather than guessed: an upper bound only ever
#: refuses a threshold that a smaller spread would also refuse.
#: Measured consequence for the campaign this table plans (2026-09-22, over all 572 pending):
#:     this table                                  A 188 · B 0 · C 45 · D 329 · E 10
#:     with `mean_chars_returned` at the pinned 1.30   unchanged
#:     with every field reduced in that proportion  A 188 · B 4 · C 41 · D 329 · E 10
#: - four claims recorded as "printed finer than the stand resolves" print as they are on the pin.
#: A spread is a property of a stand IN A MODE, not of a field and not even of a stand: naming
#: the stand and not the mode let this table outlive the change that voided it.
SPREAD = {
    "over_retraction_rate": 0.05,
    "control_miss_rate": 0.2,
    "stale_rate": 0.0833,
    "current_rate": 0.0166,
    "mean_chars_returned": 26.4,
}

#: WHERE that table was measured. Every number in it comes from five runs of the supersession
#: stand, so `SPREAD.get(leaf)` is a rule by FIELD NAME: for a claim of another stand it transfers
#: someone else's resolution. Eleven of the forty-five in group C do exactly that (abstention's
#: `current_rate`, code-sessions' `stale_rate`), and the transfer may well hold - noise of the
#: same kind is often of the same order - but it is inherited, not measured. Named here because
#: a property of a named stand quietly becoming a property of a field is the night's defect class
#: (found by the auditing session, 2026-09-22).
SPREAD_MEASURED_ON = "research/supersession_bench.py"

#: WHEN, in the only sense that moves these numbers: the extractor's sampling temperature at the
#: time of those runs, and how many runs there were. Written as VALUES rather than prose because
#: a mode recorded in a comment is a property written down and not enforced - which is the defect
#: this whole file exists to sort. `tests/_test_campaign_triage.py` reads the stand's own pin out
#: of its source and requires the flag below to agree with the comparison.
SPREAD_MEASURED_AT = {"extract_temp": "0.2", "runs": 5, "corpus": None}

#: `corpus` is None because it was never recorded, and that turned out to matter more than the
#: temperature. Measured 2026-09-22 AFTER the pin, five runs of the same stand at temperature 0
#: on one corpus and one on the other, same `code_sha`, same dataset sha:
#:
#:     explicit  443.8 / 414.0 / 414.0 / 443.4 / 408.4    range 35.4   sd 17.39
#:     implicit  492.2, against 492.2 recorded and 495.1 / 493.8 / 493.8 in the pin's own commit
#:
#: Twenty-three times the spread on one corpus against the other, at the same pin. So the pin did
#: not remove the variance - it removed it on the implicit corpus - and a spread is a property of
#: a stand in a MODE and on a CORPUS, not of a stand and not of a field. The table below cannot
#: say which corpus it describes, so "35.4 now against 26.4 before" compares two numbers that
#: each carry an unrecorded variable; only the pair measured in one sitting is defensible.
SPREAD_POST_PIN = {
    "research/supersession_bench.py": {
        "explicit": {"field": "mean_chars_returned", "range": 35.4, "sd": 17.39, "runs": 5},
        "implicit": {"field": "mean_chars_returned", "range": 1.30, "sd": 0.7506, "runs": 3},
    },
}

#: True while the table was measured in a mode the stand no longer runs in, so every number is an
#: upper bound on today's resolution rather than today's resolution. Flipping this by hand without
#: re-measuring reddens the suite, and so does re-measuring without flipping it.
SPREAD_IS_UPPER_BOUND = True

#: The six doors through which a stand reaches the extraction model. Asked of the source, not
#: kept as a list of stand names, because such a list goes stale the first time a stand grows a
#: call (`tests/_test_stands_pin_the_sampler.py:110` keeps the same six).
DOORS = ("capture_session", "process_session", "generate_json", "write_typed_note",
         "rerank_notes", "compact_context_if_needed")

#: A model called by a route that is not one of the doors. `research/k8_judge_eval.py:43` posts
#: to `/api/generate` itself, and its eight claims looked deterministic until this was asked -
#: the door test alone answers a question ADJACENT to "is this number reproducible".
#: The literal endpoint, not the word: an earlier version matched `ollama` anywhere and put every
#: retrieval claim in the noisy group because `longmem_eval.py:111` names the EMBEDDER provider
#: and `_rerank.py`'s docstring mentions Ollama in prose. Embedding is not generation - the
#: vectors are cached and the ranking over them is arithmetic. A gate refusing on a sign it has
#: not checked is the failure this file exists to prevent, so the sign is the generation URL.
#: The quote before the path was a mistake, and an expensive shape of one: it demanded that the
#: endpoint be the WHOLE string literal, while every caller in this repository builds it by
#: interpolation - `f"{OLLAMA}/api/chat"` at `research/gen_code_sessions.py:255`. Measured
#: 2026-09-22: the quoted rule saw ONE file in the tree; the literal path sees fourteen, of
#: which eleven call the endpoint and three only quote it (this tool, and two suites). The
#: register does not move (A 188 / C 45 / D 329 / E 10 under both rules, pinned in
#: `tests/_test_campaign_triage.py`), because no pending claim is produced by the twelve it
#: could not see - but `gen_code_sessions.py` generates its corpus with a local model and would
#: have been read as "deterministic given committed inputs" the day a claim cited it.
#: A file that only MENTIONS the endpoint in prose now matches too. That error keeps a claim out
#: of the deterministic group, which is the safe direction for a campaign plan; the opposite
#: error is the one that puts a model call in a list of arithmetic.
MODEL_CALL = re.compile(r"/api/(generate|chat)(?![A-Za-z])")

#: Fields that measure the machine. F1 re-measured one of these and it moved by a third between
#: two sessions on one box, with no code change.
CLOCK_FIELDS = {"ms_per_call", "seconds", "latency", "ms", "p50_ms", "p95_ms", "query_s",
                "setup_s", "mean_ms", "median_ms"}

#: Arms measured by a competitor's own ingest. Their numbers are deterministic HERE - the ranking
#: is arithmetic over a store built earlier - but re-measuring one re-runs a third-party pipeline
#: with its own model, so "deterministic given committed inputs" is true only while the inputs
#: stay committed. They are separated because the register already carries a decision not to
#: re-run them (they do not depend on this engine and their results are static), and a campaign
#: plan that counts them as work is 60 claims too large.
COMPETITOR = re.compile("mem0|zep|langmem|amem|graphiti|letta|cognee", re.I)

_SRC: dict[str, tuple[bool, bool]] = {}


def _asked_of_source(rel: str) -> tuple[bool, bool]:
    """(reaches an engine door, calls a model endpoint) for one repository file."""
    if rel not in _SRC:
        p = ROOT / rel
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            return (False, False)
        door = any((isinstance(n, ast.Attribute) and n.attr in DOORS)
                   or (isinstance(n, ast.Name) and n.id in DOORS) for n in ast.walk(tree))
        _SRC[rel] = (door, bool(MODEL_CALL.search(text)))
    return _SRC[rel]


def printed_precision(printed: list, value) -> float | None:
    """The smallest difference the claim's own printed form can express.

    `0.0667` prints to four places, so it distinguishes 0.0001 - and invites the reader to.
    """
    finest = None
    for p in list(printed or []) + ([str(value)] if value is not None else []):
        s = str(p).strip().rstrip("%").replace(",", "")
        if "." not in s:
            continue
        try:
            float(s)
        except ValueError:
            continue
        step = 10.0 ** -len(s.split(".")[1])
        finest = step if finest is None else min(finest, step)
    return finest


def triage(claim: dict) -> tuple[str, str]:
    """(group, why) for one pending claim."""
    leaf = (claim.get("pointer") or "").split(".")[-1].split("[")[0]
    if leaf in CLOCK_FIELDS:
        return "E", f"`{leaf}` measures the machine, so a re-run reproduces the box"
    door = model = None
    for f in (claim.get("produced_by") or []):
        rel = f.replace("\\", "/")
        if rel.startswith("nevertwice/"):
            continue                       # the engine DEFINES the doors; every closure holds it
        d, m = _asked_of_source(rel)
        if d:
            door = rel
        if m:
            model = rel
    if not door and not model:
        return "A", "no engine door and no model call in the closure: arithmetic over inputs"
    why_noisy = f"{door} reaches an engine door" if door else f"{model} calls a model endpoint"
    spread = SPREAD.get(leaf)
    if spread is None:
        return "D", f"{why_noisy}, and no run-to-run spread is measured for `{leaf}`"
    step = printed_precision(claim.get("printed"), claim.get("value"))
    if step is None or step >= spread:
        return "B", f"{why_noisy}; printed no finer than the spread {spread:g}"
    return "C", (f"{why_noisy}; printed to {step:g} against a measured spread of {spread:g} - "
                 f"{spread / step:.0f}x finer than this stand resolves")


def transferred(claim: dict) -> bool:
    """Does this claim's spread come from a stand other than its own?

    `supersession_v1_implicit` is the same stand on another corpus, so the transfer is within the
    instrument; `abstention_ab` and `code_sessions_eval` are different stands, and for them "finer
    than this stand resolves" is really "finer than ANOTHER stand resolves".
    """
    return _stand(claim) not in ("", SPREAD_MEASURED_ON, "(no stand in the command)")


NAMES = {"A": "deterministic given committed inputs",
         "B": "noisy, printed no finer than the measured spread",
         "C": "noisy, printed FINER than the stand resolves",
         "D": "noisy, no run-to-run spread measured for the field",
         "E": "machine-dependent"}


def groups_of(claims: list[dict]) -> dict[str, list[tuple[dict, str]]]:
    out: dict[str, list[tuple[dict, str]]] = collections.defaultdict(list)
    for c in claims:
        g, why = triage(c)
        out[g].append((c, why))
    return out


def _stand(claim: dict) -> str:
    for t in (claim.get("command") or "").split()[1:]:
        if t.endswith(".py"):
            return t
        if t.startswith("-"):
            break
    return "(no stand in the command)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--detail", choices=list("ABCDE"), help="list every claim in one group")
    args = ap.parse_args(argv)

    blob = json.loads(MANIFEST.read_text(encoding="utf-8"))
    c = blob["claims"]
    claims = list(c.values() if isinstance(c, dict) else c)
    pending = [x for x in claims if x.get("pending_remeasure")]
    groups = groups_of(pending)

    print(f"\npending claims: {len(pending)}\n")
    for g in "ABCDE":
        print(f"  {g}  {len(groups[g]):4d}   {NAMES[g]}")
    print(f"\n  B is {len(groups['B'])} OF THE {len(groups['B']) + len(groups['C'])} claims whose "
          f"field has a measured spread - the other {len(groups['D'])} are unmeasured, not coarse")

    own = [c for c, _ in groups["A"] if not COMPETITOR.search(c["id"])]
    comp = [c for c, _ in groups["A"] if COMPETITOR.search(c["id"])]
    print(f"\n  A splits: {len(own)} our own arms, re-measurable from the cache; "
          f"{len(comp)} competitor arms, static by a recorded decision")

    home = [c for c, _ in groups["C"] if not transferred(c)]
    away = [c for c, _ in groups["C"] if transferred(c)]
    print(f"  C splits: {len(home)} on the stand the spread was measured on; "
          f"{len(away)} on a spread transferred from {SPREAD_MEASURED_ON} by field name")

    for g in "ACDE":
        if not groups[g]:
            continue
        print(f"\n  {g}, by stand:")
        for s, n in collections.Counter(_stand(cl) for cl, _ in groups[g]).most_common(10):
            print(f"      {n:4d}  {s}")

    print("\n  C and D, by field:")
    for g in "CD":
        fields = collections.Counter((cl.get("pointer") or "").split(".")[-1].split("[")[0]
                                     for cl, _ in groups[g])
        print(f"      {g}: " + ", ".join(f"{f} {n}" for f, n in fields.most_common(8)))

    if args.detail:
        print(f"\n--- group {args.detail}: {NAMES[args.detail]} ---")
        for cl, why in groups[args.detail]:
            print(f"  {cl['id']}\n      {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
