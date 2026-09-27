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
"""
from __future__ import annotations

import hashlib
import importlib.util
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
STAGES = ("write", "questions")
TAGS = ("scored", "smoke", "debug")
CEILING_FLOOR_S, CEILING_FACTOR = 600.0, 3.0              # §5.6: at least 10 min; 3 x the pilot median
DEBUG_CEILING_S = 6 * 3600.0                             # Q26: the declared debug ceiling for the pilot and smoke
BUDGET_H = {"S6-SH": 24, "S6-MH": 24, "S5": 48, "S7": 24, "S4": 24, "S1": 72}   # §5.6, judge stages excluded
ARM_ORDER_DOMAIN = "nvt3-arm-order"


class SchedulerError(RuntimeError):
    """A plan or a value the preregistration does not allow; nothing was scheduled."""


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
