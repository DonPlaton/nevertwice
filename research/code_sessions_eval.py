#!/usr/bin/env python3
"""The code-session stand: every system's memory on coding sessions with gold answers (ledger J3).

The frontier stand asks chat questions; this one asks what a coding agent asks - the literal
value, the value after it changed, the lesson, and "which note should fire on this tool call" -
over `research/data/code_sessions_v1.json` (facts and gold by program, prose by a foreign
model). One project at a time, the way an agent works: every arm ingests a project's sessions
into their own store and answers that project's questions from it.

Arms (contexts): `nevertwice_full` - our extractor on every session with `capture_session(date=...)`,
then `api.recall` (title, description, prevention); `naive` - the append-only
floor: whole sessions ranked by term overlap; `mem0_infer` - Mem0's own pipeline (its venv);
`none` and `oracle` are the brackets. The reader and the judge are the frontier's.

Scoring by question type:
* `fact`      - the reader's answer carries a gold marker (no judge needed);
* `current`   - the answer carries the new value; **stale** when it carries the old one and not
                the new - the supersession stand's reading, on a reader's answer;
* `lesson`    - the local judge grades the answer against the prevention sentence;
* `situation` - retrieval only: the note (or session, or memory) that carries the lesson's
                prevention sentence is in the top three for the tool call - the I8 dataset.

Corpus gates, written first: none <= 0.10, oracle >= 0.60, naive <= oracle - 0.20 on fact +
current + lesson; a corpus that does not separate is not published.

    python research/code_sessions_eval.py contexts --arm nevertwice_full
    python research/code_sessions_eval.py contexts --arm naive
    python research/code_sessions_eval.py answer --arms nevertwice_full,naive
    python research/code_sessions_eval.py judge  --arms nevertwice_full,naive --save
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402

sandbox_guard.isolate(prefix="nevertwice_codesess_")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402
import qa_eval as qa  # noqa: E402 - the reader / judge prompts
import frontier_eval as fe  # noqa: E402 - ollama_chat, wilson, the caches' shape
import _provenance as prov  # noqa: E402 - measured_at: {commit, utc, dirty} on the final artifacts
import _ollama_pacer as pacer  # noqa: E402 - item 9B/P0(a): pace/retry/count this stand's own traffic

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                  # noqa: BLE001
    pass

DATA = HERE / "data"
CORPUS = DATA / "code_sessions_v1.json"
OUT = ROOT / "research" / "results" / "code_sessions_v1.json"
READER = os.environ.get("FRONTIER_READER", "qwen2.5:7b")
JUDGE = os.environ.get("FRONTIER_JUDGE", "qwen3.6:27b")
EXTRACTOR = os.environ.get("H2H_LLM", "qwen2.5-7b-64k:latest")
K = 5
#: The `api.recall` top-k `contexts_nevertwice_full` asks for - not yet a CLI flag, but named
#: (not a bare `10` at the call site) and folded into `_ctx_path` below, so a future edit that
#: makes it one, or a change to this constant itself, cannot silently share a cache file with a
#: run made under the old value.
RECALL_K = 10
TOP_SITUATION = 3
CHAR_BUDGET = int(os.environ.get("CODESESS_CHAR_BUDGET", "24000"))
ARMS = ("nevertwice_full", "naive", "mem0_infer")
BRACKETS = ("none", "oracle")
#: F6: the only arm whose contexts read our engine - naive/mem0_infer never touch nevertwice/.
ENGINE_ARMS = ("nevertwice_full",)
_SEP = re.compile(r"[\s_\-]+")


def _norm(t: str) -> str:
    return _SEP.sub(" ", (t or "").lower())


def hit(markers: list[str], text: str) -> bool:
    low = _norm(text)
    return any(_norm(mk) in low for mk in markers if mk)


def load_corpus(path: Path = CORPUS) -> dict:
    raw = path.read_bytes()
    d = json.loads(raw.decode("utf-8"))
    d["sha256"] = hashlib.sha256(raw).hexdigest()
    return d


def questions(corpus: dict) -> list[dict]:
    """Every question with its project, and - for lesson and situation questions - the
    prevention sentence of the note it points at (what a retrieved text must carry)."""
    out = []
    for p in corpus["projects"]:
        prev = {les["stem"]: les["prevention"] for les in p.get("lessons", [])}
        for q in p["questions"]:
            out.append({**q, "project": p["id"], "prevention": prev.get(q.get("note_stem") or "", "")})
    return out


def pool_of(corpus: dict) -> dict:
    return {s["id"]: s for p in corpus["projects"] for s in p["sessions"]}


#: The caches are keyed by corpus name so the synthetic dev set and the private held-out never
#: share a context, an answer or a verdict (set from the loaded corpus in `main`).
CORPUS_NAME = "code_sessions_v1"
#: `corpus["name"]` alone is self-declared by the FILE, not by its path or its bytes - two
#: different files can carry the same name field. The content hash (`load_corpus`'s own
#: `sha256`) is what actually identifies "which corpus", so `_ctx_path` folds a short prefix of
#: it in too (set from the loaded corpus in `main`).
CORPUS_SHA = ""
#: `--seed-pairs N` (A9) changes what `contexts_nevertwice_full` writes into a project's store
#: BEFORE ingest, so a seeded and an unseeded run of the identical corpus must not share the one
#: `nevertwice_full` cache file - the collision an earlier commit on this branch flagged and left
#: unfixed. Set from `args.seed_pairs` in `main`; irrelevant to every other arm.
SEED_PAIRS_KEY = 0


def _ctx_path(arm: str) -> Path:
    sha_part = f"_c{CORPUS_SHA[:12]}" if CORPUS_SHA else ""
    variant = ""
    if arm in ENGINE_ARMS:
        variant = f"_k{RECALL_K}" + (f"_seed{SEED_PAIRS_KEY}" if SEED_PAIRS_KEY else "")
    return DATA / f"codesess_{CORPUS_NAME}{sha_part}_contexts_{arm}{variant}_cache.json"


def _cache_path(stage: str) -> Path:
    return DATA / f"codesess_{CORPUS_NAME}_{stage}_cache.json"


# ── stage 1: contexts per arm ─────────────────────────────────────────────────

def _llm_stats_snapshot(mh) -> tuple[int, int]:
    """`(prompt_tokens, eval_tokens)` accumulated on `_LLM_STATS` so far - a process-lifetime
    running total, never reset between sessions. A per-session cost is the DELTA of two
    snapshots, never this value alone (same convention as `supersession_bench.py`'s own copy -
    a two-line function kept local rather than cross-imported between research scripts)."""
    return (mh._LLM_STATS.get("prompt_tokens", 0), mh._LLM_STATS.get("eval_tokens", 0))


def _llm_stats_delta(before: tuple[int, int], after: tuple[int, int]) -> dict:
    return {"prompt_tokens": after[0] - before[0], "eval_tokens": after[1] - before[1]}


def seed_ride_pairs(api, project: str, n: int) -> int:
    """A9 (Q3): `n` same-slug pairs of unproven-different statements, written directly through
    `api.remember` - NO extractor call (`remember` -> `remember_lessons` -> `write_typed_note`
    only) - so a project's store carries `n` genuine contested pairs BEFORE its real sessions are
    ingested (PLAN: "--seed-pairs 2 seeds two synthetic contested pairs per project store before
    ingest"). Two DIFFERENT numeric values under one title, with no `[facts]` block on either
    side: K8 layer 1's own rules all need SOME literal or text overlap to prove a replacement
    (`memory_hook._same_replacement` - "unproven" is what is left when neither side supplies
    one), so the pair is kept apart as a live '-2' sibling and the earlier note is stamped
    `contested` - the exact shape mechanism A (ride-along, TRIZ-PART2B) is meant to carry to the
    project's NEXT real session. Returns how many pairs were actually seeded (`api.remember`
    returns None for a write it refused as injection-shaped - counted, not raised on)."""
    seeded = 0
    for i in range(n):
        title = f"seed contested pair {i}"
        old = api.remember(title, project=project, type="decision",
                           description=f"The seed threshold for case {i} is {10 + i} units.")
        new = api.remember(title, project=project, type="decision",
                           description=f"The seed threshold for case {i} is {90 + i} units.")
        if old and new:
            seeded += 1
    return seeded


def contexts_nevertwice_full(corpus: dict, seed_pairs: int = 0) -> dict:
    """Our extractor, one project per synthetic project, sessions dated as the corpus says.
    `seed_pairs` > 0 (A9) seeds that many synthetic contested pairs into each project's store
    BEFORE its sessions are ingested (`seed_ride_pairs`), and every capture's `_LLM_STATS` token
    delta is recorded per session (G3.2's per-session token reading)."""
    os.environ["NEVERTWICE_CLOUD"] = "none"
    os.environ["NEVERTWICE_MODEL"] = EXTRACTOR
    os.environ["NEVERTWICE_EXTRACT_TEMP"] = "0"   # deterministic extraction: a benchmark pins the seed
    m.OLLAMA_MODEL = EXTRACTOR         # bound explicitly: the engine read the name at import
    from nevertwice import api                                   # noqa: PLC0415
    out = {"_ingest": {"llm": api.m.OLLAMA_MODEL, "llm_calls_per_session": 1, "sessions": 0, "errors": 0,
                       "sessions_with_zero_notes": 0, "seed_pairs": seed_pairs,
                       "seeded_pairs_by_project": {}, "session_tokens": {}}}
    t0 = time.time()
    for i, p in enumerate(corpus["projects"]):
        if seed_pairs:
            out["_ingest"]["seeded_pairs_by_project"][p["id"]] = seed_ride_pairs(api, p["id"], seed_pairs)
        for s in p["sessions"]:
            out["_ingest"]["sessions"] += 1
            try:
                before = _llm_stats_snapshot(api.m)
                r = api.capture_session(s["text"], project=p["id"], session_id=s["id"], trigger="ingest", date=s["day"])
                out["_ingest"]["session_tokens"][s["id"]] = _llm_stats_delta(before, _llm_stats_snapshot(api.m))
                if int(r.get("patterns", 0)) + int(r.get("mistakes", 0)) + int(r.get("decisions", 0)) == 0:
                    out["_ingest"]["sessions_with_zero_notes"] += 1
            except Exception:                                    # noqa: BLE001 - counted
                out["_ingest"]["errors"] += 1
        for q in p["questions"]:
            query = q["question"]
            hits = api.recall(query, project=p["id"], k=RECALL_K)
            out[q["id"]] = [{"id": h.get("stem"), "text": fe._hit_text(h)} for h in hits]
        print(f"  [{i + 1}/{len(corpus['projects'])}] {p['slug']}  ({time.time() - t0:.0f}s)", flush=True)
    return out


def _overlap_rank(query: str, docs: dict, k: int) -> list[str]:
    q = set(_norm(query).split())
    scored = sorted(docs, key=lambda sid: -len(q & set(_norm(docs[sid]).split())))
    return scored[:k]


def contexts_naive(corpus: dict) -> dict:
    """The floor: whole sessions of the project, ranked by term overlap with the question."""
    out = {}
    for p in corpus["projects"]:
        docs = {s["id"]: s["text"] for s in p["sessions"]}
        for q in p["questions"]:
            out[q["id"]] = [{"id": sid, "text": docs[sid]} for sid in _overlap_rank(q["question"], docs, 10)]
    return out


def contexts_mem0_infer(corpus: dict) -> dict:
    """Mem0's pipeline: one `add` per session, one user per project, then its search."""
    from mem0 import Memory                                      # noqa: PLC0415
    import tempfile, shutil                                      # noqa: PLC0415
    for var in ("MEM0_TELEMETRY", "MEM0_TELEMETRY_ENABLED", "ANONYMIZED_TELEMETRY"):
        os.environ.setdefault(var, "False")
    base = Path(tempfile.mkdtemp(prefix="nevertwice_codesess_mem0_"))
    cfg = {"llm": {"provider": "ollama", "config": {"model": EXTRACTOR, "temperature": 0.0, "ollama_base_url": fe.OLLAMA}},
           "embedder": {"provider": "ollama", "config": {"model": os.environ.get("NEVERTWICE_EMBED_MODEL", "bge-m3"),
                                                         "ollama_base_url": fe.OLLAMA}},
           "vector_store": {"provider": "qdrant", "config": {"collection_name": "codesess", "path": str(base / "qdrant"),
                                                             "on_disk": True, "embedding_model_dims": 1024}}}
    mem = Memory.from_config(cfg)
    #: Once, before any work: `limit=10` stood here and mem0 2.0.19 has no `limit` - it is
    #: `top_k`, default 20 - so the keyword went into `**kwargs` and every query fetched
    #: twenty. See `head_to_head.named_or_raise`.
    import head_to_head as hh                                    # noqa: PLC0415
    hh.named_or_raise(mem.search, "top_k", "filters")
    out = {"_ingest": {"llm": EXTRACTOR, "llm_calls_per_session": 2, "sessions": 0, "errors": 0}}
    for i, p in enumerate(corpus["projects"]):
        for s in p["sessions"]:
            out["_ingest"]["sessions"] += 1
            try:
                mem.add(s["text"], user_id=p["id"])
            except Exception:                                    # noqa: BLE001
                out["_ingest"]["errors"] += 1
        for q in p["questions"]:
            r = mem.search(q["question"], filters={"user_id": p["id"]}, top_k=10)
            res = r.get("results", r) if isinstance(r, dict) else r
            out[q["id"]] = [{"id": x.get("id"), "text": x.get("memory") or x.get("text") or ""} for x in res]
        print(f"  [{i + 1}/{len(corpus['projects'])}] {p['slug']}", flush=True)
    shutil.rmtree(base, ignore_errors=True)
    return out


CONTEXT_FNS = {"nevertwice_full": contexts_nevertwice_full, "naive": contexts_naive, "mem0_infer": contexts_mem0_infer}


# ── stage 2 and 3: the reader, the judge, the scoring ────────────────────────

def context_text(items: list[dict], k: int, budget: int = CHAR_BUDGET) -> str:
    return fe._context_text(items, k, budget)


def situation_hit(items: list[dict], prevention: str, top: int = TOP_SITUATION) -> bool:
    """Retrieval only: something in the top `top` carries the lesson's prevention sentence."""
    return any(hit([prevention], it.get("text") or "") for it in items[:top])


def answer_stage(arms: list[str], qs: list[dict], pool: dict, reader: str) -> dict:
    cache = fe._load(_cache_path("answers"))
    snap = pacer.snapshot()          # item 9B/P0(a): this stage's own reader traffic
    changed = 0
    blocked_now = []
    for arm in list(arms) + list(BRACKETS):
        ctx = {} if arm in BRACKETS else fe._load(_ctx_path(arm))
        if arm not in BRACKETS and not ctx:
            # P0(f)/K23: no bare skip - recorded into the answers cache (a `_`-prefixed key, the
            # only durable record a LATER `judge`/`summary` process, in its own separate
            # invocation, can read) so `summarise()` turns this into a named refusal in the
            # artifact (blocked + valid:false), never a vanished arm.
            print(f"- {arm}: no contexts - blocked, recorded as a named refusal in the artifact")
            blocked_now.append(arm)
            continue
        n_done = 0
        for q in qs:
            if q["type"] == "situation":
                continue                                          # retrieval only, no reader
            if arm == "none":
                context = "(no memory available)"
            elif arm == "oracle":
                context = context_text([{"id": s, "text": pool[s]["text"]} for s in q["gold_sessions"]], 99)
            else:
                context = context_text(ctx.get(q["id"], []), K)
            key = fe._akey(reader, arm, K, q["id"], context)
            if key in cache:
                continue
            r = fe.ollama_chat(reader, qa.ANSWER_PROMPT.format(context=context, question=q["question"]))
            if not r:
                continue
            try:
                ans = json.loads(m._strip_json_fence(r["content"])).get("answer", "")
            except (ValueError, AttributeError):
                ans = r["content"][:400]
            stale = fe._find_akey(cache, reader, arm, K, q["id"])
            if stale:
                del cache[stale]
            # K30: stamp every NEW answer with the commit and utc it was produced at -
            # `summarise()` refuses a point whose answers/verdicts are not provably current.
            cache[key] = {"answer": str(ans)[:600], "prompt_tokens": r["prompt_tokens"],
                          "context_chars": len(context), "commit": fe.git_head(), "utc": fe._utc_now_iso()}
            changed += 1
            n_done += 1
            if changed % 20 == 0:
                fe._save(_cache_path("answers"), cache)
        print(f"- {arm}: {n_done} new answers", flush=True)
    if blocked_now:
        cache["_blocked_arms"] = sorted(set(cache.get("_blocked_arms") or []) | set(blocked_now))
    fe._attach_prefixed_transport(cache, "_transport", snap)
    fe._save(_cache_path("answers"), cache)
    return cache


def judge_stage(arms: list[str], qs: list[dict], reader: str, judge: str) -> dict:
    answers = fe._load(_cache_path("answers"))
    verdicts = fe._load(_cache_path("verdicts"))
    snap = pacer.snapshot()          # item 9B/P0(a): this stage's own judge traffic
    changed = 0
    for arm in list(arms) + list(BRACKETS):
        for q in qs:
            if q["type"] != "lesson":
                continue
            akey = fe._find_akey(answers, reader, arm, K, q["id"])
            vkey = f"{judge}|{akey}"
            if akey is None or vkey in verdicts:
                continue
            v = fe.judge_one(judge, {"question": q["question"], "answer": q["answer"]}, answers[akey]["answer"])
            if v is None:
                continue
            verdicts[vkey] = v
            verdicts.setdefault("_meta", {})[vkey] = {"commit": fe.git_head(), "utc": fe._utc_now_iso()}
            changed += 1
            if changed % 25 == 0:
                fe._save(_cache_path("verdicts"), verdicts)
    fe._attach_prefixed_transport(verdicts, "_transport", snap)
    fe._save(_cache_path("verdicts"), verdicts)
    return verdicts


def score_answers(qs: list[dict], answers: dict, verdicts: dict, ctx: dict, reader: str, arm: str, judge: str,
                  *, head: str | None = None, closure: list[str] | None = None) -> dict:
    """Per type: the rate and its interval, tokens, and for `current` the stale rate.

    `head`/`closure` (K30): passed by `summarise()` (computed once there); a direct/test call
    computes its own so the signature stays usable standalone. `situation` is retrieval-only
    (no answer/verdict cache involved, so K30 does not apply to it - it is never a drop, P0(f))
    and is always counted, matching the stand's own scope: it is never sent to the reader."""
    head = head if head is not None else fe.git_head()
    closure = closure if closure is not None else fe.engine_closure("python research/code_sessions_eval.py")
    by = {}
    missing_by_type: dict = {}
    stale_ans_by_type: dict = {}
    stale_vd_by_type: dict = {}
    for q in qs:
        t = q["type"]
        d = by.setdefault(t, {"n": 0, "correct": 0, "stale": 0, "tokens": []})
        if t == "situation":
            # retrieval only; the brackets are 0 (nothing) and 1 (the note itself) by construction
            items = ctx.get(q["id"], []) if arm not in BRACKETS else []
            ok = arm == "oracle" or (arm != "none" and situation_hit(items, q["prevention"]))
            d["n"] += 1
            d["correct"] += int(ok)
            continue
        akey = fe._find_akey(answers, reader, arm, K, q["id"])
        if akey is None:
            missing_by_type.setdefault(t, []).append(q["id"])       # P0(f)/K23: no answer
            continue
        ans_entry = answers[akey]
        ans = ans_entry["answer"]
        a_current = fe._stamp_current(ans_entry, head, closure)
        if t == "lesson":
            vkey = f"{judge}|{akey}"
            v = verdicts.get(vkey)
            if v is None:
                missing_by_type.setdefault(t, []).append(q["id"])   # P0(f)/K23: no verdict
                continue
            d["n"] += 1
            d["tokens"].append(ans_entry["prompt_tokens"])
            d["correct"] += int(bool(v))
            v_stamp = (verdicts.get("_meta") or {}).get(vkey)
            if not fe._stamp_current(v_stamp, head, closure):
                stale_vd_by_type.setdefault(t, []).append(q["id"])
        else:
            d["n"] += 1
            d["tokens"].append(ans_entry["prompt_tokens"])
            got = hit(q["markers"], ans)
            d["correct"] += int(got)
            if t == "current" and hit(q["stale_markers"], ans) and not got:
                d["stale"] += 1
        if not a_current:
            stale_ans_by_type.setdefault(t, []).append(q["id"])
    type_counts: dict = {}
    for q in qs:
        type_counts[q["type"]] = type_counts.get(q["type"], 0) + 1
    out = {}
    for t, d in by.items():
        if not d["n"]:
            continue
        row = {"n": d["n"], "accuracy": round(d["correct"] / d["n"], 4), "ci": fe.wilson(d["correct"], d["n"])}
        if d["tokens"]:
            row["mean_prompt_tokens"] = round(sum(d["tokens"]) / len(d["tokens"]), 1)
        if t == "current":
            row["stale_rate"] = round(d["stale"] / d["n"], 4)
            row["stale_ci"] = fe.wilson(d["stale"], d["n"])
        n_expected = type_counts.get(t, d["n"])
        missing = missing_by_type.get(t, [])
        stale_a = stale_ans_by_type.get(t, [])
        stale_v = stale_vd_by_type.get(t, [])
        reasons = []
        # P0(f) item 2: a question with no answer or no verdict was silently left out of n.
        if d["n"] != n_expected or missing:
            row["missing_count"] = len(missing)
            row["missing_question_ids"] = missing
            reasons.append(f"{len(missing)} of {n_expected} {t!r} question(s) have no answer "
                           f"or no verdict for arm {arm!r}")
        if stale_a:
            row["stale_answers_count"] = len(stale_a)
            row["stale_answer_ids"] = stale_a
            reasons.append(f"K30: {len(stale_a)} answer(s) not stamped at a commit current with "
                           f"HEAD for arm {arm!r} type {t!r}: {stale_a[:20]}")
        if stale_v:
            row["stale_verdicts_count"] = len(stale_v)
            row["stale_verdict_ids"] = stale_v
            reasons.append(f"K30: {len(stale_v)} verdict(s) not stamped at a commit current with "
                           f"HEAD for arm {arm!r} type {t!r}: {stale_v[:20]}")
        if reasons:
            row["valid"] = False
            row["invalid_reason"] = "; ".join(reasons)
        out[t] = row
    core = [t for t in ("fact", "current", "lesson") if t in out]
    if core:
        n = sum(out[t]["n"] for t in core)
        c = sum(int(round(out[t]["accuracy"] * out[t]["n"])) for t in core)
        out["core"] = {"n": n, "accuracy": round(c / n, 4), "ci": fe.wilson(c, n)}
        # K25: a claim on `core` reads outside any single folded type's own dict.
        invalid_core = [t for t in core if out[t].get("valid") is False]
        if invalid_core:
            out["core"]["valid"] = False
            out["core"]["invalid_reason"] = "a folded type has missing/stale data: " + ", ".join(invalid_core)
    return out


def fact_survival(qs: list[dict], ctx: dict, arm: str) -> dict:
    """Model-free write-path metric: the share of literal-fact questions whose answer is present,
    verbatim (under the reader's own normalisation), in the notes the arm returned for that question.
    Uses the whole recall result, not the reader's top-k window, so it measures whether the fact
    reached memory at all - independent of the reader and of the context budget. Deterministic and
    fast, so the extraction prompt can be iterated dozens of times an hour (ledger J3, held-out)."""
    fq = [q for q in qs if q.get("type") == "fact"]
    survived, misses = 0, []
    for q in fq:
        items = ctx.get(q["id"], []) or []
        text = _norm(" ".join((it.get("text") or "") for it in items))
        ok = _norm(q["answer"]) in text
        survived += int(ok)
        if not ok:
            misses.append(q["answer"][:60])
    n = len(fq)
    return {"n": n, "survived": survived,
            "fact_survival": round(survived / n, 4) if n else None,
            "ci": fe.wilson(survived, n) if n else None,
            "misses": misses[:20]}


def summarise(arms: list[str], qs: list[dict], corpus: dict, reader: str, judge: str) -> dict:
    answers = fe._load(_cache_path("answers"))
    verdicts = fe._load(_cache_path("verdicts"))
    head = fe.git_head()             # K30: computed once, same HEAD/closure for every arm/type
    closure = fe.engine_closure("python research/code_sessions_eval.py")
    out = {"corpus": {"name": corpus["name"], "sha256": corpus["sha256"], "generator_model": corpus.get("generator_model"),
                      "counts": corpus.get("counts")},
           "reader": reader, "judge": judge, "extractor": EXTRACTOR, "k": K, "top_situation": TOP_SITUATION,
           "arms": {}, "brackets": {}, "ingest": {}}
    #: P0(f)/K23: `answer_stage`'s own record of a requested arm it found no contexts for - the
    #: only durable trace a LATER `judge`/`summary` process (its own separate invocation) can
    #: read; `summarise()` never loaded a contexts cache of its own before this.
    blocked_arms = set(answers.get("_blocked_arms") or [])
    for arm in list(arms) + list(BRACKETS):
        if arm in blocked_arms:
            out["arms"][arm] = {
                "blocked": f"no contexts cached for requested arm {arm!r} - run: "
                           f"python research/code_sessions_eval.py contexts --arm {arm}",
                "valid": False,
                "invalid_reason": f"{arm}: requested arm has no cached contexts",
            }
            continue
        ctx = {} if arm in BRACKETS else fe._load(_ctx_path(arm))
        sc = score_answers(qs, answers, verdicts, ctx, reader, arm, judge, head=head, closure=closure)
        if sc and arm not in BRACKETS:
            fs = fact_survival(qs, ctx, arm)
            if fs["n"]:
                sc["fact_survival"] = fs
        if sc:
            (out["brackets"] if arm in BRACKETS else out["arms"])[arm] = sc
        if ctx.get("_ingest"):
            out["ingest"][arm] = ctx["_ingest"]
        if arm in BRACKETS:
            continue
        node = out["arms"].get(arm)
        # item 1: fold this arm's own contexts-stage transport in - the flag lands on the arm
        # the claim's pointer actually reads (K16(2)/K25).
        fe._fold_arm_context_provenance(arm, node, ctx, out)
        gap = fe.ctx_coverage_gap(ctx, [q["id"] for q in qs])
        if gap:
            # P0(f) item 2: a partial competitor cache (contexts for only some questions) is a
            # mismatch, not present - `answer_stage` still answers the missing ones from an
            # empty context, invisible to the n-vs-expected gate in `score_answers`.
            if node is not None:
                node["valid"] = False
                node["missing_contexts_count"] = len(gap)
                node["missing_contexts_ids"] = gap
                node["invalid_reason"] = "; ".join(r for r in (
                    node.get("invalid_reason"),
                    f"{len(gap)} of {len(qs)} question(s) have no cached context at all for "
                    f"arm {arm!r} (a partial cache counts as a mismatch, not as present)") if r)
        if arm not in ENGINE_ARMS:
            # RN5 (item 3): a competitor arm's contexts may have been cached well before this
            # run - "declared per P3" made checkable against the file on disk.
            prov_entry = fe._ctx_file_provenance(_ctx_path(arm))
            if prov_entry:
                out.setdefault("competitor_cache", {})[arm] = prov_entry
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
    # the corpus gates, read here so the artifact says whether the corpus separates
    none_ = (out["brackets"].get("none") or {}).get("core", {}).get("accuracy")
    orac = (out["brackets"].get("oracle") or {}).get("core", {}).get("accuracy")
    naive = (out["arms"].get("naive") or {}).get("core", {}).get("accuracy")
    gates = {"none_le_0.10": None if none_ is None else none_ <= 0.10,
             "oracle_ge_0.60": None if orac is None else orac >= 0.60,
             "naive_le_oracle_minus_0.20": None if (naive is None or orac is None) else naive <= orac - 0.20}
    gates["corpus_separates"] = all(v is True for v in gates.values())
    out["corpus_gates"] = gates
    fe._propagate_root_invalidity(out)   # K25: any invalid arm/bracket invalidates the whole artifact
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stage", choices=["contexts", "answer", "judge", "summary", "fact-survival"])
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--arm", default="")
    ap.add_argument("--arms", default="nevertwice_full,naive")
    ap.add_argument("--limit", type=int, default=0, help="first N projects (a smoke run)")
    ap.add_argument("--seed-pairs", type=int, default=0, dest="seed_pairs",
                    help="A9 (Q3): seed this many synthetic contested pairs into each project's "
                         "store before ingest (nevertwice_full arm only, contexts stage only) "
                         "via api.remember - no extractor call")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    corpus = load_corpus(Path(args.corpus))
    global CORPUS_NAME, CORPUS_SHA, SEED_PAIRS_KEY
    CORPUS_NAME = str(corpus.get("name") or Path(args.corpus).stem)
    CORPUS_SHA = str(corpus.get("sha256") or "")
    SEED_PAIRS_KEY = int(args.seed_pairs or 0)
    if args.limit:
        corpus["projects"] = corpus["projects"][:args.limit]
    qs, pool = questions(corpus), pool_of(corpus)
    arms = [a for a in args.arms.split(",") if a]
    print(f"code sessions: {len(corpus['projects'])} projects, {len(qs)} questions, reader {READER}, judge {JUDGE}, stage {args.stage}")
    pacer.install()          # item 9B/P0(a): pace/retry/count every stage's own Ollama traffic
    if args.stage == "contexts":
        fn = CONTEXT_FNS.get(args.arm)
        if fn is None:
            print(f"unknown arm {args.arm!r}; have {', '.join(CONTEXT_FNS)}")
            return 2
        snap = pacer.snapshot()
        ctx = fn(corpus, seed_pairs=args.seed_pairs) if args.arm == "nevertwice_full" else fn(corpus)
        fe._attach_prefixed_transport(ctx, "_transport", snap)
        if args.arm in ENGINE_ARMS:
            fe.stamp_engine_commit(ctx)
        fe._save(_ctx_path(args.arm), ctx)
        print(f"  {args.arm}: contexts for {len([k for k in ctx if not k.startswith('_')])} questions -> {_ctx_path(args.arm).name}")
        return 0
    if args.stage == "fact-survival":
        # model-free, seconds: read each arm's cached contexts and score literal survival
        rows = {}
        for arm in arms:
            ctx = fe._load(_ctx_path(arm))
            fs = fact_survival(qs, ctx, arm)
            rows[arm] = fs
            ci = fs["ci"] or (0, 0)
            print(f"  {arm:16s} fact_survival {fs['fact_survival']}  ({fs['survived']}/{fs['n']}, ci [{ci[0]:.3f},{ci[1]:.3f}])")
            if fs["misses"]:
                print(f"      misses: {fs['misses'][:6]}")
        if args.save:
            out = Path(args.out) if args.out else ROOT / "research" / "results" / f"{CORPUS_NAME}_fact_survival.json"
            fs_out = prov.stamp({"corpus": corpus["name"], "sha256": corpus["sha256"],
                                 "extractor": EXTRACTOR, "arms": rows})
            out.write_text(json.dumps(fs_out, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
            print(f"  saved -> {out}")
        return 0
    if args.stage == "answer":
        answer_stage(arms, qs, pool, READER)
        return 0
    # F6: judge/summary/--save can make zero model calls on complete caches, and would restamp
    # an OLD engine measurement as today's (frontier_eval.check_engine_freshness - the one
    # shared implementation, not a second independently-matched copy of "is this cache current").
    problems = fe.check_engine_freshness(arms, ENGINE_ARMS, _ctx_path,
                                         "python research/code_sessions_eval.py")
    if problems:
        print("F6 guard: refusing - a cached engine context is stale relative to HEAD:")
        for p in problems:
            print(f"  - {p}")
        return 2
    if args.stage == "judge":
        judge_stage(arms, qs, READER, JUDGE)
    res = summarise(arms, qs, corpus, READER, JUDGE)
    for name, sc in list(res["arms"].items()) + list(res["brackets"].items()):
        cells = "  ".join(f"{t} {sc[t]['accuracy']:.3f}" + (f" (stale {sc[t]['stale_rate']:.3f})" if t == "current" else "")
                          for t in ("fact", "current", "lesson", "situation", "core") if t in sc)
        print(f"  {name:16s} {cells}")
    print(f"  corpus gates: {res['corpus_gates']}")
    if args.save:
        out = Path(args.out) if args.out else ROOT / "research" / "results" / f"{CORPUS_NAME}.json"
        prov.stamp(res)
        out.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"  saved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
