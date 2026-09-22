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


DOORS = {"_dirty_files", "dirty_files"}
SENTINEL = {"a/path/no/repository/has/ever/held.py"}
DOOR_MODULE = "git_status"
#: The git subcommands that answer "what in this tree is dirty" - the question the door exists
#: to answer once. `ls-files` is a listing and `rev-parse` an identity, so neither is here.
TREE_STATE = {"status", "diff-index", "diff-files"}
#: The one tool whose refusal is not an exit code, named together with the check that asks it
#: its own way. An exemption is a declared debt, not a silent gap: the name must still be a
#: discovered tool, and the check that stands in for the rule must exist.
EXIT_CODE_EXEMPT = {"remeasure": "test_remeasure_refuses_per_claim_rather_than_per_process"}


def _imports_the_door(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == DOOR_MODULE for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == DOOR_MODULE:
            return True
    return False


def _git_argvs(path: Path) -> list[list[str]]:
    """The literal words of every call that is plainly an invocation of git.

    Both shapes in this repository: `subprocess.run(["git", "status", ...])`, where the
    program names itself in the argv, and a module's own `_git("status", ...)` helper, where
    it names itself in the callee. A subcommand is data the program hands to git, so asking
    which one it hands over is a question about the program and not about its spelling.
    """
    out: list[list[str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        words: list[str] = []
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                words.append(arg.value)
            elif isinstance(arg, (ast.List, ast.Tuple)):
                words += [e.value for e in arg.elts
                          if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        callee = getattr(node.func, "id", None) or getattr(node.func, "attr", "") or ""
        if words and ("git" in words or "git" in callee):
            out.append(words)
    return out


#: Discovered, not listed. The first version of this line was `glob("register_*.py")` plus the
#: string "remeasure", so a tool that needed the door under any other name fell outside the
#: question entirely - the owner's third remark on `8f9c94b`. A module joins this set by
#: importing the door, which is a fact about the program rather than about its name; a module
#: that wants the tree's state WITHOUT importing the door is caught by
#: `test_nothing_else_asks_git_what_is_dirty`. Together the two are a cover. Either alone is
#: a list.
TOOLS = tuple(sorted(p.stem for p in (ROOT / "tools").glob("*.py")
                     if p.stem != DOOR_MODULE and _imports_the_door(p)))
REGISTRARS = tuple(n for n in TOOLS if n not in EXIT_CODE_EXEMPT)


def test_the_set_of_tools_is_discovered_and_not_spelled() -> None:
    """A rule over an empty set passes. Ask what the discovery found before trusting it.

    This suite learned that lesson from its own exit-path rule, which was vacuous for an hour
    and said so only under mutation. So: the discovery must find something, it must find
    everything that looks like a registrar, and every exemption must name a tool that exists
    and a check that exists.
    """
    print("\n- the tools are found, not listed -")
    named = sorted(p.stem for p in (ROOT / "tools").glob("register_*.py"))
    check(f"the discovery found tools at all ({len(TOOLS)})", len(TOOLS) >= 11, str(TOOLS))
    missed = [n for n in named if n not in TOOLS]
    check("and every register_*.py is among them - none escaped the door", not missed,
          str(missed))
    for name, replacement in sorted(EXIT_CODE_EXEMPT.items()):
        check(f"the exemption for {name} names a tool that exists", name in TOOLS, str(TOOLS))
        check(f"and {replacement} is a check in this suite", replacement in globals())


def test_nothing_else_asks_git_what_is_dirty() -> None:
    """The other half of the cover: a second parser cannot exist to be forgotten.

    Ten copies of one parser is what this suite was written for, and the copies were found
    because they shared a name prefix. The durable form of that question is not "did every
    registrar go through the door" but "did anything else ask git the question at all".
    Scoped to the evidence-producing side, `tools/` and `research/`.

    `nevertwice/sync.py` runs `git status --porcelain` and is deliberately out of scope: it
    tests the whole output for emptiness before committing the store and never takes a path
    out of it, so no parser lives there to go wrong.
    """
    print("\n- and nobody else asks git the question -")
    offenders = []
    for path in sorted((ROOT / "tools").glob("*.py")) + sorted((ROOT / "research").glob("*.py")):
        if path.stem == DOOR_MODULE:
            continue
        for argv in _git_argvs(path):
            hit = TREE_STATE & set(argv)
            if hit:
                offenders.append(f"{path.name}: git {sorted(hit)[0]}")
    #: A sweep that discovers nothing reports no offenders, which reads exactly like a
    #: sweep that found none. Pinned so the discovery has to keep working. Same class as
    #: `1ef491c`; found by a third signature over offender checks whose loop iterates a
    #: FILE DISCOVERY and whose size nothing asserts, 2026-09-22.
    _swept = len(sorted((ROOT / "tools").glob("*.py")) + sorted((ROOT / "research").glob("*.py")))
    check(f"the sweep sees the modules it polices ({_swept})", _swept >= 100, str(_swept))
    check(f"only tools/{DOOR_MODULE}.py asks git for the state of the tree", not offenders,
          "; ".join(offenders[:6]))


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


def _refuses_on_a_dirty_tree(tree: ast.AST) -> bool:
    """Does some exit that can be nonzero depend on the dirty set? The whole rule, in one place.

    Module-wide dependencies on purpose: the refusal may be raised from a nested helper two
    assignments away from the door.
    """
    deps = _assignment_deps(tree)
    return any(DOORS & _reaches(cond, deps)
               for fn in ast.walk(tree)
               if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
               for cond in _nonzero_exit_conditions(fn))


#: Fixtures for the rule itself. Written as sources rather than files because the property
#: being checked is the rule's own behaviour, and a rule tested only against code that
#: satisfies it is a rule that has never been observed to say no.
RULE_FIXTURES = (
    ("a constant nonzero return under a check on the dirty set", True, """
def main():
    dirty = sorted(p for p in closure(COMMAND) if p in _dirty_files())
    if dirty:
        print("working tree modifies " + dirty[0])
        return 2
    return 0
"""),
    ("the same tool with its refusal removed", False, """
def main():
    dirty = sorted(p for p in closure(COMMAND) if p in _dirty_files())
    print(dirty)
    return 0
"""),
    ("a SystemExit raised from a nested helper", True, """
def _load():
    bad = [p for p in closure(COMMAND) if p in _dirty_files()]
    if bad:
        raise SystemExit("commit first: " + bad[0])

def main():
    _load()
    return 0
"""),
    ("a return whose value is computed, not constant", False, """
def _dirty_files():
    return git_status.dirty_files(ROOT)

def main():
    return _dirty_files()
"""),
    ("a refusal that has nothing to do with the tree", False, """
def main():
    if not ARTIFACT.exists():
        return 2
    return 0
"""),
)


def test_the_exit_path_rule_errs_on_the_red_side() -> None:
    """The rule's conservatism, stated as a property instead of surviving as a side effect.

    `_nonzero_exit_conditions` counts only a CONSTANT nonzero return. That word arrived as a
    repair - without it the rule matched `_dirty_files`' own `return git_status.dirty_files(
    ROOT)` and passed every registrar, guarded or not - and a repair nobody wrote down is a
    repair the next edit removes. So it is written down here, in the direction it errs:

      a tool that refuses in a way this rule cannot see is reported UNGUARDED and read by a
      human, which costs an argument; a tool that does not refuse at all must never be
      reported guarded, which would cost a claim recorded against a tree nobody checked.

    The second fixture is the mutation that exposed the vacuous version, kept standing rather
    than performed by hand once.
    """
    print("\n- the rule says no when it should, including to itself -")
    for label, want, src in RULE_FIXTURES:
        got = _refuses_on_a_dirty_tree(ast.parse(src))
        check(f"{label} -> {'guarded' if want else 'not guarded'}", got == want, repr(got))


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
        tree = ast.parse((ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8"))
        check(f"{name}: a nonzero exit depends on the dirty set",
              _refuses_on_a_dirty_tree(tree),
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
               test_the_set_of_tools_is_discovered_and_not_spelled,
               test_nothing_else_asks_git_what_is_dirty,
               test_every_tool_answers_through_the_shared_door,
               test_the_exit_path_rule_errs_on_the_red_side,
               test_a_dirty_closure_can_reach_a_nonzero_exit,
               test_remeasure_refuses_per_claim_rather_than_per_process,
               test_and_one_of_them_is_driven_all_the_way,
               test_every_tracked_file_has_the_line_endings_git_declares):
        fn()
    print(f"\ngit status parsing: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
