# The served model — the threshold, declared before it was queried

**Written 2026-08-27, before a single embedding was fetched from Ollama.** Task M6 of
`.loop/GOAL-NEXT.md`. Hard rule §2.6. Registered `exempt`. Measurements land in
`research/EMBED_SERVING.md`.

---

## What M6 actually has to verify, after M4

M6 was written as "quantise, package for Ollama, verify recall does not regress". Two of those
three are already done and the third has never been checked:

- **Quantise** — `nevertwice-embed-f16.gguf` exists, 1.1 GB, and Ollama serves it as
  `nevertwice-embed:latest`.
- **Package** — the `Modelfile` exists and the model is loaded.
- **Verify** — **nobody has ever compared the served artifact to the measured one.**

Every number in M1–M5 was produced from `models/universal_v1_merged`, a safetensors checkpoint
loaded through sentence-transformers. Every *user* gets the f16 GGUF through Ollama's inference
stack. Those are two different files run by two different engines, and the project has a review
note recording exactly how this goes wrong: a checkpoint that loads mean-pooled when the model is
CLS-trained produces embeddings "the model was never trained or evaluated with", and nothing says
so.

**So M6's real content is: does the thing users run agree with the thing that was measured?**
M4 already established that no new weights are needed, so there is nothing to re-quantise.

## The declarations

### V1 — the served model is the measured model

> On the frozen external set, `nevertwice-embed` served by Ollama must land within **±0.02** of
> `models/universal_v1_merged` on `retrieval_situation` recall@5 and on `retrieval_title`
> recall@1, paired.

Not "no regression" — **agreement**, in both directions. A serving path that is 0.02 *better* is
as much a discrepancy as one that is worse, because it means the two are not the same model and
every published number is about the wrong one. f16 versus f32 should cost close to nothing; a
larger gap means something structural differs.

### V2 — the discrepancy, if any, is localised rather than averaged

> Cosine similarity between the two paths' vectors for the same text: **median ≥ 0.99**, and the
> count below 0.95 reported.

A metric can agree while the vectors differ, if the errors happen to cancel in the ranking. This
asks the sharper question directly. It is a *diagnosis* threshold: failing it while V1 passes
means the agreement is luck.

### V3 — reported, not gating

Per-language served results; whether stock `bge-m3` shows the same f16 gap when served the same
way, which separates "quantisation costs this much" from "our packaging is wrong"; and encode
throughput on both paths, since the served path is what latency claims are about.

## The deletion decision

There is no model to delete here — the artifact already ships. So the decision is about the
**claims**, which is stricter:

- **V1 fails** → every number in M1–M5 is republished with a stated caveat that it describes the
  safetensors checkpoint and not what users run, and fixing the packaging becomes the next task.
  That is a large correction and it is promised in advance so it cannot be quietly avoided.
- **V1 passes, V2 fails** → V1's agreement is reported as coincidental and the vector-level gap is
  published, because a caller doing anything other than top-k retrieval would see it.
- **Both pass** → the M1–M5 numbers describe what users run, and the model card may say so.

## Hardware and courtesy

Ollama is the owner's live tooling and other sessions are using it. This task **reads** from it —
the same requests the owner's own memory hook makes — and starts nothing, stops nothing, and
pulls nothing. If `nevertwice-embed` is not loaded, the task reports that and stops rather than
pulling a 1.1 GB model into someone else's working set.
