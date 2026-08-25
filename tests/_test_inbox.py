#!/usr/bin/env python3
"""The intervention inbox: one screen, five actions, and every action visible in `git diff`.

GOAL D5's exit criterion is not "the actions work" - it is that **every action round-trips to
the store and shows up in `git diff`**. That is the difference between a control panel and a
dashboard: if a decision lands somewhere the owner cannot see, diff, revert or `git blame`, the
memory is still the one in charge and the screen is decoration.

So this suite runs the real actions against a real git repository and asserts, per action, that
the store's `git status --porcelain` names the file that changed. It also holds the inbox to
the rule D4 established:

    **An operator's opinion is recorded as an operator's opinion, never as evidence.**

`approve` records one honest `accepted` outcome from one session; it does not promote, because
promotion needs K *distinct* sessions and one person clicking approve is one person. Forcing a
status is allowed - it is the owner's repository - but it is stamped `promoted_by` /
`retired_by: operator` with a reason, so a human overruling the lifecycle is legible as exactly
that rather than smuggled in as corroborations the guard never earned.

Skips loudly, never silently, when git is unavailable.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
import api                      # noqa: E402
import guards as G              # noqa: E402
import inbox                    # noqa: E402
import memory_hook as m         # noqa: E402
import outcomes as O            # noqa: E402

PASSED = 0
FAILED = 0
STORE = m.VAULT


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(("git", "-C", str(STORE), *args), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def git_available() -> bool:
    try:
        return git("rev-parse", "--git-dir").returncode == 0 or git("init", "-q").returncode == 0
    except OSError:
        return False


def commit_baseline(label: str = "baseline") -> None:
    git("config", "user.email", "suite@example.invalid")
    git("config", "user.name", "suite")
    git("add", "-A")
    git("commit", "-q", "-m", label)


def dirty() -> list[str]:
    return [ln[3:].strip().strip('"') for ln in git("status", "--porcelain").stdout.splitlines()]


def seed() -> tuple[str, str]:
    """One real note, one guard born from it. Returns (guard_id, stem)."""
    api.remember_lessons([{
        "type": "mistake", "title": "sql-built-by-fstring",
        "description": "A filter was interpolated into the SQL string.",
        "prevention": "Pass values as query parameters.",
    }], project="acme", embed=False)
    stem = next(n["stem"] for n in m._iter_all_notes() if "sql" in n["stem"])
    ledger = G.load_guards()
    guard = G.make_guard(r'f"SELECT', "past mistake: never build SQL by f-string",
                         project="acme", born_from=[stem])
    G.register(ledger, guard)
    G.save_guards(ledger)
    return guard["id"], stem


GUARD_ID, STEM = seed()
GIT = git_available()
if GIT:
    commit_baseline()


def reload(guard_id: str) -> dict | None:
    return next((g for g in G.load_guards() if g["id"] == guard_id), None)


# ------------------------------------------------------------- the screen


def test_the_screen_shows_what_is_being_asserted() -> None:
    print("\n- one screen: guards, contradictions, stale facts -")
    screen = inbox.build()
    check("it declares its shape version", screen["schema_version"] == inbox.SCHEMA_VERSION)
    check("guards are grouped by every status",
          set(screen["guards"]) >= {"blocking", "advisory", "retired"},
          str(sorted(screen["guards"])))
    check("the seeded guard is on the screen",
          any(r["id"] == GUARD_ID for r in screen["guards"]["advisory"]))
    check("contradictions have a section", "contradictions" in screen)
    check("stale facts have both kinds",
          set(screen["stale"]) >= {"orphaned_guards", "unconfirmed_notes"},
          str(sorted(screen["stale"])))
    check("every action is named on the screen",
          set(screen["actions"]) == set(inbox.ACTIONS), str(screen["actions"]))

    row = next(r for r in screen["guards"]["advisory"] if r["id"] == GUARD_ID)
    check("a row carries what the guard earned, with an interval",
          "low" in row["precision"] and "high" in row["precision"])
    check("a row carries the distinct-session tallies",
          set(row["distinct_sessions"]) == {"support", "against"})
    check("a row carries the lifecycle verdict and its reason",
          row["verdict"]["action"] in ("hold", "promote", "demote")
          and len(row["verdict"]["because"]) > 10)
    check("a row carries the source note, so it can be traced",
          STEM in row["born_from"], str(row["born_from"]))

    text = inbox.render(screen)
    check("the rendered screen names the guard", GUARD_ID in text)
    check("the rendered screen offers the actions", "approve <id>" in text)
    check("the rendered screen never presents a fire count as evidence",
          "no outcome yet" in text, "a guard with no resolved outcome must say so")


def test_an_orphaned_guard_is_surfaced() -> None:
    """A guard whose source note has gone is the shape a guard takes when it becomes
    unfalsifiable: it keeps interrupting on evidence nobody can read."""
    print("\n- a guard whose evidence can no longer be read -")
    ledger = G.load_guards()
    orphan = G.make_guard(r"eval\(", "past mistake: never eval user input", project="acme",
                          born_from=["2020-01-01-acme-mistake-long-since-deleted"])
    G.register(ledger, orphan)
    G.save_guards(ledger)
    stale = inbox.build()["stale"]["orphaned_guards"]
    check("it is listed", any(o["id"] == orphan["id"] for o in stale), str(stale))
    check("the missing note is named",
          any("long-since-deleted" in s for o in stale for s in o["missing"]))
    check("a guard with a live source is not listed",
          not any(o["id"] == GUARD_ID for o in stale))


def test_an_unconfirmed_note_is_surfaced_then_clears() -> None:
    print("\n- a fact nobody has re-confirmed in months -")
    old_date = (datetime.now() - timedelta(days=inbox.STALE_DAYS + 30)).strftime("%Y-%m-%d")
    stale_stem = f"{old_date}-acme-mistake-an-old-unconfirmed-fact"
    path = m.VAULT / "Mistakes" / f"{stale_stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n"
                    f"date: {old_date}\nproject: acme\ntype: mistake\ntags: []\n"
                    "---\n\n# An old unconfirmed fact\n\nIt was true once.\n",
                    encoding="utf-8")

    listed = inbox.build()["stale"]["unconfirmed_notes"]
    check("it is listed as unconfirmed", any(n["stem"] == stale_stem for n in listed),
          str([n["stem"] for n in listed]))
    check("its age is reported",
          next((n["age_days"] for n in listed if n["stem"] == stale_stem), 0)
          >= inbox.STALE_DAYS)

    result = inbox.confirm(stale_stem)
    check("confirm succeeds", result["ok"], result["detail"])
    check("it leaves the stale list once confirmed",
          not any(n["stem"] == stale_stem
                  for n in inbox.build()["stale"]["unconfirmed_notes"]),
          "a confirmed note that stays on the screen makes the action pointless")
    header = path.read_text(encoding="utf-8").split("\n---")[0]
    check("the review stamp is in the note's own frontmatter", "reviewed:" in header, header)
    check("confirming did not destroy the existing frontmatter",
          "project: acme" in header and "type: mistake" in header, header)


# ------------------------------------------- the exit criterion: git diff


def test_every_action_lands_in_the_store_and_shows_in_git() -> None:
    """GOAL D5's exit criterion, asserted per action against a real repository."""
    print("\n- every action round-trips to the store and is visible in git diff -")
    if not GIT:
        print("       (skipped: git is not available here)")
        return
    commit_baseline("before-actions")
    check("the store starts clean", not dirty(), str(dirty()))

    cases = [
        ("approve", lambda: inbox.approve(GUARD_ID), "guards.json"),
        ("override", lambda: inbox.override(GUARD_ID, "this call site is a fixed literal"),
         "guards.json"),
        ("edit", lambda: inbox.edit(GUARD_ID, "pass values as query parameters"),
         "guards.json"),
        ("confirm", lambda: inbox.confirm(STEM), f"{STEM}.md"),
        ("retire", lambda: inbox.retire(GUARD_ID, reason="superseded by the linter rule"),
         "guards.json"),
    ]
    for label, call, expected in cases:
        result = call()
        seen = dirty()
        check(f"{label} succeeds", result["ok"], result["detail"])
        check(f"{label} reports which file it wrote",
              any(expected in Path(p).name or expected in p for p in result["changed"]),
              str(result["changed"]))
        check(f"{label} is visible in git status",
              any(expected in path for path in seen),
              f"git saw {seen}, expected something matching {expected!r}")
        check(f"{label} produces a real diff",
              bool(git("diff", "--", *[p for p in seen]).stdout.strip()) or
              bool(git("status", "--porcelain").stdout.strip()),
              "the file is named but nothing actually changed")
        commit_baseline(label)


def test_trace_changes_nothing() -> None:
    print("\n- trace-to-source is read-only by construction -")
    if not GIT:
        print("       (skipped: git is not available here)")
        return
    commit_baseline("before-trace")
    why = inbox.trace(GUARD_ID)
    check("it returns the full WhyFired object", bool(why) and why["id"] == GUARD_ID)
    check("it reaches the source episode",
          any(e.get("stem") == STEM for e in (why["source"]["episodes"] or [])))
    check("it wrote nothing to the store", not dirty(), str(dirty()))


# ------------------------------------- an opinion is not evidence (D4's rule)


def test_an_operator_opinion_is_never_recorded_as_evidence() -> None:
    print("\n- approve is one session's outcome, not a promotion -")
    ledger = G.load_guards()
    guard = G.make_guard(r"pickle\.loads", "past mistake: never unpickle untrusted data",
                         project="acme")
    G.register(ledger, guard)
    G.save_guards(ledger)
    gid = guard["id"]

    for _ in range(G.K_PROMOTE + 4):
        inbox.approve(gid)
    stored = reload(gid)
    check("repeated approvals do not promote it", stored["status"] == "advisory",
          f"status={stored['status']} after {G.K_PROMOTE + 4} approvals")
    check("they credit exactly one distinct session", O.support_sessions(stored) == 1,
          str(O.support_sessions(stored)))
    check("every approval still counts in the totals",
          O.block(stored)["counts"]["accepted"] >= G.K_PROMOTE + 4)

    result = inbox.approve(gid, promote=True, reason="I wrote this rule and I stand behind it")
    stored = reload(gid)
    check("--promote does force the status", result["ok"] and stored["status"] == "blocking")
    check("and stamps it as an operator decision",
          (stored.get("promoted_by") or {}).get("who") == "operator", str(stored.get("promoted_by")))
    check("with the reason, so it is auditable",
          "stand behind it" in (stored.get("promoted_by") or {}).get("reason", ""))
    check("the evidence counters were not inflated to justify it",
          O.support_sessions(stored) == 1,
          "forcing a status must not manufacture distinct sessions")

    retired = inbox.retire(gid, reason="the linter covers this now")
    stored = reload(gid)
    check("retire is stamped as an operator decision too",
          retired["ok"] and (stored.get("retired_by") or {}).get("who") == "operator")


def test_the_actions_refuse_what_they_should() -> None:
    print("\n- the actions fail closed -")
    check("an override with no reason is refused",
          not inbox.override(GUARD_ID, "  ")["ok"],
          "the reason IS the learned exception; without one the feedback teaches nothing")
    check("an edit to an empty message is refused",
          not inbox.edit(GUARD_ID, "   ")["ok"])
    check("an unknown guard id is refused, not silently created",
          not inbox.approve("g-nope")["ok"] and not inbox.retire("g-nope")["ok"]
          and not inbox.edit("g-nope", "x")["ok"])
    check("confirming a note that does not exist is refused",
          not inbox.confirm("2020-01-01-acme-mistake-not-here")["ok"])
    check("tracing an unknown guard returns None", inbox.trace("g-nope") is None)

    ledger = G.load_guards()
    pack = G.make_guard(r"chmod 777", "pack: never chmod 777")
    pack["pack"] = True
    G.register(ledger, pack)
    G.save_guards(ledger)
    result = inbox.approve(pack["id"], promote=True, reason="I like it")
    check("even the operator cannot promote a cold-start pack guard",
          not result["ok"] and "pinned advisory" in result["detail"], result["detail"])
    check("but the pack guard can still be retired", inbox.retire(pack["id"])["ok"])


def main() -> int:
    for fn in (test_the_screen_shows_what_is_being_asserted,
               test_an_orphaned_guard_is_surfaced,
               test_an_unconfirmed_note_is_surfaced_then_clears,
               test_every_action_lands_in_the_store_and_shows_in_git,
               test_trace_changes_nothing,
               test_an_operator_opinion_is_never_recorded_as_evidence,
               test_the_actions_refuse_what_they_should):
        fn()
    print(f"\ninbox: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
