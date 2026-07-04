#!/usr/bin/env python3
"""RESEARCH — rare-event / black-swan memory (roadmap 2C).

THESIS & THE PRODUCTIVE TENSION. 1A's recurrence prior says *frequent ⇒ valuable* — right for
the common gotcha you keep hitting. But a black-swan **precursor** is the opposite: seen rarely
(once, before the last regime change), yet decisive. Frequency weighting BURIES it. The fix is
the *inverse*: an inverse-frequency × **consequence** salience that up-weights the rare-but-high-
impact memory. The same boost that helps recurring-lesson recall (1A) therefore HURTS tail recall —
no single global salience is right for both. This module quantifies that tension and shows a
risk-mode salience recovers tail-event analogues a recurrence-weighted ranker discards.

WORLD. A regime-change stream: N_NORMAL common pattern clusters (high recurrence, consequence≈0)
plus N_PREC rare **precursors**, each sitting just off a normal cluster (SUBTLE — it looks almost
normal, so relevance is ambiguous) with recurrence 1–2 and a high consequence; each precedes a
catastrophe by a lead time. Two query kinds: a precursor recurs (tail) → gold = the matching
precursor memory; a normal pattern recurs (common) → gold = a high-recurrence normal memory.

RANKERS (cosine + W·normalised salience):  relevance | recurrence (log n, 1A) |
rare-event (consequence / (1+log n) — inverse-frequency × consequence).

METRICS. tail-recall@k (right precursor surfaced) & warned lead-time; common-recall@k; and the
false-alarm rate (a precursor surfaced on a NORMAL query — the cost of risk-weighting).

    python research/rare_event.py            # report
    python research/rare_event.py --save     # + rare_event.json (+ .png if mpl)
    python research/rare_event.py --quick    # smoke

Research dep: numpy (matplotlib optional). Seeded; deterministic.
"""
import json
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("rare_event needs numpy (research dep): pip install numpy", file=sys.stderr)
    sys.exit(2)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

QUICK = "--quick" in sys.argv
SAVE = "--save" in sys.argv
DIM = 48
N_NORMAL = 10
NORMAL_PER = 20            # notes per common cluster (high recurrence)
N_PREC = 8                 # rare precursor types
PREC_OFFSET = 0.55         # how far a precursor sits off its normal cluster (small ⇒ subtle)
SIGMA_Q = 0.40             # query noise (ambiguity)
K = 5
W = 0.35                   # salience weight (additive on cosine)
QUERIES = 200 if QUICK else 700
SEEDS = 3 if QUICK else 6


def gen_world(rng):
    """Common clusters (high recurrence, no consequence) + rare precursors (low recurrence,
    high consequence, sitting just off a normal cluster). Returns parallel arrays."""
    cent = rng.normal(size=(N_NORMAL, DIM))
    cent /= np.linalg.norm(cent, axis=1, keepdims=True)
    vecs, recur, conseq, is_prec, prec_type, lead = [], [], [], [], [], []
    for c in range(N_NORMAL):
        for _ in range(NORMAL_PER):
            v = cent[c] + 0.12 * rng.normal(size=DIM)
            vecs.append(v / np.linalg.norm(v))
            recur.append(int(min(rng.zipf(1.6) + 4, 40)))     # common ⇒ frequently re-seen
            conseq.append(0.0)
            is_prec.append(False); prec_type.append(-1); lead.append(0)
    for p in range(N_PREC):
        c = int(rng.integers(N_NORMAL))
        off = rng.normal(size=DIM); off /= np.linalg.norm(off)
        v = cent[c] + PREC_OFFSET * off                       # subtle: just off a normal cluster
        vecs.append(v / np.linalg.norm(v))
        recur.append(int(rng.integers(1, 3)))                 # rare
        conseq.append(float(rng.uniform(5, 20)))              # high impact
        is_prec.append(True); prec_type.append(p); lead.append(int(rng.integers(2, 9)))
    return {"vec": np.array(vecs), "recur": np.array(recur, float),
            "conseq": np.array(conseq), "is_prec": np.array(is_prec),
            "prec_type": np.array(prec_type), "lead": np.array(lead), "cent": cent,
            "prec_idx": [i for i, p in enumerate(is_prec) if p]}


def _norm(x):
    lo, hi = x.min(), x.max()
    return (x - lo) / (hi - lo) if hi > lo else np.zeros_like(x)


def salience(world, mode):
    """Per-note salience feature (normalised to [0,1])."""
    if mode == "recurrence":
        return _norm(np.log1p(world["recur"]))
    if mode == "rare-event":
        return _norm(world["conseq"] / (1.0 + np.log1p(world["recur"])))   # inverse-freq × consequence
    return np.zeros(len(world["recur"]))                                   # relevance-only


# the rare-gated mode multiplies consequence INTO relevance, so an irrelevant high-consequence
# note can't surface (cuts the false alarms the always-on additive term floods); the others add.
MODES = ("relevance", "recurrence", "rare-event", "rare-gated")


def rank(world, qv, mode, feat):
    cos = world["vec"] @ qv
    if mode == "rare-gated":
        return np.argsort(-(cos * (1.0 + W * feat["rare-event"])))
    return np.argsort(-(cos + W * feat[mode]))


def run():
    M = {md: {"tail": [], "common": [], "falsealarm": [], "lead": []} for md in MODES}
    for seed in range(SEEDS):
        wrng = np.random.default_rng(33000 + seed)
        world = gen_world(wrng)
        feat = {md: salience(world, md) for md in ("relevance", "recurrence", "rare-event")}
        cent, prec_idx = world["cent"], world["prec_idx"]
        qrng = np.random.default_rng(44000 + seed)
        for _ in range(QUERIES):
            # TAIL query: a precursor recurs → gold = its memory
            j = prec_idx[int(qrng.integers(len(prec_idx)))]
            qv = world["vec"][j] + SIGMA_Q * qrng.normal(size=DIM)
            qv /= np.linalg.norm(qv)
            # COMMON query: a normal pattern recurs → gold = a same-cluster high-recurrence note
            cc = int(qrng.integers(N_NORMAL))
            qc = cent[cc] + SIGMA_Q * qrng.normal(size=DIM)
            qc /= np.linalg.norm(qc)
            for md in MODES:
                top_t = rank(world, qv, md, feat)[:K]
                M[md]["tail"].append(1.0 if j in top_t else 0.0)
                M[md]["lead"].append(world["lead"][j] if j in top_t else 0)
                top_c = rank(world, qc, md, feat)[:K]
                # common hit = a same-normal-cluster note surfaced (cosine to cluster centroid high)
                hit_c = any((not world["is_prec"][s]) and
                            (world["vec"][s] @ cent[cc]) > 0.8 for s in top_c)
                M[md]["common"].append(1.0 if hit_c else 0.0)
                M[md]["falsealarm"].append(1.0 if any(world["is_prec"][s] for s in top_c) else 0.0)
    return M


def _m(xs):
    return float(np.mean(xs)) if xs else 0.0


def main():
    bar = "=" * 78
    print(bar)
    print("  RARE-EVENT / BLACK-SWAN MEMORY (2C) — inverse-frequency × consequence salience")
    print(bar)
    print(f"  world: {N_NORMAL} common clusters × {NORMAL_PER} + {N_PREC} rare precursors (dim {DIM}); "
          f"{QUERIES}×{SEEDS} queries; top-{K}, W={W}")
    M = run()

    print(f"\n— the productive tension: same boost, opposite effect by query kind —")
    print(f"  {'salience':12} {'TAIL-recall@'+str(K):>14} {'COMMON-recall@'+str(K):>16} {'false-alarm':>12}")
    for md in MODES:
        print(f"  {md:12} {_m(M[md]['tail']):>14.3f} {_m(M[md]['common']):>16.3f} "
              f"{_m(M[md]['falsealarm']):>12.3f}")
    tr, rr = _m(M["rare-event"]["tail"]), _m(M["recurrence"]["tail"])
    cr, cc = _m(M["recurrence"]["common"]), _m(M["rare-event"]["common"])
    print(f"\n  → TAIL: rare-event {tr:.3f} vs recurrence {rr:.3f} ({tr - rr:+.3f}) — the inverse term "
          f"surfaces the rare precursor that frequency-weighting (1A) buries.")
    print(f"  → COMMON: recurrence {cr:.3f} vs rare-event {cc:.3f} ({cc - cr:+.3f}); no single global "
          f"salience wins both — frequency and inverse-frequency are opposite priors.")
    fg, fr = _m(M["rare-gated"]["falsealarm"]), _m(M["rare-event"]["falsealarm"])
    print(f"  → the cost is false alarms: always-on rare-event cries wolf ({fr:.3f}); the "
          f"relevance-GATED variant keeps tail-recall {_m(M['rare-gated']['tail']):.3f} while cutting "
          f"\n    false alarms to {fg:.3f} — the practical risk operating point (sensitivity↔specificity).")

    lr, lf = _m(M["rare-gated"]["lead"]), _m(M["recurrence"]["lead"])
    print(f"\n— warned lead-time (steps of warning before the catastrophe; 0 = missed) —")
    print(f"  rare-gated {lr:.2f}   recurrence {lf:.2f}   → risk-mode memory warns "
          f"{lr - lf:+.2f} steps earlier on average.")

    if SAVE:
        out = {"normal_clusters": N_NORMAL, "precursors": N_PREC, "k": K, "W": W,
               "metrics": {md: {kk: _m(M[md][kk]) for kk in M[md]} for md in M}}
        p = HERE / "rare_event.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved → {p}")
        _figure(M, HERE / "rare_event.png")
    print(bar)


def _figure(M, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"  [figure skipped: matplotlib unavailable — {e}]")
        return
    modes = ("relevance", "recurrence", "rare-event")
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    x = np.arange(len(modes))
    ax.bar(x - 0.2, [_m(M[md]["tail"]) for md in modes], 0.38, label=f"tail-recall@{K}")
    ax.bar(x + 0.2, [_m(M[md]["common"]) for md in modes], 0.38, label=f"common-recall@{K}")
    ax.plot(x, [_m(M[md]["falsealarm"]) for md in modes], "k^--", label="false-alarm rate")
    ax.set_xticks(x)
    ax.set_xticklabels(modes)
    ax.set_ylabel("rate")
    ax.set_title("Rare-event salience: recovers the tail, trades the common (the tension)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print(f"  figure → {path}")


if __name__ == "__main__":
    main()
