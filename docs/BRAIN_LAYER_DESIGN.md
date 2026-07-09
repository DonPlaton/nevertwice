# Brain Layer - Design

Status: **implemented** (entity layer + profiles · entity cards · SQLite scale-tier ·
temporal/evolution · salience · invariant tests - all green). This doc is the contract the
implementation follows. Anything not consistent with the **Invariants** section is a bug, not a
feature. Guards live in `nevertwice/_test_brain*.py` (102 checks); the invariants are enforced by
`_test_brain_invariants.py` (separation · budget byte-parity on/off · privacy · opt-in).

## 1. Motivation

Nevertwice today is *operational* memory for a coding agent: one fact per file
(mistake / pattern / decision), auto-captured from finished sessions, injected
token-lean at SessionStart and per-prompt. It has no durable *world knowledge* - the
cross-session entities a user actually thinks in (for a researcher: papers, methods,
datasets, experiments; for a generalist: topics, people, works, ideas).

GBrain (Garry Tan) ships exactly such a "Brain" layer - a self-wiring entity graph -
but on a VC's ontology (people / companies / `invested_in`), backed by Postgres, and
fed by *manual* capture. We take the **idea**, not the implementation:

- **Re-ontologised** to the user's real world (research / general), not a VC's.
- **Auto-fed** from the sessions Nevertwice already mines - no manual capture.
- **Zero-dep / local / markdown** - no Postgres, no external service; SQLite (stdlib)
  is the only scale tier.

The payoff also serves the core mission (*agents burn context re-deriving what they
already knew*): one dense entity card replaces re-reading N files to reconstruct
"what do we know about method X across all projects".

## 2. Invariants (non-negotiable - the philosophy)

1. **Budget.** Hot-path injection (SessionStart card + UserPromptSubmit recall) stays
   char-budget-bounded (`INJECT_BUDGET_CHARS`). Byte-for-byte unchanged from today for
   a user who has the Brain layer OFF.
2. **Separation.** Brain / entity notes are **pull-only**. They are NEVER in the default
   SessionStart / UserPromptSubmit candidate set. They surface only via: explicit
   `memory_search`, an entity-card request, MCP, or the existing opt-in
   *budget-gated* relation-expand. Rationale: hundreds of entity notes must never
   dilute or evict the high-signal mistake/pattern notes from the injection.
3. **Zero-dependency.** Standard library only (`dependencies = []`). SQLite via stdlib
   `sqlite3` for scale. No Postgres / pgvector / external services / new pip deps.
4. **Local-first privacy.** Brain extraction obeys `LOCAL_ONLY` (per-project AND
   per-agent). Research IP can be pinned to local Ollama; cloud stays unreachable-by-code
   for gated projects/agents - same gate as session extraction.
5. **Opt-in.** The Brain layer is OFF by default. It turns on only per the onboarding
   profile. A default ("coding") user gets today's lean system, unchanged.

A token-budget regression test and a privacy test (below) enforce 1, 2, and 4 in CI.

## 3. Onboarding profiles

First run asks once: **"What will you use Nevertwice for?"** (multi-select):

| Profile     | Brain layer | Ontology enabled                                   |
|-------------|-------------|----------------------------------------------------|
| `coding`    | OFF (default) | - (sessions → mistakes/patterns/decisions only)  |
| `research`  | ON          | paper, method, architecture, model, dataset, benchmark, metric, task, concept, experiment, result, tool, venue, person |
| `general`   | ON          | topic, person, place, work, idea                   |

- Stored as `NEVERTWICE_PROFILE` (comma-separated) in config / vault config file; env
  override wins (CI/tests).
- `coding` and a Brain profile compose: you still get operational memory; the Brain
  layer is purely additive and pull-only.
- No prompt in non-interactive contexts (hooks/CI): absent profile ⇒ `coding`.

## 4. Features

### Entity layer (extends `graph.py`)
- First-class entity **types** beyond code symbols, gated by active profile:
  - research (wide): `paper`, `method`, `architecture`, `model`, `dataset`, `benchmark`,
    `metric`, `task`, `concept`, `experiment`, `result`, `tool`, `venue`, `person`
  - general:  `topic`, `person`, `place`, `work`, `idea`
- Typed **edge hints** (`config.RELATION_HINTS`, suggested not allow-listed): research -
  `cites`, `builds-on`, `extends`, `evaluated-on`, `trained-on`, `reproduces`, `refutes`,
  `outperforms`, `authored-by`, `submitted-to`; general - `relates-to`, `part-of`, `influenced-by`.
- **Extraction**: the SessionEnd / sleep-time extractor gains an entity pass, added to
  the prompt ONLY when a Brain profile is active. Recognises arXiv IDs, method/arch
  names, metric names, dataset names. Runs on the SAME backend routing (local Ollama for
  LOCAL_ONLY).
- **Storage**: entities + edges in the graph; entity notes live under a dedicated
  `Entities/` namespace **excluded from the default recall pool** (Invariant 2).

### Entity cards (generalise the project card)
- A distilled, regenerated card per first-class entity, aggregating every note/session
  touching it: what it is · where used (cross-project) · what reproduced/failed ·
  related entities · timeline.
- Generated sleep-time / on-demand; stored as markdown; **pull-only** (search / MCP /
  explicit request), never auto-injected. Reuses the project-card machinery.

### Temporal / evolution
- Each entity carries a timeline: first-seen, mentions over time, and how the take
  evolved (supersession chain over the entity's facts).
- Surfaced inside the entity card and on demand. Builds on the `research/`
  temporal-graph prototype.

### SQLite scale-tier
- Promote the optional SQLite FTS5 + vector index to the official scale path. Markdown
  stays the source of truth; SQLite is derived and rebuildable.
- Index entities/edges for fast faceted + graph queries. Verify hybrid recall stays
  fast at 10K-100K notes.

### Salience (sleep-time)
- `consolidate_memory.py` scores note salience as pure graph **centrality** (inbound relation
  edges + co-occurrence degree) - the signal ORTHOGONAL to recurrence, which the ranker already
  applies separately (folding recurrence into salience too would double-count it). Stamped into
  frontmatter, read as a gentle ranking nudge and a keep-over-archive prior. Runs weekly on local
  GPU - zero agent-context tokens; inert on an entity-less store.
- Contradiction detection: **optional / later** - supersession already covers most.

## 5. Out of scope (explicitly rejected)
- `think` synthesis command - marginal over the agent reading notes itself. Dropped.
- Life-ingestion channels (email / voice / calendar / mobile) - wrong layer, breaks
  focus + zero-dep + local.
- Postgres / pgvector, multi-user / OAuth / team, cloud reranker as default.

## 6. Implementation order
1. Entity layer (the foundation) + profile plumbing.
2. Entity cards.
3. SQLite scale-tier.
4. Temporal / evolution.
5. Salience.

Develop in this repo; keep the live deployment untouched until tests are green, then
redeploy. Tests (`pytest`) at every step.

## 7. Tests that enforce the invariants
- **Budget regression**: with a Brain profile ON and entities present, assert the
  SessionStart and UserPromptSubmit injection byte-size is within the budget and
  unchanged vs Brain-OFF for the same vault (Invariant 1 + 2).
- **Separation**: assert entity/Brain notes never appear in the default
  SessionStart/UserPromptSubmit candidate set (only via explicit pull).
- **Privacy**: Brain extraction for a LOCAL_ONLY project/agent never calls the cloud
  backend (reuse the existing `is_local_only` gate test).
- Per-feature unit tests for extraction, card generation, temporal chain, SQLite parity
  with the markdown truth.
