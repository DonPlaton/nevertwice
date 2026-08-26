#!/usr/bin/env python3
"""RESEARCH - full-loop ablations (GOAL F4).

F4's exit criterion is that the paper can name **which** mechanism causes the improvement. A
system with seven moving parts and one aggregate number cannot name anything, so this removes
each part on its own and reports what the removal costs, measured on F1's corpus through F1's
matched-condition machinery.

The honest structure has two halves, and the second one matters more than the first:

**Mechanisms this surface exercises.** The anticipation path is where a past failure becomes a
warning, and five things happen inside it: IDF weighting, coverage-not-overlap, the coincidence
damper, recurrence weighting, and the outcome-feedback bar. Each is ablated alone, with the rest
held exactly as shipped.

**Mechanisms this surface does NOT exercise.** GOAL names seven ablations. Five of them - code
validation, temporal decay, graph hops, self-retirement, consolidation - do not participate in
the anticipation path at all: they act on the store and the recall path, which the F1 corpus
does not measure. Reporting them as "no effect" would be false. They are reported as **NOT
EXERCISED**, with the surface that would exercise each one, because "we did not measure it" and
"we measured it and it did nothing" are different sentences and only one of them is true here.

A removal that costs less than the interval the sample supports is reported as **not shown to
matter**, never as "does nothing" - the same rule F1, F2 and F3 apply.

    python research/ablations.py            # the table
    python research/ablations.py --save     # + research/ablations.json

Standard library only. No network, no GPU, no model.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox_guard                                    # noqa: E402
sandbox_guard.isolate("ablations")

sys.path.insert(0, str(ROOT / "nevertwice"))
sys.path.insert(0, str(ROOT / "research"))
import anticipate as A                                  # noqa: E402
import memory_hook as m                                 # noqa: E402
import matched_conditions as MC                         # noqa: E402

OUT = ROOT / "research" / "ablations.json"


# ── the scorer, with each mechanism switchable ──────────────────────────

def scorer(*, idf: bool = True, coverage: bool = True, damper: bool = True,
           recurrence: bool = True):
    """A `risk_score` with one mechanism removed and everything else exactly as shipped.

    Written as a re-implementation rather than by monkeypatching `anticipate`, so an ablation
    cannot leak into the live module and so the FULL variant can be checked against the real
    `risk_score` on every episode - which `tests/_test_ablations.py` does. If the full variant
    ever disagrees with the shipped function, every delta below is measured against a system
    nobody runs.
    """
    def score(traj_tokens: set, sig: dict, idf_table: dict) -> float:
        st = sig["tokens"]
        if not traj_tokens or not st:
            return 0.0
        shared = traj_tokens & st
        if not shared:
            return 0.0
        w = (lambda t: idf_table.get(t, 1.0)) if idf else (lambda t: 1.0)
        covered = sum(w(t) for t in shared)
        if coverage:
            # Share of the SIGNATURE's distinctive mass that the trajectory hit.
            base = covered / (sum(w(t) for t in st) or 1.0)
        else:
            # The ablation: raw overlap mass, unnormalised by how big the signature is. A long
            # note becomes easier to match than a short one, which is the thing coverage exists
            # to prevent.
            base = min(1.0, covered / 10.0)
        inter = len(shared)
        damp = (inter / (inter + 1.0)) if damper else 1.0
        rec = A._recur_weight(sig["recurrence"]) if recurrence else 1.0
        return max(0.0, min(1.0, base * damp * rec * 3.0))
    return score


def arm_for(score_fn):
    """Wrap a scorer into the (episode, sigs) -> (stem, score) shape F1's sweep expects."""
    cache: dict = {}

    def arm(episode: str, sigs: list) -> tuple:
        # Keyed by CONTENT, not by id(): a freed list's id can be reused, so an
        # id-keyed cache can hand back the IDF table of a different signature set.
        key = tuple(s["stem"] for s in sigs)
        if key not in cache:
            cache[key] = A.build_idf(sigs)
        idf_table = cache[key]
        traj = A._content_tokens(episode[:A.MAX_CHECK_CHARS])
        best, best_score = None, 0.0
        for sig in sigs:
            s = score_fn(traj, sig, idf_table)
            if s > best_score:
                best, best_score = sig["stem"], s
        return best, best_score
    return arm


#: Each entry removes exactly one thing. `full` is the shipped system and the reference.
VARIANTS = {
    "full": {"mechanism": "-", "kwargs": {}, "removes": "nothing - the shipped scorer, and the reference row"},
    "no_idf": {"mechanism": "IDF weighting", "kwargs": {"idf": False},
               "removes": "IDF weighting: every shared token counts the same, so a term that "
                          "tags half the vault carries as much evidence as a rare one"},
    "no_coverage": {"mechanism": "coverage normalisation", "kwargs": {"coverage": False},
                    "removes": "coverage normalisation: raw overlap mass instead of the share "
                               "of the signature's mass, so a long note is easier to match"},
    "no_damper": {"mechanism": "the coincidence damper", "kwargs": {"damper": False},
                  "removes": "the coincidence damper: a single shared token scores as "
                             "confidently as five"},
    "no_recurrence": {"mechanism": "recurrence weighting", "kwargs": {"recurrence": False},
                      "removes": "recurrence weighting: a failure that happened five times "
                                 "ranks exactly like one that happened once"},
}

#: The named ablations this surface cannot reach. Recorded so the paper cannot claim a null it
#: never measured.
NOT_EXERCISED = {
    "code_validation": {
        "why": "validation runs when a lesson is EXTRACTED and written, not when one is "
               "matched against a trajectory. Nothing in the anticipation path consults it.",
        "surface_that_would": "an extraction corpus with labelled valid/invalid lessons, "
                              "measuring what fraction of bad lessons reach the store",
    },
    "temporal_decay": {
        "why": "decay weights a note by age at RECALL time. F1's corpus has no time axis - "
               "every signature is equally old - so the mechanism is inert here by construction.",
        "surface_that_would": "a recall corpus with dated notes and dated queries, where the "
                              "right answer changes with when you ask",
    },
    "graph_hops": {
        "why": "hops expand a hit to its linked neighbours during retrieval. Anticipation "
               "scores each signature independently and never traverses a link.",
        "surface_that_would": "a retrieval corpus whose ground truth includes notes reachable "
                              "only through a link",
    },
    "self_retirement": {
        "why": "retirement removes a guard after sustained negative outcomes, over sessions. "
               "One pass over a fixed corpus contains no lifecycle at all.",
        "surface_that_would": "a longitudinal run with outcome reports across many sessions",
    },
    "consolidation": {
        "why": "consolidation merges near-duplicate notes in the store. The F1 corpus is a "
               "hand-written signature set with no duplicates to merge.",
        "surface_that_would": "a store grown from real transcripts, measured before and after "
                              "a consolidation pass",
    },
}


# ── outcome feedback, which needs a second kind of run ──────────────────

def outcome_feedback_effect(sigs: list) -> dict:
    """Outcome feedback is a mechanism over TIME, so it is measured differently.

    The other ablations change a score. This one changes a threshold, and only after a failure
    mode has cried wolf. So the measurement is: drive false alarms into one signature's state
    and check that its bar rises and it goes quiet, while a signature with a clean record is
    untouched. That is the mechanism's whole claim, and it is a claim about two things at once.
    """
    # The probe must come from THIS corpus. A first version reused a trajectory from
    # `_test_anticipate.py`'s synthetic signatures, which fires on nothing here - so the
    # mechanism reported itself unmeasurable when it was only being asked the wrong question.
    corpus = MC.load_corpus()
    traj = next((ep["text"] for ep in corpus["episodes"]
                 if (hits := A.anticipate(ep["text"], sigs=sigs, state={}, k=1))
                 and hits[0]["risk"] < 0.85), None)
    if traj is None:
        return {"measured": False,
                "why": "no episode fires below the adaptive bar's ceiling, so there is no "
                       "moderate signal to silence on this corpus"}
    state: dict = {}
    before = A.anticipate(traj, sigs=sigs, state=state, k=1)
    if not before:
        return {"measured": False,
                "why": "no signature fires on the probe trajectory, so there is nothing to "
                       "silence; the effect cannot be measured on this corpus"}
    stem, risk = before[0]["stem"], before[0]["risk"]

    fired_at = None
    for i in range(1, 30):
        A.feedback(stem, "false_alarm", state=state, persist=False)
        if not A.anticipate(traj, sigs=sigs, state=state, k=1):
            fired_at = i
            break

    # A different failure mode must NOT be silenced by this one's record.
    other = next((s["stem"] for s in sigs if s["stem"] != stem), None)
    other_bar = A._effective_tau(state, other) if other else None

    strong = A.anticipate("training loop device cpu throughput halved silently fell back",
                          sigs=sigs, state=state, k=1)
    return {
        "measured": True,
        "probe_risk": risk,
        "false_alarms_to_silence": fired_at,
        "silenced": fired_at is not None,
        "bar_after": A._effective_tau(state, stem),
        "other_mode_bar": other_bar,
        "other_mode_unaffected": other_bar == A.BASE_TAU,
        "strong_signal_still_breaks_through": bool(strong),
    }


# ── running the ablations ───────────────────────────────────────────────

def build(save: bool = False) -> dict:
    corpus = MC.load_corpus()
    sigs, episodes = corpus["signatures"], corpus["episodes"]
    positives = sum(1 for e in episodes if e["label"])

    rows = {}
    for name, spec in VARIANTS.items():
        arm = arm_for(scorer(**spec["kwargs"]))
        res = MC.sweep(arm, episodes, sigs)
        zero = MC.at_zero_false_alarms(res["curve"])
        rows[name] = {
            "mechanism": spec["mechanism"],
            "removes": spec["removes"],
            "zero_false_alarms": zero,
            "partial_auc": MC.partial_auc(res["curve"]),
            "recall_at_zero_fa": (zero or {}).get("recall"),
        }

    lo, hi = MC.wilson(round(0.5 * positives), positives)
    half = round((hi - lo) / 2, 4) if lo is not None else None
    reference = rows["full"]

    for name, row in rows.items():
        if name == "full":
            row["verdict"] = "reference"
            row["delta_recall"] = 0.0
            row["delta_auc"] = 0.0
            continue
        d_auc = round(reference["partial_auc"] - row["partial_auc"], 4)
        row["delta_auc"] = d_auc
        if row["zero_false_alarms"] is None:
            # Not "scored zero" - there is NO threshold at which this variant stops crying
            # wolf. Subtracting a missing value from the reference would report that as an
            # ordinary delta and lose the strongest result in the table.
            row["delta_recall"] = None
            row["verdict"] = "load-bearing (removal loses the zero-false-alarm point entirely)"
        else:
            d_recall = round((reference["recall_at_zero_fa"] or 0)
                             - (row["recall_at_zero_fa"] or 0), 4)
            row["delta_recall"] = d_recall
            row["verdict"] = _verdict(d_recall, half)

    payload = {
        "schema_version": 1,
        "generated_by": "python research/ablations.py --save",
        "surface": "F1's anticipation corpus, through F1's matched-condition sweep",
        "corpus": {"signatures": len(sigs), "episodes": len(episodes),
                   "positives": positives, "limitations": corpus["limitations"]},
        "recall_half_width_95": half,
        "variants": rows,
        "outcome_feedback": outcome_feedback_effect(sigs),
        "not_exercised": NOT_EXERCISED,
        "answer": _answer(rows, half),
    }
    if save:
        m.write_atomic(OUT, json.dumps(payload, ensure_ascii=False, indent=1))
    return payload


def _verdict(delta: float, half: float | None) -> str:
    """Removing it hurt, helped, or moved the number less than the sample can resolve."""
    if half is None:
        return "unknown"
    if abs(delta) <= half:
        return "not shown to matter"
    return "load-bearing" if delta > 0 else "harmful"


def _answer(rows: dict, half: float | None) -> list:
    """F4's exit criterion, answered from the rows.

    The criterion is that the paper can NAME which mechanism causes the improvement. So the
    answer is a list of names, and - just as importantly - the list of names it may not claim.
    """
    load_bearing = [r["mechanism"] for r in rows.values()
                    if r["verdict"].startswith("load-bearing")]
    harmful = [r["mechanism"] for r in rows.values() if r["verdict"] == "harmful"]
    unshown = [r["mechanism"] for r in rows.values()
               if r["verdict"] == "not shown to matter"]

    out = []
    if load_bearing:
        out.append("The paper MAY name these as load-bearing on this surface: " +
                   ", ".join(sorted(load_bearing)) +
                   ". Removing each costs more recall at zero false alarms than the sampling "
                   "interval allows for chance.")
    else:
        out.append(
            "The paper may name NO mechanism as load-bearing on this surface. Every single "
            "removal moved the number by less than the interval this corpus supports, which "
            "means the improvement cannot be attributed to any one part of the scorer here. "
            "That is F4's answer, and it is a negative one.")
    if harmful:
        out.append("Removing these IMPROVED the result: " + ", ".join(sorted(harmful)) +
                   ". A mechanism whose removal helps is a mechanism to delete, per the "
                   "project's own standing rule - or to justify on a surface where it earns "
                   "its place.")
    if unshown:
        out.append("Not shown to matter (delta inside the interval, which is NOT the same as "
                   "shown not to matter): " + ", ".join(sorted(unshown)) +
                   f". The interval here is plus or minus {half} on recall.")
    out.append(
        "Five of GOAL's seven named ablations are NOT EXERCISED by this surface at all - "
        "code validation, temporal decay, graph hops, self-retirement and consolidation act on "
        "the store and the recall path, which this corpus does not measure. They are listed "
        "with the surface that would reach each one. 'We did not measure it' and 'we measured "
        "it and it did nothing' are different sentences, and only the first is true of them.")
    return out


def render(p: dict) -> str:
    L = ["", "Full-loop ablations (GOAL F4) - one mechanism removed at a time", ""]
    c = p["corpus"]
    L.append(f"  surface  {p['surface']}")
    L.append(f"  corpus   {c['signatures']} signatures, {c['episodes']} episodes "
             f"({c['positives']} positive); interval +/-{p['recall_half_width_95']}")
    L.append("")
    L.append(f"  {'variant':16} {'rec@0fa':>8} {'d recall':>9} {'AUC':>7} {'d AUC':>8}  verdict")
    for name, r in p["variants"].items():
        L.append(f"  {name:16} {r['recall_at_zero_fa']!s:>8} {r['delta_recall']!s:>9} "
                 f"{r['partial_auc']!s:>7} {r['delta_auc']!s:>8}  {r['verdict']}")
    L.append("")
    of = p["outcome_feedback"]
    if of.get("measured"):
        L.append(f"  outcome feedback: {of['false_alarms_to_silence']} false alarm(s) silence a "
                 f"crying-wolf mode (bar {A.BASE_TAU} -> {of['bar_after']}); "
                 f"other modes unaffected: {of['other_mode_unaffected']}; "
                 f"strong signal still breaks through: {of['strong_signal_still_breaks_through']}")
    else:
        L.append(f"  outcome feedback: NOT MEASURED - {of['why']}")
    L.append("")
    L.append("  NOT EXERCISED by this surface:")
    for name, spec in p["not_exercised"].items():
        L.append(f"    {name}: {spec['why']}")
        L.append(f"      would need: {spec['surface_that_would']}")
    L.append("")
    L.append("  The answer F4 owes the paper:")
    for line in p["answer"]:
        L.append("    * " + line.replace(". ", ".\n      "))
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
