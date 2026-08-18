#!/usr/bin/env python3
"""Universal v1, step 5: THE comparison - stock bge-m3 vs vault-r1 vs universal v1,
identical data, every axis.

Axes:
  REAL vault (pure transfer for the universal model - zero of its pairs were trained on):
    - frozen 659-pair twin test (private, machine-local: polygon path)
    - retrieval guard: 300 queries x 4.3k docs, title + desc-head query shapes
  SYNTHETIC held-out domains (never trained by universal; foreign to r1 too):
    - twin test (label 1 twins vs label 0 distinct same-domain)
    - retrieval guard: title -> lesson over held-out lessons

Prints one table; writes results.json. The vault-r1 row is skipped gracefully when the
polygon artifact is absent (other machines)."""
import json
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

HERE = Path(__file__).parent
POLY = Path(r"D:\Coding\_nevertwice_polygon\embed_specialize")
BATCH = 64


def jl(p):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def auc(pos, neg):
    import bisect
    neg_s = sorted(neg)
    return sum(bisect.bisect_left(neg_s, s)
               + (bisect.bisect_right(neg_s, s) - bisect.bisect_left(neg_s, s)) * 0.5
               for s in pos) / (len(pos) * len(neg))


def twin_metrics(cos, labels):
    pos = [c for c, y in zip(cos, labels) if y == 1]
    neg = [c for c, y in zip(cos, labels) if y == 0]
    thr1 = float(np.quantile(neg, 0.99))
    return {"auc": round(float(auc(pos, neg)), 4),
            "recall_1fpr": round(float(sum(1 for c in pos if c >= thr1) / len(pos)), 3),
            "recall_0fp": round(float(sum(1 for c in pos if c >= max(neg) + 0.01)
                                      / len(pos)), 3),
            # every operand cast: max() over numpy elements yields np.float32, which
            # json.dumps refuses - the first full run died on exactly this at the finish
            "margin": round(float(np.median(pos)) - float(max(neg)), 3)}


def retrieval(model, queries, targets, docs_vec, stems):
    stem_ix = {s: i for i, s in enumerate(stems)}
    qv = model.encode(queries, batch_size=BATCH, convert_to_numpy=True,
                      normalize_embeddings=True, show_progress_bar=False)
    sims = qv @ docs_vec.T
    r1 = r5 = n = 0
    for i, t in enumerate(targets):
        if t not in stem_ix:
            continue
        order = np.argsort(-sims[i])[:5]
        top = [stems[j] for j in order]
        n += 1
        r1 += top[0] == t
        r5 += t in top
    return round(r1 / n, 3), round(r5 / n, 3)


def evaluate(tag, path):
    print(f"\n=== {tag} ===", flush=True)
    model = SentenceTransformer(str(path), device="cuda")

    def enc(texts):
        return model.encode(texts, batch_size=BATCH, convert_to_numpy=True,
                            normalize_embeddings=True, show_progress_bar=False)

    out = {}
    # real vault axes (machine-local private data)
    rp = POLY / "test_pairs.jsonl"
    if rp.exists():
        pairs = jl(rp)
        a, b = enc([p["a"] for p in pairs]), enc([p["b"] for p in pairs])
        out["real_twin"] = twin_metrics((a * b).sum(axis=1), [p["label"] for p in pairs])
        corpus = jl(POLY / "guard_corpus.jsonl")
        gq = jl(POLY / "guard_queries.jsonl")
        docs = enc([c["text"] for c in corpus])
        stems = [c["stem"] for c in corpus]
        tb = {c["stem"]: c["text"] for c in corpus}
        t1, t5 = retrieval(model, [g["query"] for g in gq],
                           [g["target_stem"] for g in gq], docs, stems)
        hq, ht = [], []
        for g in gq:
            body = tb.get(g["target_stem"], "")
            body = body.split("\n", 1)[1] if "\n" in body else ""
            if len(body) > 40:
                hq.append(body[:120])
                ht.append(g["target_stem"])
        h1, h5 = retrieval(model, hq, ht, docs, stems)
        out["real_guard"] = {"title_r1": t1, "title_r5": t5,
                             "hard_r1": h1, "hard_r5": h5}
    # synthetic held-out axes
    sp = HERE / "data" / "test_synth.jsonl"
    if sp.exists():
        pairs = jl(sp)
        _np_ = sum(1 for p in pairs if p["label"])
        if _np_ < 10 or len(pairs) - _np_ < 10:
            # validate composition BEFORE the GPU work: a one-sided test set used to
            # crash twin_metrics at the END of the three-model encoding run,
            # discarding all results (review 2026-08 D15)
            raise SystemExit(f"degenerate test_synth.jsonl ({_np_} pos / "
                             f"{len(pairs) - _np_} neg) - regenerate pairs first")
        a, b = enc([p["a"] for p in pairs]), enc([p["b"] for p in pairs])
        out["synth_twin"] = twin_metrics((a * b).sum(axis=1), [p["label"] for p in pairs])
        guard = jl(HERE / "data" / "guard_synth.jsonl")
        docs = enc([g["text"] for g in guard])
        stems = [str(g["qid"]) for g in guard]
        s1, s5 = retrieval(model, [g["query"] for g in guard], stems, docs, stems)
        out["synth_guard"] = {"title_r1": s1, "title_r5": s5}
    for k, v in out.items():
        print(f"  {k}: {v}", flush=True)
    del model
    import gc
    import torch
    gc.collect()
    torch.cuda.empty_cache()
    return out


models = [("stock bge-m3", "BAAI/bge-m3")]
if (POLY / "model_tuned").exists():
    models.append(("vault-r1", POLY / "model_tuned"))
if (HERE / "models" / "universal_v1").exists():
    models.append(("universal v1", HERE / "models" / "universal_v1"))
results = {tag: evaluate(tag, path) for tag, path in models}
(HERE / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")

print("\n=== SUMMARY TABLE ===")
rows = [("real twin AUC", "real_twin", "auc"), ("real twin R@1%FPR", "real_twin", "recall_1fpr"),
        ("real guard title R@1", "real_guard", "title_r1"),
        ("real guard hard R@1", "real_guard", "hard_r1"),
        ("synth twin AUC", "synth_twin", "auc"),
        ("synth twin R@1%FPR", "synth_twin", "recall_1fpr"),
        ("synth guard R@1", "synth_guard", "title_r1")]
hdr = f"{'metric':24s}" + "".join(f"{tag:>16s}" for tag, _ in models)
print(hdr)
for label, sec, key in rows:
    line = f"{label:24s}"
    for tag, _ in models:
        v = results.get(tag, {}).get(sec, {}).get(key)
        line += f"{v:>16.3f}" if isinstance(v, (int, float)) else f"{'-':>16s}"
    print(line)
print("EVALUATION COMPLETE")
