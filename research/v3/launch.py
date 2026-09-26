#!/usr/bin/env python3
"""The launch contract of PREREG-V3 §2.6 (TB9): every child the v3 campaign starts goes through here.

This first part is the core (plan step A2.1): the environment is built from an allowlist and asserted before
every spawn; the working directory is a fresh, empty, non-git unit directory under the runs tree; paths on the
deny list never reach a child; the executable is pinned to the polygon or to a named exception; and the spawn
record - names only, never values - is written before the process starts.

* ``build_env`` never starts from the parent's environment. It reads the parent only for the Windows essentials
  (§2.6.2), case-insensitively, and sets everything else itself: PATH, the fake home, PYTHONPYCACHEPREFIX, the
  catcher as the only proxy, the HF caches, the git config files, the fixed telemetry list, then the arm's
  declared variables.
* ``assert_env`` refuses: a name twice under two spellings (Windows reads names case-insensitively, so which one a
  child sees is undefined); CLAUDECODE, CLAUDE_CODE_MESSAGING_SOCKET, ALL_PROXY; a CLAUDE_* name the contract did
  not set; any *_TOKEN, *_KEY or *_SECRET that is not one of the spawn's declared proxy-token names holding a proxy
  token (``nvt3-...``, never ``sk-``); a proxy other than the catcher; a value that carries the value of one of the
  parent's secrets; a value naming a denied path; a canary.
* ``check_cwd`` refuses the repository, the owner's home, every denied root, anything outside the runs root, a
  directory this process did not just create, a non-empty one, and one inside a git work tree (checked by name,
  ``.git`` in it or any ancestor).
* ``resolve_binary`` accepts an absolute path under the polygon, or exactly one of the contract's named
  exceptions (the system git and docker CLI), and records its sha256. For a venv launcher it follows pyvenv.cfg to
  the base interpreter, holds that to the same rules, and records its path, version and sha256 too.
* Every path rule holds for the real path as well (a junction or link planted in the runs tree or under the
  polygon cannot redirect a binary or a cwd), and ``make_unit_dirs`` refuses a unit path with a link in it.
* A read exception is scoped: to one variable name for the environment, to one argv index and one exact path for
  the arguments. A value shaped like a provider key (``sk-...``) is refused under any name.
* ``spawn`` accepts only the Popen arguments on its allowlist (never ``executable``, ``shell`` or ``env``); it
  writes the record, then starts the process - or writes the refusal and raises.

Witnesses (egress, filesystem), canaries, the fetch window and the Claude Code lockdown come in the next steps
(A2.2, A2.3); ``spawn`` gains them there. Standard library only, Python 3.10+.

    python tests/_test_v3_launch_env.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

REPO = Path(__file__).resolve().parents[2]
#: §2.6.4 names the owner's home literally; it is pinned here, not read from an environment a harness could fake.
OWNER_HOME = Path(r"C:\Users\Platon")

#: §2.6.2 - read from the parent, case-insensitively, and nothing else is.
WINDOWS_ESSENTIALS = ("SystemRoot", "SystemDrive", "windir", "ComSpec", "PATHEXT", "NUMBER_OF_PROCESSORS",
                      "PROCESSOR_ARCHITECTURE", "OS")

#: §2.6.2 - set for every child; an unused variable is harmless.
TELEMETRY_OFF = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_AUTOUPDATER": "1",
    "MEM0_TELEMETRY": "False",
    "GRAPHITI_TELEMETRY_ENABLED": "false",
    "TELEMETRY_DISABLED": "1",
    "ANONYMIZED_TELEMETRY": "False",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_TRACING": "false",
    "NEXT_TELEMETRY_DISABLED": "1",
    "DO_NOT_TRACK": "1",
}

#: §2.6.2 "never present", by exact name (any case).
NEVER_NAMES = frozenset({"CLAUDECODE", "CLAUDE_CODE_MESSAGING_SOCKET", "ALL_PROXY"})
SECRET_SUFFIXES = ("_TOKEN", "_KEY", "_SECRET")
CLAUDE_PREFIX = "CLAUDE_"                     # covers CLAUDE_CODE_*
#: The CLAUDE_* names the contract itself may set (§2.6.2); the Claude Code arm adds its model variables by name.
CONTRACT_CLAUDE_NAMES = frozenset({"CLAUDE_CONFIG_DIR", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"})
#: C11 [RULING]: these hold per-arm proxy tokens when the contract sets them, never provider keys.
DUMMY_TOKEN_NAMES = frozenset({"OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN"})
TOKEN_PREFIX = "nvt3-"
PROXY_NAMES = ("HTTP_PROXY", "HTTPS_PROXY")
NO_PROXY_VALUE = "127.0.0.1,localhost"
_UNIT_PART = re.compile(r"[A-Za-z0-9._-]{1,128}")
#: A provider key under any variable name (the auditor's B5; a backstop for C12): sk-..., not preceded by a letter.
_PROVIDER_KEY = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}")
#: The only Popen arguments a caller may pass (B2): never ``executable``, ``shell``, ``env``, ``cwd``, ``preexec_fn``.
POPEN_ALLOWED = frozenset({"stdin", "stdout", "stderr", "text", "encoding", "errors", "bufsize", "creationflags",
                           "universal_newlines"})


class ContractViolation(Exception):
    """A spawn the contract refuses. The message carries names, never values."""

    def __init__(self, reasons: Sequence[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


@dataclass(frozen=True)
class Contract:
    """The fixed baseline of §2.6.4 as paths. ``Contract.default()`` is the machine's; tests build their own."""
    polygon_root: Path
    runs_root: Path
    repo_root: Path
    owner_home: Path
    secrets_dir: Path
    quarantine_root: Path
    conservation_root: Path
    other_deny: tuple[Path, ...] = ()
    worktrees: tuple[Path, ...] = ()
    system_dirs: tuple[Path, ...] = ()            # System32, then the system Git (PATH tail)
    binary_exceptions: Mapping[str, str] = field(default_factory=dict)   # exact path -> reason
    hf_home: Path | None = None
    case_insensitive_env: bool = os.name == "nt"
    require_systemroot: bool = os.name == "nt"

    @classmethod
    def default(cls) -> "Contract":
        """The real machine's contract. Only the scheduler calls this. The owner's home is the literal path §2.6.4
        names (B6); if this process's USERPROFILE says something else, that is denied too."""
        polygon = Path(r"D:\Coding\_nevertwice_polygon")
        other = [Path(r"D:\Obsidian\Claude_Memory"), Path(r"D:\Local_AI_Models")]
        env_home = os.environ.get("USERPROFILE")
        if env_home and _norm(env_home) != _norm(OWNER_HOME):
            other.append(Path(env_home))
        git = Path(r"C:\Program Files\Git\cmd\git.exe")
        docker = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
        return cls(
            polygon_root=polygon,
            runs_root=polygon / "runs" / "v3",
            repo_root=REPO,
            owner_home=OWNER_HOME,
            secrets_dir=Path(r"D:\Coding\_secrets"),
            quarantine_root=Path(r"D:\Coding\_nevertwice_owner_data_quarantine"),
            conservation_root=Path(r"D:\Nevertwice_Conservation"),
            other_deny=tuple(other),
            worktrees=(polygon / "before", polygon / "engine_ef8120d"),
            system_dirs=(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32", git.parent),
            binary_exceptions={str(git): "the system Git (our engine's git snapshot)",
                               str(docker): "the system docker CLI"},
            hf_home=polygon / "hf_cache",
        )

    def deny_roots(self) -> tuple[Path, ...]:
        """Everything a child may never see (§2.6.4): the quarantine, the worktrees, the repository, the owner's
        home, the Conservation directory, the secrets, the other owner locations."""
        return (self.quarantine_root, *self.worktrees, self.repo_root, self.owner_home, self.conservation_root,
                self.secrets_dir, *self.other_deny)


# ── paths ───────────────────────────────────────────────────────────────

def _norm(p: str | os.PathLike) -> str:
    """Absolute, one slash form, lower case. Lower case everywhere is deliberate: on Windows it is the file system's
    own rule, and elsewhere it can only make a denial broader, never let a path through."""
    return os.path.abspath(os.fspath(p)).replace("/", "\\").rstrip("\\").lower()


def _inside(child: str | os.PathLike, root: str | os.PathLike) -> bool:
    c, r = _norm(child), _norm(root)
    return c == r or c.startswith(r + "\\")


def _real(p: str | os.PathLike) -> str:
    return os.path.realpath(os.fspath(p))


def _within(child: str | os.PathLike, root: str | os.PathLike) -> bool:
    """Inside ``root`` by its written path AND by its real path (B3): a junction cannot carry it out."""
    return _inside(child, root) and _inside(_real(child), _real(root))


def _touches(child: str | os.PathLike, root: str | os.PathLike) -> bool:
    """Inside ``root`` by its written path OR its real path: a junction cannot carry it in unseen."""
    return _inside(child, root) or _inside(_real(child), root) or _inside(_real(child), _real(root))


def _is_link(p: str | os.PathLike) -> bool:
    """A symlink, a junction or any reparse point (checked without following it)."""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _link_in_chain(root: Path, leaf: Path) -> bool:
    """Any existing component from ``root`` down to ``leaf`` (both included) is a link or junction."""
    p = Path(os.path.abspath(leaf))
    stop = _norm(root)
    while True:
        if p.exists() and _is_link(p):
            return True
        if _norm(p) == stop or p.parent == p:
            return False
        p = p.parent


def _names_denied(value: str, roots: Sequence[Path]) -> list[Path]:
    """Denied roots a string value names, in either slash form and any case - or reaches through a link: every
    absolute part of the value that exists is resolved too (B3)."""
    v = value.replace("/", "\\").lower()
    hit = [r for r in roots if _norm(r) and _norm(r) in v]
    for part in value.split(os.pathsep):
        part = part.strip().strip('"')
        if part and os.path.isabs(part) and os.path.exists(part):
            real = _real(part)
            hit += [r for r in roots if r not in hit and (_inside(real, r) or _inside(real, _real(r)))]
    return hit


# ── unit directories and the working directory ─────────────────────────

@dataclass(frozen=True)
class UnitDirs:
    cwd: Path
    home: Path
    appdata: Path
    localappdata: Path
    temp: Path
    pycache: Path
    gitconfig_global: Path
    gitconfig_system: Path
    mem0: Path
    memdir: Path


_FRESH: set[str] = set()          # unit directories this process created and has not handed to a child yet


def make_unit_dirs(c: Contract, stand: str, run: str, arm: str, unit: str) -> UnitDirs:
    """A fresh, empty cwd at <runs>\\<stand>\\<run>\\<arm>\\<unit>, and its fake home as a sibling <unit>.home.
    Refuses a directory that already exists (§2.6.3 "fresh")."""
    parts = (stand, run, arm, unit)
    bad = [p for p in parts if not _UNIT_PART.fullmatch(p) or p in (".", "..")]
    if bad:
        raise ContractViolation([f"unit path component not in [A-Za-z0-9._-]{{1,128}}: {len(bad)} component(s)"])
    arm_dir = c.runs_root.joinpath(stand, run, arm)
    cwd, home = arm_dir / unit, arm_dir / f"{unit}.home"
    if _link_in_chain(c.runs_root, arm_dir) or (c.runs_root.exists() and _norm(_real(c.runs_root)) != _norm(c.runs_root)):
        raise ContractViolation(["a component of the unit path is a link or junction"])
    if cwd.exists() or home.exists():
        raise ContractViolation(["unit directory is not fresh: it already exists"])
    cwd.mkdir(parents=True)
    if not _within(cwd, c.runs_root):
        raise ContractViolation(["the unit directory resolves outside the runs root"])
    dirs = UnitDirs(cwd=cwd, home=home, appdata=home / "AppData" / "Roaming", localappdata=home / "AppData" / "Local",
                    temp=home / "Temp", pycache=arm_dir / "_pycache", gitconfig_global=home / "gitconfig-global",
                    gitconfig_system=home / "gitconfig-system", mem0=home / "mem0", memdir=home / "memory")
    for d in (dirs.appdata, dirs.localappdata, dirs.temp, dirs.pycache, dirs.mem0, dirs.memdir):
        d.mkdir(parents=True, exist_ok=True)
    dirs.gitconfig_global.write_bytes(b"")
    dirs.gitconfig_system.write_bytes(b"")
    _FRESH.add(_norm(cwd))
    return dirs


def check_cwd(c: Contract, path: str | os.PathLike) -> list[str]:
    """§2.6.3: the reasons this directory may not be a child's cwd (empty list = allowed)."""
    reasons = []
    if not _within(path, c.runs_root):
        reasons.append("cwd is outside the runs root (by its path or its real path)")
    if _touches(path, c.repo_root) or _inside(c.repo_root, path):
        reasons.append("cwd is the repository or inside it")
    if _touches(path, c.owner_home):
        reasons.append("cwd is under the owner's home")
    for root in c.deny_roots():
        if root not in (c.repo_root, c.owner_home) and _touches(path, root):
            reasons.append("cwd is under a denied root")
            break
    if _link_in_chain(c.runs_root, Path(path)):
        reasons.append("a component of the cwd is a link or junction")
    if _norm(path) not in _FRESH:
        reasons.append("cwd was not created fresh by this process")
    p = Path(path)
    if not p.is_dir():
        reasons.append("cwd does not exist")
    elif any(os.scandir(p)):
        reasons.append("cwd is not empty")
    for anc in (p, *p.resolve().parents):
        if os.path.lexists(anc / ".git"):
            reasons.append("cwd is inside a git work tree")
            break
    return reasons


# ── the environment ─────────────────────────────────────────────────────

def _lookup(env: Mapping[str, str], name: str, case_insensitive: bool) -> str | None:
    if name in env:
        return env[name]
    if case_insensitive:
        low = name.lower()
        for k, v in env.items():
            if k.lower() == low:
                return v
    return None


def new_token(arm: str) -> str:
    """A per-arm proxy token: ``nvt3-<arm>-<32 hex>``. It is never a provider key and never starts with ``sk-``."""
    safe = re.sub(r"[^a-z0-9]+", "-", arm.lower()).strip("-") or "arm"
    return f"{TOKEN_PREFIX}{safe}-{secrets.token_hex(16)}"


def build_env(c: Contract, *, parent_env: Mapping[str, str], unit: UnitDirs, path_dirs: Sequence[str | os.PathLike],
              declared: Mapping[str, str], catcher_url: str, hf_offline: bool = False) -> dict[str, str]:
    """The child's environment, from the allowlist of §2.6.2. The parent is read for the essentials only."""
    env: dict[str, str] = {}
    for name in WINDOWS_ESSENTIALS:
        v = _lookup(parent_env, name, c.case_insensitive_env)
        if v is not None:
            env[name] = v
    env["PATH"] = os.pathsep.join(os.fspath(p) for p in (*path_dirs, *c.system_dirs))
    for name in ("TEMP", "TMP"):
        env[name] = os.fspath(unit.temp)
    env["HOME"] = env["USERPROFILE"] = os.fspath(unit.home)
    env["APPDATA"] = os.fspath(unit.appdata)
    env["LOCALAPPDATA"] = os.fspath(unit.localappdata)
    env["PYTHONPYCACHEPREFIX"] = os.fspath(unit.pycache)
    env["NO_PROXY"] = NO_PROXY_VALUE
    for name in PROXY_NAMES:
        env[name] = catcher_url
    hf = c.hf_home or (c.polygon_root / "hf_cache")
    env["HF_HOME"] = os.fspath(hf)
    env["HF_HUB_CACHE"] = env["TRANSFORMERS_CACHE"] = os.fspath(hf / "hub")
    env["SENTENCE_TRANSFORMERS_HOME"] = os.fspath(hf / "sentence_transformers")
    if hf_offline:
        env["HF_HUB_OFFLINE"] = env["TRANSFORMERS_OFFLINE"] = "1"
    env["MEM0_DIR"] = os.fspath(unit.mem0)
    env["GIT_CONFIG_GLOBAL"] = os.fspath(unit.gitconfig_global)
    env["GIT_CONFIG_SYSTEM"] = os.fspath(unit.gitconfig_system)
    env.update(TELEMETRY_OFF)
    taken = {k.lower() for k in env}
    clash = sorted(k for k in declared if k.lower() in taken)
    if clash:
        raise ContractViolation([f"a declared variable overrides a contract variable: {', '.join(clash)}"])
    env.update(declared)
    return env


def _loopback_url(url: str) -> bool:
    return re.fullmatch(r"http://127\.0\.0\.1:\d{1,5}/?", url or "") is not None


def assert_env(c: Contract, env: Mapping[str, str], *, parent_env: Mapping[str, str], catcher_url: str,
               token_names: Sequence[str] = (), claude_names: Sequence[str] = (),
               env_exception: Mapping[str, Sequence[Path]] | None = None,
               read_exception: Sequence[Path] = (), canaries: Sequence[str] = ()) -> list[str]:
    """§2.6.2's assertion before every spawn. Returns the violations; each names variables, never values.
    ``env_exception`` lifts a denied root for ONE variable only, e.g. {"PYTHONPATH": [repo]} (B4, §2.6.4);
    ``read_exception`` is the import-path case of it: those roots are lifted for PYTHONPATH and nothing else."""
    out = []
    seen: dict[str, str] = {}
    for k in env:
        key = k.lower() if c.case_insensitive_env else k
        if key in seen:
            out.append(f"one name under two spellings: {seen[key]} / {k}")
        seen[key] = k
    tokens = {n.upper() for n in (*DUMMY_TOKEN_NAMES, *token_names)}
    claude_ok = {n.upper() for n in (*CONTRACT_CLAUDE_NAMES, *claude_names)}
    parent_secrets = {k: v for k, v in parent_env.items()
                      if len(v or "") >= 8 and (k.upper().endswith(SECRET_SUFFIXES) or k.upper() in NEVER_NAMES
                                                or k.upper().startswith(CLAUDE_PREFIX))}
    scoped = {k.upper(): list(v) for k, v in (env_exception or {}).items()}
    if read_exception:
        scoped.setdefault("PYTHONPATH", []).extend(read_exception)
    for k, v in env.items():
        up = k.upper()
        lifted = scoped.get(up, [])
        deny = [r for r in c.deny_roots() if not any(_norm(r) == _norm(x) for x in lifted)]
        if up in NEVER_NAMES:
            out.append(f"never-present variable: {k}")
        if up.startswith(CLAUDE_PREFIX) and up not in claude_ok:
            out.append(f"CLAUDE_* variable the contract did not set: {k}")
        if up.endswith(SECRET_SUFFIXES):
            if up not in tokens:
                out.append(f"secret-shaped variable that is not a declared proxy token: {k}")
            elif not v.startswith(TOKEN_PREFIX) or v.lower().startswith("sk-"):
                out.append(f"proxy-token variable does not hold a proxy token: {k}")
        if up in PROXY_NAMES and (v != catcher_url or not _loopback_url(catcher_url)):
            out.append(f"proxy variable is not the loopback catcher: {k}")
        if up == "NO_PROXY" and v != NO_PROXY_VALUE:
            out.append("NO_PROXY is not the contract value")
        for pk, pv in parent_secrets.items():
            if pv in v:
                out.append(f"{k} carries the value of the parent's {pk}")
        if _names_denied(v, deny):
            out.append(f"{k} names a denied path")
        if any(cv and cv in v for cv in canaries):
            out.append(f"{k} carries a canary")
        if _PROVIDER_KEY.search(v):
            out.append(f"{k} holds a value shaped like a provider key")
    if c.require_systemroot and _lookup(env, "SystemRoot", True) is None:
        out.append("SystemRoot is missing (Python children fail at startup)")
    return out


# ── binaries and argv ───────────────────────────────────────────────────

@dataclass(frozen=True)
class ResolvedBinary:
    path: str
    sha256: str
    exception: str | None
    base_path: str | None = None           # a venv's base interpreter (B1)
    base_version: str | None = None
    base_sha256: str | None = None


def _sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _venv_base(exe: Path) -> tuple[Path | None, str | None, bool]:
    """(base interpreter, version, is_venv) from the pyvenv.cfg beside a venv launcher (Scripts/ or bin/)."""
    for cfg in (exe.parent / "pyvenv.cfg", exe.parent.parent / "pyvenv.cfg"):
        if not cfg.is_file():
            continue
        vals = {}
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            k, sep, v = line.partition("=")
            if sep:
                vals[k.strip().lower()] = v.strip()
        version = vals.get("version") or vals.get("version_info")
        if vals.get("executable"):
            return Path(vals["executable"]), version, True
        if vals.get("home"):
            return Path(vals["home"]) / exe.name, version, True
        return None, version, True
    return None, None, False


def _binary_rules(c: Contract, exe: str | os.PathLike, what: str) -> tuple[str | None, list[str]]:
    reason = next((r for p, r in c.binary_exceptions.items() if _norm(p) in (_norm(exe), _norm(_real(exe)))), None)
    if reason is not None:
        return reason, []
    out = []
    if not _within(exe, c.polygon_root):
        out.append(f"{what} is outside the polygon (by its path or its real path) and is not a named exception")
    if any(_touches(exe, r) for r in c.deny_roots()):
        out.append(f"{what} is under a denied root")
    return None, out


def resolve_binary(c: Contract, exe: str | os.PathLike) -> ResolvedBinary:
    """§2.6.5: an absolute executable under the polygon (and under no denied root), or exactly a named exception -
    by its written path and by its real path. A venv launcher's base interpreter (pyvenv.cfg) is held to the same
    rules and recorded: path, version, sha256 (B1: a venv whose base lives in the owner's home is refused)."""
    if not os.path.isabs(os.fspath(exe)):
        raise ContractViolation(["executable is not an absolute path"])
    reason, problems = _binary_rules(c, exe, "executable")
    if problems:
        raise ContractViolation(problems)
    if not os.path.isfile(exe):
        raise ContractViolation(["executable does not exist"])
    base, version, is_venv = _venv_base(Path(os.path.abspath(os.fspath(exe))))
    base_sha = None
    if is_venv:
        if base is None:
            raise ContractViolation(["a venv whose pyvenv.cfg names no base interpreter"])
        _, bproblems = _binary_rules(c, base, "the venv's base interpreter")
        if bproblems:
            raise ContractViolation(bproblems)
        if not base.is_file():
            raise ContractViolation(["the venv's base interpreter does not exist"])
        base_sha = _sha256_file(base)
    return ResolvedBinary(path=os.path.abspath(os.fspath(exe)), sha256=_sha256_file(exe), exception=reason,
                          base_path=str(base) if is_venv else None, base_version=version, base_sha256=base_sha)


def assert_argv(c: Contract, argv: Sequence[str], *, argv_exception: Mapping[int, Path] | None = None) -> list[str]:
    """No argument names a denied path, except where ``argv_exception`` gives that exact index that exact path
    (B4: the proxy's three files by path, never a root). No argument holds a provider key."""
    exc = argv_exception or {}
    out = []
    for i, a in enumerate(argv[1:], 1):
        a = str(a)
        if i in exc and _norm(a) == _norm(exc[i]) and _norm(_real(a)) == _norm(_real(exc[i])):
            continue
        if _names_denied(a, c.deny_roots()):
            out.append(f"argument {i} names a denied path")
        if _PROVIDER_KEY.search(a):
            out.append(f"argument {i} holds a value shaped like a provider key")
    return out


# ── spawn ───────────────────────────────────────────────────────────────

def _append_jsonl(path: Path, record: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    with open(path, "ab") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def spawns_log(c: Contract) -> Path:
    return c.runs_root / "_launch" / "spawns.jsonl"


@dataclass
class Child:
    spawn_id: str
    process: subprocess.Popen

    def wait(self, timeout: float | None = None) -> int:
        return self.process.wait(timeout)


def spawn(c: Contract, argv: Sequence[str], *, env: Mapping[str, str], cwd: str | os.PathLike,
          record: Mapping[str, str], parent_env: Mapping[str, str], catcher_url: str,
          token_names: Sequence[str] = (), claude_names: Sequence[str] = (),
          env_exception: Mapping[str, Sequence[Path]] | None = None, argv_exception: Mapping[int, Path] | None = None,
          canaries: Sequence[str] = (), popen: Callable[..., subprocess.Popen] = subprocess.Popen,
          **popen_kw) -> Child:
    """Check everything, write the record, then start the child. A refusal is recorded too, then raised."""
    spawn_id = uuid.uuid4().hex
    reasons: list[str] = [f"Popen argument not allowed: {k}" for k in sorted(popen_kw) if k not in POPEN_ALLOWED]
    try:
        binary = resolve_binary(c, argv[0])
    except ContractViolation as e:
        binary, reasons = None, list(e.reasons)
    reasons += check_cwd(c, cwd)
    reasons += assert_env(c, env, parent_env=parent_env, catcher_url=catcher_url, token_names=token_names,
                          claude_names=claude_names, env_exception=env_exception, canaries=canaries)
    reasons += assert_argv(c, argv, argv_exception=argv_exception)
    entry = {
        "spawn_id": spawn_id, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **{k: record.get(k) for k in ("role", "stand", "run", "arm", "unit")},
        "binary": None if binary is None else {"path": binary.path, "sha256": binary.sha256,
                                               "exception": binary.exception, "base_path": binary.base_path,
                                               "base_version": binary.base_version,
                                               "base_sha256": binary.base_sha256},
        "cwd_rule": "fresh empty non-git unit directory under the runs root",
        "env_names": sorted(env), "refused": bool(reasons), "reasons": reasons,
        "env_exception": {k: [str(p) for p in v] for k, v in (env_exception or {}).items()},
        "argv_exception": {str(i): str(p) for i, p in (argv_exception or {}).items()},
    }
    _append_jsonl(spawns_log(c), entry)
    if reasons:
        raise ContractViolation(reasons)
    _FRESH.discard(_norm(cwd))                     # handed to a child: no longer fresh
    if os.name == "nt":
        popen_kw.setdefault("creationflags", subprocess.CREATE_NEW_PROCESS_GROUP)
    proc = popen([binary.path, *argv[1:]], env=dict(env), cwd=os.fspath(cwd), **popen_kw)
    return Child(spawn_id=spawn_id, process=proc)
