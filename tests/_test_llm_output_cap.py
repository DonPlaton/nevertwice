#!/usr/bin/env python3
"""Every LLM call the engine makes carries an output cap, and a capped answer is counted, not lost.

B1 (campaign v2, b8 frontier, 2026-09-25): the extraction call sent no `num_predict`, so a model
that never closed its JSON - qwen2.5-7b-64k under `format: json` - generated until the 120 s
timeout, the call failed, and the session was "left for retry"; 644 of 940 sessions in, the stand
was stopped. The OpenAI-compatible and Gemini backends sent no `max_tokens` /
`maxOutputTokens` either. A cap alone is not the fix: a capped answer whose JSON is complete (the
model closed the object and then padded) must be used and counted, and one whose JSON is cut must
fail LOUDLY - a named, counted reason, never the anonymous `{}` of a parse error.

The cap is derived, not tuned: the extractor's input is at most MAX_TRANSCRIPT_CHARS characters, a
faithful extraction is shorter than its source, and at the densest tokenisation we see (about
three characters a token for code) the whole input fits under the cap. The largest extraction
answer in the committed artifacts is 1,200 tokens (code_sessions_v1, 150 sessions).

A session whose extraction keeps failing is retried a bounded number of times, then parked and
counted - `process_session` used to leave it "for retry" on every sweep, forever.

No socket is opened: `urllib.request.urlopen` is replaced by a fake that records what was sent.

    python tests/_test_llm_output_cap.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

# The stands arm the sandbox themselves at import (isolate() re-verifies), so they are imported
# BEFORE this suite moves the store with make_sandbox below.
sys.path.insert(0, str(HERE.parent / "research"))
import frontier_eval as fe  # noqa: E402
import k8_judge_eval as k8  # noqa: E402

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


def _call(fn, payload):
    """(result, the JSON body the call sent). One fake response, no retries on a fail."""
    sent = []

    def _urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return _Resp(payload)

    with mock.patch("urllib.request.urlopen", _urlopen), mock.patch.object(m.time, "sleep", lambda s: None):
        out = fn()
    return out, (sent[0] if sent else {})


def _ollama():
    return m.call_ollama("extract this")


def _openai():
    return m._call_openai_chat("extract this", "http://127.0.0.1:9/v1/chat/completions", "k", "model", "DeepSeek")


def _gemini():
    return m.call_gemini("extract this")


def _stat(key: str) -> int:
    return int(m._LLM_STATS.get(key, 0) or 0)


CAP = getattr(m, "EXTRACT_NUM_PREDICT", None)
DONE = '{"patterns": [], "mistakes": [], "decisions": [], "session_summary": "s"}'
CUT = '{"patterns": [{"title": "a lesson", "description": "what worked and wh'

print("\n- the cap exists and is derived from the extractor's own input bound -")
check("the engine names one output cap for its LLM calls (EXTRACT_NUM_PREDICT)",
      isinstance(CAP, int) and CAP > 0, repr(CAP))
check("the cap holds an extraction as long as the largest input (MAX_TRANSCRIPT_CHARS / 3 tokens)",
      isinstance(CAP, int) and CAP >= m.MAX_TRANSCRIPT_CHARS // 3,
      f"cap {CAP}, input bound {m.MAX_TRANSCRIPT_CHARS // 3}")
check("and is above the largest extraction answer in the committed artifacts (1,200 tokens)",
      isinstance(CAP, int) and CAP > 1200, repr(CAP))

print("\n- every backend sends it -")
_, body = _call(_ollama, {"response": DONE, "done_reason": "stop"})
check("Ollama: options.num_predict is the cap", CAP is not None and (body.get("options") or {}).get("num_predict") == CAP,
      str(body.get("options")))
_, body = _call(_openai, {"choices": [{"message": {"content": DONE}, "finish_reason": "stop"}]})
check("OpenAI-compatible (DeepSeek, Cerebras, Groq): max_tokens is the cap", CAP is not None and body.get("max_tokens") == CAP,
      str({k: v for k, v in body.items() if k != "messages"}))
_, body = _call(_gemini, {"candidates": [{"content": {"parts": [{"text": DONE}]}, "finishReason": "STOP"}]})
check("Gemini: generationConfig.maxOutputTokens is the cap",
      CAP is not None and (body.get("generationConfig") or {}).get("maxOutputTokens") == CAP,
      str(body.get("generationConfig")))

print("\n- a capped answer is counted: used when its JSON is whole, a named failure when it is cut -")
cases = [
    ("Ollama", _ollama, lambda txt: {"response": txt, "done_reason": "length"}),
    ("OpenAI-compatible", _openai,
     lambda txt: {"choices": [{"message": {"content": txt}, "finish_reason": "length"}]}),
    ("Gemini", _gemini,
     lambda txt: {"candidates": [{"content": {"parts": [{"text": txt}]}, "finishReason": "MAX_TOKENS"}]}),
]
for label, fn, shape in cases:
    capped0, cut0 = _stat("capped"), _stat("truncated")
    out, _ = _call(fn, shape(DONE + "\n" * 40))
    check(f"{label}: a whole JSON object that hit the cap is used", out.get("session_summary") == "s", str(out))
    check(f"{label}: ... and counted as capped", _stat("capped") == capped0 + 1, str(m._LLM_STATS))
    logged = []
    with mock.patch.object(m, "log", lambda msg: logged.append(str(msg))):
        out, _ = _call(fn, shape(CUT))
    check(f"{label}: a JSON object cut by the cap is a failure", out == {}, str(out))
    check(f"{label}: ... counted as truncated", _stat("truncated") == cut0 + 1, str(m._LLM_STATS))
    check(f"{label}: ... and the log names the cap, not a bare parse error",
          any("output cap" in s for s in logged), " | ".join(logged)[:200])

print("\n- a session whose extraction keeps failing is retried a bounded number of times -")
check("the engine names the bound (EXTRACT_MAX_ATTEMPTS >= 1)",
      isinstance(getattr(m, "EXTRACT_MAX_ATTEMPTS", None), int) and m.EXTRACT_MAX_ATTEMPTS >= 1,
      repr(getattr(m, "EXTRACT_MAX_ATTEMPTS", None)))

from _sandbox import make_sandbox  # noqa: E402

BODY = "Came back to the API client. That earlier decision is off: the HTTP client timeout is 5 seconds."
CALLS: list[int] = []


def _failing(reason: str):
    def fake(prompt, project=None):
        CALLS.append(1)
        m._LLM_LAST["failure"] = reason
        return {}
    return fake


def _run(sid: str, db: dict, times: int) -> list:
    return [m.process_session(sid, r"D:\Coding\x", "", "ingest", db, transcript_text=BODY,
                              project_override="capproj") for _ in range(times)]


MAX = getattr(m, "EXTRACT_MAX_ATTEMPTS", 3)
sandbox = make_sandbox(m, "b1cap_", offline=True)
m.update_embeddings = lambda notes: None
_real_gen = m.generate_json

CALLS.clear()
m.generate_json = _failing("truncated")
db: dict = {}
_run("runaway", db, MAX + 2)
check(f"a session whose answer is cut every time costs exactly {MAX} extraction calls, not one per sweep",
      len(CALLS) == MAX, f"{len(CALLS)} calls for {MAX + 2} sweeps")
check("... and is parked: marked processed with the reason, so no sweep picks it up again",
      "truncated" in str((db.get("runaway") or {}).get("parked", "")), str(db.get("runaway")))
check("... and the parking is counted", m._LLM_STATS.get("parked", 0) >= 1, str(m._LLM_STATS))
check("... and its attempt record is cleared once it is parked",
      "runaway" not in (m._load_json_generations(sandbox / ".extract_attempts.json", "t") or {}))

CALLS.clear()
m.generate_json = _failing("transport")
db = {}
_run("backend-down", db, MAX + 2)
check("a TRANSPORT failure is never counted toward parking: the session waits for the backend",
      "backend-down" not in db and len(CALLS) == MAX + 2, f"{len(CALLS)} calls, db {db}")

CALLS.clear()
m.generate_json = _failing("unparsable")
db = {}
_run("flaky", db, MAX - 1)
m.generate_json = lambda prompt, project=None: {
    "project_relevant": True, "patterns": [], "mistakes": [], "session_summary": "s", "context_update": "",
    "decisions": [{"title": "timeout", "description": "the HTTP client timeout is 5 seconds",
                   "facts": ["5 seconds"]}]}
_run("flaky", db, 1)
check("a success clears the session's failure count (it is processed, not parked)",
      "flaky" in db and "parked" not in db["flaky"]
      and "flaky" not in (m._load_json_generations(sandbox / ".extract_attempts.json", "t") or {}),
      str(db.get("flaky")))
m.generate_json = _real_gen

print("\n- end to end, in the hook's own shape: a cut answer parks the session and telemetry sees it -")
#: The session tests above hand process_session a fake generate_json that sets the failure slug
#: itself, so a defect between the backend and the slug - `_json_api_call` overwriting "truncated"
#: with "empty" (auditor's M10) - passed them. This run goes the real way: a fake urlopen answers a
#: cut JSON with done_reason=length, and call_ollama -> _parse_capped -> _json_api_call ->
#: generate_json -> process_session run unpatched. It runs in a child loaded the way hook_shim
#: loads the engine - runpy.run_path, __package__ '' - because that is where `from . import
#: telemetry` failed and the extraction-failure counter never moved (B7). run_path returns a COPY
#: of the namespace, so the child patches through process_session.__globals__.
import subprocess  # noqa: E402

E2E = r'''
import json, runpy, sys
from pathlib import Path
from unittest import mock
sys.path.insert(0, TESTS)
import _env_guard
sys.path.insert(0, PKG)
g = runpy.run_path(str(Path(PKG) / "memory_hook.py"), run_name="nevertwice_hook_shape")
ns = g["process_session"].__globals__
ns["update_embeddings"] = lambda notes: None
calls = []
class R:
    def __init__(self, b): self.b = b
    def read(self): return self.b
    def __enter__(self): return self
    def __exit__(self, *a): return False
def urlopen(req, timeout=None):
    calls.append(json.loads(req.data.decode("utf-8")).get("options", {}).get("num_predict"))
    return R(json.dumps({"response": CUT, "done_reason": "length", "eval_count": 4096}).encode())
db = {}
with mock.patch("urllib.request.urlopen", urlopen), mock.patch.object(ns["time"], "sleep", lambda s: None):
    for _ in range(ns["EXTRACT_MAX_ATTEMPTS"] + 1):
        ns["process_session"]("e2e", PKG, "", "ingest", db, transcript_text=BODY, project_override="capproj")
tel = Path(ns["VAULT"]) / "telemetry.json"
print(json.dumps({"package": g.get("__package__"), "calls": calls,
                  "parked": (db.get("e2e") or {}).get("parked"),
                  "telemetry": json.loads(tel.read_text(encoding="utf-8"))["extraction_failures"]
                  if tel.exists() else None}))
'''
_child = subprocess.run(
    [sys.executable, "-c", f"TESTS={str(HERE)!r}; PKG={str(HERE.parent / 'nevertwice')!r}; "
                           f"CUT={CUT!r}; BODY={BODY!r}\n" + E2E],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
try:
    e2e = json.loads(_child.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    e2e = {"error": (_child.stderr or _child.stdout)[-400:]}
check("the child really runs the engine in the hook's shape (no package)", e2e.get("package") == "", str(e2e))
check(f"a cut answer costs exactly {MAX} real calls, each carrying the cap",
      e2e.get("calls") == [CAP] * MAX, str(e2e.get("calls")))
check("... and the session is parked for the reason the backend gave: 'truncated'",
      e2e.get("parked") == f"truncated x{MAX}", str(e2e.get("parked")))
_tel = e2e.get("telemetry") or {}
check("... and telemetry counted it, in the hook's shape (B7: this counter never moved in a hook)",
      (_tel.get("by_reason") or {}).get("truncated") == MAX - 1 and (_tel.get("by_reason") or {}).get("parked") == 1,
      str(_tel))

print("\n- B7, the same class elsewhere: outcomes and search count too when loaded flat -")
#: guards.py reaches outcomes through _sibling("outcomes") on the hook's path, and api can be
#: imported flat (api.py:45); both reached telemetry with `from . import telemetry` alone, which
#: fails outside a package - swallowed, so intervention_outcomes and search_latency never moved
#: there. The child imports both FLAT, the shape the hook gives them.
FLAT = r'''
import json, sys
sys.path.insert(0, TESTS)
import _env_guard
sys.path.insert(0, PKG)
import outcomes, api, telemetry
assert not outcomes.__package__ and not api.__package__
outcomes.record({}, "accepted", session_id="s1")
api.recall("the http client timeout", k=3, xrerank=False)
d = telemetry.load()
print(json.dumps({"outcome": d["intervention_outcomes"].get("accepted", 0),
                  "search": d["search_latency"].get("count", 0)}))
'''
#: The search asks whether an embedder is up; the child's Ollama URLs point at a closed port, so
#: that question is answered "no" without a packet reaching the machine's real Ollama.
_closed = {"OLLAMA_TAGS_URL": "http://127.0.0.1:9/api/tags", "OLLAMA_EMBED_URL": "http://127.0.0.1:9/api/embed"}
_flat = subprocess.run(
    [sys.executable, "-c", f"TESTS={str(HERE)!r}; PKG={str(HERE.parent / 'nevertwice')!r}\n" + FLAT],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    env={**os.environ, **_closed})
try:
    flat = json.loads(_flat.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    flat = {"error": (_flat.stderr or _flat.stdout)[-400:]}
check("outcomes.record, loaded flat, moves intervention_outcomes", flat.get("outcome") == 1, str(flat))
check("api.recall, loaded flat, moves search_latency", flat.get("search") == 1, str(flat))

print("\n- the stands carry a capped answer into their artifacts, never pass it off as whole -")
with tempfile.TemporaryDirectory() as _fd:        # the stand's caches live in a temp dir here
    _fdir = Path(_fd)
    _real = (fe._cache_path, fe.ollama_chat, fe.corpus_pin.record)
    fe._cache_path = lambda name: _fdir / f"{name}.json"
    fe.corpus_pin.record = lambda name: {"corpus": name}
    _q = [{"question_id": "q0", "question": "?", "answer": "a", "answer_session_ids": []}]
    try:
        fe.ollama_chat = lambda model, prompt, timeout=600: {
            "content": '{"answer": "the first half of an ans', "prompt_tokens": 10, "eval_tokens": 4096,
            "capped": True}
        fe.answer_stage([], _q, {}, "r", 1000)
        _ans = json.loads((_fdir / "answers.json").read_text(encoding="utf-8"))
        _rows = [v for k, v in _ans.items() if not k.startswith("_")]
        check("frontier: an answer the cap cut is stored marked capped and cut, not as a whole answer",
              _rows and all(r.get("capped") and r.get("cut") for r in _rows), str(_rows)[:300])
        fe.ollama_chat = lambda model, prompt, timeout=600: {
            "content": '{"correct": true}', "prompt_tokens": 5, "eval_tokens": 5, "capped": True}
        check("frontier: the judge's verdict carries its own capped flag",
              fe.judge_one_capped("j", _q[0], "a") == (True, True))
        fe.judge_stage([], _q, "r", "j", "", 0)
        _res = fe.summarise([], _q, "r", "j", "")
        _cap = (_res.get("brackets", {}).get("none") or {}).get("capped")
        check("frontier: summarise counts capped answers, cut answers and capped verdicts per point",
              _cap == {"answers": 1, "answers_cut": 1, "verdicts": 1}, str(_res.get("brackets"))[:300])
    finally:
        fe._cache_path, fe.ollama_chat, fe.corpus_pin.record = _real

_krows = [{"pair_truth": "replaces", "expected": "replaces", "verdict": "replaces", "capped": True,
           "prompt_tokens": 1, "eval_tokens": 1, "seconds": 0.1},
          {"pair_truth": "separate", "expected": "separate", "verdict": None, "capped": False,
           "prompt_tokens": 1, "eval_tokens": 1, "seconds": 0.1}]
check("k8 judge: the summary counts the capped verdicts", k8.summarise(_krows).get("capped") == 1,
      str(k8.summarise(_krows).get("capped")))

print("\n- no generation payload in the repository is uncapped (the stands had the same gap) -")
import ast  # noqa: E402

ROOT = HERE.parent
#: A payload is a dict literal handed straight to json.dumps that names a "model" and a "prompt" or
#: "messages". An Ollama payload (it has "options", or a bare "prompt") must name num_predict in its
#: options; an OpenAI-compatible one ("messages", no "options") must name max_tokens. The pacer and
#: the symmetry probe are fake SERVERS: they answer payloads, they send none.
SERVERS = {"research/_ollama_pacer.py", "research/_ollama_symmetry_probe.py"}


def _key(k):
    return k.value if isinstance(k, ast.Constant) else None


def uncapped_payloads(paths, root: Path = ROOT) -> list:
    out = []
    for p in paths:
        rel = p.relative_to(root).as_posix()
        if rel in SERVERS:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "dumps" and call.args and isinstance(call.args[0], ast.Dict)):
                continue
            node = call.args[0]
            keys = {_key(k): v for k, v in zip(node.keys, node.values)}
            if "model" not in keys or not ({"prompt", "messages"} & set(keys)):
                continue
            opts = keys.get("options")
            if isinstance(opts, ast.Dict) or "prompt" in keys:
                capped = isinstance(opts, ast.Dict) and "num_predict" in {_key(k) for k in opts.keys}
            else:
                capped = bool({"max_tokens", "max_completion_tokens"} & set(keys))
            if not capped:
                out.append(f"{rel}:{node.lineno}")
    return out


PAYLOAD_FILES = sorted((ROOT / "research").glob("*.py")) + sorted((ROOT / "nevertwice").glob("*.py"))
found = uncapped_payloads(PAYLOAD_FILES)
check("every generation payload in nevertwice/ and research/ caps its output (num_predict / max_tokens)",
      not found, ", ".join(found))
with tempfile.TemporaryDirectory() as _td:        # the probe is written outside the repository
    _probe = Path(_td) / "probe.py"
    _probe.write_text('import json\nbody = json.dumps({"model": "x", "prompt": "p", '
                      '"options": {"temperature": 0}})\n'
                      'chat = json.dumps({"model": "x", "messages": [], "temperature": 0})\n',
                      encoding="utf-8")
    _named = uncapped_payloads([_probe], root=Path(_td))
    check("the scan can fail: an Ollama payload without num_predict and a chat payload without "
          "max_tokens are both named", _named == ["probe.py:2", "probe.py:3"], str(_named))

print(f"\nllm output cap: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
