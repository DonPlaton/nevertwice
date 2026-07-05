#!/usr/bin/env python3
"""Tests for nevertwice.capture - the generic, framework-agnostic capture surface
(MemorySession, capture_chat, message parsing). api.capture_session is mocked, so no
vault/LLM is touched; we assert what gets collected and when extraction fires."""
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nevertwice"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import capture
MemorySession, capture_chat = capture.MemorySession, capture.capture_chat
_last_user, _text_of = capture._last_user, capture._text_of


# ── message parsing helpers ─────────────────────────────────────────────────────

def test_text_of_str_and_dict():
    assert _text_of("hello") == "hello"
    assert _text_of({"content": "y"}) == "y"
    assert _text_of({"text": "z"}) == "z"
    assert _text_of(None) == ""


def test_text_of_openai_parts_list():
    parts = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    assert _text_of(parts).strip() == "a b"


def test_last_user_picks_latest():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "first"},
            {"role": "assistant", "content": "yo"}, {"role": "user", "content": "latest"}]
    assert _last_user(msgs) == "latest"


def test_last_user_handles_plain_string():
    assert _last_user("just a prompt") == "just a prompt"


# ── MemorySession ───────────────────────────────────────────────────────────────

def test_session_collects_nonempty_turns():
    s = MemorySession(project="p")
    s.log_user("hello").log_assistant("world").log("user", "   ")
    assert s.turns == [("user", "hello"), ("assistant", "world")]
    assert "user: hello" in s.transcript and "assistant: world" in s.transcript


def test_flush_calls_capture_session():
    with mock.patch.object(capture._api, "capture_session",
                           return_value={"stored": True, "patterns": 1}) as cs:
        s = MemorySession(project="p", agent="b", session_id="sid1")
        s.log_user("x")
        res = s.flush()
    cs.assert_called_once()
    _, kw = cs.call_args
    assert kw["project"] == "p" and kw["agent"] == "b" and kw["session_id"] == "sid1"
    assert res["stored"] is True


def test_flush_empty_is_noop():
    with mock.patch.object(capture._api, "capture_session") as cs:
        res = MemorySession().flush()
    cs.assert_not_called()
    assert res["stored"] is False


def test_flush_resets_turns_no_duplicate_extraction():
    # the CRIT bug: a second flush() must NOT re-extract the same turns
    with mock.patch.object(capture._api, "capture_session",
                           return_value={"stored": True}) as cs:
        s = MemorySession(project="p")
        s.log_user("x").log_assistant("y")
        s.flush()
        assert s.turns == []          # buffer reset after extraction
        s.flush()                     # nothing left → no second extraction
    cs.assert_called_once()


def test_flush_keeps_turns_on_nonraising_failure():
    # extraction that FAILS WITHOUT RAISING (malformed LLM JSON → stored=False) must keep
    # the buffer so a retry can succeed - it used to wipe the conversation (code-review 2026-07)
    with mock.patch.object(capture._api, "capture_session",
                           side_effect=[{"stored": False, "reason": "llm"},
                                        {"stored": True}]) as cs:
        s = MemorySession(project="p")
        s.log_user("x").log_assistant("y")
        r1 = s.flush()
        assert r1["stored"] is False and len(s.turns) == 2    # kept for retry
        r2 = s.flush()                                        # retry re-extracts the SAME turns
        assert r2["stored"] is True and s.turns == []
    assert cs.call_count == 2


def test_capture_chat_flush_delimits_conversations():
    with mock.patch.object(capture._api, "capture_session",
                           return_value={"stored": True}) as cs:
        @capture_chat(project="p")
        def chat(messages):
            return "r"
        chat([{"role": "user", "content": "a"}])
        chat.memory.flush()
        assert chat.memory.turns == []
        chat([{"role": "user", "content": "b"}])
        # the new conversation does NOT carry "a" forward
        assert [t for t in chat.memory.turns if t[0] == "user"] == [("user", "b")]
    cs.assert_called_once()


def test_contextmanager_extracts_on_clean_exit():
    with mock.patch.object(capture._api, "capture_session",
                           return_value={"stored": True}) as cs:
        with MemorySession(project="p") as s:
            s.log_user("x").log_assistant("y")
    cs.assert_called_once()


def test_contextmanager_skips_extraction_on_exception():
    with mock.patch.object(capture._api, "capture_session") as cs:
        try:
            with MemorySession(project="p") as s:
                s.log_user("x")
                raise ValueError("boom")
        except ValueError:
            pass
    cs.assert_not_called()


def test_contextmanager_noextract_flag():
    with mock.patch.object(capture._api, "capture_session") as cs:
        with MemorySession(project="p", extract=False) as s:
            s.log_user("x")
    cs.assert_not_called()


# ── capture_chat decorator ──────────────────────────────────────────────────────

def test_capture_chat_logs_user_and_reply():
    with mock.patch.object(capture._api, "capture_session",
                           return_value={"stored": True}) as cs:
        @capture_chat(project="p", agent="b")
        def chat(messages):
            return "the reply"

        out = chat([{"role": "user", "content": "q1"}])
        assert out == "the reply"
        assert chat.memory.turns == [("user", "q1"), ("assistant", "the reply")]
        chat.memory.flush()
    cs.assert_called_once()


def test_capture_chat_accumulates_across_calls():
    @capture_chat(project="p")
    def chat(messages):
        return "r-" + _last_user(messages)

    chat([{"role": "user", "content": "a"}])
    chat([{"role": "user", "content": "b"}])
    assert [t for t in chat.memory.turns if t[0] == "user"] == [("user", "a"), ("user", "b")]
    assert ("assistant", "r-b") in chat.memory.turns


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
