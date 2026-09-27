#!/usr/bin/env python3
"""PREREG-V3 TB4.11a (A6): the v3 scheduler - A1, its pure part (rev1 §3.4, §5.6; the auditor's Q25, Q26, D1, D4, D5).

* ceiling_s: the per-unit wall-clock ceiling of §5.6 - 3 x the arm's pilot median seconds per input token x the unit's
  input tokens, at least 10 minutes; D1: it bounds the unit's own active time (its write and its read intervals, never
  a barrier wait or a re-ask pause), and the pilot's median is measured the same way. ceiling_for: smoke and debug runs
  take the declared 6 h debug ceiling (Q26); a scored run needs the arm's frozen median on the stand;
* project_hours: the D5 candidate for the Q26 projection formula (the auditor judges it before A9) - per block,
  T_w = max(the slowest (run, unit)'s sum over its ops of llm_calls x hop95_llm + embed_calls x hop95_embed, the
  block's embeds / the embed ceiling) and T_q = the slowest (run, unit)'s sum over its questions of points x
  (hop95_read + hop95_reader); the judge hours are computed and printed, never budgeted (§5.6: judge stages excluded);
* prefix_for: §3.4 - the largest block boundary whose projection fits the stand's budget (whole blocks, so every block
  keeps block x runs concurrency for every arm, §5.6); below the floor the arm is
  needs-other-env:compute-budget(<projected h at the floor>); units_for: the committed order's prefix, never a
  selection;
* wall_hours (R-Q25-T): a stand's wall time is the sum over its arms of write + questions, since arms take their
  stages one at a time (Q25(2)); campaign_wall_hours sums the stands - A9 publishes both beside the per-arm projections;
* arm_order (D4): the block's seed is the first 8 bytes, big-endian, of
  sha256(f"nvt3-arm-order|{campaign_seed}|{stand}|{block}".encode("utf-8")) - the campaign seed is FREEZE-V3's - and
  the order is status_log.seeded_order(arms, seed): the arms sorted by sha256(f"{seed}|{arm}").hexdigest(), which the
  STATUS writer checks on BLOCK START and m2_v3 S10 recomputes;
* STAGES: the proxy's stage names, the keys accounting attributes a write-port call's phase by.

A4 - the Scheduler's skeleton and its one spawn path: spawn_child() makes the unit's fresh directories (or takes the
ones a Claude Code unit reuses, Q-47-6), lets the caller write the unit's spec from them, builds the environment from
the contract's allowlist and hands everything to launch.spawn - one spawn at a time in this process. The process-global
fresh-directory set, the Claude Code unit table and the spawns log's chain then see one spawn after another (the
writer's own file lock, B11, covers a second process); the child itself runs outside the lock.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
STAGES = ("write", "questions")
TAGS = ("scored", "smoke", "debug")
CEILING_FLOOR_S, CEILING_FACTOR = 600.0, 3.0              # §5.6: at least 10 min; 3 x the pilot median
DEBUG_CEILING_S = 6 * 3600.0                             # Q26: the declared debug ceiling for the pilot and smoke
BUDGET_H = {"S6-SH": 24, "S6-MH": 24, "S5": 48, "S7": 24, "S4": 24, "S1": 72}   # §5.6, judge stages excluded
ARM_ORDER_DOMAIN = "nvt3-arm-order"


class SchedulerError(RuntimeError):
    """A plan or a value the preregistration does not allow; nothing was scheduled."""


class SystemClock:
    """The scheduler's clock: UTC for records, monotonic for ceilings."""

    @staticmethod
    def utc():
        import datetime as _dt  # noqa: PLC0415
        return _dt.datetime.now(_dt.timezone.utc)

    @staticmethod
    def monotonic() -> float:
        import time as _time  # noqa: PLC0415
        return _time.monotonic()


def _status_log():
    """research/v3/status_log.py, loaded once from this directory (the D4 order is its seeded_order)."""
    mod = sys.modules.get("v3_status_log_for_scheduler")
    if mod is None:
        spec = importlib.util.spec_from_file_location("v3_status_log_for_scheduler", HERE / "status_log.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["v3_status_log_for_scheduler"] = mod
        spec.loader.exec_module(mod)
    return mod


def _real(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ── ceilings ───────────────────────────────────────────────────────────────────────────────────────────────────

def ceiling_s(median_s_per_token: float, unit_tokens: int) -> float:
    """§5.6: max(10 min, 3 x the arm's pilot median seconds per input token x the unit's input tokens)."""
    if not (_real(median_s_per_token) and median_s_per_token >= 0):
        raise SchedulerError(f"median {median_s_per_token!r} s/token is not a measured non-negative number")
    if not (isinstance(unit_tokens, int) and not isinstance(unit_tokens, bool) and unit_tokens > 0):
        raise SchedulerError(f"a unit with {unit_tokens!r} input tokens has no ceiling - an empty unit is a finding")
    return max(CEILING_FLOOR_S, CEILING_FACTOR * median_s_per_token * unit_tokens)


def ceiling_for(tag: str, *, arm: str, stand: str, unit_tokens: int,
                medians: Mapping[tuple[str, str], float]) -> float:
    """A unit's ceiling for a run of this tag: smoke and debug take the declared debug ceiling (Q26), a scored run the
    arm's frozen median on the stand (the per-unit-ceilings slot, filled from the pilot)."""
    if tag not in TAGS:
        raise SchedulerError(f"tag {tag!r} is not one of {TAGS}")
    if tag != "scored":
        return DEBUG_CEILING_S
    if (arm, stand) not in medians:
        raise SchedulerError(f"{arm} on {stand} has no frozen median s/token - a scored run needs the pilot's")
    return ceiling_s(medians[(arm, stand)], unit_tokens)


# ── projection and prefixes ────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HopStats:
    """One arm's pilot measurements on a stand (A9 publishes them): p95 seconds per sequential hop, the embed ceiling
    in embeds per second, and the judges' seconds per question."""
    hop95_llm: float
    hop95_embed: float
    hop95_read: float
    hop95_reader: float
    embed_ceiling: float
    judge_s_per_question: float


@dataclass(frozen=True)
class UnitLoad:
    """One (run, unit) of a block: its write ops as (llm_calls, embed_calls) and its questions as reader points."""
    run: str
    unit: str
    ops: tuple = ()
    questions: tuple = ()


@dataclass(frozen=True)
class BlockLoad:
    block: str
    units: tuple
    embeds: int = 0


@dataclass(frozen=True)
class Projection:
    write_h: float
    question_h: float
    judge_h: float

    @property
    def budgeted_h(self) -> float:
        """§5.6: the budget covers the write and question stages; the judge stages are excluded."""
        return self.write_h + self.question_h


def project_hours(stats: HopStats, loads: Sequence[BlockLoad]) -> Projection:
    """The D5 candidate: blocks run one after another (the S3 barrier); inside a block the units of every run go in
    parallel, so a stage lasts as long as its slowest (run, unit)."""
    if not (_real(stats.embed_ceiling) and stats.embed_ceiling > 0):
        raise SchedulerError("the embed ceiling is a measured rate > 0")
    w = q = j = 0.0
    for b in loads:
        if not b.units:
            raise SchedulerError(f"block {b.block} has no units")
        w += max(max(sum(llm * stats.hop95_llm + emb * stats.hop95_embed for llm, emb in u.ops) for u in b.units),
                 b.embeds / stats.embed_ceiling)
        q += max(sum(p * (stats.hop95_read + stats.hop95_reader) for p in u.questions) for u in b.units)
        j += sum(len(u.questions) for u in b.units) * stats.judge_s_per_question
    return Projection(write_h=w / 3600, question_h=q / 3600, judge_h=j / 3600)


def wall_hours(per_arm: Mapping[str, Projection]) -> float:
    """R-Q25-T: a stand's wall time - its arms take their stages one at a time (Q25(2)), so the sum over the arms of
    write + questions (judges apart). A9 publishes it beside the per-arm projections; an unacceptable total is a fork
    for A10 (parallel arms under Q25(5)'s RAM margin, or the owner's decision), never a silent cut."""
    if not per_arm:
        raise SchedulerError("a stand with no arm has no wall time")
    return sum(p.budgeted_h for p in per_arm.values())


def campaign_wall_hours(per_stand: Mapping[str, Mapping[str, Projection]]) -> float:
    """The campaign's wall time: stands run one after another (§5.6 order), so the sum of their wall times."""
    return sum(wall_hours(arms) for arms in per_stand.values())


def prefix_for(arm: str, stand: str, *, blocks: Sequence[int], projection_h: Callable[[int], float], budget_h: float,
               floor: int) -> int | str:
    """§3.4: the largest committed prefix - a block boundary, as a cumulative unit count - whose projection fits the
    stand's budget (the bound inclusive); below the floor, needs-other-env:compute-budget(<h at the floor>)."""
    blocks = list(blocks)
    if not blocks or any(b <= a for a, b in zip([0] + blocks, blocks)):
        raise SchedulerError(f"{arm} on {stand}: block boundaries must increase from 1, got {blocks}")
    fit = [n for n in blocks if projection_h(n) <= budget_h]
    best = max(fit, default=None)
    if best is None or best < floor:
        at = next((n for n in blocks if n >= floor), blocks[-1])
        return f"needs-other-env:compute-budget({projection_h(at):.1f}h)"
    return best


def units_for(order: Sequence[str], n: int) -> list[str]:
    """The committed order's first n units - a prefix, never a selection (§3.4: lowering n cannot select questions)."""
    if not (isinstance(n, int) and 0 <= n <= len(order)):
        raise SchedulerError(f"a prefix of {n!r} units is beyond the order's {len(order)}")
    return list(order[:n])


# ── the arm order (D4) ─────────────────────────────────────────────────────────────────────────────────────────

def block_seed(campaign_seed: int, stand: str, block: str) -> int:
    """D4: the first 8 bytes, big-endian, of sha256(f"nvt3-arm-order|{campaign_seed}|{stand}|{block}") in UTF-8."""
    text = f"{ARM_ORDER_DOMAIN}|{campaign_seed}|{stand}|{block}"
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def arm_order(arms: Sequence[str], *, campaign_seed: int, stand: str, block: str) -> tuple[list[str], int]:
    """(the block's arm order, its seed) - the order the STATUS writer checks against seed= (D4)."""
    arms = list(arms)
    if len(set(arms)) != len(arms) or not arms:
        raise SchedulerError(f"a block's arms are a non-empty list of distinct names, got {arms}")
    seed = block_seed(campaign_seed, stand, block)
    return _status_log().seeded_order(arms, seed), seed


# ── A4: the skeleton and the spawn path ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LaunchSpec:
    """What one child needs beyond its unit directories: its argv and the declared additions to the contract's
    environment, with the named exceptions launch.spawn checks (§2.6)."""
    argv: tuple
    declared: Mapping[str, str] = field(default_factory=dict)
    path_dirs: tuple = ()
    token_names: tuple = ()
    claude_names: tuple = ()
    env_exception: Mapping | None = None
    argv_exception: Mapping | None = None
    hf_offline: bool = True
    requirement: str = "required"
    unwitnessed_reason: str | None = None
    pipes: bool = False                        # stdin and stdout are the arm protocol's pipes (arms/base.py)
    stderr_path: str | None = None             # the child's stderr, appended to a file in its fake home - never a PIPE


class Scheduler:
    """One campaign's scheduler (TB4.11a). A4 holds its collaborators and the spawn path; the stages, blocks and stands
    follow in A5-A7."""

    def __init__(self, contract: Any, proxy_ctl: Any, status: Any, launch: Any, clock: Any, ollama_ctl: Any, *,
                 tag: str, witnesses: Any, parent_env: Mapping[str, str], catcher_url: str,
                 canaries: Sequence[str] = (), popen: Callable[..., Any] = subprocess.Popen,
                 concurrency: int | None = None, hooks: Any = None) -> None:
        if tag not in TAGS:
            raise SchedulerError(f"tag {tag!r} is not one of {TAGS}")
        self.c, self.proxy_ctl, self.status, self.launch = contract, proxy_ctl, status, launch
        self.clock, self.ollama_ctl, self.tag, self.witnesses = clock, ollama_ctl, tag, witnesses
        self.parent_env, self.catcher_url, self.canaries = dict(parent_env), catcher_url, tuple(canaries)
        self.popen, self.concurrency, self.hooks = popen, concurrency, hooks
        self._spawn_lock = threading.Lock()
        self.pid = os.getpid()                        # D3: every START names the scheduler's pid

    def spawn_child(self, build: Callable[[Any], LaunchSpec], *, role: str, stand: str, run: str, arm: str,
                    unit: str, dirs: Any = None, window: Any = None) -> tuple[Any, Any]:
        """(the child, its unit directories). Under the one spawn lock: the unit's fresh directories (or ``dirs``, a
        Claude Code unit's own, Q-47-6), the caller's spec written from them, the contract's environment, and
        launch.spawn - which records the spawn, refuses what the contract forbids, and starts the child."""
        with self._spawn_lock:
            d = dirs if dirs is not None else self.launch.make_unit_dirs(self.c, stand, run, arm, unit)
            spec = build(d)
            env = self.launch.build_env(self.c, parent_env=self.parent_env, unit=d, path_dirs=spec.path_dirs,
                                        declared=dict(spec.declared), catcher_url=self.catcher_url,
                                        hf_offline=spec.hf_offline)
            kw: dict = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE} if spec.pipes else {}
            err = open(spec.stderr_path, "ab") if spec.stderr_path else None   # noqa: SIM115 - handed to the child
            if err is not None:
                kw["stderr"] = err
            try:
                child = self.launch.spawn(
                    self.c, list(spec.argv), env=env, cwd=d.cwd,
                    record={"role": role, "stand": stand, "run": run, "arm": arm, "unit": unit},
                    parent_env=self.parent_env, catcher_url=self.catcher_url, token_names=spec.token_names,
                    claude_names=spec.claude_names, env_exception=spec.env_exception,
                    argv_exception=spec.argv_exception, canaries=self.canaries, popen=self.popen,
                    witnesses=self.witnesses, requirement=spec.requirement,
                    unwitnessed_reason=spec.unwitnessed_reason, window=window, **kw)
            finally:
                if err is not None:
                    err.close()                  # the child holds its own handle
        return child, d

    def write_turn(self, launcher: Any, *, stand: str, runs: Sequence[str], units: Sequence[str],
                   ops_for: Callable[[str, str], Sequence[Mapping]], ceilings: Mapping[str, float],
                   status_ids: Mapping[str, str]) -> dict:
        """One arm's write stage in a block (A5): {(run, unit): UnitRecord}."""
        return _write_turn(self, launcher, stand=stand, runs=runs, units=units, ops_for=ops_for, ceilings=ceilings,
                           status_ids=status_ids)

    def run_block(self, sp: "StandPlan", bp: "BlockPlan") -> dict:
        """One block (A6): see _run_block."""
        return _run_block(self, sp, bp)

    def _gpu_only(self, tag: str | None) -> None:
        """§5.6: one resident Ollama model per stage - every other resident model unloaded (never woken: only what
        /api/ps names), then /api/ps must name nothing but the tag."""
        if self.ollama_ctl is None:
            return
        keep = None if tag is None else _full_name(tag)
        for m in self.ollama_ctl.ps():
            if _full_name(m) != keep:
                self.ollama_ctl.unload(m)
        stray = [m for m in self.ollama_ctl.ps() if _full_name(m) != keep]
        if stray:
            raise SchedulerError(f"models still resident beside {tag}: {stray} (§5.6)")


# ── A5: the write turn ─────────────────────────────────────────────────────────────────────────────────────────

def _arm_base():
    """research/v3/arms/base.py - the protocol's harness side (ArmClient and its errors)."""
    mod = sys.modules.get("v3_arm_base_for_scheduler")
    if mod is None:
        spec = importlib.util.spec_from_file_location("v3_arm_base_for_scheduler", HERE / "arms" / "base.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["v3_arm_base_for_scheduler"] = mod
        spec.loader.exec_module(mod)
    return mod


class UnitClient:
    """One unit's child: its protocol client and its process tree."""

    def __init__(self, child: Any, *, stage: str) -> None:
        self.child, self.stage = child, stage
        self.arm = _arm_base().ArmClient(child.process)

    @property
    def pid(self) -> int:
        return self.child.process.pid

    def request(self, op: str, *, timeout: float, **fields) -> dict:
        return self.arm.request(op, timeout=timeout, **fields)

    def kill_tree(self) -> None:
        self.child.kill_tree()

    def exit_code(self, timeout: float = 10.0) -> int | None:
        try:
            return self.child.process.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def close(self, *, timeout: float = 30.0) -> int:
        return self.arm.close(timeout=timeout)


class ChildArmLauncher:
    """An arm whose units are children speaking arms/base.py's protocol. ``argv_for(spec_path)`` is the child's argv;
    ``spec_for(stage, stand=, run=, unit=, dirs=, write_dirs=)`` its spec, written as JSON into the unit's fake home -
    never its cwd - before the spawn; stderr goes to <home>/stderr.<stage>.log."""

    def __init__(self, name: str, *, argv_for: Callable[[Path], Sequence[str]], spec_for: Callable[..., dict],
                 store_persistence: str = "disk", path_dirs: Sequence[str] = (), declared: Mapping[str, str] | None = None,
                 token_names: Sequence[str] = (), reads_point: bool = False) -> None:
        if store_persistence not in ("disk", "memory"):
            raise SchedulerError(f"store_persistence {store_persistence!r} is disk or memory (Q25(4))")
        self.name, self.argv_for, self.spec_for = name, argv_for, spec_for
        self.store_persistence, self.path_dirs = store_persistence, tuple(path_dirs)
        self.declared, self.token_names = dict(declared or {}), tuple(token_names)
        self.reads_point = reads_point

    def open(self, stage: str, *, sched: "Scheduler", stand: str, run: str, unit: str, write_dirs: Any = None) -> UnitClient:
        def build(d: Any) -> LaunchSpec:
            spec_path = Path(d.home) / f"spec.{stage}.json"
            spec_path.write_text(json.dumps(self.spec_for(stage, stand=stand, run=run, unit=unit, dirs=d,
                                                          write_dirs=write_dirs), sort_keys=True), encoding="utf-8")
            return LaunchSpec(argv=tuple(self.argv_for(spec_path)), declared=self.declared, path_dirs=self.path_dirs,
                              token_names=self.token_names, pipes=True,
                              stderr_path=str(Path(d.home) / f"stderr.{stage}.log"))
        child, _d = sched.spawn_child(build, role=f"arm-{stage}", stand=stand, run=run, arm=self.name,
                                      unit=unit if stage == "write" else f"{unit}.q")
        client = UnitClient(child, stage=stage)
        client.dirs = _d
        return client


@dataclass
class UnitRecord:
    """One (arm, run, unit)'s write stage, as the out= record and P1 (Q12) read it."""
    arm: str
    run: str
    unit: str
    spawn_id: str | None = None
    pid: int | None = None
    ops: list = field(default_factory=list)
    footprint: Any = None
    seal: Any = None
    rc: int | None = None
    active_s: float = 0.0
    aborted: str | None = None             # None | "ceiling" | "crash" (D2)
    error: str | None = None
    end_write_utc: str | None = None       # R9: when end_write returned - a write-port call after it is background
    client: Any = None                     # a memory-store arm's live client, kept for its read stage (Q25(4))
    dirs: Any = None


def _write_unit(sched: "Scheduler", launcher: Any, *, stand: str, run: str, unit: str, ops: Sequence[Mapping],
                ceiling: float, status_id: str) -> UnitRecord:
    """One unit's write stage under its ceiling (D1: the unit's own active time). A ceiling kills the child's tree and
    writes UNIT-ABORT reason=ceiling; a child that dies or breaks the protocol is UNIT-ABORT reason=crash with its exit
    code (D2); a product's ok:false on one write is that operation's error, and the unit goes on."""
    B = _arm_base()
    rec = UnitRecord(arm=launcher.name, run=run, unit=unit)
    start = sched.clock.monotonic()
    deadline = start + ceiling
    left = lambda: deadline - sched.clock.monotonic()  # noqa: E731
    client = None
    try:
        client = launcher.open("write", sched=sched, stand=stand, run=run, unit=unit)
        rec.spawn_id, rec.pid, rec.dirs = client.child.spawn_id, client.pid, getattr(client, "dirs", None)
        if left() <= 0:
            raise B.ArmTimeout("hello: the ceiling passed before the child answered")
        hello = client.request("hello", timeout=left())
        if (hello.get("protocol"), hello.get("arm"), hello.get("stage")) != (B.PROTOCOL, launcher.name, "write"):
            raise B.ArmError(f"hello: {hello.get('protocol')}/{hello.get('arm')}/{hello.get('stage')} is not this arm's "
                             f"write stage")
        for op in ops:
            if left() <= 0:
                raise B.ArmTimeout("write: the ceiling passed")
            t0 = sched.clock.utc().isoformat()
            try:
                out = client.request("write", timeout=left(), **op)
                rec.ops.append({"op_id": out.get("op_id"), "t0": t0, "t1": sched.clock.utc().isoformat(), "ok": True,
                                "error": None})
            except (B.ArmTimeout, B.ArmDied, B.ArmPoisoned):
                raise
            except B.ArmError as e:
                if client.arm.poisoned:
                    raise
                rec.ops.append({"op_id": (op.get("item") or {}).get("item_id"), "t0": t0,
                                "t1": sched.clock.utc().isoformat(), "ok": False, "error": str(e)})
        if left() <= 0:
            raise B.ArmTimeout("end_write: the ceiling passed")
        end = client.request("end_write", timeout=left())
        rec.footprint, rec.seal = end.get("footprint"), end.get("seal")
        rec.end_write_utc = sched.clock.utc().isoformat()
        if launcher.store_persistence == "memory":
            rec.client = client                                       # Q25(4): the store lives in this process
        else:
            rec.rc = client.close(timeout=max(1.0, left()))
    except B.ArmTimeout as e:
        rec.aborted, rec.error = "ceiling", str(e)
        if client is not None:
            client.kill_tree()
            rec.rc = client.exit_code()
        sched.status.unit_abort(status_id, unit, reason="ceiling")
    except B.ArmError as e:
        rec.aborted, rec.error = "crash", str(e)
        if client is not None:
            client.kill_tree()
            rec.rc = client.exit_code()
        sched.status.unit_abort(status_id, unit, reason="crash", rc=rec.rc if rec.rc is not None else -1)
    finally:
        rec.active_s = sched.clock.monotonic() - start
    return rec


def _write_turn(sched: "Scheduler", launcher: Any, *, stand: str, runs: Sequence[str], units: Sequence[str],
                ops_for: Callable[[str, str], Sequence[Mapping]], ceilings: Mapping[str, float],
                status_ids: Mapping[str, str]) -> dict:
    """One arm's write stage in a block: every (run, unit) at once - block size x runs, identical for every arm
    (§5.6), never more than the scheduler's concurrency - and the turn ends when all of them have."""
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
    pairs = [(r, u) for r in runs for u in units]
    width = sched.concurrency or len(pairs)
    with ThreadPoolExecutor(max_workers=width) as pool:
        futs = {(r, u): pool.submit(_write_unit, sched, launcher, stand=stand, run=r, unit=u, ops=ops_for(r, u),
                                    ceiling=ceilings[u], status_id=status_ids[r]) for r, u in pairs}
        return {k: f.result() for k, f in futs.items()}



# ── A6: the question turn and the block ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReadReq:
    """One read of a unit's store: its question, the reader point, and k."""
    qid: str
    query: str
    point: str = "B"
    k: int = 10


@dataclass(frozen=True)
class BlockPlan:
    """A block's units; barrier_read - the change-log read at the barrier before the block's check (Q11, D7), for every
    block after a stand's first (whose read is the STAND START one)."""
    block: str
    units: tuple
    barrier_read: bool = False


@dataclass
class StandPlan:
    """What run_block needs of a stand: its runs and arms (name -> launcher), the campaign seed, each unit's input
    tokens and the frozen medians (the ceilings), the write ops and reads per unit, the answer hook, the embed tag
    that stays resident, and the tree check's commit and dirty flag for the run records (Q2)."""
    stand: str
    runs: tuple
    launchers: Mapping[str, Any]
    campaign_seed: int
    unit_tokens: Mapping[str, int]
    medians: Mapping[tuple, float]
    write_ops: Callable[[str, str, str], Sequence[Mapping]]
    read_plan: Callable[[str], Sequence[ReadReq]]
    answer: Callable[[str, str, str, ReadReq, dict], dict]
    embed_tag: str | None
    commit: str
    dirty: bool


def read_kwargs(launcher: Any, req: ReadReq) -> dict:
    """The read request's fields: qid, query and k, and the point only for an arm whose read takes one (F4)."""
    kw = {"qid": req.qid, "query": req.query, "k": req.k}
    if getattr(launcher, "reads_point", False):
        kw["point"] = req.point
    return kw


def _question_unit(sched: "Scheduler", launcher: Any, sp: StandPlan, *, run: str, unit: str, wrec: UnitRecord,
                   ceiling: float, status_id: str) -> dict:
    """One unit's question stage: a fresh <unit>.q process on the SAME (arm, run, unit)'s store - or the memory-store
    arm's own live process - every read under what is left of the unit's one budget (D1), each answer through the
    answer hook; a ceiling or a crash is UNIT-ABORT as in the write stage."""
    B = _arm_base()
    out: dict = {"reads": [], "counters": None, "rc": None, "aborted": None, "pid": None, "spawn_id": None}
    start = sched.clock.monotonic()
    deadline = start + max(0.0, ceiling - wrec.active_s)
    left = lambda: deadline - sched.clock.monotonic()  # noqa: E731
    client = wrec.client
    try:
        if client is None:
            client = launcher.open("read", sched=sched, stand=sp.stand, run=run, unit=unit, write_dirs=wrec.dirs)
            out["spawn_id"] = client.child.spawn_id
            if left() <= 0:
                raise B.ArmTimeout("hello: the ceiling passed")
            hello = client.request("hello", timeout=left())
            if (hello.get("protocol"), hello.get("arm"), hello.get("stage")) != (B.PROTOCOL, launcher.name, "read"):
                raise B.ArmError(f"hello: not this arm's read stage ({hello.get('arm')}/{hello.get('stage')})")
        out["pid"] = client.pid
        for req in sp.read_plan(unit):
            if left() <= 0:
                raise B.ArmTimeout("read: the ceiling passed")
            t0 = sched.clock.utc().isoformat()
            got = client.request("read", timeout=left(), **read_kwargs(launcher, req))
            t1 = sched.clock.utc().isoformat()
            out["reads"].append({"qid": req.qid, "point": req.point, "t0": t0, "t1": t1,
                                 "answer": sp.answer(launcher.name, run, unit, req, got)})
        if left() <= 0:
            raise B.ArmTimeout("counters: the ceiling passed")
        out["counters"] = client.request("counters", timeout=left())
        out["rc"] = client.close(timeout=max(1.0, left()))
    except B.ArmTimeout as e:
        out["aborted"], out["error"] = "ceiling", str(e)
        if client is not None:
            client.kill_tree()
            out["rc"] = client.exit_code()
        sched.status.unit_abort(status_id, unit, reason="ceiling")
    except B.ArmError as e:
        out["aborted"], out["error"] = "crash", str(e)
        if client is not None:
            client.kill_tree()
            out["rc"] = client.exit_code()
        sched.status.unit_abort(status_id, unit, reason="crash", rc=out["rc"] if out["rc"] is not None else -1)
    finally:
        out["active_s"] = sched.clock.monotonic() - start
    return out


def _question_turn(sched: "Scheduler", launcher: Any, sp: StandPlan, *, wrecs: Mapping, ceilings: Mapping[str, float],
                   status_ids: Mapping[str, str]) -> dict:
    """One arm's question stage in a block, over the units its write stage did not abort, all at once."""
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
    todo = [(r, u) for (r, u), w in wrecs.items() if w.aborted is None]
    if not todo:
        return {}
    with ThreadPoolExecutor(max_workers=sched.concurrency or len(todo)) as pool:
        futs = {(r, u): pool.submit(_question_unit, sched, launcher, sp, run=r, unit=u, wrec=wrecs[(r, u)],
                                    ceiling=ceilings[u], status_id=status_ids[r]) for r, u in todo}
        return {k: f.result() for k, f in futs.items()}


def _run_block(sched: "Scheduler", sp: StandPlan, bp: BlockPlan) -> dict:
    """One block (§5.6, Q4, Q11, Q25): the barrier read before the block's check; the embedder alone on the GPU; the
    check; BLOCK START with the seeded order; every arm-run's START; the write stage, arm after arm; the barrier (every
    write child gone, the embedder alone again); the question stage, arm after arm in the same order; each arm-run's
    raw record (out=, measured inside START..END, Q2) and END; the stage reset; BLOCK END; the check's end."""
    L = sched.launch
    stand, block = sp.stand, bp.block
    events = {}
    if bp.barrier_read and sched.hooks is not None:
        events["barrier_read"] = sched.hooks.barrier_read(stand, block)
    sched._gpu_only(sp.embed_tag)
    check_id = f"{stand}.{block}"
    sched.witnesses.begin_check(check_id)
    order, seed = arm_order(list(sp.launchers), campaign_seed=sp.campaign_seed, stand=stand, block=block)
    sched.status.block_start(stand, block, units=list(bp.units), arm_order=order, seed=seed)
    ids = {(a, r): sched.status.start(stand, block, r, a, pid=sched.pid, tag=sched.tag) for a in order for r in sp.runs}
    ceilings = {a: {u: ceiling_for(sched.tag, arm=a, stand=stand, unit_tokens=sp.unit_tokens[u], medians=sp.medians)
                    for u in bp.units} for a in order}
    sched.proxy_ctl.stage(f"{stand}/{block}", "write")
    wrecs = {a: sched.write_turn(sp.launchers[a], stand=stand, runs=list(sp.runs), units=list(bp.units),
                                 ops_for=lambda r, u, a=a: sp.write_ops(a, r, u), ceilings=ceilings[a],
                                 status_ids={r: ids[(a, r)] for r in sp.runs}) for a in order}
    sched._gpu_only(sp.embed_tag)                        # the barrier: every write child exited or was killed
    sched.proxy_ctl.stage(f"{stand}/{block}", "questions")
    qrecs = {a: _question_turn(sched, sp.launchers[a], sp, wrecs=wrecs[a], ceilings=ceilings[a],
                               status_ids={r: ids[(a, r)] for r in sp.runs}) for a in order}
    art = _artifact()
    for a in order:
        for r in sp.runs:
            units = {u: {"write": _unit_payload(wrecs[a][(r, u)]), "questions": qrecs[a].get((r, u))} for u in bp.units}
            rcs = [x for u in bp.units for x in (wrecs[a][(r, u)].rc, (qrecs[a].get((r, u)) or {}).get("rc"))
                   if wrecs[a][(r, u)].aborted is None and not (qrecs[a].get((r, u)) or {}).get("aborted")]
            rc = next((x for x in rcs if x not in (0, None)), 0)
            wall = sum(wrecs[a][(r, u)].active_s + (qrecs[a].get((r, u)) or {}).get("active_s", 0.0) for u in bp.units)
            rel = f"{stand}/_records/{block}/{r}.{a}.json"
            path = sched.c.runs_root / rel
            sha = art.run_record(path, ids[(a, r)], {"stand": stand, "block": block, "run": r, "arm": a, "units": units},
                                 commit=sp.commit, dirty=sp.dirty, now=sched.clock.utc)
            L._append_jsonl(sched.c.runs_root / "_launch" / "records.jsonl",
                            {"status_id": ids[(a, r)], "path": rel, "sha256": sha})
            sched.status.end(ids[(a, r)], rc=rc, wall_s=wall, units=len(bp.units), out=rel)
    sched.proxy_ctl.stage(None, None)                    # a stray call is now loud in accounting, never a stage's
    sched.status.block_end(stand, block)
    events["check"] = sched.witnesses.end_check(check_id)
    return {"order": order, "seed": seed, "write": wrecs, "questions": qrecs, **events}


def _unit_payload(w: UnitRecord) -> dict:
    return {"spawn_id": w.spawn_id, "pid": w.pid, "ops": w.ops, "footprint": w.footprint, "seal": w.seal, "rc": w.rc,
            "active_s": w.active_s, "aborted": w.aborted, "end_write_utc": w.end_write_utc}


def _artifact():
    mod = sys.modules.get("v3_artifact_for_scheduler")
    if mod is None:
        spec = importlib.util.spec_from_file_location("v3_artifact_for_scheduler", HERE / "artifact.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["v3_artifact_for_scheduler"] = mod
        spec.loader.exec_module(mod)
    return mod


def _full_name(model: str) -> str:
    """Ollama's name with its tag (sched_ctl.full_name's rule): a tagless name is the ":latest" one."""
    return model if ":" in model.rsplit("/", 1)[-1] else f"{model}:latest"
