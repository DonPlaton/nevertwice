"""A FAKE langchain_openai for the v3 adapter suite: ChatOpenAI records how it was built and posts a chat completion to
<base_url>/chat/completions, merging extra_body into the body and sending temperature only when it was set - which is
what the proxy capture reads in the pilot (§5.5)."""
from __future__ import annotations

import _fake_http as H

__version__ = "0.0.0-fake"


class ChatOpenAI:
    def __init__(self, *, model: str, base_url: str, api_key: str, extra_body: dict | None = None,
                 temperature=H._UNSET, **kwargs) -> None:
        H.log("ChatOpenAI", model=model, base_url=base_url, api_key_set=bool(api_key), extra_body=extra_body,
              temperature="<default>" if temperature is H._UNSET else temperature, extra=sorted(kwargs))
        self.model, self.base_url, self._key = model, base_url.rstrip("/"), api_key
        self.extra_body, self.temperature = dict(extra_body or {}), temperature

    def complete(self, messages: list) -> dict:
        body = {"model": self.model, "messages": messages, **self.extra_body}
        if self.temperature is not H._UNSET:
            body["temperature"] = self.temperature
        return H.post(self.base_url + "/chat/completions", body, bearer=self._key)
