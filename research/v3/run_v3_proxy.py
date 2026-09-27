#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A1: the orchestrator's side of the recording proxy (research/_llm_proxy.py) - the config file and
the stdin secrets it starts with, its start under the launch contract, the URLs a unit's children are given, and its
own stop (the auditor's Q-12-1 for the smoke, R-FSYNC).

* build_config: one record-mode arm per product arm, pinned to deepseek-flash, and pinned the same on its reader port
  when the arm reads (§4.3a); its tools are launch.TOOLS_ALLOWED_BASELINE[arm] - a name the baseline does not know, or
  a role's name, is refused. The harness catcher is one catch-mode pseudo-arm (Q-12-1 O-b: the smoke's single
  catcher, declared in its artifact); the scheduler's port (D8's model probe and the balance) always, J3 (pinned
  deepseek-v4-pro) only when asked; the hop and a test upstream only as given. Nothing secret is in it.
* build_secrets: a fresh 128-bit token per arm and role and one for the control port (secrets.token_hex), the canaries,
  the Claude Code arm's home canary (R-CC-WIT: without one the proxy would not start, so it is refused here, before any
  spawn) and the identity when the owner markers are wired (Q-12-2). They go on the proxy's stdin only - never into
  a file, argv or the environment.
* start: the config written into the proxy's run directory (refused if any secret value is in its bytes), then
  launch.spawn_proxy - the contract's only key-file spawn - and a ProxyHandle (the child, its ports, its control
  client, the arm tokens, the run directory).
* url: http://127.0.0.1:<port>/u/<run>.<unit><suffix> for an arm's write, reader or Ollama port, or a role's port - a
  run id with a dot is refused (Q3: accounting splits the prefix at the first dot); catcher_url: the plain HTTP proxy
  URL of a catcher port.
* post: one loopback POST with a bearer token - (status, the JSON body), or (None, the error's type).
* stop: ProxyControl.shutdown - the proxy stops itself, every record already fsynced (R-FSYNC) - then its exit code;
  a proxy that does not exit in time is killed, and that is said (killed=True).
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
import re
import secrets as _secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
PROXY_SCRIPT = HERE.parent / "_llm_proxy.py"
LOOPBACK = "127.0.0.1"
PINNED_MODEL = "deepseek-flash"                # §4.3a: every arm port, every reader port, the scheduler's port
J3_MODEL = "deepseek-v4-pro"                   # §4.3a: J3's own port
HARNESS_CATCHER = "harness-catcher"            # Q-12-1 O-b: the smoke's one catcher, declared in its artifact
ARM_ROLES = ("write", "reader", "catcher", "ollama")
SPECIAL_ROLES = ("scheduler", "j3")            # _llm_proxy.SPECIAL_ROLES: single-port roles, always recorded
CLAUDE_CODE_ARM = "claude-code-memory"         # _llm_proxy.HOME_CANARY_ARM (R-CC-WIT)
_RUN_ID = re.compile(r"[A-Za-z0-9_-]+")
_UNIT_PART = re.compile(r"[A-Za-z0-9._-]+")
_PREFIX_MAX = 128                              # _llm_proxy._UNIT: /u/<prefix of 1..128 characters>
_HOME_CANARY = re.compile(r"[0-9a-f]{32}")
ARM_OPTIONS = frozenset({"reader", "ollama_leg", "cloud_arm", "thinking_route"})


class ProxyPlanError(ValueError):
    """A config, a secret set or a URL the proxy plan does not allow; nothing was started."""


def _load(name: str, path: Path):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def _launch():
    return _load("v3_launch_for_run_proxy", HERE / "launch.py")


def _ctl():
    return _load("v3_sched_ctl_for_run_proxy", HERE / "sched_ctl.py")


def build_config(arms: Mapping[str, Mapping[str, Any]], *, run_dir: str | os.PathLike, thinking_branch: str = "unset",
                 via_port: int | None = None, catcher: str = HARNESS_CATCHER, j3: bool = False,
                 test_upstream: Mapping[str, Any] | None = None) -> dict:
    """The proxy's config file (see the module docstring). ``arms``: name -> options - reader (bool: the arm reads,
    so it gets a reader port), ollama_leg (bool), cloud_arm (bool, default True), thinking_route (documented or
    fallback)."""
    baseline = _launch().TOOLS_ALLOWED_BASELINE
    if not arms:
        raise ProxyPlanError("a proxy with no arm")
    reserved = {*SPECIAL_ROLES, "reader", "judge", catcher}
    out_arms = []
    for name, opts in arms.items():
        if name in reserved:
            raise ProxyPlanError(f"{name!r} is a role's name, not an arm's")
        if name not in baseline:
            raise ProxyPlanError(f"arm {name!r} is not in launch.TOOLS_ALLOWED_BASELINE (§2.6.6) - no tools list, no port")
        extra = sorted(set(opts) - ARM_OPTIONS)
        if extra:
            raise ProxyPlanError(f"arm {name}: unknown options {extra}")
        route = opts.get("thinking_route", "documented")
        if route not in ("documented", "fallback"):
            raise ProxyPlanError(f"arm {name}: thinking_route {route!r} is documented or fallback")
        a = {"arm": name, "mode": "record", "thinking_route": route, "pinned_model": PINNED_MODEL,
             "tools_allowed": sorted(baseline[name]), "ollama_leg": bool(opts.get("ollama_leg", False)),
             "cloud_arm": bool(opts.get("cloud_arm", True))}
        if opts.get("reader"):
            a["reader_model"] = PINNED_MODEL
        out_arms.append(a)
    out_arms.append({"arm": catcher, "mode": "catch"})
    cfg: dict = {"arms": out_arms, "run_dir": os.fspath(run_dir), "thinking_branch": thinking_branch,
                 "scheduler": {"pinned_model": PINNED_MODEL}}
    if j3:
        cfg["j3"] = {"pinned_model": J3_MODEL}
    if via_port is not None:
        cfg["via"] = {"host": LOOPBACK, "port": int(via_port)}
    if test_upstream is not None:
        cfg["upstream"] = dict(test_upstream)              # the proxy itself accepts it only with a test key (X2)
    return cfg


def build_secrets(arm_names: Sequence[str], *, roles: Sequence[str] = ("scheduler",),
                  canaries: Mapping[str, str] | None = None, home_canaries: Mapping[str, str] | None = None,
                  identity: Mapping[str, Any] | None = None) -> dict:
    """The proxy's stdin line (see the module docstring)."""
    home = dict(home_canaries or {})
    if CLAUDE_CODE_ARM in arm_names and not _HOME_CANARY.fullmatch(str(home.get(CLAUDE_CODE_ARM, ""))):
        raise ProxyPlanError(f"{CLAUDE_CODE_ARM} needs its home canary (32 lowercase hex, R-CC-WIT) - the proxy would "
                             f"refuse to start without it")
    stray = sorted(set(home) - set(arm_names))
    if stray:
        raise ProxyPlanError(f"home canaries for arms the proxy does not have: {stray} (F-CAN)")
    bad_roles = sorted(set(roles) - set(SPECIAL_ROLES))
    if bad_roles:
        raise ProxyPlanError(f"roles {bad_roles} are not the proxy's single-port roles {SPECIAL_ROLES}")
    out: dict = {"tokens": {n: _secrets.token_hex(16) for n in [*arm_names, *roles]},
                 "control_token": _secrets.token_hex(16)}
    if canaries:
        out["canaries"] = dict(canaries)
    if home:
        out["home_canaries"] = home
    if identity:
        out["identity"] = dict(identity)
    return out


def _secret_values(secrets: Mapping[str, Any]) -> list[str]:
    vals = [*(secrets.get("tokens") or {}).values(), secrets.get("control_token") or ""]
    vals += [*(secrets.get("home_canaries") or {}).values()]
    canaries = secrets.get("canaries") or {}
    vals += [*(canaries.values() if isinstance(canaries, Mapping) else canaries)]
    return [v for v in vals if isinstance(v, str) and v]


@dataclass
class ProxyHandle:
    """A started proxy: its child, its ports (ports.json), its control client, the arm and role tokens, its run dir."""
    child: Any
    ports: dict
    control: Any
    tokens: Mapping[str, str]
    run_dir: Path


def start(c: Any, *, python: str | os.PathLike, key_file: str | os.PathLike, config: Mapping[str, Any],
          secrets: Mapping[str, Any], unit: Any, parent_env: Mapping[str, str], witnesses: Any = None,
          spawn: Callable[..., tuple] | None = None) -> ProxyHandle:
    """Write the config into its run directory, start the proxy through launch.spawn_proxy, and hand back its handle."""
    data = json.dumps(dict(config), indent=1, sort_keys=True).encode("utf-8")
    leaked = [n for n, v in enumerate(_secret_values(secrets)) if v.encode("utf-8") in data]
    if leaked:
        raise ProxyPlanError("a secret value is in the config's bytes - secrets go on stdin only")
    run_dir = Path(config["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = run_dir / "proxy_config.json"
    cfg_path.write_bytes(data)
    child, ports = (spawn or _launch().spawn_proxy)(
        c, python, script=PROXY_SCRIPT, config_path=cfg_path, key_file=Path(key_file), stdin_secrets=dict(secrets),
        unit=unit, parent_env=parent_env, witnesses=witnesses)
    control = _ctl().ProxyControl(ports["control"], secrets["control_token"])
    return ProxyHandle(child=child, ports=ports, control=control, tokens=dict(secrets["tokens"]), run_dir=run_dir)


def url(h: ProxyHandle, arm: str, run: str, unit: str, *, role: str = "write", suffix: str = "") -> str:
    """The base URL a unit's child is given for one port: /u/<run>.<unit> tags every call with its unit (Q3)."""
    if not (isinstance(run, str) and _RUN_ID.fullmatch(run)):
        raise ProxyPlanError(f"run id {run!r} is not [A-Za-z0-9_-] (Q3: the proxy's prefix is split at the first dot)")
    if not (isinstance(unit, str) and _UNIT_PART.fullmatch(unit)):
        raise ProxyPlanError(f"unit {unit!r} is not [A-Za-z0-9._-]")
    prefix = f"{run}.{unit}"
    if len(prefix) > _PREFIX_MAX:
        raise ProxyPlanError(f"the unit prefix {prefix!r} is longer than the proxy's {_PREFIX_MAX} characters")
    if suffix and not suffix.startswith("/"):
        raise ProxyPlanError(f"a suffix is a path starting with '/', not {suffix!r}")
    if arm in SPECIAL_ROLES:
        if role != "write":
            raise ProxyPlanError(f"{arm} has one port, not a {role} port")
        port = h.ports.get(arm)
    else:
        if role not in ARM_ROLES or role == "catcher":
            raise ProxyPlanError(f"role {role!r} is not one of write, reader, ollama (the catcher is catcher_url's)")
        port = (h.ports.get("arms", {}).get(arm) or {}).get(role)
    if not isinstance(port, int):
        raise ProxyPlanError(f"the proxy has no {role} port for {arm}")
    return f"http://{LOOPBACK}:{port}/u/{prefix}{suffix}"


def catcher_url(h: ProxyHandle, arm: str = HARNESS_CATCHER) -> str:
    """The HTTP proxy URL of a catcher port (a child's HTTP(S)_PROXY): what egresses there is caught and recorded."""
    port = (h.ports.get("arms", {}).get(arm) or {}).get("catcher")
    if not isinstance(port, int):
        raise ProxyPlanError(f"the proxy has no catcher port for {arm}")
    return f"http://{LOOPBACK}:{port}"


def post(port: int, path: str, body: Any, token: str, *, timeout: float = 60.0) -> tuple[int | None, Any]:
    """One POST to a loopback port with a bearer token: (status, the parsed JSON body - or the raw text when it is
    not JSON), or (None, {"error": <the exception's type>}) when nothing came back."""
    conn = http.client.HTTPConnection(LOOPBACK, int(port), timeout=timeout)
    try:
        conn.request("POST", path, body=json.dumps(body).encode("utf-8"),
                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        resp = conn.getresponse()
        raw = resp.read()
        try:
            return resp.status, json.loads(raw or b"null")
        except ValueError:
            return resp.status, raw.decode("utf-8", "replace")
    except (OSError, http.client.HTTPException) as e:
        return None, {"error": type(e).__name__}
    finally:
        conn.close()


def stop(h: ProxyHandle, *, timeout: float = 30.0) -> dict:
    """Ask the proxy to stop itself (its records are already on the disk, R-FSYNC) and wait for it:
    {"rc": its exit code or None, "killed": whether it had to be killed, "shutdown_error": what /shutdown said}."""
    err = None
    try:
        h.control.shutdown()
    except Exception as e:  # noqa: BLE001 - a proxy that is gone answers nothing; it is still waited for below
        err = type(e).__name__
    try:
        return {"rc": h.child.process.wait(timeout), "killed": False, "shutdown_error": err}
    except subprocess.TimeoutExpired:
        h.child.kill_tree()
        try:
            rc = h.child.process.wait(10.0)
        except subprocess.TimeoutExpired:
            rc = None
        return {"rc": rc, "killed": True, "shutdown_error": err}
