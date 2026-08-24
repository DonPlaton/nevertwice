# Nevertwice vs the field (2025-2026)

How Nevertwice compares to current long-term-memory systems for agents, and what to
borrow from each. Vendor benchmark numbers are self-published and mutually disputed;
the load-bearing sources here are the cited papers and docs.

<!-- comparison:snapshot-note -->
> **Snapshot.** Repository activity below was fetched from the GitHub API on **2026-08-24**. Capability rows are what each vendor documents, surveyed **2026-06**. The measured rows come from the head-to-head run in `research/head_to_head.json`, recorded **2026-07-05** - The date is when research/head_to_head.json was committed. The run itself recorded no timestamp, so this is the latest date the result can be attributed to, not the exact run date. Regenerate with `python tools/comparison_snapshot.py --fetch --write`.
>
> Retrieval recall@k and end-to-end answer accuracy are different axes and never share a table. Answer accuracy depends on the reader model as much as on the memory; ranking the two together would compare a retrieval pipeline against a retrieval pipeline plus an LLM.
<!-- /comparison:snapshot-note -->

## What each vendor documents

Read from each system's own documentation, papers and repository at the survey date
above. These are **claims**, not measurements - what was measured here is the next
section.

<!-- comparison:vendor-matrix -->
| System | Substrate | Retrieval | Temporal & contradictions | Agent-agnostic | Local & privacy | Deploy | source |
|---|---|---|---|---|---|---|---|
| **Nevertwice** | markdown + JSON under git; no DB/server | hybrid semantic (bge-m3 local) + lexical, calibrated score fusion (RRF fallback), recurrence-boost | supersession (→`Superseded/`) + RESOLVES edges + bi-temporal `as_of` | yes: hooks · MCP · ingest | fully local, $0; cloud only for opt-in extraction; secret redaction | files only | - |
| Mem0 | vector DB (graph on Pro) | hybrid dense+BM25+entity, rank-fused | ADD-only ("nothing deleted"); read-time decay | yes (SDK/MCP) | self-host or cloud; defaults to an OpenAI key | low (pip); local needs Ollama+Qdrant | [1](https://docs.mem0.ai/changelog) · [2](https://arxiv.org/abs/2504.19413) |
| Zep / Graphiti | temporal KG (Neo4j/FalkorDB) | hybrid + BFS graph traversal + rerankers | true bi-temporal (valid/invalid + created/expired); LLM invalidation, never deletes | Graphiti Py; Zep API/MCP | Graphiti self-host; Zep CE deprecated Apr 2025 → cloud | moderate (graph DB + LLM) | [1](https://github.com/getzep/graphiti) · [2](https://arxiv.org/abs/2501.13956) · [3](https://blog.getzep.com/beyond-static-knowledge-graphs/) |
| Letta (MemGPT) | git-backed markdown "MemFS" (Feb 2026); was Postgres+vector | self-editing in-context blocks + archival vector; sleep-time compute | agent rewrites blocks; git = versioned history, auto-commit per change | framework/runtime (some lock-in) | self-host or cloud | high: server + Postgres + volume | [1](https://www.letta.com/blog/context-repositories) · [2](https://www.letta.com/blog/sleep-time-compute) |
| A-MEM | ChromaDB + in-note links | vector + Zettelkasten autonomous linking | in-place note "evolution" (LLM rewrites linked notes); no version history | library (MIT) | fully local (Chroma+MiniLM+Ollama) | lowest (pip) | [1](https://arxiv.org/abs/2502.12110) |
| Cognee | vector + graph + SQL (file-based default) | graph-RAG, ~14 modes, LLM routing | event-time; bi-temporal via Graphiti backend | yes (MCP) | full local + Ollama | minimal (pip) | [1](https://github.com/topoteretes/cognee) |
| memanto (moorcheh) | closed "Moorcheh" engine (opaque store; `moorcheh-sdk` + on-prem Docker image); markdown export-only | proprietary "information-theoretic" single-query ("zero indexing"); 13 typed memory kinds | versioning + `--as-of`/`--changed-since` + `conflicts` + `daily-summary` | yes: `connect` to 8+ (Claude/Cursor/Codex/Windsurf/Cline/Goose/Copilot) | on-prem Docker (no key) but the engine is closed; cloud tier needs a Moorcheh API key | server: FastAPI `serve`/`ui` + Docker(+Ollama); pip | [1](https://github.com/moorcheh-ai/memanto) · [2](https://arxiv.org/abs/2604.22085) |
| Hindsight (vectorize.io) | server-side store behind a Docker service (API :8888 + web UI) | LLM-driven `retain` (fact/entity/temporal extraction + normalization) → `recall` | temporal facts; belief revision server-side | clients: pip `hindsight-client` / npm; REST | self-host Docker but defaults to an OpenAI key; managed Cloud tier | server: Docker + LLM key | [1](https://github.com/vectorize-io/hindsight) · [2](https://arxiv.org/abs/2512.12818) |
| LangMem / LangGraph | KV+vector BaseStore (Postgres/Redis) | vector + namespace filter | manager upsert/update/invalidate; procedural prompt-optimizer | core agnostic; persistence LangGraph-tied | self-host or platform | low SDK; DB for prod | [1](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/) |
| ChatGPT memory | cloud account | always-injected + opaque profile | edit/delete; auto-supersession ("Dreaming V3", Jun 2026) | no (account-locked) | cloud | n/a (managed) | [1](https://openai.com/index/chatgpt-memory-dreaming/) |
| Claude memory | CLAUDE.md (repo) · auto-memory (`~/.claude`, local) · API memory tool · claude.ai | CLAUDE.md/`MEMORY.md` always-injected; topic files model-read | agent-curated; no formal supersession engine | CLAUDE.md portable; rest locked | CLAUDE.md + auto-memory local | low (built-in) | [1](https://code.claude.com/docs/en/memory) |
| Cursor / Windsurf (→Devin) | repo rules + memories (Windsurf local `~/.codeium/...`) | semantic / model-judged | manual edit/delete; no contradiction engine | no (tool-locked); AGENTS.md portable | Cursor needs Privacy-Mode-off; Windsurf local | built-in | [1](https://agents.md/) |
| GitHub Copilot Memory | GitHub cloud (not repo files) | auto-extracted facts, validated vs current branch | 28-day auto-expiry; stale-guard | no (account-locked) | cloud | built-in | [1](https://docs.github.com/en/copilot/concepts/agents/copilot-memory) |
| OKF (Google/Anthropic draft) | markdown + YAML + git (format only) | n/a (interchange format, no engine) | optional `log.md`/`timestamp`; no conflict resolution | yes (portable) | both | none (a spec) | [1](https://github.com/GoogleCloudPlatform/knowledge-catalog) |
<!-- /comparison:vendor-matrix -->

### Repository activity

Pulled from the GitHub API, so the reader can see at a glance which of these is still
moving. Activity is not quality; it is the one thing a stale comparison table hides.

<!-- comparison:activity -->
| repository | stars | forks | last push | state | license |
|---|---|---|---|---|---|
| [Mem0](https://github.com/mem0ai/mem0) | 63,960 | 7,477 | 2026-08-24 | active | Apache-2.0 |
| [Zep / Graphiti](https://github.com/getzep/graphiti) | 30,263 | 3,064 | 2026-08-21 | active | Apache-2.0 |
| [Cognee](https://github.com/topoteretes/cognee) | 30,227 | 2,953 | 2026-08-24 | active | Apache-2.0 |
| [Letta (MemGPT)](https://github.com/letta-ai/letta) | 24,410 | 2,592 | 2026-08-23 | active | Apache-2.0 |
| [Hindsight (vectorize.io)](https://github.com/vectorize-io/hindsight) | 21,036 | 1,631 | 2026-08-24 | active | MIT |
| [memanto (moorcheh)](https://github.com/moorcheh-ai/memanto) | 1,836 | 626 | 2026-08-24 | active | MIT |
| [LangMem / LangGraph](https://github.com/langchain-ai/langmem) | 1,623 | 186 | 2026-08-11 | active | MIT |
| [A-MEM](https://github.com/agiresearch/A-mem) | 1,156 | 121 | 2025-12-12 | active | MIT |
| [Nevertwice](https://github.com/DonPlaton/nevertwice) | 1 | 0 | 2026-08-24 | active | MIT |
<!-- /comparison:activity -->

## Head-to-head on one stand: MEASURED (local, no paid key)

The "do you beat the leaders?" question deserves a controlled answer, not a marketing one. Here
it is. Every system ingests the **same 940 LongMemEval-oracle sessions**, answers the **same 500
human-annotated questions**, is scored on the **same recall@k metric** (`research/head_to_head.py`),
and uses the **same local embedder (bge-m3 via Ollama)**, so the table isolates the *memory
pipeline*, not the embedder. Competitors run **locally** (Ollama plus their own store; no OpenAI
key, no cloud).

Vendor blogs quote much higher LongMemEval numbers than any column below. Those come from other
protocols: answer accuracy with an LLM judge, oracle context handed to a reader, closed embedders,
sometimes different question subsets. This table is retrieval R@k with every variable pinned except
the memory pipeline itself, so compare methodology before headlines. `research/head_to_head.py`
reruns the whole stand, on the competitors' own packages, with one command.

### What could actually be run

<!-- comparison:verified -->
| System | outcome | ingest | query | what that means |
|---|---|---|---|---|
| **Nevertwice** | ran here | n/a | 53 s | no ingest step: the store is the repository of notes itself |
| Mem0 | ran here | 191 s | 120 s | run as `infer=False` (retrieval-only, one memory per session) so the comparison is retrieval against retrieval |
| LangMem / LangGraph | ran here | 229 s | 129 s | LangGraph InMemoryStore; no server needed for the stand |
| A-MEM | ran here | 180 s | 82 s | A-MEM's own ChromaDB vector store with Ollama embeddings |
| Zep / Graphiti | **could not be run** | - | - | needs Neo4j or FalkorDB in Docker; the adapter records the blocker rather than a fabricated number |
| Cognee | **could not be run** | - | - | needs a local graph/vector store plus an ingest+search adapter; the adapter records the blocker rather than a fabricated number |
| Letta (MemGPT) | *not attempted* | - | - | no `run_letta` adapter exists in research/head_to_head.py, so it was never put on the stand - it needs a server plus Postgres and a heavy per-session build, but that is a reason, not a result |
<!-- /comparison:verified -->

### Retrieval recall on that stand

<!-- comparison:head-to-head -->
| System (same bge-m3, same 500 questions, one run) | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| **Nevertwice** (calibrated fusion, shipped default, 0 deps) | **0.550** | **0.802** | **0.858** | **0.651** |
| Mem0 (`infer=False`, dense+BM25) | 0.478 | 0.758 | 0.846 | 0.603 |
| LangMem (LangGraph InMemoryStore) | 0.426 | 0.692 | 0.782 | 0.543 |
| A-MEM (ChromaDB) | 0.428 | 0.692 | 0.782 | 0.544 |
<!-- /comparison:head-to-head -->

**The honest reading:**

- **Nevertwice leads every column in the table above** - that is the whole finding, and the
  table states it; restating the figures here is how the two drift apart. The opt-in trained
  cross-encoder lifts top-1 further, but it was measured in the retrieval study rather than on
  this stand, so it is deliberately absent from a one-run table
  ([`docs/BENCHMARKS.md`](BENCHMARKS.md) has it).
- **The win is the fusion, not the embedder** (everyone here uses the same bge-m3). The popular
  reciprocal rank fusion that Nevertwice used to ship, and that Mem0 documents as `rank-fused`, discards the
  score magnitudes and so scores *below plain BM25*. Nevertwice now uses **calibrated score fusion**:
  z-normalise each signal and combine the magnitudes. The full study, including the ideas we tested
  and cut, is in [`research/RETRIEVAL_FUSION.md`](../research/RETRIEVAL_FUSION.md).
- **We are honest about what is and is not a moat.** Calibrated linear score fusion is classic
  information retrieval (CombSUM, 1994), not our invention; the contribution is measuring that it
  beats the reciprocal rank fusion Nevertwice itself shipped until 2026-07. The durable moat is the substrate: plain files,
  $0, fully local, Obsidian-readable, no server or vector DB - a combination none of the systems in
  the table above offered as of the mid-2026 survey.
  We also measured chunk-level late interaction (R@5 0.814) and deliberately did **not** ship it,
  because our distillation front-end already gives short notes the concentration it buys for long
  raw sessions (details in the study).

The rows that are not a number are not omissions. Zep/Graphiti and Cognee have adapters
(`run_zep`, `run_cognee`) that record the blocker instead of a fabricated result; bring up the
database, set the Ollama environment, and they fill in. Letta has **no adapter at all**, so it
was never put on the stand - that is a gap in this comparison, not a finding about Letta.

*Reproduce:* `python research/head_to_head.py --only=nevertwice,mem0,langmem,amem --save`
(needs the dataset + `pip install mem0ai ollama fastembed langgraph langchain-ollama chromadb`).

## Differentiators

1. **No-DB, git-versioned, human-readable substrate, and at least one funded system moved toward it in 2026.** Letta's Feb-2026 rebuild *abandoned its database for git-backed markdown edited by bash*; OpenAI Codex writes local files under `~/.codex/`; Google/Anthropic's **OKF draft independently specifies markdown+YAML+git**. Nevertwice got there earlier and more purely (Obsidian-readable, zero server), while Mem0/Zep/Letta-classic/Cognee/LangMem still need a DB or server for full features. OKF is only the *format*; Nevertwice is the *system* on top of it.
2. **Truly local + $0, including local embeddings.** bge-m3 on-device; only opt-in extraction touches cloud (with redaction + Ollama fallback). Mem0/Letta/LangMem default to an external key; ChatGPT/Claude.ai/Cursor/Copilot are account-locked.
3. **Hybrid semantic+lexical RRF with graceful lexical fallback when the GPU is busy.** Rare robustness; most hard-depend on an embed/LLM call.
4. **Explicit supersession with audit trail + RESOLVES edges + typed ontology** (mistakes/patterns/decisions): contradiction handling is the weakest area across the systems above; more structured than the generic "facts" of Mem0/ChatGPT.
5. **Cross-project knowledge transfer.** Most competitors scope to one project/user/thread.
6. **Built-in eval harness.** We found none shipped by the systems above as of the mid-2026 survey; the vendor benchmark scene is mutually disputed.
7. **Three independent on-ramps** (hooks + zero-dep MCP + ingest) with no framework runtime to adopt.

## Research-stage differentiators (2026-06): method not documented by the systems above

A cluster of mechanisms no shipping agent-memory system has (all in `research/`, with
honest scope notes; the production-facing ones are opt-in and off by default):

- **Retrieval as a calibrated posterior:** the ad-hoc salience stack derived as one
  conditional-logit model; the *fitted* form beats the hand-tuned weights (+0.07 R@1, ECE 0.004)
  and is interpretable. None of the systems above documented recall as a calibratable posterior at the mid-2026 survey.
- **Memory that learns what to remember:** an online contextual bandit (LinUCB) that updates
  retrieval weights from *implicit feedback* and recovers the offline optimum. The retrieval weighting
  documented by the systems above is **static**; this closes the dead loop (injection → did it help? → adjust).
- **Forgetting as submodular coreset selection:** budget-bounded keep-set with a 1−1/e coverage
  guarantee, against the recency/salience pruning the systems above document.
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
implementation (see the backlog table below; every item is done, 188 tests green).
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

Every item below is implemented and tested (188 checks green).
Effort tags kept for reference. ✅ = shipped.

**Tier 1: high value, matches what leaders ship**

| Idea | From / why | Effort |
|---|---|---|
| ✅ **Sleep-time consolidation:** LLM pass that dedupes, merges fragments, distills recurring mistakes → general patterns (episodic→semantic) | Letta sleep-time, ChatGPT Dreaming; the #1 absent leader feature | M |
| ✅ **Write-time contradiction detection:** embed new fact, fetch similar, LLM emits SUPERSEDES/CONTRADICTS, auto-retire loser | Zep invalidation, LangMem reconcile-on-write; kills stacked contradictions | M |
| ✅ **Time-decay + salience scoring** in RRF (type-weighted; TTL-archive untouched notes) | Mem0 decay, Copilot 28-day; stops monotonic growth | L |
| ✅ **Fact-vs-code staleness validation:** check referenced path/symbol still exists via `graph.json` before injecting | Copilot validate-vs-branch; precision win for *coding* memory | M |
| ✅ **Budget-aware injection + profile IDF cleanup:** cap injection tokens; fix noise words in learned profile | battle-test (start ≈644 tok; profile noise) | L |

**Tier 2: differentiating, builds on existing assets**

| Idea | From / why | Effort |
|---|---|---|
| ✅ **Activate bi-temporal:** real `valid_from/valid_to` frontmatter + point-in-time query in `memory_search` | Zep's headline edge; prototype already exists | M-H |
| ✅ **Graph-aware multi-hop retrieval:** 1-2 hop expansion over RESOLVES/SUPERSEDES/`[[links]]` after RRF (GraphRAG without a graph DB) | Zep/Cognee; edges already stored | M |
| ✅ **Autonomous Zettelkasten linking on ingest:** auto-propose `[[links]]` to top-k related notes (LLM-filtered) | A-MEM; turns flat notes into a navigable net | M |
| ✅ **Procedural loop:** synthesize highest-recurrence patterns into the always-injected project card / CLAUDE.md suggestions | LangMem prompt-optimizer; closes experience→behavior | M |
| ✅ **OKF format alignment:** `type` frontmatter, `index.md`/`log.md`, relative links | interop with any OKF agent + standards alignment | L-M |

**Tier 3: robustness, credibility, reach**

| Idea | From / why | Effort |
|---|---|---|
| ✅ **Benchmark on LongMemEval / BEAM** (NOT LOCOMO: academically discredited, BM25 ~94%) | comparability + credibility | M |
| ✅ **Memory-poisoning / provenance guard:** per-note source + confidence; reject injection-shaped extractions | 2026 ER-MIA security work | L-M |
| ✅ **Operationalize git for cross-machine + concurrent agents:** sync/merge convention, conflict rules | Letta MemFS proves git-as-substrate | M |
| ✅ **Surface age + recurrence in injected context** so the agent weighs stale vs fresh | cheap mitigation for the no-decay gap | L |
| ✅ **AGENTS.md interop:** emit/consume the standard so the project card is portable to Cursor/Windsurf/Copilot/Codex | the one genuinely cross-tool substrate (Linux Foundation, 60k+ projects) | L |

**The order they shipped in:** quick wins and interop first, then the consolidation, contradiction,
and graph features that close the biggest gaps, then benchmark and reach.

> Caveats: Mem0/Zep/Cognee/memanto benchmark numbers are vendor-self-published and
> disputed (the Mem0↔Zep LOCOMO war is unreconciled). **memanto's headline 89.8%
> LongMemEval / 87.1% LoCoMo are answer-accuracy** (retrieval + LLM), a *different
> axis* from the recall@k head-to-head above, and run on a **closed engine
> (Moorcheh - `moorcheh-sdk` + a proprietary Docker image)**, so they are not
> independently reproducible the way this table's local, same-embedder numbers are.
> Nevertwice's own answer-accuracy figure on the comparable axis (standard
> LongMemEval-oracle, gold context) is **0.788** with an open reasoning reader
> (deepseek-reasoner); a reader sweep walks it 0.61 → 0.68 → 0.75 → 0.79 with the memory
> held fixed, localizing the ~0.11 gap to memanto's 0.898 as reader-model strength on hard
> temporal/multi-session reasoning, not the memory - full decomposition (reader sweep, CoT
> effect, a negative result on retrieving more) in
> [`QA_ACCURACY.md`](../research/QA_ACCURACY.md). Mem0 (Apr-2026 rewrite)
> and Letta (Feb-2026 MemFS) changed architecture recently; classic papers no longer
> describe shipping behavior. Windsurf is now "Devin Desktop"; Memary is unmaintained.
