#!/usr/bin/env python3
"""A6 - the modular memory upgrade: mode selection, temporal anchoring, prior-value
history, and role-aware episodic chunks that a read-only store may not mutate.

This file is the acceptance contract for five requirements, written from the requirement
text and the existing public surfaces (`write_typed_note`, `update_embeddings`,
`api.recall`, `api.format_note`) - not from an implementation. It is expected to be RED
before the A6 modules land: `nevertwice/modes.py`, `nevertwice/temporal.py` and
`nevertwice/episodic.py` do not exist yet, so their sections report a deliberate FAIL per
planned check instead of dying on an import error, and the A6.3 / M34 sections exercise
today's behaviour and fail where the new guarantee is missing.

Requirement -> section map (every check's name starts with its A6 id):

  A6.1  modes.resolve_mode(mode_env, transcript) -> "code" | "all"
        - explicit "code"/"all" wins over the transcript's content;
        - "auto" reads code markers (`def `, `import `, `.py`, `git `, `Working directory:`)
          and leaves conversational/personal dialogue as "all";
        - modes.mode_prompt_rubric("code") == "" (the legacy coding prompt is unchanged);
          modes.mode_prompt_rubric("all") is a compact (< 900 char) rubric that treats
          personal/biographical/conversational facts, events, dates, quantities and state
          updates as project_relevant: true of type decisions / patterns.

  A6.2  temporal.resolve_relative_dates(text, anchor) and temporal.stamp_date_anchor(desc, anchor)
        - exact offsets for yesterday / tomorrow / 2 weeks ago / 3 days ago;
        - unrelated text and already-annotated dates are untouched; the pass is idempotent;
        - month and year boundaries follow plain calendar arithmetic;
        - stamp_date_anchor appends `[Date: <anchor>]` exactly once and is idempotent.

  A6.3  Prior-value history on absorb and supersede.
        - Day 1 "Completed 4 painting projects" (2023-07-10), Day 2 "Completed 5 painting
          projects" (2023-08-15, supersedes the Day-1 title): the new note's cached `desc`
          and `api.format_note()` / `api.recall()` output carry the current value AND a
          compact `[Prior 2023-07-10: ...4 painting projects...]` line, so the reader gets
          both without another LLM call;
        - the same holds when a same-session re-write ABSORBS the earlier note in place.

  A6.4  episodic.extract_episodic_chunks(transcript, session_date, session_id)
        - multi-turn transcripts split into non-empty chunks of at most 600 characters;
        - user statements carry `[Date: <session_date> | Speaker: user]`; assistant turns
          carry `[Assistant context]` or are filtered, and are never presented as the user's
          own words; no user statement is lost;
        - M34: with NEVERTWICE_TELEMETRY_READONLY=1 (and, separately, NEVERTWICE_SANDBOX=1)
          `api.recall()` leaves telemetry.json and telemetry.json.bak byte-for-byte unchanged.
          A positive control without either flag proves the read path does write when allowed,
          so the immutability checks can actually fail.

  A6.5  calendar phrases, epistemic tags, and question carryover.
        - temporal.resolve_relative_dates keeps the phrase and appends the resolved value:
          "last Saturday" on 2023-05-25 -> "[2023-05-20]", "next month" -> "[June 2023]",
          "last month" on 2023-01-10 -> "[December 2022]", "4 years ago" -> "[2019]",
          "last year" -> "[2022]"; every one is unchanged by a second pass;
        - modes.mode_prompt_rubric("all") names `[Confirmed fact]` and
          `[Hypothetical / Assistant suggestion]` so confirmed and hypothetical content stay
          distinguishable;
        - episodic.extract_episodic_chunks prefixes the next speaker's answer with
          `(re: <last question>)`, and the prefix survives the answer beginning a new
          600-character chunk.
        A6.5 is written from the requirement text and the public format only: it does not
        reproduce the resolver's calendar arithmetic or the chunker's packing rule. Its
        baseline is expected to pass where the named behaviour is already present; a red
        means the behaviour or this oracle is wrong, and is reported rather than papered over.

Not asserted here (verification gaps are listed in the report, not papered over): the
"sealed store mode" spelling the requirement mentions has no documented marker in this
repository; mode_env values other than "code"/"all"/"auto"; relative-date phrases beyond
the nine named here (four day offsets in A6.2, five calendar phrases in A6.5), and weekday
arithmetic beyond the single "last Saturday" case; question carryover longer than one turn
or longer than the chunker's bounded prefix; an answer that is itself a question; and where
`session_id` surfaces in a chunk. The M34 checks run in a child process with the flag
already in its environment, so a call-time and an import-time reading of the flag both
satisfy them. A6.5 exercises `episodic.extract_episodic_chunks` directly and does not cover
how `_engine_cards.py` indexes the two role kinds into the store.

Hermetic and offline: the store is a fresh temp dir moved with `m._rebase_vault`, the
embedder is stubbed to text-only, and the M34 children inherit explicit store paths.

    python tests/_test_a6_memory_upgrade.py
"""
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PKG = ROOT / "nevertwice"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PKG))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import sandbox_guard  # noqa: E402 - the store-location spellings the child must not inherit
import memory_hook as m  # noqa: E402

import api  # noqa: E402 - the public read surface the requirements name

P = F = 0


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}{('  ' + detail) if detail else ''}")


#: A6.5's exact calendar resolutions, each with the check name that reports it. The oracle is
#: the requirement's (phrase, anchor, expected) triple, not a re-implementation of the
#: resolver: the phrase is kept and the answer is appended in brackets, so a reader sees what
#: was said and what it meant. Defined once so the planned inventory and the executed checks
#: cannot drift.
A65_TEMPORAL_CASES = (
    ("A6.5.1 'last Saturday' on 2023-05-25 resolves to 2023-05-20",
     "last Saturday", "2023-05-25", "last Saturday [2023-05-20]"),
    ("A6.5.2 'next month' on 2023-05-25 resolves to June 2023",
     "next month", "2023-05-25", "next month [June 2023]"),
    ("A6.5.3 'last month' on 2023-01-10 crosses into December 2022",
     "last month", "2023-01-10", "last month [December 2022]"),
    ("A6.5.4 '4 years ago' on 2023-05-25 resolves to 2019",
     "4 years ago", "2023-05-25", "4 years ago [2019]"),
    ("A6.5.5 'last year' on 2023-05-25 resolves to 2022",
     "last year", "2023-05-25", "last year [2022]"),
)
A65_IDEMPOTENT = "A6.5.6 resolving each of those five phrases twice changes nothing"
A65_RUBRIC_CONFIRMED = "A6.5.7 mode_prompt_rubric('all') contains [Confirmed fact]"
A65_RUBRIC_HYPOTHETICAL = (
    "A6.5.8 mode_prompt_rubric('all') contains [Hypothetical / Assistant suggestion]")
A65_REPLY_CONTEXT = "A6.5.9 the chunk holding Caroline's answer carries the question it answers"
A65_REPLY_CONTEXT_SPLIT = "A6.5.10 the carryover survives the answer starting a new chunk"
A65_CHECKS = ([case[0] for case in A65_TEMPORAL_CASES]
              + [A65_IDEMPOTENT, A65_RUBRIC_CONFIRMED, A65_RUBRIC_HYPOTHETICAL,
                 A65_REPLY_CONTEXT, A65_REPLY_CONTEXT_SPLIT])

#: One entry per executed check, so a section that dies halfway (or a planned check that
#: was silently deleted) shows up as a failure instead of a shorter green run.
PLANNED = {
    "A6.1": [
        "A6.1.0 modes.py is importable",
        "A6.1.1 explicit 'code' wins over conversational text",
        "A6.1.2 explicit 'all' wins over code-shaped text",
        "A6.1.3 auto reads 'def ' as code",
        "A6.1.4 auto reads 'import ' as code",
        "A6.1.5 auto reads '.py' as code",
        "A6.1.6 auto reads 'git ' as code",
        "A6.1.7 auto reads 'Working directory:' as code",
        "A6.1.8 auto leaves the requirement's degree dialogue as all",
        "A6.1.9 auto leaves personal dialogue as all",
        "A6.1.10 mode_prompt_rubric('code') is exactly empty",
        "A6.1.11 mode_prompt_rubric('all') is non-empty",
        "A6.1.12 mode_prompt_rubric('all') is under 900 characters",
        "A6.1.13 the rubric says project_relevant: true",
        "A6.1.14 the rubric names decisions",
        "A6.1.15 the rubric names patterns",
        "A6.1.16 the rubric covers personal or biographical facts",
        "A6.1.17 the rubric covers events",
        "A6.1.18 the rubric covers dates",
        "A6.1.19 the rubric covers quantities",
        "A6.1.20 the rubric covers state updates",
    ],
    "A6.2": [
        "A6.2.0 temporal.py is importable",
        "A6.2.1 yesterday resolves to the previous day",
        "A6.2.2 tomorrow resolves to the next day",
        "A6.2.3 '2 weeks ago' resolves 14 days back",
        "A6.2.4 '3 days ago' resolves 3 days back",
        "A6.2.5 two relative phrases in one line both resolve",
        "A6.2.6 unrelated text is unchanged",
        "A6.2.7 an already-annotated date is not re-annotated",
        "A6.2.8 resolving twice changes nothing the second time",
        "A6.2.9 '2 weeks ago' crosses a year boundary",
        "A6.2.10 tomorrow crosses a month boundary",
        "A6.2.11 stamp_date_anchor appends the anchor exactly once",
        "A6.2.12 stamp_date_anchor keeps the description intact",
        "A6.2.13 stamp_date_anchor is idempotent",
        "A6.2.14 stamp_date_anchor leaves an existing anchor unchanged",
    ],
    "A6.3": [
        "A6.3.1 the Day-1 note is written",
        "A6.3.2 the Day-1 note is in the embedding cache",
        "A6.3.3 the Day-2 supersede creates a distinct note",
        "A6.3.4 the Day-1 note is retired to Superseded/",
        "A6.3.5 the Day-2 note is in the embedding cache",
        "A6.3.6 the cached desc keeps the current value",
        "A6.3.7 the cached desc carries a [Prior 2023-07-10: ...] line",
        "A6.3.8 the prior line carries the replaced value",
        "A6.3.9 format_note from the cache shows both values",
        "A6.3.10 recall returns the superseding note",
        "A6.3.11 format_note of the recalled hit shows both values",
        "A6.3.12 an absorbed re-write reuses the note's stem",
        "A6.3.13 the absorbed cache entry keeps the current value",
        "A6.3.14 the absorbed cache entry carries the prior line and value",
        "A6.3.15 format_note of the absorbed note shows both values",
    ],
    "A6.4": [
        "A6.4.0 episodic.py is importable",
        "A6.4.1 a multi-turn transcript splits into more than one chunk",
        "A6.4.2 every chunk is a non-empty string",
        "A6.4.3 every chunk is at most 600 characters",
        "A6.4.4 no user statement is lost in the split",
        "A6.4.5 user statements carry [Date: ... | Speaker: user]",
        "A6.4.6 assistant turns carry [Assistant context] or are filtered",
        "A6.4.7 no assistant text is presented as the user's own",
        "A6.4.8 the session_date argument drives the chunk date tag",
        "A6.4.9 an assistant-only transcript never becomes a user fact",
        "A6.4.10 chunking is deterministic for the same input",
    ],
    "A6.4-M34": [
        "A6.4.11 NEVERTWICE_TELEMETRY_READONLY=1: the read completes and returns the note",
        "A6.4.12 NEVERTWICE_TELEMETRY_READONLY=1: telemetry.json is unchanged",
        "A6.4.13 NEVERTWICE_TELEMETRY_READONLY=1: telemetry.json.bak is unchanged",
        "A6.4.14 NEVERTWICE_SANDBOX=1: the read completes and returns the note",
        "A6.4.15 NEVERTWICE_SANDBOX=1: telemetry.json is unchanged",
        "A6.4.16 NEVERTWICE_SANDBOX=1: telemetry.json.bak is unchanged",
        "A6.4.17 control (no read-only flag): the read records, so the checks can fail",
    ],
    "A6.5": A65_CHECKS,
}


def _load(name):
    """Import the module flat first (the tests' convention), then as a package submodule.

    A missing module is the expected pre-implementation state, not a reason to crash the
    harness: both errors are returned so the section can fail every planned check with the
    reason attached.
    """
    errors = []
    for candidate in (name, f"nevertwice.{name}"):
        try:
            return importlib.import_module(candidate), ""
        except Exception as exc:  # noqa: BLE001 - the reason is the check's detail
            errors.append(f"{candidate}: {type(exc).__name__}: {exc}")
    return None, "; ".join(errors)


def _fail_checks(names, reason):
    for name in names:
        check(name, False, reason)


def _run_section(label, fn, *args):
    before = P + F
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001 - one section must not hide the others
        check(f"{label}: exercised without an exception", False,
              f"{type(exc).__name__}: {exc}")
    executed = P + F - before
    if executed != len(PLANNED[label]):
        check(f"{label}: all {len(PLANNED[label])} planned checks ran", False,
              f"{executed} ran")


def _mentions(text, *needles):
    low = (text or "").lower()
    return any(n in low for n in needles)


# ── A6.1 - codemode vs allmode ─────────────────────────────────────────────────────
CONVERSATIONAL = ("Conversation from 2023-05-20:\n"
                  "user: I graduated with a degree in Business Administration")


def _a61(modes):
    check("A6.1.0 modes.py is importable", True)
    print("# A6.1 - explicit mode wins; auto classifies the transcript's content")

    check("A6.1.1 explicit 'code' wins over conversational text",
          modes.resolve_mode("code", CONVERSATIONAL) == "code",
          repr(modes.resolve_mode("code", CONVERSATIONAL)))
    check("A6.1.2 explicit 'all' wins over code-shaped text",
          modes.resolve_mode("all", "def train(model):\n    return model") == "all",
          repr(modes.resolve_mode("all", "def train(model):\n    return model")))

    markers = (
        ("A6.1.3", "def ", "user: review this\ndef train(model):\n    return model"),
        ("A6.1.4", "import ", "user: review this\nimport torch\nprint(torch.__version__)"),
        ("A6.1.5", ".py", "user: review this\nthe failure is in main.py on line 40"),
        ("A6.1.6", "git ", "user: what should I do\ngit status shows two modified files"),
        ("A6.1.7", "Working directory:", "Working directory: D:\\Coding\\proj\nuser: what changed"),
    )
    for ident, marker, transcript in markers:
        got = modes.resolve_mode("auto", transcript)
        check(f"{ident} auto reads {marker!r} as code", got == "code", f"got {got!r}")

    check("A6.1.8 auto leaves the requirement's degree dialogue as all",
          modes.resolve_mode("auto", CONVERSATIONAL) == "all",
          repr(modes.resolve_mode("auto", CONVERSATIONAL)))
    personal = "user: I adopted a dog named Rex last spring.\nassistant: That is lovely."
    check("A6.1.9 auto leaves personal dialogue as all",
          modes.resolve_mode("auto", personal) == "all",
          repr(modes.resolve_mode("auto", personal)))

    code_rubric = modes.mode_prompt_rubric("code")
    check("A6.1.10 mode_prompt_rubric('code') is exactly empty",
          code_rubric == "", repr(code_rubric))

    raw_rubric = modes.mode_prompt_rubric("all")
    check("A6.1.11 mode_prompt_rubric('all') is non-empty",
          isinstance(raw_rubric, str) and bool(raw_rubric.strip()),
          f"{type(raw_rubric).__name__}: {raw_rubric!r}")
    rubric = raw_rubric if isinstance(raw_rubric, str) else ""
    check("A6.1.12 mode_prompt_rubric('all') is under 900 characters",
          len(rubric) < 900, f"{len(rubric)} chars")
    check("A6.1.13 the rubric says project_relevant: true",
          bool(re.search(r'project_relevant["\']?\s*[:=]\s*true', rubric, re.I)),
          rubric[:200])
    check("A6.1.14 the rubric names decisions", _mentions(rubric, "decision"), rubric[:200])
    check("A6.1.15 the rubric names patterns", _mentions(rubric, "pattern"), rubric[:200])
    check("A6.1.16 the rubric covers personal or biographical facts",
          _mentions(rubric, "personal", "biograph"), rubric[:200])
    check("A6.1.17 the rubric covers events", _mentions(rubric, "event"), rubric[:200])
    check("A6.1.18 the rubric covers dates", _mentions(rubric, "date", "time"), rubric[:200])
    check("A6.1.19 the rubric covers quantities",
          _mentions(rubric, "quantit", "numeric", "number", "count", "amount"), rubric[:200])
    check("A6.1.20 the rubric covers state updates",
          _mentions(rubric, "state") and _mentions(rubric, "update", "change"), rubric[:200])


# ── A6.2 - temporal anchoring ──────────────────────────────────────────────────────
def _a62(temporal):
    check("A6.2.0 temporal.py is importable", True)
    print("# A6.2 - relative dates resolve against the anchor; the anchor stamps once")

    def resolve(text, anchor="2023-05-20"):
        return temporal.resolve_relative_dates(text, anchor)

    for ident, text, expect in (
        ("A6.2.1", "yesterday", "yesterday [2023-05-19]"),
        ("A6.2.2", "tomorrow", "tomorrow [2023-05-21]"),
        ("A6.2.3", "2 weeks ago", "2 weeks ago [2023-05-06]"),
        ("A6.2.4", "3 days ago", "3 days ago [2023-05-17]"),
    ):
        got = resolve(text)
        check(f"{ident} {text!r} resolves to {expect!r}", got == expect, repr(got))

    combined = resolve("yesterday I left; tomorrow I return")
    check("A6.2.5 two relative phrases in one line both resolve",
          combined == "yesterday [2023-05-19] I left; tomorrow [2023-05-21] I return",
          repr(combined))

    unrelated = "We shipped the release on 2023-05-02 after two reviews."
    check("A6.2.6 unrelated text is unchanged", resolve(unrelated) == unrelated,
          repr(resolve(unrelated)))
    annotated = "yesterday [2023-05-19] was the deadline"
    check("A6.2.7 an already-annotated date is not re-annotated",
          resolve(annotated) == annotated, repr(resolve(annotated)))
    once = resolve("yesterday the build failed and 2 weeks ago it passed")
    twice = resolve(once)
    check("A6.2.8 resolving twice changes nothing the second time", twice == once,
          f"second pass {twice!r}")

    year = resolve("2 weeks ago", "2023-01-05")
    check("A6.2.9 '2 weeks ago' crosses a year boundary",
          year == "2 weeks ago [2022-12-22]", repr(year))
    month = resolve("tomorrow", "2023-05-31")
    check("A6.2.10 tomorrow crosses a month boundary",
          month == "tomorrow [2023-06-01]", repr(month))

    desc = "Completed 4 painting projects"
    stamped = temporal.stamp_date_anchor(desc, "2023-05-20")
    is_str = isinstance(stamped, str)
    check("A6.2.11 stamp_date_anchor appends the anchor exactly once",
          is_str and stamped.endswith("[Date: 2023-05-20]")
          and stamped.count("[Date: 2023-05-20]") == 1, repr(stamped))
    check("A6.2.12 stamp_date_anchor keeps the description intact",
          is_str and stamped.startswith(desc), repr(stamped))
    check("A6.2.13 stamp_date_anchor is idempotent",
          is_str and temporal.stamp_date_anchor(stamped, "2023-05-20") == stamped,
          repr(stamped))
    existing = "shipped [Date: 2023-05-20]"
    check("A6.2.14 stamp_date_anchor leaves an existing anchor unchanged",
          temporal.stamp_date_anchor(existing, "2023-05-20") == existing,
          repr(temporal.stamp_date_anchor(existing, "2023-05-20")))


# ── A6.3 - prior-value history on absorb & supersede ──────────────────────────────
A63_PROJECT = "proj"
A63_TITLE = "Completed painting projects"
A63_DAY1, A63_DAY2 = "2023-07-10", "2023-08-15"
A63_DESC1 = "Completed 4 painting projects"
A63_DESC2 = "Completed 5 painting projects"
PRIOR_RE = re.compile(r"\[Prior 2023-07-10:[^\]]*4 painting projects", re.S)
#: The absorb case gets its own title and values so the two scenarios cannot retire, attach
#: to, or rank each other; the mechanism under test is identical.
A63_ABSORB_TITLE = "Completed garden bed projects"
A63_ABSORB_DESC1 = "Completed 4 garden beds"
A63_ABSORB_DESC2 = "Completed 5 garden beds"
PRIOR_ABSORB_RE = re.compile(r"\[Prior 2023-07-10:[^\]]*4 garden beds", re.S)


def _write_and_embed(title, desc, date, *, supersedes="", session=None):
    """Mirror the production ordering: write, then embed the description the caller holds.

    `_engine_cards.process_session` and `api.remember_lessons_aligned` both read the item's
    description AFTER `write_typed_note` and hand it to `update_embeddings`. Reading it after
    the write here accepts either place the history line can be attached - in place on the
    item, or inside the embedding step that reads the note - without assuming which.
    """
    item = {"title": title, "description": desc}
    if supersedes:
        item["supersedes"] = supersedes
    kwargs = {"session_stem_": session} if session else {}
    stem = m.write_typed_note("Patterns", item, A63_PROJECT, date, ["home"], "pattern", **kwargs)
    if stem:
        m.update_embeddings([(stem, "pattern", A63_PROJECT,
                              item.get("title", title), item.get("description", desc), "")])
    return stem, item.get("description", desc)


def _embed_cache():
    """The logical cache, through the engine's own reader.

    `save_embed_cache` appends hinted writes to `.embeddings_cache.json.journal` and folds
    into the snapshot only when the journal crosses its threshold (B3) - so the snapshot
    alone is NOT the cache the reader gets, and asserting on the raw file would fail a
    correct implementation on the second write. `load_embed_cache` is the one reader every
    recall path uses (snapshot + journal replayed), which is the contract A6.3 names.
    The memo is cleared first so the answer comes from disk, not from the writer's copy.
    """
    m._EMBED_CACHE_MEMO["sig"] = None
    m._EMBED_CACHE_MEMO["data"] = None
    try:
        cache = m.load_embed_cache()
        if isinstance(cache, dict):
            return cache
    except Exception:  # noqa: BLE001 - fall through to the snapshot for the detail
        pass
    path = m.EMBED_CACHE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _a63(_vault):
    print("# A6.3 - the replaced value survives in the cache and in the reader's text")
    stem1, _ = _write_and_embed(A63_TITLE, A63_DESC1, A63_DAY1)
    check("A6.3.1 the Day-1 note is written", bool(stem1), repr(stem1))
    if not stem1:
        _fail_checks(PLANNED["A6.3"][1:], "Day-1 note was not written")
        return

    cache1 = _embed_cache() or {}
    check("A6.3.2 the Day-1 note is in the embedding cache", stem1 in cache1,
          f"stems: {sorted(cache1)[:3]}")

    stem2, _ = _write_and_embed(A63_TITLE, A63_DESC2, A63_DAY2, supersedes=A63_TITLE)
    check("A6.3.3 the Day-2 supersede creates a distinct note",
          bool(stem2) and stem2 != stem1, f"{stem1!r} -> {stem2!r}")
    if not stem2 or stem2 == stem1:
        _fail_checks(PLANNED["A6.3"][3:], "Day-2 note was not a distinct write")
        return

    patterns = m.VAULT / "Patterns"
    live_old = (patterns / f"{stem1}.md").exists()
    retired_old = (patterns / "Superseded" / f"{stem1}.md").exists()
    check("A6.3.4 the Day-1 note is retired to Superseded/",
          retired_old and not live_old, f"live={live_old} retired={retired_old}")

    cache = _embed_cache() or {}
    entry = cache.get(stem2)
    check("A6.3.5 the Day-2 note is in the embedding cache", isinstance(entry, dict),
          f"stems: {sorted(cache)[:4]}")
    entry = entry if isinstance(entry, dict) else {}
    desc = entry.get("desc") or ""
    desc = desc if isinstance(desc, str) else ""
    check("A6.3.6 the cached desc keeps the current value",
          "5 painting projects" in desc, repr(desc))
    has_prior = bool(PRIOR_RE.search(desc))
    check("A6.3.7 the cached desc carries a [Prior 2023-07-10: ...] line",
          has_prior, repr(desc))
    check("A6.3.8 the prior line carries the replaced value",
          has_prior and "4 painting projects" in desc, repr(desc))

    rendered = api.format_note({"ntype": "pattern", "title": A63_TITLE, "description": desc})
    check("A6.3.9 format_note from the cache shows both values",
          "5 painting projects" in rendered
          and PRIOR_RE.search(rendered) is not None, repr(rendered))

    hits = api.recall("painting projects", project=A63_PROJECT, k=5, xrerank=False)
    hit = next((h for h in hits if h.get("stem") == stem2), None)
    check("A6.3.10 recall returns the superseding note", hit is not None,
          f"hits: {[h.get('stem') for h in hits]}")
    formatted = api.format_note(hit) if hit else ""
    check("A6.3.11 format_note of the recalled hit shows both values",
          "5 painting projects" in formatted
          and PRIOR_RE.search(formatted) is not None, repr(formatted))

    # Absorb: a same-session re-write with a different description is refreshed in place.
    stem_a, _ = _write_and_embed(A63_ABSORB_TITLE, A63_ABSORB_DESC1, A63_DAY1,
                                 session="sess-a63")
    stem_b, _ = _write_and_embed(A63_ABSORB_TITLE, A63_ABSORB_DESC2, A63_DAY1,
                                 session="sess-a63")
    check("A6.3.12 an absorbed re-write reuses the note's stem",
          bool(stem_a) and stem_a == stem_b, f"{stem_a!r} -> {stem_b!r}")
    if not stem_a or stem_a != stem_b:
        _fail_checks(PLANNED["A6.3"][12:], "absorb did not happen in place")
        return

    absorbed = (_embed_cache() or {}).get(stem_a) or {}
    adesc = absorbed.get("desc") or ""
    adesc = adesc if isinstance(adesc, str) else ""
    check("A6.3.13 the absorbed cache entry keeps the current value",
          "5 garden beds" in adesc, repr(adesc))
    check("A6.3.14 the absorbed cache entry carries the prior line and value",
          bool(PRIOR_ABSORB_RE.search(adesc)) and "4 garden beds" in adesc, repr(adesc))
    arendered = api.format_note({"ntype": "pattern", "title": A63_ABSORB_TITLE,
                                 "description": adesc})
    check("A6.3.15 format_note of the absorbed note shows both values",
          "5 garden beds" in arendered
          and PRIOR_ABSORB_RE.search(arendered) is not None, repr(arendered))


# ── A6.4 - role-aware episodic chunks ─────────────────────────────────────────────
USER_FACTS = (
    "I graduated with a degree in Business Administration in 2019.",
    "I moved from Lisbon to Berlin in March.",
    "My sister Maria had a baby in April.",
    "I now work as a logistics coordinator.",
    "I am learning to play the cello on weekends.",
    "I adopted a greyhound named Pepper.",
)
FACT_MARKERS = (
    "Business Administration",
    "Lisbon to Berlin",
    "sister Maria",
    "logistics coordinator",
    "play the cello",
    "greyhound named Pepper",
    "museum number 1",
    "museum number 6",
)
ASSISTANT_LINES = (
    "You should consider an MBA or an operations role.",
    "Berlin has a strong startup scene, so try networking events.",
    "Congratulations, that is a big family milestone.",
)
ASSISTANT_MARKERS = ("MBA", "networking events", "family milestone")


def _transcript():
    lines = ["Conversation transcript"]          # deliberately carries no date of its own
    for i, fact in enumerate(USER_FACTS):
        lines.append("user: " + fact)
        if i < len(ASSISTANT_LINES):
            lines.append("assistant: " + ASSISTANT_LINES[i])
    lines.extend(f"user: Fact {i}: I visited museum number {i} in Berlin." for i in range(1, 7))
    return "\n".join(lines)


def _a64_episodic(episodic):
    check("A6.4.0 episodic.py is importable", True)
    print("# A6.4 - user facts are dated and role-tagged; assistant context is marked")

    transcript = _transcript()
    raw = episodic.extract_episodic_chunks(transcript, session_date="2023-05-20",
                                           session_id="s1")
    chunks = list(raw) if isinstance(raw, (list, tuple)) else []
    texts = [c for c in chunks if isinstance(c, str)]
    joined = "\n".join(texts)

    check("A6.4.1 a multi-turn transcript splits into more than one chunk",
          len(transcript) > 600 and len(chunks) >= 2,
          f"{len(transcript)} chars -> {len(chunks)} chunk(s)")
    check("A6.4.2 every chunk is a non-empty string",
          bool(chunks) and len(texts) == len(chunks) and all(c.strip() for c in texts),
          repr([type(c).__name__ for c in chunks[:3]]))
    check("A6.4.3 every chunk is at most 600 characters",
          bool(texts) and all(len(c) <= 600 for c in texts),
          repr(sorted((len(c) for c in texts), reverse=True)[:3]))
    missing = [mark for mark in FACT_MARKERS if mark not in joined]
    check("A6.4.4 no user statement is lost in the split", not missing,
          f"missing: {missing}")

    bad_user = [mark for mark in FACT_MARKERS
                if not any(mark in c and "[Date: 2023-05-20 | Speaker: user]" in c
                           for c in texts)]
    check("A6.4.5 user statements carry [Date: ... | Speaker: user]", not bad_user,
          f"untagged: {bad_user[:3]}")

    bad_assistant = [mark for mark in ASSISTANT_MARKERS
                     if mark in joined
                     and not all("[Assistant context]" in c
                                 for c in texts if mark in c)]
    check("A6.4.6 assistant turns carry [Assistant context] or are filtered",
          not bad_assistant, f"unmarked: {bad_assistant}")

    # A merged chunk may legitimately carry both tags; the failure is assistant text
    # sitting inside a chunk that claims the USER as its speaker and nothing else.
    confused = [mark for mark in ASSISTANT_MARKERS
                if any(mark in c and "Speaker: user" in c and "[Assistant context]" not in c
                       for c in texts)]
    check("A6.4.7 no assistant text is presented as the user's own", not confused,
          f"confused: {confused}")

    other = episodic.extract_episodic_chunks(transcript, session_date="2024-01-02",
                                             session_id="s1")
    other_joined = "\n".join(str(c) for c in (list(other) if isinstance(other, (list, tuple))
                                              else []))
    check("A6.4.8 the session_date argument drives the chunk date tag",
          "2024-01-02" in other_joined and "2023-05-20" not in other_joined,
          other_joined[:200])

    pure = "assistant: " + ASSISTANT_LINES[0] + "\nassistant: " + ASSISTANT_LINES[1]
    out = list(episodic.extract_episodic_chunks(pure, session_date="2023-05-20",
                                                session_id="s1") or [])
    check("A6.4.9 an assistant-only transcript never becomes a user fact",
          not any("Speaker: user" in str(c) for c in out)
          and all("[Assistant context]" in str(c) for c in out),
          repr(out[:2]))

    again = episodic.extract_episodic_chunks(transcript, session_date="2023-05-20",
                                             session_id="s1")
    check("A6.4.10 chunking is deterministic for the same input",
          list(again) == chunks, "two calls differ")


# ── A6.5 - calendar phrases, epistemic tags, question carryover ────────────────────
#: The requirement's exact exchange. Turn 1 ends with a question; turn 2 is the next
#: speaker's answer, so the chunk that holds the answer must also hold what it answers.
A65_EXCHANGE = ("Melanie: We are going camping. Any fun plans for the summer?\n"
                "Caroline: Researching adoption agencies.")
A65_ANSWER = "(re: Any fun plans for the summer?) Researching adoption agencies."
#: The same exchange with a long first turn: the answer cannot share the question's chunk,
#: which is the split the carryover exists for. "I am planning the trip. " is 24 chars, so
#: 21 of them plus the question keep Melanie's tagged turn under the 600-character ceiling
#: and force Caroline's tagged answer into a second chunk.
A65_SPLIT_EXCHANGE = ("Melanie: " + "I am planning the trip. " * 21
                      + "Any fun plans for the summer?\n"
                      + "Caroline: Researching adoption agencies.")


def _a65_reply_chunks(episodic, transcript):
    """`(all chunks, chunks holding Caroline's answer)` for one A6.5 exchange."""
    raw = episodic.extract_episodic_chunks(transcript, session_date="2023-05-25",
                                           session_id="a65")
    chunks = [c for c in raw if isinstance(c, str)] if isinstance(raw, (list, tuple)) else []
    return chunks, [c for c in chunks if "Researching adoption agencies." in c]


def _a65(modes, temporal, episodic):
    print("# A6.5 - calendar phrases, epistemic tags, question carryover")

    if temporal is None:
        _fail_checks(PLANNED["A6.5"][:6], "temporal.py not importable")
    else:
        for name, phrase, anchor, expected in A65_TEMPORAL_CASES:
            got = temporal.resolve_relative_dates(phrase, anchor)
            check(name, got == expected, repr(got))

        #: Idempotence is independent of the first-pass assertions above: a resolver that
        #: returns its input unchanged is stable but still fails A6.5.1-A6.5.5, so one
        #: defect cannot hide behind the other.
        second_pass = []
        for _name, phrase, anchor, _expected in A65_TEMPORAL_CASES:
            once = temporal.resolve_relative_dates(phrase, anchor)
            twice = temporal.resolve_relative_dates(once, anchor)
            if twice != once:
                second_pass.append(f"{phrase!r}: {once!r} -> {twice!r}")
        check(A65_IDEMPOTENT, not second_pass, "; ".join(second_pass))

    if modes is None:
        _fail_checks([A65_RUBRIC_CONFIRMED, A65_RUBRIC_HYPOTHETICAL],
                     "modes.py not importable")
    else:
        rubric = modes.mode_prompt_rubric("all")
        rubric = rubric if isinstance(rubric, str) else ""
        check(A65_RUBRIC_CONFIRMED, "[Confirmed fact]" in rubric, rubric[:200])
        check(A65_RUBRIC_HYPOTHETICAL,
              "[Hypothetical / Assistant suggestion]" in rubric, rubric[:200])

    if episodic is None:
        _fail_checks([A65_REPLY_CONTEXT, A65_REPLY_CONTEXT_SPLIT],
                     "episodic.py not importable")
    else:
        chunks, carriers = _a65_reply_chunks(episodic, A65_EXCHANGE)
        check(A65_REPLY_CONTEXT,
              bool(carriers) and all(A65_ANSWER in c for c in carriers), repr(chunks))

        chunks, carriers = _a65_reply_chunks(episodic, A65_SPLIT_EXCHANGE)
        check(A65_REPLY_CONTEXT_SPLIT,
              bool(carriers) and all(A65_ANSWER in c for c in carriers)
              and all("Speaker: Melanie" not in c for c in carriers),
              f"{len(chunks)} chunk(s): {[c[:80] for c in chunks]}")


# ── A6.4 / M34 - a read must not mutate the telemetry store ────────────────────────
RECALL_CHILD = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import memory_hook as m
m._rebase_vault(Path(sys.argv[2]))
m.embed_text = lambda *a, **k: None
m.embedder_available = lambda *a, **k: False
import api
hits = api.recall("painting projects", project="proj", k=3, xrerank=False)
print("hits=" + str(len(hits)))
"""


def _child_env(vault):
    """The parent's environment minus every way to reach another store or a real model."""
    blocked = set()
    for name in sandbox_guard.LOCATION_VARS:
        blocked.update(sandbox_guard.under_every_prefix(name))
    env = {k: v for k, v in os.environ.items()
           if k not in blocked
           and not any(s in k for s in ("SANDBOX", "READONLY", "SEALED", "TELEMETRY",
                                        "CEREBRAS", "GROQ", "DEEPSEEK", "GEMINI",
                                        "OPENAI", "VOYAGE", "COHERE"))}
    (vault / "transcripts").mkdir(parents=True, exist_ok=True)
    env.update({
        "NEVERTWICE_HOME": str(vault),
        "NEVERTWICE_VAULT": str(vault),
        "NEVERTWICE_CLOUD": "none",
        "NEVERTWICE_XRERANK": "0",
        "NEVERTWICE_PROJECTS_ROOT": str(vault / "transcripts"),
        "NEVERTWICE_DOTENV": "explicit",
    })
    return env


def _recall_child(vault, extra_env):
    env = _child_env(vault)
    env.update(extra_env)
    r = subprocess.run([sys.executable, "-c", RECALL_CHILD, str(PKG), str(vault)],
                       capture_output=True, text=True, env=env, timeout=120)
    found = re.search(r"^hits=(\d+)$", r.stdout, re.M)
    return r, int(found.group(1)) if found else -1


def _seed_telemetry():
    primary = m.VAULT / "telemetry.json"
    bak = primary.with_name(primary.name + ".bak")
    payload = json.dumps({
        "schema_version": 1,
        "created": "2023-05-20",
        "marker": "M34-seeded-not-a-search-log",
        "search_latency": {"samples": [], "count": 0},
    }, indent=1) + "\n"
    primary.write_text(payload, encoding="utf-8", newline="")
    bak.write_text(payload, encoding="utf-8", newline="")
    return primary, bak, primary.read_bytes(), bak.read_bytes()


def _m34(vault):
    print("# M34 - a read against a read-only store leaves telemetry.json(.bak) alone")

    for ident, label, extra in (
        (("A6.4.11", "A6.4.12", "A6.4.13"), "NEVERTWICE_TELEMETRY_READONLY=1",
         {"NEVERTWICE_TELEMETRY_READONLY": "1"}),
        (("A6.4.14", "A6.4.15", "A6.4.16"), "NEVERTWICE_SANDBOX=1",
         {"NEVERTWICE_SANDBOX": "1"}),
    ):
        primary, bak, before_p, before_b = _seed_telemetry()
        r, hits = _recall_child(vault, extra)
        check(f"{ident[0]} {label}: the read completes and returns the note",
              r.returncode == 0 and hits >= 1,
              f"exit {r.returncode} hits {hits}: {r.stderr[-300:]}")
        check(f"{ident[1]} {label}: telemetry.json is unchanged",
              primary.read_bytes() == before_p,
              f"{len(primary.read_bytes())} != {len(before_p)} bytes")
        check(f"{ident[2]} {label}: telemetry.json.bak is unchanged",
              bak.read_bytes() == before_b,
              f"{len(bak.read_bytes())} != {len(before_b)} bytes")

    # Positive control: with neither flag, the same read DOES write. Without this, a read
    # path that never recorded anything at all would pass the two checks above.
    primary, _bak, before_p, _before_b = _seed_telemetry()
    r, hits = _recall_child(vault, {})
    check("A6.4.17 control (no read-only flag): the read records, so the checks can fail",
          r.returncode == 0 and hits >= 1 and primary.read_bytes() != before_p,
          f"exit {r.returncode} hits {hits} changed={primary.read_bytes() != before_p}")


# ── run ────────────────────────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory(prefix="a6vault_") as td:
    #: PROJECTS_ROOT first: `_rebase_vault` derives `_PROJECTS_ROOT_NORM` from it, and
    #: setting it afterwards leaves the path-classification constants describing the
    #: sandbox this suite inherited instead of this one (tests/_sandbox.py's reason).
    m.PROJECTS_ROOT = Path(td) / "transcripts"
    m.PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    m._rebase_vault(Path(td))
    #: The suites' own sandbox pins this; this file does not use make_sandbox, so the module
    #: constant that decides whether an explicit supersede retires or waits for the sleep-time
    #: judge is pinned here. `write` is the documented default, and the requirement's Day-2
    #: note is a supersede, not a judge holding pattern.
    m.EXPLICIT_RETIRE = "write"
    #: Quarantine would put a note on disk but keep it out of the cache and recall (W7), so an
    #: ambient NEVERTWICE_QUARANTINE would make A6.3 assert the wrong path. Pin the default.
    m.QUARANTINE_MODE = False

    #: No embedder: every cache entry is text-only, which keeps the run offline and makes
    #: `api.recall` use its lexical fallback (the same path a no-model user gets).
    m.embed_text = lambda *a, **k: None
    m.embedder_available = lambda *a, **k: False

    print("# A6 memory upgrade")
    modes_mod, modes_err = _load("modes")
    if modes_mod is None:
        _fail_checks(PLANNED["A6.1"], f"modes.py not importable ({modes_err})")
    else:
        _run_section("A6.1", _a61, modes_mod)

    temporal_mod, temporal_err = _load("temporal")
    if temporal_mod is None:
        _fail_checks(PLANNED["A6.2"], f"temporal.py not importable ({temporal_err})")
    else:
        _run_section("A6.2", _a62, temporal_mod)

    #: A6.3's last clause: the prior value must come from the store, not from another model
    #: call. Any path that reaches the extractor now raises, and the section reports it.
    def _no_llm(*_a, **_k):
        raise AssertionError("A6.3 must preserve the prior value without an LLM call")

    m.generate_json = _no_llm
    _run_section("A6.3", _a63, Path(td))

    episodic_mod, episodic_err = _load("episodic")
    if episodic_mod is None:
        _fail_checks(PLANNED["A6.4"], f"episodic.py not importable ({episodic_err})")
    else:
        _run_section("A6.4", _a64_episodic, episodic_mod)

    #: A6.5 spans all three modules, so it takes the already-loaded handles and reports the
    #: missing one per check instead of hiding the rest behind a single import error.
    _run_section("A6.5", _a65, modes_mod, temporal_mod, episodic_mod)

    #: M34 is a property of the READ path, not of the episodic module, so it runs whether
    #: or not `episodic.py` has landed - otherwise the missing module would hide the one
    #: section that can go green at baseline.
    _run_section("A6.4-M34", _m34, Path(td))

print()
print(f"A6 memory upgrade: {P} passed, {F} failed")
sys.exit(1 if F else 0)
