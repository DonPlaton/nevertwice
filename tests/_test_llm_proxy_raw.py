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
import sys
import tempfile
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
