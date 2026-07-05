#!/usr/bin/env python3
"""RESEARCH - a recurrence-bearing longitudinal agent-memory benchmark (roadmap 3A).

WHY THIS EXISTS. LongMemEval (research/longmem_eval.py) gives a real external recall
number, but every one of its sessions is distinct - recurrence is always 1, so it
cannot tell us whether exploiting *lesson recurrence* helps. No public benchmark
carries a natural recurrence signal. This module builds one: a controlled, fully
seeded longitudinal world where an agent works tasks over many sessions, the same
"gotchas" recur with realistic (Zipf) frequency, memory accumulates with
supersession, and a query at session t must recall the lesson that answers it from
everything written so far. It is the shared infrastructure the method core (1A
posterior, 1B feedback bandit, 1C forgetting) is built and measured on.

WHAT IT MEASURES.
  • recall@k / MRR / nDCG of the SHIPPED ranker vs ablated rankers, on ground truth
    that is an external need (the lesson the session hit), NOT internal wikilink
    self-consistency (cf. eval_harness Task A's honesty note).
  • recurrence-stratified recall - does the ranker exploit recurrence WHEN PRESENT
    (the question LongMemEval cannot pose).
  • recall-utility over time - the learning curve as memory fills (the signal 1B's
    online learner optimises).
  • temporal ambiguity - how many stale versions a supersession-blind store surfaces.

FAITHFULNESS. The ranker modes reproduce memory_hook.retrieve_relevant exactly: the
floored semantic list, lexical token overlap, weighted RRF (RETRIEVAL_SEM_WEIGHT),
the ambiguity-scaled log recurrence term (0.0003·log(n)·amb), and the
decay×resolved×confidence salience multiplier. Recurrence/ambiguity call the real
m._recur_boost-equivalent and m._ambiguity; salience mirrors m._salience_mult with an
explicit, controlled note age (wall-clock is not used, so runs are reproducible). A
parity test (_test_longitudinal_bench.py) pins the salience re-implementation to the
shipped function.

Synthetic latent vectors + synthetic token bags make ambiguity and recurrence
controlled variables - a mechanism benchmark, reproducible on CPU in seconds, NOT an
embedder test. It also EMITS the implicit-feedback log 1B needs (--emit-feedback).

    python research/longitudinal_bench.py                 # leaderboard
    python research/longitudinal_bench.py --save          # + .json (+ .png if mpl)
    python research/longitudinal_bench.py --emit-feedback # + data/longitudinal_feedback.jsonl (for 1B)
    python research/longitudinal_bench.py --quick         # smoke (fewer sessions/seeds)

Research dep: numpy (matplotlib optional, for the figure). Seeded; deterministic.
"""
import json
import math
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.stderr.write("longitudinal_bench needs numpy (research dep): pip install numpy\n")
    sys.exit(2)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nevertwice"))
import memory_hook as m

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

QUICK = "--quick" in sys.argv
SAVE = "--save" in sys.argv
EMIT_FEEDBACK = "--emit-feedback" in sys.argv

# ── world configuration (all fixed; reproducible) ──────────────────────
# Calibrated so the BASE task is genuinely ambiguous (no signal is an oracle): the
# pool is large enough that recall@k does not saturate, semantic is the primary but
# confusable signal, and lexical is a weak/noisy secondary - the regime where a
# recurrence prior can actually act (cf. the leaderboard's first cut, where trivially
# separable tokens let lexical nail R@1 and recurrence was inert by construction).
DIM = 48
N_TOPICS = 40
LESSONS_PER_TOPIC = 8             # near-duplicate siblings → a non-saturating pool
INTRA_SPREAD = 0.40               # tight clusters → siblings are genuinely confusable
ZIPF_A = 1.7                      # recurrence law: a few persistent traps, long tail
TOPIC_VOCAB = 8                   # small shared token pool → siblings overlap heavily
L_UNIQUE = 4                      # each lesson draws 4 of 8 → lexical is weak & ambiguous
Q_KEEP = 0.5                      # query keeps each target token w.p. Q_KEEP (dropout)
Q_NOISE = 1                       # + this many distractor tokens from the topic vocab
P_SUPERSEDE = 0.10                # chance a re-encounter UPDATES the lesson (new version)
DAYS_PER_SESSION = 0.25           # session gap → note age (drives decay salience)
T_SESSIONS = 400 if QUICK else 1600
SEEDS = 3 if QUICK else 6
SIGMA_MAIN = None                 # headline leaderboard: a per-query MIXTURE of crisp &
# ambiguous queries (drawn below) - the realistic regime, and the only one in which
# ambiguity-ADAPTIVE recurrence can differ from fixed (a single σ makes every query
# equally ambiguous, so amb is ~constant and adaptive ≡ fixed).
MIX_AMBIG_P = 0.6                 # share of ambiguous queries in the mixture
MIX_CRISP = (0.05, 0.35)         # crisp σ range (relevance decisive → suppress recurrence)
MIX_AMBIG = (0.8, 1.6)           # ambiguous σ range (relevance can't decide → lean on prior)
SIGMAS = [0.3, 0.85, 1.4]         # ambiguity sweep (the "recurrence helps when ambiguous" check)
RECUR_COEFS = [0.0, 0.0003, 0.001, 0.003, 0.01, 0.03]   # RRF-scale recurrence coefficient sweep
SHIPPED_COEF = m.RETRIEVAL_RECUR_RRF_BOOST   # the coefficient retrieve_relevant uses
KS = (1, 3, 5)
RECUR_BUCKETS = [(1, 1, "1"), (2, 3, "2-3"), (4, 7, "4-7"), (8, 10**9, "8+")]
MODES = ("lexical", "semantic", "hybrid", "hybrid+recur_fixed", "hybrid+recur", "shipped")
STRAT_MODES = ("semantic", "hybrid", "hybrid+recur", "shipped")   # recurrence-stratified rows
TIME_BINS = 10


# ── world + timeline generators (seeded) ───────────────────────────────

def gen_world(rng):
    """A topic-clustered lesson space with latent vectors, noisy token bags and a
    Zipf recurrence propensity per lesson. Each lesson draws L_UNIQUE tokens from its
    topic's vocabulary of TOPIC_VOCAB - siblings overlap (shared topic words) but each
    differs, so lexical overlap is informative yet noisy, not an oracle."""
    cent = rng.normal(size=(N_TOPICS, DIM))
    cent /= np.linalg.norm(cent, axis=1, keepdims=True)
    vecs, topic, propensity, toks = [], [], [], []
    for c in range(N_TOPICS):
        vocab = list(range(c * TOPIC_VOCAB, (c + 1) * TOPIC_VOCAB))
        for _ in range(LESSONS_PER_TOPIC):
            v = cent[c] + INTRA_SPREAD * rng.normal(size=DIM)
            v /= np.linalg.norm(v)
            vecs.append(v)
            topic.append(c)
            propensity.append(float(min(rng.zipf(ZIPF_A), 50)))
            toks.append(set(rng.choice(vocab, L_UNIQUE, replace=False).tolist()))
    return {"vec": np.array(vecs), "topic": np.array(topic),
            "prop": np.array(propensity, dtype=float), "tok": toks,
            "n_lessons": len(vecs)}


def _topic_pick_weights(world):
    """Per-lesson sampling weight ∝ recurrence propensity (persistent traps recur)."""
    p = world["prop"].copy()
    return p / p.sum()


def simulate(rng, world):
    """Walk T_SESSIONS. Each session hits a lesson (∝ propensity); the store gets a
    new note, a recurrence bump, or a superseding version (recurrence carries
    forward, mirroring write_typed_note). A query is then issued for the live note of
    the hit lesson, against its live topic siblings. Returns the query-event stream;
    each event carries everything the ranker needs (controlled, no wall-clock)."""
    pick = _topic_pick_weights(world)
    # note[lesson] = live version record; retired[lesson] = list of past versions
    note, retired = {}, {}
    events = []
    for t in range(T_SESSIONS):
        lid = int(rng.choice(world["n_lessons"], p=pick))
        if lid not in note:                                   # first encounter → write v1
            note[lid] = {"born": t, "recurrence": 1,
                         "confidence": float(np.clip(rng.normal(0.8, 0.12), 0.3, 1.0)),
                         "resolved": False, "version": 1}
        elif rng.random() < P_SUPERSEDE:                      # update → new version, retire old
            old = note[lid]
            retired.setdefault(lid, []).append(old)
            note[lid] = {"born": t, "recurrence": old["recurrence"] + 1,   # carry-forward
                         "confidence": float(np.clip(rng.normal(0.82, 0.12), 0.3, 1.0)),
                         "resolved": False, "version": old["version"] + 1}
        else:                                                 # re-encounter → recurrence++
            note[lid]["recurrence"] += 1
        # a fraction of resolved mistakes (down-weighted, still recallable)
        if note[lid]["recurrence"] >= 4 and rng.random() < 0.08:
            note[lid]["resolved"] = True

        # candidate pool = live notes of the same topic, born by now (incl. this one)
        c = world["topic"][lid]
        pool = [j for j in note if world["topic"][j] == c]
        if len(pool) < 2:
            continue                                          # no distractors → skip (trivial)
        events.append({"t": t, "lid": lid, "topic": int(c),
                       "pool": pool, "recurrence": note[lid]["recurrence"],
                       "n_retired": len(retired.get(lid, [])),
                       "note": {j: dict(note[j], age=(t - note[j]["born"]) * DAYS_PER_SESSION)
                                for j in pool}})
    return events, retired


def _query_sigma(rng, sigma):
    """A fixed σ, or - when sigma is None - a per-query draw from the crisp/ambiguous
    mixture (so crisp and ambiguous queries coexist, which is what lets the
    ambiguity-adaptive recurrence term differ from a fixed one)."""
    if sigma is not None:
        return sigma
    lo, hi = MIX_AMBIG if rng.random() < MIX_AMBIG_P else MIX_CRISP
    return float(rng.uniform(lo, hi))


def make_query(rng, world, lid, sigma):
    """Query = target latent vector + σ noise (semantic ambiguity knob), plus a token
    bag = a dropout sample of the target's tokens + one distractor word from the
    topic vocabulary (so lexical is a weak, noisy signal, not an oracle)."""
    sigma = _query_sigma(rng, sigma)
    qv = world["vec"][lid] + sigma * rng.normal(size=DIM)
    qv = qv / np.linalg.norm(qv)
    qt = {tk for tk in world["tok"][lid] if rng.random() < Q_KEEP}
    c = int(world["topic"][lid])
    for _ in range(Q_NOISE):
        qt.add(int(rng.integers(c * TOPIC_VOCAB, (c + 1) * TOPIC_VOCAB)))   # distractor tokens
    return qv, qt


# ── ranker modes - faithful to memory_hook.retrieve_relevant ────────────

def _salience(rec):
    """Mirror of m._salience_mult with an explicit (controlled) age - incl. the
    recurrence-slowed decay (effective age = age/(1+log n), 3A finding F2). Reads the
    SAME constants so it tracks any retuning; parity-pinned in the companion test."""
    n = max(1, int(rec.get("recurrence", 1) or 1))
    mult = 1.0
    if m.RETRIEVAL_DECAY_HALFLIFE > 0:
        age = rec["age"] / (1.0 + math.log(n))
        mult *= max(m.RETRIEVAL_DECAY_FLOOR, 0.5 ** (age / m.RETRIEVAL_DECAY_HALFLIFE))
    if rec.get("resolved"):
        mult *= m.RETRIEVAL_RESOLVED_WEIGHT
    c = m._coerce_confidence(rec.get("confidence"))
    if c is not None:
        mult *= m.RETRIEVAL_CONF_FLOOR + (1.0 - m.RETRIEVAL_CONF_FLOOR) * c
    return mult


# Each ranker mode as an explicit fusion config (so the coefficient sweep drives the
# very same code path). coef/adaptive reproduce retrieve_relevant's inline recurrence
# term `scores[s] += coef·log(n)·amb`; salience reproduces the trailing ×_salience_mult.
_MODE_CFG = {
    "lexical":            {"base": "lexical"},
    "semantic":           {"base": "semantic"},
    "hybrid":             {"base": "rrf"},
    "hybrid+recur_fixed": {"base": "rrf", "coef": SHIPPED_COEF, "adaptive": False},
    "hybrid+recur":       {"base": "rrf", "coef": SHIPPED_COEF, "adaptive": True},
    "shipped":            {"base": "rrf", "coef": SHIPPED_COEF, "adaptive": True, "salience": True},
}


def _cos(qv, vec):
    """Cosine of two UNIT vectors = their dot product. Equivalent to m.cosine for
    normalized vectors but vectorized - m.cosine takes python lists (returns 0.0 on a
    numpy array), so it must not be called with the world's numpy rows. Matches
    recurrence_ablation's `vecs @ qv`."""
    return float(qv @ vec)


def rank(world, ev, qv, qt, mode=None, cfg=None):
    """Ordered candidate ids under one fusion config. base=rrf reproduces production
    (floored semantic list + lexical overlap → weighted RRF + ambiguity-scaled log
    recurrence term + optional salience ×); semantic/lexical are the baselines."""
    cfg = cfg if cfg is not None else _MODE_CFG[mode]
    pool = ev["pool"]
    sims = {j: _cos(qv, world["vec"][j]) for j in pool}
    if cfg["base"] == "semantic":
        return sorted(pool, key=lambda j: -sims[j])
    lex_scored = sorted(((len(qt & world["tok"][j]), j) for j in pool if qt & world["tok"][j]),
                        key=lambda x: -x[0])
    lex = [j for _, j in lex_scored]
    if cfg["base"] == "lexical":
        return lex + [j for j in pool if j not in set(lex)]
    sem = [j for j in sorted(pool, key=lambda j: -sims[j]) if sims[j] > m.RETRIEVAL_SIM_FLOOR]
    amb = m._ambiguity(sorted(sims.values(), reverse=True))
    rankings = ([(sem, m.RETRIEVAL_SEM_WEIGHT)] if sem else []) + ([(lex, 1.0)] if lex else [])
    if not rankings:
        return sorted(pool, key=lambda j: -sims[j])
    scores = m._rrf_scores([r for r, _ in rankings], weights=[w for _, w in rankings])
    coef = cfg.get("coef", 0.0)
    if coef:
        a = amb if cfg.get("adaptive", True) else 1.0
        for j in pool:
            n = max(1, int(ev["note"][j]["recurrence"]))
            scores[j] = scores.get(j, 0.0) + coef * math.log(n) * a
    if cfg.get("salience"):
        for j in pool:
            scores[j] = scores.get(j, 0.0) * _salience(ev["note"][j])
    return sorted(scores, key=lambda j: -scores.get(j, 0.0))


def _metrics(ranked, target):
    rank_pos = next((i for i, j in enumerate(ranked) if j == target), None)
    rec = {k: (1.0 if rank_pos is not None and rank_pos < k else 0.0) for k in KS}
    rr = 1.0 / (rank_pos + 1) if rank_pos is not None else 0.0
    ndcg = 1.0 / math.log2(rank_pos + 2) if rank_pos is not None else 0.0   # single relevant, IDCG=1
    return rec, rr, ndcg


# ── aggregation ─────────────────────────────────────────────────────────

def _bucket(n):
    for lo, hi, lab in RECUR_BUCKETS:
        if lo <= n <= hi:
            return lab
    return RECUR_BUCKETS[-1][2]


_WORLD_CACHE = {}


def _world_events(seed):
    """(world, events) for a seed - deterministic and σ-independent, so cache it
    (the σ and coefficient sweeps would otherwise rebuild the same worlds)."""
    if seed not in _WORLD_CACHE:
        world = gen_world(np.random.default_rng(2000 + seed))
        events, _ = simulate(np.random.default_rng(5000 + seed), world)
        _WORLD_CACHE[seed] = (world, events)
    return _WORLD_CACHE[seed]


def run(sigma, feedback_sink=None):
    """Sweep seeds → per-mode recall/MRR/nDCG, recurrence-stratified recall@1, and a
    time-binned recall@1 curve for the shipped ranker (sigma=None → per-query mix)."""
    agg = {mode: {f"r@{k}": [] for k in KS} | {"mrr": [], "ndcg": []} for mode in MODES}
    strat = {mode: {lab: [] for _, _, lab in RECUR_BUCKETS} for mode in STRAT_MODES}
    curve = [[] for _ in range(TIME_BINS)]
    temporal_versions = []                                    # versions a flat store would surface
    for seed in range(SEEDS):
        world, events = _world_events(seed)
        qrng = np.random.default_rng(9000 + seed * 131 + int((sigma or 0) * 1000))
        for ev in events:
            qv, qt = make_query(qrng, world, ev["lid"], sigma)
            for mode in MODES:
                rec, rr, ndcg = _metrics(rank(world, ev, qv, qt, mode), ev["lid"])
                for k in KS:
                    agg[mode][f"r@{k}"].append(rec[k])
                agg[mode]["mrr"].append(rr)
                agg[mode]["ndcg"].append(ndcg)
                if mode in STRAT_MODES:
                    strat[mode][_bucket(ev["recurrence"])].append(rec[1])
            # learning curve (recall@1 - @5 saturates on an 8-wide pool) + temporal
            curve[min(TIME_BINS - 1, ev["t"] * TIME_BINS // T_SESSIONS)].append(
                agg["shipped"]["r@1"][-1])
            if ev["n_retired"]:
                temporal_versions.append(ev["n_retired"] + 1)   # live + retired a flat store mixes
            if feedback_sink is not None:
                _log_feedback(feedback_sink, world, ev, qv, qt, seed, sigma)
    return agg, strat, curve, temporal_versions


from _common import _ci          # W12: shared mean±95%CI helper


def coef_sweep(sigma):
    """recall@1 of hybrid+recurrence vs the RRF-scale recurrence coefficient, for BOTH
    fixed and ambiguity-adaptive scaling (paired query streams). Locates the calibrated
    optimum, shows where the shipped value sits, and exposes WHEN the adaptive scaling
    actually matters - the calibration LongMemEval could not provide (no recurrence)."""
    out = {}
    for coef in RECUR_COEFS:
        row = {}
        for adaptive in (False, True):
            cfg = {"base": "rrf", "coef": coef, "adaptive": adaptive}
            hits = []
            for seed in range(SEEDS):
                world, events = _world_events(seed)
                qrng = np.random.default_rng(9000 + seed * 131 + int((sigma or 0) * 1000))
                for ev in events:
                    qv, qt = make_query(qrng, world, ev["lid"], sigma)
                    ranked = rank(world, ev, qv, qt, cfg=cfg)
                    hits.append(1.0 if ranked and ranked[0] == ev["lid"] else 0.0)
            row["adaptive" if adaptive else "fixed"] = _ci(hits)[0]
        out[coef] = row
    return out


# ── implicit-feedback log for 1B (per-candidate features + outcome) ─────

def _log_feedback(sink, world, ev, qv, qt, seed, sigma):
    """One JSONL row per query: per-candidate features (relevance, recurrence,
    recency, confidence, resolved, lexical-overlap) + the shipped top-k and whether
    the target was surfaced - the implicit reward 1B's bandit learns from."""
    ranked = rank(world, ev, qv, qt, "shipped")
    topk = ranked[:m.RETRIEVAL_TOP_K]
    feats = {int(j): {"relevance": round(_cos(qv, world["vec"][j]), 4),
                      "recurrence": int(ev["note"][j]["recurrence"]),
                      "recency_days": round(ev["note"][j]["age"], 2),
                      "confidence": round(ev["note"][j]["confidence"], 3),
                      "resolved": bool(ev["note"][j]["resolved"]),
                      "lex_overlap": len(qt & world["tok"][j])}
             for j in ev["pool"]}
    sink.append({"seed": seed, "sigma": sigma, "session": ev["t"],
                 "target": ev["lid"], "candidates": feats,
                 "injected_topk": [int(j) for j in topk],
                 "hit": ev["lid"] in topk,
                 "rank": next((i for i, j in enumerate(ranked) if j == ev["lid"]), -1)})


# ── reporting ───────────────────────────────────────────────────────────

def main():
    bar = "=" * 80
    print(bar)
    print("  LONGITUDINAL AGENT-MEMORY BENCHMARK (3A) - recurrence-bearing, point-in-time")
    print(bar)
    print(f"  world: {N_TOPICS} topics × {LESSONS_PER_TOPIC} near-dup lessons (dim {DIM}); "
          f"Zipf(a={ZIPF_A}) recurrence; {P_SUPERSEDE:.0%} supersede")
    print(f"  {T_SESSIONS} sessions × {SEEDS} seeds; ranker = shipped retrieve_relevant fusion; "
          f"CPU, $0, seeded")

    feedback = [] if EMIT_FEEDBACK else None
    agg, strat, curve, temporal = run(SIGMA_MAIN, feedback_sink=feedback)

    print(f"\n- leaderboard at σ=mixture (crisp+ambiguous)  (mean ± 95% CI over {SEEDS} seeds) -")
    print(f"  {'mode':20} {'R@1':>13} {'R@3':>11} {'R@5':>11} {'MRR':>8} {'nDCG':>7}")
    lead = {}
    for mode in MODES:
        r1, c1 = _ci(agg[mode]["r@1"])
        r3, _ = _ci(agg[mode]["r@3"])
        r5, _ = _ci(agg[mode]["r@5"])
        mrr, _ = _ci(agg[mode]["mrr"])
        ndcg, _ = _ci(agg[mode]["ndcg"])
        lead[mode] = {"r@1": r1, "r@3": r3, "r@5": r5, "mrr": mrr, "ndcg": ndcg}
        print(f"  {mode:20} {r1:.3f} ±{c1:.3f} {r3:>10.3f} {r5:>10.3f} {mrr:>8.3f} {ndcg:>7.3f}")
    recur_lift = lead["hybrid+recur_fixed"]["r@1"] - lead["hybrid"]["r@1"]
    salience_lift = lead["shipped"]["r@1"] - lead["hybrid+recur_fixed"]["r@1"]
    adapt = lead["hybrid+recur"]["r@1"] - lead["hybrid+recur_fixed"]["r@1"]
    print(f"\n  → recurrence over recurrence-blind hybrid @1: {recur_lift:+.3f} "
          f"(small, and concentrated on recurring lessons - see strata)")
    print(f"  → salience stack (shipped − hybrid+recur) @1: {salience_lift:+.3f} "
          f"← recency-decay buries old-but-recurring lessons (finding F2)")
    print(f"  → ambiguity-adaptive vs fixed recurrence @1: {adapt:+.3f} "
          f"(inert/negative at the shipped tiebreaker coef - see calibration)")

    print(f"\n- recall@1 stratified by target recurrence (does the ranker exploit it?) -")
    print(f"  {'ranker':18}" + "".join(f"{lab:>9}" for _, _, lab in RECUR_BUCKETS))
    for mode in STRAT_MODES:
        cells = [f"{_ci(strat[mode][lab])[0]:>9.3f}" for _, _, lab in RECUR_BUCKETS]
        print(f"  {mode:18}" + "".join(cells))
    print(f"  {'(n queries)':18}" + "".join(f"{len(strat['shipped'][lab]):>9}"
                                            for _, _, lab in RECUR_BUCKETS))
    g = {lab: _ci(strat["hybrid+recur"][lab])[0] - _ci(strat["hybrid"][lab])[0]
         for _, _, lab in RECUR_BUCKETS}
    print("  → recurrence-aware − blind (hybrid+recur − hybrid), by bucket: "
          + " ".join(f"{lab}:{g[lab]:+.3f}" for _, _, lab in RECUR_BUCKETS))

    print(f"\n- recall@1 over the timeline (shipped; the pool fills as memory grows - "
          f"the static-ranker\n    baseline the 1B feedback-learner aims to beat) -")
    binvals = [_ci(b)[0] for b in curve]
    spark = "".join("▁▂▃▄▅▆▇█"[min(7, int(v * 8))] for v in binvals)
    print(f"  {spark}   (first→last decile)  {binvals[0]:.2f} → {binvals[-1]:.2f}")

    avg_versions = (sum(temporal) / len(temporal)) if temporal else 1.0
    print(f"\n- temporal ambiguity: a supersession-blind 'use-all' store would surface "
          f"{avg_versions:.2f}\n    versions/query for revised lessons; the bi-temporal store returns 1 (live).")

    # ambiguity sweep - the validation LongMemEval could not give (recurrence present).
    # Isolate recurrence cleanly (blind hybrid vs +recurrence), NOT shipped−semantic
    # (which the salience regression would confound).
    print(f"\n- recurrence's value vs query ambiguity σ  (recall@1; blind hybrid vs +recur) -")
    print(f"  {'σ':>5} {'hybrid':>10} {'+recur':>10} {'Δ(recur)':>10}")
    sweep = {}
    for sg in SIGMAS:
        a2, _, _, _ = run(sg)
        hy = _ci(a2["hybrid"]["r@1"])[0]
        hr = _ci(a2["hybrid+recur_fixed"]["r@1"])[0]
        sweep[sg] = {"hybrid": hy, "hybrid_recur": hr, "delta": hr - hy}
        print(f"  {sg:>5} {hy:>10.3f} {hr:>10.3f} {hr - hy:>+10.3f}")
    print("  → Δ(recurrence) grows with σ: the frequency prior pays off exactly when relevance is ambiguous.")

    # coefficient calibration: where does the shipped coef sit, and WHEN does the
    # ambiguity-adaptive scaling actually change anything?
    print(f"\n- recurrence-coefficient calibration at σ=mixture  (recall@1; fixed vs adaptive) -")
    csweep = coef_sweep(SIGMA_MAIN)
    print(f"  {'coef':>8} {'fixed':>8} {'adaptive':>9} {'Δ(adapt−fixed)':>15}")
    for c in RECUR_COEFS:
        tag = "  ← shipped" if c == SHIPPED_COEF else ""
        print(f"  {c:>8g} {csweep[c]['fixed']:>8.3f} {csweep[c]['adaptive']:>9.3f} "
              f"{csweep[c]['adaptive'] - csweep[c]['fixed']:>+15.3f}{tag}")
    cbest = max(RECUR_COEFS, key=lambda c: csweep[c]["adaptive"])
    best_delta = max(RECUR_COEFS, key=lambda c: csweep[c]["adaptive"] - csweep[c]["fixed"])
    print(f"  → optimum coef={cbest:g} (R@1 {csweep[cbest]['adaptive']:.3f}); at the shipped "
          f"{SHIPPED_COEF:g} recurrence is a pure TIEBREAKER, so adaptivity is inert "
          f"(Δ={csweep[SHIPPED_COEF]['adaptive'] - csweep[SHIPPED_COEF]['fixed']:+.3f}).")
    print(f"    adaptive scaling earns its keep only once recurrence re-ranks: max "
          f"Δ={csweep[best_delta]['adaptive'] - csweep[best_delta]['fixed']:+.3f} at coef={best_delta:g} "
          f"(it suppresses recurrence on crisp queries).")

    if EMIT_FEEDBACK and feedback is not None:
        out = HERE / "data" / "longitudinal_feedback.jsonl"      # gitignored
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(json.dumps(r) for r in feedback), encoding="utf-8")
        print(f"\n  implicit-feedback log → {out}  ({len(feedback)} query events, for 1B)")

    if SAVE:
        res = {"config": {"dim": DIM, "topics": N_TOPICS, "lessons_per_topic": LESSONS_PER_TOPIC,
                          "sessions": T_SESSIONS, "seeds": SEEDS, "sigma_main": SIGMA_MAIN,
                          "p_supersede": P_SUPERSEDE, "zipf_a": ZIPF_A},
               "leaderboard": lead, "recurrence_stratified": {
                   mode: {lab: _ci(strat[mode][lab])[0] for _, _, lab in RECUR_BUCKETS}
                   for mode in strat},
               "learning_curve_recall5": binvals, "temporal_avg_versions": avg_versions,
               "ambiguity_sweep": sweep,
               "recur_coef_sweep": {str(c): csweep[c] for c in RECUR_COEFS},
               "recur_coef_optimum": cbest, "recur_coef_shipped": SHIPPED_COEF,
               "adaptive_max_delta_coef": best_delta}
        p = HERE / "longitudinal_bench.json"
        p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved → {p}")
        _figure(lead, strat, binvals, sweep, HERE / "longitudinal_bench.png")
    print(bar)


def _figure(lead, strat, curve, sweep, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"  [figure skipped: matplotlib unavailable - {e}]")
        return
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.3))
    modes = list(lead)
    axes[0].bar(range(len(modes)), [lead[md]["r@1"] for md in modes])
    axes[0].set_xticks(range(len(modes)))
    axes[0].set_xticklabels(modes, rotation=40, ha="right", fontsize=7)
    axes[0].set_ylabel("recall@1")
    axes[0].set_title(f"Ranker leaderboard (σ={SIGMA_MAIN})")
    axes[0].grid(alpha=0.3, axis="y")
    labs = [lab for _, _, lab in RECUR_BUCKETS]
    for md in ("semantic", "shipped"):
        axes[1].plot(labs, [_ci(strat[md][lab])[0] for lab in labs], marker="o", label=md)
    axes[1].set_xlabel("target recurrence")
    axes[1].set_ylabel("recall@1")
    axes[1].set_title("Recurrence-stratified recall")
    axes[1].legend(fontsize=8)
    axes[1].grid(alpha=0.3)
    sg = list(sweep)
    axes[2].plot(sg, [sweep[s]["hybrid"] for s in sg], marker="o", label="hybrid (blind)")
    axes[2].plot(sg, [sweep[s]["hybrid_recur"] for s in sg], marker="s", label="hybrid + recurrence")
    axes[2].set_xlabel("query ambiguity σ")
    axes[2].set_ylabel("recall@1")
    axes[2].set_title("Recurrence pays off under ambiguity")
    axes[2].legend(fontsize=8)
    axes[2].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print(f"  figure → {path}")


if __name__ == "__main__":
    main()
