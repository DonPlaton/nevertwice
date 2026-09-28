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
        fn = getattr(P, name)

        def call(*a, **k):
            try:
                return fn(*a, **k)
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

check("C4A-8 / C5A-8: no verdict raised on any row - every failure came back as a field", RAISED == [], str(RAISED))
if os.path.islink(LINK):
    os.unlink(LINK)
elif LINK.exists():
    os.rmdir(LINK)                                   # a junction: its own entry only, never the directory it names
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 probe a8: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
