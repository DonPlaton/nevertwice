#!/usr/bin/env python3
"""M1 - the honest baseline table on the external held-out set.

Three models, one frozen benchmark, every number with a bootstrap interval and every query's
result published. The point is not to make `nevertwice-embed` look good; it is to establish what
is true on evidence a stranger can check, because the shipped headline was measured on a private
vault and cannot be.

    stock              BAAI/bge-m3, untouched
    nevertwice-embed   the shipped LoRA fine-tune, merged
    reranker           BAAI/bge-reranker-v2-m3, a CROSS-encoder

The reranker is a **ceiling, not a competitor**. It reads the query and the document together,
which costs a forward pass per candidate and cannot be indexed, so it is not shippable as a
retriever. Its score is the answer to "how much is still on the table", and a bi-encoder that
matched it would be remarkable.

Three axes and why each is here:

* **twin** - is this the same lesson twice? Reported as AUC and as recall at 1% and 0% false
  positives, because the store's dedup gate runs at a threshold, not at an average.
* **retrieval_title** - the easy control. The query is a verbatim prefix of the document, so a
  model that cannot win here is broken rather than merely weak.
* **retrieval_situation** - the shape the product faces: a forward-looking sentence that must
  find a note it does not quote.

Intervals are percentile bootstrap over the sampling unit that actually varies - pairs for the
twin axis, queries for retrieval - 2000 resamples, seed fixed. Not a normal approximation:
recall at a threshold is bounded and skewed, and a symmetric interval on it would overstate
precision near the ends.

    python research/embed_universal/heldout_eval.py            # run it, write results
    python research/embed_universal/heldout_eval.py --print    # summarise what was committed

Needs a CUDA GPU and the three models. Not runnable from a bare clone; classified accordingly.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FROZEN = HERE / "heldout" / "external_heldout_v1.json"
ARTIFACT = HERE / "heldout" / "baseline_v1.json"

#: (label, kind, path-or-id). The merged fine-tune is local; the other two come from the HF cache.
#: `nevertwice-embed-distil` was listed here by da1132e - the same commit that deleted its
#: checkpoint after M3 failed both gates - so from then on this default run could not finish: it
#: died loading the fourth model, after encoding three, and wrote nothing. Its table is
#: `heldout/distil_v1.json`, kept as the record of a rejected experiment; reproducing it means
#: regenerating the checkpoint (distil.py) and running this stand at da1132e.
MODELS = [
    ("stock bge-m3", "bi", "BAAI/bge-m3"),
    ("nevertwice-embed", "bi", str(HERE / "models" / "universal_v1_merged")),
    ("bge-reranker-v2-m3", "cross", "BAAI/bge-reranker-v2-m3"),
]


def _missing_local_models(models: list) -> list[str]:
    """Every model given as a local path that is not on disk - checked before anything loads.

    A Hugging Face id is not a path and is left to the loader: whether it is cached is the HF
    cache's business, and a miss there fails at the first model rather than after the third."""
    return [f"{label} -> {path}" for label, _kind, path in models
            if Path(path).is_absolute() and not Path(path).is_dir()]

BOOTSTRAP = 2000
SEED = 20260827
#: How deep the cross-encoder reranks. It cannot score 408 documents per query at any sane cost,
#: so it reranks the stock retriever's top-K - the standard cascade, and its recall is therefore
#: capped by that retriever's recall@K, which is reported alongside so the cap is visible.
RERANK_DEPTH = 50
BATCH = 64


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def auc(pos: list[float], neg: list[float]) -> float:
    """Mann-Whitney U / (n_pos * n_neg). Ties count a half, as they should."""
    if not pos or not neg:
        return float("nan")
    ordered = sorted([(s, 1) for s in pos] + [(s, 0) for s in neg])
    rank_sum, i = 0.0, 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][0] == ordered[i][0]:
            j += 1
        rank = (i + j) / 2.0 + 1.0
        rank_sum += rank * sum(1 for k in range(i, j + 1) if ordered[k][1] == 1)
        i = j + 1
    return (rank_sum - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def recall_at_fpr(pos: list[float], neg: list[float], fpr: float) -> float:
    """Fraction of positives above the threshold that admits at most *fpr* of negatives."""
    if not pos or not neg:
        return float("nan")
    allowed = int(math.floor(fpr * len(neg)))
    ordered = sorted(neg, reverse=True)
    threshold = ordered[allowed] if allowed < len(ordered) else -float("inf")
    return sum(1 for s in pos if s > threshold) / len(pos)


def retrieval_metrics(ranks: list[int]) -> dict:
    """*ranks* is the 1-based rank of the gold document, or 0 when it was never retrieved."""
    n = len(ranks) or 1
    return {
        "recall@1": sum(1 for r in ranks if r == 1) / n,
        "recall@5": sum(1 for r in ranks if 1 <= r <= 5) / n,
        "mrr@10": sum(1.0 / r for r in ranks if 1 <= r <= 10) / n,
    }


class _Rng:
    """A tiny deterministic PRNG, so the intervals do not depend on numpy's version."""

    def __init__(self, seed: int) -> None:
        self.state = seed & 0xFFFFFFFFFFFFFFFF

    def next_index(self, n: int) -> int:
        # splitmix64
        self.state = (self.state + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        return ((z ^ (z >> 31)) % n)


def bootstrap(units: list, statistic, resamples: int = BOOTSTRAP, seed: int = SEED) -> dict:
    """Percentile bootstrap over *units*. Returns the point estimate and a 95% interval."""
    point = statistic(units)
    if not units or (isinstance(point, float) and math.isnan(point)):
        return {"value": point, "low": None, "high": None, "resamples": 0}
    rng = _Rng(seed)
    n = len(units)
    draws = []
    for _ in range(resamples):
        sample = [units[rng.next_index(n)] for _ in range(n)]
        value = statistic(sample)
        if not (isinstance(value, float) and math.isnan(value)):
            draws.append(value)
    draws.sort()
    if not draws:
        return {"value": point, "low": None, "high": None, "resamples": 0}
    lo = draws[max(0, int(0.025 * len(draws)) - 1)]
    hi = draws[min(len(draws) - 1, int(0.975 * len(draws)))]
    return {"value": round(point, 4), "low": round(lo, 4), "high": round(hi, 4),
            "resamples": len(draws)}


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def _free_gpu() -> None:
    import gc

    import torch
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def encode(model_path: str, texts: list[str]):
    from sentence_transformers import SentenceTransformer
    import torch

    model = SentenceTransformer(model_path, device="cuda")
    model.half()          # 5090 has the memory to spare; fp16 is the deployed precision anyway
    with torch.inference_mode():
        vectors = model.encode(texts, batch_size=BATCH, convert_to_tensor=True,
                               normalize_embeddings=True, show_progress_bar=False)
    out = vectors.float().cpu()
    del model, vectors
    _free_gpu()
    return out


def cross_score(model_path: str, pairs: list[tuple[str, str]]) -> list[float]:
    from sentence_transformers import CrossEncoder
    import torch

    model = CrossEncoder(model_path, device="cuda", max_length=512)
    with torch.inference_mode():
        scores = model.predict(pairs, batch_size=BATCH, show_progress_bar=False)
    out = [float(s) for s in scores]
    del model
    _free_gpu()
    return out


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def evaluate_bi(label: str, path: str, data: dict) -> dict:
    import torch

    corpus = [d["text"] for d in data["corpus"]]
    twin = data["axes"]["twin"]
    started = time.perf_counter()

    texts = corpus + [r["a"] for r in twin] + [r["b"] for r in twin]
    for axis in ("retrieval_title", "retrieval_situation"):
        texts += [q["query"] for q in data["axes"][axis]]
    vectors = encode(path, texts)

    at = 0
    corpus_v = vectors[at:at + len(corpus)]; at += len(corpus)
    a_v = vectors[at:at + len(twin)]; at += len(twin)
    b_v = vectors[at:at + len(twin)]; at += len(twin)

    pair_units = [{"label": r["label"], "score": float(torch.dot(a_v[i], b_v[i]))}
                  for i, r in enumerate(twin)]

    axes: dict[str, dict] = {}
    per_query: dict[str, list] = {}
    for axis in ("retrieval_title", "retrieval_situation"):
        queries = data["axes"][axis]
        q_v = vectors[at:at + len(queries)]; at += len(queries)
        sims = q_v @ corpus_v.T
        order = sims.argsort(dim=1, descending=True)
        ranks, rows = [], []
        for i, q in enumerate(queries):
            row = order[i].tolist()
            rank = row.index(q["gold"]) + 1 if q["gold"] in row[:200] else 0
            ranks.append(rank)
            rows.append({"qid": q["qid"], "gold": q["gold"], "rank": rank,
                         "domain": q["domain"], "lang": q["lang"],
                         "top1": row[0]})
        axes[axis] = _retrieval_block(ranks)
        per_query[axis] = rows
        axes[axis]["recall@%d" % RERANK_DEPTH] = sum(
            1 for r in ranks if 1 <= r <= RERANK_DEPTH) / (len(ranks) or 1)

    return {
        "label": label, "kind": "bi", "seconds": round(time.perf_counter() - started, 2),
        "twin": _twin_block(pair_units),
        **axes,
        "per_query": per_query,
        "per_pair": [{"pair_id": twin[i]["pair_id"], "label": u["label"],
                      "score": round(u["score"], 6), "domain": twin[i]["domain"]}
                     for i, u in enumerate(pair_units)],
    }


def _twin_block(units: list[dict]) -> dict:
    def _auc(sample):
        return auc([u["score"] for u in sample if u["label"] == 1],
                   [u["score"] for u in sample if u["label"] == 0])

    def _at(fpr):
        return lambda sample: recall_at_fpr([u["score"] for u in sample if u["label"] == 1],
                                            [u["score"] for u in sample if u["label"] == 0], fpr)

    return {"n": len(units), "auc": bootstrap(units, _auc),
            "recall@1%fpr": bootstrap(units, _at(0.01)),
            "recall@0fp": bootstrap(units, _at(0.0))}


def _retrieval_block(ranks: list[int]) -> dict:
    return {
        "n": len(ranks),
        "recall@1": bootstrap(ranks, lambda s: retrieval_metrics(s)["recall@1"]),
        "recall@5": bootstrap(ranks, lambda s: retrieval_metrics(s)["recall@5"]),
        "mrr@10": bootstrap(ranks, lambda s: retrieval_metrics(s)["mrr@10"]),
    }


def evaluate_cross(label: str, path: str, data: dict, first_stage: dict) -> dict:
    """The ceiling: score twin pairs directly, and rerank the stock retriever's top-K."""
    corpus = [d["text"] for d in data["corpus"]]
    twin = data["axes"]["twin"]
    started = time.perf_counter()

    scores = cross_score(path, [(r["a"], r["b"]) for r in twin])
    pair_units = [{"label": r["label"], "score": s} for r, s in zip(twin, scores)]

    axes, per_query = {}, {}
    for axis in ("retrieval_title", "retrieval_situation"):
        queries = data["axes"][axis]
        stage1 = {row["qid"]: row for row in first_stage["per_query"][axis]}
        pairs, index = [], []
        for q in queries:
            candidates = stage1[q["qid"]]["candidates"]
            for doc_id in candidates:
                pairs.append((q["query"], corpus[doc_id]))
                index.append((q["qid"], doc_id))
        flat = cross_score(path, pairs) if pairs else []
        grouped: dict[int, list] = {}
        for (qid, doc_id), score in zip(index, flat):
            grouped.setdefault(qid, []).append((score, doc_id))
        ranks, rows = [], []
        for q in queries:
            ordered = [d for _, d in sorted(grouped.get(q["qid"], []), key=lambda t: -t[0])]
            rank = ordered.index(q["gold"]) + 1 if q["gold"] in ordered else 0
            ranks.append(rank)
            rows.append({"qid": q["qid"], "gold": q["gold"], "rank": rank,
                         "domain": q["domain"], "lang": q["lang"],
                         "top1": ordered[0] if ordered else -1})
        axes[axis] = _retrieval_block(ranks)
        per_query[axis] = rows

    return {"label": label, "kind": "cross", "seconds": round(time.perf_counter() - started, 2),
            "reranks": RERANK_DEPTH, "twin": _twin_block(pair_units), **axes,
            "per_query": per_query,
            "per_pair": [{"pair_id": twin[i]["pair_id"], "label": u["label"],
                          "score": round(u["score"], 6), "domain": twin[i]["domain"]}
                         for i, u in enumerate(pair_units)]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--print", dest="show", action="store_true")
    parser.add_argument("--out", default=str(ARTIFACT))
    args = parser.parse_args(argv)

    if args.show:
        report(json.loads(Path(args.out).read_text(encoding="utf-8")))
        return 0

    missing = _missing_local_models(MODELS)
    if missing:
        print("model(s) not on disk, nothing loaded and nothing written: " + "; ".join(missing),
              file=sys.stderr)
        return 2

    data = json.loads(FROZEN.read_text(encoding="utf-8"))
    results = {}

    # The bi-encoders first: the cross-encoder reranks the STOCK retriever's shortlist, so the
    # cascade is a property of the pair, not of the reranker alone, and it is named as such.
    for label, kind, path in MODELS:
        if kind != "bi":
            continue
        print(f"-- {label}")
        results[label] = evaluate_bi(label, path, data)

    stock = results["stock bge-m3"]
    for axis in ("retrieval_title", "retrieval_situation"):
        # Recompute the shortlist the reranker sees, from the stock model's own ranking.
        for row in stock["per_query"][axis]:
            row["candidates"] = []
    shortlist = _shortlists(data, stock)

    for label, kind, path in MODELS:
        if kind != "cross":
            continue
        print(f"-- {label} (reranking stock top-{RERANK_DEPTH})")
        results[label] = evaluate_cross(label, path, data, shortlist)

    payload = {
        "generated_by": "research/embed_universal/heldout_eval.py",
        "benchmark": "research/embed_universal/heldout/external_heldout_v1.json",
        "benchmark_sha256": json.loads(
            (HERE / "heldout" / "MANIFEST.json").read_text(encoding="utf-8"))["sha256"],
        "bootstrap": {"resamples": BOOTSTRAP, "seed": SEED, "method": "percentile"},
        "rerank_depth": RERANK_DEPTH,
        "device": _device_note(),
        "models": {label: {k: v for k, v in r.items() if k != "per_query"}
                   for label, r in results.items()},
        "paired_vs_stock": paired(results),
        # M2's threshold is declared against the SHIPPED model, not against stock: M1 already
        # showed v1's advantage over stock does not resolve, so beating stock again proves
        # nothing. Both references are published; the gate reads this one.
        "paired_vs_shipped": paired(results, reference="nevertwice-embed"),
        "per_query": {label: r["per_query"] for label, r in results.items()},
    }
    Path(args.out).write_bytes(
        (json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    report(payload)
    return 0


def _shortlists(data: dict, stock: dict) -> dict:
    """Top-K candidate ids per query, from the stock retriever, for the cross-encoder to rerank."""
    import torch
    corpus = [d["text"] for d in data["corpus"]]
    vectors = encode(MODELS[0][2], corpus + [q["query"] for axis in
                                             ("retrieval_title", "retrieval_situation")
                                             for q in data["axes"][axis]])
    corpus_v = vectors[:len(corpus)]
    at = len(corpus)
    out: dict[str, dict] = {"per_query": {}}
    for axis in ("retrieval_title", "retrieval_situation"):
        queries = data["axes"][axis]
        q_v = vectors[at:at + len(queries)]; at += len(queries)
        order = (q_v @ corpus_v.T).argsort(dim=1, descending=True)[:, :RERANK_DEPTH]
        out["per_query"][axis] = [{"qid": q["qid"], "candidates": order[i].tolist()}
                                  for i, q in enumerate(queries)]
    del vectors, corpus_v
    _free_gpu()
    return out


def paired(results: dict, reference: str = "stock bge-m3") -> dict:
    """Per-query differences against *reference*, bootstrapped with the pairing kept.

    Also McNemar's discordant counts at recall@1: b = the reference got it and the challenger
    did not, c = the reverse. Those two numbers are the whole evidence about a paired binary
    outcome, and an exact two-sided binomial p follows from them directly - no chi-square
    approximation, which is unreliable when b + c is small, and here it is small.
    """
    out: dict[str, dict] = {}
    for label, block in results.items():
        if label == reference:
            continue
        axes = {}
        for axis in ("retrieval_title", "retrieval_situation"):
            base = {r["qid"]: r["rank"] for r in results[reference]["per_query"][axis]}
            mine = {r["qid"]: r["rank"] for r in block["per_query"][axis]}
            qids = sorted(set(base) & set(mine))
            hits = [(1 if base[q] == 1 else 0, 1 if mine[q] == 1 else 0) for q in qids]
            top5 = [(1 if 1 <= base[q] <= 5 else 0, 1 if 1 <= mine[q] <= 5 else 0) for q in qids]
            b = sum(1 for r, m in hits if r == 1 and m == 0)
            c = sum(1 for r, m in hits if r == 0 and m == 1)
            axes[axis] = {
                "n": len(qids),
                "delta_recall@1": bootstrap(hits, lambda s: sum(m for _, m in s) / len(s)
                                            - sum(r for r, _ in s) / len(s)),
                "delta_recall@5": bootstrap(top5, lambda s: sum(m for _, m in s) / len(s)
                                            - sum(r for r, _ in s) / len(s)),
                "mcnemar": {"reference_only": b, "challenger_only": c,
                            "discordant": b + c, "p_exact": _binom_two_sided(c, b + c)},
            }
        out[label] = axes
    return out


def _binom_two_sided(k: int, n: int) -> float | None:
    """Exact two-sided binomial p under p=0.5. None when nothing was discordant."""
    if n == 0:
        return None
    total = 2.0 ** n
    def _c(i):
        return math.comb(n, i)
    observed = _c(k)
    tail = sum(_c(i) for i in range(n + 1) if _c(i) <= observed)
    return round(min(1.0, tail / total), 4)


def _device_note() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return f"{torch.cuda.get_device_name(0)}, torch {torch.__version__}, fp16"
    except Exception:  # pragma: no cover
        pass
    return "cpu"


def _fmt(block: dict) -> str:
    if block.get("low") is None:
        return f"{block['value']:.3f}"
    return f"{block['value']:.3f} [{block['low']:.3f}, {block['high']:.3f}]"


def report(payload: dict) -> None:
    print(f"\nbenchmark {payload['benchmark_sha256'][:12]}...  "
          f"bootstrap {payload['bootstrap']['resamples']} x percentile, seed "
          f"{payload['bootstrap']['seed']}\ndevice {payload['device']}\n")
    for axis, keys in (("twin", ("auc", "recall@1%fpr", "recall@0fp")),
                       ("retrieval_title", ("recall@1", "recall@5", "mrr@10")),
                       ("retrieval_situation", ("recall@1", "recall@5", "mrr@10"))):
        print(f"-- {axis}")
        for label, block in payload["models"].items():
            cells = "  ".join(f"{k} {_fmt(block[axis][k])}" for k in keys)
            print(f"   {label:22s} n={block[axis]['n']:4d}  {cells}")
        print()
    print("-- paired against stock bge-m3 (the same queries, so the pairing is kept)")
    for label, axes in payload.get("paired_vs_stock", {}).items():
        for axis, block in axes.items():
            m = block["mcnemar"]
            print(f"   {label:22s} {axis:20s} d(r@1) {_fmt(block['delta_recall@1'])}"
                  f"  d(r@5) {_fmt(block['delta_recall@5'])}"
                  f"  McNemar {m['challenger_only']}/{m['reference_only']}"
                  f" p={m['p_exact']}")


if __name__ == "__main__":
    raise SystemExit(main())
