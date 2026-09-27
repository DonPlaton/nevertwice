"""The FAKE agentic_memory.memory_system, shaped as the pin's (ceffb86): module-level imports of SentenceTransformer and
BM25Okapi, a ChromaRetriever built in __init__ (a temporary one reset, then the real one), a json_schema analysis call per
note, and consolidate_memories() rebuilding the retriever every evo_threshold evolutions - here every note evolves, so
the rebuild comes after evo_threshold notes. search_agentic(query, k) returns the retriever's hits as dicts."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

from rank_bm25 import BM25Okapi  # noqa: F401 - imported at module level, as the pin does
from sentence_transformers import SentenceTransformer  # noqa: F401 - ditto

import _fake_http as H

from .llm_controller import LLMController
from .retrievers import ChromaRetriever

SCHEMA = {"type": "json_schema", "json_schema": {"name": "response", "schema": {
    "type": "object", "properties": {"keywords": {"type": "array"}, "context": {"type": "string"},
                                     "tags": {"type": "array"}}}}}


class AgenticMemorySystem:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2", llm_backend: str = "openai",
                 llm_model: str = "gpt-4o-mini", evo_threshold: int = 100, api_key: str | None = None) -> None:
        H.log("AgenticMemorySystem", model_name=model_name, llm_backend=llm_backend, llm_model=llm_model,
              evo_threshold=evo_threshold, api_key_set=bool(api_key))
        self.memories: dict = {}
        self.model_name = model_name
        if os.environ.get("NVT3_FAKE_USE_ST"):             # a product that loads a sentence model: must be refused
            SentenceTransformer(model_name)
        try:
            temp = ChromaRetriever(collection_name="memories", model_name=self.model_name)
            temp.client.reset()
        except Exception:  # noqa: BLE001 - the pin logs a warning
            pass
        self.retriever = ChromaRetriever(collection_name="memories", model_name=self.model_name)
        self.llm_controller = LLMController(llm_backend, llm_model, api_key)
        self.evo_cnt = 0
        self.evo_threshold = evo_threshold

    def add_note(self, content: str, time: str | None = None, **kwargs) -> str:
        H.log("add_note", content=content, time=time)
        analysis = json.loads(self.llm_controller.get_completion(f"Analyze: {content}", response_format=SCHEMA) or "{}")
        nid = uuid.uuid4().hex
        stamp = time or datetime.now().strftime("%Y%m%d%H%M")
        self.memories[nid] = {"id": nid, "content": content, "timestamp": stamp,
                              "keywords": analysis.get("keywords", []), "context": analysis.get("context", "General")}
        self.retriever.add_document(content, {"id": nid, "content": content, "timestamp": stamp}, nid)
        self.evo_cnt += 1
        if self.evo_cnt % self.evo_threshold == 0:
            self.consolidate_memories()
        return nid

    def consolidate_memories(self) -> None:
        H.log("consolidate_memories", n=len(self.memories))
        self.retriever = ChromaRetriever(collection_name="memories", model_name=self.model_name)
        if os.environ.get("NVT3_FAKE_LOSE_EF"):            # a rebuild that loses the embedding function
            self.retriever.embedding_function = object()
        for m in self.memories.values():
            self.retriever.add_document(m["content"], {"id": m["id"], "content": m["content"],
                                                       "timestamp": m["timestamp"]}, m["id"])

    def search_agentic(self, query: str, k: int = 5) -> list:
        H.log("search_agentic", query=query, k=k)
        if not self.memories:
            return []
        res = self.retriever.search(query, k)
        out = []
        for i, doc_id in enumerate(res["ids"][0][:k]):
            md = res["metadatas"][0][i] or {}
            out.append({"id": doc_id, "content": md.get("content", ""), "timestamp": md.get("timestamp", ""),
                        "is_neighbor": False})
        # the pin then appends up to k linked neighbours (memory_system.py:556-...); here: the next notes in order
        seen = {m["id"] for m in out}
        for m in list(self.memories.values()):
            if len(out) >= 2 * k:
                break
            if m["id"] not in seen:
                out.append({"id": m["id"], "content": m["content"], "timestamp": m["timestamp"], "is_neighbor": True})
                seen.add(m["id"])
        return out
