#!/usr/bin/env python3
r"""What `git status --porcelain` actually says, and what ten copies of one parser heard.

Every registrar refuses to record a claim whose command closure holds an uncommitted file. All ten
found that file by the same three lines, copied ten times in three cosmetic spellings:

    {line[3:].strip().replace("\\", "/") for line in git("status", "--porcelain") ...}

Measured 2026-09-19 on a probe repository - four changed files in the closure, none of them seen:

    R  tools/alpha.py -> tools/beta.py     ->  "tools/alpha.py -> tools/beta.py", matching nothing
     M "tools/with space.py"               ->  kept its quotes, matching nothing
     M "tools/\320\267...\320\260.py"      ->  the octal escapes became "/320/267...", matching nothing

Git quotes a path whenever it holds a space or a byte outside ASCII, and reports a rename as one
line naming both sides. So `git mv` on a file in the closure - or an edit to any file whose name
has a space in it - and the registrar records the claim against a tree it believes is clean.
Nothing caught this because the only test that reached `_dirty_files` stubbed it out.

The fix is not a better regex over quoted text: `--porcelain -z` emits paths raw and
NUL-separated, with a rename's original name as its own field - no quoting, no escaping, no arrow
to split on. One parser in `tools/git_status.py`, one suite here.

    python tests/_test_git_status_parsing.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import
import git_status  # noqa: E402

PASSED = 0
FAILED = 0
NUL = chr(0)
BACKSLASH = chr(92)


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def z(*records: str) -> str:
    """The exact shape of `git status --porcelain -z`: every field NUL-TERMINATED."""
    return "".join(r + NUL for r in records)


def test_one_record_per_change() -> None:
    print("\n- the ordinary records -")
    check("a worktree modification", git_status.parse(z(" M tools/x.py")) == {"tools/x.py"})
    check("a staged modification", git_status.parse(z("M  tools/x.py")) == {"tools/x.py"})
    check("staged and modified again", git_status.parse(z("MM tools/x.py")) == {"tools/x.py"})
    check("a deletion", git_status.parse(z(" D tools/x.py")) == {"tools/x.py"})
    check("an addition", git_status.parse(z("A  tools/x.py")) == {"tools/x.py"})
    check("an untracked file", git_status.parse(z("?? tools/x.py")) == {"tools/x.py"})
    check("an ignored file", git_status.parse(z("!! tools/x.py")) == {"tools/x.py"})
    check("an unmerged file", git_status.parse(z("UU tools/x.py")) == {"tools/x.py"})
    check("several at once",
          git_status.parse(z(" M a.py", "?? b.py", "A  c.py")) == {"a.py", "b.py", "c.py"})
    check("nothing is nothing", git_status.parse("") == set())


def test_a_rename_is_two_paths() -> None:
    """The defect with teeth: `git mv` on a closure file left the registrar blind.

    Under -z a rename is TWO fields - the new name on the status record, the original name alone
    in the field that follows. Both are dirty: one gained content, one lost it. A parser that
    reads the second field as a status record would also lose its place for everything after it.
    """
    print("\n- a rename names both files -")
    got = git_status.parse(z("R  tools/beta.py", "tools/alpha.py"))
    check("the new name is dirty", "tools/beta.py" in got, repr(got))
    check("and so is the original", "tools/alpha.py" in got, repr(got))
    check("and nothing else", got == {"tools/alpha.py", "tools/beta.py"}, repr(got))
    got = git_status.parse(z("C  tools/copy.py", "tools/orig.py"))
    check("a copy names both too", got == {"tools/copy.py", "tools/orig.py"}, repr(got))
    got = git_status.parse(z("RM tools/beta.py", "tools/alpha.py", " M tools/other.py"))
    check("the parser keeps its place after a rename",
          got == {"tools/alpha.py", "tools/beta.py", "tools/other.py"}, repr(got))
    check("an arrow inside a name is part of the name, not a separator",
          git_status.parse(z(" M tools/a -> b.py")) == {"tools/a -> b.py"})


def test_paths_arrive_raw() -> None:
    """Under -z git neither quotes nor escapes, so the parser must not try to undo either."""
    print("\n- the path is the path -")
    check("a space needs no unquoting",
          git_status.parse(z(" M tools/with space.py")) == {"tools/with space.py"})
    check("a non-ascii name survives",
          git_status.parse(z(" M tools/note.py")) == {"tools/note.py"})
    check("a cyrillic name survives",
          git_status.parse(z(" M tools/zametka.py")) == {"tools/zametka.py"})
    check("a backslash in a name is not a path separator",
          git_status.parse(z(" M tools/a" + BACKSLASH + "b.py"))
          == {"tools/a" + BACKSLASH + "b.py"})
    check("a record too short to hold a path is not a path",
          git_status.parse(z(" M", "")) == set())


def test_against_a_real_repository() -> None:
    """Synthetic records prove the parser; a real `git mv` proves the whole path to git."""
    print("\n- and against git itself -")
    work = Path(tempfile.mkdtemp(prefix="git_status_"))
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    #: `--template=` is not decoration: this machine sets `init.templateDir` globally, so a plain
    #: `git init` copies the developer's own hooks into the fixture and the first commit fires
    #: them - the first draft of this check failed on a `graph.json` a post-commit hook had
    #: written into the temp repo. A test that inherits the developer's git config is not a test.
    subprocess.run(["git", "init", "-q", "--template=", "."], cwd=work, check=True,
                   capture_output=True)
    (work / "tools").mkdir()
    names = ("alpha.py", "with space.py", "заметка.py")
    for name in names:
        (work / "tools" / name).write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=work, check=True, capture_output=True,
                   env=env)
    check("a committed tree is clean", git_status.dirty_files(work) == set(),
          repr(git_status.dirty_files(work)))

    subprocess.run(["git", "mv", "tools/alpha.py", "tools/beta.py"], cwd=work, check=True,
                   capture_output=True)
    for name in names[1:]:
        (work / "tools" / name).write_text("x\ny\n", encoding="utf-8")
    got = git_status.dirty_files(work)
    want = {"tools/alpha.py", "tools/beta.py", "tools/" + names[1], "tools/" + names[2]}
    check("git mv makes BOTH names dirty", {"tools/alpha.py", "tools/beta.py"} <= got, repr(got))
    check("a name with a space is dirty under its own name",
          "tools/" + names[1] in got, repr(got))
    check("a non-ascii name is dirty under its own name", "tools/" + names[2] in got, repr(got))
    check("and those are all of them", got == want, repr(got - want) + " unexpected")

    #: the whole point, in the shape the registrars use it: `p in dirty` must match
    closure = ["tools/alpha.py", "tools/" + names[2]]
    check("a closure file renamed away is refused registration",
          sorted(p for p in closure if p in got) == sorted(closure), repr(got))


def test_no_registrar_kept_its_own_copy() -> None:
    """Ten copies were the reason one defect lived in ten places."""
    print("\n- one parser, not ten -")
    hand_rolled = []
    for path in sorted((ROOT / "tools").glob("*.py")):
        if path.name == "git_status.py":
            continue
        src = path.read_text(encoding="utf-8")
        if "--porcelain" in src and "line[3:]" in src:
            hand_rolled.append(path.name)
    check("no tool parses porcelain by hand any more", not hand_rolled, ", ".join(hand_rolled))
    users = sorted(p.name for p in (ROOT / "tools").glob("*.py")
                   if "git_status" in p.read_text(encoding="utf-8")
                   and p.name != "git_status.py")
    check("and every tool that asks about a dirty tree uses the shared one",
          len(users) >= 10, f"{len(users)}: {users}")

    #: The guard is only worth having where claims are written, so ask it of every registrar.
    #: This is how `register_divergence.py` was found: it stamped four LIVE claims onto HEAD
    #: with no dirty check at all, so the commit a claim names need not have contained the code
    #: that produced it.
    unguarded = [p.name for p in sorted((ROOT / "tools").glob("register_*.py"))
                 if "dirty" not in p.read_text(encoding="utf-8")]
    check("every registrar refuses to stamp a claim onto a dirty closure",
          not unguarded, ", ".join(unguarded))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_one_record_per_change,
               test_a_rename_is_two_paths,
               test_paths_arrive_raw,
               test_against_a_real_repository,
               test_no_registrar_kept_its_own_copy):
        fn()
    print(f"\ngit status parsing: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
