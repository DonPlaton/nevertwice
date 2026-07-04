#!/usr/bin/env python3
"""Tests for research/longitudinal_bench.py (3A). Pins the two faithfulness claims
the benchmark rests on — so a future edit cannot silently break them:

  • _salience() reproduces memory_hook._salience_mult EXACTLY (incl. the F2
    recurrence-slowed decay), across recurrence/resolved/confidence combinations;
  • _cos() equals memory_hook.cosine for unit vectors (the bug that made the first
    cut rank by insertion order — m.cosine returns 0.0 on a numpy array, so the
    benchmark MUST use the dot-product shortcut, never m.cosine, on world vectors).

Plus metric-math and determinism/smoke checks. Needs numpy (research dep).
"""
import math
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("skip: longitudinal_bench tests need numpy (research dep)")
    sys.exit(0)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import memory_hook as m
import research.longitudinal_bench as lb

P = F = 0


def ok(cond, label):
    global P, F
    P, F = P + (1 if cond else 0), F + (0 if cond else 1)
    print(f"  [{'OK ' if cond else 'FAIL'}] {label}")


# ── 1. salience parity: bench _salience ≡ production _salience_mult ──────
stem = "2020-01-01-proj-mistake-foo"            # a real, old, parseable typed stem
age = m._note_age_days(stem)
ok(age > 0, f"sanity: constructed stem has positive age ({age:.0f}d)")
for n in (1, 2, 5, 20):
    for resolved in (False, True):
        for conf in (None, 0.0, 0.5, 0.9, 1.0):
            rec = {"recurrence": n, "resolved": resolved, "confidence": conf}
            prod = m._salience_mult(stem, rec)
            bench = lb._salience(dict(rec, age=age))
            ok(abs(prod - bench) < 1e-12,
               f"salience parity n={n} resolved={resolved} conf={conf} "
               f"({prod:.6f} vs {bench:.6f})")

# F2 specifically: a recurring note decays LESS than a one-off of the same age
# (use a moderate age — a very old note floors both at DECAY_FLOOR and hides it)
one_off = lb._salience({"age": 200, "recurrence": 1, "confidence": None})
recurring = lb._salience({"age": 200, "recurrence": 20, "confidence": None})
ok(recurring > one_off, f"F2: recurrence slows decay ({recurring:.3f} > {one_off:.3f})")

# ── 2. cosine guard: _cos(unit, unit) == m.cosine(list, list) ───────────
rng = np.random.default_rng(0)
def _unit():
    v = rng.normal(size=lb.DIM)
    return v / np.linalg.norm(v)
worst = max(abs(lb._cos(a, b) - m.cosine(a.tolist(), b.tolist()))
            for a, b in ((_unit(), _unit()) for _ in range(50)))
ok(worst < 1e-9, f"cosine: dot of unit vectors == m.cosine(list,list) (max err {worst:.1e})")
# the trap that caused the original bug — m.cosine must NOT be used on numpy rows:
ok(m.cosine(_unit(), _unit()) == 0.0,
   "guard: m.cosine on numpy arrays returns 0.0 (why the bench uses _cos)")

# ── 3. metric math ──────────────────────────────────────────────────────
rec, rr, ndcg = lb._metrics(["t", "a", "b"], "t")
ok(rec[1] == 1 and rr == 1.0 and abs(ndcg - 1.0) < 1e-9, "metrics: hit@1 → 1/1/1")
rec, rr, ndcg = lb._metrics(["a", "t", "b"], "t")
ok(rec[1] == 0 and rec[3] == 1 and rr == 0.5 and abs(ndcg - 1 / math.log2(3)) < 1e-9,
   "metrics: hit@2 → R@1=0,R@3=1,RR=.5,nDCG=1/log2(3)")
rec, rr, ndcg = lb._metrics(["a", "b", "c"], "t")
ok(rec[5] == 0 and rr == 0.0 and ndcg == 0.0, "metrics: miss → all zero")

# ── 4. determinism + smoke ──────────────────────────────────────────────
w1 = lb.gen_world(np.random.default_rng(2000))
w2 = lb.gen_world(np.random.default_rng(2000))
ok(np.array_equal(w1["vec"], w2["vec"]) and w1["tok"] == w2["tok"],
   "determinism: gen_world is reproducible for a fixed seed")

lb.SEEDS, lb.T_SESSIONS = 1, 80           # shrink for a fast end-to-end
lb._WORLD_CACHE.clear()
agg, strat, curve, temporal = lb.run(0.85)
lb._WORLD_CACHE.clear()
ok(set(agg) == set(lb.MODES), "smoke: run() reports every ranker mode")
ok(len(agg["shipped"]["r@1"]) > 0, "smoke: queries were issued")
ok(all(0.0 <= v <= 1.0 for md in lb.MODES for v in agg[md]["r@1"]),
   "smoke: every recall@1 observation is in [0,1]")

print(f"\nlongitudinal_bench: {P} passed, {F} failed")
sys.exit(1 if F else 0)
