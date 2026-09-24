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

import asyncio
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
        raw_body = self.rfile.read(length) if length else b""
        with self.server.lock:                           # type: ignore[attr-defined]
            counts = self.server.counts                  # type: ignore[attr-defined]
            counts[self.path] = counts.get(self.path, 0) + 1
        stream = False
        if raw_body:
            try:
                stream = bool(json.loads(raw_body).get("stream"))
            except (ValueError, TypeError, AttributeError):
                pass
        data = _response_body_for(self.path, stream)
        self.send_response(200)
        self.send_header("Content-Type",
                         "application/x-ndjson" if stream else "application/json")
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


def _ndjson_chunks(path: str) -> list:
    """A minimal, valid two-line NDJSON stream (one partial chunk, one done=true final) -
    real Ollama streams many more, but a client's own parsing only needs `done` to
    eventually read True, and this is still delivered as exactly ONE HTTP request/response
    (an `http.server` handler writes its whole body before returning either way), so
    server-vs-pacer symmetry is unaffected by how many logical chunks the body contains."""
    if path.startswith("/api/chat"):
        return [
            {"model": "probe-model", "created_at": "2026-01-01T00:00:00Z",
             "message": {"role": "assistant", "content": "ok "}, "done": False},
            {"model": "probe-model", "created_at": "2026-01-01T00:00:00Z",
             "message": {"role": "assistant", "content": ""}, "done": True,
             "done_reason": "stop", "total_duration": 1, "load_duration": 1,
             "prompt_eval_count": 1, "eval_count": 1},
        ]
    return [                                               # /api/generate
        {"model": "probe-model", "created_at": "2026-01-01T00:00:00Z",
         "response": "ok ", "done": False},
        {"model": "probe-model", "created_at": "2026-01-01T00:00:00Z",
         "response": "", "done": True, "total_duration": 1,
         "load_duration": 1, "prompt_eval_count": 1, "eval_count": 1},
    ]


def _response_body_for(path: str, stream: bool) -> bytes:
    if stream and (path.startswith("/api/chat") or path.startswith("/api/generate")):
        return b"\n".join(json.dumps(c).encode("utf-8") for c in _ndjson_chunks(path)) + b"\n"
    return json.dumps(_response_for(path)).encode("utf-8")


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
        # item 8 (.loop/HANDOFF-PORTS.md): the SAME six fields (response, done,
        # prompt_eval_count, eval_count, model, created_at) real Ollama's /api/generate
        # returns - `created_at` was missing here (present on /api/chat above the whole
        # time). Diagnostic finding (this commit): A-MEM's OllamaController
        # (litellm.completion(model="ollama_chat/...")) never calls /api/generate at all
        # - traced under the amem_eval venv with litellm.set_verbose=True, server.counts
        # showed /api/show x3 + /api/chat x1 per logical call, /api/generate x0 - so this
        # completeness fix does not change A-MEM's own call count (still 20 for n=5, not
        # 5); recorded so the shape is correct for whichever future stack DOES exercise
        # this endpoint (litellm's non-chat "ollama/" provider, `ollama.Client.generate`).
        return {"model": "probe-model", "created_at": "2026-01-01T00:00:00Z",
                "response": "ok", "done": True, "total_duration": 1, "load_duration": 1,
                "prompt_eval_count": 1, "eval_count": 1}
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


# ── generation stacks (the auditor's follow-up: embedding alone is not the whole surface) ─

def drive_ollama_python_chat_stream(port: int, n: int = N) -> dict:
    """Same client/transport as `drive_ollama_python`, but exercises STREAMING -
    `client.chat(..., stream=True)` returns a generator over NDJSON chunks. Still exactly
    ONE HTTP request/response per call (an `http.server` handler writes its whole body
    before returning), so server-vs-pacer symmetry is expected to hold identically -
    proving the pacer's per-CALL counting does not silently multiply or drop a count on a
    streamed body the way a per-CHUNK counter would."""
    try:
        import ollama                                      # noqa: PLC0415
    except ImportError as e:
        return {"skipped": f"ollama not importable in this interpreter ({e})"}
    client = ollama.Client(host=f"http://127.0.0.1:{port}")
    errors = 0
    for i in range(n):
        try:
            for _chunk in client.chat(
                    model="probe-model",
                    messages=[{"role": "user", "content": f"probe prompt {i}"}],
                    stream=True):
                pass
        except Exception:                                  # noqa: BLE001
            errors += 1
    return {"attempted": n, "errors": errors}


def drive_mem0_llm(port: int, n: int = N) -> dict:
    """mem0's OWN Ollama LLM (`mem0.llms.ollama.OllamaLLM`, separate from its embedder
    driven above) - `generate_response()` calls `ollama.Client.chat(...)`, hitting
    /api/chat non-streaming."""
    try:
        from mem0.llms.ollama import OllamaLLM               # noqa: PLC0415
        from mem0.configs.llms.ollama import OllamaConfig     # noqa: PLC0415
    except ImportError as e:
        return {"skipped": f"mem0 LLM not importable in this interpreter ({e})"}
    cfg = OllamaConfig(model="probe-model", ollama_base_url=f"http://127.0.0.1:{port}")
    try:
        llm = OllamaLLM(cfg)
    except Exception as e:                                 # noqa: BLE001
        return {"skipped": f"mem0 OllamaLLM init failed ({type(e).__name__}: {e})"}
    errors = 0
    for i in range(n):
        try:
            llm.generate_response([{"role": "user", "content": f"probe prompt {i}"}])
        except Exception:                                  # noqa: BLE001
            errors += 1
    return {"attempted": n, "errors": errors}


def drive_langchain_ollama_chat(port: int, n: int = N) -> dict:
    """langchain_ollama's ChatOllama (LangMem's chat model) - also `ollama.Client`/httpx
    underneath, same as its embeddings sibling driven above."""
    try:
        from langchain_ollama import ChatOllama               # noqa: PLC0415
    except ImportError as e:
        return {"skipped": f"langchain_ollama ChatOllama not importable in this "
                           f"interpreter ({e})"}
    try:
        chat = ChatOllama(model="probe-model", base_url=f"http://127.0.0.1:{port}")
    except Exception as e:                                 # noqa: BLE001
        return {"skipped": f"ChatOllama init failed ({type(e).__name__}: {e})"}
    errors = 0
    for i in range(n):
        try:
            chat.invoke(f"probe prompt {i}")
        except Exception:                                  # noqa: BLE001
            errors += 1
    return {"attempted": n, "errors": errors}


def drive_langchain_ollama_chat_async(port: int, n: int = N) -> dict:
    """item 8 (.loop/HANDOFF-PORTS.md): every OTHER stack in this probe drives its client
    SYNCHRONOUSLY - `_ollama_pacer.py` patches `httpx.AsyncClient.send`
    (`_paced_httpx_async_send`) exactly as it patches `httpx.Client.send`, and nothing
    here had ever exercised that code path with a REAL competitor client. `ChatOllama.
    ainvoke` - the same client (`langchain_ollama`, already exercised synchronously
    above) with its async entry point - resolves through `httpx.AsyncClient` under the
    hood, unlike `litellm.acompletion` (A-MEM's own stack), which would additionally
    reintroduce the /api/show model-info multiplier this probe's A-MEM line already
    covers; this keeps the async case isolated to the transport question T8b asks."""
    try:
        from langchain_ollama import ChatOllama               # noqa: PLC0415
    except ImportError as e:
        return {"skipped": f"langchain_ollama ChatOllama not importable in this "
                           f"interpreter ({e})"}
    try:
        chat = ChatOllama(model="probe-model", base_url=f"http://127.0.0.1:{port}")
    except Exception as e:                                 # noqa: BLE001
        return {"skipped": f"ChatOllama init failed ({type(e).__name__}: {e})"}
    errors = 0

    async def _run() -> None:
        nonlocal errors
        for i in range(n):
            try:
                await chat.ainvoke(f"probe prompt {i}")
            except Exception:                              # noqa: BLE001
                errors += 1
    asyncio.run(_run())
    return {"attempted": n, "errors": errors}


STACKS = {
    "engine_embed (urllib.request.urlopen)": drive_engine_embed,
    "ollama-python Client.embed (httpx)": drive_ollama_python,
    "mem0 OllamaEmbedding (ollama-python/httpx)": drive_mem0_embedder,
    "langchain_ollama OllamaEmbeddings (ollama-python/httpx)": drive_langchain_ollama,
    "A-MEM OllamaController (litellm/httpx)": drive_amem_llm,
    "ollama-python Client.chat stream=True (httpx)": drive_ollama_python_chat_stream,
    "mem0 OllamaLLM.generate_response (ollama-python/httpx)": drive_mem0_llm,
    "langchain_ollama ChatOllama.invoke (ollama-python/httpx)": drive_langchain_ollama_chat,
    "langchain_ollama ChatOllama.ainvoke (ollama-python/httpx, ASYNC)":
        drive_langchain_ollama_chat_async,
}


def run() -> dict:
    """R1 (the auditor's finding, 2026-09-24): this dev machine runs a REAL Ollama on the
    default 127.0.0.1:11434 at all times, and litellm 1.100.0's model-validation step
    sends /api/show to that DEFAULT host regardless of the `api_base` this probe
    configures it with (observed 8x in the real Ollama's own log during a probe run) - so
    the pacer's AGGREGATE `calls` counter can include real-host traffic this probe never
    intended to measure, silently inflating a stack's number into looking symmetric (or
    asymmetric) by coincidence rather than by what actually happened on THIS run's own
    fake server. `pacer.calls_by_host()` is used instead of the aggregate: the symmetry
    verdict compares `server_calls` (which by construction can ONLY be requests THIS
    fake server itself received) against the pacer's count for THAT EXACT host:port,
    never the total across every host it happened to recognise. Any calls counted for a
    DIFFERENT host (real 11434 chief among them) are reported separately, by name, as
    `other_hosts_leaked` - visible, never silently folded into the "symmetric" number
    either way."""
    server, port = start_fake_ollama()
    _point_env_at(port)
    fake_host_key = ("127.0.0.1", port)
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
            by_host = pacer.calls_by_host(since=snap)
            pacer_calls_fake_host = by_host.get(fake_host_key, 0)
            pacer_calls_total = out.get("ollama_transport", {}).get("calls", 0)
            other_hosts = {f"{h}:{p}": n for (h, p), n in by_host.items()
                           if (h, p) != fake_host_key}
            if "skipped" not in r:
                r["server_calls"] = server_calls
                r["pacer_calls"] = pacer_calls_fake_host      # THIS run's fake host ONLY
                r["pacer_calls_all_hosts"] = pacer_calls_total
                r["calls_by_host"] = {f"{h}:{p}": n for (h, p), n in by_host.items()}
                r["symmetric"] = server_calls == pacer_calls_fake_host
                if other_hosts:
                    r["other_hosts_leaked"] = other_hosts
            results[name] = r
    finally:
        pacer.uninstall()
        stop_fake_ollama(server)
    return results


#: The default Ollama host, always recognised by the pacer (research/_ollama_pacer.py's
#: own _DEFAULT_HOSTS) and, on this dev machine, always a REAL Ollama listening on it -
#: the exact host litellm 1.100.0's model-validation step leaked /api/show to, ignoring
#: the api_base this probe configured it with (the auditor's finding on 623a1df/ab82ff8).
REAL_OLLAMA_HOST_KEY = "127.0.0.1:11434"


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
    # R1 (the auditor): an explicit, asserted check that the REAL Ollama host received
    # ZERO pacer-counted calls during this run - not just per-stack visibility, a single
    # number this script's own exit code is gated on.
    real_host_calls = sum(r.get("other_hosts_leaked", {}).get(REAL_OLLAMA_HOST_KEY, 0)
                          for r in results.values() if "skipped" not in r)
    merged.setdefault("runs", {})[interpreter] = {
        "python": sys.version.split()[0], "platform": platform.platform(),
        "stacks": results,
        "real_ollama_host_pacer_calls": real_host_calls,
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
            if r.get("other_hosts_leaked"):
                print(f"  {'':56s} LEAKED to other host(s), excluded from the verdict "
                      f"above: {r['other_hosts_leaked']}")
    print(f"  real Ollama host ({REAL_OLLAMA_HOST_KEY}) pacer-counted calls during this "
          f"probe: {real_host_calls}" +
          ("  [CLEAN]" if real_host_calls == 0 else
           "  [LEAK - see other_hosts_leaked per stack above]"))
    print(f"  wrote -> {out_path}")
    print(bar)
    return 3 if real_host_calls > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
