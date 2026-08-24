"""Throwaway store for the demos - import BEFORE any nevertwice module.

The README tells a stranger these examples use a throwaway store and leave their real
vault untouched. That promise was only half-kept: three of the demos pinned
`NEVERTWICE_HOME` and nothing else, and `config.py` resolves

    VAULT = env("VAULT") or NEVERTWICE_HOME or <default>

so an exported `NEVERTWICE_VAULT` - which is exactly how a person with a real install
points at their store - outranked the pin. Running the demo then wrote a fabricated
"mistake" note into the live vault, distilled a guard from it, and let the hook
auto-commit both.

This is the same failure the test sandbox was written for after the 2026-08-13 and
2026-08-18 incidents (`tests/_env_guard.py`); the examples simply never got it. The fix
is the same: scrub every store-location variable, including the legacy aliases and the
env-file pointer, then pin **both** HOME and VAULT to a fresh temporary directory.

`load_dotenv` uses `setdefault`, so a value set here can no longer be overridden by a
`.env` or `.secrets.env` sitting next to the package.
"""
from __future__ import annotations

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
)
for _k in _LOCATION_VARS:
    os.environ.pop(_k, None)

STORE = tempfile.mkdtemp(prefix="nevertwice-demo-")
os.environ["NEVERTWICE_HOME"] = STORE
os.environ["NEVERTWICE_VAULT"] = STORE     # outranks an env-file value (setdefault)
os.environ["NEVERTWICE_CLOUD"] = "none"    # no key, no network
os.environ["NEVERTWICE_XRERANK"] = "0"     # never pull a cached cross-encoder for a demo

atexit.register(lambda: shutil.rmtree(STORE, ignore_errors=True))
