"""Scope a checker to a surface, and keep the word "declared" honest.

`SCALE_X4.md` is the only mechanism that passed its gates on non-circular evidence, and it
is quiet for one reason: it answers **only what a project asked**. A note says
`records: 1_000 -> 50_000_000` and the detector speaks about that axis and nothing else.
0.073 against the ratchet's 0.364, on the same commits.

F2 asks for the same shape here. The census in `SURFACE_F2.md` §1 says the shape is not
available: of 859 confirmed positives, **three** name a symbol its defining module lists in
`__all__`. Found history does not contain a contract declaration, so what this module
mostly implements are **conventions** -- and it keeps them labelled, because a convention
promoted to a declaration is exactly the failure F2 is about.

| surface | rule | kind |
|---|---|---|
| `everything` | every symbol in every changed file | the D5 baseline |
| `public-by-convention` | no component of the qualname is a single-underscore name | convention |
| `shipped-consumers` | the referencing file is shipped code | convention |
| `both` | the two above | convention |
| `declared-public` | the defining module's `__all__` names the symbol | **declaration** |

Like `abstain`, nothing here may consult the answer key; `tests/_test_surface.py` asserts
it statically.
"""

from __future__ import annotations

import ast

from abstain import Finding

SURFACES: tuple[str, ...] = (
    "everything",
    "public-by-convention",
    "shipped-consumers",
    "both",
    "declared-public",
)

#: The surfaces that are conventions. Named so the tables cannot quietly call one a
#: declaration, which is the failure mode F2 exists to describe.
CONVENTIONS: tuple[str, ...] = ("public-by-convention", "shipped-consumers", "both")

#: The one surface that is an actual declaration by the project being checked.
DECLARED: tuple[str, ...] = ("declared-public",)

#: `AXES_F3.md` §3's path rule, shared so the two tasks scope by the same definition of
#: "shipped". Written before either measurement so it cannot be tuned to a number.
NOT_SHIPPED = ("test", "tests", "testing", "docs", "doc", "examples", "example",
               "benchmarks", "bench", "scripts", "tools", ".github")


def is_shipped(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    name = parts[-1]
    if name in ("setup.py", "conftest.py") or name.startswith("test_"):
        return False
    if name.endswith("_test.py"):
        return False
    return not any(p in NOT_SHIPPED for p in parts[:-1])


def is_public_by_convention(qualname: str) -> bool:
    """No component is a single-underscore private name.

    A dunder is not private -- `__init__` starts with an underscore and is the most public
    thing a class has -- and a private *owner* makes its members private too, because a
    caller that cannot legitimately reach `_Engine` cannot legitimately reach
    `_Engine.deliver_now` either.
    """
    for part in qualname.split("."):
        if part.startswith("__") and part.endswith("__"):
            continue
        if part.startswith("_"):
            return False
    return True


def module_declares(source: str, symbol: str) -> bool:
    """Does this module's `__all__` name `symbol`?

    A module with no `__all__` declares nothing, so it is out of scope rather than in it
    by default. That asymmetry is the point: a declared surface is what a project wrote
    down, and silence is not a declaration.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return False
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if not (isinstance(target, ast.Name) and target.id == "__all__"):
                continue
            value = getattr(node, "value", None)
            if isinstance(value, (ast.List, ast.Tuple)):
                return any(isinstance(e, ast.Constant) and e.value == symbol
                           for e in value.elts)
            return False
    return False


def _keep(finding: Finding, sources: dict[str, str], changes: dict,
          surface: str) -> bool:
    if surface == "everything":
        return True
    if surface == "public-by-convention":
        return is_public_by_convention(finding.qualname)
    if surface == "shipped-consumers":
        return is_shipped(finding.path)
    if surface == "both":
        return (is_public_by_convention(finding.qualname)
                and is_shipped(finding.path))
    # declared-public
    change = changes.get(finding.qualname)
    if change is None:
        return False
    if "." in finding.qualname:
        # `__all__` names module-level exports. A method is reached through its class,
        # so the class is what has to be declared.
        head = finding.qualname.split(".")[0]
    else:
        head = finding.qualname
    return module_declares(sources.get(change.path, ""), head)


def apply(findings: list[Finding], sources: dict[str, str], changes: dict,
          surface: str) -> list[Finding]:
    """The findings that survive `surface`.

    `changes` maps qualname -> `ContractChange`, so the defining module can be found for
    the one surface that has to read it.
    """
    if surface not in SURFACES:
        raise ValueError(
            "unknown surface " + repr(surface) + "; the grid is "
            + ", ".join(SURFACES) + ". Falling back to `everything` would report a "
            "scoped row with the unscoped row's numbers."
        )
    return [f for f in findings if _keep(f, sources, changes, surface)]
