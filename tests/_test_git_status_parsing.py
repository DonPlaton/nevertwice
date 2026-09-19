#!/usr/bin/env python3
r"""What git actually says about this tree, and what ten copies of one parser heard.

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

The checks below ask behaviour, not spelling: the door is patched and the tools are watched for
whose answer changes; the refusal is found on the exit path rather than by grepping for the word
`dirty`; and the line-ending invariant is put to `git ls-files --eol` over the tracked tree
instead of to the writers' source. The first draft of each of those three asked the text, and the
text was true while the thing it stood for was not.

    python tests/_test_git_status_parsing.py
"""
from __future__ import annotations

import ast
import os
import re
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


REGISTRARS = tuple(sorted(p.stem for p in (ROOT / "tools").glob("register_*.py")))
TOOLS = REGISTRARS + ("remeasure",)
DOORS = {"_dirty_files", "dirty_files"}
SENTINEL = {"a/path/no/repository/has/ever/held.py"}


def test_every_tool_answers_through_the_shared_door() -> None:
    """Behaviour, not spelling: patch the door and see whose answer changes.

    The first draft of this check grepped for `line[3:]` and for the word `git_status` in the
    source. Both were true and both were the wrong question - the same question the harness
    classifier was rebuilt to stop asking an hour earlier. A tool that kept its own parser
    cannot return this sentinel, because the sentinel only exists inside the shared door.
    """
    print("\n- one parser, and the tools really go through it -")
    import importlib                                              # noqa: PLC0415

    real = git_status.dirty_files
    git_status.dirty_files = lambda *a, **k: set(SENTINEL)
    try:
        for name in TOOLS:
            mod = importlib.import_module(name)
            try:
                got = mod._dirty_files()
            except Exception as exc:                              # noqa: BLE001 - reported
                got = f"{type(exc).__name__}: {exc}"
            check(f"{name}._dirty_files answers through tools/git_status.py",
                  got == SENTINEL, repr(got))
    finally:
        git_status.dirty_files = real


def _nonzero_exit_conditions(fn: ast.AST) -> list[set[str]]:
    """Every name in the conditions guarding an exit that can be nonzero, per exit."""
    found: list[set[str]] = []

    def names(node: ast.AST) -> set[str]:
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | \
               {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}

    def walk(body: list[ast.stmt], guards: set[str]) -> None:
        for node in body:
            if isinstance(node, ast.If):
                walk(node.body, guards | names(node.test))
                walk(node.orelse, guards | names(node.test))
                continue
            for field in ("body", "orelse", "finalbody", "handlers"):
                inner = getattr(node, field, None)
                if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
                    walk(inner, guards)
            value = None
            if isinstance(node, ast.Return):
                #: Only a CONSTANT nonzero return is a refusal. Without that word the rule
                #: matched `_dirty_files`' own `return git_status.dirty_files(ROOT)` - a
                #: non-constant return, inside the door itself - and so passed every registrar
                #: whether or not it had a guard. Caught by mutation: stripping the guard out of
                #: `register_h2h` left the check green, which is how a rule that measures
                #: nothing announces itself.
                if not isinstance(node.value, ast.Constant):
                    continue
                value = node.value
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) \
                    and getattr(node.value.func, "attr", None) == "exit":
                value = node.value.args[0] if node.value.args else None
            elif isinstance(node, ast.Raise):
                #: `raise SystemExit(msg)` is a nonzero exit too, and it is how `register_k8`
                #: and `register_track_j` refuse - from inside a nested helper. A rule that knew
                #: only `return` called both of them unguarded, which is exactly what this suite
                #: reported the first time it ran: the rule was wrong, not the tools.
                exc = node.exc
                target = exc.func if isinstance(exc, ast.Call) else exc
                if getattr(target, "id", None) != "SystemExit" and \
                        getattr(target, "attr", None) != "exit":
                    continue
                found.append(guards | (names(exc) if exc is not None else set()))
                continue
            else:
                continue
            if isinstance(value, ast.Constant) and value.value in (0, None):
                continue
            found.append(guards | (names(value) if value is not None else set()))

    walk(getattr(fn, "body", []), set())
    return found


def _assignment_deps(fn: ast.AST) -> dict[str, set[str]]:
    """name -> the names it was built from.

    Attribute names count: `register_divergence` reaches the door as `git_status.dirty_files`
    with no local wrapper at all, and a Name-only reading called it unguarded while the
    behavioural check three functions down was watching it return 2.
    """
    deps: dict[str, set[str]] = {}
    for node in ast.walk(fn):
        if not isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None:
            continue
        src_names = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)} | \
                    {n.attr for n in ast.walk(value) if isinstance(n, ast.Attribute)}
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name):
                    deps.setdefault(n.id, set()).update(src_names)
    return deps


def _reaches(seed: set[str], deps: dict[str, set[str]]) -> set[str]:
    seen, stack = set(seed), list(seed)
    while stack:
        for nxt in deps.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def test_a_dirty_closure_can_reach_a_nonzero_exit() -> None:
    """The exit-path question, asked of the guard instead of the word "dirty".

    Grepping for `dirty` accepts a comment and rejects a tool that spells it otherwise. The rule
    the harness classifier was rebuilt on asks the exit path: take every exit that can be
    nonzero, take the names its value and every enclosing condition depend on - transitively,
    through assignments - and require `_dirty_files` among them. `register_k8` and
    `register_track_j` branch on a name two assignments away from it, which a one-step reading
    would miss and a spelling reading would pass for the wrong reason.
    """
    print("\n- a dirty closure reaches a nonzero exit, in every registrar -")
    for name in REGISTRARS:
        path = ROOT / "tools" / f"{name}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        deps = _assignment_deps(tree)      # module-wide: the refusal may be in a nested helper
        ok = any(DOORS & _reaches(cond, deps)
                 for fn in ast.walk(tree)
                 if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                 for cond in _nonzero_exit_conditions(fn))
        check(f"{name}: a nonzero exit depends on the dirty set", ok,
              "no exit that can be nonzero is guarded by anything derived from it")


def test_remeasure_refuses_per_claim_rather_than_per_process() -> None:
    """`remeasure.py` is the one tool whose refusal is not an exit code, so ask it its own way.

    It walks the register and restores each claim that can be restored. A claim whose
    `produced_by` holds an uncommitted file is left pending with a reason instead of being
    restored, and the process still ends 0 - so the exit-path rule above does not apply to it
    and forcing it to would only teach the rule to accept something weaker. Driven here with
    the door patched, which is the same behavioural question in the shape this tool answers it.
    """
    print("\n- remeasure leaves a claim pending instead of restoring it -")
    import importlib                                              # noqa: PLC0415

    rm = importlib.import_module("remeasure")
    claim = {"id": "probe.claim", "pending_remeasure": True, "raw": "research/results/x.json",
             "pointer": "a.b", "produced_by": ["research/probe_module.py"], "value": 1}
    real = git_status.dirty_files
    git_status.dirty_files = lambda *a, **k: {"research/probe_module.py"}
    try:
        restored, left, _review = rm.restore({"claims": [dict(claim)]}, dry_run=True)
    finally:
        git_status.dirty_files = real
    check("a claim whose closure is dirty is not restored", not restored, repr(restored))
    check("and it is left pending with the file named",
          any("probe_module.py" in reason for reason in left), repr(left))


def test_and_one_of_them_is_driven_all_the_way() -> None:
    """Static reasoning about an exit path is still reasoning. Drive one tool and read the code.

    `register_divergence` is the one that needs no artifact arguments, so it can be run here;
    `register_supersession` is driven the same way by its own suite. Both must exit 2.
    """
    print("\n- and the exit code really comes back -")
    import importlib                                              # noqa: PLC0415

    mod = importlib.import_module("register_divergence")
    real_argv, real_door = sys.argv, git_status.dirty_files
    everything = type("Everything", (), {"__contains__": lambda self, item: True,
                                         "__iter__": lambda self: iter(())})()
    sys.argv = ["register_divergence.py", "--dry-run"]
    git_status.dirty_files = lambda *a, **k: everything
    try:
        rc_dirty = mod.main()
    finally:
        git_status.dirty_files = real_door
        sys.argv = real_argv
    check("with every closure file dirty, register_divergence exits 2", rc_dirty == 2, rc_dirty)

    sys.argv = ["register_divergence.py", "--dry-run"]
    git_status.dirty_files = lambda *a, **k: set()
    try:
        rc_clean = mod.main()
    finally:
        git_status.dirty_files = real_door
        sys.argv = real_argv
    check("and with none of them dirty it gets on with its work", rc_clean == 0, rc_clean)


def test_every_tracked_file_has_the_line_endings_git_declares() -> None:
    """Ask git about the TREE, not the writers about their spelling.

    The first version of this check searched tool sources for a pinned `newline` argument. It
    was true and it measured the wrong thing: `.gitattributes` declares `* text=auto eol=lf`,
    and on 2026-09-19 `git ls-files --eol` reported **369** tracked files as `i/lf w/crlf` -
    the property held for the register alone, by hand. A file in that state is one no tool can
    leave clean: rewriting `research/results/draw_divergence.json` as CRLF put ` M` in
    `git status` with an empty `git diff`, which is the defect the owner named.

    It cost nothing to fix, because the INDEX side was right in all 369: re-checking them out
    changed no git object at all. What it did expose is that the H1 code freeze had recorded
    its digests from the drifted copies, so it would have been red on every clone.
    """
    print("\n- the worktree has the line endings the repository declares -")
    out = subprocess.run(["git", "ls-files", "--eol"], cwd=ROOT, capture_output=True,
                         check=True, text=True).stdout
    drifted = []
    for line in out.splitlines():
        spec, _, path = line.partition("\t")
        m = re.match(r"i/(\S+)\s+w/(\S+)\s+attr/(.*)$", spec.strip())
        if not m:
            continue
        index, work, attr = m.group(1), m.group(2), m.group(3).strip()
        if "-text" in attr or work in ("-text", "none"):
            continue                       # declared binary, or git detected binary content
        want = re.search(r"eol=(\w+)", attr)
        if want and work != want.group(1):
            drifted.append(f"{path} (w/{work}, declared {want.group(1)}, index {index})")
    check(f"no tracked file contradicts its declared eol ({len(drifted)} do)",
          not drifted, "; ".join(drifted[:6]))


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
               test_every_tool_answers_through_the_shared_door,
               test_a_dirty_closure_can_reach_a_nonzero_exit,
               test_remeasure_refuses_per_claim_rather_than_per_process,
               test_and_one_of_them_is_driven_all_the_way,
               test_every_tracked_file_has_the_line_endings_git_declares):
        fn()
    print(f"\ngit status parsing: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
