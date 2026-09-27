"""A FAKE chromadb for the v3 adapter suite: PersistentClient(path) keeps each collection as JSON under that path (so a
new read-stage process sees it), and logs how clients and collections are made and every query's keyword arguments."""
from __future__ import annotations

import json
import math
from pathlib import Path

import _fake_http as H

__version__ = "0.0.0-fake"


class Collection:
    def __init__(self, path: Path | None, name: str, embedding_function, memory: dict | None = None) -> None:
        self._file = path / f"{name}.json" if path is not None else None
        self._mem = memory                      # an ephemeral client's collection lives in this dict
        self._ef = embedding_function
        self.name = name

    def _load(self) -> dict:
        if self._mem is not None:
            return self._mem
        return json.loads(self._file.read_text(encoding="utf-8")) if self._file.exists() else {}

    def _store(self, data: dict) -> None:
        if self._mem is None:
            self._file.write_text(json.dumps(data), encoding="utf-8")

    def add(self, *, ids: list, documents: list, metadatas=None, embeddings=None) -> None:
        H.log("add", ids=ids, documents=documents, metadatas=metadatas)
        data = self._load()
        for j, (i, d, v) in enumerate(zip(ids, documents, self._ef(documents))):
            if i in data:
                raise ValueError(f"id {i} exists")
            data[i] = {"document": d, "vec": v, "metadata": (metadatas or [None] * len(ids))[j]}
        self._store(data)

    def count(self) -> int:
        return len(self._load())

    def query(self, *, query_texts: list, n_results=H._UNSET, where=None, include=None) -> dict:
        H.log("query", query_texts=query_texts, n_results="<default>" if n_results is H._UNSET else n_results)
        n = 10 if n_results is H._UNSET else n_results
        qv = self._ef(query_texts)[0]
        data = self._load()

        def dist(a, b):
            return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
        ranked = sorted(data.items(), key=lambda kv: dist(qv, kv[1]["vec"]))[:n]
        return {"ids": [[i for i, _ in ranked]], "documents": [[r["document"] for _, r in ranked]],
                "metadatas": [[r.get("metadata") for _, r in ranked]],
                "distances": [[dist(qv, r["vec"]) for _, r in ranked]]}


class PersistentClient:
    def __init__(self, path: str) -> None:
        H.log("PersistentClient", path=path)
        self._path = Path(path)
        self._path.mkdir(parents=True, exist_ok=True)

    def get_or_create_collection(self, name: str, embedding_function=None, metadata=None) -> Collection:
        H.log("get_or_create_collection", name=name, metadata=metadata)
        return Collection(self._path, name, embedding_function)

    def get_collection(self, name: str, embedding_function=None) -> Collection:
        H.log("get_collection", name=name)
        if not (self._path / f"{name}.json").exists():
            raise ValueError(f"Collection {name} does not exist.")
        return Collection(self._path, name, embedding_function)


class _EphemeralClient:
    """chromadb.Client(Settings(...)): in memory only; reset() empties it."""

    def __init__(self, settings=None) -> None:
        H.log("Client", allow_reset=getattr(settings, "allow_reset", None))
        self._collections: dict = {}

    def reset(self) -> bool:
        self._collections.clear()
        return True

    def get_or_create_collection(self, name: str, embedding_function=None, metadata=None) -> Collection:
        H.log("get_or_create_collection", name=name, metadata=metadata, ephemeral=True)
        return Collection(None, name, embedding_function, memory=self._collections.setdefault(name, {}))


def Client(settings=None) -> _EphemeralClient:  # noqa: N802 - chromadb's own name
    return _EphemeralClient(settings)
