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
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import _env_guard  # noqa: F401, E402 - hermetic store before any project import
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "nevertwice"))
import _ollama_pacer as pacer  # noqa: E402
import longmem_eval as le      # noqa: E402
import locomo_eval as lc       # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


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


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_longmem_embed_all_wires_install_and_attach_with_a_checkpoint,
               test_locomo_embed_all_wires_install_and_attach_with_a_checkpoint):
        fn()
    print(f"\nlongmem/locomo pacer wiring: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
