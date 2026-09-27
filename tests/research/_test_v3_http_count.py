#!/usr/bin/env python3
"""PREREG-V3 TB4.6a (A6): research/v3/arms/_http_count.py - the in-process arms' HTTP counter for reconciliation check 1
(rev1 §4.6, K87), counting only (the auditor's Q-46-4).

* classify: the arm's proxy port on loopback by route (chat / embeddings / other), the Ollama port by route (embed,
  generate, tags, pull, other), everything else "other"; a proxy-looking path on another port is not the proxy;
* in a child laid out as Q9 lays out an arm directory (copies of _http_count.py and _ollama_pacer.py side by side):
  every door is counted - urllib.request.urlopen, httpx.Client.send, httpx.AsyncClient.send, requests.Session.send -
  and the request goes through unchanged (the fake saw exactly the bodies sent);
* installed BEFORE the pacer, the counter sits inside it: an Ollama generate answered 503 once and retried by the pacer
  is two attempts here, as it is two requests at the server;
* an attempt that never connects is an attempt and an exception, never a response; a second install is refused.

    python tests/research/_test_v3_http_count.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

HC_PATH = ROOT / "research" / "v3" / "arms" / "_http_count.py"
PACER = ROOT / "research" / "_ollama_pacer.py"
_spec = importlib.util.spec_from_file_location("v3_http_count", HC_PATH)
HC = importlib.util.module_from_spec(_spec)
sys.modules["v3_http_count"] = HC
_spec.loader.exec_module(HC)
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


class Fake:
    """One loopback server that records every request; /api/generate answers 503 on its first call."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, bytes]] = []
        self.generate_calls = 0
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self, code: int, obj) -> None:
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.seen.append(("GET", self.path, b""))
                self._answer(200, {"models": []})

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                fake.seen.append(("POST", self.path, body))
                if self.path == "/api/generate":
                    fake.generate_calls += 1
                    if fake.generate_calls == 1:
                        return self._answer(503, {"error": "busy"})
                self._answer(200, {"ok": True})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


CHILD = r'''
import asyncio, json, sys, urllib.request
sys.path.insert(0, sys.argv[1])
PROXY, OLLAMA = int(sys.argv[2]), int(sys.argv[3])
import _http_count as HC
doors = HC.install(proxy_port=PROXY, ollama_ports=(OLLAMA,))
try:
    HC.install(proxy_port=PROXY)
    second = "no refusal"
except RuntimeError as e:
    second = str(e)
import _ollama_pacer as P
P.install("pace")
import httpx, requests
chat = f"http://127.0.0.1:{PROXY}/u/r1.u1/v1/chat/completions"
urllib.request.urlopen(urllib.request.Request(chat, data=b'{"door": "urllib"}', method="POST"), timeout=10).read()
with httpx.Client() as c:
    c.post(chat, content=b'{"door": "httpx"}')
async def go():
    async with httpx.AsyncClient() as ac:
        await ac.post(chat, content=b'{"door": "httpx-async"}')
asyncio.run(go())
requests.post(f"http://127.0.0.1:{PROXY}/u/r1.u1/v1/embeddings", data=b'{"door": "requests"}', timeout=10)
with httpx.Client() as c:
    c.post(f"http://127.0.0.1:{OLLAMA}/api/embed", content=b'{"input": "x"}')
    c.get(f"http://127.0.0.1:{OLLAMA}/api/tags")
    r = c.post(f"http://127.0.0.1:{OLLAMA}/api/generate", content=b'{"prompt": "p"}')
    gen_status = r.status_code
refused = None
try:
    urllib.request.urlopen("http://127.0.0.1:1/nowhere", timeout=3)
except Exception as e:
    refused = type(e).__name__
print(json.dumps({"snap": HC.snapshot(), "second": second, "gen_status": gen_status, "refused": refused,
                  "doors": doors}))
'''

TMP = Path(tempfile.mkdtemp(prefix="v3hc_"))
FAKE = Fake()
OLL = Fake()
try:
    print("\n- classify -")
    HC._CFG.update(proxy_port=41000, ollama_ports=(11434,))
    rows = {
        "http://127.0.0.1:41000/u/r1.u1/v1/chat/completions": "proxy:chat",
        "http://localhost:41000/u/r1.u1/v1/embeddings": "proxy:embeddings",
        "http://127.0.0.1:41000/u/r1.u1/v1/models": "proxy:other",
        "http://127.0.0.1:11434/api/embed": "ollama:embed",
        "http://127.0.0.1:11434/api/generate": "ollama:generate",
        "http://127.0.0.1:11434/api/tags": "ollama:tags",
        "http://127.0.0.1:11434/api/pull": "ollama:pull",
        "http://127.0.0.1:41001/u/r1.u1/v1/chat/completions": "other:other",
        "https://api.deepseek.com/v1/chat/completions": "other:other",
        "http://10.0.0.5:41000/u/r1.u1/v1/chat/completions": "other:other",
    }
    got = {u: HC.classify(u) for u in rows}
    check("the proxy by port and route, Ollama by port and route, everything else other - a proxy path on another "
          "port or host is not the proxy", got == rows, str({u: g for u, g in got.items() if g != rows[u]}))
    check("total() sums a field over a class prefix",
          HC.total({"counts": {"proxy:chat": {"attempts": 2}, "proxy:embeddings": {"attempts": 1},
                               "ollama:embed": {"attempts": 5}}}, "proxy:") == 3)

    print("\n- a child laid out as an arm directory (Q9): every door counted, nothing changed -")
    arm = TMP / "arm"
    arm.mkdir()
    shutil.copyfile(HC_PATH, arm / "_http_count.py")
    shutil.copyfile(PACER, arm / "_ollama_pacer.py")
    (TMP / "child.py").write_text(CHILD, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("OLLAMA")}
    env.update({"OLLAMA_HOST": f"http://127.0.0.1:{OLL.port}", "NO_PROXY": "127.0.0.1,localhost",
                "PYTHONPYCACHEPREFIX": str(TMP / "pyc")})
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    r = subprocess.run([sys.executable, "-B", str(TMP / "child.py"), str(arm), str(FAKE.port), str(OLL.port)],
                       capture_output=True, text=True, timeout=180, env=env, cwd=str(TMP))
    out = {}
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        pass
    snap = out.get("snap") or {}
    counts = snap.get("counts") or {}
    check("the child ran", r.returncode == 0 and out, (r.stderr or r.stdout)[-400:])
    check("four doors are wrapped in a venv that has httpx and requests",
          out.get("doors") == ["urllib.request.urlopen", "httpx.Client.send", "httpx.AsyncClient.send",
                               "requests.Session.send"], str(out.get("doors")))
    check("proxy chat: urllib, httpx and httpx async each counted once (3 attempts, 3 responses)",
          counts.get("proxy:chat") == {"attempts": 3, "responses": 3, "exceptions": 0}, str(counts.get("proxy:chat")))
    check("proxy embeddings through requests counted", counts.get("proxy:embeddings") == {"attempts": 1, "responses": 1,
                                                                                           "exceptions": 0})
    check("the requests reached the server unchanged (the exact bodies, in order)",
          [b for m, p, b in FAKE.seen] == [b'{"door": "urllib"}', b'{"door": "httpx"}', b'{"door": "httpx-async"}',
                                            b'{"door": "requests"}'], str(FAKE.seen))
    check("inside the pacer: a 503 retried by the pacer is two attempts here, as it is two requests at the server",
          counts.get("ollama:generate", {}).get("attempts") == 2 and OLL.generate_calls == 2
          and out.get("gen_status") == 200, f"{counts.get('ollama:generate')} server={OLL.generate_calls}")
    check("Ollama embed and tags counted by route",
          counts.get("ollama:embed", {}).get("attempts") == 1 and counts.get("ollama:tags", {}).get("attempts") == 1)
    check("an attempt that never connects is an attempt and an exception, not a response",
          counts.get("other:other") == {"attempts": 1, "responses": 0, "exceptions": 1} and out.get("refused"),
          str(counts.get("other:other")))
    check("a second install is refused (it would count every request twice)", "installed already" in
          str(out.get("second")), str(out.get("second")))

    print("\n- self-contained (Q9: copied beside the adapter) -")
    import ast  # noqa: PLC0415
    tree = ast.parse(HC_PATH.read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    check("_http_count imports the standard library, and httpx / requests only when present",
          mods <= {"__future__", "threading", "urllib", "httpx", "requests"}, str(sorted(mods)))
finally:
    FAKE.close()
    OLL.close()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 http count: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
