"""Does a memory return a fact that has since been retracted?

Every public memory benchmark - LoCoMo, LongMemEval, BEAM - measures whether a system
*recalls* a fact. None measures whether it hands back a fact that is no longer true. For a
chat companion that is a nuance. For a coding agent it is the whole problem: an agent acting
on a withdrawn fact writes wrong code, confidently, and the wrongness is invisible until it
runs.

So this stand measures three things at once, because measuring only the first is trivially
gamed by a system that returns nothing:

* **stale rate** - the retracted fact came back. Lower is better; 0 is perfect.
* **current rate** - the replacement came back. Higher is better. A system that answers
  "I have nothing" scores a perfect stale rate and a zero current rate, and the pair of
  numbers says so immediately.
* **over-retraction rate** - measured on control cases where two facts are simply *different*
  and both remain true. A system that suppresses one of those is not careful, it is lossy.
  Without this column, "delete on any doubt" would look like the winning strategy.

**Arms.** `nevertwice` through the same public entry point a user gets (`api.capture_session`
then `api.recall`); `mem0` through `Memory.add` / `Memory.search`, one call per session so both
extract from the same unit the same number of times; and `naive`, an append-only
markdown store with BM25 retrieval and no supersession mechanism at all. The naive arm is the
floor and it is the point: if it also scores well, the benchmark does not separate systems and
must be thrown away rather than published. A benchmark that only one vendor's architecture
fails is a benchmark about that vendor.

Every arm sees the same sessions in the same order, uses the same local embedder, and runs
against the owner's already-running Ollama, so nothing is billed and nothing leaves the
machine.

    python research/supersession_bench.py --probe                # the five-fact Mem0 probe
    python research/supersession_bench.py --arms nevertwice,naive
    python research/supersession_bench.py --arms nevertwice,mem0,naive --out results.json
    python research/supersession_bench.py --arms nevertwice,naive --runs 2 --out pooled.json
    python research/supersession_bench.py --pool run1.json run2.json --with mem0.json --out pooled.json

A registered number comes from `--runs 2` or from `--pool` over two runs; `register_supersession.py`
refuses an artifact pooled over one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import sandbox_guard  # noqa: E402 - must precede any nevertwice import

sandbox_guard.isolate(prefix="nevertwice_supersession_")

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("NEVERTWICE_EMBED_MODEL", "bge-m3")
#: One extraction model for every arm. The Mem0 probe of 2026-08-30 used `qwen3-coder:30b`,
#: so that is the default here: a different model on either side would confound the
#: architecture being measured with the extractor's ability.
LLM = os.environ.get("SUPERSESSION_LLM", "qwen3-coder:30b")

DATASET = HERE / "data" / "supersession_v1.json"


def _code_sha() -> str:
    """One hash over the program a run IS: this stand plus every module of the package.

    Why a hash at all. `--runs N` starts a fresh interpreter per run, so the sources on disk are
    live state between runs. The edit that lands cleanly is the dangerous one: it is silent, and
    it makes the second run a different program from the first. `pool` refuses a mismatch - the
    same guarantee `store` gives from the other side. Two runs must be INDEPENDENT in their
    store and IDENTICAL in their code.

    Why the whole package and not a list. The first draft hashed "the stand plus `_engine*.py`
    plus `memory_hook.py`", chosen as *what this session happened to edit* - which is no basis
    for a permanent guard, and it missed `api.py`, the module through which this stand does
    both of its jobs (`capture_session` and `recall`; `api.py` is a separate module and not part
    of the exec-ed engine body, so the hand-written list did not reach it). A glob maintains
    itself: it survives the engine being cut into a different number of parts, a file moving,
    and a module that does not exist yet.

    Why not `sys.modules`, which would be exact. Because it is not deterministic HERE: run 1
    executes the engine arm and the other arms, run 2 only the engine arm, so the set of loaded
    modules differs between the two runs by construction, and deferred imports inside the engine
    make it differ again by which branches the data took. A guard that fires on two honest runs
    is a guard someone switches off. The glob is coarser and it is the same for every run.

    What it covers, counted rather than assumed: `rglob` walks the whole package, which today is
    63 files - 59 at the top level plus `integrations/` (3) and `invariants/` (1) - and the stand
    itself makes 64 hashed in all. The top-level count alone is the number a person reads off
    `ls`, and writing it beside an `rglob` would leave the comment and the hash disagreeing about
    the subpackages.

    The cost of the coarseness, stated: an edit to a package module this stand never calls also
    changes the hash. That refusal is conservative rather than wrong - between two runs of one
    repeat, nothing in the package should be moving at all. The walk is of the FILESYSTEM and
    not of git, deliberately: an untracked `.py` dropped into the package is importable, so it
    is part of what could execute.
    """
    # The repo-relative POSIX path is both the sort key and the token, and that is the whole
    # portability argument in one line. Not the basename, because `invariants/__init__.py` and
    # the package's own `__init__.py` would contribute the same token and a rename between two
    # files of equal content would leave the hash still. And sorted as STRINGS rather than as
    # `Path` objects, because `sorted(Path...)` is platform-dependent twice over: Windows
    # compares a lowercased string with `\` (0x5C), POSIX the raw string with `/` (0x2F), and
    # digits and capitals lie between those two bytes. Today's 64 sort the same either way
    # - checked - but `nevertwice/Z.py` beside `nevertwice/invariants/` is enough to
    # split them, and then one unchanged tree hashes differently on Windows and on Linux. The
    # refusal would land on the cross-OS CI run, which is exit criterion 4, and be false.
    files = sorted(p.relative_to(ROOT).as_posix()
                   for p in [Path(__file__).resolve()] + list((ROOT / "nevertwice").rglob("*.py")))
    h = hashlib.sha256()
    for rel in files:
        h.update(rel.encode() + b"\0")
        h.update((ROOT / rel).read_bytes())
    return h.hexdigest()[:12]

#: Deterministic extraction, which this stand had never actually asked for. The engine's default
#: is 0.2 - right for the live hook, wrong for a benchmark - and the stands that care pin it to 0.
#: This one did not, so every number it has ever produced was sampled, and the artifact it writes
#: said "the extraction model is not deterministic at temperature 0" about a run that was never at
#: temperature 0.
#:
#: Measured before changing it, same prompt through this engine's own `generate_json`: at the 0.2
#: default, back-to-back calls in one process differ every time (0 of 3 repeats identical, three
#: distinct answers); at 0, every condition tried is identical - back to back, with an unrelated
#: prompt in between, with a long unrelated prompt in between, and from a fresh interpreter (3 of
#: 3, one answer). The run-to-run spread this stand is famous for is sampling, not the model.
#:
#: At module level rather than inside `run_nevertwice`, so that the artifact's
#: `extract_temperature` is the truth for the file and not just for the arm that happened to set
#: it: a `--arms naive` run recorded `null` while the value it would have used was 0.
os.environ["NEVERTWICE_EXTRACT_TEMP"] = "0"


#: The exact probe Mem0 2.0.19 was measured on (2026-08-30, polygon `mem0_eval/`), kept
#: verbatim so this repository can reproduce that comparison rather than cite it. Five turns,
#: two queries, one superseded fact each.
PROBE = {
    "name": "mem0_probe_2026_08_30",
    "cases": [
        {
            "id": "probe-extraction",
            "shape": "value_replaced",
            "sessions": [
                ["The memory system extracts knowledge using Cerebras gpt-oss-120b in the cloud.",
                 "We benchmarked the embedding model and recall at 1% FPR was 0.220.",
                 "The vault lives at D:/Obsidian/Claude_Memory and is under git."],
                ["We switched extraction off Cerebras entirely; it now runs on local Ollama only."],
            ],
            "query": "what does the memory system use for extraction",
            "current": ["ollama"],
            "superseded": ["cerebras"],
        },
        {
            "id": "probe-recall",
            "shape": "value_replaced",
            "sessions": [
                ["We benchmarked the embedding model and recall at 1% FPR was 0.220."],
                ["The embedding model was retrained and recall at 1% FPR is now 0.475."],
            ],
            "query": "what is the recall at 1% FPR",
            "current": ["0.475"],
            "superseded": ["0.220"],
        },
    ],
}


# ── scoring ───────────────────────────────────────────────────────────────────────────────

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson interval. Normal approximation puts 0/12 at [0, 0], which reads as certainty."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - r) / d), min(1.0, (c + r) / d))


_SEP = re.compile(r"[\s_\-]+")


def _norm(text: str) -> str:
    """Fold separators so a marker survives the extractor's punctuation choices.

    Run one on the raw text and seven of the twenty control cases failed on nothing but
    spelling: the marker said `two approval` and the note said `two-approval`, the marker
    said `8 kb` and the note said `8KB`. A benchmark that scores those as a memory failure
    is measuring hyphens.
    """
    return _SEP.sub(" ", text.lower())


def _hit(markers: list[str], text: str) -> bool:
    low = _norm(text)
    return any(_norm(m) in low for m in markers)


def classify(items: list[str], case: dict) -> dict:
    """Per returned item: does it *assert* the retracted fact, or merely name it?

    The first scoring pass matched the retracted value anywhere in the returned text, and
    it was wrong in a way that flattered no one and confused everything. Nevertwice answered
    the extraction query with a decision note titled *disable-cerebras-integration* - the
    current truth, which cannot be stated without naming the thing that was disabled - and a
    substring test called that stale. Mem0 returned the original sentence *"extracts
    knowledge using Cerebras gpt-oss-120b"*, which asserts the retracted state as fact.
    Those are opposite outcomes and the metric could not tell them apart.

    So an item counts as a **stale assertion** only when it carries a retracted marker and
    **no** current marker: a text naming both is describing the change, not asserting the old
    state. The rank of the first such item is reported too, because a system that surfaces a
    stale assertion at position five is not in the same condition as one that puts it first.
    """
    stale_ranks = [i + 1 for i, t in enumerate(items)
                   if _hit(case.get("superseded", []), t)
                   and not _hit(case.get("current", []), t)]
    return {"stale_returned": bool(stale_ranks),
            "stale_rank": stale_ranks[0] if stale_ranks else None,
            "current_returned": any(_hit(case.get("current", []), t) for t in items),
            # K8: the raw reading beside the rule above - the retracted value anywhere in what came
            # back, attached to a newer statement or not. A memory that serves the earlier statement
            # paired with its replacement is not stale by the rule and is counted here.
            "old_value_served": any(_hit(case.get("superseded", []), t) for t in items)}


def score(rows: list[dict]) -> dict:
    """Fold per-case outcomes into the rates plus the token floor.

    Control cases carry no `superseded` markers and are scored on the two control measures only;
    supersession cases are scored on stale and current. Mixing them into one denominator
    would let a system trade one axis for the other invisibly.

    The control column has two strengths, and until 2026-09-11 both were called
    `over_retraction_rate`: **control miss** - the still-true fact did not come back, for any
    cause, which every arm can be scored on - and **over-retraction** proper - the memory retired
    it, which only a store that records a retirement can show. The first is
    `control_miss_rate` here; the second keeps the old name and is `None` for an arm whose store
    the run did not read, never a number computed from the other.
    """
    sup = [r for r in rows if r["shape"] != "control"]
    ctl = [r for r in rows if r["shape"] == "control"]
    stale = sum(1 for r in sup if r["stale_returned"])
    old_served = sum(1 for r in sup if r.get("old_value_served"))
    current = sum(1 for r in sup if r["current_returned"])
    missed = sum(1 for r in ctl if not r["current_returned"])
    chars = [r["chars_returned"] for r in rows]
    out = {
        "n_supersession": len(sup),
        "n_control": len(ctl),
        "stale_rate": round(stale / len(sup), 4) if sup else None,
        "stale_ci": [round(x, 4) for x in wilson(stale, len(sup))] if sup else None,
        "current_rate": round(current / len(sup), 4) if sup else None,
        "current_ci": [round(x, 4) for x in wilson(current, len(sup))] if sup else None,
        "old_value_served_rate": round(old_served / len(sup), 4) if sup else None,
        "control_miss_rate": round(missed / len(ctl), 4) if ctl else None,
        "control_miss_ci": [round(x, 4) for x in wilson(missed, len(ctl))] if ctl else None,
        "over_retraction_rate": None,
        "over_retraction_ci": None,
        "mean_chars_returned": round(sum(chars) / len(chars), 1) if chars else 0.0,
        "max_chars_returned": max(chars) if chars else 0,
        "stale_at_rank_1": sum(1 for r in sup if r.get("stale_rank") == 1),
    }
    if ctl and any("current_live" in r for r in ctl):
        lost = [r for r in ctl if not r["current_returned"]]
        out["control_retired_by_memory"] = sum(1 for r in lost if r.get("current_retired"))
        out["control_demoted_by_merge"] = sum(1 for r in lost if r.get("current_demoted"))
        out["control_never_written"] = sum(1 for r in lost if r.get("current_absent"))
        out["control_written_but_unranked"] = sum(
            1 for r in lost if r.get("current_live") and not r["current_returned"])
        n_ctl = len(ctl)
        # over-retraction proper: the memory stopped serving a still-true fact, whether it moved
        # the note to Superseded/ or absorbed another fact into it (K1b)
        # one row is one control case-run: a row both retired and demoted is one loss, not two (as
        # `pool()` counts it; the per-run figure double-counted until 2026-09-16)
        eager = sum(1 for r in lost if r.get("current_retired") or r.get("current_demoted"))
        out["over_retraction_rate"] = round(eager / n_ctl, 4)
        out["over_retraction_ci"] = [round(x, 4) for x in wilson(eager, n_ctl)]
    drifted = [r for r in rows if r.get("notes_in_cyrillic")]
    if any("notes_written" in r for r in rows):
        wrote = sum(r.get("notes_written", 0) for r in rows)
        cyr = sum(r.get("notes_in_cyrillic", 0) for r in rows)
        out["notes_written"] = wrote
        out["notes_in_cyrillic"] = cyr
        out["language_drift_rate"] = round(cyr / wrote, 4) if wrote else 0.0
        out["cases_touched_by_drift"] = len(drifted)
    return out


# ── arm: nevertwice ───────────────────────────────────────────────────────────────────────

def _served_text(body: str) -> str:
    """What recall hands back for this note: its title, description and prevention - the same
    three fields `hit_text` scores. The rest of the file (frontmatter, `## Previous statement`,
    related notes) is on disk and is not served."""
    import memory_hook as m                                     # noqa: PLC0415
    lines = body.split("\n")
    title = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("# ")), "")
    try:
        _, desc, prevention = m._parse_note_body(lines)
    except Exception:                                           # noqa: BLE001 - an unparsable note serves its title
        desc = prevention = ""
    return " ".join(p for p in (title, desc or "", prevention or "") if p)


def _statement_text(body: str) -> str:
    """The served text plus the `## Previous statement` block - where a fact can be on disk and not
    served (K1b). Not the frontmatter, not the `_Supersedes:_` / related-notes links: a link to a
    retired stem names the retired fact's slug and read as a demotion of the winner (2026-09-16)."""
    prev = re.search(r"^## Previous statement\s*\n((?:- .*\n?)+)", body, re.M)
    return _served_text(body) + " " + (prev.group(1) if prev else "")


def _store_state(project: str, case: dict) -> dict:
    """Where the current fact ended up: served by a live note, demoted inside one, retired, or
    never written.

    `current_returned=False` on a control case was reported as over-retraction, and that was
    several failures wearing one number. A fact can be missing because the memory retired it
    (moved to `Superseded/`), because the memory absorbed a *different* fact into the note and
    rewrote what it serves - session one's statement survives only under `## Previous statement`
    (K1b, 2026-09-11) - because retrieval ranked a note that does serve it below k, or because
    extraction never wrote it. The first two are the memory being too eager; until K1b the second
    was read as the third, because the marker was matched anywhere in the file.
    """
    import sandbox_guard as sg                                  # noqa: PLC0415
    root = Path(sg.store())
    live = retired = demoted = False
    drift = 0
    total = 0
    for folder in ("Patterns", "Mistakes", "Decisions"):
        d = root / folder
        if not d.exists():
            continue
        for md in list(d.glob(f"*-{project}-*.md")) + list(
                (d / "Superseded").glob(f"*-{project}-*.md") if (d / "Superseded").exists() else []):
            body = md.read_text(encoding="utf-8", errors="replace")
            total += 1
            if sum(1 for ch in body if "\u0400" <= ch <= "\u04ff") > 20:
                drift += 1
            in_body = _hit(case["current"], _statement_text(body))
            if not in_body:
                continue
            if md.parent.name == "Superseded":
                retired = True
            elif _hit(case["current"], _served_text(body)):
                live = True
            else:
                demoted = True
    demoted = demoted and not live
    return {"current_live": live, "current_retired": retired, "current_demoted": demoted,
            "current_absent": not (live or retired or demoted), "notes_written": total,
            "notes_in_cyrillic": drift}


def _env_int_safe(name: str, default: int) -> int:
    """also-fix (xhigh review): a bare `int(os.environ[...])` at import made a mistyped
    K8_SLEEP_BUDGET/K8_SLEEP_CAP kill this whole tool (and asof_bench/k8_collisions, which
    import it) with an import-time ValueError - `memory_hook.env_int` is not imported at
    module level here (it is loaded lazily, inside a function), so this mirrors its degrade-
    to-default behaviour locally instead of promoting that import to module scope."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


# K8: the judge's budget per consolidation run on the stand - the engine's default (100k tokens a run,
# ~240 pairs) unless `K8_SLEEP_BUDGET` says otherwise; `K8_SLEEP_CAP` > 0 adds a hard cap on calls (the
# first fast cycles ran at 50 calls; amended before the campaign, naryad K8-B).
SLEEP_BUDGET = _env_int_safe("K8_SLEEP_BUDGET", 0) or None
SLEEP_CAP = _env_int_safe("K8_SLEEP_CAP", 0) or None


def _sleep_and_reread(api, cases: list[dict], k: int, prefix: str) -> dict:
    """The second reading (K8): the sleep-time judge over every contested pair of the store, then
    recall again on every case. What a user sees after the weekly consolidation, against the first
    reading - what they see between nights."""
    from nevertwice import consolidate_memory as cm                # noqa: PLC0415
    t1 = time.time()
    adj = cm.adjudicate_contested(apply=True, has_llm=True, cap=SLEEP_CAP, budget=SLEEP_BUDGET)
    rows = []
    for i, case in enumerate(cases):
        project = f"{prefix}{i:03d}"
        hits = api.recall(case["query"], project=project, k=k)
        rows.append({**_row(case, [hit_text(h) for h in hits]), **_store_state(project, case)})
    return {"rows": rows, **score(rows), "adjudication": adj,
            "seconds": round(time.time() - t1, 1), "store_bytes": store_bytes(),
            "config": f"the same store after consolidate_memory.adjudicate_contested(budget={adj['budget']}, cap={adj['cap']})"}


def run_nevertwice(cases: list[dict], k: int, sleep: bool = False) -> dict:
    """The public path: one `capture_session` per session, then `recall`. `sleep` adds the second
    reading of K8: the judge over the contested pairs, then recall again (`after_sleep`).

    A separate project per case, so one case cannot retrieve another's notes - the same
    isolation a real user gets from working in different repositories, and without it the
    distractor sessions of case 7 would pollute case 8's ranking for reasons that have
    nothing to do with supersession.
    """
    os.environ["NEVERTWICE_CLOUD"] = "none"          # local only: nothing billed, nothing sent
    os.environ["NEVERTWICE_MODEL"] = LLM
    os.environ.setdefault("NEVERTWICE_EMBED_MODEL", EMBED_MODEL)
    try:
        from nevertwice import api
    except Exception as e:                            # pragma: no cover - import-time only
        return {"blocked": f"nevertwice import failed ({type(e).__name__}: {e})"}

    rows, t0 = [], time.time()
    #: What this pass DID, as opposed to what it found lying in the store. `capture_session`
    #: returns `stored: False` whenever `process_session` did not write, and a repeat that
    #: stored nothing has measured nothing - see `pool`, which refuses that. `notes_written`
    #: cannot stand in for this: it counts the notes present when a case is scored, so an
    #: inherited store reports the first run's work as the second run's.
    #:
    #: The second counter is named for what is OBSERVED, not for a cause. `stored: False` is one
    #: bit standing for four different endings of `process_session` - already processed, cwd not
    #: a tracked project, empty transcript, and extraction failed (four `return False` branches,
    #: `_engine_cards.py` 870 / 882 / 913 / 952, against one `return True`). Only the first is
    #: "skipped"; the last is a broken model, which is when a message pointing at the wrong cause
    #: does the most harm. The reason does not reach this side: `run_log` is appended only on the
    #: success path, so nothing carries it out (audit 2026-09-22 - reported, not fixed here,
    #: because the reason would have to travel through `run_log`, which six places in four
    #: files read and not one of them the same way: `api.py:688` reads it UNGUARDED and puts its
    #: fields in `capture_session`'s public return, `ingest.py:599` and `process_now.py:137`
    #: read it behind `if ok` and would never see a failure entry, and three `write_status`
    #: calls take the whole list. That is a public-API change plus three status surfaces, and it
    #: deserves its own commit).
    ingested = not_stored = 0
    for i, case in enumerate(cases):
        project = f"sup{i:03d}"
        try:
            for j, session in enumerate(case["sessions"]):
                r = api.capture_session("\n".join(session), project=project,
                                        session_id=f"{project}-s{j}", trigger="ingest")
                if isinstance(r, dict) and r.get("stored"):
                    ingested += 1
                else:
                    not_stored += 1
        except Exception as e:
            rows.append({**_blank(case), "error": f"{type(e).__name__}: {e}"})
            continue
        hits = api.recall(case["query"], project=project, k=k)
        items = [hit_text(h) for h in hits]
        rows.append({**_row(case, items), **_store_state(project, case)})
        print(f"  [{i + 1}/{len(cases)}] {case['id']}  hits={len(hits)}"
              f"  stale={rows[-1]['stale_returned']}@{rows[-1]['stale_rank']}", flush=True)
    out = {"rows": rows, **score(rows), "seconds": round(time.time() - t0, 1),
           "sessions_ingested": ingested, "sessions_not_stored": not_stored,
           "config": f"ollama {LLM} + {EMBED_MODEL}, k={k}",
           "store_bytes": store_bytes()}
    if sleep:
        out["after_sleep"] = _sleep_and_reread(api, cases, k, "sup")
    return out


def hit_text(h: dict) -> str:
    """What a recall hit hands the agent, as one string the markers are matched against:
    title, description and prevention. The stale and current markers see exactly the text a
    user's agent would see; characters per query are counted on the same string."""
    parts = [str(h.get(f) or "") for f in ("title", "description", "prevention")]
    return " ".join(p for p in parts if p)


def store_bytes() -> int:
    """Bytes of typed notes in the sandbox store, live and retired."""
    import sandbox_guard as sg                                  # noqa: PLC0415
    root = Path(sg.store())
    total = 0
    for folder in ("Patterns", "Mistakes", "Decisions"):
        d = root / folder
        if d.exists():
            total += sum(p.stat().st_size for p in d.rglob("*.md"))
    return total


def _state_from_texts(case: dict, live_texts: list[str], retired_texts: list[str],
                      demoted_texts: list[str] = ()) -> dict:
    """The store-state flags from what an arm's store holds: `live_texts` are the items it would
    still return, `retired_texts` the ones it invalidated, expired or deleted, `demoted_texts` the
    previous texts of items it rewrote so that the marker left them (Mem0's UPDATE - the analogue
    of our twin absorb). The same flags `_store_state` reads for our arm, so the cause split is
    computed by one rule for all."""
    markers = case.get("current", [])
    live = any(_hit(markers, t) for t in live_texts)
    retired = any(_hit(markers, t) for t in retired_texts)
    demoted = (not live) and any(_hit(markers, t) for t in demoted_texts)
    return {"current_live": live, "current_retired": retired, "current_demoted": demoted,
            "current_absent": not (live or retired or demoted)}


# ── arm: mem0 ─────────────────────────────────────────────────────────────────────────────

def run_mem0(cases: list[dict], k: int) -> dict:
    """Mem0 2.0.19 through its documented local configuration.

    Blocked rather than skipped when the package is absent: an arm that quietly disappears
    from a comparison table is how a missing competitor becomes a won one.
    """
    try:
        from mem0 import Memory
    except ImportError:
        return {"blocked": "mem0 not installed here - `pip install mem0ai ollama`"}
    for var in ("MEM0_TELEMETRY", "MEM0_TELEMETRY_ENABLED", "ANONYMIZED_TELEMETRY"):
        os.environ.setdefault(var, "False")

    base = Path(tempfile.mkdtemp(prefix="nevertwice_sup_mem0_"))
    cfg = {
        "llm": {"provider": "ollama", "config": {
            "model": LLM, "temperature": 0.0, "ollama_base_url": OLLAMA_BASE}},
        "embedder": {"provider": "ollama", "config": {
            "model": EMBED_MODEL, "ollama_base_url": OLLAMA_BASE}},
        "vector_store": {"provider": "qdrant", "config": {
            "collection_name": "sup", "path": str(base / "qdrant"), "on_disk": True,
            "embedding_model_dims": 1024}},
    }
    # ONE instance, cases separated by `user_id`. A per-case instance is the obvious shape
    # and it does not work: Mem0 keeps a process-global migrations store under ~/.mem0 and
    # the second `Memory.from_config` in a process dies with "already accessed by another
    # instance of Qdrant client". Seventy-nine of eighty cases failed that way on the first
    # run and the arm still produced a summary line - which is exactly how a broken arm gets
    # published as a win, so the per-case error is now counted and printed.
    try:
        mem = Memory.from_config(cfg)
    except Exception as e:
        return {"blocked": f"Mem0 init failed ({type(e).__name__}: {e})"}

    rows, t0 = [], time.time()
    for i, case in enumerate(cases):
        uid = f"sup{i:03d}"
        try:
            # One `add` per session, not per sentence: `capture_session` gets a whole session
            # in one call, so a per-sentence loop here would hand Mem0 twice the extraction
            # opportunities and charge it twice the latency for the same corpus. Same unit,
            # same count, same order.
            events: list[dict] = []
            for session in case["sessions"]:
                added = mem.add("\n".join(session), user_id=uid)
                if isinstance(added, dict) and isinstance(added.get("results"), list):
                    events += [e for e in added["results"] if isinstance(e, dict)]
            res = mem.search(case["query"], filters={"user_id": uid}, limit=k)
            got = res.get("results", res) if isinstance(res, dict) else res
            texts = [r.get("memory", "") for r in got] if isinstance(got, list) else []
            # what the store holds, for the cause of a control miss: every memory still stored is
            # live; a DELETE event's text, or an UPDATE whose previous text carried the marker and
            # whose new text does not, is a retirement Mem0 itself decided
            # Mem0 2.0.19's v2 API takes the scope as a filter; the keyword form raises
            # "Top-level entity parameters ... not supported in get_all()" and blocked the arm
            held = mem.get_all(filters={"user_id": uid})
            held = held.get("results", held) if isinstance(held, dict) else held
            live_texts = [r.get("memory", "") for r in held] if isinstance(held, list) else []
            retired_texts = [str(e.get("memory") or "") for e in events if e.get("event") == "DELETE"]
            demoted_texts = [str(e.get("previous_memory") or "") for e in events
                             if e.get("event") == "UPDATE"
                             and not _hit(case.get("current", []), str(e.get("memory") or ""))]
        except Exception as e:
            rows.append({**_blank(case), "error": f"{type(e).__name__}: {e}"})
            print(f"  [{i + 1}/{len(cases)}] {case['id']}  ERROR {type(e).__name__}", flush=True)
            continue
        rows.append({**_row(case, texts), **_state_from_texts(case, live_texts, retired_texts, demoted_texts)})
        print(f"  [{i + 1}/{len(cases)}] {case['id']}  hits={len(texts)}"
              f"  stale={rows[-1]['stale_returned']}@{rows[-1]['stale_rank']}", flush=True)
    shutil.rmtree(base, ignore_errors=True)
    failed = sum(1 for r in rows if r.get("error"))
    if failed > len(rows) * 0.1:
        return {"blocked": f"{failed} of {len(rows)} cases errored - "
                           f"first: {next(r['error'] for r in rows if r.get('error'))}",
                "rows": rows}
    return {"rows": rows, **score(rows), "errors": failed,
            "seconds": round(time.time() - t0, 1),
            "config": f"mem0 ollama {LLM} + {EMBED_MODEL}, limit={k}"}


# ── arm: naive append-only markdown + BM25 ────────────────────────────────────────────────

_WORD = re.compile(r"[a-z0-9_.]+")


def run_naive(cases: list[dict], k: int) -> dict:
    """The floor: keep every turn, retrieve by term overlap, never retract anything.

    This is what a person does by hand before installing anything, and it is the arm that
    decides whether this benchmark is worth publishing. It has no extraction model, so it is
    also the only arm whose result cannot be blamed on the LLM.
    """
    rows, t0 = [], time.time()
    for case in cases:
        docs = [t for s in case["sessions"] for t in s]
        q = set(_WORD.findall(case["query"].lower()))
        df = {}
        toks = [set(_WORD.findall(d.lower())) for d in docs]
        for ts in toks:
            for w in ts:
                df[w] = df.get(w, 0) + 1
        n = len(docs)
        scored = []
        for d, ts in zip(docs, toks):
            s = sum(math.log(1 + n / df[w]) for w in q & ts)
            scored.append((s, d))
        scored.sort(key=lambda x: -x[0])
        texts = [d for s, d in scored[:k] if s > 0]
        # the floor's store is every sentence and it never retires one, so a control miss here
        # can only be a ranking miss - read from the store like the other arms rather than assumed
        rows.append({**_row(case, texts), **_state_from_texts(case, [" ".join(docs)], [])})
    return {"rows": rows, **score(rows), "seconds": round(time.time() - t0, 1),
            "config": f"append-only markdown + BM25-style IDF overlap, k={k}"}


# ── plumbing ──────────────────────────────────────────────────────────────────────────────

def _blank(case: dict) -> dict:
    return {"id": case["id"], "shape": case["shape"], "n_hits": 0,
            "stale_returned": False, "stale_rank": None, "current_returned": False,
            "chars_returned": 0, "returned": []}


def _row(case: dict, items: list[str]) -> dict:
    return {"id": case["id"], "shape": case["shape"], "n_hits": len(items),
            **classify(items, case),
            "chars_returned": sum(len(t) for t in items),
            "returned": items}


# ── arm: Zep / Graphiti ───────────────────────────────────────────────────────────────────

def run_zep(cases: list[dict], k: int) -> dict:
    """Zep's engine, graphiti-core, through `research/_graphiti_arm.py` (ledger J4): one episode
    per session with the stand's extractor and embedder behind Ollama's OpenAI-compatible
    endpoint, one graph group per case, hybrid edge search fused by reciprocal rank. The facts
    of the top edges are what it hands back; edges it invalidated or expired itself are hidden,
    as its own `search()` hides them. Blocked - never a number - when graphiti-core or the
    FalkorDB it needs is absent here, and when more than a tenth of the cases error."""
    sys.path.insert(0, str(HERE))
    import _graphiti_arm as ga                                  # noqa: PLC0415
    why = ga.available()
    if why:
        return {"blocked": why}
    try:
        arm = ga.GraphitiArm(LLM, EMBED_MODEL)
    except Exception as e:                                      # noqa: BLE001
        return {"blocked": f"Graphiti init failed ({type(e).__name__}: {e})"}
    rows, t0 = [], time.time()
    for i, case in enumerate(cases):
        group = f"sup{i:03d}"
        r = arm.ingest(group, [("\n".join(session), None) for session in case["sessions"]])
        if r["errors"]:
            rows.append({**_blank(case), "error": getattr(arm, "last_error", "episode error")})
            print(f"  [{i + 1}/{len(cases)}] {case['id']}  ERROR {rows[-1]['error'][:60]}", flush=True)
            continue
        texts = arm.search_now(group, case["query"], k)
        row = _row(case, texts)
        edges = arm.edges_all(group)
        if edges is not None:
            # an edge Graphiti invalidated or expired is a retirement it decided; one it still
            # holds is live - the same three flags our arm reads from its own store
            row.update(_state_from_texts(case, [f for f, ended in edges if not ended],
                                         [f for f, ended in edges if ended]))
        rows.append(row)
        print(f"  [{i + 1}/{len(cases)}] {case['id']}  hits={len(texts)}"
              f"  stale={rows[-1]['stale_returned']}@{rows[-1]['stale_rank']}", flush=True)
    stats = arm.stats()
    arm.close()
    failed = sum(1 for r in rows if r.get("error"))
    if failed > len(rows) * 0.1:
        return {"blocked": f"{failed} of {len(rows)} cases errored - "
                           f"first: {next(r['error'] for r in rows if r.get('error'))}",
                "rows": rows, "graphiti": stats}
    return {"rows": rows, **score(rows), "errors": failed, "seconds": round(time.time() - t0, 1),
            "config": f"graphiti-core via FalkorDB, {LLM} + {EMBED_MODEL}, edge hybrid RRF, limit={k}",
            "graphiti": stats}


ARMS = {"nevertwice": run_nevertwice, "mem0": run_mem0, "naive": run_naive, "zep": run_zep}


def compare_arms(loaded: dict[str, dict[str, dict]]) -> list[dict]:
    """Pair every arm against every other on the SAME supersession cases (exact McNemar).

    `loaded` maps an arm name to its rows keyed by case id. Arms are paired in name order, so
    a pointer such as `pairs[1].p_mcnemar` in the evidence register names the same pair on
    every rebuild.
    """
    sys.path.insert(0, str(HERE))
    from uncertainty import mcnemar_exact                       # noqa: PLC0415

    names = sorted(loaded)
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shared = [cid for cid in loaded[a] if cid in loaded[b]
                      and loaded[a][cid]["shape"] != "control"]
            # discordant pairs only: cases where exactly one arm returned a stale assertion
            a_only = sum(1 for cid in shared
                         if loaded[a][cid]["stale_returned"] and not loaded[b][cid]["stale_returned"])
            b_only = sum(1 for cid in shared
                         if loaded[b][cid]["stale_returned"] and not loaded[a][cid]["stale_returned"])
            pairs.append({
                "a": a, "b": b, "n": len(shared),
                f"stale_only_{a}": a_only, f"stale_only_{b}": b_only,
                "discordant": a_only + b_only,
                "p_mcnemar": mcnemar_exact(a_only, b_only),
            })
    return pairs


def compare(files: list[Path]) -> dict:
    """Pair every arm against every other on the SAME cases, and test the difference.

    Two Wilson intervals that fail to overlap are strong evidence, but they are not the test:
    the arms saw identical cases, so the paired one is available and is what the rest of this
    repository uses. Arms run in separate processes - Mem0 lives in its own environment - so
    the comparison reads result files rather than running the arms itself.
    """
    loaded: dict[str, dict[str, dict]] = {}
    datasets = set()
    for f in files:
        blob = json.loads(Path(f).read_text(encoding="utf-8"))
        datasets.add(blob["dataset"]["sha256"])
        for name, res in blob["arms"].items():
            if res.get("blocked"):
                continue
            loaded[name] = {r["id"]: r for r in res["rows"]}
    if len(datasets) > 1:
        return {"error": f"result files come from different datasets: {sorted(datasets)}"}
    return {"dataset_sha256": datasets.pop() if datasets else None, "arms": sorted(loaded),
            "pairs": compare_arms(loaded)}


ENGINE_ARM = "nevertwice"


def _r4(pair) -> list[float]:
    return [round(x, 4) for x in pair]


def pool_other_arm(results: list[dict]) -> dict:
    """One non-engine arm from several result files: rows concatenated and tagged with their run,
    the rates re-scored over the case-runs, per-run stale and current kept beside them, seconds
    summed, the first run's config and Graphiti stats carried with the per-run stats beside them.
    A single file is returned as it is."""
    if len(results) == 1:
        return results[0]
    rows = [dict(r, run=i) for i, res in enumerate(results) for r in res["rows"]]
    out = {"rows": rows, **score(rows), "runs": len(results),
           "per_run_stale": [res.get("stale_rate") for res in results],
           "per_run_current": [res.get("current_rate") for res in results],
           "per_run_control_miss": [res.get("control_miss_rate") for res in results],
           "errors": sum(int(res.get("errors") or 0) for res in results),
           "seconds": round(sum(float(res.get("seconds") or 0) for res in results), 1),
           "config": results[0].get("config", "")}
    if any("graphiti" in res for res in results):
        out["graphiti"] = results[0].get("graphiti")
        out["graphiti_per_run"] = [res.get("graphiti") for res in results]
    return out


def pool(engine_files: list[Path], other_files: list[Path] | None = None) -> dict:
    """Pool several runs of the engine arm into one artifact, the other arms beside them.

    Two runs of the same commit on the same corpus read stale 0.017 and 0.067 (2026-09-02), and
    this docstring blamed the model: "the extraction model is not deterministic at temperature 0".
    It was not at temperature 0. The stand never pinned `NEVERTWICE_EXTRACT_TEMP` and so sampled
    at the engine's live default of 0.2 (fixed 2026-09-22, see `run_nevertwice`). Pinned to 0,
    three runs of one commit on `supersession_v1_implicit` read stale 0.0667 / 0.0667 / 0.0667,
    chars 495.1 / 493.8 / 493.8, and 1 of 80 cases served different text where all 80 had before.

    Runs are still pooled, and `tools/register_supersession.py` now refuses a single one, but for
    the other reason: agreement between two runs is a claim like any other and is worth one extra
    pass to show rather than assume. The published rate is pooled over case-runs with the per-run
    values kept beside it; the paired tests are computed on the first run, where the arms saw
    identical cases, and repeated per run against Mem0. Until 2026-09-06 this lived in a session
    scratchpad, which meant the committed artifact could not be rebuilt by anyone else.

    Every file must come from the same dataset (content hash). Engine files contribute their
    `nevertwice` arm to the pool and any other unblocked arm to the artifact; `other_files`
    contribute their unblocked arms. An arm that appears in two files is an error rather than
    a silent choice.
    """
    runs: list[dict] = []
    engine_stores: list[str | None] = []
    engine_code: list[str | None] = []
    after_runs: list[dict] = []
    other_runs: dict[str, list[dict]] = {}
    meta: dict | None = None
    shas: set[str] = set()
    for f in [Path(p) for p in engine_files]:
        blob = json.loads(f.read_text(encoding="utf-8"))
        shas.add(blob["dataset"]["sha256"])
        arm = blob["arms"].get(ENGINE_ARM)
        if not arm or arm.get("blocked"):
            raise ValueError(f"{f}: no {ENGINE_ARM} arm to pool")
        runs.append(arm)
        engine_stores.append(blob.get("store"))
        engine_code.append(blob.get("code_sha"))
        after = blob["arms"].get(f"{ENGINE_ARM}_after_sleep")     # K8: the second reading, when present
        if after and not after.get("blocked"):
            after_runs.append(after)
        if meta is None:
            meta = blob
        for name, res in blob["arms"].items():
            if not name.startswith(ENGINE_ARM) and not res.get("blocked") and name not in other_runs:
                other_runs[name] = [res]
    for f in [Path(p) for p in (other_files or [])]:
        blob = json.loads(f.read_text(encoding="utf-8"))
        shas.add(blob["dataset"]["sha256"])
        for name, res in blob["arms"].items():
            if name.startswith(ENGINE_ARM) or res.get("blocked"):
                continue
            # Until 2026-09-11 a second file carrying the same arm was refused ("which one?"). It
            # is now what the K2 parity run produces on purpose: the arm's runs are pooled over
            # case-runs with the per-run values kept, exactly as the engine arm is.
            other_runs.setdefault(name, []).append(res)
    if len(shas) != 1:
        raise ValueError(f"result files come from different datasets: {sorted(shas)}")
    #: Two engine runs that wrote into ONE store are not two runs of the same commit: the second
    #: reads what the first wrote and judged, and the engine skips a session it has already
    #: processed. Measured on the explicit corpus with `--sleep` before `--runs` spawned a
    #: process per run: run 1 served 440.0 chars/query and run 2 served 387.5 - which is run 1's
    #: own AFTER-SLEEP figure (2026-09-22). Files written before this field existed carry no
    #: store and are pooled as before; `None` is not evidence of sharing.
    #: Two runs of one commit have to be ONE program. The stand re-executes itself per run, so
    #: an edit landing between them is silent and spoils the comparison the repeat exists for.
    #: The failure this guards is the SUCCESSFUL write; a truncated one fails loudly by itself
    #: (2026-09-22: a comment edited in this file while runs were in flight, judged safe only
    #: afterwards). Files written before the field carry none and pool as before.
    shas = [x for x in engine_code if x]
    if len(set(shas)) > 1:
        raise ValueError(
            "two engine runs came from different source revisions of the stand and package "
            f"({' vs '.join(sorted(set(shas)))}) - that is not a repeat of the same commit")
    stores = [s for s in engine_stores if s]
    if len(stores) != len(set(stores)):
        raise ValueError(
            "two engine runs share one sandbox store, so the later ones read what the earlier "
            f"ones wrote - that is not a repeat of the same commit: {sorted(set(stores))}")
    #: The same failure wearing different store paths: distinct sandboxes and the work still
    #: not done. Counted as the ACTION - sessions this pass actually ingested - because the
    #: obvious state-shaped proxy does not work: in the auditing session's copy of the
    #: one-process bug BOTH runs reported `notes_written` 52, the second having inherited the
    #: first's notes and counted them honestly (2026-09-22). Runs written before this field
    #: exists report `None` and are pooled as before.
    acted = [r.get("sessions_ingested") for r in runs]
    if any(a for a in acted) and any(a == 0 for a in acted):
        offered = [r.get("sessions_ingested", 0) + (r.get("sessions_not_stored") or 0)
                   for r in runs if r.get("sessions_ingested") == 0]
        raise ValueError(
            "an engine run stored nothing while another did - that is not a repeat of the same "
            f"commit: sessions_ingested {acted}; the idle run(s) were offered {offered} "
            "session(s) and accepted none. The cause is not in the artifact: the engine returns "
            "one bit for four endings, and only one of them is 'already processed'.")
    assert meta is not None
    others: dict[str, dict] = {name: pool_other_arm(rs) for name, rs in other_runs.items()}

    pooled = _fold_engine_runs(runs)
    arms: dict[str, dict] = {ENGINE_ARM: runs[0]}
    for i, a in enumerate(runs[1:], start=2):
        arms[f"{ENGINE_ARM}_run{i}"] = a
    pooled_after = None
    if after_runs and len(after_runs) == len(runs):
        pooled_after = _fold_engine_runs(after_runs)
        arms[f"{ENGINE_ARM}_after_sleep"] = after_runs[0]
        for i, a in enumerate(after_runs[1:], start=2):
            arms[f"{ENGINE_ARM}_after_sleep_run{i}"] = a
    arms.update(others)

    # the paired tests are computed on each arm's FIRST run, where the arms saw identical cases
    first = {ENGINE_ARM: {r["id"]: r for r in runs[0]["rows"]}}
    first.update({n: {r["id"]: r for r in rs[0]["rows"]} for n, rs in other_runs.items()})

    per_run_pairs = []
    if "mem0" in other_runs:
        m0 = {r["id"]: r for r in other_runs["mem0"][0]["rows"]}
        for a in runs:
            rows = {r["id"]: r for r in a["rows"]}
            ids = [i for i, r in rows.items() if r["shape"] != "control" and i in m0]
            per_run_pairs.append({
                "nevertwice_only": sum(1 for i in ids
                                       if rows[i]["stale_returned"] and not m0[i]["stale_returned"]),
                "mem0_only": sum(1 for i in ids
                                 if m0[i]["stale_returned"] and not rows[i]["stale_returned"]),
            })

    def sup(a: dict) -> list[dict]:
        return [r for r in a["rows"] if r["shape"] != "control"]

    def ctl(a: dict) -> list[dict]:
        return [r for r in a["rows"] if r["shape"] == "control"]

    n_sup, n_ctl = len(sup(runs[0])), len(ctl(runs[0]))
    ds = dict(meta["dataset"])
    ds.update({"cases": n_sup + n_ctl, "supersession_cases": n_sup, "control_cases": n_ctl})
    per_run_stale = pooled["stale"]["per_run"]
    spread = " and ".join(f"{v:.4f}" for v in per_run_stale)
    note = (f"{len(runs)} runs of the same commit on the same corpus with the same models read "
            f"stale {spread}. The published rate is pooled over case-runs and the per-run values "
            "are kept beside it. Extraction is pinned to temperature 0 (2026-09-22); before that "
            "the stand sampled at the engine's live 0.2 and every case moved between runs, which "
            "is the spread this note used to blame on the model."
            + (" ONE RUN: the agreement between runs is not shown here, and "
               "tools/register_supersession.py refuses this artifact." if len(runs) < 2 else ""))
    out = {"arms": arms, "k": meta["k"], "llm": meta["llm"], "embedder": meta["embedder"],
           "dataset": ds, "pooled_nevertwice": pooled, "pooled_note": note,
           "pairs": compare_arms(first), "pairs_per_engine_run": per_run_pairs}
    if pooled_after is not None:
        # K8: the same fold over the second reading - the store after the sleep-time judge
        pooled_after["adjudication"] = [a.get("adjudication") for a in after_runs]
        out["pooled_nevertwice_after_sleep"] = pooled_after
    return out


def _fold_engine_runs(runs: list[dict]) -> dict:
    """The engine arm's rates pooled over the case-runs of several runs, per-run values beside them
    (the `pooled_nevertwice` block; since K8 also the after-sleep reading's)."""

    def sup(a: dict) -> list[dict]:
        return [r for r in a["rows"] if r["shape"] != "control"]

    def ctl(a: dict) -> list[dict]:
        return [r for r in a["rows"] if r["shape"] == "control"]

    stale_k = sum(1 for a in runs for r in sup(a) if r["stale_returned"])
    old_k = sum(1 for a in runs for r in sup(a) if r.get("old_value_served"))
    cur_k = sum(1 for a in runs for r in sup(a) if r["current_returned"])
    sup_n = sum(len(sup(a)) for a in runs)
    # over-retraction proper only: the memory retired a still-true fact (not "ranked below k",
    # not "never written"), which is what `_store_state` records per control row
    over_k = sum(1 for a in runs for r in ctl(a)
                 if not r["current_returned"] and (r.get("current_retired") or r.get("current_demoted")))
    ctl_n = sum(len(ctl(a)) for a in runs)
    # the broad measure beside it - the still-true fact did not come back, any cause - and the
    # split of that count by cause; the two used to share one name (2026-09-11)
    miss_k = sum(1 for a in runs for r in ctl(a) if not r["current_returned"])
    per_run_miss = [round(sum(1 for r in ctl(a) if not r["current_returned"]) / len(ctl(a)), 4)
                    for a in runs if ctl(a)]
    lost = [r for a in runs for r in ctl(a) if not r["current_returned"]]
    causes = {"retired": sum(1 for r in lost if r.get("current_retired")),
              "demoted": sum(1 for r in lost if r.get("current_demoted")),
              "never_written": sum(1 for r in lost if r.get("current_absent")),
              "unranked": sum(1 for r in lost if r.get("current_live"))}
    per_run_stale = [round(sum(1 for r in sup(a) if r["stale_returned"]) / len(sup(a)), 4)
                     for a in runs]
    per_run_cur = [round(sum(1 for r in sup(a) if r["current_returned"]) / len(sup(a)), 4)
                   for a in runs]
    pooled = {
        "runs": len(runs),
        "stale": {"k": stale_k, "n": sup_n, "rate": round(stale_k / sup_n, 4),
                  "ci": _r4(wilson(stale_k, sup_n)), "per_run": per_run_stale},
        "current": {"k": cur_k, "n": sup_n, "rate": round(cur_k / sup_n, 4),
                    "ci": _r4(wilson(cur_k, sup_n)), "per_run": per_run_cur},
        "old_value_served": {"k": old_k, "n": sup_n, "rate": round(old_k / sup_n, 4),
                             "ci": _r4(wilson(old_k, sup_n)),
                             "per_run": [round(sum(1 for r in sup(a) if r.get("old_value_served")) / len(sup(a)), 4)
                                         for a in runs]},
        "over_retraction": {"k": over_k, "n": ctl_n,
                            "rate": round(over_k / ctl_n, 4) if ctl_n else None,
                            "ci": _r4(wilson(over_k, ctl_n))},
        "control_miss": {"k": miss_k, "n": ctl_n,
                         "rate": round(miss_k / ctl_n, 4) if ctl_n else None,
                         "ci": _r4(wilson(miss_k, ctl_n)), "per_run": per_run_miss},
        "control_causes": causes,
        "mean_chars_returned": round(sum(a["mean_chars_returned"] for a in runs) / len(runs), 1),
    }
    return pooled


def load_dataset(path: Path) -> dict:
    """The corpus, with the hash the artifact is pinned by.

    `--dataset` may be given relative to the repository root, which is how `research/reproduce.py`
    prints every command, or absolute. The recorded path is repository-relative with forward
    slashes so an artifact produced on Windows reads the same everywhere; a corpus outside the
    repository keeps its own path, and the hash is what pins it either way."""
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    data["sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        data["path"] = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        data["path"] = path.as_posix()
    return data


def _print_pooled(res: dict) -> None:
    """The pooled summary, printed the same way whether the pool came from `--pool` or
    from `--runs N`."""
    p = res["pooled_nevertwice"]
    print(f"pooled {p['runs']} engine runs on {res['dataset']['name']}: "
          f"stale {p['stale']['rate']} {p['stale']['ci']} per run {p['stale']['per_run']} | "
          f"current {p['current']['rate']} {p['current']['ci']} | "
          f"control miss {p['control_miss']['rate']} (retired {p['control_causes']['retired']}, "
          f"demoted {p['control_causes'].get('demoted', 0)}, "
          f"never written {p['control_causes']['never_written']}, "
          f"unranked {p['control_causes']['unranked']}) | "
          f"over-retraction {p['over_retraction']['rate']} | "
          f"{p['mean_chars_returned']} chars/query")
    for pr in res["pairs"]:
        print(f"  {pr['a']} vs {pr['b']}: n={pr['n']} discordant {pr['discordant']} "
              f"p={pr['p_mcnemar']:.3g}")
    for i, pr in enumerate(res["pairs_per_engine_run"], start=1):
        print(f"  run {i} vs mem0: nevertwice-only {pr['nevertwice_only']}, "
              f"mem0-only {pr['mem0_only']}")


def _one_run(data: dict, cases: list[dict], args, arm_names: list[str]) -> dict:
    """One pass of the requested arms over the corpus: the shape `--out` writes and
    `--pool` reads. Split out of `main` so `--runs N` can call it N times."""
    out = {"dataset": {k: data[k] for k in ("name", "sha256", "path")},
           "n_cases": len(cases), "k": args.k, "llm": LLM, "embedder": EMBED_MODEL,
           # Which sandbox this pass wrote into. `pool` refuses two engine runs that share one,
           # because a second pass over a store the first already filled is not a repeat - see
           # the `--runs` branch in `main` for the measurement that made that concrete.
           "store": str(sandbox_guard.store()),
           "code_sha": _code_sha(),
           "extract_temperature": os.environ.get("NEVERTWICE_EXTRACT_TEMP"),
           "arms": {}}
    for name in arm_names:
        fn = ARMS.get(name)
        if fn is None:
            print(f"- {name}: unknown arm (have: {', '.join(ARMS)})")
            continue
        print(f"- {name}")
        res = fn(cases, args.k, sleep=args.sleep) if name == "nevertwice" else fn(cases, args.k)
        out["arms"][name] = res
        if res.get("blocked"):
            print(f"  BLOCKED: {res['blocked']}\n")
            continue
        print(f"  stale {res['stale_rate']} {res['stale_ci']} "
              f"(rank-1: {res['stale_at_rank_1']}; old value served {res.get('old_value_served_rate')}) | "
              f"current {res['current_rate']} {res['current_ci']} | "
              f"control miss {res['control_miss_rate']} | "
              f"{res['mean_chars_returned']} chars/query | {res['seconds']}s")
        if res.get("after_sleep"):
            after = res.pop("after_sleep")
            out["arms"]["nevertwice_after_sleep"] = after
            adj = after["adjudication"]
            print(f"  after sleep: stale {after['stale_rate']} (old value served {after.get('old_value_served_rate')}) | "
                  f"current {after['current_rate']} | control miss {after['control_miss_rate']} | "
                  f"over-retraction {after.get('over_retraction_rate')} | {after['mean_chars_returned']} chars/query | "
                  f"judge: {adj['pairs']} pairs, {adj['judged']} calls ({adj['tokens_spent']} of {adj['budget']} tokens"
                  + (f", cap {adj['cap']}" if adj.get("cap") else "") + f"), replaces {adj['replaces']}, "
                  f"separate {adj['separate']}, vetoed {adj.get('vetoed', 0)}, left {adj['left']}, "
                  f"tokens {adj['prompt_tokens']}+{adj['eval_tokens']}")
        if res.get("over_retraction_rate") is not None:
            print(f"    of the controls that came back empty: "
                  f"{res['control_retired_by_memory']} retired by the memory, "
                  f"{res.get('control_demoted_by_merge', 0)} demoted by a merge, "
                  f"{res['control_never_written']} never written, "
                  f"{res['control_written_but_unranked']} written but out of the top {args.k} "
                  f"-> over-retraction proper {res['over_retraction_rate']}")
        else:
            print("    the store was not read for this arm: over-retraction proper is not a number here")
        if res.get("notes_written"):
            print(f"    extraction wrote {res['notes_written']} notes, "
                  f"{res['notes_in_cyrillic']} of them in Cyrillic on an all-English corpus "
                  f"(drift {res['language_drift_rate']})")
        print()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe", action="store_true",
                    help="run the five-fact Mem0 probe instead of the committed dataset")
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--arms", default="nevertwice,naive")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0, help="first N cases only (a smoke run)")
    ap.add_argument("--runs", type=int, default=1, metavar="N",
                    help="repeat the engine arm N times and pool them into --out, writing each "
                         "run beside it as <out>.runN.json; the other arms run once. One command "
                         "instead of N runs plus a --pool, which is why every registered claim "
                         "from this stand carried `pooled over 1 runs` until 2026-09-22")
    ap.add_argument("--sleep", action="store_true",
                    help="K8: read the engine arm twice - after session two, and again after the "
                         "sleep-time judge over the contested pairs (arm `nevertwice_after_sleep`)")
    ap.add_argument("--out", default="")
    ap.add_argument("--compare", nargs="*", default=None,
                    help="result files to pair against each other instead of running an arm")
    ap.add_argument("--pool", nargs="+", default=None, metavar="RUN",
                    help="engine result files to pool into one artifact (each must carry the "
                         "nevertwice arm); the other arms in them are carried along")
    ap.add_argument("--with", dest="others", nargs="*", default=[], metavar="FILE",
                    help="result files whose other arms (mem0, naive) go into the pooled artifact")
    args = ap.parse_args()

    if args.pool:
        try:
            res = pool([Path(f) for f in args.pool], [Path(f) for f in args.others])
        except (ValueError, OSError, KeyError) as e:
            print(f"pool: {e}")
            return 2
        _print_pooled(res)
        if args.out:
            Path(args.out).write_text(json.dumps(res, indent=1, ensure_ascii=False),
                                      encoding="utf-8", newline="\n")
            print("wrote", args.out)
        return 0

    if args.compare:
        res = compare([Path(f) for f in args.compare])
        print(json.dumps(res, indent=1))
        if args.out:
            Path(args.out).write_text(json.dumps(res, indent=1), encoding="utf-8", newline="\n")
            print("wrote", args.out)
        return 0

    if args.probe:
        data = dict(PROBE, sha256="n/a (inline probe)", path="research/supersession_bench.py")
    else:
        p = Path(args.dataset)
        if not p.exists():
            print(f"dataset not found: {p}\nbuild it: python research/gen_supersession_dataset.py")
            return 2
        data = load_dataset(p)

    cases = data["cases"][:args.limit] if args.limit else data["cases"]
    print(f"dataset {data['name']}  n={len(cases)}  sha256={data['sha256'][:16]}")
    print(f"store   {sandbox_guard.store()}\n")

    arm_names = [a.strip() for a in args.arms.split(",") if a.strip()]

    if args.runs < 1:
        print(f"--runs {args.runs}: a run count below 1 measures nothing")
        return 2

    if args.runs > 1:
        # A SUBPROCESS per run, not a loop in this one. `sandbox_guard.isolate` makes one store
        # per PROCESS, at import, and the engine skips a session it has already processed - so an
        # in-process second pass reads a store the first pass has already written and judged, and
        # is not a repeat of the same commit at all. Measured before this was fixed, explicit
        # corpus with `--sleep`: run 1 served 440.0 chars/query and run 2 served 387.5, which is
        # exactly run 1's AFTER-SLEEP figure - the second run had inherited the first run's
        # adjudicated store (2026-09-22). `--pool a.json b.json` never had this problem because
        # its two files come from two invocations, and that is what this flag has to reproduce.
        #
        # The engine arm repeats; the other arms run once, in run 1. Only the engine arm goes
        # through the extractor, and only the extractor moves between runs of one commit. It also
        # removes an ambiguity in `pool`, which keeps the FIRST file's other arms and silently
        # drops the rest: with one run of each there is nothing to drop.
        if not args.out:
            print("--runs N needs --out: the per-run artifacts are written beside the pooled one")
            return 2
        stem = Path(args.out)
        stem.parent.mkdir(parents=True, exist_ok=True)
        base = [sys.executable, str(Path(__file__).resolve()),
                "--dataset", args.dataset, "--k", str(args.k)]
        if args.limit:
            base += ["--limit", str(args.limit)]
        if args.sleep:
            base += ["--sleep"]
        if args.probe:
            base += ["--probe"]
        paths = []
        for i in range(1, args.runs + 1):
            names = arm_names if i == 1 else [a for a in arm_names if a == ENGINE_ARM]
            q = stem.with_name(f"{stem.stem}.run{i}{stem.suffix or '.json'}")
            print(f"=== run {i} of {args.runs} ({', '.join(names)}), fresh process ===",
                  flush=True)
            rc = subprocess.run(base + ["--arms", ",".join(names), "--out", str(q)],
                                cwd=str(Path(__file__).resolve().parent.parent)).returncode
            if rc != 0 or not q.exists():
                print(f"run {i} failed (exit {rc}); nothing pooled")
                return 2
            paths.append(q)
        try:
            res = pool(paths)
        except (ValueError, OSError, KeyError) as e:
            print(f"pool: {e}")
            return 2
        _print_pooled(res)
        Path(args.out).write_text(json.dumps(res, indent=1, ensure_ascii=False),
                                  encoding="utf-8", newline="\n")
        print("wrote", args.out)
        return 0

    out = _one_run(data, cases, args, arm_names)

    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
