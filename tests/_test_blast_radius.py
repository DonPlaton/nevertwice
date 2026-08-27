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
_MODULE_PATH = HERE.parent / "nevertwice" / "invariants" / "blast_radius.py"
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
        self.assertTrue(any(p.name == "blast_radius.py" for p in listed),
                        "the scan still has to see the repository's own code")

    def test_the_walk_is_still_there_when_git_is_not(self):
        found = br._iter_files(ROOT / "nevertwice" / "invariants", {".py"})
        self.assertTrue(any(p.name == "blast_radius.py" for p in found))


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


class Layout(unittest.TestCase):
    """The spec's paths predate ``05cfdc9``; these pin the reconciled ones."""

    def test_the_checker_is_where_the_package_expects_it(self):
        self.assertEqual(
            Path(br.__file__).resolve(),
            (ROOT / "nevertwice" / "invariants" / "blast_radius.py").resolve(),
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
