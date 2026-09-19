#!/usr/bin/env python3
"""Marking a claim historical is a statement about the engine, not a way to clear a red check.

A withdrawn claim is pending: it names the command that restores it. A historical claim is not, and
never will be, because what it measured no longer exists. That makes the move one-way, so it has
exactly two preconditions and the tool refuses without them:

* every claim in the family is already withdrawn - a live claim being made historical would be a
  number quietly declared unrestorable while it still counts;
* no claim is cited anywhere. A historical number cannot be re-measured, so a page that still
  prints one has to change FIRST. That is decision 9 in the ledger, in those words: "if any page
  does print one, it is the page that changes, not the claim's status".

    python tests/_test_historical_is_not_an_escape.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before anything reads it

import make_historical as mh  # noqa: E402

P = F = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global P, F
    if ok:
        P += 1
        print(f"  ok   {label}")
    else:
        F += 1
        print(f"  FAIL {label}" + (f" - {detail}" if detail else ""))


REASON = ("the arm was evaluated and decided against, so it measures a mode the project does not "
          "ship and its artifact predates HEAD")


def run(manifest: Path, *extra: str):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "make_historical.py"),
                           "--prefix", "fam.", "--reason", REASON,
                           "--manifest", str(manifest), *extra],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def write(path: Path, claims: list) -> None:
    path.write_text(json.dumps({"claims": claims}, indent=1), encoding="utf-8")


with tempfile.TemporaryDirectory() as td:
    man = Path(td) / "m.json"

    print("# a live claim is not made historical behind the register's back")
    write(man, [{"id": "fam.a", "value": 1, "cited_in": []},
                {"id": "fam.b", "value": 2, "stale": "withdrawn", "cited_in": []}])
    r = run(man, "--apply")
    check("the run is refused", r.returncode == 1, f"rc={r.returncode}")
    check("and it names the live claim", "fam.a" in r.stdout, r.stdout[:200])
    check("nothing was written", "historical" not in man.read_text(encoding="utf-8"))

    print("# a withdrawn claim that a page still prints is refused, and the page is named")
    write(man, [{"id": "fam.a", "stale": "withdrawn", "cited_in": ["docs/PAGE.md"]},
                {"id": "fam.b", "stale": "withdrawn", "cited_in": []}])
    r = run(man, "--apply")
    check("the run is refused", r.returncode == 1, f"rc={r.returncode}")
    check("and it names where the number is printed", "docs/PAGE.md" in r.stdout, r.stdout[:200])

    print("# a pending citation counts too - a page waiting to print it is still a reader")
    write(man, [{"id": "fam.a", "stale": "withdrawn", "cited_in": [],
                 "cited_in_pending": ["README.md"]}])
    r = run(man, "--apply")
    check("the run is refused on a pending citation", r.returncode == 1, f"rc={r.returncode}")
    check("and names that page", "README.md" in r.stdout, r.stdout[:200])

    print("# a reason that is a label rather than a sentence is refused")
    write(man, [{"id": "fam.a", "stale": "withdrawn", "cited_in": []}])
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "make_historical.py"),
                        "--prefix", "fam.", "--reason", "old", "--manifest", str(man), "--apply"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("a one-word reason is refused", r.returncode == 2, f"rc={r.returncode}")

    print("# and a family that meets both preconditions moves, losing its pending flag")
    write(man, [{"id": "fam.a", "stale": "withdrawn", "cited_in": [],
                 "pending_remeasure": True, "withdrawn_on": "2026-09-01"}])
    r = run(man, "--apply")
    check("the run succeeds", r.returncode == 0, r.stdout[-200:])
    got = json.loads(man.read_text(encoding="utf-8"))["claims"][0]
    check("the reason is stamped and marked historical", str(got["stale"]).startswith("historical: "),
          repr(got.get("stale"))[:120])
    check("it is no longer pending a re-measure", "pending_remeasure" not in got, repr(got))
    check("the original withdrawal date is kept", got["withdrawn_on"] == "2026-09-01",
          repr(got.get("withdrawn_on")))
    check("and the day it became historical is recorded", bool(got.get("historical_on")))

print()
print(f"historical is not an escape: {P} passed, {F} failed")
sys.exit(1 if F else 0)
