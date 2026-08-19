"""One sandbox for every hand-rolled suite (review 2026-08 Dc10 - three fixtures
had drifted: two of them missed LOG_FILE / PROMPT_RECALL_STATE_DIR, so those
paths kept pointing wherever the previous test left them).

Uses m._rebase_vault - the structural fix from the 2026-08 live-cache incidents -
so EVERY vault-derived module constant moves to the temp dir in one call.
"""
import tempfile
from pathlib import Path


def make_sandbox(m, prefix: str = "nwtest_", offline: bool = False) -> Path:
    """Fresh temp vault for module `m` (memory_hook). `offline=True` additionally
    stubs git + the embedder so no section can reach the live embedder or repo -
    the D14 hermeticity class (cloud leak / Ollama connect-timeout per write)."""
    d = Path(tempfile.mkdtemp(prefix=prefix))
    m._rebase_vault(d)
    m.collect_existing_titles.cache_clear()
    m.collect_existing_tags.cache_clear()
    if offline:
        m.git_autocommit = lambda *a, **k: None
        m.embed_text = lambda *a, **k: None
        m.embedder_available = lambda *a, **k: False
        m.embed_cache_usable = lambda: False
    return d
