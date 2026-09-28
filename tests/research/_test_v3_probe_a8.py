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
check("C4A-8 / C5A-8: no verdict raised on any row - every failure came back as a field", RAISED == [], str(RAISED))
_cleanup()
print(f"\nv3 probe a8: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
