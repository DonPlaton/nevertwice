# Coding sessions with gold answers: the fact, the change, the lesson, the situation

Every memory benchmark this repository has run asks chat questions. A coding agent asks four
other things: the literal value stated weeks ago, the value after it changed, the lesson learned
the hard way, and - at the moment of a tool call - which note should fire. This page is the stand
for those four, on a corpus built for it, with the numbers the register holds.

## The corpus

Two halves, kept apart on purpose. **The program chooses the facts and writes the gold**
([`gen_code_sessions.py`](gen_code_sessions.py)): every synthetic project draws durable facts
from pools - a port, a version, a timeout, a path, a flag - two of which are replaced in a later
session; three lessons drawn from the guard corpus's table; and the questions with their answers
and markers follow from the draw. **A model from a foreign family writes the prose**: the
extractor, reader and judge on every stand here are Qwen, so the sessions are written by GLM,
told which facts a session must state verbatim and which it must not mention; the program checks
both against the text, retries a session that fails, and marks a fallback sentence where the
model would not comply. The generator model, seed, server version and model digest are stamped
in the corpus.

A **real held-out** stands beside it: one literal-fact question per slice of the owner's own
coding sessions, drafted by the same foreign model and **accepted by the owner by hand**
([`heldout_review.py`](heldout_review.py) builds the candidates and a local page to mark them on;
the automatic checks are shown per candidate but do not decide, because the model rewrites the
sentence it quotes in nearly half the slices it read correctly). The transcripts, questions and
answers stay outside the repository; `research/data/code_heldout_review_manifest.json` records the
corpus hash and the counts. The earlier auto-filtered set (v1, fourteen questions) is superseded
by it and its claims are dropped from the register.

## Gates written first

For the corpus: the no-memory reader at or below a tenth, the gold session whole at or above
six tenths, the append-only floor at least a fifth below the oracle - else the corpus does not
separate and is not published. For the extractor (`.loop/GOAL-CLOSE.md`, J3): literal-fact
accuracy not below the floor and not below Mem0's pipeline minus the judges' disagreement;
lesson accuracy above Mem0's pipeline by the same margin. For the write path (the J3 addendum):
**fact survival** - the share of questions whose answer is verbatim in the notes returned, counted
with no reader and no judge - at or above 0.60 on the held-out, with reader accuracy at or above
0.40; the base (five of fifty-two), the cost caps and the decision on a miss are in the ledger.

<!-- claims:code-sessions -->
| system | fact | current | stale | lesson | situation (top three) | tokens |
|---|---|---|---|---|---|---|
| **Nevertwice, our extractor's notes** | 0.083 | 0.033 | 0.017 | 0.600 | 0.000 | 113 |
| append-only sessions, term overlap (floor) | 0.967 | 0.850 | 0.083 | 0.956 | 0.922 | 2,937 |
| Mem0 full pipeline, its memories | 0.683 | 0.750 | 0.117 | 0.700 | 0.033 | 187 |
| no memory (bracket) | 0.067 | 0.017 | 0.050 | 0.478 | 0.000 | 117 |
| the gold session whole (bracket) | 0.978 | 0.967 | 0.000 | 1.000 | 1.000 | 727 |
<!-- /claims:code-sessions -->

## What the first run says

**The synthetic corpus does not separate, and is not published as a benchmark.** Two of its three
gates written before the run fail: the append-only floor lands near the gold-session ceiling
rather than a fifth below it, and the reader with no memory clears its cap, because the lessons
are generic programming advice a seven-billion-parameter reader already knows. Five short
sessions per project are few enough that term overlap finds the right one nearly every time; a
corpus that a text file passes is a corpus about text files. The table above is the re-measure on
the layer-free engine (2026-09-11). The corpus stands as a diagnostic, not as a result - and note
that the literal-fact channel which lifts the hand-marked held-out below did not move this set at
all (fact 0.083 before and after): its facts sit in prose the harvester's literal shapes do not
catch, and the extractor names none of them.

**What it diagnoses anyway.** On coding sessions with literal facts, our extractor's notes answer a twelfth of the fact questions where Mem0's sentence-keeping
pipeline answers seven in ten, and none of the situations where the floor answers nine in ten:
the note is aligned to the lesson, the question asks for the value, and a tool call does not
retrieve a lesson written about it. The lesson column is the one place the note store is at
home, and there it trails Mem0 by more than the judges' disagreement. The extraction gate of
ledger J3 (not below the floor on facts and not below Mem0 by the judges' margin; above Mem0 on
lessons) is missed on every clause.

**What the next corpus needs**, written before it is built: sessions long enough that a
question's session is not the only one that mentions its terms, many more sessions per project
with distractors that share vocabulary, and lessons the reader cannot know without the store -
project-specific facts phrased as lessons rather than the anti-patterns every model has read.

## The real held-out

<!-- claims:code-heldout -->
| system | fact | current | stale | lesson | situation (top three) | tokens |
|---|---|---|---|---|---|---|
| **Nevertwice, our extractor's notes** | 0.404 | - | - | - | - | 222 |
| append-only sessions, term overlap (floor) | 0.827 | - | - | - | - | 569 |
| Mem0 full pipeline, its memories | 0.250 | - | - | - | - | 180 |
| no memory (bracket) | 0.000 | - | - | - | - | 122 |
| the gold session whole (bracket) | 0.827 | - | - | - | - | 569 |
<!-- /claims:code-heldout -->

Two hundred candidates over thirty-seven transcripts; the automatic checks accepted thirty-nine,
and the owner, reading each slice, accepted fifty-two of the seventy-three he marked - seven in ten
against the filter's one in seven,
because almost every automatic drop was the model rephrasing a sentence it had read correctly.
Each question's slice is the only session its project has, so the floor and the ceiling read the
same text and the retrieval gate is vacuous here by construction: **by retrieval this corpus does
not separate and is not a memory benchmark; by extraction it separates hard, and that is the stand
it is published as.** The no-memory bracket reads zero, which is what a held-out over private
sessions should read.

**Where the fact was lost, measured without a model.** Before the write-path fix the answer was
literally present in the notes our arm returned for five of the fifty-two questions: the fact never reached
a note - the extractor summarised it away (*every session in a window of its own* became "regrouped
elements for better organization"). That is
the metric this page gates on, **fact survival**, counted in seconds with no reader and no judge.
The literal-fact channel - the extractor names the literal per note and a deterministic harvester
salvages the ones it dropped, each kept only if it is a verbatim substring of the session - takes
it from under a tenth to **0.635**, and the reader's accuracy from 0.058 to **0.404**, against Mem0's
pipeline at 0.288 and 0.250. The gate written first (survival at or above 0.60 with accuracy at
or above 0.40) is met on this final measure; a deterministic development run read 0.596, so the
gate sits inside the run-to-run band and the page says so rather than rounding it away. One more
honesty: the channel was iterated against this set in the fast loop the ledger prescribed, so for
that mechanism this is a development set; the candidates still unmarked are the clean measure owed
next.

<!-- claims:code-heldout-survival -->
| system | fact survival (answer verbatim in the returned notes) |
|---|---|
| **Nevertwice, our extractor's notes** | 0.635 |
| append-only sessions, term overlap (floor) | 1.000 |
| Mem0 full pipeline, its memories | 0.288 |
<!-- /claims:code-heldout-survival -->

## Reproducing

```
python research/gen_code_sessions.py --check
python research/code_sessions_eval.py contexts --arm nevertwice_full
python research/code_sessions_eval.py contexts --arm naive
python research/code_sessions_eval.py answer --arms nevertwice_full,naive
python research/code_sessions_eval.py judge  --arms nevertwice_full,naive --save
python research/heldout_review.py                          # candidates + the marking page, polygon only
python research/code_sessions_eval.py contexts --arm nevertwice_full --corpus <the private held-out>
python research/code_sessions_eval.py fact-survival --arms nevertwice_full,naive --corpus <the private held-out>
```
