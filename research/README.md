# `research/`: the honest eval lab

**Not part of the product.** Nothing here is imported by the runtime. This directory is the
measurement bench: the experiments that decided what Nevertwice ships, and, just as often,
what it *doesn't*. It exists for credibility and reproducibility, not for you to run.

The one thing that makes Nevertwice different from most "memory for agents" repos:
**we measured the clever ideas on real data and cut the ones that lost.** A memory you
can't trust is worse than no memory. The receipts are here.

## Where a number comes from

[`evidence_manifest.json`](evidence_manifest.json) is the register behind every figure
printed in the README and [`docs/BENCHMARKS.md`](../docs/BENCHMARKS.md): dataset, sample
size, model, hardware, the exact command, the raw result file and the pointer inside it,
the confidence interval where one applies, and the caveat that belongs with the number.
`tests/_test_evidence_manifest.py` fails if a printed number has no entry, if an entry
disagrees with the raw result it points at, or if a claim without a committed artifact
does not say so.

The tables in those documents are **generated** from the manifest by
[`render_claims.py`](../tools/render_claims.py) into marked regions; CI re-renders them and
fails on any difference, so a table cannot drift from the result it reports. The same tool
emits a chart's evidence footer: `python tools/render_claims.py --footer <claim-id>`.

Every tracked Markdown file is registered as **governed** (every number must resolve to a
claim), **exempt** with a stated reason, or **backlog** with a numeric budget that may only
be lowered - so a new document cannot quietly start publishing unevidenced numbers.

Two things the manifest makes visible rather than hides: **45 of 125 claims have no
committed raw artifact** (each says why - `eval_harness.py` saves into the user's vault,
`latency_bench.py` saves nothing, some studies are published only as prose), and **1,352
numbers across 23 study write-ups are still unregistered**, held under a ratchet that lets
that surface shrink but never grow.

## Start here

| Study | File | Verdict |
|---|---|---|
| External retrieval benchmark (LongMemEval-oracle, 940 sessions / 500 questions) | [`longmem_eval.py`](longmem_eval.py) → [`longmem_results.json`](longmem_results.json) | calibrated fusion R@5 **0.80** / R@10 **0.86**; +trained cross-encoder R@1 **0.55→0.61** |
| **End-to-end QA accuracy** (the *answer* axis - read→answer→judge, the metric vendors headline) | [`qa_eval.py`](qa_eval.py) → [`QA_ACCURACY.md`](QA_ACCURACY.md) | standard LongMemEval-oracle **0.788** (deepseek-reasoner); a reader sweep walks it 0.61→0.68→0.75→0.79 with the memory fixed → the gap to memanto's 0.898 is reader strength, not memory; retrieving *more* hurts (−0.06) |
| **★ Improvement-per-token** (no published measurement found, mid-2026) | [`longitudinal_improvement.py`](longitudinal_improvement.py) → [`ACTIVE_MEMORY.md`](ACTIVE_MEMORY.md) | over a 200-task family, **active memory (guards) matches always-inject's error-prevention for 31× fewer tokens** and is a *net* token saving; improvement-per-token **~30× v1**. On this bench always-inject is a net cost. |
| **★ Active Memory** (memory that acts instead of waiting to be read) | [`ACTIVE_MEMORY.md`](ACTIVE_MEMORY.md) · `guards.py` · `anticipate.py` · `causal.py` | Guards: experience→executable check (0 tokens until it fires, Popperian self-retire). Anticipation: one warning by trajectory-resemblance (precision-first, 0 below threshold). Counterfactual: "what breaks if I change X" from an induced 507-node causal graph - **~7× cheaper than dumping the notes**. |
| **★ Live validation** (does it work on a real model, outside the simulator?) | [`live_validation.py`](live_validation.py) → [`LIVE_VALIDATION.md`](LIVE_VALIDATION.md) | on DeepSeek, a fired guard cuts the real pitfall rate **0.36→0.05 (−86%)**; measured `eff`=0.88 (sim assumed a conservative 0.75); help concentrates on project-specific knowledge the model can't know. **Weak-vs-strong twist**: a 3B agent extracts *half* the benefit (eff 0.44 vs 0.79) - memory is necessary but not sufficient, the agent's ability to apply a fact bounds the payoff. |
| **Calibrated score fusion** (why it beat rank fusion and the three measured leaders) | [`RETRIEVAL_FUSION.md`](RETRIEVAL_FUSION.md) | RRF discards score magnitudes (trails plain BM25); calibrated fusion lifts R@5 0.66→**0.80** and tops Mem0 |
| Precision: rerankers & "stronger" embedders | [`W2_PRECISION.md`](W2_PRECISION.md) | promptable LLM reranker & 4 alt embedders **lose** to bge-m3 on top-1; only a *trained* cross-encoder wins → shipped opt-in |
| Abstractive consolidation ("summarise notes into a principle") | [`CONSOLIDATION_EVAL.md`](CONSOLIDATION_EVAL.md) · [`ABSTRACTIVE.md`](ABSTRACTIVE.md) | craters recall@3 0.82→0.35 → **not shipped** |
| Token economy (does memory actually save tokens?) | [`token_ab.py`](token_ab.py) | net-negative vs a small curated context, hugely positive vs full history: honest, not a headline |
| Head-to-head vs market leaders (same stand, local Ollama) | [`head_to_head.py`](head_to_head.py) → [`head_to_head.json`](head_to_head.json) | controlled recall@k vs Mem0 et al. See [`docs/COMPARISON.md`](../docs/COMPARISON.md) |
| Memory-poisoning guard (injection / exfiltration / destruction) | [`POISONING.md`](POISONING.md) | 0 false-positives on a 328-note vault |

## The rest

Mechanism studies (each a write-up + a runnable `.py` + a `.json`/`.png` result):
recurrence/salience ablation ([`ABLATION_RESULTS.md`](ABLATION_RESULTS.md)),
bandit online ranker ([`BANDIT.md`](BANDIT.md)),
bi-temporal point-in-time queries ([`bitemporal_ablation.py`](bitemporal_ablation.py)),
submodular forgetting ([`FORGETTING.md`](FORGETTING.md)),
longitudinal recall ([`LONGITUDINAL_BENCH.md`](LONGITUDINAL_BENCH.md)),
rare-event salience ([`RARE_EVENT.md`](RARE_EVENT.md)),
calibrated-posterior salience ([`POSTERIOR_MODEL.md`](POSTERIOR_MODEL.md)),
real-trace replay ([`REAL_TRACE.md`](REAL_TRACE.md)),
divergent retrieval ([`DIVERGENT.md`](DIVERGENT.md)),
biological-memory analogues ([`BIO_MEMORY.md`](BIO_MEMORY.md)).

## Reproduce

```bash
# 1. fetch the dataset (see data/README.md for the source + filename)
# 2. embed once, then score (fast, re-rankable from cache):
python research/longmem_eval.py --embed
python research/longmem_eval.py --save            # writes longmem_results.json
python research/longmem_eval.py --xrerank --save  # + the trained cross-encoder

# token economy and the head-to-head (local Ollama, no paid key):
python research/token_ab.py
python research/head_to_head.py --only=mem0 --save
```

Each `_test_*.py` here is a self-checking unit test (stdlib, mocked, no network, no GPU);
11 of them run in CI alongside the 8 core suites.

The full method (a calibrated-posterior salience stack, an online-learning ranker,
submodular forgetting, and a recurrence-bearing benchmark) is being written up for
Zenodo/arXiv.
