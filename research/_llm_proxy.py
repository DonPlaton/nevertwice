#!/usr/bin/env python3
"""The v3 recording proxy (PREREG-V3 §4.4, TB1). This first part is raw-forward mode (plan step A2.4).

One local process binds 127.0.0.1 only: a write port and an egress-catcher port per arm, and a control port. A
request to an arm port must carry that arm's token (``Authorization: Bearer`` or ``x-api-key``); without it the
proxy answers 401 itself and opens no upstream connection. With it, the proxy:

* strips the client's credentials and forwards only allowlisted headers, then injects the real key, read once by
  path from the key file and never printed, logged or written;
* strips the ``/u/<unit>`` prefix and forwards the rest of the path unchanged to api.deepseek.com (/v1, /anthropic);
* for an arm under the declared thinking fallback, and only under branch (b), inserts the one field
  ``"thinking": {"type": "disabled"}`` at the front of the JSON body, if the body has no ``thinking`` of its own;
* streams the upstream response to the client byte for byte as it arrives - SSE, keep-alive comments and blank
  lines included - with no buffering, no parsing beyond the framing needed to find the end of a response on a
  kept-alive connection, and no retry;
* closes the upstream connection when the client goes away, and counts ``client_abandoned``.

The key is bound to its host (X1/X2): the upstream is api.deepseek.com:443 over TLS, fixed in code; the only other
upstream ever accepted is plain text on 127.0.0.1, and only when the key file is not the real one under
D:\\Coding\\_secrets (tests with a sentinel key). The auditor's R4 ruling: on a machine where the name resolves only
through the owner's local HTTP proxy, the config may name that proxy as a hop - ``"via": {"host": "127.0.0.1",
"port": N}``, loopback, plain HTTP, nothing else. The proxy then sends ``CONNECT api.deepseek.com:443`` (the target
is the code's constant; the config can name only the hop), treats any reply but 200 as an upstream error, and runs
TLS with SNI and verification end to end inside the tunnel, so the key travels only inside that TLS. At start it
opens one tunnel, completes the handshake without sending a request, and records the peer certificate's issuer
(O, CN) and notAfter. The proxy's own environment still carries no proxy variable (AQ13); arms never use the hop. A header name or value carrying CR, LF or NUL is refused (X4); the
catcher records a host only as a bounded hostname (X5); /scan-files opens nothing outside the run directory and the
declared scan roots (X3).

Each client connection gets its own upstream connection (1:1), so a client that keeps its connection alive keeps
the upstream one too; a request that says ``Connection: close`` (or is HTTP/1.0 without keep-alive) has both
connections closed right after its response, so a client that reads to EOF never waits on the proxy. A chunked request body is refused (411); ``Expect: 100-continue`` is answered locally; a
pipelined request is refused. Accept-Encoding is not forwarded, so every response comes back identity-encoded for
every arm (AQ5). The catcher records the host of any request a child sends through HTTP(S)_PROXY and refuses it.

Recording mode, the Ollama leg, scans and the JSONL come in plan steps A2.6 and A2.7. Standard library only,
Python 3.10+. Run as ``python research/_llm_proxy.py serve --config ... --key-file ...``; the tokens arrive as one
JSON line on stdin, never in argv, a file or the environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import socket
import ssl
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

UPSTREAM_HOST = "api.deepseek.com"
UPSTREAM_PORT = 443
#: Forwarded from the client when present (case-insensitive). Everything else is dropped; the key is injected.
HEADER_ALLOWLIST = ("content-type", "content-length", "accept", "user-agent", "anthropic-version", "anthropic-beta")
THINKING_OFF_FIELD = b'"thinking":{"type":"disabled"},'
#: The real secrets directory: a key read from under it may only ever go to UPSTREAM_HOST over TLS.
SECRETS_ROOT = Path(r"D:\Coding\_secrets")
#: X7: with the real key, the proxy writes and scans only here (the polygon's runs tree), by written and real path.
POLYGON_RUNS = Path(r"D:\Coding\_nevertwice_polygon\runs")
#: Never opened by /scan-files, whatever it is asked (X3; the quarantine rule).
#: The owner's home is not listed here: every path must also lie inside run_dir or a declared scan root, which in
#: the campaign are under the polygon - that is what keeps the home unread.
NEVER_OPEN = (Path(r"D:\Coding\_nevertwice_owner_data_quarantine"), Path(r"D:\Obsidian"), SECRETS_ROOT)
_HOSTNAME = re.compile(r"[A-Za-z0-9._:\[\]-]{1,253}")
MAX_HEAD = 64 * 1024
MAX_BODY = 32 * 1024 * 1024
_UNIT = re.compile(r"/u/([A-Za-z0-9._-]{1,128})(/.*)$")
#: The only keys a proxy config file may carry; anything else (a CONNECT target, say) is refused at load.
CONFIG_KEYS = frozenset({"arms", "run_dir", "thinking_branch", "upstream", "scan_roots", "via"})
MAX_CONNECT_REPLY = 8 * 1024
_FORWARDED_PREFIXES = ("/v1/", "/chat/", "/anthropic/", "/models")


class _Key:
    """The upstream key. It never prints: repr and str are redacted, and errors never carry the file's content."""

    __slots__ = ("_v",)

    def __init__(self, value: str):
        self._v = value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__

    def header(self) -> str:
        return self._v


def read_key(path: str | os.PathLike, name: str = "DEEPSEEK_API_KEY") -> _Key:
    """``NAME=value`` from an env-style file. Any failure says what failed, never what the file holds."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"key file unreadable ({type(e).__name__})") from None
    for line in text.splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() == name:
            v = v.strip().strip('"').strip("'")
            if v:
                return _Key(v)
    raise RuntimeError(f"key file has no {name} line")


@dataclass
class ArmConfig:
    arm: str
    mode: str = "raw"                       # raw | record (record: A2.6)
    thinking_route: str = "documented"      # documented | fallback
    token: str = ""


@dataclass
class ProxyConfig:
    arms: list[ArmConfig]
    run_dir: Path
    thinking_branch: str = "unset"          # unset | a | b (the thinking-default slot)
    upstream_host: str = UPSTREAM_HOST
    upstream_port: int = UPSTREAM_PORT
    upstream_tls: bool = True
    control_token: str = ""
    connect_timeout_s: float = 30.0
    scan_roots: tuple = ()                  # X3: besides run_dir, where /scan-files may look (e.g. the captures dir)
    via_port: int | None = None             # R4: the owner's loopback HTTP proxy, as a CONNECT hop (host 127.0.0.1)

    def __post_init__(self):
        if self.upstream_tls and (self.upstream_host != UPSTREAM_HOST or self.upstream_port != UPSTREAM_PORT):
            raise ValueError(f"the key goes only to {UPSTREAM_HOST}:{UPSTREAM_PORT} over TLS (X1)")
        if not self.upstream_tls and self.upstream_host != "127.0.0.1":
            raise ValueError("a plain-text upstream is allowed only on 127.0.0.1 (tests)")
        if self.via_port is not None:
            if not self.upstream_tls:
                raise ValueError("the CONNECT hop serves only the TLS upstream (R4)")
            if isinstance(self.via_port, bool) or not isinstance(self.via_port, int) or not 1 <= self.via_port <= 65535:
                raise ValueError("the CONNECT hop's port must be an integer in 1..65535 (R4)")
        if self.thinking_branch not in ("unset", "a", "b"):
            raise ValueError("thinking_branch must be unset, a or b")

    @classmethod
    def load(cls, path: str | os.PathLike, secrets: dict, *, test_upstream_ok: bool = False) -> "ProxyConfig":
        """X2: the config file cannot move the key. An "upstream" entry is accepted only for tests
        (``test_upstream_ok``, which main() grants only when the key file is not under SECRETS_ROOT), and even
        then only plain text on 127.0.0.1."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        extra = sorted(set(raw) - CONFIG_KEYS)
        if extra:
            raise ValueError(f"unknown config keys refused: {', '.join(extra)}")
        via = raw.get("via")
        if via is not None:                               # R4: the hop only, loopback, plain HTTP, no userinfo
            if not isinstance(via, dict) or set(via) != {"host", "port"}:
                raise ValueError("via names exactly a host and a port - never a target, scheme or userinfo (R4)")
            if via["host"] != "127.0.0.1":
                raise ValueError("the CONNECT hop must be on 127.0.0.1 (R4)")
        up = raw.get("upstream")
        if up is not None:
            if not test_upstream_ok:
                raise ValueError("the config file cannot change the upstream (X2)")
            if up.get("host") != "127.0.0.1" or up.get("tls", True):
                raise ValueError("a test upstream must be plain text on 127.0.0.1 (X2)")
        if not test_upstream_ok:                          # X7: with the real key nothing outside the polygon runs tree
            for q in [raw["run_dir"], *(raw.get("scan_roots") or ())]:
                if not (_under(q, POLYGON_RUNS) and _under(os.path.realpath(q), os.path.realpath(POLYGON_RUNS))):
                    raise ValueError("with the real key, run_dir and scan_roots must lie in the polygon runs tree (X7)")
        tokens = secrets.get("tokens") or {}
        arms = [ArmConfig(arm=a["arm"], mode=a.get("mode", "raw"), thinking_route=a.get("thinking_route", "documented"),
                          token=tokens.get(a["arm"], "")) for a in raw["arms"]]
        up = up or {}
        return cls(arms=arms, run_dir=Path(raw["run_dir"]), thinking_branch=raw.get("thinking_branch", "unset"),
                   upstream_host=up.get("host", UPSTREAM_HOST), upstream_port=int(up.get("port", UPSTREAM_PORT)),
                   upstream_tls=bool(up.get("tls", True)), control_token=secrets.get("control_token", ""),
                   scan_roots=tuple(Path(p) for p in raw.get("scan_roots") or ()),
                   via_port=via["port"] if via is not None else None)


@dataclass
class Counters:
    requests: int = 0
    connect_refused: int = 0                # R4: the hop answered CONNECT with anything but 200
    refused_auth: int = 0
    refused_path: int = 0
    refused_chunked: int = 0
    refused_pipelined: int = 0
    refused_header: int = 0
    upstream_errors: int = 0
    client_abandoned: int = 0
    thinking_injected: int = 0
    upstream_connections: int = 0
    bytes_up: int = 0
    bytes_down: int = 0
    catcher_hosts: list = field(default_factory=list)


# ── HTTP/1.1 pieces ─────────────────────────────────────────────────────

class ConnectRefused(OSError):
    """R4: the CONNECT hop did not open a tunnel. An upstream error: counted, never retried."""


class ProtocolError(Exception):
    pass


def _read_head(sock: socket.socket, buf: bytearray) -> bytes | None:
    """Bytes up to and including the blank line; None on a clean EOF before any byte of a new request."""
    while b"\r\n\r\n" not in buf:
        if len(buf) > MAX_HEAD:
            raise ProtocolError("request head too large")
        chunk = sock.recv(65536)
        if not chunk:
            if buf:
                raise ProtocolError("connection closed inside a request head")
            return None
        buf += chunk
    end = buf.index(b"\r\n\r\n") + 4
    head = bytes(buf[:end])
    del buf[:end]
    return head


def _parse_head(head: bytes) -> tuple[str, list[tuple[str, str]]]:
    lines = head[:-4].split(b"\r\n")
    start = lines[0].decode("latin-1")
    headers = []
    for ln in lines[1:]:
        k, sep, v = ln.decode("latin-1").partition(":")
        if not sep:
            raise ProtocolError("malformed header line")
        headers.append((k.strip(), v.strip()))
    return start, headers


def _hget(headers: list[tuple[str, str]], name: str) -> str | None:
    low = name.lower()
    for k, v in headers:
        if k.lower() == low:
            return v
    return None


class ResponseFramer:
    """Follows an HTTP/1.1 response's framing as its bytes pass through, without changing any of them, so the proxy
    knows where a response ends on a kept-alive connection. ``feed`` returns how many of the given bytes belong to
    this response; ``done`` turns true at its last byte. ``body_sink`` (recording mode) receives the de-chunked body."""

    def __init__(self, request_method: str, body_sink: Callable[[bytes], None] | None = None):
        self.method = request_method.upper()
        self.sink = body_sink
        self.state = "head"
        self.buf = bytearray()
        self.remaining = 0
        self.status = 0
        self.close_after = False
        self.done = False
        self.headers: list[tuple[str, str]] = []

    def feed(self, data: bytes) -> int:
        used = 0
        while used < len(data) and not self.done:
            used += self._step(data[used:])
        return used

    def eof(self) -> None:
        """Upstream closed. Fine for a read-until-close body; anything else ends the response incomplete."""
        if self.state == "until_close":
            self.done = True
        elif not self.done:
            raise ProtocolError("upstream closed inside a response")

    def _step(self, data: bytes) -> int:
        if self.state == "head":
            self.buf += data
            if b"\r\n\r\n" not in self.buf:
                if len(self.buf) > MAX_HEAD:
                    raise ProtocolError("response head too large")
                return len(data)
            end = self.buf.index(b"\r\n\r\n") + 4
            consumed = len(data) - (len(self.buf) - end)
            start, self.headers = _parse_head(bytes(self.buf[:end]))
            self.buf.clear()
            parts = start.split(" ", 2)
            self.status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            version = parts[0]
            conn = (_hget(self.headers, "connection") or "").lower()
            self.close_after = "close" in conn or version == "HTTP/1.0"
            te = (_hget(self.headers, "transfer-encoding") or "").lower()
            cl = _hget(self.headers, "content-length")
            if 100 <= self.status < 200:
                self.state = "head"                      # an interim response: the real head follows
            elif self.method == "HEAD" or self.status in (204, 304):
                self.done = True
            elif "chunked" in te:
                self.state = "chunk_size"
            elif cl is not None:
                self.remaining = int(cl)
                self.state = "length"
                if self.remaining == 0:
                    self.done = True
            else:
                self.state = "until_close"
                self.close_after = True
            return consumed
        if self.state == "length":
            n = min(self.remaining, len(data))
            if self.sink:
                self.sink(data[:n])
            self.remaining -= n
            if self.remaining == 0:
                self.done = True
            return n
        if self.state == "until_close":
            if self.sink:
                self.sink(data)
            return len(data)
        if self.state in ("chunk_size", "trailer"):
            self.buf += data
            if b"\r\n" not in self.buf:
                return len(data)
            end = self.buf.index(b"\r\n") + 2
            consumed = len(data) - (len(self.buf) - end)
            line = bytes(self.buf[:end - 2])
            self.buf.clear()
            if self.state == "trailer":
                if line == b"":
                    self.done = True
                return consumed
            size = int(line.split(b";", 1)[0].strip() or b"0", 16)
            if size == 0:
                self.state = "trailer"
            else:
                self.remaining = size
                self.state = "chunk_data"
            return consumed
        if self.state == "chunk_data":
            n = min(self.remaining, len(data))
            if self.sink:
                self.sink(data[:n])
            self.remaining -= n
            if self.remaining == 0:
                self.state = "chunk_crlf"
                self.buf.clear()
            return n
        if self.state == "chunk_crlf":
            need = 2 - len(self.buf)
            take = data[:need]
            self.buf += take
            if len(self.buf) == 2:
                if bytes(self.buf) != b"\r\n":
                    raise ProtocolError("chunk not followed by CRLF")
                self.buf.clear()
                self.state = "chunk_size"
            return len(take)
        raise ProtocolError(f"framer in unknown state {self.state}")


# ── the proxy ───────────────────────────────────────────────────────────

def _listen() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):                   # WP5: nobody else may bind this port
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(64)
    return s


def _send_local(sock: socket.socket, status: int, reason: str, body: bytes = b"", *, close: bool = True) -> None:
    head = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
            f"{'Connection: close' if close else 'Connection: keep-alive'}\r\n\r\n").encode("latin-1")
    try:
        sock.sendall(head + body)
    except OSError:
        pass


class Proxy:
    def __init__(self, config: ProxyConfig, key: _Key, *, log: Callable[[str], None] | None = None,
                 ssl_context: ssl.SSLContext | None = None):
        self.config = config
        self._key = key
        #: Verification is always on: ssl.create_default_context(). A test may hand in its own verifying context;
        #: the config file cannot, and main() never does.
        self._ssl = ssl_context
        self.log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
        self.arms = {a.arm: a for a in config.arms}
        self.counters = {a.arm: Counters() for a in config.arms}
        self.ports: dict = {}
        self._listeners: list[socket.socket] = []
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.scan_refused = 0

    # lifecycle ---------------------------------------------------------------------------------------------
    def start(self) -> dict:
        arm_ports = {}
        for arm in self.config.arms:
            w, c = _listen(), _listen()
            self._serve(w, lambda s, a=arm: self._client(s, a))
            self._serve(c, lambda s, a=arm: self._catcher(s, a))
            arm_ports[arm.arm] = {"write": w.getsockname()[1], "catcher": c.getsockname()[1]}
        ctl = _listen()
        self._serve(ctl, self._control)
        self.ports = {"arms": arm_ports, "control": ctl.getsockname()[1]}
        return self.ports

    def _serve(self, lsock: socket.socket, handler) -> None:
        self._listeners.append(lsock)

        def loop():
            lsock.settimeout(0.5)
            while not self._stop.is_set():
                try:
                    s, _ = lsock.accept()
                except (socket.timeout, TimeoutError):
                    continue
                except OSError:
                    return
                s.settimeout(None)
                threading.Thread(target=self._guard, args=(handler, s), daemon=True).start()

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        self._threads.append(t)

    def _guard(self, handler, s: socket.socket) -> None:
        try:
            handler(s)
        except Exception as e:                           # noqa: BLE001 - a type name, never a message or a header
            self.log(f"handler error: {type(e).__name__}")
        finally:
            try:
                s.close()
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        for s in self._listeners:
            try:
                s.close()
            except OSError:
                pass

    # the arm port --------------------------------------------------------------------------------------------
    def _authorised(self, arm: ArmConfig, headers) -> bool:
        if not arm.token:
            return False
        auth = _hget(headers, "authorization") or ""
        xkey = _hget(headers, "x-api-key") or ""
        return auth == f"Bearer {arm.token}" or xkey == arm.token

    def _upstream(self) -> socket.socket:
        cfg = self.config
        if cfg.via_port is not None:                     # R4: CONNECT through the owner's loopback proxy
            raw = socket.create_connection(("127.0.0.1", cfg.via_port), timeout=cfg.connect_timeout_s)
            try:
                self._connect_tunnel(raw)
            except BaseException:
                raw.close()
                raise
        else:
            raw = socket.create_connection((cfg.upstream_host, cfg.upstream_port), timeout=cfg.connect_timeout_s)
        if cfg.upstream_tls:
            ctx = self._ssl or ssl.create_default_context()
            tls = ctx.wrap_socket(raw, server_hostname=UPSTREAM_HOST)
            tls.settimeout(None)
            return tls
        raw.settimeout(None)
        return raw

    @staticmethod
    def _connect_tunnel(raw: socket.socket) -> None:
        """CONNECT to the code's constant target; anything but a 200 reply is an upstream error (no retry). Nothing
        is sent after the request until the reply head has been read whole."""
        target = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"
        raw.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode("ascii"))
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            chunk = raw.recv(1024)
            if not chunk:
                raise ConnectRefused("the hop closed during CONNECT")
            buf += chunk
            if len(buf) > MAX_CONNECT_REPLY:
                raise ConnectRefused("the hop's CONNECT reply is too large")
        end = buf.index(b"\r\n\r\n") + 4
        parts = bytes(buf[:end]).split(b"\r\n", 1)[0].split(b" ", 2)
        if len(parts) < 2 or parts[1] != b"200":
            raise ConnectRefused("the hop did not answer CONNECT with 200")
        if len(buf) > end:
            raise ConnectRefused("the hop sent bytes before the TLS handshake")

    def probe_upstream(self) -> dict:
        """One tunnel and one TLS handshake, no request: the peer certificate's issuer (O, CN) and notAfter, for
        the log and FREEZE-V3's proxy_upstream_via. Names only; nothing is sent inside the TLS."""
        s = self._upstream()
        try:
            cert = s.getpeercert() if isinstance(s, ssl.SSLSocket) else {}
            issuer = dict(x[0] for x in cert.get("issuer", ()))
            return {"via": None if self.config.via_port is None else {"host": "127.0.0.1", "port": self.config.via_port},
                    "issuer_o": issuer.get("organizationName"), "issuer_cn": issuer.get("commonName"),
                    "not_after": cert.get("notAfter"), "tls_version": s.version() if isinstance(s, ssl.SSLSocket) else None}
        finally:
            s.close()

    def _client(self, cs: socket.socket, arm: ArmConfig) -> None:
        ctr = self.counters[arm.arm]
        buf = bytearray()
        up: socket.socket | None = None
        try:
            while not self._stop.is_set():
                head = _read_head(cs, buf)
                if head is None:
                    return
                start, headers = _parse_head(head)
                method, _, rest = start.partition(" ")
                target = rest.rsplit(" ", 1)[0]
                ctr.requests += 1
                if any(ch in k or ch in v for k, v in headers for ch in ("\r", "\n", "\0")):
                    ctr.refused_header += 1                  # X4: no smuggled header line reaches the upstream
                    _send_local(cs, 400, "Bad Request", b"a header carries CR, LF or NUL")
                    return
                if not self._authorised(arm, headers):
                    ctr.refused_auth += 1
                    _send_local(cs, 401, "Unauthorized", b"proxy token missing or wrong")
                    return
                if "chunked" in (_hget(headers, "transfer-encoding") or "").lower():
                    ctr.refused_chunked += 1
                    _send_local(cs, 411, "Length Required", b"chunked request bodies are refused")
                    return
                m = _UNIT.match(target)
                path = m.group(2) if m else target
                if ".." in path or not path.startswith(_FORWARDED_PREFIXES):
                    ctr.refused_path += 1
                    _send_local(cs, 404, "Not Found", b"path not forwarded")
                    return
                if (_hget(headers, "expect") or "").lower() == "100-continue":
                    cs.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
                length = int(_hget(headers, "content-length") or 0)
                if length > MAX_BODY:
                    _send_local(cs, 413, "Payload Too Large")
                    return
                while len(buf) < length:
                    chunk = cs.recv(65536)
                    if not chunk:
                        ctr.client_abandoned += 1
                        return
                    buf += chunk
                body = bytes(buf[:length])
                del buf[:length]
                if buf:                                  # a second request before this one was answered
                    ctr.refused_pipelined += 1
                    _send_local(cs, 400, "Bad Request", b"pipelined requests are refused")
                    return
                body, injected = self._fallback(arm, method, path, headers, body)
                ctr.thinking_injected += injected
                out = self._outgoing_head(method, path, headers, len(body))
                if up is None:
                    try:
                        up = self._upstream()
                        ctr.upstream_connections += 1
                    except OSError as e:
                        ctr.upstream_errors += 1
                        if isinstance(e, ConnectRefused):
                            ctr.connect_refused += 1
                        self.log(f"upstream connect failed: {type(e).__name__}")
                        _send_local(cs, 502, "Bad Gateway", b"upstream unreachable")
                        return
                try:
                    up.sendall(out + body)
                except OSError as e:
                    ctr.upstream_errors += 1
                    self.log(f"upstream send failed: {type(e).__name__}")
                    _send_local(cs, 502, "Bad Gateway", b"upstream send failed")
                    return
                ctr.bytes_up += len(out) + len(body)
                keep = self._pipe_response(cs, up, method, ctr)
                version = rest.rsplit(" ", 1)[-1].upper()
                conn = (_hget(headers, "connection") or "").lower()
                if not keep or "close" in conn or (version == "HTTP/1.0" and "keep-alive" not in conn):
                    return                               # the finally closes upstream; _guard closes the client
        finally:
            if up is not None:
                try:
                    up.close()
                except OSError:
                    pass

    def _fallback(self, arm: ArmConfig, method: str, path: str, headers, body: bytes) -> tuple[bytes, int]:
        """The declared thinking fallback: branch (b) only, fallback arms only, one field, at the body's front."""
        if (self.config.thinking_branch != "b" or arm.thinking_route != "fallback" or method.upper() != "POST"
                or not body.lstrip().startswith(b"{")):
            return body, 0
        try:
            obj = json.loads(body)
        except ValueError:
            return body, 0
        if not isinstance(obj, dict) or "thinking" in obj:
            return body, 0
        i = body.index(b"{") + 1
        empty = body[i:].lstrip().startswith(b"}")
        return body[:i] + (THINKING_OFF_FIELD[:-1] if empty else THINKING_OFF_FIELD) + body[i:], 1

    def _outgoing_head(self, method: str, path: str, headers, body_len: int) -> bytes:
        lines = [f"{method} {path} HTTP/1.1", f"Host: {self.config.upstream_host}"]
        for k, v in headers:
            kl = k.lower()
            if any(ch in k or ch in v for ch in ("\r", "\n", "\0")):
                continue                                 # X4, second layer: never written into the head
            if kl in HEADER_ALLOWLIST and kl != "content-length":
                lines.append(f"{k}: {v}")
        lines.append(f"Content-Length: {body_len}")
        lines.append(f"Authorization: Bearer {self._key.header()}")
        if path.startswith("/anthropic/"):
            lines.append(f"x-api-key: {self._key.header()}")
        lines.append("Connection: keep-alive")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")

    def _pipe_response(self, cs: socket.socket, up: socket.socket, method: str, ctr: Counters) -> bool:
        """Upstream bytes to the client as they arrive. Returns whether both connections stay open."""
        framer = ResponseFramer(method)
        sel = selectors.DefaultSelector()
        sel.register(up, selectors.EVENT_READ, "up")
        sel.register(cs, selectors.EVENT_READ, "client")
        watching_client = True
        try:
            while not framer.done:
                if isinstance(up, ssl.SSLSocket) and up.pending():      # TLS bytes already decrypted
                    ready = ["up"]
                else:
                    ready = [k.data for k, _ in sel.select(timeout=1.0)]
                for who in ready:
                    if who == "client" and watching_client:
                        try:
                            peek = cs.recv(1, socket.MSG_PEEK)
                        except OSError:
                            peek = b""
                        if not peek:                         # the client went away: cancel upstream now
                            ctr.client_abandoned += 1
                            return False
                        sel.unregister(cs)                   # pipelined bytes: leave them, stop watching
                        watching_client = False
                        continue
                    if who != "up":
                        continue
                    try:
                        data = up.recv(65536)
                    except OSError as e:
                        ctr.upstream_errors += 1
                        self.log(f"upstream read failed: {type(e).__name__}")
                        return False
                    if not data:
                        try:
                            framer.eof()
                        except ProtocolError:
                            ctr.upstream_errors += 1
                            return False
                        break
                    used = framer.feed(data)
                    try:
                        cs.sendall(data)
                    except OSError:
                        ctr.client_abandoned += 1
                        return False
                    ctr.bytes_down += len(data)
                    if used < len(data):                     # bytes past this response: upstream misbehaved
                        ctr.upstream_errors += 1
                        return False
                if framer.state == "until_close" and framer.done:
                    break
            return framer.done and not framer.close_after
        finally:
            sel.close()

    # the catcher ------------------------------------------------------------------------------------------------
    def _catcher(self, s: socket.socket, arm: ArmConfig) -> None:
        buf = bytearray()
        head = _read_head(s, buf)
        if head is None:
            return
        start, headers = _parse_head(head)
        method, _, rest = start.partition(" ")
        target = rest.rsplit(" ", 1)[0]
        if method.upper() == "CONNECT":
            host = target.rsplit(":", 1)[0]
        else:
            m = re.match(r"https?://([^/:]+)", target)
            host = m.group(1) if m else (_hget(headers, "host") or "?").split(":")[0]
        host, host_len = _safe_host(host)
        self.counters[arm.arm].catcher_hosts.append(host)
        _append_jsonl(self.config.run_dir / "catcher.jsonl",
                      {"arm": arm.arm, "host": host, "host_len": host_len, "utc": _utc(), "refused": True})
        _send_local(s, 403, "Forbidden", b"egress refused by the v3 launch contract")

    # the control port ---------------------------------------------------------------------------------------------
    def _control(self, s: socket.socket) -> None:
        buf = bytearray()
        head = _read_head(s, buf)
        if head is None:
            return
        start, headers = _parse_head(head)
        method, _, rest = start.partition(" ")
        path = rest.rsplit(" ", 1)[0]
        if not self.config.control_token or _hget(headers, "authorization") != f"Bearer {self.config.control_token}":
            _send_local(s, 401, "Unauthorized")
            return
        length = int(_hget(headers, "content-length") or 0)
        while len(buf) < length:
            chunk = s.recv(65536)
            if not chunk:
                return
            buf += chunk
        body = bytes(buf[:length])
        if path == "/health":
            out = {"ok": True}
        elif path == "/counters":
            out = {a: {k: v for k, v in vars(c).items()} for a, c in self.counters.items()}
        elif path == "/scan-files" and method == "POST":
            out = {"key_hits": self._scan_files(json.loads(body or b"{}").get("paths") or [])}
        elif path == "/shutdown" and method == "POST":
            out = {"ok": True}
            threading.Timer(0.2, self.stop).start()
        else:
            _send_local(s, 404, "Not Found")
            return
        data = json.dumps(out).encode("utf-8")
        s.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                  + str(len(data)).encode() + b"\r\nConnection: close\r\n\r\n" + data)

    def _scan_files(self, paths: list[str]) -> int:
        """How many of the given files hold the key - the count only (after the captures, A2.5). X3: a path is opened
        only if it is inside the run directory or a declared scan root by its written AND its real path, and never
        if it names the quarantine or another denied root; a refused path is counted, not read."""
        needle = self._key.header().encode("utf-8")
        roots = [self.config.run_dir, *self.config.scan_roots]
        hits = 0
        for p in paths:
            if not _scan_allowed(p, roots):
                self.scan_refused += 1
                continue
            try:
                if needle in Path(p).read_bytes():
                    hits += 1
            except OSError:
                continue
        return hits


def _norm(p) -> str:
    return os.path.abspath(os.fspath(p)).replace("/", "\\").rstrip("\\").lower()


def _under(p, root) -> bool:
    a, r = _norm(p), _norm(root)
    return a == r or a.startswith(r + "\\")


def _scan_allowed(p: str, roots: list) -> bool:
    low = str(p).replace("/", "\\").lower()
    if any(_norm(n) in low for n in NEVER_OPEN):
        return False                                     # by name, before anything touches the disk
    real = os.path.realpath(p)
    return any(_under(p, r) and _under(real, os.path.realpath(r)) for r in roots)


def _safe_host(host: str) -> tuple[str, int]:
    """X5: a hostname (letters, digits, dot, dash; an IP literal's colons and brackets) of at most 253 characters,
    else "<invalid>" - with the original length, so a child cannot write arbitrary bytes into the log."""
    return (host, len(host)) if _HOSTNAME.fullmatch(host or "") else ("<invalid>", len(host or ""))


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        f.write((json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
        f.flush()


def write_ports(run_dir: Path, ports: dict) -> str:
    """ports.json, atomically; returns its sha256 (the READY line carries it)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    data = json.dumps(ports, sort_keys=True).encode("utf-8")
    tmp = run_dir / "ports.json.tmp"
    tmp.write_bytes(data)
    for attempt in range(10):                        # WP16: a scanner may hold the file for a moment
        try:
            os.replace(tmp, run_dir / "ports.json")
            break
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the v3 recording proxy (raw-forward mode)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--config", required=True)
    sv.add_argument("--key-file", required=True)
    args = ap.parse_args(argv)
    secrets = json.loads(sys.stdin.readline() or "{}")          # tokens: never argv, file or environment
    real_key = _under(os.path.realpath(args.key_file), os.path.realpath(SECRETS_ROOT)) or _under(args.key_file, SECRETS_ROOT)
    config = ProxyConfig.load(args.config, secrets, test_upstream_ok=not real_key)
    key = read_key(args.key_file)
    proxy = Proxy(config, key)
    if config.upstream_tls:                              # R4: the path works and whose certificate it shows
        try:
            info = proxy.probe_upstream()
        except (OSError, ssl.SSLError) as e:
            print(f"UPSTREAM_FAILED {type(e).__name__}", flush=True)
            return 4
        _append_jsonl(config.run_dir / "upstream_tls.jsonl", {**info, "utc": _utc()})
        proxy.log(f"upstream via {info['via']}: issuer O={info['issuer_o']!r} CN={info['issuer_cn']!r} "
                  f"notAfter={info['not_after']!r}")
    ports = proxy.start()
    digest = write_ports(config.run_dir, ports)
    print(f"READY {digest}", flush=True)
    try:
        while not proxy._stop.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        proxy.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
