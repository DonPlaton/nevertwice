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

#: A suite that prints Cyrillic must not die of its console. Every suite imports this file, so
#: the reconfiguration lives here rather than in the two that happened to be caught: on the
#: Windows runner stdout is cp1252, and `_test_slug_both_sides` and `_test_stemmer` both died
#: with `UnicodeEncodeError: 'charmap' codec can't encode` while printing an `ok` line - not a
#: failure of anything they check. This machine's console is cp1251, which encodes Cyrillic, so
#: the defect was invisible here and appeared on CI's first matrix run (2026-09-22).
#:
#: `errors="replace"` rather than a hard UTF-8: a test's job is to report its verdict, and a
#: character that cannot be rendered must cost a glyph, never the verdict. Guarded because a
#: stream that is not a real console - a pipe, a capture, a pytest buffer - may not support it.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

_TMP_HOME = str(sandbox_guard.isolate(prefix="nevertwice_test_home_"))
#: Tests only: no socket to the machine's Ollama, and every child pointed at a closed port
#: (stage D, (б) hermeticity - a battery used to send the live server hundreds of requests).
sandbox_guard.forbid_ollama()
