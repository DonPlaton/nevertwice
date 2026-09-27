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
            if status == 200 and body == "NDJSON":
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
OL.script["/api/missing"] = [(404, b'{"error":"not found"}')]
got = call(lp, "/api/missing")
check("a 404 is never retried, and reaches the client as sent", OL.attempts.get("/api/missing") == 1 and got.startswith(b"HTTP/1.1 404"))
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
px.stage = {"block": None, "stage": None}
_bf = TMP / "run" / "bodies" / "local" / "r1.cov-2.jsonl"
_bl = [json.loads(x) for x in _bf.read_bytes().decode("utf-8").split("\n") if x.strip()] if _bf.exists() else []
check("Q-A5-1: a generation call on the leg in the write stage leaves its parsed strings (via ollama); an embedding "
      "call and a question-stage call never do", len(_bl) == 1 and _bl[0]["via"] == "ollama"
      and "a local sentence long enough for a coverage window" in _bl[0]["strings"]
      and not any("embedded text" in s or "question stage" in s for b_ in _bl for s in b_["strings"]), str(_bl)[:300])
idle = P.OllamaLeg("idle", mode="pace", cloud_arm=False, upstream=("127.0.0.1", OL.port), log=lambda m: None, run_dir=TMP)
check("an idle leg reports calls: 0 (never an absent record)", idle.transport().get("calls") == 0, str(idle.transport()))

px.stop()
pxo.stop()
OL.close()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nproxy ollama leg: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
