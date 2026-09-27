#!/usr/bin/env python3
"""PREREG-V3 TB4.11a A3 (A6): research/v3/sched_ctl.py - the scheduler's control clients, against stdlib fakes on
127.0.0.1 (never the machine's Ollama port):

* ProxyControl: the bearer token on every call; stage(block, stage) posts exactly {"block", "stage"} with a stage of
  scheduler.STAGES, or both None (the reset between blocks); an unknown stage, or one of the two without the other,
  refuses before a byte is sent; window() opens with exact hosts and arms, closes with neither; a non-200 answer is
  a ControlError with its status; fetch_window_control adapts launch.fetch_window's proxy_control to /window;
* OllamaCtl: the allowlist - GET /api/ps, POST /api/generate {model, keep_alive: 0}, POST /api/embed {model, input: [],
  keep_alive: 0} - and nothing else, refused before a byte; unload() asks /api/ps first and sends nothing for a model
  it does not name (B-OLW: an embed call would load it), compares names with their tags (B-OLN: "bge-m3" is
  "bge-m3:latest"), and checks /api/ps no longer names the model;
* both clients refuse a host other than 127.0.0.1.

    python tests/research/_test_v3_sched_ctl.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CT = _load("v3_sched_ctl", ROOT / "research" / "v3" / "sched_ctl.py")
SC = _load("v3_scheduler_for_ctl", ROOT / "research" / "v3" / "scheduler.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def err(fn) -> str:
    try:
        fn()
        return "no error"
    except CT.ControlError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"not a ControlError: {type(e).__name__}: {e}"


class Fake:
    """One loopback server: records (method, path, authorization, body); answers per path; an Ollama residency set."""

    def __init__(self) -> None:
        self.seen: list[tuple] = []
        self.status = 200
        self.resident = {"nvt3-bge-m3-d1:latest", "qwen3:8b"}
        self.sticky = False                                   # an unload that does not unload
        fake = self

        class H(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                fake.seen.append((self.command, self.path, self.headers.get("Authorization"), body))
                if self.path == "/api/ps":
                    out = {"models": [{"name": m} for m in sorted(fake.resident)]}
                elif self.path in ("/api/generate", "/api/embed"):
                    obj = json.loads(body or b"{}")
                    if obj.get("keep_alive") == 0 and not fake.sticky:
                        fake.resident.discard(obj.get("model"))
                    out = {"done": True}
                else:
                    out = {"ok": True}
                data = json.dumps(out).encode()
                self.send_response(fake.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _answer

            def log_message(self, *a) -> None:
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


fk = Fake()
try:
    print("- ProxyControl -")
    pc = CT.ProxyControl(fk.port, "ctl-token")
    pc.stage("S1/b01", "write")
    m, path, auth, body = fk.seen[-1]
    check("stage() posts exactly {block, stage} to /stage with the bearer token",
          (m, path, auth) == ("POST", "/stage", "Bearer ctl-token") and json.loads(body) == {"block": "S1/b01", "stage": "write"},
          str(fk.seen[-1]))
    pc.stage(None, None)
    check("stage(None, None) is the reset between blocks", json.loads(fk.seen[-1][3]) == {"block": None, "stage": None})
    n = len(fk.seen)
    check("a stage outside scheduler.STAGES refuses before a byte is sent",
          "stage" in err(lambda: pc.stage("S1/b01", "question")) and len(fk.seen) == n)
    check("a block without a stage, or a stage without a block, refuses", "both" in err(lambda: pc.stage("S1/b01", None))
          and "both" in err(lambda: pc.stage(None, "write")) and len(fk.seen) == n)
    check("the stages are the scheduler's", CT.STAGES == SC.STAGES)
    pc.window("a7-x", "open", ["registry.npmjs.org"], ["catcher-arm"])
    check("window open posts its name, state, exact hosts and arms",
          json.loads(fk.seen[-1][3]) == {"name": "a7-x", "state": "open", "hosts": ["registry.npmjs.org"],
                                         "arms": ["catcher-arm"]} and fk.seen[-1][1] == "/window")
    pc.window("a7-x", "close")
    check("window close posts its name and state only", json.loads(fk.seen[-1][3]) == {"name": "a7-x", "state": "close"})
    n = len(fk.seen)
    check("an open window without hosts or arms refuses before a byte", "hosts" in err(lambda: pc.window("w", "open", [], ["a"]))
          and "arms" in err(lambda: pc.window("w", "open", ["h.org"], [])) and len(fk.seen) == n)
    check("a state other than open / close refuses", "state" in err(lambda: pc.window("w", "ajar")))
    check("counters, flags and ollama are GETs with the token",
          pc.counters() == {"ok": True} and fk.seen[-1][:3] == ("GET", "/counters", "Bearer ctl-token")
          and pc.flags() == {"ok": True} and fk.seen[-1][1] == "/flags" and pc.ollama() == {"ok": True})
    got = pc.shutdown() if hasattr(pc, "shutdown") else "no shutdown()"
    check("R-FSYNC: shutdown() posts {} to /shutdown with the bearer token - the proxy stops itself, never killed",
          got == {"ok": True} and fk.seen[-1][:3] == ("POST", "/shutdown", "Bearer ctl-token")
          and json.loads(fk.seen[-1][3]) == {}, f"{got} {fk.seen[-1]}")
    fwc = CT.fetch_window_control(pc, ["catcher-arm"])
    fwc("open", "a7-y", ["api.github.com"])
    fwc("close", "a7-y", ["api.github.com"])
    check("fetch_window_control: launch.fetch_window's (action, name, hosts) become /window open and close",
          [json.loads(b)["state"] for _m, p_, _a, b in fk.seen[-2:]] == ["open", "close"]
          and json.loads(fk.seen[-2][3])["arms"] == ["catcher-arm"])
    fk.status = 401
    check("a non-200 answer is a ControlError with its status", "401" in err(lambda: pc.counters()))
    fk.status = 200
    check("a host other than 127.0.0.1 refuses", "127.0.0.1" in err(lambda: CT.ProxyControl(fk.port, "t", host="10.0.0.2")))
    check("an empty token refuses - the control port needs its bearer token", "token" in err(lambda: CT.ProxyControl(fk.port, "")))

    print("\n- OllamaCtl -")
    oc = CT.OllamaCtl(port=fk.port)
    check("ps() names the resident models", oc.ps() == ["nvt3-bge-m3-d1:latest", "qwen3:8b"]
          and fk.seen[-1][:2] == ("GET", "/api/ps"))
    oc.unload("qwen3:8b")
    gen = [s for s in fk.seen if s[1] == "/api/generate"]
    check("unload() of a generation model posts exactly {model, keep_alive: 0} to /api/generate - no prompt",
          len(gen) == 1 and json.loads(gen[0][3]) == {"model": "qwen3:8b", "keep_alive": 0}, str(gen))
    oc.unload("nvt3-bge-m3-d1:latest", embedder=True)
    emb = [s for s in fk.seen if s[1] == "/api/embed"]
    check("unload() of an embedding tag posts {model, input: [], keep_alive: 0} to /api/embed (R5)",
          len(emb) == 1 and json.loads(emb[0][3]) == {"model": "nvt3-bge-m3-d1:latest", "input": [], "keep_alive": 0})
    check("and each unload is checked on /api/ps", oc.ps() == [] and fk.seen[-2][1] == "/api/ps")
    fk.resident, fk.sticky = {"qwen3:8b"}, True
    check("an unload /api/ps still names is a ControlError (one resident model per stage, §5.6)",
          "still resident" in err(lambda: oc.unload("qwen3:8b")))
    fk.sticky = False
    fk.resident = set()
    n = len(fk.seen)
    got = oc.unload("nvt3-bge-m3-d1:latest", embedder=True)
    check("B-OLW: a model /api/ps does not name is never sent an unload - no POST at all (an embed call would load it)",
          got == "not resident" and [s[1] for s in fk.seen[n:]] == ["/api/ps"], str(fk.seen[n:]))
    fk.resident = {"bge-m3:latest"}
    n = len(fk.seen)
    got = oc.unload("bge-m3", embedder=True)
    check("B-OLN: a name without its tag is the :latest one - the resident model is unloaded, and checked",
          got == "unloaded" and fk.resident == set()
          and [json.loads(s[3])["model"] for s in fk.seen[n:] if s[1] == "/api/embed"] == ["bge-m3:latest"], str(fk.seen[n:]))
    fk.resident, fk.sticky = {"bge-m3:latest"}, True
    check("B-OLN: ... and a tagless name still resident after its unload is caught",
          "still resident" in err(lambda: oc.unload("bge-m3", embedder=True)))
    fk.sticky = False
    check("the name rule: a tag stays, a missing one is :latest (a registry path's last segment decides)",
          CT.full_name("bge-m3") == "bge-m3:latest" and CT.full_name("qwen3:8b") == "qwen3:8b"
          and CT.full_name("hf.co/org/model") == "hf.co/org/model:latest"
          and CT.full_name("localhost:5000/ns/m") == "localhost:5000/ns/m:latest")
    n = len(fk.seen)
    check("anything outside the allowlist refuses before a byte: a pull, a generation with a prompt, a keep_alive > 0",
          "allowlist" in err(lambda: oc._request("POST", "/api/pull", {"model": "x"}))
          and "allowlist" in err(lambda: oc._request("POST", "/api/generate", {"model": "x", "keep_alive": 0, "prompt": "hi"}))
          and "allowlist" in err(lambda: oc._request("POST", "/api/generate", {"model": "x", "keep_alive": "5m"}))
          and "allowlist" in err(lambda: oc._request("POST", "/api/embed", {"model": "x", "input": [], "keep_alive": "5m"}))
          and "allowlist" in err(lambda: oc._request("POST", "/api/embed", {"model": "x", "input": ["hi"], "keep_alive": 0}))
          and "allowlist" in err(lambda: oc._request("GET", "/api/tags", None)) and len(fk.seen) == n)
    check("a host other than 127.0.0.1 refuses", "127.0.0.1" in err(lambda: CT.OllamaCtl(host="192.168.1.5")))
finally:
    fk.close()

print(f"\nv3 sched ctl: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
