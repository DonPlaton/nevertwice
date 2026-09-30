#!/usr/bin/env python3
"""PREREG-V3 (the auditor, 2026-09-30, after an incident): a suite never reaches the real polygon or the hop.

At 09:49 a row of the lock_install suite called lock_install.main() with no stub; on code that did not refuse first,
main() took the real Contract.default(), the real hop, and ran a real window (a8-pypi-mem0_v3 run x) without GO.

* G-1: under the test sandbox (sandbox_guard's mode "sandbox", or NEVERTWICE_SANDBOX=1 inherited from a sandboxed
  parent) launch.Contract.default() refuses by name; with neither it is the machine's contract (plain data);
* G-2: sandbox_guard.isolate() - every suite's entry, through _env_guard - sets NEVERTWICE_SANDBOX=1, so a child of a
  suite refuses too;
* G-3: main() of every window module, called from here with no stub, refuses by name and makes no run directory -
  and it is called ONLY after G-1 saw the refusal in this very process: on code without the guard (a mutant) the row
  fails by name and no window can start;
* G-4 (census): no test file but this one calls <x>.Contract.default(); a row that reads the machine's declared
  values uses Contract._machine().

    python tests/_test_v3_contract_guard.py
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite - it arms the sandbox this suite checks

import sandbox_guard  # noqa: E402

V3 = ROOT / "research" / "v3"
REAL_RUNS = Path(r"D:\Coding\_nevertwice_polygon\runs\v3")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def outcome(fn) -> str:
    """"refused: <text>" for a ContractViolation (by its class name: each module loads its own launch), else what
    happened - never raised past the row."""
    try:
        got = fn()
        return f"returned {type(got).__name__}"
    except SystemExit as e:
        return f"exit {e.code}"
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ == "ContractViolation":
            return "refused: " + " ".join(str(r) for r in getattr(e, "reasons", [e]))
        return f"{type(e).__name__}: {e}"


L = _load("v3_launch_guard_t", V3 / "launch.py")

print("- G-1, G-2: Contract.default() under the sandbox -")
g1 = outcome(L.Contract.default)
saved_mode, saved_env = getattr(sandbox_guard, "_MODE", None), os.environ.get("NEVERTWICE_SANDBOX")
try:
    sandbox_guard._MODE = None
    os.environ["NEVERTWICE_SANDBOX"] = "1"
    g1b = outcome(L.Contract.default)
    os.environ.pop("NEVERTWICE_SANDBOX", None)
    g1c = outcome(L.Contract.default)
finally:
    sandbox_guard._MODE = saved_mode
    if saved_env is None:
        os.environ.pop("NEVERTWICE_SANDBOX", None)
    else:
        os.environ["NEVERTWICE_SANDBOX"] = saved_env
GUARD_OK = g1.startswith("refused:") and "test sandbox" in g1
check("G-1: under the suite's sandbox Contract.default() refuses by name (a suite never reaches the real polygon or "
      "the hop); with the mark only in the environment (a sandboxed parent's child) it refuses too; with neither it is "
      "the machine's contract, plain data", GUARD_OK and g1b.startswith("refused:") and g1c == "returned Contract",
      f"{g1} | {g1b} | {g1c}")
check("G-2: sandbox_guard.isolate() (every suite's entry, through _env_guard) marks the environment NEVERTWICE_SANDBOX=1 "
      "- a child of a suite inherits it", os.environ.get("NEVERTWICE_SANDBOX") == "1" and sandbox_guard._MODE == "sandbox",
      f"{os.environ.get('NEVERTWICE_SANDBOX')} {sandbox_guard._MODE}")
check("G-1b: Contract._machine() is the machine's declared contract, plain data, for rows that only read its values",
      outcome(getattr(L.Contract, "_machine", lambda: None)) == "returned Contract")

print("\n- G-3: every window module's main(), with no stub, refuses - called only once G-1 saw the refusal -")
PY = sys.executable
MAINS = [("fetch_a3", ["--window", "a7-docs-2", "--plan", "d4", "--run", "guardtest", "--python", PY]),
         ("fetch_pins_a3", ["--window", "a7-github", "--run", "guardtest", "--python", PY]),
         ("fetch_pins_a3", ["--window", "a7-arxiv-src", "--run", "guardtest", "--place-d8", "zep_paper_src"]),
         ("bin_install", ["--run", "guardtest"]),
         ("model_install", ["--run", "guardtest", "--install-run", "guardtest0"]),
         ("image_install", None),
         ("lock_install", ["--venv", "mem0_v3", "--run", "guardtest", "--download-only"]),
         ("lock_install", ["--venv", "mem0_v3", "--install-from", "guardtest"]),
         ("lock_install", ["--venv", "scorer_v3", "--run", "guardtest"]),
         ("m31_check", ["--venv", "mem0_v3", "--run", "guardtest"]),
         ("fetch_py_base", []),
         ("fetch_npm_a7", ["--run", "guardtest", "--python", PY]),
         ("fetch_npm_discovery_a7", ["--run", "guardtest", "--python", PY])]


def run_dirs() -> set:
    return {str(p) for p in REAL_RUNS.glob("*/*/guardtest*")} | {str(p) for p in REAL_RUNS.glob("*/guardtest*")}


before = run_dirs()
for n, (mod, argv) in enumerate(MAINS):
    label = f"{mod} {' '.join(argv or [])}".strip()
    if not GUARD_OK:
        check(f"G-3 {label}: refused before any window", False, "the guard did not refuse in this process - main() not called")
        continue
    M = _load(f"v3_guard_{mod}_{n}", V3 / f"{mod}.py")
    if argv is None:                                   # image_install: its first declared image
        argv = ["--image", sorted(M.IMAGES)[0], "--run", "guardtest"]
        label = f"{mod} {' '.join(argv)}"
    got = outcome(lambda M=M, argv=argv: M.main(argv))
    check(f"G-3 {label}: refused by name at the contract - no window, no run directory",
          got.startswith("refused:") and "test sandbox" in got and run_dirs() == before, got[:200])

print("\n- G-4: the census - no test reaches the real Contract.default() -")
hits = []
for f in sorted((ROOT / "tests").rglob("*.py")):
    if f.name == Path(__file__).name:
        continue
    try:
        tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "default"
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "Contract"):
            hits.append(f"{f.relative_to(ROOT).as_posix()}:{node.lineno}")
check("G-4: no test file but this one calls <x>.Contract.default() - a row that reads the machine's declared values "
      "calls Contract._machine()", hits == [], str(hits[:6]))

print(f"\nv3 contract guard: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
