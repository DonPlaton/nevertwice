#!/usr/bin/env python3
"""PREREG-V3 §9.4 (lines 1796-1806): the fixed rules that select the smoke units - pure functions, committed before
any data exists (plan step A3.c), with the auditor's declared interpretation Q-A3-4 (2026-09-26):

* Every §9.4 token count is tiktoken cl100k_base (the one tokenizer rev 1 names, §5.2), over the raw text the dataset
  provides - never a length field the dataset carries. The counter is passed in (``count``), so these rules never
  bind a tokenizer themselves.
* S7's "character length" of a trajectory = len(json.dumps(<the record's trajectory value>, ensure_ascii=False,
  separators=(",", ":"))) on the pinned raw record, never on our renderer's output; the SWE median the same way.
* Ties are broken by dataset order: the first in the dataset wins.

The rules only select; they never read an answer or a score. S1's and S4's smoke identities come from A7's nested
order (Q-A3-5) and are selected by ``s1_tail``.
"""
from __future__ import annotations

import json
import statistics
from typing import Callable, Sequence

Count = Callable[[str], int]


def s1_tail(nested_order: Sequence) -> list:
    """S1: the 20 questions at the tail of the nested order, positions 481-500 (1-based)."""
    if len(nested_order) < 500:
        raise ValueError("the nested order must hold 500 questions")
    return list(nested_order[480:500])


def s5_cut(session_texts: Sequence[str], count: Count, limit: int = 128_000) -> int:
    """S5: how many leading sessions of BEAM 500K's first conversation to keep - every session that ENDS within the
    first ``limit`` tokens; a session that straddles the limit is dropped, and so is everything after it."""
    used = kept = 0
    for text in session_texts:
        used += count(text)
        if used > limit:
            break
        kept += 1
    if kept == 0:
        raise ValueError("no session ends within the limit")
    return kept


def s6_pick(row_contexts: Sequence[str], count: Count, target: int = 32_000) -> int:
    """S6: the index of the Accurate_Retrieval row whose context is closest to ``target`` tokens; ties -> dataset
    order (the earlier row)."""
    if not row_contexts:
        raise ValueError("no rows")
    best, best_d = None, None
    for i, ctx in enumerate(row_contexts):
        d = abs(count(ctx) - target)
        if best_d is None or d < best_d:            # strictly closer: an equal distance keeps the earlier row
            best, best_d = i, d
    return best


def trajectory_chars(trajectory) -> int:
    """S7's character length of one trajectory value (the auditor's Q-A3-4 form)."""
    return len(json.dumps(trajectory, ensure_ascii=False, separators=(",", ":")))


def s7_pick(non_swe_trajectories: Sequence, swe_trajectories: Sequence) -> int:
    """S7: the index of the non-SWE trajectory whose character length is closest to the SWE median; ties -> dataset
    order."""
    if not non_swe_trajectories or not swe_trajectories:
        raise ValueError("both domains must hold trajectories")
    median = statistics.median(trajectory_chars(t) for t in swe_trajectories)
    best, best_d = None, None
    for i, t in enumerate(non_swe_trajectories):
        d = abs(trajectory_chars(t) - median)
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best
