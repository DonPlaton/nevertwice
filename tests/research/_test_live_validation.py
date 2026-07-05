#!/usr/bin/env python3
"""Self-check for live_validation.py - validates the MEASUREMENT INSTRUMENT (a wrong check
silently fabricates a result), plus the paired accounting with a mocked model (no network,
no key). The live run itself needs DEEPSEEK_API_KEY; this guards its logic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "research"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import live_validation as LV     # noqa: E402


def test_checks_self_test():
    # every pitfall detector must fire on buggy code and stay silent on clean code
    assert LV._selfcheck_checks() is True
    print("ok test_checks_self_test")


def test_paired_accounting_with_mock_model():
    # a mock model that writes the pitfall WITHOUT the guard and the fix WITH it
    task = next(t for t in LV.TASKS if t["id"] == "div-zero")

    def mock_call(prompt):
        if "[memory]" in prompt:                       # guarded → correct code
            return "def average(n):\n if not n: return 0\n return sum(n)/len(n)"
        return "def average(n): return sum(n)/len(n)"  # unguarded → the pitfall

    res = LV.validate([task], trials=4, call=mock_call)
    r = res["div-zero"]
    assert r["rate_without"] == 1.0 and r["rate_with"] == 0.0     # guard flips every trial
    assert r["rel_reduction"] == 1.0
    print("ok test_paired_accounting_with_mock_model")


def test_summary_splits_families_and_eff():
    def mock_call(prompt):
        # project task api-auth-order: correct only when guarded
        if "authenticate" in prompt.lower() or "[memory]" in prompt:
            return "c.authenticate()\nc.connect()"
        return "c.connect()"
    proj = next(t for t in LV.TASKS if t["id"] == "api-auth-order")
    res = LV.validate([proj], trials=4, call=mock_call)
    summ = LV._summarize(res, [proj])
    assert summ["eff_project"] == 1.0 and summ["n_project_with_pitfall"] == 1
    assert summ["mean_rate_without"] == 1.0 and summ["mean_rate_with"] == 0.0
    print("ok test_summary_splits_families_and_eff")


def test_no_harm_when_model_already_correct():
    task = next(t for t in LV.TASKS if t["id"] == "eq-none")
    res = LV.validate([task], trials=3, call=lambda p: "def m(x): return x is None")
    r = res["eq-none"]
    assert r["rate_without"] == 0.0 and r["rate_with"] == 0.0     # nothing to prevent, no harm
    assert r["rel_reduction"] is None
    print("ok test_no_harm_when_model_already_correct")


if __name__ == "__main__":
    test_checks_self_test()
    test_paired_accounting_with_mock_model()
    test_summary_splits_families_and_eff()
    test_no_harm_when_model_already_correct()
    print("\nall live_validation self-checks passed")
