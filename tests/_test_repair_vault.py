#!/usr/bin/env python3
"""The vault repair finds real damage, and writes nothing until told twice.

The engine defects that produced these are fixed forward, but a fix does not rewrite the
past. This tool exists so the accumulated damage is one reviewed command away instead of a
manual edit — and, like the install sync, it is built to be distrusted: dry run by
default, a backup before any write, and a rename rather than a delete where the right
answer is a judgement it cannot make.
"""
import _env_guard  # noqa: F401
import sys, tempfile, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "repair_vault.py"

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def run(*a):
    return subprocess.run([sys.executable, str(TOOL), *a], capture_output=True,
                          text=True, timeout=300, cwd=str(ROOT))


def note(p: Path, tags):
    p.write_text("---\ndate: 2026-09-02\ntags: [" + ", ".join(f'"{t}"' for t in tags)
                 + "]\n---\n\n# x\n", encoding="utf-8")


print("\n- a directory that is not a store is refused -")
with tempfile.TemporaryDirectory() as td:
    r = run("--vault", td)
    check("no Patterns/ means refusal", r.returncode == 2, r.stdout[-160:])
    check("nothing was created", not any(Path(td).iterdir()))

print("\n- a twin is found and the dry run leaves it alone -")
with tempfile.TemporaryDirectory() as td:
    v = Path(td)
    pat = v / "Patterns"; (pat / "Superseded").mkdir(parents=True)
    stem = "2026-09-02-proj-pattern-a-lesson.md"
    note(pat / stem, ["qa"]); note(pat / "Superseded" / stem, ["qa"])
    r = run("--vault", str(v))
    check("the twin is reported", "1 twin" in r.stdout, r.stdout[-200:])
    check("the dry run says it wrote nothing", "DRY RUN" in r.stdout)
    check("the live file is untouched", (pat / stem).exists())
    check("no backup was made", not any(p.name.startswith(".repair-backup-") for p in v.iterdir()))

    print("\n- --apply backs up and RENAMES, never deletes -")
    r2 = run("--vault", str(v), "--apply")
    check("apply succeeded", r2.returncode == 0, r2.stdout[-200:])
    check("a backup exists", any(p.name.startswith(".repair-backup-") for p in v.iterdir()))
    check("the retired copy still exists", (pat / "Superseded" / stem).exists())
    check("the live one was renamed, not removed",
          not (pat / stem).exists() and any(p.name.endswith("-live.md") for p in pat.glob("*.md")))
    check("the undo command is printed", "UNDO:" in r2.stdout)

print("\n- a tag common to ONE project only is not flagged inside that project -")
with tempfile.TemporaryDirectory() as td:
    v = Path(td); (v / "Patterns").mkdir(parents=True)
    for i in range(10):
        note(v / "Patterns" / f"2026-09-02-alpha-pattern-n{i}.md", ["alphatag"])
    r = run("--vault", str(v))
    check("its owner is not accused of carrying it", "0 foreign tag" in r.stdout, r.stdout[-200:])

print(f"\nrepair vault: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
