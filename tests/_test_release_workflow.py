#!/usr/bin/env python3
"""The release workflow cannot publish by accident, and cannot publish something else.

Two failure modes matter here and neither shows up in a normal test run.

**Publishing when nobody asked.** A dry run, a re-run of a stale workflow, a branch push -
none of these may reach PyPI or cut a GitHub release. Every publishing job is therefore
gated on a pushed tag, and this suite fails if a gate is dropped.

**Publishing something other than what was verified.** The wheel that is tested and the
wheel that is uploaded have to be the same bytes. That means exactly one job builds, and
every later job downloads that artifact instead of rebuilding. This project has already
shipped a broken first touch once because the built wheel and the uploaded artifact were
not the same test.

The workflow is parsed as text rather than YAML so the suite stays standard-library only,
like the rest of them.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release.yml"
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")

PUBLISHING_JOBS = ("github-release", "pypi")
TAG_GATE = ("github.event_name == 'push' && "
            "startsWith(github.ref, 'refs/tags/v')")

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def jobs() -> dict[str, str]:
    """Split the file into job blocks by their two-space-indented names."""
    body = WORKFLOW.split("\njobs:\n", 1)[1]
    starts = [(m.start(), m.group(1))
              for m in re.finditer(r"^  ([A-Za-z][\w-]*):$", body, re.M)]
    out = {}
    for i, (pos, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(body)
        out[name] = body[pos:end]
    return out


JOBS = jobs()


def test_the_workflow_is_tag_triggered() -> None:
    print("\n- the workflow triggers on a tag, and can be dry-run -")
    check("it triggers on version tags", 'tags: ["v*"]' in WORKFLOW)
    check("it can be dispatched manually for a dry run",
          "workflow_dispatch:" in WORKFLOW)
    check("the default permission is read-only",
          re.search(r"^permissions:\n  contents: read$", WORKFLOW, re.M) is not None)


def test_publishing_is_reachable_only_from_a_tag() -> None:
    """The dry run has to be safe, or nobody will use it and it will rot."""
    print("\n- no publishing job can fire without a pushed tag -")
    for name in PUBLISHING_JOBS:
        check(f"the {name} job exists", name in JOBS)
        if name in JOBS:
            check(f"{name} is gated on a pushed tag", TAG_GATE in JOBS[name],
                  "the `if:` gate is missing or was changed")

    for name in PUBLISHING_JOBS:
        if name in JOBS:
            check(f"{name} runs only after verify", "verify" in JOBS[name])


def test_what_is_published_is_what_was_verified() -> None:
    print("\n- one build, and every later job consumes that artifact -")
    builders = [n for n, body in JOBS.items() if "python -m build" in body]
    check("exactly one job builds the distribution", builders == ["build"],
          ", ".join(builders) or "none")

    for name in ("verify", "provenance", "github-release", "pypi"):
        if name in JOBS:
            check(f"{name} downloads the built artifact",
                  "actions/download-artifact" in JOBS[name])
            check(f"{name} does not rebuild", "python -m build" not in JOBS[name])


def test_the_release_is_verified_before_it_is_published() -> None:
    print("\n- the artifact is installed and exercised before release -")
    verify = JOBS.get("verify", "")
    check("verify installs into a clean virtualenv", "python -m venv" in verify)
    check("verify covers all three operating systems",
          all(os_name in verify for os_name in
              ("ubuntu-latest", "windows-latest", "macos-latest")))
    check("verify installs the wheel and the sdist",
          "wheel, sdist" in verify or ("wheel" in verify and "sdist" in verify))
    check("verify checks the installed version against the built one",
          "nevertwice.__version__" in verify)
    check("verify exercises every console entry point",
          "tools/console_smoke.py" in verify)
    check("verify re-checks the checksums", "sha256sum -c" in verify)


def test_the_supply_chain_artifacts_are_produced() -> None:
    print("\n- SBOM, checksums and provenance are part of the build -")
    build = JOBS.get("build", "")
    check("an SBOM is generated from the built wheel",
          "tools/make_sbom.py" in build)
    check("checksums are generated for every artifact", "sha256sum" in build)
    check("the version contract runs before the build",
          build.find("check_version.py") < build.find("python -m build"))
    check("the version contract runs again against the built artifacts",
          "check_version.py --dist dist" in build)
    check("a tag build demands the release-strict contract",
          "check_version.py --release" in build)

    provenance = JOBS.get("provenance", "")
    check("build provenance is attested", "attest-build-provenance" in provenance)
    check("attestation covers both artifacts",
          "dist/*.whl" in provenance and "dist/*.tar.gz" in provenance)


def test_pypi_upload_needs_no_secret_and_stays_gated() -> None:
    """Trusted Publishing means no API token exists to leak - and the environment gate
    keeps the upload behind a setting only the owner can make."""
    print("\n- PyPI upload uses Trusted Publishing behind an environment gate -")
    pypi = JOBS.get("pypi", "")
    check("the upload uses the PyPA publishing action",
          "pypa/gh-action-pypi-publish" in pypi)
    check("it requests an OIDC token", "id-token: write" in pypi)
    check("it runs in the `pypi` environment", "name: pypi" in pypi)
    check("no password or API token is referenced",
          "password:" not in pypi and "PYPI_API_TOKEN" not in pypi)
    check("non-distribution files are stripped before upload",
          "! -name '*.whl'" in pypi)
    # The rehearsal the release process is meant to use: a pre-release tag runs the
    # whole path and stops before PyPI, so nothing irreversible happens on a trial run.
    check("a pre-release tag never reaches PyPI",
          "needs.build.outputs.prerelease == 'false'" in pypi)


def test_every_action_is_pinned_to_a_commit() -> None:
    """A moving tag is a supply-chain hole: `@v4` is whatever that tag points at today."""
    print("\n- every action is pinned to a full commit sha -")
    unpinned = [ref for ref in re.findall(r"uses: (\S+)", WORKFLOW)
                if not re.search(r"@[0-9a-f]{40}$", ref)]
    check("no action is used by tag or branch", not unpinned, ", ".join(unpinned))

    uncommented = [line.strip() for line in WORKFLOW.splitlines()
                   if "uses:" in line and "@" in line and "#" not in line]
    check("every pin records which version the sha is", not uncommented,
          "; ".join(uncommented[:3]))


def test_only_the_release_job_may_write() -> None:
    print("\n- write permission is scoped to the one job that needs it -")
    writers = [n for n, body in JOBS.items() if "contents: write" in body]
    check("only the github-release job may write to the repository",
          writers == ["github-release"], ", ".join(writers) or "none")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\nrelease workflow: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
