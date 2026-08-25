"""One store sandbox for the whole repository - and it verifies that it worked.

Three times now a test or an example has written into the owner's live memory store
(2026-08-13, 2026-08-18, 2026-08-25). Each time the fix was the same shape and each time
it was applied to one directory only, so the next entry point re-learned the lesson.

The root cause is not "the examples never got the test guard". It is that a guard which
**pins and then trusts** is a hope, not a guarantee:

* `config.VAULT` resolves as ``env("VAULT") or NEVERTWICE_HOME or <default>``, so pinning
  ``NEVERTWICE_HOME`` alone loses to an exported ``NEVERTWICE_VAULT`` - which is exactly how
  a real user points at a real store, a documented and supported configuration. The product
  therefore has to be safe *while that variable is set*, not only when it is absent.
* ``load_dotenv()`` runs at config-import time and reads ``.env`` / ``.secrets.env`` beside
  the package, so a file can reintroduce a store location that no shell ever exported. It
  uses ``setdefault``, so a value pinned here first can no longer be overridden - but only
  if it is pinned under the name config actually prefers.
* Two hand-maintained copies of one idea drift. The test copy scrubbed
  ``NEVERTWICE_TWIN_FILE`` and ``NEVERTWICE_TWIN_SPACE``; the examples copy did not.

So: one module, two declarations, and an assertion instead of a hope.

    import sandbox_guard; sandbox_guard.isolate()       # throwaway store, verified
    import sandbox_guard; sandbox_guard.allow_live(...) # deliberately touches a real store

``isolate()`` scrubs every store-location variable, pins **both** ``NEVERTWICE_HOME`` and
``NEVERTWICE_VAULT`` to a fresh temporary directory, and then **imports config and checks
that it landed there**, raising `SandboxEscape` if it did not. It is idempotent: a research
bench that arms itself while already inside an armed test suite keeps the suite's store
rather than silently starting a second one.

``allow_live(reason)`` is the honest escape hatch for the benches whose documented interface
is ``NEVERTWICE_VAULT=/path python research/<bench>.py``. It writes nothing and pins nothing;
it records a reason, names the store on stderr, and - crucially - does **not** un-isolate a
process that a caller already sandboxed.

``tools/check_sandbox.py`` fails CI when a script under ``examples/``, ``research/`` or
``tools/`` reaches the store without making one of those two declarations first.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

__all__ = [
    "SandboxEscape", "isolate", "allow_live", "verify", "store", "mode", "live_reason",
    "LOCATION_VARS", "STORE_ROOT_VARS", "SIDE_CHANNEL_VARS", "PROJECT_MODULES",
]

ROOT = Path(__file__).resolve().parent
PKG = ROOT / "nevertwice"


class SandboxEscape(RuntimeError):
    """A store path resolved outside the throwaway directory this process pinned.

    Raised loudly rather than warned about: every historical occurrence of this bug was
    caught by a human noticing an odd value in unrelated output, days later.
    """


# Variables that name the store root. Any one of them moves the whole store, and the
# legacy prefixes are bridged into the new names by `config._bridge_legacy_prefixes`.
STORE_ROOT_VARS = (
    "NEVERTWICE_VAULT", "NEVERTWICE_HOME",
    "ANAMNESIS_VAULT", "ANAMNESIS_HOME",
    "CLAUDE_MEMORY_VAULT", "CLAUDE_MEMORY_HOME",
)

# Variables that do not name the store but still reach outside the sandbox: host transcript
# roots, a pointer to a file that can reintroduce any of the above, and machine-local model
# pins whose caches live outside the temporary directory.
SIDE_CHANNEL_VARS = (
    "NEVERTWICE_PROJECT_ROOTS", "NEVERTWICE_PROJECT_ROOT",
    "NEVERTWICE_PROJECTS_ROOT", "CLAUDE_PROJECTS_ROOT",
    "ANAMNESIS_PROJECT_ROOTS", "ANAMNESIS_PROJECT_ROOT",
    "NEVERTWICE_ENV_FILE",          # would reintroduce any of the above from a file
    "NEVERTWICE_EMBED_MODEL",       # a machine-local embedder pin must not leak in
    "NEVERTWICE_TWIN_FILE", "NEVERTWICE_TWIN_SPACE",   # twin-gate calibration, ditto
    "NEVERTWICE_TEST_XRERANK",      # an expensive opt-in must be explicit per run
)

LOCATION_VARS = STORE_ROOT_VARS + SIDE_CHANNEL_VARS


def _project_module_names() -> frozenset:
    """Every module the package ships, read from disk so a new one is covered the day it
    is added rather than the day someone remembers to update a list here."""
    try:
        stems = {p.stem for p in PKG.glob("*.py") if not p.stem.startswith("__")}
    except OSError:                                   # pragma: no cover - no package dir
        stems = set()
    return frozenset(stems | {"nevertwice"})


PROJECT_MODULES = _project_module_names()

_STORE: Path | None = None
_MODE: str | None = None
_LIVE_REASON: str | None = None
_REAL_STORES: tuple = ()


# -- path comparison ---------------------------------------------------
# Windows is a first-class platform here: the same store is reachable as D:\x and d:\X,
# and `Path.resolve()` does not normalise case. Compare normcased strings.

def _norm(p) -> str:
    try:
        s = str(Path(p).resolve())
    except (OSError, ValueError):                     # pragma: no cover - exotic paths
        s = str(p)
    return os.path.normcase(s.rstrip("\\/"))


def _inside(path, root) -> bool:
    a, b = _norm(path), _norm(root)
    return a == b or a.startswith(b + os.sep)


def _real_store_candidates() -> tuple:
    """Where a real store could be on this machine, captured BEFORE scrubbing.

    Two sources: whatever the ambient environment currently points at (the live store on a
    developer's machine), and the defaults `config._default_vault` would fall back to. A
    value that lives only in `.env`/`.secrets.env` is deliberately not read here - that file
    is off-limits - and it does not need to be: check 1 below pins the resolved VAULT itself.
    """
    found = []
    for var in STORE_ROOT_VARS:
        raw = os.environ.get(var)
        if raw:
            found.append(Path(os.path.expanduser(os.path.expandvars(raw))))
    home = Path.home()
    found += [home / ".nevertwice", home / ".anamnesis"]
    seen, out = set(), []
    for p in found:
        key = _norm(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return tuple(out)


def _loaded_project_modules() -> dict:
    out = {}
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if name.split(".")[0] in PROJECT_MODULES:
            out[name] = mod
    return out


def _import_config():
    """Import the repository's `config` the way every entry point here does.

    Bare-name import against `nevertwice/` on `sys.path`, matching the convention used
    throughout the repo. `nevertwice.config` is deliberately NOT force-imported: that would
    pull in whichever `nevertwice` package wins on `sys.path`, possibly the installed one,
    and pin it into `sys.modules` for the rest of the process.
    """
    if str(PKG) not in sys.path:
        sys.path.insert(0, str(PKG))
    try:
        import config                                  # noqa: PLC0415 - deliberately late
        return config
    except Exception:                                  # pragma: no cover - no package
        return None


# -- the two declarations ----------------------------------------------

def isolate(prefix: str = "nevertwice-sandbox-") -> Path:
    """Point the whole process at a throwaway store, then prove it landed there.

    Idempotent: a second call (a bench armed inside an already-armed suite) re-verifies
    and returns the existing store instead of starting a second one.
    """
    global _STORE, _MODE, _REAL_STORES
    if _STORE is not None:
        verify()
        return _STORE

    _REAL_STORES = _real_store_candidates()
    for key in LOCATION_VARS:
        os.environ.pop(key, None)

    _STORE = Path(tempfile.mkdtemp(prefix=prefix))
    # VAULT as well as HOME: config prefers VAULT, and `load_dotenv` uses `setdefault`,
    # so setting it first is what stops an env file beside the package from winning.
    os.environ["NEVERTWICE_HOME"] = str(_STORE)
    os.environ["NEVERTWICE_VAULT"] = str(_STORE)
    os.environ["NEVERTWICE_CLOUD"] = "none"     # no key, no network
    os.environ["NEVERTWICE_XRERANK"] = "0"      # never pull a cached cross-encoder
    _MODE = "sandbox"
    atexit.register(_cleanup)
    verify()
    return _STORE


def allow_live(reason: str, *, quiet: bool = False) -> None:
    """Declare that this entry point deliberately operates on a real store.

    For the benches whose documented interface is a populated vault. Nothing is pinned and
    nothing is scrubbed - the point is only that the intent is written down where the CI
    lint and the next reader can both see it. If a caller has already isolated the process
    (a test suite importing this bench as a library), that isolation stands: this is a
    statement about what the script needs, never a request to leave a sandbox.
    """
    global _MODE, _LIVE_REASON
    if len(reason.strip()) < 12:
        raise ValueError("allow_live() needs a written reason, not a placeholder")
    if _STORE is not None:
        return
    _MODE, _LIVE_REASON = "live", reason.strip()
    if not quiet:
        target = (os.environ.get("NEVERTWICE_VAULT") or os.environ.get("NEVERTWICE_HOME")
                  or "the default store")
        sys.stderr.write(f"sandbox_guard: operating on the LIVE store at {target}\n"
                         f"sandbox_guard: declared reason - {_LIVE_REASON}\n")


# -- the assertion -----------------------------------------------------

def verify() -> None:
    """Assert that the store actually landed in the sandbox. Raises `SandboxEscape`.

    Three checks, because the three incidents failed in three different places:

    1. the two variables this module pinned still point inside the sandbox - catches an
       env file, a later `os.environ` write, or a subprocess env rebuilt by hand;
    2. `config.VAULT`, plus the `VAULT` re-export in every project module already imported,
       resolves inside the sandbox - catches a resolution rule the pin does not cover;
    3. no project module holds *any* `Path` inside a real store on this machine - catches
       the 2026-08-18 shape, where the store moved but a derived constant (`EMBED_CACHE`,
       `EMBED_META`, ...) had already baked the live path.

    A no-op under `allow_live()`: there is no sandbox to escape from.
    """
    if _STORE is None:
        return
    store_dir = _STORE
    problems: list = []

    for var in ("NEVERTWICE_VAULT", "NEVERTWICE_HOME"):
        value = os.environ.get(var)
        if value is None:
            problems.append(f"{var} is unset - the pin was removed")
        elif not _inside(value, store_dir):
            problems.append(f"{var}={value} is outside the sandbox")

    cfg = _import_config()
    if cfg is not None and not _inside(getattr(cfg, "VAULT", store_dir), store_dir):
        problems.append(f"config.VAULT={cfg.VAULT} is outside the sandbox")

    for name, mod in _loaded_project_modules().items():
        vault = getattr(mod, "VAULT", None)
        if vault is not None and not _inside(vault, store_dir):
            problems.append(f"{name}.VAULT={vault} is outside the sandbox")
        for attr, value in list(vars(mod).items()):
            if attr.startswith("_") or not isinstance(value, Path):
                continue
            for real in _REAL_STORES:
                if _inside(value, real):
                    problems.append(f"{name}.{attr}={value} is inside a real store")
                    break

    if problems:
        raise SandboxEscape(
            "store paths escaped the sandbox at " + str(store_dir) + ":\n  - "
            + "\n  - ".join(problems)
            + "\n\nThis is the failure class behind the 2026-08-13, 2026-08-18 and"
              " 2026-08-25 incidents. Nothing was written: the process is stopping first."
        )


# -- accessors and teardown --------------------------------------------

def store():
    """The throwaway store, or None under `allow_live()`."""
    return _STORE


def mode():
    """'sandbox', 'live', or None when neither declaration has been made."""
    return _MODE


def live_reason():
    return _LIVE_REASON


def _cleanup() -> None:
    if _STORE is not None:
        shutil.rmtree(_STORE, ignore_errors=True)
