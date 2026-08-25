"""Hermeticity guard - import BEFORE any nevertwice module, in every test.

Two real incidents motivated this (2026-08-13 and 2026-08-18): the developer's shell
exported NEVERTWICE_VAULT pointing at the LIVE store, so import-time path constants
(EMBED_CACHE, EMBED_META, PROCESSED_DB, ...) baked the live paths even though the test then
patched m.VAULT to a sandbox. Files went to the sandbox; the embedding cache, its .bak and
the meta went to the LIVE vault - on 2026-08-18 a routine test batch overwrote a 4319-entry
production cache with 19 fixtures.

A third incident on 2026-08-25 came from an *example*, not a test, because the policy lived
here in a copy only the tests imported. It now lives in one place for the whole repository:

    sandbox_guard.py     the scrub list, the pin, and the assertion that the pin worked

This file is the tests' two-line entry point into it and holds no policy of its own, so
there is nothing here that can drift away from what the examples and benches enforce.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sandbox_guard  # noqa: E402 - the path insert above is what makes this importable

_TMP_HOME = str(sandbox_guard.isolate(prefix="nevertwice_test_home_"))
