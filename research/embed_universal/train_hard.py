#!/usr/bin/env python3
"""M2, step 2 - retrain on mined triplets, changing one thing and nothing else.

Everything about the recipe is v1's, because the question is what MINING buys and a second
change would make the answer unattributable: LoRA r=16 on qkv+dense, 3 epochs, lr 1e-4,
`MultipleNegativesRankingLoss`, bf16, seed 11. The single difference is the training example:
v1 fed (anchor, positive) and let the batch supply negatives; this feeds
(anchor, positive, hard_negative) from `mine_negatives.py`.

The VRAM decision the threshold document committed to is made here, before the run, and written
into the checkpoint's metadata: free memory is measured, a margin is required, and the run
refuses rather than colliding with whatever else is using the GPU.

    python research/embed_universal/train_hard.py
    python research/embed_universal/train_hard.py --dry-run   # check the plan and the VRAM only

Needs a CUDA GPU. Writes models/hard_v1/ and models/hard_v1_merged/, both gitignored.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRIPLETS = HERE / "data" / "train_triplets.jsonl"
OUT = HERE / "models" / "hard_v1"
MERGED = HERE / "models" / "hard_v1_merged"
RECORD = HERE / "heldout" / "training_hard_v1.json"

BASE = "BAAI/bge-m3"
SEED = 11
EPOCHS = 3
BATCH = 24
LR = 1e-4
LORA = {"r": 16, "lora_alpha": 32, "lora_dropout": 0.05,
        "target_modules": ["query", "key", "value", "dense"]}

#: Gigabytes of GPU memory that must remain free after this run's estimated footprint.
#: The vault records a training run colliding with Ollama on this machine; the margin exists so
#: a model loading into Ollama mid-run cannot cause it. Refusing is the correct outcome when it
#: is not there - the alternative is killing tooling the owner is actively using.
REQUIRED_FREE_GB = 20.0


def _vram() -> dict:
    import torch
    free, total = torch.cuda.mem_get_info()
    return {"free_gb": round(free / 2**30, 2), "total_gb": round(total / 2**30, 2),
            "used_gb": round((total - free) / 2**30, 2)}


def _merge(src: Path, dst: Path) -> None:
    """Fold the adapter into the base and save a real sentence-transformers checkpoint.

    Deliberately the same shape as merge_and_verify.py, whose review note explains the trap: a
    bare HF checkpoint has no modules.json or 1_Pooling, so SentenceTransformer silently loads it
    MEAN-pooled while bge-m3 is a CLS model - the embeddings would be from a pipeline the weights
    were never trained for, and nothing would say so. The verification at the end loads the
    destination exactly the way the evaluator will.
    """
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
    SentenceTransformer(modules=[word, pool, st_models.Normalize()],
                        device="cuda").save(str(dst))

    check = SentenceTransformer(str(dst), device="cuda")
    pooling = [m for m in check.modules() if type(m).__name__ == "Pooling"]
    #  is the attribute this version exposes; get_pooling_mode_str() was
    # removed, and asking for a method that no longer exists would have failed the check for a
    # reason unrelated to pooling.
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
    parser.add_argument("--batch", type=int, default=BATCH)
    parser.add_argument("--epochs", type=int, default=EPOCHS)
    args = parser.parse_args(argv)

    vram = _vram()
    print(f"VRAM before: {vram}", flush=True)
    if vram["free_gb"] < REQUIRED_FREE_GB:
        print(f"REFUSING: {vram['free_gb']} GB free, {REQUIRED_FREE_GB} GB required.\n"
              "The margin protects a concurrently-running Ollama from a mid-run collision - the "
              "mistake this vault already records. Free the GPU, or run this when the machine is "
              "idle; do not lower the margin to make it fit.", flush=True)
        return 2

    triplets = [json.loads(line) for line in TRIPLETS.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    print(f"triplets: {len(triplets)}", flush=True)
    if args.dry_run:
        print(json.dumps({"plan": {"base": BASE, "epochs": args.epochs, "batch": args.batch,
                                   "lr": LR, "lora": LORA, "seed": SEED,
                                   "examples": len(triplets)}, "vram": vram}, indent=1))
        return 0

    random.seed(SEED)
    torch.manual_seed(SEED)
    examples = [InputExample(texts=[t["anchor"], t["positive"], t["negative"]])
                for t in triplets]
    random.shuffle(examples)

    model = SentenceTransformer(BASE, device="cuda")
    model.add_adapter(LoraConfig(task_type=TaskType.FEATURE_EXTRACTION, **LORA))
    print(f"LoRA attached (r={LORA['r']}, qkv+dense)", flush=True)

    loader = DataLoader(examples, shuffle=True, batch_size=args.batch, drop_last=True)
    loss = losses.MultipleNegativesRankingLoss(model)
    started = time.perf_counter()
    model.fit(train_objectives=[(loader, loss)], epochs=args.epochs,
              warmup_steps=max(10, int(len(loader) * args.epochs * 0.1)),
              optimizer_params={"lr": LR}, use_amp=True, show_progress_bar=False)
    seconds = time.perf_counter() - started
    peak = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    model.save(str(OUT))
    print(f"saved -> {OUT}  ({seconds:.0f}s, peak {peak} GB)", flush=True)

    del model
    torch.cuda.empty_cache()
    _merge(OUT, MERGED)

    RECORD.write_bytes((json.dumps({
        "generated_by": "research/embed_universal/train_hard.py",
        "base": BASE, "seed": SEED, "epochs": args.epochs, "batch": args.batch, "lr": LR,
        "lora": LORA, "loss": "MultipleNegativesRankingLoss",
        "examples": len(triplets),
        "changed_from_v1": ("the training example only: (anchor, positive, hard_negative) "
                            "instead of (anchor, positive). Recipe, seed, rank, epochs, learning "
                            "rate and loss are v1's, so the comparison attributes to mining."),
        "seconds": round(seconds, 1),
        "peak_allocated_gb": peak,
        "vram_before": vram,
        "vram_margin_required_gb": REQUIRED_FREE_GB,
        "ollama": ("left running deliberately: the owner was working in two other projects whose "
                   "memory hooks use it. The margin above is what makes that safe, and it was "
                   "measured rather than assumed."),
    }, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
