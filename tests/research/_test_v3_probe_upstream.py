#!/usr/bin/env python3
"""PREREG-V3 A8 C5a-2b (the auditor's Q-C5-3): research/v3/probe_upstream.py's fakes - spawns NO child: both fakes and
the real proxy (research/_llm_proxy.py, in record mode, with a sentinel key file outside any secrets root) run as
threads of this process on 127.0.0.1:

* the fake DeepSeek: call i answers the declared script's entry min(i, last) with a usage distinct per call (prompt
  1000+i, completion 10+i); JSON, or SSE (chunked, the usage in the last chunk, then [DONE]) when the request streams;
  tool_calls when the entry has them; any other path, or a body that is not JSON, is 404; the script's sha256 is over
  its canonical JSON;
* the fake Ollama: exactly /api/embed, /api/embeddings, /v1/embeddings, /api/tags, /api/show, with the declared tag,
  digest and 1024 dimensions (the same text, the same vector); another model is 404; /api/pull and /api/generate are
  traps - recorded, 403; another path is recorded, 404;
* through the real proxy: a JSON and a streamed call on /u/<run>.<unit>/ are recorded with the fake's usage, so
  probe_a8.m0_usage passes on them; an embed on the arm's Ollama port is recorded with its unit (M21: the route went
  through the /u/ leg), and a trap through the leg reaches the client as 403 and the fake's trap list.

    python tests/research/_test_v3_probe_upstream.py
"""
from __future__ import annotations

import atexit
import hashlib
import http.client
import importlib.util
import json
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


U = _load("v3_probe_upstream_t", ROOT / "research" / "v3" / "probe_upstream.py")
PA = _load("v3_probe_a8_pu", ROOT / "research" / "v3" / "probe_a8.py")
PX = _load("v3_llm_proxy_pu", ROOT / "research" / "_llm_proxy.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


RAISED: list = []


def ok(fn) -> bool:
    """A row's condition, evaluated so that a raise FAILs that row by its name (and the closing row lists it) - the
    suite never crashes, whatever shape a mutated fake answers in (C4A-8's lesson)."""
    try:
        return bool(fn())
    except Exception as e:  # noqa: BLE001 - the row reads it
        RAISED.append(f"{type(e).__name__}: {e}")
        return False


def req(port: int, method: str, path: str, obj=None, *, token: str | None = None, raw: bytes | None = None):
    """(status, content-type, body bytes); never raises - a failure is (None, None, repr)."""
    body = raw if raw is not None else (json.dumps(obj).encode() if obj is not None else b"")
    headers = {"Content-Type": "application/json", "Connection": "close", "Content-Length": str(len(body))}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        data = r.read()
        out = (r.status, r.getheader("Content-Type"), data)
        c.close()
        return out
    except Exception as e:  # noqa: BLE001 - a row reads it
        return None, None, repr(e).encode()


def sse(data: bytes) -> list:
    """The data: payloads of an SSE body, [DONE] kept as the string."""
    out = []
    for block in data.decode("utf-8", "replace").split("\n\n"):
        for ln in block.splitlines():
            if ln.startswith("data: "):
                p = ln[6:]
                out.append(p if p == "[DONE]" else json.loads(p))
    return out


TMP = Path(tempfile.mkdtemp(prefix="nvt3_probe_upstream_"))
atexit.register(shutil.rmtree, TMP, True)     # a crashed run leaves nothing behind
SCRIPT = [{"content": '{"memory": [{"text": "Alice lives in Paris"}]}'},
          {"content": '{"memory": []}'},
          {"content": "", "tool_calls": [{"id": "t1", "type": "function",
                                          "function": {"name": "send_message", "arguments": "{\"message\": \"hi\"}"}}]}]
MSG = {"model": "deepseek-flash", "messages": [{"role": "user", "content": "x"}]}

print("- the fake DeepSeek -")
ds = U.FakeDeepSeek(SCRIPT)
st, ct, b = req(ds.port, "POST", "/chat/completions", MSG)
a0 = json.loads(b) if st == 200 else {}
st1, _, b1 = req(ds.port, "POST", "/v1/chat/completions", MSG)
a1 = json.loads(b1) if st1 == 200 else {}
check("two calls: the script's entries in order, each with its own usage (prompt 1000+i, completion 10+i), the model "
      "echoed", ok(lambda: st == 200 and ct == "application/json"
      and a0["choices"][0]["message"]["content"] == SCRIPT[0]["content"] and a0["usage"]["prompt_tokens"] == 1000
      and a0["usage"]["completion_tokens"] == 10 and a0["model"] == "deepseek-flash"
      and a1["choices"][0]["message"]["content"] == SCRIPT[1]["content"] and a1["usage"]["prompt_tokens"] == 1001
      and a1["usage"]["completion_tokens"] == 11), f"{st} {b[:120]!r}")
st2, _, b2 = req(ds.port, "POST", "/chat/completions", {**MSG, "stream": True})
ev = sse(b2) if st2 == 200 else []
content = "".join((e["choices"][0]["delta"].get("content") or "") for e in ev if isinstance(e, dict) and e.get("choices"))
check("a streamed call: SSE, the content in the deltas, the usage (1002/12) in the last chunk before [DONE]",
      ok(lambda: st2 == 200 and ev and ev[-1] == "[DONE]" and content == SCRIPT[2]["content"]
      and ev[-2].get("usage", {}).get("prompt_tokens") == 1002 and ev[-2]["usage"]["completion_tokens"] == 12
      and ev[-2]["choices"][0]["finish_reason"] == "tool_calls"
      and ev[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "send_message"), str(ev)[:300])
st3, _, b3 = req(ds.port, "POST", "/chat/completions", MSG)
a3 = json.loads(b3) if st3 == 200 else {}
check("past the script's end the last entry answers again, tool_calls and all, finish_reason tool_calls",
      ok(lambda: a3.get("choices", [{}])[0].get("message", {}).get("tool_calls", [{}])[0].get("id") == "t1"
      and a3["choices"][0]["finish_reason"] == "tool_calls" and a3["usage"]["prompt_tokens"] == 1003), str(a3)[:200])
check("every request is recorded: its path, its body and whether it streamed",
      ok(lambda: [(r["path"], r["stream"]) for r in ds.requests] == [("/chat/completions", False), ("/v1/chat/completions", False),
                                                           ("/chat/completions", True), ("/chat/completions", False)]
      and ds.requests[0]["body"] == MSG), str(ds.requests)[:200])
st4, _, _ = req(ds.port, "POST", "/api/generate", MSG)
st5, _, _ = req(ds.port, "POST", "/chat/completions", raw=b"not json")
check("another path, or a body that is not JSON, is 404", ok(lambda: st4 == 404 and st5 == 404), f"{st4} {st5}")
check("the script's sha256 is over its canonical JSON (sort_keys, no whitespace)",
      ok(lambda: ds.script_sha256 == hashlib.sha256(json.dumps(SCRIPT, sort_keys=True, separators=(",", ":")).encode()).hexdigest()))
try:
    U.FakeDeepSeek([])
    empty = "accepted"
except ValueError as e:
    empty = str(e)
check("an empty script is refused", ok(lambda: "at least one" in empty), empty)
check("the fakes listen on 127.0.0.1 only", ok(lambda: ds.host == "127.0.0.1"))

print("\n- the fake Ollama -")
TAG, DIG = "nvt3-bge-m3-d1:latest", "f" * 64
ol = U.FakeOllama(tag=TAG, digest=DIG)
st, _, b = req(ol.port, "GET", "/api/tags")
tags = json.loads(b) if st == 200 else {}
check("/api/tags lists exactly the declared tag and digest", ok(lambda: st == 200 and [(m["name"], m["digest"]) for m in tags["models"]]
      == [(TAG, DIG)]), str(tags)[:200])
st, _, b = req(ol.port, "POST", "/api/show", {"model": TAG})
check("/api/show gives 1024 dimensions", ok(lambda: st == 200 and json.loads(b)["model_info"]["bert.embedding_length"] == 1024), str(b[:120]))
st, _, b = req(ol.port, "POST", "/api/embed", {"model": TAG, "input": ["alpha beta", "gamma"]})
e = json.loads(b) if st == 200 else {}
st_, _, b_ = req(ol.port, "POST", "/api/embed", {"model": "nvt3-bge-m3-d1", "input": "alpha beta"})
e_ = json.loads(b_) if st_ == 200 else {}
check("/api/embed: one 1024-dim unit vector per input, the same text the same vector, another text another; its "
      "prompt_eval_count; the tag's base name is the tag", ok(lambda: st == 200 and len(e["embeddings"]) == 2
      and all(len(v) == 1024 for v in e["embeddings"]) and e_["embeddings"][0] == e["embeddings"][0]
      and e["embeddings"][0] != e["embeddings"][1] and abs(sum(x * x for x in e["embeddings"][0]) - 1) < 1e-4
      and isinstance(e["prompt_eval_count"], int) and e["prompt_eval_count"] > 0), str(b[:120]))
st, _, b = req(ol.port, "POST", "/api/embeddings", {"model": TAG, "prompt": "alpha beta"})
check("/api/embeddings (legacy): the same vector for the same text", ok(lambda: st == 200
      and json.loads(b)["embedding"] == e["embeddings"][0]), str(b[:80]))
st, _, b = req(ol.port, "POST", "/v1/embeddings", {"model": TAG, "input": ["gamma"]})
check("/v1/embeddings: the OpenAI shape with the same vector", ok(lambda: st == 200
      and json.loads(b)["data"][0]["embedding"] == e["embeddings"][1]), str(b[:80]))
check("U15 (the auditor): /v1/embeddings carries its usage - prompt_tokens == total_tokens == the count (a reader "
      "that silently took 0 would show here)", ok(lambda: json.loads(b)["usage"] == {"prompt_tokens": 3, "total_tokens": 3}),
      str(b[-80:]))
st, _, _ = req(ol.port, "POST", "/api/embed", {"model": "bge-m3", "input": "x"})
stn, _, _ = req(ol.port, "POST", "/api/embed", {"input": "x"})
check("another model, or none, is 404", ok(lambda: st == 404 and stn == 404), f"{st} {stn}")
stp, _, _ = req(ol.port, "POST", "/api/pull", {"model": TAG})
stg, _, _ = req(ol.port, "POST", "/api/generate", {"model": TAG, "prompt": "x"})
stu, _, _ = req(ol.port, "POST", "/api/chat", {"model": TAG})
check("/api/pull and /api/generate are traps: 403 and recorded; /api/chat is not served: 404, recorded apart",
      ok(lambda: stp == 403 and stg == 403 and stu == 404 and [t["path"] for t in ol.traps] == ["/api/pull", "/api/generate"]
      and [t["path"] for t in ol.unknown] == ["/api/chat"]), f"{stp} {stg} {stu} {ol.traps} {ol.unknown}")
ol2 = U.FakeOllama(tag="nvt3-bge-m3-d1", digest=DIG)
st, _, _ = req(ol2.port, "POST", "/api/embed", {"input": "x"})
check("a tag with no :latest takes exactly its own name - a request with no model is 404", ok(lambda: st == 404), str(st))
ol2.close()

print("\n- through the real proxy: record mode, the /u/ prefix, the Ollama leg -")
keyfile = TMP / "sentinel.env"
keyfile.write_bytes(b"DEEPSEEK_API_KEY=nvt3-probe-KEYSENTINEL-0000\n")
ds2 = U.FakeDeepSeek(SCRIPT[:2])
TOKEN = "nvt3-probe-armtoken-" + "0" * 32
cfg = PX.ProxyConfig(arms=[PX.ArmConfig(arm="mem0", mode="record", token=TOKEN, pinned_model="deepseek-flash",
                                        ollama_leg=True)],
                     run_dir=TMP / "proxy", upstream_host="127.0.0.1", upstream_port=ds2.port, upstream_tls=False,
                     control_token="ctl-token", ollama_mode="observe", ollama_upstream=("127.0.0.1", ol.port))
px = PX.Proxy(cfg, PX.read_key(keyfile), log=lambda m: None)
ports = px.start()
W, OP = ports["arms"]["mem0"]["write"], ports["arms"]["mem0"]["ollama"]
sa, _, ba = req(W, "POST", "/u/r1.u1/chat/completions", MSG, token=TOKEN)
sb, _, bb = req(W, "POST", "/u/r1.u1/v1/chat/completions", {**MSG, "stream": True}, token=TOKEN)
se, _, be = req(OP, "POST", "/u/r1.u1/api/embed", {"model": TAG, "input": ["alpha beta"]})
sp, _, _ = req(OP, "POST", "/u/r1.u1/api/pull", {"model": TAG})
px.stop(drain_s=0.5)
log = PA_log = None
try:
    AC = _load("v3_accounting_pu", ROOT / "research" / "v3" / "accounting.py")
    log = AC.load_proxy(TMP / "proxy")
except Exception as ex:  # noqa: BLE001 - a row reads it
    log = None
    print(f"  (accounting could not load the proxy's records: {ex!r})")
calls = list(getattr(log, "calls", []) or [])
mine = PA.mine(calls, arm="mem0", run="r1", unit="u1")
check("the proxy recorded both calls on r1.u1 with the fake's usage - JSON 1000/10, streamed 1001/11",
      ok(lambda: sa == 200 and sb == 200 and [(c.get("stream"), (c.get("usage") or {}).get("prompt"), (c.get("usage") or {}).get("completion"))
                                   for c in mine] == [(None, 1000, 10), (True, 1001, 11)]), str(mine)[:400])
u = PA.m0_usage({"calls": 2, "failed": 0, "no_usage": 0, "prompt_tokens": 2001, "completion_tokens": 21}, calls,
                run="r1", unit="u1")
check("probe_a8.m0_usage passes on the proxy's own record of the fake's answers", ok(lambda: u["ok"] is True), str(u))
emb = [r for r in (getattr(log, "ollama", []) or []) if r.get("arm") == "mem0"]
check("M21 (Q-C5-3): the embed went through the /u/ leg - the proxy's Ollama record carries arm and unit, answered 200",
      ok(lambda: se == 200 and any(r.get("unit") == "r1.u1" and r.get("is_embed") and r.get("status") == 200 for r in emb)), str(emb)[:300])
check("a trap reached through the leg is 403 at the client and in the fake's trap list", ok(lambda: sp == 403
      and ol.traps[-1]["path"] == "/api/pull" and len(ol.traps) == 3), f"{sp} {ol.traps}")
check("the fake upstream saw the proxy's sentinel key, never the arm's token", ok(lambda: all(
      (r.get("authorization") or "") == "Bearer nvt3-probe-KEYSENTINEL-0000" for r in ds2.requests) and len(ds2.requests) == 2),
      str([r.get("authorization") for r in ds2.requests]))
print("\n- Q-DRV-4: build_config's test_ollama_upstream, symmetric with test_upstream -")
RP = _load("v3_run_proxy_pu", ROOT / "research" / "v3" / "run_v3_proxy.py")
MEM0 = {"mem0": {"llm": "deepseek-flash", "llm_transport": "cloud:deepseek", "embeds_via_ollama": True}}
cfgq = RP.build_config(MEM0, run_dir=TMP / "q4", test_upstream={"host": "127.0.0.1", "port": 9, "tls": False},
                       test_ollama_upstream=("127.0.0.1", 11500))
check("the parameter puts the Ollama leg's upstream in the config as 127.0.0.1 and its port",
      ok(lambda: cfgq["ollama"]["upstream"] == ["127.0.0.1", 11500]), str(cfgq.get("ollama")))
(TMP / "q4").mkdir()
(TMP / "q4" / "c.json").write_text(json.dumps(cfgq), encoding="utf-8")
secq = RP.build_secrets(["mem0"])


(TMP / "q4" / "o.json").write_text(json.dumps(RP.build_config(MEM0, run_dir=TMP / "q4",
                                                                test_ollama_upstream=("127.0.0.1", 11500))), encoding="utf-8")


def _load_cfg(name, test_ok):
    """With no test key the X7 rule (run_dir inside the polygon runs tree) comes first: the polygon root is pointed at
    this suite's TMP for the call, so the Ollama rule itself is what answers - never the real polygon."""
    saved = PX.POLYGON_RUNS
    PX.POLYGON_RUNS = TMP
    try:
        return PX.ProxyConfig.load(TMP / "q4" / name, secq, test_upstream_ok=test_ok)
    except ValueError as e:
        return f"refused: {e}"
    finally:
        PX.POLYGON_RUNS = saved


check("the proxy takes it only with a test key (X2): with one its Ollama leg goes to that port; without one the "
      "Ollama upstream alone is refused by name", ok(lambda: tuple(_load_cfg("c.json", True).ollama_upstream) == ("127.0.0.1", 11500)
                                                     and "cannot change the Ollama upstream" in _load_cfg("o.json", False)),
      str(_load_cfg("o.json", False))[:120])
for bad_up in (("10.0.0.1", 11500), ("127.0.0.1", 0), ("127.0.0.1", True), ("127.0.0.1", "11500")):
    try:
        RP.build_config(MEM0, run_dir=TMP / "q4b", test_ollama_upstream=bad_up)
        verdict_q4 = "accepted"
    except RP.ProxyPlanError as e:
        verdict_q4 = str(e)
    check(f"a test Ollama upstream that is not 127.0.0.1 and a port is refused by name: {bad_up!r}",
          "127.0.0.1 and a port" in verdict_q4, verdict_q4)
cfgt = RP.build_config(MEM0, run_dir=TMP / "q4c", embed_tokenizer={"path": "t.json", "sha256": "a" * 64},
                       test_ollama_upstream=("127.0.0.1", 11500))
check("with an embed tokenizer too, both live in the one ollama block - neither replaces the other",
      ok(lambda: cfgt["ollama"] == {"embed_tokenizer": {"path": "t.json", "sha256": "a" * 64}, "upstream": ["127.0.0.1", 11500]}),
      str(cfgt.get("ollama")))
check("no row's condition raised - every failure came back as a named FAIL", RAISED == [], str(RAISED))
for f in (ds, ol, ds2):
    f.close()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 probe upstream: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
