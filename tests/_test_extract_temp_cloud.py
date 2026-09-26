#!/usr/bin/env python3
"""NEVERTWICE_EXTRACT_TEMP reaches every extraction backend, and the shipped default stays 0.2.

The Ollama path read the override since the benchmarks needed a pinned extraction temperature; the
cloud paths hardcoded 0.2, so a stand that set the variable changed the local backend only and the
cloud one silently kept its own value. (PREREG-V3 TB3(c): the override is used only by the
pre-declared all-0 S6 sensitivity row; a headline row runs at the shipped 0.2.)

Checked on the body each backend sends - DeepSeek, Groq, Cerebras (OpenAI-compatible
`temperature`), Gemini (`generationConfig.temperature`) and Ollama (`options.temperature`) - with
the variable unset (0.2) and set to 0.

No socket is opened: `urllib.request.urlopen` is replaced by a fake that records what was sent.

    python tests/_test_extract_temp_cloud.py
"""
import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


DONE = '{"patterns": [], "mistakes": [], "decisions": [], "session_summary": "s"}'


class _Resp(io.BytesIO):
    def __init__(self):
        super().__init__(json.dumps({"choices": [{"message": {"content": DONE}, "finish_reason": "stop"}],
                                     "candidates": [{"content": {"parts": [{"text": DONE}]}, "finishReason": "STOP"}],
                                     "response": DONE, "done_reason": "stop",
                                     "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode("utf-8"))
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def sent_body(fn, temp: str | None) -> dict:
    sent = []

    def _urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return _Resp()

    env = {k: v for k, v in os.environ.items() if k != "NEVERTWICE_EXTRACT_TEMP"}
    if temp is not None:
        env["NEVERTWICE_EXTRACT_TEMP"] = temp
    with mock.patch.dict(os.environ, env, clear=True), mock.patch("urllib.request.urlopen", _urlopen), \
            mock.patch.object(m.time, "sleep", lambda s: None), mock.patch.object(m, "provider_key", lambda p: "test-key"):
        fn()
    return sent[0] if sent else {}


BACKENDS = {
    "deepseek": (lambda: m.call_deepseek("extract this"), lambda b: b.get("temperature")),
    "groq": (lambda: m.call_groq("extract this"), lambda b: b.get("temperature")),
    "cerebras": (lambda: m.call_cerebras("extract this"), lambda b: b.get("temperature")),
    "gemini": (lambda: m.call_gemini("extract this"), lambda b: (b.get("generationConfig") or {}).get("temperature")),
    "ollama": (lambda: m.call_ollama("extract this"), lambda b: (b.get("options") or {}).get("temperature")),
}

print("\n- unset, every backend sends the shipped 0.2 -")
for name, (call, read) in BACKENDS.items():
    got = read(sent_body(call, None))
    check(f"{name}: temperature 0.2", got == 0.2, repr(got))

print("\n- NEVERTWICE_EXTRACT_TEMP=0 reaches every backend's body -")
for name, (call, read) in BACKENDS.items():
    got = read(sent_body(call, "0"))
    check(f"{name}: temperature 0.0", got == 0.0, repr(got))

print(f"\nextract temperature on every backend: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
