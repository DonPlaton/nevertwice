# Changelog

All notable changes to Nevertwice. Format loosely follows [Keep a Changelog](https://keepachangelog.com);
versions are [semantic](https://semver.org). Dates are UTC.

## [Unreleased]

### Added
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
