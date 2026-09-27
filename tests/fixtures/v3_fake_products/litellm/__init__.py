"""A FAKE litellm for the v3 adapter suite: completion(model="ollama_chat/<m>", ...) posts to OLLAMA_API_BASE/api/chat
(litellm's own env name for the Ollama base) and logs whether a temperature was sent."""
from __future__ import annotations

import os
from types import SimpleNamespace

import _fake_http as H

__version__ = "0.0.0-fake"


def completion(model: str, messages: list, **kwargs):
    base = os.environ.get("OLLAMA_API_BASE", "http://localhost:11434")
    H.log("litellm.completion", model=model, base=base, response_format_type=(kwargs.get("response_format") or {})
          .get("type"), temperature=kwargs.get("temperature", "<default>"))
    out = H.post(base.rstrip("/") + "/api/chat", {"model": model.split("/", 1)[1], "messages": messages,
                                                  "format": "json", "stream": False})
    content = (out.get("message") or {}).get("content", "{}")
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
