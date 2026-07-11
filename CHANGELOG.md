# Changelog

All notable changes to Nevertwice. Format loosely follows [Keep a Changelog](https://keepachangelog.com);
versions are [semantic](https://semver.org). Dates are UTC.

## [Unreleased]

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
