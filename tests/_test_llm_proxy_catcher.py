#!/usr/bin/env python3
"""PREREG-V3 plan step A3.a: the catcher tunnels a window's hosts through the declared hop, and nothing else.

* Outside a window every request is refused (403) and the hop is never dialled. Inside a window, only a CONNECT to
  port 443 of one of the window's exact hosts, from one of the window's arms, is tunnelled - through the hop
  (``CONNECT host:443`` on 127.0.0.1:<via>), end-to-end TLS between the child and the host; another arm, another
  port, a suffix or prefix of the host, or a plain-HTTP request is refused. With no declared hop the answer is 502.
  A hop that refuses is a 502, counted as connect_refused. The proxy never dials a window host directly.
* The catcher log records host, port, window, the hop, its answer and byte counts - never a byte of the traffic.
* /window takes exact lower-case DNS names and known arms (an IP literal, a URL, a wildcard, no arms, an unknown arm
  are refused) and records every open and close in windows_proxy.jsonl.
* The `catch` mode (the auditor's O3a) reads no key, opens only catcher ports and the control port, refuses a config
  key outside {run_dir, via, catchers}; a proxy with the key absent refuses any non-catch arm. launch.spawn_proxy with
  catcher_only=True starts it under the contract with no key file in argv - its script is its only read exception.

No network; the far end is a local TLS server with a certificate made at test time (tests/_tls_fake.py).

    python tests/_test_llm_proxy_catcher.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import ssl
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite
import _tls_fake as TF  # noqa: E402

_TEST_EXC = {sys.executable: "the test interpreter"}
if getattr(sys, "_base_executable", sys.executable) != sys.executable:
    _TEST_EXC[sys._base_executable] = "the test interpreter's base"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load("v3_llm_proxy_ct", ROOT / "research" / "_llm_proxy.py")
L = _load("v3_launch_ct", ROOT / "research" / "v3" / "launch.py")

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


TMP = Path(tempfile.mkdtemp(prefix="nvt3_catcher_"))
HOST = "huggingface.co"

#: Every outbound connect this process makes; a non-loopback one is a direct dial and is refused here.
DIALS: list[tuple] = []
_real_connect = socket.create_connection


def _guarded_connect(address, *a, **kw):
    DIALS.append(tuple(address))
    if address[0] not in ("127.0.0.1", "localhost", "::1"):
        raise OSError("direct dial refused by the test")
    return _real_connect(address, *a, **kw)


socket.create_connection = _guarded_connect


def direct_dials() -> list:
    return [d for d in DIALS if d[0] not in ("127.0.0.1", "localhost", "::1")]


made = TF.make_test_cert(TMP / "cert", HOST)
if made is None:
    print("  SKIP the tunnel checks: neither cryptography nor openssl is available (not passed)")
TRUST = ssl.create_default_context(cafile=str(made[0])) if made else None
BODY = b"hello-from-the-window-host"


def proxy(*, via_port, run: str, arms=("fetch", "other")):
    cfg = P.ProxyConfig(arms=[P.ArmConfig(arm=a, mode="catch") for a in arms], run_dir=TMP / run, via_port=via_port,
                        control_token="ctl")
    px = P.Proxy(cfg, None, log=lambda m: None)
    return px, px.start()


def ctl(px_ports, path: str, body: dict) -> bytes:
    data = json.dumps(body).encode()
    s = _real_connect(("127.0.0.1", px_ports["control"]))
    s.sendall(f"POST {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer ctl\r\nContent-Length: {len(data)}\r\n\r\n".encode()
              + data)
    s.settimeout(5)
    out = bytearray()
    try:
        while chunk := s.recv(65536):
            out += chunk
    except OSError:
        pass
    s.close()
    return bytes(out)


def via(port: int, target: str, *, tls_get: bool = False, raw: bytes | None = None) -> tuple[int, bytes]:
    """CONNECT (or ``raw``) to a catcher port; with ``tls_get`` and a 200, a TLS GET /x to the window host."""
    s = _real_connect(("127.0.0.1", port))
    s.settimeout(10)
    s.sendall(raw or f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    head = bytearray()
    try:
        while b"\r\n\r\n" not in head:
            chunk = s.recv(1)
            if not chunk:
                break
            head += chunk
    except OSError:
        pass
    parts = bytes(head).split(b" ", 2)
    status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    body = b""
    if status == 200 and tls_get:
        t = TRUST.wrap_socket(s, server_hostname=target.rsplit(":", 1)[0])
        t.sendall(b"GET /x HTTP/1.1\r\nHost: " + target.rsplit(":", 1)[0].encode() + b"\r\n\r\n")
        resp = bytearray()
        while b"\r\n\r\n" not in resp:
            resp += t.recv(65536)
        end = resp.index(b"\r\n\r\n") + 4
        length = int(next(ln.split(b":", 1)[1] for ln in resp[:end].split(b"\r\n") if ln.lower().startswith(b"content-length")))
        while len(resp) < end + length:
            resp += t.recv(65536)
        body = bytes(resp[end:end + length])
        t.close()
    else:
        s.close()
    return status, body


def log_lines(run: str) -> list[dict]:
    f = TMP / run / "catcher.jsonl"
    return [json.loads(x) for x in f.read_bytes().decode().splitlines()] if f.exists() else []


if made is not None:
    print("\n- the catcher, a window, the hop -")
    srv = TF.TlsHttpServer(made[0], made[1], {"/x": (200, [("Content-Type", "text/plain")], BODY)})
    hop = TF.TunnelHop(srv.port)
    px, ports = proxy(via_port=hop.port, run="w")
    fetch_p, other_p = ports["arms"]["fetch"]["catcher"], ports["arms"]["other"]["catcher"]
    st, _ = via(fetch_p, f"{HOST}:443")
    check("outside a window a CONNECT is refused (403) and the hop is never dialled", st == 403 and hop.connects == [],
          f"{st} {hop.connects}")
    ok_open = ctl(ports, "/window", {"name": "a3-hf", "state": "open", "hosts": [HOST], "arms": ["fetch"]})
    check("/window opens with exact hosts and known arms", ok_open.startswith(b"HTTP/1.1 200"), ok_open[:40].decode("latin-1"))
    st, body = via(fetch_p, f"{HOST}:443", tls_get=True)
    check("inside the window the arm's CONNECT to host:443 is tunnelled and TLS runs end to end to the host",
          st == 200 and body == BODY and srv.handshakes >= 1, f"{st} {body[:30]!r}")
    check("... through the hop: CONNECT host:443 on the declared hop, nothing else",
          hop.connects == [f"CONNECT {HOST}:443 HTTP/1.1".encode()], str(hop.connects))
    wait_until(lambda: log_lines("w") and log_lines("w")[-1].get("t_end"))   # the line is written when the tunnel closes
    rec = log_lines("w")[-1]
    check("the catcher log records host, port, window, hop, its answer and byte counts",
          rec["host"] == HOST and rec["port"] == 443 and rec["window"] == "a3-hf" and rec["via"] == f"127.0.0.1:{hop.port}"
          and rec["hop_status"] == 200 and rec["tunnelled"] and rec["bytes_up"] > 0 and rec["bytes_down"] > 0
          and rec.get("t_end"), str(rec))
    raw_log = (TMP / "w" / "catcher.jsonl").read_bytes()
    LOG_KEYS = {"arm", "host", "host_len", "port", "utc", "window", "via", "tunnelled", "refused", "hop_status",
                "bytes_up", "bytes_down", "t_end"}
    check("... and never a byte of the traffic: every record has exactly the named fields, nothing else",
          BODY not in raw_log and b"GET /x" not in raw_log and all(set(r) <= LOG_KEYS for r in log_lines("w")),
          str([sorted(set(r) - LOG_KEYS) for r in log_lines("w")]))
    n_connects = len(hop.connects)
    for label, port, target in (("another arm", other_p, f"{HOST}:443"), ("another port", fetch_p, f"{HOST}:8443"),
                                ("a longer name ending in the host", fetch_p, f"cdn.{HOST}:443"),
                                ("the host as a prefix", fetch_p, f"{HOST}.evil.example:443"),
                                ("an IP literal", fetch_p, "127.0.0.1:443")):
        st, _ = via(port, target)
        check(f"inside the window, {label} is refused (403)", st == 403, str(st))
    st, _ = via(fetch_p, "", raw=f"GET http://{HOST}/x HTTP/1.1\r\nHost: {HOST}\r\n\r\n".encode())
    check("a plain-HTTP request through the catcher is refused (403)", st == 403, str(st))
    check("none of the refusals reached the hop", len(hop.connects) == n_connects, str(hop.connects))
    ctl(ports, "/window", {"name": "a3-hf", "state": "close"})
    st, _ = via(fetch_p, f"{HOST}:443")
    check("after the window closes the same CONNECT is refused again", st == 403)
    wl = [json.loads(x) for x in (TMP / "w" / "windows_proxy.jsonl").read_bytes().decode().splitlines()]
    check("windows_proxy.jsonl records the open (hosts, arms) and the close",
          [w["event"] for w in wl] == ["open", "close"] and wl[0]["hosts"] == [HOST] and wl[0]["arms"] == ["fetch"], str(wl))
    for label, body in (("an IP literal host", {"hosts": ["127.0.0.1"], "arms": ["fetch"]}),
                        ("a URL as a host", {"hosts": ["https://huggingface.co"], "arms": ["fetch"]}),
                        ("a wildcard host", {"hosts": ["*.huggingface.co"], "arms": ["fetch"]}),
                        ("a host with a port", {"hosts": ["huggingface.co:443"], "arms": ["fetch"]}),
                        ("an upper-case host", {"hosts": ["HuggingFace.co"], "arms": ["fetch"]}),
                        ("no hosts", {"hosts": [], "arms": ["fetch"]}),
                        ("no arms", {"hosts": [HOST]}),
                        ("an unknown arm", {"hosts": [HOST], "arms": ["nobody"]})):
        got = ctl(ports, "/window", {"name": "bad", "state": "open", **body})
        check(f"/window refuses {label} (400)", got.startswith(b"HTTP/1.1 400"), got[:30].decode("latin-1"))
    check("the proxy never dialled a window host directly", direct_dials() == [], str(direct_dials()))
    print("\n- F-P2-6: a connection's line is written when it closes; the open count says so -")
    F = _load("v3_fetch_a3_ct", ROOT / "research" / "v3" / "fetch_a3.py")
    ctl(ports, "/window", {"name": "held", "state": "open", "hosts": [HOST], "arms": ["fetch"]})
    held = _real_connect(("127.0.0.1", fetch_p))
    held.settimeout(10)
    held.sendall(f"CONNECT {HOST}:443 HTTP/1.1\r\nHost: {HOST}:443\r\n\r\n".encode())
    got_head = b""
    while b"\r\n\r\n" not in got_head:
        got_head += held.recv(1)
    time.sleep(0.2)
    n_lines = len(log_lines("w"))
    ctr_open, still = F.drained_counters(ports["control"], "ctl", wait_s=0.3)
    check("F-P2-6: a tunnel held open by a slow peer counts as open - the drain names it, and its line is not written yet",
          got_head.startswith(b"HTTP/1.1 200") and still == {"fetch": 1} and ctr_open["fetch"]["catcher_open"] == 1
          and len(ctr_open["fetch"]["catcher_hosts"]) == sum(1 for r in log_lines("w") if r["arm"] == "fetch") + 1,
          str((still, n_lines)))
    held.close()
    ctr_done, still_done = F.drained_counters(ports["control"], "ctl", wait_s=5)
    check("F-P2-6: once the peer closes, the drain reaches 0 and the count equals the log, line for line",
          still_done is None and ctr_done["fetch"]["catcher_open"] == 0
          and len(ctr_done["fetch"]["catcher_hosts"]) == sum(1 for r in log_lines("w") if r["arm"] == "fetch")
          and len(log_lines("w")) == n_lines + 1, str((still_done, len(log_lines("w")), n_lines)))
    ctl(ports, "/window", {"name": "held", "state": "close"})
    import inspect  # noqa: E402

    ctr_src = inspect.getsource(P.Proxy._control).split('path == "/counters"', 1)[1].split("elif", 1)[0]
    check("F-P2-6: /counters is a snapshot taken under the proxy's lock (since HOP-2, in Proxy.counters_snapshot)",
          "self.counters_snapshot()" in ctr_src and "with self._lock:" in inspect.getsource(P.Proxy.counters_snapshot),
          ctr_src.strip()[:80])
    print("\n- STOP-DRAIN (CI e8e9088): a tunnel open when stop() is called leaves its line before stop() returns -")
    ctl(ports, "/window", {"name": "at-stop", "state": "open", "hosts": [HOST], "arms": ["fetch"]})
    at_stop = _real_connect(("127.0.0.1", fetch_p))
    at_stop.settimeout(10)
    at_stop.sendall(f"CONNECT {HOST}:443 HTTP/1.1\r\nHost: {HOST}:443\r\n\r\n".encode())
    head_s = b""
    while b"\r\n\r\n" not in head_s:
        head_s += at_stop.recv(1)
    wait_until(lambda: px.counters["fetch"].catcher_open == 1)
    n_before = len(log_lines("w"))
    left = px.stop()
    after = log_lines("w")
    check("STOP-DRAIN: the tunnel open at stop() has its line (host, tunnelled, t_end) when stop() returns, and stop() "
          "names no connection left open", head_s.startswith(b"HTTP/1.1 200") and left == {}
          and len(after) == n_before + 1 and after[-1]["host"] == HOST and after[-1]["tunnelled"]
          and after[-1].get("t_end") and px.counters["fetch"].catcher_open == 0, str((left, n_before, len(after))))
    at_stop.close()
    hop.close()
    silent = socket.socket()                              # a hop that accepts and never answers CONNECT
    silent.bind(("127.0.0.1", 0))
    silent.listen(4)
    px5, ports5 = proxy(via_port=silent.getsockname()[1], run="silent")
    ctl(ports5, "/window", {"name": "a3-hf", "state": "open", "hosts": [HOST], "arms": ["fetch"]})
    stuck = _real_connect(("127.0.0.1", ports5["arms"]["fetch"]["catcher"]))
    stuck.sendall(f"CONNECT {HOST}:443 HTTP/1.1\r\nHost: {HOST}:443\r\n\r\n".encode())
    wait_until(lambda: px5.counters["fetch"].catcher_open == 1)
    t_stop = time.monotonic()
    left5 = px5.stop(drain_s=0.3)
    took = time.monotonic() - t_stop
    check("STOP-DRAIN: a connection that cannot finish by the deadline is named by arm and count, and stop() returns "
          "at the deadline", left5 == {"fetch": 1} and 0.25 <= took < 3.0, f"{left5} {took:.2f}s")
    silent.close()
    stuck.close()

    print("\n- no hop, a refusing hop -")
    px2, ports2 = proxy(via_port=None, run="nohop")
    ctl(ports2, "/window", {"name": "a3-hf", "state": "open", "hosts": [HOST], "arms": ["fetch"]})
    st, _ = via(ports2["arms"]["fetch"]["catcher"], f"{HOST}:443")
    check("with no declared hop a window CONNECT is a 502, and nothing is dialled directly",
          st == 502 and log_lines("nohop")[-1]["hop_status"] == "no-hop" and direct_dials() == [], str(st))
    px2.stop()
    hop3 = TF.TunnelHop(srv.port, reply=b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
    px3, ports3 = proxy(via_port=hop3.port, run="refused")
    ctl(ports3, "/window", {"name": "a3-hf", "state": "open", "hosts": [HOST], "arms": ["fetch"]})
    st, _ = via(ports3["arms"]["fetch"]["catcher"], f"{HOST}:443")
    wait_until(lambda: px3.counters["fetch"].connect_refused >= 1 and log_lines("refused"))
    check("a hop that refuses: 502, counted as connect_refused, recorded, not retried",
          st == 502 and px3.counters["fetch"].connect_refused == 1 and log_lines("refused")[-1]["hop_status"] == "refused"
          and len(hop3.connects) == 1, f"{st} {vars(px3.counters['fetch'])}")
    px3.stop()
    hop3.close()
    srv.close()

print("\n- the catch mode reads no key -")
cfgp = TMP / "catch.json"
cfgp.write_bytes(json.dumps({"run_dir": str(TMP / "catchrun"), "via": {"host": "127.0.0.1", "port": 10809},
                             "catchers": ["fetch"]}).encode())
_read_key = P.read_key
P.read_key = lambda *a, **k: (_ for _ in ()).throw(AssertionError("the catch mode read a key"))
try:
    pxc, portsc, digest = P.start_catch(cfgp, {"control_token": "ctl"})
    check("start_catch runs without reading a key, and opens only catcher ports and the control port",
          set(portsc) == {"arms", "control"} and portsc["arms"] == {"fetch": {"catcher": portsc["arms"]["fetch"]["catcher"]}}
          and len(digest) == 64, str(portsc))
    pxc.stop()
except AssertionError as e:
    check("start_catch runs without reading a key, and opens only catcher ports and the control port", False, str(e))
finally:
    P.read_key = _read_key
for label, raw in (("an arms list", {"arms": []}), ("an upstream", {"upstream": {"host": "127.0.0.1", "port": 1}}),
                   ("a key file", {"key_file": "x"}), ("a hop with a target", {"via": {"host": "127.0.0.1", "port": 1, "target": "x:443"}}),
                   ("a hop on another host", {"via": {"host": "10.0.0.5", "port": 3128}}),
                   ("no catchers", {"catchers": []}), ("a repeated catcher", {"catchers": ["a", "a"]})):
    body = {"run_dir": str(TMP / "catchrun2"), "catchers": ["fetch"], **raw}
    cfgp.write_bytes(json.dumps(body).encode())
    try:
        P.load_catch_config(cfgp, {})
        check(f"a catch config with {label} is refused", False)
    except ValueError:
        check(f"a catch config with {label} is refused", True)
try:
    P.Proxy(P.ProxyConfig(arms=[P.ArmConfig(arm="w", mode="raw")], run_dir=TMP / "x"), None)
    check("a proxy without the key refuses any arm that is not catch-only", False)
except ValueError:
    check("a proxy without the key refuses any arm that is not catch-only", True)
for_serve = TMP / "serve.json"
for_serve.write_bytes(json.dumps({"arms": [], "run_dir": str(TMP / "s"), "catchers": ["fetch"]}).encode())
try:
    P.ProxyConfig.load(for_serve, {}, test_upstream_ok=True)
    check("a serve config refuses the catch-only key", False)
except ValueError:
    check("a serve config refuses the catch-only key", True)

print("\n- launch.spawn_proxy(catcher_only=True), a real process under the contract -")
C = L.Contract(polygon_root=TMP / "polygon", runs_root=TMP / "polygon" / "runs" / "v3", repo_root=ROOT,
               owner_home=TMP / "owner", secrets_dir=TMP / "secrets", quarantine_root=TMP / "quarantine",
               conservation_root=TMP / "conservation", binary_exceptions=_TEST_EXC,
               system_dirs=(Path(sys.executable).parent,))
pdir = C.runs_root / "_proxy" / "c1"
pdir.mkdir(parents=True)
ccfg = pdir / "catch_config.json"
ccfg.write_bytes(json.dumps({"run_dir": str(pdir), "catchers": ["fetch"]}).encode())
unit = L.make_unit_dirs(C, "_proxy", "c1", "proxy", "p1")
script = ROOT / "research" / "_llm_proxy.py"
child, cports = L.spawn_proxy(C, sys.executable, script=script, config_path=ccfg, stdin_secrets={"control_token": "ctl"},
                              unit=unit, parent_env=os.environ, catcher_only=True)
check("the catch-mode proxy reports READY with catcher ports only", set(cports["arms"]["fetch"]) == {"catcher"}, str(cports))
rec = [json.loads(x) for x in L.spawns_log(C).read_bytes().decode().splitlines()][-1]
check("its argv names no key file: its script is its only read exception, and no proxy variable reaches it",
      rec["argv_exception"] == {"1": str(script)} and not any(n.upper() in ("HTTP_PROXY", "HTTPS_PROXY") for n in rec["env_names"])
      and rec["witness"]["requirement"] == "optional" and "reads no key" in rec["witness"]["unwitnessed_reason"], str(rec))
ctl(cports, "/shutdown", {})
try:
    rc = child.process.wait(timeout=15)
except Exception:  # noqa: BLE001
    child.kill_tree()
    rc = None
check("it shuts down cleanly", rc == 0, str(rc))
for label, kw in (("catcher-only with a key file", {"catcher_only": True, "key_file": TMP / "k.env"}),
                  ("the full proxy without a key file", {"catcher_only": False})):
    u2 = L.make_unit_dirs(C, "_proxy", "c1", "proxy", f"p-{len(label)}")
    try:
        L.spawn_proxy(C, sys.executable, script=script, config_path=ccfg, stdin_secrets={}, unit=u2,
                      parent_env=os.environ, **kw)
        check(f"spawn_proxy refuses {label}", False)
    except L.ContractViolation:
        check(f"spawn_proxy refuses {label}", True)

socket.create_connection = _real_connect
print("\n- F-P2-6: concurrent appends land whole, none lost -")
import threading  # noqa: E402

RACE_THREADS, RACE_LINES = 8, 200


def race(path: Path) -> tuple[int, int, int]:
    """(lines, unparseable, distinct records) after RACE_THREADS threads append RACE_LINES records each at once."""
    go = threading.Event()

    def worker(k: int) -> None:
        go.wait()
        for i in range(RACE_LINES):
            P._append_jsonl(path, {"arm": "fetch", "host": "huggingface.co", "k": k, "i": i, "pad": "x" * 64})

    ts = [threading.Thread(target=worker, args=(k,)) for k in range(RACE_THREADS)]
    for th in ts:
        th.start()
    go.set()
    for th in ts:
        th.join()
    lines = path.read_bytes().decode("utf-8", "replace").splitlines()
    parsed, bad = [], 0
    for line in lines:
        try:
            parsed.append(json.loads(line))
        except ValueError:
            bad += 1
    return len(lines), bad, len({(r["k"], r["i"]) for r in parsed})


got = [race(TMP / f"race{n}.jsonl") for n in range(3)]
want = RACE_THREADS * RACE_LINES
check("F-P2-6: 8 threads x 200 appends at once, three times - every line whole, none lost, every record once",
      all(g == (want, 0, want) for g in got), str(got))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nproxy catcher (A3.a): {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
