"""A FAKE mem0 for the v3 adapter suite (tests/research/_test_v3_arm_mem0.py) - never a benchmark subject.

It records the keyword arguments of every call (to the JSONL file NVT3_FAKE_LOG names) and talks HTTP the way the real
product does, so the adapter's counter, pacer and preflight are exercised: at build its Ollama embedder LISTS the models
and PULLS a missing one (mem0/embeddings/ollama.py in 2.0.19), an add with infer=True calls the configured DeepSeek base
URL's /chat/completions with the key mem0 reads from DEEPSEEK_API_KEY, and every text is embedded through
<ollama_base_url>/api/embed. add(timestamp=...) raises ValueError, as mem0 OSS does. Memories persist as JSON under the
vector store's path, so a new read-stage process sees them (Q25).
"""
from __future__ import annotations

import json
import math
import os
import urllib.request
import uuid
from pathlib import Path

__version__ = "0.0.0-fake"
_UNSET = object()      # a keyword the caller did not pass is logged as "<default>", so "explicit" is testable


def _log(event: str, **kw) -> None:
    path = os.environ.get("NVT3_FAKE_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": event, **kw}, default=str, sort_keys=True) + "\n")


def _post(url: str, obj, bearer: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=json.dumps(obj).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


class Memory:
    def __init__(self, config: dict) -> None:
        self.config = config
        self._emb = config["embedder"]["config"]
        self._llm = config["llm"]
        self._path = Path(config["vector_store"]["config"]["path"]) / "fake_memories.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_config(cls, config_dict: dict) -> "Memory":
        _log("from_config", config=config_dict)
        emb = config_dict["embedder"]["config"]
        with urllib.request.urlopen(emb["ollama_base_url"].rstrip("/") + "/api/tags", timeout=30) as r:
            tags = json.loads(r.read() or b"{}")
        names = {m.get(k) for m in tags.get("models") or [] for k in ("name", "model")}
        if os.environ.get("NVT3_FAKE_PULL_ALWAYS") or (emb["model"] not in names
                                                       and f"{emb['model']}:latest" not in names):
            _post(emb["ollama_base_url"].rstrip("/") + "/api/pull", {"model": emb["model"]})
        return cls(config_dict)

    def _load(self) -> list:
        return json.loads(self._path.read_text(encoding="utf-8")) if self._path.exists() else []

    def _embed(self, text: str) -> list:
        out = _post(self._emb["ollama_base_url"].rstrip("/") + "/api/embed", {"model": self._emb["model"], "input": text})
        return out["embeddings"][0]

    def add(self, messages, *, user_id=None, agent_id=None, run_id=None, metadata=None, timestamp=None,
            expiration_date=None, infer=True, memory_type=None, prompt=None) -> dict:
        _log("add", messages=messages, user_id=user_id, metadata=metadata, timestamp=timestamp, infer=infer)
        if timestamp is not None:
            raise ValueError("Platform-only temporal parameter: timestamp is not supported in OSS")
        msgs = [{"role": "user", "content": messages}] if isinstance(messages, str) else list(messages)
        if infer:
            cfg = self._llm["config"]
            base = cfg.get("deepseek_base_url") or cfg.get("ollama_base_url")
            _post(base.rstrip("/") + "/chat/completions",
                  {"model": cfg["model"], "messages": [{"role": "system", "content": "extract facts"}, *msgs]},
                  bearer=os.environ.get("DEEPSEEK_API_KEY"))
        store = self._load()
        results = []
        for m in msgs:
            mem = {"id": uuid.uuid4().hex, "memory": m["content"], "user_id": user_id, "metadata": metadata or {},
                   "vec": self._embed(m["content"]), "created_at": "2026-09-27T12:00:00+00:00"}
            store.append(mem)
            results.append({"id": mem["id"], "memory": mem["memory"], "event": "ADD"})
        self._path.write_text(json.dumps(store), encoding="utf-8")
        return {"results": results}

    def search(self, query, *, top_k=_UNSET, filters=None, threshold=_UNSET, rerank=False, explain=False,
               reference_date=None, show_expired=False, **kwargs) -> dict:
        _log("search", query=query, top_k="<default>" if top_k is _UNSET else top_k, filters=filters,
             threshold="<default>" if threshold is _UNSET else threshold, rerank=rerank, extra=sorted(kwargs))
        top_k = 20 if top_k is _UNSET else top_k                   # mem0 2.0.19's defaults
        threshold = 0.1 if threshold is _UNSET else threshold
        qv = self._embed(query)

        def cos(a, b):
            na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
            return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0
        mine = [m for m in self._load() if m["user_id"] == (filters or {}).get("user_id")]
        scored = sorted(((cos(qv, m["vec"]), m) for m in mine), key=lambda t: -t[0])
        hits = [{"id": m["id"], "memory": m["memory"], "metadata": m["metadata"], "score": s,
                 "created_at": m["created_at"]} for s, m in scored if s >= threshold][:top_k]
        return {"results": hits}

    def get_all(self, *, filters=None, top_k=100, **kwargs) -> dict:
        _log("get_all", filters=filters, top_k=top_k)
        mine = [m for m in self._load() if m["user_id"] == (filters or {}).get("user_id")]
        return {"results": [{"id": m["id"], "memory": m["memory"], "metadata": m["metadata"]} for m in mine][:top_k]}
