#!/usr/bin/env python3
"""RESEARCH - uncertainty, per family, corrected (GOAL F5).

F1 through F4 each published a number with a Wilson interval beside it. Those intervals are
**unpaired**, and the data is not: every arm sees the *same* episodes, so the right question is
not "how far apart are these two rates" but "on how many episodes did the two arms disagree, and
in which direction". Treating paired data as unpaired throws away the pairing and widens every
interval - conservative, but wrong, and wrong in a way that hides small real effects as readily
as it protects against false ones.

So this file redoes the comparisons properly, and adds the two things that decide whether an
aggregate deserves to be read at all:

* **Per-family results.** The corpus has fifteen failure families and thirty positive episodes.
  An aggregate over that is one number covering fifteen different questions. Every family's
  result is published, and the aggregate is recomputed with each family removed in turn -
  because F5's exit criterion is that **no aggregate hides a single easy family**, and the only
  way to know is to take each one out and look.
* **Multiple-comparison correction.** F1 through F4 made many comparisons. Testing enough
  hypotheses guarantees a winner; Holm-Bonferroni over the whole family is what stops the one
  that happened to clear a threshold from being reported as a finding.

Everything is exact and stdlib: McNemar's test by exact binomial rather than the chi-squared
approximation, which is wrong at these counts; bootstrap resampling of episodes with a **fixed,
recorded seed** so the intervals are reproducible; Cohen's h for effect size, because a
difference of proportions without an effect size invites reading 0.02 and 0.20 the same way.

    python research/uncertainty.py            # the report
    python research/uncertainty.py --save     # + research/uncertainty.json

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox_guard                                    # noqa: E402
sandbox_guard.isolate("uncertainty")

sys.path.insert(0, str(ROOT / "nevertwice"))
sys.path.insert(0, str(ROOT / "research"))
import cheap_baselines as CB                            # noqa: E402
import matched_conditions as MC                         # noqa: E402
import memory_hook as m                                 # noqa: E402

OUT = ROOT / "research" / "uncertainty.json"

#: Fixed and recorded. A bootstrap interval that moves between runs is not an interval.
SEED = 20260826
BOOTSTRAP = 10000
ALPHA = 0.05

#: The arms worth comparing the system against. `no_memory` is excluded: it never fires, so
#: every pair is concordant and McNemar has nothing to test.
COMPARISONS = ("lexical_recall", "linter_or_test", "curated_agents_md",
               "session_summary_extractive")


# ── exact statistics, no scipy ──────────────────────────────────────────

def binom_pmf(k: int, n: int, p: float = 0.5) -> float:
    return math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value on the discordant pairs.

    `b` = episodes where ours won and theirs lost; `c` = the reverse. Under the null the
    discordant pairs split 50/50, so this is an exact binomial test on b out of b+c. The
    chi-squared approximation is the usual shortcut and is not trustworthy at these counts -
    with a handful of discordant pairs it reports significance that an exact test does not.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(binom_pmf(i, n) for i in range(k + 1))
    return min(1.0, 2 * tail)


def cohens_h(p1: float, p2: float) -> float:
    """Effect size for two proportions. A difference without one invites reading 0.02 as 0.20."""
    return round(2 * math.asin(math.sqrt(max(0.0, min(1.0, p1))))
                 - 2 * math.asin(math.sqrt(max(0.0, min(1.0, p2)))), 4)


def holm(pvalues: dict, alpha: float = ALPHA) -> dict:
    """Holm-Bonferroni, step-down. Controls the family-wise error rate without assuming
    independence - which these comparisons certainly are not, since they share a corpus."""
    ordered = sorted(pvalues.items(), key=lambda kv: kv[1])
    out, rejected_so_far = {}, True
    m_total = len(ordered)
    for i, (name, p) in enumerate(ordered):
        threshold = alpha / (m_total - i)
        # Step-down: once one fails to reject, nothing after it may either.
        reject = rejected_so_far and p <= threshold
        rejected_so_far = reject
        out[name] = {"p": round(p, 6), "holm_threshold": round(threshold, 6),
                     "significant": reject}
    return out


def percentile(values: list, q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    idx = q * (len(s) - 1)
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return s[int(idx)]
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


# ── outcomes per episode, per arm ───────────────────────────────────────

def per_episode() -> dict:
    """For each arm, whether it got each positive episode right at its zero-false-alarm bar.

    Every arm is read at ITS OWN zero-false-alarm threshold - the operating point F2 compared
    them at - so the pairing is between two systems each configured the way it would actually
    be deployed, not between two arbitrary cutoffs.
    """
    corpus = MC.load_corpus()
    sigs, episodes = corpus["signatures"], corpus["episodes"]
    positives = [e for e in episodes if e["label"]]

    outcomes, thresholds = {}, {}
    for name, arm in CB.ARMS.items():
        res = MC.sweep(arm, episodes, sigs)
        zero = MC.at_zero_false_alarms(res["curve"])
        tau = zero["threshold"] if zero else None
        thresholds[name] = tau
        got = {}
        for ep in positives:
            if tau is None:
                got[ep["id"]] = False
                continue
            stem, score = arm(ep["text"], sigs)
            got[ep["id"]] = bool(stem == ep["label"] and score > 0.0 and score >= tau)
        outcomes[name] = got
    return {"outcomes": outcomes, "thresholds": thresholds,
            "episodes": [{"id": e["id"], "family": e["label"]} for e in positives]}


# ── the comparisons ─────────────────────────────────────────────────────

def paired_compare(ours: dict, theirs: dict, ids: list, rng: random.Random) -> dict:
    """One paired comparison: McNemar, a bootstrap CI on the difference, and an effect size."""
    b = sum(1 for i in ids if ours[i] and not theirs[i])
    c = sum(1 for i in ids if theirs[i] and not ours[i])
    concordant = len(ids) - b - c
    p = mcnemar_exact(b, c)

    diffs = []
    for _ in range(BOOTSTRAP):
        sample = [ids[rng.randrange(len(ids))] for _ in ids]
        ours_rate = sum(ours[i] for i in sample) / len(sample)
        theirs_rate = sum(theirs[i] for i in sample) / len(sample)
        diffs.append(ours_rate - theirs_rate)

    our_rate = sum(ours[i] for i in ids) / len(ids)
    their_rate = sum(theirs[i] for i in ids) / len(ids)
    return {
        "n": len(ids),
        "ours_correct": sum(ours[i] for i in ids),
        "theirs_correct": sum(theirs[i] for i in ids),
        "discordant_ours_only": b,
        "discordant_theirs_only": c,
        "concordant": concordant,
        "difference": round(our_rate - their_rate, 4),
        "bootstrap_ci95": [round(percentile(diffs, 0.025), 4),
                           round(percentile(diffs, 0.975), 4)],
        "cohens_h": cohens_h(our_rate, their_rate),
        "mcnemar_p": round(p, 6),
    }


def by_family(data: dict) -> dict:
    """Per-family results, published rather than summarised away."""
    families: dict = {}
    for ep in data["episodes"]:
        families.setdefault(ep["family"], []).append(ep["id"])
    out = {}
    for family, ids in sorted(families.items()):
        out[family] = {
            "n": len(ids),
            "episodes": ids,
            "by_arm": {name: sum(data["outcomes"][name][i] for i in ids)
                       for name in CB.ARMS},
        }
    return out


def leave_one_family_out(data: dict, rival: str, rng: random.Random) -> dict:
    """Recompute the headline with each family removed. F5's exit criterion, executed.

    If the aggregate is carried by one easy family, dropping that family moves it. Anything
    that only holds while a particular family is included is a finding about that family, and
    reporting it as an aggregate is how a single easy case becomes a systems claim.
    """
    families = {}
    for ep in data["episodes"]:
        families.setdefault(ep["family"], []).append(ep["id"])
    ours, theirs = data["outcomes"]["nevertwice"], data["outcomes"][rival]
    all_ids = [e["id"] for e in data["episodes"]]
    full = paired_compare(ours, theirs, all_ids, random.Random(SEED))

    out = {"full": full, "without": {}}
    for family, ids in sorted(families.items()):
        remaining = [i for i in all_ids if i not in set(ids)]
        if len(remaining) < 5:
            out["without"][family] = {"skipped": "fewer than 5 episodes would remain"}
            continue
        out["without"][family] = paired_compare(ours, theirs, remaining,
                                                random.Random(SEED))
    swings = [(f, r["difference"]) for f, r in out["without"].items() if "difference" in r]
    if swings:
        worst = max(swings, key=lambda kv: abs(kv[1] - full["difference"]))
        out["largest_swing"] = {"family": worst[0],
                                "difference_without_it": worst[1],
                                "difference_with_everything": full["difference"],
                                "swing": round(worst[1] - full["difference"], 4)}
    return out


def complementarity(data: dict, other: str) -> dict:
    """Do the memory arm and another arm fail on the SAME episodes, or on different ones?

    The per-family table made this worth computing: `linter_or_test` scores full marks on
    exactly the families the memory arm does worst on. Two arms with the same aggregate can be
    the same system twice or two different systems, and the discordant cells are what tell them
    apart. If the union beats both, the honest engineering conclusion is not "ours wins" but
    "these are complements, and a deployment should run both".
    """
    ids = [e["id"] for e in data["episodes"]]
    ours, theirs = data["outcomes"]["nevertwice"], data["outcomes"][other]
    union = {i: (ours[i] or theirs[i]) for i in ids}
    both = sum(1 for i in ids if ours[i] and theirs[i])
    n = len(ids)
    return {
        "arm": other,
        "n": n,
        "ours_only": sum(1 for i in ids if ours[i] and not theirs[i]),
        "theirs_only": sum(1 for i in ids if theirs[i] and not ours[i]),
        "both": both,
        "neither": sum(1 for i in ids if not ours[i] and not theirs[i]),
        "ours_rate": round(sum(ours[i] for i in ids) / n, 4),
        "theirs_rate": round(sum(theirs[i] for i in ids) / n, 4),
        "union_rate": round(sum(union[i] for i in ids) / n, 4),
        "union_vs_ours": paired_compare(union, ours, ids, random.Random(SEED)),
        "union_vs_theirs": paired_compare(union, theirs, ids, random.Random(SEED)),
    }


def family_winners(families: dict) -> dict:
    """Which arm wins each family. An aggregate over a mixture should show the mixture."""
    out = {}
    for family, r in families.items():
        scores = {a: r["by_arm"][a] / r["n"] for a in r["by_arm"]}
        best = max(scores.values())
        out[family] = {"n": r["n"], "best_rate": round(best, 4),
                       "won_by": sorted(a for a, s in scores.items()
                                        if abs(s - best) < 1e-9 and s > 0) or ["nobody"]}
    return out


def build(save: bool = False) -> dict:
    rng = random.Random(SEED)
    data = per_episode()
    ids = [e["id"] for e in data["episodes"]]
    ours = data["outcomes"]["nevertwice"]

    comparisons = {}
    for rival in COMPARISONS:
        comparisons[rival] = paired_compare(ours, data["outcomes"][rival], ids,
                                            random.Random(SEED))

    corrected = holm({k: v["mcnemar_p"] for k, v in comparisons.items()})
    for name, entry in corrected.items():
        comparisons[name]["holm"] = entry

    families = by_family(data)
    lofo = leave_one_family_out(data, "lexical_recall", rng)
    comps = {name: complementarity(data, name)
             for name in ("linter_or_test", "lexical_recall")}
    winners = family_winners(families)

    payload = {
        "schema_version": 1,
        "generated_by": "python research/uncertainty.py --save",
        "method": {
            "pairing": "every arm sees the same episodes, so comparisons are PAIRED. The "
                       "Wilson intervals published in F1-F4 are unpaired and therefore "
                       "conservative; these supersede them for any arm-vs-arm statement.",
            "test": "McNemar, exact binomial on the discordant pairs (not the chi-squared "
                    "approximation, which is untrustworthy at these counts)",
            "interval": f"percentile bootstrap over episodes, {BOOTSTRAP} resamples, "
                        f"seed {SEED}",
            "effect_size": "Cohen's h",
            "correction": f"Holm-Bonferroni over {len(COMPARISONS)} comparisons at "
                          f"alpha={ALPHA}",
            "operating_point": "each arm read at its own zero-false-alarm threshold",
        },
        "seed": SEED,
        "thresholds": data["thresholds"],
        "per_episode": {name: data["outcomes"][name] for name in CB.ARMS},
        "per_family": families,
        "comparisons": comparisons,
        "leave_one_family_out": lofo,
        "complementarity": comps,
        "family_winners": winners,
        "verdict": _verdict(comparisons, families, lofo, comps),
    }
    if save:
        m.write_atomic(OUT, json.dumps(payload, ensure_ascii=False, indent=1))
    return payload


def _verdict(comparisons: dict, families: dict, lofo: dict,
             comps: dict | None = None) -> list:
    """F5's exit criterion, decided by the numbers."""
    out = []
    significant = [n for n, c in comparisons.items() if c["holm"]["significant"]]
    if significant:
        out.append("Survives Holm-Bonferroni correction against: " + ", ".join(significant) +
                   ". Those are the only arm-vs-arm differences this corpus supports.")
    else:
        out.append(
            "NO comparison survives Holm-Bonferroni correction. After controlling for the "
            "number of hypotheses tested, this corpus does not establish a difference between "
            "the memory arm and ANY of the cheap alternatives it was compared against. Every "
            "F1-F4 statement of the form 'ours beats theirs' is unsupported at this sample "
            "size, and the correct wording everywhere is 'not distinguished'.")

    for name, c in comparisons.items():
        if c["discordant_ours_only"] == 0 and c["discordant_theirs_only"] > 0:
            out.append(
                f"SUBSET: against `{name}` the memory arm wins ZERO episodes the baseline "
                f"misses, while the baseline wins {c['discordant_theirs_only']}. That is "
                "stronger than 'not distinguished' - on this corpus its successes are a strict "
                "subset of the cheaper arm's, so there is no episode here that justifies the "
                "extra machinery.")

    zero_families = [f for f, r in families.items() if r["by_arm"]["nevertwice"] == 0]
    if zero_families:
        out.append(
            "Families the memory arm never gets right: " + ", ".join(sorted(zero_families)) +
            f" ({len(zero_families)} of {len(families)}). An aggregate over families this "
            "uneven is describing a mixture, not a capability.")

    swing = lofo.get("largest_swing")
    if swing:
        out.append(
            f"Leave-one-family-out: removing `{swing['family']}` moves the headline difference "
            f"from {swing['difference_with_everything']} to {swing['difference_without_it']} "
            f"(swing {swing['swing']}). THE EXIT CRITERION asks whether an aggregate hides a "
            "single easy family, and this is the number that answers it.")
        if abs(swing["swing"]) >= 0.05:
            out.append(
                "That swing is large relative to the effect itself, so the aggregate DOES "
                "lean on one family. The per-family table must be published beside any "
                "headline, and no headline may be quoted without it.")
        else:
            out.append(
                "No single family moves the headline much, so the aggregate is not carried by "
                "one easy case - which is the one reassuring thing in this report.")

    for name, comp in (comps or {}).items():
        gain = comp["union_rate"] - max(comp["ours_rate"], comp["theirs_rate"])
        if comp["ours_only"] and comp["theirs_only"] and gain > 0.05:
            out.append(
                f"COMPLEMENTARY with `{name}`: it gets {comp['theirs_only']} episodes the "
                f"memory arm misses, the memory arm gets {comp['ours_only']} it misses, and "
                f"the union reaches {comp['union_rate']} against {comp['ours_rate']} and "
                f"{comp['theirs_rate']} alone. Two arms with similar aggregates are not the "
                "same system twice. The engineering conclusion is not that one wins - it is "
                "that a deployment should run both, and that comparing them head-to-head was "
                "the wrong question.")

    out.append(
        "The intervals here supersede the Wilson intervals in F1-F4 for arm-vs-arm claims: "
        "those treat paired data as unpaired, which is conservative but wrong. The Wilson "
        "figures remain correct for a single arm's own rate.")
    out.append(
        "All of this rests on one author-written corpus of 30 positive episodes. No correction "
        "repairs that; it is the dominant term in every uncertainty on this page.")
    return out


def render(p: dict) -> str:
    L = ["", "Uncertainty (GOAL F5) - paired, bootstrapped, corrected", ""]
    mth = p["method"]
    L.append(f"  test      {mth['test']}")
    L.append(f"  interval  {mth['interval']}")
    L.append(f"  correction {mth['correction']}")
    L.append("")
    L.append(f"  {'vs':30} {'diff':>8} {'95% CI':>18} {'h':>7} {'p':>9} {'holm':>6}")
    for name, c in p["comparisons"].items():
        ci = f"[{c['bootstrap_ci95'][0]}, {c['bootstrap_ci95'][1]}]"
        L.append(f"  {name:30} {c['difference']!s:>8} {ci:>18} {c['cohens_h']!s:>7} "
                 f"{c['mcnemar_p']!s:>9} {'YES' if c['holm']['significant'] else 'no':>6}")
    L.append("")
    L.append(f"  {'family':26} {'n':>3}  " +
             " ".join(f"{a[:9]:>9}" for a in ("nevertwic", "lexical_r", "linter_or", "curated_a")))
    for family, r in p["per_family"].items():
        L.append(f"  {family:26} {r['n']:>3}  " +
                 " ".join(f"{r['by_arm'][a]:>9}" for a in
                          ("nevertwice", "lexical_recall", "linter_or_test",
                           "curated_agents_md")))
    L.append("")
    L.append("  Leave-one-family-out (vs lexical_recall):")
    swing = p["leave_one_family_out"].get("largest_swing")
    if swing:
        L.append(f"    largest swing: dropping {swing['family']} moves the difference "
                 f"{swing['difference_with_everything']} -> {swing['difference_without_it']}")
    L.append("")
    L.append("  Complementarity (do they fail on the same episodes?):")
    L.append(f"    {'arm':18} {'ours only':>10} {'theirs only':>12} {'both':>6} "
             f"{'neither':>8} {'union':>7}")
    for name, c in p.get("complementarity", {}).items():
        L.append(f"    {name:18} {c['ours_only']:>10} {c['theirs_only']:>12} {c['both']:>6} "
                 f"{c['neither']:>8} {c['union_rate']!s:>7}")
    L.append("")
    for line in p["verdict"]:
        L.append("  * " + line.replace(". ", ".\n    "))
    L.append("")
    return "\n".join(L)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", action="store_true", help=f"write {OUT.name}")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    payload = build(save=args.save)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(render(payload))
        if args.save:
            print(f"  saved -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
