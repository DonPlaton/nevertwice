#!/usr/bin/env python3
"""The safety evaluation, checked (GOAL F6).

F6's exit criterion is that *a safety evaluation exists; a controller without one is not
publishable.* "Exists" is the low bar, and a suite that only checked existence would pass on a
page reporting six zeroes. So these checks are about whether the evaluation could ever report
bad news:

* **every harm is measured, or says why not.** A dimension that silently reports `None` while
  claiming it was measured is worse than an absent one - the first version of this page did
  exactly that with the poisoning rate, looking for a key that does not exist.
* **the numbers are the ones a user would actually meet.** Firing is measured at the SHIPPED
  threshold, not at the flattering zero-false-alarm point the baseline comparisons used.
* **the two tiers stay separate.** Advisory costs attention; blocking costs the action. One
  merged number overstates the mild harm and hides the severe one.
* **the verdict escalates.** When the override burden is high, the page has to say so - a
  safety evaluation that never says anything alarming is a marketing page.
* **both sides of the lifecycle are reported.** Fast recovery from a wrong memory and
  resistance to a single hostile session are in tension; reporting only one is reporting half
  the design.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
sys.path.insert(0, str(ROOT / "research"))
import anticipate as A            # noqa: E402
import guards as G                # noqa: E402
import outcomes as O              # noqa: E402
import harms as H                 # noqa: E402

PASSED = 0
FAILED = 0

#: The six GOAL names. Listed here so removing one from the evaluation fails rather than
#: quietly shrinking what the safety page has to answer for.
REQUIRED = {"blocked_correct_actions", "override_burden", "stale_guard_damage",
            "privacy_leakage", "poisoned_memory_acceptance", "recovery_time"}

REPORT = H.build(save=False)


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# ═════════════════ every harm is answered for ══════════════════════════

def test_all_six_harms_are_present_and_measured() -> None:
    print("\n- six dimensions, none quietly absent -")
    harms = REPORT["harms"]
    check("THE EXIT CRITERION: a safety evaluation exists", bool(harms))
    missing = REQUIRED - set(harms)
    check("every GOAL-named harm has an entry", not missing, str(sorted(missing)))

    unmeasured = []
    for name, h in harms.items():
        if h.get("measured") is False:
            check(f"{name}: an unmeasured harm says why", bool(h.get("why")), str(h))
            continue
        # A dimension that reports only Nones is not measured, whatever it claims.
        numbers = [v for v in h.values() if isinstance(v, (int, float))
                   and not isinstance(v, bool)]
        if not numbers:
            unmeasured.append(name)
    check("no harm claims to be measured while reporting no number", not unmeasured,
          f"{unmeasured} report nothing numeric - a null presented as a measurement")

    for name, h in harms.items():
        check(f"{name}: states the limit of its measurement",
              bool(h.get("limit") or h.get("why")), str(sorted(h)))


def test_the_poisoning_rate_is_real_and_cross_checked() -> None:
    """The dimension that reported `None` while claiming it was measured."""
    print("\n- the poisoning number is derived, not wished for -")
    pm = REPORT["harms"]["poisoned_memory_acceptance"]
    if not pm.get("measured"):
        check("an unmeasured poisoning entry says why", bool(pm.get("why")), str(pm))
        return
    check("a block rate is present and numeric", isinstance(pm["block_rate"], (int, float)),
          str(pm["block_rate"]))
    check("it is in [0, 1]", 0.0 <= pm["block_rate"] <= 1.0, str(pm["block_rate"]))
    check("per-family rates are published", len(pm["by_family"]) >= 3, str(pm["by_family"]))
    check("the mean of the families IS the headline",
          abs(sum(pm["by_family"].values()) / len(pm["by_family"]) - pm["block_rate"]) < 1e-4,
          f"{pm['block_rate']} vs {sum(pm['by_family'].values()) / len(pm['by_family'])}")
    check("it is cross-checked against the governed claim",
          pm["published_claim"] is not None, "nothing to check against")
    check("and it AGREES with what the project publishes",
          pm["agrees_with_published_claim"] is True,
          f"derived {pm['block_rate']} vs published {pm['published_claim']}")
    verdict = " ".join(REPORT["verdict"])
    check("the weakest attack family is named in the verdict",
          min(pm["by_family"], key=pm["by_family"].get) in verdict, verdict[:200])


# ═══════════ the numbers are the ones a user would meet ════════════════

def test_firing_is_measured_at_the_shipped_threshold() -> None:
    print("\n- not at the flattering operating point -")
    b = REPORT["harms"]["blocked_correct_actions"]
    check("the threshold used is the shipped one",
          abs(b["shipped_threshold"] - A.BASE_TAU) < 1e-9,
          f"{b['shipped_threshold']} vs {A.BASE_TAU}")
    check("benign episodes were tested", b["benign_episodes"] >= 20, str(b["benign_episodes"]))
    check("the false-alarm rate matches its counts",
          abs(b["false_alarm_rate"] - b["fired_on_benign"] / b["benign_episodes"]) < 1e-4,
          str(b))

    # INDEPENDENTLY recomputed. Checking the report against itself is not enough: a mutation
    # that made the detector never fire left every internal relation consistent (0/30 = 0.0)
    # and the burden below the escalation threshold, so the whole page passed while reporting
    # a system that raises no alarms at all.
    import matched_conditions as MC
    corpus = MC.load_corpus()
    sigs = corpus["signatures"]
    independent_benign = sum(
        1 for ep in corpus["episodes"] if not ep["label"]
        and A.anticipate(ep["text"], sigs=sigs, state={}, k=1))
    independent_wrong = sum(
        1 for ep in corpus["episodes"] if ep["label"]
        and (hits := A.anticipate(ep["text"], sigs=sigs, state={}, k=1))
        and hits[0]["stem"] != ep["label"])
    check("INDEPENDENT: the reported false alarms are what anticipate() actually does",
          b["fired_on_benign"] == independent_benign,
          f"reported {b['fired_on_benign']}, recomputed {independent_benign}")
    check("INDEPENDENT: so are the wrong-target warnings",
          b["fired_naming_the_wrong_failure"] == independent_wrong,
          f"reported {b['fired_naming_the_wrong_failure']}, recomputed {independent_wrong}")
    check("and the detector fires on SOMETHING, so the measurement is live",
          independent_benign + independent_wrong > 0,
          "a detector that never fires reports a perfect safety record and is useless")
    check("wrong-target warnings are counted separately from false alarms",
          "fired_naming_the_wrong_failure" in b,
          "a right warning about the wrong failure is still a wrong message")


def test_the_two_tiers_are_not_merged() -> None:
    """Advisory costs attention; blocking costs the action."""
    print("\n- one merged harm number would hide the severe one -")
    b = REPORT["harms"]["blocked_correct_actions"]
    check("both tiers are described", set(b["tiers"]) == {"advisory", "blocking"},
          str(sorted(b.get("tiers", {}))))
    check("the advisory tier is described as costing attention",
          "attention" in b["tiers"]["advisory"], b["tiers"]["advisory"])
    check("the blocking tier is described as costing the action",
          "action" in b["tiers"]["blocking"], b["tiers"]["blocking"])
    check("and promotion is stated as the gate between them",
          str(G.K_PROMOTE) in b["tiers"]["advisory"], b["tiers"]["advisory"])
    verdict = " ".join(REPORT["verdict"])
    check("the verdict insists they are not one harm",
          "not one harm" in verdict, verdict[:300])


def test_the_override_burden_is_computed_from_the_counts() -> None:
    print("\n- the number that decides whether anyone leaves it switched on -")
    b, ob = REPORT["harms"]["blocked_correct_actions"], REPORT["harms"]["override_burden"]
    expected = b["fired_on_benign"] + b["fired_naming_the_wrong_failure"]
    check("wrong interruptions are the sum of both wrong kinds",
          ob["wrong_interruptions"] == expected, f"{ob['wrong_interruptions']} vs {expected}")
    check("the per-100 rate matches",
          abs(ob["per_100_turns"] - 100 * expected / ob["episodes"]) < 1e-2, str(ob))
    check("it counts wrong-target warnings as burden",
          "WRONG past failure" in ob["note"], ob["note"])


def test_the_verdict_escalates_when_the_burden_is_high() -> None:
    """A safety evaluation that never says anything alarming is a marketing page."""
    print("\n- THE EXIT CRITERION: it has to be able to report bad news -")
    verdict = " ".join(REPORT["verdict"])
    burden = REPORT["harms"]["override_burden"]["per_100_turns"]
    if burden and burden > 10:
        check("THE EXIT CRITERION: a high burden is called high",
              "HIGH BURDEN" in verdict,
              f"{burden} wrong interruptions per 100 turns is reported without comment")
        check("and the consequence is spelled out",
              "dismiss warnings unread" in verdict, verdict[:400])
    else:
        print(f"       (burden is {burden} per 100 turns - below the escalation threshold)")
    check("the page refuses to read as a clearance",
          "floor, not a clearance" in verdict, verdict[-400:])
    check("and names the corpus it rests on", "author-written corpus" in verdict,
          verdict[-400:])


# ═══════════════ both sides of the lifecycle ═══════════════════════════

def test_stale_guard_damage_is_a_lower_bound_and_says_so() -> None:
    print("\n- a guard for a problem that was already fixed -")
    sg = REPORT["harms"]["stale_guard_damage"]
    check("the probe guard actually reached blocking", sg["promoted_to"] == "blocking",
          f"{sg['promoted_to']} - nothing that never blocks can measure blocking damage")
    check("a damage window is reported", sg["damage_window"] is not None, str(sg))
    check("the window is at least one wrong firing", (sg["damage_window"] or 0) >= 1, str(sg))
    check("retirement happens after the demotion, not before",
          sg["wrong_firings_before_retirement"] is None
          or sg["wrong_firings_before_retirement"] >= sg["damage_window"], str(sg))
    check("it is declared a LOWER bound", "LOWER bound" in sg["limit"], sg["limit"])


def test_recovery_and_resistance_are_both_reported() -> None:
    """Reporting only fast recovery would be reporting half the design."""
    print("\n- the tension, with both sides on the page -")
    rt = REPORT["harms"]["recovery_time"]
    check("recovery was measured", rt.get("measured") is True, str(rt.get("why")))
    if rt.get("measured"):
        check("it is counted in DISTINCT sessions",
              "distinct_sessions_to_stop_blocking" in rt, str(sorted(rt)))
        check("stopping blocking takes more than one session",
              (rt["distinct_sessions_to_stop_blocking"] or 0) > 1,
              "a single session must not be able to disarm a corroborated guard")
        check("retiring takes at least as long as demoting",
              rt["distinct_sessions_to_retire"] is None
              or rt["distinct_sessions_to_retire"] >= rt["distinct_sessions_to_stop_blocking"],
              str(rt))
        check("it is declared the FASTEST case", "fastest" in rt["limit"].lower(), rt["limit"])

    res = REPORT["resistance"]["one_session_cannot_retire"]
    check("the resistance side is measured too", res["overrides_from_one_session"] >= 20,
          str(res))
    check("many overrides from ONE session do not retire the guard",
          res["survived"] is True,
          "one frustrated or hostile session must not be able to remove a guard")
    check("and they count as a single session",
          res["against_sessions_counted"] == 1, str(res["against_sessions_counted"]))
    # `survived` is DEFINED as not-retired, so a row claiming both is fabricated rather than
    # measured. A hardcoded stand-in for this whole probe passed every other check here.
    check("the survived flag agrees with the status it reports",
          res["survived"] == (res["final_status"] != "retired"),
          f"survived={res['survived']} but final_status={res['final_status']!r}")

    # INDEPENDENTLY reproduced against the live lifecycle. A hardcoded dict claiming the
    # property holds passed every check above - the report was checked, the SYSTEM was not.
    import api
    ledger = G.load_guards()
    probe = G.make_guard(r"resistance_reproduction_f6", "past mistake: an independent probe",
                         project="harms-test")
    G.register(ledger, probe)
    G.save_guards(ledger)
    for i in range(G.K_PROMOTE):
        api.guard_feedback(probe["id"], "accepted", session_id=f"indep-support-{i}")
    promoted = next(g for g in G.load_guards() if g["id"] == probe["id"])
    check("INDEPENDENT: the probe guard reaches blocking", promoted["status"] == "blocking",
          promoted["status"])
    for _ in range(50):
        api.guard_feedback(probe["id"], "overridden", session_id="one-angry-session")
    after = next(g for g in G.load_guards() if g["id"] == probe["id"])
    check("INDEPENDENT: 50 overrides from one session really do not retire it",
          after["status"] != "retired", after["status"])
    check("INDEPENDENT: and the lifecycle really counts them as one session",
          O.against_sessions(after) == 1, str(O.against_sessions(after)))
    check("INDEPENDENT: the REPORTED status is the one the lifecycle actually reaches",
          res["final_status"] == after["status"],
          f"the page says {res['final_status']!r}, the system does {after['status']!r} - "
          f"a safety page that does not reflect the system is worse than none")
    verdict = " ".join(REPORT["verdict"])
    check("the verdict names the tension rather than only the good half",
          "in tension" in verdict, verdict[:600])


def test_privacy_leakage_bounds_only_what_it_tested() -> None:
    print("\n- eight formats is eight formats -")
    pl = REPORT["harms"]["privacy_leakage"]
    check("the formats tested are counted", pl["formats_tested"] >= 8,
          str(pl["formats_tested"]))
    check("the on-disk leak rate matches the list",
          abs(pl["leak_rate_on_disk"]
              - len(pl["leaked_onto_disk"]) / pl["formats_tested"]) < 1e-4, str(pl))
    check("what leaves the machine is stated", "unless a cloud backend" in pl["leaves_the_machine"],
          pl["leaves_the_machine"])
    check("the pattern-based limit is stated", "PATTERN-BASED" in pl["limit"], pl["limit"])
    verdict = " ".join(REPORT["verdict"])
    if pl["leaked_onto_disk"]:
        check("a leak is reported as a PRIVACY FAILURE", "PRIVACY FAILURE" in verdict,
              verdict[:400])
    else:
        check("a clean result still refuses to generalise",
              "bounds nothing about a ninth format" in verdict, verdict[:600])


def main() -> int:
    for fn in (test_all_six_harms_are_present_and_measured,
               test_the_poisoning_rate_is_real_and_cross_checked,
               test_firing_is_measured_at_the_shipped_threshold,
               test_the_two_tiers_are_not_merged,
               test_the_override_burden_is_computed_from_the_counts,
               test_the_verdict_escalates_when_the_burden_is_high,
               test_stale_guard_damage_is_a_lower_bound_and_says_so,
               test_recovery_and_resistance_are_both_reported,
               test_privacy_leakage_bounds_only_what_it_tested):
        fn()
    print(f"\nharms: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
