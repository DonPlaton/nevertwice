# Matryoshka — the mechanism does nothing, and the product win was already there

**Run 2026-08-27** on the frozen external held-out set, RTX 5090, fp16. Thresholds fixed first in
[`EMBED_M4_THRESHOLD.md`](EMBED_M4_THRESHOLD.md) and committed before anything was trained.
Artifacts: `research/embed_universal/heldout/matryoshka_v1.json`,
`heldout/matryoshka_v1_training.json`.

---

## Verdict

| | declared before the run | measured | |
|---|---|---|---|
| **K1** full vector vs shipped v1 | no regression worse than −0.02 | title r@1 **+0.003**, situation r@5 **+0.009** | pass |
| **K2** 512d vs its own full vector | within −0.03 | **−0.0278** | pass |
| **K2** 256d vs its own full vector | within −0.06 | **−0.0602** | **fail** |
| **K3** v1 truncated naively, 256d | reported | **−0.0602** | identical |

**K2 fails at 256 dimensions by 0.0002.** That is a bright line and it is not being rounded
across. It is also the least important sentence here, because K3 makes the decision the same
either way: **v1 truncated naively loses exactly the same amount, to four decimal places.** The
Matryoshka training bought nothing.

Per the deletion decision's second branch — *"K1 passes, K2 fails → the model is still deleted…
a full vector that is merely unharmed is not a reason to keep a model"* — the checkpoint is
deleted and v1 stays shipped.

## The useful result is the baseline, not the mechanism

K3 existed because this project has been caught by an unbaselined mechanism before. It caught one
again, and this time the baseline is the deliverable:

| width | index for 408 notes | bytes/vector | situation recall@5, shipped v1 | cost vs full |
|---|---|---|---|---|
| 1024 | 1632 KiB | 4096 | 0.833 [0.782, 0.880] | — |
| 512 | 816 KiB | 2048 | 0.806 [0.755, 0.857] | −0.028 [−0.056, −0.005] |
| 256 | 408 KiB | 1024 | 0.773 [0.718, 0.829] | −0.060 [−0.097, −0.028] |

**The model that is already shipped truncates.** Halving the index costs under three points of
recall@5; quartering it costs six. The title axis barely moves at all (−0.005 at 256d) and the
twin axis does not move: AUC stays 1.000 at every width, for both models.

That is a product decision available today, with no training, no new checkpoint, and no
migration: keep the first N dimensions of the vector and renormalise. The owner's live embedding
cache is 96 MB; at 256 dimensions the same content is 24 MB.

## Why the training added nothing

bge-m3 is a CLS-pooled model whose representation is evidently already front-loaded — the
information that matters is concentrated in the early dimensions before anyone asks it to be.
Matryoshka training exists to *create* that property in models that lack it. Here it was already
present, so the constraint had nothing to add, and the two models truncate identically.

The honest form of that sentence is narrower: **on this benchmark, at these widths, the
constraint changed nothing measurable.** 216 queries resolve about five points, and the whole
effect being looked for was smaller than that. A real difference of one or two points between the
two models would be invisible here, and this run cannot exclude it.

## What was held constant

The Matryoshka wrapper is the only difference from v1: same (anchor, positive) pairs, same
`MultipleNegativesRankingLoss` inside, same LoRA r=16, 3 epochs, batch 24, lr 1e-4, seed 11 —
and deliberately **not** M2's mined triplets, which failed their own gate. 59 s, peak 10.5 GB.

Truncation is prefix-then-renormalise, which is what an index would do: cosine over an
unnormalised prefix is not cosine, and skipping the renormalise would have measured a bug
instead of a representation.

The merged checkpoint was verified cls-pooled through the published load path before evaluation.

## Hardware

Free VRAM 30.2 of 31.8 GB against a required margin of 20; Ollama left running for the reason
recorded in M2.

## What follows

- **The truncation table is the M4 deliverable**, and it belongs in the model card (M7) as a
  supported operating mode of the shipped model rather than as a research note.
- **M6 should package the shipped model with the widths documented**, since the saving needs no
  new weights.
- Nothing here argues Matryoshka training is useless in general. It argues it is redundant on a
  representation that is already front-loaded, and that checking whether truncation already works
  costs one evaluation run and should precede the training run rather than follow it.
