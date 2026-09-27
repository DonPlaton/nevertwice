"""A FAKE langchain_ollama for the v3 adapter suite: OllamaEmbeddings embeds through <base_url>/api/embed and logs how
it was built."""
from __future__ import annotations

import _fake_http as H

__version__ = "0.0.0-fake"


class OllamaEmbeddings:
    def __init__(self, *, model: str, base_url: str = "http://127.0.0.1:11434", **kwargs) -> None:
        H.log("OllamaEmbeddings", model=model, base_url=base_url, extra=sorted(kwargs))
        self.model, self.base_url = model, base_url.rstrip("/")

    def embed_documents(self, texts: list) -> list:
        return H.post(self.base_url + "/api/embed", {"model": self.model, "input": list(texts)})["embeddings"]

    def embed_query(self, text: str) -> list:
        return self.embed_documents([text])[0]
