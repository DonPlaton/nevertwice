#!/usr/bin/env python3
"""PREREG-V3 TB4.6b (A6): the chroma-store adapter - the retrieval arm "chroma-store" (rev1 §2.2: "chromadb of amem_v3
(A-MEM's store)", vendor-default), one child per (run, unit) and stage (Q25), speaking base.py's protocol. It runs in the
amem_v3 venv from the arm's runs-tree directory beside byte-identical copies of base.py, _http_count.py and
_ollama_pacer.py (Q9 O-b); it imports nothing of the repository, and is named arm_chroma.py so that `import chromadb`
from that directory finds the venv's package (Q-46-1).

    <amem venv python> -B arm_chroma.py <spec.json>

* the store is chroma's own persistent mode, chromadb.PersistentClient(path=<unit>/store/chroma) - on disk, so the
  write stage exits, the store is sealed (Q-45-5) and a new read-stage process checks the seal before opening it;
* one collection "items" with chroma's defaults (no metadata of ours, so the default distance), its embedding function
  chroma's OllamaEmbeddingFunction with the v3 tag - the one change from vendor-default, declared (§2.5, §5.1);
* write: add(ids=[str(index)], documents=[<the item's bytes>]) - the bytes the harness gives every retrieval arm, the
  §5.3 header already in them on a dated stand (the auditor's retrieval-tier rule); item_sha256 per item and
  items_sha256 per unit;
* read: query(query_texts=[q], n_results=k), the index and the item's bytes back in rank order; a hit that is not one
  of the unit's items is refused by name;
* no LLM and no token; a tag Ollama lacks is refused before the store is built.

The spec: arm ("chroma-store"), stage, stand, run, unit, unit_dir, embed_tag, ollama_url, record_path.
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

ARM = "chroma-store"
STAGES = ("write", "read")
SPEC_KEYS = ("arm", "stage", "stand", "run", "unit", "unit_dir", "embed_tag", "ollama_url", "record_path")
COLLECTION = "items"
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
    if spec["arm"] != ARM or spec["stage"] not in STAGES:
        raise Refused(f"unknown arm {spec['arm']!r} or stage {spec['stage']!r}")
    if not (_ID.fullmatch(str(spec["run"])) and _ID.fullmatch(str(spec["unit"]))):
        raise Refused("run and unit ids are [A-Za-z0-9_-]{1,64}, no dots (Q3)")
    if not Path(spec["unit_dir"]).is_absolute():
        raise Refused("unit_dir is not an absolute path")
    return spec


DECLARED = {"date_route": "none (item bytes, §5.3 by the harness)",
            "renderer": {"name": "index and item bytes (#<index> by the harness)"},
            "threshold": "n/a", "n_results": "the point's k, explicit",
            "namespace": "chromadb.PersistentClient at <unit>/store/chroma, collection 'items'",
            "write_granularity": "one item per add()", "llm_params": {"llm": None},
            "deviations": ["embedding function: chroma's OllamaEmbeddingFunction with the v3 tag instead of "
                           "all-MiniLM-L6-v2 (§2.5, §5.1)"],
            "tools_allowed": [], "store_persistence": "disk"}


def _tag_present(tags: Mapping[str, Any], tag: str) -> bool:
    want = {tag, tag if ":" in tag else f"{tag}:latest"}
    return bool(want & {m.get(k) for m in tags.get("models") or [] for k in ("name", "model")})


def bind(spec: Mapping[str, Any], env: Mapping[str, str]) -> tuple[dict, dict]:
    unit_dir = Path(spec["unit_dir"])
    store = unit_dir / "store"
    rec: dict[str, Any] = {"arm": ARM, "stage": spec["stage"], "stand": spec["stand"], "run": spec["run"],
                           "unit": spec["unit"], "unit_store": str(store), "declared": DECLARED}
    held = [n for n in TOKEN_NAMES if n in env]
    if held:
        raise Refused(f"chroma-store has no LLM, yet {held} is set")
    if spec["stage"] == "write" and store.exists():
        raise Refused("the write stage needs a fresh store, and the unit store already exists")
    if spec["stage"] == "read":
        if not store.is_dir():
            raise Refused("the read stage opens the write stage's store, and there is none")
        rec["seal"] = B.check_seal(unit_dir, store)          # Q-45-5, before the product opens the store
    ollama_port = int(spec["ollama_url"].rsplit(":", 1)[1].split("/")[0])
    rec["http_doors"] = HC.install(proxy_port=None, ollama_ports=(ollama_port,))
    import _ollama_pacer as P  # noqa: PLC0415 - copied beside this file; installed AFTER the counter (Q-46-4)
    P.install("observe")               # R-EMBED-PATH: the proxy leg paces and retries - one layer for every arm
    P.set_route("127.0.0.1", ollama_port)        # the leg is the one route; any other Ollama is direct_calls
    rec["pacer"] = "observe"
    rec["ollama_route"] = spec["ollama_url"]
    with urllib.request.urlopen(spec["ollama_url"].rstrip("/") + "/api/tags", timeout=30) as r:
        if not _tag_present(json.loads(r.read() or b"{}"), spec["embed_tag"]):
            raise Refused(f"the embed tag {spec['embed_tag']!r} is not in Ollama")
    import chromadb  # noqa: PLC0415 - the venv's product (Q-46-1: never this file)
    rec["product"] = {"name": "chromadb", "version": getattr(chromadb, "__version__", None),
                      "file": str(Path(chromadb.__file__).resolve())}
    if HERE in Path(chromadb.__file__).resolve().parents:
        raise Refused("import chromadb found a module in the arm's directory, not the venv's package")
    from chromadb.utils.embedding_functions import OllamaEmbeddingFunction  # noqa: PLC0415
    ef = OllamaEmbeddingFunction(url=spec["ollama_url"], model_name=spec["embed_tag"])
    store.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(store / "chroma"))
    if spec["stage"] == "write":
        coll = client.get_or_create_collection(name=COLLECTION, embedding_function=ef)
    else:
        coll = client.get_collection(name=COLLECTION, embedding_function=ef)
    rec["env_names"] = sorted(env)
    rec["python"] = sys.version.split()[0]
    return {"collection": coll, "pacer": P}, rec


class Handler:
    def __init__(self, spec: Mapping[str, Any], ns: Mapping[str, Any], rec: Mapping[str, Any]) -> None:
        self.spec, self.rec = spec, rec
        self.coll, self.pacer = ns["collection"], ns["pacer"]
        self.stage, self.unit = spec["stage"], spec["unit"]
        self.store = Path(spec["unit_dir"]) / "store"
        self.item_shas: dict[int, str] = {}
        self.reads = {"reads": 0, "items_returned": 0}

    def _stage(self, want: str, op: str) -> None:
        if self.stage != want:
            raise RuntimeError(f"{op} belongs to the {want} stage; this process is the {self.stage} stage (Q25)")

    def hello(self) -> dict:
        return {"protocol": B.PROTOCOL, "system": "chromadb", "arm": ARM, "stage": self.stage,
                "version": self.rec["product"]["version"], "python": self.rec["python"], "llm_label": None,
                "embedder": self.spec["embed_tag"], "env_names": self.rec["env_names"], "start": self.rec}

    def write(self, item: dict, date: str | None = None) -> dict:
        self._stage("write", "write")
        t0 = time.time()
        idx = item["index"]
        if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 or idx in self.item_shas:
            raise ValueError(f"an item needs a new non-negative int index, got {idx!r}")
        self.item_shas[idx] = B.text_sha256(item["text"])
        self.coll.add(ids=[str(idx)], documents=[item["text"]])
        return {"op_id": item["item_id"], "item_sha256": self.item_shas[idx], "t0": t0, "t1": time.time()}

    def end_write(self) -> dict:
        self._stage("write", "end_write")
        t0 = time.time()
        n = self.coll.count()
        seal = B.write_seal(self.spec["unit_dir"], self.store, arm=ARM, run=self.spec["run"], unit=self.unit)
        return {"footprint": {"retrievable": n}, "seal": seal, "items_sha256": B.items_digest(self.item_shas),
                "t0": t0, "t1": time.time()}

    def read(self, qid: str, query: str, k: int) -> dict:
        self._stage("read", "read")
        t0 = time.time()
        res = self.coll.query(query_texts=[query], n_results=int(k))
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        items = []
        for rank, (i, d) in enumerate(zip(ids, docs), 1):
            if not (isinstance(i, str) and i.isdigit()):
                raise RuntimeError(f"a chroma-store hit is not one of the unit's items: id {str(i)[:40]!r}")
            items.append({"index": int(i), "text": d, "rank": rank})
        self.reads["reads"] += 1
        self.reads["items_returned"] += len(items)
        return {"qid": qid, "items": items, "items_returned": len(items), "k": int(k), "t0": t0, "t1": time.time()}

    def counters(self) -> dict:
        snap = HC.snapshot()
        transport: dict = {}
        self.pacer.attach(transport)
        return {"http": snap, "llm_calls": HC.total(snap, "proxy:") + HC.total(snap, "ollama:generate"),
                **self.reads, "ollama_transport": transport.get("ollama_transport")}


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
        _record(spec["record_path"], {"ok": False, "error": reason[:B.ERROR_MAX], "arm": ARM,
                                      "stage": spec["stage"], "unit": spec["unit"]})
        return RefusedHandler(reason)
    _record(spec["record_path"], {"ok": True, **rec})
    return handler


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("usage: arm_chroma.py <spec.json>\n")
        sys.exit(2)
    ENV_AT_START = dict(os.environ)
    sys.exit(B.main_with(lambda: build(sys.argv[1], ENV_AT_START)))
