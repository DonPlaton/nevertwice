---
license: mit
base_model: BAAI/bge-m3
base_model_relation: finetune
pipeline_tag: feature-extraction
library_name: sentence-transformers
tags:
- sentence-transformers
- embeddings
- memory
- agent-memory
- deduplication
- bge-m3
- gguf
- ollama
language:
- en
- ru
- multilingual
---

# nevertwice-embed

[BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3), LoRA-specialised for **AI-agent memory**: telling
*"the same lesson, re-phrased"* from *"a different lesson on the same topic"*, and finding the note
that matches a situation you are heading into.

Built for [Nevertwice](https://github.com/DonPlaton/nevertwice), useful for any note-shaped retrieval.

## Read this first

This card separates two kinds of evidence, because they are not equally checkable.

- **Reproducible** — measured on a frozen external set built from public material
  (rust-lang/book, arXiv quant-ph, a Project Gutenberg title), committed with a content hash and a
  harness you can run. **On it, this model's advantage over stock `bge-m3` does not resolve from
  noise.**
- **Not reproducible** — measured on the author's private note store. Those numbers may well be
  true of that store; nobody outside it can check them, and they are labelled as such rather than
  quoted as if they were the same kind of thing.

The reproducible evidence is the headline. That is a deliberate demotion of this model's original
claim, made after measuring it properly.

## Reproducible results — external held-out set

408 synthetic memory notes over three domains never trained on, 284 English / 124 Russian.
Percentile bootstrap, 2000 resamples; comparisons are **paired** on the same queries.

| axis | stock bge-m3 | nevertwice-embed | paired difference |
|---|---|---|---|
| situation query → note, recall@1 | 0.551 | 0.579 | **+0.028 [−0.005, +0.065]** |
| situation query → note, recall@5 | 0.801 | 0.833 | **+0.032 [−0.005, +0.069]** |
| title query → note, recall@1 | 0.973 | 0.963 | −0.010 [−0.025, +0.003] |
| twin detection, AUC | 1.000 | 1.000 | — |

**Both differences on the axis that matters contain zero.** McNemar: 10 queries won, 4 lost,
exact p = .18. The point estimate leans the right way; 216 queries cannot resolve it.

Two axes are reported and then set aside because they measure nothing here: **twin detection is
saturated** — every model scores AUC 1.000, including untuned `bge-m3` — and **title queries are at
ceiling** for all models, because the query is a verbatim prefix of its answer. A gain claimed on
either would be unfalsifiable.

## Not reproducible — the author's private store

Kept, not withdrawn, and clearly marked. 4.3k notes, 659 twin pairs, none of them in training:

| metric | stock bge-m3 | nevertwice-embed | vault-specific fine-tune |
|---|---|---|---|
| twin AUC | 0.964 | 0.972 | 0.972 |
| twin recall @ 1% FPR | 0.220 | **0.475** | 0.339 |
| retrieval R@1 (title) | 0.877 | 0.890 | 0.933 |

**Nobody but the author can run this.** The store is private, the pairs are private, and no content
hash of either exists. Treat it as a report, not as evidence — and note that the external set does
not reproduce the size of the effect.

## The vector truncates — use it

The full vector is 1024 dimensions, and **the first N dimensions work on their own**. Keep the
prefix and renormalise:

| width | index for 408 notes | bytes/vector | situation recall@5 | cost |
|---|---|---|---|---|
| 1024 | 1632 KiB | 4096 | 0.833 | — |
| 512 | 816 KiB | 2048 | 0.806 | −0.028 [−0.056, −0.005] |
| 256 | 408 KiB | 1024 | 0.773 | −0.060 [−0.097, −0.028] |

Halve the index for under three points of recall@5, quarter it for six. Title retrieval moves
0.005 at 256 dimensions and twin AUC does not move at all.

```python
v = model.encode(texts, normalize_embeddings=True)[:, :256]
v /= np.linalg.norm(v, axis=1, keepdims=True)   # renormalise, or it is not cosine any more
```

This needed **no Matryoshka training** — a model trained for it truncated identically, so the
property was already there. bge-m3's representation appears front-loaded to begin with.

## What the GGUF costs

The f16 GGUF served by Ollama is **numerically indistinguishable** from the safetensors checkpoint
every number above was measured on: median cosine 1.0000 over 2412 texts, minimum 0.9997, zero
vectors below 0.99, and every retrieval query ranks identically. Stock `bge-m3` shows the same,
so it is a property of the conversion rather than luck.

It is slower: **≈37 ms per text served against ≈6 ms locally** on an RTX 5090, batch 32 versus 64.
Some of that is HTTP and batching rather than the model.

## Training

Supervision is synthesised from the **memory lifecycle**: an LLM writes typed memory notes
(mistake / pattern / decision, ~30% natively Russian) grounded in diverse external material — repos
(flask, fastapi, vue, redis, ripgrep, ggml, an ML course), Gutenberg books, arXiv abstracts, dataset
cards — then mints same-language re-phrasings. 2,848 lessons → **1,463** (anchor, positive) pairs →
`MultipleNegativesRankingLoss`, LoRA r=16 on qkv+dense, 3 epochs, batch 24, lr 1e-4, seed 11.

**The recipe reproduces the method, not the weights.** Re-running it on the same machine with the
same seed lands ~0.018 below the shipped checkpoint on situation recall@5 — inside the noise, and
proof that this is a particular checkpoint rather than a rebuildable artifact. Library versions have
moved and GPU non-determinism does the rest. The LoRA adapter is included so you can start from
what actually shipped.

Cross-lingual translation pairs are excluded. That decision was measured on the twin axis, which is
now known to be saturated, so it rests on evidence that cannot discriminate — it is inherited, not
confirmed, and re-testing it needs a corpus that does not exist yet.

## What was tried and deleted

Four improvements were attempted against thresholds written before each run. **All four failed
their own gates and every checkpoint was deleted:**

| | result |
|---|---|
| hard-negative mining | −0.023 recall@5. It taught the near-duplicate axis, because the mined negatives were near-duplicate notes and a situation query is not a note. |
| cross-encoder distillation | −0.056 recall@5, −0.101 title recall@1. The teacher is *worse* than the student at recall@1; distilling its full ranking transferred that too. |
| Matryoshka training | Identical to naive truncation, to four decimals. |
| LoRA rank sweep (1 / 4 / 16 / 64) | None promoted. The curve falls past r4: 64× the parameters buys **less** than 1×. |

If you are planning one of these, the write-ups say what would have to change first.

## Usage

```python
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("DonPlaton/nevertwice-embed")
emb = model.encode(["fix: pin the CUDA rng state in checkpoints"], normalize_embeddings=True)
```

```bash
ollama pull hf.co/DonPlaton/nevertwice-embed
curl http://localhost:11434/api/embed -d '{"model":"nevertwice-embed","input":"..."}'
```

In Nevertwice: `NEVERTWICE_EMBED_MODEL=nevertwice-embed`, then re-embed the store once.

## Caveats

- **The external advantage over stock `bge-m3` is not statistically resolved.** If you need a
  decision today, the honest one is that this model is at least as good on note retrieval and the
  case for switching rests on the private-store result you cannot verify.
- Optimised for short technical notes (title + 1–3 sentences); not evaluated on long documents.
- The external benchmark is **synthetic** — LLM-written notes over public sources. A model that
  learned the generator's register rather than the task would look good on it. The generator is
  published and hashed so the suspicion is at least checkable.
- 408 documents. An index's difficulty grows with its size; the truncation costs above are not
  established at production scale.
- English and Russian measured (Russian is *ahead*: 0.896 vs 0.816 situation recall@5, n=48). Other
  languages inherit bge-m3 untested.
- Single seed, single base.

## Evidence

Every number above resolves to a committed artifact and a command:
[`research/EMBED_HELDOUT_BASELINE.md`](https://github.com/DonPlaton/nevertwice/blob/master/research/EMBED_HELDOUT_BASELINE.md),
[`EMBED_MATRYOSHKA.md`](https://github.com/DonPlaton/nevertwice/blob/master/research/EMBED_MATRYOSHKA.md),
[`EMBED_SERVING.md`](https://github.com/DonPlaton/nevertwice/blob/master/research/EMBED_SERVING.md),
[`EMBED_CAPACITY.md`](https://github.com/DonPlaton/nevertwice/blob/master/research/EMBED_CAPACITY.md),
[`EMBED_HARD_NEGATIVES.md`](https://github.com/DonPlaton/nevertwice/blob/master/research/EMBED_HARD_NEGATIVES.md),
[`EMBED_DISTILLATION.md`](https://github.com/DonPlaton/nevertwice/blob/master/research/EMBED_DISTILLATION.md).

## License

MIT (base model BAAI/bge-m3 is MIT).
