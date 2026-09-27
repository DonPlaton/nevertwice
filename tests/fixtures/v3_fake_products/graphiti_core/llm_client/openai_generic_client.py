"""The FAKE OpenAIGenericClient: logs its structured_output_mode and posts each generate_response to
<base_url>/chat/completions with that response_format and the config's key as the bearer."""
from __future__ import annotations

import _fake_http as H


class OpenAIGenericClient:
    def __init__(self, config=None, cache: bool = False, client=None, max_tokens: int = 16384,
                 structured_output_mode: str = "json_schema") -> None:
        H.log("OpenAIGenericClient", structured_output_mode=structured_output_mode, max_tokens=max_tokens)
        self.config, self.mode = config, structured_output_mode

    async def generate_response(self, messages, *args, **kwargs) -> dict:
        return H.post(self.config.base_url.rstrip("/") + "/chat/completions",
                      {"model": self.config.model, "messages": messages, "response_format": {"type": self.mode}},
                      bearer=self.config.api_key)
