# External retrieval, on a corpus we did not choose and can now prove we ran

Sixteen retrieval figures were withdrawn in 2026-08. The reason was never that they were wrong.
It was that the file behind them could not be identified: the LongMemEval corpus is third-party
and was not committed, and **no content hash was recorded when the numbers were produced**. A
result that cannot name its own input describes nothing, so it came down.

This page is the same benchmark, re-run, with that hole closed.

## What changed

`research/corpus_pin.py` holds the hash. The corpus still is not committed - it is 15 MB of
third-party data for the oracle variant and 278 MB for the standard one - but its fingerprint is
in source, together with the URL it came from and the licence that lets you fetch it. Every
harness calls `verify()` before it reads a byte and stamps the fingerprint into its result file.

```
python research/corpus_pin.py --fetch longmemeval_oracle   # 15 MB, MIT, from the authors
python research/corpus_pin.py --verify longmemeval_oracle
python research/longmem_eval.py --embed                    # about a minute on a local GPU
python research/longmem_eval.py --save --out=research/results/longmem_oracle.json
```

`verify()` raises rather than warns. A warning about a corpus mismatch is a warning nobody reads
until the numbers are already public.

## The setting

**LongMemEval** (Wu et al., ICLR 2025, MIT). Each question carries **human-annotated** evidence
sessions, so relevance comes from the dataset's authors rather than from our embeddings. That is
what makes it worth restoring first: it is the one number here that cannot be a self-grade.

The oracle variant ships each question with its evidence plus a few distractors. Pooling it
**globally** - all 940 unique sessions in one store, every question retrieving from all of them -
turns it into a real retrieval task rather than the easy per-question setting the variant's name
suggests. That global pooling is what `longmem_eval.py` has always done, and it is why the
harness's own docstring calls it "the GLOBAL-pool variant".

## Result: the withdrawn numbers were right

| method | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| semantic (bge-m3 bi-encoder) | 0.422 | 0.652 | 0.728 | 0.528 |
| lexical (term overlap, no embedder) | 0.522 | 0.752 | 0.834 | 0.623 |
| **calibrated score fusion** (shipped default) | 0.550 | 0.802 | 0.858 | 0.657 |
| **+ trained cross-encoder** (opt-in) | 0.614 | 0.826 | 0.858 | 0.712 |

Re-measured on the pinned corpus, the figures come back **identical to three decimals** to the
ones withdrawn in 2026-08. That is worth stating plainly in both directions. It means the
retraction cost nothing in accuracy - and it means the retraction was still right, because at the
time nobody could have shown this. Provenance is not a formality that turned out to be
unnecessary; it is the thing that lets this paragraph exist.

The withdrawn claims stay withdrawn. They were measured on a file that still cannot be
identified, and reviving them because a *later* run agrees would be assuming the conclusion. The
numbers below are new claims, on a named corpus, with the hash in the artifact.

## Head to head, same stand

| system | R@1 | R@5 | R@10 | MRR | ingest | query |
|---|---|---|---|---|---|---|
| **Nevertwice** (calibrated fusion) | 0.550 | 0.802 | 0.858 | 0.651 | n/a | 55s |
| Mem0 2.0.19 | 0.478 | 0.758 | 0.846 | 0.603 | 71s | 96s |
| LangMem 0.0.30 | 0.426 | 0.692 | 0.782 | 0.543 | 63s | 37s |
| A-MEM (chromadb 1.5.9) | 0.428 | 0.692 | 0.782 | 0.544 | 81s | 25s |

Nevertwice's ingest column is blank because there is nothing to ingest: it reads the pool
directly and has no store to populate, which is the same property that makes it zero-dependency
and also means the column is not a like-for-like cost comparison. Query time is like for like.

**Every one of these sixteen figures matches the 2026-07 run to three decimals** - ours and all
three competitors'. The retraction cost nothing in accuracy. It was still correct, because at the
time nobody could have shown that, and a number whose input cannot be named is not a weaker
number, it is a different kind of object.

Every system ingests the same sessions, is queried with the same 500 questions, and is scored by
the same function against the same human-annotated ground truth. All of them use the same local
embedder, so this isolates the memory pipeline rather than the embedder.

## What is still missing

- **This is one dataset.** It is external and it is not one we built, which is the point, but a
  single benchmark is a single benchmark. BEAM remains the other candidate.
- **Two systems record a blocker rather than a number.** Zep needs Neo4j or FalkorDB, and Cognee
  needs an adapter nobody has written. A blocker is recorded instead of an estimate.
- **Competitor versions move.** The result file records the installed version of every package it
  compared against, so a citation can be pinned to the versions that produced it.
