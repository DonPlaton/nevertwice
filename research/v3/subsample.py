#!/usr/bin/env python3
"""PREREG-V3 TB5 (A7): the S1/S2 nested order and the S4-S7 unit orders - pure functions, committed with their seeds
BEFORE any list is built (the auditor's A7 rulings Q-T5-1..5). The lists themselves are built by the ls1 run, a child
under the launch contract that reads ids and question types only.

* S1/S2 (§3.4): strata are (question_type, abstention), abstention = a question id ending in "_abs"; the published
  counts (SSU 70, SSA 56, SSP 30, MS 133, KU 78, TR 133; 30 _abs) are checked against the file first - a difference
  stops the build. One random.Random(SEED); the strata sorted by (type, abstention); each stratum's sorted ids shuffled
  in turn. The strata are then interleaved by RULE. Every one of the N prefixes is checked to stay within floor/ceil of
  its proportional share per stratum; a violation stops the build - there is no choice between rules.
* S4, S5, S7: a seeded permutation of the sorted unit ids, one Random(SEED + i) per stand (S4 = 4, S5 = 5, S7 = 7).
* S6 (Q-T5-4): the FC rows by length tier ascending, no permutation - the floor (FC-SH 6K, 32K, 64K) and the blocks
  ({6K, 32K}, {64K}, {262K}) fix that order; FC-SH and FC-MH are separate lists (separate budgets).
* A list record holds ids only (never data, §3.1 T30), the seed, the rule, the python version and the sha256 of the
  canonical ids.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
import random
from collections import Counter
from fractions import Fraction
from typing import Iterable, Sequence

SEED = 20260926
UNIT_SEEDS = {"S4": SEED + 4, "S5": SEED + 5, "S7": SEED + 7}
S6_TIERS = ("6k", "32k", "64k", "262k")
#: §3.4's published counts, by the dataset's question_type names.
PUBLISHED = {"single-session-user": 70, "single-session-assistant": 56, "single-session-preference": 30,
             "multi-session": 133, "knowledge-update": 78, "temporal-reasoning": 133}
PUBLISHED_ABS = 30
RULE = ("sequential largest deficit: at position k take the stratum with the largest k*n_i/N - c_i; ties to the larger "
        "n_i, then the fixed stratum order (strata sorted by (question_type, abstention)). Declared erratum (A7 Q-T5-1): "
        "rev1 §3.4's largest remainder is infeasible for nested prefixes (the Alabama paradox). On the pinned strata "
        "every prefix is verified within 1 per stratum; the rule carries no general guarantee.")


class SubsampleRefused(ValueError):
    """A list the rules do not allow to be built; nothing was written."""


def stratum(qid: str, qtype: str) -> tuple[str, bool]:
    return str(qtype), str(qid).endswith("_abs")


def published_problems(items: Sequence[tuple[str, str]]) -> list[str]:
    """§3.4's counts against the file: per question type, and the abstention total."""
    by_type = Counter(str(t) for _, t in items)
    n_abs = sum(1 for q, _ in items if str(q).endswith("_abs"))
    out = [f"{t}: {by_type.get(t, 0)} in the file, {n} published" for t, n in PUBLISHED.items() if by_type.get(t, 0) != n]
    out += [f"{t}: a question type the published counts do not name" for t in sorted(by_type) if t not in PUBLISHED]
    if n_abs != PUBLISHED_ABS:
        out.append(f"_abs: {n_abs} in the file, {PUBLISHED_ABS} published")
    return out


def _sort_key(x):
    return (type(x).__name__, x)


def interleave(sizes: dict, order_of_strata: Sequence) -> list:
    """RULE over stratum sizes: the sequence of strata, position by position (exact integer arithmetic)."""
    n_total = sum(sizes.values())
    idx = {s: i for i, s in enumerate(order_of_strata)}
    c = {s: 0 for s in order_of_strata}
    seq = []
    for k in range(1, n_total + 1):
        s = max((s for s in order_of_strata if c[s] < sizes[s]),
                key=lambda s: (k * sizes[s] - c[s] * n_total, sizes[s], -idx[s]))
        c[s] += 1
        seq.append(s)
    return seq


def prefix_violations(seq: Sequence, sizes: dict) -> list[str]:
    """Every prefix k, every stratum: floor(k*n_i/N) <= c_i <= ceil(k*n_i/N)."""
    n_total = sum(sizes.values())
    c = {s: 0 for s in sizes}
    out = []
    for k, s in enumerate(seq, 1):
        c[s] += 1
        for t, n in sizes.items():
            q = Fraction(k * n, n_total)
            if not (math.floor(q) <= c[t] <= math.ceil(q)):
                out.append(f"prefix {k}: stratum {t} holds {c[t]}, outside [{math.floor(q)}, {math.ceil(q)}]")
    return out


def max_discrepancy(seq: Sequence, sizes: dict) -> Fraction:
    n_total = sum(sizes.values())
    c = {s: 0 for s in sizes}
    worst = Fraction(0)
    for k, s in enumerate(seq, 1):
        c[s] += 1
        worst = max(worst, max(abs(Fraction(k * n, n_total) - c[t]) for t, n in sizes.items()))
    return worst


def nested_order(items: Iterable[tuple[str, str]], *, seed: int = SEED, check_published: bool = True) -> list[str]:
    """(question_id, question_type) pairs -> the nested order of question ids (S1 = its first n_S, S2 = its first n_M,
    the smoke split = positions 481-500)."""
    items = [(str(q), str(t)) for q, t in items]
    if len({q for q, _ in items}) != len(items):
        raise SubsampleRefused("a question id repeats")
    if check_published:
        probs = published_problems(items)
        if probs:
            raise SubsampleRefused("the file differs from §3.4's published counts: " + "; ".join(probs))
    cells: dict = {}
    for q, t in items:
        cells.setdefault(stratum(q, t), []).append(q)
    strata = sorted(cells)
    rng = random.Random(seed)
    for s in strata:
        cells[s] = sorted(cells[s])
        rng.shuffle(cells[s])
    sizes = {s: len(cells[s]) for s in strata}
    seq = interleave(sizes, strata)
    bad = prefix_violations(seq, sizes)
    if bad:
        raise SubsampleRefused(f"RULE leaves floor/ceil on these strata - stop, no other rule is chosen: {bad[0]}")
    taken = {s: 0 for s in strata}
    out = []
    for s in seq:
        out.append(cells[s][taken[s]])
        taken[s] += 1
    return out


def unit_order(stand: str, ids: Iterable) -> list:
    """S4, S5, S7: the stand's own seeded permutation of its sorted unit ids."""
    if stand not in UNIT_SEEDS:
        raise SubsampleRefused(f"{stand} has no unit seed; S6 is ordered by tier, S1/S2 by the nested order")
    ids = list(ids)
    if len(set(ids)) != len(ids):
        raise SubsampleRefused(f"{stand}: a unit id repeats")
    out = sorted(ids, key=_sort_key)
    random.Random(UNIT_SEEDS[stand]).shuffle(out)
    return out


def s6_order(sources: Iterable[str], hop: str) -> list[str]:
    """S6 (Q-T5-4): the FC rows of one hop ("sh" or "mh") by length tier ascending; every tier present once."""
    if hop not in ("sh", "mh"):
        raise SubsampleRefused(f"hop {hop!r} is sh or mh")
    have = [s for s in sources if s.startswith(f"factconsolidation_{hop}_")]
    want = [f"factconsolidation_{hop}_{t}" for t in S6_TIERS]
    if sorted(have) != sorted(want):
        raise SubsampleRefused(f"FC-{hop.upper()} rows {sorted(have)} are not the four tiers {want}")
    return want


def list_record(stand: str, ids: Sequence, *, seed: int | None, rule: str) -> dict:
    """What research/v3/lists/<stand>.json holds: ids only, never data (§3.1 T30)."""
    canon = json.dumps(list(ids), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"stand": stand, "seed": seed, "rule": rule, "python": platform.python_version(), "n": len(ids),
            "ids": list(ids), "ids_sha256": hashlib.sha256(canon).hexdigest()}
