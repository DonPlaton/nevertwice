# Does the memory hand back a fact that has since been retracted?

<!-- review-2026-09-05 -->
> **Withdrawn 2026-09-05, pending a re-run.** The engine changed after these figures were
> measured (guard delivery, the re-mine date and floor, the rollback generation of state
> files - none of them the supersession path, and the register does not distinguish), so
> every claim on this page is marked `stale` until the bench is re-run at the new HEAD. The
> method, the dataset and the caveats stand; quote the numbers only once
> `python tools/check_freshness.py --list-stale` no longer lists them.

Every public benchmark for agent memory asks whether a system **recalls** a fact. LoCoMo,
LongMemEval and BEAM all measure retrieval against a set of questions whose answers were true
when the corpus was written and stayed true. None of them asks what happens when a fact is
replaced.

For a chat companion that omission is defensible: both halves of "I used to live in Berlin,
now I live in Lisbon" are worth keeping. For a coding agent it is the whole problem. A memory
that returns *the timeout is 30 seconds* after the team changed it to 5 does not fail loudly.
It fails by being confidently helpful, and the agent writes the wrong number into working
code.

So this is a benchmark for the other half. It is small, it is local, and its dataset is
committed and content-hashed in this repository, which is the one property every external
number this project previously published turned out to lack.

## What is measured

Three rates, because any one of them alone is trivially gamed.

| | | |
|---|---|---|
| **stale** | the retracted fact came back, asserted as current | lower is better |
| **current** | the replacement came back | higher is better |
| **over-retraction** | on control cases, a fact that is still true went missing | lower is better |

A system that answers *I have nothing* scores a perfect stale rate and a zero current rate,
and the pair of numbers says so at a glance. A system that deletes on any doubt scores well on
both and is caught by the third: a third of the cases assert two facts that are simply
different, where forgetting one is the failure.

**A returned item counts as a stale assertion only if it carries a retracted marker and no
current marker.** The first scoring pass matched the retracted value anywhere in the returned
text, and it was wrong in a way that flattered no one. Nevertwice answered the extraction
query with a decision note titled *disable-cerebras-integration* - the current truth, which
cannot be stated without naming the thing that was disabled - and a substring test called that
stale. Mem0 returned the original sentence *"extracts knowledge using Cerebras gpt-oss-120b"*,
which asserts the retracted state as fact. Those are opposite outcomes and the first metric
could not tell them apart.

## The dataset

[`research/data/supersession_v1.json`](data/supersession_v1.json) - 80 cases, built by
[`gen_supersession_dataset.py`](gen_supersession_dataset.py), content-hashed, regenerable byte
for byte.

Each case is two sessions written weeks apart, the way a project actually accumulates facts:
one states something, the next states what replaced it, and a query asks what is true now.
Sessions carry neighbouring sentences, because a memory that only ever sees a bare assertion
is not being tested on anything a user will do - and because an extraction pipeline with a
relevance gate correctly declines to store a single context-free sentence.

| shape | n | what changes |
|---|---|---|
| `value_replaced` | 15 | a number or name changes and the old one is simply wrong |
| `approach_abandoned` | 15 | a whole approach is dropped for a different one |
| `retracted_no_replacement` | 15 | something is removed and nothing takes its place |
| `narrowed` | 15 | the fact still holds, in a smaller scope than before |
| `control` | 20 | two facts that differ and both remain true |

`retracted_no_replacement` is the hardest shape by construction: there is no new fact for
retrieval to rank above the old one. `narrowed` is the subtlest - the old statement is not
false so much as over-general, which is the failure mode that reads as correct.

Phrasing is rotated over six frames per role so that no arm can pass by keying on one
sentence pattern, and controls get their own frames. That last point was learned the hard way:
the first draft handed controls the *replacement* frames, so a control session opened with
"and it did not survive", and a system that read the sentence correctly was scored as
over-retracting for obeying it.

The generator refuses to write a case no arm could score - a marker missing from the session
that asserts it, or a retracted marker sitting in the replacement text with nothing marking it
current.

## Arms

All three see identical sessions in identical order, on the same machine, with the same local
embedder (`bge-m3`) and the same extraction model (`qwen3-coder:30b`). Nothing is billed and
nothing leaves the machine.

- **Nevertwice** through its public API: `capture_session` per session, then `recall`.
- **Mem0 2.0.19** through `Memory.add` / `Memory.search`, one `add` per session so both
  extract from the same unit the same number of times. Installed with `mem0ai[extras]`, so
  BM25 keyword retrieval is **on** - a plain `pip install mem0ai` leaves two of its three
  retrieval signals disabled, and measuring that install would be measuring a packaging
  decision rather than a design.
- **naive** - an append-only markdown store with IDF term-overlap retrieval and no
  supersession mechanism whatsoever.
- **Zep/Graphiti** (`graphiti-core`, since 2026-09-10) - the one competitor built for this axis:
  bitemporal edges with an LLM invalidation step. One episode per session, the stand's extractor
  and embedder through Ollama's OpenAI-compatible endpoint, a local FalkorDB (the embedded
  drivers do not run on this machine), structured output in `json_schema` mode (the only mode the
  local model fills its schemas in), hybrid edge search fused by reciprocal rank. What it hands
  back is the facts of its top edges with the ones it invalidated or expired hidden, as its own
  `search()` hides them. Three and a third model calls per episode, measured, against our one.

The naive arm is the point rather than a courtesy. A benchmark that only one vendor's
architecture fails is a benchmark about that vendor; if the floor also scores well, this one
does not separate systems and has to be thrown away.

## Result

Run of 2026-09-10 at commit e225d9c (the Zep arm at dc523f1). The text each arm is scored on is
what a caller receives: for Nevertwice, the lesson and - since the evidence layer - the verbatim
transcript line it was aligned to; for Mem0 and Graphiti, their memory or fact text; for the
floor, the stored sentence. The table is the generated one in the section *The same corpus, with
the cue removed* below, which carries both corpora; on the explicit corpus the stale column reads
ours 0.075, Mem0 0.883, Graphiti 0.317, the floor 0.950, and the current column 0.967, 0.967, 0.550,
0.950. Over-retraction and characters per query are in the same table and in the register.

Nevertwice's row is pooled over **two runs of the same commit**, so n = 120 case-runs for
stale and current and n = 40 for over-retraction; the other two arms are one run each, n = 60
and n = 20. Intervals are Wilson at 95%.

**Why two runs.** The two runs read stale 0.083 and 0.067 on the same commit, the same corpus
and the same models. The extraction model is not deterministic at temperature 0, so a single
run of this stand is not a result, and publishing one would have been the same mistake as
reading a regression out of one latency run. Both per-run values are kept in the artifact.

**What the evidence layer changed here.** The scored text now carries the verbatim line, so a
retracted value the note's own wording had paraphrased away is counted as stale when the line
still says it - the metric got stricter, not the engine looser - and the replacement's literal
value is found more often, which is where the current column's rise comes from. The stale
column moved from the run before by less than its own interval.

The other two columns are what make the first one mean anything. A memory that returned
nothing at all would score 0.000 stale, which is the best possible number, and 0.000 current,
which is the worst; a memory that deleted on any doubt would score well on both and be caught
by over-retraction. Ours reads 0.075 / 0.967 / 0.05, and the third figure is the same as the
floor's, so the silence on the first is not bought by forgetting.

Paired, on the same cases, McNemar exact:

| pair | discordant | p |
|---|---|---|
| Nevertwice vs Mem0 | 49 - 1 | 9.1 x 10^-14 |
| Nevertwice vs naive | 52 - 0 | 4.4 x 10^-16 |
| Nevertwice vs Graphiti | 19 - 5 | 0.01 |
| **Mem0 vs naive** | 2 - 6 | **0.29** |

**The third row is the finding, and it is easy to misread.** Mem0 and the append-only file
are tied *with each other*, at the wrong end: both hand back the retracted fact on the large
majority of cases. The tie says nothing good about either. On supersession, Mem0 is
statistically indistinguishable from a text file. That is not a defect report: Mem0 2.0 is
single-pass and ADD-only by published design, and its note on the change says both facts
survive on purpose. It is a good design for conversational history. This benchmark measures
the axis where that design has nothing to offer, and the number says exactly that.

Its retrieval, meanwhile, is as good as ours on this run - the same current rate - with zero
over-retraction. Mem0 loses this benchmark and leads on the one everyone else runs.

**Graphiti is the row that tests the claim.** It is the one system on the stand designed for
retraction - an edge carries `valid_at` and `invalid_at`, and a model decides what an episode
contradicts - and it does retract: a third of the retracted facts come back where Mem0 and the
file return nine in ten. It pays on the other two columns, which is the whole reason they are
printed: it returns the replacement barely more often than not, and it retires a still-true
fact three times in twenty. Paired with us on the same cases the discordant pairs run nineteen
to five - five cases where its invalidation caught a replacement ours did not - and on the
implicit corpus, with the retraction cue removed, its stale rate reads 0.217 to our 0.092. The
lead over the ADD-only stores was never the finding; this row is.

### By shape

| shape | Nevertwice (2 runs) | Mem0 | Graphiti | naive |
|---|---|---|---|---|
| `value_replaced` | 1/30 | 15/15 | 5/15 | 15/15 |
| `approach_abandoned` | **7/30** | 13/15 | 2/15 | 15/15 |
| `retracted_no_replacement` | 1/30 | 13/15 | 7/15 | 15/15 |
| `narrowed` | 0/30 | 12/15 | 5/15 | 12/15 |

Stale count, lower is better. `narrowed` is clean across both runs and `value_replaced` nearly
so. `approach_abandoned` carries most of what remains: an abandoned approach is stated in
different words from the one that replaced it, the two notes rarely share a slug, and the
twin gate does not always join them - so the old note stays live, and its verbatim line now
says exactly what it used to say.

`approach_abandoned` is the weakest of the four, and a run before the language fix had four
failures there. They went away for a reason worth recording rather than celebrating - see
below.

## The same corpus, with the cue removed

Every case above says so when it replaces a fact: *actually, we moved off Postgres 15*. A real
transcript often does not - the new fact simply arrives, framed like any first assertion, and
whether it replaces anything is something the reader works out. `research/gen_supersession_dataset.py
--variant implicit` writes exactly that corpus: same facts, same markers, same queries, the
second session reframed and rotated so the two sessions never share a frame. The explicit
corpus is unchanged byte for byte, and `--check` proves it.

<!-- claims:supersession-variants -->
| system | stale, explicit | stale, implicit | current, explicit | current, implicit |
|---|---|---|---|---|
| **Nevertwice** | 0.075 | 0.092 | 0.967 | 0.983 |
| Mem0 | 0.883 | 0.933 | 0.967 | 0.983 |
| Zep/Graphiti (`graphiti-core`, FalkorDB) | 0.317 | 0.217 | 0.550 | 0.783 |
| an append-only markdown file | 0.950 | 0.950 | 0.950 | 0.950 |

<sub>Stale = the retracted fact came back, lower is better. Current = the fact that replaced it was returned, higher is better. *Explicit* names the retraction in the second session; *implicit* frames the replacement like any first assertion.</sub>
<!-- /claims:supersession-variants -->

The gate for this variant was written in the ledger before the run (item I5): our stale rate
below the floor's, with a Wilson interval that excludes it. If it had missed, the README's
supersession row would have been narrowed to "explicit retractions only".

## As of a day

A memory that knows when a fact stopped being true can answer a second question: what did we
believe on some day in the past? `api.as_of(query, date)` walks every note whose belief
interval contains that date - live and retired alike - and ranks them by the query, with no
LLM and no embedder on the path. `research/asof_bench.py` ingests the same sixty cases with
dates two months apart and asks each one twice: for a day between the two sessions, and for a
day after the second. A case counts only when both answers are right.

<!-- claims:asof -->
| arm | both days | the old day | the day after |
|---|---|---|---|
| **Nevertwice** (`api.as_of`) | 0.783 | 0.825 | 0.917 |
| Zep/Graphiti (`graphiti-core`, its own bitemporal edges) | 0.433 | 0.667 | 0.550 |
| an append-only markdown file, no dates | 0.000 | 0.000 | 1.000 |

<sub>The gate written before the run was 0.80 on both days, and this is below it. The loss is on the old day, and the stand now says why per case: a first session the extractor left without a note, a note whose wording lost the marker, or the new fact leaking into the old day; the artifact carries the split. Mem0 has no row - it stamps a memory with the wall-clock time of the `add()` call and its search has no as-of filter, so facts cannot be placed in the past without patching the product.</sub>
<!-- /claims:asof -->

Graphiti's row is the first competitor number on this stand: its bitemporal edges answer both
days for fewer than half the cases, and lose mostly on the day after - the replacement it did not
extract or invalidated late - where we lose on the day before. Its as-of is read from its own
`valid_at` / `invalid_at` / `expired_at` fields, filtered on our side, because its server-side
date filter admitted edges from after the day asked for in the probe.

## What it costs us

The stale rate is not free, and the honest accounting is on the other two columns.

**Current rate 0.967, level with Mem0 and above the floor's 0.950.** The store says where the
remaining misses went. Of the twenty control cases in the first run, eight returned nothing
useful: **one** was retired by the memory - the only true over-retraction in the run - **three**
were never written at all, and **four** were written and ranked below the top five; the second
run reads one, two and three. Only the first is the memory being too eager, which is why the raw
control miss rate is reported as 0.05 here.

**A defect this benchmark found in its own first run: the extractor answered in the wrong
language.** On a corpus containing no Russian at all, the local model wrote **17 of 123 notes
in Russian**. The prompt carried the rule, *write in the dominant language of the
session*, and left the model to apply it. About one time in seven it declined.

Fixing it moved this benchmark more than anything else did, and the reason matters. Those
notes were correct and unfindable by an English query, so the drift was suppressing the
current rate directly. It was also defeating supersession: a Russian note about the same fact
gets a different title, the slug-keyed match misses, and the old note is never retired. That
is where the four `approach_abandoned` failures went. Before and after, same corpus, same
models, same commit except for the prompt:

| | before | after |
|---|---|---|
| notes written in Cyrillic | 17 of 123 | **0 of 128** |
| stale | 0.067 | **0.017** |
| current | 0.867 | **0.933** |

The condition is now resolved in Python and the prompt names one language. The detector counts
letters rather than bytes, because a Russian session is full of Latin identifiers and a
majority vote reads almost every bilingual transcript as English - which, on this owner's
bilingual store, is the population that matters.

## Three defects this benchmark found

Neither was visible from reading the code, and neither had a failing test.

- **The extractor answered in Russian on an English corpus** about one time in seven, which
  the section above covers. It is listed here too because it is the one defect of the three
  that a user would have seen without reading any code.
- **`capture_session` raised on the second session of any project in one process.** Making the
  dedup window date-aware changed the title cache's entries from a slug to a `(date, slug)`
  pair in one of the three places that touch it. The appender kept writing a bare string,
  which the next date-aware read unpacked as a pair - `ValueError: too many values to unpack`.
  It had a silent twin: the remover kept testing `slug in bucket` against a list of pairs,
  always false, so a note that had just been retired was still offered to the extractor as an
  existing title. The crash is the loud half and the silent one is the dangerous one.
- **The delta reader discarded one whole event per re-mine.** It seeked to the watermark and
  dropped a line unconditionally, on the assumption that a byte offset lands mid-line. A
  watermark is recorded as the file size after a completed write, so it lands on a newline
  nearly every time.

## Reproducing

```
python research/gen_supersession_dataset.py --check      # the dataset matches its generator
python research/supersession_bench.py --arms nevertwice,naive --out nt.json
python research/supersession_bench.py --arms mem0 --out mem0.json      # needs mem0ai[extras]
python research/supersession_bench.py --compare nt.json mem0.json
```

The Mem0 arm needs its own environment. It also needs one instance rather than one per case:
Mem0 keeps a process-global migrations store under `~/.mem0`, and a second
`Memory.from_config` in the same process dies with *"already accessed by another instance of
Qdrant client"*. Seventy-nine of eighty cases failed that way on the first attempt and the arm
still printed a summary line - which is precisely how a broken arm gets published as a win, so
the harness now counts per-case errors and refuses to score an arm that mostly failed.

## What this does not show

- **The column that matters most is the least measured.** Over-retraction is this design's own
  worst failure mode: retiring a fact that is still true is silent data loss, and unlike a
  stale answer nothing downstream can catch it. It rests on **40 control case-runs**, two per
  control. Two errors in forty is 0.05, and the Wilson interval on that runs to **0.165** - a
  bound consistent with losing one still-true fact in six. The floor sits in the same place, so
  the comparison holds, but the absolute number does not support the reading "about one in
  twenty". Bringing the upper bound under 0.10 needs about **130 controls**, roughly ninety
  more than the dataset carries, and that is the first thing it should gain.
- **n = 120 supersession case-runs.** Enough to separate 0.075 from 0.950 many times over; not
  enough to distinguish 0.075 from 0.04, and the two runs behind it read 0.083 and 0.067.
- **The cases are written here, not scraped.** They are realistic in shape and were authored
  before any arm ran, but they are ours, and a corpus its author wrote is a weaker instrument
  than one they did not. The naive floor is the guard against the obvious failure mode - if
  the task were trivially easy, the floor would show it - and the floor scores 0.950.
- **One extraction model, and one machine.** A different model would move every arm that uses
  one, which is why both LLM arms here use the same one. The machine is the less obvious half:
  the largest single change to these numbers this week came from the extractor answering in the
  wrong language, which is a property of one model on one host and not of the architecture. A
  second machine has not run this, so nothing here separates what the design does from what
  this installation does.
- **Retrieval quality is not the subject.** On this run the wanted fact comes back as often
  from us as from Mem0, and more often than from the floor; a run earlier this year read us two
  points behind both. Part of the rise is the scored text now carrying the verbatim line the
  caller receives, which is the honest reading of what a caller gets, and part is the run. The
  axis a user meets every day, did my fact come back, is stated in the same table as the win
  rather than a footnote below it.