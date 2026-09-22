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
import re
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
             "extractor", "embedding_space", "twin_calibration", "index_age", "scheduler", "graph_generator",
             "orphaned_temp", "list_fields", "package_source"]

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


def test_it_says_which_twin_gate_this_install_is_running() -> None:
    """The twin gate RETIRES live notes. A machine-local calibration that falls out of
    bounds - after an update tightened them, say - silently reverts it to the baked weights,
    and the only trace is one line written into the hook's log at import. An operator
    updating an install has to be able to ask."""
    print("\n- which twin calibration is in force -")
    saved = os.environ.get("NEVERTWICE_TWIN_FILE")
    with tempfile.TemporaryDirectory(prefix="nevertwice_doc_twin_") as tmp:
        tf = Path(tmp) / "twin_calibration.json"
        good = {"space": "test-embed", "w": [0.98725, 2, 3, 4, 5], "b": -0.5,
                "mu": [0, 0, 0, 0, 0], "sd": [0.777, 1, 1, 1, 1]}
        try:
            os.environ["NEVERTWICE_TWIN_FILE"] = str(tf)
            result = doctor.check_twin_calibration()
            check("no file is not a problem", result["status"] == doctor.SKIP,
                  result["status"] + " " + result["detail"])
            check("and it says the baked weights are what runs",
                  "baked" in result["detail"], result["detail"])

            tf.write_text(json.dumps(good), encoding="utf-8")
            result = doctor.check_twin_calibration()
            check("an accepted calibration is ok", result["status"] == doctor.OK,
                  result["status"] + " " + result["detail"])
            check("and it names the space it is keyed to",
                  "test-embed" in result["detail"], result["detail"])

            # Degenerate, and with values that do not collide with the BOUNDS the message
            # quotes: the point of the next check is that nothing from the FILE is echoed.
            tf.write_text(json.dumps({**good, "sd": [3.3e-6, 1, 1, 1, 1],
                                      "w": [812.5, 1, 1, 1, 1]}), encoding="utf-8")
            result = doctor.check_twin_calibration()
            check("a refused calibration is a warning, not a failure",
                  result["status"] == doctor.WARN, result["status"])
            check("it says the gate fell back rather than that it is broken",
                  "baked" in result["detail"], result["detail"])
            check("it gives the bound that refused it",
                  "out of bounds" in result["detail"], result["detail"])
            check("and the repair points at the retraining procedure",
                  "TWIN_GATE" in result["repair"], result["repair"])
            check("no calibration value is echoed anywhere in the check",
                  "812.5" not in json.dumps(result) and "3.3e-06" not in json.dumps(result)
                  and "0.777" not in json.dumps(result), json.dumps(result))

            # A value of the WRONG TYPE took a different path out of the validator: the
            # float() conversion raised, and the exception text - which contains the value -
            # was what the reason carried and this check printed. The bounds case above was
            # the only leak shape covered, and it is the one shape where the message holds
            # no file content to begin with.
            mark = "ZZ-not-a-number-ZZ"
            tf.write_text(json.dumps({**good, "w": [mark, 2, 3, 4, 5]}), encoding="utf-8")
            result = doctor.check_twin_calibration()
            check("a calibration with a string where a number belongs is refused",
                  result["status"] == doctor.WARN, result["status"])
            check("and the doctor prints no part of it",
                  mark not in json.dumps(result), json.dumps(result))
            check("while still naming the field that refused it",
                  "w" in result["detail"] and "str" in result["detail"], result["detail"])

            tf.write_text("{", encoding="utf-8")
            result = doctor.check_twin_calibration()
            check("an unreadable file is a warning that names the error",
                  result["status"] == doctor.WARN and "Error" in result["detail"],
                  result["detail"])
        finally:
            if saved is None:
                os.environ.pop("NEVERTWICE_TWIN_FILE", None)
            else:
                os.environ["NEVERTWICE_TWIN_FILE"] = saved


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
                #: `all(cid in proc.stdout or True for cid in CHECK_IDS)` was here, and it is
                #: identically True: the left operand is discarded, so the twelve identifiers
                #: were asserted about nothing. Measured: with `or True` an EMPTY report and a
                #: report reading "complete nonsense" both pass; without it, both fail. The
                #: second mechanism of the catalogue, literally - a condition true independent
                #: of the system. Found by the auditing session, 2026-09-22.
                #:
                #: Removing `or True` alone would go red for the WRONG reason: the human report
                #: prints LABELS, not identifiers - `[ok  ] store exists and is writable`, never
                #: `store_writable` - so zero of twelve identifiers appear and always did. The
                #: property the check names is true today; the question was malformed. Making
                #: the report print identifiers to satisfy it would damage the product to please
                #: a broken test.
                #:
                #: So it is asked two ways, both of which the report can answer. By COUNT, which
                #: is cheap and population-shaped: one verdict line per check, no more and no
                #: fewer. And by LABEL, which is strict: `doctor.py` carries the pair at every
                #: `_check("store_writable", "store exists and is writable", ...)`, so the
                #: labels are read from the source rather than copied here, where they would be
                #: a second source of truth agreeing on the day it was written.
                _verdicts = [ln for ln in proc.stdout.splitlines()
                             if re.match(r"\s*\[(ok|warn|skip|fail)", ln, re.I)]
                check(f"the human report prints one verdict per check "
                      f"({len(_verdicts)} lines, {len(CHECK_IDS)} checks)",
                      len(_verdicts) == len(CHECK_IDS),
                      f"{len(_verdicts)} != {len(CHECK_IDS)}")
                _doctor_src = (ROOT / "nevertwice" / "doctor.py").read_text(encoding="utf-8")
                _labels = {}
                for _m in re.finditer(r'_check\(\s*"([a-z_]+)"\s*,\s*"([^"]+)"', _doctor_src,
                                      re.S):
                    _labels.setdefault(_m.group(1), _m.group(2))
                #: One identifier builds its label from a variable (`twin_calibration`, whose
                #: title names the model), so eleven of the twelve carry a literal. The count is
                #: asserted, or an empty mapping would make the next check vacuous.
                check(f"the labels are read from doctor.py itself ({len(_labels)} of "
                      f"{len(CHECK_IDS)})", len(_labels) == 11, str(len(_labels)))
                _unnamed = [cid for cid, label in _labels.items() if label not in proc.stdout]
                check("and every one of those labels appears in the report", not _unnamed,
                      ", ".join(_unnamed[:4]))
                check("the report identifies itself", "nevertwice doctor" in proc.stdout)

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


def test_the_oldest_temp_file_is_the_oldest_one() -> None:
    """`min(p.name for p in orphans)` is the alphabetically first name, reported as "oldest".

    The repair says to review them and delete - so the one the operator is pointed at should be
    the one that has been sitting there longest, not the one whose name happens to sort first.
    """
    print("\n- oldest means oldest -")
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "store"
        vault.mkdir()
        old = vault / "zzz-note.md.tmp"
        new = vault / "aaa-note.md.tmp"
        old.write_text("x", encoding="utf-8")
        new.write_text("x", encoding="utf-8")
        long_ago = time.time() - 86400 * 9
        os.utime(old, (long_ago, long_ago))
        result = doctor.check_orphaned_temp(vault)
        check("both files are reported", "2 temporary files" in result["detail"], result["detail"])
        check("and the one named is the one that has been there longest",
              old.name in result["detail"], result["detail"])


def test_a_note_archived_mid_run_does_not_crash_the_diagnostic() -> None:
    """The freshness and index checks glob the note folders, then `stat()` what the glob found.

    A sweep archiving a note between those two steps raises `FileNotFoundError` out of a
    generator inside `max(...)`, and it escapes the check - so a diagnostic run concurrent with
    the maintenance it exists to watch reports nothing at all. A note that moved while we
    counted is the store working, not a fault to crash on.
    """
    print("\n- a diagnostic survives the store moving under it -")
    import pathlib
    with tempfile.TemporaryDirectory() as tmp:
        vault = healthy_store(Path(tmp) / "store", now=time.time())
        # More than one note, or "every listed note vanished" is the honest answer and the
        # check never reaches the branch this test is about.
        for i in range(2):
            (vault / "Patterns" / f"2026-08-2{i}-demo-pattern-survivor-{i}.md").write_text(
                "---\ntype: pattern\n---\n\nbody\n", encoding="utf-8")
        gone = sorted((vault / "Mistakes").glob("*.md"))[0].name
        real_stat = pathlib.Path.stat

        def flaky(self, *a, **k):
            if self.name == gone:
                raise FileNotFoundError(2, "archived between the glob and the stat", str(self))
            return real_stat(self, *a, **k)

        pathlib.Path.stat = flaky
        try:
            fresh = doctor.check_capture_freshness(vault)
            age = doctor.check_index_age(vault)
        except Exception as exc:                  # noqa: BLE001 - that is the defect
            fresh = age = None
            check("the freshness check survives it", False, f"{type(exc).__name__}: {exc}")
        finally:
            pathlib.Path.stat = real_stat
        if fresh is not None:
            check("the freshness check survives it", fresh["status"] in
                  (doctor.OK, doctor.WARN), str(fresh))
            check("and still counts the notes that are there",
                  "notes" in fresh["detail"], str(fresh))
            check("the index-age check survives it too", age["status"] in
                  (doctor.OK, doctor.WARN, doctor.SKIP), str(age))


def test_a_list_the_engine_cannot_read_is_counted() -> None:
    """A hand-edited list the engine's parser reads as text is a number in the report.

    The frontmatter parser reads JSON-style lists - the form the engine writes - and reads an
    unquoted flow list `[a, b]` as ONE string and a block list (`- a` lines under the key) as
    empty. The engine's own notes round-trip; a note edited by hand does not, and its tags or its
    `contested` stamp are lost without a word. Measured on the owner's store 2026-09-23: 0 such
    lists in 17 192 notes, with Obsidian's property editor switched on - a latent risk, and this
    makes the first real one visible instead of discovered.
    """
    print("\n- a list the engine cannot read is counted, not discovered -")
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "store"
        folder = vault / "Decisions"
        folder.mkdir(parents=True)

        def note(name, header):
            (folder / f"{name}.md").write_text(f"---\ntype: decision\n{header}\n---\n\nbody\n",
                                               encoding="utf-8")

        note("2026-01-01-p-decision-json", 'tags: ["python", "testing"]\n'
             'relations: [{"rel": "caused-by", "target": "x"}]\ncontested: []\n'
             'related: [[2025-12-01-p-decision-older]]')
        #: Reached through getattr: a revert of the check must fail by name, not by
        #: AttributeError - the auditing session reverted doctor.py and got the crash.
        check_list_fields = getattr(doctor, "check_list_fields", None)
        check("doctor has a check for list fields the engine reads as text",
              callable(check_list_fields),
              "no check_list_fields - a hand-edited list that loses its tags is silent again")
        if not callable(check_list_fields):
            return
        result = check_list_fields(vault)
        check("JSON lists and a wiki-link - both read as meant - are not counted",
              result["status"] == doctor.OK, f"{result['status']}: {result['detail']}")

        note("2026-01-02-p-decision-flow", "tags: [python, testing]")
        note("2026-01-03-p-decision-block", "tags:\n  - python\n  - testing")
        result = check_list_fields(vault)
        check("an unquoted flow list and a block list are both counted",
              result["status"] == doctor.WARN and result["detail"].startswith("2 list field"),
              f"{result['status']}: {result['detail']}")
        check("and the report names a note and the key, so a human can find it",
              "tags" in result["detail"] and "2026-01-0" in result["detail"], result["detail"])
        check("the repair names the form that reads, not a command that rewrites notes",
              '["' in result["repair"] and not any(d in result["repair"] for d in DESTRUCTIVE),
              result["repair"])

        #: Only ONE whole link is exempt. Each of these starts with `[[` and each is misread:
        #: the parser returns a string, and for `contested` that string becomes a stem named
        #: "[[...]]" that does not exist, so the pair falls off the judge's queue. The first
        #: exemption was the prefix `[[` and silenced all four (the auditing session's probe).
        #: `disputed` is read the same way - the dispute re-queue walks it - so it is held here
        #: too: with only `contested` in the fixture, dropping DISPUTED_KEY from the exemption's
        #: key test stayed green (the auditing session's mutation M3).
        note("2026-01-05-p-decision-links", "contested: [[2026-01-01-p-decision-x]]\n"
             "disputed: [[2026-01-01-p-decision-y]]\n"
             "see_also: [[[note-a]], [[note-b]]]\n"
             "pair: [[note-a]], [[note-b]]\n"
             "topics: [[python, testing]]")
        result = check_list_fields(vault)
        check("a stem list written as a link (contested AND disputed), a list of links and a "
              "nested flow list all count",
              result["detail"].startswith("7 list field"), result["detail"])

        sup = folder / "Superseded"
        sup.mkdir()
        (sup / "2026-01-04-p-decision-old.md").write_text(
            "---\ntype: decision\ntags: [a, b]\n---\n\nbody\n", encoding="utf-8")
        result = check_list_fields(vault)
        check("a retired note is not counted - nothing reads it any more",
              result["detail"].startswith("7 list field"), result["detail"])

    with tempfile.TemporaryDirectory() as tmp:
        result = check_list_fields(Path(tmp) / "absent")
        check("no store: skipped, not failed", result["status"] == doctor.SKIP, str(result))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


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
