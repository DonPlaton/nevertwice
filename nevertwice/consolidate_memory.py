#!/usr/bin/env python3
"""Sleep-time memory consolidation (audit F38/F42, research: offline
consolidation). Run weekly from Task Scheduler.

Does three things:
  1. Embeds any not-yet-cached notes (keeps the retrieval index complete).
  2. Finds near-duplicate typed notes within a (project, ntype) and merges
     each cluster: keeps the newest, moves the rest to <folder>/Archive/
     (still reachable by Obsidian), and stamps `recurrence: N` on the keeper
     so recurring lessons outrank one-offs.
  3. Compacts oversized Context files and archives aged notes/sessions.

Safe by default - prints a plan and changes NOTHING. Pass --apply to execute.

    python consolidate_memory.py            # dry-run
    python consolidate_memory.py --apply
"""
import hashlib
import heapq
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from . import memory_hook as m
except ImportError:                 # run as a script, not as a package
    import memory_hook as m

try:                                      # never crash printing → / Cyrillic on a cp1251 console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 0.86, calibrated on a live 4.2k-vector store (2026-08-18): random DISTINCT same-
# project/type pairs top out at cosine 0.737, real same-lesson re-phrasings sit at
# 0.78-0.90. The old 0.92 default was measured NEAR-INERT - it caught only near-verbatim
# copies, which is how a store accumulated 141 exact-slug twin pairs under a weekly
# consolidator. Merging is heavier than the write-time gate's retire (0.80), so this
# stays a notch more conservative.
SIM_THRESHOLD = m.env_float("NEVERTWICE_DEDUP_SIM", 0.86)   # safe-cast: a mistyped env var
# Per-project live-note cap (improvement P2). 0 = OFF - a memory store must not shed
# memory without being told to. When >0, the lowest-salience excess is archived.
MAX_LIVE_PER_PROJECT = m.env_int("NEVERTWICE_MAX_LIVE_PER_PROJECT", 0)  # degrades, never crashes
# K8 layer 3: the judge's budget per consolidation run over the `contested` same-slug pairs the write
# path kept apart - in tokens (prompt + answer as the backend reports them - Ollama's
# prompt_eval_count/eval_count, Gemini's usageMetadata, an OpenAI-compatible usage block; a call
# that reports none is charged the measured mean), not in calls. 100k tokens is ~240 pairs at
# the 415 measured on the K7
# store (research/results/k8_judge_eval.json) - three times the 73 pairs a week the owner's vault
# produces (k8_vault_dryrun.json), so a weekly run empties the queue. A cap of 50 calls (the first
# fast cycles, ledger K8) left 15-19 pairs a stand run and would have left ~23 a week on the vault
# never judged under "newest first" - amended before the campaign (naryad K8-B, 2026-09-17). 0 switches
# the judge off. The number of calls is printed; `NEVERTWICE_CONTESTED_CAP` > 0 adds a hard cap on
# calls for a stand that wants one.
CONTESTED_BUDGET = m.env_int("NEVERTWICE_CONTESTED_BUDGET", 100_000)
CONTESTED_CAP = m.env_int("NEVERTWICE_CONTESTED_CAP", 0)
#: What one pair is charged when the backend reports no token counts at all: the K7 store's mean,
#: prompt 375 + answer 40.
#:
#: "A cloud backend" used to be on that list, and that was the defect rather than the design.
#: Gemini returns `usageMetadata` and every OpenAI-compatible provider returns `usage`, both in
#: the body the extractor already parses, and neither was read - so on cloud EVERY verdict fell
#: through to this constant and a run reporting "38,180 of 100,000 tokens" was reporting 92 x 415.
#: `_record_usage` reads both shapes now, and `estimated_calls` (printed below, and equal to
#: `judged` on every cloud run before this) is what says whether a given run leaned on the
#: estimate at all. The fallback stays for a provider that genuinely reports nothing: a gateway
#: that strips the field, a stub, an older API version.
TOKENS_PER_PAIR_EST = 415
# F6 (xhigh review): a wall-clock ceiling on the judge step itself, independent of the token
# budget above - Ollama hanging on /api/generate can cost 364s a pair (retries x timeout), and
# the step held the vault lock the whole time with no refresh_lock(), so past LOCK_STALE_S*10 a
# concurrent SessionEnd hook stole the lock and wrote at the same time. 900s (15 min) comfortably
# covers the ~73-pair weekly inflow even at several seconds a call; 0 disables the ceiling.
CONTESTED_SECONDS = m.env_int("NEVERTWICE_CONTESTED_SECONDS", 900)
#: consecutive None/timeout verdicts that stop the step early (F6): a hung backend answering
#: nothing must not spend the whole run's budget retrying the SAME oldest pair forever.
CONTESTED_FAIL_LIMIT = 3


def _pair_fields(p: Path) -> tuple[dict, str, str]:
    """(frontmatter, title, description) of one typed note - the judge's two sides."""
    text = p.read_text(encoding="utf-8", errors="replace")
    fm, body = m._read_frontmatter(text)
    title, desc, _ = m._parse_note_body(body.split("\n"))
    return fm, (title or p.stem), (desc or "")


#: What each dispute was about, on the earlier note: `{newer stem: "<hash old>:<hash new>"}`.
DISPUTED_AT_KEY = "disputed_at"


def _live_or_archived(folder: Path, stem: str) -> Path | None:
    """The note `stem` where the judge can still read it: live, or aged into Archive/.
    Superseded/ is gone - a retired note is no side of a pair (R12: one copy of the lookup)."""
    return next((q for q in (folder / f"{stem}.md", folder / "Archive" / f"{stem}.md")
                 if q.exists()), None)


def _dispute_hash(old_path: Path, new_path: Path) -> str:
    """Both statements the guard judged - title and description - hashed. A dispute is about
    those words; when either changes, the answer the guard gave may no longer be the answer."""
    def h(title: str, desc: str) -> str:
        return hashlib.sha256(f"{title}\n{desc}".encode("utf-8")).hexdigest()[:16]
    _, t_old, d_old = _pair_fields(old_path)
    _, t_new, d_new = _pair_fields(new_path)
    return f"{h(t_old, d_old)}:{h(t_new, d_new)}"


def _set_contested(p: Path, stems: list[str], disputed: str | None = None,
                   dispute_hash: str | None = None) -> None:
    text = p.read_text(encoding="utf-8", errors="replace")
    fields = {m.CONTESTED_KEY: stems}
    if disputed:
        fm, _ = m._read_frontmatter(text)
        cur = m._contested_of({m.CONTESTED_KEY: fm.get(m.DISPUTED_KEY)})
        fields[m.DISPUTED_KEY] = cur + ([disputed] if disputed not in cur else [])
        if dispute_hash:
            seen = fm.get(DISPUTED_AT_KEY)
            fields[DISPUTED_AT_KEY] = {**(seen if isinstance(seen, dict) else {}),
                                       disputed: dispute_hash}
    m.write_atomic(p, m._stamp_frontmatter(text, fields))


def _requeue_changed_disputes(apply: bool,
                              disputed_rows: list[dict]) -> tuple[list[tuple[dict, str, Path]], int]:
    """Put a disputed pair back on the judge's queue when either statement changed since the
    dispute. Returns ([(row, new_stem, new_path), ...] re-queued, baselined).

    The rows come from the caller's walk (review 2026-09-23, #13: one consolidation header-read
    every typed note four times); the pairs come back so a dry run can plan them (#11) - apply
    writes the stamp, and the caller's queue was read before it. A note that cannot be read
    right now (#1: Obsidian, AV or OneDrive holding it) is skipped for this run, as F10 skips a
    pair whose write fails, instead of ending the run before the judge's own guard is reached.

    F14's second half, recorded in K8 as a follow-up and never built. A pair the judge said
    `replaces` and the guard vetoed leaves the queue as `disputed`, so the judge is not paid
    again for the same answer on the same text. But nothing ever brought one back: a note
    corrected later - the value the session really said, now in its facts - stayed disputed for
    good, and off limits to the near-duplicate merge for good.

    A dispute stamped before `disputed_at` existed has no hash. It is baselined, not re-judged:
    nothing says its text changed since the dispute, and re-judging every old dispute would pay
    the judge for answers it already gave. From the baseline on, a change counts.
    """
    baselined = 0
    queued: list[tuple[dict, str, Path]] = []
    for c in disputed_rows:
        old_path = Path(c["path"])
        folder = m.VAULT / m.TYPE_FOLDER[c["ntype"]]
        try:
            text = old_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, _ = m._read_frontmatter(text)
        seen = fm.get(DISPUTED_AT_KEY)
        seen = dict(seen) if isinstance(seen, dict) else {}
        back, baseline = [], False
        for ns in c["new_stems"]:
            new_path = _live_or_archived(folder, ns)
            if new_path is None:
                continue                  # the newer note is gone: the record stays for a human
            try:
                now = _dispute_hash(old_path, new_path)
            except OSError as e:
                print(f"      dispute check skipped for {old_path.name} | {ns} ({e}) - next run",
                      file=sys.stderr)
                continue
            if ns not in seen:
                seen[ns] = now
                baseline = True
                baselined += 1
            elif seen[ns] != now:
                back.append(ns)
                seen.pop(ns, None)
                queued.append((c, ns, new_path))
        if apply and (back or baseline):
            fields = {m.DISPUTED_KEY: [s for s in c["new_stems"] if s not in back],
                      DISPUTED_AT_KEY: seen}
            if back:
                contested = m._contested_of(fm)
                fields[m.CONTESTED_KEY] = contested + [s for s in back if s not in contested]
            try:
                m.write_atomic(old_path, m._stamp_frontmatter(text, fields))
            except OSError as e:
                print(f"      dispute re-queue failed for {old_path.name} ({e}) - left as is",
                      file=sys.stderr)
                queued = [q for q in queued if q[0] is not c]
    return queued, baselined


def _replacement_guard(old_desc: str, new_desc: str) -> str:
    """Delegates to `memory_hook._replacement_guard` (xhigh review F3): the guard moved there so
    the write-time rules 2/2' of `_same_replacement` apply the identical check, not just the
    sleep-time judge here - a changed or hallucinated value used to replace the true note at write
    time because this guard used to live only in this module. Kept as a local name (like `_int1`)
    so this module's own call site and tests need no change."""
    return m._replacement_guard(old_desc, new_desc)


def _drop_retired_vectors(cache: dict, dry: bool = False) -> int:
    """Pop every vector whose note is retired and live nowhere; return how many.

    A killed consolidation runs no `except` and no `finally`: its notes are already in
    Superseded/ and their vectors are still in the cache file, because the cache is written once
    at the end of the run (see `adjudicate_contested`). Recall never serves such a note - every
    delivered hit is stat-checked by `_live_note_exists` - but the vector sits in the cache until a
    full rebuild. The next run clears it here. The rule is the delivery rule, not "has a copy in
    Superseded/": a basename can exist live and retired at once (the 2026-09 review found five),
    and the live one keeps its vector.
    """
    dropped = 0
    for ntype, folder in m.TYPE_FOLDER.items():
        sup = m.VAULT / folder / "Superseded"
        if not sup.is_dir():
            continue
        for q in sup.glob("*.md"):
            if q.stem in cache and not m._live_note_exists(q.stem, ntype):
                if not dry:
                    cache.pop(q.stem, None)
                dropped += 1
    return dropped


def _carry_into(new_path: Path, text: str, rec: int, sources: set[str],
                supersedes: list[str]) -> int:
    """Merge a retiring note's history into the note that replaces it; return the recurrence.

    Recurrence by max, sources and supersedes by union, so applying the same carry twice changes
    nothing. That is what lets the carry go FIRST, before the retirement (review 2026-09-23, R10):
    it used to follow `supersede_note`, and a failure there came after the old note had already
    left - reported "left as is" when nothing was (#4), and a pending-carry ledger to patch it
    brought three failure modes of its own. Now a failed carry changes nothing, and a failed
    retirement is undone by the caller writing `text` back. `text` is the note as read just
    before, so the undo restores exactly what was there."""
    fm, _ = m._read_frontmatter(text)
    #: sorted, then the newest CAP kept - the order the carry always wrote
    src = sorted(set(m._list_field(fm.get("sources"))) | set(map(str, sources)))
    have_sup = m._list_field(fm.get("supersedes"))
    sup = have_sup + [s for s in supersedes if s not in have_sup]
    rec = max(m._coerce_recurrence(fm.get("recurrence")), int(rec), len(set(src)))
    m.write_atomic(new_path, m._stamp_frontmatter(
        text, {"recurrence": rec, "sources": src[-m.RECUR_SOURCES_CAP:], "supersedes": sup}))
    return rec


def adjudicate_contested(apply: bool, has_llm: bool, cap: int | None = None,
                         judge=None, cache: dict | None = None, budget: int | None = None,
                         seconds: float | None = None) -> dict:
    """K8 layer 3 - the same-fact judge over the `contested` pairs, outside any session.

    The write path keeps a same-slug note from another session as a live sibling unless the
    replacement is proven, and stamps the earlier note `contested: [<new stems>]` (layer 1). Here,
    the OLDEST contested pair first and within a token `budget` a run (`CONTESTED_BUDGET`; a call
    whose backend reports no token counts is charged `TOKENS_PER_PAIR_EST`), each pair goes to the
    judge (`memory_hook._same_fact_verdict`, K7's prompt): `replaces` - the earlier note is retired
    with `valid_to` and `superseded_via: judge`, its recurrence and sources carried into the note that
    replaced it; `separate` - the stamp is cleared and both stay; no answer - the pair stays
    contested. A pair whose newer note is gone (retired, archived or deleted) is dropped from the
    stamp. Without a backend nothing is judged and the pairs stay visible in `conflicts()`. Dry-run
    calls the judge and prints the plan; `apply` writes. `cap` > 0 is a hard cap on calls besides the
    budget (stands). Returns the counts, the calls, and the tokens spent against the budget.

    Oldest first, one queue: with the budget above the inflow the queue empties every run, and when
    it does not, no pair waits more than one run - "newest first" starved the tail forever (15-19
    pairs a stand run, ~23 a week on the owner's vault); two queues with a quota only pay when the
    budget is below the inflow, which at three times the measured inflow it is not."""
    cap = CONTESTED_CAP if cap is None else cap
    budget = CONTESTED_BUDGET if budget is None else budget
    seconds = CONTESTED_SECONDS if seconds is None else seconds
    judge = judge or m._same_fact_verdict
    contested_rows, disputed_rows = m._iter_contested_both(None)      # the run's one walk (#13)
    requeued_pairs, baselined = _requeue_changed_disputes(apply, disputed_rows)
    pairs: list[tuple] = []
    seen_pairs: set[tuple[str, str]] = set()
    back_by_note: dict[str, list[str]] = {}

    def _queue(c: dict, old_path: Path, new_path: Path, ns: str) -> None:
        #: once a pair (R2): a stem in both `contested` and `disputed` on one note - a dispute
        #: re-stamped contested by a later write - was built twice from the two lists and judged
        #: and charged twice. The fresh walk after the re-queue write used to fold them.
        if (str(old_path), ns) not in seen_pairs:
            seen_pairs.add((str(old_path), ns))
            pairs.append((c, old_path, new_path, ns))

    for c, ns, new_path in requeued_pairs:
        # read before the re-queue stamp was written, so the walk above does not carry them;
        # a dry run never writes the stamp, and still plans what apply would judge (#11)
        _queue(c, Path(c["path"]), new_path, ns)
        back_by_note.setdefault(c["path"], []).append(ns)
    for c in contested_rows:
        old_path = Path(c["path"])
        folder = m.VAULT / m.TYPE_FOLDER[c["ntype"]]
        live_new = []
        for ns in c["new_stems"]:
            # the newer note is live, or has itself aged into Archive/ (an importer of old transcripts
            # archives on the way in - the as-of stand's dating does exactly that); Superseded/ is gone
            new_path = _live_or_archived(folder, ns)
            if new_path is not None:
                _queue(c, old_path, new_path, ns)
                live_new.append(ns)
        if apply and len(live_new) != len(c["new_stems"]):
            #: the stamp is rewritten from a row read BEFORE the re-queue above stamped its stems
            #: into it: carry them, or the cleanup erases a pair from both lists at once (R1)
            keep = live_new + [s for s in back_by_note.get(c["path"], []) if s not in live_new]
            try:
                _set_contested(old_path, keep)      # the newer note is gone: nothing left to judge
            except OSError as e:
                print(f"      stale stamp cleanup failed for {old_path.name} ({e}) - left as is",
                      file=sys.stderr)
    pairs.sort(key=lambda t: t[3])                     # date-prefixed stems: the OLDEST pair first
    stats = {"pairs": len(pairs), "budget": budget, "cap": cap, "judged": 0, "tokens_spent": 0,
             "estimated_calls": 0, "replaces": 0, "separate": 0, "vetoed": 0, "unanswered": 0,
             "errors": 0, "left": len(pairs), "prompt_tokens": 0, "eval_tokens": 0, "skipped": None}
    stats["requeued"], stats["baselined"] = len(requeued_pairs), baselined
    #: One vector cache for the whole run, written once at the end. `supersede_note` used to load
    #: it, pop one stem and write the whole file back for EVERY pair it retired - N full rewrites
    #: of a cache the consolidator (below, `consolidate`) already holds and writes itself. Given a
    #: cache, `supersede_note` only pops; so a caller that passes one writes it, and a standalone
    #: run loads its own here and writes it once after the loop.
    #:
    #: The ghosts a killed run left are cleared BEFORE any early exit, because healing has no reason
    #: to wait for a judge. It first sat after the three exits below, and the auditing session
    #: measured what that meant: healed with pairs and a backend, left in place with nothing to
    #: judge, with no backend, and with a zero budget. "Nothing to judge" is the likeliest state
    #: after a kill - a process killed after its last retirement leaves an empty queue - and a quiet
    #: week or a down Ollama are the next two. `healed` is in the report on every path, 0 included.
    own_cache = apply and cache is None
    if own_cache:
        cache = m.load_embed_cache()
    if apply:
        healed = _drop_retired_vectors(cache) if cache is not None else 0
    else:
        #: a dry run counts what apply would heal (#11) on the cache its caller holds, and pops
        #: nothing. Standalone - no cache handed in - it does not load one just to count: on the
        #: owner's store that is a 107 MB parse to print one number (R13); `healed` is then None.
        healed = _drop_retired_vectors(cache, dry=True) if cache else (0 if cache is not None else None)
    stats["healed"] = healed
    retired = 0

    def _early(reason: str | None = None) -> dict:
        if reason:
            stats["skipped"] = reason
        if own_cache and healed:
            m.save_embed_cache(cache)      # what was healed is written even when nothing is judged
        return stats

    if not pairs:
        return _early()
    if not has_llm:
        return _early("no LLM backend - the pairs stay contested and visible in conflicts()")
    if budget <= 0:
        return _early("judge budget 0 - the pairs stay contested and visible in conflicts()")
    p0 = m._LLM_STATS.get("prompt_tokens", 0)
    e0 = m._LLM_STATS.get("eval_tokens", 0)
    spent = 0
    deadline = (time.monotonic() + seconds) if seconds > 0 else None
    consecutive_none = 0
    try:
        for c, old_path, new_path, new_stem in pairs:
            if spent >= budget or (cap and stats["judged"] >= cap):
                break                                      # the rest stays contested, served and visible
            if deadline is not None and time.monotonic() >= deadline:
                # F6 (xhigh review): a wall-clock ceiling independent of the token budget - Ollama
                # hanging on /api/generate can cost minutes a pair (retries x timeout), and this step
                # held the vault lock the whole time.
                stats["skipped"] = (f"wall-clock budget ({seconds:.0f}s) reached - "
                                     "the rest stays contested and visible in conflicts()")
                break
            try:
                fm_old, old_title, old_desc = _pair_fields(old_path)
                fm_new, _, new_desc = _pair_fields(new_path)
            except OSError as e:
                print(f"      contested pair unreadable ({e}) - left as is", file=sys.stderr)
                continue
            tp, te = m._LLM_STATS.get("prompt_tokens", 0), m._LLM_STATS.get("eval_tokens", 0)
            c0, o0 = m._LLM_STATS.get("cloud", 0), m._LLM_STATS.get("ollama", 0)
            verdict = judge(old_title, old_desc, new_desc, c["project"])
            # F6: refresh the lock after EVERY call, not just once a run - a long step (Ollama
            # fallback on a big backlog) never touched the mtime before, so past LOCK_STALE_S*10 a
            # concurrent SessionEnd hook stole the lock and wrote to the vault at the same time.
            m.refresh_lock()
            if verdict is None:
                # K8-B: a None verdict is NOT counted `judged` or charged against the budget - the
                # old code charged it the same as a real answer, which fed the SAME timing-out pair
                # back to the head of the oldest-first queue every run, burning the whole budget on
                # repeats instead of ever reaching the rest.
                stats["unanswered"] += 1
                consecutive_none += 1
                print(f"      unanswered: {c['stem']} ? {new_stem}")
                if consecutive_none >= CONTESTED_FAIL_LIMIT:
                    stats["skipped"] = (f"{CONTESTED_FAIL_LIMIT} consecutive unanswered verdicts - "
                                         "stopping the step; the rest stays contested")
                    break
                continue
            consecutive_none = 0
            stats["judged"] += 1
            backend = "cloud" if m._LLM_STATS.get("cloud", 0) > c0 else (
                "ollama" if m._LLM_STATS.get("ollama", 0) > o0 else "unknown")
            stats[f"judged_{backend}"] = stats.get(f"judged_{backend}", 0) + 1
            used = (m._LLM_STATS.get("prompt_tokens", 0) - tp) + (m._LLM_STATS.get("eval_tokens", 0) - te)
            if used <= 0:
                used = TOKENS_PER_PAIR_EST                 # the backend did not say: charge the measured mean
                stats["estimated_calls"] += 1
            spent += used
            remaining = [s_ for s_ in m._contested_of(fm_old) if s_ != new_stem]
            veto = _replacement_guard(old_desc, new_desc) if verdict is True else ""
            try:
                if veto:
                    # the verdict says replace, the proof is not there: both stay, off the judge's
                    # queue, and the pair is stamped `disputed` so conflicts() still shows it to a human
                    stats["vetoed"] += 1
                    print(f"      vetoed ({veto}): {c['stem']} | {new_stem} - both stay, disputed")
                    if apply:
                        _set_contested(old_path, remaining, disputed=new_stem,
                                       dispute_hash=_dispute_hash(old_path, new_path))
                elif verdict is True:
                    stats["replaces"] += 1
                    print(f"      replaces: {c['stem']} -> {new_stem}")
                    if apply:
                        # the retired statement's history carries into the one that replaced it, as
                        # the write-time absorb used to carry it (recurrence = distinct sessions)
                        r_old, s_old = m._note_recur_sources(old_path)
                        #: both sides read through the one list reader: `_note_recur_sources` drops a
                        #: string-valued `sources` (a hand-written flow list) and falls back to the
                        #: session, so the retiring note's own history was lost from the carry (R6)
                        s_old = set(s_old) | set(m._list_field(fm_old.get("sources")))
                        new_sources = m._list_field(fm_new.get("sources"))           # #3: never per character
                        sources = set(s_old) | set(new_sources)
                        for sess in (fm_old.get("session"), fm_new.get("session")):
                            if sess:
                                sources.add(str(sess))
                        # also-fix (xhigh review): the same "a known session adds nothing" gate the
                        # write-time absorb applies (memory_hook.py, `grew = session_stem_ not in
                        # prior_sources`) - unconditionally adding 1 to r_old let one session's note
                        # replacing another already-known-session note yield recurrence 2 for what is
                        # still ONE distinct source. Anonymous notes (no session on either side, no
                        # prior sources - session_stem_ was never passed, same as write-time's own
                        # `if session_stem_ is None` branch) carry no identity to check "already
                        # known" against, so they keep the unconditional +1 exactly as before.
                        if fm_old.get("session") or fm_new.get("session") or s_old or new_sources:
                            grew = len(sources) > len(s_old)
                        else:
                            grew = True
                        rec = max(m._coerce_recurrence(fm_new.get("recurrence")), r_old + (1 if grew else 0),
                                  len(sources))
                        sup_list = m._list_field(fm_new.get("supersedes"))
                        if c["stem"] not in sup_list:
                            sup_list.append(c["stem"])
                        # The carry goes FIRST (R10): an idempotent merge, so an OSError here changes
                        # nothing and the pair really is left as is (F10 below). Then the retirement -
                        # F5 still holds: the contested clear rides in the retirement's own atomic
                        # stamp, never a separate write before it. If the retirement fails, the carry
                        # is written back out, and the pair stays contested for the next run.
                        before = new_path.read_text(encoding="utf-8", errors="replace")
                        rec = _carry_into(new_path, before, rec, sources, sup_list)
                        if not m.supersede_note(old_path, new_stem, via="judge",
                                                extra_fields={m.CONTESTED_KEY: remaining},
                                                cache=cache):
                            #: the pair is still contested on disk: an error, so `left` counts it -
                            #: "nothing left" over a pair still queued is #4 read the other way
                            #: (auditing session's case B on 5961f38, a real WinError 32 unlink)
                            stats["errors"] += 1
                            try:
                                m.write_atomic(new_path, before)
                                print(f"      supersede failed for {old_path.name} - left live, still "
                                      "contested; the carry was undone", file=sys.stderr)
                            except OSError as e:
                                print(f"      supersede failed for {old_path.name} and the carry into "
                                      f"{new_path.name} could not be undone ({e}) - the pair stays "
                                      "contested, and a replaces next run re-applies the same merge",
                                      file=sys.stderr)
                        else:
                            # the retired stem already left the cache: supersede_note pops it from
                            # the cache it is handed (the F13 pop that stood here was dead, #15)
                            retired += 1
                            if cache is not None and isinstance(cache.get(new_stem), dict):
                                cache[new_stem]["recurrence"] = rec
                elif verdict is False:
                    stats["separate"] += 1
                    print(f"      separate: {c['stem']} | {new_stem}")
                    if apply:
                        _set_contested(old_path, remaining)
                # verdict is None was already handled above (unanswered, uncounted, uncharged)
                # and never reaches here.
            except OSError as e:
                # F10 (xhigh review): one PermissionError (Windows: Obsidian/AV/OneDrive holding a
                # note open) used to abort the WHOLE weekly run mid-queue - the same class fixed once
                # before for archival (cap_project_notes, P4). This pair is left exactly as
                # _iter_contested found it (the judge call already happened and is already counted/
                # charged above; only the write that would have acted on its verdict failed) and the
                # run continues with the next pair and its later steps.
                stats["errors"] += 1
                print(f"      pair failed ({e}): {c['stem']} | {new_stem} - left as is", file=sys.stderr)
    except BaseException:
        #: Anything but a kill passes through here - an exception, Ctrl+C. The notes this run
        #: retired are already in Superseded/, so their vectors leave the cache file now, whoever
        #: owns the cache: a caller that handed one in never reaches its own save when this raises.
        #: Before the cache was written once a run, each retirement wrote at once and this window
        #: was one pair; now it is the queue, so the write has to follow the queue out.
        if (retired or healed) and cache is not None:
            m.save_embed_cache(cache)
        raise

    # F10: a pair that errored still bumped its verdict's counter before the write that
    # implements it failed (the judged/charged accounting above is verdict-level, same as
    # `judged` itself) - add errors back so "left" reflects what is ACTUALLY still contested
    # on disk, not what the verdict alone would have resolved.
    stats["left"] = len(pairs) - stats["replaces"] - stats["separate"] - stats["vetoed"] + stats["errors"]
    stats["tokens_spent"] = spent
    stats["prompt_tokens"] = m._LLM_STATS.get("prompt_tokens", 0) - p0
    stats["eval_tokens"] = m._LLM_STATS.get("eval_tokens", 0) - e0
    if own_cache and (retired or healed):
        m.save_embed_cache(cache)                      # the run's one write (see own_cache above)
    return stats


def _int1(x) -> int:
    """recurrence as a positive int. Delegates to m._coerce_recurrence - THE one parse
    rule (review 2026-08 R2): the local `int(x or 1)` variant mapped '2.7'→1 where the
    shared rule rounds to 3, let negatives through into sort keys/thresholds, and did
    not catch OverflowError on inf - so the keeper's merged count disagreed with what
    retrieval used for the same notes."""
    return m._coerce_recurrence(x)


def set_recurrence(p: Path, n: int):
    # one frontmatter-stamp implementation, shared with supersession (launch-round dedup):
    # _stamp_frontmatter does exact-key replace-or-append and leaves the body untouched.
    text = p.read_text(encoding="utf-8", errors="replace")
    new = m._stamp_frontmatter(text, {"recurrence": n})
    if new != text:
        m.write_atomic(p, new)


def date_of(stem: str) -> str:
    parsed = m.parse_typed_stem(stem)
    return parsed["date"] if parsed else stem[:10]


def _fields(p: Path) -> tuple[str, str]:
    """(description, prevention) from a typed note body - the shared parser (m._parse_note_body)
    so it can't drift from the other note-body readers."""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return "", ""
    _, desc, prevention = m._parse_note_body(lines)
    return desc, prevention


def _cluster_recurrence(cache: dict, cluster: list) -> int:
    """The recurrence a merged keeper inherits: the cluster size OR the HIGHEST recurrence
    of any member (a merged dup may have recurred more than the newest keeper), whichever is
    larger - so a near-duplicate merge never drops the recall-boosting count (W15)."""
    recs = [_int1((cache.get(s) or {}).get("recurrence", 1)) for s in cluster]
    return max(len(cluster), max(recs) if recs else 1)


def _archive_target(arch: Path, name: str) -> Path:
    """Collision-safe destination inside Archive/: a bare os.replace(src, arch/name)
    silently OVERWROTE an already-archived note with the same stem (a re-mined old
    session recreates old stems) - permanent loss of the earlier copy's merged
    fragments and duplicate_of provenance (review 2026-08). Suffix -2, -3, ..."""
    t = arch / name
    if not t.exists():
        return t
    base, ext = (name.rsplit(".", 1) + [""])[:2]
    for i in range(2, 100):
        t = arch / f"{base}-{i}.{ext}" if ext else arch / f"{base}-{i}"
        if not t.exists():
            return t
    return arch / name                     # 100 same-stem copies: give up, overwrite


def merge_into_keeper(keep_fp: Path, dup_fps: list[Path]) -> list[str]:
    """Fold any unique description/prevention from the duplicates INTO the keeper
    before they are archived - so 'merge' no longer silently drops a better
    older wording (audit H3). Idempotent: fragments already present are skipped.
    Returns the fragments actually added (callers use it to refresh the keeper's
    cache/index text - review 2026-08: merged-in content was unsearchable)."""
    try:
        ktext = keep_fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # Compare by normalized whole-line equality, NOT substring containment (audit
    # M-i): the round-1 `frag not in ktext` dropped a unique fragment whenever it
    # happened to occur as a substring of an unrelated line (e.g. inside a wikilink
    # stem), silently losing content during dedup.
    # ... and strip the list marker on BOTH sides. The keeper stores a merged fragment as the
    # rendered bullet `- <frag>`, while the next pass offers the bare `<frag>`, so no fragment
    # this function ever merged was recognised on the pass after - and the docstring above
    # promises the opposite. Each consolidation appended the same lesson again, and the caller,
    # told the fragment was new, folded another " | merged: ..." tail into the keeper's indexed
    # description, whose 800-character cap then evicted real content to hold the repeat.
    def _norm(s: str) -> str:
        return re.sub(r"^\s*[-*]\s+", "", re.sub(r"\s+", " ", s).strip()).lower()

    existing = {_norm(ln) for ln in ktext.splitlines() if ln.strip()}
    extra = []
    for d in dup_fps:
        for frag in _fields(d):
            frag = frag.strip()
            nf = _norm(frag)
            if nf and nf not in existing:
                existing.add(nf)
                extra.append(frag)
    if not extra:
        return []
    header = ("" if ("## Merged from duplicates" in ktext
                     or "## Слито из дублей" in ktext)      # legacy header, dual-read
              else "\n\n## Merged from duplicates\n")
    block = header + "\n".join(f"- {e}" for e in extra) + "\n"
    m.write_atomic(keep_fp, ktext.rstrip() + "\n" + block)
    return extra


def _union_meta_into_keeper(keep_fp: Path, member_fps: list[Path]) -> None:
    """Union the graph-bearing frontmatter of every cluster member into the keeper before the
    duplicates are archived. Recurrence already carries forward as the cluster max, but its
    siblings - tags, entities, entity_types, relations, sources, confidence - were keeper-only,
    so each merge silently shrank the entity graph and dropped provenance (critic R3, same class
    as the recurrence fix, left open for every other field). List fields are de-duplicated
    (relations by identity); confidence takes the max. Idempotent."""
    try:
        ktext = keep_fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    kfm, _ = m._read_frontmatter(ktext)
    fms = [kfm]
    for fp in member_fps:
        try:
            fms.append(m._read_frontmatter(fp.read_text(encoding="utf-8", errors="replace"))[0])
        except OSError:
            continue

    def _as_list(v):
        #: a string through the engine's one list reader (R11): a hand-written `[a, b]` is two
        #: entries here too, not the one string `["[a, b]"]` the merge used to write back
        if isinstance(v, str):
            return m._list_field(v)
        return v if isinstance(v, list) else ([v] if v not in (None, "") else [])

    merged = {}
    for field in ("tags", "entities", "sources"):        # plain list fields
        seen, out = set(), []
        for fm in fms:
            for item in _as_list(fm.get(field)):
                key = str(item)
                if key not in seen:
                    seen.add(key)
                    out.append(item)
        if out and out != _as_list(kfm.get(field)):
            merged[field] = out
    # entity_types is a MAP ({name: type}), not a list: union as a dict (keeper wins on a
    # conflict) - folding it through _as_list produced a list-of-dicts that every later reader
    # then silently reset to {} (critic R3, the fix-review found this in its own fix).
    et: dict = {}
    for fm in reversed(fms):                              # kfm is fms[0] → applied last → wins
        v = fm.get("entity_types")
        if isinstance(v, dict):
            et.update(v)
    if et and et != (kfm.get("entity_types") if isinstance(kfm.get("entity_types"), dict) else {}):
        merged["entity_types"] = et
    # relations are list-of-maps: de-dup by canonical JSON
    seen_rel, rels = set(), []
    for fm in fms:
        for r in _as_list(fm.get("relations")):
            key = __import__("json").dumps(r, sort_keys=True, ensure_ascii=False) if isinstance(r, dict) else str(r)
            if key not in seen_rel:
                seen_rel.add(key)
                rels.append(r)
    if rels and rels != _as_list(kfm.get("relations")):
        merged["relations"] = rels
    # confidence: the cluster's strongest
    confs = [m._coerce_confidence(fm.get("confidence")) for fm in fms]
    confs = [c for c in confs if c is not None]
    if confs and max(confs) != m._coerce_confidence(kfm.get("confidence")):
        merged["confidence"] = round(max(confs), 3)

    if merged:
        m.write_atomic(keep_fp, m._stamp_frontmatter(ktext, merged))


def _cluster_tokens(rec: dict) -> set:
    return set(m._tokens(f"{rec.get('title','')} {rec.get('desc','')} {rec.get('prevention','')}"))


def find_clusters(cache: dict, exclude: set | None = None) -> list[list[str]]:
    """Greedy near-duplicate clusters within the same (project, ntype). Cosine is
    computed only between notes that share lexical tokens (found via a token→stems
    inverted index), not across all M² pairs in a bucket (audit A6): near-duplicates
    always share vocabulary, so the weekly scan that was quadratic (10k notes in one
    bucket ≈ 87 min of Python cosine) becomes roughly linear and finds the same
    clusters.

    `exclude` (F2, xhigh review): both members of every pair K8's write path is still keeping
    apart (`contested`) or the judge vetoed (`disputed`) are dropped from clustering entirely,
    on EITHER side. The same-title saturation that makes this merge useful (cosine/the twin
    classifier both key heavily on a shared title) is exactly what used to let it archive one
    side of a pair before the judge ever ruled - a duplicate merge is a different, unrelated
    mechanism from K8's own adjudication and must not pre-empt it."""
    exclude = exclude or set()
    # ≥1 shared content token (was 2): the twin classifier's own learned fact is that
    # true twins are re-PHRASINGS with LOW word overlap, so the 2-token prefilter was
    # biased against exactly the pairs worth merging (review 2026-08 D9). One shared
    # token still bounds the candidate set via the inverted index.
    MIN_SHARED = 1
    # The write-time twin gate shipped only into write_typed_note; the consolidator -
    # the only mechanism that can merge twins ALREADY in the store - kept scoring raw
    # cosine at a threshold most historical twin pairs sit below (median 0.833 vs
    # 0.86). When the learned gate's calibration space matches the active embedder, a
    # confident classifier verdict now also merges (review 2026-08 D9); plain-cosine
    # merging at SIM_THRESHOLD is unchanged, and "cosine" mode disables the extra path.
    twin_mode = m.WRITE_DEDUP_MODE != "cosine" and m._twin_space_ok()
    groups: dict[tuple, list[str]] = {}
    for stem, rec in cache.items():
        # only valid-ntype records - a malformed/legacy entry must not crash the
        # later TYPE_FOLDER[rec['ntype']] lookup (audit C4)
        if not isinstance(rec, dict) or rec.get("ntype") not in m.TYPE_FOLDER or stem in exclude:
            continue
        groups.setdefault((rec.get("project"), rec.get("ntype")), []).append(stem)

    clusters = []
    for stems in groups.values():
        # seed clustering from the highest-recurrence note, tie-broken by stem, so the merge
        # is deterministic run-to-run and the most-proven note anchors its cluster (audit)
        stems = sorted(stems, key=lambda s: (-_int1(cache[s].get("recurrence", 1)), s))
        toks = {s: _cluster_tokens(cache[s]) for s in stems}
        inv: dict = {}                          # token → stems sharing it
        for s in stems:
            for t in toks[s]:
                inv.setdefault(t, []).append(s)
        used = set()
        for a in stems:
            if a in used:
                continue
            shared: dict = {}                   # sibling stem → shared-token count
            for t in toks[a]:
                for b in inv.get(t, ()):
                    if b != a and b not in used:
                        shared[b] = shared.get(b, 0) + 1
            cluster = [a]
            for b, ov in shared.items():
                if ov < MIN_SHARED:
                    continue
                sim = m.cosine(cache[a].get("vec") or [], cache[b].get("vec") or [])
                merge = sim >= SIM_THRESHOLD
                if (not merge and twin_mode and sim >= m.WRITE_DEDUP_PREFILTER):
                    ra, rb = cache[a], cache[b]
                    p_twin = m._twin_probability(
                        sim, ra.get("title", ""), ra.get("desc", ""),
                        _note_entities(a, ra),
                        rb.get("title", ""), rb.get("desc", ""),
                        _note_entities(b, rb))
                    merge = p_twin >= m.WRITE_DEDUP_TWIN_P
                if merge:
                    cluster.append(b)
                    used.add(b)
            if len(cluster) > 1:
                used.add(a)
                clusters.append(cluster)
    return clusters


def _note_entities(stem: str, rec: dict) -> list:
    """A note's frontmatter entities for the twin-gate features (read only for pairs
    that already passed the cosine prefilter, like the write-time gate)."""
    folder = m.TYPE_FOLDER.get(rec.get("ntype"))
    if not folder:
        return []
    try:
        return m._read_frontmatter_file(m.VAULT / folder / f"{stem}.md").get("entities") or []
    except OSError:
        return []


AUTO_LINK_HEADER = "## Related (auto)"


def link_related_notes(cache: dict, apply: bool, k: int = 3, min_overlap: int = 3) -> int:
    """Dynamic Zettelkasten linking (M-7): for each note, add `[[links]]` to its
    top-k lexically-related siblings in the same project, so the store becomes a
    navigable network instead of isolated cards. GPU-free (token overlap). Runs
    once per note (skips notes already carrying the auto-link section)."""
    by_proj: dict = {}
    for stem, r in cache.items():
        if isinstance(r, dict) and r.get("ntype") in m.TYPE_FOLDER:
            by_proj.setdefault(r.get("project"), []).append((stem, r))
    linked = 0
    for _proj, notes in by_proj.items():
        toks = {s: m._tokens(f"{r.get('title','')} {r.get('desc','')} {r.get('prevention','')}")
                for s, r in notes}
        # Inverted index token→stems so each note only scores siblings it actually
        # shares a token with, instead of all N pairs (audit H3): the round-1
        # all-pairs intersection was ~quadratic (N=800 → 3 s, 5000 → ~2 min EVERY
        # weekly run). The per-token postings count IS the overlap size.
        inv: dict = {}
        for s, ts in toks.items():
            for t in ts:
                inv.setdefault(t, []).append(s)
        for s, r in notes:
            counts: dict = {}
            for t in toks[s]:
                for s2 in inv.get(t, ()):
                    if s2 != s:
                        counts[s2] = counts.get(s2, 0) + 1
            scored = sorted(((ov, s2) for s2, ov in counts.items() if ov >= min_overlap),
                            reverse=True)
            related = [s2 for ov, s2 in scored][:k]
            if not related:
                continue
            fp = m.VAULT / m.TYPE_FOLDER[r["ntype"]] / f"{s}.md"
            if not fp.exists():
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if AUTO_LINK_HEADER in text or "## Связанные (авто)" in text:
                continue                           # already auto-linked (either generation)
            fresh = [x for x in related if f"[[{x}]]" not in text]
            if not fresh:
                continue
            if apply:
                block = f"\n\n{AUTO_LINK_HEADER}\n" + "\n".join(f"- [[{x}]]" for x in fresh) + "\n"
                m.write_atomic(fp, text.rstrip() + "\n" + block)
            linked += 1
    return linked


def distill_patterns(cache: dict, apply: bool, max_distill: int = 3) -> int:
    """Sleep-time reflection (M-1): distil recurring mistakes (episodic) into a
    general prevention pattern (semantic) via the LLM. Same-slug supersession
    keeps it idempotent across weekly runs. The flagship 'leader' feature
    (cf. Letta sleep-time, ChatGPT Dreaming)."""
    cand = [(s, r) for s, r in cache.items()
            if isinstance(r, dict) and r.get("ntype") == "mistake"
            and _int1(r.get("recurrence", 1)) >= 2]
    cand.sort(key=lambda sr: -_int1(sr[1].get("recurrence", 1)))
    made = 0
    for s, r in cand[:max_distill]:
        print(f"  distil recurring mistake -> pattern: {s}")
        if not apply:
            made += 1
            continue
        prompt = ("From this RECURRING mistake derive the GENERAL durable pattern-rule for "
                  "avoiding it in the future, in the language the mistake is written in. "
                  'Return ONLY JSON {"title": "short rule", "description": "1-2 sentences"}.\n\n'
                  f"MISTAKE (recurred {r.get('recurrence')}x): {r.get('title','')} - "
                  f"{r.get('desc','')} {r.get('prevention','')}")
        res = m.generate_json(prompt, project=r.get("project"))
        m.refresh_lock()    # F6, per unit: this runs under the same lock as the judge loop
        title = (res.get("title") or "").strip() if isinstance(res, dict) else ""
        desc = (res.get("description") or "").strip() if isinstance(res, dict) else ""
        # Quality gate (audit M-e): only accept a REAL distilled rule, not model
        # junk. The round-1 code accepted anything with a title and stamped it as a
        # learned pattern that RESOLVES the mistake - silently poisoning recall and
        # muting a real warning. Require a substantive title + description, and
        # reject a verbatim echo of the source mistake.
        src_title = (r.get("title", "") or "").strip().lower()
        src_desc = (r.get("desc", "") or "").strip().lower()
        if len(title) < 6 or len(desc) < 15:
            print(f"    [skip] distilled pattern failed quality gate: {title[:40]!r}")
            continue
        if title.lower() == src_title or desc.lower() in (src_desc, src_title):
            print(f"    [skip] distilled pattern echoes the source mistake")
            continue
        date = m.datetime.now().strftime("%Y-%m-%d")
        stem = m.write_typed_note(
            m.TYPE_FOLDER["pattern"],
            {"title": title, "description": desc,
             "resolves": r.get("title", "")}, r.get("project"), date,
            ["distilled"], "pattern")
        if stem:
            made += 1
    return made


def select_coreset(ids, budget, utility_of, tokens_of):
    """Choose `budget` items maximizing the facility-location coverage
    F(S) = Σ_m u(m)·max_{s∈S} sim(m, s), sim = token Jaccard - a monotone submodular
    objective, so lazy greedy (CELF) is within (1−1/e) of optimal (1C). Keeps a
    diverse, high-utility coreset: it won't hoard near-duplicates of one cluster while
    forgetting another (which a pure salience sort does). Pure stdlib; sparse via an
    inverted index, so cost scales with shared tokens, not N²·dim. Returns the kept set."""
    ids = list(ids)
    if len(ids) <= budget:
        return set(ids)
    toks = {i: tokens_of(i) for i in ids}
    u = {i: max(0.0, float(utility_of(i))) for i in ids}
    inv: dict = {}                                   # token -> items (sparse neighbours)
    for i in ids:
        for t in toks[i]:
            inv.setdefault(t, []).append(i)
    nbr = {i: set() for i in ids}
    for lst in inv.values():
        for a in lst:
            nbr[a].update(lst)

    def sim(a, b):
        ta, tb = toks[a], toks[b]
        inter = len(ta & tb)
        return inter / (len(ta) + len(tb) - inter) if inter else 0.0

    cov = {i: 0.0 for i in ids}                      # current max sim of i to S
    S: set = set()

    def gain(s):                                     # marginal coverage gain of adding s
        g = u[s] * (1.0 - cov[s])                    # s covers itself (sim 1)
        for mm in nbr[s]:
            if mm != s:
                g += u[mm] * max(0.0, sim(mm, s) - cov[mm])
        return g

    # lazy greedy (CELF): (−gain, −utility, item, round stamp); −utility breaks coverage
    # ties toward the higher-utility representative (else, among near-duplicates whose
    # marginal coverage is equal/zero, the kept one would be arbitrary, not the best).
    heap = [(-gain(i), -u[i], i, 0) for i in ids]
    heapq.heapify(heap)
    rnd = 0
    while len(S) < budget and heap:
        neg_g, neg_u, s, stamp = heapq.heappop(heap)
        if s in S:
            continue
        if stamp == rnd:                             # gain is fresh ⇒ optimal pick
            S.add(s)
            cov[s] = 1.0
            for mm in nbr[s]:
                cov[mm] = max(cov[mm], sim(mm, s))
            rnd += 1
        else:                                        # stale ⇒ recompute and reinsert
            heapq.heappush(heap, (-gain(s), -u[s], s, rnd))
    return S


def cap_project_notes(cache: dict, apply: bool) -> int:
    """Bound the live-note count per (project, ntype) - the storage counterpart to
    the retrieval prefilter (P1), so a single project can't grow the cache/index/
    build time without bound. OFF by default (cap=0): a memory store should not
    silently shed memory. When NEVERTWICE_MAX_LIVE_PER_PROJECT>0, only the
    *lowest-salience* excess is archived into <folder>/Archive/ (still on disk, just
    out of active recall) - high recurrence, then unresolved, then newest are kept.
    A kept note's [[wikilinks]] to an archived one don't dangle: Obsidian resolves
    links by stem regardless of folder, and graph-hop recall skips non-cached stems."""
    if MAX_LIVE_PER_PROJECT <= 0:
        return 0
    buckets: dict = {}
    for stem, r in cache.items():
        if isinstance(r, dict) and r.get("ntype") in m.TYPE_FOLDER:
            buckets.setdefault((r.get("project"), r.get("ntype")), []).append((stem, r))
    archived = 0
    sal = m.salience_index()                         # F5: one corpus-wide scan, reused for every bucket
    for (proj, nt), items in buckets.items():
        if len(items) <= MAX_LIVE_PER_PROJECT:
            continue
        rec_of = dict(items)
        def _util(stem, _r=rec_of, _s=sal):          # query-independent value (1A frequency prior)
            r = _r[stem]
            n = _int1(r.get("recurrence", 1))
            base = n * (m.RETRIEVAL_RESOLVED_WEIGHT if r.get("resolved") else 1.0)
            return base * (1.0 + _s.get(stem, 0.0))  # a graph-central note is kept over a peripheral one
        def _toks(stem, _r=rec_of):
            r = _r[stem]
            return m._tokens(f"{r.get('title','')} {r.get('desc','')} {r.get('prevention','')} {stem}")
        keep = select_coreset(list(rec_of), MAX_LIVE_PER_PROJECT, _util, _toks)
        excess = [(s, r) for s, r in items if s not in keep]
        folder = m.VAULT / m.TYPE_FOLDER[nt]
        print(f"  cap {proj}/{nt}: {len(items)} live > {MAX_LIVE_PER_PROJECT} "
              f"-> archive {len(excess)} (utility-coverage coreset keeps the diverse {len(keep)})")
        if not apply:
            archived += len(excess)
            continue
        arch = folder / "Archive"
        arch.mkdir(exist_ok=True)
        for stem, _ in excess:
            src = folder / f"{stem}.md"
            if src.exists():
                # os.replace + per-file guard (review 2026-08 P4): a transiently-locked
                # note (Obsidian/AV/sync) must cost ONE skip, not the whole weekly run
                try:
                    os.replace(src, _archive_target(arch, src.name))
                except OSError as e:
                    print(f"      archive failed for {src.name} ({e}) - left live",
                          file=sys.stderr)
                    continue
            cache.pop(stem, None)
            archived += 1
    return archived


def stamp_salience(apply: bool) -> int:
    """Brain F5: score every note's graph SALIENCE (pure centrality - inbound edges + degree;
    recurrence is applied separately by the ranker) and stamp it into frontmatter, so retrieval
    applies the gentle centrality nudge and the coreset/cards can prefer central notes. Sleep-time,
    GPU-free, idempotent (writes only on a meaningful change). Returns the count (re)stamped. Inert
    on an entity-less store (salience {}) AND on a coding-only install: the brain layer is opt-in,
    so a default install must not accumulate salience frontmatter the ranker then (previously)
    applied on the hot path (critic R3)."""
    if not m._cfg.brain_enabled():
        return 0
    sal = m.salience_index()                   # {stem: [0,1]}; {} → nothing central → no-op
    if not sal:
        return 0
    stamped = 0
    for ntype, folder in m.TYPE_FOLDER.items():
        d = m.VAULT / folder
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            s = sal.get(p.stem)
            if s is None:
                continue
            new = round(s, 3)
            cur = m._read_frontmatter_file(p).get("salience")
            try:
                if cur is not None and abs(float(cur) - new) < 1e-3:
                    continue                   # unchanged → skip (no churn)
            except (TypeError, ValueError):
                pass
            if new <= 0 and cur is None:
                continue                       # don't stamp a 0 onto a peripheral note
            if not apply:
                stamped += 1
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                m.write_atomic(p, m._stamp_frontmatter(text, {"salience": new}))
                stamped += 1
            except OSError:
                pass
    return stamped


def main():
    apply = "--apply" in sys.argv
    mode = "APPLY" if apply else "DRY-RUN"
    # Don't abort without a backend (audit LOW): the GPU-free steps - near-dup
    # merge, dynamic linking, archival, card refresh - still run. Only the
    # LLM-dependent steps (distillation, context summaries) are skipped.
    has_llm = m.llm_available()
    if not has_llm:
        print("[consolidate] No LLM backend - running GPU-free steps only "
              "(dedup · link · archival · cards); skipping distillation + "
              "context summaries.", file=sys.stderr)
    # Single-writer invariant: the APPLY path mutates the vault (merges, archival, context
    # compaction, index rebuilds), so it must hold the SAME lock the live hook takes - else a
    # concurrent SessionEnd write races this run and one clobbers the other (launch-round
    # audit). DRY-RUN is read-only and needs no lock.
    if apply and not m.acquire_lock(timeout_s=120):
        print("[consolidate] vault lock busy - another writer is active; aborting",
              file=sys.stderr)
        sys.exit(2)
    try:
        _run_consolidation(apply, mode, has_llm)
    finally:
        if apply:
            m.release_lock()


def _run_consolidation(apply, mode, has_llm):
    # 1) load the embedding index (kept fresh by the hook; run embed_index.py
    #    first if you bulk-edited notes)
    cache = m.load_embed_cache()
    if not cache:
        # First install / freshly cloned vault: no vectors yet, so the cosine dedup has
        # nothing to do - but the GPU-free aging/archival/compaction below still should,
        # so warn and continue instead of aborting ALL maintenance (launch-round audit).
        print("[consolidate] empty embedding cache - skipping near-dup merge (run "
              "embed_index.py to enable it); continuing with archival/compaction.",
              file=sys.stderr)

    # 1a) SPACE GATE (review 2026-08): every retrieval path abstains via
    #     embed_cache_usable() when the cached vectors were produced by a different
    #     embedding model - the DESTRUCTIVE dedup below must abstain too, or it
    #     clusters cross-space cosines with thresholds calibrated for the live
    #     space and mass-merges distinct notes.
    dedup_ok = bool(cache) and m.embed_cache_usable()
    if cache and not dedup_ok:
        print("[consolidate] embedding cache is from a different embedding space - "
              "SKIPPING near-dup merge (run embed_index.py --rebuild); "
              "continuing with archival/compaction.", file=sys.stderr)

    # 1b) self-heal: upgrade text-only entries (written while the embedder was down,
    #     #32) to real vectors - the docstring's step 1, previously unimplemented:
    #     nothing else on the scheduled paths ever ran embed_index, so those notes
    #     stayed lexical-only forever.
    if apply and dedup_ok and m.embedder_available(2):
        kind = "document" if m.cache_is_prefixed() else None
        upgraded = 0
        for s, r in cache.items():
            if not isinstance(r, dict) or r.get("vec"):
                continue
            vec = m.embed_text(
                f"{r.get('title', '')}\n{r.get('desc', '')}\n{r.get('prevention', '')}".strip(),
                kind=kind, project=r.get("project"))
            if vec:
                r["vec"] = vec
                upgraded += 1
        if upgraded:
            m.save_embed_cache(cache)
            print(f"[consolidate] embedded {upgraded} text-only note(s) (index self-heal)")

    # 2) K8 layer 3: the contested same-slug pairs the write path kept apart go to the judge here -
    #    one call a pair, capped, outside any session chain (no cache confound, no hook millisecond).
    #    F2 (xhigh review): this runs BEFORE the near-duplicate merge below, not after - the merge
    #    saturates on a shared title (cosine and the twin classifier both key heavily on it) and used
    #    to archive one side of a pair K8 deliberately keeps apart before the judge ever ruled on it.
    #    Judging first also means a pair the judge just resolved this run is already off the
    #    contested list by the time the merge's own exclusion set (next) is built.
    adj = adjudicate_contested(apply, has_llm, cache=cache)
    print(f"[consolidate] contested pairs: {adj['pairs']} - {adj['judged']} judge call(s), "
          f"{adj['tokens_spent']} of {adj['budget']} tokens (~{adj['budget'] // TOKENS_PER_PAIR_EST} pairs a run "
          f"at {TOKENS_PER_PAIR_EST}): replaces {adj['replaces']}, separate {adj['separate']}, vetoed {adj['vetoed']}, "
          f"unanswered {adj['unanswered']}, left {adj['left']}"
          + (f"; tokens {adj['prompt_tokens']}+{adj['eval_tokens']}" if adj["judged"] else "")
          + (f"; {adj['estimated_calls']} call(s) charged the estimate" if adj["estimated_calls"] else "")
          + (f"; {adj['errors']} pair(s) failed (left as is)" if adj.get("errors") else "")  # F10
          # #11: what the run did besides judging - each printed when it happened, 0 omitted
          + "".join(f"; {k.replace('_', ' ')} {adj[k]}" for k in
                    ("requeued", "baselined", "healed")
                    if adj.get(k))
          + (f" ({adj['skipped']})" if adj.get("skipped") else "") + f" [{mode}]")
    if apply and (adj["replaces"] or adj.get("healed")):
        m.save_embed_cache(cache)

    # 2b) F2: both members of every pair K8 is still keeping apart (contested) or the judge
    #     vetoed (disputed) are off limits to the near-dup merge below - a pair still on either
    #     list after the judging above is one no automatic mechanism has resolved yet.
    #     One walk for both keys (#13): the two stamps come from the same header read.
    contested_stems: set = set()
    for _rows in m._iter_contested_both(None):
        for _c in _rows:
            contested_stems.add(_c["stem"])
            contested_stems.update(_c["new_stems"])

    # 3) near-duplicate merge
    clusters = find_clusters(cache, exclude=contested_stems) if dedup_ok else []
    print(f"[consolidate] {mode} | {len(clusters)} near-duplicate cluster(s) "
          f"(sim>={SIM_THRESHOLD})")
    merged = 0
    for cluster in clusters:
        cluster.sort(key=date_of, reverse=True)
        keep, dups = cluster[0], cluster[1:]
        rec = cache[keep]
        folder = m.VAULT / m.TYPE_FOLDER[rec["ntype"]]
        print(f"  cluster ({rec['project']}/{rec['ntype']}) x{len(cluster)} "
              f"-> keep {keep}")
        for d in dups:
            print(f"      archive {d}")
        if apply:
            keep_fp = folder / f"{keep}.md"
            dup_fps = [folder / f"{d}.md" for d in dups]
            if not keep_fp.exists():
                # The keeper exists only in the cache (manual deletion, a crash between
                # file ops and the cache save). Archiving the rest of the cluster anyway
                # removed EVERY live representative of the lesson, stamped with a
                # duplicate_of pointing at a nonexistent note (review 2026-08 B6).
                # Promote the newest EXISTING member instead; none → drop the stale
                # cache entry and leave the cluster alone.
                live = [d for d in dup_fps if d.exists()]
                if not live:
                    cache.pop(keep, None)
                    print(f"      keeper {keep} missing on disk and no live members - "
                          f"cluster skipped", file=sys.stderr)
                    continue
                cache.pop(keep, None)
                keep_fp = sorted(live, key=lambda p: p.stem, reverse=True)[0]
                keep = keep_fp.stem
                dup_fps = [d for d in live if d is not keep_fp]
                print(f"      keeper missing on disk - promoted {keep}", file=sys.stderr)
            live_dups = [d for d in dup_fps if d.exists()]
            merged_frags = merge_into_keeper(keep_fp, live_dups)
            _union_meta_into_keeper(keep_fp, live_dups)   # keep the graph edges + provenance
            # carry the cluster's HIGHEST recurrence forward, not just the keeper's - a
            # merged older dup may have recurred more than the (newest) keeper, and
            # archiving it would otherwise SILENTLY DROP that count the recall boost
            # depends on (extends the round-3 max fix; matters now that recurrence
            # actually grows via supersession - W15).
            rec_n = _cluster_recurrence(cache, cluster)
            set_recurrence(keep_fp, rec_n)
            if isinstance(cache.get(keep), dict):
                krec = cache[keep]
                krec["recurrence"] = rec_n            # so recall boosts it (H4)
                if merged_frags:
                    # The merge promised to preserve the duplicates' unique wording, but
                    # the keeper's cache/FTS/vector text stayed pre-merge, so a query
                    # matching only the merged-in wording found NOTHING (review 2026-08).
                    # Fold the fragments into the indexed desc and re-embed; if the
                    # embedder is down the old vector stays (still valid for the
                    # keeper's own text) and FTS covers the fragments after the
                    # rebuild_scale_index below.
                    krec["desc"] = (f"{krec.get('desc', '')} | merged: "
                                    + " ; ".join(merged_frags))[:800].strip()
                    vec = m.embed_text(
                        f"{krec.get('title', '')}\n{krec.get('desc', '')}\n"
                        f"{krec.get('prevention', '')}".strip(),
                        kind=("document" if m.cache_is_prefixed() else None),
                        project=krec.get("project"))
                    if vec:
                        krec["vec"] = vec
            arch = folder / "Archive"
            arch.mkdir(exist_ok=True)
            for src in dup_fps:
                if src.exists():
                    # Stamp the keeper BEFORE archiving: without `duplicate_of` the cluster
                    # relationship evaporated the moment apply ran - unauditable, and it
                    # threw away free labeled training pairs for twin-detection /
                    # embedding-specialization research (2026-08-18: 103 freshly-archived
                    # duplicates had to be reconstructed by similarity because nothing
                    # recorded which keeper each one merged into).
                    try:
                        text = src.read_text(encoding="utf-8", errors="replace")
                        m.write_atomic(src, m._stamp_frontmatter(
                            text, {"duplicate_of": keep}))
                    except OSError:
                        pass
                    # Per-file guards (review 2026-08 P4): the store is a live Obsidian
                    # vault - the indexer/AV/sync briefly holding one note open raised
                    # PermissionError and aborted the WHOLE weekly run mid-loop, with
                    # already-popped cache entries saved only on loop completion.
                    # os.replace: atomic overwrite, no unlink→rename race window.
                    try:
                        os.replace(src, _archive_target(arch, src.name))
                    except OSError as e:
                        print(f"      archive failed for {src.name} ({e}) - left live",
                              file=sys.stderr)
                        continue
                    cache.pop(src.stem, None)
                    merged += 1
    if apply and (merged or clusters):
        m.save_embed_cache(cache)

    # 4) per-project cap (P2): bound live notes per project so none grows unbounded
    #     (opt-in; archives lowest-salience excess). The scale-index rebuild below
    #     reflects the archival.
    capped = cap_project_notes(cache, apply)
    if capped:
        print(f"[consolidate] per-project cap: {capped} low-salience note(s) archived")
        if apply:
            m.save_embed_cache(cache)

    # 5) compaction + aging. This is the designated heavy/non-interactive window,
    #    so LLM context summaries are allowed here (unlike the live hook path,
    #    which is GPU-free to keep the vault lock off any model call - audit C4).
    print("[consolidate] context compaction + archival "
          f"({'applying' if apply else 'skipped in dry-run'})")
    if apply:
        m.maintain_contexts(allow_llm=has_llm)   # compact (LLM) + refresh cards (GPU-free)
        m.archive_old_typed()
        m.archive_old_sessions()

    # 6) sleep-time reflection (M-1) + dynamic linking (M-7) - episodic→semantic
    #    distillation and a navigable note network. Reload cache (links/distil read it).
    distilled = distill_patterns(cache, apply) if has_llm else 0
    linked = link_related_notes(m.load_embed_cache() if apply else cache, apply)
    print(f"[consolidate] reflection: {distilled} pattern(s) distilled, "
          f"{linked} note(s) auto-linked ({'applied' if apply else 'dry-run'})")

    # 6b) salience scoring (Brain F5): stamp graph-centrality salience BEFORE the index rebuild
    #     so retrieval reads the fresh nudge. GPU-free; inert on an entity-less store.
    salstamp = stamp_salience(apply)
    if salstamp:
        print(f"[consolidate] salience: {salstamp} note(s) "
              f"{'stamped' if apply else 'would be stamped'} (graph centrality)")

    # 6c) active memory (axis A): distil high-recurrence mistakes into executable guards so the
    #     PreToolUse hot path has something to fire on. Sleep-time only (may use the LLM); the
    #     hot path only ever READS the resulting ledger. Idempotent (dedup by born_from).
    if apply:
        try:
            import guards as _guards
            added = _guards.generate_from_vault(min_recurrence=2, use_llm=has_llm)
            if added:
                print(f"[consolidate] active memory: {added} new guard(s) distilled from mistakes")
        except Exception as e:
            print(f"[consolidate] guard generation skipped: {e}", file=sys.stderr)

    if apply:
        m.rebuild_index()    # Index.md is itself OKF-valid now (type: index - audit H1/M-14)
        # the merges/archival/distillation above changed the note set → rebuild the
        # SQLite scale index so retrieval stays consistent (audit C2/C3)
        m.rebuild_scale_index()
        # refresh the learned user model (I-6) as part of sleep-time work
        try:
            import build_user_model
            build_user_model.main()
        except Exception as e:
            print(f"[consolidate] user-model refresh skipped: {e}", file=sys.stderr)

    print(f"[consolidate] done ({mode}): {merged} note(s) archived as duplicates")


if __name__ == "__main__":
    main()
