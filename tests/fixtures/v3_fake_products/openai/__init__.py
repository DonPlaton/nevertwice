"""A FAKE openai for the v3 adapter suite: OpenAI().chat.completions.create posts to OPENAI_BASE_URL/chat/completions
with the key from the constructor or OPENAI_API_KEY, as the SDK does; a non-2xx answer raises (APIStatusError-like)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from types import SimpleNamespace

import _fake_http as H

__version__ = "0.0.0-fake"


class APIStatusError(RuntimeError):
    pass


class _Completions:
    def __init__(self, owner) -> None:
        self.owner = owner

    def create(self, **kwargs):
        H.log("openai.chat.completions.create", model=kwargs.get("model"),
              response_format_type=(kwargs.get("response_format") or {}).get("type"),
              temperature=kwargs.get("temperature", "<default>"))
        req = urllib.request.Request(self.owner.base_url.rstrip("/") + "/chat/completions",
                                     data=json.dumps(kwargs).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.owner.key}"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise APIStatusError(f"Error code: {e.code} - {e.read().decode('utf-8', 'replace')[:200]}") from None
        content = data["choices"][0]["message"]["content"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class OpenAI:
    def __init__(self, **kwargs) -> None:
        self.key = kwargs.get("api_key") or os.environ.get("OPENAI_API_KEY")
        self.base_url = kwargs.get("base_url") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        H.log("OpenAI", base_url=self.base_url, key_set=bool(self.key))
        self.chat = SimpleNamespace(completions=_Completions(self))
