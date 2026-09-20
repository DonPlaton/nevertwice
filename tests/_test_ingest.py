#!/usr/bin/env python3
"""Tests for ingest.py - the generic / cross-agent capture entrypoint, focused on the
--dir sweep idempotency contract (#36). Fully offline: process_session and the vault
ops are mocked, so no LLM, no vault, no git."""
import contextlib
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nevertwice"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import _env_guard  # noqa: F401  hermetic: scrub store env BEFORE package imports bake path constants (incidents 2026-08-13 / 2026-08-18)
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


# ── the watermark tier, branch by branch ──────────────────────────────────────────────
# `ingest_files` is the one loop shared by the `--dir` sweep and the watch daemon, and it is
# where a transcript is decided to be new, grown, rewritten or already mined. Two checks stood
# over it. Every branch is pinned here, because the next section changes HOW the file is read
# and a wrong answer in any of these is silent knowledge loss.


class _Sweep:
    """One `ingest_files` call with the vault and the extractor stubbed out."""

    def __init__(self, vault: Path):
        self.vault = vault
        self.db: dict = {}
        self.mined: list[tuple[str, str]] = []          # (sid, the text handed to the pipeline)

    def _ps(self, sid, cwd, path, trig, db, run_log=None, agent=None,
            transcript_text=None, project_override=None, timestamp=None):
        self.mined.append((sid, transcript_text or ""))
        db[sid] = {"processed_at": "x"}
        return True

    def wm(self, f: Path) -> dict:
        """The watermark record for `f`, read through the same vault the sweep wrote it to."""
        with mock.patch.object(m, "VAULT", self.vault):
            return ingest.load_watermarks().get(ingest._path_hash(f)) or {}

    def set_wm(self, f: Path, value) -> None:
        with mock.patch.object(m, "VAULT", self.vault):
            wmk = ingest.load_watermarks()
            wmk[ingest._path_hash(f)] = value
            ingest.save_watermarks(wmk)

    def run(self, files, *, settle_s=0, deadline=None):
        self.mined = []
        with mock.patch.object(m, "VAULT", self.vault),              mock.patch.object(m, "process_session", side_effect=self._ps):
            return ingest.ingest_files(files, "p", "bot", self.db,
                                       settle_s=settle_s, deadline=deadline)


def test_watermark_branches():
    import io
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        vault = tmp / "vault"
        vault.mkdir()
        sw = _Sweep(vault)

        def line(n):
            return '{"role": "user", "content": "turn ' + str(n) + ' about the batch size"}' + "\n"

        # 1. a new plain-text transcript is mined whole and gets a watermark
        f = tmp / "a.jsonl"
        f.write_text("".join(line(i) for i in range(5)), encoding="utf-8")
        new, skipped, stored, errors = sw.run([f])
        assert (new, errors) == (1, 0), (new, skipped, stored, errors)
        wm = sw.wm(f)
        assert wm["chars"] == len(f.read_text(encoding="utf-8")), wm

        # 2. unchanged: skipped, not re-mined
        new, skipped, _, _ = sw.run([f])
        assert (new, skipped) == (0, 1), (new, skipped)

        # 3. grown: ONLY the appended tail reaches the pipeline
        with f.open("a", encoding="utf-8") as fh:
            fh.write(line(99))
        new, _, _, _ = sw.run([f])
        assert new == 1, new
        _sid, text = sw.mined[-1]
        assert "turn 99" in text and "turn 0" not in text, text[:200]

        # 4. rewritten IN PLACE: the prefix proof fails, the watermark is dropped, full re-mine
        f.write_text("".join(line(i) for i in range(200, 206)), encoding="utf-8")
        new, _, _, _ = sw.run([f])
        assert new == 1, new
        _sid, text = sw.mined[-1]
        assert "turn 200" in text and "turn 205" in text, text[:200]

        # 5. a delta over the cap is skipped and the watermark is NOT advanced, so raising the
        #    cap recovers the content instead of losing it
        before = sw.wm(f)["chars"]
        cap = ingest.MAX_SWEEP_BYTES
        ingest.MAX_SWEEP_BYTES = 200
        try:
            with f.open("a", encoding="utf-8") as fh:
                fh.write("".join(line(i) for i in range(300, 320)))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                new, skipped, _, _ = sw.run([f])
            assert (new, skipped) == (0, 1), (new, skipped)
            assert "cap 200" in err.getvalue(), err.getvalue()
            assert sw.wm(f)["chars"] == before
        finally:
            ingest.MAX_SWEEP_BYTES = cap
        new, _, _, _ = sw.run([f])                       # cap restored -> the delta mines
        assert new == 1 and "turn 319" in sw.mined[-1][1]

        # 6. an oversized UNWATERMARKED file is refused by name, not silently
        big = tmp / "big.jsonl"
        big.write_text("".join(line(i) for i in range(400, 460)), encoding="utf-8")
        ingest.MAX_SWEEP_BYTES = 100
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                new, skipped, _, _ = sw.run([big])
            assert (new, skipped) == (0, 1), (new, skipped)
            assert "big.jsonl" in err.getvalue() and "cap 100" in err.getvalue()
        finally:
            ingest.MAX_SWEEP_BYTES = cap

        # 7. a LIVE .jsonl consumes complete lines only; the partial tail mines once settled
        live = tmp / "live.jsonl"
        live.write_text("".join(line(i) for i in range(500, 503)), encoding="utf-8")
        sw.run([live])                                   # establish the watermark
        with live.open("a", encoding="utf-8") as fh:
            fh.write(line(504) + '{"role": "user", "content": "half a li')
        new, _, _, _ = sw.run([live], settle_s=3600)     # mtime is now -> treated as live
        assert new == 1 and "turn 504" in sw.mined[-1][1]
        assert "half a li" not in sw.mined[-1][1], sw.mined[-1][1][-120:]
        assert sw.wm(live)["bytes"] == -1
        with live.open("a", encoding="utf-8") as fh:
            fh.write('ne"}' + "\n")
        new, _, _, _ = sw.run([live])                    # settled: the whole line mines
        assert new == 1 and "half a line" in sw.mined[-1][1], sw.mined[-1][1][-120:]

        # 8. a corrupt watermark record is not a crash: the file mines as if unwatermarked
        corrupt = tmp / "corrupt.jsonl"
        corrupt.write_text("".join(line(i) for i in range(600, 603)), encoding="utf-8")
        sw.set_wm(corrupt, "not a dict")
        new, _, _, errors = sw.run([corrupt])
        assert (new, errors) == (1, 0), (new, errors)

        # 9. an unchanged size AND mtime skips without opening the file at all
        reads: list[str] = []
        real_open = ingest.Path.read_text
        ingest.Path.read_text = lambda self, *a, **k: (reads.append(self.name),
                                                       real_open(self, *a, **k))[1]
        try:
            new, skipped, _, _ = sw.run([corrupt])
        finally:
            ingest.Path.read_text = real_open
        assert (new, skipped) == (0, 1), (new, skipped)
        assert "corrupt.jsonl" not in reads, reads


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
