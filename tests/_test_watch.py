#!/usr/bin/env python3
"""Tests for the always-on multi-agent capture: the `watch` daemon's poll cycle
(idempotency - a finished transcript is mined exactly once) and the `auto_capture`
drop-in proxy for OpenAI-style clients. Fully offline: process_session / capture_session
and the vault ops are mocked, so no LLM, no vault, no git, no network."""
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nevertwice"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import watch
import capture
import memory_hook as m

watch.SETTLE_S = 0            # tests write transcripts "now"; the settle window is tested explicitly


def _vault_mocks(db, fake_ps):
    """The standard set of patches that stub out the vault/LLM for a poll cycle."""
    return [
        mock.patch.object(m, "llm_available", return_value=True),
        mock.patch.object(m, "acquire_lock", return_value=True),
        mock.patch.object(m, "release_lock"),
        mock.patch.object(m, "VAULT", Path(tempfile.gettempdir())),
        mock.patch.object(m, "load_processed", return_value=db),
        mock.patch.object(m, "process_session", side_effect=fake_ps),
        mock.patch.object(m, "rebuild_index"),
        mock.patch.object(m, "archive_old_sessions"),
        mock.patch.object(m, "archive_old_typed"),
        mock.patch.object(m, "prune_processed_db"),
        mock.patch.object(m, "git_autocommit"),
    ]


def test_watch_captures_new_transcript_exactly_once():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        f = tmp / "sess1.jsonl"
        f.write_text("user: hi\nassistant: fixed the OOM by lowering the batch size", encoding="utf-8")
        db, seen = {}, []

        def fake_ps(sid, cwd, path, trig, dbarg, run_log=None, agent=None,
                    transcript_text=None, project_override=None):
            seen.append(sid)
            dbarg[sid] = {"processed_at": "x"}      # mimic process_session marking it done
            return True

        targets = [watch.Target("mybot", tmp, ["*.jsonl"], False, "proj")]
        patches = _vault_mocks(db, fake_ps)
        for p in patches:
            p.start()
        try:
            n1 = watch.poll_cycle(targets)                       # first sweep → 1 new
            n2 = watch.poll_cycle(targets)                       # unchanged → 0 (idempotent)
            f.write_text("user: hi\nassistant: also set num_workers=4", encoding="utf-8")
            n3 = watch.poll_cycle(targets)                       # changed → mined once more
        finally:
            for p in patches:
                p.stop()
        assert n1 == 1, f"first sweep should capture 1, got {n1}"
        assert n2 == 0, f"unchanged sweep should capture 0, got {n2}"
        assert n3 == 1, f"changed file should capture 1, got {n3}"
        assert len(seen) == 2, seen
        assert seen[0] != seen[1]                                # content change → new id


def test_watch_waits_for_a_live_transcript_to_settle():
    """A file whose mtime is fresher than SETTLE_S is a session still being written - it must
    NOT be mined this cycle (each growth would mint a new session id → note spam), and must be
    mined once its mtime is old enough."""
    import os
    import time
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        f = tmp / "live.jsonl"
        f.write_text("user: hi\nassistant: still typing...", encoding="utf-8")
        db, seen = {}, []

        def fake_ps(sid, cwd, path, trig, dbarg, run_log=None, agent=None,
                    transcript_text=None, project_override=None):
            seen.append(sid)
            dbarg[sid] = {"processed_at": "x"}
            return True

        targets = [watch.Target("mybot", tmp, ["*.jsonl"], False, "proj")]
        patches = _vault_mocks(db, fake_ps)
        for p in patches:
            p.start()
        old = watch.SETTLE_S
        watch.SETTLE_S = 3600
        try:
            assert watch.poll_cycle(targets) == 0, "fresh file must be skipped (still settling)"
            past = time.time() - 7200
            os.utime(f, (past, past))                    # the session finished long ago
            assert watch.poll_cycle(targets) == 1, "settled file must be mined"
        finally:
            watch.SETTLE_S = old
            for p in patches:
                p.stop()
        assert len(seen) == 1


def test_watch_yields_when_no_backend_or_lock_busy():
    targets = [watch.Target("x", Path(tempfile.gettempdir()), ["*.none"], False, None)]
    with mock.patch.object(m, "llm_available", return_value=False):
        assert watch.poll_cycle(targets) == 0                   # no LLM → no-op
    with mock.patch.object(m, "llm_available", return_value=True), \
         mock.patch.object(m, "acquire_lock", return_value=False):
        assert watch.poll_cycle(targets) == 0                   # vault busy → yields to the hook


def test_known_targets_is_a_list_of_existing_dirs():
    ts = watch.known_targets()
    assert isinstance(ts, list)
    for t in ts:
        assert t.dir.is_dir()                                   # only existing dirs are returned
        assert t.agent and t.globs


# ── auto_capture drop-in proxy ────────────────────────────────────────────────

class _FakeMsg:
    def __init__(self, content): self.content = content


class _FakeChoice:
    def __init__(self, content): self.message = _FakeMsg(content)


class _FakeResp:
    def __init__(self, content): self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def create(self, **kw): return _FakeResp("use tmp + os.replace for atomic writes")


class _FakeChat:
    def __init__(self): self.completions = _FakeCompletions()


class _FakeClient:
    def __init__(self):
        self.chat = _FakeChat()
        self.api_key = "sk-passthrough"


def test_auto_capture_passes_through_and_records():
    captured = {}

    def fake_capture_session(transcript, project=None, agent=None, session_id=None):
        captured["t"] = transcript
        return {"stored": True, "project": project}

    with mock.patch.object(capture._api, "capture_session", side_effect=fake_capture_session):
        client = capture.auto_capture(_FakeClient(), project="p", agent="bot", auto_flush=False)
        assert client.api_key == "sk-passthrough"               # arbitrary attr passes through
        r = client.chat.completions.create(
            model="m", messages=[{"role": "user", "content": "how do I write a file atomically?"}])
        assert "os.replace" in r.choices[0].message.content     # real response returned untouched
        out = client.memory.flush()
        assert out["stored"] and out["project"] == "p"
        assert "how do I write a file atomically?" in captured["t"]   # user turn captured
        assert "os.replace" in captured["t"]                         # assistant reply captured


def test_capture_chat_buffers_and_flush_resets():
    @capture.capture_chat(project="p", agent="bot")
    def chat(messages):
        return "lowered batch size to avoid OOM"

    chat([{"role": "user", "content": "training crashes on the GPU"}])
    assert len(chat.memory.turns) == 2                            # user + assistant buffered
    with mock.patch.object(capture._api, "capture_session",
                           return_value={"stored": True}) as cs:
        chat.memory.flush()
    cs.assert_called_once()
    assert chat.memory.turns == []                               # flush resets the buffer


def test_auto_flush_quietly_swallows_errors():
    # opt-in auto_flush mines the buffer at exit, but must never raise (no LLM / lock busy)
    s = capture.MemorySession(project="p", auto_flush=True)
    s.log_user("x").log_assistant("y")
    with mock.patch.object(capture._api, "capture_session",
                           side_effect=RuntimeError("no backend")):
        s._flush_quietly()                                       # swallows the error
    assert s.turns == [("user", "x"), ("assistant", "y")]        # turns kept on failure (not lost)
    with mock.patch.object(capture._api, "capture_session",
                           return_value={"stored": True}) as cs:
        s._flush_quietly()
    cs.assert_called_once()


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
