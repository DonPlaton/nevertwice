#!/usr/bin/env python3
"""The vault repair finds real damage, and writes nothing until told twice.

The engine defects that produced these are fixed forward, but a fix does not rewrite the
past. This tool exists so the accumulated damage is one reviewed command away instead of a
manual edit - and, like the install sync, it is built to be distrusted: dry run by
default, a backup before any write, and a rename rather than a delete where the right
answer is a judgement it cannot make.

The review of 2026-09-05 found the first version renaming the wrong twin (the live note,
which changed its slug and broke every link to it), stripping a foreign tag only when it
opened its line, never writing the untagged note back, and detecting foreign tags from the
frontmatter list alone while the engine counts them from the body.
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


def note(p: Path, tags, body_tags=None):
    body = "# x\n\n" + (" ".join(f"#{t}" for t in body_tags) + "\n" if body_tags else "")
    p.write_text("---\ndate: 2026-09-02\ntags: [" + ", ".join(f'"{t}"' for t in tags)
                 + "]\n---\n\n" + body, encoding="utf-8")


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

    print("\n- --apply backs up and RENAMES the retired copy, never the live one -")
    r2 = run("--vault", str(v), "--apply")
    check("apply succeeded", r2.returncode == 0, r2.stdout[-200:])
    check("a backup exists", any(p.name.startswith(".repair-backup-") for p in v.iterdir()))
    check("the live note keeps its name, so every link to it still resolves", (pat / stem).exists())
    check("the retired copy was renamed with the engine's own collision suffix",
          not (pat / "Superseded" / stem).exists()
          and (pat / "Superseded" / "2026-09-02-proj-pattern-a-lesson-2.md").exists(),
          str(sorted(p.name for p in (pat / "Superseded").glob("*.md"))))
    check("the undo command is printed", "UNDO:" in r2.stdout)
    check("a second run finds nothing left to repair", "0 twin" in run("--vault", str(v)).stdout)

print("\n- a tag common to ONE project only is not flagged inside that project -")
with tempfile.TemporaryDirectory() as td:
    v = Path(td); (v / "Patterns").mkdir(parents=True)
    for i in range(10):
        note(v / "Patterns" / f"2026-09-02-alpha-pattern-n{i}.md", ["alphatag"])
    r = run("--vault", str(v))
    check("its owner is not accused of carrying it", "0 foreign tag" in r.stdout, r.stdout[-200:])

print("\n- a foreign tag is found in the body, and removed wherever it sits -")
with tempfile.TemporaryDirectory() as td:
    v = Path(td); (v / "Patterns").mkdir(parents=True)
    for i in range(10):
        note(v / "Patterns" / f"2026-09-02-alpha-pattern-n{i}.md", ["alphatag"], ["alphatag"])
    # beta's note carries alpha's signature tag only in the BODY, and not at line start
    victim = v / "Patterns" / "2026-09-02-beta-pattern-victim.md"
    note(victim, ["qa"], ["qa", "alphatag", "mistake"])
    r = run("--vault", str(v))
    check("the body tag is detected", "1 foreign tag" in r.stdout, r.stdout[-300:])
    r2 = run("--vault", str(v), "--apply")
    text = victim.read_text(encoding="utf-8")
    check("the tag is gone from the body", "#alphatag" not in text, text)
    check("the neighbouring tags survive", "#qa" in text and "#mistake" in text, text)
    check("the frontmatter list is intact", 'tags: ["qa"]' in text, text)
    check("a second run finds the store clean", "0 foreign tag" in run("--vault", str(v)).stdout)

print(f"\nrepair vault: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
