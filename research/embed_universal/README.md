# nevertwice-embed universal v1 — plan and protocol

**Goal:** a UNIVERSAL drop-in improvement over stock `bge-m3` for the memory-note domain —
shipped as a ready model (`ollama pull`-able), zero fine-tuning on the user's side. The
per-vault prototype (research/EMBED_SPECIALIZE.md) proved the training signal; this project
makes it vault-agnostic.

## Design decisions (carried from measured results)

1. **Supervision = memory lifecycle, synthesized at scale.** Twins (re-phrasings of one
   lesson), supersede-style revisions, and distinct same-domain siblings — the exact pair
   structure the real vault exhibits — generated over EXTERNAL source material.
2. **Same-language twins only.** Cross-lingual translation pairs measurably destroyed the
   round-2 model (language invariance ≠ lesson identity). Bilingual coverage comes from
   generating RU and EN lessons natively, not from translation pairs.
3. **The real vault is a pure held-out.** Not one real-vault pair enters training; it is
   the transfer benchmark.

## Corpus: maximally diverse external sources (data/raw/)

| domain | sources |
|---|---|
| open-source code + docs | shallow clones: flask, fastapi (incl. RU docs), vuejs/core, redis, ripgrep, ggml |
| books | Project Gutenberg plain-text (science + classics) |
| course / presentation material | microsoft/ML-For-Beginners (lesson+slide format) |
| scientific papers | arXiv API abstracts: cs.LG, q-bio, quant-ph |
| scientific datasets | HuggingFace dataset cards (READMEs) |

A local LLM (qwen2.5:7b) reads each chunk as "material I just worked with" and writes
realistic typed memory notes (mistake/pattern/decision: title, description, prevention,
entities; ~30% Russian). A second pass mints twins (same-language rewrite) and
supersede-revisions for a sample of lessons.

**Held-out domains** (never trained on, form the synthetic test): rust-lang/book,
quant-ph papers, one Gutenberg book.

## Mandatory comparison — three models, identical data

| axis | data | models |
|---|---|---|
| REAL-vault twin detection | the frozen 659-pair test (machine-local, private, never committed) | stock bge-m3 · vault-r1 · **universal v1** |
| REAL-vault retrieval guard | 300 queries × 4.3k docs, two query shapes | same three |
| SYNTHETIC held-out domains | twin pairs + distinct pairs + retrieval over unseen domains | same three |

Success criteria for v1: on the REAL vault (transfer!) universal beats stock bge-m3 on the
twin axis with no guard regression. Beating vault-r1 on its home distribution is NOT
required — r1 trained on that distribution; universal must merely approach it while
remaining domain-general.

## Pipeline (this folder)

    sources.py      fetch external material            -> data/raw/
    gen_corpus.py   chunk + LLM-write lessons          -> data/lessons.jsonl
    gen_pairs.py    twins/supersedes + heldout test    -> data/train_pairs.jsonl, data/test_synth.jsonl
    train.py        LoRA r=16 on bge-m3 (proven recipe)-> models/universal_v1/
    evaluate.py     3-model comparison on all axes     -> results.json + this README's results section

`data/` and `models/` are gitignored (big, and the real-vault eval material is private);
scripts and results are committed.

## Ship path (if criteria met)

merge adapter → GGUF (llama.cpp convert) → HF upload (owner's account) → Nevertwice
defaults `NEVERTWICE_EMBED_MODEL=nevertwice-embed` with bge-m3 fallback; `embed_signature`
change triggers the documented full re-embed on user machines.

## Results (2026-08-18)

Corpus: **2848 synthetic lessons** over 21 external sources (~30% Russian, natively
generated), **1354 training pairs** (twins + supersede-revisions), held-out domains
excluded from training end-to-end. Training: 3 epochs LoRA, ~40s on one RTX 5090.

| metric | stock bge-m3 | vault-r1 | **universal v1** |
|---|---|---|---|
| REAL vault: twin AUC | 0.964 | 0.972 | **0.972** |
| REAL vault: twin recall @ 1% FPR | 0.220 | 0.339 | **0.475** |
| REAL vault: guard title R@1 | 0.877 | 0.933 | 0.890 |
| REAL vault: guard hard R@1 | 0.993 | 0.953 | **0.993** |
| synth held-out: twin AUC | 1.000 | 1.000 | 1.000 |
| synth held-out: guard R@1 | 0.973 | 0.975 | 0.963 |

**Verdict: criteria met, decisively.** On the REAL vault - pure transfer, zero of its
pairs trained on - universal v1 more than DOUBLES stock bge-m3's twin-recall at 1% FPR
(0.220 → 0.475) and even beats the vault-specialized r1 (0.339) **while paying none of
r1's regressions**: hard-query retrieval stays exactly at stock (0.993 vs r1's 0.953).
Domain-diverse synthetic supervision generalized BETTER than training on the target vault
itself - the headline result for the paper.

Honest caveats: (1) the synthetic twin test saturates for all three models (LLM rewrites
are too easy) - only the real vault discriminates, which is why it is the criterion;
(2) title-query R@1 trails r1 (0.890 vs 0.933) - r1 memorized vault phrasing;
(3) single seed, single base model.

## Shipped artifacts (this machine)

- `models/universal_v1/` - LoRA adapter (a few MB, the retrainable artifact)
- `models/universal_v1_merged/` - merged HF checkpoint (**verified identical** to the
  adapter model: cosine 1.000000)
- `models/nevertwice-embed-f16.gguf` (1.15 GB) - imported into Ollama as
  **`nevertwice-embed`**, embedding parity with the HF model **1.0000** incl. Russian.
- **GGUF gotcha (cost one crash):** convert_hf_to_gguf must see the sentence-transformers
  pooling files (`1_Pooling/`, `modules.json`) next to the plain HF checkpoint - without
  them the GGUF loads as a completion model and Ollama's runner dies with a stack-buffer
  error (0xc0000409). With them: `Capabilities: embedding`, clean load.

## Deployment status

**The reference machine RUNS nevertwice-embed since 2026-08-18** (full re-embed 4319/4319,
recall verified end-to-end). The new space is dramatically better separated: distinct
same-project pairs max **0.437** (bge-m3: 0.737), twins median 0.683 - the margin flipped
from −0.05 to **+0.25**. Recalibrated thresholds for this space (env, not code - code
defaults stay bge-m3-calibrated until the product default flips):
`WRITE_DEDUP_PREFILTER=0.45, WRITE_DEDUP_SIM=0.50, WRITE_DEDUP_TWIN_P=0.80,
DEDUP_SIM=0.62` (consolidator); `SIM_FLOOR=0.40` stays (weakest real query top-1: 0.478,
noise now ~0.16). The twin classifier retrained in the new space composes to
**AUC 0.9989, precision 1.000 / twin-recall 0.902** (bge-m3 composition: 0.852).

## Remaining owner steps

1. `huggingface-cli login` then `python hf_publish.py` → publishes the model card, merged
   checkpoint, LoRA adapter and f16 GGUF to `<you>/nevertwice-embed` (users then
   `ollama pull hf.co/<you>/nevertwice-embed`).
2. Product default flip (repo-wide): `NEVERTWICE_EMBED_MODEL=nevertwice-embed` default +
   ship the new-space classifier weights + threshold defaults in one release.
