#!/usr/bin/env python3
"""PREREG-V3 TB1, step A2.6: the proxy's recording mode records what §4.4 lists, refuses what §4.3/§2.6 forbid,
and writes nothing it must not.

A fake upstream answers in DeepSeek's shapes: /v1 JSON with usage (cache hit and miss, reasoning tokens), /v1 SSE
with the usage chunk, tool calls in deltas, /anthropic SSE with a thinking block and a tool_use block.

* One JSONL line per call: response model, fingerprint, usage, finish reason, json_ok, content_empty, tools called,
  thinking, the request key (stable across key order and whitespace, different across arms), the stage stamp.
* Refused locally with no upstream request, counted and flagged: a model other than the port's pin (arm, reader,
  J3), an unparsable body, an offered tool outside the arm's set or matching a forbidden pattern (any case), a
  canary - raw or \\u-escaped - and an owner marker (a name in Latin or Cyrillic, an email, a home path with doubled
  backslashes, an 8-word shingle of the owner's rules). The ancestor canary is counted, not refused. A lone first
  name is not a marker; a marker that fires on smoke text is dropped, and only the count is kept.
* A reasoning response is a thinking call (flagged); flags survive a restart; the log only grows; the request body
  is forwarded byte for byte; /user/balance goes out on the scheduler port only; the catcher tunnels a CONNECT only
  inside an open window, only to its hosts.
* No written file holds a body, a key, a canary, a marker's text, or a name from the owner's files.
* Q-AB-2, the own hop: one sample per forwarded call in raw and record mode alike, the reply bytes unchanged, the
  upstream's wait and the connect left out, the proxy's own work kept in, the formula pinned on a scripted clock,
  the list capped with every sample past the cap counted; a raw arm writes nothing in the run directory (T6).

    python tests/_test_llm_proxy_record.py
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import socket
import sys
import tempfile
import threading
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


P = _load("v3_llm_proxy_r", ROOT / "research" / "_llm_proxy.py")
ST = _load("v3_llm_proxy_selftest_r", ROOT / "research" / "_llm_proxy_selftest.py")

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_proxy_rec_"))
KEYFILE = TMP / "deepseek.env"
KEYFILE.write_bytes(f"DEEPSEEK_API_KEY={ST.SENTINEL_KEY}\n".encode())

V1_JSON = (b'{"id":"c1","model":"deepseek-flash","system_fingerprint":"fp_abc123","choices":[{"index":0,"message":'
           b'{"role":"assistant","content":"{\\"ok\\": true}"},"finish_reason":"stop"}],"usage":{"prompt_tokens":120,'
           b'"completion_tokens":7,"prompt_cache_hit_tokens":64,"prompt_cache_miss_tokens":56,'
           b'"completion_tokens_details":{"reasoning_tokens":0}}}')
V1_REASON = V1_JSON.replace(b'"reasoning_tokens":0', b'"reasoning_tokens":42')
V1_SSE = (b': keep-alive\n\n'
          b'data: {"id":"s","model":"deepseek-flash","system_fingerprint":"fp_s1","choices":[{"delta":{"content":"he"}}]}\n\n'
          b'data: {"id":"s","model":"deepseek-flash","choices":[{"delta":{"tool_calls":[{"index":0,"function":'
          b'{"name":"lookup_memory","arguments":""}}]}}]}\n\n'
          b'data: {"id":"s","model":"deepseek-flash","choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
          b'data: {"id":"s","model":"deepseek-flash","choices":[],"usage":{"prompt_tokens":30,"completion_tokens":9,'
          b'"prompt_cache_hit_tokens":0,"prompt_cache_miss_tokens":30}}\n\n'
          b'data: [DONE]\n\n')
ANTH_SSE = (b'event: message_start\ndata: {"type":"message_start","message":{"model":"deepseek-flash","usage":'
            b'{"input_tokens":50}}}\n\n'
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":'
            b'{"type":"thinking","thinking":""}}\n\n'
            b'event: content_block_start\ndata: {"type":"content_block_start","index":1,"content_block":'
            b'{"type":"tool_use","name":"Read","input":{}}}\n\n'
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":'
            b'{"output_tokens":11}}\n\n'
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n')


class Upstream(ST.FakeUpstream):
    def _serve(self, c, path):
        if path.startswith(b"/v1/chat/completions/reason"):
            body = V1_REASON
        elif path.startswith(b"/v1/chat/completions/fail500"):
            err = b'{"error":{"message":"overloaded"}}'
            self._send(c, b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\nContent-Length: "
                       + str(len(err)).encode() + b"\r\n\r\n" + err)
            return True
        elif path.startswith(b"/v1/chat/completions"):
            body = V1_JSON
        elif path.startswith(b"/user/balance"):
            body = b'{"is_available":true}'
        elif path.startswith(b"/v1/sse") or path.startswith(b"/anthropic/"):
            sse = ANTH_SSE if path.startswith(b"/anthropic/") else V1_SSE
            self._send(c, b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n")
            for piece in sse.split(b"\n\n")[:-1]:
                piece += b"\n\n"
                self._send(c, b"%x\r\n%s\r\n" % (len(piece), piece))
            self._send(c, b"0\r\n\r\n")
            return True
        else:
            return super()._serve(c, path)
        self._send(c, b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                   + str(len(body)).encode() + b"\r\n\r\n" + body)
        return True


IDENTITY = {"name": "Ivan Testov", "email": "ivan.testov@example.org", "home": "C:\\Users\\ivantestov"}
RULE_TEXT = "Always keep the purple elephant ledger sorted by the third column before lunch every day."
CANARIES = {"decoy_env": "nvt3c-decoy_env-" + "a" * 32, "decoy_claude_md": "nvt3c-decoy_claude_md-" + "b" * 32,
            "decoy_credentials": "nvt3c-decoy_credentials-" + "c" * 32, "ancestor": "nvt3c-ancestor-" + "d" * 32}
T_ARM, T_LETTA, T_J3, T_SCHED = ST.TOKEN, ST.TOKEN + "l", ST.TOKEN + "j", ST.TOKEN + "s"


def make(run: str):
    up = Upstream()
    arms = [P.ArmConfig(arm="nevertwice", mode="record", token=T_ARM, pinned_model="deepseek-flash",
                        reader_model="deepseek-flash"),
            P.ArmConfig(arm="letta", mode="record", token=T_LETTA, pinned_model="deepseek-flash",
                        tools_allowed=("send_message", "archival_memory_insert", "Read", "BASH_RUNNER")),
            P.ArmConfig(arm="j3", mode="record", token=T_J3, pinned_model="deepseek-v4-pro"),
            P.ArmConfig(arm="scheduler", mode="record", token=T_SCHED, pinned_model="deepseek-flash")]
    cfg = P.ProxyConfig(arms=arms, run_dir=TMP / run, upstream_host="127.0.0.1", upstream_port=up.port,
                        upstream_tls=False, control_token="ctl-token")
    markers = P.OwnerMarkers(IDENTITY, [RULE_TEXT])
    px = P.Proxy(cfg, P.read_key(KEYFILE), markers=markers, canaries=CANARIES, log=lambda m: None)
    return px, px.start(), up


def call(port: int, path: str, obj=None, *, token=T_ARM, raw: bytes | None = None, method="POST") -> bytes:
    body = raw if raw is not None else (json.dumps(obj).encode() if obj is not None else b"")
    req = (f"{method} {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {token}\r\nContent-Type: application/json\r\n"
           f"Connection: close\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(req)
    got = ST._read_all(s, 5)
    s.close()
    time.sleep(0.05)
    return got


def records(px) -> list[dict]:
    f = px.config.run_dir / "calls.jsonl"
    return [json.loads(x) for x in f.read_bytes().decode().splitlines()] if f.exists() else []


pxc, portsc, upc = make("run_close")                   # the auditor's A2.6 probe, on its own instance
wc = portsc["arms"]["nevertwice"]["write"]
t0 = time.monotonic()
gotc = call(wc, "/u/u1/v1/chat/completions", {"model": "deepseek-flash", "messages": [], "thinking": {"type": "disabled"}})
dtc = time.monotonic() - t0
check("a recording arm's request with Connection: close gets EOF right after the response",
      gotc.startswith(b"HTTP/1.1 200") and dtc < 1.0 and len(records(pxc)) == 1, f"{dtc:.2f}s {gotc[:30]!r}")
pxc.stop()
upc.close()


class _SendFails:
    """An upstream socket that connected but fails on the first send (a reset keep-alive upstream, a broken pipe - the
    auditor's B-SEND): the proxy must still write the call's record, or a key lost this way never becomes
    transport_lost."""

    def __init__(self, exc: type[OSError]) -> None:
        self.exc = exc

    def sendall(self, data: bytes) -> None:
        raise self.exc("the upstream went away")

    def close(self) -> None:
        pass


for n, exc in enumerate((ConnectionResetError, BrokenPipeError)):
    pxs, portss, ups = make(f"run_send{n}")
    pxs._upstream = lambda exc=exc: _SendFails(exc)
    gots = call(portss["arms"]["nevertwice"]["write"], "/u/r1.u1/v1/chat/completions",
                {"model": "deepseek-flash", "messages": [], "thinking": {"type": "disabled"}})
    rs = records(pxs)
    check(f"B-SEND: a send that fails ({exc.__name__}) is a 502 AND a call record - upstream_error, t1, the request "
          f"key and thinking_injected, no status",
          gots.startswith(b"HTTP/1.1 502") and len(rs) == 1 and rs[0].get("upstream_error") == exc.__name__
          and bool(rs[0].get("t1")) and bool(rs[0].get("request_key")) and rs[0].get("status") is None
          and rs[0].get("thinking_injected") == 0 and pxs.counters["nevertwice"].upstream_errors == 1,
          f"{gots[:30]!r} {rs}")
    pxs.stop()
    ups.close()

class _BrokenReply:
    """One side of a socketpair as the upstream: a thread reads the forwarded request, answers with a reply the framer
    cannot parse, and keeps the connection open (the auditor's B-SEND2: the record must still be written)."""

    def __init__(self, reply: bytes) -> None:
        self.mine, self.theirs = socket.socketpair()
        self.reply = reply
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        try:
            self.theirs.recv(65536)
            self.theirs.sendall(self.reply)
            time.sleep(2.0)
        except OSError:
            pass
        finally:
            self.theirs.close()


for n, (label, reply) in enumerate((
        ("a chunk without its CRLF", b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}XX0\r\n\r\n"),
        ("a header line without a colon", b"HTTP/1.1 200 OK\r\nNoColonHere\r\nContent-Length: 2\r\n\r\n{}"))):
    pxb, portsb, upb = make(f"run_broken{n}")
    fake = _BrokenReply(reply)
    pxb._upstream = lambda fake=fake: fake.mine
    call(portsb["arms"]["nevertwice"]["write"], "/u/r1.u1/v1/chat/completions",
         {"model": "deepseek-flash", "messages": [], "thinking": {"type": "disabled"}})
    wait_until_rb = time.monotonic() + 5
    while not records(pxb) and time.monotonic() < wait_until_rb:
        time.sleep(0.05)
    rb = records(pxb)
    check(f"B-SEND2: {label} from the upstream is a call record, complete False, upstream_error ProtocolError and no "
          f"status (a reply we could not parse has none), counted in upstream_errors",
          len(rb) == 1 and rb[0].get("complete") is False and bool(rb[0].get("request_key"))
          and rb[0].get("upstream_error") == "ProtocolError" and rb[0].get("status") is None
          and pxb.counters["nevertwice"].upstream_errors == 1, f"{rb} {pxb.counters['nevertwice'].upstream_errors}")
    pxb.stop()
    upb.close()

px, ports, up = make("run1")
W = ports["arms"]["nevertwice"]["write"]
R = ports["arms"]["nevertwice"]["reader"]
LW = ports["arms"]["letta"]["write"]
MSG = {"model": "deepseek-flash", "messages": [{"role": "user", "content": "extract"}], "temperature": 0.2,
       "max_tokens": 4096, "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"}}

print("\n- one record per call, from the parsed copy -")
ctl = lambda path, obj: call(ports["control"], path, obj, token="ctl-token")  # noqa: E731
ctl("/stage", {"block": "b1", "stage": "write"})
body_bytes = b'{"model":"deepseek-flash",  "messages":[{"role":"user","content":"x"}],"temperature":0.2}'
call(W, "/u/unit-7/v1/chat/completions", raw=body_bytes)
check("the request body reaches the upstream byte for byte in recording mode", up.requests[-1].endswith(body_bytes))
call(W, "/u/unit-7/v1/chat/completions", MSG)
r = records(px)[-1]
check("response model, fingerprint, finish reason", (r["response_model"], r["system_fingerprint"], r["finish_reason"])
      == ("deepseek-flash", "fp_abc123", "stop"), str(r))
check("usage: prompt, completion, cache hit, cache miss, reasoning",
      r["usage"] == {"prompt": 120, "completion": 7, "cache_hit": 64, "cache_miss": 56, "reasoning": 0}, str(r["usage"]))
check("json_object content parsed (json_ok), content not empty", r["json_ok"] is True and r["content_empty"] is False)
check("as sent: model, temperature, max_tokens, response_format, thinking",
      (r["requested_model"], r["temperature"], r["max_tokens"], r["response_format"], r["thinking_sent"])
      == ("deepseek-flash", 0.2, 4096, "json_object", "disabled"), str(r))
check("unit from the /u/ prefix, the stage stamp, the port role", (r["unit"], r["block"], r["stage"], r["port_role"])
      == ("unit-7", "b1", "write", "write"), str(r))
check("no thinking on a zero-reasoning answer", r["thinking"] is False and px.counters["nevertwice"].thinking_calls == 0)
call(W, "/v1/sse", dict(MSG, stream=True))
r = records(px)[-1]
check("SSE: usage from the last chunk, the tool called in a delta, the finish reason",
      r["usage"]["prompt"] == 30 and r["usage"]["cache_miss"] == 30 and r["tools_called"] == ["lookup_memory"]
      and r["finish_reason"] == "tool_calls" and r["system_fingerprint"] == "fp_s1", str(r))
call(W, "/v1/chat/completions/reason", MSG)
r = records(px)[-1]
check("reasoning tokens > 0 is a thinking call, counted and flagged",
      r["thinking"] is True and px.counters["nevertwice"].thinking_calls == 1 and px.flags.get("thinking_call") == 1)
call(LW, "/anthropic/v1/messages", {"model": "deepseek-flash", "messages": [], "stream": True}, token=T_LETTA)
r = records(px)[-1]
check("/anthropic SSE: model, input and output tokens, a thinking block, the tool_use name",
      r["response_model"] == "deepseek-flash" and r["usage"]["prompt"] == 50 and r["usage"]["completion"] == 11
      and r["thinking"] is True and r["tools_called"] == ["Read"] and r["system_fingerprint"] is None, str(r))

print("\n- Q-A5-1: the writer's request strings, for K76's coverage -")
COVER = "a sentence of the unit that is long enough to be a window of the coverage"
ctl("/stage", {"block": "b1", "stage": "write"})
call(W, "/u/r1.cov-1/v1/chat/completions", {"model": "deepseek-flash", "messages": [{"role": "user", "content": COVER}]})
rw = records(px)[-1]
call(R, "/u/r1.cov-1/v1/chat/completions", {"model": "deepseek-flash", "messages": [{"role": "user",
                                                                                     "content": "reader text only"}]})
call(W, "/u/r1.cov-1/v1/chat/completions", {"model": "other-model", "messages": [{"role": "user",
                                                                                  "content": "refused text"}]})
ctl("/stage", {"block": "b1", "stage": "questions"})
call(W, "/u/r1.cov-1/v1/chat/completions", {"model": "deepseek-flash", "messages": [{"role": "user",
                                                                                     "content": "question stage text"}]})
ctl("/stage", {"block": "b1", "stage": "write"})
_bf = px.config.run_dir / "bodies" / "nevertwice" / "r1.cov-1.jsonl"
_braw = _bf.read_bytes() if _bf.exists() else b""
_bl = [json.loads(x) for x in _braw.decode("utf-8").split("\n") if x.strip()]
check("Q-A5-1: a writer call in the write stage leaves its parsed strings in bodies/<arm>/<run>.<unit>.jsonl - keyed "
      "like its call record, via the write port", len(_bl) == 1 and COVER in _bl[0]["strings"]
      and _bl[0]["request_key"] == rw["request_key"] and _bl[0]["via"] == "write" and _bl[0]["unit"] == "r1.cov-1"
      and _bl[0]["t0"] == rw["t0"], str(_bl)[:300])
COVER2 = {"model": "deepseek-flash", "messages": [{"role": "user", "content": "a second unit sentence that the "
                                                                              "LLM first failed to answer"}]}
call(W, "/u/r1.cov-2/v1/chat/completions/fail500", COVER2)
_bf2 = px.config.run_dir / "bodies" / "nevertwice" / "r1.cov-2.jsonl"
_failed_left = _bf2.exists()
call(W, "/u/r1.cov-2/v1/chat/completions", COVER2)
_bl2 = [json.loads(x) for x in _bf2.read_bytes().decode("utf-8").split("\n") if x.strip()] if _bf2.exists() else []
check("Q-A5-1 (C-1): a request the LLM answered 500 leaves no body - it made no memory; its successful retry leaves "
      "one, with status 200", not _failed_left and len(_bl2) == 1 and _bl2[0]["status"] == 200
      and _bl2[0]["request_key"] == records(px)[-1]["request_key"], f"{_failed_left} {_bl2}")
check("Q-A5-1: never a reader call, a refused call or a call in the question stage - and never the token",
      bool(_braw) and all(t_ not in _braw for t_ in (b"reader text only", b"refused text", b"question stage text",
                                                     T_ARM.encode())), _braw.decode("utf-8", "replace")[:300])

print("\n- the request key -")
k1 = P.request_key({"a": 1, "b": [1, 2]}, "nevertwice")
check("the request key ignores key order and whitespace", k1 == P.request_key(json.loads('{ "b":[1,2], "a":1 }'), "nevertwice"))
check("the request key differs across arms", k1 != P.request_key({"a": 1, "b": [1, 2]}, "letta"))

print("\n- refused locally, with no upstream request -")


def refused(label: str, port: int, obj=None, *, kind: str, token=T_ARM, raw=None, path="/v1/chat/completions"):
    n0 = len(up.requests)
    got = call(port, path, obj, token=token, raw=raw)
    r = records(px)[-1]
    check(f"{label}: 400, refused={kind}, no upstream request",
          got.startswith(b"HTTP/1.1 400") and r["refused"] == kind and len(up.requests) == n0,
          f"{got[:30]!r} {r.get('refused')} {len(up.requests) - n0}")


refused("deepseek-v4-pro on an arm port", W, dict(MSG, model="deepseek-v4-pro"), kind="model_mismatch")
refused("a Claude model on an arm port", W, dict(MSG, model="claude-opus-4"), kind="model_mismatch")
refused("deepseek-flash on the J3 port", ports["j3"], MSG, kind="model_mismatch", token=T_J3)
refused("deepseek-v4-pro on the reader port", R, dict(MSG, model="deepseek-v4-pro"), kind="model_mismatch")
refused("an unparsable body", W, raw=b'{"model": "deepseek-flash", ', kind="unparsable")
refused("an offered Bash tool", W, dict(MSG, tools=[{"type": "function", "function": {"name": "Bash"}}]),
        kind="tool_violation")
refused("a legacy functions entry outside the set", LW, dict(MSG, functions=[{"name": "delete_everything"}]),
        kind="tool_violation", token=T_LETTA)
refused("an Anthropic tool BASH_RUNNER (upper case), even though the arm's own set lists it",
        LW, {"model": "deepseek-flash", "tools": [{"name": "BASH_RUNNER"}]},
        kind="tool_violation", token=T_LETTA, path="/anthropic/v1/messages")
n0 = len(up.requests)
call(LW, "/v1/chat/completions", dict(MSG, tools=[{"type": "function", "function": {"name": "archival_memory_insert"}}]),
     token=T_LETTA)
check("a tool in the arm's own set is forwarded", len(up.requests) == n0 + 1 and records(px)[-1]["refused"] is None)
refused("a raw canary", W, dict(MSG, messages=[{"role": "user", "content": "x " + CANARIES["decoy_claude_md"]}]), kind="canary")
esc = json.dumps({"model": "deepseek-flash", "messages": [{"role": "user", "content": CANARIES["decoy_env"]}]},
                 ensure_ascii=True).replace("nvt3c", "\\u006evt3c").encode()
refused("a \\u-escaped canary", W, raw=esc, kind="canary")
n0 = len(up.requests)
call(W, "/v1/chat/completions", dict(MSG, messages=[{"role": "user", "content": "note " + CANARIES["ancestor"]}]))
r = records(px)[-1]
check("the ancestor canary is counted, and not refused", r["ancestor_canary_hits"] == 1 and r["refused"] is None
      and len(up.requests) == n0 + 1)
for label, text in (("the full name", "hello ivan   TESTOV how are you"),
                    ("the Cyrillic name, \\u-escaped", None),
                    ("the email", "write to ivan.testov@example.org"),
                    ("the home path with doubled backslashes", None),
                    ("an 8-word shingle of the owner's rules", "note: keep the purple elephant ledger sorted by the third column")):
    if label.startswith("the Cyrillic"):
        raw = json.dumps({"model": "deepseek-flash", "messages": [{"role": "user", "content": "Иван Тестов"}]},
                         ensure_ascii=True).encode()
        refused(f"owner marker: {label}", W, raw=raw, kind="owner_marker")
    elif label.startswith("the home"):
        raw = b'{"model":"deepseek-flash","messages":[{"role":"user","content":"C:\\\\Users\\\\ivantestov\\\\x"}]}'
        refused(f"owner marker: {label}", W, raw=raw, kind="owner_marker")
    else:
        refused(f"owner marker: {label}", W, dict(MSG, messages=[{"role": "user", "content": text}]), kind="owner_marker")
n0 = len(up.requests)
call(W, "/v1/chat/completions", dict(MSG, messages=[{"role": "user", "content": "Ivan the Terrible, and Plato"}]))
check("a lone first name is not a marker (forwarded)", len(up.requests) == n0 + 1 and records(px)[-1]["owner_marker_hits"] == 0)
m2 = P.OwnerMarkers(IDENTITY, [RULE_TEXT])
dropped = m2.drop_firing(["a smoke unit that happens to mention Ivan Testov"])
check("a marker that fires on smoke text is dropped; only the count is kept",
      dropped >= 1 and not m2.hit(["Ivan Testov"]) and m2.hit(["ivan.testov@example.org"]), str(dropped))

print("\n- scheduler port, catcher windows, flags, the log -")
got_arm = call(W, "/user/balance", method="GET")
got_sched = call(ports["scheduler"], "/user/balance", method="GET", token=T_SCHED)
check("/user/balance: 404 on an arm port, forwarded on the scheduler port",
      got_arm.startswith(b"HTTP/1.1 404") and got_sched.startswith(b"HTTP/1.1 200"), f"{got_arm[:14]!r} {got_sched[:14]!r}")
cp = ports["arms"]["nevertwice"]["catcher"]


def via_catcher(target: str) -> bytes:
    s = socket.create_connection(("127.0.0.1", cp))
    s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    got = ST._read_all(s, 3)
    s.close()
    return got


outside = via_catcher("huggingface.co:443")
ctl("/window", {"name": "w-test", "hosts": ["huggingface.co"], "arms": ["nevertwice"], "state": "open"})
wrong_port = via_catcher("huggingface.co:8443")
no_hop = via_catcher("huggingface.co:443")
ctl("/window", {"name": "w-test", "state": "close"})
check("the catcher refuses a CONNECT outside a window (403)", outside.startswith(b"HTTP/1.1 403"))
check("inside an open window a port other than 443 is still refused (403)", wrong_port.startswith(b"HTTP/1.1 403"))
check("inside an open window, with no declared hop, the catcher answers 502 and dials nothing (A3.a)",
      no_hop.startswith(b"HTTP/1.1 502"), no_hop[:40].decode("latin-1"))
n_lines = len(records(px))
flags_before = dict(px.flags)
px.stop()
up.close()
px2 = P.Proxy(px.config, P.read_key(KEYFILE), log=lambda m: None)
check("flags survive a restart (read back from flags.jsonl)", px2.flags == flags_before and flags_before.get("model_mismatch", 0) >= 4,
      f"{px2.flags} vs {flags_before}")
n_records = sum(c.records for c in px.counters.values())
check("the log only grows: one line per record written, all of them kept", n_lines == n_records and n_lines >= 20,
      f"{n_lines} lines, {n_records} records")

print("\n- nothing written holds what it must not -")
forbidden = [ST.SENTINEL_KEY, *CANARIES.values(), "Ivan Testov", "ivan.testov@example.org", "ivantestov",
             "purple elephant", "Иван", "extract", "hello-from-target"]
files = [f for f in TMP.rglob("*") if f.is_file() and f.name != "deepseek.env"]
bodies = [f for f in files if "bodies" in f.relative_to(TMP).parts]         # Q-A5-1: the writers' request strings
logs_ = [f for f in files if f not in bodies]
leaks = [(f.name, w) for f in logs_ for w in forbidden if w.encode("utf-8") in f.read_bytes()]
check(f"no key, canary, marker text, name or body in the {len(logs_)} written log files", leaks == [], str(leaks[:5]))
body_leaks = [(f.name, w) for f in bodies for w in forbidden if w != "extract" and w.encode("utf-8") in f.read_bytes()]
check(f"Q-A5-1: the {len(bodies)} body files hold request strings only - never the key, a canary, an owner's name or "
      f"marker (such a call is refused before it is sent), nor an answer", bodies and body_leaks == [],
      str(body_leaks[:5]))
check("the run directory holds only the logs - and the writers' request strings under bodies/<arm>/<run>.<unit>.jsonl",
      {f.name for f in logs_} <= {"calls.jsonl", "flags.jsonl", "catcher.jsonl", "windows_proxy.jsonl"}
      and all(f.parent.parent.name == "bodies" and f.suffix == ".jsonl" for f in bodies),
      str(sorted({str(f.relative_to(TMP)) for f in files})))

print("\n- R-FSYNC: a record is on the disk before the proxy goes on - flushed, then fsynced -")
import ast as _ast  # noqa: E402
import os as _os  # noqa: E402

_fsynced: list = []
_real_fsync = _os.fsync


def _count_fsync(fd):
    _fsynced.append(_os.fstat(fd).st_size)        # what the file holds at the fsync
    return _real_fsync(fd)


_os.fsync = _count_fsync                          # the proxy module's os is this os
try:
    _fp = TMP / "fsync_probe" / "calls.jsonl"
    P._append_jsonl(_fp, {"a": 1})
    P._append_jsonl(_fp, {"b": 22})
finally:
    _os.fsync = _real_fsync
_one = len((json.dumps({"a": 1}, sort_keys=True) + "\n").encode("utf-8"))
check("R-FSYNC: each appended record is fsynced once, after its whole line is flushed (a hard kill loses no call)",
      _fsynced == [_one, _fp.stat().st_size], f"{_fsynced} vs {[_one, _fp.stat().st_size]}")


def _appends_outside(src: str) -> list:
    """Functions other than _append_jsonl that open a file for appending (open(..., "a..") or Path.open("a..."))."""
    out = []
    for fn in _ast.walk(_ast.parse(src)):
        if not isinstance(fn, (_ast.FunctionDef, _ast.AsyncFunctionDef)) or fn.name == "_append_jsonl":
            continue
        for n in _ast.walk(fn):
            if not isinstance(n, _ast.Call):
                continue
            name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if name != "open":
                continue
            modes = [a for a in (n.args[1:2] if getattr(n.func, "id", None) else n.args[:1])]
            modes += [k.value for k in n.keywords if k.arg == "mode"]
            if any(isinstance(m, _ast.Constant) and isinstance(m.value, str) and "a" in m.value for m in modes):
                out.append(fn.name)
    return out


check("R-FSYNC: the proxy appends to a file only through _append_jsonl - calls, catcher, flags and ollama records all "
      "take the fsync path", _appends_outside((ROOT / "research" / "_llm_proxy.py").read_text(encoding="utf-8")) == [],
      str(_appends_outside((ROOT / "research" / "_llm_proxy.py").read_text(encoding="utf-8"))))

print("\n- B-RST: a local refusal is READ by the client - answer, shutdown, bounded drain, then close -")
T_CC = ST.TOKEN + "c"
_bup = Upstream()
_bcfg = P.ProxyConfig(arms=[P.ArmConfig(arm="nevertwice", mode="record", token=T_ARM, pinned_model="deepseek-flash"),
                            P.ArmConfig(arm="claude-code-memory", mode="record", token=T_CC, pinned_model="deepseek-flash",
                                        home_canary="a" * 32)],
                      run_dir=TMP / "brst", upstream_host="127.0.0.1", upstream_port=_bup.port, upstream_tls=False,
                      control_token="ctl-token")
_bpx = P.Proxy(_bcfg, P.read_key(KEYFILE), log=lambda m: None)
_bports = _bpx.start()
NW, CCP = _bports["arms"]["nevertwice"]["write"], _bports["arms"]["claude-code-memory"]["write"]


def refused_exchange(port: int, head: bytes, body: bytes, *, hold: bool = False):
    """Send a whole request, then read until end-of-file: (bytes read, send error, read error, seconds from the last
    byte sent to end-of-file, the socket - left open when ``hold``)."""
    s = socket.create_connection(("127.0.0.1", port))
    s.settimeout(10)
    send_err = read_err = None
    try:
        s.sendall(head + body)
    except OSError as e:
        send_err = repr(e)
    t_sent = time.monotonic()
    data = b""
    try:
        while True:
            c = s.recv(65536)
            if not c:
                break
            data += c
    except OSError as e:
        read_err = repr(e)
    took = time.monotonic() - t_sent
    if not hold:
        s.close()
    return data, send_err, read_err, took, s


def head_of(token: str, length: int, extra: str = "") -> bytes:
    return (f"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {token}\r\n{extra}"
            f"Content-Length: {length}\r\n\r\n").encode()


BIG = b"x" * 1_000_000
REPEATS = 20                                             # the auditor's probe: without the drain 17-20 of 20 were resets
CHUNKED = head_of(T_ARM, 0, "Transfer-Encoding: chunked\r\n").replace(b"Content-Length: 0\r\n", b"")
CASES = (("401 (a wrong token)", NW, head_of("wrong-token", len(BIG)), BIG, b"HTTP/1.1 401", "nevertwice", "refused_auth"),
         ("403 (no home canary)", CCP, head_of(T_CC, len(BIG)), BIG, b"HTTP/1.1 403", "claude-code-memory",
          "refused_home_canary"),
         ("411 (a chunked body)", NW, CHUNKED, b"f4240\r\n" + BIG + b"\r\n0\r\n\r\n", b"HTTP/1.1 411", "nevertwice",
          "refused_chunked"),
         ("404 (a path not forwarded)", NW, head_of(T_ARM, len(BIG)).replace(b"/v1/chat/completions", b"/u/r1.u1/admin/x"),
          BIG, b"HTTP/1.1 404", "nevertwice", "refused_path"))
for label, port, head, body, want, arm_, counter in CASES:
    before = dict(vars(_bpx.counters[arm_]))
    ups_before = sum(c.upstream_errors for c in _bpx.counters.values())
    got_codes = []
    for _ in range(REPEATS):
        data, se, re_, _t, _s = refused_exchange(port, head, body)
        got_codes.append(data[:12] if data.startswith(want) and se is None and re_ is None else (data[:12], se, re_))
    after = dict(vars(_bpx.counters[arm_]))
    ok_reads = sum(1 for g in got_codes if g == want)
    check(f"B-RST: {label} with a ~1 MB body - the client reads its code {REPEATS}/{REPEATS}",
          ok_reads == REPEATS, f"{ok_reads}/{REPEATS} {[g for g in got_codes if g != want][:2]}")
    check(f"B-RST: {label} - one refusal counted per request, no upstream error, no byte sent up",
          after[counter] - before[counter] == REPEATS and after["requests"] - before["requests"] == REPEATS
          and sum(c.upstream_errors for c in _bpx.counters.values()) == ups_before and after["bytes_up"] == before["bytes_up"],
          str({k: (before[k], after[k]) for k in (counter, "requests", "bytes_up")}))
OVER = b"q" * (P.MAX_BODY + 1)
d413, se, re_, _t, _s = refused_exchange(NW, head_of(T_ARM, len(OVER)), OVER)
check("B-RST: a body over MAX_BODY - the client reads its 413", d413.startswith(b"HTTP/1.1 413") and se is None
      and re_ is None, f"{d413[:40]!r} {se} {re_}")
_b1 = json.dumps({"model": "deepseek-flash", "messages": [{"role": "user", "content": "q"}]}).encode()
_pipe = head_of(T_ARM, len(_b1)) + _b1 + head_of(T_ARM, len(BIG)) + BIG     # a second request sent before the answer
_before_p = _bpx.counters["nevertwice"].refused_pipelined
_got_p = []
for _ in range(REPEATS):
    data, se, re_, _t, _s = refused_exchange(NW, _pipe, b"")
    _got_p.append(data[:12] if data.startswith(b"HTTP/1.1 400") and se is None and re_ is None else (data[:12], se, re_))
_ok_p = sum(1 for g in _got_p if g == b"HTTP/1.1 400")
check(f"B-RST (BRk): a pipelined second request of ~1 MB after a whole first one - the client reads its 400 "
      f"{REPEATS}/{REPEATS}: the drain reads to end-of-file, not to the first request's length",
      _ok_p == REPEATS and _bpx.counters["nevertwice"].refused_pipelined - _before_p == REPEATS,
      f"{_ok_p}/{REPEATS} {[g for g in _got_p if g != b'HTTP/1.1 400'][:2]}")


def guard_threads() -> set:
    """The proxy's live connection threads, as objects: a row waits for the ones ITS exchange started, never on a count
    that other rows' lingering threads confound."""
    return {th for th in threading.enumerate() if "_guard" in th.name}


DRAIN_PREREG = 2.0                                       # the auditor's ruling - never the module's (mutable) constant
check("B-RST: the drain's time limit is the ruled 2 s", P.DRAIN_S == DRAIN_PREREG, str(P.DRAIN_S))
base_threads = guard_threads()
dh, se, re_, took, held = refused_exchange(NW, head_of("wrong-token", 1000), b"y" * 10, hold=True)
t_hold = time.monotonic()
mine = guard_threads() - base_threads                      # this exchange's connection thread
while any(th.is_alive() for th in mine) and time.monotonic() - t_hold < 5:
    time.sleep(0.05)
thread_gone = time.monotonic() - t_hold
gone_while_held = not any(th.is_alive() for th in mine)    # read BEFORE the client lets go
held.close()
check("B-RST (BRc): a client that got its 401 and holds its socket, 990 body bytes owed and no end-of-file - it read "
      "the answer and end-of-file at once, and the proxy's connection thread ended within 2 s + e while it still held",
      dh.startswith(b"HTTP/1.1 401") and took < 1.0 and len(mine) == 1 and gone_while_held
      and thread_gone <= DRAIN_PREREG + 0.6,
      f"took={took:.2f} threads={len(mine)} gone={thread_gone:.2f} while_held={gone_while_held}")
_bpx.stop()
_bup.close()

_a, _b = socket.socketpair()


def _flood():
    try:
        _a.sendall(b"z" * (3 << 20))
    except OSError:
        pass


threading.Thread(target=_flood, daemon=True).start()
_got: list = []
_th = threading.Thread(target=lambda: _got.append(P._refuse(_b, 401, "Unauthorized",
                                                           headers=[("Content-Length", str(3 << 20))])), daemon=True)
_th.start()
_th.join(8)
_a.close()
_b.close()
check("B-RST: a 3 MiB body - the refusal drops exactly its Content-Length (no cap: nothing is buffered), then closes",
      _got == [3 << 20], str(_got))
_e, _f = socket.socketpair()


def _flood_close():
    """A client that sends its whole chunked body, reads the answer to its end, then closes - as a real one does
    (closing with the answer unread would itself be the RST this fixes)."""
    try:
        _e.sendall(b"w" * (2 << 20))
        while _e.recv(65536):
            pass
    except OSError:
        pass
    _e.close()


threading.Thread(target=_flood_close, daemon=True).start()
_got3: list = []
_th3 = threading.Thread(target=lambda: _got3.append(P._refuse(_f, 411, "Length Required",
                                                             headers=[("Transfer-Encoding", "chunked")])), daemon=True)
_th3.start()
_th3.join(8)
_f.close()
check("B-RST: a chunked body (no length) is dropped to end-of-file", _got3 == [2 << 20], str(_got3))
_src = (ROOT / "research" / "_llm_proxy.py").read_text(encoding="utf-8")
import ast as _ast2  # noqa: E402
_client_fn = next(n for n in _ast2.walk(_ast2.parse(_src)) if isinstance(n, _ast2.FunctionDef) and n.name == "_client")
_direct = [n.lineno for n in _ast2.walk(_client_fn) if isinstance(n, _ast2.Call)
           and getattr(n.func, "id", None) == "_send_local"]
check("B-RST: every local refusal of _client goes through _refuse - no _send_local past it", _direct == [], str(_direct))
_consumed = [n.lineno for n in _ast2.walk(_client_fn) if isinstance(n, _ast2.Delete) and len(n.targets) == 1
             and isinstance(n.targets[0], _ast2.Subscript) and getattr(n.targets[0].value, "id", None) == "buf"
             and isinstance(n.targets[0].slice, _ast2.Slice) and getattr(n.targets[0].slice.upper, "id", None) == "length"]
_refs = sorted((n.lineno, any(k.arg == "headers" for k in n.keywords)) for n in _ast2.walk(_client_fn)
               if isinstance(n, _ast2.Call) and getattr(n.func, "id", None) == "_refuse")
_cut = _consumed[0] if len(_consumed) == 1 else None
check("B-RST (BRl, BRm): in _client every _refuse before the body is consumed (del buf[:length]) passes the headers, "
      "and none after it does - a refusal after the body drains to end-of-file, whatever came in after the check",
      _cut is not None and any(ln < _cut for ln, _h in _refs) and any(ln > _cut for ln, _h in _refs)
      and all(h_ == (ln < _cut) for ln, h_ in _refs), f"consumed at {_consumed}; refusals (line, headers=) {_refs}")
_c, _d = socket.socketpair()
_got2: list = []
_t0 = time.monotonic()
_th2 = threading.Thread(target=lambda: _got2.append((P._refuse(_d, 401, "Unauthorized",
                                                               headers=[("Content-Length", "100")]),
                                                     time.monotonic() - _t0)), daemon=True)
_th2.start()
_th2.join(6)
_alive2 = _th2.is_alive()                                  # read BEFORE the peer closes
_c.close()
_d.close()
check("B-RST: a client that sends nothing more and never closes - the drain gives up after 2 s",
      not _alive2 and len(_got2) == 1 and _got2[0][0] == 0 and 1.5 <= _got2[0][1] <= DRAIN_PREREG + 0.6,
      f"alive={_alive2} {_got2}")
_g, _h = socket.socketpair()
_got4: list = []
_t4 = time.monotonic()
_g.sendall(b"v" * 900)                                     # the rest of a 1000-byte body; 100 came with the head
_th4 = threading.Thread(target=lambda: _got4.append((P._refuse(_h, 401, "Unauthorized",
                                                               headers=[("Content-Length", "1000")], buffered=100),
                                                     time.monotonic() - _t4)), daemon=True)
_th4.start()
_th4.join(6)
_alive4 = _th4.is_alive()
_g.close()
_h.close()
check("B-RST (BRf): head and part of the body came in one packet, the rest was sent, the client holds - _refuse drops "
      "exactly Content-Length - buffered (900) and returns at once, not at the time limit",
      not _alive4 and len(_got4) == 1 and _got4[0][0] == 900 and _got4[0][1] < 1.0, f"alive={_alive4} {_got4}")
_e1, _e2 = socket.socketpair()
_got5: list = []
_t5 = time.monotonic()
_e1.sendall(b"w" * 500)                                    # bytes past a request whose body was read, then end-of-file
_e1.shutdown(socket.SHUT_WR)
_th5 = threading.Thread(target=lambda: _got5.append((P._refuse(_e2, 502, "Bad Gateway", b"upstream unreachable"),
                                                     time.monotonic() - _t5)), daemon=True)
_th5.start()
_th5.join(6)
_alive5 = _th5.is_alive()
_e1.close()
_e2.close()
check("B-RST (BRe): a refusal after the body was read (no headers given) drains to end-of-file - all 500 bytes the "
      "client sent past it, at once - never a length of 0 that leaves them unread for the close's RST",
      not _alive5 and len(_got5) == 1 and _got5[0][0] == 500 and _got5[0][1] < 1.0, f"alive={_alive5} {_got5}")

_probe_up = Upstream()
_probe_port = _probe_up.port
_probe_up.close()
try:
    _cc = socket.create_connection(("127.0.0.1", _probe_port), timeout=5)
    _cc.close()
    _dead_state = "connected"
except OSError as e:
    _dead_state = type(e).__name__
check("B-DEADPORT: after FakeUpstream.close() a connect to its port does not connect - on Linux a close alone leaves "
      "the thread in accept() listening, and a 'closed' upstream answers", _dead_state != "connected", _dead_state)
_dead = socket.socket()                                    # BRh's dead upstream: a port held, never listening -
_dead.bind(("127.0.0.1", 0))                               # a connect to it is refused on every platform
_dead_port = _dead.getsockname()[1]
_hcfg = P.ProxyConfig(arms=[P.ArmConfig(arm="nevertwice", mode="record", token=T_ARM, pinned_model="deepseek-flash")],
                      run_dir=TMP / "brh", upstream_host="127.0.0.1", upstream_port=_dead_port, upstream_tls=False,
                      control_token="ctl-token")
_hpx = P.Proxy(_hcfg, P.read_key(KEYFILE), log=lambda m: None)
_hports = _hpx.start()
_body = json.dumps({"model": "deepseek-flash", "messages": [{"role": "user", "content": "q"}]}).encode()
_base502 = guard_threads()
d502, se, re_, took502, held502 = refused_exchange(_hports["arms"]["nevertwice"]["write"],
                                                   head_of(T_ARM, len(_body)), _body, hold=True)
t_resp = time.time()                                       # the 502 and end-of-file are in: the drain runs on
_mine502 = guard_threads() - _base502
time.sleep(0.3)
_calls = [json.loads(x) for x in (TMP / "brh" / "calls.jsonl").read_text(encoding="utf-8").splitlines()] \
    if (TMP / "brh" / "calls.jsonl").exists() else []
_draining = len(_mine502) == 1 and all(th.is_alive() for th in _mine502)   # the client holds: the drain waits on it
held502.close()
_t_close = time.monotonic()
while any(th.is_alive() for th in _mine502) and time.monotonic() - _t_close < 5:
    time.sleep(0.05)
_ended502 = time.monotonic() - _t_close
_hpx.stop()
_dead.close()


def _secs(iso_: str) -> float:
    import datetime as _dt  # noqa: PLC0415
    return _dt.datetime.fromisoformat(iso_.replace("Z", "+00:00")).timestamp()


_c502 = _calls[-1] if _calls else {}
check("B-RST (BRh): upstream unreachable, the client holds its socket - its 502 comes at once, and the call's record is "
      "already written while the drain still runs, its t1 the failure's (no later than the 502), not the drain's end",
      d502.startswith(b"HTTP/1.1 502") and _c502.get("upstream_error") and _c502.get("t1")
      and _secs(_c502["t1"]) <= t_resp + 0.3, f"{d502[:20]!r} t_resp={t_resp:.2f} {_c502.get('t1')} {_c502.get('upstream_error')} took={took502:.2f}")
check("B-RST (BRh): ... the proxy drains that held socket to end-of-file - its connection thread still runs 0.3 s after "
      "the 502 and ends once the client closes, well inside the 2 s limit",
      _draining and _ended502 < 1.0, f"draining={_draining} threads={len(_mine502)} ended_after_close={_ended502:.2f}")

print("\n- R-TOOLS: an arm with write_port false has no write port - its catcher and reader only -")
_up = Upstream()
_cfg = P.ProxyConfig(arms=[P.ArmConfig(arm="bm25-floor", mode="record", token=T_ARM, reader_model="deepseek-flash",
                                       write_port=False),
                           P.ArmConfig(arm="nevertwice", mode="record", token=T_ARM, pinned_model="deepseek-flash")],
                     run_dir=TMP / "wp", upstream_host="127.0.0.1", upstream_port=_up.port, upstream_tls=False,
                     control_token="ctl-token")
_px = P.Proxy(_cfg, P.read_key(KEYFILE), log=lambda m: None)
_ports = _px.start()
_px.stop()
_up.close()
check("R-TOOLS: write_port false - catcher and reader ports, no write port; another arm keeps its write port",
      set(_ports["arms"]["bm25-floor"]) == {"catcher", "reader"}
      and {"write", "catcher"} <= set(_ports["arms"]["nevertwice"]), str(_ports))
(TMP / "wp_cfg.json").write_text(json.dumps({"arms": [{"arm": "bm25-floor", "write_port": False}, {"arm": "x1"}],
                                             "run_dir": str(TMP / "wp2")}), encoding="utf-8")
_loaded = P.ProxyConfig.load(TMP / "wp_cfg.json", {}, test_upstream_ok=True)
check("R-TOOLS: the config file carries write_port (false read as false, absent means true)",
      [a.write_port for a in _loaded.arms] == [False, True], str([a.write_port for a in _loaded.arms]))

print("\n- Q-AB-2: the proxy's own hop - one path in both modes, bounded, the upstream's wait and the connect excluded -")


class HopUpstream(Upstream):
    """/v1/chat/completions/wait answers after 0.3 s: the upstream's own time, which the own hop must not hold."""

    def _serve(self, c, path):
        if path.startswith(b"/v1/chat/completions/wait"):
            time.sleep(0.3)
        return super()._serve(c, path)


T_RAW = ST.TOKEN + "r"
HOP_BODY = {"model": "deepseek-flash", "messages": []}


def hop_proxy(run: str):
    """One process, the A/B's two ArmConfigs of one product arm: the same but for mode, name, token and ports."""
    up = HopUpstream()
    arms = [P.ArmConfig(arm="mem0", mode="record", token=T_ARM, pinned_model="deepseek-flash"),
            P.ArmConfig(arm="mem0-raw", mode="raw", token=T_RAW, pinned_model="deepseek-flash")]
    cfg = P.ProxyConfig(arms=arms, run_dir=TMP / run, upstream_host="127.0.0.1", upstream_port=up.port,
                        upstream_tls=False, control_token="ctl-token")
    px = P.Proxy(cfg, P.read_key(KEYFILE), log=lambda m: None)
    return px, px.start(), up


def hop_calls(px, ports, path: str = "/u/u1/v1/chat/completions", n: int = 1) -> dict:
    """n calls on each of the two arms; each arm's replies, in order."""
    got = {}
    for arm, tok in (("mem0", T_ARM), ("mem0-raw", T_RAW)):
        got[arm] = [call(ports["arms"][arm]["write"], path, HOP_BODY, token=tok) for _ in range(n)]
    return got


def samples(px, arm: str) -> list:
    return list(getattr(px.counters[arm], "own_hop_ms", None) or [])


hp, hports, hup = hop_proxy("hop")
hgot = hop_calls(hp, hports, n=3)
check("Q-AB-2 (2): every forwarded call adds one own-hop sample, raw and record alike - one path, not two",
      len(samples(hp, "mem0")) == 3 and len(samples(hp, "mem0-raw")) == 3
      and getattr(hp.counters["mem0"], "own_hop_dropped", None) == 0
      and getattr(hp.counters["mem0-raw"], "own_hop_dropped", None) == 0,
      f"{samples(hp, 'mem0')} {samples(hp, 'mem0-raw')}")
n_sent = len(hup.sent)
hraw = call(hports["arms"]["mem0-raw"]["write"], "/u/u1/v1/chat/completions", HOP_BODY, token=T_RAW)
check("Q-AB-2 (1): with the own hop measured, a raw arm's client gets the upstream's reply byte for byte",
      len(hup.sent) == n_sent + 1 and hraw == hup.sent[-1], f"{hraw[:60]!r} vs {hup.sent[-1][:60]!r}")
hrec = call(hports["arms"]["mem0"]["write"], "/u/u1/v1/chat/completions", HOP_BODY, token=T_ARM)
check("... and so does a recording arm's client", len(hup.sent) == n_sent + 2 and hrec == hup.sent[-1])
hc = json.loads(call(hports["control"], "/counters", {}, token="ctl-token").partition(b"\r\n\r\n")[2] or b"{}")
check("Q-AB-2: /counters carries each arm's own-hop samples and the count dropped past the cap",
      all(len(hc.get(a, {}).get("own_hop_ms") or []) == 4 and hc.get(a, {}).get("own_hop_dropped") == 0
          for a in ("mem0", "mem0-raw")), str({a: sorted(v)[:4] for a, v in hc.items()}))
k0 = {a: len(samples(hp, a)) for a in ("mem0", "mem0-raw")}
t_w = time.monotonic()
hop_calls(hp, hports, path="/u/u1/v1/chat/completions/wait")
waited = time.monotonic() - t_w
new = {a: samples(hp, a)[k0[a]:] for a in k0}
check("Q-AB-2: the upstream's own wait (0.3 s) is not in the own hop, in either mode",
      waited >= 0.6 and all(len(v) == 1 and v[0] < 150 for v in new.values()), f"{waited:.2f}s {new}")
_orig_up = hp._upstream


def _slow_upstream():
    time.sleep(0.3)
    return _orig_up()


hp._upstream = _slow_upstream
k0 = {a: len(samples(hp, a)) for a in ("mem0", "mem0-raw")}
hop_calls(hp, hports)
new = {a: samples(hp, a)[k0[a]:] for a in k0}
check("Q-AB-2: the upstream connect (0.3 s) is not in the own hop, in either mode",
      all(len(v) == 1 and v[0] < 150 for v in new.values()), str(new))
hp._upstream = _orig_up
_orig_fb = hp._fallback


def _slow_fallback(*a, **kw):
    time.sleep(0.2)
    return _orig_fb(*a, **kw)


hp._fallback = _slow_fallback
k0 = {a: len(samples(hp, a)) for a in ("mem0", "mem0-raw")}
hop_calls(hp, hports)
new = {a: samples(hp, a)[k0[a]:] for a in k0}
check("Q-AB-2: time the proxy spends between the request read and the upstream send is in the own hop, both modes",
      all(len(v) == 1 and v[0] >= 200 for v in new.values()), str(new))
hp._fallback = _orig_fb


class _ScriptedClock:
    """time, but perf_counter returns the script: request read, connect start, connect end, upstream send done,
    first upstream byte, first client byte sent - one fresh connection's six boundaries."""

    def __init__(self, script):
        self.script = list(script)

    def perf_counter(self):
        return self.script.pop(0)

    def __getattr__(self, name):
        return getattr(time, name)


for arm, tok in (("mem0", T_ARM), ("mem0-raw", T_RAW)):
    k0 = len(samples(hp, arm))
    P.time = _ScriptedClock([10.000, 10.100, 10.400, 10.410, 11.000, 11.004])
    try:
        call(hports["arms"][arm]["write"], "/u/u1/v1/chat/completions", HOP_BODY, token=tok)
    finally:
        P.time = time
    new = samples(hp, arm)[k0:]
    check(f"Q-AB-2: {arm}: own hop = (send done - read) - connect + (first byte sent - first byte in) = 114 ms",
          new == [114.0], str(new))
hp.stop()
hup.close()

_cap = getattr(P, "OWN_HOP_CAP", None)
check("Q-AB-2 (4): the sample list has a named cap", isinstance(_cap, int) and _cap >= 1000, str(_cap))
hp, hports, hup = hop_proxy("hop_cap")
P.OWN_HOP_CAP = 2
try:
    hop_calls(hp, hports, n=5)
finally:
    P.OWN_HOP_CAP = _cap
check("Q-AB-2 (4): past the cap no sample is kept, and each one dropped is counted",
      all(len(samples(hp, a)) == 2 and getattr(hp.counters[a], "own_hop_dropped", None) == 3 for a in ("mem0", "mem0-raw")),
      str({a: (samples(hp, a), getattr(hp.counters[a], "own_hop_dropped", None)) for a in ("mem0", "mem0-raw")}))
hp.stop()
hup.close()

hp, hports, hup = hop_proxy("hop_raw_only")
for _ in range(2):
    call(hports["arms"]["mem0-raw"]["write"], "/u/u1/v1/chat/completions", HOP_BODY, token=T_RAW)
written = sorted(str(f.relative_to(TMP / "hop_raw_only")) for f in (TMP / "hop_raw_only").rglob("*"))
check("T6 at the proxy: a raw arm's calls write nothing in the run directory - no calls.jsonl, bodies/ or flags.jsonl",
      len(samples(hp, "mem0-raw")) == 2 and written == [], str(written))
hp.stop()
hup.close()

print("\n- D-AB-8: the upstream's statuses, counted in memory in both modes (the A/B's 401/402/403 stop) -")


class PayUpstream(HopUpstream):
    """/v1/chat/completions/pay answers 402, as DeepSeek does when the balance is gone; /forbidden 403, /key 401."""

    def _serve(self, c, path):
        if path.startswith(b"/v1/chat/completions/close"):   # HOP-1: the upstream closes before its first byte
            return False
        for tail, code in ((b"/pay", b"402 Payment Required"), (b"/forbidden", b"403 Forbidden"), (b"/key", b"401 Unauthorized")):
            if path.startswith(b"/v1/chat/completions" + tail):
                err = b'{"error":{"message":"no"}}'
                self._send(c, b"HTTP/1.1 " + code + b"\r\nContent-Type: application/json\r\nContent-Length: "
                           + str(len(err)).encode() + b"\r\n\r\n" + err)
                return True
        return super()._serve(c, path)


pu = PayUpstream()
_arms = [P.ArmConfig(arm="mem0", mode="record", token=T_ARM, pinned_model="deepseek-flash"),
         P.ArmConfig(arm="mem0-raw", mode="raw", token=T_RAW, pinned_model="deepseek-flash")]
pp = P.Proxy(P.ProxyConfig(arms=_arms, run_dir=TMP / "pay", upstream_host="127.0.0.1", upstream_port=pu.port,
                           upstream_tls=False, control_token="ctl-token"), P.read_key(KEYFILE), log=lambda m: None)
pports = pp.start()
for arm, tok in (("mem0", T_ARM), ("mem0-raw", T_RAW)):
    for tail in ("", "/pay", "/forbidden", "/key"):
        call(pports["arms"][arm]["write"], "/u/u1/v1/chat/completions" + tail, HOP_BODY, token=tok)
st = {a: dict(getattr(pp.counters[a], "upstream_statuses", None) or {}) for a in ("mem0", "mem0-raw")}
check("D-AB-8: every upstream reply's status is counted in memory, raw and record alike - 402, 403 and 401 included "
      "(the proxy's own refusals are not the upstream's)",
      all(st[a] == {"200": 1, "402": 1, "403": 1, "401": 1} for a in st), str(st))
pc_ = json.loads(call(pports["control"], "/counters", {}, token="ctl-token").partition(b"\r\n\r\n")[2] or b"{}")
check("... and /counters carries them",
      all((pc_.get(a) or {}).get("upstream_statuses") == {"200": 1, "402": 1, "403": 1, "401": 1} for a in st),
      str({a: (pc_.get(a) or {}).get("upstream_statuses") for a in st}))
call(pports["arms"]["mem0-raw"]["write"], "/u/u1/v1/chat/completions", HOP_BODY, token="nvt3-wrong-token")
check("... a request the proxy itself refused (401, no upstream) is not an upstream status",
      dict(getattr(pp.counters["mem0-raw"], "upstream_statuses", None) or {}).get("401") == 1
      and pp.counters["mem0-raw"].refused_auth == 1)
before = {a: (len(pp.counters[a].own_hop_ms), pp.counters[a].upstream_errors) for a in st}
for arm, tok in (("mem0", T_ARM), ("mem0-raw", T_RAW)):
    call(pports["arms"][arm]["write"], "/u/u1/v1/chat/completions/close", HOP_BODY, token=tok)
after = {a: (len(pp.counters[a].own_hop_ms), pp.counters[a].upstream_errors) for a in st}
check("HOP-1 (the auditor): an upstream that closes before its first byte adds no own-hop sample, raw and record alike "
      "- it is an upstream error, never a hop",
      all(after[a][0] == before[a][0] and after[a][1] == before[a][1] + 1 for a in st), f"{before} -> {after}")
snap_fn = getattr(pp, "counters_snapshot", None)
snap = snap_fn() if callable(snap_fn) else {}
live_st, live_hop = pp.counters["mem0"].upstream_statuses, pp.counters["mem0"].own_hop_ms
frozen = json.dumps(snap, sort_keys=True)
call(pports["arms"]["mem0"]["write"], "/u/u1/v1/chat/completions/pay", HOP_BODY, token=T_ARM)
call(pports["arms"]["mem0"]["write"], "/u/u1/v1/chat/completions", HOP_BODY, token=T_ARM)
check("HOP-2 (the auditor's O-a): Proxy.counters_snapshot() copies every container under the lock - its dicts and lists "
      "are not the live ones, and later calls do not change it (so /counters' json.dumps never walks a live dict)",
      snap and snap["mem0"]["upstream_statuses"] is not live_st and snap["mem0"]["own_hop_ms"] is not live_hop
      and json.dumps(snap, sort_keys=True) == frozen and snap["mem0"]["upstream_statuses"] != dict(live_st),
      str(snap.get("mem0", {}).get("upstream_statuses")))
pc2 = json.loads(call(pports["control"], "/counters", {}, token="ctl-token").partition(b"\r\n\r\n")[2] or b"{}")
check("HOP-2: /counters answers with that snapshot", pc2.get("mem0", {}).get("upstream_statuses")
      == dict(pp.counters["mem0"].upstream_statuses), str(pc2.get("mem0", {}).get("upstream_statuses")))
got_snap: list = []
with pp._lock:                                         # HOP-5: the snapshot waits for the lock the writers hold
    th = threading.Thread(target=lambda: got_snap.append(pp.counters_snapshot()), daemon=True)
    th.start()
    th.join(0.2)
    waited = th.is_alive() and not got_snap
th.join(5)
check("HOP-5 (the auditor): counters_snapshot takes the proxy's lock - it waits while the lock is held, and returns once "
      "it is released", waited and len(got_snap) == 1, f"waited={waited} got={len(got_snap)}")
SENTINEL = {"sentinel-arm": {"requests": 424242}}
pp.counters_snapshot = lambda: SENTINEL
pc3 = json.loads(call(pports["control"], "/counters", {}, token="ctl-token").partition(b"\r\n\r\n")[2] or b"{}")
del pp.counters_snapshot
check("HOP-6 (the auditor): /counters answers exactly what counters_snapshot() returns", pc3 == SENTINEL, str(pc3)[:200])
pp.stop()
pu.close()

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nproxy recording: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
