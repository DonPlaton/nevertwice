# External retrieval, on a corpus we did not choose and can now prove we ran

Sixteen retrieval figures were withdrawn in 2026-08. The reason was never that they were wrong.
It was that the file behind them could not be identified: the LongMemEval corpus is third-party
and was not committed, and **no content hash was recorded when the numbers were produced**. A
result that cannot name its own input describes nothing, so it came down.

This page is the same benchmark, re-run with that hole closed - and re-run once more after the
review of 2026-09-05 found that the stand had been embedding a seventh of each session on our
side and the whole session on the competitors'. The figures below are from the second re-run,
whole sessions for everyone, at the reviewed engine.

## What changed

`research/corpus_pin.py` holds the hash. The corpus still is not committed - it is 15 MB of
third-party data for the oracle variant and 278 MB for the standard one - but its fingerprint is
in source, together with the URL it came from and the licence that lets you fetch it. Every
harness calls `verify()` before it reads a byte and stamps the fingerprint into its result file.

```
python research/corpus_pin.py --fetch longmemeval_oracle   # 15 MB, MIT, from the authors
python research/corpus_pin.py --verify longmemeval_oracle
python research/longmem_eval.py --embed                    # about two minutes on a local GPU
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

## Result

Whole sessions embedded, up to the pool's own cap of 28,000 characters, through the same local
endpoint the competitor arms use:

<!-- claims:longmem-pinned -->
> **Withdrawn 2026-09.** the J1 evidence layer removed and the archive-aware reconcile added in one package (ledger J2b); every temporal, frontier and code-session stand re-measures without spans on the GPU campaign, and the model is bound by name in each artifact
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/longmem_eval.py --save --out=research/results/longmem_oracle.json` is what re-measures this one.
<!-- /claims:longmem-pinned -->

Against the run that embedded only the first 2,000 characters of each session, the semantic arm
rose by four points at R@5 - the answer turn begins past that cap in a third of the evidence
sessions, and now it is in the vector. The fused ranker did not follow it: its dense weight was
tuned on the capped vectors, and re-tuning it is a separate measurement with its own threshold,
not something this page does by hand. The lexical arm is unchanged, as it has to be - it never
saw a cap.

The 2026-08 figures stay withdrawn. They were measured on a file that still cannot be
identified, and reviving them because a later run agrees would be assuming the conclusion. The
numbers above are claims on a named corpus, with the hash in the artifact.

## Head to head, same stand

<!-- claims:head-to-head-pinned -->
> **Withdrawn 2026-09.** the J1 evidence layer removed and the archive-aware reconcile added in one package (ledger J2b); every temporal, frontier and code-session stand re-measures without spans on the GPU campaign, and the model is bound by name in each artifact
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/head_to_head.py --only=nevertwice,mem0,langmem,amem --save --out=research/results/head_to_head_v2.json` is what re-measures this one.
<!-- /claims:head-to-head-pinned -->

The competitor rows are their **store** arms: Mem0 with its LLM extraction off (`infer=False`),
searching with its default hybrid of dense cosine and fastembed BM25; LangGraph's InMemoryStore
search, which is LangMem's storage layer without its memory manager; chromadb cosine over the
same vectors, which is A-MEM's store without its LLM note construction or link evolution. They
isolate the retrieval layer the way our arm does. The products' own pipelines are separate arms
(`mem0_infer`, `langmem_full`, `amem_full`) and get their own table in `docs/BENCHMARKS.md`; each
blocks itself when more than a tenth of its LLM calls fail silently.

Nevertwice has no ingest column because there is nothing to ingest: it reads the pool directly
and has no store to populate, which is the same property that makes it zero-dependency and also
means ingest is not a like-for-like cost comparison. Query time is like for like and is in the
artifact.

Every system ingests the same sessions, is queried with the same 500 questions, and is scored by
the same function against the same human-annotated ground truth. All of them use the same local
embedder on the same text, so this isolates the memory pipeline rather than the embedder.

## The same benchmark outside the oracle setting

The oracle variant pools 940 sessions. The standard variant pools every non-empty haystack
session - the same 500 questions, the same annotated evidence, twenty-one times the haystack.
This is the setting the roadmap meant when it asked for a number "outside the oracle setting",
and it is where a retrieval claim earns its keep.

<!-- claims:longmem-s -->
> **Withdrawn 2026-09.** the J1 evidence layer removed and the archive-aware reconcile added in one package (ledger J2b); every temporal, frontier and code-session stand re-measures without spans on the GPU campaign, and the model is bound by name in each artifact
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/longmem_eval.py --data=s --save --out=research/results/longmem_s.json` is what re-measures this one.
<!-- /claims:longmem-s -->

Everything falls, which is what a twenty-one-fold haystack does, and the shape holds: fusion beats both signals it fuses, by +0.068 at R@5 over semantic alone and +0.006 over lexical. Lexical retrieval beats the bi-encoder on this pool, as it did on the smaller one. This is also the pool where the two changes of 2026-09-06 - stop words and stems on the lexical arm, the dense weight moved to one - cost rather than gained: the fused R@5 is below the raw-token, half-weight run that preceded them. Both gates were written on the oracle pool and LoCoMo, and this pool was outside them; `research/LEXICAL_MORPHOLOGY.md` says so and carries the figure.

**623 of the 19,829 sessions carry no text at all** in the published corpus and are skipped,
which is where the pool size comes from. That is a property of the dataset, stated here so the
number is not mistaken for a loading failure on our side. Three more are so dense that they
exceed the embedder's context even under the character cap; at this commit they have no vector,
and the count in the table says so.

The four-system table on this pool:

<!-- claims:head-to-head-s -->
> **Withdrawn 2026-09.** the J1 evidence layer removed and the archive-aware reconcile added in one package (ledger J2b); every temporal, frontier and code-session stand re-measures without spans on the GPU campaign, and the model is bound by name in each artifact
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/head_to_head.py --data=s --only=nevertwice,mem0,langmem,amem --save --out=research/results/head_to_head_s.json` is what re-measures this one.
<!-- /claims:head-to-head-s -->

### What running it found

Two things, neither of them in the results table.

**The Pareto-safety check was passing for the wrong reason.** The harness ranks a
`semantic+recur` arm to prove that the production recurrence prior changes nothing when every
note has recurrence 1 - the boost is exactly 0.0, so adding it must be inert. The two arms broke
ties differently: `semantic` resolved equal scores by session id, `semantic+recur` left them to
the stable sort's fallback order. On 940 sessions the two orders agreed often enough for recall@k
to match and the check printed "inert by construction". On the large pool they disagreed, and
the check reported a CHANGED ranking for a boost of zero. It had never tested the boost; it had
tested the tie-break, and got away with it because the pool was small. Both arms now use the
same key and the check asserts the two lists are **identical**, which is what "changes nothing"
means.

**A larger pool is a different instrument, not the same one further away.** Everything about this
corpus that made the oracle result look stable - few ties, a small candidate set, a generous
top-10 - stops holding at twenty-one times the size. The oracle numbers are not wrong; they
answer an easier question, and this page prints both so nobody has to guess which one a
headline came from.

## What is still missing

- **This is one dataset, in two sizes.** It is external and not one we built, which is the
  point, but a single benchmark is a single benchmark. BEAM remains the other candidate.
- **Two systems record a blocker rather than a number.** Zep needs Neo4j or FalkorDB, and Cognee
  needs an adapter nobody has written. A blocker is recorded instead of an estimate.
- **Competitor versions move.** The result file records the installed version of every package it
  compared against, so a citation can be pinned to the versions that produced it.
- **The dense weight of the fusion was tuned on capped vectors.** Whether the shipped weight is
  still the right one on whole sessions is an open measurement with a threshold to write first.

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
the memory pipeline* - was false, and false against us: the semantic arm was working from
less text than any competitor's. The stand now sends the whole session (up to the pool's own
cap of 28,000 characters) through the same endpoint, the vector cache carries the cap in its
file name and a stamp inside, and a cache built under another cap is refused rather than
loaded. The re-run is the table above: the semantic arm rose, the fusion did not, and both
facts are published.

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
