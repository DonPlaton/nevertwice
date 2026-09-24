#!/usr/bin/env python3
"""`research/_ollama_pacer.py` (R-v2-ports, commit 1 of the plan): pacing + retry for
Windows ephemeral-port exhaustion against a local Ollama, patched transparently under
`urllib.request.urlopen` / `httpx.Client.send` / `httpx.AsyncClient.send`.

Every network call in this suite is FAKE (a monkeypatched `urllib.request.urlopen`, an
`httpx.MockTransport`) and every sleep is a FAKE CLOCK (`FakeClock`, injected through the
module's own `_now`/`_sleep`/`_async_sleep` seams) - no real socket opens, no real 15 s
wait, no Ollama, no network. `_isolated()` resets every test to zero counters, an
un-patched urllib/httpx, and restores exactly that on exit.

    python tests/research/_test_ollama_pacer.py
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import _env_guard  # noqa: F401, E402 - hermetic store before any project import
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "nevertwice"))
import _ollama_pacer as pacer  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


class FakeClock:
    """A monotonic clock this suite drives by hand - `sleep()` ADVANCES the clock rather
    than blocking, so a 16-retry, 240-second exhaustion (T4) or a 10-call pacing floor
    (T5) costs microseconds here instead of minutes."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept = 0.0
        self.sleeps: list = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept += s
        self.sleeps.append(s)
        self.t += s

    async def async_sleep(self, s: float) -> None:
        self.sleep(s)


class _JsonResp:
    """A urllib-style response: `.read()` + context-manager protocol, the shape
    `_embed_http` (`nevertwice/_engine_store.py`) expects back from `urlopen`."""

    def __init__(self, payload: dict) -> None:
        self._p = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@contextlib.contextmanager
def _crash_guard(*names: str):
    """K9 (the auditor's finding on 717f482): T2/T4/T7 called `urllib.request.urlopen`/
    an httpx client method directly, with no try/except - a plausible regression (e.g.
    `is_ollama_host` mutated to always return False, or MAX_RETRIES mutated to allow at
    most one attempt) raises an exception THROUGH the check() calls that were supposed to
    grade it, crashing the whole suite process and silently skipping every test after the
    one that happened to trip it - the worst failure mode a test harness can have: fewer
    FAIL lines than a real regression should produce, not more.

    Wrap the risky call(s) in `with _crash_guard("name of every check the block would
    otherwise make"): ...` - on ANY exception, every name is reported as a FAIL carrying
    the exception's repr, the exception is swallowed, and `main()`'s loop moves on to the
    next test exactly as if this one block had simply failed its assertions."""
    try:
        yield
    except Exception as exc:                                          # noqa: BLE001
        for n in names:
            check(n, False, repr(exc))


@contextlib.contextmanager
def _isolated():
    """Every test starts from zero counters, an un-patched urllib/httpx and the real
    clock, and leaves exactly that behind on exit (success or failure) - module-global
    state by design, so nothing here may leak from one test into the next.

    K9: also captures httpx.Client.send/AsyncClient.send (when httpx is importable)
    BEFORE the test runs, and after `pacer.uninstall()` asserts - by IDENTITY, not just
    "behaves the same" - that all three patched callables are back to exactly the
    objects saved here. A mutated `uninstall()` that forgets to restore one of them
    leaves the NEXT test's `install()` capturing the wrapper as its own "original"
    (`_ORIG["httpx_send"] = httpx.Client.send` when that IS still `_paced_httpx_send`),
    which self-references into an eventual RecursionError several tests later rather
    than failing where the actual bug is - this check reddens AT THE TEST THAT BROKE IT,
    by name, every single time (checked after every test, not just once)."""
    assert not pacer.installed(), "a previous test left the pacer installed"
    saved_now, saved_sleep, saved_async_sleep = pacer._now, pacer._sleep, pacer._async_sleep
    saved_urlopen = urllib.request.urlopen
    try:
        import httpx as _httpx
        saved_httpx_send = _httpx.Client.send
        saved_httpx_async_send = _httpx.AsyncClient.send
    except ImportError:
        _httpx = None
        saved_httpx_send = saved_httpx_async_send = None
    try:
        import requests as _requests
        saved_requests_send = _requests.Session.send
    except ImportError:
        _requests = None
        saved_requests_send = None
    try:
        import aiohttp as _aiohttp
        saved_aiohttp_request = _aiohttp.ClientSession._request
    except ImportError:
        _aiohttp = None
        saved_aiohttp_request = None
    pacer._reset_for_tests()
    try:
        yield
    finally:
        # The "saved originals" a test's OWN uninstall() must restore to are whatever
        # install() itself captured into `_ORIG` - NOT `saved_urlopen`/`saved_httpx_send`
        # above, which several tests (T2/T3/T4/T6) deliberately overwrite with their own
        # fake urlopen BEFORE calling install(), so install() legitimately captures THAT
        # as "the original" for the duration of the test. Peeked from `_ORIG` here, right
        # before `uninstall()` pops it, so the check verifies uninstall()'s own fidelity
        # to what install() saved - independent of what any given test's pre-install
        # stub happened to be. `saved_urlopen`/`saved_httpx_send`/etc (captured at entry,
        # before the test touched anything) are still what this function forcibly
        # restores to afterward, for full isolation regardless of whether uninstall()
        # itself is the one under test right now.
        was_installed = pacer.installed()
        pre_orig_urlopen = pacer._ORIG.get("urlopen")
        pre_orig_httpx_send = pacer._ORIG.get("httpx_send")
        pre_orig_httpx_async_send = pacer._ORIG.get("httpx_async_send")
        pre_orig_requests_send = pacer._ORIG.get("requests_send")
        pre_orig_aiohttp_request = pacer._ORIG.get("aiohttp_request")
        pacer.uninstall()
        if was_installed:
            urlopen_ok = urllib.request.urlopen is pre_orig_urlopen
            httpx_ok = True
            detail = f"urlopen restored={urlopen_ok}"
            if _httpx is not None and pre_orig_httpx_send is not None:
                httpx_ok = (_httpx.Client.send is pre_orig_httpx_send and
                           _httpx.AsyncClient.send is pre_orig_httpx_async_send)
                detail += (f", Client.send restored={_httpx.Client.send is pre_orig_httpx_send}, "
                          f"AsyncClient.send restored="
                          f"{_httpx.AsyncClient.send is pre_orig_httpx_async_send}")
            requests_ok = True
            if _requests is not None and pre_orig_requests_send is not None:
                requests_ok = _requests.Session.send is pre_orig_requests_send
                detail += f", requests.Session.send restored={requests_ok}"
            aiohttp_ok = True
            if _aiohttp is not None and pre_orig_aiohttp_request is not None:
                aiohttp_ok = _aiohttp.ClientSession._request is pre_orig_aiohttp_request
                detail += f", aiohttp.ClientSession._request restored={aiohttp_ok}"
            check("uninstall restores urllib.request.urlopen, httpx.Client.send, "
                  "httpx.AsyncClient.send (identity against the saved originals)",
                  urlopen_ok and httpx_ok and requests_ok and aiohttp_ok, detail)
        urllib.request.urlopen = saved_urlopen
        if _httpx is not None:
            _httpx.Client.send = saved_httpx_send
            _httpx.AsyncClient.send = saved_httpx_async_send
        if _requests is not None:
            _requests.Session.send = saved_requests_send
        if _aiohttp is not None:
            _aiohttp.ClientSession._request = saved_aiohttp_request
        pacer._now, pacer._sleep, pacer._async_sleep = saved_now, saved_sleep, saved_async_sleep
        pacer._reset_for_tests()


# ── T1: the classifier - exactly the port-exhaustion signature, nothing wider ──────────

def test_t1_classifier_matches_only_the_port_exhaustion_signature() -> None:
    print("\n- T1: classify() matches ONLY the port-exhaustion signature -")

    class _Obj:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _HTTPErrorLike(_Obj):
        def read(self):
            return self._body.encode("utf-8")

    urllib_addr = _HTTPErrorLike(code=400, _body=f"whatever {pacer.SOCKET_ADDR_MSG} whatever")
    check("urllib-shaped (.code/.read()), socket-address body -> retry",
          pacer.classify(urllib_addr, is_ollama_host=True))
    check("its body is STILL READABLE afterwards (read-once, restored)",
          urllib_addr.read() == urllib_addr._body.encode("utf-8"))

    urllib_buf = _HTTPErrorLike(code=400, _body=f"prefix {pacer.BUFFER_SPACE_MSG} suffix")
    check("urllib-shaped, buffer-space body -> retry",
          pacer.classify(urllib_buf, is_ollama_host=True))

    ollama_err = _Obj(status_code=400, error=f"... {pacer.SOCKET_ADDR_MSG} ...")
    check("ollama.ResponseError-shaped (.status_code/.error, a plain string) -> retry "
          "(matched without importing the real `ollama` package)",
          pacer.classify(ollama_err, is_ollama_host=True))

    httpx_resp = _Obj(status_code=400, text=f"... {pacer.BUFFER_SPACE_MSG} ...")
    check("httpx.Response-shaped (.status_code/.text) -> retry",
          pacer.classify(httpx_resp, is_ollama_host=True))

    # K9 (the auditor's finding on 717f482): iterating `pacer.PORT_WINERRORS` here is a
    # tautology - a mutation that SHRINKS the module's own constant to `(10048,)` leaves
    # this loop green too, since it only ever asks the mutated module about itself. Both
    # winerrors are hardcoded literals instead, so a mutated constant is checked against
    # an expectation the mutation cannot also move.
    check("10048 is retried", pacer.classify(_Obj(winerror=10048), is_ollama_host=True))
    check("10055 is retried", pacer.classify(_Obj(winerror=10055), is_ollama_host=True))

    # ── near-misses: every one of these must NOT retry ──
    near_ctxlen = _HTTPErrorLike(code=400, _body="the input exceeds the context length")
    check("near-miss: 400 context-length -> no retry",
          not pacer.classify(near_ctxlen, is_ollama_host=True))
    near_buffer_alone = _HTTPErrorLike(code=400, _body="a buffer was involved somewhere")
    check("near-miss: 400 'buffer' alone, not the full phrase -> no retry",
          not pacer.classify(near_buffer_alone, is_ollama_host=True))
    near_500 = _HTTPErrorLike(code=500, _body=pacer.SOCKET_ADDR_MSG)
    check("near-miss: 500 with the exact string -> no retry (wrong status)",
          not pacer.classify(near_500, is_ollama_host=True))
    near_10061 = _Obj(winerror=10061)
    check("near-miss: winerror 10061 (connection refused - Ollama is really down) -> no retry",
          not pacer.classify(near_10061, is_ollama_host=True))
    near_timeout = _Obj()
    check("near-miss: a bare timeout (no code/status/winerror at all) -> no retry",
          not pacer.classify(near_timeout, is_ollama_host=True))
    near_other_host = _HTTPErrorLike(code=400, _body=pacer.SOCKET_ADDR_MSG)
    check("near-miss: the exact signature but a non-Ollama host -> no retry",
          not pacer.classify(near_other_host, is_ollama_host=False))

    # ── mutations: each removes exactly one guard, and reddens exactly the near-miss
    #    that guard exists for ──
    saved_classify = pacer.classify

    def _no_status_check(obj, *, is_ollama_host):
        if not is_ollama_host:
            return False
        we = pacer._winerror_of(obj)
        if we is not None:
            return we in pacer.PORT_WINERRORS
        return pacer._is_port_exhaustion_text(pacer._body_of(obj))  # status never checked
    pacer.classify = _no_status_check
    try:
        check("mutation 'drop status check': the 500 near-miss now WRONGLY retries "
              "(would FAIL 'near-miss: 500 with the exact string' above)",
              pacer.classify(near_500, is_ollama_host=True))
    finally:
        pacer.classify = saved_classify

    saved_text = pacer._is_port_exhaustion_text
    pacer._is_port_exhaustion_text = lambda body: "buffer" in (body or "")
    try:
        check("mutation 'match buffer' (bare substring, not the full phrase): the "
              "bare-'buffer' near-miss now WRONGLY retries (would FAIL the near-miss "
              "check above)",
              pacer.classify(near_buffer_alone, is_ollama_host=True))
    finally:
        pacer._is_port_exhaustion_text = saved_text

    def _no_host_filter(obj, *, is_ollama_host):
        we = pacer._winerror_of(obj)
        if we is not None:
            return we in pacer.PORT_WINERRORS
        status = getattr(obj, "code", None)
        if status is None:
            status = getattr(obj, "status_code", None)
        if status != 400:
            return False
        return pacer._is_port_exhaustion_text(pacer._body_of(obj))  # is_ollama_host unread
    pacer.classify = _no_host_filter
    try:
        check("mutation 'drop host filter': the other-host near-miss now WRONGLY retries "
              "(would FAIL 'near-miss: ... a non-Ollama host' above)",
              pacer.classify(near_other_host, is_ollama_host=False))
    finally:
        pacer.classify = saved_classify


# ── T2: a retry resends the IDENTICAL request object, not a rebuilt one ────────────────

def test_t2_retry_resends_the_identical_request_object() -> None:
    print("\n- T2: a retried urlopen call resends the SAME Request object and bytes -")
    with _isolated():
        clock = FakeClock()
        pacer._now, pacer._sleep = clock.now, clock.sleep
        req = urllib.request.Request("http://127.0.0.1:11434/api/embed", data=b'{"a":1}')
        seen_ids: list = []
        attempts = {"n": 0}

        def flaky(r, *a, **k):
            seen_ids.append(id(r))
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise urllib.error.HTTPError(
                    r.full_url, 400, "Bad Request", {},
                    io.BytesIO(pacer.SOCKET_ADDR_MSG.encode("utf-8")))
            return "OK"
        urllib.request.urlopen = flaky
        pacer.install()
        # K9: a plausible regression here (is_ollama_host mutated to always return
        # False - MP1; MAX_RETRIES mutated to allow at most one attempt - MP2) makes
        # this call raise the underlying HTTPError on attempt 1 or 2, UNCAUGHT - without
        # this guard that crashes the whole suite process and every test after this one
        # silently never runs. Names every check this block would otherwise make.
        with _crash_guard("the call eventually succeeds",
                          "exactly 3 attempts were made (2 failures + 1 success)",
                          "every attempt reused the IDENTICAL Request object (same id())",
                          "retries counted = 2"):
            result = urllib.request.urlopen(req)
            check("the call eventually succeeds", result == "OK")
            check("exactly 3 attempts were made (2 failures + 1 success)",
                  attempts["n"] == 3, str(attempts["n"]))
            check("every attempt reused the IDENTICAL Request object (same id())",
                  len(set(seen_ids)) == 1 and seen_ids[0] == id(req), str(seen_ids))
            check("retries counted = 2", pacer.snapshot()["retries"] == 2)

        # mutation: rebuild the request from scratch on every attempt (same bytes, a
        # NEW object) instead of resending the one the caller handed us
        attempts["n"] = 0
        seen_ids.clear()

        def _rebuilding_paced_urlopen(*args, **kwargs):
            r = args[0] if args else kwargs.get("url")
            host, port = pacer._host_port(r.full_url if hasattr(r, "full_url") else str(r))
            orig = pacer._ORIG["urlopen"]
            if not pacer.is_ollama_host(host, port):
                return orig(*args, **kwargs)

            def _call():
                rebuilt = urllib.request.Request(r.full_url, data=r.data,
                                                  headers=dict(r.headers))
                return orig(rebuilt, *args[1:], **kwargs)
            return pacer._run_paced(_call)
        urllib.request.urlopen = _rebuilding_paced_urlopen
        with _crash_guard("mutation 'rebuild request': the attempts no longer share the "
                         "SAME object id (would FAIL the identical-object check above)"):
            urllib.request.urlopen(req)
            check("mutation 'rebuild request': the attempts no longer share the SAME object "
                  "id (would FAIL the identical-object check above)",
                  len(set(seen_ids)) > 1, str(seen_ids))


# ── T3: a non-port HTTPError is re-raised once, its body still readable afterwards ─────

def test_t3_a_non_port_httperror_is_reraised_with_its_body_still_readable() -> None:
    print("\n- T3: a non-port HTTPError propagates once, body still readable by the caller -")
    with _isolated():
        body = b"the input exceeds the context length"

        def always_wrong_kind(r, *a, **k):
            return_url = r if isinstance(r, str) else r.full_url
            raise urllib.error.HTTPError(return_url, 400, "Bad Request", {}, io.BytesIO(body))
        urllib.request.urlopen = always_wrong_kind
        pacer.install()
        req = urllib.request.Request("http://127.0.0.1:11434/api/embed", data=b"{}")
        raised = None
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            raised = e
        check("the HTTPError propagates (not swallowed, not retried forever)",
              raised is not None)
        check("its body is STILL READABLE by the caller (restored after classification)",
              raised is not None and raised.read() == body,
              str(raised and raised.read()))
        check("no retry was counted (this is not the port-exhaustion signature)",
              pacer.snapshot()["retries"] == 0)

        # mutation: read the body to classify it, but never put it back
        saved_restore = pacer._read_and_restore_body

        def _no_restore(exc):
            try:
                raw = exc.read()
            except Exception:                                        # noqa: BLE001
                return ""
            return raw.decode("utf-8", "replace")                    # .fp never restored
        pacer._read_and_restore_body = _no_restore
        try:
            raised2 = None
            try:
                urllib.request.urlopen(req)
            except urllib.error.HTTPError as e:
                raised2 = e
            check("mutation 'skip restore': the SAME error's body is now UNREADABLE the "
                  "second time (would FAIL the readable-body check above)",
                  raised2 is not None and raised2.read() == b"",
                  str(raised2 and raised2.read()))
        finally:
            pacer._read_and_restore_body = saved_restore


# ── T4: exhaustion - 17 straight failures, the original error, gave_up=1 ──────────────

def test_t4_seventeen_failures_exhaust_retries_and_raise_the_original_error() -> None:
    print("\n- T4: 17 straight port-exhaustion failures -> the original error, gave_up=1 -")
    with _isolated():
        clock = FakeClock()
        pacer._now, pacer._sleep = clock.now, clock.sleep
        attempts = {"n": 0}

        def always_fails(r, *a, **k):
            attempts["n"] += 1
            raise urllib.error.HTTPError(
                r.full_url, 400, "Bad Request", {},
                io.BytesIO(pacer.SOCKET_ADDR_MSG.encode("utf-8")))
        urllib.request.urlopen = always_fails
        pacer.install()
        req = urllib.request.Request("http://127.0.0.1:11434/api/embed", data=b"{}")
        # K9: the inner except is narrowly typed to urllib.error.HTTPError (the EXPECTED
        # failure) on purpose, so it can still inspect `raised`; the outer guard is the
        # net for anything else a regression could raise (MP1/MP2 - see T2's comment).
        with _crash_guard("the ORIGINAL error still propagates after exhausting retries",
                          f"exactly {pacer.MAX_RETRIES + 1} attempts were made (1 + "
                          "MAX_RETRIES retries) = 17",
                          f"retries counted = MAX_RETRIES = {pacer.MAX_RETRIES}",
                          "gave_up counted = 1",
                          f"total retry sleep = MAX_RETRIES x {pacer.RETRY_INTERVAL_S}s = "
                          f"{pacer.MAX_RETRIES * pacer.RETRY_INTERVAL_S}s (2x TIME_WAIT)"):
            raised = None
            try:
                urllib.request.urlopen(req)
            except urllib.error.HTTPError as e:
                raised = e
            check("the ORIGINAL error still propagates after exhausting retries",
                  raised is not None)
            check(f"exactly {pacer.MAX_RETRIES + 1} attempts were made (1 + MAX_RETRIES "
                  f"retries) = 17", attempts["n"] == pacer.MAX_RETRIES + 1 == 17,
                  str(attempts["n"]))
            snap = pacer.snapshot()
            check(f"retries counted = MAX_RETRIES = {pacer.MAX_RETRIES}",
                  snap["retries"] == pacer.MAX_RETRIES, str(snap))
            check("gave_up counted = 1", snap["gave_up"] == 1, str(snap))
            check(f"total retry sleep = MAX_RETRIES x {pacer.RETRY_INTERVAL_S}s = "
                  f"{pacer.MAX_RETRIES * pacer.RETRY_INTERVAL_S}s (2x TIME_WAIT)",
                  abs(snap["retry_sleep_s"] - pacer.MAX_RETRIES * pacer.RETRY_INTERVAL_S) < 1e-6,
                  str(snap))
            # F1: this request was to /api/embed - giving up on it is ALSO an embed
            # failure (P0(a)'s own concern), tallied separately from the general gave_up
            # above so a reader can ask "did an EMBED call give up" without re-deriving
            # it from the URL of every retried call.
            check("F1: the embed-specific gave_up counter also reads 1 (this request "
                  "was to /api/embed)", snap["_embed_failures"]["gave_up"] == 1, str(snap))


# ── T5: pacing floor, and call_ms excludes every sleep ─────────────────────────────────

def test_t5_pacing_enforces_the_floor_gap_and_call_ms_excludes_sleep() -> None:
    print("\n- T5: 10 successive calls sleep >= 9 gaps; call_ms excludes the pacing sleep -")
    with _isolated():
        clock = FakeClock()
        pacer._now, pacer._sleep = clock.now, clock.sleep
        saved_pace_s = pacer.PACE_S
        pacer.PACE_S = 0.125          # hermetic regardless of NEVERTWICE_OLLAMA_PACE_S in env
        try:
            for _ in range(10):
                pacer._run_paced(lambda: "ok")     # zero simulated network cost - isolates
                                                    # the pacing math from a second variable
            snap = pacer.snapshot()
            floor = 9 * pacer.PACE_S  # 10 calls -> 9 gaps; the first call never waits
            check(f"total pace_sleep_s >= {floor}s over 10 calls (9 inter-call gaps at the "
                  f"{pacer.PACE_S}s floor)", snap["pace_sleep_s"] >= floor - 1e-9, str(snap))
            out: dict = {}
            pacer.attach(out)
            cm = out["ollama_transport"]["call_ms"]
            check("call_ms n == 10 (one successful attempt recorded per call)",
                  cm["n"] == 10, str(cm))
            check(f"call_ms EXCLUDES the pacing sleep (max << the {pacer.PACE_S * 1000:.0f}ms "
                  "gap; a wrapper that timed the sleep too would read ~125ms here instead)",
                  cm["max"] < pacer.PACE_S * 1000 / 2, str(cm))
        finally:
            pacer.PACE_S = saved_pace_s


# ── T6: the real engine, untouched, transparently rides through two port failures ──────

def test_t6_engine_embed_text_transparently_survives_two_port_failures() -> None:
    print("\n- T6: memory_hook.embed_text (real engine code) survives two port failures, "
          "via urllib.request.urlopen alone -")
    import memory_hook as m  # noqa: PLC0415
    with _isolated():
        clock = FakeClock()
        pacer._now, pacer._sleep = clock.now, clock.sleep
        attempts = {"n": 0}
        vector = [0.1, 0.2, 0.3]

        def flaky(req, *a, **k):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise urllib.error.HTTPError(
                    req.full_url, 400, "Bad Request", {},
                    io.BytesIO(pacer.SOCKET_ADDR_MSG.encode("utf-8")))
            return _JsonResp({"embeddings": [vector]})
        urllib.request.urlopen = flaky
        pacer.install()
        with _crash_guard("embed_text returns the real vector after 2 port failures, "
                          "transparently",
                          "attempted 3 times underneath (2 failures + 1 success)",
                          "no 'HTTP 400' reached the engine's own logger - the pacer "
                          "absorbed both failures beneath _embed_http entirely"):
            with mock.patch.object(m, "EMBED_PROVIDER", "ollama"), \
                 mock.patch.object(m, "OLLAMA_EMBED_URL", "http://127.0.0.1:11434/api/embed"), \
                 mock.patch.object(m, "_EMBED_TEXT_MEMO", {"key": None, "vec": None}):
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    vec = m.embed_text("hello world")
            check("embed_text returns the real vector after 2 port failures, transparently",
                  vec == vector, str(vec))
            check("attempted 3 times underneath (2 failures + 1 success)",
                  attempts["n"] == 3, str(attempts["n"]))
            check("no 'HTTP 400' reached the engine's own logger - the pacer absorbed both "
                  "failures beneath _embed_http entirely", "HTTP 400" not in buf.getvalue(),
                  buf.getvalue())

        # mutation: uninstall the pacer - the SAME two-failure flake now returns None,
        # and the engine's own log DOES see the HTTP 400 (proving the WIRING, not the
        # engine's own retry logic - it has none here - is what makes the block above pass)
        pacer.uninstall()
        attempts["n"] = 0
        with _crash_guard("mutation 'uninstall': WITHOUT the pacer the same flake now "
                          "returns None (would FAIL the real-vector check above)",
                          "mutation 'uninstall': the engine's own log now DOES see "
                          "'HTTP 400'"):
            with mock.patch.object(m, "EMBED_PROVIDER", "ollama"), \
                 mock.patch.object(m, "OLLAMA_EMBED_URL", "http://127.0.0.1:11434/api/embed"), \
                 mock.patch.object(m, "_EMBED_TEXT_MEMO", {"key": None, "vec": None}):
                buf2 = io.StringIO()
                with contextlib.redirect_stderr(buf2):
                    vec2 = m.embed_text("hello world")
            check("mutation 'uninstall': WITHOUT the pacer the same flake now returns None "
                  "(would FAIL the real-vector check above)", vec2 is None, str(vec2))
            check("mutation 'uninstall': the engine's own log now DOES see 'HTTP 400'",
                  "HTTP 400" in buf2.getvalue(), buf2.getvalue())


# ── T7: httpx.Client / httpx.AsyncClient, via MockTransport - plain, stream, async ─────

def test_t7_httpx_client_and_asyncclient_are_paced_through_mocktransport() -> None:
    print("\n- T7: httpx.Client.send / AsyncClient.send paced+retried via MockTransport -")
    try:
        import httpx
    except ImportError:
        check("httpx importable (research extra) - this environment lacks it; every "
              "other T7 assertion is skipped, not failed", True,
              "install the `research` extra to exercise T7's httpx path")
        return
    with _isolated():
        clock = FakeClock()
        pacer._now, pacer._sleep, pacer._async_sleep = (clock.now, clock.sleep,
                                                        clock.async_sleep)
        pacer.install()

        # plain (non-streaming): two port-exhaustion 400s, then 200
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] <= 2:
                return httpx.Response(400, text=pacer.SOCKET_ADDR_MSG)
            return httpx.Response(200, json={"ok": True})
        # K9: every direct client call below is a call into REAL httpx machinery, which
        # is exactly where MP4 (a broken uninstall() - see _isolated()'s docstring)
        # manifests as a RecursionError, and where MP1/MP2 (see T2) would surface too if
        # this test ran before T2. Guarded so any of those redden by name instead of
        # crashing the suite.
        with _crash_guard("plain request eventually succeeds (200) after 2 paced retries",
                          "3 attempts were made underneath (2 retried 400s + 1 success)"):
            client = httpx.Client(transport=httpx.MockTransport(handler),
                                  base_url="http://127.0.0.1:11434")
            resp = client.get("/api/tags")
            client.close()
            check("plain request eventually succeeds (200) after 2 paced retries",
                  resp.status_code == 200, str(resp.status_code))
            check("3 attempts were made underneath (2 retried 400s + 1 success)",
                  calls["n"] == 3, str(calls["n"]))

        # streaming: a 400 with the exact signature is PACED but never retried on body
        # content - reading the body here would consume the caller's own stream (R3)
        stream_calls = {"n": 0}

        def stream_handler(request):
            stream_calls["n"] += 1
            return httpx.Response(400, text=pacer.SOCKET_ADDR_MSG)
        with _crash_guard("a streamed 400 passes through UNRETRIED (its body was never "
                          "consumed here to classify it)",
                          "exactly 1 attempt for the streamed call (no retry on a "
                          "streamed body)"):
            sclient = httpx.Client(transport=httpx.MockTransport(stream_handler),
                                   base_url="http://127.0.0.1:11434")
            with sclient.stream("GET", "/api/generate") as sresp:
                check("a streamed 400 passes through UNRETRIED (its body was never consumed "
                      "here to classify it)", sresp.status_code == 400, str(sresp.status_code))
            sclient.close()
            check("exactly 1 attempt for the streamed call (no retry on a streamed body)",
                  stream_calls["n"] == 1, str(stream_calls["n"]))

        # async: two port-exhaustion 400s, then 200, via an async handler
        async def _run_async():
            acalls = {"n": 0}

            async def ahandler(request):
                acalls["n"] += 1
                if acalls["n"] <= 2:
                    return httpx.Response(400, text=pacer.BUFFER_SPACE_MSG)
                return httpx.Response(200, json={"ok": True})
            aclient = httpx.AsyncClient(transport=httpx.MockTransport(ahandler),
                                        base_url="http://127.0.0.1:11434")
            r = await aclient.get("/api/tags")
            await aclient.aclose()
            return r, acalls["n"]
        with _crash_guard("async request eventually succeeds (200) after 2 paced retries",
                          "3 async attempts were made underneath"):
            aresp, an = asyncio.run(_run_async())
            check("async request eventually succeeds (200) after 2 paced retries",
                  aresp.status_code == 200, str(aresp.status_code))
            check("3 async attempts were made underneath", an == 3, str(an))

        snap = pacer.snapshot()
        check("retries counted across the plain+async requests (2 + 2 = 4; the streamed "
              "400 contributes none)", snap["retries"] == 4, str(snap))


# ── T8: attach(out) - writes the key only when this module actually did something ──────

def test_t8_attach_writes_the_key_only_when_something_ran_through_it() -> None:
    print("\n- T8: attach(out) writes ollama_transport only when install()'d AND used -")
    with _isolated():
        out: dict = {}
        pacer.attach(out)
        check("never installed, never called -> attach() writes NOTHING",
              "ollama_transport" not in out, str(out))

        pacer.install()
        out2: dict = {}
        pacer.attach(out2)
        check("installed but zero calls through it -> attach() still writes NOTHING",
              "ollama_transport" not in out2, str(out2))

        clock = FakeClock()
        pacer._now, pacer._sleep = clock.now, clock.sleep

        def ok():
            return "ok"
        before = pacer.snapshot()
        pacer._run_paced(ok)
        pacer._run_paced(ok)
        out3: dict = {}
        pacer.attach(out3)
        check("one call went through -> attach() DOES write the key",
              "ollama_transport" in out3, str(out3))
        ot = out3.get("ollama_transport", {})
        check("calls == 2 (cumulative, no `since`)", ot.get("calls") == 2, str(ot))
        check("shape carries every documented field",
              {"calls", "pace_sleep_s", "retries", "retry_sleep_s", "gave_up", "call_ms",
               "timing_includes_pacing", "pace_floor_s"} <= set(ot),
              str(sorted(ot)))
        check("timing_includes_pacing is True", ot.get("timing_includes_pacing") is True)
        check("pace_floor_s == calls * PACE_S",
              abs(ot["pace_floor_s"] - ot["calls"] * pacer.PACE_S) < 1e-9, str(ot))

        # a per-arm DELTA (head_to_head's own usage: snapshot before, run, attach(since=))
        pacer._run_paced(ok)
        out4: dict = {}
        pacer.attach(out4, since=before)
        check("attach(since=snapshot taken before all 3 calls) reports the full delta (3)",
              out4["ollama_transport"]["calls"] == 3, str(out4))

        mid = pacer.snapshot()
        out5: dict = {}
        pacer.attach(out5, since=mid)
        check("attach(since=a snapshot taken AFTER the last call) writes nothing - this "
              "arm made no calls of its OWN in that span",
              "ollama_transport" not in out5, str(out5))


def test_t8b_calls_by_host_distinguishes_two_recognised_ollama_hosts() -> None:
    print("\n- T8b: calls_by_host() tallies paced calls PER (host, port) - a probe or a "
          "stand reachable from more than one recognised Ollama host can tell them apart -")
    with _isolated():
        clock = FakeClock()
        pacer._now, pacer._sleep = clock.now, clock.sleep
        req_a = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        req_b = urllib.request.Request("http://localhost:11434/api/tags")

        def ok(*a, **k):
            return "OK"
        urllib.request.urlopen = ok
        pacer.install()
        snap = pacer.snapshot()
        urllib.request.urlopen(req_a)
        urllib.request.urlopen(req_a)
        urllib.request.urlopen(req_b)
        delta = pacer.calls_by_host(since=snap)
        check("host A (127.0.0.1:11434) tallied twice", delta.get(("127.0.0.1", 11434)) == 2,
              str(delta))
        check("host B (localhost:11434) tallied once", delta.get(("localhost", 11434)) == 1,
              str(delta))
        check("per-host sums equal the aggregate calls delta",
              sum(delta.values()) == pacer.snapshot()["calls"] - snap["calls"], str(delta))
        check("a host with zero calls in this span is absent, not zero-valued",
              ("::1", 11434) not in delta, str(delta))


def test_t8b_async_calls_by_host_via_httpx_asyncclient() -> None:
    print("\n- T8b async (item 8, .loop/HANDOFF-PORTS.md): calls_by_host() tallies the "
          "ASYNC httpx.AsyncClient.send path per (host, port) too - the sync case above "
          "never exercises _run_paced_async's OWN host tally (a different code path, a "
          "different `with _LOCK: _CALLS_BY_HOST[host_key] = ...` line) at all -")
    try:
        import httpx
    except ImportError:
        check("httpx importable (research extra) - this environment lacks it; every "
              "other T8b-async assertion is skipped, not failed", True,
              "install the `research` extra to exercise T8b-async's httpx path")
        return
    with _isolated():
        def handler(request):
            return httpx.Response(200, json={"ok": True})

        async def _run_two_hosts():
            client_a = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                         base_url="http://127.0.0.1:11434")
            await client_a.get("/api/tags")
            await client_a.get("/api/tags")
            await client_a.aclose()
            client_b = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                         base_url="http://localhost:11434")
            await client_b.get("/api/tags")
            await client_b.aclose()
        pacer.install()
        snap = pacer.snapshot()
        with _crash_guard("T8b-async: host A (127.0.0.1:11434) tallied twice via "
                          "AsyncClient",
                          "T8b-async: host B (localhost:11434) tallied once via "
                          "AsyncClient"):
            asyncio.run(_run_two_hosts())
            delta = pacer.calls_by_host(since=snap)
            check("T8b-async: host A (127.0.0.1:11434) tallied twice via AsyncClient",
                  delta.get(("127.0.0.1", 11434)) == 2, str(delta))
            check("T8b-async: host B (localhost:11434) tallied once via AsyncClient",
                  delta.get(("localhost", 11434)) == 1, str(delta))

        # mutation MN2: no per-host tally in _run_paced_async. Wraps the REAL function
        # (so pacing/retry/counters still behave faithfully) and erases only the
        # per-host side effect it just made - the precise, OBSERVABLE shape "no per-host
        # tally" has from calls_by_host()'s own point of view, without duplicating
        # _run_paced_async's internals (which a copy-pasted reimplementation would drift
        # from the moment either one changes).
        pacer._reset_for_tests()
        real_run_paced_async = pacer._run_paced_async

        async def _no_host_tally(call, host_key=None, is_embed=False):
            before = dict(pacer._CALLS_BY_HOST)
            result = await real_run_paced_async(call, host_key=host_key, is_embed=is_embed)
            with pacer._LOCK:
                pacer._CALLS_BY_HOST.clear()
                pacer._CALLS_BY_HOST.update(before)
            return result
        pacer._run_paced_async = _no_host_tally
        try:
            with _crash_guard(
                    "mutation MN2 'no per-host tally in _run_paced_async': the SAME "
                    "two-host traffic is now WRONGLY invisible to calls_by_host() "
                    "(would FAIL the T8b-async checks above), even though the "
                    "aggregate calls counter still moved"):
                snap2 = pacer.snapshot()
                asyncio.run(_run_two_hosts())
                delta2 = pacer.calls_by_host(since=snap2)
                agg2 = pacer.snapshot()["calls"] - snap2["calls"]
                check("mutation MN2 'no per-host tally in _run_paced_async': the SAME "
                      "two-host traffic is now WRONGLY invisible to calls_by_host() "
                      "(would FAIL the T8b-async checks above), even though the "
                      "aggregate calls counter still moved",
                      not delta2 and agg2 == 3, str((delta2, agg2)))
        finally:
            pacer._run_paced_async = real_run_paced_async
        check("_run_paced_async is restored to the real function",
              pacer._run_paced_async is real_run_paced_async)


def test_t8c_max_inflight_tracks_real_concurrency() -> None:
    """R2 (the auditor's finding): `elapsed - pace_sleep_s - retry_sleep_s` (research/
    head_to_head.py's `_pace_excluded`, research/token_floor.py's `finish_arm`) assumes
    the pacer's sleeps never overlap - false the moment two callers are paced at once,
    where the same wall-clock second is double-counted as "pacing time" by each one.
    `max_inflight` is the real mechanism this suite can drive with actual OS threads
    (not the fake clock every other test here uses, since overlap is the exact thing
    under test) - a bounded number, small sleeps, no network."""
    print("\n- T8c: max_inflight - a real high-water mark under genuine thread "
          "concurrency, exact only when nothing ever overlapped -")
    with _isolated():
        def slow_ok():
            time.sleep(0.05)
            return "OK"

        # four REAL threads, each pacing a call at once. PACE_S forced to 0 here: the
        # default 0.125s min-gap between call STARTS is longer than slow_ok's own 0.05s
        # body, so at the default rate no two calls are ever actually inflight together
        # at once (pacing's whole job is to keep starts apart) - found by running this
        # exact scenario BEFORE disabling PACE_S and watching max_inflight read 1.
        saved_pace_s = pacer.PACE_S
        pacer.PACE_S = 0.0
        try:
            snap = pacer.snapshot()
            threads = [threading.Thread(target=pacer._run_paced, args=(slow_ok,),
                                        kwargs={"host_key": ("127.0.0.1", 11434)})
                      for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            pacer.PACE_S = saved_pace_s
        out: dict = {}
        pacer.attach(out, since=snap)
        ot = out["ollama_transport"]
        check("max_inflight reflects genuine overlap (> 1) under real concurrency",
              ot["max_inflight"] > 1, str(ot))
        check("pace_excluded_exact is False once concurrency was ever observed",
              ot["pace_excluded_exact"] is False, str(ot))

        # a FRESH span, entirely serial (one call at a time, no other thread) - exact
        pacer._reset_for_tests()
        snap2 = pacer.snapshot()
        for _ in range(3):
            pacer._run_paced(slow_ok, host_key=("127.0.0.1", 11434))
        out2: dict = {}
        pacer.attach(out2, since=snap2)
        ot2 = out2["ollama_transport"]
        check("max_inflight == 1 for a purely serial span", ot2["max_inflight"] == 1, str(ot2))
        check("pace_excluded_exact is True for a purely serial span",
              ot2["pace_excluded_exact"] is True, str(ot2))

        # mutation: _inflight_enter/_inflight_exit never update max_inflight
        saved_enter = pacer._inflight_enter
        pacer._inflight_enter = lambda: None            # never bumps _COUNTERS["max_inflight"]
        try:
            pacer._reset_for_tests()
            snap3 = pacer.snapshot()
            threads2 = [threading.Thread(target=pacer._run_paced, args=(slow_ok,))
                       for _ in range(4)]
            pacer.PACE_S = 0.0
            try:
                for t in threads2:
                    t.start()
                for t in threads2:
                    t.join()
            finally:
                pacer.PACE_S = saved_pace_s
            out3: dict = {}
            pacer.attach(out3, since=snap3)
            check("mutation 'inflight tracking removed': the SAME 4-thread overlap now "
                  "WRONGLY reads max_inflight<=1 (would FAIL the overlap check above)",
                  out3["ollama_transport"]["max_inflight"] <= 1, str(out3))
        finally:
            pacer._inflight_enter = saved_enter


def test_t8d_max_concurrent_paced_covers_the_whole_paced_operation() -> None:
    """K14 (the auditor's review of 8450a74): T8c above forces `PACE_S = 0.0` for its
    overlap scenario BECAUSE, in its own words, "the default 0.125s min-gap between call
    STARTS is longer than slow_ok's own 0.05s body, so at the default rate no two calls
    are ever actually inflight together" - so `max_inflight`/`pace_excluded_exact` reads
    "exact" at the DEFAULT pacing floor even under real 4-thread concurrency, because it
    only ever watches the call body, never the pacing WAIT. Measured (the auditor's own
    probe): 4 threads, default PACE_S, a 0.05s call body - wall 0.426s, pace_sleep_s
    SUMMED to 0.749s across the four. `max_concurrent_paced` watches the WHOLE paced
    operation (entry, before `_pace()`, to return) and must catch this."""
    print("\n- T8d: K14 - max_concurrent_paced covers the WHOLE paced operation (pacing "
          "wait + call), not just the call body; exact at the DEFAULT PACE_S -")
    with _isolated():
        def slow_ok():
            time.sleep(0.05)
            return "OK"
        # DEFAULT PACE_S this time - no override. The exact scenario T8c's own comment
        # says would (wrongly) read "exact" under the old, call-only tracking.
        snap = pacer.snapshot()
        threads = [threading.Thread(target=pacer._run_paced, args=(slow_ok,),
                                    kwargs={"host_key": ("127.0.0.1", 11434)})
                  for _ in range(4)]
        t_start = pacer._now()
        with _crash_guard("max_inflight (call-only) stays <= 1 at the default pacing "
                          "floor - unchanged by this fix",
                          "max_concurrent_paced (whole operation) reads > 1 - the "
                          "pacing WAITS overlapped even though the calls did not",
                          "pace_excluded_exact is False - the naive elapsed - "
                          "pace_sleep_s - retry_sleep_s subtraction is NOT trustworthy "
                          "here",
                          "the naive subtraction would have gone negative before the "
                          "clamp: pace_sleep_s summed across 4 threads exceeds the "
                          "real wall time"):
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            wall = pacer._now() - t_start
            out: dict = {}
            pacer.attach(out, since=snap)
            ot = out["ollama_transport"]
            check("max_inflight (call-only) stays <= 1 at the default pacing floor - "
                  "unchanged by this fix", ot["max_inflight"] <= 1, str(ot))
            check("max_concurrent_paced (whole operation) reads > 1 - the pacing WAITS "
                  "overlapped even though the calls did not",
                  ot["max_concurrent_paced"] > 1, str(ot))
            check("pace_excluded_exact is False - the naive elapsed - pace_sleep_s - "
                  "retry_sleep_s subtraction is NOT trustworthy here",
                  ot["pace_excluded_exact"] is False, str(ot))
            check("the naive subtraction would have gone negative before the clamp: "
                  "pace_sleep_s summed across 4 threads exceeds the real wall time",
                  ot["pace_sleep_s"] > wall, f"pace_sleep_s={ot['pace_sleep_s']} "
                  f"wall={wall:.3f}")

        # mutation: go back to the call-only window - K14's own regression, simulated by
        # never entering/exiting the WHOLE-operation tracker
        saved_enter, saved_exit = pacer._concurrent_enter, pacer._concurrent_exit
        pacer._concurrent_enter = lambda: None
        pacer._concurrent_exit = lambda: None
        try:
            pacer._reset_for_tests()
            snap2 = pacer.snapshot()
            threads2 = [threading.Thread(target=pacer._run_paced, args=(slow_ok,))
                       for _ in range(4)]
            with _crash_guard("mutation 'go back to the call-only window': the SAME "
                              "default-pace, 4-thread overlap now WRONGLY reads "
                              "pace_excluded_exact=True (would FAIL the exact-is-False "
                              "check above)"):
                for t in threads2:
                    t.start()
                for t in threads2:
                    t.join()
                out2: dict = {}
                pacer.attach(out2, since=snap2)
                check("mutation 'go back to the call-only window': the SAME "
                      "default-pace, 4-thread overlap now WRONGLY reads "
                      "pace_excluded_exact=True (would FAIL the exact-is-False check "
                      "above)", out2["ollama_transport"]["pace_excluded_exact"] is True,
                      str(out2))
        finally:
            pacer._concurrent_enter, pacer._concurrent_exit = saved_enter, saved_exit


# ── F1: failed_outcomes on EMBED endpoints - P0(a) ──────────────────────────────────────
#
# .loop/PREREG-V2-2026-09-24.md, P0(a): `_embed_http` (nevertwice/_engine_store.py) returns
# None on ANY failure and the caller falls back to lexical recall, silently - the one
# failure class this module could always see (it sits below every caller) but never
# counted until now. `failed_outcomes` on an embed endpoint (`/api/embed`, `_is_embed_path`)
# > 0 marks the run invalid, same as a pacer bypass.

def test_f1a_urlopen_embed_failure_marks_the_run_invalid() -> None:
    print("\n- F1a: urlopen raising on /api/embed -> failed_outcomes, run marked invalid -")
    with _isolated():
        # by_status: an HTTPError that is NOT the port-exhaustion signature (a real 500) -
        # never retried, immediately a genuine failure. Installed FIRST (T2/T4's own
        # pattern) so pacer.install() captures IT as "the original" - swapping
        # urllib.request.urlopen again afterward would overwrite the pacer's own
        # wrapper, not the thing underneath it; later scenarios swap `pacer._ORIG
        # ["urlopen"]` instead, for exactly that reason.
        def fails_500(r, *a, **k):
            raise urllib.error.HTTPError(
                r.full_url, 500, "Internal Server Error", {}, io.BytesIO(b"model busy"))
        urllib.request.urlopen = fails_500
        pacer.install()
        req_embed = urllib.request.Request("http://127.0.0.1:11434/api/embed", data=b"{}")
        with _crash_guard("F1a: run marked invalid (by_status)",
                          "by_status tallies the 500 for /api/embed"):
            raised = None
            try:
                urllib.request.urlopen(req_embed)
            except urllib.error.HTTPError as e:
                raised = e
            check("the original 500 still propagates", raised is not None)
            out: dict = {}
            pacer.attach(out)
            ot = out.get("ollama_transport", {})
            check("by_status tallies the 500 for /api/embed",
                  ot.get("failed_outcomes", {}).get("by_status") == {500: 1}, str(ot))
            check("F1a: run marked invalid (by_status)",
                  out.get("valid") is False and "embed" in out.get("invalid_reason", ""),
                  str(out))

        # by_exception_type: a pure transport failure (a timeout) - no status at all.
        # Swaps pacer._ORIG["urlopen"] (what the ALREADY-INSTALLED wrapper calls as "the
        # original"), never urllib.request.urlopen itself, which stays _paced_urlopen.
        pacer._reset_for_tests()

        def fails_timeout(r, *a, **k):
            raise socket.timeout("timed out")
        pacer._ORIG["urlopen"] = fails_timeout
        # socket.timeout is an ALIAS for the builtin TimeoutError since Python 3.10
        # (confirmed: socket.timeout is TimeoutError) - the exception's OWN class name,
        # not the alias it was raised through, is what `type(exc).__name__` reads.
        with _crash_guard("F1a: run marked invalid (by_exception_type)",
                          "by_exception_type tallies 'TimeoutError' for /api/embed"):
            raised2 = None
            try:
                urllib.request.urlopen(req_embed)
            except socket.timeout as e:
                raised2 = e
            check("the original socket.timeout still propagates", raised2 is not None)
            out2: dict = {}
            pacer.attach(out2)
            ot2 = out2.get("ollama_transport", {})
            check("by_exception_type tallies 'TimeoutError' for /api/embed",
                  ot2.get("failed_outcomes", {}).get("by_exception_type") == {"TimeoutError": 1},
                  str(ot2))
            check("F1a: run marked invalid (by_exception_type)",
                  out2.get("valid") is False and "embed" in out2.get("invalid_reason", ""),
                  str(out2))

        # scope: the SAME failure on a NON-embed endpoint (/api/tags) is paced/counted
        # normally but must NOT be tallied into failed_outcomes at all.
        pacer._reset_for_tests()
        req_tags = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with _crash_guard("a non-embed endpoint's failure is not tallied into "
                          "failed_outcomes at all"):
            try:
                urllib.request.urlopen(req_tags)
            except socket.timeout:
                pass
            out3: dict = {}
            pacer.attach(out3)
            ot3 = out3.get("ollama_transport", {})
            fo3 = ot3.get("failed_outcomes", {})
            check("a non-embed endpoint's failure is not tallied into failed_outcomes at "
                  "all", not fo3.get("by_status") and not fo3.get("by_exception_type")
                  and not fo3.get("gave_up"), str(ot3))
            check("... yet the call itself IS still counted/paced normally (calls == 1)",
                  ot3.get("calls") == 1, str(ot3))

        # mutation: F1 removed - _is_embed_path always says "not an embed endpoint"
        pacer._reset_for_tests()
        pacer._ORIG["urlopen"] = fails_500
        saved_is_embed = pacer._is_embed_path
        pacer._is_embed_path = lambda url: False
        try:
            with _crash_guard("mutation 'F1 removed': the SAME by_status failure on "
                              "/api/embed is now WRONGLY never tallied - run stays "
                              "valid (would FAIL the F1a 'run marked invalid' check "
                              "above)"):
                try:
                    urllib.request.urlopen(req_embed)
                except urllib.error.HTTPError:
                    pass
                out4: dict = {}
                pacer.attach(out4)
                check("mutation 'F1 removed': the SAME by_status failure on /api/embed "
                      "is now WRONGLY never tallied - run stays valid (would FAIL the "
                      "F1a 'run marked invalid' check above)",
                      "valid" not in out4, str(out4))
        finally:
            pacer._is_embed_path = saved_is_embed
        check("_is_embed_path is restored to the real function",
              pacer._is_embed_path is saved_is_embed)


def test_f1b_httpx_embed_failure_marks_the_run_invalid() -> None:
    print("\n- F1b: httpx.MockTransport raising ReadTimeout on /api/embed -> "
          "failed_outcomes, run marked invalid -")
    try:
        import httpx
    except ImportError:
        check("httpx importable (research extra) - this environment lacks it; every "
              "other F1b assertion is skipped, not failed", True,
              "install the `research` extra to exercise F1b's httpx path")
        return
    with _isolated():
        pacer.install()

        def handler(request):
            raise httpx.ReadTimeout("timed out", request=request)
        with _crash_guard("F1b: run marked invalid (by_exception_type, ReadTimeout)",
                          "by_exception_type tallies 'ReadTimeout' for /api/embed"):
            client = httpx.Client(transport=httpx.MockTransport(handler),
                                  base_url="http://127.0.0.1:11434")
            raised = None
            try:
                client.post("/api/embed", json={"model": "x", "input": "y"})
            except httpx.ReadTimeout as e:
                raised = e
            client.close()
            check("the original httpx.ReadTimeout still propagates", raised is not None)
            out: dict = {}
            pacer.attach(out)
            ot = out.get("ollama_transport", {})
            check("by_exception_type tallies 'ReadTimeout' for /api/embed",
                  ot.get("failed_outcomes", {}).get("by_exception_type") == {"ReadTimeout": 1},
                  str(ot))
            check("F1b: run marked invalid (by_exception_type, ReadTimeout)",
                  out.get("valid") is False and "embed" in out.get("invalid_reason", ""),
                  str(out))

        # mutation: F1 removed - _record_embed_failure replaced with a no-op
        pacer._reset_for_tests()
        saved_record = pacer._record_embed_failure
        pacer._record_embed_failure = lambda exc: None
        try:
            with _crash_guard("mutation 'F1 removed': the SAME ReadTimeout on "
                              "/api/embed is now WRONGLY never tallied - run stays "
                              "valid (would FAIL the F1b 'run marked invalid' check "
                              "above)"):
                client2 = httpx.Client(transport=httpx.MockTransport(handler),
                                       base_url="http://127.0.0.1:11434")
                try:
                    client2.post("/api/embed", json={"model": "x", "input": "y"})
                except httpx.ReadTimeout:
                    pass
                client2.close()
                out2: dict = {}
                pacer.attach(out2)
                check("mutation 'F1 removed': the SAME ReadTimeout on /api/embed is now "
                      "WRONGLY never tallied - run stays valid (would FAIL the F1b 'run "
                      "marked invalid' check above)", "valid" not in out2, str(out2))
        finally:
            pacer._record_embed_failure = saved_record
        check("_record_embed_failure is restored to the real function",
              pacer._record_embed_failure is saved_record)


def test_f1c_httpx_non2xx_response_without_raising_marks_the_run_invalid() -> None:
    print("\n- F1c/F1b-gap (the auditor's reproduction, .loop/HANDOFF-PORTS.md): httpx "
          "does NOT raise on a non-2xx response by itself - a fake Ollama answering 500 "
          "on /api/embed WITHOUT raising (the shape ollama-python's Client.embed sees "
          "before it calls raise_for_status() on its OWN, outside this module) must "
          "still be tallied -")
    try:
        import httpx
    except ImportError:
        check("httpx importable (research extra) - this environment lacks it; every "
              "other F1c assertion is skipped, not failed", True,
              "install the `research` extra to exercise F1c's httpx path")
        return
    with _isolated():
        pacer.install()

        def handler_500(request):
            return httpx.Response(500, json={"error": "model busy"})
        with _crash_guard("F1c: run marked invalid (by_status, a 500 that was never "
                          "raised)",
                          "by_status tallies the 500 for /api/embed even though httpx "
                          "returned it as a normal response"):
            client = httpx.Client(transport=httpx.MockTransport(handler_500),
                                  base_url="http://127.0.0.1:11434")
            resp = client.post("/api/embed", json={"model": "x", "input": "y"})
            client.close()
            check("httpx itself never raised - the response came back normally",
                  resp.status_code == 500)
            out: dict = {}
            pacer.attach(out)
            ot = out.get("ollama_transport", {})
            check("by_status tallies the 500 for /api/embed even though httpx returned "
                  "it as a normal response",
                  ot.get("failed_outcomes", {}).get("by_status") == {500: 1}, str(ot))
            check("F1c: run marked invalid (by_status, a 500 that was never raised)",
                  out.get("valid") is False and "embed" in out.get("invalid_reason", ""),
                  str(out))

        # (в): the LEGACY /api/embeddings endpoint tallies the same way - _is_embed_path
        # must recognise it too, not just the current /api/embed.
        pacer._reset_for_tests()
        with _crash_guard("(в): by_status tallies the 500 for the LEGACY /api/embeddings "
                          "endpoint too"):
            client_legacy = httpx.Client(transport=httpx.MockTransport(handler_500),
                                         base_url="http://127.0.0.1:11434")
            client_legacy.post("/api/embeddings", json={"model": "x", "prompt": "y"})
            client_legacy.close()
            out_legacy: dict = {}
            pacer.attach(out_legacy)
            check("(в): by_status tallies the 500 for the LEGACY /api/embeddings "
                  "endpoint too",
                  out_legacy.get("ollama_transport", {}).get("failed_outcomes", {})
                  .get("by_status") == {500: 1}, str(out_legacy))

        # async counterpart: _paced_httpx_async_send got the identical fix
        pacer._reset_for_tests()

        async def _run_async():
            aclient = httpx.AsyncClient(transport=httpx.MockTransport(handler_500),
                                        base_url="http://127.0.0.1:11434")
            r = await aclient.post("/api/embed", json={"model": "x", "input": "y"})
            await aclient.aclose()
            return r
        with _crash_guard("F1c async: by_status tallies the 500 for /api/embed via "
                          "AsyncClient too"):
            aresp = asyncio.run(_run_async())
            check("async response also came back normally (no raise)",
                  aresp.status_code == 500)
            out_async: dict = {}
            pacer.attach(out_async)
            check("F1c async: by_status tallies the 500 for /api/embed via AsyncClient "
                  "too",
                  out_async.get("ollama_transport", {}).get("failed_outcomes", {})
                  .get("by_status") == {500: 1}, str(out_async))

        # a genuine port-exhaustion 400 is NOT double-counted by this new check (already
        # routed through the retry loop / gave_up path - T7/F1a own that accounting)
        pacer._reset_for_tests()
        port_calls = {"n": 0}

        def handler_port(request):
            port_calls["n"] += 1
            return httpx.Response(400, text=pacer.SOCKET_ADDR_MSG)
        with _crash_guard("a port-exhaustion 400 is retried, not tallied as a failed "
                          "outcome (no double count with the existing gave_up path)"):
            saved_retries, saved_sleep = pacer.MAX_RETRIES, pacer._sleep
            pacer.MAX_RETRIES = 1
            pacer._sleep = lambda s: None
            try:
                client_port = httpx.Client(transport=httpx.MockTransport(handler_port),
                                           base_url="http://127.0.0.1:11434")
                resp_port = client_port.post("/api/embed", json={"model": "x", "input": "y"})
                client_port.close()
            finally:
                pacer.MAX_RETRIES, pacer._sleep = saved_retries, saved_sleep
            check("the port-exhaustion 400 is handed back as-is after retries exhaust",
                  resp_port.status_code == 400 and port_calls["n"] == 2, str(port_calls))
            out_port: dict = {}
            pacer.attach(out_port)
            fo_port = out_port.get("ollama_transport", {}).get("failed_outcomes", {})
            check("no double count: by_status carries no 400 entry (this is the "
                  "existing gave_up/retry accounting, not F1b's)",
                  not fo_port.get("by_status"), str(out_port))

        # mutation: F1b removed - _maybe_record_embed_response_failure replaced with a no-op
        pacer._reset_for_tests()
        saved_maybe = pacer._maybe_record_embed_response_failure
        pacer._maybe_record_embed_response_failure = lambda response, *, is_embed: None
        try:
            with _crash_guard("mutation 'F1b removed': the SAME 500-without-raising on "
                              "/api/embed is now WRONGLY never tallied - run stays "
                              "valid (would FAIL the F1c 'run marked invalid' check "
                              "above)"):
                client3 = httpx.Client(transport=httpx.MockTransport(handler_500),
                                       base_url="http://127.0.0.1:11434")
                client3.post("/api/embed", json={"model": "x", "input": "y"})
                client3.close()
                out3: dict = {}
                pacer.attach(out3)
                check("mutation 'F1b removed': the SAME 500-without-raising on /api/embed "
                      "is now WRONGLY never tallied - run stays valid (would FAIL the "
                      "F1c 'run marked invalid' check above)",
                      "valid" not in out3, str(out3))
        finally:
            pacer._maybe_record_embed_response_failure = saved_maybe
        check("_maybe_record_embed_response_failure is restored to the real function",
              pacer._maybe_record_embed_response_failure is saved_maybe)


# ── TW1/TW2: the tripwire - requests/aiohttp are COUNTED, never paced or retried ────────
#
# R1 (the auditor's review of 717f482, after the empirical arm-symmetry probe found no
# bypass among the httpx/urllib-based stacks): "symmetry becomes a property of every
# run". `requests` and `aiohttp` are declared dependencies of neither `dev` nor (aiohttp)
# `research` - `requests` WAS added to the `research` extra alongside this commit
# specifically so CI's research job exercises TW1 for real; `aiohttp` was not (heavier),
# so TW2 degrades to a printed note there, the same guard T7 already uses for httpx.

def test_tw1_requests_session_send_is_counted_not_paced_and_marks_invalid() -> None:
    print("\n- TW1: requests.Session.send to the Ollama host is COUNTED (never paced or "
          "retried); a non-Ollama host is not counted; the arm's record is marked invalid -")
    try:
        import requests
    except ImportError:
        check("requests importable (declared in the `research` extra) - this "
              "environment lacks it; every other TW1 assertion is skipped, not failed",
              True, "install the `research` extra to exercise TW1")
        return
    with _isolated():
        class _FakeResp:
            status_code = 200

        def fake_send(self, request, **kwargs):
            return _FakeResp()
        saved_requests_send = requests.Session.send
        requests.Session.send = fake_send
        try:
            pacer.install()
            with _crash_guard("an Ollama-host requests.Session.send is counted as a "
                              "bypass, not raised through"):
                session = requests.Session()
                ollama_req = requests.Request(
                    "GET", "http://127.0.0.1:11434/api/tags").prepare()
                resp = session.send(ollama_req)
                check("the fake response still comes back untouched (count-only, never "
                      "intercepted or blocked)", resp.status_code == 200)
                out: dict = {}
                pacer.attach(out)
                ot = out.get("ollama_transport", {})
                check("bypass_calls.requests == 1 for the Ollama-host request",
                      ot.get("bypass_calls", {}).get("requests") == 1, str(ot))
                check("the arm's record is marked invalid, naming 'requests'",
                      out.get("valid") is False and "requests" in out.get(
                          "invalid_reason", ""), str(out))
                check("calls (the PACED count) stays 0 - requests is counted, never paced",
                      ot.get("calls") == 0, str(ot))

            with _crash_guard("a non-Ollama-host requests.Session.send is NOT counted"):
                other_req = requests.Request("GET", "http://example.com/").prepare()
                session.send(other_req)
                out2: dict = {}
                pacer.attach(out2)
                check("a non-Ollama-host request does not bump bypass_calls.requests "
                      "(still 1, from the Ollama-host call above only)",
                      out2.get("ollama_transport", {}).get("bypass_calls", {})
                      .get("requests") == 1, str(out2))
        finally:
            requests.Session.send = saved_requests_send
            pacer.uninstall()          # end the normal-behaviour section cleanly

        # mutation: "remove the tripwire" - install() that never patches requests at all.
        # A FRESH snapshot is taken right after the uninstall above, so this section's
        # delta cannot be inflated by the requests already counted in the block above.
        def _install_without_tripwire():
            with pacer._LOCK:
                if pacer._INSTALLED:
                    return
                pacer._ORIG["urlopen"] = urllib.request.urlopen
                urllib.request.urlopen = pacer._paced_urlopen
                pacer._INSTALLED = True
        requests.Session.send = fake_send
        try:
            with _crash_guard("mutation 'remove the tripwire': the SAME Ollama-host "
                              "request is now WRONGLY not counted (would FAIL the "
                              "bypass_calls check above)"):
                snap = pacer.snapshot()
                _install_without_tripwire()
                session2 = requests.Session()
                ollama_req2 = requests.Request(
                    "GET", "http://127.0.0.1:11434/api/tags").prepare()
                session2.send(ollama_req2)
                out3: dict = {}
                pacer.attach(out3, since=snap)
                check("mutation 'remove the tripwire': the SAME Ollama-host request is "
                      "now WRONGLY not counted (would FAIL the bypass_calls check above)",
                      out3.get("ollama_transport", {}).get("bypass_calls", {})
                      .get("requests", 0) == 0, str(out3))
        finally:
            requests.Session.send = saved_requests_send


def test_tw2_aiohttp_client_session_request_is_counted_not_paced() -> None:
    print("\n- TW2: aiohttp.ClientSession._request to the Ollama host is COUNTED (never "
          "paced or retried) -")
    try:
        import aiohttp
    except ImportError:
        check("aiohttp importable - this environment lacks it (not a declared "
              "dependency); every other TW2 assertion is skipped, not failed", True,
              "aiohttp is not in the `research` extra - install it manually to "
              "exercise TW2")
        return
    with _isolated():
        class _FakeAResp:
            status = 200

        async def fake_request(self, method, str_or_url, **kwargs):
            return _FakeAResp()
        saved_aiohttp_request = aiohttp.ClientSession._request
        aiohttp.ClientSession._request = fake_request
        try:
            pacer.install()

            async def _drive():
                async with aiohttp.ClientSession() as session:
                    r = await session._request("GET", "http://127.0.0.1:11434/api/tags")
                    await session._request("GET", "http://example.com/")
                    return r
            with _crash_guard("an Ollama-host aiohttp request is counted as a bypass",
                              "a non-Ollama-host aiohttp request is NOT counted",
                              "calls (the PACED count) stays 0 for aiohttp too"):
                r = asyncio.run(_drive())
                check("the fake response still comes back untouched", r.status == 200)
                out: dict = {}
                pacer.attach(out)
                ot = out.get("ollama_transport", {})
                check("an Ollama-host aiohttp request is counted as a bypass",
                      ot.get("bypass_calls", {}).get("aiohttp") == 1, str(ot))
                check("a non-Ollama-host aiohttp request is NOT counted (still 1)",
                      ot.get("bypass_calls", {}).get("aiohttp") == 1, str(ot))
                check("calls (the PACED count) stays 0 for aiohttp too",
                      ot.get("calls") == 0, str(ot))
                check("the arm's record is marked invalid, naming 'aiohttp'",
                      out.get("valid") is False and "aiohttp" in out.get(
                          "invalid_reason", ""), str(out))
        finally:
            aiohttp.ClientSession._request = saved_aiohttp_request


# ── TW3: a bypass NESTED inside an already-paced call is not double-flagged (K11) ──────

def test_tw3_nested_aiohttp_inside_a_paced_httpx_call_is_not_a_bypass() -> None:
    print("\n- TW3: K11 - litellm 1.100.0's async path routes httpx.AsyncClient through "
          "an aiohttp-backed transport; the resulting NESTED aiohttp call must be counted "
          "separately, never flagged as an independent bypass -")
    try:
        import httpx
        import aiohttp
    except ImportError as e:
        check("httpx and aiohttp importable - this environment lacks one; every other "
              "TW3 assertion is skipped, not failed", True, repr(e))
        return
    with _isolated():
        class _FakeAiohttpResp:
            status = 200

        async def fake_aiohttp_request(self, method, str_or_url, **kwargs):
            return _FakeAiohttpResp()
        saved_aiohttp_request = aiohttp.ClientSession._request
        aiohttp.ClientSession._request = fake_aiohttp_request

        class _AiohttpBackedTransport(httpx.AsyncBaseTransport):
            """Stands in for litellm's own `LiteLLMAiohttpTransport`: an httpx transport
            whose `handle_async_request` reaches Ollama through `aiohttp.ClientSession`
            rather than a raw socket - so the SAME logical call is seen twice by this
            module: once as the outer, PACED httpx.AsyncClient.send, and once as the
            inner aiohttp._request it delegates to."""

            async def handle_async_request(self, request):
                async with aiohttp.ClientSession() as session:
                    await session._request("GET", str(request.url))
                return httpx.Response(200, request=request)
        try:
            pacer.install()
            with _crash_guard("paced calls == 1 (the outer httpx.AsyncClient.send)",
                              "bypass_calls.aiohttp == 0 (nested inside a paced call, "
                              "not an independent bypass)",
                              "nested_calls.aiohttp == 1 (the inner aiohttp call, "
                              "counted separately)",
                              "the arm's record is NOT marked invalid - the call was "
                              "correctly paced"):
                async def _drive():
                    client = httpx.AsyncClient(transport=_AiohttpBackedTransport(),
                                               base_url="http://127.0.0.1:11434")
                    r = await client.get("/api/tags")
                    await client.aclose()
                    return r
                resp = asyncio.run(_drive())
                check("the fake response comes back untouched", resp.status_code == 200)
                out: dict = {}
                pacer.attach(out)
                ot = out.get("ollama_transport", {})
                check("paced calls == 1 (the outer httpx.AsyncClient.send)",
                      ot.get("calls") == 1, str(ot))
                check("bypass_calls.aiohttp == 0 (nested inside a paced call, not an "
                      "independent bypass)", ot.get("bypass_calls", {}).get("aiohttp") == 0,
                      str(ot))
                check("nested_calls.aiohttp == 1 (the inner aiohttp call, counted "
                      "separately)", ot.get("nested_calls", {}).get("aiohttp") == 1,
                      str(ot))
                check("the arm's record is NOT marked invalid - the call was correctly "
                      "paced", "valid" not in out, str(out))

            # mutation: drop the contextvar check - every hit counts as a bypass
            async def _no_context_check_aiohttp(self, method, str_or_url, **kwargs):
                orig = pacer._ORIG["aiohttp_request"]
                host, port = pacer._host_port(str(str_or_url))
                if pacer.is_ollama_host(host, port):
                    with pacer._LOCK:
                        pacer._COUNTERS["bypass_aiohttp"] += 1   # _PACED_CONTEXT never read
                return await orig(self, method, str_or_url, **kwargs)
            aiohttp.ClientSession._request = _no_context_check_aiohttp
            try:
                with _crash_guard("mutation 'drop the contextvar check': the SAME "
                                  "nested call is now WRONGLY counted as a bypass "
                                  "(would FAIL the bypass_calls check above)"):
                    resp2 = asyncio.run(_drive())
                    out2: dict = {}
                    pacer.attach(out2)
                    check("mutation 'drop the contextvar check': the SAME nested call "
                          "is now WRONGLY counted as a bypass (would FAIL the "
                          "bypass_calls check above)",
                          out2.get("ollama_transport", {}).get("bypass_calls", {})
                          .get("aiohttp") == 1 and out2.get("valid") is False, str(out2))
            finally:
                aiohttp.ClientSession._request = fake_aiohttp_request
        finally:
            aiohttp.ClientSession._request = saved_aiohttp_request


# ── T9: "observe" mode - item 9A addendum, PREREG-V2 P5 timing claims ──────────────────
#
# guard_bench.py's `ms_per_call` and k8_judge_eval.py's `seconds_per_pair` are registered
# claims that P5 requires measured WITHOUT this module's own artificial pacing - but
# skipping the pacer entirely for those two stands would also hide a failed embed from
# P0(a), and a silent lexical fallback makes the guard FASTER, corrupting the very timing
# P5 exists to protect. `install(mode="observe")` installs the identical hooks with
# pacing and retry both disabled.

def test_t9_observe_mode_paces_nothing_retries_nothing_still_counts_failures() -> None:
    print("\n- T9: install(mode='observe') - zero pacing sleep, zero retries, a bypass/"
          "failed embed still counted, attach() names the mode -")
    with _isolated():
        clock = FakeClock()
        pacer._now, pacer._sleep = clock.now, clock.sleep

        def ok(*a, **k):
            return "OK"
        urllib.request.urlopen = ok
        pacer.install(mode="observe")
        with _crash_guard("observe mode: 10 calls give pace_sleep_s == 0",
                          "observe mode: 10 calls give retries == 0",
                          "attach() writes ollama_transport.mode == 'observe'",
                          "attach() writes timing_includes_pacing == False in observe mode",
                          "attach() writes pace_floor_s == 0.0 in observe mode"):
            for _ in range(10):
                urllib.request.urlopen(
                    urllib.request.Request("http://127.0.0.1:11434/api/tags"))
            snap = pacer.snapshot()
            check("observe mode: 10 calls give pace_sleep_s == 0",
                  snap["pace_sleep_s"] == 0.0, str(snap))
            check("observe mode: 10 calls give retries == 0", snap["retries"] == 0, str(snap))
            out: dict = {}
            pacer.attach(out)
            ot = out.get("ollama_transport", {})
            check("attach() writes ollama_transport.mode == 'observe'",
                  ot.get("mode") == "observe", str(ot))
            check("attach() writes timing_includes_pacing == False in observe mode",
                  ot.get("timing_includes_pacing") is False, str(ot))
            check("attach() writes pace_floor_s == 0.0 in observe mode",
                  ot.get("pace_floor_s") == 0.0, str(ot))

        # a port-exhaustion signature: raised through exactly once, never retried, no sleep
        pacer._reset_for_tests()
        attempts = {"n": 0}

        def always_port_error(r, *a, **k):
            attempts["n"] += 1
            raise urllib.error.HTTPError(
                r.full_url, 400, "Bad Request", {},
                io.BytesIO(pacer.SOCKET_ADDR_MSG.encode("utf-8")))
        pacer._ORIG["urlopen"] = always_port_error
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with _crash_guard("observe mode: a port error is raised once, not retried",
                          "observe mode: no retry sleep was spent",
                          "observe mode: gave_up is still counted (F1's own accounting)"):
            raised = None
            try:
                urllib.request.urlopen(req)
            except urllib.error.HTTPError as e:
                raised = e
            check("observe mode: a port error is raised once, not retried",
                  raised is not None and attempts["n"] == 1, str(attempts))
            snap2 = pacer.snapshot()
            check("observe mode: no retry sleep was spent", snap2["retry_sleep_s"] == 0.0,
                  str(snap2))
            check("observe mode: gave_up is still counted (F1's own accounting)",
                  snap2["gave_up"] == 1, str(snap2))

        # a 500 on /api/embed still marks the run invalid, same as "pace" mode (P0(a))
        pacer._reset_for_tests()

        def embed_500(r, *a, **k):
            raise urllib.error.HTTPError(
                r.full_url, 500, "Internal Server Error", {}, io.BytesIO(b"busy"))
        pacer._ORIG["urlopen"] = embed_500
        req_embed = urllib.request.Request("http://127.0.0.1:11434/api/embed", data=b"{}")
        with _crash_guard("observe mode: a 500 on /api/embed still marks the run invalid"):
            try:
                urllib.request.urlopen(req_embed)
            except urllib.error.HTTPError:
                pass
            out2: dict = {}
            pacer.attach(out2)
            check("observe mode: a 500 on /api/embed still marks the run invalid",
                  out2.get("valid") is False and "embed" in out2.get("invalid_reason", ""),
                  str(out2))

        # mutation: observe mode still sleeping (a plausible regression - _pace() forgot
        # to check _MODE) -> pace_sleep_s > 0, would FAIL the pace_sleep_s==0 check above
        pacer._reset_for_tests()
        pacer._ORIG["urlopen"] = ok
        saved_pace_s = pacer.PACE_S
        pacer.PACE_S = 0.125
        saved_pace = pacer._pace

        def _pace_ignoring_mode() -> None:
            wait = pacer._reserve_slot()
            if wait > 0:
                pacer._sleep(wait)
                with pacer._LOCK:
                    pacer._COUNTERS["pace_sleep_s"] += wait
        pacer._pace = _pace_ignoring_mode
        try:
            with _crash_guard("mutation 'observe still sleeping': pace_sleep_s > 0 "
                              "(would FAIL the pace_sleep_s==0 check above)"):
                urllib.request.urlopen(req)
                urllib.request.urlopen(req)
                snap3 = pacer.snapshot()
                check("mutation 'observe still sleeping': pace_sleep_s > 0 (would FAIL "
                      "the pace_sleep_s==0 check above)", snap3["pace_sleep_s"] > 0,
                      str(snap3))
        finally:
            pacer._pace = saved_pace
            pacer.PACE_S = saved_pace_s
        check("_pace is restored to the real function", pacer._pace is saved_pace)

        # mutation: observe mode still retrying (a plausible regression -
        # _effective_max_retries() forgot to check _MODE) -> retries > 0, would FAIL the
        # retries==0 check above
        pacer._reset_for_tests()
        pacer._ORIG["urlopen"] = always_port_error
        attempts["n"] = 0
        saved_eff = pacer._effective_max_retries
        pacer._effective_max_retries = lambda: pacer.MAX_RETRIES
        try:
            with _crash_guard("mutation 'observe still retrying': retries > 0 (would "
                              "FAIL the retries==0 check above)"):
                try:
                    urllib.request.urlopen(req)
                except urllib.error.HTTPError:
                    pass
                snap4 = pacer.snapshot()
                check("mutation 'observe still retrying': retries > 0 (would FAIL the "
                      "retries==0 check above)", snap4["retries"] > 0, str(snap4))
        finally:
            pacer._effective_max_retries = saved_eff
        check("_effective_max_retries is restored to the real function",
              pacer._effective_max_retries is saved_eff)


# ── T9b: K26 (the auditor's finding, 2026-09-24) - three gaps in T9's own coverage ──────
#
# The auditor's `mut82.py` mutates `research/_ollama_pacer.py` at 82013e4 four ways (M1-M4)
# and reruns this whole file as a subprocess. Before the checks below existed, M1-M3
# survived (this suite stayed green under each): T9 only ever drives observe mode through
# `_pace()` (the SYNC urllib path), never `_pace_async()` (M1); nothing here calls
# `uninstall()` after an `install(mode="observe")` and checks `_MODE` afterward (M2); and
# T8's "shape carries every documented field" check does not even list `mode` among the
# keys it requires, let alone its VALUE in "pace" mode (M3). M4 already dies on T9's own
# "attach() writes timing_includes_pacing == False in observe mode" check.

def test_t9b_async_observe_mode_and_mode_bookkeeping() -> None:
    print("\n- T9b (K26): async observe mode paces nothing either; uninstall() resets "
          "_MODE so a later install() is NOT stuck in 'observe'; a PACE-mode attach() "
          "names its own mode too -")
    try:
        import httpx
    except ImportError:
        check("httpx importable (research extra) - this environment lacks it; every "
              "other T9b assertion is skipped, not failed", True,
              "install the `research` extra to exercise T9b's httpx path")
        return
    with _isolated():
        # (a) K26: the ASYNC httpx.AsyncClient path through observe mode - a SEPARATE code
        # path from T9's sync urllib coverage (`_pace_async`, not `_pace`), with its own
        # `if _MODE == "observe": return` guard (mut82.py's M1 removes exactly this one).
        clock = FakeClock()
        pacer._now, pacer._sleep, pacer._async_sleep = (clock.now, clock.sleep,
                                                         clock.async_sleep)
        saved_pace_s = pacer.PACE_S
        pacer.PACE_S = 0.125          # hermetic regardless of NEVERTWICE_OLLAMA_PACE_S in env

        async def _ten_async_calls():
            async def handler(request):
                return httpx.Response(200, json={"ok": True})
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                       base_url="http://127.0.0.1:11434")
            for _ in range(10):
                await client.get("/api/tags")
            await client.aclose()
        try:
            pacer.install(mode="observe")
            with _crash_guard("T9b: 10 async observe-mode calls give pace_sleep_s == 0",
                              "T9b: 10 async observe-mode calls are still counted (calls "
                              "== 10)"):
                asyncio.run(_ten_async_calls())
                snap = pacer.snapshot()
                check("T9b: 10 async observe-mode calls give pace_sleep_s == 0",
                      snap["pace_sleep_s"] == 0.0, str(snap))
                check("T9b: 10 async observe-mode calls are still counted (calls == 10)",
                      snap["calls"] == 10, str(snap))
        finally:
            pacer.uninstall()
            pacer.PACE_S = saved_pace_s

        # (b) K26: install(mode="observe") -> uninstall() -> install() gives mode "pace".
        # Before this check existed, mut82.py's M2 (uninstall() drops its own
        # `_MODE = "pace"` reset) survived: a stand that calls `install(mode="observe")`
        # and later, in the SAME process, `uninstall()` then a plain `install()` (the exact
        # sequence a test suite or a multi-stand campaign driver performs) would silently
        # inherit "observe" pacing-off behaviour for a caller that asked for the default.
        #
        # The FIRST check below (right after `uninstall()`, before any second `install()`
        # call) is the one that actually distinguishes M2: `install()` unconditionally
        # re-derives `_MODE = mode` whenever `_INSTALLED` is False, so a SECOND install()
        # call masks a leaked `_MODE` regardless of whether uninstall() reset it - checking
        # only after the second install() (as the practical consequence reads) would pass
        # under M2 too, and miss it entirely.
        pacer._reset_for_tests()
        pacer.install(mode="observe")
        pacer.uninstall()
        with _crash_guard("T9b: uninstall() itself resets _MODE to 'pace' (checked "
                          "BEFORE any second install() call, which would mask this)"):
            check("T9b: uninstall() itself resets _MODE to 'pace' (checked BEFORE any "
                  "second install() call, which would mask this)",
                  pacer._MODE == "pace", pacer._MODE)
        pacer.install()
        with _crash_guard("T9b: ... and the practical consequence holds too: a later "
                          "plain install() gives mode 'pace'"):
            check("T9b: ... and the practical consequence holds too: a later plain "
                  "install() gives mode 'pace'", pacer._MODE == "pace", pacer._MODE)
        pacer.uninstall()

        # (c) K26: a PACE-mode attach() names its own mode too. T8's "shape carries every
        # documented field" check never listed `mode` among the required keys, so mut82.py's
        # M3 (attach() always writes "mode": "observe", even in pace mode) survived: a
        # reader trusting `ollama_transport.mode == "pace"` to mean "no artificial spacing
        # was excluded" would be told the OPPOSITE of what actually happened.
        pacer._reset_for_tests()
        pacer.install()

        def ok():
            return "ok"
        pacer._run_paced(ok)
        out: dict = {}
        pacer.attach(out)
        with _crash_guard("T9b: a PACE-mode attach() writes ollama_transport.mode == "
                          "'pace'"):
            check("T9b: a PACE-mode attach() writes ollama_transport.mode == 'pace'",
                  out.get("ollama_transport", {}).get("mode") == "pace", str(out))
        pacer.uninstall()


# ── T10: K27 (the auditor's finding, 2026-09-24) - install() rejects a bad mode, and a ──
# second install() in a DIFFERENT mode, instead of silently accepting either ────────────
#
# Before this: `install(mode="obsrve")` (a typo) was accepted and stored verbatim, and
# `attach()` reported `"mode": "obsrve"` back - neither "pace" nor "observe" to a reader
# checking PREREG-V2 P5's own rule. And `install()` then `install(mode="observe")` (the
# auditor's own `observe_probe.py` reproduction) was ALWAYS a silent no-op regardless of
# mode - the pacer stayed stuck in "pace", sleeping and retrying, while the caller believed
# it had switched to observe.

def test_t10_install_rejects_bad_mode_and_silent_mode_switch() -> None:
    print("\n- T10 (K27): install() raises ValueError on a mode outside {'pace','observe'}; "
          "raises RuntimeError on a second install() in a DIFFERENT mode; the SAME mode "
          "twice stays the pre-existing idempotent no-op -")
    with _isolated():
        # (a) an invalid mode is refused outright - never installed, nothing patched.
        with _crash_guard("T10: install(mode='obsrve') (a typo) raises ValueError",
                          "T10: the pacer is NOT installed after the rejected call"):
            raised = None
            try:
                pacer.install(mode="obsrve")
            except ValueError as e:
                raised = e
            check("T10: install(mode='obsrve') (a typo) raises ValueError",
                  raised is not None, str(raised))
            check("T10: the pacer is NOT installed after the rejected call",
                  not pacer.installed())

        # (b) install() then install(mode="observe") - the exact sequence observe_probe.py
        # (the auditor's own reproduction) showed as a silent no-op - now raises, and the
        # pacer stays in its ORIGINAL mode rather than ending up half-switched.
        pacer.install()
        with _crash_guard("T10: install() then install(mode='observe') raises RuntimeError",
                          "T10: ... and the pacer stays in its ORIGINAL mode ('pace'), not "
                          "half-switched"):
            raised2 = None
            try:
                pacer.install(mode="observe")
            except RuntimeError as e:
                raised2 = e
            check("T10: install() then install(mode='observe') raises RuntimeError",
                  raised2 is not None, str(raised2))
            check("T10: ... and the pacer stays in its ORIGINAL mode ('pace'), not "
                  "half-switched", pacer._MODE == "pace", pacer._MODE)
        pacer.uninstall()

        # (c) control: the SAME mode twice is still the pre-existing idempotent no-op -
        # neither new check may make a defensively-repeated install() start raising.
        pacer.install(mode="observe")
        with _crash_guard("T10: control - install(mode='observe') twice in a row does NOT "
                          "raise (still idempotent for the SAME mode)"):
            try:
                pacer.install(mode="observe")
                ok_twice = True
            except (ValueError, RuntimeError):
                ok_twice = False
            check("T10: control - install(mode='observe') twice in a row does NOT raise "
                  "(still idempotent for the SAME mode)", ok_twice)
        pacer.uninstall()
        pacer.install()
        with _crash_guard("T10: control - install() (mode='pace') twice in a row does NOT "
                          "raise either"):
            try:
                pacer.install()
                ok_twice2 = True
            except (ValueError, RuntimeError):
                ok_twice2 = False
            check("T10: control - install() (mode='pace') twice in a row does NOT raise "
                  "either", ok_twice2)
        pacer.uninstall()

        # mutation (rule 1 removed): a minimal reimplementation that drops ONLY the
        # mode-membership check, keeping the different-mode guard intact - the same
        # "reimplement, don't monkeypatch a helper that does not exist" style TW1's own
        # `_install_without_tripwire` uses above for `install()`'s tripwire half.
        def _install_without_mode_validation(mode: str = "pace") -> None:
            with pacer._LOCK:
                if pacer._INSTALLED:
                    if mode != pacer._MODE:
                        raise RuntimeError(f"already installed in mode {pacer._MODE!r}")
                    return
                pacer._MODE = mode
                pacer._ORIG["urlopen"] = urllib.request.urlopen
                urllib.request.urlopen = pacer._paced_urlopen
                pacer._INSTALLED = True
        saved_install = pacer.install
        pacer.install = _install_without_mode_validation
        try:
            with _crash_guard("mutation 'rule 1 removed': install(mode='obsrve') no "
                              "longer raises ValueError (would FAIL the ValueError check "
                              "above)"):
                raised3 = None
                try:
                    pacer.install(mode="obsrve")
                except ValueError as e:
                    raised3 = e
                check("mutation 'rule 1 removed': install(mode='obsrve') no longer raises "
                      "ValueError (would FAIL the ValueError check above)", raised3 is None,
                      str(raised3))
        finally:
            pacer.uninstall()
            pacer.install = saved_install
        check("install is restored to the real function", pacer.install is saved_install)

        # mutation (rule 2 removed): a minimal reimplementation that drops ONLY the
        # already-installed-in-a-different-mode guard, keeping the mode-membership check.
        def _install_without_mode_switch_guard(mode: str = "pace") -> None:
            if mode not in pacer.VALID_MODES:
                raise ValueError(f"bad mode {mode!r}")
            with pacer._LOCK:
                if pacer._INSTALLED:
                    return                                    # the different-mode check removed
                pacer._MODE = mode
                pacer._ORIG["urlopen"] = urllib.request.urlopen
                urllib.request.urlopen = pacer._paced_urlopen
                pacer._INSTALLED = True
        pacer.install = _install_without_mode_switch_guard
        try:
            pacer.install()
            with _crash_guard("mutation 'rule 2 removed': install() then "
                              "install(mode='observe') is WRONGLY a silent no-op again "
                              "(would FAIL the RuntimeError check above), and _MODE stays "
                              "stuck at 'pace'"):
                raised4 = None
                try:
                    pacer.install(mode="observe")
                except RuntimeError as e:
                    raised4 = e
                check("mutation 'rule 2 removed': install() then install(mode='observe') "
                      "is WRONGLY a silent no-op again (would FAIL the RuntimeError check "
                      "above), and _MODE stays stuck at 'pace'",
                      raised4 is None and pacer._MODE == "pace",
                      f"raised4={raised4!r} _MODE={pacer._MODE!r}")
        finally:
            pacer.uninstall()
            pacer.install = saved_install
        check("install is restored to the real function (again)",
              pacer.install is saved_install)


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_t1_classifier_matches_only_the_port_exhaustion_signature,
               test_t2_retry_resends_the_identical_request_object,
               test_t3_a_non_port_httperror_is_reraised_with_its_body_still_readable,
               test_t4_seventeen_failures_exhaust_retries_and_raise_the_original_error,
               test_t5_pacing_enforces_the_floor_gap_and_call_ms_excludes_sleep,
               test_t6_engine_embed_text_transparently_survives_two_port_failures,
               test_t7_httpx_client_and_asyncclient_are_paced_through_mocktransport,
               test_t8_attach_writes_the_key_only_when_something_ran_through_it,
               test_t8b_calls_by_host_distinguishes_two_recognised_ollama_hosts,
               test_t8b_async_calls_by_host_via_httpx_asyncclient,
               test_t8c_max_inflight_tracks_real_concurrency,
               test_t8d_max_concurrent_paced_covers_the_whole_paced_operation,
               test_f1a_urlopen_embed_failure_marks_the_run_invalid,
               test_f1b_httpx_embed_failure_marks_the_run_invalid,
               test_f1c_httpx_non2xx_response_without_raising_marks_the_run_invalid,
               test_tw1_requests_session_send_is_counted_not_paced_and_marks_invalid,
               test_tw2_aiohttp_client_session_request_is_counted_not_paced,
               test_tw3_nested_aiohttp_inside_a_paced_httpx_call_is_not_a_bypass,
               test_t9_observe_mode_paces_nothing_retries_nothing_still_counts_failures,
               test_t9b_async_observe_mode_and_mode_bookkeeping,
               test_t10_install_rejects_bad_mode_and_silent_mode_switch):
        fn()
    print(f"\nollama_pacer: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
