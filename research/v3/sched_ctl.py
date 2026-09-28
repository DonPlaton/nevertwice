#!/usr/bin/env python3
"""PREREG-V3 TB4.11a A3 (A6): the scheduler's two control clients - the recording proxy's control port and the local
Ollama's model residency - each refusing, before a byte is sent, anything outside what the scheduler may ask.

* ProxyControl: 127.0.0.1 only, the bearer token on every call. stage(block, stage) posts exactly {"block", "stage"}:
  a stage of scheduler.STAGES with its block, or both None - the reset between blocks, after which a stray call is
  loud in accounting instead of being charged to a stage. window(name, state, hosts, arms): open needs the exact
  hosts and the arms it is for, close needs neither. counters(), flags(), ollama(), health() are GETs; shutdown()
  posts {} to /shutdown - the proxy stops itself (R-FSYNC).
  fetch_window_control(pc, arms) adapts launch.fetch_window's proxy_control(action, name, hosts) to /window.
* OllamaCtl: 127.0.0.1 only. The allowlist is GET /api/ps, POST /api/generate {model, keep_alive: 0} and POST
  /api/embed {model, input: [], keep_alive: 0} - an unload and nothing else (R5: an embedding-only tag is unloaded
  through /api/embed). ps() names the resident models. unload() asks /api/ps FIRST and sends nothing for a model it
  does not name (B-OLW: an embed call for a model that is not loaded would load it into the owner's VRAM); names are
  compared with their tags - a tagless name is the ":latest" one (B-OLN); after an unload /api/ps must stop naming
  the model within UNLOAD_WAIT_S (§5.6: one resident Ollama model per stage). B-OLA: Ollama answers the unload at
  once (expireRunner) and frees the runner afterwards, after its VRAM recovery wait - /api/ps names the model until
  then, so the check asks /api/ps again every UNLOAD_POLL_S, never once. No generation is ever asked: a body with any
  other field refuses.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

HERE = Path(__file__).resolve().parent
LOOPBACK = "127.0.0.1"
TIMEOUT_S = 30.0
OLLAMA_PORT = 11434
UNLOAD_WAIT_S, UNLOAD_POLL_S = 30.0, 0.25          # B-OLA: how long an unloaded model may still be named by /api/ps


def _scheduler():
    mod = sys.modules.get("v3_scheduler_for_ctl")
    if mod is None:
        spec = importlib.util.spec_from_file_location("v3_scheduler_for_ctl", HERE / "scheduler.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["v3_scheduler_for_ctl"] = mod
        spec.loader.exec_module(mod)
    return mod


STAGES = _scheduler().STAGES


class ControlError(RuntimeError):
    """A control call outside the allowlist, or an answer other than 200; nothing, or nothing more, was sent."""


def _http(host: str, port: int, method: str, path: str, body: Any, headers: dict, timeout: float) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        h = dict(headers, **({"Content-Type": "application/json"} if data is not None else {}))
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status != 200:
            raise ControlError(f"{method} {path}: {resp.status} {resp.reason}")
        return json.loads(raw or b"null")
    finally:
        conn.close()


def _loopback(host: str) -> str:
    if host != LOOPBACK:
        raise ControlError(f"a control client talks to {LOOPBACK} only, not {host!r}")
    return host


class ProxyControl:
    """The recording proxy's control port."""

    def __init__(self, port: int, token: str, *, host: str = LOOPBACK, timeout: float = TIMEOUT_S) -> None:
        self.host, self.port, self.timeout = _loopback(host), int(port), timeout
        if not token:
            raise ControlError("the control port needs its bearer token")
        self._auth = {"Authorization": f"Bearer {token}"}

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        return _http(self.host, self.port, method, path, body, self._auth, self.timeout)

    def stage(self, block: str | None, stage: str | None) -> None:
        if (block is None) != (stage is None):
            raise ControlError("a stage names both its block and its stage, or neither (the reset)")
        if stage is not None and stage not in STAGES:
            raise ControlError(f"stage {stage!r} is not one of {STAGES}")
        self._call("POST", "/stage", {"block": block, "stage": stage})

    def window(self, name: str, state: str, hosts: Sequence[str] = (), arms: Sequence[str] = ()) -> Any:
        if state == "open":
            if not hosts:
                raise ControlError(f"window {name}: an open window names its exact hosts")
            if not arms:
                raise ControlError(f"window {name}: an open window names the arms it is for")
            return self._call("POST", "/window", {"name": name, "state": "open", "hosts": list(hosts), "arms": list(arms)})
        if state == "close":
            return self._call("POST", "/window", {"name": name, "state": "close"})
        raise ControlError(f"window state {state!r} is open or close")

    def health(self) -> Any:
        return self._call("GET", "/health")

    def counters(self) -> Any:
        return self._call("GET", "/counters")

    def flags(self) -> Any:
        return self._call("GET", "/flags")

    def ollama(self) -> Any:
        return self._call("GET", "/ollama")

    def shutdown(self) -> Any:
        """The proxy's own stop (R-FSYNC): every record it wrote is already fsynced; the orchestrator never kills it."""
        return self._call("POST", "/shutdown", {})


def fetch_window_control(pc: ProxyControl, arms: Sequence[str]) -> Callable[[str, str, Sequence[str]], None]:
    """launch.fetch_window's proxy_control(action, name, hosts), as /window calls for these arms."""
    def control(action: str, name: str, hosts: Sequence[str]) -> None:
        if action == "open":
            pc.window(name, "open", hosts, arms)
        elif action == "close":
            pc.window(name, "close")
        else:
            raise ControlError(f"fetch_window action {action!r} is open or close")
    return control


def full_name(model: str) -> str:
    """Ollama's name with its tag: a tagless name is the ":latest" one (the tag follows the last path segment)."""
    return model if ":" in model.rsplit("/", 1)[-1] else f"{model}:latest"


class OllamaCtl:
    """The local Ollama, for residency only: which models are loaded, and unloading one."""

    def __init__(self, *, port: int = OLLAMA_PORT, host: str = LOOPBACK, timeout: float = TIMEOUT_S,
                 unload_wait_s: float = UNLOAD_WAIT_S) -> None:
        self.host, self.port, self.timeout = _loopback(host), int(port), timeout
        self.unload_wait_s = unload_wait_s

    @staticmethod
    def _allowed(method: str, path: str, body: Any) -> bool:
        if (method, path) == ("GET", "/api/ps"):
            return body is None
        if method != "POST" or not isinstance(body, dict) or not isinstance(body.get("model"), str) or not body["model"]:
            return False
        if path == "/api/generate":
            return set(body) == {"model", "keep_alive"} and body["keep_alive"] == 0
        if path == "/api/embed":
            return set(body) == {"model", "input", "keep_alive"} and body["input"] == [] and body["keep_alive"] == 0
        return False

    def _request(self, method: str, path: str, body: Any) -> Any:
        if not self._allowed(method, path, body):
            raise ControlError(f"{method} {path} is outside the allowlist (ps, and an unload with keep_alive 0)")
        return _http(self.host, self.port, method, path, body, {}, self.timeout)

    def ps(self) -> list[str]:
        out = self._request("GET", "/api/ps", None)
        return sorted(m.get("name") for m in (out or {}).get("models") or [] if isinstance(m, dict))

    def unload(self, model: str, *, embedder: bool = False) -> str:
        """"not resident" (nothing sent) or "unloaded" (sent, and /api/ps asked until it no longer names the model -
        at most unload_wait_s: Ollama frees the runner after it has answered the unload, B-OLA)."""
        name = full_name(model)
        if name not in {full_name(m) for m in self.ps()}:
            return "not resident"
        if embedder:
            self._request("POST", "/api/embed", {"model": name, "input": [], "keep_alive": 0})
        else:
            self._request("POST", "/api/generate", {"model": name, "keep_alive": 0})
        deadline = time.monotonic() + self.unload_wait_s
        while name in {full_name(m) for m in self.ps()}:
            if time.monotonic() >= deadline:
                raise ControlError(f"{name} is still resident {self.unload_wait_s:g} s after its unload (§5.6: one "
                                   f"resident model per stage)")
            time.sleep(UNLOAD_POLL_S)
        return "unloaded"
