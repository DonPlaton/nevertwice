"""A FAKE mem0 for the v3 adapter suite (tests/research/_test_v3_arm_mem0.py) - never a benchmark subject.

It records the keyword arguments of every call (to the JSONL file NVT3_FAKE_LOG names) and talks HTTP the way the real
product does, so the adapter's counter, pacer and preflight are exercised: at build its Ollama embedder LISTS the models
and PULLS a missing one (mem0/embeddings/ollama.py in 2.0.19), an add with infer=True calls the configured DeepSeek base
URL's /chat/completions with the key mem0 reads from DEEPSEEK_API_KEY - through Memory.llm.client.chat.completions.create,
the path of mem0's DeepSeekLLM and its openai.OpenAI (2.0.19: llms/deepseek.py:41, 108), whose response carries the
upstream's usage (Q-AB-1: the adapter counts there) - and every text is embedded through
<ollama_base_url>/api/embed. add(timestamp=...) raises ValueError, as mem0 OSS does. Memories persist as JSON under the
vector store's path, so a new read-stage process sees them (Q25). Memory.vector_store is mem0 2.2.0's Qdrant store as
far as M35 reads it (_FakeQdrant): the BM25 encoder loaded lazily at the first add or keyword_search, the bm25 slot,
keyword_search called by search, and the store's warnings on its own logger - NVT3_FAKE_BM25 "noslot" (a collection
without the slot) or "noencoder" (the encoder fails to load) turns BM25 off the way the product does.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace

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


class _FakeAPIError(RuntimeError):
    """What the fake SDK raises for a message carrying NVT3-RAISE-LLM (the adapter's counter must hand it on as is)."""


class _Completions:
    """DeepSeekLLM.client.chat.completions - an openai.OpenAI in the real product (mem0/llms/deepseek.py): create()
    posts to <base_url>/chat/completions with the key and returns an SDK-like object carrying the upstream's usage.
    It keeps what it received and what it returned, so the product can check the adapter's counter changed neither."""

    def __init__(self, base_url: str, key: str | None) -> None:
        self.base_url, self.key = base_url, key
        self.last_params = self.last_response = self.last_error = None

    def create(self, **params):
        self.last_params = params
        if any("NVT3-RAISE-LLM" in str(m.get("content")) for m in params.get("messages") or []):
            self.last_error = _FakeAPIError("the fake SDK refused this request")
            raise self.last_error
        data = _post(self.base_url.rstrip("/") + "/chat/completions", params, bearer=self.key)
        u = data.get("usage")
        usage = (SimpleNamespace(prompt_tokens=u.get("prompt_tokens"), completion_tokens=u.get("completion_tokens"))
                 if isinstance(u, dict) else None)
        msg = data["choices"][0]["message"]
        self.last_response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(**msg))], usage=usage)
        return self.last_response


_QLOG = logging.getLogger("mem0.vector_stores.qdrant")     # mem0 2.2.0's store logger (vector_stores/qdrant.py:26)


def _words(text: str) -> set:
    return set(re.findall(r"\w+", text.lower()))


class _FakeQdrant:
    """mem0 2.2.0's Qdrant store as M35 reads it (vector_stores/qdrant.py): _bm25_encoder None until first needed, then
    the encoder or False (sticky, with the warning "Failed to load BM25 encoder"); _has_bm25_slot, False for a
    collection that predates v3 (the "predates v3 hybrid search" warning at init); keyword_search None without either,
    else the unit's memories scored by the words they share with the query."""

    def __init__(self, mem: "Memory") -> None:
        self._mem = mem
        self._bm25_encoder = None
        self._has_bm25_slot = os.environ.get("NVT3_FAKE_BM25") != "noslot"
        if not self._has_bm25_slot:
            _QLOG.warning("Collection 'nvt3' predates v3 hybrid search (no 'bm25' sparse slot). BM25 keyword scoring "
                          "will be disabled for this collection; semantic search works normally. To enable hybrid "
                          "search, use a fresh collection.")

    def _get_bm25_encoder(self):
        if self._bm25_encoder is None:
            if os.environ.get("NVT3_FAKE_BM25") == "noencoder":
                _QLOG.warning("Failed to load BM25 encoder: %s", "the fake has no model files")
                self._bm25_encoder = False
            else:
                self._bm25_encoder = object()
        return self._bm25_encoder if self._bm25_encoder is not False else None

    def keyword_search(self, query, top_k=5, filters=None):
        _log("keyword_search", query=query, top_k=top_k, filters=filters)
        if not self._has_bm25_slot or self._get_bm25_encoder() is None:
            return None
        q = _words(query)
        mine = [m for m in self._mem._load() if m["user_id"] == (filters or {}).get("user_id")]
        pts = [SimpleNamespace(id=m["id"], score=float(len(q & _words(m["memory"])))) for m in mine]
        return sorted(pts, key=lambda x: -x.score)[:top_k]


class Memory:
    def __init__(self, config: dict) -> None:
        self.config = config
        self._emb = config["embedder"]["config"]
        self._llm = config["llm"]
        cfg = self._llm["config"]
        if self._llm["provider"] == "deepseek" and not os.environ.get("NVT3_FAKE_NO_CLIENT"):
            comp = _Completions(cfg["deepseek_base_url"], os.environ.get("DEEPSEEK_API_KEY"))
            self.llm = SimpleNamespace(config=cfg, client=SimpleNamespace(chat=SimpleNamespace(completions=comp)))
        else:
            self.llm = SimpleNamespace(config=cfg)            # the Ollama LLM: no OpenAI client
        self._path = Path(config["vector_store"]["config"]["path"]) / "fake_memories.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self.vector_store = _FakeQdrant(self)

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
            comp = self.llm.client.chat.completions
            params = {"model": self._llm["config"]["model"],
                      "messages": [{"role": "system", "content": "extract facts"}, *msgs]}
            try:
                resp = comp.create(**params)
            except Exception as e:  # noqa: BLE001 - mem0 logs a failed extraction and stores nothing
                _log("llm_raised", type=type(e).__name__, same=e is comp.last_error)
                return {"results": []}
            got = comp.last_params or {}
            _log("llm_call", same_response=resp is comp.last_response,
                 same_params=set(got) == set(params) and all(got[k] is params[k] for k in params))
        store = self._load()
        results = []
        if msgs and self.vector_store._has_bm25_slot:
            self.vector_store._get_bm25_encoder()        # the store encodes BM25 at insert (qdrant.py:198-215)
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
        self.vector_store.keyword_search(query=query.lower(), top_k=max(top_k * 4, 60), filters=filters)

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
