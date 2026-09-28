#!/usr/bin/env python3
"""PREREG-V3 A8 C5 (the auditor's Q-C5-3): the loopback fakes the A8 probes run against - never a real model, never the
owner's Ollama store. Both are plain HTTP/1.1 on 127.0.0.1 (the proxy's test upstream and its Ollama leg's upstream),
and both record every request they get.

* FakeDeepSeek: POST /chat/completions or /v1/chat/completions. Call i (0-based) answers from a SCRIPT declared as data
  (entry min(i, last)): its content, and optional tool_calls (Letta), with a usage distinct per call - prompt 1000+i,
  completion 10+i - so every call's tokens can be told apart in the proxy's record. A request with "stream": true gets
  SSE (chunked; the content in one delta, the usage in the last chunk, then [DONE]); any other gets one JSON body. The
  script's sha256 is over its canonical JSON (sort_keys, no whitespace) and goes into probe.json (R-C5-3). Any other
  path is 404.
* FakeOllama: exactly the paths the proxy's leg forwards - /api/embed, /api/embeddings, /v1/embeddings, /api/tags,
  /api/show - answering with the declared tag's name and digest and 1024 dimensions (a vector derived from the text's
  sha256, the same text the same vector). /api/pull and /api/generate are TRAPS: recorded and answered 403. Any other
  path is recorded and 404; a model other than the tag is 404. M21 in A8 means "the route went through the /u/ leg";
  the real model's behaviour is A9's (Q-A8-7).
"""
from __future__ import annotations

import hashlib
import json
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping, Sequence

DIMS = 1024
OLLAMA_PATHS = ("/api/embed", "/api/embeddings", "/v1/embeddings", "/api/tags", "/api/show")
OLLAMA_TRAPS = ("/api/pull", "/api/generate")
CHAT_PATHS = ("/chat/completions", "/v1/chat/completions")


def canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def vector(text: str, dims: int = DIMS) -> list[float]:
    """A deterministic unit vector from the text's sha256 (the same text, the same vector)."""
    raw = b""
    i = 0
    while len(raw) < dims * 4:
        raw += hashlib.sha256(f"{i}\0{text}".encode("utf-8")).digest()
        i += 1
    vals = [(x / 0xFFFFFFFF) * 2 - 1 for x in struct.unpack(f"<{dims}I", raw[:dims * 4])]
    norm = sum(v * v for v in vals) ** 0.5 or 1.0
    return [round(v / norm, 7) for v in vals]


class _Server:
    """A threaded HTTP/1.1 server on 127.0.0.1 with its own handler class."""

    def __init__(self, handler: type) -> None:
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        owner = self

        class H(handler):  # type: ignore[misc, valid-type]
            fake = owner
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.host, self.port = self.httpd.server_address[:2]
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()

    def record(self, rec: dict) -> int:
        with self._lock:
            self.requests.append(rec)
            return len(self.requests) - 1

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    fake: Any = None

    def log_message(self, *a) -> None:              # quiet: the fake's own record is the log
        pass

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _json(self, status: int, obj: Any) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _ChatHandler(_Handler):
    def do_POST(self) -> None:                      # noqa: N802 - http.server's name
        body = self._body()
        path = self.path.split("?", 1)[0]
        try:
            obj = json.loads(body or b"{}")
        except ValueError:
            obj = None
        stream = bool(isinstance(obj, dict) and obj.get("stream") is True)
        i = self.fake.record({"path": path, "body": obj, "stream": stream,
                              "authorization": self.headers.get("Authorization")})
        if path not in CHAT_PATHS or not isinstance(obj, dict):
            self._json(404, {"error": {"message": f"the fake serves {list(CHAT_PATHS)} with a JSON body"}})
            return
        entry = self.fake.script[min(i, len(self.fake.script) - 1)]
        usage = {"prompt_tokens": 1000 + i, "completion_tokens": 10 + i, "total_tokens": 1010 + 2 * i}
        msg: dict = {"role": "assistant", "content": entry.get("content", "")}
        if entry.get("tool_calls"):
            msg["tool_calls"] = [dict(t) for t in entry["tool_calls"]]
        finish = "tool_calls" if entry.get("tool_calls") else "stop"
        base = {"id": f"fake-{i}", "created": int(time.time()), "model": obj.get("model") or "fake"}
        if not stream:
            self._json(200, {**base, "object": "chat.completion", "usage": usage,
                             "choices": [{"index": 0, "message": msg, "finish_reason": finish}]})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        delta = {k: v for k, v in msg.items()}
        chunks = [{**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {**base, "object": "chat.completion.chunk", "usage": usage,
                   "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
        for c in chunks:
            self._chunk(b"data: " + json.dumps(c).encode("utf-8") + b"\n\n")
        self._chunk(b"data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")

    def _chunk(self, data: bytes) -> None:
        self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))

    do_GET = do_POST


class FakeDeepSeek(_Server):
    """The fake DeepSeek upstream: the script is data, declared before the probe runs; call i answers entry min(i, last)."""

    def __init__(self, script: Sequence[Mapping]) -> None:
        if not script:
            raise ValueError("a fake upstream needs at least one scripted answer")
        self.script = [dict(e) for e in script]
        self.script_sha256 = canonical_sha256(self.script)
        super().__init__(_ChatHandler)


class _OllamaHandler(_Handler):
    def _serve(self, method: str) -> None:
        body = self._body()
        path = self.path.split("?", 1)[0]
        try:
            obj = json.loads(body) if body else {}
        except ValueError:
            obj = None
        f = self.fake
        f.record({"method": method, "path": path, "body": obj})
        if path in OLLAMA_TRAPS:
            with f._lock:
                f.traps.append({"method": method, "path": path})
            self._json(403, {"error": f"trap: {path} is not allowed in an A8 probe"})
            return
        if path not in OLLAMA_PATHS:
            with f._lock:
                f.unknown.append({"method": method, "path": path})
            self._json(404, {"error": f"the fake serves {list(OLLAMA_PATHS)} only"})
            return
        if path == "/api/tags":
            self._json(200, {"models": [{"name": f.tag, "model": f.tag, "digest": f.digest, "size": 0,
                                         "modified_at": "2026-09-28T00:00:00Z",
                                         "details": {"family": "bert", "format": "gguf"}}]})
            return
        names = {f.tag, f.tag.split(":", 1)[0]} if f.tag.endswith(":latest") else {f.tag}
        if not isinstance(obj, dict) or obj.get("model") not in names:
            self._json(404, {"error": f"model {obj.get('model') if isinstance(obj, dict) else None!r} not found"})
            return
        if path == "/api/show":
            self._json(200, {"details": {"family": "bert", "format": "gguf"}, "model_info": {"bert.embedding_length": f.dims},
                             "digest": f.digest})
            return
        if path == "/api/embeddings":
            text = obj.get("prompt") or ""
            self._json(200, {"embedding": vector(str(text), f.dims)})
            return
        raw = obj.get("input")
        inputs = [raw] if isinstance(raw, str) else list(raw or [])
        vecs = [vector(str(x), f.dims) for x in inputs]
        count = sum(len(str(x).split()) + 2 for x in inputs)
        if path == "/api/embed":
            self._json(200, {"model": f.tag, "embeddings": vecs, "total_duration": 0, "load_duration": 0,
                             "prompt_eval_count": count})
            return
        self._json(200, {"object": "list", "model": f.tag, "usage": {"prompt_tokens": count, "total_tokens": count},
                         "data": [{"object": "embedding", "index": k, "embedding": v} for k, v in enumerate(vecs)]})

    def do_POST(self) -> None:                      # noqa: N802
        self._serve("POST")

    def do_GET(self) -> None:                       # noqa: N802
        self._serve("GET")


class FakeOllama(_Server):
    """The fake Ollama: the declared tag and digest, DIMS dimensions; the traps are recorded in ``traps``."""

    def __init__(self, *, tag: str, digest: str, dims: int = DIMS) -> None:
        self.tag, self.digest, self.dims = tag, digest, dims
        self.traps: list[dict] = []
        self.unknown: list[dict] = []
        super().__init__(_OllamaHandler)
