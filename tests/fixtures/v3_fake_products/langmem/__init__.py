"""A FAKE langmem for the v3 adapter suite: create_memory_store_manager logs every keyword it was given (so "defaults
only" is testable), and the manager's invoke logs the messages, asks the model once (a real chat completion to the
proxy) and puts one memory per user message into the store, in langmem's default Memory shape
{"kind": "Memory", "content": {"content": <text>}}."""
from __future__ import annotations

import uuid

import _fake_http as H

__version__ = "0.0.0-fake"


class _Manager:
    def __init__(self, model, store, namespace) -> None:
        self.model, self.store, self.namespace = model, store, tuple(namespace)

    def invoke(self, input: dict, config: dict | None = None) -> list:
        H.log("invoke", messages=input.get("messages"), config=config)
        self.model.complete([{"role": "system", "content": "extract memories"}, *input["messages"]])
        out = []
        for m in input["messages"]:
            if m["role"] == "user":
                key = uuid.uuid4().hex
                self.store.put(self.namespace, key, {"kind": "Memory", "content": {"content": m["content"]}})
                out.append(key)
        return out


def create_memory_store_manager(model, /, **kwargs) -> _Manager:
    H.log("create_memory_store_manager", kwargs=sorted(kwargs), namespace=list(kwargs.get("namespace") or ()))
    return _Manager(model, kwargs["store"], kwargs["namespace"])
