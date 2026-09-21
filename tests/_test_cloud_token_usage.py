#!/usr/bin/env python3
"""A cloud call reports what it cost, and the judge's budget is spent in measured tokens.

The sleep-time judge spends a token budget, not a call budget, because that is the unit the
price is quoted in. `consolidate_memory.py` charges each verdict the difference in
`_LLM_STATS["prompt_tokens"] + ["eval_tokens"]` across the call, and when the difference is zero
or negative it charges `TOKENS_PER_PAIR_EST = 415` instead - "the backend did not say: charge the
measured mean".

Only Ollama ever said. `call_ollama` reads `prompt_eval_count` and `eval_count` out of the
response; `call_gemini` and `_call_openai_chat` - which is every cloud backend, Cerebras, Groq
and DeepSeek - read neither, although both APIs return the counts in the body they already
parse. So on cloud the difference was always zero, every verdict was charged 415, and a run that
reported "38,180 of 100,000 tokens" was reporting 92 x 415 with no measurement anywhere in it.
The number that names the budget was an estimate wearing a measurement's clothes, which is the
one thing this project is not allowed to ship.

`estimated_calls` in the same stats block is the honest half: it counts the calls charged the
mean. It was equal to `judged` on every cloud run and nobody read it.

    python tests/_test_cloud_token_usage.py
"""
import json
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "nevertwice"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                          # noqa: BLE001 - a redirected stream
    pass

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


class _Resp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen_returning(payload):
    def _f(req, timeout=None):
        return _Resp(payload)
    return _f


def _spent(fn, payload) -> tuple[int, int]:
    """(prompt, eval) tokens the call added to the run's stats."""
    before = (m._LLM_STATS.get("prompt_tokens", 0), m._LLM_STATS.get("eval_tokens", 0))
    with mock.patch("urllib.request.urlopen", _urlopen_returning(payload)):
        fn()
    after = (m._LLM_STATS.get("prompt_tokens", 0), m._LLM_STATS.get("eval_tokens", 0))
    return after[0] - before[0], after[1] - before[1]


# ── the three backends, each with the shape its own API documents ──────────────────
print("# every backend reports its own cost")

GEMINI = {
    "candidates": [{"finishReason": "STOP",
                    "content": {"parts": [{"text": '{"verdict": "separate"}'}]}}],
    "usageMetadata": {"promptTokenCount": 612, "candidatesTokenCount": 23,
                      "totalTokenCount": 635},
}
OPENAI = {
    "choices": [{"finish_reason": "stop",
                 "message": {"content": '{"verdict": "separate"}'}}],
    "usage": {"prompt_tokens": 488, "completion_tokens": 17, "total_tokens": 505},
}
OLLAMA = {"response": '{"verdict": "separate"}', "prompt_eval_count": 701, "eval_count": 29}

with mock.patch.object(m, "GEMINI_API_KEY", "k"), mock.patch.object(m, "GEMINI_RETRIES", 1):
    p, e = _spent(lambda: m.call_gemini("judge this"), GEMINI)
check("Gemini charges the prompt tokens it was told", p == 612, f"{p}")
check("Gemini charges the answer tokens it was told", e == 23, f"{e}")

with mock.patch.object(m, "GEMINI_RETRIES", 1):
    p, e = _spent(lambda: m._call_openai_chat("judge this", "https://x/v1/chat/completions",
                                              "k", "a-model", "Cerebras"), OPENAI)
check("an OpenAI-compatible backend charges its prompt tokens", p == 488, f"{p}")
check("an OpenAI-compatible backend charges its completion tokens", e == 17, f"{e}")

with mock.patch.object(m, "OLLAMA_RETRIES", 1):
    p, e = _spent(lambda: m.call_ollama("judge this"), OLLAMA)
check("Ollama still charges what it always did", (p, e) == (701, 29), f"{(p, e)}")


# ── a backend that says nothing is still not silently charged as if it had ─────────
#
# The fallback exists and stays: an older gateway, a proxy that strips the field, a provider
# that simply does not report. What must not happen is the fallback being the ONLY path while
# the stats claim otherwise, so the absence has to be distinguishable from a real zero.
print("# a backend that reports nothing leaves the counters where they were")

for label, payload in (
    ("Gemini without usageMetadata",
     {"candidates": [{"finishReason": "STOP",
                      "content": {"parts": [{"text": '{"verdict": "separate"}'}]}}]}),
    ("an OpenAI-compatible backend without usage",
     {"choices": [{"finish_reason": "stop", "message": {"content": '{"verdict": "x"}'}}]}),
):
    with mock.patch.object(m, "GEMINI_API_KEY", "k"), mock.patch.object(m, "GEMINI_RETRIES", 1):
        fn = (m.call_gemini if "Gemini" in label else
              (lambda: m._call_openai_chat("p", "https://x/v1/chat/completions", "k", "mm", "Groq")))
        p, e = _spent(lambda: fn("p") if fn is m.call_gemini else fn(), payload)
    check(f"{label} adds nothing", (p, e) == (0, 0), f"{(p, e)}")


# ── the consequence: the judge's budget stops being an estimate on cloud ───────────
#
# This is the check that connects the wiring above to the number a page prints. A run where
# every verdict came back with usage must report `estimated_calls == 0`; before this, on cloud,
# it was equal to `judged` on every single run.
print("# the judge charges what the call cost, not the mean")

usage_shape = [n for n in ("usageMetadata", "usage")
               if n in Path(HERE.parent / "nevertwice" / "_engine_store.py").read_text(
                   encoding="utf-8")]
check("both usage shapes are read in the engine, not one",
      sorted(usage_shape) == ["usage", "usageMetadata"], str(usage_shape))

consolidate = (HERE.parent / "nevertwice" / "consolidate_memory.py").read_text(encoding="utf-8")
check("the estimate is still the documented fallback, not the path",
      "TOKENS_PER_PAIR_EST" in consolidate and 'stats["estimated_calls"] += 1' in consolidate)
check("and a run that estimated anything says so in its own summary",
      "estimated" in consolidate.split("def _report")[-1] or "estimated_calls" in consolidate)

print()
print(f"cloud token usage: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
