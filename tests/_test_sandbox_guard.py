#!/usr/bin/env python3
"""One sandbox for the repository, and it verifies itself.

On 2026-08-13 a test batch overwrote a 4319-entry production embedding cache. On 2026-08-18
the same class of bug hit again. On 2026-08-25 an *example* wrote a fabricated mistake note
into the live vault and let the hook commit it. Each fix was correct and each was applied to
one directory, so the next entry point re-learned the lesson from scratch.

What the three have in common is not "the examples never got the test guard". It is that a
guard which **pins and then trusts** is a hope. `NEVERTWICE_VAULT` is exported machine-wide
on the maintainer's host - and that is the *supported* way a real user points at a real
store, so the product has to be safe while it is set, not only when it is absent.

This suite holds the guard to the stronger contract:

* the policy exists in exactly one module, and the per-directory entry points hold none of it;
* the pin is followed by an **assertion** that config actually landed in the sandbox;
* with a hostile `NEVERTWICE_VAULT` exported at a stand-in store, every example runs to
  completion and the stand-in is byte-for-byte unchanged;
* the lint that enforces the declaration catches a planted violation;
* and - the check that keeps the three above honest - a guard with the assertion removed is
  *proved* to let the write through, so none of this passes for the wrong reason.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402

EXAMPLES = ROOT / "examples"
LINT = ROOT / "tools" / "check_sandbox.py"

# Every example, with the arguments that make it terminate. `demo.sh` is added at run time
# when a POSIX shell exists (it does on all three CI runners, via git-bash on Windows).
EXAMPLE_RUNS: list = [
    ("guard_demo.py", [sys.executable, str(EXAMPLES / "guard_demo.py"), "--check"]),
    ("no_model_demo.py", [sys.executable, str(EXAMPLES / "no_model_demo.py")]),
    ("scenario_demo.py", [sys.executable, str(EXAMPLES / "scenario_demo.py")]),
    ("demo.py", [sys.executable, str(EXAMPLES / "demo.py")]),
]

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


# ── helpers ───────────────────────────────────────────────────────────

def fingerprint(root: Path) -> dict:
    """Every file under `root`, by relative path, with its SHA-256. Byte-level, so a
    rewritten-but-identical file passes and a single changed counter does not."""
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return out


def seed_stand_in(root: Path) -> None:
    """A directory shaped like a real store, so a demo that escapes has somewhere to land
    and something to overwrite - the 2026-08-18 incident was an overwrite, not a create."""
    (root / "Mistakes").mkdir(parents=True, exist_ok=True)
    (root / "Context").mkdir(parents=True, exist_ok=True)
    (root / "guards.json").write_text('{"guards": [], "version": 3}\n', encoding="utf-8")
    (root / ".embeddings_cache.json").write_text('{"real": [0.1, 0.2]}\n', encoding="utf-8")
    (root / "Mistakes" / "2026-01-01-real-lesson.md").write_text(
        "# a note that predates the test\n", encoding="utf-8")
    (root / "Context" / "realproject.md").write_text("# real context\n", encoding="utf-8")


def hostile_env(stand_in: Path) -> dict:
    """The maintainer's actual shell, reduced to its essentials: a store location exported
    under the name `config` prefers. `NEVERTWICE_CLOUD` is pinned off only so the run does
    not depend on whether Ollama happens to be up - it says nothing about *where* the store
    is, which is what is under test."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("NEVERTWICE_", "ANAMNESIS_", "CLAUDE_MEMORY_"))}
    env.update({"NEVERTWICE_VAULT": str(stand_in), "NEVERTWICE_CLOUD": "none",
                "NEVERTWICE_XRERANK": "0", "PYTHONUTF8": "1", "NEVERTWICE_DEMO_PAUSE": "0"})
    return env


def example_runs() -> list:
    runs = list(EXAMPLE_RUNS)
    bash = shutil.which("bash")
    if bash and (EXAMPLES / "demo.sh").exists():
        runs.append(("demo.sh", [bash, str(EXAMPLES / "demo.sh")]))
    return runs


# ── (a) one module, no second copy ────────────────────────────────────

def test_the_policy_lives_in_exactly_one_module() -> None:
    print("\n- the scrub list, the pin and the assertion exist once -")
    canonical = (ROOT / "sandbox_guard.py").read_text(encoding="utf-8")
    check("the canonical module pins VAULT", 'os.environ["NEVERTWICE_VAULT"]' in canonical)
    check("the canonical module pins HOME", 'os.environ["NEVERTWICE_HOME"]' in canonical)
    check("the canonical module scrubs the env-file pointer",
          '"NEVERTWICE_ENV_FILE"' in canonical)

    # The drift that motivated the merge: the test guard scrubbed the twin-gate pins and the
    # examples guard did not. One list means one answer.
    for var in ("NEVERTWICE_TWIN_FILE", "NEVERTWICE_TWIN_SPACE", "ANAMNESIS_VAULT",
                "CLAUDE_MEMORY_VAULT", "NEVERTWICE_PROJECTS_ROOT"):
        check(f"{var} is in the one scrub list", var in sandbox_guard.LOCATION_VARS)

    for shim in (HERE / "_env_guard.py", EXAMPLES / "_sandbox.py"):
        text = shim.read_text(encoding="utf-8")
        rel = shim.relative_to(ROOT).as_posix()
        check(f"{rel} delegates to the canonical module",
              "sandbox_guard.isolate(" in text)
        check(f"{rel} carries no policy of its own",
              "os.environ[" not in text and "mkdtemp" not in text
              and "ANAMNESIS_VAULT" not in text)


# ── (b) the assertion, and that it actually fires ─────────────────────

def test_the_assertion_fires_on_each_escape_route() -> None:
    print("\n- the pin is checked, not trusted -")
    store = sandbox_guard.store()
    check("this process is isolated", store is not None and store.exists())
    if store is None:
        return

    saved = os.environ.get("NEVERTWICE_VAULT")
    try:
        os.environ["NEVERTWICE_VAULT"] = str(Path.home() / ".nevertwice")
        try:
            sandbox_guard.verify()
            check("a relocated NEVERTWICE_VAULT is caught", False, "verify() stayed silent")
        except sandbox_guard.SandboxEscape as exc:
            check("a relocated NEVERTWICE_VAULT is caught", True)
            check("the error names the offending variable", "NEVERTWICE_VAULT" in str(exc))
    finally:
        if saved is None:
            os.environ.pop("NEVERTWICE_VAULT", None)
        else:
            os.environ["NEVERTWICE_VAULT"] = saved

    saved_home = os.environ.pop("NEVERTWICE_HOME", None)
    try:
        try:
            sandbox_guard.verify()
            check("a deleted NEVERTWICE_HOME pin is caught", False)
        except sandbox_guard.SandboxEscape:
            check("a deleted NEVERTWICE_HOME pin is caught", True)
    finally:
        if saved_home is not None:
            os.environ["NEVERTWICE_HOME"] = saved_home

    # Check 3: a derived constant that baked a live path before the store moved. This is the
    # 2026-08-18 shape, and no environment variable is wrong when it happens.
    import types
    fake = types.ModuleType("config")
    fake.EMBED_CACHE = Path.home() / ".nevertwice" / ".embeddings_cache.json"
    sys.modules["config__probe"] = fake
    real = sandbox_guard._REAL_STORES
    try:
        sandbox_guard.PROJECT_MODULES = frozenset(
            set(sandbox_guard.PROJECT_MODULES) | {"config__probe"})
        sandbox_guard._REAL_STORES = (Path.home() / ".nevertwice",)
        try:
            sandbox_guard.verify()
            check("a derived path left inside a real store is caught", False)
        except sandbox_guard.SandboxEscape as exc:
            check("a derived path left inside a real store is caught", True)
            check("the error names the constant", "EMBED_CACHE" in str(exc))
    finally:
        sandbox_guard._REAL_STORES = real
        sys.modules.pop("config__probe", None)

    check("verify() is quiet once the sandbox is intact again",
          sandbox_guard.verify() is None)

    try:
        sandbox_guard.allow_live("too short")
        check("allow_live() rejects a placeholder reason", False)
    except ValueError:
        check("allow_live() rejects a placeholder reason", True)
    check("allow_live() cannot un-isolate an already-sandboxed process",
          sandbox_guard.allow_live("a fully written reason, long enough") is None
          and sandbox_guard.store() is not None)


# ── (d) every example, against a hostile store location ───────────────

def test_no_example_can_be_captured_by_an_exported_vault() -> None:
    print("\n- a hostile NEVERTWICE_VAULT cannot capture any example -")
    runs = example_runs()
    check(f"all {len(runs)} examples are exercised", len(runs) >= 4,
          ", ".join(n for n, _ in runs))

    with tempfile.TemporaryDirectory(prefix="nevertwice_stand_in_") as tmp:
        stand_in = Path(tmp)
        seed_stand_in(stand_in)
        before = fingerprint(stand_in)
        for name, argv in runs:
            proc = subprocess.run(argv, cwd=ROOT, env=hostile_env(stand_in),
                                  capture_output=True, text=True, timeout=900,
                                  encoding="utf-8", errors="replace")
            check(f"{name} runs to completion", proc.returncode == 0,
                  f"exit {proc.returncode}: {(proc.stdout + proc.stderr).strip()[-300:]}")
            after = fingerprint(stand_in)
            added = sorted(set(after) - set(before))
            changed = sorted(k for k in set(after) & set(before) if after[k] != before[k])
            removed = sorted(set(before) - set(after))
            check(f"{name} left the stand-in store byte-identical",
                  not (added or changed or removed),
                  f"+{added[:3]} ~{changed[:3]} -{removed[:3]}")


# ── (e) the mutation check that keeps all of the above honest ─────────

def _mutant_tree(tmp: Path, *, drop_vault: bool, drop_assertion: bool) -> Path:
    """A copy of the repo's guard and package with named lines removed.

    `drop_vault` reproduces the guard exactly as the demos had it on 2026-08-25: HOME is
    pinned, `NEVERTWICE_VAULT` is neither scrubbed nor pinned, so an exported value survives
    and outranks the pin. `drop_assertion` then removes the check that catches it.

    Copying rather than editing in place: the mutants must never be importable from the
    working tree, and a crashed run must not be able to leave one behind.
    """
    shutil.copytree(ROOT / "nevertwice", tmp / "nevertwice",
                    ignore=shutil.ignore_patterns("__pycache__", "*.env", "*.json"))
    text = (ROOT / "sandbox_guard.py").read_text(encoding="utf-8")
    if drop_vault:
        before = text
        text = text.replace('    "NEVERTWICE_VAULT", "NEVERTWICE_HOME",\n',
                            '    "NEVERTWICE_HOME",\n')
        text = text.replace('    os.environ["NEVERTWICE_VAULT"] = str(_STORE)\n', "")
        assert text != before, "the mutation matched nothing - the guard was rewritten"
    if drop_assertion:
        text = text.replace("    if problems:\n", "    if False:\n")
    (tmp / "sandbox_guard.py").write_text(text, encoding="utf-8")
    return tmp


PROBE = '''\
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sandbox_guard
sandbox_guard.isolate()
sys.path.insert(0, str(HERE / "nevertwice"))
import api
api.remember_lessons([{"type": "mistake", "title": "mutant-probe",
                       "description": "written by the mutation check",
                       "prevention": "this note proves the escape happened"}],
                     project="mutantprobe", embed=False)
print("PROBE WROTE")
'''


def test_a_guard_without_its_assertion_is_proved_to_leak() -> None:
    print("\n- the mutants: remove the pin, remove the assertion -")
    with tempfile.TemporaryDirectory(prefix="nevertwice_stand_in_") as live, \
            tempfile.TemporaryDirectory(prefix="nevertwice_mut_a_") as a, \
            tempfile.TemporaryDirectory(prefix="nevertwice_mut_b_") as b:
        stand_in = Path(live)
        seed_stand_in(stand_in)
        before = fingerprint(stand_in)

        # Mutant A: the 2026-08-25 guard, verbatim - HOME pinned, VAULT left alone, so
        # the exported value wins. The assertion must catch it before anything is
        # written; that is what makes the assertion, and not the pin, load-bearing.
        tree_a = _mutant_tree(Path(a), drop_vault=True, drop_assertion=False)
        (tree_a / "probe.py").write_text(PROBE, encoding="utf-8")
        run_a = subprocess.run([sys.executable, str(tree_a / "probe.py")],
                               env=hostile_env(stand_in), capture_output=True, text=True,
                               timeout=600, encoding="utf-8", errors="replace")
        check("mutant A (VAULT neither scrubbed nor pinned) is stopped", run_a.returncode != 0,
              f"exit {run_a.returncode}")
        check("mutant A names the failure class", "SandboxEscape" in run_a.stderr,
              run_a.stderr.strip()[-200:])
        check("mutant A wrote nothing", fingerprint(stand_in) == before)

        # Mutant B: pin AND assertion gone. The write must land - if it did not, the test
        # above would be passing for some unrelated reason and would never catch a
        # regression in the guard.
        tree_b = _mutant_tree(Path(b), drop_vault=True, drop_assertion=True)
        (tree_b / "probe.py").write_text(PROBE, encoding="utf-8")
        run_b = subprocess.run([sys.executable, str(tree_b / "probe.py")],
                               env=hostile_env(stand_in), capture_output=True, text=True,
                               timeout=600, encoding="utf-8", errors="replace")
        escaped = fingerprint(stand_in) != before
        check("mutant B (same, assertion removed) reaches the stand-in store", escaped,
              f"exit {run_b.returncode}: {(run_b.stdout + run_b.stderr).strip()[-300:]}")
        check("mutant B's write is what the assertion was preventing",
              any("mutant-probe" in k for k in fingerprint(stand_in)),
              ", ".join(sorted(set(fingerprint(stand_in)) - set(before))[:4]))


# ── (c) the lint, and that it catches a planted violation ─────────────

def _lint_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_check_sandbox_probe", LINT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_lint_is_green_and_catches_a_planted_violation() -> None:
    print("\n- the CI lint holds the line for new scripts -")
    proc = subprocess.run([sys.executable, str(LINT)], cwd=ROOT, capture_output=True,
                          text=True, timeout=300, encoding="utf-8", errors="replace")
    check("the repository passes the lint", proc.returncode == 0,
          (proc.stdout + proc.stderr).strip()[-400:])
    check("the lint reports what it scanned", "reach the store" in proc.stdout)

    mod = _lint_module()
    with tempfile.TemporaryDirectory(prefix="nevertwice_lint_") as tmp:
        fake = Path(tmp)
        (fake / "nevertwice").mkdir()
        (fake / "nevertwice" / "api.py").write_text("VAULT = 1\n", encoding="utf-8")
        (fake / "examples").mkdir()
        mod.ROOT, mod.PKG = fake, fake / "nevertwice"
        mod.PROJECT = mod.project_modules()
        check("the probe sees the stub package", "api" in mod.PROJECT)

        good = "import sys\nimport sandbox_guard\nsandbox_guard.isolate()\nimport api\n"
        (fake / "examples" / "ok_demo.py").write_text(good, encoding="utf-8")
        check("an armed script passes", mod.main([]) == 0)

        (fake / "examples" / "bad_demo.py").write_text("import api\n", encoding="utf-8")
        check("an unarmed script fails the lint", mod.main([]) == 1)
        (fake / "examples" / "bad_demo.py").unlink()

        late = "import sys\nimport api\nimport sandbox_guard\nsandbox_guard.isolate()\n"
        (fake / "examples" / "late_demo.py").write_text(late, encoding="utf-8")
        check("arming after the project import fails the lint", mod.main([]) == 1)
        (fake / "examples" / "late_demo.py").unlink()

        home_only = 'import os\nos.environ["NEVERTWICE_HOME"] = "/tmp/x"\n'
        (fake / "examples" / "home_only.py").write_text(home_only, encoding="utf-8")
        check("pinning HOME without VAULT fails the lint", mod.main([]) == 1)
        (fake / "examples" / "home_only.py").unlink()

        check("the lint is green again once the plants are removed", mod.main([]) == 0)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\nsandbox guard: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
