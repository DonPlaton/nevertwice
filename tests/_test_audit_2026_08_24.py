#!/usr/bin/env python3
"""Regression checks for the 2026-08-24 repository-wide audit."""
from __future__ import annotations

import os
import io
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

_stdout = sys.stdout
import mcp_server as mcp  # noqa: E402 - redirects stdout while serving stdio
sys.stdout = _stdout


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


def test_mcp_jsonrpc_notification_and_validation() -> None:
    sent: list[dict] = []
    with mock.patch.object(mcp, "_send", side_effect=sent.append):
        mcp._handle({"jsonrpc": "2.0", "method": "ping"})
        mcp._handle({"jsonrpc": "2.0", "method": "tools/list"})
        mcp._handle({"jsonrpc": "2.0", "method": "tools/call",
                     "params": {"name": "does-not-exist"}})
    check("MCP notifications never receive JSON-RPC responses", sent == [])

    sent.clear()
    crashed = False
    with mock.patch.object(mcp, "_send", side_effect=sent.append):
        try:
            mcp._handle({"jsonrpc": "2.0", "id": None, "method": "initialize",
                         "params": []})
        except Exception:
            crashed = True
    check("MCP invalid params do not crash the dispatcher", not crashed)
    check("MCP invalid params return -32602 even for id=null",
          bool(sent) and sent[0].get("id", "missing") is None
          and sent[0].get("error", {}).get("code") == -32602)

    sent.clear()
    with mock.patch.object(mcp, "_send", side_effect=sent.append), \
         mock.patch.object(mcp, "_handle", side_effect=RuntimeError("private path")), \
         mock.patch.object(sys, "stdin", io.StringIO(
             '{"jsonrpc":"2.0","id":null,"method":"ping"}\n')), \
         mock.patch.object(sys, "stderr", io.StringIO()):
        mcp.main()
    check("MCP internal errors answer explicit id=null without leaking details",
          bool(sent) and sent[0].get("id", "missing") is None
          and sent[0].get("error", {}) == {"code": -32603,
                                            "message": "internal error"})


def test_distribution_name_is_consistent() -> None:
    paths = [
        PKG / "integrations" / "__init__.py",
        PKG / "integrations" / "langchain_memory.py",
        PKG / "integrations" / "llamaindex_retriever.py",
        ROOT / "research" / "qa_eval.py",
    ]
    stale = [p for p in paths if "nevertwice-memory" in p.read_text(encoding="utf-8")]
    check("optional-dependency install hints use the real PyPI name", not stale)


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
