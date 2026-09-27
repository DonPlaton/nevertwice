#!/usr/bin/env python3
"""The v3 recording proxy (PREREG-V3 §4.4, TB1): raw-forward mode (plan step A2.4) and recording mode (A2.6).

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

Recording mode adds, per port role (an arm's write and reader ports, the J3 and scheduler ports), before anything
is forwarded: the pinned model (§4.3a: any other name is refused, model_mismatch), an unparsable body (refused),
the tool names offered against the arm's §2.6.6 set (refused, tool_violation), the canary and owner-marker scans in
memory (refused, so neither reaches the provider; counted); and after each forwarded write, a copy of the response
to an in-memory parser (usage, fingerprint, finish reason, tools called, reasoning). One JSONL line per call holds
counts and names, never a body; zero-tolerance events also land in flags.jsonl, read back at start (K35). The
catcher tunnels a CONNECT only inside a declared window, only to that window's hosts (AQ1). /user/balance is
forwarded on the scheduler port only. The Ollama leg (A2.7) gives an arm that needs one its own port to the local
Ollama at 127.0.0.1:11434 - never the key, never another host - paced and retried by that arm's own copy of
research/_ollama_pacer.py, and counted (a generation call from a cloud arm is fallback_local).

Standard library only, Python 3.10+. Run as ``python research/_llm_proxy.py serve --config ... --key-file ...
[--owner-claude-md ... --owner-rules-dir ...]``; tokens, canaries and the owner's identity arrive as one JSON line
on stdin, never in argv, a file or the environment.
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
#: R-CC-WIT: the Claude Code arm's home canary header (checked on the arm port, never forwarded: not on the allowlist).
HOME_CANARY_HEADER = "x-nvt3-home-canary"
#: F-CAN (the auditor): the arm whose port REQUIRES a home canary - without one the positive control would be off
#: silently, so an ArmConfig of this name (a forwarding port) and a config that lacks its canary are refused.
HOME_CANARY_ARM = "claude-code-memory"
_HOME_CANARY_VALUE = re.compile(r"[0-9a-f]{32}")
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
CONFIG_KEYS = frozenset({"arms", "run_dir", "thinking_branch", "upstream", "scan_roots", "via", "j3",
                         "scheduler", "ollama"})
MAX_CONNECT_REPLY = 8 * 1024
#: The keys of a catcher-only (`catch`) config: the fetch windows of A3/A8 need the catcher and nothing else.
CATCH_CONFIG_KEYS = frozenset({"run_dir", "via", "catchers"})
#: A window host: a lower-case DNS name with at least one dot - never an IP literal, a port, a scheme or a wildcard.
_WINDOW_HOST = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")
_ARM_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")


def _window_host_ok(h) -> bool:
    """An exact DNS name for a window: lower case, dotted, and a last label that is not all digits - so an IP
    literal (whose labels are digits) is never a window host."""
    return isinstance(h, str) and _WINDOW_HOST.fullmatch(h) is not None and not h.rsplit(".", 1)[-1].isdigit()
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
    mode: str = "raw"                       # raw | record | catch (a catcher port only; A3.a)
    thinking_route: str = "documented"      # documented | fallback
    token: str = ""
    pinned_model: str = ""                  # §4.3a: deepseek-flash on arm ports, deepseek-v4-pro on J3
    reader_model: str = ""                  # set: the arm also gets a reader port, pinned to this
    tools_allowed: tuple = ()               # §2.6.6
    ollama_leg: bool = False                # A2.7: the arm gets an Ollama port (out-of-process arms, S8, embed ceiling)
    cloud_arm: bool = True                  # its generation is DeepSeek's: an Ollama generation call is fallback_local
    #: R-CC-WIT (the auditor's positive control, Claude Code): set, every request on this arm's port must carry the
    #: header HOME_CANARY_HEADER with exactly this value - it comes from the settings.json of the unit's fake
    #: CLAUDE_CONFIG_DIR, so its absence means the binary did not read the fake home. Missing or wrong: refused locally
    #: (403, "fake home not read"), counted and flagged, never forwarded. The header is not on the allowlist, so a
    #: request that carries it reaches DeepSeek without it.
    home_canary: str = ""

    def __post_init__(self) -> None:
        if self.home_canary != "" and not (isinstance(self.home_canary, str)
                                           and _HOME_CANARY_VALUE.fullmatch(self.home_canary)):
            raise ValueError(f"arm {self.arm}: a home canary is 32 lowercase hex characters (R-CC-WIT)")
        if self.arm == HOME_CANARY_ARM and self.mode != "catch" and not self.home_canary:
            raise ValueError(f"arm {self.arm}: no home canary - R-CC-WIT's positive control would be off (F-CAN); "
                             "the proxy does not start")


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
    ollama_mode: str = "pace"               # pace | observe (the pacer's own VALID_MODES)
    ollama_upstream: tuple = ("127.0.0.1", 11434)

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
        if tuple(self.ollama_upstream)[0] != "127.0.0.1":
            raise ValueError("the Ollama leg goes only to the local server on 127.0.0.1")

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
        canaries = secrets.get("home_canaries") or {}
        if not isinstance(canaries, dict):
            raise ValueError("home_canaries on stdin is an object of arm name -> home canary (R-CC-WIT)")
        stray = sorted(set(canaries) - {a["arm"] for a in raw["arms"]})
        if stray:
            raise ValueError(f"a home canary for arms the config does not have: {stray} (F-CAN: a mis-wired orchestrator)")
        arms = [ArmConfig(arm=a["arm"], mode=a.get("mode", "raw"), thinking_route=a.get("thinking_route", "documented"),
                          token=tokens.get(a["arm"], ""), pinned_model=a.get("pinned_model", ""),
                          reader_model=a.get("reader_model", ""), tools_allowed=tuple(a.get("tools_allowed") or ()),
                          ollama_leg=bool(a.get("ollama_leg", False)), cloud_arm=bool(a.get("cloud_arm", True)),
                          home_canary=canaries.get(a["arm"], ""))
                for a in raw["arms"]]
        oll = raw.get("ollama") or {}
        if oll.get("upstream") is not None and not test_upstream_ok:
            raise ValueError("the config file cannot change the Ollama upstream")
        for role in ("j3", "scheduler"):                  # single-port roles, always recorded
            if raw.get(role):
                arms.append(ArmConfig(arm=role, mode="record", token=tokens.get(role, ""),
                                      pinned_model=raw[role].get("pinned_model", "")))
        up = up or {}
        return cls(arms=arms, run_dir=Path(raw["run_dir"]), thinking_branch=raw.get("thinking_branch", "unset"),
                   upstream_host=up.get("host", UPSTREAM_HOST), upstream_port=int(up.get("port", UPSTREAM_PORT)),
                   upstream_tls=bool(up.get("tls", True)), control_token=secrets.get("control_token", ""),
                   scan_roots=tuple(Path(p) for p in raw.get("scan_roots") or ()),
                   via_port=via["port"] if via is not None else None,
                   ollama_mode=oll.get("mode", "pace"),
                   ollama_upstream=tuple(oll.get("upstream") or ("127.0.0.1", 11434)))


def load_catch_config(path: str | os.PathLike, secrets: dict) -> ProxyConfig:
    """A3.a: a catcher-only config - {run_dir, via?, catchers: [arm names]} and nothing else. No key is involved: no
    arm write port exists, so no request can reach the upstream; the catcher's window tunnels go through the hop."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    extra = sorted(set(raw) - CATCH_CONFIG_KEYS)
    if extra:
        raise ValueError(f"unknown catch config keys refused: {', '.join(extra)}")
    via = raw.get("via")
    if via is not None and (not isinstance(via, dict) or set(via) != {"host", "port"} or via["host"] != "127.0.0.1"):
        raise ValueError("via names exactly {host: 127.0.0.1, port} (R4)")
    names = raw.get("catchers") or []
    if not names or not all(isinstance(n, str) and _ARM_NAME.fullmatch(n) for n in names) or len(set(names)) != len(names):
        raise ValueError("catchers must be a non-empty list of distinct arm names")
    return ProxyConfig(arms=[ArmConfig(arm=n, mode="catch") for n in names], run_dir=Path(raw["run_dir"]),
                       control_token=secrets.get("control_token", ""),
                       via_port=via["port"] if via is not None else None)


@dataclass
class Counters:
    requests: int = 0
    connect_refused: int = 0                # R4: the hop answered CONNECT with anything but 200
    refused_auth: int = 0
    refused_home_canary: int = 0
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
    refused_unparsable: int = 0
    model_mismatch: int = 0
    tool_violation: int = 0
    canary_hits: int = 0
    ancestor_canary_hits: int = 0
    owner_marker_hits: int = 0
    thinking_calls: int = 0
    records: int = 0
    catcher_hosts: list = field(default_factory=list)
    catcher_open: int = 0                   # F-P2-6: catcher connections whose log line is not written yet


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
        self.protocol_error = False                # set by the proxy when feed() raised ProtocolError (B-SEND2)
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


# ── recording mode (A2.6): what is read, what is written ────────────────

def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def request_key(obj, arm: str) -> str:
    """§4.5: sha256(canonical request body ‖ arm). Only the hash is kept."""
    return hashlib.sha256(canonical(obj) + b"\0" + arm.encode("utf-8")).hexdigest()


def strings_in(obj) -> list[str]:
    """Every string in a parsed JSON value, keys included (\\u escapes already decoded)."""
    out: list[str] = []
    stack = [obj]
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                out.append(str(k))
                stack.append(x)
        elif isinstance(v, list):
            stack.extend(v)
    return out


def tools_offered(obj) -> list[str]:
    """Tool names a request offers: OpenAI ``tools[].function.name``, legacy ``functions[].name``, Anthropic
    ``tools[].name``."""
    names = []
    if isinstance(obj, dict):
        for t in obj.get("tools") or []:
            if isinstance(t, dict):
                fn = t.get("function")
                n = fn.get("name") if isinstance(fn, dict) else t.get("name")
                if n:
                    names.append(str(n))
        for f in obj.get("functions") or []:
            if isinstance(f, dict) and f.get("name"):
                names.append(str(f["name"]))
    return names


FORBIDDEN_TOOL_SUBSTRINGS = ("bash", "shell", "powershell", "cmd", "terminal", "exec", "run_code", "code_interpreter",
                             "python", "computer", "browser", "web", "fetch", "http", "url", "download", "task",
                             "agent", "notebook", "kill")


def tool_violation(name: str, allowed) -> bool:
    """The same rule as research/v3/launch.py (§2.6.6): outside the arm's set, or a forbidden pattern."""
    low = name.lower()
    if low.startswith("mcp__") or any(s in low for s in FORBIDDEN_TOOL_SUBSTRINGS):
        return True
    return name not in allowed


_TRANSLIT = [("shch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ya", "я"),
             ("yu", "ю"), ("yo", "ё"), ("a", "а"), ("b", "б"), ("v", "в"), ("g", "г"), ("d", "д"), ("e", "е"),
             ("z", "з"), ("i", "и"), ("y", "й"), ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"),
             ("p", "п"), ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"), ("f", "ф"), ("h", "х"), ("c", "к"),
             ("w", "в"), ("x", "кс"), ("q", "к"), ("j", "дж")]


def cyrillic(latin: str) -> str:
    """A fixed Latin-to-Cyrillic transliteration, computed in memory so no owner name sits in code (AQ3)."""
    s, out, i = latin.lower(), [], 0
    while i < len(s):
        for lat, cyr in _TRANSLIT:
            if s.startswith(lat, i):
                out.append(cyr)
                i += len(lat)
                break
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def normalise(text: str) -> str:
    """AQ3: NFKC, casefold, one space for any run of whitespace, one slash form."""
    import unicodedata  # noqa: PLC0415
    t = unicodedata.normalize("NFKC", text).casefold().replace("\\", "/")
    return " ".join(t.split())


class OwnerMarkers:
    """§2.6.9 + the auditor's AQ3 ruling. Built at start, in memory only, never written: whole-sequence identity
    markers (full name in Latin and Cyrillic, email, git identity, home path - no single tokens) and 8-word shingles
    of the owner's CLAUDE.md and rules, hashed with BLAKE2b under a salt drawn per process. A body is a hit when any
    marker matches it."""

    N = 8

    def __init__(self, identity: dict, texts: list[str]):
        self._salt = os.urandom(16)
        seqs = []
        name = (identity.get("name") or "").strip()
        if len(name.split()) >= 2:                               # whole sequences only
            seqs += [name, cyrillic(name)]
        for k in ("email", "git_email", "git_name", "home"):
            v = (identity.get(k) or "").strip()
            if v and (k != "git_name" or len(v.split()) >= 2):
                seqs.append(v)
        self._identity = sorted({normalise(s) for s in seqs if s})
        self._shingles = set()
        for t in texts:
            words = normalise(t).split()
            for i in range(len(words) - self.N + 1):
                self._shingles.add(self._h(" ".join(words[i:i + self.N])))
        self.dropped = 0                                          # AQ3: dropped by the A9 smoke rule, count only

    def _h(self, s: str) -> bytes:
        return hashlib.blake2b(s.encode("utf-8"), key=self._salt, digest_size=16).digest()

    def hit(self, strings: list[str]) -> bool:
        text = normalise(" ".join(strings))
        if any(m and m in text for m in self._identity):
            return True
        words = text.split()
        return any(self._h(" ".join(words[i:i + self.N])) in self._shingles for i in range(len(words) - self.N + 1))

    def drop_firing(self, smoke_texts: list[str]) -> int:
        """The A9 rule: a marker that fires on the smoke split (no owner data can be there) is a false positive and
        is dropped mechanically. Returns only the count."""
        before = len(self._identity) + len(self._shingles)
        joined = [normalise(t) for t in smoke_texts]
        self._identity = [m for m in self._identity if not any(m in t for t in joined)]
        for t in joined:
            w = t.split()
            for i in range(len(w) - self.N + 1):
                self._shingles.discard(self._h(" ".join(w[i:i + self.N])))
        self.dropped = before - len(self._identity) - len(self._shingles)
        return self.dropped


class TeeParser:
    """Reads a copy of the response body after it was forwarded: SSE (OpenAI deltas, Anthropic events) or one JSON
    document. In memory only; the record keeps counts and names, never text."""

    LIMIT = 16 * 1024 * 1024

    def __init__(self):
        self.buf = bytearray()
        self.over = False

    def feed(self, data: bytes) -> None:
        if len(self.buf) + len(data) > self.LIMIT:
            self.over = True
            return
        self.buf += data

    def result(self, content_type: str, endpoint: str, response_format: str | None) -> dict:
        facts = {"response_model": None, "system_fingerprint": None, "finish_reason": None,
                 "usage": {"prompt": None, "completion": None, "cache_hit": None, "cache_miss": None, "reasoning": None},
                 "tools_called": [], "reasoning_seen": False, "thinking_block": False, "content_empty": None,
                 "json_ok": None, "parse_ok": True}
        if self.over:
            facts["parse_ok"] = False
            return facts
        text = self.buf.decode("utf-8", "replace")
        content: list[str] = []
        try:
            if "event-stream" in (content_type or ""):
                for line in text.splitlines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    self._event(json.loads(payload), facts, content)
            else:
                body = text.strip()
                if body:
                    self._document(json.loads(body), facts, content)
        except ValueError:
            facts["parse_ok"] = False
        joined = "".join(content)
        facts["content_empty"] = joined.strip() == "" and not facts["tools_called"]
        if response_format == "json_object":
            try:
                json.loads(joined)
                facts["json_ok"] = True
            except ValueError:
                facts["json_ok"] = False
        return facts

    @staticmethod
    def _usage_openai(u: dict, facts: dict) -> None:
        f = facts["usage"]
        f["prompt"] = u.get("prompt_tokens", f["prompt"])
        f["completion"] = u.get("completion_tokens", f["completion"])
        f["cache_hit"] = u.get("prompt_cache_hit_tokens", f["cache_hit"])
        f["cache_miss"] = u.get("prompt_cache_miss_tokens", f["cache_miss"])
        det = u.get("completion_tokens_details") or {}
        if isinstance(det, dict) and "reasoning_tokens" in det:
            f["reasoning"] = det["reasoning_tokens"]

    def _document(self, d: dict, facts: dict, content: list[str]) -> None:
        if d.get("type") == "message":                          # Anthropic, non-streamed
            facts["response_model"] = d.get("model")
            facts["finish_reason"] = d.get("stop_reason")
            u = d.get("usage") or {}
            facts["usage"]["prompt"] = u.get("input_tokens")
            facts["usage"]["completion"] = u.get("output_tokens")
            for b in d.get("content") or []:
                t = b.get("type")
                if t == "text":
                    content.append(b.get("text") or "")
                elif t in ("thinking", "redacted_thinking"):
                    facts["thinking_block"] = True
                elif t == "tool_use" and b.get("name"):
                    facts["tools_called"].append(b["name"])
            return
        facts["response_model"] = d.get("model")
        facts["system_fingerprint"] = d.get("system_fingerprint")
        self._usage_openai(d.get("usage") or {}, facts)
        for ch in d.get("choices") or []:
            msg = ch.get("message") or {}
            content.append(msg.get("content") or "")
            if msg.get("reasoning_content"):
                facts["reasoning_seen"] = True
            for tc in msg.get("tool_calls") or []:
                n = (tc.get("function") or {}).get("name")
                if n:
                    facts["tools_called"].append(n)
            if ch.get("finish_reason"):
                facts["finish_reason"] = ch["finish_reason"]

    def _event(self, e: dict, facts: dict, content: list[str]) -> None:
        typ = e.get("type")
        if typ:                                                  # Anthropic events
            if typ == "message_start":
                m = e.get("message") or {}
                facts["response_model"] = m.get("model")
                facts["usage"]["prompt"] = (m.get("usage") or {}).get("input_tokens")
            elif typ == "content_block_start":
                b = e.get("content_block") or {}
                if b.get("type") in ("thinking", "redacted_thinking"):
                    facts["thinking_block"] = True
                elif b.get("type") == "tool_use" and b.get("name"):
                    facts["tools_called"].append(b["name"])
            elif typ == "content_block_delta":
                d = e.get("delta") or {}
                if d.get("type") == "text_delta":
                    content.append(d.get("text") or "")
                elif d.get("type") == "thinking_delta":
                    facts["thinking_block"] = True
            elif typ == "message_delta":
                facts["finish_reason"] = (e.get("delta") or {}).get("stop_reason") or facts["finish_reason"]
                out = (e.get("usage") or {}).get("output_tokens")
                if out is not None:
                    facts["usage"]["completion"] = out
            return
        facts["response_model"] = e.get("model") or facts["response_model"]
        facts["system_fingerprint"] = e.get("system_fingerprint") or facts["system_fingerprint"]
        if e.get("usage"):
            self._usage_openai(e["usage"], facts)
        for ch in e.get("choices") or []:
            d = ch.get("delta") or {}
            if d.get("content"):
                content.append(d["content"])
            if d.get("reasoning_content"):
                facts["reasoning_seen"] = True
            for tc in d.get("tool_calls") or []:
                n = (tc.get("function") or {}).get("name")
                if n:
                    facts["tools_called"].append(n)
            if ch.get("finish_reason"):
                facts["finish_reason"] = ch["finish_reason"]



# ── the Ollama leg (A2.7, §4.4): a counting pass-through under the pacer's own code ──

OLLAMA_HOST, OLLAMA_PORT = "127.0.0.1", 11434          # the local server; never the key, never another host
PACER_PATH = Path(__file__).resolve().parent / "_ollama_pacer.py"


def load_pacer_copy(arm: str, mode: str = "pace"):
    """One copy of research/_ollama_pacer.py per arm (plan O1a): its own pace schedule (the pacer's floor, per arm:
    AQ8), its own counters, the pacer's own code and constants - nothing copied. ``install()`` is never called: the
    leg calls ``_run_paced`` directly, so no process-wide hook is patched twice."""
    import importlib.util  # noqa: PLC0415
    spec = importlib.util.spec_from_file_location(f"_ollama_pacer_leg_{re.sub(r'[^A-Za-z0-9_]', '_', arm)}", PACER_PATH)
    inst = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inst)
    if mode not in inst.VALID_MODES:
        raise ValueError(f"pacer mode {mode!r} not in {sorted(inst.VALID_MODES)}")
    inst._MODE = mode
    return inst


class LegHTTPError(Exception):
    """An Ollama answer >= 400, shaped for the pacer's ``classify``/``_llm_retryable`` (``.code``, ``.read()``),
    carrying the raw response bytes so the client still receives exactly what Ollama said."""

    def __init__(self, code: int, raw: bytes, body: bytes):
        super().__init__(f"ollama answered {code}")
        self.code, self.raw, self._body = code, raw, body

    def read(self) -> bytes:
        return self._body


def _read_response(sock: socket.socket, method: str) -> tuple[bytes, bytes, ResponseFramer]:
    """A whole (small) response: raw bytes, decoded body, and its framer - for an error the pacer must classify."""
    body = bytearray()
    framer = ResponseFramer(method, body.extend)
    raw = bytearray()
    while not framer.done:
        chunk = sock.recv(65536)
        if not chunk:
            framer.eof()
            break
        raw += chunk
        framer.feed(chunk)
    return bytes(raw), bytes(body), framer


class OllamaLeg:
    """One arm's Ollama port (§4.4): the request goes byte for byte to 127.0.0.1:11434 inside the arm's own pacer copy
    - paced in pace mode, retried only as the pacer retries (port exhaustion; a generation-path 5xx twice, 15 s then
    30 s; a 4xx never), in observe mode neither - and the answer comes back byte for byte, streamed. A generation
    call from a cloud arm is counted as fallback_local (§4.5 zero tolerance)."""

    def __init__(self, arm: str, *, mode: str, cloud_arm: bool, upstream: tuple[str, int], log: Callable[[str], None],
                 run_dir: Path, connect: Callable[[tuple[str, int]], socket.socket] | None = None):
        self.arm, self.cloud_arm, self.upstream, self.log, self.run_dir = arm, cloud_arm, upstream, log, run_dir
        self.pacer = load_pacer_copy(arm, mode)
        self.connect = connect or (lambda hp: socket.create_connection(hp, timeout=600))
        self.fallback_local = 0
        self.calls = 0

    def transport(self) -> dict:
        """The pacer's own ``ollama_transport`` record for this arm - written even with no traffic (calls: 0)."""
        out: dict = {}
        self.pacer.attach(out)
        rec = out.get("ollama_transport") or {"calls": 0}
        rec["fallback_local"] = self.fallback_local
        return rec

    def serve(self, cs: socket.socket, stage: dict) -> None:
        buf = bytearray()
        head = _read_head(cs, buf)
        if head is None:
            return
        start, headers = _parse_head(head)
        method, _, rest = start.partition(" ")
        target = rest.rsplit(" ", 1)[0]
        if any(ch in k or ch in v for k, v in headers for ch in ("\r", "\n", "\0")):
            _send_local(cs, 400, "Bad Request", b"a header carries CR, LF or NUL")
            return
        if "chunked" in (_hget(headers, "transfer-encoding") or "").lower():
            _send_local(cs, 411, "Length Required")
            return
        m = _UNIT.match(target)
        unit, path = (m.group(1), m.group(2)) if m else (None, target)
        if ".." in path or not path.startswith("/"):
            _send_local(cs, 404, "Not Found")
            return
        length = int(_hget(headers, "content-length") or 0)
        while len(buf) < length:
            chunk = cs.recv(65536)
            if not chunk:
                return
            buf += chunk
        body = bytes(buf[:length])
        lines = [f"{method} {path} HTTP/1.1", f"Host: {self.upstream[0]}:{self.upstream[1]}"]
        lines += [f"{k}: {v}" for k, v in headers if k.lower() in ("content-type", "accept")]
        lines += [f"Content-Length: {len(body)}", "Connection: close"]
        out = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body
        url = f"http://{self.upstream[0]}:{self.upstream[1]}{path}"
        is_embed, is_llm = self.pacer._is_embed_path(url), self.pacer._is_llm_path(url)
        self.calls += 1
        if is_llm and self.cloud_arm:
            self.fallback_local += 1
        t0 = time.time()

        def call():
            up = self.connect(self.upstream)                 # an OSError here (WinError 10048) is the pacer's to classify
            try:
                up.sendall(out)
                first = bytearray()
                while b"\r\n\r\n" not in first:
                    chunk = up.recv(65536)
                    if not chunk:
                        raise ConnectionResetError("ollama closed before answering")
                    first += chunk
                code = int(first.split(b" ", 2)[1])
                if code >= 400:
                    rest_raw, _, _ = _read_response(_Prefixed(up, bytes(first)), method)
                    head_end = rest_raw.index(b"\r\n\r\n") + 4
                    up.close()
                    raise LegHTTPError(code, rest_raw, _dechunk(rest_raw[head_end:], rest_raw[:head_end]))
                return up, bytes(first)
            except BaseException:
                try:
                    up.close()
                except OSError:
                    pass
                raise

        status, error = None, None
        try:
            up, first = self.pacer._run_paced(call, host_key=self.upstream, is_embed=is_embed, is_llm=is_llm)
        except LegHTTPError as e:
            status, error = e.code, "http"
            try:
                cs.sendall(e.raw)                            # exactly what Ollama said
            except OSError:
                pass
        except OSError as e:
            error = type(e).__name__
            self.log(f"ollama leg failed: {type(e).__name__}")
            _send_local(cs, 502, "Bad Gateway", b"ollama unreachable")
        else:
            try:
                framer = ResponseFramer(method)
                framer.feed(first)
                cs.sendall(first)
                status = framer.status
                while not framer.done:
                    chunk = up.recv(65536)
                    if not chunk:
                        framer.eof()
                        break
                    cs.sendall(chunk)                        # NDJSON streams through as it arrives
                    framer.feed(chunk)
            except OSError:
                error = "client_or_upstream_closed"
            finally:
                up.close()
        _append_jsonl(self.run_dir / "ollama.jsonl",
                      {"arm": self.arm, "unit": unit, "path": path, "is_embed": is_embed, "is_llm": is_llm,
                       "status": status, "error": error, "fallback_local": bool(is_llm and self.cloud_arm),
                       "t0": _iso(t0), "t1": _iso(time.time()), **stage})


class _Prefixed:
    """A socket whose first bytes were already read: recv returns them first."""

    def __init__(self, sock: socket.socket, prefix: bytes):
        self.sock, self.prefix = sock, prefix

    def recv(self, n: int) -> bytes:
        if self.prefix:
            d, self.prefix = self.prefix[:n], self.prefix[n:]
            return d
        return self.sock.recv(n)


def _dechunk(body_raw: bytes, head: bytes) -> bytes:
    if b"chunked" not in head.lower():
        return body_raw
    out, i = bytearray(), 0
    while i < len(body_raw):
        j = body_raw.index(b"\r\n", i)
        size = int(body_raw[i:j].split(b";")[0] or b"0", 16)
        if size == 0:
            break
        out += body_raw[j + 2:j + 2 + size]
        i = j + 2 + size + 2
    return bytes(out)


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


#: Zero-tolerance kinds (§4.5): each one lands in flags.jsonl, which survives a restart (K35).
FLAG_KINDS = ("model_mismatch", "tool_violation", "canary", "owner_marker", "thinking_call", "unparsable")
SPECIAL_ROLES = ("j3", "scheduler")


class Proxy:
    def __init__(self, config: ProxyConfig, key: _Key | None, *, log: Callable[[str], None] | None = None,
                 ssl_context: ssl.SSLContext | None = None,
                 markers: OwnerMarkers | None = None, canaries: dict | None = None):
        if key is None and any(a.mode != "catch" for a in config.arms):
            raise ValueError("only a catcher-only proxy runs without the key")
        self.config = config
        self._key = key
        #: Verification is always on: ssl.create_default_context(). A test may hand in its own verifying context;
        #: the config file cannot, and main() never does.
        self._ssl = ssl_context
        self.log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
        self.arms = {a.arm: a for a in config.arms}
        self.counters = {a.arm: Counters() for a in config.arms}
        self.markers = markers
        self._canaries = {k: v for k, v in (canaries or {}).items() if v}
        self.ports: dict = {}
        self.stage = {"block": None, "stage": None}
        self.windows: dict[str, dict] = {}          # A3.a: name -> {"hosts": frozenset, "arms": frozenset}
        self._listeners: list[socket.socket] = []
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.scan_refused = 0
        self._lock = threading.Lock()
        self.flags = self._load_flags()
        self.legs = {a.arm: OllamaLeg(a.arm, mode=config.ollama_mode, cloud_arm=a.cloud_arm,
                                      upstream=tuple(config.ollama_upstream), log=self.log, run_dir=config.run_dir)
                     for a in config.arms if a.ollama_leg}

    # lifecycle ---------------------------------------------------------------------------------------------
    def start(self) -> dict:
        arm_ports, special = {}, {}
        for arm in self.config.arms:
            if arm.mode == "catch":                      # A3.a: a catcher port and nothing else
                c = _listen()
                self._serve(c, lambda s, a=arm: self._catcher(s, a))
                arm_ports[arm.arm] = {"catcher": c.getsockname()[1]}
                continue
            if arm.arm in SPECIAL_ROLES:
                p = _listen()
                self._serve(p, lambda s, a=arm: self._client(s, a, a.arm))
                special[arm.arm] = p.getsockname()[1]
                continue
            w, c = _listen(), _listen()
            self._serve(w, lambda s, a=arm: self._client(s, a, "write"))
            self._serve(c, lambda s, a=arm: self._catcher(s, a))
            arm_ports[arm.arm] = {"write": w.getsockname()[1], "catcher": c.getsockname()[1]}
            if arm.reader_model:
                r = _listen()
                self._serve(r, lambda s, a=arm: self._client(s, a, "reader"))
                arm_ports[arm.arm]["reader"] = r.getsockname()[1]
            if arm.arm in self.legs:
                o = _listen()
                self._serve(o, lambda s, leg=self.legs[arm.arm]: leg.serve(s, dict(self.stage)))
                arm_ports[arm.arm]["ollama"] = o.getsockname()[1]
        ctl = _listen()
        self._serve(ctl, self._control)
        self.ports = {"arms": arm_ports, **special, "control": ctl.getsockname()[1]}
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

    # flags ------------------------------------------------------------------------------------------------------
    def _load_flags(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        path = self.config.run_dir / "flags.jsonl"
        if path.exists():
            for line in path.read_bytes().decode("utf-8", "replace").splitlines():
                try:
                    k = json.loads(line).get("kind")
                except ValueError:
                    continue
                counts[k] = counts.get(k, 0) + 1
        return counts

    def _flag(self, arm: str, kind: str, key: str | None) -> None:
        with self._lock:
            self.flags[kind] = self.flags.get(kind, 0) + 1
            _append_jsonl(self.config.run_dir / "flags.jsonl",
                          {"arm": arm, "kind": kind, "request_key": key, "utc": _utc(), **self.stage})

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
    def _connect_tunnel(raw: socket.socket, target: str = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}") -> None:
        """CONNECT to ``target`` - the code's constant for the key's upstream; a window host:443 for the catcher
        (A3.a). Anything but a 200 reply is an error (no retry). Nothing is sent after the request until the reply
        head has been read whole."""
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

    def _hop_tunnel(self, host: str) -> socket.socket:
        """A3.a: a raw tunnel to ``host``:443 through the declared hop - the only way the catcher reaches a window
        host. TLS stays end to end between the child and the host; the proxy only relays bytes."""
        raw = socket.create_connection(("127.0.0.1", self.config.via_port), timeout=self.config.connect_timeout_s)
        try:
            self._connect_tunnel(raw, f"{host}:443")
        except BaseException:
            raw.close()
            raise
        raw.settimeout(None)
        return raw

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

    def _path_allowed(self, role: str, method: str, path: str) -> bool:
        if ".." in path:
            return False
        if path == "/user/balance":
            return role == "scheduler" and method.upper() == "GET"
        return path.startswith(_FORWARDED_PREFIXES)

    def _client(self, cs: socket.socket, arm: ArmConfig, role: str = "write") -> None:
        ctr = self.counters[arm.arm]
        buf = bytearray()
        up: socket.socket | None = None
        try:
            while not self._stop.is_set():
                head = _read_head(cs, buf)
                if head is None:
                    return
                t0 = time.time()
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
                if arm.home_canary and _hget(headers, HOME_CANARY_HEADER) != arm.home_canary:
                    ctr.refused_home_canary += 1                 # R-CC-WIT: the binary did not read the fake home
                    self._flag(arm.arm, "home_canary_missing", None)
                    _send_local(cs, 403, "Forbidden", b"fake home not read: the home canary is missing or wrong")
                    return
                if "chunked" in (_hget(headers, "transfer-encoding") or "").lower():
                    ctr.refused_chunked += 1
                    _send_local(cs, 411, "Length Required", b"chunked request bodies are refused")
                    return
                m = _UNIT.match(target)
                unit, path = (m.group(1), m.group(2)) if m else (None, target)
                if not self._path_allowed(role, method, path):
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
                rec = None
                if arm.mode == "record":
                    rec, refusal = self._inspect(arm, role, method, path, body)
                    rec.update(arm=arm.arm, port_role=role, unit=unit, t0=_iso(t0), **self.stage)
                    if refusal:
                        rec.update(refused=refusal, t1=_iso(time.time()))
                        self._write_call(rec)
                        _send_local(cs, 400, "Bad Request", f"refused: {refusal}".encode())
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
                        if rec is not None:
                            rec.update(upstream_error=type(e).__name__, t1=_iso(time.time()), thinking_injected=injected)
                            self._write_call(rec)
                        return
                try:
                    up.sendall(out + body)
                except OSError as e:
                    ctr.upstream_errors += 1
                    self.log(f"upstream send failed: {type(e).__name__}")
                    _send_local(cs, 502, "Bad Gateway", b"upstream send failed")
                    if rec is not None:                  # B-SEND: a key lost here must reach transport_lost
                        rec.update(upstream_error=type(e).__name__, t1=_iso(time.time()), thinking_injected=injected)
                        self._write_call(rec)
                    return
                ctr.bytes_up += len(out) + len(body)
                tee = TeeParser() if rec is not None else None
                keep, framer, ttfb, abandoned = self._pipe_response(cs, up, method, ctr, tee)
                if rec is not None:
                    self._finish_call(arm, rec, framer, tee, t0, ttfb, abandoned, injected, path)
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

    def _inspect(self, arm: ArmConfig, role: str, method: str, path: str, body: bytes) -> tuple[dict, str | None]:
        """Recording mode's checks before anything is forwarded. Returns the record so far and a refusal kind."""
        rec = {"v": 1, "endpoint": ("anthropic" if path.startswith("/anthropic/") else
                                    "balance" if path == "/user/balance" else
                                    "models" if path.startswith(("/models", "/v1/models")) else "v1"),
               "method": method.upper(), "requested_model": None, "stream": None, "temperature": None,
               "max_tokens": None, "response_format": None, "thinking_sent": None, "tools_offered": [],
               "tool_violation": False, "canary_hits": 0, "ancestor_canary_hits": 0, "owner_marker_hits": 0,
               "refused": None, "request_key": None}
        obj = None
        if body:
            try:
                obj = json.loads(body)
            except ValueError:
                rec["request_key"] = hashlib.sha256(body + b"\0" + arm.arm.encode()).hexdigest()
                self.counters[arm.arm].refused_unparsable += 1
                self._flag(arm.arm, "unparsable", rec["request_key"])
                return rec, "unparsable"
        rec["request_key"] = request_key(obj if obj is not None else {"path": path, "method": method.upper()}, arm.arm)
        if isinstance(obj, dict):
            rf = obj.get("response_format")
            th = obj.get("thinking")
            rec.update(requested_model=obj.get("model"), stream=obj.get("stream"), temperature=obj.get("temperature"),
                       max_tokens=obj.get("max_tokens"), response_format=rf.get("type") if isinstance(rf, dict) else rf,
                       thinking_sent=th.get("type") if isinstance(th, dict) else th)
        pinned = arm.reader_model if role == "reader" else arm.pinned_model
        if rec["endpoint"] in ("v1", "anthropic") and pinned and rec["requested_model"] != pinned:
            self.counters[arm.arm].model_mismatch += 1
            self._flag(arm.arm, "model_mismatch", rec["request_key"])
            return rec, "model_mismatch"
        offered = tools_offered(obj)
        rec["tools_offered"] = offered
        if any(tool_violation(n, set(arm.tools_allowed)) for n in offered):
            rec["tool_violation"] = True
            self.counters[arm.arm].tool_violation += 1
            self._flag(arm.arm, "tool_violation", rec["request_key"])
            return rec, "tool_violation"
        strings = strings_in(obj) if obj is not None else []
        raw = body.decode("utf-8", "replace")
        for kind, value in self._canaries.items():
            if value in raw or any(value in s for s in strings):
                if kind == "ancestor":
                    rec["ancestor_canary_hits"] += 1
                else:
                    rec["canary_hits"] += 1
        if self.markers is not None and strings and self.markers.hit(strings):
            rec["owner_marker_hits"] = 1
        ctr = self.counters[arm.arm]
        ctr.canary_hits += rec["canary_hits"]
        ctr.ancestor_canary_hits += rec["ancestor_canary_hits"]
        ctr.owner_marker_hits += rec["owner_marker_hits"]
        if rec["canary_hits"] or rec["owner_marker_hits"]:
            # Owner data or a planted secret must not reach the provider: refused, counted, and the stand's rows
            # are invalid anyway (P0h).
            kind = "canary" if rec["canary_hits"] else "owner_marker"
            self._flag(arm.arm, kind, rec["request_key"])
            return rec, kind
        return rec, None

    def _finish_call(self, arm: ArmConfig, rec: dict, framer, tee: TeeParser, t0: float, ttfb: float | None,
                     abandoned: bool, injected: int, path: str) -> None:
        ctype = _hget(framer.headers, "content-type") or ""
        facts = tee.result(ctype, rec["endpoint"], rec["response_format"])
        thinking = bool((facts["usage"]["reasoning"] or 0) > 0 or facts["reasoning_seen"]) \
            if rec["endpoint"] == "v1" else facts["thinking_block"]
        rec.update(status=framer.status, ttfb_ms=None if ttfb is None else round((ttfb - t0) * 1000, 1),
                   latency_ms=round((time.time() - t0) * 1000, 1), t1=_iso(time.time()),
                   response_model=facts["response_model"], system_fingerprint=facts["system_fingerprint"],
                   usage=facts["usage"], finish_reason=facts["finish_reason"], json_ok=facts["json_ok"],
                   content_empty=facts["content_empty"], parse_ok=facts["parse_ok"],
                   tools_called=facts["tools_called"], thinking=thinking, thinking_injected=injected,
                   client_abandoned=abandoned, complete=framer.done)
        if framer.protocol_error:                    # a reply that could not be parsed has no status (B-SEND2)
            rec.update(status=None, upstream_error="ProtocolError")
        called_bad = [n for n in facts["tools_called"] if tool_violation(n, set(arm.tools_allowed))]
        if called_bad:
            rec["tool_violation"] = True
            self.counters[arm.arm].tool_violation += 1
            self._flag(arm.arm, "tool_violation", rec["request_key"])
        if thinking and rec["endpoint"] in ("v1", "anthropic"):
            self.counters[arm.arm].thinking_calls += 1
            self._flag(arm.arm, "thinking_call", rec["request_key"])
        self._write_call(rec)

    def _write_call(self, rec: dict) -> None:
        with self._lock:
            self.counters[rec["arm"]].records += 1
            _append_jsonl(self.config.run_dir / "calls.jsonl", rec)

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

    def _pipe_response(self, cs: socket.socket, up: socket.socket, method: str, ctr: Counters,
                       tee: TeeParser | None = None):
        """Upstream bytes to the client as they arrive; a copy to ``tee`` only after each forwarded write.
        Returns (keep both connections, the framer, time of the first upstream byte, client abandoned)."""
        framer = ResponseFramer(method, tee.feed if tee is not None else None)
        sel = selectors.DefaultSelector()
        sel.register(up, selectors.EVENT_READ, "up")
        sel.register(cs, selectors.EVENT_READ, "client")
        watching_client = True
        ttfb = None
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
                            return False, framer, ttfb, True
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
                        return False, framer, ttfb, False
                    if not data:
                        try:
                            framer.eof()
                        except ProtocolError:
                            ctr.upstream_errors += 1
                            return False, framer, ttfb, False
                        break
                    if ttfb is None:
                        ttfb = time.time()
                    try:
                        cs.sendall(data)                     # forwarded first ...
                    except OSError:
                        ctr.client_abandoned += 1
                        return False, framer, ttfb, True
                    try:
                        used = framer.feed(data)             # ... then teed and framed
                    except ProtocolError:                    # B-SEND2: a reply we cannot frame is still a call
                        framer.protocol_error = True
                        ctr.upstream_errors += 1
                        ctr.bytes_down += len(data)
                        return False, framer, ttfb, False
                    ctr.bytes_down += len(data)
                    if used < len(data):                     # bytes past this response: upstream misbehaved
                        ctr.upstream_errors += 1
                        return False, framer, ttfb, False
                if framer.state == "until_close" and framer.done:
                    break
            return framer.done and not framer.close_after, framer, ttfb, False
        finally:
            sel.close()

    # the catcher ------------------------------------------------------------------------------------------------
    def _catcher(self, s: socket.socket, arm: ArmConfig) -> None:
        """Refuses everything - except, inside a declared window, a CONNECT to port 443 of one of that window's hosts
        (exact name) from one of that window's arms, which is tunnelled through the declared hop (AQ1, A3.a) and never
        dialled directly. Every request is recorded - host, port, window, the hop's answer, byte counts - never a byte
        of the traffic."""
        buf = bytearray()
        head = _read_head(s, buf)
        if head is None:
            return
        start, headers = _parse_head(head)
        method, _, rest = start.partition(" ")
        target = rest.rsplit(" ", 1)[0]
        port = 443
        if method.upper() == "CONNECT":
            host, _, p = target.rpartition(":")
            port = int(p) if p.isdigit() else 443
        else:
            mm = re.match(r"https?://([^/:]+)", target)
            host = mm.group(1) if mm else (_hget(headers, "host") or "?").split(":")[0]
        host, host_len = _safe_host(host)                # X5: a bounded hostname or "<invalid>", never raw bytes
        name = host.lower()
        window = None
        if method.upper() == "CONNECT" and port == 443 and host != "<invalid>":
            window = next((n for n, w in list(self.windows.items()) if name in w["hosts"] and arm.arm in w["arms"]), None)
        with self._lock:
            self.counters[arm.arm].catcher_hosts.append(host)
            self.counters[arm.arm].catcher_open += 1
        try:
            via = None if self.config.via_port is None else f"127.0.0.1:{self.config.via_port}"
            rec = {"arm": arm.arm, "host": host, "host_len": host_len, "port": port, "utc": _utc(), "window": window,
                   "via": via, "tunnelled": False, "refused": True, "hop_status": None, "bytes_up": 0, "bytes_down": 0}
            if window is None:
                _append_jsonl(self.config.run_dir / "catcher.jsonl", rec)
                _send_local(s, 403, "Forbidden", b"egress refused by the v3 launch contract")
                return
            if self.config.via_port is None:                 # never a direct dial: without the hop there is no path
                rec["hop_status"] = "no-hop"
                _append_jsonl(self.config.run_dir / "catcher.jsonl", rec)
                _send_local(s, 502, "Bad Gateway", b"no declared hop")
                return
            try:
                far = self._hop_tunnel(name)
            except ConnectRefused:
                self.counters[arm.arm].connect_refused += 1
                rec["hop_status"] = "refused"
                _append_jsonl(self.config.run_dir / "catcher.jsonl", rec)
                _send_local(s, 502, "Bad Gateway")
                return
            except OSError as e:
                rec["hop_status"] = f"error:{type(e).__name__}"
                _append_jsonl(self.config.run_dir / "catcher.jsonl", rec)
                _send_local(s, 502, "Bad Gateway")
                return
            rec.update(hop_status=200, tunnelled=True, refused=False)
            up = down = 0
            try:
                s.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if buf:
                    far.sendall(bytes(buf))
                    up += len(buf)
                sel = selectors.DefaultSelector()
                sel.register(s, selectors.EVENT_READ, (far, "up"))
                sel.register(far, selectors.EVENT_READ, (s, "down"))
                try:
                    done = False
                    while not done and not self._stop.is_set():
                        for k, _ in sel.select(timeout=1.0):
                            data = k.fileobj.recv(65536)
                            if not data:
                                done = True
                                break
                            k.data[0].sendall(data)
                            if k.data[1] == "up":
                                up += len(data)
                            else:
                                down += len(data)
                finally:
                    sel.close()
            except OSError:
                pass
            finally:
                far.close()
                rec.update(bytes_up=up, bytes_down=down, t_end=_utc())
                _append_jsonl(self.config.run_dir / "catcher.jsonl", rec)
        finally:
            with self._lock:                            # only after this connection's line is in the log
                self.counters[arm.arm].catcher_open -= 1

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
        body = json.loads(bytes(buf[:length]) or b"{}")
        if path == "/health":
            out = {"ok": True}
        elif path == "/counters":
            with self._lock:                            # a snapshot: catcher_hosts and catcher_open move together
                out = {a: {k: (list(v) if isinstance(v, list) else v) for k, v in vars(c).items()}
                       for a, c in self.counters.items()}
        elif path == "/flags":
            out = dict(self.flags)
        elif path == "/ollama":
            out = {a: leg.transport() for a, leg in self.legs.items()}
        elif path == "/stage" and method == "POST":
            self.stage = {"block": body.get("block"), "stage": body.get("stage")}
            out = {"ok": True}
        elif path == "/window" and method == "POST":
            name = str(body.get("name") or "")
            if not _ARM_NAME.fullmatch(name):
                _send_local(s, 400, "Bad Request", b"a window needs a name")
                return
            if body.get("state") == "open":             # A3.a: exact hosts and the arms the window is for
                hosts, arms = body.get("hosts") or [], body.get("arms") or []
                if (not hosts or not all(_window_host_ok(h) for h in hosts)
                        or not arms or not all(isinstance(a, str) and a in self.arms for a in arms)):
                    _send_local(s, 400, "Bad Request", b"a window needs exact lower-case hosts and known arms")
                    return
                self.windows[name] = {"hosts": frozenset(hosts), "arms": frozenset(arms)}
                _append_jsonl(self.config.run_dir / "windows_proxy.jsonl",
                              {"event": "open", "window": name, "hosts": sorted(hosts), "arms": sorted(arms), "utc": _utc()})
            else:
                self.windows.pop(name, None)
                _append_jsonl(self.config.run_dir / "windows_proxy.jsonl", {"event": "close", "window": name, "utc": _utc()})
            out = {"ok": True, "open": sorted(self.windows)}
        elif path == "/scan-files" and method == "POST":
            out = {"key_hits": self._scan_files(body.get("paths") or [])}
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


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


#: F-P2-6: the proxy runs one thread per connection, and on Windows an "ab" append is a seek then a write - two
#: tunnels closing together overwrote or lost each other's lines. Every JSONL append in this process takes this one
#: lock (catcher.jsonl, windows_proxy.jsonl, ollama.jsonl and the rest), so each line lands whole and none is lost.
_APPEND_LOCK = threading.Lock()


def _append_jsonl(path: Path, record: dict) -> None:
    line = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    with _APPEND_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "ab") as f:
            f.write(line)
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


def _owner_texts(claude_md: str | None, rules_dir: str | None) -> list[str]:
    """The proxy's single read exception (§2.6.1): read once, at start, into memory."""
    texts = []
    if claude_md and Path(claude_md).is_file():
        texts.append(Path(claude_md).read_text(encoding="utf-8", errors="replace"))
    if rules_dir and Path(rules_dir).is_dir():
        for f in sorted(Path(rules_dir).glob("*.md")):
            texts.append(f.read_text(encoding="utf-8", errors="replace"))
    return texts


def start_catch(config_path: str | os.PathLike, secrets: dict):
    """A3.a: the catcher-only proxy - no key read, no upstream probe, catcher ports and the control port only.
    Returns (proxy, ports, sha256 of the ports file)."""
    config = load_catch_config(config_path, secrets)
    proxy = Proxy(config, None)
    ports = proxy.start()
    return proxy, ports, write_ports(config.run_dir, ports)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the v3 recording proxy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--config", required=True)
    sv.add_argument("--key-file", required=True)
    sv.add_argument("--owner-claude-md")
    sv.add_argument("--owner-rules-dir")
    ct = sub.add_parser("catch")
    ct.add_argument("--config", required=True)
    args = ap.parse_args(argv)
    secrets = json.loads(sys.stdin.readline() or "{}")          # tokens: never argv, file or environment
    if args.cmd == "catch":
        proxy, _ports, digest = start_catch(args.config, secrets)
        print(f"READY {digest}", flush=True)
        try:
            while not proxy._stop.is_set():
                time.sleep(0.2)
        except KeyboardInterrupt:
            proxy.stop()
        return 0
    real_key = _under(os.path.realpath(args.key_file), os.path.realpath(SECRETS_ROOT)) or _under(args.key_file, SECRETS_ROOT)
    config = ProxyConfig.load(args.config, secrets, test_upstream_ok=not real_key)
    key = read_key(args.key_file)
    identity = secrets.get("identity") or {}
    texts = _owner_texts(args.owner_claude_md, args.owner_rules_dir)
    markers = OwnerMarkers(identity, texts) if (identity or texts) else None
    proxy = Proxy(config, key, markers=markers, canaries=secrets.get("canaries") or {})
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
