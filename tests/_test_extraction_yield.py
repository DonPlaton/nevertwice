#!/usr/bin/env python3
"""B4: a run of sessions that captured nothing is visible, not green.

Premortem N2 (2026-09-25): a VALID extraction with zero items - the model answered, the JSON
parsed, every list was empty - was marked processed like any other, counted nowhere, and the
store's health stayed green: `doctor`'s capture freshness is kept fresh by the Session note every
session writes, typed lesson or not. The auditor's probe: ten sessions of valid empty JSON -> ten
processed, telemetry total 0, doctor "OK, newest 0.0 days old".

Now every relevant, non-trivial session records its yield (empty or not) in telemetry's
`extraction_yield`, a separate counter from `extraction_failures` - an empty answer is not a
failure - and `doctor` WARNs when the last YIELD_WINDOW such sessions were ALL empty.

The window is derived, not tuned: assume, pessimistically, that half of genuine working sessions
carry no durable lesson (p0 = 0.5, a design bound, not a measurement on any corpus); a false alarm
rate of one window in a thousand then needs 0.5 ** W <= 0.001, so W = 10. One or two empty sessions
in ten is ordinary and stays OK. Off-topic sessions and sessions too short to carry a lesson are
empty by design and are not counted. No retry is switched on (K5 missed its own gate).

    python tests/_test_extraction_yield.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402
import doctor  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


EMPTY = {"project_relevant": True, "patterns": [], "mistakes": [], "decisions": [],
         "session_summary": "read some code", "context_update": ""}
ONE = {**EMPTY, "decisions": [{"title": "http client timeout", "description":
                               "the HTTP client timeout is 5 seconds", "facts": ["5 seconds"]}]}
OFFTOPIC = {**EMPTY, "project_relevant": False}
BODY = "Came back to the API client. That earlier decision is off: the HTTP client timeout is 5 seconds."


def run(answers: list, body: str = BODY) -> Path:
    d = make_sandbox(m, "b4_", offline=True)
    m.update_embeddings = lambda notes: None
    seq = list(answers)
    m.generate_json = lambda prompt, project=None: dict(seq.pop(0))
    db: dict = {}
    for i in range(len(answers)):
        m.process_session(f"b4-{i}", r"D:\Coding\x", "", "ingest", db, transcript_text=body,
                          project_override="yieldproj")
    return d


def yield_of(d: Path) -> dict:
    import json  # noqa: PLC0415
    p = d / "telemetry.json"
    return (json.loads(p.read_text(encoding="utf-8")).get("extraction_yield") or {}) if p.exists() else {}


print("\n- a valid empty extraction is counted as an empty yield, not as a failure -")
d = run([EMPTY] * 10)
y = yield_of(d)
check("ten relevant sessions with valid empty answers are counted, all ten empty",
      y.get("sessions") == 10 and y.get("empty") == 10 and y.get("recent", [])[-10:] == [1] * 10, str(y))
import json  # noqa: E402
fails = json.loads((d / "telemetry.json").read_text(encoding="utf-8"))["extraction_failures"]
check("... and none of them is an extraction FAILURE", fails.get("total", 0) == 0, str(fails))
check("telemetry keeps no rollback copy (.prev) - it is written on every capture now",
      not (d / "telemetry.json.prev").exists())
res = doctor.check_extraction_yield(d)
check(f"doctor WARNs when the last {doctor.YIELD_WINDOW} relevant sessions all captured nothing",
      res["status"] == doctor.WARN and str(doctor.YIELD_WINDOW) in res["detail"], str(res))
check("... with a repair a person can run, not a destructive one", "python" in res["repair"], res["repair"])

print("\n- an ordinary share of empty sessions stays OK -")
d = run([ONE] * 8 + [EMPTY] * 2)
y = yield_of(d)
check("eight sessions with a lesson and two without: counted as two empty of ten",
      y.get("sessions") == 10 and y.get("empty") == 2, str(y))
res = doctor.check_extraction_yield(d)
check("doctor says OK, and says how many were empty", res["status"] == doctor.OK and "2" in res["detail"], str(res))

print("\n- empty by design is not counted -")
d = run([OFFTOPIC] * 3)
check("an off-topic session is not a yield sample", yield_of(d).get("sessions", 0) == 0, str(yield_of(d)))
d = run([EMPTY] * 3, body="ok")
check("a session too short to carry a lesson is not a yield sample", yield_of(d).get("sessions", 0) == 0,
      str(yield_of(d)))
res = doctor.check_extraction_yield(d)
check("with no samples at all, doctor skips the check rather than calling it healthy",
      res["status"] == doctor.SKIP, str(res))

print("\n- the window is the derived one -")
check("YIELD_WINDOW is 10: the smallest W with 0.5 ** W <= 0.001", doctor.YIELD_WINDOW == 10
      and 0.5 ** doctor.YIELD_WINDOW <= 0.001 < 0.5 ** (doctor.YIELD_WINDOW - 1), str(doctor.YIELD_WINDOW))
check("nine empty sessions after a full one are not yet a window of ten",
      doctor._yield_verdict([0] + [1] * 9)[0] == doctor.OK)

print("\n- a partial failure is visible too (the share rule, same premise) -")
check("nine empty and one full, five times over (45 of 50), WARNs although no ten in a row were empty",
      doctor._yield_verdict(([1] * 9 + [0]) * 5)[0] == doctor.WARN, str(doctor._yield_verdict(([1] * 9 + [0]) * 5)))
check("twenty empty of fifty is ordinary and stays OK", doctor._yield_verdict([1, 0, 0, 1, 0] * 10)[0] == doctor.OK)
from math import comb  # noqa: E402
_tail = sum(comb(50, k) for k in range(doctor.YIELD_SHARE_MIN_EMPTY, 51)) / 2 ** 50
check("the share rule adds no false alarm the run rule did not: P(X >= 40 | 50, 0.5) = 1.2e-5 "
      "is below the run rule's 0.5 ** 10",
      doctor.YIELD_SHARE_WINDOW == 50 and _tail < 0.5 ** doctor.YIELD_WINDOW, f"{_tail:.2e}")

print("\n- telemetry stays on the machine: the store's git ignores it -")
#: Written on every capture since B4, and git_autocommit commits the store - to a remote, if the
#: owner has one. EXPORT_SCHEMA says "transmission: none"; without these lines that was untrue.
check("an auto-initialised store ignores telemetry.json (_VAULT_GITIGNORE)",
      "telemetry.json*" in m._VAULT_GITIGNORE, str(m._VAULT_GITIGNORE))
check("install.py's store .gitignore ignores it too",
      '"telemetry.json*"' in (ROOT / "install.py").read_text(encoding="utf-8"))

print("\n- the check is wired into doctor.run, not only callable -")
_run = doctor.run(d, settings=d / "no-settings.json", now=__import__("time").time())
check("doctor.run reports extraction_yield among its checks",
      "extraction_yield" in [c["id"] for c in _run["checks"]], str([c["id"] for c in _run["checks"]]))

print(f"\nextraction yield: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
