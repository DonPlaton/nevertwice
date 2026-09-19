#!/usr/bin/env python3
"""One malformed transcript does not cost the sweep every session behind it.

`_iter_events` says in its own comment that a bad byte "must degrade, never raise - which is a
ValueError, slips past `except OSError`, and crashed the hook mid-sweep, aborting every later
session in the batch (audit A1)". It handles the bytes and the JSON syntax, and then yields
whatever `json.loads` returned. A line that is valid JSON but not an object - `[1, 2]`, `"text"`,
`null`, `7` - yields a list, a string, None or an int, and the first consumer to call `.get()` on
it raises `AttributeError`.

`sweep_unprocessed` calls `read_session_meta` at the top of its per-candidate loop, OUTSIDE the
`try` that guards `process_session`. So the exact outcome the comment describes was still reachable,
by a route the comment did not cover: one stray line in one transcript, and every candidate sorted
behind it is skipped for that run. The type guard belongs at the source, where every consumer
inherits it - `_user_lines` and `_assistant_lines` already filter non-dicts one level down.

    python tests/_test_sweep_survives_a_bad_line.py
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

#: Every shape `json.loads` can return that is not a dict. Each one is a line a transcript can
#: legitimately contain - a JSON array logged by a tool, a bare string, a null placeholder.
NON_OBJECTS = ["[1, 2, 3]", '"a bare string"', "null", "7", "true", "[]"]

tdir = m.VAULT / "transcripts"
tdir.mkdir(parents=True, exist_ok=True)
bad = tdir / "mixed.jsonl"
bad.write_text("\n".join(
    [json.dumps({"cwd": None, "timestamp": None})] + NON_OBJECTS
    + [json.dumps({"cwd": "D:/Coding/demo", "timestamp": "2026-09-19T10:00:00Z"})]
) + "\n", encoding="utf-8")

print("# _iter_events yields events, not whatever the line happened to decode to")
try:
    got = list(m._iter_events(str(bad)))
    raised = None
except Exception as exc:                                       # noqa: BLE001 - that is the defect
    got, raised = [], f"{type(exc).__name__}: {exc}"
check("iterating a transcript with non-object lines does not raise", raised is None, raised or "")
check("every yielded event is a mapping", all(isinstance(e, dict) for e in got),
      f"yielded {[type(e).__name__ for e in got if not isinstance(e, dict)]}")
check("and the real events still come through", len(got) == 2, f"{len(got)} event(s)")

print("# read_session_meta reaches the cwd that sits BEHIND the bad lines")
try:
    meta = m.read_session_meta(str(bad))
    raised = None
except Exception as exc:                                       # noqa: BLE001
    meta, raised = {}, f"{type(exc).__name__}: {exc}"
check("reading the session meta does not raise", raised is None, raised or "")
check("the cwd after the malformed lines is found", meta.get("cwd") == "D:/Coding/demo",
      f"got {meta.get('cwd')!r}")

print("# and one bad transcript does not cost the sweep the sessions queued behind it")
#: The sweep's per-candidate `try` guards `process_session`; `read_session_meta` runs before it.
#: This drives the real entry point so the guard's placement is what is under test, not a helper.
projects = m.VAULT / "projects"
projects.mkdir(parents=True, exist_ok=True)
m.PROJECTS_ROOT = projects
import time  # noqa: E402

old = time.time() - 10_000
for name, lines in (("aaa-broken", NON_OBJECTS),
                    ("zzz-healthy", [json.dumps({"cwd": str(m.VAULT), "timestamp": "2026-09-19T10:00:00Z"})])):
    f = projects / f"{name}.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    import os
    os.utime(f, (old, old))

seen = []
m.process_session = lambda sid, cwd, path, trigger, db, **kw: (seen.append(sid), db.__setitem__(sid, {"ok": 1}), True)[-1]
m.is_tracked_project = lambda cwd: True
try:
    m.sweep_unprocessed({}, max_n=10)
    raised = None
except Exception as exc:                                       # noqa: BLE001
    raised = f"{type(exc).__name__}: {exc}"
check("the sweep does not raise on the broken transcript", raised is None, raised or "")
check("the healthy session sorted behind it is still processed", "zzz-healthy" in seen,
      f"processed {seen}")

print()
print(f"sweep survives a bad line: {P} passed, {F} failed")
sys.exit(1 if F else 0)
