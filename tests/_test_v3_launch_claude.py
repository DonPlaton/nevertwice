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
* Q-47-6: the sessions of one claude-code-memory unit run in that unit's directory one after another (Claude Code binds
  its memory to the project's working directory); another unit, another arm, a directory that is not empty, or a
  session while the previous one still runs is refused; each reuse is a line of the spawn journal.

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
st = L.claude_code_settings(MEM)
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
    cfg, settings = L.make_claude_config(u)
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
cfg_n, settings_n = L.make_claude_config(u_n)
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
    cfg_dir = unit_dirs.home / "claude_config"
    if cfg_dir.exists():
        shutil.rmtree(cfg_dir)
        (unit_dirs.home / "empty-mcp.json").unlink()
    cfg, settings = L.make_claude_config(unit_dirs)
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
L.check_ancestors_for_claude = real_ancestors

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 launch claude: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
