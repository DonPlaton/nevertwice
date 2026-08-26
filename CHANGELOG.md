# Changelog

All notable changes to Nevertwice. Format loosely follows [Keep a Changelog](https://keepachangelog.com);
versions are [semantic](https://semver.org). Dates are UTC.

## [Unreleased]

### Added
- **Uncertainty, done properly - and it changes what the earlier results mean (GOAL F5).**

  F1 through F4 each published a Wilson interval. Those are **unpaired**, and the data is not:
  every arm sees the same episodes. `research/uncertainty.py` redoes the comparisons paired -
  exact McNemar on the discordant pairs, a percentile bootstrap over episodes with a fixed
  recorded seed, Cohen's h, and Holm-Bonferroni across the whole family of comparisons.

  Three findings the earlier, cruder analysis could not have produced:

  - **A strict subset, not a tie.** Against raw lexical recall the memory arm wins *zero*
    episodes the baseline misses, while the baseline wins two. That is stronger than "not
    distinguished": on this corpus its successes are a subset of the cheaper arm's, and no
    episode here justifies the extra machinery.
  - **Complementarity with the linter, which head-to-head comparison was hiding.** The two arms
    have the *same* aggregate and fail on different episodes - ten each that the other gets. The
    union reaches far higher than either alone. Two arms with equal scores can be one system
    twice or two different systems, and the discordant cells are what tell them apart. The
    engineering conclusion is not that one wins; it is that a deployment should run both.
  - **The aggregate is a mixture.** Four of fifteen families are never got right at all. The
    per-family table and the per-episode results are both published, and the headline is
    recomputed with each family removed in turn - which is F5's exit criterion executed rather
    than asserted. No single family carries it, which is the one reassuring thing in the report.

  Only the curated-`AGENTS.md` and extractive-summary comparisons survive correction. The
  paired intervals supersede F1-F4's for any arm-vs-arm statement; the Wilson figures remain
  correct for a single arm's own rate.

  `tests/_test_uncertainty.py` (107 checks) tests the statistics against cases with known
  answers, because a hand-rolled McNemar or Holm is exactly the code that looks right and is off
  by one. Ten mutations, all killed. Two found real gaps: the step-down rule was never
  discriminated by the test case chosen for it, and reproducibility could not be checked by
  comparing two runs at all - at ten thousand resamples the percentiles are stable to four
  decimals whatever the seed, so the seed is now enforced **structurally**, by walking the
  module's AST for any unseeded generator.

- **Full-loop ablations, and the one mechanism they can name (GOAL F4).**

  A system with seven moving parts and one aggregate number cannot say which part earns its
  place. `research/ablations.py` removes each one alone, on F1's corpus through F1's
  matched-condition sweep, and reports what the removal costs against the interval the sample
  supports.

  **The paper may name exactly one mechanism: coverage normalisation.** Removing it does not
  merely lower recall - it loses the zero-false-alarm operating point *entirely*, so the system
  can no longer be run without crying wolf. That is the strongest result the table can hold, and
  it gets its own verdict rather than a subtraction against a missing value.

  **It may name no other.** IDF weighting, the coincidence damper and recurrence weighting all
  move the number by less than the sampling interval, and are reported as *not shown to matter* -
  which is not the same sentence as "does nothing", and the suite fails if any verdict says
  otherwise. Read beside F1 and F2, where the shipped scorer ties raw token overlap, this is
  coherent: coverage is what keeps it quiet, and the rest is not yet earning its place.

  **Outcome feedback is measured separately, because it is a mechanism over time**, and it
  works: three false alarms silence a crying-wolf failure mode, an unrelated mode's bar is
  untouched, and a strong signal still breaks through a raised bar.

  **Five of GOAL's seven named ablations are NOT EXERCISED by this surface** - code validation,
  temporal decay, graph hops, self-retirement and consolidation act on the store and the recall
  path, which this corpus does not measure. Each is listed with the surface that would reach it,
  because "we did not measure it" and "we measured it and it did nothing" are different
  sentences and only the first is true of them. A mutation deleting that distinction turns the
  suite red.

  The reference row is checked against the shipped `risk_score` on all 900 episode-signature
  pairs. Without that, every delta would be measured against a system nobody runs - the failure
  F1 had once already. `tests/_test_ablations.py`, 57 checks, 8 mutations all killed.

- **The model-capability grid: how much of the help is the reader's (GOAL F3).**

  A memory system prevents nothing by itself. It surfaces a sentence, and a *reader* either
  turns that sentence into a different action or does not. `research/capability_grid.py` holds
  the memory fixed - the same episodes and the very prevention F2's curated-file arm was scored
  on - and varies only who is reading, across a local Qwen2.5 ladder on the RTX 5090.

  **The reader does bound actionability on this evidence**, and the grid says so with the
  interval attached: the spread across the ladder exceeds the half-width the sample supports.
  It also says the ladder is **not monotonic** - the lift dips in the middle - which at this
  sample size reads as noise and is the strongest single reason not to quote any one cell as
  "the" number. A prevention rate published without naming the reader is not a property of the
  memory system, and that is now a written constraint.

  Two decisions decide whether the numbers mean anything, and both are checked:

  - **The grader is deterministic word-overlap, identical in every cell** - crude, and crude in
    exactly the same way for every model, which is what a capability comparison needs. An LLM
    judge was the obvious alternative and the wrong one: it puts a second, uncontrolled
    capability in the middle of a capability measurement. The suite greps the grader for any
    model call.
  - **Adoption is swept over grader strictness**, not pinned to one cutoff, for the same reason
    F1 publishes a threshold sweep.

  **Cells that could not run are recorded, never missing.** The 7B cell is `BLOCKED`: its
  weights are a half-finished download, three shards short, and the grid now names the missing
  files instead of surfacing a `FileNotFoundError` forty frames deep. The two frontier cells are
  `NOT RUN` under G8, and asking for one exits non-zero with the gate and the remedy rather than
  quietly running something else.

  `tests/_test_capability_grid.py` (62 checks) runs on the recorded generations with no GPU, so
  it stays hermetic. Ten mutations, all killed - including handing the control arm the
  prevention, calling a spread inside the noise a capability effect, and dropping a blocked cell
  from the grid.

- **The cheap-baseline suite, and the claim it narrowed (GOAL F2).**

  `research/BASELINES.md` was a policy with three unbuilt arms in it. `research/cheap_baselines.py`
  builds them and runs all six B5 baselines through F1's matched-condition machinery, so the
  policy stops being a promise. Two of the three are the ones that hurt, and both were built to
  be **strong**:

  - **a curated `AGENTS.md`** - hand-written from the same incident history, charged its full
    length on *every* episode rather than only when it helps, because that is what
    always-injected means. It loses decisively: an order of magnitude more context per episode
    for far less prevention. That settles what this project had called its single most
    important missing comparison.
  - **the relevant linter or test**, built as an explicit *oracle upper bound* - it never cries
    wolf, because a linter reads code rather than intentions, and it catches every instance of a
    class it covers. It **ties** the memory arm at zero false alarms, at a fraction of the
    latency. For the classes an existing tool already covers, a guard is a worse linter; what
    the memory system can claim has to be about the classes no linter covers, and that is now a
    written constraint.

  **The verdict is that the claim does not survive, and it is narrowed in writing.** Raw lexical
  recall is not distinguished from the memory arm on this corpus. The narrowing is *derived*
  from the numbers rather than written beside them, names every arm the claim does not beat, and
  refuses to call a difference smaller than the sampling interval a win.

  The **LLM session-summary arm is built and its run is gated**: no local model server is
  reachable here and a frontier call is G8. What runs is a deterministic extractive summariser,
  reported under its own name, `session_summary_extractive`, and never as an LLM result. Asking
  for the model arm exits non-zero rather than quietly returning the stub.

  `tests/_test_cheap_baselines.py` (43 checks) plus nine mutations, all killed - including
  reporting a tie as a decisive win, billing the always-injected file only when it fires,
  weakening the linter arm into a strawman that cries wolf, and dropping the unrun arm from the
  table. Two of those mutations found real defects: a narrowing branch that never fires on the
  current data shipped untested, and the *survives* branch returned early and so skipped the
  warning that the LLM arm never ran - the one case where that omission would do damage.

- **The matched-condition harness, and the negative result it found (GOAL F1).**

  `research/matched_conditions.py` answers the one-line attack every proactive-memory result
  invites - *you fired more often, so of course you caught more* - by making the firing
  threshold the x-axis. It sweeps the whole range, publishes the complete precision/recall
  curve to `research/matched_conditions.json`, and reads every arm at a **matched false-alarm
  rate**. An arm that cannot reach that rate is reported as unreachable rather than dropped,
  which is `full_history`'s honest result: firing every time means a hundred-percent
  false-alarm rate by construction.

  **The finding is negative, and it is about the shipped mechanism.** At the operating point
  that matters most for a system that interrupts - zero false alarms - raw token overlap
  recovers *more* past failures than the shipped risk scorer does. Recurrence weighting and the
  coincidence damper buy a better area under the curve and do not buy that point. Neither gap
  is resolvable at this sample size; the correct reading is "not yet distinguished", not
  "equivalent" and not "better". F2 has to settle it on a corpus this author did not write.

  The numbers are deliberately **not** registered as manifest claims yet. Registering a result
  that says "not yet distinguished" would invite exactly the over-reading this harness exists
  to prevent. They live in the artifact until a non-author corpus makes them worth citing.

  Two things the harness caught about itself, both worth more than the numbers:

  - It was scoring `risk_score` **without the IDF table** `anticipate()` builds, so it measured
    a function the product does not ship. The suite runs every episode through `anticipate()`
    itself and fails on any disagreement, which is what surfaced it.
  - Its findings list was prose with the figures typed in. When the harness was corrected every
    number moved and the prose silently became false. The findings are now *derived* from the
    results, so a list that cannot disagree with its own data is the only kind published.

  `tests/_test_matched_conditions.py` (48 checks) turns the exit criterion into an executable
  statement, including that no positive episode may share a majority of its content tokens with
  the note it is labelled against - an episode that quotes its own note measures string
  matching and reports it as anticipation. Eight mutations of the harness and the corpus, all
  killed, including truncating the curve to its flattering half and crediting "fire every time"
  with no false alarms.

- **The first seam out of `memory_hook.py`: `store_state.py` (GOAL E4).**

  6,411 lines to 6,333, with the public import surface unchanged. `write_atomic`,
  `_replace_with_retry`, `_load_json_generations` and `_save_json_generations` now live in
  `nevertwice/store_state.py` and are re-exported from `memory_hook`, so every existing
  `from memory_hook import write_atomic` keeps working - verified in **both install shapes**,
  package and flat scripts dir.

  The risk in a move like this is not that the new module is wrong, it is that nobody can
  tell. So `tests/_test_characterize_store_state.py` was written **against the code before it
  moved**, run green there, and then run **unchanged** afterwards - 49 checks pinning the
  behaviours each incident paid for: all-or-nothing publish (audit F1/F3/F30), no orphaned
  `.tmp` (audit D3), eight threads not racing on one temp name (GOAL E1), strict decoding so a
  bit flip reaches the `.bak` instead of decoding into plausible data (review 2026-08-24), loud
  recovery, and an absent primary not being reported as corruption.

  A mutation sweep of the moved module and the facade killed 8 of 8 - including deleting a
  re-export, dropping the thread id from the temp name, and copying the `.bak` from the on-disk
  primary. **Three of those eight survived the first sweep**, which is the point of running
  one: the orphan-`.tmp` check injected its failure before the temp file existed, the
  `.bak` check compared two files that were identical under both implementations, and the
  retry-bound check asserted a constant rather than that the retry ever stops. All three were
  gaps in the tests, not the code, and all three are now closed.

- **Local operational telemetry, and a proof that it is local: `nevertwice-telemetry`.**

  `stats.py` is the token ledger - what recall cost, what it plausibly avoided. This is the
  other half, and the questions are different: is capture keeping up, is extraction failing, is
  search getting slower, are interventions being accepted or overridden, how big has the store
  got. **Every failure this project has had in production was silent**, and four of the five
  counters are the ones whose movement would have made a silent failure visible.

  **Local-only here is structural, not a promise.** The module contains no networking code at
  all - no socket, no urllib, no http, no requests - so there is no code path that could
  transmit anything, whatever a config file said. `--export` writes a *file*; sending it is an
  action a person takes. That is deliberately less flexible than an opt-in flag, and stronger:
  a flag can be flipped by a config file, an environment variable, or a future patch that means
  well, and the absence of a transport cannot.

  `tests/_test_telemetry.py` proves both halves rather than asserting them - 43 checks. It
  walks the module's own AST for any networking import, dynamic import or call, and it runs the
  whole lifecycle - record, refresh, snapshot, export - **in a child process where
  `socket.socket` raises on construction**, requiring it to complete. A module that merely
  happens not to phone home on an offline machine would pass the second check and fail the
  first. Six mutations *of the module* turn the suite red, including adding an import of
  `socket`.

  Writing the suite found three real defects in the new module: `refresh_capture_lag` raised
  `TypeError` on a store with no `Patterns/` directory yet - in a function whose whole contract
  is that it never raises; `record_search` raised on a non-numeric measurement, from the hook
  path; and `record_outcome` invented outcome names outside `outcomes.OUTCOMES`, which would
  have produced a dashboard that disagrees with the guard lifecycle about what happened. All
  three are fixed and pinned.

  The export carries a versioned schema documenting every field, states its own transmission
  policy in the file, and ships **no note content, titles or queries** - and not the raw latency
  samples either, the one field that could grow without bound.

- **An executable security policy: `docs/THREAT_MODEL.md`, machine-checked.**

  `SECURITY.md` says what to do about a vulnerability. It never said what the system claims to
  *defend*, and a claim nobody runs is a claim nobody keeps. The threat model names **eight
  trust boundaries** - session capture, extraction, note file to reader, imported memory, guard
  lifecycle, the store on disk, the MCP surface, outbound network - and for each one: who owns
  it, what is trusted, what is not, and **17 claims each naming a check that runs in CI**.

  `tests/_test_threat_model.py` parses the document and fails when a boundary has no owner,
  when an owner names a file that does not exist, when a boundary states only one side, or when
  a claim names a check that is not in the suite it points at. It caught a real error on its
  first run: a claim pointing at `_test_budget.py` for a check that lives in `_test_outcomes.py`.
  Seven mutations *of the document* turn it red. It runs as its own CI step.

  `tests/_test_security_policy.py` is the fixture half - eight secret formats, indirect prompt
  injection, path traversal, malicious frontmatter, an untrusted export carrying a payload, and
  poisoned recurrence - 24 checks against the real primitives.

  **The known gaps are in the document**, because a threat model listing only what it defends is
  marketing. The HTML-comment injection gap is *pinned by a test that fails the day it closes*,
  so the document gets corrected rather than silently becoming wrong.

### Fixed
- **An unbounded recurrence count read from a note file was a ranking-poisoning vector.**
  Recurrence counts distinct contributing sessions and its provenance set was already capped at
  25, but the count itself - the ranking signal - was read from frontmatter unbounded. One
  hand-edited, synced or imported note claiming `recurrence: 999999999` would outrank the entire
  store forever: memory poisoning through arithmetic rather than through content, needing no
  injection at all. Now capped, far above any real note and far below anything that can dominate
  the ranking.
- **Every failure this project has actually had now has a test that reproduces it - and
  writing them found a live concurrency bug.**

  `nevertwice-doctor` proves the diagnostic *detects* three historical failures from fixtures.
  `tests/_test_properties.py` is the other half: it **recreates the condition** for each of the
  four incident classes - the graph generator that died on import while its wrapper logged
  success for a month, extraction stalling behind an unreachable backend, an embedding cache
  built by one model and queried by another, and test code writing to the owner's real vault -
  and requires the system to surface it. Plus the properties E1 lists: arbitrary Unicode and
  YAML in a note header, truncated state and two-generation recovery, concurrent writers,
  symlinks *and Windows junctions*, JSON-RPC fuzzing, clock jumps, and duplicate or reordered
  events replaying idempotently.

### Fixed
- **Concurrent writers inside one process raced on the same temporary file.**
  `write_atomic` put the **pid** in the temp name, which separates concurrent hook *processes*
  and does nothing for threads - they share a pid. Eight threads writing one state file all
  raced on a single `.tmp`: on Windows the second `os.replace` failed with `WinError 32`, and
  on POSIX it would silently publish whichever thread wrote last, which is the worse outcome
  because nothing reports it. The temp name now carries the thread id as well.

  `os.replace` also needed a bounded retry. Windows fails a rename with a sharing violation
  while another handle to the target is briefly open - exactly what two threads replacing one
  state file do to each other - and the failure is transient by nature. It now retries for up
  to two seconds with backoff, so a spurious crash becomes a wait, while a genuinely locked
  file still fails rather than hanging the hook forever. Both halves have their own mutation.

  Found by GOAL E1's "concurrent writers" item, which is the entire argument for writing the
  property suite rather than assuming the atomic-write path was safe because it said "atomic".
- **Store versioning, a migration planner, and a reproducible rebuild.**

  A store that does not say which layout it is in cannot be migrated safely - every future
  change has to guess from the shape of what it finds, and guessing wrong on someone's memory
  is not a recoverable mistake. `nevertwice-doctor` has been asking for
  `.nevertwice_schema.json` since task D1 and telling people to come back when the planner
  landed. `nevertwice/store_version.py` is the planner.

  `--migrate` plans by default and writes nothing. `--apply` takes a **backup before the first
  write**, runs the steps, validates the result, stamps the version, and prints the rollback.
  **The Markdown is never modified** - migration touches derived artifacts and state files only,
  which is exactly what makes rollback cheap: the expensive half was never at risk. A 2.2-era
  store migrates forward with its pre-D4 guard counters carried across rather than reset.

  `--rebuild` reconstructs every derived artifact from the notes, and two rebuilds of the same
  store produce a **byte-identical index**. The reason is measured, not assumed: the index is
  *removed* before it is rebuilt, and a build into a fresh file is deterministic.

  Two claims in the first draft of that paragraph were wrong in opposite directions, and both
  were caught by something trying to break them. It first credited `VACUUM` with the
  determinism - a mutation removing `VACUUM` left the suite green. It then claimed `VACUUM`
  restored byte-equality for an in-place build - masking SQLite's write counters still left 627
  differing bytes, the schema cookie and the FTS index's internal segment layout. So `VACUUM` is
  documented as compaction, `content_digest()` answers "is this the same index" across that
  path, and byte-equality is claimed only for the fresh-build path, where it holds.

  The embedding cache is the deliberate exception: it is **not** rebuilt without
  `--include-embeddings`, because recreating it needs a model and deleting it on a machine
  without one destroys work that cannot be recovered.

  `tests/_test_store_version.py` → 52 checks. The byte-identity assertion runs the clone's
  rebuild in a subprocess, because `m.VAULT` resolves at import time - the first attempt
  reloaded the module mid-test, reported success, and had written the index back into the
  original store.
- **A budget policy, and abstention that is a decision rather than a side effect.**

  The payload has always had a cap: sections are added by priority until the character budget
  runs out, and the rest is dropped. That is truncation, and it has two properties worth naming.
  It **cannot refuse something that fits** - a worthless lesson gets injected whenever there
  happens to be room - and it **cannot say why** anything was dropped, because nothing decided
  to; the string simply ended.

  `nevertwice/budget.py` replaces "does it fit?" with "is it worth it?". A `Policy` carries
  per-turn and per-session token *and* latency caps plus `min_value`, an expected-value
  threshold. Every call returns a `Decision` with a `reason` from a closed set - on spends as
  well as refusals - so "why did memory go quiet in this session" is a question you can count
  the answers to rather than guess at.

  **Value is checked before affordability, deliberately.** Checking budgets first would make a
  low-value item look acceptable right up until the budget filled - the same item taken or
  refused depending on what preceded it rather than on what it is worth. That is truncation
  wearing a policy's clothes.

  Two behaviours no length-based mechanism can produce, and both are asserted: an item is
  refused **while the budget is nearly untouched**, and two items of **identical token cost**
  get opposite decisions when their values differ, in either offering order.

  Wired into `guards_check(..., budget=..., policy=...)`, opt-in, with the default path
  byte-identical to before. A **blocking guard is exempt from the value threshold** - refusing
  to mention a hard stop to save tokens would be the budget overruling safety - and still
  consumes budget, with the exemption stated on the decision rather than applied silently.

  **`avoided` is never invented.** It is caller-supplied, requires an attribution that is stored
  with it, and `net` is labelled an estimate everywhere it appears. A zero means nothing was
  claimed, not that nothing was saved. `receipt.py` learned this the hard way once already; task
  B8 withdrew 120 claims over the same principle.

  `tests/_test_budget.py` → 52 checks and eight mutations, each red.
- **Five extension protocols, and the promise that filling one costs nothing but one file.**

  Every extension point in this system meant importing `memory_hook`: 6,000 lines that resolve
  a vault path at import time, read config, and pull in half the engine. So "swap the store for
  Postgres" or "add my agent as an episode source" was not an afternoon's work, it was a
  decision to depend on the whole project.

  `nevertwice/protocols.py` declares `MemoryStore`, `Retriever`, `Extractor`, `EpisodeSource`
  and `InterventionSink`, and **imports nothing from the engine** - only `typing` and the
  dependency-free `schemas`. A registry plugs implementations in, and refuses to shadow an
  existing provider unless you say `replace=True`, because two plugins both believing they are
  the store is a thing the loser finds out about in production.

  `conforms(obj, "MemoryStore")` returns the list of what does not fit, not a bare False,
  because "your class does not fit" is not an error message anyone can act on. It exists
  because `isinstance()` against a `runtime_checkable` Protocol is weaker than it looks -
  verified, not assumed: a method with the wrong signature passes it, and so does a non-callable
  attribute of the right name. Both fail at the first call instead.

  **The exit criterion is proven in a subprocess**, because in-process it would prove nothing -
  the suite imports the engine, so `memory_hook` is already loaded and every assertion would
  pass for free. A third-party dict store and a Slack episode source are written to a temp file
  that imports `protocols` alone, registered, driven end to end, and the child then asserts that
  none of `memory_hook`, `api`, `config`, `guards` or `hosts` ever reached `sys.modules`.

  The shipped engine is held to its own surface: all four host adapters are checked against
  `EpisodeSource`, and the shipped search against `Retriever`. A plugin surface that no shipped
  component fits is a description of an intention.

  `tests/_test_protocols.py` → 47 checks and seven mutations, each red - including one that
  survived the first draft: dropping a member from the contract let every half-implementation
  start "conforming" to a weaker protocol, silently. `REQUIRED` is now tied to the methods each
  Protocol actually declares.
- **Migrations in: five sources, provenance kept, and a way back out.**

  `import_memory.py` already parsed four sources. The two things it did not do matter more than
  the parsing. It forgot where each note came from - so an imported claim arrived looking like
  something this store had worked out for itself - and once written there was no undo, which
  makes a migration a decision you must be certain about in advance rather than one you can try.

  `nevertwice/migrate.py` and `nevertwice-migrate` bring in **Claude auto-memory**, a
  **claude-mem SQLite export**, a **Mem0 JSON export**, a **Letta MemFS archive**, and **generic
  Markdown/JSONL**. Every note keeps `imported_from`, `source_author`, `source_created`,
  `source_ref` and `import_batch` in its own frontmatter - in the note, so the answer survives a
  clone, `git log`, and this module being deleted.

  An unknown timestamp stays **empty**. Defaulting it to today would quietly claim the memory
  was made during the import, which is the one thing provenance exists to prevent. `--dry-run`
  reports the counts, the type breakdown and the *provenance gaps* - how many records arrived
  with no author or no date - because an import that loses most of its provenance is something
  to know about before it lands.

  Every import records a batch. `--revert` undoes exactly that batch, is a dry run unless you
  pass `--apply`, and re-checks each note's **own** `import_batch` stamp before removing it: the
  ledger records what was written, the note records what it is now, and deleting from someone's
  memory on the strength of a stale index entry is the failure worth engineering against. A note
  edited into another batch, superseded, or already gone is skipped with the reason.

  Re-importing the same export **converges rather than duplicating** - the shared write path
  derives a stem from title and date, so the second run rewrites the same notes and re-stamps
  them. That makes a re-run after a partial import safe, and it is exactly the case where a
  ledger-trusting revert would delete notes a later batch had claimed.

  `tests/_test_migrate.py` round-trips all five sources from recorded fixtures - import, verify
  the origin in the file, revert, compare the store to its baseline - with no account, no
  network and nobody's real memory: 148 checks and ten mutations, each red.
- **One host-adapter contract, four adapters, and fixtures instead of accounts.**

  Supporting an agent used to mean editing three modules that did not know about each other:
  discovery was a hardcoded registry in `watch.known_targets()`, normalisation was a heuristic
  in `ingest` that sniffed the payload shape, cursoring was a watermark dict. Install knew only
  about Claude Code, there was no way to ask whether a host was wired, and no way to undo it.

  `nevertwice/hosts.py` states the five answers once - `discover()`, `read(cursor)`,
  `normalize(raw)`, `install_status()`, `uninstall()` - and ships four: **Claude Code**,
  **Codex**, **Cursor** and a **generic JSONL** fallback. `nevertwice-hosts` prints the lot.

  Normalised events are `schemas.EpisodeEvent`, the D2 boundary, so "normalised" means one
  declared shape rather than four plausible dicts. `tests/fixtures/hosts/` holds the *same
  conversation* in four on-disk shapes, and the suite asserts the four adapters agree on it -
  which is the difference between a contract and four parsers sharing a docstring. **No account
  with any agent is needed to add an adapter or prove it works.**

  Things the suite pins because they are easy to get wrong:
  - Codex's `session_meta` line is scaffolding, not content. The fixture carries a ~10KB one,
    because treating it as flat text consumed the whole truncation budget and mined zero content
    on a real 57MB corpus - and the `cwd` is still taken from it.
  - Claude Code is captured by hooks and must **not** also be swept; sweeping both mines every
    session twice.
  - Uninstall removes only the entries this package wrote, keeps a backup, and a dry run really
    writes nothing. The easy implementation rewrites `settings.json` and eats every hook the
    user configured themselves.
  - A **hand-rolled flat copy** of the engine - the shape this project's own author runs under
    `~/.claude/scripts` - is detected, reported, and never repointed or removed. The marker is
    the `nevertwice/memory_hook.py` *path suffix*, matching `install.py` exactly, so status,
    install and uninstall share one definition of "ours". Before this, the status check used a
    looser marker and told the author to install over their own deployment.
  - Cursor cannot be swept at all (`state.vscdb` is SQLite), so it reports why and names the two
    ways out. An adapter that quietly returns nothing looks identical to one that works.
  - A truncated final line costs one turn, not the whole session.

  `tests/_test_hosts.py` → 117 checks, twelve mutations, each red.
- **`nevertwice-inbox`: one screen for everything the memory is asserting on your behalf.**

  Guards promote and retire themselves, contradictions resolve at write time, and until now the
  person whose repository it is had no seat at the table until something fired at a bad moment.
  The inbox is that seat: guards by status - blocking, advisory, retired - each with what it has
  actually *earned* (precision and override rate with intervals, never how often it fired);
  unresolved contradictions; and two kinds of stale fact, both actionable - a guard whose source
  note has left the live store, so its evidence can no longer be read, and a live note nobody has
  re-confirmed in months that never recurred.

  Five actions - approve, edit, override, retire, confirm - plus trace-to-source, on the CLI and
  through `api.inbox()` / `api.inbox_action()`.

  **Every action round-trips into the store and shows up in `git diff`.** Guard actions rewrite
  `guards.json`; `confirm` splices a `reviewed:` line into the note's own Markdown frontmatter
  rather than into a side-car. Each action returns the paths it wrote and the CLI prints them, so
  the round-trip is something you can check instead of something you are told. The suite asserts
  it per action against a real git repository.

  **An operator's opinion is recorded as an operator's opinion.** `approve` records one honest
  `accepted` outcome from one session and deliberately does *not* promote - promotion needs K
  distinct sessions, and one person approving is one person. `--promote` and `retire` do force
  the status, stamped `promoted_by` / `retired_by: operator` with a reason. Corrupting the
  evidence channel to express an opinion is how a feedback loop stops meaning anything, so the
  two channels stay separate: forcing a status never manufactures a distinct session. Even the
  operator cannot promote a cold-start pack guard, which stays advisory by design.

  `tests/_test_inbox.py` → 61 checks and eight mutations, each red - including `confirm` writing
  to a side-car instead of the note, an action that stops reporting what it wrote, and approve
  quietly minting a fresh session id per call so repeated clicks would promote.
- **The feedback loop that can actually falsify a guard.**

  A memory that warns you is easy to build and impossible to trust; everything rests on the
  loop deciding which warnings keep the right to interrupt you. `nevertwice/outcomes.py` owns
  that vocabulary and its arithmetic, under one rule that makes it non-circular: **firing is
  not evidence of success.** A guard that fires a thousand times has proved only that its regex
  matches, so `fired` never reaches precision, the intervals or the lifecycle.

  Five outcomes, because three were hiding a distinction that matters. `prevented_failure` and
  `accepted` are evidence for; `overridden` and `false_positive` are evidence against and answer
  **different questions** - burden versus correctness - so they are counted and reported apart;
  `unknown` is recorded and counts for nothing, which is the point of naming it. The old
  vocabulary collapsed override into false positive (its docstring said so outright), and under
  that reading a guard that was *right but annoying* retired for being right.

  **Falsification is no longer easier than confirmation.** Promotion deduped by session while
  demotion counted every call, so one frustrated session could retire a guard it could not have
  promoted - and a caller passing no session id could promote one by repeating itself K times.
  Both directions count distinct sessions now, and an unattributed outcome moves the rates but
  no threshold.

  Precision and override rate are published with **Wilson** intervals - `_confidence` claimed to
  be "Wilson-ish" and was Laplace - because these samples are tiny and at the boundary, where
  the normal approximation reports 3-of-3 as exactly [1.0, 1.0]. They reach every surface
  through `why_fired`: `api.guard_outcomes()`, `nevertwice-guards list`, the MCP feedback tool's
  reply, and the dashboard, whose guard column is now what the guard *earned* rather than how
  often it fired.

  `tests/_test_outcomes.py` drives the real ledger: 65 checks and eight mutations, each red.

  **The bug its exit criterion caught.** A demotion cleared the *opposing* sessions but left the
  corroborations that had earned the lost rung standing, so the next override immediately
  re-promoted the guard: it oscillated advisory-blocking forever and could never retire. A
  demotion consumes the evidence on both sides now - the guard proved it did not deserve that
  rung, so it re-earns promotion from zero - and `demotions` is counted so the history stays
  legible.
- **The demo's headline ratio depended on whether Ollama happened to be running, and the
  manifest called both answers `stdlib_only`.**

  `examples/scenario_demo.py` uses a local bge-m3 through Ollama when one is up and falls back
  to lexical search when it is not - the source says so on line 146 - so the same command
  publishes **5.9x** on one machine and **9.1x** on another. The manifest recorded both under
  one environment that claimed neither model nor network, which is how a number stops being
  falsifiable: a reader who cannot reproduce it has no way to tell whether the claim is wrong
  or their stand is different.

  Two environments are now declared. The no-embedder ratio - what a fresh clone and CI get - is
  published; the semantic-path ratio is withdrawn as gated on a running local model. That also
  **corrects a B8 withdrawal**: 9.1x was withdrawn as "no invocation reproduces this value at
  HEAD", and it reproduces exactly, on the path CI takes.

- **`cited_in` is now checked, not asserted.** The manifest's coverage check runs one way -
  every number in a document resolves to a claim - so it could not see a claim that believed it
  was printed somewhere it was not. The B8 README rewrite left five such claims behind. Both
  directions are checked now, and the stale citations are corrected.
- **`why_fired`: one object, four surfaces, and a test that makes them agree.**

  A guard that fires interrupts the agent, so the interruption has to carry its own
  justification. Four surfaces answered that differently: the Python API returned
  `{id, status, message, scope}`, the CLI printed one line, the MCP tool printed a slightly
  different line, and the dashboard did not mention guards at all. Nothing forced them to agree,
  so "why did this fire?" had four answers depending on where you asked.

  `nevertwice/why_fired.py` builds the answer once - the matched span, the recorded mistake the
  guard was distilled from and how often that failure recurred, confidence and what it is an
  estimate of, the guard's age, the policy that made it a warning rather than a block and exactly
  what would change that, and the token arithmetic behind the zero-token claim: nothing until it
  matched, one message now, against what reading the source notes would have cost.

  All four surfaces render *that*: `api.guards_check(..., explain=True)` and `api.why_fired()`,
  `nevertwice-guards check --why` (and `--json`), `"explain": true` on the MCP
  `memory_guard_check` tool, and a new guard table in the dashboard. The CLI and MCP share one
  formatter, because two formatters over one object is how two surfaces start disagreeing.

  **Where a signal does not apply, it says so.** A guard fires on a regex, so it has no lexical
  or semantic contribution and no ranked candidate set; those fields carry `None` and a reason
  rather than `0.0`, which would be a lie shaped like a measurement. The causal-graph walk is
  behind `deep=True` because it reads the whole store.

  **The hot path is untouched.** `guards.check()` is still a regex-and-scope match returning
  exactly `{id, status, message, scope}`, and explaining is opt-in - the zero-token argument
  rests on that, and a test asserts the default result is unchanged.

  `nevertwice/schemas.py` gains `WhyFired`, its eighth declared boundary.
  `tests/_test_why_fired.py` drives the real engine on a throwaway store: 47 checks, and seven
  mutations - MCP formatting its own line, the dashboard dropping the section, a signal reporting
  0.0, the explanation leaking onto the default path, a faked span, a sourceless guard going
  quiet about it, a pack guard claiming it can be promoted - each turns it red.

  Two defects it caught while being written: the object emitted `last_fired: None`, which is a
  string that is null rather than an absent key, and the dashboard filtered guards on the
  digest's *display* string (`"(all)"`) instead of the project argument, which silently emptied
  the table on a whole-store dashboard.
- **Every published number now names the commit whose code produced it - and most of them
  turned out to be describing an engine that no longer exists.**

  All 133 claims in `research/evidence_manifest.json` carried the same commit, `05cfdc96`. That
  was the commit that last touched the artifact *file*, and it was a directory move. It recorded
  when a JSON file was relocated, not what the code did when the number was measured. Tasks B1
  and B2 had closed drift between the documents and the artifacts; this half - drift between the
  artifacts and the code - was wide open, and the whole published corpus was sitting in it.

  `tools/produced_by.py` resolves each claim's `command` to the repository files it imports,
  transitively, including the deferred `_sibling("name")` loader that is the *only* path to
  `nevertwice/rankers.py` - the ranker the retrieval numbers measure appears in no import
  statement at all. `tools/check_freshness.py` then fails when the last commit to touch any of
  those files is not an ancestor of the claim's commit, and runs in CI. On first run it failed
  on **all 133 claims**.

  **Regenerated at HEAD, and the deltas, including every place the current engine scores worse:**

  | claim | published | at HEAD |
  |---|---|---|
  | poisoning, acceptance attacks blocked | 88% | **81%** |
  | poisoning, plausible-false facts blocked | 50% | **25%** |
  | poisoning, quarantine precision | 0.91 | **0.90** |
  | poisoning, quarantine recall | 0.83 | **0.75** |
  | PreToolUse end-to-end | 85 ms | **98 ms** |
  | UserPromptSubmit end-to-end | 68 ms | **84 ms** |
  | SessionStart end-to-end, idle | 73 ms | **84 ms** |
  | cold import of the engine | 25 ms | **26 ms** |
  | longitudinal bench, hybrid RRF R@1 | 0.766 | **0.760** |
  | longitudinal bench, shipped ranker R@1 | 0.656 | **0.648** |
  | active guards vs always-injecting | 31× | 31× - unchanged |
  | forgetting, coverage gain at a 20% budget | 0.14 | 0.14 - unchanged |

  Not one number improved. The poisoning defence is materially weaker than the artifact claimed:
  the false-fact family, already this project's stated open problem, is now a quarter defended
  rather than half. `research/POISONING.md` says so where the old table stood.

  **Withdrawn: 120 of 133 claims.** They cannot be re-measured here - LongMemEval-oracle is
  third-party and uncommitted with no recorded content hash, the internal tasks were measured on
  the owner's private vault, and the live-validation and QA numbers need a paid frontier API.
  Rather than reprint a figure the current code does not produce, each is marked `stale` with its
  reason and removed from every governed document, where a rendered notice names the gate and the
  command that would restore it. `python tools/check_freshness.py --list-stale` lists them all.
  The README's headline retrieval comparison and its −86% repeat-error figure are among them.

  **Two defects surfaced by the re-measurement**, both recorded rather than worked around:
  `research/latency_bench.py` seeded its store in a subprocess while measuring in-process against
  the store pinned at import, so its `guards.check()` and lexical-recall rows had been measuring
  an *empty* store - the label now prints the real count, and both claims are withdrawn. And a
  single invocation of that bench was never a cost: three consecutive runs on one commit gave 142,
  185 and 112 ms for the same hot path, so it now repeats the whole measurement and reports the
  minimum with the median and maximum beside it in `research/latency_bench.json` - the first
  committed artifact those badge numbers have ever had.

  Withdrawal is enforced, not declared: a withdrawn claim contributes no accounted number, so any
  document still printing one fails `tests/_test_evidence_manifest.py`, and `tests/_test_freshness.py`
  turns red if a claim is stamped before the newest code in its own closure, if a closure loses the
  deferred-import pass, or if a claim is marked stale while still being cited.
- **The seven boundaries are written down, and checked against what the code passes.**
  Every value crossing a seam here is a plain `dict`, and every reader is defensive about it -
  `n.get("desc", "")`, `(params or {}).get(...)`. That is not paranoia, it is the absence of a
  contract: each new reader re-derives the shape, guesses one key wrong, and adds another
  `.get` with a default that hides the mistake. `nevertwice/schemas.py` declares the episode
  event, the frontmatter, the note meta, the retrieval hit, the intervention, the JSON state
  file and the MCP request, with a small structural `conforms()` and no third-party
  dependency.

  It changes no behaviour: readers convert one module at a time. What lands now is the
  answer, so the next reader can look it up. `tests/_test_schemas.py` drives the real engine
  on a throwaway store and requires every value that comes back to conform, which is what
  keeps the file describing production instead of intent - and it pins the one undocumented
  rename that has caused bugs, `NoteMeta.desc` becoming `RetrievalHit.description` at the
  public boundary.
- **`nevertwice-doctor`: what is wrong with this install, and the safe way to fix it.**
  Every production failure this project has had was silent. The graph generator died on
  *import* with a `NameError` for over a month while the fire-and-forget wrapper around it
  logged "graph.json refreshed" on every crashed run; extraction stalled behind an unreachable
  backend with nothing to show for it but a store that stopped growing; and an embedding cache
  built by one model, queried by another, made retrieval abstain - correct behaviour, and
  indistinguishable from an empty store.

  `nevertwice/doctor.py` asks the eleven questions that would have made those three visible:
  store writability and schema stamp, hook registration, capture freshness, the selected
  extractor, the embedding space against the cache, index age, the background sweep's
  heartbeat, whether the graph generator imports, orphaned temporary files, and which copy of
  the package is actually running. Each answer carries a repair, **printed rather than
  executed**. `--json` is schema-stable - fixed keys, a fixed check order, a declared
  `schema_version` - and only `--probe` touches the network.

  `tests/_test_doctor.py` builds all three historical failures as fixtures and requires the
  doctor to catch each one; six mutations were walked through, including one that quietly
  drops a check from the report and one that turns a repair destructive.
- **One visual system, and no figure without its provenance.** Eleven benches each called
  `fig.savefig(path, dpi=130)` with default Matplotlib styling: a palette that is not
  colourblind-safe, several charts encoding pass/fail in green against red alone, raster only
  at 130 dpi, and no sample size, dataset, model or command anywhere on the image - so a chart
  that travelled past its page arrived with no way back to what produced it.

  `research/_figstyle.py` is now the only place a figure is written. It applies the Okabe-Ito
  palette, writes **SVG beside a 2x PNG**, and **refuses to save a figure that carries no
  evidence line** - either a manifest claim id, which renders the same footer
  `tools/render_claims.py --footer` produces, or a written line naming the dataset and the
  command. All eleven figures were re-rendered through it.
- **Two generated diagrams.** `tools/make_diagrams.py` emits `docs/architecture.svg` - write
  time and read time as separate lanes, with the stages that cost nothing in context and the
  stages that never leave the machine marked on the stage rather than left to the prose, and
  the intervention point called out once - and `docs/never-twice.svg`, the four beats
  `examples/guard_demo.py --check` prints. Both carry a `<title>` and a `<desc>` that describe
  the whole diagram in words, and CI fails if either drifts from the description that produced
  it.
- **A stranger can wire their agent up without asking.** `docs/AGENT_CONFIGS.md` carries the
  block to paste for Claude Code, Cursor, Codex CLI, Claude Desktop and Zed, plus how to tell
  whether it took - the failure mode being a host that shows no error, just no tools.
  `tests/_test_discoverability.py` parses every JSON and TOML block and checks that every
  command they name is a console script this package actually installs, so a renamed entry
  point breaks the build instead of someone's setup.
- **Three starter issues, described in full before they are filed.**
  `docs/starter-issues/` holds a small mechanical one (make `latency_bench.py` save its
  results, which is why those figures still carry a `raw_gap` in the manifest), one that needs
  a model and judgement (run the `AGENTS.md` baseline the repeat-error headline has never been
  compared against), and one a stranger notices first (the structural labels inside every note
  are Russian whatever language you write in). Each names the files to start from, and the
  suite fails if one of those files stops existing. Filing them is a maintainer action.
- **`CITATION.cff` joined the version contract.** It declared no version at all, so a
  bibliography entry or a Zenodo record could quote whatever was true when it was written.
  `tools/check_version.py` now checks it alongside `pyproject.toml`, the runtime
  `__version__` and the MCP server, and CI runs it on every push.
- **Release automation.** `.github/workflows/release.yml` is tag-triggered and builds the
  sdist and wheel exactly once; every later job downloads that artifact instead of
  rebuilding, so the bytes that are verified are the bytes that get published. Verification
  installs each artifact into an empty virtualenv on Linux, Windows and macOS, checks the
  installed version against the built one, re-checks the checksums, and proves every console
  entry point resolves to a callable. The build also emits a CycloneDX SBOM
  (`tools/make_sbom.py`, generated from the wheel's own metadata) and `SHA256SUMS`, and a
  tagged run attests build provenance so a downloaded artifact can be verified with
  `gh attestation verify`.

  Publishing is reachable only from a pushed tag: `workflow_dispatch` is a dry run that
  produces every artifact and publishes nothing, and a **pre-release tag runs the whole
  path and stops before PyPI**. The PyPI upload uses Trusted Publishing, so no API token
  exists anywhere, and it waits on a `pypi` environment that only the repository owner can
  create. `tests/_test_release_workflow.py` fails if a publishing gate is removed, if a
  second job starts building, or if an action stops being pinned to a commit sha.
- **Baseline gates are written policy, and machine-checked.** `research/BASELINES.md`
  names the six baselines a headline has to clear - no memory, full-history injection,
  lexical recall, a curated `AGENTS.md`, an LLM session summary, and the relevant linter
  or test - plus a seventh this project already runs (an already-curated small haystack).
  The manifest carries a verdict for every headline against every baseline, and
  `tests/_test_baselines.py` fails if a headline skips one, if `not_compared` or
  `not_applicable` is asserted without an argument, or if a `beats` verdict cannot name
  the raw file the comparison lives in.

  The current matrix is 6 beats, 1 loses, 19 not compared, 16 not applicable. Three
  things it makes visible: the repeat-error headline has never been run against a
  hand-written `AGENTS.md` carrying the same one-line rule, nor against the linter that
  already catches several of those pitfalls; and the live two-arm token claim **fails**
  its gate, because input tokens and answer accuracy fell together and the policy
  requires matched accuracy.
- **The comparison document is generated and dated.** `tools/comparison_snapshot.py`
  pulls repository activity - stars, forks, last push, archived, licence - from the GitHub
  API into a committed `docs/comparison_snapshot.json`, then renders four tables in
  `docs/COMPARISON.md`: what each vendor **documents** (with a source link per row, at a
  stated survey date), repository activity, what could actually be **run here**, and the
  retrieval recall from that one run. Vendor claim and measurement are now separate tables
  rather than one matrix that mixed them. `--fetch` is the only mode that touches the
  network and is never run in CI; checking and rendering are offline and byte-stable, and
  CI fails if the document drifts from the data.
- Registered eight more head-to-head claims (R@10 and MRR for each system) so the
  comparison's retrieval table comes from the evidence manifest, like the README's.
- **The published tables are generated, not typed.** `tools/render_claims.py` renders the
  ten benchmark tables in the README and `docs/BENCHMARKS.md` from the evidence manifest
  into marked regions, and CI re-renders and fails on any difference - so a table cannot
  drift from the result it reports. It also emits chart evidence footers
  (`--footer <claim-id>`: n, dataset, model, interval, commit, repro command) for the
  figures. Table emphasis now follows a stated rule - the best value in each column -
  instead of hand-applied bold that outlives the number it was highlighting.
- **Every tracked Markdown file has a governance mode.** The manifest's document register
  marks each one governed (every number must resolve to a claim), exempt with a stated
  reason, or backlog with a numeric budget that may only be lowered. A new document
  cannot quietly start publishing unevidenced numbers, and the 1,352 numbers still
  unregistered across 23 study write-ups can only shrink.
- **Every published number now resolves to its evidence.**
  `research/evidence_manifest.json` registers all 133 figures printed in the README and
  `docs/BENCHMARKS.md` with the dataset, sample size, model, hardware, exact command, raw
  result file and pointer, confidence interval where one applies, and the caveat that
  belongs with the number. `tests/_test_evidence_manifest.py` fails if a printed number
  has no entry, if an entry disagrees with the raw file it points at, or if a claim
  without a committed artifact does not say so. Two things the register makes visible
  instead of hiding: 45 claims have no committed raw artifact (`eval_harness.py` saves
  into the user's vault, `latency_bench.py` saves nothing), and five published figures that
  disagreed with the stored results - all corrected below.
- **The version contract is enforced, not documented.** `tools/check_version.py` checks
  that `pyproject.toml`, the runtime `__version__`, the MCP server's reported version, the
  git tag and the built distribution's metadata all carry the same version - and that the
  metadata still declares the SPDX `License-Expression`. CI runs it on every push, before
  and after building, so a release cannot be cut on a mismatched tag or a stale wheel.
- A `packaging` CI job installs `.[dev]` and runs `python -m pytest -q` - the exact path
  the README and CONTRIBUTING tell a contributor to follow.

### Changed
- **`SECURITY.md` no longer quotes a version.** It said Nevertwice was "pre-1.0 in spirit even
  at v1.0.0" while the project shipped 2.3.0 - a support statement that had been wrong for two
  releases. It now says what is actually true (the default branch, best effort, one
  maintainer) and tells you how to find the version you are running; a test fails if a release
  number reappears in it.
- **The docs map is a task map, and nothing is more than two clicks away.** `docs/README.md`
  was an alphabetical file listing; it now answers *what do you want to do* in six lanes -
  install, integrate, operate, understand, reproduce, contribute. Six documents were reachable
  from nowhere at all (`CODE_OF_CONDUCT.md`, `examples/README.md`, and the `CAUSAL_VOCAB`,
  `EMBED_SPECIALIZE`, `QUANTIZATION` and `TWIN_GATE` studies) and `CHANGELOG.md` sat at three
  clicks. The four orphaned studies are now listed in `research/README.md`, the rest in the map,
  and the sample store links the four fixture notes so a reader can see the on-disk format
  without installing anything.

  `tests/_test_docs_map.py` walks the relative-link graph from the README and fails when any
  tracked document is unreachable or deeper than two clicks, when a lane disappears or empties,
  or when a link points at a file that does not exist. The only exclusions are
  `research/embed_universal/data/`, which vendors cloned third-party repositories, and
  `.github/` templates, which GitHub surfaces itself.
- **The README is a funnel, not an encyclopedia.** It was 468 lines, and the acquisition story -
  what this is, proof that it works, how to install it - was interleaved with per-agent setup, the
  full benchmark commentary, the import recipes and the feature inventory, all of which already had
  homes under `docs/`. It is now 180 lines, with the whole acquisition story in the first 120:
  banner, one sentence, the install command, the four-beat guard transcript from
  `examples/guard_demo.py`, the differentiator, three evidence rows, the head-to-head table and one
  architecture diagram.

  Depth moved rather than disappeared. `docs/FEATURES.md` is new and holds what shipped and had
  nowhere else to live - reading the store with `digest` and the offline dashboard, bi-temporal
  queries, supersession, the `AGENTS.md`/OKF export, the bootstrapper, the opt-in Brain layer, and
  the mechanisms measured and cut. The vendor table was already in `docs/COMPARISON.md` and the
  fusion commentary already in `docs/BENCHMARKS.md`, so both were duplicates and are gone from the
  README; the `longmem-readme` generated region duplicated `longmem-benchmarks` at two decimal
  places instead of three, so that renderer is retired.

  `tests/_test_readme_funnel.py` enforces the budget, and - the part that makes a line limit safe -
  asserts for every relocated topic that it exists in the document the README now points at, so a
  future shortening cannot pass by deleting what the page documented.
- **The roadmap says what already exists.** Three entries had gone stale. The
  tagged-release workflow they promised now ships, so the entry is reduced to the part
  that is genuinely blocked - a `pypi` environment and a Trusted Publishing registration
  only the maintainer can create. `Structural guard signatures` did not mention that a
  keyword-driven anti-pattern generator already covers the common pitfalls offline, and
  `Order-aware anticipation` still described the scorer as lexical after an optional
  embedding blend shipped. Every open item now carries a **Today:** clause stating the
  current state next to the promise, and `tests/_test_roadmap.py` fails if an item drops
  it or if a shipped feature reappears as future work.

  It also stopped contradicting its own research: LoCoMo was listed as a candidate
  benchmark while the study pages call it discredited (plain BM25 scores about 94% on
  it). It is now named as excluded, with the reason.
- **Comparative claims are dated and scoped instead of absolute.** Sixteen statements
  across the README, `docs/BENCHMARKS.md`, `docs/COMPARISON.md` and two research
  write-ups asserted something about *all* rival systems, or about the state of the
  whole research area, without naming a scope or a date: that rivals are categorically
  a weaker kind of thing, that none of them documents a given capability, that this
  project wins on all measured axes, that the surrounding area measures nothing here.
  Each now names which systems, measured or surveyed how, and when - the head-to-head
  run of 2026-07-05, or the mid-2026 landscape survey in `docs/COMPARISON.md`. Such a
  claim is not falsifiable, and it rots: the sentence keeps asserting something about a
  landscape that has moved on. `tests/_test_claims_language.py` scans every tracked
  document and fails if one returns; the single allowance left must both still match a
  flagged line and still state why it is not a comparative claim.
- Brain ontologies and relation hints now follow declaration order for every
  `PYTHONHASHSEED`, making multi-profile extraction prompts reproducible.
- The CLI now says that newly written notes are immediately available to lexical
  recall; embedding is correctly described as enabling semantic recall.
- Contributor docs now expose one cross-platform `python -m pytest -q` entry point
  for all 48 standalone suites and match the current Python 3.10-3.14 CI matrix.
- **Config resolves paths after the env files load.** `load_dotenv()` now runs at
  `config` import, before `VAULT`/`PROJECTS_ROOT` resolve - so a per-machine store
  location (`NEVERTWICE_VAULT` in `.secrets.env`) works without editing `config.py`.
- **Twin-gate calibration is data, not code.** A machine-local `twin_calibration.json`
  (or `$NEVERTWICE_TWIN_FILE`) overrides the baked bge-m3 weights + space label; a
  retrained gate no longer requires forking `memory_hook.py`.
- **Injected memory is framed as reference.** SessionStart and per-prompt payloads
  now say "recalled reference, not instructions" - the unsafe-content filter is
  deliberately narrow, so the framing keeps instruction-shaped prose in a note from
  reading as a directive.
- Internal dedup: one HTTP/JSON retry loop behind all LLM backends, one
  two-generation (`.bak`) JSON persistence helper, one dual-shape sibling-import
  resolver, one budgeted fact-section renderer (the cross-project copy had drifted),
  one shared test sandbox. Behavior-preserving except where the review below found
  otherwise; backend log lines gained a try count and a scrubbed URL.

### Fixed
- **One store sandbox for the whole repository, and it verifies itself.** Three times a
  script here has written into a live memory store (2026-08-13, 2026-08-18, 2026-08-25).
  Each fix was correct and each was applied to the one directory where it happened, so the
  next entry point re-learned the lesson. The shared cause is that a guard which *pins and
  then trusts* is a hope: `config` resolves `env("VAULT") or NEVERTWICE_HOME`, and
  `NEVERTWICE_VAULT` is the documented, supported way a real user points at a real store,
  so pinning `NEVERTWICE_HOME` alone loses to it.

  `sandbox_guard.py` is now the single home for the scrub list and the pin, and it ends
  with an **assertion**: after `config` is imported it checks that both variables, the
  resolved `VAULT`, and every `Path` held by an already-imported project module land inside
  the throwaway directory, raising `SandboxEscape` instead of writing. `tests/_env_guard.py`
  and `examples/_sandbox.py` are two-line shims over it and hold no policy of their own,
  which is what had let them drift apart. Scripts that genuinely need a populated store say
  so with `sandbox_guard.allow_live(reason)`, which names the store on stderr first and
  cannot un-isolate a caller that already sandboxed the process.

  Twenty-two `research/` entry points reached the store with no guard at all.
  `research/latency_bench.py` was the sharpest: it seeds 150 fabricated notes and 50
  fabricated guards, and built the child environment from `os.environ` with
  `NEVERTWICE_HOME` pinned and `NEVERTWICE_VAULT` inherited. Eleven now isolate, nine
  declare `allow_live`, and `research/forgetting.py` imported `consolidate_memory` one line
  before the sibling that armed it.

  `tools/check_sandbox.py` runs in CI and fails when a tracked script under `examples/`,
  `research/` or `tools/` imports a project module without arming first, when the arming
  call comes after that import, or when any file names `NEVERTWICE_HOME` without
  `NEVERTWICE_VAULT`. `tests/_test_sandbox_guard.py` runs **every** example with a hostile
  `NEVERTWICE_VAULT` exported at a stand-in store and asserts the stand-in is byte-identical
  afterwards - and then proves the check is load-bearing by rebuilding the 2026-08-25 guard
  from source: with the assertion it stops before writing, without it the note lands in the
  stand-in.
- **The comparison implied Letta had been benchmarked and blocked.** There is no
  `run_letta` adapter in `research/head_to_head.py`, so it was never put on the stand at
  all. The verified table now distinguishes *ran here*, *could not be run* (an adapter
  exists and recorded a blocker) and *not attempted* - a gap in the comparison rather
  than a finding about the system.
- **Five published figures disagreed with the stored results.** The distillation A/B table
  and its prose were from an older run than `research/token_ab.json` (30.7x compression
  from 968,715 -> 31,517; the stored result is **31.4x from 1,013,847 -> 32,263**, with
  every per-k row different). The live two-arm memory arm read 345 tokens and 0.33
  answer-match; the stored result is **348 and 0.267** - the document was understating its
  own honest accuracy caveat. The `~5-35x` state-conveyance range was a hand-rounding of
  one project's ratio; the measured figures are 34.6x, 46.6x and 115x. And the +0.14
  forgetting gain is measured against the **salience** baseline, not recency-sorting as the
  README said (against recency the gap is 0.096). All corrected from the raw results.
- **The head-to-head table now quotes one run.** Nevertwice's row was taken from the
  retrieval study while the competitors' rows came from the head-to-head run; both now come
  from `research/head_to_head.json`, which is the only thing that makes "the same stand"
  mean anything.
- **The research suites were outside the test sandbox.** All fourteen ran without
  `tests/_env_guard.py`, and five of them import `memory_hook` directly - so on a machine
  whose shell exports `NEVERTWICE_VAULT`, they baked live store paths into import-time
  constants: exactly the setup behind the 2026-08-13 and 2026-08-18 incidents. Every suite
  now arms the guard before its first project import, and `tests/_test_hermeticity.py`
  fails if one forgets or if a hostile environment survives the guard.
- **Project scanners no longer follow file symlinks.** `graphify` and the context
  bootstrapper skip linked files (and linked directories while collecting layout),
  preventing content outside the selected project from entering a graph or cloud prompt.
- **MCP notifications are silent as required by JSON-RPC.** `ping`, `tools/list`,
  `initialize` and `tools/call` only emit results for requests; malformed parameter
  objects return `-32602`, including requests whose explicit id is `null`.
- Optional LangChain, LlamaIndex and reranker install errors now name the real PyPI
  distribution, `nevertwice`, instead of the nonexistent `nevertwice-memory`.
- The shared test sandbox forces the optional cross-encoder off, so a developer's
  cached model cannot silently load Torch or a GPU during ordinary tests.
- Packaging metadata now uses an SPDX license expression and an explicit license
  file, removing setuptools' overdue license-table/classifier deprecations.
- **Context compaction could amputate the newest entries.** The compressed
  state-block + link-archive now fits the room actually left by the kept entries
  (archive links dropped oldest-first, then the state text trims). When even the
  kept tail crowds the cap, the oldest kept entries spill VERBATIM to
  `Context/Archive/<project>-overflow.md` and a single oversized entry is truncated
  with a marker pointing at its archived copy - the byte-cap guard no longer
  truncates the file tail, and the emergency (no-LLM) spill path now respects the
  cap too.
- **Compaction could re-fire forever.** The cap guard truncated to exactly
  `CONTEXT_MAX_BYTES` and then appended a newline, writing cap+1 bytes; since the
  entry gate is `<= cap`, every scheduled maintenance pass recompacted that project
  again, re-summarizing its own summary and burning an LLM call each time.
- **Corrupt state files could beat their own backups.** The shared two-generation
  loader decoded with `errors="replace"`, so a bit flip inside a `guards.json`
  pattern parsed as valid JSON with a U+FFFD in it: the guard silently stopped
  matching and the intact `.bak` was never read. Decoding is strict now, and a
  merely absent primary no longer logs a corruption that never happened.
- **Twin-gate calibration is bounds-checked**, and a `NEVERTWICE_TWIN_SPACE` that
  contradicts a calibration file's own `space` is refused with a warning. Either
  gap could silently retire correct notes: `sd = 1e-12` saturates the classifier at
  p = 1.0 for every prefilter survivor, and a stale space label re-enabled the gate
  for weights from a different embedder.
- **The injection budget bounds the payload again for long project names.** The
  "recalled reference" framing added 39 never-trimmed characters; header and footer
  now degrade before the budget is exceeded.
- A store that resolves to an empty directory while a populated one exists at a
  default location now says so in the log instead of returning nothing forever.

### Added
- **Learned twin-gate** (stage 0 of embedding specialization). The write-time dedup gate
  now scores candidates with a logistic classifier over five pair features, trained on
  labels mined from the memory's own lifecycle (supersede pairs + slug twins vs random
  distinct pairs) - no hand labeling. Held-out: precision 1.000 / twin-recall 0.852 at
  the default 0.90 operating point, vs 0.977 / 0.689 for the calibrated cosine gate;
  AUC 0.998 vs 0.991. Notable learned fact: word-overlap carries a NEGATIVE weight given
  cosine - true twins are re-phrasings, high word overlap signals template-similar but
  distinct notes. `NEVERTWICE_WRITE_DEDUP_MODE=cosine` is the kill-switch. See
  `research/TWIN_GATE.md`.

### Fixed
- **The README's suite count was a number kept by hand.** It said seventy-six; there were
  seventy-seven. Nothing checked it, so it went stale every time a suite was added - the exact
  shape of unverified claim this project refuses everywhere else.
  `tests/_test_readme_funnel.py` now counts the tracked `_test_*.py` files and fails when the
  README disagrees.

### Fixed
- **The published latency figures were precise to a millisecond and should never have been.**
  The freshness ratchet forced a re-measurement after the `store_state` extraction, and the
  same minimum-of-five statistic on the same unchanged tree produced three materially
  different PreToolUse numbers within one day. `--repeat` stabilises the figure *within* a
  session and does not across them. The table now says to read it as a tenth of a second, and
  states plainly that the drop from the previously published number is machine state rather
  than an optimisation - crediting a file-layout refactor with a speedup would have been the
  easy and wrong reading. The individual measurements are recorded in the `caveat` field of
  each latency claim, which is where evidence *about* a measurement belongs.

  Every other live claim re-ran byte-identical at HEAD: poisoning, forgetting and longitudinal
  artifacts reproduced exactly, which is what makes the latency spread attributable to the
  clock rather than to the code.

## [2.3.0] - 2026-08-18

### Added
- **Graph laws (`nevertwice-integrity`).** The knowledge graph is now checked against its own
  algebra rather than only for broken links: relation targets must resolve, `rel` types must be
  defined somewhere, the causal orientation must be acyclic, and no pair may be asserted with a
  relation *and* its converse. It also reports **vocabulary coherence** - the drift between the
  relation types extraction writes and the ones the causal model reads, a class of bug no
  per-note validation can see. `--strict` turns it into a CI gate. On a 3.4k-note store it runs
  in 0.4 s and found 20+ causal cycles, 4 converse contradictions, 142 edges with undefined
  types, 86 dead wikilinks, and a 73% unreachable-edge rate.
- **Refraction (`nevertwice-lens`).** A small algebra of pure `store -> View` projections:
  relational primitives (`where` / `order_by` / `top` / `select`) plus semantic lenses a
  relational view cannot express. The headline lens is the **falsification frontier** - the
  beliefs nearest to being wrong, ranked by `(1 - confidence) x recurrence x revision history`,
  the first surface that answers *what do I believe that I should test first?* Emitters render
  one View as markdown, mermaid, JSON, or an Obsidian `.base` file, which honestly declares
  which columns a Base cannot express.
- **Injection receipt.** The SessionStart payload now reports what it cost, how many lessons the
  budget refused, and what it saved: `_memory: ~430 tok - 5 lessons (2 held back) - saved ~11.2k_`.
  Budget-driven truncation used to be entirely silent. It takes no reservation and degrades or
  disappears rather than displace a lesson; `NEVERTWICE_INJECT_RECEIPT=0` restores the exact
  previous payload.

### Fixed
- **Live-pipeline review round (2026-08-18, /code-review max over a production deployment;
  13 findings, all fixed here and ported to the live install).**
  (1) *Watermark delta-mining*: `--dir` sweeps keyed idempotency on path+content hash, so a
  resumed/growing transcript was re-mined IN FULL every sweep (one Codex rollout six times,
  another sixteen; ~61% of watch-path LLM spend was repeat work). A per-file byte watermark
  (`.ingest_watermarks.json`) now mines only the appended tail; a rewritten file re-mines
  once. (2) *Near-duplicate write gate*: the LLM re-states one lesson under twin titles that
  exact-slug reconcile cannot see - a calibrated embedding gate (0.80; measured on 4.2k live
  vectors: distinct pairs top out at 0.737, twins median 0.833 - the first-shipped 0.92 was
  measured near-inert) retires the older twin and carries recurrence forward. The weekly
  consolidator's threshold was recalibrated the same way (0.92 -> 0.86; it had accumulated
  141 exact-slug twin pairs while nominally deduplicating). (3) *Session identity*: stems
  used `session_id[:8]`, collapsing every prefix-constant ingest id to the literal
  'ingest-f' (34 notes) and conflating same-minute transcripts - now a hash of the whole id,
  with the collision-free stem reserved BEFORE typed notes stamp it as provenance, and a
  same-day same-slug re-encounter from another session ABSORBED into the existing note
  (recurrence/sources bumped) instead of minting a '-2' twin. (4) *Card slots*: day-granular
  date sort over glob order made the five card slots "alphabetically-first five of today" -
  ties now break by recurrence, then stated confidence, then stem. (5) *Sentence-aware card
  truncation*: Status lines were hard-sliced mid-word with no marker. (6) *Honest token
  ledger*: the "tokens saved" headline booked a full-store-re-paste counterfactual (one
  recall booked +190k) that grew with duplicate bloat - the ledger now records REAL injected
  tokens first and labels the bound as a bound; the injection receipt no longer quotes it.
  (7) *Anti-confabulation*: repeated injected boilerplate (global CLAUDE.md, project
  rosters, system reminders) is collapsed before extraction and the prompt forbids
  attributing work to projects named only there (a live card had credited VPN work to an
  unrelated project). (8) *Relation-target reachability*: edge targets are auto-tagged as
  entities on the asserting note, so typed edges resolve by construction.
- **`fixes` / `fixed-by` now reach the causal model.** They were the store's most common typed
  edges (517 of 2400) and the impact graph oriented neither, so `what_breaks` / `why` reasoned
  over a graph missing a fifth of its edges - including the fix-relations the "never repeat a
  mistake" premise rests on. Measured on the live store: entities the causal model can answer
  for went 102 -> 210 of the top 300 (34% -> 70%), impact edges +38.9%. Cost: 26 -> 85 cycles,
  each a real data contradiction that `nevertwice-integrity` now lists. See
  `research/CAUSAL_VOCAB.md`.
- **The SessionStart budget really does bound the whole payload now.** Two leaks broke the
  audited M-15/M-d invariant on real data: the "show at least one lesson" guarantee appended the
  first line of every section regardless of the cap, and section headings were never charged to
  the budget at all. Measured before the fix: 11 of 12 projects overshot a 1200-char budget and
  one reached 2418 against a cap of 2200. An oversized lesson is now trimmed into its snippet
  with the title kept whole, and dropped entirely rather than shown under a truncated name.
- **`confidence` reaches the metadata layer.** M-10 stamped it into every note and the ranker
  read it off the embed cache, but `_note_meta` dropped it - so every read surface built on note
  metadata (digest, dashboard, graph, lenses) was blind to how sure the memory is of what it
  knows.
- **Adversarial-review hardening (2026-08, 15 findings + 5 runner-ups, all fixed).** The sharp
  ones: (1) the new test harnesses patched `m.VAULT` but not the import-time `EMBED_CACHE`
  constant, so one test run overwrote a real deployment's embedding cache AND its `.bak` with
  fixture data - harnesses now patch every vault-derived path (mirroring `sandbox()`), pinned
  by a canary test; (2) cycle detection rewritten from simple-path DFS (exponential on dense
  ACYCLIC graphs - a 61-node lattice took ~12s and `--strict` would hang CI on healthy stores)
  to Tarjan SCC, O(V+E) always, with EXACT uncapped region totals (the old totals silently
  capped at 20); (3) a missing `receipt.py` in a flat deployment no longer kills the whole hook
  at import; (4) one more budget off-by-one (the accept predicate omitted the joining newline -
  payloads landed at budget+1 on exact-boundary budgets); (5) mixed cause/fix cycles downgraded
  to warnings (a `caused-by` + `fixes` pair can be consistent); (6) `causal_closure` no longer
  draws synthetic root->effect edges - mermaid shows only the store's real typed edges among
  shown nodes; (7) the frontier's revision factor silently zeroed for non-slug project names;
  (8) integrity reads the causal orientation FROM `causal.py` instead of mirroring it; plus
  scoped-run entity universes, case-insensitive wikilink resolution, markdown/mermaid escaping,
  project-scoped `.base` emission, type-stable `order_by`, UTF-8 `--json`, and `human()`
  rounding at the 999_999 boundary.

## [2.2.1] - 2026-07-11

### Changed
- **The engine speaks English everywhere it writes or prompts.** The extraction /
  compaction / consolidation / rerank prompts, the note and project-card markers
  (`**Prevention:**`, `## Accumulated state`, `## Project card`, `## Merged from
  duplicates`, `## Related (auto)`), the SessionStart / prompt-recall injection
  headers, Index.md, and the `process_now` console are English now - a store created
  by any user reads naturally. Note content still follows the session: the prompts
  explicitly keep titles/descriptions in the language the session was written in.
- **A pre-2.2.1 store keeps working forever, unmigrated.** Every legacy Russian
  marker is dual-read (`**Как избежать:**`, `## Накопленное состояние`, the old
  type labels, consolidation headers), pinned by a new compat suite
  (`_test_legacy_markers`). Deliberately kept bilingual because they are functional,
  not cosmetic: the RU/EN stopword list (IDF profile), the injection/off-topic
  filter regexes, the Cyrillic transliteration table, and the localized
  Task-Scheduler output parsing.

## [2.2.0] - 2026-07-11

Personalization and long-session round: bring the memory you already have, keep recall
alive through compaction, and let the precision reranker manage itself.

### Added
- **`nevertwice-import`** - one-shot importers that turn what other tools learned about
  you into ordinary typed notes: `--from claude` (Claude Code auto-memory),
  `--from chatgpt` (a pasted memory export), `--from cursor` (`.cursor/rules` +
  `.cursorrules`), `--from agents` (top-level bullets of any AGENTS.md; the
  Nevertwice-managed block is skipped so the store never eats its own output).
  Everything lands through the same write path as `remember` - secret redaction,
  injection-shaped rejection, recallable at once even with no model - and a
  content-hash ledger makes re-runs no-ops. `--dry-run` shows the plan.

### Changed
- **PreCompact resets the per-session recall dedup.** Compaction wipes the injected
  notes out of the agent's context; the "already shown" state now goes with them, so a
  multi-hour (loop) session keeps recalling instead of starving. SessionEnd still keeps
  the state - a resumed session returns with its context intact.
- **The trained cross-encoder manages itself.** `NEVERTWICE_XRERANK` defaults to `auto`:
  on when the `[reranker]` deps are installed AND the model is already in the local HF
  cache. One `=1` run downloads it; from then on the measured precision win (top-1
  0.550 -> 0.614) stays on by itself. The cache gate means a machine that merely has
  torch for other work never gets a surprise ~2 GB download. `1`/`0` still forces it.

## [2.1.1] - 2026-07-10

A launch-audit round: two independent execution-verified reviews (published-package
e2e + hot-path bug-hunt), a TRIZ contradiction probe, and a council verdict. Every
fix carries a test or an executed repro.

### Fixed
- **The pip first-touch loop on a no-model box.** With no Ollama and no cloud key,
  `nevertwice-remember` wrote the note but `nevertwice-search` answered "(no memory
  stored yet)": `api.remember` gated `update_embeddings` on embedder availability, so
  the note never got its text-only FTS record (batch writes already had one). Both
  paths now share the contract - vectors when an embedder is up, else a text-only
  record that lexical recall serves immediately. Regression test pins remember->recall
  with the embedder down; the flagship `examples/demo.py` now produces a real hit
  with no model at all.
- A real lexical hit could display **score 0.00** (bm25 on a tiny corpus is ~0) and
  agents filtering `score > 0` dropped it; the FTS score now floors at the
  token-overlap score.
- The token-savings baseline could trigger a full vault scan inside the per-prompt
  hook (measured 14.9 s on a 2.6k-note store): the hot path now reads only the cached
  value, and the sleep-time refresh sums the baseline from the SQLite index (8 ms).
- `nevertwice-search --help` exited 1; it now prints usage titled `nevertwice-search`
  and exits 0.
- Recovery hints use pip-valid forms (`python -m nevertwice.embed_index`); the
  empty-store message no longer points pip users at repo-relative files.
- Importing the engine no longer creates the store directory as a side effect, so
  `install.py --print` is a true dry run.
- `examples/demo.py` propagates child exit codes instead of always exiting 0.
- The embed-failure log line ascii-escapes OS-localized error text (codepage-proof).
- Import hygiene: one import style per module (the last two CodeQL notes), unused
  imports dropped, and a stray generated `memory_dashboard.html` untracked+ignored.

### Changed
- Docs: the watch daemon is spelled `nevertwice-watch` everywhere; the guard-pack
  comment no longer overstates seeding immediacy; `pip install nevertwice` leads the
  README hero block and the install section.

## [2.1.0] - 2026-07-09

A hostile-critique hardening round: every finding below was verified by execution before fixing,
and each fix carries a regression test.

### Added
- **Token-savings counter** (`nevertwice stats`, `python -m nevertwice.stats`): a best-effort ledger
  of what the active layer bought - tokens saved vs re-injecting the whole store each turn, guard
  fires, counterfactuals - shown as a terminal panel with a 14-day activity sparkline, a dashboard
  card, and a one-line digest summary. Stdlib, atomic, hot-path-safe (a failure here can never
  affect the recall it measures).
- **Universal guard pack** (`NEVERTWICE_GUARD_PACK=1` or `python -m nevertwice.guards pack`):
  11 high-precision classic pitfalls that warn from the first session with no model and no
  history. Advisory-only, never promotes to blocking, self-retires like any guard.
- Benchmark infographic in the README; a measured **Speed** section in docs/BENCHMARKS.md with
  `research/latency_bench.py` to reproduce it anywhere.
- Community surface: CODE_OF_CONDUCT, issue/PR templates, ROADMAP, docs and examples indexes.
- `env_int`/`env_float`: a mistyped numeric env var now degrades to the default with a warning
  instead of crashing the import.

### Fixed
- Idle SessionStart no longer pays the LLM liveness probe: 2,188 ms -> 80 ms measured. Every
  hook process sheds two lazy imports: PreToolUse end-to-end 146 -> 76-85 ms.
- AGENTS.md refresh crashed on a Windows path in the project card (regex replacement template).
- 3 of the 12 MCP tools were advertised but not dispatchable (memory_why, memory_guard_feedback,
  memory_anticipate_feedback); a parity test now pins TOOLS == dispatch.
- The git merge driver silently dropped block-style YAML lists (as written by Obsidian's
  Properties panel); that shape now surfaces as a real conflict instead of losing tags.
- The ReDoS filter is now a shape-agnostic subprocess probe with a hard 0.6s timeout and fails
  closed; a static denylist had missed several catastrophic patterns across review rounds
  (including a paren-less `a+a+...b` and the bounded `(a{1,2}){38}`).
- Calibrated score fusion no longer sinks a lone hit to the bottom of the ranking (a single-signal
  z-score collapsed to zero); one relevant note now ranks correctly in default retrieval.
- Recall survives a malformed note whose frontmatter triggers a RecursionError (now caught with the
  other parse errors instead of aborting the sweep).
- `embed_index` takes the vault lock, so a rebuild can no longer race consolidation's cache writes;
  consolidation no longer crashes on a cached recurrence float; `bootstrap --force` no longer erases
  a project's Context history.
- `.docx` was the only size-capped document format; the cap now guards every format and stdin.
- install.py could claim a foreign script that happened to be named memory_hook.py.
- Larger, more readable dashboard type; the dashboard also builds from one vault scan (was three).
- Two research figures baked the pre-rename name into their title; regenerated. The post-retrieval
  infographic's footer line overlapped the bottom cards; canvas raised so it clears them.

### Changed
- The tagline leads with the active layer: *"Proactive, local-first memory for AI coding agents -
  it acts before your agent repeats a mistake."* Reader-facing docs no longer carry internal
  tracking codes; the recall-leanness numbers read as one honest range across the tour and the
  infographic.

## [2.0.0] - 2026-07-04

The project was **renamed from Anamnesis to Nevertwice**, and the headline feature became Active
Memory: memory that acts on a past mistake instead of only recalling text.

### Renamed (nothing breaks for a release)
- Repo, package, and store are now `nevertwice`. The old GitHub URL 301-redirects; stars and forks
  carried over.
- `ANAMNESIS_*` and `CLAUDE_MEMORY_*` environment variables are bridged to `NEVERTWICE_*`
  automatically, so an existing config keeps working.
- An existing `~/.anamnesis` store is used in place; new installs create `~/.nevertwice`. Your data
  is never moved silently.
- The `anamnesis-search` / `-remember` / `-mcp` console commands remain as aliases.

### Added
- **Active Memory.** Guards compile a past mistake into an executable check that fires *before* the
  agent repeats it, at zero context tokens until it fires (Popperian lifecycle: advisory until
  corroborated, self-retiring on false positives, always overridable). Wired into the Claude Code
  PreToolUse hot path. Plus anticipation (trajectory-resemblance warnings) and counterfactual
  (`what breaks if I change X?` from an induced causal graph).
- **12-tool MCP server** (was 9): added `memory_why`, `memory_guard_feedback`, and
  `memory_anticipate_feedback`, so MCP-only agents (Cursor, Cline, Zed, Claude Desktop) can train
  guards, not only read them.
- **Cross-machine sync that merges.** A structured git merge driver auto-resolves concurrent edits
  to the same note (recurrence takes the max, a retirement wins, tags union) and leaves honest
  conflict markers on a genuine divergence. Verified end to end through real git.
- **Self-contained HTML dashboard** (`python -m nevertwice.dashboard`): the whole store rendered
  into one offline file, no server.
- A comparison row and honest write-up for the Hindsight memory system.

### Changed
- README leads with the moat (plain files you own, plus memory that acts) and the measured
  guard result, with the token-economy number kept in context rather than as a headline.
- Repo layout: tests and the research harnesses moved out of the shipped package, so a
  `pip install` gets runtime code only.
- CI runs the product suite on Python 3.10-3.14 across Linux, macOS, and Windows; the research
  harnesses run on a current Python.

### Fixed
- Two critical merge-driver bugs (a non-existent module path in the driver registration, and a
  conflict that could silently drop one side on rebase) plus ~20 correctness and hardening fixes
  from two adversarial review rounds and a five-advisor council review.
- Secret redaction now also covers the embeddings-cache and cloud-embedder path.
- A regex character class that emitted a Python 3.14 `FutureWarning` and had stopped stripping
  en/em dashes.
- All 162 open code-scanning alerts resolved (real fixes; a few documented policy exclusions).

## [1.1.0] - 2026-06

### Added
- Opt-in **Brain layer** (`NEVERTWICE_PROFILE=research` or `general`): the same captured sessions
  self-wire into a knowledge graph of typed entities (paper, method, dataset, ...) with per-entity
  cards, an evolution timeline, and graph-centrality salience. Pull-only, so the token-bounded hot
  path is byte-for-byte unchanged when it is off (the default).
- A SQLite scale-tier keeps entity queries single-digit-millisecond into thousands of notes.

### Invariants (enforced by tests)
- Hot-path injection is byte-for-byte unchanged with the Brain layer off.
- Brain notes are pull-only, never in the default injection set.
- `LOCAL_ONLY` projects and agents never reach the cloud.

## [1.0.0] - 2026-06

Initial public release: local-first, agent-agnostic long-term memory as plain Markdown under git.
Hybrid retrieval (local `bge-m3` embeddings fused with BM25, calibrated abstention), write-time
supersession so contradictions do not pile up, capture for Claude Code (hooks) and any agent
(MCP / watch daemon / Python API / LangChain / LlamaIndex). On LongMemEval-oracle with one shared
local embedder, calibrated fusion reached R@5 0.80 against Mem0 0.76, with the harness and the
negative results published.

[2.0.0]: https://github.com/DonPlaton/nevertwice/releases/tag/v2.0.0
[1.1.0]: https://github.com/DonPlaton/nevertwice/releases/tag/v1.1.0
[1.0.0]: https://github.com/DonPlaton/nevertwice/releases/tag/v1.0.0
