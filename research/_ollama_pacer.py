"""Pace and retry the research tier's traffic to a LOCAL Ollama, transparently.

R-v2-ports (plan by planner, 2026-09-24). The problem is Windows' own ephemeral-port
budget: a campaign that opens thousands of short HTTP connections to 127.0.0.1:11434 a
minute can exhaust the dynamic port range faster than TIME_WAIT drains it, and Windows
answers a connect it cannot service with an HTTP 400 whose BODY names the reason
("Only one usage of each socket address is normally permitted" / "...sufficient buffer
space is normally required...") or with a raw connect failure (WinError 10048/10055).
Nothing upstream can tell that apart from a real 400 (`nevertwice/_engine_store.py`'s
`_embed_http` returns None either way, `research/head_to_head.py:603` and
`research/longmem_eval.py:121` see only "HTTP Error 400: Bad Request") - so a stand that
should have measured something reads a wall of silent embedder failures instead.

The fix is at the TRANSPORT, below every caller: `install()` patches
`urllib.request.urlopen` (every engine and research HTTP call resolves this attribute at
call time, never binds it at import - F2 of the plan) and, when the package is present,
`httpx.Client.send` / `httpx.AsyncClient.send` (the transport a competitor's own client -
`ollama-python`, `langchain-ollama` - is built on). Every call THROUGH this module to
something recognised as the Ollama host is paced to a floor gap (`NEVERTWICE_OLLAMA_PACE_S`,
default 0.125 s - the derivation is in the plan) and, on exactly the port-exhaustion
signature above, retried in place with the SAME request object, 15 s apart, up to 16 times
(240 s = 2x Windows' default TIME_WAIT) before the original error is allowed through. A
non-port HTTPError is untouched: read once for classification and its body restored, so a
caller that reads it again (as `_embed_http`'s own except-block does) still can.

Nothing here touches `nevertwice/` or `research/_provenance.py`. A wired stand calls
`install()` once, runs, and calls `attach(out)` (or `attach(out, since=snapshot())` for a
per-arm delta - `research/head_to_head.py` runs several arms in one process) to record what
this module actually did as `out["ollama_transport"]`. A run that never dialled the Ollama
host through this module writes nothing: `ollama_transport` presence is itself evidence
that traffic passed through here.

    import _ollama_pacer as pacer
    pacer.install()
    ...
    pacer.attach(out)               # out["ollama_transport"] = {...}, only if calls > 0

Out of scope (recorded, not fixed here): `.loop/`/`explore` probes, `research/embed_universal/*`,
`invariants_lab/*` never import this (R2); aiohttp transports - litellm on aiohttp, Graphiti's
AsyncOpenAI client - are not patched (R3); this is a PER-PROCESS pacer, so two campaigns racing
each other are only caught when each one's OWN stand starts (R5); localized (non-English)
netsh/PowerShell output is `_port_budget.py`'s concern, not this module's (R4).
"""
from __future__ import annotations

import asyncio
import contextvars
import io
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

#: Overridable so a stand (or a test) can dial the ceiling without an environment
#: variable - `NEVERTWICE_OLLAMA_PACE_S` is read once, at import, exactly like every
#: other numeric knob this tree reads through `env_int`/`os.environ.get`.
PACE_S = float(os.environ.get("NEVERTWICE_OLLAMA_PACE_S", "0.125"))
#: 240 s total (2x Windows' default 120 s TIME_WAIT) at 15 s apart - the plan's own
#: arithmetic, not chosen here.
RETRY_INTERVAL_S = 15.0
MAX_RETRIES = 16

#: The two Windows socket messages this module treats as "the port budget, not the
#: endpoint, refused this call". Matched as an exact phrase, never a bare substring like
#: "buffer" - a model that legitimately refuses a too-long prompt also says "buffer" in
#: some driver messages, and a bare match would retry a request that can never succeed.
SOCKET_ADDR_MSG = "Only one usage of each socket address"
BUFFER_SPACE_MSG = "sufficient buffer space"
#: WSAEADDRINUSE, WSAENOBUFS. WSAECONNREFUSED (10061, "nobody is listening") is a REAL
#: failure - Ollama is down - and must never be retried as if it were a port problem.
PORT_WINERRORS = (10048, 10055)

#: The Ollama host(s) this process will pace/retry against. Always includes the engine's
#: and the research tier's own defaults (they differ: `127.0.0.1` vs `localhost`), plus
#: whatever `OLLAMA_URL`/`OLLAMA_TAGS_URL`/`OLLAMA_EMBED_URL`/`OLLAMA_BASE_URL`/`OLLAMA_HOST`
#: names - read fresh on every call (not baked at import) so a test that sets one of these
#: mid-run is honoured, the same freedom `research/head_to_head.py` already relies on by
#: reading `OLLAMA_BASE_URL` at its own import time.
_DEFAULT_HOSTS = frozenset({("127.0.0.1", 11434), ("localhost", 11434), ("::1", 11434)})
_HOST_ENV_VARS = ("OLLAMA_URL", "OLLAMA_TAGS_URL", "OLLAMA_EMBED_URL", "OLLAMA_BASE_URL",
                  "OLLAMA_HOST")

# ── overridable clock/sleep seams (fake-clock tests never sleep for real) ───────────────
_now: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep
_async_sleep: Callable[[float], "asyncio.Future"] = asyncio.sleep

_LOCK = threading.Lock()
_INSTALLED = False
_ORIG: dict = {}
_PACE_STATE = {"next_at": float("-inf")}
_COUNTERS = {"calls": 0, "pace_sleep_s": 0.0, "retries": 0, "retry_sleep_s": 0.0, "gave_up": 0,
            "bypass_requests": 0, "bypass_aiohttp": 0,
            "nested_requests": 0, "nested_aiohttp": 0}
_CALL_MS: list = []

#: K11 (the auditor's finding on 623a1df): litellm 1.100.0's ASYNC path (amem_eval's own
#: dependency) routes its httpx.AsyncClient through a custom transport backed by aiohttp
#: (`LiteLLMAiohttpTransport`) - so a call already paced through `_paced_httpx_async_send`
#: reaches `aiohttp.ClientSession._request` a SECOND time, INSIDE that same paced send,
#: and the tripwire counted it as an independent bypass (attach() then said `valid: False`
#: on a call that was, in fact, paced). Set True for the exact duration of the underlying
#: `orig(...)` call in the urllib/httpx paced paths (never around the pacing sleep or the
#: retry loop itself, so a concurrent, genuinely independent task's own call - which could
#: run while THIS call's asyncio sleep yields control - is never mistaken for "nested
#: inside this one"). `contextvars.ContextVar` propagates through nested `await`s in the
#: SAME coroutine chain but each `asyncio.Task` gets its own copy at creation, which is
#: exactly the boundary "nested inside this specific paced call" needs.
_PACED_CONTEXT: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_nevertwice_pacer_in_paced_call", default=False)


def _mark_paced(fn):
    """Run the zero-arg `fn` with `_PACED_CONTEXT` True for its exact duration."""
    token = _PACED_CONTEXT.set(True)
    try:
        return fn()
    finally:
        _PACED_CONTEXT.reset(token)


async def _mark_paced_async(coro_fn):
    """Async counterpart of `_mark_paced`: `coro_fn` is a zero-arg callable returning an
    awaitable."""
    token = _PACED_CONTEXT.set(True)
    try:
        return await coro_fn()
    finally:
        _PACED_CONTEXT.reset(token)


# ── host recognition ─────────────────────────────────────────────────────────────────

def _host_port(url: str) -> tuple[str, int]:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return host, port


def _ollama_hosts() -> frozenset:
    hosts = set(_DEFAULT_HOSTS)
    for var in _HOST_ENV_VARS:
        val = os.environ.get(var)
        if not val:
            continue
        if "://" not in val:
            val = f"http://{val}"
        h, p = _host_port(val)
        if h:
            hosts.add((h, p))
    return frozenset(hosts)


def is_ollama_host(host: str, port: int) -> bool:
    """True iff (host, port) is one this process recognises as the local Ollama - the
    ONLY thing pacing/retry ever acts on (R1: everything else passes straight through)."""
    return ((host or "").lower(), port) in _ollama_hosts()


# ── classification: the one signature this module ever retries ─────────────────────────

def _winerror_of(obj) -> int | None:
    """`obj`'s own `.winerror`, or its wrapped cause's - httpx wraps a raw `OSError` connect
    failure inside `httpx.ConnectError`, and the winerror only survives on the inner one."""
    for candidate in (obj, getattr(obj, "__cause__", None), getattr(obj, "__context__", None)):
        w = getattr(candidate, "winerror", None)
        if w is not None:
            return w
    return None


def _read_and_restore_body(exc) -> str:
    """Read an `.read()`-able exception's body exactly once, then put it back so a
    caller upstream (e.g. `_embed_http`'s own `except urllib.error.HTTPError` block) can
    still read it - a stream is consumable once, and this module is not the last reader.

    `urllib.error.HTTPError` is an `addinfourl` over `tempfile._TemporaryFileWrapper`,
    which delegates `.read`/`.readline`/`.readlines` through `__getattr__` and then
    CACHES each as a plain instance attribute bound to the file object current at that
    first access (`tempfile.py`'s own `__getattr__`). Re-pointing `exc.fp` alone is not
    enough - `.fp` is kept only "as this was part of the original API" and nothing reads
    it back; the real target is `.file`, and any already-cached bound method has to be
    evicted or it keeps calling the OLD (now exhausted) file regardless of what `.file`
    is reassigned to."""
    try:
        raw = exc.read()
    except Exception:                                                # noqa: BLE001
        return ""
    try:
        fresh = io.BytesIO(raw)
        exc.fp = fresh
        exc.file = fresh
        for attr in ("read", "readline", "readlines"):
            exc.__dict__.pop(attr, None)
    except Exception:                                                # noqa: BLE001
        pass
    try:
        return raw.decode("utf-8", "replace")
    except Exception:                                                # noqa: BLE001
        return ""


def _body_of(obj) -> str:
    """Duck-typed across every shape this module retries underneath: an
    `ollama.ResponseError`-shaped object (`.error`, a plain string - matched without
    importing the real `ollama` package, so this works whether or not it is installed),
    an `httpx.Response` (`.text`), or a raw `urllib.error.HTTPError` (`.read()`)."""
    err = getattr(obj, "error", None)
    if isinstance(err, str):
        return err
    if hasattr(obj, "text"):
        try:
            return obj.text or ""
        except Exception:                                            # noqa: BLE001
            return ""
    if hasattr(obj, "read"):
        return _read_and_restore_body(obj)
    return ""


def _is_port_exhaustion_text(body: str) -> bool:
    return bool(body) and (SOCKET_ADDR_MSG in body or BUFFER_SPACE_MSG in body)


def classify(obj, *, is_ollama_host: bool) -> bool:
    """True iff `obj` (an exception or a response, whichever this module caught) is the
    ONE thing worth retrying: a connect failure with WinError 10048/10055, or an HTTP 400
    whose body is the port-exhaustion message above - AND the call was to the Ollama host
    (the caller already knows this from the URL it dialled; never re-derived here)."""
    if not is_ollama_host:
        return False
    winerror = _winerror_of(obj)
    if winerror is not None:
        return winerror in PORT_WINERRORS
    status = getattr(obj, "code", None)
    if status is None:
        status = getattr(obj, "status_code", None)
    if status != 400:
        return False
    return _is_port_exhaustion_text(_body_of(obj))


# ── pacing: one process-wide schedule, reserved under the lock, slept outside it ────────

def _reserve_slot() -> float:
    with _LOCK:
        now = _now()
        start = _PACE_STATE["next_at"]
        if start < now:
            start = now
        _PACE_STATE["next_at"] = start + PACE_S
    return max(0.0, start - now)


def _pace() -> None:
    wait = _reserve_slot()
    if wait > 0:
        _sleep(wait)
        with _LOCK:
            _COUNTERS["pace_sleep_s"] += wait


async def _pace_async() -> None:
    wait = _reserve_slot()
    if wait > 0:
        await _async_sleep(wait)
        with _LOCK:
            _COUNTERS["pace_sleep_s"] += wait


def _percentiles(samples: list) -> dict:
    if not samples:
        return {"n": 0, "p50": 0.0, "p90": 0.0, "p99": 0.0, "max": 0.0}
    s = sorted(samples)

    def pct(p):
        k = min(len(s) - 1, int(round(p * (len(s) - 1))))
        return round(s[k], 3)
    return {"n": len(s), "p50": pct(0.50), "p90": pct(0.90), "p99": pct(0.99),
            "max": round(s[-1], 3)}


# ── the retry driver: sync and async, identical policy, different sleep primitive ──────

def _run_paced(call: Callable):
    """`call()` to the Ollama host, paced and retried in place. `call` raises on any
    failure worth classifying (a plain function return is success); `call_ms` records
    only a SUCCESSFUL attempt's wall time, never a failed attempt's, and never a sleep -
    the timing window opens after `_pace()` has already returned."""
    _pace()
    with _LOCK:
        _COUNTERS["calls"] += 1
    attempt = 0
    while True:
        t0 = _now()
        try:
            result = call()
        except BaseException as exc:                                  # noqa: BLE001
            retry = classify(exc, is_ollama_host=True)
            if retry and attempt < MAX_RETRIES:
                attempt += 1
                with _LOCK:
                    _COUNTERS["retries"] += 1
                _sleep(RETRY_INTERVAL_S)
                with _LOCK:
                    _COUNTERS["retry_sleep_s"] += RETRY_INTERVAL_S
                continue
            if retry:
                with _LOCK:
                    _COUNTERS["gave_up"] += 1
            raise
        else:
            with _LOCK:
                _CALL_MS.append((_now() - t0) * 1000.0)
            return result


async def _run_paced_async(call: Callable):
    await _pace_async()
    with _LOCK:
        _COUNTERS["calls"] += 1
    attempt = 0
    while True:
        t0 = _now()
        try:
            result = await call()
        except BaseException as exc:                                  # noqa: BLE001
            retry = classify(exc, is_ollama_host=True)
            if retry and attempt < MAX_RETRIES:
                attempt += 1
                with _LOCK:
                    _COUNTERS["retries"] += 1
                await _async_sleep(RETRY_INTERVAL_S)
                with _LOCK:
                    _COUNTERS["retry_sleep_s"] += RETRY_INTERVAL_S
                continue
            if retry:
                with _LOCK:
                    _COUNTERS["gave_up"] += 1
            raise
        else:
            with _LOCK:
                _CALL_MS.append((_now() - t0) * 1000.0)
            return result


# ── urllib.request.urlopen ───────────────────────────────────────────────────────────

def _paced_urlopen(*args, **kwargs):
    req = args[0] if args else kwargs.get("url")
    url = req.full_url if hasattr(req, "full_url") else str(req)
    host, port = _host_port(url)
    orig = _ORIG["urlopen"]
    if not is_ollama_host(host, port):
        return orig(*args, **kwargs)
    return _run_paced(lambda: _mark_paced(lambda: orig(*args, **kwargs)))


# ── httpx.Client.send / httpx.AsyncClient.send ───────────────────────────────────────

class _PortExhaustionResponse(Exception):
    """Raised internally to route an httpx 400 RESPONSE (httpx does not raise on a non-2xx
    status by itself) through the same exception-driven retry loop urllib's HTTPError
    already uses. Carries the same `.status_code`/`.text` shape `classify()` reads, so
    the retry decision is made by the identical code path either way - never a second,
    drifting copy of the port-exhaustion check. `.response` is the ORIGINAL httpx.Response,
    handed back to the caller (never raised further) once retries are exhausted, restoring
    httpx's own contract: `Client.send` returns a Response, it does not raise on a 400."""

    def __init__(self, response):
        self.response = response
        self.status_code = response.status_code
        self.text = response.text
        super().__init__(f"ollama port exhaustion: HTTP {response.status_code}")


def _classify_httpx_response(response, *, stream: bool) -> bool:
    if response.status_code != 400:
        return False
    if stream:
        # A streamed response's body is read LAZILY by the caller; reading it here to
        # classify it would consume the stream out from under a legitimate caller (R3 -
        # httpx's own contract, not a port-exhaustion question). Streamed 400s are paced
        # but never retried on body content; a connect failure (below, exception-driven)
        # is still caught regardless of streaming, since it never produced a response.
        return False
    return classify(response, is_ollama_host=True)


def _paced_httpx_send(self, request, **kwargs):
    import httpx  # noqa: PLC0415 - only reachable when httpx installed this got patched
    host, port = (request.url.host or "").lower(), (
        request.url.port or (443 if request.url.scheme == "https" else 80))
    orig = _ORIG["httpx_send"]
    if not is_ollama_host(host, port):
        return orig(self, request, **kwargs)
    stream = bool(kwargs.get("stream", False))

    def _attempt():
        response = _mark_paced(lambda: orig(self, request, **kwargs))
        if _classify_httpx_response(response, stream=stream):
            raise _PortExhaustionResponse(response)
        return response
    try:
        return _run_paced(_attempt)
    except _PortExhaustionResponse as marker:
        return marker.response          # retries exhausted; hand back the last 400 as-is
    except httpx.HTTPError:
        raise


async def _paced_httpx_async_send(self, request, **kwargs):
    host, port = (request.url.host or "").lower(), (
        request.url.port or (443 if request.url.scheme == "https" else 80))
    orig = _ORIG["httpx_async_send"]
    if not is_ollama_host(host, port):
        return await orig(self, request, **kwargs)
    stream = bool(kwargs.get("stream", False))

    async def _attempt():
        response = await _mark_paced_async(lambda: orig(self, request, **kwargs))
        if _classify_httpx_response(response, stream=stream):
            raise _PortExhaustionResponse(response)
        return response
    try:
        return await _run_paced_async(_attempt)
    except _PortExhaustionResponse as marker:
        return marker.response


# ── tripwire: requests / aiohttp - COUNT-ONLY, never paced or retried ──────────────────
#
# R1 (the auditor's review): "symmetry becomes a property of every RUN", not just this
# one-off probe (research/_ollama_symmetry_probe.py). `requests` and `aiohttp` are NOT
# safe to pace/retry the way urllib and httpx are here - a `requests.PreparedRequest`'s
# body may already be a consumed stream, and re-sending it silently would not be the same
# request a second time, unlike the urllib/httpx paths where the caller's own object is
# reused verbatim (T2). So neither is retried; both are only COUNTED when the destination
# is the Ollama host, and `attach()` marks the arm's record `"valid": False` if either
# BYPASS count is nonzero - a stand that reaches Ollama through one of these bypassed the
# pacer entirely, and a paced-looking number that never was is worse than an honest
# refusal.
#
# K11: a hit is a bypass ONLY when `_PACED_CONTEXT` is False - a hit while it is True is
# NESTED inside a call this module ALREADY paced (litellm 1.100.0's async path routes its
# httpx.AsyncClient through a custom aiohttp-backed transport, so `_paced_httpx_async_send`
# calls `orig(...)`, which calls `aiohttp.ClientSession._request` a second time, INSIDE the
# same paced send - the auditor's finding on 623a1df: this counted as an independent
# bypass and marked a correctly-paced call `invalid`). Counted separately as
# `nested_requests`/`nested_aiohttp` - informational, never affects `valid`.

def _paced_requests_send(self, request, **kwargs):
    orig = _ORIG["requests_send"]
    host, port = _host_port(str(getattr(request, "url", "") or ""))
    if is_ollama_host(host, port):
        with _LOCK:
            if _PACED_CONTEXT.get():
                _COUNTERS["nested_requests"] += 1
            else:
                _COUNTERS["bypass_requests"] += 1
    return orig(self, request, **kwargs)


async def _paced_aiohttp_request(self, method, str_or_url, **kwargs):
    orig = _ORIG["aiohttp_request"]
    host, port = _host_port(str(str_or_url))
    if is_ollama_host(host, port):
        with _LOCK:
            if _PACED_CONTEXT.get():
                _COUNTERS["nested_aiohttp"] += 1
            else:
                _COUNTERS["bypass_aiohttp"] += 1
    return await orig(self, method, str_or_url, **kwargs)


# ── install / uninstall (idempotent) ─────────────────────────────────────────────────

def install() -> None:
    """Patch `urllib.request.urlopen` and, when importable, `httpx.Client.send` /
    `httpx.AsyncClient.send` (paced and retried), plus `requests.Session.send` /
    `aiohttp.ClientSession._request` (the tripwire - counted only, never paced or
    retried). A second call while already installed is a no-op - it does NOT re-capture
    `_ORIG` (which would point the "original" at THIS module's own wrapper) and does NOT
    reset counters (a stand may call `install()` defensively more than once in one
    process)."""
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return
        _ORIG["urlopen"] = urllib.request.urlopen
        urllib.request.urlopen = _paced_urlopen
        try:
            import httpx
        except ImportError:
            httpx = None
        if httpx is not None:
            _ORIG["httpx_send"] = httpx.Client.send
            _ORIG["httpx_async_send"] = httpx.AsyncClient.send
            httpx.Client.send = _paced_httpx_send
            httpx.AsyncClient.send = _paced_httpx_async_send
        try:
            import requests
        except ImportError:
            requests = None
        if requests is not None:
            _ORIG["requests_send"] = requests.Session.send
            requests.Session.send = _paced_requests_send
        try:
            import aiohttp
        except ImportError:
            aiohttp = None
        if aiohttp is not None:
            _ORIG["aiohttp_request"] = aiohttp.ClientSession._request
            aiohttp.ClientSession._request = _paced_aiohttp_request
        _INSTALLED = True


def uninstall() -> None:
    """Restore whatever `install()` saved. A no-op when not installed."""
    global _INSTALLED
    with _LOCK:
        if not _INSTALLED:
            return
        urllib.request.urlopen = _ORIG.pop("urlopen")
        if "httpx_send" in _ORIG:
            import httpx  # noqa: PLC0415 - only present if install() found it importable
            httpx.Client.send = _ORIG.pop("httpx_send")
            httpx.AsyncClient.send = _ORIG.pop("httpx_async_send")
        if "requests_send" in _ORIG:
            import requests  # noqa: PLC0415
            requests.Session.send = _ORIG.pop("requests_send")
        if "aiohttp_request" in _ORIG:
            import aiohttp  # noqa: PLC0415
            aiohttp.ClientSession._request = _ORIG.pop("aiohttp_request")
        _INSTALLED = False


def installed() -> bool:
    return _INSTALLED


# ── counters: snapshot / delta / attach ──────────────────────────────────────────────

def snapshot() -> dict:
    """A cheap, opaque marker of this process's counters right now - pass it back to
    `attach(out, since=...)` later for a DELTA (one arm's own share of a multi-arm run,
    e.g. `research/head_to_head.py`, which installs once and runs several competitors in
    one process - A4 of the plan)."""
    with _LOCK:
        return {"calls": _COUNTERS["calls"], "pace_sleep_s": _COUNTERS["pace_sleep_s"],
                "retries": _COUNTERS["retries"], "retry_sleep_s": _COUNTERS["retry_sleep_s"],
                "gave_up": _COUNTERS["gave_up"],
                "bypass_requests": _COUNTERS["bypass_requests"],
                "bypass_aiohttp": _COUNTERS["bypass_aiohttp"],
                "nested_requests": _COUNTERS["nested_requests"],
                "nested_aiohttp": _COUNTERS["nested_aiohttp"],
                "_call_ms_len": len(_CALL_MS)}


def attach(out: dict, *, since: dict | None = None) -> None:
    """Write `out["ollama_transport"]` with this process's counters (or, with `since`, the
    DELTA against an earlier `snapshot()`). Writes NOTHING when the span covers zero paced
    calls AND zero tripwire hits - `install()` with no traffic (an LLM-only arm, `--dry`,
    a run that never reached this module) must not claim a transport it never used, and a
    reader can treat the KEY's presence as proof traffic passed through here at all.

    When the tripwire counted a genuine BYPASS (`bypass_requests`/`bypass_aiohttp` > 0 - a
    stand reached Ollama through `requests` or `aiohttp` OUTSIDE any paced call), out
    `["valid"]` is set to `False` and `out["invalid_reason"]` names which client(s)
    bypassed it - even if `calls` itself is zero, i.e. even a run that bypassed the pacer
    ENTIRELY is flagged, never silently left unmarked because "nothing went through the
    paced path". `out["valid"]` is never set to `True` here: a clean run says nothing, so
    a stand's own, unrelated validity semantics are never overwritten.

    `nested_requests`/`nested_aiohttp` (K11) are a hit INSIDE an already-paced urllib/httpx
    call (litellm's async path routes through an aiohttp-backed httpx transport) -
    informational only, reported alongside `bypass_calls` but never affecting `valid`."""
    base = since or {"calls": 0, "pace_sleep_s": 0.0, "retries": 0, "retry_sleep_s": 0.0,
                     "gave_up": 0, "bypass_requests": 0, "bypass_aiohttp": 0,
                     "nested_requests": 0, "nested_aiohttp": 0, "_call_ms_len": 0}
    with _LOCK:
        calls = _COUNTERS["calls"] - base["calls"]
        bypass_requests = _COUNTERS["bypass_requests"] - base.get("bypass_requests", 0)
        bypass_aiohttp = _COUNTERS["bypass_aiohttp"] - base.get("bypass_aiohttp", 0)
        nested_requests = _COUNTERS["nested_requests"] - base.get("nested_requests", 0)
        nested_aiohttp = _COUNTERS["nested_aiohttp"] - base.get("nested_aiohttp", 0)
        if (calls <= 0 and bypass_requests <= 0 and bypass_aiohttp <= 0
                and nested_requests <= 0 and nested_aiohttp <= 0):
            return
        pace_sleep_s = _COUNTERS["pace_sleep_s"] - base["pace_sleep_s"]
        retries = _COUNTERS["retries"] - base["retries"]
        retry_sleep_s = _COUNTERS["retry_sleep_s"] - base["retry_sleep_s"]
        gave_up = _COUNTERS["gave_up"] - base["gave_up"]
        samples = list(_CALL_MS[base["_call_ms_len"]:])
    out["ollama_transport"] = {
        "calls": calls, "pace_sleep_s": round(pace_sleep_s, 3), "retries": retries,
        "retry_sleep_s": round(retry_sleep_s, 3), "gave_up": gave_up,
        "call_ms": _percentiles(samples),
        "timing_includes_pacing": True,
        "pace_floor_s": round(calls * PACE_S, 3),
        "bypass_calls": {"requests": bypass_requests, "aiohttp": bypass_aiohttp},
        "nested_calls": {"requests": nested_requests, "aiohttp": nested_aiohttp},
    }
    if bypass_requests > 0 or bypass_aiohttp > 0:
        culprits = [name for name, n in
                   (("requests", bypass_requests), ("aiohttp", bypass_aiohttp)) if n > 0]
        out["valid"] = False
        out["invalid_reason"] = (
            f"bypassed the pacer via {' and '.join(culprits)}: "
            f"{bypass_requests + bypass_aiohttp} request(s) reached the Ollama host "
            f"directly - unpaced, unretried, uncounted by the paced transports")


def _reset_for_tests() -> None:
    """Test-only: zero every counter and the pacing schedule, WITHOUT touching install
    state. Never called by a stand - `attach`'s delta form is how a real run isolates one
    arm's numbers from another's."""
    with _LOCK:
        _PACE_STATE["next_at"] = float("-inf")
        for k in _COUNTERS:
            _COUNTERS[k] = 0 if isinstance(_COUNTERS[k], int) else 0.0
        _CALL_MS.clear()
