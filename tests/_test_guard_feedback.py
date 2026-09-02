#!/usr/bin/env python3
"""A fired guard records WHICH session saw it, and does not repeat itself in that session.

record_fired bumped `fired` and `last_fired` and took no session id, so on every automatic
path `seen_sessions` stayed empty. Three things followed: `corroborations` never grew, so
the advisory→blocking promotion gated on K distinct sessions could never fire; a noisy
guard could never be retired on evidence; and nothing knew a guard had already spoken, so
the same advisory re-injected on every matching PreToolUse for the whole session. In the
live ledger 7 of 95 guards had fired, one 35 times, and every one had seen_sessions: [].
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import guards as g  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def guard(gid="g-1"):
    return {"id": gid, "fired": 0, "seen_sessions": [], "corroborations": 0, "last_fired": ""}


print("\n- the session is recorded, and distinct sessions corroborate -")
led = [guard()]
g.record_fired(["g-1"], guards=led, persist=False, session="s-A")
check("the session is recorded", led[0]["seen_sessions"] == ["s-A"], str(led[0]))
check("corroborations follow the distinct sessions", led[0]["corroborations"] == 1)
g.record_fired(["g-1"], guards=led, persist=False, session="s-A")
check("the SAME session does not corroborate twice", led[0]["corroborations"] == 1)
check("fired still counts every hit", led[0]["fired"] == 2, str(led[0]["fired"]))
g.record_fired(["g-1"], guards=led, persist=False, session="s-B")
check("a distinct session does corroborate", led[0]["corroborations"] == 2)

print("\n- the ledger entry stays bounded -")
led2 = [guard("g-2")]
for i in range(g.SEEN_SESSIONS_CAP + 25):
    g.record_fired(["g-2"], guards=led2, persist=False, session=f"s{i}")
check("seen_sessions is capped", len(led2[0]["seen_sessions"]) == g.SEEN_SESSIONS_CAP,
      str(len(led2[0]["seen_sessions"])))
check("the cap keeps the NEWEST sessions", led2[0]["seen_sessions"][-1] == f"s{g.SEEN_SESSIONS_CAP + 24}")

print("\n- a guard that has spoken does not repeat -")
check("already_delivered is true for a seen session", g.already_delivered(led[0], "s-A") is True)
check("already_delivered is false for a new session", g.already_delivered(led[0], "s-Z") is False)
check("no session id means no suppression", g.already_delivered(led[0], None) is False)
check("an empty ledger entry never suppresses", g.already_delivered({}, "s-A") is False)

print("\n- the old signature still works -")
led3 = [guard("g-3")]
g.record_fired(["g-3"], guards=led3, persist=False)
check("a call with no session still bumps fired", led3[0]["fired"] == 1)
check("and records no session", led3[0]["seen_sessions"] == [])

print(f"\nguard feedback: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
