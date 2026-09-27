"""The FAKE graphiti_core.llm_client.config.LLMConfig: logs every field it was given (an unset temperature or max_tokens
logs as "<default>", so "none of ours" is testable). Its fields arrive as keywords (read by name below)."""
from __future__ import annotations

import _fake_http as H

_FIELDS = ("api_key", "model", "base_url", "small_model")


class LLMConfig:
    def __init__(self, **kwargs) -> None:
        temperature = kwargs.pop("temperature", H._UNSET)
        max_tokens = kwargs.pop("max_tokens", H._UNSET)
        values = {f: kwargs.pop(f, None) for f in _FIELDS}
        H.log("LLMConfig", api_key_set=bool(values["api_key"]), model=values["model"],
              small_model=values["small_model"], base_url=values["base_url"],
              temperature="<default>" if temperature is H._UNSET else temperature,
              max_tokens="<default>" if max_tokens is H._UNSET else max_tokens, extra=sorted(kwargs))
        self.api_key, self.model = values["api_key"], values["model"]
        self.base_url, self.small_model = values["base_url"], values["small_model"]
