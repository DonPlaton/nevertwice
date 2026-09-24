#!/usr/bin/env python3
"""`research/_ollama_symmetry_probe.py` (R1, the auditor's review of 717f482): the
hermetic slice of the arm-symmetry probe, covering the ENGINE stack only.

The competitor clients (ollama-python, mem0, langchain_ollama, A-MEM/litellm) live in
separate polygon venvs (`D:/Coding/_nevertwice_polygon/{mem0_eval,amem_eval}/.venv`) and
are not installed in this repository's own environment - which is exactly what CI's core
matrix has too. The FULL probe (all five stacks, run under three interpreters) is
exploratory and its results live at `.loop/explore/ollama_symmetry.json`, not as a
registered claim; this suite only proves the probe's OWN mechanism (a real loopback
fake-Ollama server, request counting, the pacer delta comparison) is sound, using the one
stack every environment has: the engine's own `embed_text`.

Fully local: `http.server` bound to `127.0.0.1` on an OS-assigned port - no real Ollama,
no internet, deterministic.

    python tests/research/_test_ollama_symmetry_probe.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import _env_guard  # noqa: F401, E402 - hermetic store before any project import
sys.path.insert(0, str(ROOT / "research"))
import _ollama_pacer as pacer  # noqa: E402
import _ollama_symmetry_probe as probe  # noqa: E402

PASSED = 0
FAILED = 0

_ENV_KEYS = ("OLLAMA_URL", "OLLAMA_TAGS_URL", "OLLAMA_EMBED_URL", "OLLAMA_BASE_URL",
            "OLLAMA_HOST", "OLLAMA_API_BASE")


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def _server_delta(server, before: int) -> int:
    with server.lock:                                       # type: ignore[attr-defined]
        return sum(server.counts.values()) - before          # type: ignore[attr-defined]


def _total(server) -> int:
    with server.lock:                                        # type: ignore[attr-defined]
        return sum(server.counts.values())                    # type: ignore[attr-defined]


def test_engine_stack_is_symmetric_with_a_real_fake_ollama() -> None:
    print("\n- R1: the engine embed stack - every request the fake server received, "
          "the pacer also counted -")
    saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
    server, port = probe.start_fake_ollama()
    try:
        probe._point_env_at(port)
        pacer.install()

        snap = pacer.snapshot()
        before = _total(server)
        r = probe.drive_engine_embed(port, n=5)
        server_calls = _server_delta(server, before)
        out: dict = {}
        pacer.attach(out, since=snap)
        pacer_calls = out.get("ollama_transport", {}).get("calls", 0)

        check("the engine stack made 5 attempts with no errors",
              r == {"attempted": 5, "errors": 0}, str(r))
        check("the fake server received exactly 5 requests", server_calls == 5,
              str(server_calls))
        check("the pacer counted the SAME 5 calls (arm symmetry holds for the engine "
              "stack)", pacer_calls == server_calls,
              f"server={server_calls} pacer={pacer_calls}")

        # mutation: simulate a bypass - is_ollama_host always False, so the pacer never
        # even recognises these requests as Ollama's, though the server still receives
        # every one of them. This is the exact mismatch the whole probe exists to catch.
        saved_is_ollama_host = pacer.is_ollama_host
        pacer.is_ollama_host = lambda host, port: False
        try:
            snap2 = pacer.snapshot()
            before2 = _total(server)
            probe.drive_engine_embed(port, n=5)
            server_calls2 = _server_delta(server, before2)
            out2: dict = {}
            pacer.attach(out2, since=snap2)
            pacer_calls2 = out2.get("ollama_transport", {}).get("calls", 0)
            check("mutation 'bypass' (is_ollama_host -> always False): the server still "
                  "sees all 5 requests but the pacer counts ZERO of them - proving the "
                  "server-vs-pacer comparison actually detects a real bypass",
                  server_calls2 == 5 and pacer_calls2 == 0,
                  f"server={server_calls2} pacer={pacer_calls2}")
        finally:
            pacer.is_ollama_host = saved_is_ollama_host
    finally:
        pacer.uninstall()
        probe.stop_fake_ollama(server)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    test_engine_stack_is_symmetric_with_a_real_fake_ollama()
    print(f"\nollama_symmetry_probe: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
