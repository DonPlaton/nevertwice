#!/usr/bin/env python3
"""Accuracy per token: a local reader and a local judge over every system's context (ledger I3).

Every vendor publishes an answer-accuracy number; this repository has published retrieval
recall and, once, an answer accuracy on our own pipeline alone. The frontier puts every system
on one stand: the same questions, the same reader, the same judge, and for each system the
context it would hand the agent at k = 1, 3, 5 - with the tokens that context costs, counted
by the model that read it (`prompt_eval_count`), not estimated. The result is a curve per
system, accuracy against injected tokens, bracketed by a no-memory reader (question only) and
the oracle ceiling (the gold evidence sessions, whole).

Three stages, each cached so a run is resumable and a re-score is instant:

    python research/frontier_eval.py contexts --arm nevertwice_whole      # ranks, dumps top-10 texts
    python research/frontier_eval.py contexts --arm mem0                  # in the mem0 venv
    python research/frontier_eval.py answer   --arms nevertwice_whole,mem0 --stratify 25
    python research/frontier_eval.py judge    --arms ...  --save

Arms. `nevertwice_whole`: the shipped ranker, the top-k sessions whole. `nevertwice_snippet`:
the same ranking, each session cut to the query-focused passage the cross-encoder reads (the
token-economy point a hook actually injects). `nevertwice_full`: our extractor writes notes
from every session with the same local model the competitor pipelines use, then `api.recall`
over the notes. `mem0`: Mem0's store search over whole sessions (infer=False). `mem0_infer`
and `amem_full`: the products' own pipelines, read back from the stores their head-to-head
run left on disk - their memories are what they would inject. `langmem_full` keeps its
memories in process and is recorded as a blocker here. `none` and `oracle` are the brackets.

Stated once, everywhere the number goes: the reader and the judge are named in the artifact,
the judge's own disagreement with a second judge on a hundred answers is measured beside the
accuracies, and a difference between two systems smaller than that disagreement is not a
difference (ledger I3).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402 - one store sandbox for the whole repo

sandbox_guard.isolate(prefix="nevertwice_frontier_")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402
import longmem_eval as le  # noqa: E402
import qa_eval as qa  # noqa: E402 - the reader / judge prompts, the same rubric as the QA study

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                  # noqa: BLE001
    pass

DATA = HERE / "data"
OUT = ROOT / "research" / "results" / "frontier.json"
OLLAMA = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
READER = os.environ.get("FRONTIER_READER", "qwen2.5:7b")
JUDGE = os.environ.get("FRONTIER_JUDGE", "qwen3.6:27b")
JUDGE2 = os.environ.get("FRONTIER_JUDGE2", "qwen3:32b")
EXTRACTOR = os.environ.get("H2H_LLM", "qwen2.5-7b-64k:latest")   # the pipeline arms' model
NUM_CTX = int(os.environ.get("FRONTIER_NUM_CTX", "32768"))
KS = (1, 3, 5)
CHAR_BUDGET = int(os.environ.get("FRONTIER_CHAR_BUDGET", "90000"))   # ~22k tokens at k=5, whole
ARMS = ("nevertwice_whole", "nevertwice_snippet", "nevertwice_full", "mem0", "mem0_infer",
        "amem_full", "langmem_full")
BRACKETS = ("none", "oracle")


def _ctx_path(arm: str) -> Path:
    return DATA / f"frontier_contexts_{arm}_cache.json"          # *_cache*.json: never committed


def _cache_path(stage: str) -> Path:
    return DATA / f"frontier_{stage}_cache.json"


def _akey(reader: str, arm: str, k: int, qid: str, context: str) -> str:
    """The cache key of one answer, ending in a digest of the context it was answered from.

    Without the digest, a cached answer outlives the ranker that produced its context: change
    the fusion weight, re-run the contexts stage, and every answer is served from the cache as
    if nothing had moved. The stage that changed is the stage that must re-run, and the only
    way the cache can know is to key on the bytes the reader actually saw."""
    return f"{reader}|{arm}|{k}|{qid}|{hashlib.blake2s(context.encode('utf-8'), digest_size=6).hexdigest()}"


def _find_akey(answers: dict, reader: str, arm: str, k: int, qid: str) -> str | None:
    """The one answer key for this (reader, arm, k, question), whatever context produced it.
    `answer_stage` drops the previous digest when it writes a new one, so at most one survives;
    a key from before the digest existed has four fields and is deliberately not found."""
    prefix = f"{reader}|{arm}|{k}|{qid}|"
    for key in answers:
        if key.startswith(prefix):
            return key
    return None


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def _save(p: Path, d: dict) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(max(0.0, (c - r) / d), 4), round(min(1.0, (c + r) / d), 4)


# ── the corpus ────────────────────────────────────────────────────────────────

def corpus():
    data, pool = le.load()
    return data, pool


def stratified(data: list, per_type: int) -> list:
    """`per_type` questions of each LongMemEval type, in corpus order - a balanced sample."""
    if not per_type:
        return data
    seen: collections.Counter = collections.Counter()
    out = []
    for e in data:
        t = e.get("question_type", "?")
        if seen[t] < per_type:
            out.append(e)
            seen[t] += 1
    return out


# ── stage 1: contexts per arm ─────────────────────────────────────────────────

def _capture_rankings(run_fn, data, pool) -> dict:
    """Run a head-to-head adapter and keep the per-question ranking it hands to `score`.

    The adapters compute a ranking and pass it to the module-level `score`; this wraps that
    function for the duration of one run and records the ranking, so the frontier reads the
    exact lists the head-to-head table was scored on rather than a second implementation.
    """
    import head_to_head as hh                                    # noqa: PLC0415
    captured: dict = {}
    original = hh.score

    def recording(ranked_by_q, d, pool_ids):
        captured.update(ranked_by_q)
        return original(ranked_by_q, d, pool_ids)

    hh.score = recording
    try:
        res = run_fn(data, pool)
    finally:
        hh.score = original
    if res.get("blocked"):
        raise RuntimeError(res["blocked"])
    return captured


def contexts_nevertwice(data, pool, snippet: bool) -> dict:
    import head_to_head as hh                                    # noqa: PLC0415
    ranked = _capture_rankings(hh.run_nevertwice, data, pool)
    out = {}
    for e in data:
        qid = e["question_id"]
        sids = [s for s in ranked.get(qid, []) if s in pool][:10]
        if snippet:
            qt = m._tokens(e["question"])
            items = [{"id": s, "text": le._passage(qt, pool[s], le.XRERANK_SNIP)} for s in sids]
        else:
            items = [{"id": s, "text": pool[s]} for s in sids]
        out[qid] = items
    return out


def contexts_mem0_store(data, pool) -> dict:
    import head_to_head as hh                                    # noqa: PLC0415
    ranked = _capture_rankings(lambda d, p: hh.run_mem0(d, p, infer=False), data, pool)
    return {e["question_id"]: [{"id": s, "text": pool[s]} for s in ranked.get(e["question_id"], []) if s in pool][:10]
            for e in data}


def contexts_mem0_infer(data, pool) -> dict:
    """Read back the memories Mem0's pipeline run left in its on-disk store (head_to_head.py,
    `--only=mem0_infer`): what it would inject, no re-ingest."""
    import head_to_head as hh                                    # noqa: PLC0415
    from mem0 import Memory                                      # noqa: PLC0415
    store = hh._bench_dir() / "qdrant_mem0_infer"
    if not store.exists():
        raise RuntimeError(f"no Mem0 pipeline store at {store} - run head_to_head.py --only=mem0_infer first")
    cfg = {"llm": {"provider": "ollama", "config": {"model": EXTRACTOR, "ollama_base_url": OLLAMA,
                                                     "temperature": 0.0}},
           "embedder": {"provider": "ollama", "config": {"model": hh.EMBED_MODEL, "ollama_base_url": OLLAMA,
                                                          "embedding_dims": 1024}},
           "vector_store": {"provider": "qdrant", "config": {"path": str(store), "embedding_model_dims": 1024,
                                                             "on_disk": True}}}
    mem = Memory.from_config(cfg)
    out = {}
    for e in data:
        r = mem.search(e["question"], filters={"user_id": "lme"}, limit=10)
        res = r.get("results", r) if isinstance(r, dict) else r
        out[e["question_id"]] = [{"id": (x.get("metadata") or {}).get("session_id") or x.get("id"),
                                  "text": x.get("memory") or x.get("text") or ""} for x in res]
    return out


def contexts_amem_full(data, pool) -> dict:
    """A-MEM's notes from the chroma store its pipeline run left on disk."""
    import head_to_head as hh                                    # noqa: PLC0415
    import agentic_memory.retrievers as amr                      # noqa: PLC0415
    from agentic_memory.memory_system import AgenticMemorySystem  # noqa: PLC0415
    store = hh._bench_dir() / "chroma_amem_full"
    if not store.exists():
        raise RuntimeError(f"no A-MEM store at {store} - run head_to_head.py --only=amem_full first")
    amr.SentenceTransformerEmbeddingFunction = hh._OllamaChromaEF
    sysm = AgenticMemorySystem(model_name=hh.EMBED_MODEL, llm_backend="ollama", llm_model=EXTRACTOR,
                               storage_path=str(store), evo_threshold=100)
    out = {}
    for e in data:
        hits = sysm.search_agentic(e["question"], k=10)
        out[e["question_id"]] = [{"id": h.get("id"), "text": h.get("content") or ""} for h in hits]
    return out


def contexts_nevertwice_full(data, pool) -> dict:
    """Our extractor over every session with the pipeline arms' model, then `api.recall`.

    The context of a hit is what `api.recall` hands a caller: title, description, prevention
    and - since J1 - the verbatim evidence lines the note was aligned to. The ingest cache is
    keyed to the sandbox store it was built in: the store is a fresh temporary directory per
    process, so a cache that outlived its store would mark every session done and rank over
    nothing. Each session's entry is the number of notes it produced, which is what lets the
    stand publish the extractor's silence - the questions whose gold sessions yielded no note."""
    os.environ["NEVERTWICE_CLOUD"] = "none"
    os.environ["NEVERTWICE_MODEL"] = EXTRACTOR
    # The engine binds its model name at import, and this module imported it at the top - so the
    # environment variable alone left the arm on whatever NEVERTWICE_MODEL the shell exported
    # (the campaign of 2026-09-10 ran 940 sessions through qwen3.6:35b-a3b, which declared every
    # chat session off-topic and wrote two notes). Bind it explicitly and record what ran.
    m.OLLAMA_MODEL = EXTRACTOR
    from nevertwice import api                                   # noqa: PLC0415
    if api.m.OLLAMA_MODEL != EXTRACTOR:
        raise RuntimeError(f"extractor bound to {api.m.OLLAMA_MODEL!r}, wanted {EXTRACTOR!r}")
    project = "lme"
    cache_p = DATA / "frontier_full_ingest_cache.json"
    done = _load(cache_p)
    store = str(sandbox_guard.store())
    if done.get("_store") != store:
        if done:
            print(f"  ingest cache belongs to another store ({done.get('_store')}); starting over", flush=True)
        done = {"_store": store}
    t0 = time.time()
    for i, (sid, txt) in enumerate(pool.items()):
        if not txt.strip() or sid in done:
            continue
        try:
            r = api.capture_session(txt, project=project, session_id=sid, trigger="ingest")
            done[sid] = int(r.get("patterns", 0)) + int(r.get("mistakes", 0)) + int(r.get("decisions", 0))
        except Exception as e:                                   # noqa: BLE001 - counted
            done[sid] = f"error: {type(e).__name__}"
        if (i + 1) % 25 == 0:
            _save(cache_p, done)
            print(f"  ingested {i + 1}/{len(pool)}  ({time.time() - t0:.0f}s)", flush=True)
    _save(cache_p, done)
    sessions = {k: v for k, v in done.items() if not k.startswith("_")}
    errors = sum(1 for v in sessions.values() if isinstance(v, str))
    silent = sum(1 for v in sessions.values() if v == 0)
    gold_silent = sum(1 for e in data
                      if all(sessions.get(s, 0) == 0 or isinstance(sessions.get(s), str)
                             for s in e["answer_session_ids"]))
    ev = getattr(api, "_evidence", None)
    out = {"_ingest": {"sessions": len(pool), "errors": errors, "llm": api.m.OLLAMA_MODEL,
                       "llm_calls_per_session": 1, "sessions_with_zero_notes": silent,
                       "questions_gold_without_notes": gold_silent, "questions": len(data),
                       "evidence": dict(ev.STATS) if ev is not None else None,
                       "evidence_enabled": bool(ev.enabled()) if ev is not None else False}}
    for e in data:
        hits = api.recall(e["question"], project=project, k=10)
        out[e["question_id"]] = [{"id": h.get("stem"), "text": _hit_text(h)} for h in hits]
    return out


def _hit_text(h: dict) -> str:
    """A recall hit as the agent would read it: the lesson, then the quoted evidence lines."""
    parts = [str(h.get(f) or "") for f in ("title", "description", "prevention")]
    parts += [str(s) for s in (h.get("evidence") or [])]
    return " ".join(p for p in parts if p)


CONTEXT_FNS = {
    "nevertwice_whole": lambda d, p: contexts_nevertwice(d, p, snippet=False),
    "nevertwice_snippet": lambda d, p: contexts_nevertwice(d, p, snippet=True),
    "nevertwice_full": contexts_nevertwice_full,
    "mem0": contexts_mem0_store,
    "mem0_infer": contexts_mem0_infer,
    "amem_full": contexts_amem_full,
}


# ── stage 2: the reader ───────────────────────────────────────────────────────

def ollama_chat(model: str, prompt: str, timeout: int = 600) -> dict:
    """One chat call; returns {content, prompt_tokens, eval_tokens} or {} on failure."""
    body = json.dumps({"model": model, "stream": False, "format": "json",
                       "messages": [{"role": "user", "content": prompt}],
                       "options": {"num_ctx": NUM_CTX, "temperature": 0}}).encode("utf-8")
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
    except Exception as e:                                       # noqa: BLE001 - a stand reports
        print(f"  [ollama] {model}: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return {}
    return {"content": ((d.get("message") or {}).get("content") or "").strip(),
            "prompt_tokens": int(d.get("prompt_eval_count") or 0),
            "eval_tokens": int(d.get("eval_count") or 0)}


def _context_text(items: list[dict], k: int, budget: int) -> str:
    parts, used = [], 0
    for it in items[:k]:
        t = (it.get("text") or "").strip()
        if not t:
            continue
        room = budget - used
        if room <= 0:
            break
        t = t[:room]
        parts.append(t)
        used += len(t) + 6
    return "\n\n---\n\n".join(parts)


def answer_stage(arms: list[str], data: list, pool: dict, reader: str, budget: int) -> dict:
    cache = _load(_cache_path("answers"))
    changed = 0
    for arm in list(arms) + list(BRACKETS):
        ctx = {} if arm in BRACKETS else _load(_ctx_path(arm))
        if arm not in BRACKETS and not ctx:
            print(f"- {arm}: no contexts (run the contexts stage in the right venv) - skipped")
            continue
        ks = (0,) if arm == "none" else ((99,) if arm == "oracle" else KS)
        for k in ks:
            n_done = 0
            for e in data:
                qid = e["question_id"]
                if arm == "none":
                    context = "(no memory available)"
                elif arm == "oracle":
                    context = _context_text([{"id": s, "text": pool.get(s, "")} for s in e["answer_session_ids"]],
                                            99, budget)
                else:
                    context = _context_text(ctx.get(qid, []), k, budget)
                key = _akey(reader, arm, k, qid, context)
                if key in cache:
                    continue
                r = ollama_chat(reader, qa.ANSWER_PROMPT.format(context=context, question=e["question"]))
                if not r:
                    continue
                try:
                    ans = json.loads(m._strip_json_fence(r["content"])).get("answer", "")
                except (ValueError, AttributeError):
                    ans = r["content"][:400]
                stale = _find_akey(cache, reader, arm, k, qid)
                if stale:                # the context moved; its answer is not this run's answer
                    del cache[stale]
                cache[key] = {"answer": str(ans)[:600], "prompt_tokens": r["prompt_tokens"],
                              "context_chars": len(context)}
                changed += 1
                n_done += 1
                if changed % 20 == 0:
                    _save(_cache_path("answers"), cache)
            print(f"- {arm} k={k}: {n_done} new answers", flush=True)
    _save(_cache_path("answers"), cache)
    return cache


# ── stage 3: the judge, and the artifact ──────────────────────────────────────

def judge_one(judge: str, e: dict, pred: str) -> bool | None:
    r = ollama_chat(judge, qa.JUDGE_PROMPT.format(question=e["question"], gold=e["answer"], pred=pred))
    if not r:
        return None
    try:
        v = json.loads(m._strip_json_fence(r["content"])).get("correct")
        return bool(v) if v is not None else None
    except (ValueError, AttributeError):
        low = r["content"].lower()
        return True if '"correct": true' in low or "correct: true" in low else (False if "false" in low else None)


def judge_stage(arms: list[str], data: list, reader: str, judge: str, judge2: str, agree_n: int) -> dict:
    answers = _load(_cache_path("answers"))
    verdicts = _load(_cache_path("verdicts"))
    by_qid = {e["question_id"]: e for e in data}
    changed = 0
    for arm in list(arms) + list(BRACKETS):
        ks = (0,) if arm == "none" else ((99,) if arm == "oracle" else KS)
        for k in ks:
            for e in data:
                akey = _find_akey(answers, reader, arm, k, e["question_id"])
                vkey = f"{judge}|{akey}"
                if akey is None or vkey in verdicts:
                    continue
                v = judge_one(judge, e, answers[akey]["answer"])
                if v is None:
                    continue
                verdicts[vkey] = v
                changed += 1
                if changed % 25 == 0:
                    _save(_cache_path("verdicts"), verdicts)
    # judge agreement: a second judge on the first `agree_n` judged answers of the shipped arm
    pairs = [(k, v) for k, v in verdicts.items() if k.startswith(f"{judge}|{reader}|nevertwice_whole|5|")][:agree_n]
    for vkey, v in pairs:
        akey = vkey.split("|", 1)[1]
        v2key = f"{judge2}|{akey}"
        if v2key in verdicts or akey not in answers:   # its answer was re-read from a new context
            continue
        qid = akey.split("|")[3]                       # reader|arm|k|qid|context-digest
        v2 = judge_one(judge2, by_qid[qid], answers[akey]["answer"])
        if v2 is not None:
            verdicts[v2key] = v2
    _save(_cache_path("verdicts"), verdicts)
    return verdicts


def summarise(arms: list[str], data: list, reader: str, judge: str, judge2: str) -> dict:
    answers = _load(_cache_path("answers"))
    verdicts = _load(_cache_path("verdicts"))
    qids = [e["question_id"] for e in data]
    out = {"reader": READER, "judge": judge, "second_judge": judge2, "num_ctx": NUM_CTX,
           "char_budget": CHAR_BUDGET, "questions": len(qids), "extractor": EXTRACTOR,
           "corpus": "longmemeval_oracle", "arms": {}, "brackets": {}}

    def point(arm: str, k: int) -> dict | None:
        vs, toks = [], []
        for qid in qids:
            akey = _find_akey(answers, reader, arm, k, qid)
            vkey = f"{judge}|{akey}"
            if akey is not None and vkey in verdicts:
                vs.append(1 if verdicts[vkey] else 0)
                toks.append(answers[akey]["prompt_tokens"])
        if not vs:
            return None
        n = len(vs)
        acc = sum(vs) / n
        toks_sorted = sorted(toks)
        return {"n": n, "accuracy": round(acc, 4), "ci": wilson(sum(vs), n),
                "mean_prompt_tokens": round(sum(toks) / n, 1),
                "median_prompt_tokens": toks_sorted[n // 2]}

    for arm in arms:
        pts = {str(k): point(arm, k) for k in KS}
        if any(pts.values()):
            out["arms"][arm] = pts
    for arm, k in (("none", 0), ("oracle", 99)):
        pt = point(arm, k)
        if pt:
            out["brackets"][arm] = pt
    # agreement between the two judges on the shipped arm's k=5 answers
    agree = tot = 0
    for qid in qids:
        akey = _find_akey(answers, reader, "nevertwice_whole", 5, qid)
        if akey is None:
            continue
        v1, v2 = verdicts.get(f"{judge}|{akey}"), verdicts.get(f"{judge2}|{akey}")
        if v1 is not None and v2 is not None:
            tot += 1
            agree += int(v1 == v2)
    out["judge_agreement"] = {"n": tot, "rate": round(agree / tot, 4) if tot else None,
                              "disagreement": round(1 - agree / tot, 4) if tot else None}
    # the extractor's silence ceiling (J1): questions whose gold sessions produced no note at
    # all - no span can lift those, and the number is published beside the arm's accuracy
    ingest = (_load(_ctx_path("nevertwice_full")) or {}).get("_ingest") or {}
    if ingest.get("questions"):
        q = int(ingest["questions"])
        out["extractor_silence"] = {
            "questions_gold_without_notes": int(ingest.get("questions_gold_without_notes", 0)),
            "questions": q,
            "fraction": round(int(ingest.get("questions_gold_without_notes", 0)) / q, 4),
            "sessions_with_zero_notes": int(ingest.get("sessions_with_zero_notes", 0)),
            "sessions": int(ingest.get("sessions", 0)),
            "evidence_enabled": bool(ingest.get("evidence_enabled")),
            "evidence": ingest.get("evidence"),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stage", choices=["contexts", "answer", "judge", "summary"])
    ap.add_argument("--arm", default="", help="contexts: one arm to rank and dump")
    ap.add_argument("--arms", default="nevertwice_whole,nevertwice_snippet,mem0")
    ap.add_argument("--stratify", type=int, default=25, help="questions per LongMemEval type (0 = all)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--agree-n", type=int, default=100)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    data, pool = corpus()
    if args.stratify:
        data = stratified(data, args.stratify)
    if args.limit:
        data = data[:args.limit]
        # a smoke run ingests and ranks only the sessions its questions can ask about; the full
        # run keeps the whole pool, the same store composition the competitor pipelines saw
        wanted = {s for e in data for s in e.get("haystack_session_ids", [])}
        pool = {sid: txt for sid, txt in pool.items() if sid in wanted}
    arms = [a for a in args.arms.split(",") if a]
    print(f"frontier: {len(data)} questions, reader {READER}, judge {JUDGE}, stage {args.stage}")

    if args.stage == "contexts":
        fn = CONTEXT_FNS.get(args.arm)
        if fn is None:
            print(f"unknown arm {args.arm!r}; have {', '.join(CONTEXT_FNS)} (langmem_full is a blocker: "
                  f"its memories live in process and are not retained after the head-to-head run)")
            return 2
        ctx = fn(data, pool)
        _save(_ctx_path(args.arm), ctx)
        n_items = sum(len(v) for k, v in ctx.items() if not k.startswith("_"))
        print(f"  {args.arm}: contexts for {len([k for k in ctx if not k.startswith('_')])} questions, "
              f"{n_items} items -> {_ctx_path(args.arm).name}")
        return 0
    if args.stage == "answer":
        answer_stage(arms, data, pool, READER, CHAR_BUDGET)
        return 0
    if args.stage == "judge":
        judge_stage(arms, data, READER, JUDGE, JUDGE2, args.agree_n)
    res = summarise(arms, data, READER, JUDGE, JUDGE2)
    for arm, pts in res["arms"].items():
        for k, pt in pts.items():
            if pt:
                print(f"  {arm:20s} k={k}  acc {pt['accuracy']:.3f} {pt['ci']}  tokens {pt['mean_prompt_tokens']:.0f}  n {pt['n']}")
    for b, pt in res["brackets"].items():
        print(f"  {b:20s}      acc {pt['accuracy']:.3f} {pt['ci']}  tokens {pt['mean_prompt_tokens']:.0f}")
    print(f"  judge agreement: {res['judge_agreement']}")
    if args.save:
        Path(args.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(f"  saved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
