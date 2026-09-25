"""Pytest entry point for Nevertwice's standalone self-check suites.

The historical suites execute assertions at module scope and intentionally remain
directly runnable by CI. Running each in a subprocess preserves that isolation while
giving contributors one conventional ``python -m pytest`` command.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SUITES = tuple(sorted((ROOT / "tests").glob("_test_*.py"))) + tuple(
    sorted((ROOT / "tests" / "research").glob("_test_*.py"))
)

#: Read from pyproject, not listed here. The `research` extra IS the definition of what that
#: tier may import; a copy of the list in this file is a second definition that can disagree
#: with the first, and the one deciding a skip must be the one `pip install -e ".[research]"`
#: actually installs.
_REQ = re.compile(r"^[A-Za-z0-9._-]+")
_MISSING = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")


def _research_distributions() -> frozenset[str]:
    """Import names the `research` extra brings in, as pyproject declares them."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return frozenset(_REQ.match(n).group(0).lower().replace("-", "_")
                     for n in data["project"]["optional-dependencies"]["research"]
                     if _REQ.match(n))


RESEARCH_DISTS = _research_distributions()


def tracked_files(root: Path = ROOT) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=False).stdout
    return [f.decode("utf-8", "replace") for f in out.split(b"\0") if f]


def tracked_state(root: Path = ROOT, files: list[str] | None = None) -> dict:
    """(mtime_ns, size) of every tracked file - a rewrite with the SAME bytes changes mtime, and
    that is a write too (stage D: the auditor's snapshot caught two suites doing exactly that)."""
    state = {}
    for rel in (files if files is not None else tracked_files(root)):
        try:
            st = (root / rel).stat()
            state[rel] = (st.st_mtime_ns, st.st_size)
        except OSError:
            state[rel] = None
    return state


def touched(before: dict, after: dict) -> list[str]:
    return sorted(rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel))


TRACKED = tracked_files()


@pytest.mark.parametrize("suite", SUITES, ids=lambda path: str(path.relative_to(ROOT)))
def test_standalone_suite(suite: Path) -> None:
    """Run one legacy suite in a clean interpreter and expose its output on failure."""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    before = tracked_state(ROOT, TRACKED)
    result = subprocess.run(
        [sys.executable, str(suite)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    #: A suite that cannot run is not a suite that failed, and until now `pytest -q` - the
    #: command README and CONTRIBUTING give - reported them as the same thing. The `dev` extra
    #: does not carry numpy, scipy, statsmodels or networkx; the `research` extra does, and the
    #: research tier is maintainer tooling deliberately kept out of the wheel. So the documented
    #: command died on `ModuleNotFoundError: No module named 'networkx'` in three suites, on a
    #: correctly installed machine, in CI's packaging job.
    #:
    #: The skip is narrow on purpose: only a suite under tests/research/, only a module the
    #: `research` extra declares, only when the process died naming it. A missing stdlib module,
    #: a typo in an import, or a research suite failing for any other reason stays red - and the
    #: `research` job installs the extra, so these suites are RUN somewhere on every push. The
    #: reason is printed rather than counted, because a silent skip is how a suite stops running
    #: for a year without anyone noticing.
    if result.returncode != 0 and suite.parent.name == "research":
        missing = _MISSING.search(result.stderr or "")
        name = missing.group(1).split(".")[0].lower() if missing else ""
        if name in RESEARCH_DISTS:
            pytest.skip(f"{suite.relative_to(ROOT)} needs the research extra: no module named "
                        f"{name!r}. Install with `pip install -e \".[research]\"`; CI's "
                        f"research job runs this suite with it installed.")
    assert result.returncode == 0, (
        f"{suite.relative_to(ROOT)} failed with exit code {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    #: A suite writes into temporary directories, never into the repository: a tracked file it
    #: rewrote - even byte for byte - is a test mutating the thing under test, left changed if the
    #: run is killed, and visible to every process importing the package meanwhile.
    changed = touched(before, tracked_state(ROOT, TRACKED))
    assert not changed, (f"{suite.relative_to(ROOT)} wrote tracked file(s) in the repository: "
                         f"{changed[:10]} - write to a temporary directory instead")
