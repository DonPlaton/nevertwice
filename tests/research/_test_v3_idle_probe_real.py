#!/usr/bin/env python3
"""PREREG-V3 TB2 (A4) on real processes: research/_idle_probe.py's samplers on this machine (the research tier: psutil).

* RR1 a CPU-burning process outside the probe's tree is foreign at >= 50 % of a core;
* RR2 Windows pid 0 (the System Idle Process) is not a process in the record;
* RR3 one process pass costs well under the 5-s tick (the probe must not load the machine it measures);
* RR4 the CPU total from deltas agrees with psutil's over the same interval, within 5 points;
* RR5 a real during window reaches >= 0.9 coverage;
* RR6 NVML (by name when absent): utilisation within 0-100, used <= total, used within 64 MiB or 2 % of a ONE-SHOT
  nvidia-smi run by this test (never by the probe - the auditor's O1), every NVML pid a real pid (the struct layout),
  and on WDDM every per-process value NOT_AVAILABLE with the record's entry naming WDDM (I1's evidence);
* RR7 on Windows an orphan (its parent gone) stays in the tree;
* RR8 on Windows the one-call NT process table agrees with psutil (pids, create times, names but the two known renames);
* on Windows with NVML, nothing GPU-related was skipped.
Every skip is named and printed, never a silent pass. No real Ollama is touched (the socket guard forbids it).

    python tests/research/_test_v3_idle_probe_real.py
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))

import _env_guard  # noqa: F401,E402  hermetic like every suite
import psutil  # noqa: E402  the research extra; the self-check skips this suite cleanly without it


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


I = _load("idle_probe_real", ROOT / "research" / "_idle_probe.py")
PASSED = FAILED = 0
SKIPPED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def skip(what: str, why: str) -> None:
    SKIPPED.append(what)
    print(f"       SKIP {what}: {why}")


class NoOllama:
    def resident(self):
        raise I.Unavailable("the test does not touch the real Ollama")


host = I.WinHost() if os.name == "nt" else I.PsutilHost()
me = psutil.Process(os.getpid())
try:
    gpu = I.NvmlGpu()
except I.Unavailable as e:
    gpu = None
    skip("NVML", str(e))

print("\n- RR1/RR2: a foreign burner, pid 0 -")
# The "stand" is its own process; the burner is this test's child, so it is NOT in the stand's tree.
stand = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
burner = subprocess.Popen([sys.executable, "-c", "import time\nt = time.time()\nwhile time.time() - t < 12: pass"],
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(0.5)
    sp = psutil.Process(stand.pid)
    probe = I.IdleProbe(gpu=gpu if gpu is not None else False, host=host, ollama=NoOllama(),
                        allowlist=I.Allowlist(root=(sp.pid, sp.create_time())), commit="c" * 40)
    w = probe.window("pre", 6, 1)
finally:
    for pr in (burner, stand):
        pr.kill()
        pr.wait()
row = next((r for r in w["foreign_all"] if r["pid"] == burner.pid), None)
check("RR1 a CPU-burning process outside the stand's tree is foreign at >= 50 % of a core", row is not None
      and (row["cpu_pct_of_core_mean"] or 0) >= 50, str(row))
check("RR1b ... while the stand itself is allowed through its tree (its root matched across psutil and the probe's table)",
      any(r["pid"] == stand.pid for r in w["allowed"]), str([r["pid"] for r in w["allowed"]]))
if os.name == "nt":
    check("RR2 pid 0 (System Idle Process) is not a process in the record",
          not any(r["pid"] == 0 for r in w["foreign_all"] + w["allowed"] + w["kernel"]))
else:
    skip("RR2", "pid 0 exists only on Windows")

print("\n- RR3/RR4: cost and agreement -")
t0 = time.perf_counter()
tab = host.processes()
cost = time.perf_counter() - t0
check("RR3 one process pass costs < 20 % of the 5-s tick (here < 1 s)", cost < 1.0, f"{cost * 1000:.0f} ms for {len(tab)}")
print(f"       measured: one process pass {cost * 1000:.1f} ms for {len(tab)} processes ({type(host).__name__})")
a0, p0 = host.cpu_times(), psutil.cpu_times()
time.sleep(2.0)
a1, p1 = host.cpu_times(), psutil.cpu_times()
ours = 100.0 * (a1[0] - a0[0]) / (a1[1] - a0[1])
tp0, tp1 = sum(p0), sum(p1)
theirs = 100.0 * ((tp1 - p1.idle) - (tp0 - p0.idle)) / (tp1 - tp0)
check("RR4 the CPU total from deltas agrees with psutil's over the same 2 s, within 5 points", abs(ours - theirs) <= 5.0,
      f"{ours:.1f} vs {theirs:.1f}")

print("\n- RR5: a real during window -")
probe5 = I.IdleProbe(gpu=gpu if gpu is not None else False, host=host, ollama=NoOllama(),
                     allowlist=I.Allowlist(root=(me.pid, me.create_time())), commit="c" * 40)
with probe5.during(interval=0.5) as d:
    time.sleep(3.2)
check("RR5 a real during window reaches >= 0.9 coverage", d.result["samples"] >= 0.9 * d.result["expected_samples"]
      and d.result["expected_samples"] >= 5, str((d.result["samples"], d.result["expected_samples"])))

print("\n- RR6: NVML -")
if gpu is not None:
    info = gpu.info()
    util, used = gpu.sample()
    check("RR6 NVML utilisation within 0-100 and used <= total", 0 <= util <= 100 and 0 < used <= info["memory_total_mib"],
          str((util, used, info)))
    smi = shutil.which("nvidia-smi")
    if smi:
        out = subprocess.run([smi, "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True,
                             timeout=30).stdout.strip().splitlines()
        ref = int(out[0]) if out and out[0].strip().isdigit() else None
        _, used2 = gpu.sample()
        check("RR6 NVML's used memory is within 64 MiB or 2 % of a one-shot nvidia-smi run by this test (never by the probe)",
              ref is not None and abs(used2 - ref) <= max(64, 0.02 * ref), str((used2, ref)))
    else:
        skip("RR6 nvidia-smi cross-check", "no nvidia-smi on PATH")
    procs = gpu.processes()
    live = {p.pid for p in psutil.process_iter()}
    check("RR6 every NVML process pid is a real pid (the v3 struct layout)", procs and all(pid in live or pid == 0 for pid, _, _ in procs)
          and sum(pid in live for pid, _, _ in procs) >= 0.8 * len(procs), str(procs[:5]))
    if info["driver_model"] == "WDDM":
        rec = probe.record(w, {**w, "expected_samples": 0}, w)
        check("RR6 on WDDM every per-process value is NOT_AVAILABLE, and the record's entry names WDDM (I1, shown here)",
              all(m is None for _, m, _ in procs) and rec["unmeasurable"] and "WDDM" in rec["unmeasurable"][0]["reason"],
              str((procs[:3], rec["unmeasurable"])))
    else:
        skip("RR6 WDDM", f"driver model {info['driver_model']}")
else:
    skip("RR6", "no NVML here")

print("\n- RR7/RR8: Windows -")
if os.name == "nt":
    gc_code = "import time; time.sleep(6)"
    parent = subprocess.Popen([sys.executable, "-c",
                               "import subprocess, sys, time\n"
                               f"p = subprocess.Popen([sys.executable, '-c', {gc_code!r}], stdin=subprocess.DEVNULL, "
                               "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x00000008)\n"
                               "print(p.pid, flush=True)\ntime.sleep(1.5)"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    gpid = int(parent.stdout.readline().decode().strip())
    pp = psutil.Process(parent.pid)
    al = I.Allowlist(root=(parent.pid, pp.create_time()))
    al.refresh(host.processes())
    parent.wait(timeout=30)
    time.sleep(0.5)
    table = host.processes()
    al.refresh(table)
    g = next((p for p in table if p["pid"] == gpid), None)
    check("RR7 an orphan whose parent exited stays in the tree", g is not None and al.allowed(g), str(g))
    try:
        psutil.Process(gpid).kill()
    except psutil.Error:
        pass
    nt = {p["pid"]: p for p in host.processes()}
    agree, names = 0, []
    for pr in psutil.process_iter(["pid", "name", "create_time"]):
        q = nt.get(pr.info["pid"])
        if q is None:
            continue
        agree += abs((pr.info["create_time"] or 0) - q["create_time"]) < 0.01 or pr.info["pid"] in (0, 4)
        if (pr.info["name"] or "").lower() != q["name"].lower():
            names.append((pr.info["name"], q["name"]))
    check("RR8 the one-call NT process table agrees with psutil: create times, and names but the two known renames",
          agree >= 0.98 * len(nt) - 5 and all(n in (("MemCompression", "Memory Compression"), ("", "Secure System")) for n in names),
          str((agree, len(nt), names)))
else:
    skip("RR7", "an orphan re-parented to init is the witness's declared limit off Windows")
    skip("RR8", "the NT process table exists only on Windows")

check("on Windows with NVML, nothing GPU-related was skipped",
      not (os.name == "nt" and gpu is not None) or not [s for s in SKIPPED if s.startswith(("NVML", "RR6"))], str(SKIPPED))
print(f"\nv3 idle probe (real): {PASSED} passed, {FAILED} failed, {len(SKIPPED)} skipped by name")
sys.exit(1 if FAILED else 0)
