#!/usr/bin/env python3
"""A6 ruling B11: research/v3/launch._append_jsonl under concurrent writers.

The hash-chained logs (spawns.jsonl, the fetch-window log) read the last line and then append a record whose "prev" is
that line's sha256. Two writers that read the same last line chain two records to it, and a Windows "ab" append is not
atomic between writers either (the class found 2026-09-27 in the proxy). The ruling puts the lock in the WRITER itself:
file_lock, an inter-process lock on <log>.lock that also excludes threads.

* processes: PROCS children, released together through a go-file barrier, each append PER records;
* threads: THREADS threads behind a threading.Barrier, each append PER_T records;
* each run: the exact line count, every line JSON, the prev chain intact from the first line, and every (writer, i)
  record present exactly once;
* file_lock gives up with TimeoutError past its timeout and never appends without the lock;
* B12: research/_idle_probe.append_chained is the same writer - concurrent probe writers keep every line and the chain,
  and its lines are byte for byte launch._append_jsonl's.

    python tests/research/_test_v3_append_lock.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import
LAUNCH = ROOT / "research" / "v3" / "launch.py"
_spec = importlib.util.spec_from_file_location("v3_launch_lock", LAUNCH)
L = importlib.util.module_from_spec(_spec)
sys.modules["v3_launch_lock"] = L   # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(L)

PROCS, PER = 6, 40
THREADS, PER_T = 8, 40
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def audit(path: Path) -> tuple[int, list[str], list[tuple]]:
    """(line count, chain problems, the (w, i) keys in file order)."""
    raw = path.read_bytes() if path.exists() else b""
    lines = raw.split(b"\n")
    problems = [] if raw.endswith(b"\n") or not raw else ["the file does not end with a newline"]
    body = lines[:-1] if raw.endswith(b"\n") else lines
    keys, prev = [], None
    for n, ln in enumerate(body, 1):
        try:
            rec = json.loads(ln)
        except ValueError:
            problems.append(f"line {n} is not JSON")
            prev = ln
            continue
        want = hashlib.sha256(prev).hexdigest() if prev is not None else "0" * 64
        if rec.get("prev") != want:
            problems.append(f"line {n} prev does not hash line {n - 1}")
        keys.append((rec.get("w"), rec.get("i")))
        prev = ln
    return len(body), problems, keys


CHILD = r"""
import importlib.util, sys, time
from pathlib import Path
launch, log, go, ready, w, per = sys.argv[1:7]
spec = importlib.util.spec_from_file_location("v3_launch_child", launch)
L = importlib.util.module_from_spec(spec)
sys.modules["v3_launch_child"] = L
spec.loader.exec_module(L)
Path(ready).write_text("1")
deadline = time.monotonic() + 60
while not Path(go).exists():
    if time.monotonic() > deadline:
        sys.exit("no go")
    time.sleep(0.001)
for i in range(int(per)):
    L._append_jsonl(Path(log), {"w": w, "i": i})
"""

print("\n- processes behind a barrier -")
with tempfile.TemporaryDirectory(prefix="v3lock_") as td:
    d = Path(td)
    log, go = d / "runs" / "spawns.jsonl", d / "go"
    kids = [subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(LAUNCH), str(log), str(go), str(d / f"ready{k}"),
                              f"p{k}", str(PER)], cwd=td, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for k in range(PROCS)]
    t0 = time.monotonic()
    while not all((d / f"ready{k}").exists() for k in range(PROCS)) and time.monotonic() - t0 < 120:
        time.sleep(0.01)
    go.write_text("go")
    rcs = [k.wait(timeout=300) for k in kids]
    errs = [k.stderr.read().decode("utf-8", "replace")[-300:] for k in kids if k.returncode]
    for k in kids:
        k.stdout.close()
        k.stderr.close()
    n, problems, keys = audit(log)
    check(f"every child exits 0 ({PROCS} processes)", rcs == [0] * PROCS, str(errs[:1]))
    check(f"the exact line count: {PROCS} x {PER} = {PROCS * PER}", n == PROCS * PER, f"{n} lines")
    check("the prev chain is intact from the first line, every line JSON", not problems, str(problems[:3]))
    check("every (writer, i) record is present exactly once",
          sorted(keys) == sorted((f"p{k}", i) for k in range(PROCS) for i in range(PER)), f"{len(set(keys))} distinct")

print("\n- threads behind a barrier -")
with tempfile.TemporaryDirectory(prefix="v3lock_t_") as td:
    log = Path(td) / "spawns.jsonl"
    bar = threading.Barrier(THREADS)
    errors: list[str] = []

    def writer(w: str) -> None:
        try:
            bar.wait(timeout=30)
            for i in range(PER_T):
                L._append_jsonl(log, {"w": w, "i": i})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{w}: {e!r}")

    ts = [threading.Thread(target=writer, args=(f"t{k}",)) for k in range(THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=300)
    n, problems, keys = audit(log)
    check(f"no writer thread raised ({THREADS} threads)", not errors, str(errors[:2]))
    check(f"the exact line count: {THREADS} x {PER_T} = {THREADS * PER_T}", n == THREADS * PER_T, f"{n} lines")
    check("the prev chain is intact from the first line (threads)", not problems, str(problems[:3]))
    check("every (thread, i) record is present exactly once",
          sorted(keys) == sorted((f"t{k}", i) for k in range(THREADS) for i in range(PER_T)), f"{len(set(keys))} distinct")

print("\n- the lock itself -")
with tempfile.TemporaryDirectory(prefix="v3lock_l_") as td:
    log = Path(td) / "spawns.jsonl"
    got: list[str] = []

    def contender() -> None:
        try:
            with L.file_lock(log, timeout_s=0.3):
                got.append("acquired")
        except TimeoutError:
            got.append("timeout")
        except Exception as e:  # noqa: BLE001
            got.append(repr(e))

    held_then, raised = None, None
    try:
        with L.file_lock(log):
            t = threading.Thread(target=contender)
            t.start()
            t.join(timeout=10)
            held_then = list(got)
        t2 = threading.Thread(target=contender)
        t2.start()
        t2.join(timeout=10)
    except Exception as e:  # noqa: BLE001  - a missing or broken lock is a named FAIL, not a crash
        raised = repr(e)
    check("while one holder has the lock a second (another descriptor, another thread) times out",
          raised is None and held_then == ["timeout"], str((held_then, raised)))
    check("after release the lock is acquired", raised is None and got[-1:] == ["acquired"], str((got, raised)))

print("\n- B12: the idle probe's chained log is the same writer -")
CHILD_PROBE = r"""
import importlib.util, sys, time
from pathlib import Path
probe, log, go, ready, w, per = sys.argv[1:7]
spec = importlib.util.spec_from_file_location("v3_idle_probe_child", probe)
I = importlib.util.module_from_spec(spec)
sys.modules["v3_idle_probe_child"] = I
spec.loader.exec_module(I)
Path(ready).write_text("1")
deadline = time.monotonic() + 60
while not Path(go).exists():
    if time.monotonic() > deadline:
        sys.exit("no go")
    time.sleep(0.001)
for i in range(int(per)):
    I.append_chained(Path(log), {"w": w, "i": i})
"""
PROBE = ROOT / "research" / "_idle_probe.py"
with tempfile.TemporaryDirectory(prefix="v3lock_p_") as td:
    d = Path(td)
    log, go = d / "_idle" / "calibrations.jsonl", d / "go"
    kids = [subprocess.Popen([sys.executable, "-B", "-c", CHILD_PROBE, str(PROBE), str(log), str(go), str(d / f"ready{k}"),
                              f"p{k}", str(PER)], cwd=td, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for k in range(PROCS)]
    t0 = time.monotonic()
    while not all((d / f"ready{k}").exists() for k in range(PROCS)) and time.monotonic() - t0 < 120:
        time.sleep(0.01)
    go.write_text("go")
    rcs = [k.wait(timeout=300) for k in kids]
    errs = [k.stderr.read().decode("utf-8", "replace")[-300:] for k in kids if k.returncode]
    for k in kids:
        k.stdout.close()
        k.stderr.close()
    n, problems, keys = audit(log)
    check(f"B12: {PROCS} probe writers exit 0", rcs == [0] * PROCS, str(errs[:1]))
    check(f"B12: the probe's log keeps every line ({PROCS * PER}) and its prev chain",
          n == PROCS * PER and not problems, f"{n} lines, {problems[:2]}")
    check("B12: every (writer, i) record of the probe's log is present exactly once",
          sorted(keys) == sorted((f"p{k}", i) for k in range(PROCS) for i in range(PER)))
with tempfile.TemporaryDirectory(prefix="v3lock_b_") as td:
    _pspec = importlib.util.spec_from_file_location("v3_idle_probe_lock", PROBE)
    I = importlib.util.module_from_spec(_pspec)
    sys.modules["v3_idle_probe_lock"] = I
    _pspec.loader.exec_module(I)
    a, b = Path(td) / "a.jsonl", Path(td) / "b.jsonl"
    for rec in ({"run": "c1", "x": "é"}, {"run": "c2"}):
        I.append_chained(a, rec)
        L._append_jsonl(b, rec)
    check("B12: the probe's lines are byte for byte launch._append_jsonl's", a.read_bytes() == b.read_bytes()
          and I.chain_ok(a))

print(f"\nv3 append lock: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
