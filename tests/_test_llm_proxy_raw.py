#!/usr/bin/env python3
"""PREREG-V3 TB1, step A2.4: the proxy's raw-forward mode, against a fake upstream on loopback.

* Every property of research/_llm_proxy_selftest.py holds: the key is injected and the client's token is not
  forwarded, only allowlisted headers go upstream, request and response bytes are identical, a stream reaches the
  client before the upstream finishes, an upstream 500 is not retried, a client that leaves closes the upstream
  within 1 s, a missing or wrong token is refused locally, one client connection keeps one upstream connection,
  every listener is on 127.0.0.1.
* The declared thinking fallback inserts exactly one field, only under branch (b), only for a fallback arm, and
  leaves a body that already says ``thinking`` alone.
* The catcher records a CONNECT's host and refuses it; a chunked request body, a path outside /v1, /chat,
  /anthropic and /models, and a pipelined request are refused without an upstream connection; ``Expect:
  100-continue`` is answered locally; an unreachable upstream is a 502 whose log line names only an error type.
* The key sentinel appears nowhere: not in stdout, stderr, the proxy's log lines, its counters, any file it wrote,
  the spawn records, or the environment ``launch`` builds for it.
* ``launch.spawn_proxy`` starts the real process under the contract: tokens on stdin only, READY carrying the
  ports file's sha256, the control port answering, a clean shutdown; its read exceptions are its script and the key
  file at their argv indexes, nothing wider (the auditor's X6).
* The auditor's X7: with the real key, a run_dir or scan root outside the polygon runs tree is refused at load.
* The auditor's R4 ruling, the CONNECT hop: ``via`` is exactly {host 127.0.0.1, port}; any other host, key (a
  target, a scheme, userinfo) or top-level config key is refused at load, and a hop with a plain-text upstream is
  refused. Through a fake hop on loopback the proxy sends ``CONNECT api.deepseek.com:443`` with the matching Host,
  and after a 200 its next bytes are a TLS ClientHello naming api.deepseek.com - never the key or an HTTP request
  in clear. A reply other than 200, or stray bytes after it, sends nothing more, is a 502 counted as
  connect_refused, and is not retried. At start main() probes the TLS upstream and, if the hop refuses, exits with
  UPSTREAM_FAILED before READY. main() never hands in its own TLS context.
* TLS verification inside the tunnel (the auditor's H3): the hop answers 200 and tunnels to a local TLS server whose
  self-signed *.deepseek.com certificate is made at test time (``cryptography`` if importable, else the ``openssl``
  CLI; never committed). The proxy's default verification refuses it: no handshake completes, the server receives
  0 bytes of application data - so no key - and the client gets 502. A control run whose TLS context trusts that
  certificate completes the round trip (200), and the key reaches the server only inside TLS, never in clear on the
  hop. With neither tool available the check prints a named SKIP and is not counted as passed.
* The auditor's A2.6 early probe: after a response to a request that said ``Connection: close`` - or was HTTP/1.0
  without keep-alive - the client sees EOF at once; HTTP/1.1 by default and HTTP/1.0 with keep-alive stay open.
* The auditor's X1-X5: the key goes only to api.deepseek.com:443 over TLS - another host or port is refused in the
  config and in the config file, and a test upstream (plain text, 127.0.0.1) is accepted only when the key is not
  the real one; /scan-files opens nothing outside the run directory and its declared roots, nothing naming the
  quarantine, nothing reached through a junction; a header carrying CR, LF or NUL is refused and never written
  upstream; the catcher records a bounded hostname, never a child's arbitrary bytes.

No network; the key is a sentinel in a temporary file.

    python tests/_test_llm_proxy_raw.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite

#: This interpreter, and its base when it is a venv (the bare CI-like interpreter is one): both named exceptions,
#: or B1 (a venv's base must be in the polygon) refuses the test's own interpreter - correctly.
_TEST_EXC = {sys.executable: "the test interpreter"}
if getattr(sys, "_base_executable", sys.executable) != sys.executable:
    _TEST_EXC[sys._base_executable] = "the test interpreter's base"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load("v3_llm_proxy", ROOT / "research" / "_llm_proxy.py")
ST = _load("v3_llm_proxy_selftest", ROOT / "research" / "_llm_proxy_selftest.py")
L = _load("v3_launch_px", ROOT / "research" / "v3" / "launch.py")
KEY = ST.SENTINEL_KEY.encode()

PASSED = FAILED = 0


def wait_until(cond, timeout: float = 10.0, step: float = 0.02) -> bool:
    """Poll until cond() holds or the timeout passes - an event wait, never a fixed sleep (CI 36292260735)."""
    import time as _t  # noqa: PLC0415
    end = _t.monotonic() + timeout
    while True:
        try:
            if cond():
                return True
        except Exception:  # noqa: BLE001 - a record not yet written reads as "not yet"
            pass
        if _t.monotonic() > end:
            return False
        _t.sleep(step)


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_proxy_raw_"))
out_buf, err_buf = io.StringIO(), io.StringIO()
logs: list[str] = []

print("\n- the selftest's properties -")
with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
    results, ctx = ST.run_checks(P, tmp=TMP / "selftest", log=logs.append)
for name, ok, detail in results:
    check(name, ok, detail)


def serve(**kw):
    up = ST.FakeUpstream()
    arms = kw.pop("arms", [P.ArmConfig(arm="a1", token=ST.TOKEN)])
    cfg = P.ProxyConfig(arms=arms, run_dir=TMP / "run", upstream_host="127.0.0.1", upstream_port=kw.pop("port", up.port),
                        upstream_tls=False, control_token="ctl", **kw)
    keyfile = TMP / "deepseek.env"
    keyfile.write_bytes(f"DEEPSEEK_API_KEY={ST.SENTINEL_KEY}\n".encode())
    px = P.Proxy(cfg, P.read_key(keyfile), log=logs.append)
    return px, px.start(), up


def exchange(port: int, raw: bytes, timeout: float = 3.0) -> bytes:
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(raw)
    got = ST._read_all(s, timeout)
    s.close()
    return got


def request(port, path, body, token=ST.TOKEN, extra=""):
    raw = ST._request(port, path, token=token, body=body, extra=extra)
    return raw.replace(b"User-Agent: selftest\r\n", b"Connection: close\r\n")


print("\n- the declared thinking fallback -")
BODY = b'{"model":"deepseek-flash","messages":[]}'
arms = [P.ArmConfig(arm="fb", token=ST.TOKEN, thinking_route="fallback"),
        P.ArmConfig(arm="doc", token=ST.TOKEN + "d", thinking_route="documented")]
for branch, arm, token, body, want, label in (
        ("b", "fb", ST.TOKEN, BODY, b'{"thinking":{"type":"disabled"},"model":"deepseek-flash","messages":[]}',
         "branch (b), fallback arm: one field at the front"),
        ("unset", "fb", ST.TOKEN, BODY, BODY, "branch unset: the body is untouched"),
        ("a", "fb", ST.TOKEN, BODY, BODY, "branch (a): the body is untouched"),
        ("b", "doc", ST.TOKEN + "d", BODY, BODY, "branch (b), documented-route arm: untouched"),
        ("b", "fb", ST.TOKEN, b'{"thinking":{"type":"enabled"},"model":"m"}', b'{"thinking":{"type":"enabled"},"model":"m"}',
         "a body that already says thinking: untouched")):
    px, ports, up = serve(arms=arms, thinking_branch=branch)
    exchange(ports["arms"][arm]["write"], request(ports["arms"][arm]["write"], "/v1/chat/completions", body, token))
    sent = up.requests[-1].partition(b"\r\n\r\n")[2] if up.requests else b""
    check(label, sent == want, sent.decode("utf-8", "replace"))
    if branch == "b" and arm == "fb" and body == BODY:
        check("... and it is counted as thinking_injected", px.counters["fb"].thinking_injected == 1)
    px.stop()
    up.close()

print("\n- R-CC-WIT: the Claude Code arm's home canary, a positive control on every request -")
CAN = "ab" * 16
px, ports, up = serve(arms=[P.ArmConfig(arm="cc", token=ST.TOKEN, home_canary=CAN),
                            P.ArmConfig(arm="plain", token=ST.TOKEN + "p")])
wp_cc, wp_plain = ports["arms"]["cc"]["write"], ports["arms"]["plain"]["write"]
n0 = len(up.requests)
got = exchange(wp_cc, request(wp_cc, "/v1/chat/completions", BODY))
check("a Claude Code request without the home canary is refused locally, never forwarded ('fake home not read')",
      got.startswith(b"HTTP/1.1 403") and b"fake home not read" in got and len(up.requests) == n0
      and px.counters["cc"].refused_home_canary == 1, got[:80].decode("latin-1"))
got = exchange(wp_cc, request(wp_cc, "/v1/chat/completions", BODY, extra="x-nvt3-home-canary: " + "cd" * 16 + "\r\n"))
check("... and one with another canary value too",
      got.startswith(b"HTTP/1.1 403") and len(up.requests) == n0 and px.counters["cc"].refused_home_canary == 2)
got = exchange(wp_cc, request(wp_cc, "/v1/chat/completions", BODY, extra=f"X-Nvt3-Home-Canary: {CAN}\r\n"))
check("the canary present (any header case): forwarded, and the header itself never reaches the upstream",
      got.startswith(b"HTTP/1.1 200") and len(up.requests) == n0 + 1 and CAN.encode() not in up.requests[-1]
      and b"home-canary" not in up.requests[-1].lower(), got[:80].decode("latin-1"))
flags_file = TMP / "run" / "flags.jsonl"
flags = [json.loads(x) for x in flags_file.read_bytes().decode().splitlines() if x] if flags_file.exists() else []
check("each missing canary is a flag 'home_canary_missing' on the arm, without the value",
      sum(1 for f in flags if f.get("kind") == "home_canary_missing" and f.get("arm") == "cc") == 2
      and CAN not in json.dumps(flags), str([f.get("kind") for f in flags]))
cfg_file = TMP / "cc_config.json"
cfg_file.write_bytes(json.dumps({"arms": [{"arm": "cc"}, {"arm": "plain"}], "run_dir": str(TMP / "run_cc")}).encode())
loaded = safely_load = None
try:
    loaded = P.ProxyConfig.load(cfg_file, {"tokens": {"cc": ST.TOKEN, "plain": ST.TOKEN + "p"},
                                           "home_canaries": {"cc": CAN}}, test_upstream_ok=True)
except Exception as e:  # noqa: BLE001 - a crash is a named FAIL of the row below
    print(f"       (load raised {type(e).__name__}: {e})")
check("the home canary reaches the proxy with the tokens, on stdin (the secrets), per arm - never in the config file",
      loaded is not None and {a.arm: a.home_canary for a in loaded.arms} == {"cc": CAN, "plain": ""}
      and CAN.encode() not in cfg_file.read_bytes())
got = exchange(wp_plain, request(wp_plain, "/v1/chat/completions", BODY, token=ST.TOKEN + "p"))
check("an arm without a home canary is unaffected", got.startswith(b"HTTP/1.1 200")
      and px.counters["plain"].refused_home_canary == 0)
px.stop()
up.close()


def raises(fn, exc) -> bool:
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def load_error(arms: list, home_canaries) -> str:
    """The ValueError a config with these arms and stdin canaries raises at load, or '' when it loads."""
    f = TMP / "fcan_config.json"
    f.write_bytes(json.dumps({"arms": [{"arm": a} for a in arms], "run_dir": str(TMP / "run_fcan")}).encode())
    sec = {"tokens": {a: ST.TOKEN + a for a in arms}}
    if home_canaries is not None:
        sec["home_canaries"] = home_canaries
    try:
        P.ProxyConfig.load(f, sec, test_upstream_ok=True)
    except ValueError as e:
        return str(e) or "ValueError"
    except Exception as e:  # noqa: BLE001 - another exception is not the named refusal
        return f"not a ValueError: {type(e).__name__}: {e}"
    return ""


print("\n- F-CAN: the positive control cannot be switched off by a missing canary -")
check("the Claude Code arm is named the same in the proxy and in launch",
      getattr(P, "HOME_CANARY_ARM", None) == "claude-code-memory")
err = load_error(["claude-code-memory", "mem0"], None)
check("F-CAN a claude-code-memory arm with no home canary on stdin: the proxy does not start, refused by name",
      "home canary" in err and "claude-code-memory" in err, err)
err = load_error(["claude-code-memory"], {"mem0": CAN})
check("F-CAN ... nor when the canaries name only other arms", "home canary" in err, err)
for bad in ("", "AB" * 16, "ab" * 15, "ab" * 17, "ab" * 16 + "\n", "zz" * 16, 12345):
    err = load_error(["claude-code-memory"], {"claude-code-memory": bad})
    check(f"F-CAN a home canary that is not 32 lowercase hex is refused: {bad!r}", "home canary" in err, err)
for bad in ("ab", None, 0, "ab" * 17):
    err = load_error(["mem0"], {"mem0": bad})
    check(f"F-CAN a malformed canary on any arm is refused, not only on Claude Code's: {bad!r}", "home canary" in err, err)
err = load_error(["claude-code-memory"], {"claude-code-memory": CAN, "claude-code-memroy": CAN})
check("F-CAN a canary for an arm the config does not have is refused (a mis-wired orchestrator)",
      "home canary" in err and "claude-code-memroy" in err, err)
err = load_error(["claude-code-memory"], ["claude-code-memory", CAN])
check("F-CAN home_canaries must be an object of arm -> canary", "home_canaries" in err and "object" in err, err)
check("F-CAN a well-formed canary for the Claude Code arm loads", load_error(["claude-code-memory", "mem0"],
                                                                            {"claude-code-memory": CAN}) == "")
check("F-CAN an ArmConfig built directly for claude-code-memory without a canary is refused too",
      raises(lambda: P.ArmConfig(arm="claude-code-memory", token=ST.TOKEN), ValueError))
check("F-CAN a catcher-only port of that name needs none (it never forwards an arm request)",
      not raises(lambda: P.ArmConfig(arm="claude-code-memory", mode="catch"), Exception))

print("\n- refusals, the catcher, 100-continue, an unreachable upstream -")
px, ports, up = serve()
wp, cp = ports["arms"]["a1"]["write"], ports["arms"]["a1"]["catcher"]
c0 = up.connections
got = exchange(wp, request(wp, "/v1/chat/completions", BODY).replace(b"Content-Length", b"Transfer-Encoding: chunked\r\nContent-Length"))
check("a chunked request body is refused (411)", got.startswith(b"HTTP/1.1 411"))
got = exchange(wp, request(wp, "/v2/secret", BODY))
check("a path outside the forwarded classes is refused (404)", got.startswith(b"HTTP/1.1 404"))
got = exchange(wp, request(wp, "/v1/chat/completions", BODY) + request(wp, "/v1/chat/completions", BODY))
check("a pipelined request is refused (400)", got.startswith(b"HTTP/1.1 400"), got[:40].decode())
time.sleep(0.1)
check("... none of them opened an upstream connection", up.connections == c0, str(up.connections - c0))
got = exchange(wp, request(wp, "/v1/chat/completions", BODY, extra="Expect: 100-continue\r\n"))
check("Expect: 100-continue is answered locally, then the response follows",
      got.startswith(b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200"), got[:60].decode())
check("Expect is not forwarded", b"expect" not in up.requests[-1].lower())
got = exchange(cp, b"CONNECT api.example.com:443 HTTP/1.1\r\nHost: api.example.com:443\r\n\r\n")
cj = (TMP / "run" / "catcher.jsonl").read_bytes().decode()
check("the catcher refuses a CONNECT (403) and records its host", got.startswith(b"HTTP/1.1 403")
      and '"host": "api.example.com"' in cj, cj[-120:])
px.stop()
up.close()
px, ports, up = serve(port=0)                            # port 0: the connect fails at once
wp = ports["arms"]["a1"]["write"]
got = exchange(wp, request(wp, "/v1/chat/completions", BODY))
check("an unreachable upstream is a 502, logged by error type only",
      got.startswith(b"HTTP/1.1 502") and any(line.startswith("upstream connect failed:") for line in logs), str(logs[-2:]))
px.stop()
up.close()

print("\n- the auditor's X1-X5 -")
for label, kw in (("api.openai.com", {"upstream_host": "api.openai.com"}),
                  ("api.deepseek.com on port 8443", {"upstream_port": 8443}),
                  ("a plain-text upstream other than loopback", {"upstream_host": "10.0.0.5", "upstream_tls": False})):
    try:
        P.ProxyConfig(arms=[], run_dir=TMP, **kw)
        check(f"X1 {label} is refused as the key's upstream", False)
    except ValueError:
        check(f"X1 {label} is refused as the key's upstream", True)
cfgf = TMP / "x2.json"
# The run_dir sits INSIDE the polygon runs tree, so X7 passes it and X2 alone must refuse (the auditor's probe:
# a real-key config with a plain loopback upstream would otherwise send the key in clear text to that port).
for label, up, ok_flag in (("a config file naming collector.example", {"host": "collector.example", "port": 443}, True),
                           ("a config file naming a loopback test upstream while the key is real",
                            {"host": "127.0.0.1", "port": 1, "tls": False}, False)):
    cfgf.write_bytes(json.dumps({"arms": [], "run_dir": str(P.POLYGON_RUNS / "x2-test"), "upstream": up}).encode())
    try:
        P.ProxyConfig.load(cfgf, {}, test_upstream_ok=ok_flag)
        check(f"X2 {label} is refused", False)
    except ValueError as e:
        check(f"X2 {label} is refused, by X2", "(X2)" in str(e), str(e))
cfgf.write_bytes(json.dumps({"arms": [], "run_dir": str(TMP / "x2"), "upstream": {"host": "127.0.0.1", "port": 1, "tls": False}}).encode())
check("X2 ... and accepted for a test with a sentinel key", P.ProxyConfig.load(cfgf, {}, test_upstream_ok=True).upstream_host == "127.0.0.1")
check("X2 the real secrets directory is what decides 'real key'", P._under(P.SECRETS_ROOT / "deepseek.env", P.SECRETS_ROOT))
for label, raw in (("scan_roots naming the owner's home", {"arms": [], "run_dir": str(P.POLYGON_RUNS / "v3" / "_proxy" / "x"),
                                                           "scan_roots": [str(Path.home())]}),
                   ("a run_dir outside the polygon runs tree", {"arms": [], "run_dir": str(TMP / "elsewhere")})):
    cfgf.write_bytes(json.dumps(raw).encode())
    try:
        P.ProxyConfig.load(cfgf, {}, test_upstream_ok=False)
        check(f"X7 with the real key, {label} is refused", False)
    except ValueError as e:
        check(f"X7 with the real key, {label} is refused", "X7" in str(e), str(e))
cfgf.write_bytes(json.dumps({"arms": [], "run_dir": str(P.POLYGON_RUNS / "v3" / "_proxy" / "x")}).encode())
check("X7 ... and a run_dir inside it is accepted", P.ProxyConfig.load(cfgf, {}, test_upstream_ok=False).run_dir
      == P.POLYGON_RUNS / "v3" / "_proxy" / "x")
run3 = TMP / "x3run"
run3.mkdir()
inside = run3 / "capture.bin"
inside.write_bytes(b"...." + KEY + b"....")
outside3 = TMP / "x3out" / "note.bin"
outside3.parent.mkdir()
outside3.write_bytes(KEY)
px3 = P.Proxy(P.ProxyConfig(arms=[], run_dir=run3), P.read_key(TMP / "deepseek.env"), log=logs.append)
opened: list[str] = []
_real_read = Path.read_bytes


def _spy(self):
    opened.append(str(self))
    return _real_read(self)


Path.read_bytes = _spy
try:
    hits_in = px3._scan_files([str(inside)])
    n0 = len(opened)
    hits_out = px3._scan_files([str(outside3), r"D:\Coding\_nevertwice_owner_data_quarantine\anything"])
    n_out = len(opened) - n0
finally:
    Path.read_bytes = _real_read
check("X3 /scan-files counts the key in a file inside the run directory", hits_in == 1)
check("X3 ... and opens nothing outside it, nor anything naming the quarantine (counted as refused)",
      hits_out == 0 and n_out == 0 and px3.scan_refused == 2, f"opened {n_out}, refused {px3.scan_refused}")
if os.name == "nt":
    try:
        import _winapi
        _winapi.CreateJunction(str(outside3.parent), str(run3 / "j"))
        n1 = len(opened)
        Path.read_bytes = _spy
        try:
            px3._scan_files([str(run3 / "j" / "note.bin")])
        finally:
            Path.read_bytes = _real_read
        check("X3 ... nor a file reached through a junction out of the run directory", len(opened) == n1)
        os.rmdir(run3 / "j")
    except OSError as e:
        print(f"       (no junction here: {type(e).__name__})")
px, ports, up = serve()
wp = ports["arms"]["a1"]["write"]
c0 = up.connections
got = exchange(wp, request(wp, "/v1/chat/completions", BODY).replace(b"Content-Type: application/json\r\n",
                                                                      b"Content-Type: application/json\nAccept-Encoding: gzip\r\n"))
check("X4 a header carrying a bare LF is refused (400) with no upstream connection",
      got.startswith(b"HTTP/1.1 400") and up.connections == c0, got[:40].decode("latin-1"))
head = px._outgoing_head("POST", "/v1/chat/completions", [("User-Agent", "x\nAccept-Encoding: gzip")], 2)
check("X4 ... and _outgoing_head never writes such a header", b"Accept-Encoding" not in head)
cp = ports["arms"]["a1"]["catcher"]
exchange(cp, f"CONNECT owner-data-{'Z' * 5000}:443 HTTP/1.1\r\nHost: x\r\n\r\n".encode())
last = (TMP / "run" / "catcher.jsonl").read_bytes().decode().splitlines()[-1]
rec5 = json.loads(last)
check("X5 the catcher records <invalid> and the length for a host that is not a hostname",
      rec5["host"] == "<invalid>" and rec5["host_len"] > 5000 and len(last) < 1000, last[:100])
px.stop()
up.close()


print("\n- R4: the CONNECT hop through the owner's loopback proxy -")


class FakeHop:
    """A loopback HTTP proxy that answers CONNECT with ``reply`` and records what follows (it never completes TLS)."""

    def __init__(self, reply: bytes):
        self.reply = reply
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.heads: list[bytes] = []
        self.after: list[bytes] = []
        self.everything = bytearray()
        self.connections = 0
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()

    def _conn(self, c):
        c.settimeout(2.0)
        buf = bytearray()
        try:
            while b"\r\n\r\n" not in buf:
                chunk = c.recv(4096)
                if not chunk:
                    return
                buf += chunk
            end = buf.index(b"\r\n\r\n") + 4
            self.heads.append(bytes(buf[:end]))
            self.everything += buf
            c.sendall(self.reply)
            rest = bytearray(buf[end:])
            try:
                while len(rest) < 16384:
                    chunk = c.recv(4096)
                    if not chunk:
                        break
                    rest += chunk
            except OSError:
                pass
            self.after.append(bytes(rest))
            self.everything += rest
        except OSError:
            pass
        finally:
            c.close()

    def close(self):
        self.sock.close()


def hop_proxy(hop: FakeHop):
    cfg = P.ProxyConfig(arms=[P.ArmConfig(arm="a1", token=ST.TOKEN)], run_dir=TMP / "hop", via_port=hop.port,
                        control_token="ctl", connect_timeout_s=5)
    px = P.Proxy(cfg, P.read_key(TMP / "deepseek.env"), log=logs.append)
    return px, px.start()


hopcfg = TMP / "via.json"
for label, raw in (("a hop on another host", {"via": {"host": "10.0.0.5", "port": 3128}}),
                   ("a hop named localhost (only the literal 127.0.0.1)", {"via": {"host": "localhost", "port": 3128}}),
                   ("a hop that also names a CONNECT target", {"via": {"host": "127.0.0.1", "port": 3128,
                                                                        "target": "collector.example:443"}}),
                   ("a hop with a scheme", {"via": {"host": "127.0.0.1", "port": 3128, "scheme": "https"}}),
                   ("a hop with userinfo", {"via": {"host": "127.0.0.1", "port": 3128, "userinfo": "u:p"}}),
                   ("a hop given as a URL string", {"via": "http://127.0.0.1:3128"}),
                   ("a top-level CONNECT target", {"connect_target": "collector.example:443"}),
                   ("a top-level TLS trust file", {"cafile": str(TMP / "evil-ca.pem")})):
    hopcfg.write_bytes(json.dumps({"arms": [], "run_dir": str(TMP / "hopcfg"), **raw}).encode())
    try:
        P.ProxyConfig.load(hopcfg, {}, test_upstream_ok=True)
        check(f"R4 {label} is refused at load", False)
    except ValueError as e:
        check(f"R4 {label} is refused at load", True, str(e))
for label, kw in (("a hop port 0", {"via_port": 0}), ("a hop port 70000", {"via_port": 70000}),
                  ("a hop port given as text", {"via_port": "3128"}), ("a hop port given as a bool", {"via_port": True}),
                  ("a hop in front of a plain-text test upstream", {"via_port": 3128, "upstream_host": "127.0.0.1",
                                                                     "upstream_tls": False})):
    try:
        P.ProxyConfig(arms=[], run_dir=TMP, **kw)
        check(f"R4 {label} is refused", False)
    except ValueError:
        check(f"R4 {label} is refused", True)
hopcfg.write_bytes(json.dumps({"arms": [], "run_dir": str(TMP / "hopcfg"), "via": {"host": "127.0.0.1", "port": 3128}}).encode())
okcfg = P.ProxyConfig.load(hopcfg, {}, test_upstream_ok=True)
check("R4 a hop of exactly {127.0.0.1, port} is accepted, and the upstream stays api.deepseek.com:443 over TLS",
      okcfg.via_port == 3128 and okcfg.upstream_host == "api.deepseek.com" and okcfg.upstream_port == 443
      and okcfg.upstream_tls)

hop = FakeHop(b"HTTP/1.1 200 Connection established\r\n\r\n")
px, ports = hop_proxy(hop)
wp = ports["arms"]["a1"]["write"]
got = exchange(wp, request(wp, "/v1/chat/completions", BODY), timeout=8)
wait_until(lambda: hop.heads and hop.after)            # the hop's reader thread records what followed the 200
check("R4 the proxy sends CONNECT to the code's constant target, with the matching Host",
      hop.heads[:1] == [b"CONNECT api.deepseek.com:443 HTTP/1.1\r\nHost: api.deepseek.com:443\r\n\r\n"], str(hop.heads[:1]))
first = hop.after[0] if hop.after else b""
check("R4 after a 200 the next bytes are a TLS handshake record (ClientHello) naming api.deepseek.com (SNI)",
      first[:1] == b"\x16" and first[1:2] == b"\x03" and b"api.deepseek.com" in first, repr(first[:12]))
check("R4 ... and never the key or an HTTP request in clear on the hop",
      KEY not in bytes(hop.everything) and b"POST " not in bytes(hop.everything)
      and b"Authorization" not in bytes(hop.everything))
check("R4 a tunnel whose TLS never completes is a 502 upstream error, not retried",
      got.startswith(b"HTTP/1.1 502") and px.counters["a1"].upstream_errors == 1 and hop.connections == 1
      and px.counters["a1"].connect_refused == 0, f"{got[:30]!r} {vars(px.counters['a1'])}")
px.stop()
hop.close()
for label, reply in (("a 403 reply", b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"),
                     ("a 200 reply followed by stray bytes", b"HTTP/1.1 200 OK\r\n\r\nSTRAY")):
    hop = FakeHop(reply)
    px, ports = hop_proxy(hop)
    wp = ports["arms"]["a1"]["write"]
    got = exchange(wp, request(wp, "/v1/chat/completions", BODY), timeout=8)
    wait_until(lambda: hop.after and px.counters["a1"].connect_refused >= 1)
    ctr = px.counters["a1"]
    check(f"R4 {label}: nothing more is sent, the client gets 502, counted as connect_refused, no retry",
          got.startswith(b"HTTP/1.1 502") and ctr.connect_refused == 1 and ctr.upstream_errors == 1
          and hop.connections == 1 and hop.after == [b""] and KEY not in bytes(hop.everything),
          f"{got[:30]!r} {vars(ctr)} after={hop.after!r}")
    px.stop()
    hop.close()
hop = FakeHop(b"HTTP/1.1 403 Forbidden\r\n\r\n")
try:
    hop_proxy(hop)[0].probe_upstream()
    check("R4 probe_upstream through a refusing hop raises ConnectRefused", False)
except P.ConnectRefused:
    check("R4 probe_upstream through a refusing hop raises ConnectRefused", True)
mcfg = TMP / "main_via.json"
mrun = TMP / "main_via_run"
mcfg.write_bytes(json.dumps({"arms": [{"arm": "a1"}], "run_dir": str(mrun), "via": {"host": "127.0.0.1", "port": hop.port}}).encode())
try:                                                   # a real process: a proxy that skipped the probe would serve forever
    r_main = subprocess.run([sys.executable, str(ROOT / "research" / "_llm_proxy.py"), "serve", "--config", str(mcfg),
                             "--key-file", str(TMP / "deepseek.env")], input=b"{}\n", capture_output=True, timeout=20)
    rc_main, printed = r_main.returncode, r_main.stdout.decode("utf-8", "replace")
except subprocess.TimeoutExpired as e:
    rc_main, printed = None, (e.stdout or b"").decode("utf-8", "replace")
check("R4 main() probes the TLS upstream first: a refusing hop is UPSTREAM_FAILED, exit 4, no READY, no ports file",
      rc_main == 4 and printed.startswith("UPSTREAM_FAILED ConnectRefused") and "READY" not in printed
      and not (mrun / "ports.json").exists(), f"{rc_main} {printed!r}")
hop.close()
import inspect  # noqa: E402

check("R4 main() never hands the proxy its own TLS context (verification is the default context's)",
      "ssl_context" not in inspect.getsource(P.main) and "CERT_NONE" not in inspect.getsource(P)
      and "check_hostname = False" not in inspect.getsource(P))

print("\n- R4: TLS verification inside the tunnel (H3) -")


def make_test_cert(d: Path):
    """A throwaway self-signed certificate for api.deepseek.com, made now and never committed: (cert, key, how)."""
    d.mkdir(parents=True, exist_ok=True)
    cert, keyf = d / "tls_cert.pem", d / "tls_key.pem"
    try:
        import datetime  # noqa: PLC0415

        from cryptography import x509  # noqa: PLC0415
        from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
        from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
        from cryptography.x509.oid import NameOID  # noqa: PLC0415
    except ImportError:
        x509 = None
    if x509 is not None:
        k = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "*.deepseek.com")])
        now = datetime.datetime.now(datetime.timezone.utc)
        c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
             .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
             .not_valid_after(now + datetime.timedelta(days=1))
             .add_extension(x509.SubjectAlternativeName([x509.DNSName("api.deepseek.com")]), critical=False)
             .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
             .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                          data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                          crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(k.public_key()), critical=False)
             .sign(k, hashes.SHA256()))
        keyf.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
        cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
        return cert, keyf, "cryptography"
    exe = shutil.which("openssl")
    if exe:
        env = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
        r = subprocess.run([exe, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                            "-subj", "/CN=*.deepseek.com", "-addext", "subjectAltName=DNS:api.deepseek.com",
                            "-keyout", str(keyf), "-out", str(cert)], capture_output=True, timeout=120, env=env)
        if r.returncode == 0 and cert.is_file() and keyf.is_file():
            return cert, keyf, "openssl"
    return None


class TlsServer:
    """A local TLS server with the throwaway certificate: counts completed handshakes and application bytes."""

    def __init__(self, cert: Path, keyf: Path):
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(str(cert), str(keyf))
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.handshakes = 0
        self.app = bytearray()
        self.errors: list[str] = []
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()

    def _conn(self, c):
        c.settimeout(5)
        try:
            s = self.ctx.wrap_socket(c, server_side=True)
        except (ssl.SSLError, OSError) as e:
            self.errors.append(type(e).__name__)
            c.close()
            return
        self.handshakes += 1
        try:
            buf = bytearray()
            while b"\r\n\r\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            self.app += buf
            s.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}")
        except (ssl.SSLError, OSError) as e:
            self.errors.append(type(e).__name__)
        finally:
            s.close()

    def close(self):
        self.sock.close()


class TunnelHop:
    """A loopback HTTP proxy that answers CONNECT with 200 and then pipes bytes to a local target port."""

    def __init__(self, target_port: int):
        self.target_port = target_port
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.seen = bytearray()                          # everything the proxy sent to the hop, head included
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()

    def _pipe(self, a, b, record: bool):
        try:
            while True:
                chunk = a.recv(65536)
                if not chunk:
                    break
                if record:
                    self.seen.extend(chunk)
                b.sendall(chunk)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _conn(self, c):
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            chunk = c.recv(4096)
            if not chunk:
                c.close()
                return
            buf += chunk
        self.seen.extend(buf)
        c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        t = socket.create_connection(("127.0.0.1", self.target_port))
        a = threading.Thread(target=self._pipe, args=(c, t, True), daemon=True)
        b = threading.Thread(target=self._pipe, args=(t, c, False), daemon=True)
        a.start()
        b.start()
        a.join(10)
        b.join(10)
        c.close()
        t.close()

    def close(self):
        self.sock.close()


made = make_test_cert(TMP / "tlscert")
if made is None:
    print("  SKIP R4 TLS verification inside the tunnel: neither cryptography nor openssl is available (not passed)")
else:
    cert, keyf, how = made
    print(f"       (throwaway certificate made with {how})")
    for label, mode in (("default verification", None), ("control: a context that trusts the test certificate", "trust")):
        srv = TlsServer(cert, keyf)
        th = TunnelHop(srv.port)
        cfg = P.ProxyConfig(arms=[P.ArmConfig(arm="a1", token=ST.TOKEN)], run_dir=TMP / "hoptls", via_port=th.port,
                            control_token="ctl", connect_timeout_s=5)
        trusted = ssl.create_default_context(cafile=str(cert)) if mode == "trust" else None
        px = P.Proxy(cfg, P.read_key(TMP / "deepseek.env"), log=logs.append, ssl_context=trusted)
        wp = px.start()["arms"]["a1"]["write"]
        got = exchange(wp, request(wp, "/v1/chat/completions", BODY), timeout=8)
        time.sleep(0.3)
        if mode is None:
            check("H3 the proxy's default verification refuses the untrusted certificate: no handshake completes",
                  srv.handshakes == 0, f"handshakes={srv.handshakes} errors={srv.errors}")
            check("H3 ... the TLS server receives 0 bytes of application data (no request, no key)",
                  len(srv.app) == 0 and KEY not in bytes(srv.app), f"{len(srv.app)} B")
            check("H3 ... and the client gets 502", got.startswith(b"HTTP/1.1 502"), repr(got[:30]))
        else:
            check("H3 control: with the certificate trusted, the round trip through the tunnel completes (200)",
                  got.startswith(b"HTTP/1.1 200") and got.endswith(b"{}") and srv.handshakes == 1,
                  f"{got[:30]!r} handshakes={srv.handshakes} errors={srv.errors}")
            check("H3 control: the key reaches the server only inside TLS - never in clear on the hop",
                  f"Authorization: Bearer {ST.SENTINEL_KEY}".encode() in bytes(srv.app) and KEY not in bytes(th.seen)
                  and b"Authorization" not in bytes(th.seen), f"{len(th.seen)} B on the hop")
        px.stop()
        th.close()
        srv.close()

print("\n- Connection: close is honoured (the auditor's A2.6 early probe) -")


def read_eof(port: int, raw: bytes, wait: float) -> tuple[bytes, float, bool]:
    """Send, read until EOF or ``wait`` seconds of silence: (bytes, seconds, whether EOF came)."""
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(raw)
    s.settimeout(wait)
    t0, out, eof = time.monotonic(), bytearray(), False
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                eof = True
                break
            out += chunk
    except OSError:
        pass
    s.close()
    return bytes(out), time.monotonic() - t0, eof


px, ports, up = serve()
wp = ports["arms"]["a1"]["write"]
base = ST._request(wp, "/v1/chat/completions", body=BODY)
for label, raw, want_eof in (
        ("HTTP/1.1 with Connection: close", base.replace(b"User-Agent: selftest\r\n", b"Connection: close\r\n"), True),
        ("HTTP/1.0 without keep-alive", base.replace(b" HTTP/1.1\r\n", b" HTTP/1.0\r\n", 1), True),
        ("HTTP/1.0 with Connection: keep-alive", base.replace(b" HTTP/1.1\r\n", b" HTTP/1.0\r\n", 1)
         .replace(b"User-Agent: selftest\r\n", b"Connection: keep-alive\r\n"), False),
        ("HTTP/1.1 by default", base, False)):
    got, secs, eof = read_eof(wp, raw, wait=1.5)
    if want_eof:
        check(f"{label}: EOF right after the response", got.startswith(b"HTTP/1.1 200") and eof and secs < 1.0,
              f"eof={eof} {secs:.2f}s")
    else:
        check(f"{label}: the connection stays open after the response", got.startswith(b"HTTP/1.1 200") and not eof,
              f"eof={eof} {secs:.2f}s")
px.stop()
up.close()


print("\n- the key never leaves the proxy -")
sweep = {"stdout": out_buf.getvalue().encode(), "stderr": err_buf.getvalue().encode(),
         "log lines": "\n".join(logs).encode(), "counters": json.dumps(
             {a: vars(c) for a, c in ctx["proxy"].counters.items()}, default=str).encode()}
for f in TMP.rglob("*"):
    if f.is_file() and f.name != "deepseek.env" and f.parent.name not in ("x3run", "x3out"):  # X3 fixtures hold it
        sweep[str(f.relative_to(TMP))] = f.read_bytes()
leaks = [k for k, v in sweep.items() if KEY in v]
check(f"the key sentinel is absent from {len(sweep)} outputs and files", leaks == [], str(leaks))
check("the key object never prints", str(P.read_key(TMP / "deepseek.env")) == "<redacted>"
      and repr(P.read_key(TMP / "deepseek.env")) == "<redacted>")
try:
    P.read_key(TMP / "missing.env")
    check("a missing key file fails without echoing a path's content", False)
except RuntimeError as e:
    check("a missing key file fails without echoing a path's content", ST.SENTINEL_KEY not in str(e))

print("\n- the real proxy process, started by the launch contract -")
C = L.Contract(polygon_root=TMP / "polygon", runs_root=TMP / "polygon" / "runs" / "v3", repo_root=ROOT,
               owner_home=TMP / "owner", secrets_dir=TMP / "secrets", quarantine_root=TMP / "quarantine",
               conservation_root=TMP / "conservation", binary_exceptions=_TEST_EXC,
               system_dirs=(Path(sys.executable).parent,))
(TMP / "secrets").mkdir()
kf = TMP / "secrets" / "deepseek.env"
kf.write_bytes(f"DEEPSEEK_API_KEY={ST.SENTINEL_KEY}\n".encode())
up = ST.FakeUpstream()
pdir = C.runs_root / "_proxy" / "t1"
pdir.mkdir(parents=True)
cfgp = pdir / "proxy_config.json"
cfgp.write_bytes(json.dumps({"arms": [{"arm": "a1"}], "run_dir": str(pdir),
                             "upstream": {"host": "127.0.0.1", "port": up.port, "tls": False}}).encode())
unit = L.make_unit_dirs(C, "_proxy", "t1", "proxy", "p1")
secrets = {"tokens": {"a1": ST.TOKEN}, "control_token": "ctl-" + "c" * 32}
child, ports = L.spawn_proxy(C, sys.executable, script=ROOT / "research" / "_llm_proxy.py", config_path=cfgp,
                             key_file=kf, stdin_secrets=secrets, unit=unit, parent_env=os.environ)
check("READY came with the ports file's sha256, and every port is set",
      set(ports) == {"arms", "control"} and ports["arms"]["a1"]["write"] > 0)
wp = ports["arms"]["a1"]["write"]
got = exchange(wp, request(wp, "/u/p1/v1/chat/completions", BODY))
check("a request through the real process reaches the fake upstream with the key",
      got.startswith(b"HTTP/1.1 200") and f"Authorization: Bearer {ST.SENTINEL_KEY}".encode() in up.requests[-1])


def control(path: str, method: str = "GET") -> bytes:
    return exchange(ports["control"], f"{method} {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer "
                    f"{secrets['control_token']}\r\nContent-Length: 0\r\n\r\n".encode())


check("the control port answers /health with the control token", b'"ok": true' in control("/health"))
check("the control port refuses a request without it",
      exchange(ports["control"], b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n").startswith(b"HTTP/1.1 401"))
control("/shutdown", "POST")
try:
    rc = child.process.wait(timeout=15)
except Exception:  # noqa: BLE001
    child.kill_tree()
    rc = None
out, err = child.process.stdout.read(), child.process.stderr.read()
check("the proxy shuts down cleanly on /shutdown", rc == 0, str(rc))
rec = [json.loads(x) for x in L.spawns_log(C).read_bytes().decode().splitlines() if '"proxy"' in x][-1]
check("its environment carries no key, token or proxy variable",
      not any(n.upper().endswith(("_KEY", "_TOKEN", "_SECRET")) or n.upper() in ("HTTP_PROXY", "HTTPS_PROXY")
              for n in rec["env_names"]), str(rec["env_names"]))
spawn_bytes = L.spawns_log(C).read_bytes()
check("X6 the proxy's read exceptions are its script and the key file at their argv indexes, nothing wider",
      rec["argv_exception"] == {"1": str(ROOT / "research" / "_llm_proxy.py"), "6": str(kf)} and rec["env_exception"] == {}
      and "PYTHONPATH" not in rec["env_names"], str((rec.get("argv_exception"), rec.get("env_exception"))))
check("the tokens reached it on stdin only: absent from the spawn record",
      ST.TOKEN.encode() not in spawn_bytes and secrets["control_token"].encode() not in spawn_bytes)
check("the real process printed no key (stdout, stderr)", KEY not in out and KEY not in err, err[-200:].decode("utf-8", "replace"))
check("the files it wrote hold no key", all(KEY not in f.read_bytes() for f in pdir.rglob("*") if f.is_file()))
up.close()

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nproxy raw-forward: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
