#!/usr/bin/env python3
"""PREREG-V3 TB9, step A2.3: the Claude Code arm runs locked down, and no spawn may name a bypass mode.

* The argv is exactly §2.6.6's: ``-p --output-format json --permission-mode default --setting-sources user
  --settings <unit settings> --strict-mcp-config --mcp-config <empty>``, ``--allowedTools`` exactly the four
  memory-directory rules, ``--disallowedTools`` every other offered tool.
* The unit settings disable all hooks and carry no defaultMode but "default".
* bypassPermissions, acceptEdits and --dangerously-skip-permissions are refused anywhere and in any case: an argv
  word, the ``--permission-mode=`` form, a nested settings value - and on every spawn, not only Claude Code's; a
  refused spawn never reaches Popen.
* ``tool_violation`` flags a name outside an arm's set or matching a forbidden pattern (Bash, mcp__x, WebFetch,
  TaskCreate, run_code, any case); no allowed name in the §2.6.6 table matches a forbidden pattern.
* The auditor's L1: the argv must EQUAL the canonical one and the settings must EQUAL the unit settings - a second
  --permission-mode, --add-dir, a second --mcp-config or --settings, the --allowed-tools alias, an emptied
  --disallowedTools, and settings keys like permissions.allow, apiKeyHelper, statusLine, env or
  additionalDirectories are all refused; once A8 records the binary's tools, --disallowedTools must match them.
* L2: spawn itself refuses a Claude Code spawn - found by arm, role or the binary's name - unless the lockdown
  passes, no CLAUDE.md sits in an ancestor of the runs tree, CLAUDE_CONFIG_DIR is a fresh directory in the unit's
  fake home holding exactly our settings.json (no CLAUDE.md, no .credentials.json there: D4/D5), the binary is the
  polygon-pinned one (its version recorded), a node + cli.js launch of the package is recognised whatever its
  label (D6), and A8 has recorded the tools that exact pinned file offers, by its sha256 (D7).
* R-CC-WIT: the unit's settings.json carries the home canary header as its only env entry (the proxy checks it on
  every request), a settings file without it or with another shape is refused; the fake home holds a decoy
  .claude.json beside the .claude decoys.
* Q-47-6: the sessions of one claude-code-memory unit run in that unit's directory one after another (Claude Code binds
  its memory to the project's working directory); another unit, another arm, a directory that is not empty, or a
  session while the previous one still runs is refused; each reuse is a line of the spawn journal.
* Q-47-7 / Q-C5-6 (B-C5-1): one CLAUDE_CONFIG_DIR per unit - exactly our settings.json on its first session (D4/D5);
  a later session holds settings.json byte-equal plus only the top-level names A8 recorded for this binary; CLAUDE.md,
  .credentials.json, hooks, agents, commands and settings.local.json are refused anywhere, in any case, always, and so
  is a link or junction; the probe-only discovery mode (role=probe, stand=_a8) records every other name - name, kind,
  size, sha256 - instead, and runs before the offered-tools record (D7 waived there, Q-C5-7 attempt 2, B-C5-2); the
  first spawn's init-event tools must equal the record, else blocked:unsupported-surface and the record is withdrawn.

    python tests/_test_v3_launch_claude.py
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
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite

#: This interpreter, and its base when it is a venv (the bare CI-like interpreter is one): both named exceptions,
#: or B1 (a venv's base must be in the polygon) refuses the test's own interpreter - correctly.
_TEST_EXC = {sys.executable: "the test interpreter"}
if getattr(sys, "_base_executable", sys.executable) != sys.executable:
    _TEST_EXC[sys._base_executable] = "the test interpreter's base"

_spec = importlib.util.spec_from_file_location("v3_launch_c", ROOT / "research" / "v3" / "launch.py")
L = importlib.util.module_from_spec(_spec)
sys.modules["v3_launch_c"] = L
_spec.loader.exec_module(L)

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_claude_"))
MEM = TMP / "u.home" / "memory"
BIN = TMP / "claude.exe"
SETTINGS, EMPTY = TMP / "settings.json", TMP / "empty-mcp.json"
OFFERED = ["Read", "Write", "Edit", "MultiEdit", "Bash", "WebFetch", "WebSearch", "Task", "Glob", "Grep",
           "NotebookEdit", "TodoWrite"]

print("\n- the fixed argv and settings -")
argv = L.claude_code_argv(BIN, settings_path=SETTINGS, empty_mcp_path=EMPTY, memdir=MEM, offered_tools=OFFERED)
root = MEM.as_posix()
want = [str(BIN), "-p", "--output-format", "json", "--permission-mode", "default", "--setting-sources", "user",
        "--settings", str(SETTINGS), "--strict-mcp-config", "--mcp-config", str(EMPTY), "--allowedTools",
        f"Read({root}/**)", f"Write({root}/**)", f"Edit({root}/**)", f"MultiEdit({root}/**)", "--disallowedTools",
        *sorted(t for t in OFFERED if t not in ("Read", "Write", "Edit", "MultiEdit"))]
check("the argv is exactly §2.6.6's", argv == want, str(argv))
HC_CAN = "0f" * 16
st = L.claude_code_settings(MEM, HC_CAN)
check("the settings disable all hooks and keep the default mode", st.get("disableAllHooks") is True
      and st.get("permissions", {}).get("defaultMode") == "default")
check("the built argv and settings pass the check", L.check_claude_code(argv, st, memdir=MEM) == [],
      str(L.check_claude_code(argv, st, memdir=MEM)))

print("\n- bypass modes are refused anywhere, in any case -")


def found(av, stt=None) -> list[str]:
    return L.check_claude_code(av, st if stt is None else stt, memdir=MEM)


cases = {
    "--dangerously-skip-permissions appended": argv + ["--dangerously-skip-permissions"],
    "--permission-mode bypassPermissions": [a if a != "default" else "bypassPermissions" for a in argv],
    "--permission-mode=acceptEdits": argv + ["--permission-mode=acceptEdits"],
    "BYPASSPERMISSIONS in upper case": argv + ["BYPASSPERMISSIONS"],
}
for label, av in cases.items():
    check(f"argv: {label} is refused", any("bypass" in f for f in found(av)), str(found(av)))
for label, stt in (("nested defaultMode bypassPermissions", {"disableAllHooks": True, "permissions": {"defaultMode": "bypassPermissions"}}),
                   ("acceptEdits deep in a list", {"disableAllHooks": True, "x": [{"y": "AcceptEdits"}]}),
                   ("a missing disableAllHooks", {"permissions": {"defaultMode": "default"}}),
                   ("defaultMode plan", {"disableAllHooks": True, "permissions": {"defaultMode": "plan"}})):
    check(f"settings: {label} is refused", found(argv, stt) != [], str(found(argv, stt)))
check("a missing --strict-mcp-config is refused", "missing --strict-mcp-config" in found([a for a in argv if a != "--strict-mcp-config"]))
wider = [a if not a.startswith("Read(") else "Read(D:/**)" for a in argv]
check("--allowedTools wider than the memory directory is refused",
      any("allowedTools" in f for f in found(wider)), str(found(wider)))

print("\n- every spawn refuses a bypass mode, before Popen -")
C = L.Contract(polygon_root=TMP / "polygon", runs_root=TMP / "polygon" / "runs" / "v3", repo_root=ROOT,
               owner_home=TMP / "owner", secrets_dir=TMP / "secrets", quarantine_root=TMP / "quarantine",
               conservation_root=TMP / "conservation", binary_exceptions=_TEST_EXC,
               system_dirs=(Path(sys.executable).parent,))
check("assert_argv refuses a bypass word on any spawn", any("bypass" in v for v in L.assert_argv(C, [sys.executable, "--x", "acceptEdits"])))
def _raises(fn, exc) -> bool:
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


called = []


class _Popen:
    running = False                                  # Q-47-6: a session that has not exited yet

    def __init__(self, *a, **kw):
        called.append(a)
        self._running = _Popen.running              # fixed when the session starts

    def poll(self):
        return None if self._running else 0


u = L.make_unit_dirs(C, "s", "r", "cc", "u1")
env = L.build_env(C, parent_env=os.environ, unit=u, path_dirs=[], declared={}, catcher_url="http://127.0.0.1:47004")
try:
    L.spawn(C, [sys.executable, "--dangerously-skip-permissions"], env=env, cwd=u.cwd, record={"role": "claude-code"},
            parent_env=os.environ, catcher_url="http://127.0.0.1:47004", popen=_Popen, requirement="optional",
            unwitnessed_reason="lockdown suite: never started")
    check("a bypass spawn is refused", False)
except L.ContractViolation:
    check("a bypass spawn is refused, and Popen is never called", called == [])

print("\n- tool names -")
for name in ("Bash", "bash", "mcp__filesystem__read", "WebFetch", "TaskCreate", "run_code", "PowerShell", "BrowserTool"):
    check(f"{name} is a violation for every arm", all(L.tool_violation(name, s) for s in L.TOOLS_ALLOWED_BASELINE.values()))
check("an allowed name is not a violation for its arm (letta archival_memory_insert, claude Read)",
      not L.tool_violation("archival_memory_insert", L.TOOLS_ALLOWED_BASELINE["letta"])
      and not L.tool_violation("Read", L.TOOLS_ALLOWED_BASELINE["claude-code-memory"]))
missed = [s for s in L.FORBIDDEN_TOOL_SUBSTRINGS if not L.tool_violation(f"X{s.upper()}y", {f"X{s.upper()}y"})]
check("every forbidden pattern wins even over a name in the arm's own set (task, bash, ... in any case)",
      missed == [] and len(L.FORBIDDEN_TOOL_SUBSTRINGS) == 20, str(missed))
check("the mcp__ prefix wins over the arm's own set", L.tool_violation("mcp__x", {"mcp__x"}))
check("a name outside the arm's set is a violation (Read for letta)", L.tool_violation("Read", L.TOOLS_ALLOWED_BASELINE["letta"]))
clash = [n for s in L.TOOLS_ALLOWED_BASELINE.values() for n in s
         if n.lower().startswith(L.FORBIDDEN_TOOL_PREFIX) or any(f in n.lower() for f in L.FORBIDDEN_TOOL_SUBSTRINGS)]
check("no allowed name in the §2.6.6 table matches a forbidden pattern", clash == [], str(clash))

print("\n- L1: exact, not piecemeal -")
cases = {
    "a second --permission-mode auto": argv + ["--permission-mode", "auto"],
    "--add-dir C:/": argv + ["--add-dir", "C:/"],
    "a second --mcp-config": argv + ["--mcp-config", str(TMP / "servers.json")],
    "the alias --allowed-tools Bash": argv + ["--allowed-tools", "Bash"],
    "a second --settings": argv + ["--settings", str(TMP / "other.json")],
}
for label, av in cases.items():
    check(f"L1 argv: {label} is refused as not exactly the argv", "argv is not exactly the §2.6.6 argv" in found(av), str(found(av)))
cut = argv[:argv.index("--disallowedTools") + 1]
check("L1 an emptied --disallowedTools is refused (the always-offered tools are missing)",
      any("misses tools the binary always offers" in f for f in found(cut)), str(found(cut)))
for label, extra in (("permissions.allow Bash(*)", {"permissions": {"defaultMode": "default", "allow": ["Bash(*)"]}}),
                     ("apiKeyHelper", {"apiKeyHelper": "cmd /c type x"}),
                     ("statusLine", {"statusLine": {"type": "command", "command": "cmd /c whoami"}}),
                     ("env re-adding a proxy", {"env": {"HTTPS_PROXY": "http://10.0.0.1:8080"}}),
                     ("additionalDirectories C:/", {"permissions": {"defaultMode": "default", "additionalDirectories": ["C:/"]}})):
    check(f"L1 settings: {label} is refused as not exactly the unit settings",
          "settings are not exactly the unit settings" in found(argv, {**st, **extra}))
L.record_offered_tools("sha-of-pinned", OFFERED + ["NotebookEdit2"])
check("L1 once A8 records the binary's tools, --disallowedTools must list every one of them",
      "argv is not exactly the §2.6.6 argv" in found(argv))
L._OFFERED.clear()

print("\n- L2: spawn runs the lockdown itself -")
CC = L.Contract(polygon_root=TMP / "polygon", runs_root=TMP / "polygon" / "runs" / "v3", repo_root=ROOT,
                owner_home=TMP / "owner", secrets_dir=TMP / "secrets", quarantine_root=TMP / "quarantine",
                conservation_root=TMP / "conservation", binary_exceptions=_TEST_EXC,
                system_dirs=(Path(sys.executable).parent,), claude_code_binary=TMP / "polygon" / "claude" / "claude.exe",
                claude_code_version="2.0.0-pinned")
CC.claude_code_binary.parent.mkdir(parents=True)
CC.claude_code_binary.write_bytes(b"MZ-fake-claude")
L.record_offered_tools(L._sha256_file(CC.claude_code_binary), OFFERED)
ancestors = {"found": 0}
real_ancestors = L.check_ancestors_for_claude
L.check_ancestors_for_claude = lambda c: {"checked": 3, "found": ancestors["found"]}


def cc_spawn(unit: str, *, mutate=None, record=None, binary=None):
    u = L.make_unit_dirs(CC, "cc", "r", "claude-code-memory", unit)
    cfg, settings = L.make_claude_config(u, home_canary=L.new_home_canary())
    av = L.claude_code_argv(binary or CC.claude_code_binary, settings_path=settings, empty_mcp_path=u.home / "empty-mcp.json",
                            memdir=u.memdir, offered_tools=OFFERED)
    env = L.build_env(CC, parent_env=os.environ, unit=u, path_dirs=[], catcher_url="http://127.0.0.1:47004",
                      declared={"CLAUDE_CONFIG_DIR": str(cfg)})
    if mutate:
        av, env = mutate(u, cfg, av, env)
    return L.spawn(CC, av, env=env, cwd=u.cwd, record=record or {"role": "claude-code", "arm": "claude-code-memory"},
                   parent_env=os.environ, catcher_url="http://127.0.0.1:47004", popen=_Popen, requirement="optional",
                   unwitnessed_reason="lockdown suite: never started")


called.clear()
try:
    cc_spawn("ok")
    ok_reasons = []
except L.ContractViolation as e:
    ok_reasons = e.reasons
last = json.loads(L.spawns_log(CC).read_bytes().decode().splitlines()[-1])
check("L2 a fully locked-down Claude Code spawn passes, with the pinned version recorded",
      not ok_reasons and len(called) == 1 and last["refused"] is False
      and (last.get("claude_code") or {}).get("version") == "2.0.0-pinned", str(ok_reasons or last.get("reasons")))


def refused(label: str, want: str, **kw) -> None:
    called.clear()
    try:
        cc_spawn(f"u-{abs(hash(label)) % 10**8}", **kw)
        check(f"L2 {label} is refused", False)
    except L.ContractViolation as e:
        check(f"L2 {label} is refused ({want})", any(want in r for r in e.reasons) and called == [], str(e.reasons))


ancestors["found"] = 1
refused("a CLAUDE.md in an ancestor of the runs tree", "ancestor")
ancestors["found"] = 0
refused("no CLAUDE_CONFIG_DIR", "CLAUDE_CONFIG_DIR", mutate=lambda u, cfg, av, env: (av, {k: v for k, v in env.items() if k != "CLAUDE_CONFIG_DIR"}))


def extra_file(u, cfg, av, env):
    (cfg / "hooks.json").write_bytes(b"{}")
    return av, env


refused("a config dir holding more than our settings", "exactly our settings.json", mutate=extra_file)
refused("an argv with --add-dir", "not exactly", mutate=lambda u, cfg, av, env: (av + ["--add-dir", "C:/"], env))
other = TMP / "polygon" / "claude2" / "claude.exe"
other.parent.mkdir(parents=True)
other.write_bytes(b"MZ-other-claude")
refused("a Claude binary other than the pinned one", "polygon-pinned", binary=other)
refused("a mislabelled spawn of the claude binary (role 'arm')", "CLAUDE_CONFIG_DIR", record={"role": "arm", "arm": "x"},
        mutate=lambda u, cfg, av, env: (av, {k: v for k, v in env.items() if k != "CLAUDE_CONFIG_DIR"}))


def plant(name):
    def m(u, cfg, av, env):
        (cfg / name).write_bytes(b"x")
        return av, env
    return m


refused("D4 a CLAUDE.md inside CLAUDE_CONFIG_DIR (it would load as user memory)", "exactly our settings.json",
        mutate=plant("CLAUDE.md"))
refused("D5 a .credentials.json inside CLAUDE_CONFIG_DIR", "exactly our settings.json", mutate=plant(".credentials.json"))
NODE = TMP / "polygon" / "node" / "node.exe"
CLI = TMP / "polygon" / "node" / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
CLI.parent.mkdir(parents=True)
NODE.write_bytes(b"MZ-node")
CLI.write_bytes(b"// cli")
refused("D6 node + cli.js under another label, with an extra flag", "not exactly",
        record={"role": "arm", "arm": "other"}, mutate=lambda u, cfg, av, env: ([str(NODE), str(CLI)] + av[1:] + ["--add-dir", "C:/"], env))
saved = dict(L._OFFERED)
L._OFFERED.clear()
refused("D7 no A8 record of the pinned binary's offered tools", "no A8 record")
L.record_offered_tools("0" * 64, OFFERED)
refused("D7 an A8 record made for another file (sha mismatch)", "no A8 record")
L._OFFERED.clear()
L._OFFERED.update(saved)
CC2 = L.Contract(**{**CC.__dict__, "claude_code_binary": CLI})
L.record_offered_tools(L._sha256_file(CLI), OFFERED)
u_n = L.make_unit_dirs(CC2, "cc", "r", "claude-code-memory", "u-node")
cfg_n, settings_n = L.make_claude_config(u_n, home_canary=L.new_home_canary())
av_n = [str(NODE), str(CLI)] + L.claude_code_argv(CLI, settings_path=settings_n, empty_mcp_path=u_n.home / "empty-mcp.json",
                                                   memdir=u_n.memdir, offered_tools=OFFERED)[1:]
env_n = L.build_env(CC2, parent_env=os.environ, unit=u_n, path_dirs=[], catcher_url="http://127.0.0.1:47004",
                    declared={"CLAUDE_CONFIG_DIR": str(cfg_n)})
called.clear()
L.spawn(CC2, av_n, env=env_n, cwd=u_n.cwd, record={"role": "claude-code", "arm": "claude-code-memory"},
        parent_env=os.environ, catcher_url="http://127.0.0.1:47004", popen=_Popen, requirement="optional",
        unwitnessed_reason="lockdown suite: never started")
last = json.loads(L.spawns_log(CC2).read_bytes().decode().splitlines()[-1])
check("D6 an honest node + cli.js launch of the pinned package starts, recorded as that form",
      len(called) == 1 and (last.get("claude_code") or {}).get("form") == "node+cli.js", str(last.get("reasons")))
L.record_offered_tools(L._sha256_file(CC.claude_code_binary), OFFERED)
refused("D8 a binary that is not the pinned one (node in claude's place)", "polygon-pinned",
        mutate=lambda u, cfg, av, env: ([str(NODE)] + av[1:], env))

print("\n- Q-47-6: the sessions of one claude-code-memory unit share its directory, one after another -")
u_r = L.make_unit_dirs(CC, "cc", "r", "claude-code-memory", "reuse1")


def cc_session(unit_dirs, record, *, mutate=None):
    cfg_dir = unit_dirs.home / "claude_config"      # Q-47-7: one CLAUDE_CONFIG_DIR per unit, made before its first session
    if cfg_dir.exists():
        cfg, settings = cfg_dir, cfg_dir / "settings.json"
    else:
        cfg, settings = L.make_claude_config(unit_dirs, home_canary=L.new_home_canary())
    av = L.claude_code_argv(CC.claude_code_binary, settings_path=settings, empty_mcp_path=unit_dirs.home / "empty-mcp.json",
                            memdir=unit_dirs.memdir, offered_tools=OFFERED)
    env = L.build_env(CC, parent_env=os.environ, unit=unit_dirs, path_dirs=[], catcher_url="http://127.0.0.1:47004",
                      declared={"CLAUDE_CONFIG_DIR": str(cfg)})
    if mutate:
        mutate(unit_dirs)
    try:
        L.spawn(CC, av, env=env, cwd=unit_dirs.cwd, record=record, parent_env=os.environ,
                catcher_url="http://127.0.0.1:47004", popen=_Popen, requirement="optional",
                unwitnessed_reason="lockdown suite: never started")
        return []
    except L.ContractViolation as e:
        return e.reasons


REC = {"role": "claude-code", "arm": "claude-code-memory", "stand": "cc", "run": "r", "unit": "reuse1"}
check("Q-47-6 the unit's first session starts in its fresh directory", cc_session(u_r, REC) == [])
got = cc_session(u_r, REC)
last = json.loads(L.spawns_log(CC).read_bytes().decode().splitlines()[-1])
check("Q-47-6 its second session reuses the directory, and the spawn journal says so (session 2)",
      got == [] and (last.get("cwd_reuse") or {}).get("session") == 2, str(got or last.get("cwd_reuse")))
got = cc_session(u_r, dict(REC, unit="reuse2"))
check("Q-47-6 a session of ANOTHER unit may not reuse it", any("belongs to another unit" in r for r in got), str(got))
got = cc_session(u_r, dict(REC, arm="mem0", role="arm"))
check("Q-47-6 another arm may not reuse it (the exception is claude-code-memory's only)",
      any("claude-code-memory arm only" in r for r in got), str(got))
got = cc_session(u_r, REC, mutate=lambda d: (d.cwd / "left-behind.txt").write_bytes(b"x"))
check("Q-47-6 a directory that is not empty before the spawn is refused", any("cwd is not empty" in r for r in got),
      str(got))
(u_r.cwd / "left-behind.txt").unlink()
_Popen.running = True
check("Q-47-6 while the unit's session is running, the next one waits (a first spawn under the flag)",
      cc_session(u_r, REC) == [])
got = cc_session(u_r, REC)
check("Q-47-6 ... and a second session while the previous one still runs is refused (sequential only)",
      any("still running" in r for r in got), str(got))
_Popen.running = False
u_other = L.make_unit_dirs(CC, "cc", "r", "mem0", "reuse3")
L.spawn(CC, [sys.executable, "-c", "pass"], env=L.build_env(CC, parent_env=os.environ, unit=u_other, path_dirs=[],
                                                          declared={}, catcher_url="http://127.0.0.1:47004"),
        cwd=u_other.cwd, record={"role": "arm", "arm": "mem0", "stand": "cc", "run": "r", "unit": "reuse3"},
        parent_env=os.environ, catcher_url="http://127.0.0.1:47004", popen=_Popen, requirement="optional",
        unwitnessed_reason="lockdown suite: never started")
try:
    L.spawn(CC, [sys.executable, "-c", "pass"], env=L.build_env(CC, parent_env=os.environ, unit=u_other, path_dirs=[],
                                                              declared={}, catcher_url="http://127.0.0.1:47004"),
            cwd=u_other.cwd, record={"role": "arm", "arm": "mem0", "stand": "cc", "run": "r", "unit": "reuse3"},
            parent_env=os.environ, catcher_url="http://127.0.0.1:47004", popen=_Popen, requirement="optional",
            unwitnessed_reason="lockdown suite: never started")
    check("Q-47-6 another arm's unit directory is never reusable, even by its own unit", False)
except L.ContractViolation as e:
    check("Q-47-6 another arm's unit directory is never reusable, even by its own unit",
          any("not created fresh" in r for r in e.reasons), str(e.reasons))

print("\n- Q-47-7 / Q-C5-6: one CLAUDE_CONFIG_DIR per unit; a later session holds settings.json byte-equal plus the "
      "recorded names; discovery is the A8 probe's only -")
import hashlib  # noqa: E402


def cc7_unit(stand, unit):
    u7 = L.make_unit_dirs(CC, stand, "r", "claude-code-memory", unit)
    cfg7, settings7 = L.make_claude_config(u7, home_canary=L.new_home_canary())
    return u7, cfg7, settings7


def cc7_session(u7, cfg7, settings7, record, *, mode=None):
    av = L.claude_code_argv(CC.claude_code_binary, settings_path=settings7, empty_mcp_path=u7.home / "empty-mcp.json",
                            memdir=u7.memdir, offered_tools=OFFERED)
    env7 = L.build_env(CC, parent_env=os.environ, unit=u7, path_dirs=[], catcher_url="http://127.0.0.1:47004",
                       declared={"CLAUDE_CONFIG_DIR": str(cfg7)})
    called.clear()
    try:
        L.spawn(CC, av, env=env7, cwd=u7.cwd, record=record, parent_env=os.environ, catcher_url="http://127.0.0.1:47004",
                popen=_Popen, requirement="optional", unwitnessed_reason="lockdown suite: never started",
                **({"cc_mode": mode} if mode is not None else {}))
        return []
    except L.ContractViolation as e:
        return list(e.reasons)


def last_cc():
    return json.loads(L.spawns_log(CC).read_bytes().decode().splitlines()[-1]).get("claude_code") or {}


def put(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


BIN_SHA = L._sha256_file(CC.claude_code_binary)
L.record_offered_tools(BIN_SHA, OFFERED)
L._CONFIG_NAMES.clear() if hasattr(L, "_CONFIG_NAMES") else None
u7, cfg7, st7 = cc7_unit("cc", "q7a")
R7 = {"role": "claude-code", "arm": "claude-code-memory", "stand": "cc", "run": "r", "unit": "q7a"}
first7 = cc7_session(u7, cfg7, st7, R7)
check("Q-47-7 a unit's first session: its CLAUDE_CONFIG_DIR holds exactly our settings.json (D4/D5), recorded as the first",
      first7 == [] and last_cc().get("config", {}).get("first") is True, str(first7 or last_cc()))
put(cfg7 / "projects" / "slug-q7a" / "s1.jsonl", b'{"a": 1}\n')
put(cfg7 / "todos" / "t.json", b"[]")
put(cfg7 / ".claude.json", b"{}")
got7 = cc7_session(u7, cfg7, st7, R7)
check("Q-47-7 a later session with the product's files but no A8 record of the config names is refused by name",
      any("no A8 record of the product's config names" in r for r in got7) and called == [], str(got7))
L.record_config_names(BIN_SHA, [".claude.json", "projects", "statsig", "todos"])
got7 = cc7_session(u7, cfg7, st7, R7)
check("Q-47-7 a later session with the product's files under the recorded names passes - settings.json byte-equal, "
      "the names present recorded", got7 == [] and last_cc().get("config") == {"first": False,
                                                                              "names": [".claude.json", "projects", "todos"]},
      str(got7 or last_cc()))
for label, make, want in (
        ("a CLAUDE.md at the top", lambda: put(cfg7 / "CLAUDE.md"), "CLAUDE.md"),
        ("a claude.md deep inside the product's files", lambda: put(cfg7 / "projects" / "slug-q7a" / "claude.md"), "claude.md"),
        ("a .credentials.json", lambda: put(cfg7 / ".credentials.json"), ".credentials.json"),
        ("a hooks directory", lambda: put(cfg7 / "hooks" / "pre.json"), "hooks"),
        ("an agents directory", lambda: put(cfg7 / "agents" / "a.md"), "agents"),
        ("a commands directory", lambda: put(cfg7 / "commands" / "c.md"), "commands"),
        ("a settings.local.json", lambda: put(cfg7 / "settings.local.json"), "settings.local.json")):
    planted = make()
    got7 = cc7_session(u7, cfg7, st7, R7)
    check(f"Q-47-7 {label} in CLAUDE_CONFIG_DIR is refused by name, always",
          any("forbidden in CLAUDE_CONFIG_DIR" in r and want in r for r in got7) and called == [], str(got7))
    top = cfg7 / planted.relative_to(cfg7).parts[0]
    if planted.parent != cfg7 and top.name != "projects":
        shutil.rmtree(top)
    else:
        planted.unlink()
L.record_config_names("0" * 64, [".claude.json", "projects", "statsig", "todos"])
got7 = cc7_session(u7, cfg7, st7, R7)
check("Q-47-7 names recorded for another binary's sha are no record for this one - the later session is refused",
      any("no A8 record of the product's config names" in r for r in got7), str(got7))
L.record_config_names(BIN_SHA, [".claude.json", "projects", "statsig", "todos"])
put(cfg7 / "notes.txt")
got7 = cc7_session(u7, cfg7, st7, R7)
check("Q-47-7 a name outside the recorded list is refused by name - an unknown is investigated, never passed",
      any("outside A8's recorded names" in r and "notes.txt" in r for r in got7), str(got7))
(cfg7 / "notes.txt").unlink()
orig7 = st7.read_bytes()
st7.write_bytes(orig7.replace(b"}", b" }", 1))
got7 = cc7_session(u7, cfg7, st7, R7)
check("Q-47-7 a later session whose settings.json is not byte-equal to the first session's is refused",
      any("settings.json is not byte-equal" in r for r in got7), str(got7))
st7.write_bytes(orig7)
import _winapi  # noqa: E402
_winapi.CreateJunction(str(u7.memdir), str(cfg7 / "statsig"))
got7 = cc7_session(u7, cfg7, st7, R7)
check("Q-47-7 a link or junction inside CLAUDE_CONFIG_DIR is refused by name, even under a recorded name",
      any("a link or junction inside CLAUDE_CONFIG_DIR" in r for r in got7), str(got7))
os.rmdir(cfg7 / "statsig")
check("Q-47-7 ... and with every plant removed the unit's next session passes again", cc7_session(u7, cfg7, st7, R7) == [])
cfg7b = u7.home / "claude_config_2"
cfg7b.mkdir()
(cfg7b / "settings.json").write_bytes(orig7)
got7 = cc7_session(u7, cfg7b, cfg7b / "settings.json", R7)
check("Q-47-7 a new CLAUDE_CONFIG_DIR for a later session of the same unit is refused - one per unit",
      any("one CLAUDE_CONFIG_DIR per unit" in r for r in got7), str(got7))
shutil.rmtree(cfg7b)

uz, cfgz, stz = cc7_unit("cc", "q7z")
Rz = {**R7, "unit": "q7z"}
put(cfgz / "CLAUDE.md")
got_z1 = cc7_session(uz, cfgz, stz, Rz)
(cfgz / "CLAUDE.md").unlink()
put(cfgz / "todos" / "t.json", b"[]")
got_z2 = cc7_session(uz, cfgz, stz, Rz)
check("Q-47-7 a refused first session registers nothing: the unit's next session is still its first (D4/D5), so a "
      "product file there is refused even under a recorded name",
      any("forbidden" in r for r in got_z1) and any("exactly our settings.json" in r for r in got_z2), str((got_z1, got_z2)))
ud, cfgd, std = cc7_unit("_a8", "d1")
RD = {"role": "probe", "arm": "claude-code-memory", "stand": "_a8", "run": "r", "unit": "d1"}
check("Q-C5-6 the probe's first session is an ordinary first session (D4/D5)", cc7_session(ud, cfgd, std, RD) == [])
f1 = put(cfgd / "newthing.bin", b"abc")
put(cfgd / "projects" / "p" / "a.jsonl", b"12")
put(cfgd / "projects" / "p" / "b.jsonl", b"345")
got_d = cc7_session(ud, cfgd, std, RD, mode="discovery")
tree = hashlib.sha256(("projects/p/a.jsonl\0" + hashlib.sha256(b"12").hexdigest() + "\n"
                       + "projects/p/b.jsonl\0" + hashlib.sha256(b"345").hexdigest() + "\n").encode("utf-8")).hexdigest()
check("Q-C5-6 discovery: the probe's second session passes with names no list holds yet, each recorded - name, kind, "
      "size and sha256 (a directory's over its files' relative paths and sha256s) - which IS the allowed list",
      got_d == [] and last_cc().get("config") == {"first": False, "mode": "discovery", "entries": [
          {"name": "newthing.bin", "kind": "file", "size": 3, "sha256": hashlib.sha256(b"abc").hexdigest()},
          {"name": "projects", "kind": "dir", "size": 5, "sha256": tree}]}, str(got_d or last_cc()))
put(cfgd / "CLAUDE.md")
got_d = cc7_session(ud, cfgd, std, RD, mode="discovery")
check("Q-C5-6 discovery: a forbidden name is still refused", any("forbidden in CLAUDE_CONFIG_DIR" in r for r in got_d),
      str(got_d))
(cfgd / "CLAUDE.md").unlink()
origd = std.read_bytes()
std.write_bytes(origd + b"\n")
got_d = cc7_session(ud, cfgd, std, RD, mode="discovery")
check("Q-C5-6 discovery: settings.json is still byte-equal", any("settings.json is not byte-equal" in r for r in got_d),
      str(got_d))
std.write_bytes(origd)
ue, cfge, ste = cc7_unit("cc", "d2")
RE = {"role": "probe", "arm": "claude-code-memory", "stand": "cc", "run": "r", "unit": "d2"}
got_e = cc7_session(ue, cfge, ste, RE, mode="discovery")
uf, cfgf, stf = cc7_unit("_a8", "d3")
got_f = cc7_session(uf, cfgf, stf, {**RD, "unit": "d3", "role": "claude-code"}, mode="discovery")
ug, cfgg, stg = cc7_unit("_a8", "d4")
got_g = cc7_session(ug, cfgg, stg, {**RD, "unit": "d4"}, mode="free")
check("Q-C5-6 discovery outside stand _a8, or not by role probe, is refused by name; an unknown mode is refused",
      any("discovery mode is the A8 probe's only" in r for r in got_e)
      and any("discovery mode is the A8 probe's only" in r for r in got_f)
      and any("unknown Claude Code mode" in r for r in got_g), str((got_e, got_f, got_g)))
saved_off = dict(L._OFFERED)
L._OFFERED.clear()
uh, cfgh, sth = cc7_unit("_a8", "d5")
got_h = cc7_session(uh, cfgh, sth, {**RD, "unit": "d5"}, mode="discovery")
off_h = last_cc().get("offered")
ui, cfgi, sti = cc7_unit("cc", "q7b")
got_i = cc7_session(ui, cfgi, sti, {**R7, "unit": "q7b"})
check("Q-C5-7 attempt 2: a discovery spawn runs before any record of the offered tools (D7 waived there, and said so); a "
      "campaign spawn without the record is still refused",
      got_h == [] and off_h == "waived: discovery (Q-C5-7 attempt 2)"
      and any("no A8 record of the tools" in r for r in got_i), str((got_h, off_h, got_i)))
L._OFFERED.update(saved_off)
p_same = L.init_tools_problems(list(reversed(OFFERED)))
kept = dict(L._OFFERED)
p_more = L.init_tools_problems(OFFERED + ["NotebookEdit2"])
check("Q-C5-7: the first spawn's init-event tools must equal the record - a mismatch is blocked:unsupported-surface and "
      "the record is withdrawn", p_same == [] and kept == saved_off and len(p_more) == 1
      and p_more[0].startswith("blocked:unsupported-surface") and "NotebookEdit2" in p_more[0] and L._OFFERED == {},
      str((p_same, p_more, L._OFFERED)))
L._OFFERED.clear()
p_none = L.init_tools_problems(OFFERED)
check("Q-C5-7: without a record there is nothing the init event can equal - blocked:unsupported-surface",
      len(p_none) == 1 and p_none[0].startswith("blocked:unsupported-surface"), str(p_none))
L._OFFERED.update(saved_off)
u_nc = L.make_unit_dirs(CC, "_a8", "r", "mem0", "nc1")
called.clear()
try:
    L.spawn(CC, [sys.executable, "-c", "pass"], env=L.build_env(CC, parent_env=os.environ, unit=u_nc, path_dirs=[],
                                                              declared={}, catcher_url="http://127.0.0.1:47004"),
            cwd=u_nc.cwd, record={"role": "probe", "arm": "mem0", "stand": "_a8", "run": "r", "unit": "nc1"},
            parent_env=os.environ, catcher_url="http://127.0.0.1:47004", popen=_Popen, requirement="optional",
            unwitnessed_reason="lockdown suite: never started", cc_mode="discovery")
    got_nc = []
except L.ContractViolation as e:
    got_nc = list(e.reasons)
check("Q-C5-6 a Claude Code mode on a spawn that is not Claude Code is refused by name",
      any("not Claude Code" in r for r in got_nc) and called == [], str(got_nc))

print("\n- R-CC-WIT: the home canary in the unit's settings, and the fake home's decoys -")
_pspec = importlib.util.spec_from_file_location("v3_llm_proxy_for_claude", ROOT / "research" / "_llm_proxy.py")
PX = importlib.util.module_from_spec(_pspec)
sys.modules["v3_llm_proxy_for_claude"] = PX
_pspec.loader.exec_module(PX)
check("launch and the proxy name the same home canary header", L.HOME_CANARY_HEADER == PX.HOME_CANARY_HEADER)
check("F-CAN the arm the proxy requires a canary for is launch's Claude Code arm",
      getattr(PX, "HOME_CANARY_ARM", None) == L.CC_ARM)
u_c = L.make_unit_dirs(CC, "cc", "r", "claude-code-memory", "canary1")
can = L.new_home_canary()
cfg_c, settings_c = L.make_claude_config(u_c, home_canary=can)
written = json.loads(settings_c.read_bytes())
check("the unit's settings.json: hooks off, default mode, and the canary header as the ONLY env entry",
      written == {"disableAllHooks": True, "permissions": {"defaultMode": "default"},
                  "env": {"ANTHROPIC_CUSTOM_HEADERS": f"x-nvt3-home-canary: {can}"}} and len(can) == 32, str(written))
check("check_claude_code reads the canary back from the settings", L.settings_home_canary(written) == can)
av_c = L.claude_code_argv(CC.claude_code_binary, settings_path=settings_c, empty_mcp_path=u_c.home / "empty-mcp.json",
                          memdir=u_c.memdir, offered_tools=OFFERED)
check("the locked-down settings with a canary pass", L.check_claude_code(av_c, written, memdir=u_c.memdir,
                                                                        offered_tools=OFFERED) == [])
for label, bad in (("no env at all", {k: v for k, v in written.items() if k != "env"}),
                   ("a canary of the wrong shape", {**written, "env": {"ANTHROPIC_CUSTOM_HEADERS": "x-nvt3-home-canary: 12"}}),
                   ("another header name", {**written, "env": {"ANTHROPIC_CUSTOM_HEADERS": f"x-other: {can}"}})):
    got = L.check_claude_code(av_c, bad, memdir=u_c.memdir, offered_tools=OFFERED)
    check(f"R-CC-WIT settings with {label} are refused by name", any("home canary" in r for r in got), str(got))
got = L.check_claude_code(av_c, {**written, "env": {**written["env"], "HTTPS_PROXY": "http://10.0.0.1:8080"}},
                          memdir=u_c.memdir, offered_tools=OFFERED)
check("R-CC-WIT the canary does not open env: a second env entry is refused (L1)",
      any("not exactly the unit settings" in r for r in got), str(got))
check("claude_code_settings refuses a canary that is not 32 lowercase hex",
      all(_raises(lambda c=c: L.claude_code_settings(u_c.memdir, c), ValueError) for c in ("", "ABC", "0f" * 15, None)))
canaries = L.Canaries.generate()
L.plant_canaries(u_c, canaries)
cj = u_c.home / ".claude.json"
check("the fake home holds a decoy .claude.json carrying its own canary (and the .claude decoys)",
      cj.is_file() and canaries.values["decoy_claude_json"] in cj.read_text(encoding="utf-8")
      and (u_c.home / ".claude" / "CLAUDE.md").is_file() and (u_c.home / ".claude" / ".credentials.json").is_file(),
      str(sorted(p.name for p in u_c.home.iterdir())))
L.check_ancestors_for_claude = real_ancestors

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 launch claude: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
