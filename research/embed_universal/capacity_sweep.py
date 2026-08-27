#!/usr/bin/env python3
"""M5 - train one arm per LoRA rank, evaluate all of them, keep only what clears S1.

Everything except the rank is v1's recipe, so the curve is a capacity curve and not a mixture of
changes. Each arm is trained, merged, evaluated on the frozen external set, and its checkpoint is
deleted immediately unless it clears the promotion rule - four ranks of bge-m3 is 9 GB of disk for
models that, on the evidence of M2, M3 and M4, are unlikely to be kept.

Per-language results are broken out for every arm, because the store this serves is bilingual and
a single average can hide a regression in the smaller half.

    python research/embed_universal/capacity_sweep.py --dry-run
    python research/embed_universal/capacity_sweep.py

Needs a CUDA GPU. Writes heldout/capacity_sweep.json (committed).
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from heldout_eval import (  # noqa: E402  shared metric and interval code
    BOOTSTRAP, SEED as BOOT_SEED, _fmt, _retrieval_block, _twin_block, bootstrap, encode,
)

PAIRS = HERE / "data" / "train_pairs.jsonl"
FROZEN = HERE / "heldout" / "external_heldout_v1.json"
ARTIFACT = HERE / "heldout" / "capacity_sweep.json"
SHIPPED = str(HERE / "models" / "universal_v1_merged")
BASE = "BAAI/bge-m3"

RANKS = [1, 4, 16, 64]
SEED = 11
EPOCHS = 3
BATCH = 24
LR = 1e-4
TARGETS = ["query", "key", "value", "dense"]
REQUIRED_FREE_GB = 20.0


def _vram() -> dict:
    import torch
    free, total = torch.cuda.mem_get_info()
    return {"free_gb": round(free / 2**30, 2), "total_gb": round(total / 2**30, 2)}


def train_arm(rank: int, examples, workdir: Path) -> dict:
    import torch
    from peft import LoraConfig, TaskType
    from sentence_transformers import SentenceTransformer, losses, models as st_models
    from torch.utils.data import DataLoader
    from peft import PeftModel
    from transformers import AutoModel, AutoTokenizer

    random.seed(SEED)
    torch.manual_seed(SEED)
    adapter, merged_dir = workdir / f"r{rank}", workdir / f"r{rank}_merged"

    model = SentenceTransformer(BASE, device="cuda")
    model.add_adapter(LoraConfig(task_type=TaskType.FEATURE_EXTRACTION, r=rank,
                                 lora_alpha=rank * 2, lora_dropout=0.05,
                                 target_modules=TARGETS))
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    loader = DataLoader(list(examples), shuffle=True, batch_size=BATCH, drop_last=True)
    loss = losses.MultipleNegativesRankingLoss(model)
    started = time.perf_counter()
    model.fit(train_objectives=[(loader, loss)], epochs=EPOCHS,
              warmup_steps=max(10, int(len(loader) * EPOCHS * 0.1)),
              optimizer_params={"lr": LR}, use_amp=True, show_progress_bar=False)
    seconds = round(time.perf_counter() - started, 1)
    model.save(str(adapter))
    del model
    torch.cuda.empty_cache()

    base = AutoModel.from_pretrained(BASE, torch_dtype=torch.float32)
    PeftModel.from_pretrained(base, str(adapter)).merge_and_unload().save_pretrained(str(merged_dir))
    AutoTokenizer.from_pretrained(BASE).save_pretrained(str(merged_dir))
    word = st_models.Transformer(str(merged_dir))
    pool = st_models.Pooling(word.get_word_embedding_dimension(), pooling_mode="cls")
    SentenceTransformer(modules=[word, pool, st_models.Normalize()],
                        device="cuda").save(str(merged_dir))
    check = SentenceTransformer(str(merged_dir), device="cuda")
    pooling = [m for m in check.modules() if type(m).__name__ == "Pooling"]
    if getattr(pooling[0], "pooling_mode", "?") != "cls":
        raise SystemExit(f"REFUSING: r{rank} merged checkpoint is not cls-pooled")
    del check
    torch.cuda.empty_cache()
    return {"rank": rank, "trainable_parameters": trainable, "seconds": seconds,
            "path": str(merged_dir)}


def evaluate(path: str, data: dict) -> dict:
    import torch
    corpus = [d["text"] for d in data["corpus"]]
    twin = data["axes"]["twin"]
    texts = corpus + [r["a"] for r in twin] + [r["b"] for r in twin]
    for axis in ("retrieval_title", "retrieval_situation"):
        texts += [q["query"] for q in data["axes"][axis]]
    vectors = encode(path, texts)

    at = 0
    corpus_v = vectors[at:at + len(corpus)]; at += len(corpus)
    a_v = vectors[at:at + len(twin)]; at += len(twin)
    b_v = vectors[at:at + len(twin)]; at += len(twin)
    out = {"twin": _twin_block([{"label": r["label"], "score": float(torch.dot(a_v[i], b_v[i]))}
                                for i, r in enumerate(twin)]),
           "ranks": {}, "by_language": {}}
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
            lang: {"n": len(rs),
                   "recall@1": round(sum(1 for r in rs if r == 1) / len(rs), 4),
                   "recall@5": round(sum(1 for r in rs if 1 <= r <= 5) / len(rs), 4)}
            for lang, rs in sorted(per_lang.items())}
    return out


def _paired(reference: list[int], challenger: list[int]) -> dict:
    h1 = [(1 if r == 1 else 0, 1 if c == 1 else 0) for r, c in zip(reference, challenger)]
    h5 = [(1 if 1 <= r <= 5 else 0, 1 if 1 <= c <= 5 else 0) for r, c in zip(reference, challenger)]
    return {"n": len(h1),
            "delta_recall@1": bootstrap(h1, lambda s: sum(c for _, c in s) / len(s)
                                        - sum(r for r, _ in s) / len(s)),
            "delta_recall@5": bootstrap(h5, lambda s: sum(c for _, c in s) / len(s)
                                        - sum(r for r, _ in s) / len(s))}


def main(argv: list[str] | None = None) -> int:
    from sentence_transformers import InputExample

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default=str(ARTIFACT))
    args = parser.parse_args(argv)

    vram = _vram()
    print(f"VRAM before: {vram}", flush=True)
    if vram["free_gb"] < REQUIRED_FREE_GB:
        print(f"REFUSING: {vram['free_gb']} GB free, {REQUIRED_FREE_GB} required.", flush=True)
        return 2

    data = json.loads(FROZEN.read_text(encoding="utf-8"))
    pairs = [json.loads(line) for line in PAIRS.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    examples = [InputExample(texts=[p["anchor"], p["positive"]]) for p in pairs]
    print(f"pairs {len(pairs)}  ranks {RANKS}", flush=True)
    if args.dry_run:
        print(json.dumps({"ranks": RANKS, "pairs": len(pairs), "vram": vram}, indent=1))
        return 0

    print("-- shipped v1 (the baseline)", flush=True)
    shipped = evaluate(SHIPPED, data)

    workdir = HERE / "models" / "_sweep"
    workdir.mkdir(parents=True, exist_ok=True)
    arms, promoted, arm_ranks = {}, [], {}
    try:
        for rank in RANKS:
            print(f"-- r{rank}", flush=True)
            meta = train_arm(rank, examples, workdir)
            result = evaluate(meta["path"], data)
            paired = {axis: _paired(shipped["ranks"][axis], result["ranks"][axis])
                      for axis in ("retrieval_title", "retrieval_situation")}
            d5 = paired["retrieval_situation"]["delta_recall@5"]
            clears = d5["low"] is not None and d5["low"] > 0
            if clears:
                promoted.append(rank)
            arm_ranks[f"r{rank}"] = result["ranks"]
            arms[f"r{rank}"] = {
                **{k: v for k, v in meta.items() if k != "path"},
                **{k: v for k, v in result.items() if k != "ranks"},
                "paired_vs_shipped": paired,
                "clears_S1": clears,
                "checkpoint": "kept" if clears else "deleted",
            }
            print(f"   S1 {'CLEARS' if clears else 'fails'}: "
                  f"d(r@5) {_fmt(d5)}", flush=True)
            if not clears:
                shutil.rmtree(Path(meta["path"]), ignore_errors=True)
                shutil.rmtree(Path(meta["path"]).with_name(f"r{rank}"), ignore_errors=True)
    finally:
        if not promoted:
            shutil.rmtree(workdir, ignore_errors=True)

    # S2: r1 against r16, the belief M5 was asked to re-test - on an axis that discriminates,
    # with r16 as the reference because the belief is phrased as "r1 won".
    s2 = None
    if "r1" in arm_ranks and "r16" in arm_ranks:
        s2 = {axis: _paired(arm_ranks["r16"][axis], arm_ranks["r1"][axis])
              for axis in ("retrieval_title", "retrieval_situation")}

    payload = {
        "generated_by": "research/embed_universal/capacity_sweep.py",
        "thresholds": "research/EMBED_M5_THRESHOLD.md",
        "benchmark_sha256": json.loads(
            (HERE / "heldout" / "MANIFEST.json").read_text(encoding="utf-8"))["sha256"],
        "bootstrap": {"resamples": BOOTSTRAP, "seed": BOOT_SEED, "method": "percentile"},
        "held_constant": ("everything but the rank: same pairs, MultipleNegativesRankingLoss, "
                          "3 epochs, batch 24, lr 1e-4, seed 11, targets qkv+dense. lora_alpha "
                          "tracks the rank at 2x, which is the convention the recipe already used."),
        "ranks": RANKS,
        "shipped_baseline": {k: v for k, v in shipped.items() if k != "ranks"},
        "arms": arms,
        "promoted": promoted,
        "s2_r1_vs_r16": s2,
        "vram_before": vram,
        "cross_lingual_note": (
            "The other half of M5 - re-testing whether cross-lingual pairs hurt - could not run: "
            "gen_pairs.py produces same-language twins only, so no cross-lingual arm exists in "
            "train_pairs.jsonl. Per-language results are reported instead and are NOT a "
            "substitute for that test."),
    }
    Path(args.out).write_bytes(
        (json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    report(payload)
    return 0



def report(payload: dict) -> None:
    print(f"\nbenchmark {payload['benchmark_sha256'][:12]}...")
    print(f"{'arm':>6}  {'params':>10}  {'s':>5}  situation r@5            d(r@5) vs shipped")
    for name, arm in payload["arms"].items():
        d5 = arm["paired_vs_shipped"]["retrieval_situation"]["delta_recall@5"]
        print(f"{name:>6}  {arm['trainable_parameters']:>10,}  {arm['seconds']:>5.0f}  "
              f"{_fmt(arm['retrieval_situation']['recall@5']):24s} {_fmt(d5)}"
              f"   {'PROMOTED' if arm['clears_S1'] else ''}")
    print(f"\npromoted: {payload['promoted'] or 'none - every arm deleted'}")


if __name__ == "__main__":
    raise SystemExit(main())
