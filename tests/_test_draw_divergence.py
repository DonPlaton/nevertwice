#!/usr/bin/env python3
"""The measurement behind the four `instrument.draw_divergence.*` claims, checked on its own.

`tools/draw_divergence.py` was split out of `tools/r1_verdict.py` on 2026-09-19 because a claim
goes stale when anything in its closure changes, and an argument parser sharing a file with a
measurement stales the number every time the CLI moves. The split is only worth something if the
module keeps the properties it was split out for, so this suite checks them directly:

1. **The number is re-derivable.** Running the module reproduces the committed artifact exactly -
   the same four figures the register publishes, not merely four figures.
2. **The register agrees with the artifact.** Four claims, four rates, one source.
3. **`rows_of` and `divergence` read nothing but the document handed to them.** `divergence_all`
   takes its root as an argument, so a crafted tree yields crafted numbers; if either function
   reached for anything else - a global, a file, the repository - that would not hold.
4. **The closure is the module alone.** That is the whole point of the split; if an import
   creeps back in, the four claims silently acquire a second way to go stale.
5. **The documented command leaves the tree clean.** A reproduction step that dirties
   `git status` is a reproduction step a reviewer has to take on faith. (`tools/produced_by.py`
   pays the same tax explicitly, for the same reason.)

    python tests/_test_draw_divergence.py
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

import _env_guard  # noqa: F401,E402  hermetic: this suite must not reach the live store

sys.path.insert(0, str(ROOT / "tools"))

import draw_divergence as dd  # noqa: E402
import produced_by as pb      # noqa: E402

ARTIFACT = ROOT / "research" / "results" / "draw_divergence.json"
MANIFEST = json.loads((ROOT / "research" / "evidence_manifest.json").read_text(encoding="utf-8"))

#: claim id suffix -> artifact key. The register and the artifact must name the same four runs.
CLAIMS = {
    "instrument.draw_divergence.explicit": "supersession_v1",
    "instrument.draw_divergence.explicit_implicit": "supersession_v1_implicit",
    "instrument.draw_divergence.baseline": "supersession_baseline_ef8120d",
    "instrument.draw_divergence.baseline_implicit": "supersession_baseline_ef8120d_implicit",
}

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def test_the_four_numbers_are_rederivable() -> None:
    print("\n- the committed artifact is what the module computes today -")
    computed = dd.divergence_all()
    stored = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    check("the artifact covers exactly the declared set",
          sorted(stored["artifacts"]) == sorted(dd.DIVERGENCE_SET),
          ", ".join(sorted(stored["artifacts"])))
    check("re-running the measurement reproduces the artifact exactly", computed == stored,
          "the artifact is out of date, or the measurement moved without a re-run")
    for key in dd.DIVERGENCE_SET:
        got = computed["artifacts"][key]
        check(f"{key}: {got['diverging']} of {got['cases']} diverge",
              got["cases"] > 0 and got["diverging"] == stored["artifacts"][key]["diverging"])


def test_the_register_and_the_artifact_agree() -> None:
    print("\n- four claims, four rates, one source -")
    stored = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in MANIFEST["claims"]}
    for cid, key in CLAIMS.items():
        claim = by_id.get(cid)
        if claim is None:
            check(f"{cid} is registered", False, "no such claim")
            continue
        d = stored["artifacts"][key]
        check(f"{cid} publishes the artifact's rate", claim["value"] == d["rate"],
              f"claim {claim['value']} vs artifact {d['rate']}")
        check(f"{cid} publishes the artifact's n", claim.get("n") == d["cases"],
              f"claim {claim.get('n')} vs artifact {d['cases']}")
        check(f"{cid} points at the artifact",
              claim["raw"] == "research/results/draw_divergence.json", claim["raw"])
        check(f"{cid} names the reproducing command",
              claim["command"] == "python tools/draw_divergence.py", claim["command"])


def test_rows_of_reads_only_the_document() -> None:
    print("\n- the readers take their whole input from the argument -")
    doc = {"arms": {"nevertwice": {"rows": [{"id": "a"}, {"id": "b"}]}}}
    check("rows come back for a named arm", dd.rows_of(doc, "nevertwice") == doc["arms"]["nevertwice"]["rows"])
    check("an absent arm is empty, not an error", dd.rows_of(doc, "nevertwice_run2") == [])
    check("an absent arms block is empty", dd.rows_of({}, "nevertwice") == [])
    check("a null rows list is empty",
          dd.rows_of({"arms": {"x": {"rows": None}}}, "x") == [])
    check("the returned list is a copy, not the document's own",
          dd.rows_of(doc, "nevertwice") is not doc["arms"]["nevertwice"]["rows"])


def test_divergence_counts_only_what_flips() -> None:
    print("\n- a crafted document gives the crafted answer -")
    a = [{"id": "1", "stale_returned": True}, {"id": "2"}, {"id": "3", "current_absent": True}]
    b = [{"id": "1", "stale_returned": False}, {"id": "2"}, {"id": "3", "current_absent": True}]
    doc = {"arms": {dd.DRAW_KEYS[0]: {"rows": a}, dd.DRAW_KEYS[1]: {"rows": b}}}
    got = dd.divergence(doc)
    check("only the flipped case is counted", got["diverging"] == 1, str(got))
    check("the shared cases are the denominator", got["cases"] == 3, str(got))
    check("the field that flipped is named", got["by_field"] == {"stale_returned": 1},
          str(got["by_field"]))
    check("a field that did not flip is not reported", "current_absent" not in got["by_field"])

    # A case present in one draw only cannot be a disagreement about that case.
    doc["arms"][dd.DRAW_KEYS[1]]["rows"] = b[:2]
    check("cases missing from one draw drop out of the denominator",
          dd.divergence(doc)["cases"] == 2)
    check("no shared case at all is zero, not a division error",
          dd.divergence({"arms": {}})["cases"] == 0)


def test_divergence_all_takes_its_tree_from_the_caller() -> None:
    print("\n- pointed at another tree, it measures that tree -")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "research" / "results").mkdir(parents=True)
        for key in dd.DIVERGENCE_SET:
            doc = {"arms": {dd.DRAW_KEYS[0]: {"rows": [{"id": "1", "current_demoted": True}]},
                            dd.DRAW_KEYS[1]: {"rows": [{"id": "1", "current_demoted": False}]}}}
            (tmp / "research" / "results" / f"{key}.json").write_text(
                json.dumps(doc), encoding="utf-8")
        got = dd.divergence_all(root=tmp)
    check("every declared artifact was read from the given root",
          sorted(got["artifacts"]) == sorted(dd.DIVERGENCE_SET))
    check("and the numbers are the crafted ones, not the repository's",
          all(d["cases"] == 1 and d["diverging"] == 1 for d in got["artifacts"].values()),
          str(got["artifacts"]))
    check("the record says what produced it",
          got["measured_by"] == "python tools/draw_divergence.py")


def test_the_closure_is_the_module_alone() -> None:
    print("\n- nothing else can stale these four claims -")
    deps = pb.closure("python tools/draw_divergence.py")
    check("the closure is exactly this module", deps == ["tools/draw_divergence.py"],
          ", ".join(deps))
    by_id = {c["id"]: c for c in MANIFEST["claims"]}
    for cid in CLAIMS:
        stored = by_id.get(cid, {}).get("produced_by")
        check(f"{cid} stores that same closure", stored == ["tools/draw_divergence.py"],
              str(stored))
    src = (ROOT / "tools" / "draw_divergence.py").read_text(encoding="utf-8")
    check("the measurement still carries no argument parser", "argparse" not in src,
          "a parser is back in the closure: a CLI edit will stale the four claims again")


def test_the_documented_command_leaves_the_tree_clean() -> None:
    """Reproduction that dirties `git status` is reproduction a reviewer has to take on faith."""
    print("\n- running the documented command changes nothing -")
    before = ARTIFACT.read_bytes()
    run = subprocess.run([sys.executable, "tools/draw_divergence.py"], cwd=str(ROOT),
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=300)
    after = ARTIFACT.read_bytes()
    if after != before:
        ARTIFACT.write_bytes(before)          # never leave the tree dirty on a red run
    check("the command succeeds", run.returncode == 0, (run.stderr or "").strip()[-200:])
    check("and it rewrites the artifact byte for byte", after == before,
          "the bytes changed although the numbers did not - line endings, key order or spacing")
    check("the artifact is stored with newline-separated lines", b"\r\n" not in before,
          "the committed artifact itself carries CRLF")


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_the_four_numbers_are_rederivable,
               test_the_register_and_the_artifact_agree,
               test_rows_of_reads_only_the_document,
               test_divergence_counts_only_what_flips,
               test_divergence_all_takes_its_tree_from_the_caller,
               test_the_closure_is_the_module_alone,
               test_the_documented_command_leaves_the_tree_clean):
        fn()
    print(f"\ndraw divergence: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
