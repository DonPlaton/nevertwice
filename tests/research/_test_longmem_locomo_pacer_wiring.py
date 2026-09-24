#!/usr/bin/env python3
"""K13 (the auditor's finding on 980eda1): `research/longmem_eval.py`'s and
`research/locomo_eval.py`'s own `embed_all()` call `_ollama_pacer.install()` and
`pacer.attach(cache, since=snap)` (a checkpoint attach mid-loop, plus a final one) - but
nothing exercised either function end to end, so a regression removing either call (ML1:
the FINAL `attach()`, ML2: longmem's `install()`, ML3: locomo's `install()`) would have
stayed green.

Every network call here is a FAKE `urllib.request.urlopen` (no real socket, no Ollama);
`embed_all()` runs for real, with a synthetic pool/question set sized to cross each
module's own checkpoint boundary (50 sessions for longmem, 500 turns for locomo) once,
so the intermediate checkpoint write is observed directly, not just the final one.

    python tests/research/_test_longmem_locomo_pacer_wiring.py
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import _env_guard  # noqa: F401, E402 - hermetic store before any project import
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "nevertwice"))
sys.path.insert(0, str(ROOT / "tools"))
import _ollama_pacer as pacer  # noqa: E402
import longmem_eval as le      # noqa: E402
import locomo_eval as lc       # noqa: E402
import remeasure as rm         # noqa: E402 - K16(2): row_refusal on the RESULT artifact

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


@contextlib.contextmanager
def _crash_guard(*names: str):
    """A mutation block that raises - a plausible regression, not just the deliberate
    ones below - must redden every check it would otherwise have made, by name, and let
    the REST of the suite keep running (the same guard `_test_ollama_pacer.py` and
    `_test_supersession_pool.py` use, MK18a/(в))."""
    try:
        yield
    except Exception as exc:                                          # noqa: BLE001
        for n in names:
            check(n, False, repr(exc))


class _JsonResp:
    """A urllib-style response: `.read()` + context-manager protocol - the shape
    `longmem_eval.embed_full` expects back from `urlopen`."""

    def __init__(self, payload: dict) -> None:
        self._p = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(*a, **kw):
    return _JsonResp({"embedding": [0.1, 0.2, 0.3]})


class _RecordingEMB:
    """Stands in for the module's own `EMB` (a `pathlib.Path`) - in memory only, so a
    real 500+-write embedding cache never touches disk. Records every `write_text()`
    call's PARSED json, in order, so a checkpoint write and the final write are both
    directly inspectable."""

    def __init__(self) -> None:
        self.writes: list[dict] = []

    def exists(self) -> bool:
        return False

    def write_text(self, text: str, encoding: str = "utf-8", newline=None) -> None:
        self.writes.append(json.loads(text))

    @property
    def name(self) -> str:
        return "fake_embeds.json"


@contextlib.contextmanager
def _isolated():
    assert not pacer.installed(), "a previous test left the pacer installed"
    saved_urlopen = urllib.request.urlopen
    saved_pace_s = pacer.PACE_S
    # 555/558 fake calls at the real 0.125 s floor gap would cost this suite over a
    # minute for no reason - pacing itself is `_test_ollama_pacer.py`'s job, not this
    # one's; zeroed here so `calls`/checkpoint counting is exercised at full speed.
    pacer.PACE_S = 0.0
    pacer._reset_for_tests()
    try:
        yield
    finally:
        if pacer.installed():
            pacer.uninstall()
        urllib.request.urlopen = saved_urlopen
        pacer.PACE_S = saved_pace_s
        pacer._reset_for_tests()


def test_longmem_embed_all_wires_install_and_attach_with_a_checkpoint() -> None:
    print("\n- longmem_eval.embed_all(): install()+attach() wired for real, a checkpoint "
          "write mid-loop AND the final write, meta untouched -")
    with _isolated():
        urllib.request.urlopen = _fake_urlopen
        n_sessions, n_questions = 55, 3          # crosses the 50-session checkpoint once
        pool = {f"s{i}": f"session text number {i}" for i in range(n_sessions)}
        data = [{"question_id": f"q{i}", "question": f"question {i}"} for i in range(n_questions)]
        emb = _RecordingEMB()
        saved_load, saved_emb = le.load, le.EMB
        le.load = lambda: (data, pool)
        le.EMB = emb
        try:
            le.embed_all()
        finally:
            le.load, le.EMB = saved_load, saved_emb

        check("at least two writes happened: one checkpoint + one final",
              len(emb.writes) >= 2, str(len(emb.writes)))
        if len(emb.writes) >= 2:
            checkpoint, final = emb.writes[0], emb.writes[-1]
            check("ML2 (install() removed) would show here: the checkpoint (50th session) "
                  "carries a PARTIAL ollama_transport delta, calls==50",
                  checkpoint.get("ollama_transport", {}).get("calls") == 50,
                  str(checkpoint.get("ollama_transport")))
            check("ML1 (final attach() removed) would show here: the FINAL write carries "
                  "the FULL delta - 55 sessions + 3 questions = 58 calls, not the stale "
                  "checkpoint value of 50",
                  final.get("ollama_transport", {}).get("calls") == n_sessions + n_questions,
                  str(final.get("ollama_transport")))
            check("meta is untouched by the pacer wiring - still exactly cache_meta()",
                  final.get("meta") == le.cache_meta(), str(final.get("meta")))
            check("every session and question actually got embedded",
                  len(final.get("sessions", {})) == n_sessions and
                  len(final.get("questions", {})) == n_questions,
                  f"sessions={len(final.get('sessions', {}))} "
                  f"questions={len(final.get('questions', {}))}")


def test_locomo_embed_all_wires_install_and_attach_with_a_checkpoint() -> None:
    print("\n- locomo_eval.embed_all(): install()+attach() wired for real, a checkpoint "
          "write mid-loop AND the final write -")
    with _isolated():
        urllib.request.urlopen = _fake_urlopen
        n_turns, n_questions = 500, 2            # crosses the 500-turn checkpoint once
        convs = [{"pool": {f"t{i}": f"turn text {i}" for i in range(n_turns)},
                 "qa": [{"question": f"q{i}"} for i in range(n_questions)]}]
        emb = _RecordingEMB()
        saved_emb = lc.EMB
        lc.EMB = emb
        try:
            lc.embed_all(convs)
        finally:
            lc.EMB = saved_emb

        check("at least two writes happened: one checkpoint + one final",
              len(emb.writes) >= 2, str(len(emb.writes)))
        if len(emb.writes) >= 2:
            checkpoint, final = emb.writes[0], emb.writes[-1]
            check("ML3 (install() removed) would show here: the checkpoint (500th turn) "
                  "carries a PARTIAL ollama_transport delta, calls==500",
                  checkpoint.get("ollama_transport", {}).get("calls") == n_turns,
                  str(checkpoint.get("ollama_transport")))
            check("ML1-equivalent (final attach() removed) would show here: the FINAL "
                  "write carries the FULL delta - 500 turns + 2 questions = 502 calls, "
                  "not the stale checkpoint value of 500",
                  final.get("ollama_transport", {}).get("calls") == n_turns + n_questions,
                  str(final.get("ollama_transport")))
            check("every turn and question actually got embedded",
                  len(final.get("turns", {})) == n_turns and
                  len(final.get("questions", {})) == n_questions,
                  f"turns={len(final.get('turns', {}))} "
                  f"questions={len(final.get('questions', {}))}")


def _fake_urlopen_failing_on(needle: str):
    """A urlopen stand-in that raises a real 500 `HTTPError` for any embed request whose
    JSON body's `input` contains `needle`, and succeeds otherwise - simulates exactly ONE
    embed genuinely failing among several (B1's own scenario), through the REAL
    `urllib.request.urlopen` path `embed_full` calls - never routed through
    `_PortExhaustionResponse`, since the body carries none of that signature, so the
    pacer's own retry logic never intercepts it; it is a genuine failure the first time."""
    def _fn(req, *a, **kw):
        body = req.data.decode("utf-8") if req.data else ""
        if needle in body:
            raise urllib.error.HTTPError(req.full_url, 500, "Internal Server Error", {},
                                         io.BytesIO(b"model busy"))
        return _JsonResp({"embedding": [0.1, 0.2, 0.3]})
    return _fn


def test_longmem_embed_all_records_dropped_sessions_by_name() -> None:
    print("\n- B1 (.loop/HANDOFF-PORTS.md, item 6): longmem_eval.embed_all() records a "
          "session whose embed genuinely failed in cache['dropped_sessions'], BY ID -")
    with _isolated():
        n_sessions, n_questions = 5, 1
        pool = {f"s{i}": f"session text number {i}" for i in range(n_sessions)}
        data = [{"question_id": "q0", "question": "question 0"}]
        urllib.request.urlopen = _fake_urlopen_failing_on("session text number 2")
        emb = _RecordingEMB()
        saved_load, saved_emb = le.load, le.EMB
        le.load = lambda: (data, pool)
        le.EMB = emb
        try:
            with _crash_guard(
                    "B1: dropped_sessions names exactly the one session that failed "
                    "('s2'), no others",
                    "B1: the other 4 sessions embedded fine and are NOT in "
                    "dropped_sessions"):
                le.embed_all()
                final = emb.writes[-1]
                check("B1: dropped_sessions names exactly the one session that failed "
                      "('s2'), no others",
                      final.get("dropped_sessions") == ["s2"],
                      str(final.get("dropped_sessions")))
                check("B1: the other 4 sessions embedded fine and are NOT in "
                      "dropped_sessions",
                      len(final.get("sessions", {})) == 4 and
                      "s2" not in final.get("sessions", {}),
                      str(sorted(final.get("sessions", {}))))
        finally:
            le.load, le.EMB = saved_load, saved_emb

        # mutation: "one embed fails -> named red" - simulate the pre-B1 shape (a failed
        # session leaves NOTHING behind, the same silence :282-284 always had) and show
        # the SAME assertion the checks above make now reads false.
        pre_b1_shape = {k: v for k, v in final.items() if k != "dropped_sessions"}
        with _crash_guard("mutation 'dropped_sessions tracking removed': the failed "
                          "session ('s2') is now invisible everywhere - no key records "
                          "it at all (would FAIL the B1 'names exactly the one session' "
                          "check above)"):
            check("mutation 'dropped_sessions tracking removed': the failed session "
                  "('s2') is now invisible everywhere - no key records it at all (would "
                  "FAIL the B1 'names exactly the one session' check above)",
                  pre_b1_shape.get("dropped_sessions") != ["s2"], str(pre_b1_shape.get(
                      "dropped_sessions")))


def test_locomo_embed_all_records_dropped_turns_by_name() -> None:
    print("\n- B1 (.loop/HANDOFF-PORTS.md, item 6, 'same for locomo'): "
          "locomo_eval.embed_all() records a turn whose embed genuinely failed in "
          "cache['dropped_turns'], BY ID -")
    with _isolated():
        n_turns, n_questions = 5, 1
        convs = [{"pool": {f"t{i}": f"turn text number {i}" for i in range(n_turns)},
                 "qa": [{"question": f"q{i}"} for i in range(n_questions)]}]
        urllib.request.urlopen = _fake_urlopen_failing_on("turn text number 3")
        emb = _RecordingEMB()
        saved_emb = lc.EMB
        lc.EMB = emb
        try:
            with _crash_guard(
                    "B1: dropped_turns names exactly the one turn that failed ('t3'), "
                    "no others"):
                lc.embed_all(convs)
                final = emb.writes[-1]
                check("B1: dropped_turns names exactly the one turn that failed ('t3'), "
                      "no others",
                      final.get("dropped_turns") == ["t3"], str(final.get("dropped_turns")))
                check("B1: the other 4 turns embedded fine and are NOT in dropped_turns",
                      len(final.get("turns", {})) == 4 and "t3" not in final.get("turns", {}),
                      str(sorted(final.get("turns", {}))))
        finally:
            lc.EMB = saved_emb


def _longmem_k16_scenario(out_dir: Path, *, skip_copy: bool = False) -> dict:
    """Runs longmem_eval.embed_all() (one embed genuinely fails, via a real urlopen 500)
    then evaluate(--save) against the SAME on-disk cache, and returns the parsed result
    artifact. `skip_copy=True` monkeypatches `le.copy_cache_provenance` to a no-op - the
    'skip the copy' mutation the auditor's item 6/K16(2) note asks for."""
    n_sessions = 3
    pool = {f"s{i}": f"session text number {i}" for i in range(n_sessions)}
    data = [{"question_id": "q0", "question": "question 0", "answer_session_ids": ["s0"]}]
    urllib.request.urlopen = _fake_urlopen_failing_on("session text number 1")
    emb_path = out_dir / "cache.json"
    out_path = out_dir / "result.json"
    saved_load, saved_emb, saved_out = le.load, le.EMB, le.OUT
    saved_record, saved_copy = le.corpus_pin.record, le.copy_cache_provenance
    saved_argv = sys.argv[:]
    le.load = lambda: (data, pool)
    le.EMB = emb_path
    le.corpus_pin.record = lambda name: {"corpus": name, "sha256": "0" * 64, "bytes": 1,
                                         "questions": 1, "pool_sessions": n_sessions,
                                         "url": "x", "licence": "x", "citation": "x"}
    if skip_copy:
        le.copy_cache_provenance = lambda *a, **k: None
    try:
        le.embed_all()
        le.OUT = str(out_path)
        sys.argv = ["longmem_eval.py", "--save"]
        le.evaluate()
    finally:
        le.load, le.EMB, le.OUT = saved_load, saved_emb, saved_out
        le.corpus_pin.record, le.copy_cache_provenance = saved_record, saved_copy
        sys.argv = saved_argv
    return json.loads(out_path.read_text(encoding="utf-8"))


def test_longmem_evaluate_save_copies_cache_provenance_into_the_result() -> None:
    print("\n- K16(2) (.loop/HANDOFF-PORTS.md): longmem_eval.evaluate()'s --save copies "
          "ollama_transport/valid/invalid_reason/dropped_sessions from the CACHE (where "
          "the pacer and B1's own tracking write them) into the RESULT artifact - the "
          "file a real claim's POINTER actually resolves through, never the cache "
          "beside it - and tools/remeasure.row_refusal refuses it on a REAL pointer -")
    with _isolated():
        with tempfile.TemporaryDirectory() as tmp:
            with _crash_guard(
                    "K16(2): dropped_sessions in the result names the one session that "
                    "failed ('s1')",
                    "K16(2): ollama_transport (incl. failed_outcomes) copied into the "
                    "result",
                    "K16(2): the result is marked invalid, naming the embed failure",
                    "K16(2)/B1: the pool-size mismatch (2 of 3 pinned sessions) is also "
                    "named",
                    "K16(2): tools/remeasure.row_refusal refuses a REAL claim pointer "
                    "(methods.semantic.recall@1) on this invalid result"):
                result = _longmem_k16_scenario(Path(tmp))
                check("K16(2): dropped_sessions in the result names the one session "
                      "that failed ('s1')",
                      result.get("dropped_sessions") == ["s1"],
                      str(result.get("dropped_sessions")))
                check("K16(2): ollama_transport (incl. failed_outcomes) copied into the "
                      "result",
                      result.get("ollama_transport", {}).get("failed_outcomes", {})
                      .get("by_status") == {"500": 1}, str(result.get("ollama_transport")))
                check("K16(2): the result is marked invalid, naming the embed failure",
                      result.get("valid") is False and
                      "embed" in (result.get("invalid_reason") or ""),
                      str(result.get("invalid_reason")))
                check("K16(2)/B1: the pool-size mismatch (2 of 3 pinned sessions) is "
                      "also named",
                      "2 of the pinned corpus's 3 sessions" in (result.get("invalid_reason") or ""),
                      str(result.get("invalid_reason")))
                reason = rm.row_refusal(result, "methods.semantic.recall@1", 0)
                check("K16(2): tools/remeasure.row_refusal refuses a REAL claim pointer "
                      "(methods.semantic.recall@1) on this invalid result",
                      reason is not None and "invalid" in reason, str(reason))

        # mutation: "skip the copy -> named red"
        with tempfile.TemporaryDirectory() as tmp2:
            with _crash_guard(
                    "mutation 'K16(2) copy skipped': the result stays WRONGLY valid - "
                    "no ollama_transport, no dropped_sessions - even though the cache "
                    "itself recorded the failure (would FAIL the K16(2) checks above)"):
                mutated = _longmem_k16_scenario(Path(tmp2), skip_copy=True)
                check("mutation 'K16(2) copy skipped': the result stays WRONGLY valid - "
                      "no ollama_transport, no dropped_sessions - even though the cache "
                      "itself recorded the failure (would FAIL the K16(2) checks above)",
                      "valid" not in mutated and "dropped_sessions" not in mutated and
                      "ollama_transport" not in mutated, str(mutated))
                reason2 = rm.row_refusal(mutated, "methods.semantic.recall@1", 0)
                check("mutation 'K16(2) copy skipped': row_refusal no longer refuses "
                      "this row either (would FAIL the row_refusal check above)",
                      reason2 is None, str(reason2))


def _locomo_k16_scenario(out_dir: Path, *, skip_copy: bool = False) -> dict:
    """locomo_eval's counterpart of `_longmem_k16_scenario`: embed_all() (one turn's
    embed genuinely fails) then main(--save) against the SAME on-disk cache."""
    n_turns = 3
    convs = [{"sample_id": "c0", "pool": {f"t{i}": f"turn text number {i}"
                                          for i in range(n_turns)},
             "qa": [{"question": "q0", "evidence": ["t0"], "category": "x",
                    "sample_id": "c0"}]}]
    urllib.request.urlopen = _fake_urlopen_failing_on("turn text number 1")
    emb_path = out_dir / "locomo_cache.json"
    out_path = out_dir / "locomo_result.json"
    saved_load, saved_emb = lc.load, lc.EMB
    saved_record, saved_copy = le.corpus_pin.record, le.copy_cache_provenance
    saved_argv = sys.argv[:]
    lc.load = lambda: convs
    lc.EMB = emb_path
    le.corpus_pin.record = lambda name: {"corpus": name, "sha256": "0" * 64, "bytes": 1,
                                         "questions": 1, "pool_sessions": n_turns,
                                         "url": "x", "licence": "x", "citation": "x"}
    if skip_copy:
        le.copy_cache_provenance = lambda *a, **k: None
    try:
        lc.embed_all(convs)
        sys.argv = ["locomo_eval.py", "--save", f"--out={out_path}"]
        lc.main()
    finally:
        lc.load, lc.EMB = saved_load, saved_emb
        le.corpus_pin.record, le.copy_cache_provenance = saved_record, saved_copy
        sys.argv = saved_argv
    return json.loads(out_path.read_text(encoding="utf-8"))


def test_locomo_main_save_copies_cache_provenance_into_the_result() -> None:
    print("\n- K16(2) (.loop/HANDOFF-PORTS.md): locomo_eval.main()'s --save copies "
          "ollama_transport/valid/invalid_reason/dropped_turns from the CACHE into the "
          "RESULT artifact - `le.copy_cache_provenance`, the SAME function longmem_eval "
          "uses - and tools/remeasure.row_refusal refuses it on a REAL locomo pointer -")
    with _isolated():
        with tempfile.TemporaryDirectory(dir=str(ROOT)) as tmp:
            with _crash_guard(
                    "K16(2) locomo: dropped_turns in the result names the one turn "
                    "that failed ('t1')",
                    "K16(2) locomo: ollama_transport copied into the result",
                    "K16(2) locomo: the result is marked invalid, naming the embed "
                    "failure",
                    "K16(2) locomo: tools/remeasure.row_refusal refuses a REAL claim "
                    "pointer (methods.semantic.recall@1) on this invalid result"):
                result = _locomo_k16_scenario(Path(tmp))
                check("K16(2) locomo: dropped_turns in the result names the one turn "
                      "that failed ('t1')",
                      result.get("dropped_turns") == ["t1"], str(result.get("dropped_turns")))
                check("K16(2) locomo: ollama_transport copied into the result",
                      result.get("ollama_transport", {}).get("failed_outcomes", {})
                      .get("by_status") == {"500": 1}, str(result.get("ollama_transport")))
                check("K16(2) locomo: the result is marked invalid, naming the embed "
                      "failure",
                      result.get("valid") is False and
                      "embed" in (result.get("invalid_reason") or ""),
                      str(result.get("invalid_reason")))
                reason = rm.row_refusal(result, "methods.semantic.recall@1", 0)
                check("K16(2) locomo: tools/remeasure.row_refusal refuses a REAL claim "
                      "pointer (methods.semantic.recall@1) on this invalid result",
                      reason is not None and "invalid" in reason, str(reason))

        with tempfile.TemporaryDirectory(dir=str(ROOT)) as tmp2:
            with _crash_guard(
                    "mutation 'K16(2) locomo copy skipped': the result stays WRONGLY "
                    "valid (would FAIL the K16(2) locomo checks above)"):
                mutated = _locomo_k16_scenario(Path(tmp2), skip_copy=True)
                check("mutation 'K16(2) locomo copy skipped': the result stays WRONGLY "
                      "valid - no ollama_transport, no dropped_turns (would FAIL the "
                      "K16(2) locomo checks above)",
                      "valid" not in mutated and "dropped_turns" not in mutated and
                      "ollama_transport" not in mutated, str(mutated))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_longmem_embed_all_wires_install_and_attach_with_a_checkpoint,
               test_locomo_embed_all_wires_install_and_attach_with_a_checkpoint,
               test_longmem_embed_all_records_dropped_sessions_by_name,
               test_locomo_embed_all_records_dropped_turns_by_name,
               test_longmem_evaluate_save_copies_cache_provenance_into_the_result,
               test_locomo_main_save_copies_cache_provenance_into_the_result):
        fn()
    print(f"\nlongmem/locomo pacer wiring: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
