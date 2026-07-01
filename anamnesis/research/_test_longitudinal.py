#!/usr/bin/env python3
"""Self-check for longitudinal_improvement.py (axis D). Verifies the token-accounting
invariants, determinism, that memory reduces errors, that v2 spends far fewer memory
tokens than v1 for the same knowledge, and that a high false-positive rate honestly
erodes v2's edge (the anti-rigging check). Pure sim — no network, no LLM."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import longitudinal_improvement as L   # noqa: E402


def test_determinism():
    a = L.run_arm("v2", L.make_family(200, 3), 0.4, 3)
    b = L.run_arm("v2", L.make_family(200, 3), 0.4, 3)
    assert a == b, "same seed must reproduce exactly"
    print("ok test_determinism")


def test_memory_reduces_errors():
    out = L.evaluate(n=200, base_fail=0.4, trials=20)
    assert out["nomem"]["errors"] > out["v1"]["errors"]
    assert out["nomem"]["errors"] > out["v2"]["errors"]
    # both arms apply the same knowledge effect → similar error counts (within noise)
    assert abs(out["v1"]["errors"] - out["v2"]["errors"]) < 5, (out["v1"], out["v2"])
    print("ok test_memory_reduces_errors")


def test_v2_far_cheaper_than_v1():
    out = L.evaluate(n=200, base_fail=0.4, trials=20)
    assert out["v2"]["mem_tokens"] < out["v1"]["mem_tokens"] / 5, out   # >5x cheaper memory
    assert out["v2"]["improvement_per_1k_tok"] > out["v1"]["improvement_per_1k_tok"]
    # v2 is a NET token saving vs no-mem (prevented redo outweighs guard fires); v1 is not
    assert out["v2"]["total_tokens"] < out["nomem"]["total_tokens"]
    print("ok test_v2_far_cheaper_than_v1")


def test_v1_taxes_every_task():
    # v1 memory tokens == inject cost × N exactly (unconditional tax), pitfall or not
    tasks = L.make_family(150, 9)
    r = L.run_arm("v1", tasks, 0.4, 9)
    assert r["mem_tokens"] == L.C_INJECT_V1 * len(tasks), r["mem_tokens"]
    print("ok test_v1_taxes_every_task")


def test_high_false_positive_erodes_v2_honestly():
    clean = L.evaluate(n=200, base_fail=0.4, trials=25, guard_fp_rate=0.0)
    noisy = L.evaluate(n=200, base_fail=0.4, trials=25, guard_fp_rate=0.3)
    # the anti-rigging check: a noisy guard population is Popperian — it self-retires under
    # false positives, which costs prevention. So the honest signal is a LOWER
    # improvement-per-token and NOT-FEWER errors, i.e. the benchmark does not always flatter v2.
    assert noisy["v2"]["improvement_per_1k_tok"] < clean["v2"]["improvement_per_1k_tok"], \
        "FP must erode v2's improvement-per-token"
    assert noisy["v2"]["errors"] >= clean["v2"]["errors"] - 0.5, \
        "retirement under FP should cost some prevention, not gain it"
    print("ok test_high_false_positive_erodes_v2_honestly")


def test_nomem_spends_zero_memory():
    r = L.run_arm("nomem", L.make_family(100, 2), 0.4, 2)
    assert r["mem_tokens"] == 0 and r["guard_fires"] == 0
    print("ok test_nomem_spends_zero_memory")


if __name__ == "__main__":
    test_determinism()
    test_memory_reduces_errors()
    test_v2_far_cheaper_than_v1()
    test_v1_taxes_every_task()
    test_high_false_positive_erodes_v2_honestly()
    test_nomem_spends_zero_memory()
    print("\nall longitudinal self-checks passed")
