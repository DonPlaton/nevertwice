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
> **Withdrawn 2026-09.** the J1 evidence layer removed and the archive-aware reconcile added in one package (ledger J2b); every temporal, frontier and code-session stand re-measures without spans on the GPU campaign, and the model is bound by name in each artifact
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/frontier_eval.py judge --save` is what re-measures this one.
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
- **Our own extractor's notes are the weakest memory on this stand.** Every question found
  notes from its gold sessions, so this is not the extractor's silence. It is what a note is:
  the prompt is built for coding sessions and writes patterns, mistakes and decisions -
  *"selected lentil bolognese as the primary protein source"* - while LongMemEval asks for the
  literal fact the user stated. Mem0's extractor keeps the sentence; ours keeps the lesson.
  **Withdrawn pending the J2b campaign:** the evidence-span layer this arm was re-measured with
  (ledger J1) missed its gate by a wide margin and has been removed by the owner's rule; the
  frontier accuracy is re-measured on the layer-free engine and the numbers return with that
  run. On a corpus of code sessions the trade looks different, which is what
  `research/CODE_SESSIONS.md` is for.
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
