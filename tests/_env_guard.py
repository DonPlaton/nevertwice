"""Hermeticity guard - import BEFORE any nevertwice module, in every test.

Two real incidents motivated this (2026-08-13 and 2026-08-18): the developer's
shell exported NEVERTWICE_VAULT pointing at the LIVE store, so import-time path
constants (EMBED_CACHE, EMBED_META, PROCESSED_DB, ...) baked the live paths even
though the test then patched m.VAULT to a sandbox. Files went to the sandbox;
the embedding cache, its .bak and the meta went to the LIVE vault - on 2026-08-18
a routine test batch overwrote a 4319-entry production cache with 19 fixtures.

Scrubs the STORE-LOCATION env vars only (a test's own feature flags like
NEVERTWICE_BRAIN survive) and pins BOTH NEVERTWICE_HOME and NEVERTWICE_VAULT to a
fresh temp dir, so even an unpatched constant can only ever point into a sandbox.
Subprocess-spawning tests inherit the scrubbed environment automatically.

Pinning the VAULT name too is not belt-and-braces (review 2026-08-24, reproduced):
config.py resolves `env("VAULT") or NEVERTWICE_HOME`, and since load_dotenv() moved
to config-import time a `.env`/`.secrets.env` in the repo can supply NEVERTWICE_VAULT
(or a legacy alias, bridged) - which outranks a HOME-only pin and lands the whole
suite back in the live store. `load_dotenv` uses setdefault, so a var this guard sets
first can no longer be overridden by any file. NEVERTWICE_ENV_FILE is scrubbed for
the same reason: it points load_dotenv at an arbitrary file.
"""
import atexit
import os
import shutil
import tempfile

_LOCATION_VARS = (
    "NEVERTWICE_VAULT", "NEVERTWICE_HOME",
    "ANAMNESIS_VAULT", "ANAMNESIS_HOME",
    "CLAUDE_MEMORY_VAULT", "CLAUDE_MEMORY_HOME",
    "NEVERTWICE_PROJECT_ROOTS", "NEVERTWICE_PROJECT_ROOT",
    "NEVERTWICE_PROJECTS_ROOT", "CLAUDE_PROJECTS_ROOT",
    "ANAMNESIS_PROJECT_ROOTS", "ANAMNESIS_PROJECT_ROOT",
    "NEVERTWICE_ENV_FILE",          # would re-introduce any of the above from a file
    "NEVERTWICE_EMBED_MODEL",       # a machine-local embedder pin must not leak in
    "NEVERTWICE_TWIN_FILE", "NEVERTWICE_TWIN_SPACE",   # ditto, twin-gate calibration
    "NEVERTWICE_TEST_XRERANK",      # expensive research opt-in must be explicit per test run
)
for _k in _LOCATION_VARS:
    os.environ.pop(_k, None)

_TMP_HOME = tempfile.mkdtemp(prefix="nevertwice_test_home_")
os.environ["NEVERTWICE_HOME"] = _TMP_HOME
os.environ["NEVERTWICE_VAULT"] = _TMP_HOME     # wins over any env-file value (setdefault)
# A cached optional cross-encoder auto-enables in production. Test fixtures must
# never load that ~2 GB model or touch a GPU unless a test overrides this switch.
os.environ["NEVERTWICE_XRERANK"] = "0"
atexit.register(lambda: shutil.rmtree(_TMP_HOME, ignore_errors=True))
