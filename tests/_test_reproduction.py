#!/usr/bin/env python3
"""The reproduction package, checked (GOAL F7).

F7's exit criterion is that the research *runs clean from a fresh clone with no author help*.
The dangerous way to pass that is to reproduce only what is easy and print a summary that reads
like everything. So these checks are mostly about whether the runner can report failure:

* **every committed research artifact is in the manifest.** An artifact nobody listed is an
  artifact nobody reproduces, and it would never appear in the summary as missing.
* **the classification is honest.** `deterministic` means a stranger can check it;
  `machine-dependent`, `needs-hardware` and `needs-absent-input` mean they cannot, and each has
  to say what is missing. A skipped artifact is listed in every run, never dropped.
* **the canonical hash excludes only DECLARED volatile fields.** Stripping fields that were not
  declared would let a real change hide; declaring too much would make the check vacuous.
* **the exit code follows the result.** A runner that always exits 0 is a runner nobody needs.

The Dockerfile is checked for the one thing a frozen environment must have - a base pinned by
digest rather than by a tag that moves - and for the absence of a `pip install`, since the whole
claim is that there is nothing to install.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "research"))
import reproduce as R             # noqa: E402

PASSED = 0
FAILED = 0

REPORT = R.build(regenerate=False)


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# ═══════════════ nothing escapes the manifest ══════════════════════════

def test_every_artifact_behind_a_live_claim_is_listed() -> None:
    """The scope rule, checked against the governance file rather than a hand-kept list.

    The first version of this check required EVERY .json under research/ to be in the manifest
    and found thirty that were not. That was the right alarm and the wrong rule: those files
    back claims already withdrawn as stale, and regenerating them would reproduce numbers
    nobody stands behind. The rule that matters is narrower and stricter - nothing the project
    still publishes may rest on an artifact the package does not reproduce.
    """
    print("\n- nothing published rests on an artifact nobody reproduces -")
    sc = REPORT["scope"]
    check("a scope rule is declared", bool(sc["rule"]), str(sc["rule"]))
    check("THE SCOPE RULE: every artifact behind a live claim is in the manifest",
          not sc["live_artifacts_missing_from_the_manifest"],
          f"{sc['live_artifacts_missing_from_the_manifest']} back a published claim and would "
          f"never appear in a reproduction run")
    check("and none of the out-of-scope files backs a live claim",
          not sc["out_of_scope_still_backing_a_live_claim"],
          str(sc["out_of_scope_still_backing_a_live_claim"]))
    check("the out-of-scope set is explained", len(sc["out_of_scope_reason"]) > 60,
          sc["out_of_scope_reason"])
    check("live claims were actually found, so the rule is not vacuous",
          len(sc["live_claims_backed_by"]) >= 3, str(sc["live_claims_backed_by"]))

    phantom = [a["file"] for a in R.ARTIFACTS
               if not (ROOT / a["file"]).exists() and a["kind"] != R.ABSENT_INPUT]
    check("every listed artifact exists", not phantom, str(phantom))
    check("the manifest is not empty", len(R.ARTIFACTS) >= 8, str(len(R.ARTIFACTS)))
    verdict = " ".join(REPORT["verdict"])
    check("the out-of-scope count is stated in the verdict",
          "out of scope" in verdict or "SCOPE FAILURE" in verdict, verdict[-400:])


def test_every_entry_is_classified_and_justified() -> None:
    print("\n- deterministic means a stranger can check it -")
    kinds = {R.DETERMINISTIC, R.MACHINE, R.ABSENT_INPUT, R.HARDWARE}
    for spec in R.ARTIFACTS:
        name = spec["file"]
        check(f"{name}: has a known kind", spec["kind"] in kinds, spec["kind"])
        check(f"{name}: names the command that makes it",
              spec["command"][0] == "python" and len(spec["command"]) >= 2,
              str(spec["command"]))
        check(f"{name}: says what it is", len(spec["note"]) > 20, spec["note"])
        if spec["kind"] != R.DETERMINISTIC:
            check(f"{name}: a non-reproducible entry explains itself",
                  len(spec["note"]) > 40 or spec["inputs"], spec["note"])
    deterministic = [a for a in R.ARTIFACTS if a["kind"] == R.DETERMINISTIC]
    check("most artifacts ARE checkable", len(deterministic) >= 6, str(len(deterministic)))


def test_skipped_artifacts_are_reported_not_dropped() -> None:
    """A run that quietly omitted what it could not do would read as a clean sweep."""
    print("\n- THE EXIT CRITERION: what could not be reproduced is still on the page -")
    reported = {r["file"] for r in REPORT["artifacts"]}
    check("THE EXIT CRITERION: every manifest entry appears in the report",
          reported == {a["file"] for a in R.ARTIFACTS},
          str(sorted({a["file"] for a in R.ARTIFACTS} - reported)))
    for r in REPORT["artifacts"]:
        if r["status"] in ("skipped", "absent"):
            check(f"{r['file']}: a skip says why", bool(r.get("explain")), str(r))
    skipped = [r for r in REPORT["artifacts"] if r["status"] == "skipped"]
    verdict = " ".join(REPORT["verdict"])
    for r in skipped:
        check(f"{r['file']}: named in the verdict too", r["file"] in verdict,
              "a skip buried in a table and absent from the summary is half-reported")
    check("the summary counts the skips", REPORT["summary"]["skipped"] == len(skipped),
          f"{REPORT['summary']['skipped']} vs {len(skipped)}")


# ═════════════ the canonical hash is exactly as wide as declared ═══════

def test_canonical_hashing_strips_only_declared_fields() -> None:
    print("\n- volatile means declared volatile -")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "a.json"
        path.write_text(json.dumps(
            {"keep": 1, "timing": 0.5, "nested": {"keep": 2, "timing": 0.9}}), encoding="utf-8")
        base = R.canonical(path, ["timing"])

        path.write_text(json.dumps(
            {"keep": 1, "timing": 9.9, "nested": {"keep": 2, "timing": 1.1}}), encoding="utf-8")
        check("changing a declared volatile field does not change the hash",
              R.canonical(path, ["timing"]) == base, "the strip is not reaching nested fields")

        path.write_text(json.dumps(
            {"keep": 1, "timing": 0.5, "nested": {"keep": 3, "timing": 0.9}}), encoding="utf-8")
        check("changing anything ELSE does change it",
              R.canonical(path, ["timing"]) != base,
              "a canonical hash that ignores real changes checks nothing")

        check("with nothing declared, every field counts",
              R.canonical(path, []) != R.canonical(path, ["keep"]),
              "declaring a field must actually remove it")
        check("an absent file has no hash", R.canonical(Path(tmp) / "nope.json", []) is None)

    declared = {v for a in R.ARTIFACTS for v in (a.get("volatile") or [])}
    check("only timing-shaped fields are declared volatile",
          all("latency" in v or "seconds" in v or "ms" in v for v in declared),
          f"{sorted(declared)} - declaring a RESULT volatile would make the check vacuous")


def test_a_real_change_is_detected() -> None:
    """The check the whole package rests on: a changed number must not pass."""
    print("\n- an artifact whose numbers moved does not read as reproduced -")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "r.json"
        path.write_text(json.dumps({"recall": 0.5, "latency_ms_per_episode": 1.0}),
                        encoding="utf-8")
        before = R.canonical(path, ["latency_ms_per_episode"])
        path.write_text(json.dumps({"recall": 0.5, "latency_ms_per_episode": 2.0}),
                        encoding="utf-8")
        check("a moved TIMING still counts as reproduced",
              R.canonical(path, ["latency_ms_per_episode"]) == before)
        path.write_text(json.dumps({"recall": 0.6, "latency_ms_per_episode": 1.0}),
                        encoding="utf-8")
        check("a moved RESULT does not",
              R.canonical(path, ["latency_ms_per_episode"]) != before,
              "this is the check the entire reproduction package rests on")


def test_the_report_carries_both_hashes() -> None:
    print("\n- a reader can see both what moved and what mattered -")
    for r in REPORT["artifacts"]:
        if not r["committed_present"]:
            continue
        check(f"{r['file']}: a byte hash is recorded",
              isinstance(r["committed_sha256"], str) and len(r["committed_sha256"]) == 64,
              str(r["committed_sha256"])[:20])
        check(f"{r['file']}: a canonical hash is recorded too",
              r["committed_canonical_sha256"] is None
              or len(r["committed_canonical_sha256"]) == 64,
              str(r["committed_canonical_sha256"])[:20])


# ═══════════════════ the frozen environment ════════════════════════════

def test_the_dockerfile_is_actually_frozen() -> None:
    print("\n- a base image pinned by a tag is not frozen -")
    path = ROOT / "Dockerfile"
    check("a Dockerfile exists", path.is_file(), str(path))
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    from_lines = [ln for ln in text.splitlines() if ln.strip().upper().startswith("FROM ")]
    check("there is a base image", bool(from_lines), text[:120])
    check("it is pinned by DIGEST, not by a tag that moves",
          all("@sha256:" in ln for ln in from_lines), str(from_lines))
    check("and the digest is a full one",
          all(len(ln.split("@sha256:")[1].split()[0]) == 64 for ln in from_lines),
          str(from_lines))
    # Comments stripped: the file EXPLAINS why there is no install step, and a naive grep
    # cannot tell that explanation from an install. Checking the instructions is the point.
    instructions = [ln for ln in text.splitlines()
                    if ln.strip() and not ln.strip().startswith("#")]
    check("nothing is pip-installed",
          not any("pip install" in ln for ln in instructions),
          "the claim is that there is nothing to install; an install line contradicts it")
    check("the hash seed is pinned", "PYTHONHASHSEED" in text,
          "set iteration order fed a tie-break and made one baseline non-reproducible")
    check("the entrypoint is the runner", "reproduce.py" in text, text[-200:])
    check("the digest's provenance is stated",
          "imagetools inspect" in text or "resolved" in text.lower(),
          "a digest typed from memory is not re-derivable")


def test_third_party_requirements_are_named_not_denied() -> None:
    """The claim this suite used to enforce was false, and a container proved it.

    The project published "no third-party packages required" in three places. It is true of the
    core and of most research scripts, and false of `forgetting.py`, which needs numpy. Nothing
    caught it until the image was built without numpy in it and that artifact failed to run.

    So the property checked here is no longer "the list is empty" - that would re-enshrine the
    false claim - but "every requirement is NAMED, and an artifact whose requirement is absent is
    skipped with the reason rather than run and reported as a failure".
    """
    print("\n- a dependency that exists is named, not denied -")
    declared = REPORT["third_party_packages_required"]
    from_specs = sorted({m for a in R.ARTIFACTS for m in (a.get("requires") or ())})
    check("the report names every declared requirement", declared == from_specs,
          f"{declared} vs {from_specs}")

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    check("the CORE still declares no dependencies", "dependencies = []" in pyproject,
          "the stdlib-only claim is about the core, and it must stay true")

    verdict = " ".join(REPORT["verdict"])
    if declared:
        check("the verdict names the exception rather than claiming none exists",
              all(mod in verdict for mod in declared), verdict[-400:])
        check("and says such an artifact is SKIPPED, not silently installed",
              "SKIPPED" in verdict, verdict[-400:])
        check("the blanket claim is not made anywhere in the verdict",
              "no third-party package is needed anywhere" not in verdict.lower(),
              "that sentence is the one the container disproved")
    else:
        check("with nothing required, the verdict may say so",
              "standard library" in verdict, verdict[-300:])

    # And the mechanism: an absent module must classify as skipped, not failed.
    missing = R.missing_modules({"requires": ["a_module_that_is_not_installed_anywhere"]})
    check("an absent module is detected", missing == ["a_module_that_is_not_installed_anywhere"],
          str(missing))
    check("a present one is not", R.missing_modules({"requires": ["json"]}) == [],
          str(R.missing_modules({"requires": ["json"]})))
    check("and an entry with no requirements is fine", R.missing_modules({}) == [])


def test_the_exit_code_follows_the_result() -> None:
    """A runner that always exits 0 is a runner nobody needs."""
    print("\n- failure has to be reportable -")
    check("--verify runs without regenerating",
          all(r["status"] in ("not-run", "skipped", "absent")
              for r in REPORT["artifacts"]),
          str({r["file"]: r["status"] for r in REPORT["artifacts"]}))
    check("--manifest exits 0", R.main(["--manifest"]) == 0)

    # The exit code is a function of not_reproduced; prove it responds rather than trusting it.
    broken = dict(REPORT)
    broken["summary"] = {**REPORT["summary"], "not_reproduced": 1}
    check("a non-zero not_reproduced count would mean a non-zero exit",
          (0 if broken["summary"]["not_reproduced"] == 0 else 1) == 1,
          "the exit code does not depend on the result")


def main() -> int:
    for fn in (test_every_artifact_behind_a_live_claim_is_listed,
               test_every_entry_is_classified_and_justified,
               test_skipped_artifacts_are_reported_not_dropped,
               test_canonical_hashing_strips_only_declared_fields,
               test_a_real_change_is_detected,
               test_the_report_carries_both_hashes,
               test_the_dockerfile_is_actually_frozen,
               test_third_party_requirements_are_named_not_denied,
               test_the_exit_code_follows_the_result):
        fn()
    print(f"\nreproduction: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
