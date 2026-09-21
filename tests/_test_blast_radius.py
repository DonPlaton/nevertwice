#!/usr/bin/env python3
"""Tests for the blast-radius structural invariant.

Standard library only, no network, no model, no git required: the core takes
``before``/``after`` source maps directly, so every case here is in-memory.

Lives at ``tests/_test_blast_radius.py``. The integration spec placed it at
``nevertwice/_test_blast_radius.py`` and argued that CI globbed
``nevertwice/_test_*.py``; that stopped being true at ``05cfdc9``, which moved
every suite under ``tests/``. Following the spec literally today would put this
file where neither ``.github/workflows/ci.yml`` (``for t in tests/_test_*.py``)
nor ``tests/test_self_checks.py`` would ever run it - inverting the spec's own
reasoning. The path is reconciled with the layout; the reasoning is kept.

Run:  python tests/_test_blast_radius.py
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before anything imports the package

# Load the module by path so this test never depends on package __init__
# side effects, import order, or the repo being installed. The sys.modules
# registration before exec_module is required: dataclasses resolves field
# types through it, and without it @dataclass raises on load.
_MODULE_PATH = HERE.parent / "research" / "invariants_lab" / "blast_radius_deleted.py"
_spec = importlib.util.spec_from_file_location("_nt_blast_radius", _MODULE_PATH)
assert _spec and _spec.loader
br = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = br
_spec.loader.exec_module(br)


LIB_BEFORE = """\
def train_step(model, batch):
    return model(batch)
"""

LIB_AFTER = """\
def train_step(model, batch, accum):
    return model(batch) * accum
"""

CALLER_STALE = """\
from lib import train_step


def loop(model, batches):
    for b in batches:
        train_step(model, b)
"""

CALLER_UPDATED = """\
from lib import train_step


def loop(model, batches):
    for b in batches:
        train_step(model, b, 1)
"""


class SymbolExtraction(unittest.TestCase):
    def test_signature_captures_parameter_names(self):
        a = br.extract_symbols("def f(x): pass")["f"]
        b = br.extract_symbols("def f(y): pass")["f"]
        self.assertNotEqual(a.signature, b.signature)

    def test_signature_captures_default_values(self):
        a = br.extract_symbols("def f(lr=0.001): pass")["f"]
        b = br.extract_symbols("def f(lr=0.01): pass")["f"]
        self.assertNotEqual(
            a.signature, b.signature, "a changed default silently changes every caller"
        )

    def test_signature_captures_keyword_only(self):
        a = br.extract_symbols("def f(x): pass")["f"]
        b = br.extract_symbols("def f(x, *, strict): pass")["f"]
        self.assertNotEqual(a.signature, b.signature)

    def test_signature_captures_decorators(self):
        a = br.extract_symbols("class C:\n    def f(self): pass")["C.f"]
        b = br.extract_symbols("class C:\n    @property\n    def f(self): pass")["C.f"]
        self.assertNotEqual(a.signature, b.signature)

    def test_body_change_leaves_signature_alone(self):
        a = br.extract_symbols("def f(x):\n    return 1")["f"]
        b = br.extract_symbols("def f(x):\n    return 2")["f"]
        self.assertEqual(a.signature, b.signature)
        self.assertNotEqual(a.body_hash, b.body_hash)

    def test_reformatting_does_not_change_body_hash(self):
        a = br.extract_symbols("def f(x):\n    return x+1")["f"]
        b = br.extract_symbols("def f(x):\n    # a comment\n    return x + 1")["f"]
        self.assertEqual(a.body_hash, b.body_hash)

    def test_class_public_surface_is_part_of_contract(self):
        a = br.extract_symbols("class C:\n    def go(self): pass")["C"]
        b = br.extract_symbols("class C:\n    def go2(self): pass")["C"]
        self.assertNotEqual(a.signature, b.signature)

    def test_qualnames_are_nested(self):
        syms = br.extract_symbols("class C:\n    def m(self): pass")
        self.assertIn("C.m", syms)
        self.assertEqual(syms["C.m"].kind, "method")

    def test_module_constants_are_symbols(self):
        self.assertIn("MAX_BATCH", br.extract_symbols("MAX_BATCH = 64"))

    def test_local_variables_are_not_symbols(self):
        self.assertNotIn("tmp", br.extract_symbols("def f():\n    tmp = 1"))

    def test_syntax_error_returns_empty(self):
        self.assertEqual(br.extract_symbols("def ("), {})


class ChangedLines(unittest.TestCase):
    def test_single_line_edit(self):
        self.assertEqual(br.changed_lines("a\nb\nc\n", "a\nX\nc\n"), {2})

    def test_identical_text_has_no_changes(self):
        self.assertEqual(br.changed_lines("a\nb\n", "a\nb\n"), set())

    def test_insertion(self):
        self.assertEqual(br.changed_lines("a\nc\n", "a\nb\nc\n"), {2})


class ContractDiff(unittest.TestCase):
    def test_signature_change_is_reported(self):
        changes, _, _ = br.contract_changes(LIB_BEFORE, LIB_AFTER, "lib.py")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].reason, "signature")
        self.assertEqual(changes[0].qualname, "train_step")

    def test_body_only_change_is_not_a_contract_change(self):
        changes, _, body = br.contract_changes(
            "def f(x):\n    return 1", "def f(x):\n    return 2", "lib.py"
        )
        self.assertEqual(changes, [])
        self.assertIn("f", body)

    def test_removal_is_reported(self):
        changes, _, _ = br.contract_changes("def f(): pass", "", "lib.py")
        self.assertEqual([c.reason for c in changes], ["removed"])

    def test_addition_is_not_a_contract_change(self):
        changes, added, _ = br.contract_changes("", "def f(): pass", "lib.py")
        self.assertEqual(changes, [])
        self.assertEqual(added, {"f"})


class LargeFiles(unittest.TestCase):
    """D7. A pre-commit checker that stalls for minutes is not a pre-commit checker.

    `changed_lines` runs `difflib.SequenceMatcher` over the whole file. That is
    quadratic in practice: a 200,000-line changed file takes **over two minutes**,
    and the spec's own budget is a 2.00 s hard ceiling. It stayed invisible until a
    6 MB research artifact landed in the working tree and the live check stopped
    returning.

    Above the cap the diff is not computed at all -- every line is reported as
    changed, which is conservative for the size statistic and costs nothing for
    contract analysis, because a file that large is never a Python module whose
    signature anyone reads.
    """

    def test_a_huge_file_does_not_take_forever(self):
        import time
        old = (chr(10)).join(f"line {i}" for i in range(120_000))
        new = old.replace("line 5", "line five", 1)
        t0 = time.time()
        lines = br.changed_lines(old, new)
        elapsed = time.time() - t0
        self.assertLess(elapsed, 2.0, f"took {elapsed:.1f}s")
        self.assertTrue(lines)

    def test_a_huge_file_reports_every_line_as_changed(self):
        old = (chr(10)).join(str(i) for i in range(br.MAX_DIFF_LINES + 10))
        new = old + chr(10) + "extra"
        self.assertEqual(len(br.changed_lines(old, new)), len(new.splitlines()))

    def test_a_repetitive_file_is_not_diffed_precisely(self):
        """The real trigger is repetition, not length: a pretty-printed JSON is
        thousands of copies of the literal line `  },`."""
        import time
        old = (chr(10)).join("  }," if i % 3 else '  "k": 1,' for i in range(8_000))
        new = old.replace('  "k": 1,', '  "k": 2,', 1)
        t0 = time.time()
        lines = br.changed_lines(old, new)
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(len(lines), len(new.splitlines()))

    def test_a_long_but_distinct_file_is_still_diffed_precisely(self):
        """Source code is long and varied; it must not be approximated."""
        old = (chr(10)).join(f"x{i} = {i}" for i in range(8_000))
        new = old.replace("x4000 = 4000", "x4000 = 9999", 1)
        self.assertEqual(br.changed_lines(old, new), {4001})

    def test_a_normal_file_is_still_diffed_precisely(self):
        old = "a" + chr(10) + "b" + chr(10) + "c" + chr(10)
        new = "a" + chr(10) + "B" + chr(10) + "c" + chr(10)
        self.assertEqual(br.changed_lines(old, new), {2})

    def test_the_cap_is_high_enough_for_real_source(self):
        """No Python file in this repository comes close to the cap."""
        longest = max((ROOT / "nevertwice").rglob("*.py"),
                      key=lambda p: len(p.read_text(encoding="utf-8",
                                                    errors="replace").splitlines()))
        biggest = len(longest.read_text(encoding="utf-8", errors="replace").splitlines())
        self.assertLess(biggest * 4, br.MAX_DIFF_LINES)
        #: Found, not named. This used to spell `_engine.py`, and `_engine.py` is now a 70-line
        #: index over the parts that hold the body - the check would have gone on passing while
        #: measuring the smallest file in the package instead of the largest.
        source = longest.read_text(encoding="utf-8", errors="replace").splitlines()
        self.assertFalse(br._too_costly(source, source),
                         "the longest module in this repository must still be diffed")


class UnreadableFiles(unittest.TestCase):
    """D6. A file the checker cannot parse is a file it has nothing to say about.

    `extract_symbols` returns an empty map on a `SyntaxError`, so when the *after*
    side fails to parse, every symbol the *before* side defined looks removed. One
    flask commit that reintroduced a Python-2 `print` statement produced **30
    findings** this way, all of them for symbols still defined three lines below.

    Found by the silence pool: the checker fired on 29% of commits where a contract
    changed and nothing in the repository called it, and this was a large share of
    the reason. 5.43% of the corpus cannot be parsed by Python 3.14, so on a corpus
    that reaches back to 2005 this is not an edge case.
    """

    PY2 = "def f(a): pass" + chr(10) + "print 'oops'" + chr(10)

    def _changes(self, before: str, after: str):
        changes, _, _ = br.contract_changes(before, after, "lib.py")
        return sorted((c.qualname, c.reason) for c in changes)

    def test_an_unparseable_after_reports_nothing(self):
        self.assertEqual(self._changes("def f(a): pass" + chr(10), self.PY2), [])

    def test_an_unparseable_before_reports_nothing(self):
        self.assertEqual(self._changes(self.PY2, "def f(a): pass" + chr(10)), [])

    def test_both_sides_unparseable_reports_nothing(self):
        self.assertEqual(self._changes(self.PY2, self.PY2), [])

    def test_an_unparseable_after_does_not_chase_references(self):
        before = {"lib.py": "def f(a): pass" + chr(10) + "def g(a): pass" + chr(10)}
        after = {"lib.py": self.PY2}
        scan = dict(after)
        scan["app.py"] = "from lib import f, g" + chr(10) + "f(1)" + chr(10) + "g(1)" + chr(10)
        v = br.check_sources(before, after, scan=scan)
        self.assertEqual(v.contract_changes, [])
        self.assertEqual(v.unhandled, {})

    def test_the_checker_says_it_could_not_read_the_file(self):
        """Silence that nobody can see is indistinguishable from a clean bill."""
        before = {"lib.py": "def f(a): pass" + chr(10)}
        after = {"lib.py": self.PY2}
        v = br.check_sources(before, after, scan=after)
        self.assertTrue(any("could not be parsed" in n for n in v.notes), v.notes)

    def test_a_parseable_pair_is_unaffected(self):
        self.assertEqual(
            self._changes("def f(a): pass" + chr(10), "def f(a, b): pass" + chr(10)),
            [("f", "signature")])

    def test_a_real_removal_between_parseable_files_still_fires(self):
        self.assertEqual(
            self._changes("def f(a): pass" + chr(10), "def other(a): pass" + chr(10)),
            [("f", "removed")])


class ForeignReceivers(unittest.TestCase):
    """D4b. A defect the old census never saw, because 150 commits of one repository
    had no name collisions worth the name.

    `research/invariants_lab/facade_shapes.py` ran the checker over 1,118 corpus
    commits and found that **1,092 of 1,299** false high-confidence findings were
    one shape: a method contract change matched against an attribute call on a
    *different* class. `EmailBackend.__init__` changed, and every
    `MIMEText.__init__(...)` in the file was reported as a stale caller.

    The rule: an attribute call whose receiver is a name this file binds to some
    other class or module is a reference to that other thing. A receiver the file
    does not bind -- a parameter, a local, an attribute chain -- stays a candidate,
    because dropping those would trade this false positive for a recall hole.
    """

    def _unhandled(self, before: dict, after: dict, scan: dict | None = None):
        v = br.check_sources(before, after, scan=scan if scan is not None else after)
        return {q: [(r.path, r.lineno) for r in refs]
                for q, refs in v.unhandled.items()}

    OWNER_BEFORE = "class EmailBackend:\n    def send_now(self, a): pass\n"
    OWNER_AFTER = "class EmailBackend:\n    def send_now(self, a, b): pass\n"

    def test_an_attribute_call_on_a_different_imported_class_is_not_a_reference(self):
        scan = {"app.py": "from email import MIMEText\nMIMEText.send_now(1)\n"}
        after = {"lib.py": self.OWNER_AFTER}
        scan.update(after)
        self.assertEqual(self._unhandled({"lib.py": self.OWNER_BEFORE}, after, scan), {})

    def test_an_attribute_call_on_a_different_local_class_is_not_a_reference(self):
        scan = {"app.py": "class Other:\n    pass\nOther.send_now(1)\n"}
        after = {"lib.py": self.OWNER_AFTER}
        scan.update(after)
        self.assertEqual(self._unhandled({"lib.py": self.OWNER_BEFORE}, after, scan), {})

    def test_an_attribute_call_on_a_different_imported_module_is_not_a_reference(self):
        scan = {"app.py": "import other_mod\nother_mod.send_now(1)\n"}
        after = {"lib.py": self.OWNER_AFTER}
        scan.update(after)
        self.assertEqual(self._unhandled({"lib.py": self.OWNER_BEFORE}, after, scan), {})

    # --- and the references that must survive ------------------------------

    def test_an_attribute_call_on_the_owner_itself_is_still_a_reference(self):
        scan = {"app.py": "from lib import EmailBackend\nEmailBackend.send_now(1)\n"}
        after = {"lib.py": self.OWNER_AFTER}
        scan.update(after)
        self.assertEqual(list(self._unhandled({"lib.py": self.OWNER_BEFORE}, after, scan)),
                         ["EmailBackend.send_now"])

    def test_an_attribute_call_on_an_unbound_receiver_is_still_a_reference(self):
        """`backend.send_now(1)` -- a local or a parameter. Not statically knowable,
        and dropping it would trade a false positive for a recall hole."""
        scan = {"app.py": "def run(backend):\n    backend.send_now(1)\n"}
        after = {"lib.py": self.OWNER_AFTER}
        scan.update(after)
        self.assertEqual(list(self._unhandled({"lib.py": self.OWNER_BEFORE}, after, scan)),
                         ["EmailBackend.send_now"])

    def test_a_self_call_is_still_a_reference(self):
        scan = {"app.py": "class Sub(EmailBackend):\n    def go(self):\n        self.send_now(1)\n"}
        after = {"lib.py": self.OWNER_AFTER}
        scan.update(after)
        self.assertEqual(list(self._unhandled({"lib.py": self.OWNER_BEFORE}, after, scan)),
                         ["EmailBackend.send_now"])

    def test_a_module_level_function_is_not_filtered_by_receiver(self):
        """The rule applies to methods only; a module function has no owner class."""
        scan = {"app.py": "import other_mod\nother_mod.helper(1)\n"}
        after = {"lib.py": "def helper(a, b): pass\n"}
        scan.update(after)
        self.assertEqual(list(self._unhandled({"lib.py": "def helper(a): pass\n"},
                                              after, scan)),
                         ["helper"])


class RemainingFacadeShapes(unittest.TestCase):
    """D4. The 25% the census could not explain, and the shapes I2 did not solve.

    `research/BLAST_RADIUS_PRECISION.md` names two by hand and says there is "no
    reason to believe the list ends", so `research/invariants_lab/facade_shapes.py`
    reads the shapes off the corpus instead of off this repository's habits. The
    named two are pinned here, plus the corpus idioms the enumeration surfaced.
    """

    def _problems(self, before: dict, after: dict, scan: dict | None = None):
        v = br.check_sources(before, after, scan=scan if scan is not None else after)
        return sorted(v.unhandled), [c.qualname for c in v.contract_changes]

    def test_a_symbol_reached_as_a_module_attribute_is_not_stale(self):
        """The first named shape: `_st.est_tokens(...)` where `_st` imports it back."""
        before = {"facade.py": "def est_tokens(text): pass\n"}
        after = {"facade.py": "from real import est_tokens\n"}
        scan = dict(after)
        scan["real.py"] = "def est_tokens(text): pass\n"
        scan["app.py"] = "import facade as _st\n_st.est_tokens('x')\n"
        unhandled, changes = self._problems(before, after, scan)
        self.assertEqual(changes, [])
        self.assertEqual(unhandled, [])

    def test_an_import_reaching_past_the_module_the_symbol_left_is_not_stale(self):
        """The second: the caller imports from the facade, the symbol lives elsewhere."""
        before = {"facade.py": "def helper(a): pass\n"}
        after = {"facade.py": "from real import helper\n"}
        scan = dict(after)
        scan["real.py"] = "def helper(a): pass\n"
        scan["app.py"] = "from facade import helper\nhelper(1)\n"
        unhandled, changes = self._problems(before, after, scan)
        self.assertEqual(changes, [])
        self.assertEqual(unhandled, [])

    def test_a_relative_import_facade_is_not_a_removal(self):
        """The corpus idiom: `from ._real import helper` inside a package."""
        before = {"pkg/facade.py": "def helper(a): pass\n"}
        after = {"pkg/facade.py": "from ._real import helper\n"}
        scan = dict(after)
        scan["pkg/_real.py"] = "def helper(a): pass\n"
        unhandled, changes = self._problems(before, after, scan)
        self.assertEqual(changes, [])
        self.assertEqual(unhandled, [])

    def test_an_aliased_import_facade_is_not_a_removal(self):
        """`from x import math_reference as eqref  # to keep compatibility` -- real
        sphinx code, and the shape that fooled this project's own answer key."""
        before = {"m.py": "def eqref(node): pass\n"}
        after = {"m.py": "from nodes import math_reference as eqref\n"}
        scan = dict(after)
        scan["nodes.py"] = "def math_reference(node): pass\n"
        unhandled, changes = self._problems(before, after, scan)
        self.assertEqual(changes, [])
        self.assertEqual(unhandled, [])

    def test_a_facade_guarded_by_try_except_is_not_a_removal(self):
        """Optional-dependency idiom, everywhere in the corpus."""
        before = {"m.py": "def helper(a): pass\n"}
        after = {"m.py": "try:\n    from fast import helper\nexcept ImportError:\n    from slow import helper\n"}
        scan = dict(after)
        scan["fast.py"] = "def helper(a): pass\n"
        scan["slow.py"] = "def helper(a): pass\n"
        unhandled, changes = self._problems(before, after, scan)
        self.assertEqual(changes, [])
        self.assertEqual(unhandled, [])

    def test_a_symbol_that_really_left_with_no_facade_is_still_a_removal(self):
        before = {"m.py": "def helper(a): pass\n"}
        after = {"m.py": "def other(a): pass\n"}
        scan = dict(after)
        scan["app.py"] = "from m import helper\nhelper(1)\n"
        unhandled, changes = self._problems(before, after, scan)
        self.assertEqual(changes, ["helper"])
        self.assertEqual(unhandled, ["helper"])

    def test_a_facade_whose_target_changed_signature_is_still_a_change(self):
        """Following the pointer is the point: the move is safe, the rewrite is not."""
        before = {"m.py": "def helper(a): pass\n"}
        after = {"m.py": "from real import helper\n"}
        scan = dict(after)
        scan["real.py"] = "def helper(a, b): pass\n"
        _unhandled, changes = self._problems(before, after, scan)
        self.assertEqual(changes, ["helper"])


class AnnotationsAndClassMembers(unittest.TestCase):
    """D3. Two shapes of one mistake: the text of the contract changed and its
    runtime meaning did not.

    Annotation-only was 17% of the census's findings and a class gaining a member 4%.
    `_class_signature` renders bases plus the sorted public member list, so a class
    growing a method reads as a signature change -- which is exactly what a
    *compatible* change looks like.
    """

    def _changes(self, before: str, after: str):
        changes, _, _ = br.contract_changes(before, after, "lib.py")
        return sorted((c.qualname, c.reason) for c in changes)

    # --- annotations ------------------------------------------------------

    def test_a_parameter_annotation_appearing_is_not_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a): pass\n", "def f(a: int): pass\n"), [])

    def test_a_return_annotation_appearing_is_not_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a): pass\n", "def f(a) -> str: pass\n"), [])

    def test_an_annotation_changing_is_not_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a: int) -> str: pass\n",
                          "def f(a: float) -> bytes: pass\n"), [])

    def test_annotating_a_defaulted_parameter_is_not_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a, b=2): pass\n",
                          "def f(a: int, b: int = 2): pass\n"), [])

    def test_a_default_VALUE_changing_is_not_a_contract_change(self):
        """A caller that omitted it still binds; what it receives is behaviour."""
        self.assertEqual(
            self._changes("def f(a, b=2): pass\n", "def f(a, b=3): pass\n"), [])

    def test_an_annotated_module_constant_keeps_its_binding(self):
        self.assertEqual(
            self._changes("TIMEOUT = 5\n", "TIMEOUT: int = 5\n"), [])

    def test_an_annotation_added_to_a_method_is_not_a_contract_change(self):
        self.assertEqual(
            self._changes("class C:\n    def m(self, a): pass\n",
                          "class C:\n    def m(self, a: int) -> None: pass\n"), [])

    # --- class members ----------------------------------------------------

    def test_a_class_gaining_a_public_method_is_not_a_contract_change(self):
        self.assertEqual(
            self._changes("class C:\n    def a(self): pass\n",
                          "class C:\n    def a(self): pass\n    def b(self): pass\n"),
            [])

    def test_a_class_gaining_a_private_method_is_not_a_contract_change(self):
        self.assertEqual(
            self._changes("class C:\n    def a(self): pass\n",
                          "class C:\n    def a(self): pass\n    def _b(self): pass\n"),
            [])

    def test_a_class_LOSING_a_public_method_is_still_a_contract_change(self):
        self.assertEqual(
            self._changes("class C:\n    def a(self): pass\n    def b(self): pass\n",
                          "class C:\n    def a(self): pass\n"),
            [("C", "signature"), ("C.b", "removed")])

    def test_a_class_changing_its_bases_is_still_a_contract_change(self):
        self.assertEqual(
            self._changes("class C(A):\n    def a(self): pass\n",
                          "class C(B):\n    def a(self): pass\n"),
            [("C", "signature")])

    def test_a_class_gaining_a_base_is_still_a_contract_change(self):
        """isinstance and MRO are observable, so widening bases is not free."""
        self.assertEqual(
            self._changes("class C(A):\n    def a(self): pass\n",
                          "class C(A, B):\n    def a(self): pass\n"),
            [("C", "signature")])

    def test_a_class_gaining_a_decorator_is_still_a_contract_change(self):
        self.assertEqual(
            self._changes("class C:\n    def a(self): pass\n",
                          "@final\nclass C:\n    def a(self): pass\n"),
            [("C", "signature")])

    def test_a_class_gaining_a_member_does_not_chase_references(self):
        before = {"lib.py": "class C:\n    def a(self): pass\n",
                  "app.py": "from lib import C\nC()\nC()\n"}
        after = {"lib.py": "class C:\n    def a(self): pass\n    def b(self): pass\n",
                 "app.py": "from lib import C\nC()\nC()\n"}
        v = br.check_sources(before, after, scan=after)
        self.assertEqual(v.contract_changes, [])
        self.assertEqual(v.unhandled, {})


class TupleUnpackingRebinds(unittest.TestCase):
    """D2. `visit_Assign` recorded only `ast.Name` targets, so `A, B, C = load()`
    bound nothing and the three names looked *removed* at the next commit that kept
    them.

    21% of the census's findings, and the write-up calls it "a plain defect". The
    answer key in `research/invariants_lab/sigscan.py` has walked tuple targets from
    its first line of code precisely so this hole is in one instrument, not both.
    """

    def _names(self, src: str):
        return sorted(br.extract_symbols(src))

    def _changes(self, before: str, after: str):
        changes, _, _ = br.contract_changes(before, after, "lib.py")
        return sorted((c.qualname, c.reason) for c in changes)

    def test_a_tuple_target_binds_every_name(self):
        self.assertEqual(self._names("A, B, C = load()\n"), ["A", "B", "C"])

    def test_a_list_target_binds_every_name(self):
        self.assertEqual(self._names("[X, Y] = load()\n"), ["X", "Y"])

    def test_a_starred_target_binds(self):
        self.assertEqual(self._names("first, *rest = load()\n"), ["first", "rest"])

    def test_a_nested_tuple_target_binds(self):
        self.assertEqual(self._names("(A, (B, C)) = load()\n"), ["A", "B", "C"])

    def test_chained_assignment_binds_both(self):
        self.assertEqual(self._names("A = B = load()\n"), ["A", "B"])

    def test_a_surviving_tuple_binding_is_not_a_removal(self):
        """The defect itself: unchanged code reported three removals."""
        src = "A, B, C = load()\n"
        self.assertEqual(self._changes(src, src), [])

    def test_a_tuple_binding_that_gains_a_name_is_not_a_removal(self):
        self.assertEqual(self._changes("A, B = load()\n", "A, B, C = load()\n"), [])

    def test_losing_one_name_from_a_tuple_is_still_a_removal(self):
        self.assertEqual(
            self._changes("A, B, C = load()\n", "A, B = load()\n"),
            [("C", "removed")])

    def test_a_local_tuple_inside_a_function_is_not_a_module_symbol(self):
        self.assertEqual(
            self._names("def f():\n    a, b = load()\n    return a\n"), ["f"])

    def test_a_tuple_rebind_does_not_chase_references(self):
        """End to end: unchanged tuple bindings, an unrelated edit, no findings."""
        before = {"lib.py": "A, B, C = load()\ndef f(x): pass\n",
                  "app.py": "from lib import A, B, C\nprint(A, B, C)\n"}
        after = {"lib.py": "A, B, C = load()\ndef f(x, y): pass\n",
                 "app.py": "from lib import A, B, C\nprint(A, B, C)\n"}
        v = br.check_sources(before, after, scan=after)
        self.assertEqual([c.qualname for c in v.contract_changes], ["f"])


class WidenedSignatures(unittest.TestCase):
    """D1. A signature that still accepts every call the old one accepted cannot
    break a caller, and that is decidable.

    33% of the findings in `research/BLAST_RADIUS_PRECISION.md` were this class --
    the single largest -- and the write-up called it "the easiest to remove". It was
    quantified and left in the code, which is the defect this class exists to close.
    """

    def _changes(self, before: str, after: str):
        changes, _, _ = br.contract_changes(before, after, "lib.py")
        return [(c.qualname, c.reason) for c in changes]

    def test_a_defaulted_parameter_appearing_is_not_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a): pass\n", "def f(a, b=2): pass\n"), [])

    def test_several_defaulted_parameters_appearing_is_not_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a): pass\n",
                          "def f(a, b=2, c=3, d=None): pass\n"), [])

    def test_a_keyword_only_parameter_with_a_default_is_not_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a): pass\n", "def f(a, *, mode='x'): pass\n"), [])

    def test_gaining_star_args_is_not_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a): pass\n", "def f(a, *rest): pass\n"), [])

    def test_gaining_star_kwargs_is_not_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a): pass\n", "def f(a, **kw): pass\n"), [])

    def test_a_default_appearing_on_an_existing_parameter_is_not_a_change(self):
        self.assertEqual(
            self._changes("def f(a, b): pass\n", "def f(a, b=2): pass\n"), [])

    # --- and the narrowings, which must still be reported -----------------

    def test_a_new_required_parameter_is_still_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a): pass\n", "def f(a, b): pass\n"),
            [("f", "signature")])

    def test_a_parameter_disappearing_is_still_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a, b): pass\n", "def f(a): pass\n"),
            [("f", "signature")])

    def test_a_required_keyword_only_parameter_is_still_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a): pass\n", "def f(a, *, mode): pass\n"),
            [("f", "signature")])

    def test_losing_a_default_is_still_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a, b=2): pass\n", "def f(a, b): pass\n"),
            [("f", "signature")])

    def test_renaming_a_parameter_is_still_a_contract_change(self):
        """A caller passing it by keyword breaks, so this is not a widening."""
        self.assertEqual(
            self._changes("def f(a, b=2): pass\n", "def f(a, c=2): pass\n"),
            [("f", "signature")])

    def test_reordering_parameters_is_still_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a, b): pass\n", "def f(b, a): pass\n"),
            [("f", "signature")])

    def test_a_positional_becoming_keyword_only_is_still_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a, b=2): pass\n", "def f(a, *, b=2): pass\n"),
            [("f", "signature")])

    def test_a_parameter_becoming_positional_only_is_still_a_contract_change(self):
        self.assertEqual(
            self._changes("def f(a, b): pass\n", "def f(a, b, /): pass\n"),
            [("f", "signature")])

    def test_a_decorator_appearing_is_still_a_contract_change(self):
        """A decorator can change what the call returns, so it is not a widening."""
        self.assertEqual(
            self._changes("def f(a): pass\n",
                          "@lru_cache\ndef f(a): pass\n"),
            [("f", "signature")])

    def test_a_widened_method_is_not_a_contract_change(self):
        self.assertEqual(
            self._changes("class C:\n    def m(self, a): pass\n",
                          "class C:\n    def m(self, a, b=2): pass\n"), [])

    def test_a_widening_with_a_new_body_is_an_implementation_change(self):
        """It is still a change -- just not one a caller can observe."""
        changes, _, body = br.contract_changes(
            "def f(a):\n    return 1\n", "def f(a, b=2):\n    return 2\n", "lib.py")
        self.assertEqual(changes, [])
        self.assertIn("f", body)

    def test_a_widening_does_not_chase_references(self):
        """The end-to-end consequence: no contract change, so no stale callers."""
        before = {"lib.py": "def f(a): pass\n",
                  "app.py": "from lib import f\nf(1)\nf(2)\nf(3)\n"}
        after = {"lib.py": "def f(a, b=2): pass\n",
                 "app.py": "from lib import f\nf(1)\nf(2)\nf(3)\n"}
        v = br.check_sources(before, after, scan=after)
        self.assertEqual(v.contract_changes, [])
        self.assertEqual(v.unhandled, {})


class UnderReach(unittest.TestCase):
    """The half everyone hits: the contract moved, the callers did not."""

    def test_stale_caller_is_flagged(self):
        verdict = br.check_sources(
            before={"lib.py": LIB_BEFORE, "caller.py": CALLER_STALE},
            after={"lib.py": LIB_AFTER, "caller.py": CALLER_STALE},
            scan={},
        )
        self.assertFalse(verdict.ok)
        self.assertIn("train_step", verdict.unhandled)
        self.assertTrue(any("train_step" in p for p in verdict.problems))

    def test_updated_caller_is_clean(self):
        verdict = br.check_sources(
            before={"lib.py": LIB_BEFORE, "caller.py": CALLER_STALE},
            after={"lib.py": LIB_AFTER, "caller.py": CALLER_UPDATED},
            scan={},
        )
        self.assertTrue(verdict.ok, verdict.render())

    def test_caller_outside_the_diff_is_found(self):
        verdict = br.check_sources(
            before={"lib.py": LIB_BEFORE},
            after={"lib.py": LIB_AFTER},
            scan={"elsewhere.py": CALLER_STALE},
        )
        self.assertFalse(verdict.ok)
        paths = [r.path for r in verdict.unhandled["train_step"]]
        self.assertIn("elsewhere.py", paths)

    def test_import_line_is_not_a_stale_caller(self):
        """A signature change does not oblige the import statement to move."""
        verdict = br.check_sources(
            before={"lib.py": LIB_BEFORE},
            after={"lib.py": LIB_AFTER},
            scan={"only_import.py": "from lib import train_step\n"},
        )
        self.assertTrue(verdict.ok, verdict.render())

    def test_import_line_is_stale_when_symbol_is_removed(self):
        verdict = br.check_sources(
            before={"lib.py": LIB_BEFORE},
            after={"lib.py": "\n"},
            scan={"only_import.py": "from lib import train_step\n"},
        )
        self.assertFalse(verdict.ok)


class OverReach(unittest.TestCase):
    """The other half: the agent rewrote the world to fix one line."""

    def test_declared_l0_but_contract_changed(self):
        verdict = br.check_sources(
            before={"lib.py": LIB_BEFORE},
            after={"lib.py": LIB_AFTER},
            scan={},
            declared="L0",
        )
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.inferred, "L1")
        self.assertTrue(any("declared L0" in p for p in verdict.problems))

    def test_declared_l0_but_too_many_files(self):
        # Derived from the budget rather than typed: a hand-written 6 was a second copy of
        # BUDGETS that silently stopped exceeding it when I1 recalibrated L0 from 3 to 10.
        n = br.BUDGETS["L0"][0] + 1
        before = {f"m{i}.py": "x = 1\n" for i in range(n)}
        after = {f"m{i}.py": "x = 2\n" for i in range(n)}
        verdict = br.check_sources(before, after, scan={}, declared="L0")
        self.assertFalse(verdict.ok)
        self.assertTrue(any("over-reach" in p for p in verdict.problems))

    def test_honest_l0_is_clean(self):
        verdict = br.check_sources(
            before={"lib.py": "def f(x):\n    return 1"},
            after={"lib.py": "def f(x):\n    return 2"},
            scan={},
            declared="L0",
        )
        self.assertTrue(verdict.ok, verdict.render())
        self.assertEqual(verdict.inferred, "L0")

    def test_l2_requires_a_written_plan_once_the_repo_opts_in(self):
        before = {f"pkg{i}/m.py": "def f(): pass\n" for i in range(5)}
        after = {f"pkg{i}/m.py": "def g(): pass\n" for i in range(5)}
        verdict = br.check_sources(before, after, scan={},
                                   plan_required=True, plan_present=False)
        self.assertEqual(verdict.inferred, "L2")
        self.assertTrue(any("plan" in p for p in verdict.problems))

    def test_l2_with_a_plan_clears_the_plan_problem(self):
        before = {f"pkg{i}/m.py": "def f(): pass\n" for i in range(5)}
        after = {f"pkg{i}/m.py": "def f(): pass\n# note\n" for i in range(5)}
        verdict = br.check_sources(before, after, scan={},
                                   plan_required=True, plan_present=True)
        self.assertFalse(any("plan" in p for p in verdict.problems))


class Calibrated(unittest.TestCase):
    """I1: what the checker stopped saying, and why.

    Replayed over 150 commits with the shipped settings the checker flagged 83% of them and
    produced no dependency finding at all. Both causes are policy, not arithmetic, so both are
    pinned here rather than left to a constant somebody will retune.
    """

    def test_an_undeclared_diff_is_never_charged_a_budget(self):
        """The inference is this module's own guess, not evidence against the caller.

        _infer_scope calls a diff L0 *because* it changed no contract; charging that same diff
        for touching six files is two rules reading one piece of evidence and disagreeing.
        """
        n = br.BUDGETS["L0"][0] + 1
        before = {f"m{i}.py": "x = 1\n" for i in range(n)}
        after = {f"m{i}.py": "x = 2\n" for i in range(n)}
        verdict = br.check_sources(before, after, scan={})
        self.assertEqual(verdict.inferred, "L0")
        self.assertTrue(verdict.ok, verdict.render())
        self.assertFalse(any("over-reach" in p for p in verdict.problems))

    def test_a_declared_scope_is_still_held_to_its_budget(self):
        """Silencing the inference must not silence the promise."""
        n = br.BUDGETS["L0"][0] + 1
        before = {f"m{i}.py": "x = 1\n" for i in range(n)}
        after = {f"m{i}.py": "x = 2\n" for i in range(n)}
        verdict = br.check_sources(before, after, scan={}, declared="L0")
        self.assertFalse(verdict.ok)
        self.assertTrue(any("over-reach" in p for p in verdict.problems))

    def test_the_shipped_behaviour_is_still_reachable_for_measurement(self):
        """BUDGET_SCOPE exists so the negative result stays reproducible."""
        n = br.BUDGETS["L0"][0] + 1
        before = {f"m{i}.py": "x = 1\n" for i in range(n)}
        after = {f"m{i}.py": "x = 2\n" for i in range(n)}
        previous = br.BUDGET_SCOPE
        br.BUDGET_SCOPE = "always"
        try:
            verdict = br.check_sources(before, after, scan={})
        finally:
            br.BUDGET_SCOPE = previous
        self.assertFalse(verdict.ok)
        self.assertTrue(any("over-reach" in p for p in verdict.problems))

    def test_a_repo_that_never_opted_in_is_not_asked_for_a_plan(self):
        before = {f"pkg{i}/m.py": "def f(): pass\n" for i in range(5)}
        after = {f"pkg{i}/m.py": "def g(): pass\n" for i in range(5)}
        verdict = br.check_sources(before, after, scan={})
        self.assertEqual(verdict.inferred, "L2", "the class is still reported")
        self.assertFalse(any("plan" in p for p in verdict.problems))

    def test_the_l2_class_survives_the_silence(self):
        """Opting out of the demand must not opt out of the diagnosis."""
        before = {f"pkg{i}/m.py": "def f(): pass\n" for i in range(5)}
        after = {f"pkg{i}/m.py": "def g(): pass\n" for i in range(5)}
        self.assertEqual(br.check_sources(before, after, scan={}).to_dict()["inferred"], "L2")

    def test_the_working_tree_opts_in_by_having_a_dot_nevertwice_directory(self):
        """This repository has none, so the live check must stay silent about plans."""
        self.assertFalse((ROOT / ".nevertwice").exists())
        verdict = br.check_working_tree(ROOT)
        self.assertFalse(any("plan" in p for p in verdict.problems), verdict.render())

    def test_the_scan_asks_git_rather_than_walking_everything(self):
        """5.3 GB of vendored clones live under research/embed_universal/data/ here.

        SKIP_DIRS cannot know that - it is a hand-kept list that does not name `data` - so the
        walk used to descend into other people's repositories, which is both slow and wrong:
        a reference inside a vendored checkout is not a caller of this project.
        """
        listed = br._tracked_files(ROOT, {".py"})
        self.assertIsNotNone(listed, "git should be able to list this repository")
        offenders = [p for p in listed if "embed_universal" in p.as_posix()
                     and "/data/" in p.as_posix()]
        self.assertEqual(offenders, [])
        self.assertTrue(any(p.name == "blast_radius_deleted.py" for p in listed),
                        "the scan still has to see the repository's own code")

    def test_the_walk_is_still_there_when_git_is_not(self):
        found = br._iter_files(ROOT / "research" / "invariants_lab", {".py"})
        self.assertTrue(any(p.name == "blast_radius_deleted.py" for p in found))


class CompatibilityFacades(unittest.TestCase):
    """I2: a symbol that leaves a module and is re-exported from it broke nobody.

    This codebase's own refactoring idiom - ``write_atomic = _store_state.write_atomic`` after
    the E4 seam extraction - made the checker report 42 untouched references to a function no
    caller had to touch. Recognising the facade is half the fix; the other half is following it,
    so a facade that quietly changed the contract is still reported. Silencing without verifying
    would trade a false positive for a false negative, which is the worse trade for a guard.
    """

    LIB_MOVED = """\
_store = _sibling("store")
write_atomic = _store.write_atomic
"""
    STORE_SAME = """\
def write_atomic(path, text, encoding="utf-8"):
    return None
"""
    STORE_CHANGED = """\
def write_atomic(path, text, encoding, mode):
    return None
"""
    LIB_ORIGINAL = """\
def write_atomic(path, text, encoding="utf-8"):
    return None
"""
    CALLER = """\
from lib import write_atomic


def go(p):
    write_atomic(p, "x")
"""

    def _run(self, store_source, **kw):
        scan = {"caller.py": self.CALLER}
        if store_source is not None:
            scan["store.py"] = store_source
        return br.check_sources(
            before={"lib.py": self.LIB_ORIGINAL},
            after={"lib.py": self.LIB_MOVED},
            scan=scan, **kw,
        )

    def test_a_verified_facade_is_not_a_problem(self):
        verdict = self._run(self.STORE_SAME)
        self.assertEqual(verdict.problems, [])
        self._assert_verified(verdict)

    def test_a_verified_facade_does_not_even_count_as_a_contract_change(self):
        """It must not push the diff into L1 either - one piece of evidence, one conclusion."""
        verdict = self._run(self.STORE_SAME)
        self.assertEqual(verdict.contract_changes, [])
        self.assertEqual(verdict.stats["contract_changes"], 0)
        self.assertEqual(verdict.inferred, "L0")

    def test_a_facade_that_changed_the_signature_is_still_reported(self):
        verdict = self._run(self.STORE_CHANGED)
        self.assertFalse(verdict.ok, verdict.render())
        reasons = {c.reason for c in verdict.contract_changes}
        self.assertIn("signature", reasons)
        change = next(c for c in verdict.contract_changes if c.qualname == "write_atomic")
        self.assertIn("encoding='utf-8'", change.before)
        self.assertIn("mode", change.after)

    def test_an_unreachable_target_degrades_to_a_note(self):
        verdict = self._run(None)
        self.assertTrue(verdict.ok, verdict.render())
        self.assertTrue(any("cannot be checked from here" in n for n in verdict.notes),
                        verdict.notes)

    def test_a_rebinding_to_a_different_name_is_not_a_facade(self):
        """The name resolves, but nothing structural says the two are the same function."""
        verdict = br.check_sources(
            before={"lib.py": self.LIB_ORIGINAL},
            after={"lib.py": "write_atomic = _legacy_writer\n"},
            scan={"caller.py": self.CALLER},
        )
        self.assertFalse(verdict.ok, verdict.render())

    def test_an_attribute_under_a_different_name_is_not_a_facade(self):
        """`x = mod.y` moves the name as well as the definition; a rename, not a facade."""
        moved = '_store = _sibling("store")\nwrite_atomic = _store.other_name\n'
        verdict = br.check_sources(
            before={"lib.py": self.LIB_ORIGINAL},
            after={"lib.py": moved},
            scan={"caller.py": self.CALLER, "store.py": self.STORE_SAME},
        )
        self.assertFalse(verdict.ok, verdict.render())


    def test_a_plain_deletion_is_still_a_removal(self):
        verdict = br.check_sources(
            before={"lib.py": self.LIB_ORIGINAL},
            after={"lib.py": "\n"},
            scan={"caller.py": self.CALLER},
        )
        self.assertFalse(verdict.ok, verdict.render())
        self.assertIn("removed", {c.reason for c in verdict.contract_changes})

    def test_the_import_form_of_a_facade_is_recognised(self):
        verdict = br.check_sources(
            before={"lib.py": self.LIB_ORIGINAL},
            after={"lib.py": "from store import write_atomic\n"},
            scan={"caller.py": self.CALLER, "store.py": self.STORE_SAME},
        )
        self.assertTrue(verdict.ok, verdict.render())
        self.assertTrue(any("compatibility facade" in n for n in verdict.notes), verdict.notes)

    def _assert_verified(self, verdict):
        """Clean is not enough: an unreachable target is also clean, and proves nothing.

        Only a facade whose target was located AND whose signature matched produces the
        "compatibility facade" note. Asserting ok alone let a mutation that broke module
        resolution pass, because giving up looks exactly like succeeding from the outside.
        """
        self.assertTrue(verdict.ok, verdict.render())
        self.assertTrue(any("compatibility facade" in n for n in verdict.notes),
                        f"target was never resolved: {verdict.notes}")

    def test_a_relative_import_facade_resolves_too(self):
        self._assert_verified(br.check_sources(
            before={"pkg/lib.py": self.LIB_ORIGINAL},
            after={"pkg/lib.py": "from .store import write_atomic\n"},
            scan={"pkg/store.py": self.STORE_SAME},
        ))

    def test_an_import_alias_names_the_module(self):
        self._assert_verified(br.check_sources(
            before={"lib.py": self.LIB_ORIGINAL},
            after={"lib.py": "import pkg.store as _s\nwrite_atomic = _s.write_atomic\n"},
            scan={"pkg/store.py": self.STORE_SAME, "caller.py": self.CALLER},
        ))

    def test_the_dynamic_sibling_idiom_names_the_module(self):
        """`_sibling("store_state")` is a call, not an import - the string is the only clue."""
        aliases = br._module_aliases(__import__("ast").parse(self.LIB_MOVED))
        self.assertEqual(aliases.get("_store"), "store")

    def test_a_facade_inside_a_class_is_not_treated_as_a_module_re_export(self):
        before = "class C:\n    def f(self, a):\n        return a\n"
        after = "class C:\n    f = other.f\n"
        verdict = br.check_sources({"lib.py": before}, {"lib.py": after},
                                   scan={"c.py": "C().f(1)\n"})
        self.assertTrue(any(c.qualname == "C.f" for c in verdict.contract_changes),
                        [c.qualname for c in verdict.contract_changes])


class NoiseControl(unittest.TestCase):
    """False positives kill guards. These cases must stay quiet."""

    def test_ambiguous_name_degrades_to_a_note(self):
        verdict = br.check_sources(
            before={"lib.py": "class C:\n    def run(self, a):\n        return a"},
            after={"lib.py": "class C:\n    def run(self, a, b):\n        return a"},
            scan={"other.py": "x.run(1)\ny.run(2)\n"},
        )
        self.assertTrue(verdict.ok, verdict.render())
        self.assertTrue(verdict.notes)

    def test_ignore_pattern_suppresses_a_symbol(self):
        verdict = br.check_sources(
            before={"lib.py": LIB_BEFORE},
            after={"lib.py": LIB_AFTER},
            scan={"caller.py": CALLER_STALE},
            ignore=["train_*"],
        )
        self.assertTrue(verdict.ok, verdict.render())

    def test_unparseable_scan_file_does_not_crash(self):
        verdict = br.check_sources(
            before={"lib.py": LIB_BEFORE},
            after={"lib.py": LIB_AFTER},
            scan={"broken.py": "def ((("},
        )
        self.assertIsInstance(verdict.ok, bool)

    def test_no_changes_is_clean(self):
        verdict = br.check_sources({}, {}, scan={})
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.inferred, "L0")

    def test_non_python_files_are_ignored_for_contracts(self):
        verdict = br.check_sources(
            before={"README.md": "# a\n"},
            after={"README.md": "# b\n"},
            scan={},
        )
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.contract_changes, [])


class Output(unittest.TestCase):
    def test_json_is_serialisable(self):
        import json

        verdict = br.check_sources(
            before={"lib.py": LIB_BEFORE},
            after={"lib.py": LIB_AFTER},
            scan={"caller.py": CALLER_STALE},
        )
        payload = json.loads(json.dumps(verdict.to_dict()))
        self.assertIn("unhandled", payload)
        self.assertFalse(payload["ok"])

    def test_render_is_a_string_both_ways(self):
        dirty = br.check_sources(
            {"lib.py": LIB_BEFORE}, {"lib.py": LIB_AFTER}, scan={"c.py": CALLER_STALE}
        )
        clean = br.check_sources({}, {}, scan={})
        self.assertIn("blast radius", dirty.render())
        self.assertIn("blast radius", clean.render())


class KillSwitch(unittest.TestCase):
    def test_env_var_disables_the_guard(self):
        previous = os.environ.get(br._ENV_DISABLE)
        os.environ[br._ENV_DISABLE] = "0"
        try:
            verdict = br.check_working_tree(Path("."))
            self.assertTrue(verdict.ok)
            self.assertTrue(any("disabled" in n for n in verdict.notes))
        finally:
            if previous is None:
                os.environ.pop(br._ENV_DISABLE, None)
            else:
                os.environ[br._ENV_DISABLE] = previous

    def test_missing_repo_degrades_to_clean(self):
        verdict = br.check_working_tree(Path(os.sep))
        self.assertTrue(verdict.ok)

    def test_cli_is_advisory_by_default(self):
        self.assertEqual(br.main(["--root", os.sep, "--quiet"]), 0)


# ---------------------------------------------------------------------------
# I0 regressions - the three defects that made the checker unusable as shipped
# ---------------------------------------------------------------------------

ROOT = HERE.parent


class _Console(io.StringIO):
    """A stream that accepts exactly what a console with this codepage accepts."""

    def __init__(self, encoding: str) -> None:
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self) -> str:  # what render() interrogates
        return self._encoding

    def write(self, text: str) -> int:
        text.encode(self._encoding)  # raises exactly where a real console would
        return super().write(text)


def _violation():
    return br.check_sources(
        {"lib.py": LIB_BEFORE},
        {"lib.py": LIB_AFTER},
        scan={"caller.py": CALLER_STALE},
    )


def _cp1251_child(source: str, timeout: int = 120):
    """Run *source* in a child interpreter whose stdio really is cp1251.

    PYTHONUTF8 is stripped on purpose: tests/test_self_checks.py exports it as
    1 for every suite, and in UTF-8 mode a cp1251 regression cannot reproduce.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUTF8"}
    env["PYTHONIOENCODING"] = "cp1251"
    return subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True, text=True, encoding="cp1251", errors="replace",
        env=env, timeout=timeout, check=False,
    )


class ConsoleEncoding(unittest.TestCase):
    """The checker used to die printing its own warning sign into cp1251.

    U+26A0 has no cp1251 code point, so ``print(verdict.render())`` raised
    UnicodeEncodeError on the author's default console - a guard that promises
    it can never break your workflow, breaking it on the first violation it
    ever reported.
    """

    def test_a_violation_renders_onto_a_cp1251_console(self):
        verdict = _violation()
        self.assertFalse(verdict.ok)
        console = _Console("cp1251")
        rendered = verdict.render(stream=console)
        console.write(rendered)  # would raise if a glyph slipped through
        self.assertNotIn("⚠", rendered)
        self.assertIn("blast radius", rendered)
        # Readability is the property, not mere absence: a glyph that fell
        # through to the last-resort replace would show up here as '?'.
        self.assertNotIn("?", rendered)
        self.assertTrue(rendered.startswith("!"), rendered[:40])

    def test_a_clean_verdict_renders_onto_a_cp1251_console(self):
        clean = br.check_sources({}, {}, scan={})
        console = _Console("cp1251")
        rendered = clean.render(stream=console)
        console.write(rendered)
        self.assertNotIn("✓", rendered)
        self.assertNotIn("?", rendered)
        self.assertTrue(rendered.startswith("OK"), rendered[:40])

    def test_every_glyph_the_renderer_can_emit_has_a_fallback(self):
        """The table must cover the renderer, not just today's two verdicts.

        Every decorative code point ``_render`` is capable of emitting is
        exercised here - the clean head, the violation head, the em dash, the
        bullet, the signature arrow and the truncation ellipsis - and each one
        must have an ASCII equivalent. Without this the table silently rots the
        moment a new glyph is added to the renderer.
        """
        renders = [
            br.check_sources({}, {}, scan={})._render(),
            _violation()._render(max_refs=1),
            br.check_sources(
                {"lib.py": LIB_BEFORE},
                {"lib.py": LIB_AFTER},
                scan={"a.py": CALLER_STALE, "b.py": CALLER_STALE, "c.py": CALLER_STALE},
                declared="L0",
            )._render(max_refs=1),
        ]
        seen = {ch for text in renders for ch in text if ord(ch) > 127}
        self.assertTrue(seen, "no glyph was exercised - the fixtures stopped covering the renderer")
        for glyph in ("✓", "⚠", "•", "—", "→", "…"):
            self.assertIn(glyph, seen, f"fixture no longer exercises {glyph!r}")
        missing = sorted(g for g in seen if g not in br.ASCII_FALLBACK)
        self.assertEqual(missing, [], f"no ASCII fallback for {missing}")

    def test_unicode_survives_when_the_stream_can_carry_it(self):
        """The downgrade is conditional. A utf-8 console keeps the glyphs."""
        rendered = _violation().render(stream=_Console("utf-8"))
        self.assertIn("⚠", rendered)

    def test_no_stream_means_no_downgrade(self):
        self.assertIn("⚠", _violation().render())

    def test_text_beyond_the_glyph_table_degrades_instead_of_raising(self):
        """A Cyrillic path is not in the fallback table and must not raise."""
        verdict = br.Verdict(ok=False, problems=["файл.py: contract changed"])
        console = _Console("ascii")
        rendered = verdict.render(stream=console)
        console.write(rendered)
        self.assertIn("?", rendered)

    def test_a_stream_without_an_encoding_is_left_alone(self):
        self.assertIn("⚠", _violation().render(stream=io.StringIO()))

    def test_an_unknown_codec_name_does_not_raise(self):
        self.assertIsInstance(br.encode_safely("⚠ x", _Console("not-a-codec")), str)

    def test_the_cli_prints_on_a_real_cp1251_console(self):
        """End to end through main(), on a genuine cp1251 stdio, in a child.

        PYTHONUTF8 is cleared deliberately: tests/test_self_checks.py sets it to
        1 for every suite, and under UTF-8 mode this test would pass without
        ever exercising a narrow codepage. Driving main() rather than _write()
        is the point - the crash was in main's print, not in the renderer.
        """
        child = (
            "import importlib.util, sys\n"
            "enc = sys.stdout.encoding.lower().replace('-', '')\n"
            "assert enc == 'cp1251', enc\n"
            "spec = importlib.util.spec_from_file_location('br', %r)\n"
            "m = importlib.util.module_from_spec(spec); sys.modules['br'] = m\n"
            "spec.loader.exec_module(m)\n"
            "sys.exit(m.main(['--root', %r, '--base', 'HEAD']))\n"
        ) % (str(_MODULE_PATH), str(ROOT))
        proc = _cp1251_child(child, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("UnicodeEncodeError", proc.stderr)
        self.assertIn("blast radius", proc.stdout)

    def test_the_renderer_writes_a_violation_through_a_cp1251_stream(self):
        """The same guarantee one layer down, with a violation guaranteed."""
        child = (
            "import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('br', %r)\n"
            "m = importlib.util.module_from_spec(spec); sys.modules['br'] = m\n"
            "spec.loader.exec_module(m)\n"
            "v = m.Verdict(ok=False, problems=['signature changed'])\n"
            "m._write(sys.stdout, v.render(stream=sys.stdout))\n"
        ) % str(_MODULE_PATH)
        proc = _cp1251_child(child)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("blast radius", proc.stdout)


class WireFormat(unittest.TestCase):
    """Section 4.4 tells the MCP integrator to return ``verdict.to_dict()``.

    The payload had no field named ``verdict``, so every consumer had to
    re-derive the judgement from ``ok`` and a scan of ``notes``.
    """

    def test_to_dict_names_the_verdict(self):
        payload = json.loads(json.dumps(_violation().to_dict()))
        self.assertEqual(payload["verdict"], "violation")

    def test_a_clean_tree_says_clean(self):
        self.assertEqual(br.check_sources({}, {}, scan={}).to_dict()["verdict"], "clean")

    def test_the_killswitch_is_not_reported_as_a_pass(self):
        """``ok`` is True either way; only ``verdict`` tells them apart."""
        previous = os.environ.get(br._ENV_DISABLE)
        os.environ[br._ENV_DISABLE] = "0"
        try:
            payload = br.check_working_tree(ROOT).to_dict()
        finally:
            if previous is None:
                os.environ.pop(br._ENV_DISABLE, None)
            else:
                os.environ[br._ENV_DISABLE] = previous
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["verdict"], "disabled")

    def test_every_key_the_spec_promises_is_present(self):
        payload = _violation().to_dict()
        for key in ("verdict", "ok", "declared", "inferred", "problems",
                    "contract_changes", "unhandled", "stats", "notes"):
            self.assertIn(key, payload)

    def test_json_output_is_ascii_and_so_prints_anywhere(self):
        json.dumps(_violation().to_dict(), indent=2).encode("cp1251")


class PhaseZero(unittest.TestCase):
    """I3: the integration contract, as tests rather than as a checklist someone once ran.

    Section 4.3 of the integration spec lists four verifications and the killswitch. Each was
    run by hand once; a hand-run check is a claim about the past. These are the same properties
    as assertions, so a later change that quietly makes the package expensive to import, or
    wires it into a hot path, fails here instead of in somebody's PreToolUse hook.
    """

    def _child(self, source: str, env_extra: dict | None = None):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, "-c", source], cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env, timeout=120, check=False,
        )

    def test_the_package_is_empty_because_the_checker_failed_its_gates(self):
        """T4. A decision nobody executed is a decision nobody made.

        `blast_radius` was installed here by the previous run's I3 and failed two of
        its four declared gates in D5 -- precision 0.396 against a floor of 0.50, and
        a flag rate of 0.278 against a ceiling of 0.05. The declared consequence of
        failing a gate is deletion, so the registry is empty and the module is not in
        the package. This test exists so that putting it back is a visible act.
        """
        proc = self._child(
            "import nevertwice.invariants as inv\n"
            "print('registry', inv.INVARIANTS)\n"
            "print('exports', inv.__all__)\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("registry {}", proc.stdout)
        self.assertIn("exports []", proc.stdout)
        self.assertFalse((ROOT / "nevertwice" / "invariants" / "blast_radius.py").exists())

    def test_the_deleted_checker_is_still_readable_in_the_lab(self):
        """A negative result is only worth something if its subject can still be read."""
        kept = ROOT / "research" / "invariants_lab" / "blast_radius_deleted.py"
        self.assertTrue(kept.exists())
        self.assertGreater(len(kept.read_text(encoding="utf-8").splitlines()), 500)

    def test_importing_the_package_is_cheap(self):
        """A generous absolute bound: this catches "it imports the world", not jitter."""
        proc = self._child(
            "import time\n"
            "t = time.perf_counter()\n"
            "import nevertwice.invariants  # noqa: F401\n"
            "print('cost', time.perf_counter() - t)\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        cost = float(proc.stdout.split("cost")[1].strip().splitlines()[0])
        self.assertLess(cost, 0.5, f"importing the package took {cost:.3f}s")

    def test_an_unknown_attribute_still_raises_attribute_error(self):
        with self.assertRaises(AttributeError):
            import nevertwice.invariants as inv
            inv.no_such_thing

    def test_the_registry_names_a_module_that_exists(self):
        import nevertwice.invariants as inv
        for checker_id, module_name in inv.INVARIANTS.items():
            rel = Path(*module_name.split(".")).with_suffix(".py")
            self.assertTrue((ROOT / rel).is_file(), f"{checker_id} -> {module_name}")

    def test_everything_all_promises_actually_exists(self):
        """__all__ is a contract with importers; a name that is not there is a broken import."""
        missing = [name for name in br.__all__ if not hasattr(br, name)]
        self.assertEqual(missing, [])

    def test_the_package_re_exports_what_it_says_it_does(self):
        import nevertwice.invariants as inv
        for name in inv.__all__:
            self.assertTrue(hasattr(inv, name), name)

    def test_no_production_module_imports_the_invariants_package(self):
        """The deletability property, and the only one that can silently stop being true.

        4.7 promises that removing nevertwice/invariants/ leaves the system working exactly as
        before. That was verified by actually deleting it in a detached worktree and running the
        full suite - 87 green, which is 89 minus the two suites that went with it. What a
        one-off experiment cannot do is stay true, so the invariant behind it is asserted here:
        nothing outside the package imports it.
        """
        import ast as _ast

        offenders = []
        for path in sorted((ROOT / "nevertwice").rglob("*.py")):
            if "invariants" in path.parts:
                continue
            try:
                tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:  # pragma: no cover - a file the interpreter would reject anyway
                continue
            for node in _ast.walk(tree):
                names = []
                if isinstance(node, _ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, _ast.ImportFrom):
                    module = ("." * node.level) + (node.module or "")
                    names = [module] + [f"{module}.{a.name}" for a in node.names]
                for name in names:
                    if name.split(".")[-1] == "invariants" or ".invariants." in name + ".":
                        offenders.append(f"{path.relative_to(ROOT).as_posix()}: {name}")
        self.assertEqual(offenders, [], "; ".join(offenders))

    def test_the_killswitch_makes_the_checker_a_no_op(self):
        """Loaded by path, because the package no longer carries it (T4). The
        killswitch is a property of the checker, and it survived the deletion."""
        proc = self._child(
            "import importlib.util, json, sys\n"
            "spec = importlib.util.spec_from_file_location("
            "'_br', r'research/invariants_lab/blast_radius_deleted.py')\n"
            "m = importlib.util.module_from_spec(spec); sys.modules['_br'] = m\n"
            "spec.loader.exec_module(m)\n"
            "print(json.dumps(m.check_working_tree().to_dict()['verdict']))\n",
            {br._ENV_DISABLE: "0"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("disabled", proc.stdout)

    def test_every_falsy_spelling_of_the_killswitch_works(self):
        for value in ("0", "false", "off", "no", "OFF", " No "):
            previous = os.environ.get(br._ENV_DISABLE)
            os.environ[br._ENV_DISABLE] = value
            try:
                self.assertTrue(br._disabled(), f"{value!r} should disable the guard")
            finally:
                if previous is None:
                    os.environ.pop(br._ENV_DISABLE, None)
                else:
                    os.environ[br._ENV_DISABLE] = previous

    def test_an_unset_killswitch_leaves_the_guard_armed(self):
        previous = os.environ.pop(br._ENV_DISABLE, None)
        try:
            self.assertFalse(br._disabled())
        finally:
            if previous is not None:
                os.environ[br._ENV_DISABLE] = previous

    def test_enforce_is_what_turns_a_violation_into_a_nonzero_exit(self):
        """Advisory by default is the promise that lets it be wired into a hook safely."""
        dirty = _violation()
        self.assertFalse(dirty.ok)
        real = br.check_working_tree
        br.check_working_tree = lambda *a, **k: dirty
        try:
            self.assertEqual(br.main(["--quiet"]), 0, "default must stay advisory")
            self.assertEqual(br.main(["--quiet", "--enforce"]), 1)
        finally:
            br.check_working_tree = real

    def test_a_clean_tree_exits_zero_even_under_enforce(self):
        clean = br.check_sources({}, {}, scan={})
        real = br.check_working_tree
        br.check_working_tree = lambda *a, **k: clean
        try:
            self.assertEqual(br.main(["--quiet", "--enforce"]), 0)
        finally:
            br.check_working_tree = real

    def test_an_internal_failure_degrades_to_clean_rather_than_a_traceback(self):
        """Crash-proof is a design rule, not an aspiration: a guard may never break a caller."""
        def boom(*a, **k):
            raise RuntimeError("something broke inside the checker")

        real = br.check_working_tree
        br.check_working_tree = boom
        try:
            self.assertEqual(br.main(["--quiet"]), 0)
            self.assertEqual(br.main(["--enforce"]), 0)
            self.assertEqual(br.main(["--json"]), 0)
        finally:
            br.check_working_tree = real


class Layout(unittest.TestCase):
    """The spec's paths predate ``05cfdc9``; these pin the reconciled ones."""

    def test_the_checker_is_where_the_package_expects_it(self):
        self.assertEqual(
            Path(br.__file__).resolve(),
            (ROOT / "research" / "invariants_lab" / "blast_radius_deleted.py").resolve(),
        )

    def test_this_suite_sits_under_the_glob_ci_actually_runs(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("tests/_test_*.py", workflow)
        self.assertEqual(Path(__file__).resolve().parent.name, "tests")
        self.assertTrue(Path(__file__).name.startswith("_test_"))

    def test_the_subpackage_is_declared_in_pyproject(self):
        """4.2 is mandatory here: this project lists packages explicitly, so an
        undeclared subpackage ships a wheel that raises ModuleNotFoundError."""
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"nevertwice.invariants"', pyproject)

    def test_the_stale_research_path_the_spec_guards_is_gone(self):
        """4.6 protects ``nevertwice/research/**``, which no longer exists;
        the reproducible evals live at ``research/`` in the repository root."""
        self.assertFalse((ROOT / "nevertwice" / "research").exists())
        self.assertTrue((ROOT / "research").is_dir())


if __name__ == "__main__":
    unittest.main(verbosity=2)
