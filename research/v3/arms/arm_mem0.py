#!/usr/bin/env python3
"""PREREG-V3 TB4.6a (A6): the mem0 adapter - the product arm "mem0" and the retrieval arm "mem0-store" (infer=False), one
child per (arm, run, unit) and stage (Q25), speaking base.py's protocol. It runs in the mem0 venv from the arm's
runs-tree directory, beside byte-identical copies of base.py, _http_count.py and _ollama_pacer.py (ruling Q9 O-b); it
imports nothing of the repository. Named arm_mem0.py so that `import mem0` from that directory finds the venv's package,
never this file (Q-46-1).

    <mem0 venv python> -B arm_mem0.py <spec.json>

rev1 §2.2, as the auditor's Q-46-3 and Q-46-6 read it:
* mem0 writes through the DeepSeek provider at its proxy port (/u/<run>.<unit>/v1), model deepseek-flash; the key is the
  arm's proxy token, read by mem0 itself from DEEPSEEK_API_KEY - it never enters the config; the temperature is NOT set
  (the product's default; the value in effect is read from the proxy capture in the pilot, §5.5); no thinking route is
  known (§2.2.1), so none is sent here;
* one message per add(), role as given ("speaker_a" -> user, "speaker_b" -> assistant is the loader's mapping), content
  "<speaker>: <text>", the §5.3 header "Conversation from <YYYY-MM-DD>:" as its first line on a dated stand; timestamp=
  is never passed (mem0 OSS refuses it); user_id = the unit id;
* read: search(query, filters={"user_id": <unit>}, top_k=<k>, threshold=0.1), both explicit; the context is the memory
  lines, and mem0's add-time is never rendered;
* mem0-store: add(<the item's bytes>, infer=False, metadata={"index": i}); its LLM is never called - the config names an
  Ollama model on a closed loopback port, whose client does not connect at init, and llm_calls is counted (must be 0);
  the item bytes carry the §5.3 header already (the harness puts it there for every retrieval arm), so none is added;
* the embedder is Ollama with the v3 tag, 1024 dims. mem0's Ollama embedder LISTS the models at init and PULLS a missing
  one: before the product is built the adapter asks /api/tags itself and refuses by name when the tag is absent, so no
  pull can ever start (and a pull request, if one were made, refuses the arm);
* the store - qdrant on disk and the history database - lives under <unit>/store; end_write seals it (Q-45-5) and the
  read stage checks the seal before the product opens it.

The spec: arm, stage, stand, run, unit, unit_dir, port (the proxy port; None for mem0-store), embed_tag, ollama_url,
dated (does the stand carry dates), record_path. Counters: the HTTP counter (_http_count, installed inside the pacer),
the pacer's Ollama transport, the writes and their results.
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

ARMS = ("mem0", "mem0-store")
STAGES = ("write", "read")
SPEC_KEYS = ("arm", "stage", "stand", "run", "unit", "unit_dir", "port", "embed_tag", "ollama_url", "dated",
             "record_path")
DEEPSEEK_MODEL = "deepseek-flash"
THRESHOLD = 0.1
EMBED_DIMS = 1024
TOKEN_NAME = "DEEPSEEK_API_KEY"
TOKEN_NAMES = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN")
NO_LLM = {"provider": "ollama", "config": {"model": "nvt3-no-llm", "ollama_base_url": "http://127.0.0.1:0"}}
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Refused(RuntimeError):
    """The arm may not start, or may not take this request."""


def load_spec(path: str | os.PathLike) -> dict:
    spec = json.loads(Path(path).read_bytes().decode("utf-8"))
    missing = [k for k in SPEC_KEYS if k not in spec]
    extra = sorted(set(spec) - set(SPEC_KEYS))
    if missing or extra:
        raise Refused(f"the spec lacks {missing} or carries unknown keys {extra}")
    if spec["arm"] not in ARMS or spec["stage"] not in STAGES:
        raise Refused(f"unknown arm {spec['arm']!r} or stage {spec['stage']!r}")
    if not (_ID.fullmatch(str(spec["run"])) and _ID.fullmatch(str(spec["unit"]))):
        raise Refused("run and unit ids are [A-Za-z0-9_-]{1,64}, no dots (Q3)")
    if not Path(spec["unit_dir"]).is_absolute():
        raise Refused("unit_dir is not an absolute path")
    if (spec["arm"] == "mem0") != isinstance(spec["port"], int):
        raise Refused("mem0 writes through its proxy port; mem0-store has none")
    return spec


def proxy_base(spec: Mapping[str, Any]) -> str:
    return f"http://127.0.0.1:{spec['port']}/u/{spec['run']}.{spec['unit']}/v1"


def mem0_config(spec: Mapping[str, Any]) -> dict:
    """The product's config: no temperature, no key, no thinking field - only what rev1 §2.2 declares."""
    store = Path(spec["unit_dir"]) / "store"
    llm = ({"provider": "deepseek", "config": {"model": DEEPSEEK_MODEL, "deepseek_base_url": proxy_base(spec)}}
           if spec["arm"] == "mem0" else NO_LLM)
    return {"llm": llm,
            "embedder": {"provider": "ollama", "config": {"model": spec["embed_tag"],
                                                          "ollama_base_url": spec["ollama_url"],
                                                          "embedding_dims": EMBED_DIMS}},
            "vector_store": {"provider": "qdrant", "config": {"collection_name": "nvt3", "path": str(store / "qdrant"),
                                                              "embedding_model_dims": EMBED_DIMS, "on_disk": True}},
            "history_db_path": str(store / "history.db")}


def _tag_present(tags: Mapping[str, Any], tag: str) -> bool:
    want = {tag, tag if ":" in tag else f"{tag}:latest"}
    names = {m.get(k) for m in tags.get("models") or [] for k in ("name", "model")}
    return bool(want & names)


def declared(spec: Mapping[str, Any]) -> dict:
    """The §2.2 constants of this arm, for arm_decl."""
    product = spec["arm"] == "mem0"
    return {"date_route": ("header" if spec["dated"] else "none") if product else "none (item bytes, §5.3 by the harness)",
            "renderer": {"name": "mem0 memory lines, no add-time" if product else "index and item bytes (#<index> by the harness)"},
            "threshold": THRESHOLD, "top_k": "the point's k, explicit",
            "namespace": "user_id = unit id; qdrant and history.db under <unit>/store",
            "write_granularity": "one message per add()" if product else "one item per add(), infer=False",
            "llm_params": ({"temperature": "product default (value in effect: the proxy capture, §5.5)",
                            "max_tokens": "product default", "thinking_route": "none known (§2.2.1)"}
                           if product else {"llm": None, "llm_calls": "must be 0"}),
            "tools_allowed": [], "store_persistence": "disk"}


def bind(spec: Mapping[str, Any], env: Mapping[str, str]) -> tuple[Any, dict]:
    arm = spec["arm"]
    unit_dir = Path(spec["unit_dir"])
    store = unit_dir / "store"
    rec: dict[str, Any] = {"arm": arm, "stage": spec["stage"], "stand": spec["stand"], "run": spec["run"],
                           "unit": spec["unit"], "unit_store": str(store), "declared": declared(spec)}
    token = env.get(TOKEN_NAME)
    if arm == "mem0":
        if not token or not token.startswith("nvt3-"):
            raise Refused(f"{TOKEN_NAME} does not hold the arm's proxy token (nvt3-...)")
    else:
        held = [n for n in TOKEN_NAMES if n in env]
        if held:
            raise Refused(f"mem0-store has no LLM, yet {held} is set")
    if spec["stage"] == "write" and store.exists():
        raise Refused("the write stage needs a fresh store, and the unit store already exists")
    if spec["stage"] == "read":
        if not store.is_dir():
            raise Refused("the read stage opens the write stage's store, and there is none")
        rec["seal"] = B.check_seal(unit_dir, store)          # Q-45-5, before the product opens the store
    ollama_port = int(spec["ollama_url"].rsplit(":", 1)[1].split("/")[0])
    rec["http_doors"] = HC.install(proxy_port=spec["port"], ollama_ports=(ollama_port,))
    import _ollama_pacer as P  # noqa: PLC0415 - copied beside this file; installed AFTER the counter (Q-46-4)
    P.install("pace")
    with urllib.request.urlopen(spec["ollama_url"].rstrip("/") + "/api/tags", timeout=30) as r:
        tags = json.loads(r.read() or b"{}")
    if not _tag_present(tags, spec["embed_tag"]):
        raise Refused(f"the embed tag {spec['embed_tag']!r} is not in Ollama; mem0's embedder would pull it")
    import mem0  # noqa: PLC0415 - the venv's product (Q-46-1: never this file)
    from mem0 import Memory  # noqa: PLC0415
    rec["product"] = {"name": "mem0ai", "version": getattr(mem0, "__version__", None),
                      "file": str(Path(mem0.__file__).resolve())}
    if HERE in Path(mem0.__file__).resolve().parents:
        raise Refused("import mem0 found a module in the arm's directory, not the venv's package")
    store.mkdir(parents=True, exist_ok=True)
    cfg = mem0_config(spec)
    rec["config"] = cfg
    mem = Memory.from_config(cfg)
    pulls = HC.total(HC.snapshot(), "ollama:pull")
    if pulls:
        raise Refused(f"the product sent {pulls} pull request(s) to Ollama while it was built")
    rec["env_names"] = sorted(env)
    rec["python"] = sys.version.split()[0]
    return {"mem": mem, "pacer": P}, rec


class Handler:
    def __init__(self, spec: Mapping[str, Any], ns: Mapping[str, Any], rec: Mapping[str, Any]) -> None:
        self.spec, self.rec = spec, rec
        self.mem, self.pacer = ns["mem"], ns["pacer"]
        self.arm, self.stage, self.unit = spec["arm"], spec["stage"], spec["unit"]
        self.store = Path(spec["unit_dir"]) / "store"
        self.item_shas: dict[int, str] = {}
        self.writes = {"adds": 0, "results": 0, "empty": 0}
        self.reads = {"reads": 0, "items_returned": 0}

    def _stage(self, want: str, op: str) -> None:
        if self.stage != want:
            raise RuntimeError(f"{op} belongs to the {want} stage; this process is the {self.stage} stage (Q25)")

    def hello(self) -> dict:
        return {"protocol": B.PROTOCOL, "system": "mem0", "arm": self.arm, "stage": self.stage,
                "version": self.rec["product"]["version"], "python": self.rec["python"],
                "llm_label": f"deepseek:{DEEPSEEK_MODEL}" if self.arm == "mem0" else None,
                "embedder": self.spec["embed_tag"], "env_names": self.rec["env_names"], "start": self.rec}

    def write(self, item: dict, date: str | None = None) -> dict:
        self._stage("write", "write")
        t0 = time.time()
        if self.arm == "mem0":
            if item["role"] not in ("user", "assistant"):
                raise ValueError(f"a mem0 message is from user or assistant, got {item['role']!r}")
            content = f"{item['speaker']}: {item['text']}"
            if self.spec["dated"]:
                if not date:
                    raise ValueError("a dated stand's write carries its date (§5.3 header)")
                content = f"Conversation from {date[:10]}:\n{content}"
            given = {"text_sha256": B.text_sha256(content)}
            res = self.mem.add([{"role": item["role"], "content": content}], user_id=self.unit, infer=True)
        else:
            idx = item["index"]
            if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 or idx in self.item_shas:
                raise ValueError(f"an item needs a new non-negative int index, got {idx!r}")
            self.item_shas[idx] = B.text_sha256(item["text"])
            given = {"item_sha256": self.item_shas[idx]}
            res = self.mem.add(item["text"], user_id=self.unit, infer=False, metadata={"index": idx})
        results = (res or {}).get("results", []) if isinstance(res, dict) else (res or [])
        self.writes["adds"] += 1
        self.writes["results"] += len(results)
        self.writes["empty"] += not results
        return {"op_id": item["item_id"], **given, "results": len(results), "t0": t0, "t1": time.time()}

    def end_write(self) -> dict:
        self._stage("write", "end_write")
        t0 = time.time()
        got = self.mem.get_all(filters={"user_id": self.unit})
        memories = got.get("results", []) if isinstance(got, dict) else (got or [])
        seal = B.write_seal(self.spec["unit_dir"], self.store, arm=self.arm, run=self.spec["run"], unit=self.unit)
        out = {"footprint": {"retrievable": len(memories), "chars": sum(len(m.get("memory") or "") for m in memories)},
               "seal": seal, "t0": t0, "t1": time.time()}
        if self.arm == "mem0-store":
            out["items_sha256"] = B.items_digest(self.item_shas)
        return out

    def read(self, qid: str, query: str, k: int) -> dict:
        self._stage("read", "read")
        t0 = time.time()
        res = self.mem.search(query, filters={"user_id": self.unit}, top_k=int(k), threshold=THRESHOLD)
        hits = res.get("results", []) if isinstance(res, dict) else (res or [])
        items = []
        for rank, h in enumerate(hits, 1):
            if self.arm == "mem0":
                items.append({"text": h.get("memory") or "", "rank": rank})
            else:
                idx = (h.get("metadata") or {}).get("index")
                if not isinstance(idx, int) or isinstance(idx, bool):
                    raise RuntimeError(f"a mem0-store hit carries no item index: {str(h.get('id'))[:40]}")
                items.append({"index": idx, "text": h.get("memory") or "", "rank": rank})
        self.reads["reads"] += 1
        self.reads["items_returned"] += len(items)
        return {"qid": qid, "items": items, "items_returned": len(items), "k": int(k), "t0": t0, "t1": time.time()}

    def counters(self) -> dict:
        snap = HC.snapshot()
        transport: dict = {}
        self.pacer.attach(transport)
        return {"http": snap, "llm_calls": HC.total(snap, "proxy:chat") + HC.total(snap, "ollama:generate"),
                "writes": dict(self.writes), **self.reads, "ollama_transport": transport.get("ollama_transport")}


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
                                      "stage": spec["stage"], "unit": spec["unit"]})
        return RefusedHandler(reason)
    _record(spec["record_path"], {"ok": True, **rec})
    return handler


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("usage: arm_mem0.py <spec.json>\n")
        sys.exit(2)
    ENV_AT_START = dict(os.environ)
    sys.exit(B.main_with(lambda: build(sys.argv[1], ENV_AT_START)))
