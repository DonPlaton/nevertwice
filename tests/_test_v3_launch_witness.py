#!/usr/bin/env python3
"""PREREG-V3 TB9, step A2.2: the witnesses see what §2.6.9 says they see, and never read a failure as clean.

* /proc/net/tcp and tcp6 parse (IPv4 as one little-endian word, IPv6 as four); the container witness ignores a
  listener, in-container loopback and the host gateway on an allowed port, counts the gateway on another port and
  any other remote, and records a failed ``docker exec`` as a failed sample.
* The native witness tracks a tree by (pid, create time), keeps a grandchild after its parent exits, treats
  127.0.0.2 and ::ffff:127.0.0.1 as loopback, counts a non-loopback listener (AQ16), files a remote as a window host
  only while a fetch window is open, and records a sampler failure as a failed sample.
* The auditor's W7 (loopback is not neutral: a local HTTP or SOCKS proxy is a path out): a loopback connection is a
  hit unless its port's listener is in the same tree, is an allowed listener (the v3 proxy, held by pid and create
  time), or the port is a declared container gateway port; 127.0.0.1:11434 (Ollama) is no hit but is counted per
  tree as distinct (local port, 11434) pairs; an accepted connection on the tree's own listener is inbound; a
  listener that cannot be read fails the sample and is never read as clean; a window does not excuse it.
* The amended Q1 (the py-base-314 window): an allowance can be revoked and the revocation is recorded; a window's
  START names its hop (only 127.0.0.1:<port>); the hop is ONE declared value, <runs>\\_config\\network.json,
  refused in any other shape; the hop's pid is read from the LISTEN row of its port, and an ambiguous or missing
  row names no pid; py314 is in the watched idle set.
* The filesystem witness: one appended byte, a touched mtime, a new file are each a hit; changes under .git, .loop,
  research/v3/results, __pycache__ and .claude/settings.local.json are not, while another file under .claude is
  (AQ15); no entry name reaches a persisted byte (UTF-8, UTF-16-LE, JSON-escaped); the quarantine is stat-ed by
  root and known name only - an audit hook sees no listing, no open, no glob under it - and a junction or link
  into it is never followed.
* Canaries persist as hashes; the ancestor check counts CLAUDE.md by name; a check without its "after" snapshot or
  with a failed sample is incomplete; a required spawn without the native witness is refused; an optional one
  needs its reason, and the record keeps it.

The auditor's pre-gate W1-W6: on Windows a spawned tree is a Job Object (created suspended, assigned, resumed), so
an orphan stays in it and kill_tree ends it whole; a child with no readable identity is never a pid wildcard; a
window covers only its own spawns' trees; a sampler that throws or falls behind leaves the check incomplete; every
egress record carries its tick, expected samples and declared limit; the decoy secret is planted in the scheduler's
environment and never reaches a child. The launch logs are hash-chained, and a check records when it began.

The native witness runs on a fake sampler here; tests/research/_test_v3_launch_psutil.py runs it on psutil.

    python tests/_test_v3_launch_witness.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
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

_spec = importlib.util.spec_from_file_location("v3_launch_w", ROOT / "research" / "v3" / "launch.py")
L = importlib.util.module_from_spec(_spec)
sys.modules["v3_launch_w"] = L
_spec.loader.exec_module(L)

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_witness_"))
QUAR = TMP / "quarantine"

# Every listing, open or glob under the fake quarantine is caught here, from any code path.
_AUDIT: list[str] = []
_QN = os.path.normcase(os.path.abspath(QUAR))


_IN_HOOK = [False]


def _audit(event: str, args) -> None:
    """Paths are resolved (junctions and links included), so a listing THROUGH a link into the quarantine is caught
    as well as one naming it; a guard stops the resolution's own events from re-entering."""
    if _IN_HOOK[0] or event not in ("os.scandir", "os.listdir", "open", "glob.glob", "os.walk") or not args:
        return
    a = args[0]
    if not isinstance(a, (str, bytes, os.PathLike)):
        return
    _IN_HOOK[0] = True
    try:
        s = os.fsdecode(a)
        for cand in (os.path.abspath(s), os.path.realpath(s)):
            p = os.path.normcase(cand)
            if p == _QN or p.startswith(_QN + os.sep):
                _AUDIT.append(event)
                break
    except (TypeError, ValueError, OSError):
        pass
    finally:
        _IN_HOOK[0] = False


sys.addaudithook(_audit)

print("\n- /proc/net/tcp -")
TCP = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 1 1 0 100 0 0 10 0
   1: 0F02000A:C350 0202A8C0:01BB 01 00000000:00000000 00:00000000 00000000     0        0 2 1 0 20 4 30 10 -1
   2: 0F02000A:C351 FE41A8C0:B799 01 00000000:00000000 00:00000000 00000000     0        0 3 1 0 20 4 30 10 -1
   3: 0F02000A:C352 FE41A8C0:0050 02 00000000:00000000 00:00000000 00000000     0        0 4 1 0 20 4 30 10 -1
   4: 0100007F:C353 0100007F:1F90 06 00000000:00000000 00:00000000 00000000     0        0 0 1 0 20 4 30 10 -1
"""
TCP6 = """  sl  local_address                         remote_address                        st
   0: 00000000000000000000000001000000:1F90 00000000000000000000000000000000:0000 0A 0 0
   1: 00000000000000000000000001000000:C354 B80D0120000000000000000001000000:01BB 01 0 0
"""
c4 = L.parse_proc_net_tcp(TCP)
check("IPv4 listener 127.0.0.1:8080 LISTEN", (c4[0].local_ip, c4[0].local_port, c4[0].state) == ("127.0.0.1", 8080, "LISTEN"), str(c4[0]))
check("IPv4 remote 192.168.2.2:443 ESTABLISHED, local 10.0.2.15:50000",
      (c4[1].remote_ip, c4[1].remote_port, c4[1].state, c4[1].local_ip) == ("192.168.2.2", 443, "ESTABLISHED", "10.0.2.15"), str(c4[1]))
check("states SYN_SENT and TIME_WAIT", c4[3].state == "SYN_SENT" and c4[4].state == "TIME_WAIT")
c6 = L.parse_proc_net_tcp(TCP6)
check("IPv6 ::1 listener and remote 2001:db8::1:443 (four little-endian words)",
      c6[0].local_ip == "::1" and (c6[1].remote_ip, c6[1].remote_port) == ("2001:db8::1", 443), str(c6))

print("\n- the container witness -")
GW = "192.168.65.254"


def docker_ok(text):
    return lambda argv: (0, text)


cw = L.ContainerEgressWitness("letta", gateway_ip=GW, allowed_ports=[47001], run_docker=docker_ok(TCP + TCP6))
cw.sample()
check("listener, loopback and the gateway on an allowed port are not hits; the gateway elsewhere and a remote are",
      cw.result.hits == 3 and cw.result.hit_remotes == {"192.168.2.2:443", f"{GW}:80", "2001:db8::1:443"},
      str(cw.result.hit_remotes))
bad = L.ContainerEgressWitness("letta", gateway_ip=GW, allowed_ports=[47001], run_docker=lambda argv: (1, ""))
bad.sample()
check("a failed docker exec is a failed sample, and the check is incomplete",
      bad.result.failed_samples == 1 and not bad.result.complete and bad.result.hits == 0)

print("\n- the native witness (fake sampler) -")


class FakeSampler:
    def __init__(self):
        self.procs = [(100, 1, 10.0), (101, 100, 11.0), (102, 101, 12.0), (200, 999, 5.0)]
        self.rows = []
        self.listen: dict | None = {}                    # W7: {(proto, port): {pid}} for this tick
        self.fail = False

    def listeners(self):
        return self.listen

    def processes(self):
        if self.fail:
            raise OSError("sampler failed")
        return list(self.procs)

    def identity(self, pid):
        return next((ct for p, _pp, ct in self.procs if p == pid), None)

    def connections(self, pids):
        return [r for r in self.rows if r[0] in pids], 0


fs_ = FakeSampler()
nw = L.NativeEgressWitness(sampler=fs_, tick_s=60, jobs=None)
nw.register(100)
nw.sample()
check("the tree is tracked by parent identity: child and grandchild, not the unrelated 200",
      set(nw.tracked) == {100, 101, 102}, str(sorted(nw.tracked)))
fs_.procs = [(100, 1, 10.0), (102, 101, 12.0), (200, 999, 5.0)]         # 101 exited; its child 102 lives on
fs_.rows = [(102, "10.0.0.5", 5000, "93.184.216.34", 443, "ESTABLISHED"),
            (100, "127.0.0.1", 6000, "127.0.0.2", 80, "ESTABLISHED"),
            (102, "::ffff:127.0.0.1", 6001, "::ffff:127.0.0.1", 81, "ESTABLISHED"),
            (100, "0.0.0.0", 7000, None, None, "LISTEN"),
            (100, "127.0.0.1", 7001, None, None, "LISTEN"),
            (200, "10.0.0.5", 5001, "8.8.8.8", 53, "ESTABLISHED")]
fs_.listen = {("tcp", 80): {102}, ("tcp", 81): {100}, ("tcp", 7000): {100}, ("tcp", 7001): {100}}   # in-tree servers
nw.sample()
check("a grandchild stays tracked after its parent exits, and its remote is a hit",
      "93.184.216.34:443" in nw.result.hit_remotes, str(nw.result.hit_remotes))
check("127.0.0.2 and ::ffff:127.0.0.1 are loopback, not hits", not any(r.startswith(("127.", "::ffff")) for r in nw.result.hit_remotes))
check("a listener on 0.0.0.0 is a hit (AQ16), one on 127.0.0.1 is not",
      nw.result.listen_nonloopback == 1 and nw.result.hits == 2, f"{nw.result.listen_nonloopback} / {nw.result.hits}")
check("an unrelated process's connection is not counted", "8.8.8.8:53" not in nw.result.hit_remotes)
nw.open_window("fetch-hf", {100})
fs_.rows = [(102, "10.0.0.5", 5002, "13.35.1.1", 443, "ESTABLISHED")]
nw.sample()
check("inside a window covering this tree a remote is a window host, not a hit", "13.35.1.1:443" in nw.result.window_hosts and nw.result.hits == 2)
nw.close_window("fetch-hf")
nw.sample()
check("after the window closes the same remote is a hit", "13.35.1.1:443" in nw.result.hit_remotes and nw.result.hits == 3)
fs_.fail = True
nw.sample()
check("a failed sample is recorded as failed, and the check is incomplete", nw.result.failed_samples == 1 and not nw.result.complete)

print("\n- W7: a loopback destination is judged by its listener -")
PROXY_PID, XRAY_PID, OTHER_ROOT = 300, 400, 500


def w7(rows, listen, *, allow=True, ports=(), window=False, other_tree=False):
    s = FakeSampler()
    s.procs = [(100, 1, 10.0), (101, 100, 11.0), (PROXY_PID, 1, 30.0), (XRAY_PID, 1, 40.0), (OTHER_ROOT, 1, 50.0)]
    s.rows, s.listen = rows, listen
    w = L.NativeEgressWitness(sampler=s, tick_s=60, jobs=None)
    w.register(100, label="spawn-a")
    if other_tree:
        w.register(OTHER_ROOT, label="spawn-b")
    if allow:
        check("W7 the proxy is allowed by its identity", w.allow_listener(PROXY_PID, "the v3 proxy"))
    for port in ports:
        w.allow_port(port)
    if window:
        w.open_window("hf", {100})
    w.sample()
    return w.result


XRAY = {("tcp", 10809): {XRAY_PID}, ("tcp", 10808): {XRAY_PID}}
r = w7([(101, "127.0.0.1", 6100, "127.0.0.1", 10809, "ESTABLISHED")], XRAY)
check("W7 a tree's connection to a listener outside it (a local HTTP proxy) is a hit",
      r.hits == 1 and r.loopback_hits == 1 and "127.0.0.1:10809" in r.hit_remotes and r.failed_samples == 0,
      str(r.as_record()))
r = w7([(101, "127.0.0.1", 6101, "127.0.0.1", 10808, "ESTABLISHED")], XRAY, window=True)
check("W7 ... the SOCKS port too, and an open window for the tree does not excuse it",
      r.loopback_hits == 1 and not r.window_hosts)
r = w7([(101, "127.0.0.1", 6102, "127.0.0.1", 47001, "ESTABLISHED")], {("tcp", 47001): {PROXY_PID}})
check("W7 the proxy's port is no hit", r.hits == 0 and r.failed_samples == 0 and r.loopback_hits == 0, str(r.as_record()))
r = w7([(101, "127.0.0.1", 6102, "127.0.0.1", 47001, "ESTABLISHED")], {("tcp", 47001): {PROXY_PID}}, allow=False)
check("W7 ... but without allow_listener the same port is a hit", r.loopback_hits == 1)
s_reuse = FakeSampler()
s_reuse.procs = [(100, 1, 10.0), (PROXY_PID, 1, 30.0)]
w_reuse = L.NativeEgressWitness(sampler=s_reuse, tick_s=60, jobs=None)
w_reuse.register(100)
w_reuse.allow_listener(PROXY_PID, "the v3 proxy")
s_reuse.procs = [(100, 1, 10.0), (PROXY_PID, 1, 99.0)]                   # the proxy died; its pid was reused
s_reuse.rows, s_reuse.listen = [(100, "127.0.0.1", 6103, "127.0.0.1", 47001, "ESTABLISHED")], {("tcp", 47001): {PROXY_PID}}
w_reuse.sample()
check("W7 an allowed pid reused by another process is not allowed", w_reuse.result.loopback_hits == 1)
r = w7([(101, "127.0.0.1", 6104, "127.0.0.1", 8000, "ESTABLISHED"), (100, "127.0.0.1", 8000, "127.0.0.1", 6104,
        "ESTABLISHED"), (100, "127.0.0.1", 8000, None, None, "LISTEN")], {("tcp", 8000): {100}})
check("W7 an in-tree listener is no hit, and its accepted side is inbound, not undetermined",
      r.hits == 0 and r.failed_samples == 0 and r.undetermined_loopback == 0, str(r.as_record()))
r = w7([(101, "127.0.0.1", 6105, "127.0.0.1", 8001, "ESTABLISHED")], {("tcp", 8001): {OTHER_ROOT}}, other_tree=True)
check("W7 a listener in another witnessed tree (another arm) is a hit", r.loopback_hits == 1)
r = w7([(101, "127.0.0.1", 6106, "127.0.0.1", 11434, "ESTABLISHED"),
        (101, "127.0.0.1", 6107, "127.0.0.1", 11434, "ESTABLISHED")], {("tcp", 11434): {999}})
check("W7 Ollama's 11434 is no hit, counted per tree as distinct (local port, 11434) pairs",
      r.hits == 0 and r.as_record()["ollama_direct_conns"] == {"spawn-a": 2}, str(r.as_record()["ollama_direct_conns"]))
s_ol = FakeSampler()
s_ol.procs = [(100, 1, 10.0)]
w_ol = L.NativeEgressWitness(sampler=s_ol, tick_s=60, jobs=None)
w_ol.register(100, label="spawn-a")
s_ol.rows, s_ol.listen = [(100, "127.0.0.1", 6108, "127.0.0.1", 11434, "ESTABLISHED")], {("tcp", 11434): {999}}
w_ol.sample()
w_ol.sample()
check("W7 ... the same connection seen in two ticks counts once", w_ol.result.as_record()["ollama_direct_conns"] == {"spawn-a": 1})
r = w7([(101, "127.0.0.1", 6109, "127.0.0.1", 47005, "ESTABLISHED")], {("tcp", 47005): {12345}}, ports=(47005,))
check("W7 a declared container gateway port is no hit", r.hits == 0 and r.as_record()["allowed_ports"] == [47005])
r = w7([(101, "127.0.0.1", 6110, "127.0.0.1", 9999, "ESTABLISHED")], {})
check("W7 a loopback port with no readable listener fails the sample: incomplete, never clean",
      r.hits == 0 and r.undetermined_loopback == 1 and r.failed_samples == 1 and not r.complete, str(r.as_record()))
r = w7([(101, "127.0.0.1", 6111, "127.0.0.1", 9998, "ESTABLISHED")], {("tcp", 9998): {None}})
check("W7 a listener whose pid psutil could not read fails the sample", r.failed_samples == 1 and r.loopback_hits == 0)
r = w7([(101, "127.0.0.1", 6112, "127.0.0.1", 10809, "ESTABLISHED")], None)
check("W7 a sampler that gives no listener table fails the sample", r.failed_samples == 1 and r.loopback_hits == 0)
r = w7([(101, "127.0.0.1", 6113, "127.0.0.1", 10808, "NONE")], {("tcp", 10808): {101}, ("udp", 10808): {XRAY_PID}})
check("W7 UDP is judged by the UDP owner of the port, not a TCP listener on the same number", r.loopback_hits == 1)
check("W7 the record names loopback_hits, undetermined_loopback, ollama_direct_conns and the allowed listeners",
      {"loopback_hits", "undetermined_loopback", "ollama_direct_conns", "allowed_listeners", "allowed_ports"}
      <= set(r.as_record()) and r.as_record()["allowed_listeners"] == [{"reason": "the v3 proxy", "pid": PROXY_PID}])
check("W7 the listener map keeps TCP LISTEN and unconnected UDP on loopback or unspecified addresses only",
      L.listener_map([(1, "127.0.0.1", 80, False, "LISTEN"), (2, "0.0.0.0", 81, False, "LISTEN"),
                      (3, "10.0.0.5", 82, False, "LISTEN"), (4, "::", 83, False, "NONE"),
                      (5, "127.0.0.1", 84, True, "NONE"), (6, "127.0.0.1", 85, True, "ESTABLISHED")])
      == {("tcp", 80): {1}, ("tcp", 81): {2}, ("udp", 83): {4}})

print("\n- C4 (Q-A8S-LISTEN = a, SF3): a declared listener - by identity, port, unspecified address, firewall -")
SM_PID, SM_KID, SM_PORT = 600, 601, 6543
FW = {"name": "nvt3-supermemory-inbound-block", "enabled": True, "direction": "Inbound", "action": "Block",
      "profile_covered": True, "program_matches": True}
SM_REASON = "supermemory-local binds 0.0.0.0 (sm_bundle r8)"


def decl(rows, *, firewall=FW, kid_ct=61.0):
    """(the result after one tick, what declare_listener answered) for a tree rooted at SM_PID that declared
    (SM_PID, SM_PORT); an exception is the row's named FAIL (None, repr), never a traceback."""
    try:
        s = FakeSampler()
        s.procs = [(SM_PID, 1, 60.0), (SM_KID, SM_PID, kid_ct)]
        w = L.NativeEgressWitness(sampler=s, tick_s=60, jobs=None)
        w.register(SM_PID, label="spawn-sm")
        said = w.declare_listener(SM_PID, SM_PORT, SM_REASON, firewall=firewall)
        s.rows, s.listen = rows, {}
        w.sample()
        return w.result, said
    except Exception as e:  # noqa: BLE001
        return None, repr(e)


def _listen(pid, host, port=SM_PORT):
    return [(pid, host, port, None, None, "LISTEN")]


def _hit(r) -> bool:
    return r is not None and r.hits == 1 and r.listen_nonloopback == 1 and r.listen_declared == 0


r4, said4 = decl(_listen(SM_PID, "0.0.0.0"))
r6, said6 = decl(_listen(SM_PID, "::"))
rec4 = r4.as_record() if r4 is not None else {}
check("LW-DECL-1: a declared listener - its pid by identity, its port, on 0.0.0.0 or :: - is listen_declared, not a "
      "hit, and the record names it with its reason and firewall rule",
      said4 is True and said6 is True and r4.hits == 0 and r4.listen_nonloopback == 0 and r4.listen_declared == 1
      and r6.hits == 0 and r6.listen_declared == 1 and rec4.get("listen_declared") == 1
      and rec4.get("declared_listeners") == [{"reason": SM_REASON, "pid": SM_PID, "port": SM_PORT,
                                              "firewall_rule": FW["name"]}] and rec4.get("refused_declarations") == [],
      str(rec4 or said4))
r_kid, _ = decl(_listen(SM_KID, "0.0.0.0"), kid_ct=60.0)
check("LW-DECL-2: another process on the declared port - a child born in the same tick as the server - is a hit: "
      "the declaration is the pid's and the port's together", _hit(r_kid), str(r_kid.as_record() if r_kid else r_kid))
r_port, _ = decl(_listen(SM_PID, "0.0.0.0", SM_PORT + 1))
check("LW-DECL-3: the declared process on another port is a hit", _hit(r_port),
      str(r_port.as_record() if r_port else r_port))


def _reused():
    """The result after one tick: pid 650 declared while another process (create time 50) held it, then reused by
    a child of the tree (create time 99) that listens on the declared port. A pid reused inside the tree keeps its
    stale entry and is never alive again, so this is the one path the identity clause decides."""
    try:
        s = FakeSampler()
        s.procs = [(SM_PID, 1, 60.0), (SM_KID, SM_PID, 61.0), (650, 1, 50.0)]
        w = L.NativeEgressWitness(sampler=s, tick_s=60, jobs=None)
        w.register(SM_PID, label="spawn-sm")
        w.declare_listener(650, SM_PORT, SM_REASON, firewall=FW)
        s.procs = [(SM_PID, 1, 60.0), (SM_KID, SM_PID, 61.0), (650, SM_PID, 99.0)]
        s.rows, s.listen = _listen(650, "0.0.0.0"), {}
        w.sample()
        return w.result
    except Exception:  # noqa: BLE001 - the row FAILs by name
        return None


r_re = _reused()


def _no_identity():
    """(what declare_listener answered for a pid with no identity, what was declared, what was refused)."""
    try:
        w = L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=None)
        said = w.declare_listener(777, SM_PORT, SM_REASON, firewall=FW)
        return said, dict(w.declared), list(w.result.refused_declarations)
    except Exception as e:  # noqa: BLE001 - the row FAILs by name
        return repr(e), None, None


said_id, decl_id, ref_id = _no_identity()
check("LW-DECL-4: a reused pid (another create time) on the declared port is a hit; a pid with no identity is "
      "refused, named, and declares nothing", _hit(r_re) and said_id is False and decl_id == {}
      and ref_id == [{"reason": SM_REASON, "pid": 777, "port": SM_PORT, "why": "no identity"}],
      f"{r_re.as_record() if r_re else r_re} {said_id} {decl_id} {ref_id}")
r_if, _ = decl(_listen(SM_PID, "10.0.0.5"))
check("LW-DECL-5: the declared process and port on one named interface (10.0.0.5) is a hit - only 0.0.0.0 and :: "
      "are declared", _hit(r_if), str(r_if.as_record() if r_if else r_if))
NO_GROUND = {"no rule": None, "disabled": {**FW, "enabled": False}, "outbound": {**FW, "direction": "Outbound"},
             "allow": {**FW, "action": "Allow"}, "profile not covered": {**FW, "profile_covered": False},
             "unnamed": {**FW, "name": ""}, "another program": {**FW, "program_matches": False}}
fw_got = {k: decl(_listen(SM_PID, "0.0.0.0"), firewall=v) for k, v in NO_GROUND.items()}
fw_view = {k: (said, r.hits if r else None, [x.get("why") for x in r.refused_declarations] if r else None)
           for k, (r, said) in fw_got.items()}
check("LW-DECL-6: with no inbound-blocking firewall rule - none, disabled, outbound, allow, the active profile not "
      "covered, unnamed, another program - a declaration is refused, named, and the listener stays a hit (SF3)",
      all(said is False and _hit(r) and [x.get("why") for x in r.refused_declarations]
          == ["no inbound-blocking firewall rule"] and r.declared_listeners == [] for r, said in fw_got.values()),
      str(fw_view))

print("\n- B-PID-REUSE: a tracked pid with another create time is a new process - adopted again or dropped -")
EGRESS = [(0, "10.0.0.5", 50000, "93.184.216.34", 443, "ESTABLISHED")]      # pid 0 is replaced per row


class FakeJobs:
    """W1's job object for one tick: every root's members are what the row says; identity is the sampler's."""

    def __init__(self, sampler: FakeSampler, members: dict):
        self.s, self.jobs, self._members = sampler, {}, members

    def adopt(self, pid, handle=None):
        self.jobs[pid] = object()
        return self.s.identity(pid)

    def identity(self, pid):
        return self.s.identity(pid)

    def members(self, root):
        return list(self._members.get(root, []))


def reuse_tick(first, second, rows, *, members=None, declare=False):
    """(the witness, its result) after two ticks of a tree rooted at SM_PID: ``first`` then ``second`` as the process
    list, ``rows`` read in the second; ``members`` - the job's members (W1) or no job; ``declare`` - SM_PID declared
    its listener on SM_PORT before the first tick. An exception is the row's named FAIL (None, repr)."""
    try:
        s = FakeSampler()
        s.procs = list(first)
        jobs = FakeJobs(s, {SM_PID: members}) if members is not None else None
        w = L.NativeEgressWitness(sampler=s, tick_s=60, jobs=jobs)
        w.register(SM_PID, label="spawn-sm")
        if declare:
            w.declare_listener(SM_PID, SM_PORT, SM_REASON, firewall=FW)
        s.rows, s.listen = [], {}
        w.sample()
        s.procs, s.rows = list(second), rows
        w.sample()
        return w, w.result
    except Exception as e:  # noqa: BLE001 - the row FAILs by name
        return None, repr(e)


def _egress(pid):
    return [(pid, *EGRESS[0][1:])]


def _read(r) -> bool:
    return not isinstance(r, str) and r is not None and r.hits == 1 and r.hit_remotes == {"93.184.216.34:443"}


_w1, r_pp = reuse_tick([(SM_PID, 1, 60.0), (SM_KID, SM_PID, 61.0)], [(SM_PID, 1, 60.0), (SM_KID, SM_PID, 99.0)],
                       _egress(SM_KID))
check("PR-1: a child's pid reused by a new child of the same live member (the ppid branch) is adopted again - its "
      "connection is read, a hit", _read(r_pp), str(r_pp.as_record() if not isinstance(r_pp, str) else r_pp))
JOB_KID = 700
_w2, r_job = reuse_tick([(SM_PID, 1, 60.0), (JOB_KID, 1, 70.0)], [(SM_PID, 1, 60.0), (JOB_KID, 1, 99.0)],
                        _egress(JOB_KID), members=[SM_PID, JOB_KID])
check("PR-2: an orphan's pid reused by a new member of the same job (the job branch) is adopted again - its connection "
      "is read, a hit", _read(r_job), str(r_job.as_record() if not isinstance(r_job, str) else r_job))
_w3, r_sm = reuse_tick([(SM_PID, 1, 60.0), (SM_KID, SM_PID, 61.0)], [(SM_PID, SM_KID, 99.0), (SM_KID, SM_PID, 61.0)],
                       _listen(SM_PID, "0.0.0.0"), declare=True)
check("PR-3: the declared server's pid reused inside the tree (a child of its own child) listening on the declared "
      "port is read and is a hit - the declaration was the old process's (LW-DECL-4's first scenario)",
      not isinstance(r_sm, str) and r_sm is not None and r_sm.hits == 1 and r_sm.listen_nonloopback == 1
      and r_sm.listen_declared == 0, str(r_sm.as_record() if not isinstance(r_sm, str) else r_sm))
ok4 = {}
for tag, second in (("a stranger's child", [(SM_PID, 1, 60.0), (SM_KID, 4242, 99.0)]),
                    ("older than its parent", [(SM_PID, 1, 60.0), (SM_KID, SM_PID, 55.0)])):
    w4_, r4_ = reuse_tick([(SM_PID, 1, 60.0), (SM_KID, SM_PID, 61.0)], second, _egress(SM_KID))
    ok4[tag] = (w4_ is not None and r4_.hits == 0 and SM_KID not in w4_.tracked and SM_KID not in w4_.root_of
                and SM_PID in w4_.tracked)
check("PR-4: a tracked pid reused outside the tree - a stranger's child, or a 'child' older than its parent - is not "
      "adopted: its connection is not read and the stale entry is dropped from tracked and root_of",
      all(ok4.values()) and len(ok4) == 2, str(ok4))


print("\n- PID reuse between two registered job trees -")
try:
    s_multi = FakeSampler()
    s_multi.procs = [(600, 1, 60.0), (800, 1, 80.0), (801, 800, 81.0)]
    jobs_multi = FakeJobs(s_multi, {600: [600], 800: [800, 801]})
    w_multi = L.NativeEgressWitness(sampler=s_multi, tick_s=60, jobs=jobs_multi)
    w_multi.register(600, label="old-job-a")
    w_multi.register(800, label="job-b")
    w_multi.sample()
    s_multi.procs = [(600, 801, 99.0), (800, 1, 80.0), (801, 800, 81.0)]
    jobs_multi._members = {600: [], 800: [800, 801, 600]}
    w_multi.sample()
    s_multi.procs.append((602, 1, 100.0))
    jobs_multi._members[600] = [602]
    w_multi.open_window("only-b", {800})
    s_multi.rows = _egress(602)
    w_multi.sample()
    check("PR-5: an old job's new orphan keeps that job's identity after its root PID becomes another tree's child; it cannot borrow the other window",
          w_multi.root_of.get(600) == 800 and w_multi.root_of.get(602) == 600
          and w_multi.result.hits == 1 and w_multi.result.window_hosts == set(), str(w_multi.result.as_record()))
    old_job = jobs_multi.jobs[600]
    old_label = w_multi.label_of[600]
    before_unseen = w_multi.result.unwitnessed_registrations
    admitted = w_multi.register(600, label="replacement-root")
    check("PR-6: a new root reusing a registered PID is refused before the old job/label can be overwritten",
          admitted is False and jobs_multi.jobs[600] is old_job and w_multi.label_of[600] == old_label
          and w_multi.result.unwitnessed_registrations == before_unseen + 1, str(admitted))
except Exception as e:  # noqa: BLE001 - named failure, never a suite crash
    check("PR-5/6: the multi-tree identity seam runs", False, repr(e))

print("\n- the filesystem witness -")
REPO = TMP / "repo"
for d in (".git", ".loop", "research/v3/results", "pkg/__pycache__", ".claude/agents", "src", "src/SENTINEL_DIR_QXZ"):
    (REPO / d).mkdir(parents=True)
for f in (".git/HEAD", ".loop/STATE.md", "research/v3/results/r.json", "pkg/__pycache__/m.pyc",
          ".claude/settings.local.json", ".claude/agents/a.md", "src/SENTINEL_NAME_ZXQ.txt", "src/b.txt",
          "src/SENTINEL_DIR_QXZ/c.txt"):
    (REPO / f).write_bytes(b"x")
QUAR.mkdir()
(QUAR / "known_top").mkdir()
(QUAR / "known_top" / "inner_owner_file.txt").write_bytes(b"secret-ish")
C = L.Contract(polygon_root=TMP / "polygon", runs_root=TMP / "polygon" / "runs" / "v3", repo_root=REPO,
               owner_home=TMP / "owner", secrets_dir=TMP / "secrets", quarantine_root=QUAR,
               conservation_root=TMP / "conservation", quarantine_known=("known_top",), conservation_known=("2026-09-25",))
specs = [s for s in L.watched_set(C) if s.label in ("repo", "quarantine", "conservation")]


class LoggingFs(L.OsFs):
    def __init__(self):
        self.calls = []

    def scandir(self, path):
        self.calls.append(("scandir", path))
        return super().scandir(path)

    def stat(self, path):
        self.calls.append(("stat", path))
        return super().stat(path)


lfs = LoggingFs()
W = L.FsWitness(specs, fs=lfs)
base = W.snapshot()


def hits_after(mutate) -> int:
    before = W.snapshot()
    mutate()
    return L.FsWitness.diff(before, W.snapshot())["fs_hits"]


check("an unchanged tree gives 0 hits", L.FsWitness.diff(base, W.snapshot())["fs_hits"] == 0)
check("one appended byte is a hit", hits_after(lambda: open(REPO / "src" / "b.txt", "ab").write(b"y")) >= 1)
check("an mtime-only touch is a hit", hits_after(lambda: os.utime(REPO / "src" / "b.txt", ns=(1_000_000_000, 1_000_000_000))) >= 1)
check("a new file is a hit", hits_after(lambda: (REPO / "src" / "new.txt").write_bytes(b"n")) >= 1)
for rel in (".git/HEAD", ".loop/STATE.md", "research/v3/results/r.json", "pkg/__pycache__/m.pyc", ".claude/settings.local.json"):
    check(f"a change under {rel} is not a hit", hits_after(lambda rel=rel: open(REPO / rel, "ab").write(b"z")) == 0)
check("a change to another file under .claude is a hit (AQ15: only settings.local.json is excluded)",
      hits_after(lambda: open(REPO / ".claude" / "agents" / "a.md", "ab").write(b"z")) >= 1)
check("a change inside the quarantine's known entry is seen by its stat, never by a listing",
      hits_after(lambda: (QUAR / "known_top" / "later.txt").write_bytes(b"q")) >= 1)
q_calls = [(k, p) for k, p in lfs.calls if L._inside(p, QUAR)]
check("the quarantine: stat of its root and known names only, never a scandir",
      q_calls and all(k == "stat" for k, _ in q_calls)
      and {L._norm(p) for _, p in q_calls} <= {L._norm(QUAR), L._norm(QUAR / "known_top")}, str(q_calls[:4]))


class FlakyFs(L.OsFs):
    """A stat that fails once for one path (a transient OSError), then works - the witness never retries it."""

    def __init__(self, path):
        self.path, self.failed, self.calls = L._norm(path), False, []

    def stat(self, path):
        self.calls.append(L._norm(path))
        if L._norm(path) == self.path and not self.failed:
            self.failed = True
            raise OSError("transient")
        return super().stat(path)


def one_absent_row(rc: dict) -> bool:
    """Row 1 (the first known name) absent at begin and there at end; its kind and mtime changed (a directory's size
    is 0 on Windows and not on Linux, so size may or may not be among the fields)."""
    ch = rc.get("quarantine") or [{}]
    return (set(rc) == {"quarantine"} and len(ch) == 1 and ch[0].get("row") == 1 and ch[0].get("begin") == "absent"
            and ch[0].get("end") == "e" and {"kind", "mtime"} <= set(ch[0].get("changed", ()))
            <= {"kind", "size", "mtime"})


ffs = FlakyFs(QUAR / "known_top")
FW = L.FsWitness(specs, fs=ffs)
fb = FW.snapshot()
fb_rows = dict(FW.last_rows)
fa = FW.snapshot()
rc_absent = L.FsWitness.row_changes(fb_rows, FW.last_rows)
check("R-WIT-DIAG: a stat that failed at begin shows as row 1 (the first known name): absent at begin, there at end - "
      "the label changed, and the stat was not retried", L.FsWitness.diff(fb, fa)["changed_labels"] == ["quarantine"]
      and one_absent_row(rc_absent) and ffs.calls.count(L._norm(QUAR / "known_top")) == 2,
      f"{rc_absent} {ffs.calls.count(L._norm(QUAR / 'known_top'))}")
mb = FW.snapshot()
mb_rows = dict(FW.last_rows)
os.utime(QUAR / "known_top", ns=(2_000_000_000, 2_000_000_000))
FW.snapshot()
rc_mtime = L.FsWitness.row_changes(mb_rows, FW.last_rows)
check("R-WIT-DIAG: a changed mtime shows as row 1, there at begin and end, only its mtime changed",
      rc_mtime == {"quarantine": [{"row": 1, "begin": "e", "end": "e", "changed": ["mtime"]}]}, str(rc_mtime))
check("R-WIT-DIAG: an unchanged label has no row change", L.FsWitness.row_changes(FW.last_rows, FW.last_rows) == {})
check("R-WIT-DIAG: what a row change says carries no name, size or time",
      "known_top" not in json.dumps(rc_absent) + json.dumps(rc_mtime)
      and "2000000000" not in json.dumps(rc_mtime), json.dumps(rc_mtime))
persisted = json.dumps(L.FsWitness.persistable(W.snapshot())).encode("utf-8")
for name in ("SENTINEL_NAME_ZXQ", "SENTINEL_DIR_QXZ", "src"):
    check(f"no entry name in the persisted digests: {name} (UTF-8, UTF-16-LE, JSON-escaped)",
          name.encode() not in persisted and name.encode("utf-16-le") not in persisted
          and json.dumps(name).encode() not in persisted, persisted[:120].decode())

link = REPO / "src" / "to_quarantine"
made = False
try:
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(QUAR), str(link))
    else:
        os.symlink(QUAR, link, target_is_directory=True)
    made = True
except (OSError, ImportError, AttributeError) as e:
    print(f"       (no junction or link could be made here: {type(e).__name__}; the follow check is skipped)")
if made:
    _AUDIT.clear()
    snap = W.snapshot()
    check("a junction or link into the quarantine is never followed (no listing under it)", _AUDIT == [], str(_AUDIT[:4]))
    check("... and nothing behind it is in the snapshot", not any(k.startswith("src/to_quarantine") for k in snap["repo"]),
          str([k for k in snap["repo"] if "to_quarantine" in k]))
_AUDIT.clear()
W.snapshot()
check("the audit hook saw no listing, open or glob under the quarantine during a snapshot", _AUDIT == [], str(_AUDIT[:4]))

print("\n- canaries, the ancestor check, checks -")
cn = L.Canaries.generate()
check("five distinct canaries, one per decoy (decoy_claude_json added by R-CC-WIT)",
      len(set(cn.values.values())) == 5 and set(cn.values) == {"decoy_env", "decoy_claude_md", "decoy_credentials",
                                                                "decoy_claude_json", "ancestor"}, str(sorted(cn.values)))
before = L.check_ancestors_for_claude(C)
(TMP / "CLAUDE.md").write_bytes(b"decoy")
after = L.check_ancestors_for_claude(C)
check("the ancestor check counts a CLAUDE.md by name", after["found"] == before["found"] + 1, f"{before} -> {after}")
fs2 = FakeSampler()
nat = L.NativeEgressWitness(sampler=fs2, tick_s=60, jobs=None)
Wn = L.Witnesses(C, native=nat, fs=L.FsWitness(specs), canaries=cn)
Wn.begin_check("chk-1")
(REPO / "src" / "during.txt").write_bytes(b"d")
rec = Wn.end_check("chk-1")
check("a change during a check is counted", rec["fs"]["fs_hits"] >= 1 and rec["complete"], str(rec["fs"].get("fs_hits")))
raw = (C.runs_root / "_witness" / "chk-1.json").read_bytes()
check("canaries are persisted as hashes, never as values",
      all(v.encode() not in raw for v in cn.values.values()) and all(h.encode() in raw for h in cn.hashes().values()))
check("no entry name in the check record", all(n not in raw for n in (b"SENTINEL_NAME_ZXQ", b"SENTINEL_DIR_QXZ", b"during.txt")))
fs2.fail = True
Wf = L.Witnesses(C, native=L.NativeEgressWitness(sampler=fs2, tick_s=60, jobs=None), fs=L.FsWitness(specs))
Wf.begin_check("chk-2")
recf = Wf.end_check("chk-2")
check("a check whose egress sample failed is incomplete, not zero", recf["complete"] is False)
W0 = L.Witnesses(C, native=L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=None), fs=None)
W0.begin_check("chk-3")
check("a check with no filesystem witness is incomplete", W0.end_check("chk-3")["complete"] is False)
Wd = L.Witnesses(C, native=L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=None),
                 fs=L.FsWitness(specs, fs=FlakyFs(QUAR / "known_top")))
Wd.begin_check("chk-diag")
recd = Wd.end_check("chk-diag")
rawd = (C.runs_root / "_witness" / "chk-diag.json").read_bytes()
fsd = recd.get("fs", {})
check("R-WIT-DIAG: the check record tells 'could not be read at begin' from 'written' - row 1 absent at begin and "
      "there at end, the quarantine's begin and end digests differ while the repo's agree, and no name is written",
      one_absent_row(fsd.get("row_changes") or {}) and fsd.get("changed_labels") == ["quarantine"]
      and (fsd.get("begin_labels") or {}).get("quarantine") != (fsd.get("labels") or {}).get("quarantine")
      and (fsd.get("begin_labels") or {}).get("repo") == (fsd.get("labels") or {}).get("repo")
      and b"known_top" not in rawd, json.dumps({k: fsd.get(k) for k in ("row_changes", "changed_labels")}))

print("\n- spawn and the witness requirement -")
C2 = L.Contract(polygon_root=TMP / "polygon", runs_root=TMP / "polygon" / "runs" / "v3", repo_root=REPO,
                owner_home=TMP / "owner", secrets_dir=TMP / "secrets", quarantine_root=QUAR,
                conservation_root=TMP / "conservation", binary_exceptions=_TEST_EXC,
                system_dirs=(Path(sys.executable).parent,))
CATCHER = "http://127.0.0.1:47002"


class _NoPopen:
    pid = 102

    def __init__(self, argv, **kw):
        pass


def try_spawn(unit: str, **kw):
    u = L.make_unit_dirs(C2, "s", "r", "a", unit)
    env = L.build_env(C2, parent_env=os.environ, unit=u, path_dirs=[], declared={}, catcher_url=CATCHER)
    return L.spawn(C2, [sys.executable, "-c", "pass"], env=env, cwd=u.cwd, record={"role": "t"}, parent_env=os.environ,
                   catcher_url=CATCHER, popen=_NoPopen, **kw)


for label, kw in (("a required spawn without the native witness", {}),
                  ("a required spawn without the native witness, even with a written reason",
                   {"unwitnessed_reason": "a reason does not excuse a required spawn"}),
                  ("an optional spawn without a reason", {"requirement": "optional"}),
                  ("an optional spawn with a too-short reason", {"requirement": "optional", "unwitnessed_reason": "short"})):
    try:
        try_spawn(f"u-{len(label)}", **kw)
        check(f"{label} is refused", False)
    except L.ContractViolation:
        check(f"{label} is refused", True)
def guarded(label: str, fn):
    """An unexpected refusal is a named failure, never the end of the suite."""
    try:
        return fn()
    except L.ContractViolation as e:
        check(f"{label} (refused unexpectedly)", False, str(e.reasons))
        return None


guarded("an optional unwitnessed spawn starts",
        lambda: try_spawn("u-opt", requirement="optional", unwitnessed_reason="capture client talks only to loopback"))
last = json.loads(L.spawns_log(C2).read_bytes().decode("utf-8").splitlines()[-1])
check("an optional unwitnessed spawn records its reason", last["witness"] == {
    "native": "off", "requirement": "optional", "unwitnessed_reason": "capture client talks only to loopback"}, str(last["witness"]))
nat2 = L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=None)
guarded("a witnessed spawn starts", lambda: try_spawn("u-req", witnesses=L.Witnesses(C2, native=nat2, fs=None)))
check("a witnessed spawn registers its child with the native witness", 102 in nat2.tracked)

print("\n- the auditor's pre-gate: W1-W6, R1 -")
# W2: no wildcard
w2 = L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=None)
ok2 = w2.register(99999)
w2.sample()
check("W2 a child with no readable identity is not tracked and the check is incomplete",
      ok2 is False and 99999 not in w2.tracked and w2.result.unwitnessed_registrations == 1 and not w2.result.complete)
check("W2 no tracked identity is ever the 0.0 wildcard", all(v != 0.0 for v in w2.tracked.values()) and 0.0 not in nw.tracked.values())
# W3: windows cover their own trees only
fs3 = FakeSampler()
fs3.procs = [(100, 1, 10.0), (300, 1, 30.0)]
fs3.rows = [(100, "10.0.0.5", 5000, "13.35.1.1", 443, "ESTABLISHED"), (300, "10.0.0.5", 5001, "192.0.2.1", 9, "ESTABLISHED")]
w3 = L.NativeEgressWitness(sampler=fs3, tick_s=60, jobs=None)
w3.register(100)
w3.register(300)
w3.open_window("hf-fetch", {100})
w3.sample()
check("W3 a window files its own tree's remote as a window host", "13.35.1.1:443" in w3.result.window_hosts)
check("W3 ... and another tree's remote during it stays a hit", "192.0.2.1:9" in w3.result.hit_remotes and w3.result.hits == 1)
# W4: the sampler thread never dies; coverage
calls = {"n": 0}


def docker_bad(argv):
    calls["n"] += 1
    if calls["n"] == 2:
        return 0, "  sl  local_address rem_address   st\n   0: GARBAGE 0100007F:0000 0A\n"
    return 0, "  sl  local_address rem_address   st\n"


import time as _time


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
cw4 = L.ContainerEgressWitness("letta", gateway_ip=GW, allowed_ports=[47001], run_docker=docker_bad, tick_s=0.05)
cw4.start()
reached = wait_until(lambda: calls["n"] >= 10)          # an event wait: 8 ticks AFTER the malformed one (call 2)
r4 = cw4.stop()
check("W4 a malformed /proc line is a failed sample and the thread lives on (it kept sampling after the bad tick)",
      reached and r4.failed_samples >= 1 and r4.samples >= 8 and calls["n"] - 2 >= 8 and not r4.complete,
      f"calls {calls['n']} samples {r4.samples} failed {r4.failed_samples}")


class SlowSampler(FakeSampler):
    def processes(self):
        _time.sleep(0.2)
        return super().processes()


w4 = L.NativeEgressWitness(sampler=SlowSampler(), tick_s=0.05, jobs=None)
w4.register(100)
w4.start()
_time.sleep(1.0)
r4b = w4.stop()
check("W4 a sampler that falls behind its ticks leaves the check incomplete (coverage < 0.9)",
      not r4b.complete and r4b.samples < 0.9 * r4b.expected_samples, f"{r4b.samples}/{r4b.expected_samples}")
# W5: the record carries its tick, expected samples and limit
rec5 = r4b.as_record()
check("W5 every egress record carries tick_s, expected_samples, a sample cost and a non-empty limit",
      rec5["tick_s"] == 0.05 and rec5["expected_samples"] >= 1 and rec5["sample_cost_ms"] > 0
      and "not seen" in rec5["limit"], str({k: rec5[k] for k in ("tick_s", "expected_samples", "limit")}))
# W6: the decoy secret
fake_env = {"SystemRoot": os.environ.get("SystemRoot", "C:\\Windows")}
name6 = L.plant_decoy_env(cn, environ=fake_env)
check("W6 the decoy secret is planted in the scheduler's environment under a secret-shaped name",
      name6 == "NVT3_DECOY_TOKEN" and fake_env.get(name6) == cn.values["decoy_env"], str(sorted(fake_env)))
u6 = L.make_unit_dirs(C2, "s6", "r", "a", "u")
env6 = L.build_env(C2, parent_env=fake_env, unit=u6, path_dirs=[], declared={}, catcher_url=CATCHER)
check("W6 build_env never copies it", "NVT3_DECOY_TOKEN" not in env6 and cn.values["decoy_env"] not in " ".join(env6.values()))
check("W6 assert_env refuses it if it ever appears",
      any("NVT3_DECOY_TOKEN" in v for v in L.assert_env(C2, dict(env6, NVT3_DECOY_TOKEN=cn.values["decoy_env"]),
                                                          parent_env=fake_env, catcher_url=CATCHER)))
# R1: the logs are hash-chained
log = L.spawns_log(C2)
check("R1 the spawn log is hash-chained", L.verify_chain(log))
lines = log.read_bytes().split(b"\n")
lines[1] = lines[1].replace(b'"role"', b'"rolE"')
tampered = TMP / "tampered.jsonl"
tampered.write_bytes(b"\n".join(lines))
check("R1 ... and an edited line breaks the chain", not L.verify_chain(tampered))
check("the check record says when the check began", bool(rec.get("begin_utc")) and rec["begin_utc"] <= rec["utc"], str(rec.get("begin_utc")))
check("the check record's native block carries the m5 fields",
      all(k in rec["native"] for k in ("hits", "samples", "failed_samples", "complete", "tick_s", "expected_samples", "limit")))
# W1: a real job object (Windows)
if os.name == "nt":
    jobs = L.WinJobs()
    nat1 = L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=jobs)
    marker = TMP / "grandchild.pid"
    gcode = ("import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'], "
             "creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0), stdin=subprocess.DEVNULL, "
             "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); open(sys.argv[1], 'w').write(str(p.pid))")
    u1 = L.make_unit_dirs(C2, "s1", "r", "a", "u")
    e1 = L.build_env(C2, parent_env=os.environ, unit=u1, path_dirs=[Path(sys.executable).parent], declared={},
                     catcher_url=CATCHER)
    kid = guarded("W1 a real spawn into its own job starts",
                  lambda: L.spawn(C2, [sys.executable, "-c", gcode, str(marker)], env=e1, cwd=u1.cwd, record={"role": "t"},
                                  parent_env=os.environ, catcher_url=CATCHER,
                                  witnesses=L.Witnesses(C2, native=nat1, fs=None)))
if os.name == "nt" and kid is not None:
    kid.wait(timeout=60)
    gpid = int(marker.read_text()) if marker.exists() else -1
    members = jobs.members(kid.process.pid)
    check("W1 the child was resumed after joining its job (it ran)", kid.process.returncode == 0 and gpid > 0)
    check("W1 an orphaned, detached grandchild stays in the tree's job", gpid in members, f"{gpid} in {members}")
    kid.kill_tree()
    deadline = _time.time() + 5
    while jobs.members(kid.process.pid) and _time.time() < deadline:
        _time.sleep(0.05)
    check("W1 kill_tree ends the whole tree, orphan included (TerminateJobObject)", jobs.members(kid.process.pid) == [])
    try:
        L.spawn(C2, [sys.executable, "-c", "pass"], env=e1, cwd=L.make_unit_dirs(C2, "s1", "r", "a", "u2").cwd,
                record={"role": "t"}, parent_env=os.environ, catcher_url=CATCHER,
                witnesses=L.Witnesses(C2, native=nat1, fs=None), creationflags=0x01000000)
        check("W1 a spawn asking to break away from its job is refused", False)
    except L.ContractViolation as e:
        check("W1 a spawn asking to break away from its job is refused", any("break away" in r for r in e.reasons))
else:
    print("       (not Windows: the job-object checks are Windows-only; the limit is declared in the record)")


print("\n- fetch windows -")
nat3 = L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=None)
W3 = L.Witnesses(C2, native=nat3, fs=None)
opened = []
with L.fetch_window(C2, "hf", ["huggingface.co"], witnesses=W3, proxy_control=lambda *a: opened.append(a[0])) as win:
    inside = dict(nat3.windows)
check("a window is open inside and closed after, and the catcher is told both",
      "hf" in inside and inside["hf"] is win.roots and nat3.windows == {} and opened == ["open", "close"])
wl = [json.loads(x) for x in (C2.runs_root / "_launch" / "windows.jsonl").read_bytes().decode("utf-8").splitlines()]
check("START and END are recorded with the host allowlist", [w["event"] for w in wl] == ["START", "END"]
      and wl[0]["hosts"] == ["huggingface.co"])

print("\n- the amended Q1: revocation, the window's hop, the declared hop value -")
s_rv = FakeSampler()
s_rv.procs = [(100, 1, 10.0), (300, 1, 30.0)]
w_rv = L.NativeEgressWitness(sampler=s_rv, tick_s=60, jobs=None)
w_rv.register(100)
w_rv.allow_listener(300, "declared hop for window py-base-314")
s_rv.rows, s_rv.listen = [(100, "127.0.0.1", 6200, "127.0.0.1", 10809, "ESTABLISHED")], {("tcp", 10809): {300}}
w_rv.sample()
inside_hits = w_rv.result.loopback_hits
revoked = w_rv.revoke_listener(300)
w_rv.sample()
check("an allowed hop is no hit inside the window, and a hit once revoked", inside_hits == 0 and revoked
      and w_rv.result.loopback_hits == 1, f"{inside_hits} -> {w_rv.result.loopback_hits}")
rec_rv = w_rv.result.as_record()
check("the revocation is recorded by reason and pid, and a second revoke is a no-op",
      rec_rv["revoked_listeners"] == [{"reason": "declared hop for window py-base-314", "pid": 300}]
      and w_rv.revoke_listener(300) is False)
with L.fetch_window(C2, "py-base-314", ["api.nuget.org"], via="127.0.0.1:10809"):
    pass
wl = [json.loads(x) for x in (C2.runs_root / "_launch" / "windows.jsonl").read_bytes().decode("utf-8").splitlines()]
check("a window's START names its hop next to its hosts", wl[-2]["event"] == "START" and wl[-2].get("via") == "127.0.0.1:10809"
      and wl[-2]["hosts"] == ["api.nuget.org"] and wl[-1]["event"] == "END", str(wl[-2:]))
check("a window without a hop records via null", wl[0]["via"] is None if "via" in wl[0] else False, str(wl[0]))
for bad in ("10.0.0.5:3128", "localhost:10809", "http://127.0.0.1:10809", "127.0.0.1"):
    try:
        with L.fetch_window(C2, "bad", ["x"], via=bad):
            pass
        check(f"a window hop {bad!r} is refused", False)
    except L.ContractViolation:
        check(f"a window hop {bad!r} is refused", True)
cfgdir = C2.runs_root / "_config"
cfgdir.mkdir(parents=True, exist_ok=True)
check("no network.json: no hop", L.network_via_port(C2) is None)
(cfgdir / "network.json").write_bytes(json.dumps({"via": {"host": "127.0.0.1", "port": 10809}}).encode())
check("network.json {via: {127.0.0.1, port}} gives the port", L.network_via_port(C2) == 10809)
for label, raw in (("another host", {"via": {"host": "10.0.0.5", "port": 3128}}),
                   ("a target beside the hop", {"via": {"host": "127.0.0.1", "port": 1, "target": "x:443"}}),
                   ("a port as text", {"via": {"host": "127.0.0.1", "port": "10809"}}),
                   ("a port as a bool", {"via": {"host": "127.0.0.1", "port": True}}),
                   ("an extra top-level key", {"via": {"host": "127.0.0.1", "port": 10809}, "cafile": "x"})):
    (cfgdir / "network.json").write_bytes(json.dumps(raw).encode())
    try:
        L.network_via_port(C2)
        check(f"network.json with {label} is refused", False)
    except L.ContractViolation:
        check(f"network.json with {label} is refused", True)


class ListenSampler:
    def __init__(self, table):
        self.table = table

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return self.table


check("the hop's pid is the one LISTEN owner of its port",
      L.hop_listener_pid(10809, sampler=ListenSampler({("tcp", 10809): {4242}, ("tcp", 10808): {4242}})) == 4242)
check("no owner, two owners, an unreadable owner or no table: no pid",
      L.hop_listener_pid(10809, sampler=ListenSampler({})) is None
      and L.hop_listener_pid(10809, sampler=ListenSampler({("tcp", 10809): {1, 2}})) is None
      and L.hop_listener_pid(10809, sampler=ListenSampler({("tcp", 10809): {None}})) is None
      and L.hop_listener_pid(10809, sampler=ListenSampler(None)) is None
      and L.hop_listener_pid(10809, sampler=ListenSampler({("udp", 10809): {7}})) is None)
check("py314 is in the watched idle set of the machine's contract", "py314" in L.Contract._machine().polygon_idle
      and any(s.label == "polygon_py314" for s in L.watched_set(L.Contract._machine())))
check("Q-A4-5: arms314, the venv our arms and letta's adapter run on, is in the watched idle set - no child alters "
      "the interpreter an arm runs on", "arms314" in L.Contract._machine().polygon_idle
      and any(s.label == "polygon_arms314" for s in L.watched_set(L.Contract._machine())))
check("the auditor's replay tooling (py310, core_bare310, ci_linux) is in the watched idle set too",
      {"py310", "core_bare310", "ci_linux"} <= set(L.Contract._machine().polygon_idle)
      and {"polygon_py310", "polygon_core_bare310", "polygon_ci_linux"}
      <= {s.label for s in L.watched_set(L.Contract._machine())})

# Resource ownership controls use a declared injected Win32 API, not foreign PIDs.
print("\n- partial Win32 acquisition and registration -")
import ctypes as _ct
from ctypes import wintypes as _wt
from unittest import mock as _mock
from types import SimpleNamespace as _Namespace
import threading as _threading

class _JobFunction:
    def __init__(self, call):
        self.call = call
    def __call__(self, *args):
        return self.call(*args)

class _JobApi:
    def __init__(self):
        self.next_handle = 1000
        self.live = {}
        self.close_calls = []
        self.close_refused = set()
        self.config_ok = self.assign_ok = True
        self.at_config = None
        self.job_members = {}
        self.job_flags = []
        for name, method in [('CreateJobObjectW', self.create), ('OpenProcess', self.open),
                             ('SetInformationJobObject', self.configure), ('AssignProcessToJobObject', self.assign),
                             ('CloseHandle', self.close), ('GetProcessTimes', self.times),
                             ('QueryInformationJobObject', self.query), ('TerminateJobObject', lambda *a: True)]:
            setattr(self, name, _JobFunction(method))
    def acquire(self, kind):
        self.next_handle += 1
        self.live[self.next_handle] = kind
        return self.next_handle
    def create(self, *args):
        return self.acquire('job')
    def open(self, *args):
        return self.acquire('process')
    def configure(self, handle, kind, info, size):
        self.job_flags.append(info._obj.Basic.LimitFlags)
        if self.at_config is not None:
            self.at_config(handle)
        return self.config_ok
    def assign(self, job, process):
        if self.assign_ok:
            self.job_members[job] = [42]
        return self.assign_ok
    def close(self, handle):
        self.close_calls.append(handle)
        if handle in self.close_refused:
            return False
        if handle not in self.live:
            raise AssertionError('must not close an unowned or previously closed handle')
        del self.live[handle]
        return True
    def times(self, handle, created, *args):
        value = 116444736000000000 + 120000000
        created._obj.dwLowDateTime, created._obj.dwHighDateTime = value & 0xffffffff, value >> 32
        return True
    def query(self, handle, kind, info, size, returned):
        members = self.job_members.get(handle, [])
        info._obj.inlist = len(members)
        for index, pid in enumerate(members):
            info._obj.ids[index] = pid
        return True

def _job_fixture():
    api = _JobApi()
    nt = _Namespace(NtResumeProcess=_JobFunction(lambda *a: 0))
    with _mock.patch.object(_ct, 'WinDLL', side_effect=[api, nt], create=True):
        jobs = L.WinJobs()
    return jobs, api

def _ownership_row(name, action):
    try:
        with _mock.patch.object(_ct, 'get_last_error', return_value=1722, create=True):
            check(name, action())
    except Exception as error:
        check(name, False, type(error).__name__ + ': ' + str(error))

def _configuration_failure_keeps_ownership():
    jobs, api = _job_fixture()
    api.config_ok = False
    observed = []
    def retained_before_config(handle):
        observed.append(handle in getattr(jobs, '_owned_handles', {}))
        api.close_refused.add(handle)
    api.at_config = retained_before_config
    try:
        jobs._new_job()
        return False
    except OSError as error:
        if error.errno != 1722 or observed != [True] or not api.live or set(api.live) != set(jobs._owned_handles):
            return False
    api.close_refused.clear()
    return jobs.cleanup_owned() is True and not api.live and not jobs._owned_handles

_ownership_row('JOB-CONFIG-1: a configured Job is owned before the next API call; failed close retains it for retry', _configuration_failure_keeps_ownership)

def _assignment_failure_keeps_every_handle():
    jobs, api = _job_fixture()
    api.assign_ok = False
    api.at_config = lambda h: api.close_refused.update(api.live)
    if jobs.adopt(42) is not None or jobs.jobs or len(api.live) != 2 or set(jobs._owned_handles) != set(api.live):
        return False
    api.close_refused.clear()
    return jobs.cleanup_owned() is True and not api.live and not jobs._owned_handles

_ownership_row('JOB-ASSIGN-1: refused assignment retains both owned acquisitions after false CloseHandle results', _assignment_failure_keeps_every_handle)

def _identity_close_is_retained():
    jobs, api = _job_fixture()
    old_open = api.OpenProcess.call
    def refuse_close(*args):
        handle = old_open(*args)
        api.close_refused.add(handle)
        return handle
    api.OpenProcess.call = refuse_close
    if jobs.identity(42) != 12.0 or len(api.live) != 1 or set(jobs._owned_handles) != set(api.live):
        return False
    api.close_refused.clear()
    return jobs.cleanup_owned() is True and not api.live

_ownership_row('JOB-PROCESS-1: identity retains an owned process handle until CloseHandle actually succeeds', _identity_close_is_retained)

def _duplicate_assignment_cannot_overwrite():
    jobs, api = _job_fixture()
    if jobs.adopt(42, handle=700) != 12.0:
        return False
    first, before = jobs.jobs[42], dict(api.live)
    return jobs.adopt(42, handle=700) is None and jobs.jobs[42] == first and api.live == before \
        and api.job_flags == [L.WinJobs.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE]

_ownership_row('JOB-DUP-1: repeated direct adoption refuses before acquiring or replacing the existing Job', _duplicate_assignment_cannot_overwrite)

def _borrowed_and_live_resources_stay_owned():
    jobs, api = _job_fixture()
    if jobs.adopt(42, handle=700) != 12.0 or len(api.live) != 1:
        return False
    if jobs.cleanup_owned() is not True or 700 in api.close_calls or len(api.live) != 1:
        return False
    api.job_members[jobs.jobs[42]] = []
    if jobs.cleanup_owned(close_drained=True) is not True or api.live or jobs.jobs or jobs._owned_handles:
        return False
    before = list(api.close_calls)
    return jobs.cleanup_owned() is True and api.close_calls == before

_ownership_row('JOB-BORROWED-1: borrowed process handles are never closed; live assigned Jobs persist and drained Jobs close once', _borrowed_and_live_resources_stay_owned)

def _independent_cleanup_keeps_failed_handle():
    jobs, api = _job_fixture()
    job = jobs._new_job()
    process = jobs._open(42, jobs.PROCESS_QUERY_LIMITED)
    api.close_refused.add(job)
    if jobs.cleanup_owned() is not False or process in api.live or set(api.live) != {job} \
            or set(jobs._owned_handles) != {job} or job not in api.close_calls or process not in api.close_calls:
        return False
    api.close_refused.clear()
    return jobs.cleanup_owned() is True and not api.live

_ownership_row('JOB-CLEANUP-1: one failed close cannot suppress independent cleanup or disappear from the retained registry', _independent_cleanup_keeps_failed_handle)

def _failed_job_is_not_an_identity_only_registration():
    jobs, api = _job_fixture()
    api.assign_ok = False
    native = L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=jobs)
    accepted = native.register(42, handle=700, label='failed-job')
    return accepted is False and native.registered_roots == {} and native.tracked == {} \
        and native.result.unwitnessed_registrations == 1

_ownership_row('JOB-REGISTER-1: failed Job adoption never falls back to a readable identity while claiming Job coverage', _failed_job_is_not_an_identity_only_registration)

def _failed_resource_cleanup_is_not_a_complete_witness():
    jobs, api = _job_fixture()
    process = jobs._open(42, jobs.PROCESS_QUERY_LIMITED)
    api.close_refused.add(process)
    native = L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=jobs)
    result = native.stop()
    if result.complete is not False or result.resource_errors <= 0 or process not in jobs._owned_handles:
        return False
    api.close_refused.clear()
    return jobs.cleanup_owned() is True and result.complete is False

_ownership_row('JOB-RESULT-1: a failed retained-handle cleanup leaves the native result incomplete even if a later retry succeeds', _failed_resource_cleanup_is_not_a_complete_witness)

def _capture_precedes_job_configuration():
    spec = importlib.util.spec_from_file_location('partial_job_probe203', ROOT / 'research/v3/probe_sm.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    jobs, api = _job_fixture()
    retained = _Namespace(child=_Namespace(process=_Namespace(pid=42, _handle=700)), job=None)
    capture = module.CapturingJobs(jobs, retained)
    api.config_ok = False
    observed = []
    def observed_before_config(handle):
        observed.append(handle in capture.created and handle in getattr(jobs, '_owned_handles', {}))
        api.close_refused.add(handle)
    api.at_config = observed_before_config
    try:
        capture.adopt(42, handle=700)
        return False
    except OSError:
        pass
    api.close_refused.clear()
    return observed == [True] and len(capture.created) == 1 and retained.job is None and jobs.cleanup_owned() is True and not api.live

_ownership_row('JOB-CAPTURE-1: actual Job acquisition reaches the capture owner before configuration can fail', _capture_precedes_job_configuration)

def _native_stop_preserves_retained_job():
    jobs, api = _job_fixture()
    jobs.adopt(42, handle=700)
    handle = jobs.jobs[42]
    api.job_members[handle] = []
    native = L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=jobs)
    result = native.stop()
    if not result.complete or jobs.jobs.get(42) != handle or handle not in jobs._owned_handles or handle not in api.live:
        return False
    return jobs.members(42) == [] and jobs.cleanup_owned(close_drained=True) is True and not api.live

_ownership_row('JOB-LIFETIME-1: stopping a witness preserves a captured drained Job until its owner explicitly retires it', _native_stop_preserves_retained_job)

def _stuck_ticker_is_not_sampled_or_cleaned():
    jobs, api = _job_fixture()
    handle = jobs._open(42, jobs.PROCESS_QUERY_LIMITED)
    native = L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=jobs)
    class Stuck:
        def join(self, timeout):
            pass
        def is_alive(self):
            return True
    native._thread = Stuck()  # Declared non-drained worker; no real thread is started.
    def forbidden_final_sample():
        raise AssertionError('must not enter a sample while the worker is still live')
    native._safe_sample = forbidden_final_sample
    result = native.stop()
    return result.complete is False and result.failed_samples > 0 and result.resource_errors > 0 \
        and handle in jobs._owned_handles and handle in api.live and not api.close_calls

_ownership_row('JOB-STOP-TICKER-1: a worker that did not join never triggers another blocking sample or concurrent handle cleanup', _stuck_ticker_is_not_sampled_or_cleaned)

def _early_close_failure_remains_incomplete():
    jobs, api = _job_fixture()
    old_open = api.OpenProcess.call
    def refuse_initial_close(*args):
        handle = old_open(*args)
        api.close_refused.add(handle)
        return handle
    api.OpenProcess.call = refuse_initial_close
    if jobs.identity(42) != 12.0:
        return False
    api.close_refused.clear()
    native = L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=jobs)
    result = native.stop()
    return not api.live and not jobs._owned_handles and result.resource_errors > 0 and result.complete is False

_ownership_row('JOB-EARLY-CLOSE-1: a later successful cleanup cannot erase an earlier failed CloseHandle observation', _early_close_failure_remains_incomplete)

def _direct_registration_exception_remains_incomplete():
    jobs, api = _job_fixture()
    api.config_ok = False
    native = L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=jobs)
    try:
        native.register(42, handle=700, label='exception-job')
        return False
    except OSError:
        pass
    result = native.stop()
    return native.registered_roots == {} and result.unwitnessed_registrations == 1 \
        and result.complete is False and not api.live

_ownership_row('JOB-REGISTER-EXC-1: failed direct adoption stays an unwitnessed registration even after its acquired handles close', _direct_registration_exception_remains_incomplete)


# Declared API controls exercise generic launch exceptions, not vendor admission.
print('\n- exact created-process exception ownership -')
_spawn208_count = 0

class _SpawnProcess208:
    pid = 42
    def __init__(self, *, handle_fault=False):
        self.handle_fault = handle_fault
        self.alive = True
        self.kill_refused = False
        self.kill_calls = self.wait_calls = 0
        self.retained_before_handle = False
    @property
    def _handle(self):
        self.retained_before_handle = any(owner.process is self for owner in getattr(L, '_FAILED_SPAWNS', {}).values())
        if self.handle_fault:
            raise ValueError('declared handle conversion fault')
        return 700
    def poll(self):
        return None if self.alive else 0
    def kill(self):
        self.kill_calls += 1
        if self.kill_refused:
            raise OSError('declared exact-root termination refusal')
        self.alive = False
    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.alive:
            raise subprocess.TimeoutExpired('declared own process', timeout)
        return 0

def _spawn208(native, process, *, window=None):
    global _spawn208_count
    _spawn208_count += 1
    unit = L.make_unit_dirs(C2, 's208', 'r', 'a', 'u' + str(_spawn208_count))
    env = L.build_env(C2, parent_env=os.environ, unit=unit, path_dirs=[], declared={}, catcher_url=CATCHER)
    return L.spawn(C2, [sys.executable, '-c', 'pass'], env=env, cwd=unit.cwd,
        record={'role': 't'}, parent_env=os.environ, catcher_url=CATCHER,
        witnesses=L.Witnesses(C2, native=native, fs=None), popen=lambda *a, **kw: process, window=window)

def _spawn_fixture208():
    jobs, api = _job_fixture()
    process = _SpawnProcess208()
    native = L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=jobs)
    events = []
    def terminate(handle, code):
        events.append(('terminate', handle))
        api.job_members[handle] = []
        return True
    api.TerminateJobObject.call = terminate
    return jobs, api, process, native, events

def _pending208(native):
    return [owner for owner in getattr(L, '_FAILED_SPAWNS', {}).values() if owner.native is native]

def _before_registration208():
    jobs, api, proc, native, events = _spawn_fixture208()
    proc.handle_fault = True
    try:
        _spawn208(native, proc)
        return False
    except ValueError as error:
        return ('declared handle' in str(error) and proc.retained_before_handle and not proc.alive
                and proc.kill_calls == 1 and proc.wait_calls == 1 and not _pending208(native)
                and native.result.resource_errors > 0 and not native.result.complete and not api.live)

_ownership_row('JOB-SPAWN-PRE-1: exact Popen retained before handle access; original fault survives verified root cleanup', _before_registration208)

def _assigned_failure208():
    jobs, api, proc, native, events = _spawn_fixture208()
    jobs.adopt(999, 701)
    foreign = jobs.jobs[999]
    api.job_members[foreign] = [999]
    original_register = native.register
    assigned = []
    def fault(pid, **kw):
        result = original_register(pid, **kw)
        assigned.append(jobs.jobs[pid])
        jobs.jobs[pid] = foreign
        raise LookupError('declared post-adoption fault')
    native.register = fault
    try:
        _spawn208(native, proc)
        return False
    except LookupError:
        return (events == [('terminate', assigned[0])] and assigned[0] not in api.live
            and foreign in api.live and api.job_members[foreign] == [999]
            and not proc.alive and not _pending208(native) and not native.result.complete)

_ownership_row('JOB-SPAWN-ASSIGNED-1: adoption exception drains captured Job despite a replaced PID mapping; unrelated Job untouched', _assigned_failure208)

def _post_resume208():
    jobs, api, proc, native, events = _spawn_fixture208()
    assigned = []
    def resume(handle):
        assigned.append(jobs.jobs[42])
        api.job_members[assigned[-1]] = [42, 43, 44]
        raise KeyboardInterrupt('declared after-execution interruption')
    jobs.resume = resume
    try:
        _spawn208(native, proc)
        return False
    except KeyboardInterrupt as error:
        return (str(error) == 'declared after-execution interruption'
            and events == [('terminate', assigned[0])] and api.job_members[assigned[0]] == []
            and assigned[0] not in api.live and not proc.alive and not _pending208(native)
            and not native.result.complete)

_ownership_row('JOB-SPAWN-RESUME-1: interrupted resume terminates and drains the exact Job including descendants before discarding ownership', _post_resume208)

def _cleanup_retry208():
    jobs, api, proc, native, events = _spawn_fixture208()
    proc.kill_refused = True
    refused = True
    def terminate(handle, code):
        events.append(('terminate', handle))
        if refused:
            return False
        api.job_members[handle] = []
        return True
    api.TerminateJobObject.call = terminate
    jobs.resume = lambda handle: (_ for _ in ()).throw(RuntimeError('declared resume fault'))
    clock = [0.0]
    def now():
        clock[0] += 0.25
        return clock[0]
    with _mock.patch.object(L.time, 'monotonic', side_effect=now), _mock.patch.object(L.time, 'sleep'):
        try:
            _spawn208(native, proc)
            return False
        except RuntimeError as error:
            if str(error) != 'declared resume fault':
                return False
        pending = _pending208(native)
        if len(pending) != 1 or pending[0].process is not proc or not pending[0].failed or not api.live:
            return False
        # Ongoing acquisition entries cannot be selected by a concurrent stop.
        pending[0].failed = False
        before = len(events)
        if L._retry_failed_spawns(native) is not True or len(events) != before:
            return False
        pending[0].failed = True
        refused, proc.kill_refused = False, False
        if L._retry_failed_spawns(native) is not True:
            return False
        return not _pending208(native) and not api.live and not proc.alive and not native.result.complete

_ownership_row('JOB-SPAWN-RETRY-1: failed cleanup retains exact ownership for bounded retry; acquiring entries excluded and original result sticky', _cleanup_retry208)

def _bookkeeping_before_resume208():
    jobs, api, proc, native, events = _spawn_fixture208()
    resumed = []
    jobs.resume = lambda h: resumed.append(h) or True
    class Roots:
        def add(self, pid):
            raise ArithmeticError('declared window bookkeeping fault')
    try:
        _spawn208(native, proc, window=_Namespace(roots=Roots()))
        return False
    except ArithmeticError:
        return not resumed and not proc.alive and not api.live and not _pending208(native)

_ownership_row('JOB-SPAWN-BOOKKEEP-1: fallible window bookkeeping occurs while the retained process is still suspended', _bookkeeping_before_resume208)

def _generation208():
    jobs, api = _job_fixture()
    first = jobs._new_job()
    lease = jobs._job_leases[first]
    if not jobs.drain_lease(lease):
        return False
    api.next_handle = first - 1
    replacement = jobs._new_job()
    if replacement != first or jobs._job_leases[first] is lease:
        return False
    calls = []
    api.TerminateJobObject.call = lambda *a: calls.append(a) or True
    forged = L._JobLease(first)
    return (jobs.terminate_lease(lease) is True and jobs.drain_lease(lease) is True
        and jobs.terminate_lease(forged) is False and jobs.drain_lease(forged) is False
        and not calls and first in api.live and jobs._job_leases[first] is not lease)

_ownership_row('JOB-SPAWN-GENERATION-1: retired or forged leases cannot terminate a recycled numeric Job handle', _generation208)

def _no_job_proof208():
    native = L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=None)
    proc = _SpawnProcess208()
    native.register = lambda *a, **kw: (_ for _ in ()).throw(ValueError('declared no-Job registration fault'))
    try:
        _spawn208(native, proc)
        return False
    except ValueError:
        pending = _pending208(native)
        if not (len(pending) == 1 and pending[0].process is proc and not proc.alive and not native.result.complete):
            return False
        # Declared injected process has no actual descendants or OS resources.
        # Remove only this test-owned record after asserting production refusal.
        with L._SPAWN_OWNERS_LOCK:
            del L._FAILED_SPAWNS[pending[0].key]
        return True

_ownership_row('JOB-SPAWN-NO-JOB-1: root wait without suspension or a captured Job refuses whole-tree proof and retains incomplete state', _no_job_proof208)

print('\n- interrupted close and retained acquisition generations -')

def _close_interrupted212():
    jobs, api = _job_fixture()
    handle = jobs._new_job()
    lease = jobs._job_leases[handle]
    original_close = api.CloseHandle.call
    def close_then_interrupt(value):
        original_close(value)
        raise KeyboardInterrupt('declared after successful CloseHandle')
    api.CloseHandle.call = close_then_interrupt
    try:
        jobs._close_owned(handle)
    except KeyboardInterrupt:
        pass
    api.CloseHandle.call = original_close
    api.next_handle = handle - 1
    # A separate declared component acquires the recycled handle. WinJobs does
    # not receive a new ownership lease; the old ambiguous lease must refuse it.
    replacement = api.create(None, None)
    if replacement != handle:
        return False
    calls = []
    api.TerminateJobObject.call = lambda *a: calls.append(a) or True
    before = list(api.close_calls)
    if jobs.terminate_lease(lease) is not False or jobs.drain_lease(lease) is not False:
        return False
    return (not calls and api.close_calls == before and replacement in api.live
        and getattr(lease, 'uncertain', False) and jobs._resource_errors > 0
        and jobs.cleanup_owned() is False)

_ownership_row('JOB-CLOSE-INTERRUPT-1: interrupted successful close revokes old authority before a recycled numeric handle can be used', _close_interrupted212)

def _retained_generation212():
    spec = importlib.util.spec_from_file_location('probe_sm_generation212', ROOT / 'research/v3/probe_sm.py')
    probe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = probe
    spec.loader.exec_module(probe)
    jobs, api = _job_fixture()
    jobs.adopt(42, handle=700)
    handle = jobs.jobs[42]
    retained = probe.RetainedJob.from_handle(jobs, 42, handle)
    api.job_members[handle] = []
    if jobs.cleanup_owned(close_drained=True) is not True:
        return False
    api.next_handle = handle - 1
    if jobs.adopt(42, handle=701) != 12.0 or jobs.jobs[42] != handle:
        return False
    api.job_members[handle] = [42, 99]
    calls = []
    api.TerminateJobObject.call = lambda *a: calls.append(a) or True
    try:
        retained.members()
        return False
    except (probe.PreflightRefused, L.WitnessUnavailable):
        pass
    return retained.terminate() is False and retained.same_binding() is False and not calls \
        and api.job_members[handle] == [42, 99] and handle in api.live

_ownership_row('JOB-RETAINED-GENERATION-1: RetainedJob cannot read, terminate or accept a replacement with the same handle and PID', _retained_generation212)

def _retained_lock213():
    spec = importlib.util.spec_from_file_location('probe_sm_lock213', ROOT / 'research/v3/probe_sm.py')
    probe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = probe
    spec.loader.exec_module(probe)
    for operation in ('terminate', 'same_binding'):
        jobs, api = _job_fixture()
        jobs.adopt(42, handle=700)
        handle = jobs.jobs[42]
        retained = probe.RetainedJob.from_handle(jobs, 42, handle)
        lease = jobs._job_leases[handle]
        api.job_members[handle] = []
        original_active = jobs.active_lease
        entered, finished = _threading.Event(), _threading.Event()
        errors, raced, workers = [], [], []
        def retire():
            entered.set()
            try:
                if not jobs.drain_lease(lease):
                    raise AssertionError('declared lease retirement failed')
                api.next_handle = handle - 1
                jobs.adopt(42, handle=701)
            except Exception as error:
                errors.append(type(error).__name__)
            finally:
                finished.set()
        fired = [False]
        main_thread = _threading.current_thread()
        def after_check(value):
            answer = original_active(value)
            if fired[0] or _threading.current_thread() is not main_thread:
                return answer
            fired[0] = True
            worker = _threading.Thread(target=retire, daemon=True)
            workers.append(worker)
            worker.start()
            if not entered.wait(1):
                raise AssertionError('owned race worker did not start')
            raced.append(finished.wait(0.1))
            return answer
        jobs.active_lease = after_check
        try:
            answer = getattr(retained, operation)()
        finally:
            jobs.active_lease = original_active
            for worker in workers:
                worker.join(timeout=2)
        if answer is not True or raced != [False] or errors or not finished.is_set() \
                or any(worker.is_alive() for worker in workers):
            return False
    return True

_ownership_row('JOB-RETAINED-LOCK-1: retained generation check and operation share the retirement lock while another owner retires and reuses the Job', _retained_lock213)

print('\n- actual owned Windows handles and interrupted child tree -')
import ctypes as _realct211
import subprocess
from ctypes import wintypes as _realwt211

class _ActualApi211:
    """Capture acquisitions for test containment; all handles belong to this test."""
    def __init__(self, base):
        self.base = base
        self.acquired = []
        self.refuse_close = set()
        self.refuse_configuration = False
        base.GetHandleInformation.argtypes = [_realwt211.HANDLE, _realct211.POINTER(_realwt211.DWORD)]
    def __getattr__(self, name):
        return getattr(self.base, name)
    def CreateJobObjectW(self, *args):
        handle = self.base.CreateJobObjectW(*args)
        if handle:
            self.acquired.append(int(handle))
        return handle
    def OpenProcess(self, *args):
        handle = self.base.OpenProcess(*args)
        if handle:
            self.acquired.append(int(handle))
            self.refuse_close.add(int(handle))
        return handle
    def SetInformationJobObject(self, handle, *args):
        if self.refuse_configuration:
            self.refuse_close.add(int(handle))
            _realct211.set_last_error(87)
            return False
        return self.base.SetInformationJobObject(handle, *args)
    def CloseHandle(self, handle):
        return False if int(handle) in self.refuse_close else self.base.CloseHandle(handle)
    def valid(self, handle):
        flags = _realwt211.DWORD()
        return bool(self.base.GetHandleInformation(handle, _realct211.byref(flags)))
    def members(self, handle):
        class Rows(_realct211.Structure):
            _fields_ = [('assigned', _realwt211.DWORD), ('inlist', _realwt211.DWORD),
                        ('ids', _realct211.c_size_t * 4096)]
        rows = Rows()
        if not self.base.QueryInformationJobObject(handle, 3, _realct211.byref(rows), _realct211.sizeof(rows), None):
            raise OSError('actual own test Job query refused')
        if rows.inlist > 4096:
            raise AssertionError('actual own test Job membership exceeds buffer')
        return [int(rows.ids[index]) for index in range(rows.inlist)]
    def contain(self):
        self.refuse_close.clear()
        for handle in self.acquired:
            if self.valid(handle):
                self.base.CloseHandle(handle)

def _actual_check211(name, action):
    if os.name != 'nt':
        check(name, False, 'requires Windows; not a substitute passing result')
        return
    try:
        check(name, action())
    except Exception as error:
        check(name, False, type(error).__name__ + ': ' + str(error))

def _actual_job_config211():
    jobs = L.WinJobs()
    api = _ActualApi211(jobs.k32)
    jobs.k32 = api
    api.refuse_configuration = True
    try:
        try:
            jobs._new_job()
            return False
        except OSError:
            pass
        handle, = api.acquired
        if not api.valid(handle) or handle not in getattr(jobs, '_owned_handles', {}):
            return False
        api.refuse_close.clear()
        return jobs.cleanup_owned() is True and not api.valid(handle) and not jobs._owned_handles
    finally:
        api.contain()

_actual_check211('JOB-PHYSICAL-CONFIG-1: actual newly acquired Job survives false CloseHandle and closes on owned retry', _actual_job_config211)

def _actual_process_identity211():
    jobs = L.WinJobs()
    api = _ActualApi211(jobs.k32)
    jobs.k32 = api
    try:
        identity = jobs.identity(os.getpid())  # Own current process; query-only handle.
        handle, = api.acquired
        if identity is None or not api.valid(handle) or handle not in getattr(jobs, '_owned_handles', {}):
            return False
        api.refuse_close.clear()
        return jobs.cleanup_owned() is True and not api.valid(handle) and not jobs._owned_handles
    finally:
        api.contain()

_actual_check211('JOB-PHYSICAL-PROCESS-1: actual OpenProcess query handle to this test remains owned after false close', _actual_process_identity211)

def _actual_interrupted_tree211():
    jobs = L.WinJobs()
    api = _ActualApi211(jobs.k32)
    jobs.k32 = api
    native = L.NativeEgressWitness(sampler=FakeSampler(), tick_s=60, jobs=jobs)
    marker = TMP / 'actual-interrupted211.pid'
    code = ("import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'],"
            "creationflags=getattr(subprocess,'DETACHED_PROCESS',0),stdin=subprocess.DEVNULL,"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); open(sys.argv[1],'w').write(str(p.pid))")
    unit = L.make_unit_dirs(C2, 'physical211', 'r', 'a', 'u')
    env = L.build_env(C2, parent_env=os.environ, unit=unit, path_dirs=[Path(sys.executable).parent],
                      declared={}, catcher_url=CATCHER)
    owned = []
    observed = []
    original_resume = jobs.resume
    def resume(handle):
        if not original_resume(handle):
            raise AssertionError('actual own resume failed')
        deadline = _time.monotonic() + 5
        while not marker.exists() and _time.monotonic() < deadline:
            _time.sleep(0.01)
        if not marker.exists():
            raise AssertionError('actual descendant was not created')
        descendant = int(marker.read_text())
        observed.append(descendant in jobs.members(owned[0].pid))
        raise RuntimeError('actual own interrupted-resume211')
    jobs.resume = resume
    def popen(*args, **kwargs):
        proc = subprocess.Popen(*args, **kwargs)
        owned.append(proc)  # Test containment even for the old source regression.
        return proc
    try:
        try:
            L.spawn(C2, [sys.executable, '-c', code, str(marker)], env=env, cwd=unit.cwd,
                record={'role': 't'}, parent_env=os.environ, catcher_url=CATCHER,
                witnesses=L.Witnesses(C2, native=native, fs=None), popen=popen)
            return False
        except RuntimeError as error:
            if str(error) != 'actual own interrupted-resume211':
                return False
        return (observed == [True] and len(owned) == 1 and owned[0].poll() is not None
            and len(api.acquired) == 1 and not api.valid(api.acquired[0])
            and not jobs.jobs and not native.result.complete
            and not any(owner.native is native for owner in getattr(L, '_FAILED_SPAWNS', {}).values()))
    finally:
        # Only newly captured physical Job handles, never the descendant PID.
        for handle in api.acquired:
            if api.valid(handle):
                api.base.TerminateJobObject(handle, 1)
                deadline = _time.monotonic() + 5
                while api.members(handle) and _time.monotonic() < deadline:
                    _time.sleep(0.01)
        for proc in owned:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        api.contain()

_actual_check211('JOB-PHYSICAL-TREE-1: actual interrupted resumed child and detached descendant drain through the captured Job', _actual_interrupted_tree211)

print('\n- initial capture, interrupted commit and raw termination lock -')

def _capture_root217():
    spec = importlib.util.spec_from_file_location('probe_sm_capture217', ROOT / 'research/v3/probe_sm.py')
    probe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = probe
    spec.loader.exec_module(probe)
    jobs, api = _job_fixture()
    jobs.adopt(42, handle=700)
    handle = jobs.jobs[42]
    old_lease = jobs._job_leases[handle]
    api.job_members[handle] = []
    entered, finished = _threading.Event(), _threading.Event()
    errors, raced, workers = [], [], []
    def retire():
        entered.set()
        try:
            if not jobs.drain_lease(old_lease):
                raise AssertionError('declared old root did not retire')
            api.next_handle = handle - 1
            jobs.adopt(999, handle=701)
        except Exception as error:
            errors.append(type(error).__name__)
        finally:
            finished.set()
    class RootLookup(dict):
        fired = False
        def get(self, key, default=None):
            value = super().get(key, default)
            if key == 42 and not self.fired:
                self.fired = True
                worker = _threading.Thread(target=retire, daemon=True)
                workers.append(worker)
                worker.start()
                if not entered.wait(1):
                    raise AssertionError('declared capture worker did not start')
                raced.append(finished.wait(0.1))
            return value
    jobs.jobs = RootLookup(jobs.jobs)
    try:
        retained = probe.RetainedJob(_Namespace(jobs=jobs), 42)
    finally:
        for worker in workers:
            worker.join(timeout=2)
    return (retained.lease is old_lease and raced == [False] and not errors and finished.is_set()
        and not any(worker.is_alive() for worker in workers) and retained.terminate() is False
        and retained.same_binding() is False)

_ownership_row('JOB-RETAINED-CAPTURE-1: root lookup and initial lease capture hold the retirement lock against concurrent handle reuse', _capture_root217)

def _close_commit217():
    jobs, api = _job_fixture()
    jobs.adopt(42, handle=700)
    handle = jobs.jobs[42]
    old_lease = jobs._job_leases[handle]
    class InterruptedDeletion(dict):
        def __delitem__(self, key):
            super().__delitem__(key)
            raise KeyboardInterrupt('declared interrupted close metadata deletion')
    jobs._handle_generations = InterruptedDeletion(jobs._handle_generations)
    jobs._close_owned(handle)
    jobs._handle_generations = dict(jobs._handle_generations)
    api.next_handle = handle - 1
    jobs.adopt(999, handle=701)
    if jobs.jobs[999] != handle or jobs._job_leases[handle] is old_lease:
        return False
    queries = []
    original_query = api.QueryInformationJobObject.call
    api.QueryInformationJobObject.call = lambda *a: queries.append(a[0]) or original_query(*a)
    try:
        jobs.members(42)
        return False
    except L.WitnessUnavailable:
        pass
    return not queries and getattr(old_lease, 'uncertain', False) and jobs._resource_errors > 0

_ownership_row('JOB-CLOSE-COMMIT-1: interrupted close metadata cannot authorize old-root queries against a replacement generation', _close_commit217)

def _raw_termination_lock217():
    jobs, api = _job_fixture()
    jobs.adopt(42, handle=700)
    handle = jobs.jobs[42]
    lease = jobs._job_leases[handle]
    api.job_members[handle] = []
    entered, finished = _threading.Event(), _threading.Event()
    raced, errors, workers = [], [], []
    def retire():
        entered.set()
        try:
            if not jobs.drain_lease(lease):
                raise AssertionError('declared raw termination retirement failed')
        except Exception as error:
            errors.append(type(error).__name__)
        finally:
            finished.set()
    def terminate(*args):
        worker = _threading.Thread(target=retire, daemon=True)
        workers.append(worker)
        worker.start()
        if not entered.wait(1):
            raise AssertionError('declared raw termination worker did not start')
        raced.append(finished.wait(0.1))
        return True
    api.TerminateJobObject.call = terminate
    try:
        result = jobs.terminate(42)
    finally:
        for worker in workers:
            worker.join(timeout=2)
    return result is True and raced == [False] and not errors and finished.is_set() \
        and not any(worker.is_alive() for worker in workers)

_ownership_row('JOB-TERMINATE-LOCK-1: raw root termination and Job retirement share the same handle lock', _raw_termination_lock217)

def _native_view_retry220():
    jobs, api, proc, native, events = _spawn_fixture208()
    class View:
        def __getattr__(self, name):
            return getattr(native, name)
    view = View()
    proc.kill_refused = True
    refused = [True]
    def terminate(handle, code):
        if refused[0]:
            return False
        api.job_members[handle] = []
        return True
    api.TerminateJobObject.call = terminate
    jobs.resume = lambda handle: (_ for _ in ()).throw(RuntimeError('declared native-view launch fault'))
    clock = [0.0]
    def now():
        clock[0] += 0.25
        return clock[0]
    with _mock.patch.object(L.time, 'monotonic', side_effect=now), _mock.patch.object(L.time, 'sleep'):
        try:
            _spawn208(view, proc)
            return False
        except RuntimeError:
            pass
        if not any(owner.process is proc and owner.failed for owner in L._FAILED_SPAWNS.values()):
            return False
        refused[0], proc.kill_refused = False, False
        L._retry_failed_spawns(native)
        passed = not any(owner.process is proc for owner in L._FAILED_SPAWNS.values()) and not api.live and not proc.alive
        if not passed:
            # Only a declared fake: old failure evidence must not pollute other controls.
            L._retry_failed_spawns(view)
        return passed and not native.result.complete

_ownership_row('JOB-NATIVE-VIEW-1: failed cleanup registered through a controller NativeView is retried by its underlying witness owner', _native_view_retry220)

def _boot_retirement226():
    spec = importlib.util.spec_from_file_location('probe_sm_retirement226', ROOT / 'research/v3/probe_sm.py')
    probe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = probe
    spec.loader.exec_module(probe)
    for fault in ('none', 'close', 'pump'):
        jobs, api, proc, native, events = _spawn_fixture208()
        proc.alive = False
        native.register(42, handle=700, label='owned-boot-retirement')
        handle = jobs.jobs[42]
        api.job_members[handle] = []
        lease = jobs._job_leases[handle]
        retained = probe.RetainedJob(native, 42)
        pump = _Namespace(error=None, join=lambda timeout: fault != 'pump')
        boot = probe.OwnedBoot(_Namespace(process=proc), pump, retained, native=native,
                              birth_time=12.0, port=43199, dirs=None)
        if fault == 'close':
            api.close_refused.add(handle)
        result = boot.close(None, graceful=False)
        if fault != 'none':
            if result['complete'] is not False or boot.closed or not jobs.active_lease(lease) or handle not in api.live:
                return False
            if fault == 'pump' and handle in api.close_calls:
                return False
            api.close_refused.clear()
            pump.join = lambda timeout: True
            result = boot.close(None, graceful=False)
        if (result['complete'] is not True or not boot.closed or handle in api.live or jobs.jobs
                or jobs._root_leases or jobs._job_leases or jobs._owned_handles or retained.same_binding()):
            return False
        if fault == 'close' and native.stop().complete:
            return False  # Successful later cleanup cannot erase the earlier false CloseHandle.
    return True

_ownership_row('JOB-BOOT-RETIRE-1: successful boot close retires its exact Job; false close and pump failure retain authority until retry', _boot_retirement226)

def _boot_retirement_interrupt228():
    spec = importlib.util.spec_from_file_location('probe_sm_retirement_interrupt228', ROOT / 'research/v3/probe_sm.py')
    probe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = probe
    spec.loader.exec_module(probe)
    jobs, api, proc, native, events = _spawn_fixture208()
    proc.alive = False
    native.register(42, handle=700, label='owned-boot-retirement-interrupt')
    handle = jobs.jobs[42]
    api.job_members[handle] = []
    lease = jobs._job_leases[handle]
    retained = probe.RetainedJob(native, 42)
    boot = probe.OwnedBoot(_Namespace(process=proc), _Namespace(error=None, join=lambda timeout: True),
        retained, native=native, birth_time=12.0, port=43199, dirs=None)
    original_drain = jobs.drain_lease
    jobs.drain_lease = lambda value: (_ for _ in ()).throw(KeyboardInterrupt('declared interrupted retirement'))
    try:
        boot.close(None, graceful=False)
        return False
    except KeyboardInterrupt as error:
        if str(error) != 'declared interrupted retirement':
            return False
    finally:
        jobs.drain_lease = original_drain
    if boot.closed or handle not in api.live or not jobs.active_lease(lease) \
            or not isinstance(boot.stop_result, dict) or boot.stop_result.get('complete') is not False:
        return False
    result = boot.close(None, graceful=False)
    return result['complete'] is True and boot.closed and handle not in api.live and not jobs._owned_handles

_ownership_row('JOB-BOOT-RETIRE-INTERRUPT-1: interrupted retirement cannot publish closed or lose incomplete stop evidence before exact-owner retry', _boot_retirement_interrupt228)

if made:
    try:
        os.rmdir(link) if os.name == "nt" else os.unlink(link)
    except OSError:
        pass
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 launch witness: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
