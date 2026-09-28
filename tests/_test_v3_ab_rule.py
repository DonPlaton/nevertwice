#!/usr/bin/env python3
"""PREREG-V3 §4.6 (K87), plan step A2.8: the recording-vs-raw-forward A/B tolerance rule, checked at its edges.

* Each widening at its boundary: calls +/- 5 %, tokens +/- 10 %, lost_share +/- 1 pp, items +/- 10 %, wall time
  + 10 % plus 20 ms per call (a faster recording run passes); a hair beyond each edge fails, by name.
* client_abandoned > 0 fails; the added time to first byte is judged by percentiles - p50 21 ms or p95 101 ms fail,
  a mean under the bound does not rescue a p95 over it.
* The rule wants exactly two runs of each leg.
* The hop microbenchmark runs against a local echo and reports added p50/p95 for raw-forward and recording.

    python tests/_test_v3_ab_rule.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite

_spec = importlib.util.spec_from_file_location("v3_ab_rule", ROOT / "research" / "v3" / "ab_rule.py")
A = importlib.util.module_from_spec(_spec)
sys.modules["v3_ab_rule"] = A
_spec.loader.exec_module(A)

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


RAW = [{"calls": 10.0, "tokens_in": 1000.0, "tokens_out": 200.0, "lost_share": 0.02, "items": 8.0, "wall_s": 5.0},
       {"calls": 12.0, "tokens_in": 1100.0, "tokens_out": 220.0, "lost_share": 0.04, "items": 10.0, "wall_s": 6.0}]
OK_TTFB = [5.0] * 95 + [50.0] * 5


def verdict(rec_change: dict, **kw):
    rec = [dict(RAW[0]), dict(RAW[1], **rec_change)]
    return A.ab_verdict(RAW, rec, client_abandoned=kw.get("abandoned", 0), ttfb_added_ms=kw.get("ttfb", OK_TTFB))


check("identical legs are in tolerance", verdict({})["in_tolerance"], str(verdict({})["failures"]))
edges = {"calls": (12.0 * 1.05, 10.0 * 0.95), "tokens_in": (1100 * 1.10, 1000 * 0.90), "tokens_out": (220 * 1.10, 200 * 0.90),
         "lost_share": (0.04 + 0.01, 0.02 - 0.01), "items": (10 * 1.10, 8 * 0.90)}
EPS = 1e-6
for m, (hi, lo) in edges.items():
    check(f"{m}: exactly at the upper edge ({hi:.6g}) passes", verdict({m: hi})["in_tolerance"],
          str(verdict({m: hi})["failures"]))
    check(f"{m}: just above the upper edge fails, by name", any(m in f for f in verdict({m: hi + max(EPS, abs(hi) * 1e-6)})["failures"]))
    check(f"{m}: exactly at the lower edge ({lo:.6g}) passes", verdict({m: lo})["in_tolerance"], str(verdict({m: lo})["failures"]))
    check(f"{m}: just below the lower edge fails, by name", any(m in f for f in verdict({m: lo - max(EPS, abs(lo) * 1e-6)})["failures"]))
wall_hi = 6.0 * 1.10 + 0.020 * 12.0
check("wall time: + 10 % plus 20 ms per call is the upper edge", verdict({"wall_s": wall_hi})["in_tolerance"])
check("wall time: just above it fails", any("wall_s" in f for f in verdict({"wall_s": wall_hi + 1e-6})["failures"]))
check("wall time: a faster recording run passes (no lower bound)", verdict({"wall_s": 0.5})["in_tolerance"])
check("client_abandoned > 0 fails, by name", any("client_abandoned" in f for f in verdict({}, abandoned=1)["failures"]))
check("added TTFB p50 21 ms fails", any("p50" in f for f in verdict({}, ttfb=[21.0] * 100)["failures"]))
check("added TTFB p50 20 ms passes", verdict({}, ttfb=[20.0] * 100)["in_tolerance"])
tail = [1.0] * 90 + [101.0] * 10
check("a p95 of 101 ms fails even though the mean is under 20 ms", any("p95" in f for f in verdict({}, ttfb=tail)["failures"])
      and sum(tail) / len(tail) < 20)
try:
    A.ab_verdict(RAW[:1], RAW, client_abandoned=0, ttfb_added_ms=OK_TTFB)
    check("the rule wants exactly two runs of each leg", False)
except ValueError:
    check("the rule wants exactly two runs of each leg", True)
_added = getattr(A, "added_over", None)
check("Q-AB-2: added_over - each sample over the baseline's MEDIAN (not its mean), never below 0",
      _added is not None and _added([5.0, 12.0, 2.0, 1.0], [1.0, 2.0, 30.0]) == [3.0, 10.0, 0.0, 0.0],
      str(_added([5.0, 12.0, 2.0, 1.0], [1.0, 2.0, 30.0]) if _added else None))
try:
    _added([1.0], [])
    check("Q-AB-2: added_over refuses an empty baseline (no median, no figure)", False)
except (ValueError, TypeError) as e:
    check("Q-AB-2: added_over refuses an empty baseline (no median, no figure)", _added is not None, repr(e))
_calls: list = []
_real_added = A.added_over


def _spy(samples, baseline):
    _calls.append((samples, baseline))
    return _real_added(samples, baseline)


A.added_over = _spy
try:
    A.hop_benchmark(n=10)
finally:
    A.added_over = _real_added
check("HOP-4 (the auditor): hop_benchmark takes each mode's added time over the DIRECT samples - one baseline list for "
      "both modes, never the mode's own samples", len(_calls) == 2 and _calls[0][1] is _calls[1][1]
      and all(s is not b and len(s) == 10 and len(b) == 10 for s, b in _calls), str([(len(s), len(b)) for s, b in _calls]))
hop = A.hop_benchmark(n=40)
check("the hop microbenchmark reports added p50/p95 for raw-forward and recording",
      all(k in hop and hop[k]["n"] == 40 and hop[k]["p50_ms"] >= 0 and hop[k]["p95_ms"] >= hop[k]["p50_ms"]
          for k in ("raw", "record")), str(hop))
print(f"       measured hop (informational): {hop}")

print(f"\nv3 ab rule: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
