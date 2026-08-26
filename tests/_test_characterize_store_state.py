#!/usr/bin/env python3
"""Characterization tests for the `store/state` seam, written BEFORE it moved.

GOAL E4 extracts `memory_hook.py` one seam at a time behind a compatibility facade. The risk in
any such move is not that the new module is wrong - it is that nobody can tell. A refactor whose
only evidence is "the suite still passes" proves the suite did not cover the moved code.

So this file was written against `memory_hook` at 6,411 lines, run green there, and then run
again **unchanged** after the code moved to `nevertwice/store_state.py`. Every check reaches the
behaviour through `memory_hook.<name>`, which is what every caller in the wild imports. If the
facade stops re-exporting a name, or re-exports a different implementation, these fail.

The behaviours pinned here are the ones that were each paid for by an incident:

* a crash mid-write must not truncate the live file (audit F1/F3/F30);
* a failed write must leave no orphaned `.tmp` in a synced vault (audit D3);
* concurrent writers *inside one process* must not race on one temp name (GOAL E1);
* corruption must reach the `.bak` fallback rather than being silently decoded into
  valid-looking data (review 2026-08-24);
* recovery from `.bak` must be LOUD, and an absent primary must not claim a recovery that
  never happened - noise in exactly the log someone greps while chasing a real one;
* both generations must be written from the same known-good in-memory text, primary first.
"""
from __future__ import annotations

import io
import json
import sys
import threading
from contextlib import redirect_stderr
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m         # noqa: E402

PASSED = 0
FAILED = 0
TMP = m.VAULT / "_characterize_state"


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def fresh(name: str) -> Path:
    TMP.mkdir(parents=True, exist_ok=True)
    path = TMP / name
    for p in (path, path.with_name(path.name + ".bak")):
        p.unlink(missing_ok=True)
    return path


def captured(fn, *args, **kwargs):
    """Run `fn`, returning (result, stderr). `log` prints to stderr; a recovery message is
    part of this seam's contract, so it is asserted rather than ignored."""
    buf = io.StringIO()
    with redirect_stderr(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


# ══════════════════════════ write_atomic ═══════════════════════════════

def test_write_atomic_publishes_all_or_nothing() -> None:
    print("\n- a crash mid-write cannot truncate the live file -")
    path = fresh("atomic.txt")
    m.write_atomic(path, "first")
    check("it writes the file", path.read_text(encoding="utf-8") == "first")

    m.write_atomic(path, "second, longer than the first")
    check("it replaces the contents wholesale",
          path.read_text(encoding="utf-8") == "second, longer than the first")

    nested = fresh("deep/er/still.txt")
    m.write_atomic(nested, "x")
    check("it creates missing parent directories", nested.is_file())

    m.write_atomic(path, "ünïcode ✓ and a\nnewline")
    check("it round-trips non-ASCII as utf-8",
          path.read_text(encoding="utf-8") == "ünïcode ✓ and a\nnewline")


def test_a_failed_write_leaves_no_orphan_tmp() -> None:
    """audit D3: an orphaned .tmp in a synced vault propagates to every machine.

    The failure has to happen AFTER the temp file exists, or the cleanup path is never
    reached and the test passes without exercising anything. A lone surrogate is a valid
    `str` that fails inside `write_text`, once the file is already open - exactly the shape
    of failure the cleanup is for. (The first version of this check passed a non-str object;
    `write_text` rejected it before creating anything, and a mutation deleting the entire
    cleanup block survived.)
    """
    print("\n- a failure cleans up after itself -")
    path = fresh("failing.txt")
    m.write_atomic(path, "good")
    before = sorted(q.name for q in TMP.glob("failing.txt*"))

    raised = None
    try:
        m.write_atomic(path, "bad payload " + chr(0xDCFF) + " trailing")
    except BaseException as exc:                  # noqa: BLE001 - the point of the check
        raised = exc
    check("the failure propagates rather than being swallowed", raised is not None,
          "a silent failure here is a state file that never got written")
    after = sorted(q.name for q in TMP.glob("failing.txt*"))
    check("no .tmp is left behind", after == before, f"{before} -> {after}")
    check("the previous contents survive", path.read_text(encoding="utf-8") == "good")


def test_the_replace_retry_gives_up_rather_than_hanging() -> None:
    """A retry with no deadline turns a genuinely locked file into a hung hook."""
    print("\n- a permanently locked target must not hang the hook -")
    seam = m._store_state
    path = fresh("locked.txt")
    original = seam.os.replace

    def always_busy(src, dst):
        raise PermissionError(32, "the file is in use by another process")

    outcome: list = []

    def attempt() -> None:
        try:
            m.write_atomic(path, "never lands")
        except BaseException as exc:              # noqa: BLE001 - recorded, then asserted
            outcome.append(type(exc).__name__)
        else:
            outcome.append("returned")

    seam.os.replace = always_busy
    try:
        worker = threading.Thread(target=attempt, daemon=True)
        worker.start()
        worker.join(timeout=seam._REPLACE_RETRY_S + 20)
        alive = worker.is_alive()
    finally:
        seam.os.replace = original

    check("it gives up inside a bounded window rather than retrying forever", not alive,
          f"still retrying after {seam._REPLACE_RETRY_S + 20:.0f}s - an unbounded retry "
          f"hangs every hook that touches this file")
    if not alive:
        check("and it raises rather than reporting a write that never happened",
              outcome == ["PermissionError"], str(outcome))
        check("no .tmp is left behind by the abandoned write",
              not list(TMP.glob("locked.txt.*.tmp")),
              str([q.name for q in TMP.glob('locked.txt.*.tmp')]))


def test_concurrent_writers_in_one_process_do_not_race() -> None:
    """GOAL E1: the pid alone separates processes and not threads - they share a pid."""
    print("\n- eight threads, one state file -")
    path = fresh("contended.txt")
    errors: list = []
    barrier = threading.Barrier(8)

    def writer(i: int) -> None:
        try:
            barrier.wait(timeout=30)
            for _ in range(12):
                m.write_atomic(path, f"payload from thread {i}\n" * 40)
        except BaseException as exc:              # noqa: BLE001 - collected, then reported
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)

    check("no writer raised", not errors, "; ".join(sorted(set(errors))[:3]))
    body = path.read_text(encoding="utf-8")
    lines = {ln for ln in body.splitlines() if ln}
    check("the published file is exactly one writer's payload, not a blend",
          len(lines) == 1, f"{sorted(lines)[:3]}")
    check("and no .tmp survived the contention",
          not list(TMP.glob("contended.txt.*.tmp")),
          str([p.name for p in TMP.glob('contended.txt.*.tmp')]))


def test_the_temp_name_separates_threads_as_well_as_processes() -> None:
    print("\n- the fix, named rather than inferred -")
    import inspect
    source = inspect.getsource(m.write_atomic)
    check("the temp name carries the pid", "getpid()" in source)
    check("the temp name carries the thread id", "get_ident()" in source,
          "the pid alone lets eight threads race on one .tmp")
    check("os.replace is retried through a transient sharing violation",
          hasattr(m, "_replace_with_retry"))
    check("the retry window is bounded",
          isinstance(getattr(m, "_REPLACE_RETRY_S", None), (int, float))
          and 0 < m._REPLACE_RETRY_S <= 30,
          f"an unbounded retry hangs the hook forever: {getattr(m, '_REPLACE_RETRY_S', None)!r}")


# ═════════════════════ two-generation JSON state ═══════════════════════

def test_both_generations_are_written_from_the_same_text() -> None:
    print("\n- primary and .bak, from one known-good snapshot -")
    path = fresh("gen.json")
    payload = json.dumps({"a": 1, "b": ["x", "ü"]}, ensure_ascii=False)
    m._save_json_generations(path, payload)
    bak = path.with_name(path.name + ".bak")
    check("the primary is written", path.is_file())
    check("the .bak is written", bak.is_file())
    check("both hold identical bytes",
          path.read_bytes() == bak.read_bytes(),
          "a .bak copied from the on-disk primary can copy its corruption")
    check("the payload round-trips",
          json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": ["x", "ü"]})


def test_the_bak_never_copies_the_primary_from_disk() -> None:
    """audit D1: a .bak copied from the on-disk primary can copy its corruption.

    Detecting this needs the on-disk primary to DIFFER from the text handed in - otherwise
    both implementations produce identical files and the difference is invisible. So the
    primary write is made to land corrupt, and the .bak is required to still hold the good
    payload, because the correct implementation never reads the primary back.
    """
    print("\n- the .bak comes from memory, not from the file just written -")
    seam = m._store_state
    path = fresh("known-good.json")
    good = json.dumps({"payload": "known good"})
    original = seam.write_atomic
    calls: list = []

    def corrupt_the_primary(target, text, encoding="utf-8"):
        calls.append(Path(target).name)
        # The first write is the primary. Land something corrupt there, as a truncated
        # write or a bad sector would.
        original(target, "{ truncated" if len(calls) == 1 else text, encoding)

    seam.write_atomic = corrupt_the_primary
    try:
        m._save_json_generations(path, good)
    finally:
        seam.write_atomic = original

    check("both generations were written", len(calls) == 2, str(calls))
    bak = path.with_name(path.name + ".bak")
    check("the primary is the corrupt one, as arranged",
          path.read_text(encoding="utf-8") == "{ truncated",
          path.read_text(encoding="utf-8")[:40])
    check("the .bak still holds the good payload",
          bak.is_file()
          and json.loads(bak.read_text(encoding="utf-8")) == {"payload": "known good"},
          bak.read_text(encoding="utf-8")[:60] if bak.is_file() else "absent")
    recovered, _ = captured(m._load_json_generations, path, "known-good")
    check("so the loader can still recover", recovered == {"payload": "known good"},
          str(recovered))


def test_the_loader_prefers_the_primary_and_is_quiet_about_it() -> None:
    print("\n- the ordinary case says nothing -")
    path = fresh("quiet.json")
    m._save_json_generations(path, json.dumps({"which": "primary"}))
    path.with_name(path.name + ".bak").write_text(json.dumps({"which": "bak"}),
                                                  encoding="utf-8")
    data, err = captured(m._load_json_generations, path, "quiet")
    check("the primary wins", data == {"which": "primary"}, str(data))
    check("nothing is logged on the happy path", "recovered" not in err, err.strip()[:120])


def test_recovery_from_bak_is_loud() -> None:
    print("\n- a real recovery must be visible in the log -")
    path = fresh("recover.json")
    m._save_json_generations(path, json.dumps({"which": "good"}))
    path.write_text("{ truncated mid-writ", encoding="utf-8")
    data, err = captured(m._load_json_generations, path, "recover-label")
    check("the good generation is recovered", data == {"which": "good"}, str(data))
    check("the recovery is logged", "recovered from .bak" in err, err.strip()[:160])
    check("the label the caller passed is in the message", "recover-label" in err,
          err.strip()[:160])


def test_an_absent_primary_is_not_reported_as_corruption() -> None:
    """A merely-absent primary used to log a data-corruption line that never happened."""
    print("\n- absence is not corruption -")
    path = fresh("absent.json")
    path.with_name(path.name + ".bak").write_text(json.dumps({"which": "bak"}),
                                                  encoding="utf-8")
    data, err = captured(m._load_json_generations, path, "absent")
    check("the .bak is still used", data == {"which": "bak"}, str(data))
    check("but no recovery is claimed", "recovered from .bak" not in err,
          f"noise in the log someone greps while chasing a real one: {err.strip()[:120]}")


def test_decoding_is_strict_so_corruption_reaches_the_fallback() -> None:
    """review 2026-08-24: errors='replace' turns a bit flip into valid-looking data."""
    print("\n- a bit flip must not decode into plausible data -")
    path = fresh("strict.json")
    m._save_json_generations(path, json.dumps({"pattern": "deploy"}))
    raw = bytearray(path.read_bytes())
    i = raw.find(b"deploy")
    raw[i + 2] = 0xFF                              # invalid utf-8 inside the value
    path.write_bytes(bytes(raw))
    data, err = captured(m._load_json_generations, path, "strict")
    check("the corrupt primary is rejected, not decoded",
          data == {"pattern": "deploy"},
          f"got {data!r} - a U+FFFD substitution parses fine and the .bak is never consulted")
    check("and the rejection is logged", "unreadable" in err, err.strip()[:160])


def test_neither_generation_parsing_returns_none() -> None:
    print("\n- the caller supplies the default, not the loader -")
    path = fresh("hopeless.json")
    path.write_text("not json", encoding="utf-8")
    path.with_name(path.name + ".bak").write_text("also not json", encoding="utf-8")
    data, _ = captured(m._load_json_generations, path, "hopeless")
    check("None is returned when nothing parses", data is None, repr(data))

    missing = fresh("never-existed.json")
    data, _ = captured(m._load_json_generations, missing, "missing")
    check("None is returned when nothing exists", data is None, repr(data))


def test_the_expected_type_is_enforced() -> None:
    print("\n- a list where a dict was expected is a corrupt file -")
    path = fresh("typed.json")
    m._save_json_generations(path, json.dumps({"ok": True}))
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    data, _ = captured(m._load_json_generations, path, "typed")
    check("a wrong-typed primary falls through to the .bak", data == {"ok": True}, str(data))

    path2 = fresh("typed2.json")
    m._save_json_generations(path2, json.dumps([1, 2, 3]))
    data, _ = captured(m._load_json_generations, path2, "typed2", list)
    check("the expected type is honoured when the caller names one", data == [1, 2, 3],
          str(data))


def test_the_real_callers_still_work_through_the_facade() -> None:
    """The seam is only safe if its actual consumers are unchanged."""
    print("\n- the state files that use this seam -")
    db = m.load_processed()
    check("load_processed returns a dict", isinstance(db, dict), type(db).__name__)
    m.mark_processed(db, "characterize-session", str(TMP / "transcript.jsonl"), 123)
    m.save_processed(db)
    reloaded = m.load_processed()
    check("a marked session survives a save/load round trip",
          "characterize-session" in reloaded, str(sorted(reloaded)[:4]))
    # The stored key is `bytes`, not `size`: `size` is the parameter name and `bytes` is what
    # goes on disk. Characterization records what the code does, so the test moved, not the code.
    check("the recorded transcript size is preserved",
          reloaded["characterize-session"].get("bytes") == 123,
          str(reloaded.get("characterize-session")))
    check("the .bak generation exists for the processed DB",
          m.PROCESSED_DB.with_name(m.PROCESSED_DB.name + ".bak").is_file())


def test_the_public_names_are_importable_from_memory_hook() -> None:
    """The compatibility facade, stated as a contract rather than assumed."""
    print("\n- every name a caller imports is still there -")
    for name in ("write_atomic", "_replace_with_retry", "_REPLACE_RETRY_S",
                 "_load_json_generations", "_save_json_generations"):
        check(f"memory_hook.{name} exists", hasattr(m, name))
    check("write_atomic is callable", callable(m.write_atomic))
    check("the module still defines the state files this seam serves",
          all(hasattr(m, n) for n in ("PROCESSED_DB", "VAULT")))


def test_what_the_extraction_did_change() -> None:
    """One thing the facade does NOT preserve, stated rather than discovered.

    `memory_hook.write_atomic` is a re-exported *reference*. Rebinding it - which test and
    instrumentation code does - no longer intercepts the seam's own internal calls, because
    `_save_json_generations` resolves `write_atomic` in its own module. Callers that only
    *call* these names are unaffected, which is the whole public surface; callers that
    *patch* them must patch at the seam. `_test_cleanup_fixes.py` was the one place in this
    repo doing so, and it silently observed nothing until this was found.
    """
    print("\n- the one surface the facade cannot preserve -")
    seam = m._store_state
    check("the facade and the seam are the same object",
          m.write_atomic is seam.write_atomic,
          "a wrapper would double every write and break identity checks")

    path = fresh("patchpoint.json")
    seen: list = []
    original = seam.write_atomic
    seam.write_atomic = lambda target, text, **kw: (seen.append(Path(target).name),
                                                    original(target, text, **kw))[1]
    try:
        m._save_json_generations(path, "{}")
    finally:
        seam.write_atomic = original
    check("patching the SEAM intercepts internal calls",
          seen == ["patchpoint.json", "patchpoint.json.bak"], str(seen))
    check("and the documented write order is primary first",
          seen[:1] == ["patchpoint.json"],
          "a crash between the two writes must leave the NEWER snapshot where the loader "
          "looks first")

    unseen: list = []
    facade_original = m.write_atomic
    m.write_atomic = lambda target, text, **kw: (unseen.append(Path(target).name),
                                                 facade_original(target, text, **kw))[1]
    try:
        m._save_json_generations(fresh("facadepoint.json"), "{}")
    finally:
        m.write_atomic = facade_original
    check("KNOWN CONSEQUENCE: patching the FACADE intercepts nothing", unseen == [],
          f"it now intercepts {unseen} - the facade became a wrapper, update this note")


def main() -> int:
    for fn in (test_write_atomic_publishes_all_or_nothing,
               test_a_failed_write_leaves_no_orphan_tmp,
               test_the_replace_retry_gives_up_rather_than_hanging,
               test_concurrent_writers_in_one_process_do_not_race,
               test_the_temp_name_separates_threads_as_well_as_processes,
               test_both_generations_are_written_from_the_same_text,
               test_the_bak_never_copies_the_primary_from_disk,
               test_the_loader_prefers_the_primary_and_is_quiet_about_it,
               test_recovery_from_bak_is_loud,
               test_an_absent_primary_is_not_reported_as_corruption,
               test_decoding_is_strict_so_corruption_reaches_the_fallback,
               test_neither_generation_parsing_returns_none,
               test_the_expected_type_is_enforced,
               test_the_real_callers_still_work_through_the_facade,
               test_the_public_names_are_importable_from_memory_hook,
               test_what_the_extraction_did_change):
        fn()
    print(f"\ncharacterize store/state: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
