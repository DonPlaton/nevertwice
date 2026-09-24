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
import datetime
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
import _provenance as prov  # noqa: E402 - measured_at: {commit, utc, dirty} on the final artifact
import qa_eval as qa  # noqa: E402 - the reader / judge prompts, the same rubric as the QA study
import _ollama_pacer as pacer  # noqa: E402 - item 9B/P0(a): pace/retry/count this stand's own traffic

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
#: The arms whose contexts read OUR engine - `contexts_nevertwice`/`contexts_nevertwice_full`.
#: The competitor arms (mem0*, amem_full, langmem_full) read a competitor's own store or
#: pipeline and never touch `nevertwice/`, so a commit here cannot restamp what they measured.
ENGINE_ARMS = ("nevertwice_whole", "nevertwice_snippet", "nevertwice_full")


# ── F6: the restamp trap ────────────────────────────────────────────────────────
#
# With complete answer/verdict caches, `judge --save` and `summary` make ZERO model calls -
# every key the loop looks for is already there - and `tools/remeasure.py` decides an artifact
# is fresh by reading ITS OWN mtime, not the code that produced the numbers inside it. Running
# either stage today re-writes `frontier.json` with today's timestamp, over numbers an ENGINE
# arm's cache produced on an OLD commit - a restamp, not a measurement. The guard: every cached
# `contexts` entry for an engine arm records the commit it was produced at
# (`stamp_engine_commit`), and `judge`/`summary`/`--save` refuse when that commit is not
# provably still current (`check_engine_freshness`) - named after `tools/produced_by.py`'s own
# question, "has the code moved since this number was measured?", asked at read time instead of
# left to a manifest's freshness check that never sees this file's cache at all.

def git_head() -> str:
    import subprocess                                              # noqa: PLC0415
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True,
                              text=True, timeout=10, check=True).stdout.strip()
    except (OSError, ValueError) as e:
        return f"?({type(e).__name__})"
    except Exception:                                              # noqa: BLE001
        return "?"


def git_diff_quiet(base: str, paths: list[str]) -> bool:
    """True iff nothing under `paths` differs between `base` and HEAD. Any git failure (a bad or
    unresolvable revision, no git on PATH) reads as "changed": this exists to refuse a stale
    restamp, and an unprovable "unchanged" must not pass as one."""
    import subprocess                                              # noqa: PLC0415
    if not base or not paths:
        return False
    try:
        res = subprocess.run(["git", "diff", "--quiet", base, "HEAD", "--", *paths],
                             cwd=str(ROOT), timeout=30, capture_output=True)
        return res.returncode == 0
    except (OSError, ValueError):
        return False


def engine_closure(entry_command: str) -> list[str]:
    """The repo files `entry_command` depends on (`tools/produced_by.py`, static AST walk - no
    GPU, no execution). `entry_command` is a full command string, e.g.
    "python research/frontier_eval.py" - the arguments after the entry file do not affect the
    import closure, only which file is the entry point."""
    sys.path.insert(0, str(ROOT / "tools"))
    import produced_by as pb                                       # noqa: PLC0415
    return pb.closure(entry_command)


def stamp_engine_commit(ctx: dict) -> dict:
    """Mark a `contexts` cache entry for an ENGINE arm with the commit it was produced at, so a
    later `judge`/`summary`/`--save` can tell a fresh cache from a restamped one. `_`-prefixed,
    the convention `_ingest`/`_store` already use for cache metadata that is not a question id."""
    ctx["_engine_commit"] = git_head()
    return ctx


def check_engine_freshness(arms: list[str], engine_arms: tuple[str, ...], ctx_path_fn,
                           entry_command: str) -> list[str]:
    """Refusal messages for every requested arm in `engine_arms` whose cached `contexts` entry
    is stale relative to HEAD, or carries no recorded commit at all (a legacy cache from before
    this check existed - refused the same way, since staleness cannot be proven either way for
    it). Empty means every engine arm named in `arms` that HAS a cache is provably still
    current; an arm with no cache yet is not this function's job - the stage that reads it
    (`answer_stage`/`judge_stage`) already reports that absence on its own.

    `ctx_path_fn` and `entry_command` are the one seam this function is shared through
    (`code_sessions_eval.py` calls it with its own `_ctx_path` and its own entry command) -
    two research scripts must not each carry an independently-matched copy of "is this cache
    still current"."""
    wanted = [a for a in arms if a in engine_arms]
    if not wanted:
        return []
    head = git_head()
    closure = engine_closure(entry_command)
    rerun_prefix = f"{entry_command} contexts --arm"
    problems = []
    for arm in wanted:
        ctx = _load(ctx_path_fn(arm))
        if not ctx:
            continue
        recorded = ctx.get("_engine_commit")
        if not recorded:
            problems.append(f"{arm}: cached contexts carry no recorded engine commit (a legacy "
                            f"cache from before this check) - rerun: {rerun_prefix} {arm}")
            continue
        if recorded == head or git_diff_quiet(recorded, closure):
            continue
        problems.append(f"{arm}: cached contexts were produced at {recorded[:12]}, HEAD is "
                        f"{head[:12]}, and the engine has changed since - rerun: "
                        f"{rerun_prefix} {arm}")
    return problems


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
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8", newline="\n")
    os.replace(tmp, p)


# ── item 9B/P0(a): the pacer's own transport record, folded into a cache safely ────────

def _attach_prefixed_transport(cache: dict, key: str, since: dict) -> None:
    """`pacer.attach()`'s own fixed keys (`ollama_transport`, `valid`, `invalid_reason`) are
    never `_`-prefixed - writing them at a cache dict's own top level would corrupt a
    consumer that counts real entries by filtering `not k.startswith('_')` (this module's
    own contexts-stage printout) or crash one that assumes every value is a list/dict (a
    bare `False` under `cache["valid"]`, `len()`-ed by that same filter). Captured on a
    scratch dict and folded in, NESTED, under `cache[key]` (a single new `_`-prefixed key,
    e.g. `ctx["_transport"]`) instead - HANDOFF-PORTS gotcha 5, checked here rather than
    assumed. Writes nothing when the window paced zero calls and saw zero bypasses (mirrors
    `pacer.attach()`'s own "a clean run says nothing" rule)."""
    scratch: dict = {}
    pacer.attach(scratch, since=since)
    if scratch:
        cache[key] = scratch


def _longmem_vector_cache_provenance() -> dict:
    """P0(a) item 1, bullet 2: `nevertwice_whole`/`nevertwice_snippet` never call Ollama at the
    contexts stage themselves - `head_to_head.run_nevertwice` (via `_capture_rankings`) reads
    longmem's own pre-built VECTOR cache (`le._emb_path()`) and does cosine similarity over
    vectors already sitting in it. That cache's own validity (a failed embed, dropped
    sessions/questions - B1/K21, `.loop/HANDOFF-PORTS.md`) lives on THAT file, invisible to
    anything this stand's own `pacer.install()` can see and invisible to
    `tools/remeasure.row_refusal`, which only ever walks the RESULT artifact a claim's pointer
    resolves through (K16(2)/K25) - never a cache sitting beside it. Copied here, at contexts
    time, so `summarise()` can fold it into the arm the claim actually points at."""
    p = le._emb_path()
    if not p.exists():
        return {}
    try:
        cache = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: cache[k] for k in
           ("valid", "invalid_reason", "ollama_transport", "dropped_sessions", "dropped_questions")
           if k in cache}


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _stamp_current(stamp: dict | None, head: str, closure: list[str]) -> bool:
    """K30 (the auditor, 2026-09-24): a cached ANSWER or VERDICT is current only if its own
    recorded commit IS head, or nothing in the reader/judge closure has changed since it -
    reusing F6's own `git_diff_quiet`/closure idea, one level down from the contexts cache it
    was built for. Missing entirely (no `commit` recorded at all - a legacy answer/verdict
    from before this stamp existed, or the 09-09 competitor caches K30 exists to catch) counts
    as NOT current: an unprovable freshness is refused, never assumed."""
    if not stamp or not stamp.get("commit"):
        return False
    commit = stamp["commit"]
    return commit == head or git_diff_quiet(commit, closure)


def ctx_coverage_gap(ctx: dict, ids: list[str]) -> list[str]:
    """P0(f) item 2: a partial cache (contexts for only SOME of the requested questions -
    plausible for a competitor cache built before the question set grew, or one that only
    partly transferred) counts as a MISMATCH, not as present. `answer_stage` would still
    generate an answer from an EMPTY context for a question missing here (`ctx.get(qid, [])`
    defaults to `[]`, never a KeyError), which the n-vs-expected answer/verdict gate alone
    cannot see - every question still gets an answer, just a meaningless one from nothing.
    `ids` is every question id this arm is asked about (`_`-prefixed metadata keys in `ctx`
    are never question ids and are excluded on both sides)."""
    have = {k for k in ctx if not str(k).startswith("_")}
    return sorted(i for i in ids if i not in have)


def _fold_arm_context_provenance(arm: str, node: dict | None, ctx: dict, out: dict) -> None:
    """item 1: fold an arm's own contexts-stage transport (`ctx["_transport"]`, gotcha 5) and,
    for `nevertwice_whole`/`nevertwice_snippet`, the longmem vector-cache provenance
    (`ctx["_cache_provenance"]`) into the RESULT artifact - both land on the arm `node` a
    claim's pointer actually reads (K16(2)/K25), never left sitting on the contexts cache
    beside it where `tools/remeasure.row_refusal` cannot see them. A standalone, named
    function (not inlined into `summarise()`) so a test can monkeypatch it to a no-op and show
    the ctx's own `valid:false` silently fails to reach the arm without it."""
    transport = (ctx or {}).get("_transport")
    if transport:
        out.setdefault("contexts_transport", {})[arm] = transport
        if node is not None and transport.get("valid") is False:
            node["valid"] = False
            node["invalid_reason"] = "; ".join(
                r for r in (node.get("invalid_reason"), transport.get("invalid_reason")) if r)
    cache_prov = (ctx or {}).get("_cache_provenance")
    if cache_prov and node is not None:
        node["cache_provenance"] = cache_prov
        if cache_prov.get("valid") is False:
            node["valid"] = False
            node["invalid_reason"] = "; ".join(
                r for r in (node.get("invalid_reason"),
                           "longmem vector cache: " + (cache_prov.get("invalid_reason") or ""))
                if r)


def _ctx_file_provenance(path: Path) -> dict | None:
    """RN5 (item 3): for a competitor arm whose contexts cache may have been produced well
    before this run (P3: "the date of any cached competitor contexts ... are declared"), the
    file's own sha256, mtime (ISO, UTC) and key count - so "declared per P3" is something a
    reader can actually check against the file on disk, not a claim taken on faith."""
    if not path.exists():
        return None
    raw = path.read_bytes()
    try:
        d = json.loads(raw.decode("utf-8"))
        keys = len([k for k in d if not str(k).startswith("_")])
    except (OSError, ValueError):
        keys = None
    mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.timezone.utc).isoformat()
    return {"sha256": hashlib.sha256(raw).hexdigest(), "mtime": mtime, "keys": keys}


def _propagate_root_invalidity(out: dict) -> None:
    """K25 (the auditor's finding, carried over from item 9A's asof_bench fix): a claim can
    point at the artifact's ROOT or at a field OUTSIDE any one arm's own dict (judge_agreement,
    corpus_gates) - marking only the arm `"valid": false` is not enough, `row_refusal` would
    still resolve a claim on a different, otherwise-clean arm clean. Scans every arm (both the
    flat "blocked" shape and the keyed-by-k / keyed-by-type shape), every bracket, and any
    reason already set at the root (the stage-transport check in `summarise()` runs first) -
    if anything is invalid, the WHOLE artifact is marked invalid too, every reason joined."""
    reasons = []
    if out.get("invalid_reason"):
        reasons.append(out["invalid_reason"])
    for arm, node in out.get("arms", {}).items():
        if not isinstance(node, dict):
            continue
        if node.get("valid") is False:
            reasons.append(f"{arm}: {node.get('invalid_reason') or 'no reason recorded'}")
        for sub, pt in node.items():
            if isinstance(pt, dict) and pt.get("valid") is False:
                reasons.append(f"{arm}[{sub}]: {pt.get('invalid_reason') or 'no reason recorded'}")
    for name, pt in out.get("brackets", {}).items():
        if isinstance(pt, dict) and pt.get("valid") is False:
            reasons.append(f"{name}: {pt.get('invalid_reason') or 'no reason recorded'}")
    if reasons:
        out["valid"] = False
        out["invalid_reason"] = "; ".join(dict.fromkeys(reasons))


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
    data, pool, _empty_skipped = le.load()
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
    cache_prov = _longmem_vector_cache_provenance()
    if cache_prov:
        out["_cache_provenance"] = cache_prov
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
    hh.named_or_raise(mem.search, "top_k", "filters")
    for e in data:
        r = mem.search(e["question"], filters={"user_id": "lme"}, top_k=10)
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

    The context of a hit is what `api.recall` hands a caller: title, description and
    prevention. The ingest cache is
    keyed to the sandbox store it was built in: the store is a fresh temporary directory per
    process, so a cache that outlived its store would mark every session done and rank over
    nothing. Each session's entry is the number of notes it produced, which is what lets the
    stand publish the extractor's silence - the questions whose gold sessions yielded no note."""
    os.environ["NEVERTWICE_CLOUD"] = "none"
    os.environ["NEVERTWICE_MODEL"] = EXTRACTOR
    # The extractor samples: the engine's default is 0.2, right for the live hook and
    # wrong for a measurement. Pinning the MODEL and leaving the TEMPERATURE loose is
    # what `supersession_bench` did for a year, publishing a run-to-run spread it blamed
    # on the model (fixed 2026-09-22: at 0.2 every one of 80 cases served different text
    # between runs of one commit, at 0 exactly one did).
    os.environ["NEVERTWICE_EXTRACT_TEMP"] = "0"
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
    out = {"_ingest": {"sessions": len(pool), "errors": errors, "llm": api.m.OLLAMA_MODEL,
                       "llm_calls_per_session": 1, "sessions_with_zero_notes": silent,
                       "questions_gold_without_notes": gold_silent, "questions": len(data)}}
    for e in data:
        hits = api.recall(e["question"], project=project, k=10)
        out[e["question_id"]] = [{"id": h.get("stem"), "text": _hit_text(h)} for h in hits]
    return out


def _hit_text(h: dict) -> str:
    """A recall hit as the agent would read it: title, description, prevention."""
    parts = [str(h.get(f) or "") for f in ("title", "description", "prevention")]
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
    snap = pacer.snapshot()          # item 9B/P0(a): this stage's own reader traffic
    changed = 0
    blocked_now = []
    for arm in list(arms) + list(BRACKETS):
        ctx = {} if arm in BRACKETS else _load(_ctx_path(arm))
        if arm not in BRACKETS and not ctx:
            # P0(f)/K23: no bare skip - recorded into the answers cache (a `_`-prefixed key,
            # the only durable record a LATER `judge`/`summary` process, in its own separate
            # invocation, can read) so `summarise()` turns this into a named refusal in the
            # artifact (blocked + valid:false), never a vanished arm.
            print(f"- {arm}: no contexts (run the contexts stage in the right venv) - "
                  f"blocked, recorded as a named refusal in the artifact")
            blocked_now.append(arm)
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
                # K30 (the auditor, 2026-09-24): stamp every NEW answer with the commit and utc
                # it was produced at - `summarise()` refuses a point whose answers/verdicts are
                # not provably current with HEAD, the same idea F6 already applies to contexts.
                cache[key] = {"answer": str(ans)[:600], "prompt_tokens": r["prompt_tokens"],
                              "context_chars": len(context), "commit": git_head(), "utc": _utc_now_iso()}
                changed += 1
                n_done += 1
                if changed % 20 == 0:
                    _save(_cache_path("answers"), cache)
            print(f"- {arm} k={k}: {n_done} new answers", flush=True)
    if blocked_now:
        cache["_blocked_arms"] = sorted(set(cache.get("_blocked_arms") or []) | set(blocked_now))
    _attach_prefixed_transport(cache, "_transport", snap)
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


def _stamp_verdict(verdicts: dict, vkey: str) -> None:
    """K30: verdicts are plain bools (`verdicts[vkey] = True/False`), so a stamp cannot live on
    the value itself without changing a shape every existing reader assumes - a parallel
    `_`-prefixed meta map instead (HANDOFF-PORTS gotcha 5: `_meta` never matches a `judge|...`
    prefix, so every existing consumer that filters or iterates verdicts skips it unchanged)."""
    verdicts.setdefault("_meta", {})[vkey] = {"commit": git_head(), "utc": _utc_now_iso()}


def judge_stage(arms: list[str], data: list, reader: str, judge: str, judge2: str, agree_n: int) -> dict:
    answers = _load(_cache_path("answers"))
    verdicts = _load(_cache_path("verdicts"))
    snap = pacer.snapshot()          # item 9B/P0(a): this stage's own judge traffic
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
                _stamp_verdict(verdicts, vkey)
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
            _stamp_verdict(verdicts, v2key)
    _attach_prefixed_transport(verdicts, "_transport", snap)
    _save(_cache_path("verdicts"), verdicts)
    return verdicts


def summarise(arms: list[str], data: list, reader: str, judge: str, judge2: str) -> dict:
    answers = _load(_cache_path("answers"))
    verdicts = _load(_cache_path("verdicts"))
    qids = [e["question_id"] for e in data]
    n_expected = len(qids)
    #: K30: computed ONCE - the freshness check every point below runs against the SAME
    #: HEAD/closure, mirroring F6's own `check_engine_freshness` one level down (answers and
    #: verdicts, not contexts).
    head = git_head()
    closure = engine_closure("python research/frontier_eval.py")
    out = {"reader": READER, "judge": judge, "second_judge": judge2, "num_ctx": NUM_CTX,
           "char_budget": CHAR_BUDGET, "questions": len(qids), "extractor": EXTRACTOR,
           "corpus": "longmemeval_oracle", "arms": {}, "brackets": {}}

    def point(arm: str, k: int) -> dict | None:
        vs, toks, missing = [], [], []
        stale_answers, stale_verdicts = [], []
        for qid in qids:
            akey = _find_akey(answers, reader, arm, k, qid)
            vkey = f"{judge}|{akey}" if akey is not None else None
            if akey is not None and vkey in verdicts:
                vs.append(1 if verdicts[vkey] else 0)
                toks.append(answers[akey]["prompt_tokens"])
                # K30: an answer or a verdict found in cache but not stamped current with HEAD
                # is exactly what lets a run measure nothing while stamping the anchor.
                if not _stamp_current(answers[akey], head, closure):
                    stale_answers.append(qid)
                v_stamp = (verdicts.get("_meta") or {}).get(vkey)
                if not _stamp_current(v_stamp, head, closure):
                    stale_verdicts.append(qid)
            else:
                missing.append(qid)          # P0(f)/K23: no answer, or no verdict
        if not vs:
            return None
        n = len(vs)
        acc = sum(vs) / n
        toks_sorted = sorted(toks)
        pt = {"n": n, "accuracy": round(acc, 4), "ci": wilson(sum(vs), n),
              "mean_prompt_tokens": round(sum(toks) / n, 1),
              "median_prompt_tokens": toks_sorted[n // 2],
              "answers_stamp": {"current": n - len(stale_answers), "stale": len(stale_answers)},
              "verdicts_stamp": {"current": n - len(stale_verdicts), "stale": len(stale_verdicts)}}
        # P0(f) item 2: n compared against the number of questions the stand asks for this
        # point - previously a question with no answer or no verdict was silently left out.
        reasons = []
        if n != n_expected:
            pt["missing_count"] = len(missing)
            pt["missing_question_ids"] = missing
            reasons.append(f"{len(missing)} of {n_expected} question(s) have no answer or no "
                           f"verdict for arm {arm!r} k={k}")
        if stale_answers:
            pt["stale_answers_count"] = len(stale_answers)
            pt["stale_answer_ids"] = stale_answers
            reasons.append(f"K30: {len(stale_answers)} answer(s) are not stamped at a commit "
                           f"current with HEAD for arm {arm!r} k={k}: {stale_answers[:20]}")
        if stale_verdicts:
            pt["stale_verdicts_count"] = len(stale_verdicts)
            pt["stale_verdict_ids"] = stale_verdicts
            reasons.append(f"K30: {len(stale_verdicts)} verdict(s) are not stamped at a commit "
                           f"current with HEAD for arm {arm!r} k={k}: {stale_verdicts[:20]}")
        if reasons:
            pt["valid"] = False
            pt["invalid_reason"] = "; ".join(reasons)
        return pt

    #: P0(f)/K23: `answer_stage`'s own record of a requested arm it found no contexts for - the
    #: only durable trace a LATER `judge`/`summary` process (its own separate invocation) can
    #: read; `summarise()` never loaded a contexts cache of its own before this.
    blocked_arms = set(answers.get("_blocked_arms") or [])
    for arm in arms:
        if arm in blocked_arms:
            # a named refusal instead of a vanished arm.
            out["arms"][arm] = {
                "blocked": f"no contexts cached for requested arm {arm!r} - run: "
                           f"python research/frontier_eval.py contexts --arm {arm}",
                "valid": False,
                "invalid_reason": f"{arm}: requested arm has no cached contexts",
            }
            continue
        ctx = {} if arm in BRACKETS else _load(_ctx_path(arm))
        pts = {str(k): point(arm, k) for k in KS}
        if any(pts.values()):
            out["arms"][arm] = pts
        _fold_arm_context_provenance(arm, out["arms"].get(arm), ctx, out)
        gap = ctx_coverage_gap(ctx, qids)
        if gap:
            # P0(f) item 2: a partial cache (contexts for only some questions) is a mismatch,
            # not present - `answer_stage` still answers the missing ones from an empty
            # context, invisible to the n-vs-expected gate above.
            node = out["arms"].get(arm)
            if node is not None:
                node["valid"] = False
                node["missing_contexts_count"] = len(gap)
                node["missing_contexts_ids"] = gap
                node["invalid_reason"] = "; ".join(r for r in (
                    node.get("invalid_reason"),
                    f"{len(gap)} of {len(qids)} question(s) have no cached context at all for "
                    f"arm {arm!r} (a partial cache counts as a mismatch, not as present)") if r)
        if arm not in ENGINE_ARMS:
            # RN5 (item 3): a competitor arm's contexts may have been cached well before this
            # run - "declared per P3" made checkable against the file on disk.
            prov_entry = _ctx_file_provenance(_ctx_path(arm))
            if prov_entry:
                out.setdefault("competitor_cache", {})[arm] = prov_entry
    for arm, k in (("none", 0), ("oracle", 99)):
        pt = point(arm, k)
        if pt:
            out["brackets"][arm] = pt
    # item 1: the answer/judge stages' own transport - a bypass or failed embed there taints
    # every arm's numbers, not just one, so it is folded straight into the root.
    ans_transport = answers.get("_transport")
    jdg_transport = verdicts.get("_transport")
    stage_reasons = []
    if ans_transport:
        out["answer_transport"] = ans_transport
        if ans_transport.get("valid") is False:
            stage_reasons.append(f"answer stage: {ans_transport.get('invalid_reason')}")
    if jdg_transport:
        out["judge_transport"] = jdg_transport
        if jdg_transport.get("valid") is False:
            stage_reasons.append(f"judge stage: {jdg_transport.get('invalid_reason')}")
    if stage_reasons:
        out["valid"] = False
        out["invalid_reason"] = "; ".join(stage_reasons)
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
        }
    _propagate_root_invalidity(out)      # K25: any invalid arm/bracket invalidates the whole artifact
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
    pacer.install()          # item 9B/P0(a): pace/retry/count every stage's own Ollama traffic

    if args.stage == "contexts":
        fn = CONTEXT_FNS.get(args.arm)
        if fn is None:
            print(f"unknown arm {args.arm!r}; have {', '.join(CONTEXT_FNS)} (langmem_full is a blocker: "
                  f"its memories live in process and are not retained after the head-to-head run)")
            return 2
        snap = pacer.snapshot()
        ctx = fn(data, pool)
        _attach_prefixed_transport(ctx, "_transport", snap)
        if args.arm in ENGINE_ARMS:
            stamp_engine_commit(ctx)
        _save(_ctx_path(args.arm), ctx)
        n_items = sum(len(v) for k, v in ctx.items() if not k.startswith("_"))
        print(f"  {args.arm}: contexts for {len([k for k in ctx if not k.startswith('_')])} questions, "
              f"{n_items} items -> {_ctx_path(args.arm).name}")
        return 0
    if args.stage == "answer":
        answer_stage(arms, data, pool, READER, CHAR_BUDGET)
        return 0
    # F6: judge/summary/--save can make zero model calls on complete caches, and would restamp
    # an OLD engine measurement as today's - refuse before either stage runs, not after.
    problems = check_engine_freshness(arms, ENGINE_ARMS, _ctx_path, "python research/frontier_eval.py")
    if problems:
        print("F6 guard: refusing - a cached engine context is stale relative to HEAD:")
        for p in problems:
            print(f"  - {p}")
        return 2
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
        prov.stamp(res)
        Path(args.out).write_text(json.dumps(res, indent=1), encoding="utf-8", newline="\n")
        print(f"  saved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
