#!/usr/bin/env python3
"""Prove every console command the distribution installs is actually wired up.

A green test suite once shipped a broken first touch. The suites import modules, but an
entry point is a *wiring* between a name in the installed metadata and a function inside
the package. Nothing in a test exercises that wiring, so if a module is renamed the first
person to type the command finds out.

This checks the wiring without running it. For every entry point the installed
distribution declares:

* the launcher script exists on disk, so the install really wrote it;
* the target module imports;
* the target attribute exists and is callable.

It deliberately does **not** invoke the commands. `--help` would be the obvious way, but
most of these CLIs parse `sys.argv` by hand and treat an unrecognised flag as "no
arguments", so `--help` runs the real command against whatever store the environment
points at. Executing them to prove they start would be the same mistake this project has
already made twice with its test sandbox.

Usage:

    python tools/console_smoke.py                     # the installed `nevertwice`
    python tools/console_smoke.py --python .venv/bin/python
    python tools/console_smoke.py --distribution nevertwice

Exit code 0 when every entry point resolves, 1 otherwise.
Standard library only, so it runs inside a bare virtualenv holding just the wheel.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Runs inside the target interpreter. Imports each entry point's module and looks up its
# attribute; never calls it. mcp_server rebinds sys.stdout at import (it speaks stdio
# JSON-RPC), so the real stream is kept aside and restored before anything is reported.
PROBE = r'''
import json, sys, shutil
from importlib.metadata import distribution
from pathlib import Path


def _launcher(name):
    """A console script lands beside the interpreter that installed it. Look there
    first: PATH is not necessarily set up for the environment under test, and a
    PATH-only check would report a missing launcher that is really present."""
    scripts = Path(sys.executable).parent
    for candidate in (scripts / name, scripts / (name + ".exe")):
        if candidate.exists():
            return True
    return bool(shutil.which(name))


_out = sys.stdout
dist = distribution(DIST_NAME)
results = []
for ep in dist.entry_points:
    if ep.group != "console_scripts":
        continue
    row = {"name": ep.name, "value": ep.value, "launcher": _launcher(ep.name)}
    try:
        module_name, _, attr = ep.value.partition(":")
        module = __import__(module_name, fromlist=["_"])
        target = module
        for part in attr.split(".") if attr else []:
            target = getattr(target, part)
        row["resolved"] = callable(target)
        row["error"] = None
    except Exception as exc:
        row["resolved"] = False
        row["error"] = f"{type(exc).__name__}: {exc}"
    results.append(row)
sys.stdout = _out
open(OUT_PATH, "w", encoding="utf-8").write(json.dumps(results))
'''


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--python", default=sys.executable,
                    help="interpreter of the environment the wheel was installed into")
    ap.add_argument("--distribution", default="nevertwice")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args(argv)

    sandbox = tempfile.mkdtemp(prefix="nevertwice_console_smoke_")
    out_path = Path(sandbox) / "result.json"
    code = (f"DIST_NAME = {args.distribution!r}\nOUT_PATH = {str(out_path)!r}\n" + PROBE)

    # Importing the package resolves store paths from the environment. Point them at a
    # throwaway directory so a smoke run can never touch a real store.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("NEVERTWICE_", "ANAMNESIS_", "CLAUDE_MEMORY_"))}
    env.update({"NEVERTWICE_HOME": sandbox, "NEVERTWICE_VAULT": sandbox,
                "NEVERTWICE_ENV_FILE": str(Path(sandbox) / "absent.env"),
                "NEVERTWICE_CLOUD": "none", "NEVERTWICE_XRERANK": "0",
                "PYTHONUTF8": "1"})

    proc = subprocess.run([args.python, "-c", code], capture_output=True, text=True,
                          env=env, timeout=args.timeout, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0 or not out_path.exists():
        print("the probe failed to run:")
        print((proc.stdout + proc.stderr).strip()[:2000])
        return 1

    results = json.loads(out_path.read_text(encoding="utf-8"))
    if not results:
        print(f"{args.distribution} declares no console scripts - "
              f"is it installed in {args.python}?")
        return 1

    failures = []
    for row in sorted(results, key=lambda r: r["name"]):
        problems = []
        if not row["launcher"]:
            problems.append("no launcher on PATH")
        if not row["resolved"]:
            problems.append(row["error"] or "target is not callable")
        if problems:
            failures.append(f"{row['name']} -> {row['value']}: {'; '.join(problems)}")
            print(f"  FAIL  {row['name']:<24} {row['value']}")
        else:
            print(f"  ok    {row['name']:<24} {row['value']}")

    print(f"\n{len(results) - len(failures)}/{len(results)} console entry points wired")
    for failure in failures:
        print(f"  {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
