#!/usr/bin/env python3
"""RESEARCH - LongMemEval external retrieval benchmark for Nevertwice.

Fills the "external benchmark - NOT RUN" gap (eval_harness Task D) with a REAL,
independent recall@k number, and confirms the recurrence/salience fusion is
*inert* on a no-recurrence corpus (the Pareto-safety check the ablation flagged for
real embeddings - every LongMemEval session is distinct, recurrence=1, so a
relevance×recurrence blend must leave relevance retrieval unchanged).

Setup: the GLOBAL-pool variant of LongMemEval-oracle. All 940 unique haystack
sessions become one shared memory store (a real agent's memory); each of the 500
questions must retrieve its evidence session(s) (`answer_session_ids`) from the
whole pool - sessions from other questions are distractors. Sessions and questions
are embedded once with the production embedder (`bge-m3` via `m.embed_text`, with the
production doc/query prefixes) and cached to disk, so re-ranking (e.g. before/after a
ranker change) is instant.

    python research/longmem_eval.py --embed     # embed pool+questions → data/longmem_embeds.json (slow, once)
    python research/longmem_eval.py             # rank + report recall@k (fast; needs the cache)
    python research/longmem_eval.py --limit=150 # first N questions only

Data: data/longmemeval_oracle.json (download separately - see README/this dir).
Research dep: none beyond the package (uses m.cosine, m.embed_text, m._tokens).
"""
import collections
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "nevertwice"))
import memory_hook as m
import _rerank as rr


# ── Shared session-level BM25 + calibrated score fusion ───────────────────────
# The production ranker (memory_hook._calibrated_fusion) fuses NOTE signals; the
# benchmark stand uses raw SESSIONS, so these mirror the same algorithm at session
# level. head_to_head.py and token_ab.py import these so every harness ranks identically
# to production (calibrated score fusion, not the old rank-fusion). See RETRIEVAL_FUSION.md.

def build_bm25(pool_ids, toks_lists):
    """tf / dl / df / avgdl over session token LISTS (counts) for BM25."""
    tf = {s: collections.Counter(toks_lists[s]) for s in pool_ids}
    dl = {s: len(toks_lists[s]) for s in pool_ids}
    avgdl = (sum(dl.values()) / len(dl)) if dl else 1.0
    df = collections.Counter()
    for s in pool_ids:
        for w in set(toks_lists[s]):
            df[w] += 1
    return tf, dl, df, avgdl


def bm25_scores(qt, pool_ids, tf, dl, df, avgdl, k1=1.5, b=0.75):
    """BM25 of query terms `qt` (a set) against each session. {sid: score}."""
    nd = len(pool_ids) or 1
    out = {}
    for s in pool_ids:
        t = tf[s]
        sc = 0.0
        for w in qt:
            f = t.get(w, 0)
            if f:
                idf = math.log(1 + (nd - df.get(w, 0) + 0.5) / (df.get(w, 0) + 0.5))
                sc += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl[s] / avgdl))
        if sc > 0:
            out[s] = sc
    return out


def _zmap(d):
    if not d:
        return {}
    vals = list(d.values())
    mu = statistics.fmean(vals)
    sd = statistics.pstdev(vals) or 1.0
    return {k: (v - mu) / sd for k, v in d.items()}


def calibrated(sem_scores, lex_scores, sem_w=None):
    """Calibrated score fusion (z-normalise each signal, combine magnitudes, logistic →
    (0,1)) - identical to memory_hook._calibrated_fusion. {sid: fused score}."""
    if sem_w is None:
        sem_w = getattr(m, "FUSION_SEM_WEIGHT", 0.5)
    zs, zl = _zmap(sem_scores), _zmap(lex_scores)
    out = {}
    for s in set(zs) | set(zl):
        z = sem_w * zs.get(s, -3.0) + 1.0 * zl.get(s, -3.0)
        out[s] = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
    return out

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DATA = HERE / "data"
ORACLE = DATA / "longmemeval_oracle.json"


def _emb_path(model=None):
    """Per-embedder cache path so embedder A/B runs never mix vectors from
    different models in one file. bge-m3 keeps the original filename so the
    first benchmark's cache still loads unchanged."""
    model = model or m.EMBED_MODEL
    if model == "bge-m3":
        return DATA / "longmem_embeds.json"
    slug = "".join(c if (c.isalnum() or c in "-.") else "_" for c in model)
    return DATA / f"longmem_embeds__{slug}.json"


EMB = _emb_path()
KS = (1, 3, 5, 10)
MAXCHARS = 28000        # bge-m3 ~8k tokens; keep the whole session, cap pathological outliers
# CLI flags are read only when run as a script - importing this module (e.g. from a test)
# must NOT pick up the importer's sys.argv (audit 2026-06-18: a test runner passing --xrerank
# would silently flip XRERANK at import time).
_ARGV = sys.argv if __name__ == "__main__" else []
LIMIT = next((int(a.split("=", 1)[1]) for a in _ARGV if a.startswith("--limit=")), None)
# W2 reranker: re-order the first-stage (hybrid) top-N with a local LLM cross-encoder substitute.
# This is the DECISIVE precision test - external GT (answer_session_ids), not cosine-circular.
RERANK = "--rerank" in _ARGV
# W2 reranker (trained): re-order the top-N with a purpose-trained cross-encoder
# (bge-reranker-v2-m3), the standard precision tool - distinct from the LLM reranker above.
XRERANK = "--xrerank" in _ARGV
RERANK_N = int(os.environ.get("NEVERTWICE_RERANK_N", "10"))      # matches the R@10 pool ceiling
RERANK_SNIP = int(os.environ.get("NEVERTWICE_RERANK_SNIP", "700"))   # per-session passage budget
RERANK_MODEL = os.environ.get("NEVERTWICE_RERANK_MODEL", m.OLLAMA_MODEL)
XRERANK_SNIP = int(os.environ.get("NEVERTWICE_XRERANK_SNIP", "1200"))  # cross-encoder passage budget
OUT = next((a.split("=", 1)[1] for a in _ARGV if a.startswith("--out=")), None)


def _passage(qtokens, text, budget):
    """A question-relevant excerpt of a long session for the reranker: the highest
    token-overlap lines first (kept in original order), falling back to the head.
    Gives the cross-encoder a fair shot at the evidence turn without the full 28k chars."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return text[:budget]
    order = sorted(range(len(lines)), key=lambda i: -len(qtokens & m._tokens(lines[i])))
    keep, used = set(), 0
    for i in order:
        if used >= budget or len(qtokens & m._tokens(lines[i])) == 0:
            break
        keep.add(i)
        used += len(lines[i])
    if not keep:
        return text[:budget]
    return "\n".join(lines[i] for i in sorted(keep))[:budget]


def load():
    data = json.loads(ORACLE.read_text(encoding="utf-8"))
    if LIMIT:
        data = data[:LIMIT]
    pool = {}                                   # session_id -> text
    for e in data:
        for sid, turns in zip(e["haystack_session_ids"], e["haystack_sessions"]):
            if sid not in pool:
                pool[sid] = "\n".join(f"{t.get('role','')}: {t.get('content','')}"
                                      for t in turns)[:MAXCHARS]
    return data, pool


def embed_all():
    data, pool = load()
    cache = {"sessions": {}, "questions": {}}
    if EMB.exists():
        cache = json.loads(EMB.read_text(encoding="utf-8"))
    cache.setdefault("sessions", {})
    cache.setdefault("questions", {})
    sids = [s for s in pool if s not in cache["sessions"]]
    qs = [e for e in data if e["question_id"] not in cache["questions"]]
    print(f"[embed] {len(sids)} sessions + {len(qs)} questions to embed "
          f"(cached: {len(cache['sessions'])} / {len(cache['questions'])})", file=sys.stderr)
    t0 = time.time()
    for i, sid in enumerate(sids):
        v = m.embed_text(pool[sid], kind=m.doc_embed_kind())
        if v:
            cache["sessions"][sid] = v
        if (i + 1) % 50 == 0:
            print(f"  sessions {i+1}/{len(sids)}  ({time.time()-t0:.0f}s)", file=sys.stderr)
            EMB.write_text(json.dumps(cache), encoding="utf-8")   # checkpoint
    for i, e in enumerate(qs):
        v = m.embed_text(e["question"], kind=m.query_embed_kind())
        if v:
            cache["questions"][e["question_id"]] = v
        if (i + 1) % 100 == 0:
            print(f"  questions {i+1}/{len(qs)}  ({time.time()-t0:.0f}s)", file=sys.stderr)
    EMB.write_text(json.dumps(cache), encoding="utf-8")
    print(f"[embed] done in {time.time()-t0:.0f}s → {EMB.name}", file=sys.stderr)


def _recall_mrr(ranked, relevant):
    rec = {k: (1.0 if set(ranked[:k]) & relevant else 0.0) for k in KS}
    rr = 0.0
    for i, sid in enumerate(ranked):
        if sid in relevant:
            rr = 1.0 / (i + 1)
            break
    return rec, rr


def evaluate():
    data, pool = load()
    if not EMB.exists():
        print("No embeddings - run: python research/longmem_eval.py --embed", file=sys.stderr)
        sys.exit(1)
    cache = json.loads(EMB.read_text(encoding="utf-8"))
    svec = cache["sessions"]
    qvec = cache["questions"]
    pool_ids = [s for s in pool if s in svec]
    toks_lists = {s: m._token_list(pool[s]) for s in pool_ids}
    bm_tf, bm_dl, bm_df, bm_avgdl = build_bm25(pool_ids, toks_lists)
    methods = ("semantic", "lexical", "hybrid", "semantic+recur") + (
        ("hybrid+rerank",) if RERANK else ()) + (
        ("hybrid+xrerank",) if XRERANK else ())
    agg = {mth: ({k: 0.0 for k in KS}, [0.0]) for mth in methods}
    rstats = {"calls": 0, "errors": 0, "prompt_chars": 0}
    xr = None
    if XRERANK:
        import _xreranker as xr
        if not xr.available():
            print("xrerank needs torch+transformers - `pip install transformers`", file=sys.stderr)
            sys.exit(1)
        print(f"[xrerank] loading cross-encoder {xr.MODEL} …", file=sys.stderr)
        xr._load()                                     # warm the model once, up front
    t0, n = time.time(), 0
    for e in data:
        qid = e["question_id"]
        rel = set(e["answer_session_ids"])
        if qid not in qvec or not (rel & set(pool_ids)):
            continue
        q = qvec[qid]
        qt = m._tokens(e["question"])
        cos = {s: m.cosine(q, svec[s]) for s in pool_ids}
        bm = bm25_scores(qt, pool_ids, bm_tf, bm_dl, bm_df, bm_avgdl)
        sem = sorted(pool_ids, key=lambda s: (-cos[s], s))
        lex = sorted(bm, key=lambda s: (-bm[s], s))           # BM25 (IDF-weighted) - the real lexical signal
        # hybrid = calibrated score fusion (the shipped production ranker): z-normalise each
        # signal and combine magnitudes (beats rank-fusion, which discards them).
        cal = calibrated(cos, bm)
        hyb = sorted(cal, key=lambda s: (-cal[s], s))
        # semantic + the production recurrence boost (recurrence=1 here → boost=0:
        # this must NOT change the ranking - the Pareto-safety check on real vectors)
        semr = sorted(pool_ids,
                      key=lambda s: -(m.cosine(q, svec[s]) + m._recur_boost({"recurrence": 1})))
        n += 1
        ranked_lists = [("semantic", sem), ("lexical", lex),
                        ("hybrid", hyb), ("semantic+recur", semr)]
        if RERANK:
            pool_n = hyb[:RERANK_N]                      # rerank the best first-stage top-N
            snips = [_passage(qt, pool[s], RERANK_SNIP) for s in pool_n]
            scores = rr.ollama_rerank(e["question"], snips, RERANK_MODEL, rstats, RERANK_SNIP)
            if scores:                                    # re-order by LLM score, tie-break RRF
                order = sorted(range(len(pool_n)),
                               key=lambda i: (-scores[i], -cal.get(pool_n[i], 0.0)))
                rer = [pool_n[i] for i in order]
            else:
                rer = pool_n                              # model failed → keep first-stage order
            seen = set(rer)
            ranked_lists.append(("hybrid+rerank", rer + [s for s in hyb if s not in seen]))
        if XRERANK:
            pool_n = hyb[:RERANK_N]                       # same first-stage top-N as the LLM rerank
            snips = [_passage(qt, pool[s], XRERANK_SNIP) for s in pool_n]
            xs = xr.rerank_scores(e["question"], snips)
            if xs:                                        # re-order by cross-encoder logit, tie-break RRF
                order = sorted(range(len(pool_n)),
                               key=lambda i: (-xs[i], -cal.get(pool_n[i], 0.0)))
                xrr = [pool_n[i] for i in order]
            else:
                xrr = pool_n
            seen = set(xrr)
            ranked_lists.append(("hybrid+xrerank", xrr + [s for s in hyb if s not in seen]))
        for mth, ranked in ranked_lists:
            rec, mr = _recall_mrr(ranked, rel)
            for k in KS:
                agg[mth][0][k] += rec[k]
            agg[mth][1][0] += mr
    print("=" * 74)
    print(f"  LongMemEval-oracle (global pool) - external retrieval recall@k")
    print(f"  {len(pool_ids)} sessions in the shared store, {n} questions, embedder={m.EMBED_MODEL}")
    print("=" * 74)
    print(f"  {'method':16} " + " ".join(f"{'R@'+str(k):>7}" for k in KS) + f" {'MRR':>7}")
    out = {}
    for mth in methods:
        rec, mr = agg[mth]
        row = {f"recall@{k}": rec[k] / n if n else 0 for k in KS}
        row["mrr"] = mr[0] / n if n else 0
        out[mth] = row
        print(f"  {mth:16} " + " ".join(f"{row['recall@'+str(k)]:7.3f}" for k in KS)
              + f" {row['mrr']:7.3f}")
    hy, se = out["hybrid"]["recall@5"], out["semantic"]["recall@5"]
    print(f"\n  → calibrated hybrid vs semantic-only @5: {hy - se:+.3f} "
          f"(external GT, not internal-linkage - this is a real recall number)")
    inert = all(abs(out["semantic"][f"recall@{k}"] - out["semantic+recur"][f"recall@{k}"]) < 1e-9
                for k in KS)
    # NB: every session here is distinct (recurrence=1 → _recur_boost=0), so this is a
    # by-CONSTRUCTION no-harm floor - relevance retrieval is provably unchanged by the
    # recurrence prior - NOT an empirical test of the adaptive scaling (no public corpus
    # carries a natural recurrence signal; that benefit is shown on the synthetic study).
    print(f"  → recurrence boost on this no-recurrence corpus (recurrence=1, boost=0): "
          f"{'inert by construction - relevance retrieval provably unchanged ✓' if inert else 'CHANGED ranking (!)'}")
    rerank_cost = None
    if RERANK and "hybrid+rerank" in out:
        hr, hb = out["hybrid+rerank"], out["hybrid"]
        dt = time.time() - t0
        rerank_cost = {"model": RERANK_MODEL, "n": RERANK_N, "snippet_chars": RERANK_SNIP,
                       "calls": rstats["calls"], "errors": rstats["errors"],
                       "est_prompt_tokens": rstats["prompt_chars"] // 4, "wall_s": round(dt, 1),
                       "delta_recall@1": hr["recall@1"] - hb["recall@1"],
                       "delta_recall@5": hr["recall@5"] - hb["recall@5"]}
        print(f"\n  → RERANK (hybrid+rerank vs hybrid), model={RERANK_MODEL}, N={RERANK_N}:")
        print(f"    R@1 {hb['recall@1']:.3f}→{hr['recall@1']:.3f} ({hr['recall@1']-hb['recall@1']:+.3f})"
              f"   R@3 {hb['recall@3']:.3f}→{hr['recall@3']:.3f} ({hr['recall@3']-hb['recall@3']:+.3f})"
              f"   R@5 {hb['recall@5']:.3f}→{hr['recall@5']:.3f} ({hr['recall@5']-hb['recall@5']:+.3f})")
        print(f"    cost: {rstats['calls']} calls, {rstats['errors']} errors, "
              f"~{rstats['prompt_chars']//4} prompt tok, {dt:.0f}s wall ({dt/max(1,n)*1000:.0f} ms/q)")
        verdict = ("WIN - a cross-encoder reranker lifts external precision; ship as opt-in"
                   if hr["recall@1"] > hb["recall@1"] + 0.005 else
                   "NO WIN - reranking the bi-encoder top-N does not raise external precision here")
        print(f"    → {verdict}")
    xrerank_cost = None
    if XRERANK and "hybrid+xrerank" in out:
        hr, hb = out["hybrid+xrerank"], out["hybrid"]
        xrerank_cost = {"model": xr.MODEL, "n": RERANK_N, "snippet_chars": XRERANK_SNIP,
                        "delta_recall@1": hr["recall@1"] - hb["recall@1"],
                        "delta_recall@5": hr["recall@5"] - hb["recall@5"],
                        "delta_recall@10": hr["recall@10"] - hb["recall@10"]}
        print(f"\n  → XRERANK (hybrid+xrerank vs hybrid), trained cross-encoder {xr.MODEL}, N={RERANK_N}:")
        print(f"    R@1 {hb['recall@1']:.3f}→{hr['recall@1']:.3f} ({hr['recall@1']-hb['recall@1']:+.3f})"
              f"   R@3 {hb['recall@3']:.3f}→{hr['recall@3']:.3f} ({hr['recall@3']-hb['recall@3']:+.3f})"
              f"   R@5 {hb['recall@5']:.3f}→{hr['recall@5']:.3f} ({hr['recall@5']-hb['recall@5']:+.3f})")
        verdict = ("WIN - the trained cross-encoder lifts external precision; ship as opt-in"
                   if hr["recall@1"] > hb["recall@1"] + 0.005 else
                   "NO WIN - even a trained cross-encoder does not raise external precision here")
        print(f"    → {verdict}")
    if "--save" in sys.argv:
        res = {"sessions": len(pool_ids), "questions": n, "embedder": m.EMBED_MODEL,
               "methods": out, "recur_inert": inert}
        if rerank_cost:
            res["rerank"] = rerank_cost
        if xrerank_cost:
            res["xrerank"] = xrerank_cost
        target = Path(OUT) if OUT else (HERE / "longmem_results.json")
        target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  saved → {target}")
    print("=" * 74)
    return out


def main():
    if "--embed" in sys.argv:
        embed_all()
        return
    evaluate()


if __name__ == "__main__":
    main()
