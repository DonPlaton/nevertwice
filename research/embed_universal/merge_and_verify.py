#!/usr/bin/env python3
"""Ship step A: fold the LoRA adapter into base bge-m3 -> a SENTENCE-TRANSFORMERS
checkpoint (models/universal_v1_merged), then verify by loading the destination
EXACTLY the way a downstream user will (`SentenceTransformer(DST)`).

Review 2026-08 D12: the first version saved a bare HF checkpoint (no modules.json /
1_Pooling / sentence_bert_config.json) and verified with a hand-assembled CLS
pipeline - so "MERGE VERIFIED" never exercised the published load path, and a clean
re-run would have shipped a repo that sentence-transformers silently loads
MEAN-pooled ("Creating a new one with mean pooling"): every downstream user getting
embeddings the model was never trained or evaluated with. The ST files that made
the actual publish work had been added by hand, out of band. Now the script saves
the assembled ST model itself and the verification loads DST from disk.
"""
import sys
from pathlib import Path

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
print(f"merged (bare HF) -> {DST}", flush=True)

from sentence_transformers import SentenceTransformer, models as st_models

# assemble the ST pipeline (CLS pooling + normalize, bge-m3's convention) and SAVE it
# on top of the bare checkpoint - this is what makes SentenceTransformer(DST) and the
# GGUF conversion (which needs 1_Pooling/ + modules.json) load with the right pooling
word = st_models.Transformer(str(DST))
pool = st_models.Pooling(word.get_word_embedding_dimension(), pooling_mode="cls")
st_assembled = SentenceTransformer(modules=[word, pool, st_models.Normalize()],
                                   device="cuda")
st_assembled.save(str(DST))
print(f"sentence-transformers files saved -> {DST}", flush=True)

# verification: adapter-attached vs the PUBLISHED load path (DST from disk, no
# hand-assembly - if the pooling files are wrong or missing, this diverges)
st_adapter = SentenceTransformer(str(SRC), device="cuda")
st_published = SentenceTransformer(str(DST), device="cuda")
first_module = str(type(st_published[1]).__name__) if len(st_published) > 1 else "?"

texts = ["frozen diff integrity check before merging",
         "перепроверять контрольные суммы артефактов после каждой регенерации",
         "the retry logic exists because of the flaky upstream api and also fixes it",
         "gradient checkpointing halves memory at the cost of recompute",
         "избегать мутации аргументов по умолчанию в python"] * 4
a = st_adapter.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
b = st_published.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
cos = (a * b).sum(axis=1)
pool_ok = getattr(st_published[1], "pooling_mode_cls_token", False) \
    if len(st_published) > 1 else False
print(f"published-path pooling module: {first_module} (cls={pool_ok})", flush=True)
print(f"adapter-vs-published cosine: min {cos.min():.6f} mean {cos.mean():.6f}", flush=True)
ok = bool(cos.min() > 0.9999 and pool_ok)
print("MERGE VERIFIED (published load path)" if ok
      else "MERGE MISMATCH - do not ship", flush=True)
sys.exit(0 if ok else 1)
