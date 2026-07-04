#!/usr/bin/env python3
"""RESEARCH — retrieval as posterior inference (roadmap 1A).

THESIS. Nevertwice ranks with a hand-tuned stack: a cosine relevance score, an additive
log-recurrence boost, and a multiplicative salience re-weight (recency decay × resolved
× confidence). This module shows that stack IS the log-posterior of one generative model

    score(m│q) = log P(q│m) + Σ_k log P_k(m)
                 └ relevance ┘   └─ priors ─┘

  • relevance likelihood  P(q│m) ∝ exp(cos/T)     → log-term  w_cos·cos   (temperature link)
  • frequency prior       P_freq ∝ n^β            → w_freq·log n          (Bayesian frequency prior)
  • recency  (survival)   P_rec  ∝ exp(−λ·age)    → w_rec·age             (hazard, NOT a fixed half-life)
  • reliability           P_rel  ∝ exp(β·conf)    → w_conf·conf
  • status gate           resolved/superseded     → w_res·[resolved]  (superseded excluded)

so the per-query ranking is a LINEAR score in (cos, log n, age, conf, resolved) — a
conditional-logit / Plackett-Luce top-1 model. We FIT the weights by maximum likelihood
on labeled retrieval data from the 3A longitudinal world (train seeds), then on HELD-OUT
seeds compare the fitted posterior against the shipped hand-tuned heuristic, ablate each
prior (leave-one-out), and check calibration (does cos/T predict relevance?).

Honest scope: relevance here is the SEMANTIC cosine (no lexical/RRF — this isolates the
salience-stack question); numbers therefore differ from the 3A leaderboard's fused ranker.
The posterior is fit to recall on this world, so out-performing the heuristic in-distribution
is partly by construction — the contribution is that the stack is a *calibratable, inter-
pretable* posterior whose fitted priors can be read off and compared to the hand-tuned ones,
not a claim of magic. On a no-recurrence corpus (LongMemEval) the priors are inert and the
posterior reduces to relevance-only — see the note printed at the end.

    python research/posterior_model.py            # fit + report
    python research/posterior_model.py --save     # + posterior_model.json (+ .png if mpl)
    python research/posterior_model.py --quick     # fewer iterations (smoke)

Research dep: numpy (matplotlib optional). Seeded; deterministic.
"""
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("posterior_model needs numpy (research dep): pip install numpy", file=sys.stderr)
    sys.exit(2)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nevertwice"))
import memory_hook as m
import longitudinal_bench as lb

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

QUICK = "--quick" in sys.argv
SAVE = "--save" in sys.argv
ITERS = 150 if QUICK else 500
LR, L2 = 0.5, 1e-3
KS = (1, 3, 5)
FEATURES = ("cos", "log_recurrence", "age_days", "confidence", "resolved")
PRIORS = {1: "frequency", 2: "recency", 3: "reliability", 4: "status"}   # cols (0=cos kept)


# ── labeled dataset from the 3A world ───────────────────────────────────

def build_dataset(seeds, sigma=None):
    """Per query: (raw feature matrix [n_cand × 5], target row index). Reuses the 3A
    longitudinal world — candidates carry the real (cos, recurrence, age, confidence,
    resolved) the production ranker sees."""
    data = []
    for seed in seeds:
        world, events = lb._world_events(seed)
        qrng = np.random.default_rng(9000 + seed * 131)
        for ev in events:
            qv, qt = lb.make_query(qrng, world, ev["lid"], sigma)
            rows, tgt = [], None
            for idx, j in enumerate(ev["pool"]):
                rec = ev["note"][j]
                n = max(1, int(rec["recurrence"]))
                rows.append([lb._cos(qv, world["vec"][j]), math.log(n), rec["age"],
                             float(rec["confidence"]), 1.0 if rec["resolved"] else 0.0])
                if j == ev["lid"]:
                    tgt = idx
            if tgt is not None and len(rows) >= 2:
                data.append((np.array(rows), tgt))
    return data


def standardize_params(data):
    """Per-feature mean/std over all candidate rows (computed on TRAIN only)."""
    allrows = np.vstack([X for X, _ in data])
    mean, std = allrows.mean(0), allrows.std(0)
    std[std < 1e-9] = 1.0                              # guard constant features
    return mean, std


def compile_dataset(data, mean, std):
    """Flatten the per-query matrices into one (ΣnCand × d) array with group offsets,
    so the conditional-logit gradient is a few whole-array ops (reduceat over groups)
    instead of a Python loop per query — the difference between ~2 min and <1 s."""
    Xs = [(X - mean) / std for X, _ in data]
    counts = np.array([len(X) for X in Xs])
    starts = np.zeros(len(Xs), dtype=int)
    starts[1:] = np.cumsum(counts)[:-1]
    X_all = np.vstack(Xs)
    tgt_rows = starts + np.array([t for _, t in data])
    return X_all, starts, counts, tgt_rows, len(data)


# ── conditional-logit MLE (the calibration), vectorised over all queries ─

def fit(comp, mask=None, iters=ITERS, lr=LR, l2=L2):
    """Maximum-likelihood weights for the per-query softmax (Plackett-Luce top-1) by
    gradient ascent. The gradient Σ_q(E_p[x] − x_target) is computed in one pass:
    per-query softmax via segment max/sum (reduceat), then X_allᵀ·p − Σ x_target.
    `mask` zeroes a feature column (leave-one-prior-out)."""
    X_all, starts, counts, tgt_rows, Q = comp
    d = X_all.shape[1]
    keep = np.ones(d) if mask is None else mask
    Xk = X_all * keep
    tgt_sum = Xk[tgt_rows].sum(0)
    w = np.zeros(d)
    for _ in range(iters):
        s = Xk @ w
        s -= np.repeat(np.maximum.reduceat(s, starts), counts)   # per-query shift (stability)
        e = np.exp(s)
        p = e / np.repeat(np.add.reduceat(e, starts), counts)     # per-query softmax
        grad = (Xk.T @ p - tgt_sum) / Q + l2 * w
        w = w - lr * grad
    return w * keep


# ── rankers on raw features ─────────────────────────────────────────────

def score_posterior(X_raw, w, mean, std):
    return ((X_raw - mean) / std) @ w


def score_relevance(X_raw):
    return X_raw[:, 0]                                  # cosine only


def score_heuristic(X_raw):
    """The SHIPPED stack on the cosine basis: (cos + RECUR_BOOST·log n) × salience_mult
    (recurrence-slowed decay × resolved × confidence), with production constants —
    the hand-tuned comparator the posterior must match or beat."""
    cos, logn, age, conf, res = (X_raw[:, i] for i in range(5))
    base = cos + m.RETRIEVAL_RECUR_BOOST * logn
    age_eff = age / (1.0 + logn)
    decay = np.maximum(m.RETRIEVAL_DECAY_FLOOR, 0.5 ** (age_eff / m.RETRIEVAL_DECAY_HALFLIFE))
    sal = decay * np.where(res > 0, m.RETRIEVAL_RESOLVED_WEIGHT, 1.0) \
        * (m.RETRIEVAL_CONF_FLOOR + (1.0 - m.RETRIEVAL_CONF_FLOOR) * conf)
    return base * sal


def evaluate(data, score_fn):
    rec = {k: 0.0 for k in KS}
    mrr = ndcg = 0.0
    for X_raw, t in data:
        order = np.argsort(-score_fn(X_raw), kind="stable")
        pos = int(np.where(order == t)[0][0])
        for k in KS:
            rec[k] += 1.0 if pos < k else 0.0
        mrr += 1.0 / (pos + 1)
        ndcg += 1.0 / math.log2(pos + 2)
    nq = len(data)
    return {**{f"recall@{k}": rec[k] / nq for k in KS},
            "mrr": mrr / nq, "ndcg": ndcg / nq}


# ── calibration: does the relevance link predict relevance? ─────────────

def calibration(data, w, mean, std, bins=10):
    """Reliability diagram: bin candidates by the model's predicted P(target│pool)
    (per-query softmax), report empirical target-rate per bin."""
    hit = np.zeros(bins)
    cnt = np.zeros(bins)
    conf_sum = np.zeros(bins)
    for X_raw, t in data:
        s = score_posterior(X_raw, w, mean, std)
        s -= s.max()
        e = np.exp(s)
        p = e / e.sum()
        for idx, pi in enumerate(p):
            b = min(bins - 1, int(pi * bins))
            cnt[b] += 1
            conf_sum[b] += pi
            if idx == t:
                hit[b] += 1
    nz = cnt > 0
    pred = np.where(nz, conf_sum / np.maximum(cnt, 1), 0.0)
    emp = np.where(nz, hit / np.maximum(cnt, 1), 0.0)
    ece = float(np.sum(cnt[nz] * np.abs(pred[nz] - emp[nz])) / cnt.sum())
    return pred.tolist(), emp.tolist(), cnt.tolist(), ece


# ── report ──────────────────────────────────────────────────────────────

def main():
    seeds = list(range(lb.SEEDS))
    cut = max(1, int(round(len(seeds) * 0.67)))
    train_seeds, test_seeds = seeds[:cut], seeds[cut:] or seeds[-1:]
    train = build_dataset(train_seeds)
    test = build_dataset(test_seeds)
    mean, std = standardize_params(train)
    comp = compile_dataset(train, mean, std)
    w = fit(comp)

    bar = "=" * 78
    print(bar)
    print("  RETRIEVAL AS POSTERIOR INFERENCE (1A) — the salience stack as a calibrated")
    print("  conditional-logit posterior, fit on the 3A longitudinal world")
    print(bar)
    print(f"  train: {len(train)} queries (seeds {train_seeds});  "
          f"test: {len(test)} queries (seeds {test_seeds}); relevance = semantic cosine")

    rankers = {"relevance-only": score_relevance,
               "heuristic (shipped)": score_heuristic,
               "posterior (fitted)": lambda X: score_posterior(X, w, mean, std)}
    print(f"\n— held-out ranking quality —")
    print(f"  {'ranker':22} {'R@1':>7} {'R@3':>7} {'R@5':>7} {'MRR':>7} {'nDCG':>7}")
    res = {}
    for name, fn in rankers.items():
        r = evaluate(test, fn)
        res[name] = r
        print(f"  {name:22} {r['recall@1']:7.3f} {r['recall@3']:7.3f} "
              f"{r['recall@5']:7.3f} {r['mrr']:7.3f} {r['ndcg']:7.3f}")
    lift = res["posterior (fitted)"]["recall@1"] - res["heuristic (shipped)"]["recall@1"]
    print(f"\n  → posterior − heuristic @1: {lift:+.3f} "
          f"(in-distribution; the stack is a calibratable posterior, not a free lunch)")

    print(f"\n— fitted posterior weights (standardized; sign = direction, |·| = importance) —")
    for i, name in enumerate(FEATURES):
        print(f"  {name:16} {w[i]:+.3f}")
    # interpretability: the fitted recency weight implies a hazard rate vs the shipped half-life
    if w[2] < 0:
        print(f"  → recency: fitted weight is negative (older ⇒ less useful), as the survival "
              f"model predicts; shipped uses a fixed {m.RETRIEVAL_DECAY_HALFLIFE:g}-day half-life.")

    print(f"\n— leave-one-prior-out (refit without each prior; Δ = recall@1 lost) —")
    full = res["posterior (fitted)"]["recall@1"]
    loo = {}
    for col, pname in PRIORS.items():
        mask = np.ones(len(FEATURES))
        mask[col] = 0.0
        w_abl = fit(comp, mask=mask)
        r = evaluate(test, lambda X, _w=w_abl: score_posterior(X, _w, mean, std))["recall@1"]
        loo[pname] = full - r
        print(f"  −{pname:12} R@1 {r:.3f}   Δ {full - r:+.3f}")
    top = max(loo, key=loo.get)
    print(f"  → most load-bearing prior: {top} (Δ {loo[top]:+.3f}); "
          f"relevance (cosine) is always kept.")

    pred, emp, cnt, ece = calibration(test, w, mean, std)
    print(f"\n— calibration (ECE = {ece:.3f}; lower = predicted P matches empirical relevance) —")
    print(f"  predicted P : " + " ".join(f"{p:.2f}" for p in pred))
    print(f"  empirical   : " + " ".join(f"{e:.2f}" for e in emp))

    print(f"\n  NOTE: on a no-recurrence, no-metadata corpus (LongMemEval: recurrence=1, no age/"
          f"confidence) the priors are inert and the posterior reduces to relevance-only — the "
          f"\n  priors help only where the metadata is informative (the 3A regime).")

    if SAVE:
        out = {"train_q": len(train), "test_q": len(test), "iters": ITERS,
               "weights": {FEATURES[i]: float(w[i]) for i in range(len(FEATURES))},
               "rankers": res, "posterior_minus_heuristic_r1": float(lift),
               "leave_one_out_r1_drop": loo, "calibration_ece": ece,
               "calibration": {"predicted": pred, "empirical": emp, "count": cnt}}
        p = HERE / "posterior_model.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved → {p}")
        _figure(w, loo, pred, emp, cnt, HERE / "posterior_model.png")
    print(bar)


def _figure(w, loo, pred, emp, cnt, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"  [figure skipped: matplotlib unavailable — {e}]")
        return
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.3))
    axes[0].barh(range(len(FEATURES)), w)
    axes[0].set_yticks(range(len(FEATURES)))
    axes[0].set_yticklabels(FEATURES, fontsize=8)
    axes[0].axvline(0, color="k", lw=0.8)
    axes[0].set_title("Fitted posterior weights (standardized)")
    axes[0].grid(alpha=0.3, axis="x")
    names = list(loo)
    axes[1].bar(range(len(names)), [loo[n] for n in names])
    axes[1].set_xticks(range(len(names)))
    axes[1].set_xticklabels(names, rotation=30, ha="right", fontsize=8)
    axes[1].set_ylabel("recall@1 lost when removed")
    axes[1].set_title("Per-prior contribution (leave-one-out)")
    axes[1].grid(alpha=0.3, axis="y")
    axes[2].plot([0, 1], [0, 1], "--", color="grey", label="ideal")
    sel = [i for i, c in enumerate(cnt) if c > 0]
    axes[2].plot([pred[i] for i in sel], [emp[i] for i in sel], marker="o", label="model")
    axes[2].set_xlabel("predicted P(target)")
    axes[2].set_ylabel("empirical target rate")
    axes[2].set_title("Calibration (reliability)")
    axes[2].legend(fontsize=8)
    axes[2].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print(f"  figure → {path}")


if __name__ == "__main__":
    main()
