#!/usr/bin/env python3
"""Tests for nevertwice.api — the in-process library surface (recall/remember/
capture_session/format_note). Fully offline: memory_hook + memory_search are mocked,
so no vault, Ollama, or git is touched."""
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nevertwice"))
import api
import memory_hook as m
import memory_search as ms

_IDENT = lambda x: x if isinstance(x, list) else list(x)


# ── format_note ─────────────────────────────────────────────────────────────────

def test_format_note_full():
    s = api.format_note({"ntype": "mistake", "title": "OOM",
                         "description": "ran out of memory", "prevention": "lower batch"})
    assert "MISTAKE — OOM" in s
    assert "ran out of memory" in s
    assert "Prevention: lower batch" in s


def test_format_note_title_only():
    assert api.format_note({"title": "Just a title"}) == "Just a title"


def test_format_note_omits_empty_fields():
    s = api.format_note({"ntype": "pattern", "title": "T", "description": "", "prevention": ""})
    assert s == "PATTERN — T"


# ── recall ──────────────────────────────────────────────────────────────────────

def test_recall_blank_query_short_circuits():
    with mock.patch.object(ms, "search_core") as sc:
        assert api.recall("") == []
        assert api.recall("   ") == []
    sc.assert_not_called()


def test_recall_passes_args_and_returns_results():
    fake = mock.Mock(return_value=([{"title": "X", "score": 0.9}], "semantic"))
    with mock.patch.object(ms, "search_core", fake):
        out = api.recall("hello", "proj", 3, rerank=True)
    assert out == [{"title": "X", "score": 0.9}]
    fake.assert_called_once_with("hello", "proj", 3, rerank=True)


# ── remember ────────────────────────────────────────────────────────────────────

def test_remember_rejects_bad_type():
    try:
        api.remember("t", project="p", type="bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_remember_requires_project_and_title():
    for kw in ({"title": "", "project": "p"}, {"title": "t", "project": ""}):
        try:
            api.remember(**kw)
            assert False, "expected ValueError"
        except ValueError:
            pass


def test_remember_happy_path_embeds():
    with mock.patch.object(m, "acquire_lock", return_value=True), \
         mock.patch.object(m, "release_lock"), \
         mock.patch.object(m, "slug_project", side_effect=_IDENT), \
         mock.patch.object(m, "_norm_tags", side_effect=_IDENT), \
         mock.patch.object(m, "write_typed_note", return_value="2026-06-18-p-pattern-t"), \
         mock.patch.object(m, "ollama_alive", return_value=True), \
         mock.patch.object(m, "update_embeddings") as ue, \
         mock.patch.object(m, "rebuild_index"), \
         mock.patch.object(m, "git_autocommit"):
        stem = api.remember("t", project="p", type="pattern", prevention="do x")
    assert stem == "2026-06-18-p-pattern-t"
    ue.assert_called_once()


def test_remember_no_embed_when_flag_false():
    with mock.patch.object(m, "acquire_lock", return_value=True), \
         mock.patch.object(m, "release_lock"), \
         mock.patch.object(m, "slug_project", side_effect=_IDENT), \
         mock.patch.object(m, "_norm_tags", side_effect=_IDENT), \
         mock.patch.object(m, "write_typed_note", return_value="stem"), \
         mock.patch.object(m, "ollama_alive", return_value=True), \
         mock.patch.object(m, "update_embeddings") as ue, \
         mock.patch.object(m, "rebuild_index"), \
         mock.patch.object(m, "git_autocommit"):
        api.remember("t", project="p", embed=False)
    ue.assert_not_called()


def test_remember_injection_returns_none():
    with mock.patch.object(m, "acquire_lock", return_value=True), \
         mock.patch.object(m, "release_lock"), \
         mock.patch.object(m, "slug_project", side_effect=_IDENT), \
         mock.patch.object(m, "_norm_tags", side_effect=_IDENT), \
         mock.patch.object(m, "write_typed_note", return_value=""):
        assert api.remember("t", project="p") is None


def test_remember_lock_busy_raises_runtime():
    with mock.patch.object(m, "acquire_lock", return_value=False), \
         mock.patch.object(m, "slug_project", side_effect=_IDENT), \
         mock.patch.object(m, "_norm_tags", side_effect=_IDENT):
        try:
            api.remember("t", project="p")
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


# ── remember_lessons (self-extraction batch, #34) ─────────────────────────────────

def _wtn(folder, item, proj, date, tags, typ):
    """Fake write_typed_note: returns '' (rejected) for a REJECT-prefixed title."""
    if item["title"].startswith("REJECT"):
        return ""
    return f"{date}-{proj}-{typ}-{item['title']}".replace(" ", "-").lower()


def test_remember_lessons_batch_single_commit():
    lessons = [{"type": "mistake", "title": "OOM", "prevention": "lower batch"},
               {"type": "pattern", "title": "tmp then replace"},
               {"type": "decision", "title": "use sqlite", "description": "scale"}]
    with mock.patch.object(m, "acquire_lock", return_value=True), \
         mock.patch.object(m, "release_lock"), \
         mock.patch.object(m, "slug_project", side_effect=_IDENT), \
         mock.patch.object(m, "_norm_tags", side_effect=_IDENT), \
         mock.patch.object(m, "write_typed_note", side_effect=_wtn), \
         mock.patch.object(m, "update_embeddings") as ue, \
         mock.patch.object(m, "rebuild_index") as ri, \
         mock.patch.object(m, "git_autocommit") as gc:
        stems = api.remember_lessons(lessons, project="p")
    assert len(stems) == 3
    ue.assert_called_once()           # ONE batch embed, not three
    ri.assert_called_once()           # ONE rebuild
    gc.assert_called_once()           # ONE commit for the batch


def test_remember_lessons_skips_malformed():
    lessons = [{"type": "bogus", "title": "bad type"},   # not a TYPED_TYPE
               {"type": "pattern"},                       # no title
               "not a dict",                              # wrong shape
               {"type": "pattern", "title": "good one"}]
    with mock.patch.object(m, "acquire_lock", return_value=True), \
         mock.patch.object(m, "release_lock"), \
         mock.patch.object(m, "slug_project", side_effect=_IDENT), \
         mock.patch.object(m, "_norm_tags", side_effect=_IDENT), \
         mock.patch.object(m, "write_typed_note", side_effect=_wtn), \
         mock.patch.object(m, "update_embeddings"), \
         mock.patch.object(m, "rebuild_index"), \
         mock.patch.object(m, "git_autocommit"):
        stems = api.remember_lessons(lessons, project="p")
    assert len(stems) == 1 and "good-one" in stems[0]


def test_remember_lessons_skips_rejected_injection():
    lessons = [{"type": "pattern", "title": "REJECT injection"},
               {"type": "pattern", "title": "keep me"}]
    with mock.patch.object(m, "acquire_lock", return_value=True), \
         mock.patch.object(m, "release_lock"), \
         mock.patch.object(m, "slug_project", side_effect=_IDENT), \
         mock.patch.object(m, "_norm_tags", side_effect=_IDENT), \
         mock.patch.object(m, "write_typed_note", side_effect=_wtn), \
         mock.patch.object(m, "update_embeddings"), \
         mock.patch.object(m, "rebuild_index"), \
         mock.patch.object(m, "git_autocommit"):
        stems = api.remember_lessons(lessons, project="p")
    assert len(stems) == 1 and "keep-me" in stems[0]


def test_remember_lessons_empty_is_noop():
    with mock.patch.object(m, "slug_project", side_effect=_IDENT), \
         mock.patch.object(m, "acquire_lock", return_value=True) as al:
        assert api.remember_lessons([], project="p") == []
    al.assert_not_called()            # no lock taken when there's nothing to write


def test_remember_lessons_lock_busy_raises():
    with mock.patch.object(m, "acquire_lock", return_value=False), \
         mock.patch.object(m, "slug_project", side_effect=_IDENT), \
         mock.patch.object(m, "_norm_tags", side_effect=_IDENT):
        try:
            api.remember_lessons([{"type": "pattern", "title": "x"}], project="p")
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


# ── capture_session ─────────────────────────────────────────────────────────────

def test_capture_session_empty_text_raises():
    try:
        api.capture_session("   ")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_capture_session_no_llm_raises():
    with mock.patch.object(m, "llm_available", return_value=False):
        try:
            api.capture_session("a real transcript")
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


def test_capture_session_happy_path_summary():
    def fake_ps(sid, cwd, _t, trig, db, run_log=None, agent=None,
                transcript_text=None, project_override=None):
        run_log.append({"project": project_override or "p", "patterns": 1,
                        "mistakes": 0, "decisions": 2})
        return True

    tmp = Path(tempfile.mkdtemp())
    with mock.patch.object(m, "llm_available", return_value=True), \
         mock.patch.object(m, "acquire_lock", return_value=True), \
         mock.patch.object(m, "release_lock"), \
         mock.patch.object(m, "load_processed", return_value={}), \
         mock.patch.object(m, "process_session", side_effect=fake_ps), \
         mock.patch.object(m, "rebuild_index"), \
         mock.patch.object(m, "archive_old_sessions"), \
         mock.patch.object(m, "archive_old_typed"), \
         mock.patch.object(m, "prune_processed_db"), \
         mock.patch.object(m, "git_autocommit"), \
         mock.patch.object(m, "VAULT", tmp):
        res = api.capture_session("some transcript", project="p", agent="bot")
    assert res["stored"] is True
    assert res["patterns"] == 1 and res["decisions"] == 2
    assert res["agent"] == "bot" and res["project"] == "p"
    assert res["session_id"].startswith("ingest-")


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
