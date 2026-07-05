#!/usr/bin/env python3
"""RESEARCH - W2 precision ceiling: can we beat the bi-encoder on a real store? (roadmap Phase 1).

W2 (WEAKNESSES.md): bge-m3 cosines for short multilingual notes bunch near a high background, so a
genuinely-relevant note clears the median by only ~0.16 and retrieval precision saturates -
real-trace recall@3 ~0.71 with relevance alone (3A.2), no recurrence prior beats it. The audit names
three candidate fixes (W4/W2): a stronger embedder, a cross-encoder reranker, or query expansion.
Two are tested here on the live store, against the SAME cross-session-cluster ground truth the 3A.2
number uses (apples-to-apples):

  Experiment 1 (default, cache-only, zero-dep): embedding-space pseudo-relevance feedback (Rocchio).
    q' = (1-beta)*unit(q) + beta*unit(mean(topK0 neighbours)). Same averaging-as-denoising as 4A
    abstractive consolidation, applied at QUERY time - re-weights the existing geometry, needs no
    model and no text. Tests whether the bi-encoder ceiling is liftable without a new relevance model.

  Experiment 2 (--rerank, needs Ollama): a local LLM-as-reranker (cross-encoder substitute). Takes
    the cosine top-N and jointly scores (query, candidate) text for same-topic relevance, re-orders.
    This is a DIFFERENT relevance signal than cosine, so unlike Rocchio it can promote a true twin
    cosine ranks 4..N into the top-3. The honest test of "does a stronger relevance model beat the
    bi-encoder ceiling?". Ollama is already a hard dependency (extraction/embeddings), so a local
    reranker is not a new dep. An opt-in DeepSeek cloud backend (--rerank-backend deepseek) exists
    for parity; it is runtime-blocked without DEEPSEEK_API_KEY (offline-mock-tested).

PRIVACY. Aggregate only: recall@k / MRR / cost fractions and hyper-parameters. Reads the LOCAL,
gitignored embedding cache (vectors + the title/desc/prevention text the embedder already stored)
purely in-process; never prints, saves, or commits any note text. Identical posture to real_trace.

    NEVERTWICE_VAULT=/path python research/precision_bench.py [--save]
    NEVERTWICE_VAULT=/path python research/precision_bench.py --rerank [--save]
    NEVERTWICE_VAULT=/path NEVERTWICE_RERANK_MODEL=qwen2.5:7b python research/precision_bench.py --rerank

Research dep: Experiment 1 stdlib-only (cached vectors, no Ollama); Experiment 2 calls Ollama.
"""
import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "nevertwice"))
import memory_hook as m
import _rerank as rr

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SAVE = "--save" in sys.argv
RERANK = "--rerank" in sys.argv
BACKEND = "deepseek" if "--rerank-backend" in sys.argv and "deepseek" in sys.argv else "local"
CLUSTER_THR = 0.55           # same operating threshold as real_trace_bench (3A.2)
K = 3                        # recall@K, matched to the 3A.2 headline
BETAS = [0.1, 0.2, 0.3, 0.4, 0.5]
K0S = [3, 5, 10]             # pseudo-relevant pool size for the Rocchio centroid
EPS = 0.005                  # a delta below this is noise, not a win
RERANK_N = int(os.environ.get("NEVERTWICE_RERANK_N", "12"))     # cosine pool the LLM re-orders
RERANK_MODEL = os.environ.get("NEVERTWICE_RERANK_MODEL", m.OLLAMA_MODEL)
RERANK_CHARS = 320           # per-note text budget into the prompt (bounds tokens)

VEC: dict = {}               # stem -> embedding (filled in main)
TEXT: dict = {}              # stem -> "title\ndesc\nprevention" (filled in main, never persisted)


def _date(stem):
    p = m.parse_typed_stem(stem)
    return p["date"] if p else "?"


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def _mean(vecs):
    if not vecs:
        return []
    d = len(vecs[0])
    out = [0.0] * d
    for v in vecs:
        for i in range(d):
            out[i] += v[i]
    return [x / len(vecs) for x in out]


def _clusters(notes, thr):
    """Greedy cosine clusters within an already-same-project note list (== real_trace_bench)."""
    used, groups = set(), []
    for i, (s1, r1) in enumerate(notes):
        if s1 in used:
            continue
        grp = [s1]
        for s2, r2 in notes[i + 1:]:
            if s2 not in used and m.cosine(r1["vec"], r2["vec"]) >= thr:
                grp.append(s2)
                used.add(s2)
        if len(grp) > 1:
            used.add(s1)
            groups.append(grp)
    return groups


def _build_queries(by_proj):
    """Each cross-session cluster member is a query; ground truth = same-cluster members on a
    DIFFERENT date (the same topic re-encountered in another session). Candidate pool = all other
    in-project notes. Returns list of (qi_stem, cand_stems, gt_set)."""
    queries = []
    for ns in by_proj.values():
        stems = [s for s, _ in ns]
        for g in _clusters(ns, CLUSTER_THR):
            if len({_date(x) for x in g}) < 2:
                continue
            for qi in g:
                gt = {x for x in g if x != qi and _date(x) != _date(qi)}
                if gt:
                    queries.append((qi, [s for s in stems if s != qi], gt))
    return queries


def _eval(rank_fn, queries):
    """rank_fn(qi, cand) -> ranked stem list. Returns (recall@K, MRR)."""
    hit = mrr = 0.0
    for qi, cand, gt in queries:
        ranked = rank_fn(qi, cand)
        if set(ranked[:K]) & gt:
            hit += 1
        for i, s in enumerate(ranked, 1):
            if s in gt:
                mrr += 1.0 / i
                break
    n = len(queries)
    return (hit / n, mrr / n) if n else (0.0, 0.0)


# ── Experiment 1: Rocchio PRF (cache-only) ────────────────────────────────────

def _baseline(qi, cand):
    q = VEC[qi]
    return sorted(cand, key=lambda s: -m.cosine(q, VEC[s]))


def _rocchio(beta, k0):
    def rank(qi, cand):
        q = VEC[qi]
        cos = {s: m.cosine(q, VEC[s]) for s in cand}
        nbrs = sorted(cand, key=lambda s: -cos[s])[:k0]
        qp = [(1 - beta) * a + beta * b
              for a, b in zip(_unit(q), _unit(_mean([VEC[s] for s in nbrs])))]
        return sorted(cand, key=lambda s: -m.cosine(qp, VEC[s]))
    return rank


def run_rocchio(queries, base_r, base_m):
    print(f"  Rocchio PRF sweep - recall@{K} (Δ vs baseline) / MRR:")
    _hdr = "beta\\K0"          # backslash lifted out of the f-string (PEP 701 is 3.12+ only)
    print(f"  {_hdr:>8}" + "".join(f"{k0:>14}" for k0 in K0S))
    best = (base_r, base_m, 0.0, 0)
    grid = {}
    for beta in BETAS:
        row = f"  {beta:>8.1f}"
        for k0 in K0S:
            r, mr = _eval(_rocchio(beta, k0), queries)
            grid[f"{beta}|{k0}"] = {"recall": r, "mrr": mr}
            row += f"  {r:.3f}({r - base_r:+.3f})"
            if r > best[0] + EPS or (abs(r - best[0]) <= EPS and mr > best[1]):
                best = (r, mr, beta, k0)
        print(row)
    br, bm, bbeta, bk0 = best
    print()
    if br > base_r + EPS:
        print(f"  → WIN: Rocchio beta={bbeta}, K0={bk0}: recall@{K} {base_r:.3f}→{br:.3f} "
              f"(+{br-base_r:.3f}). Embedding-space PRF denoises the query on this geometry.")
    else:
        print(f"  → HONEST NEGATIVE: no (beta,K0) beats baseline by >{EPS:.3f} (best {br:.3f}).")
        print(f"    The top-K0 neighbourhood is too distractor-heavy for the centroid to denoise -")
        print(f"    query-time PRF cannot lift the bi-encoder ceiling. The gap is in the encoder")
        print(f"    (W2/W4): it needs a different relevance model (Experiment 2), not re-weighting.")
    return grid, {"recall": br, "mrr": bm, "beta": bbeta, "k0": bk0,
                  "beats_baseline": bool(br > base_r + EPS)}


# ── Experiment 2: LLM-as-reranker (local Ollama, opt-in DeepSeek) ──────────────
# The reranker primitive is shared (research/_rerank.py) - same code the LongMemEval
# external test and a future core opt-in mode use.

def run_rerank(queries, base_r, base_m):
    backend_fn = ((lambda q, c, st: rr.deepseek_rerank(q, c, st, RERANK_CHARS)) if BACKEND == "deepseek"
                  else (lambda q, c, st: rr.ollama_rerank(q, c, RERANK_MODEL, st, RERANK_CHARS)))
    if BACKEND == "deepseek" and not os.environ.get("DEEPSEEK_API_KEY"):
        print("  → DeepSeek backend selected but DEEPSEEK_API_KEY is UNSET → runtime-blocked-on-key.")
        print("    The code path is implemented and offline-mock-tested; rerun with the key to measure.")
        return None
    if BACKEND == "local" and not m.ollama_alive():
        print("  → Ollama not reachable → cannot run the local reranker. Start Ollama and retry.")
        return None

    print(f"  LLM-as-reranker - backend={BACKEND} model={RERANK_MODEL if BACKEND=='local' else 'deepseek'} "
          f"N={RERANK_N}")
    stats = {"calls": 0, "errors": 0, "prompt_chars": 0}
    pool_hit = rer_hit = rer_mrr = 0.0
    t0 = time.time()
    for qi, cand, gt in queries:
        topn = sorted(cand, key=lambda s: -m.cosine(VEC[qi], VEC[s]))[:RERANK_N]
        if set(topn) & gt:                             # the rerank pool ceiling
            pool_hit += 1
        scores = backend_fn(TEXT.get(qi, ""), [TEXT.get(s, "") for s in topn], stats)
        cos = {s: m.cosine(VEC[qi], VEC[s]) for s in topn}
        if scores:                                     # re-order by LLM score, tie-break cosine
            order = sorted(range(len(topn)), key=lambda i: (-scores[i], -cos[topn[i]]))
            ranked = [topn[i] for i in order]
        else:
            ranked = sorted(topn, key=lambda s: -cos[s])   # fall back to cosine order
        if set(ranked[:K]) & gt:
            rer_hit += 1
        for i, s in enumerate(ranked, 1):
            if s in gt:
                rer_mrr += 1.0 / i
                break
    n = len(queries)
    dt = time.time() - t0
    pool_r, rer_r, rer_m = pool_hit / n, rer_hit / n, rer_mrr / n
    est_tok = stats["prompt_chars"] // 4
    print(f"\n  baseline       recall@{K}={base_r:.3f}  MRR={base_m:.3f}")
    print(f"  pool ceiling   recall@{K}={pool_r:.3f}  (a twin is in the cosine top-{RERANK_N} this often)")
    print(f"  RERANKED       recall@{K}={rer_r:.3f}  MRR={rer_m:.3f}  (Δ recall {rer_r-base_r:+.3f})")
    print(f"  cost: {stats['calls']} calls, {stats['errors']} errors, ~{est_tok} prompt tok, "
          f"{dt:.1f}s wall ({dt/max(1,n)*1000:.0f} ms/query)")
    if rer_r > base_r + EPS:
        print(f"\n  → WIN: the LLM reranker lifts recall@{K} {base_r:.3f}→{rer_r:.3f} (+{rer_r-base_r:.3f}) "
              f"toward the {pool_r:.3f} pool ceiling.")
        print(f"    A different relevance signal DOES beat the bi-encoder - ship as opt-in recall mode.")
    else:
        print(f"\n  → NO WIN: the reranker does not beat baseline by >{EPS:.3f} on this GT.")
        print(f"    Either the cosine top-3 is already near the {pool_r:.3f} pool ceiling (little")
        print(f"    headroom), or the model cannot tell same-topic twins apart on terse notes.")
    return {"backend": BACKEND, "model": RERANK_MODEL if BACKEND == "local" else "deepseek",
            "n": RERANK_N, "pool_ceiling": pool_r, "reranked_recall": rer_r, "reranked_mrr": rer_m,
            "delta": rer_r - base_r, "calls": stats["calls"], "errors": stats["errors"],
            "est_prompt_tokens": est_tok, "wall_s": round(dt, 1),
            "beats_baseline": bool(rer_r > base_r + EPS)}


def main():
    cache = m.load_embed_cache()
    notes = [(s, r) for s, r in cache.items()
             if isinstance(r, dict) and isinstance(r.get("vec"), list)]
    bar = "=" * 78
    print(bar)
    print("  W2 PRECISION - can a re-weighting (Rocchio) or a reranker beat the bi-encoder")
    print("  on real cross-session ground truth? (aggregate-only)")
    print(bar)
    if len(notes) < 20:
        print(f"  only {len(notes)} embedded notes - set NEVERTWICE_VAULT to a real, populated store.")
        return
    by_proj = defaultdict(list)
    for s, r in notes:
        VEC[s] = r["vec"]
        TEXT[s] = f"{r.get('title','')}\n{r.get('desc','')}\n{r.get('prevention','')}".strip()
        by_proj[r.get("project")].append((s, r))
    queries = _build_queries(by_proj)
    print(f"  {len(notes)} notes / {len(by_proj)} projects / {len(queries)} cross-session queries")
    if not queries:
        print("  no cross-session clusters - store too young or too sparse.")
        return
    has_text = sum(1 for s in TEXT if TEXT[s]) / max(1, len(TEXT))
    base_r, base_m = _eval(_baseline, queries)
    print(f"  baseline (bi-encoder cosine):  recall@{K}={base_r:.3f}  MRR={base_m:.3f}\n")

    out = {"notes": len(notes), "projects": len(by_proj), "queries": len(queries), "k": K,
           "cluster_thr": CLUSTER_THR, "baseline": {"recall": base_r, "mrr": base_m}}

    if RERANK:
        if has_text < 0.5:
            print(f"  → cache stores text for only {has_text:.0%} of notes - rebuild embeddings "
                  f"(embed_index.py) to run the reranker on full text.")
            return
        out["rerank"] = run_rerank(queries, base_r, base_m)
    else:
        grid, best = run_rocchio(queries, base_r, base_m)
        out["rocchio_grid"], out["rocchio_best"] = grid, best
        print(f"\n  CAVEAT: ground truth is built from the SAME cosine signal (clusters >=0.55), so")
        print(f"  this is an upper bound on re-weighting the existing geometry, not a test of a new")
        print(f"  relevance model - run --rerank for that.")

    if SAVE and out.get("rerank") is not None or (SAVE and not RERANK):
        p = HERE / "precision_bench.json"
        prev = {}
        if p.exists():
            try:
                prev = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                prev = {}
        prev.update(out)
        p.write_text(json.dumps(prev, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved aggregate metrics -> {p}")
    print(bar)


if __name__ == "__main__":
    main()
