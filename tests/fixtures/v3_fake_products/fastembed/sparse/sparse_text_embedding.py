"""The fake fastembed's SparseTextEmbedding (see fastembed/__init__.py): the encoder keeps its Bm25 under .model."""
from __future__ import annotations

from fastembed.sparse.bm25 import Bm25


class SparseTextEmbedding:
    def __init__(self, model_name: str = "Qdrant/bm25", cache_dir: str | None = None, **kw) -> None:
        self.model_name = model_name
        self.model = Bm25(model_name, cache_dir=cache_dir, **kw)
