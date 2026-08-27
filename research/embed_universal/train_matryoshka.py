#!/usr/bin/env python3
"""M4 - train the vector so its first 512 and first 256 dimensions stand on their own.

Not an accuracy bet. M2 and M3 both tried to make the model better and both failed; this trades
index size for a bounded amount of accuracy, and the threshold document gates on the FULL vector
not regressing rather than on it improving.

`MatryoshkaLoss` wraps the ordinary loss and applies it at each width, so the model is optimised
to be correct when truncated as well as when whole. The inner loss is v1's
`MultipleNegativesRankingLoss` over the same (anchor, positive) pairs v1 trained on - not M2's
mined triplets, which failed their own gate - so the only difference from v1 is the Matryoshka
wrapper. One variable again.

    python research/embed_universal/train_matryoshka.py --dry-run
    python research/embed_universal/train_matryoshka.py

Needs a CUDA GPU. Writes models/matryoshka_v1{,_merged}/ (gitignored) and
heldout/matryoshka_v1_training.json (committed).
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAIRS = HERE / "data" / "train_pairs.jsonl"
OUT = HERE / "models" / "matryoshka_v1"
MERGED = HERE / "models" / "matryoshka_v1_merged"
RECORD = HERE / "heldout" / "matryoshka_v1_training.json"

BASE = "BAAI/bge-m3"
SEED = 11
EPOCHS = 3
BATCH = 24
LR = 1e-4
LORA = {"r": 16, "lora_alpha": 32, "lora_dropout": 0.05,
        "target_modules": ["query", "key", "value", "dense"]}

#: The widths the vector must work at. 1024 is bge-m3's native size; the two below it are the
#: ones the index would actually use, and each halving quarters or halves the store.
WIDTHS = [1024, 512, 256]
#: Weight per width. The full vector carries the most because K1 gates on it not regressing;
#: the shorter ones are the point of the exercise and are not far behind.
WEIGHTS = [1.0, 1.0, 1.0]
REQUIRED_FREE_GB = 20.0


def _vram() -> dict:
    import torch
    free, total = torch.cuda.mem_get_info()
    return {"free_gb": round(free / 2**30, 2), "total_gb": round(total / 2**30, 2)}


def _merge(src: Path, dst: Path) -> None:
    """The routine train_hard.py and distil.py use, for the reason recorded there: a bare HF
    checkpoint silently loads MEAN-pooled while bge-m3 is a CLS model."""
    import torch
    from peft import PeftModel
    from sentence_transformers import SentenceTransformer, models as st_models
    from transformers import AutoModel, AutoTokenizer

    base = AutoModel.from_pretrained(BASE, torch_dtype=torch.float32)
    merged = PeftModel.from_pretrained(base, str(src)).merge_and_unload()
    merged.save_pretrained(str(dst))
    AutoTokenizer.from_pretrained(BASE).save_pretrained(str(dst))
    word = st_models.Transformer(str(dst))
    pool = st_models.Pooling(word.get_word_embedding_dimension(), pooling_mode="cls")
    SentenceTransformer(modules=[word, pool, st_models.Normalize()], device="cuda").save(str(dst))
    check = SentenceTransformer(str(dst), device="cuda")
    pooling = [m for m in check.modules() if type(m).__name__ == "Pooling"]
    mode = getattr(pooling[0], "pooling_mode", "?") if pooling else "?"
    if mode != "cls":
        raise SystemExit(f"REFUSING: the merged checkpoint loads {mode}-pooled, not cls")
    print(f"merged -> {dst}  (verified {mode}-pooled through the published load path)", flush=True)


def main(argv: list[str] | None = None) -> int:
    import torch
    from peft import LoraConfig, TaskType
    from sentence_transformers import InputExample, SentenceTransformer, losses
    from torch.utils.data import DataLoader

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    vram = _vram()
    print(f"VRAM before: {vram}", flush=True)
    if vram["free_gb"] < REQUIRED_FREE_GB:
        print(f"REFUSING: {vram['free_gb']} GB free, {REQUIRED_FREE_GB} required - the margin "
              "protects a concurrently-running Ollama from a mid-run collision.", flush=True)
        return 2

    pairs = [json.loads(line) for line in PAIRS.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    print(f"pairs: {len(pairs)}  widths: {WIDTHS}", flush=True)
    if args.dry_run:
        print(json.dumps({"widths": WIDTHS, "weights": WEIGHTS, "pairs": len(pairs),
                          "vram": vram}, indent=1))
        return 0

    random.seed(SEED)
    torch.manual_seed(SEED)
    examples = [InputExample(texts=[p["anchor"], p["positive"]]) for p in pairs]
    random.shuffle(examples)

    model = SentenceTransformer(BASE, device="cuda")
    model.add_adapter(LoraConfig(task_type=TaskType.FEATURE_EXTRACTION, **LORA))
    loader = DataLoader(examples, shuffle=True, batch_size=BATCH, drop_last=True)
    inner = losses.MultipleNegativesRankingLoss(model)
    loss = losses.MatryoshkaLoss(model, inner, matryoshka_dims=WIDTHS,
                                 matryoshka_weights=WEIGHTS)
    started = time.perf_counter()
    model.fit(train_objectives=[(loader, loss)], epochs=EPOCHS,
              warmup_steps=max(10, int(len(loader) * EPOCHS * 0.1)),
              optimizer_params={"lr": LR}, use_amp=True, show_progress_bar=False)
    seconds = time.perf_counter() - started
    peak = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    model.save(str(OUT))
    print(f"saved -> {OUT}  ({seconds:.0f}s, peak {peak} GB)", flush=True)
    del model
    torch.cuda.empty_cache()
    _merge(OUT, MERGED)

    RECORD.write_bytes((json.dumps({
        "generated_by": "research/embed_universal/train_matryoshka.py",
        "base": BASE, "seed": SEED, "epochs": EPOCHS, "batch": BATCH, "lr": LR, "lora": LORA,
        "loss": "MatryoshkaLoss(MultipleNegativesRankingLoss)",
        "widths": WIDTHS, "weights": WEIGHTS, "pairs": len(pairs),
        "changed_from_v1": ("the Matryoshka wrapper only. Same pairs, recipe, seed, rank, "
                            "epochs and learning rate as v1, and NOT M2's mined triplets, which "
                            "failed their own gate."),
        "seconds": round(seconds, 1), "peak_allocated_gb": peak,
        "vram_before": vram, "vram_margin_required_gb": REQUIRED_FREE_GB,
        "ollama": ("left running deliberately, as in M2 and M3: the owner was working in other "
                   "projects whose memory hooks use it. The measured margin is what makes that "
                   "safe."),
    }, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
