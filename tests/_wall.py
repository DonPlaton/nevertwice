"""Environment wall: every test that could touch ~/.claude/settings.json builds its
subprocess/process env through this module, never `dict(os.environ)` alone.

Not a suite - `_test_*.py` globs miss it on purpose (`tests/_test_hermeticity.py`'s own
census would otherwise demand it import `_env_guard`, which is backwards: this module IS
part of the wall `_env_guard` builds on top of, for the two variables it does not own -
HOME/USERPROFILE and the Claude Code settings/projects overrides).

A previous test of the installer wrote five hooks into the owner's real settings.json
(2026-09-23, restored from the installer's own backup) because a test pinned only
NEVERTWICE_CLAUDE_SETTINGS while the installer under test read only Path.home(). The fix on
the product side is `hookwire.settings_path()` - one definition both honour - but the test
side needs its own guarantee: every env this repository hands to `install.py`, `hosts.py` or
the shim has to be unable to reach ~/.claude even when the code under test is wrong, because
that is exactly when a test runs against it.

    from _wall import walled, walled_in_process, settings_unchanged
    env = walled(tmp)                       # subprocess env dict
    with walled_in_process(tmp):            # in-process os.environ patch
        ...
    assert settings_unchanged()             # in every suite's test_zz
"""
from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from pathlib import Path

#: The four variables that decide where a hook-wiring call can write. Every one of them has
#: to land inside the same temp directory, or a call under test can reach the real
#: ~/.claude/settings.json - the incident this file exists to make structurally impossible.
WALL_VARS = ("HOME", "USERPROFILE", "NEVERTWICE_CLAUDE_SETTINGS", "NEVERTWICE_CLAUDE_PROJECTS")


def _real_settings_path() -> Path:
    return Path(os.environ.get("NEVERTWICE_CLAUDE_SETTINGS")
                or Path.home() / ".claude" / "settings.json")


def _sha256_or_none(path: Path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


#: Captured once, at import - before any test in this process has had a chance to move HOME -
#: so every suite's `test_zz_every_check_passed` can assert the file this process started with
#: is the file it ends with, byte for byte. Read only to hash it; never printed, never parsed.
REAL_SETTINGS_PATH = _real_settings_path()
REAL_SETTINGS_SIGNATURE = _sha256_or_none(REAL_SETTINGS_PATH)


def _assert_walled(env: dict, tmp: Path) -> None:
    tmp_r = str(Path(tmp).resolve())
    for name in WALL_VARS:
        value = env.get(name)
        if not value:
            raise AssertionError(f"{name} is unset in a walled env - refusing to hand it out")
        resolved = str(Path(value).resolve())
        if resolved != tmp_r and not resolved.startswith(tmp_r + os.sep):
            raise AssertionError(f"{name}={value} escapes the wall at {tmp}")


def walled(tmp) -> dict:
    """A subprocess env dict pointed entirely inside `tmp`.

    Built from the current environment (a subprocess should still see PATH, PYTHONPATH and
    the rest), with HOME, USERPROFILE and both Nevertwice Claude Code overrides repointed.
    Asserts before returning: an env this wrong has to fail loudly here, not quietly wire a
    real settings.json three calls later.
    """
    tmp = Path(tmp)
    env = dict(os.environ)
    env["HOME"] = str(tmp)
    env["USERPROFILE"] = str(tmp)
    env["NEVERTWICE_CLAUDE_SETTINGS"] = str(tmp / "settings.json")
    env["NEVERTWICE_CLAUDE_PROJECTS"] = str(tmp / "projects")
    _assert_walled(env, tmp)
    return env


@contextmanager
def walled_in_process(tmp):
    """Patch `os.environ` to the wall for the duration of the block, then restore it - for
    code under test that reads `os.environ` directly in THIS process rather than through a
    subprocess (`hookwire.settings_path()` among it)."""
    tmp = Path(tmp)
    saved = {name: os.environ.get(name) for name in WALL_VARS}
    env = walled(tmp)
    for name in WALL_VARS:
        os.environ[name] = env[name]
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def settings_unchanged() -> bool:
    """Has the real settings.json this process started with changed since import?"""
    return _sha256_or_none(REAL_SETTINGS_PATH) == REAL_SETTINGS_SIGNATURE
