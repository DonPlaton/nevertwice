#!/usr/bin/env python3
"""Tests for research/bandit.py (1B). Pins the claims the flagship rests on:
the bandit LEARNS from the feedback signal (true reward beats a shuffled-reward
control and its weights converge toward the offline optimum θ*), the run is
deterministic, and the per-query series are well-formed. Needs numpy.
"""
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("skip: bandit tests need numpy (research dep)")
    sys.exit(0)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import research.posterior_model as pm
import research.bandit as bd

P = F = 0


def ok(cond, label):
    global P, F
    P, F = P + (1 if cond else 0), F + (0 if cond else 1)
    print(f"  [{'OK ' if cond else 'FAIL'}] {label}")


# one seed's standardised temporal stream + the offline optimum θ*
raw = pm.build_dataset([0])
mean, std = pm.standardize_params(raw)
stream = [((X - mean) / std, t) for X, t in raw]
theta_star = bd.ridge_offline([stream])

ok(len(stream) > 100, f"built a non-trivial stream ({len(stream)} queries)")
ok(theta_star.shape == (bd.D,) and np.all(np.isfinite(theta_star)), "θ* is finite, right shape")
ok(theta_star[1] > 0, "θ* recurrence weight is positive (frequency prior helps)")

# determinism: identical inputs → identical trajectory
u1, r1, c1 = bd.run_bandit(stream, theta_star)
u2, r2, c2 = bd.run_bandit(stream, theta_star)
ok(np.array_equal(u1, u2) and np.array_equal(c1, c2), "run_bandit is deterministic")
ok(len(u1) == len(stream) == len(r1) == len(c1), "per-query series have the stream length")
ok(np.all((u1 == 0) | (u1 == 1)), "utility is a 0/1 hit series")

# learning: weights converge toward θ* (late distance < early distance)
early, late = float(np.mean(c1[:len(c1) // 10])), float(np.mean(c1[-len(c1) // 10:]))
ok(late < early, f"weights converge toward θ* (cos-dist {early:.3f} → {late:.3f})")

# the signal matters: a shuffled reward must NOT learn as well as the true reward
rng = np.random.default_rng(7)
us, _, cs = bd.run_bandit(stream, theta_star, shuffle_reward=True, rng=rng)
ok(float(np.mean(c1[-len(c1) // 5:])) < float(np.mean(cs[-len(cs) // 5:])),
   "true-reward bandit converges closer to θ* than the shuffled control")
ok(float(u1[len(u1) // 2:].mean()) > float(us[len(us) // 2:].mean()),
   "true-reward bandit out-utilities the shuffled control (gain is from the signal)")

# beats a mismatched static ranker (relevance-only), once warmed up
rel = bd.run_static(stream, lambda Xs: Xs[:, 0])
ok(float(u1[len(u1) // 2:].mean()) > float(rel[len(rel) // 2:].mean()),
   "learned bandit beats the relevance-only static ranker (post-warmup)")

print(f"\nbandit: {P} passed, {F} failed")
sys.exit(1 if F else 0)
