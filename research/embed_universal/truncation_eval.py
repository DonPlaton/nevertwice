#!/usr/bin/env python3
"""M4 - what truncation costs, measured at every width that matters.

The Matryoshka question is not "is it better" but "what does the shorter vector lose, and did the
full one survive". So every model is evaluated at 1024, 512 and 256 dimensions, and three
comparisons come out of it:

* **K1** the Matryoshka model's FULL vector against shipped v1's - the guard;
* **K2** each truncated width against the same model's own full vector - what truncatable means;
* **K3** v1 truncated the same way, which is the baseline any Matryoshka claim must beat. A
  transformer's dimensions are not ordered by importance, so naive truncation is expected to
  hurt - but "expected" is not "measured", and this project has been caught by an unbaselined
  mechanism before.

Truncation is prefix-then-renormalise, which is what an index would do: keep the first N
dimensions and divide by the new norm, because cosine over an unnormalised prefix is not cosine.

    python research/embed_universal/truncation_eval.py
    python research/embed_universal/truncation_eval.py --print
    python research/embed_universal/truncation_eval.py --models nevertwice-embed \\
        --out research/embed_universal/heldout/truncation_v1.json

Needs a CUDA GPU. Writes heldout/matryoshka_v1.json (committed).

The Matryoshka checkpoint was deleted by the decision M4 recorded, so the two-model run can no
longer be repeated; K1 and K2 are kept as the record of that experiment. K3 needs only the shipped
model, and `--models nevertwice-embed` measures it alone into its OWN artifact. Every chosen model
is checked on disk before anything loads - a missing one used to surface as a loader traceback
after the first model's encode - and a subset run is refused onto the two-model artifact, which it
would silently cut down to one model and take K1 and K2 with it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from heldout_eval import (  # noqa: E402  the metric and interval code is shared on purpose
    BOOTSTRAP, SEED, _fmt, _retrieval_block, _twin_block, bootstrap, encode,
)

FROZEN = HERE / "heldout" / "external_heldout_v1.json"
ARTIFACT = HERE / "heldout" / "matryoshka_v1.json"

MODELS = [
    ("nevertwice-embed", str(HERE / "models" / "universal_v1_merged")),
    ("nevertwice-embed-matryoshka", str(HERE / "models" / "matryoshka_v1_merged")),
]
SHIPPED, MATRYOSHKA = "nevertwice-embed", "nevertwice-embed-matryoshka"
#: comparison -> the models it reads. A comparison is computed only when all of them were run.
NEEDS = {"K1_full_vs_shipped": (SHIPPED, MATRYOSHKA),
         "K2_truncated_vs_own_full": (MATRYOSHKA,),
         "K3_shipped_truncated_vs_own_full": (SHIPPED,)}
WIDTHS = [1024, 512, 256]
#: float32 is what a naive index stores. The saving is proportional either way, but the bytes
#: quoted have to name a dtype or they are not bytes.
BYTES_PER_DIM = 4


def _truncate(vectors, width: int):
    import torch
    cut = vectors[:, :width]
    return cut / torch.clamp(cut.norm(dim=1, keepdim=True), min=1e-12)


def evaluate(label: str, path: str, data: dict) -> dict:
    import torch

    corpus = [d["text"] for d in data["corpus"]]
    twin = data["axes"]["twin"]
    texts = corpus + [r["a"] for r in twin] + [r["b"] for r in twin]
    for axis in ("retrieval_title", "retrieval_situation"):
        texts += [q["query"] for q in data["axes"][axis]]

    started = time.perf_counter()
    full = encode(path, texts)
    encode_seconds = round(time.perf_counter() - started, 2)

    out: dict = {"label": label, "encode_seconds": encode_seconds, "widths": {}}
    for width in WIDTHS:
        vectors = _truncate(full, width)
        at = 0
        corpus_v = vectors[at:at + len(corpus)]; at += len(corpus)
        a_v = vectors[at:at + len(twin)]; at += len(twin)
        b_v = vectors[at:at + len(twin)]; at += len(twin)
        pair_units = [{"label": r["label"], "score": float(torch.dot(a_v[i], b_v[i]))}
                      for i, r in enumerate(twin)]
        block: dict = {"twin": _twin_block(pair_units),
                       "index_bytes": len(corpus) * width * BYTES_PER_DIM,
                       "bytes_per_vector": width * BYTES_PER_DIM}
        ranks_by_axis = {}
        for axis in ("retrieval_title", "retrieval_situation"):
            queries = data["axes"][axis]
            q_v = vectors[at:at + len(queries)]; at += len(queries)
            order = (q_v @ corpus_v.T).argsort(dim=1, descending=True)
            ranks = []
            for i, q in enumerate(queries):
                row = order[i].tolist()
                ranks.append(row.index(q["gold"]) + 1 if q["gold"] in row[:200] else 0)
            block[axis] = _retrieval_block(ranks)
            ranks_by_axis[axis] = ranks
        block["ranks"] = ranks_by_axis
        out["widths"][str(width)] = block
    return out


def _paired(reference_ranks: list[int], challenger_ranks: list[int]) -> dict:
    hits1 = [(1 if r == 1 else 0, 1 if c == 1 else 0)
             for r, c in zip(reference_ranks, challenger_ranks)]
    hits5 = [(1 if 1 <= r <= 5 else 0, 1 if 1 <= c <= 5 else 0)
             for r, c in zip(reference_ranks, challenger_ranks)]
    return {
        "n": len(hits1),
        "delta_recall@1": bootstrap(hits1, lambda s: sum(c for _, c in s) / len(s)
                                    - sum(r for r, _ in s) / len(s)),
        "delta_recall@5": bootstrap(hits5, lambda s: sum(c for _, c in s) / len(s)
                                    - sum(r for r, _ in s) / len(s)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--print", dest="show", action="store_true")
    parser.add_argument("--out", default=str(ARTIFACT))
    parser.add_argument("--models", default=",".join(label for label, _ in MODELS),
                        help="comma-separated labels to run (default: all); a comparison is "
                             "computed only when every model it reads was run")
    args = parser.parse_args(argv)

    if args.show:
        report(json.loads(Path(args.out).read_text(encoding="utf-8")))
        return 0

    known = dict(MODELS)
    wanted = list(dict.fromkeys(x.strip() for x in args.models.split(",") if x.strip()))
    unknown = [x for x in wanted if x not in known]
    if not wanted or unknown:
        print(f"unknown model label(s): {', '.join(unknown) or '(none given)'}; "
              f"have {', '.join(known)}", file=sys.stderr)
        return 2
    missing = [f"{x} -> {known[x]}" for x in wanted if not Path(known[x]).is_dir()]
    if missing:
        print("model(s) not on disk, nothing loaded and nothing written: " + "; ".join(missing)
              + f"\n  K3 needs only the shipped model: --models {SHIPPED} --out "
                "research/embed_universal/heldout/truncation_v1.json", file=sys.stderr)
        return 2
    if set(wanted) != set(known) and Path(args.out).resolve() == ARTIFACT.resolve():
        print(f"refusing a {len(wanted)}-model run onto {ARTIFACT.name}: it holds all "
              f"{len(known)} models and K1/K2, which this run would erase - name an --out",
              file=sys.stderr)
        return 2

    data = json.loads(FROZEN.read_text(encoding="utf-8"))
    results = {}
    for label in wanted:
        print(f"-- {label}", flush=True)
        results[label] = evaluate(label, known[label], data)

    def pair(ref: str, ref_w: str, chal: str, chal_w: str, axis: str) -> dict:
        return _paired(results[ref]["widths"][ref_w]["ranks"][axis],
                       results[chal]["widths"][chal_w]["ranks"][axis])

    comparisons: dict = {}
    skipped = {name: "not run: " + ", ".join(m for m in needs if m not in results)
               for name, needs in NEEDS.items() if any(m not in results for m in needs)}
    for axis in ("retrieval_title", "retrieval_situation"):
        if "K1_full_vs_shipped" not in skipped:
            comparisons.setdefault("K1_full_vs_shipped", {})[axis] = pair(
                SHIPPED, "1024", MATRYOSHKA, "1024", axis)
        for width in ("512", "256"):
            if "K2_truncated_vs_own_full" not in skipped:
                comparisons.setdefault("K2_truncated_vs_own_full", {}).setdefault(width, {})[axis] = \
                    pair(MATRYOSHKA, "1024", MATRYOSHKA, width, axis)
            if "K3_shipped_truncated_vs_own_full" not in skipped:
                comparisons.setdefault("K3_shipped_truncated_vs_own_full", {}).setdefault(
                    width, {})[axis] = pair(SHIPPED, "1024", SHIPPED, width, axis)

    payload = {
        "generated_by": "research/embed_universal/truncation_eval.py",
        "thresholds": "research/EMBED_M4_THRESHOLD.md",
        "benchmark": "research/embed_universal/heldout/external_heldout_v1.json",
        "benchmark_sha256": json.loads(
            (HERE / "heldout" / "MANIFEST.json").read_text(encoding="utf-8"))["sha256"],
        "bootstrap": {"resamples": BOOTSTRAP, "seed": SEED, "method": "percentile"},
        "widths": WIDTHS, "bytes_per_dim": BYTES_PER_DIM,
        "truncation": "prefix then renormalise - what an index would do",
        "models": {label: {"label": r["label"], "encode_seconds": r["encode_seconds"],
                           "widths": {w: {k: v for k, v in b.items() if k != "ranks"}
                                      for w, b in r["widths"].items()}}
                   for label, r in results.items()},
        "comparisons": comparisons,
        **({"comparisons_skipped": skipped} if skipped else {}),
        "ranks": {label: {w: b["ranks"] for w, b in r["widths"].items()}
                  for label, r in results.items()},
    }
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # research/, for _provenance
    import _provenance as prov  # noqa: PLC0415 - (б) b-c: measured_at on every register artifact
    prov.stamp(payload)
    Path(args.out).write_bytes(
        (json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    report(payload)
    return 0


def report(payload: dict) -> None:
    print(f"\nbenchmark {payload['benchmark_sha256'][:12]}...  truncation: {payload['truncation']}")
    for label, block in payload["models"].items():
        print(f"\n-- {label}  (encode {block['encode_seconds']}s)")
        for width in ("1024", "512", "256"):
            b = block["widths"][width]
            print(f"   {width:>4}d  index {b['index_bytes'] // 1024:5d} KiB   "
                  f"situation r@5 {_fmt(b['retrieval_situation']['recall@5'])}   "
                  f"title r@1 {_fmt(b['retrieval_title']['recall@1'])}   "
                  f"twin auc {_fmt(b['twin']['auc'])}")
    for key, why in payload.get("comparisons_skipped", {}).items():
        print(f"\n-- {key}: skipped ({why})")
    if "K1_full_vs_shipped" in payload["comparisons"]:
        print("\n-- K1: the Matryoshka full vector against shipped v1 (the guard)")
        for axis, b in payload["comparisons"]["K1_full_vs_shipped"].items():
            print(f"   {axis:20s} d(r@1) {_fmt(b['delta_recall@1'])}   "
                  f"d(r@5) {_fmt(b['delta_recall@5'])}")
    for key, title in (("K2_truncated_vs_own_full", "K2: truncated against its own full vector"),
                       ("K3_shipped_truncated_vs_own_full", "K3: v1 truncated naively (baseline)")):
        if key not in payload["comparisons"]:
            continue
        print(f"\n-- {title}")
        for width, axes in payload["comparisons"][key].items():
            for axis, b in axes.items():
                print(f"   {width:>4}d {axis:20s} d(r@1) {_fmt(b['delta_recall@1'])}   "
                      f"d(r@5) {_fmt(b['delta_recall@5'])}")


if __name__ == "__main__":
    raise SystemExit(main())
