# Morphology on the lexical signal: stop words and stems, measured before they shipped

The lexical half of the ranker scored raw tokens for three months. `running` and `run` were
different words, `ошибка` and `ошибки` were different words, and `the` carried the same weight
as a term that occurs once. Mem0's local search does not work that way - fastembed's BM25 stems
and drops stop words - and on LoCoMo's global pool that difference was the whole of its lead.
So the change was measured against a threshold written before the run, on four stands, and
shipped on by default (`NEVERTWICE_LEXICAL_MORPHOLOGY`, `docs/CONFIG.md`).

What ships is pure Python with no dependency: Porter's 1980 stemmer for English, the algorithm
SQLite's own `porter` tokenizer implements, so the in-process BM25 and the FTS5 index agree on
every token; Snowball for Russian, with `ё` folded to `е` so both spellings meet at one stem.
The port is checked against reference implementations in a hermetic test with the expected
values frozen in it: NLTK's original-algorithm mode for English, `py_rust_stemmers` for
Russian - the library behind the competitor's BM25. Neither is imported here.

## The gate, written first

Threshold, declared in the working ledger before anything ran: on LoCoMo, lexical R@5 up by at
least two hundredths; on LongMemEval-oracle, no loss beyond one hundredth on lexical R@5. Added
for the owner's store before its run: no loss beyond one hundredth on lexical R@5 on either
language half of the session protocol below. A miss on any of those keeps the change out of the default.

## LoCoMo, per conversation

Dialogue turns the length of a real note, each question retrieving its human-annotated evidence
turn from its own conversation. Same cached vectors for both arms; only the tokenizer differs.

<!-- claims:lexical-morphology-locomo -->
> **Withdrawn 2026-09.** withdrawn 2026-09-06: the dense weight of the calibrated fusion moved from a half to one after the sweep on whole-session vectors and the stemmed lexical arm; the re-measurement needs the GPU (cross-encoder, embedder) and lands in the next commit
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/locomo_eval.py --no-morphology --save --out=research/results/locomo_raw.json` is what re-measures this one.
<!-- /claims:lexical-morphology-locomo -->

## LongMemEval-oracle, global pool

Whole sessions, most of them longer than ten thousand characters, embedded whole. A long
document already contains most inflections of its own words, so morphology has less to add
and a stem can cost the exact-form match at rank one.

<!-- claims:lexical-morphology-oracle -->
> **Withdrawn 2026-09.** withdrawn 2026-09-06: the dense weight of the calibrated fusion moved from a half to one after the sweep on whole-session vectors and the stemmed lexical arm; the re-measurement needs the GPU (cross-encoder, embedder) and lands in the next commit
>
> The claim is kept in `research/evidence_manifest.json` marked `stale`, with the command that would restore it. `python tools/check_freshness.py --list-stale` prints every withdrawn number and why; `python research/longmem_eval.py --no-morphology --save --out=research/results/longmem_oracle_raw.json` is what re-measures this one.
<!-- /claims:lexical-morphology-oracle -->

## The owner's store, by language half

An external corpus is English chat. A Nevertwice store is bilingual notes of about a thousand
characters, written by the extractor, and nobody else's benchmark looks like it. So
`research/lexical_morphology_probe.py` runs the same two arms over a populated vault, read-only,
lexical only, with the engine's own tokenizer switched off and on - the shipped code path, not
a copy of it. The protocol that decides: the summary of a Session note is the query, the typed
notes extracted from that session are the relevant set, and the pool is the project's typed
notes. That is the direction production runs in - a situation, then the lessons about it.

<!-- claims:lexical-morphology-vault -->
> **Not measured yet.** No `morphology.vault.session.*` claim is registered; `NEVERTWICE_VAULT=<store> python research/lexical_morphology_probe.py --protocol both --out research/results/lexical_morphology_vault.json` is the run that produces them.
<!-- /claims:lexical-morphology-vault -->

The store is private and the numbers are published from the committed artifact
(`research/results/lexical_morphology_vault.json`, rates and counts only); anyone with a
populated store can print their own with one command.

## The protocol that must not decide

The first protocol tried on the store was the one the internal harness has always used: a typed
note's prose as the query, its wikilinked typed neighbours as the relevant set. Every
morphological move lost on it, on both halves - and so did a thirty-word list of articles and
prepositions with no stemming at all. A stop list cannot hurt term matching unless the function
words were the signal, and here they were: notes written from one session share the session's
phrasing, and that fingerprint is what wikilinked siblings have in common. No user prompt shares
it. The result is on the record (`--protocol sibling`) as a property of that protocol, and the
protocol is not a retrieval gate.

## What it costs

A stem is a dictionary hit after the first time a token is seen (the stemmers are memoised);
the hot-path latency figures in `docs/BENCHMARKS.md` are measured with morphology on. The SQLite
index stamps the tokenisation it was built with (`lex_format`) and is rebuilt once, from the
full-precision cache, when the switch changes - never searched with stems against raw text.

## What it does not show

- **Fusion weight.** The dense weight was tuned on raw-token lexical scores; retuning it on
  the shipped tokenizer is a separate measurement with its own threshold, not done here.
- **One embedder, one machine**, as everywhere else in this repository.
- **The sibling protocol's loss is real on its own terms.** A workflow that asks for a note's
  siblings by pasting the note would get exact-form ranking from `NEVERTWICE_LEXICAL_MORPHOLOGY=0`.

## Reproducing

```bash
python research/locomo_eval.py --save --out=research/results/locomo.json
python research/locomo_eval.py --no-morphology --save --out=research/results/locomo_raw.json
python research/longmem_eval.py --save --out=research/results/longmem_oracle.json
python research/longmem_eval.py --no-morphology --save --out=research/results/longmem_oracle_raw.json
NEVERTWICE_VAULT=/path/to/store python research/lexical_morphology_probe.py --protocol both \
    --out research/results/lexical_morphology_vault.json
python tests/_test_stemmer.py            # the reference vectors
python tests/_test_lexical_morphology.py # the wiring
```
