#!/usr/bin/env python3
"""PREREG-V3 plan step A2.5: research/v3/capture_deepseek.py, offline, against a fake DeepSeek on loopback.

* The client frames a response itself - Content-Length, chunked with trailers, an interim 100 head, read until
  close, 204 - and returns at the framed end while the server keeps the connection open; a connection that closes
  inside a response is an error, never a short capture.
* The header rule (the auditor's Q3): only the seven kept names stay verbatim; every other value becomes as many
  ``x``, the name and its whitespace kept, the byte count and the body unchanged, the names listed once.
* The requests: seven calls, synthetic and fixed, deepseek-flash everywhere, temperature 0, max_tokens 64 (256 for
  thinking on), thinking off on /v1 except the thinking-on call, no balance call; /anthropic authenticates with
  x-api-key and anthropic-version, /v1 with a bearer; the body sent is the body hashed.
* A whole run through the real proxy process and the real client process under the launch contract: the fake
  upstream receives the key and never the arm token, seven fixtures and a manifest appear with matching sha256,
  the client's spawn is required and witnessed with the script as its only read exception, the proxy's is optional
  with its reason, the check is complete with 0 native and 0 file-system hits even though the fixtures land inside
  the watched set (they are written after the check closes), and the key and the tokens appear in no file.
* The gates: a key echoed by the upstream makes /scan-files count it and no fixture is written; a provider-key-
  shaped header value blocks the fixtures too; a run label cannot be reused.
* The auditor's C1 (the hop): a real-key run without the declared hop is refused by name before anything is
  created or spawned; a hop cannot be combined with a test upstream; with a hop the proxy's config carries exactly
  {"via": {"host": "127.0.0.1", "port": N}} and the proxy really goes through it (CONNECT api.deepseek.com:443) - a
  hop that refuses leaves no fixture and a named problem; the probe's issuer lands in the manifest.
* The auditor's C2: every result check is pinned - native hits, an incomplete native check, fs hits, a request
  count other than seven, a catcher host, upstream errors, a refused CONNECT, a failed client, a missing key scan
  and a key-shaped file each yield a named problem, and main() exits 1 on any problem, 0 on none, with the hop
  taken from the contract's network.json.

No network; the key is a sentinel in a temporary file.

    python tests/_test_v3_capture.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite
import _tls_fake as TF  # noqa: E402

#: This interpreter, and its base when it is a venv: both named exceptions, or B1 refuses the test's own interpreter.
_TEST_EXC = {sys.executable: "the test interpreter"}
if getattr(sys, "_base_executable", sys.executable) != sys.executable:
    _TEST_EXC[sys._base_executable] = "the test interpreter's base"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


L = _load("v3_launch_cap", ROOT / "research" / "v3" / "launch.py")
CAP = _load("v3_capture", ROOT / "research" / "v3" / "capture_deepseek.py")
SENTINEL_KEY = "nvt3-capture-KEYSENTINEL-9d2e41b7"

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_capture_"))


# ── framing ─────────────────────────────────────────────────────────────

def framed(pieces: list[bytes], *, keep_open: bool = True, timeout: float = 3.0):
    """Serve ``pieces`` over a socket pair with small pauses; the server end stays open unless told otherwise."""
    a, b = socket.socketpair()

    def serve():
        for p in pieces:
            b.sendall(p)
            time.sleep(0.02)
        if not keep_open:
            b.close()

    threading.Thread(target=serve, daemon=True).start()
    try:
        return CAP.read_response(a, timeout=timeout)
    finally:
        a.close()
        if keep_open:
            time.sleep(0.05)
            b.close()


def attempt(pieces: list[bytes], **kw):
    """framed(), with an exception turned into its type name as the bytes: a failed check by name, never a crash."""
    try:
        return framed(pieces, **kw)
    except Exception as e:  # noqa: BLE001
        return type(e).__name__.encode(), []


print("\n- the client frames each response itself -")
CL = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n{\"a\":\"xyz\"}"
got, arr = attempt([CL[:20], CL[20:50], CL[50:]])
check("Content-Length: exactly the response, returned while the connection stays open", got == CL, repr(got[-20:]))
check("arrivals record each recv's time and size", len(arr) >= 1 and sum(n for _t, n in arr) == len(CL))
CH = (b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
      + b"".join(b"%x\r\n%s\r\n" % (len(e), e) for e in (b": keep-alive\n\n", b'data: {"x":1}\n\ndata: [DONE]\n\n'))
      + b"0\r\nX-Trailer: t\r\n\r\n")
got, _ = attempt([CH[:70], CH[70:95], CH[95:]])
check("chunked with a trailer: to the empty line after the last chunk, the connection still open", got == CH,
      repr(got[-30:]))
got, _ = attempt([CH + b"HTTP/1.1 200 OK\r\n"])
check("bytes past the framed end are not part of the response", got == CH)
INTERIM = b"HTTP/1.1 100 Continue\r\n\r\n" + CL
got, _ = attempt([INTERIM])
check("an interim 100 head stays in the bytes and the final response is framed", got == INTERIM)
UC = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nuntil close"
got, _ = attempt([UC], keep_open=False)
check("no length and no chunking: read until the server closes", got == UC)
NC = b"HTTP/1.1 204 No Content\r\nDate: x\r\n\r\n"
got, _ = attempt([NC])
check("204: the head alone", got == NC)
try:
    framed([CH[:90]], keep_open=False)
    check("a connection closed inside a chunked response is an error", False)
except CAP.FrameError:
    check("a connection closed inside a chunked response is an error", True)
try:
    framed([b"HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\nshort"], keep_open=False)
    check("a connection closed before Content-Length is reached is an error", False)
except CAP.FrameError:
    check("a connection closed before Content-Length is reached is an error", True)

print("\n- the header rule (Q3) -")
BODY = b'{"id":"cmpl-1","x-request-id":"stays in the body"}'
RESP = (b"HTTP/1.1 200 OK\r\nDate: Sat, 26 Sep 2026 09:00:00 GMT\r\nContent-Type: application/json\r\n"
        b"Content-Length: " + str(len(BODY)).encode() + b"\r\nConnection: keep-alive\r\n"
        b"Set-Cookie: __cf_bm=COOKIEVALUE; path=/; HttpOnly\r\nx-request-id:  REQ-ID-42\r\nCF-RAY: 8c1f-AMS\r\n"
        b"x-ratelimit-remaining-requests: 999\r\nCache-Control: no-cache\r\nContent-Encoding: identity\r\n"
        b"Transfer-Encoding: identity\r\nx-ds-org: ORG-7\r\n\r\n" + BODY)
red, names = CAP.redact_headers(RESP)
check("the byte count is unchanged", len(red) == len(RESP))
check("the body is untouched", red.endswith(BODY))
for kept in (b"Date: Sat, 26 Sep 2026 09:00:00 GMT", b"Content-Type: application/json", b"Connection: keep-alive",
             b"Cache-Control: no-cache", b"Content-Encoding: identity", b"Transfer-Encoding: identity",
             b"Content-Length: " + str(len(BODY)).encode()):
    check(f"kept verbatim: {kept.split(b':')[0].decode()}", kept in red)
for secret in (b"COOKIEVALUE", b"REQ-ID-42", b"8c1f-AMS", b"999", b"ORG-7"):
    check(f"replaced: {secret.decode()}", secret not in red[:red.index(b"\r\n\r\n")])
check("the name and its whitespace stay, the value is x", b"x-request-id:  xxxxxxxxx\r\n" in red
      and b"Set-Cookie: " + b"x" * len(b"__cf_bm=COOKIEVALUE; path=/; HttpOnly") + b"\r\n" in red)
check("the redacted names are listed once each, lower-cased",
      names == ["set-cookie", "x-request-id", "cf-ray", "x-ratelimit-remaining-requests", "x-ds-org"], str(names))
red2, names2 = CAP.redact_headers(b"HTTP/1.1 100 Continue\r\nX-Interim: v\r\n\r\n" + RESP)
check("an interim head is redacted too, and the final head after it",
      b"X-Interim: x\r\n" in red2 and b"COOKIEVALUE" not in red2 and names2[0] == "x-interim")
check("a response with no head is returned as is", CAP.redact_headers(b"garbage") == (b"garbage", []))

print("\n- the requests (Q4) -")
names_ = [c["name"] for c in CAP.CALLS]
check("seven calls with unique names", len(CAP.CALLS) == 7 and len(set(names_)) == 7)
check("deepseek-flash on every call, temperature 0",
      all(c["body"]["model"] == "deepseek-flash" and c["body"]["temperature"] == 0 for c in CAP.CALLS))
check("max_tokens 64, and 256 only for the thinking-on call",
      all(c["body"]["max_tokens"] == (256 if c["name"] == "v1_sse_thinking_on" else 64) for c in CAP.CALLS))
check("thinking is off on every /v1 call but the thinking-on one, which turns it on explicitly",
      all(c["body"]["thinking"] == {"type": "enabled" if c["name"] == "v1_sse_thinking_on" else "disabled"}
          for c in CAP.CALLS if c["path"].startswith("/v1/")))
check("the endpoints: five /v1 chat calls and two /anthropic messages calls, no balance call",
      sorted(c["path"] for c in CAP.CALLS) == ["/anthropic/v1/messages"] * 2 + ["/v1/chat/completions"] * 5)
check("stream matches the body: SSE calls ask for it, the others do not",
      all(bool(c["body"].get("stream")) == c["stream"] for c in CAP.CALLS))
check("the /v1 SSE calls ask for usage in the stream",
      all(c["body"].get("stream_options") == {"include_usage": True} for c in CAP.CALLS
          if c["stream"] and c["path"].startswith("/v1/")))
check("both tool-call captures offer the same tool",
      [c["body"].get("tools") for c in CAP.CALLS if "tool" in c["name"]] == [[CAP._TOOL]] * 2)
anth = CAP.build_request(CAP.CALLS[5], port=1, unit="c1", token="TOK")
v1 = CAP.build_request(CAP.CALLS[0], port=1, unit="c1", token="TOK")
check("/anthropic authenticates with x-api-key and sends anthropic-version",
      b"x-api-key: TOK\r\n" in anth and b"anthropic-version: 2023-06-01\r\n" in anth and b"Authorization" not in anth)
check("/v1 authenticates with a bearer", b"Authorization: Bearer TOK\r\n" in v1 and b"x-api-key" not in v1)
check("the path carries the unit prefix", v1.startswith(b"POST /u/c1/v1/chat/completions HTTP/1.1\r\n"))
check("the body sent is the body hashed, and Content-Length matches it",
      v1.endswith(CAP.body_bytes(CAP.CALLS[0]))
      and f"Content-Length: {len(CAP.body_bytes(CAP.CALLS[0]))}\r\n".encode() in v1)


# ── a whole run ─────────────────────────────────────────────────────────

class FakeDeepSeek:
    """Plain HTTP/1.1 on loopback, one response per request, keep-alive. Records every raw request."""

    def __init__(self, *, leak_key: bool = False, key_shaped_header: bool = False):
        self.leak_key, self.key_shaped_header = leak_key, key_shaped_header
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.requests: list[bytes] = []
        self.sent: dict[str, bytes] = {}
        threading.Thread(target=self._loop, daemon=True).start()

    def close(self):
        self.sock.close()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()

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
                length = next((int(ln.split(b":", 1)[1]) for ln in head.split(b"\r\n")
                               if ln.lower().startswith(b"content-length:")), 0)
                while len(buf) < length:
                    buf += c.recv(65536)
                body = bytes(buf[:length])
                del buf[:length]
                self.requests.append(head + body)
                c.sendall(self._respond(head, body))
        except OSError:
            return
        finally:
            c.close()

    def _respond(self, head: bytes, body: bytes) -> bytes:
        req = json.loads(body)
        auth = next((ln.split(b":", 1)[1].strip() for ln in head.split(b"\r\n")
                     if ln.lower().startswith(b"authorization:")), b"")
        extra = (b"Set-Cookie: __cf_bm=COOKIE-SENTINEL; path=/\r\nx-request-id: REQ-SENTINEL\r\n"
                 b"x-ratelimit-remaining-requests: 4242\r\n")
        if self.key_shaped_header:
            extra += b"x-debug: sk-" + b"A" * 30 + b"\r\n"
        text = b"hello " + (auth if self.leak_key else b"")
        if req.get("stream"):
            events = [b": keep-alive\n\n", b'data: {"choices":[{"delta":{"content":"' + text + b'"}}]}\n\n',
                      b'data: {"usage":{"prompt_tokens":5}}\n\ndata: [DONE]\n\n']
            payload = b"".join(b"%x\r\n%s\r\n" % (len(e), e) for e in events) + b"0\r\n\r\n"
            resp = (b"HTTP/1.1 200 OK\r\nDate: Sat, 26 Sep 2026 09:00:00 GMT\r\nContent-Type: text/event-stream\r\n"
                    b"Transfer-Encoding: chunked\r\nConnection: keep-alive\r\nCache-Control: no-cache\r\n" + extra
                    + b"\r\n" + payload)
        else:
            out = b'{"id":"cmpl-1","choices":[{"message":{"content":"' + text + b'"}}]}'
            resp = (b"HTTP/1.1 200 OK\r\nDate: Sat, 26 Sep 2026 09:00:00 GMT\r\nContent-Type: application/json\r\n"
                    b"Content-Length: " + str(len(out)).encode() + b"\r\nConnection: keep-alive\r\n" + extra
                    + b"\r\n" + out)
        self.sent[json.dumps(req, sort_keys=True)] = resp
        return resp


class AnySampler:
    """A native-witness sampler with an identity for every pid and no connections: the harness's plumbing is what
    is under test here; the witness itself is tested in _test_v3_launch_witness.py and the psutil suite."""

    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0


def contract(tag: str):
    base = TMP / tag
    (base / "secrets").mkdir(parents=True)
    kf = base / "secrets" / "deepseek.env"
    kf.write_bytes(f"DEEPSEEK_API_KEY={SENTINEL_KEY}\n".encode())
    (base / "watched").mkdir()
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    c = L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                   owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                   conservation_root=base / "conservation", binary_exceptions=_TEST_EXC,
                   system_dirs=(Path(sys.executable).parent,))
    return c, kf, base


def run(tag: str, up: FakeDeepSeek, **kw):
    c, kf, base = contract(tag)
    out = kw.pop("out", base / "watched" / "fixtures")
    m = CAP.run_captures(c, Path(sys.executable), key_file=kf, run=kw.pop("run", "r1"), out_dir=out,
                         parent_env=os.environ, launch=L,
                         test_upstream={"host": "127.0.0.1", "port": up.port, "tls": False},
                         native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
                         fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]), client_timeout=120)
    return m, c, kf, base, out


print("\n- a whole run: the real proxy and client processes under the contract -")
up = FakeDeepSeek()
m, C, KF, BASE, OUT = run("ok", up)
check("the run reports no problem", m["problems"] == [], str(m["problems"]))
check("seven fixtures and a manifest", sorted(p.name for p in OUT.iterdir())
      == sorted([f"{c['name']}.bin" for c in CAP.CALLS] + ["MANIFEST.json"]), str(sorted(p.name for p in OUT.iterdir())))
man = json.loads((OUT / "MANIFEST.json").read_bytes())
check("each fixture's sha256 is the manifest's",
      all(CAP._sha256((OUT / e["file"]).read_bytes()) == e["sha256"] for e in man["captures"]))
check("each request body and its sha256 are recorded",
      all(e["request_sha256"] == CAP._sha256(CAP.body_bytes(c)) and e["request_body"] == c["body"]
          for e, c in zip(man["captures"], CAP.CALLS)))
check("the upstream received each body exactly as hashed, in order",
      [r.split(b"\r\n\r\n", 1)[1] for r in up.requests] == [CAP.body_bytes(c) for c in CAP.CALLS])
check("the upstream received the key, never the arm token",
      all(f"Bearer {SENTINEL_KEY}".encode() in r and b"nvt3-capture-" not in r.split(b"\r\n\r\n")[0].replace(
          SENTINEL_KEY.encode(), b"") for r in up.requests))
fx = {e["name"]: (OUT / e["file"]).read_bytes() for e in man["captures"]}
sent = list(up.sent.values())
check("each fixture is the upstream's response with only the header values replaced",
      all(len(fx[c["name"]]) == len(s) and fx[c["name"]].split(b"\r\n\r\n", 1)[1] == s.split(b"\r\n\r\n", 1)[1]
          for c, s in zip(CAP.CALLS, sent)))
check("no cookie, request id or rate-limit value reached a fixture",
      not any(s in b for b in fx.values() for s in (b"COOKIE-SENTINEL", b"REQ-SENTINEL", b"4242")))
check("the manifest names the replaced headers",
      all(e["redacted_headers"] == ["set-cookie", "x-request-id", "x-ratelimit-remaining-requests"]
          for e in man["captures"]), str(man["captures"][0]["redacted_headers"]))
check("SSE fixtures keep their chunked framing", all(b"Transfer-Encoding: chunked" in fx[c["name"]]
                                                     and fx[c["name"]].endswith(b"0\r\n\r\n")
                                                     for c in CAP.CALLS if c["stream"]))
check("the proxy counted seven requests, none abandoned or refused, no catcher host",
      m["proxy_counters"]["requests"] == 7 and m["proxy_counters"]["client_abandoned"] == 0
      and m["catcher_hosts"] == 0)
check("/scan-files reported 0 key hits and the regex found no key-shaped file",
      m["scan_key_hits"] == 0 and m["provider_key_shaped_files"] == 0)
recs = [json.loads(x) for x in L.spawns_log(C).read_bytes().decode().splitlines()]
cap = [r for r in recs if r.get("role") == "capture"]
prx = [r for r in recs if r.get("role") == "proxy"]
check("the client's spawn is required and witnessed, outside any stand",
      len(cap) == 1 and cap[0]["witness"] == {"native": "on", "requirement": "required", "unwitnessed_reason": None}
      and cap[0]["stand"] is None and not cap[0]["refused"], str(cap))
check("the client's only read exception is its script at argv index 1, and no PYTHONPATH",
      cap[0]["argv_exception"] == {"1": str(ROOT / "research" / "v3" / "capture_deepseek.py")}
      and cap[0]["env_exception"] == {} and "PYTHONPATH" not in cap[0]["env_names"])
check("the client's HTTP(S)_PROXY is set (its catcher)",
      {"HTTP_PROXY", "HTTPS_PROXY"} <= {n.upper() for n in cap[0]["env_names"]})
check("the proxy's spawn is optional with its recorded reason",
      len(prx) == 1 and prx[0]["witness"]["requirement"] == "optional" and prx[0]["witness"]["unwitnessed_reason"])
chk = json.loads((C.runs_root / "_witness" / "a2_5-r1.json").read_bytes())
check("the check record is complete with 0 native hits and 0 file-system hits",
      chk["complete"] is True and chk["native"]["hits"] == 0 and chk["fs"]["fs_hits"] == 0, str(chk.get("fs")))
check("W7 the proxy is an allowed loopback listener in the check record (its ports are no hit)",
      [x["reason"] for x in chk["native"]["allowed_listeners"]] == ["the v3 proxy"], str(chk["native"].get("allowed_listeners")))
check("the fixtures landed inside the watched set after the check closed (0 fs hits, files present)",
      m["check"]["fs_hits"] == 0 and (BASE / "watched" / "fixtures" / "MANIFEST.json").is_file())
everything = [p for p in BASE.rglob("*") if p.is_file() and p.parent.name != "secrets"]
check("the key appears in no file the run wrote (fixtures, manifest, spawn and check records, unit dirs)",
      all(SENTINEL_KEY.encode() not in p.read_bytes() for p in everything), str(len(everything)))
check("the arm and control tokens appear in no spawn record",
      b"nvt3-capture-" not in L.spawns_log(C).read_bytes().replace(b'"arm": "capture"', b"")
      and b"ctl-" not in L.spawns_log(C).read_bytes())
try:
    CAP.run_captures(C, Path(sys.executable), key_file=KF, run="r1", out_dir=TMP / "again", parent_env=os.environ,
                     launch=L, test_upstream={"host": "127.0.0.1", "port": up.port, "tls": False},
                     native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
                     fs=L.FsWitness([L.WatchSpec("watched", BASE / "watched")]))
    check("a run label cannot be reused", False)
except RuntimeError:
    check("a run label cannot be reused", True)
up.close()

print("\n- the gates: nothing is written when a capture holds a key -")
up2 = FakeDeepSeek(leak_key=True)
m2, _c2, _kf2, base2, out2 = run("leak", up2)
check("an upstream that echoes the key: /scan-files counts it and the run reports it",
      (m2["scan_key_hits"] or 0) >= 1 and any("key_hits" in p for p in m2["problems"]), str(m2["problems"]))
check("... and no fixture or manifest is written", not out2.exists() or not any(out2.iterdir()))
up2.close()
up3 = FakeDeepSeek(key_shaped_header=True)
m3, _c3, _kf3, base3, out3 = run("shaped", up3)
check("a provider-key-shaped header value: the regex counts the file and the run reports it",
      m3["provider_key_shaped_files"] >= 1 and any("provider-key-shaped" in p for p in m3["problems"]),
      str(m3["problems"]))
check("... and no fixture or manifest is written", not out3.exists() or not any(out3.iterdir()))
up3.close()

print("\n- C1: the declared hop -")
C9, KF9, BASE9 = contract("c1")
before = L.spawns_log(C9).read_bytes() if L.spawns_log(C9).exists() else b""
try:
    CAP.run_captures(C9, Path(sys.executable), key_file=CAP.SECRETS_ROOT / "deepseek.env", run="r1",
                     out_dir=BASE9 / "out", parent_env=os.environ, launch=L)
    check("C1 a real-key run without the declared hop is refused", False)
except RuntimeError as e:
    check("C1 a real-key run without the declared hop is refused by name, before anything is created or spawned",
          "(C1)" in str(e) and not (C9.runs_root / CAP.STAND).exists()
          and (L.spawns_log(C9).read_bytes() if L.spawns_log(C9).exists() else b"") == before, str(e))
check("C1 the real key is recognised by its written path only",
      CAP._real_key(Path(r"D:\Coding\_secrets\deepseek.env")) and not CAP._real_key(KF9))
try:
    CAP.run_captures(C9, Path(sys.executable), key_file=KF9, run="r2", out_dir=BASE9 / "out", parent_env=os.environ,
                     launch=L, via_port=1, test_upstream={"host": "127.0.0.1", "port": 1, "tls": False})
    check("C1 a hop with a test upstream is refused", False)
except ValueError:
    check("C1 a hop with a test upstream is refused", not (C9.runs_root / CAP.STAND / "r2").exists())
hop9 = TF.TunnelHop(1, reply=b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
m9 = CAP.run_captures(C9, Path(sys.executable), key_file=KF9, run="r3", out_dir=BASE9 / "out", parent_env=os.environ,
                      launch=L, via_port=hop9.port,
                      native=L.NativeEgressWitness(sampler=AnySampler(), tick_s=60, jobs=None),
                      fs=L.FsWitness([L.WatchSpec("watched", BASE9 / "watched")]), client_timeout=60)
cfg9 = json.loads((C9.runs_root / CAP.STAND / "r3" / "_proxy" / "proxy_config.json").read_bytes())
check("C1 with a hop the proxy's config carries exactly {via: {127.0.0.1, port}}",
      cfg9.get("via") == {"host": "127.0.0.1", "port": hop9.port} and "upstream" not in cfg9, str(cfg9))
check("C1 the proxy really goes through it: CONNECT api.deepseek.com:443 on the hop",
      hop9.connects[:1] == [b"CONNECT api.deepseek.com:443 HTTP/1.1"], str(hop9.connects))
check("C1 a refusing hop: a named problem, no fixture, no probe record",
      any("did not report READY" in p for p in m9["problems"]) and not (BASE9 / "out").exists()
      and m9["proxy_upstream_via"] is None, str(m9["problems"]))
hop9.close()
pd = BASE9 / "pd"
pd.mkdir()
(pd / "upstream_tls.jsonl").write_bytes(b'{"issuer_o": "Amazon", "issuer_cn": "Amazon RSA 2048 M01", '
                                        b'"not_after": "Dec 25 23:59:59 2026 GMT", "tls_version": "TLSv1.3", "via": null}\n')
check("C1 the probe's record becomes proxy_upstream_via {host, port, issuer, notAfter, TLS version}",
      CAP.upstream_via(pd, 10809) == {"host": "127.0.0.1", "port": 10809, "issuer_o": "Amazon",
                                      "issuer_cn": "Amazon RSA 2048 M01", "not_after": "Dec 25 23:59:59 2026 GMT",
                                      "tls_version": "TLSv1.3"}
      and CAP.upstream_via(pd, None) is None and CAP.upstream_via(BASE9 / "nowhere", 10809) is None)
check("C1 the whole run's manifest names proxy_upstream_via (null without a hop)", m["proxy_upstream_via"] is None)

print("\n- C2: every result check is pinned -")
CLEAN = dict(spawn_error=None, rc=0, scan={"key_hits": 0}, counters={"requests": 7, "catcher_hosts": []},
             check={"native": {"hits": 0, "complete": True}, "fs": {"fs_hits": 0}}, key_shaped=[], n_calls=7)
check("C2 a clean result has no problem", CAP.judge(**CLEAN) == [], str(CAP.judge(**CLEAN)))
for label, change, want in (
        ("native hits = 1", {"check": {"native": {"hits": 1, "complete": True}, "fs": {"fs_hits": 0}}}, "native witness counted 1"),
        ("native complete = False", {"check": {"native": {"hits": 0, "complete": False}, "fs": {"fs_hits": 0}}}, "not complete"),
        ("fs_hits = 1", {"check": {"native": {"hits": 0, "complete": True}, "fs": {"fs_hits": 1}}}, "file-system witness counted 1"),
        ("6 requests instead of 7", {"counters": {"requests": 6, "catcher_hosts": []}}, "saw 6 requests, not 7"),
        ("a catcher host", {"counters": {"requests": 7, "catcher_hosts": ["example.org"]}}, "catcher saw 1 host"),
        ("upstream_errors = 1", {"counters": {"requests": 7, "upstream_errors": 1, "catcher_hosts": []}}, "upstream_errors = 1"),
        ("connect_refused = 1", {"counters": {"requests": 7, "connect_refused": 1, "catcher_hosts": []}}, "connect_refused = 1"),
        ("client_abandoned = 1", {"counters": {"requests": 7, "client_abandoned": 1, "catcher_hosts": []}}, "client_abandoned = 1"),
        ("the client exited 3", {"rc": 3}, "exited with 3"),
        ("no key scan", {"scan": {}}, "key_hits 0"),
        ("a key-shaped file", {"key_shaped": ["x.bin"]}, "provider-key-shaped"),
        ("a refused spawn", {"spawn_error": ["the proxy did not report READY in time"]}, "refused a spawn")):
    got = CAP.judge(**{**CLEAN, **change})
    check(f"C2 {label}: a named problem", len(got) == 1 and want in got[0], str(got))


class FakeLaunch:
    """launch's network_via_port on the test contract; nothing else is reached by main()."""
    network_via_port = staticmethod(L.network_via_port)


seen_via = []


def fake_run(problems):
    def run(c, python, **kw):
        seen_via.append(kw.get("via_port"))
        return {"problems": problems, "captures": [], "check": {}}
    return run


(C9.runs_root / "_config").mkdir(parents=True, exist_ok=True)
(C9.runs_root / "_config" / "network.json").write_bytes(b'{"via": {"host": "127.0.0.1", "port": 10809}}')
argv9 = ["--python", sys.executable, "--run", "m1", "--key-file", str(KF9), "--out", str(BASE9 / "mout")]
rc_bad = CAP.main(argv9, _launch=FakeLaunch, _contract=C9, _run=fake_run(["the native witness counted 1 egress hit(s)"]))
rc_ok = CAP.main(argv9, _launch=FakeLaunch, _contract=C9, _run=fake_run([]))
check("C2 main() exits 1 on any problem and 0 on none", rc_bad == 1 and rc_ok == 0, f"{rc_bad} {rc_ok}")
check("C2 main() hands the run the hop from the contract's network.json", seen_via == [10809, 10809], str(seen_via))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 capture: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
