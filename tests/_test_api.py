#!/usr/bin/env python3
"""Tests for nevertwice.api - the in-process library surface (recall/remember/
capture_session/format_note). Fully offline: memory_hook + memory_search are mocked,
so no vault, Ollama, or git is touched."""
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
import _env_guard  # noqa: F401  hermetic: scrub store env BEFORE package imports bake path constants (incidents 2026-08-13 / 2026-08-18)
import api
import memory_hook as m
import memory_search as ms

_IDENT = lambda x: x if isinstance(x, list) else list(x)


# ── format_note ─────────────────────────────────────────────────────────────────

def test_format_note_full():
    s = api.format_note({"ntype": "mistake", "title": "OOM",
                         "description": "ran out of memory", "prevention": "lower batch"})
    assert "MISTAKE - OOM" in s
    assert "ran out of memory" in s
    assert "Prevention: lower batch" in s


def test_format_note_title_only():
    assert api.format_note({"title": "Just a title"}) == "Just a title"


def test_format_note_omits_empty_fields():
    s = api.format_note({"ntype": "pattern", "title": "T", "description": "", "prevention": ""})
    assert s == "PATTERN - T"


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
    fake.assert_called_once_with("hello", "proj", 3, rerank=True, xrerank=None)


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
                transcript_text=None, project_override=None, timestamp=None):
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


def test_remember_is_recallable_with_no_embedder():
    # The pip first-touch loop on a no-model box: remember -> recall must hit via the
    # text-only lexical record. api.remember used to gate update_embeddings on
    # embedder_available, which left the note invisible to search until something
    # embedded it (launch-audit HIGH).
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with mock.patch.object(m, "VAULT", tmp), \
             mock.patch.object(m, "embedder_available", lambda *a, **k: False), \
             mock.patch.object(m, "embed_text", lambda *a, **k: None), \
             mock.patch.object(m, "git_autocommit"):
            stem = api.remember("CUDA OOM at batch=64 on the GPU", project="demo",
                                type="mistake",
                                prevention="lower batch size or enable gradient checkpointing")
            assert stem, "remember returned no stem"
            hits = api.recall("training crashes out of gpu memory", project="demo", k=3)
        assert hits and any("CUDA OOM" in (h.get("title") or "") for h in hits), \
            f"note invisible to recall on a no-embedder box: {hits!r}"
        assert all((h.get("score") or 0) > 0 for h in hits), f"zero-score hit: {hits!r}"


def test_recall_says_which_rerankers_can_run_and_lets_the_caller_say_no():
    """The docstring offered one opt-in reranker. A second one turns itself on.

    `search_core(xrerank=None)` resolves to `reranker_ce.enabled()`, which is ON by default once
    the 2 GB cross-encoder is cached - by design, so one deliberate run keeps paying off. But
    `recall()` documented only "`rerank=True` adds an opt-in cloud rerank", and had no parameter
    for the other one, so an API caller could neither learn that a cross-encoder was reordering
    their results nor stop it.
    """
    seen = {}
    real = api._search.search_core

    def spy(query, project=None, k=5, **kw):
        seen.update(kw)
        return [], "stub"

    api._search.search_core = spy
    try:
        api.recall("a query")
        assert "xrerank" in seen, f"recall does not pass xrerank at all: {seen}"
        assert seen["xrerank"] is None, f"default must leave the switch to resolve: {seen}"
        api.recall("a query", xrerank=False)
        assert seen["xrerank"] is False, f"an explicit refusal must reach search_core: {seen}"
    finally:
        api._search.search_core = real

    doc = api.recall.__doc__ or ""
    assert "cross-encoder" in doc, "the docstring does not mention the reranker that self-enables"
    assert "xrerank" in doc, "the docstring does not name the parameter that turns it off"


def test_as_of_reads_each_note_once():
    """`_note_description` opens the note. It was called once per candidate to rank, and then
    again for each row of the answer, so every note a caller actually sees was read twice -
    the same bytes, parsed twice, on a path whose whole point is that a retired note has no
    vector and has to be read (T1 review)."""
    held = [{"stem": f"2026-01-0{i}-p-mistake-n{i}", "ntype": "mistake", "project": "p",
             "title": f"note {i}", "path": f"/nowhere/n{i}.md",
             "valid_from": "2026-01-01", "valid_to": None} for i in range(1, 6)]
    reads = []

    def counting(path, limit=1200):
        reads.append(path)
        return "the cache bug was a connection pool exhausted by a missing close"

    with mock.patch.object(m, "as_of", return_value=held), \
         mock.patch.object(api, "_note_description", side_effect=counting):
        out = api.as_of("cache bug", "2026-01-03", project="p", k=3)

    assert len(out) == 3, f"expected the top 3, got {len(out)}"
    assert len(reads) == len(held), (
        f"{len(held)} notes held, {len(reads)} reads - the answer re-read what ranking "
        f"already had: {reads}")
    assert len(set(reads)) == len(reads), f"a note was read twice: {reads}"
    assert all(r["description"] for r in out), "the descriptions did not survive the dedup"


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
