#!/usr/bin/env python3
"""The abstention gate fails safe: an unknown value is refused, and a bad setting is ignored.

`budget.py` exists to refuse an item that is not worth its tokens. Two inputs made it do the
opposite.

`_env_int` is `int(_env_float(...))`, and `_env_float` only catches what `float()` raises - so
`nan`, `inf` and `1e400` parse happily and then `int()` raises `ValueError`/`OverflowError` out of
`Policy()`. A mistyped environment variable took down `api.budget_policy()` and every
`api.guards_check(budget=...)` call: the gate crashed the caller instead of falling back to its
default.

And `value < self.min_value` is False when `value` is NaN, so an item whose expected value is NaN
always spends. A fused or calibrated score is exactly where a NaN comes from. "Unknown" is not
"worth it"; the fail-safe direction is to abstain.

    python tests/_test_budget_fails_safe.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths

P = F = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global P, F
    if ok:
        P += 1
        print(f"  ok   {label}")
    else:
        F += 1
        print(f"  FAIL {label}" + (f" - {detail}" if detail else ""))


import budget  # noqa: E402

print("# a setting that is not a number falls back to the default instead of raising")
for raw in ("nan", "inf", "-inf", "1e400", "NaN", "Infinity", "banana", ""):
    os.environ["NEVERTWICE_BUDGET_TURN_TOKENS"] = raw
    try:
        pol = budget.Policy()
        ok, detail = isinstance(pol.per_turn_tokens, int), f"per_turn_tokens={pol.per_turn_tokens!r}"
    except Exception as exc:                                  # noqa: BLE001 - that is the defect
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    check(f"{raw!r} does not take the policy down", ok, detail)
os.environ.pop("NEVERTWICE_BUDGET_TURN_TOKENS", None)

print("# and a float setting that is not finite is refused the same way")
for raw in ("nan", "inf"):
    os.environ["NEVERTWICE_BUDGET_MIN_VALUE"] = raw
    try:
        pol = budget.Policy()
        finite = pol.min_value == pol.min_value and abs(pol.min_value) != float("inf")
        ok, detail = finite, f"min_value={pol.min_value!r}"
    except Exception as exc:                                  # noqa: BLE001
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    check(f"min_value {raw!r} falls back to a finite default", ok, detail)
os.environ.pop("NEVERTWICE_BUDGET_MIN_VALUE", None)

print("# an item whose value is unknown is refused, not spent on")
pol = budget.Policy(min_value=0.9)
ledger = budget.Ledger()
d = pol.decide(ledger, item="a lesson", tokens=40, value=float("nan"))
check("a NaN value does not spend", not d.spend, f"reason={d.reason!r}")
check("and it says why", d.reason in budget.VALUE_REASONS or "value" in d.reason, f"{d.reason!r}")
d2 = pol.decide(budget.Ledger(), item="a lesson", tokens=40, value=float("inf"))
check("an infinite value does not spend either", not d2.spend, f"reason={d2.reason!r}")

print("# and the gate still does its ordinary job")
good = pol.decide(budget.Ledger(), item="a lesson", tokens=40, value=0.95)
check("a valuable item is still spent on", good.spend, f"{good.reason!r}")
poor = pol.decide(budget.Ledger(), item="a lesson", tokens=40, value=0.10)
check("a low-value item is still refused", not poor.spend and poor.reason == "below_value_threshold",
      f"{poor.reason!r}")

print("# and the public surface survives a guard ledger a human has edited")
from _sandbox import make_sandbox  # noqa: E402
import memory_hook as m  # noqa: E402

make_sandbox(m, offline=True)
import api  # noqa: E402

ledger = [{"id": "g1", "confidence": None, "status": "advisory"},
          {"id": "g2", "confidence": "high", "status": "blocking"},
          {"id": "g3", "status": "advisory"}]
hits = [{"id": gid, "message": "x" * 40, "status": st}
        for gid, st in (("g1", "advisory"), ("g2", "blocking"), ("g3", "advisory"))]
try:
    kept = api._apply_budget(hits, ledger, budget.Ledger(), budget.Policy(min_value=0.1))
    ok, detail = True, [h["id"] for h in kept]
except Exception as exc:                                      # noqa: BLE001 - that is the defect
    ok, detail = False, f"{type(exc).__name__}: {exc}"
check("a null or word confidence does not abort guards_check", ok, str(detail))
if ok:
    blocking = next((h for h in kept if h["id"] == "g2"), {})
    check("a blocking hit carries its budget decision like every other hit",
          "budget" in blocking,
          "the exemption belongs in the reason, not in a missing key a caller will index into")

print()
print(f"budget fails safe: {P} passed, {F} failed")
sys.exit(1 if F else 0)
