"""Shared helpers for the research benches (W12 DRY boundary).

The standalone benches each computed the same mean ± 95% CI inline; this is the one canonical home
for such cross-bench utilities (alongside _rerank.py). gen_world stays per-module on purpose — each
experiment's synthetic world is a distinct generator, not duplicated boilerplate.
"""
import numpy as np


def ci(xs):
    """Mean and 95% half-width (1.96·SE) of a sample. Empty → (0, 0); singleton → (x, 0)."""
    a = np.asarray(xs, dtype=float)
    if a.size == 0:
        return 0.0, 0.0
    return float(a.mean()), (float(1.96 * a.std(ddof=1) / np.sqrt(a.size)) if a.size > 1 else 0.0)


_ci = ci          # the benches reference `_ci`; keep the private alias so call sites are untouched
