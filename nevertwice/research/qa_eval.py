#!/usr/bin/env python3
"""RESEARCH — end-to-end QA-accuracy on LongMemEval-oracle (answer-correctness).

Companion to `longmem_eval.py` (which reports retrieval recall@k). This adds the
*answer* axis: retrieve -> read -> answer -> LLM-judge against the gold answer — the
same metric vendors quote as "X% on LongMemEval" (e.g. memanto's 89.8%). We report it
on the comparable axis and stay honest about two things vendors usually elide:

  1. The JUDGE model (a weak judge inflates the score). Default is local Ollama
     (`qwen3:30b-a3b`, no key, fully reproducible); a configured cloud backend is
     used automatically if present (same router as production). The judge is stamped
     into the results so the number is never quoted naked.
  2. The retrieval SETTING:
       * oracle    — context = the gold evidence sessions (`answer_session_ids`):
                     the answer/reasoning CEILING given perfect retrieval.
       * retrieved — context = our shipped hybrid ranker's top-k over the 940-session
                     global pool: the FULL pipeline, the number comparable to vendors.
     The gap between the two is exactly how much retrieval (not the LLM) costs us.

    python research/qa_eval.py --limit=25                   # quick pilot (both settings)
    python research/qa_eval.py --setting=retrieved --save   # full pipeline number -> qa_results.json
    python research/qa_eval.py --setting=both --k=5 --save   # both settings, save

Answers + verdicts are cached to `data/qa_cache.json` keyed by (setting, model, qid),
so a run is resumable and re-scoring is instant. Needs the embedding cache from
`longmem_eval.py --embed` for the `retrieved` setting (the `oracle` setting needs no
embeddings). Research dep: none beyond the package (reuses longmem_eval + memory_hook).
"""
import collections
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import memory_hook as m          # noqa: E402
import longmem_eval as le        # noqa: E402  (BM25 + calibrated fusion, identical to production)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DATA = HERE / "data"
QTYPES_ORDER = ("single-session-user", "single-session-assistant", "single-session-preference",
                "multi-session", "temporal-reasoning", "knowledge-update")

# CLI flags are read only as a script — importing this module (e.g. from a test) must
# NOT pick up the importer's sys.argv (the longmem_eval audit caught exactly this).
_ARGV = sys.argv if __name__ == "__main__" else []
LIMIT = next((int(a.split("=", 1)[1]) for a in _ARGV if a.startswith("--limit=")), None)
K = int(next((a.split("=", 1)[1] for a in _ARGV if a.startswith("--k=")), "5"))
SETTING = next((a.split("=", 1)[1] for a in _ARGV if a.startswith("--setting=")), "both")
CHAR_BUDGET = int(next((a.split("=", 1)[1] for a in _ARGV if a.startswith("--budget=")), "16000"))
SAVE = "--save" in _ARGV
# Separate cache file per backend so a cloud run and a local run can churn concurrently
# without racing on one file (each writes its own; keys also carry the model name).
CACHE = DATA / next((a.split("=", 1)[1] for a in _ARGV if a.startswith("--cache=")), "qa_cache.json")
OUTNAME = next((a.split("=", 1)[1] for a in _ARGV if a.startswith("--out=")), "qa_results.json")
# --stratify=N: N questions PER type (the 6 LongMemEval types), a balanced sample for a fast
# strong-model calibration vs the temporal-heavy first-N that --limit would grab.
STRATIFY = int(next((a.split("=", 1)[1] for a in _ARGV if a.startswith("--stratify=")), "0"))
# --cloud-paced: call the cloud backend DIRECTLY (no local fallback, so the number stays a
# pure cloud-judge number) with per-call breaker reset + backoff, to ride a free-tier TPM cap.
CLOUD_PACED = "--cloud-paced" in _ARGV
PACE = float(next((a.split("=", 1)[1] for a in _ARGV if a.startswith("--pace=")), "0"))
# --concurrency=N: run N questions' answer→judge pipelines in parallel (cloud judges with no
# rate cap finish ~Nx faster). Default 1 = sequential, unchanged for local Ollama and tests.
CONCURRENCY = int(next((a.split("=", 1)[1] for a in _ARGV if a.startswith("--concurrency=")), "1"))
# --cot: allow a brief chain-of-thought before the final answer (helps temporal/multi-session).
COT = "--cot" in _ARGV
# --reasoner: ANSWER with deepseek-reasoner (R1-class native reasoning) to probe the reader
# ceiling; the JUDGE is held at deepseek-chat (set NEVERTWICE_DEEPSEEK_MODEL=deepseek-chat) so the
# only variable vs the 0.748 run is the reader. Reasoner needs a bespoke call (no json-mode/temp).
REASONER = "--reasoner" in _ARGV
REASONER_MODEL = next((a.split("=", 1)[1] for a in _ARGV if a.startswith("--reasoner-model=")),
                      "deepseek-reasoner")
# --xrerank: in the retrieved setting, re-order the calibrated-fusion top-N with the trained
# cross-encoder (bge-reranker-v2-m3) before taking top-k — the product lever the k=10 negative
# result pointed at (ranking precision, not recall depth). Needs torch+transformers (opt-in).
XRERANK = "--xrerank" in _ARGV
XRERANK_N = int(next((a.split("=", 1)[1] for a in _ARGV if a.startswith("--xrerank-n=")), "30"))

# Prompts use str.format → literal JSON braces are doubled. Kept deliberately plain so a
# small local model follows them; the judge rubric mirrors LongMemEval's "key fact" grading.
ANSWER_PROMPT = """You answer a question using ONLY the CONTEXT below, which are excerpts from a user's past chat sessions with an assistant. Answer the QUESTION concisely and factually from the context. If the context lacks the answer, give your single best one-sentence guess from what is present. Reply ONLY as JSON: {{"answer": "<your concise answer>"}}.

CONTEXT:
{context}

QUESTION: {question}
"""

JUDGE_PROMPT = """You grade one answer from a memory-QA system. Decide whether the MODEL ANSWER conveys the same key fact as the REFERENCE ANSWER to the QUESTION. Correct = it states or paraphrases the reference's key information, even with different wording or extra detail. Incorrect = it contradicts, omits, or hedges away that key information. Reply ONLY as JSON: {{"correct": true}} or {{"correct": false}}.

QUESTION: {question}
REFERENCE ANSWER: {gold}
MODEL ANSWER: {pred}
"""

# --cot variant: let the model reason before committing to the final answer. The judge still
# scores only `answer`, but the scratch space lifts the date-arithmetic / cross-session
# categories — which is how a memory is used in practice (the agent reasons over what it recalled).
ANSWER_PROMPT_COT = """You answer a question using ONLY the CONTEXT below, which are excerpts from a user's past chat sessions with an assistant. Reason step by step over the relevant facts and any dates, then give the final concise answer. If the context lacks the answer, give your single best one-sentence guess from what is present. Reply ONLY as JSON: {{"reasoning": "<brief step-by-step>", "answer": "<your concise final answer>"}}.

CONTEXT:
{context}

QUESTION: {question}
"""


def make_deepseek_reasoner(model="deepseek-reasoner", timeout=240, retries=3):
    """A reader callable for deepseek-reasoner (R1-class). Unlike chat models it does NOT
    accept `response_format=json_object` or `temperature`, and reasons natively (the answer
    lands in `content`, the scratch work in `reasoning_content`). So we send a plain request,
    then strip a code fence and json.loads the content; if that fails we wrap the raw text as
    the answer rather than discard a real response. Returns {} only on a true backend failure
    (so the harness's down-backend guard still fires). Reads DEEPSEEK_API_KEY from the env."""
    import os
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set — needed for --reasoner")

    def _call(prompt):
        body = json.dumps({"model": model,
                           "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {key}", "User-Agent": m._UA}
        for attempt in range(retries):
            try:
                req = urllib.request.Request(m.DEEPSEEK_URL, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data = json.loads(r.read())
                txt = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
                txt = (txt or "").strip()
                if not txt:
                    raise ValueError("empty content")
                try:
                    parsed = json.loads(m._strip_json_fence(txt))
                    return parsed if isinstance(parsed, dict) else {"answer": str(parsed)}
                except (ValueError, json.JSONDecodeError):
                    # reasoner sometimes returns the bare answer (or judgement) as prose:
                    low = txt.lower()
                    if '"correct"' in low or "correct" in low[:40]:
                        return {"correct": ("true" in low or "correct: true" in low
                                            or '"correct": true' in low)}
                    return {"answer": txt[:600]}        # keep the real answer, don't drop it
            except Exception:
                if attempt < retries - 1:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                return {}
        return {}
    return _call


def _load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(c: dict) -> None:
    DATA.mkdir(exist_ok=True)
    tmp = CACHE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(c), encoding="utf-8")
    tmp.replace(CACHE)             # atomic on same volume (Windows + POSIX)


def _context(sids, pool, budget) -> str:
    """Concatenate the chosen sessions as the answer model's context, within `budget`
    chars total (so the prompt fits a 16k-ctx local model). Raw session text in original
    turn order, an equal char slice each — an answer model wants coherent context, not the
    high-overlap line cherry-picking a *reranker* wants (that excerpting lives in
    longmem_eval._passage and is deliberately NOT used here)."""
    sids = [s for s in sids if s in pool]
    if not sids:
        return ""
    per = max(1200, budget // len(sids))
    return "\n\n---\n\n".join(pool[s][:per] for s in sids)[:budget]


def _rank_topk(qvec_row, pool_ids, svec, bm, qtokens):
    """The shipped hybrid ranker (calibrated score fusion) at session level — identical
    to longmem_eval's `hybrid`. Returns pool ids best-first."""
    cos = {s: m.cosine(qvec_row, svec[s]) for s in pool_ids}
    bmv = le.bm25_scores(qtokens, pool_ids, *bm)
    cal = le.calibrated(cos, bmv)
    return sorted(cal, key=lambda s: (-cal[s], s))


def evaluate(data, pool, settings, k, budget, llm, *, svec=None, qvec=None,
             pool_ids=None, bm=None, cache=None, model="local", checkpoint=None,
             progress=None, concurrency=1, cot=False, judge_llm=None, rerank_fn=None):
    """Core loop, LLM injected for testability. `llm(prompt) -> dict` is the production
    router (memory_hook.generate_json); a {} return means the backend is down, which
    invalidates the metric, so we raise rather than silently score those wrong.
    `concurrency` > 1 runs the per-question (answer→judge) pipeline across a thread pool —
    questions are independent, so a cloud judge with no rate cap finishes ~Nx faster; the
    default sequential path is byte-identical and is what the tests and local Ollama use.
    A distinct `judge_llm` holds the grader constant while the answerer changes (the
    --reasoner probe: reader=reasoner, judge=chat); it defaults to `llm` so the tests and
    every same-model run are unchanged. Returns (results, cache)."""
    cache = {} if cache is None else cache
    judge_llm = judge_llm or llm

    def _build(setting, e):
        """(qtype, sids, ck) for one question; None to skip (retrieved w/o a query vector)."""
        qid, qtype = e["question_id"], e.get("question_type", "?")
        if setting == "oracle":
            sids = list(e["answer_session_ids"])
        else:
            if not qvec or qid not in qvec:
                return None                                      # never embedded — skip, don't fake
            ranked = _rank_topk(qvec[qid], pool_ids, svec, bm, m._tokens(e["question"]))
            sids = rerank_fn(e["question"], ranked, k) if rerank_fn else ranked[:k]
        # config-aware key: budget/k/cot/rerank change → fresh answer, so re-runs don't reuse stale ctx
        tag = ("cot" if cot else "plain") + ("+xr" if rerank_fn else "")
        return qtype, sids, f"{setting}:{model}:b{budget}:k{k}:{tag}:{qid}"

    def _answer_and_judge(e, sids):
        ctx = _context(sids, pool, budget)
        ares = llm((ANSWER_PROMPT_COT if cot else ANSWER_PROMPT).format(context=ctx, question=e["question"]))
        if not ares:
            raise RuntimeError("LLM backend returned nothing on an ANSWER call — is the "
                               "backend up / a key set? (prior answers are cached)")
        pred = (ares.get("answer") or "") if isinstance(ares, dict) else ""
        jres = judge_llm(JUDGE_PROMPT.format(question=e["question"], gold=e["answer"], pred=pred))
        if not jres:
            raise RuntimeError("LLM backend returned nothing on a JUDGE call.")
        return {"answer": pred, "correct": bool(jres.get("correct"))}

    def _safe(it):
        """Run one item; return (ck, verdict) or (ck, None) on a transient failure so a
        single dead call never aborts a 500-question run — failures are retried, then
        excluded from the metric with a logged count rather than crashing or faking a score."""
        try:
            return it[3], _answer_and_judge(it[0], it[2])
        except RuntimeError:
            return it[3], None

    results = {}
    for setting in settings:
        items = [(e, *built) for e in data if (built := _build(setting, e))]   # (e, qtype, sids, ck)
        todo = [it for it in items if it[3] not in cache]
        t0, done = time.time(), 0
        # two passes: full concurrency, then a slower retry of whatever transiently failed.
        for rnd, (workers, batch) in enumerate(((concurrency, todo),
                                                (max(1, concurrency // 4), None))):
            batch = [it for it in todo if it[3] not in cache] if batch is None else batch
            if not batch:
                continue
            if workers > 1:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    for fut in as_completed([ex.submit(_safe, it) for it in batch]):
                        ck, verdict = fut.result()
                        if verdict is not None:
                            cache[ck] = verdict
                        done += 1
                        if checkpoint and done % 20 == 0:
                            checkpoint(cache)
                            if progress:
                                progress(setting, done, time.time() - t0)
            else:
                for it in batch:
                    ck, verdict = _safe(it)
                    if verdict is not None:
                        cache[ck] = verdict
                    done += 1
                    if checkpoint and done % 10 == 0:
                        checkpoint(cache)
                        if progress:
                            progress(setting, done, time.time() - t0)
        if checkpoint:
            checkpoint(cache)
        missing = [it for it in items if it[3] not in cache]
        if missing and len(missing) == len(todo) and todo:
            # nothing at all got answered → the backend is down, not flaky: fail loudly
            # (preserves the "no key / Ollama down" contract) rather than report 0/0.
            raise RuntimeError("LLM backend returned nothing for every question — is the "
                               "backend up / a key set? (prior answers are cached)")
        if missing:
            print(f"  [{setting}] WARNING: {len(missing)}/{len(items)} questions had no "
                  f"backend response after retries — EXCLUDED from accuracy (not scored wrong).",
                  file=sys.stderr)
        cc, tt = collections.Counter(), collections.Counter()   # correct / total per type
        for e, qtype, sids, ck in items:
            if ck in cache:
                tt[qtype] += 1
                if cache[ck]["correct"]:
                    cc[qtype] += 1
        n = sum(tt.values())
        results[setting] = {
            "accuracy": sum(cc.values()) / n if n else 0.0,
            "n": n,
            "by_type": {t: {"acc": cc[t] / tt[t], "n": tt[t]}
                        for t in sorted(tt, key=lambda x: (QTYPES_ORDER.index(x)
                                        if x in QTYPES_ORDER else 99, x))},
        }
    return results, cache


def _model_name() -> str:
    if m.cloud_key() and m.ACTIVE_CLOUD != "none":
        return f"{m.ACTIVE_CLOUD}:{getattr(m, '_CLOUD_MODELS', {}).get(m.ACTIVE_CLOUD, '?')}"
    return m.OLLAMA_MODEL


def run():
    data, pool = le.load()                       # full 940-session pool (le.LIMIT is None on import)
    if LIMIT:
        data = data[:LIMIT]                      # answer only the first N questions; pool stays full
    if STRATIFY:                                 # N per type — a balanced sample, pool still full
        bytype = {}
        for e in data:
            bytype.setdefault(e.get("question_type", "?"), []).append(e)
        data = [e for t in sorted(bytype) for e in bytype[t][:STRATIFY]]
    settings = ["oracle", "retrieved"] if SETTING == "both" else [SETTING]
    svec = qvec = pool_ids = bm = None
    if "retrieved" in settings:
        if not le.EMB.exists():
            print("No embeddings cache — run: python research/longmem_eval.py --embed", file=sys.stderr)
            sys.exit(1)
        emb = json.loads(le.EMB.read_text(encoding="utf-8"))
        svec, qvec = emb["sessions"], emb["questions"]
        pool_ids = [s for s in pool if s in svec]
        bm = le.build_bm25(pool_ids, {s: m._token_list(pool[s]) for s in pool_ids})

    model = _model_name()
    backend = m.llm_backend_desc()
    print(f"[qa] backend: {backend}", file=sys.stderr)
    print(f"[qa] settings={settings} k={K} budget={CHAR_BUDGET} questions={len(data)} "
          f"judge={model}", file=sys.stderr)

    cache = _load_cache()

    def _progress(setting, made, dt):
        print(f"  [{setting}] {made} new answers ({dt:.0f}s, "
              f"{dt/max(1,made)*1000:.0f} ms/q)", file=sys.stderr)

    def _paced_cloud(prompt):
        # pure cloud (NO local fallback, so the number stays a clean cloud-judge number) with a
        # per-call breaker reset + backoff, to ride a free-tier tokens-per-minute cap.
        for attempt in range(4):
            m._CLOUD_DEAD = False
            if PACE:
                time.sleep(PACE)
            res = m.call_cloud(prompt)
            if res:
                return res
            time.sleep(2.0 * (attempt + 1))
        return {}

    rerank_fn = None
    if XRERANK:
        try:
            from . import reranker_ce as rc
        except ImportError:
            import reranker_ce as rc
        if not rc.available():
            print("--xrerank needs torch+transformers (pip install nevertwice-memory[reranker])",
                  file=sys.stderr)
            sys.exit(1)
        print(f"[qa] xrerank ON — {rc.MODEL}, fusion top-{XRERANK_N} → cross-encoder → top-{K}",
              file=sys.stderr)

        def rerank_fn(question, ranked, k):
            cand = ranked[:XRERANK_N]
            if len(cand) <= 1:
                return cand[:k]
            qtok = m._tokens(question)
            passages = [le._passage(qtok, pool[s], 1200) for s in cand]
            scores = rc.rerank_scores(question, passages)
            if not scores or len(scores) != len(cand):
                return cand[:k]                       # degrade to fusion order on any mismatch
            order = sorted(range(len(cand)), key=lambda i: -scores[i])[:k]
            return [cand[i] for i in order]

    judge_llm = None
    if REASONER:
        # reader = deepseek-reasoner (probe the ceiling); judge = the configured chat backend,
        # held constant vs the 0.748 run so the only change is the reader. Stamp the reader into
        # `model` (and thus the cache key) so reasoner answers never collide with chat answers.
        answer_llm = make_deepseek_reasoner(REASONER_MODEL)
        judge_llm = _paced_cloud if CLOUD_PACED else m.generate_json
        model = f"deepseek:{REASONER_MODEL}"
        print(f"[qa] reader={model}  judge={_model_name()} (held constant)", file=sys.stderr)
    else:
        answer_llm = _paced_cloud if CLOUD_PACED else m.generate_json

    results, cache = evaluate(
        data, pool, settings, K, CHAR_BUDGET, answer_llm,
        svec=svec, qvec=qvec, pool_ids=pool_ids, bm=bm, cache=cache, model=model,
        checkpoint=_save_cache, progress=_progress, concurrency=CONCURRENCY, cot=COT,
        judge_llm=judge_llm, rerank_fn=rerank_fn)

    bar = "=" * 74
    print(bar)
    print("  LongMemEval-oracle — END-TO-END QA ACCURACY (answer-correctness)")
    print(f"  judge={model}   embedder={m.EMBED_MODEL}   k={K}   budget={CHAR_BUDGET} chars")
    print(bar)
    for setting in settings:
        r = results[setting]
        tag = ("perfect-retrieval CEILING" if setting == "oracle"
               else f"FULL pipeline (our hybrid top-{K})")
        print(f"\n  [{setting}]  {tag}")
        print(f"    overall accuracy: {r['accuracy']:.3f}   (n={r['n']})")
        for t, d in r["by_type"].items():
            print(f"      {t:30} {d['acc']:.3f}  (n={d['n']})")
    if "oracle" in results and "retrieved" in results:
        gap = results["oracle"]["accuracy"] - results["retrieved"]["accuracy"]
        print(f"\n  → retrieval cost (oracle − retrieved): {gap:+.3f} "
              f"(how much answer-accuracy our retrieval, not the LLM, leaves on the table)")
    print(bar)

    if SAVE:
        summary = {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "dataset": "LongMemEval-oracle (global pool, 940 sessions / 500 questions)",
            "embedder": m.EMBED_MODEL, "answer_model": model, "judge_model": model,
            "backend": backend, "k": K, "char_budget": CHAR_BUDGET, "cot": COT,
            "reader_model": model, "reasoner": REASONER,
            "metric": "answer-accuracy (LLM-judge vs gold answer) — comparable to vendor "
                      "'X% on LongMemEval' headlines",
            "settings": results,
        }
        out = HERE / OUTNAME
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  saved → {out}")
    return results


if __name__ == "__main__":
    run()
