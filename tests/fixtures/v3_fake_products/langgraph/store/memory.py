"""A FAKE langgraph.store.memory.InMemoryStore for the v3 adapter suite: put / search with the shapes of the real one
(namespace tuples, string keys, dict values, SearchItem-like hits with a score), embedding the index fields through the
embeddings object it was given, and logging every search's keyword arguments."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

import _fake_http as H


@dataclass
class SearchItem:
    namespace: tuple
    key: str
    value: dict
    score: float | None = None


class InMemoryStore:
    def __init__(self, *, index: dict | None = None) -> None:
        H.log("InMemoryStore", dims=(index or {}).get("dims"), fields=(index or {}).get("fields"))
        self._index = index or {}
        self._items: dict = {}

    def _text(self, value: dict) -> str:
        fields = self._index.get("fields") or ["$"]
        if fields == ["$"]:
            return json.dumps(value, sort_keys=True)
        return " ".join(str(value.get(f, "")) for f in fields)

    def put(self, namespace: tuple, key: str, value: dict) -> None:
        H.log("put", namespace=list(namespace), key=key, value=value)
        vec = self._index["embed"].embed_documents([self._text(value)])[0] if self._index.get("embed") else None
        self._items[(tuple(namespace), key)] = (value, vec)

    def search(self, namespace_prefix: tuple, *, query: str | None = None, filter=None, limit: int = H._UNSET,
               offset: int = 0) -> list:
        H.log("search", namespace_prefix=list(namespace_prefix), query=query,
              limit="<default>" if limit is H._UNSET else limit)
        limit = 10 if limit is H._UNSET else limit
        mine = [(ns, k, v, vec) for (ns, k), (v, vec) in self._items.items() if ns[:len(namespace_prefix)]
                == tuple(namespace_prefix)]
        if query is None:
            return [SearchItem(ns, k, v) for ns, k, v, _ in mine][offset:offset + limit]
        qv = self._index["embed"].embed_query(query)

        def cos(a, b):
            na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
            return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0
        scored = sorted(((cos(qv, vec), ns, k, v) for ns, k, v, vec in mine), key=lambda t: -t[0])
        return [SearchItem(ns, k, v, s) for s, ns, k, v in scored][offset:offset + limit]
