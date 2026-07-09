# Benchmarks & real-task evaluation

Two kinds of number here, and the difference matters:
- **External retrieval (LongMemEval-oracle):** the headline, independent ground truth.
- **Internal / real-store tasks:** self-consistency, temporal correctness, token economy
  on a real bilingual (RU/EN) store of ~320 notes across 11 projects (`research/eval_harness.py`,
  GPU-free, $0).

## Speed: what the hot paths cost

A memory that hooks every tool call has to be fast on modest hardware, so the costs are
measured end to end (real subprocess, stdin event to exit) with no model and no network,
the exact profile of a weak machine driving a cloud agent. Ryzen 7 7700, Windows 11,
Python 3.14; reproduce anywhere with `python research/latency_bench.py`:

| hot path | cost | when it is paid |
|---|---|---|
| PreToolUse end-to-end | **85 ms** | every tool call (interpreter start included) |
| UserPromptSubmit end-to-end | 68 ms | per prompt (task-aware recall) |
| SessionStart end-to-end, idle | 73 ms | per session start with no backlog |
| cold import of the engine | 25 ms | once per hook process (inside the numbers above) |
| `guards.check()`, 61 guards, 2 KB | 0.18 ms | the actual guard match, pure regex |
| lexical recall, no embedder | 0.2 ms | the zero-model floor recall falls back to |

Two of these were 10-30x worse until a 2026-07 perf audit: an idle SessionStart used to
pay a 4-second-timeout LLM liveness probe before checking whether it had any work
(2,188 ms measured; now gated to 73 ms), and every hook process imported network
machinery the guard path never uses (59 ms import, now 25 ms). The lesson generalizes:
hooks get measured end to end, because module-level convenience is a per-tool-call tax.

## External retrieval: LongMemEval-oracle (the headline)

940 real agent sessions in one shared store, 500 questions, each with **human-annotated**
evidence sessions (`answer_session_ids`). Relevance is **independent of our embeddings**, so
this is a real recall number, not a self-grade. Reproduce:
`python research/longmem_eval.py [--xrerank]` (dataset fetched separately, see
[`research/data/README.md`](../research/data/README.md)).

| method | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| semantic (bge-m3) | 0.422 | 0.652 | 0.728 | 0.528 |
| lexical (BM25) | 0.522 | 0.752 | 0.834 | 0.623 |
| **calibrated fusion (shipped default, 0 deps)** | 0.550 | **0.802** | **0.858** | 0.657 |
| **+ trained cross-encoder (opt-in)** | **0.614** | 0.826 | 0.858 | **0.712** |

The shipped ranker fuses the two signals with **calibrated score fusion** (z-normalise each, combine
the magnitudes), which lifts R@5 from 0.66 under the old rank fusion to **0.80**, and beats every
local competitor on the same stand (Mem0 0.758, LangMem and A-MEM 0.692; see
[COMPARISON.md](COMPARISON.md)). The popular reciprocal rank fusion most systems ship discards the
score magnitudes and scores below plain BM25; the full study is in
[`research/RETRIEVAL_FUSION.md`](../research/RETRIEVAL_FUSION.md).

The optional reranker (bge-reranker-v2-m3, `NEVERTWICE_XRERANK=1`, `[reranker]` extra) then stacks on
top, taking **top-1 recall to 0.614** and MRR to 0.712. A *promptable* LLM reranker, by contrast,
degraded top-1, so we ship the trained one and not the LLM one. The embedder A/B (no local embedder
beat bge-m3 as a drop-in) and the consolidation negative are in
[`research/W2_PRECISION.md`](../research/W2_PRECISION.md).

## Retrieval quality (Task A: leave-one-out, n=327, self-consistency only)

> ⚠️ **What this is and isn't (read before quoting any number).** The relevance
> ground truth here is each note's own `[[wikilink]]` neighbours, which the system
> itself writes. So Task A measures **internal-linkage recovery / ranker
> self-consistency** ("does the ranker resurface a note's own siblings"), **not**
> relevance to an external information need. The table is a fair *relative* comparison
> of the three rankers on identical ground truth; the absolute R@5 is **not** an
> external quality benchmark and must not be cited as one. **For the independent number,
> see the LongMemEval-oracle section above** (external human-annotated GT). That is the
> one to cite; this self-consistency table is kept only as a relative ranker comparison.

| method | R@1 | R@3 | R@5 | MRR |
|---|---|---|---|---|
| semantic (bge-m3) | 0.661 | 0.817 | **0.881** | 0.755 |
| lexical | 0.581 | 0.761 | 0.829 | 0.689 |
| hybrid (RRF) | 0.645 | 0.813 | 0.875 | 0.747 |

Relative reading only: with a strong multilingual embedder, semantic leads on this
self-consistency task; lexical is the graceful fallback when the GPU is busy. (On an
earlier weaker embedder, hybrid led; the fusion is kept as a robustness floor.)

## Temporal correctness (Task B: point-in-time QA)

| | accuracy |
|---|---|
| bi-temporal graph | **1.000** |
| flat "use newest" | 0.455 |

Flat "return all versions" surfaces **2.27 contradictory versions/query**. The
bi-temporal model answers "what did we believe about X at time T" correctly where a
flat store either guesses or dumps contradictions.

## Token economy (Task C: tokens to convey project state)

The project **card** (the SessionStart surface) vs dumping the full Context journal:

| project | card | full Context | ratio |
|---|---|---|---|
| project_alpha | 111 | 3836 | **35×** |
| project_beta | 63 | 2936 | 47× |
| project_delta | 20 | 2300 | 115× |

Overall current-snapshot vs full Context: **2.5× fewer tokens**, point-in-time and
contradiction-free.

## Token A/B: retrieval vs no-retrieval, controlled on LongMemEval

Most "agent memory saves tokens" claims are never measured against the prompts where retrieval
**misses**. We measured it. `research/token_ab.py` runs a controlled A/B on the 500-question
LongMemEval-oracle set with the counterfactual stated up front: a no-memory agent must load the
relevant history to answer; with memory it reads only the top-k and escalates to a full load on a
miss, so **net = recall@k · counterfactual − top-k cost**. The value of retrieval depends entirely
on *what it replaces*, so we report both honest bounds (full history = **3.3M tok** across 940
sessions) rather than cherry-picking the flattering one:

| k | recall@k | top-k cost (tok) | net vs a curated small haystack | net vs the full history |
|---|---|---|---|---|
| 3 | 0.722 | 10,137 | **−5,331** | +2,370,699 |
| 5 | 0.802 | 17,065 | **−11,726** | +2,627,576 |
| 10 | 0.858 | 34,619 | **−28,908** | +2,794,684 |

Read honestly, both directions:
- **Against an already-curated small context, raw-session retrieval saves nothing, often
  net-negative.** Retrieval is not magic; if the haystack is already small, just load it.
- **Against the realistic alternative at scale (the *whole* accumulated history) retrieval is
  overwhelmingly cheaper.** It is what makes recall feasible when full-load is impossible.
- This A/B models retrieval of **raw sessions**. Nevertwice's real mechanism adds a lever it omits:
  **distillation** (each session becomes a ~one-screen typed note). Measured next.

### Distillation A/B: the real mechanism, measured (the net flips positive)

Nevertwice never stores raw sessions; it stores **distilled notes**. We measured that lever directly:
distil each retrieved session into a compact note via local Ollama, then recompute the net
(`research/token_ab.py --distill`). On a 40-question sample the distiller compressed
**968,715 → 31,517 tokens = 30.7× smaller**, and the per-hit cost collapsed:

| k | recall@k | raw top-k (tok) | **distilled top-k (tok)** | net raw vs curated | **net distilled vs curated** |
|---|---|---|---|---|---|
| 3 | 0.83 | 10,629 | **344** | −3,965 | **+6,320** |
| 5 | 0.93 | 17,703 | **560** | −10,232 | **+6,911** |
| 10 | 0.98 | 35,224 | **1,136** | −27,349 | **+6,738** |

**This is the headline that raw-session retrieval couldn't earn:** with distillation, memory is
**net-positive even against the already-curated small haystack**, the conservative counterfactual.
(Sample is 40 questions, higher-variance than the 500-set above; the low-variance finding is
the **30.7× compression** and the **sign flip** from negative to positive.)

### Live two-arm run: measured, not modeled

A real two-arm run (`--live`): the same local agent (qwen2.5:3b) answers each question twice, once
fed the full curated haystack (no memory), once fed only the top-3 **distilled notes**, recording
Ollama's own `prompt_eval_count` (actual input tokens) for each. 15 questions:

| arm | mean input tokens | answer-match (crude) |
|---|---|---|
| no memory (full haystack) | **5,132** | 0.47 |
| with memory (top-3 distilled) | **345** | 0.33 |

**Memory cut input tokens 93%** (5,132 → 345), a *measured* number, not modeled. Honest caveat: on
this tiny sample with a weak 3B reader, crude answer-match was *lower* with memory (0.33 vs 0.47).
The distilled notes sometimes drop a detail the full context kept, so the token saving is **real but
not free**. A larger sample on a stronger reader is needed to pin the accuracy trade; we report the
dip rather than hide it.

**Bottom line:** raw-session retrieval is net-negative vs a small curated context (we publish that);
**distillation flips it positive** (30.7× compression, net +6.3-6.9k tok/query even vs the curated
haystack), and a **live two-arm run measures a 93% input-token cut**, with an honest accuracy caveat
on a small local-model sample. The defensible headline is *distillation makes memory token-positive*,
not "saves X tokens unconditionally." Reproduce: `python research/token_ab.py --distill --live`.

## Real-task battle-test: does it save tokens & help recall?

Honest accounting on a live project (`project_alpha`):

**Cost (what memory adds to context):**
- SessionStart injection ≈ **644 tok** (project card + learned profile + top relevant
  mistakes/patterns + cross-project lessons).
- Task-aware recall ≈ **266 tok** per *substantial* prompt (trivial prompts skipped;
  already-shown notes deduped; capped per session).

**Payoff:**
- **State conveyance is ~5-35× cheaper** than the alternative of reading the full
  Context journal to orient (111-tok card vs 3836-tok journal).
- **It surfaces the exact prior lesson.** For the prompt *"how to avoid CX regression
  when integrating a new QSD algorithm"*, recall returned precisely the past mistake
  `naive-swap-regression` ("naive swap in QSD caused CX/time regression; benchmark
  before integrating") plus the patterns that resolved it. Without memory the agent
  re-discovers this by re-exploring and, worst case, **repeats the failed approach**,
  an entire wasted code+test+debug iteration (thousands of tokens).

**Verdict (honest about the counterfactual).** The *measured* facts: state
conveyance is ~5-35× cheaper as a representation (card vs journal), and recall
surfaced the exact prior lesson in live queries. The *unmeasured* part: whether
memory nets out cheaper **overall** depends on the counterfactual "the agent would
have re-explored the codebase." The controlled token A/B above quantifies exactly this
(net-negative vs a tiny curated context, hugely positive vs the full history); a *live*
two-arm agent run remains the one unmeasured piece. On a session where memory isn't
needed, the injection (~0.6k tok start + ~0.27k/substantial prompt) is pure overhead;
the smart throttle (skip trivial, dedup, per-session cap) bounds it but doesn't make
it zero. So: clearly positive when it prevents a repeated mistake or a re-exploration;
a small bounded overhead otherwise. Treat the token math as a favourable side-effect,
**not** a proven net saving, and not the headline.
