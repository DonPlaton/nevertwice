#!/usr/bin/env python3
"""Tests for ingest.py — the generic / cross-agent capture entrypoint, focused on the
--dir sweep idempotency contract (#36). Fully offline: process_session and the vault
ops are mocked, so no LLM, no vault, no git."""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ingest
import memory_hook as m


def test_sweep_session_id_stable_and_content_sensitive():
    p = Path("/tmp/a.md")
    a = ingest.sweep_session_id(p, "hello")
    assert a == ingest.sweep_session_id(p, "hello")                     # same file+content → stable
    assert a != ingest.sweep_session_id(p, "hello world")              # content change → new id
    assert a != ingest.sweep_session_id(Path("/tmp/b.md"), "hello")    # path change → new id
    assert a.startswith("ingest-file-")


def test_sweep_skips_processed_ingests_new():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "s1.md").write_text("user: hi\nassistant: lowered batch to fix OOM", encoding="utf-8")
        (tmp / "s2.md").write_text("user: persist?\nassistant: tmp then os.replace", encoding="utf-8")
        (tmp / "empty.md").write_text("   ", encoding="utf-8")          # blank → ignored
        sid1 = ingest.sweep_session_id(tmp / "s1.md",
                                       (tmp / "s1.md").read_text(encoding="utf-8"))
        seen = []

        def fake_ps(sid, cwd, path, trig, db, run_log=None, agent=None,
                    transcript_text=None, project_override=None):
            seen.append(sid)
            return True

        args = SimpleNamespace(dir=str(tmp), glob="*.md", recursive=False,
                               project="p", agent="bot")
        with mock.patch.object(m, "llm_available", return_value=True), \
             mock.patch.object(m, "acquire_lock", return_value=True), \
             mock.patch.object(m, "release_lock"), \
             mock.patch.object(m, "VAULT", tmp), \
             mock.patch.object(m, "load_processed", return_value={sid1: {"processed_at": "x"}}), \
             mock.patch.object(m, "process_session", side_effect=fake_ps), \
             mock.patch.object(m, "rebuild_index"), \
             mock.patch.object(m, "archive_old_sessions"), \
             mock.patch.object(m, "archive_old_typed"), \
             mock.patch.object(m, "prune_processed_db"), \
             mock.patch.object(m, "git_autocommit"):
            ingest._sweep(args, "p", "bot")
        # s1 already processed → skipped; empty.md → skipped; only s2 ingested
        assert len(seen) == 1
        assert seen[0].startswith("ingest-file-") and seen[0] != sid1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
