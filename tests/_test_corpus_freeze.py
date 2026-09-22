#!/usr/bin/env python3
"""The development corpus is frozen, and the held-out corpus is sealed until Phase V.

`GOAL-SHIP.md` §0.3: the eight repositories were used both to *find* the defect classes
and to *score* the gates, so every number measured on them is in-sample. They stay --
tuning on a development set is what a development set is for -- but they stop being the
thing any published claim rests on.

The failure this file exists to prevent is not malice. It is a measurement script that
quietly widens its glob, reads the held-out clones during development, and turns a
held-out corpus into a second in-sample one without anybody noticing. Two locks:

* **runtime** -- `corpora.heldout_repos()` raises while the seal is closed, so a script
  that reaches for the held-out corpus early dies instead of returning data;
* **static** -- no module outside a named allowlist may spell the held-out path itself,
  so the runtime lock cannot be walked around with a `Path(...)` literal.

Neither lock needs the corpus on disk, which is the point: this suite is green on a
fresh clone with no polygon at all.

Run:  python tests/_test_corpus_freeze.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

LAB = ROOT / "research" / "invariants_lab"
sys.path.insert(0, str(LAB))
import corpora as C  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = "  [" + detail + "]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# ---------------------------------------------------------------------------
# the freeze record itself
# ---------------------------------------------------------------------------

def test_the_development_set_is_eight_named_repositories() -> None:
    print("\n- the frozen development set -")
    freeze = C.dev_freeze()
    check("the freeze names exactly eight repositories", len(freeze["repos"]) == 8,
          str(len(freeze["repos"])))
    check("every repository is pinned to a full 40-character commit",
          all(len(r["head"]) == 40 and all(ch in "0123456789abcdef" for ch in r["head"])
              for r in freeze["repos"]),
          str([r["head"] for r in freeze["repos"]])[:120])
    check("every repository names a licence and a slug",
          all(r.get("licence") and r.get("slug") for r in freeze["repos"]))
    check("the freeze records 859 confirmed positives",
          freeze["positives"] == 859, str(freeze.get("positives")))
    check("in 345 independent source commits",
          freeze["source_commits"] == 345, str(freeze.get("source_commits")))


def test_the_freeze_agrees_with_the_answer_key_it_describes() -> None:
    """A freeze that drifts from `mutants.json` is a freeze of nothing."""
    print("\n- the freeze against the answer key -")
    mutants = json.loads((LAB / "mutants.json").read_text(encoding="utf-8"))
    confirmed = [m for m in mutants["mutants"] if m["confirmed"]]
    freeze = C.dev_freeze()
    check("the positive count is the answer key's confirmed count",
          freeze["positives"] == len(confirmed),
          str(freeze["positives"]) + " vs " + str(len(confirmed)))
    check("the source-commit count is the answer key's distinct shas",
          freeze["source_commits"] == len({m["sha"] for m in confirmed}))
    key_repos = {m["repo"] for m in confirmed}
    frozen_dirs = {r["dir"] for r in freeze["repos"]}
    check("every repository carrying a positive is in the freeze",
          key_repos <= frozen_dirs, str(key_repos - frozen_dirs))
    per_repo = {r["dir"]: r for r in freeze["repos"]}
    mismatched = [
        r for r in sorted(key_repos)
        if per_repo[r]["positives"] != sum(1 for m in confirmed if m["repo"] == r)
    ]
    check("and its per-repository positive count matches", not mismatched, str(mismatched))


# ---------------------------------------------------------------------------
# the seal
# ---------------------------------------------------------------------------

def test_the_heldout_corpus_is_sealed_until_phase_v() -> None:
    print("\n- the seal, at runtime -")
    seal = C.heldout_seal()
    check("the seal exists and records its own phase", bool(seal.get("phase")))
    if seal["open"]:
        check("an open seal names the code freeze it was opened against",
              bool(seal.get("frozen_code")) and bool(seal.get("opened_at")))
        return
    raised = None
    try:
        C.heldout_repos()
    except C.HeldOutSealed as exc:
        raised = exc
    check("a closed seal makes heldout_repos() raise, not return", raised is not None)
    check("and the exception says which phase opens it",
          raised is not None and "Phase V" in str(raised), str(raised)[:160])


def test_the_dev_corpus_is_readable_whatever_the_seal_says() -> None:
    """The seal must not make development impossible; it only guards the held-out set."""
    print("\n- the dev corpus is never sealed -")
    try:
        repos = C.dev_repos()
    except C.HeldOutSealed:
        check("dev_repos() is not blocked by the held-out seal", False)
        return
    check("dev_repos() returns without raising", True)
    if not repos:
        print("       (polygon absent on this machine -- path check only)")
    else:
        check("and every path it returns is under the dev root",
              all(C.DEV_ROOT in p.parents for p in repos))
    check("the two roots are different directories", C.DEV_ROOT != C.HELDOUT_ROOT)


# ---------------------------------------------------------------------------
# the static lock: nobody spells the held-out path themselves
# ---------------------------------------------------------------------------

def test_no_module_reaches_around_the_seal() -> None:
    print("\n- the seal, statically -")
    allowed = set(C.HELDOUT_READERS)
    offenders, _lab = [], 0
    for path in sorted(LAB.glob("*.py")):
        if path.name in allowed:
            continue
        _lab += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in ("corpus_heldout", "HELDOUT_ROOT", "heldout_repos"):
            if needle in text:
                offenders.append(path.name + ":" + needle)
    #: A sweep that discovers nothing reports no offenders, which reads exactly like a
    #: sweep that found none. Pinned so the discovery has to keep working. Same class as
    #: `1ef491c`; found by a third signature over offender checks whose loop iterates a
    #: FILE DISCOVERY and whose size nothing asserts, 2026-09-22.
    #: Counted INSIDE the loop, after the allowlist skip: a second, independent call to the
    #: same glob would be a copy of the intention, green while the loop looked at nothing.
    #: Measured by the auditing session, 2026-09-22.
    check(f"the lab has modules to police ({_lab})", _lab >= 30, str(_lab))
    check("no lab module outside the allowlist names the held-out corpus",
          not offenders, str(offenders))
    check("the allowlist names corpora.py, which owns the seal",
          "corpora.py" in allowed, str(sorted(allowed)))


def test_nothing_refers_to_an_unnamed_corpus() -> None:
    """`GOAL-SHIP.md` F0: every reference distinguishes dev from heldout *by name*.

    A helper called `corpus_repos()` reads whichever corpus it was pointed at, and a
    reader of the call site cannot tell which. That ambiguity is exactly what turns a
    held-out set into a second development set, so the ambiguous names are removed
    rather than deprecated.
    """
    print("\n- no bare 'the corpus' left in the lab -")
    stale = []
    for path in sorted(LAB.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith(("#", "*")) or '"""' in line:
                continue
            for needle in ("corpus_repos", "CORPUS_ROOT"):
                if needle in line and "dev_" + needle not in line:
                    stale.append(path.name + ":" + str(line_no) + ":" + needle)
    check("no lab module calls an unqualified corpus_repos/CORPUS_ROOT",
          not stale, str(stale[:8]))


def test_every_harness_takes_a_corpus_rather_than_assuming_one() -> None:
    """H1: a harness edited *after* the freeze is a harness that broke the freeze.

    Phase V measures the same code on a different corpus. If a measurement script has
    `dev_repos()` written into it, running it on the held-out corpus means editing it --
    after the hashes were recorded, which is exactly what freezing was for. So the corpus
    becomes an argument **before** the freeze, and this test is what stops it drifting
    back.
    """
    print("\n- the corpus is an argument, not a constant -")
    harnesses = ["measure_blast_radius.py", "measure_abstention.py", "measure_surface.py",
                 "measure_ratchet.py", "measure_scale.py", "measure_together.py",
                 "audit_axes.py", "mutate.py", "verify_mutants.py", "corpus_census.py",
                 "mine_quadratics.py", "facade_shapes.py"]
    import subprocess
    missing_flag, hardcoded, could_not_start = [], [], []
    for name in harnesses:
        path = LAB / name
        if not path.exists():
            missing_flag.append(name + " (absent)")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "repos_for(" not in text:
            hardcoded.append(name)
        # Asked of the parser rather than of the source, because the flag is defined once
        # in `corpora.add_corpus_argument` and a grep for the literal would pass for a
        # harness that merely mentions it in a docstring.
        try:
            proc = subprocess.run([sys.executable, str(path), "--help"],
                                  capture_output=True, text=True, timeout=120,
                                  cwd=str(ROOT), check=False)
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            missing_flag.append(name + " (" + type(exc).__name__ + ")")
            continue
        if "--corpus" not in (proc.stdout or ""):
            #: "refused the flag" and "could not start" are different answers, and asking one
            #: question of both is how this check read as a corpus defect on a machine without
            #: the research extras: the harness dies importing `numpy`/`networkx`/`scipy`
            #: before argparse ever prints, so stdout carries no `--corpus` and the harness is
            #: reported as missing a flag it actually declares. Measured 2026-09-22 by
            #: reproducing the core CI matrix locally with those imports blocked - eight of
            #: eight harnesses "missing the flag", none of them missing it.
            err = (proc.stderr or "")
            if "ModuleNotFoundError" in err or "ImportError" in err:
                could_not_start.append(f"{name}: {err.strip().splitlines()[-1][:60]}")
            else:
                missing_flag.append(name)
    check("every harness resolves its corpus through corpora.repos_for()",
          not hardcoded, str(hardcoded))
    #: The population, so that "no harness is missing the flag" cannot be true because none of
    #: them started. A run where every harness dies is a dependency finding, not a corpus one.
    started = len(harnesses) - len(could_not_start)
    check(f"enough harnesses started for the question to mean anything ({started} of "
          f"{len(harnesses)})", started >= 1,
          "; ".join(could_not_start[:3]))
    check("and every harness that STARTED accepts --corpus",
          not missing_flag, str(missing_flag))
    if could_not_start:
        print(f"    ({len(could_not_start)} harness(es) could not start - a research extra is "
              f"absent; that is the dependency tier's question, not this one)")


def test_no_harness_dies_printing_its_own_help() -> None:
    """`corpusio.progress`'s lesson, applied to `--help`.

    This project already lost an MCP server to a cp1251 console. `measure_ratchet` and
    `measure_scale` carried a `U+2264` in their module docstrings, so `--help` raised
    `UnicodeEncodeError` on the owner's default console -- a research script nobody can
    ask what its arguments are.
    """
    print("\n- every harness docstring survives a cp1251 console -")
    offenders = []
    for path in sorted(LAB.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            doc = __import__("ast").get_docstring(__import__("ast").parse(text)) or ""
        except SyntaxError:  # pragma: no cover - caught elsewhere
            continue
        try:
            doc.encode("cp1251")
        except UnicodeEncodeError as exc:
            offenders.append(path.name + ":" + hex(ord(doc[exc.start])))
    check("no module docstring contains a character cp1251 cannot encode",
          not offenders, str(offenders))


def test_the_artifact_of_one_corpus_cannot_overwrite_the_other() -> None:
    """Two corpora, two artifacts. One filename for both is one measurement lost."""
    print("\n- artifacts are named per corpus -")
    check("census paths differ", C.census_path("dev") != C.census_path("heldout"))
    check("mutant paths differ", C.mutants_path("dev") != C.mutants_path("heldout"))
    check("result paths differ",
          C.artifact_path("x.json", "dev") != C.artifact_path("x.json", "heldout"))
    check("the dev paths are the ones already committed",
          C.census_path("dev").name == "corpus_census.json"
          and C.mutants_path("dev").name == "mutants.json",
          str((C.census_path("dev").name, C.mutants_path("dev").name)))
    raised = None
    try:
        C.repos_for("nonsense")
    except ValueError as exc:
        raised = exc
    check("an unknown corpus name raises rather than defaulting", raised is not None)


def test_the_frozen_code_still_hashes_to_what_h1_recorded() -> None:
    """The freeze itself. Red the moment a frozen module changes, which is the point."""
    print("\n- the H1 code freeze -")
    seal = C.heldout_seal()
    frozen = seal.get("frozen_code") or {}
    if not frozen:
        print("       (no freeze recorded yet -- H1 has not run)")
        check("an unfrozen seal is a closed seal", not seal.get("open"),
              "the seal is open with no frozen hashes")
        return
    check("the freeze names at least the mechanisms and the harnesses",
          len(frozen) >= 15, str(len(frozen)))
    drifted = [name for name, digest in sorted(frozen.items())
               if C.file_digest(LAB / name) != digest]
    check("every frozen file still hashes to its recorded digest",
          not drifted, str(drifted))


def test_the_allowlist_cannot_be_emptied_by_accident() -> None:
    """A phase-V script joins the allowlist by being written, not by the glob widening."""
    print("\n- the allowlist is a list, not a pattern -")
    check("HELDOUT_READERS is a concrete tuple of file names",
          isinstance(C.HELDOUT_READERS, tuple)
          and all(n.endswith(".py") for n in C.HELDOUT_READERS),
          repr(C.HELDOUT_READERS))
    check("every name on it is a file that exists",
          all((LAB / n).exists() for n in C.HELDOUT_READERS),
          str([n for n in C.HELDOUT_READERS if not (LAB / n).exists()]))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_the_development_set_is_eight_named_repositories,
               test_the_freeze_agrees_with_the_answer_key_it_describes,
               test_the_heldout_corpus_is_sealed_until_phase_v,
               test_the_dev_corpus_is_readable_whatever_the_seal_says,
               test_no_module_reaches_around_the_seal,
               test_nothing_refers_to_an_unnamed_corpus,
               test_every_harness_takes_a_corpus_rather_than_assuming_one,
               test_no_harness_dies_printing_its_own_help,
               test_the_artifact_of_one_corpus_cannot_overwrite_the_other,
               test_the_frozen_code_still_hashes_to_what_h1_recorded,
               test_the_allowlist_cannot_be_emptied_by_accident):
        fn()
    print("\ncorpus freeze and held-out seal: " + str(PASSED) + " passed, "
          + str(FAILED) + " failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
