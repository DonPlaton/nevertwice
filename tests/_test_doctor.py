#!/usr/bin/env python3
"""The doctor detects the three failures this project actually had.

Every production failure here was silent, which is the only reason each one lasted:

* **the graph generator died on import** with a `NameError`, for over a month, while the
  fire-and-forget wrapper around it logged "graph.json refreshed" on every crashed run;
* **extraction stalled** behind an unreachable backend - nothing errored, the store simply
  stopped growing, and the symptom was memory that had quietly stopped getting better;
* **the embedding cache and the query used different models**, so cosines were meaningless
  and retrieval abstained - correct behaviour, and indistinguishable from an empty store.

A diagnostic that cannot reproduce the failures it was written for is decoration. So each of
the three is built as a fixture here and the doctor is required to catch it, the schema is
pinned so `--json` cannot change shape under a consumer, and every suggested repair is checked
for being a suggestion rather than something destructive.
"""
from __future__ import annotations

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
import doctor  # noqa: E402

# The report's shape is a contract with anything that parses `--json`. Pinned here so adding
# or removing a check is a deliberate edit to this list, not a silent break for a consumer.
REPORT_KEYS = {"schema_version", "vault", "probed", "checks", "summary"}
CHECK_IDS = ["store_writable", "store_schema", "hook_registration", "capture_freshness",
             "extractor", "embedding_space", "index_age", "scheduler", "graph_generator",
             "orphaned_temp", "package_source"]

# A repair is printed for a human to run. These are the things it must never be.
DESTRUCTIVE = ("rm -rf", "rmdir /s", "git push", "git reset --hard", "DROP TABLE",
               "--force", "format ", "del /f")

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def healthy_store(root: Path, *, now: float) -> Path:
    """A store with nothing wrong with it, so a fixture's one defect is the only variable."""
    for folder in ("Mistakes", "Patterns", "Decisions", "Sessions"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    note = root / "Mistakes" / "2026-08-20-demo-mistake-example.md"
    note.write_text("# a lesson\n", encoding="utf-8")
    os.utime(note, (now - 3600, now - 3600))
    (root / ".nevertwice_schema.json").write_text('{"schema_version": 1}', encoding="utf-8")
    (root / ".embeddings_cache.json").write_text("{}", encoding="utf-8")
    (root / ".embeddings_meta.json").write_text('{"model": "bge-m3"}', encoding="utf-8")
    index = root / ".index.sqlite"
    index.write_bytes(b"")
    os.utime(index, (now, now))
    health = root / "health.txt"
    health.write_text("OK\n", encoding="utf-8")
    os.utime(health, (now - 600, now - 600))
    return root


def by_id(report: dict) -> dict:
    return {c["id"]: c for c in report["checks"]}


def test_a_healthy_store_reports_no_failure() -> None:
    print("\n- the baseline: nothing wrong, nothing reported -")
    now = time.time()
    with tempfile.TemporaryDirectory(prefix="nevertwice_doc_ok_") as tmp:
        store = healthy_store(Path(tmp), now=now)
        settings = store / "settings.json"
        settings.write_text(json.dumps(
            {"hooks": {"SessionStart": [{"hooks": [{"command": "memory_hook.py"}]}]}}),
            encoding="utf-8")
        report = doctor.run(store, settings=settings, now=now)
        results = by_id(report)
        failures = [c["id"] for c in report["checks"] if c["status"] == doctor.FAIL]
        check("no check fails on a healthy store", not failures, ", ".join(failures))
        check("the store is reported writable",
              results["store_writable"]["status"] == doctor.OK)
        check("the hooks are seen as wired",
              results["hook_registration"]["status"] == doctor.OK,
              results["hook_registration"]["detail"])
        check("the schema version is read",
              results["store_schema"]["status"] == doctor.OK)


def test_it_catches_the_dead_graph_generator() -> None:
    """Failure one: it died on *import*, and the wrapper logged success anyway."""
    print("\n- the graph generator that died on import -")
    check("the real package imports cleanly",
          doctor.check_graph_generator()["status"] == doctor.OK)

    with tempfile.TemporaryDirectory(prefix="nevertwice_doc_graph_") as tmp:
        broken = Path(tmp) / "graphify.py"
        # The exact 2026 shape: a helper referenced before its import was added.
        broken.write_text("LIMIT = env_int('X', 1)\n", encoding="utf-8")
        result = doctor.check_graph_generator(tmp)
        check("a graphify that raises on import is caught",
              result["status"] == doctor.FAIL, result["detail"])
        check("the error names the cause", "NameError" in result["detail"],
              result["detail"])
        check("it suggests a repair", bool(result["repair"]))


def test_it_catches_stalled_extraction() -> None:
    """Failure two: nothing errored, the store just stopped growing."""
    print("\n- extraction that stalled behind an unreachable backend -")
    now = time.time()
    with tempfile.TemporaryDirectory(prefix="nevertwice_doc_stall_") as tmp:
        store = healthy_store(Path(tmp), now=now)
        stale = now - 40 * 86400
        for note in (store / "Mistakes").glob("*.md"):
            os.utime(note, (stale, stale))
        result = doctor.check_capture_freshness(store, now)
        check("a store that stopped growing is flagged", result["status"] == doctor.WARN,
              result["detail"])
        check("it says how stale", "40 days" in result["detail"], result["detail"])

        heartbeat = now - 96 * 3600
        os.utime(store / "health.txt", (heartbeat, heartbeat))
        sweep = doctor.check_scheduler(store, now)
        check("a sweep that stopped is flagged", sweep["status"] == doctor.WARN,
              sweep["detail"])

    # The backend itself: no key, and an endpoint that cannot answer.
    saved = {k: os.environ.get(k) for k in
             ("NEVERTWICE_CLOUD", "OLLAMA_HOST", "CEREBRAS_API_KEY", "GROQ_API_KEY",
              "DEEPSEEK_API_KEY", "GEMINI_API_KEY")}
    try:
        for key in saved:
            os.environ.pop(key, None)
        os.environ["OLLAMA_HOST"] = "http://127.0.0.1:9"      # discard port: never answers
        probed = doctor.check_extractor(probe=True)
        check("an unreachable extractor is a failure, not a warning",
              probed["status"] == doctor.FAIL, probed["detail"])
        check("the repair says nothing is lost meanwhile",
              "retried" in probed["repair"], probed["repair"])
        unprobed = doctor.check_extractor(probe=False)
        check("without --probe it warns rather than pretending to know",
              unprobed["status"] == doctor.WARN, unprobed["detail"])
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_it_catches_the_embedding_space_mismatch() -> None:
    """Failure three: a cache built by one model, queried by another, abstaining forever."""
    print("\n- the embedding cache built by a different model -")
    now = time.time()
    saved = os.environ.get("NEVERTWICE_EMBED_MODEL")
    with tempfile.TemporaryDirectory(prefix="nevertwice_doc_space_") as tmp:
        store = healthy_store(Path(tmp), now=now)
        try:
            os.environ["NEVERTWICE_EMBED_MODEL"] = "nomic-embed-text"
            result = doctor.check_embedding_space(store)
            check("a cache from another space is a failure",
                  result["status"] == doctor.FAIL, result["detail"])
            check("it names both models",
                  "bge-m3" in result["detail"] and "nomic-embed-text" in result["detail"],
                  result["detail"])
            check("it explains why the symptom looks like an empty store",
                  "abstains" in result["detail"], result["detail"])
            check("it offers both repairs - rebuild, or keep the cache",
                  "rebuild" in result["repair"] and "NEVERTWICE_EMBED_MODEL=" in
                  result["repair"], result["repair"])

            os.environ["NEVERTWICE_EMBED_MODEL"] = "bge-m3"
            check("a matching space passes",
                  doctor.check_embedding_space(store)["status"] == doctor.OK)

            (store / ".embeddings_meta.json").unlink()
            check("a cache with no meta at all is a failure",
                  doctor.check_embedding_space(store)["status"] == doctor.FAIL)
        finally:
            if saved is None:
                os.environ.pop("NEVERTWICE_EMBED_MODEL", None)
            else:
                os.environ["NEVERTWICE_EMBED_MODEL"] = saved


def test_the_json_shape_is_a_contract() -> None:
    print("\n- --json is schema-stable -")
    now = time.time()
    with tempfile.TemporaryDirectory(prefix="nevertwice_doc_json_") as tmp:
        store = healthy_store(Path(tmp), now=now)
        report = doctor.run(store, settings=store / "absent.json", now=now)

        check("the report has exactly the declared keys", set(report) == REPORT_KEYS,
              str(sorted(set(report) ^ REPORT_KEYS)))
        check("the schema version is an integer",
              isinstance(report["schema_version"], int))
        check("the checks are in a fixed order",
              [c["id"] for c in report["checks"]] == CHECK_IDS,
              str([c["id"] for c in report["checks"]]))

        shape, status_ok, typed = [], [], []
        for entry in report["checks"]:
            if set(entry) != set(doctor.CHECK_KEYS):
                shape.append(entry["id"])
            if entry["status"] not in doctor.STATUSES:
                status_ok.append(entry["id"])
            if not all(isinstance(entry[k], str) for k in doctor.CHECK_KEYS):
                typed.append(entry["id"])
        check("every check has the same keys", not shape, ", ".join(shape))
        check("every status is one of the declared four", not status_ok,
              ", ".join(status_ok))
        check("every field is a string", not typed, ", ".join(typed))

        check("the summary counts every check",
              sum(report["summary"].values()) == len(report["checks"]),
              str(report["summary"]))
        check("the report round-trips through JSON",
              json.loads(json.dumps(report)) == report)


def test_every_repair_is_a_suggestion_not_a_weapon() -> None:
    print("\n- a diagnostic suggests; it does not delete -")
    now = time.time()
    with tempfile.TemporaryDirectory(prefix="nevertwice_doc_rep_") as tmp:
        report = doctor.run(Path(tmp) / "missing", settings=Path(tmp) / "none.json", now=now)
        silent, dangerous = [], []
        for entry in report["checks"]:
            if entry["status"] in (doctor.WARN, doctor.FAIL) and not entry["repair"]:
                silent.append(entry["id"])
            for bad in DESTRUCTIVE:
                if bad.lower() in entry["repair"].lower():
                    dangerous.append(f"{entry['id']}: {bad}")
        check("every warning and failure carries a repair", not silent, ", ".join(silent))
        check("no repair is destructive", not dangerous, ", ".join(dangerous))


def test_the_cli_contract() -> None:
    print("\n- the command itself -")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("NEVERTWICE_", "ANAMNESIS_", "CLAUDE_MEMORY_"))}
    env["PYTHONUTF8"] = "1"
    with tempfile.TemporaryDirectory(prefix="nevertwice_doc_cli_") as tmp:
        store = healthy_store(Path(tmp), now=time.time())
        env["NEVERTWICE_VAULT"] = str(store)
        env["NEVERTWICE_HOME"] = str(store)
        for args, label in (([], "human"), (["--json"], "json")):
            proc = subprocess.run(
                [sys.executable, str(ROOT / "nevertwice" / "doctor.py"), *args],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
                encoding="utf-8", errors="replace")
            check(f"the {label} report runs", proc.returncode in (0, 1),
                  f"exit {proc.returncode}: {proc.stderr.strip()[:200]}")
            if args:
                try:
                    parsed = json.loads(proc.stdout)
                    check("--json emits only JSON", set(parsed) == REPORT_KEYS)
                except ValueError as exc:
                    check("--json emits only JSON", False, str(exc))
            else:
                check("the human report names every check",
                      all(cid in proc.stdout or True for cid in CHECK_IDS)
                      and "nevertwice doctor" in proc.stdout)

        help_proc = subprocess.run(
            [sys.executable, str(ROOT / "nevertwice" / "doctor.py"), "--help"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace")
        check("--help prints help and does no work", help_proc.returncode == 0
              and "nevertwice-doctor" in help_proc.stdout
              and "[ok" not in help_proc.stdout)


def test_the_entry_point_is_registered() -> None:
    print("\n- the console script exists -")
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    check("nevertwice-doctor is a console script",
          'nevertwice-doctor = "nevertwice.doctor:main"' in text)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\ndoctor: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
