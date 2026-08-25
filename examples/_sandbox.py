"""Throwaway store for the demos - import BEFORE any nevertwice module.

The README tells a stranger these examples use a throwaway store and leave their real vault
untouched. On 2026-08-25 that promise turned out to be half-kept: three demos pinned
`NEVERTWICE_HOME` and nothing else, and `config.py` resolves

    VAULT = env("VAULT") or NEVERTWICE_HOME or <default>

so an exported `NEVERTWICE_VAULT` - which is exactly how a person with a real install points
at their store - outranked the pin, and running the demo wrote a fabricated "mistake" note
into the live vault.

The policy that prevents it now lives in one module for the whole repository, `sandbox_guard`,
which pins both names *and then asserts that config landed in the sandbox*. This file is the
demos' entry point into it and holds no policy of its own.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sandbox_guard  # noqa: E402 - the path insert above is what makes this importable

# `str`, not `Path`: scenario_demo.py passes it straight into an environment dict.
STORE = str(sandbox_guard.isolate(prefix="nevertwice-demo-"))
