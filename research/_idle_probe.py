#!/usr/bin/env python3
"""PREREG-V3 TB2 (A4): the machine_idle record of §7, measured - never written by hand.

    python research/_idle_probe.py --check --run k1
    python research/_idle_probe.py --calibrate --run c1 [--owner-window <the owner's answer id>]

§7 (rule "v3-idle-1"): pre and post are 60 s at 1 s and are checked against every threshold; the during window, every
5 s beside the timed loop, is checked for foreign processes and resident models only; idle is true iff violations is
empty. Sources: NVML (in process, through ctypes - no child), psutil (deltas, never cpu_percent's shared state), Ollama
GET /api/ps on 127.0.0.1 (nothing that loads or wakes a model). The auditor's A4 rulings (.loop/A4-RULINGS-AUDITOR.md):
* I1/Q1: per-process GPU memory that NVML reports NOT_AVAILABLE (WDDM, read from NVML at run time) is "unmeasurable" - a
  loud pass with a top-level entry, never 0 and never a violation; device-level GPU util and memory in the during
  window are description, not a rule;
* Q2/Q3: foreign CPU is judged per process (pid + create_time), as CPU seconds over the WINDOW's length;
* Q4: pre and post carry foreign_processes and ollama_resident; Q5: seven attempts, start to start, 0..30 min - a failed
  wait still returns (the stand runs with idle:false); Q10: kernel pseudo-processes are not foreign, their CPU is
  description; Q14: a resident embedder must match the pinned tag AND digest;
* --calibrate (I2, I3, Q6, Q8, Q16): 600 s on a strict allowlist; the class is "unverified" unless the owner's idle
  window is named; branch (b) raises only violated UPPER-bound thresholds to ceil(observed max x 1.2), never RAM, GPU
  memory or the resident set; a busy calibration (unverified, RAM < 8 GiB, a foreign resident, an incomplete or
  unmeasured window) is branch none and the thresholds stay at rev1.
The probe starts no child process and writes nothing in the repository. Standard library at import; psutil and NVML
are loaded only when a real sampler is built.
"""
from __future__ import annotations

import argparse
import ctypes
import decimal
import fnmatch
import hashlib
import http.client
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

RULE = "v3-idle-1"
#: rev1 §7's thresholds; the embedder is the D1 tag at its pinned digest (A3.i d1, cleared 2026-09-27).
EMBEDDER = {"tag": "nvt3-bge-m3-d1:latest", "digest": "561ce53b3730a15c5b08676cc985d4a0805d95353c6576e5ef982bc5cc3c5a65"}
REV1 = {"gpu_util_p95": 5, "gpu_util_max": 25, "cpu_total_p95": 10, "cpu_total_max": 35, "ram_available_gib_min": 8,
        "foreign_cpu_pct_of_core_mean": 10, "foreign_gpu_mem_mib": 256,
        "ollama_resident_only": [f"{EMBEDDER['tag']}@{EMBEDDER['digest']}"]}
UPPER = ("gpu_util_p95", "gpu_util_max", "cpu_total_p95", "cpu_total_max", "foreign_cpu_pct_of_core_mean")
PRE_S, PRE_INTERVAL_S = 60, 1
DURING_INTERVAL_S = 5
TABLE_EVERY = 5                                   # the process table, /api/ps and the GPU process list every 5 samples
WAIT_ATTEMPTS, WAIT_STEP_S = 7, 300               # Q5: 0, 5, ..., 30 min, start to start
CALIBRATE_S = 600
COVERAGE_MIN = 0.9
OLLAMA_HOST, OLLAMA_PORT = "127.0.0.1", 11434
ALLOWED = {("GET", "/api/ps"), ("GET", "/api/version")}
NOT_AVAILABLE = 2 ** 64 - 1
#: Q10: kernel pseudo-processes - never foreign; their CPU stays in cpu_total and is written as description.
KERNEL_NAMES = {"system", "registry", "memory compression", "memcompression", "secure system"}   # psutil: MemCompression
DRIVER_MODELS = {0: "WDDM", 1: "TCC", 2: "MCDM"}
REQUIRED = ("rule", "probe", "pre", "post", "thresholds")
#: One process identity from two sources (the harness's psutil, the probe's NT table) differs in the float's last digits;
#: a pid reused within this many seconds is not a real case.
CT_TOL = 0.05


class ProbeRefused(RuntimeError):
    pass


class Unavailable(RuntimeError):
    """A real sampler that cannot run here (NVML, psutil or Ollama) - recorded as "unmeasured: <reason>"."""


# ── the samplers (each injectable; nothing loads at import) ─────────────────────────────────────────────

class NvmlGpu:
    """NVML through ctypes, in this process (O1a). Per-process memory NOT_AVAILABLE -> None (I1)."""

    def __init__(self, dll: str | None = None):
        paths = [dll] if dll else ([os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll")]
                                    if os.name == "nt" else ["libnvidia-ml.so.1"])
        try:
            self.lib = ctypes.CDLL(paths[0])
            if self.lib.nvmlInit_v2() != 0:
                raise OSError("nvmlInit_v2 failed")
            self.h = ctypes.c_void_p()
            if self.lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self.h)) != 0:
                raise OSError("no device 0")
        except (OSError, AttributeError) as e:
            raise Unavailable(f"NVML: {type(e).__name__}: {e}") from None

    def info(self) -> dict:
        name = ctypes.create_string_buffer(96)
        self.lib.nvmlDeviceGetName(self.h, name, 96)
        cur, pend = ctypes.c_uint(), ctypes.c_uint()
        rc = self.lib.nvmlDeviceGetDriverModel(self.h, ctypes.byref(cur), ctypes.byref(pend))
        mem = self.sample()[1]
        m = self._mem()
        return {"name": name.value.decode("utf-8", "replace"), "driver_model": DRIVER_MODELS.get(cur.value) if rc == 0 else None,
                "memory_total_mib": m.total >> 20, "memory_used_mib_at_start": mem, "memory_api": self.memory_api,
                "memory_reserved_mib": getattr(m, "reserved", 0) >> 20}

    memory_api = "v2"

    def _mem(self):
        """nvmlDeviceGetMemoryInfo_v2: "used" without the driver's reserved memory, as nvidia-smi reports it (v1 counts the
        reserved memory as used: 5264 vs 4845 MiB on this machine). v1 is a named fallback where v2 is missing."""
        class Mem2(ctypes.Structure):
            _fields_ = [("version", ctypes.c_uint), ("total", ctypes.c_ulonglong), ("reserved", ctypes.c_ulonglong),
                        ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]

        class Mem1(ctypes.Structure):
            _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]
        if hasattr(self.lib, "nvmlDeviceGetMemoryInfo_v2"):
            m = Mem2()
            m.version = ctypes.sizeof(Mem2) | (2 << 24)
            if self.lib.nvmlDeviceGetMemoryInfo_v2(self.h, ctypes.byref(m)) == 0:
                self.memory_api = "v2"
                return m
        m = Mem1()
        if self.lib.nvmlDeviceGetMemoryInfo(self.h, ctypes.byref(m)) != 0:
            raise Unavailable("NVML: memory info failed")
        self.memory_api = "v1 (reserved memory counted as used)"
        return m

    def sample(self) -> tuple[int, int]:
        class Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]
        u = Util()
        if self.lib.nvmlDeviceGetUtilizationRates(self.h, ctypes.byref(u)) != 0:
            raise Unavailable("NVML: utilisation failed")
        return int(u.gpu), int(self._mem().used >> 20)

    def processes(self) -> list[tuple[int, int | None, str]]:
        class Proc(ctypes.Structure):
            _fields_ = [("pid", ctypes.c_uint), ("usedGpuMemory", ctypes.c_ulonglong),
                        ("gpuInstanceId", ctypes.c_uint), ("computeInstanceId", ctypes.c_uint)]
        out = []
        for kind, fn in (("compute", "nvmlDeviceGetComputeRunningProcesses_v3"),
                         ("graphics", "nvmlDeviceGetGraphicsRunningProcesses_v3")):
            f = getattr(self.lib, fn)
            n = ctypes.c_uint(0)
            f(self.h, ctypes.byref(n), None)
            arr = (Proc * (n.value + 16))()
            n2 = ctypes.c_uint(n.value + 16)
            if f(self.h, ctypes.byref(n2), arr) != 0:
                raise Unavailable(f"NVML: {fn} failed")
            out += [(int(arr[i].pid), None if arr[i].usedGpuMemory == NOT_AVAILABLE else int(arr[i].usedGpuMemory >> 20), kind)
                    for i in range(n2.value)]
        return out


class PsutilHost:
    """CPU from deltas of psutil.cpu_times(); processes as (pid, ppid, name, create_time, cpu seconds, rss)."""

    def __init__(self):
        try:
            import psutil  # noqa: PLC0415 - the research extra
        except ImportError as e:
            raise Unavailable(f"psutil: {e}") from None
        self.p = psutil

    def cores(self) -> int:
        return int(self.p.cpu_count() or 1)

    def cpu_times(self) -> tuple[float, float]:
        t = self.p.cpu_times()
        total = sum(t)
        idle = t.idle + getattr(t, "iowait", 0.0)
        return total - idle, total

    def ram_available_bytes(self) -> int:
        return int(self.p.virtual_memory().available)

    def processes(self) -> list[dict]:
        out = []
        for pr in self.p.process_iter(["pid", "ppid", "name", "create_time", "cpu_times", "memory_info"]):
            i = pr.info
            ct = i.get("cpu_times")
            mi = i.get("memory_info")
            out.append({"pid": i["pid"], "ppid": i.get("ppid"), "name": i.get("name") or "?",
                        "create_time": i.get("create_time") or 0.0,
                        "cpu_s": (ct.user + ct.system) if ct is not None else None,
                        "rss": mi.rss if mi is not None else None})
        return out


class WinHost:
    """Windows through ctypes: one NtQuerySystemInformation(SystemProcessInformation) per process tick (measured 6.5 ms
    for 517 processes, against 0.7 s for psutil's per-process calls, which re-enumerate the system each time - the probe
    must not load the machine it measures), GetSystemTimes for CPU, GlobalMemoryStatusEx for RAM."""

    EPOCH_DELTA = 11644473600

    def __init__(self):
        if os.name != "nt":
            raise Unavailable("WinHost: not Windows")
        self.ntdll = ctypes.WinDLL("ntdll")
        self.k32 = ctypes.WinDLL("kernel32")

    def cores(self) -> int:
        return int(os.cpu_count() or 1)

    def cpu_times(self) -> tuple[float, float]:
        idle, kern, user = (ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong())
        if not self.k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
            raise Unavailable("GetSystemTimes failed")
        total = (kern.value + user.value) / 1e7             # kernel time includes idle time
        return total - idle.value / 1e7, total

    def ram_available_bytes(self) -> int:
        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        ms = MS()
        ms.dwLength = ctypes.sizeof(MS)
        if not self.k32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            raise Unavailable("GlobalMemoryStatusEx failed")
        return int(ms.ullAvailPhys)

    def processes(self) -> list[dict]:
        size = 1 << 20
        while True:
            buf = ctypes.create_string_buffer(size)
            ret = ctypes.c_ulong(0)
            st = self.ntdll.NtQuerySystemInformation(5, buf, size, ctypes.byref(ret)) & 0xFFFFFFFF
            if st == 0:
                break
            if st == 0xC0000004 and size < 1 << 28:          # STATUS_INFO_LENGTH_MISMATCH
                size = max(size * 2, ret.value + 65536)
                continue
            raise Unavailable(f"NtQuerySystemInformation 0x{st:08X}")
        raw, out, off = buf.raw, [], 0

        def u(a, b, signed=False):
            return int.from_bytes(raw[off + a:off + b], "little", signed=signed)

        while True:
            create, nlen, nptr, pid = u(32, 40, True), u(56, 58), u(64, 72), u(80, 88)
            name = ctypes.wstring_at(nptr, nlen // 2) if nptr and nlen else ("System Idle Process" if pid == 0 else "?")
            out.append({"pid": pid, "ppid": u(88, 96), "name": name,
                        "create_time": (create / 1e7 - self.EPOCH_DELTA) if create else 0.0,
                        "cpu_s": (u(40, 48, True) + u(48, 56, True)) / 1e7, "rss": u(144, 152)})
            nxt = u(0, 4)
            if nxt == 0:
                return out
            off += nxt


class OllamaPs:
    """GET /api/ps (and /api/version) on 127.0.0.1 only - anything else is refused before a byte is sent."""

    def __init__(self, port: int = OLLAMA_PORT, host: str = OLLAMA_HOST, timeout: float = 2.0):
        self.port, self.host, self.timeout = port, host, timeout

    def request(self, method: str, path: str) -> dict:
        if self.host != OLLAMA_HOST or (method, path) not in ALLOWED:
            raise ProbeRefused(f"refused: {method} {self.host}{path} - only {sorted(ALLOWED)} on {OLLAMA_HOST}")
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request(method, path)
            r = conn.getresponse()
            body = r.read()
            if r.status != 200:
                raise OSError(f"status {r.status}")
            return json.loads(body)
        finally:
            conn.close()

    def resident(self) -> list[dict]:
        try:
            return [{"name": m.get("name") or m.get("model"), "digest": m.get("digest")}
                    for m in self.request("GET", "/api/ps").get("models") or []]
        except (OSError, ValueError) as e:
            raise Unavailable(f"Ollama /api/ps: {type(e).__name__}: {e}") from None


class Clock:
    def now(self) -> float:
        return time.monotonic()

    def utc(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def sleep_until(self, t: float) -> None:
        d = t - self.now()
        if d > 0:
            time.sleep(d)


# ── statistics and the allowlist, pure ──────────────────────────────────────────────────────────────────

def pct(values: list, q: float):
    """s[round(q*(n-1))] (Q11 - the auditor's witness)."""
    s = sorted(values)
    return s[round(q * (len(s) - 1))] if s else None


def _r(x, nd: int):
    return None if x is None else round(float(x), nd)


class Allowlist:
    """Typed entries (§7): the stand's process tree, ollama*, the recording proxy, a declared service."""

    def __init__(self, *, root: tuple[int, float] | None = None, names: tuple = ("ollama*",),
                 pids: tuple = (), service: dict | None = None, tree_members=None):
        self.root, self.names, self.pids = root, tuple(n.lower() for n in names), tuple(pids)
        self.service = service or None          # {"label", "names": [...]} - only when declared (Q9 decides the names)
        self.tree_members = tree_members        # a callable giving the Job Object's pids, when the harness has one
        self.tracked: set[tuple[int, float]] = set()

    def describe(self) -> list[dict]:
        out = []
        if self.root is not None:
            out.append({"kind": "tree", "root_pid": self.root[0]})
        out += [{"kind": "name", "pattern": n} for n in self.names]
        out += [{"kind": "pid", "pid": p} for p, _ in self.pids]
        if self.service:
            out.append({"kind": "service", "label": self.service["label"], "names": sorted(self.service["names"])})
        return out

    def refresh(self, table: list[dict]) -> None:
        """Descendants of the root by ppid (a child never older than its parent); an orphan seen before its parent
        died stays tracked."""
        if self.root is None:
            return
        by_pid = {p["pid"]: p for p in table}
        members = set(self.tree_members()) if self.tree_members is not None else set()
        rp = by_pid.get(self.root[0])
        if rp is not None and abs(rp["create_time"] - self.root[1]) < CT_TOL:
            self.tracked.add((rp["pid"], rp["create_time"]))       # the table's own value of the root's identity
        changed = True
        while changed:
            changed = False
            for p in table:
                key = (p["pid"], p["create_time"])
                if key in self.tracked:
                    continue
                par = by_pid.get(p.get("ppid"))
                if p["pid"] in members or (par is not None and (par["pid"], par["create_time"]) in self.tracked
                                           and p["create_time"] >= par["create_time"]):
                    self.tracked.add(key)
                    changed = True

    def allowed(self, p: dict) -> bool:
        key = (p["pid"], p["create_time"])
        name = (p.get("name") or "").lower()
        if key in self.tracked or any(p["pid"] == pid and abs(p["create_time"] - ct) < CT_TOL for pid, ct in self.pids):
            return True
        if any(fnmatch.fnmatchcase(name, pat) for pat in self.names):
            return True
        return bool(self.service) and name in {n.lower() for n in self.service["names"]}


def kernel(p: dict) -> bool:
    return p["pid"] == 4 or (p.get("name") or "").lower() in KERNEL_NAMES


def excluded(p: dict) -> bool:
    return os.name == "nt" and p["pid"] == 0     # the System Idle Process is idle time, not a process


# ── a window ────────────────────────────────────────────────────────────────────────────────────────────

class IdleProbe:
    def __init__(self, *, gpu=None, host=None, ollama=None, clock=None, thresholds: dict | None = None,
                 thresholds_source: str = "rev1", allowlist: Allowlist | None = None, commit: str | None = None,
                 containers: dict | None = None):
        self.clock = clock or Clock()
        self.errors: dict[str, str] = {}
        if gpu is False:                       # no GPU sampler at all (a machine without NVML, or a test): unmeasured
            self.gpu, self.errors["gpu"] = None, "GPU sampler not available"
        else:
            self.gpu = gpu if gpu is not None else self._build(NvmlGpu, "gpu")
        self.host = host if host is not None else self._build(WinHost if os.name == "nt" else PsutilHost, "host")
        self.ollama = ollama if ollama is not None else OllamaPs()
        self.thresholds = dict(thresholds or REV1)
        self.thresholds_source = thresholds_source
        self.allow = allowlist or Allowlist(root=None)
        self.commit = commit
        self.containers = containers         # Q9: {"declared": [...], "running": [...]} recorded by the harness

    def _build(self, cls, what: str):
        try:
            return cls()
        except Unavailable as e:
            self.errors[what] = str(e)
            return None

    def _table(self, win: dict, t_rel: float) -> None:
        """One process-table tick: processes, the GPU process list and /api/ps."""
        try:
            table = self.host.processes()
        except Exception as e:  # noqa: BLE001 - a failed tick is counted, never fatal
            win["table_errors"].append(f"processes: {type(e).__name__}")
            return
        self.allow.refresh(table)
        gpu_procs = None
        if self.gpu is not None:
            try:
                gpu_procs = {}
                for pid, mib, kind in self.gpu.processes():
                    prev = gpu_procs.get(pid)
                    gpu_procs[pid] = mib if prev is None or mib is None else max(prev, mib)
            except Exception as e:  # noqa: BLE001 - NVML should work here: unmeasured, a violation (not I1's pass)
                gpu_procs = None
                msg = f"gpu processes: {type(e).__name__}: {e}"
                win["table_errors"].append(msg)
                if msg not in win["unmeasured"]:
                    win["unmeasured"].append(msg)
        win["gpu_lists_read"] = win["gpu_lists_read"] and gpu_procs is not None
        for p in table:
            if excluded(p):
                continue
            key = (p["pid"], p["create_time"])
            st = win["procs"].setdefault(key, {"name": p["name"], "pid": p["pid"], "first_t": t_rel,
                                                "born_inside": p["create_time"] > win["start_epoch"],
                                                "cpu_first": p["cpu_s"], "cpu_last": p["cpu_s"], "rss_max": 0,
                                                "gpu": 0, "unreadable": None})
            if p["cpu_s"] is None:
                st["unreadable"] = "cpu times not readable"
            else:
                st["cpu_last"] = p["cpu_s"]
                if st["cpu_first"] is None:
                    st["cpu_first"] = p["cpu_s"]
            st["rss_max"] = max(st["rss_max"], p["rss"] or 0)
            if gpu_procs is not None and p["pid"] in gpu_procs:
                mib = gpu_procs[p["pid"]]
                st["gpu"] = "unmeasurable" if mib is None else max(mib, st["gpu"] if isinstance(st["gpu"], int) else 0)
            st["allowed"] = self.allow.allowed(p)
            st["kernel"] = kernel(p)
            if p["cpu_s"] is not None:
                win["proc_series"].setdefault(key, []).append((t_rel, p["cpu_s"]))
        try:
            names = self.ollama.resident()
            win["ollama_ok"] = True
            win["ollama_resident"] |= {f"{m['name']}@{m['digest']}" for m in names}
        except Unavailable as e:
            running = any((p.get("name") or "").lower().startswith("ollama") for p in table)
            if running:
                win["unmeasured"].append(str(e))
            else:
                win["ollama_note"] = "not running"

    def _new(self, kind: str, seconds: float, interval: float) -> dict:
        return {"kind": kind, "seconds": seconds, "interval_s": interval, "start_utc": self.clock.utc(),
                "start_epoch": time.time() if not hasattr(self.clock, "epoch") else self.clock.epoch(),
                "gpu_util": [], "gpu_mem": [], "cpu": [], "ram": [], "procs": {}, "table_errors": [],
                "sample_errors": 0, "samples": 0, "unmeasured": [], "ollama_resident": set(), "ollama_ok": False,
                "gpu_lists_read": True, "cost_ms": [], "proc_series": {}}

    def window(self, kind: str, seconds: float = PRE_S, interval: float = PRE_INTERVAL_S, *, system: bool = True) -> dict:
        """A pre or post window (or the calibration's): system values every ``interval``, the process table every
        TABLE_EVERY samples and at the end, on an absolute schedule."""
        win = self._new(kind, seconds, interval)
        n = int(round(seconds / interval))
        win["expected_samples"] = n
        for what, obj in (("gpu", self.gpu), ("host", self.host)):
            if obj is None:
                win["unmeasured"].append(self.errors.get(what, f"{what} unavailable"))
        prev = None
        if self.host is not None:
            try:
                prev = self.host.cpu_times()
            except Exception:  # noqa: BLE001
                prev = None
        t0 = self.clock.now()
        if self.host is not None:
            self._table(win, 0.0)                 # the baseline at the window's start
        for i in range(1, n + 1):
            self.clock.sleep_until(t0 + i * interval)
            c0 = time.perf_counter()
            try:
                if system:
                    if self.gpu is not None:
                        u, m = self.gpu.sample()
                        win["gpu_util"].append(u)
                        win["gpu_mem"].append(m)
                    if self.host is not None:
                        cur = self.host.cpu_times()
                        if prev is not None and cur[1] > prev[1]:
                            win["cpu"].append(100.0 * (cur[0] - prev[0]) / (cur[1] - prev[1]))
                        prev = cur
                        win["ram"].append(self.host.ram_available_bytes() / 2 ** 30)
                win["samples"] += 1
            except Exception:  # noqa: BLE001 - a failed sample is counted
                win["sample_errors"] += 1
            if self.host is not None and (i % TABLE_EVERY == 0 or i == n):
                self._table(win, i * interval)
            win["cost_ms"].append((time.perf_counter() - c0) * 1000)
        return self._finish(win)

    def _finish(self, win: dict) -> dict:
        secs = float(win["seconds"])
        foreign, allowed, kernel_rows, unreadable = [], [], [], []
        for (_pid, _ct), st in sorted(win["procs"].items()):
            base = 0.0 if st["born_inside"] else (st["cpu_first"] or 0.0)
            mean = None if st["unreadable"] and st["cpu_last"] is None else \
                100.0 * max(0.0, (st["cpu_last"] or 0.0) - base) / secs
            row = {"name": st["name"], "pid": st["pid"], "cpu_pct_of_core_mean": _r(mean, 1),
                   "gpu_mem_mib": st["gpu"] if win["gpu_lists_read"] else "unmeasurable",
                   "rss_mib": int(st["rss_max"] / 2 ** 20)}
            if st["unreadable"]:
                row["unreadable"] = st["unreadable"]
            if st.get("kernel"):
                kernel_rows.append(row)
            elif st.get("allowed"):
                allowed.append(row)
            else:
                foreign.append(row)
        shown = [r for r in foreign if (r["cpu_pct_of_core_mean"] or 0) >= 1 or r["gpu_mem_mib"] not in (0,)]
        out = {"start_utc": win["start_utc"], "seconds": win["seconds"], "interval_s": win["interval_s"],
               "samples": win["samples"], "expected_samples": win["expected_samples"],
               "gpu_util_pct": {"p50": pct(win["gpu_util"], .5), "p95": pct(win["gpu_util"], .95),
                                "max": max(win["gpu_util"]) if win["gpu_util"] else None},
               "gpu_mem_used_mib": {"p50": pct(win["gpu_mem"], .5), "max": max(win["gpu_mem"]) if win["gpu_mem"] else None},
               "cpu_total_pct": {"p50": _r(pct(win["cpu"], .5), 1), "p95": _r(pct(win["cpu"], .95), 1),
                                 "max": _r(max(win["cpu"]) if win["cpu"] else None, 1)},
               "ram_available_gib": {"min": _r(min(win["ram"]) if win["ram"] else None, 2)},
               "foreign_processes": shown, "foreign_other_count": len(foreign) - len(shown),
               "foreign_all": foreign, "allowed": allowed, "kernel": kernel_rows,
               "ollama_resident": sorted(win["ollama_resident"]),
               "sample_cost_ms": _r(max(win["cost_ms"]) if win["cost_ms"] else None, 1),
               "table_errors": win["table_errors"], "unmeasured": win["unmeasured"]}
        if "ollama_note" in win and not win["ollama_ok"]:
            out["ollama_note"] = win["ollama_note"]
        if not win["gpu_lists_read"]:
            out["gpu_process_lists"] = "not read"
        out["_raw"] = {"cpu": win["cpu"], "gpu_util": win["gpu_util"], "ram": win["ram"],
                       "foreign_series": {f"{k[0]}:{k[1]}": v for k, v in win["proc_series"].items()
                                          if not win["procs"][k].get("allowed") and not win["procs"][k].get("kernel")}}
        return out

    # ── during ──────────────────────────────────────────────────────────────────────────────────────────
    def during(self, interval: float = DURING_INTERVAL_S):
        return _During(self, interval)

    # ── evaluation and the busy wait ────────────────────────────────────────────────────────────────────
    def evaluate(self, kind: str, w: dict) -> list[dict]:
        return evaluate(kind, w, self.thresholds)

    def wait_for_idle(self) -> tuple[dict, list[dict]]:
        """Q5: seven attempts, start to start, at t0 + k*300 s; the first idle one wins, else the last is returned."""
        attempts, t0, w = [], self.clock.now(), None
        for k in range(WAIT_ATTEMPTS):
            self.clock.sleep_until(t0 + k * WAIT_STEP_S)
            w = self.window("pre")
            v = self.evaluate("pre", w)
            attempts.append({"start_utc": w["start_utc"], "violations": v})
            if not v:
                break
        return w, attempts

    def record(self, pre: dict, during: dict, post: dict, attempts: list | None = None) -> dict:
        return build_record(pre=pre, during=during, post=post, thresholds=self.thresholds,
                            thresholds_source=self.thresholds_source, allowlist=self.allow.describe(),
                            probe=probe_identity(self.commit), gpu_info=self._gpu_info(), attempts=attempts or [],
                            containers=self.containers)

    def _gpu_info(self) -> dict | None:
        try:
            return self.gpu.info() if self.gpu is not None else None
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}"}


class _During:
    """The during window: a daemon thread in the harness (O3a); a failing tick is counted, the thread always joins."""

    def __init__(self, probe: IdleProbe, interval: float):
        self.p, self.interval, self.result = probe, interval, None
        self._stop = threading.Event()
        self.win = probe._new("during", 0.0, interval)

    def __enter__(self):
        self.t0 = self.p.clock.now()
        self.p._table(self.win, 0.0)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        i = 0
        while True:
            i += 1
            target = self.t0 + i * self.interval
            while not self._stop.is_set() and self.p.clock.now() < target:
                self._stop.wait(min(0.05, max(0.0, target - self.p.clock.now())))
            if self._stop.is_set():
                return
            c0 = time.perf_counter()
            try:
                self.p._table(self.win, i * self.interval)
                if self.p.gpu is not None:
                    u, m = self.p.gpu.sample()
                    self.win["gpu_util"].append(u)
                    self.win["gpu_mem"].append(m)
                self.win["samples"] += 1
            except Exception:  # noqa: BLE001
                self.win["sample_errors"] += 1
            self.win["cost_ms"].append((time.perf_counter() - c0) * 1000)

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        elapsed = self.p.clock.now() - self.t0
        self.p._table(self.win, elapsed)
        self.win["seconds"] = max(elapsed, 1e-9)
        self.win["expected_samples"] = int(elapsed // self.interval)
        self.result = self.p._finish(self.win)
        self.result["device_description"] = {"gpu_util_pct_max": self.result.pop("gpu_util_pct")["max"],
                                             "gpu_mem_used_mib_max": self.result.pop("gpu_mem_used_mib")["max"]}
        for k in ("cpu_total_pct", "ram_available_gib"):
            self.result.pop(k)
        return False


# ── evaluation, pure ────────────────────────────────────────────────────────────────────────────────────

def _resident_ok(entry: str, allowed: list) -> bool:
    """Q14: the tag (a bare name means :latest) AND the pinned digest."""
    def norm(s: str) -> tuple[str, str]:
        name, _, digest = s.partition("@")
        return (name if ":" in name else f"{name}:latest"), digest
    n, d = norm(entry)
    return any(n == an and d == ad for an, ad in (norm(a) for a in allowed))


def evaluate(kind: str, w: dict, th: dict) -> list[dict]:
    """§7: pre and post against every threshold; during against foreign processes and resident models only (Q1: device
    GPU values there are description). Unmeasurable per-process GPU memory is never a violation (Q1)."""
    v = []

    def add(threshold, observed, limit, **extra):
        v.append({"window": kind, "threshold": threshold, "observed": observed, "limit": limit, **extra})

    if kind in ("pre", "post"):
        g, c, r = w["gpu_util_pct"], w["cpu_total_pct"], w["ram_available_gib"]
        for key, obs, lim in (("gpu_util_p95", g["p95"], th["gpu_util_p95"]), ("gpu_util_max", g["max"], th["gpu_util_max"]),
                              ("cpu_total_p95", c["p95"], th["cpu_total_p95"]), ("cpu_total_max", c["max"], th["cpu_total_max"])):
            if obs is not None and obs > lim:
                add(key, obs, lim)
        if r["min"] is not None and r["min"] < th["ram_available_gib_min"]:
            add("ram_available_gib_min", r["min"], th["ram_available_gib_min"])
    for p in w["foreign_all"]:
        cpu = p["cpu_pct_of_core_mean"]
        if cpu is not None and cpu > th["foreign_cpu_pct_of_core_mean"]:
            add("foreign_cpu_pct_of_core_mean", cpu, th["foreign_cpu_pct_of_core_mean"], process=f"{p['name']}:{p['pid']}")
        if isinstance(p["gpu_mem_mib"], int) and p["gpu_mem_mib"] > th["foreign_gpu_mem_mib"]:
            add("foreign_gpu_mem_mib", p["gpu_mem_mib"], th["foreign_gpu_mem_mib"], process=f"{p['name']}:{p['pid']}")
    for m in w["ollama_resident"]:
        if not _resident_ok(m, th["ollama_resident_only"]):
            add("ollama_resident_only", m, th["ollama_resident_only"])
    if w["expected_samples"] and w["samples"] < COVERAGE_MIN * w["expected_samples"]:
        add("complete", w["samples"], f">= {COVERAGE_MIN} x {w['expected_samples']}")
    for u in w["unmeasured"]:
        add("unmeasured", u, "measured")
    return v


def _unmeasurable(windows: dict, gpu_info: dict | None) -> list[dict]:
    hit = [k for k, w in windows.items()
           if w and (w.get("gpu_process_lists") or any(p["gpu_mem_mib"] == "unmeasurable" for p in w["foreign_all"]))]
    if not hit:
        return []
    model = (gpu_info or {}).get("driver_model")
    return [{"threshold": "foreign_gpu_mem_mib", "windows": hit,
             "reason": f"NVML reports per-process GPU memory NOT_AVAILABLE; driver model {model} (read from NVML)"}]


def probe_identity(commit: str | None = None) -> dict:
    return {"tool": "research/_idle_probe.py", "commit": commit or git_commit(REPO),
            "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def git_commit(repo: Path) -> str | None:
    """Q13: HEAD read from the .git files - a worktree's .git file (gitdir, commondir) and packed-refs; no child."""
    g = Path(repo) / ".git"
    try:
        gitdir = g if g.is_dir() else Path(g.read_text(encoding="utf-8").strip().split("gitdir:", 1)[1].strip())
        if not gitdir.is_absolute():
            gitdir = (Path(repo) / gitdir).resolve()
        common = gitdir
        if (gitdir / "commondir").is_file():
            common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head
        ref = head.split(":", 1)[1].strip()
        for base in (gitdir, common):
            f = base / Path(*ref.split("/"))
            if f.is_file():
                return f.read_text(encoding="utf-8").strip()
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except (OSError, IndexError):
        return None
    return None


def _clean(w: dict | None) -> dict | None:
    if w is None:
        return None
    return {k: v for k, v in w.items() if k not in ("foreign_all", "_raw")}


def build_record(*, pre: dict, during: dict, post: dict, thresholds: dict, thresholds_source: str, allowlist: list,
                 probe: dict, gpu_info: dict | None, attempts: list, containers: dict | None = None) -> dict:
    violations = evaluate("pre", pre, thresholds) + evaluate("during", during, thresholds) + evaluate("post", post, thresholds)
    rec = {"idle": not violations, "rule": RULE, "probe": probe, "pre": _clean(pre), "post": _clean(post),
           "during": _clean(during), "allowlist": allowlist, "thresholds": thresholds,
           "thresholds_source": thresholds_source,
           "unmeasurable": _unmeasurable({"pre": pre, "during": during, "post": post}, gpu_info),
           "host": {"gpu": gpu_info}, "wait": attempts, "violations": violations,
           "limits": ["processes that live under one 5-s process tick can be missed",
                      "per-process GPU memory is unmeasurable where NVML says NOT_AVAILABLE (I1)"]}
    if containers is not None:
        rec["containers"] = containers
    problems = shape_problems(rec)
    if problems:
        raise ProbeRefused(f"the record fails its own shape check: {problems}")
    return rec


def shape_problems(rec: dict) -> list[str]:
    """What TB6b's remeasure checks: rule, probe, pre, post and thresholds (§7); idle is exactly (violations == [])."""
    out = [f"missing {k}" for k in REQUIRED if not rec.get(k)]
    if rec.get("rule") and rec["rule"] != RULE:
        out.append(f"rule is {rec['rule']}, not {RULE}")
    if "idle" in rec and rec["idle"] != (rec.get("violations") == []):
        out.append("idle does not follow the violations")
    return out


# ── calibration (I2, I3, Q6, Q8, Q16) ───────────────────────────────────────────────────────────────────

def sliding_maxima(cpu: list, gpu_util: list, ram: list, proc_series: dict, window: int = 60) -> dict:
    """Q6: a p95 threshold's observed maximum is the largest p95 over every sliding 60-sample window; a max threshold's
    is the whole maximum; RAM's is the whole minimum; foreign CPU is the largest 60-s mean of one process (its 5-s
    CPU-seconds series, 12 steps per window)."""
    def slide_p95(xs):
        if len(xs) < window:
            return pct(xs, .95)
        return max(pct(xs[i:i + window], .95) for i in range(len(xs) - window + 1))
    fcpu = 0.0
    steps = window // TABLE_EVERY
    for series in proc_series.values():
        for i in range(max(1, len(series) - steps)):
            j = min(i + steps, len(series) - 1)
            if j > i:
                fcpu = max(fcpu, 100.0 * (series[j][1] - series[i][1]) / max(series[j][0] - series[i][0], 1e-9))
    return {"cpu_total_p95": _r(slide_p95(cpu), 1), "cpu_total_max": _r(max(cpu) if cpu else None, 1),
            "gpu_util_p95": slide_p95(gpu_util), "gpu_util_max": max(gpu_util) if gpu_util else None,
            "ram_available_gib_min": _r(min(ram) if ram else None, 2), "foreign_cpu_pct_of_core_mean": _r(fcpu, 1)}


def decide(observed: dict, *, owner_window: str | None, seconds: float, complete: bool, unmeasured: list,
           foreign_resident: list, thresholds: dict | None = None) -> dict:
    """The idle-thresholds slot, as the auditor ruled: branch none unless an owner-idle, full, clean calibration; then
    (a) nothing violated, or (b) each violated UPPER bound -> ceil(observed x 1.2), never RAM, GPU memory or residents."""
    th = dict(thresholds or REV1)
    cls = "owner-idle" if owner_window else "unverified"
    reasons = []
    if cls != "owner-idle":
        reasons.append("unverified window (I2)")
    ram = observed.get("ram_available_gib_min")
    if ram is not None and ram < th["ram_available_gib_min"]:
        reasons.append(f"ram_available_gib_min {ram} < {th['ram_available_gib_min']} (I3, Q8)")
    if foreign_resident:
        reasons.append(f"foreign resident model(s) {foreign_resident}")
    if not complete:
        reasons.append("an incomplete window")
    if unmeasured:
        reasons.append(f"unmeasured: {unmeasured}")
    if seconds != CALIBRATE_S:
        reasons.append(f"length {seconds} s is not {CALIBRATE_S} s")
    violated = [k for k in UPPER if observed.get(k) is not None and observed[k] > th[k]]
    if reasons:
        return {"class": cls, "owner_window": owner_window, "branch": "none", "reasons": reasons, "violated": violated,
                "thresholds_after": th}
    if not violated:
        return {"class": cls, "owner_window": owner_window, "branch": "a", "reasons": [], "violated": [],
                "thresholds_after": th}
    after = dict(th)
    for k in violated:
        after[k] = int((decimal.Decimal(str(observed[k])) * decimal.Decimal("1.2")).to_integral_value(decimal.ROUND_CEILING))
    return {"class": cls, "owner_window": owner_window, "branch": "b", "reasons": [], "violated": violated,
            "thresholds_after": after}


def calibrate(probe: IdleProbe, *, owner_window: str | None, seconds: float = CALIBRATE_S) -> dict:
    """600 s at 1 s, the process table every 5 s, on the probe's strict allowlist (Q16); the raw series stay in the
    record (the auditor's witness is compared against them)."""
    w = probe.window("calibrate", seconds, PRE_INTERVAL_S)
    raw = w["_raw"]
    obs = sliding_maxima(raw["cpu"], raw["gpu_util"], raw["ram"], raw["foreign_series"])
    foreign_resident = [m for m in w["ollama_resident"] if not _resident_ok(m, probe.thresholds["ollama_resident_only"])]
    complete = w["samples"] >= COVERAGE_MIN * w["expected_samples"]
    dec = decide(obs, owner_window=owner_window, seconds=seconds, complete=complete, unmeasured=w["unmeasured"],
                 foreign_resident=foreign_resident, thresholds=probe.thresholds)
    return {"rule": RULE, "probe": probe_identity(probe.commit), "window": _clean(w),
            "series": {"cpu_total_pct": [_r(x, 1) for x in raw["cpu"]], "gpu_util_pct": raw["gpu_util"],
                       "ram_available_gib": [_r(x, 2) for x in raw["ram"]]},
            "observed": obs, "decision": dec, "thresholds_before": probe.thresholds, "gpu": probe._gpu_info()}


# ── output ──────────────────────────────────────────────────────────────────────────────────────────────

def render(obj: dict) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def append_chained(path: Path, record: dict) -> None:
    """One line chained to the previous one by its sha256 ("prev"); the first line's prev is zeros."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_bytes().rstrip(b"\n").split(b"\n") if path.exists() and path.read_bytes().strip() else []
    prev = hashlib.sha256(lines[-1]).hexdigest() if lines else "0" * 64
    with open(path, "ab") as f:
        f.write((json.dumps({**record, "prev": prev}, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))


def chain_ok(path: Path) -> bool:
    prev = None
    for line in path.read_bytes().rstrip(b"\n").split(b"\n") if path.exists() else []:
        want = hashlib.sha256(prev).hexdigest() if prev is not None else "0" * 64
        try:
            if json.loads(line).get("prev") != want:
                return False
        except ValueError:
            return False
        prev = line
    return True


def write_run(root: Path, kind: str, run: str, record: dict) -> Path:
    base = Path(root) / "_idle" / kind / run
    if base.exists():
        raise ProbeRefused(f"the run label {run!r} was used before")
    base.mkdir(parents=True)
    out = base / "record.json"
    out.write_bytes(render(record))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the machine_idle probe (PREREG-V3 §7, TB2)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="one 60-s window against the thresholds")
    g.add_argument("--calibrate", action="store_true", help="600 s; decides the idle-thresholds slot's branch")
    ap.add_argument("--run", required=True)
    ap.add_argument("--owner-window", default=None, help="the owner's idle-window answer id, verbatim")
    args = ap.parse_args(argv)
    import importlib.util  # noqa: PLC0415
    spec = importlib.util.spec_from_file_location("v3_launch", HERE / "v3" / "launch.py")
    L = importlib.util.module_from_spec(spec)
    sys.modules["v3_launch"] = L
    spec.loader.exec_module(L)
    root = L.Contract.default().runs_root
    probe = IdleProbe(allowlist=Allowlist(root=_self_root()))
    if args.check:
        w = probe.window("check")
        rec = {"rule": RULE, "probe": probe_identity(), "window": _clean(w), "violations": probe.evaluate("pre", w),
               "thresholds": probe.thresholds, "gpu": probe._gpu_info()}
        out = write_run(root, "check", args.run, rec)
    else:
        rec = calibrate(probe, owner_window=args.owner_window)
        out = write_run(root, "calibrate", args.run, rec)
        append_chained(Path(root) / "_idle" / "calibrations.jsonl",
                       {"run": args.run, "record_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
                        "class": rec["decision"]["class"], "branch": rec["decision"]["branch"]})
    print(json.dumps({"record": str(out), "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}, indent=1))
    return 0


def _self_root() -> tuple[int, float] | None:
    try:
        import psutil  # noqa: PLC0415
        p = psutil.Process(os.getpid())
        return p.pid, p.create_time()
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    sys.exit(main())
