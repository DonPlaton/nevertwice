#!/usr/bin/env python3
"""Universal v1, step 4: LoRA-train bge-m3 on the synthetic memory-pair corpus.

Recipe proven in research/EMBED_SPECIALIZE.md round 1: MultipleNegativesRankingLoss over
(anchor, positive) pairs - in-batch negatives supply the "distinct lessons" population -
LoRA r=16 on qkv+dense, 3 epochs, bf16. The model is saved with the adapter attached
(merge for GGUF happens at ship time).
"""
import json
import random
import sys
from pathlib import Path

import torch
from sentence_transformers import SentenceTransformer, InputExample, losses
from torch.utils.data import DataLoader

random.seed(11)
torch.manual_seed(11)
HERE = Path(__file__).parent
PAIRS = HERE / "data" / "train_pairs.jsonl"
OUT = HERE / "models" / "universal_v1"
EPOCHS = 3
BATCH = 24
LR = 1e-4

pairs = [json.loads(l) for l in PAIRS.read_text(encoding="utf-8").splitlines() if l.strip()]
print(f"training pairs: {len(pairs)}", flush=True)
examples = [InputExample(texts=[p["anchor"], p["positive"]]) for p in pairs]
random.shuffle(examples)

model = SentenceTransformer("BAAI/bge-m3", device="cuda")
from peft import LoraConfig, TaskType
model.add_adapter(LoraConfig(task_type=TaskType.FEATURE_EXTRACTION, r=16, lora_alpha=32,
                             lora_dropout=0.05,
                             target_modules=["query", "key", "value", "dense"]))
print("LoRA attached (r=16, qkv+dense)", flush=True)

loader = DataLoader(examples, shuffle=True, batch_size=BATCH, drop_last=True)
loss = losses.MultipleNegativesRankingLoss(model)
model.fit(train_objectives=[(loader, loss)], epochs=EPOCHS,
          warmup_steps=max(10, int(len(loader) * EPOCHS * 0.1)),
          optimizer_params={"lr": LR}, use_amp=True, show_progress_bar=False)
model.save(str(OUT))
print(f"saved -> {OUT}", flush=True)
