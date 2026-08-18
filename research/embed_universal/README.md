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

## Results

*(filled by evaluate.py runs)*
