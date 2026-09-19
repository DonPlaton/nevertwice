# LoCoMo, the benchmark this project excluded, measured

For months the roadmap has carried one sentence about LoCoMo: **not a candidate**, because plain
BM25 is reported to score about 94% on it, so it no longer separates memory systems and a vendor
headline there says little about one. A test even pinned the wording, so the exclusion could not
quietly disappear.

That is a claim about a benchmark. This project's own rule is that a claim needs a measurement,
and refusing to run something because you believe it is saturated - without showing that it is -
is an argument from authority where the authority is us. So it was run, on the same stand as
everything else.

The result does not confirm the exclusion and does not refute it either. It shows the exclusion
was reasoning about **a different axis** than the one this system is built for.

## Two axes, and only one of them was ever the argument

LoCoMo ships human-annotated evidence turns: every question names the `dia_id` of the line that
answers it. That supports two entirely different measurements.

| | what it scores | who publishes it |
|---|---|---|
| **retrieval** | did the system return the annotated evidence turn | this page |
| **answer accuracy** | did a reader model, given what the system returned, produce the right answer, as scored by a judge model | the published LoCoMo headlines, including the vendor figures |

The "94% for BM25" concern is about the second. It is a statement that once a trivial retriever
feeds a strong reader, the reader carries the task and the memory stops mattering. That is a
reasonable worry and this page does not test it, because it has no reader and no judge.

What this page tests is the first, which is the axis the retrieval half of this system actually
occupies, and on which every other number in this repository is reported.

## Result

Per conversation, which is LoCoMo's own setting: each question is about one long dialogue and
the haystack is that dialogue's turns. 10 conversations, 5,882 turns, **1,977 of the 1,986
questions scored**. Nine are dropped and each is accounted for: four carry no annotated evidence
at all, and five point at a turn id that is not in the conversation's own turns. Seven of the
nine are category 3.

<!-- claims:locomo -->
> **Withdrawn 2026-09.** the part-four review changed the engine (privacy gate, stem parser, recurrence ceiling, tag fold); the numbers need one GPU campaign (extractor + judge + embedder) at the post-review HEAD before they can be restored
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/locomo_eval.py --save --out=research/results/locomo.json` is what re-measures this one.
<!-- /claims:locomo -->

Per category at R@5 for the shipped ranker (this table and the one above are generated from the
register; until 2026-09-11 both were typed by hand here and had drifted a whole engine revision -
the morphology of 2026-09-06 - behind the figures the register carried):

<!-- claims:locomo-categories -->
> **Withdrawn 2026-09.** the part-four review changed the engine (privacy gate, stem parser, recurrence ceiling, tag fold); the numbers need one GPU campaign (extractor + judge + embedder) at the post-review HEAD before they can be restored
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/locomo_eval.py --save --out=research/results/locomo.json` is what re-measures this one.
<!-- /claims:locomo-categories -->

Category 3 is the hardest and is also where seven of the nine dropped questions sit.

**The term-overlap floor scores 0.601 at R@5, not 0.94.** On the retrieval axis LoCoMo separates
systems perfectly well: fusion is four points above the lexical floor and twenty-one above the bi-encoder,
and the three methods are ordered the same way they are on LongMemEval. Whatever is saturated
about LoCoMo, it is not this.

Lexical beating semantic is the same pattern LongMemEval shows, and more pronounced here: a
dialogue turn is one short line, which gives a bi-encoder very little to work with and a term
match quite a lot.

## Head to head on the global pool

A competitor store holds one collection, so the fair comparison pools the turns of all ten
conversations into one store for every system and asks each question against all of it. That is
harder than LoCoMo's own setting above, and it is what a user's whole history looks like. Same
embedder, same scoring function, same evidence ids; the competitor rows are their store layers,
labelled as such (`research/EXTERNAL_RETRIEVAL.md` says what each is).

<!-- claims:head-to-head-locomo -->
> **Withdrawn 2026-09.** the part-four review changed the engine (privacy gate, stem parser, recurrence ceiling, tag fold); the numbers need one GPU campaign (extractor + judge + embedder) at the post-review HEAD before they can be restored
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/head_to_head.py --data=locomo --only=nevertwice,mem0,langmem,amem --save --out=research/results/head_to_head_locomo.json` is what re-measures this one.
<!-- /claims:head-to-head-locomo -->

## What running it found first

The first run of this file reported semantic recall of **0.037** - near enough to noise that it
would have read as "the bi-encoder collapses on dialogue turns". It was a defect in the harness,
not a finding about the ranker.

LoCoMo numbers turns per conversation: every one of the ten starts at `D1:1`, so `D1:3` names ten
different lines. The embedding cache was keyed by `dia_id` alone, and 5,882 turns collapsed into
1,033 entries, each holding whichever conversation happened to be embedded last. Eighty-three per
cent of the semantic arm's vectors belonged to a different dialogue. The lexical arm was
untouched, because it reads text from the per-conversation pool, which is exactly why the result
looked plausible enough to publish: one arm was broken and the other vouched for the stand.

Namespacing the ids with the conversation moved semantic recall from 0.037 to 0.182.

## What this does not show

- **The axis the exclusion was about is still not measured.** Answer accuracy needs a reader and
  a judge, and a local judge would produce a number that is mostly about the judge. The concern
  that a strong reader washes out the memory's contribution stands, unmeasured here.
- **A number from this page must never be compared with a published LoCoMo accuracy figure.**
  They are different quantities. A vendor's ninety-odd per cent and our 0.640 are not on the same scale, not
  measuring the same thing, and putting them in one table would be the most misleading thing
  this repository could do with either.
- **Nine questions are unscoreable and are dropped, not counted as failures.** Four have no
  annotated evidence and five name a turn absent from their conversation. Dropping them is the
  right call for a retrieval metric - there is nothing to retrieve - but it means this page
  scores 1,977 of 1,986, and a system that correctly declined to answer them would get no credit
  here either way.
- **One embedder, one machine**, as everywhere else here.

## Reproducing

```bash
python research/corpus_pin.py --fetch locomo10     # 2.8 MB, CC-BY-NC-4.0, hash-verified
python research/locomo_eval.py --embed             # about five minutes on a local GPU
python research/locomo_eval.py --save --out=research/results/locomo.json
```
