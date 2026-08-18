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

A universal embedding model for **AI-agent memory**: [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3)
LoRA-specialized to tell *"the same lesson, re-phrased"* from *"a different lesson on the
same topic"* — the distinction every memory system's deduplication, supersession and recall
quality hangs on, and the one a general-purpose embedder is weakest at.

Built for [Nevertwice](https://github.com/DonPlaton/nevertwice) (local-first memory for AI
coding agents), useful for any note/lesson-shaped retrieval.

## Why

On a real 4.3k-note agent-memory store, distinct same-project lessons reach cosine 0.74
under stock bge-m3 while true re-phrasings of one lesson sit at 0.78–0.83 — a ~0.05
working margin. This model widens that separation **without training on the target store
at all**.

## Training

Supervision is synthesized from the **memory lifecycle**: an LLM writes realistic typed
memory notes (mistake / pattern / decision, ~30% natively Russian) grounded in maximally
diverse external material — open-source repos (flask, fastapi, vue, redis, ripgrep, ggml,
an ML course), Project Gutenberg books, fresh arXiv abstracts, dataset cards — then mints
same-language re-phrasings (twins) and supersede-style revisions. 2,848 lessons → 1,354
(anchor, positive) pairs → MultipleNegativesRankingLoss, LoRA r=16, 3 epochs.
Cross-lingual translation pairs are deliberately **excluded** — measured to destroy the
objective (language invariance ≠ lesson identity).

## Results (held-out REAL store — zero of its pairs in training)

| metric | stock bge-m3 | nevertwice-embed |
|---|---|---|
| twin-detection AUC | 0.964 | **0.972** |
| twin recall @ 1% FPR | 0.220 | **0.475** |
| retrieval R@1 (title query) | 0.877 | **0.890** |
| retrieval R@1 (content query) | 0.993 | **0.993** |

Twin recall at a fixed 1% false-positive rate **more than doubles**, with retrieval intact.
It also outperformed a model fine-tuned directly on that store's own history (0.339) —
domain-diverse synthetic supervision transferred better than target-store training.

## Usage

**sentence-transformers**

```python
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("DonPlaton/nevertwice-embed")
emb = model.encode(["fix: pin the CUDA rng state in checkpoints"], normalize_embeddings=True)
```

**Ollama (GGUF, f16)**

```bash
ollama pull hf.co/DonPlaton/nevertwice-embed
curl http://localhost:11434/api/embed -d '{"model":"nevertwice-embed","input":"..."}'
```

In Nevertwice: `NEVERTWICE_EMBED_MODEL=nevertwice-embed` (then re-embed the store once).

## Caveats

- Optimized for short technical note/lesson texts (title + 1–3 sentences); not evaluated
  on long-document retrieval.
- Single seed, single base; the LoRA adapter is included for retraining.
- English/Russian validated; other languages inherit bge-m3's behavior untested.

## License

MIT (base model BAAI/bge-m3 is MIT).
