#!/usr/bin/env python3
"""Tests for research/rare_event.py (2C). Pins the productive tension: frequency
weighting buries the rare precursor (catastrophic tail-recall), the inverse-frequency ×
consequence term recovers it, and the relevance-gated variant keeps the tail while cutting
the false alarms the always-on term floods. Needs numpy."""
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("skip: rare_event tests need numpy (research dep)")
    sys.exit(0)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "nevertwice"))
sys.path.insert(0, str(HERE.parent.parent / "research"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import rare_event as r

P = F = 0


def ok(cond, label):
    global P, F
    P, F = P + (1 if cond else 0), F + (0 if cond else 1)
    print(f"  [{'OK ' if cond else 'FAIL'}] {label}")


# world shape: rare high-consequence precursors vs common high-recurrence normals
world = r.gen_world(np.random.default_rng(0))
prec = world["is_prec"]
ok(int(prec.sum()) == r.N_PREC, f"world has {r.N_PREC} precursors")
ok(world["conseq"][prec].min() > 0 and float(world["conseq"][~prec].max()) == 0.0,
   "only precursors carry consequence")
ok(world["recur"][prec].max() <= 2 and world["recur"][~prec].min() >= 1,
   "precursors are rare, normals recur")

# rare-event salience up-weights precursors; recurrence salience up-weights normals
rare_sal = r.salience(world, "rare-event")
recur_sal = r.salience(world, "recurrence")
ok(rare_sal[prec].mean() > rare_sal[~prec].mean(), "rare-event salience favours precursors")
ok(recur_sal[~prec].mean() > recur_sal[prec].mean(), "recurrence salience favours normals")

# determinism + the tension, on a small run
r.SEEDS, r.QUERIES = 2, 150
M = r.run()


def mean(md, k):
    return float(np.mean(M[md][k]))


ok(mean("recurrence", "tail") < mean("relevance", "tail") < mean("rare-event", "tail"),
   f"frequency buries the precursor; inverse recovers it "
   f"({mean('recurrence','tail'):.2f} < {mean('relevance','tail'):.2f} < {mean('rare-event','tail'):.2f})")
ok(mean("rare-event", "falsealarm") > mean("rare-gated", "falsealarm"),
   f"relevance-gating cuts false alarms ({mean('rare-event','falsealarm'):.2f} → "
   f"{mean('rare-gated','falsealarm'):.2f})")
ok(mean("rare-gated", "tail") > mean("recurrence", "tail") + 0.3,
   "the gated variant still recovers most of the tail")
ok(mean("rare-gated", "common") >= mean("recurrence", "common") - 0.05,
   "the gated variant keeps common recall (no real cost there)")

print(f"\nrare_event: {P} passed, {F} failed")
sys.exit(1 if F else 0)
