"""A FAKE chromadb.utils.embedding_functions: OllamaEmbeddingFunction(url, model_name) embeds through <url>/api/embed."""
from __future__ import annotations

import _fake_http as H


class OllamaEmbeddingFunction:
    def __init__(self, url: str = "http://localhost:11434", model_name: str = "chroma/all-minilm-l6-v2-f32",
                 **kwargs) -> None:
        H.log("OllamaEmbeddingFunction", url=url, model_name=model_name, extra=sorted(kwargs))
        self.url, self.model_name = url.rstrip("/"), model_name

    def __call__(self, input: list) -> list:
        return H.post(self.url + "/api/embed", {"model": self.model_name, "input": list(input)})["embeddings"]


class SentenceTransformerEmbeddingFunction:
    def __init__(self, *a, **k) -> None:
        raise RuntimeError("the fake refuses SentenceTransformer: v3 arms embed with the v3 tag only")
