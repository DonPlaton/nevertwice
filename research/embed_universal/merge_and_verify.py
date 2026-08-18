#!/usr/bin/env python3
"""Ship step A: fold the LoRA adapter into base bge-m3 -> a plain HF checkpoint
(models/universal_v1_merged), then verify the merged model embeds IDENTICALLY to the
adapter-attached one (cosine ~1.0 over sample texts) - a silent merge bug here would
ship a model that never saw the training.
"""
import sys
from pathlib import Path

import numpy as np
import torch
from peft import PeftModel
from transformers import AutoModel, AutoTokenizer

HERE = Path(__file__).parent
SRC = HERE / "models" / "universal_v1"
DST = HERE / "models" / "universal_v1_merged"

base = AutoModel.from_pretrained("BAAI/bge-m3", torch_dtype=torch.float32)
peft = PeftModel.from_pretrained(base, str(SRC))
merged = peft.merge_and_unload()
merged.save_pretrained(str(DST))
tok = AutoTokenizer.from_pretrained("BAAI/bge-m3")
tok.save_pretrained(str(DST))
print(f"merged -> {DST}", flush=True)

# verification: adapter-attached (via sentence-transformers) vs merged (manual CLS+norm)
from sentence_transformers import SentenceTransformer, models as st_models

st_adapter = SentenceTransformer(str(SRC), device="cuda")
word = st_models.Transformer(str(DST))
pool = st_models.Pooling(word.get_word_embedding_dimension(), pooling_mode="cls")
st_merged = SentenceTransformer(modules=[word, pool, st_models.Normalize()], device="cuda")

texts = ["frozen diff integrity check before merging",
         "перепроверять контрольные суммы артефактов после каждой регенерации",
         "the retry logic exists because of the flaky upstream api and also fixes it",
         "gradient checkpointing halves memory at the cost of recompute",
         "избегать мутации аргументов по умолчанию в python"] * 4
a = st_adapter.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
b = st_merged.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
cos = (a * b).sum(axis=1)
print(f"adapter-vs-merged cosine: min {cos.min():.6f} mean {cos.mean():.6f}", flush=True)
ok = cos.min() > 0.9999
print("MERGE VERIFIED" if ok else "MERGE MISMATCH - do not ship", flush=True)
sys.exit(0 if ok else 1)
