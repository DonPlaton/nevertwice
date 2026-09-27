"""The FAKE graphiti_core.embedder.openai: OpenAIEmbedder(config) posts to <base_url>/embeddings in the OpenAI shape."""
from __future__ import annotations

from dataclasses import dataclass

import _fake_http as H


@dataclass
class OpenAIEmbedderConfig:
    embedding_model: str
    embedding_dim: int
    api_key: str | None = None
    base_url: str | None = None


class OpenAIEmbedder:
    def __init__(self, config: OpenAIEmbedderConfig) -> None:
        H.log("OpenAIEmbedder", model=config.embedding_model, dim=config.embedding_dim, base_url=config.base_url)
        self.config = config

    async def create(self, input_data: list) -> list:
        out = H.post(self.config.base_url.rstrip("/") + "/embeddings",
                     {"model": self.config.embedding_model, "input": list(input_data)})
        return out["data"][0]["embedding"]
