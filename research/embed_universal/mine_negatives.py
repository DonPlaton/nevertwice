#!/usr/bin/env python3
"""M2, step 1 - mine hard negatives and filter the false ones out with the cross-encoder.

v1 trained on (anchor, positive) with `MultipleNegativesRankingLoss`, whose negatives are
whatever else landed in the batch: a random other lesson, usually from another domain, usually
easy. This replaces them with negatives the CURRENT model already ranks highly and which are
nonetheless wrong - the single largest known lever for a retrieval embedder.

The lever has a matching hazard. A top-ranked "negative" is very often a **false** one: a
genuinely relevant document that simply has no label. Training against it teaches the model to
push away exactly what it should retrieve. So every mined candidate is scored by
`bge-reranker-v2-m3` - the cross-encoder M1 established as the ceiling on this data - and
anything it likes as much as the true positive is dropped rather than trained against.

    python research/embed_universal/mine_negatives.py

Writes data/train_triplets.jsonl (gitignored, like the rest of data/) and
heldout/mining_stats.json, which IS committed: how many candidates were rejected and at what
score is the evidence that the filter did anything.

Needs a CUDA GPU, the shipped model and the reranker. Standard library plus torch.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAIRS = HERE / "data" / "train_pairs.jsonl"
OUT = HERE / "data" / "train_triplets.jsonl"
STATS = HERE / "heldout" / "mining_stats.json"

CURRENT = str(HERE / "models" / "universal_v1_merged")
RERANKER = "BAAI/bge-reranker-v2-m3"

#: How many neighbours to consider per anchor before filtering.
DEPTH = 12
#: How many survivors to keep. More than one negative per anchor multiplies the training signal
#: without multiplying the corpus.
KEEP = 2
#: A candidate the cross-encoder gives at least this probability of being relevant is treated as
#: a false negative and dropped.
#:
#: ABSOLUTE, on a probability scale. The first version of this constant was a relative margin -
#: "within 1.0 of the true positive" - justified by the idea that a cross-encoder's scale is not
#: comparable across queries. That is wrong for this model: bge-reranker-v2-m3 emits a sigmoid
#: probability, so 1.0 spans the entire range and the filter rejected 17556 of 17556 candidates,
#: leaving nothing to train on. The run that discovered it also measured the scale: true
#: positives sit at a median of 0.9997, mined candidates at 0.024, three quarters below 0.17.
#: Half is the reranker's own decision boundary, and using its boundary rather than a number of
#: ours is the point of filtering with it at all.
RELEVANT_PROBABILITY = 0.5
BATCH = 64


def _load_pairs() -> list[dict]:
    return [json.loads(line) for line in PAIRS.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def main(argv: list[str] | None = None) -> int:
    import torch
    from sentence_transformers import CrossEncoder, SentenceTransformer

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--depth", type=int, default=DEPTH)
    parser.add_argument("--keep", type=int, default=KEEP)
    parser.add_argument("--relevant", type=float, default=RELEVANT_PROBABILITY,
                        help="drop a candidate the reranker scores at or above this")
    args = parser.parse_args(argv)

    pairs = _load_pairs()
    anchors = [p["anchor"] for p in pairs]
    positives = [p["positive"] for p in pairs]
    print(f"pairs: {len(pairs)}", flush=True)

    started = time.perf_counter()
    model = SentenceTransformer(CURRENT, device="cuda")
    model.half()
    with torch.inference_mode():
        a_v = model.encode(anchors, batch_size=BATCH, convert_to_tensor=True,
                           normalize_embeddings=True, show_progress_bar=False)
        p_v = model.encode(positives, batch_size=BATCH, convert_to_tensor=True,
                           normalize_embeddings=True, show_progress_bar=False)
    # Candidates come from the POSITIVE pool: every other pair's positive is a real document that
    # is not this anchor's answer, which is exactly the population a retriever confuses.
    sims = (a_v @ p_v.T).float()
    sims.fill_diagonal_(-1e4)          # never mine an anchor's own positive as its negative
    order = sims.argsort(dim=1, descending=True)[:, :args.depth].cpu().tolist()
    del model, a_v, p_v, sims
    torch.cuda.empty_cache()
    print(f"mined {args.depth} candidates each in {time.perf_counter() - started:.1f}s", flush=True)

    started = time.perf_counter()
    cross = CrossEncoder(RERANKER, device="cuda", max_length=512)
    with torch.inference_mode():
        true_scores = cross.predict(list(zip(anchors, positives)), batch_size=BATCH,
                                    show_progress_bar=False)
        flat_pairs, index = [], []
        for i, candidates in enumerate(order):
            for j in candidates:
                flat_pairs.append((anchors[i], positives[j]))
                index.append((i, j))
        cand_scores = cross.predict(flat_pairs, batch_size=BATCH, show_progress_bar=False)
    del cross
    torch.cuda.empty_cache()
    print(f"cross-encoder scored {len(flat_pairs)} candidates in "
          f"{time.perf_counter() - started:.1f}s", flush=True)

    by_anchor: dict[int, list] = {}
    for (i, j), score in zip(index, cand_scores):
        by_anchor.setdefault(i, []).append((float(score), j))

    triplets, rejected, kept_scores, rejected_scores = [], 0, [], []
    for i, pair in enumerate(pairs):
        true_score = float(true_scores[i])
        survivors = []
        for score, j in sorted(by_anchor.get(i, []), key=lambda t: -t[0]):
            if score >= args.relevant:
                rejected += 1
                rejected_scores.append(round(score, 4))
                continue                       # too plausible to be a labelled negative
            survivors.append((score, j))
            if len(survivors) >= args.keep:
                break
        for score, j in survivors:
            kept_scores.append(round(score, 4))
            triplets.append({"anchor": pair["anchor"], "positive": pair["positive"],
                             "negative": positives[j], "cross_score": round(score, 4),
                             "true_score": round(true_score, 4)})

    OUT.write_bytes(("\n".join(json.dumps(t, ensure_ascii=False) for t in triplets) + "\n")
                    .encode("utf-8"))

    def _quantiles(values: list[float]) -> dict:
        if not values:
            return {}
        s = sorted(values)
        return {"min": s[0], "p25": s[len(s) // 4], "median": s[len(s) // 2],
                "p75": s[3 * len(s) // 4], "max": s[-1]}

    stats = {
        "generated_by": "research/embed_universal/mine_negatives.py",
        "miner": "nevertwice-embed v1 (the shipped model)",
        "filter": RERANKER,
        "depth": args.depth, "keep": args.keep,
        "relevant_probability": args.relevant,
        "filter_rule": ("a mined candidate is dropped when the cross-encoder gives it at least "
                        f"{args.relevant} probability of being relevant - its own decision "
                        "boundary, not a number chosen here"),
        "pairs": len(pairs),
        "candidates_scored": len(flat_pairs),
        "rejected_as_false_negatives": rejected,
        "rejection_rate": round(rejected / len(flat_pairs), 4) if flat_pairs else 0.0,
        "triplets": len(triplets),
        "anchors_with_no_survivor": len(pairs) - len({t["anchor"] for t in triplets}),
        "kept_score_quantiles": _quantiles(kept_scores),
        "rejected_score_quantiles": _quantiles(rejected_scores),
        "true_score_quantiles": _quantiles([round(float(s), 4) for s in true_scores]),
    }
    STATS.write_bytes((json.dumps(stats, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    print(json.dumps({k: v for k, v in stats.items() if k != "generated_by"},
                     indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
