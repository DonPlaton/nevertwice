#!/usr/bin/env python3
"""The demos keep the promise the README makes about them.

The README tells a stranger the examples use a throwaway store and leave their real vault
untouched. Three of them pinned `NEVERTWICE_HOME` and nothing else, and `config.py`
resolves `VAULT = env("VAULT") or NEVERTWICE_HOME or <default>` - so an exported
`NEVERTWICE_VAULT`, which is exactly how someone with a real install points at their
store, outranked the pin. Running the demo wrote a fabricated "mistake" note into the live
vault, distilled a guard from it, and let the hook auto-commit both.

That is the third occurrence of this failure class (2026-08-13, 2026-08-18, and this one),
and the second time it was caught only by someone noticing an unexpected value in output.
So it gets the same treatment the test sandbox got:

* **statically** - every demo that touches the engine imports the sandbox before any
  project module;
* **behaviourally** - with a hostile `NEVERTWICE_VAULT` exported at a directory that
  stands in for a live store, the canonical demo runs to completion and that directory is
  still empty afterwards;
* **deterministically** - `--check` output is byte-identical to a committed transcript, so
  the demo CI runs cannot quietly start depending on the machine.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

EXAMPLES = ROOT / "examples"
DEMO = EXAMPLES / "guard_demo.py"
EXPECTED = EXAMPLES / "guard_demo.expected.txt"

# Demos that import the engine in-process. demo.py drives a subprocess with its own
# pinned environment, so it is checked separately below.
IN_PROCESS_DEMOS = ("guard_demo.py", "no_model_demo.py", "scenario_demo.py")

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _project_modules() -> set[str]:
    return {p.stem for p in (ROOT / "nevertwice").glob("*.py")
            if not p.stem.startswith("__")} | {"nevertwice"}


_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)")


def test_every_demo_arms_the_sandbox_first() -> None:
    print("\n- the sandbox is the first project import in every demo -")
    package = _project_modules()
    missing, late = [], []
    for name in IN_PROCESS_DEMOS:
        path = EXAMPLES / name
        sandbox_line = first_project_line = None
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            m = _IMPORT_RE.match(line)
            if not m:
                continue
            root_name = m.group(1).split(".")[0]
            if root_name == "_sandbox" and sandbox_line is None:
                sandbox_line = i
            elif root_name in package and first_project_line is None:
                first_project_line = i
        if sandbox_line is None:
            missing.append(name)
        elif first_project_line is not None and sandbox_line > first_project_line:
            late.append(name)
    check(f"all {len(IN_PROCESS_DEMOS)} in-process demos import _sandbox", not missing,
          ", ".join(missing))
    check("none imports a project module before the sandbox", not late,
          ", ".join(late))


def test_the_sandbox_pins_the_vault_not_only_the_home() -> None:
    """Pinning HOME alone is the bug. The vault name is what config actually prefers."""
    print("\n- the sandbox pins both HOME and VAULT -")
    text = (EXAMPLES / "_sandbox.py").read_text(encoding="utf-8")
    check("NEVERTWICE_VAULT is pinned", 'os.environ["NEVERTWICE_VAULT"]' in text)
    check("NEVERTWICE_HOME is pinned", 'os.environ["NEVERTWICE_HOME"]' in text)
    check("the env-file pointer is scrubbed", '"NEVERTWICE_ENV_FILE"' in text)
    check("the legacy aliases are scrubbed",
          '"ANAMNESIS_VAULT"' in text and '"CLAUDE_MEMORY_VAULT"' in text)

    # demo.sh and demo.py do not import the module, so they carry their own pin.
    shell = (EXAMPLES / "demo.sh").read_text(encoding="utf-8")
    check("demo.sh exports its own throwaway vault",
          "export NEVERTWICE_VAULT=" in shell)
    driver = (EXAMPLES / "demo.py").read_text(encoding="utf-8")
    check("demo.py pins the vault for the subprocess it drives",
          '"NEVERTWICE_VAULT"' in driver)


def test_a_hostile_vault_variable_cannot_reach_the_demo() -> None:
    """The incident, reproduced: export a store location and prove nothing lands in it."""
    print("\n- an exported NEVERTWICE_VAULT cannot capture the demo -")
    with tempfile.TemporaryDirectory(prefix="nevertwice_fake_live_") as live:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("NEVERTWICE_", "ANAMNESIS_", "CLAUDE_MEMORY_"))}
        env.update({"NEVERTWICE_VAULT": live, "PYTHONUTF8": "1"})
        proc = subprocess.run([sys.executable, str(DEMO), "--check"], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=600,
                              encoding="utf-8", errors="replace")
        check("the demo still completes", proc.returncode == 0,
              f"exit {proc.returncode}: {(proc.stdout + proc.stderr).strip()[:300]}")
        leftovers = sorted(p.name for p in Path(live).iterdir())
        check("the exported store is still empty afterwards", not leftovers,
              ", ".join(leftovers[:6]))


def test_the_check_transcript_is_byte_identical() -> None:
    print("\n- --check is deterministic and matches the committed transcript -")
    check("a transcript is committed", EXPECTED.exists())
    if not EXPECTED.exists():
        return
    expected = EXPECTED.read_bytes()
    check("the transcript is LF-only", b"\r" not in expected)

    runs = []
    for _ in range(2):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("NEVERTWICE_", "ANAMNESIS_", "CLAUDE_MEMORY_"))}
        env["PYTHONUTF8"] = "1"
        proc = subprocess.run([sys.executable, str(DEMO), "--check"], cwd=ROOT, env=env,
                              capture_output=True, timeout=600)
        runs.append((proc.returncode, proc.stdout))

    check("both runs succeed", all(rc == 0 for rc, _ in runs),
          ", ".join(str(rc) for rc, _ in runs))
    check("two runs produce identical bytes", runs[0][1] == runs[1][1])
    check("the output matches the committed transcript", runs[0][1] == expected,
          f"got {runs[0][1][:120]!r}")


def test_the_exit_codes_are_documented_and_distinct() -> None:
    """A fixed code per beat is what makes a CI failure say which claim broke."""
    print("\n- each beat has its own exit code -")
    source = DEMO.read_text(encoding="utf-8")
    returns = sorted({int(m) for m in re.findall(r"^\s+return ([0-5])$", source, re.M)})
    check("the check function returns a distinct code per failure",
          returns == [0, 2, 3, 4, 5], str(returns))
    for code, meaning in ((2, "the lesson was not recorded"),
                          (3, "no guard was distilled"),
                          (4, "the repeat was NOT flagged"),
                          (5, "the corrected action was flagged anyway")):
        check(f"exit {code} is documented", meaning.split(" - ")[0] in source)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\ndemo contract: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
