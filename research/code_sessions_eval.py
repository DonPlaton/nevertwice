#!/usr/bin/env python3
"""The code-session stand: every system's memory on coding sessions with gold answers (ledger J3).

The frontier stand asks chat questions; this one asks what a coding agent asks - the literal
value, the value after it changed, the lesson, and "which note should fire on this tool call" -
over `research/data/code_sessions_v1.json` (facts and gold by program, prose by a foreign
model). One project at a time, the way an agent works: every arm ingests a project's sessions
into their own store and answers that project's questions from it.

Arms (contexts): `nevertwice_full` - our extractor on every session with `capture_session(date=...)`,
then `api.recall` (title, description, prevention, evidence spans); `naive` - the append-only
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
TOP_SITUATION = 3
CHAR_BUDGET = int(os.environ.get("CODESESS_CHAR_BUDGET", "24000"))
ARMS = ("nevertwice_full", "naive", "mem0_infer")
BRACKETS = ("none", "oracle")
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


def _ctx_path(arm: str) -> Path:
    return DATA / f"codesess_{CORPUS_NAME}_contexts_{arm}_cache.json"


def _cache_path(stage: str) -> Path:
    return DATA / f"codesess_{CORPUS_NAME}_{stage}_cache.json"


# ── stage 1: contexts per arm ─────────────────────────────────────────────────

def contexts_nevertwice_full(corpus: dict) -> dict:
    """Our extractor, one project per synthetic project, sessions dated as the corpus says."""
    os.environ["NEVERTWICE_CLOUD"] = "none"
    os.environ["NEVERTWICE_MODEL"] = EXTRACTOR
    m.OLLAMA_MODEL = EXTRACTOR         # bound explicitly: the engine read the name at import
    from nevertwice import api                                   # noqa: PLC0415
    out = {"_ingest": {"llm": api.m.OLLAMA_MODEL, "llm_calls_per_session": 1, "sessions": 0, "errors": 0,
                       "sessions_with_zero_notes": 0}}
    t0 = time.time()
    for i, p in enumerate(corpus["projects"]):
        for s in p["sessions"]:
            out["_ingest"]["sessions"] += 1
            try:
                r = api.capture_session(s["text"], project=p["id"], session_id=s["id"], trigger="ingest", date=s["day"])
                if int(r.get("patterns", 0)) + int(r.get("mistakes", 0)) + int(r.get("decisions", 0)) == 0:
                    out["_ingest"]["sessions_with_zero_notes"] += 1
            except Exception:                                    # noqa: BLE001 - counted
                out["_ingest"]["errors"] += 1
        for q in p["questions"]:
            query = q["question"]
            hits = api.recall(query, project=p["id"], k=10)
            out[q["id"]] = [{"id": h.get("stem"), "text": fe._hit_text(h)} for h in hits]
        print(f"  [{i + 1}/{len(corpus['projects'])}] {p['slug']}  ({time.time() - t0:.0f}s)", flush=True)
    ev = getattr(api, "_evidence", None)
    out["_ingest"]["evidence"] = dict(ev.STATS) if ev is not None else None
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
    out = {"_ingest": {"llm": EXTRACTOR, "llm_calls_per_session": 2, "sessions": 0, "errors": 0}}
    for i, p in enumerate(corpus["projects"]):
        for s in p["sessions"]:
            out["_ingest"]["sessions"] += 1
            try:
                mem.add(s["text"], user_id=p["id"])
            except Exception:                                    # noqa: BLE001
                out["_ingest"]["errors"] += 1
        for q in p["questions"]:
            r = mem.search(q["question"], filters={"user_id": p["id"]}, limit=10)
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
    changed = 0
    for arm in list(arms) + list(BRACKETS):
        ctx = {} if arm in BRACKETS else fe._load(_ctx_path(arm))
        if arm not in BRACKETS and not ctx:
            print(f"- {arm}: no contexts - skipped")
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
            cache[key] = {"answer": str(ans)[:600], "prompt_tokens": r["prompt_tokens"], "context_chars": len(context)}
            changed += 1
            n_done += 1
            if changed % 20 == 0:
                fe._save(_cache_path("answers"), cache)
        print(f"- {arm}: {n_done} new answers", flush=True)
    fe._save(_cache_path("answers"), cache)
    return cache


def judge_stage(arms: list[str], qs: list[dict], reader: str, judge: str) -> dict:
    answers = fe._load(_cache_path("answers"))
    verdicts = fe._load(_cache_path("verdicts"))
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
            changed += 1
            if changed % 25 == 0:
                fe._save(_cache_path("verdicts"), verdicts)
    fe._save(_cache_path("verdicts"), verdicts)
    return verdicts


def score_answers(qs: list[dict], answers: dict, verdicts: dict, ctx: dict, reader: str, arm: str, judge: str) -> dict:
    """Per type: the rate and its interval, tokens, and for `current` the stale rate."""
    by = {}
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
            continue
        ans = answers[akey]["answer"]
        d["n"] += 1
        d["tokens"].append(answers[akey]["prompt_tokens"])
        if t == "lesson":
            v = verdicts.get(f"{judge}|{akey}")
            if v is None:
                d["n"] -= 1
                d["tokens"].pop()
                continue
            d["correct"] += int(bool(v))
        else:
            got = hit(q["markers"], ans)
            d["correct"] += int(got)
            if t == "current" and hit(q["stale_markers"], ans) and not got:
                d["stale"] += 1
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
        out[t] = row
    core = [t for t in ("fact", "current", "lesson") if t in out]
    if core:
        n = sum(out[t]["n"] for t in core)
        c = sum(int(round(out[t]["accuracy"] * out[t]["n"])) for t in core)
        out["core"] = {"n": n, "accuracy": round(c / n, 4), "ci": fe.wilson(c, n)}
    return out


def summarise(arms: list[str], qs: list[dict], corpus: dict, reader: str, judge: str) -> dict:
    answers = fe._load(_cache_path("answers"))
    verdicts = fe._load(_cache_path("verdicts"))
    out = {"corpus": {"name": corpus["name"], "sha256": corpus["sha256"], "generator_model": corpus.get("generator_model"),
                      "counts": corpus.get("counts")},
           "reader": reader, "judge": judge, "extractor": EXTRACTOR, "k": K, "top_situation": TOP_SITUATION,
           "arms": {}, "brackets": {}, "ingest": {}}
    for arm in list(arms) + list(BRACKETS):
        ctx = {} if arm in BRACKETS else fe._load(_ctx_path(arm))
        sc = score_answers(qs, answers, verdicts, ctx, reader, arm, judge)
        if sc:
            (out["brackets"] if arm in BRACKETS else out["arms"])[arm] = sc
        if ctx.get("_ingest"):
            out["ingest"][arm] = ctx["_ingest"]
    # the corpus gates, read here so the artifact says whether the corpus separates
    none_ = (out["brackets"].get("none") or {}).get("core", {}).get("accuracy")
    orac = (out["brackets"].get("oracle") or {}).get("core", {}).get("accuracy")
    naive = (out["arms"].get("naive") or {}).get("core", {}).get("accuracy")
    gates = {"none_le_0.10": None if none_ is None else none_ <= 0.10,
             "oracle_ge_0.60": None if orac is None else orac >= 0.60,
             "naive_le_oracle_minus_0.20": None if (naive is None or orac is None) else naive <= orac - 0.20}
    gates["corpus_separates"] = all(v is True for v in gates.values())
    out["corpus_gates"] = gates
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stage", choices=["contexts", "answer", "judge", "summary"])
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--arm", default="")
    ap.add_argument("--arms", default="nevertwice_full,naive")
    ap.add_argument("--limit", type=int, default=0, help="first N projects (a smoke run)")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    corpus = load_corpus(Path(args.corpus))
    global CORPUS_NAME
    CORPUS_NAME = str(corpus.get("name") or Path(args.corpus).stem)
    if args.limit:
        corpus["projects"] = corpus["projects"][:args.limit]
    qs, pool = questions(corpus), pool_of(corpus)
    arms = [a for a in args.arms.split(",") if a]
    print(f"code sessions: {len(corpus['projects'])} projects, {len(qs)} questions, reader {READER}, judge {JUDGE}, stage {args.stage}")
    if args.stage == "contexts":
        fn = CONTEXT_FNS.get(args.arm)
        if fn is None:
            print(f"unknown arm {args.arm!r}; have {', '.join(CONTEXT_FNS)}")
            return 2
        ctx = fn(corpus)
        fe._save(_ctx_path(args.arm), ctx)
        print(f"  {args.arm}: contexts for {len([k for k in ctx if not k.startswith('_')])} questions -> {_ctx_path(args.arm).name}")
        return 0
    if args.stage == "answer":
        answer_stage(arms, qs, pool, READER)
        return 0
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
        out.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  saved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
