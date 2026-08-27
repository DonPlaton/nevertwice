# The served model is the measured model — the first thing in this track that passed

**Run 2026-08-27.** Thresholds fixed first in [`EMBED_M6_THRESHOLD.md`](EMBED_M6_THRESHOLD.md).
Artifact: `research/embed_universal/heldout/serving_check.json`.

---

## Verdict: both gates pass, and not narrowly

| | declared before the run | measured | |
|---|---|---|---|
| **V1** served vs local, both axes | within ±0.02 | **0.000**, both axes, both cutoffs | pass |
| **V2** vector agreement | median cosine ≥ 0.99 | **median 1.0000, minimum 0.9997** | pass |

Zero of 2412 vectors fall below 0.99. Zero fall below 0.95. Every one of the 624 retrieval
queries ranks its documents **identically** through both paths — the paired deltas are not small,
they are exactly zero.

## Why this needed checking at all

Every number in M1–M5 was produced from `models/universal_v1_merged`, a safetensors checkpoint
loaded through sentence-transformers. Every *user* gets `nevertwice-embed-f16.gguf` through
Ollama's inference stack. Two files, two engines, and nobody had compared them.

This directory carries a review note about precisely this class of gap: a merged checkpoint saved
without `modules.json` loads **mean-pooled** while bge-m3 is CLS-trained, and "every downstream
user gets embeddings the model was never trained or evaluated with" — silently. That trap was
caught once by review. Whether it had been re-opened by the GGUF conversion was, until now,
unknown.

It has not been. **The M1–M5 numbers describe what users actually run**, and the model card may
say so.

## The control that makes it meaningful

Stock `bge-m3` served the same way agrees with its own safetensors weights to a median cosine of
1.0001 and a minimum of 0.9992 — the same picture. So the agreement is a property of the
conversion pipeline generally, not a lucky property of this checkpoint, and f16 quantisation
costs nothing measurable on this workload.

*(A median cosine slightly above 1.0 is float32 accumulation over 1024 dimensions of two
unit-norm vectors, not a model scoring better than itself.)*

## The cost that is real

| path | 2412 texts | per text |
|---|---|---|
| local, sentence-transformers, fp16, batch 64 | 14.5 s | 6 ms |
| served, Ollama, f16 GGUF, batch 32 | 88.1 s | 37 ms |

**The served path is about six times slower.** That is the number any latency claim about this
model has to use, because it is the path the product takes — and it is measured here rather than
extrapolated from the local one. Part of it is HTTP and batching rather than the model, and this
run does not separate those; it measures what a caller experiences.

## Per-language, through the served path

Russian remains ahead of English, exactly as it was locally: situation recall@5 **0.896** against
**0.816**. Since the vectors are identical, that had to be true — but the multilingual check M6
requires is a check on the *served* artifact, and it is now made on the served artifact rather
than assumed from the local one.

## What follows

- **The model card (M7) may quote the M1–M5 numbers as describing the shipped model**, which
  before this run it could not honestly have done.
- **No re-quantisation is needed**, and M4 already established no new weights are needed either.
  M6's original brief — "quantise, package, verify" — is complete because two thirds of it were
  already done and the third has now been checked instead of assumed.
- **The latency figure belongs in the card**, alongside the truncation table.

## Courtesy note

This task read from the owner's running Ollama — the same requests their own memory hook makes —
and started, stopped and pulled nothing. Had `nevertwice-embed` not already been loaded, the
script would have reported that and stopped rather than pulling 1.1 GB into someone else's
working set.
