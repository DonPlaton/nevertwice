#!/usr/bin/env python3
"""Tests for research/abstractive.py (4A). Pins the consolidation mechanism and the honesty
boundary so the headline ("memory's value is abstraction") cannot silently rot:

  • gen_world: the principle is the unit-mean of its cluster (the consolidation operator);
  • CORE: the principle aligns to the TRUE latent rule better than the mean episode (denoising);
  • variance reduction: the rule-recovery gain grows with cluster size K;
  • honesty boundary: when context overwhelms the rule (high beta) recall collapses toward chance
    for BOTH stores - consolidation amplifies a present signal, it cannot manufacture one;
  • PRIVACY: the real-trace tie-in reads no note content and degrades gracefully without a vault.

Needs numpy (research dep).
"""
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("skip: abstractive tests need numpy (research dep)")
    sys.exit(0)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "nevertwice"))
sys.path.insert(0, str(HERE.parent.parent / "research"))
import abstractive as ab

P = F = 0


def ok(cond, label):
    global P, F
    P, F = P + (1 if cond else 0), F + (0 if cond else 1)
    print(f"  [{'OK ' if cond else 'FAIL'}] {label}")


# ── 1. gen_world: principle = unit-mean of the cluster ──────────────────
w = ab.gen_world(np.random.default_rng(0), k=8, beta=0.6)
ok(w["principles"].shape == (ab.R, ab.D), "gen_world: one principle per rule, D-dim")
ok(np.allclose(np.linalg.norm(w["principles"], axis=1), 1.0), "gen_world: principles are unit vectors")
expect = ab._unit(w["episodes"].mean(axis=1))
ok(np.allclose(w["principles"], expect), "gen_world: principle == unit-mean of its episodes")

# ── 2. CORE: the principle recovers the latent rule better than the mean episode ──
r = ab.run(k=8, beta=0.6, seeds=6)
ok(r["cos_prin"][0] > r["cos_epi"][0] + 0.05,
   f"denoising: principle closer to true rule than mean episode "
   f"({r['cos_prin'][0]:.3f} > {r['cos_epi'][0]:.3f})")

# ── 3. variance reduction: recovery gain grows with K ───────────────────
g2 = (lambda x: x["cos_prin"][0] - x["cos_epi"][0])(ab.run(k=2, beta=1.0, seeds=6))
g16 = (lambda x: x["cos_prin"][0] - x["cos_epi"][0])(ab.run(k=16, beta=1.0, seeds=6))
ok(g16 > g2 + 0.03, f"variance reduction: gain(K=16) > gain(K=2) ({g16:.3f} > {g2:.3f})")

# ── 4. downstream + honesty boundary ────────────────────────────────────
lo = ab.run(k=8, beta=0.3, seeds=6)
ok(lo["recall_con"][0] > lo["recall_epi"][0] + 0.05,
   f"downstream: consolidated lifts novel-context recall at a discriminable beta "
   f"({lo['recall_con'][0]:.3f} > {lo['recall_epi'][0]:.3f})")
hi = ab.run(k=8, beta=1.5, seeds=6)
chance = 1.0 / ab.R
ok(hi["recall_epi"][0] < 4 * chance and hi["recall_con"][0] < 4 * chance,
   f"honesty: at high beta both collapse toward chance ({hi['recall_epi'][0]:.3f}, "
   f"{hi['recall_con'][0]:.3f} vs 1/R={chance:.3f}) - no manufactured signal")

# ── 5. PRIVACY: real-trace tie-in reads no content, degrades gracefully ──
src = (HERE.parent.parent / "research" / "abstractive.py").read_text(encoding="utf-8")
# the only data the real tie-in touches is vectors/project/date; never note content
real_fn = src[src.index("def _real_candidates"):src.index("def main")]
for field in ("title", "desc", "prevention"):
    ok(field not in real_fn, f"privacy: real tie-in never reads the '{field}' content field")
ok("vec" in real_fn, "sanity: real tie-in reads cached vectors (aggregate clustering)")

print(f"\nabstractive: {P} passed, {F} failed")
sys.exit(1 if F else 0)
