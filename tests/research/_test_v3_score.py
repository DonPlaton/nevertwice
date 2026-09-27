#!/usr/bin/env python3
"""PREREG-V3 TB4.12 (A6): research/v3/score_v3.py - the official scorers re-written in pure Python (Q-51 O-c).

* MAB (FactConsolidation takes default_post_process): exact match, F1 with the special answers, substring exact
  match, the max over ground truths (a flat or nested list), parse_output, the larger of the whole output's and the
  parsed answer's metrics - equal, on every row of the golden table, to the pinned functions themselves;
* AMA: the mean score and the share of exact 1.0.

The golden table: the pinned utils/eval_other_utils.py (@5380260, sha256 d77976be…93a2) - its normalize_answer,
f1_score, drqa_exact_match_score, substring_exact_match_score, drqa_metric_max_over_ground_truths and parse_output,
extracted by ast and run with only string/re/Counter - on these inputs, once, 2026-09-27 (the executor's differential
probe; no third-party code runs in this suite or in a campaign run).

    python tests/research/_test_v3_score.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

_spec = importlib.util.spec_from_file_location("v3_score", ROOT / "research" / "v3" / "score_v3.py")
SV = importlib.util.module_from_spec(_spec)
sys.modules["v3_score"] = SV
_spec.loader.exec_module(SV)
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


GOLDEN = [
    ["Paris", "Paris", {"exact_match": True, "f1": 1.0, "substring_exact_match": True}],
    ["The Paris.", "paris", {"exact_match": True, "f1": 1.0, "substring_exact_match": True}],
    ["Paris, France", "Paris", {"exact_match": False, "f1": 0.6666666666666666, "substring_exact_match": True}],
    ["yes", "no", {"exact_match": False, "f1": 0, "substring_exact_match": False}],
    ["Yes", "yes", {"exact_match": True, "f1": 1.0, "substring_exact_match": True}],
    ["no answer", "noanswer", {"exact_match": False, "f1": 0, "substring_exact_match": False}],
    ["noanswer", "noanswer", {"exact_match": True, "f1": 1.0, "substring_exact_match": True}],
    ["London", "Paris", {"exact_match": False, "f1": 0, "substring_exact_match": False}],
    ["Answer: Oslo\nmore text", "Oslo", {"exact_match": True, "f1": 1.0, "substring_exact_match": True}],
    ["I think the answer is Oslo", "Oslo", {"exact_match": False, "f1": 0.33333333333333337, "substring_exact_match": True}],
    ["an apple a day", "apple day", {"exact_match": True, "f1": 1.0, "substring_exact_match": True}],
    ["Barack Obama", ["Obama", "Barack Obama"], {"exact_match": True, "f1": 1.0, "substring_exact_match": True}],
    ["Obama", [["B. Obama", "Obama"]], {"exact_match": True, "f1": 1.0, "substring_exact_match": True}],
    ["", "Paris", {"exact_match": False, "f1": 0, "substring_exact_match": False}],
    ["answer: answer: Rome", "Rome", {"exact_match": True, "f1": 1.0, "substring_exact_match": True}],
    ["42", "forty two", {"exact_match": False, "f1": 0, "substring_exact_match": False}],
    ["yes", "yes indeed", {"exact_match": False, "f1": 0, "substring_exact_match": False}],
    ["no way", "no", {"exact_match": False, "f1": 0, "substring_exact_match": True}],
]

def run(fn, *args):
    """The scorer's value, or the exception it raised - a crash is a named FAIL of its row, never a traceback (SC3)."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        return f"raised {type(e).__name__}: {e}"


print("- MAB, against the pinned functions' golden table -")
for pred, gts, want in GOLDEN:
    got = run(SV.mab_default_post_process, pred, gts)
    check(f"default_post_process({pred[:24]!r}, {str(gts)[:24]}) = the pinned result", got == want, f"{got} vs {want}")
check("the special answers: yes against no scores 0; noanswer against itself 1",
      SV.mab_f1("yes", "no") == (0, 0, 0) and SV.mab_f1("noanswer", "noanswer")[0] == 1.0)
check("parse_output takes the text after 'Answer:', else the first line", SV.mab_parse_output("x\nAnswer: Rome\n") == "Rome"
      and SV.mab_parse_output("first line\nsecond") == "first line")
check("the max over a nested list of ground truths", run(SV.mab_max_over, SV.mab_em, "Obama", [["x", "Obama"], ["y"]]) is True)
check("SC3: a nested list is flattened, never compared as a list", run(SV.mab_default_post_process, "Rome",
                                                                        [["Paris"], ["Rome", "Roma"]])
      == {"exact_match": True, "f1": 1.0, "substring_exact_match": True})

print("\n- AMA -")
s = SV.ama_summary([1.0, 0.0, 1.0, 0.5])
check("the mean score and the share of exact 1.0", s == {"avg_score": 0.625, "accuracy": 0.5, "n": 4}, str(s))
check("no scores: zeros", SV.ama_summary([]) == {"avg_score": 0, "accuracy": 0, "n": 0})

print(f"\nv3 score: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
