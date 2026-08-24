#!/usr/bin/env python3
"""Regression checks for the 2026-08-24 repository-wide audit."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PKG = ROOT / "nevertwice"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PKG))

import _env_guard  # noqa: F401, E402 - must run before package imports

import bootstrap_contexts as bootstrap  # noqa: E402
import graphify  # noqa: E402


PASSED = 0
FAILED = 0


def check(name: str, condition: bool) -> None:
    global PASSED, FAILED
    print(("  ok   " if condition else "  FAIL ") + name)
    PASSED += int(condition)
    FAILED += int(not condition)


def test_brain_profile_order_is_deterministic() -> None:
    outputs = set()
    for seed in range(1, 9):
        env = os.environ.copy()
        env.update({"PYTHONHASHSEED": str(seed),
                    "NEVERTWICE_PROFILE": "general,research"})
        proc = subprocess.run(
            [sys.executable, "-c",
             "from nevertwice.config import entity_types, relation_hints; "
             "print('|'.join(entity_types())); print('|'.join(relation_hints()))"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
        )
        outputs.add(proc.stdout.strip())
    check("Brain ontology order is stable across hash seeds", len(outputs) == 1)
    only = next(iter(outputs), "")
    check("Brain ontology follows declared profile order", only.startswith("paper|method|"))
    check("Brain relation hints follow declared profile order", "\ncites|builds-on|" in only)


def test_project_scanners_skip_symlinked_files() -> None:
    with tempfile.TemporaryDirectory(prefix="nevertwice_audit_scan_") as tmp:
        root = Path(tmp)
        leak = root / "README.md"
        leak.write_text("# DO NOT INDEX THIS SECRET\n", encoding="utf-8")
        original = Path.is_symlink

        def fake_is_symlink(path: Path) -> bool:
            return path == leak or original(path)

        with mock.patch.object(Path, "is_symlink", fake_is_symlink):
            graph = graphify.build(root)
            configs = bootstrap.collect_configs(root)
        check("graphify skips symlinked project files",
              all(node["path"] != "README.md" for node in graph["files"]))
        check("bootstrap skips symlinked config files", "DO NOT INDEX" not in configs)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for test in tests:
        try:
            test()
        except Exception as exc:
            FAILED += 1
            print(f"  ERR  {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\naudit regressions: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
