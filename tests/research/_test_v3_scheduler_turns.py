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
    res1 = s2.run_block(SP, SC.BlockPlan(block="b01", units=("v1", "v2")))
    res2 = s2.run_block(SP, SC.BlockPlan(block="b02", units=("v3", "v4"), barrier_read=True))
    status2.stand("SY", "END", model="m", changelog="2026-09-10")
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
    first_write = {a: min(o["t"] for o in ops if o["arm"] == a and o["op"] == "write" and o["unit"] in ("v1", "v2"))
                   for a in arms6}
    turns = sorted(first_write, key=first_write.get)
    check("T16: the arms took their write turns in the block's seeded order - which here is NOT the sorted order",
          turns == res1["order"] and res1["order"] != sorted(arms6), f"turns {turns}, seeded {res1['order']}")
    wev = [e for e in EV if e[0] in ("begin_check", "end_check", "barrier_read")]
    check("T17 (Q11): the barrier read of block 2 comes after block 1's check ended and before block 2's began",
          wev == [("begin_check", "SY.b01"), ("end_check", "SY.b01"), ("barrier_read", "b02"), ("begin_check", "SY.b02"),
                  ("end_check", "SY.b02")], str(wev))
    mem_w = {(o["run"], o["unit"]) for o in ops if o["arm"] == "a3" and o["op"] == "write"}
    q3 = res1["questions"]["a3"]
    check("T18: the memory-store arm reads from the process that wrote (the same pid), and spawns no read process",
          all(q3[k]["pid"] == res1["write"]["a3"][k].pid and q3[k]["spawn_id"] is None for k in q3) and len(q3) == 4,
          str({k: (q3[k]["pid"], res1["write"]["a3"][k].pid) for k in q3}))
    check("the GPU holds the embedder alone at the block (the other model unloaded, never the embedder)",
          ("unload", "qwen3:8b") in EV and ("unload", "nvt3-bge-m3-d1:latest") not in EV)
    recs = [json.loads(x) for x in (C.runs_root / "_launch" / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    check("every arm-run's raw record is written (out=), chained, and named by its END",
          len(recs) == 12 and L.verify_chain(C.runs_root / "_launch" / "records.jsonl")
          and all((C.runs_root / r["path"]).is_file() for r in recs)
          and all(f" out={r['path']} " in " ".join(lines2) + " " for r in recs), str(recs[:1]))
    one = json.loads((C.runs_root / recs[0]["path"]).read_text(encoding="utf-8"))
    check("the record holds each unit's write (ops, footprint, end_write time) and its questions, measured inside "
          "START..END (Q2)", set(one["units"]) and all(v["write"]["footprint"] == 2 and v["write"]["end_write_utc"]
                                                        for v in one["units"].values())
          and one["measured_at"]["commit"] == "c" * 40, str(one)[:300])

    print("\n- A7: run_stand - the tree check, STAND lines, the gate, the judges one model at a time -")

    class GpuOllama:
        """Residency with a load log: a judge's run loads its model; loading beside another model is a violation."""

        def __init__(self):
            self.resident, self.violations, self.log = {"qwen3:8b"}, [], []

        def ps(self):
            return sorted(self.resident)

        def unload(self, model, *, embedder=False):
            self.log.append(("unload", model))
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
            return {"changelog": "2026-09-10"}

        def model_probe(self):
            self.ev.append(("model_probe",))
            return "deepseek-v4-flash"

        def tree_check(self):
            self.ev.append(("tree_check",))
            return {"clean": self.tree_ok, "problems": [] if self.tree_ok else ["HEAD is not the anchor"]}

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
    s, sp, judges, ev, gpu, st = stand_world("scored", tree_ok=False, stand="ST", sfile="STATUS5")
    try:
        s.run_stand(sp, [SC.BlockPlan(block="b01", units=("w1",))], judges=judges, order=3)
        refusal = "ran"
    except SC.SchedulerError as e:
        refusal = str(e)
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
        res = s.run_block(sp, SC.BlockPlan(block="b01", units=("x1",)))
        return res["questions"]["a1"][("r1", "x1")], vc

    q, vc = w3_world("SW1", "STATUS8", {("r1", "x1"): {"fail_reads": 1}}, lambda *a_: {"sha256": "3" * 64})
    check("T22: a read that failed once is re-asked after 5 min and recovers - the question is answered",
          [r.get("reasks") for r in q["reads"]] == [1, 0] and not any(r.get("unrecovered") for r in q["reads"])
          and vc.sleeps == [300.0], f"{q['reads']} {vc.sleeps}")
    q, vc = w3_world("SW2", "STATUS9", {("r1", "x1"): {"fail_reads": 3}}, lambda *a_: {"sha256": "3" * 64},
                     tag="scored")                        # a scored unit: its ceiling is the 600 s floor
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
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 scheduler turns: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
