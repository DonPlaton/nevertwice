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

R-LAUNCHER: a spawn needs a way to kill the unit's whole tree - the witness's job object or psutil - else it is refused
before the child exists (tree_kill_route).

FIX-SCHED (the auditor's B-RC, B-OPEN, B-CL, B-TE, R-UNL): a unit's UNIT-ABORT names only what happened - rc= is the
child's own exit code, signal=SIGKILL a kill of ours, and a code that never came is a SchedulerError, never an
invented -1; a spawn the contract refused is no unit at all; a request never gets a timeout the ceiling has spent. A
block or a stand that fails after its first line closes every line it opened (ABORT reason=harness-error, BLOCK END,
STAND END), resets the proxy stage, ends its check and kills its live children, and the error goes on - a cleanup step
that fails is named beside it. A scored stand never writes an unread change log as a value; a dirty tree or an unread
change log at STAND END stops its judges. The stand's D1 embedder is unloaded through /api/embed.

B-GATE-D1: a unit's clock starts when the incident gate admits it - an incident's wait is no unit's active time (D1),
so it neither spends the unit's ceiling (an exogenous wait never becomes the arm's UNIT-ABORT reason=ceiling) nor enters
its active seconds, the END's wall and the pilot's median; B-PAUSE: nor do W3's re-ask pauses, which the deadline
already skipped. B-OPS: a write turn has every unit's ops before its first spawn - a plan that refuses a unit refuses
the turn with no child alive. B-RC at the close: a bye that times out or breaks the protocol is the unit's ArmError
(the tree killed, the ceiling or a crash named), never the root's kill code passed off as the child's own; B-SIGRC: a
POSIX death by signal (returncode -N) is signal=, never rc=. A refused STAND END still closes the stand (B-OPEN), and
a judge failure after STAND END carries the stand's result as partial. B-CUT: a read the unit ended in (its ceiling, a
crash) keeps its row - qid, t0, t1 at the abort, cut: true - so R9's read windows cover it. B-PREFLIGHT: a scored
stand's hooks without a balance preflight are refused before STAND START. B-JPART: a judge failure of any kind (the
unload's ControlError, a judge's own error) is a SchedulerError carrying that partial, its cause kept.
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
GATE_POLL_S = 0.5                                        # how often a closed incident gate is asked again
REASK_MAX, REASK_SPACING_S = 2, 300.0                    # §4.5 W3: the stand's re-asks - at most 2, at least 5 min apart
DIED_WAIT_S = 10.0                                       # B-RC: a child whose stream closed gets this long to exit itself
KILLED = "SIGKILL"                                       # D2: a unit killed by the scheduler is named by the signal
#: B-HELLO: the stage a write child's hello may name - a memory-store arm's one process serves both stages and says so
WRITE_HELLO_STAGES = {"disk": ("write",), "memory": ("write", "both")}
#: R-HOME-CANARY: the decoys launch.plant_canaries writes into a unit's fake home, by the canary each holds.
HOME_CANARY_FILES = {"decoy_claude_md": ".claude/CLAUDE.md", "decoy_credentials": ".claude/.credentials.json",
                     "decoy_claude_json": ".claude.json"}
UNREAD = "unread"                                        # B-CL: what a STAND line says for a value nobody read
_NOT_READ = (None, "", UNREAD, "unknown")                # a change-log read that gave any of these read nothing


class SchedulerError(RuntimeError):
    """A plan or a value the preregistration does not allow, or a step the scheduler could not take; the lines it had
    opened are closed (B-OPEN). ``partial``: what a stand had measured when it stopped after STAND END (B-CL, B-TE)."""
    partial: dict | None = None


class ReaskableError(RuntimeError):
    """The answer hook's transport failure (the reader's call did not come back): the stand may re-ask it (W3)."""


class SystemClock:
    """The scheduler's clock: UTC for records, monotonic for ceilings, sleep for the re-ask pauses."""

    @staticmethod
    def sleep(s: float) -> None:
        import time as _time  # noqa: PLC0415
        _time.sleep(s)

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


def tree_kill_route(witnesses: Any) -> str | None:
    """R-LAUNCHER: how a unit's WHOLE process tree dies - the native witness's job object (W1), else psutil (children
    first, launch.Child.kill_tree); None when neither. A kill would then reach the root alone: an arm started from a
    venv is the grandchild of the venv's launcher, and it would live on - writing to its store and calling the proxy
    after its unit was killed (background writes, R9, and a neighbour unit's store polluted). R-FINDSPEC: psutil counts
    only when `import psutil` succeeds - a file that is there but does not import is the root-only kill again."""
    native = getattr(witnesses, "native", None)
    if native is not None and getattr(native, "jobs", None) is not None:
        return "job"
    try:
        importlib.import_module("psutil")
    except Exception:  # noqa: BLE001 - any failure to import is no route
        return None
    return "psutil"


class Scheduler:
    """One campaign's scheduler (TB4.11a). A4 holds its collaborators and the spawn path; the stages, blocks and stands
    follow in A5-A7."""

    def __init__(self, contract: Any, proxy_ctl: Any, status: Any, launch: Any, clock: Any, ollama_ctl: Any, *,
                 tag: str, witnesses: Any, parent_env: Mapping[str, str], catcher_url: str,
                 canaries: Sequence[str] = (), popen: Callable[..., Any] = subprocess.Popen,
                 concurrency: int | None = None, hooks: Any = None, home_canaries: Any = None) -> None:
        if tag not in TAGS:
            raise SchedulerError(f"tag {tag!r} is not one of {TAGS}")
        if tag == "scored" and home_canaries is None:
            raise SchedulerError("a scored scheduler plants the home canaries in every unit's home (R-HOME-CANARY: "
                                 "without them the P0h boundary is not measured) - none were given")
        self.c, self.proxy_ctl, self.status, self.launch = contract, proxy_ctl, status, launch
        self.clock, self.ollama_ctl, self.tag, self.witnesses = clock, ollama_ctl, tag, witnesses
        self.parent_env, self.catcher_url, self.canaries = dict(parent_env), catcher_url, tuple(canaries)
        self.popen, self.concurrency, self.hooks = popen, concurrency, hooks
        self.home_canaries = home_canaries           # R-HOME-CANARY: a launch.Canaries, planted in every unit's home
        self._spawn_lock = threading.Lock()
        self.pid = os.getpid()                        # D3: every START names the scheduler's pid

    def spawn_child(self, build: Callable[[Any], LaunchSpec], *, role: str, stand: str, run: str, arm: str,
                    unit: str, dirs: Any = None, window: Any = None) -> tuple[Any, Any]:
        """(the child, its unit directories). Under the one spawn lock: the unit's fresh directories (or ``dirs``, a
        Claude Code unit's own, Q-47-6), the caller's spec written from them, the contract's environment, and
        launch.spawn - which records the spawn, refuses what the contract forbids, and starts the child."""
        if tree_kill_route(self.witnesses) is None:
            raise SchedulerError(f"{stand}/{run}/{arm}/{unit}: no way to kill a unit's process tree - neither the "
                                 f"native witness's job object (W1) nor psutil (R-LAUNCHER); nothing was spawned")
        with self._spawn_lock:
            d = dirs if dirs is not None else self.launch.make_unit_dirs(self.c, stand, run, arm, unit)
            planted = self._plant(d)                      # R-HOME-CANARY: before the child exists
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
                    record={"role": role, "stand": stand, "run": run, "arm": arm, "unit": unit,
                            "home_canaries": planted},
                    parent_env=self.parent_env, catcher_url=self.catcher_url, token_names=spec.token_names,
                    claude_names=spec.claude_names, env_exception=spec.env_exception,
                    argv_exception=spec.argv_exception, canaries=self.canaries, popen=self.popen,
                    witnesses=self.witnesses, requirement=spec.requirement,
                    unwitnessed_reason=spec.unwitnessed_reason, window=window, **kw)
            finally:
                if err is not None:
                    err.close()                  # the child holds its own handle
        return child, d

    def _plant(self, d: Any) -> dict | None:
        """R-HOME-CANARY: the home canaries in the unit's fake home - every arm alike; the record gets their file names
        and the sha256 of each value, never a value. None when the scheduler was given none."""
        if self.home_canaries is None:
            return None
        self.launch.plant_canaries(d, self.home_canaries)
        vals = self.home_canaries.values
        return {"files": sorted(HOME_CANARY_FILES.values()),
                "sha256": {k: hashlib.sha256(vals[k].encode("utf-8")).hexdigest() for k in sorted(HOME_CANARY_FILES)}}

    def write_turn(self, launcher: Any, *, stand: str, runs: Sequence[str], units: Sequence[str],
                   ops_for: Callable[[str, str], Sequence[Mapping]], ceilings: Mapping[str, float],
                   status_ids: Mapping[str, str]) -> dict:
        """One arm's write stage in a block (A5): {(run, unit): UnitRecord}."""
        return _write_turn(self, launcher, stand=stand, runs=runs, units=units, ops_for=ops_for, ceilings=ceilings,
                           status_ids=status_ids)

    def run_block(self, sp: "StandPlan", bp: "BlockPlan") -> dict:
        """One block (A6): see _run_block."""
        return _run_block(self, sp, bp)

    def run_stand(self, sp: "StandPlan", blocks: Sequence["BlockPlan"], *, judges: Sequence[Any] = (),
                  order: int) -> dict:
        """One stand (A7): see _run_stand."""
        return _run_stand(self, sp, blocks, judges=judges, order=order)

    def _await_gate(self) -> None:
        """No new unit starts while the incident gate refuses (§4.5, TB4.11b); asked again every GATE_POLL_S."""
        gate = getattr(self.hooks, "gate", None) if self.hooks is not None else None
        if gate is None:
            return
        sleep = getattr(self.clock, "sleep", None) or __import__("time").sleep
        while not gate.admits_new_unit():
            sleep(GATE_POLL_S)

    def _gpu_only(self, tag: str | None, *, embedders: Sequence[str] = ()) -> None:
        """§5.6: one resident Ollama model per stage - every other resident model unloaded (never woken: only what
        /api/ps names), an embedding-only one (the stand's D1 tag) through /api/embed (R-UNL: the generate endpoint is
        no unload for it), then /api/ps must name nothing but the tag."""
        if self.ollama_ctl is None:
            return
        keep = None if tag is None else _full_name(tag)
        emb = {_full_name(m) for m in embedders if m}
        for m in self.ollama_ctl.ps():
            if _full_name(m) != keep:
                self.ollama_ctl.unload(m, embedder=_full_name(m) in emb)
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
        """bye, then the child's OWN exit code (B-RC at the close). A bye that timed out or broke the protocol is the
        unit's ArmError - _unit_failed then kills the whole tree and names it (the ceiling, or a crash with our
        signal); a child that answered bye and does not exit is subprocess.TimeoutExpired, the ceiling. ArmClient.close()
        swallows a failed bye, kills the root alone and returns that kill's code as if the child had exited with it.
        A child that exited before its bye answer was read (ArmDied) is waited for: its code is its own."""
        B = _arm_base()
        try:
            self.arm.request("bye", timeout=timeout)
        except B.ArmDied:
            pass
        except B.ArmError:
            if self.arm.poisoned:
                raise
        try:
            self.child.process.stdin.close()
        except OSError:
            pass
        return self.child.process.wait(timeout)


class ChildArmLauncher:
    """An arm whose units are children speaking arms/base.py's protocol. ``argv_for(spec_path, stage=, stand=, run=,
    unit=)`` is the child's argv; ``spec_for(stage, stand=, run=, unit=, dirs=, write_dirs=)`` its spec, written as JSON
    into the unit's fake home - never its cwd - before the spawn; stderr goes to <home>/stderr.<stage>.log. Its
    environment: ``declared`` (the same in every unit) and ``declared_for(stage, stand=, run=, unit=, dirs=,
    write_dirs=)`` - the values that name the unit, a URL with /u/<run>.<unit> in it (Q3) - which may set a name
    ``declared`` sets only to the same value. Q-A4-6 (1): an arm's environment NAMES in a stage are those of its first
    unit in that stage, in every unit after it (the write and read stages may differ: the reader's URL); a unit that
    would differ is refused before its spawn - and launch.spawn records each spawn's names. ``argv_exception`` is launch.spawn's named argv exception (Q9:
    our arms run from the repository; a competitor never has one)."""

    def __init__(self, name: str, *, argv_for: Callable[..., Sequence[str]], spec_for: Callable[..., dict],
                 store_persistence: str = "disk", path_dirs: Sequence[str] = (), declared: Mapping[str, str] | None = None,
                 token_names: Sequence[str] = (), reads_point: bool = False,
                 declared_for: Callable[..., Mapping[str, str]] | None = None,
                 argv_exception: Mapping[int, str] | None = None) -> None:
        if store_persistence not in ("disk", "memory"):
            raise SchedulerError(f"store_persistence {store_persistence!r} is disk or memory (Q25(4))")
        self.name, self.argv_for, self.spec_for = name, argv_for, spec_for
        self.store_persistence, self.path_dirs = store_persistence, tuple(path_dirs)
        self.declared, self.token_names = dict(declared or {}), tuple(token_names)
        self.reads_point, self.declared_for = reads_point, declared_for
        self.argv_exception = dict(argv_exception) if argv_exception else None
        self.env_names: dict[str, frozenset] = {}    # Q-A4-6 (1): per stage, the first unit's names

    def environment(self, stage: str, *, stand: str, run: str, unit: str, dirs: Any, write_dirs: Any) -> dict:
        """The declared environment of one unit's child; SchedulerError when it would clash or change names."""
        env = dict(self.declared)
        if self.declared_for is not None:
            extra = dict(self.declared_for(stage, stand=stand, run=run, unit=unit, dirs=dirs, write_dirs=write_dirs))
            clash = sorted(k for k in set(extra) & set(env) if extra[k] != env[k])
            if clash:
                raise SchedulerError(f"{self.name}: declared_for sets {clash} to another value than declared already sets")
            env.update(extra)
        names = frozenset(env)
        first = self.env_names.setdefault(stage, names)
        if names != first:
            raise SchedulerError(f"{self.name}/{run}/{unit}: its {stage} environment names {sorted(names ^ first)} "
                                 f"differ from its first {stage} unit's (Q-A4-6 (1)); nothing was spawned")
        return env

    def open(self, stage: str, *, sched: "Scheduler", stand: str, run: str, unit: str, write_dirs: Any = None) -> UnitClient:
        def build(d: Any) -> LaunchSpec:                 # under the scheduler's spawn lock
            env = self.environment(stage, stand=stand, run=run, unit=unit, dirs=d, write_dirs=write_dirs)
            spec_path = Path(d.home) / f"spec.{stage}.json"
            spec_path.write_text(json.dumps(self.spec_for(stage, stand=stand, run=run, unit=unit, dirs=d,
                                                          write_dirs=write_dirs), sort_keys=True), encoding="utf-8")
            argv = self.argv_for(spec_path, stage=stage, stand=stand, run=run, unit=unit)
            return LaunchSpec(argv=tuple(argv), declared=env, path_dirs=self.path_dirs, token_names=self.token_names,
                              argv_exception=self.argv_exception, pipes=True,
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
    rc: int | None = None                  # the child's own exit code - never one our kill produced (B-RC)
    signal: str | None = None              # KILLED when the scheduler killed the unit's child (D2)
    active_s: float = 0.0
    aborted: str | None = None             # None | "ceiling" | "crash" (D2)
    error: str | None = None
    end_write_utc: str | None = None       # R9: when end_write returned - a write-port call after it is background
    #: B-WCTR: the write child's counters, asked right after end_write (a service request: no write op, and after the
    #: end_write stamp); a memory-store arm's question stage then counts on in the same process (cumulative).
    counters: Any = None
    client: Any = None                     # a memory-store arm's live client, kept for its read stage (Q25(4))
    dirs: Any = None


def _budget(B: Any, clock: Any, deadline: Callable[[], float]) -> Callable[[str], float]:
    """The unit's seconds left for its next request - or ArmTimeout, the ceiling, when none are: a request never gets a
    zero or negative timeout (a negative one is the queue's ValueError, which is no ceiling, B-BUDGET)."""
    def left_for(what: str) -> float:
        t = deadline() - clock.monotonic()
        if t <= 0:
            raise B.ArmTimeout(f"{what}: the ceiling passed")
        return t
    return left_for


def _open(launcher: Any, stage: str, who: str, **kw) -> Any:
    """The unit's child - or a SchedulerError: a spawn the launch contract refused, or the OS could not start, is no
    unit (nothing ran, so there is nothing to abort, B-RC)."""
    try:
        return launcher.open(stage, **kw)
    except Exception as e:  # noqa: BLE001 - every refusal is named, whatever raised it
        raise SchedulerError(f"{who}: the {stage} child was not started - {type(e).__name__}: {e}") from e


def _signal_name(n: int) -> str:
    """STATUS's signal= for a POSIX returncode -n: the signal's name where this platform knows it, else its number."""
    import signal as _signal  # noqa: PLC0415
    try:
        return _signal.Signals(n).name
    except ValueError:
        return str(n)


def _kill(client: Any, who: str) -> str:
    """Kill the unit's tree and reap its root: KILLED - or a SchedulerError when the root outlives the kill (no code
    and no signal can then be named, D2)."""
    client.kill_tree()
    if client.exit_code() is None:
        raise SchedulerError(f"{who}: the child outlived its kill - no exit code, so no UNIT-ABORT can name it (D2)")
    return KILLED


def _unit_failed(sched: "Scheduler", B: Any, client: Any, e: BaseException, *, status_id: str, unit: str,
                 who: str) -> tuple[str, int | None, str | None]:
    """(reason, rc, signal) of a unit stage that ended in ``e``, its UNIT-ABORT written (D2, B-RC). The ceiling - ours
    (ArmTimeout), or a child that did not exit after bye within it (TimeoutExpired) - kills the tree: reason=ceiling.
    A child whose stream closed (ArmDied) gets DIED_WAIT_S to exit by itself: its own code is rc= (a POSIX death by
    signal N, returncode -N, is signal=, B-SIGRC), and one still running is killed, signal=SIGKILL. Any other
    ArmError (a protocol break, a hello that is not this stage's, ok:false outside a write) is ours to kill:
    signal=SIGKILL. An exit code is never made up."""
    if isinstance(e, (B.ArmTimeout, subprocess.TimeoutExpired)):
        signal = _kill(client, who) if client is not None else None
        sched.status.unit_abort(status_id, unit, reason="ceiling")
        return "ceiling", None, signal
    if isinstance(e, B.ArmDied):
        rc = client.exit_code(timeout=DIED_WAIT_S)
        if rc is not None:
            client.kill_tree()                           # what it left behind; the child itself has exited
            if rc < 0:                                   # B-SIGRC: POSIX's -N is death by signal N, never an exit code
                sig = _signal_name(-rc)
                sched.status.unit_abort(status_id, unit, reason="crash", signal=sig)
                return "crash", None, sig
            sched.status.unit_abort(status_id, unit, reason="crash", rc=rc)
            return "crash", rc, None
    signal = _kill(client, who)
    sched.status.unit_abort(status_id, unit, reason="crash", signal=signal)
    return "crash", None, signal


def _kill_live(recs: Any) -> None:
    """Kill the live children a set of unit records still holds (a memory-store arm's writers, Q25(4))."""
    for r in recs:
        c = getattr(r, "client", None)
        if c is not None and c.child.process.poll() is None:
            c.kill_tree()
            c.exit_code()


def _write_unit(sched: "Scheduler", launcher: Any, *, stand: str, run: str, unit: str, ops: Sequence[Mapping],
                ceiling: float, status_id: str) -> UnitRecord:
    """One unit's write stage under its ceiling (D1: the unit's own active time); after end_write (and its stamp) the
    child's counters are asked for and kept (B-WCTR) - a child that does not answer is the unit's error. A ceiling
    kills the child's tree and writes UNIT-ABORT reason=ceiling; a child that dies or breaks the protocol is
    UNIT-ABORT reason=crash with its own exit code or the signal of our kill (D2, _unit_failed); a product's ok:false
    on one write is that operation's error, and the unit goes on. Anything else kills the child and goes on up
    (B-OPEN)."""
    B = _arm_base()
    rec = UnitRecord(arm=launcher.name, run=run, unit=unit)
    who = f"{stand}/{run}/{launcher.name}/{unit}"
    if launcher.store_persistence not in WRITE_HELLO_STAGES:
        raise SchedulerError(f"{who}: store_persistence {launcher.store_persistence!r} is disk or memory (Q25(4)); "
                             f"no child was opened")
    start = deadline = None
    left = lambda: deadline - sched.clock.monotonic()  # noqa: E731
    budget = _budget(B, sched.clock, lambda: deadline)
    client = None
    try:
        sched._await_gate()
        start = sched.clock.monotonic()          # B-GATE-D1: a wait at the incident gate is no unit's active time
        deadline = start + ceiling
        client = _open(launcher, "write", who, sched=sched, stand=stand, run=run, unit=unit)
        rec.spawn_id, rec.pid, rec.dirs = client.child.spawn_id, client.pid, getattr(client, "dirs", None)
        hello = client.request("hello", timeout=budget("hello"))
        stages = WRITE_HELLO_STAGES[launcher.store_persistence]         # checked above: disk or memory
        if (hello.get("protocol"), hello.get("arm")) != (B.PROTOCOL, launcher.name) or hello.get("stage") not in stages:
            raise B.ArmError(f"hello: {hello.get('protocol')}/{hello.get('arm')}/{hello.get('stage')} is not this arm's "
                             f"write stage ({' or '.join(stages)})")
        for op in ops:
            t0 = sched.clock.utc().isoformat()
            try:
                out = client.request("write", timeout=budget("write"), **op)
                rec.ops.append({"op_id": out.get("op_id"), "t0": t0, "t1": sched.clock.utc().isoformat(), "ok": True,
                                "error": None})
            except (B.ArmTimeout, B.ArmDied, B.ArmPoisoned):
                raise
            except B.ArmError as e:
                if client.arm.poisoned:
                    raise
                rec.ops.append({"op_id": (op.get("item") or {}).get("item_id"), "t0": t0,
                                "t1": sched.clock.utc().isoformat(), "ok": False, "error": str(e)})
        end = client.request("end_write", timeout=budget("end_write"))
        rec.footprint, rec.seal = end.get("footprint"), end.get("seal")
        rec.end_write_utc = sched.clock.utc().isoformat()
        rec.counters = client.request("counters", timeout=budget("counters"))     # B-WCTR: no reply is an ArmError
        if launcher.store_persistence == "memory":
            rec.client = client                                       # Q25(4): the store lives in this process
        else:
            rec.rc = client.close(timeout=max(1.0, left()))
    except (B.ArmError, subprocess.TimeoutExpired) as e:
        rec.error = str(e)
        rec.aborted, rec.rc, rec.signal = _unit_failed(sched, B, client, e, status_id=status_id, unit=unit, who=who)
    except BaseException:
        if client is not None:                                        # B-OPEN: no child outlives its failed unit
            client.kill_tree()
            client.exit_code()
        raise
    finally:
        rec.active_s = sched.clock.monotonic() - start if start is not None else 0.0
    return rec


def _write_turn(sched: "Scheduler", launcher: Any, *, stand: str, runs: Sequence[str], units: Sequence[str],
                ops_for: Callable[[str, str], Sequence[Mapping]], ceilings: Mapping[str, float],
                status_ids: Mapping[str, str]) -> dict:
    """One arm's write stage in a block: every (run, unit) at once - block size x runs, identical for every arm
    (§5.6), never more than the scheduler's concurrency - and the turn ends when all of them have."""
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
    pairs = [(r, u) for r in runs for u in units]
    width = sched.concurrency or len(pairs)
    # B-OPS: every unit's ops, ceiling and STATUS id before the first child exists - a plan that refuses one unit's
    # ops half-way through the submits left the units submitted before it running (a memory-store writer alive, and
    # never killed: its record was lost with the turn) and the turn waiting for their whole write stage.
    args = {(r, u): (ops_for(r, u), ceilings[u], status_ids[r]) for r, u in pairs}
    done, failed = {}, None
    with ThreadPoolExecutor(max_workers=width) as pool:
        futs = {(r, u): pool.submit(_write_unit, sched, launcher, stand=stand, run=r, unit=u, ops=a[0], ceiling=a[1],
                                    status_id=a[2]) for (r, u), a in args.items()}
        for k, f in futs.items():
            try:
                done[k] = f.result()
            except BaseException as e:  # noqa: BLE001 - raised below, once every unit of the turn has ended
                failed = failed or e
    if failed is not None:
        _kill_live(done.values())                   # B-OPEN: a memory-store writer never outlives its failed turn
        raise failed
    return done



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
    tokens and the frozen medians (the ceilings), the write ops per (arm, run, unit) and the reads per (arm, unit) -
    an arm's reads are its own (Q-12-3: its points) - the answer hook, the embed tag that stays resident, and the tree
    check's commit and dirty flag for the run records (Q2); record_extra(arm, run, units), when given, is the plan's
    own record of that arm-run in the block (the §5.1 truncation share, the ops' shas - the auditor's A5 condition),
    written into the run record under "plan"."""
    stand: str
    runs: tuple
    launchers: Mapping[str, Any]
    campaign_seed: int
    unit_tokens: Mapping[str, int]
    medians: Mapping[tuple, float]
    write_ops: Callable[[str, str, str], Sequence[Mapping]]
    read_plan: Callable[[str, str], Sequence[ReadReq]]            # (arm, unit) - Q-12-3
    answer: Callable[[str, str, str, ReadReq, dict], dict]
    embed_tag: str | None
    commit: str
    dirty: bool
    record_extra: Callable[[str, str, Sequence[str]], Mapping] | None = None


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
    out: dict = {"reads": [], "counters": None, "rc": None, "signal": None, "aborted": None, "pid": None,
                 "spawn_id": None}
    who = f"{sp.stand}/{run}/{launcher.name}/{unit}"
    start = deadline = None                              # set once the incident gate admits the unit (B-GATE-D1)
    paused = [0.0]                                       # W3's re-ask pauses: never the unit's active time (D1)
    in_flight: list = [None]                             # B-CUT: the read under way - {qid, point, t0} - or None
    left = lambda: deadline + paused[0] - sched.clock.monotonic()  # noqa: E731
    budget = _budget(B, sched.clock, lambda: deadline + paused[0])
    sleep = getattr(sched.clock, "sleep", None) or __import__("time").sleep

    def reask(attempt: Callable[[], Any], retry_on: tuple) -> tuple[Any, int, str | None]:
        """(the result, or None if still unrecovered; the re-asks made; the last error) - W3's order after the client's
        own retries: at most REASK_MAX re-asks, REASK_SPACING_S apart."""
        n, last = 0, None
        while True:
            try:
                return attempt(), n, None
            except retry_on as e:
                if isinstance(e, B.ArmError) and (client.arm.poisoned or isinstance(e, (B.ArmTimeout, B.ArmDied))):
                    raise
                last = str(e)
                if n >= REASK_MAX:
                    return None, n, last
                n += 1
                sleep(REASK_SPACING_S)
                paused[0] += REASK_SPACING_S

    client = wrec.client
    out["counters_include_write"] = client is not None       # B-WCTR: the memory-store arm's one process counts on
    try:
        sched._await_gate()
        start = sched.clock.monotonic()          # B-GATE-D1: a wait at the incident gate is no unit's active time
        deadline = start + max(0.0, ceiling - wrec.active_s)
        if client is None:
            client = _open(launcher, "read", who, sched=sched, stand=sp.stand, run=run, unit=unit,
                           write_dirs=wrec.dirs)
            out["spawn_id"] = client.child.spawn_id
            hello = client.request("hello", timeout=budget("hello"))
            if (hello.get("protocol"), hello.get("arm"), hello.get("stage")) != (B.PROTOCOL, launcher.name, "read"):
                raise B.ArmError(f"hello: not this arm's read stage ({hello.get('arm')}/{hello.get('stage')})")
        out["pid"] = client.pid
        for req in sp.read_plan(launcher.name, unit):
            budget("read")
            t0 = sched.clock.utc().isoformat()
            in_flight[0] = {"qid": req.qid, "point": req.point, "t0": t0}
            got, n_read, err = reask(lambda req=req: client.request("read", timeout=budget("read"),
                                                                    **read_kwargs(launcher, req)), (B.ArmError,))
            in_flight[0] = None
            t1 = sched.clock.utc().isoformat()
            row = {"qid": req.qid, "point": req.point, "t0": t0, "t1": t1, "reasks": n_read}
            if got is None:
                row.update(unrecovered=True, error=err)       # W3 step 3: P2 decides the drop, for every arm
            else:
                ans, n_ans, err = reask(lambda req=req, got=got: sp.answer(launcher.name, run, unit, req, got),
                                        (ReaskableError,))
                row["reasks"] = n_read + n_ans
                row.update(answer=ans) if ans is not None else row.update(unrecovered=True, error=err)
            out["reads"].append(row)
        out["counters"] = client.request("counters", timeout=budget("counters"))
        out["rc"] = client.close(timeout=max(1.0, left()))
    except (B.ArmError, subprocess.TimeoutExpired) as e:
        out["error"] = str(e)
        if in_flight[0] is not None:     # B-CUT: the read the unit ended in keeps its window, so a product call made
            cut = {**in_flight[0], "t1": sched.clock.utc().isoformat(), "cut": True, "error": str(e)}   # during it
            out["reads"].append(cut)     # is no R9 background write (read_windows are built from these rows)
        out["aborted"], out["rc"], out["signal"] = _unit_failed(sched, B, client, e, status_id=status_id, unit=unit,
                                                                who=who)
    except BaseException:
        if client is not None:                                        # B-OPEN: no child outlives its failed unit
            client.kill_tree()
            client.exit_code()
        raise
    finally:                             # D1 (B-PAUSE): the re-ask pauses are no unit's active time either
        out["active_s"] = sched.clock.monotonic() - start - paused[0] if start is not None else 0.0
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
    order, seed = arm_order(list(sp.launchers), campaign_seed=sp.campaign_seed, stand=stand, block=block)
    ceilings = {a: {u: ceiling_for(sched.tag, arm=a, stand=stand, unit_tokens=sp.unit_tokens[u], medians=sp.medians)
                    for u in bp.units} for a in order}            # B-OPEN: a unit without a ceiling opens nothing
    emb = _embedders(sp)
    events = {}
    if bp.barrier_read and sched.hooks is not None:
        events["barrier_read"] = sched.hooks.barrier_read(stand, block)
    sched._gpu_only(sp.embed_tag, embedders=emb)
    check_id = f"{stand}.{block}"
    sched.witnesses.begin_check(check_id)
    ids: dict = {}
    ended: set = set()
    wrecs: dict = {}
    opened = False
    try:
        sched.status.block_start(stand, block, units=list(bp.units), arm_order=order, seed=seed)
        opened = True
        for a in order:
            for r in sp.runs:
                ids[(a, r)] = sched.status.start(stand, block, r, a, pid=sched.pid, tag=sched.tag)
        sched.proxy_ctl.stage(f"{stand}/{block}", "write")
        for a in order:
            wrecs[a] = sched.write_turn(sp.launchers[a], stand=stand, runs=list(sp.runs), units=list(bp.units),
                                        ops_for=lambda r, u, a=a: sp.write_ops(a, r, u), ceilings=ceilings[a],
                                        status_ids={r: ids[(a, r)] for r in sp.runs})
        sched._gpu_only(sp.embed_tag, embedders=emb)     # the barrier: every write child exited or was killed
        sched.proxy_ctl.stage(f"{stand}/{block}", "questions")
        qrecs = {}
        for a in order:
            qrecs[a] = _question_turn(sched, sp.launchers[a], sp, wrecs=wrecs[a], ceilings=ceilings[a],
                                      status_ids={r: ids[(a, r)] for r in sp.runs})
        rcs = {(a, r): _end_rc(sp, bp, a, r, wrecs, qrecs) for a in order for r in sp.runs}   # every code, then ENDs
        art = _artifact()
        for a in order:
            for r in sp.runs:
                units = {u: {"write": _unit_payload(wrecs[a][(r, u)]), "questions": qrecs[a].get((r, u))}
                         for u in bp.units}
                wall = sum(wrecs[a][(r, u)].active_s + (qrecs[a].get((r, u)) or {}).get("active_s", 0.0)
                           for u in bp.units)
                rel = f"{stand}/_records/{block}/{r}.{a}.json"
                path = sched.c.runs_root / rel
                payload = {"stand": stand, "block": block, "run": r, "arm": a, "units": units}
                if sp.record_extra is not None:
                    payload["plan"] = dict(sp.record_extra(a, r, list(bp.units)))
                sha = art.run_record(path, ids[(a, r)], payload, commit=sp.commit, dirty=sp.dirty,
                                     now=sched.clock.utc)
                L._append_jsonl(sched.c.runs_root / "_launch" / "records.jsonl",
                                {"status_id": ids[(a, r)], "path": rel, "sha256": sha})
                sched.status.end(ids[(a, r)], rc=rcs[(a, r)], wall_s=wall, units=len(bp.units), out=rel)
                ended.add((a, r))
        sched.proxy_ctl.stage(None, None)                # a stray call is now loud in accounting, never a stage's
        sched.status.block_end(stand, block)
    except BaseException as e:
        _close_block(sched, e, stand=stand, block=block, ids=ids, ended=ended, wrecs=wrecs, opened=opened,
                     check_id=check_id)
        raise
    events["check"] = sched.witnesses.end_check(check_id)
    return {"order": order, "seed": seed, "write": wrecs, "questions": qrecs, **events}


def _embedders(sp: StandPlan) -> tuple:
    """The stand's embedding-only models (its D1 tag): unloaded through /api/embed (R-UNL)."""
    return (sp.embed_tag,) if sp.embed_tag else ()


def _end_rc(sp: StandPlan, bp: BlockPlan, a: str, r: str, wrecs: Mapping, qrecs: Mapping) -> int:
    """The arm-run's END rc: the first non-zero exit code of its units that were not aborted, else 0. Every such unit
    has its codes - a disk arm's write child and read child, a memory-store arm's one process (closed after its
    questions); a missing one is a SchedulerError, never a silent 0 (B-RC)."""
    memory = getattr(sp.launchers[a], "store_persistence", "disk") == "memory"
    rcs = []
    for u in bp.units:
        w, q = wrecs[a][(r, u)], qrecs[a].get((r, u)) or {}
        if w.aborted or q.get("aborted"):
            continue
        need = [("questions", q.get("rc"))] if memory else [("write", w.rc), ("questions", q.get("rc"))]
        missing = [s for s, x in need if x is None]
        if missing:
            raise SchedulerError(f"{sp.stand}/{bp.block}/{r}/{a}/{u}: no exit code for its {' and '.join(missing)} "
                                 f"stage - an END rc is never guessed (B-RC)")
        rcs += [x for _s, x in need]
    return next((x for x in rcs if x != 0), 0)


def _gate_halt(sched: "Scheduler") -> str | None:
    """Q2: the incident gate's halt read without raising (its halt_kind), None without a gate or a halt - a halt
    that came after the stand's last unit asked the gate still names itself on the normal STAND END."""
    gate = getattr(sched.hooks, "gate", None) if sched.hooks is not None else None
    f = getattr(gate, "halt_kind", None)
    return f() if callable(f) else None


def _halt_of(e: BaseException | None) -> str | None:
    """Q2: the gate's halt an error carries (run_v3_gate.GateHalted.halt: 401, 402, 403 or harness-error), looked for
    through its causes - None for any other error."""
    seen: set = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        h = getattr(e, "halt", None)
        if isinstance(h, str) and h:
            return h
        e = e.__cause__ or e.__context__
    return None


def _close_block(sched: "Scheduler", e: BaseException, *, stand: str, block: str, ids: Mapping, ended: set,
                 wrecs: Mapping, opened: bool, check_id: str) -> None:
    """B-OPEN: a block that failed after its check began closes what it opened - its live children killed, every START
    without an END closed by ABORT reason=harness-error (B-ABORT-REASON: on a halt, reason=<the halt's kind> - a 402 is
    the provider's, never our failure), the proxy stage reset, BLOCK END, the check ended - and the error goes on. A
    step that fails is named beside the error (a SchedulerError from it), never swallowed."""
    problems: list[str] = []

    def step(what: str, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except Exception as x:  # noqa: BLE001 - collected and named below
            problems.append(f"{what}: {type(x).__name__}: {x}")

    for recs in wrecs.values():
        step("killing the live writers", lambda recs=recs: _kill_live(recs.values()))
    reason = _halt_of(e) or "harness-error"          # B-ABORT-REASON
    for k, ident in ids.items():
        if k not in ended:
            step(f"ABORT {ident}", lambda ident=ident: sched.status.abort(ident, reason=reason))
    if sched.proxy_ctl is not None:
        step("the proxy stage reset", lambda: sched.proxy_ctl.stage(None, None))
    if opened:
        step("BLOCK END", lambda: sched.status.block_end(stand, block))
    step("the check's end", lambda: sched.witnesses.end_check(check_id))
    if problems:
        msg = f"{stand}/{block}: {type(e).__name__}: {e} - and closing the block failed: {'; '.join(problems)}"
        if isinstance(e, Exception):
            raise SchedulerError(msg) from e
        print(msg, file=sys.stderr)


def _unit_payload(w: UnitRecord) -> dict:
    """The write stage's part of the run record - with its error: why an aborted write stage ended is in no STATUS
    line (the question stage's record keeps its own)."""
    return {"spawn_id": w.spawn_id, "pid": w.pid, "ops": w.ops, "footprint": w.footprint, "seal": w.seal, "rc": w.rc,
            "signal": w.signal, "active_s": w.active_s, "aborted": w.aborted, "error": w.error,
            "end_write_utc": w.end_write_utc, "counters": w.counters}


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


# ── A7: the stand ──────────────────────────────────────────────────────────────────────────────────────────────

import re as _re  # noqa: E402

_RUN_ID = _re.compile(r"[A-Za-z0-9_-]+")


def _tree(sched: "Scheduler", stand: str, name: str) -> dict:
    """The tree check (Q10, D10) inside a witness check of its own - never inside a window (Q11). Q3 (the auditor): that
    check's record rides on the verdict as witness_check - a hit in the STAND START/END window is the stand's (rev1
    §2.6.9: an arm's orphan that outlived its kill, a write into the watched set), so the smoke reads it too."""
    cid = f"{stand}.{name}"
    sched.witnesses.begin_check(cid)
    try:
        v = dict(sched.hooks.tree_check())
    finally:
        check = sched.witnesses.end_check(cid)
    v["witness_check"] = check
    return v


def _run_stand(sched: "Scheduler", sp: StandPlan, blocks: Sequence[BlockPlan], *, judges: Sequence[Any],
               order: int) -> dict:
    """One stand (§5.6, D8, D10, D11): the plan's ids checked before any line; the preflight (balance); the tree check
    - a scored stand on a dirty tree is refused before STAND START, a smoke or debug one records it; the change-log read
    - a scored stand whose read gave no change log is refused there too, a smoke or debug one writes "unread" (B-CL) -
    and the model probe; STAND START; the blocks, every one after the first behind its barrier read; the end read, the
    probe and the tree check again; STAND END (written also when anything between the two failed, B-OPEN); then -
    scored stands only, and only when the end read gave a change log and the end tree is clean (B-CL, B-TE: else a
    SchedulerError carrying the result) - the judges, one model resident at a time (§5.6), after STAND END and before
    the next stand (D11)."""
    H = sched.hooks
    bad = [r for r in sp.runs if not _RUN_ID.fullmatch(r)]
    if bad:
        raise SchedulerError(f"run ids {bad} are not [A-Za-z0-9_-] (Q3: the proxy splits <run>.<unit> at the first dot)")
    if not blocks:
        raise SchedulerError(f"{sp.stand}: a stand with no block")
    out: dict = {"stand": sp.stand, "blocks": [], "judged": []}
    preflight = getattr(H, "preflight", None)
    if preflight is not None:
        out["preflight"] = preflight(sp.stand)
    elif sched.tag == "scored":                  # B-PREFLIGHT: the 402 halt and the 2x balance rule are never skipped
        raise SchedulerError(f"{sp.stand}: a scored stand's hooks have no balance preflight (§4.5); nothing was "
                             f"started")
    out["tree_start"] = _tree(sched, sp.stand, "tree-start")
    if not out["tree_start"].get("clean") and sched.tag == "scored":
        raise SchedulerError(f"{sp.stand}: the tree is not clean at STAND START - {out['tree_start'].get('problems')} "
                             f"(P0e); nothing was started")
    out["start_read"] = H.barrier_read(sp.stand, "start")
    cl_start = _changelog(out["start_read"])
    if cl_start is None and sched.tag == "scored":
        raise SchedulerError(f"{sp.stand}: the change-log read at STAND START gave no change log "
                             f"({(out['start_read'] or {}).get('changelog')!r}) - a scored stand never writes an unread "
                             f"one as a value (B-CL); nothing was started")
    model = H.model_probe()
    sched.status.stand(sp.stand, "START", model=model, changelog=cl_start or UNREAD, order=order)
    try:
        for i, bp in enumerate(blocks):
            run_bp = bp if i == 0 else BlockPlan(block=bp.block, units=tuple(bp.units), barrier_read=True)
            out["blocks"].append(_run_block(sched, sp, run_bp))
        out["end_read"] = H.barrier_read(sp.stand, "end")
        model_end = H.model_probe()
        out["tree_end"] = _tree(sched, sp.stand, "tree-end")
    except BaseException as e:
        _close_stand(sched, e, sp.stand)
        raise
    cl_end = _changelog(out["end_read"])
    try:
        sched.status.stand(sp.stand, "END", model=model_end, changelog=cl_end or UNREAD, halt=_gate_halt(sched))
    except BaseException as e:           # B-OPEN: a refused STAND END (a change log with a space, say) still closes it
        _close_stand(sched, e, sp.stand)
        raise
    if not out["tree_end"].get("clean"):
        out["dirty_end"] = list(out["tree_end"].get("problems") or ["the tree check did not say clean"])
    if sched.tag == "scored":
        stop = (["the change-log read at STAND END gave no change log (B-CL)"] if cl_end is None else []) \
            + ([f"the tree is not clean at STAND END - {out['dirty_end']} (P0e, B-TE)"] if "dirty_end" in out else [])
        if stop:
            err = SchedulerError(f"{sp.stand}: {'; '.join(stop)}; STAND END is written and no judge ran")
            err.partial = out
            raise err
        emb = _embedders(sp)
        try:
            for j in judges:
                sched._gpu_only(None, embedders=emb)     # nothing resident before a judge loads its model
                j.run()
                if sched.ollama_ctl is not None:
                    sched.ollama_ctl.unload(j.model, embedder=_full_name(j.model) in {_full_name(m) for m in emb})
                    left = sched.ollama_ctl.ps()
                    if left:
                        raise SchedulerError(f"after judge {j.name} the GPU still holds {left} (§5.6)")
                out["judged"].append(j.name)
        except Exception as err:         # after STAND END, as B-CL and B-TE: what the stand measured rides on it
            if isinstance(err, SchedulerError):
                if err.partial is None:
                    err.partial = out
                raise
            wrapped = SchedulerError(f"{sp.stand}: a judge failed after STAND END - {type(err).__name__}: {err}")
            wrapped.partial = out        # B-JPART: sched_ctl's ControlError (B-OLA's unload) or a judge's own error
            raise wrapped from err
    return out


def _changelog(read: Any) -> str | None:
    """The change log a barrier read gave, or None when it gave nothing a STAND line may carry as a value (B-CL)."""
    v = (read or {}).get("changelog")
    return None if v in _NOT_READ else v


def _close_stand(sched: "Scheduler", e: BaseException, stand: str) -> None:
    """B-OPEN at the stand: a stand that failed after STAND START writes STAND END - its model and change log unread,
    nothing probed on the way out, and halt=<kind> when the gate halted it (Q2, O-b2) - and the error goes on; a STAND
    END that cannot be written is named beside it."""
    try:
        sched.status.stand(stand, "END", model=UNREAD, changelog=UNREAD, halt=_halt_of(e))
    except Exception as x:  # noqa: BLE001 - named below
        msg = f"{stand}: {type(e).__name__}: {e} - and STAND END could not be written: {type(x).__name__}: {x}"
        if isinstance(e, Exception):
            raise SchedulerError(msg) from e
        print(msg, file=sys.stderr)
