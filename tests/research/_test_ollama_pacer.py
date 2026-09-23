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
import sys
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
def _isolated():
    """Every test starts from zero counters, an un-patched urllib/httpx and the real
    clock, and leaves exactly that behind on exit (success or failure) - module-global
    state by design, so nothing here may leak from one test into the next."""
    assert not pacer.installed(), "a previous test left the pacer installed"
    saved_now, saved_sleep, saved_async_sleep = pacer._now, pacer._sleep, pacer._async_sleep
    saved_urlopen = urllib.request.urlopen
    pacer._reset_for_tests()
    try:
        yield
    finally:
        pacer.uninstall()
        urllib.request.urlopen = saved_urlopen
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

    for we in pacer.PORT_WINERRORS:
        check(f"connect failure winerror={we} -> retry",
              pacer.classify(_Obj(winerror=we), is_ollama_host=True))

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
               test_t8_attach_writes_the_key_only_when_something_ran_through_it):
        fn()
    print(f"\nollama_pacer: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
