#!/usr/bin/env python3
"""Every class the guard stand scores reaches the register, not only the arms that clear the budget.

The stand splits each arm's result by class: repeats a linter could also catch, repeats only memory
can know (the project class), and the hard negatives - the same identifiers with correct code. The
project class is the one the market question is about, so a page that says "we tie with a linter
there" has to be reading a registered number.

Two arms had no project row at all. `research/guard_bench.py` computed the split only for arms that
reached the target false-alarm rate, and `tools/register_guard_bench.py` mirrored that: an arm above
the budget took the binary branch and registered its token and latency costs alone. The two arms
missing from the table were the two the question is about.

This suite fixes the invariant rather than the two values: for every arm whose artifact carries a
class split, the register must carry a claim pointing at it. A claim withdrawn by a re-measure still
counts - freshness is `tools/check_freshness.py`'s job, and existence is this one's.

    python tests/_test_guard_claims_cover_arms.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env even where nothing here reads it

ARTIFACT = ROOT / "research" / "results" / "guard_bench_v1.json"
MANIFEST = ROOT / "research" / "evidence_manifest.json"

P = F = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global P, F
    if ok:
        P += 1
        print(f"  ok   {label}")
    else:
        F += 1
        print(f"  FAIL {label}" + (f" - {detail}" if detail else ""))


if not ARTIFACT.exists() or not MANIFEST.exists():
    print("guard bench artifact or manifest absent - nothing to check")
    sys.exit(0)

arms = json.loads(ARTIFACT.read_text(encoding="utf-8"))["arms"]
claims = {c["id"]: c for c in json.loads(MANIFEST.read_text(encoding="utf-8"))["claims"]}

# The split is a diagnostic: WHICH repeats an arm catches. An arm that fires too often still has one.
WANTED = (
    ("project", "recall", "project_recall", "arms.{arm}.project.recall"),
    ("hard_negatives", "false_positive_rate", "hard_negative_fpr",
     "arms.{arm}.hard_negatives.false_positive_rate"),
)

print("# every arm with a class split has a claim pointing at it")
for arm, sc in sorted(arms.items()):
    for block, field, suffix, pointer in WANTED:
        if (sc.get(block) or {}).get(field) is None:
            continue
        cid = f"guards.{arm}.{suffix}"
        c = claims.get(cid)
        check(f"{cid} is in the register", c is not None,
              f"the artifact carries {block}.{field} = {sc[block][field]} and nothing cites it")
        if c is not None:
            check(f"{cid} points at the artifact's own field",
                  c.get("pointer") == pointer.format(arm=arm), repr(c.get("pointer")))

print("# and the operating point is recorded, so a rate is never read at the wrong threshold")
for arm, sc in sorted(arms.items()):
    if (sc.get("project") or {}).get("recall") is None:
        continue
    at = sc.get("split_at") or {}
    check(f"{arm} records the threshold its split was taken at", at.get("threshold") is not None,
          repr(at))
    check(f"{arm} records the false-alarm rate at that threshold",
          at.get("false_positive_rate") is not None, repr(at))

print("# and a registered number reaches the page, or registering it changed nothing")
PAGE = ROOT / "research" / "GUARD_BENCH.md"
if PAGE.exists():
    body = PAGE.read_text(encoding="utf-8")
    block = body.split("<!-- claims:guard-bench -->")[-1].split("<!-- /claims:guard-bench -->")[0]
    LABEL = {"guards_deterministic": "engine's no-model patterns",
             "guards_llm": "model-written patterns"}
    for arm, label in LABEL.items():
        row = next((ln for ln in block.splitlines() if label in ln), "")
        check(f"the {arm} row is on the table", bool(row))
        cells = [x.strip() for x in row.split("|")]
        pr = claims.get(f"guards.{arm}.project_recall")
        if row and pr is not None:
            # columns: arm | recall | precision | hard-negative | project-only | tokens | ms
            check(f"the {arm} row prints its project-class recall",
                  cells[5] not in ("-", ""), f"row reads {row!r}")
            check(f"the {arm} row prints its hard-negative rate", cells[4] not in ("-", ""),
                  f"row reads {row!r}")
    check("the caption says the two rates were read at different operating points",
          "where the arm fires" in block,
          "a reader would compare a rate taken at the budget with one taken above it")

print()
print(f"guard claims cover arms: {P} passed, {F} failed")
sys.exit(1 if F else 0)
