#!/usr/bin/env python3
"""Selftest of the v3 proxy's raw-forward mode against a fake upstream on loopback (PREREG-V3 §4.4, TB1, A2.4).

Run in every arm environment as a logged pre-step, beside _pacer_selftest.py. No network, no real key: the key
file is a temporary file holding a sentinel. Each property is checked and named:

* key_injected - the upstream sees the injected key, not the client's token, and only allowlisted headers;
* body_identical - the request body arrives byte for byte (no fallback configured);
* response_identical - the client receives the upstream's bytes unchanged, SSE keep-alive comments included;
* streams_before_end - the client holds chunk 1 before the upstream sends chunk 2 (nothing is buffered);
* no_retry - an upstream 500 reaches the client once, and the upstream saw exactly one request;
* abandon_cancels - a client that leaves mid-stream closes the upstream within 1 s, counted as client_abandoned;
* token_refused - a missing or wrong token gets a local 401 and no upstream connection;
* keep_alive - two requests on one client connection use one upstream connection;
* loopback_only - every listener is bound to 127.0.0.1.

    python research/_llm_proxy_selftest.py [--json]
Exit 1 names the failing property.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SENTINEL_KEY = "nvt3-selftest-KEYSENTINEL-5f1c0e2a"
TOKEN = "nvt3-selftest-armtoken-00000000000000000000000000000000"


def load_proxy():
    spec = importlib.util.spec_from_file_location("v3_llm_proxy", HERE / "_llm_proxy.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["v3_llm_proxy"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeUpstream:
    """Plain HTTP/1.1 on loopback. Records every raw request and every byte it sends; serves per path."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.requests: list[bytes] = []
        self.sent: list[bytes] = []
        self.connections = 0
        self.gate = threading.Event()               # the client has chunk 1
        self.abandon_seen_at: list[float] = []
        self._stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    def close(self):
        self._stop = True
        self.sock.close()

    def _loop(self):
        while not self._stop:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()

    def _send(self, c, data: bytes):
        self.sent.append(data)
        c.sendall(data)

    def _conn(self, c):
        buf = bytearray()
        try:
            while True:
                while b"\r\n\r\n" not in buf:
                    chunk = c.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                end = buf.index(b"\r\n\r\n") + 4
                head = bytes(buf[:end])
                del buf[:end]
                length = 0
                for line in head.split(b"\r\n"):
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1])
                while len(buf) < length:
                    buf += c.recv(65536)
                body = bytes(buf[:length])
                del buf[:length]
                self.requests.append(head + body)
                path = head.split(b" ", 2)[1]
                if not self._serve(c, path):
                    return
        except OSError:
            return
        finally:
            c.close()

    def _serve(self, c, path: bytes) -> bool:
        if path.startswith(b"/v1/chat/completions"):
            body = b'\n\n{"id":"x","choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]}'
            self._send(c, b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                       + str(len(body)).encode() + b"\r\n\r\n" + body)
            return True
        if path.startswith(b"/v1/stream"):
            self._send(c, b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n")
            one = b': keep-alive\n\ndata: {"choices":[{"delta":{"content":"a"}}]}\n\n'
            self._send(c, b"%x\r\n%s\r\n" % (len(one), one))
            if not self.gate.wait(5):
                return False                               # the client never saw chunk 1: buffering
            two = b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n\ndata: [DONE]\n\n'
            self._send(c, b"%x\r\n%s\r\n0\r\n\r\n" % (len(two), two))
            return True
        if path.startswith(b"/v1/fail"):
            self._send(c, b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 4\r\n\r\noops")
            return True
        if path.startswith(b"/v1/slow"):
            self._send(c, b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
                          b"6\r\n: hi\n\n\r\n")
            c.settimeout(10)
            try:
                while c.recv(65536):
                    pass
            except OSError:
                pass
            self.abandon_seen_at.append(time.monotonic())
            return False
        self._send(c, b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
        return True


def _request(port: int, path: str, *, token: str | None = TOKEN, body: bytes = b'{"model":"deepseek-flash"}',
             extra: str = "") -> bytes:
    auth = f"Authorization: Bearer {token}\r\n" if token else ""
    return (f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{auth}Content-Type: application/json\r\n"
            f"User-Agent: selftest\r\nX-Stainless-Os: Windows\r\n{extra}Content-Length: {len(body)}\r\n\r\n"
            ).encode() + body


def _read_all(s: socket.socket, timeout: float = 5.0) -> bytes:
    s.settimeout(timeout)
    out = bytearray()
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            out += chunk
    except OSError:
        pass
    return bytes(out)


def _response_len(data: bytes) -> int:
    """Where the first response in ``data`` ends (for keep-alive reads)."""
    end = data.index(b"\r\n\r\n") + 4
    for line in data[:end].split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            return end + int(line.split(b":", 1)[1])
    return len(data)


def run_checks(P=None, *, tmp: Path | None = None, log=None) -> tuple[list[tuple[str, bool, str]], dict]:
    """The named checks. Returns (results, context) - the context lets a caller sweep what was written."""
    P = P or load_proxy()
    tmp = tmp or Path(tempfile.mkdtemp(prefix="nvt3_proxy_selftest_"))
    tmp.mkdir(parents=True, exist_ok=True)
    keyfile = tmp / "deepseek.env"
    keyfile.write_bytes(f"DEEPSEEK_API_KEY={SENTINEL_KEY}\n".encode())
    up = FakeUpstream()
    logs: list[str] = []
    cfg = P.ProxyConfig(arms=[P.ArmConfig(arm="a1", token=TOKEN)], run_dir=tmp / "run", upstream_host="127.0.0.1",
                        upstream_port=up.port, upstream_tls=False, control_token="ctl-token")
    proxy = P.Proxy(cfg, P.read_key(keyfile), log=log or logs.append)
    ports = proxy.start()
    port = ports["arms"]["a1"]["write"]
    res: list[tuple[str, bool, str]] = []

    def ok(name, cond, detail=""):
        res.append((name, bool(cond), detail))

    def _safe(names, fn):
        """A check that raises is a named failure: every name its section has not reported yet fails."""
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            done = {n for n, _, _ in res}
            for n in names:
                if n not in done:
                    res.append((n, False, f"raised {type(e).__name__}"))

    try:
        # key_injected, body_identical, response_identical
        def _sec0():
            body = b'{"model":"deepseek-flash","messages":[{"role":"user","content":"hi"}],"temperature":0.2}'
            s = socket.create_connection(("127.0.0.1", port))
            s.sendall(_request(port, "/u/unit-1/v1/chat/completions", body=body))
            got = bytearray()
            s.settimeout(5)
            while True:
                try:
                    chunk = s.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                got += chunk
                if b"\r\n\r\n" in got and len(got) >= _response_len(bytes(got)):
                    break
            s.close()
            req = up.requests[-1] if up.requests else b""
            head, _, rbody = req.partition(b"\r\n\r\n")
            names = {ln.split(b":", 1)[0].strip().lower() for ln in head.split(b"\r\n")[1:]}
            ok("key_injected", f"Authorization: Bearer {SENTINEL_KEY}".encode() in head and TOKEN.encode() not in head
               and names <= {b"host", b"content-type", b"user-agent", b"content-length", b"authorization", b"connection"}
               and head.startswith(b"POST /v1/chat/completions "), str(sorted(names)))
            ok("body_identical", rbody == body, f"{len(rbody)} vs {len(body)} bytes")
            ok("response_identical", bytes(got) == (up.sent[-1] if up.sent else b"x"), f"{len(got)} bytes")

        _safe(['key_injected', 'body_identical', 'response_identical'], _sec0)

        # streams_before_end
        def _sec1():
            s = socket.create_connection(("127.0.0.1", port))
            s.sendall(_request(port, "/v1/stream"))
            s.settimeout(5)
            seen = bytearray()
            try:
                while b"data: {" not in seen:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    seen += chunk
            except OSError:
                pass
            first = b"data: {" in seen
            up.gate.set()
            rest = bytearray()
            try:
                while not rest.endswith(b"0\r\n\r\n") and not (seen + rest).endswith(b"0\r\n\r\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    rest += chunk
            except OSError:
                pass
            s.close()
            whole = bytes(seen + rest)
            sent = b"".join(up.sent[-3:])
            ok("streams_before_end", first and whole == sent, f"chunk1 before chunk2: {first}; {len(whole)}/{len(sent)} bytes")

        _safe(['streams_before_end'], _sec1)

        # no_retry
        def _sec2():
            n0 = len(up.requests)
            s = socket.create_connection(("127.0.0.1", port))
            s.sendall(_request(port, "/v1/fail"))
            got = _read_all(s, 3)
            s.close()
            ok("no_retry", got.startswith(b"HTTP/1.1 500") and len(up.requests) - n0 == 1, f"{len(up.requests) - n0} upstream request(s)")

        _safe(['no_retry'], _sec2)

        # abandon_cancels
        def _sec3():
            before = proxy.counters["a1"].client_abandoned
            s = socket.create_connection(("127.0.0.1", port))
            s.sendall(_request(port, "/v1/slow"))
            s.settimeout(5)
            try:
                s.recv(65536)
            except OSError:
                pass
            t0 = time.monotonic()
            s.close()
            deadline = time.monotonic() + 3
            while not up.abandon_seen_at and time.monotonic() < deadline:
                time.sleep(0.02)
            lag = (up.abandon_seen_at[-1] - t0) if up.abandon_seen_at else 99.0
            time.sleep(0.1)
            ok("abandon_cancels", lag < 1.0 and proxy.counters["a1"].client_abandoned == before + 1,
               f"upstream saw EOF after {lag:.2f} s; abandoned {proxy.counters['a1'].client_abandoned - before}")

        _safe(['abandon_cancels'], _sec3)

        # token_refused
        def _sec4():
            c0 = up.connections
            codes = []
            for tok in (None, "nvt3-wrong"):
                s = socket.create_connection(("127.0.0.1", port))
                s.sendall(_request(port, "/v1/chat/completions", token=tok))
                codes.append(_read_all(s, 3)[:12])
                s.close()
            time.sleep(0.1)
            ok("token_refused", all(c.startswith(b"HTTP/1.1 401") for c in codes) and up.connections == c0,
               f"{codes}; {up.connections - c0} upstream connection(s)")

        _safe(['token_refused'], _sec4)

        # keep_alive
        def _sec5():
            c0 = up.connections
            s = socket.create_connection(("127.0.0.1", port))
            s.settimeout(5)
            oks = 0
            for _ in range(2):
                s.sendall(_request(port, "/v1/chat/completions"))
                got = bytearray()
                while not (b"\r\n\r\n" in got and len(got) >= _response_len(bytes(got))):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    got += chunk
                oks += got.startswith(b"HTTP/1.1 200")
            s.close()
            ok("keep_alive", oks == 2 and up.connections - c0 == 1, f"{oks} answers over {up.connections - c0} upstream connection(s)")

        _safe(['keep_alive'], _sec5)

        # loopback_only
        def _sec6():
            addrs = [s_.getsockname()[0] for s_ in proxy._listeners]
            ok("loopback_only", addrs and all(a == "127.0.0.1" for a in addrs), str(addrs))
        _safe(['loopback_only'], _sec6)
    finally:
        proxy.stop()
        up.close()
    return res, {"tmp": tmp, "logs": logs, "proxy": proxy, "upstream": up, "P": P}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res, _ = run_checks()
    failed = [n for n, ok, _ in res if not ok]
    if a.json:
        print(json.dumps({"checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in res], "failed": failed}))
    else:
        for n, ok, d in res:
            print(f"  {'ok  ' if ok else 'FAIL'} {n}" + ("" if ok else f" - {d}"))
        print(f"proxy selftest (raw-forward): {len(res) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
