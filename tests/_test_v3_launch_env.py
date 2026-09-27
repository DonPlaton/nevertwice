#!/usr/bin/env python3
"""PREREG-V3 TB9, step A2.1: the launch contract's core refuses what §2.6 forbids, before a child exists.

* A hostile parent environment - a Cloudflare token, a GitHub token, *_KEY / *_SECRET names, CLAUDECODE, the
  Claude Code messaging socket, inherited proxies, a store path - leaves nothing in the child: the environment is
  built from an allowlist, and a real child process started through ``spawn`` does not see a decoy token planted in
  this test's own environment.
* The telemetry list is exact; PYTHONPYCACHEPREFIX points into the runs tree (and the child's
  ``sys.pycache_prefix`` says so); the proxy is the loopback catcher; proxy tokens never start with ``sk-``.
* One name under two spellings (Path / PATH) is refused; SystemRoot is present on Windows; a value naming a denied
  root is refused in either slash form and any case.
* The working directory: the repository, a directory inside it, the owner's home, anything outside the runs root,
  a directory this process did not just create, a non-empty one and one inside a git work tree are refused.
* An executable outside the polygon is refused; a named exception matches its exact path only.
* The spawn record exists when the process starts, and holds names, never values.
* The auditor's pre-gate B1-B6: a venv's base interpreter is held to the polygon and deny rules and recorded;
  Popen takes only allowlisted arguments (never executable, shell, env); every path rule holds for the real path,
  and a link or junction in a unit path is refused; a read exception lifts one variable, or one argv index at one
  exact path; a provider-key value is refused under any name; the owner's home is pinned, not read from USERPROFILE.
* K1 (the auditor's key ruling): a value held in any *.env of the secrets directory is refused under any name, compared
  in memory and never printed; spawn() reads the contract's own directory; an unreadable *.env refuses, never skips.

No network; a temporary polygon; the one real child is this interpreter, named as the test's binary exception.

    python tests/_test_v3_launch_env.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite

_spec = importlib.util.spec_from_file_location("v3_launch", ROOT / "research" / "v3" / "launch.py")
L = importlib.util.module_from_spec(_spec)
sys.modules["v3_launch"] = L
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


TMP = Path(tempfile.mkdtemp(prefix="nvt3_launch_"))
POLY = TMP / "polygon"
SYSTEM = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32",) if os.name == "nt" else (Path("/usr/bin"),)


#: This interpreter, and its base when it is a venv (the bare CI-like interpreter is one): both named exceptions.
EXCEPTIONS = {sys.executable: "the test interpreter"}
if getattr(sys, "_base_executable", sys.executable) != sys.executable:
    EXCEPTIONS[sys._base_executable] = "the test interpreter's base"


def contract(runs: Path | None = None, **kw) -> "L.Contract":
    base = dict(polygon_root=POLY, runs_root=runs or POLY / "runs" / "v3", repo_root=ROOT,
                owner_home=TMP / "owner_home", secrets_dir=TMP / "secrets", quarantine_root=TMP / "quarantine",
                conservation_root=TMP / "conservation", other_deny=(TMP / "obsidian",), worktrees=(POLY / "before",),
                system_dirs=SYSTEM, binary_exceptions=EXCEPTIONS)
    base.update(kw)
    return L.Contract(**base)


C = contract()
(TMP / "owner_home").mkdir()
CATCHER = "http://127.0.0.1:47001"
#: The witnesses are A2.2's suite; these children make no network call, and say so in the record.
OPTIONAL = "core env suite: the child makes no network call"
HOSTILE = {
    "CLOUDFLARE_API_TOKEN": "cf-token-value-0001",
    "GITHUB_TOKEN": "ghp_hostile_value_0002",
    "FOO_KEY": "foo-key-value-0003",
    "X_SECRET": "x-secret-value-0004",
    "CLAUDECODE": "1",
    "CLAUDE_CODE_ENTRYPOINT": "cli-entrypoint-0005",
    "CLAUDE_CODE_MESSAGING_SOCKET": r"\\.\pipe\hostile-socket-0006",
    "HTTP_PROXY": "http://127.0.0.1:9999",
    "https_proxy": "http://127.0.0.1:9998",
    "NEVERTWICE_VAULT": str(TMP / "owner_home" / "vault"),
    "NVT3_DECOY": "decoy-value-0007",
}
if os.name == "nt":
    HOSTILE["SystemRoot"] = os.environ.get("SystemRoot", r"C:\Windows")


def fresh(name: str, c=None) -> "L.UnitDirs":
    return L.make_unit_dirs(c or C, "s1", "r1", "arm", name)


print("\n- the environment comes from the allowlist, never from the parent -")
U = fresh("u-env")
ENV = L.build_env(C, parent_env=HOSTILE, unit=U, path_dirs=[Path(sys.executable).parent],
                  declared={"DEEPSEEK_API_KEY": L.new_token("nevertwice")}, catcher_url=CATCHER)
lower = {k.lower() for k in ENV}
leaked = [k for k in HOSTILE if k.lower() in lower and k.lower() not in ("http_proxy", "https_proxy", "systemroot")]
check("no hostile name reaches the child", not leaked, str(leaked))
carried = [k for k, v in HOSTILE.items() if len(v) >= 8 and k.lower() != "systemroot" and any(v in x for x in ENV.values())]
check("no hostile value reaches the child under any name", not carried, str(carried))
check("the proxies are the loopback catcher, not the inherited ones",
      ENV.get("HTTP_PROXY") == CATCHER and ENV.get("HTTPS_PROXY") == CATCHER and "https_proxy" not in ENV)
check("the telemetry list is exact", all(ENV.get(k) == v for k, v in L.TELEMETRY_OFF.items())
      and len(L.TELEMETRY_OFF) == 14, str({k: ENV.get(k) for k in L.TELEMETRY_OFF}))
check("PYTHONPYCACHEPREFIX points into the runs tree",
      "PYTHONPYCACHEPREFIX" in ENV and L._inside(ENV["PYTHONPYCACHEPREFIX"], C.runs_root), ENV.get("PYTHONPYCACHEPREFIX"))
check("HOME / USERPROFILE / TEMP are the fake home, not the owner's",
      ENV["HOME"] == ENV["USERPROFILE"] == str(U.home) and L._inside(ENV["TEMP"], U.home))
check("the assertion passes on the built environment",
      L.assert_env(C, ENV, parent_env=HOSTILE, catcher_url=CATCHER) == [],
      str(L.assert_env(C, ENV, parent_env=HOSTILE, catcher_url=CATCHER)))
if os.name == "nt":
    check("SystemRoot is present (Python children fail without it)", "SystemRoot" in ENV)
try:
    L.build_env(C, parent_env=HOSTILE, unit=fresh("u-clash"), path_dirs=[], declared={"http_proxy": "http://x"},
                catcher_url=CATCHER)
    check("a declared variable cannot override a contract variable", False)
except L.ContractViolation:
    check("a declared variable cannot override a contract variable", True)

print("\n- the assertion refuses what §2.6.2 forbids -")


def violations(extra: dict, **kw) -> list[str]:
    env = dict(ENV)
    env.update(extra)
    return L.assert_env(kw.pop("c", C), env, parent_env=HOSTILE, catcher_url=CATCHER, **kw)


check("a stray GITHUB_TOKEN is refused", any("GITHUB_TOKEN" in v for v in violations({"GITHUB_TOKEN": "x" * 12})))
check("a stray FOO_KEY / X_SECRET is refused",
      sum("not a declared proxy token" in v for v in violations({"FOO_KEY": "a" * 9, "X_SECRET": "b" * 9})) == 2)
check("CLAUDECODE and the messaging socket are refused",
      sum("never-present" in v for v in violations({"CLAUDECODE": "1", "CLAUDE_CODE_MESSAGING_SOCKET": "p"})) == 2)
check("a CLAUDE_* name the contract did not set is refused",
      any("CLAUDE_CODE_ENTRYPOINT" in v for v in violations({"CLAUDE_CODE_ENTRYPOINT": "cli"})))
check("a proxy that is not the catcher is refused", any("HTTP_PROXY" in v for v in violations({"HTTP_PROXY": "http://10.0.0.1:8080"})))
check("a proxy-token name holding an sk- key is refused",
      any("does not hold a proxy token" in v for v in violations({"DEEPSEEK_API_KEY": "sk-" + "a" * 32})))
check("a value carrying a parent secret under another name is refused",
      any("carries the value of the parent's GITHUB_TOKEN" in v for v in violations({"NVT3_X": "pre" + HOSTILE["GITHUB_TOKEN"]})))
for label, value in (("backslash", str(TMP / "secrets")), ("forward slash", str(TMP / "secrets").replace("\\", "/")),
                     ("upper case", str(TMP / "owner_home" / "x").upper())):
    check(f"a value naming a denied root is refused ({label})", any("names a denied path" in v for v in violations({"NVT3_P": value})))
check("the repository is a denied path in a value", any("names a denied path" in v for v in violations({"NVT3_R": str(ROOT)})))
check("... unless that variable's exception lifts it",
      not any("NVT3_R" in v for v in violations({"NVT3_R": str(ROOT)}, env_exception={"NVT3_R": [ROOT]})))
check("a canary in a value is refused", any("canary" in v for v in violations({"NVT3_C": "zz-canary-zz"}, canaries=("zz-canary-zz",))))
CI = contract(case_insensitive_env=True, require_systemroot=False)
check("one name under two spellings (Path / PATH) is refused, on every OS",
      any("two spellings" in v for v in violations({"Path": "x"}, c=CI)))
check("the names in every violation carry no value",
      not any(HOSTILE["GITHUB_TOKEN"] in v for v in violations({"NVT3_X": HOSTILE["GITHUB_TOKEN"]})))
toks = [L.new_token(a) for a in ("nevertwice", "sk", "SK-arm", "mem0") for _ in range(250)]
check("no proxy token starts with sk- (1,000 drawn, arm names 'sk' included)",
      all(t.startswith(L.TOKEN_PREFIX) and not t.lower().startswith("sk-") for t in toks))

print("\n- the working directory -")
F = fresh("u-cwd")
check("a fresh empty unit directory under the runs root is allowed", L.check_cwd(C, F.cwd) == [], str(L.check_cwd(C, F.cwd)))
check("the repository is refused", any("repository" in r for r in L.check_cwd(C, ROOT)))
check("a directory inside the repository is refused", any("repository" in r for r in L.check_cwd(C, ROOT / "tests")))
(TMP / "owner_home" / "d").mkdir()
check("the owner's home is refused", any("owner's home" in r for r in L.check_cwd(C, TMP / "owner_home" / "d")))
(TMP / "elsewhere").mkdir()
check("a directory outside the runs root is refused", any("outside the runs root" in r for r in L.check_cwd(C, TMP / "elsewhere")))
pre = C.runs_root / "s1" / "r1" / "arm" / "pre-made"
pre.mkdir(parents=True)
check("a directory this process did not create is refused", any("not created fresh" in r for r in L.check_cwd(C, pre)))
N = fresh("u-full")
(N.cwd / "left-over.txt").write_bytes(b"x")
check("a non-empty directory is refused", any("not empty" in r for r in L.check_cwd(C, N.cwd)))
(TMP / "gitted" / ".git").mkdir(parents=True)
CG = contract(runs=TMP / "gitted" / "runs")
G = L.make_unit_dirs(CG, "s1", "r1", "arm", "u-git")
check("a directory inside a git work tree is refused (a .git in an ancestor)",
      any("git work tree" in r for r in L.check_cwd(CG, G.cwd)))
try:
    fresh("u-cwd")
    check("make_unit_dirs refuses a unit directory that exists", False)
except L.ContractViolation:
    check("make_unit_dirs refuses a unit directory that exists", True)
try:
    L.make_unit_dirs(C, "s1", "r1", "arm", "..")
    check("make_unit_dirs refuses '..' as a component", False)
except L.ContractViolation:
    check("make_unit_dirs refuses '..' as a component", True)

print("\n- executables -")
(TMP / "outside").mkdir()
foreign = TMP / "outside" / "tool.exe"
foreign.write_bytes(b"x")
for label, exe in (("outside the polygon", foreign), ("the exception's directory", Path(sys.executable).parent),
                   ("a relative path", Path("python"))):
    try:
        L.resolve_binary(C, exe)
        check(f"an executable {label} is refused", False)
    except L.ContractViolation:
        check(f"an executable {label} is refused", True)
inside = POLY / "venv" / "Scripts" / "python.exe"
inside.parent.mkdir(parents=True)
inside.write_bytes(b"not really python")
rb = L.resolve_binary(C, inside)
check("an executable under the polygon is accepted and hashed", rb.exception is None and len(rb.sha256) == 64)
rb = L.resolve_binary(C, sys.executable)
check("a named exception matches its exact path and carries its reason", rb.exception == "the test interpreter")
check("an argument naming a denied path is refused",
      L.assert_argv(C, [sys.executable, "--store", str(TMP / "quarantine" / "x")]) != [])

print("\n- spawn: the record comes first, and holds names only -")
seen_at_popen: list[list[str]] = []          # the log's lines at EVERY Popen call


class _FakePopen:
    def __init__(self, argv, **kw):
        log = L.spawns_log(C)
        seen_at_popen.append(log.read_bytes().decode("utf-8").splitlines() if log.exists() else [])

    def wait(self, timeout=None):
        return 0


R = fresh("u-rec")
env_r = dict(ENV, NVT3_PLANT="planted-value-0042")
child = L.spawn(C, [sys.executable, "-c", "pass"], env=env_r, cwd=R.cwd, record={"role": "test", "arm": "arm"},
                parent_env=HOSTILE, catcher_url=CATCHER, popen=_FakePopen, requirement="optional",
                unwitnessed_reason=OPTIONAL)
check("the spawn record exists at every moment a process is started",
      bool(seen_at_popen) and all(any(json.loads(x)["spawn_id"] == child.spawn_id for x in lines)
                                  for lines in seen_at_popen), f"{len(seen_at_popen)} Popen call(s)")
log_bytes = L.spawns_log(C).read_bytes()
check("the record holds names, not values (a planted value and the proxy token are absent)",
      b"planted-value-0042" not in log_bytes and ENV["DEEPSEEK_API_KEY"].encode() not in log_bytes
      and b"NVT3_PLANT" in log_bytes)
Q = fresh("u-refused")
try:
    L.spawn(C, [sys.executable, "-c", "pass"], env=dict(ENV, GITHUB_TOKEN="x" * 12), cwd=Q.cwd, record={"role": "test"},
            parent_env=HOSTILE, catcher_url=CATCHER, popen=_FakePopen, requirement="optional",
                unwitnessed_reason=OPTIONAL)
    check("a violating spawn is refused", False)
except L.ContractViolation:
    last = json.loads(L.spawns_log(C).read_bytes().decode("utf-8").splitlines()[-1])
    check("a violating spawn is refused, and the refusal is recorded by name", last["refused"] and last["reasons"])

print("\n- the auditor's pre-gate: B1-B6 -")
# B1: a venv's base interpreter
v_owner = POLY / "v312o"
(v_owner / "Scripts").mkdir(parents=True)
(v_owner / "Scripts" / "python.exe").write_bytes(b"MZ-launcher")
(TMP / "owner_home" / "Python312").mkdir(parents=True)
(TMP / "owner_home" / "Python312" / "python.exe").write_bytes(b"MZ-base-owner")
(v_owner / "pyvenv.cfg").write_text(f"home = {TMP / 'owner_home' / 'Python312'}\nversion = 3.12.10\n", encoding="utf-8")
try:
    L.resolve_binary(C, v_owner / "Scripts" / "python.exe")
    check("B1 a venv whose base interpreter lives in the owner's home is refused", False)
except L.ContractViolation as e:
    check("B1 a venv whose base interpreter lives in the owner's home is refused",
          any("base interpreter" in r for r in e.reasons), str(e.reasons))
v_ok = POLY / "v312p"
(v_ok / "Scripts").mkdir(parents=True)
(v_ok / "Scripts" / "python.exe").write_bytes(b"MZ-launcher-2")
(POLY / "base312").mkdir()
(POLY / "base312" / "python.exe").write_bytes(b"MZ-base-polygon")
(v_ok / "pyvenv.cfg").write_text(f"home = {POLY / 'base312'}\nversion = 3.12.10\n", encoding="utf-8")
rb = L.resolve_binary(C, v_ok / "Scripts" / "python.exe")
check("B1 a venv based under the polygon is accepted, and its base is recorded (path, version, sha256)",
      L._norm(rb.base_path) == L._norm(POLY / "base312" / "python.exe") and rb.base_version == "3.12.10"
      and rb.base_sha256 and len(rb.base_sha256) == 64 and rb.base_sha256 != rb.sha256, str(rb))
(v_ok / "pyvenv.cfg").write_text("version = 3.12.10\n", encoding="utf-8")
try:
    L.resolve_binary(C, v_ok / "Scripts" / "python.exe")
    check("B1 a pyvenv.cfg naming no base interpreter is refused", False)
except L.ContractViolation:
    check("B1 a pyvenv.cfg naming no base interpreter is refused", True)
# B2: Popen arguments
for bad in ({"executable": str(TMP / "outside" / "tool.exe")}, {"shell": True}, {"close_fds": False}, {"startupinfo": None}):
    name = next(iter(bad))
    try:
        u = fresh(f"u-popen-{name}")
        L.spawn(C, [sys.executable, "-c", "pass"], env=L.build_env(C, parent_env=HOSTILE, unit=u, path_dirs=[],
                declared={}, catcher_url=CATCHER), cwd=u.cwd, record={"role": "t"}, parent_env=HOSTILE,
                catcher_url=CATCHER, popen=_FakePopen, **bad)
        check(f"B2 Popen argument {name}= is refused", False)
    except L.ContractViolation as e:
        check(f"B2 Popen argument {name}= is refused by name", f"Popen argument not allowed: {name}" in e.reasons, str(e.reasons))
# every reason is kept: a bad Popen argument and a bad binary both reach the record
foreign_bin = TMP / "outside" / "tool.exe"
try:
    u = fresh("u-both")
    L.spawn(C, [str(foreign_bin), "-c", "pass"], env=L.build_env(C, parent_env=HOSTILE, unit=u, path_dirs=[],
            declared={}, catcher_url=CATCHER), cwd=u.cwd, record={"role": "t"}, parent_env=HOSTILE,
            catcher_url=CATCHER, popen=_FakePopen, shell=True)
    check("a spawn refused for two reasons records both (Popen argument and binary)", False)
except L.ContractViolation as e:
    last = json.loads(L.spawns_log(C).read_bytes().decode("utf-8").splitlines()[-1])
    check("a spawn refused for two reasons records both (Popen argument and binary)",
          "Popen argument not allowed: shell" in last["reasons"] and any("polygon" in r for r in last["reasons"]),
          str(last["reasons"]))
# B3: real paths
made = []


def link(at: Path, target: Path) -> bool:
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(at))
        else:
            os.symlink(target, at, target_is_directory=True)
        made.append(at)
        return True
    except (OSError, ImportError, AttributeError) as e:
        print(f"       (no junction or link here: {type(e).__name__}; that check is skipped)")
        return False


if link(POLY / "j_out", TMP / "outside"):
    try:
        L.resolve_binary(C, POLY / "j_out" / "tool.exe")
        check("B3 a binary reached through a junction out of the polygon is refused", False)
    except L.ContractViolation as e:
        check("B3 a binary reached through a junction out of the polygon is refused", True, str(e.reasons))
(C.runs_root / "s3" / "r").mkdir(parents=True)
if link(C.runs_root / "s3" / "r" / "arm", TMP / "owner_home"):
    try:
        L.make_unit_dirs(C, "s3", "r", "arm", "u")
        check("B3 a unit path with a junction to the owner's home is refused", False)
    except L.ContractViolation as e:
        check("B3 a unit path with a junction to the owner's home is refused",
              any("link or junction" in r for r in e.reasons), str(e.reasons))
    check("B3 no unit directory was created behind the junction", not (TMP / "owner_home" / "u").exists())
# B4: scoped exceptions
pp = violations({"PYTHONPATH": str(ROOT), "NVT3_STORE": str(ROOT / "stores")}, env_exception={"PYTHONPATH": [ROOT]})
check("B4 the repository lifted for PYTHONPATH does not lift it for another variable",
      any("NVT3_STORE" in v for v in pp) and not any(v.startswith("PYTHONPATH") for v in pp), str(pp))
pr = violations({"PYTHONPATH": str(ROOT), "NVT3_STORE": str(ROOT)}, read_exception=(ROOT,))
check("B4 read_exception is the import path's case only", any("NVT3_STORE" in v for v in pr)
      and not any(v.startswith("PYTHONPATH") for v in pr), str(pr))
(TMP / "secrets").mkdir(exist_ok=True)
key = TMP / "secrets" / "deepseek.env"
other = TMP / "secrets" / "other.env"
check("B4 an argv exception passes that exact path at that index",
      L.assert_argv(C, [sys.executable, str(key)], argv_exception={1: key}) == [])
check("B4 ... not another file under the same root", L.assert_argv(C, [sys.executable, str(other)], argv_exception={1: key}) != [])
check("B4 ... nor the same path at another index",
      L.assert_argv(C, [sys.executable, "-x", str(key)], argv_exception={1: key}) != [])
# B5: provider keys under any name
check("B5 an sk- key inside a non-secret variable is refused",
      any("provider key" in v for v in violations({"MEM0_LLM_CONFIG": '{"api_key": "sk-' + "a1B2c3D4" * 5 + '"}'})))
check("B5 ... and in an argument", any("provider key" in v for v in L.assert_argv(C, [sys.executable, "--k", "sk-" + "x" * 30])))
check("B5 a word ending in sk- is not a key (desk-...)", not any("provider key" in v for v in violations({"NVT3_D": "desk-" + "a" * 30})))
# K1 (the auditor's key ruling, 2026-09-27): no value held in the secrets directory reaches a child under any name -
# compared in memory, never printed; the check names the variable, never the value.
SECRET_VALUE = "FAKEPROVIDERVALUE" + "0123456789abcdef"
(TMP / "secrets").mkdir(exist_ok=True)
(TMP / "secrets" / "provider.env").write_text(f"# a comment\nPROVIDER_LOOKALIKE='{SECRET_VALUE}'\nSHORT=abc\n\n",
                                              encoding="utf-8")
(TMP / "secrets" / "notes.txt").write_text("NOT_AN_ENV_FILE=" + "y" * 20, encoding="utf-8")
try:
    SV = L.secret_values(C)
except Exception as e:  # noqa: BLE001 - a crash is a named FAIL of the rows below
    SV = frozenset()
    print(f"       (secret_values raised {type(e).__name__})")
check("K1 the secrets directory's *.env values are read into memory (quotes stripped; short values and other files "
      "ignored)", SV == frozenset({SECRET_VALUE}), f"{len(SV)} value(s)")
k1 = violations({"DEEPSEEK_API_KEY": "nvt3-nevertwice-" + SECRET_VALUE}, secret_values=SV)
check("K1 a proxy token carrying a secrets-directory value is refused by name, and the reason holds no value",
      any("secrets directory" in v and "DEEPSEEK_API_KEY" in v for v in k1) and not any(SECRET_VALUE in v for v in k1),
      str(k1))
check("K1 ... under a name that is not secret-shaped too",
      any("secrets directory" in v and "NVT3_NOTE" in v for v in violations({"NVT3_NOTE": "x" + SECRET_VALUE},
                                                                          secret_values=SV)))
check("K1 the built environment passes the check", violations({}, secret_values=SV) == [],
      str(violations({}, secret_values=SV)))
check("K1 no secrets directory means nothing to compare, not a crash",
      L.secret_values(contract(secrets_dir=TMP / "no_such_secrets")) == frozenset())
(TMP / "secrets" / "locked.env").mkdir()
try:
    L.secret_values(C)
    check("K1 an unreadable *.env refuses by name instead of comparing less", False)
except L.ContractViolation as e:
    check("K1 an unreadable *.env refuses by name instead of comparing less",
          any("could not be read" in r and "locked.env" in r for r in e.reasons), str(e.reasons))
except Exception as e:  # noqa: BLE001
    check("K1 an unreadable *.env refuses by name instead of comparing less", False, type(e).__name__)
(TMP / "secrets" / "locked.env").rmdir()
S1 = fresh("u-secret")
try:
    L.spawn(C, [sys.executable, "-c", "pass"], env=dict(ENV, NVT3_NOTE="x" + SECRET_VALUE), cwd=S1.cwd,
            record={"role": "test"}, parent_env=HOSTILE, catcher_url=CATCHER, popen=_FakePopen,
            requirement="optional", unwitnessed_reason=OPTIONAL)
    check("K1 spawn() compares against the contract's secrets directory itself", False)
except L.ContractViolation as e:
    log_k1 = L.spawns_log(C).read_bytes()
    check("K1 spawn() compares against the contract's secrets directory itself, and its record holds no value",
          any("secrets directory" in r for r in e.reasons) and SECRET_VALUE.encode() not in log_k1, str(e.reasons))
(TMP / "secrets" / "provider.env").unlink()
(TMP / "secrets" / "notes.txt").unlink()
# B6: the owner's home
with mock.patch.dict(os.environ, {"USERPROFILE": str(TMP / "fake_profile")}):
    D = L.Contract.default()
check("B6 the default contract pins the owner's home literally, whatever USERPROFILE says",
      L._norm(D.owner_home) == L._norm(r"C:\Users\Platon"), str(D.owner_home))
check("B6 ... and denies a USERPROFILE that differs from it", any(L._norm(p) == L._norm(TMP / "fake_profile") for p in D.other_deny),
      str(D.other_deny))
# CI class A (76cb0e9): macOS keeps TMPDIR under /var -> /private/var and Windows runners give C:\Users\RUNNER~1 (an 8.3
# name) - a link ABOVE the contract's roots belongs to the machine. The roots are taken at their real paths once, and a
# link BELOW them is still refused.
(TMP / "root_real").mkdir()
if link(TMP / "root_link", TMP / "root_real"):
    CL = contract(polygon_root=TMP / "root_link" / "polygon", runs=TMP / "root_link" / "polygon" / "runs" / "v3", worktrees=())
    check("A: a contract whose roots are given through a link takes them at their real paths",
          L._norm(CL.runs_root) == L._norm(os.path.realpath(TMP / "root_real" / "polygon" / "runs" / "v3"))
          and L._norm(CL.polygon_root) == L._norm(os.path.realpath(TMP / "root_real" / "polygon")), str(CL.runs_root))
    (TMP / "root_link" / "polygon" / "runs" / "v3").mkdir(parents=True, exist_ok=True)   # as on CI: the runs root exists
    try:
        ud_a = L.make_unit_dirs(CL, "s4", "r", "arm", "u")
        made_a = ud_a.cwd.is_dir() and L._inside(ud_a.cwd, CL.runs_root)
    except L.ContractViolation as e:
        made_a = False
        print(f"       {e.reasons}")
    check("A: ... and a unit is made under them - the link above the roots is not part of the unit path", made_a)
    (CL.runs_root / "s5" / "r").mkdir(parents=True)
    if link(CL.runs_root / "s5" / "r" / "arm", TMP / "owner_home"):
        try:
            L.make_unit_dirs(CL, "s5", "r", "arm", "u")
            below = False
        except L.ContractViolation as e:
            below = any("link or junction" in r for r in e.reasons)
        check("A: ... while a link BELOW the resolved roots is still refused", below)
    check("A: a path written through the link above the roots is inside them (by its real path too)",
          L._within(TMP / "root_link" / "polygon" / "tool.exe", CL.polygon_root)
          and L._within(TMP / "root_link" / "polygon" / "runs" / "v3" / "x", CL.runs_root))
    check("G1: ... and the root itself, written through its alias, is within it (the walk starts at the path, not its "
          "parent)", L._within(TMP / "root_link" / "polygon", CL.polygon_root)
          and L._within(TMP / "root_link" / "polygon" / "runs" / "v3", CL.runs_root))
    (TMP / "root_real" / "polygon" / "sub").mkdir(parents=True, exist_ok=True)
    if link(TMP / "j_sub", TMP / "root_real" / "polygon" / "sub"):
        check("A: ... but a junction to a place BELOW the root is no alias of it - refused",
              not L._within(TMP / "j_sub" / "tool.exe", CL.polygon_root))
for m_ in reversed(made):
    try:
        os.rmdir(m_) if os.name == "nt" else os.unlink(m_)
    except OSError:
        pass


print("\n- a real child does not inherit what this process holds -")
os.environ["NVT3_TEST_DECOY_TOKEN"] = "decoy-token-value-0099"
try:
    K = fresh("u-real")
    env_k = L.build_env(C, parent_env=os.environ, unit=K, path_dirs=[Path(sys.executable).parent], declared={},
                        catcher_url=CATCHER)
    code = ("import json, os, sys; print(json.dumps({'names': sorted(os.environ), 'cwd': os.getcwd(), "
            "'pycache': sys.pycache_prefix}))")
    import subprocess
    kid = L.spawn(C, [sys.executable, "-c", code], env=env_k, cwd=K.cwd, record={"role": "test"},
                  parent_env=os.environ, catcher_url=CATCHER, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                  requirement="optional", unwitnessed_reason=OPTIONAL)
    out, err = kid.process.communicate(timeout=60)
    rep = json.loads(out.decode("utf-8") or "{}") if kid.process.returncode == 0 else {}
    check("the child started", kid.process.returncode == 0, err.decode("utf-8", "replace")[-300:])
    check("the child does not see the decoy token planted in this process",
          rep and "NVT3_TEST_DECOY_TOKEN" not in {n.upper() for n in rep["names"]}, str(rep.get("names")))
    check("the child's cwd is its unit directory", rep and L._norm(rep["cwd"]) == L._norm(K.cwd), str(rep.get("cwd")))
    check("the child's bytecode goes to the runs tree", rep and rep.get("pycache")
          and L._inside(rep["pycache"], C.runs_root), str(rep.get("pycache")))
finally:
    os.environ.pop("NVT3_TEST_DECOY_TOKEN", None)
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 launch env: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
