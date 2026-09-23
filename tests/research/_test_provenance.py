#!/usr/bin/env python3
"""`research/_provenance.py`: the shared `stamp(payload)` helper every listed stand calls before
writing its artifact - "an artifact proves its own commit instead of leaning on a time window"
(the auditor's ask). Covers the helper itself (deterministic, no ambient-repo-state dependence -
`git_commit`/`is_dirty` are exercised through a monkeypatched `subprocess.run`, not by asserting
on whatever this checkout's OWN dirty state happens to be right now) and one real stand-level
check: `research/cheap_baselines.py`'s `build(save=True)` runs entirely without Ollama (the
default `_SUMMARISER = "extractive"` path) and its saved artifact carries the stamp.

    python tests/research/_test_provenance.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import _env_guard  # noqa: F401, E402 - hermetic store before any project import
sys.path.insert(0, str(ROOT / "research"))
import _provenance as prov  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


class _FakeCompleted:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_git_commit_returns_the_real_head() -> None:
    print("\n- git_commit() matches git rev-parse HEAD -")
    import subprocess
    real = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(prov.ROOT),
                          capture_output=True, text=True, check=True).stdout.strip()
    check("matches", prov.git_commit() == real, f"{prov.git_commit()} vs {real}")


def test_git_commit_degrades_on_a_git_failure_without_raising() -> None:
    print("\n- git_commit() degrades to a '?' placeholder rather than raising -")
    saved_root = prov.ROOT
    with tempfile.TemporaryDirectory() as td:
        prov.ROOT = Path(td)              # not a git repository at all
        try:
            result = prov.git_commit()
        finally:
            prov.ROOT = saved_root
    check("no exception reached the caller (we got here at all)", True)
    check("the placeholder names that it failed", result.startswith("?"), result)


def test_is_dirty_reflects_the_git_diff_returncode() -> None:
    print("\n- is_dirty() follows subprocess's returncode deterministically (monkeypatched) -")
    saved = prov.subprocess.run
    try:
        prov.subprocess.run = lambda *a, **k: _FakeCompleted(returncode=0)
        check("returncode 0 (no diff) -> not dirty", prov.is_dirty() is False)
        prov.subprocess.run = lambda *a, **k: _FakeCompleted(returncode=1)
        check("returncode 1 (a diff exists) -> dirty", prov.is_dirty() is True)

        def boom(*a, **k):
            raise OSError("no git on PATH")
        prov.subprocess.run = boom
        check("a git failure reads as dirty (unprovable clean is not a clean)",
              prov.is_dirty() is True)
    finally:
        prov.subprocess.run = saved


def test_measured_at_shape() -> None:
    print("\n- measured_at() has the three keys, utc in the right format -")
    m = prov.measured_at()
    check("exactly commit/utc/dirty", set(m) == {"commit", "utc", "dirty"}, str(m))
    check("dirty is a bool", isinstance(m["dirty"], bool), str(m["dirty"]))
    check("utc matches YYYY-MM-DDTHH:MM:SSZ",
          bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", m["utc"])), m["utc"])


def test_stamp_mutates_in_place_and_returns_the_same_object() -> None:
    print("\n- stamp(payload) mutates in place, returns the same dict, touches nothing else -")
    payload = {"a": 1, "arms": {"x": 2}}
    result = prov.stamp(payload)
    check("returns the SAME object (identity, not a copy)", result is payload)
    check("measured_at was added", "measured_at" in payload, str(payload))
    check("every other key survives untouched", payload["a"] == 1 and payload["arms"] == {"x": 2},
          str(payload))
    check("measured_at has the same shape as measured_at()", set(payload["measured_at"]) ==
          {"commit", "utc", "dirty"}, str(payload["measured_at"]))


def test_stamp_does_not_clobber_an_existing_differently_named_provenance_field() -> None:
    print("\n- stamp() adds measured_at alongside an existing code_sha/engine_commit field -")
    payload = {"code_sha": "abc123", "engine_commit": "def456"}
    prov.stamp(payload)
    check("code_sha survives", payload["code_sha"] == "abc123", str(payload))
    check("engine_commit survives", payload["engine_commit"] == "def456", str(payload))
    check("measured_at was added alongside both", "measured_at" in payload, str(payload))


def test_mutation_a_broken_is_dirty_would_silence_a_real_warning() -> None:
    """A monkeypatch of `prov.is_dirty` to always report clean, restored in `finally` - proves
    the warning print is driven by the real dirty check, not printed unconditionally or never."""
    import io
    from contextlib import redirect_stderr
    print("\n- mutation: is_dirty() forced False silences the dirty warning even when dirty -")
    saved = prov.is_dirty
    prov.is_dirty = lambda *a, **k: False
    try:
        buf = io.StringIO()
        with redirect_stderr(buf):
            payload = prov.stamp({})
    finally:
        prov.is_dirty = saved
    check("mutation: no warning printed, and dirty reads False in the stamp even though the "
          "real tree may be dirty (would FAIL the real dirty-warning path)",
          "WARNING" not in buf.getvalue() and payload["measured_at"]["dirty"] is False,
          buf.getvalue())
    # sanity: with a forced-dirty stub, the unmutated code path DOES warn.
    saved2 = prov.is_dirty
    prov.is_dirty = lambda *a, **k: True
    try:
        buf2 = io.StringIO()
        with redirect_stderr(buf2):
            payload2 = prov.stamp({})
    finally:
        prov.is_dirty = saved2
    check("sanity: forced-dirty prints the warning and stamps dirty: true",
          "WARNING" in buf2.getvalue() and payload2["measured_at"]["dirty"] is True,
          buf2.getvalue())


def test_stand_level_cheap_baselines_artifact_carries_the_stamp() -> None:
    """The one real, end-to-end stand-level check: cheap_baselines.py needs no Ollama on its
    default (extractive) summariser path, so this runs the REAL build(save=True) - not a stub -
    and reads the stamp back from the file it actually wrote."""
    print("\n- stand-level: cheap_baselines.build(save=True) writes a stamped artifact -")
    sys.path.insert(0, str(ROOT / "research"))
    import cheap_baselines as cb                                    # noqa: PLC0415
    saved_out = cb.OUT
    with tempfile.TemporaryDirectory() as td:
        cb.OUT = Path(td) / "cheap_baselines_test.json"
        try:
            payload = cb.build(save=True)
        finally:
            cb.OUT = saved_out
        out_path = Path(td) / "cheap_baselines_test.json"
        check("the file was written", out_path.exists())
        on_disk = json.loads(out_path.read_text(encoding="utf-8"))
    check("the RETURNED payload carries measured_at", "measured_at" in payload, str(payload.keys()))
    check("the ON-DISK artifact carries the same stamp",
          on_disk.get("measured_at") == payload.get("measured_at"), str(on_disk.get("measured_at")))
    check("the stamp's commit matches git_commit()",
          on_disk["measured_at"]["commit"] == prov.git_commit(), str(on_disk["measured_at"]))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them, and
    reports them passed while `check()` printed FAIL and the script would exit 1. Enforced for
    every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_git_commit_returns_the_real_head,
               test_git_commit_degrades_on_a_git_failure_without_raising,
               test_is_dirty_reflects_the_git_diff_returncode,
               test_measured_at_shape,
               test_stamp_mutates_in_place_and_returns_the_same_object,
               test_stamp_does_not_clobber_an_existing_differently_named_provenance_field,
               test_mutation_a_broken_is_dirty_would_silence_a_real_warning,
               test_stand_level_cheap_baselines_artifact_carries_the_stamp):
        fn()
    print(f"\nprovenance: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
