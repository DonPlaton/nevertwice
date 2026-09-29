#!/usr/bin/env python3
"""PREREG-V3 TB4.11a A5 (A6): research/v3/scheduler.py - the write turn, with REAL children (a fake arm speaking
arms/base.py's protocol from a byte copy of base.py, the Q9 mirror) under a temporary contract (rev1 §5.6; the
auditor's Q25, D1, D2):

* T12 concurrency: an arm's write turn runs block size x runs units at once - every child saw exactly that many live
  peers of its own arm and none of the other arm, whose turn came before or after (M-SCHED-concurrency-asym);
* T11 ceiling: a unit that outlives its ceiling is killed with its tree and logged UNIT-ABORT reason=ceiling; its
  heartbeat stops within a second of the line; the arm-run's END then lists it (M-SCHED-ceiling-noabort);
* T21 crash: a unit whose child dies mid-write is UNIT-ABORT reason=crash with its exit code (D2);
* the unit record: spawn id, pid, each write's op id and times, footprint, exit code, active seconds; a memory-store
  arm keeps its live client for the read stage (Q25(4));
* B-WCTR: the write child's counters, asked after end_write and its stamp (never a write op), are in the unit record
  and the run record; a child that does not answer is UNIT-ABORT by name; a memory-store arm's question counters are
  marked as including the write snapshot, a disk arm's as not;
* FIX-SCHED (the auditor's B-RC, B-OPEN, B-CL, B-TE): a unit's exit code is only ever the child's own - a kill of ours
  is signal=SIGKILL, a code that never came is a SchedulerError, a refused spawn is no unit at all; a block or a stand
  that fails after its START closes every line it opened (ABORT reason=harness-error, BLOCK END, STAND END), resets
  the proxy stage, ends its check and kills its live children; a scored stand never writes an unread change log as a
  value, and a dirty tree at STAND END stops its judges.

    python tests/research/_test_v3_scheduler_turns.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SC = _load("v3_scheduler_turns", ROOT / "research" / "v3" / "scheduler.py")
L = _load("v3_launch_for_turns", ROOT / "research" / "v3" / "launch.py")
SL = _load("v3_status_log_for_turns", ROOT / "research" / "v3" / "status_log.py")
#: R-LAUNCHER: a spawn needs a way to kill the unit's whole tree. The fake arms here are single processes on the base
#: interpreter (the B-VENV row) that start nothing (the row after it), so where neither a job object nor psutil is at
#: hand their root's kill IS their tree's - this suite then runs on that one declared route. The refusal itself has its
#: rows in the pure scheduler suite, on the real function.
_REAL_ROUTE = SC.tree_kill_route
SC.tree_kill_route = lambda witnesses: _REAL_ROUTE(witnesses) or "root-is-the-tree"
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def attempt(fn):
    """(result, None) or (None, the exception) - a row's failure is a named FAIL, never a traceback."""
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001
        return None, e


FAKE_ARM = r'''
import hashlib, json, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base as B
spec = json.load(open(sys.argv[1], encoding="utf-8"))
shared = spec["shared"]
knobs = spec.get("knobs") or {}
STREAMS = {}
_claim = B.claim_stdio
os.makedirs(os.path.join(shared, "base_sha"), exist_ok=True)       # the Q9 mirror as THIS child imported it
with open(os.path.join(shared, "base_sha", str(os.getpid())), "w") as _f:
    with open(B.__file__, "rb") as _b:
        _f.write(hashlib.sha256(_b.read()).hexdigest())


class _MuteBye:
    """knobs mute_bye: the answer to bye never comes - the child hangs at its close (B-RC at the close); bad_bye: the
    answer to bye is a line that is no protocol, and the child then exits 0 by itself."""

    def __init__(self, f):
        self.f = f

    def write(self, b):
        if b'"op":"bye"' in b:
            if knobs.get("bad_bye"):
                return self.f.write(b"this is no protocol line\n")
            time.sleep(60)
        return self.f.write(b)

    def flush(self):
        return self.f.flush()


def _claim_and_keep():
    fin, fout = _claim()
    STREAMS["out"] = fout
    return fin, (_MuteBye(fout) if knobs.get("mute_bye") or knobs.get("bad_bye") else fout)


B.claim_stdio = _claim_and_keep              # main_with looks the name up in base's globals


def live_dir(arm):
    return os.path.join(shared, "live", arm)


def seen_stage():
    try:
        with open(os.path.join(shared, "stage.txt"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def log_op(op, **kw):
    os.makedirs(os.path.join(shared, "ops"), exist_ok=True)
    with open(os.path.join(shared, "ops", str(os.getpid()) + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"op": op, "stage": seen_stage(), "arm": spec["arm"], "run": spec["run"], "unit": spec["unit"],
                            "t": time.time(), **kw}) + "\n")


class H:
    def __init__(self):
        self.first, self.items, self.marker = True, [], spec["run"] + "/" + spec["unit"]
        if spec["stage"] == "read":
            store = os.path.join(spec["write_dir"], "store")
            with open(os.path.join(store, "items.json"), encoding="utf-8") as f:
                data = json.load(f)
            self.items, self.marker = data["items"], data["marker"]
        if knobs.get("heartbeat"):
            threading.Thread(target=self._hb, daemon=True).start()

    def _hb(self):
        p = os.path.join(shared, "hb", spec["arm"] + "." + spec["run"] + "." + spec["unit"])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        while True:
            with open(p, "w") as f:
                f.write(repr(time.time()))
            time.sleep(0.1)

    def hello(self):
        os.makedirs(os.path.join(shared, "canary"), exist_ok=True)            # R-HOME-CANARY: seen at the child's start
        with open(os.path.join(shared, "canary", "-".join([spec["arm"], spec["run"], spec["unit"], spec["stage"]])),
                  "w") as f:
            f.write("yes" if os.path.isfile(os.path.join(os.environ.get("HOME", ""), ".claude", "CLAUDE.md")) else "no")
        if knobs.get("bad_hello"):                    # another arm's hello: a protocol break the scheduler kills
            return {"protocol": B.PROTOCOL, "arm": "not-" + spec["arm"], "stage": spec["stage"], "pid": os.getpid()}
        if spec["stage"] == "write":                  # a live WRITE child: its marker goes at end_write
            os.makedirs(live_dir(spec["arm"]), exist_ok=True)
            open(os.path.join(live_dir(spec["arm"]), spec["run"] + "." + spec["unit"]), "w").close()
        stage = "both" if knobs.get("hello_both") else spec["stage"]   # a-mem's and langmem's one process (B-HELLO)
        arm = knobs.get("hello_arm") or spec["arm"]           # another arm's name, all else right (BHd)
        return {"protocol": B.PROTOCOL, "arm": arm, "stage": stage, "pid": os.getpid()}

    def write(self, item, date=None):
        log_op("write")
        if self.first:
            self.first = False
            t = time.time()
            while time.time() - t < 8 and len(os.listdir(live_dir(spec["arm"]))) < spec["expect"]:
                time.sleep(0.02)
            others = {a: len(os.listdir(os.path.join(shared, "live", a)))
                      for a in os.listdir(os.path.join(shared, "live")) if a != spec["arm"]}
            os.makedirs(os.path.join(shared, "seen"), exist_ok=True)
            with open(os.path.join(shared, "seen", spec["arm"] + "." + spec["run"] + "." + spec["unit"] + ".json"), "w") as f:
                json.dump({"own": len(os.listdir(live_dir(spec["arm"]))), "others": others}, f)
            passed = os.path.join(shared, "passed", spec["arm"])
            os.makedirs(passed, exist_ok=True)
            open(os.path.join(passed, spec["run"] + "." + spec["unit"]), "w").close()
        if knobs.get("sleep_write_s"):
            time.sleep(knobs["sleep_write_s"])
        if knobs.get("die_on_write"):
            os._exit(7)
        if knobs.get("close_stdout"):                 # the protocol stream ends, the process lives on
            STREAMS["out"].close()
            time.sleep(30)
        self.items.append(item)
        return {"op_id": item.get("item_id")}

    def end_write(self):
        passed, t = os.path.join(shared, "passed", spec["arm"]), time.time()
        while time.time() - t < 8 and len(os.listdir(passed)) < spec["expect"]:
            time.sleep(0.02)                  # no live marker leaves before every peer has counted it
        os.remove(os.path.join(live_dir(spec["arm"]), spec["run"] + "." + spec["unit"]))
        os.makedirs(os.path.join(os.getcwd(), "store"), exist_ok=True)
        with open(os.path.join(os.getcwd(), "store", "items.json"), "w", encoding="utf-8") as f:
            json.dump({"items": self.items, "marker": self.marker}, f)
        return {"footprint": len(self.items), "seal": {"sha256": "0" * 64}}

    def read(self, qid, query, k=10):
        log_op("read", cwd=os.getcwd(), marker=self.marker)
        fails = os.path.join(shared, "fails", spec["arm"] + "." + spec["run"] + "." + spec["unit"])
        if knobs.get("fail_reads"):
            n = len(os.listdir(fails)) if os.path.isdir(fails) else 0
            if n < knobs["fail_reads"]:
                os.makedirs(fails, exist_ok=True)
                open(os.path.join(fails, str(n)), "w").close()
                raise RuntimeError("upstream 503 after the client's own retries")
        return {"qid": qid, "items": self.items[:k], "marker": self.marker}

    def counters(self):
        if knobs.get("counters_fail"):
            raise RuntimeError("the product's counter is gone")
        return {"stage": spec["stage"], "items": len(self.items), "pid": os.getpid(), "t": time.time()}


sys.exit(B.main_with(H))
'''

TMP = Path(tempfile.mkdtemp(prefix="nvt3_turns_"))
POLY = TMP / "polygon"
SYSTEM = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32",) if os.name == "nt" else (Path("/usr/bin"),)
EXC = {sys.executable: "the test interpreter"}
if getattr(sys, "_base_executable", sys.executable) != sys.executable:
    EXC[sys._base_executable] = "the test interpreter's base"
(TMP / "owner_home").mkdir()
C = L.Contract(polygon_root=POLY, runs_root=POLY / "runs" / "v3", repo_root=ROOT, owner_home=TMP / "owner_home",
               secrets_dir=TMP / "secrets", quarantine_root=TMP / "quarantine", conservation_root=TMP / "conservation",
               system_dirs=SYSTEM, binary_exceptions=EXC)
FAKE_DIR = POLY / "_fake"
#: B-VENV: the fake arms run on the BASE interpreter. A venv's python.exe on Windows is a launcher whose child - the
#: real interpreter - holds the pipes too, so a stream the arm closes is no end-of-file until that child exits (the
#: auditor's gate of a5999f2: under a venv the B-RC row was red 3/3, under the base green 3/3). The contract already
#: names the base interpreter (EXC below).
ARM_PY = Path(getattr(sys, "_base_executable", None) or sys.executable)
FAKE_DIR.mkdir(parents=True)
BASE_SRC = (ROOT / "research" / "v3" / "arms" / "base.py").read_bytes()
(FAKE_DIR / "base.py").write_bytes(BASE_SRC)                    # the Q9 mirror: a byte copy, its sha asserted
(FAKE_DIR / "fake_arm.py").write_text(FAKE_ARM, encoding="utf-8")
SHARED = TMP / "shared"
(SHARED / "live").mkdir(parents=True)


class StubNative:
    """The native egress witness in place (its own suite, A2.2): registers any child; kills nothing itself."""
    jobs = None

    def register(self, pid, handle=None, label=None) -> bool:
        return True

    def kill_tree(self, root) -> bool:
        return False


class Clock:
    @staticmethod
    def utc():
        return dt.datetime.now(dt.timezone.utc)

    @staticmethod
    def monotonic():
        return time.monotonic()


def launcher(name: str, *, expect: int, knobs=None, store="disk"):
    def spec_for(stage, *, stand, run, unit, dirs, write_dirs):
        return {"arm": name, "run": run, "unit": unit, "stage": stage, "shared": str(SHARED), "expect": expect,
                "knobs": (knobs or {}).get((run, unit), {}),
                "write_dir": str(write_dirs.cwd) if write_dirs is not None else None}
    return SC.ChildArmLauncher(name, argv_for=lambda p, **_: [str(ARM_PY), "-B", str(FAKE_DIR / "fake_arm.py"), str(p)],
                               spec_for=spec_for, store_persistence=store, path_dirs=(str(ARM_PY.parent),))


status = SL.StatusLog(TMP / "STATUS", local_tz=dt.timezone.utc)
sched = SC.Scheduler(C, None, status, L, Clock(), None, tag="smoke", witnesses=SimpleNamespace(native=StubNative()),
                     parent_env=dict(os.environ), catcher_url="http://127.0.0.1:47001")
ARMS = ["a1", "a2"]
order, seed = SC.arm_order(ARMS, campaign_seed=7, stand="SX", block="b01")
status.stand("SX", "START", model="m", changelog="2026-09-10", order=1)
status.block_start("SX", "b01", units=["u1", "u2"], arm_order=order, seed=seed)
ids = {(a, r): status.start("SX", "b01", r, a, pid=os.getpid(), tag="smoke") for a in order for r in ("r1", "r2")}
OPS = lambda r, u: [{"item": {"item_id": f"{u}-i{k}", "text": "x"}, "date": None} for k in range(2)]  # noqa: E731

try:
    print("- the Q9 mirror -")
    check("B-VENV: the fake arms run on the base interpreter - no venv launcher between the scheduler and the arm, so a "
          "stream the arm closes is end-of-file at once",
          not (ARM_PY.parent / "pyvenv.cfg").exists() and not (ARM_PY.parent.parent / "pyvenv.cfg").exists(), str(ARM_PY))
    check("R-LAUNCHER: the fake arm starts no process of its own - its root's kill is its tree's",
          not any(w in FAKE_ARM for w in ("subprocess", "Popen", "os.system", "os.spawn", "multiprocessing", "os.exec")))
    check("the fake arm imports a byte copy of arms/base.py (its sha256 equals the repository's)",
          hashlib.sha256((FAKE_DIR / "base.py").read_bytes()).hexdigest() == hashlib.sha256(BASE_SRC).hexdigest())

    print("\n- T12: block size x runs units at once, one arm at a time -")
    recs = {}
    for a in order:
        recs[a] = sched.write_turn(launcher(a, expect=4), stand="SX", runs=["r1", "r2"], units=["u1", "u2"], ops_for=OPS,
                                   ceilings={"u1": 60.0, "u2": 60.0}, status_ids={r: ids[(a, r)] for r in ("r1", "r2")})
    seen = {p.stem: json.loads(p.read_text()) for p in (SHARED / "seen").iterdir()}
    check("M-SCHED-concurrency-asym: every child of every arm saw exactly 4 live peers of its own arm (2 units x 2 runs)",
          len(seen) == 8 and all(v["own"] == 4 for v in seen.values()), json.dumps(seen))
    check("... and no live child of the other arm (their turns never overlap)",
          all(all(n == 0 for n in v["others"].values()) for v in seen.values()), json.dumps(seen))
    r11 = recs[order[0]][("r1", "u1")]
    check("the unit record: spawn id, pid, each write's op id and times, footprint, exit code 0, active seconds",
          r11.spawn_id and r11.pid and [o["op_id"] for o in r11.ops] == ["u1-i0", "u1-i1"]
          and all(o["ok"] and o["t0"] <= o["t1"] for o in r11.ops) and r11.footprint == 2 and r11.rc == 0
          and r11.active_s > 0 and r11.aborted is None, str(r11))
    c11 = r11.counters if isinstance(r11.counters, dict) else {}
    check("B-WCTR: the write stage asks its child for counters after end_write, and the unit record carries them",
          (c11.get("stage"), c11.get("items"), c11.get("pid")) == ("write", 2, r11.pid), str(r11.counters))
    check("B-WCTR (1): the counters request is a service request - no write op of the unit, and end_write_utc is the "
          "moment end_write returned, before counters was asked (R9 background_writes cannot see it)",
          [o["op_id"] for o in r11.ops] == ["u1-i0", "u1-i1"] and isinstance(c11.get("t"), float)
          and dt.datetime.fromisoformat(r11.end_write_utc).timestamp() <= c11["t"], f"{r11.end_write_utc} {c11}")
    base_shas = {p.read_text() for p in (SHARED / "base_sha").iterdir()} if (SHARED / "base_sha").exists() else set()
    check("the Q9 mirror as the children imported it: every fake arm's own base.py hashed to the repository's",
          base_shas == {hashlib.sha256(BASE_SRC).hexdigest()}, str(base_shas))
    for a in order:
        for r in ("r1", "r2"):
            status.end(ids[(a, r)], rc=0, wall_s=1.0, units=2, out=f"runs/{a}.{r}.json")
    status.block_end("SX", "b01")

    print("\n- T11 ceiling and T21 crash (D2) -")
    order2, seed2 = SC.arm_order(["a1"], campaign_seed=7, stand="SX", block="b02")
    status.block_start("SX", "b02", units=["u3", "u4", "u5"], arm_order=order2, seed=seed2)
    sid = status.start("SX", "b02", "r1", "a1", pid=os.getpid(), tag="smoke")
    knobs = {("r1", "u4"): {"sleep_write_s": 30, "heartbeat": True}, ("r1", "u5"): {"die_on_write": True}}
    t0 = time.monotonic()
    rs = sched.write_turn(launcher("a1", expect=1, knobs=knobs), stand="SX", runs=["r1"], units=["u3", "u4", "u5"],
                          ops_for=OPS, ceilings={"u3": 60.0, "u4": 4.0, "u5": 60.0}, status_ids={"r1": sid})
    took = time.monotonic() - t0
    lines = (TMP / "STATUS").read_text(encoding="utf-8").splitlines()
    ua = [x for x in lines if " UNIT-ABORT SX/b02/r1/a1/" in x]
    check("M-SCHED-ceiling-noabort: the unit past its ceiling is UNIT-ABORT reason=ceiling, and the turn does not wait "
          "its 30 s", any(" UNIT-ABORT SX/b02/r1/a1/u4 reason=ceiling utc=" in x for x in ua) and took < 25
          and rs[("r1", "u4")].aborted == "ceiling", f"{ua} {took:.1f}s")
    hb = SHARED / "hb" / "a1.r1.u4"
    before = hb.read_text() if hb.exists() else ""
    time.sleep(1.2)
    check("... its tree is killed: the heartbeat stops", hb.exists() and hb.read_text() == before, before)
    check("T21: the child that died mid-write is UNIT-ABORT reason=crash with its exit code 7 (D2)",
          any(" UNIT-ABORT SX/b02/r1/a1/u5 reason=crash rc=7 utc=" in x for x in ua) and rs[("r1", "u5")].aborted == "crash"
          and rs[("r1", "u5")].rc == 7, str(ua))
    check("the healthy unit of the same turn is untouched", rs[("r1", "u3")].aborted is None and rs[("r1", "u3")].footprint == 2)
    status.end(sid, rc=0, wall_s=5.0, units=3, out="runs/a1.b02.json")
    check("END lists the aborted units, crash and ceiling (Q1)", " aborted=u5,u4 " in (TMP / "STATUS").read_text(
        encoding="utf-8").splitlines()[-1] or " aborted=u4,u5 " in (TMP / "STATUS").read_text(encoding="utf-8").splitlines()[-1],
        (TMP / "STATUS").read_text(encoding="utf-8").splitlines()[-1])
    status.block_end("SX", "b02")

    print("\n- B-WCTR (2): a write child that does not answer counters -")
    order_c, seed_c = SC.arm_order(["a1"], campaign_seed=7, stand="SX", block="b02c")
    status.block_start("SX", "b02c", units=["u7", "u8"], arm_order=order_c, seed=seed_c)
    sc_id = status.start("SX", "b02c", "r1", "a1", pid=os.getpid(), tag="smoke")
    rcw = sched.write_turn(launcher("a1", expect=1, knobs={("r1", "u8"): {"counters_fail": True}}), stand="SX",
                           runs=["r1"], units=["u7", "u8"], ops_for=OPS, ceilings={"u7": 60.0, "u8": 60.0},
                           status_ids={"r1": sc_id})
    u7, u8 = rcw[("r1", "u7")], rcw[("r1", "u8")]
    ua8 = [x for x in (TMP / "STATUS").read_text(encoding="utf-8").splitlines() if " UNIT-ABORT SX/b02c/r1/a1/u8 " in x]
    check("B-WCTR (2): no counters from a write child is the unit's error by name - UNIT-ABORT, never a None that "
          "passes", u8.aborted == "crash" and "counters" in (u8.error or "") and u8.counters is None and len(ua8) == 1
          and u7.aborted is None and (u7.counters or {}).get("items") == 2, f"{u8.error} {u8.aborted} {ua8}")
    status.end(sc_id, rc=0, wall_s=1.0, units=2, out="runs/a1.b02c.json")
    status.block_end("SX", "b02c")

    print("\n- Q25(4): a memory-store arm keeps its process -")
    order3, seed3 = SC.arm_order(["am"], campaign_seed=7, stand="SX", block="b03")
    status.block_start("SX", "b03", units=["u6"], arm_order=order3, seed=seed3)
    sm = status.start("SX", "b03", "r1", "am", pid=os.getpid(), tag="smoke")
    rm = sched.write_turn(launcher("am", expect=1, store="memory"), stand="SX", runs=["r1"], units=["u6"], ops_for=OPS,
                          ceilings={"u6": 60.0}, status_ids={"r1": sm})[("r1", "u6")]
    check("B-WCTR: a memory-store arm's write stage records its counters too (the snapshot at end_write: OPS wrote 2)",
          (rm.counters or {}).get("stage") == "write" and (rm.counters or {}).get("items") == 2, str(rm.counters))
    check("the memory-store unit's client is kept, its child still alive and answering",
          rm.client is not None and rm.client.child.process.poll() is None
          and rm.client.request("read", timeout=10, qid="q", query="x")["items"][0]["item_id"] == "u6-i0")
    rm.client.close(timeout=10)
    status.end(sm, rc=0, wall_s=1.0, units=1, out="runs/am.json")
    status.block_end("SX", "b03")
    check("the STATUS file stays valid for the writer's own replay", SL.self_check(TMP / "STATUS") == [],
          str(SL.self_check(TMP / "STATUS")))

    print("\n- A6: two blocks of a stand - the barrier, the stages, the own-run store, the order -")
    EV: list = []

    class FakeProxyCtl:
        def stage(self, block, stage):
            live = sum(len(os.listdir(SHARED / "live" / a)) for a in os.listdir(SHARED / "live"))
            EV.append(("stage", block, stage, live))
            tmp = SHARED / "stage.tmp"
            tmp.write_text(stage or "", encoding="utf-8")
            os.replace(tmp, SHARED / "stage.txt")

    class FakeWitnesses:
        native = StubNative()

        def begin_check(self, cid):
            EV.append(("begin_check", cid))

        def end_check(self, cid):
            EV.append(("end_check", cid))
            return {"check_id": cid, "complete": True}

    class FakeOllama:
        def __init__(self):
            self.resident = {"nvt3-bge-m3-d1:latest", "qwen3:8b"}

        def ps(self):
            return sorted(self.resident)

        def unload(self, model, *, embedder=False):
            EV.append(("unload", model))
            self.resident.discard(model)
            return "unloaded"

    class Hooks:
        def barrier_read(self, stand, block):
            EV.append(("barrier_read", block))
            return {"changelog": "2026-09-10"}

    for sub in ("live", "passed", "seen", "ops", "canary"):
        shutil.rmtree(SHARED / sub, ignore_errors=True)
    (SHARED / "live").mkdir()
    status2 = SL.StatusLog(TMP / "STATUS2", local_tz=dt.timezone.utc)
    CAN = L.Canaries.generate()
    s2 = SC.Scheduler(C, FakeProxyCtl(), status2, L, Clock(), FakeOllama(), tag="smoke", witnesses=FakeWitnesses(),
                      parent_env=dict(os.environ), catcher_url="http://127.0.0.1:47001", hooks=Hooks(),
                      canaries=tuple(CAN.values.values()), home_canaries=CAN)
    arms6 = {"a1": launcher("a1", expect=4), "a2": launcher("a2", expect=4), "a3": launcher("a3", expect=4, store="memory")}
    answers = []

    def answer(arm, run, unit, req, got):
        answers.append((arm, run, unit, req.qid, got.get("marker")))
        return {"sha256": "1" * 64}

    SP = SC.StandPlan(stand="SY", runs=("r1", "r2"), launchers=arms6, campaign_seed=20260927,     # b01: a3, a1, a2
                      unit_tokens={u: 1000 for u in ("v1", "v2", "v3", "v4")}, medians={},
                      write_ops=lambda a, r, u: OPS(r, u), read_plan=lambda a, u: [SC.ReadReq(qid=f"{u}-q1-{a}", query="x")],
                      answer=answer, embed_tag="nvt3-bge-m3-d1:latest", commit="c" * 40, dirty=False,
                      record_extra=lambda a, r, us: {"arm": a, "run": r, "units": list(us)})
    status2.stand("SY", "START", model="m", changelog="2026-09-10", order=1)
    res1, e_b1 = attempt(lambda: s2.run_block(SP, SC.BlockPlan(block="b01", units=("v1", "v2"))))
    res2, e_b2 = attempt(lambda: s2.run_block(SP, SC.BlockPlan(block="b02", units=("v3", "v4"), barrier_read=True)))
    attempt(lambda: status2.stand("SY", "END", model="m", changelog="2026-09-10"))
    check("both blocks of the stand ran to their END (a disk arm, and a memory-store arm whose write stage keeps its "
          "process)", e_b1 is None and e_b2 is None, f"{e_b1!r} {e_b2!r}")
    res1 = res1 or {"order": [], "write": {}, "questions": {}}
    res2 = res2 or {"order": [], "write": {}, "questions": {}}
    ops = [json.loads(x) for f in (SHARED / "ops").iterdir() for x in f.read_text(encoding="utf-8").splitlines()]
    check("T13: every write op saw the stage 'write' and every read op 'questions' (the proxy's stamp)",
          ops and all(o["stage"] == ("write" if o["op"] == "write" else "questions") for o in ops),
          str([o for o in ops if o["stage"] != ("write" if o["op"] == "write" else "questions")][:3]))
    st = [(e[1], e[2]) for e in EV if e[0] == "stage"]
    check("T13: per block the stage sequence is exactly write, questions, reset",
          st == [("SY/b01", "write"), ("SY/b01", "questions"), (None, None),
                 ("SY/b02", "write"), ("SY/b02", "questions"), (None, None)], str(st))
    check("T13: no write child is live at the switch to questions",
          all(e[3] == 0 for e in EV if e[0] == "stage" and e[2] == "questions"), str([e for e in EV if e[0] == "stage"]))
    reads = [o for o in ops if o["op"] == "read"]
    check("T14: every read answers from its OWN run's store (the marker is its own <run>/<unit>)",
          reads and all(o["marker"] == f"{o['run']}/{o['unit']}" for o in reads)
          and all(m == f"{r}/{u}" for _a, r, u, _q, m in answers), str([o for o in reads if o["marker"] != f"{o['run']}/{o['unit']}"][:3]))
    check("Q-12-3: each arm's reads are its own - the read plan is asked for (arm, unit), and every arm read every unit "
          "with its own questions", answers and all(q_.endswith(f"-{a_}") for a_, _r, _u, q_, _m in answers)
          and {(a_, u_) for a_, _r, u_, _q, _m in answers} == {(a_, u_) for a_ in arms6 for u_ in ("v1", "v2", "v3", "v4")},
          str(sorted({(a_, q_) for a_, _r, _u, q_, _m in answers})[:6]))
    seen_c = {p.name: p.read_text() for p in (SHARED / "canary").iterdir()} if (SHARED / "canary").exists() else {}
    sy = [json.loads(x) for x in L.spawns_log(C).read_text(encoding="utf-8").splitlines() if '"stand": "SY"' in x]
    want_sha = {k: hashlib.sha256(CAN.values[k].encode()).hexdigest() for k in SC.HOME_CANARY_FILES}
    log_bytes = L.spawns_log(C).read_bytes()
    check("R-HOME-CANARY: every child the stand spawned found the canaries in its fake home at its start - every arm, "
          "both stages", len(seen_c) == len(sy) > 0 and set(seen_c.values()) == {"yes"},
          f"{len(seen_c)} {len(sy)} {sorted(set(seen_c.values()))}")
    check("R-HOME-CANARY: each spawn record names the planted files and each value's sha256 - and no value is in the log",
          sy and all(e.get("home_canaries") == {"files": sorted(SC.HOME_CANARY_FILES.values()), "sha256": want_sha}
                     for e in sy) and not any(v.encode() in log_bytes for v in CAN.values.values()),
          str(sy[0].get("home_canaries") if sy else None))
    disk_reads = [o for o in reads if o["arm"] in ("a1", "a2")]
    check("T14: a disk arm reads in a fresh <unit>.q process, never in the write stage's directory",
          disk_reads and all(Path(o["cwd"]).name == f"{o['unit']}.q" for o in disk_reads), str(disk_reads[:2]))
    lines2 = (TMP / "STATUS2").read_text(encoding="utf-8").splitlines()
    bs = [x for x in lines2 if " BLOCK SY/b01 START " in x][0]
    order_line = bs.split(" arm_order=")[1].split()[0].split(",")
    check("T16: STATUS passes the writer's own replay; BLOCK START's order is D4's; every START names the scheduler's pid",
          SL.self_check(TMP / "STATUS2") == [] and order_line == res1["order"]
          and all(f" pid={os.getpid()} " in x for x in lines2 if x.split()[2] == "START" and x.split()[3].count("/") == 3),
          str(SL.self_check(TMP / "STATUS2")))
    first_write = {a: min((o["t"] for o in ops if o["arm"] == a and o["op"] == "write" and o["unit"] in ("v1", "v2")),
                          default=0.0)
                   for a in arms6}
    turns = sorted(first_write, key=first_write.get)
    check("T16: the arms took their write turns in the block's seeded order - which here is NOT the sorted order",
          turns == res1["order"] and res1["order"] != sorted(arms6), f"turns {turns}, seeded {res1['order']}")
    wev = [e for e in EV if e[0] in ("begin_check", "end_check", "barrier_read")]
    check("T17 (Q11): the barrier read of block 2 comes after block 1's check ended and before block 2's began",
          wev == [("begin_check", "SY.b01"), ("end_check", "SY.b01"), ("barrier_read", "b02"), ("begin_check", "SY.b02"),
                  ("end_check", "SY.b02")], str(wev))
    mem_w = {(o["run"], o["unit"]) for o in ops if o["arm"] == "a3" and o["op"] == "write"}
    q3 = res1["questions"].get("a3", {})
    check("T18: the memory-store arm reads from the process that wrote (the same pid), and spawns no read process",
          all(q3[k]["pid"] == res1["write"]["a3"][k].pid and q3[k]["spawn_id"] is None for k in q3) and len(q3) == 4,
          str({k: (q3[k]["pid"], res1["write"]["a3"][k].pid) for k in q3}))
    q1 = res1["questions"].get("a1", {})
    check("B-WCTR: a memory-store arm's question counters come from the process that wrote, marked as including the "
          "write snapshot; a disk arm's come from its own read process, marked as not",
          q3 and q1 and all(v.get("counters_include_write") is True for v in q3.values())
          and all(v.get("counters_include_write") is False for v in q1.values()),
          str({k: v.get("counters_include_write") for k, v in list(q3.items())[:2] + list(q1.items())[:2]}))
    check("the GPU holds the embedder alone at the block (the other model unloaded, never the embedder)",
          ("unload", "qwen3:8b") in EV and ("unload", "nvt3-bge-m3-d1:latest") not in EV)
    rj = C.runs_root / "_launch" / "records.jsonl"
    recs = [json.loads(x) for x in rj.read_text(encoding="utf-8").splitlines()] if rj.exists() else []
    check("every arm-run's raw record is written (out=), chained, and named by its END",
          len(recs) == 12 and L.verify_chain(C.runs_root / "_launch" / "records.jsonl")
          and all((C.runs_root / r["path"]).is_file() for r in recs)
          and all(f" out={r['path']} " in " ".join(lines2) + " " for r in recs), str(recs[:1]))
    one = json.loads((C.runs_root / recs[0]["path"]).read_text(encoding="utf-8")) if recs else {"units": {}}
    check("the record holds each unit's write (ops, footprint, end_write time) and its questions, measured inside "
          "START..END (Q2)", set(one["units"]) and all(v["write"]["footprint"] == 2 and v["write"]["end_write_utc"]
                                                        for v in one["units"].values())
          and one.get("measured_at", {}).get("commit") == "c" * 40, str(one)[:300])
    check("B-WCTR: the run record carries each unit's write-stage counters",
          set(one["units"]) and all((v["write"].get("counters") or {}).get("stage") == "write"
                                    for v in one["units"].values()), str([v["write"].get("counters")
                                                                          for v in one["units"].values()])[:200])
    plans = [json.loads((C.runs_root / r["path"]).read_text(encoding="utf-8")) for r in recs]
    check("the A5 condition: each run record carries the plan's own record of ITS arm-run and block (record_extra) "
          "under 'plan'", plans and all(p_.get("plan") == {"arm": p_["arm"], "run": p_["run"], "units": sorted(p_["units"])}
                                        for p_ in plans), str([p_.get("plan") for p_ in plans[:2]]))

    print("\n- A7: run_stand - the tree check, STAND lines, the gate, the judges one model at a time -")

    class GpuOllama:
        """Residency with a load log: a judge's run loads its model; loading beside another model is a violation."""

        def __init__(self):
            self.resident, self.violations, self.log = {"qwen3:8b", "nvt3-bge-m3-d1:latest"}, [], []

        def ps(self):
            return sorted(self.resident)

        def unload(self, model, *, embedder=False):
            self.log.append(("unload", model, embedder))
            self.resident.discard(model)
            return "unloaded"

        def load(self, model):
            if self.resident - {model}:
                self.violations.append((model, sorted(self.resident)))
            self.log.append(("load", model))
            self.resident.add(model)

    class Judge:
        def __init__(self, name, model, gpu, ev, status_path, stand):
            self.name, self.model, self.gpu, self.ev = name, model, gpu, ev
            self.status_path, self.stand = status_path, stand

        def run(self):
            self.gpu.load(self.model)
            ended = f" STAND {self.stand} END " in self.status_path.read_text(encoding="utf-8")
            self.ev.append(("judge", self.name, ended))

    class GateScript:
        """Refuses the first `closed` asks, then admits (the IncidentGate shape the scheduler reads). ``spawns``, when
        given, counts the stand's spawns so far: each refusing ask records it - a unit spawned while the gate refused
        shows as a count above 0."""

        def __init__(self, closed, spawns=None):
            self.closed, self.asked, self.spawns, self.at_refusal = closed, 0, spawns, []

        def admits_new_unit(self):
            self.asked += 1
            if self.asked <= self.closed and self.spawns is not None:
                self.at_refusal.append(self.spawns())
            return self.asked > self.closed

    class StandHooks:
        def __init__(self, ev, tree_ok=True, gate=None):
            self.ev, self.tree_ok, self.gate = ev, tree_ok, gate

        def barrier_read(self, stand, block):
            self.ev.append(("barrier_read", block))
            cl = getattr(self, "changelogs", {}).get(block, "2026-09-10")
            return {} if cl is None else {"changelog": cl}

        def model_probe(self):
            self.ev.append(("model_probe",))
            return "deepseek-v4-flash"

        def tree_check(self):
            self.ev.append(("tree_check",))
            trees = getattr(self, "trees", None)
            ok = trees.pop(0) if trees else self.tree_ok
            return {"clean": ok, "problems": [] if ok else ["HEAD is not the anchor"]}

        def preflight(self, stand):
            self.ev.append(("preflight", stand))
            return {"balance_ok": True}

    def stand_world(tag, *, tree_ok=True, gate=None, stand="SZ", sfile="STATUS3"):
        ev = []
        for sub in ("live", "passed", "seen", "ops"):
            shutil.rmtree(SHARED / sub, ignore_errors=True)
        (SHARED / "live").mkdir()
        gpu = GpuOllama()
        st = SL.StatusLog(TMP / sfile, local_tz=dt.timezone.utc)

        class W(FakeWitnesses):
            def begin_check(self, cid):
                ev.append(("begin_check", cid))

            def end_check(self, cid):
                ev.append(("end_check", cid))
                return {"check_id": cid, "complete": True}

        s = SC.Scheduler(C, FakeProxyCtl(), st, L, Clock(), gpu, tag=tag, witnesses=W(), parent_env=dict(os.environ),
                         catcher_url="http://127.0.0.1:47001", hooks=StandHooks(ev, tree_ok, gate),
                         home_canaries=L.Canaries.generate() if tag == "scored" else None)
        sp = SC.StandPlan(stand=stand, runs=("r1",), launchers={"a1": launcher("a1", expect=1)}, campaign_seed=20260927,
                          unit_tokens={u: 1000 for u in ("w1", "w2")}, medians={("a1", stand): 0.001},
                          write_ops=lambda a, r, u: OPS(r, u), read_plan=lambda a, u: [SC.ReadReq(qid=f"{u}-q", query="x")],
                          answer=lambda *a_: {"sha256": "2" * 64}, embed_tag="nvt3-bge-m3-d1:latest", commit="c" * 40,
                          dirty=False)
        judges = [Judge("J1", "gpt-oss:20b", gpu, ev, TMP / sfile, stand), Judge("J2", "qwen3:32b", gpu, ev, TMP / sfile, stand)]
        return s, sp, judges, ev, gpu, st

    s, sp, judges, ev, gpu, st = stand_world("smoke", stand="SZ", sfile="STATUS3")
    res = s.run_stand(sp, [SC.BlockPlan(block="b01", units=("w1",)), SC.BlockPlan(block="b02", units=("w2",))],
                      judges=judges, order=1)
    lines3 = (TMP / "STATUS3").read_text(encoding="utf-8").splitlines()
    kinds = [" ".join(x.split()[2:4]) if x.split()[2] in ("STAND", "BLOCK") else x.split()[2] for x in lines3]
    check("T23: the tree check (inside its own check) and the barrier read come before STAND START; STAND END after the "
          "last block, after the end tree check", [e for e in ev if e[0] in ("begin_check", "tree_check", "barrier_read",
                                                                             "end_check")][:4]
          == [("begin_check", "SZ.tree-start"), ("tree_check",), ("end_check", "SZ.tree-start"), ("barrier_read", "start")]
          and lines3[0].split()[2:5] == ["STAND", "SZ", "START"] and lines3[-1].split()[2:5] == ["STAND", "SZ", "END"],
          f"{ev[:6]} {lines3[0][:60]}")
    check("T19: a smoke stand never judges", not [e for e in ev if e[0] == "judge"] and res.get("judged") == [])
    check("Q3 (the auditor): each tree check's own witness record rides on its verdict (witness_check) - the STAND "
          "START and STAND END windows are read, never dropped",
          (res.get("tree_start") or {}).get("witness_check") == {"check_id": "SZ.tree-start", "complete": True}
          and (res.get("tree_end") or {}).get("witness_check") == {"check_id": "SZ.tree-end", "complete": True},
          f"{(res.get('tree_start') or {}).get('witness_check')} {(res.get('tree_end') or {}).get('witness_check')}")
    s, sp, judges, ev, gpu, st = stand_world("smoke", stand="SJ", sfile="STATUS4")
    s.tag = "scored"
    st.campaign_start(anchor="c" * 40, prereg="d" * 64, freeze="e" * 64)
    try:
        res = s.run_stand(sp, [SC.BlockPlan(block="b01", units=("w1",))], judges=judges, order=2)
        err_ = None
    except Exception as e:  # noqa: BLE001
        res, err_ = {}, repr(e)
    j = [e for e in ev if e[0] == "judge"]
    check("T19: a scored stand's judges run after STAND END, J1 then J2", j == [("judge", "J1", True), ("judge", "J2", True)]
          and res.get("judged") == ["J1", "J2"], f"{err_} {ev[-6:]}")
    check("T15 (M-SCHED-gpu-two-models): no model was ever loaded beside another, and the GPU is empty after the judges",
          gpu.violations == [] and gpu.ps() == [], f"{gpu.violations} {gpu.log}")
    d1 = [x for x in gpu.log if x[0] == "unload" and x[1] == "nvt3-bge-m3-d1:latest"]
    check("R-UNL: the resident D1 embedder is unloaded before the judges through /api/embed (embedder=True), never "
          "the generate endpoint", d1 == [("unload", "nvt3-bge-m3-d1:latest", True)], str(gpu.log))
    s, sp, judges, ev, gpu, st = stand_world("scored", tree_ok=False, stand="ST", sfile="STATUS5")
    _r, e5 = attempt(lambda: s.run_stand(sp, [SC.BlockPlan(block="b01", units=("w1",))], judges=judges, order=3))
    refusal = str(e5) if isinstance(e5, SC.SchedulerError) else f"not refused: {e5!r}"
    check("T23: a scored stand on a dirty tree is refused - no STAND START written",
          "tree" in refusal and not (TMP / "STATUS5").exists() or "tree" in refusal and "STAND ST START" not in
          (TMP / "STATUS5").read_text(encoding="utf-8"), refusal)
    s, sp, judges, ev, gpu, st = stand_world("smoke", tree_ok=False, stand="SU", sfile="STATUS6")
    r_ = s.run_stand(sp, [SC.BlockPlan(block="b01", units=("w1",))], judges=judges, order=4)
    check("... a smoke stand on a dirty tree runs, the tree check recorded", r_["tree_start"]["clean"] is False
          and "STAND SU START" in (TMP / "STATUS6").read_text(encoding="utf-8"))
    def sg_spawns():
        return sum(1 for x in L.spawns_log(C).read_text(encoding="utf-8").splitlines()
                   if json.loads(x).get("stand") == "SG")

    gate = GateScript(closed=3, spawns=sg_spawns)
    s, sp, judges, ev, gpu, st = stand_world("smoke", gate=gate, stand="SG", sfile="STATUS7")
    s.run_stand(sp, [SC.BlockPlan(block="b01", units=("w1",))], judges=judges, order=5)
    spawns = [json.loads(x) for x in L.spawns_log(C).read_text(encoding="utf-8").splitlines()]
    sg = [x for x in spawns if x.get("stand") == "SG"]
    check("T20: no unit spawns while the gate refuses - it asked until it admitted, then the unit ran (every refusing "
          "ask saw no spawn of the stand yet: a unit spawned before its gate, or past it, is caught)",
          gate.asked >= 4 and len(sg) >= 1 and gate.at_refusal == [0, 0, 0],
          f"asked {gate.asked}, spawns {len(sg)}, at refusal {gate.at_refusal}")

    print("\n- A8 (W3): a failed read or answer is re-asked at most twice, at least 5 min apart; then unrecovered -")

    class VirtualClock:
        """Real UTC and monotonic time, plus the re-ask pauses - slept virtually, recorded."""

        def __init__(self):
            self.offset, self.sleeps = 0.0, []

        def utc(self):
            return dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=self.offset)

        def monotonic(self):
            return time.monotonic() + self.offset

        def sleep(self, s):
            self.sleeps.append(s)
            self.offset += s

    def w3_world(stand, sfile, knobs, answer, tag="smoke", gate=None):
        for sub in ("live", "passed", "seen", "ops", "fails"):
            shutil.rmtree(SHARED / sub, ignore_errors=True)
        (SHARED / "live").mkdir()
        vc = VirtualClock()
        st = SL.StatusLog(TMP / sfile, now=vc.utc, local_tz=dt.timezone.utc)
        s = SC.Scheduler(C, FakeProxyCtl(), st, L, vc, None, tag=tag, witnesses=FakeWitnesses(),
                         parent_env=dict(os.environ), catcher_url="http://127.0.0.1:47001",
                         hooks=SimpleNamespace(gate=gate) if gate is not None else None,
                         home_canaries=L.Canaries.generate() if tag == "scored" else None)
        if tag == "scored":
            st.campaign_start(anchor="c" * 40, prereg="d" * 64, freeze="e" * 64)
        sp = SC.StandPlan(stand=stand, runs=("r1",), launchers={"a1": launcher("a1", expect=1, knobs=knobs)},
                          campaign_seed=20260927, unit_tokens={"x1": 1000}, medians={("a1", stand): 0.0001},
                          write_ops=lambda a, r, u: OPS(r, u),
                          read_plan=lambda a, u: [SC.ReadReq(qid=f"{u}-q1", query="x"), SC.ReadReq(qid=f"{u}-q2", query="y")],
                          answer=answer, embed_tag=None, commit="c" * 40, dirty=False)
        st.stand(stand, "START", model="m", changelog="2026-09-10", order=1)
        res, err = attempt(lambda: s.run_block(sp, SC.BlockPlan(block="b01", units=("x1",))))
        if err is not None:
            return {"reads": [{"error": repr(err)}, {}], "aborted": f"raised {err!r}", "raised": err}, vc
        return res["questions"]["a1"][("r1", "x1")], vc

    q, vc = w3_world("SW1", "STATUS8", {("r1", "x1"): {"fail_reads": 1}}, lambda *a_: {"sha256": "3" * 64})
    check("T22: a read that failed once is re-asked after 5 min and recovers - the question is answered",
          [r.get("reasks") for r in q["reads"]] == [1, 0] and not any(r.get("unrecovered") for r in q["reads"])
          and vc.sleeps == [300.0], f"{q['reads']} {vc.sleeps}")
    q, vc = w3_world("SW2", "STATUS9", {("r1", "x1"): {"fail_reads": 3}}, lambda *a_: {"sha256": "3" * 64},
                     tag="scored")                        # a scored unit: its ceiling is the 600 s floor
    q["reads"] = list(q["reads"]) + [{}] * max(0, 2 - len(q["reads"]))   # an aborted unit read fewer
    check("M-SCHED-reask-spacing: three failures - two re-asks, 5 min apart - then unrecovered; the unit goes on with "
          "its next question", q["reads"][0].get("unrecovered") is True and q["reads"][0].get("reasks") == 2
          and vc.sleeps == [300.0, 300.0] and q["reads"][1].get("answer") and q["aborted"] is None,
          f"{q['reads']} {vc.sleeps} {q.get('aborted')}")
    check("the re-ask pauses are not the unit's active time - 2 x 5 virtual min under the 600 s ceiling abort nothing (D1)",
          q["aborted"] is None and "UNIT-ABORT" not in (TMP / "STATUS9").read_text(encoding="utf-8"))
    check("B-PAUSE: ... and the recorded active seconds leave them out too - END's wall and the pilot's median are D1's",
          q.get("active_s", 1e9) < 300, str(q.get("active_s")))
    calls = {"n": 0}

    def flaky_answer(arm, run, unit, req, got):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise SC.ReaskableError("reader: upstream 502")
        return {"sha256": "4" * 64}

    q, vc = w3_world("SW3", "STATUS10", {}, flaky_answer)
    check("an answer the reader could not give is re-asked the same way (two re-asks, then answered)",
          q["reads"][0].get("reasks") == 2 and q["reads"][0]["answer"] == {"sha256": "4" * 64} and vc.sleeps == [300.0, 300.0],
          f"{q['reads']} {vc.sleeps}")

    print("\n- B-REASK-HALT: the incident gate's halt ends a re-ask pause at once - the stand stops, it never re-asks a "
          "halted provider -")

    class HaltGate:
        """The gate driver's shape: admits every unit; halt_kind() reads None for its first ``clear`` asks, then '402'
        (the driver polls calls.jsonl on its own thread, so a halt can come before a pause or in the middle of one)."""

        def __init__(self, clear):
            self.clear, self.asked = clear, 0

        def admits_new_unit(self):
            return True

        def halt_kind(self):
            self.asked += 1
            return "402" if self.clear is not None and self.asked > self.clear else None

    def paid_402(*a_):
        raise SC.ReaskableError("reader: upstream 402")

    def halted_rows(sfile, q, vc):
        """(the error's halt, the ABORT lines, the UNIT-ABORT lines) of a stand stopped by the halt."""
        lines = (TMP / sfile).read_text(encoding="utf-8").splitlines()
        return (SC._halt_of(q.get("raised")), [x for x in lines if " ABORT " in x],
                [x for x in lines if " UNIT-ABORT " in x])

    q, vc = w3_world("SH1", "STATUS10h", {}, paid_402, gate=HaltGate(clear=0))
    h1, ab1, ua1 = halted_rows("STATUS10h", q, vc)
    check("B-REASK-HALT: an answer that failed while the gate is halted is never re-asked - no pause at all, the unit "
          "raises the halt, and the block's ABORT says reason=402 (a 402 is the provider's, never harness-error)",
          vc.sleeps == [] and h1 == "402" and len(ab1) == 1 and " reason=402" in ab1[0] and not ua1,
          f"sleeps={vc.sleeps[:4]}.. n={len(vc.sleeps)} halt={h1} {ab1} {ua1} {q.get('aborted')}")
    q, vc = w3_world("SH2", "STATUS10i", {}, paid_402, gate=HaltGate(clear=4))
    h2, ab2, _ua2 = halted_rows("STATUS10i", q, vc)
    check("B-REASK-HALT: a halt that comes during a pause ends it at the next GATE_POLL_S - 4 slices slept, not the 300 s "
          "spacing, and the halt is raised by name",
          vc.sleeps == [SC.GATE_POLL_S] * 4 and h2 == "402" and len(ab2) == 1 and " reason=402" in ab2[0],
          f"sleeps={vc.sleeps[:6]}.. n={len(vc.sleeps)} halt={h2} {ab2} {q.get('aborted')}")
    paid = {"n": 0}

    def paid_402_counted(*a_):
        paid["n"] += 1
        raise SC.ReaskableError("reader: upstream 402")

    n_slices = int(SC.REASK_SPACING_S / SC.GATE_POLL_S)
    q, vc = w3_world("SH4", "STATUS10k", {}, paid_402_counted, gate=HaltGate(clear=n_slices))
    h4, _ab4, _ua4 = halted_rows("STATUS10k", q, vc)
    check("B-REASK-HALT: a halt seen only at the end of a whole pause is still asked before the re-ask - the halted "
          "provider is called once, never re-asked",
          paid["n"] == 1 and len(vc.sleeps) == n_slices and h4 == "402",
          f"answers={paid['n']} n={len(vc.sleeps)} halt={h4} {q.get('aborted')}")
    calls["n"] = 0
    q, vc = w3_world("SH3", "STATUS10j", {}, flaky_answer, gate=HaltGate(clear=None))
    check("B-REASK-HALT: a gate that never halts changes nothing in W3 - two pauses of 300 s each (asked every "
          "GATE_POLL_S), the answer recovers, and the pauses are still no active time (D1)",
          q["reads"][0].get("reasks") == 2 and q["reads"][0].get("answer") == {"sha256": "4" * 64}
          and abs(sum(vc.sleeps) - 2 * SC.REASK_SPACING_S) < 1e-6 and max(vc.sleeps or [0.0]) <= SC.GATE_POLL_S
          and q.get("active_s", 1e9) < 300, f"{q['reads']} n={len(vc.sleeps)} sum={sum(vc.sleeps)} {q.get('active_s')}")

    print("\n- FIX-SCHED B-RC: a unit's exit code is the child's own; a kill of ours is SIGKILL; a refusal is no unit -")
    B_ = SC._arm_base()
    SC.DIED_WAIT_S = 1.0                     # the grace a child whose stream closed gets to exit on its own

    def aborts(path):
        return [x for x in path.read_text(encoding="utf-8").splitlines() if " UNIT-ABORT " in x]

    def refused_launcher(name):
        """A launcher whose child the launch contract refuses (the binary does not exist): no process is ever made."""
        return SC.ChildArmLauncher(name, argv_for=lambda p, **_: [str(FAKE_DIR / "no_such_arm.exe"), str(p)],
                                   spec_for=lambda *a_, **k_: {}, path_dirs=(str(Path(sys.executable).parent),))

    class FakeClient:
        """A UnitClient's shape with a scripted child: died with no code, a close that times out, a close with no code."""

        def __init__(self, name, stage, script):
            self.name, self.stage, self.script, self.killed, self.requests = name, stage, script, 0, []
            self.child = SimpleNamespace(spawn_id=f"fake-{stage}", process=SimpleNamespace(pid=4242, poll=lambda: 0))
            self.pid, self.dirs, self.arm = 4242, None, SimpleNamespace(poisoned=None)

        def request(self, op, *, timeout, **f):
            self.requests.append((op, timeout))
            if timeout is not None and timeout < 0:          # ArmClient's queue.get refuses it the same way
                raise ValueError("'timeout' must be a non-negative number")
            if op == "hello":
                return {"protocol": B_.PROTOCOL, "arm": self.name, "stage": self.stage}
            if op == "write" and self.script.get("advance"):
                vclock, secs = self.script["advance"]
                vclock.offset += secs                         # the op answered, but spent the unit's ceiling
            if op == "write" and self.script.get("died"):
                self.arm.poisoned = "died"
                raise B_.ArmDied("write: the child's stdout closed (exit None)")
            if op == "read" and self.script.get("read_timeout"):
                self.arm.poisoned = "timeout"
                raise B_.ArmTimeout("read: no answer within the unit's ceiling")
            return {"write": {"op_id": (f.get("item") or {}).get("item_id")}, "end_write": {"footprint": 1, "seal": None},
                    "read": {"items": []}, "counters": {}}[op]

        def close(self, *, timeout):
            if self.script.get("close_timeout"):
                raise subprocess.TimeoutExpired("fake-arm", timeout)
            return self.script.get("close_rc", 0)

        def kill_tree(self):
            self.killed += 1

        def exit_code(self, timeout=10.0):
            return self.script.get("exit_rc", 1 if self.killed else None)

    class FakeLauncher:
        reads_point = False

        def __init__(self, name, *, store="disk", scripts=None):
            self.name, self.store_persistence, self.scripts, self.opened = name, store, scripts or {}, []

        def open(self, stage, *, sched, stand, run, unit, write_dirs=None):
            c = FakeClient(self.name, stage, self.scripts.get((stage, run, unit), {}))
            self.opened.append(c)
            return c

    for sub in ("live", "passed", "seen", "ops", "hb"):
        shutil.rmtree(SHARED / sub, ignore_errors=True)
    (SHARED / "live").mkdir()
    st11 = SL.StatusLog(TMP / "STATUS11", local_tz=dt.timezone.utc)
    s11 = SC.Scheduler(C, None, st11, L, Clock(), None, tag="smoke", witnesses=SimpleNamespace(native=StubNative()),
                       parent_env=dict(os.environ), catcher_url="http://127.0.0.1:47001")
    st11.stand("SR", "START", model="m", changelog="2026-09-10", order=1)
    o11, sd11 = SC.arm_order(["a1"], campaign_seed=7, stand="SR", block="b01")
    st11.block_start("SR", "b01", units=["k1", "k2", "k3", "k4", "k5", "k6"], arm_order=o11, seed=sd11)
    sid11 = st11.start("SR", "b01", "r1", "a1", pid=os.getpid(), tag="smoke")
    kn = {("r1", "k1"): {"close_stdout": True, "heartbeat": True}, ("r1", "k2"): {"bad_hello": True}}
    r11s, e11 = attempt(lambda: s11.write_turn(launcher("a1", expect=1, knobs=kn), stand="SR", runs=["r1"],
                                                units=["k1", "k2"], ops_for=OPS, ceilings={"k1": 60.0, "k2": 60.0},
                                                status_ids={"r1": sid11}))
    r11s = r11s or {}
    ua11 = aborts(TMP / "STATUS11")
    k1 = r11s.get(("r1", "k1"))
    check("B-RC: a child whose stream closed and which did not exit is killed by us - UNIT-ABORT crash signal=SIGKILL, "
          "and no exit code of ours in the line or the record",
          any(" UNIT-ABORT SR/b01/r1/a1/k1 reason=crash signal=SIGKILL utc=" in x for x in ua11)
          and k1 is not None and k1.rc is None and getattr(k1, "signal", None) == "SIGKILL", f"{e11!r} {ua11} {k1}")
    hb11 = SHARED / "hb" / "a1.r1.k1"
    b11 = hb11.read_text() if hb11.exists() else ""
    time.sleep(1.2)
    check("... and its tree is dead (the heartbeat stopped)", hb11.exists() and hb11.read_text() == b11, b11)
    k2 = r11s.get(("r1", "k2"))
    check("B-RC: a child that broke the protocol at hello is killed by us - UNIT-ABORT crash signal=SIGKILL, no rc",
          any(" UNIT-ABORT SR/b01/r1/a1/k2 reason=crash signal=SIGKILL utc=" in x for x in ua11)
          and k2 is not None and k2.rc is None and getattr(k2, "signal", None) == "SIGKILL", f"{ua11} {k2}")
    u4 = rs[("r1", "u4")]
    check("B-RC: a unit killed at its ceiling keeps no exit code in its record - the code is our kill's, not the "
          "product's (signal SIGKILL)", u4.rc is None and getattr(u4, "signal", None) == "SIGKILL", str(u4))
    u5 = rs[("r1", "u5")]
    check("B-RC: a child that died on its own keeps its own code and no signal (T21's rc=7)",
          u5.rc == 7 and getattr(u5, "signal", "absent") is None, str(u5))
    _r, e = attempt(lambda: s11.write_turn(refused_launcher("a1"), stand="SR", runs=["r1"], units=["k3"], ops_for=OPS,
                                            ceilings={"k3": 60.0}, status_ids={"r1": sid11}))
    check("B-RC: a spawn the launch contract refused is a SchedulerError naming its unit - no UNIT-ABORT: it never ran",
          isinstance(e, SC.SchedulerError) and "k3" in str(e)
          and not any("/k3 " in x for x in aborts(TMP / "STATUS11")), repr(e))
    fl = FakeLauncher("a1", scripts={("write", "r1", "k4"): {"died": True, "exit_rc": None}})
    _r, e = attempt(lambda: s11.write_turn(fl, stand="SR", runs=["r1"], units=["k4"], ops_for=OPS, ceilings={"k4": 60.0},
                                            status_ids={"r1": sid11}))
    check("B-RC: a child that died and never gave its exit code is a SchedulerError - a crash line is never written "
          "without rc= or signal= (rc=-1 was invented)", isinstance(e, SC.SchedulerError) and "k4" in str(e)
          and not any("/k4 " in x for x in aborts(TMP / "STATUS11")) and fl.opened and fl.opened[0].killed >= 1,
          f"{e!r} {aborts(TMP / 'STATUS11')}")
    fl = FakeLauncher("a1", scripts={("write", "r1", "k5"): {"close_timeout": True}})
    r5, e = attempt(lambda: s11.write_turn(fl, stand="SR", runs=["r1"], units=["k5"], ops_for=OPS, ceilings={"k5": 60.0},
                                            status_ids={"r1": sid11}))
    check("B-RC: a child that does not exit after bye within its ceiling is UNIT-ABORT reason=ceiling - its "
          "TimeoutExpired is the unit's, never a harness error", e is None
          and any(" UNIT-ABORT SR/b01/r1/a1/k5 reason=ceiling utc=" in x for x in aborts(TMP / "STATUS11"))
          and r5[("r1", "k5")].aborted == "ceiling" and fl.opened[0].killed >= 1, f"{e!r} {aborts(TMP / 'STATUS11')}")
    bad_ops = lambda r, u: [{"id": 5, "item": {"item_id": f"{u}-i0", "text": "x"}}]  # noqa: E731 - a reserved field
    _r, e6 = attempt(lambda: s11.write_turn(launcher("a1", expect=1, knobs={("r1", "k6"): {"heartbeat": True}}),
                                             stand="SR", runs=["r1"], units=["k6"], ops_for=bad_ops,
                                             ceilings={"k6": 60.0}, status_ids={"r1": sid11}))
    hb6 = SHARED / "hb" / "a1.r1.k6"
    b6 = hb6.read_text() if hb6.exists() else ""
    time.sleep(1.2)
    check("B-OPEN: a write child whose request broke on our side (the client's ValueError) is killed, and the error "
          "goes on - no UNIT-ABORT for what was not the unit's", isinstance(e6, ValueError) and hb6.exists()
          and hb6.read_text() == b6 and not any("/k6 " in x for x in aborts(TMP / "STATUS11")), f"{e6!r} {b6}")
    st11.end(sid11, rc=0, wall_s=1.0, units=6, out="runs/sr.json")
    st11.block_end("SR", "b01")
    st11.stand("SR", "END", model="m", changelog="2026-09-10")
    check("the B-RC file passes the writer's own replay", SL.self_check(TMP / "STATUS11") == [],
          str(SL.self_check(TMP / "STATUS11")))
    vc17 = VirtualClock()
    st17 = SL.StatusLog(TMP / "STATUS17", now=vc17.utc, local_tz=dt.timezone.utc)
    s17 = SC.Scheduler(C, None, st17, L, vc17, None, tag="smoke", witnesses=SimpleNamespace(native=StubNative()),
                       parent_env=dict(os.environ), catcher_url="http://127.0.0.1:47001")
    st17.stand("SV", "START", model="m", changelog="2026-09-10", order=1)
    o17, sd17 = SC.arm_order(["a1"], campaign_seed=7, stand="SV", block="b01")
    st17.block_start("SV", "b01", units=["v1"], arm_order=o17, seed=sd17)
    sid17 = st17.start("SV", "b01", "r1", "a1", pid=os.getpid(), tag="smoke")
    fl17 = FakeLauncher("a1", scripts={("write", "r1", "v1"): {"advance": (vc17, 61.0)}})
    r17, e17 = attempt(lambda: s17.write_turn(fl17, stand="SV", runs=["r1"], units=["v1"], ops_for=OPS,
                                               ceilings={"v1": 60.0}, status_ids={"r1": sid17}))
    v1 = (r17 or {}).get(("r1", "v1"))
    check("B-BUDGET: a unit whose ceiling passed between two requests is UNIT-ABORT reason=ceiling - the next request "
          "never gets a spent (negative) timeout", e17 is None and v1 is not None and v1.aborted == "ceiling"
          and any(" UNIT-ABORT SV/b01/r1/a1/v1 reason=ceiling " in x for x in aborts(TMP / "STATUS17")),
          f"{e17!r} {aborts(TMP / 'STATUS17')}")

    class FrozenClock:
        """Time that moves only when told: a unit's remainder can be EXACTLY zero."""

        def __init__(self):
            self.offset = 0.0

        def monotonic(self):
            return 1000.0 + self.offset

        def utc(self):
            return dt.datetime(2026, 9, 27, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=self.offset)

        def sleep(self, s):
            self.offset += s

    fc = FrozenClock()
    stamps = [0]

    def st19_now():                               # STATUS's own clock: 1 ms further on every line
        stamps[0] += 1
        return dt.datetime(2026, 9, 27, tzinfo=dt.timezone.utc) + dt.timedelta(milliseconds=stamps[0])
    st19 = SL.StatusLog(TMP / "STATUS19", now=st19_now, local_tz=dt.timezone.utc)
    s19 = SC.Scheduler(C, None, st19, L, fc, None, tag="smoke", witnesses=SimpleNamespace(native=StubNative()),
                       parent_env=dict(os.environ), catcher_url="http://127.0.0.1:47001")
    st19.stand("SZ0", "START", model="m", changelog="2026-09-10", order=1)
    o19, sd19 = SC.arm_order(["a1"], campaign_seed=7, stand="SZ0", block="b01")
    st19.block_start("SZ0", "b01", units=["z1"], arm_order=o19, seed=sd19)
    sid19 = st19.start("SZ0", "b01", "r1", "a1", pid=os.getpid(), tag="smoke")
    fl19 = FakeLauncher("a1", scripts={("write", "r1", "z1"): {"advance": (fc, 60.0)}})
    r19, e19 = attempt(lambda: s19.write_turn(fl19, stand="SZ0", runs=["r1"], units=["z1"], ops_for=OPS,
                                               ceilings={"z1": 60.0}, status_ids={"r1": sid19}))
    z1 = (r19 or {}).get(("r1", "z1"))
    sent = fl19.opened[0].requests if fl19.opened else []
    check("B-BUDGET (F4): a remainder of EXACTLY zero is the ceiling - UNIT-ABORT reason=ceiling, and the next request "
          "is never sent with a zero timeout", e19 is None and z1 is not None and z1.aborted == "ceiling"
          and [op for op, _t in sent] == ["hello", "write"] and all(t_ > 0 for _op, t_ in sent),
          f"{e19!r} {sent} {z1.aborted if z1 else None}")

    print("\n- B-HELLO: a memory-store arm's one process may answer hello 'both'; a disk arm may not -")
    for sub in ("live", "passed", "seen", "ops", "hb"):
        shutil.rmtree(SHARED / sub, ignore_errors=True)
    (SHARED / "live").mkdir()
    st20 = SL.StatusLog(TMP / "STATUS20", local_tz=dt.timezone.utc)
    s20 = SC.Scheduler(C, None, st20, L, Clock(), None, tag="smoke", witnesses=SimpleNamespace(native=StubNative()),
                       parent_env=dict(os.environ), catcher_url="http://127.0.0.1:47001")
    st20.stand("SHB", "START", model="m", changelog="2026-09-10", order=1)
    both = {("r1", "h1"): {"hello_both": True}}
    res20 = {}
    for blk, arm, store in (("b01", "hm", "memory"), ("b02", "hd", "disk"), ("b03", "hw", "disk")):
        o20, sd20 = SC.arm_order([arm], campaign_seed=7, stand="SHB", block=blk)
        st20.block_start("SHB", blk, units=["h1"], arm_order=o20, seed=sd20)
        sid20 = st20.start("SHB", blk, "r1", arm, pid=os.getpid(), tag="smoke")
        kn20 = {("r1", "h1"): {"hello_arm": "someone-else"}} if arm == "hw" else both
        r20, e20 = attempt(lambda arm=arm, store=store, sid20=sid20, kn20=kn20: s20.write_turn(
            launcher(arm, expect=1, knobs=kn20, store=store), stand="SHB", runs=["r1"], units=["h1"], ops_for=OPS,
            ceilings={"h1": 60.0}, status_ids={"r1": sid20}))
        res20[arm] = ((r20 or {}).get(("r1", "h1")), e20)
        rec20 = res20[arm][0]
        if rec20 is not None and rec20.client is not None:
            rec20.client.close(timeout=10)
        st20.end(sid20, rc=0, wall_s=1.0, units=1, out=f"runs/{arm}.json")
        st20.block_end("SHB", blk)
    fl21 = FakeLauncher("hx", store="cloud")
    _r, e21 = attempt(lambda: s20.write_turn(fl21, stand="SHB", runs=["r1"], units=["h1"], ops_for=OPS,
                                              ceilings={"h1": 60.0}, status_ids={"r1": "SHB/b01/r1/hm"}))
    check("B-HELLO: a store_persistence outside disk and memory is a named SchedulerError before any child is opened "
          "(Q25(4)) - never a KeyError", isinstance(e21, SC.SchedulerError) and "Q25(4)" in str(e21) and not fl21.opened,
          repr(e21))
    hw, e_hw = res20["hw"]
    check("B-HELLO (BHd): a hello that names ANOTHER arm, protocol and stage right, is not this arm's - killed by us, "
          "UNIT-ABORT crash signal=SIGKILL, 'hello' in its error", e_hw is None and hw is not None and hw.aborted == "crash"
          and "hello" in (hw.error or "") and any(" UNIT-ABORT SHB/b03/r1/hw/h1 reason=crash signal=SIGKILL " in x
                                                   for x in aborts(TMP / "STATUS20")), f"{e_hw!r} {hw}")
    hm, e_hm = res20["hm"]
    hd, e_hd = res20["hd"]
    ua20 = aborts(TMP / "STATUS20")
    check("B-HELLO: a memory-store arm whose hello says 'both' writes - not aborted, its footprint whole",
          e_hm is None and hm is not None and hm.aborted is None and hm.footprint == 2
          and not any("/hm/h1 " in x for x in ua20), f"{e_hm!r} {hm}")
    check("B-HELLO: a disk arm whose hello says 'both' is not this arm's write stage - killed by us, UNIT-ABORT crash "
          "signal=SIGKILL", e_hd is None and hd is not None and hd.aborted == "crash" and "hello" in (hd.error or "")
          and any(" UNIT-ABORT SHB/b02/r1/hd/h1 reason=crash signal=SIGKILL " in x for x in ua20), f"{e_hd!r} {hd} {ua20}")

    print("\n- FIX-SCHED B-OPEN: a block that fails after its START closes what it opened, and kills its children -")
    EV12: list = []

    class PC12:
        def stage(self, block, stage):
            EV12.append(("stage", block, stage))

    class W12:
        native = StubNative()

        def begin_check(self, cid):
            EV12.append(("begin", cid))

        def end_check(self, cid):
            EV12.append(("end", cid))
            return {"check_id": cid, "complete": True}

    def block_world(sfile, launchers, *, tag="smoke", answer=None, medians=None, status_cls=None, stand="SO",
                    units=("y1",)):
        EV12.clear()
        for sub in ("live", "passed", "seen", "ops", "hb"):
            shutil.rmtree(SHARED / sub, ignore_errors=True)
        (SHARED / "live").mkdir()
        st = (status_cls or SL.StatusLog)(TMP / sfile, local_tz=dt.timezone.utc)
        if tag == "scored":
            st.campaign_start(anchor="c" * 40, prereg="d" * 64, freeze="e" * 64)
        s = SC.Scheduler(C, PC12(), st, L, Clock(), None, tag=tag, witnesses=W12(), parent_env=dict(os.environ),
                         catcher_url="http://127.0.0.1:47001",
                         home_canaries=L.Canaries.generate() if tag == "scored" else None)
        names = sorted(launchers)
        seed = next(x for x in range(1, 5000)
                    if SC.arm_order(names, campaign_seed=x, stand=stand, block="b01")[0] == names)   # the rows' order
        sp = SC.StandPlan(stand=stand, runs=("r1",), launchers=launchers, campaign_seed=seed,
                          unit_tokens={u: 1000 for u in units}, medians=medians if medians is not None else {},
                          write_ops=lambda a, r, u: OPS(r, u), read_plan=lambda a, u: [SC.ReadReq(qid=f"{u}-q", query="x")],
                          answer=answer or (lambda *a_: {"sha256": "5" * 64}), embed_tag=None, commit="c" * 40,
                          dirty=False)
        st.stand(stand, "START", model="m", changelog="2026-09-10", order=1)
        _r, err = attempt(lambda: s.run_block(sp, SC.BlockPlan(block="b01", units=tuple(units))))
        lines = (TMP / sfile).read_text(encoding="utf-8").splitlines()
        return err, lines, st

    def closed(lines, arms, stand="SO"):
        """Every START of the block is closed by ABORT reason=harness-error, and the block by BLOCK END."""
        return (all(any(f" ABORT {stand}/b01/r1/{a} reason=harness-error utc=" in x for x in lines) for a in arms)
                and any(f" BLOCK {stand}/b01 END" in x for x in lines))

    e12, l12, st12 = block_world("STATUS12", {"m1": launcher("m1", expect=1, knobs={("r1", "y1"): {"heartbeat": True}},
                                                             store="memory"), "zb": refused_launcher("zb")})
    check("B-OPEN: a failure after START reaches the caller as what it was (the refused spawn's SchedulerError)",
          isinstance(e12, SC.SchedulerError), repr(e12))
    check("B-OPEN: every START of the block is closed by ABORT reason=harness-error, then BLOCK END",
          closed(l12, ("m1", "zb")), "\n".join(l12[-6:]))
    check("B-OPEN: the proxy stage is reset and the block's check ended",
          EV12[-2:] == [("stage", None, None), ("end", "SO.b01")], str(EV12))
    _r, e_end = attempt(lambda: st12.stand("SO", "END", model="m", changelog="2026-09-10"))
    check("... and the file passes the writer's own replay", e_end is None and SL.self_check(TMP / "STATUS12") == [],
          f"{e_end!r} {SL.self_check(TMP / 'STATUS12')}")
    hb12 = SHARED / "hb" / "m1.r1.y1"
    b12 = hb12.read_text() if hb12.exists() else ""
    time.sleep(1.2)
    check("B-OPEN: the memory-store arm's live writer (the arm before the failure) is killed - no child outlives its "
          "block", hb12.exists() and hb12.read_text() == b12, b12)

    def refuse_unit(name, bad_unit, **kw):
        """A launcher whose child for one unit is refused (its binary does not exist); its other units run."""
        good = launcher(name, **kw)
        return SC.ChildArmLauncher(
            name, argv_for=lambda p, **_: ([str(FAKE_DIR / "no_such_arm.exe")] if f"{bad_unit}.home" in str(p)
                                      else [str(ARM_PY), "-B", str(FAKE_DIR / "fake_arm.py")]) + [str(p)],
            spec_for=good.spec_for, store_persistence=good.store_persistence, path_dirs=good.path_dirs)

    e18, l18, _st = block_world("STATUS18", {"m2": refuse_unit("m2", "y2", expect=1, store="memory",
                                                                knobs={("r1", "y1"): {"heartbeat": True}})},
                                units=("y1", "y2"), stand="SL")
    hb18 = SHARED / "hb" / "m2.r1.y1"
    b18 = hb18.read_text() if hb18.exists() else ""
    time.sleep(1.2)
    check("B-OPEN: a write turn that fails in one unit kills the live writers of its other units (a memory store) "
          "before the error goes on", isinstance(e18, SC.SchedulerError) and hb18.exists() and hb18.read_text() == b18
          and closed(l18, ("m2",), stand="SL"), f"{e18!r} {b18}")

    e13, l13, _st = block_world("STATUS13", {"a1": launcher("a1", expect=1)}, tag="scored", stand="SQ")
    check("B-OPEN: the ceilings come before BLOCK START - a scored arm without a frozen median is refused with no "
          "block line, no check and no stage", isinstance(e13, SC.SchedulerError) and "median" in str(e13)
          and not any(" BLOCK SQ/b01 " in x for x in l13) and not EV12, f"{e13!r} {l13[-3:]} {EV12}")

    def exploding(*_a):
        raise ValueError("the reader hook broke")

    e14, l14, st14 = block_world("STATUS14", {"a1": launcher("a1", expect=1, knobs={("r1", "y1"): {"heartbeat": True}})},
                                 answer=exploding, stand="SP")
    check("B-OPEN: a failure in the question stage keeps its own type (ValueError) and closes the block the same way",
          isinstance(e14, ValueError) and closed(l14, ("a1",), stand="SP")
          and EV12[-2:] == [("stage", None, None), ("end", "SP.b01")], f"{e14!r} {l14[-4:]} {EV12}")
    hb14 = SHARED / "hb" / "a1.r1.y1"
    b14 = hb14.read_text() if hb14.exists() else ""
    time.sleep(1.2)
    check("... and the read child whose answer broke is killed (the heartbeat stopped)",
          hb14.exists() and hb14.read_text() == b14, b14)

    e15, l15, _st = block_world("STATUS15", {"a1": FakeLauncher("a1", scripts={("write", "r1", "y1"): {"close_rc": None}})},
                                stand="SN")
    check("B-RC (END): a disk unit with no exit code is a SchedulerError naming it - never a silent rc=0; no END line, "
          "the START closed by ABORT", isinstance(e15, SC.SchedulerError) and "y1" in str(e15)
          and not any(" END SN/b01/r1/a1 " in x for x in l15) and closed(l15, ("a1",), stand="SN"),
          f"{e15!r} {l15[-4:]}")
    e15m, l15m, _st = block_world("STATUS15M", {"a1": FakeLauncher("a1", store="memory")}, stand="SM")
    check("... while a memory-store unit's write stage has no code of its own - its END is written",
          e15m is None and any(" END SM/b01/r1/a1 rc=0 " in x for x in l15m), f"{e15m!r} {l15m[-3:]}")

    class BlockEndFails(SL.StatusLog):
        def block_end(self, stand, block):
            raise RuntimeError("the disk is full")

    e16, _l16, _st = block_world("STATUS16", {"a1": launcher("a1", expect=1)}, answer=exploding, stand="SK",
                                 status_cls=BlockEndFails)
    check("B-OPEN: a cleanup step that fails is named beside the original error, never swallowed",
          isinstance(e16, SC.SchedulerError) and "the disk is full" in str(e16) and "the reader hook broke" in str(e16)
          and isinstance(e16.__cause__, ValueError), repr(e16))

    print("\n- FIX-SCHED B-CL, B-TE and B-OPEN at the stand -")

    def stand_run(tag, stand, sfile, *, changelogs=None, trees=None, launchers=None):
        s, sp, judges, ev, gpu, st = stand_world(tag, stand=stand, sfile=sfile)
        s.hooks.changelogs, s.hooks.trees = changelogs or {}, list(trees or [])
        if launchers:
            sp.launchers = launchers
        if tag == "scored":
            st.campaign_start(anchor="c" * 40, prereg="d" * 64, freeze="e" * 64)
        res, err = attempt(lambda: s.run_stand(sp, [SC.BlockPlan(block="b01", units=("w1",))], judges=judges, order=7))
        text = (TMP / sfile).read_text(encoding="utf-8") if (TMP / sfile).exists() else ""
        return res, err, text, [e for e in ev if e[0] == "judge"]

    for cl, sfile, stand in ((None, "STATUS20", "SC1"), ("unread", "STATUS21", "SC2"), ("unknown", "STATUS22", "SC3")):
        _res, err, text, _j = stand_run("scored", stand, sfile, changelogs={"start": cl})
        check(f"B-CL: a scored stand whose start read gave {cl!r} as its change log is refused before STAND START",
              isinstance(err, SC.SchedulerError) and "change" in str(err) and f"STAND {stand} START" not in text,
              f"{err!r} {text[-200:]}")
    res, err, text, _j = stand_run("smoke", "SC4", "STATUS23", changelogs={"start": None, "end": None})
    check("B-CL: a smoke stand records an unread change log as 'unread', at START and at END - never 'unknown'",
          err is None and " STAND SC4 START " in text and "changelog=unread" in text.split(" STAND SC4 START ")[1].split("\n")[0]
          and "changelog=unread" in text.split(" STAND SC4 END ")[1].split("\n")[0], f"{err!r} {text[-300:]}")
    res, err, text, j = stand_run("scored", "SC5", "STATUS24", changelogs={"end": None})
    check("B-CL: a scored stand whose end read gave no change log writes STAND END (changelog=unread), runs no judge, "
          "and raises", isinstance(err, SC.SchedulerError) and "change" in str(err) and j == []
          and "changelog=unread" in text.split(" STAND SC5 END ")[-1].split("\n")[0] and " STAND SC5 END " in text,
          f"{err!r} {j} {text[-200:]}")
    res, err, text, j = stand_run("scored", "ST2", "STATUS25", trees=[True, False])
    check("B-TE: a scored stand whose tree is dirty at STAND END writes STAND END, runs no judge, and raises naming the "
          "tree; the result so far rides on the error", isinstance(err, SC.SchedulerError) and "tree" in str(err)
          and j == [] and " STAND ST2 END " in text
          and (getattr(err, "partial", None) or {}).get("dirty_end") == ["HEAD is not the anchor"], f"{err!r} {j}")
    res, err, text, j = stand_run("smoke", "ST3", "STATUS26", trees=[True, False])
    check("B-TE: a smoke stand records a dirty end tree in its result and goes on",
          err is None and (res or {}).get("dirty_end") == ["HEAD is not the anchor"], f"{err!r} {res}")
    res, err, text, j = stand_run("scored", "SB1", "STATUS27", launchers={"a1": refused_launcher("a1")})
    check("B-OPEN (stand): a block's failure writes STAND END (model and change log unread), runs no judge, and "
          "reaches the caller", isinstance(err, SC.SchedulerError) and j == []
          and " STAND SB1 END model=unread changelog=unread " in text, f"{err!r} {j} {text[-300:]}")
    check("... and the stand's file passes the writer's own replay", SL.self_check(TMP / "STATUS27") == [],
          str(SL.self_check(TMP / "STATUS27")))

    print("\n- O1 (B-P3, Q-A4-6 (1)): a unit's own environment, the argv exception, the names fixed per arm -")

    class Built(Exception):
        """Raised by the capturing scheduler once open() has built its LaunchSpec - nothing is spawned."""

    O1_SEQ = __import__('itertools').count(1)               # one home per capture, never reused

    class CaptureSched:
        def __init__(self):
            self.specs, self.n = [], 0

        def spawn_child(self, build, **kw):
            self.n += 1
            home = TMP / "o1" / f"h{next(O1_SEQ)}"
            home.mkdir(parents=True)
            self.specs.append((kw, build(SimpleNamespace(home=home, cwd=TMP / "o1" / f"c{next(O1_SEQ)}"))))
            raise Built()

    def o1_launcher(declared_for, *, argv_exception=None):
        return SC.ChildArmLauncher("o1", argv_for=lambda p, *, stage, stand, run, unit: ["py", f"{stage}:{run}.{unit}",
                                                                                         str(p)],
                                   spec_for=lambda *a_, **k_: {}, path_dirs=("pydir",), declared={"CONST": "1"},
                                   token_names=("DEEPSEEK_API_KEY",), declared_for=declared_for,
                                   argv_exception=argv_exception)

    def o1_open(ln, cs, run, unit, stage="write"):
        try:
            ln.open(stage, sched=cs, stand="SO", run=run, unit=unit)
            return "opened"
        except Built:
            return "built"
        except SC.SchedulerError as e:
            return str(e)

    cs = CaptureSched()
    ln = o1_launcher(lambda stage, *, stand, run, unit, dirs, write_dirs: {"URL": f"http://x/u/{run}.{unit}/v1"},
                     argv_exception={2: "script-in-repo"})
    got = [o1_open(ln, cs, r, u) for r, u in (("r1", "u1"), ("r1", "u2"), ("r2", "u1"))]
    specs = [s for _kw, s in cs.specs]
    check("O1: each unit's child gets the arm's constant values and its own - a URL naming its run and unit - and the "
          "argv names the stage, run and unit it was built for", got == ["built"] * 3
          and [s.declared for s in specs] == [{"CONST": "1", "URL": f"http://x/u/{r}.{u}/v1"}
                                              for r, u in (("r1", "u1"), ("r1", "u2"), ("r2", "u1"))]
          and [s.argv[1] for s in specs] == ["write:r1.u1", "write:r1.u2", "write:r2.u1"], f"{got} {specs}")
    check("O1: the arm's argv exception reaches every LaunchSpec (Q9: our arms run from the repository), and so do its "
          "token names and path dirs", specs and all(s.argv_exception == {2: "script-in-repo"}
                                                      and s.token_names == ("DEEPSEEK_API_KEY",)
                                                      and s.path_dirs == ("pydir",) for s in specs), str(specs))
    cs2 = CaptureSched()
    drift = o1_launcher(lambda stage, *, stand, run, unit, dirs, write_dirs:
                        ({"URL": "u"} if unit == "u1" else {"URL": "u", "EXTRA": "x"}))
    got2 = [o1_open(drift, cs2, "r1", u) for u in ("u1", "u2")]
    check("Q-A4-6 (1): a unit whose environment names differ from the arm's first unit's in the same stage is refused "
          "by name before its spawn", got2[0] == "built" and "EXTRA" in got2[1] and "differ from its first write unit"
          in got2[1] and len(cs2.specs) == 1, str(got2))
    cs4 = CaptureSched()
    staged = o1_launcher(lambda stage, *, stand, run, unit, dirs, write_dirs:
                         ({"URL": "w"} if stage == "write" else {"URL": "w", "READER_URL": "r"}))
    got4 = [o1_open(staged, cs4, "r1", u, stage=s) for s, u in (("write", "u1"), ("read", "u1"), ("write", "u2"),
                                                                ("read", "u2"))]
    check("Q-A4-6 (1): the write and read stages of one arm may differ (the reader's URL) - the names are fixed per "
          "(arm, stage), not per arm", got4 == ["built"] * 4, str(got4))
    clash = o1_open(o1_launcher(lambda stage, *, stand, run, unit, dirs, write_dirs: {"CONST": "2"}), CaptureSched(),
                    "r1", "u1")
    same = o1_open(o1_launcher(lambda stage, *, stand, run, unit, dirs, write_dirs: {"CONST": "1"}), CaptureSched(),
                   "r1", "u1")
    check("O1: declared_for may not set a name the arm's constant values set to another value - the same value is no "
          "clash", "another value" in clash and "CONST" in clash and same == "built", f"{clash} | {same}")
    plain = o1_launcher(None)
    cs3 = CaptureSched()
    o1_open(plain, cs3, "r1", "u1")
    check("O1: an arm with no per-unit values and no exception - its declared values as they are, no argv exception",
          cs3.specs and cs3.specs[0][1].declared == {"CONST": "1"} and cs3.specs[0][1].argv_exception is None,
          str(cs3.specs))

    print("\n- B-GATE-D1: a wait at the incident gate is no unit's active time; B-OPS: every op before the first spawn -")
    vcg = VirtualClock()

    class SlowGate:
        """Refuses once while 700 virtual seconds pass - an incident longer than a scored unit's 600 s ceiling."""

        def __init__(self):
            self.asked = 0

        def admits_new_unit(self):
            self.asked += 1
            if self.asked == 1:
                vcg.offset += 700.0
                return False
            return True

    for sub in ("live", "passed", "seen", "ops", "hb"):
        shutil.rmtree(SHARED / sub, ignore_errors=True)
    (SHARED / "live").mkdir()
    st28 = SL.StatusLog(TMP / "STATUS28", now=vcg.utc, local_tz=dt.timezone.utc)
    st28.campaign_start(anchor="c" * 40, prereg="d" * 64, freeze="e" * 64)
    gate28 = SlowGate()
    s28 = SC.Scheduler(C, PC12(), st28, L, vcg, None, tag="scored", witnesses=W12(), parent_env=dict(os.environ),
                       catcher_url="http://127.0.0.1:47001", hooks=SimpleNamespace(gate=gate28),
                       home_canaries=L.Canaries.generate())
    sp28 = SC.StandPlan(stand="SGW", runs=("r1",), launchers={"a1": launcher("a1", expect=1)}, campaign_seed=20260927,
                        unit_tokens={"g1": 1000}, medians={("a1", "SGW"): 0.0001}, write_ops=lambda a, r, u: OPS(r, u),
                        read_plan=lambda a, u: [SC.ReadReq(qid=f"{u}-q1", query="x")],
                        answer=lambda *a_: {"sha256": "6" * 64}, embed_tag=None, commit="c" * 40, dirty=False)
    st28.stand("SGW", "START", model="m", changelog="2026-09-10", order=1)
    res28, e28 = attempt(lambda: s28.run_block(sp28, SC.BlockPlan(block="b01", units=("g1",))))
    w28 = ((res28 or {}).get("write") or {}).get("a1", {}).get(("r1", "g1"))
    q28 = ((res28 or {}).get("questions") or {}).get("a1", {}).get(("r1", "g1")) or {}
    check("B-GATE-D1: a scored unit that waited 700 virtual s at the incident gate is not killed by its 600 s ceiling "
          "(an exogenous wait is never the arm's UNIT-ABORT), and neither stage counts the wait in its active seconds",
          e28 is None and gate28.asked >= 2 and w28 is not None and w28.aborted is None and w28.active_s < 600
          and q28.get("aborted") is None and q28.get("active_s", 1e9) < 600
          and "UNIT-ABORT" not in (TMP / "STATUS28").read_text(encoding="utf-8"),
          f"{e28!r} asked {gate28.asked} {w28} {q28.get('aborted')} {q28.get('active_s')}")

    class SlowGateQ(SlowGate):
        """Admits the write stage's ask; the question stage's ask waits 700 virtual seconds first."""

        def admits_new_unit(self):
            self.asked += 1
            if self.asked == 2:
                vcg.offset += 700.0
                return False
            return True

    for sub in ("live", "passed", "seen", "ops", "hb"):
        shutil.rmtree(SHARED / sub, ignore_errors=True)
    (SHARED / "live").mkdir()
    st28q = SL.StatusLog(TMP / "STATUS28q", now=vcg.utc, local_tz=dt.timezone.utc)
    st28q.campaign_start(anchor="c" * 40, prereg="d" * 64, freeze="e" * 64)
    gate28q = SlowGateQ()
    s28q = SC.Scheduler(C, PC12(), st28q, L, vcg, None, tag="scored", witnesses=W12(), parent_env=dict(os.environ),
                        catcher_url="http://127.0.0.1:47001", hooks=SimpleNamespace(gate=gate28q),
                        home_canaries=L.Canaries.generate())
    sp28q = SC.StandPlan(stand="SGQ", runs=("r1",), launchers={"a1": launcher("a1", expect=1)}, campaign_seed=20260927,
                         unit_tokens={"g2": 1000}, medians={("a1", "SGQ"): 0.0001}, write_ops=lambda a, r, u: OPS(r, u),
                         read_plan=lambda a, u: [SC.ReadReq(qid=f"{u}-q1", query="x")],
                         answer=lambda *a_: {"sha256": "6" * 64}, embed_tag=None, commit="c" * 40, dirty=False)
    st28q.stand("SGQ", "START", model="m", changelog="2026-09-10", order=1)
    res28q, e28q = attempt(lambda: s28q.run_block(sp28q, SC.BlockPlan(block="b01", units=("g2",))))
    q28q = ((res28q or {}).get("questions") or {}).get("a1", {}).get(("r1", "g2")) or {}
    check("B-GATE-D1 (the question stage): a 700 virtual s wait at the gate before the unit's question stage is no "
          "active time either - no UNIT-ABORT by the ceiling, the stage's active seconds under 600",
          e28q is None and gate28q.asked >= 2 and q28q.get("aborted") is None and q28q.get("active_s", 1e9) < 600
          and "UNIT-ABORT" not in (TMP / "STATUS28q").read_text(encoding="utf-8"),
          f"{e28q!r} asked {gate28q.asked} {q28q.get('aborted')} {q28q.get('active_s')}")

    def ops_refusing(r, u):
        if u == "p2":
            raise ValueError("the plan refuses p2's ops (a speaker outside the sample's two)")
        return OPS(r, u)

    n_sp = len(L.spawns_log(C).read_text(encoding="utf-8").splitlines())
    _r, e_ops = attempt(lambda: s28.write_turn(launcher("pm", expect=1, store="memory"), stand="SGW", runs=["r1"],
                                               units=["p1", "p2"], ops_for=ops_refusing,
                                               ceilings={"p1": 60.0, "p2": 60.0}, status_ids={"r1": "SGW/b01/r1/pm"}))
    new_sp = L.spawns_log(C).read_text(encoding="utf-8").splitlines()[n_sp:]
    check("B-OPS: a plan that refuses one unit's ops refuses the arm's write turn before any child is spawned - a "
          "memory-store writer spawned before it outlived the failed turn", isinstance(e_ops, ValueError)
          and "p2" in str(e_ops) and new_sp == [], f"{e_ops!r} {len(new_sp)} spawn(s)")

    print("\n- B-RC at the close, B-SIGRC, the write error in the run record, a refused STAND END, a judge's partial -")
    for sub in ("live", "passed", "seen", "ops", "hb"):
        shutil.rmtree(SHARED / sub, ignore_errors=True)
    (SHARED / "live").mkdir()
    st29 = SL.StatusLog(TMP / "STATUS29", local_tz=dt.timezone.utc)
    s29 = SC.Scheduler(C, None, st29, L, Clock(), None, tag="smoke", witnesses=SimpleNamespace(native=StubNative()),
                       parent_env=dict(os.environ), catcher_url="http://127.0.0.1:47001")
    st29.stand("SBY", "START", model="m", changelog="2026-09-10", order=1)
    o29, sd29 = SC.arm_order(["a1"], campaign_seed=7, stand="SBY", block="b01")
    st29.block_start("SBY", "b01", units=["m1", "s1", "b1"], arm_order=o29, seed=sd29)
    sid29 = st29.start("SBY", "b01", "r1", "a1", pid=os.getpid(), tag="smoke")
    r29, e29 = attempt(lambda: s29.write_turn(launcher("a1", expect=1, knobs={("r1", "m1"): {"mute_bye": True}}),
                                               stand="SBY", runs=["r1"], units=["m1"], ops_for=OPS,
                                               ceilings={"m1": 5.0}, status_ids={"r1": sid29}))
    m29 = (r29 or {}).get(("r1", "m1"))
    check("B-RC (bye): a child that never answers bye within its ceiling is UNIT-ABORT reason=ceiling with its tree "
          "killed - never the root's kill code passed off as the child's own exit code",
          e29 is None and m29 is not None and m29.aborted == "ceiling" and m29.rc is None and m29.signal == "SIGKILL"
          and any(" UNIT-ABORT SBY/b01/r1/a1/m1 reason=ceiling " in x for x in aborts(TMP / "STATUS29")),
          f"{e29!r} {m29}")
    fl29 = FakeLauncher("a1", scripts={("write", "r1", "s1"): {"died": True, "exit_rc": -11}})
    r29s, e29s = attempt(lambda: s29.write_turn(fl29, stand="SBY", runs=["r1"], units=["s1"], ops_for=OPS,
                                                 ceilings={"s1": 60.0}, status_ids={"r1": sid29}))
    s29r = (r29s or {}).get(("r1", "s1"))
    check("B-SIGRC: a child that died by signal 11 (POSIX returncode -11) is UNIT-ABORT crash signal=SIGSEGV - never "
          "rc=-11, an exit code that never was", e29s is None and s29r is not None and s29r.rc is None
          and s29r.signal == "SIGSEGV"
          and any(" UNIT-ABORT SBY/b01/r1/a1/s1 reason=crash signal=SIGSEGV " in x for x in aborts(TMP / "STATUS29")),
          f"{e29s!r} {s29r} {aborts(TMP / 'STATUS29')}")
    r29b, e29b = attempt(lambda: s29.write_turn(launcher("a1", expect=1, knobs={("r1", "b1"): {"bad_bye": True}}),
                                                 stand="SBY", runs=["r1"], units=["b1"], ops_for=OPS,
                                                 ceilings={"b1": 30.0}, status_ids={"r1": sid29}))
    b29 = (r29b or {}).get(("r1", "b1"))
    check("B-RC (bye): a bye answered with a line that is no protocol is the unit's crash, killed by us (signal=SIGKILL) "
          "- never a clean rc 0 from a child that broke the protocol at its close and then exited by itself",
          e29b is None and b29 is not None and b29.aborted == "crash" and b29.rc is None and b29.signal == "SIGKILL"
          and any(" UNIT-ABORT SBY/b01/r1/a1/b1 reason=crash signal=SIGKILL " in x for x in aborts(TMP / "STATUS29")),
          f"{e29b!r} {b29}")
    st29.end(sid29, rc=0, wall_s=1.0, units=3, out="runs/sby.json")
    st29.block_end("SBY", "b01")
    st29.stand("SBY", "END", model="m", changelog="2026-09-10")
    e31, _l31, _st31 = block_world("STATUS31", {"a1": launcher("a1", expect=1, knobs={("r1", "y1"): {"bad_hello": True}})},
                                   stand="SE")
    rr31 = C.runs_root / "SE" / "_records" / "b01" / "r1.a1.json"
    w31 = (json.loads(rr31.read_text(encoding="utf-8"))["units"]["y1"]["write"] if rr31.is_file() else {})
    check("the run record keeps an aborted write stage's error - why it ended is in no STATUS line",
          e31 is None and w31.get("aborted") == "crash" and "hello" in (w31.get("error") or ""), f"{e31!r} {w31}")
    _res32, e32, text32, _j32 = stand_run("smoke", "SC6", "STATUS32", changelogs={"end": "2026-09-10 (v1.2)"})
    check("B-OPEN: a STAND END that STATUS refuses (a change log with a space) still closes the stand - STAND END "
          "model=unread changelog=unread - and the refusal goes on", e32 is not None
          and " STAND SC6 END model=unread changelog=unread " in text32
          and SL.self_check(TMP / "STATUS32") == [], f"{e32!r} {text32[-200:]}")
    s33, sp33, _judges33, ev33, gpu33, st33 = stand_world("scored", stand="SJ2", sfile="STATUS33")
    st33.campaign_start(anchor="c" * 40, prereg="d" * 64, freeze="e" * 64)

    class LeakyJudge(Judge):
        def run(self):
            super().run()
            gpu33.resident.add("leftover:7b")              # a judge that leaves a second model behind

    lj = LeakyJudge("J9", "gpt-oss:20b", gpu33, ev33, TMP / "STATUS33", "SJ2")
    _r33, e33 = attempt(lambda: s33.run_stand(sp33, [SC.BlockPlan(block="b01", units=("w1",))], judges=[lj], order=8))
    part33 = getattr(e33, "partial", None) or {}
    check("a judge's failure after STAND END (the GPU still holds a model) carries what the stand measured, as B-CL "
          "and B-TE do", isinstance(e33, SC.SchedulerError) and "still holds" in str(e33)
          and part33.get("stand") == "SJ2" and len(part33.get("blocks") or []) == 1, f"{e33!r} {sorted(part33)}")

    class ControlError(RuntimeError):                      # sched_ctl.ControlError's shape: a RuntimeError, no partial
        pass

    def unload_refuses(*a, **k):
        raise ControlError("qwen3:32b is still resident 30 s after its unload (§5.6)")

    for tag_, stand_, why in (("unload", "SJ3", "the judge's unload refused after UNLOAD_WAIT_S, B-OLA's path"),
                              ("run", "SJ4", "the judge's own run raised")):
        s_j, sp_j, _jj, ev_j, gpu_j, st_j = stand_world("scored", stand=stand_, sfile=f"STATUS_{stand_}")
        st_j.campaign_start(anchor="c" * 40, prereg="d" * 64, freeze="e" * 64)

        class RaisingJudge(Judge):
            def run(self, tag_=tag_):
                if tag_ == "run":
                    raise ValueError("the judge's verdict file does not parse")
                super().run()
                self.gpu.unload = unload_refuses           # the scheduler's unload of THIS judge's model refuses

        rj = RaisingJudge("J8", "qwen3:32b", gpu_j, ev_j, TMP / f"STATUS_{stand_}", stand_)
        _rj, e_j = attempt(lambda s_j=s_j, sp_j=sp_j, rj=rj: s_j.run_stand(
            sp_j, [SC.BlockPlan(block="b01", units=("w1",))], judges=[rj], order=10))
        part_j = getattr(e_j, "partial", None) or {}
        check(f"B-JPART: a judge failure that is no SchedulerError ({why}) still carries what the stand measured - a "
              f"SchedulerError with the partial, its cause kept", isinstance(e_j, SC.SchedulerError)
              and part_j.get("stand") == stand_ and len(part_j.get("blocks") or []) == 1
              and isinstance(e_j.__cause__, (ControlError, ValueError)), f"{e_j!r} {sorted(part_j)}")
    s34, sp34, judges34, _ev34, _gpu34, st34 = stand_world("scored", stand="SPF", sfile="STATUS34")
    st34.campaign_start(anchor="c" * 40, prereg="d" * 64, freeze="e" * 64)
    s34.hooks.preflight = None                              # hooks without a balance preflight
    _r34, e34 = attempt(lambda: s34.run_stand(sp34, [SC.BlockPlan(block="b01", units=("w1",))], judges=judges34,
                                              order=9))
    text34 = (TMP / "STATUS34").read_text(encoding="utf-8") if (TMP / "STATUS34").exists() else ""
    check("B-PREFLIGHT: a scored stand whose hooks have no balance preflight is refused before STAND START - the 402 "
          "halt and the 2x balance rule are never skipped silently", isinstance(e34, SC.SchedulerError)
          and "preflight" in str(e34) and "STAND SPF START" not in text34, f"{e34!r}")
    st35 = SL.StatusLog(TMP / "STATUS35", local_tz=dt.timezone.utc)
    s35 = SC.Scheduler(C, PC12(), st35, L, Clock(), None, tag="smoke", witnesses=W12(), parent_env=dict(os.environ),
                       catcher_url="http://127.0.0.1:47001")
    fl35 = FakeLauncher("a1", scripts={("read", "r1", "c1"): {"read_timeout": True}})
    sp35 = SC.StandPlan(stand="SCT", runs=("r1",), launchers={"a1": fl35}, campaign_seed=7, unit_tokens={"c1": 1000},
                        medians={}, write_ops=lambda a, r, u: OPS(r, u),
                        read_plan=lambda a, u: [SC.ReadReq(qid=f"{u}-q1", query="x"), SC.ReadReq(qid=f"{u}-q2", query="y")],
                        answer=lambda *a_: {"sha256": "7" * 64}, embed_tag=None, commit="c" * 40, dirty=False)
    st35.stand("SCT", "START", model="m", changelog="2026-09-10", order=1)
    res35, e35 = attempt(lambda: s35.run_block(sp35, SC.BlockPlan(block="b01", units=("c1",))))
    q35 = ((res35 or {}).get("questions") or {}).get("a1", {}).get(("r1", "c1")) or {}
    cut35 = [r for r in q35.get("reads") or [] if r.get("cut")]
    check("B-CUT: the read the unit's ceiling cut keeps its row - qid, t0, t1 at the abort, cut: true - so R9's read "
          "windows cover a product call made during it (it is no background write)", e35 is None
          and q35.get("aborted") == "ceiling" and len(cut35) == 1 and cut35[0]["qid"] == "c1-q1"
          and bool(cut35[0].get("t0")) and bool(cut35[0].get("t1")), f"{e35!r} {q35}")
    st36 = SL.StatusLog(TMP / "STATUS36", local_tz=dt.timezone.utc)
    s36 = SC.Scheduler(C, PC12(), st36, L, Clock(), None, tag="smoke", witnesses=W12(), parent_env=dict(os.environ),
                       catcher_url="http://127.0.0.1:47001")
    fl36 = FakeLauncher("a1", scripts={("read", "r1", "c2"): {"close_timeout": True}})
    sp36 = SC.StandPlan(stand="SCU", runs=("r1",), launchers={"a1": fl36}, campaign_seed=7, unit_tokens={"c2": 1000},
                        medians={}, write_ops=lambda a, r, u: OPS(r, u),
                        read_plan=lambda a, u: [SC.ReadReq(qid=f"{u}-q1", query="x"), SC.ReadReq(qid=f"{u}-q2", query="y")],
                        answer=lambda *a_: {"sha256": "8" * 64}, embed_tag=None, commit="c" * 40, dirty=False)
    st36.stand("SCU", "START", model="m", changelog="2026-09-10", order=1)
    res36, e36 = attempt(lambda: s36.run_block(sp36, SC.BlockPlan(block="b01", units=("c2",))))
    q36 = ((res36 or {}).get("questions") or {}).get("a1", {}).get(("r1", "c2")) or {}
    check("B-CUT: a unit that ends after its reads completed (its close timed out) keeps its two read rows and no cut "
          "row - a finished read is never re-marked as the one the unit ended in", e36 is None
          and q36.get("aborted") == "ceiling" and [r.get("qid") for r in q36.get("reads") or []] == ["c2-q1", "c2-q2"]
          and not any(r.get("cut") for r in q36.get("reads") or []), f"{e36!r} {q36}")

    print("\n- Q2: a halt raises into B-OPEN - STAND END halt=<kind>, ABORT reason=<kind>, the unit's clock unstarted -")

    class Halt(Exception):
        """run_v3_gate.GateHalted's shape: the scheduler reads its ``halt``."""

        def __init__(self, kind):
            super().__init__(f"the incident gate halted ({kind})")
            self.halt = kind

    class HaltGate:
        """Admits ``admit`` asks, then halts - opening INCIDENT START kind=<kind> as GateDriver does for a provider's
        stop (none for harness-error) - and raises on every later ask."""

        def __init__(self, kind, admit):
            self.kind, self.admit, self.asked, self.st, self.opened = kind, admit, 0, None, False

        def admits_new_unit(self):
            self.asked += 1
            if self.asked <= self.admit:
                return True
            if self.kind != "harness-error" and not self.opened:
                self.st.incident(f"q2-{self.kind}", "START", arms=["a1"], kind=self.kind)
                self.opened = True
            raise Halt(self.kind)

    got_q2 = {}
    for kind, admit, sfile, stand_ in (("402", 0, "STATUS40", "SQ2"), ("harness-error", 2, "STATUS41", "SQ3")):
        hg = HaltGate(kind, admit)
        s_q, sp_q, _jq, _evq, _gq, st_q = stand_world("smoke", gate=hg, stand=stand_, sfile=sfile)
        hg.st = st_q
        _rq, e_q = attempt(lambda s_q=s_q, sp_q=sp_q: s_q.run_stand(
            sp_q, [SC.BlockPlan(block="b01", units=("w1",)), SC.BlockPlan(block="b02", units=("w2",))], judges=(),
            order=1))
        text_q = (TMP / sfile).read_text(encoding="utf-8") if (TMP / sfile).exists() else ""
        got_q2[kind] = {"error": type(e_q).__name__, "halt": getattr(e_q, "halt", None),
                        "aborts": [x.split(" reason=")[1].split()[0] for x in text_q.splitlines() if " ABORT " in x],
                        "stand_end": [x for x in text_q.splitlines() if f" STAND {stand_} END " in x],
                        "replay": SL.self_check(TMP / sfile)}
    g402, ghe = got_q2["402"], got_q2["harness-error"]
    check("Q2 (F15): a 402 halt at the block's first unit raises into B-OPEN - the halt itself propagates (an unstarted "
          "unit's clock is no TypeError), the arm-run's ABORT says reason=402 (B-ABORT-REASON: the provider's stop, "
          "never our failure), STAND END says halt=402, and the STATUS replays clean",
          g402["error"] == "Halt" and g402["halt"] == "402" and g402["aborts"] == ["402"]
          and len(g402["stand_end"]) == 1 and " halt=402 " in g402["stand_end"][0] and g402["replay"] == [], str(g402))
    check("Q2 (F15): a gate gone blind between blocks (harness-error at block 2's unit) - its arm-run ABORT "
          "reason=harness-error, STAND END halt=harness-error with no INCIDENT line, the STATUS replays clean",
          ghe["error"] == "Halt" and ghe["aborts"] == ["harness-error"] and len(ghe["stand_end"]) == 1
          and " halt=harness-error " in ghe["stand_end"][0] and ghe["replay"] == [], str(ghe))

    class LateHaltGate:
        """Admits every ask; at the stand's last ask a 402 has come (INCIDENT START kind=402, halted) - no unit asks
        after it, so only halt_kind() tells the scheduler."""

        def __init__(self, last):
            self.last, self.asked, self.st, self.halted = last, 0, None, None

        def admits_new_unit(self):
            self.asked += 1
            if self.asked == self.last:
                self.st.incident("q2-late", "START", arms=["a1"], kind="402")
                self.halted = "402"
            return True

        def halt_kind(self):
            return self.halted

    lh = LateHaltGate(last=4)                              # w1 write, w1 questions, w2 write, w2 questions
    s_l, sp_l, _jl, _evl, _gl, st_l = stand_world("smoke", gate=lh, stand="SQ4", sfile="STATUS42")
    lh.st = st_l
    _rl, e_l = attempt(lambda: s_l.run_stand(sp_l, [SC.BlockPlan(block="b01", units=("w1",)),
                                                    SC.BlockPlan(block="b02", units=("w2",))], judges=(), order=1))
    text_l = (TMP / "STATUS42").read_text(encoding="utf-8") if (TMP / "STATUS42").exists() else ""
    ends_l = [x for x in text_l.splitlines() if " STAND SQ4 END " in x]
    check("Q2: a 402 that comes after the stand's last unit asked the gate still names itself - the normal STAND END "
          "says halt=402 (read from the gate's halt_kind, never raised), and the STATUS replays clean",
          e_l is None and lh.asked == 4 and len(ends_l) == 1 and " halt=402 " in ends_l[0]
          and SL.self_check(TMP / "STATUS42") == [], f"{e_l!r} asked {lh.asked} {ends_l} {SL.self_check(TMP / 'STATUS42')}")

    class ResetFails:
        """The proxy's stage reset fails while the halted block closes (a B-OPEN step): _close_block then raises a
        SchedulerError from the halt - the halt is the error's cause, never the error itself."""

        def stage(self, block, stage):
            if block is None and stage is None:
                raise RuntimeError("the proxy's stage reset failed")

    hw = HaltGate("402", 0)
    s_w, sp_w, _jw, _evw, _gw, st_w = stand_world("smoke", gate=hw, stand="SQ5", sfile="STATUS43")
    hw.st = st_w
    s_w.proxy_ctl = ResetFails()
    _rw, e_w = attempt(lambda: s_w.run_stand(sp_w, [SC.BlockPlan(block="b01", units=("w1",))], judges=(), order=1))
    text_w = (TMP / "STATUS43").read_text(encoding="utf-8") if (TMP / "STATUS43").exists() else ""
    ends_w = [x for x in text_w.splitlines() if " STAND SQ5 END " in x]
    aborts_w = [x.split(" reason=")[1].split()[0] for x in text_w.splitlines() if " ABORT " in x]
    check("Q2 (the auditor's SQ4): a 402 halt whose block also fails to close (the proxy's stage reset raises) comes "
          "wrapped in a SchedulerError - STAND END still says halt=402 (the halt is read through the error's causes), "
          "the arm-run's ABORT says reason=402, and the STATUS replays clean",
          isinstance(e_w, SC.SchedulerError) and getattr(e_w, "halt", None) is None and SC._halt_of(e_w) == "402"
          and aborts_w == ["402"] and len(ends_w) == 1 and " halt=402 " in ends_w[0]
          and SL.self_check(TMP / "STATUS43") == [],
          f"{type(e_w).__name__}: {str(e_w)[:160]} aborts={aborts_w} ends={ends_w} {SL.self_check(TMP / 'STATUS43')[:2]}")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 scheduler turns: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
