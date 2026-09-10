# Accuracy per token: every system's context, one reader, one judge

Every vendor publishes an answer-accuracy figure. This repository published retrieval recall,
because recall is what a memory controls and a reader's accuracy is mostly the reader's - and
then measured the answer axis once, on our own pipeline alone (`research/QA_ACCURACY.md`, since
withdrawn with the rest of the unpinned corpus). That left the comparison a stranger actually
wants unmade: on the same questions, with the same reader and the same judge, what does each
system's context buy, and what does it cost in tokens?

This page is that stand (`research/frontier_eval.py`, ledger I3). For every system the context
it would hand the agent at one, three and five hits goes to one local reader; one local judge grades the
answer against LongMemEval's gold; the tokens are the count the reader itself reports for the
prompt, not an estimate. Two brackets frame the curve: the reader with no memory at all, and
the oracle ceiling - the gold evidence sessions, whole. A second local judge re-grades the
shipped arm's answers so the judges' own disagreement is printed beside the accuracies, and a
gap between two systems smaller than that disagreement is not a gap.

## The stand

<!-- claims:frontier -->
| system | k=1 acc | k=1 tokens | k=3 acc | k=3 tokens | k=5 acc | k=5 tokens |
|---|---|---|---|---|---|---|
| **Nevertwice, shipped ranker, sessions whole** | 0.353 | 2,777 | 0.447 | 8,707 | 0.447 | 14,274 |
| Nevertwice, shipped ranker, query passages | 0.293 | 379 | 0.427 | 897 | 0.433 | 1,417 |
| Nevertwice, our extractor's notes | 0.020 | 193 | 0.047 | 333 | 0.067 | 472 |
| Mem0 store search, sessions whole | 0.353 | 2,733 | 0.433 | 8,552 | 0.400 | 13,839 |
| Mem0 full pipeline, its memories | 0.207 | 144 | 0.273 | 184 | 0.333 | 227 |
| A-MEM full pipeline, its notes | 0.027 | 124 | 0.027 | 124 | 0.027 | 124 |

Brackets - no memory, the question alone: accuracy 0.020 at 128 tokens; the oracle ceiling, gold sessions whole: accuracy 0.573 at 5,522 tokens. The two judges disagree on 0.050 of the shipped arm's answers; a gap between two rows smaller than that is not a gap.
<!-- /claims:frontier -->

What the table says, in words, since the numbers are above:

- **Passages, not whole sessions, is where this project's advantage is.** The shipped ranker
  reading whole sessions and Mem0's store search reading whole sessions land on top of each
  other, and both spend five figures of tokens to do it. Cutting each session to the passage
  the cross-encoder reads keeps the accuracy - the drop is inside the judges' own disagreement
  - for a tenth of the tokens. That is the number this stand was built to find.
- **Mem0's own pipeline is the cheapest arm that works.** Its extracted memories answer for a
  couple of hundred tokens, which is less than our passages cost, and it pays for that in
  accuracy. Neither of us is strictly better: it is cheaper, we are more accurate, and the
  page prints both columns rather than choosing the one that flatters us.
- **Our own extractor's notes are the weakest memory on this stand, evidence spans included.**
  Every session produced notes - 2,015 of them, 1,883 carrying the verbatim line they were
  aligned to - and every question found notes from its gold sessions, so this is not the
  extractor's silence. It is what a note is: the prompt is built for coding sessions and writes
  patterns, mistakes and decisions - *"selected lentil bolognese as the primary protein
  source"* - while LongMemEval asks for the literal fact the user stated, and the one line a
  note is aligned to is the line that restates the lesson, not the line that holds the detail
  the question wants. Mem0's extractor keeps the sentence; ours keeps the lesson and one
  sentence of evidence. The gate written for the evidence layer on this stand (ledger J1,
  accuracy at five hits at least Mem0's pipeline plus the judges' disagreement) is missed by a
  wide margin, and the page says so; on a corpus of code sessions the trade looks different,
  which is what `research/CODE_SESSIONS.md` is for.
- **The ceiling is the reader's.** The oracle bracket - the gold evidence sessions, whole -
  is where a small local reader tops out, and no arm can pass it. Read every row against that
  bracket rather than against one, and remember the judges' disagreement is printed with the
  table: a gap narrower than it is not a gap.

Systems, and what each one's context is:

- **Nevertwice, sessions whole** - the shipped ranker's top-k sessions, as `head_to_head.py`
  scores them, each session whole. The retrieval-quality point.
- **Nevertwice, query passages** - the same ranking, each session cut to the passage the
  cross-encoder reads. The token-economy point: what a hook would inject.
- **Nevertwice, our extractor's notes** - every session run through our own extraction with
  the same local model the competitor pipelines use, then `api.recall` over the notes. Our
  pipeline against theirs, on their terms (ledger I4).
- **Mem0 store search** - `infer=False`, its default hybrid over whole sessions.
- **Mem0 full pipeline** and **A-MEM full pipeline** - the products' own extraction; their
  memories read back from the stores the head-to-head run left on disk, so the context is what
  they would inject. LangMem's pipeline keeps its memories in process and is a blocker here.

**Which extractor wrote our notes, and how the stand knows.** The engine binds its model name when it
is imported, and this stand imported it before setting the name, so until 2026-09-10 the
`nevertwice_full` arm ran on whatever `NEVERTWICE_MODEL` the shell exported - on this machine the
owner's live-hook model, which declared nearly every chat session off-topic and wrote almost
nothing. The first published number for that arm was produced by an extractor the page did not
name. The arm now binds the extractor explicitly, refuses to run if the engine reports another
name, and records the model that ran in the artifact beside the count of sessions it left
without a note - the silence no evidence span can lift.

## What it does not show

- **The reader is small and local**, chosen so the stand runs on the same machine as every
  other number here; the oracle bracket says how much of the miss is the reader's. A stronger
  reader moves every curve up and is a different stand, stated as such if run.
- **LLM calls are not measured here.** Each pipeline arm's cost at write time is what its code
  does: ours is one extraction call per session; Mem0's `add` with inference makes an
  extraction call and a memory-update call; A-MEM analyses and then evolves each note. Those
  are statements about the products' code, not measurements, and the page says so.
- **One corpus, one sample.** The LongMemEval-oracle pool, a stratified sample of its
  questions by type; the non-oracle pool and LoCoMo are not on this stand.

## Reproducing

```bash
python research/frontier_eval.py contexts --arm nevertwice_whole
python research/frontier_eval.py contexts --arm nevertwice_snippet
python research/frontier_eval.py contexts --arm nevertwice_full            # runs our extractor
<mem0 venv> python research/frontier_eval.py contexts --arm mem0
<mem0 venv> python research/frontier_eval.py contexts --arm mem0_infer     # after head_to_head --only=mem0_infer
<amem venv> python research/frontier_eval.py contexts --arm amem_full      # after head_to_head --only=amem_full
python research/frontier_eval.py answer --arms nevertwice_whole,nevertwice_snippet,nevertwice_full,mem0,mem0_infer,amem_full
python research/frontier_eval.py judge  --arms ... --save
```
