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
> **Not measured yet.** No `code_sessions.*` claim is registered; `python research/code_sessions_eval.py judge --arms nevertwice_full,naive,mem0_infer --save` is the run that produces them.
<!-- /claims:code-sessions -->

## Reproducing

```
python research/gen_code_sessions.py --check
python research/code_sessions_eval.py contexts --arm nevertwice_full
python research/code_sessions_eval.py contexts --arm naive
python research/code_sessions_eval.py answer --arms nevertwice_full,naive
python research/code_sessions_eval.py judge  --arms nevertwice_full,naive --save
```
