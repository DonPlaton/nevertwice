#!/usr/bin/env python3
"""The feedback loop: what a guard has earned, and what can falsify it.

A memory that warns you is easy to build and impossible to trust. Everything rests on the loop
that decides which warnings keep the right to interrupt you, and that loop has exactly one
honest input - what happened after each fire. This suite holds that loop to four rules.

1. **Firing is not evidence.** A guard that fires a thousand times has proved only that its
   regex matches. `fired` must not reach precision, the intervals, or the lifecycle. A loop
   that counted it would confirm every guard it ever created, which is the failure mode the
   whole design exists to avoid.
2. **Falsification is not easier than confirmation.** Before D4, promotion deduped by session
   while demotion counted every call, so one frustrated session could retire a guard it could
   not have promoted - and a caller passing no session id could promote by repeating itself.
   Both directions count distinct sessions now, and an unattributed outcome moves neither.
3. **Burden and correctness are different questions.** An override says the guard went
   unheeded; a false positive says it was wrong. The old vocabulary collapsed them, so a guard
   that was right but annoying retired for being right.
4. **A guard demotes itself from recorded overrides** - GOAL D4's exit criterion, driven here
   through the real ledger rather than by calling the arithmetic directly.

Six mutations at the end break each guarantee on purpose and must turn this red.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
import api                      # noqa: E402
import guards as G              # noqa: E402
import outcomes as O            # noqa: E402
import why_fired as W           # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def fresh(pattern: str, *, project: str = "acme", pack: bool = False) -> dict:
    """A brand-new guard in the real ledger. Returns the stored dict."""
    ledger = G.load_guards()
    guard = G.make_guard(pattern, f"past mistake: {pattern}", project=project)
    assert guard is not None, pattern
    if pack:
        guard["pack"] = True
    G.register(ledger, guard)
    G.save_guards(ledger)
    return guard


def reload(guard_id: str) -> dict | None:
    return next((g for g in G.load_guards() if g["id"] == guard_id), None)


# --------------------------------------------------------------- vocabulary


def test_the_vocabulary_is_five_and_says_what_each_means() -> None:
    print("\n- five outcomes, and the two that are not the same thing -")
    check("five outcomes are declared", len(O.OUTCOMES) == 5, str(O.OUTCOMES))
    check("prevented_failure and accepted are evidence for",
          set(O.SUPPORT) == {"prevented_failure", "accepted"}, str(O.SUPPORT))
    check("overridden and false_positive are evidence against",
          set(O.AGAINST) == {"overridden", "false_positive"}, str(O.AGAINST))
    check("unknown counts for nothing", O.NEUTRAL == ("unknown",))

    check("the pre-D4 names still resolve",
          O.normalise("helped") == "accepted" and O.normalise("corroborated") == "accepted")
    check("an unrecognised outcome is rejected, not filed as unknown",
          O.normalise("sort-of-worked") is None,
          "a typo that silently became `unknown` is indistinguishable from a real one")


def test_precision_and_override_rate_answer_different_questions() -> None:
    print("\n- burden and correctness are counted apart -")
    guard = fresh(r"eval\(user")
    for outcome, sid in (("accepted", "s1"), ("accepted", "s2"), ("overridden", "s3")):
        api.guard_feedback(guard["id"], outcome, session_id=sid)
    summary = api.guard_outcomes(guard["id"])

    check("precision ignores the override",
          summary["precision"]["point"] == 1.0,
          f"precision {summary['precision']} - an override is unheeded, not wrong")
    check("the override still shows in the override rate",
          summary["override_rate"]["point"] == round(1 / 3, 3),
          str(summary["override_rate"]))
    check("precision names what it is computed over",
          "override is excluded" in summary["precision_basis"])
    check("the override rate names what it is computed over",
          "burden" in summary["override_rate_basis"])

    api.guard_feedback(guard["id"], "false_positive", session_id="s4")
    summary = api.guard_outcomes(guard["id"])
    check("a false positive does move precision", summary["precision"]["point"] == round(2 / 3, 3),
          str(summary["precision"]))


def test_the_interval_is_wilson_and_never_claims_certainty() -> None:
    print("\n- an interval, because these samples are tiny -")
    check("no trials gives an undefined precision, not zero",
          O.wilson(0, 0)["point"] is None and "undefined" in O.wilson(0, 0)["note"])
    perfect = O.wilson(3, 3)
    check("3 of 3 does not claim certainty", perfect["high"] <= 1.0 and perfect["low"] < 1.0,
          f"{perfect} - the normal approximation would give [1.0, 1.0], which is nonsense")
    check("the interval widens as n shrinks",
          O.wilson(3, 3)["high"] - O.wilson(3, 3)["low"]
          > O.wilson(30, 30)["high"] - O.wilson(30, 30)["low"])
    check("a half-and-half sample straddles 0.5",
          O.wilson(5, 10)["low"] < 0.5 < O.wilson(5, 10)["high"])
    check("the interval stays inside the unit interval",
          O.wilson(0, 5)["low"] >= 0.0 and O.wilson(5, 5)["high"] <= 1.0)


# ------------------------------------------------------- the two hard rules


def test_firing_is_never_evidence_of_success() -> None:
    print("\n- displaying a warning proves nothing -")
    guard = fresh(r"subprocess\.run\(.*shell=True")
    action = "subprocess.run(cmd, shell=True)"
    for _ in range(25):
        api.guards_check(action, project="acme")

    stored = reload(guard["id"])
    check("the fire counter did move", stored["fired"] >= 25, str(stored["fired"]))
    summary = api.guard_outcomes(guard["id"])
    check("precision is still undefined after 25 fires",
          summary["precision"]["point"] is None, str(summary["precision"]))
    check("no outcome was recorded by firing", summary["resolved"] == 0,
          str(summary["counts"]))
    check("no session was credited", summary["distinct_sessions"] == {"support": 0, "against": 0})
    check("the status did not move", stored["status"] == "advisory")
    check("the object says firing is not evidence",
          "not evidence" in summary["fired_is_not_evidence"])


def test_both_directions_need_distinct_sessions() -> None:
    print("\n- falsification is no easier to fake than confirmation -")

    one_session = fresh(r"pickle\.loads")
    for _ in range(G.M_RETIRE + 3):
        api.guard_feedback(one_session["id"], "false_positive", session_id="same-session")
    stored = reload(one_session["id"])
    check("one session repeating itself cannot retire a guard",
          stored["status"] == "advisory",
          f"status={stored['status']} after {G.M_RETIRE + 3} calls from one session")

    anonymous = fresh(r"verify=False")
    for _ in range(G.K_PROMOTE + 3):
        api.guard_feedback(anonymous["id"], "accepted")
    stored = reload(anonymous["id"])
    check("feedback with no session id cannot promote a guard",
          stored["status"] == "advisory",
          f"status={stored['status']} after {G.K_PROMOTE + 3} anonymous accepts")
    summary = api.guard_outcomes(anonymous["id"])
    check("anonymous feedback still counts toward the rates",
          summary["counts"]["accepted"] == G.K_PROMOTE + 3)

    earned = fresh(r"md5\(")
    for i in range(G.K_PROMOTE):
        api.guard_feedback(earned["id"], "accepted", session_id=f"session-{i}")
    check("K distinct supporting sessions do promote",
          reload(earned["id"])["status"] == "blocking")


def test_a_guard_demotes_itself_from_recorded_overrides() -> None:
    """GOAL D4's exit criterion, driven through the real ledger."""
    print("\n- a guard retires itself when nobody complies -")
    guard = fresh(r"os\.system\(")
    for i in range(G.K_PROMOTE):
        api.guard_feedback(guard["id"], "accepted", session_id=f"up-{i}")
    check("it earned blocking first", reload(guard["id"])["status"] == "blocking")

    for i in range(G.M_RETIRE):
        api.guard_feedback(guard["id"], "overridden", session_id=f"down-{i}",
                           reason="this call site is a fixed literal")
    demoted = reload(guard["id"])
    check("overrides alone demote it a rung", demoted["status"] == "advisory",
          f"status={demoted['status']}")
    check("the demotion says why",
          "overrode it" in (demoted.get("last_decision") or {}).get("because", ""),
          str(demoted.get("last_decision")))
    check("the override reasons are kept as learned exceptions",
          len(demoted["overrides"]) == G.M_RETIRE, str(demoted["overrides"]))

    for i in range(G.M_RETIRE):
        api.guard_feedback(guard["id"], "overridden", session_id=f"down2-{i}")
    check("a second round of overrides retires it",
          reload(guard["id"])["status"] == "retired",
          f"status={reload(guard['id'])['status']}")
    check("a retired guard stops firing",
          not api.guards_check("os.system('ls')", project="acme"))


def test_a_pack_guard_can_never_earn_the_right_to_block() -> None:
    print("\n- a shipped heuristic stays advisory however popular -")
    guard = fresh(r"chmod 777", pack=True)
    for i in range(G.K_PROMOTE + 5):
        api.guard_feedback(guard["id"], "prevented_failure", session_id=f"s{i}")
    check("it is still advisory", reload(guard["id"])["status"] == "advisory")
    verdict = O.verdict(reload(guard["id"]), promote_at=G.K_PROMOTE, retire_at=G.M_RETIRE)
    check("the verdict says why it is held", verdict["action"] == "hold"
          and "pinned advisory" in verdict["because"], str(verdict))
    check("but it can still be retired",
          O.verdict({**reload(guard["id"]), "outcomes":
                     {"counts": {k: 0 for k in O.OUTCOMES},
                      "sessions": {"support": [], "against": [f"x{i}" for i in range(G.M_RETIRE)]}}},
                    promote_at=G.K_PROMOTE, retire_at=G.M_RETIRE)["action"] == "demote",
          "a pack guard that nobody complies with must still be able to retire")


# ------------------------------------------------------------- the surfaces


def test_the_numbers_reach_every_surface() -> None:
    print("\n- what a guard earned is visible where it fires -")
    guard = fresh(r"yaml\.load\(")
    api.guard_feedback(guard["id"], "accepted", session_id="a")
    api.guard_feedback(guard["id"], "overridden", session_id="b")

    why = api.why_fired(guard["id"], "yaml.load(f)", project="acme")
    block = why.get("outcomes") or {}
    check("why_fired carries the outcome block", block.get("available") is True)
    check("it carries precision with an interval",
          "low" in block.get("precision", {}) and "high" in block.get("precision", {}))
    check("it carries the override rate", block.get("override_rate", {}).get("point") is not None)
    check("it carries the lifecycle verdict", "action" in (block.get("verdict") or {}))

    rendered = W.render(why)
    check("the shared formatter prints what it earned", "earned:" in rendered, rendered)
    check("the rendered line shows the interval", "[" in rendered.split("earned:")[1].split("\n")[0])

    import schemas
    check("the object still conforms to WhyFired", not schemas.conforms(why, "WhyFired"),
          "; ".join(schemas.conforms(why, "WhyFired")))

    html = api.dashboard()
    check("the dashboard column is what it earned, not how often it fired",
          "earned" in html and guard["id"] in html)

    import mcp_server
    text, is_error = mcp_server._tool_memory_guard_feedback(
        {"guard_id": guard["id"], "outcome": "accepted", "session_id": "c"})
    check("the MCP tool accepts the new vocabulary", not is_error, text[:140])
    check("the MCP tool reports what it earned", "precision" in text, text[:140])
    text, is_error = mcp_server._tool_memory_guard_feedback(
        {"guard_id": guard["id"], "outcome": "not-a-real-outcome"})
    check("the MCP tool rejects an outcome outside the vocabulary", is_error, text[:140])


def test_the_legacy_counters_still_agree() -> None:
    print("\n- the pre-D4 fields still describe the same guard -")
    guard = fresh(r"requests\.get\(.*verify=0")
    api.guard_feedback(guard["id"], "accepted", session_id="p")
    api.guard_feedback(guard["id"], "prevented_failure", session_id="q")
    api.guard_feedback(guard["id"], "false_positive", session_id="r")
    stored = reload(guard["id"])
    summary = api.guard_outcomes(guard["id"])

    check("helped mirrors the supporting outcomes", stored["helped"] == 2, str(stored["helped"]))
    check("corroborations mirrors distinct supporting sessions",
          stored["corroborations"] == summary["distinct_sessions"]["support"] == 2)
    check("seen_sessions mirrors the supporting session list",
          sorted(stored["seen_sessions"]) == ["p", "q"], str(stored["seen_sessions"]))
    check("confidence is the Wilson point over correctness only",
          stored["confidence"] == round(O.wilson(2, 3)["point"], 3),
          f"{stored['confidence']} vs {O.wilson(2, 3)}")


# --------------------------------------------------------------- mutations


def test_mutations_turn_it_red() -> None:
    print("\n- the checks fail when they should -")

    # 1. Counting fires as support would confirm every guard ever created.
    faked = dict(fresh(r"tempfile\.mktemp"))
    faked["fired"] = 99
    check("a fire count cannot manufacture precision",
          O.summary(faked)["precision"]["point"] is None,
          "summary() read `fired` as evidence")

    # 2. An anonymous outcome must not credit a session.
    anon = fresh(r"ssl\._create_unverified")
    O.record(anon, "accepted", session_id=None)
    O.record(anon, "false_positive", session_id="   ")
    check("a blank session id credits neither direction",
          O.support_sessions(anon) == 0 and O.against_sessions(anon) == 0)

    # 3. The same session repeating an outcome counts once.
    repeat = fresh(r"exec\(")
    for _ in range(5):
        O.record(repeat, "accepted", session_id="one")
    check("a repeated session counts once toward the threshold",
          O.support_sessions(repeat) == 1)
    check("but every call is still counted in the totals",
          O.block(repeat)["counts"]["accepted"] == 5)

    # 4. An unrecognised outcome records nothing at all.
    bogus = fresh(r"input\(")
    before = dict(O.block(bogus)["counts"])
    check("an unknown outcome name is rejected", O.record(bogus, "worked-ish") is None)
    check("and records nothing", O.block(bogus)["counts"] == before)

    # 5. `unknown` is recorded but is not evidence in either direction.
    unresolved = fresh(r"random\.seed")
    for i in range(9):
        O.record(unresolved, "unknown", session_id=f"u{i}")
    summary = O.summary(unresolved)
    check("unknown moves no threshold",
          summary["distinct_sessions"] == {"support": 0, "against": 0})
    check("unknown is excluded from the rates", summary["resolved"] == 0)
    check("but it is visible as unresolved", summary["unknown"] == 9)

    # 6. Demotion must clear the evidence it consumed, or the next override retires instantly.
    served = fresh(r"assert .* is None")
    for i in range(G.M_RETIRE):
        api.guard_feedback(served["id"], "overridden", session_id=f"d{i}")
    check("it demoted once", reload(served["id"])["status"] == "retired")
    check("the opposing sessions were cleared with the demotion",
          O.against_sessions(reload(served["id"])) == 0,
          "leaving them would retire a demoted guard on its very next override")

    # 7. The oscillation this suite actually caught: clearing only the *opposing* sessions
    #    left the corroborations that earned `blocking` standing, so the next override
    #    re-promoted the guard. It bounced advisory<->blocking forever and could never retire.
    oscillator = fresh(r"json\.loads\(open")
    for i in range(G.K_PROMOTE):
        api.guard_feedback(oscillator["id"], "accepted", session_id=f"a{i}")
    for i in range(G.M_RETIRE):
        api.guard_feedback(oscillator["id"], "overridden", session_id=f"b{i}")
    check("a demotion consumes the supporting evidence too",
          O.support_sessions(reload(oscillator["id"])) == 0,
          "the corroborations that earned the lost rung must be falsified with it")
    statuses = []
    for i in range(G.M_RETIRE):
        api.guard_feedback(oscillator["id"], "overridden", session_id=f"c{i}")
        statuses.append(reload(oscillator["id"])["status"])
    check("a demoted guard never re-promotes itself on the way down",
          "blocking" not in statuses, str(statuses))
    check("it reaches retired instead of oscillating",
          reload(oscillator["id"])["status"] == "retired", str(statuses))
    check("the demotions are counted, so the history stays legible",
          reload(oscillator["id"]).get("demotions") == 2,
          str(reload(oscillator["id"]).get("demotions")))


def main() -> int:
    for fn in (test_the_vocabulary_is_five_and_says_what_each_means,
               test_precision_and_override_rate_answer_different_questions,
               test_the_interval_is_wilson_and_never_claims_certainty,
               test_firing_is_never_evidence_of_success,
               test_both_directions_need_distinct_sessions,
               test_a_guard_demotes_itself_from_recorded_overrides,
               test_a_pack_guard_can_never_earn_the_right_to_block,
               test_the_numbers_reach_every_surface,
               test_the_legacy_counters_still_agree,
               test_mutations_turn_it_red):
        fn()
    print(f"\noutcomes: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
