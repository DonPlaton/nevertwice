"""Environment wall: every test that could touch ~/.claude/settings.json builds its
subprocess/process env through this module, never `dict(os.environ)` alone.

Not a suite - `_test_*.py` globs miss it on purpose (`tests/_test_hermeticity.py`'s own
census would otherwise demand it import `_env_guard`, which is backwards: this module IS
part of the wall `_env_guard` builds on top of, for the variables it does not own -
HOME/USERPROFILE, the Claude Code settings/projects overrides, and the store-location
variables `sandbox_guard.py` pins for the in-process suites but a SUBPROCESS running
`install.py` does not inherit any pinning from: `ensure_store()` reads NEVERTWICE_HOME /
NEVERTWICE_VAULT (and the legacy ANAMNESIS_*/CLAUDE_MEMORY_* aliases) straight from its own
environment, and a real (non `--print`) install with only the settings wall applied would
`mkdir` and write a `.gitignore` wherever those resolve on the machine actually running the
suite - found while smoke-testing this file, on a machine whose shell exports
NEVERTWICE_HOME for the owner's own live use.

A previous test of the installer wrote five hooks into the owner's real settings.json
(2026-09-23, restored from the installer's own backup) because a test pinned only
NEVERTWICE_CLAUDE_SETTINGS while the installer under test read only Path.home(). The fix on
the product side is `hookwire.settings_path()` - one definition both honour - but the test
side needs its own guarantee: every env this repository hands to `install.py`, `hosts.py` or
the shim has to be unable to reach ~/.claude even when the code under test is wrong, because
that is exactly when a test runs against it.

F1 (class b): the SAME shape, one layer further out. `walled()` covered every path
`nevertwice/config.py` itself resolves, but the host adapters (`hosts.py`) and the watch
daemon (`watch.py`) read their own SWEEP roots from other names entirely. Measured under an
early version of this wall: `CursorAdapter.roots()` resolved to the real
`%APPDATA%/Cursor/User/workspaceStorage`, and `watch._vscode_globalstorage_bases()` to the
real `%APPDATA%/Code/User/globalStorage` (and `Code - Insiders`) - nothing WRITES to either,
but a walled sweep would READ the owner's real Cursor/VS Code transcripts, and results would
differ between machines. `NEVERTWICE_CODEX_HOME`, `NEVERTWICE_CURSOR_EXPORT`,
`NEVERTWICE_GENERIC_JSONL`, `NEVERTWICE_PROJECT_ROOT(S)`, `NEVERTWICE_TWIN_FILE` (the dedup
gate's machine-local calibration - thresholds would otherwise follow the ambient env) and the
HuggingFace cache variables the optional reranker probes are the same class.

Deliberately NOT walled directly: `APPDATA` (Windows) and `XDG_CONFIG_HOME` (Linux/macOS) -
both are also where `CursorAdapter` and the watch daemon fall back to when their own override
is unset, so pinning them looked like the more general fix. Measured instead of assumed:
pointing `APPDATA` at an empty directory makes `import pytest` (and anything else installed to
user site - Windows resolves user site under `%APPDATA%/Python`) fail in every child process
that inherits a walled() env, which is most of them. So the fix is at the ADAPTER level
instead - `NEVERTWICE_CURSOR_EXPORT` (already existed) and `NEVERTWICE_VSCODE_GLOBALSTORAGE_
ROOT` (added to `watch.py` for this, the smallest hook with the same shape as the Cursor one) -
both checked BEFORE the `APPDATA`/`XDG_CONFIG_HOME` fallback, so pinning them never needs to
touch either.

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

#: Store-location variables `install.py`'s own `ensure_store()` (and, transitively, a real
#: engine run) resolves from the raw environment. Not asserted as strictly as WALL_VARS - a
#: caller may deliberately clear one to test a fallback - but always POINTED inside the wall
#: by `walled()`, so a real (non `--print`) install run under it can never reach a real store
#: even when the ambient shell exports one of these (mirrors `sandbox_guard.STORE_ROOT_VARS`).
STORE_VARS = ("NEVERTWICE_HOME", "NEVERTWICE_VAULT", "ANAMNESIS_HOME", "ANAMNESIS_VAULT",
             "CLAUDE_MEMORY_HOME", "CLAUDE_MEMORY_VAULT")

#: `nevertwice/config.py`'s OWN name for the transcript sweep root - `NEVERTWICE_CLAUDE_PROJECTS`
#: above is `hosts.ClaudeCodeAdapter`'s name for the SAME kind of thing and the two are read by
#: two different consumers (the host adapter vs. the engine's catch-up sweep). Missed on the
#: first cut of this file: `walled()` pinned only the adapter's name, so `NEVERTWICE_
#: PROJECTS_ROOT` (and its own legacy alias `CLAUDE_PROJECTS_ROOT`, no NEVERTWICE_ prefix)
#: passed straight through from the ambient environment into a "walled" subprocess env
#: unchanged - the exact settings.json-leak shape, one variable over, caught by an auditing
#: pass rather than by this file's own test. All three names now get the same directory; they
#: do not have to agree with each other semantically, only each has to stay inside the wall.
PROJECTS_ROOT_VARS = ("NEVERTWICE_PROJECTS_ROOT", "CLAUDE_PROJECTS_ROOT")

#: Points `load_dotenv()` at a custom `.env` - and a file living there could reintroduce a
#: real VAULT/HOME/PROJECTS_ROOT that no shell ever exported (`config.load_dotenv` uses
#: `setdefault`, so a value pinned by this module first always wins - but only for names this
#: module actually sets before the subprocess imports config). Pinned to a path that does not
#: exist: `load_dotenv()` silently skips a missing file, which is exactly "no custom .env".
ENV_FILE_VAR = "NEVERTWICE_ENV_FILE"

#: F1: the host adapters' and the watch daemon's own SWEEP-root overrides, each pinned to a
#: directory that does not exist - `discover()`/`roots()` then read "nothing here", the honest
#: default for a machine with no Cursor/Codex/VS Code install, never the real one. NOT
#: `APPDATA`/`XDG_CONFIG_HOME` themselves - see the module docstring for why those stay out.
ADAPTER_ROOT_VARS = {
    "NEVERTWICE_CODEX_HOME": "codex-home",
    "NEVERTWICE_CURSOR_EXPORT": "cursor-export",
    "NEVERTWICE_VSCODE_GLOBALSTORAGE_ROOT": "vscode-globalstorage",
}

#: A pathsep/comma-separated LIST of extra roots (`GenericJsonlAdapter`'s sweep dirs, the
#: engine's own tracked-project roots) whose safe pin is "configured to nothing" rather than
#: "configured to a directory inside tmp" - empty is the documented default and reads
#: identically to unset, so the ambient value simply never reaches the parser.
EMPTY_LIST_VARS = ("NEVERTWICE_GENERIC_JSONL", "NEVERTWICE_PROJECT_ROOT",
                   "NEVERTWICE_PROJECT_ROOTS")

#: The dedup gate's machine-local calibration file (`_engine_text.py`). Pinned to a
#: nonexistent path so the gate falls back to its BAKED thresholds, deterministic across
#: machines, rather than to whatever calibration the ambient environment happens to point at
#: (F1: "thresholds would follow the ambient env").
TWIN_FILE_VAR = "NEVERTWICE_TWIN_FILE"

#: The HuggingFace cache the optional cross-encoder reranker probes to decide whether to
#: auto-enable itself (`reranker_ce.py`) - a real, potentially multi-gigabyte, machine-local
#: cache. Pinned to a nonexistent directory: the probe reads "not cached", auto-enable stays
#: off, and nothing here ever touches the owner's real cache - the same "a machine-local pin
#: must not leak in" class `sandbox_guard.py` already applies to `NEVERTWICE_EMBED_MODEL`.
HF_CACHE_VARS = ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE")


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
    for name in PROJECTS_ROOT_VARS:
        env[name] = str(tmp / "projects")
    for name in STORE_VARS:
        env[name] = str(tmp / "store")
    env[ENV_FILE_VAR] = str(tmp / "no-such-file.env")
    for name, subdir in ADAPTER_ROOT_VARS.items():
        env[name] = str(tmp / subdir)
    for name in EMPTY_LIST_VARS:
        env[name] = ""
    env[TWIN_FILE_VAR] = str(tmp / "no-such-twin-calibration.json")
    for name in HF_CACHE_VARS:
        env[name] = str(tmp / "hf-cache")
    env["NEVERTWICE_CLOUD"] = "none"
    _assert_walled(env, tmp)
    return env


#: Every name `walled()` itself sets, beyond the strictly-asserted WALL_VARS - used both by
#: `walled_in_process()` (which has to save/restore each one) and by the package-wide AST scan
#: test, which checks this list rather than re-deriving it.
PINNED_VARS = (STORE_VARS + PROJECTS_ROOT_VARS + tuple(ADAPTER_ROOT_VARS) + EMPTY_LIST_VARS
              + HF_CACHE_VARS + (ENV_FILE_VAR, TWIN_FILE_VAR, "NEVERTWICE_CLOUD"))


@contextmanager
def walled_in_process(tmp):
    """Patch `os.environ` to the wall for the duration of the block, then restore it - for
    code under test that reads `os.environ` directly in THIS process rather than through a
    subprocess (`hookwire.settings_path()` among it)."""
    tmp = Path(tmp)
    all_vars = WALL_VARS + PINNED_VARS
    saved = {name: os.environ.get(name) for name in all_vars}
    env = walled(tmp)
    for name in all_vars:
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
