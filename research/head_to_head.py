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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sandbox_guard  # noqa: E402 - one store sandbox for the whole repo
sandbox_guard.isolate()  # throwaway store, verified, before any project import
import corpus_pin
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
COMP_NUM_CTX = int(os.environ.get("H2H_NUM_CTX", "16384"))         # where the client can set it

# The pip package each adapter actually imports, so the run records the versions it compared
# against - "same stand, reproducible" is only credible if the competitor versions are pinned in
# the output rather than left to whatever happened to be installed (critic 2026-07).
_PKG = {"mem0": "mem0ai", "mem0_infer": "mem0ai", "langmem": "langmem",
        "langmem_full": "langmem", "amem": "chromadb", "amem_full": "a-mem",
        "cognee": "cognee", "zep": "graphiti-core", "nevertwice": "nevertwice"}

# What each competitor arm actually exercises. The first version of this stand called the
# store arms "LangMem" and "A-MEM": LangGraph's InMemoryStore search is LangMem's storage
# layer with none of its memory manager, and chromadb cosine over the same vectors is
# A-MEM's vector store with none of its LLM note construction or link evolution. A reader
# took those rows as the products (review 2026-09-05). The `*_full` arms run the products'
# own pipelines, LLM included; the store arms stay, labelled as what they are, because they
# isolate the retrieval layer the way the Nevertwice arm does.
ARM_LABEL = {
    "nevertwice": "Nevertwice (calibrated fusion, the shipped ranker)",
    "mem0": "Mem0 store search (infer=False: no LLM extraction, one memory per item; "
            "its default hybrid of dense cosine + fastembed BM25 when fastembed is installed)",
    "mem0_infer": "Mem0 full pipeline (infer=True: its LLM fact extraction, then its search)",
    "langmem": "LangGraph InMemoryStore semantic search (LangMem's store layer, no memory manager)",
    "langmem_full": "LangMem full pipeline (create_memory_store_manager: LLM extraction into the store)",
    "amem": "chromadb cosine over the same vectors (A-MEM's vector store, no LLM notes, no links)",
    "amem_full": "A-MEM full pipeline (agentic_memory: LLM note construction, link evolution)",
}


def _pkg_ver(system: str) -> str:
    """Installed version of a competitor's package, or '?' if unavailable."""
    pkg = _PKG.get(system)
    if not pkg:
        return "?"
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return f"{pkg}=={version(pkg)}"
        except PackageNotFoundError:
            return f"{pkg} (not installed)"
    except Exception:
        return "?"


def _args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma list: nevertwice,mem0,mem0_infer,langmem,"
                    "langmem_full,amem,amem_full,cognee,zep")
    ap.add_argument("--limit", type=int, default=None, help="first N questions (fast smoke)")
    ap.add_argument("--mem0-infer", action="store_true", help="Mem0 with its LLM fact-extraction (slow)")
    ap.add_argument("--sessions", type=int, default=None, help="cap ingested sessions (testing only)")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--out", default="", help="write the result here instead of head_to_head.json")
    # Consumed by longmem_eval at import (it reads sys.argv directly so an importer can select
    # the corpus); declared here so argparse does not reject it.
    ap.add_argument("--data", default="oracle", choices=["oracle", "s", "locomo"],
                    help="which pinned corpus to run on: two LongMemEval variants or LoCoMo")
    ap.add_argument("--no-morphology", action="store_true",
                    help="ablation for our arm: raw tokens, no stop words, no stems")
    return ap.parse_args()


ARGS = _args() if __name__ == "__main__" else argparse.Namespace(out="",
    only="", limit=None, mem0_infer=False, sessions=None, save=False, no_morphology=False)
if getattr(ARGS, "no_morphology", False):
    m.LEXICAL_MORPHOLOGY = False


def _git_head() -> str:
    """HEAD from the repository files, no subprocess: spawning git failed under memory pressure
    on 2026-09-06 and stamped two rows with '?'. Falls back to `git rev-parse` only when the
    files do not resolve (a packed ref, a worktree)."""
    import subprocess                                            # noqa: PLC0415
    git = HERE.parent / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = git / head[5:]
            if ref.exists():
                return ref.read_text(encoding="utf-8").strip()
            packed = git / "packed-refs"
            if packed.exists():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == head[5:]:
                        return parts[0]
        elif len(head) >= 7:
            return head
    except OSError:
        pass
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE.parent, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "?"


def _measured_at() -> dict:
    """The commit and the moment an arm's row was produced. Rows are merged into one artifact
    across runs (a competitor arm is not re-run when only our engine changed), so each row
    carries its own stamp rather than inheriting the file's."""
    import datetime                                              # noqa: PLC0415
    head = _git_head()
    return {"commit": head, "utc": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ")}


# ── shared stand + metric ─────────────────────────────────────────────────────

def _too_long(exc: BaseException) -> bool:
    """The embedder refused the text for its length (Ollama: 'exceeds the context length').

    A raw `urllib` HTTPError says only "HTTP Error 400: Bad Request"; the reason is in its body,
    which is read once here (A-MEM's S run blocked on exactly this, 2026-09-06, while the Mem0
    and LangChain clients had surfaced the text in the exception)."""
    if "context length" in str(exc):
        return True
    body = getattr(exc, "_nevertwice_body", None)
    if body is None and hasattr(exc, "read"):
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:                                        # noqa: BLE001
            body = ""
        try:
            exc._nevertwice_body = body                          # a body can be read only once
        except Exception:                                        # noqa: BLE001
            pass
    return bool(body) and "context length" in body


def _shrinking(call, text: str, stats: dict):
    """Run `call(text)`; on the embedder's length refusal retry with half the text, up to three
    times, and count it in `stats`. Our own arm does the same in `longmem_eval.embed_full`,
    so a session that is too dense for the embedder is treated alike on every arm instead
    of aborting a competitor's whole run an hour into its ingest (S pool, 2026-09-06)."""
    for attempt in range(4):
        try:
            return call(text)
        except Exception as e:                                # noqa: BLE001 - counted below
            if _too_long(e) and attempt < 3 and len(text) > 1000:
                text = text[:len(text) // 2]
                stats["shrunk"] = stats.get("shrunk", 0) + 1 if attempt == 0 else stats["shrunk"]
                continue
            raise


def _ingestable(items):
    """The (sid, text) pairs with any text. 623 of the S pool's 19,829 sessions are empty in
    the published corpus; our arm never embeds them (no vector, so they cannot be retrieved,
    and the count is on the page), and an embedder handed an empty string raises - Mem0's did,
    and blocked its whole S run an hour in (2026-09-06). Same rule for every arm."""
    return [(sid, txt) for sid, txt in items if txt and txt.strip()]


def _pool_from(data) -> dict:
    """session_id -> joined transcript text, exactly as longmem_eval builds it."""
    pool = {}
    for e in data:
        for sid, turns in zip(e["haystack_session_ids"], e["haystack_sessions"]):
            if sid not in pool:
                pool[sid] = "\n".join(f"{t.get('role','')}: {t.get('content','')}"
                                      for t in turns)[:MAXCHARS]
    return pool


def named_or_raise(fn, *names) -> None:
    """Refuse to call `fn` with keywords it does not have.

    A library with `**kwargs` accepts any keyword and may ignore it in silence. `limit=10` was
    passed to mem0's `Memory.search` for as long as this stand has existed; the parameter is
    `top_k`, default 20, and `limit` went into `**kwargs` and was dropped - no exception, no
    warning, and every run fetched twenty where the code said ten.

    The diagnostic that catches this class: name the value at which the argument would change
    the result. There is none, so the argument is not about the system - it is a word. This
    asks the receiving signature instead of trusting the call, and it can only run where the
    package is installed, which is exactly where the call happens.

    It does not catch a keyword the library accepts and then ignores by its own logic; that
    boundary is real and is why this refuses rather than certifies.
    """
    import inspect                                            # noqa: PLC0415
    params = inspect.signature(fn).parameters
    missing = [n for n in names if n not in params]
    if missing:
        raise TypeError(f"{fn.__qualname__} has no parameter(s) {missing} - it would swallow "
                        f"them in **kwargs. Its signature is ({', '.join(params)}).")


def _dedup(seq):
    seen, out = set(), []
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def accept(name: str, row: dict) -> dict:
    """A scored row, or a blocker when the run cannot be a product's number.

    Zero recall at every k over a pool that contains the answers means no query retrieved
    anything at all - an adapter or endpoint failure, not a ranking. The A-MEM pipeline arm
    read exactly that on 2026-09-06 (the shim above), and the row would have been published as
    the product's score. The numbers are kept under `refused` so the artifact still records
    what happened."""
    if "blocked" in row or not row.get("n") or row.get(f"recall@{max(KS)}"):
        return row
    keep = {k: row[k] for k in ("version", "label", "measured_at", "_wall_s") if k in row}
    return {**keep, "blocked": f"{name}: every query returned nothing over {row['n']} questions "
                               "- the stand refuses to score a run that retrieved nothing",
            "refused": {k: v for k, v in row.items() if k not in keep}}


def score(ranked_by_q: dict, data, pool_ids) -> dict:
    """R@k + MRR over the canonical question subset (questions whose answer sessions are in
    the pool) - the identical denominator for every system. ranked_by_q maps question_id to
    a best-first list of retrieved session_ids."""
    relset = set(pool_ids)
    hit = {k: 0 for k in KS}
    mrr = 0.0
    n = 0
    per_q = []
    for e in data:
        rel = set(e["answer_session_ids"])
        if not (rel & relset):
            continue
        n += 1
        got = _dedup(ranked_by_q.get(e["question_id"], []))
        row = {"q": e["question_id"]}
        if e.get("category") is not None:
            row["category"] = e["category"]
        for k in KS:
            in_k = bool(rel & set(got[:k]))
            row[f"h{k}"] = int(in_k)
            if in_k:
                hit[k] += 1
        #: MRR is taken over the SAME depth for every arm, and it was not. R@k truncates at k
        #: just above; this loop walked the whole list, so the metric quietly depended on how
        #: many candidates an arm happened to return. And they differ: `Memory.search` in mem0
        #: 2.0.19 has no `limit` parameter at all - it is `top_k`, default 20 - so `limit=max(KS)`
        #: below went into `**kwargs` and vanished, and Mem0 answered with twenty where our arm
        #: and LangMem answered with ten. A hit at rank 11-20 then earned Mem0 up to 1/11 of a
        #: point that the other arms could not earn at any quality, because they were never
        #: allowed to look that deep. Found by the auditing session, in a stand that is ours.
        #:
        #: Truncating here is the fix that does not depend on remembering it again: whatever an
        #: arm returns, the metric reads max(KS) of it, and an arm that returns more gains
        #: nothing from the surplus. It changes no recorded number of ours - measured over the
        #: three committed artifacts, our arm has ZERO hits past rank 10 out of 500, 1977 and
        #: 500 questions, which is what an arm returning exactly ten must have.
        for i, s in enumerate(got[:max(KS)]):
            if s in rel:
                mrr += 1.0 / (i + 1)
                row["rr"] = round(1.0 / (i + 1), 4)
                break
        else:
            row["rr"] = 0.0
        per_q.append(row)
    out = {f"recall@{k}": round(hit[k] / n, 3) if n else 0.0 for k in KS}
    #: `mrr@10`, not `mrr`, because the loop below reads max(KS) of the list and no further.
    #: The truncation is required - without it an arm returning twenty candidates earns
    #: rank-11-to-20 credit that an arm returning ten cannot - but it changes WHAT the number
    #: is, and a field called `mrr` next to a published MRR from a paper invites a comparison
    #: between a full ranked list and the first ten of one. The value changed definition; the
    #: name had to change with it. Named from max(KS) rather than written as "mrr@10", so the
    #: label cannot drift from the depth it describes.
    out[f"mrr@{max(KS)}"] = round(mrr / n, 4) if n else 0.0
    out["n"] = n
    #: P1 (part 3.5). Without these rows the arms can only be compared unpaired, and unpaired this
    #: design resolves 4.4 points while the interesting differences are one - "behind Mem0 at R@5"
    #: was 8 questions of 1,977 inside an interval three points wide. The rows cost nothing: the
    #: loop above already computed every one of them, and they are what makes a McNemar possible.
    #: They are written beside the aggregates, never instead of them; the aggregates above are
    #: untouched arithmetic.
    out["per_question"] = per_q
    return out


# ── Nevertwice (re-scored through the same score(), apples-to-apples) ───────────

def run_nevertwice(data, pool) -> dict:
    """Rank with the shipped production ranker (calibrated score fusion of semantic bge-m3 +
    BM25), scored by the SAME score() on the SAME subset as every competitor. Uses the embed
    cache; falls back to the committed result file only if the cache is absent."""
    # LoCoMo keeps its own cache, keyed by dia_id and by question TEXT rather than by a
    # question_id the dataset does not carry. Same vectors, same embedder, different keys.
    locomo = getattr(ARGS, "data", "oracle") == "locomo"
    if locomo:
        import locomo_eval as lc                                 # noqa: PLC0415
        emb = lc.EMB
        if not emb.exists():
            return {"blocked": "no LoCoMo embed cache - run `python research/locomo_eval.py --embed`"}
        cache = json.loads(emb.read_text(encoding="utf-8"))
        svec = cache["turns"]
        qvec_by_text = cache["questions"]
        pool_ids = [s for s in pool if s in svec]
        toks_lists = {s: m._token_list(pool[s]) for s in pool_ids}
        bm_tf, bm_dl, bm_df, bm_avgdl = le.build_bm25(pool_ids, toks_lists)
        t0 = time.time()
        ranked = {}
        for e in data:
            q = qvec_by_text.get(e["question"])
            if q is None:
                continue
            qt = m._tokens(e["question"])
            cos = {s: m.cosine(q, svec[s]) for s in pool_ids}
            bm = le.bm25_scores(qt, pool_ids, bm_tf, bm_dl, bm_df, bm_avgdl)
            cal = le.calibrated(cos, bm)
            ranked[e["question_id"]] = sorted(cal, key=lambda s: (-cal[s], s))[:max(KS)]
        sc = score(ranked, data, list(pool))
        sc["query_s"] = round(time.time() - t0, 1)
        sc["setup"] = "local bge-m3 via Ollama (calibrated fusion; 0 deps, no server, no DB)"
        return sc

    emb = le._emb_path()
    if not emb.exists():
        # No fallback to an older result file: that path once answered with the withdrawn
        # July numbers under a fresh timestamp. A stand measures or it blocks.
        return {"blocked": f"no embed cache at {emb.name} - run `python research/longmem_eval.py "
                           f"--embed` (add --data=s for the standard pool)"}
    cache = json.loads(emb.read_text(encoding="utf-8"))
    if not le.cache_ok(cache):
        return {"blocked": f"{emb.name} was built under a different embedding cap or model - "
                           f"run `python research/longmem_eval.py --embed`"}
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
    sc["embed_chars"] = le.MAXCHARS
    sc["setup"] = "local bge-m3 via Ollama (calibrated fusion; 0 deps, no server, no DB)"
    return sc


def _bench_dir() -> Path:
    return Path(os.environ.get("H2H_DATA") or (Path(tempfile.gettempdir()) / "nevertwice_h2h"))


# ── Mem0 (LOCAL: Ollama LLM + bge-m3 embedder + embedded qdrant) ──────────────

def run_mem0(data, pool, infer=None) -> dict:
    try:
        from mem0 import Memory
    except ImportError:
        return {"blocked": "mem0 not installed - `pip install mem0ai ollama`"}
    infer = ARGS.mem0_infer if infer is None else infer
    bench_dir = _bench_dir()
    store = bench_dir / ("qdrant_mem0_infer" if infer else "qdrant_mem0")
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
    items = _ingestable(pool.items())
    if ARGS.sessions:
        items = items[:ARGS.sessions]
    stats: dict = {}
    silent = 0
    try:
        t0 = time.time()
        for sid, txt in items:
            res = _shrinking(lambda t, sid=sid: mem.add(t, user_id="lme",
                                                        metadata={"session_id": sid}, infer=infer),
                             txt, stats)
            written = res.get("results", res) if isinstance(res, dict) else res
            if infer and not written:
                silent += 1            # its extractor failed or extracted nothing: no memory
        ingest_s = time.time() - t0
        named_or_raise(mem.search, "top_k", "filters")
        t1 = time.time()
        ranked = {}
        for e in data:
            #: `top_k`, not `limit`: mem0 2.0.19 takes `top_k` (default 20) and swallows
            #: every other keyword in `**kwargs` without a word. `limit=max(KS)` was a
            #: sentence, not an argument - there was no value of it at which this line
            #: would have behaved differently.
            r = mem.search(e["question"], filters={"user_id": "lme"}, top_k=max(KS))
            res = r.get("results", r) if isinstance(r, dict) else r
            ranked[e["question_id"]] = [(x.get("metadata") or {}).get("session_id") for x in res]
        query_s = time.time() - t1
    except Exception as e:
        return {"blocked": f"Mem0 run failed ({type(e).__name__}: {e})"}
    if infer and items and silent > 0.1 * len(items):
        return {"blocked": f"Mem0 pipeline: {silent} of {len(items)} sessions yielded no memory "
                           f"(its extractor's response could not be parsed, or it extracted "
                           f"nothing) - not a measurement of Mem0", "silent_extractions": silent}
    sc = score(ranked, data, list(pool))
    if infer:
        sc["silent_extractions"] = silent
    sc["ingest_s"] = round(ingest_s, 1)
    sc["query_s"] = round(query_s, 1)
    sc["mode"] = f"infer={infer} ({'LLM ' + COMP_LLM if infer else 'retrieval-only, 1 memory/session'})"
    sc["sessions_shrunk"] = stats.get("shrunk", 0)
    sc["embedder"] = f"ollama {EMBED_MODEL}"
    # Mem0's search is hybrid by default once fastembed is present: dense cosine plus its
    # BM25 sparse vector (Qdrant/bm25), scored additively; the spaCy entity boost is off
    # because spaCy is not installed. Recorded so the row is read as what it is.
    try:
        import fastembed                                       # noqa: F401
        sc["search"] = "dense cosine + fastembed BM25 (Mem0 default hybrid); entity boost off"
    except ImportError:
        sc["search"] = "dense cosine only (fastembed absent)"
    sc["setup"] = "pip install mem0ai ollama fastembed; embedded qdrant (no server)"
    return sc


def run_mem0_infer(data, pool) -> dict:
    """Mem0 with its LLM extraction on - the product as shipped, not only its store."""
    return run_mem0(data, pool, infer=True)


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
        items = _ingestable(pool.items())
        if ARGS.sessions:
            items = items[:ARGS.sessions]
        stats: dict = {}
        t0 = time.time()
        for sid, txt in items:
            _shrinking(lambda t, sid=sid: store.put(("lme",), sid, {"text": t}), txt, stats)
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
    sc["mode"] = "store search only: LangGraph InMemoryStore, no memory manager, no LLM"
    sc["sessions_shrunk"] = stats.get("shrunk", 0)
    sc["setup"] = "pip install langgraph langmem langchain-ollama (no server)"
    return sc


def run_langmem_full(data, pool) -> dict:
    """LangMem's own pipeline: `create_memory_store_manager` extracts memories with an LLM
    into a LangGraph store, one namespace per item, and the store is searched with the
    same embedder as everyone else. The namespace maps a hit back to its session."""
    try:
        from langmem import create_memory_store_manager
        from langgraph.store.memory import InMemoryStore
        from langchain_ollama import ChatOllama, OllamaEmbeddings
    except ImportError as e:
        return {"blocked": f"langmem pipeline needs langmem, langgraph, langchain-ollama ({e})"}
    try:
        emb = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE)
        store = InMemoryStore(index={"embed": emb, "dims": 1024, "fields": ["$"]})
        llm = ChatOllama(model=COMP_LLM, base_url=OLLAMA_BASE, temperature=0.0,
                         num_ctx=COMP_NUM_CTX)
        manager = create_memory_store_manager(llm, store=store, enable_inserts=True,
                                              enable_deletes=False,
                                              namespace=("memories", "{langgraph_user_id}"))
        items = list(pool.items())
        if ARGS.sessions:
            items = items[:ARGS.sessions]
        errors = 0
        t0 = time.time()
        for sid, txt in items:
            try:
                manager.invoke({"messages": [{"role": "user", "content": txt}]},
                               config={"configurable": {"langgraph_user_id": sid}})
            except Exception as e:                            # noqa: BLE001 - counted, reported
                errors += 1
                if errors <= 3:
                    print(f"  langmem_full: {sid[:12]} {type(e).__name__}: {str(e)[:120]}")
        ingest_s = time.time() - t0
        n_mem = sum(1 for _ in store.search(("memories",), limit=10 ** 6))
        t1 = time.time()
        ranked = {}
        for e in data:
            res = store.search(("memories",), query=e["question"], limit=max(KS) * 4)
            sids = []
            for it in res:
                ns = tuple(it.namespace)
                if len(ns) >= 2 and ns[1] not in sids:
                    sids.append(ns[1])
            ranked[e["question_id"]] = sids[:max(KS)]
        query_s = time.time() - t1
    except Exception as e:
        return {"blocked": f"LangMem pipeline failed ({type(e).__name__}: {e})"}
    if items and errors > 0.1 * len(items):
        return {"blocked": f"LangMem pipeline: {errors} of {len(items)} items failed extraction",
                "errors": errors}
    sc = score(ranked, data, list(pool))
    sc["ingest_s"] = round(ingest_s, 1)
    sc["query_s"] = round(query_s, 1)
    sc["embedder"] = f"ollama {EMBED_MODEL}"
    sc["extraction_errors"] = errors
    sc["memories_written"] = n_mem
    sc["mode"] = (f"full pipeline: create_memory_store_manager with {COMP_LLM} "
                  f"(num_ctx {COMP_NUM_CTX}); store search over the extracted memories")
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
        items = _ingestable(pool.items())
        if ARGS.sessions:
            items = items[:ARGS.sessions]
        stats: dict = {}
        t0 = time.time()
        B = 64
        for i in range(0, len(items), B):
            chunk = items[i:i + B]
            col.add(ids=[s for s, _ in chunk],
                    embeddings=[_shrinking(embed, t, stats) for _, t in chunk],
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
    sc["mode"] = "store search only: chromadb cosine, no LLM note construction, no link evolution"
    sc["sessions_shrunk"] = stats.get("shrunk", 0)
    sc["setup"] = "pip install chromadb (A-MEM's vector store) + Ollama embeddings"
    return sc


class _OllamaChromaEF:
    """A chromadb embedding function over the same Ollama endpoint every other arm uses.

    A-MEM constructs its retriever with `SentenceTransformerEmbeddingFunction(model_name)`,
    which would load bge-m3 through sentence-transformers - the same weights on a different
    runtime. The stand's fairness premise is one embedder for everyone, so the retriever is
    handed this instead. It implements the pieces of chroma's EmbeddingFunction protocol a
    persistent collection asks for.
    """

    def __init__(self, model_name: str = ""):
        self.model_name = model_name or EMBED_MODEL

    shrunk = 0

    def __call__(self, input):                                 # noqa: A002 - chroma's name
        import urllib.request

        def one(text):
            req = urllib.request.Request(
                f"{OLLAMA_BASE}/api/embed",
                data=json.dumps({"model": EMBED_MODEL, "input": text}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read())["embeddings"][0]

        out = []
        stats: dict = {}
        for text in input:
            out.append(_shrinking(one, str(text)[:MAXCHARS], stats))
        _OllamaChromaEF.shrunk += stats.get("shrunk", 0)
        return out

    def embed_query(self, input):                              # noqa: A002 - chroma's name
        """chroma embeds a search differently from a document when the function says so; this
        one does not, but the method must exist. A-MEM's full-pipeline arm scored zero on
        2026-09-06 because it did not: chroma's own base class defaults `embed_query` to
        `__call__`, and this shim is a protocol implementation rather than a subclass."""
        return self(input)

    @staticmethod
    def name() -> str:
        return "nevertwice_ollama_ef"

    def get_config(self) -> dict:
        return {"model_name": self.model_name}

    @staticmethod
    def build_from_config(config: dict):
        return _OllamaChromaEF(config.get("model_name", ""))

    def default_space(self) -> str:
        return "cosine"

    def supported_spaces(self) -> list:
        return ["cosine", "l2", "ip"]

    @staticmethod
    def validate_config(config: dict) -> None:
        return None

    def validate_config_update(self, old_config: dict, new_config: dict) -> None:
        return None


def run_amem_full(data, pool) -> dict:
    """A-MEM's own pipeline (`agentic_memory`, the authors' package): every item becomes a
    note through its LLM analysis (keywords, context, tags), is linked and possibly evolved
    against its nearest neighbours by a second LLM call, and is searched through its own
    `search_agentic`. The embedder is swapped for the stand's shared Ollama one; the LLM is
    the same local model the other full-pipeline arms use."""
    try:
        import agentic_memory.retrievers as amr
        from agentic_memory.memory_system import AgenticMemorySystem   # __init__ exports nothing
    except ImportError as e:
        return {"blocked": f"a-mem not importable ({e}) - `pip install a-mem` in its own venv "
                           "(it pins litellm, sentence-transformers, nltk)"}
    import shutil
    store = _bench_dir() / "chroma_amem_full"
    shutil.rmtree(store, ignore_errors=True)
    try:
        amr.SentenceTransformerEmbeddingFunction = _OllamaChromaEF   # same embedder for everyone
        sysm = AgenticMemorySystem(model_name=EMBED_MODEL, llm_backend="ollama",
                                   llm_model=COMP_LLM, storage_path=str(store),
                                   evo_threshold=100)
        items = list(pool.items())
        if ARGS.sessions:
            items = items[:ARGS.sessions]
        note_to_sid, silent = {}, 0
        t0 = time.time()
        for sid, txt in items:
            nid = sysm.add_note(txt[:MAXCHARS])
            note_to_sid[nid] = sid
            note = sysm.read(nid)
            # A-MEM's Ollama controller swallows a failed LLM call and returns an empty
            # analysis, which would quietly turn this arm into the store arm. Count it.
            if note is not None and not note.keywords and note.context == "General":
                silent += 1
        ingest_s = time.time() - t0
        t1 = time.time()
        ranked = {}
        for e in data:
            hits = sysm.search_agentic(e["question"], k=max(KS) * 2)
            sids = []
            for h in hits:
                sid = note_to_sid.get(h.get("id"))
                if sid and sid not in sids:
                    sids.append(sid)
            ranked[e["question_id"]] = sids[:max(KS)]
        query_s = time.time() - t1
    except Exception as e:
        return {"blocked": f"A-MEM pipeline failed ({type(e).__name__}: {e})"}
    if items and silent > 0.1 * len(items):
        return {"blocked": f"A-MEM pipeline: {silent} of {len(items)} notes got no LLM analysis "
                           f"(the controller hides failures) - not a measurement of A-MEM",
                "silent_analyses": silent}
    sc = score(ranked, data, list(pool))
    sc["ingest_s"] = round(ingest_s, 1)
    sc["query_s"] = round(query_s, 1)
    sc["embedder"] = f"ollama {EMBED_MODEL} (chroma embedding function over the shared endpoint)"
    sc["silent_analyses"] = silent
    sc["mode"] = (f"full pipeline: agentic_memory with {COMP_LLM} via litellm (Ollama's own "
                  f"context default); search_agentic over its notes")
    sc["setup"] = "pip install a-mem (litellm, chromadb, sentence-transformers); Ollama LLM + embeddings"
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


ADAPTERS = {"nevertwice": run_nevertwice, "mem0": run_mem0, "mem0_infer": run_mem0_infer,
            "langmem": run_langmem, "langmem_full": run_langmem_full,
            "amem": run_amem, "amem_full": run_amem_full, "cognee": run_cognee, "zep": run_zep}


def _locomo():
    """LoCoMo as (questions, pool), pooled globally over all ten conversations.

    Global rather than per-conversation: the competitor stores hold one collection, and a
    per-conversation setting would mean ten stores per system measuring ten easy tasks. The
    global pool is the harder question and the one a user's real history looks like. Our own
    per-conversation numbers are in `research/locomo_eval.py`, which is LoCoMo's own setting.
    """
    import locomo_eval as lc                                     # noqa: PLC0415

    convs = lc.load()
    pool, data = {}, []
    for c in convs:
        pool.update(c["pool"])
        for i, q in enumerate(c["qa"]):
            data.append({"question_id": f"{c['sample_id']}#{i}", "question": q["question"],
                         "answer_session_ids": q["evidence"]})
    return data, pool


def main():
    # The corpus is verified against its committed hash before anything is ingested. The July
    # run of this stand was withdrawn because the file behind it could not be identified after
    # the fact; a run that cannot name its bytes is a run that produces nothing.
    corpus = "locomo10" if ARGS.data == "locomo" else le.CORPUS
    try:
        provenance = corpus_pin.record(corpus)
    except (corpus_pin.CorpusMismatch, KeyError) as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    if ARGS.data == "locomo":
        data, pool = _locomo()
        if ARGS.limit:
            data = data[:ARGS.limit]
    else:
        data = json.loads(le.ORACLE.read_text(encoding="utf-8"))
        if ARGS.limit:
            data = data[:ARGS.limit]
        pool = _pool_from(data)
    want = [s.strip() for s in ARGS.only.split(",") if s.strip()] or ["nevertwice", "mem0"]

    bar = "=" * 80
    print(bar)
    print(f"  HEAD-TO-HEAD - {corpus}, {len(pool)} items / {len(data)} questions")
    print(f"  corpus sha256 {provenance['sha256'][:16]}... ({provenance['licence']})")
    print(f"  same metric as longmem_eval.py · competitors on LOCAL Ollama ({EMBED_MODEL})")
    print(bar)

    out_path = Path(ARGS.out) if getattr(ARGS, "out", "") else (HERE / "head_to_head.json")
    results = {"_provenance": provenance, "_questions": len(data), "_pool_sessions": len(pool)}
    for name in want:
        fn = ADAPTERS.get(name)
        if not fn:
            print(f"\n- {name} - unknown system (have: {', '.join(ADAPTERS)})")
            continue
        print(f"\n- {name} -", flush=True)
        t0 = time.time()
        r = fn(data, pool)
        r["_wall_s"] = round(time.time() - t0, 1)
        r["version"] = _pkg_ver(name)          # record what we actually compared against
        r["label"] = ARM_LABEL.get(name, name)
        r["measured_at"] = _measured_at()
        if name == "nevertwice":
            r["morphology"] = bool(m.LEXICAL_MORPHOLOGY)
        r = accept(name, r)
        r = accept(name, r)
        results[name] = r
        if "blocked" in r:
            print(f"  BLOCKED: {r['blocked']}")
        else:
            print("  " + "  ".join(f"{k} {r[k]}" for k in
                  ("recall@1", "recall@3", "recall@5", "recall@10", f"mrr@{max(KS)}", "n") if k in r))
            extra = {k: r[k] for k in ("ingest_s", "query_s", "mode", "setup", "version") if k in r}
            if extra:
                print("  " + "  ".join(f"{k}={v}" for k, v in extra.items()))

    # honest verdict
    print("\n- VERDICT -")
    ranked = {k: v for k, v in results.items()
              if not k.startswith("_") and isinstance(v, dict) and "recall@5" in v}
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
    print(f"  NB: same embedder ({EMBED_MODEL}) and the same endpoint for everyone, every arm")
    print(f"  embedding each item whole (cap {le.MAXCHARS:,} chars) → this isolates the MEMORY")
    print("  pipeline, not the embedder. Store arms are labelled as store arms; the *_full arms")
    print("  run each product's own LLM pipeline. Nevertwice uses calibrated score fusion; its")
    print("  opt-in trained cross-encoder stacks further on top of this first stage.")
    print(bar)

    if ARGS.save:
        # Merge into whatever the file holds NOW: only the arms this process ran are replaced,
        # so a run that started hours ago cannot overwrite a row another process wrote since.
        merged = {}
        if out_path.exists():
            try:
                merged = json.loads(out_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                merged = {}
        merged.update(results)
        out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        print(f"  saved → {out_path}")


if __name__ == "__main__":
    main()
