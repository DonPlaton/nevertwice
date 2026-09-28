#!/usr/bin/env python3
"""PREREG-V3 TB1, step A2.7: the proxy's Ollama leg runs under the pacer's own code, per arm, and passes bytes.

* A port-exhaustion 400 twice, then 200: three attempts, two retry sleeps of the pacer's RETRY_INTERVAL_S, and
  the client gets the 200 byte for byte. A WinError 10048 on connect is retried the same way.
* A 500 on /api/generate twice: sleeps of the pacer's LLM_RETRY_INTERVALS_S (15 s, then 30 s), then the NDJSON
  stream reaches the client byte for byte. A 404 is never retried; a non-port-exhaustion 400 on embed is a failed
  outcome (by status) and reaches the client as Ollama sent it.
* Observe mode: no retry and no pace sleep. Two arms keep independent pace floors (one pacer copy each).
* A generation call from a cloud arm is fallback_local; an embed call is not; each call is a line in ollama.jsonl.
* The leg's pacer is research/_ollama_pacer.py itself, and the proxy's source carries none of its constants; the
  Ollama upstream is pinned to 127.0.0.1 (the config file cannot move it outside tests); 127.0.0.1:11434 is never
  dialled here.

No network: a fake Ollama on a loopback port; the pacer's sleeps are a fake clock.

    python tests/_test_llm_proxy_ollama.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
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


P = _load("v3_llm_proxy_o", ROOT / "research" / "_llm_proxy.py")
ST = _load("v3_llm_proxy_selftest_o", ROOT / "research" / "_llm_proxy_selftest.py")

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


PORT_EXHAUSTED = b'{"error":"dial tcp: Only one usage of each socket address (protocol/network address/port) is normally permitted."}'
EMBED_OK = b'{"model":"nomic","embeddings":[[0.1,0.2]]}'
NDJSON = [b'{"response":"he","done":false}\n', b'{"response":"llo","done":false}\n', b'{"response":"","done":true}\n']


class FakeOllama:
    """Scripted per path: a queue of (status, body) answers; the last one repeats. Counts attempts per path."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(32)
        self.port = self.sock.getsockname()[1]
        self.script: dict[str, list] = {}
        self.attempts: dict[str, int] = {}
        self.sent: list[bytes] = []
        threading.Thread(target=self._loop, daemon=True).start()

    def close(self):
        self.sock.close()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._conn, args=(c,), daemon=True).start()

    def _conn(self, c):
        buf = bytearray()
        try:
            while b"\r\n\r\n" not in buf:
                chunk = c.recv(65536)
                if not chunk:
                    return
                buf += chunk
            head = bytes(buf[:buf.index(b"\r\n\r\n")])
            path = head.split(b" ", 2)[1].decode()
            self.attempts[path] = self.attempts.get(path, 0) + 1
            q = self.script.get(path) or [(200, EMBED_OK)]
            status, body = q.pop(0) if len(q) > 1 else q[0]
            if status == 200 and body == "CUT":           # B-OLR: Ollama closes inside its answer
                out = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n" + EMBED_OK[:10]
            elif status == 200 and body == "BADHEAD":     # B-OLR: a status line whose code is no number
                out = b"HTTP/1.1 abc OK\r\nContent-Length: 0\r\n\r\n"
            elif status == 200 and body == "NDJSON":
                out = b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\nTransfer-Encoding: chunked\r\n\r\n"
                for line in NDJSON:
                    out += b"%x\r\n%s\r\n" % (len(line), line)
                out += b"0\r\n\r\n"
            else:
                out = (f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                       f"Connection: close\r\n\r\n").encode() + body
            self.sent.append(out)
            c.sendall(out)
        finally:
            c.close()


TMP = Path(tempfile.mkdtemp(prefix="nvt3_proxy_oll_"))
(TMP / "deepseek.env").write_bytes(f"DEEPSEEK_API_KEY={ST.SENTINEL_KEY}\n".encode())
OL = FakeOllama()
dialled: list[tuple] = []
sleeps: dict[str, list] = {}


def make(mode: str = "pace", run: str = "run"):
    arms = [P.ArmConfig(arm="local", token="t-local", ollama_leg=True, cloud_arm=False),
            P.ArmConfig(arm="cloud", token="t-cloud", ollama_leg=True, cloud_arm=True)]
    cfg = P.ProxyConfig(arms=arms, run_dir=TMP / run, upstream_host="127.0.0.1", upstream_port=1, upstream_tls=False,
                        control_token="ctl", ollama_mode=mode, ollama_upstream=("127.0.0.1", OL.port))
    px = P.Proxy(cfg, P.read_key(TMP / "deepseek.env"), log=lambda m: None)
    for arm, leg in px.legs.items():
        sleeps[f"{run}:{arm}"] = []
        leg.pacer._sleep = sleeps[f"{run}:{arm}"].append       # the fake clock: nothing sleeps for real
        real_connect = leg.connect

        def connect(hp, _real=real_connect):
            dialled.append(tuple(hp))
            return _real(hp)
        leg.connect = connect
    return px, px.start()


def call(port: int, path: str, body: bytes = b'{"model":"m","input":"x"}') -> bytes:
    req = (f"POST {path} HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
           ).encode() + body
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(req)
    got = ST._read_all(s, 5)
    s.close()
    return got


px, ports = make()
lp, cp = ports["arms"]["local"]["ollama"], ports["arms"]["cloud"]["ollama"]
leg = px.legs["local"]
pacer = leg.pacer

print("\n- the pacer's own retries -")
OL.script["/api/embed"] = [(400, PORT_EXHAUSTED), (400, PORT_EXHAUSTED), (200, EMBED_OK)]
got = call(lp, "/api/embed")
check("port exhaustion twice, then 200: three attempts", OL.attempts.get("/api/embed") == 3, str(OL.attempts))
check("... two retry sleeps of the pacer's RETRY_INTERVAL_S",
      [s for s in sleeps["run:local"] if s == pacer.RETRY_INTERVAL_S] == [pacer.RETRY_INTERVAL_S] * 2, str(sleeps["run:local"]))
check("... and the client gets the 200 byte for byte", got == OL.sent[-1], got[:60].decode("latin-1"))
fails = {"n": 0}
orig_connect = leg.connect


def flaky(hp):
    if fails["n"] < 2:
        fails["n"] += 1
        e = OSError("Only one usage of each socket address")
        e.winerror = 10048
        raise e
    return orig_connect(hp)


leg.connect = flaky
OL.script["/api/embed"] = [(200, EMBED_OK)]
got = call(lp, "/api/embed")
leg.connect = orig_connect
check("WinError 10048 on connect is retried, then the call succeeds", got.startswith(b"HTTP/1.1 200") and fails["n"] == 2)
sleeps["run:local"].clear()
OL.attempts.clear()
OL.script["/api/generate"] = [(500, b'{"error":"model runner has unexpectedly stopped"}'),
                              (500, b'{"error":"model runner has unexpectedly stopped"}'), (200, "NDJSON")]
got = call(lp, "/api/generate", b'{"model":"m","prompt":"x","stream":true}')
check("a generation 500 twice: sleeps of the pacer's LLM_RETRY_INTERVALS_S (15 s, then 30 s)",
      [s for s in sleeps["run:local"] if s in pacer.LLM_RETRY_INTERVALS_S] == list(pacer.LLM_RETRY_INTERVALS_S),
      str(sleeps["run:local"]))
check("... then the NDJSON stream reaches the client byte for byte", got == OL.sent[-1] and b'"done":true' in got)
OL.attempts.clear()
OL.script["/api/show"] = [(404, b'{"error":"model \'missing\' not found"}')]   # an allowed path (B-OLM-ENC)
got = call(lp, "/api/show", b'{"model":"missing"}')
del OL.script["/api/show"]
check("a 404 is never retried, and reaches the client as sent", OL.attempts.get("/api/show") == 1 and got.startswith(b"HTTP/1.1 404"))
OL.script["/api/embed"] = [(400, b'{"error":"input too long"}')]
got = call(lp, "/api/embed")
tr = leg.transport()
check("a non-port-exhaustion 400 on embed is exactly one failed outcome by status, and reaches the client",
      got.startswith(b"HTTP/1.1 400") and (tr.get("failed_outcomes") or {}).get("by_status") == {400: 1},
      str(tr.get("failed_outcomes")))

print("\n- observe mode, per-arm floors, fallback_local -")
pxo, portso = make("observe", "run_obs")
lego = pxo.legs["local"]
OL.attempts.clear()
OL.script["/api/embed"] = [(400, PORT_EXHAUSTED), (200, EMBED_OK)]
got = call(portso["arms"]["local"]["ollama"], "/api/embed")
t_obs = lego.transport()
check("observe mode: port exhaustion is not retried (one attempt), and no pace sleep",
      OL.attempts.get("/api/embed") == 1 and not sleeps["run_obs:local"] and t_obs.get("pace_sleep_s", 0) == 0, str(t_obs)[:200])
check("two arms keep independent pace schedules (one pacer copy each)",
      px.legs["local"].pacer is not px.legs["cloud"].pacer
      and px.legs["local"].pacer._PACE_STATE is not px.legs["cloud"].pacer._PACE_STATE)
OL.script["/api/generate"] = [(200, "NDJSON")]
call(cp, "/api/generate", b'{"model":"m","prompt":"x"}')
call(cp, "/api/embed")
check("a generation call from a cloud arm is fallback_local; its embed call is not",
      px.legs["cloud"].fallback_local == 1 and px.legs["local"].fallback_local == 0)
lines = [json.loads(x) for x in (TMP / "run" / "ollama.jsonl").read_bytes().decode().splitlines()]
check("every call is a line in ollama.jsonl, the cloud generate flagged fallback_local",
      len(lines) >= 7 and any(x["arm"] == "cloud" and x["is_llm"] and x["fallback_local"] for x in lines), str(len(lines)))

print("\n- the pacer's own code, the pinned upstream -")
check("the leg's pacer is research/_ollama_pacer.py itself",
      Path(pacer.__file__).resolve() == (ROOT / "research" / "_ollama_pacer.py").resolve())
src = (ROOT / "research" / "_llm_proxy.py").read_text(encoding="utf-8")
leg_src = src[src.index("# ── the Ollama leg"):src.index("# ── the proxy ─")]
check("the proxy's leg carries none of the pacer's constants (15.0, 30.0, 0.125, 16)",
      not re.search(r"(?<![\w.])(15\.0|30\.0|0\.125)(?![\w.])", leg_src))
check("the pacer's contract the leg relies on is there",
      all(hasattr(pacer, n) for n in ("_run_paced", "_MODE", "VALID_MODES", "attach", "classify", "_is_embed_path",
                                       "_is_llm_path", "_sleep")))
for label, kw in (("another host", {"ollama_upstream": ("10.0.0.5", 11434)}),):
    try:
        P.ProxyConfig(arms=[], run_dir=TMP, **kw)
        check(f"the Ollama upstream is pinned to 127.0.0.1: {label} refused", False)
    except ValueError:
        check(f"the Ollama upstream is pinned to 127.0.0.1: {label} refused", True)
cf = TMP / "o.json"
cf.write_bytes(json.dumps({"arms": [], "run_dir": str(TMP / "o"), "ollama": {"upstream": ["127.0.0.1", 9999]}}).encode())
try:
    P.ProxyConfig.load(cf, {}, test_upstream_ok=False)
    check("the config file cannot move the Ollama upstream with the real key", False)
except ValueError:
    check("the config file cannot move the Ollama upstream with the real key", True)
check("127.0.0.1:11434 was never dialled", ("127.0.0.1", 11434) not in dialled and dialled, str(set(dialled)))
print("\n- Q-A5-1: a local writer's request strings, for K76's coverage -")
px.stage = {"block": "b1", "stage": "write"}
COVER = b'{"model":"m","messages":[{"role":"user","content":"a local sentence long enough for a coverage window"}]}'
call(lp, "/u/r1.cov-2/api/chat", COVER)
call(lp, "/u/r1.cov-2/api/embed", b'{"model":"m","input":"an embedded text never counted as reaching a writer"}')
px.stage = {"block": "b1", "stage": "questions"}
call(lp, "/u/r1.cov-2/api/chat", b'{"model":"m","messages":[{"role":"user","content":"a question stage chat"}]}')
px.stage = {"block": "b1", "stage": "write"}
OL.script["/api/chat"] = [(204, b"")]                                    # answered, but no generation: not a 200
call(lp, "/u/r1.cov-3/api/chat", b'{"model":"m","messages":[{"role":"user","content":"a chat the model failed"}]}')
OL.script["/api/chat"] = [(500, b'{"error":"model crashed"}')]           # every try of the pacer answers 500
call(lp, "/u/r1.cov-3/api/chat", b'{"model":"m","messages":[{"role":"user","content":"a chat the model failed"}]}')
_failed3 = (TMP / "run" / "bodies" / "local" / "r1.cov-3.jsonl").exists()
OL.script["/api/chat"] = [(200, EMBED_OK)]
call(lp, "/u/r1.cov-3/api/chat", b'{"model":"m","messages":[{"role":"user","content":"a chat the model failed"}]}')
_bf3 = TMP / "run" / "bodies" / "local" / "r1.cov-3.jsonl"
_bl3 = [json.loads(x) for x in _bf3.read_bytes().decode("utf-8").split("\n") if x.strip()] if _bf3.exists() else []
check("Q-A5-1 (C-1): a leg call Ollama answered 204 or 500 leaves no body; its 200 retry leaves one, with status 200",
      not _failed3 and len(_bl3) == 1 and _bl3[0]["status"] == 200, f"{_failed3} {_bl3}")
px.stage = {"block": None, "stage": None}
_bf = TMP / "run" / "bodies" / "local" / "r1.cov-2.jsonl"
_bl = [json.loads(x) for x in _bf.read_bytes().decode("utf-8").split("\n") if x.strip()] if _bf.exists() else []
check("Q-A5-1: a generation call on the leg in the write stage leaves its parsed strings (via ollama); an embedding "
      "call and a question-stage call never do", len(_bl) == 1 and _bl[0]["via"] == "ollama"
      and "a local sentence long enough for a coverage window" in _bl[0]["strings"]
      and not any("embedded text" in s or "question stage" in s for b_ in _bl for s in b_["strings"]), str(_bl)[:300])
idle = P.OllamaLeg("idle", mode="pace", cloud_arm=False, upstream=("127.0.0.1", OL.port), log=lambda m: None, run_dir=TMP)
check("an idle leg reports calls: 0 (never an absent record)", idle.transport().get("calls") == 0, str(idle.transport()))

print("\n- B-OLR: an answer Ollama cut keeps its record; B-OLM: the model store is never reached -")
OL.script["/api/embed"] = [(200, "CUT")]
call(lp, "/u/r1.cut/api/embed")
OL.script["/api/embed"] = [(200, EMBED_OK)]
_ol = [json.loads(x) for x in (TMP / "run" / "ollama.jsonl").read_bytes().decode("utf-8").split("\n") if x.strip()]
cut = [r for r in _ol if r.get("unit") == "r1.cut"]
check("B-OLR: an embed answer Ollama closed inside its body is a line in ollama.jsonl - error ProtocolError, no embed "
      "stats - never a handler error that leaves no record", len(cut) == 1 and cut[0].get("error") == "ProtocolError"
      and "embed_inputs" not in cut[0], str(cut))
OL.script["/api/embed"] = [(200, "BADHEAD")]
got_bh = call(lp, "/u/r1.badhead/api/embed")
OL.script["/api/embed"] = [(200, EMBED_OK)]
_ol = [json.loads(x) for x in (TMP / "run" / "ollama.jsonl").read_bytes().decode("utf-8").split("\n") if x.strip()]
bh = [r for r in _ol if r.get("unit") == "r1.badhead"]
check("B-OLR: an answer whose status line does not parse (a code that is no number) is a 502 to the client and a line in "
      "ollama.jsonl naming the error - never a handler error that leaves no record and no answer",
      got_bh.startswith(b"HTTP/1.1 502") and len(bh) == 1 and bh[0].get("error") == "ValueError"
      and bh[0].get("status") is None, f"{got_bh[:30]!r} {bh}")
OL.attempts.clear()
got_lf = call(lp, "/u/r1.lf/api/embed\nX-Injected: 1")
_s = socket.create_connection(("127.0.0.1", lp))
_s.sendall(b"POST /u/r1.cl/api/embed HTTP/1.1\r\nHost: x\r\nContent-Length: x\r\n\r\n{}")
got_cl = ST._read_all(_s, 5)
_s.close()
check("B-X4-LINE on the leg: a request TARGET carrying a bare LF is refused (400) and never reaches Ollama",
      got_lf.startswith(b"HTTP/1.1 400") and not OL.attempts, f"{got_lf[:30]!r} {OL.attempts}")
check("B-FRAME on the leg: a Content-Length that is no number is refused (400) and never reaches Ollama - never a "
      "ValueError that drops the connection unanswered", got_cl.startswith(b"HTTP/1.1 400") and not OL.attempts,
      f"{got_cl[:30]!r} {OL.attempts}")
got_pull = call(lp, "/u/r1.pull/api/pull", b'{"model":"qwen3:8b"}')
got_del = call(lp, "/u/r1.pull/api/delete", b'{"model":"qwen3:8b"}')
_ol = [json.loads(x) for x in (TMP / "run" / "ollama.jsonl").read_bytes().decode("utf-8").split("\n") if x.strip()]
refused = [r for r in _ol if r.get("unit") == "r1.pull"]
check("B-OLM: a pull or a delete on the leg is refused (403) and never reaches Ollama - the owner's model store is not "
      "the product's, and a pull is egress no witness sees; each refusal is a line in ollama.jsonl",
      got_pull.startswith(b"HTTP/1.1 403") and got_del.startswith(b"HTTP/1.1 403") and "/api/pull" not in OL.attempts
      and "/api/delete" not in OL.attempts and [r.get("error") for r in refused] == ["refused:path"] * 2,
      f"{got_pull[:30]!r} {OL.attempts} {refused}")

print("\n- B-OLM-ENC: the leg forwards its allow-list only, exactly; an encoded or queried target is refused first -")
OL.attempts.clear()
leg_log: list[str] = []
leg.log = leg_log.append
enc = {t: call(lp, f"/u/r1.enc/{t}", b'{"model":"qwen3:8b"}') for t in ("api/%70ull", "api/%64elete", "api/embed?x=1")}
att_enc = dict(OL.attempts)                             # what the encoded targets alone reached
bad = {t: call(lp, f"/u/r1.path/{t}", b'{"model":"qwen3:8b"}')
       for t in ("API/PULL", "api/pull/", "api//pull", "api/push", "API/EMBED", "api/embed/", "api//embed")}
_ol = [json.loads(x) for x in (TMP / "run" / "ollama.jsonl").read_bytes().decode("utf-8").split("\n") if x.strip()]
enc_rec = [r.get("error") for r in _ol if r.get("unit") == "r1.enc"]
path_rec = [r.get("error") for r in _ol if r.get("unit") == "r1.path"]
enc_heads = {t: g[:20] for t, g in enc.items()}
bad_heads = {t: g[:20] for t, g in bad.items()}
check("B-OLM-ENC: a target with a percent-escape or a query is refused (400) before routing - Ollama's router matches "
      "the DECODED path, so /api/%70ull is its /api/pull - and never reaches Ollama; each is a line in ollama.jsonl",
      all(g.startswith(b"HTTP/1.1 400") for g in enc.values()) and not att_enc
      and enc_rec == ["refused:encoded-target"] * 3, f"{enc_heads} {att_enc} {enc_rec}")
check("B-OLM-ENC: a path outside the leg's allow-list is refused (403), compared exactly and case-sensitively after "
      "/u/<run>.<unit> - /API/PULL, /api/pull/, /api//pull, /api/push, /API/EMBED, /api/embed/, /api//embed never "
      "reach Ollama; each is a line in ollama.jsonl", all(g.startswith(b"HTTP/1.1 403") for g in bad.values())
      and not OL.attempts and path_rec == ["refused:path"] * 7, f"{bad_heads} {OL.attempts} {path_rec}")
check("B-OLM-ENC: each refusal is named in the leg's log, with its path", len(leg_log) == 10
      and all("refused" in x for x in leg_log) and any("/api/%70ull" in x for x in leg_log)
      and any("/API/PULL" in x for x in leg_log), str(leg_log[:3]))
leg.log = lambda m: None
got_ok = {p: call(lp, f"/u/r1.ok{p}", b'{"model":"m"}') for p in ("/api/show", "/v1/models", "/v1/completions")}
check("... while the safe paths still reach Ollama - /api/show, the OpenAI shim's /v1/models and /v1/completions (a "
      "safe path refused would change the product's behaviour)", all(g.startswith(b"HTTP/1.1 200") for g in got_ok.values())
      and all(OL.attempts.get(p) == 1 for p in got_ok), f"{ {p: g[:20] for p, g in got_ok.items()} } {OL.attempts}")
OL.attempts.clear()
LEG_WANT = {"/api/generate", "/api/chat", "/v1/chat/completions", "/v1/completions",      # generation
            "/api/embed", "/api/embeddings", "/v1/embeddings",                              # embed
            "/api/tags", "/api/show", "/api/version", "/api/ps", "/v1/models"}               # read-only listing
through = {p: call(lp, f"/u/r1.def{p}", b'{"model":"m","input":"x","prompt":"x"}') for p in sorted(LEG_WANT)}
check("B-OLM-ENC: one definition - the leg forwards exactly Ollama's generation and embed paths as the pacer knows them "
      "(_LLM_PATHS, _EMBED_PATHS), the OpenAI-compatible /v1/embeddings and the read-only listing, and each reaches "
      "Ollama", all(g.startswith(b"HTTP/1.1 200") for g in through.values())
      and all(OL.attempts.get(p) == 1 for p in through) and leg.paths == LEG_WANT
      and set(pacer._LLM_PATHS) | set(pacer._EMBED_PATHS) <= leg.paths,
      f"{ {p: g[:20] for p, g in through.items()} } missing {sorted(LEG_WANT - leg.paths)} extra "
      f"{sorted(leg.paths - LEG_WANT)}")

print("\n- TB7 embed_at_cap (Q-A7-7 O-a): each input counted by the pinned tokenizer, prompt_eval_count beside it -")
AC = _load("v3_accounting_for_oll", ROOT / "research" / "v3" / "accounting.py")
px.stage = {"block": None, "stage": None}
leg.embed_count, leg.embed_cap = (lambda text: len(text.split()) + 2), 5      # a fake count: words + 2 specials
OL.script["/api/embed"] = [(200, b'{"model":"m","embeddings":[[0.1],[0.2]],"prompt_eval_count":11}'),
                           (200, b'{"model":"m","embeddings":[[0.1]],"prompt_eval_count":12}')]
call(lp, "/u/r1.tb7/api/embed", b'{"model":"m","input":["one two","one two three four five"]}')
call(lp, "/u/r1.tb7/api/embed", b'{"model":"m","input":"one two three"}')
call(cp, "/u/r1.tb7/api/embed", b'{"model":"m","input":"no tokenizer on this leg"}')
olog = [json.loads(x) for x in (TMP / "run" / "ollama.jsonl").read_bytes().decode("utf-8").split("\n") if x.strip()]
tb7 = [r for r in olog if r.get("unit") == "r1.tb7" and r["arm"] == "local"]
check("TB7: per embed call the inputs, their tokens (content + specials), the inputs at or over the cap and Ollama's "
      "prompt_eval_count - two inputs of 4 and 7 tokens at cap 5: one at the cap",
      [(r.get("embed_inputs"), r.get("embed_tokens"), r.get("embed_at_cap"), r.get("prompt_eval_count")) for r in tb7]
      == [(2, 11, 1, 11), (1, 5, 1, 12)], str([(r.get("embed_inputs"), r.get("embed_tokens"), r.get("embed_at_cap"),
                                                 r.get("prompt_eval_count")) for r in tb7]))
both = [r for r in olog if r.get("unit") == "r1.tb7"]            # both arms' records: the arm is the function's to pick
try:
    ei = AC.embed_inputs(both, arm="local")
except Exception as e:  # noqa: BLE001 - the row FAILs by name
    ei = f"{type(e).__name__}: {e}"
check("TB7: accounting sums the arm's calls - embed_at_cap 2 - and counts the call whose token sum is not Ollama's "
      "prompt_eval_count (5 against 12), never adjusting it", ei == {"embed_at_cap": 2, "calls": 2, "inputs": 3,
                                                                    "tokens": 16, "prompt_eval_count": 23,
                                                                    "mismatched_calls": 1}, str(ei))
check("TB7: the leg's transport carries embed_at_cap (2); a leg without a tokenizer says None, never 0",
      leg.transport().get("embed_at_cap") == 2 and px.legs["cloud"].transport().get("embed_at_cap") is None,
      f"{leg.transport().get('embed_at_cap')} {px.legs['cloud'].transport().get('embed_at_cap')}")
cloud_rec = [r for r in olog if r.get("unit") == "r1.tb7" and r["arm"] == "cloud"]
try:
    AC.embed_inputs(both, arm="cloud")
    un = "accepted"
except Exception as e:  # noqa: BLE001 - only accounting's named refusal passes the row
    un = str(e) if isinstance(e, AC.AccountingError) else f"not refused by name: {type(e).__name__}: {e}"
check("TB7: an embed call recorded without the leg's count refuses in accounting - an unmeasured cap is never 0",
      cloud_rec and cloud_rec[0].get("embed_at_cap") is None and "no measured" in un, un)
cut_rec = [r for r in olog if r.get("unit") == "r1.cut" and r["arm"] == "local"]
try:
    ei_cut = AC.embed_inputs([*[r for r in both if r["arm"] == "local"], *cut_rec], arm="local")
except Exception as e:  # noqa: BLE001 - the row FAILs by name
    ei_cut = f"{type(e).__name__}: {e}"
check("B-EMB-ERR: an embed answer cut after its 200 head (the leg's record carries its error and no stats) is no answered "
      "call - it neither refuses the arm's TB7 accounting as 'no tokenizer' nor counts", len(cut_rec) == 1
      and ei_cut == ei, f"{cut_rec} {ei_cut}")
OL.script["/v1/embeddings"] = [(200, b'{"object":"list","data":[{"embedding":[0.1]}],"model":"m",'
                                     b'"usage":{"prompt_tokens":5,"total_tokens":5}}')]
call(lp, "/u/r1.oa/v1/embeddings", b'{"model":"m","input":"one two three"}')
oa = [json.loads(x) for x in (TMP / "run" / "ollama.jsonl").read_bytes().decode("utf-8").split("\n")
      if x.strip() and '"r1.oa"' in x]
check("B-OAEMB: an embed call on Ollama's OpenAI-compatible /v1/embeddings (zep-graphiti's OpenAIEmbedder) is an embed "
      "call - its TB7 stats are measured and usage.prompt_tokens stands for prompt_eval_count",
      len(oa) == 1 and oa[0].get("is_embed") is True
      and (oa[0].get("embed_inputs"), oa[0].get("embed_tokens"), oa[0].get("prompt_eval_count")) == (1, 5, 5), str(oa))
try:
    from tokenizers import Tokenizer, models, pre_tokenizers, processors  # noqa: E402
    vocab = {"<s>": 0, "</s>": 1, "[UNK]": 2, "one": 3, "two": 4, "three": 5}
    tk = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
    tk.pre_tokenizer = pre_tokenizers.Whitespace()
    tk.post_processor = processors.TemplateProcessing(single="<s> $A </s>", special_tokens=[("<s>", 0), ("</s>", 1)])
    tjson = TMP / "tokenizer.json"
    tk.save(str(tjson))
    tsha = hashlib.sha256(tjson.read_bytes()).hexdigest()
    have_tok = True
except ImportError:
    have_tok = False
    print("       SKIP TB7 tokenizer rows: the tokenizers package is not here - not passed")
if have_tok:
    cfg_t = P.ProxyConfig(arms=[P.ArmConfig(arm="local", token="t-local", ollama_leg=True, cloud_arm=False)],
                          run_dir=TMP / "run_tok", upstream_host="127.0.0.1", upstream_port=1, upstream_tls=False,
                          control_token="ctl", ollama_upstream=("127.0.0.1", OL.port),
                          embed_tokenizer=(str(tjson), tsha))
    pxt = P.Proxy(cfg_t, P.read_key(TMP / "deepseek.env"), log=lambda m: None)
    rec_t = json.loads((TMP / "run_tok" / "embed_tokenizer.json").read_text(encoding="utf-8"))
    import importlib.metadata  # noqa: E402
    check("TB7: the proxy loads the pinned tokenizer.json (sha verified) - an input counted as tokens.Truncator counts "
          "it, content + 2 specials, cap 2048 - and records the tokenizers version and its RECORD's sha256",
          pxt.legs["local"].embed_count("one two three") == 5 and pxt.legs["local"].embed_cap == 2048
          and rec_t["tokenizers"] == importlib.metadata.version("tokenizers") and rec_t["sha256"] == tsha
          and len(rec_t["record_sha256"]) == 64 and rec_t["cap"] == 2048 and rec_t["specials"] == 2, str(rec_t))
    try:
        P.Proxy(P.ProxyConfig(arms=[P.ArmConfig(arm="local", token="t-local", ollama_leg=True, cloud_arm=False)],
                              run_dir=TMP / "run_tok2", upstream_host="127.0.0.1", upstream_port=1, upstream_tls=False,
                              control_token="ctl", embed_tokenizer=(str(tjson), "0" * 64)),
                P.read_key(TMP / "deepseek.env"), log=lambda m: None)
        wrong = "started"
    except ValueError as e:
        wrong = str(e)
    check("TB7: a tokenizer.json off its sha stops the proxy - no leg counts with another tokenizer",
          "not the pinned" in wrong, wrong)

print("\n- Q4: the leg runs the main path's canary and owner-marker scan; capture_body scans before it writes -")
CAN = "nvt3-canary-home-Q4q4Q4q4"
cfg_s = P.ProxyConfig(arms=[P.ArmConfig(arm="local", token="t-local", ollama_leg=True, cloud_arm=False)],
                      run_dir=TMP / "run_scan", upstream_host="127.0.0.1", upstream_port=1, upstream_tls=False,
                      control_token="ctl", ollama_upstream=("127.0.0.1", OL.port))
pxs = P.Proxy(cfg_s, P.read_key(TMP / "deepseek.env"), log=lambda m: None, canaries={"home": CAN},
              markers=P.OwnerMarkers({"name": "Ivan Testov"}, []))
ports_s = pxs.start()
ls_ = ports_s["arms"]["local"]["ollama"]
OL.attempts.clear()
got_can = call(ls_, "/u/r1.sc/api/chat", json.dumps({"model": "m", "messages": [
    {"role": "user", "content": f"my notes: {CAN}"}]}).encode())
got_mark = call(ls_, "/u/r1.sm/api/embed", json.dumps({"model": "m", "input": "a letter from Ivan Testov"}).encode())
got_clean = call(ls_, "/u/r1.sk/api/embed", b'{"model":"m","input":"nothing of the owner"}')
_ol_s = [json.loads(x) for x in (TMP / "run_scan" / "ollama.jsonl").read_bytes().decode("utf-8").split("\n")
         if x.strip()] if (TMP / "run_scan" / "ollama.jsonl").exists() else []
by_unit = {r.get("unit"): r for r in _ol_s}
fl_s = [json.loads(x) for x in (TMP / "run_scan" / "flags.jsonl").read_bytes().decode("utf-8").split("\n")
        if x.strip()] if (TMP / "run_scan" / "flags.jsonl").exists() else []
check("Q4 (R-HOME-CANARY, K1): a leg request carrying a planted canary or an owner marker is refused (403) and never "
      "reaches Ollama - its ollama.jsonl line names refused:canary / refused:owner_marker with its hit count, and the "
      "arm's flag is in flags.jsonl; a clean one still goes through",
      got_can.startswith(b"HTTP/1.1 403") and got_mark.startswith(b"HTTP/1.1 403") and got_clean.startswith(b"HTTP/1.1 200")
      and "/api/chat" not in OL.attempts and OL.attempts.get("/api/embed") == 1
      and (by_unit.get("r1.sc") or {}).get("error") == "refused:canary" and (by_unit.get("r1.sc") or {}).get("canary_hits") == 1
      and (by_unit.get("r1.sm") or {}).get("error") == "refused:owner_marker"
      and (by_unit.get("r1.sm") or {}).get("owner_marker_hits") == 1
      and sorted(f.get("kind") for f in fl_s if f.get("arm") == "local") == ["canary", "owner_marker"],
      f"{got_can[:20]!r} {got_mark[:20]!r} {got_clean[:20]!r} {OL.attempts} {by_unit} {fl_s}")
pxs.stop()
try:
    P.capture_body(TMP / "run_scan", arm="local", unit="r1.cb", t0=0.0, request_key=None, via="ollama",
                   body=json.dumps({"messages": [{"role": "user", "content": CAN}]}).encode(), status=200,
                   scan=pxs.scan_body)
    cap = "written"
except Exception as e:  # noqa: BLE001 - CaptureRefused by name, anything else FAILs the row by name
    cap = f"refused:{e.kind}" if type(e).__name__ == "CaptureRefused" else f"{type(e).__name__}: {e}"
check("Q4: capture_body scans a body before it writes it - a canary is refused by name (CaptureRefused) and no record "
      "is written", cap == "refused:canary" and not (TMP / "run_scan" / "bodies" / "local" / "r1.cb.jsonl").exists(), cap)
try:
    P.capture_body(TMP / "run_scan", arm="local", unit="r1.cn", t0=0.0, request_key=None, via="ollama",
                   body=b'{"messages":[{"role":"user","content":"clean"}]}', status=200, scan=None)
    capn = "written or skipped quietly"
except ValueError as e:
    capn = "ValueError: scan" if "scan" in str(e) else f"ValueError: {e}"
except Exception as e:  # noqa: BLE001 - the row FAILs by name
    capn = f"{type(e).__name__}: {e}"
check("Q4: capture_body without the proxy's scan refuses by name (ValueError) - a body is never written unscanned, "
      "and never skipped quietly (a leg built without a scan passes scan=None)",
      capn == "ValueError: scan" and not (TMP / "run_scan" / "bodies" / "local" / "r1.cn.jsonl").exists(), capn)

px.stop()
pxo.stop()
OL.close()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nproxy ollama leg: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
