# Nevertwice: Active, Token-Budgeted Long-Term Memory for AI Coding Agents

**Platon Chernov**
`https://github.com/DonPlaton/nevertwice`

> **Working draft (v0.2, 2026-07).** Targets Zenodo → arXiv. Numbers below are produced
> by the scripts in `nevertwice/research/` and regenerate from one command each; this
> manuscript is assembled from those studies. arXiv submission is pending an endorsement;
> the Zenodo deposit can go out independently.

## Abstract

Long-term memory should make an agent improve with use, yet every deployed system implements
memory the same way — retrieve past text and inject it into the prompt — which taxes every turn
and, we show, is bottlenecked by the **reader LLM**, not the memory: on LongMemEval-oracle,
answer-accuracy climbs 0.61 → 0.79 as we upgrade only the reader with the memory held fixed. We
present **Nevertwice**, which reframes memory as a set of **token-budgeted interventions** that
stay silent until they have something worth saying, and then *act*: an executable **guard**
compiled from a past mistake that fires *before* it repeats (zero context tokens until it does), an
**anticipatory** warning triggered by resemblance to a past failure, and a **counterfactual**
("what breaks if I change X?") answered from an induced causal graph rather than an episode dump.
On a longitudinal benchmark we introduce — **improvement-per-token**, the metric the field does not
measure — active interventions match the error-prevention of the field's always-inject design for
**~31× fewer tokens**, a net token *saving*; and in a paired live study on a real model (DeepSeek)
a fired guard cuts the real error rate **0.36 → 0.05 (−86%)**, an effect that scales monotonically
with agent capability. Underneath the interventions, Nevertwice keeps the substrate the field is
converging toward — **plain Markdown under git**, zero required dependencies, fully local, with the
engine and every evaluation **open and reproducible** — and on it the shipped ranker out-retrieves
the hosted leaders (recall@5 **0.80** vs Mem0's 0.76) and answers **0.788** on standard
LongMemEval-oracle. The contribution is a memory that **acts** rather than merely recalls, measured
end to end, on a substrate the user owns.

## 1. Introduction

An AI coding agent without memory rediscovers the same gotchas every session. The past two
years produced a wave of "memory for agents" systems — Mem0, Zep/Graphiti, Letta (MemGPT),
A-MEM, Cognee, LangMem, and most recently memanto — that store facts across sessions and
inject them back. Almost all share three properties the user rarely examines: (i) the store
is a **vector database or a proprietary engine**, not something the user can read; (ii) the
system **defaults to a hosted service** or an external LLM key, so the user's work leaves
the machine; and (iii) the quality claim is a **self-published benchmark number** that is
mutually disputed across vendors and, for closed engines, not independently reproducible.

Nevertwice is built on the inverse of each:

1. **Substrate.** Memory is plain Markdown files with YAML frontmatter under git —
   readable in any text editor or Obsidian, greppable, diffable, and portable. There is no
   database and no server. (This is, notably, where the field is independently converging:
   Letta's Feb-2026 rebuild abandoned its database for git-backed Markdown, OpenAI Codex
   writes local files, and a Google/Anthropic draft specifies Markdown+YAML+git as an
   interchange format.)
2. **Locality.** Extraction runs on a local model (Ollama) or one optional cloud key with
   secret redaction and per-project local-only routing; embeddings are local (bge-m3);
   retrieval needs no network. The default install ships nothing to anyone.
3. **Reproducibility.** The retrieval engine is a few hundred lines of open Python; every
   number in this paper regenerates from a script on a public dataset, including the ones
   that argue *against* shipping a feature.
4. **Action, not just recall.** Because the retrieval axis is reader-bound and commoditizing
   (§6.3), the durable contribution is not a better ranker but a different *shape* of memory:
   interventions that stay silent until they earn a place in the context and then act (§5). This
   is where memory stops being a library the agent reads and becomes a faculty that changes what
   the agent does — measured in errors prevented per token, not facts recalled.

This paper describes the system (§3), the memory-ranking mechanisms we studied (§4), **Active
Memory** — the intervention layer that is this work's main contribution (§5) — a reproducible
evaluation against the field (§6), and is candid about limitations (§8).

## 2. Related work

We group the landscape by substrate and contrast it with Nevertwice; a continuously updated
table is in the repository's `docs/COMPARISON.md`.

- **Vector-database memory.** *Mem0* stores facts in a vector DB (graph on its Pro tier)
  with hybrid dense+BM25 retrieval; it is add-only and defaults to an OpenAI key. *A-MEM*
  pairs ChromaDB with autonomous Zettelkasten linking and in-place note "evolution".
  *LangMem* uses a LangGraph KV+vector store. These are strong systems but require a vector
  store and, typically, an external embedder/LLM.
- **Temporal knowledge graphs.** *Zep/Graphiti* builds a bi-temporal knowledge graph in
  Neo4j/FalkorDB with BFS traversal and LLM-based invalidation — the most sophisticated
  temporal model in the field, at the cost of a graph database and a per-session LLM graph
  build.
- **Self-editing context.** *Letta (MemGPT)* manages in-context memory blocks with
  sleep-time consolidation; its 2026 rebuild moved to git-backed Markdown edited by the
  agent, validating the file-substrate thesis.
- **Closed-engine memory.** *memanto* (2026) is the closest recent system in framing — an
  "active memory agent" with typed memories, temporal queries, and a `conflicts` command —
  but its retrieval runs on the **proprietary Moorcheh engine** (`moorcheh-sdk` plus an
  on-prem Docker image; the cloud tier needs a Moorcheh API key). It reports strong
  headline accuracy (89.8% LongMemEval, 87.1% LoCoMo), but because the engine is closed and
  the methodology undisclosed, the number cannot be independently reproduced.

Nevertwice differs on the axis none of these occupy: an **open, no-database, no-server,
local file substrate** with the retrieval engine and the full evaluation in the open. Where
others publish a percentage, we publish the harness.

## 3. System

**Capture → distill.** A finished agent session (a Claude Code hook, an MCP call, the
`ingest` CLI, a directory sweep, or — new in this work — a `.pdf`/`.docx`/`.md` document)
is passed to an extractor (local Ollama or one cloud key) that emits typed lessons:
**mistakes** (with a prevention), **patterns**, and **decisions**, plus a per-project
"card" (status, stack, open gotchas). A relevance gate keeps off-topic sessions from
polluting a project's memory, and a secret-redaction pass plus an injection-shaped-payload
filter run before anything is written.

**Substrate.** Each lesson is one Markdown file with frontmatter (`date`, `project`,
`type`, `tags`, optional `supersedes`/`entities`/`relations`) in `Patterns/`, `Mistakes/`,
or `Decisions/`. Writes are atomic (temp + `os.replace`) and auto-committed to git, so the
memory has a full version history for free.

**Retrieval.** Recall fuses two signals over the store: semantic cosine on local bge-m3
embeddings and lexical BM25. The fusion is the load-bearing detail (§6.2): rather than the
reciprocal-rank fusion most systems ship, Nevertwice uses **calibrated score fusion** —
z-normalize each signal and combine magnitudes — behind a **calibrated abstention gate** so
a nonsense query returns *"no confident match"* rather than a confident wrong memory. When
the GPU is busy, retrieval degrades to lexical-only rather than failing.

**Consistency.** When a new lesson supersedes an old one, the loser is retired to a
`Superseded/` subfolder with a `superseded_by` stamp (write-time contradiction resolution),
so live recall never surfaces stacked contradictions while the full history remains
queryable. Two read-only review commands surface this: `conflicts` (the supersession
ledger) and `digest` (a windowed "what changed" rollup).

**Knowledge layer.** An optional, pull-only entity/relation graph types each lesson's
entities (tools, files, concepts; in a research profile, papers/methods/datasets/…) and
exposes per-entity cards, timelines, and graph-centrality salience — surfaced only on
explicit request, never auto-injected, so the session-start budget is unchanged whether the
layer is on or off.

**Interfaces.** Four on-ramps with no framework runtime to adopt: Claude Code hooks
(zero-config), a zero-dependency MCP server (six tools), the `ingest` CLI, and a stable
in-process Python API; LangChain and LlamaIndex adapters wrap the same read/write path.

## 4. Memory-ranking mechanisms (research)

A cluster of mechanisms, each a write-up plus a runnable script and a result file in
`nevertwice/research/`. The production-facing ones are opt-in and off by default; the rest
are studies that informed the design. The honest-measurement stance is the point: we report
the ones that lost as prominently as the ones that won.

- **Retrieval as a calibrated posterior.** The ad-hoc salience stack is re-derived as one
  conditional-logit model; the fitted form beats the hand-tuned weights (+0.07 recall@1,
  expected-calibration-error 0.004) and is interpretable — recall framed as a calibratable
  posterior rather than a tuned heuristic.
- **A memory that learns what to remember.** An online contextual bandit (LinUCB) updates
  retrieval weights from implicit feedback and recovers the offline optimum, closing the
  loop (inject → did it help? → adjust) that every static system leaves open.
- **Forgetting as submodular coreset selection.** A budget-bounded keep-set with a
  1−1/e coverage guarantee, versus the field's recency/salience pruning.
- **Bi-temporal supersession.** `valid_from`/`valid_to` frontmatter supports point-in-time
  queries ("what did we believe on date X"); on an internal temporal-QA task the
  bi-temporal model resolves the then-current fact where a flat "use newest" store cannot.
- **A memory-poisoning taxonomy and defense.** Recurrence-gaming is defeated by
  distinct-session counting, and supersession-abuse/confidence-spoofing by corroboration
  gating; the guard produced **0 false-positives on a 328-note vault**.

## 5. Active Memory: interventions that earn their tokens

The retrieval work in §4/§6 makes Nevertwice a better *library*. This section is the different
idea: a memory that **acts**. The motivation is empirical — §6.3 shows the retrieval/answer axis
is **reader-bound** (answer-accuracy walks 0.61 → 0.79 with the reader while the memory is fixed),
so out-retrieving a competitor is a race the LLM keeps winning. The durable move is to change what
memory *does*, and to make it cost almost nothing until it pays off.

### 5.1 The intervention primitive

v1 memory is a thing the agent **reads**: it injects recalled text every turn, a fixed token tax.
v2 memory is a thing that **acts**, and is **silent until it has something worth saying**. The unit
is the *intervention* — `(trigger, action, cost, value, feedback)` — and memory fires only those
whose expected value exceeds their token cost. The default token cost of memory drops from
"hundreds every turn" to **zero until an intervention earns its place**. Token economy stops being
a constraint and becomes the selection pressure. Three action types:

- **A — Guards (controller).** A high-recurrence mistake compiles into a tiny scoped, executable
  check (a safe regex over the code/command about to be written). It fires *before* the mistake
  repeats, at **zero context tokens until it fires**, then one line. Guards are **Popperian**: born
  *advisory* (warn, never block), promoted to *blocking* only after K distinct-session
  corroborations, **self-retiring** after M false positives, and always overridable — the override
  is itself feedback that narrows the guard. A wrong guard dies on contact with reality, so the
  agent is never ossified. Guards run automatically on the pre-tool hot path (before an edit or
  command) and are exposed to every agent over MCP.
- **B — Anticipation.** Where a guard matches a literal pattern, anticipation fires on *resemblance*
  of the current trajectory to a past failure — catching a novel manifestation a regex misses. It
  surfaces **one** precise warning above an adaptive threshold, or stays silent; the threshold is
  Popperian (a cry-wolf failure mode has its bar raised, capped so an overwhelming signal always
  breaks through). Lexical by default (0-dep), with an optional embedding blend for recall.
- **C — Counterfactual.** *"What breaks if I change X?"* is answered from a causal model **induced
  from the store's own typed relation edges** (`causes`, `depends-on`, `part-of`, …), oriented into
  a single impact direction and traversed downstream, plus the failure modes mistakes attach to X.
  The output is a synthesized consequence list, **not** the underlying notes — on a real 2.2k-note
  vault it induces a 507-node / 1014-edge impact graph and answers a full counterfactual in ~300
  tokens versus ~2,250 to dump the notes that mention the entity (**~7× cheaper**).

### 5.2 Improvement-per-token: the metric the field does not measure

Retrieval recall and answer-accuracy both ask "can memory surface a fact." For an *agent's*
memory the question is whether the agent **gets better over a series of related tasks, net of what
memory costs.** We introduce a longitudinal benchmark (`research/longitudinal_improvement.py`)
that runs a task family (recurring pitfalls) under three arms — no memory, v1 always-inject, and
v2 active — with identical knowledge, and scores **errors prevented per token**. Over a 200-task
family (25 seeds):

| arm | errors | memory tokens | total vs no-mem | improvement / 1k tok |
|---|---|---|---|---|
| no memory | 46.9 | 0 | — | — |
| v1 always-inject | 16.7 | 80,000 | **+55,840 (net cost)** | 0.38 |
| **v2 active (guards)** | 17.6 | **2,581** | **−20,811 (net saving)** | **11.33** |

Both memory arms prevent ~the same errors; v2 delivers it for **31× fewer tokens** and is a *net
token saving* (prevented rework outweighs the tiny guard cost), while always-inject's tax makes
memory a net *cost*. A sensitivity sweep confirms v2's edge shrinks honestly as the guard
false-positive rate rises (Popperian retirement trades prevention for safety) but never inverts in
the tested regimes.

### 5.3 Does it work on a real model? A live study

The simulation assumes one thing: that a fired guard changes a real model's output. We measured it
(`research/live_validation.py`) — 12 coding micro-tasks with objective, execution-free checks, run
on DeepSeek paired without-vs-with the guard. The guard cut the real pitfall rate **0.36 → 0.05
(−86%)**; the measured effect (`eff = 0.88`) *exceeds* the 0.75 the simulation assumed, so the
simulation was conservative. The help concentrates exactly where the thesis predicts — on
**project-specific constraints the model cannot know from training** (invented API contracts;
eff 0.79) — while doing no harm on textbook pitfalls a strong model already avoids.

Sweeping the reader across four models of increasing capability (3B → 4B → 7B → DeepSeek) yields a
monotone curve — `eff` 0.63 → 0.76 → 0.85 → 0.88 — with a **base error rate that stays flat**
(~0.4): stronger models do not err less on these tasks, they **apply the delivered memory better**.
On project-specific knowledge the curve jumps sharply between 4B and 7B (0.47 → 0.78), a capability
threshold below which the model often cannot act on a fact even when told. The honest reading:
**memory removes the *knowledge* bottleneck, not the *capability* one** — a stronger agent is worth
*more* memory, not less, the same "the reader is the variable" lesson as the retrieval axis.

## 6. Evaluation

All retrieval numbers use **LongMemEval-oracle**, global-pool variant: 940 unique sessions
in one shared store, 500 human-annotated questions, scored on the same recall@k metric with
the same local embedder (bge-m3) for every system, so the comparison isolates the memory
pipeline, not the embedder. Competitors run locally (their own store + Ollama, no paid key).

### 5.1 Retrieval head-to-head

| system (same bge-m3) | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| **Nevertwice — calibrated fusion (shipped, 0 deps)** | 0.550 | **0.802** | **0.858** | 0.657 |
| **Nevertwice — + trained cross-encoder (opt-in)** | **0.614** | **0.826** | 0.858 | **0.712** |
| Mem0 (`infer=False`, dense+BM25) | 0.478 | 0.758 | 0.846 | 0.603 |
| LangMem (LangGraph store) | 0.426 | 0.692 | 0.782 | 0.543 |
| A-MEM (ChromaDB) | 0.428 | 0.692 | 0.782 | 0.544 |

Nevertwice's shipped ranker leads every metric; the opt-in trained cross-encoder lifts top-1
to 0.614. (Zep/Graphiti, Cognee and Letta need a graph DB or Postgres plus a per-session
LLM build; the adapters record the blocker rather than a fabricated number.)

### 5.2 The win is the fusion, not the embedder

Every system above uses the same bge-m3. The popular reciprocal-rank fusion discards score
magnitudes and scores *below* plain BM25; calibrated score fusion (z-normalize, combine
magnitudes) lifts recall@5 from **0.66 to 0.80**. This is classic information retrieval
(CombSUM, 1994), not a novel algorithm — the contribution is *measuring* that it beats the
rank fusion the field actually ships.

### 5.3 End-to-end answer accuracy (this work)

Retrieval recall is not what vendors headline; **answer-accuracy** is (read the evidence →
answer → LLM-judge against the gold). We add a reproducible harness (`qa_eval.py`) and
report it with the two things headlines omit stamped in: the reader/judge model and the
retrieval setting. The dataset's per-question haystack equals its gold evidence
(`haystack_session_ids == answer_session_ids`, verified for all 500), so our **oracle**
setting (gold context) **is the standard LongMemEval-oracle protocol** — the
directly-vendor-comparable number — while our **retrieved** setting is a deliberately
harder variant that pools all 940 sessions and makes the ranker find each question's ~2
gold among 938 distractors (standard LongMemEval gives each question only its own ~2).

With an open reasoning reader (`deepseek-reasoner`, judge held at `deepseek-chat`):

| setting | overall | single-user | single-asst | preference | multi-session | temporal | knowledge-update |
|---|---|---|---|---|---|---|---|
| **oracle** (= standard LongMemEval-oracle) | **0.788** | 0.957 | 1.000 | 0.667 | 0.812 | 0.571 | 0.859 |
| retrieved (our harder global-pool variant) | 0.464 | 0.586 | 0.929 | 0.267 | 0.308 | 0.338 | 0.577 |

On the axis that matches a vendor headline, Nevertwice answers **78.8%**. The single-session
ceilings (0.96–1.00) confirm the store surfaces the answer cleanly; what remains below 1.0
is reasoning difficulty (temporal date arithmetic, cross-session synthesis), where the
*reader model* is the limiter, not the memory.

**The bottleneck is the reader, not the memory — shown by a sweep.** Holding the memory and
the judge fixed and upgrading only the reader (and the answer format), on the same questions:

| reader (judge = deepseek-chat) | mode | oracle |
|---|---|---|
| `qwen3:30b-a3b` (local, no key) | terse JSON | 0.614 |
| `deepseek-chat` | terse JSON | 0.678 |
| `deepseek-chat` | chain-of-thought | 0.748 |
| `deepseek-reasoner` (R1-class) | native reasoning | **0.788** |

The climb is **monotone with the memory unchanged**. A stronger chat judge alone adds little
(+0.064); letting the model reason is the unlock (CoT +0.070, a true reasoning model +0.040
more), concentrated on the hard categories — across the sweep multi-session climbs
0.49 → 0.74 → 0.81 and single-session-preference 0.40 → 0.53 → 0.67, purely from the reader.
The memory held the answer at every step; the low early scores were reader/prompt artifacts.

**Two negative results on the retrieval lever.** Raising the global-pool retrieval from top-5
to top-10 *lowers* accuracy 0.464 → 0.408 (fixed budget → fewer characters per session plus
more distractors, collapsing single-session-user 0.586 → 0.314). Re-ranking the fusion top-30
with the trained cross-encoder before answering is *flat* (0.464 → 0.468): its retrieval
recall@1 win does not translate to answer accuracy because the reader already sees the top-5.
So neither retrieving more nor ranking better closes the global-pool gap at the answer level —
the shipped fusion top-5 is near its ceiling for the answer task, and the residual is a
first-stage recall problem on hard multi-evidence questions, not a tuning knob.

So on the comparable oracle axis we reach 0.788 with a mid-tier open *reasoning* reader,
against a vendor headline of 0.898 on a closed engine; the reader sweep localizes the ~0.11
gap to **reader-model strength on hard reasoning, not the memory substrate** (oracle gives
perfect retrieval; `deepseek-reasoner` is not frontier, and an o1/GPT-4o/Claude-class reader
extends the same curve). We make no inflated claim; the contribution is the reproducible,
decomposed benchmark, full account in
[`research/QA_ACCURACY.md`](../nevertwice/research/QA_ACCURACY.md).

### 5.4 What we cut

- **Abstractive consolidation** ("summarize notes into a general principle") craters
  recall@3 from 0.82 to 0.35, because a general principle embeds away from the specific
  query — **not shipped**.
- **Chunk-level late interaction** reaches recall@5 0.814 but is not shipped, because the
  distillation front-end already gives short notes the concentration it would buy.
- **Promptable LLM rerankers and four "stronger" embedders** lose to bge-m3 on top-1; only
  a *trained* cross-encoder wins, so that is the single opt-in precision lever.

## 7. Reproducibility

Every figure regenerates from a script on a public dataset with an open model:

```bash
python nevertwice/research/longmem_eval.py --embed         # embed the pool once
python nevertwice/research/longmem_eval.py --save           # retrieval recall@k
python nevertwice/research/head_to_head.py --only=mem0,langmem,amem --save
python nevertwice/research/qa_eval.py --setting=both --save   # end-to-end answer accuracy
python nevertwice/research/longitudinal_improvement.py --sweep --save   # improvement-per-token (§5.2)
python nevertwice/research/live_validation.py --trials 8 --save         # live guard study (§5.3)
```

The core is stdlib-only (no required pip dependencies); tests are mocked, network-free, and
run in CI across Linux/macOS/Windows and Python 3.10–3.13.

## 8. Limitations

The §4 mechanisms are measured on synthetic or curated data and internal tasks, not against
external SOTA on a shared leaderboard; they are research results, opt-in and off by default.
The **Active Memory** evaluation (§5) is honest about its scope: the improvement-per-token
benchmark is a faithful simulation whose one assumption (a fired guard changes the output) is
*separately* validated live (§5.3), but the live study is small (8 trials/task) and its generic
pitfalls understate memory's value rather than inflate it; axis B's lexical recall is
precision-first and moderate (the embedding blend and the adaptive threshold carry the rest); and
guard *generation* from mistakes is only as good as the extractor. Nevertwice is behind the
graph-native systems (Zep, Cognee) on **LLM entity/relation graph construction and multi-hop
traversal**, and behind Letta/Zep on a **production server/scale** story (single-machine,
single-user; git is available but not yet operationalized for cross-machine merge). These are
stated plainly in `WEAKNESSES.md`.

## 9. Conclusion

Nevertwice makes two claims. The first is that long-term agent memory does not require a vector
database, a server, or trust in a self-published number: a local, file-based, git-versioned
substrate with a calibrated hybrid ranker matches or beats hosted vector-DB memory on a controlled
retrieval benchmark, in the open. The second, and the one we think matters more, is that the
retrieval axis is **reader-bound and commoditizing** — so the future of agent memory is not a
better library but a memory that **acts**. Nevertwice's interventions (guards, anticipation,
counterfactuals) stay silent until they earn a place in the context and then change what the agent
does, preventing real errors at **~31× fewer tokens** than the field's always-inject design and
**−86%** real error rate in a live study — an effect that scales with the agent. As models get
stronger, a memory that merely recalls fades into the model; a memory that acts, and costs almost
nothing until it does, does not. That is the memory we built, measured end to end, on a substrate
the user owns.

## Availability

Code, data adapters, and all evaluation scripts: `https://github.com/DonPlaton/nevertwice`
(MIT). The Active Memory design and measurements are in `research/ACTIVE_MEMORY.md`,
`research/LIVE_VALIDATION.md`, and `research/QA_ACCURACY.md`; the retrieval studies in
`research/` and `docs/COMPARISON.md`. The LongMemEval dataset is downloaded separately (see
`research/data/README.md`).

## References

Cited inline and resolved in `docs/COMPARISON.md`: LongMemEval (arXiv:2410.10813);
Mem0 (arXiv:2504.19413); Zep/Graphiti (arXiv:2501.13956); A-MEM (arXiv:2502.12110);
Letta/MemGPT (arXiv:2310.08560); Cognee; LangMem; memanto (arXiv:2604.22085); CombSUM
(Fox & Shaw, 1994).
