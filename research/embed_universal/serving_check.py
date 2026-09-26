#!/usr/bin/env python3
"""M6 - does the model users actually run agree with the model that was measured?

Every number in M1-M5 came from `models/universal_v1_merged`, a safetensors checkpoint loaded
through sentence-transformers. Every user gets `nevertwice-embed-f16.gguf` through Ollama. Two
files, two engines, and nobody had ever compared them - while this directory carries a review
note about a checkpoint that silently loaded MEAN-pooled and would have shipped embeddings the
model was never trained with.

Two questions, because a metric can agree while the vectors do not:

* **V1** do the retrieval numbers match, within 0.02 and in BOTH directions? A serving path that
  is better is as much a discrepancy as one that is worse.
* **V2** do the vectors themselves match - median cosine at least 0.99 between the two paths for
  the same text?

Reads from Ollama only. It starts nothing, stops nothing and pulls nothing: if the model is not
already loaded it says so and stops, rather than pulling 1.1 GB into somebody else's working set.

    python research/embed_universal/serving_check.py

Writes heldout/serving_check.json (committed).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from heldout_eval import _fmt, _retrieval_block, _twin_block, bootstrap, encode  # noqa: E402

FROZEN = HERE / "heldout" / "external_heldout_v1.json"
ARTIFACT = HERE / "heldout" / "serving_check.json"
LOCAL = str(HERE / "models" / "universal_v1_merged")

OLLAMA = "http://127.0.0.1:11434"
SERVED = "nevertwice-embed"
STOCK_SERVED = "bge-m3"
BATCH = 32
V1_TOLERANCE = 0.02
V2_MEDIAN_COSINE = 0.99


def _post(path: str, payload: dict, timeout: int = 300) -> dict:
    request = urllib.request.Request(
        OLLAMA + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def loaded_models() -> list[str]:
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=30) as response:
            return [m["name"] for m in json.loads(response.read().decode("utf-8"))["models"]]
    except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError):
        return []


def serve_encode(model: str, texts: list[str]):
    """Embeddings from Ollama, normalised the way the local path normalises them."""
    import torch
    vectors = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i:i + BATCH]
        reply = _post("/api/embed", {"model": model, "input": chunk})
        vectors.extend(reply["embeddings"])
    out = torch.tensor(vectors, dtype=torch.float32)
    return out / torch.clamp(out.norm(dim=1, keepdim=True), min=1e-12)


def metrics(vectors, data: dict) -> dict:
    import torch
    corpus = [d["text"] for d in data["corpus"]]
    twin = data["axes"]["twin"]
    at = 0
    corpus_v = vectors[at:at + len(corpus)]; at += len(corpus)
    a_v = vectors[at:at + len(twin)]; at += len(twin)
    b_v = vectors[at:at + len(twin)]; at += len(twin)
    out = {"twin": _twin_block([{"label": r["label"], "score": float(torch.dot(a_v[i], b_v[i]))}
                                for i, r in enumerate(twin)]), "ranks": {}, "by_language": {}}
    for axis in ("retrieval_title", "retrieval_situation"):
        queries = data["axes"][axis]
        q_v = vectors[at:at + len(queries)]; at += len(queries)
        order = (q_v @ corpus_v.T).argsort(dim=1, descending=True)
        ranks = []
        for i, q in enumerate(queries):
            row = order[i].tolist()
            ranks.append(row.index(q["gold"]) + 1 if q["gold"] in row[:200] else 0)
        out[axis] = _retrieval_block(ranks)
        out["ranks"][axis] = ranks
        per_lang: dict[str, list[int]] = {}
        for q, rank in zip(queries, ranks):
            per_lang.setdefault(q["lang"] or "?", []).append(rank)
        out["by_language"][axis] = {
            lang: {"n": len(rs), "recall@5": round(sum(1 for r in rs if 1 <= r <= 5) / len(rs), 4)}
            for lang, rs in sorted(per_lang.items())}
    return out


def paired(reference: list[int], challenger: list[int]) -> dict:
    h1 = [(1 if r == 1 else 0, 1 if c == 1 else 0) for r, c in zip(reference, challenger)]
    h5 = [(1 if 1 <= r <= 5 else 0, 1 if 1 <= c <= 5 else 0) for r, c in zip(reference, challenger)]
    return {"n": len(h1),
            "delta_recall@1": bootstrap(h1, lambda s: sum(c for _, c in s) / len(s)
                                        - sum(r for r, _ in s) / len(s)),
            "delta_recall@5": bootstrap(h5, lambda s: sum(c for _, c in s) / len(s)
                                        - sum(r for r, _ in s) / len(s))}


def _texts(data: dict) -> list[str]:
    texts = [d["text"] for d in data["corpus"]]
    texts += [r["a"] for r in data["axes"]["twin"]] + [r["b"] for r in data["axes"]["twin"]]
    for axis in ("retrieval_title", "retrieval_situation"):
        texts += [q["query"] for q in data["axes"][axis]]
    return texts


def main(argv: list[str] | None = None) -> int:
    import torch

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ARTIFACT))
    args = parser.parse_args(argv)

    available = loaded_models()
    if not available:
        print("Ollama is not answering. Nothing was started; nothing to report.")
        return 3
    served = next((m for m in available if m.split(":")[0] == SERVED), None)
    if served is None:
        print(f"{SERVED!r} is not loaded in Ollama. Refusing to pull 1.1 GB into somebody "
              f"else's working set - load it and re-run.")
        return 3
    stock = next((m for m in available if m.split(":")[0] == STOCK_SERVED), None)

    data = json.loads(FROZEN.read_text(encoding="utf-8"))
    texts = _texts(data)
    print(f"{len(texts)} texts; served model {served!r}", flush=True)

    started = time.perf_counter()
    local_v = encode(LOCAL, texts)
    local_seconds = round(time.perf_counter() - started, 2)
    started = time.perf_counter()
    served_v = serve_encode(served, texts)
    served_seconds = round(time.perf_counter() - started, 2)

    cosines = (local_v * served_v).sum(dim=1)
    ordered = cosines.sort().values
    vector_agreement = {
        "n": len(ordered),
        "min": round(float(ordered[0]), 4),
        "p05": round(float(ordered[len(ordered) // 20]), 4),
        "median": round(float(ordered[len(ordered) // 2]), 4),
        "mean": round(float(cosines.mean()), 4),
        "below_0.95": int((cosines < 0.95).sum()),
        "below_0.99": int((cosines < 0.99).sum()),
    }

    local_m = metrics(local_v, data)
    served_m = metrics(served_v, data)
    comparison = {axis: paired(local_m["ranks"][axis], served_m["ranks"][axis])
                  for axis in ("retrieval_title", "retrieval_situation")}

    v1_pass = (abs(comparison["retrieval_situation"]["delta_recall@5"]["value"]) <= V1_TOLERANCE
               and abs(comparison["retrieval_title"]["delta_recall@1"]["value"]) <= V1_TOLERANCE)
    v2_pass = vector_agreement["median"] >= V2_MEDIAN_COSINE

    stock_gap = None
    if stock:
        print(f"-- stock {stock!r}, to separate quantisation from packaging", flush=True)
        stock_local = encode("BAAI/bge-m3", texts)
        stock_served = serve_encode(stock, texts)
        stock_cos = (stock_local * stock_served).sum(dim=1).sort().values
        stock_gap = {"model": stock,
                     "median_cosine": round(float(stock_cos[len(stock_cos) // 2]), 4),
                     "min_cosine": round(float(stock_cos[0]), 4)}
        del stock_local, stock_served
        torch.cuda.empty_cache()

    payload = {
        "generated_by": "research/embed_universal/serving_check.py",
        "thresholds": "research/EMBED_M6_THRESHOLD.md",
        "benchmark_sha256": json.loads(
            (HERE / "heldout" / "MANIFEST.json").read_text(encoding="utf-8"))["sha256"],
        "served_model": served, "local_checkpoint": "models/universal_v1_merged",
        "v1_tolerance": V1_TOLERANCE, "v2_median_cosine": V2_MEDIAN_COSINE,
        "V1_pass": v1_pass, "V2_pass": v2_pass,
        "vector_agreement": vector_agreement,
        "comparison_served_vs_local": comparison,
        "local": {k: v for k, v in local_m.items() if k != "ranks"},
        "served": {k: v for k, v in served_m.items() if k != "ranks"},
        "seconds": {"local": local_seconds, "served": served_seconds, "texts": len(texts)},
        "stock_serving_gap": stock_gap,
        "courtesy": ("read-only against the owner's running Ollama - the same requests their own "
                     "memory hook makes. Nothing started, stopped or pulled."),
    }
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # research/, for _provenance
    import _provenance as prov  # noqa: PLC0415 - (б) b-c: measured_at on every register artifact
    prov.stamp(payload)
    Path(args.out).write_bytes(
        (json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    report(payload)
    return 0


def report(payload: dict) -> None:
    va = payload["vector_agreement"]
    print(f"\nV1 {'PASS' if payload['V1_pass'] else 'FAIL'}   "
          f"V2 {'PASS' if payload['V2_pass'] else 'FAIL'}")
    print(f"vectors: median cosine {va['median']}  min {va['min']}  "
          f"below 0.99: {va['below_0.99']}/{va['n']}  below 0.95: {va['below_0.95']}")
    for axis, block in payload["comparison_served_vs_local"].items():
        print(f"  {axis:20s} d(r@1) {_fmt(block['delta_recall@1'])}   "
              f"d(r@5) {_fmt(block['delta_recall@5'])}")
    s = payload["seconds"]
    print(f"encode {s['texts']} texts: local {s['local']}s, served {s['served']}s")
    if payload.get("stock_serving_gap"):
        print(f"stock control: {payload['stock_serving_gap']}")


if __name__ == "__main__":
    raise SystemExit(main())
