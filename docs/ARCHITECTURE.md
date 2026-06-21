# Architecture

Anamnesis turns finished agent sessions into durable, recallable knowledge, stored
as plain markdown under git. There is no server and no database in the core path:
just files, embeddings, and a thin engine.

## Data flow

```mermaid
flowchart TD
    A[Agent session] -->|Claude Code hooks| H(memory_hook.py)
    A2[Other agent] -->|ingest.py / MCP| H
    H --> X{extract\n(cloud-first, local fallback)}
    X -->|redact secrets| N[Typed notes\nMistakes / Patterns / Decisions]
    X --> C[Per-project card\nContext/&lt;project&gt;.md]
    N --> E[Embeddings\nbge-m3, local]
    N & C & E --> S[(markdown + git store\n~/.anamnesis)]
    S -->|SessionStart| I1[Inject project card + relevant facts]
    S -->|UserPromptSubmit| I2[Task-aware recall by prompt text]
    S -->|on demand| I3[memory_search / MCP memory_search]
    I1 & I2 & I3 --> A
    S -.optional accelerator.-> Q[(SQLite index\nFTS5 + vectors)]
```

## Components

| Module | Role |
|---|---|
| `config.py` | Cross-platform, env-driven paths & settings (`ANAMNESIS_*`). |
| `memory_hook.py` | The engine: event dispatch, extraction, note writing, supersession, retrieval, injection, scheduling-safe locking. |
| `mcp_server.py` | Zero-dep MCP stdio server → any MCP client. |
| `memory_search.py` | On-demand recall (shared `search_core`). |
| `graph.py` | Entity + typed-relation knowledge graph over the notes (faceting, multi-hop, relation-aware recall, Mermaid/DOT/JSON export). Also the opt-in **Brain layer**: typed-entity index, per-entity timeline/evolution, and graph-centrality salience. |
| `remember.py` / `ingest.py` | Agent self-write / generic transcript ingestion. |
| `embed_index.py` | (Re)build the embedding cache. |
| `consolidate_memory.py` | Sleep-time dedup + compaction + recurrence; stamps Brain-layer salience. |
| `index_sqlite.py` | Optional SQLite scale-index (derived from markdown): FTS5 + vectors, plus the entity/relation graph tables for fast Brain-layer queries at scale. |
| `manage_tasks.py` / `install.py` | Scheduling (Windows tasks / POSIX cron) and setup. |
| `research/` | Eval harness, temporal-graph prototype, contradiction scan. |

## Knowledge model

- **Typed notes:** one fact per file: a *mistake* (with "how to avoid"), a *pattern*,
  or a *decision*. Filenames encode date + project + type + slug.
- **Supersession:** a newer note that restates/overrides an older one (same slug, or
  an explicit `supersedes`) retires the old note to `Superseded/`; recall sees current
  truth only.
- **RESOLVES edges:** a fix (decision/pattern) links to the bug it closed; the resolved
  mistake is flagged and de-emphasised in recall.
- **Project card:** a distilled, regenerated block (status · stack · open gotchas ·
  decisions · recurring) at the top of each `Context/<project>.md`; the high-signal
  injection surface.
- **Links:** notes cross-link with `[[wikilinks]]`; `graph.json` is the machine-readable
  graph. Obsidian can render both, but is not required.
- **Brain layer (opt-in):** a research/general profile turns the captured sessions into a
  self-wiring knowledge graph — typed entities, per-entity cards (a cross-project rollup),
  an evolution timeline, and graph-centrality salience. It is **pull-only**: stored under
  `Entities/` (never in the recall pool) and read on demand, so the token-bounded hot path
  is byte-for-byte unchanged when off. See [BRAIN_LAYER_DESIGN.md](BRAIN_LAYER_DESIGN.md).

## Retrieval

Hybrid **Reciprocal Rank Fusion** of a semantic ranking (bge-m3 cosine) and a lexical
ranking (token overlap), recurrence-weighted. The semantic side runs only if the local
embedder answers a fast ping, so a busy GPU degrades gracefully to lexical, then to
recency; recall never blocks. An optional cloud-judge **rerank** can reorder the top
candidates for deliberate on-demand search.

## Privacy

Embeddings are local (Ollama). Extraction is cloud-first (configurable, e.g. a
zero-retention provider) or fully local. Secrets are regex-redacted before anything is
written or sent. Per-project routing (`ANAMNESIS_LOCAL_ONLY` / `ANAMNESIS_CLOUD_ONLY`)
keeps sensitive projects off the network entirely.
