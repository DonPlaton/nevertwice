#!/usr/bin/env python3
"""What each memory system puts INTO THE PROMPT, per session and per query.

This is the one axis where the project's advantage is structural rather than incremental,
and until now it was measured nowhere. `head_to_head.py` counts lexical tokens for BM25 --
words fed to a ranker -- not characters or tokens sent to a model. So the strongest
competitive sentence the project can say has never had a number behind it.

Three things this stand does differently from the ones beside it, each because a measurement
made the other way would not survive a reader.

**Characters are primary; tokens are derived, and never anonymous.** The ratio of characters
to tokens is a property of the PAIR (tokenizer, text), not a constant. Measured 2026-09-22 by
the auditing session: 4.6 characters per token on LongMemEval dialogue (from 2,536 real
`prompt_tokens` the frontier stand recorded beside `context_chars`), and 3.3 on the owner's
own store read with `cl100k_base` -- the same project constant of 4 is 14% high on the first
and 20% low on the second, in OPPOSITE directions. So every token figure here is labelled with
the tokenizer that produced it, and a bare "tokens" number is never printed. Characters are
reported first because they survive a change of tokenizer.

**The consumer's fixed prompt overhead is excluded, and said so.** The same measurement found
125 tokens of prompt that carry no memory at all (the `none` arm: 21 characters of context,
125 tokens of prompt). That overhead belongs to the agent's template, not to either memory
system; including it flatters whichever system injects less, because on small payloads it
dominates the ratio. It is named in the result as excluded rather than quietly dropped.

**Cost is a curve in session length, not a single ratio.** The two systems charge on different
units: this one injects once at session start (capped by `INJECT_BUDGET_CHARS`) and then
nothing per turn unless a guard fires; Mem0 searches on every query and pays its floor each
time. A single "per query" number therefore has no meaning until N is fixed -- and the ratio
at N=1 and at N=50 differ by nearly N. Reporting the curve is the only form that is true at
every N, and it is also the only form a competitor cannot accuse of picking its N.

    python research/token_floor.py --limit 50            # smoke, Nevertwice arm only
    python research/token_floor.py --save                # full, writes the artifact
    MEM0_TELEMETRY=False <mem0 venv python> research/token_floor.py --only mem0 --save

An arm that cannot run here records a blocker string with the reason. It never records a
number it did not measure.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sandbox_guard  # noqa: E402 - must precede any project import
sandbox_guard.isolate(prefix="nevertwice_tokenfloor_")

#: Three environment pins, all SET rather than defaulted, all before any project import. Copied
#: in shape from `abstention_ab.py:149-155` rather than from memory of what stands do, because
#: the first draft of this file carried none of them and `tests/_test_stands_pin_the_sampler.py`
#: caught it -- the gate working exactly as intended, on the stand written to measure honestly.
#:
#: `NEVERTWICE_CLOUD`: the engine's default is `auto`, which adopts the first of four provider
#: keys it finds in the environment and sends extraction off this machine. A benchmark that
#: silently ships its corpus to a third party is not a benchmark, and the keys are ambient here.
#: `NEVERTWICE_MODEL`: set, not defaulted -- the shell on this machine exports one for the live
#: hook, and `setdefault` once let that model build a store while the register named another.
#: `NEVERTWICE_EXTRACT_TEMP`: the engine reads it with a default of 0.2, right for a live hook
#: and wrong for a measurement; at 0.2, 80 of 80 cases served different text between two runs of
#: one commit, at 0 exactly one did (`supersession_bench`, 2026-09-22).
os.environ["NEVERTWICE_CLOUD"] = "none"
os.environ["NEVERTWICE_MODEL"] = os.environ.get("SUPERSESSION_LLM", "qwen3-coder:30b")
os.environ["NEVERTWICE_EXTRACT_TEMP"] = "0"

import api  # noqa: E402 - the write path lives here, NOT on memory_hook
import corpus_pin  # noqa: E402
import longmem_eval as le  # noqa: E402
import memory_hook as m  # noqa: E402

#: BOTH corpora, fixed here rather than behind a flag with a default. A default is a choice
#: whose consequence nobody sees afterwards, and this particular choice decides the answer:
#: LongMemEval is dialogue and this project's extractor writes lessons from working sessions,
#: so measuring only there reports a mismatch as a cost, and measuring only on code sessions
#: reports the favourable half. The pair measures the BORDER of the advantage, which is what
#: the claim is actually about; either one alone measures a point and calls it a rule.
#:
#: `longmemeval_oracle` is also the corpus everyone is compared on by default, so leaving it
#: out would be choosing the ground. Its row is expected to read "costs nothing, helps nothing"
#: -- unflattering, true, and the reader's to interpret.
CORPORA = ("longmemeval_oracle", "code_sessions_v1")
OUT = HERE / "results" / "token_floor.json"

#: Session lengths the curve is reported at. Not a sweep to find a favourable point: the whole
#: reason the curve exists is that no single point is honest, so the endpoints are fixed here
#: rather than chosen after seeing the numbers.
TURNS = (1, 5, 10, 25, 50, 100)

#: The consumer's fixed prompt overhead, in tokens, measured on the `none` arm of
#: `research/data/frontier_answers_cache.json` (n=150: median 21 context characters against
#: 125 prompt tokens). Recorded so a reader can add it back, never subtracted from an arm:
#: it belongs to the agent's template and is identical for every arm, so including it would
#: compress the ratio between them without changing what either system costs.
CONSUMER_PROMPT_OVERHEAD_TOKENS = 125

#: Mem0's own default, read from `inspect.signature(Memory.search)` rather than
#: chosen here. Printed with every Mem0 figure: their cost is a property of their
#: settings, and a number measured at ours would be ours wearing their name.
MEM0_TOP_K = 20


def load_corpus(name: str) -> tuple[list, dict]:
    """(questions, session pool) in one shape for both corpora, verified before it is read.

    The two corpora are pinned differently and that difference is worth stating rather than
    hiding behind a common loader: `longmemeval_oracle` is third-party, so `corpus_pin` carries
    its sha256, licence and URL; `code_sessions_v1.json` is this project's own and lives IN GIT,
    so its provenance is a commit -- stronger than a digest in a file, because the digest proves
    the bytes and the commit proves where they came from. It also records `generator_model` and
    `seed`, so it is reproducible rather than merely identified.
    """
    if name == "longmemeval_oracle":
        corpus_pin.verify(name)                      # before reading a byte
        data, pool = le.load()
        rows = [{"question_id": e.get("question_id"), "question": e["question"],
                 "gold": {str(s) for s in (e.get("answer_session_ids") or [])}} for e in data]
        return rows, pool
    if name == "code_sessions_v1":
        path = HERE / "data" / "code_sessions_v1.json"
        if not path.exists():
            raise FileNotFoundError(f"{path} is missing - it is tracked in git; check the tree")
        raw = json.loads(path.read_text(encoding="utf-8"))
        pool, rows = {}, []
        for proj in raw["projects"]:
            for s in proj["sessions"]:
                pool[s["id"]] = s["text"]
            for q in proj["questions"]:
                gold = q.get("gold_sessions")
                if isinstance(gold, str):            # the corpus stores these as repr'd lists
                    try:
                        gold = ast.literal_eval(gold)
                    except (ValueError, SyntaxError):
                        gold = []
                #: `markers` carries the answer as TEXT ("60 seconds", "60s"), and that is what
                #: reachability has to look for. The first version of this stand searched the
                #: injected payload for the gold SESSION ID and got 0 of 20 - of course it did:
                #: the payload carries notes, and a note never quotes the id of the session it
                #: came from. The column read "we inject and never deliver", which was a
                #: property of the question I asked, not of the system (measured 2026-09-22).
                markers = q.get("markers")
                if isinstance(markers, str):
                    try:
                        markers = ast.literal_eval(markers)
                    except (ValueError, SyntaxError):
                        markers = []
                rows.append({"question_id": q["id"], "question": q["question"],
                             "gold": {str(g) for g in (gold or [])},
                             "markers": [str(x) for x in (markers or [])]})
        return rows, pool
    raise KeyError(f"unknown corpus {name!r}; have {', '.join(CORPORA)}")


def tokenizers() -> dict:
    """Every tokenizer this machine can offer, by name and version.

    A dict rather than a choice: naming one "the" tokenizer would make the published ratio a
    property of that choice, and the choice is the owner's to make, not this stand's. Whatever
    is here is measured; whatever is absent is recorded as absent.
    """
    out: dict = {}
    try:
        import tiktoken  # noqa: PLC0415
        for enc_name in ("cl100k_base", "o200k_base"):
            try:
                enc = tiktoken.get_encoding(enc_name)
            except Exception:
                continue
            out[f"tiktoken/{enc_name}"] = {
                "version": getattr(tiktoken, "__version__", "?"),
                "encode": (lambda e: (lambda s: len(e.encode(s))))(enc),
            }
    except ImportError:
        pass
    return out


def _count(text: str, toks: dict) -> dict:
    """Characters, then one token count per named tokenizer. Characters never omitted."""
    row = {"chars": len(text)}
    for name, spec in toks.items():
        try:
            row[f"tokens[{name}]"] = spec["encode"](text)
        except Exception as exc:                                    # noqa: BLE001
            row[f"tokens[{name}]"] = None
            row.setdefault("_errors", {})[name] = f"{type(exc).__name__}: {exc}"
    return row


def _ingest(pool: dict, cap: int | None, project: str) -> dict:
    """Put the corpus sessions into the sandbox store, the way the engine would.

    Returns a blocker dict instead of raising when the write path cannot run here, because a
    stand that dies on a missing backend tells the reader less than one that says which
    backend was missing.
    """
    items = list(pool.items())
    if cap:
        items = items[:cap]
    if not items:
        return {"blocked": "empty session pool - refusing to measure over nothing"}
    written = 0
    #: The write side of the cost, taken from the model's own counters rather than estimated.
    #: `_LLM_STATS` carries `prompt_tokens` and `eval_tokens` as the backend reported them, so
    #: for this half no tokenizer has to be chosen at all: the number is what was paid for, not
    #: a reconstruction of it. Half of the cost of ownership is `read x turns + write x sessions`
    #: and the second half is measured nowhere -- not here, not for any competitor.
    stats0 = dict(getattr(m, "_LLM_STATS", {}) or {})
    #: Proposed against written, because they differ and the difference decides the reading:
    #: "the extractor produced nothing" is a regime and "it produced and the write path refused"
    #: is a defect, and the written counters alone print the same for both.
    proposed = {"pattern": 0, "mistake": 0, "decision": 0}
    refused = {"pattern": 0, "mistake": 0, "decision": 0}
    t0 = time.time()
    for sid, text in items:
        try:
            #: `api.capture_session`, not `memory_hook.capture_session`: the write path is on
            #: the package surface and the hook module does not carry it. Written the other way
            #: first, from memory of where it lives; the name check against the source caught it
            #: before the first run, which is the whole reason that check exists.
            res = api.capture_session(text, project=project, session_id=sid)
        except Exception as exc:                                    # noqa: BLE001
            return {"blocked": f"capture_session failed on {sid}: {type(exc).__name__}: {exc}",
                    "written": written}
        #: From the RETURN, never from the log line: the log prints refusals only when there
        #: are some, so "no line" would become evidence again - the shape this stand exists to
        #: avoid. The return carries both numbers on every call, including zero.
        for key, acc in (("proposed", proposed), ("refused", refused)):
            for kind in acc:
                acc[kind] += int((res.get(key) or {}).get(kind, 0) or 0)
        written += 1
    #: The population this stand can pass over silently, and the reason it must not.
    #: An empty store injects nothing, so the session-start payload is 0 characters and every
    #: per-turn payload is 0 - which prints identically to "this system is free". Measured on
    #: the first run of this stand, 2026-09-22: three LongMemEval sessions ingested, three
    #: session notes written, `P=0 M=0 D=0` typed notes, and the arm reported a cost of zero
    #: with no hint that it had measured an empty store. The third mechanism of the night's
    #: catalogue, in the stand written to avoid it: "zero offenders" and "zero examined" print
    #: the same. So the typed-note count is asserted, not assumed, and a store with none is a
    #: blocker rather than a number.
    typed = 0
    store = Path(sandbox_guard.store())
    for folder in ("Mistakes", "Patterns", "Decisions"):
        d = store / folder
        if d.exists():
            typed += sum(1 for p in d.rglob("*.md") if "Superseded" not in p.parts)
    stats1 = dict(getattr(m, "_LLM_STATS", {}) or {})
    write_cost = {k: stats1.get(k, 0) - stats0.get(k, 0)
                  for k in ("prompt_tokens", "eval_tokens", "ollama", "cloud", "fail")}
    #: No blocker here any more, and that is the point of `proposed`. A store with no typed
    #: notes is reported, not refused, PROVIDED the reason travels with it: `proposed` all
    #: zero says the extractor produced nothing (a regime, and an honest "costs nothing, helps
    #: nothing" row), while `proposed` non-zero with `written` zero says the write path refused
    #: it (a defect, and the same row would be selling a bug as a design). The refusal that
    #: remains lives at the printing site: a zero cost may be printed only beside both numbers.
    return {"written": written, "typed_notes": typed,
            "proposed": proposed, "refused": refused,
            "write_cost_tokens": write_cost,
            "seconds": round(time.time() - t0, 1)}


def _injection_text(cwd: str) -> str:
    """The SessionStart payload the engine would actually emit, captured rather than rebuilt.

    Captured from the real emitter instead of reassembling its parts: a reimplementation here
    would be a second source of truth that agrees on the day it is written and drifts after,
    which is the defect class this repository spent a night cataloguing.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            m.emit_session_start_context(cwd)
        except Exception as exc:                                    # noqa: BLE001
            return f"__ERROR__{type(exc).__name__}: {exc}"
    return buf.getvalue()


def _per_turn_text(cwd: str, prompt: str, session_id: str) -> str:
    """The UserPromptSubmit payload for one turn. Zero unless a guard fires, by design."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            m.emit_prompt_recall(cwd, prompt, session_id)
        except Exception as exc:                                    # noqa: BLE001
            return f"__ERROR__{type(exc).__name__}: {exc}"
    return buf.getvalue()


def run_nevertwice(data, pool, toks, cap) -> dict:
    """Session-start cost once, per-turn cost per question, and whether the answer is reachable."""
    #: The cwd is chosen BEFORE the ingest, and the project name is DERIVED from it rather than
    #: chosen beside it. Written the other way first -- notes under a project called
    #: "tokenfloor", injection asked from a directory named `tokenfloor` -- and the payload came
    #: back empty because `derive_project_from_cwd` walks up to the repository root and answers
    #: "nevertwice", not the leaf. Two names that agreed in my head and never in the engine.
    #: Asking the engine which project this directory is, instead of telling it, removes the
    #: possibility: there is one name now, and it comes from the thing that will use it.
    cwd_dir = ROOT / ".loop" / "token_floor_cwd" / "tokenfloor"
    cwd_dir.mkdir(parents=True, exist_ok=True)
    cwd = str(cwd_dir)
    if not m.is_tracked_project(cwd):
        return {"blocked": (
            f"is_tracked_project({cwd!r}) is False, so the session-start emitter returns before "
            f"assembling anything and any payload this arm reports would be a property of the "
            f"harness, not of the system. Configure a project root the engine tracks.")}
    project = m.derive_project_from_cwd(cwd)
    ing = _ingest(pool, cap, project)
    if "blocked" in ing:
        return ing
    start = _injection_text(cwd)
    if start.startswith("__ERROR__"):
        return {"blocked": f"session-start injection did not run: {start[9:]}"}
    #: The zero this stand could still print wrongly, and the rule that catches it.
    #: "A zero cost may only be printed beside `answer_reachable` and `proposed`" is necessary
    #: and NOT sufficient: measured 2026-09-22, the code-sessions corpus wrote 10 typed notes
    #: and the session-start payload was still 0 characters, with both explaining numbers
    #: present. The cause was this stand, not the cost -- `is_tracked_project()` excludes
    #: transient paths by design, and the sandbox store lives in Temp, so the emitter returned
    #: before assembling anything. A store with notes and an empty payload is a contradiction:
    #: it says the READING path did not run, which is not a fact about price.
    if ing.get("typed_notes", 0) > 0 and not start.strip():
        #: Every clause here is MEASURED at the moment of refusing, not asserted. The first
        #: version of this message named `is_tracked_project` as the cause in fixed text, and
        #: kept naming it after that cause was fixed and a different one took over - a refusal
        #: that lies about its reason is worse than a number, because it sends the next reader
        #: to the wrong place with confidence. Same defect class as everything else tonight,
        #: found in the refusal written to catch it (auditing session, 2026-09-22).
        probe = {
            "is_tracked_project": bool(m.is_tracked_project(cwd)),
            "derived_project": m.derive_project_from_cwd(cwd),
            "project_written_as": project,
            "context_file_exists": (Path(sandbox_guard.store()) / "Context" /
                                    f"{m.derive_project_from_cwd(cwd)}.md").exists(),
            "inject_context_on": bool(getattr(m, "INJECT_CONTEXT", False)),
        }
        return {"blocked": (
            f"{ing['typed_notes']} typed note(s) in the store and a 0-character session-start "
            f"payload: the reading path produced nothing, so this is not a cost of zero. "
            f"Measured at the point of refusal: {probe}"), "ingest": ing, "probe": probe}
    rows = []
    for e in data:
        turn = _per_turn_text(cwd, e["question"], "tokenfloor-session")
        if turn.startswith("__ERROR__"):
            return {"blocked": f"per-turn injection did not run: {turn[9:]}"}
        row = _count(turn, toks)
        row["question_id"] = e.get("question_id")
        #: "found" asks of the TEXT INJECTED, not of a ranker: the claim is about what the
        #: model can see, and a hit that never reached the prompt did not help the answer.
        #: Reachability asks of the TEXT INJECTED, and it asks for the ANSWER, not for the id
        #: of the session the answer came from. Where the corpus gives answer markers, they are
        #: the question; where it gives only session ids, the column says so rather than
        #: reporting a zero it cannot justify - a reachability of 0 that comes from asking the
        #: wrong string is indistinguishable from a system that delivers nothing.
        mk = e.get("markers") or []
        both = start + "\n" + turn
        if mk:
            row["answer_reachable"] = any(x and x.lower() in both.lower() for x in mk)
        elif e["gold"]:
            row["answer_reachable"] = any(g in both for g in e["gold"])
        else:
            row["answer_reachable"] = None      # nothing to ask with; not a zero
        rows.append(row)
    if not rows:
        return {"blocked": "no questions - refusing to report over an empty set"}
    return {
        "ingest": ing,
        "session_start": _count(start, toks),
        "per_turn": {
            "n": len(rows),
            "median_chars": sorted(r["chars"] for r in rows)[len(rows) // 2],
            "turns_that_injected": sum(1 for r in rows if r["chars"] > 0),
            "answer_reachable": sum(1 for r in rows if r["answer_reachable"]),
            "reachability_unanswerable": sum(1 for r in rows
                                             if r["answer_reachable"] is None),
        },
        "rows": rows,
    }


def run_mem0(data, pool, toks, cap) -> dict:
    """What Mem0 would put in the prompt for each query: its search payload, serialised.

    Runs only in the competitor venv. The serialisation is Mem0's own `memory` strings joined
    by newlines - what an integration passes to the model - and not the full JSON envelope,
    which would count transport rather than payload and overstate its cost.
    """
    try:
        from mem0 import Memory  # noqa: PLC0415
    except ImportError:
        return {"blocked": "mem0 not installed here - run this arm in the competitor venv"}
    try:
        import head_to_head as h2h  # noqa: PLC0415
        cfg_store = h2h._bench_dir() / "qdrant_token_floor"
        import shutil  # noqa: PLC0415
        shutil.rmtree(cfg_store, ignore_errors=True)
        mem = Memory.from_config({
            "llm": {"provider": "ollama", "config": {
                "model": h2h.COMP_LLM, "ollama_base_url": h2h.OLLAMA_BASE, "temperature": 0.0}},
            "embedder": {"provider": "ollama", "config": {
                "model": h2h.EMBED_MODEL, "ollama_base_url": h2h.OLLAMA_BASE,
                "embedding_dims": 1024}},
            "vector_store": {"provider": "qdrant", "config": {
                "path": str(cfg_store), "embedding_model_dims": 1024, "on_disk": True}},
        })
    except Exception as exc:                                        # noqa: BLE001
        return {"blocked": f"Mem0 init failed ({type(exc).__name__}: {exc})"}
    items = list(pool.items())[:cap] if cap else list(pool.items())
    if not items:
        return {"blocked": "empty session pool - refusing to measure over nothing"}
    t0 = time.time()
    for sid, text in items:
        try:
            mem.add(text, user_id="tf", metadata={"session_id": sid}, infer=False)
        except Exception as exc:                                    # noqa: BLE001
            return {"blocked": f"mem0.add failed on {sid}: {type(exc).__name__}: {exc}"}
    ingest_s = round(time.time() - t0, 1)
    rows = []
    for e in data:
        try:
            #: `top_k`, not `limit`. Mem0 2.0.19 takes `top_k=20` and swallows `limit`
            #: into `**kwargs` without a word: measured, `limit=1` and `limit=10` both return
            #: 20 rows, `top_k=1` returns 1. Three stands in this repository pass `limit` and
            #: have been getting 20 all along. Left at Mem0's OWN default and printed beside
            #: the cost as a condition, so the number belongs to (Mem0, its settings) rather
            #: than to (Mem0, our choice) - a cost measured at a limit we imposed would be our
            #: number wearing their name.
            res = mem.search(e["question"], filters={"user_id": "tf"}, top_k=MEM0_TOP_K)
        except Exception as exc:                                    # noqa: BLE001
            return {"blocked": f"mem0.search failed: {type(exc).__name__}: {exc}"}
        hits = res.get("results", res) if isinstance(res, dict) else res
        payload = "\n".join(str(h.get("memory", "")) for h in hits)
        row = _count(payload, toks)
        row["question_id"] = e.get("question_id")
        row["hits"] = len(hits)
        row["top_k"] = MEM0_TOP_K
        gold = e["gold"]
        row["answer_reachable"] = bool(gold) and any(
            g == str((h.get("metadata") or {}).get("session_id")) for h in hits for g in gold)
        rows.append(row)
    if not rows:
        return {"blocked": "no questions - refusing to report over an empty set"}
    return {
        "ingest": {"written": len(items), "seconds": ingest_s},
        "session_start": _count("", toks),          # Mem0 injects nothing at session start
        "per_turn": {
            "n": len(rows),
            "median_chars": sorted(r["chars"] for r in rows)[len(rows) // 2],
            "turns_that_injected": sum(1 for r in rows if r["chars"] > 0),
            "answer_reachable": sum(1 for r in rows if r["answer_reachable"]),
            "reachability_unanswerable": sum(1 for r in rows
                                             if r["answer_reachable"] is None),
        },
        "rows": rows,
    }


def curve(arms: dict, toks: dict) -> dict:
    """Total cost for a session of N turns, per arm, per unit. The point of the whole stand.

    `session_start + N * per_turn` is the arithmetic, and it is written out per N rather than
    reduced to one ratio because the ratio is a function of N: a system that pays once and a
    system that pays per query cannot be compared at an unstated N without the comparison
    being a choice dressed as a measurement.
    """
    units = ["chars"] + [f"tokens[{k}]" for k in toks]
    out: dict = {}
    for unit in units:
        per_n = {}
        for n in TURNS:
            row = {}
            for arm, res in arms.items():
                if "blocked" in res:
                    row[arm] = None
                    continue
                start = res["session_start"].get(unit)
                if start is None:
                    row[arm] = None
                    continue
                #: The MEAN, not the median, and the difference is the whole curve. Cost over N
                #: turns is a SUM, and the statistic that predicts a sum is the mean; the median
                #: predicts a typical turn and says nothing about the total. Measured
                #: 2026-09-22 on 50 questions: our per-turn median is 0 because the path fires
                #: on 6 turns of 50, while the mean is 97. Built on the median this curve read
                #: "our cost never grows" and made us 668x cheaper at N=100; built on the mean
                #: it is 101x. Six-fold, and in OUR favour - which is exactly the direction a
                #: number has to be checked in hardest.
                vals = [r.get(unit) or 0 for r in res["rows"]]
                mean = sum(vals) / len(vals) if vals else 0
                row[arm] = round(start + n * mean)
            per_n[str(n)] = row
        out[unit] = per_n
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default="nevertwice",
                    help="comma list of arms to run: nevertwice,mem0")
    ap.add_argument("--limit", type=int, default=0, help="first N questions (a smoke run)")
    ap.add_argument("--sessions", type=int, default=0, help="cap ingested sessions (testing only)")
    ap.add_argument("--save", action="store_true", help="write the artifact")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)

    toks = tokenizers()
    if not toks:
        print("no tokenizer available - characters would be the only unit; refusing to publish "
              "a cost claim with no token column. `pip install tiktoken`", file=sys.stderr)
        return 2

    wanted = [x.strip() for x in a.only.split(",") if x.strip()]
    runners = {"nevertwice": run_nevertwice, "mem0": run_mem0}
    unknown = [w for w in wanted if w not in runners]
    if unknown:
        print(f"unknown arm(s): {', '.join(unknown)}; have {', '.join(runners)}", file=sys.stderr)
        return 2

    corpora: dict = {}
    for cname in CORPORA:
        try:
            data, pool = load_corpus(cname)
        except Exception as exc:                                    # noqa: BLE001
            corpora[cname] = {"blocked": f"{type(exc).__name__}: {exc}"}
            continue
        if a.limit:
            data = data[:a.limit]
        if not data:
            corpora[cname] = {"blocked": "no questions after --limit - nothing to measure over"}
            continue
        arms = {name: runners[name](data, pool, toks, a.sessions or None) for name in wanted}
        corpora[cname] = {
            "questions": len(data), "pool_sessions": len(pool),
            "provenance": (corpus_pin.record(cname) if cname in corpus_pin.CORPORA
                           else {"corpus": cname, "provenance": "tracked in git; see code_sha"}),
            "arms": arms,
            "curve": curve(arms, toks),
        }

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                          capture_output=True, text=True).stdout.strip() or None
    result = {
        "code_sha": head,
        "corpora": corpora,
        "tokenizers": {k: v["version"] for k, v in toks.items()},
        "consumer_prompt_overhead_tokens": CONSUMER_PROMPT_OVERHEAD_TOKENS,
        "consumer_prompt_overhead_note":
            "excluded from every arm: it is the agent's template, identical for all arms, and "
            "including it compresses the ratio between them without changing either cost",
        "inject_budget_chars": getattr(m, "INJECT_BUDGET_CHARS", None),
    }

    for cname, cres in corpora.items():
        if "blocked" in cres:
            print(f"\n{cname}: BLOCKED: {cres['blocked']}")
            continue
        print(f"\n{cname}: {cres['questions']} questions, {cres['pool_sessions']} sessions")
        for name, res in cres["arms"].items():
            if "blocked" in res:
                print(f"  {name:12s} BLOCKED: {res['blocked']}")
                continue
            pt, ing = res["per_turn"], res.get("ingest", {})
            start = res["session_start"]["chars"]
            prop = sum((ing.get("proposed") or {}).values())
            refu = sum((ing.get("refused") or {}).values())
            #: THE refusal, and it lives here rather than in the arm: a zero cost may be
            #: printed only beside the two numbers that explain it. Without them "0 chars"
            #: reads as "this system is free" whether the store was empty, the extractor
            #: silent, or the write path refusing - three different facts, one line.
            if start == 0 and pt["median_chars"] == 0 and (
                    "answer_reachable" not in pt or ing.get("proposed") is None):
                print(f"  {name:12s} REFUSED to print a zero cost: nothing beside it explains "
                      f"the zero (need both `answer_reachable` and `proposed`)")
                continue
            print(f"  {name:12s} session start {start:6d} chars   "
                  f"per turn median {pt['median_chars']:5d} chars   "
                  f"injected on {pt['turns_that_injected']}/{pt['n']} turns   "
                  f"answer reachable {pt['answer_reachable']}/{pt['n']}")
            if ing:
                wc = ing.get("write_cost_tokens") or {}
                verdict = ("extractor produced nothing - a regime, not a refusal"
                           if prop == 0 else
                           f"extractor proposed {prop}, write path refused {refu}")
                print(f"  {'':12s}   write: {ing.get('written', 0)} session(s), "
                      f"{ing.get('typed_notes', 0)} typed note(s), "
                      f"{wc.get('prompt_tokens', 0)}+{wc.get('eval_tokens', 0)} tokens "
                      f"(model's own count) - {verdict}")
        live = [k for k, v in cres["arms"].items() if "blocked" not in v]
        if len(live) >= 2:
            print("  total chars for a session of N turns:")
            for n in TURNS:
                row = cres["curve"]["chars"][str(n)]
                cells = "   ".join(f"{k} {row[k]}" for k in live if row.get(k) is not None)
                print(f"    N={n:4d}   {cells}")
        elif live:
            print(f"  only one arm ran ({live[0]}); a curve needs two, so none is printed - "
                  f"a one-armed cost is a number, not a comparison")

    if a.save:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        #: MERGE AT WRITE TIME, the shape `head_to_head.py` uses, and here it is not a
        #: convenience: the two arms cannot run in one process. Ours needs this interpreter,
        #: Mem0's needs the polygon venv, so a single run can only ever hold one arm and the
        #: curve -- the entire point of this stand -- would never be printed.
        #:
        #: Two guards, because merging is how a stale number survives a change it should not
        #: have survived. A run refuses to merge into a file written at a DIFFERENT code_sha
        #: (two arms measured on two engines are not a comparison), and it records which run
        #: contributed which arm, so "when was this measured" has an answer per arm.
        merged_from = []
        if out.exists():
            try:
                prev = json.loads(out.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"refusing to merge into {out}: unreadable ({type(exc).__name__})",
                      file=sys.stderr)
                return 2
            if prev.get("code_sha") and head and prev["code_sha"] != head:
                print(f"refusing to merge: {out.name} was written at {prev['code_sha'][:8]}, "
                      f"this run is at {head[:8]} - two arms measured on two engines are not "
                      f"a comparison. Delete the file to start a fresh pair.", file=sys.stderr)
                return 2
            for cname, cres in (prev.get("corpora") or {}).items():
                keep = {k: v for k, v in (cres.get("arms") or {}).items() if k not in wanted}
                if keep:
                    result["corpora"].setdefault(cname, cres).setdefault("arms", {}).update(keep)
            merged_from = list(prev.get("measured_by") or [])
        result["measured_by"] = merged_from + [
            {"arms": wanted, "code_sha": head, "python": sys.version.split()[0],
             "at": time.strftime("%Y-%m-%dT%H:%M:%S")}]
        for cres in result["corpora"].values():
            if "arms" in cres:
                cres["curve"] = curve(cres["arms"], toks)
        out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
        present = sorted({a for c in result["corpora"].values() for a in (c.get("arms") or {})})
        print(f"\nwrote {out}  (arms present: {', '.join(present)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
