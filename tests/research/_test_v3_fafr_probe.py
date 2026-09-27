#!/usr/bin/env python3
"""PREREG-V3 TB4.12 (A6): research/v3/fafr_probe.py - the FA/FR probe (rev1 §8.5; Q22, F-1..F-3).

* the sources lie outside every scored list (M-FAFR-scored-positions);
* the templated items: 200 per judged prompt, drawn by the seed and stratified by class; the distractor is another
  source's gold; relative-date only for a dated gold; the same seed gives the same items; labels from the per-family
  table (F-2), which covers every class x family, with its contested cells named;
* freeze: the sha256 of the canonical items, changed by any field;
* path (A) by the answer (F-1): distractor context without the gold -> FA candidate, with it -> excluded; gold context
  EM 1 -> FR candidate, EM 0 -> excluded;
* the rule: pooled templated FA and FR, > 30 % FA -> no verdicts (30 % exactly still gives them); free-form items never
  enter it (M-FAFR-freeform-in-rule); items that are not the frozen set refuse (M-FAFR-unfrozen).

    python tests/research/_test_v3_fafr_probe.py
"""
from __future__ import annotations

import collections
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

_spec = importlib.util.spec_from_file_location("v3_fafr_probe", ROOT / "research" / "v3" / "fafr_probe.py")
FP = importlib.util.module_from_spec(_spec)
sys.modules["v3_fafr_probe"] = FP
_spec.loader.exec_module(FP)
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
    except FP.ProbeError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"not a ProbeError: {type(e).__name__}: {e}"


GOLDS = [f"entity {i}" if i % 3 else f"{1 + i % 28} May 2023" for i in range(120)]
SRC = [{"id": f"q{i}", "question": f"Question {i}?", "gold": g} for i, g in enumerate(GOLDS)]

print("- sources outside every scored list -")
check("disjoint sources pass", err(lambda: FP.check_sources(SRC, ["s1", "s2"])) == "no error")
check("M-FAFR-scored-positions: a source in a scored list refuses, naming it",
      "q7" in err(lambda: FP.check_sources(SRC, ["q7", "zz"])))

print("\n- the templated items -")
items = FP.templated_items("lme", SRC, seed=11)
cnt = collections.Counter(i["class"] for i in items)
check("200 items for the prompt, stratified evenly by class", len(items) == 200 and set(cnt.values()) == {25}, str(cnt))
check("the same seed gives the same items; another seed other ones",
      FP.templated_items("lme", SRC, seed=11) == items and FP.templated_items("lme", SRC, seed=12) != items)
own = {s["id"]: s["gold"] for s in SRC}
dp = [i for i in items if i["class"] == "distractor-plain"]
check("the distractor is another source's gold, never the item's own", all(i["answer"] != own[i["source"]] and
                                                                          i["answer"] in own.values() for i in dp))
small = [{"id": f"s{i}", "question": f"Q{i}?", "gold": f"answer {i}"} for i in range(3)]
own_hits = 0
for sd in range(40):                                             # 3 sources x 7 applicable classes: every item, 40 seeds
    every = FP.templated_items("lme", small, seed=sd, n=21)
    own_hits += sum(1 for i in every if i["class"] == "distractor-plain" and i["answer"] == f"answer {i['source'][1:]}")
check("over EVERY item of a small source set and 40 seeds, no distractor is the item's own gold", own_hits == 0,
      f"{own_hits} own-gold distractors")
rel = [i for i in items if i["class"] == "relative-date"]
check("relative-date only for a dated gold", rel and all("May 2023" in i["gold"] for i in rel)
      and all(i["answer"] == "last Tuesday" for i in rel))
check("each item's label is the family table's", all(i["label"] == FP.LABELS[(i["class"], "lme")] for i in items)
      and all(not i["free_form"] for i in items))
check("F-2': every error class is WRONG for every family, the gold classes right",
      set(FP.LABELS) == {(k.name, f) for k in FP.CLASSES for f in FP.FAMILIES}
      and all(FP.LABELS[(c, f)] is False for c in ("enumeration-with-gold", "old-plus-new", "hedge-gold", "relative-date",
                                                   "near-miss-entity", "distractor-plain") for f in FP.FAMILIES)
      and all(FP.LABELS[(c, f)] is True for c in ("gold-plain", "gold-verbose") for f in FP.FAMILIES))
check("F-2': an exclusion exists only where the family's pinned policy names the form correct (its quote in POLICY)",
      FP.EXCLUDED == {("relative-date", "locomo_j"): "relative time references"}
      and all(q_ in FP.POLICY[f] for (c, f), q_ in FP.EXCLUDED.items()))
lj = FP.templated_items("locomo_j", SRC, seed=11)
check("F-2': relative-date items of LoCoMo J are generated, flagged excluded, and still labelled wrong",
      all(i["excluded"] and i["label"] is False for i in lj if i["class"] == "relative-date")
      and not any(i["excluded"] for i in lj if i["class"] != "relative-date"))
check("a near miss is one deterministic change (a digit, else a letter)",
      FP._near_miss("Paris") == "Parit" and FP._near_miss("42 apples") == "52 apples")
check("too few applicable items refuse", "only" in err(lambda: FP.templated_items("lme", SRC[:10], seed=1)))
check("one source cannot give a distractor", "two source" in err(lambda: FP.templated_items("lme", SRC[:1], seed=1, n=1)))
check("an unknown family refuses", "unknown judge family" in err(lambda: FP.templated_items("gpt", SRC, seed=1)))

print("\n- freeze -")
sha = FP.freeze(items)
changed = [dict(items[0], answer=items[0]["answer"] + "!")] + items[1:]
check("the sha256 of the canonical items, stable, changed by any field", sha == FP.freeze(list(items)) and len(sha) == 64
      and FP.freeze(changed) != sha)

print("\n- path (A), by the answer (F-1) -")
for ctx, sa, gold, em, want in (("distractor", "Lyon", "Paris", 0.0, "fa-candidate"),
                                ("distractor", "It was Paris", "Paris", 0.0, "excluded"),
                                ("distractor", "Paris", "Paris", 1.0, "excluded"),
                                ("gold", "Paris", "Paris", 1.0, "fr-candidate"),
                                ("gold", "the French capital", "Paris", 0.0, "excluded")):
    check(f"{ctx} context, answer {sa!r}: {want}", FP.label_reader(ctx, sa, gold, em) == want)
check("an unknown context kind refuses", "unknown context" in err(lambda: FP.label_reader("both", "x", "y", 0)))

print("\n- the rule (§8.5) -")
wrong = [i for i, it in enumerate(items) if it["label"] is False]
right = [i for i, it in enumerate(items) if it["label"] is True]
acc = {i: True for i in right}
acc.update({i: (k < 0.3 * len(wrong)) for k, i in enumerate(wrong)})
r = FP.rule(items, acc, frozen_sha=sha)
rl = FP.rule(lj, {i: True for i in range(len(lj))}, frozen_sha=FP.freeze(lj))
check("F-2': excluded items leave the FA pool and are counted", rl["policy_excluded"] == 25
      and rl["wrong_items"] == len([i for i in lj if i["label"] is False and not i["excluded"]]), str(rl))
by_cls = collections.defaultdict(list)
for i, it in enumerate(items):
    by_cls[it["class"]].append(i)
acc_s = {i: True for i in by_cls["gold-plain"] + by_cls["gold-verbose"] + by_cls["distractor-plain"]
         + by_cls["near-miss-entity"][:20]}
rs = FP.rule(items, acc_s, frozen_sha=sha)
check("the sensitivity never decides: FA 30 % by F-2' gives verdicts though the literal-policy FA is 45 %",
      abs(rs["fa"] - 0.30) < 1e-12 and abs(rs["sensitivity"]["fa_literal_policy"] - 0.45) < 1e-12
      and rs["verdicts_allowed"] is True, str(rs))
check("the literal-policy FA is computed as a sensitivity and is not in the rule",
      rl["sensitivity"]["in_rule"] is False and 0 <= rl["sensitivity"]["fa_literal_policy"] <= 1
      and rl["verdicts_allowed"] is (rl["fa"] <= FP.FA_MAX))
check("pooled templated FA and FR; exactly 30 % FA still gives verdicts", abs(r["fa"] - sum(1 for k in range(len(wrong))
                                                                                         if k < 0.3 * len(wrong)) / len(wrong)) < 1e-12
      and r["fr"] == 0.0 and r["verdicts_allowed"] is (r["fa"] <= 0.30), str(r))
gap = {i: v for i, v in acc.items() if i not in right[:10]}
check("a missing verdict on a right item is not a rejection (FR counts only an explicit reject)",
      FP.rule(items, gap, frozen_sha=sha)["fr"] == 0.0)
acc2 = dict(acc)
acc2.update({i: True for i in wrong})
check("above 30 % FA the prompt gives no verdicts", not FP.rule(items, acc2, frozen_sha=sha)["verdicts_allowed"])
free = items + [{"family": "lme", "source": "q0", "class": "free", "question": "Q", "gold": "g", "answer": "x",
                 "label": False, "free_form": True}] * 50
fsha = FP.freeze(free)
r2 = FP.rule(free, {**acc, **{len(items) + k: True for k in range(50)}}, frozen_sha=fsha)
check("M-FAFR-freeform-in-rule: free-form items never enter the rule", r2["fa"] == r["fa"]
      and r2["free_form_items_excluded"] == 50 and r2["wrong_items"] == r["wrong_items"], str(r2))
check("M-FAFR-unfrozen: items that are not the frozen set refuse",
      "not the frozen set" in err(lambda: FP.rule(changed, acc, frozen_sha=sha)))
check("the probe's constants are rev1's (200 per prompt, 30 % FA)", FP.N_PER_PROMPT == 200 and FP.FA_MAX == 0.30)

print(f"\nv3 fafr probe: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
