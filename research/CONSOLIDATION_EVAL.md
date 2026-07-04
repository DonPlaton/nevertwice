# Phase 2 — Does real LLM consolidation help retrieval? (closing the 4A gap) — results

*Companion to `research/consolidation_eval.py`. Reproduce: `NEVERTWICE_VAULT=/path python
research/consolidation_eval.py --save` (calls Ollama for synthesis + bge-m3 embedding; ~18 min for
109 leave-one-out syntheses on the real store). Aggregate-only: reads the local cache + synthesises
text in-process, prints cosines / recall / cost — never note or principle text.*

## Why this exists — the 4A gap

4A (`ABSTRACTIVE.md`) showed on **synthetic latent vectors** that the unit-mean of K episodic
instances of a lesson recovers the latent rule better than any single instance, and named the
load-bearing caveat: that "principle" is the **vector mean** of the cluster — an *idealisation*. A
production consolidation does something different: an **LLM summarises the cluster's text** into a
principle and embeds *that*. Before shipping consolidation (Phase 3), the real operator had to be
measured on real data. This is that measurement, and it is the decision input the ship rests on.

## Method

Leave-one-out on the live store's real cross-session clusters (cosine ≥ 0.55, K ≥ 3, spanning >1
date — the 26 clusters / 109 notes 4A's tie-in counted). Each member is held out as a **simulated new
occurrence**; a principle is synthesised (local LLM, qwen2.5:7b) from the *other* K−1 members' text
and embedded. Then two questions, both against the real store:

1. **Mechanism** — cosine of the held-out occurrence to: the best single episode (status quo), the
   vector mean of the K−1 episodes (the 4A idealisation), and the LLM principle (the real operator).
2. **Downstream** — full-store recall@3 for the right topic when the K−1 episodes are *replaced* by
   the 1 principle, vs the episodic store (production status quo), at 109→26 compression.

## Result — the idealisation does NOT transfer; consolidation-by-replacement craters retrieval

108 leave-out queries, 0 synthesis errors:

| cosine of the held-out new occurrence to … | value |
|---|---|
| best single **episode** (status quo) | **0.642** |
| **vector mean** of episodes (4A idealisation) | 0.684 |
| LLM-**synthesised principle** (the real operator) | **0.542** |

- The real principle (0.542) is **worse than the best episode** (0.642) and **far short of the
  vector-mean idealisation** (0.684). It beats the best episode in only **9%** of queries and the
  vector mean in **2%**. The 4A vector-mean result **does not transfer** to real text synthesis.
- **Downstream full-store recall@3: episodic 0.824 → consolidated 0.352 (−0.472).** Replacing
  episodes with the principle is severely retrieval-harmful.

## Why — and why a bigger model would not save it

A new occurrence of a lesson is *specific* ("CUDA OOM at batch=64 on this GPU"); the synthesised
principle is *general* ("size batches to VRAM"). The general statement embeds into a more abstract
region than the specific occurrence, so a specific re-occurrence matches a stored **specific episode**
strongly and the **abstracted principle** weakly. Abstraction discards exactly the surface detail that
same-topic retrieval keys on.

This is **structural, not a model-quality artifact**: the **vector mean is the model-independent best
possible aggregation**, and even it clears the best episode by only **+0.042 cosine** — so *no*
synthesiser, however strong, turns consolidation-by-replacement into a same-topic retrieval win. A
better model would lift 0.542 toward 0.684; it cannot lift it past the episodes for this regime. (We
report the conservative measured operator and the model-independent ceiling together, rather than
chasing a larger synthesiser that the ceiling already bounds.)

## Honest scope

This tests the **common** case — retrieving a *similar* re-occurrence — where specific episodes are the
right answer by construction. It does **not** test 4A's other regime (a *novel, distant* application of
the rule, where an abstraction could in principle help); the cluster structure of a real single-user
store does not supply distant-application queries. So the claim is bounded: **for same-topic recall,
which is what an agent memory does on virtually every real query, raw episodes beat synthesised
principles, and replacing episodes with principles is strongly harmful.**

## Decision → Phase 3: do NOT ship abstractive consolidation

Shipping was authorised *contingent on verification* ("add it, but verify everything yourself"). The
verification says **don't**: consolidation-by-replacement cuts recall@3 nearly in half on real data,
and there is no measured regime on this store where the synthesised principle helps. Shipping it —
even archive-and-link, since archiving episodes is what removes them from the index that the −0.472
measures — would add a feature that **measurably degrades the system's core function**: code for a
non-benefit, i.e. the bloat the project explicitly rejects. The episodic bi-encoder store is already
the right design; the consolidation research stands as a **clean negative result**
(the abstraction/specificity tradeoff, measured: the synthetic vector-mean win is an idealisation that
real text synthesis does not realise). If a future deployment is storage-bound or dominated by
novel-application queries, this harness is the gate to re-open the question — with episodes kept
indexed, not archived.
