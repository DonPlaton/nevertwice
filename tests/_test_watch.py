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
import _env_guard  # noqa: F401  hermetic: scrub store env BEFORE package imports bake path constants (incidents 2026-08-13 / 2026-08-18)
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


def test_watch_bounds_how_long_it_holds_the_vault_lock():
    """The daemon's own promise: it "never starves the live agent".

    The cycle took ONE lock and then mined up to MAX_PER_CYCLE transcripts inside it - 40 by
    default, at up to ~33 s each on the Ollama fallback, so the hold reached twenty minutes
    while the cap's own comment said it existed so the lock was never held "for minutes". A
    SessionEnd hook waits 180 s for that lock and then logs "Aborting", which costs the whole
    session's knowledge.

    The hold is bounded by wall clock now, checked before each transcript is started, so the
    worst case is the budget plus the one extraction already running. Nothing is lost: what the
    budget defers is mined by the next cycle.
    """
    import time
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for i in range(10):
            (tmp / f"s{i}.jsonl").write_text(
                f"user: hi\nassistant: lesson number {i} about the batch size",
                encoding="utf-8")
        db, seen = {}, []

        def fake_ps(sid, cwd, path, trig, dbarg, run_log=None, agent=None,
                    transcript_text=None, project_override=None):
            time.sleep(0.05)                     # stands in for one LLM extraction
            seen.append(sid)
            dbarg[sid] = {"processed_at": "x"}
            return True

        targets = [watch.Target("mybot", tmp, ["*.jsonl"], False, "proj")]
        patches = _vault_mocks(db, fake_ps)
        for p_ in patches:
            p_.start()
        saved = watch.MAX_LOCK_S
        watch.MAX_LOCK_S = 0.12
        try:
            t0 = time.monotonic()
            first = watch.poll_cycle(targets)
            held = time.monotonic() - t0
            rest = 0
            for _ in range(12):                  # the remainder, over later cycles
                rest += watch.poll_cycle(targets)
        finally:
            watch.MAX_LOCK_S = saved
            for p_ in patches:
                p_.stop()

        assert first < 10, f"the budget deferred nothing: {first} of 10 in one cycle"
        assert held < watch.MAX_LOCK_S + 0.05 * 3, f"held {held:.3f}s on a {0.12}s budget"
        assert first + rest == 10, f"the deferred transcripts were lost: {first} + {rest}"
        assert len(set(seen)) == 10, f"each transcript mined once: {len(set(seen))}"


def test_the_lock_hold_ceiling_is_derived_from_the_timeouts_themselves():
    """MAX_LOCK_S bounds the LOOP, not the hold: the check runs before a transcript is
    started, so the hold is the budget plus however long that one extraction takes - and
    nothing bounded that. A transcript that times out everywhere pays the cloud chain and
    then the Ollama chain in series, both with their retries and linear backoff. The number
    is derived from the constants rather than written into a comment, because a number in a
    comment is wrong the day either timeout is tuned."""
    saved = (m.OLLAMA_TIMEOUT, m.OLLAMA_RETRIES, m.OLLAMA_RETRY_BACKOFF)
    try:
        m.OLLAMA_TIMEOUT, m.OLLAMA_RETRIES, m.OLLAMA_RETRY_BACKOFF = 10, 0, 0.0
        with mock.patch.object(m, "cloud_key", return_value=""):
            assert m.extraction_ceiling_s() == 10.0, m.extraction_ceiling_s()
            m.OLLAMA_RETRIES = 2                      # three tries, two linear backoffs
            m.OLLAMA_RETRY_BACKOFF = 1.0
            assert m.extraction_ceiling_s() == 33.0, m.extraction_ceiling_s()
        with mock.patch.object(m, "cloud_key", return_value="k"), \
             mock.patch.object(m, "ACTIVE_CLOUD", "gemini"):
            both = m.extraction_ceiling_s()
        assert both > 33.0, both                      # the cloud chain is paid first, in series
    finally:
        m.OLLAMA_TIMEOUT, m.OLLAMA_RETRIES, m.OLLAMA_RETRY_BACKOFF = saved

    assert watch.lock_hold_ceiling_s() == watch.MAX_LOCK_S + m.extraction_ceiling_s()
    report = watch.lock_budget_report()
    assert str(int(watch.MAX_LOCK_S)) in report, report
    assert str(int(m.HOOK_LOCK_WAIT_S)) in report, report


def test_the_daemon_says_when_its_hold_can_outlast_the_hooks_wait():
    """A hook that waits out `acquire_lock` does not crash - it logs "Aborting" and continues
    read-only, and that session's writes are simply not made. So the arithmetic that decides
    whether that happens has to be visible, not asserted in a docstring."""
    with mock.patch.object(m, "extraction_ceiling_s", return_value=1.0):
        fits = watch.lock_budget_report()
    with mock.patch.object(m, "extraction_ceiling_s",
                           return_value=m.HOOK_LOCK_WAIT_S * 4):
        over = watch.lock_budget_report()
    assert "waits" in fits and "waits" in over
    assert "longer" in over.lower() or "exceed" in over.lower(), over
    assert "longer" not in fits.lower() and "exceed" not in fits.lower(), fits


def test_the_remedy_it_names_is_one_that_actually_subtracts():
    """The startup line offered NEVERTWICE_WATCH_MAX_LOCK_S first. On the shipped timeouts
    the extraction ceiling ALONE is twice the hook's wait, so no value of that variable -
    zero included - closes the gap, and an operator who lowers it has traded mining for
    nothing. A remedy printed where it cannot work is worse than no remedy: it ends the
    search. Only when the extraction fits inside the wait is there a budget to lower, and
    then the line says how far."""
    wait = m.HOOK_LOCK_WAIT_S
    with mock.patch.object(m, "extraction_ceiling_s", return_value=wait * 2):
        hopeless = watch.lock_budget_report()
    assert "MAX_LOCK_S" not in hopeless, hopeless
    assert "NEVERTWICE_TIMEOUT" in hopeless, hopeless
    # ...and it says WHY lowering the budget is not on the list, in the same numbers.
    assert f"{wait * 2:.0f}s" in hopeless and f"{wait:.0f}s" in hopeless, hopeless

    # The other side of the branch: a short extraction leaves a real budget, and the line
    # names the bound rather than the variable alone.
    room = wait / 4
    with mock.patch.object(m, "extraction_ceiling_s", return_value=room),             mock.patch.object(watch, "MAX_LOCK_S", wait):
        fixable = watch.lock_budget_report()
    assert "MAX_LOCK_S" in fixable, fixable
    assert f"{wait - room:.0f}s" in fixable, fixable


def test_the_housekeeping_after_the_loop_is_inside_the_budget_too():
    """The index rebuild, the two archive passes and the git commit ran AFTER the deadline
    was spent, under the same lock, with nothing bounding them - so the budget described a
    part of the hold and the rest was whatever it was. The mining deadline now leaves them
    room inside MAX_LOCK_S, and an overrun is printed with its parts rather than absorbed."""
    import io
    import time
    import contextlib
    import ingest as ig
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for i in range(3):
            (tmp / f"h{i}.jsonl").write_text(f"user: hi\nassistant: lesson {i}", encoding="utf-8")
        db, seen_deadline = {}, []

        def fake_ps(sid, cwd, path, trig, dbarg, run_log=None, agent=None,
                    transcript_text=None, project_override=None):
            dbarg[sid] = {"processed_at": "x"}
            return True

        real_ingest = ig.ingest_files

        def spy_ingest(*a, **kw):
            seen_deadline.append(kw.get("deadline"))
            return real_ingest(*a, **kw)

        targets = [watch.Target("mybot", tmp, ["*.jsonl"], False, "proj")]
        patches = _vault_mocks(db, fake_ps)
        patches[6] = mock.patch.object(m, "rebuild_index", side_effect=lambda *a, **k: time.sleep(0.2))
        for p_ in patches:
            p_.start()
        ig.ingest_files = spy_ingest
        saved = (watch.MAX_LOCK_S, watch.HOUSEKEEPING_RESERVE_S)
        watch.MAX_LOCK_S, watch.HOUSEKEEPING_RESERVE_S = 1.0, 0.5
        err = io.StringIO()
        try:
            t0 = time.monotonic()
            with contextlib.redirect_stderr(err):
                watch.poll_cycle(targets)
            held = time.monotonic() - t0
        finally:
            watch.MAX_LOCK_S, watch.HOUSEKEEPING_RESERVE_S = saved
            ig.ingest_files = real_ingest
            for p_ in patches:
                p_.stop()
        assert seen_deadline and seen_deadline[0] is not None, seen_deadline
        # the mining loop was given 0.5s of the 1.0s budget, so the 0.2s tail fits inside it
        assert held < 1.0, f"held {held:.3f}s on a 1.0s budget with a 0.2s tail"
        assert err.getvalue() == "", err.getvalue()   # inside the budget: nothing to report


def test_an_overrun_names_what_it_cost():
    import io
    import time
    import contextlib
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "slow.jsonl").write_text("user: hi\nassistant: one slow lesson", encoding="utf-8")
        db = {}

        def fake_ps(sid, cwd, path, trig, dbarg, run_log=None, agent=None,
                    transcript_text=None, project_override=None):
            time.sleep(0.4)                       # one extraction, longer than the whole budget
            dbarg[sid] = {"processed_at": "x"}
            return True

        targets = [watch.Target("mybot", tmp, ["*.jsonl"], False, "proj")]
        patches = _vault_mocks(db, fake_ps)
        for p_ in patches:
            p_.start()
        saved = watch.MAX_LOCK_S
        watch.MAX_LOCK_S = 0.1
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                watch.poll_cycle(targets)
        finally:
            watch.MAX_LOCK_S = saved
            for p_ in patches:
                p_.stop()
        out = err.getvalue()
        assert "held the vault lock" in out, out
        assert "0.1" in out, out                  # the budget it overran
        assert "extraction" in out, out           # and the term that is not bounded by it


def test_watch_discovers_transcripts_outside_the_vault_lock():
    """Finding the files is read-only over the AGENTS' log directories, not over the store, so
    it has no business inside the lock - and on a large log dir it is the slowest thing in the
    cycle that is not an extraction."""
    import ingest as ig
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "a.jsonl").write_text("user: hi\nassistant: a lesson", encoding="utf-8")
        db, order = {}, []

        def fake_ps(sid, cwd, path, trig, dbarg, run_log=None, agent=None,
                    transcript_text=None, project_override=None):
            dbarg[sid] = {"processed_at": "x"}
            return True

        real_collect = ig.collect_transcripts

        def spy_collect(*a, **kw):
            order.append("collect")
            return real_collect(*a, **kw)

        targets = [watch.Target("mybot", tmp, ["*.jsonl"], False, "proj")]
        patches = _vault_mocks(db, fake_ps)
        patches[1] = mock.patch.object(m, "acquire_lock",
                                       side_effect=lambda *a, **k: order.append("lock") or True)
        for p_ in patches:
            p_.start()
        ig.collect_transcripts = spy_collect
        try:
            watch.poll_cycle(targets)
        finally:
            ig.collect_transcripts = real_collect
            for p_ in patches:
                p_.stop()
        assert order[:2] == ["collect", "lock"], order


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
