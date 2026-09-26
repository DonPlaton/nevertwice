# Changelog

All notable changes to Nevertwice. Format loosely follows [Keep a Changelog](https://keepachangelog.com);
versions are [semantic](https://semver.org). Dates are UTC.

## [Unreleased]

Two threads. A research programme that ended in **NO-GO** and deleted most of what it built,
and a review of the engine that has been running in production on the author's machine for
three months. The review found fifteen defects; a benchmark built to check one of this
project's central claims immediately found two more, and one of the three new mechanisms
failed the gate written for it before it ran.

Nothing here is a release. `nevertwice/invariants/` is empty and a test pins that it stays
empty.

### Added

- **Q5, the principle layer (in progress): de-identified cross-project recall, no verdict-adjudication
  half.** `nevertwice/_engine_text.py::principle_scan` rejects an IP, URL/FQDN, Windows/POSIX path,
  email, host:port/bare port, version string, or a caller-named forbidden token (project slug,
  entities, and - at promotion time - a project's whole vocabulary), every pattern a bounded
  `_lazy_re` shape. The extraction prompt (`_engine_config.py`) asks pattern/mistake items for a
  one-sentence `principle`, gated by `NEVERTWICE_PRINCIPLE` (default on) and placed in the STATIC
  part of the prompt for prefix-cache friendliness. `write_typed_note` (`_engine_write.py`) turns
  it into `fm["principle"]` through redact → cap (word boundary) → the W8 unsafe-payload gate →
  `principle_scan`, never touching the rest of the note on a miss, and an absorb rewrite carries
  it forward (`_ABSORB_CARRY_FIELDS`). `NEVERTWICE_CROSS_PROJECT` grows from on/off into
  `off`/`all`/`universal` (default `universal`, docs/CONFIG.md), and `retrieve_cross_project`'s
  universal mode draws candidates ONLY from the synthetic `universal` project - a project's own
  notes cannot reach another project's cross-project section this way, proved against an `all`-mode
  positive control (`tests/_test_cross_mode.py`). New boundary in `docs/THREAT_MODEL.md`
  ("cross-project recall"); known gap recorded in `docs/WEAKNESSES.md` (W16: `_user_brief` is a
  separate cross-project channel this boundary does not cover). Tests:
  `tests/_test_principle_scan.py`, `tests/_test_principle_prompt.py`,
  `tests/_test_principle_write.py`, `tests/_test_cross_mode.py`. The sleep-time promoter that
  actually populates the `universal` pool (A5), its threshold calibration (A6) and the
  cross-project benchmark (A9) are separate, not-yet-landed steps of the same plan
  (`.loop/PLAN-Q3Q5.md`) - until A5 ships, `universal` mode is silent by construction (an empty
  pool). The ride-along contested-block/verdict-adjudication half of that plan (Q3) is explicitly
  NOT part of this work - it waits on a GPU kill-switch measurement (A0).

- **K8-B, four corrections before the merge (2026-09-17;** `tests/_test_k8_vault_dryrun_readonly.py`**).** The three
  step-0 research scripts declare their store to the sandbox lint: `k8_judge_eval.py` and `k8_skeleton.py` isolate
  before the first project import; `k8_vault_dryrun.py` declares `allow_live` (it parses the owner's vault by path),
  and the new suite proves the word - the script runs over a real-engine store with every write-capable hook
  function spied (the set derived from the hook's source, not kept by hand) and the store fingerprinted, and three
  planted writes each turn the check red. The judge's cap is a token budget a run, `NEVERTWICE_CONTESTED_BUDGET`
  (100k, ~240 pairs at the measured 415, three times the owner's vault inflow), oldest contested pair first, the
  number of calls printed, 0 switching the judge off - a cap of 50 calls newest-first had left 15-19 pairs a stand
  run never judged.
  **The numbers below are fast-cycle figures from the pre-merge draws, not a committed campaign artifact
  (no `research/results/k8b_*.json` for this paragraph) - pending the campaign re-measuring this branch
  post-review and publishing under `research/results/`:** the J2b gate `s0_retired_rate` (>= 0.734 after
  sleep) read 0.707 - a miss by two pairs (four `separate` verdicts, thirteen guard vetoes).
  `NEVERTWICE_EXPLICIT_RETIRE=judge` measured within one extraction draw on both corpora, both readings:
  over-retraction 0.000 everywhere, stale after sleep 0.067 / 0.183 against caps of 0.117 / 0.167 - by the
  rule written before the run the default stays `write`; the switch removes the last loss (the extractor's
  false `contradicts`) at the price of one stale case in sixty after sleep. Tokens a pair held at 411-416
  across eight runs. Seen on the way: the extractor is not deterministic across runs at temperature 0 (two
  draws on the implicit corpus, 28-29 of 80 cases apart).
- **A same-title note from another session is kept, not absorbed, unless the replacement is proven -
  and the judge moves out of the hook into sleep** (ledger K8; `tests/_test_k8_same_replacement.py`,
  `_test_k8_read_pairing.py`, `_test_k8_adjudicate.py`, `_test_k8_one_call_per_session.py`). The slug is a
  title the extractor composes - a topic, not a fact's identity - and two facts on one topic share it. The
  same-day collision was absorbed in place by the title alone and the other-day collision retired the
  earlier note by the slug alone; on the supersession stand's controls that lost 5 of 40 explicit and 14 of
  40 implicit still-true facts, and 7 of 17 on the two-day dating. Three layers, each with its price in the
  market's units. *Write (zero calls):* one function for both branches - an explicit `supersedes` /
  `contradicts` naming the title, the old literals all in the new `[facts]` block, or the old statement's
  text inside the new one replaces as before; a lesson without literals never absorbs a note with them;
  everything else is a `-2` sibling and the earlier note is stamped `contested`. *Read (zero calls):* two
  live notes of one slug fold into the newest hit, the earlier statement attached as one bounded line
  (`NEVERTWICE_EARLIER_MAX_CHARS`, 100) - both served, newest first, nothing demoted on a presumption;
  `conflicts()` lists the pairs, `integrity()` counts them. *Sleep:* `consolidate_memory.py` adjudicates
  the contested pairs with K7's prompt, oldest pair first, within a token budget a run
  (`NEVERTWICE_CONTESTED_BUDGET`, 100k - ~240 pairs at the measured 415, three times the 73 a week the
  owner's vault produces; the number of calls is printed; a cap of 50 calls newest-first, the first
  cut, left 15-19 pairs a stand run never judged - amended before the campaign) - `replaces`
  retires the earlier note with `valid_to` and `superseded_via: judge` and carries its recurrence into
  the winner, `separate` clears the stamp. Measured before the code: the skeleton similarity of the two
  statements reads AUC 0.63-0.66 on the extractor's descriptions and 0.76-0.81 on the facts block, so it
  is published and not shipped as a rule; the judge reads accuracy 0.956 on 204 pairs of known truth
  (`replaces` precision 1.000, recall 0.953) at 414 tokens a pair; the owner's vault would send at most
  73 pairs a week. Two guards sit between the judge's `replaces` and the retirement, both found on the
  first fast cycle: a note with verified literals is never retired for one without (rule 4 kept at
  sleep), and a value in the new statement that its own `[facts]` block does not carry - the session
  was never seen to say it - cannot retire a verified one. The one write-time loss the same-slug rule
  leaves is the extractor's explicit `contradicts` naming another title on a same-topic control;
  `NEVERTWICE_EXPLICIT_RETIRE=judge` routes that claim to the judge too (default `write`, rule 1 as
  the ledger wrote it). A store without a model gets the first two layers whole. The K7 hook judge
  (`NEVERTWICE_ABSORB_JUDGE`) and its shadow mode are removed; a `-2` sibling is now found by every
  slug lookup (a crash-retry of the session that wrote it used to mint a `-3`); the supersession stand
  reports `old_value_served` beside `stale_returned` and reads the engine twice under `--sleep`.
- **One retry, differently framed, when a relevant session yields no item** (ledger K5;
  `tests/_test_extraction_retry.py`). Ledger K3 read the extractor's "silence" on a bare two-sentence
  fact as instability - same text, temperature zero, a note or nothing depending on incidental prompt
  context. `_retry_if_silent` makes one more call with the prompt framed as a second pass when a
  non-empty, on-topic session came back with zero patterns, mistakes and decisions; the first call is
  byte-identical to before, an off-topic session is never retried (empty beats wrong), a second silence
  stands and is counted. `NEVERTWICE_EXTRACT_RETRY=0` turns it off. Gate: as-of never-written <= 5 of 120.
- **The literal channel quotes the session, never the hook's preamble** (ledger K6;
  `tests/_test_harvest_skips_preamble.py`). The harvester read the whole text handed to the extractor,
  which begins with `Working directory:` and `Trigger:`, so the working directory landed in nearly every
  note's `[facts]` block as a "fact" - 101 of 110 served blocks in an explicit-corpus run. `_facts_source`
  strips the frame before `_note_facts`, so both the harvested literals and the verbatim check of
  extractor-named ones read the session body only. Gate: the cwd literal in 0 notes, survival and the
  demoted count not worse, store bytes down.
- **`docs/WEAKNESSES.md` opens with what the campaigns since June measured.** The page was a June
  audit read as the current picture. Its header now says the body is the June record, and a first
  section states the ceiling measured since, each with its ledger gate and a table rendered from the
  register: over-consolidation - a later fact on the same topic absorbed into an earlier note, the
  note kept on disk and no longer handing that fact back (K1b/K7); the extractor's unstable output
  on a bare two-sentence fact (K3/K5); the guard stand's missed false-positive ceiling (J6); and the
  code-session corpus that does not separate retrieval systems (J3), with the held-out survival and
  accuracy figures named as dev-set numbers until the marked cards arrive.
- **A stand's page may not print a decimal the register lost** (`tests/_test_decimal_drift.py`).
  The governed front pages already resolve every number to a live claim; the study pages were only
  capped in how many unregistered numbers they print, and nothing asked whether those numbers were
  still the stand's. On 2026-09-11 that held four pages at once: `SUPERSESSION.md` quoted the
  previous campaign's rates under a heading one commit old while its generated table read the new
  ones; `ABSTENTION_AB.md` said its sweep was "read from" an artifact whose every cell differed;
  `LOCOMO.md` carried its headline table by hand, an engine revision behind the register; the as-of
  caption asserted a miss on a run that had met its gate. The suite requires every decimal outside
  fences and generated regions on a page with a `generator` (and every governed page) to resolve to
  a live claim's forms or interval, an external citation, a drift entry or a non-metric rule. The
  four pages now render those tables from the register: `tools/register_abstention.py` registers
  the recall-abstention sweep (35 claims), `register_retrieval.py --categories` the per-category
  LoCoMo recall, `register_supersession.py` the control-miss split and the paired discordant counts,
  and every registrar takes `--at COMMIT` to register further fields of an artifact the register
  already carries - COMMIT must be an ancestor of HEAD, a live claim on the artifact must carry it,
  and no file in the command's closure may have moved after it.

- **Archive-aware reconcile closes an interval past the archive window**
  (`nevertwice/memory_hook.py`, ledger J2b). A fact older than the 90-day window is in
  `Archive/` when its replacement arrives; the reconcile globbed only the live folder, so
  `valid_to` was never stamped and `as_of` kept showing the stale fact as current - on the
  owner store that is a quarter of the knowledge (`s0_retired_rate = 0.0` on 107 as-of
  case-runs). The same-slug and explicit-supersedes passes now also walk `Archive/`
  (`_reconcilable_typed_paths`), and every retirement records how it was decided
  (`superseded_via`: `slug` | `explicit` | `twin`). The near-duplicate twin pass stays
  live-only, because an archived note has left the embedding cache the classifier ranks on.
  The as-of stand gains a `--recent` control that dates the replaced note inside the window,
  so the archived-closure rate is read against the write path's own rate. Gate, baseline and
  the decision on a miss are in `.loop/GOAL-CLOSE.md`. Measured on the campaign of 2026-09-11:
  `s0_retired_rate` 0.000 -> **0.908** against a `--recent` control of 0.917 (gate: at least
  0.80 x the control and 0.60 absolute - met); as-of both-days-correct 0.783 -> 0.800; the
  supersession caps held (explicit stale 0.083 -> 0.033, implicit 0.100 -> 0.067; over-retraction
  proper - the memory retired a still-true fact - 0.05 -> 0.00 on both corpora). The first report of
  the campaign read "over-retraction 0.30 -> 0.35, the one cap that moved the wrong way" off a field
  that was never the gate: the broad control-miss rate, which the register now carries under its own
  name (see *Fixed*). Its rise is real and is a different finding: per run of twenty implicit
  controls the misses went retired 1 -> 0, never written 3 -> 0, written-but-unranked 3 -> 7 - the
  write path got strictly better and the whole rise moved into ranking, the first measured sign that
  the `[facts]` block dilutes a query that does not share its literals. Its gate is in the ledger (K1).

- **A write-path channel keeps the literal the summary drops** (`nevertwice/memory_hook.py`,
  ledger J3 addendum). The owner hand-marked a held-out of 52 literal-fact questions over his own
  coding sessions; the extractor answered 0.058 of them where the raw slice answered 0.827, and a
  model-free probe found the answer present in the notes returned for only 5 of 52 - lost at
  write, not at retrieval (`nvcc -arch=sm_120` had become "sm_120 support"). Two channels put it
  back, each verified as a verbatim substring of the session so a paraphrase cannot enter: the
  extractor names the literal per item (prompt field `facts`) and a deterministic harvester
  (`_harvest_literals`) salvages the ones it omitted from the session text near the note's topic;
  `_note_facts` merges them (at most ten per note) and `_append_facts` writes them into the
  description, so recall, the embedding and every reader carry them with no read-path change.
  `code_sessions_eval.py` gains a `fact-survival` stage - the share of questions whose answer is
  verbatim in the returned notes, counted in seconds without a reader or a judge - and pins the
  extraction temperature to 0 through a new `NEVERTWICE_EXTRACT_TEMP` override (the live hook
  keeps 0.2). On the hand-marked held-out: fact survival 0.096 -> **0.635**, reader accuracy
  0.058 -> **0.404** (Mem0's pipeline: 0.288 and 0.250). The gate written first - survival at
  or above 0.60 with accuracy at or above 0.40 - is met on the final measure; a deterministic
  development run read 0.596, so the gate sits inside the run-to-run band and the page says so.
  On the synthetic dev corpus the channel moved nothing (fact 0.083 -> 0.083); that corpus is
  a diagnostic, not a benchmark. `research/heldout_review.py` builds the marking package (to the
  polygon only; the repository gets a hashes-and-counts manifest); `tests/_test_fact_survival.py`
  pins both channels.

- **The as-of stand says why an old day failed** (`research/asof_bench.py`, ledger J2). Every
  engine row records what the store holds for the case's first session - notes written, live
  or retired, their `valid_to` - and classifies a miss as *never written*, *unranked*,
  *paraphrase* or *leak*. Reading the existing artifact this way showed the proposal the
  session opened with (retiring a fact without a replacement) was aimed at the wrong half:
  most misses were a paraphrased marker or a session the extractor left empty, not a
  retirement the write path failed to make. That proposal is recorded as dropped. The re-run with
  evidence spans closed most of the paraphrase misses and left the gate short by under two points;
  the instrumentation also showed that a replacement arriving after the ninety-day archive never
  closes the old fact's interval, an engine change proposed with its own gate.

- **The extractor is bound by name on every pipeline stand.** The engine reads its model name at
  import; four stands set it afterwards and ran on whatever the shell exported. Found when the
  frontier full arm produced two notes from nine hundred and forty sessions; fixed, recorded in
  every artifact, and both affected stands re-run.

- **The active-memory stand** (`research/guard_bench.py`, `research/GUARD_BENCH.md`): guards written
  by rule or by model against the linter, the cold-start pack, prompt recall and silence, every arm at
  a matched false-alarm rate on a deterministic labelled corpus. Guards miss their gate: a regex is
  binary, and firing catches a third of the repeats at a false-alarm rate no operator accepts. The
  README's guard sentence now says so.

- **A code-session corpus with gold answers** (`research/gen_code_sessions.py`,
  `research/code_sessions_eval.py`, `research/code_heldout.py`, `research/CODE_SESSIONS.md`): facts
  and gold chosen by the program, prose by a model from a foreign family, a real held-out over the
  owner's own sessions kept outside the repository. Built and tested; the runs follow.

- **Accuracy per token: every system's context, one reader, one judge**
  (`research/frontier_eval.py`, `research/FRONTIER.md`). The axis every vendor publishes and
  this repository had not, because recall is what a memory controls and answer accuracy is
  mostly the reader's. Now both are measured on the same 150 stratified questions: each
  system's context at one, three and five hits goes to the same local reader, a local judge
  grades against the gold answer, and the tokens are the count the reader itself reports.
  Two brackets frame it - the reader with no memory, and the gold evidence sessions whole -
  and a second judge re-grades the shipped arm so the judges' own disagreement is printed
  beside the accuracies.

  Three findings, one of them against us. Cutting each session to the passage the
  cross-encoder reads holds the accuracy of whole sessions for a tenth of the tokens - the
  drop is inside the judges' disagreement - which is the strongest thing this project can say
  about cost. Mem0's own pipeline answers for fewer tokens still and pays in accuracy;
  neither arm dominates, and both columns are printed. And our own extractor is the worst arm
  on the stand: not a retrieval failure - every question gets its notes, on the right topics -
  but a domain one. The prompt is built for coding sessions and writes patterns, mistakes and
  decisions, while this benchmark asks for the literal fact the user stated. Mem0's extractor
  keeps the sentence; ours keeps the lesson.

- **A second supersession corpus, where nothing announces the replacement.** Every case of the
  first corpus says so when it revises a fact - *actually, we moved off Postgres 15*. A real
  transcript often does not: the new fact simply arrives, framed like any first assertion.
  `research/gen_supersession_dataset.py --variant implicit` writes that corpus - same facts,
  same markers, same queries, the second session reframed and rotated so the two sessions never
  share a frame - and the explicit corpus is unchanged byte for byte (`--check` proves it).

  The gate was written before the run (ledger I5): our stale rate below the floor's with an
  interval that excludes it. We hand back the retracted fact **0.100** of the time without the
  cue against **0.058** with it; Mem0 reads **0.967** on both corpora and an append-only file
  **0.950** on both. Removing the cue costs us four points and costs them nothing, because
  neither supersedes with the cue either.

- **As-of recall missed its gate, and the number is published.** The threshold written in the
  ledger before the bench ran was 0.80 of cases correct on both days; the measurement is
  **0.625** [0.536, 0.707] over 120 case-runs. The floor - an append-only file with no notion of
  a date - scores 0.000, and Mem0 records a blocker rather than a number, because its memories
  carry the wall-clock time of the `add()` call and its search has no as-of filter. The loss is
  on the old day (0.683) rather than the day after (0.833): the scan of belief intervals is
  exact, and what bounds the number is whether the write path recognised the replacement at all.
  Per the ledger's own rule for a missed gate, the feature is not claimed in the README and the
  study page prints the figure beside the threshold it missed.

- **As-of recall** (`nevertwice.api.as_of(query, date)`, MCP `memory_as_of`): what the memory
  believed on a date about a query - every note whose belief interval contains the date, live
  and retired alike, ranked by the query with no LLM and no embedder. The engine has kept
  `valid_from` / `valid_to` on every note and a point-in-time scan since M-5; this puts a query
  in front of it and exposes it. `capture_session(text, date=...)` lets an importer of old
  transcripts, or a bench, place a session in time instead of today. Measured by
  `research/asof_bench.py` on the supersession corpus, sixty cases ingested two months apart and
  asked twice - for a day between the two sessions and for a day after - against a dateless
  floor; the gate (ledger I6) is both answers right on at least four cases in five.

- **The evidence tooling that the re-measurement needed.** `research/supersession_bench.py --pool`
  rebuilds the committed supersession artifact from the run files - two engine runs pooled, the
  other arms carried beside them, the paired tests per run - which until now lived in a session
  scratchpad, so nobody but the author could rebuild the file the claims point at.
  `tools/register_h2h.py` and `tools/register_retrieval.py` register a head-to-head or a
  retrieval family from its artifact (Wilson interval per recall, the package version in the
  label, the command's import closure stamped), instead of a fresh one-off script per family;
  both refuse an artifact older than HEAD and a dirty closure, the two conditions a restore
  demands. Each has a hermetic suite.

- **The external retrieval benchmark, on a corpus pinned by content hash.** Sixteen figures came
  down in 2026-08 for one reason: the LongMemEval corpus is third-party and uncommitted, and no
  content hash was recorded when the numbers were produced, so the run could not be pinned to a
  revision. `research/corpus_pin.py` closes that. The corpus stays out of git - 15 MB for the
  oracle variant, 278 MB for the standard one - and its sha256 comes in, committed in source with
  the URL it came from and the licence that permits the fetch. Both harnesses verify before
  reading a byte, and `verify()` raises rather than warns: a warning about a corpus mismatch is
  one nobody reads until the numbers are already public.

  Re-run on the pinned bytes, **every figure returns identical to three decimals**, ours and all
  three competitors'.

  | | R@1 | R@5 | R@10 | MRR |
  |---|---|---|---|---|
  | semantic (bge-m3) | 0.422 | 0.652 | 0.728 | 0.528 |
  | lexical, no embedder | 0.522 | 0.752 | 0.834 | 0.623 |
  | **calibrated fusion (shipped)** | **0.550** | **0.802** | 0.858 | 0.657 |
  | + trained cross-encoder (opt-in) | 0.614 | 0.826 | 0.858 | 0.712 |

  Head to head on the same pool, same embedder, same scoring function, 500 questions over 940
  sessions: Nevertwice R@5 **0.802**, Mem0 2.0.19 0.758, LangMem 0.692, A-MEM 0.692. Zep and
  Cognee record a blocker rather than a number, as before.

  The result cuts both ways and the page says so. The retraction cost nothing in accuracy; it was
  still right, because at the time nobody could have shown that. The withdrawn claims stay
  withdrawn - they were measured on a file that still cannot be identified, and reviving them
  because a later run agrees would be assuming the conclusion. The re-run is a new claim family
  on a named corpus.

  **And the same benchmark outside the oracle setting**, which is what the roadmap actually
  asked for: the standard variant pools 19,206 retrievable sessions against the oracle's 940,
  the same 500 questions and the same annotated evidence. Everything falls - the shipped fusion
  reads R@5 0.452 against 0.802 - and the shape holds, fusion still beating both signals it
  fuses. `research/EXTERNAL_RETRIEVAL.md`.

- **LoCoMo, the benchmark this project had excluded on paper.** The roadmap carried one
  sentence about it for months - not a candidate, because plain BM25 is reported to score about
  94% - and a test pinned the wording. That is a claim about a benchmark, and the rule here is
  that a claim needs a measurement. Refusing to run something because you believe it is
  saturated, without showing that it is, is an argument from authority where the authority is
  us.

  Ten conversations, 5,882 turns, 1,977 of 1,986 questions scored, retrieving the annotated
  evidence turn from the question's own conversation. Semantic 0.432 at R@5, lexical 0.499, the
  shipped fusion **0.549**. The term-overlap floor reads 0.499, not 0.94, and the three methods
  order exactly as they do on LongMemEval.

  The exclusion is narrowed rather than lifted, and the distinction is the whole finding: the
  94% figure describes judge-scored **answer accuracy**, which measures the reader as much as
  the memory. This measures **retrieval**. They are different quantities on different scales and
  the pages say, in as many words, that they must never appear in one table. LoCoMo is now "not
  a candidate *as a headline*", and the guard that pinned the old phrasing asks for that
  qualifier - a higher bar than the phrase it replaced, and both mutations of it fail.
  `research/LOCOMO.md`.

- **A second harness defect, and this one would have libelled our own ranker.** The first LoCoMo
  run reported semantic recall of **0.037**, which reads as "the bi-encoder collapses on dialogue
  turns". LoCoMo numbers turns per conversation - all ten start at `D1:1` - so `D1:3` names ten
  different lines, and an embedding cache keyed by the bare id collapsed 5,882 turns into 1,033
  entries. Eighty-three per cent of the semantic arm's vectors belonged to another dialogue. The
  lexical arm was untouched because it reads per-conversation text, so one arm was broken while
  the other vouched for the stand. Namespacing the ids moved it to 0.182.

- **A safety check that had been passing for the wrong reason.** The retrieval harness ranks a
  `semantic+recur` arm to show that the production recurrence prior changes nothing when every
  note has recurrence 1, the boost being exactly 0.0. The two arms broke ties differently:
  `semantic` resolved equal scores by session id, `semantic+recur` left them to the stable
  sort's fallback order. On 940 sessions the orders agreed often enough for recall@k to match
  and the check printed "inert by construction"; on 19,206 they disagreed and it reported a
  changed ranking for a boost of zero. It had been testing the tie-break and getting away with
  it because the pool was small. Both arms now use the same key and the check asserts the two
  rankings are **identical**, which is what "changes nothing" means. Found by running the larger
  pool, not by reading the code.

- **A benchmark for supersession, which nobody in the field has.** LoCoMo, LongMemEval and
  BEAM all ask whether a system *recalls* a fact. None asks whether it hands back one that has
  since been retracted - for a chat companion a nuance, for a coding agent the whole problem,
  because an agent acting on a withdrawn fact writes wrong code confidently and the wrongness
  is invisible until it runs.

  Three rates, because any one alone is trivially gamed: **stale**, **current**, and
  **over-retraction** measured on control cases where two facts are simply different and both
  still true. A system answering "I have nothing" scores perfectly on the first and zero on
  the second, and the pair says so immediately.

  | arm | stale | current | over-retraction |
  |---|---|---|---|
  | Nevertwice | **0.042** | 0.933 | 0.05 |
  | Mem0 2.0.19 | 0.917 | 0.950 | 0.00 |
  | append-only markdown + BM25 | 0.950 | 0.950 | 0.05 |

  Nevertwice's row is pooled over two runs of the same commit, which read 0.017 and 0.067. The
  extraction model is not deterministic at temperature 0, so one run of this stand is not a
  result - the same lesson as the latency retraction below, one level up.

  Paired on the same 60 cases: Nevertwice against Mem0 gives 54 discordant pairs and not one
  the other way. **Mem0 against the append-only floor: p = 0.69** - on this axis their design
  is indistinguishable from a text file, which is what single-pass ADD-only predicts and what
  their own design note says happens on purpose. Their retrieval still edges ours, 0.950
  against 0.933, and the page says so.

  The dataset is committed and content-hashed - `research/data/supersession_v1.json`, 80 cases
  over four shapes plus 20 controls, built by a generator that refuses to write a case no arm
  could score. Every external figure this project published before was withdrawn for want of
  exactly that. Full method and caveats: `research/SUPERSESSION.md`.

  Two things the table does not say on its own, and the page now does. On the column a user
  meets every day, did my fact come back, this design **loses**: 0.933 against 0.950 for both
  other arms. And over-retraction, the design's own worst failure mode, rests on 20 controls,
  so an observed 0.05 carries a Wilson upper bound of 0.236.

- **`tools/stamp_withdrawn.py`** and `tests/_test_withdrawn_pages.py` - a retracted figure now
  says so on the page that prints it. `docs/COMPARISON.md` was corrected by hand, and doing it
  exposed the general case: **36 registered pages** printed at least one withdrawn figure and
  none told the reader. The front pages are governed, so their numbers are generated from live
  claims and cannot drift; the study archive is `backlog`, which caps how many unregistered
  numbers a page may print and says nothing about whether they are still true. That is the
  wrong way round - the studies are where someone goes to check. Nothing was deleted: a
  project that removes the results it was wrong about loses the only useful part of having
  been wrong.

- **`research/gen_benchmarks_figure.py`** - `docs/benchmarks.png` had no generator. It was
  drawn by hand in July and rendered four LongMemEval-oracle bars that were retracted a month
  later, embedded with alt-text promising three more retracted results. A chart travels
  further than the page it sits on, so the retraction never reached anyone who saw only the
  image. The replacement reads every value from the evidence register at draw time and refuses
  to draw a withdrawn one; the provenance line is drawn into the figure.

- **`research/abstention_ab.py`** - the measurement three mechanisms shipped without. Each
  switch is swept over a curve rather than compared to *off*, because a default only ever
  tested against its own absence cannot be shown to be the right default.

- **`tools/sync_install.py`** - a reversible way to update a hand-rolled flat install. Backs
  up, copies, then verifies by importing the installed engine in a subprocess against a
  throwaway store. Never copies `.secrets.env`, `twin_calibration.json`, `guards.json` or
  `.processed_sessions.json`: machine state, not code.

- **`tools/repair_vault.py`** - puts accumulated store damage one reviewed command away.
  Renames, never deletes.

### Removed

- **The evidence-span layer (`nevertwice/evidence.py`, ledger J1) is deleted.** It missed its
  frontier gate by a wide margin (accuracy 0.067 against 0.37), and the rule written before the
  run was to delete on that miss. The incidental gain it showed on the temporal stands is a new
  hypothesis, not a defence of the old one: it is re-proposed as "a span only on the as-of path"
  with a gate to be written before the layer-free baseline is measured. `api.recall`,
  `api.as_of`, `api.format_note` and `api.capture_session` no longer read or attach spans, and
  the temporal, frontier and code-session stands score the note text alone again.

### Changed

- **The K5 retry and the K7 same-fact gate ship off by default; their gates were read and missed**
  (ledger K5, K7; 2026-09-12). At d07375e, two runs per corpus: the K7 judge took over-retraction proper
  from 0.125 / 0.350 to 0.000 / 0.000 - not one still-true fact absorbed or retired on eighty control
  case-runs, the cap of 0.05 met - and cost the stale column 0.033 / 0.067 to 0.117 / 0.167 against a
  cap of +0.02: missed, so by its own rule the default is off (`NEVERTWICE_ABSORB_JUDGE=1` opts in). The
  K5 retry took the as-of stand's never-written first sessions from 11 to 8 of 120 against a gate of 5:
  missed, default off (`NEVERTWICE_EXTRACT_RETRY=1` opts in). K6 stays: the working directory is in no
  served `[facts]` block. The engine the register describes is the one the gates left standing.
- **The README's supersession row prints its price beside the advantage.** The row said we hand back
  a retracted fact 0.033 of the time against 0.933 / 0.317 / 0.950 and said nothing about the next
  column, where we are the worst arm of the stand: a fact that stayed true stops being served 0.125
  of the time on the explicit corpus and 0.350 with the cue removed, against nothing for Mem0 and
  Zep/Graphiti. Both figures are live claims cited on the page, with the cause table linked.
- **The supersession bench names its two control metrics and reads every arm's store**
  (`research/supersession_bench.py`, `research/_graphiti_arm.py`; ledger K0/K2). `score()` reports
  `control_miss_rate` for every arm (the still-true fact did not come back, any cause) and keeps
  `over_retraction_rate` for the memory's own retirements only - `None`, never a number computed
  from the other, for an arm whose store the run did not read. Mem0's `get_all` and its add-events
  (a DELETE, or an UPDATE that drops the marker) and Graphiti's edges (`edges_all`: invalidated or
  expired = retired) now feed the same three flags our arm reads from its own store, so the cause
  split - retired / never written / written but below k - is computed by one rule for all arms;
  the floor's flags are read from its sentence store rather than assumed. `pool()` carries the
  pooled control miss with its per-run values and the pooled cause counts. A bench edit withdraws
  every claim whose closure reaches it: 120 claims (both supersession families and as-of) are
  withdrawn in this commit and return with the K2 re-run.

- **The dense weight of the calibrated fusion is 1.0, from 0.5.** The weight was tuned when
  the stand embedded the first 2,000 characters of a session and the lexical arm scored raw
  tokens; with whole-session vectors and the stemmed lexical arm the shipped 0.5 gave back
  eight thousandths at R@5 on the oracle pool while the semantic arm alone rose four points.
  `research/fusion_sweep.py` re-sweeps both pinned corpora from cached vectors; 1.0 beat 0.5 by
  0.012 R@5 on the oracle pool and 0.014 on LoCoMo, clearing the gate written in the ledger
  before the run (at least 0.01 on one corpus, no more than 0.005 lost on the other). The whole
  curve is a generated table on `research/RETRIEVAL_FUSION.md`, and the two passages that
  called 0.5 Pareto-optimal now say on which stand that was true.

- **Re-measured with the shipped tokenizer; sixty-eight claims restored, twenty-four
  registered for the ablation.** LoCoMo, per conversation: lexical R@5 0.499
  raw to 0.601 stemmed, the fused ranker 0.549 to
  0.626 (R@1 0.293 to 0.353).
  LongMemEval-oracle, whole-session vectors: lexical 0.752 to
  0.738, the fused ranker 0.794 to
  0.788 at the shipped dense weight - the loss the gate allowed - while
  the opt-in cross-encoder over the stemmed first stage rose to 0.836
  at R@5 and 0.616 at R@1 (from 0.814 and 0.610 on raw tokens):
  the candidate set it reranks got better even where the fusion's own top five did not. The
  non-oracle pool reads 0.228 / 0.426 /
  0.520 fused over 19,206 sessions, the three that exceeded
  the embedder's context now embedded from a shorter prefix and counted. Both ablation arms are
  claim families of their own (`locomo_raw.*`, `longmem_raw.*`), so the page that argues the
  change (`research/LEXICAL_MORPHOLOGY.md`, governed, three generated tables) cannot quote a
  before-number the register does not hold.

  The competitor store arms came back on the oracle pool to three decimals (Mem0 2.0.19
  0.758,
  LangMem and A-MEM's stores 0.692),
  and ran on LoCoMo's global pool for the first time (Mem0 0.575, LangMem
  0.441, A-MEM 0.436 at R@5); our rows of those
  tables, and the non-oracle competitor rows, wait for the next engine commit so that they are
  measured once. Still withdrawn: supersession (17), abstention (5), latency (4, an idle machine),
  our head-to-head rows (4).

- **Stop words out and stems in on the lexical signal** (`NEVERTWICE_LEXICAL_MORPHOLOGY`, default
  on). The lexical arm scored raw tokens: `running` and `run` were different words, and so were
  `ошибка` and `ошибки`. Now English goes through Porter's 1980 stemmer - the algorithm SQLite's
  own `porter` tokenizer implements, so the in-process BM25 and the FTS5 index agree on every
  token - and Russian through Snowball, both pure Python, both checked against reference
  implementations in a hermetic suite with the expected values frozen in it (NLTK's original
  mode, 100% of 23,268 tokens; `py_rust_stemmers`, every non-`ё` token of the vault vocabulary).
  Measured before it shipped, against thresholds written first, on four stands
  (`research/LEXICAL_MORPHOLOGY.md`): LoCoMo lexical R@5 0.499 to 0.601 and the fused ranker
  0.549 to 0.626; the owner's store, a session's summary finding the notes written from it,
  Russian R@1 0.583 to 0.685 and English 0.761 to 0.783; LongMemEval's 14k-character sessions
  0.014 down on the lexical arm, inside the gate. The protocol that lost - a note finding its
  wikilinked siblings - turned out to score the session's phrasing fingerprint, which no prompt
  shares, and is recorded rather than obeyed. The SQLite index stamps the tokenisation it was
  built with and is rebuilt once when the switch changes. Every retrieval claim is withdrawn by
  this commit and re-measured with the shipped tokenizer in the next, with a `--no-morphology`
  ablation arm beside it.

- **Tier 1 of the re-measurement, at the reviewed engine, with whole sessions embedded.** The
  review of 2026-09-05 found our semantic arm embedding the first 2,000 characters of every
  LongMemEval session while the competitors embedded the whole one; the stand now embeds whole
  sessions (cap 28,000, the pool's own) for everyone, and the vector caches carry the cap in
  their name and a stamp inside. Re-run on the GPU: forty claims restored at this commit.

  | oracle pool | R@1 | R@5 | R@10 | MRR |
  |---|---|---|---|---|
  | semantic (bge-m3) | 0.428 | 0.692 | 0.782 | 0.552 |
  | lexical (BM25) | 0.522 | 0.752 | 0.834 | 0.623 |
  | calibrated fusion (shipped) | 0.534 | 0.794 | 0.848 | 0.650 |
  | + trained cross-encoder (opt-in) | 0.610 | 0.814 | 0.848 | 0.705 |

  The semantic arm rose from 0.652 to 0.692 at R@5 - the answer turn
  begins past the old cap in a third of the evidence sessions. The fused ranker did not follow:
  0.802 to 0.794, because its dense weight was tuned on the capped vectors;
  re-tuning it is a separate measurement with its own threshold (ledger I1). The cross-encoder
  lifts R@1 by +0.076 over the fusion.
  On the non-oracle pool (19,203 sessions) the fusion reads
  0.252 / 0.438 / 0.548
  against 0.184 / 0.354 / 0.440
  semantic and 0.242 / 0.442 / 0.534
  lexical. Three of that pool's non-empty sessions exceed the embedder's context even under the
  character cap and have no vector at this commit; the next commit gives every arm the same
  fallback (halve and retry) and counts them.

  Latency re-measured on the idle machine before anything else ran: PreToolUse
  85 ms, UserPromptSubmit 78 ms, SessionStart
  78 ms, cold import 28 ms - within the
  cross-session spread the caveat on each claim records. The head-to-head table carries our row
  (0.794 at R@5); the competitor rows return with their re-run at the next engine
  commit, so that the store arms are measured once rather than twice.

- **One hundred and one claims withdrawn on 2026-09-05, pending re-measurement.** The review
  changed the engine, and the register's rule is that a number measured before a change
  describes a different engine. `tools/remeasure.py` withdraws by import closure and restores
  from artifacts re-run at HEAD, refusing a dirty tree and an artifact older than the code; the
  families a CPU can re-measure came back the same day - twenty-seven claims (LoCoMo, poisoning,
  the cheap baselines, forgetting, the guard pack), every one reproducing its value exactly -
  and seventy-four wait for the GPU re-run: retrieval, supersession, abstention, and latency
  until the machine is idle. The README's comparative rows say so in place of their numbers, and
  every study page whose figures are still down says so under its title.
- **Value-based abstention is off by default, on the measurement rather than on doubt.** It
  shipped at 0.35 on both the recall and injection paths with tests proving the mechanism
  works and nothing measuring whether it helps. Swept over the labelled corpus: at 0.35 the
  payload is 26.6% smaller and the wanted fact comes back 8.7 points less often, against a
  gate - written before the run - of 20% for at most 2 points. No threshold on the curve
  clears both. Sixty characters is not worth an eight-point drop in finding the right lesson.
  Kept as an opt-in switch, because the trade plausibly reverses on a store where recall
  returns ten hits rather than one and a half; that is a hypothesis and it is labelled as one.
  `research/ABSTENTION_AB.md`.

- **The extractor is told which language to answer in, instead of being asked to work it
  out.** The prompt stated the rule - write in the dominant language of the session - and left
  the model to apply it. On a corpus with no Russian in it, the local model wrote 17 of 123
  notes in Russian: a drift of 0.138. The condition is now resolved in Python and the prompt
  names one language; the detector counts letters rather than bytes, because a Russian session
  is full of Latin identifiers and a majority vote reads almost every bilingual transcript as
  English.

  It was not a cosmetic defect. Those notes were correct and unfindable by an English query,
  and a replacement written in the wrong language gets a title the slug-keyed match cannot
  find, so the old note is never retired. Fixing it moved the supersession benchmark more than
  anything else did: **0 of 128** notes in Cyrillic, stale 0.067 → **0.017**, current 0.867 →
  **0.933**.

- **A latency regression was published and then retracted the same day.** The claims were
  re-measured because an engine change staled them, and the run happened while a 30B model
  was resident: UserPromptSubmit read 95 ms against its published 88, SessionStart 91
  against 85. That went out as a measured regression. Re-run at the same commit on an idle
  machine: 28 / 89 / 83 / 82 ms, at or below every published value. There was no
  regression. The bench moves about fifteen per cent with ambient load, which this
  repository already documented, and one run of it cannot support a claim about a change,
  including a claim against ourselves.

### Fixed

- **The test battery talked to the machine's model server and wrote into the repository.** The
  server log counted 277 embed and 54 tag requests from one battery run, and a before/after
  snapshot found two suites rewriting tracked files (an artifact byte for byte; `nevertwice/api.py`
  edited and put back in a `finally`). A test process now refuses every connection to the local
  Ollama port - at the socket and, on Windows, at the asyncio proactor, which connects without
  the socket's `connect` - and every child it starts inherits closed endpoints under each name the
  engine and the stands read. The battery fails a suite that changes a tracked file, and the two
  suites work on temporary copies. A concurrent-writers test no longer asserts that a Windows
  replace is never refused; a new test pins the bounded wait and the loud refusal on a clock it
  controls.
- **A benchmark artifact could name a model that did not run.** Stands labelled their output with
  the model they meant to set, or read it from a second, stale engine object; the label now comes
  from the engine that ran - the cloud backend's model when one is configured with a key, since
  extraction tries it first. The supersession stand's per-session token counts had the same
  cause: they were read from a second, idle engine object, and every one was zero.
- **A timing measured on a busy machine could be restored as current.** Restoring a withdrawn
  timing checked only the transport half of its rule, and two stands are exempt from that half,
  so a serving-latency figure timed beside other GPU work would have come back; a hand-written
  exclusion kept it out. Every timing claim - found by its unit too, not only by its field name -
  now needs the stand's own `machine_idle` record, with no exemption.
- **A competitor's model traffic could pass the benchmark pacer unseen.** The openai SDK 3.x sends
  through `httpx2`, a separate package the pacer did not hook, so graphiti's (and, in principle,
  any openai-compatible client's) calls to the local model carried no transport record - the
  server log was the only witness. The pacer now hooks `httpx2` too; an installed pacer records a
  quiet span as `calls: 0` instead of writing nothing; and the supersession, as-of and head-to-head
  stands mark an arm that needs the model but shows no paced call as invalid. `research/_pacer_selftest.py`
  checks, inside any environment, that every HTTP client present there is seen.
- **Most benchmark artifacts did not say which commit produced them, or when.** Every script the
  evidence register names as a writer now stamps `measured_at` (commit, UTC time, dirty tree) on
  its artifact - the product's guard-pack count through a small stand that stamps it - and
  reproduction ignores that stamp when it compares results. Five live figures whose stands
  changed for this (four from the embedder study, one token ratio) are withdrawn until the next
  campaign re-measures them.
- **A figure derived from other artifacts stayed "fresh" after they were re-measured.** Its
  freshness followed only the code that computed it. A derived artifact - the draw-divergence
  figures, a pool over per-run files, a merge of competitor runs - now records each input file by
  hash; a live figure whose inputs moved reads as stale, and a restore refuses it. The four
  draw-divergence figures are withdrawn until they are re-derived with their inputs recorded.
- **A restored confidence interval used the withdrawn run's sample size.** Restoring a claim
  recomputed its Wilson interval from the register's `n`, not from the run being restored (restore
  #2 fixed three by hand; twelve withdrawn claims still carry an `n` their artifact no longer has).
  The count is now read from the artifact - the claim's `n_pointer`, or the nearest `n` on the
  value's path - and a claim whose artifact records no count is not restored.
- **The guard that a run uses its declared model compared by substring.** `qwen2.5-7b` passed
  against `qwen2.5-7b-64k:latest`. `tools/check_llm_rows.py` compares the model tag exactly, and
  head-to-head rows carry it as a field.
- **A stopped benchmark's ingest cache could be resumed by a later run.** The frontier stand's
  ingest cache was keyed to its store alone; it now names the engine commit, the extractor and its
  output cap too, and a cache built under anything else is set aside, never resumed or overwritten.
- **The reproduction package named commands that no longer wrote their files.** Four entries
  lagged the commands the last campaign recorded; they now carry them (three remake the whole file
  from committed per-run files), and three as-of entries stopped declaring as foreign an arm the
  stand writes itself.
- **Recall that fell back to word matching said nothing (B6).** A cold-loading embedder misses the
  hook's one-second ping or its query embed, and ranking silently fell to word overlap; the hook
  injected those hits as if nothing had changed. `retrieve_relevant` now records why semantic
  ranking did or did not run, and both injections - per prompt and at SessionStart - add one line
  when it fell back on a store that has vectors (SessionStart only into room its budget leaves,
  before the receipt). A text-only store is word matching by design and says nothing.
- **A run of sessions that captured nothing stayed green (B4).** A valid extraction with every
  list empty was marked processed and counted nowhere, and `doctor`'s freshness stayed green on the
  Session note every session writes. Each relevant, non-trivial session now records its yield in
  telemetry (`extraction_yield`, a counter of its own: an empty answer is not a failure), and
  `doctor` WARNs when the last ten were all empty, or when 40 of the last 50 were - a partial
  failure one lesson in ten would hide from the first rule; both thresholds derive from one design
  bound, not a tuning. No retry is switched on. `telemetry.json` is written every session now, so
  the store's `.gitignore` names it - git_autocommit would otherwise commit it into a store that
  may be pushed to a remote, against its own promise of no transmission - it keeps no rollback
  copy, and the
  golden store does not certify it, as it does not certify logs (K54).
- **A lesson older than ninety days vanished from recall (B2).** The age pass moved typed notes
  into `Archive/` and dropped their vectors and index rows in the same pass, and recall reads
  candidates from those two only - against the engine's own rule that `Archive/` is age, not
  retraction. Every lesson past the window, and every note an importer of old transcripts wrote,
  disappeared the day it was written; the campaign-v2 code-sessions stand measured an empty
  context for 420 of 420 questions on exactly this (K46). The file still moves; the entry stays,
  marked `archived`, re-keyed when a name collision renamed the file, and ranking ages it through
  the existing decay. `embed_index` re-embeds archived notes the old rule dropped, but not merged
  duplicates. What the consolidator archives (a merged duplicate, a note over the opt-in cap) still
  leaves recall.
- **Capturing one note rewrote the whole vector cache (B3).** Every capture parsed and rewrote
  `.embeddings_cache.json` and its `.bak` - about 280 MB of writes per session on a 115 MB store,
  linear in the store. The snapshot keeps its format, and an append-only journal
  (`.embeddings_cache.json.journal`) takes what a capture changed: one note's records, fsync'd.
  The journal is folded into the snapshot once it passes a tenth of it, so the amortised write per
  note is constant. A journal is written against one snapshot (size, mtime, a hash of both ends);
  one whose snapshot was rewritten by anything else is set aside as `.stale-*`, never replayed
  onto it. A torn last line costs that line. A save that did not reach the disk no longer leaves
  the in-process memo ahead of it (F13), and archiving drops SQLite rows only when the cache change
  landed. That F13 fix exposed a revert that never worked: `migrate.revert` popped the reverted
  notes and saved the rest, the save was refused when they were the last entries, and the
  vector stayed on disk while the memo said it was gone - it now names its deletions, and an
  empty cache is written when it is the very dict a good load handed out and the caller emptied
  (only a failed load's fresh `{}` is refused). The SQLite index follows the cache only when the
  cache change reached the disk. Loading still parses the whole snapshot.
- **A sandboxed test could run on the owner's model and cloud key (b-e).** `isolate()` scrubbed
  `NEVERTWICE_ENV_FILE` but not its legacy mirrors: `config._bridge_legacy_prefixes` copied
  `ANAMNESIS_ENV_FILE` / `CLAUDE_MEMORY_ENV_FILE` back after the scrub and the file they named was
  read; `ANAMNESIS_EMBED_MODEL` and `ANAMNESIS_TWIN_FILE` got through the same way; and the
  clone-root `.secrets.env` was read inside every sandbox. Every scrubbed name is now scrubbed
  under each bridged prefix (pinned equal to `config.LEGACY_PREFIXES`), and a sandbox sets
  `NEVERTWICE_DOTENV=explicit`, so it reads no env file but one it names itself.
- **`doctor` said the hooks were wired when they could not run (B5).** The check looked for the
  substring `memory_hook` in `settings.json`; the interpreter is pinned into every hook command at
  install time, so a deleted venv made every hook call exit 127 - memory off - while `doctor`
  printed ok. It now asks `hookwire.dead_reason` about each wired entry and fails on a missing
  interpreter, shim or engine, with `python install.py` as the repair.
- **An LLM answer that never ends is capped and counted, and a failure of the content is retried a bounded number of times (B1).** The
  extraction call sent no output limit, so a model that never closed its JSON generated until the
  120 s timeout - the campaign-v2 frontier stand was stopped 644 sessions in on exactly this. Every
  engine backend now sends `NEVERTWICE_EXTRACT_NUM_PREDICT` (4,096: the 12,000-character input at
  about three characters a token), an answer that reaches it is used when its JSON is whole
  (`capped`) and is a named, counted failure when it is cut (`truncated`), and a session that fails
  on its content three times (`NEVERTWICE_EXTRACT_MAX_ATTEMPTS`) is parked - marked processed with
  the reason; `process_now --retry-parked` tries them again - instead of costing a full
  generation on every sweep. A transport or HTTP failure (a backend down, a 4xx/5xx) is never
  counted: that session waits, as before. The research stands' own LLM calls got the same cap and
  carry a capped answer into their artifacts, marked; a test scans every payload. Telemetry's
  extraction-failure and outcome counters were dead on the hook's own path - `from . import
  telemetry` fails under `runpy.run_path`, and the error was swallowed (B7) - and now count.
- **A deleted or moved clone used to BLOCK the agent, not just turn memory off.** Every hook
  command was `"<python>" "<clone>/nevertwice/memory_hook.py"`; delete the clone (or `pip
  uninstall` it) while wired and `python <missing file>` exits 2 - CPython's own exit code for
  a script that is not there - which Claude Code treats as a BLOCK from PreToolUse and
  UserPromptSubmit: every edit, command and prompt refused, with no way left to ask the agent
  to repair its own settings.json (third premortem, 2026-09-23). `install.py` now also writes
  `~/.claude/nevertwice/hook_shim.py` - beside settings.json, never inside the clone being
  wired - and points every hook at it with the clone's `memory_hook.py` as its one argument.
  The shim hands the process to the real engine unchanged (same stdin/stdout/exit code,
  `SystemExit` included) when it is there, and degrades to a message on stderr (and stdout for
  `SessionStart`) plus **exit 0** when it is not - both DELETE and RENAME of the clone,
  verified across all five wired events. A venv created inside the clone is never the wired
  interpreter either (`hookwire.hook_python` rebases it to the venv's base interpreter, which
  survives the clone's deletion). `nevertwice.hosts` gained schema 2: `STATES` adds `"dead"` -
  a wired entry whose command names a file that is gone now reads as `dead`, with a detail
  that says whether the specific failure blocks the agent (an old-style entry or a missing
  shim script does; a shim whose engine went missing does not) - not silently `wired` forever,
  which told the owner nothing was wrong. `nevertwice-hosts` prints `[dead]`.
  `install.py --uninstall` now removes the shim along with the hook entries it always removed.
  See `docs/CONFIG.md` (`NEVERTWICE_CLAUDE_SETTINGS`) and QUICKSTART.md §5.
- **Two run-count sentences lagged the pooled Zep arm.** `docs/BENCHMARKS.md` said "the other arms are
  one run each" and `README.md` counted Zep/Graphiti among the arms kept as last measured and
  restamped, after that arm had been re-run twice on a flushed FalkorDB and pooled. Word claims
  about runs and gates are what `_test_decimal_drift.py` cannot see; a sweep of the governed and
  stand pages for the class found no other.
- **The cause-table caption names the absorb in outside words.** "The old statement survives on disk,
  unserved" read as a storage detail; the caption now says that the note stays on disk, that what it
  hands back is the other fact, and that this one is no longer served - and its docstring no longer
  says the competitor stores were read for ours alone.
- **The as-of caption was a constant.** `tools/render_claims.py` printed "the gate written before the
  run was 0.80 on both days, and this is below it" under the as-of table - a sentence written when
  the gate was missed and never compared again, so it went on asserting a miss on the campaign of
  2026-09-11, which met the gate exactly at its threshold (both days 0.800 [0.720, 0.862], old day
  0.892 against 0.85, runs 0.867 and 0.733). The verdict is now computed from the claims
  (`asof_verdict`) and names the spread and the interval, because a value on its threshold is a
  boundary, not a margin. No other renderer carried a typed verdict.
- **Two metrics under one name.** The supersession table's third column printed our over-retraction
  proper (the memory retired a still-true fact, read from the store: 0.000) beside the other arms'
  broad control-miss rate (the still-true fact did not come back, for any cause), both labelled
  "retires a still-true fact". The register now carries `control_miss_rate` for every arm - the
  comparable column, on which we are the worst row (0.225 against the floor's 0.050) - and
  `over_retraction_rate` only where a store records a retirement; a second generated table splits
  each arm's misses into retired / never written / written but below the top five, with "not read"
  where the run did not inspect that arm's store. Six mislabeled competitor claims were removed.
- **A p-value that shrank below its printed precision restored as `0.00`.** `remeasure.reformat`
  now switches to the power-of-ten form instead of printing a claim the artifact does not make.
- **The supersession bench read a demoted fact as a ranking miss** (`_store_state`, ledger K1b).
  The dilution probe (`research/facts_dilution_probe.py`, K1) found the `[facts]` block moves no
  rank at all - and found what the "written, live, ranked below the top five" control misses
  really were: the twin gate had judged session two's *different* fact a twin of session one's
  note, absorbed it, rewritten the description to the new fact and kept the old statement only
  under `## Previous statement`; the bench matched the marker anywhere in the file and called
  the note live. The store state is now read against the *served* text (title, description,
  prevention - what recall hands back) and a marker found only elsewhere in a live note is a new
  cause, **demoted**; over-retraction proper counts retired and demoted alike, for every arm
  (Mem0's UPDATE that drops the marker is its analogue). The K2 re-measurement carries the new
  split; the harvester's own defect the probe exposed - the hook's `Working directory:` line kept
  as a fact in nearly every note - has its gate in the ledger (K6) and waits for the marked cards.

- **The retrieval stand's embed fallback, the same on every arm.** Three of the non-oracle
  pool's 19,206 non-empty sessions exceed bge-m3's context even under the 28,000-character cap
  (Ollama answers HTTP 400, "the input length exceeds the context length"). Our arm skipped them
  silently - no vector, never retrievable - and each competitor store arm would have aborted
  its whole run on the first one, an hour into its ingest, because their loops caught nothing
  per item. Now every arm halves the text and retries, up to three times, and the artifact
  counts how many sessions that touched (`sessions_shrunk`).
- **A head-to-head run that retrieved nothing was published as a product's score.** A-MEM's
  full-pipeline arm read 0.000 at every k on the oracle pool (2026-09-06), and the row went into
  the artifact as A-MEM's number. The cause was ours: chroma asks an embedding function for
  `embed_query` at search time - its own base class defaults that to `__call__` - and the shim
  this stand substitutes so that every arm embeds with the same bge-m3 is a protocol
  implementation rather than a subclass, so the attribute was missing and A-MEM's
  `search_agentic` swallowed the error per query. The shim answers `embed_query` now, and
  `accept()` refuses to score any arm whose recall is zero at every k over a pool that contains
  the answers: the row becomes a blocker, the numbers are kept under `refused`, and the table
  says there is no row rather than printing a zero. No claim was ever registered from that run.

- **The A-MEM full-pipeline arm could not import the package it was written for.**
  `agentic_memory`'s `__init__` exports nothing; the class lives in `memory_system`. The arm
  reported "not installed" on a machine where it was. Its venv also needs the `ollama` client the
  package imports at construction. Found by a five-session smoke run before the real one.
- **Each head-to-head row now says when and at which commit it was produced** (`measured_at`),
  because rows are merged into one artifact across runs and a competitor arm is not re-run when
  only our engine changed; and our row says which way the tokenizer ran (`morphology`).

Fifteen defects in the engine, each with a test that fails without the fix - and, from the
review of 2026-09-05, eleven more, listed first.

- **The rollback generation of the embeddings cache was tracked by the vault's git.**
  `store_state` writes `<file>.prev` beside every JSON state file; `*.prev` was in no ignore
  list, so the live vault committed a 97 MB copy of the cache on every change, six commits
  deep, one commit short of GitHub's hard limit. `*.prev` is ignored everywhere now and the
  embeddings cache keeps no rollback copy at all - it is rebuildable, and each save was
  reading the primary and writing a third 97 MB file under the vault lock for nothing.
- **A guard that merely matched in three sessions could be promoted to blocking by a false
  positive.** `record_fired` wrote the delivering session into `seen_sessions`, which is the
  support list the outcome verdict seeds from. Delivery and support are two fields now;
  promotion needs feedback, as the design said.
- **After a compaction the same advisory stayed silent for the rest of the session.** The
  per-session suppression is keyed on the session id, which survives compaction; PreCompact
  now forgets the deliveries as it already forgets the injected notes.
- **A re-mine that crossed midnight forked the session.** The delta read took its timestamp
  from the first event after the watermark, so the date, the session stem and the typed-note
  dates all moved and the same-session absorb missed its own notes. A re-mine keeps the
  session's own start.
- **The re-mine floor lost the end of a session for good.** Growth under one extractor window
  (12 kB of JSONL) never re-triggered, and the SessionEnd hook, both sweeps and `process_now`
  gate on the same predicate. The floor is one byte; the fork it guarded against is closed by
  the delta read and the stable timestamp instead.
- **An entity card dropped one project's note when two projects hit the same lesson on the
  same day.** The restatement collapse keyed on (slug, date, session) with a session field no
  producer ever set; the key carries the project and the session is read from the note.
- **`tools/repair_vault.py` renamed the wrong twin and never wrote the untagged note back.**
  Renaming the live note changed its slug and broke every link to it; the retired copy takes
  the suffix now, as the engine does on a collision. The foreign-tag repair stripped a tag
  only at the start of a line, detected it only in the frontmatter while the engine counts
  body hashtags, and computed the cleaned text without saving it.
- **The external-retrieval stand embedded the first 2,000 characters of each session on our
  side and the whole session on the competitors'.** `embed_text` caps its input for the
  per-prompt hot path; a LongMemEval session is 14,000 characters at the median. The stand
  now embeds whole sessions through the same endpoint, the vector cache carries the cap in
  its name and refuses a file built under another, and every retrieval figure measured under
  the cap is withdrawn until the re-run.
- **The stand's copy of the ranker had drifted from the engine's.** A signal's sole candidate
  scored z = 0.0 in the copy and 1.0 in the engine, under a docstring that said "identical".
  Inert on every question of both corpora; the stand calls the engine's function now.
- **The head-to-head stand fell back to a July result file when the vector cache was missing**
  and reported it as the current arm. It blocks instead.
- **Competitor arms were labelled as products they only partly were.** "LangMem" was
  LangGraph's store search without its memory manager; "A-MEM" was chromadb cosine without
  its LLM notes or link evolution; Mem0 ran with extraction off, undisclosed. The store arms
  are labelled as store arms, Mem0's mode is an arm of its own (`mem0_infer`), and
  `langmem_full` and `amem_full` run the products' own pipelines.

- **The extractor was grounded on the wrong project's vocabulary.** `collect_existing_tags`
  scanned the whole store, so a batch run handed one project the signature tags of whichever
  project held the most notes. Nine notes from an ML project acquired `quantum_computing` and
  began surfacing for a different project's queries while no longer matching their own.
- **A re-mined session re-read its transcript from byte zero.** The extractor's window is
  anchored to the end of the file, so growth slid it, the model saw a different document,
  invented different titles, and the old notes were retired as superseded by their own rename.
  Re-mining now reads only the region added since the recorded watermark: **77.8% fewer bytes**
  over eight growth stages, at identical coverage.
- **The delta reader threw away one whole event per re-mine.** It seeked to the watermark and
  discarded a line unconditionally, on the assumption that a byte offset lands mid-line. A
  watermark is the file size after a completed write, so it lands on a newline nearly every
  time. Found by measuring the mechanism, not by reading it.
- **The title cache crashed the second session of every sweep, and silently kept retired
  titles.** Making the dedup window date-aware changed the cache's entries from a slug to a
  `(date, slug)` pair in one of the three places that touch it. The appender kept writing a
  bare string, which the next date-aware read unpacked as a pair - `ValueError`, taking down
  the second session for a project in any single process: a sweep, `ingest.py`, the watch
  daemon, or `capture_session` twice in a row. The remover kept testing `slug in bucket`
  against a list of pairs, always false, so a note that had just been retired was still
  offered to the extractor as an existing title. The crash is the loud half; the silent one is
  the dangerous one.
- **A retired stem could be re-minted live under the same name**, overwriting the earlier
  retired note's history.
- **A rewrite could strip a mistake of its "how to avoid" half**, leaving the record of the
  error without the part that prevents it.
- **A re-labelled entity minted a third card** instead of updating its own.
- **One observation stored twice counted twice**, so duplication read as corroboration.
- **A re-mined session note dropped the notes an earlier pass had linked.**
- **The entity card's two counts did not say what each of them counted.**
- **`trigger` recorded whatever the payload happened to say** rather than the pipeline path
  the run actually took.
- **A guard could repeat itself in one session** because nothing recorded which session had
  already seen it.
- **The compacted context block lost the ability to show a hole** - counts and spans are now
  cumulative.
- **An absorb rewrite could reopen a fix that had already shipped.**
- **The store kept only one previous generation**; a bad write is now undoable.

### Research

- **The engine before J2b, measured under today's supersession bench (ledger K2, naryad B item 2).**
  The over-retraction cap J2b registered had a baseline read by a classifier that could not see the
  absorb, and the corrected classifier had none. The commit before J2b and the literal channel
  (`ef8120d`) was checked out into a worktree and run under HEAD's bench, two runs per corpus
  (`research/results/supersession_baseline_ef8120d{,_implicit}.json`; `tools/register_supersession.py
  --historical` registers such a family born withdrawn, citing nothing, pointers kept). The absorb is
  older than J2b - over-retraction proper 0.100 explicit / 0.175 implicit on the older engine against
  0.125 / 0.350 now - but on the implicit corpus it doubled with the package while never-written fell
  from eight to none and the stale rate halved: the cause of the control misses moved from the
  extractor's silence to over-consolidation, the total barely moved. The cap is missed on both
  engines once the metric is read correctly; whether the J2b mechanism stays now depends on the K7
  gate. `SUPERSESSION.md` corrects its own account of the mechanism: the same-day same-title rewrite
  in the write path, decided by the title alone, not the cosine twin gate.
- **The register re-measured on the two-metric bench (2026-09-11, ledger K2).** Every arm re-run
  after the last bench commit, extraction pinned deterministic; Zep/Graphiti twice on a flushed
  FalkorDB with per-run group namespaces (its earlier figures had been measured on graphs shared
  between the two corpora - Graphiti keeps one graph per group id). Ours reproduced the previous
  campaign to the digit: stale 0.033 / 0.067, current 0.975 / 1.000 on the explicit / implicit
  corpus, as-of both-days 0.800 (runs 0.867 / 0.733), `s0_retired_rate` 0.908 against the
  `--recent` control 0.917. Zep pooled over two runs: stale 0.317 (0.317 / 0.317) and 0.242
  (0.267 / 0.217), current 0.575 / 0.633, as-of both-days 0.367 (0.367 / 0.367). **Over-retraction
  proper, on the right metric, is not zero: 0.125 explicit and 0.350 implicit** - five and fourteen
  of forty control case-runs where the twin gate absorbed a different fact into the note and stopped
  serving the still-true one. The J2b cap (<= 0.07) is therefore missed and published; the fix is
  gated (K7) and waits for the marked cards. Every Zep control miss is *never written* (9 and 11 of
  40); Mem0 missed none. The `[facts]` block moved no rank on any control (K1, dilution not
  confirmed). K3: the eight as-of first sessions marked never written are the extractor's
  instability on a bare two-sentence fact, not its silence - captured alone six of eight write the
  note; three were silent in both runs, five in one only.

- **Phase V: the invariants track is NO-GO, on evidence rather than on a missing
  measurement.** Six mechanisms were built; one survived. `blast_radius` under
  `decidable-only` cleared all three of its out-of-sample gates on 27 repositories the frozen
  code had never seen. The complexity ratchet, `scale`'s static half and the three-heuristic
  union each failed theirs and were deleted. The end-to-end gate was then re-run out of sample
  at 150 benefit and 150 harm trials and is **not met**: 5 fixed against 2 broken on 87 usable
  pairs, p = 0.453. Nothing was promoted.

  Three findings worth carrying. **Recall generalises and precision does not** - recall moved
  by three ten-thousandths across repositories chosen to be as unlike the development set as
  possible, while precision fell 31% and every flag rate rose 43 - 86%. **Repository size
  governs precision** (Spearman ρ = −0.620, p = 0.00056), and the apparent domain effect does
  not survive residualising on it (p = 0.025 → 0.43). **A gate cleared by one thousandth was
  never cleared**: the union passed in sample at 0.049 against a 0.05 ceiling and reads 0.055
  out of sample.

- **The fine-tuned embedder does not resolve on external material.** +0.0278 recall@1 with an
  interval of [−0.005, 0.065]. Hard-negative mining, distillation, Matryoshka training and a
  64× capacity increase each failed their pre-declared thresholds and were deleted. `bge-m3`
  remains the default.

## [2.4.0] - 2026-08-26

The release that followed the 2026-08-24 external audit. Two thirds of it is machinery
for making this project's own claims checkable; the last third is research that
**narrowed** the central claim rather than confirming it. PyPI skips 2.3.0, which was
tagged on GitHub and never published.

### Added
- **The billable generator behind gate G8, and the two locks it sits behind.**

  F3's two frontier cells and F2's LLM session-summary arm were described as "built and scored -
  only the generator changes". The scoring half was true. The generator half was not: both entry
  points printed `GATED` and exited 2, and no API client existed anywhere in the repository.
  Nobody could have opened that gate by setting a key, which is a worse failure than an honest
  refusal because it reads like one command stands between you and the result.

  `research/_frontier.py` is the generator. It turns a prompt into a completion, reports what
  that cost, and is the one module here that can spend money - so the interesting part is
  everything it does *before* the call.

  **A key in the environment is not approval to spend.** This machine exports `ANTHROPIC_API_KEY`
  for ordinary work, and `tests/_test_capability_grid.py` drives the very entry point that can
  bill - so a single-lock gate would have charged the owner for checking that the refusal works.
  Spending needs the key **and** `NEVERTWICE_ALLOW_BILLABLE=1`, and the refusal names which half
  is shut rather than saying only "gated". The switch reads its *value*: `=0`, `=false` and `=no`
  are refusals, because a bare truthiness test on the string would have read all three as yes.

  **A frontier cell is not a rung of the ladder, and says so in its own artifact.** The local
  cells decode greedily at 70 tokens; a frontier cell has adaptive thinking, no settable
  temperature and a far larger ceiling. A gap between them confounds capability with decoding
  procedure. Every API cell carries that caveat, `params_b` is `None` rather than an invented
  size, and the rendered grid reports those rows beside the ladder instead of inside its spread.
  `frontier-a` and `frontier-b` are **slots**, repointable by environment variable, and the cell
  records the model that actually ran - a row labelled only `frontier-a` would be unreadable in
  six months.

  **The cost is computed, not guessed.** `python research/capability_grid.py --estimate` sends
  nothing and prints the exact input-token count from the real prompts, a worst case at the
  per-call ceiling and a typical case, against a price table that carries the date it was
  checked. An unpriced model reports `usd: null` with a reason, never `0.00` - reading "free" is
  the most expensive way to be wrong about a price.

  The F2 arm is renamed `session_summary_llm` when a model produces the summaries, and the model
  is held to the same twelve-word budget as the stub: a summariser given more room would be a
  different experiment wearing the same name. Whichever ran, the standing caveat says which -
  the stub reported under an LLM's name is the single most misleading thing that file could
  publish.

  `tests/_test_frontier.py`, 37 checks, no key and no network. Two mutations killed: dropping
  the approval switch from the gate, and softening the not-matched-decoding declaration.

- **The preregistration: what the confirmatory run will test, fixed before it runs (GOAL F8).**

  Everything in `research/` so far is exploratory. F1-F6 built the harnesses, measured what one
  corpus could measure, and repeatedly found less than the project had been claiming. That work
  *generated* these hypotheses and therefore cannot confirm them - the corpus that suggested a
  hypothesis is not evidence for it. `research/PREREGISTRATION.md` fixes the hypotheses,
  endpoints, decision rules and corpus requirements in advance; `research/preregistration.py`
  computes the one thing that cannot be written by hand.

  **H1 is registered as a hypothesis we expect to fail.** Against raw lexical recall the system
  has not been shown to help, and on the exploratory corpus its successes were a strict subset
  of that baseline's. Registering it anyway is the whole point: a claim that quietly disappears
  between exploration and write-up is the failure preregistration exists to prevent, and this
  project has already withdrawn one published headline.

  **H2 - complementarity with an existing linter - would reframe the contribution.** If it
  holds, the finding is not that this system beats the alternatives but that it reaches failure
  classes static analysis cannot. That is a smaller and far more defensible claim than the one
  the project started with, and it is the only hypothesis the exploratory corpus is adequately
  powered for.

  **The sample size is computed, exactly.** Power is marginalised over the discordant-pair
  distribution with an exact binomial throughout - never the chi-squared approximation, which at
  these counts reports power the design does not have. Three of the four hypotheses are
  underpowered at the exploratory corpus size, and publishing that *before* the run is what
  stops "we ran what we had and it was not significant" from being reported as evidence of no
  effect.

  The corpus requirements are preregistered too, because each is a way a result could be
  weakened afterwards. The binding one: **it must be written by somebody other than the author
  of the notes.** No amount of statistical care repairs a corpus where one person wrote both the
  failures and the situations heading toward them.

  Submission is gate **G7** and remains the owner's action. `tests/_test_preregistration.py`,
  103 checks: every hypothesis must have an endpoint, a test, and a falsification rule that is
  not its support rule; the losing hypothesis must be registered; and the document may not
  describe its own prior work as settled. Ten mutations, all killed - including dropping the
  losing hypothesis, swapping the exact test for the approximation, and deleting the
  different-author requirement.

- **The reproduction package - and the two irreproducibilities it found (GOAL F7).**

  `research/reproduce.py` regenerates every research artifact behind a claim the project still
  publishes, hashes the result, and reports which came back identical. The Dockerfile pins a
  base image **by digest** and installs nothing, because the core and every research script run
  on the standard library alone - a frozen environment here is a pinned interpreter and a copy
  of the repository, with no lockfile to drift and no index to go missing.

  **Running it immediately found two real defects, which is the point of building it:**

  - **A baseline was hash-seed dependent.** The extractive-summary arm sorted tokens by length
    and let ties fall in whatever order they came out of a *set*, so a different `PYTHONHASHSEED`
    produced a different summary and different counts for that arm. Under three seeds the F2
    artifact hashed three ways. The tie-break is now explicit, and the image pins the seed as
    well - both are cheaper than a result nobody can reproduce.
  - **Two artifacts were stale.** F1's and F2's outputs predated later fixes to their own code
    and had never been restamped. Nothing caught it, because these numbers are deliberately not
    registered as manifest claims and so the freshness ratchet never looked at them.

  Two design decisions keep the report honest. Artifacts that legitimately embed a **timing** are
  hashed in a canonical form with only the *declared* volatile fields stripped - a byte-hash over
  a file containing a latency could never match on another machine, and a package that reported
  permanent failure would teach its reader to ignore it. And the **scope rule is derived from
  the evidence manifest**, not hand-kept: every artifact behind a live claim must be listed, and
  if a withdrawn claim is ever revived its artifact stops being out of scope and the check fails
  until somebody lists it.

  Three artifacts are deliberately **not** reproduced, each named in every run with the reason:
  latency (machine-dependent), the capability grid (needs a GPU and local weights), and
  LongMemEval (a third-party dataset that is not committed - and every claim resting on it was
  already withdrawn). A package that quietly re-ran only the easy artifacts and printed "all
  reproduced" would be worth less than no package.

  **Building the image found a third defect, and it was a published claim.** "No third-party
  packages required" appeared in the Dockerfile, the runner's verdict and this changelog. It is
  true of the core and of most research scripts, and false of `forgetting.py`, which needs
  numpy. Nothing caught it until the container ran without numpy in it. The requirement is now
  *named*: in a bare image that artifact is **skipped with the reason**, exactly as a missing
  GPU or an uncommitted dataset is, and the suite's own check was rewritten - it had been
  enforcing the false claim.

  `tests/_test_reproduction.py`, 101 checks. Its own first version demanded that *every* `.json`
  under `research/` be in the manifest and found thirty that were not - the right alarm and the
  wrong rule, since those back only withdrawn claims.

- **The safety evaluation: what it costs when the system is wrong (GOAL F6).**

  Everything in F1-F5 asks whether the system helps. `research/harms.py` asks what it costs when
  it is wrong, which is the question a controller that interrupts an agent has to answer before
  anyone runs it. Six harms, each measured at the **shipped** threshold rather than at the
  flattering operating point the baseline comparisons used:

  - **Blocked-correct actions**, in two tiers, because they are different harms. An advisory
    guard costs attention; a *blocking* one costs the action, and only a guard corroborated by
    three distinct sessions can block. One merged number would overstate the mild harm and hide
    the severe one.
  - **Override burden** - and the page says out loud that the measured rate is high: at roughly
    one wrong interruption every ten turns, users learn to dismiss warnings unread, at which
    point the recall the system does have stops mattering.
  - **Stale-guard damage** - a guard for an already-fixed problem, declared a **lower** bound,
    since it assumes every session reports the failure clearly and a real one is overridden
    intermittently and survives longer.
  - **Privacy leakage** - none of the eight secret formats reached disk, and the page refuses to
    generalise from that: redaction is pattern-based, and the mitigation that does not depend on
    patterns is that the store never leaves the machine.
  - **Poisoned-memory acceptance** - derived from the committed artifact and cross-checked
    against the governed manifest claim, so this page cannot quietly disagree with what
    `research/POISONING.md` publishes.
  - **Recovery time**, with the tension stated rather than the good half: recovery from a wrong
    memory takes distinct sessions, and fifty overrides from *one* session leave a guard
    standing. Fast recovery and resistance to a single hostile session pull against each other,
    and this design chose resistance.

  The page ends by refusing to read as a clearance: it establishes that a safety evaluation
  exists and what it currently says, not that the system is safe to deploy unattended.

  `tests/_test_harms.py` (59 checks) and ten mutations, all killed. Three found real defects.
  The poisoning dimension looked for a key that does not exist and reported `None` while
  claiming it was measured - a null presented as a measurement. And twice, checking the report
  against *itself* proved nothing: a detector mutated to never fire left every internal relation
  consistent, and a hardcoded stand-in for the whole resistance probe passed every field check.
  Both are now **independently recomputed** by the suite against the live system, and the report
  is required to match what the system actually does.

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

### Fixed
- **Guard creation failed silently on a slow machine.** The ReDoS probe runs a candidate pattern
  in a subprocess under a hard timeout, which is the right design - a thread cannot be timed out
  because CPython's `re` holds the GIL through a catastrophic match, and a subprocess can be
  killed by the OS. But the timeout was a flat 0.6 seconds covering the **whole subprocess**,
  including Python's own startup.

  On a quiet machine startup is ~30 ms and the budget was never in danger. On a loaded Windows
  CI runner it exceeded 0.6 s by itself, so a perfectly safe pattern timed out, `make_guard`
  returned `None`, and guard creation failed **with no message at all** - which is precisely the
  failure the probe exists to prevent rather than cause. A user on a busy laptop would have hit
  the same thing and seen nothing.

  The budget now bounds the **match**, which is what it was always about: this machine's
  interpreter startup is measured once and the match budget added on top, capped so a real
  backtracker still cannot run forever. A rejection is logged. Found by CI going red on a run
  where nothing relevant had changed.

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

[2.4.0]: https://github.com/DonPlaton/nevertwice/releases/tag/v2.4.0
[2.0.0]: https://github.com/DonPlaton/nevertwice/releases/tag/v2.0.0
[1.1.0]: https://github.com/DonPlaton/nevertwice/releases/tag/v1.1.0
[1.0.0]: https://github.com/DonPlaton/nevertwice/releases/tag/v1.0.0
