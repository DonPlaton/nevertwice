"""R1: the metrics a ratchet needs, and a way to tell whether they are the right ones.

Four numbers per callable and two per module, all from the standard library except the
import graph, which uses `networkx` because a hand-rolled traversal of a cyclic directed
graph is a bug waiting for a large enough repository.

| metric | what it is | why a ratchet wants it |
|---|---|---|
| cyclomatic | McCabe: 1 + the number of decisions | the count everyone means by "complexity" |
| nesting | deepest control structure | the number a reader actually feels |
| length | lines from `def` to the last statement | the crudest, and the hardest to argue with |
| returns | exit points | a function with nine of them has nine contracts |
| cycles | strongly connected components of the import graph | a cycle is a design fact, not a style opinion |
| fan-in / fan-out | in- and out-degree per module | how much a change here can cost |

## The metric has to be the metric everyone means

A ratchet whose complexity number disagrees with the tools everyone else runs measures its author's
taste. `cyclomatic()` is therefore checked against **`ruff`'s C901**, which implements
McCabe in Rust, by different people, for a different purpose. Agreement is not decoration:
it is what lets R3 compare this mechanism against an instrument it did not write.

`radon` was the baseline named in `PREREGISTRATION.md`; PyPI and GitHub were both
unreachable from this machine, and the substitution is logged in that document's §9 rather
than made quietly. `ruff` is a stronger choice for the same reason `radon` was chosen --
independence -- since it shares neither language nor authors with anything here.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

#: One decision point each, and this list is not a matter of taste: it is what the
#: `mccabe` package and `ruff`'s C901 count, verified case by case against `ruff` and
#: pinned in `tests/_test_complexity.py`. A ratchet whose complexity number disagrees
#: with every other tool measures its author's opinion.
_DECISION = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)

#: Deliberately NOT counted, because the standard does not count them: boolean
#: operators, ternaries, `assert`, comprehensions, `with`, `finally`, and a `class`
#: statement. Several of them feel like decisions and are not, which is exactly why
#: this is checked against another implementation rather than reasoned about.

#: What `PLR1702` counts as a nested block, and therefore what this counts. `ast.Match`
#: was here and is not: the instrument does not count it, and F3's whole point is that
#: the metric is the metric everyone means rather than this project's taste.
_NESTS = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith,
    ast.Try,
)


@dataclass(frozen=True)
class FuncMetrics:
    qualname: str
    lineno: int
    cyclomatic: int
    nesting: int
    length: int
    returns: int

    def worse_than(self, other: "FuncMetrics") -> list[str]:
        """Which axes got worse. Named, because "it got worse" is not actionable."""
        out = []
        for axis in ("cyclomatic", "nesting", "length", "returns"):
            if getattr(self, axis) > getattr(other, axis):
                out.append(axis)
        return out


def _parses(source: str) -> bool:
    try:
        ast.parse(source)
        return True
    except (SyntaxError, ValueError, RecursionError):
        return False


def cyclomatic(node: ast.AST) -> int:
    """McCabe complexity of one callable, as `mccabe` and `ruff` compute it.

    Three rules that are easy to get wrong and were got wrong here first, each fixed
    by disagreeing with `ruff` on a case written to isolate it:

    * a **nested `def` counts** -- one for the definition, plus everything in its body.
      A function that hides a branchy closure has not hidden it;
    * a `match` contributes **one per case beyond the first**, not one per case;
    * `and`, `or`, ternaries, `assert`, comprehensions, `with` and `finally` contribute
      **nothing**. Several of those feel like decisions. The standard disagrees, and
      the standard is the point.
    """
    total = 1
    stack = list(ast.iter_child_nodes(node))
    while stack:
        child = stack.pop()
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            total += 1
        elif isinstance(child, ast.Match):
            total += max(len(child.cases) - 1, 0)
        elif isinstance(child, _DECISION):
            total += 1
        elif isinstance(child, ast.Try) and child.orelse:
            total += 1
        stack.extend(ast.iter_child_nodes(child))
    return total


def _walk_own_scope(node: ast.AST):
    """Every descendant that is not inside a nested function or class."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        child = stack.pop()
        yield child
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(child))


def nesting(node: ast.AST) -> int:
    """Deepest control structure inside this callable, its own body counted as depth 0.

    Two rules that were wrong until `PLR1702` said so, and neither was visible by
    reading this function:

    * an **`elif` chain is one level, not one per branch**. In the AST an `elif` is an
      `If` inside the parent's `orelse`, so walking children charged a level per branch
      and read a flat three-way chain as depth 3. Every one of the audit's 133 nesting
      disagreements was this shape;
    * a **`match` does not count**. That one is a definitional difference rather than a
      defect -- a `match` is a branch and a reader does feel it -- and it is resolved the
      way R1 resolved the same question for cyclomatic: the metric has to be the metric
      everyone means, and the only reachable statement of what everyone means is the
      instrument.
    """
    def depth(n: ast.AST, d: int, *, in_orelse_of_if: bool = False) -> int:
        best = d
        for child in ast.iter_child_nodes(n):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            # `elif`: the sole statement of an `If`'s `orelse` being another `If` is
            # not a nested block, it is the next arm of the same branch. The AST is
            # *identical* for `elif x:` and for `else:` followed by an indented `if x:`,
            # so the column offset is what separates them -- an `elif` sits at its
            # parent's column, an indented `if` does not. Without the column test this
            # rule swallowed real nesting: `psf/requests`'s `Server.__exit__` scored 1
            # where both a reader and PLR1702 say 2.
            elif_arm = (isinstance(n, ast.If) and isinstance(child, ast.If)
                        and len(n.orelse) == 1 and n.orelse[0] is child
                        and child.col_offset == n.col_offset)
            step = 0 if elif_arm else (1 if isinstance(child, _NESTS) else 0)
            best = max(best, depth(child, d + step))
        return best
    return depth(node, 0)


def has_nested_callable(node: ast.AST) -> bool:
    """Does this callable define another callable inside itself?

    `PLR1702` counts a nested block chain **without resetting at a `def`**, and reports
    the whole chain at its outermost block. So for a callable that contains a nested
    function -- or one that *is* nested inside another callable's block chain -- ruff's
    output cannot be read back as this callable's own depth, in either direction. That
    is a genuine definitional difference and no amount of parsing recovers the number.

    `audit_axes.py` therefore compares `nesting` only on callables that neither contain
    nor sit inside another callable, and reports how many that excludes. F3's declared
    consequence -- drop what has no independent check -- is applied to the excluded set
    by `ratchet.py`, which does not hold `nesting` on a callable containing a nested
    `def`.
    """
    for child in _walk_own_scope(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return True
    return False


def auditable_nesting(source: str) -> set[str]:
    """Qualnames whose `nesting` value `PLR1702` can be compared against at all."""
    if not _parses(source):
        return set()
    tree = ast.parse(source)
    out: set[str] = set()
    duplicated = duplicate_qualnames(source)
    # `enclosed` is true once the walk has passed through another callable **or** a
    # block. A block matters for the same reason a callable does: `PLR1702` reports a
    # chain at its outermost block, so a `def` under `if sys.platform == "win32":` has
    # its blocks reported at the module-level `if`, outside the function entirely, and
    # ruff appears to say 0. `psf/requests` and `pytest` both use that idiom.
    stack: list[tuple[list[str], ast.AST, bool]] = [([], n, False) for n in tree.body]
    while stack:
        prefix, node, enclosed = stack.pop()
        if isinstance(node, _TRANSPARENT):
            stack.extend((prefix, c, True) for c in _children(node))
        elif isinstance(node, ast.ClassDef):
            stack.extend((prefix + [node.name], c, enclosed) for c in node.body)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = ".".join(prefix + [node.name])
            if (not enclosed and not has_nested_callable(node)
                    and qualname not in duplicated):
                out.add(qualname)
            stack.extend((prefix + [node.name], c, True) for c in node.body)
    return out


#: Statements whose bodies still run at import time, so a `def` inside one is an
#: ordinary definition. Missing these hid four classes behind the optional-dependency
#: idiom (`try: import x / except ImportError:`) -- the same blind spot D6 found in the
#: checker and in the answer key, in a third instrument.
_TRANSPARENT = (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.While)


def _children(node: ast.AST) -> list[ast.stmt]:
    body = list(getattr(node, "body", []))
    body += list(getattr(node, "orelse", []))
    body += list(getattr(node, "finalbody", []))
    for handler in getattr(node, "handlers", []):
        body += list(handler.body)
    return body


def scan_file(source: str) -> dict[str, FuncMetrics]:
    """Every top-level and method callable in one file, by qualname.

    A qualname can be defined more than once in a file -- `@overload` stubs before the
    real implementation, or one `def` per platform branch. This keys by qualname, so one
    of them wins, and until F3 the winner was decided by traversal order: `psf/requests`
    put `cookiejar_from_dict`'s `...` stub (nesting 0) where the real function's metrics
    (nesting 3) belonged. A ratchet storing that baseline compares next quarter's real
    function against this quarter's ellipsis.

    Python's own answer is that the **last** definition is the one that runs, so that is
    the one kept. `duplicate_qualnames()` reports which names had more than one, because
    a collision resolved silently is a collision nobody can audit.
    """
    if not _parses(source):
        return {}
    tree = ast.parse(source)
    out: dict[str, FuncMetrics] = {}
    stack: list[tuple[list[str], ast.AST]] = [([], n) for n in tree.body]
    while stack:
        prefix, node = stack.pop()
        if isinstance(node, _TRANSPARENT):
            stack.extend((prefix, c) for c in _children(node))
        elif isinstance(node, ast.ClassDef):
            stack.extend((prefix + [node.name], c) for c in node.body)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = ".".join(prefix + [node.name])
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            previous = out.get(qualname)
            if previous is not None and previous.lineno > node.lineno:
                stack.extend((prefix + [node.name], c) for c in node.body)
                continue  # a later definition already won; this one never runs
            out[qualname] = FuncMetrics(
                qualname=qualname,
                lineno=node.lineno,
                cyclomatic=cyclomatic(node),
                nesting=nesting(node),
                length=end - node.lineno + 1,
                returns=sum(1 for c in _walk_own_scope(node)
                            if isinstance(c, ast.Return)),
            )
            stack.extend((prefix + [node.name], c) for c in node.body)
    return out


def duplicate_qualnames(source: str) -> set[str]:
    """Qualnames this file defines more than once.

    Excluded from the `nesting` agreement audit: `PLR1702` reports per block, so its
    value for a duplicated name is the maximum across every definition, while this
    project keeps the last. The two are not the same question and comparing them would
    manufacture a disagreement out of an ambiguity.
    """
    if not _parses(source):
        return set()
    tree = ast.parse(source)
    seen: dict[str, int] = {}
    stack: list[tuple[list[str], ast.AST]] = [([], n) for n in tree.body]
    while stack:
        prefix, node = stack.pop()
        if isinstance(node, _TRANSPARENT):
            stack.extend((prefix, c) for c in _children(node))
        elif isinstance(node, ast.ClassDef):
            stack.extend((prefix + [node.name], c) for c in node.body)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = ".".join(prefix + [node.name])
            seen[qualname] = seen.get(qualname, 0) + 1
            stack.extend((prefix + [node.name], c) for c in node.body)
    return {q for q, n in seen.items() if n > 1}


# --------------------------------------------------------------------------
# the import graph
# --------------------------------------------------------------------------


def module_name(path: str) -> str:
    mod = path.replace("\\", "/").removesuffix(".py").replace("/", ".")
    return mod.removesuffix(".__init__")


def import_graph(sources: dict[str, str]) -> nx.DiGraph:
    """A directed graph of module -> the modules it imports, within this source set.

    Only edges to modules *present in the set* are kept. An edge to `os` would make
    every module in the world a node and every project cyclic through the standard
    library, which measures nothing about the project.
    """
    graph = nx.DiGraph()
    known = {module_name(p): p for p in sources if p.endswith(".py")}
    for name in known:
        graph.add_node(name)

    for path, src in sources.items():
        if not path.endswith(".py") or not _parses(src):
            continue
        me = module_name(path)
        for node in ast.walk(ast.parse(src)):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parent = me.rsplit(".", node.level)[0] if "." in me else ""
                    base = f"{parent}.{node.module}" if node.module else parent
                else:
                    base = node.module or ""
                targets = [base] + [f"{base}.{a.name}" for a in node.names if base]
            for target in targets:
                hit = _resolve(target, known)
                if hit and hit != me:
                    graph.add_edge(me, hit)
    return graph


def _resolve(target: str, known: dict[str, str]) -> str | None:
    """Longest known module that is a prefix of, or suffix-matches, the import target."""
    if target in known:
        return target
    parts = target.split(".")
    for i in range(len(parts) - 1, 0, -1):
        head = ".".join(parts[:i])
        if head in known:
            return head
    tail = parts[-1]
    matches = [k for k in known if k == tail or k.endswith("." + tail)]
    return min(matches, key=len) if len(matches) == 1 else None


def cycles(graph: nx.DiGraph) -> list[list[str]]:
    """Every import cycle, as sorted module lists, largest first.

    Strongly connected components rather than `simple_cycles`: a component is the unit a
    developer has to break, and enumerating every simple cycle inside one component
    produces thousands of restatements of a single fact.
    """
    out = [sorted(c) for c in nx.strongly_connected_components(graph) if len(c) > 1]
    out += [[n] for n in graph.nodes if graph.has_edge(n, n)]
    return sorted(out, key=lambda c: (-len(c), c))


def fan(graph: nx.DiGraph) -> dict[str, tuple[int, int]]:
    """(fan-in, fan-out) per module."""
    return {n: (graph.in_degree(n), graph.out_degree(n)) for n in graph.nodes}


# --------------------------------------------------------------------------
# the independent instrument
# --------------------------------------------------------------------------


def ruff_complexity(path: Path) -> dict[str, int] | None:
    """Per-function McCabe complexity according to `ruff`, or None if it is unavailable.

    `ruff check --select C901 --config "lint.mccabe.max-complexity = 0"` reports every
    function with its number, which is the whole point: not a pass/fail at some
    threshold, but the value itself, from an implementation written in another language
    by other people.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache",
             "--select", "C901", "--output-format", "concise",
             "--config", "lint.mccabe.max-complexity = 0", str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode not in (0, 1):
        return None
    out: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        if "C901" not in line or "is too complex" not in line:
            continue
        try:
            name = line.split("`")[1]
            number = int(line.split("(")[-1].split(">")[0].strip())
        except (IndexError, ValueError):
            continue
        out[name] = number
    return out


# --------------------------------------------------------------------------
# F3: independent instruments for the three axes `C901` cannot see
# --------------------------------------------------------------------------

#: `ruff`'s pylint-derived rules, each run with its threshold set to zero so the
#: message carries the *value* rather than a pass or a fail -- the same trick
#: `ruff_complexity` uses for C901. `RATCHET_R3.md` recorded nesting, returns and
#: length as unaudited; two of the three had an instrument on this machine all along.
_RUFF_AXIS_RULES = {
    "nesting": ("PLR1702", "lint.pylint.max-nested-blocks = 0"),
    "returns": ("PLR0911", "lint.pylint.max-returns = 0"),
    "statements": ("PLR0915", "lint.pylint.max-statements = 0"),
}


def _ruff_diagnostics(path: Path, rule: str, config: str) -> list[tuple[int, int]] | None:
    """`(lineno, value)` for each diagnostic of `rule`, or None if `ruff` did not run.

    None rather than an empty list, because a control that reports "nothing wrong" when
    it did not run is worse than no control: the agreement it is supposed to check
    would pass by default.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache",
             "--preview",       # PLR1702 is a preview rule in ruff 0.15
             "--ignore-noqa",   # a control a source comment can switch off is not a
                                # control: `psf/requests` carries `def proxy_bypass(
                                # ...):  # noqa`, and without this the instrument
                                # returned nothing there and the audit scored it as
                                # agreement
             "--select", rule, "--output-format", "concise",
             "--config", config, str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode not in (0, 1):
        return None
    # `path:LINE:COL: error[RULE] message (VALUE > 0)`. Matched with a regex rather
    # than split on ':' because on Windows the path starts `C:\`, and splitting gives
    # the drive letter -- a bug that returns an empty result and therefore an agreement
    # that passes by default.
    pattern = re.compile(r":(\d+):\d+:\s+\w+\[" + re.escape(rule) + r"\]")
    out: list[tuple[int, int]] = []
    for line in proc.stdout.splitlines():
        match = pattern.search(line)
        if match is None or "(" not in line:
            continue
        try:
            value = int(line.rsplit("(", 1)[1].split(">")[0].strip())
        except (IndexError, ValueError):
            continue
        out.append((int(match.group(1)), value))
    return out


def _def_lines(source: str) -> dict[int, str]:
    """`def` line -> qualname, so a diagnostic reported at a definition can be named."""
    return {m.lineno: q for q, m in scan_file(source).items()}


def _func_ranges(source: str) -> list[tuple[int, int, str]]:
    """`(start, end, qualname)` per callable, innermost last when sorted by width."""
    if not _parses(source):
        return []
    tree = ast.parse(source)
    out: list[tuple[int, int, str]] = []
    stack: list[tuple[list[str], ast.AST]] = [([], n) for n in tree.body]
    while stack:
        prefix, node = stack.pop()
        if isinstance(node, _TRANSPARENT):
            stack.extend((prefix, c) for c in _children(node))
        elif isinstance(node, ast.ClassDef):
            stack.extend((prefix + [node.name], c) for c in node.body)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = ".".join(prefix + [node.name])
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            out.append((node.lineno, end, qualname))
            stack.extend((prefix + [node.name], c) for c in node.body)
    return out


def _at_definition(path: Path, axis: str) -> dict[str, int] | None:
    """Axes `ruff` reports once per function, at the `def` line."""
    rule, config = _RUFF_AXIS_RULES[axis]
    diags = _ruff_diagnostics(path, rule, config)
    if diags is None:
        return None
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    names = _def_lines(source)
    return {names[ln]: v for ln, v in diags if ln in names}


def ruff_returns(path: Path) -> dict[str, int] | None:
    """Per-function return count according to `PLR0911`."""
    return _at_definition(path, "returns")


def ruff_statements(path: Path) -> dict[str, int] | None:
    """Per-function statement count according to `PLR0915`.

    Not an implementation of this project's `length`, which counts *lines* from `def` to
    the last statement. Statements and lines are different numbers about the same thing,
    so this supports a directional check and never an equality one. `AXES_F3.md` says so
    before the measurement rather than after it.
    """
    return _at_definition(path, "statements")


def ruff_nesting(path: Path) -> dict[str, int] | None:
    """Per-function nesting depth according to `PLR1702`.

    `PLR1702` reports once per nested *block group*, not once per function, so each
    diagnostic is attributed to the innermost callable whose line range contains it and
    the function's value is the maximum over its own blocks. Attributing to the innermost
    is what makes this comparable: `nesting()` stops at a nested `def`, and so must the
    instrument checking it.
    """
    rule, config = _RUFF_AXIS_RULES["nesting"]
    diags = _ruff_diagnostics(path, rule, config)
    if diags is None:
        return None
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    ranges = sorted(_func_ranges(source), key=lambda r: r[1] - r[0])
    out: dict[str, int] = {}
    for lineno, value in diags:
        owner = next((q for start, end, q in ranges if start <= lineno <= end), None)
        if owner is None:
            continue  # a module-level block belongs to no callable
        out[owner] = max(out.get(owner, 0), value)
    return out
