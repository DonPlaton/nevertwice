"""A FAKE fastembed for the v3 mem0 fakes - never a benchmark subject (the auditor's Q-PB, 2026-09-30).

Only what mem0 2.2.0's Qdrant store reaches (vector_stores/qdrant.py:92-107: SparseTextEmbedding(model_name=
"Qdrant/bm25")) and what arm_mem0.fe_cache_state reads, shaped as fastembed 0.8.1 (read as data): the encoder keeps its
Bm25 under .model (sparse_text_embedding.py:85); Bm25 resolves its cache from FASTEMBED_CACHE_PATH, else
<tempdir>/fastembed_cache (common/utils.py:58-69), honours HF_HUB_OFFLINE (model_management.py:454-458) and accepts a
cached snapshot - the one refs/main names, read unstripped - only if its model_file and every language file exist
(:466-467, :481-484), else "Could not find model in cache_dir" (:526-527, :536). The fake has no network: without
HF_HUB_OFFLINE it refuses where the real one would dial out.
"""
from fastembed.sparse.sparse_text_embedding import SparseTextEmbedding  # noqa: F401

__version__ = "0.8.1-fake"
