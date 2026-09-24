#!/usr/bin/env python3
"""Empirical arm-symmetry probe (R1, the auditor's review of 717f482): does
`research/_ollama_pacer.py` actually see EVERY request a stack sends to Ollama, or does
some client's own HTTP layer bypass the two transports the pacer patches
(`urllib.request.urlopen`, `httpx.Client.send`/`httpx.AsyncClient.send`)?

A fake Ollama - a plain `http.server`, no real model weights, no real network - answers
every request with the minimal valid shape and counts requests received, by path. Each
stack is driven a FIXED number of times against it; the server's own count is then
compared with what the pacer itself recorded (`pacer.snapshot()["calls"]`, taken as a
delta around the drive). A mismatch means that stack talks to Ollama through something
this module does not patch - said by name, not assumed from reading the client's source.

This repository's own venv has none of the competitor packages installed (by design -
see `research/head_to_head.py`'s docstring); they live in the polygon venvs used for the
head-to-head comparisons. Each interpreter sees only the stacks its own site-packages can
import - a stack unavailable here is reported `"skipped"`, never silently dropped:

    python research/_ollama_symmetry_probe.py                                    # engine only
    D:/Coding/_nevertwice_polygon/mem0_eval/.venv/Scripts/python.exe \\
        research/_ollama_symmetry_probe.py                                       # + ollama-python, mem0, langchain_ollama
    D:/Coding/_nevertwice_polygon/amem_eval/.venv/Scripts/python.exe \\
        research/_ollama_symmetry_probe.py                                       # + A-MEM (litellm)

Writes `.loop/explore/ollama_symmetry.json`, MERGING this run's stacks (keyed by the
interpreter path) into whatever the file already holds - an exploration note the plan
asked for, not a registered claim: no `evidence_manifest.json` entry, no `produced_by`.
"""
from __future__ import annotations

import http.server
import json
import os
import platform
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(ROOT), str(HERE), str(ROOT / "nevertwice")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _ollama_pacer as pacer  # noqa: E402 - stdlib-only, importable from any interpreter
#: Armed at MODULE level (tools/check_sandbox.py's own rule): this file's engine_embed
#: stack imports `memory_hook`, which resolves the store the instant it is imported -
#: harmless here (a throwaway sandbox, not a live vault), but the lint cannot see that a
#: LATER, function-scoped `import memory_hook` (deferred so the other four stacks never
#: pay for it) is actually guarded unless the arming call itself is also at module level.
#: Cheap and side-effect-free for every OTHER stack, which never touches the engine at all.
import sandbox_guard  # noqa: E402
sandbox_guard.isolate(prefix="nevertwice_symmetry_")

N = 5                                       # calls driven per stack, fixed - not a sweep
#: Names the fake `/api/tags` reports as already pulled, so a client's own
#: "is the model present locally" check (mem0, ollama-python) never tries a real
#: `/api/pull` against this fake server.
MODEL_NAMES = ("probe-model", "bge-m3", "nomic-embed-text")


# ── the fake Ollama: counts requests by path, answers with the minimal valid shape ──────

class _CountingHandler(http.server.BaseHTTPRequestHandler):
    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)                     # drain the body; content unused
        with self.server.lock:                           # type: ignore[attr-defined]
            counts = self.server.counts                  # type: ignore[attr-defined]
            counts[self.path] = counts.get(self.path, 0) + 1
        payload = _response_for(self.path)
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, OSError):
            pass                                          # a client that closed early

    do_GET = _handle
    do_POST = _handle

    def log_message(self, fmt, *args) -> None:            # noqa: A002 - stdlib's own name
        pass                                               # silence per-request console spam


def _response_for(path: str) -> dict:
    """The minimal shape each Ollama endpoint's real response has, per client library -
    just enough that a caller's OWN parsing does not raise before the request is already
    counted (which is the only thing this probe measures)."""
    if path.startswith("/api/tags"):
        return {"models": [
            {"name": f"{n}:latest", "model": f"{n}:latest",
             "modified_at": "2026-01-01T00:00:00Z", "size": 1, "digest": "0" * 64,
             "details": {"parent_model": "", "format": "gguf", "family": "llama",
                        "families": ["llama"], "parameter_size": "1B",
                        "quantization_level": "Q4_0"}}
            for n in MODEL_NAMES]}
    if path.startswith("/api/show"):
        return {"modelfile": "", "parameters": "", "template": "", "details": {}}
    if path.startswith("/api/embed") or path.startswith("/api/embeddings"):
        vec = [0.1, 0.2, 0.3, 0.4]
        return {"model": "probe-model", "embeddings": [vec], "embedding": vec,
                "total_duration": 1, "load_duration": 1, "prompt_eval_count": 1}
    if path.startswith("/api/chat"):
        return {"model": "probe-model", "created_at": "2026-01-01T00:00:00Z",
                "message": {"role": "assistant", "content": "ok"}, "done": True,
                "done_reason": "stop", "total_duration": 1, "load_duration": 1,
                "prompt_eval_count": 1, "eval_count": 1}
    if path.startswith("/api/generate"):
        return {"model": "probe-model", "response": "ok", "done": True,
                "total_duration": 1, "load_duration": 1, "prompt_eval_count": 1,
                "eval_count": 1}
    return {"ok": True}


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_fake_ollama() -> tuple[_Server, int]:
    server = _Server(("127.0.0.1", 0), _CountingHandler)
    server.lock = threading.Lock()                        # type: ignore[attr-defined]
    server.counts = {}                                    # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def stop_fake_ollama(server: _Server) -> None:
    server.shutdown()
    server.server_close()


def _point_env_at(port: int) -> None:
    """Every env var ANY stack (ours or a competitor's) reads for its Ollama base URL,
    all pointed at the same fake server - so `pacer.is_ollama_host()` (which reads these
    same names) recognises this random port as Ollama regardless of which one a given
    client actually constructs its request from."""
    base = f"http://127.0.0.1:{port}"
    os.environ["OLLAMA_URL"] = f"{base}/api/generate"
    os.environ["OLLAMA_TAGS_URL"] = f"{base}/api/tags"
    os.environ["OLLAMA_EMBED_URL"] = f"{base}/api/embed"
    os.environ["OLLAMA_BASE_URL"] = base
    os.environ["OLLAMA_HOST"] = base
    os.environ["OLLAMA_API_BASE"] = base                  # litellm's ollama/ollama_chat providers


# ── stacks - each returns {"skipped": reason} or {"attempted", "errors"} ───────────────

def drive_engine_embed(port: int, n: int = N) -> dict:
    try:
        import memory_hook as m                           # noqa: PLC0415 - module already armed
    except ImportError as e:
        return {"skipped": f"engine not importable in this interpreter ({e})"}
    m.EMBED_PROVIDER = "ollama"
    m.OLLAMA_EMBED_URL = f"http://127.0.0.1:{port}/api/embed"
    m._EMBED_TEXT_MEMO = {"key": None, "vec": None}
    errors = 0
    for i in range(n):
        try:
            m.embed_text(f"probe text {i} {time.time()}")
        except Exception:                                  # noqa: BLE001 - counted, reported
            errors += 1
    return {"attempted": n, "errors": errors}


def drive_ollama_python(port: int, n: int = N) -> dict:
    try:
        import ollama                                      # noqa: PLC0415
    except ImportError as e:
        return {"skipped": f"ollama not importable in this interpreter ({e})"}
    client = ollama.Client(host=f"http://127.0.0.1:{port}")
    errors = 0
    for i in range(n):
        try:
            client.embed(model="probe-model", input=f"probe text {i}")
        except Exception:                                  # noqa: BLE001
            errors += 1
    return {"attempted": n, "errors": errors}


def drive_mem0_embedder(port: int, n: int = N) -> dict:
    try:
        from mem0.embeddings.ollama import OllamaEmbedding  # noqa: PLC0415
        from mem0.configs.embeddings.base import BaseEmbedderConfig  # noqa: PLC0415
    except ImportError as e:
        return {"skipped": f"mem0 not importable in this interpreter ({e})"}
    cfg = BaseEmbedderConfig(model="probe-model", ollama_base_url=f"http://127.0.0.1:{port}")
    try:
        emb = OllamaEmbedding(cfg)
    except Exception as e:                                 # noqa: BLE001
        return {"skipped": f"mem0 OllamaEmbedding init failed ({type(e).__name__}: {e})"}
    errors = 0
    for i in range(n):
        try:
            emb.embed(f"probe text {i}")
        except Exception:                                  # noqa: BLE001
            errors += 1
    return {"attempted": n, "errors": errors}


def drive_langchain_ollama(port: int, n: int = N) -> dict:
    try:
        from langchain_ollama import OllamaEmbeddings       # noqa: PLC0415
    except ImportError as e:
        return {"skipped": f"langchain_ollama not importable in this interpreter ({e})"}
    try:
        emb = OllamaEmbeddings(model="probe-model", base_url=f"http://127.0.0.1:{port}")
    except Exception as e:                                 # noqa: BLE001
        return {"skipped": f"OllamaEmbeddings init failed ({type(e).__name__}: {e})"}
    errors = 0
    for i in range(n):
        try:
            emb.embed_query(f"probe text {i}")
        except Exception:                                  # noqa: BLE001
            errors += 1
    return {"attempted": n, "errors": errors}


def drive_amem_llm(port: int, n: int = N) -> dict:
    """A-MEM's Ollama backend (`agentic_memory.llm_controller.OllamaController`) does not
    call the `ollama` package's client at all for completions - it calls
    `litellm.completion(model="ollama_chat/...")`, catching every exception and returning
    an empty schema on failure. The REQUEST still reaches the server before any response
    parsing happens, so a parsing failure here does not undercount `server_calls` - only
    `errors` (this probe's own bookkeeping, not a measure of arm symmetry)."""
    try:
        from agentic_memory.llm_controller import OllamaController  # noqa: PLC0415
    except ImportError as e:
        return {"skipped": f"agentic_memory not importable in this interpreter ({e})"}
    try:
        ctrl = OllamaController(model="probe-model")
    except Exception as e:                                 # noqa: BLE001
        return {"skipped": f"OllamaController init failed ({type(e).__name__}: {e})"}
    errors = 0
    schema = {"json_schema": {"schema": {"type": "object", "properties": {}}}}
    for i in range(n):
        try:
            ctrl.get_completion(f"probe prompt {i}", schema)
        except Exception:                                  # noqa: BLE001
            errors += 1
    return {"attempted": n, "errors": errors}


STACKS = {
    "engine_embed (urllib.request.urlopen)": drive_engine_embed,
    "ollama-python Client.embed (httpx)": drive_ollama_python,
    "mem0 OllamaEmbedding (ollama-python/httpx)": drive_mem0_embedder,
    "langchain_ollama OllamaEmbeddings (ollama-python/httpx)": drive_langchain_ollama,
    "A-MEM OllamaController (litellm/httpx)": drive_amem_llm,
}


def run() -> dict:
    server, port = start_fake_ollama()
    _point_env_at(port)
    pacer.install()
    results: dict = {}
    try:
        for name, fn in STACKS.items():
            with server.lock:                              # type: ignore[attr-defined]
                before_total = sum(server.counts.values())  # type: ignore[attr-defined]
            snap = pacer.snapshot()
            r = fn(port)
            with server.lock:                              # type: ignore[attr-defined]
                after_total = sum(server.counts.values())   # type: ignore[attr-defined]
            server_calls = after_total - before_total
            out: dict = {}
            pacer.attach(out, since=snap)
            pacer_calls = out.get("ollama_transport", {}).get("calls", 0)
            if "skipped" not in r:
                r["server_calls"] = server_calls
                r["pacer_calls"] = pacer_calls
                r["symmetric"] = server_calls == pacer_calls
            results[name] = r
    finally:
        pacer.uninstall()
        stop_fake_ollama(server)
    return results


def main() -> int:
    results = run()
    out_path = ROOT / ".loop" / "explore" / "ollama_symmetry.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged: dict = {}
    if out_path.exists():
        try:
            merged = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            merged = {}
    interpreter = sys.executable
    merged.setdefault("runs", {})[interpreter] = {
        "python": sys.version.split()[0], "platform": platform.platform(),
        "stacks": results,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out_path.write_text(json.dumps(merged, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")

    bar = "=" * 78
    print(bar)
    print(f"  arm symmetry probe (R1) - {interpreter}")
    print(bar)
    for name, r in results.items():
        if "skipped" in r:
            print(f"  {name:56s} SKIPPED: {r['skipped']}")
        else:
            verdict = "OK" if r["symmetric"] else "MISMATCH - BYPASSES THE PACER"
            print(f"  {name:56s} server={r['server_calls']:3d}  pacer={r['pacer_calls']:3d}"
                  f"  errors={r['errors']}  {verdict}")
    print(f"  wrote -> {out_path}")
    print(bar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
