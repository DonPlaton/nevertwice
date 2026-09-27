#!/usr/bin/env python3
"""PREREG-V3 TB4.11a A1 (A6): research/v3/scheduler.py - the scheduler's pure part (rev1 §3.4, §5.6; the auditor's
Q25, Q26, D1, D4, D5).

* ceiling_s: 3 x the arm's pilot median seconds per input token x the unit's input tokens, at least 10 min (§5.6);
  ceiling_for: smoke and debug runs take the declared 6 h debug ceiling (Q26), a scored run needs its frozen median;
* project_hours (the D5 candidate for Q26): per block the write stage is the slowest (run, unit) of its ops at the p95
  hops, or the embed ceiling if that is slower, the question stage the slowest (run, unit) of its questions; the judge
  hours are computed and printed, never budgeted (M-SCHED-budget-includes-judge);
* prefix_for: the largest block boundary whose projection fits the stand's budget; below the floor the arm is
  needs-other-env:compute-budget(<h>) (§3.4); units_for is the order's prefix, never a selection
  (M-SCHED-prefix-not-nested);
* arm_order (D4): the block's seed is the first 8 bytes of sha256("nvt3-arm-order|<campaign seed>|<stand>|<block>"),
  big-endian; the order is status_log.seeded_order - the STATUS writer and m2_v3 S10 recompute the same;
* wall_hours (R-Q25-T): arms take their stages one at a time (Q25(2)), so a stand's wall time is the sum over its
  arms of write + questions - A9 publishes it beside the per-arm projections; campaign_wall_hours sums the stands;
* STAGES are the proxy's stage names accounting reads (M-SCHED-stage-name);
* A4 spawn_child: the one spawn path - fresh unit directories, the spec, the environment, launch.spawn - one spawn
  at a time; T10 races 8 threads x 2 spawns with the writer's lock neutralised and the last-line read widened (16
  lines, the chain whole), T10b shows the same race breaks without the scheduler's lock (M-SCHED-spawn-unlocked).

    python tests/research/_test_v3_scheduler.py
"""
from __future__ import annotations

import hashlib
import json
import importlib.util
import math
import sys
from pathlib import Path

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


SC = _load("v3_scheduler", ROOT / "research" / "v3" / "scheduler.py")
AC = _load("v3_accounting_for_scheduler", ROOT / "research" / "v3" / "accounting.py")
SL = _load("v3_status_log_for_scheduler", ROOT / "research" / "v3" / "status_log.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def err(fn) -> str:
    try:
        fn()
        return "no error"
    except SC.SchedulerError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"not a SchedulerError: {type(e).__name__}: {e}"


print("- T1 ceiling_s: 3 x median s/token x tokens, at least 10 min -")
for med, tok, want in ((0.01, 1000, 600.0), (0.5, 1000, 1500.0), (1.0, 1000, 3000.0), (0.2, 1000, 600.0), (0.2001, 1000, 600.3)):
    got = SC.ceiling_s(med, tok)
    check(f"ceiling_s({med}, {tok}) = {want}", abs(got - want) < 1e-9, str(got))
check("a negative, NaN or bool median refuses", all("median" in err(lambda m=m: SC.ceiling_s(m, 1000))
                                                    for m in (-0.1, math.nan, True)))
check("a unit with no input tokens refuses - a ceiling of the floor would hide an empty unit",
      "tokens" in err(lambda: SC.ceiling_s(0.5, 0)))

print("\n- T2 ceiling_for: the Q26 debug ceiling for smoke and debug, the frozen median for scored -")
MED = {("mem0", "S1"): 0.5}
check("smoke and debug take 6 h whatever the median", SC.ceiling_for("smoke", arm="x", stand="S1", unit_tokens=10**9,
                                                                    medians={}) == 21600.0
      and SC.ceiling_for("debug", arm="mem0", stand="S1", unit_tokens=1, medians=MED) == 21600.0)
check("scored takes the arm's frozen median on the stand", SC.ceiling_for("scored", arm="mem0", stand="S1",
                                                                         unit_tokens=1000, medians=MED) == 1500.0)
check("a scored run without a frozen median refuses by name",
      "no frozen median" in err(lambda: SC.ceiling_for("scored", arm="zep", stand="S1", unit_tokens=1000, medians=MED)))
check("an unknown tag refuses", "tag" in err(lambda: SC.ceiling_for("best", arm="mem0", stand="S1", unit_tokens=1,
                                                                    medians=MED)))

print("\n- T3/T4 project_hours and prefix_for -")
HOPS = SC.HopStats(hop95_llm=2.0, hop95_embed=0.5, hop95_read=1.0, hop95_reader=3.0, embed_ceiling=10.0,
                   judge_s_per_question=4.0)
# one block: 2 units x 2 runs; unit u1 (run r1) is the slowest writer: 3 ops of (2 llm, 4 embed) = 3 x (4 + 2) = 18 s
blk = SC.BlockLoad(block="b01", units=(
    SC.UnitLoad(run="r1", unit="u1", ops=((2, 4), (2, 4), (2, 4)), questions=(3, 3)),
    SC.UnitLoad(run="r1", unit="u2", ops=((1, 1),), questions=(1,)),
    SC.UnitLoad(run="r2", unit="u1", ops=((1, 0),), questions=(3, 3, 3)),
    SC.UnitLoad(run="r2", unit="u2", ops=((1, 1),), questions=(1,))), embeds=60)
pj = SC.project_hours(HOPS, [blk])
check("write: the slowest (run, unit) at the p95 hops - 18 s, over the embed ceiling's 60/10 = 6 s",
      abs(pj.write_h - 18 / 3600) < 1e-12, str(pj))
check("questions: the slowest (run, unit) - 9 points x (1 + 3) s = 36 s", abs(pj.question_h - 36 / 3600) < 1e-12, str(pj))
check("judges: every question of every (run, unit) - 2 + 1 + 3 + 1 - x 4 s, computed apart",
      abs(pj.judge_h - 7 * 4 / 3600) < 1e-12, str(pj))
blk_e = SC.BlockLoad(block="b02", units=(SC.UnitLoad(run="r1", unit="u3", ops=((1, 1),), questions=(1,)),), embeds=1000)
check("the embed ceiling bounds the write stage when it is slower (1000 embeds / 10 per s = 100 s)",
      abs(SC.project_hours(HOPS, [blk_e]).write_h - 100 / 3600) < 1e-12)
check("blocks add up", abs(SC.project_hours(HOPS, [blk, blk_e]).budgeted_h - (18 + 36 + 100 + 4) / 3600) < 1e-12)
check("M-SCHED-budget-includes-judge: budgeted_h = write + questions, the judges never in it",
      abs(pj.budgeted_h - (18 + 36) / 3600) < 1e-12, str(pj.budgeted_h))
proj = {10: 3.0, 20: 5.9, 30: 6.0, 40: 6.1}.__getitem__
check("the largest block boundary whose projection fits (6 h budget: 30, the bound inclusive)",
      SC.prefix_for("mem0", "S5", blocks=[10, 20, 30, 40], projection_h=proj, budget_h=6.0, floor=10) == 30)
check("a boundary under the floor that fits is still below the floor - needs-other-env with the floor's projection",
      SC.prefix_for("mem0", "S5", blocks=[10, 20, 30, 40], projection_h=proj, budget_h=5.0, floor=20)
      == "needs-other-env:compute-budget(5.9h)")
check("S4: a prefix exactly at the floor whose projection fits is taken, not needs-other-env",
      SC.prefix_for("mem0", "S5", blocks=[10, 20, 30, 40], projection_h=proj, budget_h=5.9, floor=20) == 20)
check("nothing fits - needs-other-env", SC.prefix_for("mem0", "S5", blocks=[10, 20], projection_h=proj, budget_h=1.0,
                                                      floor=10) == "needs-other-env:compute-budget(3.0h)")
check("the reason is in the m4 vocabulary (needs-other-env:<word>...)", SC.prefix_for(
    "mem0", "S5", blocks=[10], projection_h=proj, budget_h=1.0, floor=10).startswith("needs-other-env:compute-budget("))
check("block boundaries must increase", "increase" in err(lambda: SC.prefix_for(
    "mem0", "S5", blocks=[20, 10], projection_h=proj, budget_h=9.0, floor=10)))
loads4 = [blk] * 4
jfit = SC.prefix_for("mem0", "S5", blocks=[2, 4, 6, 8], budget_h=4 * (18 + 36) / 3600,
                     projection_h=lambda n: SC.project_hours(HOPS, loads4[:n // 2]).budgeted_h, floor=2)
check("T4: a budget that fits 4 blocks of write + questions takes all 4 - the judge hours do not cut it", jfit == 8, str(jfit))

print("\n- R-Q25-T wall_hours: arms take their stages one at a time, so a stand's wall time is the sum over arms -")
PER_ARM = {"mem0": SC.Projection(write_h=0.2, question_h=0.3, judge_h=5.0),
           "zep": SC.Projection(write_h=0.4, question_h=0.3, judge_h=5.0)}
check("wall_hours(stand) = the sum over arms of write + questions, the judges apart",
      abs(SC.wall_hours(PER_ARM) - 1.2) < 1e-12, str(SC.wall_hours(PER_ARM)))
check("the campaign's wall time is the sum over its stands",
      abs(SC.campaign_wall_hours({"S1": PER_ARM, "S5": {"mem0": SC.Projection(1.0, 1.0, 0.0)}}) - 3.2) < 1e-12)
check("a stand with no arm refuses", "no arm" in err(lambda: SC.wall_hours({})))

print("\n- T5 units_for: the order's prefix, nested -")
ORDER = ["q7", "q3", "q9", "q1", "q5"]
check("units_for(order, n) = order[:n]", SC.units_for(ORDER, 3) == ["q7", "q3", "q9"])
check("M-SCHED-prefix-not-nested: every smaller prefix is inside every larger one",
      all(set(SC.units_for(ORDER, a)) <= set(SC.units_for(ORDER, b)) for a in range(6) for b in range(a, 6)))
check("n beyond the order refuses", "beyond" in err(lambda: SC.units_for(ORDER, 6)))

print("\n- T6 arm_order (D4): the block seed from the declared string, the order from status_log -")
ARMS = ["mem0", "nevertwice", "letta", "zep"]
order, seed = SC.arm_order(ARMS, campaign_seed=20260926, stand="S5", block="b01")
want_seed = int.from_bytes(hashlib.sha256("nvt3-arm-order|20260926|S5|b01".encode("utf-8")).digest()[:8], "big")
check("seed = the first 8 bytes, big-endian, of sha256('nvt3-arm-order|<campaign seed>|<stand>|<block>')",
      seed == want_seed, f"{seed} vs {want_seed}")
check("the order is status_log.seeded_order - what the STATUS writer and m2_v3 S10 recompute",
      order == SL.seeded_order(ARMS, seed) == sorted(ARMS, key=lambda a: hashlib.sha256(f"{seed}|{a}".encode()).hexdigest()))
check("another block, another seed", SC.arm_order(ARMS, campaign_seed=20260926, stand="S5", block="b02")[1] != seed)
check("the order does not depend on the arms' listing order", SC.arm_order(list(reversed(ARMS)), campaign_seed=20260926,
                                                                            stand="S5", block="b01") == (order, seed))
check("duplicate arms refuse", "distinct" in err(lambda: SC.arm_order(["a", "a"], campaign_seed=1, stand="S5", block="b01")))

print("\n- T7 STAGES -")
check("M-SCHED-stage-name: the stage names are the ones accounting attributes (write, questions)",
      SC.STAGES == tuple(AC.STAGE_PHASE))
check("the §5.6 budgets", SC.BUDGET_H == {"S6-SH": 24, "S6-MH": 24, "S5": 48, "S7": 24, "S4": 24, "S1": 72})

print("\n- A4 spawn_child: the one spawn path, one spawn at a time (M-SCHED-spawn-unlocked) -")
import contextlib  # noqa: E402
import os  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from types import SimpleNamespace  # noqa: E402

L = _load("v3_launch_for_scheduler", ROOT / "research" / "v3" / "launch.py")
TMPS = Path(tempfile.mkdtemp(prefix="nvt3_sched_"))
SYSTEM = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32",) if os.name == "nt" else (Path("/usr/bin"),)
EXC = {sys.executable: "the test interpreter"}
if getattr(sys, "_base_executable", sys.executable) != sys.executable:
    EXC[sys._base_executable] = "the test interpreter's base"
(TMPS / "owner_home").mkdir()


def contract(tag: str):
    poly = TMPS / tag / "polygon"
    return L.Contract(polygon_root=poly, runs_root=poly / "runs" / "v3", repo_root=ROOT, owner_home=TMPS / "owner_home",
                      secrets_dir=TMPS / "secrets", quarantine_root=TMPS / "quarantine",
                      conservation_root=TMPS / "conservation", system_dirs=SYSTEM, binary_exceptions=EXC)


class StubNative:
    """The native egress witness in place (A2.2 is its own suite): a required spawn needs one; it registers anything."""
    jobs = None

    def register(self, pid, handle=None, label=None) -> bool:
        return True


class FakePopen:
    """Starts nothing: a pid, and a process that has already exited 0."""
    _next = [40000]
    _lock = threading.Lock()

    def __init__(self, args, env=None, cwd=None, **kw) -> None:
        with FakePopen._lock:
            FakePopen._next[0] += 1
            self.pid = FakePopen._next[0]
        self.args, self.cwd, self.env = args, cwd, env

    def poll(self) -> int:
        return 0

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None:
        pass


def sched(tag: str):
    return SC.Scheduler(contract(tag), None, None, L, None, None, tag="smoke", witnesses=SimpleNamespace(native=StubNative()),
                        parent_env=dict(os.environ), catcher_url="http://127.0.0.1:47001", popen=FakePopen)


SPEC = lambda d: SC.LaunchSpec(argv=(sys.executable, "-c", "pass"), path_dirs=(str(Path(sys.executable).parent),))  # noqa: E731
s1 = sched("one")
child, dirs = s1.spawn_child(SPEC, role="arm-write", stand="S1", run="r1", arm="mem0", unit="u1")
recs = [json.loads(x) for x in L.spawns_log(s1.c).read_bytes().decode().splitlines()]
check("spawn_child: a fresh unit directory under <runs>/<stand>/<run>/<arm>/<unit>, one spawn record naming it",
      dirs.cwd == s1.c.runs_root / "S1" / "r1" / "mem0" / "u1" and len(recs) == 1
      and {k: recs[0][k] for k in ("role", "stand", "run", "arm", "unit")} == {"role": "arm-write", "stand": "S1",
                                                                             "run": "r1", "arm": "mem0", "unit": "u1"}
      and recs[0]["refused"] is False and child.process.pid > 40000, str(recs))
check("the build callback gets the unit's directories (the spec is written from them)", SPEC(dirs).argv[0] == sys.executable)
bad = SC.LaunchSpec(argv=(str(TMPS / "not-a-binary.exe"),))
try:
    s1.spawn_child(lambda d: bad, role="arm-write", stand="S1", run="r1", arm="mem0", unit="u2")
    refusal = "spawned"
except L.ContractViolation as e:
    refusal = str(e)
check("a refused spawn raises the contract's refusal, and the lock is released - the next spawn goes through",
      refusal != "spawned" and s1.spawn_child(SPEC, role="arm-write", stand="S1", run="r1", arm="mem0", unit="u3")[0] is not None,
      refusal)
own = L.make_unit_dirs(s1.c, "S1", "r1", "mem0", "u10")
ch10, d10 = s1.spawn_child(SPEC, role="arm-write", stand="S1", run="r1", arm="mem0", unit="u10", dirs=own)
check("given directories are used as they are - never made a second time (Q-47-6's reuse passes its unit's own)",
      d10 is own and Path(ch10.process.cwd) == own.cwd)
w = L.Window(name="win", hosts=("registry.npmjs.org",))
ch11, _d11 = s1.spawn_child(SPEC, role="fetch", stand="S1", run="r1", arm="mem0", unit="u11", window=w)
check("a spawn made inside a window is that window's root (its egress is filed as window hosts, W3)",
      ch11.process.pid in w.roots)
check("the environment is the contract's, offline for HF by default", ch11.process.env.get("HF_HUB_OFFLINE") == "1"
      and ch11.process.env.get("HTTP_PROXY") == "http://127.0.0.1:47001")
check("a tag outside scored / smoke / debug refuses", "tag" in err(lambda: SC.Scheduler(
    contract("x"), None, None, L, None, None, tag="best", witnesses=None, parent_env={}, catcher_url="")))

N_THREADS, PER_THREAD = 8, 2


def race(tag: str, *, unlock_scheduler: bool) -> tuple[int, bool, int]:
    """8 threads behind a barrier, 2 spawns each, with the writer's own lock (B11) neutralised and the log's last-line
    read widened (each read waits for every thread to read, or 300 ms) - only the scheduler's lock serialises."""
    s = sched(tag)
    if unlock_scheduler:
        s._spawn_lock = contextlib.nullcontext()
    saved_lock, saved_last = L.file_lock, L._last_line
    gate = {"n": 0}
    cond = threading.Condition()

    def slow_last(path):
        line = saved_last(path)
        with cond:
            gate["n"] += 1
            cond.notify_all()
            cond.wait_for(lambda: gate["n"] >= N_THREADS, timeout=0.3)
        return line

    L.file_lock, L._last_line = (lambda path, **kw: contextlib.nullcontext()), slow_last
    barrier = threading.Barrier(N_THREADS)

    def worker(k: int) -> None:
        barrier.wait()
        for j in range(PER_THREAD):
            s.spawn_child(SPEC, role="arm-write", stand="S5", run="r1", arm="mem0", unit=f"t{k}-{j}")

    try:
        ts = [threading.Thread(target=worker, args=(k,)) for k in range(N_THREADS)]
        for th in ts:
            th.start()
        for th in ts:
            th.join(60)
    finally:
        L.file_lock, L._last_line = saved_lock, saved_last
    log = L.spawns_log(s.c)
    lines = log.read_bytes().decode().splitlines() if log.exists() else []
    units = {json.loads(x)["unit"] for x in lines}
    return len(lines), L.verify_chain(log), len(units)


n, chained, distinct = race("race", unlock_scheduler=False)
check("T10: 16 spawns from 8 threads at once - 16 lines, the chain whole, every unit recorded once",
      (n, chained, distinct) == (16, True, 16), f"{n} lines, chain {chained}, {distinct} units")
n2, chained2, distinct2 = race("race-unlocked", unlock_scheduler=True)
check("T10b (the row sees what it guards): without the scheduler's lock the same race breaks the chain or loses lines",
      not chained2 or n2 != 16 or distinct2 != 16, f"{n2} lines, chain {chained2}, {distinct2} units")
import shutil  # noqa: E402
shutil.rmtree(TMPS, ignore_errors=True)

print(f"\nv3 scheduler: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
