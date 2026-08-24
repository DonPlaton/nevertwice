#!/usr/bin/env python3
"""The comparison document is generated, dated, and keeps its axes apart.

A comparison table is the fastest-rotting thing in a repository: star counts move,
projects get archived, architectures get rewritten, and a hand-maintained table keeps
asserting last year's landscape with no date on it. So the volatile parts are generated
by `tools/comparison_snapshot.py`, and this suite holds the three properties that make
the result trustworthy:

* **dated** - the repository snapshot carries the date it was fetched, and every system
  with a repository is in it;
* **separated** - what a vendor *documents* and what was *measured here* are different
  tables, and retrieval recall never shares a table with end-to-end answer accuracy,
  which depends on the reader model as much as on the memory;
* **stable** - regenerating changes nothing but the counts and the date, so a diff on
  this file is always a real change.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

DATA = json.loads((ROOT / "docs" / "comparison_data.json").read_text(encoding="utf-8"))
SNAPSHOT = json.loads(
    (ROOT / "docs" / "comparison_snapshot.json").read_text(encoding="utf-8"))
DOC_PATH = ROOT / "docs" / "COMPARISON.md"
DOC = DOC_PATH.read_text(encoding="utf-8")
HEAD_TO_HEAD = json.loads(
    (ROOT / "research" / "head_to_head.json").read_text(encoding="utf-8"))

AXES = ("substrate", "retrieval", "temporal", "agnostic", "local", "deploy")
RECALL_RX = re.compile(r"\bR@\d|\brecall\b", re.I)
ACCURACY_RX = re.compile(r"\banswer[- ]?accuracy\b|\baccuracy\b", re.I)

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _tables(markdown: str) -> list[list[str]]:
    """Every markdown table in the document, as its list of rows."""
    tables, current = [], []
    for line in markdown.splitlines():
        if line.startswith("|"):
            current.append(line)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def test_data_file_is_complete() -> None:
    print("\n- the comparison data is complete -")
    systems = DATA["systems"]
    check("every system declares every axis",
          all(all(a in s["vendor"] for a in AXES) for s in systems),
          ", ".join(s["id"] for s in systems
                    if not all(a in s["vendor"] for a in AXES)))

    ids = [s["id"] for s in systems]
    check("system ids are unique", len(ids) == len(set(ids)))

    bad_repo = [s["id"] for s in systems
                if s["repo"] is not None and s["repo"].count("/") != 1]
    check("every repository is owner/name or explicitly null", not bad_repo,
          ", ".join(bad_repo))

    # Nevertwice is the one system whose behaviour this repository can state without
    # citing anyone; every other row is somebody else's claim and needs a source.
    unsourced = [s["id"] for s in systems
                 if s["id"] != "nevertwice" and not s["sources"]]
    check("every third-party row cites a source", not unsourced,
          ", ".join(unsourced))

    check("the survey date is recorded", bool(DATA.get("surveyed")))
    check("the axis rule is stated", bool(DATA.get("axis_rule")))


def test_snapshot_is_dated_and_covers_every_repository() -> None:
    print("\n- the repository snapshot is dated and complete -")
    check("the snapshot records when it was fetched",
          bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", SNAPSHOT.get("fetched", ""))),
          SNAPSHOT.get("fetched", "(absent)"))

    wanted = {s["repo"] for s in DATA["systems"] if s["repo"]}
    missing = sorted(wanted - set(SNAPSHOT["repos"]))
    check("every system with a repository is in the snapshot", not missing,
          ", ".join(missing))

    extra = sorted(set(SNAPSHOT["repos"]) - wanted)
    check("the snapshot holds no repository the data file dropped", not extra,
          ", ".join(extra))

    fields = ("stars", "forks", "pushed_at", "archived", "license")
    incomplete = [r for r, v in SNAPSHOT["repos"].items()
                  if any(f not in v for f in fields)]
    check("every snapshot row carries stars, forks, last push, state and licence",
          not incomplete, ", ".join(incomplete))

    check("the document prints the fetch date",
          SNAPSHOT["fetched"] in DOC, SNAPSHOT["fetched"])


def test_verified_rows_match_the_measured_run() -> None:
    """A row claiming a system "ran here" must correspond to a real result, and a row
    claiming it could not be run must correspond to a recorded blocker."""
    print("\n- the verified table matches research/head_to_head.json -")
    known = {s["id"] for s in DATA["systems"]}
    unknown = [v["id"] for v in DATA["verified"] if v["id"] not in known]
    check("every verified row names a system in the data file", not unknown,
          ", ".join(unknown))

    wrong = []
    for entry in DATA["verified"]:
        raw = HEAD_TO_HEAD.get(entry["id"])
        outcome = entry["outcome"]
        if outcome not in ("ran", "blocked", "not-attempted"):
            wrong.append(f"{entry['id']}: unknown outcome {outcome!r}")
        elif outcome == "not-attempted":
            # The honest third state: nobody wrote an adapter, so there is nothing in
            # the run at all. Claiming it was "blocked" would imply someone tried.
            if raw is not None:
                wrong.append(f"{entry['id']}: says not attempted but the run has an entry")
        elif raw is None:
            wrong.append(f"{entry['id']}: absent from head_to_head.json")
        elif outcome == "ran" and "recall@5" not in raw:
            wrong.append(f"{entry['id']}: claims it ran but has no recall")
        elif outcome == "blocked" and "blocked" not in raw:
            wrong.append(f"{entry['id']}: claims it was blocked but has a result")
    check("each outcome matches what the run recorded", not wrong,
          "; ".join(wrong[:4]))

    unexplained = [v["id"] for v in DATA["verified"] if not v.get("note")]
    check("every verified row says what its outcome means", not unexplained,
          ", ".join(unexplained))


def test_recall_and_answer_accuracy_never_share_a_table() -> None:
    """Retrieval recall and end-to-end answer accuracy measure different things - one
    is a memory pipeline, the other is that pipeline plus a reader model. Ranking them
    in one table is the single most common way these comparisons mislead."""
    print("\n- retrieval recall and answer accuracy stay in separate tables -")
    mixed = []
    for rows in _tables(DOC):
        header = rows[0]
        if RECALL_RX.search(header) and ACCURACY_RX.search(header):
            mixed.append(header.strip()[:70])
    check("no table mixes a recall column with an accuracy column", not mixed,
          "; ".join(mixed))

    # The answer-accuracy figure is quoted in prose, and must say it is another axis.
    if "0.788" in DOC or "0.898" in DOC:
        window = DOC[max(0, DOC.find("0.898") - 900):DOC.find("0.898") + 900]
        check("the answer-accuracy passage says it is a different axis",
              "different" in window.lower() and "axis" in window.lower())


def test_vendor_claims_are_separated_from_measurement() -> None:
    print("\n- vendor claim and measurement are different sections -")
    for region in ("vendor-matrix", "activity", "verified", "head-to-head",
                   "snapshot-note"):
        check(f"the {region} region exists",
              f"<!-- comparison:{region} -->" in DOC)

    vendor = DOC.split("<!-- comparison:vendor-matrix -->")[1] \
                .split("<!-- /comparison:vendor-matrix -->")[0]
    check("the vendor matrix carries a source column", "| source |" in vendor)
    check("the vendor matrix is introduced as claims, not measurements",
          "**claims**, not measurements" in DOC)


def test_regenerating_is_byte_stable() -> None:
    """The exit criterion for this generator: rendering again changes nothing."""
    print("\n- regenerating changes nothing but the counts and the date -")
    before = DOC_PATH.read_bytes()
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "comparison_snapshot.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    check("the generator reports every region current", proc.returncode == 0,
          (proc.stdout + proc.stderr).strip().replace("\n", " | ")[:300])
    check("checking the document did not modify it",
          DOC_PATH.read_bytes() == before)

    write = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "comparison_snapshot.py"), "--write"],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    check("rewriting an up-to-date document is a no-op",
          write.returncode == 0 and DOC_PATH.read_bytes() == before,
          (write.stdout + write.stderr).strip()[:200])


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\ncomparison snapshot: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
