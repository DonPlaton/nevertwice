"""One sandbox for every hand-rolled suite (review 2026-08 Dc10 - three fixtures
had drifted: two of them missed LOG_FILE / PROMPT_RECALL_STATE_DIR, so those
paths kept pointing wherever the previous test left them).

Uses m._rebase_vault - the structural fix from the 2026-08 live-cache incidents -
so EVERY vault-derived module constant moves to the temp dir in one call.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sandbox_guard  # noqa: E402 - the path insert above is what makes it importable


def make_sandbox(m, prefix: str = "nwtest_", offline: bool = False) -> Path:
    """Fresh temp vault for module `m` (memory_hook). `offline=True` additionally
    stubs git + the embedder so no section can reach the live embedder or repo -
    the D14 hermeticity class (cloud leak / Ollama connect-timeout per write).

    Also-fix (xhigh review): three K8 module constants are read from the environment ONCE at
    import time (`m.EARLIER_MAX_CHARS`, `m.EXPLICIT_RETIRE`, and consolidate_memory's
    `CONTESTED_BUDGET`/`CONTESTED_CAP`/`CONTESTED_SECONDS`) - a suite asserting "the default
    is X" was actually asserting "whatever this PROCESS happened to import with", true only as
    long as nobody's shell exports the matching NEVERTWICE_* var. Pinned here, every time, so
    the suites that assert these defaults test the code's default, not the machine's env."""
    d = Path(tempfile.mkdtemp(prefix=prefix))
    m._rebase_vault(d)
    m.collect_existing_titles.cache_clear()
    m.collect_existing_tags.cache_clear()
    m.EARLIER_MAX_CHARS = 100
    m.EXPLICIT_RETIRE = "write"
    try:
        import consolidate_memory as _cm                      # noqa: PLC0415
        _cm.CONTESTED_BUDGET = 100_000
        _cm.CONTESTED_CAP = 0
        _cm.CONTESTED_SECONDS = 900
    except ImportError:
        pass
    if offline:
        m.git_autocommit = lambda *a, **k: None
        m.embed_text = lambda *a, **k: None
        m.embedder_available = lambda *a, **k: False
        m.embed_cache_usable = lambda: False
    # The one moment sandbox_guard's baked-path check has something to look at. It runs at
    # isolate(), with a single project module loaded; by here the imports are done and the
    # vault has just moved again - which is exactly the 2026-08-18 shape, where a derived
    # constant kept the live path the rebase did not reach.
    sandbox_guard.verify_no_live_paths()
    return d
