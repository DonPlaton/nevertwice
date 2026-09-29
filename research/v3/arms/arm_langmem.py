#!/usr/bin/env python3
"""PREREG-V3 TB4.6a (A6): the langmem adapter - the product arm "langmem" and the retrieval arm "langmem-store", one child
per (arm, run, unit) that serves BOTH stages: LangGraph's InMemoryStore lives in memory only, so the write stage and the
read stage share the process (ruling Q25's own exception; store_persistence = memory, no seal - the auditor's Q-46-2),
and the write and read times are kept apart. It runs in the langmem venv from the arm's runs-tree directory beside
byte-identical copies of base.py, _http_count.py and _ollama_pacer.py (Q9 O-b) and imports nothing of the repository.
Named arm_langmem.py so that `import langmem` from that directory finds the venv's package (Q-46-1).

    <langmem venv python> -B arm_langmem.py <spec.json>

rev1 §2.2, as the auditor's Q-46-5 reads it:
* the writer is langchain_openai.ChatOpenAI with model "deepseek-flash", base_url <proxy>/u/<run>.<unit>/v1, the api key
  the arm's proxy token, extra_body {"thinking": {"type": "disabled"}} - thinking off through the model's documented
  extra_body; the temperature is NOT set (the value in effect is read from the proxy capture in the pilot, §5.5);
* the manager is create_memory_store_manager(model, store=<the unit's store>, namespace=("memories", <unit>)) with the
  product's defaults only (query_limit 5; no enable_inserts / enable_deletes of our own) - ("memories", <unit>) is
  rev1's "namespace = unit id" in the manager's own namespace form;
* one session thread is one manager.invoke({"messages": [...]}) - the role-preserving list, content "<speaker>: <text>"
  (Q-45-4's text form), the §5.3 header "Conversation from <YYYY-MM-DD>:" as the first line of the first message's
  content on a dated stand;
* the store is InMemoryStore(index={"dims": 1024, "embed": OllamaEmbeddings(the v3 tag), "fields": ...}) per unit;
* read: store.search(<namespace>, query=q, limit=<k>), rendered as "- item" lines (the memory's content);
* langmem-store: store.put(("items", <unit>), <index>, {"text": <the item's bytes>}), index field "text", no LLM and
  no token; the §5.3 header is in the item bytes already (the harness puts it there for every retrieval arm).

The spec: arm, stage ("both"), stand, run, unit, unit_dir, port (None for langmem-store), embed_tag, ollama_url, dated,
record_path.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import base as B  # noqa: E402 - the protocol module, copied beside this file
import _http_count as HC  # noqa: E402 - the counting-only HTTP wrapper, copied beside this file

ARMS = ("langmem", "langmem-store")
SPEC_KEYS = ("arm", "stage", "stand", "run", "unit", "unit_dir", "port", "embed_tag", "ollama_url", "dated",
             "record_path")
DEEPSEEK_MODEL = "deepseek-flash"
THINKING_OFF = {"thinking": {"type": "disabled"}}
EMBED_DIMS = 1024
TOKEN_NAME = "DEEPSEEK_API_KEY"
TOKEN_NAMES = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN")
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Refused(RuntimeError):
    """The arm may not start, or may not take this request."""


def load_spec(path: str | os.PathLike) -> dict:
    spec = json.loads(Path(path).read_bytes().decode("utf-8"))
    missing = [k for k in SPEC_KEYS if k not in spec]
    extra = sorted(set(spec) - set(SPEC_KEYS))
    if missing or extra:
        raise Refused(f"the spec lacks {missing} or carries unknown keys {extra}")
    if spec["arm"] not in ARMS:
        raise Refused(f"unknown arm {spec['arm']!r}")
    if spec["stage"] != "both":
        raise Refused("a langmem arm's store lives in memory: one process serves both stages (stage 'both', Q-46-2)")
    if not (_ID.fullmatch(str(spec["run"])) and _ID.fullmatch(str(spec["unit"]))):
        raise Refused("run and unit ids are [A-Za-z0-9_-]{1,64}, no dots (Q3)")
    if (spec["arm"] == "langmem") != isinstance(spec["port"], int):
        raise Refused("langmem writes through its proxy port; langmem-store has none")
    return spec


def proxy_base(spec: Mapping[str, Any]) -> str:
    return f"http://127.0.0.1:{spec['port']}/u/{spec['run']}.{spec['unit']}/v1"


def namespace(spec: Mapping[str, Any]) -> tuple:
    return ("memories", spec["unit"]) if spec["arm"] == "langmem" else ("items", spec["unit"])


def declared(spec: Mapping[str, Any]) -> dict:
    product = spec["arm"] == "langmem"
    return {"date_route": ("header" if spec["dated"] else "none") if product else "none (item bytes, §5.3 by the harness)",
            "renderer": {"name": "'- item' lines of the memory content" if product
                         else "index and item bytes (#<index> by the harness)"},
            "threshold": "n/a", "limit": "the point's k, explicit",
            "namespace": f"{list(namespace(spec))} (rev1: namespace = unit id), InMemoryStore per unit",
            "write_granularity": "one session thread per manager.invoke" if product else "one item per store.put",
            "llm_params": ({"temperature": "product default (value in effect: the proxy capture, §5.5)",
                            "max_tokens": "product default",
                            "thinking_route": "ChatOpenAI extra_body {'thinking': {'type': 'disabled'}}"}
                           if product else {"llm": None, "llm_calls": "must be 0"}),
            "manager": "create_memory_store_manager defaults (query_limit 5)" if product else None,
            "tools_allowed": ["Memory", "PatchDoc", "RemoveDoc", "PatchFunctionErrors", "PatchFunctionName"]
            if product else [], "store_persistence": "memory"}


def _tag_present(tags: Mapping[str, Any], tag: str) -> bool:
    want = {tag, tag if ":" in tag else f"{tag}:latest"}
    return bool(want & {m.get(k) for m in tags.get("models") or [] for k in ("name", "model")})


def bind(spec: Mapping[str, Any], env: Mapping[str, str]) -> tuple[dict, dict]:
    arm = spec["arm"]
    rec: dict[str, Any] = {"arm": arm, "stage": "both", "stand": spec["stand"], "run": spec["run"], "unit": spec["unit"],
                           "declared": declared(spec)}
    token = env.get(TOKEN_NAME)
    if arm == "langmem":
        if not token or not token.startswith("nvt3-"):
            raise Refused(f"{TOKEN_NAME} does not hold the arm's proxy token (nvt3-...)")
    else:
        held = [n for n in TOKEN_NAMES if n in env]
        if held:
            raise Refused(f"langmem-store has no LLM, yet {held} is set")
    ollama_port = int(spec["ollama_url"].rsplit(":", 1)[1].split("/")[0])
    rec["http_doors"] = HC.install(proxy_port=spec["port"], ollama_ports=(ollama_port,))
    import _ollama_pacer as P  # noqa: PLC0415 - copied beside this file; installed AFTER the counter (Q-46-4)
    P.install("observe")               # R-EMBED-PATH: the proxy leg paces and retries - one layer for every arm
    P.set_route("127.0.0.1", ollama_port)        # the leg is the one route; any other Ollama is direct_calls
    rec["pacer"] = P._MODE  # the installed pacer's own mode, never a copy of the intention (B-PACER-REC)
    rec["ollama_route"] = spec["ollama_url"]
    with urllib.request.urlopen(spec["ollama_url"].rstrip("/") + "/api/tags", timeout=30) as r:
        if not _tag_present(json.loads(r.read() or b"{}"), spec["embed_tag"]):
            raise Refused(f"the embed tag {spec['embed_tag']!r} is not in Ollama")
    import langmem  # noqa: PLC0415 - the venv's product (Q-46-1: never this file)
    from langchain_ollama import OllamaEmbeddings  # noqa: PLC0415
    from langgraph.store.memory import InMemoryStore  # noqa: PLC0415
    rec["product"] = {"name": "langmem", "version": getattr(langmem, "__version__", None),
                      "file": str(Path(langmem.__file__).resolve())}
    if HERE in Path(langmem.__file__).resolve().parents:
        raise Refused("import langmem found a module in the arm's directory, not the venv's package")
    embeddings = OllamaEmbeddings(model=spec["embed_tag"], base_url=spec["ollama_url"])
    fields = ["$"] if arm == "langmem" else ["text"]
    store = InMemoryStore(index={"dims": EMBED_DIMS, "embed": embeddings, "fields": fields})
    rec["store"] = {"class": "InMemoryStore", "dims": EMBED_DIMS, "fields": fields, "namespace": list(namespace(spec))}
    manager = None
    if arm == "langmem":
        from langchain_openai import ChatOpenAI  # noqa: PLC0415
        model = ChatOpenAI(**{"model": DEEPSEEK_MODEL, "base_url": proxy_base(spec), "api_key": token,
                              "extra_body": THINKING_OFF})
        manager = langmem.create_memory_store_manager(model, store=store, namespace=namespace(spec))
        rec["model"] = {"class": "ChatOpenAI", "model": DEEPSEEK_MODEL, "base_url": proxy_base(spec),
                        "extra_body": THINKING_OFF, "api_key": "<set>", "temperature": "not set"}
    rec["env_names"] = sorted(env)
    rec["python"] = sys.version.split()[0]
    return {"store": store, "manager": manager, "pacer": P}, rec


def render_memory(value: Any) -> str:
    """A stored memory as one "- item" line: langmem's default Memory schema holds {"content": <text>}."""
    content = value.get("content") if isinstance(value, dict) else None
    if isinstance(content, dict) and isinstance(content.get("content"), str):
        text = content["content"]
    elif isinstance(content, str):
        text = content
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return f"- {text}"


class Handler:
    def __init__(self, spec: Mapping[str, Any], ns: Mapping[str, Any], rec: Mapping[str, Any]) -> None:
        self.spec, self.rec = spec, rec
        self.store, self.manager, self.pacer = ns["store"], ns["manager"], ns["pacer"]
        self.arm, self.unit = spec["arm"], spec["unit"]
        self.ns = namespace(spec)
        self.phase = "write"                  # write -> read, once: no write after the first read (Q25)
        self.item_shas: dict[int, str] = {}
        self.times = {"write": [None, None], "read": [None, None]}
        self.counts = {"writes": 0, "reads": 0, "items_returned": 0}

    def _mark(self, phase: str, t0: float, t1: float) -> None:
        span = self.times[phase]
        span[0] = t0 if span[0] is None else span[0]
        span[1] = t1

    def hello(self) -> dict:
        return {"protocol": B.PROTOCOL, "system": "langmem", "arm": self.arm, "stage": "both",
                "version": self.rec["product"]["version"], "python": self.rec["python"],
                "llm_label": f"deepseek:{DEEPSEEK_MODEL}" if self.arm == "langmem" else None,
                "embedder": self.spec["embed_tag"], "env_names": self.rec["env_names"], "start": self.rec}

    def write(self, item: dict, date: str | None = None) -> dict:
        if self.phase != "write":
            raise RuntimeError("a write after end_write: the write stage is closed (Q25)")
        t0 = time.time()
        if self.arm == "langmem":
            msgs = []
            for m in item["messages"]:
                if m["role"] not in ("user", "assistant"):
                    raise ValueError(f"a langmem message is from user or assistant, got {m['role']!r}")
                msgs.append({"role": m["role"], "content": f"{m['speaker']}: {m['text']}"})
            if not msgs:
                raise ValueError("a session thread holds at least one message")
            if self.spec["dated"]:
                if not date:
                    raise ValueError("a dated stand's write carries its date (§5.3 header)")
                msgs[0]["content"] = f"Conversation from {date[:10]}:\n" + msgs[0]["content"]
            given = {"text_sha256": B.text_sha256(json.dumps(msgs, ensure_ascii=False, sort_keys=True))}
            self.manager.invoke({"messages": msgs})
        else:
            idx = item["index"]
            if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 or idx in self.item_shas:
                raise ValueError(f"an item needs a new non-negative int index, got {idx!r}")
            self.item_shas[idx] = B.text_sha256(item["text"])
            given = {"item_sha256": self.item_shas[idx]}
            self.store.put(self.ns, str(idx), {"text": item["text"]})
        t1 = time.time()
        self._mark("write", t0, t1)
        self.counts["writes"] += 1
        return {"op_id": item["item_id"], **given, "t0": t0, "t1": t1}

    def end_write(self) -> dict:
        if self.phase != "write":
            raise RuntimeError("end_write twice")
        t0 = time.time()
        self.phase = "read"
        stored = self.store.search(self.ns, limit=10 ** 6)
        out = {"footprint": {"retrievable": len(stored)}, "store_persistence": "memory", "t0": t0, "t1": time.time()}
        if self.arm == "langmem-store":
            out["items_sha256"] = B.items_digest(self.item_shas)
        return out

    def read(self, qid: str, query: str, k: int) -> dict:
        if self.phase != "read":
            raise RuntimeError("a read before end_write: the write stage is still open (Q25)")
        t0 = time.time()
        hits = self.store.search(self.ns, query=query, limit=int(k))
        items = []
        for rank, h in enumerate(hits, 1):
            if self.arm == "langmem":
                items.append({"text": render_memory(h.value), "rank": rank})
            else:
                if not (isinstance(h.key, str) and h.key.isdigit() and int(h.key) in self.item_shas):
                    raise RuntimeError(f"a langmem-store hit is not one of the unit's items: key {str(h.key)[:40]!r}")
                items.append({"index": int(h.key), "text": h.value.get("text", ""), "rank": rank})
        t1 = time.time()
        self._mark("read", t0, t1)
        self.counts["reads"] += 1
        self.counts["items_returned"] += len(items)
        return {"qid": qid, "items": items, "items_returned": len(items), "k": int(k), "t0": t0, "t1": t1}

    def counters(self) -> dict:
        snap = HC.snapshot()
        transport: dict = {}
        self.pacer.attach(transport)
        return {"http": snap, "llm_calls": HC.total(snap, "proxy:chat") + HC.total(snap, "ollama:generate"),
                **self.counts, "stage_times": {k: list(v) for k, v in self.times.items()},
                "ollama_transport": transport.get("ollama_transport")}


class RefusedHandler:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __getattr__(self, op: str):
        if op not in B.OPS:
            raise AttributeError(op)

        def refuse(**_kw):
            raise Refused(f"the arm refused to start: {self.reason}")
        return refuse


def _record(path: str, rec: Mapping[str, Any]) -> None:
    Path(path).write_bytes((json.dumps(rec, ensure_ascii=False, sort_keys=True, indent=1, default=str) + "\n")
                           .encode("utf-8"))


def build(spec_path: str, env: Mapping[str, str]) -> Any:
    try:
        spec = load_spec(spec_path)
    except (Refused, OSError, ValueError, KeyError) as e:
        return RefusedHandler(f"{type(e).__name__}: {e}")
    try:
        ns, rec = bind(spec, env)
        handler = Handler(spec, ns, rec)
    except Exception as e:  # noqa: BLE001 - every failure to bind is a named refusal, recorded
        reason = f"{type(e).__name__}: {e}"
        _record(spec["record_path"], {"ok": False, "error": reason[:B.ERROR_MAX], "arm": spec["arm"],
                                      "unit": spec["unit"]})
        return RefusedHandler(reason)
    _record(spec["record_path"], {"ok": True, **rec})
    return handler


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("usage: arm_langmem.py <spec.json>\n")
        sys.exit(2)
    ENV_AT_START = dict(os.environ)
    sys.exit(B.main_with(lambda: build(sys.argv[1], ENV_AT_START)))
