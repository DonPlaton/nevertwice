#!/usr/bin/env python3
"""A guard's lifecycle turns on distinct sessions, so the session must not be the client's to name.

`outcomes.record`'s docstring is explicit: an outcome with no session id counts toward the totals
and toward neither distinct-session tally, because "a mechanism that let an unattributed caller
promote or retire a guard by repeating itself would not be a feedback loop, it would be a volume
knob." The MCP tool then took `session_id` straight from the caller, so a client could send three
different strings and turn the knob anyway: three `accepted` calls promoted an advisory guard to
`blocking`, three `false_positive` calls retired it. The reviewer proved both.

The same string was stored verbatim and uncapped. Twenty calls carrying 5 KB ids grew `guards.json`
to 201 KB - a file the PreToolUse hook reads before every edit.

Two fixes, each at the layer that owns the problem. The server derives the session itself, one per
process, because one stdio MCP server is one client session; and `outcomes.record` caps what it
stores, the way `guards.py` already caps `delivered_sessions`.

    python tests/_test_client_cannot_forge_sessions.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

from _sandbox import make_sandbox  # noqa: E402

P = F = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global P, F
    if ok:
        P += 1
        print(f"  ok   {label}")
    else:
        F += 1
        print(f"  FAIL {label}" + (f" - {detail}" if detail else ""))


make_sandbox(m, offline=True)
import guards as _guards  # noqa: E402
import outcomes as _outcomes  # noqa: E402
import mcp_server  # noqa: E402


def _fresh_guard(gid: str = "g-test") -> dict:
    ledger = []
    g = _guards.make_guard(r"subprocess\.run\([^)]*shell=True", "pass a list, not a shell string",
                           project="my_app", born_from=["2026-01-01-my_app-mistake-shell-true"])
    g["id"] = gid
    _guards.register(ledger, g)
    _guards.save_guards(ledger)
    return g


print("# three ids from one client do not make three sessions")
_fresh_guard()
for i in range(3):
    mcp_server._tool_memory_guard_feedback(
        {"guard_id": "g-test", "outcome": "accepted", "session_id": f"forged-{i}"})
g = next(x for x in _guards.load_guards() if x["id"] == "g-test")
check("an advisory guard is not promoted by one client repeating itself",
      g.get("status") != "blocking",
      f"status is {g.get('status')!r} after three forged ids; support sessions "
      f"{_outcomes.block(g)['sessions']['support']}")
check("and only one distinct supporting session was recorded",
      _outcomes.support_sessions(g) <= 1, f"{_outcomes.block(g)['sessions']['support']}")

print("# nor three ids retire one")
_fresh_guard("g-retire")
for i in range(3):
    mcp_server._tool_memory_guard_feedback(
        {"guard_id": "g-retire", "outcome": "false_positive", "session_id": f"forged-{i}"})
g2 = next(x for x in _guards.load_guards() if x["id"] == "g-retire")
check("the guard is not retired by one client repeating itself", g2.get("status") != "retired",
      f"status is {g2.get('status')!r}")

print("# and what is stored is bounded, because a ledger entry is not a log")
_fresh_guard("g-fat")
for i in range(20):
    mcp_server._tool_memory_guard_feedback(
        {"guard_id": "g-fat", "outcome": "accepted", "session_id": "x" * 5000})
ledger_path = m.VAULT / "guards.json"
size = ledger_path.stat().st_size if ledger_path.exists() else 0
check("the guard ledger stays small", size < 32_000, f"{size} bytes")
g3 = next(x for x in _guards.load_guards() if x["id"] == "g-fat")
longest = max((len(s) for s in _outcomes.block(g3)["sessions"]["support"]), default=0)
check("no stored session id is longer than the cap", longest <= 64, f"longest is {longest}")
check("and the number of stored ids is capped too",
      len(_outcomes.block(g3)["sessions"]["support"]) <= _guards.SEEN_SESSIONS_CAP,
      f"{len(_outcomes.block(g3)['sessions']['support'])} stored")

print("# a real distinct session still counts - the mechanism is not disabled, only attributed")
_fresh_guard("g-real")
gl = _guards.load_guards()
gr = next(x for x in gl if x["id"] == "g-real")
for sid in ("session-a", "session-b", "session-c"):
    _outcomes.record(gr, "accepted", session_id=sid)
check("three genuinely distinct sessions are three", _outcomes.support_sessions(gr) == 3,
      f"{_outcomes.block(gr)['sessions']['support']}")

print("# and the other two client-named values are bounded as well")
out, err = mcp_server._tool_memory_entities({"project": "my_app", "k": 1000000})
check("a huge k does not ask for the whole store", not err, out[:60])
out, err = mcp_server._tool_memory_entities({"project": "my_app", "k": -1})
check("a negative k does not slice from the end", not err, out[:60])
out, err = mcp_server._tool_memory_anticipate_feedback({"stem": "not-a-real-stem",
                                                        "outcome": "false_alarm"})
check("feedback on a stem that names no note is refused", err, out[:80])

print()
print(f"client cannot forge sessions: {P} passed, {F} failed")
sys.exit(1 if F else 0)
