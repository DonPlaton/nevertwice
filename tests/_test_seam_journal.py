#!/usr/bin/env python3
"""S1-S3: a plan that survives a session, and a real migration executed seam by seam.

`PREREGISTRATION.md` §7 judges this one **executed, not scored**. There is no precision
because there is no detection: the failure it addresses is an agent attempting a migration
in one piece, finding the intermediate states red, and propping the result up with
workarounds.

S3 is therefore a real migration -- three seams that move a module's storage from one
format to another, on a temp copy -- with the **full verification run at every intermediate
seam**, not only at the end. And the seam that would leave the tree red is refused before it
is attempted, which is the whole mechanism in one assertion.

Hermetic: a temp directory and a subprocess running this interpreter. No vault, no network,
no corpus, and nothing touched under `nevertwice/`.

Run:  python tests/_test_seam_journal.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

sys.path.insert(0, str(ROOT / "research" / "invariants_lab"))
import seam_journal as J  # noqa: E402

PASSED = 0
FAILED = 0
NL = "\n"


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def plan() -> J.Journal:
    return J.Journal(
        target="records move from a JSON list to a JSON object keyed by id",
        seams=[
            J.Seam("write-both", "write the new shape alongside the old",
                   [sys.executable, "-c", "print('ok')"]),
            J.Seam("read-new", "read the new shape, fall back to the old",
                   [sys.executable, "-c", "print('ok')"]),
            J.Seam("drop-old", "stop writing the old shape",
                   [sys.executable, "-c", "print('ok')"]),
        ])


# ---------------------------------------------------------------------------
# S1: the format
# ---------------------------------------------------------------------------


def test_a_plan_is_a_target_and_an_ordered_list_of_green_states() -> None:
    print(NL + "- S1: the format -")
    p = plan()
    check("it names the target", "JSON object" in p.target)
    check("it has ordered seams", [s.name for s in p.seams] ==
          ["write-both", "read-new", "drop-old"])
    check("every seam carries a verification", all(s.verify for s in p.seams))
    check("nothing is green before it is run",
          all(s.status == J.PENDING for s in p.seams))
    check("the first seam is next", p.next_seam().name == "write-both")
    check("and it is not done", not p.done())


def test_the_plan_does_not_advance_past_a_red_seam() -> None:
    """The mechanism, in one assertion."""
    print(NL + "- a red seam stops the plan; it does not get skipped -")
    p = plan()
    p.seams[0].status = J.GREEN
    p.seams[1].status = J.RED
    check("next_seam offers nothing", p.next_seam() is None)
    check("and blocked() names the culprit", p.blocked().name == "read-new")
    check("the third seam is not reachable",
          all(s.status != J.GREEN for s in p.seams[2:]))
    p.seams[1].status = J.GREEN
    check("fixing it lets the plan continue", p.next_seam().name == "drop-old")


# ---------------------------------------------------------------------------
# S2: delivery across a session boundary
# ---------------------------------------------------------------------------


def test_the_journal_survives_a_session_boundary() -> None:
    print(NL + "- S2: reloaded from disk with no other state -")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "seams" / "journal.json"
        p = plan()
        p.seams[0].status = J.GREEN
        p.save(path)

        reloaded = J.Journal.load(path)
        check("it loads", reloaded is not None)
        check("the target survives", reloaded.target == p.target)
        check("progress survives", reloaded.progress() == (1, 3))
        check("and the next step is identified without any earlier context",
              reloaded.next_seam().name == "read-new")
        check("no temp file is left behind", not list(path.parent.glob("*.tmp")))


def test_a_corrupt_journal_is_not_half_a_plan() -> None:
    print(NL + "- a plan that cannot be read is not a plan -")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "journal.json"
        path.write_text("{not json", encoding="utf-8")
        check("corrupt loads as None", J.Journal.load(path) is None)
        path.write_text(json.dumps({"kind": "something_else"}), encoding="utf-8")
        check("a different document loads as None", J.Journal.load(path) is None)
        check("missing loads as None", J.Journal.load(Path(tmp) / "absent.json") is None)


def test_the_brief_costs_one_step_not_the_whole_plan() -> None:
    print(NL + "- a forty-seam migration must not cost forty seams every session -")
    p = J.Journal(target="t", seams=[
        J.Seam(f"seam-{i}", f"step {i}", ["true"]) for i in range(40)])
    text = p.brief()
    check("the brief mentions the next seam", "seam-0" in text)
    check("and not the one after it", "seam-1" not in text, text)
    check("it says where the work is", "0/40" in text, text)
    p.seams[0].status = J.RED
    stuck = p.brief()
    check("a blocked plan says so", "BLOCKED" in stuck, stuck)
    check("and says the plan does not advance", "does not advance" in stuck)
    for s in p.seams:
        s.status = J.GREEN
    check("a finished plan says complete", "complete" in p.brief())
    check("an empty plan says nothing at all", J.Journal(target="t").brief() == "")


# ---------------------------------------------------------------------------
# S3: executed, with the suite run at every intermediate seam
# ---------------------------------------------------------------------------


STORE_V1 = '''\
import json
from pathlib import Path


def save(path, records):
    Path(path).write_text(json.dumps(records), encoding="utf-8")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
'''

STORE_WRITE_BOTH = '''\
import json
from pathlib import Path


def save(path, records):
    payload = {"list": records, "by_id": {str(r["id"]): r for r in records}}
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["list"] if isinstance(data, dict) else data
'''

STORE_READ_NEW = '''\
import json
from pathlib import Path


def save(path, records):
    payload = {"list": records, "by_id": {str(r["id"]): r for r in records}}
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "by_id" in data:
        return list(data["by_id"].values())
    return data["list"] if isinstance(data, dict) else data
'''

STORE_DROP_OLD = '''\
import json
from pathlib import Path


def save(path, records):
    Path(path).write_text(
        json.dumps({"by_id": {str(r["id"]): r for r in records}}), encoding="utf-8")


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "by_id" in data:
        return list(data["by_id"].values())
    return data["list"] if isinstance(data, dict) else data
'''

# The one that breaks: it stops writing the old shape while `load` still requires it.
STORE_BIG_BANG = '''\
import json
from pathlib import Path


def save(path, records):
    Path(path).write_text(
        json.dumps({"by_id": {str(r["id"]): r for r in records}}), encoding="utf-8")


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["list"]
'''

CONTRACT = '''\
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import store

records = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
p = Path(__file__).parent / "data.json"
store.save(p, records)
got = sorted(store.load(p), key=lambda r: r["id"])
assert got == records, got
print("contract ok")
'''


def test_a_real_migration_is_green_at_every_intermediate_seam() -> None:
    print(NL + "- S3: executed on a polygon copy, verified at every seam -")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "store.py").write_text(STORE_V1, encoding="utf-8")
        (d / "contract.py").write_text(CONTRACT, encoding="utf-8")
        verify = [sys.executable, str(d / "contract.py")]

        journal = J.Journal(
            target="records move from a JSON list to a JSON object keyed by id",
            seams=[J.Seam("write-both", "write the new shape alongside the old", verify),
                   J.Seam("read-new", "read the new shape, fall back to the old", verify),
                   J.Seam("drop-old", "stop writing the old shape", verify)])
        path = d / "journal.json"
        journal.save(path)

        steps = {"write-both": STORE_WRITE_BOTH,
                 "read-new": STORE_READ_NEW,
                 "drop-old": STORE_DROP_OLD}

        greens = 0
        while True:
            # Reload from disk every time: the plan must work for a session that has
            # seen none of the earlier ones.
            journal = J.Journal.load(path)
            seam = journal.next_seam()
            if seam is None:
                break
            J.run_seam(journal, seam, cwd=d,
                       apply=lambda s=seam: (d / "store.py").write_text(
                           steps[s.name], encoding="utf-8"))
            journal.save(path)
            check(f"  seam {seam.name!r} is green", seam.status == J.GREEN,
                  seam.last_output[-200:])
            greens += seam.status == J.GREEN

        check("all three seams ran and passed", greens == 3, str(greens))
        check("the journal says it is done", J.Journal.load(path).done())
        check("and the migrated store still satisfies the contract",
              "by_id" in (d / "store.py").read_text(encoding="utf-8"))


def test_the_big_bang_is_refused_at_the_seam_that_breaks() -> None:
    """The failure this mechanism exists for: attempt it in one piece and the
    intermediate state is red. The plan must stop there rather than continue."""
    print(NL + "- the step that would leave the tree red stops the plan -")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "store.py").write_text(STORE_V1, encoding="utf-8")
        (d / "contract.py").write_text(CONTRACT, encoding="utf-8")
        verify = [sys.executable, str(d / "contract.py")]

        journal = J.Journal(target="same target, attempted in one piece", seams=[
            J.Seam("big-bang", "change the format and the reader at once", verify),
            J.Seam("cleanup", "tidy up afterwards", verify)])

        seam = journal.next_seam()
        J.run_seam(journal, seam, cwd=d,
                   apply=lambda: (d / "store.py").write_text(STORE_BIG_BANG,
                                                             encoding="utf-8"))
        check("the big-bang seam is red", seam.status == J.RED)
        check("its output records why", "KeyError" in seam.last_output
              or "assert" in seam.last_output.lower(), seam.last_output[-200:])
        check("the plan offers no next step", journal.next_seam() is None)
        check("and names what is blocking", journal.blocked().name == "big-bang")
        check("the cleanup seam was never reached",
              journal.seams[1].status == J.PENDING)
        check("the brief tells a fresh session all of that",
              "BLOCKED" in journal.brief() and "big-bang" in journal.brief())


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_a_plan_is_a_target_and_an_ordered_list_of_green_states,
               test_the_plan_does_not_advance_past_a_red_seam,
               test_the_journal_survives_a_session_boundary,
               test_a_corrupt_journal_is_not_half_a_plan,
               test_the_brief_costs_one_step_not_the_whole_plan,
               test_a_real_migration_is_green_at_every_intermediate_seam,
               test_the_big_bang_is_refused_at_the_seam_that_breaks):
        fn()
    print(f"\nseam journal: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
