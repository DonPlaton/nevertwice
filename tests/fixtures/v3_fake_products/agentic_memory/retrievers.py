"""The FAKE agentic_memory.retrievers, shaped as the pin's (ceffb86 retrievers.py:42-110): ChromaRetriever builds an
EPHEMERAL chromadb.Client(Settings(allow_reset=True)) and looks the embedding function up by its module-level name
SentenceTransformerEmbeddingFunction at call time - the name the adapter replaces."""
from __future__ import annotations

import json

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction


class ChromaRetriever:
    def __init__(self, collection_name: str = "memories", model_name: str = "all-MiniLM-L6-v2") -> None:
        self.client = chromadb.Client(Settings(allow_reset=True))
        self.embedding_function = SentenceTransformerEmbeddingFunction(model_name=model_name)
        self.collection = self.client.get_or_create_collection(name=collection_name,
                                                               embedding_function=self.embedding_function)

    def add_document(self, document: str, metadata: dict, doc_id: str) -> None:
        processed = {k: json.dumps(v) if isinstance(v, (list, dict)) else str(v) for k, v in metadata.items()}
        self.collection.add(documents=[document], metadatas=[processed], ids=[doc_id])

    def search(self, query: str, k: int = 5) -> dict:
        return self.collection.query(query_texts=[query], n_results=k)
