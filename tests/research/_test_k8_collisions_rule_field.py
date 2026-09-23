#!/usr/bin/env python3
"""A7 (PLAN-Q3Q5.md, TRIZ-PART2B part 5 candidate 3): `research/k8_collisions.py`'s recorder also
records K8 layer 1's own rule verdict (`memory_hook._same_replacement`) beside each pair it
already collects - the SAME verdict `write_typed_note`'s real call is about to reach, read a
second time purely to log it, so a later analysis can join "decided by rule" against "decided by
the judge" (G3.0, `research/ride_along_judge_eval.py`) without a second extraction pass. This is
instrumentation only - it changes what one dict in `PAIRS` carries, never what gets written.

No Ollama here: `install_recorder` wraps `write_typed_note` directly, and this suite calls
`write_typed_note` itself (never `api.capture_session`), so no extraction ever runs.

    python tests/research/_test_k8_collisions_rule_field.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import _env_guard  # noqa: F401, E402 - hermetic store before any project import
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT))
import k8_collisions as k8c  # noqa: E402
import k8_skeleton  # noqa: E402 - imported here, before any make_sandbox rebase: its own
                    # top-level sandbox_guard.isolate() call is idempotent (a re-`verify()`), and
                    # a rebased m.VAULT by the time it runs would trip SandboxEscape on a mismatch
                    # that is not a real escape - only this test's own sandbox moving twice.
import ride_along_judge_eval as rj  # noqa: E402 - same reason
from nevertwice import api  # noqa: E402

m = api.m

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


F = m._FACTS_MARK


def _armed():
    """A fresh sandbox with the recorder installed and `write_typed_note` restored after - the
    same shape `_test_k8_adjudicate.py`/`_test_k8_one_call_per_session.py` use for a fixture that
    monkeypatches a module-level function on the shared `memory_hook` object."""
    from _sandbox import make_sandbox  # noqa: PLC0415
    make_sandbox(m, "k8c_rule_", offline=True)
    k8c.PAIRS.clear()
    saved = m.write_typed_note
    k8c.install_recorder(m)
    return saved


def _case(cid: str, old_markers: list[str], new_markers: list[str]) -> None:
    k8c._set_case({"id": cid, "shape": "value_replaced", "superseded": old_markers,
                  "current": new_markers}, "ruleproj", m)


def test_a_rule_decidable_pair_is_recorded_true() -> None:
    """A same-day restatement with the same verified value literal - K8 layer 1's rule 2
    ("literals") decides this with no model call at all."""
    print("\n- a pair the rules CAN decide records rule_ok True, rule 'literals' -")
    saved = _armed()
    try:
        _case("rule-yes", ["30 seconds"], ["30 seconds"])
        old_desc = f"The deploy timeout is 30 seconds.{F}the deploy timeout is 30 seconds"
        new_desc = (f"The deploy timeout is 30 seconds, confirmed again this session."
                    f"{F}the deploy timeout is 30 seconds")
        m.write_typed_note("Decisions", {"title": "the deploy timeout", "description": old_desc},
                           "ruleproj", "2026-01-01", ["t"], "decision")
        m.write_typed_note("Decisions", {"title": "the deploy timeout", "description": new_desc},
                           "ruleproj", "2026-01-01", ["t"], "decision")
    finally:
        m.write_typed_note = saved
    check("exactly one pair was recorded", len(k8c.PAIRS) == 1, str(len(k8c.PAIRS)))
    row = k8c.PAIRS[-1]
    check("rule_ok is a bool", isinstance(row.get("rule_ok"), bool), str(row.get("rule_ok")))
    check("rule is a non-empty string", bool(isinstance(row.get("rule"), str) and row["rule"]),
          str(row.get("rule")))
    check("this pair's rule verdict is True", row["rule_ok"] is True, str(row))
    check("via rule 'literals' (the same verified value literal on both sides)",
          row["rule"] == "literals", row["rule"])
    # pin: the recorded verdict matches a DIRECT call with the same arguments, so the
    # instrumentation is not a paraphrase of what write_typed_note itself decided.
    direct = m._same_replacement(_old_path(row), "the deploy timeout", new_desc, "", "")
    check("matches a direct call to _same_replacement on the same inputs",
          (row["rule_ok"], row["rule"]) == direct, f"{(row['rule_ok'], row['rule'])} vs {direct}")


def _old_path(row: dict) -> Path:
    return m.VAULT / m.TYPE_FOLDER[row["ntype"]] / f"{row['old_stem']}.md"


def test_a_rule_undecidable_pair_is_recorded_false() -> None:
    """Two DIFFERENT verified values (K7's own timeout fixture, `_test_k8_adjudicate.py`'s and
    `_test_k8_one_call_per_session.py`'s) - the rules refuse, correctly leaving it for the judge."""
    print("\n- a pair the rules CANNOT decide records rule_ok False -")
    saved = _armed()
    try:
        _case("rule-no", ["30 seconds"], ["5 seconds"])
        old_desc = f"The HTTP client timeout is 30 seconds.{F}the HTTP client timeout is 30 seconds"
        new_desc = f"The HTTP client timeout is 5 seconds.{F}the HTTP client timeout is 5 seconds"
        m.write_typed_note("Decisions", {"title": "http client timeout", "description": old_desc},
                           "ruleproj", "2026-01-01", ["t"], "decision")
        m.write_typed_note("Decisions", {"title": "http client timeout", "description": new_desc},
                           "ruleproj", "2026-01-09", ["t"], "decision")
    finally:
        m.write_typed_note = saved
    check("exactly one pair was recorded", len(k8c.PAIRS) == 1, str(len(k8c.PAIRS)))
    row = k8c.PAIRS[-1]
    check("this pair's rule verdict is False - two different verified values, correctly left "
          "for the judge", row["rule_ok"] is False, str(row))
    check("the branch is 'r' (another day, the retirement path)", row["branch"] == "r", row["branch"])


def test_old_artifact_readers_tolerate_the_field_being_absent() -> None:
    """PLAN's own condition: the field is OPTIONAL. An artifact recorded before this change (the
    three shipped `research/results/k8_collisions_*.json` files) has no `rule_ok`/`rule` key on
    any pair - every existing reader must still work on such a pair, and a MIXED list (some pairs
    with the field, some without, exactly what re-running only part of a corpus would produce)
    must not raise either."""
    print("\n- k8_skeleton.label_pairs (an existing reader) tolerates the field's absence -")
    old_shaped = {"case": "c1", "old_title": "x", "old_desc": "a", "new_title": "x", "new_desc": "b",
                 "old_marked": True, "new_marked": True, "truth": "replaces"}    # no rule_ok/rule
    new_shaped = {**old_shaped, "case": "c2", "rule_ok": True, "rule": "literals"}
    corpus = {"c1": {"current": ["b"]}, "c2": {"current": ["b"]}}
    pairs = [dict(old_shaped), dict(new_shaped)]
    raised = None
    try:
        k8_skeleton.label_pairs(pairs, corpus)
    except Exception as e:                                       # noqa: BLE001 - the thing under test
        raised = e
    check("label_pairs does not raise on a pair missing rule_ok/rule", raised is None, str(raised))
    check("and both pairs still got labelled", all("pair_truth" in p for p in pairs), str(pairs))
    check("the field-carrying pair kept its rule_ok/rule untouched",
          pairs[1].get("rule_ok") is True and pairs[1].get("rule") == "literals", str(pairs[1]))

    print("\n- research/ride_along_judge_eval.py's load_pairs also tolerates it -")
    check("rj.load_pairs never reads rule_ok/rule at all (so an absent field cannot break it)",
          "rule_ok" not in Path(rj.__file__).read_text(encoding="utf-8"), "")


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them, and
    reports them passed while `check()` printed FAIL and the script would exit 1. Enforced for
    every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_a_rule_decidable_pair_is_recorded_true,
               test_a_rule_undecidable_pair_is_recorded_false,
               test_old_artifact_readers_tolerate_the_field_being_absent):
        fn()
    print(f"\nk8_collisions rule field: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
