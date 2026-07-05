#!/usr/bin/env python3
"""RESEARCH - evaluation harness for the memory vault. GPU-free: leave-one-out
over the EXISTING embedding cache (cached vectors used as queries → CPU cosine),
lexical token overlap, and structural temporal QA. No new embeddings, no network.

Three tasks (the measurable foundation for every retrieval claim, incl. I-2/I-3):
  A. Retrieval recall@k & MRR - SEMANTIC vs LEXICAL vs HYBRID (RRF). IMPORTANT
     (audit H7): the ground truth here is the notes' own `[[wikilink]]` neighbours,
     which the SAME system writes. So Task A measures INTERNAL-LINKAGE RECOVERY /
     ranker self-consistency - "does the ranker resurface a note's own siblings"
     - NOT relevance to an external information need. It is a fair RELATIVE
     comparator of the three rankers on identical ground truth (which is what I-2
     uses it for), but its absolute number is NOT an external quality benchmark
     and must not be quoted as one. For that, use Task D (--longmem) with an
     independent dataset.
  B. Temporal point-in-time QA - given a date, return the version of a fact that
     was current THEN. Compares the bi-temporal graph vs flat "use newest" vs
     flat "return all" (ambiguous). Auto-generated from supersession families.
  C. Token economy - tokens per recall: full-Context read vs flat top-k vs
     temporal current-snapshot, averaged across projects.

    python research/eval_harness.py            # report
    python research/eval_harness.py --save      # + eval_results.json
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import memory_hook as m
import temporal_graph as tg

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KS = (1, 3, 5)
# --fresh-query: re-embed each query's TEXT with the query prefix (real retrieval
# scenario) instead of reusing its stored doc vector - needed to measure the
# query/doc prefix asymmetry and to compare embedders faithfully (uses GPU).
FRESH = "--fresh-query" in sys.argv


def toks(s):
    return len(s) // 4


# ── ranking primitives (GPU-free) ─────────────────────────────────────

def rank_semantic(qvec, pool, cache):
    out = []
    for oid in pool:
        v = cache.get(oid, {}).get("vec")
        if isinstance(v, list):
            out.append((m.cosine(qvec, v), oid))
    out.sort(key=lambda x: -x[0])
    return [oid for _, oid in out]


def rank_lexical(qtext, pool, textmap):
    qt = m._tokens(qtext)
    if not qt:
        return []
    out = []
    for oid in pool:
        ov = len(qt & textmap.get(oid, set()))
        if ov:
            out.append((ov / (len(qt) ** 0.5), oid))
    out.sort(key=lambda x: -x[0])
    return [oid for _, oid in out]


def rrf(rankings, k=60, weights=None):
    """Weighted Reciprocal Rank Fusion of ranked id-lists (mirrors production)."""
    score = {}
    for j, rk in enumerate(rankings):
        w = weights[j] if (weights and j < len(weights)) else 1.0
        for i, oid in enumerate(rk):
            score[oid] = score.get(oid, 0.0) + w / (k + i + 1)
    return [oid for oid, _ in sorted(score.items(), key=lambda x: -x[1])]


def recall_mrr(ranked, relevant, ks=KS):
    rec = {k: 0.0 for k in ks}
    rr = 0.0
    if not relevant:
        return rec, rr
    for k in ks:
        if set(ranked[:k]) & relevant:
            rec[k] = 1.0
    for i, oid in enumerate(ranked):
        if oid in relevant:
            rr = 1.0 / (i + 1)
            break
    return rec, rr


# ── Task A: retrieval recall@k / MRR ───────────────────────────────────

def task_a(nodes):
    cache = m.load_embed_cache()
    by_id = {n["id"]: n for n in nodes}
    vec_ids = {n["id"] for n in nodes if isinstance(cache.get(n["id"], {}).get("vec"), list)}
    textmap = {n["id"]: m._tokens(f"{n['desc']} {n['prevention']} {n['slug']}")
               for n in nodes}

    agg = {meth: ({k: 0.0 for k in KS}, [0.0]) for meth in ("semantic", "lexical", "hybrid")}
    n_q = 0
    for q in nodes:
        if q["id"] not in vec_ids:
            continue
        # relevance = wikilinked typed-note neighbours in the same project that have vectors
        rel = {l for l in q["links"]
               if l in by_id and by_id[l]["project"] == q["project"] and l in vec_ids}
        if not rel:
            continue
        pool = [i for i in vec_ids if i != q["id"] and by_id[i]["project"] == q["project"]]
        if len(pool) < 2:
            continue
        qtext = f"{q['desc']} {q['prevention']} {q['slug']}"
        if FRESH:
            qvec = m.embed_text(qtext.strip(), kind=m.query_embed_kind())
            if not qvec:
                continue
        else:
            qvec = cache[q["id"]]["vec"]
        n_q += 1
        rsem = rank_semantic(qvec, pool, cache)
        rlex = rank_lexical(qtext, pool, textmap)
        rhyb = rrf([rsem, rlex], weights=[m.RETRIEVAL_SEM_WEIGHT, 1.0])
        for meth, ranked in (("semantic", rsem), ("lexical", rlex), ("hybrid", rhyb)):
            rec, rr = recall_mrr(ranked, rel)
            for k in KS:
                agg[meth][0][k] += rec[k]
            agg[meth][1][0] += rr
    out = {}
    for meth, (rec, rr) in agg.items():
        out[meth] = {f"recall@{k}": (rec[k] / n_q if n_q else 0) for k in KS}
        out[meth]["mrr"] = rr[0] / n_q if n_q else 0
    return out, n_q


# ── Task B: temporal point-in-time QA ──────────────────────────────────

def task_b(nodes):
    fam = {}
    for n in nodes:
        fam.setdefault((n["project"], n["ntype"], n["slug"]), []).append(n)
    fams = {k: sorted(v, key=lambda n: n["valid_from"]) for k, v in fam.items() if len(v) > 1}
    q_total = temporal_ok = flat_new_ok = 0
    ambiguity = []
    for key, versions in fams.items():
        for i, v in enumerate(versions):
            # query at the moment this version became current → truth = this version
            qdate = v["valid_from"]
            truth = v["id"]
            q_total += 1
            # temporal graph: the version whose [valid_from, valid_to) contains qdate
            cur = [u for u in versions
                   if u["valid_from"] <= qdate and (u["valid_to"] is tg.OPEN or qdate < u["valid_to"])]
            if len(cur) == 1 and cur[0]["id"] == truth:
                temporal_ok += 1
            # flat "use newest": always the last version
            if versions[-1]["id"] == truth:
                flat_new_ok += 1
            # flat "return all": ambiguity = how many versions a similarity search returns
            ambiguity.append(len(versions))
    return {
        "questions": q_total,
        "temporal_accuracy": temporal_ok / q_total if q_total else None,
        "flat_newest_accuracy": flat_new_ok / q_total if q_total else None,
        "flat_all_avg_versions_returned": (sum(ambiguity) / len(ambiguity)) if ambiguity else None,
        "families_tested": len(fams),
    }


# ── Task C: token economy ──────────────────────────────────────────────

def task_c(nodes):
    by_proj = {}
    for n in nodes:
        by_proj.setdefault(n["project"], []).append(n)
    rows = []
    for proj, ns in by_proj.items():
        cur = [n for n in ns if n["status"] == "current"]
        flat5 = sorted(ns, key=lambda n: n["valid_from"], reverse=True)[:5]
        snap_cur = "\n".join(f"- {n['ntype']}: {n['desc'][:80] or n['slug']}" for n in cur)
        snap_flat = "\n".join(f"- {n['ntype']}: {n['desc'][:80] or n['slug']}" for n in flat5)
        ctx = m.VAULT / "Context" / f"{proj}.md"
        ctx_t = toks(ctx.read_text(encoding="utf-8", errors="replace")) if ctx.exists() else 0
        rows.append((proj, len(cur), ctx_t, toks(snap_flat), toks(snap_cur)))
    return rows


def task_longmem(path):
    """Recall@k against an external benchmark (M-9): a JSON list of
    {"question": str, "relevant": [stem, ...]}. We target LongMemEval / BEAM (NOT
    LOCOMO - academically discredited, BM25≈0.94); the dataset is downloaded
    separately and converted to this shape. GPU-free lexical runner so it always
    works; swap in semantic by embedding the questions if the GPU is free."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as e:
        print(f"\n- TASK D: external benchmark - could not read {path}: {e}")
        return
    cache = m.load_embed_cache()
    textmap = {s: m._tokens(f"{r.get('title','')} {r.get('desc','')} {r.get('prevention','')}")
               for s, r in cache.items() if isinstance(r, dict)}
    pool = list(textmap)
    hits, n = {1: 0, 3: 0, 5: 0}, 0
    for item in (data if isinstance(data, list) else []):
        q, rel = item.get("question", ""), set(item.get("relevant", []))
        if not q or not rel:
            continue
        ranked = rank_lexical(q, pool, textmap)
        n += 1
        for k in hits:
            if rel & set(ranked[:k]):
                hits[k] += 1
    print(f"\n- TASK D: external benchmark recall ({n} queries from {Path(path).name}) -")
    for k in (1, 3, 5):
        print(f"  recall@{k}: {hits[k] / n:.3f}" if n else f"  recall@{k}: n/a")


def main():
    t0 = time.time()
    nodes = tg.load_nodes()
    tg.apply_supersession(nodes)
    a, n_q = task_a(nodes)
    b = task_b(nodes)
    c = task_c(nodes)
    dt = time.time() - t0

    bar = "=" * 76
    print(bar)
    print("  MEMORY EVAL HARNESS - GPU-free (leave-one-out on cached vectors + lexical)")
    print(bar)
    print(f"\n- TASK A: INTERNAL-LINKAGE recall@k / MRR  (n={n_q} queries) -")
    print("  ground truth = each note's own [[wikilink]] neighbours (system-written);")
    print("  this RELATIVELY compares the three rankers - it is NOT an external")
    print("  relevance benchmark and its absolute value must not be quoted as one (H7).")
    print(f"  {'method':10} {'R@1':>7} {'R@3':>7} {'R@5':>7} {'MRR':>7}")
    for meth in ("semantic", "lexical", "hybrid"):
        r = a[meth]
        print(f"  {meth:10} {r['recall@1']:7.3f} {r['recall@3']:7.3f} "
              f"{r['recall@5']:7.3f} {r['mrr']:7.3f}")
    best = max(("semantic", "lexical", "hybrid"), key=lambda mth: a[mth]["recall@5"])
    print(f"  → best (relative): {best}.  Hybrid lift over semantic: "
          f"{a['hybrid']['recall@5'] - a['semantic']['recall@5']:+.3f}  (ranker ablation, I-2)")

    print(f"\n- TASK B: temporal point-in-time QA  ({b['questions']} questions, "
          f"{b['families_tested']} revised facts) -")
    print(f"  bi-temporal graph accuracy : {b['temporal_accuracy']:.3f}")
    print(f"  flat 'use newest' accuracy : {b['flat_newest_accuracy']:.3f}")
    print(f"  flat 'return all' ambiguity: {b['flat_all_avg_versions_returned']:.2f} "
          f"versions/query (contradictory facts surfaced at once)")
    print(f"  → temporal advantage on point-in-time recall: "
          f"{b['temporal_accuracy'] - b['flat_newest_accuracy']:+.3f}")

    print(f"\n- TASK C: token economy per project (tokens to convey project state) -")
    print(f"  {'project':24} {'cur':>4} {'fullCtx':>8} {'flat5':>6} {'temporalNow':>12}")
    tot_ctx = tot_snap = 0
    for proj, ncur, ctx_t, flat_t, snap_t in sorted(c, key=lambda r: -r[2]):
        print(f"  {proj:24} {ncur:4} {ctx_t:8} {flat_t:6} {snap_t:12}")
        tot_ctx += ctx_t
        tot_snap += snap_t
    if tot_snap:
        print(f"  → temporal current-snapshot vs full Context overall: "
              f"{tot_ctx}/{tot_snap} = {tot_ctx/tot_snap:.2f}x fewer tokens, "
              f"point-in-time & contradiction-free")
    print(f"\n  eval time: {dt:.2f}s, CPU only, $0")
    print(bar)

    lm = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--longmem=")), None)
    if lm:
        task_longmem(lm)
    elif "--longmem" in sys.argv:
        print("  [longmem] usage: --longmem=PATH.json  (target LongMemEval/BEAM, "
              "converted to [{question, relevant:[stem,...]}])")
    else:
        print("\n- TASK D: external benchmark - NOT RUN (audit M-c). No public "
              "number is claimed; Task A above is internal-linkage only. Run with "
              "--longmem=PATH.json (LongMemEval/BEAM, downloaded separately) for an "
              "independent recall figure.")

    if "--save" in sys.argv:
        res = {"generated": datetime.now().isoformat(timespec="seconds"),
               "task_a_retrieval": a, "task_a_n_queries": n_q,
               "task_b_temporal_qa": b,
               "task_c_token_economy": [
                   {"project": p, "current_facts": n, "full_context_tok": ct,
                    "flat5_tok": ft, "temporal_now_tok": st}
                   for p, n, ct, ft, st in c]}
        out = m.VAULT / "eval_results.json"
        m.write_atomic(out, json.dumps(res, ensure_ascii=False, indent=1))
        print(f"  saved → {out}")


if __name__ == "__main__":
    main()
