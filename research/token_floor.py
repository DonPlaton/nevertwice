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
    python research/token_floor.py --dry                  # stub path, no model, no store beyond
                                                            # a throwaway sandbox - proves the
                                                            # STAND's own plumbing (fixes 1/3/4/5/7)
    python research/token_floor.py --save                # full, writes the artifact
    MEM0_TELEMETRY=False <mem0 venv python> research/token_floor.py --only mem0 --save

An arm that cannot run here records a blocker string with the reason. It never records a
number it did not measure.

## v2 (2026-09-23 review): nine fixes, block A re-run

The first cut's zeros were a property of the STAND, not the product (`.loop/TF-ANALYSIS.md`,
verified independently). Nine fixes, listed here so the CHANGE from v1 is legible in one place
rather than scattered across nine docstrings:

  1. `session_id` is now `f"tokenfloor-{question_id}"` (was one shared id for every question,
     which let the injection cap - PROMPT_RECALL_MAX_PER_SESSION - silently exhaust itself on
     the first six questions and read 0 for the rest); AND each CORPUS gets a fresh, empty
     sandbox store (`_rebase_vault`), so the second corpus's cap state and notes cannot be the
     first corpus's leftovers.
  2. The curve is labelled EXPLICITLY, per `curve_model` in the artifact: "N independent
     single-turn sessions" - total = N * (session_start + mean_per_turn) - is what fix 1's
     methodology actually measures (every row is turn 1 of its OWN fresh session_id), so that
     is what is reported; the old `start + N*mean` formula matched neither that model nor a
     single N-turn session capped at 6 (see the module docstring below `curve()`).
  3. Every row now carries `reason` (cap/trivial/untracked/no_hits/injected), `hit_stems` (+
     `hit_scores` where the SQLite index path is live), `semantic_ran`, and
     `raw_additional_context` (the actual injected text, not just its length).
  4. ONE reachability definition for both arms and both corpora: a marker's TEXT as a whole
     word in the per-TURN injected text only (never session-start + turn combined, and never a
     session id). On the oracle the marker is LongMemEval's own `answer` field.
  5. `code_sessions_v1`'s 30 projects are ingested and queried EACH FROM ITS OWN tracked
     directory (a `.git` marker per project) instead of one shared "tokenfloor" project; Mem0
     gets a `user_id` per project too.
  6. Mem0's `infer=False` is named in the same printed line as its cost.
  7. Every char/token count is of the PARSED `additionalContext` string, never the JSON
     envelope (whose keys/quoting/escapes inflated the old count 5-8%).
  8. `fallback: "lexical-only"` is logged per row whenever the embedder was not reachable for
     that turn (`semantic_ran: false`).
  9. `marker_freq_in_session_start` is reported per marker per row - a marker that is already a
     common word in the session-start card would read as "reachable" regardless of whether the
     relevant note actually injected, so its background frequency has to be visible.

Existing registered claims' pointers may not resolve after this rewrite - block A is re-run in
v2 and the claims get re-registered; the pointers that changed shape are listed in the
implementing session's report, not repeated here.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
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
import _ollama_pacer as pacer  # noqa: E402 - R-v2-ports: pace/retry/count every arm's traffic

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

#: Fix 3: the exhaustive set of reasons a per-turn row can have. `injected` and `no_hits` are
#: read off the actual output (nothing upstream can tell them apart without running retrieval);
#: the other three are read BEFORE the real emission, by replicating emit_prompt_recall's own
#: early-exit gates read-only (`_classify_pre_reason`) - so the reason is KNOWN, not inferred
#: from an empty string that could mean four different things.
REASONS = ("cap", "trivial", "untracked", "no_hits", "injected")


def load_corpus(name: str) -> tuple[list, dict, dict | None]:
    """(questions, session pool, project groups) in one shape for both corpora, verified
    before it is read.

    The two corpora are pinned differently and that difference is worth stating rather than
    hiding behind a common loader: `longmemeval_oracle` is third-party, so `corpus_pin` carries
    its sha256, licence and URL; `code_sessions_v1.json` is this project's own and lives IN GIT,
    so its provenance is a commit -- stronger than a digest in a file, because the digest proves
    the bytes and the commit proves where they came from. It also records `generator_model` and
    `seed`, so it is reproducible rather than merely identified.

    `groups` (fix 5): None for the oracle (one implicit project - LongMemEval has no project
    structure); for code_sessions, {project_slug: {"session_ids": [...], "question_ids": [...]}}
    - the corpus's own 30-project structure, preserved instead of flattened into one pool the
    way v1 did (F6/F7: all 30 projects landed in ONE tracked project, which is why identical
    questions with different answers could not be told apart and same-slug retirement acted
    across projects that share nothing).
    """
    if name == "longmemeval_oracle":
        corpus_pin.verify(name)                      # before reading a byte
        data, pool = le.load()
        rows = [{"question_id": e.get("question_id"), "question": e["question"],
                 #: fix 4 (auditor's addition): the oracle's reachability marker is the TEXT of
                 #: LongMemEval's own `answer` field, not the gold session id - a note never
                 #: quotes the id of the session it came from, so asking for one is asking a
                 #: question retrieval cannot answer by construction (F3, TF-ANALYSIS.md).
                 "answer": e.get("answer"),
                 "gold": {str(s) for s in (e.get("answer_session_ids") or [])}} for e in data]
        return rows, pool, None
    if name == "code_sessions_v1":
        path = HERE / "data" / "code_sessions_v1.json"
        if not path.exists():
            raise FileNotFoundError(f"{path} is missing - it is tracked in git; check the tree")
        raw = json.loads(path.read_text(encoding="utf-8"))
        pool, rows, groups = {}, [], {}
        for proj in raw["projects"]:
            pslug = m.slug_project(proj.get("slug") or proj.get("id") or "project")
            sess_ids: list = []
            q_ids: list = []
            for s in proj["sessions"]:
                pool[s["id"]] = s["text"]
                sess_ids.append(s["id"])
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
                             "markers": [str(x) for x in (markers or [])],
                             "project": pslug})
                q_ids.append(q["id"])
            groups[pslug] = {"session_ids": sess_ids, "question_ids": q_ids}
        return rows, pool, groups
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


# ── fix 7: parse the envelope, count the payload ────────────────────────────────────────────

def _parse_additional_context(raw: str) -> str:
    """The engine's stdout is a JSON envelope
    (`{"hookSpecificOutput": {"hookEventName": ..., "additionalContext": <text>}}`) - fix 7:
    every char/token count in this stand is of the PARSED `additionalContext` string, never
    the envelope. The envelope's own keys, quoting and `\\uXXXX` escapes (emoji: `\U0001f9e0`)
    inflated the count 5-8% (measured, TF-ANALYSIS.md F10) - a real cost difference wearing a
    parsing bug's clothes.

    "" for an envelope with no payload (nothing was injected - the emitter printed nothing at
    all, which is valid and not an error) or one this stand cannot parse - recorded as a
    visible parse failure, never silently substituted with the raw text, which would
    reintroduce exactly the defect this fix removes."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("__ERROR__"):
        return raw
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return f"__PARSE_ERROR__ could not parse as JSON: {raw[:120]!r}"
    return str((payload.get("hookSpecificOutput") or {}).get("additionalContext") or "")


# ── fix 4/9: one whole-word marker definition, and its background frequency ────────────────

def _whole_word_present(marker: str, text: str) -> bool:
    """Fix 4: a marker counts only as a WHOLE WORD - "60" must not match inside "1960", and a
    multi-word marker ("60 seconds") is bounded at its own start and end, not internally."""
    marker = (marker or "").strip()
    if not marker or not text:
        return False
    try:
        return re.search(r"\b" + re.escape(marker) + r"\b", text, re.IGNORECASE) is not None
    except re.error:
        return marker.lower() in text.lower()          # a marker with no word chars at all


def _marker_freq(marker: str, text: str) -> int:
    """Fix 9: how often a marker already occurs in `text` as a whole word - printed per row so
    a marker that is already a common word in the session-start card cannot pass for evidence
    that the SPECIFIC relevant note was the thing that made it reachable."""
    marker = (marker or "").strip()
    if not marker or not text:
        return 0
    try:
        return len(re.findall(r"\b" + re.escape(marker) + r"\b", text, re.IGNORECASE))
    except re.error:
        return text.lower().count(marker.lower())


def _row_markers(corpus_name: str, e: dict) -> list[str]:
    """The SAME kind of value for both corpora (fix 4): a list of ANSWER-TEXT strings to look
    for as whole words in the injected text, never a session id. On the oracle this is
    LongMemEval's own `answer` field (auditor's addition, 2026-09-23); code_sessions already
    carries its own `markers` list, unchanged in source, only in how they are matched."""
    if corpus_name == "longmemeval_oracle":
        ans = e.get("answer")
        return [str(ans)] if ans else []
    return [x for x in (e.get("markers") or []) if x]


# ── fix 3: the reason a row did or did not inject ───────────────────────────────────────────

def _classify_pre_reason(cwd: str, prompt: str, session_id: str) -> str | None:
    """Read-only replication of `emit_prompt_recall`'s own early-exit gates (`_engine_hooks.py`),
    called BEFORE the real emission - so a row's reason is KNOWN, not guessed from an empty
    string afterwards. `None` means none of the early gates applied; the caller then reads
    `injected` vs `no_hits` off the actual output, which is the only way to tell those two
    apart (they both depend on what retrieval found, which this function does not run).

    Every check here calls the ENGINE's own function (`m.is_tracked_project`,
    `m._is_trivial_prompt`, `m._load_prompt_recall_state`) rather than re-deriving the
    condition - a stand that reimplements the gate can drift from the gate it is reporting on,
    which is the defect class the auditing session spent a night cataloguing."""
    if not (m.PROMPT_RECALL_ENABLED and m.INJECT_CONTEXT) or not m.is_tracked_project(cwd):
        return "untracked"
    if m._is_trivial_prompt(prompt):
        return "trivial"
    state = m._load_prompt_recall_state(session_id)
    if m.PROMPT_RECALL_MODE == "once" and state.get("count", 0) >= 1:
        return "cap"
    if state.get("count", 0) >= m.PROMPT_RECALL_MAX_PER_SESSION:
        return "cap"
    return None


def _diagnose(project: str, prompt: str, cache) -> tuple[list, dict, bool]:
    """Read-only probe alongside the real emission: hit STEMS (fix 3), a fused SCORE where
    available (fix 3), and whether the semantic arm ran (fix 3/8). Never called when a pre-
    reason already fully explains an empty row (untracked/trivial/cap) - it would only cost a
    call the real gate already skipped, for a reason that is already known.

    `retrieve_relevant`'s own public hits carry no score field (`_hit()`, `_engine_recall.py`
    - `{ntype, title, stem, recurrence}`), so a fused score is genuinely not always available;
    `index_sqlite.search` (a SEPARATE, also read-only path) is consulted for one, and when the
    scale index is not live this stays empty - the honest "not available" reading fix 3 asks
    for, not a fabricated number."""
    hits = m.retrieve_relevant(project, prompt, m.PROMPT_RECALL_K,
                               embed_timeout=m.PROMPT_RECALL_EMBED_TIMEOUT,
                               alive_timeout=m.PROMPT_RECALL_ALIVE_TIMEOUT, cache=cache,
                               recency_fallback=False)
    stems = [h.get("stem") for h in hits if h.get("stem")]
    scores: dict = {}
    try:
        idx = m._sibling("index_sqlite")
        results, mode = idx.search(prompt, project=project, k=m.PROMPT_RECALL_K)
        if mode != "no-index":
            scores = {r["stem"]: r.get("score") for r in results if r.get("stem")}
    except Exception:                                                # noqa: BLE001
        pass
    semantic_ran = bool(m.embedder_available(m.PROMPT_RECALL_ALIVE_TIMEOUT))
    return stems, {s: scores.get(s) for s in stems}, semantic_ran


def _make_cwd(name: str) -> str:
    """A dedicated, TRACKED directory for one project (fix 5): a bare `.git` marker - an empty
    directory is enough, `_find_repo_root` only checks existence, and `NEVERTWICE_TRACK_ANY_
    PROJECT` defaults on - directly INSIDE this project's own directory, never shared with a
    sibling. Two projects sharing an ancestor `.git` would both derive to the FIRST repo root
    found, which is exactly the F6/F7 bug ("all 30 projects went into ONE project") this fix
    removes; a `.git` at each project's OWN level means `_find_repo_root` stops there every
    time, regardless of what a shared parent directory does or does not have."""
    cwd_dir = ROOT / ".loop" / "token_floor_cwd" / name
    (cwd_dir / ".git").mkdir(parents=True, exist_ok=True)
    return str(cwd_dir)


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
    #: The rest of the gap between proposed and written, each with its own cause (review
    #: 2026-09-23, #2 and #12): the relevance gate, the W7 quarantine, a crash retry's skip.
    #: Without them an off-topic session reads as a refusing write path.
    quarantined = {"pattern": 0, "mistake": 0, "decision": 0}
    skipped = {"pattern": 0, "mistake": 0, "decision": 0}
    off_topic = {"pattern": 0, "mistake": 0, "decision": 0}
    #: A model that obeys the off-topic instruction returns empty lists, so `off_topic` above stays
    #: 0 and the zero reads as a regime. The session's own verdict is what separates the two.
    off_topic_sessions = 0
    not_stored = 0
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
        if res.get("relevant") is False:
            off_topic_sessions += 1
        #: an extraction that failed (process_session left it for retry) returns stored False and
        #: empty counts - the "zero examined reads as zero offenders" shape, so it is counted apart
        #: and named beside any zero (third review, 2026-09-23)
        if not res.get("stored"):
            not_stored += 1
        for key, acc in (("proposed", proposed), ("refused", refused), ("quarantined", quarantined),
                         ("skipped", skipped), ("off_topic", off_topic)):
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
            #: Quarantine/ is on disk for review and never served (W7) - counting it reported a
            #: quarantined, empty-for-recall store as holding typed notes (review 2026-09-23, R9)
            typed += sum(1 for p in d.rglob("*.md")
                         if not {"Superseded", "Quarantine"} & set(p.relative_to(d).parts))
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
            "proposed": proposed, "refused": refused, "quarantined": quarantined,
            "skipped": skipped, "off_topic": off_topic, "off_topic_sessions": off_topic_sessions,
            "not_stored": not_stored,
            "write_cost_tokens": write_cost,
            "seconds": round(time.time() - t0, 1)}


def _ingest_stub(pool: dict, cap: int | None, project: str) -> dict:
    """`--dry`'s stand-in for `_ingest`: writes one pattern note per session directly via
    `write_typed_note`, no model, no extractor. Exists ONLY to prove this STAND's own plumbing
    (fixes 1/3/4/5/7) end to end without an LLM - never used for a real measurement, whose
    whole point is what the REAL extractor does to real dialogue (the write-cost numbers this
    function reports are all zero/empty on purpose, so nobody mistakes them for a measurement)."""
    items = list(pool.items())
    if cap:
        items = items[:cap]
    if not items:
        return {"blocked": "empty session pool - refusing to measure over nothing"}
    written = 0
    for sid, text in items:
        title = f"note for {sid}"
        desc = (text or "")[:200] or f"a session about {sid}"
        stem = m.write_typed_note("Patterns", {"title": title, "description": desc},
                                  project, "2026-09-23", [], "pattern")
        if stem:
            m.update_embeddings([(stem, "pattern", project, title, desc, "")])
            written += 1
    typed = written
    zero3 = {"pattern": 0, "mistake": 0, "decision": 0}
    return {"written": written, "typed_notes": typed,
            "proposed": {"pattern": written, "mistake": 0, "decision": 0},
            "refused": dict(zero3), "quarantined": dict(zero3), "skipped": dict(zero3),
            "off_topic": dict(zero3), "off_topic_sessions": 0, "not_stored": 0,
            "write_cost_tokens": {}, "seconds": 0.0, "stub": True}


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


def run_nevertwice(data, pool, toks, cap, *, groups: dict | None = None,
                   dry: bool = False, corpus_name: str = "") -> dict:
    """Session-start cost once per project, per-turn cost per question, and whether the
    answer is reachable - now per project (fix 5) when `groups` is given.

    Fix 1: a FRESH, EMPTY sandbox store for this call - `_rebase_vault` to a new temp dir, so
    the injection cap's state (`.prompt_recall`) and the notes from a PRIOR corpus can never
    be this corpus's leftovers (measured 2026-09-22: code_sessions started with the cap
    already spent by the oracle corpus's first six rows, reading 0/420 for a reason that had
    nothing to do with code_sessions).
    """
    m._rebase_vault(Path(tempfile.mkdtemp(
        prefix=f"nevertwice_tokenfloor_{corpus_name or 'run'}_")))

    if groups is None:
        # The oracle: one implicit project, same shape everywhere else in this function.
        groups = {"tokenfloor": {"session_ids": list(pool.keys()),
                                 "question_ids": [e["question_id"] for e in data]}}

    zero3 = {"pattern": 0, "mistake": 0, "decision": 0}
    ing_total = {"written": 0, "typed_notes": 0, "proposed": dict(zero3), "refused": dict(zero3),
                "quarantined": dict(zero3), "skipped": dict(zero3), "off_topic": dict(zero3),
                "off_topic_sessions": 0, "not_stored": 0,
                "write_cost_tokens": {"prompt_tokens": 0, "eval_tokens": 0, "ollama": 0,
                                      "cloud": 0, "fail": 0},
                "seconds": 0.0, "projects": 0}
    rows: list = []
    session_start_rows: dict = {}   # project -> _count(...) of its own session-start payload

    by_qid = {e["question_id"]: e for e in data}
    for gname, g in groups.items():
        cwd = _make_cwd(gname)
        if not m.is_tracked_project(cwd):
            return {"blocked": (
                f"is_tracked_project({cwd!r}) is False for project {gname!r}, so the "
                f"session-start emitter returns before assembling anything and any payload "
                f"this arm reports would be a property of the harness, not of the system.")}
        project = m.derive_project_from_cwd(cwd)
        sub_pool = {sid: pool[sid] for sid in g["session_ids"] if sid in pool}
        ing = (_ingest_stub if dry else _ingest)(sub_pool, cap, project)
        if "blocked" in ing:
            ing["blocked"] = f"[{gname}] {ing['blocked']}"
            return ing
        ing_total["written"] += ing.get("written", 0)
        ing_total["typed_notes"] += ing.get("typed_notes", 0)
        ing_total["off_topic_sessions"] += ing.get("off_topic_sessions", 0)
        ing_total["not_stored"] += ing.get("not_stored", 0)
        ing_total["seconds"] += ing.get("seconds", 0.0)
        ing_total["projects"] += 1
        for key in ("proposed", "refused", "quarantined", "skipped", "off_topic"):
            for kind, val in (ing.get(key) or {}).items():
                ing_total[key][kind] = ing_total[key].get(kind, 0) + int(val or 0)
        for kind, val in (ing.get("write_cost_tokens") or {}).items():
            ing_total["write_cost_tokens"][kind] = ing_total["write_cost_tokens"].get(kind, 0) + int(val or 0)

        start_raw = _injection_text(cwd)
        if start_raw.startswith("__ERROR__"):
            return {"blocked": f"[{gname}] session-start injection did not run: {start_raw[9:]}"}
        start_text = _parse_additional_context(start_raw)          # fix 7
        if ing.get("typed_notes", 0) > 0 and not start_text.strip():
            #: Every clause here is MEASURED at the moment of refusing, not asserted. A refusal
            #: that names the wrong cause is worse than a number, because it sends the next
            #: reader to the wrong place with confidence (auditing session, 2026-09-22).
            probe = {
                "is_tracked_project": bool(m.is_tracked_project(cwd)),
                "derived_project": m.derive_project_from_cwd(cwd),
                "project_written_as": project,
                "context_file_exists": (Path(sandbox_guard.store()) / "Context" /
                                        f"{m.derive_project_from_cwd(cwd)}.md").exists(),
                "inject_context_on": bool(getattr(m, "INJECT_CONTEXT", False)),
            }
            return {"blocked": (
                f"[{gname}] {ing['typed_notes']} typed note(s) in the store and a 0-character "
                f"session-start payload: the reading path produced nothing, so this is not a "
                f"cost of zero. Measured at the point of refusal: {probe}"),
                "ingest": ing, "probe": probe}
        session_start_rows[gname] = _count(start_text, toks)

        cache = None if m.scale_index_ready() else m.load_embed_cache()
        for qid in g["question_ids"]:
            e = by_qid.get(qid)
            if e is None:
                continue
            sid = f"tokenfloor-{qid}"                                # fix 1
            prompt = e["question"]
            pre_reason = _classify_pre_reason(cwd, prompt, sid)
            raw_turn = _per_turn_text(cwd, prompt, sid)
            if raw_turn.startswith("__ERROR__"):
                return {"blocked": f"[{gname}] per-turn injection did not run: {raw_turn[9:]}"}
            turn_text = _parse_additional_context(raw_turn)          # fix 7
            if pre_reason:
                reason = pre_reason
                stems, scores, semantic_ran = [], {}, False
            elif turn_text.strip():
                reason = "injected"
                stems, scores, semantic_ran = _diagnose(project, prompt, cache)
            else:
                reason = "no_hits"
                stems, scores, semantic_ran = _diagnose(project, prompt, cache)

            markers = _row_markers(corpus_name, e)
            reachable = None
            if markers:
                #: fix 4: the PER-TURN text only, never session-start + turn - reachability is
                #: about whether THIS query's injection delivered the answer, and folding the
                #: session-start card in credited the system for content it may not have
                #: chosen to show this turn at all.
                reachable = any(_whole_word_present(mk, turn_text) for mk in markers)
            elif e["gold"]:
                reachable = None            # fix 4: a gold SESSION id is not an answer string;
                                             # this corpus/row gives nothing reachability can use

            row = {
                "question_id": qid, "project": gname,
                "reason": reason,                                    # fix 3
                "hit_stems": stems,                                  # fix 3
                "hit_scores": scores,                                # fix 3
                "semantic_ran": semantic_ran,                        # fix 3
                "fallback": None if semantic_ran else "lexical-only",  # fix 8
                "raw_additional_context": turn_text,                 # fix 3
                "marker_freq_in_session_start":                      # fix 9
                    {mk: _marker_freq(mk, start_text) for mk in markers},
                "answer_reachable": reachable,
                **_count(turn_text, toks),
            }
            rows.append(row)

    if not rows:
        return {"blocked": "no questions - refusing to report over an empty set"}
    #: One session-start figure for the whole corpus result: the MEAN across projects when
    #: there is more than one (code_sessions), or the single project's own count (the oracle).
    #: `curve()` needs one `session_start` per arm; per-project figures are still in `rows` via
    #: whichever project each row belongs to for a reader who wants the split.
    ss_counts = list(session_start_rows.values())
    session_start = {"chars": round(sum(r["chars"] for r in ss_counts) / len(ss_counts))}
    for name in toks:
        key = f"tokens[{name}]"
        vals = [r.get(key) for r in ss_counts if r.get(key) is not None]
        session_start[key] = round(sum(vals) / len(vals)) if vals else None
    return {
        "ingest": ing_total,
        "session_start": session_start,
        "session_start_by_project": session_start_rows,
        "per_turn": {
            "n": len(rows),
            "median_chars": sorted(r["chars"] for r in rows)[len(rows) // 2],
            "turns_that_injected": sum(1 for r in rows if r["chars"] > 0),
            "answer_reachable": sum(1 for r in rows if r["answer_reachable"]),
            "reachability_unanswerable": sum(1 for r in rows
                                             if r["answer_reachable"] is None),
            "by_reason": {reason: sum(1 for r in rows if r["reason"] == reason)
                         for reason in REASONS},
            "lexical_fallback": sum(1 for r in rows if r["fallback"] == "lexical-only"),
        },
        "rows": rows,
    }


def run_mem0(data, pool, toks, cap, *, groups: dict | None = None, corpus_name: str = "") -> dict:
    """What Mem0 would put in the prompt for each query: its search payload, serialised.

    Runs only in the competitor venv. The serialisation is Mem0's own `memory` strings joined
    by newlines - what an integration passes to the model - and not the full JSON envelope,
    which would count transport rather than payload and overstate its cost.

    Fix 5: `user_id` is per PROJECT for code_sessions (`groups`), matching the ingest boundary
    the Nevertwice arm now respects, instead of one shared id ("tf") that let a query on
    project A's questions search across every project's sessions. Fix 4: reachability is the
    SAME whole-word marker-text definition the Nevertwice arm now uses, never a session id
    among the hits - the id/text asymmetry (F9, TF-ANALYSIS.md) made the two arms incomparable
    even where both delivered something.
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

    by_qid = {e["question_id"]: e for e in data}
    if groups is None:
        groups = {"tf": {"session_ids": list(pool.keys()),
                         "question_ids": [e["question_id"] for e in data]}}

    rows: list = []
    t0 = time.time()
    written_total = 0
    for gname, g in groups.items():
        user_id = m.slug_project(f"tf-{gname}")                     # fix 5: one user_id/project
        items = [(sid, pool[sid]) for sid in g["session_ids"] if sid in pool]
        if cap:
            items = items[:cap]
        if not items:
            continue
        for sid, text in items:
            try:
                mem.add(text, user_id=user_id, metadata={"session_id": sid}, infer=False)
            except Exception as exc:                                # noqa: BLE001
                return {"blocked": f"[{gname}] mem0.add failed on {sid}: {type(exc).__name__}: {exc}"}
            written_total += 1
        for qid in g["question_ids"]:
            e = by_qid.get(qid)
            if e is None:
                continue
            try:
                res = mem.search(e["question"], filters={"user_id": user_id}, top_k=MEM0_TOP_K)
            except Exception as exc:                                # noqa: BLE001
                return {"blocked": f"[{gname}] mem0.search failed: {type(exc).__name__}: {exc}"}
            hits = res.get("results", res) if isinstance(res, dict) else res
            payload = "\n".join(str(h.get("memory", "")) for h in hits)
            markers = _row_markers(corpus_name, e)
            reachable = any(_whole_word_present(mk, payload) for mk in markers) if markers else None
            row = {"question_id": qid, "project": gname, "hits": len(hits), "top_k": MEM0_TOP_K,
                  "infer": False, "answer_reachable": reachable, **_count(payload, toks)}
            rows.append(row)
    if not rows:
        return {"blocked": "no questions - refusing to report over an empty set"}
    return {
        "ingest": {"written": written_total, "seconds": round(time.time() - t0, 1),
                  "projects": len(groups)},
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


#: Fix 2: EXPLICIT curve model. Fix 1's methodology (a fresh session_id per question) makes
#: every row genuinely "turn 1 of its own independent single-turn session" - which is what is
#: reported, and named as such, rather than the v1 formula (`start + N*mean`) that matched
#: neither this model (which pays `session_start` EVERY session, i.e. N times) nor a single
#: N-turn session capped at PROMPT_RECALL_MAX_PER_SESSION (which would need `mean_when_firing`
#: measured over turns 2..6 of ONE shared session - a genuinely different measurement fix 1
#: deliberately stops making, because that is the exact shared-session-id methodology whose
#: cap-exhaustion bug this rewrite exists to remove).
CURVE_MODEL = "N independent single-turn sessions"
CURVE_FORMULA = "total(N) = N * (session_start + mean_per_turn)"


def curve(arms: dict, toks: dict) -> dict:
    """Total cost for N independent single-turn sessions, per arm, per unit - see CURVE_MODEL."""
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
                #: The MEAN, not the median: cost over N sessions is a SUM, and the mean is
                #: what predicts a sum. Measured 2026-09-22 on 50 questions: the median read 0
                #: because the path fired on 6 of 50, the mean read 97 - a median-built curve
                #: read "cost never grows" and overstated the advantage six-fold.
                vals = [r.get(unit) or 0 for r in res["rows"]]
                mean = sum(vals) / len(vals) if vals else 0
                row[arm] = round(n * (start + mean))
            per_n[str(n)] = row
        out[unit] = per_n
    return out


def finish_arm(name: str, res: dict, wall_s: float, snap: dict) -> dict:
    """Mutates and returns `res` (one arm's own result dict from `run_nevertwice`/
    `run_mem0`) with the pacer's own findings about that arm's run: `wall_s` (the whole
    call, timed by the caller), `pacer.attach(res, since=snap)`'s per-arm delta under
    `res["ollama_transport"]`, a pace-excluded companion for `wall_s` when that delta
    exists, and (R-v2-ports A4) `res["coverage"] = "unobserved"` when a COMPETITOR arm
    (never our own `nevertwice`, which measures its OWN write path rather than a
    client's) made fewer paced calls than the sessions it says it wrote - a lower
    bound, not a complete measurement, and the row says so rather than implying full
    coverage it did not have.

    R2 (the auditor's finding): `wall_s - pace_sleep_s - retry_sleep_s` is a SUM of
    sleeps and assumes they never overlap - false under real concurrency, where the
    result can go negative. Clamped at 0; `wall_s_pace_excluded_exact` names whether
    `ollama_transport["max_concurrent_paced"]` (the pacer's own process-wide high-water
    mark over the WHOLE paced operation, K14 - pacing/retry WAITS overlap even when the
    calls themselves never do, which the narrower `max_inflight` - call-only - cannot
    see) ever exceeded 1, so a reader knows whether to trust the number exactly."""
    res["wall_s"] = round(wall_s, 1)
    pacer.attach(res, since=snap)
    if "ollama_transport" in res:
        res["timing_includes_pacing"] = True
        ot = res["ollama_transport"]
        res["wall_s_pace_excluded"] = max(0.0, round(
            res["wall_s"] - ot["pace_sleep_s"] - ot["retry_sleep_s"], 3))
        res["wall_s_pace_excluded_exact"] = ot.get("max_concurrent_paced", 0) <= 1
    if name != "nevertwice" and "blocked" not in res:
        ingested = (res.get("ingest") or {}).get("written")
        observed = res.get("ollama_transport", {}).get("calls", 0)
        if ingested is not None and observed < ingested:
            res["coverage"] = "unobserved"
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default="nevertwice",
                    help="comma list of arms to run: nevertwice,mem0")
    ap.add_argument("--limit", type=int, default=0, help="first N questions (a smoke run)")
    ap.add_argument("--sessions", type=int, default=0, help="cap ingested sessions (testing only)")
    ap.add_argument("--dry", action="store_true",
                    help="stub extractor, no model, no LLM at all - proves the stand's own "
                         "plumbing (fixes 1/3/4/5/7) end to end, never a real measurement")
    ap.add_argument("--save", action="store_true", help="write the artifact")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)

    if a.dry:
        #: HARD RULE: --dry must never reach a real backend, on THIS machine or any other -
        #: `embedder_available()` pings whatever Ollama the environment happens to have
        #: running, and on a machine with one live (this one, routinely) that ping succeeds
        #: and the retrieval path underneath _ingest_stub/_diagnose would embed for real. The
        #: stub extractor alone (no api.capture_session call) is NOT sufficient hermeticity -
        #: update_embeddings and retrieve_relevant both call embed_text independently of
        #: extraction. Stubbed unconditionally, before any corpus is touched.
        m.embed_text = lambda *a, **k: None
        m.embedder_available = lambda *a, **k: False
        m.embed_cache_usable = lambda: False
        m.ollama_alive = lambda *a, **k: False
        m.llm_available = lambda: False

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

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                          capture_output=True, text=True).stdout.strip() or None
    measured_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    pacer.install()          # R-v2-ports: pace/retry/count every arm's own Ollama traffic -
                              # a no-op under --dry, which never reaches a real backend at all
    corpora: dict = {}
    for cname in CORPORA:
        try:
            data, pool, groups = load_corpus(cname)
        except Exception as exc:                                    # noqa: BLE001
            corpora[cname] = {"blocked": f"{type(exc).__name__}: {exc}"}
            continue
        if a.dry:
            data = data[:2]
            if groups:
                keep_qids = {e["question_id"] for e in data}
                groups = {gn: g for gn, g in groups.items()
                         if keep_qids & set(g["question_ids"])}
                for gn in list(groups):
                    groups[gn] = {"session_ids": groups[gn]["session_ids"][:1],
                                 "question_ids": [q for q in groups[gn]["question_ids"]
                                                  if q in keep_qids]}
        elif a.limit:
            data = data[:a.limit]
        if not data:
            corpora[cname] = {"blocked": "no questions after --limit - nothing to measure over"}
            continue
        cap = 1 if a.dry else (a.sessions or None)
        arms = {}
        for name in wanted:
            snap = pacer.snapshot()
            t0 = time.time()
            if name == "nevertwice":
                arms[name] = run_nevertwice(data, pool, toks, cap, groups=groups, dry=a.dry,
                                            corpus_name=cname)
            else:
                arms[name] = run_mem0(data, pool, toks, cap, groups=groups, corpus_name=cname)
            finish_arm(name, arms[name], time.time() - t0, snap)
        corpora[cname] = {
            "questions": len(data), "pool_sessions": len(pool),
            "provenance": (corpus_pin.record(cname) if cname in corpus_pin.CORPORA
                           else {"corpus": cname, "provenance": "tracked in git; see code_sha"}),
            "arms": arms,
            "curve": curve(arms, toks),
            "curve_model": CURVE_MODEL,                    # fix 2
            "curve_formula": CURVE_FORMULA,                 # fix 2
            #: A code_sha/measured_at PER CORPUS (not only the top-level, once-per-run one):
            #: fix 1 gives each corpus its own fresh store, and a merge (--save) can bring in
            #: an arm measured at a different moment than the one just run - so "when was THIS
            #: corpus's number produced" needs its own answer, not the run's.
            "code_sha": head, "measured_at": measured_at,
        }

    result = {
        "code_sha": head,
        "measured_at": measured_at,
        "dry_run": bool(a.dry),
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
            #: The CONDITION travels with the number, never in a footnote. Mem0's cost is a
            #: function of how many memories it returns (`top_k`) AND whether it infers
            #: structured facts from raw text (`infer`) - both named in the SAME line (fix 6),
            #: because a reader who sees a character count without them cannot tell whose
            #: setting produced the figure.
            vals = [r.get("chars") or 0 for r in res["rows"]]
            mean = sum(vals) / len(vals) if vals else 0.0
            cond = ""
            if res.get("rows") and "top_k" in res["rows"][0]:
                cond = (f"   [top_k={res['rows'][0]['top_k']}, infer="
                       f"{res['rows'][0].get('infer')}, Mem0 default top_k]")
            print(f"  {name:12s} session start {start:6d} chars   "
                  f"per turn median {pt['median_chars']:5d} mean {mean:7.1f} chars   "
                  f"injected on {pt['turns_that_injected']}/{pt['n']} turns   "
                  f"answer reachable {pt['answer_reachable']}/{pt['n']}{cond}")
            if "by_reason" in pt:
                print(f"  {'':12s}   reasons: " +
                     ", ".join(f"{r}={pt['by_reason'][r]}" for r in REASONS) +
                     f"   lexical-only fallback on {pt.get('lexical_fallback', 0)}/{pt['n']}")
            if ing:
                wc = ing.get("write_cost_tokens") or {}
                k_off, n_sess = ing.get("off_topic_sessions") or 0, ing.get("written", 0)
                k_fail = ing.get("not_stored") or 0
                verdict = (("extractor produced nothing - a regime, not a refusal"
                            if not (k_off or k_fail) else
                            f"extractor produced nothing; of {n_sess} session(s) the relevance gate "
                            f"judged {k_off} off-topic and {k_fail} stored nothing (extraction failed "
                            "or skipped) - for those a gate or a failure, not a regime")
                           if prop == 0 else
                           f"extractor proposed {prop}, write path refused {refu}"
                           + "".join(f", {k.replace('_', '-')} {n}" for k in ("off_topic", "quarantined", "skipped")
                                     if (n := sum((ing.get(k) or {}).values()))))
                #: a session that stored nothing is named whatever else was proposed: `written`
                #: counts it as ingested, and "stored False" is also an already-processed or empty
                #: transcript - so it is called what the return can prove, not "failed" (fourth
                #: review, 2026-09-23: 6 failures beside 4 good sessions printed nothing)
                if k_fail and prop:
                    verdict += f"; {k_fail} of {n_sess} session(s) stored nothing (extraction failed or skipped)"
                proj_note = f", {ing.get('projects')} project(s)" if ing.get("projects") else ""
                print(f"  {'':12s}   write: {ing.get('written', 0)} session(s){proj_note}, "
                      f"{ing.get('typed_notes', 0)} typed note(s), "
                      f"{wc.get('prompt_tokens', 0)}+{wc.get('eval_tokens', 0)} tokens "
                      f"(model's own count) - {verdict}")
        live = [k for k, v in cres["arms"].items() if "blocked" not in v]
        if len(live) >= 2:
            print(f"  total chars for {cres['curve_model']}, {cres['curve_formula']}:")
            for n in TURNS:
                row = cres["curve"]["chars"][str(n)]
                cells = "   ".join(f"{k} {row[k]}" for k in live if row.get(k) is not None)
                print(f"    N={n:4d}   {cells}")
        elif live:
            print(f"  only one arm ran ({live[0]}); a curve needs two, so none is printed - "
                  f"a one-armed cost is a number, not a comparison")

    if a.dry:
        print("\n--dry: plumbing exercised, nothing written to the artifact")
        return 0

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
             "at": measured_at}]
        for cres in result["corpora"].values():
            if "arms" in cres:
                cres["curve"] = curve(cres["arms"], toks)
        out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
        present = sorted({a for c in result["corpora"].values() for a in (c.get("arms") or {})})
        print(f"\nwrote {out}  (arms present: {', '.join(present)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
