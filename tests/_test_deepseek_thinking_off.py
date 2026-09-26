#!/usr/bin/env python3
"""Our DeepSeek backend turns thinking off, so an extraction is not cut at the output cap.

DeepSeek's API documents thinking as the default (on, effort high; read 2026-09-26); the only off
switch is a request-body field, `"thinking": {"type": "disabled"}`. Our DeepSeek body sent none, so
every DeepSeek-backend user extracted with reasoning on: reasoning tokens count against B1's
4,096-token output cap and the JSON answer is truncated. The Ollama path already sends
`think: false` for the same reason. (PREREG-V3 TB3(b), the auditor's C3 ruling: a product fix, not a
stand-side setting.)

Only the DeepSeek call carries the field - the other OpenAI-compatible backends (Groq, Cerebras)
do not document it and must not be sent it.

No socket is opened: `urllib.request.urlopen` is replaced by a fake that records what was sent.

    python tests/_test_deepseek_thinking_off.py
"""
import io
import json
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
                                     "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode("utf-8"))
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def sent_body(fn) -> dict:
    sent = []

    def _urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return _Resp()

    with mock.patch("urllib.request.urlopen", _urlopen), mock.patch.object(m.time, "sleep", lambda s: None), \
            mock.patch.object(m, "provider_key", lambda p: "test-key"):
        fn()
    return sent[0] if sent else {}


print("\n- the DeepSeek request turns thinking off -")
ds = sent_body(lambda: m.call_deepseek("extract this"))
check("call_deepseek sends thinking: {type: disabled}", ds.get("thinking") == {"type": "disabled"}, str(ds.get("thinking")))
check("... alongside JSON mode and the B1 output cap",
      ds.get("response_format") == {"type": "json_object"} and ds.get("max_tokens") == m.EXTRACT_NUM_PREDICT, str(ds))
check("... and the shipped temperature is unchanged (0.2)", ds.get("temperature") == 0.2, str(ds.get("temperature")))

print("\n- the backends that do not document the field are not sent it -")
for name, fn in (("groq", lambda: m.call_groq("extract this")), ("cerebras", lambda: m.call_cerebras("extract this"))):
    body = sent_body(fn)
    check(f"call_{name} sends no thinking field", bool(body) and "thinking" not in body, str(sorted(body)))

print(f"\ndeepseek thinking off: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
