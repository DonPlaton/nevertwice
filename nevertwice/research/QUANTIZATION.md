# One bit per dimension: scaling recall without a database

A reproducible study of how far the retrieval index compresses before recall moves, and
why Nevertwice needs no approximate-nearest-neighbour dependency to scale. Every number is
on the LongMemEval-oracle stand (940 sessions, 500 questions, local bge-m3), scored exactly
as `longmem_eval.py`. Reproduce with `python nevertwice/research/rnd_launch.py`.

## The setup

Nevertwice keeps three layers. The markdown notes are the source of truth. The JSON embedding
cache holds float32 vectors and is the rebuild source. The hot retrieval path reads a derived
SQLite index, which already packs vectors as float16 (half the size of float32, cosine loss
negligible). The question for this round: can the index go smaller still, and does the brute
force scan need an ANN structure as a vault grows.

## Binary codes cost almost no recall

Replace each float vector with one sign bit per dimension. A 1024-dim bge-m3 vector becomes
128 bytes plus a 2-byte length header, against 4096 bytes for float32 and 2048 for float16.
That is 16x smaller than the shipped index and 32x smaller than the cache.

The ranker never changes. It cosines the float query against the unpacked code, which is the
asymmetric binary score (a float query against a sign vector). On the stand:

| method | bytes/vec | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---|---|---|---|---|---|
| float32 cosine (reference) | 4096 | 0.550 | 0.722 | 0.802 | 0.858 | 0.657 |
| binary, symmetric | 128 | 0.546 | 0.728 | 0.794 | 0.852 | 0.654 |
| binary, asymmetric (shipped scoring) | 128 | 0.548 | 0.726 | 0.796 | 0.846 | 0.655 |

Four questions out of five hundred move at R@5. For a 32x size cut that is a clean trade, so
it ships as the opt-in `NEVERTWICE_EMBED_QUANT=binary`. The default stays float16 and the cache
stays float32, so the switch only changes the derived index and reverses with one rebuild.

Matryoshka prefix truncation (keep the first d dimensions, renormalise) degrades more
gracefully than expected for a model not trained for it, but it is the weaker lever:

| dimensions kept | bytes (float32) | R@5 |
|---|---|---|
| 1024 (full) | 4096 | 0.802 |
| 512 | 2048 | 0.796 |
| 256 | 1024 | 0.788 |
| 128 | 512 | 0.784 |

Binary at 128 bytes beats matryoshka-256 at 1024 bytes, so binary is the size lever we ship.
The two compose (512-dim binary is 64 bytes), recorded here for completeness.

## Why there is no ANN dependency

The honest motivation for an HNSW or IVF index is query latency once the candidate set is
large. We measured the pure-Python brute force first, because that is the real fallback path:

| notes scanned | float cosine per query |
|---|---|
| 1000 | 0.11 s |
| 10000 | 1.08 s |
| 100000 | 10.7 s (extrapolated, linear) |

Ten seconds at a hundred thousand notes would indeed stall a prompt. Two things already
prevent it. The hot path FTS-prefilters candidates by lexical match before any cosine runs,
so the scan is bounded by the prefilter, not the vault size. And binary codes turn the scan
into an integer XOR plus a population count, which is a different cost class entirely:

| notes scanned | binary hamming (XOR + bit_count) |
|---|---|
| 100000 | 0.0094 s |

That is about 1100x faster than the float scan and roughly eleven milliseconds at a hundred
thousand notes, in standard-library Python with no index structure at all. A C++ ANN library
(usearch, hnswlib) would add a binary dependency, a build step, and a second copy of the
vectors, to beat a number that is already imperceptible. For a personal agent memory, where a
hundred thousand distilled notes is years of heavy use, the trade is not worth breaking the
no-database, no-dependency design over.

Verdict: no ANN backend. The scale answer is the FTS prefilter that already ships, plus the
binary code path measured above. If a future workload genuinely needs sublinear search over
millions of vectors, an ANN index belongs behind the same opt-in plugin boundary the SQLite
index already uses, never in the default path.

## What this changes

`NEVERTWICE_EMBED_QUANT=binary` is a new opt-in for users with very large vaults who want a
smaller index. It is off by default, covered by `_test_quant.py` (round-trip, the asymmetric
ranking property, and a full build-and-search), and documented in `docs/CONFIG.md`. The
published recall numbers for Nevertwice are unchanged, because the default ranker is untouched.

*Run it:* `python nevertwice/research/rnd_launch.py` (battery) and
`NEVERTWICE_EMBED_QUANT=binary python nevertwice/_test_quant.py` (regression).
