"""The FAKE OpenAIRerankerClient: a rank() would post a chat completion (the RRF recipes never call it)."""
from __future__ import annotations

import _fake_http as H


class OpenAIRerankerClient:
    def __init__(self, config=None, client=None) -> None:
        H.log("OpenAIRerankerClient", model=getattr(config, "model", None))
        self.config = config

    async def rank(self, query: str, passages: list) -> list:
        H.post(self.config.base_url.rstrip("/") + "/chat/completions",
               {"model": self.config.model, "messages": [{"role": "user", "content": query}]}, bearer=self.config.api_key)
        return [(p, 1.0) for p in passages]
