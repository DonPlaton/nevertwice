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

_NESTS = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith,
    ast.Try, ast.Match,
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
    """Deepest control structure inside this callable, its own body counted as depth 0."""
    def depth(n: ast.AST, d: int) -> int:
        best = d
        for child in ast.iter_child_nodes(n):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            step = 1 if isinstance(child, _NESTS) else 0
            best = max(best, depth(child, d + step))
        return best
    return depth(node, 0)


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
    """Every top-level and method callable in one file, by qualname."""
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
