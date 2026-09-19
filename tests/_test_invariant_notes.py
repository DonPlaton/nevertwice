#!/usr/bin/env python3
"""Contract assertions for the invariant note (mechanism 1).

Not a detector, so not a precision gate: `PREREGISTRATION.md` §7 judges this one by
contract, and every line below is one of those contracts. The two that matter most are
the ones the spec warned about rather than the ones that were easy to write:

* **zero context tokens when nothing fires** -- the token-economy core. A mechanism that
  costs something on every quiet diff is a mechanism the owner turns off.
* **at most one finding per diff** -- because
  `research/invariants_lab/BLAST_RADIUS_D5.md` measured the first candidate checker
  firing on 27.8% of commits that broke nothing. The delivery layer caps output *before*
  any checker is trusted to behave.

Hermetic: no vault, no network, no corpus. Every ledger here is constructed in memory or
in the test's own temp directory.

Run:  python tests/_test_invariant_notes.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

sys.path.insert(0, str(ROOT / "research" / "invariants_lab"))
import invariant_notes as I  # noqa: E402
import preconfigured as P  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _note(**kw):
    kw.setdefault("checker", "demo")
    kw.setdefault("message", "the loader must stay lazy")
    return I.make_invariant(**kw)


def _fires(_before, _after, note):
    return [I.Finding(note["id"], note["message"], subject=note.get("subject", ""),
                      rank=note.get("_rank", 0.0))]


def _silent(_before, _after, _note):
    return []


def _explodes(_before, _after, _note):
    raise RuntimeError("a checker with a bug in it")


# ---------------------------------------------------------------------------
# the note
# ---------------------------------------------------------------------------


def test_a_note_is_well_formed_or_it_is_not_a_note() -> None:
    print("\n- a note is well formed, or it is not made at all -")
    n = _note(subject="lazy-import", project="nevertwice")
    check("it carries its kind", n["kind"] == "invariant")
    check("it starts advisory, never blocking", n["status"] == "advisory")
    check("it records provenance", n["provenance"] == "declared")
    check("its counters start at zero",
          (n["fired"], n["helped"], n["false_positives"]) == (0, 0, 0))
    check("no checker, no note", I.make_invariant("", "x") is None)
    check("no message, no note", I.make_invariant("demo", "   ") is None)


def test_the_id_is_a_function_of_what_the_note_is_about() -> None:
    print("\n- the same claim twice is one note -")
    a = _note(subject="lazy-import")
    b = _note(subject="lazy-import")
    c = _note(subject="something-else")
    d = _note(subject="lazy-import", project="other")
    check("the same claim gets the same id", a["id"] == b["id"])
    check("a different subject gets a different id", a["id"] != c["id"])
    check("a different scope gets a different id", a["id"] != d["id"])
    ledger: list[dict] = []
    check("registering is idempotent",
          I.register(ledger, a) and not I.register(ledger, b) and len(ledger) == 1)


def test_a_ledger_holds_scars_and_invariants_together() -> None:
    print("\n- one ledger, two kinds, and they do not contaminate each other -")
    ledger = [{"id": "g-1", "pattern": "x", "status": "advisory"}]
    I.register(ledger, _note(subject="a"))
    check("both records survive", len(ledger) == 2)
    check("only the invariant is returned as one", len(I.invariants(ledger)) == 1)
    check("the scar is untouched", ledger[0]["id"] == "g-1")


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


def test_storage_round_trips_and_never_writes_where_it_was_not_asked() -> None:
    print("\n- storage: the caller names the file, always -")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nested" / "invariants.json"
        ledger = [_note(subject="a"), _note(subject="b")]
        I.save(path, ledger)
        check("it creates the directory it was given", path.exists())
        check("it round-trips", [n["id"] for n in I.load(path)] ==
              [n["id"] for n in ledger])
        check("no temp file is left behind",
              not list(path.parent.glob("*.tmp")), str(list(path.parent.glob("*"))))


def test_an_unreadable_ledger_is_empty_rather_than_fatal() -> None:
    print("\n- a corrupt ledger does not take the commit with it -")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "invariants.json"
        path.write_text("{not json", encoding="utf-8")
        check("corrupt reads as empty", I.load(path) == [])
        path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        check("wrong shape reads as empty", I.load(path) == [])
        check("missing reads as empty", I.load(Path(tmp) / "absent.json") == [])


# ---------------------------------------------------------------------------
# the hot path -- the contract that decides whether anyone keeps it switched on
# ---------------------------------------------------------------------------


def test_nothing_fires_means_nothing_costs() -> None:
    print("\n- zero context tokens until it fires -")
    diff = {"a.py": "def f(): pass\n"}
    check("an empty ledger returns nothing",
          I.check_diff(diff, diff, ledger=[], registry={"demo": _fires}) == [])
    ledger = [_note(subject="a")]
    check("a silent checker returns nothing",
          I.check_diff(diff, diff, ledger=ledger, registry={"demo": _silent}) == [])
    check("and renders to the empty string, not a heading",
          I.render([]) == "")
    check("an unregistered checker cannot fire",
          I.check_diff(diff, diff, ledger=ledger, registry={}) == [])


def test_at_most_one_finding_reaches_context() -> None:
    print("\n- at most one finding per diff, however many fire -")
    ledger = [_note(subject=s) for s in ("a", "b", "c", "d")]
    for i, n in enumerate(ledger):
        n["_rank"] = float(i)
    out = I.check_diff({}, {}, ledger=ledger, registry={"demo": _fires})
    check("four fired, one is delivered", len(out) == 1, str(len(out)))
    check("and it is the highest ranked", out[0].subject == "d", out[0].subject)
    check("the cap is the module's, not the caller's whim",
          I.MAX_FINDINGS_PER_DIFF == 1)


def test_a_retired_invariant_never_fires() -> None:
    print("\n- retirement is retirement -")
    n = _note(subject="a")
    n["status"] = "retired"
    check("retired is silent",
          I.check_diff({}, {}, ledger=[n], registry={"demo": _fires}) == [])
    check("and is not counted as live", I.live([n]) == [])


def test_a_checker_that_crashes_does_not_break_the_commit() -> None:
    print("\n- a broken checker is not a broken commit -")
    ledger = [_note(subject="boom"), _note(subject="fine")]
    out = I.check_diff({}, {}, ledger=ledger,
                       registry={"demo": lambda b, a, n:
                                 _explodes(b, a, n) if n["subject"] == "boom"
                                 else _fires(b, a, n)})
    check("the surviving checker still reports", len(out) == 1)
    check("and it is the one that did not crash", out[0].subject == "fine")


def test_scope_is_honoured_before_any_checker_runs() -> None:
    print("\n- an out-of-scope invariant costs nothing to skip -")
    ran: list[str] = []

    def counting(before, after, note):
        ran.append(note["id"])
        return _fires(before, after, note)

    ledger = [_note(subject="a", project="other"),
              _note(subject="b", path_glob="src/*.py")]
    out = I.check_diff({"docs/x.md": ""}, {"docs/x.md": ""}, ledger=ledger,
                       registry={"demo": counting}, project="nevertwice")
    check("neither fired", out == [])
    check("and neither checker was even called", ran == [], str(ran))
    out = I.check_diff({"src/x.py": ""}, {"src/x.py": ""}, ledger=ledger,
                       registry={"demo": counting}, project="nevertwice")
    check("the path-scoped one fires when the path matches", len(out) == 1)


def test_the_rendered_output_is_the_whole_cost() -> None:
    print("\n- what reaches context is one line, and it names the invariant -")
    n = _note(subject="lazy-import")
    n["_rank"] = 1.0
    out = I.check_diff({}, {}, ledger=[n], registry={"demo": _fires})
    text = I.render(out)
    check("one line", len(text.splitlines()) == 1, text)
    check("it names the invariant", n["id"] in text)
    check("it carries the message", n["message"] in text)
    check("it names the subject", "lazy-import" in text)


# ---------------------------------------------------------------------------
# I2: the lifecycle, stricter than intuition wants
# ---------------------------------------------------------------------------


def test_two_false_positives_retire_an_invariant() -> None:
    """Not five. The spec's own warning is that an invariant firing on 30% of diffs
    is switched off in week one, and after that being right does not matter. The
    first candidate checker measured 27.8% (BLAST_RADIUS_D5.md), so the budget for
    being wrong is spent in two."""
    print(chr(10) + "- two false positives, and it is gone -")
    n = _note(subject="a")
    check("one is a warning", I.feedback(n, "false_positive")["status"] == "advisory")
    check("two is retirement", I.feedback(n, "false_positive")["status"] == "retired")
    check("the count is kept", n["false_positives"] == 2)
    check("the threshold is two, not five", I.RETIRE_AFTER_FALSE_POSITIVES == 2)


def test_being_helpful_does_not_buy_forgiveness() -> None:
    """A guard earns corroboration; an invariant does not get to average its
    mistakes away. Attention spent on a wrong finding is not refunded by a right one."""
    print(chr(10) + "- helpfulness does not reset the budget -")
    n = _note(subject="a")
    I.feedback(n, "false_positive")
    for _ in range(10):
        I.feedback(n, "helped")
    check("ten wins do not undo one miss", n["false_positives"] == 1)
    check("the second miss still retires it",
          I.feedback(n, "false_positive")["status"] == "retired")
    check("and the wins were counted", n["helped"] == 10)


def test_retirement_is_recorded_not_just_applied() -> None:
    print(chr(10) + "- a retirement nobody can see is a bug nobody can find -")
    n = _note(subject="a")
    I.feedback(n, "false_positive")
    I.feedback(n, "false_positive")
    check("it says when", bool(n.get("retired_date")))
    check("it says why", "false positive" in (n.get("retired_reason") or "").lower(),
          str(n.get("retired_reason")))


def test_firing_is_recorded_and_costs_nothing_when_it_does_not() -> None:
    print(chr(10) + "- the counters move only when something happens -")
    n = _note(subject="a")
    n["_rank"] = 1.0
    out = I.check_diff({}, {}, ledger=[n], registry={"demo": _fires})
    I.record_fired([f.invariant_id for f in out], [n])
    check("fired is 1", n["fired"] == 1)
    check("last_fired is stamped", bool(n["last_fired"]))
    I.record_fired([], [n])
    check("an empty round changes nothing", n["fired"] == 1)


def test_a_retired_invariant_can_be_revived_only_deliberately() -> None:
    print(chr(10) + "- revival is a decision, not a side effect -")
    n = _note(subject="a")
    I.feedback(n, "false_positive")
    I.feedback(n, "false_positive")
    I.feedback(n, "helped")
    check("a helped signal does not revive it", n["status"] == "retired")
    I.revive(n)
    check("revive does, and resets the budget",
          n["status"] == "advisory" and n["false_positives"] == 0)
    check("but it remembers it was retired once", n["revivals"] == 1)


def test_an_unknown_outcome_is_refused_rather_than_ignored() -> None:
    print(chr(10) + "- an outcome nobody defined is not silently dropped -")
    n = _note(subject="a")
    try:
        I.feedback(n, "sort-of-helped")
        check("it raises", False, "no exception")
    except ValueError:
        check("it raises", True)
    check("and the note is untouched", n["false_positives"] == 0)


# ---------------------------------------------------------------------------
# I3: the preconfigured pack -- the cold-start claim's delivery vehicle
# ---------------------------------------------------------------------------


def _run(before, after):
    ledger = P.build_pack()
    return I.check_diff(before, after, ledger=ledger, registry=P.REGISTRY, cap=-1)


def test_the_pack_ships_advisory_and_says_where_it_came_from() -> None:
    print(chr(10) + "- the pack is preconfigured, and admits it -")
    pack = P.build_pack()
    check("every note is built", all(pack) and len(pack) == len(P.PACK))
    check("all advisory", all(n["status"] == "advisory" for n in pack))
    check("all preconfigured", all(n["provenance"] == "preconfigured" for n in pack))
    check("every checker in the pack is registered",
          all(n["checker"] in P.REGISTRY for n in pack))
    check("the ids are stable across builds",
          [n["id"] for n in P.build_pack(date="2020-01-01")] == [n["id"] for n in pack])


def test_a_new_mutable_default_fires_and_an_old_one_does_not() -> None:
    print(chr(10) + "- a mutable default, and only a NEW one -")
    before = {"a.py": "def f(x): pass" + chr(10)}
    after = {"a.py": "def f(x, items=[]): pass" + chr(10)}
    out = _run(before, after)
    check("it fires", len(out) == 1, str(out))
    check("it names the parameter", "items" in out[0].subject, out[0].subject)
    check("a pre-existing one does not fire", _run(after, after) == [])
    check("a dict default fires",
          len(_run({"a.py": "def f(): pass" + chr(10)},
                   {"a.py": "def f(o={}): pass" + chr(10)})) == 1)
    check("set() is a CALL, not a literal, so the checker does not see it",
          len(_run({"a.py": "def f(): pass" + chr(10)},
                   {"a.py": "def f(*, o=set()): pass" + chr(10)})) == 0)
    check("an immutable default does not fire",
          _run({"a.py": "def f(): pass" + chr(10)},
               {"a.py": "def f(o=None, n=0, s=''): pass" + chr(10)}) == [])
    check("a method's mutable default fires with its class in the subject",
          "C.m" in _run({"a.py": "class C:" + chr(10) + "    def m(self): pass" + chr(10)},
                        {"a.py": "class C:" + chr(10) +
                         "    def m(self, o=[]): pass" + chr(10)})[0].subject)


def test_a_bare_except_fires_and_a_typed_one_does_not() -> None:
    print(chr(10) + "- bare except, and not a judgement call -")
    ok = {"a.py": "try:" + chr(10) + "    f()" + chr(10) + "except ValueError:" + chr(10) + "    pass" + chr(10)}
    bare = {"a.py": "try:" + chr(10) + "    f()" + chr(10) + "except:" + chr(10) + "    pass" + chr(10)}
    check("bare fires", len(_run(ok, bare)) == 1)
    check("typed does not", _run(bare, ok) == [])
    broad = {"a.py": "try:" + chr(10) + "    f()" + chr(10) + "except Exception:" + chr(10) + "    pass" + chr(10)}
    check("except Exception is not the invariant's business", _run(ok, broad) == [])
    check("an unchanged bare except does not fire", _run(bare, bare) == [])


def test_an_assert_outside_a_test_fires_and_inside_one_does_not() -> None:
    print(chr(10) + "- assert is deleted by -O, except where it belongs -")
    check("shipped code fires",
          len(_run({"a.py": "def f(x): pass" + chr(10)},
                   {"a.py": "def f(x):" + chr(10) + "    assert x" + chr(10)})) == 1)
    check("a test file does not",
          _run({"tests/test_a.py": "def f(x): pass" + chr(10)},
               {"tests/test_a.py": "def f(x):" + chr(10) + "    assert x" + chr(10)}) == [])
    check("a _test_ file does not",
          _run({"tests/_test_a.py": "def f(x): pass" + chr(10)},
               {"tests/_test_a.py": "def f(x):" + chr(10) + "    assert x" + chr(10)}) == [])


def test_the_pack_is_silent_on_code_that_breaks_nothing() -> None:
    """The property that decides whether anyone leaves it switched on."""
    print(chr(10) + "- silence on ordinary work -")
    before = {"a.py": "def f(x):" + chr(10) + "    return x + 1" + chr(10)}
    after = {"a.py": "def f(x):" + chr(10) + "    return x + 2" + chr(10)}
    check("a body change is silent", _run(before, after) == [])
    check("a new function is silent",
          _run(before, {"a.py": before["a.py"] + "def g(y=None): return y" + chr(10)}) == [])
    check("a non-python file is silent",
          _run({"a.md": "text"}, {"a.md": "other text"}) == [])
    check("an unparseable file is silent",
          _run({"a.py": "def f(): pass" + chr(10)},
               {"a.py": "def f(: pass" + chr(10)}) == [])
    check("the empty diff is silent", _run({}, {}) == [])


def test_the_pack_needs_no_prior_session_to_work() -> None:
    """The cold-start claim, in the only form this task can assert it: the pack fires
    on a repository the tool has never seen, with an empty store and no history. What
    that is WORTH is T3's measurement, not this one's."""
    print(chr(10) + "- minute zero: no store, no history, still a finding -")
    ledger = P.build_pack()
    check("no note has ever fired", all(n["fired"] == 0 for n in ledger))
    check("no note has any provenance in this repository",
          all(n["born_from"] == [] for n in ledger))
    out = I.check_diff({"brand_new.py": ""},
                       {"brand_new.py": "def f(items=[]): pass" + chr(10)},
                       ledger=ledger, registry=P.REGISTRY)
    check("and it still fires", len(out) == 1)


def test_the_pack_carries_the_cap_with_it() -> None:
    print(chr(10) + "- three violations in one diff is still one finding -")
    before = {"a.py": "def f(): pass" + chr(10)}
    after = {"a.py": ("def f(items=[]):" + chr(10) + "    assert items" + chr(10) +
                      "    try:" + chr(10) + "        g()" + chr(10) +
                      "    except:" + chr(10) + "        pass" + chr(10))}
    check("all three checkers fire", len(_run(before, after)) == 3)
    capped = I.check_diff(before, after, ledger=P.build_pack(), registry=P.REGISTRY)
    check("but one reaches context", len(capped) == 1)
    check("and it is the highest ranked one",
          "items" in capped[0].subject, capped[0].subject)


def test_the_killswitch_costs_nothing_and_honours_every_falsy_spelling() -> None:
    """PREREGISTRATION.md section 7, I3-K2. Checked before the ledger is read, so
    "off" costs a dictionary lookup -- a killswitch that still pays for the machinery
    it disables is a killswitch nobody believes."""
    print(chr(10) + "- the killswitch, and it is cheap -")
    ran = []

    def counting(before, after, note):
        ran.append(note["id"])
        return _fires(before, after, note)

    ledger = [_note(subject="a")]
    for spelling in ("0", "false", "FALSE", "no", "off", ""):
        out = I.check_diff({}, {}, ledger=ledger, registry={"demo": counting},
                           environ={I.KILLSWITCH_ENV: spelling})
        check(f"{spelling!r} disables it", out == [], f"{spelling!r} -> {out}")
    check("and no checker was called", ran == [], str(ran))
    check("unset means on",
          len(I.check_diff({}, {}, ledger=ledger, registry={"demo": counting},
                           environ={})) == 1)
    check("1 means on",
          len(I.check_diff({}, {}, ledger=ledger, registry={"demo": counting},
                           environ={I.KILLSWITCH_ENV: "1"})) == 1)


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_a_note_is_well_formed_or_it_is_not_a_note,
               test_the_id_is_a_function_of_what_the_note_is_about,
               test_a_ledger_holds_scars_and_invariants_together,
               test_storage_round_trips_and_never_writes_where_it_was_not_asked,
               test_an_unreadable_ledger_is_empty_rather_than_fatal,
               test_nothing_fires_means_nothing_costs,
               test_at_most_one_finding_reaches_context,
               test_a_retired_invariant_never_fires,
               test_a_checker_that_crashes_does_not_break_the_commit,
               test_scope_is_honoured_before_any_checker_runs,
               test_the_rendered_output_is_the_whole_cost,
               test_two_false_positives_retire_an_invariant,
               test_being_helpful_does_not_buy_forgiveness,
               test_retirement_is_recorded_not_just_applied,
               test_firing_is_recorded_and_costs_nothing_when_it_does_not,
               test_a_retired_invariant_can_be_revived_only_deliberately,
               test_an_unknown_outcome_is_refused_rather_than_ignored,
               test_the_pack_ships_advisory_and_says_where_it_came_from,
               test_a_new_mutable_default_fires_and_an_old_one_does_not,
               test_a_bare_except_fires_and_a_typed_one_does_not,
               test_an_assert_outside_a_test_fires_and_inside_one_does_not,
               test_the_pack_is_silent_on_code_that_breaks_nothing,
               test_the_pack_needs_no_prior_session_to_work,
               test_the_pack_carries_the_cap_with_it,
               test_the_killswitch_costs_nothing_and_honours_every_falsy_spelling):
        fn()
    print(f"\ninvariant notes: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
