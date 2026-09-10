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
coding sessions, drafted by the same foreign model and kept only when its quoted sentence is
verbatim in the transcript, its answer is inside that sentence, and a second pass with a
different prompt agrees. The transcripts, questions and answers stay outside the repository;
`research/data/code_heldout_manifest.json` (written by the builder) records counts, drop
reasons and a hash per item. The held-out is touched once, at the end, never during development.

## Gates written first

For the corpus: the no-memory reader at or below a tenth, the gold session whole at or above
six tenths, the append-only floor at least a fifth below the oracle - else the corpus does not
separate and is not published. For the extractor (`.loop/GOAL-CLOSE.md`, J3): literal-fact
accuracy not below the floor and not below Mem0's pipeline minus the judges' disagreement;
lesson accuracy above Mem0's pipeline by the same margin.

<!-- claims:code-sessions -->
| system | fact | current | stale | lesson | situation (top three) | tokens |
|---|---|---|---|---|---|---|
| **Nevertwice, our extractor's notes with evidence spans** | 0.083 | 0.033 | 0.017 | 0.600 | 0.000 | 113 |
| append-only sessions, term overlap (floor) | 0.967 | 0.850 | 0.083 | 0.956 | 0.922 | 2,937 |
| Mem0 full pipeline, its memories | 0.706 | 0.700 | 0.217 | 0.722 | 0.033 | 187 |
| no memory (bracket) | 0.067 | 0.017 | 0.050 | 0.478 | 0.000 | 117 |
| the gold session whole (bracket) | 0.978 | 0.967 | 0.000 | 1.000 | 1.000 | 727 |
<!-- /claims:code-sessions -->

## What the first run says

**The synthetic corpus does not separate, and is not published as a benchmark.** Two of its three
gates written before the run fail: the append-only floor reads 0.942 on fact, change and lesson
questions together against the gold-session ceiling's 0.982 - not a fifth below it - and the
reader with no memory reads 0.170, above the tenth allowed, because the lessons are generic
programming advice a seven-billion-parameter reader already knows. Five short sessions per
project are few enough that term overlap finds the right one nearly every time; a corpus that a
text file passes is a corpus about text files. The table stands as a diagnostic, not as a result.

**What it diagnoses anyway.** On coding sessions with literal facts, our extractor's notes -
evidence spans included - answer a twelfth of the fact questions where Mem0's sentence-keeping
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
| **Nevertwice, our extractor's notes with evidence spans** | 0.143 | - | - | - | - | 322 |
| append-only sessions, term overlap (floor) | 0.714 | - | - | - | - | 7,038 |
| no memory (bracket) | 0.000 | - | - | - | - | 124 |
| the gold session whole (bracket) | 0.714 | - | - | - | - | 7,038 |

<sub>No row for Mem0 full pipeline, its memories: no registered number for the arm.</sub>
<!-- /claims:code-heldout -->

Fourteen questions survived the three checks out of ninety-three transcript slices - far short of
the forty the ledger asked for, and the manifest says where the rest went: the foreign model
rewrites the sentence it claims to quote in nearly half the slices, and its second pass disagrees
with its first in a quarter. Each question's slice is the only session its project has, so the
floor and the ceiling read the same sessions and the floor gate is vacuous here by construction;
the no-memory bracket reads zero, which is what a held-out over private sessions should read.
On these fourteen our extractor's notes answer one question in seven where the raw slice answers
five in seven - the same shape as the synthetic corpus, on sessions nobody generated.

## Reproducing

```
python research/gen_code_sessions.py --check
python research/code_sessions_eval.py contexts --arm nevertwice_full
python research/code_sessions_eval.py contexts --arm naive
python research/code_sessions_eval.py answer --arms nevertwice_full,naive
python research/code_sessions_eval.py judge  --arms nevertwice_full,naive --save
```
