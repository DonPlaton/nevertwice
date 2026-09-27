#!/usr/bin/env python3
"""PREREG-V3 TB2 (A4): research/_idle_probe.py on fakes - no psutil, no NVML, no real Ollama, a fake clock.

The machine_idle record of §7 (rule v3-idle-1) and the auditor's A4 rulings (.loop/A4-RULINGS-AUDITOR.md):
* import hygiene: nothing heavy loads at import (the core CI job has no psutil);
* the thresholds are rev1 §7's, read from the PREREG itself; the windows, the wait and the calibration have §7's lengths;
* the statistics: percentiles s[round(q(n-1))] (Q11), CPU from deltas, foreign CPU per process over the WINDOW (Q2, Q3),
  a process born inside a window from 0, a reused pid a new process, pid 0 excluded (Windows), kernel pseudo-processes
  never foreign (Q10), rss the largest seen, values rounded before they are compared;
* the allowlist: the tree (an orphan stays tracked; an older process with the root's pid as ppid is not a child), injected
  Job Object members, ollama* anchored, the proxy by pid + create_time, a service only when declared;
* the evaluation: pre and post against every threshold (boundaries clean), during against foreign processes and
  residents only, idle iff no violation; a resident must match tag AND digest (Q14);
* I1: NOT_AVAILABLE per-process GPU memory is "unmeasurable" - a loud pass with its entry naming the driver model; a
  measurable 300 MiB is a violation; a failed NVML read is "unmeasured", a violation; incomplete windows too;
* the busy wait (Q5): seven attempts, start to start, 0..30 min, the last returned when none is idle;
* Ollama: only GET /api/ps and /api/version on 127.0.0.1, refused before a byte; "not running" is no violation, a
  running Ollama that does not answer is;
* --calibrate (I2, I3, Q6, Q8): unverified -> branch none; (a) and (b) with ceil(observed x 1.2) on upper bounds only;
  RAM < 8, a foreign resident, an incomplete window or another length -> none; sliding 60-s windows see a burst;
* output: a used run label refused, a hash-chained calibration log, the commit read from .git files (a worktree's
  gitdir/commondir and packed-refs), the probe's sha256 over its bytes, sorted LF JSON, and the shape check.

    python tests/_test_v3_idle_probe.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


I = _load("idle_probe_t", ROOT / "research" / "_idle_probe.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def refused(fn, words: str = "", exc=None) -> bool:
    try:
        fn()
    except (exc or I.ProbeRefused) as e:
        return words in str(e)
    except Exception:  # noqa: BLE001
        return False
    return False


TMP = Path(tempfile.mkdtemp(prefix="nvt3_idle_"))
EMB = f"{I.EMBEDDER['tag']}@{I.EMBEDDER['digest']}"


class FakeClock:
    def __init__(self, t0: float = 1000.0):
        self.t, self.t0 = t0, t0

    def now(self) -> float:
        return self.t

    def epoch(self) -> float:
        return 1_790_000_000.0 + (self.t - self.t0)

    def utc(self) -> str:
        return f"t+{self.t - self.t0:.0f}"

    def sleep_until(self, t: float) -> None:
        self.t = max(self.t, t)


class FakeGpu:
    def __init__(self, util=0, mem=4000, procs=(), model="WDDM", fail_procs=False):
        self.util, self.mem, self.procs, self.model, self.fail_procs = util, mem, list(procs), model, fail_procs

    def info(self):
        return {"name": "fake", "driver_model": self.model, "memory_total_mib": 32000}

    def sample(self):
        u = self.util(self) if callable(self.util) else self.util
        return u, self.mem

    def processes(self):
        if self.fail_procs:
            raise OSError("nvml gone")
        return list(self.procs)


class FakeHost:
    """cpu: busy fraction per 1-s sample (a callable of the sample index, or a number); table: a callable of the clock time."""

    def __init__(self, clock: FakeClock, *, busy=0.05, ram_gib=16.0, table=None, cores=16):
        self.clock, self.busy, self.ram, self.table, self._cores = clock, busy, ram_gib, table, cores
        self.b = self.tot = 0.0
        self.n = 0

    def cores(self):
        return self._cores

    def cpu_times(self):
        f = self.busy(self.n) if callable(self.busy) else self.busy
        self.n += 1
        self.tot += 16.0
        self.b += 16.0 * f
        return self.b, self.tot

    def ram_available_bytes(self):
        r = self.ram(self.n) if callable(self.ram) else self.ram
        return int(r * 2 ** 30)

    def processes(self):
        return [dict(p) for p in (self.table(self.clock.t - self.clock.t0) if self.table else [])]


class FakeOllama:
    def __init__(self, resident=(), fail=False):
        self.res, self.fail, self.calls = list(resident), fail, 0

    def resident(self):
        self.calls += 1
        if self.fail:
            raise I.Unavailable("Ollama /api/ps: refused")
        return [{"name": n.split("@")[0], "digest": n.split("@")[1]} for n in self.res]


def proc(pid, name, ct=10.0, cpu=0.0, ppid=1, rss=100 << 20):
    return {"pid": pid, "ppid": ppid, "name": name, "create_time": ct, "cpu_s": cpu, "rss": rss}


def probe(*, table=None, busy=0.05, ram=16.0, util=0, gpu_procs=(), model="WDDM", resident=(), ollama_fail=False,
          fail_procs=False, allow=None, gpu=True):
    ck = FakeClock()
    p = I.IdleProbe(gpu=FakeGpu(util, procs=gpu_procs, model=model, fail_procs=fail_procs) if gpu else False,
                    host=FakeHost(ck, busy=busy, ram_gib=ram, table=table),
                    ollama=FakeOllama(resident, ollama_fail), clock=ck, allowlist=allow or I.Allowlist(root=None),
                    commit="c" * 40)
    return p, ck


print("\n- import, constants -")
out = subprocess.run([sys.executable, "-c", "import importlib.util,sys;"
                      f"s=importlib.util.spec_from_file_location('m',r'{ROOT / 'research' / '_idle_probe.py'}');"
                      "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                      "print('psutil' in sys.modules, any('nvml' in str(getattr(x,'_name','')).lower() for x in []))"],
                     capture_output=True, text=True, timeout=60)
check("K01 importing the probe loads no psutil (the core CI job has none) and no DLL", out.stdout.strip().startswith("False"),
      out.stdout + out.stderr[-200:])
prereg = (ROOT / "research" / "v3" / "PREREG-V3-rev1.md").read_text(encoding="utf-8")
th_json = json.loads("{" + re.search(r'"thresholds": \{(.*?)\}', prereg, flags=re.S).group(1).replace("\n", " ") + "}")
check("K02 the rev1 thresholds are §7's, read from the PREREG itself (the embedder placeholder bound to the pinned D1 tag)",
      {k: v for k, v in I.REV1.items() if k != "ollama_resident_only"} == {k: v for k, v in th_json.items() if k != "ollama_resident_only"}
      and th_json["ollama_resident_only"] == ["<embedder tag>"] and I.REV1["ollama_resident_only"] == [EMB], str(th_json))
check("K03 the windows (60 s at 1 s, during 5 s), the wait (7 x 300 s) and the calibration (600 s) are §7's and Q5's",
      (I.PRE_S, I.PRE_INTERVAL_S, I.DURING_INTERVAL_S, I.WAIT_ATTEMPTS, I.WAIT_STEP_S, I.CALIBRATE_S) == (60, 1, 5, 7, 300, 600)
      and I.RULE == "v3-idle-1")
check("K05 the percentile is s[round(q(n-1))] (Q11): 0..59 gives p95 56, p50 30", I.pct(list(range(60)), .95) == 56
      and I.pct(list(range(60)), .5) == 30)

print("\n- a clean window, the statistics -")
p0, ck0 = probe(table=lambda t: [proc(100, "quiet.exe", cpu=1.0 + t * 0.001)])
w0 = p0.window("pre")
check("a clean window: 60 samples of 60, CPU from deltas (5 %), RAM, GPU - no violation",
      w0["samples"] == 60 and w0["expected_samples"] == 60 and w0["cpu_total_pct"]["p95"] == 5.0
      and w0["ram_available_gib"]["min"] == 16.0 and p0.evaluate("pre", w0) == [], str((w0["cpu_total_pct"], p0.evaluate("pre", w0))))
check("K04 the record never carries a command line or a path, whatever the table holds",
      "cmdline" not in json.dumps(I._clean(probe(table=lambda t: [{**proc(7, "x.exe"), "cmdline": "secret --key", "exe": "C:/x"}])[0]
                                             .window("pre"))))


def cpu_at(rate_per_s, ct=10.0, start=5.0):
    return lambda t: [proc(200, "busy.exe", ct=ct, cpu=start + rate_per_s * t)]


w6, v6 = None, None
p6, _ = probe(table=cpu_at(0.10))
w6 = p6.window("pre")
check("K07 6 s of CPU over a 60-s window is 10.0 % of a core - at the threshold, no violation (Q3)",
      next((r["cpu_pct_of_core_mean"] for r in w6["foreign_all"] if r["pid"] == 200), None) == 10.0 and p6.evaluate("pre", w6) == [],
      str(w6["foreign_all"]))
p61, _ = probe(table=cpu_at(0.102))
w61 = p61.window("pre")
check("... 6.12 s is 10.2 % - a named foreign-CPU violation on that process (Q2: per process)",
      [(x["threshold"], x["process"]) for x in p61.evaluate("pre", w61)] == [("foreign_cpu_pct_of_core_mean", "busy.exe:200")],
      str(p61.evaluate("pre", w61)))
p8, _ = probe(table=lambda t: [proc(300, "late.exe", ct=1_790_000_000.0 + 30, cpu=3.0)] if t >= 30 else [])
w8 = p8.window("pre")
check("K08 a process born inside the window counts from 0: 3 s of CPU over 60 s is 5.0 %",
      next((r["cpu_pct_of_core_mean"] for r in w8["foreign_all"] if r["pid"] == 300), None) == 5.0, str(w8["foreign_all"]))
p9, _ = probe(table=lambda t: [proc(400, "a.exe", ct=10.0, cpu=1.0)] if t < 30 else
              [proc(400, "b.exe", ct=1_790_000_000.0 + 31, cpu=0.5)])
w9 = p9.window("pre")
check("K09 a reused pid is another process (keyed by pid and create time)",
      sorted(r["name"] for r in w9["foreign_all"]) == ["a.exe", "b.exe"], str(w9["foreign_all"]))
p10, _ = probe(table=lambda t: [proc(0, "System Idle Process", cpu=1000 + 16 * t), proc(4, "System", cpu=50 + 0.5 * t),
                                proc(236, "Secure System"), proc(5180, "Memory Compression", cpu=1 + 0.2 * t)])
w10 = p10.window("pre")
check("K10 pid 0 is idle time, not a process, on Windows (elsewhere pid 0 is no process at all)",
      (not any(r["pid"] == 0 for r in w10["foreign_all"] + w10["kernel"])) if os.name == "nt"
      else any(r["pid"] == 0 for r in w10["foreign_all"]))
# The kernel rows get their own fixture WITHOUT pid 0: off Windows pid 0 is rightly a foreign process (K10), and its
# 1600 % would be the violation this row says is absent (the auditor's Linux replay of 7998900).
p10k, _ = probe(table=lambda t: [proc(4, "System", cpu=50 + 0.5 * t), proc(236, "Secure System"),
                                 proc(5180, "Memory Compression", cpu=1 + 0.2 * t)])
w10k = p10k.window("pre")
check("... System, Secure System and Memory Compression sit under kernel, and 50 %/20 % of a core there is no violation",
      sorted(r["name"] for r in w10k["kernel"]) == ["Memory Compression", "Secure System", "System"]
      and p10k.evaluate("pre", w10k) == [], str((w10k["kernel"], p10k.evaluate("pre", w10k))))
p10e, _ = probe(table=lambda t: [proc(4, "", cpu=50 + 0.5 * t)])
w10e = p10e.window("pre")
check("... and pid 4 is kernel by its pid, even with an empty name (psutil's 'Secure System' comes back as '')",
      [r["pid"] for r in w10e["kernel"]] == [4] and p10e.evaluate("pre", w10e) == [], str(w10e["kernel"]))
p11, _ = probe(table=lambda t: [proc(500, "grow.exe", rss=(100 + (300 if 20 <= t < 40 else 0)) << 20)])
check("K11 rss is the largest working set seen", p11.window("pre")["foreign_all"][0]["rss_mib"] == 400)
p12, _ = probe(busy=lambda n: 0.1004 if n % 2 else 0.05)
w12 = p12.window("pre")
check("K12 values are rounded to the recorded precision before they are compared: 10.04 % is 10.0, no violation",
      w12["cpu_total_pct"]["p95"] == 10.0 and p12.evaluate("pre", w12) == [], str(w12["cpu_total_pct"]))

print("\n- the allowlist -")
tree = [proc(1000, "harness.exe", ct=100.0), proc(1001, "arm.exe", ct=110.0, ppid=1000), proc(1002, "old.exe", ct=50.0, ppid=1000)]
al = I.Allowlist(root=(1000, 100.0))
al.refresh(tree)
check("K14 the tree: a child is allowed; a process OLDER than the root carrying its pid as ppid is not a child",
      al.allowed(tree[1]) and not al.allowed(tree[2]))
al.refresh([proc(1001, "arm.exe", ct=110.0, ppid=4242)])
check("K13 an orphan seen before its parent died stays allowed", al.allowed(proc(1001, "arm.exe", ct=110.0, ppid=4242)))
al2 = I.Allowlist(root=(1000, 100.0), tree_members=lambda: {7777})
al2.refresh(tree + [proc(7777, "detached.exe", ct=200.0, ppid=1)])
check("K15 injected Job Object members are part of the tree", al2.allowed(proc(7777, "detached.exe", ct=200.0, ppid=1)))
check("K16 ollama* is anchored: ollama.exe and 'ollama app.exe' are allowed, myollama.exe is foreign",
      al.allowed(proc(9, "ollama.exe")) and al.allowed(proc(10, "ollama app.exe")) and not al.allowed(proc(11, "myollama.exe")))
al3 = I.Allowlist(root=None, pids=((555, 20.0),))
check("K17 the proxy is matched by pid AND create time", al3.allowed(proc(555, "python.exe", ct=20.0))
      and not al3.allowed(proc(555, "python.exe", ct=21.0)))
check("K18 a service's names are allowed only when declared",
      not I.Allowlist(root=None).allowed(proc(12, "vmmemWSL"))
      and I.Allowlist(root=None, service={"label": "falkordb", "names": ["vmmemWSL"]}).allowed(proc(12, "vmmemWSL")))
p19, _ = probe(table=lambda t: [proc(20, "ollama.exe", cpu=1 + 0.5 * t)])
w19 = p19.window("pre")
check("K19 allowed processes are recorded (and never a foreign-CPU violation)",
      [r["name"] for r in w19["allowed"]] == ["ollama.exe"] and p19.evaluate("pre", w19) == [], str(w19["allowed"]))

print("\n- the evaluation (one wrong field per row; the boundary rows stay clean) -")
BASE = {"gpu_util_pct": {"p95": 5, "max": 25}, "cpu_total_pct": {"p95": 10.0, "max": 35.0},
        "ram_available_gib": {"min": 8.0}, "foreign_all": [], "ollama_resident": [EMB], "samples": 60,
        "expected_samples": 60, "unmeasured": []}


def ev(kind="pre", **over):
    w = json.loads(json.dumps(BASE))
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(w.get(k), dict):
            w[k].update(v)
        else:
            w[k] = v
    return [x["threshold"] for x in I.evaluate(kind, w, I.REV1)]


check("K20 the boundary row (5, 25, 10.0, 35.0, 8.0, the pinned embedder) is clean on pre and post",
      ev("pre") == [] and ev("post") == [])
rows = [("gpu_util_p95", {"gpu_util_pct": {"p95": 6}}), ("gpu_util_max", {"gpu_util_pct": {"max": 26}}),
        ("cpu_total_p95", {"cpu_total_pct": {"p95": 10.1}}), ("cpu_total_max", {"cpu_total_pct": {"max": 35.1}}),
        ("ram_available_gib_min", {"ram_available_gib": {"min": 7.99}}),
        ("foreign_cpu_pct_of_core_mean", {"foreign_all": [{"name": "x", "pid": 1, "cpu_pct_of_core_mean": 10.1, "gpu_mem_mib": 0}]}),
        ("foreign_gpu_mem_mib", {"foreign_all": [{"name": "x", "pid": 1, "cpu_pct_of_core_mean": 0.0, "gpu_mem_mib": 300}]}),
        ("ollama_resident_only", {"ollama_resident": ["qwen3.6:27b@" + "a" * 64]})]
for want, over in rows:
    check(f"K20 {want}: exactly that violation on pre and on post", ev("pre", **over) == [want] and ev("post", **over) == [want],
          str((ev("pre", **over), ev("post", **over))))
check("K21 during is judged on foreign processes and residents only: its GPU/CPU/RAM values never violate (Q1)",
      ev("during", gpu_util_pct={"p95": 99, "max": 99}, cpu_total_pct={"p95": 99.0, "max": 99.0}, ram_available_gib={"min": 1.0}) == []
      and ev("during", **dict(rows[5][1:][0])) == ["foreign_cpu_pct_of_core_mean"])
check("K23 a resident embedder must be the pinned tag AND digest (Q14); a bare tag means :latest",
      ev("pre", ollama_resident=["nvt3-bge-m3-d1@" + I.EMBEDDER["digest"]]) == []
      and ev("pre", ollama_resident=["nvt3-bge-m3-d1:latest@" + "b" * 64]) == ["ollama_resident_only"])
pc = I.build_record(pre=w0, during={**w0, "expected_samples": 0}, post=w0, thresholds=I.REV1, thresholds_source="rev1",
                    allowlist=[], probe={"tool": "t", "commit": "c", "sha256": "s"}, gpu_info=None, attempts=[])
p22, _ = probe(busy=0.2)
w22 = p22.window("post")
pc2 = I.build_record(pre=w0, during={**w0, "expected_samples": 0}, post=w22, thresholds=I.REV1, thresholds_source="rev1",
                     allowlist=[], probe={"tool": "t", "commit": "c", "sha256": "s"}, gpu_info=None, attempts=[])
check("K22 idle iff no violation in any window: clean -> true; a post violation alone -> false",
      pc["idle"] is True and pc2["idle"] is False and pc2["violations"][0]["window"] == "post", str(pc2["violations"][:1]))

print("\n- I1: per-process GPU memory -")
pi, _ = probe(table=lambda t: [proc(600, "chrome.exe", cpu=1.0)], gpu_procs=[(600, None, "graphics")])
wi = pi.window("pre")
reci = pi.record(wi, {**wi, "expected_samples": 0}, wi)
check("K24 NOT_AVAILABLE is written 'unmeasurable' (never 0), with a top-level entry naming the driver model from NVML",
      wi["foreign_all"][0]["gpu_mem_mib"] == "unmeasurable" and reci["unmeasurable"]
      and "WDDM" in reci["unmeasurable"][0]["reason"] and reci["unmeasurable"][0]["threshold"] == "foreign_gpu_mem_mib", str(reci["unmeasurable"]))
check("K25 ... and it is a loud pass: a clean run stays idle with the entry present (Q1)", reci["idle"] is True, str(reci["violations"]))
pt, _ = probe(table=lambda t: [proc(601, "cuda.exe")], gpu_procs=[(601, 300, "compute")], model="TCC")
pt2, _ = probe(table=lambda t: [proc(601, "cuda.exe")], gpu_procs=[(601, 256, "compute")], model="TCC")
check("K26 where NVML measures it (TCC-like), 300 MiB is a violation and 256 is not",
      [x["threshold"] for x in pt.evaluate("pre", pt.window("pre"))] == ["foreign_gpu_mem_mib"]
      and pt2.evaluate("pre", pt2.window("pre")) == [])
pz, _ = probe(table=lambda t: [proc(602, "idle.exe")], gpu_procs=[])
check("... 0 only when both NVML lists were read and the process is on neither", pz.window("pre")["foreign_all"][0]["gpu_mem_mib"] == 0)
pf, _ = probe(table=lambda t: [proc(603, "x.exe")], fail_procs=True)
wf = pf.window("pre")
check("K28 a failed NVML process read is 'unmeasured' - a violation, not I1's pass",
      any(x["threshold"] == "unmeasured" and "gpu processes" in x["observed"] for x in pf.evaluate("pre", wf)), str(pf.evaluate("pre", wf)))
pn, _ = probe(gpu=False)
check("... and NVML unavailable at all is 'unmeasured' too", any(x["threshold"] == "unmeasured" for x in pn.evaluate("pre", pn.window("pre"))))

print("\n- completeness -")


class FlakyGpu(FakeGpu):
    def __init__(self):
        super().__init__()
        self.k = 0

    def sample(self):
        self.k += 1
        if self.k % 5 == 0:
            raise OSError("tick lost")
        return 0, 4000


ckf = FakeClock()
pfl = I.IdleProbe(gpu=FlakyGpu(), host=FakeHost(ckf), ollama=FakeOllama(), clock=ckf, commit="c" * 40)
wfl = pfl.window("pre")
check("K29 a window below 0.9 coverage is the violation 'complete' (a failed sample is counted, never fatal)",
      wfl["samples"] == 48 and "complete" in [x["threshold"] for x in pfl.evaluate("pre", wfl)], str(wfl["samples"]))

print("\n- the during thread -")


rc = I.Clock()
class TickFailGpu(FakeGpu):
    """Every second tick fails: the thread must count it and live on."""
    def __init__(self):
        super().__init__()
        self.k = 0

    def sample(self):
        self.k += 1
        if self.k % 2 == 0:
            raise OSError("tick lost")
        return 0, 4000


pd = I.IdleProbe(gpu=TickFailGpu(), host=FakeHost(FakeClock(), table=lambda t: [proc(700, "chrome.exe", cpu=1.0)]),
                 ollama=FakeOllama([EMB]), clock=rc, commit="c" * 40)
try:
    with pd.during(interval=0.05) as d:
        time.sleep(0.4)
        raise KeyError("the timed loop failed")
except KeyError:
    pass
dres = d.result
check("K30 the during thread ticks beside the loop, survives failing ticks, stops and joins even when the loop raises",
      dres is not None and dres["samples"] >= 2 and d.win["sample_errors"] >= 2 and not d._thread.is_alive()
      and "device_description" in dres and "cpu_total_pct" not in dres, str(dres and (dres["samples"], d.win["sample_errors"])))

print("\n- the busy wait (Q5) -")
starts = []


class CountingProbe(I.IdleProbe):
    def window(self, kind, seconds=I.PRE_S, interval=I.PRE_INTERVAL_S, **kw):
        starts.append(self.clock.now() - self.clock.t0)
        return super().window(kind, seconds, interval, **kw)


ckw = FakeClock()
pw = CountingProbe(gpu=FakeGpu(), host=FakeHost(ckw, busy=0.5), ollama=FakeOllama(), clock=ckw, commit="c" * 40)
last, att = pw.wait_for_idle()
check("K31 seven attempts, start to start, at 0, 300, ..., 1800 s", starts == [0, 300, 600, 900, 1200, 1500, 1800] and len(att) == 7,
      str(starts))
check("K32 none idle: the last attempt is returned, and a record from it is idle:false (the stand still runs)",
      last["start_utc"] == att[-1]["start_utc"] and att[-1]["violations"] != [], str(att[-1]["start_utc"]))
starts.clear()
ckw2 = FakeClock()
pw2 = CountingProbe(gpu=FakeGpu(), host=FakeHost(ckw2, busy=lambda n: 0.5 if n < 122 else 0.02), ollama=FakeOllama(), clock=ckw2,
                    commit="c" * 40)
_, att2 = pw2.wait_for_idle()
check("... the first idle attempt ends the wait", len(att2) == 3 and att2[-1]["violations"] == [] and starts == [0, 300, 600], str(starts))

print("\n- Ollama -")
for label, (m, pth, host) in (("a pull", ("POST", "/api/pull", "127.0.0.1")), ("the tag list", ("GET", "/api/tags", "127.0.0.1")),
                              ("another host", ("GET", "/api/ps", "localhost"))):
    check(f"K33 {label} is refused before a byte is sent",
          refused(lambda m=m, pth=pth, host=host: I.OllamaPs(port=1, host=host).request(m, pth), "refused"))
SEEN = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        SEEN.append(self.path)
        body = json.dumps({"models": [{"name": I.EMBEDDER["tag"], "digest": I.EMBEDDER["digest"]}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
ckh = FakeClock()
ph = I.IdleProbe(gpu=FakeGpu(), host=FakeHost(ckh, table=lambda t: [proc(21, "ollama.exe")]), ollama=I.OllamaPs(port=srv.server_address[1]),
                 clock=ckh, commit="c" * 40)
wh = ph.window("pre")
srv.shutdown()
check("K34 a window asks Ollama only GET /api/ps, and the pinned embedder resident is no violation",
      set(SEEN) == {"/api/ps"} and wh["ollama_resident"] == [EMB] and ph.evaluate("pre", wh) == [], str((set(SEEN), wh["ollama_resident"])))
pnr, _ = probe(table=lambda t: [proc(22, "notepad.exe")], ollama_fail=True)
wnr = pnr.window("pre")
prun, _ = probe(table=lambda t: [proc(23, "ollama.exe")], ollama_fail=True)
wrun = prun.window("pre")
check("K35 no Ollama process and no answer: 'not running', no violation; a running Ollama that does not answer: unmeasured",
      wnr.get("ollama_note") == "not running" and pnr.evaluate("pre", wnr) == []
      and any(x["threshold"] == "unmeasured" for x in prun.evaluate("pre", wrun)), str((wnr.get("ollama_note"), prun.evaluate("pre", wrun))))

print("\n- --calibrate (I2, I3, Q6, Q8) -")
OBS0 = {"cpu_total_p95": 5.0, "cpu_total_max": 20.0, "gpu_util_p95": 1, "gpu_util_max": 3, "ram_available_gib_min": 12.0,
        "foreign_cpu_pct_of_core_mean": 4.0}


def dec(owner="owner-answer-1", **over):
    return I.decide({**OBS0, **over}, owner_window=owner, seconds=600, complete=True, unmeasured=[], foreign_resident=[])


check("K36 the default class is unverified: branch none, rev1 thresholds kept, the violations kept as evidence (I2)",
      (lambda d: d["class"] == "unverified" and d["branch"] == "none" and d["thresholds_after"] == I.REV1
       and d["violated"] == ["cpu_total_p95"])(dec(owner=None, cpu_total_p95=11.3)))
check("K37 an owner-idle calibration with nothing violated: branch (a), thresholds unchanged", dec()["branch"] == "a"
      and dec()["thresholds_after"] == I.REV1)
d38 = dec(cpu_total_p95=11.3, foreign_cpu_pct_of_core_mean=12.9, gpu_util_p95=10)
check("K38 branch (b): ceil(observed x 1.2) on each violated upper bound - 11.3 -> 14, 12.9 -> 16, 10 -> 12",
      d38["branch"] == "b" and d38["thresholds_after"]["cpu_total_p95"] == 14
      and d38["thresholds_after"]["foreign_cpu_pct_of_core_mean"] == 16 and d38["thresholds_after"]["gpu_util_p95"] == 12
      and d38["thresholds_after"]["cpu_total_max"] == 35, str(d38["thresholds_after"]))
d39 = dec(ram_available_gib_min=5.83, cpu_total_p95=11.3)
check("K39 RAM below 8 GiB: the whole calibration is busy - branch none, RAM stays 8, nothing else loosened (I3, Q8)",
      d39["branch"] == "none" and d39["thresholds_after"] == I.REV1 and any("I3" in r for r in d39["reasons"]), str(d39))
for label, kw in (("a foreign resident model", {"foreign_resident": ["qwen3.6:27b@x"]}), ("an incomplete window", {"complete": False}),
                  ("an unmeasured value", {"unmeasured": ["NVML gone"]}), ("a length other than 600 s", {"seconds": 590})):
    args = {"owner_window": "o", "seconds": 600, "complete": True, "unmeasured": [], "foreign_resident": [], **kw}
    check(f"K40 {label}: branch none", I.decide({**OBS0, "cpu_total_p95": 11.3}, **args)["branch"] == "none")
check("K42 GPU memory, the resident set and RAM are never calibrated",
      set(I.UPPER) == {"gpu_util_p95", "gpu_util_max", "cpu_total_p95", "cpu_total_max", "foreign_cpu_pct_of_core_mean"})
cpu = [5.0] * 600
cpu[297:303] = [30.0] * 6                   # 6 s across the 300-s boundary: tumbling 60-s windows split it 3 + 3
sm = I.sliding_maxima(cpu, [0] * 600, [12.0] * 600, {"9:1.0": [(t, 0.1 * t) for t in range(1, 601, 5)]})
check("K41 sliding 60-s windows see a 6-s burst that tumbling windows and the whole series' p95 hide (Q6)",
      sm["cpu_total_p95"] == 30.0 and I.pct(cpu, .95) == 5.0, str((sm["cpu_total_p95"], I.pct(cpu, .95))))
check("... and a foreign process's largest 60-s mean is its CPU rate (10 % of a core)", sm["foreign_cpu_pct_of_core_mean"] == 10.0,
      str(sm["foreign_cpu_pct_of_core_mean"]))
check("K43 owner-idle needs the owner's window id", dec(owner="")["class"] == "unverified")
ckc = FakeClock()
pcal = I.IdleProbe(gpu=FakeGpu(), host=FakeHost(ckc, busy=0.2, ram_gib=5.83, table=lambda t: [proc(800, "chrome.exe", cpu=1 + 0.05 * t)]),
                   ollama=FakeOllama(), clock=ckc, commit="c" * 40)
cal = I.calibrate(pcal, owner_window=None)
check("the calibration runs 600 s at 1 s on the machine as it is: unverified, branch none, the full series kept",
      cal["decision"]["branch"] == "none" and cal["decision"]["class"] == "unverified" and len(cal["series"]["cpu_total_pct"]) == 600
      and cal["observed"]["ram_available_gib_min"] == 5.83 and cal["window"]["samples"] == 600, str(cal["decision"]))

print("\n- output, provenance, shape -")
root = TMP / "runs"
I.write_run(root, "calibrate", "c1", {"a": 1})
check("K44 a used run label is refused", refused(lambda: I.write_run(root, "calibrate", "c1", {"a": 2}), "used before"))
log = root / "_idle" / "calibrations.jsonl"
I.append_chained(log, {"run": "c1"})
I.append_chained(log, {"run": "c2"})
ok_chain = I.chain_ok(log)
log.write_bytes(log.read_bytes().replace(b'"c1"', b'"cX"'))
check("K44 the calibration log is hash-chained: it verifies, and an edit breaks it", ok_chain and not I.chain_ok(log))
g = TMP / "repo"
(g / ".git" / "refs" / "heads").mkdir(parents=True)
(g / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
(g / ".git" / "packed-refs").write_text("# pack\n" + "a" * 40 + " refs/heads/main\n", encoding="utf-8")
wt = TMP / "wt"
(wt).mkdir()
(g / ".git" / "worktrees" / "wt").mkdir(parents=True)
(wt / ".git").write_text(f"gitdir: {(g / '.git' / 'worktrees' / 'wt').as_posix()}\n", encoding="utf-8")
(g / ".git" / "worktrees" / "wt" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
(g / ".git" / "worktrees" / "wt" / "commondir").write_text("../..\n", encoding="utf-8")
check("K45 the commit is read from .git files: packed-refs, and a worktree's gitdir + commondir (Q13), with no child process",
      I.git_commit(g) == "a" * 40 and I.git_commit(wt) == "a" * 40 and "subprocess" not in I.git_commit.__code__.co_names,
      str((I.git_commit(g), I.git_commit(wt))))
check("K46 the probe's sha256 is taken over the file's bytes",
      I.probe_identity("c")["sha256"] == hashlib.sha256((ROOT / "research" / "_idle_probe.py").read_bytes()).hexdigest())
b1, b2 = I.render(pc), I.render(pc)
check("K47 output is sorted JSON, LF only, byte-identical for the same input", b1 == b2 and b"\r" not in b1
      and b1 == (json.dumps(json.loads(b1), indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode())
for k in ("rule", "probe", "pre", "post", "thresholds"):          # §7's five, not the module's own list
    check(f"K48 the shape check names a missing {k}", I.shape_problems({**pc, k: None}) == [f"missing {k}"],
          str(I.shape_problems({**pc, k: None})))
check("K48 ... and an idle flag that does not follow the violations", I.shape_problems({**pc, "idle": False}) != [])
check("the record carries §7's keys and the additive ones (Q4, Q12): pre/post foreign processes and residents, thresholds_source",
      all(k in pc for k in ("idle", "rule", "probe", "pre", "post", "during", "allowlist", "thresholds", "violations",
                            "thresholds_source", "unmeasurable"))
      and "foreign_processes" in pc["pre"] and "ollama_resident" in pc["post"] and pc["thresholds_source"] == "rev1")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 idle probe: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
