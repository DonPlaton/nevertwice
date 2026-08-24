#!/usr/bin/env python3
"""The sandbox contract: no suite can reach the developer's live memory store.

Two incidents (2026-08-13, 2026-08-18) wrote test fixtures into a production
vault because a shell exported NEVERTWICE_VAULT and the package baked that path
into import-time constants. `tests/_env_guard.py` exists to make that impossible.

This suite asserts the guard actually holds - not that it exists:

* every suite imports it before any package module (a suite that forgets is the
  whole failure mode coming back);
* a hostile ambient environment - live vault, legacy aliases, a planted env file,
  the cross-encoder switched on - is fully neutralised in a child process;
* a test's own feature flags survive, or the guard would be unusable.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - the guard guards its own suite too

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _suites() -> list[Path]:
    return (sorted((ROOT / "tests").glob("_test_*.py"))
            + sorted((ROOT / "tests" / "research").glob("_test_*.py")))


def _package_modules() -> set[str]:
    """Top-level module names that resolve to project code once a suite has put
    `nevertwice/` or `research/` on sys.path - importing any of them bakes
    store paths from the environment."""
    names = {"nevertwice"}
    for pkg in ("nevertwice", "research"):
        names |= {p.stem for p in (ROOT / pkg).glob("*.py")
                  if not p.stem.startswith("__")}
    return names


_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)")


def test_every_suite_arms_the_guard_first() -> None:
    print("\n- the guard is the first project import in every suite -")
    package = _package_modules()
    missing: list[str] = []
    late: list[str] = []

    for suite in _suites():
        guard_line = None
        first_package_line = None
        for i, line in enumerate(suite.read_text(encoding="utf-8").splitlines()):
            m = _IMPORT_RE.match(line)
            if not m:
                continue
            root_name = m.group(1).split(".")[0]
            if root_name == "_env_guard" and guard_line is None:
                guard_line = i
            elif root_name in package and first_package_line is None:
                first_package_line = i
        rel = suite.relative_to(ROOT).as_posix()
        if guard_line is None:
            missing.append(rel)
        elif first_package_line is not None and guard_line > first_package_line:
            late.append(rel)

    check(f"all {len(_suites())} suites import _env_guard", not missing,
          ", ".join(missing))
    check("no suite imports a project module before the guard", not late,
          ", ".join(late))


def _probe(extra_env: dict[str, str], planted_env_file: str | None = None) -> dict:
    """Import the guard, then config, in a child with a hostile environment."""
    script = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(HERE)!r})\n"
        f"sys.path.insert(0, {str(ROOT / 'nevertwice')!r})\n"
        "import _env_guard\n"
        "import config as cfg\n"
        "watched = ('NEVERTWICE_VAULT', 'NEVERTWICE_HOME', 'NEVERTWICE_XRERANK',\n"
        "           'NEVERTWICE_TEST_XRERANK', 'NEVERTWICE_EMBED_MODEL',\n"
        "           'NEVERTWICE_ENV_FILE', 'ANAMNESIS_VAULT',\n"
        "           'CLAUDE_MEMORY_VAULT', 'NEVERTWICE_BRAIN')\n"
        "print(json.dumps({'env': {k: __import__('os').environ.get(k) for k in watched},\n"
        "                  'vault': str(cfg.VAULT),\n"
        "                  'projects': str(cfg.PROJECTS_ROOT)}))\n"
    )
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("NEVERTWICE_", "ANAMNESIS_", "CLAUDE_MEMORY_"))}
    env.update(extra_env)
    if planted_env_file is not None:
        env["NEVERTWICE_ENV_FILE"] = planted_env_file
    proc = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"probe failed:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_hostile_environment_is_neutralised() -> None:
    print("\n- a hostile ambient environment cannot reach the suite -")
    with tempfile.TemporaryDirectory(prefix="nevertwice_fake_live_") as live:
        planted = Path(live) / "planted.env"
        planted.write_text(f"NEVERTWICE_VAULT={live}\n"
                           f"NEVERTWICE_PROJECTS_ROOT={live}\n", encoding="utf-8")
        got = _probe({
            "NEVERTWICE_VAULT": live,
            "NEVERTWICE_HOME": live,
            "ANAMNESIS_VAULT": live,
            "CLAUDE_MEMORY_VAULT": live,
            "NEVERTWICE_EMBED_MODEL": "a-machine-local-pin",
            "NEVERTWICE_XRERANK": "1",
            "NEVERTWICE_TEST_XRERANK": "1",
            "NEVERTWICE_BRAIN": "1",
        }, planted_env_file=str(planted))
        env, vault = got["env"], got["vault"]

        check("the resolved vault is not the live store",
              Path(live) != Path(vault) and live not in vault, vault)
        check("the resolved vault is the guard's sandbox",
              "nevertwice_test_home_" in vault, vault)
        check("the resolved projects root is not the live store",
              live not in got["projects"], got["projects"])
        check("a planted env file cannot re-inject a vault path",
              env["NEVERTWICE_ENV_FILE"] is None)
        check("legacy ANAMNESIS_VAULT is scrubbed",
              env["ANAMNESIS_VAULT"] is None)
        check("legacy CLAUDE_MEMORY_VAULT is scrubbed",
              env["CLAUDE_MEMORY_VAULT"] is None)
        check("a machine-local embedder pin is scrubbed",
              env["NEVERTWICE_EMBED_MODEL"] is None)
        check("the optional cross-encoder is pinned off even when it was on",
              env["NEVERTWICE_XRERANK"] == "0", repr(env["NEVERTWICE_XRERANK"]))
        check("the expensive research opt-in is scrubbed",
              env["NEVERTWICE_TEST_XRERANK"] is None,
              repr(env["NEVERTWICE_TEST_XRERANK"]))
        check("a test's own feature flag survives",
              env["NEVERTWICE_BRAIN"] == "1")


def test_env_file_beside_the_package_cannot_relocate_the_store() -> None:
    """`NEVERTWICE_ENV_FILE` is only one of the ways an env file is found.

    `config.load_dotenv()` also reads `.env` / `.secrets.env` next to the package and
    at the repo root, and it runs at config-import time - before `VAULT` resolves. Only
    the guard *pinning* NEVERTWICE_VAULT stops such a file relocating the whole suite
    into a live store, because load_dotenv uses setdefault and a value already in the
    environment wins. Scrubbing alone is not enough. Run against a throwaway copy of
    config.py so no env file is ever created inside the repository.
    """
    print("\n- an env file beside the package cannot relocate the store -")
    with tempfile.TemporaryDirectory(prefix="nevertwice_pkgcopy_") as tmp:
        pkg = Path(tmp) / "pkg"
        pkg.mkdir()
        (pkg / "config.py").write_bytes((ROOT / "nevertwice" / "config.py").read_bytes())
        live = Path(tmp) / "fake_live_store"
        live.mkdir()
        (pkg / ".secrets.env").write_text(f"NEVERTWICE_VAULT={live}\n", encoding="utf-8")

        script = (
            "import json, sys\n"
            f"sys.path.insert(0, {str(HERE)!r})\n"
            f"sys.path.insert(0, {str(pkg)!r})\n"
            "import _env_guard\n"
            "import config as cfg\n"
            "print(json.dumps({'vault': str(cfg.VAULT)}))\n"
        )
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("NEVERTWICE_", "ANAMNESIS_", "CLAUDE_MEMORY_"))}
        proc = subprocess.run([sys.executable, "-c", script], cwd=tmp, env=env,
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(f"probe failed:\n{proc.stderr.strip()}")
        vault = json.loads(proc.stdout.strip().splitlines()[-1])["vault"]

        check("a .secrets.env beside the package does not become the store",
              str(live) != vault, vault)
        check("the store is still the guard's sandbox",
              "nevertwice_test_home_" in vault, vault)


def test_guard_pins_a_fresh_sandbox_per_process() -> None:
    print("\n- each process gets its own sandbox, never the user's home -")
    first = _probe({})["vault"]
    second = _probe({})["vault"]
    check("two processes get different sandboxes", first != second,
          f"{first} == {second}")
    home = str(Path.home())
    check("the sandbox is not inside the user's nevertwice store",
          not first.startswith(str(Path(home) / ".nevertwice")), first)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\nhermeticity: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
