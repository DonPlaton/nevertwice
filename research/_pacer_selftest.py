#!/usr/bin/env python3
"""Does the pacer see every HTTP client THIS interpreter has? Run it in each arm's own environment.

    <venv>/Scripts/python research/_pacer_selftest.py [--json]

(б) b-a, K45: graphiti's Ollama traffic went through `httpx2` (the openai SDK 3.x transport), which
the pacer did not hook - the zep rows reached the register with no transport record, and only the
Ollama server log could say how many calls they made. What a stand's arms can reach Ollama through
depends on the environment the arm runs in, so the check has to run THERE, not in the repository's
own interpreter.

A throwaway HTTP server on an ephemeral loopback port plays Ollama (`OLLAMA_HOST` points the pacer
at it; no model and no real server is touched). The pacer is installed, and one request goes out
through every client library importable here: urllib, httpx and httpx2 (sync and async), the
openai SDK (whatever transport it carries), requests and aiohttp. Each must leave a trace - a paced
call for the paced transports, a bypass count for the tripwire ones. A library that reached the
server and left no trace is a transport the pacer cannot see: exit 1, named.

Standard library plus whatever the environment already has; installs nothing.
"""
from __future__ import annotations

import asyncio
import http.server
import json
import os
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


class _FakeOllama(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        _FakeOllama.hits.append(self.path)
        if self.path.startswith("/v1/embeddings"):
            body = {"object": "list", "model": "m",
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1}}
        else:
            body = {"embeddings": [[0.1, 0.2]], "models": []}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = _answer

    def log_message(self, *a):
        pass


def probe() -> dict:
    """{client: {"reached": bool, "seen": "paced"|"bypass"|None, "error": str|None}} plus meta."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllama)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    os.environ["OLLAMA_HOST"] = base                  # the pacer recognises this host as Ollama
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
    import _ollama_pacer as pacer                     # noqa: PLC0415
    pacer.uninstall()
    pacer._now, pacer._sleep = (lambda: 0.0), (lambda s: None)

    async def _no_sleep(s):
        return None
    pacer._async_sleep = _no_sleep
    pacer.install()
    body = json.dumps({"model": "m", "input": "x"}).encode()
    embed = f"{base}/api/embed"

    def urllib_call():
        import urllib.request  # noqa: PLC0415
        req = urllib.request.Request(embed, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()

    def httpx_like(lib):
        def sync():
            mod = __import__(lib)
            with mod.Client(timeout=10) as c:
                c.post(embed, content=body)

        def asyn():
            mod = __import__(lib)

            async def go():
                async with mod.AsyncClient(timeout=10) as c:
                    await c.post(embed, content=body)
            asyncio.run(go())
        return sync, asyn

    def openai_call():
        import openai  # noqa: PLC0415
        #: the SDK refuses to build a client without a key; the fake server ignores it. A
        #: placeholder, never a credential - passed through the environment the SDK reads.
        os.environ.setdefault("OPENAI_API_KEY", "selftest-placeholder")
        openai.OpenAI(base_url=f"{base}/v1", max_retries=0).embeddings.create(model="m", input="x")

    def requests_call():
        import requests  # noqa: PLC0415
        requests.post(embed, data=body, timeout=10)

    def aiohttp_call():
        import aiohttp  # noqa: PLC0415

        async def go():
            async with aiohttp.ClientSession() as s:
                async with s.post(embed, data=body) as r:
                    await r.read()
        asyncio.run(go())

    clients = {"urllib": ("stdlib", urllib_call, "paced")}
    for lib in ("httpx", "httpx2"):
        s, a = httpx_like(lib)
        clients[f"{lib}.Client"] = (lib, s, "paced")
        clients[f"{lib}.AsyncClient"] = (lib, a, "paced")
    clients["openai"] = ("openai", openai_call, "paced")
    clients["requests"] = ("requests", requests_call, "bypass")
    clients["aiohttp"] = ("aiohttp", aiohttp_call, "bypass")

    report: dict = {"python": sys.version.split()[0], "executable": sys.executable, "clients": {}}
    import importlib.util  # noqa: PLC0415
    for name, (lib, fn, expected) in clients.items():
        if lib != "stdlib" and importlib.util.find_spec(lib) is None:
            report["clients"][name] = {"present": False}
            continue
        before_hits, snap = len(_FakeOllama.hits), pacer.snapshot()
        err = None
        try:
            fn()
        except Exception as e:                        # noqa: BLE001 - reported, not hidden
            err = f"{type(e).__name__}: {str(e)[:160]}"
        reached = len(_FakeOllama.hits) > before_hits
        out: dict = {}
        pacer.attach(out, since=snap)
        tr = out.get("ollama_transport") or {}
        seen = ("paced" if tr.get("calls", 0) > 0 else
                "bypass" if sum((tr.get("bypass_calls") or {}).values()) > 0 else None)
        report["clients"][name] = {"present": True, "reached": reached, "seen": seen,
                                   "expected": expected, "error": err}
    pacer.uninstall()
    srv.shutdown()
    unseen = [n for n, r in report["clients"].items() if r.get("present") and r.get("reached")
              and r.get("seen") is None]
    report["unseen"] = unseen
    report["ok"] = not unseen
    return report


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    rep = probe()
    if "--json" in argv:
        print(json.dumps(rep, indent=1))
    else:
        print(f"pacer selftest under {rep['executable']} (Python {rep['python']})")
        for name, r in rep["clients"].items():
            if not r.get("present"):
                print(f"  -      {name}: not installed here")
                continue
            mark = "ok  " if (r["seen"] or not r["reached"]) else "MISS"
            print(f"  {mark}  {name}: reached={r['reached']} seen={r['seen']} (expected {r['expected']})"
                  + (f" error={r['error']}" if r["error"] else ""))
        print("OK: every client that reached the server was seen" if rep["ok"]
              else f"FAIL: unseen transport(s): {', '.join(rep['unseen'])}")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
