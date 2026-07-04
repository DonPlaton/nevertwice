# Nevertwice vs the field (2025-2026)

How Nevertwice compares to current long-term-memory systems for agents, and what to
borrow from each. Landscape as of mid-2026; vendor specifics shift fast (treat as a
map, not a datasheet). Vendor benchmark numbers are self-published and mutually
disputed; the load-bearing sources here are the cited papers/docs.

## Comparison

Axes: **Substrate · Retrieval · Temporal/contradiction · Agent-agnostic · Local & privacy · Deploy**

| System | Substrate | Retrieval | Temporal & contradictions | Agent-agnostic | Local & privacy | Deploy |
|---|---|---|---|---|---|---|
| **Nevertwice** | **markdown + JSON under git**; no DB/server | **hybrid** semantic (bge-m3 local) + lexical, **calibrated score fusion** (RRF fallback), recurrence-boost | supersession (→`Superseded/`) + RESOLVES edges + bi-temporal `as_of` | **yes**: hooks · MCP · ingest | **fully local, $0**; cloud only for opt-in extraction; secret redaction | **trivial, files only** |
| Mem0 | vector DB (graph on Pro) | hybrid dense+BM25+entity, rank-fused | **ADD-only** ("nothing deleted"); read-time decay | yes (SDK/MCP) | self-host or cloud; **defaults to OpenAI key** | low (pip); local needs Ollama+Qdrant |
| Zep / Graphiti | **temporal KG** (Neo4j/FalkorDB) | hybrid + **BFS graph traversal** + rerankers | **true bi-temporal** (valid/invalid + created/expired); LLM invalidation, never deletes | Graphiti Py; Zep API/MCP | Graphiti self-host; **Zep CE deprecated Apr 2025** → cloud | moderate (graph DB + LLM) |
| **Letta (MemGPT)** | **→ git-backed markdown "MemFS" (Feb 2026)**; was Postgres+vector | self-editing in-context blocks + archival vector; **sleep-time compute** | agent rewrites blocks; **git = versioned history, auto-commit per change** | framework/runtime (some lock-in) | self-host or cloud | **high**: server + Postgres + volume |
| A-MEM | ChromaDB + in-note links | vector + **Zettelkasten autonomous linking** | **in-place note "evolution"** (LLM rewrites linked notes); no version history | library (MIT) | fully local (Chroma+MiniLM+Ollama) | lowest (pip) |
| Cognee | vector + graph + SQL (file-based default) | **graph-RAG**, ~14 modes, LLM routing | event-time; bi-temporal via Graphiti backend | yes (MCP) | full local + Ollama | minimal (pip) |
| **memanto** (moorcheh, 2026) | **closed "Moorcheh" engine** (opaque store; `moorcheh-sdk` + on-prem Docker image); markdown **export-only** | proprietary **"information-theoretic"** single-query ("zero indexing"); 13 typed memory kinds | versioning + **`--as-of`/`--changed-since`** + **`conflicts`** + `daily-summary` | **yes**: `connect` to 8+ (Claude/Cursor/Codex/Windsurf/Cline/Goose/Copilot) | on-prem Docker (no key) but **engine is closed**; cloud tier needs **Moorcheh API key** | **server**: FastAPI `serve`/`ui` + Docker(+Ollama); pip |
| **Hindsight** (vectorize.io, 2026) | server-side store behind a **Docker service** (API :8888 + web UI) | LLM-driven `retain` (fact/entity/temporal extraction + normalization) → `recall`; "learn, not just remember" (opinion/belief formation) | temporal facts; belief revision server-side | clients: pip `hindsight-client` / npm; REST | self-host Docker but **defaults to an OpenAI key**; managed Cloud tier | **server**: Docker + LLM key |
| LangMem / LangGraph | KV+vector BaseStore (Postgres/Redis) | vector + namespace filter | manager upsert/update/invalidate; **procedural prompt-optimizer** | core agnostic; persistence **LangGraph-tied** | self-host or platform | low SDK; DB for prod |
| ChatGPT memory | cloud account | **always-injected** + opaque profile | edit/delete; **auto-supersession ("Dreaming V3", Jun 2026)** | no (account-locked) | cloud | n/a (managed) |
| Claude memory | CLAUDE.md (repo) · auto-memory (`~/.claude`, local) · API memory tool · claude.ai | CLAUDE.md/`MEMORY.md` always-injected; topic files model-read | agent-curated; no formal supersession engine | CLAUDE.md portable; rest locked | CLAUDE.md + auto-memory **local** | low (built-in) |
| Cursor / Windsurf(→Devin) | repo rules + memories (Windsurf local `~/.codeium/...`) | semantic / model-judged | manual edit/delete; **no contradiction engine** | no (tool-locked); AGENTS.md portable | Cursor needs Privacy-Mode-off; Windsurf local | built-in |
| GitHub Copilot Memory | **GitHub cloud** (not repo files) | auto-extracted facts, **validated vs current branch** | **28-day auto-expiry**; stale-guard | no (account-locked) | cloud | built-in |
| **OKF** (Google/Anthropic draft) | **markdown + YAML + git** (format only) | n/a (interchange *format*, no engine) | optional `log.md`/`timestamp`; **no conflict resolution** | yes (portable) | both | none (a spec) |

*Sources: [Mem0](https://docs.mem0.ai/changelog) · [arXiv:2504.19413](https://arxiv.org/abs/2504.19413); [Graphiti](https://github.com/getzep/graphiti) · [arXiv:2501.13956](https://arxiv.org/abs/2501.13956) · [bi-temporal](https://blog.getzep.com/beyond-static-knowledge-graphs/); [Letta MemFS](https://www.letta.com/blog/context-repositories) · [sleep-time](https://www.letta.com/blog/sleep-time-compute); [A-MEM arXiv:2502.12110](https://arxiv.org/abs/2502.12110); [Cognee](https://github.com/topoteretes/cognee); [LangMem](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/); [ChatGPT Dreaming](https://openai.com/index/chatgpt-memory-dreaming/); [Claude Code memory](https://code.claude.com/docs/en/memory); [Copilot Memory](https://docs.github.com/en/copilot/concepts/agents/copilot-memory); [AGENTS.md](https://agents.md/); [memanto](https://github.com/moorcheh-ai/memanto) · [arXiv:2604.22085](https://arxiv.org/abs/2604.22085); [Hindsight](https://github.com/vectorize-io/hindsight) · [arXiv:2512.12818](https://arxiv.org/abs/2512.12818); OKF SPEC.md (GoogleCloudPlatform/knowledge-catalog).*

## Head-to-head on one stand: MEASURED (local, no paid key)

The "do you beat the leaders?" question deserves a controlled answer, not a marketing one. Here
it is. Every system ingests the **same 940 LongMemEval-oracle sessions**, answers the **same 500
human-annotated questions**, is scored on the **same recall@k metric** (`research/head_to_head.py`),
and uses the **same local embedder (bge-m3 via Ollama)**, so the table isolates the *memory
pipeline*, not the embedder. Competitors run **locally** (Ollama plus their own store; no OpenAI
key, no cloud).

| System (same bge-m3) | R@1 | R@5 | R@10 | MRR | ingest | store |
|---|---|---|---|---|---|---|
| **Nevertwice: calibrated fusion (shipped default, 0 deps)** | 0.550 | **0.802** | **0.858** | 0.657 | n/a | files |
| **Nevertwice: + trained cross-encoder** (opt-in) | **0.614** | **0.826** | 0.858 | **0.712** | n/a | files |
| Mem0 (`infer=False`, dense+BM25) | 0.478 | 0.758 | 0.846 | 0.603 | 343 s | qdrant |
| LangMem (LangGraph InMemoryStore) | 0.426 | 0.692 | 0.782 | 0.543 | 229 s | memory |
| A-MEM (ChromaDB) | 0.428 | 0.692 | 0.782 | 0.544 | 180 s | chroma |
| Cognee · Zep/Graphiti · Letta | n/a | n/a | n/a | n/a | n/a | *blocked (below)* |

**The honest reading:**

- **Nevertwice leads on every metric.** Its shipped ranker reaches R@5 0.802 against Mem0's 0.758,
  R@1 0.550 against 0.478, R@10 0.858 against 0.846, and the best MRR in the table. The opt-in
  trained cross-encoder then takes top-1 to 0.614.
- **The win is the fusion, not the embedder** (everyone here uses the same bge-m3). The popular
  reciprocal rank fusion that most systems ship, and that Nevertwice used to ship, discards the
  score magnitudes and so scores *below plain BM25*. Nevertwice now uses **calibrated score fusion**:
  z-normalise each signal and combine the magnitudes. The full study, including the ideas we tested
  and cut, is in [`research/RETRIEVAL_FUSION.md`](../research/RETRIEVAL_FUSION.md).
- **We are honest about what is and is not a moat.** Calibrated linear score fusion is classic
  information retrieval (CombSUM, 1994), not our invention; the contribution is measuring that it
  beats the rank fusion the field actually ships. The durable moat is the substrate: plain files,
  $0, fully local, Obsidian-readable, no server or vector DB, which none of the competitors offer.
  We also measured chunk-level late interaction (R@5 0.814) and deliberately did **not** ship it,
  because our distillation front-end already gives short notes the concentration it buys for long
  raw sessions (details in the study).

**Still blocked (honest):** Cognee, Zep/Graphiti and Letta need a graph DB or Postgres server plus
a heavy per-session LLM graph build; the adapters are present (`run_cognee` / `run_zep`) and record
the blocker rather than a fabricated number. Bring up the DB and set the Ollama env and they fill in.

*Reproduce:* `python research/head_to_head.py --only=nevertwice,mem0,langmem,amem --save`
(needs the dataset + `pip install mem0ai ollama fastembed langgraph langchain-ollama chromadb`).

## Differentiators

1. **No-DB, git-versioned, human-readable substrate, and it's now where the field is moving.** Letta's Feb-2026 rebuild *abandoned its database for git-backed markdown edited by bash*; OpenAI Codex writes local files under `~/.codex/`; Google/Anthropic's **OKF draft independently specifies markdown+YAML+git**. Nevertwice got there earlier and more purely (Obsidian-readable, zero server), while Mem0/Zep/Letta-classic/Cognee/LangMem still need a DB or server for full features. OKF is only the *format*; Nevertwice is the *system* on top of it.
2. **Truly local + $0, including local embeddings.** bge-m3 on-device; only opt-in extraction touches cloud (with redaction + Ollama fallback). Mem0/Letta/LangMem default to an external key; ChatGPT/Claude.ai/Cursor/Copilot are account-locked.
3. **Hybrid semantic+lexical RRF with graceful lexical fallback when the GPU is busy.** Rare robustness; most hard-depend on an embed/LLM call.
4. **Explicit supersession with audit trail + RESOLVES edges + typed ontology** (mistakes/patterns/decisions): contradiction handling is the field's weakest area; more structured than the generic "facts" of Mem0/ChatGPT.
5. **Cross-project knowledge transfer.** Most competitors scope to one project/user/thread.
6. **Built-in eval harness.** Almost no competitor ships its own; the vendor benchmark scene is mutually disputed.
7. **Three independent on-ramps** (hooks + zero-dep MCP + ingest) with no framework runtime to adopt.

## Research-stage differentiators (2026-06): ahead of the field on *method*

A cluster of mechanisms no shipping agent-memory system has (all in `research/`, with
honest scope notes; the production-facing ones are opt-in and off by default):

- **Retrieval as a calibrated posterior:** the ad-hoc salience stack derived as one
  conditional-logit model; the *fitted* form beats the hand-tuned weights (+0.07 R@1, ECE 0.004)
  and is interpretable. No competitor frames recall as a calibratable posterior.
- **Memory that learns what to remember:** an online contextual bandit (LinUCB) that updates
  retrieval weights from *implicit feedback* and recovers the offline optimum. Every leader is
  **static**; this closes the dead loop (injection → did it help? → adjust).
- **Forgetting as submodular coreset selection:** budget-bounded keep-set with a 1−1/e coverage
  guarantee vs the field's recency/salience pruning.
- **Domain bridges:** replication-weighted bi-temporal memory for scientific claims (resists
  single-study hype, contradiction-aware); controllable **divergent/serendipitous** recall
  (relevance×novelty frontier); **rare-event** salience (the deliberate inverse of recurrence for
  tail-risk). These are application surfaces no general memory system targets.
- **Poisoning taxonomy + a shipped defense:** recurrence-gaming defeated by **distinct-session**
  counting; corroboration-gating for supersession-abuse/confidence-spoofing.

**Honest counter-balance** (see [`WEAKNESSES.md`](WEAKNESSES.md)): these are mechanism
results on synthetic/curated data, not external SOTA; and Nevertwice is still **behind** on an
LLM entity/relation **knowledge graph** (Zep/Cognee), a production **server/scale** story
(Letta/Zep), default **rerankers**, and recall **confidence under embedding compression**.

## Gaps vs leaders → **all addressed (2026-06-15)**

The gaps below are what Nevertwice lacked *relative to leaders*; each now has an
implementation (see the backlog table; every M-item is done, v3 188 tests green).
Kept here for context on *why* each feature exists.

## Gaps vs leaders (the original analysis)

1. **No sleep-time consolidation / LLM reflection.** Letta (sleep-time) and ChatGPT (Dreaming V3) run background passes that dedupe, merge and **distill episodic→semantic**. Nevertwice's weekly consolidation is dedup/compaction, not reflective synthesis. *Biggest gap.*
2. **Bi-temporal is only a prototype:** no `valid_from/valid_to` querying ("what was true on date X"). Supersession is transaction-time only.
3. **No graph traversal / multi-hop retrieval** (Zep BFS, Cognee graph-RAG). RRF is flat; the edges exist but aren't traversed.
4. **No write-time contradiction detection:** conflicts aren't caught at ingestion by comparing to similar existing notes.
5. **No decay / salience / forgetting:** recurrence-boost only; the store grows monotonically, stale notes keep ranking.
6. **No procedural-memory loop:** patterns are stored as reference text, not folded back into changed behavior (cf. LangMem prompt-optimizer).
7. **No fact-vs-code staleness validation** (Copilot validates vs the current branch): a refactor can leave a stale "decision" poisoning context.
8. **Extraction quality / memory-poisoning surface:** beyond secret redaction, no provenance/confidence scoring or injection guard (2026 ER-MIA work shows this is a live attack surface).
9. **Single-machine, single-user:** auto-memory isn't synced across machines or merged across concurrent agents (git is available but not operationalized).
10. **No standardized public benchmark numbers.**

## Improvement backlog: **DONE (all 15 implemented 2026-06-15)**

Every item below is implemented and tested (v1 + v2 + v3 = 188 checks green).
Effort tags kept for reference. ✅ = shipped.

**Tier 1: high value, matches what leaders ship**

| ID | Idea | From / why | Effort |
|---|---|---|---|
| ✅ **M-1** | **Sleep-time consolidation:** LLM pass that dedupes, merges fragments, distills recurring mistakes → general patterns (episodic→semantic) | Letta sleep-time, ChatGPT Dreaming; the #1 absent leader feature | M |
| ✅ **M-2** | **Write-time contradiction detection:** embed new fact, fetch similar, LLM emits SUPERSEDES/CONTRADICTS, auto-retire loser | Zep invalidation, LangMem reconcile-on-write; kills stacked contradictions | M |
| ✅ **M-3** | **Time-decay + salience scoring** in RRF (type-weighted; TTL-archive untouched notes) | Mem0 decay, Copilot 28-day; stops monotonic growth | L |
| ✅ **M-4** | **Fact-vs-code staleness validation:** check referenced path/symbol still exists via `graph.json` before injecting | Copilot validate-vs-branch; precision win for *coding* memory | M |
| ✅ **M-15** | **Budget-aware injection + profile IDF cleanup:** cap injection tokens; fix noise words in learned profile | battle-test (start ≈644 tok; profile noise) | L |

**Tier 2: differentiating, builds on existing assets**

| ID | Idea | From / why | Effort |
|---|---|---|---|
| ✅ **M-5** | **Activate bi-temporal:** real `valid_from/valid_to` frontmatter + point-in-time query in `memory_search` | Zep's headline edge; prototype already exists | M-H |
| ✅ **M-6** | **Graph-aware multi-hop retrieval:** 1-2 hop expansion over RESOLVES/SUPERSEDES/`[[links]]` after RRF (GraphRAG without a graph DB) | Zep/Cognee; edges already stored | M |
| ✅ **M-7** | **Autonomous Zettelkasten linking on ingest:** auto-propose `[[links]]` to top-k related notes (LLM-filtered) | A-MEM; turns flat notes into a navigable net | M |
| ✅ **M-8** | **Procedural loop:** synthesize highest-recurrence patterns into the always-injected project card / CLAUDE.md suggestions | LangMem prompt-optimizer; closes experience→behavior | M |
| ✅ **M-14** | **OKF format alignment:** `type` frontmatter, `index.md`/`log.md`, relative links | interop with any OKF agent + standards alignment | L-M |

**Tier 3: robustness, credibility, reach**

| ID | Idea | From / why | Effort |
|---|---|---|---|
| ✅ **M-9** | **Benchmark on LongMemEval / BEAM** (NOT LOCOMO: academically discredited, BM25 ~94%) | comparability + credibility | M |
| ✅ **M-10** | **Memory-poisoning / provenance guard:** per-note source + confidence; reject injection-shaped extractions | 2026 ER-MIA security work | L-M |
| ✅ **M-11** | **Operationalize git for cross-machine + concurrent agents:** sync/merge convention, conflict rules | Letta MemFS proves git-as-substrate | M |
| ✅ **M-12** | **Surface age + recurrence in injected context** so the agent weighs stale vs fresh | cheap mitigation for the no-decay gap | L |
| ✅ **M-13** | **AGENTS.md interop:** emit/consume the standard so the project card is portable to Cursor/Windsurf/Copilot/Codex | the one genuinely cross-tool substrate (Linux Foundation, 60k+ projects) | L |

**Suggested order:** M-15 → M-14 → M-3 → M-1 → M-2 → M-4 → M-6 → M-5 → M-9.
(Quick wins + interop first; then the consolidation/contradiction/graph features that close the biggest gaps; then benchmark + reach.)

> Caveats: Mem0/Zep/Cognee/memanto benchmark numbers are vendor-self-published and
> disputed (the Mem0↔Zep LOCOMO war is unreconciled). **memanto's headline 89.8%
> LongMemEval / 87.1% LoCoMo are answer-accuracy** (retrieval + LLM), a *different
> axis* from the recall@k head-to-head above, and run on a **closed engine
> (Moorcheh — `moorcheh-sdk` + a proprietary Docker image)**, so they are not
> independently reproducible the way this table's local, same-embedder numbers are.
> Nevertwice's own answer-accuracy figure on the comparable axis (standard
> LongMemEval-oracle, gold context) is **0.788** with an open reasoning reader
> (deepseek-reasoner); a reader sweep walks it 0.61 → 0.68 → 0.75 → 0.79 with the memory
> held fixed, localizing the ~0.11 gap to memanto's 0.898 as reader-model strength on hard
> temporal/multi-session reasoning, not the memory — full decomposition (reader sweep, CoT
> effect, a negative result on retrieving more) in
> [`QA_ACCURACY.md`](../research/QA_ACCURACY.md). Mem0 (Apr-2026 rewrite)
> and Letta (Feb-2026 MemFS) changed architecture recently; classic papers no longer
> describe shipping behavior. Windsurf is now "Devin Desktop"; Memary is unmaintained.
