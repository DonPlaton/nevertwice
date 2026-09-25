"""The battery's tracked-file snapshot, importable without pytest.

`tests/test_self_checks.py` (the pytest entry point) asserts that no suite changes a tracked file;
`tests/_test_battery_offline.py` proves the helpers can fail. The CI core job runs every
`tests/_test_*.py` on a bare interpreter with nothing installed, so the helpers live here rather
than in the pytest module: importing that module from a suite needed pytest, and all twelve CI
test jobs failed on it at 748581b while both local batteries - pytest installed - stayed green.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
