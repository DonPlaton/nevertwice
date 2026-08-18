"""Hermeticity guard - import BEFORE any nevertwice module, in every test.

Two real incidents motivated this (2026-08-13 and 2026-08-18): the developer's
shell exported NEVERTWICE_VAULT pointing at the LIVE store, so import-time path
constants (EMBED_CACHE, EMBED_META, PROCESSED_DB, ...) baked the live paths even
though the test then patched m.VAULT to a sandbox. Files went to the sandbox;
the embedding cache, its .bak and the meta went to the LIVE vault - on 2026-08-18
a routine test batch overwrote a 4319-entry production cache with 19 fixtures.

Scrubs the STORE-LOCATION env vars only (a test's own feature flags like
NEVERTWICE_BRAIN survive) and pins NEVERTWICE_HOME to a fresh temp dir, so even
an unpatched constant can only ever point into a sandbox. Subprocess-spawning
tests inherit the scrubbed environment automatically.
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
    "NEVERTWICE_EMBED_MODEL",       # a machine-local embedder pin must not leak in
)
for _k in _LOCATION_VARS:
    os.environ.pop(_k, None)

_TMP_HOME = tempfile.mkdtemp(prefix="nevertwice_test_home_")
os.environ["NEVERTWICE_HOME"] = _TMP_HOME
atexit.register(lambda: shutil.rmtree(_TMP_HOME, ignore_errors=True))
