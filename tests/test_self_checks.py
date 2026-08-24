"""Pytest entry point for Nevertwice's standalone self-check suites.

The historical suites execute assertions at module scope and intentionally remain
directly runnable by CI. Running each in a subprocess preserves that isolation while
giving contributors one conventional ``python -m pytest`` command.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SUITES = tuple(sorted((ROOT / "tests").glob("_test_*.py"))) + tuple(
    sorted((ROOT / "tests" / "research").glob("_test_*.py"))
)


@pytest.mark.parametrize("suite", SUITES, ids=lambda path: str(path.relative_to(ROOT)))
def test_standalone_suite(suite: Path) -> None:
    """Run one legacy suite in a clean interpreter and expose its output on failure."""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
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
    assert result.returncode == 0, (
        f"{suite.relative_to(ROOT)} failed with exit code {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
