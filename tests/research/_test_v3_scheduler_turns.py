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
  arm keeps its live client for the read stage (Q25(4)).

    python tests/research/_test_v3_scheduler_turns.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
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
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


FAKE_ARM = r'''
import json, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base as B
spec = json.load(open(sys.argv[1], encoding="utf-8"))
shared = spec["shared"]
knobs = spec.get("knobs") or {}


def live_dir(arm):
    return os.path.join(shared, "live", arm)


class H:
    def __init__(self):
        self.first, self.items = True, []
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
        os.makedirs(live_dir(spec["arm"]), exist_ok=True)
        open(os.path.join(live_dir(spec["arm"]), spec["run"] + "." + spec["unit"]), "w").close()
        return {"protocol": B.PROTOCOL, "arm": spec["arm"], "stage": spec["stage"], "pid": os.getpid()}

    def write(self, item, date=None):
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
        self.items.append(item)
        return {"op_id": item.get("item_id")}

    def end_write(self):
        passed, t = os.path.join(shared, "passed", spec["arm"]), time.time()
        while time.time() - t < 8 and len(os.listdir(passed)) < spec["expect"]:
            time.sleep(0.02)                  # no live marker leaves before every peer has counted it
        os.remove(os.path.join(live_dir(spec["arm"]), spec["run"] + "." + spec["unit"]))
        return {"footprint": len(self.items), "seal": {"sha256": "0" * 64}}

    def read(self, qid, query, k=10):
        return {"qid": qid, "items": self.items[:k]}

    def counters(self):
        return {}


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
                "knobs": (knobs or {}).get((run, unit), {})}
    return SC.ChildArmLauncher(name, argv_for=lambda p: [sys.executable, "-B", str(FAKE_DIR / "fake_arm.py"), str(p)],
                               spec_for=spec_for, store_persistence=store, path_dirs=(str(Path(sys.executable).parent),))


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

    print("\n- Q25(4): a memory-store arm keeps its process -")
    order3, seed3 = SC.arm_order(["am"], campaign_seed=7, stand="SX", block="b03")
    status.block_start("SX", "b03", units=["u6"], arm_order=order3, seed=seed3)
    sm = status.start("SX", "b03", "r1", "am", pid=os.getpid(), tag="smoke")
    rm = sched.write_turn(launcher("am", expect=1, store="memory"), stand="SX", runs=["r1"], units=["u6"], ops_for=OPS,
                          ceilings={"u6": 60.0}, status_ids={"r1": sm})[("r1", "u6")]
    check("the memory-store unit's client is kept, its child still alive and answering",
          rm.client is not None and rm.client.child.process.poll() is None
          and rm.client.request("read", timeout=10, qid="q", query="x")["items"][0]["item_id"] == "u6-i0")
    rm.client.close(timeout=10)
    status.end(sm, rc=0, wall_s=1.0, units=1, out="runs/am.json")
    status.block_end("SX", "b03")
    check("the STATUS file stays valid for the writer's own replay", SL.self_check(TMP / "STATUS") == [],
          str(SL.self_check(TMP / "STATUS")))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 scheduler turns: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
