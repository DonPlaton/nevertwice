#!/usr/bin/env python3
"""The install sync writes nothing without being told to, and refuses a wrong target.

hosts.py documents a hand-rolled flat copy under ~/.claude/scripts as a supported way to
run this, and nothing shipped a way to update one — so engine fixes reached such an
install only by hand-copying. A dry run against the real install on 2026-09-02 showed 21
files diverged, ELEVEN of them modules absent there entirely, so this is not a small
operation and the tool is built to be distrusted.
"""
import _env_guard  # noqa: F401
import shutil
import sys, tempfile, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "sync_install.py"

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(*args, cwd=None):
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                          text=True, timeout=300, cwd=cwd or str(ROOT))


print("\n- a target that is not an install is refused -")
with tempfile.TemporaryDirectory() as td:
    r = run("--target", td)
    check("an empty directory is refused", r.returncode == 2, r.stdout[-200:])
    check("and it says what is missing", "missing" in r.stdout, r.stdout[-200:])
    check("nothing was written into it", not any(Path(td).iterdir()))

print("\n- a missing target is refused, not created -")
with tempfile.TemporaryDirectory() as td:
    gone = Path(td) / "nope"
    r = run("--target", str(gone))
    check("a nonexistent target exits non-zero", r.returncode == 2)
    check("and is not created", not gone.exists())

print("\n- a recognised target is planned but NOT written without --apply -")
with tempfile.TemporaryDirectory() as td:
    t = Path(td)
    (t / "memory_hook.py").write_text("# stale", encoding="utf-8")
    (t / "guards.py").write_text("# stale", encoding="utf-8")
    before = {p.name: p.read_text(encoding="utf-8") for p in t.iterdir()}
    r = run("--target", str(t))
    check("the dry run succeeds", r.returncode == 0, r.stdout[-200:])
    check("it says it wrote nothing", "DRY RUN" in r.stdout)
    check("it lists files that would change", "would change" in r.stdout)
    after = {p.name: p.read_text(encoding="utf-8") for p in t.iterdir()}
    check("no file was modified", before == after)
    check("no backup directory was created",
          not any(p.name.startswith("scripts-backup-") for p in Path(td).iterdir()))

print("\n- an install that does not run is not a PASS -")
#: The verdict is the marker on stdout, never the exit code. A half-copied install exits 0 by
#: design - the hooks run the entry point before every tool call, so memory being unavailable
#: must cost memory and never the agent's Edit - which means a directory missing one engine part
#: returns 0 with an explanation on stderr and nothing on stdout. Reading the exit code alone,
#: `_verify` reported PASS on an install that does not answer a recall at all, which is the one
#: thing its own docstring says it exists to prevent.
sys.path.insert(0, str(ROOT / "tools"))
import sync_install as si  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    whole = Path(td) / "whole"
    shutil.copytree(ROOT / "nevertwice", whole, ignore=shutil.ignore_patterns("__pycache__"))
    ok, detail = si._verify(whole)
    check("a complete copy verifies", ok, detail)

    broken = Path(td) / "broken"
    shutil.copytree(whole, broken, ignore=shutil.ignore_patterns("__pycache__"))
    parts = sorted(broken.glob("_engine_*.py"))
    missing = parts[len(parts) // 2]
    missing.unlink()
    ok, detail = si._verify(broken)
    check(f"a copy missing {missing.name} does NOT verify", not ok, detail)
    check("and the reason names the quiet exit rather than a bare code",
          "half-copied" in detail or "missing" in detail, detail)


print("\n- machine-local state is never in the copy set -")
src = TOOL.read_text(encoding="utf-8")
for f in (".secrets.env", "twin_calibration.json", "guards.json",
          ".processed_sessions.json"):
    check(f"{f} is excluded", f'"{f}"' in src)

print(f"\nsync install: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
