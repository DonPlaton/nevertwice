"""X1-X3: declared growth axes, static quadratic detection, and the 10x canary.

The mechanism's premise, and the reason it is different from the other two:

> A project declares its growth axes -- *"this dataset goes from 1 GB to 500 GB"*. Without a
> declared axis there is nothing to check, and that is **correct behaviour rather than a gap**.

That single design choice is what makes silence the default instead of something to be
engineered afterwards. `blast_radius` and the ratchet both fire on every repository they are
pointed at, and both failed on flag rate. This one is quiet on every project that has not asked
it a question.

## The three parts

**X1, the declaration.** A named axis, what it is measured in, and the range it is expected to
cross. `records: 1_000 -> 50_000_000` is a claim about the future that the code can be held to.

**X2, static detection.** A loop over a declared axis whose body loops over the same axis again,
or which materialises the whole axis into memory. Both are decidable from the AST, and both
are asked *only about names the declaration mentions*, which is why the false-positive rate can
be low without any cleverness.

**X3, the canary.** Run the same test at 1x and 10x and assert that time and memory did not
blow up. This is the part that converts an unfalsifiable *"it should scale"* into a boolean,
and it is the only mechanism in this lab that observes the program running rather than reading
it.
"""

from __future__ import annotations

import ast
import gc
import time
import tracemalloc
from dataclasses import dataclass, field
from typing import Callable

from invariant_notes import Finding

#: Builders that pull an entire iterable into memory at once. `sum`, `any`, `all`, `min`
#: and `max` are deliberately absent: they consume lazily and are the right answer.
_MATERIALISERS = {"list", "tuple", "set", "dict", "sorted", "frozenset"}

#: Methods that read a whole stream. `readline` and `iter` are absent for the same reason.
_SLURPS = {"readlines", "read", "fetchall"}


# --------------------------------------------------------------------------
# X1: the declaration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Axis:
    """One growth axis a project has committed to."""

    name: str            # the identifier that carries it in code
    unit: str            # records, bytes, files...
    start: int
    target: int

    @property
    def factor(self) -> float:
        return self.target / self.start if self.start else float("inf")


@dataclass
class Declaration:
    axes: list[Axis] = field(default_factory=list)

    @classmethod
    def parse(cls, data: dict) -> "Declaration":
        """From the note's payload. A malformed axis is dropped, not guessed at."""
        out: list[Axis] = []
        for entry in data.get("axes", []):
            try:
                out.append(Axis(str(entry["name"]), str(entry.get("unit", "")),
                                int(entry["start"]), int(entry["target"])))
            except (KeyError, TypeError, ValueError):
                continue
        return cls(out)

    def names(self) -> set[str]:
        return {a.name for a in self.axes}


# --------------------------------------------------------------------------
# X2: static detection, and only along a declared axis
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScaleProblem:
    path: str
    lineno: int
    axis: str
    kind: str          # quadratic | materialise
    detail: str


def _iterated_name(node: ast.AST) -> str | None:
    """The identifier a `for` walks over, if it is a plain name or `x.attr`."""
    target = node
    if isinstance(target, ast.Call):
        target = target.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def find_problems(source: str, path: str, declaration: Declaration) -> list[ScaleProblem]:
    """Quadratic walks and whole-axis materialisation, along declared axes only.

    Asking the question only about names the declaration mentions is what keeps this
    quiet. A nested loop over two small lists is ordinary code; a nested loop over the
    axis that is going to grow five thousandfold is a fault that has not happened yet.
    """
    names = declaration.names()
    if not names:
        return []
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return []

    problems: list[ScaleProblem] = []
    for outer in ast.walk(tree):
        if not isinstance(outer, (ast.For, ast.AsyncFor)):
            continue
        outer_axis = _iterated_name(outer.iter)
        if outer_axis not in names:
            continue
        for inner in ast.walk(outer):
            if inner is outer:
                continue
            if isinstance(inner, (ast.For, ast.AsyncFor)):
                if _iterated_name(inner.iter) == outer_axis:
                    problems.append(ScaleProblem(
                        path, inner.lineno, outer_axis, "quadratic",
                        f"a loop over {outer_axis!r} inside a loop over {outer_axis!r}"))
            elif isinstance(inner, ast.Compare):
                for op, comparator in zip(inner.ops, inner.comparators):
                    if isinstance(op, (ast.In, ast.NotIn)) and \
                            _iterated_name(comparator) == outer_axis:
                        problems.append(ScaleProblem(
                            path, inner.lineno, outer_axis, "quadratic",
                            f"a membership test against {outer_axis!r} inside a loop "
                            f"over it; a set costs the same once"))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        called = fn.id if isinstance(fn, ast.Name) else (
            fn.attr if isinstance(fn, ast.Attribute) else None)
        if called in _MATERIALISERS and node.args:
            axis = _iterated_name(node.args[0])
            if axis in names:
                problems.append(ScaleProblem(
                    path, node.lineno, axis, "materialise",
                    f"{called}({axis}) pulls the whole axis into memory"))
        elif called in _SLURPS and isinstance(fn, ast.Attribute):
            axis = _iterated_name(fn.value)
            if axis in names:
                problems.append(ScaleProblem(
                    path, node.lineno, axis, "materialise",
                    f"{axis}.{called}() reads the whole axis at once"))
    return problems


def checker(before: dict[str, str], after: dict[str, str], note: dict) -> list[Finding]:
    """The `invariant_notes` interface. No declared axis, no finding, and that is correct."""
    declaration = Declaration.parse(note.get("declaration") or {})
    if not declaration.axes:
        return []
    out: list[Finding] = []
    for path, src in after.items():
        if not path.endswith(".py"):
            continue
        was = {(p.lineno, p.kind, p.detail)
               for p in find_problems(before.get(path, ""), path, declaration)}
        for problem in find_problems(src, path, declaration):
            if (problem.lineno, problem.kind, problem.detail) in was:
                continue          # not introduced here
            out.append(Finding(
                note["id"],
                note.get("message") or "this grows quadratically along a declared axis",
                subject=f"{problem.path}:{problem.lineno} [{problem.axis}]",
                rank=1.0 if problem.kind == "quadratic" else 0.7,
                evidence=problem.detail,
            ))
    return out


# --------------------------------------------------------------------------
# X3: the canary
# --------------------------------------------------------------------------


#: A run shorter than this cannot be timed against scheduler noise, so the canary
#: grows the input rather than reporting a verdict it did not earn.
MEASURABLE = 0.005


@dataclass(frozen=True)
class CanaryResult:
    ok: bool
    time_factor: float
    memory_factor: float
    detail: str
    abstained: bool = False


def canary(run: Callable[[int], object], n: int = 200, multiplier: int = 10, *,
           time_budget: float = 3.0, memory_budget: float = 3.0,
           repeats: int = 3, max_n: int = 2_000_000) -> CanaryResult:
    """Run `run(n)` and `run(n * multiplier)`; did time or memory blow up?

    A linear function takes about `multiplier` times as long; a quadratic one takes
    `multiplier ** 2`. The budget sits between them -- 3.0 against a 10x step -- so the
    test is not "is it fast" but "did the cost grow faster than the input", which is the
    only question that has a stable answer across machines.

    Timed as the **minimum of `repeats`** rather than the mean: the minimum is the run
    least disturbed by whatever else the machine was doing, and a scheduler hiccup
    inflating a mean is exactly how a flaky assertion gets deleted.
    """
    def measure(size: int) -> tuple[float, int]:
        best_t = float("inf")
        peak = 0
        for _ in range(repeats):
            gc.collect()
            tracemalloc.start()
            start = time.perf_counter()
            run(size)
            elapsed = time.perf_counter() - start
            _current, this_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            best_t = min(best_t, elapsed)
            peak = max(peak, this_peak)
        return best_t, peak

    # A run too fast to time says nothing about its own growth. The first version
    # abstained here and reported ok=True, which scored an abstention as a pass -- the
    # canary was silent on 12 of 40 quadratics purely because they finished in under
    # 0.1 ms. Sizing up until the base run is measurable is the mechanism's job, not
    # the caller's: a scale test that only works if you guess n correctly is a scale
    # test that reports whatever you guessed.
    size = n
    small_t, small_m = measure(size)
    while small_t < MEASURABLE and size * multiplier <= max_n:
        size *= 2
        small_t, small_m = measure(size)
    if small_t < MEASURABLE:
        return CanaryResult(
            True, float("nan"), float("nan"),
            f"still under {MEASURABLE * 1000:g} ms at n={size}; nothing to measure",
            abstained=True)

    big_t, big_m = measure(size * multiplier)
    tf = big_t / small_t / multiplier
    mf = (big_m / max(small_m, 1)) / multiplier
    ok = tf <= time_budget and mf <= memory_budget
    return CanaryResult(
        ok, tf, mf,
        f"at n={size}: time grew {tf:.2f}x and memory {mf:.2f}x faster than the input "
        f"(budget {time_budget:g}x / {memory_budget:g}x)",
    )
