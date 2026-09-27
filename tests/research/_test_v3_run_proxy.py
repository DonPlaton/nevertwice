#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A1: research/v3/run_v3_proxy.py - the proxy's config and stdin secrets, its start under the launch
contract (a REAL proxy process in front of a loopback fake upstream, a sentinel key file outside the secrets root),
the unit URLs, and its own stop (the auditor's Q-12-1, R-CC-WIT, R-FSYNC):

* PX-config-loads: the real ProxyConfig.load accepts the built config with the built secrets;
* PX-pins: every arm port and reader port is pinned to deepseek-flash, the scheduler's too; J3 (deepseek-v4-pro) only
  when asked;
* PX-tools: an arm's tools are launch.TOOLS_ALLOWED_BASELINE[arm]; an arm the baseline does not know, or a role's
  name, is refused;
* PX-catcher: the harness catcher is one catch-mode pseudo-arm (Q-12-1 O-b), and its URL is a plain HTTP proxy URL;
* PX-cc-canary: the Claude Code arm without its home canary is refused before any spawn;
* PX-no-secret-on-disk: no token, control token or canary value is in the config file, the spawn records or any
  file the run wrote; a config holding one is refused;
* PX-url: /u/<run>.<unit> on the right port; a run id with a dot, an unknown role or a missing port is refused;
* PX-roundtrip: a call on an arm's reader port reaches the fake upstream and is recorded with its arm, port_role
  reader and unit r1.u1, which accounting splits into (r1, u1);
* PX-shutdown: stop() asks the proxy to stop itself; it exits 0 and is not killed.

    python tests/research/_test_v3_run_proxy.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
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


RP = _load("v3_run_proxy_t", ROOT / "research" / "v3" / "run_v3_proxy.py")
L = RP._launch()                   # the SAME launch module: its fresh-directory set is per module
P = _load("v3_llm_proxy_for_run_proxy_t", ROOT / "research" / "_llm_proxy.py")
ST = _load("v3_llm_proxy_selftest_for_run_proxy_t", ROOT / "research" / "_llm_proxy_selftest.py")
A = _load("v3_accounting_for_run_proxy_t", ROOT / "research" / "v3" / "accounting.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def refused(fn) -> str:
    """The refusal's text, or 'accepted'."""
    try:
        fn()
        return "accepted"
    except RP.ProxyPlanError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001 - any other failure is not the named refusal: the row FAILs by name
        return f"not refused by the plan: {type(e).__name__}: {e}"


TMP = Path(tempfile.mkdtemp(prefix="nvt3_runproxy_"))
_TEST_EXC = {sys.executable: "the test interpreter"}
if getattr(sys, "_base_executable", sys.executable) != sys.executable:
    _TEST_EXC[sys._base_executable] = "the test interpreter's base"
CAN = "a" * 32
CC = "claude-code-memory"
ARMS = {"mem0": {"reader": True}, CC: {}}
up = None
h = None
try:
    print("- the config and the secrets -")
    cfg = RP.build_config(ARMS, run_dir=TMP / "cfgcheck", test_upstream={"host": "127.0.0.1", "port": 9, "tls": False})
    sec = RP.build_secrets(list(ARMS), home_canaries={CC: CAN})
    (TMP / "cfgcheck").mkdir()
    (TMP / "cfgcheck" / "c.json").write_text(json.dumps(cfg), encoding="utf-8")
    try:
        pc = P.ProxyConfig.load(TMP / "cfgcheck" / "c.json", sec, test_upstream_ok=True)
        loaded = {a.arm: a for a in pc.arms}
        err = None
    except (ValueError, KeyError) as e:
        loaded, err = {}, e
    check("PX-config-loads: the real ProxyConfig.load accepts the built config and secrets",
          err is None and set(loaded) == {"mem0", CC, RP.HARNESS_CATCHER, "scheduler"}, repr(err))
    check("PX-pins: every arm port and reader port is pinned to deepseek-flash, the scheduler's port too",
          all(loaded[a].pinned_model == "deepseek-flash" for a in ("mem0", CC, "scheduler"))
          and loaded["mem0"].reader_model == "deepseek-flash" and loaded[CC].reader_model == ""
          if not err else False, str({a: (c.pinned_model, c.reader_model) for a, c in loaded.items()}))
    j3cfg = RP.build_config(ARMS, run_dir=TMP / "x", j3=True)
    check("PX-pins: J3 only when asked, pinned to deepseek-v4-pro", "j3" not in cfg
          and j3cfg.get("j3") == {"pinned_model": "deepseek-v4-pro"}, str(j3cfg.get("j3")))
    check("PX-tools: an arm's tools are exactly launch.TOOLS_ALLOWED_BASELINE[arm]",
          all(sorted(loaded[a].tools_allowed) == sorted(L.TOOLS_ALLOWED_BASELINE[a]) for a in ("mem0", CC))
          and set(loaded[CC].tools_allowed) == set(L.TOOLS_ALLOWED_BASELINE[CC]) if not err else False,
          str({a: loaded[a].tools_allowed for a in ("mem0", CC)} if not err else err))
    r_unknown = refused(lambda: RP.build_config({"mystery-arm": {}}, run_dir=TMP / "x"))
    r_role = refused(lambda: RP.build_config({"judge": {}}, run_dir=TMP / "x"))
    r_opt = refused(lambda: RP.build_config({"mem0": {"tools": ["Bash"]}}, run_dir=TMP / "x"))
    check("PX-tools: an arm the baseline does not know, a role's name as an arm, an unknown option - each refused",
          "TOOLS_ALLOWED_BASELINE" in r_unknown and "role" in r_role and "unknown options" in r_opt,
          f"{r_unknown} | {r_role} | {r_opt}")
    catch = [a for a in cfg["arms"] if a["arm"] == RP.HARNESS_CATCHER]
    check("PX-catcher: the harness catcher is one catch-mode pseudo-arm (Q-12-1 O-b)",
          catch == [{"arm": RP.HARNESS_CATCHER, "mode": "catch"}] and not err
          and loaded[RP.HARNESS_CATCHER].mode == "catch", str(catch))
    check("PX-cc-canary: the Claude Code arm without its home canary is refused before any spawn (R-CC-WIT)",
          "home canary" in refused(lambda: RP.build_secrets(list(ARMS)))
          and "home canary" in refused(lambda: RP.build_secrets(list(ARMS), home_canaries={CC: "short"})),
          refused(lambda: RP.build_secrets(list(ARMS))))
    toks = list(sec["tokens"].values()) + [sec["control_token"]]
    check("the tokens are fresh 128-bit values, one per arm and role, and distinct",
          set(sec["tokens"]) == {"mem0", CC, "scheduler"} and len(set(toks)) == len(toks)
          and all(len(t) == 32 for t in toks), str(sorted(sec["tokens"])))

    print("\n- the real proxy, started through the launch contract -")
    C = L.Contract(polygon_root=TMP / "polygon", runs_root=TMP / "polygon" / "runs" / "v3", repo_root=ROOT,
                   owner_home=TMP / "owner", secrets_dir=TMP / "secrets", quarantine_root=TMP / "quarantine",
                   conservation_root=TMP / "conservation", binary_exceptions=_TEST_EXC,
                   system_dirs=(Path(sys.executable).parent,))
    kdir = TMP / "keys"
    kdir.mkdir()
    kf = kdir / "deepseek.env"
    kf.write_bytes(f"DEEPSEEK_API_KEY={ST.SENTINEL_KEY}\n".encode())
    up = ST.FakeUpstream()
    run_dir = C.runs_root / "_proxy" / "px1"
    cfg = RP.build_config(ARMS, run_dir=run_dir, test_upstream={"host": "127.0.0.1", "port": up.port, "tls": False})
    sec = RP.build_secrets(list(ARMS), home_canaries={CC: CAN})
    bad = dict(cfg, thinking_branch=sec["control_token"])
    check("PX-no-secret-on-disk: a config holding a secret value is refused before anything is written",
          "stdin only" in refused(lambda: RP.start(C, python=sys.executable, key_file=kf, config=bad, secrets=sec,
                                                   unit=None, parent_env=os.environ))
          and not (run_dir / "proxy_config.json").exists())
    unit = L.make_unit_dirs(C, "_proxy", "px1", "proxy", "p1")
    h = RP.start(C, python=sys.executable, key_file=kf, config=cfg, secrets=sec, unit=unit, parent_env=os.environ)
    check("the proxy is READY: write and reader ports for the reading arm, a catcher port per arm, the scheduler's port",
          {"write", "reader", "catcher"} <= set(h.ports["arms"]["mem0"]) and "reader" not in h.ports["arms"][CC]
          and set(h.ports["arms"][RP.HARNESS_CATCHER]) == {"catcher"} and isinstance(h.ports.get("scheduler"), int),
          str(h.ports))
    u = RP.url(h, "mem0", "r1", "u1", role="reader", suffix="/v1")
    check("PX-url: /u/<run>.<unit> on the arm's reader port",
          u == f"http://127.0.0.1:{h.ports['arms']['mem0']['reader']}/u/r1.u1/v1", u)
    check("PX-url: the scheduler's one port, and the catcher's plain proxy URL",
          RP.url(h, "scheduler", "r1", "probe") == f"http://127.0.0.1:{h.ports['scheduler']}/u/r1.probe"
          and RP.catcher_url(h) == f"http://127.0.0.1:{h.ports['arms'][RP.HARNESS_CATCHER]['catcher']}")
    r_dot = refused(lambda: RP.url(h, "mem0", "r.1", "u1"))
    r_role2 = refused(lambda: RP.url(h, "mem0", "r1", "u1", role="catcher"))
    r_missing = refused(lambda: RP.url(h, CC, "r1", "u1", role="reader"))
    check("PX-url: a run id with a dot, the catcher as a unit port, a port the arm does not have - each refused",
          "Q3" in r_dot and "catcher_url" in r_role2 and "no reader port" in r_missing, f"{r_dot} | {r_role2} | {r_missing}")
    status, body = RP.post(h.ports["arms"]["mem0"]["reader"], "/u/r1.u1/v1/chat/completions",
                           {"model": "deepseek-flash", "messages": [{"role": "user", "content": "q"}]},
                           h.tokens["mem0"])
    status2, _b = RP.post(h.ports["arms"]["mem0"]["reader"], "/u/r1.u1/v1/chat/completions",
                          {"model": "deepseek-flash"}, h.tokens[CC])
    check("another arm's token is refused on this arm's port (401)", status2 == 401, str(status2))
    res = RP.stop(h)
    h = None
    check("PX-shutdown: stop() asks the proxy to stop itself - exit 0, not killed", res == {"rc": 0, "killed": False,
                                                                                         "shutdown_error": None}, str(res))
    calls = [json.loads(x) for x in (run_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines()] \
        if (run_dir / "calls.jsonl").exists() else []           # a call's record follows its reply: read after the stop
    fwd = [c for c in calls if c.get("status") == 200]
    last = fwd[-1] if len(fwd) == 1 else {}
    check("PX-roundtrip: a reader call reaches the fake upstream and is recorded with its arm, port_role reader and "
          "unit r1.u1", status == 200 and len(up.requests) == 1 and last.get("arm") == "mem0"
          and last.get("port_role") == "reader" and last.get("unit") == "r1.u1", f"{status} {body} {calls}")
    check("... which accounting splits into (r1, u1)", A.split_unit(last.get("unit") or "") == ("r1", "u1")
          if last.get("unit") else False, str(last.get("unit")))
    values = [*sec["tokens"].values(), sec["control_token"], CAN]
    leaks = [str(f.relative_to(TMP)) for f in TMP.rglob("*") if f.is_file()
             and any(v.encode() in f.read_bytes() for v in values)]
    check("PX-no-secret-on-disk: no token, control token or home canary value in any file the run wrote (config, "
          "spawn records, calls, ports)", leaks == [], str(leaks))
finally:
    if h is not None:
        h.child.kill_tree()
    if up is not None:
        up.close()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 run proxy: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
