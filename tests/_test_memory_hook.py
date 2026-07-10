"""Quick unit tests for the refactored memory_hook helpers.

Run: python -u nevertwice/_test_memory_hook.py
Exits with non-zero on failure. No external services, no real vault writes.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Force a sandbox vault for tests so we never touch the real one.
_sandbox = Path(tempfile.mkdtemp(prefix="memhook_test_"))
_ROOT = r"D:\Projects" if os.name == "nt" else "/projects"   # OS-appropriate test root
os.environ["NEVERTWICE_PROJECT_ROOT"] = _ROOT

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))   # import the package

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import memory_hook as mh  # noqa: E402
mh.VAULT = _sandbox
mh.PROCESSED_DB = _sandbox / ".processed_sessions.json"
mh.STATUS_FILE = _sandbox / "status.txt"

failures: list[str] = []


def check(name, ok, detail=""):
    mark = "OK " if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  ←  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def test_slug_helpers():
    print("\n- slug helpers -")
    check("slugify lowercases", mh.slugify("Hello World") == "hello-world")
    # slugify only strips Windows-forbidden chars; '!' / '?' are passed through.
    check("slugify strips colon", mh.slugify("Fix: TypeError") == "fix-typeerror")
    check("slug_tag lowercases",  mh.slug_tag("Python") == "python")
    check("slug_tag keeps slash", mh.slug_tag("project/foo") == "project/foo")
    check("slug_project no dash", "-" not in mh.slug_project("My-Cool-Project"))
    check("slug_project sample",
          mh.slug_project("Project_Epsilon") == "project_epsilon")


def test_stem_parse():
    print("\n- stem parsing -")
    typed = mh.typed_stem("2026-05-13", "project_epsilon", "pattern",
                          "Code Review And Fix Loop")
    check("typed_stem builds",
          typed == "2026-05-13-project_epsilon-pattern-code-review-and-fix-loop",
          typed)
    p = mh.parse_typed_stem(typed)
    check("parse_typed roundtrip", p is not None
          and p["date"] == "2026-05-13" and p["project"] == "project_epsilon"
          and p["ntype"] == "pattern"
          and p["slug"] == "code-review-and-fix-loop", repr(p))

    sess = mh.session_stem("2026-05-13", "14:35", "project_alpha",
                           "abcdef1234567890")
    check("session_stem builds",
          sess == "2026-05-13-1435-project_alpha-session-abcdef12", sess)
    s = mh.parse_session_stem(sess)
    check("parse_session roundtrip", s is not None
          and s["project"] == "project_alpha" and s["id8"] == "abcdef12", repr(s))

    check("parse_typed rejects session", mh.parse_typed_stem(sess) is None)
    check("parse_session rejects typed", mh.parse_session_stem(typed) is None)
    check("parse_typed handles dashy slug",
          mh.parse_typed_stem("2026-05-13-foo-pattern-a-b-c-d-e-f")["slug"]
          == "a-b-c-d-e-f")


def test_project_filter():
    print("\n- project filter / derivation -")
    sub = os.path.join(_ROOT, "Project_Epsilon")
    deep = os.path.join(_ROOT, "Project_Epsilon", "src", "foo")
    outside = os.path.join((r"C:\Nowhere" if os.name == "nt" else "/nowhere"), "x")
    check("root rejected", not mh.is_tracked_project(_ROOT))
    check("subdir tracked", mh.is_tracked_project(sub))
    check("deep subdir tracked", mh.is_tracked_project(deep))
    check("outside-root non-repo rejected", not mh.is_tracked_project(outside))
    check("empty rejected", not mh.is_tracked_project(""))

    check("project = first segment",
          mh.derive_project_from_cwd(deep) == "project_epsilon")
    check("non-root fallback last segment",
          mh.derive_project_from_cwd(
              os.path.join((r"D:\Other" if os.name == "nt" else "/other"), "proj"))
          == "proj")
    if os.name == "nt":
        check("root forward-slash rejected", not mh.is_tracked_project("D:/Projects"))
    if mh._CASEFOLD:        # case-insensitive FS (Windows / macOS) only
        alt = r"D:\projects\Project_Alpha" if os.name == "nt" else "/Projects/Project_Alpha"
        check("project case-insensitive root",
              mh.derive_project_from_cwd(alt) == "project_alpha")


def test_yaml_quoting():
    print("\n- YAML scalar quoting -")
    fm = mh.fm_block({
        "date": "2026-05-13",
        "project": "project_epsilon",
        "tags": ["python", "cuda"],
        "title_with_colon": "Fix: TypeError in train_loop",
        "with_hash": "topic #urgent",
        "session_id": "3f8a669a",
        "empty": "",
    })
    # 1. The colon-laden value must be wrapped in quotes.
    has_quoted = '"Fix: TypeError in train_loop"' in fm
    check("colon value is quoted", has_quoted, fm[:200])
    # 2. List is JSON-formatted.
    check("tags JSON list", '["python", "cuda"]' in fm)
    # 3. Empty value is quoted.
    check("empty value quoted", 'empty: ""' in fm)
    # 4. Plain alnum stays unquoted.
    check("plain unquoted", "project: project_epsilon" in fm)


def test_truncate_smart():
    print("\n- truncate_smart -")
    txt = "A" * 100 + "B" * 50000 + "Z" * 100
    out = mh.truncate_smart(txt, 12000)
    check("under-budget passes through",
          mh.truncate_smart("hi", 12000) == "hi")
    check("output length within budget", len(out) <= 12000, len(out))
    check("head preserved", out.startswith("A" * 100))
    check("tail preserved", out.endswith("Z" * 100))
    check("middle marker", "[...середина транскрипта вырезана...]" in out)


def test_lock():
    print("\n- advisory lock -")
    check("acquire fresh", mh.acquire_lock(timeout_s=1))
    check("acquire reentrant fails", not mh.acquire_lock(timeout_s=1))
    mh.release_lock()
    check("release allows re-acquire", mh.acquire_lock(timeout_s=1))
    mh.release_lock()


def test_archive_filename_date():
    print("\n- archive_old_sessions: filename-date based -")
    sess = mh.VAULT / "Sessions"
    sess.mkdir(parents=True, exist_ok=True)
    # Dates are relative to TODAY so the test never time-bombs when the calendar
    # rolls past a hard-coded date (the old fixed 2026-05-13 aged out → false fail).
    old_date = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    new_date = datetime.now().strftime("%Y-%m-%d")
    # Old filename (well past cutoff) but freshly touched mtime → must still archive.
    old = sess / f"{old_date}-1200-demo-session-deadbeef.md"
    old.write_text("# old", encoding="utf-8")
    os.utime(old, None)  # touch - fresh mtime
    # New filename (today), fresh mtime → must NOT archive.
    new = sess / f"{new_date}-1200-demo-session-cafebabe.md"
    new.write_text("# new", encoding="utf-8")

    moved = mh.archive_old_sessions(days=30)
    check("archived 1 by filename date", moved == 1)
    check("archived file moved", (sess / "Archive" / old.name).exists())
    check("fresh file kept", new.exists())


def test_unique_path_collision():
    print("\n- write_typed_note collision handling -")
    patterns = mh.VAULT / "Patterns"
    patterns.mkdir(parents=True, exist_ok=True)
    item = {"title": "code-review-loop", "description": "demo"}
    s1 = mh.write_typed_note("Patterns", item, "demo", "2026-05-13", ["t"],
                             "pattern", session_stem_="sess-a")
    s2 = mh.write_typed_note("Patterns", item, "demo", "2026-05-13", ["t"],
                             "pattern", session_stem_="sess-b")
    check("first write keeps base stem", s1.endswith("code-review-loop"), s1)
    check("second write gets -2 suffix", s2.endswith("code-review-loop-2"), s2)
    check("both files exist on disk",
          (patterns / f"{s1}.md").exists() and (patterns / f"{s2}.md").exists())


def test_processed_db_guard():
    print("\n- process_session re-entry guard -")
    db = {"sid123": {"transcript": "x", "processed_at": "2026-05-13T10:00:00"}}
    ok = mh.process_session("sid123", os.path.join(_ROOT, "demo"), "irrelevant",
                            "test", db)
    check("returns False on already-processed", ok is False)
    check("did not write new entry", db["sid123"]["transcript"] == "x")


def test_collect_existing_titles():
    print("\n- collect_existing_titles tolerates similar project names -")
    pat = mh.VAULT / "Patterns"
    pat.mkdir(parents=True, exist_ok=True)
    (pat / "2026-05-13-foo-pattern-aaa.md").write_text("x", encoding="utf-8")
    (pat / "2026-05-13-foo_bar-pattern-bbb.md").write_text("x", encoding="utf-8")
    (pat / "2026-05-13-foo-mistake-ccc.md").write_text("x", encoding="utf-8")
    mh.collect_existing_titles.cache_clear()
    got = mh.collect_existing_titles("foo")
    check("includes own slug", "aaa" in got["pattern"])
    check("excludes look-alike project",
          "bbb" not in got["pattern"], repr(got))
    check("excludes wrong ntype", "ccc" not in got["pattern"])


def test_strip_lead_icon():
    print("\n- strip_lead_icon drops icons/dashes/bullets, keeps text, no 3.14 warning -")
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)   # the char-class range trap fires here
        cases = [("✅ Fixed the bug", "Fixed the bug"), ("— dash title", "dash title"),
                 ("– en dash", "en dash"), ("•· bullet", "bullet"),
                 ("⚠️ warn", "warn"), ("plain", "plain"), ("", "untitled"),
                 ("\U0001f3af target", "target")]
        for inp, want in cases:
            check(f"strip {want!r}", mh._strip_lead_icon(inp) == want,
                  repr(mh._strip_lead_icon(inp)))


def test_version_is_single_sourced():
    print("\n- version single-sourced: config.VERSION == pyproject == mcp -")
    import re
    import config as cfg
    root = Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    pv = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    check("pyproject matches config.VERSION", pv == cfg.VERSION, f"{pv} vs {cfg.VERSION}")
    import mcp_server
    check("mcp SERVER_VERSION matches", mcp_server.SERVER_VERSION == cfg.VERSION)


def test_has_unprocessed_gate():
    print("\n- has_unprocessed: the SessionStart LLM-probe gate (perf audit A1) -")
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        old_root = mh.PROJECTS_ROOT
        try:
            mh.PROJECTS_ROOT = Path(t) / "missing"
            check("missing root -> False (no probe)", mh.has_unprocessed({}) is False)
            root = Path(t) / "projects"; (root / "proj").mkdir(parents=True)
            mh.PROJECTS_ROOT = root
            check("empty root -> False", mh.has_unprocessed({}) is False)
            (root / "proj" / "s1.jsonl").write_text("{}", encoding="utf-8")
            check("one candidate -> True", mh.has_unprocessed({}) is True)
            check("already processed -> False", mh.has_unprocessed({"s1": {}}) is False)
            check("current session excluded -> False",
                  mh.has_unprocessed({}, exclude_session_id="s1") is False)
        finally:
            mh.PROJECTS_ROOT = old_root


if __name__ == "__main__":
    print("=== memory_hook unit tests (sandbox: %s) ===" % mh.VAULT)
    for fn in [test_slug_helpers, test_stem_parse, test_project_filter,
              test_yaml_quoting, test_truncate_smart, test_lock,
              test_archive_filename_date, test_unique_path_collision,
              test_processed_db_guard, test_collect_existing_titles,
              test_strip_lead_icon, test_version_is_single_sourced,
              test_has_unprocessed_gate]:
        try:
            fn()
        except Exception as e:
            print(f"  [FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
            failures.append(f"{fn.__name__}:exception")
    print()
    if failures:
        print(f"FAILED: {len(failures)} - " + ", ".join(failures))
        sys.exit(1)
    print("ALL TESTS PASSED")
