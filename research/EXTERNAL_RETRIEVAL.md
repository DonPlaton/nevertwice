# External retrieval, on a corpus we did not choose and can now prove we ran

<!-- review-2026-09-05 -->
> **Withdrawn 2026-09-05, pending a re-run.** Every figure below was measured with our
> semantic arm embedding only the first 2,000 characters of each session - the engine's hot-path
> cap, applied by the stand without anyone noticing - while the competitors on the same stand
> embedded the whole session through the same endpoint. The premise "same embedder for
> everyone" was therefore false in our own disfavour. The stand now embeds whole sessions and
> refuses the old vector cache; the numbers return when the re-run has been done on the GPU.
> The figures stay on the page as the record of what was measured, not as results to quote.
> Details: the review section at the end of this page.

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

## The same benchmark outside the oracle setting

The oracle variant pools 940 sessions. The standard variant pools **19,206** - the same 500
questions, the same annotated evidence, twenty-one times the haystack. This is the setting the
roadmap meant when it asked for a number "outside the oracle setting", and it is where a
retrieval claim earns its keep.

| method | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| semantic (bge-m3 bi-encoder) | 0.188 | 0.344 | 0.426 | 0.266 |
| lexical (term overlap, no embedder) | 0.242 | 0.442 | 0.534 | 0.338 |
| **calibrated score fusion** (shipped default) | 0.264 | 0.452 | 0.554 | 0.362 |

Everything falls, which is what a twenty-one-fold haystack does, and the shape holds: fusion
beats both signals it fuses, by +0.108 at R@5
over semantic alone and +0.010 over lexical.
Lexical retrieval beats the bi-encoder on this pool, as it did on the smaller one; the fusion is
what makes the pair worth having.

The competitor arms have not been run here. Their cost is ingest, and ingest is twenty-one times
larger; the four-system comparison above stands on the oracle pool.

**623 of the 19,829 sessions carry no text at all** in the published corpus and are
skipped, which is where the pool size comes from. That is a property of the dataset, stated here
so the number is not mistaken for a loading failure on our side.

### What running it found

Two things, neither of them in the results table.

**The Pareto-safety check was passing for the wrong reason.** The harness ranks a
`semantic+recur` arm to prove that the production recurrence prior changes nothing when every
note has recurrence 1 - the boost is exactly 0.0, so adding it must be inert. The two arms broke
ties differently: `semantic` resolved equal scores by session id, `semantic+recur` left them to
the stable sort's fallback order. On 940 sessions the two orders agreed often enough for recall@k
to match and the check printed "inert by construction". On 19,206 they disagreed, and the check
reported a CHANGED ranking for a boost of zero. It had never tested the boost; it had tested the
tie-break, and got away with it because the pool was small. Both arms now use the same key and
the check asserts the two lists are **identical**, which is what "changes nothing" means.

**A larger pool is a different instrument, not the same one further away.** Everything about this
corpus that made the oracle result look stable - few ties, a small candidate set, a generous
top-10 - stops holding at twenty-one times the size. The oracle numbers are not wrong; they
answer an easier question, and this page now prints both so nobody has to guess which one a
headline came from.

## What is still missing

- **This is one dataset, in two sizes.** It is external and not one we built, which is the
  point, but a single benchmark is a single benchmark. BEAM remains the other candidate.
- **The competitors ran on the oracle pool only.** The non-oracle numbers above are ours and the
  two baselines the harness computes; extending the four-system table to the larger pool is
  ingest cost, not new machinery.
- **Two systems record a blocker rather than a number.** Zep needs Neo4j or FalkorDB, and Cognee
  needs an adapter nobody has written. A blocker is recorded instead of an estimate.
- **Competitor versions move.** The result file records the installed version of every package it
  compared against, so a citation can be pinned to the versions that produced it.

## What the review of 2026-09-05 found on this stand

Three things, all of them in the harness rather than in the engine, and all of them fixed
before the re-run rather than after it.

**Our arm embedded a seventh of each session.** `memory_hook.embed_text` cuts its input to
2,000 characters before calling the embedder. That is a latency guard for the per-prompt
query and costs nothing on a note, which is shorter than that. A LongMemEval session is not:
the oracle pool's median is 14,386 characters, 936 of its 940 sessions are longer than the
cap, and the annotated answer turn begins past the cap in 34.9% of the evidence sessions.
The competitors embedded the whole session through the same Ollama endpoint. So the
sentence this page used to carry - *all of them use the same local embedder, so this isolates
the memory pipeline* - was false, and false against us: the semantic arm above was working
from less text than any competitor's. The stand now sends the whole session (up to the
pool's own cap of 28,000 characters) through the same endpoint, the vector cache carries the
cap in its file name and a stamp inside, and a cache built under another cap is refused
rather than loaded. Whether our semantic figure rises is the re-run's business; nothing here
predicts it.

**The stand's ranker was a copy, not a call.** `longmem_eval.calibrated` re-implemented the
engine's calibrated fusion and had drifted from it in one case (a signal's sole candidate).
It is inert on every question of both corpora - checked - and it is gone: the stand calls
`memory_hook._calibrated_fusion`.

**The competitor rows were labelled as more than they were.** Mem0 ran with its LLM
extraction off, which this page did not say; its search is its default hybrid of dense cosine
and fastembed BM25 when fastembed is installed, which it was. "LangMem" was LangGraph's
InMemoryStore search, the product's storage layer without its memory manager. "A-MEM" was
chromadb cosine over the same vectors, the product's store without its LLM note construction
or link evolution. The store arms are kept, labelled as store arms, because they isolate the
retrieval layer the way our arm does; the products' own pipelines are new arms
(`mem0_infer`, `langmem_full`, `amem_full`), and each blocks itself when more than a tenth of
its LLM calls fail silently, which A-MEM's controller otherwise hides.

The re-run replaces every figure on this page. Until then they are the record of a
measurement whose premise did not hold.
