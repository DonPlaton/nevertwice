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
    "TRANSCRIPT_ROOT_VARS", "BRIDGED_PREFIXES", "under_every_prefix",
    "forbid_ollama", "CLOSED_OLLAMA", "OLLAMA_PORT",
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

#: Every prefix a NEVERTWICE_* name can arrive under: `config._bridge_legacy_prefixes` mirrors
#: ANAMNESIS_X and CLAUDE_MEMORY_X into NEVERTWICE_X, at import and again after every
#: `load_dotenv()`. Scrubbing only the NEVERTWICE_ spelling left the mirror standing, and the
#: next bridge run put the value back AFTER the scrub - ANAMNESIS_ENV_FILE named a file that was
#: then read (auditor's probe on 0a2c0ad, 2026-09-25). Pinned equal to config.LEGACY_PREFIXES by a
#: test; not imported from config, because importing config runs load_dotenv before the scrub.
BRIDGED_PREFIXES = ("NEVERTWICE_", "ANAMNESIS_", "CLAUDE_MEMORY_")


def under_every_prefix(name: str) -> tuple:
    """`name` spelled under each bridged prefix; a name with none of them is only itself."""
    for prefix in BRIDGED_PREFIXES:
        if name.startswith(prefix):
            return tuple(p + name[len(prefix):] for p in BRIDGED_PREFIXES)
    return (name,)


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


#: Variables that name the host agent's TRANSCRIPT root, and the default it falls back to.
#: Not a store - nothing is written there - but a real directory of the owner's sessions, and
#: `sweep_unprocessed` READS it. Listed here so it counts as a live path for check 3.
TRANSCRIPT_ROOT_VARS = ("NEVERTWICE_PROJECTS_ROOT", "CLAUDE_PROJECTS_ROOT")


def _real_store_candidates() -> tuple:
    """Where a real store or transcript root could be on this machine, captured BEFORE
    scrubbing.

    Three sources: whatever the ambient environment currently points at (the live store on a
    developer's machine), the defaults `config._default_vault` would fall back to, and the
    host agent's transcript root. A value that lives only in `.env`/`.secrets.env` is
    deliberately not read here - that file is off-limits - and it does not need to be: check 1
    below pins the resolved VAULT itself.

    The transcript root is here because scrubbing does not protect it - it OPENS it. Removing
    `NEVERTWICE_PROJECTS_ROOT` sends `config` to its default, and the default is the real
    `~/.claude/projects`; a sandboxed suite could therefore see every live transcript on the
    machine while this list, which held only store roots, said nothing (found 2026-09-21).
    """
    found = []
    for var in STORE_ROOT_VARS:
        raw = os.environ.get(var)
        if raw:
            found.append(Path(os.path.expanduser(os.path.expandvars(raw))))
    home = Path.home()
    found += [home / ".nevertwice", home / ".anamnesis"]
    for var in TRANSCRIPT_ROOT_VARS:
        raw = os.environ.get(var)
        if raw:
            found.append(Path(os.path.expanduser(os.path.expandvars(raw))))
    found.append(home / ".claude" / "projects")
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


# -- the machine's Ollama, which no test reaches ------------------------

#: Where a test process and every child it starts send model traffic: a closed port. Every name
#: the engine, `doctor` and the stands read for an endpoint is here - the engine's three URLs,
#: OLLAMA_HOST (doctor, the ollama client), OLLAMA_BASE_URL (head_to_head, supersession_bench,
#: frontier_eval, gen_code_sessions), OLLAMA_OPENAI_BASE (the graphiti arm's OpenAI-compatible
#: base) and OLLAMA_API_BASE (litellm) - so a child that never imports this module (an example, a
#: stand run as a command) inherits a refused endpoint instead of the live one. Only the ADDRESS is
#: closed; the paths stay Ollama's own, because the pacer and the tests' fakes classify a call by
#: its path (`/api/embed` is an embed) - a made-up path turned every failed embed into an
#: unclassified call and silently disarmed P0(a)'s invalidity in five stand suites.
#:
#: NOT covered by the environment, and said so rather than implied: a child that hardcodes the
#: port - research/invariants_lab/measure_*.py, research/embed_universal/serving_check.py,
#: gen_corpus.py and gen_pairs.py write `127.0.0.1:11434` / `localhost:11434` literally. A test
#: that started one of those as a child would reach the live server; for children the proof is
#: the server-log witness over the battery window, not this table.
#:
#: Port 0, not a closed port such as 9: on Windows a refused loopback connect costs about two
#: seconds (the stack retries the SYN after the reset), measured 2037 ms per attempt here, and a
#: battery pays it on every retry of every call; port 0 is not connectable and fails at once
#: (WSAEADDRNOTAVAIL, 2 ms; ECONNREFUSED on Linux). Loopback, so no system proxy is consulted -
#: `0.0.0.0` was measured going out through the machine's HTTP proxy instead.
CLOSED_OLLAMA = {
    "OLLAMA_URL": "http://127.0.0.1:0/api/generate",
    "OLLAMA_TAGS_URL": "http://127.0.0.1:0/api/tags",
    "OLLAMA_EMBED_URL": "http://127.0.0.1:0/api/embed",
    "OLLAMA_HOST": "http://127.0.0.1:0",
    "OLLAMA_BASE_URL": "http://127.0.0.1:0",
    "OLLAMA_OPENAI_BASE": "http://127.0.0.1:0/v1",
    "OLLAMA_API_BASE": "http://127.0.0.1:0",
}
#: The live server's port on this machine and on any machine that runs Ollama by default.
OLLAMA_PORT = 11434
_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0", ""}
_FORBIDDEN = False


def _ollama_target(address) -> bool:
    try:
        host, port = address[0], address[1]
    except (TypeError, IndexError):
        return False
    return port == OLLAMA_PORT and str(host).lower() in _LOOPBACK


def forbid_ollama() -> None:
    """Refuse every connection a TEST process opens to the machine's Ollama, and point every child
    at a closed port. Called from `tests/_env_guard.py` only - never from `isolate()`: benches
    call `isolate()` and do talk to a model on purpose.

    Why (stage D, the auditor's witness in server.log): a battery sent 277 POST /api/embed and 54
    GET /api/tags to the live server - fixtures that forgot to stub the embedder, `doctor`
    probes, examples run as children. A test that silently reaches a real model measures the
    machine, not the code, and it can load a model onto the GPU in the middle of a campaign step.

    Two seams, because there are two ways a Python process opens a TCP connection:
    - `socket.socket.connect` / `connect_ex`: every blocking client (urllib, requests, httpx sync,
      `socket.create_connection`) and asyncio's selector loop, which calls `sock.connect` itself;
    - `asyncio.windows_events.IocpProactor.connect`: the default loop on Windows is the proactor,
      which connects through `_overlapped.ConnectEx` and never calls `socket.socket.connect` - the
      auditor's probe connected `asyncio.open_connection` and `httpx.AsyncClient` straight through
      the first seam alone (graphiti's AsyncOpenAI, ollama's AsyncClient, litellm's acompletion).
    A fake server a test starts on a random port is untouched by both."""
    global _FORBIDDEN
    for key, value in CLOSED_OLLAMA.items():
        os.environ[key] = value
    if _FORBIDDEN:
        return
    import errno                                    # noqa: PLC0415
    import socket                                   # noqa: PLC0415

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def _refuse(address):
        raise ConnectionRefusedError(
            errno.ECONNREFUSED,
            f"sandbox_guard: a test may not reach the machine's Ollama ({address[0]}:{address[1]})")

    def connect(self, address):
        if _ollama_target(address):
            _refuse(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        if _ollama_target(address):
            return errno.ECONNREFUSED               # what a closed port answers on this platform
        return real_connect_ex(self, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex

    if sys.platform == "win32":
        from asyncio import windows_events         # noqa: PLC0415

        real_iocp_connect = windows_events.IocpProactor.connect

        def iocp_connect(self, conn, address):
            if _ollama_target(address):
                _refuse(address)
            return real_iocp_connect(self, conn, address)

        windows_events.IocpProactor.connect = iocp_connect
    _FORBIDDEN = True


# -- the two declarations ----------------------------------------------

#: git settings every sandboxed process hands to every git it starts.
GIT_HOUSEKEEPING_OFF = (("maintenance.auto", "false"), ("gc.auto", "0"))


def _no_git_housekeeping() -> None:
    """No background housekeeping in a sandbox's repositories.

    Git runs auto-gc and auto-maintenance after a commit, in a child it does not wait for, and
    that child creates and deletes files under `.git/objects/` while the process is still walking
    or removing the tree. macOS jobs of runs 35781171527 and 35794625037 failed on exactly that,
    once in the product's backup and once in a test's own cleanup. The product keeps tolerating it
    - a live store IS maintained - but a sandbox store has nothing to maintain, and switching it
    off makes a run deterministic instead of lucky. It lives here, with the rest of the sandbox,
    so tests, examples and benches that make repositories all get it.

    Carried in the environment (GIT_CONFIG_COUNT, git >= 2.31), so every git the process starts
    inherits it. Keys already present are kept and not repeated.
    """
    n = int(os.environ.get("GIT_CONFIG_COUNT", "0") or 0)
    have = {os.environ.get(f"GIT_CONFIG_KEY_{i}") for i in range(n)}
    for key, value in GIT_HOUSEKEEPING_OFF:
        if key in have:
            continue
        os.environ[f"GIT_CONFIG_KEY_{n}"] = key
        os.environ[f"GIT_CONFIG_VALUE_{n}"] = value
        n += 1
    os.environ["GIT_CONFIG_COUNT"] = str(n)


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
        for spelling in under_every_prefix(key):
            os.environ.pop(spelling, None)
    # No env file but one this process names itself: the fixed `.env` / `.secrets.env` beside
    # the package and at the clone root are the owner's, not the sandbox's (config.load_dotenv).
    os.environ["NEVERTWICE_DOTENV"] = "explicit"

    _STORE = Path(tempfile.mkdtemp(prefix=prefix))
    # VAULT as well as HOME: config prefers VAULT, and `load_dotenv` uses `setdefault`,
    # so setting it first is what stops an env file beside the package from winning.
    os.environ["NEVERTWICE_HOME"] = str(_STORE)
    os.environ["NEVERTWICE_VAULT"] = str(_STORE)
    os.environ["NEVERTWICE_CLOUD"] = "none"     # no key, no network
    os.environ["NEVERTWICE_XRERANK"] = "0"      # never pull a cached cross-encoder
    # The TRANSCRIPT root, pinned like the store and for the same reason. Scrubbing it alone
    # does not isolate it - it OPENS it: with no variable, `config` falls back to the real
    # `~/.claude/projects`, so an isolated process could read every live transcript on the
    # machine (found 2026-09-21; 758 of them here). Nothing writes there, but
    # `sweep_unprocessed` reads, and would mine the owner's sessions into a throwaway vault.
    (_STORE / "transcripts").mkdir(parents=True, exist_ok=True)
    os.environ["NEVERTWICE_PROJECTS_ROOT"] = str(_STORE / "transcripts")
    _no_git_housekeeping()
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

def _live_store_paths() -> list:
    """Every `Path` on a loaded project module that points inside a real store on this
    machine - the 2026-08-18 shape, where the store moved but a derived constant
    (`EMBED_CACHE`, `EMBED_META`, ...) had already baked the live path."""
    found = []
    for name, mod in _loaded_project_modules().items():
        for attr, value in list(vars(mod).items()):
            if attr.startswith("_") or not isinstance(value, Path):
                continue
            for real in _REAL_STORES:
                if _inside(value, real):
                    found.append(f"{name}.{attr}={value} is inside a real store")
                    break
    return found


def verify_no_live_paths() -> None:
    """The third check of `verify()`, on its own, for callers that move the store again.

    `verify()` runs at `isolate()`, when one project module is loaded and this check has
    almost nothing to look at; the imports that bake path constants happen after. A fixture
    that re-bases the vault later (tests/_sandbox.make_sandbox) is the moment there IS
    something to check, and it cannot call `verify()` itself - its vault is a fresh temp dir,
    not the one `isolate()` pinned, so checks one and two would refuse it. This check does
    not depend on which sandbox is in force: it asks only whether a constant points at a
    store that is real.
    """
    if _STORE is None:
        return
    problems = _live_store_paths()
    if problems:
        raise SandboxEscape(
            "a module constant points inside a real store:\n  - "
            + "\n  - ".join(problems)
            + "\n\nThis is the 2026-08-18 failure class - the store moved after a"
              " derived constant had already baked the live path. Nothing was written:"
              " the process is stopping first."
        )


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
    problems += _live_store_paths()

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
