#!/usr/bin/env python3
"""PREREG-V3 A8 C5a: research/v3/probe_a8.py's verdicts - spawns NO child, reads no product. Every input is a record
written here: a source tree, the proxy's call lines, the adapter's counters, an install record:

* a source fact is exactly one match of a declared pattern in a file read as data - its value, its line, the sha256 of
  the file's BYTES; none is blocked:source-missing:<field>, two are blocked:source-ambiguous:<field>; a path that
  leaves the tree is refused;
* the proxy's lines of one arm and one unit (<run>.<unit>) on the v1 endpoint - nothing else is counted;
* mem0's fields: the pin (2.2.0, from the install record's import answer), the client (the adapter's start answer names
  the wrapped path, else blocked:no-llm-client), the usage (the adapter's LLMUsage equals the proxy's answered lines -
  calls and both token sums - with no failed and no unreported call; zero against zero is unmeasured, not a pass), the
  temperature and the thinking field as the pinned source sends them, the response format and no tool offered, the
  timestamp refusal found in the source;
* the verdict: "pass" only when every field is ok, there is no problem, every boundary check is complete with 0/0,
  and no catcher line - else the first blocked:<reason> in the declared order, else "fail"; probe.json is written once.

    python tests/research/_test_v3_probe_a8.py
"""
from __future__ import annotations

import atexit
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic like every suite


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load("v3_probe_a8_t", ROOT / "research" / "v3" / "probe_a8.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_probe_a8_"))


def _cleanup() -> None:
    """At the end and at exit, whatever raised: the link under lroot first, by its own entry (a junction's target is
    never walked), then the tree - a crashed run leaves nothing behind."""
    link = TMP / "lroot" / "link"
    if os.path.lexists(link):
        (os.unlink if os.path.islink(link) else os.rmdir)(link)
    shutil.rmtree(TMP, ignore_errors=True)


atexit.register(_cleanup)
SRC = TMP / "site"
(SRC / "mem0" / "llms").mkdir(parents=True)
DEEPSEEK = (b"class DeepSeekLLM:\r\n"
            b"    def generate_response(self, messages, response_format=None):\r\n"
            b"        params = {\"model\": self.config.model, \"temperature\": 0.1,\r\n"
            b"                  \"max_tokens\": 2000}\r\n")
(SRC / "mem0" / "llms" / "deepseek.py").write_bytes(DEEPSEEK)
(SRC / "mem0" / "twice.py").write_bytes(b"x = 1\nx = 2\n")


RAISED: list = []


class _Safe:
    """C4A-8 / C5A-8 (the auditor), applied to every call by its signature, not row by row: a verdict never raises - a
    raise comes back as a RAISED marker of that verdict's own shape, so the row that called it FAILs by its name, the
    closing row names it, and the suite never crashes. The rows that expect a refusal call P directly."""

    def __getattr__(self, name):
        def call(*a, **k):
            try:
                return getattr(P, name)(*a, **k)
            except Exception as e:  # noqa: BLE001 - the promise under test
                msg = f"RAISED {type(e).__name__}: {e}"
                RAISED.append(f"{name}: {msg}")
                if name == "verdict":
                    return msg, [msg]
                if name == "mine":
                    return []
                return {"ok": None, "value": None, "rule_failed": msg}
        return call


S = _Safe()


def ok(fn) -> bool:
    """A row's condition; a raise FAILs that row by its name and the closing row lists it."""
    try:
        return bool(fn())
    except Exception as e:  # noqa: BLE001 - the row reads it
        RAISED.append(f"condition: {type(e).__name__}: {e}")
        return False


def refused(fn):
    try:
        fn()
        return "accepted"
    except P.ProbeError as e:
        return f"refused: {e}"
    except Exception as e:  # noqa: BLE001 - another type is the row's to read
        return f"{type(e).__name__}: {e}"


print("- a source fact: exactly one match, read as data -")
f = S.fact(SRC, "mem0/llms/deepseek.py", r'"temperature":\s*([0-9.]+)', name="m0_temperature")
sha = hashlib.sha256(DEEPSEEK).hexdigest()
check("one match: the value, its 1-based line, the sha256 of the file's bytes (CRLF and all), and the source string",
      f.get("value") == "0.1" and f.get("line") == 3 and f.get("sha256") == sha and f.get("file") == "mem0/llms/deepseek.py"
      and f.get("source") == f"mem0/llms/deepseek.py:3@sha256:{sha}" and "blocked" not in f, str(f))
f0 = S.fact(SRC, "mem0/llms/deepseek.py", r'"top_p":\s*([0-9.]+)', name="m0_top_p")
check("no match is blocked:source-missing:<field>", f0.get("blocked") == "blocked:source-missing:m0_top_p"
      and f0.get("value") is None, str(f0))
fm = S.fact(SRC, "mem0/llms/nope.py", r"x", name="m0_x")
check("a missing file is blocked:source-missing:<field> too", fm.get("blocked") == "blocked:source-missing:m0_x", str(fm))
f2 = S.fact(SRC, "mem0/twice.py", r"x = (\d)", name="m0_x")
check("two matches are blocked:source-ambiguous:<field> - never the first", f2.get("blocked") == "blocked:source-ambiguous:m0_x"
      and f2.get("value") is None and f2.get("lines") == [1, 2], str(f2))
check("a path that leaves the tree is refused", refused(lambda: P.fact(SRC, "../outside.py", r"x", name="m0_x")).startswith("refused")
      and refused(lambda: P.fact(SRC, str(TMP / "abs.py"), r"x", name="m0_x")).startswith("refused"))
LROOT, OUTSIDE = TMP / "lroot", TMP / "outside"
LROOT.mkdir()
OUTSIDE.mkdir()
(OUTSIDE / "x.py").write_bytes(b"x = 1\n")
LINK = LROOT / "link"
try:
    os.symlink(OUTSIDE, LINK, target_is_directory=True)
except OSError:                                     # no symlink privilege on Windows: a junction is the same escape
    import _winapi
    _winapi.CreateJunction(str(OUTSIDE), str(LINK))
esc = refused(lambda: P.fact(LROOT, "link/x.py", r"x = (\d)", name="m0_x"))
check("C5A-1: a link inside the root to a directory outside is refused - the file resolves outside the root",
      esc.startswith("refused") and "resolves outside" in esc, esc)
BAD = b"v = 7\n\xff\xfe not utf-8\n"
(SRC / "mem0" / "bad.py").write_bytes(BAD)
fb = S.fact(SRC, "mem0/bad.py", r"^v = (\d)", name="m0_v")
check("C5A-6: the sha256 is the file's BYTES, even where they are not UTF-8", fb.get("sha256") == hashlib.sha256(BAD).hexdigest()
      and fb.get("value") == "7", str(fb))
fl = S.fact(SRC, "mem0/llms/deepseek.py", r"^\s+def (\w+)", name="m0_def")
check("C5A-7: a ^-anchored pattern matches at the start of a later line, with that line",
      fl.get("value") == "generate_response" and fl.get("line") == 2, str(fl))

print("\n- the proxy's lines of one arm and one unit -")


def line(arm="mem0", unit="r1.u1", endpoint="v1", status=200, prompt=100, completion=10, **kw):
    rec = {"arm": arm, "unit": unit, "endpoint": endpoint, "status": status, "complete": True, "refused": None,
           "usage": {"prompt": prompt, "completion": completion}, "temperature": 0.1, "thinking_sent": None,
           "response_format": "json_object", "tools_offered": []}
    rec.update(kw)
    return rec


CALLS = [line(prompt=1000, completion=10), line(prompt=1001, completion=11), line(unit="r1.u2", prompt=5),
         line(arm="letta", prompt=7), line(endpoint="models", prompt=None, completion=None)]
mine = S.mine(CALLS, arm="mem0", run="r1", unit="u1")
check("only this arm's lines of <run>.<unit> on the v1 endpoint", len(mine) == 2
      and all(x["arm"] == "mem0" and x["unit"] == "r1.u1" and x["endpoint"] == "v1" for x in mine), str(len(mine)))

print("\n- m0_usage: the adapter's LLMUsage against the proxy -")
SNAP = {"calls": 2, "failed": 0, "no_usage": 0, "prompt_tokens": 2001, "completion_tokens": 21}
u = S.m0_usage(SNAP, CALLS, run="r1", unit="u1")
check("equal counts and sums pass, and the value holds both sides", u["ok"] is True
      and u["value"] == {"adapter": SNAP, "proxy": {"calls": 2, "prompt_tokens": 2001, "completion_tokens": 21,
                                                    "unanswered": 0}}, str(u))
r = S.m0_usage(SNAP, CALLS + [line(status=500, prompt=None, completion=None)], run="r1", unit="u1")
check("the SDK's own retry (a 500, then the 200 of the same create) is one call: only answered lines count, and the "
      "unanswered line is recorded", r["ok"] is True and ((r.get("value") or {}).get("proxy") or {}).get("calls") == 2
      and ((r.get("value") or {}).get("proxy") or {}).get("unanswered") == 1, str(r))
r = S.m0_usage(SNAP, CALLS + [line(complete=False)], run="r1", unit="u1")
check("C5A-2: a 200 line that never completed (an abandoned stream) is not an answered line",
      r["ok"] is True and ((r.get("value") or {}).get("proxy") or {}).get("calls") == 2, str(r))
for label, patch, want in (("calls", {"calls": 3}, "calls"), ("prompt", {"prompt_tokens": 2002}, "prompt_tokens"),
                           ("completion", {"completion_tokens": 20}, "completion_tokens"),
                           ("no_usage", {"no_usage": 1}, "no_usage"), ("failed", {"failed": 1}, "failed")):
    r = S.m0_usage({**SNAP, **patch}, CALLS, run="r1", unit="u1")
    check(f"a {label} that differs (or is not 0) fails by its name", r["ok"] is False and want in r["rule_failed"], str(r))
r = S.m0_usage({**SNAP, "calls": 3, "failed": 1}, CALLS + [line(status=500, prompt=None, completion=None)], run="r1", unit="u1")
check("an upstream 500 is not an answered line: the adapter's failed call fails the field by name",
      r["ok"] is False and "failed" in r["rule_failed"], str(r))
r = S.m0_usage({**SNAP, "calls": 3}, CALLS + [line(refused="model_mismatch")], run="r1", unit="u1")
check("a line the proxy refused is not an answered line", r["ok"] is False and "calls" in r["rule_failed"], str(r))
r = S.m0_usage({"calls": 0, "failed": 0, "no_usage": 0, "prompt_tokens": 0, "completion_tokens": 0}, [], run="r1", unit="u1")
check("zero calls against zero lines is unmeasured, never a pass", r["ok"] is False and "unmeasured" in r["rule_failed"], str(r))
r = S.m0_usage(None, CALLS, run="r1", unit="u1")
check("no LLMUsage snapshot fails by name", r["ok"] is False and "llm_usage" in r["rule_failed"], str(r))

print("\n- m0_client, m0_pin -")
ok_start = {"id": 1, "ok": True, "llm_usage": P.M0_USAGE_SOURCE}
c = S.m0_client(ok_start)
check("the adapter's start answer names the wrapped path", c["ok"] is True and c["value"] == P.M0_USAGE_SOURCE, str(c))
c = S.m0_client({"id": 1, "ok": False, "error": "Refused: mem0's LLM has no OpenAI client (llm.client.chat.completions"
                                                 ".create) - the K87 check-2 source (Q-AB-1) would be missing"})
check("the adapter's refusal is blocked:no-llm-client (nevertwice becomes the in-process A/B arm, §4.6)",
      c["ok"] is False and c.get("blocked") == "blocked:no-llm-client", str(c))
c = S.m0_client({"id": 1, "ok": False, "error": "ValueError: something else"})
check("any other failed start is a failure, not that blocked reason", c["ok"] is False and "blocked" not in c, str(c))
c = S.m0_client({"id": 1, "ok": True, "llm_usage": None})
check("a start that names no wrapped path fails", c["ok"] is False, str(c))
c = S.m0_client({"id": 1, "ok": True, "llm_usage": "mem0.embedder.client, response.usage"})
check("C5A-3: a start that names ANOTHER path fails, the path named", c["ok"] is False
      and "mem0.embedder.client" in (c.get("rule_failed") or ""), str(c))
src = (ROOT / "research" / "v3" / "arms" / "arm_mem0.py").read_text(encoding="utf-8")
check("the wrapped path the probe expects is the adapter's own literal, written once there",
      src.count(json.dumps(P.M0_USAGE_SOURCE)) == 1, P.M0_USAGE_SOURCE)
INST = {"problems": [], "import_versions": {"python": "3.12.10", "dists": {"mem0ai": "2.2.0"}}}
check("the pin: an install record with no problem and mem0ai 2.2.0 passes", S.m0_pin(INST)["ok"] is True)
check("no install record is blocked:not-installed", S.m0_pin(None).get("blocked") == "blocked:not-installed")
check("an install record with a problem is blocked:not-installed",
      S.m0_pin({**INST, "problems": ["pip's offline install failed (exit 1)"]}).get("blocked") == "blocked:not-installed")
check("another version is blocked:not-the-pin",
      S.m0_pin({**INST, "import_versions": {"python": "3.12.10", "dists": {"mem0ai": "2.2.1"}}}).get("blocked")
      == "blocked:not-the-pin")

print("\n- temperature, thinking, format and tools, timestamp -")
T = S.fact(SRC, "mem0/llms/deepseek.py", r'"temperature":\s*([0-9.]+)', name="m0_temperature")
t = S.m0_temperature(T, CALLS, run="r1", unit="u1")
check("the proxy's temperature equals the pinned source's: the recorded value is the source's, with its source",
      t["ok"] is True and t["value"] == 0.1 and t["source"] == T["source"], str(t))
t = S.m0_temperature(T, [line(temperature=0.7), line()], run="r1", unit="u1")
check("a line sending 0.7 against the source's 0.1 fails naming both; the value stays the source's",
      t["ok"] is False and t["value"] == 0.1 and "0.7" in t["rule_failed"] and "0.1" in t["rule_failed"], str(t))
t = S.m0_temperature(T, [], run="r1", unit="u1")
check("no line is unmeasured", t["ok"] is False and "unmeasured" in t["rule_failed"], str(t))
t = S.m0_temperature(f0, CALLS, run="r1", unit="u1")
check("a blocked source fact carries its blocked reason into the field", t["ok"] is False
      and t.get("blocked") == "blocked:source-missing:m0_top_p", str(t))
t = S.m0_temperature({"value": "self.config.temperature", "source": "mem0/llms/deepseek.py:3@sha256:z"}, CALLS,
                     run="r1", unit="u1")
check("C5A-9: a source temperature that is not a number is blocked:source-unparsable:m0_temperature, never a raise",
      t["ok"] is False and t.get("blocked") == "blocked:source-unparsable:m0_temperature", str(t))
h = S.m0_thinking(None, CALLS, run="r1", unit="u1")
check("no thinking field in the source and none sent: the route is none", h["ok"] is True and h["value"] == "none", str(h))
h = S.m0_thinking(None, [line(), line(thinking_sent="enabled")], run="r1", unit="u1")
check("a thinking field sent that the source does not set fails", h["ok"] is False and "enabled" in h["rule_failed"], str(h))
h = S.m0_thinking(None, [], run="r1", unit="u1")
check("no line is unmeasured for thinking too", h["ok"] is False and "unmeasured" in h["rule_failed"], str(h))
h = S.m0_thinking({"value": None, "blocked": "blocked:source-ambiguous:m0_thinking"}, CALLS, run="r1", unit="u1")
check("C5A-4: a blocked thinking source fact carries its blocked reason into the field", h["ok"] is False
      and h.get("blocked") == "blocked:source-ambiguous:m0_thinking", str(h))
FMT = {"value": "json_object", "source": "mem0/memory/main.py:10@sha256:x", "file": "f", "line": 10, "sha256": "x"}
r = S.m0_format_tools(FMT, CALLS, run="r1", unit="u1")
check("every line asks the source's response format and offers no tool", r["ok"] is True, str(r))
r = S.m0_format_tools(FMT, [line(), line(tools_offered=["add_memory"])], run="r1", unit="u1")
check("a line that offers a tool fails by name", r["ok"] is False and "add_memory" in r["rule_failed"], str(r))
r = S.m0_format_tools(FMT, [line(), line(response_format=None)], run="r1", unit="u1")
check("a line with another response format fails", r["ok"] is False and "response_format" in r["rule_failed"], str(r))
TS = {"value": "timestamp", "source": "mem0/memory/main.py:99@sha256:y", "file": "f", "line": 99, "sha256": "y"}
check("the OSS timestamp refusal found in the source keeps date_route header", S.m0_timestamp(TS)["value"] == "header"
      and S.m0_timestamp(TS)["ok"] is True)
ts0 = S.m0_timestamp({"value": None, "blocked": "blocked:source-missing:m0_timestamp"})
check("a timestamp refusal missing from the source is blocked by name - never a silent switch of the route",
      ts0["ok"] is False and ts0.get("blocked") == "blocked:source-missing:m0_timestamp" and ts0["value"] is None, str(ts0))

print("\n- the verdict and probe.json -")
CLEAN = {"complete": True, "native_hits": 0, "loopback_hits": 0, "fs_hits": 0}
GOOD = {"m0_pin": S.m0_pin(INST), "m0_client": S.m0_client(ok_start), "m0_usage": S.m0_usage(SNAP, CALLS, run="r1", unit="u1")}
out, reasons = S.verdict(GOOD, problems=[], checks=[("adapter", CLEAN)], catcher_lines=0)
check("every field ok, no problem, every check 0/0, no catcher line: pass", out == "pass" and reasons == [], str(reasons))
bad = {**GOOD, "m0_usage": S.m0_usage({**SNAP, "calls": 3}, CALLS, run="r1", unit="u1")}
out, reasons = S.verdict(bad, problems=[], checks=[("adapter", CLEAN)], catcher_lines=0)
check("one failed field makes it fail, the field named", out == "fail" and any("m0_usage" in r for r in reasons), str(reasons))
blk = {"m0_pin": S.m0_pin(None), "m0_client": S.m0_client({"ok": False, "error": "Refused: mem0's LLM has no OpenAI client"}),
       "m0_usage": S.m0_usage(None, [], run="r1", unit="u1")}
out, reasons = S.verdict(blk, problems=[], checks=[], catcher_lines=0)
check("the first blocked reason in the declared order is the outcome", out == "blocked:not-installed", out)
for label, chk in (("incomplete", {**CLEAN, "complete": False}), ("native unknown", {**CLEAN, "native_hits": None}),
                   ("native hit", {**CLEAN, "native_hits": 1}), ("fs change", {**CLEAN, "fs_hits": 2}),
                   ("fs unknown", {**CLEAN, "fs_hits": None})):
    out, reasons = S.verdict(GOOD, problems=[], checks=[("adapter", chk)], catcher_lines=0)
    check(f"a boundary check that is {label} is a problem by the step's name", out == "fail"
          and any("adapter" in r for r in reasons), str(reasons))
out, reasons = S.verdict(GOOD, problems=[], checks=[("adapter", CLEAN)], catcher_lines=1)
check("a catcher line of the arm is a problem", out == "fail" and any("catcher" in r for r in reasons), str(reasons))
out, reasons = S.verdict(GOOD, problems=["the proxy flagged a canary"], checks=[("adapter", CLEAN)], catcher_lines=0)
check("any problem keeps it from passing", out == "fail" and "the proxy flagged a canary" in reasons, str(reasons))
out, reasons = S.verdict(GOOD, problems=["blocked:local-server - no proxy line from the container"], checks=[], catcher_lines=0)
check("a problem that is a blocked reason is the outcome when no field is blocked",
      out == "blocked:local-server", out)
dest = TMP / "runs" / "_a8" / "r1" / "mem0" / "probe.json"
P.write_probe(dest, {"arm": "mem0", "outcome": "pass"})
check("probe.json is written", json.loads(dest.read_text(encoding="utf-8"))["outcome"] == "pass")
check("probe.json is written once - a second write is refused", refused(lambda: P.write_probe(dest, {"x": 1})).startswith("refused")
      and json.loads(dest.read_text(encoding="utf-8"))["outcome"] == "pass")

print("\n- C5a-2a: mem0's source facts, declared before any read of 2.2.0 (the auditor's O-a) -")
M2 = TMP / "site22"
for d in ("mem0/configs/llms", "mem0/llms", "mem0/memory"):
    (M2 / d).mkdir(parents=True)
(M2 / "mem0/configs/llms/deepseek.py").write_bytes(
    b"class DeepSeekConfig:\n    def __init__(\n        self,\n        model=None,\n        temperature: float = 0.3,\n"
    b"        max_tokens: int = 2000,\n    ):\n        pass\n")
(M2 / "mem0/llms/deepseek.py").write_bytes(b"class DeepSeekLLM:\n    def generate_response(self, messages, response_format=None):\n"
                                           b"        return self.client.chat.completions.create(**params)\n")
MAIN = (b"from mem0.utils.entity_extraction import extract_entities\n"
        b"from mem0.utils.lemmatization import lemmatize_for_bm25\n"
        b"\n"
        b"class Memory:\n"
        b"    def add(self, messages, timestamp=None):\n"
        b"        if timestamp is not None:\n"
        b"            raise ValueError(get_temporal_feature_error_message(\"sync\", \"add\", \"timestamp\"))\n"
        b"        return self._add_to_vector_store(messages)\n"
        b"\n"
        b"    def _add_to_vector_store(self, messages, infer=True):\n"
        b"        response = self.llm.generate_response(\n"
        b"            messages=[{\"role\": \"system\", \"content\": \"x\"}],\n"
        b"            response_format={\"type\": \"json_object\"},\n"
        b"        )\n"
        b"        try:\n"
        b"            items = json.loads(response, strict=False).get(\"memory\", [])\n"
        b"        except ValueError:\n"
        b"            items = json.loads(extract_json(response), strict=False).get(\"memory\", [])\n"
        b"        mem_texts = [m.get(\"text\", \"\") for m in items if m.get(\"text\")]\n"
        b"        return mem_texts\n"
        b"\n"
        b"class AsyncMemory:\n"
        b"    async def add(self, messages, timestamp=None):\n"
        b"        if timestamp is not None:\n"
        b"            raise ValueError(await get_temporal_feature_error_message_async(\"async\", \"add\", \"timestamp\"))\n"
        b"\n"
        b"    async def _add_to_vector_store(self, messages, infer=True):\n"
        b"        response = await asyncio.to_thread(self.llm.generate_response, messages=messages)\n"
        b"        items = json.loads(response, strict=False).get(\"memory\", [])\n")
(M2 / "mem0/memory/main.py").write_bytes(MAIN)
sc = S.scope(M2, "mem0/memory/main.py", "Memory._add_to_vector_store", name="m0_add_path")
check("a function scope is exactly one class and one def by its qualified name - the async twin is another class",
      sc.get("first_line") == 10 and "blocked" not in sc and sc.get("sha256") == hashlib.sha256(MAIN).hexdigest()
      and sc.get("source") == f"mem0/memory/main.py:10@sha256:{hashlib.sha256(MAIN).hexdigest()}", str({k: sc.get(k) for k in ("first_line", "blocked")}))
check("a class or def that is not there is blocked:source-missing:<field>",
      S.scope(M2, "mem0/memory/main.py", "Memory._add_nowhere", name="m0_x").get("blocked") == "blocked:source-missing:m0_x"
      and S.scope(M2, "mem0/memory/main.py", "Mem.add", name="m0_y").get("blocked") == "blocked:source-missing:m0_y")
(M2 / "mem0/memory/dup.py").write_bytes(b"class Memory:\n    def add(self):\n        pass\n\n    def add(self):\n        pass\n")
check("two defs of one name in the class are blocked:source-ambiguous:<field>",
      S.scope(M2, "mem0/memory/dup.py", "Memory.add", name="m0_z").get("blocked") == "blocked:source-ambiguous:m0_z")
ts = S.fact_in(S.scope(M2, "mem0/memory/main.py", "Memory.add", name="m0_timestamp"), P.M0_SOURCE["m0_timestamp"][2],
               name="m0_timestamp")
check("a fact within a scope: exactly one match there, its line counted in the file (the sync refusal, not the async)",
      ts.get("line") == 6 and "blocked" not in ts, str(ts))
ck = S.fact_in(sc, P.M0_SOURCE["m0_content_key"][2], name="m0_content_key")
check("the answer's key read where the answer is parsed: 'memory', on its own line", ck.get("value") == "memory"
      and ck.get("line") == 16, str(ck))
(M2 / "mem0/memory/two.py").write_bytes(b"class Memory:\n    def add(self):\n        pass\n\nclass Memory:\n    def add(self):\n        pass\n")
check("two classes of one name are blocked:source-ambiguous:<field>",
      S.scope(M2, "mem0/memory/two.py", "Memory.add", name="m0_w").get("blocked") == "blocked:source-ambiguous:m0_w")
asc = S.scope(M2, "mem0/memory/main.py", "AsyncMemory._add_to_vector_store", name="m0_async")
check("an async def is a function scope too", asc.get("first_line") == 27 and "blocked" not in asc, str(asc.get("blocked")))
hs = S.llm_sites(asc, P.M0_SOURCE["m0_llm_sites"][2])
check("a call that hands the LLM call on (a thread, a pool) is a site; no response_format written is None",
      hs.get("value") == [{"line": 28, "in_loop": False, "response_format": None}], str(hs))
amb = S.fact_in(sc, r'\.get\("(\w+)", \[\]\)', name="m0_amb")
check("two matches within a scope are blocked:source-ambiguous:<field> with their lines",
      amb.get("blocked") == "blocked:source-ambiguous:m0_amb" and amb.get("lines") == [16, 18], str(amb))
nos = S.llm_sites(S.scope(M2, "mem0/memory/main.py", "Memory.add", name="m0_llm_sites"), P.M0_SOURCE["m0_llm_sites"][2])
check("a scope with no LLM call site is blocked:source-missing:m0_llm_sites", nos.get("blocked") == "blocked:source-missing:m0_llm_sites",
      str(nos))
bsc = S.fact_in(S.scope(M2, "mem0/memory/main.py", "Memory.nothing", name="m0_scope_q"), r"x", name="m0_q")
check("a fact within a blocked scope carries the SCOPE's blocked reason", bsc.get("blocked") == "blocked:source-missing:m0_scope_q",
      str(bsc))
RAW = b"class Memory:\n    # \xff\xfe not utf-8\n    def add(self):\n        pass\n"
(M2 / "mem0/memory/raw.py").write_bytes(RAW)
rs = S.scope(M2, "mem0/memory/raw.py", "Memory.add", name="m0_raw")
check("a scope's sha256 is the file's BYTES, even where they are not UTF-8", rs.get("sha256") == hashlib.sha256(RAW).hexdigest()
      and rs.get("first_line") == 3, str({k: rs.get(k) for k in ("sha256", "first_line", "blocked")}))
sites = S.llm_sites(sc, P.M0_SOURCE["m0_llm_sites"][2])
check("the add path's LLM call sites by the AST: one, its line, its response_format as written, not in a loop",
      sites.get("value") == [{"line": 11, "in_loop": False, "response_format": {"type": "json_object"}}], str(sites))
(M2 / "mem0/memory/loop.py").write_bytes(
    b"class Memory:\n    def _add_to_vector_store(self, facts):\n        for f in facts:\n"
    b"            self.llm.generate_response(messages=f, response_format={\"type\": \"text\"})\n"
    b"        self.llm.generate_response(messages=facts, response_format=fmt)\n")
lp = S.llm_sites(S.scope(M2, "mem0/memory/loop.py", "Memory._add_to_vector_store", name="m0_add_path"),
                 P.M0_SOURCE["m0_llm_sites"][2])
check("a call site inside a loop is marked, and a response_format that is not a literal is 'unparsable'",
      lp.get("value") == [{"line": 4, "in_loop": True, "response_format": {"type": "text"}},
                          {"line": 5, "in_loop": False, "response_format": "unparsable"}], str(lp))
SNAP1 = {"calls": 1, "failed": 0, "no_usage": 0, "prompt_tokens": 1000, "completion_tokens": 10}
one = [line(prompt=1000, completion=10)]
r = S.m0_calls_per_add(sites, one, run="r1", unit="u1", adds=1)
check("calls per add: the answered lines of the unit are at most the sites x the adds", r["ok"] is True
      and r["value"] == {"bound_per_add": 1, "adds": 1, "answered": 1}, str(r))
r = S.m0_calls_per_add(sites, one + [line()], run="r1", unit="u1", adds=1)
check("more answered lines than the bound fail by name", r["ok"] is False and "bound" in r["rule_failed"], str(r))
r = S.m0_calls_per_add(sites, one + [line(unit="r1.u2"), line(arm="letta"), line(status=500, prompt=None, completion=None)],
                       run="r1", unit="u1", adds=1)
check("only the unit's own answered lines count against the bound", r["ok"] is True
      and (r.get("value") or {}).get("answered") == 1, str(r))
r = S.m0_calls_per_add(sites, [], run="r1", unit="u1", adds=1)
check("no answered line is unmeasured", r["ok"] is False and "unmeasured" in r["rule_failed"], str(r))
r = S.m0_calls_per_add(lp, one, run="r1", unit="u1", adds=1)
check("a site in a loop has no static bound: blocked:source-unbounded:m0_calls_per_add",
      r.get("blocked") == "blocked:source-unbounded:m0_calls_per_add", str(r))
fm1 = S.formats(sites)
check("C5A-10: one distinct response_format across the sites is the single format", fm1.get("value") == "json_object"
      and "formats" not in fm1, str(fm1))
two = {"value": [{"line": 3, "in_loop": False, "response_format": {"type": "json_object"}},
                 {"line": 9, "in_loop": False, "response_format": {"type": "text"}}], "source": "x"}
fm2 = S.formats(two)
check("C5A-10: formats that differ across the sites become a set, the sites listed", fm2.get("value") is None
      and fm2.get("formats") == ["json_object", "text"] and fm2.get("sites") == [3, 9], str(fm2))
r = S.m0_format_tools(fm2, [line(), line(response_format="text")], run="r1", unit="u1")
check("C5A-10: with a set, each line's format must be one of the sites' formats", r["ok"] is True, str(r))
r = S.m0_format_tools(fm2, [line(response_format="xml")], run="r1", unit="u1")
check("C5A-10: a format none of the sites declares fails", r["ok"] is False and "xml" in r["rule_failed"], str(r))
fm3 = S.formats(lp)
check("a site whose format is not a literal makes the format blocked:source-unparsable:m0_format_tools",
      fm3.get("blocked") == "blocked:source-unparsable:m0_format_tools", str(fm3))
th = S.thinking_fact(M2)
check("no thinking field in the pinned DeepSeek LLM: the thinking route is none (None to m0_thinking)", th is None, str(th))
(M2 / "mem0/llms/deepseek.py").write_bytes(b"class DeepSeekLLM:\n    extra_body = {\"thinking\": {\"type\": \"disabled\"}}\n")
th = S.thinking_fact(M2)
check("a thinking field in the pinned DeepSeek LLM is blocked:source-changed:m0_thinking - its meaning comes to the "
      "auditor, never guessed", (th or {}).get("blocked") == "blocked:source-changed:m0_thinking", str(th))
(M2 / "mem0/llms/deepseek.py").unlink()
th = S.thinking_fact(M2)
check("no DeepSeek LLM file at all is blocked:source-missing:m0_thinking, never the route none",
      (th or {}).get("blocked") == "blocked:source-missing:m0_thinking", str(th))
nlp = S.all_in(M2, P.M0_SOURCE["m0_nlp"][0], P.M0_SOURCE["m0_nlp"][1], name="m0_nlp")
check("the spaCy-backed utilities main.py imports, every one with its line (Q-A8-8: facts for the auditor)",
      nlp.get("value") == [{"value": "entity_extraction", "line": 1}, {"value": "lemmatization", "line": 2}], str(nlp))
tf = S.fact(M2, P.M0_SOURCE["m0_temperature"][0], P.M0_SOURCE["m0_temperature"][1], name="m0_temperature")
check("the DeepSeek config's own temperature default, one match", tf.get("value") == "0.3" and tf.get("line") == 5, str(tf))
check("every declared mem0 source fact names a relative file and a pattern, fixed in code before any read of 2.2.0",
      set(P.M0_SOURCE) == {"m0_temperature", "m0_thinking", "m0_timestamp", "m0_llm_sites", "m0_content_key",
                           "m0_item_key", "m0_nlp", *P.M0_NLP_VARS, "m0_nlp_model"}
      and all(not Path(v[0]).is_absolute() for v in P.M0_SOURCE.values()))
ik = S.fact_in(sc, P.M0_SOURCE["m0_item_key"][2], name="m0_item_key")
check("the item's text key, read where the texts are taken", ik.get("value") == "text" and ik.get("line") == 19, str(ik))

(M2 / "mem0/memory/broken.py").write_bytes(b"class Memory:\n    def add(self:\n        pass\n")
check("Q2 (the auditor): a source that does not parse is blocked:source-unparsable:<field>, never a raise",
      S.scope(M2, "mem0/memory/broken.py", "Memory.add", name="m0_b").get("blocked") == "blocked:source-unparsable:m0_b")
(M2 / "mem0/memory/loops.py").write_bytes(
    b"class Memory:\n    def _add_to_vector_store(self, facts):\n        while facts:\n"
    b"            self.llm.generate_response(messages=facts.pop(), response_format={\"type\": \"json_object\"})\n"
    b"\n    def comp(self, facts):\n"
    b"        return [self.llm.generate_response(messages=f, response_format={\"type\": \"json_object\"}) for f in facts]\n")
for label, fn, want_line in (("a while", "_add_to_vector_store", 4), ("a list comprehension", "comp", 7)):
    ws = S.llm_sites(S.scope(M2, "mem0/memory/loops.py", f"Memory.{fn}", name="m0_add_path"), P.M0_SOURCE["m0_llm_sites"][2])
    r = S.m0_calls_per_add(ws, one, run="r1", unit="u1", adds=1)
    check(f"Q12/Q13 (the auditor): an LLM site inside {label} is in a loop, so the calls per add are "
          f"blocked:source-unbounded (C6's bound)", ws.get("value") == [{"line": want_line, "in_loop": True,
                                                                          "response_format": {"type": "json_object"}}]
          and r.get("blocked") == "blocked:source-unbounded:m0_calls_per_add", str(ws.get("value")))
(M2 / "mem0/memory/nested.py").write_bytes(
    b"def factory():\n    class Memory:\n        def add(self):\n            pass\n    return Memory\n"
    b"\nclass Memory:\n    def add(self):\n        pass\n")
ns = S.scope(M2, "mem0/memory/nested.py", "Memory.add", name="m0_n")
check("Q5 (the auditor): only the module's own top-level class is taken - a class of the same name nested in a "
      "function is not", ns.get("first_line") == 8 and "blocked" not in ns, str({k: ns.get(k) for k in ("first_line", "blocked")}))
print("\n- NLP-3 (the auditor's passive check): spaCy's state as mem0 left it, read, never loaded -")
(M2 / "mem0/utils").mkdir(parents=True, exist_ok=True)
SPM = (b"import threading\n\n_nlp_full = None\n_nlp_lemma = None\n_load_failed_full = False\n_load_failed_lemma = False\n"
       b"_lock = threading.Lock()\n\n\ndef _ensure_model_available():\n    import spacy\n"
       b"    if not spacy.util.is_package(\"en_core_web_sm\"):\n        download(\"en_core_web_sm\")\n\n\n"
       b"def get_nlp_full():\n    global _nlp_full, _load_failed_full\n    if _load_failed_full:\n        return None\n"
       b"    if _nlp_full is not None:\n        return _nlp_full\n    _nlp_full = spacy.load(\"en_core_web_sm\")\n"
       b"    return _nlp_full\n\n\ndef get_nlp_lemma():\n    global _nlp_lemma, _load_failed_lemma\n"
       b"    if _nlp_lemma is not None:\n        return _nlp_lemma\n    return None\n\n\n"
       b"def _other():\n    return spacy.util.is_package(\"xx_ent_wiki_sm\")\n")
(M2 / "mem0/utils/spacy_models.py").write_bytes(SPM)
NF = S.nlp_facts(M2)
check("the module's state names and the model the product checks for are source facts, each one match",
      {k: (v or {}).get("value") for k, v in NF.items()} == {"m0_nlp_full_var": "_nlp_full", "m0_nlp_lemma_var": "_nlp_lemma",
                                                            "m0_nlp_failed_full_var": "_load_failed_full",
                                                            "m0_nlp_failed_lemma_var": "_load_failed_lemma",
                                                            "m0_nlp_model": "en_core_web_sm"}
      and NF["m0_nlp_model"].get("line") == 12, str({k: (v or {}).get("value") for k, v in NF.items()}))
NAMES = {"full": "_nlp_full", "lemma": "_nlp_lemma", "failed_full": "_load_failed_full",
         "failed_lemma": "_load_failed_lemma", "model": "en_core_web_sm"}
ON = {"names": NAMES, "module": True, "nlp_full": True, "nlp_lemma": True, "failed_full": False, "failed_lemma": False,
      "is_package": True}
r = S.m0_nlp_active(ON, NF, catcher_lines=0)
check("both models loaded after the adds, no failed flag, the model installed, no catcher line: active",
      r["ok"] is True and r["value"] == {"model": "en_core_web_sm", "nlp_full": True, "nlp_lemma": True}, str(r))
for label, patch, want in (("NA6 (the auditor): the full model's load unknown", {"nlp_full": None}, "nlp_full"),
                           ("the model not installed", {"is_package": False}, "is_package"),
                           ("the model's install unknown", {"is_package": None}, "is_package"),
                           ("a failed full load", {"failed_full": True}, "failed_full"),
                           ("a failed lemma load", {"failed_lemma": True}, "failed_lemma"),
                           ("an unknown failed flag", {"failed_full": None}, "failed_full"),
                           ("no full model after the adds", {"nlp_full": False}, "nlp_full"),
                           ("no lemma model after the adds", {"nlp_lemma": False}, "nlp_lemma"),
                           ("the module never imported", {"module": False}, "module")):
    r = S.m0_nlp_active({**ON, **patch}, NF, catcher_lines=0)
    check(f"{label} is blocked:nlp-off, named", r["ok"] is False and r.get("blocked") == "blocked:nlp-off"
          and want in r["rule_failed"], str(r))
r = S.m0_nlp_active(ON, NF, catcher_lines=1)
check("a catcher line of the unit (a run-time model download) is blocked:nlp-off", r.get("blocked") == "blocked:nlp-off"
      and "catcher" in r["rule_failed"], str(r))
r = S.m0_nlp_active({**ON, "names": {**NAMES, "full": "_nlp"}}, NF, catcher_lines=0)
check("the adapter must read exactly the names the pinned source defines - another name is blocked:nlp-off",
      r.get("blocked") == "blocked:nlp-off" and "_nlp" in r["rule_failed"], str(r))
r = S.m0_nlp_active({**ON, "names": {**NAMES, "model": "en_core_web_md"}}, NF, catcher_lines=0)
check("NA13 (the auditor): the adapter reporting another model than the source's is blocked:nlp-off, naming both",
      r.get("blocked") == "blocked:nlp-off" and "en_core_web_md" in r["rule_failed"] and "en_core_web_sm" in r["rule_failed"],
      str(r))
check("NA11/NA12 (the auditor): in the module's real shape (global lines, uses, a second is_package outside the "
      "function) each state name is still one module-level line and the model is read inside _ensure_model_available",
      NF["m0_nlp_full_var"].get("line") == 3 and NF["m0_nlp_model"].get("line") == 12
      and S.fact(M2, "mem0/utils/spacy_models.py", r'spacy\.util\.is_package\("([\w.-]+)"\)', name="m0_x").get("blocked")
      == "blocked:source-ambiguous:m0_x"
      and S.fact(M2, "mem0/utils/spacy_models.py", r"(_nlp_full)", name="m0_y").get("blocked") == "blocked:source-ambiguous:m0_y",
      str({k: (v or {}).get("line") for k, v in NF.items()}))
r = S.m0_nlp_active(None, NF, catcher_lines=0)
check("no state from the adapter is blocked:nlp-off by that name, never a pass", r.get("blocked") == "blocked:nlp-off"
      and "no nlp state" in (r.get("rule_failed") or ""), str(r))
(M2 / "mem0/utils/spacy_models.py").write_bytes(SPM.replace(b"_load_failed_lemma = False\n", b""))
r = S.m0_nlp_active(ON, S.nlp_facts(M2), catcher_lines=0)
check("a state name the pinned source no longer defines carries blocked:source-missing - it comes to the auditor",
      r.get("blocked") == "blocked:source-missing:m0_nlp_failed_lemma_var", str(r))
print("\n- C5a-2c: the mem0 probe's assembly - its source facts, its fake's script, its ops, its fields -")
(M2 / "mem0/utils/spacy_models.py").write_bytes(SPM)
(M2 / "mem0/llms/deepseek.py").write_bytes(b"class DeepSeekLLM:\n    def generate_response(self, messages):\n        pass\n")
SF = S.mem0_source_facts(M2)
check("the source facts: every declared one read from the tree, each by its own rule",
      ok(lambda: SF["m0_temperature"]["value"] == "0.3" and SF["m0_thinking"] is None and SF["m0_timestamp"]["line"] == 6
         and SF["m0_llm_sites"]["value"][0]["line"] == 11 and SF["m0_format"]["value"] == "json_object"
         and SF["m0_content_key"]["value"] == "memory" and SF["m0_item_key"]["value"] == "text"
         and [x["value"] for x in SF["m0_nlp"]["value"]] == ["entity_extraction", "lemmatization"]
         and SF["nlp"]["m0_nlp_model"]["value"] == "en_core_web_sm"), str({k: (v or {}).get("value") if isinstance(v, dict) else v
                                                                             for k, v in SF.items() if k != "nlp"})[:300])
sc_ = S.mem0_script(SF)
check("Q-DRV-5 (proposed): the fake's script is ONE answer in the pinned source's own shape - {content_key: [{item_key: "
      "text}]} - so every call, the gate's probes included, gets a valid mem0 answer",
      ok(lambda: len(sc_) == 1 and json.loads(sc_[0]["content"]) == {"memory": [{"text": P.M0_SCRIPT_TEXT}]}), str(sc_))
check("the answer's keys are the source's own, whatever they are - never a literal the probe assumes",
      ok(lambda: json.loads(S.mem0_script({**SF, "m0_content_key": {"value": "facts"}, "m0_item_key": {"value": "fact"}})[0]["content"])
         == {"facts": [{"fact": P.M0_SCRIPT_TEXT}]}))
check("a blocked shape fact is a blocked script - no answer is invented",
      ok(lambda: S.mem0_script({**SF, "m0_item_key": {"value": None, "blocked": "blocked:source-missing:m0_item_key"}})
         == {"blocked": "blocked:source-missing:m0_item_key"}))
check("the ops are declared data: role user or assistant, a speaker, a text, a date on every one (a dated stand)",
      ok(lambda: len(P.M0_OPS) == 3 and all(o["item"]["role"] in ("user", "assistant") and o["item"]["speaker"]
                                            and o["item"]["text"] and o["date"][:10] == "2026-03-02" for o in P.M0_OPS)
         and len({o["item"]["item_id"] for o in P.M0_OPS}) == 3))
START = {"ok": True, "llm_usage": P.M0_USAGE_SOURCE}
NLP_ON = {"names": {"full": "_nlp_full", "lemma": "_nlp_lemma", "failed_full": "_load_failed_full",
                    "failed_lemma": "_load_failed_lemma", "model": "en_core_web_sm"}, "module": True, "nlp_full": True,
          "nlp_lemma": True, "failed_full": False, "failed_lemma": False, "is_package": True}
C3 = [line(prompt=1000 + i, completion=10 + i, temperature=0.3) for i in range(3)]
CNT = {"llm_usage": {"calls": 3, "failed": 0, "no_usage": 0, "prompt_tokens": 3003, "completion_tokens": 33}, "nlp": NLP_ON}
F = S.mem0_fields(start=START, counters=CNT, calls=C3, catcher_lines=0, facts=SF, install_record=INST, run="r1", unit="u1",
                  adds=3)
check("the fields in their declared order, every one from its own inputs - and together they pass",
      ok(lambda: list(F) == list(P.M0_FIELDS) and all(f["ok"] for f in F.values())
         and P.verdict(F, problems=[], checks=[("probe", CLEAN)], catcher_lines=0)[0] == "pass"),
      str({k: (v.get("ok"), v.get("rule_failed")) for k, v in F.items()} if isinstance(F, dict) else F)[:400])
check("the declared order, written out: the pin first (not installed outranks everything), the client, the usage, the "
      "bound, the sent fields, the timestamp, the nlp check, then the information",
      list(P.M0_FIELDS) == ["m0_pin", "m0_client", "m0_usage", "m0_calls_per_add", "m0_temperature", "m0_thinking",
                            "m0_format_tools", "m0_timestamp", "m0_nlp_active", "m0_nlp"], str(P.M0_FIELDS))
F2 = S.mem0_fields(start=START, counters=CNT, calls=C3, catcher_lines=1, facts=SF, install_record=INST, run="r1", unit="u1",
                   adds=3)
check("the catcher lines reach m0_nlp_active (a run-time download is blocked:nlp-off)",
      ok(lambda: F2["m0_nlp_active"].get("blocked") == "blocked:nlp-off"), str(F2.get("m0_nlp_active")))
F3 = S.mem0_fields(start=START, counters=CNT, calls=C3, catcher_lines=0, facts=SF, install_record=INST, run="r1", unit="u1",
                   adds=2)
check("the adds reach m0_calls_per_add (3 answered lines over 1 site x 2 adds fail)",
      ok(lambda: F3["m0_calls_per_add"]["ok"] is False and "bound" in F3["m0_calls_per_add"]["rule_failed"]),
      str(F3.get("m0_calls_per_add")))
F4 = S.mem0_fields(start=START, counters={**CNT, "llm_usage": None}, calls=C3, catcher_lines=0, facts=SF,
                   install_record={**INST, "import_versions": {"dists": {"mem0ai": "2.2.1"}}}, run="r1", unit="u1", adds=3)
check("the install record reaches m0_pin and the counters' llm_usage reaches m0_usage",
      ok(lambda: F4["m0_pin"].get("blocked") == "blocked:not-the-pin" and F4["m0_usage"]["ok"] is False), str(F4.get("m0_pin")))
F5 = S.mem0_fields(start=START, counters=CNT, calls=C3, catcher_lines=0, facts={**SF, "m0_thinking": {"value": None,
                   "blocked": "blocked:source-changed:m0_thinking"}}, install_record=INST, run="r1", unit="u1", adds=3)
check("the thinking fact reaches m0_thinking (a changed source is blocked, to the auditor)",
      ok(lambda: F5["m0_thinking"].get("blocked") == "blocked:source-changed:m0_thinking"), str(F5.get("m0_thinking")))
F6 = S.mem0_fields(start=START, counters=CNT, calls=[line(prompt=1000 + i, completion=10 + i, temperature=0.9) for i in range(3)],
                   catcher_lines=0, facts=SF, install_record=INST, run="r1", unit="u1", adds=3)
check("the calls reach m0_temperature under the right unit (0.9 against the source's 0.3 fails)",
      ok(lambda: F6["m0_temperature"]["ok"] is False and F6["m0_usage"]["ok"] is True), str(F6.get("m0_temperature")))
check("m0_nlp is information: the imports listed, never failing the probe", ok(lambda: F["m0_nlp"]["ok"] is True
      and [x["value"] for x in F["m0_nlp"]["value"]] == ["entity_extraction", "lemmatization"]), str(F.get("m0_nlp")))
print("\n- C6 C1: the source facts of mem0's writer bound, declared before any read of 2.2.0 -")
M3 = TMP / "site_bound"
SYS_PROMPT = "You extract memories. \u00e9" * 3
#: F-C6-4: the prompt helpers, parse_messages, parse_vision_messages and DeepSeekLLM.generate_response are mem0 2.0.19's
#: own (Apache-2.0), reformatted - other quotes, docstrings and comments the shape drops - so the declared shapes match
PRO_SRC = r'''ADDITIVE_EXTRACTION_PROMPT = """{SYS}"""

AGENT_CONTEXT_SUFFIX = """agent suffix"""

PAST_MESSAGE_TRUNCATION_LIMIT = 300


def _truncate_content(text, limit=PAST_MESSAGE_TRUNCATION_LIMIT):
    """Truncate text to limit characters - a docstring the shape drops."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."  # a comment the shape drops


def _format_summary(summary):
    if isinstance(summary, dict):
        return summary.get("summary", "")
    return summary or ""


def _format_conversation_history(messages):
    if not messages:
        return ""
    result = ""
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("message") or msg.get("content", "")
        if role and content:
            result += f"{role}: {_truncate_content(content)}\n"
    return result


def _serialize_memories(memories):
    return json.dumps(memories or [], ensure_ascii=False)


def _format_new_messages(new_messages):
    if isinstance(new_messages, str):
        return new_messages
    return json.dumps(new_messages or [], ensure_ascii=False)


def _resolve_dates(current_date=None, observation_date=None):
    if current_date is None:
        current_date = datetime.now(timezone.utc).date().isoformat()
    if observation_date is None:
        observation_date = current_date
    return current_date, observation_date


def generate_additive_extraction_prompt(
    summary=None,
    recently_extracted_memories=None,
    existing_memories=None,
    new_messages=None,
    *,
    last_k_messages=None,
    current_date=None,
    timestamp=None,
    custom_instructions=None,
    use_input_language=False,
):
    current_date, observation_date = _resolve_dates(current_date, timestamp)
    sections = []
    sections.append(f"## Last k Messages\n{_format_conversation_history(last_k_messages)}")
    sections.append(f"## Existing Memories\n{_serialize_memories(existing_memories)}")
    sections.append(f"## New Messages\n{_format_new_messages(new_messages)}")
    if custom_instructions:
        sections.append(f"## Custom Instructions\n{custom_instructions}")
    if use_input_language:
        sections.append("## Language\nkeep it")
    sections.append("# Output:")
    return "\n\n".join(sections)
'''
MAI_SRC = r'''class Memory:
    def __init__(self, config=None):
        self.config = config
        self.custom_instructions = self.config.custom_instructions

    def add(
        self,
        messages,
        *,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        timestamp: Optional[Any] = None,
        infer: bool = True,
        memory_type: Optional[str] = None,
        prompt: Optional[str] = None,
    ):
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        elif isinstance(messages, dict):
            messages = [messages]
        if self.config.llm.config.get("enable_vision"):
            messages = parse_vision_messages(messages, self.llm, self.config.llm.config.get("vision_details"))
        else:
            messages = parse_vision_messages(messages)
        vector_store_result = self._add_to_vector_store(messages, processed_metadata, effective_filters, infer, prompt=prompt)
        return vector_store_result

    def _add_to_vector_store(self, messages, metadata, filters, infer, prompt=None):
        last_messages = self.db.get_last_messages(session_scope, limit=10)
        parsed_messages = parse_messages(messages)
        existing_results = self.vector_store.search(
            query=parsed_messages,
            vectors=query_embedding,
            top_k=10,
            filters=search_filters,
        )
        existing_memories = []
        for idx, mem in enumerate(existing_results):
            existing_memories.append({"id": str(idx), "text": mem.payload.get("data", "")})
        system_prompt = ADDITIVE_EXTRACTION_PROMPT
        if is_agent_scoped:
            system_prompt += AGENT_CONTEXT_SUFFIX
        custom_instr = prompt or self.custom_instructions
        user_prompt = generate_additive_extraction_prompt(
            existing_memories=existing_memories,
            new_messages=parsed_messages,
            last_k_messages=last_messages,
            custom_instructions=custom_instr,
        )
        response = self.llm.generate_response(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        entity = self.vector_store.search(query=q, top_k=1)
'''
UTL_SRC = r'''def parse_messages(messages):
    """Parse the messages - a docstring the shape drops."""
    response = ""
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if content is None:
            continue
        if role == "system":
            response += f"system: {content}\n"
        elif role == "user":
            response += f"user: {content}\n"
        elif role == "assistant":
            response += f"assistant: {content}\n"
    return response


def parse_vision_messages(messages, llm=None, vision_details="auto"):
    returned_messages = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            returned_messages.append(msg)
            continue
        if content is None:
            continue
        if isinstance(content, list):
            if llm is None:
                text_parts = [part["text"] for part in msg["content"] if isinstance(part, dict) and part.get("type") == "text"]
                if not text_parts:
                    continue
                returned_messages.append({"role": role, "content": " ".join(text_parts)})
            else:
                description = get_image_description(msg, llm, vision_details)
                returned_messages.append({"role": role, "content": description})
        elif isinstance(content, dict) and content.get("type") == "image_url":
            if llm is None:
                continue
            image_url_obj = content.get("image_url")
            image_url = image_url_obj.get("url") if isinstance(image_url_obj, dict) else None
            if not image_url:
                raise ValueError("image_url content part is missing image_url.url")
            try:
                description = get_image_description(image_url, llm, vision_details)
                returned_messages.append({"role": role, "content": description})
            except Exception as e:
                raise Exception(f"Error while downloading {image_url}.") from e
        else:
            returned_messages.append(msg)
    return returned_messages
'''
DSK_SRC = r'''class DeepSeekLLM(LLMBase):
    def __init__(self, config=None):
        self.client = OpenAI(base_url=base_url, timeout=t)

    def generate_response(
        self,
        messages: List[Dict[str, str]],
        response_format=None,
        tools: Optional[List[Dict]] = None,
        tool_choice: str = "auto",
        **kwargs,
    ):
        """Generate a response - a docstring the shape drops."""
        params = self._get_supported_params(messages=messages, **kwargs)
        params.update(
            {
                "model": self.config.model,
                "messages": messages,
            }
        )

        if response_format:
            params["response_format"] = response_format
        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice

        response = self.client.chat.completions.create(**params)
        return self._parse_response(response, tools)
'''
CFG_SRC = r'''class MemoryConfig(BaseModel):
    version: str = Field(
        description="The version of the API",
        default="v1.1",
    )
    custom_instructions: Optional[str] = Field(
        description="Custom instructions for fact extraction",
        default=None,
    )
'''
FILES3 = {
    "openai/_constants.py": b"import httpx\n\nDEFAULT_TIMEOUT = 600\nDEFAULT_MAX_RETRIES = 2\n",
    "openai/_client.py": (b"class OpenAI(SyncAPIClient):\n    def __init__(\n        self,\n        *,\n"
                          b"        max_retries: int = DEFAULT_MAX_RETRIES,\n    ):\n        pass\n\n\n"
                          b"class AsyncOpenAI(AsyncAPIClient):\n    def __init__(\n        self,\n        *,\n"
                          b"        max_retries: int = DEFAULT_MAX_RETRIES,\n    ):\n        pass\n"),
    "mem0/llms/deepseek.py": DSK_SRC.encode("utf-8"),
    "mem0/configs/llms/deepseek.py": b"class C:\n    def __init__(\n        self,\n        max_tokens: int = 2000,\n    ):\n        pass\n",
    "mem0/llms/base.py": b"class LLMBase:\n    def _p(self):\n            params[\"max_tokens\"] = self.config.max_tokens\n",
    "mem0/configs/prompts.py": PRO_SRC.replace("{SYS}", SYS_PROMPT).encode("utf-8"),
    "mem0/configs/base.py": CFG_SRC.encode("utf-8"),
    "mem0/memory/main.py": MAI_SRC.encode("utf-8"),
    "mem0/memory/utils.py": UTL_SRC.encode("utf-8"),
}
for rel, data in FILES3.items():
    (M3 / rel).parent.mkdir(parents=True, exist_ok=True)
    (M3 / rel).write_bytes(data)
BF = S.mem0_bound_facts(M3)
val = {k: (v.get("value") if isinstance(v, dict) else None) for k, v in BF.items()} if isinstance(BF, dict) else {}
check("C6: every declared bound fact read from the tree - retries 2 (the SDK's), the client default, one client "
      "with no override, max_tokens 2000 and its send line, the prompts' literal bytes, the prompt call's keywords, "
      "the two windows, the truncation limit and its use, the memory shape, the message frames",
      ok(lambda: val["m0_max_retries"] == "2" and val["m0_client_default"] == "DEFAULT_MAX_RETRIES"
         and val["m0_client_ctor"] == [{"line": 3, "keywords": ["base_url", "timeout"], "starstar": False}]
         and val["m0_client_override"] == [] and val["m0_max_tokens"] == "2000" and BF["m0_max_tokens_sent"].get("line") == 3
         and val["m0_system_prompt"] == len(SYS_PROMPT.encode("utf-8")) and val["m0_agent_suffix"] == len(b"agent suffix")
         and val["m0_system_prompt_used"] == "ADDITIVE_EXTRACTION_PROMPT"
         and val["m0_prompt_call"] == sorted(P.M0_PROMPT_CALL) and val["m0_last_k"] == "10" and val["m0_top_k"] == "10"
         and val["m0_trunc_limit"] == "300" and BF["m0_trunc_used"].get("line") and BF["m0_memory_item"].get("line")
         and val["m0_memory_dump"] == "False" and val["m0_message_frame"] == ["assistant", "system", "user"]
         and S.bound_blocked(BF) == []), str(val)[:600])
up = val.get("m0_user_prompt") if isinstance(val.get("m0_user_prompt"), dict) else {}
check("C6: the user prompt's sections by the AST - each append's constant bytes and fields, the conditional ones "
      "marked, the separator of its join",
      ok(lambda: up["separator"] == "\n\n" and [(x["const_bytes"], x["fields"], x["conditional"]) for x in up["parts"]] == [
          (len("## Last k Messages\n"), ["_format_conversation_history(last_k_messages)"], False),
          (len("## Existing Memories\n"), ["_serialize_memories(existing_memories)"], False),
          (len("## New Messages\n"), ["_format_new_messages(new_messages)"], False),
          (len("## Custom Instructions\n"), ["custom_instructions"], True),
          (len("## Language\nkeep it"), [], True), (len("# Output:"), [], False)]), str(up)[:400])
(M3 / "mem0/configs/prompts.py").write_bytes(FILES3["mem0/configs/prompts.py"].replace(b'f\"## New Messages\\n{', "f\"## Nouveaux Messages \u00e9\\n{".encode("utf-8")))
try:
    up17 = ((S.mem0_bound_facts(M3).get("m0_user_prompt") or {}).get("value") or {}).get("parts") or []
finally:
    (M3 / "mem0/configs/prompts.py").write_bytes(FILES3["mem0/configs/prompts.py"])
check("B17: an f-string section's non-ASCII constant is counted in bytes, never in characters",
      ok(lambda: up17[2]["const_bytes"] == len("## Nouveaux Messages \u00e9\n".encode("utf-8"))), str(up17[2:3]))
check("C6: the add path's own top_k - the search that fills existing memories, not the entity search's top_k=1; a bare "
      "top_k= would be ambiguous", ok(lambda: val["m0_top_k"] == "10" and S.fact_in(S.scope(M3, "mem0/memory/main.py",
                                          "Memory._add_to_vector_store", name="t"), r"top_k=(\d+)", name="t").get("blocked")
                                      == "blocked:source-ambiguous:t"), str(val.get("m0_top_k")))
check("C6: the retries' default is OpenAI's own __init__, never AsyncOpenAI's", ok(lambda: BF["m0_client_default"]["line"] == 5),
      str(BF.get("m0_client_default")))
check("the declared bound facts, written out, all relative paths",
      ok(lambda: set(P.M0_BOUND_SOURCE) == {"m0_max_retries", "m0_client_default", "m0_client_ctor", "m0_client_override",
                                            "m0_max_tokens", "m0_max_tokens_sent", "m0_system_prompt", "m0_agent_suffix",
                                            "m0_system_prompt_used", "m0_user_prompt", "m0_prompt_call", "m0_last_k",
                                            "m0_top_k", "m0_trunc_limit", "m0_trunc_used", "m0_memory_item",
                                            "m0_memory_dump", "m0_message_frame"}
         and all(not Path(v[0]).is_absolute() for v in P.M0_BOUND_SOURCE.values())))


PRO, MAI = FILES3["mem0/configs/prompts.py"], FILES3["mem0/memory/main.py"]


def bound_with(rel, data):
    (M3 / rel).write_bytes(data)
    try:
        return S.bound_blocked(S.mem0_bound_facts(M3))
    finally:
        (M3 / rel).write_bytes(FILES3[rel])


for label, rel, data, want in (
        ("a client with max_retries", "mem0/llms/deepseek.py",
         b"class DeepSeekLLM(LLMBase):\n    def __init__(self, config=None):\n        self.client = OpenAI(base_url=u, max_retries=5)\n",
         "m0_client_ctor"),
        ("a client built with **kwargs", "mem0/llms/deepseek.py",
         b"class DeepSeekLLM(LLMBase):\n    def __init__(self, config=None):\n        self.client = OpenAI(**opts)\n", "m0_client_ctor"),
        ("two clients", "mem0/llms/deepseek.py",
         b"class DeepSeekLLM(LLMBase):\n    def __init__(self, config=None):\n        a = OpenAI()\n        b = OpenAI()\n",
         "m0_client_ctor"),
        ("with_options anywhere in the LLM", "mem0/llms/deepseek.py",
         b"class DeepSeekLLM(LLMBase):\n    def __init__(self, config=None):\n        self.client = OpenAI(base_url=u)\n"
         b"        self.client = self.client.with_options(timeout=5)\n", "m0_client_override"),
        ("a prompt field nobody declared", "mem0/configs/prompts.py",
         FILES3["mem0/configs/prompts.py"].replace(b'sections.append("# Output:")', b'sections.append(f"## X\\n{secret}")'),
         "blocked:source-changed:m0_user_prompt"),
        ("a prompt call with another keyword", "mem0/memory/main.py",
         FILES3["mem0/memory/main.py"].replace(b"custom_instructions=custom_instr,", b"summary=s,"), "m0_prompt_call"),
        ("a system prompt that is not a literal", "mem0/configs/prompts.py",
         FILES3["mem0/configs/prompts.py"].replace(b'ADDITIVE_EXTRACTION_PROMPT = """', b'ADDITIVE_EXTRACTION_PROMPT = X + """'),
         "blocked:source-changed:m0_system_prompt"),
        ("two system prompts", "mem0/configs/prompts.py",
         FILES3["mem0/configs/prompts.py"] + b'\nADDITIVE_EXTRACTION_PROMPT = "again"\n', "blocked:source-ambiguous:m0_system_prompt"),
        ("B6: parse_messages with no role frame", "mem0/memory/utils.py",
         b"def parse_messages(messages):\n    return \"\".join(m[\"content\"] for m in messages)\n", "m0_message_frame"),
        ("B8: a system prompt that is bytes", "mem0/configs/prompts.py",
         PRO.replace(b'ADDITIVE_EXTRACTION_PROMPT = """' + SYS_PROMPT.encode("utf-8") + b'"""',
                     b'ADDITIVE_EXTRACTION_PROMPT = b"plain bytes"'), "blocked:source-changed:m0_system_prompt"),
        ("B8: a system prompt that is a number", "mem0/configs/prompts.py",
         PRO.replace(b'ADDITIVE_EXTRACTION_PROMPT = """' + SYS_PROMPT.encode("utf-8") + b'"""',
                     b"ADDITIVE_EXTRACTION_PROMPT = 123"), "blocked:source-changed:m0_system_prompt"),
        ("B11: the prompt call with **extra", "mem0/memory/main.py",
         MAI.replace(b"custom_instructions=custom_instr,", b"custom_instructions=custom_instr, **extra,"),
         "blocked:source-changed:m0_prompt_call"),
        ("B11: the prompt call with a positional", "mem0/memory/main.py",
         MAI.replace(b"generate_additive_extraction_prompt(\n", b"generate_additive_extraction_prompt(\n            summary,\n"),
         "blocked:source-changed:m0_prompt_call"),
        ("B12: two calls of the prompt builder", "mem0/memory/main.py",
         MAI + b"        again = generate_additive_extraction_prompt(existing_memories=[])\n",
         "blocked:source-ambiguous:m0_prompt_call"),
        ("B14: a section appended from a variable", "mem0/configs/prompts.py",
         PRO.replace(b'sections.append("# Output:")', b'sections.append(header)'), "blocked:source-changed:m0_user_prompt"),
        ("B16: two joins of the sections", "mem0/configs/prompts.py",
         PRO.replace(b'    return "\\n\\n".join(sections)', b'    x = "\\n".join(sections)\n    return "\\n\\n".join(sections)'),
         "blocked:source-ambiguous:m0_user_prompt"),
        ("C6C1-1: sections.extend", "mem0/configs/prompts.py",
         PRO.replace(b'sections.append("# Output:")', b'sections.extend(["# Output:"])'), "blocked:source-changed:m0_user_prompt"),
        ("C6C1-1: sections.insert", "mem0/configs/prompts.py",
         PRO.replace(b'sections.append("# Output:")', b'sections.insert(0, "# Output:")'), "blocked:source-changed:m0_user_prompt"),
        ("C6C1-1: sections +=", "mem0/configs/prompts.py",
         PRO.replace(b'sections.append("# Output:")', b'sections += ["# Output:"]'), "blocked:source-changed:m0_user_prompt"),
        ("C6C1-1: a non-empty initial list", "mem0/configs/prompts.py",
         PRO.replace(b"    sections = []\n", b'    sections = ["# Preface"]\n'), "blocked:source-changed:m0_user_prompt"),
        ("C6C1-1: an assignment by index", "mem0/configs/prompts.py",
         PRO.replace(b'sections.append("# Output:")', b'sections.append("# Output:")\n    sections[0] = "x"'),
         "blocked:source-changed:m0_user_prompt"),
        ("C6C1-1: an alias that appends", "mem0/configs/prompts.py",
         PRO.replace(b'sections.append("# Output:")', b's = sections\n    s.append("# Output:")'), "blocked:source-changed:m0_user_prompt"),
        ("C6C1-1: sections passed to a function", "mem0/configs/prompts.py",
         PRO.replace(b'sections.append("# Output:")', b'_add_output(sections)'), "blocked:source-changed:m0_user_prompt"),
        ("C6C1-1: an annotated non-empty initial list", "mem0/configs/prompts.py",
         PRO.replace(b"    sections = []\n", b'    sections: list = ["# Preface"]\n'), "blocked:source-changed:m0_user_prompt"),
        ("C6C1-1: a bound append", "mem0/configs/prompts.py",
         PRO.replace(b'sections.append("# Output:")', b'add = sections.append\n    add("# Output:")'),
         "blocked:source-changed:m0_user_prompt"),
        ("B14: a section appended from a declared field, not a literal", "mem0/configs/prompts.py",
         PRO.replace(b'sections.append("# Output:")', b'sections.append(custom_instructions)'),
         "blocked:source-changed:m0_user_prompt"),
        ("no truncation of the history lines", "mem0/configs/prompts.py",
         FILES3["mem0/configs/prompts.py"].replace(b"{_truncate_content(content)}", b"{content}"),
         "blocked:source-missing:m0_trunc_used")):
    got = bound_with(rel, data)
    check(f"C6: {label} makes no bound - named ({want})", ok(lambda: any(want in x for x in got)), str(got)[:300])
(M3 / "mem0/configs/prompts.py").write_bytes(PRO.replace(b'sections.append("# Output:")', b'sections.extend(["# Output:"])'))
try:
    upx = S.mem0_bound_facts(M3).get("m0_user_prompt") or {}
finally:
    (M3 / "mem0/configs/prompts.py").write_bytes(PRO)
LN_OUT = 1 + next(i for i, x in enumerate(PRO.split(b"\n")) if b'sections.append("# Output:")' in x)
check("C6C1-1: the blocked user prompt names each other use of sections by its file line",
      ok(lambda: upx["uses"] == [f"line {LN_OUT}: sections.extend"]), str(upx)[:300])

print("\n- F-C6-4: the chain from the adapter's message to the two prompt strings, declared from 2.0.19 -")
UTL, DSK, CFG = FILES3["mem0/memory/utils.py"], FILES3["mem0/llms/deepseek.py"], FILES3["mem0/configs/base.py"]
NEW_F = set()
for _name in ("M0_HELPER_SHAPES", "M0_WRITES", "M0_DEFAULTS", "M0_CALLS", "M0_CONFIG_DEFAULTS"):
    NEW_F |= set(getattr(P, _name, {}))
NEW_F.add("m0_adapter")
#: mem0 2.0.19's helper shapes (scratch record c2a_2019.json), written out a second time: the code's constant must equal it
SHAPES_2019 = {
    "m0_fn_summary": ("mem0/configs/prompts.py", "_format_summary",
                      "eca1bb27e66bcf6112f3263398760ce7b84257e93a04356d23bc7f219a9a6b88"),
    "m0_fn_truncate": ("mem0/configs/prompts.py", "_truncate_content",
                       "5a1733903f942c44b858e38a5008b5380abba74334aadd3811ff8cee698d1bf6"),
    "m0_fn_history": ("mem0/configs/prompts.py", "_format_conversation_history",
                      "b13d2cd83d9ab62dead7df6b72ad5adec25d89612594e33ec8b34f0f07964edb"),
    "m0_fn_memories": ("mem0/configs/prompts.py", "_serialize_memories",
                       "d40626a070252d9c84826b6d66e0190bf66320784516ef59eab08a5db8f4d8d4"),
    "m0_fn_new_messages": ("mem0/configs/prompts.py", "_format_new_messages",
                           "59409fdf8f46337d1320e3131fc5707df513129b81d138f061192881d4781d45"),
    "m0_fn_dates": ("mem0/configs/prompts.py", "_resolve_dates",
                    "9b8b45dcccaee8147f13a376f335beeac05c395867f4830808c6a688081fa689"),
    "m0_fn_parse": ("mem0/memory/utils.py", "parse_messages",
                    "0fae0b7c2fe4ac52861739453e12c572b7f2698c8d1a1f813755cd44fad30990"),
    "m0_fn_vision": ("mem0/memory/utils.py", "parse_vision_messages",
                     "145e95c39edaf28f208ff42522fe87f68fcb27b2b8f03dcb4355915602752e33"),
    "m0_fn_generate": ("mem0/llms/deepseek.py", "DeepSeekLLM.generate_response",
                       "c0488b092afc3aa86e05f7be9eba05d3147378ed014e34ab7503bd6bd325ea12")}
check("F-C6-4: the declared shapes written out - nine helpers by file, qualified name and the sha256 of their "
      "normalized source (ast.unparse, the docstring dropped) under Python 3.14",
      ok(lambda: P.M0_HELPER_SHAPES == SHAPES_2019 and P.M0_SHAPE_PYTHON == (3, 14)))
check("F-C6-4: the declared writes, defaults, calls' arguments and config default, written out, all relative paths",
      ok(lambda: set(P.M0_WRITES) == {"m0_builder_writes", "m0_add_path_writes", "m0_add_writes", "m0_custom_writes"}
         and set(P.M0_DEFAULTS) == {"m0_prompt_defaults", "m0_add_defaults"}
         and set(P.M0_CALLS) == {"m0_prompt_args", "m0_llm_args", "m0_add_call"}
         and set(P.M0_CONFIG_DEFAULTS) == {"m0_custom_default"}
         and P.M0_DEFAULTS["m0_prompt_defaults"][3] == {"summary": "None", "recently_extracted_memories": "None",
                                                         "current_date": "None", "timestamp": "None",
                                                         "use_input_language": "False"}
         and P.M0_DEFAULTS["m0_add_defaults"][3] == {"prompt": "None", "timestamp": "None", "agent_id": "None",
                                                      "memory_type": "None"}
         and P.M0_CALLS["m0_add_call"][3] == {"args": ["messages", "processed_metadata", "effective_filters", "infer"],
                                               "keywords": {"prompt": "prompt"}}
         and P.M0_CONFIG_DEFAULTS["m0_custom_default"][3] == "None"
         and all(not Path(v[0]).is_absolute() for d in (P.M0_HELPER_SHAPES, P.M0_WRITES, P.M0_DEFAULTS, P.M0_CALLS,
                                                           P.M0_CONFIG_DEFAULTS) for v in d.values())))
check("F-C6-4: every new fact read from the tree (2.0.19's own helpers, reformatted) - nineteen facts and the adapter's, "
      "none blocked, and still no reason against the bound",
      ok(lambda: len(NEW_F) == 20 and all(isinstance(BF.get(k), dict) and BF[k].get("blocked") is None
                                          and BF[k].get("value") is not None for k in NEW_F)
         and S.bound_blocked(BF) == []), str({k: (BF.get(k) or {}).get("blocked") for k in sorted(NEW_F)})[:600])
check("F-C6-4: a fact's value - a shape's sha256 and length, the writes by name, the defaults, the calls' arguments, "
      "the config's default, the adapter's keys",
      ok(lambda: BF["m0_fn_truncate"]["value"] == {"sha256": SHAPES_2019["m0_fn_truncate"][2], "chars": 144}
         and BF["m0_add_path_writes"]["value"]["existing_memories"] == [
             "existing_memories = []", "existing_memories.append({'id': str(idx), 'text': mem.payload.get('data', '')})"]
         and BF["m0_builder_writes"]["value"] == {
             "current_date": ["current_date, observation_date = _resolve_dates(current_date, timestamp)"],
             "observation_date": ["current_date, observation_date = _resolve_dates(current_date, timestamp)"]}
         and BF["m0_prompt_defaults"]["value"]["use_input_language"] == "False"
         and BF["m0_llm_args"]["value"]["keywords"]["messages"]
         == "[{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}]"
         and BF["m0_custom_default"]["value"] == "None"
         and BF["m0_adapter"]["value"]["config_keys"] == ["embedder", "history_db_path", "llm", "vector_store"]
         and BF["m0_adapter"]["value"]["add_keywords"] == [["infer", "user_id"], ["infer", "metadata", "user_id"]]
         and all(BF[k].get("source") for k in NEW_F)), str({k: BF.get(k) for k in ("m0_fn_truncate", "m0_adapter")})[:600])
got_q = bound_with("mem0/configs/prompts.py", PRO.replace(b'    return summary or ""', b"    return summary or ''  # same"))
check("F-C6-4: other quotes and a comment keep a helper's shape - no reason against the bound", ok(lambda: got_q == []),
      str(got_q)[:300])
_py = getattr(P, "M0_SHAPE_PYTHON", None)
P.M0_SHAPE_PYTHON = (3, 13)
try:
    bf_py = S.mem0_bound_facts(M3)
finally:
    P.M0_SHAPE_PYTHON = _py
check("F-C6-4: a shape normalized under another Python minor is blocked:normalizer-changed, never compared",
      ok(lambda: bf_py["m0_fn_summary"].get("blocked") == "blocked:normalizer-changed:m0_fn_summary"
         and bf_py["m0_fn_summary"].get("value") is None), str(bf_py.get("m0_fn_summary"))[:300])
(M3 / "mem0/configs/prompts.py").write_bytes(PRO.replace(b'.date().isoformat()', b'.isoformat()'))
try:
    bf_ch = S.mem0_bound_facts(M3)
finally:
    (M3 / "mem0/configs/prompts.py").write_bytes(PRO)
check("F-C6-4: a changed shape carries its normalized text, so the change can be read",
      ok(lambda: bf_ch["m0_fn_dates"]["blocked"] == "blocked:source-changed:m0_fn_dates"
         and "current_date = datetime.now(timezone.utc).isoformat()" in bf_ch["m0_fn_dates"]["text"]),
      str(bf_ch.get("m0_fn_dates"))[:300])
ADP = (ROOT / "research" / "v3" / "arms" / "arm_mem0.py").read_text(encoding="utf-8")


def adapter_with(text):
    f = TMP / "arm_mem0_variant.py"
    f.write_text(text, encoding="utf-8")
    return S.bound_blocked(S.mem0_bound_facts(M3, adapter=f))


for label, rel, data, want in (
        ("F-C6-4: a summary helper with a default text", "mem0/configs/prompts.py",
         PRO.replace(b'    return summary or ""', b'    return summary or "none"'), "blocked:source-changed:m0_fn_summary"),
        ("F-C6-4: no truncation in _truncate_content", "mem0/configs/prompts.py",
         PRO.replace(b'    return text[:limit] + "..."', b"    return text"), "blocked:source-changed:m0_fn_truncate"),
        ("F-C6-4: the history taking more than the content", "mem0/configs/prompts.py",
         PRO.replace(b'msg.get("content", "")\n', b'msg.get("content", "") + msg.get("extra", "")\n'),
         "blocked:source-changed:m0_fn_history"),
        ("F-C6-4: memories serialized with escapes", "mem0/configs/prompts.py",
         PRO.replace(b"return json.dumps(memories or [], ensure_ascii=False)", b"return json.dumps(memories or [], ensure_ascii=True)"),
         "blocked:source-changed:m0_fn_memories"),
        ("F-C6-4: new messages serialized even as a string", "mem0/configs/prompts.py",
         PRO.replace(b"    if isinstance(new_messages, str):\n        return new_messages\n", b""),
         "blocked:source-changed:m0_fn_new_messages"),
        ("F-C6-4: dates as full date-times", "mem0/configs/prompts.py",
         PRO.replace(b".date().isoformat()", b".isoformat()"), "blocked:source-changed:m0_fn_dates"),
        ("F-C6-4: parse_messages adding a line", "mem0/memory/utils.py",
         UTL.replace(b"    return response\n", b'    response += "names: all"\n    return response\n'),
         "blocked:source-changed:m0_fn_parse"),
        ("F-C6-4: parse_vision_messages rewriting a text message", "mem0/memory/utils.py",
         UTL.replace(b"            returned_messages.append(msg)\n    return", b'            returned_messages.append({"role": role, '
                     b'"content": "[text] " + content})\n    return'), "blocked:source-changed:m0_fn_vision"),
        ("F-C6-4: generate_response adding messages", "mem0/llms/deepseek.py",
         DSK.replace(b'                "messages": messages,', b'                "messages": messages + extra,'),
         "blocked:source-changed:m0_fn_generate"),
        ("F-C6-4: a method whose text does not dedent to a parse (a string at column 0)", "mem0/llms/deepseek.py",
         DSK.replace(b"        response = self.client.chat.completions.create(**params)\n",
                     b'        note = """\nat column 0\n"""\n        response = self.client.chat.completions.create(**params)\n'),
         "blocked:source-unparsable:m0_fn_generate"),
        ("F-C6-4: a helper defined twice", "mem0/configs/prompts.py",
         PRO + b"\n\ndef _format_summary(summary):\n    return ''\n", "blocked:source-ambiguous:m0_fn_summary"),
        ("F-C6-4: the builder rebinding a parameter", "mem0/configs/prompts.py",
         PRO.replace(b"    sections = []\n", b"    summary = summary or 'none'\n    sections = []\n"),
         "blocked:source-changed:m0_builder_writes"),
        ("F-C6-4 (the auditor's W3): a del of a builder parameter", "mem0/configs/prompts.py",
         PRO.replace(b"    sections = []\n", b"    del summary\n    sections = []\n"), "blocked:source-changed:m0_builder_writes"),
        ("F-C6-4 (the auditor's W4): a with ... as a builder parameter", "mem0/configs/prompts.py",
         PRO.replace(b"    sections = []\n", b"    with open(p) as summary:\n        pass\n    sections = []\n"),
         "blocked:source-changed:m0_builder_writes"),
        ("F-C6-4 (the auditor's W6): a loop over a builder parameter's name", "mem0/configs/prompts.py",
         PRO.replace(b"    sections = []\n", b"    for summary in notes:\n        pass\n    sections = []\n"),
         "blocked:source-changed:m0_builder_writes"),
        ("F-C6-4 (the auditor's W6): a comprehension target named as a builder parameter", "mem0/configs/prompts.py",
         PRO.replace(b"    sections = []\n", b"    notes = [summary for summary in extra]\n    sections = []\n"),
         "blocked:source-changed:m0_builder_writes"),
        ("F-C6-4 (the auditor's W6): a walrus on a builder parameter", "mem0/configs/prompts.py",
         PRO.replace(b"    sections = []\n", b"    if (summary := notes):\n        pass\n    sections = []\n"),
         "blocked:source-changed:m0_builder_writes"),
        ("F-C6-4: the add path extending the parsed messages", "mem0/memory/main.py",
         MAI.replace(b"        parsed_messages = parse_messages(messages)\n",
                     b"        parsed_messages = parse_messages(messages)\n        parsed_messages += context\n"),
         "blocked:source-changed:m0_add_path_writes"),
        ("F-C6-4: the add path adding memories another way", "mem0/memory/main.py",
         MAI.replace(b"        system_prompt = ADDITIVE_EXTRACTION_PROMPT\n",
                     b"        existing_memories.extend(recent)\n        system_prompt = ADDITIVE_EXTRACTION_PROMPT\n"),
         "blocked:source-changed:m0_add_path_writes"),
        ("F-C6-4: the user prompt extended after the builder", "mem0/memory/main.py",
         MAI.replace(b"        response = self.llm.generate_response(",
                     b"        user_prompt += extra\n        response = self.llm.generate_response("),
         "blocked:source-changed:m0_add_path_writes"),
        ("F-C6-4: add() rewriting the messages", "mem0/memory/main.py",
         MAI.replace(b"        vector_store_result = self._add_to_vector_store(",
                     b"        messages = messages + history\n        vector_store_result = self._add_to_vector_store("),
         "blocked:source-changed:m0_add_writes"),
        ("F-C6-4: custom instructions set outside the config", "mem0/memory/main.py",
         MAI.replace(b"        self.config = config\n", b"        self.config = config\n        self.custom_instructions = 'be brief'\n"),
         "blocked:source-changed:m0_custom_writes"),
        ("F-C6-4: a builder default that is not None", "mem0/configs/prompts.py",
         PRO.replace(b"    summary=None,\n", b"    summary='s',\n"), "blocked:source-changed:m0_prompt_defaults"),
        ("F-C6-4: a new builder parameter", "mem0/configs/prompts.py",
         PRO.replace(b"    use_input_language=False,\n", b"    use_input_language=False,\n    extra_context=None,\n"),
         "blocked:source-changed:m0_prompt_defaults"),
        ("F-C6-4: a builder taking **kwargs", "mem0/configs/prompts.py",
         PRO.replace(b"    use_input_language=False,\n):", b"    use_input_language=False,\n    **kwargs,\n):"),
         "blocked:source-changed:m0_prompt_defaults"),
        ("F-C6-4: a keyword-only builder parameter without a default", "mem0/configs/prompts.py",
         PRO.replace(b"    timestamp=None,\n", b"    timestamp,\n"), "blocked:source-changed:m0_prompt_defaults"),
        ("F-C6-4: a positional builder parameter without a default", "mem0/configs/prompts.py",
         PRO.replace(b"    summary=None,\n", b"    summary,\n"), "blocked:source-changed:m0_prompt_defaults"),
        ("F-C6-4: add()'s prompt default", "mem0/memory/main.py",
         MAI.replace(b"        prompt: Optional[str] = None,\n", b"        prompt: Optional[str] = 'be brief',\n"),
         "blocked:source-changed:m0_add_defaults"),
        ("F-C6-4: add() without a prompt parameter", "mem0/memory/main.py",
         MAI.replace(b"        prompt: Optional[str] = None,\n", b""), "blocked:source-changed:m0_add_defaults"),
        ("F-C6-4: the builder given the raw messages", "mem0/memory/main.py",
         MAI.replace(b"            new_messages=parsed_messages,\n", b"            new_messages=messages,\n"),
         "blocked:source-changed:m0_prompt_args"),
        ("F-C6-4: the LLM sent a third message", "mem0/memory/main.py",
         MAI.replace(b'                {"role": "user", "content": user_prompt},\n',
                     b'                {"role": "user", "content": user_prompt},\n                {"role": "user", "content": parsed_messages},\n'),
         "blocked:source-changed:m0_llm_args"),
        ("F-C6-4: add() passing another prompt", "mem0/memory/main.py",
         MAI.replace(b"infer, prompt=prompt)", b"infer, prompt=prompt or self.default_prompt)"),
         "blocked:source-changed:m0_add_call"),
        ("F-C6-4: two LLM calls on the add path", "mem0/memory/main.py",
         MAI + b"        again = self.llm.generate_response(messages=[])\n", "blocked:source-ambiguous:m0_llm_args"),
        ("F-C6-4: the config's custom instructions with a default text", "mem0/configs/base.py",
         CFG.replace(b"        default=None,\n", b"        default='be brief',\n"), "blocked:source-changed:m0_custom_default"),
        ("F-C6-4: the config's custom instructions from a factory", "mem0/configs/base.py",
         CFG.replace(b"        default=None,\n", b"        default_factory=lambda: 'x',\n"), "blocked:source-changed:m0_custom_default"),
        ("F-C6-4: no custom instructions in the config", "mem0/configs/base.py",
         CFG.split(b"    custom_instructions")[0], "blocked:source-missing:m0_custom_default"),
        ("F-C6-4 (the auditor's CD1): the config's default positional in Field(...)", "mem0/configs/base.py",
         CFG.replace(b"        description=\"Custom instructions", b"        \"x\",\n        description=\"Custom instructions")
         .replace(b"        default=None,\n", b""), "blocked:source-changed:m0_custom_default"),
        ("F-C6-4 (the auditor's CD1): a positional Field(...) value beside default=None", "mem0/configs/base.py",
         CFG.replace(b"        description=\"Custom instructions", b"        \"x\",\n        description=\"Custom instructions"),
         "blocked:source-changed:m0_custom_default"),
        ("F-C6-4 (the auditor's CD1): a default_factory beside default=None", "mem0/configs/base.py",
         CFG.replace(b"        default=None,\n", b"        default=None,\n        default_factory=lambda: 'x',\n"),
         "blocked:source-changed:m0_custom_default"),
        ("F-C6-4: two custom instructions fields in the config", "mem0/configs/base.py",
         CFG + b"    custom_instructions: Optional[str] = None\n", "blocked:source-ambiguous:m0_custom_default"),
        ("F-C6-4: a config that does not parse", "mem0/configs/base.py", CFG + b"def (:\n",
         "blocked:source-unparsable:m0_custom_default")):
    got = bound_with(rel, data)
    check(f"C6: {label} makes no bound - named ({want})", ok(lambda: any(want in x for x in got)), str(got)[:300])
for label, text, want in (
        ("the adapter's config with custom instructions",
         ADP.replace('"history_db_path": str(store / "history.db")}',
                     '"history_db_path": str(store / "history.db"), "custom_instructions": "be brief"}'),
         "blocked:source-changed:m0_adapter"),
        ("the adapter's DeepSeek config with vision",
         ADP.replace('"deepseek_base_url": proxy_base(spec)}', '"deepseek_base_url": proxy_base(spec), "enable_vision": True}'),
         "blocked:source-changed:m0_adapter"),
        ("the adapter passing a prompt to add()",
         ADP.replace("user_id=self.unit, infer=True)", "user_id=self.unit, infer=True, prompt=\"be brief\")"),
         "blocked:source-changed:m0_adapter"),
        ("the adapter passing **kwargs to add()",
         ADP.replace("user_id=self.unit, infer=True)", "user_id=self.unit, infer=True, **extra)"),
         "blocked:source-changed:m0_adapter"),
        ("the adapter writing through another call than add()", ADP.replace("self.mem.add(", "self.mem.put("),
         "blocked:source-changed:m0_adapter")):
    got = adapter_with(text)
    check(f"F-C6-4 (Q-C6-1): {label} makes no bound - named ({want})", ok(lambda: text != ADP and any(want in x for x in got)),
          str(got)[:300])

print("\n- C5b (Q-C5b-1 = O-a): letta's OpenAPI-generic facts, offline, on the independent fake document -")
import copy  # noqa: E402
FL = _load("v3_fake_letta_for_a8_t", ROOT / "tests" / "fixtures" / "v3_fake_products" / "_fake_letta.py")
AL = _load("v3_arm_letta_for_a8_t", ROOT / "research" / "v3" / "arms" / "arm_letta.py")
LDOC = copy.deepcopy(FL.DOC)
LRAW = json.dumps(LDOC, sort_keys=True).encode("utf-8")
LCANON = hashlib.sha256(json.dumps(LDOC, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                        .encode("utf-8")).hexdigest()
pin_l = S.lt_pin(LRAW)
pin_pretty = S.lt_pin(json.dumps(LDOC, indent=2).encode("utf-8"))
check("C5b lt_pin (R-C5-1): the document's pin is the sha256 over canonical JSON, the raw bytes' sha recorded beside it - "
      "another key order or layout keeps the pin; a body that is not a JSON object is blocked:unparsable-openapi",
      ok(lambda: pin_l["ok"] is True and pin_l["value"] == {"sha256": LCANON, "raw_sha256": hashlib.sha256(LRAW).hexdigest(),
                                                            "paths": len(LDOC["paths"])}
         and pin_pretty["value"]["sha256"] == LCANON
         and all(S.lt_pin(b).get("blocked") == "blocked:unparsable-openapi" for b in (b"<html>", b"[1, 2]", b""))),
      str(pin_l)[:300])
LDOC2 = copy.deepcopy(LDOC)
LDOC2["paths"]["/v1/agents/{agent_id}/archival-memory"]["get"]["parameters"].append(
    {"name": "descending", "in": "query", "schema": {"type": "boolean"}})
st_same = S.lt_stable([LRAW, json.dumps(LDOC, indent=2).encode("utf-8")])
st_diff = S.lt_stable([LRAW, json.dumps(LDOC2).encode("utf-8")])
st_one = S.lt_stable([LRAW])
check("C5b lt_stable (R-C5-1, L1): two starts' documents with one canonical sha are stable; otherwise "
      "blocked:unstable-openapi naming the paths that differ; one start is not a comparison",
      ok(lambda: st_same["ok"] is True and st_same["value"] == LCANON
         and st_diff.get("blocked") == "blocked:unstable-openapi"
         and st_diff.get("paths") == ["/v1/agents/{agent_id}/archival-memory"]
         and st_one.get("blocked") == "blocked:one-start"), str((st_same, st_diff, st_one))[:400])
LCALLS = S.letta_calls()
check("C5b letta_calls: the adapter's declared call table, read from arm_letta.py as data (never imported) - every call "
      "as the adapter itself builds it",
      ok(lambda: isinstance(LCALLS, dict) and set(LCALLS) == set(AL.CALLS)
         and all((c.method, c.path, c.query, c.body, c.response) == (a.method, a.path, a.query, a.body, a.response)
                 for c, a in ((LCALLS[k], AL.CALLS[k]) for k in AL.CALLS))), str(sorted(LCALLS) if isinstance(LCALLS, dict) else LCALLS)[:300])
bad_src = TMP / "arm_letta_bad.py"
bad_src.write_text((ROOT / "research" / "v3" / "arms" / "arm_letta.py").read_text(encoding="utf-8").replace(
    'C("GET", "/v1/agents/{agent_id}/context"', 'C("GET", "/v1/agents/" + AGENT + "/context"'), encoding="utf-8")
calls_bad = S.letta_calls(bad_src)
check("C5b letta_calls: a call table entry that is not a literal is blocked:source-changed, never evaluated",
      ok(lambda: isinstance(calls_bad, dict) and calls_bad.get("blocked") == "blocked:source-changed:lt_calls"
         and "context" in (calls_bad.get("entries") or [])), str(calls_bad)[:300])
for label, text, want in (
        ("L8: a call entry with **extra", 'C("GET", "/v1/agents/{agent_id}/context",', 'C("GET", "/v1/agents/{agent_id}/context", **extra,'),
        ("L9: a second CALLS assignment", "\n#: Q-47-8b:", '\nCALLS = {"x": C("GET", "/v1/x")}\n#: Q-47-8b:')):
    f_l = TMP / "arm_letta_owed.py"
    src_l = (ROOT / "research" / "v3" / "arms" / "arm_letta.py").read_text(encoding="utf-8")
    f_l.write_text(src_l.replace(text, want, 1), encoding="utf-8")
    got_l = S.letta_calls(f_l)
    exp_l = "blocked:source-changed:lt_calls" if label.startswith("L8") else "blocked:source-ambiguous:lt_calls"
    check(f"C5b letta_calls (the auditor's {label}) is {exp_l}" + (", the entry named" if label.startswith("L8") else ""),
          ok(lambda: src_l.count(text) >= 1 and got_l.get("blocked") == exp_l
             and (not label.startswith("L8") or got_l.get("entries") == ["context"])), str(got_l)[:300])
LDOC_E = copy.deepcopy(LDOC)
LDOC_E["info"]["description"] = "Letta - mémoire, 记忆"
LRAW_E = json.dumps(LDOC_E, indent=1).encode("utf-8")
L_E = hashlib.sha256(json.dumps(LDOC_E, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
check("C5b lt_pin (the auditor's RS2): a document with non-ASCII text - the pin, the adapter's own document_sha256 and an "
      "independent canonical sha (ensure_ascii=False) are the same bytes' hash",
      ok(lambda: S.lt_pin(LRAW_E)["value"]["sha256"] == P._rest_module().document_sha256(LRAW_E) == L_E
         and L_E != hashlib.sha256(json.dumps(LDOC_E, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()))
conf = S.lt_conformance(LDOC, LCALLS)
LDOC3 = copy.deepcopy(LDOC)
del LDOC3["paths"]["/v1/agents/{agent_id}/context"]
conf_bad = S.lt_conformance(LDOC3, LCALLS)
check("C5b lt_conformance: the adapter's calls against the document (_rest.conformance) - none differs on the fake; a "
      "route the document lacks is blocked:openapi-mismatch, the call named",
      ok(lambda: conf["ok"] is True and conf["value"] == [] and conf_bad.get("blocked") == "blocked:openapi-mismatch"
         and any(p.startswith("context:") for p in conf_bad["value"])), str((conf, conf_bad))[:400])
check("C5b: the recall route's pattern and the archival listing, declared before any real document is read",
      ok(lambda: P.LT_RECALL_ROUTE == r"^/v1/agents/(\{agent_id\}/)?(messages|recall[-_]memory|conversations?)/search/?$"
         and P.LT_RECALL_MODE == ("search_mode", "mode")
         and P.LT_ARCHIVAL == ("/v1/agents/{agent_id}/archival-memory", "get", "limit")))
rr0 = S.lt_recall_route(LDOC)


def with_paths(**extra):
    d = copy.deepcopy(LDOC)
    d["paths"].update(extra)
    return d


R_GET = {"parameters": [FL.AGENT_ID], "get": {"parameters": [
    {"name": "query", "in": "query", "required": True, "schema": FL.STR},
    {"name": "search_mode", "in": "query", "schema": {"type": "string", "enum": ["vector", "fts", "hybrid"],
                                                     "default": "hybrid"}}],
    "responses": {"200": FL._json(FL._arr(FL._ref("LettaMessageUnion")))}}}
R_POST = {"post": {"requestBody": {"required": True, "content": {"application/json": {"schema": {
    "type": "object", "properties": {"agent_id": FL.STR, "query": FL.STR,
                                     "mode": {"$ref": "#/components/schemas/SearchMode"}}}}}},
    "responses": {"200": FL._json(FL._arr(FL._ref("LettaMessageUnion")))}}}
DOC_POST = with_paths(**{"/v1/agents/messages/search": R_POST})
DOC_POST["components"]["schemas"]["SearchMode"] = {"type": "string", "enum": ["vector", "hybrid"], "default": "vector"}
R_NOMODE = {"parameters": [FL.AGENT_ID], "get": {"parameters": [
    {"name": "query", "in": "query", "required": True, "schema": FL.STR}], "responses": {"200": FL._json(FL.STR)}}}
rr1 = S.lt_recall_route(with_paths(**{"/v1/agents/{agent_id}/messages/search": R_GET}))
rr_post = S.lt_recall_route(DOC_POST)
rr_nomode = S.lt_recall_route(with_paths(**{"/v1/agents/{agent_id}/recall-memory/search": R_NOMODE}))
rr2 = S.lt_recall_route(with_paths(**{"/v1/agents/{agent_id}/messages/search": R_GET, "/v1/agents/messages/search": R_POST["post"] and R_POST}))
check("C5b lt_recall_route (Q-47-8b): no route under the declared pattern is a declared deviation - the reader is core + "
      "archival, an E5 row - never a failure; the archival search route is not a recall route",
      ok(lambda: rr0["ok"] is True and rr0["value"] is None and "E5" in rr0["deviation"]), str(rr0)[:300])
check("C5b lt_recall_route: exactly one route gives its path, its methods and its default mode - a query parameter's "
      "default, a JSON body property's default through $ref - or no mode when it has no mode parameter",
      ok(lambda: rr1["value"] == {"path": "/v1/agents/{agent_id}/messages/search", "methods": ["get"], "mode": "hybrid"}
         and rr_post["value"] == {"path": "/v1/agents/messages/search", "methods": ["post"], "mode": "vector"}
         and rr_nomode["value"] == {"path": "/v1/agents/{agent_id}/recall-memory/search", "methods": ["get"], "mode": None}
         and all(x["ok"] is True for x in (rr1, rr_post, rr_nomode))), str((rr1, rr_post, rr_nomode))[:500])
rr_near = S.lt_recall_route(with_paths(**{"/v1/agents/{agent_id}/messages/search/extra": R_GET,
                                          "/x/v1/agents/{agent_id}/messages/search": R_GET}))
check("C5b lt_recall_route (the auditor's L4): the pattern is anchored at both ends - a path that only contains a recall "
      "route is none", ok(lambda: rr_near["ok"] is True and rr_near["value"] is None), str(rr_near)[:300])
check("C5b lt_recall_route: two routes under the pattern are blocked:ambiguous-route, both named - never the first",
      ok(lambda: rr2.get("blocked") == "blocked:ambiguous-route"
         and rr2.get("paths") == ["/v1/agents/messages/search", "/v1/agents/{agent_id}/messages/search"]), str(rr2)[:300])


def with_limit(schema=None, *, ref=False, drop=False):
    d = copy.deepcopy(LDOC)
    op = d["paths"]["/v1/agents/{agent_id}/archival-memory"]["get"]
    if drop:
        del d["paths"]["/v1/agents/{agent_id}/archival-memory"]
        return d
    params = [p for p in op["parameters"] if p.get("name") != "limit"]
    p = {"name": "limit", "in": "query", "schema": schema if schema is not None else FL.INT}
    if ref:
        d["components"]["parameters"] = {"Limit": p}
        p = {"$ref": "#/components/parameters/Limit"}
    op["parameters"] = params + [p]
    return d


ad0 = S.lt_archival_default(LDOC)
ad50 = S.lt_archival_default(with_limit({"type": "integer", "default": 50}))
ad_ref = S.lt_archival_default(with_limit({"type": "integer", "default": 50}, ref=True))
ad_any = S.lt_archival_default(with_limit({"anyOf": [{"type": "integer"}, {"type": "null"}], "default": 1000}))
DOC_PS = with_limit({"$ref": "#/components/schemas/PageSize"})
DOC_PS["components"]["schemas"]["PageSize"] = {"type": "integer", "default": 20}
ad_sref = S.lt_archival_default(DOC_PS)
check("C5b lt_archival_default: a parameter schema given by $ref is followed to its default",
      ok(lambda: ad_sref["ok"] is True and ad_sref["value"] == 20), str(ad_sref)[:300])
check("C5b lt_archival_default: the archival listing's page-size default from the schema - a parameter's own, through a "
      "$ref, beside an anyOf; none in the schema leaves Point V refusing (the source basis is deferred, Q-C5b-1)",
      ok(lambda: ad0["ok"] is True and ad0["value"] is None and "Point V" in ad0["deviation"]
         and ad50["value"] == 50 and ad_ref["value"] == 50 and ad_any["value"] == 1000
         and all(x["ok"] is True for x in (ad50, ad_ref, ad_any))), str((ad0, ad50, ad_ref, ad_any))[:500])
ad_bad = {repr(v): S.lt_archival_default(with_limit({"type": "integer", "default": v})) for v in ("50", True, 0, -5, 1.5)}
ad_missing = S.lt_archival_default(with_limit(drop=True))
check("C5b lt_archival_default: a default that is not a positive int is blocked:source-changed; no archival listing is "
      "blocked:source-missing",
      ok(lambda: all(v.get("blocked") == "blocked:source-changed:lt_archival_default" for v in ad_bad.values())
         and ad_missing.get("blocked") == "blocked:source-missing:lt_archival_default"), str((ad_bad, ad_missing))[:500])

print("\n- C5f (Q-C5-6, Q-C5-7): Claude Code's offered tools from its package (attempt 1), its config names from discovery -")
LA = _load("v3_launch_for_a8_t", ROOT / "research" / "v3" / "launch.py")
PKG = TMP / "cc_pkg"
PKG.mkdir()
ARR = '["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "Task"]'
(PKG / "cli.js").write_text(f'var a=1;const T={ARR};function f(){{return ["x","y"]}}', encoding="utf-8")
off = S.cc_offered_tools(PKG)
check("C5f cc_offered_tools: the pattern declared before any read of the package - one array literal of quoted tool "
      "names holding Bash, Read and Write - in cli.js",
      ok(lambda: P.CC_OFFERED_SOURCE == ("cli.js", r'\[(?=[^\]]*"Bash")(?=[^\]]*"Read")(?=[^\]]*"Write")'
                                                   r'((?:"[A-Za-z]+",\s*)+"[A-Za-z]+")\]')))
check("C5f cc_offered_tools: exactly one match gives the tools, in their order, the file's sha256 named",
      ok(lambda: off["value"] == ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "Task"]
         and off["sha256"] == hashlib.sha256((PKG / "cli.js").read_bytes()).hexdigest()
         and off["source"].startswith("cli.js@sha256:") and off.get("blocked") is None), str(off)[:300])
(PKG / "cli.js").write_text(f"const T={ARR};const U={ARR};", encoding="utf-8")
off2 = S.cc_offered_tools(PKG)
(PKG / "cli.js").write_text('const T=["Read","Write"];', encoding="utf-8")
off0 = S.cc_offered_tools(PKG)
(PKG / "cli.js").unlink()
offm = S.cc_offered_tools(PKG)
check("C5f cc_offered_tools: two matches or none go to attempt 2 by name (Q-C5-7: one discovery spawn), the count kept; "
      "no cli.js is blocked:source-missing",
      ok(lambda: off2.get("blocked") == "blocked:attempt-2:cc_offered" and off2.get("matches") == 2
         and off0.get("blocked") == "blocked:attempt-2:cc_offered" and off0.get("matches") == 0
         and offm.get("blocked") == "blocked:source-missing:cc_offered"), str((off2, off0, offm))[:400])
LA._OFFERED.clear()
LA._OFFERED_ATTEMPT1.clear()
rec_off = S.record_cc_offered(LA, off, "b" * 64)
a1_after_ok = dict(LA._OFFERED_ATTEMPT1)
rec_bad = S.record_cc_offered(LA, off2, "e" * 64)
check("C5f record_cc_offered: one match writes launch's _OFFERED for D7 under the binary's sha256; attempt 1 blocked "
      "writes no tools but launch's attempt-1 failure for that binary (C-CC-1) - the one door to attempt 2",
      ok(lambda: rec_off == {"recorded": True, "binary_sha256": "b" * 64, "tools": off["value"]}
         and LA._OFFERED == {"sha256": "b" * 64, "tools": tuple(off["value"])} and a1_after_ok == {}
         and rec_bad == {"recorded": False, "blocked": "blocked:attempt-2:cc_offered", "attempt1_failed_for": "e" * 64}
         and LA._OFFERED_ATTEMPT1 == {"e" * 64: "blocked:attempt-2:cc_offered"}), str((rec_off, rec_bad))[:300])
LA._OFFERED.clear()
LA._OFFERED_ATTEMPT1.clear()
SPL = TMP / "spawns_c5f.jsonl"
disc = {"spawn_id": "s1", "refused": False, "claude_code": {"sha256": "b" * 64, "config": {
    "first": False, "mode": "discovery", "entries": [{"name": "projects", "kind": "dir", "size": 5, "sha256": "c" * 64},
                                                     {"name": ".claude.json", "kind": "file", "size": 2, "sha256": "d" * 64}]}}}
plain = {"spawn_id": "s2", "refused": False, "claude_code": {"sha256": "b" * 64, "config": {"first": False, "names": []}}}
refd = {**disc, "spawn_id": "s3", "refused": True}
SPL.write_text("\n".join(json.dumps(x) for x in (disc, plain, refd)) + "\n", encoding="utf-8")
names = S.cc_config_names(SPL, "s1")
check("C5f cc_config_names: the probe's discovery spawn's recording IS the list - its names, entries and binary sha256, "
      "for the auditor's review before FREEZE-V3",
      ok(lambda: names["value"] == [".claude.json", "projects"] and names["binary_sha256"] == "b" * 64
         and names["entries"] == disc["claude_code"]["config"]["entries"]), str(names)[:300])
SPL2 = TMP / "spawns_c5f_dup.jsonl"
SPL2.write_text("\n".join(json.dumps(x) for x in (disc, disc)) + "\n", encoding="utf-8")
nm_dup = S.cc_config_names(SPL2, "s1")
check("C5f cc_config_names: two journal lines with one spawn id are ambiguous - never the first",
      ok(lambda: nm_dup.get("blocked") == "blocked:source-ambiguous:cc_config_names"), str(nm_dup)[:300])
nm_bad = {k: S.cc_config_names(SPL, k) for k in ("s2", "s3", "s9")}
check("C5f cc_config_names: a spawn that was not a discovery, a refused one, or none by that id is refused by name",
      ok(lambda: nm_bad["s2"].get("blocked") == "blocked:not-discovery:cc_config_names"
         and nm_bad["s3"].get("blocked") == "blocked:refused-spawn:cc_config_names"
         and nm_bad["s9"].get("blocked") == "blocked:source-missing:cc_config_names"), str(nm_bad)[:400])
check("C4A-8 / C5A-8: no verdict raised on any row - every failure came back as a field", RAISED == [], str(RAISED))
_cleanup()
print(f"\nv3 probe a8: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
