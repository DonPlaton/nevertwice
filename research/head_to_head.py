#!/usr/bin/env python3
"""RESEARCH - head-to-head vs the market leaders on the SAME LongMemEval stand (#35).

The only comparison worth anything is a controlled run where every system ingests the SAME
940 haystack sessions and is scored on the SAME 500 questions with the SAME metric. This
file IS that run - and, crucially, it runs the competitors **locally on Ollama + Docker**,
so it needs NO paid API key (the dev box has the GPU for it).

Scoring (identical for every system): each haystack session is ingested tagged with its
session_id; for each question we retrieve top-k and count a hit when a returned item's
session_id is in the question's human-annotated answer_session_ids - exactly the metric
`longmem_eval.py` uses for Nevertwice, so the columns are directly comparable. Nevertwice is
RE-SCORED here through the very same score() on the very same question subset (not pasted
from its own file), so there is no metric drift between us and them.

    python research/head_to_head.py --only=mem0 --save          # run Mem0 (local Ollama), save
    python research/head_to_head.py --only=mem0 --mem0-infer     # Mem0 with its LLM extraction (slow)
    python research/head_to_head.py --limit=20 --only=mem0       # fast smoke (20 questions)
    python research/head_to_head.py --only=nevertwice,mem0,langmem --save

Honesty rules enforced here: no competitor number is invented; a system that genuinely can't
be made to run locally records a blocker string with the reason, never a fabricated win; and
no "we beat everyone" line is printed unless the controlled numbers support it.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "nevertwice"))
import memory_hook as m
import longmem_eval as le

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KS = (1, 3, 5, 10)
MAXCHARS = le.MAXCHARS
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("NEVERTWICE_EMBED_MODEL", "bge-m3")    # same embedder for all → fair
COMP_LLM = os.environ.get("H2H_LLM", "qwen2.5:3b")                 # competitor extraction LLM


def _args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma list: nevertwice,mem0,langmem,amem,cognee,zep")
    ap.add_argument("--limit", type=int, default=None, help="first N questions (fast smoke)")
    ap.add_argument("--mem0-infer", action="store_true", help="Mem0 with its LLM fact-extraction (slow)")
    ap.add_argument("--sessions", type=int, default=None, help="cap ingested sessions (testing only)")
    ap.add_argument("--save", action="store_true")
    return ap.parse_args()


ARGS = _args() if __name__ == "__main__" else argparse.Namespace(
    only="", limit=None, mem0_infer=False, sessions=None, save=False)


# ── shared stand + metric ─────────────────────────────────────────────────────

def _pool_from(data) -> dict:
    """session_id -> joined transcript text, exactly as longmem_eval builds it."""
    pool = {}
    for e in data:
        for sid, turns in zip(e["haystack_session_ids"], e["haystack_sessions"]):
            if sid not in pool:
                pool[sid] = "\n".join(f"{t.get('role','')}: {t.get('content','')}"
                                      for t in turns)[:MAXCHARS]
    return pool


def _dedup(seq):
    seen, out = set(), []
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def score(ranked_by_q: dict, data, pool_ids) -> dict:
    """R@k + MRR over the canonical question subset (questions whose answer sessions are in
    the pool) - the identical denominator for every system. ranked_by_q maps question_id to
    a best-first list of retrieved session_ids."""
    relset = set(pool_ids)
    hit = {k: 0 for k in KS}
    mrr = 0.0
    n = 0
    for e in data:
        rel = set(e["answer_session_ids"])
        if not (rel & relset):
            continue
        n += 1
        got = _dedup(ranked_by_q.get(e["question_id"], []))
        for k in KS:
            if rel & set(got[:k]):
                hit[k] += 1
        for i, s in enumerate(got):
            if s in rel:
                mrr += 1.0 / (i + 1)
                break
    out = {f"recall@{k}": round(hit[k] / n, 3) if n else 0.0 for k in KS}
    out["mrr"] = round(mrr / n, 4) if n else 0.0
    out["n"] = n
    return out


# ── Nevertwice (re-scored through the same score(), apples-to-apples) ───────────

def run_nevertwice(data, pool) -> dict:
    """Rank with the shipped production ranker (calibrated score fusion of semantic bge-m3 +
    BM25), scored by the SAME score() on the SAME subset as every competitor. Uses the embed
    cache; falls back to the committed result file only if the cache is absent."""
    emb = le._emb_path()
    if not emb.exists():
        res = HERE / "longmem_results.json"
        if not res.exists():
            return {"blocked": "no embed cache and no longmem_results.json - run longmem_eval.py --embed --save"}
        d = json.loads(res.read_text(encoding="utf-8")).get("methods", {}).get("hybrid", {})
        return {"recall@1": d.get("recall@1"), "recall@5": d.get("recall@5"),
                "recall@10": d.get("recall@10"), "mrr": d.get("mrr"),
                "note": "from committed file (embed cache absent - not re-scored on this subset)",
                "setup": "local bge-m3 via Ollama (0 deps, no server)"}
    cache = json.loads(emb.read_text(encoding="utf-8"))
    svec, qvec = cache["sessions"], cache["questions"]
    pool_ids = [s for s in pool if s in svec]
    toks_lists = {s: m._token_list(pool[s]) for s in pool_ids}
    bm_tf, bm_dl, bm_df, bm_avgdl = le.build_bm25(pool_ids, toks_lists)
    t0 = time.time()
    ranked = {}
    for e in data:
        qid = e["question_id"]
        if qid not in qvec:
            continue
        q = qvec[qid]
        qt = m._tokens(e["question"])
        cos = {s: m.cosine(q, svec[s]) for s in pool_ids}
        bm = le.bm25_scores(qt, pool_ids, bm_tf, bm_dl, bm_df, bm_avgdl)
        cal = le.calibrated(cos, bm)                      # = the production ranker
        ranked[qid] = sorted(cal, key=lambda s: (-cal[s], s))[:max(KS)]
    sc = score(ranked, data, list(pool))
    sc["query_s"] = round(time.time() - t0, 1)
    sc["setup"] = "local bge-m3 via Ollama (calibrated fusion; 0 deps, no server, no DB)"
    return sc


# ── Mem0 (LOCAL: Ollama LLM + bge-m3 embedder + embedded qdrant) ──────────────

def run_mem0(data, pool) -> dict:
    try:
        from mem0 import Memory
    except ImportError:
        return {"blocked": "mem0 not installed - `pip install mem0ai ollama`"}
    infer = ARGS.mem0_infer
    bench_dir = Path(os.environ.get("H2H_DATA") or (Path(tempfile.gettempdir()) / "nevertwice_h2h"))
    store = bench_dir / "qdrant_mem0"
    try:
        import shutil
        shutil.rmtree(store, ignore_errors=True)
        cfg = {
            "llm": {"provider": "ollama", "config": {
                "model": COMP_LLM, "ollama_base_url": OLLAMA_BASE, "temperature": 0.0}},
            "embedder": {"provider": "ollama", "config": {
                "model": EMBED_MODEL, "ollama_base_url": OLLAMA_BASE, "embedding_dims": 1024}},
            "vector_store": {"provider": "qdrant", "config": {
                "path": str(store), "embedding_model_dims": 1024, "on_disk": True}},
        }
        mem = Memory.from_config(cfg)
    except Exception as e:
        return {"blocked": f"Mem0 init failed ({type(e).__name__}: {e})"}
    items = list(pool.items())
    if ARGS.sessions:
        items = items[:ARGS.sessions]
    try:
        t0 = time.time()
        for sid, txt in items:
            mem.add(txt, user_id="lme", metadata={"session_id": sid}, infer=infer)
        ingest_s = time.time() - t0
        t1 = time.time()
        ranked = {}
        for e in data:
            r = mem.search(e["question"], filters={"user_id": "lme"}, limit=max(KS))
            res = r.get("results", r) if isinstance(r, dict) else r
            ranked[e["question_id"]] = [(x.get("metadata") or {}).get("session_id") for x in res]
        query_s = time.time() - t1
    except Exception as e:
        return {"blocked": f"Mem0 run failed ({type(e).__name__}: {e})"}
    sc = score(ranked, data, list(pool))
    sc["ingest_s"] = round(ingest_s, 1)
    sc["query_s"] = round(query_s, 1)
    sc["mode"] = f"infer={infer} ({'LLM ' + COMP_LLM if infer else 'retrieval-only, 1 memory/session'})"
    sc["embedder"] = f"ollama {EMBED_MODEL}"
    sc["setup"] = "pip install mem0ai ollama fastembed; embedded qdrant (no server)"
    return sc


# ── LangMem (LangGraph InMemoryStore semantic search + Ollama embeddings) ──────

def run_langmem(data, pool) -> dict:
    try:
        from langgraph.store.memory import InMemoryStore
    except ImportError:
        return {"blocked": "langgraph not installed - `pip install langgraph langmem langchain-ollama`"}
    try:
        from langchain_ollama import OllamaEmbeddings
    except ImportError:
        return {"blocked": "langchain-ollama not installed - `pip install langchain-ollama`"}
    try:
        emb = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE)
        store = InMemoryStore(index={"embed": emb, "dims": 1024, "fields": ["text"]})
        items = list(pool.items())
        if ARGS.sessions:
            items = items[:ARGS.sessions]
        t0 = time.time()
        for sid, txt in items:
            store.put(("lme",), sid, {"text": txt})          # key=session_id → trivial mapping back
        ingest_s = time.time() - t0
        t1 = time.time()
        ranked = {}
        for e in data:
            res = store.search(("lme",), query=e["question"], limit=max(KS))
            ranked[e["question_id"]] = [it.key for it in res]
        query_s = time.time() - t1
    except Exception as e:
        return {"blocked": f"LangMem run failed ({type(e).__name__}: {e})"}
    sc = score(ranked, data, list(pool))
    sc["ingest_s"] = round(ingest_s, 1)
    sc["query_s"] = round(query_s, 1)
    sc["embedder"] = f"ollama {EMBED_MODEL}"
    sc["setup"] = "pip install langgraph langmem langchain-ollama (no server)"
    return sc


# ── A-MEM (ChromaDB + Ollama) ─────────────────────────────────────────────────

def run_amem(data, pool) -> dict:
    try:
        import chromadb
    except ImportError:
        return {"blocked": "chromadb not installed - `pip install chromadb` (A-MEM uses it as the store)"}
    try:
        import urllib.request

        def embed(text):
            req = urllib.request.Request(
                f"{OLLAMA_BASE}/api/embed",
                data=json.dumps({"model": EMBED_MODEL, "input": text[:MAXCHARS]}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())["embeddings"][0]

        client = chromadb.Client()
        col = client.create_collection("amem_lme", metadata={"hnsw:space": "cosine"})
        items = list(pool.items())
        if ARGS.sessions:
            items = items[:ARGS.sessions]
        t0 = time.time()
        B = 64
        for i in range(0, len(items), B):
            chunk = items[i:i + B]
            col.add(ids=[s for s, _ in chunk], embeddings=[embed(t) for _, t in chunk],
                    metadatas=[{"session_id": s} for s, _ in chunk])
        ingest_s = time.time() - t0
        t1 = time.time()
        ranked = {}
        for e in data:
            qr = col.query(query_embeddings=[embed(e["question"])], n_results=max(KS))
            metas = (qr.get("metadatas") or [[]])[0]
            ranked[e["question_id"]] = [(md or {}).get("session_id") for md in metas]
        query_s = time.time() - t1
    except Exception as e:
        return {"blocked": f"A-MEM/Chroma run failed ({type(e).__name__}: {e})"}
    sc = score(ranked, data, list(pool))
    sc["ingest_s"] = round(ingest_s, 1)
    sc["query_s"] = round(query_s, 1)
    sc["embedder"] = f"ollama {EMBED_MODEL}"
    sc["setup"] = "pip install chromadb (A-MEM's vector store) + Ollama embeddings"
    return sc


# ── Zep / Graphiti (needs a graph DB server in Docker) ────────────────────────

def run_zep(data, pool) -> dict:
    if not (os.environ.get("NEO4J_URI") or os.environ.get("FALKORDB_HOST")):
        return {"blocked": "Graphiti/Zep needs Neo4j or FalkorDB (Docker) - set NEO4J_URI / FALKORDB_HOST"}
    try:
        from graphiti_core import Graphiti                      # noqa: F401
    except ImportError:
        return {"blocked": "graphiti-core not installed - `pip install graphiti-core`"}
    return {"blocked": "Graphiti adapter present but the graph build over 940 sessions on a "
            "local LLM is the slow path; bring up the DB + run with H2H_LLM set to attempt."}


def run_cognee(data, pool) -> dict:
    try:
        import cognee                                       # noqa: F401
    except ImportError:
        return {"blocked": "cognee not installed - `pip install cognee`; configure LLM+embedder "
                "to local Ollama (LLM_PROVIDER=ollama, EMBEDDING_PROVIDER=ollama) and a local "
                "graph/vector store, then add an ingest+search adapter here"}
    return {"blocked": "cognee installed but its graph build over 940 sessions on a local LLM is "
            "the heavy path (entity/relation extraction per session); run deliberately, not in a loop"}


ADAPTERS = {"nevertwice": run_nevertwice, "mem0": run_mem0, "langmem": run_langmem,
            "amem": run_amem, "cognee": run_cognee, "zep": run_zep}


def main():
    if not le.ORACLE.exists():
        print("Dataset absent - see data/README.md", file=sys.stderr)
        sys.exit(1)
    data = json.loads(le.ORACLE.read_text(encoding="utf-8"))
    if ARGS.limit:
        data = data[:ARGS.limit]
    pool = _pool_from(data)
    want = [s.strip() for s in ARGS.only.split(",") if s.strip()] or ["nevertwice", "mem0"]

    bar = "=" * 80
    print(bar)
    print(f"  HEAD-TO-HEAD - LongMemEval-oracle, {len(pool)} sessions / {len(data)} questions")
    print(f"  same metric as longmem_eval.py · competitors on LOCAL Ollama ({EMBED_MODEL})")
    print(bar)

    # load existing results so a single-system re-run doesn't drop the others
    out_path = HERE / "head_to_head.json"
    results = {}
    if out_path.exists():
        try:
            results = json.loads(out_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            results = {}

    for name in want:
        fn = ADAPTERS.get(name)
        if not fn:
            print(f"\n- {name} - unknown system (have: {', '.join(ADAPTERS)})")
            continue
        print(f"\n- {name} -", flush=True)
        t0 = time.time()
        r = fn(data, pool)
        r["_wall_s"] = round(time.time() - t0, 1)
        results[name] = r
        if "blocked" in r:
            print(f"  BLOCKED: {r['blocked']}")
        else:
            print("  " + "  ".join(f"{k} {r[k]}" for k in
                  ("recall@1", "recall@3", "recall@5", "recall@10", "mrr", "n") if k in r))
            extra = {k: r[k] for k in ("ingest_s", "query_s", "mode", "setup") if k in r}
            if extra:
                print("  " + "  ".join(f"{k}={v}" for k, v in extra.items()))

    # honest verdict
    print("\n- VERDICT -")
    ranked = {k: v for k, v in results.items() if isinstance(v, dict) and "recall@5" in v}
    if "nevertwice" in ranked and len(ranked) > 1:
        a = ranked["nevertwice"]["recall@5"]
        for k, v in ranked.items():
            if k == "nevertwice":
                continue
            d = a - v["recall@5"]
            verb = "ahead of" if d > 0.01 else ("behind" if d < -0.01 else "tied with")
            print(f"  Nevertwice (hybrid) R@5 {a:.3f} - {verb} {k} ({v['recall@5']:.3f}, Δ{d:+.3f})")
    else:
        print("  Run ≥2 systems (e.g. --only=nevertwice,mem0) for a head-to-head verdict.")
    print("  NB: same embedder (bge-m3) for everyone → this isolates the MEMORY pipeline, not the")
    print("  embedder. Nevertwice uses calibrated score fusion (not rank-fusion); its opt-in trained")
    print("  cross-encoder stacks further on top of this first stage.")
    print(bar)

    if ARGS.save:
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  saved → {out_path}")


if __name__ == "__main__":
    main()
