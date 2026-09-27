#!/usr/bin/env python3
"""PREREG-V3 TB9, step A2.2, on real processes: the native egress witness follows a tree psutil can see.

A child is started through ``launch.spawn`` with the native witness on. It starts a grandchild and exits at once;
the grandchild holds a loopback connection to a server in this test. The witness must:

* keep the grandchild tracked after its parent has exited, and read its connection (rows seen > 0);
* count 0 hits for loopback;
* count exactly the non-loopback row a wrapped sampler adds for the real grandchild's pid - the classification
  path on real pids, with no packet leaving the machine.

And, on real processes (the auditor's W1): an intermediate that starts a detached grandchild and exits at once
leaves an orphan the witness still tracks - through the tree's Job Object on Windows - whose connection is a hit
when no window covers it. The measured cost of one native sample at the default tick is printed and must stay
well under the tick (W5).

Needs psutil (the research extra); the core suite tests/_test_v3_launch_witness.py covers the logic with fakes.

    python tests/research/_test_v3_launch_psutil.py
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))

import _env_guard  # noqa: F401,E402  hermetic like every suite

#: This interpreter, and its base when it is a venv (the bare CI-like interpreter is one): both named exceptions,
#: or B1 (a venv's base must be in the polygon) refuses the test's own interpreter - correctly.
_TEST_EXC = {sys.executable: "the test interpreter"}
if getattr(sys, "_base_executable", sys.executable) != sys.executable:
    _TEST_EXC[sys._base_executable] = "the test interpreter's base"
import psutil  # noqa: E402,F401  the research extra; the self-check skips this suite cleanly without it

_spec = importlib.util.spec_from_file_location("v3_launch_p", ROOT / "research" / "v3" / "launch.py")
L = importlib.util.module_from_spec(_spec)
sys.modules["v3_launch_p"] = L
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


TMP = Path(tempfile.mkdtemp(prefix="nvt3_psutil_"))
C = L.Contract(polygon_root=TMP / "polygon", runs_root=TMP / "polygon" / "runs" / "v3", repo_root=ROOT,
               owner_home=TMP / "owner", secrets_dir=TMP / "secrets", quarantine_root=TMP / "quarantine",
               conservation_root=TMP / "conservation", binary_exceptions=_TEST_EXC,
               system_dirs=(Path(sys.executable).parent,))

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.bind(("127.0.0.1", 0))
srv.listen(4)
PORT = srv.getsockname()[1]
held = []


def _accept():
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        held.append(conn)


threading.Thread(target=_accept, daemon=True).start()

GRANDCHILD = (f"import socket, time; s = socket.create_connection(('127.0.0.1', {PORT})); time.sleep(4); s.close()")
#: The grandchild gets its own stdio: inheriting the child's stdout pipe would keep ``communicate`` waiting for it.
CHILD = ("import subprocess, sys; "
         f"p = subprocess.Popen([sys.executable, '-c', {GRANDCHILD!r}], stdin=subprocess.DEVNULL, "
         "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); print(p.pid, flush=True)")

GC_PID = [None]
SKIPPED: list[str] = []            # named, printed skips (never a silent pass) - the auditor reads them from the log


def wait_until(cond, timeout: float = 10.0, step: float = 0.02) -> bool:
    """Poll until cond() holds or the timeout passes - an event wait, never a fixed sleep (CI 36292260735)."""
    end = time.monotonic() + timeout
    while True:
        try:
            if cond():
                return True
        except Exception:  # noqa: BLE001
            pass
        if time.monotonic() > end:
            return False
        time.sleep(step)


class Inject(L.PsutilSampler):
    """Real rows, plus one synthetic non-loopback row for the real grandchild's pid."""

    def connections(self, pids):
        rows, errors = super().connections(pids)
        if GC_PID[0] in pids:
            rows = rows + [(GC_PID[0], "10.0.0.5", 5000, "192.0.2.10", 443, "ESTABLISHED")]
        return rows, errors


try:
    for label, sampler in (("real", L.PsutilSampler()), ("injected", Inject())):
        nat = L.NativeEgressWitness(sampler=sampler, tick_s=0.2)
        nat.allow_listener(os.getpid(), "the test's own server")      # W7: the loopback server lives in this process
        W = L.Witnesses(C, native=nat, fs=None)
        unit = L.make_unit_dirs(C, "s", "r", "a", f"u-{label}")
        env = L.build_env(C, parent_env=os.environ, unit=unit, path_dirs=[Path(sys.executable).parent], declared={},
                          catcher_url="http://127.0.0.1:47003")
        W.begin_check(f"chk-{label}")
        kid = L.spawn(C, [sys.executable, "-c", CHILD], env=env, cwd=unit.cwd, record={"role": "test"},
                      parent_env=os.environ, catcher_url="http://127.0.0.1:47003", witnesses=W,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = kid.process.communicate(timeout=60)
        GC_PID[0] = int(out.decode().strip() or 0)
        time.sleep(2.0)                                   # the check window's length (its coverage is measured over it)
        if os.name == "nt":                               # ... and the Windows claims wait for their event, not a clock
            wait_until(lambda: GC_PID[0] in nat.tracked
                       and (nat.result.rows_seen > 0 if label == "real" else nat.result.hits >= 1))
        rec = W.end_check(f"chk-{label}")
        n = rec["native"]
        if label == "real":
            check("the child ran and printed its child's pid", kid.process.returncode == 0 and GC_PID[0] > 0,
                  err.decode("utf-8", "replace")[-300:])
            if os.name == "nt":
                check("the grandchild stays tracked after its parent exited", GC_PID[0] in nat.tracked, str(sorted(nat.tracked)))
                check("the witness read the tree's connections (rows seen > 0)", n["rows_seen"] > 0, str(n))
            else:
                SKIPPED.append("the grandchild stays tracked / rows seen > 0 / the injected hit")
                check("G3b: the skip rests on the witness's own declared limit (LIMIT_NO_JOBS is in the record)",
                      L.LIMIT_NO_JOBS in n["limit"], n["limit"])
                print("       SKIP (not Windows - CI 76cb0e9 class D): the child exits before the first tick and its "
                      "grandchild is re-parented to init; with no Job Object that orphan is the witness's declared "
                      f"limit, so the three orphan checks run on Windows only:{L.LIMIT_NO_JOBS}")
            check("a loopback-only tree gives 0 hits, and the check is complete", n["hits"] == 0 and n["complete"], str(n))
        elif os.name == "nt":
            check("a non-loopback row on the real grandchild's pid is exactly one hit",
                  n["hits"] >= 1 and n["hit_remotes"] == ["192.0.2.10:443"], str(n))
        try:
            psutil.Process(GC_PID[0]).wait(timeout=10)
        except psutil.Error:
            pass
    # W1 on real processes: an orphaned grandchild, its parent gone before the first tick
    sampler = L.PsutilSampler()
    nat = L.NativeEgressWitness(sampler=sampler)
    nat.allow_listener(os.getpid(), "the test's own server")
    W = L.Witnesses(C, native=nat, fs=None)
    grand = (f"import socket, time; time.sleep(1.2); s = socket.create_connection(('127.0.0.1', {PORT})); "
             "time.sleep(2.5); s.close()")
    inter = (f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {grand!r}], "
             "creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0), stdin=subprocess.DEVNULL, "
             "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)")
    outer = f"import subprocess, sys, time; subprocess.run([sys.executable, '-c', {inter!r}]); time.sleep(4.5)"
    unit = L.make_unit_dirs(C, "s", "r", "a", "u-orphan")
    env = L.build_env(C, parent_env=os.environ, unit=unit, path_dirs=[Path(sys.executable).parent], declared={},
                      catcher_url="http://127.0.0.1:47003")
    W.begin_check("chk-orphan")
    kid = L.spawn(C, [sys.executable, "-c", outer], env=env, cwd=unit.cwd, record={"role": "test"},
                  parent_env=os.environ, catcher_url="http://127.0.0.1:47003", witnesses=W)
    kid.process.wait(timeout=60)
    rec = W.end_check("chk-orphan")
    n = rec["native"]
    if os.name == "nt":
        check("W1 an orphan whose parent exited at once is still in the tree: its (loopback) connection is read",
              n["rows_seen"] > 0 and n["complete"], str(n))
    else:
        print(f"       (not Windows: orphans re-parented to init are a declared limit: {n['limit'][-80:]})")
    check("W5 the record carries the tick, the expected samples and the declared limit",
          n["tick_s"] == L.DEFAULT_TICK_S and n["expected_samples"] >= 1 and "not seen" in n["limit"], str(n))
    print(f"       measured: one native sample costs {n['sample_cost_ms']:.1f} ms at a {n['tick_s']} s tick "
          f"({n['samples']}/{n['expected_samples']} samples)")
    check("W5 one sample costs well under the tick (< 50 %)", n["sample_cost_ms"] < 500 * n["tick_s"], str(n["sample_cost_ms"]))

    # W7 on real processes: a listener outside the tree (a stand-in for a local HTTP/SOCKS proxy), then allowed,
    # then a listener inside the tree.
    outside = subprocess.Popen([sys.executable, "-c", "import socket, sys, time; s = socket.socket(); "
                                "s.bind(('127.0.0.1', 0)); s.listen(4); print(s.getsockname()[1], flush=True); "
                                "c = [s.accept() for _ in range(2)]; time.sleep(6)"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    OUT_PORT = int(outside.stdout.readline().decode().strip())
    HOLD = f"import socket, time; s = socket.create_connection(('127.0.0.1', {OUT_PORT})); time.sleep(2.5); s.close()"
    for label, allow in (("w7-outside", False), ("w7-allowed", True)):
        nat = L.NativeEgressWitness(sampler=L.PsutilSampler(), tick_s=0.2)
        if allow:
            nat.allow_listener(outside.pid, "stand-in for the v3 proxy")
        W = L.Witnesses(C, native=nat, fs=None)
        unit = L.make_unit_dirs(C, "s", "r", "a", f"u-{label}")
        env = L.build_env(C, parent_env=os.environ, unit=unit, path_dirs=[Path(sys.executable).parent], declared={},
                          catcher_url="http://127.0.0.1:47003")
        W.begin_check(f"chk-{label}")
        kid = L.spawn(C, [sys.executable, "-c", HOLD], env=env, cwd=unit.cwd, record={"role": "test"},
                      parent_env=os.environ, catcher_url="http://127.0.0.1:47003", witnesses=W)
        kid.process.wait(timeout=60)
        n = W.end_check(f"chk-{label}")["native"]
        if allow:
            check("W7 the same connection to an allowed listener (the proxy's role) is no hit, check complete",
                  n["hits"] == 0 and n["loopback_hits"] == 0 and n["complete"], str(n))
        else:
            check("W7 a real child's loopback connection to a listener outside its tree is a hit",
                  n["loopback_hits"] >= 1 and f"127.0.0.1:{OUT_PORT}" in n["hit_remotes"] and n["failed_samples"] == 0,
                  str(n))
    outside.kill()
    SELF = ("import socket, threading, time; s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(1); "
            "threading.Thread(target=s.accept, daemon=True).start(); "
            "c = socket.create_connection(s.getsockname()); time.sleep(2.5); c.close()")
    nat = L.NativeEgressWitness(sampler=L.PsutilSampler(), tick_s=0.2)
    W = L.Witnesses(C, native=nat, fs=None)
    unit = L.make_unit_dirs(C, "s", "r", "a", "u-w7-self")
    env = L.build_env(C, parent_env=os.environ, unit=unit, path_dirs=[Path(sys.executable).parent], declared={},
                      catcher_url="http://127.0.0.1:47003")
    W.begin_check("chk-w7-self")
    kid = L.spawn(C, [sys.executable, "-c", SELF], env=env, cwd=unit.cwd, record={"role": "test"},
                  parent_env=os.environ, catcher_url="http://127.0.0.1:47003", witnesses=W)
    kid.process.wait(timeout=60)
    n = W.end_check("chk-w7-self")["native"]
    check("W7 a real child talking to its own listener is no hit, and its accepted side is not undetermined",
          n["hits"] == 0 and n["undetermined_loopback"] == 0 and n["rows_seen"] > 0 and n["complete"], str(n))
finally:
    srv.close()
    for c in held:
        c.close()
    shutil.rmtree(TMP, ignore_errors=True)

check("G3: on Windows nothing is skipped - the orphan checks run where they matter", os.name != "nt" or not SKIPPED,
      str(SKIPPED))
print(f"\nv3 launch psutil: {PASSED} passed, {FAILED} failed, {len(SKIPPED)} skipped by name")
sys.exit(1 if FAILED else 0)
