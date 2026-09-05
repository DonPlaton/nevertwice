#!/usr/bin/env python3
"""A fired guard records WHICH session it was delivered to, and firing is not corroboration.

record_fired used to take no session id, so on every automatic path nothing knew a guard had
already spoken and the same advisory re-injected on every matching PreToolUse for the whole
session. The first fix wrote the delivering session into `seen_sessions`, which is the
support list `outcomes` seeds its verdict from - so a guard that merely MATCHED in three
sessions was promoted to blocking by the first feedback of any kind, a false positive
included (review 2026-09-05). Delivery and support are now two fields.
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
    return {"id": gid, "fired": 0, "seen_sessions": [], "delivered_sessions": [],
            "corroborations": 0, "last_fired": ""}


print("\n- the delivering session is recorded, and it is not a corroboration -")
led = [guard()]
g.record_fired(["g-1"], guards=led, persist=False, session="s-A")
check("the session is recorded as a delivery", led[0]["delivered_sessions"] == ["s-A"], str(led[0]))
check("support is untouched", led[0]["seen_sessions"] == [] and led[0]["corroborations"] == 0,
      str(led[0]))
g.record_fired(["g-1"], guards=led, persist=False, session="s-A")
check("the SAME session is recorded once", led[0]["delivered_sessions"] == ["s-A"])
check("fired still counts every hit", led[0]["fired"] == 2, str(led[0]["fired"]))
g.record_fired(["g-1"], guards=led, persist=False, session="s-B")
check("a second session is a second delivery", led[0]["delivered_sessions"] == ["s-A", "s-B"])
check("and still no corroboration", led[0]["corroborations"] == 0)

print("\n- the ledger entry stays bounded -")
led2 = [guard("g-2")]
for i in range(g.SEEN_SESSIONS_CAP + 25):
    g.record_fired(["g-2"], guards=led2, persist=False, session=f"s{i}")
check("delivered_sessions is capped", len(led2[0]["delivered_sessions"]) == g.SEEN_SESSIONS_CAP,
      str(len(led2[0]["delivered_sessions"])))
check("the cap keeps the NEWEST sessions",
      led2[0]["delivered_sessions"][-1] == f"s{g.SEEN_SESSIONS_CAP + 24}")

print("\n- a guard that has spoken does not repeat -")
check("already_delivered is true for a delivered session", g.already_delivered(led[0], "s-A") is True)
check("already_delivered is false for a new session", g.already_delivered(led[0], "s-Z") is False)
check("no session id means no suppression", g.already_delivered(led[0], None) is False)
check("an empty ledger entry never suppresses", g.already_delivered({}, "s-A") is False)
check("a support session alone does not suppress",
      g.already_delivered({"seen_sessions": ["s-A"]}, "s-A") is False)

print("\n- compaction forgets the delivery, so the guard may speak again -")
n = g.forget_delivery("s-A", guards=led, persist=False)
check("one guard forgot the session", n == 1 and led[0]["delivered_sessions"] == ["s-B"],
      str(led[0]["delivered_sessions"]))
check("the guard can be delivered again", g.already_delivered(led[0], "s-A") is False)
check("an unknown session changes nothing", g.forget_delivery("s-none", guards=led, persist=False) == 0)
check("no session id changes nothing", g.forget_delivery(None, guards=led, persist=False) == 0)

print("\n- matching in K sessions does not promote; feedback does -")
real = g.make_guard(r"rm -rf /", "never wipe the root")
check("a real guard is constructible", real is not None)
if real is not None:
    led3 = [real]
    gid = real["id"]
    for sid in ("s-1", "s-2", "s-3"):
        g.record_fired([gid], guards=led3, persist=False, session=sid)
    check("three deliveries, no support", len(led3[0]["delivered_sessions"]) == 3
          and led3[0]["corroborations"] == 0)
    g.feedback(gid, "false_positive", session_id="s-4", guards=led3, persist=False)
    check("a false positive after three matches does NOT promote",
          led3[0]["status"] == "advisory", led3[0]["status"])
    check("the delivery record survives the feedback pass",
          led3[0]["delivered_sessions"] == ["s-1", "s-2", "s-3"], str(led3[0]["delivered_sessions"]))
    for sid in ("s-5", "s-6", "s-7"):
        g.feedback(gid, "accepted", session_id=sid, guards=led3, persist=False)
    check("three accepted sessions promote", led3[0]["status"] == "blocking", led3[0]["status"])
    check("corroborations count the accepting sessions, not the matches",
          led3[0]["corroborations"] == 3, str(led3[0]["corroborations"]))

print("\n- the register's command for the pack size is read-only -")
import contextlib, io  # noqa: E402
_argv = sys.argv
try:
    sys.argv = ["guards", "pack", "--count"]
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        g.main()
    check("pack --count prints the pack size", _buf.getvalue().strip() == str(len(g._UNIVERSAL_GUARDS)),
          _buf.getvalue()[:40])
    check("and installs nothing", not g._ledger_path().exists())
finally:
    sys.argv = _argv

print("\n- the old signature still works -")
led4 = [guard("g-4")]
g.record_fired(["g-4"], guards=led4, persist=False)
check("a call with no session still bumps fired", led4[0]["fired"] == 1)

print(f"\nguard delivery: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
