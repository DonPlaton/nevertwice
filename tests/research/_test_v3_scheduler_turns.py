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
import json, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base as B
spec = json.load(open(sys.argv[1], encoding="utf-8"))
shared = spec["shared"]
knobs = spec.get("knobs") or {}
STREAMS = {}
_claim = B.claim_stdio


def _claim_and_keep():
    fin, fout = _claim()
    STREAMS["out"] = fout
    return fin, fout


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
        if knobs.get("bad_hello"):                    # another arm's hello: a protocol break the scheduler kills
            return {"protocol": B.PROTOCOL, "arm": "not-" + spec["arm"], "stage": spec["stage"], "pid": os.getpid()}
        if spec["stage"] == "write":                  # a live WRITE child: its marker goes at end_write
            os.makedirs(live_dir(spec["arm"]), exist_ok=True)
            open(os.path.join(live_dir(spec["arm"]), spec["run"] + "." + spec["unit"]), "w").close()
        return {"protocol": B.PROTOCOL, "arm": spec["arm"], "stage": spec["stage"], "pid": os.getpid()}

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
    return SC.ChildArmLauncher(name, argv_for=lambda p: [str(ARM_PY), "-B", str(FAKE_DIR / "fake_arm.py"), str(p)],
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

    for sub in ("live", "passed", "seen", "ops"):
        shutil.rmtree(SHARED / sub, ignore_errors=True)
    (SHARED / "live").mkdir()
    status2 = SL.StatusLog(TMP / "STATUS2", local_tz=dt.timezone.utc)
    s2 = SC.Scheduler(C, FakeProxyCtl(), status2, L, Clock(), FakeOllama(), tag="smoke", witnesses=FakeWitnesses(),
                      parent_env=dict(os.environ), catcher_url="http://127.0.0.1:47001", hooks=Hooks())
    arms6 = {"a1": launcher("a1", expect=4), "a2": launcher("a2", expect=4), "a3": launcher("a3", expect=4, store="memory")}
    answers = []

    def answer(arm, run, unit, req, got):
        answers.append((arm, run, unit, req.qid, got.get("marker")))
        return {"sha256": "1" * 64}

    SP = SC.StandPlan(stand="SY", runs=("r1", "r2"), launchers=arms6, campaign_seed=20260927,     # b01: a3, a1, a2
                      unit_tokens={u: 1000 for u in ("v1", "v2", "v3", "v4")}, medians={},
                      write_ops=lambda a, r, u: OPS(r, u), read_plan=lambda u: [SC.ReadReq(qid=f"{u}-q1", query="x")],
                      answer=answer, embed_tag="nvt3-bge-m3-d1:latest", commit="c" * 40, dirty=False)
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
        """Refuses the first `closed` asks, then admits (the IncidentGate shape the scheduler reads)."""

        def __init__(self, closed):
            self.closed, self.asked = closed, 0

        def admits_new_unit(self):
            self.asked += 1
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
                         catcher_url="http://127.0.0.1:47001", hooks=StandHooks(ev, tree_ok, gate))
        sp = SC.StandPlan(stand=stand, runs=("r1",), launchers={"a1": launcher("a1", expect=1)}, campaign_seed=20260927,
                          unit_tokens={u: 1000 for u in ("w1", "w2")}, medians={("a1", stand): 0.001},
                          write_ops=lambda a, r, u: OPS(r, u), read_plan=lambda u: [SC.ReadReq(qid=f"{u}-q", query="x")],
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
    gate = GateScript(closed=3)
    s, sp, judges, ev, gpu, st = stand_world("smoke", gate=gate, stand="SG", sfile="STATUS7")
    t_gate = time.monotonic()
    s.run_stand(sp, [SC.BlockPlan(block="b01", units=("w1",))], judges=judges, order=5)
    spawns = [json.loads(x) for x in L.spawns_log(C).read_text(encoding="utf-8").splitlines()]
    sg = [x for x in spawns if x.get("stand") == "SG"]
    check("T20: no unit spawns while the gate refuses - it asked until it admitted, then the unit ran",
          gate.asked >= 4 and len(sg) >= 1, f"asked {gate.asked}, spawns {len(sg)}")

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

    def w3_world(stand, sfile, knobs, answer, tag="smoke"):
        for sub in ("live", "passed", "seen", "ops", "fails"):
            shutil.rmtree(SHARED / sub, ignore_errors=True)
        (SHARED / "live").mkdir()
        vc = VirtualClock()
        st = SL.StatusLog(TMP / sfile, now=vc.utc, local_tz=dt.timezone.utc)
        s = SC.Scheduler(C, FakeProxyCtl(), st, L, vc, None, tag=tag, witnesses=FakeWitnesses(),
                         parent_env=dict(os.environ), catcher_url="http://127.0.0.1:47001")
        if tag == "scored":
            st.campaign_start(anchor="c" * 40, prereg="d" * 64, freeze="e" * 64)
        sp = SC.StandPlan(stand=stand, runs=("r1",), launchers={"a1": launcher("a1", expect=1, knobs=knobs)},
                          campaign_seed=20260927, unit_tokens={"x1": 1000}, medians={("a1", stand): 0.0001},
                          write_ops=lambda a, r, u: OPS(r, u),
                          read_plan=lambda u: [SC.ReadReq(qid=f"{u}-q1", query="x"), SC.ReadReq(qid=f"{u}-q2", query="y")],
                          answer=answer, embed_tag=None, commit="c" * 40, dirty=False)
        st.stand(stand, "START", model="m", changelog="2026-09-10", order=1)
        res, err = attempt(lambda: s.run_block(sp, SC.BlockPlan(block="b01", units=("x1",))))
        if err is not None:
            return {"reads": [{"error": repr(err)}, {}], "aborted": f"raised {err!r}"}, vc
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

    print("\n- FIX-SCHED B-RC: a unit's exit code is the child's own; a kill of ours is SIGKILL; a refusal is no unit -")
    B_ = SC._arm_base()
    SC.DIED_WAIT_S = 1.0                     # the grace a child whose stream closed gets to exit on its own

    def aborts(path):
        return [x for x in path.read_text(encoding="utf-8").splitlines() if " UNIT-ABORT " in x]

    def refused_launcher(name):
        """A launcher whose child the launch contract refuses (the binary does not exist): no process is ever made."""
        return SC.ChildArmLauncher(name, argv_for=lambda p: [str(FAKE_DIR / "no_such_arm.exe"), str(p)],
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
                         catcher_url="http://127.0.0.1:47001")
        names = sorted(launchers)
        seed = next(x for x in range(1, 5000)
                    if SC.arm_order(names, campaign_seed=x, stand=stand, block="b01")[0] == names)   # the rows' order
        sp = SC.StandPlan(stand=stand, runs=("r1",), launchers=launchers, campaign_seed=seed,
                          unit_tokens={u: 1000 for u in units}, medians=medians if medians is not None else {},
                          write_ops=lambda a, r, u: OPS(r, u), read_plan=lambda u: [SC.ReadReq(qid=f"{u}-q", query="x")],
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
            name, argv_for=lambda p: ([str(FAKE_DIR / "no_such_arm.exe")] if f"{bad_unit}.home" in str(p)
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
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 scheduler turns: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
