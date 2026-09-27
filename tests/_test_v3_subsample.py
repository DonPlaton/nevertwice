#!/usr/bin/env python3
"""PREREG-V3 TB5 (A7): research/v3/subsample.py - the nested order and the unit orders (the auditor's rulings Q-T5-1..5).

* the published counts are checked against the file first; a difference stops the build;
* the nested order is a permutation; each stratum's ids come in the order one Random(SEED) gives when the strata are
  shuffled in sorted order; every one of the 500 prefixes stays within floor/ceil per stratum on the pinned strata
  (the auditor's blind counts: _abs KU 6, MS 12, SSU 6, TR 6), max discrepancy 0.784;
* RULE carries no general guarantee: on {1,1,1,9,9} it leaves floor/ceil at prefix 14, and the build stops there;
* golden first ids (the same on Python 3.10 and 3.14 - random.shuffle's sequence is part of the rule);
* unit orders: one seed per stand (S4 = +4, S5 = +5, S7 = +7) over sorted ids; S6 by tier ascending, SH and MH apart;
* a list record holds ids only, the seed, the rule, the python version and the sha256 of the canonical ids.

    python tests/_test_v3_subsample.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import platform
import random
import sys
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import
_spec = importlib.util.spec_from_file_location("v3_subsample", ROOT / "research" / "v3" / "subsample.py")
S = importlib.util.module_from_spec(_spec)
sys.modules["v3_subsample"] = S
_spec.loader.exec_module(S)
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def refused(fn, words: str = "") -> bool:
    try:
        fn()
    except S.SubsampleRefused as e:
        return words in str(e)
    except Exception:  # noqa: BLE001
        return False
    return False


ABBR = {"single-session-user": "ssu", "single-session-assistant": "ssa", "single-session-preference": "ssp",
        "multi-session": "ms", "knowledge-update": "ku", "temporal-reasoning": "tr"}
ABS = {"knowledge-update": 6, "multi-session": 12, "single-session-user": 6, "temporal-reasoning": 6}   # the auditor's
ITEMS = [(f"{ABBR[t]}{i:03d}" + ("_abs" if i < ABS.get(t, 0) else ""), t) for t, n in S.PUBLISHED.items() for i in range(n)]

print("\n- the published counts -")
check("the pinned strata match §3.4's published counts", S.published_problems(ITEMS) == [])
short = [x for x in ITEMS if x[0] != "tr000_abs"]
check("a type count that differs is named", any("temporal-reasoning: 132" in p for p in S.published_problems(short)))
check("an _abs total that differs is named", any("_abs: 29" in p for p in S.published_problems(short)))
check("the build stops on a file that differs from the published counts",
      refused(lambda: S.nested_order(short), "published counts"))

print("\n- the nested order (RULE) -")
order = S.nested_order(ITEMS)
check("a permutation of all 500 question ids", sorted(order) == sorted(q for q, _ in ITEMS) and len(order) == 500)
cells: dict = {}
for q, t in ITEMS:
    cells.setdefault(S.stratum(q, t), []).append(q)
rng = random.Random(S.SEED)
want_cells = {}
for s in sorted(cells):
    ids = sorted(cells[s])
    rng.shuffle(ids)
    want_cells[s] = ids
got_cells: dict = {}
for q in order:
    t = dict(ITEMS)[q]
    got_cells.setdefault(S.stratum(q, t), []).append(q)
check("each stratum's ids come in the order one Random(SEED) gives, strata shuffled in sorted order",
      got_cells == want_cells)
sizes = {s: len(v) for s, v in cells.items()}
seq = [S.stratum(q, dict(ITEMS)[q]) for q in order]
check("every one of the 500 prefixes stays within floor/ceil per stratum on the pinned strata",
      S.prefix_violations(seq, sizes) == [], str(S.prefix_violations(seq, sizes)[:2]))
check("the largest discrepancy over all prefixes is 0.784 (the auditor's independent figure)",
      round(float(S.max_discrepancy(seq, sizes)), 3) == 0.784, str(float(S.max_discrepancy(seq, sizes))))
check("golden: the first ids of the nested order (the same on Python 3.10 and 3.14)",
      order[:6] == ["tr040", "ms123", "ku036", "ssu029", "ssa050", "tr095"], str(order[:6]))
check("golden: the sha256 of the whole order of 500 (every tie-break and every position; 3.10 and 3.14 agree)",
      hashlib.sha256(json.dumps(order, separators=(",", ":")).encode()).hexdigest()
      == "8469526eee606bf2a1d148a1ca1e6d529dbdde1a4bb1fac940c34d2fe9d7f018")
check("the order does not depend on the file's order of records (ids are sorted before the shuffle)",
      S.nested_order(list(reversed(ITEMS))) == order and S.nested_order(sorted(ITEMS, key=lambda x: x[0][::-1])) == order)
check("the order is deterministic, and another seed gives another order",
      S.nested_order(ITEMS) == order and S.nested_order(ITEMS, seed=S.SEED + 1) != order)
check("the smoke split (481-500) and the largest scored prefix (480) are disjoint by construction",
      not set(order[480:500]) & set(order[:480]))
check("a repeated question id is refused", refused(lambda: S.nested_order(ITEMS + ITEMS[:1]), "repeats"))

print("\n- RULE has no general guarantee: the build stops -")
sizes5 = {"a": 1, "b": 1, "c": 1, "d": 9, "e": 9}
seq5 = S.interleave(sizes5, sorted(sizes5))
check("on {1,1,1,9,9} RULE leaves floor/ceil at prefix 14 (stratum e)",
      any(v.startswith("prefix 14: stratum e") for v in S.prefix_violations(seq5, sizes5)), str(S.prefix_violations(seq5, sizes5)[:1]))
items5 = [(f"{s}{i}", s) for s, n in sizes5.items() for i in range(n)]
check("... and a build over such strata stops by name, with no other rule chosen",
      refused(lambda: S.nested_order(items5, check_published=False), "stop, no other rule is chosen"))

print("\n- unit orders -")
conv = [f"conv-{i}" for i in (26, 30, 41, 42, 43, 44, 47, 48, 49, 50)]
u4 = S.unit_order("S4", reversed(conv))
check("S4: its own seed (SEED+4) over the sorted ids - golden, whatever the input order",
      u4 == ["conv-30", "conv-49", "conv-26", "conv-50", "conv-43", "conv-41", "conv-44", "conv-42", "conv-47", "conv-48"],
      str(u4))
exp5 = sorted(range(20))
random.Random(S.SEED + 5).shuffle(exp5)
check("S5: Random(SEED+5) over the sorted ids; S7: Random(SEED+7)", S.unit_order("S5", range(20)) == exp5
      and S.unit_order("S7", range(36)) != S.unit_order("S5", range(36)))
check("S6 has no unit seed (it is ordered by tier)", refused(lambda: S.unit_order("S6", [1, 2]), "no unit seed"))
check("a repeated unit id is refused", refused(lambda: S.unit_order("S4", ["a", "a"]), "repeats"))
src = [f"factconsolidation_{h}_{t}" for h in ("mh", "sh") for t in ("262k", "6k", "64k", "32k")]
check("S6: FC-SH by length tier ascending, no permutation", S.s6_order(src, "sh") == [
    "factconsolidation_sh_6k", "factconsolidation_sh_32k", "factconsolidation_sh_64k", "factconsolidation_sh_262k"])
check("S6: FC-MH is its own list, same tier order", S.s6_order(src, "mh")[0] == "factconsolidation_mh_6k")
check("S6: a missing tier is refused", refused(lambda: S.s6_order(src[:-1], "sh"), "four tiers"))

print("\n- the list record -")
rec = S.list_record("S1", order, seed=S.SEED, rule=S.RULE)
canon = json.dumps(order, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
check("ids only, with the seed, the rule, the python version and the sha256 of the canonical ids",
      set(rec) == {"stand", "seed", "rule", "python", "n", "ids", "ids_sha256"} and rec["n"] == 500
      and rec["ids_sha256"] == hashlib.sha256(canon).hexdigest() and rec["python"] == platform.python_version())
check("the rule text names the erratum and its limit",
      "Alabama" in S.RULE and "no general guarantee" in S.RULE and "within 1 per stratum" in S.RULE)

print(f"\nv3 subsample: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
