#!/usr/bin/env python3
"""PREREG-V3 TB4.6b (A6): the a-mem adapter - the product arm "a-mem" (agiresearch/A-mem at the pinned ceffb86), one
child per (run, unit) serving BOTH stages: A-mem keeps its store in an ephemeral chromadb.Client in memory
(retrievers.py:55 at the pin), so the write and read stages share the process (Q25's exception; the auditor's Q-46b-5,
and a declared erratum of rev1's "a fresh chroma directory per unit"). It runs in the amem venv from the arm's runs-tree
directory beside byte-identical copies of base.py, _http_count.py and _ollama_pacer.py (Q9 O-b), imports nothing of the
repository, and is named arm_amem.py so that `import agentic_memory` finds the venv's package (Q-46-1).

    <amem venv python> -B arm_amem.py <spec.json>

rev1 §2.2 and Q-46b-5:
* the writer is the upstream controller: on the DeepSeek stands OpenAIController, whose OpenAI client reads
  OPENAI_BASE_URL (the arm's proxy port, /u/<run>.<unit>/v1) and OPENAI_API_KEY (the arm's proxy token) from the
  environment - the adapter checks both and passes neither; its temperature is the pin's 0.7. Both controllers send a
  json_schema response_format, which DeepSeek refuses: blocked:structured-output is expected there, and a write that
  fails that way is refused under that name. On S6L the writer is qwen3-coder:30b through OllamaController, i.e.
  litellm's "ollama_chat/<model>" at OLLAMA_API_BASE = the arm's Ollama leg of the proxy (which drops the temperature,
  as the pin does);
* the embedder: the v3 tag through chroma's Ollama embedding function, replacing all-MiniLM-L6-v2 by rebinding the one
  name A-mem's retriever looks up at call time, agentic_memory.retrievers.SentenceTransformerEmbeddingFunction - so the
  temporary retriever, the real one and every consolidation's rebuild (memory_system.py:113, 119, 269) all get it;
  SentenceTransformer itself is rebound to a refusal, so a model can never be loaded;
* one note per turn: add_note("Speaker <speaker> says : <text>", time=<the session date>) on a dated stand, no time
  on an undated one (A-mem then stamps its add-time, which is never rendered);
* read: search_agentic(q, k); the note's content, with its timestamp on a dated stand; Point K the first k of what it
  returns (it can add linked neighbours), Point B all.

The spec: arm ("a-mem"), stage ("both"), stand, run, unit, unit_dir, llm ("deepseek" | "ollama"), llm_model, port (the
proxy port, deepseek), ollama_leg_url (the proxy's Ollama leg, ollama), embed_tag, ollama_url, dated, product_pin
({"commit", "blobs"}: the harness builds it from FREEZE-V3), record_path.

The installed package must be the pin's, file for file: the spec's product_pin names PIN_COMMIT (a constant here - the
double binding) and the git blob id of each .py; bind refuses a missing, extra or different file by name, and the map
goes to arm_decl (the venv amem_eval holds PyPI a-mem 0.2.6, a different code base - the auditor's check).
"""
from __future__ import annotations

import hashlib
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

ARM = "a-mem"
SPEC_KEYS = ("arm", "stage", "stand", "run", "unit", "unit_dir", "llm", "llm_model", "port", "ollama_leg_url",
             "embed_tag", "ollama_url", "dated", "product_pin", "record_path")
#: The A-mem commit rev1 pins (agiresearch/A-mem, A3): the spec's product_pin must name it (the auditor's double binding).
PIN_COMMIT = "ceffb860f0712bbae97b184d440df62bc910ca8d"
LLMS = {"deepseek": "deepseek-flash", "ollama": "qwen3-coder:30b"}
POINTS = ("K", "B")
TOKEN_NAME = "OPENAI_API_KEY"
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Refused(RuntimeError):
    """The arm may not start, or may not take this request."""


def load_spec(path: str | os.PathLike) -> dict:
    spec = json.loads(Path(path).read_bytes().decode("utf-8"))
    missing = [k for k in SPEC_KEYS if k not in spec]
    extra = sorted(set(spec) - set(SPEC_KEYS))
    if missing or extra:
        raise Refused(f"the spec lacks {missing} or carries unknown keys {extra}")
    if spec["arm"] != ARM:
        raise Refused(f"unknown arm {spec['arm']!r}")
    if spec["stage"] != "both":
        raise Refused("a-mem's store lives in memory: one process serves both stages (stage 'both', Q-46b-5)")
    if not (_ID.fullmatch(str(spec["run"])) and _ID.fullmatch(str(spec["unit"]))):
        raise Refused("run and unit ids are [A-Za-z0-9_-]{1,64}, no dots (Q3)")
    if LLMS.get(spec["llm"]) != spec["llm_model"]:
        raise Refused(f"llm {spec['llm']!r} writes with {LLMS.get(spec['llm'])!r}, not {spec['llm_model']!r}")
    if (spec["llm"] == "deepseek") != isinstance(spec["port"], int) or \
            (spec["llm"] == "ollama") != isinstance(spec["ollama_leg_url"], str):
        raise Refused("DeepSeek goes through the arm's proxy port, S6L's Ollama through the proxy's Ollama leg")
    return spec


def proxy_base(spec: Mapping[str, Any]) -> str:
    return f"http://127.0.0.1:{spec['port']}/u/{spec['run']}.{spec['unit']}/v1"


def declared(spec: Mapping[str, Any]) -> dict:
    return {"date_route": "field:time" if spec["dated"] else "none (add-time never rendered)",
            "renderer": {"name": "note content, its timestamp on dated stands; Point K the first k, Point B all"},
            "threshold": "n/a", "k": "search_agentic(q, k)",
            "namespace": "one AgenticMemorySystem per unit, in memory (an ephemeral chromadb.Client)",
            "write_granularity": "one note per turn",
            "llm_params": {"temperature": "0.7 (the pin's OpenAIController)" if spec["llm"] == "deepseek"
                           else "not sent (the pin's OllamaController)", "response_format": "json_schema (the pin's)",
                           "thinking_route": "not reached on DeepSeek (expected block); qwen3-coder:30b does not think"},
            "deviations": ["embedding function: chroma's OllamaEmbeddingFunction with the v3 tag instead of "
                           "all-MiniLM-L6-v2, bound in every retriever the system builds (§2.5, §5.1)",
                           "erratum rev1: the store is an ephemeral in-memory chromadb.Client at the pin, not a directory"],
            "tools_allowed": [], "store_persistence": "memory"}


def git_blob_sha(data: bytes) -> str:
    """git's blob id of a file's bytes, CRLF normalised to LF first (a checkout's line endings are not the pin's)."""
    body = data.replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(body) + body).hexdigest()


def installed_blobs(package_dir: Path) -> dict[str, str]:
    """{"agentic_memory/<path>": blob id} of every .py of the installed package (__pycache__ and .pyc aside)."""
    out = {}
    for f in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        out[f"{package_dir.name}/{f.relative_to(package_dir).as_posix()}"] = git_blob_sha(f.read_bytes())
    return out


def check_pin(pin: Mapping[str, Any], package_dir: Path) -> dict[str, str]:
    """The installed package is the pinned commit's, file for file (the auditor's rule for a product installed from
    git, not from a PyPI version): the spec names PIN_COMMIT and its blob map, and every installed .py matches it."""
    if pin.get("commit") != PIN_COMMIT:
        raise Refused(f"the spec's product_pin is not the pinned A-mem commit {PIN_COMMIT}")
    want, got = dict(pin.get("blobs") or {}), installed_blobs(package_dir)
    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    differ = sorted(k for k in set(want) & set(got) if want[k] != got[k])
    if missing or extra or differ:
        raise Refused(f"the installed agentic_memory is not the pin's: missing {missing}, extra {extra}, "
                      f"different {differ}")
    return got


def _tag_present(tags: Mapping[str, Any], tag: str) -> bool:
    want = {tag, tag if ":" in tag else f"{tag}:latest"}
    return bool(want & {m.get(k) for m in tags.get("models") or [] for k in ("name", "model")})


def bind(spec: Mapping[str, Any], env: Mapping[str, str]) -> tuple[dict, dict]:
    rec: dict[str, Any] = {"arm": ARM, "stage": "both", "stand": spec["stand"], "run": spec["run"], "unit": spec["unit"],
                           "llm": spec["llm"], "declared": declared(spec)}
    if spec["llm"] == "deepseek":
        token = env.get(TOKEN_NAME)
        if not token or not token.startswith("nvt3-"):
            raise Refused(f"{TOKEN_NAME} does not hold the arm's proxy token (nvt3-...)")
        if env.get("OPENAI_BASE_URL") != proxy_base(spec):
            raise Refused("OPENAI_BASE_URL is not the arm's proxy route")
        leg_port = spec["port"]
    else:
        if env.get("OLLAMA_API_BASE") != spec["ollama_leg_url"]:
            raise Refused("OLLAMA_API_BASE is not the arm's Ollama leg")
        if TOKEN_NAME in env:
            raise Refused(f"S6L's writer is local, yet {TOKEN_NAME} is set")
        leg_port = int(spec["ollama_leg_url"].rsplit(":", 1)[1].split("/")[0])
    ollama_port = int(spec["ollama_url"].rsplit(":", 1)[1].split("/")[0])
    rec["http_doors"] = HC.install(proxy_port=leg_port, ollama_ports=(ollama_port,))
    import _ollama_pacer as P  # noqa: PLC0415 - copied beside this file; installed AFTER the counter (Q-46-4)
    P.install("observe")               # R-EMBED-PATH: the proxy leg paces and retries - one layer for every arm
    P.set_route("127.0.0.1", ollama_port)        # the leg is the one route; any other Ollama is direct_calls
    rec["pacer"] = "observe"
    rec["ollama_route"] = spec["ollama_url"]
    with urllib.request.urlopen(spec["ollama_url"].rstrip("/") + "/api/tags", timeout=30) as r:
        if not _tag_present(json.loads(r.read() or b"{}"), spec["embed_tag"]):
            raise Refused(f"the embed tag {spec['embed_tag']!r} is not in Ollama")
    import agentic_memory  # noqa: PLC0415 - the venv's product (Q-46-1: never this file)
    rec["product"] = {"name": "agentic_memory", "version": getattr(agentic_memory, "__version__", None),
                      "file": str(Path(agentic_memory.__file__).resolve())}
    if HERE in Path(agentic_memory.__file__).resolve().parents:
        raise Refused("import agentic_memory found a module in the arm's directory, not the venv's package")
    rec["product"]["commit"] = PIN_COMMIT
    rec["product"]["blobs"] = check_pin(spec["product_pin"], Path(agentic_memory.__file__).resolve().parent)
    from chromadb.utils.embedding_functions import OllamaEmbeddingFunction  # noqa: PLC0415
    import agentic_memory.retrievers as AR  # noqa: PLC0415
    import agentic_memory.memory_system as MS  # noqa: PLC0415
    ef_built: list[str] = []

    def v3_embedding_function(*_a, **_k):
        """What A-mem's retriever gets whenever it asks for SentenceTransformerEmbeddingFunction."""
        ef = OllamaEmbeddingFunction(url=spec["ollama_url"], model_name=spec["embed_tag"])
        ef_built.append(type(ef).__name__)
        return ef

    class NoSentenceTransformer:
        def __init__(self, *a, **k):
            raise Refused("A-mem asked for a SentenceTransformer: v3 arms embed with the v3 tag only (§5.1)")

    AR.SentenceTransformerEmbeddingFunction = v3_embedding_function
    MS.SentenceTransformer = NoSentenceTransformer
    ms = MS.AgenticMemorySystem(model_name=spec["embed_tag"], llm_backend="openai" if spec["llm"] == "deepseek"
                                else "ollama", llm_model=spec["llm_model"])
    if not isinstance(ms.retriever.embedding_function, OllamaEmbeddingFunction):
        raise Refused("A-mem's retriever does not embed with the v3 tag")
    rec["evo_threshold"] = getattr(ms, "evo_threshold", None)
    rec["env_names"] = sorted(env)
    rec["python"] = sys.version.split()[0]
    return {"ms": ms, "ef_built": ef_built, "ef_class": OllamaEmbeddingFunction, "pacer": P}, rec


class Handler:
    def __init__(self, spec: Mapping[str, Any], ns: Mapping[str, Any], rec: Mapping[str, Any]) -> None:
        self.spec, self.rec = spec, rec
        self.ms, self.ef_built, self.ef_class, self.pacer = ns["ms"], ns["ef_built"], ns["ef_class"], ns["pacer"]
        self.unit = spec["unit"]
        self.phase = "write"
        self.times = {"write": [None, None], "read": [None, None]}
        self.counts = {"notes": 0, "reads": 0, "items_returned": 0}

    def _mark(self, phase: str, t0: float, t1: float) -> None:
        span = self.times[phase]
        span[0] = t0 if span[0] is None else span[0]
        span[1] = t1

    def hello(self) -> dict:
        return {"protocol": B.PROTOCOL, "system": "a-mem", "arm": ARM, "stage": "both",
                "version": self.rec["product"]["version"], "python": self.rec["python"],
                "llm_label": f"{self.spec['llm']}:{self.spec['llm_model']}", "embedder": self.spec["embed_tag"],
                "env_names": self.rec["env_names"], "start": self.rec}

    def _ef_ok(self) -> bool:
        return isinstance(self.ms.retriever.embedding_function, self.ef_class) and \
            all(n == self.ef_class.__name__ for n in self.ef_built)

    def write(self, item: dict, date: str | None = None) -> dict:
        if self.phase != "write":
            raise RuntimeError("a write after end_write: the write stage is closed (Q25)")
        t0 = time.time()
        content = f"Speaker {item['speaker']} says : {item['text']}"
        if self.spec["dated"] and not date:
            raise ValueError("a dated stand's write carries its date (time=)")
        try:
            self.ms.add_note(content, time=date if self.spec["dated"] else None)
        except Refused:
            raise
        except Exception as e:  # noqa: BLE001 - the expected DeepSeek block is named, anything else passes through
            text = str(e)
            if "json_schema" in text or "response_format" in text:
                raise Refused(f"blocked:structured-output - the writer's json_schema request was refused: {text[:200]}") \
                    from None
            raise
        t1 = time.time()
        self._mark("write", t0, t1)
        self.counts["notes"] += 1
        if not self._ef_ok():
            raise Refused("A-mem rebuilt its retriever without the v3 embedding function")
        return {"op_id": item["item_id"], "text_sha256": B.text_sha256(content), "t0": t0, "t1": t1}

    def end_write(self) -> dict:
        if self.phase != "write":
            raise RuntimeError("end_write twice")
        t0 = time.time()
        self.phase = "read"
        return {"footprint": {"retrievable": len(self.ms.memories)}, "store_persistence": "memory",
                "embedding_functions_built": list(self.ef_built), "ef_ok": self._ef_ok(), "t0": t0, "t1": time.time()}

    def read(self, qid: str, query: str, k: int, point: str | None = None) -> dict:
        if self.phase != "read":
            raise RuntimeError("a read before end_write: the write stage is still open (Q25)")
        if point not in POINTS:
            raise ValueError("point is K or B")
        t0 = time.time()
        hits = self.ms.search_agentic(query, int(k))
        texts = [(h.get("content") or "") + (f" (timestamp: {h.get('timestamp')})" if self.spec["dated"] else "")
                 for h in hits]
        if point == "K":
            texts = texts[:int(k)]
        items = [{"text": t, "rank": r} for r, t in enumerate(texts, 1)]
        t1 = time.time()
        self._mark("read", t0, t1)
        self.counts["reads"] += 1
        self.counts["items_returned"] += len(items)
        return {"qid": qid, "items": items, "items_returned": len(items), "k": int(k), "point": point, "t0": t0,
                "t1": t1}

    def counters(self) -> dict:
        snap = HC.snapshot()
        transport: dict = {}
        self.pacer.attach(transport)
        return {"http": snap, "llm_calls": HC.total(snap, "proxy:") + HC.total(snap, "ollama:generate"),
                **self.counts, "embedding_functions_built": len(self.ef_built), "ef_ok": self._ef_ok(),
                "stage_times": {k: list(v) for k, v in self.times.items()},
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
        _record(spec["record_path"], {"ok": False, "error": reason[:B.ERROR_MAX], "arm": ARM, "unit": spec["unit"]})
        return RefusedHandler(reason)
    _record(spec["record_path"], {"ok": True, **rec})
    return handler


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("usage: arm_amem.py <spec.json>\n")
        sys.exit(2)
    ENV_AT_START = dict(os.environ)
    sys.exit(B.main_with(lambda: build(sys.argv[1], ENV_AT_START)))
