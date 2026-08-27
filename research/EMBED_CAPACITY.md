# Capacity sweep — no arm earns promotion, and the curve points down

**Run 2026-08-27** on the frozen external held-out set, RTX 5090, fp16. Rules fixed first in
[`EMBED_M5_THRESHOLD.md`](EMBED_M5_THRESHOLD.md), committed before any rank was trained.
Artifact: `research/embed_universal/heldout/capacity_sweep.json`.

---

## Verdict: none promoted. All four checkpoints deleted.

| arm | trainable params | train s | situation recall@5 | Δ vs shipped v1 |
|---|---|---|---|---|
| r1 | 444,416 | 57 | 0.819 [0.764, 0.870] | −0.014 [−0.046, +0.018] |
| **r4** | 1,777,664 | 62 | **0.838 [0.782, 0.884]** | **+0.005 [−0.018, +0.028]** |
| r16 | 7,110,656 | 61 | 0.815 [0.759, 0.866] | −0.018 [−0.042, 0.000] |
| r64 | 28,442,624 | 61 | 0.806 [0.750, 0.857] | −0.028 [−0.060, 0.000] |

S1 required a paired interval excluding zero. **None of the four does.** Every checkpoint was
deleted as it failed, which is why a four-rank sweep of bge-m3 left no disk behind.

## What the curve says

Sixty-four times the trainable parameters buys **less** than one times. The best arm is r4 at 1.8M
parameters, and past it the numbers fall monotonically: 0.838 → 0.815 → 0.806. On 1463 training
pairs that is what overfitting looks like, and the shipped model sits at r16 — on the descending
half of its own curve.

Not a promotion, because r4's interval contains zero and the rule was written before the numbers
existed. But if a future run wants a rank, **the evidence points at 4, not 16**, and it points
there on the axis that discriminates rather than on the one that does not.

## S2 — the inherited belief, replaced rather than replicated

The prior work concluded **"LoRA r1 won"**, measured on the twin axis. M1 found that axis saturated
at AUC 1.000 for every model including the untuned one, so the original claim was made where
nothing can be distinguished — it can be neither confirmed nor refuted, only asked again somewhere
it means something.

Asked again on `retrieval_situation`, r1 against r16:

| | Δ recall@1 | Δ recall@5 |
|---|---|---|
| situation | +0.019 [−0.014, +0.051] | +0.005 [−0.023, +0.037] |
| title | +0.005 [0.000, +0.012] | 0.000 |

**r1 is not worse than r16, and may be slightly better** — consistent with the original direction,
with intervals that do not resolve it. That is as much support as this benchmark can give, and
combined with the curve above it is a real argument against the shipped rank: r16 costs 16× the
parameters of r1 to land in the same place or lower.

The twin axis stayed at AUC 1.000 for all four arms, confirming again that it cannot rank models.

## The finding nobody was looking for: v1 is not reproducible from its own recipe

The **r16 arm is v1's recipe** — same pairs, loss, epochs, batch, learning rate, seed, targets,
rank. It lands at situation recall@5 **0.815** against the shipped model's **0.833**, a paired
difference of **−0.018 [−0.042, 0.000]**.

Inside the noise, and beside the point. What it means is that **"nevertwice-embed v1" is a
particular checkpoint, not a reproducible artifact**: re-running its committed recipe on the same
machine with the same seed does not land on the same model. Library versions have moved since it
was trained, and GPU non-determinism does the rest.

That is worth knowing before the model card (M7) says anything about how the model was built. The
recipe reproduces the *method*, not the weights, and the card should say which.

## Per-language — the check the product needs

The store is bilingual, so a single average could hide a regression in the smaller half. It does
not; if anything the reverse:

| arm | English situation r@5 (n=168) | Russian situation r@5 (n=48) |
|---|---|---|
| shipped v1 | 0.816 | 0.896 |
| r1 | 0.804 | 0.875 |
| r4 | 0.821 | 0.896 |
| r16 | 0.798 | 0.875 |
| r64 | 0.780 | 0.896 |

Russian is consistently *ahead* of English on this benchmark, at every rank. With 48 Russian
queries that is not a strong result — the interval on a 48-query recall is wide — but it is
evidence against the worry, not for it, and it is the first per-language number this project has
on external material.

## The half of M5 that could not run

Re-testing **"cross-lingual pairs hurt"** was not possible from committed data. `gen_pairs.py`
produces same-language twins *only* — precisely because of the belief being tested — so
`train_pairs.jsonl` contains no cross-lingual arm to compare against. Doing it properly means
generating cross-lingual pairs with the LLM corpus generator and adding a fourth training arm:
a corpus task, not a sweep.

The per-language table above is **not** a substitute and is not offered as one. It measures
whether the model serves both languages, not whether training on translation pairs damages it.

## Method and limits

Everything but the rank is v1's: same pairs, `MultipleNegativesRankingLoss`, 3 epochs, batch 24,
lr 1e-4, seed 11, qkv+dense, `lora_alpha` at 2× the rank as the recipe already did. Every merged
checkpoint was verified cls-pooled through the published load path before evaluation.

216 situation queries resolve about five points of recall@5, so differences smaller than that are
invisible here — and every difference in the table is smaller than that. **Four arms compared
against one baseline also inflates the chance that one clears a 95% threshold by luck**; no
correction is applied because S1 is a promotion rule rather than a hypothesis test, and this
sentence is the disclosure the threshold document promised instead.
