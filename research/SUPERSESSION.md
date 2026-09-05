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

The naive arm is the point rather than a courtesy. A benchmark that only one vendor's
architecture fails is a benchmark about that vendor; if the floor also scores well, this one
does not separate systems and has to be thrown away.

## Result

| arm | stale ↓ | current ↑ | over-retraction ↓ | chars returned per query |
|---|---|---|---|---|
| **Nevertwice** | **0.042** [0.018, 0.094] | 0.933 [0.874, 0.966] | **0.05** | 277 |
| Mem0 2.0.19 | 0.917 [0.819, 0.964] | 0.950 [0.863, 0.983] | 0.00 | 457 |
| naive append-only + BM25 | 0.950 [0.863, 0.983] | 0.950 [0.863, 0.983] | 0.05 | 223 |

Nevertwice's row is pooled over **two runs of the same commit**, so n = 120 case-runs for
stale and current and n = 40 for over-retraction; the other two arms are one run each, n = 60
and n = 20. Intervals are Wilson at 95%.

**Why two runs.** The first run of the fixed engine read stale 0.017 and the second read 0.067,
on the same commit, the same corpus and the same models. The extraction model is not
deterministic at temperature 0, so a single run of this stand is not a result, and publishing
one would have been the same mistake as reading a regression out of one latency run. The pooled
rate is 0.042 and both per-run values are kept in the artifact.

The other two columns are what make the first one mean anything. A memory that returned
nothing at all would score 0.000 stale, which is the best possible number, and 0.000 current,
which is the worst; a memory that deleted on any doubt would score well on both and be caught
by over-retraction. Ours reads 0.017 / 0.933 / 0.05, and the third figure is the same as the
floor's, so the silence on the first is not bought by forgetting.

Paired, on the same cases, McNemar exact:

| pair | discordant | p |
|---|---|---|
| Nevertwice vs Mem0 | 54 - 0, and 51 - 0 on the second run | 1.1 x 10^-16 |
| Nevertwice vs naive | 56 - 0 | 2.8 x 10^-17 |
| **Mem0 vs naive** | 2 - 4 | **0.69** |

**The third row is the finding, and it is easy to misread.** Mem0 and the append-only file
are tied *with each other*, at the wrong end: both hand back the retracted fact on more than
nine cases in ten. The tie says nothing good about either. On supersession, Mem0 is
statistically indistinguishable from a text file. That is not a defect report: Mem0 2.0 is
single-pass and ADD-only by published design, and its note on the change says both facts
survive on purpose. It is a good design for conversational history. This benchmark measures
the axis where that design has nothing to offer, and the number says exactly that.

Its retrieval, meanwhile, is excellent - a current rate of 0.950 against our 0.933, and zero
over-retraction. Mem0 loses this benchmark and leads on the one everyone else runs.

### By shape

| shape | Nevertwice (2 runs) | Mem0 | naive |
|---|---|---|---|
| `value_replaced` | 1/30 | 15/15 | 15/15 |
| `approach_abandoned` | **3/30** | 15/15 | 15/15 |
| `retracted_no_replacement` | 1/30 | 12/15 | 15/15 |
| `narrowed` | 0/30 | 13/15 | 12/15 |

Stale count, lower is better. `narrowed` is clean across both runs. The rest are one or two
cases each, and which cases they are moves between runs, which is the nondeterminism above
showing up per shape rather than only in the total.

`approach_abandoned` is the weakest of the four, and a run before the language fix had four
failures there. They went away for a reason worth recording rather than celebrating - see
below.

## What it costs us

The stale rate is not free, and the honest accounting is on the other two columns.

**Current rate 0.933 against 0.950 for both other arms.** Fewer than two points, and the store
says where they went. Of the twenty control cases, seven returned nothing useful: **one** was
retired by the memory - the only true over-retraction in the run - **two** were never written
at all, and **four** were written and ranked below the top five. Only the first is the memory
being too eager, which is why the raw 0.30 is reported as 0.05 here.

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
- **n = 120 supersession case-runs.** Enough to separate 0.042 from 0.950 many times over; not
  enough to distinguish 0.042 from 0.08, and the two runs behind it read 0.017 and 0.067.
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
- **Retrieval quality is not the subject, and on it we lose.** Mem0 and the append-only file
  both return the wanted fact 0.950 of the time against our 0.933. On the axis a user meets
  every day, did my fact come back, this design is slightly worse than both arms it beats on
  staleness. That is the trade this architecture makes, and it is stated in the same table as
  the win rather than a footnote below it.
