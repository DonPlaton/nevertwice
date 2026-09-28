#!/usr/bin/env python3
"""PREREG-V3 TB4.6a (A6): the in-process arms' HTTP counter - reconciliation check 1 of rev1 §4.6 (K87), proxy vs adapter
at the HTTP level. It COUNTS and never changes a request (the auditor's Q-46-4).

Copied byte-identical beside each in-process adapter at block start, with its sha256 recorded (ruling Q9 O-b), so it is
stdlib-only; the clients it wraps (httpx, requests) are wrapped only when the product's venv has them.

    import _http_count as HC
    HC.install(proxy_port=41000)       # FIRST - then the pacer wraps these wrappers
    import _ollama_pacer; _ollama_pacer.install("observe")   # R-EMBED-PATH: the proxy leg paces

Installed before the pacer, the counter sits INSIDE it: each attempt the pacer makes, a retry included, is one request
here, as it is one request at the proxy. Four doors are wrapped: urllib.request.urlopen, httpx.Client.send,
httpx.AsyncClient.send and requests.Session.send. A transport that goes around all four is not counted - and the proxy
still sees it, so the K87 comparison (proxy_calls against adapter_calls) names the gap instead of hiding it.

Each request is counted under a class - "proxy" (the arm's own proxy port on loopback), "ollama" (the Ollama port on
loopback, 11434 or a declared one), "other" - and a route: chat / embeddings / other for the proxy, embed / generate /
tags / pull / other for Ollama. Attempts, responses and exceptions are counted apart: an attempt that never reached the
proxy is an adapter-side count the proxy cannot have.
"""
from __future__ import annotations

import threading
import urllib.request
from urllib.parse import urlsplit

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_LOCK = threading.Lock()
_COUNTS: dict[str, dict[str, int]] = {}
_CFG: dict = {"proxy_port": None, "ollama_ports": (11434,)}
_ORIG: dict = {}


def classify(url: str) -> str:
    """"<class>:<route>" for one request URL."""
    try:
        parts = urlsplit(str(url))
        host = (parts.hostname or "").lower()
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return "other:unparsable"
    path = parts.path or "/"
    if host in _LOOPBACK and _CFG["proxy_port"] is not None and port == _CFG["proxy_port"]:
        if path.endswith("/chat/completions") or path.endswith("/messages"):
            return "proxy:chat"
        if path.endswith("/embeddings"):
            return "proxy:embeddings"
        return "proxy:other"
    if host in _LOOPBACK and port in _CFG["ollama_ports"]:
        for route, ends in (("embed", ("/api/embed", "/api/embeddings", "/v1/embeddings")),
                            ("generate", ("/api/generate", "/api/chat", "/v1/chat/completions")),
                            ("tags", ("/api/tags",)), ("pull", ("/api/pull",))):
            if path.endswith(ends):
                return f"ollama:{route}"
        return "ollama:other"
    return "other:other"


def _bump(key: str, field: str) -> None:
    with _LOCK:
        row = _COUNTS.setdefault(key, {"attempts": 0, "responses": 0, "exceptions": 0})
        row[field] += 1


def _counted(call, url):
    key = classify(url)
    _bump(key, "attempts")
    try:
        out = call()
    except BaseException:
        _bump(key, "exceptions")
        raise
    _bump(key, "responses")
    return out


async def _counted_async(call, url):
    key = classify(url)
    _bump(key, "attempts")
    try:
        out = await call()
    except BaseException:
        _bump(key, "exceptions")
        raise
    _bump(key, "responses")
    return out


def installed() -> bool:
    return bool(_ORIG)


def install(*, proxy_port: int | None, ollama_ports=(11434,)) -> list[str]:
    """Wrap every door present in this venv; returns the doors wrapped. A second install is refused - it would count
    every request twice."""
    if _ORIG:
        raise RuntimeError("_http_count is installed already")
    _CFG["proxy_port"] = proxy_port
    _CFG["ollama_ports"] = tuple(int(p) for p in ollama_ports)
    doors = []

    orig_urlopen = urllib.request.urlopen
    _ORIG["urlopen"] = orig_urlopen

    def urlopen(url, *args, **kwargs):
        target = url.full_url if isinstance(url, urllib.request.Request) else url
        return _counted(lambda: orig_urlopen(url, *args, **kwargs), target)
    urllib.request.urlopen = urlopen
    doors.append("urllib.request.urlopen")

    try:
        import httpx  # noqa: PLC0415 - only when the venv has it
    except ImportError:
        httpx = None
    if httpx is not None:
        orig_send, orig_asend = httpx.Client.send, httpx.AsyncClient.send
        _ORIG["httpx_send"], _ORIG["httpx_asend"] = orig_send, orig_asend

        def send(self, request, *args, **kwargs):
            return _counted(lambda: orig_send(self, request, *args, **kwargs), request.url)

        async def asend(self, request, *args, **kwargs):
            return await _counted_async(lambda: orig_asend(self, request, *args, **kwargs), request.url)
        httpx.Client.send, httpx.AsyncClient.send = send, asend
        doors += ["httpx.Client.send", "httpx.AsyncClient.send"]

    try:
        import requests  # noqa: PLC0415
    except ImportError:
        requests = None
    if requests is not None:
        orig_rsend = requests.Session.send
        _ORIG["requests_send"] = orig_rsend

        def rsend(self, request, **kwargs):
            return _counted(lambda: orig_rsend(self, request, **kwargs), request.url)
        requests.Session.send = rsend
        doors.append("requests.Session.send")
    _ORIG["doors"] = tuple(doors)
    return doors


def snapshot() -> dict:
    """{"<class>:<route>": {attempts, responses, exceptions}} so far, and the doors wrapped."""
    with _LOCK:
        counts = {k: dict(v) for k, v in _COUNTS.items()}
    return {"counts": counts, "doors": list(_ORIG.get("doors", ()))}


def total(snap: dict, prefix: str, field: str = "attempts") -> int:
    """The sum of ``field`` over the keys that start with ``prefix`` (e.g. "proxy:" or "ollama:generate")."""
    return sum(v.get(field, 0) for k, v in (snap.get("counts") or {}).items() if k.startswith(prefix))
