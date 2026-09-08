"""`api.as_of` (ledger I6): what the memory believed on a date, about a query - retired notes
included, ranked by the query; and `capture_session(date=...)` placing a session in time.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import
from unittest import mock  # noqa: E402

import memory_hook as m  # noqa: E402
import api  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


def note(folder, stem, body, valid_from, valid_to=None, retired=False):
    d = m.VAULT / folder / ("Superseded" if retired else "")
    d.mkdir(parents=True, exist_ok=True)
    fm = [f"date: {stem[:10]}", "project: proj", "type: decision", f"valid_from: {valid_from}"]
    if valid_to:
        fm += [f"valid_to: {valid_to}", "status: superseded"]
    (d / f"{stem}.md").write_text("---\n" + "\n".join(fm) + "\n---\n\n# Decision " + stem[26:]
                                  + "\n\n" + body + "\n", encoding="utf-8")


d = make_sandbox(m, "asof_")
note("Decisions", "2026-03-01-proj-decision-http-timeout", "The HTTP client timeout is 30 seconds for every request.",
     "2026-03-01", valid_to="2026-05-01", retired=True)
note("Decisions", "2026-05-01-proj-decision-http-timeout-five", "The HTTP client timeout is 5 seconds now.",
     "2026-05-01")
note("Decisions", "2026-03-15-proj-decision-cache-ttl", "The cache TTL is one hour.", "2026-03-15")
(m.VAULT / "Patterns").mkdir(exist_ok=True)
note("Patterns", "2026-04-01-other-pattern-retry", "Retry the HTTP client call twice on timeout.", "2026-04-01")

print("\n- what was believed, when -")
mid = api.as_of("http client timeout", "2026-04-01", "proj")
check("in the interval of the retired note, it is the answer",
      mid and mid[0]["stem"].endswith("http-timeout") and "30 seconds" in mid[0]["description"], str([h["stem"] for h in mid]))
check("...and the replacement, not yet believed, is absent", not any(h["stem"].endswith("five") for h in mid))
check("the hit says when it held", mid[0]["valid_from"] == "2026-03-01" and mid[0]["valid_to"] == "2026-05-01")
late = api.as_of("http client timeout", "2026-06-01", "proj")
check("after the replacement, the live note answers and the retired one is gone",
      late and late[0]["stem"].endswith("five") and not any(h["stem"].endswith("http-timeout") for h in late),
      str([h["stem"] for h in late]))
check("on the boundary day the replacement holds (interval is half-open)",
      api.as_of("http client timeout", "2026-05-01", "proj")[0]["stem"].endswith("five"))
check("before anything was believed, nothing", api.as_of("http client timeout", "2026-01-01", "proj") == [])
check("ranked by the query, not by date: the cache note is not first for a timeout query",
      not mid[0]["stem"].endswith("cache-ttl") and any(h["stem"].endswith("cache-ttl") for h in api.as_of("cache ttl", "2026-04-01", "proj")))
check("k caps the list", len(api.as_of("http timeout cache", "2026-06-01", "proj", k=1)) == 1)

print("\n- scope and refusals -")
allp = api.as_of("http client timeout", "2026-04-15", None)
check("project=None walks every project and names each hit's project",
      {h["project"] for h in allp} >= {"proj", "other"}, str([(h["stem"], h["project"]) for h in allp]))
check("the project name is normalised like everywhere else", api.as_of("http client timeout", "2026-04-01", "Proj") == mid)
check("a malformed date is an empty answer, not an exception", api.as_of("x", "yesterday", "proj") == []
      and api.as_of("x", "", "proj") == [])
check("an empty query is an empty answer", api.as_of("   ", "2026-04-01", "proj") == [])
check("read-only: no file appeared", not list((m.VAULT / "Decisions").glob("*.tmp")))

print("\n- capture_session places a session in time -")
seen = {}


def fake_ps(sid, cwd, path, trigger, db, run_log=None, agent=None, transcript_text=None,
            project_override=None, timestamp=None):
    seen["timestamp"] = timestamp
    (run_log if run_log is not None else []).append({"project": project_override, "patterns": 0,
                                                     "mistakes": 0, "decisions": 0})
    return True


with mock.patch.object(m, "llm_available", return_value=True), \
     mock.patch.object(m, "acquire_lock", return_value=True), \
     mock.patch.object(m, "release_lock"), \
     mock.patch.object(m, "load_processed", return_value={}), \
     mock.patch.object(m, "process_session", side_effect=fake_ps), \
     mock.patch.object(m, "rebuild_index"), mock.patch.object(m, "archive_old_sessions"), \
     mock.patch.object(m, "archive_old_typed"), mock.patch.object(m, "prune_processed_db"), \
     mock.patch.object(m, "git_autocommit"):
    api.capture_session("a transcript", project="proj", date="2026-03-01")
    check("the date reaches the engine as the session timestamp", seen.get("timestamp") == "2026-03-01")
    api.capture_session("a transcript", project="proj")
    check("no date: no timestamp (the engine dates the session today)", seen.get("timestamp") is None)
    try:
        api.capture_session("a transcript", project="proj", date="March 1st")
        check("a malformed date is refused", False)
    except ValueError as e:
        check("a malformed date is refused", "ISO" in str(e))

print("\n- the engine dates the ingest from the timestamp -")
with mock.patch.object(m, "_parse_iso", wraps=m._parse_iso) as spy:
    parsed = {"body": "x", "cwd": str(d), "timestamp": "2026-03-01"}
    dt = m._parse_iso(parsed["timestamp"])
    check("an ISO date parses to that day", dt is not None and dt.strftime("%Y-%m-%d") == "2026-03-01")

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
