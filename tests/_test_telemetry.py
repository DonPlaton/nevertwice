#!/usr/bin/env python3
"""Local operational telemetry, and a proof that it is local.

GOAL E3's exit criterion is *default is local-only, provably*. "Provably" is the whole task:
every project with telemetry says it respects your privacy, and the ones that mean it are
distinguishable only by what can be checked. So this suite does not ask the module whether it
sends anything. It establishes two things that do not depend on the module's cooperation:

1. **There is no transport.** The module's AST is walked for any networking import or call -
   `socket`, `urllib`, `http`, `requests`, `httpx`, `ssl`, `smtplib`, `ftplib`, `asyncio`
   openers. A flag can be flipped by a config file, an environment variable, or a future patch
   that means well. The absence of code that could send cannot.
2. **It runs with the network destroyed.** The whole lifecycle - record, refresh, snapshot,
   export - runs in a child process where `socket.socket` raises on construction, and it has
   to complete. If any path reached for the network, that child would fail rather than
   silently succeed on a machine that happens to be offline.

The rest holds the counters to being useful rather than merely present: percentiles rather than
a mean, because a mean hides the tail a user actually notices; a bounded sample that drops the
*oldest* rather than the newest; an export carrying the schema that documents it; and no note
content, titles or queries anywhere in the exported file.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
import api                      # noqa: E402
import memory_hook as m         # noqa: E402
import outcomes as O            # noqa: E402
import telemetry as T           # noqa: E402

PASSED = 0
FAILED = 0

MODULE = ROOT / "nevertwice" / "telemetry.py"

#: Anything that could open a connection. Deliberately broad: the check is worth having only
#: if it fails on the sneaky ways as well as the obvious one.
NETWORK_MODULES = {
    "socket", "ssl", "urllib", "http", "httplib", "requests", "httpx", "aiohttp",
    "smtplib", "ftplib", "telnetlib", "xmlrpc", "webbrowser", "asyncio", "subprocess",
}


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# ═══════════════════════════ the exit criterion ════════════════════════

def test_the_module_contains_no_transport() -> None:
    """Half one: there is no code that could send anything."""
    print("\n- nothing in this module can open a connection -")
    tree = ast.parse(MODULE.read_text(encoding="utf-8"), filename=str(MODULE))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    offending = imported & NETWORK_MODULES
    check("it imports nothing that can reach the network", not offending,
          f"imports {sorted(offending)}")

    # A dynamic import would slip past the check above, so look for that shape too.
    dynamic = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in ("import_module", "__import__"):
                dynamic.append(name)
    check("it performs no dynamic import", not dynamic, str(dynamic))
    check("its imports are a small, readable set",
          imported <= {"__future__", "argparse", "json", "sys", "time", "datetime",
                       "pathlib", "memory_hook", "outcomes"}, str(sorted(imported)))

    text = MODULE.read_text(encoding="utf-8")
    check("and it says so where a reader will see it",
          "no networking code" in text and "cannot" in text)


BLOCKED_CHILD = '''
"""Run the whole telemetry lifecycle with the network destroyed."""
import json, os, socket, sys

class _NoNetwork(socket.socket):
    def __init__(self, *a, **k):
        raise OSError("network access is disabled in this process")

socket.socket = _NoNetwork
def _fail(*a, **k):
    raise OSError("network access is disabled in this process")
socket.create_connection = _fail
socket.getaddrinfo = _fail

os.environ["NEVERTWICE_HOME"] = VAULT
os.environ["NEVERTWICE_VAULT"] = VAULT
os.environ["NEVERTWICE_CLOUD"] = "none"
sys.path.insert(0, PKG)
import telemetry as T

T.record_search(12.5)
T.record_search(40.0)
T.record_extraction_failure("backend unreachable")
T.record_outcome("accepted")
T.refresh_capture_lag()
T.refresh_store_size()
snap = T.snapshot()
out = os.path.join(VAULT, "export.json")
T.export(out)

print(json.dumps({
    "ok": True,
    "counters": sorted(k for k in snap if k not in ("schema_version", "generated")),
    "p50": snap["search_latency"]["p50_ms"],
    "export_exists": os.path.isfile(out),
    "export_has_schema": "schema" in json.loads(open(out, encoding="utf-8").read()),
}))
'''


def test_the_whole_lifecycle_runs_with_the_network_destroyed() -> None:
    """Half two: it completes on a machine where opening a socket raises."""
    print("\n- record, refresh, snapshot and export, with sockets disabled -")
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "store"
        (vault / "Mistakes").mkdir(parents=True)
        (vault / "Mistakes" / "2026-08-01-acme-mistake-x.md").write_text(
            "---\ntype: mistake\n---\n\n# x\n", encoding="utf-8")

        script = Path(tmp) / "blocked.py"
        script.write_text(f"VAULT = {str(vault)!r}\nPKG = {str(ROOT / 'nevertwice')!r}\n"
                          + BLOCKED_CHILD, encoding="utf-8")
        proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300,
                              env={**os.environ, "PYTHONUTF8": "1"})

    check("THE EXIT CRITERION: it completes with the network destroyed",
          proc.returncode == 0,
          (proc.stdout + proc.stderr)[-400:].replace("\n", " | "))
    if proc.returncode != 0:
        return
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    check("every counter is present", set(result["counters"]) == set(T.COUNTERS),
          str(result["counters"]))
    check("latency percentiles were computed", result["p50"] is not None, str(result))
    check("the export was written", result["export_exists"])
    check("and it carries its schema", result["export_has_schema"])


# ═══════════════════════════════ the counters ══════════════════════════

def test_the_counters_answer_the_operational_questions() -> None:
    print("\n- five counters, and what each one is for -")
    check("the counter set is declared once", len(T.COUNTERS) == 5, str(T.COUNTERS))
    snap = T.snapshot()
    for counter in T.COUNTERS:
        check(f"{counter} is in the snapshot", counter in snap)

    T.record_search(10.0)
    T.record_search(20.0)
    T.record_search(100.0)
    snap = T.snapshot()
    check("search latency reports percentiles, not a mean",
          "p50_ms" in snap["search_latency"] and "p95_ms" in snap["search_latency"]
          and "mean" not in json.dumps(snap["search_latency"]),
          "a mean hides the tail a user actually notices")
    check("the max is the slowest sample", snap["search_latency"]["max_ms"] == 100.0,
          str(snap["search_latency"]))

    T.record_extraction_failure("Backend Unreachable")
    T.record_extraction_failure("backend unreachable")
    snap = T.snapshot()
    check("extraction failures are grouped by a normalised reason",
          snap["extraction_failures"]["by_reason"].get("backend_unreachable") == 2,
          str(snap["extraction_failures"]))
    check("a free-text reason cannot fragment the grouping",
          len(snap["extraction_failures"]["by_reason"]) == 1,
          "a reason nobody can group is a reason nobody will aggregate")

    for outcome in ("accepted", "overridden", "accepted"):
        T.record_outcome(outcome)
    snap = T.snapshot()
    check("intervention outcomes are counted per outcome",
          snap["intervention_outcomes"].get("accepted") == 2
          and snap["intervention_outcomes"].get("overridden") == 1,
          str(snap["intervention_outcomes"]))
    check("and they use the D4 vocabulary",
          set(snap["intervention_outcomes"]) <= set(O.OUTCOMES),
          f"{sorted(snap['intervention_outcomes'])} vs {sorted(O.OUTCOMES)}")

    before = dict(snap["intervention_outcomes"])
    T.record_outcome("mostly_helpful")
    after = T.snapshot()["intervention_outcomes"]
    check("an outcome the lifecycle does not have is dropped, not invented",
          after == before,
          "telemetry that invents a category produces a dashboard disagreeing with outcomes.py")


def test_the_latency_sample_is_bounded_and_drops_the_oldest() -> None:
    print("\n- a telemetry file that grows without limit is the problem it reports -")
    for i in range(T.SAMPLE_CAP + 50):
        T.record_search(float(i))
    raw = T.load()["search_latency"]
    check("the sample is capped", len(raw["samples"]) == T.SAMPLE_CAP,
          str(len(raw["samples"])))
    check("the total count keeps rising past the cap", raw["count"] > T.SAMPLE_CAP,
          str(raw["count"]))
    check("it kept the NEWEST samples, not the oldest",
          max(raw["samples"]) == float(T.SAMPLE_CAP + 49),
          "a reservoir that protects old samples reports last month's performance forever")


def test_capture_lag_is_the_counter_that_would_have_shown_the_stall() -> None:
    print("\n- sessions arriving, notes not being written -")
    (m.VAULT / "Sessions").mkdir(parents=True, exist_ok=True)
    api.remember_lessons([{"type": "mistake", "title": "a note", "description": "d",
                           "prevention": "p"}], project="acme", embed=False)
    old = time.time() - 3600
    for folder in ("Mistakes", "Patterns", "Decisions"):
        for path in (m.VAULT / folder).glob("*.md"):
            os.utime(path, (old, old))
    session = m.VAULT / "Sessions" / "2026-08-26-acme-session-fresh.md"
    session.write_text("---\ntype: session\n---\n\n# s\n", encoding="utf-8")

    lag = T.refresh_capture_lag()
    check("the lag is measured", lag["lag_seconds"] is not None, str(lag))
    check("and it is roughly the gap we created", lag["lag_seconds"] > 1800, str(lag))
    check("both sides of the gap are reported",
          bool(lag["last_session"]) and bool(lag["last_note"]), str(lag))

    for folder in ("Mistakes", "Patterns", "Decisions"):
        for path in (m.VAULT / folder).glob("*.md"):
            os.utime(path, None)
    check("a store that is keeping up reports no lag",
          T.refresh_capture_lag()["lag_seconds"] == 0,
          str(T.refresh_capture_lag()))


def test_store_size_is_measured_from_the_store() -> None:
    print("\n- is it still growing -")
    size = T.refresh_store_size()
    check("notes are counted", size["notes"] > 0, str(size))
    check("bytes are counted", size["bytes"] > 0, str(size))
    check("projects are counted", size["projects"] >= 1, str(size))
    before = size["notes"]
    api.remember_lessons([{"type": "pattern", "title": "another note", "description": "d",
                           "prevention": "p"}], project="beta", embed=False)
    after = T.refresh_store_size()
    check("adding a note moves the counter", after["notes"] > before, str(after))
    check("and a second project is seen", after["projects"] >= 2, str(after))


# ═══════════════════════════════ the export ════════════════════════════

def test_the_export_is_documented_and_carries_nothing_private() -> None:
    print("\n- an export you could read before deciding to share it -")
    api.remember_lessons([{
        "type": "mistake", "title": "a very distinctive note title",
        "description": "a very distinctive body about invoices",
        "prevention": "p"}], project="acme", embed=False)
    T.refresh_store_size()

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "export.json"
        payload = T.export(target)
        text = target.read_text(encoding="utf-8")

    check("the export declares a schema version",
          payload["schema_version"] == T.SCHEMA_VERSION)
    check("the schema documents every counter",
          set(T.COUNTERS) <= set(payload["schema"]["fields"]),
          str(sorted(payload["schema"]["fields"])))
    check("the schema states the transmission policy in the file itself",
          "no code that sends" in payload["schema"]["transmission"],
          payload["schema"]["transmission"])
    check("it lists what it does not contain",
          "note content" in payload["schema"]["contains_no"])

    check("no note title is in the export", "a very distinctive note title" not in text,
          "an export carrying titles is an export nobody can safely share")
    check("no note body is in the export", "distinctive body about invoices" not in text)
    check("the raw latency samples are not shipped", '"samples"' not in text,
          "the one field that could grow without bound")


def test_recording_never_breaks_the_caller() -> None:
    """Telemetry that can break the hook is worse than no telemetry."""
    print("\n- best-effort, by contract -")
    raised = []
    for call, arg in ((T.record_search, "not a number"), (T.record_extraction_failure, None),
                      (T.record_outcome, None), (T.record_outcome, "")):
        try:
            call(arg)
        except Exception as exc:                # noqa: BLE001 - the finding
            raised.append(f"{call.__name__}({arg!r}) -> {type(exc).__name__}")
    check("junk input does not raise out of a recorder", not raised, "; ".join(raised))

    path = T._path()
    path.write_text("{not json at all", encoding="utf-8")
    try:
        snap = T.snapshot()
        ok = set(T.COUNTERS) <= set(snap)
    except Exception as exc:                    # noqa: BLE001
        ok = False
        snap = f"{type(exc).__name__}: {exc}"
    check("a corrupt ledger yields a blank snapshot rather than an exception", ok, str(snap))


def main() -> int:
    for fn in (test_the_module_contains_no_transport,
               test_the_whole_lifecycle_runs_with_the_network_destroyed,
               test_the_counters_answer_the_operational_questions,
               test_the_latency_sample_is_bounded_and_drops_the_oldest,
               test_capture_lag_is_the_counter_that_would_have_shown_the_stall,
               test_store_size_is_measured_from_the_store,
               test_the_export_is_documented_and_carries_nothing_private,
               test_recording_never_breaks_the_caller):
        fn()
    print(f"\ntelemetry: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
