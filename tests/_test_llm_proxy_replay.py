#!/usr/bin/env python3
"""PREREG-V3 plan steps A2.6/A2.7, gated on the real captures: the recording proxy replays the seven A2.5 fixtures.

A fake upstream on loopback answers each of the seven fixed requests (research/v3/capture_deepseek.py CALLS) with the
bytes of its fixture (tests/fixtures/v3_captures), paced by the recorded arrivals (scaled down). Through a recording
arm of the real proxy:

* the client receives each fixture byte for byte, and the upstream receives each fixed body with the key and never
  the arm's token;
* each calls.jsonl record says what the fixture holds - status 200, complete, deepseek-flash, the usage block
  (prompt, completion, cache hit and miss, reasoning), finish reason, tools called, JSON validity - and nothing
  was abandoned, injected or refused;
* the thinking check (§2.2.1) reads real responses: on /v1 the three thinking-off calls are not thinking calls and
  the thinking-on call is; on /anthropic, whose requests name no thinking, both responses are thinking calls (the
  provider's default) - three thinking_call flags in all;
* the tool check on real requests: allowed, get_weather is no violation; on an arm that allows no tool, both
  tool-call requests are tool_violation, refused locally with zero upstream bytes (AQ7);
* nothing the proxy wrote holds the key.

    python tests/_test_llm_proxy_replay.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load("v3_llm_proxy_rp", ROOT / "research" / "_llm_proxy.py")
ST = _load("v3_llm_proxy_selftest_rp", ROOT / "research" / "_llm_proxy_selftest.py")
CAP = _load("v3_capture_rp", ROOT / "research" / "v3" / "capture_deepseek.py")
FX = HERE / "fixtures" / "v3_captures"
MAN = json.loads((FX / "MANIFEST.json").read_bytes())
BY_REQ = {e["request_sha256"]: e for e in MAN["captures"]}
KEY = ST.SENTINEL_KEY.encode()

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_proxy_replay_"))
KEYFILE = TMP / "deepseek.env"
KEYFILE.write_bytes(f"DEEPSEEK_API_KEY={ST.SENTINEL_KEY}\n".encode())


class FixtureUpstream(ST.FakeUpstream):
    """Answers a request with the fixture whose request body hashes the same, paced by its recorded arrivals."""

    PACE = 0.1

    def _serve(self, c, path: bytes) -> bool:
        body = self.requests[-1].split(b"\r\n\r\n", 1)[1]
        e = BY_REQ.get(hashlib.sha256(body).hexdigest())
        if e is None:
            self._send(c, b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            return True
        data = (FX / e["file"]).read_bytes()
        pos, last = 0, 0.0
        for t_ms, n in e["arrivals"] or [(0.0, len(data))]:
            time.sleep(max(0.0, (t_ms - last) / 1000 * self.PACE))
            last = t_ms
            self._send(c, data[pos:pos + n])
            pos += n
        if pos < len(data):
            self._send(c, data[pos:])
        return True


T_CAP, T_NOTOOL = "nvt3-replay-cap-" + "1" * 32, "nvt3-replay-notool-" + "2" * 32


def run(arm: str, token: str, tools: tuple):
    up = FixtureUpstream()
    cfg = P.ProxyConfig(arms=[P.ArmConfig(arm=arm, mode="record", token=token, pinned_model="deepseek-flash",
                                          tools_allowed=tools)],
                        run_dir=TMP / arm, upstream_host="127.0.0.1", upstream_port=up.port, upstream_tls=False,
                        control_token="ctl")
    px = P.Proxy(cfg, P.read_key(KEYFILE), log=lambda m: None)
    wp = px.start()["arms"][arm]["write"]
    got = {}
    for call in CAP.CALLS:
        s = socket.create_connection(("127.0.0.1", wp))
        try:
            s.sendall(CAP.build_request(call, port=wp, unit="u1", token=token))
            got[call["name"]] = CAP.read_response(s, timeout=20)[0]
        finally:
            s.close()
    time.sleep(0.3)
    px.stop()
    up.close()
    recs = [json.loads(x) for x in (TMP / arm / "calls.jsonl").read_bytes().decode().splitlines()]
    flags_f = TMP / arm / "flags.jsonl"
    flags = [json.loads(x) for x in flags_f.read_bytes().decode().splitlines()] if flags_f.exists() else []
    return px, up, got, recs, flags


#: What the seven fixtures hold, read off them once (2026-09-26, run r1) and pinned here, so a regression in the
#: recording mode's own parser cannot agree with itself: usage (prompt, completion, cache hit, cache miss,
#: reasoning), finish reason, tools called, JSON validity.
def _u(p, c, h, m, r=None):
    return {"prompt": p, "completion": c, "cache_hit": h, "cache_miss": m, "reasoning": r}


EXPECTED = {
    "v1_json_object": (_u(47, 6, 0, 47), "stop", [], True),
    "v1_sse_usage": (_u(14, 5, 0, 14), "stop", [], None),
    "v1_sse_thinking_on": (_u(43, 18, 0, 43, 16), "stop", [], None),
    "v1_tool_call": (_u(275, 49, 0, 275), "tool_calls", ["get_weather"], None),
    "v1_tool_call_sse": (_u(275, 49, 128, 147), "tool_calls", ["get_weather"], None),
    "anthropic_messages": (_u(36, 29, None, None), "end_turn", [], None),
    "anthropic_messages_sse": (_u(36, 41, None, None), "end_turn", [], None),
}


print("\n- a recording arm replays the seven captures -")
px, up, got, recs, flags = run("cap", T_CAP, ("get_weather",))
check("the client receives every fixture byte for byte",
      all(got[c["name"]] == (FX / f"{c['name']}.bin").read_bytes() for c in CAP.CALLS),
      str([c["name"] for c in CAP.CALLS if got[c["name"]] != (FX / f"{c['name']}.bin").read_bytes()]))
bodies = [r.split(b"\r\n\r\n", 1)[1] for r in up.requests]
heads = [r.split(b"\r\n\r\n", 1)[0] for r in up.requests]
check("the upstream receives the seven fixed bodies in order, with the key and never the arm's token",
      bodies == [CAP.body_bytes(c) for c in CAP.CALLS] and all(KEY in h and T_CAP.encode() not in h for h in heads))
check("one record per call", len(recs) == 7, str(len(recs)))
ctr = px.counters["cap"]
check("nothing abandoned, injected, refused or mismatched",
      ctr.client_abandoned == 0 and ctr.thinking_injected == 0 and ctr.model_mismatch == 0 and ctr.upstream_errors == 0
      and ctr.refused_auth == 0 and ctr.tool_violation == 0, str(vars(ctr)))
for call, rec in zip(CAP.CALLS, recs):
    usage, finish, tools, json_ok = EXPECTED[call["name"]]
    check(f"{call['name']}: the record is the fixture's (200, complete, model, usage, finish, tools, JSON)",
          rec["status"] == 200 and rec["complete"] is True and rec["response_model"] == "deepseek-flash"
          and rec["usage"] == usage and rec["finish_reason"] == finish
          and rec["tools_called"] == tools and rec["json_ok"] == json_ok and rec["parse_ok"] is True
          and rec["client_abandoned"] is False and rec["thinking_injected"] == 0,
          str({k: rec.get(k) for k in ("status", "complete", "response_model", "usage", "finish_reason", "tools_called")}))
thinking = {c["name"]: r["thinking"] for c, r in zip(CAP.CALLS, recs)}
check("§2.2.1 on real responses: the three /v1 thinking-off calls are not thinking calls, thinking-on is",
      thinking == {**{n: False for n in ("v1_json_object", "v1_sse_usage", "v1_tool_call", "v1_tool_call_sse")},
                   "v1_sse_thinking_on": True, "anthropic_messages": True, "anthropic_messages_sse": True}, str(thinking))
check("... and /anthropic with no thinking named is a thinking call on both endpoints forms (F1): 3 flags in all",
      ctr.thinking_calls == 3 and sum(1 for x in flags if x.get("kind") == "thinking_call" or "thinking_call" in json.dumps(x)) == 3,
      f"{ctr.thinking_calls} {flags}")
check("the ttfb and latency of every record are measured", all(r["ttfb_ms"] is not None and r["latency_ms"] >= r["ttfb_ms"]
                                                                 for r in recs))
check("an allowed get_weather is no tool violation", not any(r.get("tool_violation") for r in recs))

print("\n- the tool check on real tool calls -")
px2, up2, got2, recs2, flags2 = run("notool", T_NOTOOL, ())
viol = [c["name"] for c, r in zip(CAP.CALLS, recs2) if r.get("tool_violation")]
check("on an arm that allows no tool, both tool-call requests are tool_violation, nothing else",
      viol == ["v1_tool_call", "v1_tool_call_sse"] and px2.counters["notool"].tool_violation == 2, str(viol))
check("... refused locally (AQ7): a 4xx to the client, zero upstream bytes - the upstream saw only the other five",
      all(got2[n].startswith(b"HTTP/1.1 4") for n in ("v1_tool_call", "v1_tool_call_sse"))
      and [r.split(b"\r\n\r\n", 1)[1] for r in up2.requests]
      == [CAP.body_bytes(c) for c in CAP.CALLS if c["name"] not in ("v1_tool_call", "v1_tool_call_sse")],
      str([got2[n][:20] for n in ("v1_tool_call", "v1_tool_call_sse")]))

print("\n- nothing written holds the key -")
files = [f for f in TMP.rglob("*") if f.is_file() and f.name != "deepseek.env"]
check(f"the key is in none of the {len(files)} files the proxy wrote", all(KEY not in f.read_bytes() for f in files))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nproxy replay (captures): {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
