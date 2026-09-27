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
check("py314 is in the watched idle set of the machine's contract", "py314" in L.Contract.default().polygon_idle
      and any(s.label == "polygon_py314" for s in L.watched_set(L.Contract.default())))
check("the auditor's replay tooling (py310, core_bare310, ci_linux) is in the watched idle set too",
      {"py310", "core_bare310", "ci_linux"} <= set(L.Contract.default().polygon_idle)
      and {"polygon_py310", "polygon_core_bare310", "polygon_ci_linux"}
      <= {s.label for s in L.watched_set(L.Contract.default())})

if made:
    try:
        os.rmdir(link) if os.name == "nt" else os.unlink(link)
    except OSError:
        pass
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 launch witness: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
