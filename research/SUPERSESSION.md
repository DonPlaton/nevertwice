# Does the memory hand back a fact that has since been retracted?

<!-- withdrawn-banner -->
> **Withdrawn: figures on this page must not be quoted.** They were retracted and remain
> here because deleting a result one was wrong about destroys the record of having been
> wrong. The design, the method and the caveats stand; the numbers do not. Each figure's
> own reason and date are in
> [`research/evidence_manifest.json`](evidence_manifest.json), and
> `python tools/check_freshness.py --list-stale` lists every one.

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

Three rates, because any one of them alone is trivially gamed - and the third comes in two
strengths, which this page kept under one name until 2026-09-11.

| | | |
|---|---|---|
| **stale** | the retracted fact came back, asserted as current | lower is better |
| **current** | the replacement came back | higher is better |
| **control miss** | on control cases, a fact that is still true did not come back - for any cause | lower is better; comparable across arms |
| **over-retraction** | on control cases, the memory itself retired a fact that is still true | lower is better; readable only where the store records a retirement |

A system that answers *I have nothing* scores a perfect stale rate and a zero current rate,
and the pair of numbers says so at a glance. A system that deletes on any doubt scores well on
both and is caught by the third and fourth: a quarter of the cases assert two facts that are
simply different, where forgetting one is the failure. The third is the broad measure - the
fact did not come back, whether the memory retired it, never wrote it, or ranked it too low -
and it is the one every arm can be scored on. The fourth is the narrow one, the design's own
failure mode, and it needs the arm's store: ours records `valid_to`, Graphiti records
`invalid_at`, Mem0 reports a delete event, the floor never retires by construction. Until
2026-09-11 the published table printed our fourth beside the other arms' third under the one
word *over-retraction*; the rows measured different things, and the register has since split
them (`control_miss_rate` against `over_retraction_rate`).

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

Run of this September (commit 74cfcd4) for every arm - ours twice, Mem0 once, the floor once, and
Zep/Graphiti twice, on a FalkorDB flushed between corpora (the K2 parity run; see *What this does
not show* for why its earlier figures were withdrawn). The text each arm is scored on is what a
caller receives: for Nevertwice, the note's
title, description and prevention - the description carrying, since this September, the literals the
write path verified against the transcript (`[facts]`); for Mem0 and Graphiti, their memory or
fact text; for the floor, the stored sentence. The table is the generated one in the section *The
same corpus, with the cue removed* below, which carries the stale and current columns for both
corpora. Control miss and characters per query are in the register; the control misses split by
cause are the tables under *What it costs us*.

Nevertwice's row is pooled over **two runs of the same commit**, so n = 120 case-runs for
stale and current and n = 40 for the two control measures; since the parity run Zep/Graphiti is
pooled the same way (its two runs read the same stale rate on the explicit corpus and five points
apart on the implicit one); Mem0 and the floor are one run each, n = 60 and n = 20. Intervals are
Wilson at 95%.

**Why two runs.** On this campaign the two runs read different stale rates on the explicit corpus and
one figure twice on the implicit one, with the extractor pinned to temperature zero for the whole
re-measurement; the campaign before had read a single figure twice, and even pinned the model is not
bit-for-bit deterministic (the two as-of runs of one campaign differ, and an unrelated call between
two sessions changes what the next one extracts - see the confound below). A single run of this stand is
therefore still not a result, and publishing one would be the same mistake as reading a
regression out of one latency run. Both per-run values are kept in the artifact.

**What the literal-fact channel changed here.** The scored text now carries the literals the
extractor named or the harvester found in the session, verified as substrings before they are
written, so a retracted value the note's own wording had paraphrased away is counted as stale
when the literal still says it - the metric got stricter, not the engine looser - and the
replacement's literal is found more often, which is where the current column's rise to 1.000
comes from. The stale column moved from the run before by less than its own interval.

The other columns are what make the first one mean anything. A memory that returned nothing at
all would score zero stale, which is the best possible number, and zero current, which is the
worst; a memory that deleted on any doubt would score well on both and be caught on the
controls. Ours leads on the first two. On the controls the honest reading needs
both measures, and *What it costs us* gives them: a still-true fact failed to come back in four
of forty control case-runs, against one miss for the floor and none for Mem0 - and in **none of
them was the memory the cause**. Every one is a session the extractor wrote no note for at all. Before
K8 this paragraph read the other way round, and the difference is the whole of that package. The silence on the first
column is partly bought by that, and the split below says exactly how much.

Paired, on the same cases, McNemar exact:

<!-- claims:supersession-pairs -->
| pair | discordant (first - second) | p, McNemar exact |
|---|---|---|
| Nevertwice vs Mem0 | 0 - 55 | 5.6 x 10^-17 |
| Nevertwice vs naive | 0 - 56 | 2.8 x 10^-17 |
| Nevertwice vs Zep/Graphiti | withdrawn - withdrawn | withdrawn |
| **Mem0 vs naive** | 3 - 4 | 1.00 |

<sub>**Withdrawn** cells: owner decision pending: the zep arm's model traffic was not observed (Graphiti's openai SDK sends through a vendored httpx the pacer does not hook); the server log shows no failed call</sub>
<!-- /claims:supersession-pairs -->

**The last row is the finding, and it is easy to misread.** Mem0 and the append-only file
are tied *with each other*, at the wrong end: both hand back the retracted fact on the large
majority of cases. The tie says nothing good about either. On supersession, Mem0 is
statistically indistinguishable from a text file. That is not a defect report: Mem0 2.0.19 is
single-pass and ADD-only by published design, and its note on the change says both facts
survive on purpose. It is a good design for conversational history. This benchmark measures
the axis where that design has nothing to offer, and the number says exactly that.

Its retrieval, meanwhile, misses no control fact at all where we miss four of forty - every one
of those a session our extractor wrote no note for, none of them a fact the memory retired. On the
current column the shade of a lead it held over us on the campaign before this one is gone - the
table above has both figures. Mem0 loses this benchmark and leads on the one everyone else runs.

**Graphiti is the row that tests the claim.** It is the one system on the stand designed for
retraction - an edge carries `valid_at` and `invalid_at`, and a model decides what an episode
contradicts - and it does retract: three in ten of the retracted facts come back where Mem0 and
the file return nine in ten. It pays on the other columns, which is the whole reason they are
printed: it returns the replacement barely more often than not, and a still-true fact fails to
come back nine times in forty - and its graph says why: it never wrote them (no edge carried the
fact), and it retired none. Paired with us on the same cases the discordant pairs run nineteen to
two - two cases where its invalidation caught a replacement ours did not - and on the implicit
corpus, with the retraction cue removed, its stale rate is several times ours (its two runs sit five
points apart). The lead over the ADD-only stores was never the finding; this row is.

## Between nights, and after the night

**The mode without a model is the first mode.** Nothing on the write path calls a model to decide a
replacement. Two notes land under one title; a small set of rules asks whether the newer one *proves*
it replaces the older - the writer named the older note, or every literal the older note verified
appears in the newer, or the older statement is contained word for word in the newer. If one of those
holds, the older note is retired with its interval closed. If none holds, nothing is retired and
nothing is rewritten: both notes stay, the pair is marked contested, and recall hands back the newer
statement with the earlier one attached to it, newest first. An installation with no model backend at
all stops here, and the contested pairs stay visible through `conflicts()` and `integrity()`.

A contested pair is a real state, not a queue: the user is served both facts and told which is newer.
What the weekly consolidation adds is a verdict. It takes the contested pairs oldest first, asks one
model call per pair whether the newer statement replaces the older, and retires the older only if
the verdict survives three guards - a note with verified literals is never retired for one without, a
value-shaped token the newer statement's own verified block does not carry vetoes the replacement,
and a replacement that names no value at all does not displace one that does. A vetoed pair stays
two live notes and is marked disputed.

So each store is read twice, and the page prints both: once after the replacing session, which is
what a user sees between nights, and once after the adjudication on that same store.

<!-- claims:supersession-readings -->
| reading | returns the retracted fact | the retracted value appears anywhere | returns the replacement | retires a still-true fact | characters per query |
|---|---|---|---|---|---|
| Between nights | 0.017 [0.005, 0.059] | 0.633 | 1.000 [0.969, 1.000] | 0.000 [0.000, 0.088] | 444 |
| After consolidation | 0.017 [0.005, 0.059] | 0.358 | 1.000 [0.969, 1.000] | 0.000 [0.000, 0.088] | 392 |

<sub>The second column counts an item that asserts the retracted value without the current one, which is the bench's rule and the one the comparison uses. The third counts the retracted value wherever it appears in the returned text, so a paired hit - the newest statement with the earlier one attached - is counted here and not there. Both are printed because the design serves the older statement on purpose rather than hiding it, and a reader deciding whether that is acceptable needs the number it costs.</sub>

<sub>What the second row costs: one model call per contested pair at consolidation and none in the hook, at 414 tokens a pair; on a real store the dry run counted at most withdrawn contested pairs a week, against a run budget of a hundred thousand tokens. With no model backend at all the first row is what the product does, and the contested pairs stay visible through `conflicts()` and `integrity()`.</sub>

<sub>Why the judge's verdict is not the last word. On a recorded set of pairs whose truth is known it rules correctly 0.956 of the time, and its precision on `replaces` - the verdict that retires a note - is 0.995, not one. An earlier draw of the same prompt over the same bytes read exactly one, so that figure is a draw and not a property of the judge. This is why three guards sit between a verdict and a retirement: a note with verified literals is never retired for one without, a value the new statement's own verified block does not carry vetoes the replacement, and a replacement naming no value does not displace one that does. The column above reads zero with the judge making false calls, which is the guards doing the work.</sub>

<sub>**Withdrawn** cells: withdrawn at the step-4 engine merge (Q5 principle layer, note_snippet word boundary, defaults set by the Q5 gates): re-measurement needs the v2 campaign on GPU with local Ollama models</sub>
<!-- /claims:supersession-readings -->

### By shape

| shape | Nevertwice (2 runs) | Mem0 | Graphiti (2 runs) | naive |
|---|---|---|---|---|
| `value_replaced` | 1/30 | 15/15 | 9/30 | 15/15 |
| `approach_abandoned` | **2/30** | 14/15 | 5/30 | 15/15 |
| `retracted_no_replacement` | **2/30** | 14/15 | 16/30 | 15/15 |
| `narrowed` | 0/30 | 13/15 | 8/30 | 12/15 |

Stale count, lower is better, read from the rows of the pooled artifact. `narrowed` is clean
across both runs and `value_replaced` nearly so; `approach_abandoned` and
`retracted_no_replacement` carry what remains, two each. An abandoned approach is stated in
different words from the one that replaced it, the two notes rarely share a title, and nothing
joins them - so the old note stays live, and its literals still say what it used to say; the
two retractions that slipped this campaign are the same shape, a removal titled unlike the
thing removed.

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
| **Nevertwice** | 0.017 | 0.067 | 1.000 | 1.000 |
| Mem0 | 0.933 | 0.950 | 1.000 | 0.983 |
| Zep/Graphiti (`graphiti-core`, FalkorDB) | withdrawn | withdrawn | withdrawn | withdrawn |
| an append-only markdown file | 0.950 | 0.950 | 0.950 | 0.950 |

<sub>Stale = the retracted fact came back, lower is better. Current = the fact that replaced it was returned, higher is better. *Explicit* names the retraction in the second session; *implicit* frames the replacement like any first assertion.</sub>

<sub>**Withdrawn** cells: owner decision pending: the zep arm's model traffic was not observed (Graphiti's openai SDK sends through a vendored httpx the pacer does not hook); the server log shows no failed call</sub>
<!-- /claims:supersession-variants -->

The same two readings on this corpus:

<!-- claims:supersession-readings-implicit -->
| reading | returns the retracted fact | the retracted value appears anywhere | returns the replacement | retires a still-true fact | characters per query |
|---|---|---|---|---|---|
| Between nights | 0.067 [0.034, 0.126] | 0.700 | 1.000 [0.969, 1.000] | 0.000 [0.000, 0.088] | 494 |
| After consolidation | 0.067 [0.034, 0.126] | 0.417 | 1.000 [0.969, 1.000] | 0.000 [0.000, 0.088] | 442 |

<sub>The second column counts an item that asserts the retracted value without the current one, which is the bench's rule and the one the comparison uses. The third counts the retracted value wherever it appears in the returned text, so a paired hit - the newest statement with the earlier one attached - is counted here and not there. Both are printed because the design serves the older statement on purpose rather than hiding it, and a reader deciding whether that is acceptable needs the number it costs.</sub>

<sub>What the second row costs: one model call per contested pair at consolidation and none in the hook, at 414 tokens a pair; on a real store the dry run counted at most withdrawn contested pairs a week, against a run budget of a hundred thousand tokens. With no model backend at all the first row is what the product does, and the contested pairs stay visible through `conflicts()` and `integrity()`.</sub>

<sub>Why the judge's verdict is not the last word. On a recorded set of pairs whose truth is known it rules correctly 0.956 of the time, and its precision on `replaces` - the verdict that retires a note - is 0.995, not one. An earlier draw of the same prompt over the same bytes read exactly one, so that figure is a draw and not a property of the judge. This is why three guards sit between a verdict and a retirement: a note with verified literals is never retired for one without, a value the new statement's own verified block does not carry vetoes the replacement, and a replacement naming no value does not displace one that does. The column above reads zero with the judge making false calls, which is the guards doing the work.</sub>

<sub>**Withdrawn** cells: withdrawn at the step-4 engine merge (Q5 principle layer, note_snippet word boundary, defaults set by the Q5 gates): re-measurement needs the v2 campaign on GPU with local Ollama models</sub>
<!-- /claims:supersession-readings-implicit -->

Paired on the same cases of this corpus, McNemar exact:

<!-- claims:supersession-pairs-implicit -->
| pair | discordant (first - second) | p, McNemar exact |
|---|---|---|
| Nevertwice vs Mem0 | 0 - 53 | 2.2 x 10^-16 |
| Nevertwice vs naive | 0 - 53 | 2.2 x 10^-16 |
| Nevertwice vs Zep/Graphiti | withdrawn - withdrawn | withdrawn |
| **Mem0 vs naive** | 3 - 3 | 1.00 |

<sub>**Withdrawn** cells: owner decision pending: the zep arm's model traffic was not observed (Graphiti's openai SDK sends through a vendored httpx the pacer does not hook); the server log shows no failed call</sub>
<!-- /claims:supersession-pairs-implicit -->

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
| **Nevertwice** (`api.as_of`) | 0.867 | 0.933 | 0.917 |
| Zep/Graphiti (`graphiti-core`, its own bitemporal edges) | withdrawn | withdrawn | withdrawn |
| an append-only markdown file, no dates | 0.000 | 0.000 | 1.000 |

<sub>The gate written before the run was 0.80 on both days and 0.85 on the old day; this run meets it: both days 0.867, the old day 0.933; the two runs behind the pooled figure read 0.867 and 0.867, and the interval [0.794, 0.916] covers the threshold. The larger loss is on the day after; the old-day misses split by kind in the artifact: 6 where the extractor left the first session without a note, 0 where its note was absorbed into the second session's and no longer serves the old fact, 2 where its note existed and nothing came back, 0 where the note came back without the marker, 0 where the new fact leaked into the old day. Mem0 has no row - it stamps a memory with the wall-clock time of the `add()` call and its search has no as-of filter, so facts cannot be placed in the past without patching the product.</sub>

<sub>**Withdrawn** cells: owner decision pending: the zep arm's model traffic was not observed (Graphiti's openai SDK sends through a vendored httpx the pacer does not hook); the server log shows no failed call</sub>
<!-- /claims:asof -->

The gate under the table was written in the ledger (I6) before the first run - four fifths on both
days, later joined by a slightly higher bar on the old day (J2) - and was missed twice, by
seventeen points and then by two. This September's campaign meets it exactly at the threshold, and
the caption says so in those words because it is computed from the claims rather than written once:
a pooled figure sitting on the threshold, whose two runs straddle it, is a boundary, not a margin. What moved it was
J2b, the archive-aware reconcile: the stand dates the first session past the ninety-day
archive window, and until this September the replacing session never closed an archived note's
interval (`s0_retired_rate` 0.000). With the archive-aware reconcile it closed most of them at write
time; since K8 an unproven pair is left contested instead of retired on the title, so the closure
moved to the night: a fifth of them before the weekly consolidation and
most of them after it, against a `--recent` control that reads like the
before-sleep figure, so the archive is no longer what holds the closure back. Until this September the caption here read *and
this is below it* on a run that had met the gate - a verdict typed when the gate was missed
and never compared again, which is the defect that made it computed.

**The residual old-day misses are not the extractor's silence on a short session.** The caption
names eleven case-runs where the first session left no note. They are eight distinct sessions -
three silent in both runs, five in one run only - and captured alone, one by one
([`silence_probe.py`](silence_probe.py)), six of the eight wrote a note carrying the fact, one
paraphrased it and one produced no item. They are not shorter than the sessions that were
written (their median length is the same), and the stand now looks for the twin absorb of
*What it costs us* and finds none of it here. What is left is instability: the same two
sentences at temperature zero yield a note or nothing depending on the project name in the
prompt. The gate for the fix is in the ledger (K5); its mechanism is a retry on an empty
extraction, not a prompt change, and it waits for the marked cards.

Graphiti's row is pooled over two runs on a flushed FalkorDB (both read the same both-days rate):
its bitemporal edges answer both days for fewer than half the cases, and lose mostly on the day
after - the replacement it did not extract or invalidated late - where we lose on the day before.
Its as-of is read from its own `valid_at` / `invalid_at` / `expired_at` fields, filtered on our
side, because its server-side date filter admitted edges from after the day asked for in the
probe.

## What it costs us

The stale rate is not free, and the honest accounting is on the other two columns.

**Current rate at the ceiling, above both Mem0 and the floor.** The store says where the control
misses went, and since K8 the answer is short: of the forty control case-runs, four returned nothing
useful and **none of them is the memory's doing**. Nothing was retired to `Superseded/`; nothing was
absorbed into another note; nothing was written, served and ranked below the top five. All four are
sessions the extractor wrote no note for at all, which is the extractor's silence and not the write
path's eagerness.

That is the sentence this section existed to avoid having to write the other way round. Before K8 the
write path met a second fact under a first fact's title and rewrote the note, keeping the old
statement only under `## Previous statement` - live on disk, absent from everything recall returned.
Now a second fact under one title is proved a replacement or left alone: both notes stay, the pair is
marked contested, and recall hands back the newer statement with the earlier one attached. Over-retraction
proper - the memory retiring or displacing a fact that is still true - is zero on both corpora, in both
readings. Both tables below say so per arm.

<!-- claims:supersession-causes -->
| arm | a still-true fact did not come back | retired by the memory | absorbed into another note | never written | served, below the top five |
|---|---|---|---|---|---|
| **Nevertwice**, between nights | 0.100 [0.040, 0.231] | 0 of 40 | 0 of 40 | 4 of 40 | 0 of 40 |
| **Nevertwice**, after consolidation | 0.100 [0.040, 0.231] | 0 of 40 | 0 of 40 | 4 of 40 | 0 of 40 |
| Mem0 | 0.000 [0.000, 0.161] | 0 of 20 | 0 of 20 | 0 of 20 | 0 of 20 |
| Zep/Graphiti (`graphiti-core`, FalkorDB) | withdrawn | withdrawn of 40 | withdrawn of 40 | withdrawn of 40 | withdrawn of 40 |
| an append-only markdown file | 0.050 [0.009, 0.236] | 0 of 20 | 0 of 20 | 0 of 20 | 1 of 20 |

<sub>The first column is the rate in the table above; the four after it split its count by cause. The first two are the memory being too eager - it retired the note, or it judged a different fact a twin of this one and absorbed that fact into the note: the note stays on disk, but what it now hands back is the other fact, and this one is no longer served - the other two are the extractor's silence and the ranker's depth. A cause reads *not read* where the run did not inspect that arm's store: a retirement is visible only where the store records one (our `valid_to` and `## Previous statement`; Graphiti's `invalid_at`/`expired_at`; Mem0's delete and update events).</sub>

<sub>Over-retraction proper - the memory stopped serving a fact that was still true, by retiring the note or by absorbing another fact into it - is the first two cause columns as a rate: 0.000 [0.000, 0.088] for Nevertwice over its control case-runs.</sub>

<sub>**Withdrawn** cells: owner decision pending: the zep arm's model traffic was not observed (Graphiti's openai SDK sends through a vendored httpx the pacer does not hook); the server log shows no failed call</sub>
<!-- /claims:supersession-causes -->

On the implicit corpus the split is empty in every column - no still-true fact failed to come back
at all, where the engine before K8 lost fourteen of forty to an absorb:

<!-- claims:supersession-causes-implicit -->
| arm | a still-true fact did not come back | retired by the memory | absorbed into another note | never written | served, below the top five |
|---|---|---|---|---|---|
| **Nevertwice**, between nights | 0.000 [0.000, 0.088] | 0 of 40 | 0 of 40 | 0 of 40 | 0 of 40 |
| **Nevertwice**, after consolidation | 0.000 [0.000, 0.088] | 0 of 40 | 0 of 40 | 0 of 40 | 0 of 40 |
| Mem0 | 0.000 [0.000, 0.161] | 0 of 20 | 0 of 20 | 0 of 20 | 0 of 20 |
| Zep/Graphiti (`graphiti-core`, FalkorDB) | withdrawn | withdrawn of 40 | withdrawn of 40 | withdrawn of 40 | withdrawn of 40 |
| an append-only markdown file | 0.050 [0.009, 0.236] | 0 of 20 | 0 of 20 | 0 of 20 | 1 of 20 |

<sub>The first column is the rate in the table above; the four after it split its count by cause. The first two are the memory being too eager - it retired the note, or it judged a different fact a twin of this one and absorbed that fact into the note: the note stays on disk, but what it now hands back is the other fact, and this one is no longer served - the other two are the extractor's silence and the ranker's depth. A cause reads *not read* where the run did not inspect that arm's store: a retirement is visible only where the store records one (our `valid_to` and `## Previous statement`; Graphiti's `invalid_at`/`expired_at`; Mem0's delete and update events).</sub>

<sub>Over-retraction proper - the memory stopped serving a fact that was still true, by retiring the note or by absorbing another fact into it - is the first two cause columns as a rate: 0.000 [0.000, 0.088] for Nevertwice over its control case-runs.</sub>

<sub>**Withdrawn** cells: owner decision pending: the zep arm's model traffic was not observed (Graphiti's openai SDK sends through a vendored httpx the pacer does not hook); the server log shows no failed call</sub>
<!-- /claims:supersession-causes-implicit -->

**The one loss the write path leaves standing, and the switch that removes it.** Rule one
is the extractor's own word: when it writes `contradicts: <another note's title>`, that note is
retired. On a control case where two facts on one topic are both true, a wrong `contradicts` retires
a fact that still holds - and it is the only loss left on either corpus. `NEVERTWICE_EXPLICIT_RETIRE`
decides what that claim does: `write` retires at once, `judge` sends the pair to the sleep-time judge
like any other contested pair. The campaign ran both arms on both corpora in both readings, and the
rule that picks the default was written before the runs. What the switch costs and what it buys:

<!-- claims:supersession-switch -->
| corpus and reading | default | with the switch on |
|---|---|---|
| explicit, between nights - retires a still-true fact | 0.000 | withdrawn |
| explicit, between nights - returns the retracted fact | 0.017 | withdrawn |
| explicit, after consolidation - retires a still-true fact | 0.000 | withdrawn |
| explicit, after consolidation - returns the retracted fact | 0.017 | withdrawn |
| implicit, between nights - retires a still-true fact | 0.000 | withdrawn |
| implicit, between nights - returns the retracted fact | 0.067 | withdrawn |
| implicit, after consolidation - retires a still-true fact | 0.000 | withdrawn |
| implicit, after consolidation - returns the retracted fact | 0.067 | withdrawn |

<sub>**Withdrawn** cells: historical: the NEVERTWICE_EXPLICIT_RETIRE=judge arm, evaluated at K8-C and decided against on 2026-09-18: the default stays write, so this measures a mode the project does not ship; its artifacts predate HEAD and remeasure --restore refuses an artifact older than the commit, by design. If the arm is wanted alive it returns as an arm in a later window, re-measured, not restored</sub>
<!-- /claims:supersession-switch -->

**How the absorb was found, and what it is not.** The first reading of this run's artifact called
these misses "written, live, ranked below the top five", because the bench matched the marker
anywhere in the note file - and the old statement is still in the file, under `## Previous
statement`. The ledger's first hypothesis was dilution: the literal-fact block appended to every
description pushing the right note down the ranking. A probe built for it
([`facts_dilution_probe.py`](facts_dilution_probe.py), gate written first) ranked the correct
note for every implicit control with the block in the notes' cached text and with it stripped and
re-embedded: the rank was the same on every one of the twenty, and on all seven named cases
(worse 0, better 0). Dilution is **not confirmed**. Re-ingesting two of the named cases and
reading the note files showed the absorb instead, and the bench now classifies a control miss
against the text recall serves. Re-reading the store settled which path absorbs: the case files
hold *one* note for the two sessions, so it is the same-day same-title rewrite, decided by the
title alone, not the cosine twin gate (which retires into `Superseded/`, and retired nothing here).
The working directory the hook's preamble injects still ends nearly every `[facts]` block on this
stand and has its own gate in the ledger (K6) as noise, not as the cause.

**The engine before J2b, under the same classifier.** Was the absorb bought by J2b and the
literal channel? The commit before both was checked out into a worktree and run under today's
bench, two runs per corpus (`research/results/supersession_baseline_*.json`, registered as
historical claims that cite nothing). The absorb was already there: on the explicit corpus the
older engine lost a still-true fact to retirement or absorption on four of forty control case-runs
against five at the J2b engine - the same within noise; on the implicit corpus seven of forty then
against fourteen there, while its never-written misses fell from eight to none and its stale rate
halved. (Both of those columns read zero on the engine that ships today; K8 is what closed them.) The
total control miss on that corpus barely moved; what moved is its cause - from the extractor's
silence to over-consolidation - and that is a finding, not a confirmation: the J2b prompt made
session two write more often, and every extra same-title note it wrote was absorbed. The cap J2b
registered for over-retraction was missed on both engines once the metric is read correctly; the
fix is gated as K7 and measured in the campaign that follows.

**The fix, measured and reverted (K7).** A same-title note from another session is now rewritten in
place only when it is the same fact: literals that agree absorb without a call, anything else goes to
one adjudication call on the extraction model, and a *separate* verdict keeps both notes. Two runs per
corpus at the commit that shipped it on: not one still-true fact was absorbed or retired on eighty
control case-runs - over-retraction proper went to nil on both corpora, and the control-miss column,
the worst on this stand, became the extractor's silence alone. The price was the stale column: up by
eight points on the explicit corpus and ten on the implicit, four to five times the cap the gate
wrote. By its own rule the mechanism ships off (`NEVERTWICE_ABSORB_JUDGE=1` opts in) and the record
stays: `research/results/supersession_k7_*.json`, registered as historical families that cite nothing.
Neither engine has both columns; the stale figure above was partly bought by over-consolidation, and
this page now says so on both sides.

**A confound this stand found in itself.** Reading where the stale went showed that the *call alone*
costs half of it: with the judge's verdict ignored - the call made, the absorb done regardless - the
same sixty implicit cases lost eleven same-title collisions and gained five stale facts against the
no-call control. An extra request to the extraction model between two sessions leaves the server's
prompt cache cold for the next one, and a cold prefill is not the warm one even at temperature zero:
the next session titles its items differently, the titles stop colliding, and the same-title path that
does most of this stand's supersession work fires less. Every campaign figure on this page is the
warm-path one - one uniform stream of extraction calls, which is what made two runs agree to the
digit. The field is mostly cold (a shared local server swaps models between hook calls), so the stale
rate published here overstates what a user sees, by an amount this stand cannot yet put a number on.

Reading the cause needs the arm's store. Ours records `valid_to`, and the absorb leaves the
`## Previous statement` block; Graphiti's edges carry `invalid_at`/`expired_at` and are read from
the group's own graph; Mem0's `get_all` and its add-events (a DELETE, or an UPDATE that drops the
marker) play the same roles; the floor stores every sentence and never retires one. On this run
Graphiti's nine explicit and eleven implicit misses were all *never written* - no edge carried the
fact - and Mem0 missed nothing.

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
| stale, of 60 | 4 | **1** |
| current, of 60 | 52 | **56** |

The condition is now resolved in Python and the prompt names one language. The detector counts
letters rather than bytes, because a Russian session is full of Latin identifiers and a
majority vote reads almost every bilingual transcript as English - which, on this owner's
bilingual store, is the population that matters.

**The extractor is not deterministic, and that is a property of every number on
this page.** The local extraction model is pinned to temperature zero, and two runs of one commit on
one corpus still differ: on the implicit corpus two draws disagreed on more than a third of the
cases' note counts. Every engine figure here is therefore pooled over two runs with the per-run
values kept beside it in the artifact, and the two modes of the switch above are compared only within
one draw. A single run of this stand is not a result, and a difference smaller than the spread
between two runs of the same commit is not a finding.

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
python research/supersession_bench.py --arms nevertwice,naive --out run1.json
python research/supersession_bench.py --arms nevertwice --out run2.json
python research/supersession_bench.py --arms mem0 --out mem0.json      # needs mem0ai[extras]
python research/supersession_bench.py --arms zep --out zep.json        # needs graphiti-core + FalkorDB
python research/supersession_bench.py --pool run1.json run2.json --with mem0.json zep.json --out research/results/supersession_v1.json
```

The Mem0 arm needs its own environment. It also needs one instance rather than one per case:
Mem0 keeps a process-global migrations store under `~/.mem0`, and a second
`Memory.from_config` in the same process dies with *"already accessed by another instance of
Qdrant client"*. Seventy-nine of eighty cases failed that way on the first attempt and the arm
still printed a summary line - which is precisely how a broken arm gets published as a win, so
the harness now counts per-case errors and refuses to score an arm that mostly failed.

## What this does not show

- **The column that matters most is the one this page had wrong.** Over-retraction is this
  design's own worst failure mode: a still-true fact the memory stops serving is silent data
  loss, and unlike a stale answer nothing downstream can catch it. Until this September this page
  printed it as zero, because the bench looked for a `Superseded/` copy and the twin absorb
  leaves none. Read against the served text it is **five in forty** on the explicit corpus and
  **fourteen in forty** on the implicit one - a third of the controls whose two facts share a
  topic. It rests on forty control case-runs per corpus; the interval on the implicit figure
  is wide. The engine is frozen until the next marked cards arrive; the gate for
  the fix is written in the ledger (K6, K7), and until it runs this number is the price of the
  stale column.
- **Zep/Graphiti's earlier figures were measured on shared graphs.** Graphiti's FalkorDB driver
  keeps one graph per group id, and the arm named its groups by the case index alone in every run and
  in both corpora, so the implicit run of 2026-09-10 ingested case *i* into the graph already
  holding the explicit run's case *i*. Those figures were withdrawn with the rest; this page's
  Graphiti rows are two runs on a flushed FalkorDB with every group id prefixed by the run's own
  name, pooled and published with their spread like ours.
- **n = 120 supersession case-runs.** Enough to separate a few per cent from almost everything many times over; not
  enough to distinguish the pooled stale rate from half of it, and the two runs behind it sit five points apart,
  which says something about a third only in that it will land between.
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
  points behind both. Part of the rise is the scored text now carrying the literals the write
  path kept, which is the honest reading of what a caller gets, and part is the run. The
  axis a user meets every day, did my fact come back, is stated in the same table as the win
  rather than a footnote below it.