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

`install(mode="observe")` (item 9A addendum, 2026-09-24): the identical hooks, with pacing
and retry both disabled - for a stand whose own published number IS a wall-clock time
(`guard_bench.py`'s `ms_per_call`, `k8_judge_eval.py`'s `seconds_per_pair`) and must never
include this module's own artificial spacing, while a bypass or a failed embed (P0(a))
still marks the run invalid exactly as in the default `"pace"` mode.

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

#: K33 (the auditor's finding, 2026-09-24): a failed LLM call (a fake Ollama's /api/generate
#: answering 500 "model runner has unexpectedly stopped") was scored as the SYSTEM's own
#: failure - never counted, never invalidated - because failed_outcomes only ever asked about
#: embed traffic. A SEPARATE, shorter retry policy from the port-exhaustion one above: at most
#: 2 retries, 15s then 30s apart (not a uniform interval), PACE mode only (never observe - see
#: `_llm_retryable`'s callers), because the engine's OWN retry logic (nevertwice/
#: consolidate_memory.py's own /api/generate hang-handling) already recovers from a transient
#: model-runner hiccup or a timeout/reset/refused-class transport failure, and counting one as
#: a failure the pacer never gave the engine a chance to recover from would trigger a needless
#: P2 re-run for something that was never actually lost.
LLM_RETRY_INTERVALS_S = (15.0, 30.0)
LLM_MAX_RETRIES = len(LLM_RETRY_INTERVALS_S)

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
#: item 9A addendum (the coordinator, 2026-09-24): `research/guard_bench.py`'s
#: `ms_per_call` and `research/k8_judge_eval.py`'s `seconds_per_pair` are registered
#: claims that PREREG-V2-2026-09-24 P5 requires measured WITHOUT this module's own
#: artificial pacing - but running those two stands with the pacer uninstalled entirely
#: would also hide a failed embed from P0(a), and a silent lexical fallback makes the
#: guard FASTER, corrupting the very timing P5 is protecting. `install(mode="observe")`
#: installs the identical hooks (so a bypass/failed-embed tripwire still fires) but with
#: pacing and retry both disabled: `_pace()`/`_pace_async()` are no-ops (pace_sleep_s
#: stays exactly 0 - the proof a reader checks) and `_effective_max_retries()` reads 0 (a
#: port-exhaustion signature is raised straight through exactly once, never retried).
#: `install()`'s default stays `"pace"`; every other stand is unaffected. Reset to
#: `"pace"` by `uninstall()`, so one process installing several stands in sequence (a
#: test suite) never leaks one stand's mode into the next stand's `install()` call.
_MODE = "pace"
_PACE_STATE = {"next_at": float("-inf")}
#: R2 (the auditor's finding): a consumer's own `elapsed - pace_sleep_s - retry_sleep_s`
#: subtraction (research/head_to_head.py's `_pace_excluded`, research/token_floor.py's
#: `finish_arm`) assumes the pacer's sleeps never OVERLAP - true for one caller paced
#: serially, false the moment two threads/tasks are both waiting on `_pace()` or a retry
#: sleep at once, where the SAME wall-clock second is double-counted as "pacing time" by
#: each one. `_INFLIGHT_STATE["n"]` is the number of paced calls currently inside their
#: OWN underlying `call()` (never during a sleep, which is already excluded from
#: `call_ms` for the same reason); `_COUNTERS["max_inflight"]` is the highest that count
#: has ever reached in this process - a narrower diagnostic than the one a caller doing
#: `elapsed - paced` actually needs (K14, immediately below: that caller asks
#: `max_concurrent_paced`, not this).
_INFLIGHT_STATE = {"n": 0}
#: K14 (the auditor's review of 8450a74): `_INFLIGHT_STATE`/`max_inflight` above tracks
#: concurrency ONLY during the underlying `call()` - deliberately, since `call_ms` needs
#: that same narrow window excluded from any sleep. But `pace_excluded_exact` uses that
#: SAME number to answer a DIFFERENT question: "is it safe to subtract this module's
#: summed `pace_sleep_s`/`retry_sleep_s` from a caller's own wall-clock elapsed time as
#: if those sleeps never overlapped." They can overlap even when no two calls ever do:
#: four real threads, the DEFAULT pacing floor (0.125 s) and a 0.05 s call body measured
#: wall 0.426 s, pace_sleep_s SUMMED to 0.749 s across the four - the pacer spaces each
#: thread's call START apart (so `call()` itself never overlaps, `max_inflight` reads 1,
#: "exact") while each thread's own PACING WAIT overlaps every other thread's wait AND
#: call, because spacing calls apart while other callers keep arriving is this module's
#: entire job. `_CONCURRENT_STATE`/`max_concurrent_paced` tracks the WHOLE paced
#: operation instead - from the moment a caller enters `_run_paced`/`_run_paced_async`,
#: BEFORE `_pace()`, until it returns or gives up - exactly the span a caller's own
#: subtraction assumes is serial. `pace_excluded_exact` is driven by THIS counter alone;
#: `max_inflight` is kept, unchanged, as the narrower "did two calls physically overlap"
#: diagnostic it always was.
_CONCURRENT_STATE = {"n": 0}
_COUNTERS = {"calls": 0, "pace_sleep_s": 0.0, "retries": 0, "retry_sleep_s": 0.0, "gave_up": 0,
            "bypass_requests": 0, "bypass_aiohttp": 0,
            "nested_requests": 0, "nested_aiohttp": 0, "max_inflight": 0,
            "max_concurrent_paced": 0,
            "llm_retries": 0, "llm_retry_sleep_s": 0.0}
_CALL_MS: list = []
#: F1 (.loop/PREREG-V2-2026-09-24.md, P0(a)): a genuine, non-retried (or retried-and-
#: gave-up) failure on an EMBED endpoint - counted separately from `_COUNTERS["gave_up"]`
#: (which is EVERY endpoint's port-exhaustion give-up, embed or not) because P0(a) only
#: ever asks about embed traffic: this is where `_embed_http` returns None and the caller
#: falls back to lexical, silently. `by_status`/`by_exception_type` are DICTS (status code
#: or exception class name -> count), never scalars, so they live here rather than in
#: `_COUNTERS` (whose own `_reset_for_tests` assumes every value is an int or a float).
_EMBED_FAILURES = {"by_status": {}, "by_exception_type": {}, "gave_up": 0}
#: K33 (the auditor's finding, 2026-09-24): the SAME shape as `_EMBED_FAILURES`, for the LLM
#: generation endpoints (`_is_llm_path`) instead of the embed ones - a SEPARATE bucket so the
#: embed rule's own record is unchanged. `gave_up` here means the SAME thing it means for
#: embed: gave up on the PORT-EXHAUSTION retry above, never on the K33 5xx/transport-exception
#: retry below (whose own exhaustion falls through to `by_status`/`by_exception_type` instead,
#: exactly the way a non-port-exhaustion embed failure already does).
_LLM_FAILURES = {"by_status": {}, "by_exception_type": {}, "gave_up": 0}
#: R1 (the auditor's finding, 2026-09-24): a probe or a stand can be reachable from MORE
#: than one recognised Ollama host at once (this dev machine runs a real Ollama on the
#: default 127.0.0.1:11434 AND a fake one on a random port during
#: research/_ollama_symmetry_probe.py's own runs) - `calls` alone cannot tell which host a
#: paced call actually went to, so a stray request to the WRONG one (litellm's /api/show
#: ignoring api_base, observed hitting the real 11434 during probing) silently inflated
#: the aggregate without anyone able to see it. Tallied by (host, port) at the exact same
#: point `_COUNTERS["calls"]` increments, so per-host sums always equal the aggregate.
_CALLS_BY_HOST: dict = {}

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


#: (в) (the coordinator's reproduction, .loop/HANDOFF-PORTS.md): `/api/embed` is Ollama's
#: current endpoint; `/api/embeddings` is its LEGACY one, still what `ollama-python`'s
#: `Client.embed` falls back to internally on an older server (`_client.py:442`) - both
#: route through this module's is_embed accounting, or a failure on the legacy path is as
#: invisible to F1/F1b as it was before either existed.
_EMBED_PATHS = ("/api/embed", "/api/embeddings")


def _is_embed_path(url: str) -> bool:
    """True iff `url`'s path names an embedding endpoint (current or legacy - see
    `_EMBED_PATHS`). F1 (.loop/PREREG-V2-2026-09-24.md, P0(a)): `nevertwice/_engine_store.py`'s
    `_embed_http` returns None on ANY failure and the caller falls back to lexical recall,
    silently - the one failure class this module could see (it sits below every caller)
    but never counted. Matched loosely (a trailing slash, a query string) rather than an
    exact string compare."""
    try:
        path = urllib.parse.urlsplit(url).path
    except Exception:                                                # noqa: BLE001
        return False
    path = path.rstrip("/")
    return any(path.endswith(p) for p in _EMBED_PATHS)


#: K33: the LLM generation endpoints a stand can reach on the LOCAL Ollama host - its native
#: surface (`/api/generate` - k8_judge_eval.py, token_ab.py's own OLLAMA_URL default; `/api/chat`
#: - litellm's `ollama_chat/...` provider per _ollama_symmetry_probe.py's own comment on
#: 623a1df ["ollama_chat/... never calls /api/generate at all"], frontier_eval.py and
#: gen_code_sessions.py both post here directly) and the OpenAI-compatible surface Ollama ALSO
#: serves (`/v1/chat/completions`, `/v1/completions` - what a client library pointed at Ollama
#: through its OpenAI-compatible shim, rather than its native one, would hit). Never the embed
#: endpoints above - the two sets are disjoint by construction, so a call is counted on at most
#: one of `_is_embed_path`/`_is_llm_path`.
_LLM_PATHS = ("/api/generate", "/api/chat", "/v1/chat/completions", "/v1/completions")


def _is_llm_path(url: str) -> bool:
    """True iff `url`'s path names an LLM generation endpoint (K33, the auditor's finding,
    2026-09-24): a failed extraction/generation call was scored as the SYSTEM's own failure,
    with no record anywhere - the pacer only ever asked this question of embed traffic.
    Matched loosely (a trailing slash, a query string), exactly like `_is_embed_path`."""
    try:
        path = urllib.parse.urlsplit(url).path
    except Exception:                                                # noqa: BLE001
        return False
    path = path.rstrip("/")
    return any(path.endswith(p) for p in _LLM_PATHS)


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


def _record_embed_failure(exc) -> None:
    """F1: a genuine failure on an EMBED endpoint - never classified as port-exhaustion
    (so never retried), or retried and given up on (see the `is_embed` branch in
    `_run_paced`/`_run_paced_async`, one caller; `_maybe_record_embed_response_failure`,
    F1b, is the other). `by_status`: the exception (or, since F1b, a bare `httpx.Response`
    that was never raised at all - it has no `.code`, only `.status_code`) carries an HTTP
    status - `urllib.error.HTTPError`'s `.code`, an `ollama.ResponseError`-shaped
    `.status_code`, or an `httpx.HTTPStatusError`'s `.response.status_code` - the server
    answered, just badly. `by_exception_type`: no status anywhere - a pure transport
    failure (a timeout, a refused connection) that never got a response to read a status
    from. The two are mutually exclusive per call: a status, when found, is trusted over
    the exception's own class name. `exc` is typed loosely on purpose - it is duck-typed
    across four different shapes, not one exception hierarchy."""
    status = getattr(exc, "code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
    with _LOCK:
        if status is not None:
            _EMBED_FAILURES["by_status"][status] = _EMBED_FAILURES["by_status"].get(status, 0) + 1
        else:
            name = type(exc).__name__
            _EMBED_FAILURES["by_exception_type"][name] = (
                _EMBED_FAILURES["by_exception_type"].get(name, 0) + 1)


def _llm_status_of(obj) -> int | None:
    """The same status-extraction `_record_embed_failure` already does, factored out so
    `_llm_retryable` can ask "is this a 5xx" without duplicating it. Works on an exception
    (`.code`, `.status_code`, `.response.status_code`) or a bare `httpx.Response` (`.status_code`
    alone - K33's own F1b-style gap, an httpx 5xx that was never raised)."""
    status = getattr(obj, "code", None)
    if status is None:
        status = getattr(obj, "status_code", None)
    if status is None:
        resp = getattr(obj, "response", None)
        status = getattr(resp, "status_code", None)
    return status


def _is_llm_retryable_transport_exc(obj) -> bool:
    """K33 (the coordinator's scope addition, 2026-09-24): True iff `obj` (or its wrapped
    cause/context/reason - the same unwrapping `_winerror_of` already does for a port-exhaustion
    WinError, plus `urllib.error.URLError.reason`) is a TIMEOUT / CONNECTION-RESET /
    CONNECTION-REFUSED class transport failure - the class of exception the engine's OWN retry
    logic (nevertwice/consolidate_memory.py's own /api/generate hang-handling) already recovers
    from, so leaving the pacer blind to it would count a failure the caller never actually sees.
    Never a bare "the call raised SOMETHING", which would retry a programming error as if it
    were transient; never an HTTP status (`_llm_status_of` is asked FIRST by `_llm_retryable`,
    and a status-bearing object never matches these types anyway)."""
    candidates = (obj, getattr(obj, "reason", None), getattr(obj, "__cause__", None),
                 getattr(obj, "__context__", None))
    types: tuple = (TimeoutError, ConnectionResetError, ConnectionRefusedError)
    try:
        import httpx
        types = types + (httpx.TimeoutException, httpx.ConnectError)
    except ImportError:
        pass
    return any(isinstance(c, types) for c in candidates if c is not None)


def _llm_retryable(exc) -> bool:
    """K33: True iff `exc` (an exception this module caught, or a bare httpx.Response it did
    not - both duck-typed identically via `_llm_status_of`) is worth this module's OWN bounded
    LLM retry - a 5xx HTTP status, or (the coordinator's scope addition) a timeout/
    connection-reset/connection-refused class transport exception. A STATUS is checked first and
    decides it ALONE: a 4xx is NEVER retried (returns False immediately, never falling through
    to the transport-exception check, which a status-bearing object would not match anyway)."""
    status = _llm_status_of(exc)
    if status is not None:
        return 500 <= status < 600
    return _is_llm_retryable_transport_exc(exc)


def _llm_retry_allowed() -> bool:
    """PACE mode only, never observe (K33) - mirrors `_effective_max_retries`'s own
    `_MODE`-gating for the port-exhaustion retry, factored out the same way so a test can
    monkeypatch this ONE seam to simulate "the mode guard was removed" without touching
    `_MODE` itself or reimplementing the retry loop around it."""
    return _MODE == "pace"


def _record_llm_retry(wait: float) -> None:
    """K33: bump `llm_retries`/`llm_retry_sleep_s` - factored out (the same reasoning as
    `_llm_retry_allowed`) so a test can monkeypatch this ONE seam to simulate "the retry ran
    but was never counted" without touching the retry itself (the sleep+continue stays in
    `_run_paced`/`_run_paced_async`, unaffected by this mutation)."""
    with _LOCK:
        _COUNTERS["llm_retries"] += 1
        _COUNTERS["llm_retry_sleep_s"] += wait


def _record_llm_failure(exc) -> None:
    """K33: the LLM-endpoint counterpart of `_record_embed_failure` - same shape, same
    status-then-exception-type rule, a SEPARATE dict (`_LLM_FAILURES`) so the embed rule's own
    record is unchanged. Called both when a failure was never retryable at all (a 4xx, or a
    transport exception outside the timeout/reset/refused class) and when the bounded K33 retry
    above was exhausted without recovering."""
    status = _llm_status_of(exc)
    with _LOCK:
        if status is not None:
            _LLM_FAILURES["by_status"][status] = _LLM_FAILURES["by_status"].get(status, 0) + 1
        else:
            name = type(exc).__name__
            _LLM_FAILURES["by_exception_type"][name] = (
                _LLM_FAILURES["by_exception_type"].get(name, 0) + 1)


# ── pacing: one process-wide schedule, reserved under the lock, slept outside it ────────

def _reserve_slot() -> float:
    with _LOCK:
        now = _now()
        start = _PACE_STATE["next_at"]
        if start < now:
            start = now
        _PACE_STATE["next_at"] = start + PACE_S
    return max(0.0, start - now)


def _effective_max_retries() -> int:
    """`MAX_RETRIES` in `"pace"` mode; 0 in `"observe"` mode (see `_MODE`'s docstring) -
    a port-exhaustion signature is still classified and still counted (`gave_up`), it is
    simply never slept on or resent."""
    return 0 if _MODE == "observe" else MAX_RETRIES


def _pace() -> None:
    if _MODE == "observe":
        return
    wait = _reserve_slot()
    if wait > 0:
        _sleep(wait)
        with _LOCK:
            _COUNTERS["pace_sleep_s"] += wait


async def _pace_async() -> None:
    if _MODE == "observe":
        return
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


def _dict_delta(now_d: dict, base_d: dict) -> dict:
    """F1: `now_d - base_d`, key by key (a status code or an exception class name),
    dropping any key whose delta is <= 0 - the same "only report what grew" rule
    `attach()`'s scalar counters already follow, extended to the dict-shaped ones
    (`_EMBED_FAILURES["by_status"]`/`["by_exception_type"]`) `_COUNTERS` cannot hold
    (its own `_reset_for_tests` assumes every value is a plain int or float)."""
    keys = set(now_d) | set(base_d)
    delta = {k: now_d.get(k, 0) - base_d.get(k, 0) for k in keys}
    return {k: v for k, v in delta.items() if v > 0}


# ── the retry driver: sync and async, identical policy, different sleep primitive ──────

def _inflight_enter() -> None:
    with _LOCK:
        _INFLIGHT_STATE["n"] += 1
        if _INFLIGHT_STATE["n"] > _COUNTERS["max_inflight"]:
            _COUNTERS["max_inflight"] = _INFLIGHT_STATE["n"]


def _inflight_exit() -> None:
    with _LOCK:
        _INFLIGHT_STATE["n"] -= 1


def _concurrent_enter() -> None:
    """K14: entered BEFORE `_pace()`, for the WHOLE paced operation - see
    `_CONCURRENT_STATE`'s own docstring above."""
    with _LOCK:
        _CONCURRENT_STATE["n"] += 1
        if _CONCURRENT_STATE["n"] > _COUNTERS["max_concurrent_paced"]:
            _COUNTERS["max_concurrent_paced"] = _CONCURRENT_STATE["n"]


def _concurrent_exit() -> None:
    with _LOCK:
        _CONCURRENT_STATE["n"] -= 1


def _run_paced(call: Callable, host_key: tuple | None = None, is_embed: bool = False,
               is_llm: bool = False):
    """`call()` to the Ollama host, paced and retried in place. `call` raises on any
    failure worth classifying (a plain function return is success); `call_ms` records
    only a SUCCESSFUL attempt's wall time, never a failed attempt's, and never a sleep -
    the timing window opens after `_pace()` has already returned. `host_key`, when given,
    is tallied in `_CALLS_BY_HOST` alongside the aggregate `calls` counter. `is_embed`
    (F1): when the call ultimately fails - never classified as port-exhaustion, or
    retried and given up on - and was to an embed endpoint, the failure is tallied into
    `_EMBED_FAILURES` (never for a call this function itself successfully retried past).
    `is_llm` (K33): the same, into `_LLM_FAILURES`, AFTER a bounded LLM-specific retry
    (`_llm_retryable`, `LLM_RETRY_INTERVALS_S`) has had its own chance - PACE mode only,
    never observe (`_MODE` read fresh on every attempt, guarding the retry branch below)."""
    _concurrent_enter()                 # K14: before _pace() - the WHOLE operation
    try:
        _pace()
        with _LOCK:
            _COUNTERS["calls"] += 1
            if host_key is not None:
                _CALLS_BY_HOST[host_key] = _CALLS_BY_HOST.get(host_key, 0) + 1
        attempt = 0
        llm_attempt = 0
        while True:
            _inflight_enter()
            t0 = _now()
            try:
                result = call()
            except BaseException as exc:                              # noqa: BLE001
                _inflight_exit()
                retry = classify(exc, is_ollama_host=True)
                if retry and attempt < _effective_max_retries():
                    attempt += 1
                    with _LOCK:
                        _COUNTERS["retries"] += 1
                    _sleep(RETRY_INTERVAL_S)
                    with _LOCK:
                        _COUNTERS["retry_sleep_s"] += RETRY_INTERVAL_S
                    continue
                #: K33: a SEPARATE bounded retry, PACE mode only, never for a failure the
                #: port-exhaustion classifier already claimed (`not retry`).
                if (not retry and is_llm and _llm_retry_allowed()
                        and llm_attempt < LLM_MAX_RETRIES and _llm_retryable(exc)):
                    wait = LLM_RETRY_INTERVALS_S[llm_attempt]
                    llm_attempt += 1
                    _record_llm_retry(wait)
                    _sleep(wait)
                    continue
                if retry:
                    with _LOCK:
                        _COUNTERS["gave_up"] += 1
                    if is_embed:
                        with _LOCK:
                            _EMBED_FAILURES["gave_up"] += 1
                    if is_llm:
                        with _LOCK:
                            _LLM_FAILURES["gave_up"] += 1
                else:
                    if is_embed:
                        _record_embed_failure(exc)
                    if is_llm:
                        _record_llm_failure(exc)
                raise
            else:
                _inflight_exit()
                with _LOCK:
                    _CALL_MS.append((_now() - t0) * 1000.0)
                return result
    finally:
        _concurrent_exit()


async def _run_paced_async(call: Callable, host_key: tuple | None = None,
                           is_embed: bool = False, is_llm: bool = False):
    """Async counterpart of `_run_paced` - identical policy, `await`ed sleeps. See
    `_run_paced`'s own docstring for `is_embed`/`is_llm` (K33)."""
    _concurrent_enter()                 # K14: before _pace() - the WHOLE operation
    try:
        await _pace_async()
        with _LOCK:
            _COUNTERS["calls"] += 1
            if host_key is not None:
                _CALLS_BY_HOST[host_key] = _CALLS_BY_HOST.get(host_key, 0) + 1
        attempt = 0
        llm_attempt = 0
        while True:
            _inflight_enter()
            t0 = _now()
            try:
                result = await call()
            except BaseException as exc:                              # noqa: BLE001
                _inflight_exit()
                retry = classify(exc, is_ollama_host=True)
                if retry and attempt < _effective_max_retries():
                    attempt += 1
                    with _LOCK:
                        _COUNTERS["retries"] += 1
                    await _async_sleep(RETRY_INTERVAL_S)
                    with _LOCK:
                        _COUNTERS["retry_sleep_s"] += RETRY_INTERVAL_S
                    continue
                #: K33: a SEPARATE bounded retry, PACE mode only, never for a failure the
                #: port-exhaustion classifier already claimed (`not retry`).
                if (not retry and is_llm and _llm_retry_allowed()
                        and llm_attempt < LLM_MAX_RETRIES and _llm_retryable(exc)):
                    wait = LLM_RETRY_INTERVALS_S[llm_attempt]
                    llm_attempt += 1
                    _record_llm_retry(wait)
                    await _async_sleep(wait)
                    continue
                if retry:
                    with _LOCK:
                        _COUNTERS["gave_up"] += 1
                    if is_embed:
                        with _LOCK:
                            _EMBED_FAILURES["gave_up"] += 1
                    if is_llm:
                        with _LOCK:
                            _LLM_FAILURES["gave_up"] += 1
                else:
                    if is_embed:
                        _record_embed_failure(exc)
                    if is_llm:
                        _record_llm_failure(exc)
                raise
            else:
                _inflight_exit()
                with _LOCK:
                    _CALL_MS.append((_now() - t0) * 1000.0)
                return result
    finally:
        _concurrent_exit()


# ── urllib.request.urlopen ───────────────────────────────────────────────────────────

def _paced_urlopen(*args, **kwargs):
    req = args[0] if args else kwargs.get("url")
    url = req.full_url if hasattr(req, "full_url") else str(req)
    host, port = _host_port(url)
    orig = _ORIG["urlopen"]
    if not is_ollama_host(host, port):
        return orig(*args, **kwargs)
    return _run_paced(lambda: _mark_paced(lambda: orig(*args, **kwargs)),
                      host_key=(host, port), is_embed=_is_embed_path(url),
                      is_llm=_is_llm_path(url))


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


class _LLMRetryableResponse(Exception):
    """K33: the LLM counterpart of `_PortExhaustionResponse` - routes an httpx 5xx RESPONSE on
    an LLM endpoint, in PACE mode only, through the same exception-driven retry loop (httpx
    does not raise on a non-2xx status by itself). Carries the same `.status_code` shape
    `_llm_status_of` reads. `.response` is the ORIGINAL httpx.Response, handed back to the
    caller once retries are exhausted, restoring httpx's own contract."""

    def __init__(self, response):
        self.response = response
        self.status_code = response.status_code
        super().__init__(f"llm 5xx retry: HTTP {response.status_code}")


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


def _maybe_record_embed_response_failure(response, *, is_embed: bool) -> None:
    """F1b (.loop/HANDOFF-PORTS.md, the auditor's reproduction against a fake Ollama
    answering 500 on /api/embed): httpx does NOT raise on a non-2xx response by itself -
    `Client.send`/`AsyncClient.send` hand the caller a `Response` with `.status_code` set
    and it is up to the caller to read it. `ollama-python`'s `Client.embed` DOES call
    `response.raise_for_status()`, but only AFTER `Client.send` has already RETURNED -
    outside `_run_paced`'s own try/except, the only place `_record_embed_failure` was
    ever called from before this. A 500 (or a non-port-exhaustion 400 - wrong shape,
    wrong body) was therefore recorded as a plain SUCCESSFUL call: `failed_outcomes`
    stayed empty and `valid` stayed unset, exactly the silent-lexical-fallback P0(a)
    exists to catch.

    Called from `_attempt()` in `_paced_httpx_send`/`_paced_httpx_async_send`, AFTER
    `_classify_httpx_response` has already had its chance to route the SAME response
    through the retry loop as `_PortExhaustionResponse` - that path raises before
    reaching this call, so a port-exhaustion 400 is never double-counted here (it is
    counted as a retry, and on `gave_up`, by the exception path in `_run_paced`
    instead). Reads only `.status_code`, never `.text`/`.read()`, so this is safe to call
    on a STREAMED response too - R3's stream exclusion in `_classify_httpx_response` is
    about not consuming a lazy BODY, and a status line is already on the wire regardless
    of streaming."""
    if is_embed and response.status_code >= 400:
        _record_embed_failure(response)


def _maybe_record_llm_response_failure(response, *, is_llm: bool) -> None:
    """K33's F1b/F1c-equivalent for LLM endpoints: httpx does NOT raise on a non-2xx response by
    itself, so a 4xx (never retried) or a 5xx that arrived outside PACE mode must still be
    tallied here - a 5xx IN pace mode that this call's own retry routed through
    `_LLMRetryableResponse` never reaches this function at all (that path raises past it).
    Reads only `.status_code`, safe on a streamed response too."""
    if is_llm and response.status_code >= 400:
        _record_llm_failure(response)


def _paced_httpx_send(self, request, **kwargs):
    import httpx  # noqa: PLC0415 - only reachable when httpx installed this got patched
    host, port = (request.url.host or "").lower(), (
        request.url.port or (443 if request.url.scheme == "https" else 80))
    orig = _ORIG["httpx_send"]
    if not is_ollama_host(host, port):
        return orig(self, request, **kwargs)
    stream = bool(kwargs.get("stream", False))
    is_embed = _is_embed_path(str(request.url))
    is_llm = _is_llm_path(str(request.url))

    def _attempt():
        response = _mark_paced(lambda: orig(self, request, **kwargs))
        if _classify_httpx_response(response, stream=stream):
            raise _PortExhaustionResponse(response)
        #: K33: httpx does not raise on a 5xx by itself - route it through the SAME
        #: exception-driven retry loop `_PortExhaustionResponse` already uses, PACE mode only.
        if is_llm and _llm_retry_allowed() and _llm_retryable(response):
            raise _LLMRetryableResponse(response)
        _maybe_record_embed_response_failure(response, is_embed=is_embed)  # F1b
        _maybe_record_llm_response_failure(response, is_llm=is_llm)        # K33
        return response
    try:
        return _run_paced(_attempt, host_key=(host, port), is_embed=is_embed, is_llm=is_llm)
    except _PortExhaustionResponse as marker:
        return marker.response          # retries exhausted; hand back the last 400 as-is
    except _LLMRetryableResponse as marker:
        return marker.response          # K33: retries exhausted; hand back the last 5xx as-is
    except httpx.HTTPError:
        raise


async def _paced_httpx_async_send(self, request, **kwargs):
    host, port = (request.url.host or "").lower(), (
        request.url.port or (443 if request.url.scheme == "https" else 80))
    orig = _ORIG["httpx_async_send"]
    if not is_ollama_host(host, port):
        return await orig(self, request, **kwargs)
    stream = bool(kwargs.get("stream", False))
    is_embed = _is_embed_path(str(request.url))
    is_llm = _is_llm_path(str(request.url))

    async def _attempt():
        response = await _mark_paced_async(lambda: orig(self, request, **kwargs))
        if _classify_httpx_response(response, stream=stream):
            raise _PortExhaustionResponse(response)
        if is_llm and _llm_retry_allowed() and _llm_retryable(response):
            raise _LLMRetryableResponse(response)
        _maybe_record_embed_response_failure(response, is_embed=is_embed)  # F1b
        _maybe_record_llm_response_failure(response, is_llm=is_llm)        # K33
        return response
    try:
        return await _run_paced_async(_attempt, host_key=(host, port), is_embed=is_embed,
                                      is_llm=is_llm)
    except _PortExhaustionResponse as marker:
        return marker.response
    except _LLMRetryableResponse as marker:
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

#: K27 (the auditor's finding, 2026-09-24): the only two modes this module knows about -
#: see `_MODE`'s own docstring. `install()` used to accept ANY string here (a typo like
#: `"obsrve"` was stored verbatim and `attach()` reported it back the same way, neither
#: `"pace"` nor `"observe"` to a reader checking PREREG-V2 P5's own rule) and a second
#: `install()` call while already installed was ALWAYS a silent no-op regardless of mode -
#: `install()` then `install(mode="observe")` left the pacer stuck paced, sleeping, while
#: the caller believed it had switched to observe.
VALID_MODES = frozenset({"pace", "observe"})


def install(mode: str = "pace") -> None:
    """Patch `urllib.request.urlopen` and, when importable, `httpx.Client.send` /
    `httpx.AsyncClient.send` (paced and retried), plus `requests.Session.send` /
    `aiohttp.ClientSession._request` (the tripwire - counted only, never paced or
    retried). A second call while already installed IN THE SAME MODE is a no-op - it does
    NOT re-capture `_ORIG` (which would point the "original" at THIS module's own wrapper)
    and does NOT reset counters (a stand may call `install()` defensively more than once in
    one process).

    `mode="observe"` (item 9A addendum, see `_MODE`'s own docstring): the identical hooks,
    with pacing and retry both disabled - for a stand (`guard_bench.py`, `k8_judge_eval.py`)
    whose published claim IS a wall-clock time and must never include this module's own
    artificial spacing, while still catching a bypass or a failed embed (P0(a)).

    K27: `mode` outside `VALID_MODES` raises `ValueError` - it used to be accepted and
    silently misreported. A second call while already installed in a DIFFERENT mode raises
    `RuntimeError` - it used to be a silent no-op that left the FIRST mode in force; call
    `uninstall()` first to actually switch modes. The same mode twice stays the pre-existing
    idempotent no-op, unchanged - a stand that defensively calls `install()` more than once
    with its own, consistent mode is not affected by either new check."""
    if mode not in VALID_MODES:
        raise ValueError(
            f"_ollama_pacer.install(mode={mode!r}): mode must be one of "
            f"{sorted(VALID_MODES)} - a mode outside this set used to be accepted and "
            f"stored verbatim, reported back the same way by attach(), neither 'pace' nor "
            f"'observe' to a reader checking PREREG-V2 P5's own rule (K27)")
    global _INSTALLED, _MODE
    with _LOCK:
        if _INSTALLED:
            if mode != _MODE:
                raise RuntimeError(
                    f"_ollama_pacer.install(mode={mode!r}): already installed in mode "
                    f"{_MODE!r} - a second install() in a DIFFERENT mode used to be a "
                    f"silent no-op that left the FIRST mode in force (K27). Call "
                    f"uninstall() first to switch modes, or pass mode={_MODE!r} to stay "
                    f"the pre-existing idempotent no-op.")
            return
        _MODE = mode
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
    global _INSTALLED, _MODE
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
        _MODE = "pace"          # item 9A addendum: never leak one stand's mode into the next


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
                "max_inflight": _COUNTERS["max_inflight"],
                "max_concurrent_paced": _COUNTERS["max_concurrent_paced"],
                "llm_retries": _COUNTERS["llm_retries"],
                "llm_retry_sleep_s": _COUNTERS["llm_retry_sleep_s"],
                "_call_ms_len": len(_CALL_MS),
                "_calls_by_host": dict(_CALLS_BY_HOST),
                "_embed_failures": {"by_status": dict(_EMBED_FAILURES["by_status"]),
                                    "by_exception_type": dict(_EMBED_FAILURES["by_exception_type"]),
                                    "gave_up": _EMBED_FAILURES["gave_up"]},
                "_llm_failures": {"by_status": dict(_LLM_FAILURES["by_status"]),
                                  "by_exception_type": dict(_LLM_FAILURES["by_exception_type"]),
                                  "gave_up": _LLM_FAILURES["gave_up"]}}


def calls_by_host(since: dict | None = None) -> dict:
    """`{(host, port): count}` for paced calls (urllib/httpx) this process made, as a
    DELTA against an earlier `snapshot()` when given. A read-only diagnostic - separate
    from `attach()`'s own aggregate `calls`, which every existing caller already reads and
    which this does not change the meaning of. Exists because a probe or a stand can be
    reachable from more than one recognised Ollama host at once (R1: this dev machine
    runs a real Ollama on the default 127.0.0.1:11434 AND a fake one on a random port
    during research/_ollama_symmetry_probe.py's own runs) - the aggregate alone cannot
    say whether every counted call actually reached the host the caller INTENDED."""
    with _LOCK:
        now = dict(_CALLS_BY_HOST)
    if since is None:
        return now
    before = since.get("_calls_by_host", {})
    keys = set(now) | set(before)
    delta = {k: now.get(k, 0) - before.get(k, 0) for k in keys}
    return {k: v for k, v in delta.items() if v != 0}


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
    informational only, reported alongside `bypass_calls` but never affecting `valid`.

    F1 (.loop/PREREG-V2-2026-09-24.md, P0(a)): `failed_outcomes` (`by_status`,
    `by_exception_type`, `gave_up` - EMBED endpoints only, `_is_embed_path`) is where
    `nevertwice/_engine_store.py`'s `_embed_http` returns None and a note or query
    silently falls back to lexical. Any of the three > 0 sets `out["valid"] = False` too,
    combined with a bypass's own reason (below) when both fire in the same window - a
    run is invalid for every reason this call found, not just the last one checked."""
    base = since or {"calls": 0, "pace_sleep_s": 0.0, "retries": 0, "retry_sleep_s": 0.0,
                     "gave_up": 0, "bypass_requests": 0, "bypass_aiohttp": 0,
                     "nested_requests": 0, "nested_aiohttp": 0, "_call_ms_len": 0,
                     "llm_retries": 0, "llm_retry_sleep_s": 0.0}
    base_ef = base.get("_embed_failures") or {"by_status": {}, "by_exception_type": {},
                                              "gave_up": 0}
    base_lf = base.get("_llm_failures") or {"by_status": {}, "by_exception_type": {},
                                            "gave_up": 0}
    with _LOCK:
        calls = _COUNTERS["calls"] - base["calls"]
        bypass_requests = _COUNTERS["bypass_requests"] - base.get("bypass_requests", 0)
        bypass_aiohttp = _COUNTERS["bypass_aiohttp"] - base.get("bypass_aiohttp", 0)
        nested_requests = _COUNTERS["nested_requests"] - base.get("nested_requests", 0)
        nested_aiohttp = _COUNTERS["nested_aiohttp"] - base.get("nested_aiohttp", 0)
        failed_by_status = _dict_delta(_EMBED_FAILURES["by_status"], base_ef["by_status"])
        failed_by_exc = _dict_delta(_EMBED_FAILURES["by_exception_type"],
                                    base_ef["by_exception_type"])
        failed_gave_up = _EMBED_FAILURES["gave_up"] - base_ef["gave_up"]
        #: K33: the LLM-endpoint counterparts, computed the same way.
        failed_by_status_llm = _dict_delta(_LLM_FAILURES["by_status"], base_lf["by_status"])
        failed_by_exc_llm = _dict_delta(_LLM_FAILURES["by_exception_type"],
                                        base_lf["by_exception_type"])
        failed_gave_up_llm = _LLM_FAILURES["gave_up"] - base_lf["gave_up"]
        if (calls <= 0 and bypass_requests <= 0 and bypass_aiohttp <= 0
                and nested_requests <= 0 and nested_aiohttp <= 0
                and not failed_by_status and not failed_by_exc and failed_gave_up <= 0
                and not failed_by_status_llm and not failed_by_exc_llm
                and failed_gave_up_llm <= 0):
            return
        pace_sleep_s = _COUNTERS["pace_sleep_s"] - base["pace_sleep_s"]
        retries = _COUNTERS["retries"] - base["retries"]
        retry_sleep_s = _COUNTERS["retry_sleep_s"] - base["retry_sleep_s"]
        gave_up = _COUNTERS["gave_up"] - base["gave_up"]
        llm_retries = _COUNTERS["llm_retries"] - base.get("llm_retries", 0)
        llm_retry_sleep_s = _COUNTERS["llm_retry_sleep_s"] - base.get("llm_retry_sleep_s", 0.0)
        samples = list(_CALL_MS[base["_call_ms_len"]:])
        #: R2/K14: the PROCESS-WIDE peak, read here rather than as a delta - a monotonic
        #: HIGH-WATER MARK, not a sum, and a delta of two peaks answers "did a NEW record
        #: get set during this window", not "was there ever overlap during it".
        #: Conservative in one direction only: once any concurrency has EVER happened in
        #: this process, every later `pace_excluded_exact` reads False, even for a window
        #: that was itself perfectly serial - never the other way around (a truly
        #: concurrent window is never reported as exact). `pace_excluded_exact` is driven
        #: by `max_concurrent_paced` (the WHOLE paced operation, K14), never by the
        #: narrower `max_inflight` (call-only) - see `_CONCURRENT_STATE`'s docstring.
        max_inflight = _COUNTERS["max_inflight"]
        max_concurrent_paced = _COUNTERS["max_concurrent_paced"]
    out["ollama_transport"] = {
        "calls": calls, "pace_sleep_s": round(pace_sleep_s, 3), "retries": retries,
        "retry_sleep_s": round(retry_sleep_s, 3), "gave_up": gave_up,
        "call_ms": _percentiles(samples),
        #: item 9A addendum: "observe" mode never sleeps or retries at all (`_pace()`,
        #: `_effective_max_retries()`), so `pace_sleep_s`/`retry_sleep_s` are already,
        #: honestly, 0 - `timing_includes_pacing` says so too, rather than reporting
        #: `True` for a mode whose entire point is that it added nothing to subtract.
        "mode": _MODE,
        "timing_includes_pacing": _MODE != "observe",
        "pace_floor_s": round(calls * PACE_S, 3) if _MODE != "observe" else 0.0,
        "bypass_calls": {"requests": bypass_requests, "aiohttp": bypass_aiohttp},
        "nested_calls": {"requests": nested_requests, "aiohttp": nested_aiohttp},
        "max_inflight": max_inflight,
        "max_concurrent_paced": max_concurrent_paced,
        "pace_excluded_exact": max_concurrent_paced <= 1,
        "failed_outcomes": {"by_status": failed_by_status, "by_exception_type": failed_by_exc,
                            "gave_up": failed_gave_up},
        #: K33: llm_retry_sleep_s is excluded from call_ms the same way pace_sleep_s/
        #: retry_sleep_s already are - t0 is reset fresh after every sleep in _run_paced/
        #: _run_paced_async, so only a successful attempt's own wall time is ever appended.
        "llm_retries": llm_retries, "llm_retry_sleep_s": round(llm_retry_sleep_s, 3),
        "failed_outcomes_llm": {"by_status": failed_by_status_llm,
                                "by_exception_type": failed_by_exc_llm,
                                "gave_up": failed_gave_up_llm},
    }
    reasons = []
    if bypass_requests > 0 or bypass_aiohttp > 0:
        culprits = [name for name, n in
                   (("requests", bypass_requests), ("aiohttp", bypass_aiohttp)) if n > 0]
        reasons.append(
            f"bypassed the pacer via {' and '.join(culprits)}: "
            f"{bypass_requests + bypass_aiohttp} request(s) reached the Ollama host "
            f"directly - unpaced, unretried, uncounted by the paced transports")
    if failed_by_status or failed_by_exc or failed_gave_up > 0:
        n_failed = (sum(failed_by_status.values()) + sum(failed_by_exc.values())
                   + failed_gave_up)
        reasons.append(
            f"{n_failed} embed call(s) failed (by_status={failed_by_status}, "
            f"by_exception_type={failed_by_exc}, gave_up={failed_gave_up}) - "
            f"a note or query may have silently fallen back to lexical recall")
    if failed_by_status_llm or failed_by_exc_llm or failed_gave_up_llm > 0:
        n_failed_llm = (sum(failed_by_status_llm.values()) + sum(failed_by_exc_llm.values())
                       + failed_gave_up_llm)
        reasons.append(
            f"{n_failed_llm} llm call(s) failed (by_status={failed_by_status_llm}, "
            f"by_exception_type={failed_by_exc_llm}, gave_up={failed_gave_up_llm}) - "
            f"K33: a failed extraction/generation call is scored as the system's own "
            f"failure, with no record, unless this fires")
    if reasons:
        out["valid"] = False
        out["invalid_reason"] = "; ".join(reasons)


def _reset_for_tests() -> None:
    """Test-only: zero every counter and the pacing schedule, WITHOUT touching install
    state. Never called by a stand - `attach`'s delta form is how a real run isolates one
    arm's numbers from another's."""
    with _LOCK:
        _PACE_STATE["next_at"] = float("-inf")
        for k in _COUNTERS:
            _COUNTERS[k] = 0 if isinstance(_COUNTERS[k], int) else 0.0
        _CALL_MS.clear()
        _CALLS_BY_HOST.clear()
        _INFLIGHT_STATE["n"] = 0
        _CONCURRENT_STATE["n"] = 0
        _EMBED_FAILURES["by_status"].clear()
        _EMBED_FAILURES["by_exception_type"].clear()
        _EMBED_FAILURES["gave_up"] = 0
        _LLM_FAILURES["by_status"].clear()
        _LLM_FAILURES["by_exception_type"].clear()
        _LLM_FAILURES["gave_up"] = 0
