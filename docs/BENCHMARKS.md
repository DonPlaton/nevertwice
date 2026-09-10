# Benchmarks & real-task evaluation

<p align="center"><img src="benchmarks.png" alt="Four panels, every figure on them registered and live. Supersession: how often each system hands back a fact that has since been retracted - Nevertwice far below Mem0 and an append-only store, each with a confidence interval. Hot-path latency for the cold import and the three hooks. Poisoning defence, with the weakest cell - a plausible false fact - drawn in the negative colour and hatched rather than left out. And four embedding ideas that were measured against a pre-declared threshold, missed it, and were deleted." width="880"></p>

**The figure is regenerated from the register.** `python research/gen_benchmarks_figure.py` reads every value from `research/evidence_manifest.json` and refuses to draw a withdrawn one, so a chart cannot outlive the number it draws - which is what happened to the figure this one replaced, drawn by hand in July with four numbers that were retracted a month later. A chart travels further than the page it sits on, so that retraction never reached anyone who saw only the image.

Two kinds of number here, and the difference matters:
- **External retrieval (LongMemEval-oracle):** the headline, independent ground truth.
- **Internal / real-store tasks:** self-consistency, temporal correctness, token economy
  on the owner's real bilingual (RU/EN) store (`research/eval_harness.py`, GPU-free, no key).

Most numbers below were **withdrawn in 2026-08**, and the ones that were not are marked as such
where they appear. Each claim used to carry the commit that last touched its artifact *file*
rather than the commit whose code produced it; once the claims were made to name the source files
their commands import, the external-retrieval corpus turned out to describe the first release, and
the internal tasks turned out to rest on a private store no third party can rebuild. The studies,
their designs and their honest caveats stay here; the figures do not.

External retrieval is live again below, on a corpus pinned by content hash and with whole sessions
embedded on every arm; supersession returns with its own re-run.
`python tools/check_freshness.py --list-stale` lists every number that is still withdrawn and the
gate that blocks re-measuring it.

## Speed: what the hot paths cost

A memory that hooks every tool call has to be fast on modest hardware, so the costs are
measured end to end (real subprocess, stdin event to exit) with no model and no network,
the exact profile of a weak machine driving a cloud agent. Ryzen 7 7700, Windows,
Python 3.14; reproduce anywhere with `python research/latency_bench.py`:

<!-- claims:latency -->
| hot path | cost | when it is paid |
|---|---|---|
| PreToolUse end-to-end | **84 ms** | every tool call (interpreter start included) |
| UserPromptSubmit end-to-end | 78 ms | per prompt (task-aware recall) |
| SessionStart end-to-end, idle | 78 ms | per session start with no backlog |
| cold import of the engine | 25 ms | once per hook process (inside the numbers above) |

<sub>**Withdrawn** - `guards.check()` over a seeded ledger, lexical recall, no embedder: the bench's seed lands in the subprocess store while the in-process half reads the store pinned at import, so this row now measures an empty store (0 guards, 0 notes) instead of the seeded one the published number describes - the measurement, not just the value, is broken</sub>
<!-- /claims:latency -->

**Read these as a tenth of a second, not as millisecond figures.** Each is the *minimum of five*
runs, which makes it stable within a session and not across them: the same statistic on the same
unchanged tree gave three materially different PreToolUse figures within a single day on this
machine, spanning about a third of the value. The published number fell when the table was
regenerated after a pure file-layout refactor, and that fall is machine state, not an
optimisation - saying otherwise would be crediting a rename with a speedup. What the row supports
is the order of magnitude and the shape: the hook costs a tenth of a second, dominated by
interpreter start, and pays nothing in context tokens. The exact figures behind that spread are
recorded in the `caveat` field of each latency claim in `research/evidence_manifest.json`, which
is where a number belongs when it is evidence about measurement rather than a published result.

Two of these were an order of magnitude worse until a 2026-07 perf audit: an idle SessionStart
used to pay a four-second-timeout LLM liveness probe before checking whether it had any work, and
every hook process imported network machinery the guard path never uses. The before-figures are
withdrawn - they describe a tree no committed artifact records, and re-measuring them means
checking out and running the pre-audit engine. The lesson generalizes and needs no number: hooks
get measured end to end, because module-level convenience is a per-tool-call tax.

## Supersession: does a retracted fact come back?

The one comparison here that runs on a corpus this repository ships. Full method, per-shape
breakdown and what it costs us: [`research/SUPERSESSION.md`](../research/SUPERSESSION.md).

<!-- claims:supersession-pinned -->
| arm | returns the retracted fact | returns the replacement | retires a still-true fact |
|---|---|---|---|
| **Nevertwice** | 0.075 [0.040, 0.136] | 0.967 [0.917, 0.987] | 0.050 [0.014, 0.165] |
| Mem0 | 0.883 [0.778, 0.942] | 0.967 [0.886, 0.991] | 0.000 [0.000, 0.161] |
| an append-only markdown file | 0.950 [0.863, 0.983] | 0.950 [0.863, 0.983] | 0.050 [0.009, 0.236] |
<!-- /claims:supersession-pinned -->

Nevertwice's row is pooled over two runs of the same commit; the other two arms are one run each.
60 supersession cases and 20 controls per run, Wilson intervals, one local stand,
the same embedder and the same extraction model for every arm, and since the evidence layer
the text scored is the text a caller receives - the lesson and the verbatim line it came from.
Paired on the same cases, the discordant pairs between Nevertwice and Mem0 run almost entirely in
Mem0's disfavour - one case the other way; Mem0 and the append-only file are tied with each other at McNemar p = 0.29,
tied at the bad end of the column. Our payload per query is 400 characters
against Mem0's 444. The floor is why the table is worth printing at
all: a benchmark only one vendor's architecture fails is a benchmark about that vendor, and this one
is failed by an append-only text file too, which is what supersession costs when nothing implements it.

## Abstention: does refusing a weak hit pay for itself?

No, on the only labelled store this project has, and the defaults were turned off because of
it. Full sweep: [`research/ABSTENTION_AB.md`](../research/ABSTENTION_AB.md).

Both retrieval paths shipped with a value threshold - a hit scoring below a fraction of the
batch's best hit is refused even when there is room for it, which is the distinction between
*does it fit* and *is it worth it*. Swept over a curve rather than compared to off: at the
shipped threshold the payload shrank by about a quarter and the wanted fact came back several
points less often, against a gate written before the run that allowed a fraction of that loss.
Nothing on the curve cleared both, so both defaults are 0 and the switches stay opt-in. The
sweep was re-run after the engine review of 2026-09 and read a larger loss than the first run;
the decision did not depend on the exact figure and stands.

The one that did pay: re-mining a grown transcript from its recorded watermark instead of from
byte zero reads well under half the bytes at identical coverage of the appended material.

## External retrieval: LongMemEval, on a hash-pinned corpus

Real agent sessions in one shared store - 940 of them, 500 questions - each question carrying
**human-annotated** evidence sessions (`answer_session_ids`). Relevance is independent of our embeddings, so this is a real
recall number rather than a self-grade.

The 2026-07 run of this benchmark was withdrawn because the corpus behind it could not be
identified after the fact. `research/corpus_pin.py` now holds its sha256, the harness verifies it
before reading a byte, and the fingerprint is stamped into every result file. Full method and what
the re-run found: [`research/EXTERNAL_RETRIEVAL.md`](../research/EXTERNAL_RETRIEVAL.md).

<!-- claims:longmem-pinned -->
| method | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| semantic (bge-m3) | 0.428 | 0.692 | 0.782 | 0.552 |
| lexical (BM25) | 0.470 | 0.738 | 0.830 | 0.596 |
| **calibrated fusion (shipped default, 0 deps)** | 0.512 | 0.800 | **0.866** | 0.636 |
| **+ trained cross-encoder (opt-in)** | **0.626** | **0.834** | **0.866** | **0.723** |
<!-- /claims:longmem-pinned -->

The same methods on the **non-oracle** pool - 19,206 retrievable sessions, twenty-one times the
haystack, the same questions and the same annotated evidence:

<!-- claims:longmem-s -->
| method | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| semantic (bge-m3) | 0.184 | 0.354 | 0.440 | 0.267 |
| lexical (BM25) | 0.218 | 0.416 | 0.510 | 0.313 |
| **calibrated fusion (shipped default, 0 deps)** | **0.228** | **0.422** | **0.514** | **0.329** |
<!-- /claims:longmem-s -->

Everything falls, which is what twenty-one times the haystack does, and the shape holds: fusion
still beats both signals it fuses. Running it also found that the harness's own inertness check
had been passing for the wrong reason; that story is on the study page. The same four systems on
this pool, the competitor arms being their store layers as in the oracle table:

<!-- claims:head-to-head-s -->
| system | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| **Nevertwice (calibrated fusion)** | **0.228** | **0.422** | **0.514** | **0.314** |
| Mem0 | 0.194 | 0.380 | 0.464 | 0.281 |
| LangMem | 0.150 | 0.354 | 0.442 | 0.237 |
| A-MEM | 0.148 | 0.346 | 0.430 | 0.232 |
<!-- /claims:head-to-head-s -->

Four systems on the oracle pool, same embedder, same scoring function, the same questions:

<!-- claims:head-to-head-pinned -->
| system | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| **Nevertwice (calibrated fusion)** | **0.512** | **0.800** | **0.866** | **0.630** |
| Mem0 | 0.478 | 0.758 | 0.846 | 0.603 |
| LangMem | 0.426 | 0.692 | 0.782 | 0.543 |
| A-MEM | 0.428 | 0.692 | 0.782 | 0.544 |
<!-- /claims:head-to-head-pinned -->

The rows above are the competitors' **store** arms - their retrieval layer over whole
sessions, which is what isolates the ranker. The products as shipped extract memories with an
LLM before storing anything; those pipelines ran on the same pool with the same local model
(`qwen2.5-7b-64k`), and are read against our shipped ranker here. An arm that blocked itself -
more than a tenth of its LLM calls failing silently, or a package whose search path raised on
every query - has no row, and the note under the table says which. A-MEM's pipeline arm is
missing for the second reason, and the defect was ours: its search calls `embed_query` on the
embedding function, the shim this stand substitutes so that every product embeds with the same
bge-m3 did not answer that method, and each query came back empty. The recall columns of that
run measure the shim, not A-MEM, so no claim was registered from them; the shim is fixed and
the arm is re-measured with the rest of the head-to-head families.

<!-- claims:head-to-head-full -->
| system | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| **Nevertwice (calibrated fusion)** | **0.512** | **0.800** | **0.866** | **0.630** |
| Mem0, full pipeline (`infer=True`: its LLM extraction, then its search) | 0.394 | 0.694 | 0.796 | 0.523 |
| LangMem, full pipeline (`create_memory_store_manager`) | 0.434 | 0.728 | 0.820 | 0.558 |
| A-MEM, full pipeline (`agentic_memory`: LLM notes and link evolution) | 0.426 | 0.694 | 0.782 | 0.544 |
<!-- /claims:head-to-head-full -->

### LoCoMo, the benchmark this project had excluded on paper

Ten long conversations, 5,882 turns, 1,977 of 1,986 questions scored, retrieving the
human-annotated evidence turn from the question's own conversation - LoCoMo's own setting. Full
method and the defect running it found: [`research/LOCOMO.md`](../research/LOCOMO.md).
Re-measured at the reviewed engine on the cached vectors: every figure reproduced exactly, and
LoCoMo turns are short enough that the embedding-cap defect never touched them.

<!-- claims:locomo -->
| method | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| semantic (bge-m3) | 0.182 | 0.432 | 0.560 | 0.301 |
| lexical (BM25) | 0.339 | 0.601 | 0.681 | 0.459 |
| **calibrated fusion (shipped default, 0 deps)** | **0.350** | **0.640** | **0.727** | **0.481** |
<!-- /claims:locomo -->

The exclusion was written around a reported 94% for plain BM25. The term-overlap floor here
reads 0.601 at R@5, and the three methods order exactly as they do on LongMemEval, so on the
**retrieval** axis LoCoMo separates systems perfectly well. The 94% figure is about judge-scored
**answer accuracy**, which measures the reader as much as the memory and which nothing in this
repository measures. So the exclusion is narrowed rather than lifted: not a candidate as a
headline, on a named axis. **A number in this table must never be compared with a published
LoCoMo accuracy figure** - they are different quantities.

The competitors see LoCoMo the way a store sees a user's whole history: the turns of all ten
conversations in one collection, which is harder than the per-conversation setting above and is
the setting every system is scored in here:

<!-- claims:head-to-head-locomo -->
| system | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| **Nevertwice (calibrated fusion)** | **0.311** | 0.571 | 0.667 | **0.421** |
| Mem0 | 0.271 | **0.575** | **0.674** | 0.404 |
| LangMem | 0.189 | 0.441 | 0.549 | 0.295 |
| A-MEM | 0.188 | 0.436 | 0.543 | 0.292 |
<!-- /claims:head-to-head-locomo -->

Reproduce:

```bash
python research/corpus_pin.py --fetch longmemeval_oracle
python research/longmem_eval.py --embed
python research/longmem_eval.py --save --out=research/results/longmem_oracle.json
python research/head_to_head.py --only=nevertwice,mem0,langmem,amem --save \
       --out=research/results/head_to_head_v2.json
```

### The 2026-07 run, which stays withdrawn

<!-- claims:longmem-benchmarks -->
> **Withdrawn 2026-08.** the LongMemEval-oracle dataset is third-party and not committed (research/data/longmemeval_oracle.json is absent here), and no content hash was recorded when the number was produced, so the run cannot be reproduced or even pinned to a revision
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/longmem_eval.py` is what re-measures this one.
<!-- /claims:longmem-benchmarks -->

The shipped ranker fuses the two signals with **calibrated score fusion** (z-normalise each, combine
the magnitudes), which measured above rank fusion and above the three local competitors run on the
same stand; the numbers are withdrawn with the rest of the LongMemEval corpus, and the design
argument is not (see [COMPARISON.md](COMPARISON.md)). Reciprocal rank fusion, which Nevertwice
itself shipped until 2026-07, discards the score magnitudes and scored below plain BM25; the full
study is in [`research/RETRIEVAL_FUSION.md`](../research/RETRIEVAL_FUSION.md).

The optional reranker (bge-reranker-v2-m3, `[reranker]` extra; one `NEVERTWICE_XRERANK=1` run
downloads the model, after which it stays on automatically) then stacks on top and raised top-1
recall - again on the withdrawn corpus. A *promptable* LLM reranker, by contrast, degraded top-1,
so we ship the trained one and not the LLM one. The embedder A/B (no local embedder
beat bge-m3 as a drop-in) and the consolidation negative are in
[`research/W2_PRECISION.md`](../research/W2_PRECISION.md).

## Retrieval quality (Task A: leave-one-out, self-consistency only)

> ⚠️ **What this is and isn't (read before quoting any number).** The relevance
> ground truth here is each note's own `[[wikilink]]` neighbours, which the system
> itself writes. So Task A measures **internal-linkage recovery / ranker
> self-consistency** ("does the ranker resurface a note's own siblings"), **not**
> relevance to an external information need. The table is a fair *relative* comparison
> of the three rankers on identical ground truth; the absolute R@5 is **not** an
> external quality benchmark and must not be cited as one. **For the independent number,
> see the LongMemEval-oracle section above** (external human-annotated GT). That is the
> one to cite; this self-consistency table is kept only as a relative ranker comparison.

<!-- claims:task-a -->
> **Withdrawn 2026-08.** measured on the owner's private vault, which is not committed and must never be read or written by this repository's tests - no third party can reproduce it
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/eval_harness.py --save` is what re-measures this one.
<!-- /claims:task-a -->

Relative reading only: with a strong multilingual embedder, semantic leads on this
self-consistency task; lexical is the graceful fallback when the GPU is busy. (On an
earlier weaker embedder, hybrid led; the fusion is kept as a robustness floor.)

## Temporal correctness (Task B: point-in-time QA)

<!-- claims:task-b -->
> **Withdrawn 2026-08.** measured on the owner's private vault, which is not committed and must never be read or written by this repository's tests - no third party can reproduce it
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/eval_harness.py --save` is what re-measures this one.
<!-- /claims:task-b -->

Flat "return all versions" surfaces several contradictory versions per query. The
bi-temporal model answers "what did we believe about X at time T" correctly where a
flat store either guesses or dumps contradictions. The measured margin is withdrawn with
the rest of the private-store corpus.

## Token economy (Task C: tokens to convey project state)

The project **card** (the SessionStart surface) vs dumping the full Context journal:

<!-- claims:task-c -->
> **Withdrawn 2026-08.** measured on the owner's private vault, which is not committed and must never be read or written by this repository's tests - no third party can reproduce it
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/eval_harness.py --save` is what re-measures this one.
<!-- /claims:task-c -->

Overall the current snapshot costs materially fewer tokens than the full Context journal, and is
point-in-time and contradiction-free. The ratio itself is withdrawn.

## Token A/B: retrieval vs no-retrieval, controlled on LongMemEval

Most "agent memory saves tokens" claims are never measured against the prompts where retrieval
**misses**. We measured it. `research/token_ab.py` runs a controlled A/B on the
LongMemEval-oracle set with the counterfactual stated up front: a no-memory agent must load the
relevant history to answer; with memory it reads only the top-k and escalates to a full load on a
miss, so **net = recall@k · counterfactual − top-k cost**. The value of retrieval depends entirely
on *what it replaces*, so we reported both honest bounds - the curated small haystack and the whole
accumulated history - rather than cherry-picking the flattering one:

<!-- claims:token-ab-raw -->
> **Withdrawn 2026-08.** the LongMemEval-oracle dataset is third-party and not committed (research/data/longmemeval_oracle.json is absent here), and no content hash was recorded when the number was produced, so the run cannot be reproduced or even pinned to a revision
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/token_ab.py` is what re-measures this one.
<!-- /claims:token-ab-raw -->

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
(`research/token_ab.py --distill`). On a question sample the distiller compressed the retrieved
sessions by more than an order of magnitude, and the per-hit cost collapsed:

<!-- claims:token-ab-distill -->
> **Withdrawn 2026-08.** the LongMemEval-oracle dataset is third-party and not committed (research/data/longmemeval_oracle.json is absent here), and no content hash was recorded when the number was produced, so the run cannot be reproduced or even pinned to a revision
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/token_ab.py --distill` is what re-measures this one.
<!-- /claims:token-ab-distill -->

**This is the headline that raw-session retrieval couldn't earn:** with distillation, memory
measured **net-positive even against the already-curated small haystack**, the conservative
counterfactual. The sample was smaller and higher-variance than the full set above; the
low-variance finding was the compression ratio and the **sign flip** from negative to positive.
Both are withdrawn until the dataset ships with a hash.

### Live two-arm run: measured, not modeled

A real two-arm run (`--live`): the same small local agent answers each question twice, once
fed the full curated haystack (no memory), once fed only the top-3 **distilled notes**, recording
Ollama's own `prompt_eval_count` (actual input tokens) for each:

<!-- claims:token-ab-live -->
> **Withdrawn 2026-08.** the LongMemEval-oracle dataset is third-party and not committed (research/data/longmemeval_oracle.json is absent here), and no content hash was recorded when the number was produced, so the run cannot be reproduced or even pinned to a revision
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/token_ab.py --distill --live` is what re-measures this one.
<!-- /claims:token-ab-live -->

Memory cut input tokens by a wide margin on this arm - a *measured* number, not modeled, and now
withdrawn with the rest of the LongMemEval corpus. The honest caveat survives the withdrawal
because it is a direction, not a figure: on that tiny sample with a weak 3B reader, crude
answer-match was *lower* with memory. The distilled notes sometimes drop a detail the full context
kept, so the token saving is **real but not free**. A larger sample on a stronger reader is needed
to pin the accuracy trade; we report the dip rather than hide it.

**Bottom line, with every figure withdrawn and the shape intact:** raw-session retrieval measured
net-negative against a small curated context - we published that when it was unflattering, and it is
why the distillation lever exists. Distillation flipped the sign, and a live two-arm run measured a
large input-token cut with an honest accuracy caveat on a small local-model sample. The defensible
headline is *distillation makes memory token-positive*, not "saves X tokens unconditionally", and it
will carry numbers again when the corpus is re-measured. Reproduce:
`python research/token_ab.py --distill --live`.

## Real-task battle-test: does it save tokens & help recall?

Honest accounting on a live project (`project_alpha`):

Every figure in this section was measured on the owner's private vault. None of them can be
reproduced by a reader, so all of them are withdrawn; what follows is the shape of the accounting,
which is the part a reader can check against their own store.

**Cost (what memory adds to context):**
- SessionStart injection - project card, learned profile, top relevant mistakes and patterns,
  cross-project lessons.
- Task-aware recall per *substantial* prompt (trivial prompts skipped; already-shown notes
  deduped; capped per session).

**Payoff:**
- **State conveyance is far cheaper as a representation** than reading the full Context journal to
  orient - a project card against a whole journal, on every project measured.
- **It surfaces the exact prior lesson.** For the prompt *"how to avoid CX regression
  when integrating a new QSD algorithm"*, recall returned precisely the past mistake
  `naive-swap-regression` ("naive swap in QSD caused CX/time regression; benchmark
  before integrating") plus the patterns that resolved it. Without memory the agent
  re-discovers this by re-exploring and, worst case, **repeats the failed approach**,
  an entire wasted code+test+debug iteration (thousands of tokens).

**Verdict (honest about the counterfactual).** The *measured* facts: state
conveyance is much cheaper as a representation (card vs journal), and recall
surfaced the exact prior lesson in live queries. The *unmeasured* part: whether
memory nets out cheaper **overall** depends on the counterfactual "the agent would
have re-explored the codebase." The controlled token A/B above quantifies exactly this
(net-negative vs a tiny curated context, hugely positive vs the full history); a *live*
two-arm agent run remains the one unmeasured piece. On a session where memory isn't
needed, the injection at session start and per substantial prompt is pure overhead - the
per-turn figures came off the owner's private store and are withdrawn with it;
the smart throttle (skip trivial, dedup, per-session cap) bounds it but doesn't make
it zero. So: clearly positive when it prevents a repeated mistake or a re-exploration;
a small bounded overhead otherwise. Treat the token math as a favourable side-effect,
**not** a proven net saving, and not the headline.
