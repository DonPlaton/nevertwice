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

Step A2.2 adds the witnesses of §2.6.9: the native egress witness (psutil, imported only there: without it a
required spawn is refused, an optional one needs a recorded reason; on Windows each tree is a Job Object, created
suspended, assigned, then resumed, so no descendant escapes), the container egress witness (/proc/net/tcp through
``docker exec``), the filesystem witness on the fixed watched set (names, sizes and mtimes; only digests leave
memory; the quarantine and Conservation are stat-ed by root and already-known name, never listed), canaries (the
decoy secret planted in the scheduler's own environment included), the ancestor check for Claude Code, and declared
fetch windows that cover only their own spawns' trees. A check that could not complete - no sample, a failed one,
coverage under 0.9 of its ticks, an unwitnessed child - never reads as zero hits; every egress count carries its
declared limit. The launch and window logs are hash-chained. The Claude Code lockdown comes in A2.3. Standard
library only, Python 3.10+.

    python tests/_test_v3_launch_env.py
    python tests/_test_v3_launch_witness.py
"""
from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import os
import re
import secrets
import stat
import subprocess
import threading
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
CREATE_SUSPENDED = 0x00000004
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


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
    #: §2.6.4 - the quarantine's already-known top-level names (stat-ed, never listed) and today's two additions.
    quarantine_known: tuple[str, ...] = ()
    conservation_known: tuple[str, ...] = ()      # AQ15: {2026-09-25}, from the owner's instructions, never listed
    polygon_idle: tuple[str, ...] = ()            # §2.6.9 watched: the polygon's idle entries
    claude_code_binary: Path | None = None        # §2.6.6: the polygon-pinned Claude Code (set at A8)
    claude_code_version: str | None = None
    case_insensitive_env: bool = os.name == "nt"
    require_systemroot: bool = os.name == "nt"

    def __post_init__(self) -> None:
        # CI class A (76cb0e9): the roots below which a link is refused are taken at their real paths, once. A link ABOVE
        # them is the machine's (macOS TMPDIR under /var -> /private/var, a Windows runner's 8.3 C:\Users\RUNNER~1); a
        # link INSIDE the polygon or the runs tree is still refused (_link_in_chain walks from the leaf up to the root).
        for name in ("polygon_root", "runs_root"):
            object.__setattr__(self, name, Path(_real(getattr(self, name))))

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
            quarantine_known=("code_heldout", "heldout_marks.json", "quarantine_live", "stores", "embed_specialize",
                              "corpus_heldout", "polygon_unknown", "research_data_code_heldout",
                              "loop_campaign_v2_heldout"),
            conservation_known=("2026-09-25",),
            polygon_idle=("backups", "core_bare", "bare314", "mem0_eval", "graphiti_eval", "amem_eval", "llama.cpp",
                          "h2h_v2_stores", "h2h_v2_stores.pre-v2-fe6ddff-055329",
                          "h2h_v2_stores.pre-v2-fe6ddff-062907", "py314"),
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
    """Inside ``root`` by its real path (B3: a junction cannot carry it out), and written under ``root`` - or under a
    path that resolves to exactly ``root``: a link ABOVE the root is the machine's (CI class A: macOS /var ->
    /private/var, a runner's 8.3 name), never a junction to somewhere below it."""
    if not _inside(_real(child), _real(root)):
        return False
    if _inside(child, root):
        return True
    target, p = _norm(_real(root)), Path(os.path.abspath(child))
    while p.parent != p:
        if _norm(_real(p)) == target:
            return True
        p = p.parent
    return False


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
              declared: Mapping[str, str], catcher_url: str, hf_offline: bool = False,
              proxies: bool = True) -> dict[str, str]:
    """The child's environment, from the allowlist of §2.6.2. The parent is read for the essentials only.
    ``proxies=False`` is the proxy process's own environment (AQ13): its sockets ignore the environment, so it
    gets no HTTP(S)_PROXY at all."""
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
    if proxies:
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
    if any(_bypass_in(str(a)) for a in argv):
        out.append("argv names a bypass permission mode")          # §2.6.6: refused on every spawn, any case
    return out


# ── spawn ───────────────────────────────────────────────────────────────

def _last_line(path: Path) -> bytes:
    if not path.exists():
        return b""
    lines = path.read_bytes().rstrip(b"\n").split(b"\n")
    return lines[-1] if lines and lines[-1] else b""


if os.name == "nt":
    import msvcrt

    def _os_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _os_unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _os_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _os_unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def file_lock(path: Path, *, timeout_s: float = 60.0, poll_s: float = 0.005):
    """An exclusive lock on <path>.lock for the with-block, across processes AND threads (A6 ruling B11).

    Each acquisition opens its own descriptor: a Windows byte-range lock and a POSIX flock both conflict between two
    descriptors even inside one process, so one mechanism covers the scheduler's threads and a second process alike.
    Waiting past timeout_s raises TimeoutError - a writer never appends without the lock."""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                _os_lock(fd)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{lock} held for more than {timeout_s} s") from None
                time.sleep(poll_s)
        try:
            yield
        finally:
            _os_unlock(fd)
    finally:
        os.close(fd)


def _append_jsonl(path: Path, record: Mapping) -> None:
    """One record, chained to the previous line by its sha256 ("prev"), so an edit anywhere breaks the chain (R1:
    these logs sit in the runs tree, which children can write).

    Reading the last line and appending are one step under file_lock (A6 ruling B11): two writers that both read the
    same last line would chain two records to it and break the log, and a Windows "ab" append is not atomic between
    writers either."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        prev = _last_line(path)
        rec = dict(record, prev=hashlib.sha256(prev).hexdigest() if prev else "0" * 64)
        line = (json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        with open(path, "ab") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


def verify_chain(path: Path) -> bool:
    """True when every line's "prev" is the sha256 of the line before it (the first: zeros)."""
    prev = b""
    for line in path.read_bytes().rstrip(b"\n").split(b"\n") if path.exists() else []:
        want = hashlib.sha256(prev).hexdigest() if prev else "0" * 64
        try:
            if json.loads(line).get("prev") != want:
                return False
        except ValueError:
            return False
        prev = line
    return True


def spawns_log(c: Contract) -> Path:
    return c.runs_root / "_launch" / "spawns.jsonl"


@dataclass
class Child:
    spawn_id: str
    process: subprocess.Popen
    witness: "NativeEgressWitness | None" = None

    def wait(self, timeout: float | None = None) -> int:
        return self.process.wait(timeout)

    def kill_tree(self) -> None:
        """The whole tree: its Job Object when there is one (W1), else children first through psutil (WP9:
        terminate() on Windows kills the root only), else the root alone."""
        if self.witness is not None and self.witness.kill_tree(self.process.pid):
            return
        try:
            import psutil  # noqa: PLC0415
        except ImportError:
            self.process.kill()
            return
        try:
            root = psutil.Process(self.process.pid)
            for ch in root.children(recursive=True):
                with contextlib.suppress(psutil.Error):
                    ch.kill()
            root.kill()
        except psutil.Error:
            pass


def spawn(c: Contract, argv: Sequence[str], *, env: Mapping[str, str], cwd: str | os.PathLike,
          record: Mapping[str, str], parent_env: Mapping[str, str], catcher_url: str,
          token_names: Sequence[str] = (), claude_names: Sequence[str] = (),
          env_exception: Mapping[str, Sequence[Path]] | None = None, argv_exception: Mapping[int, Path] | None = None,
          canaries: Sequence[str] = (), popen: Callable[..., subprocess.Popen] = subprocess.Popen,
          witnesses: "Witnesses | None" = None, requirement: str = "required", unwitnessed_reason: str | None = None,
          window: "Window | None" = None, **popen_kw) -> Child:
    """Check everything, write the record, then start the child and register it with the native egress witness.
    A refusal is recorded too, then raised. A required spawn needs the native witness (AQ2); an optional one
    without it needs a written reason of at least 12 characters, which the record keeps."""
    spawn_id = uuid.uuid4().hex
    reasons: list[str] = [f"Popen argument not allowed: {k}" for k in sorted(popen_kw) if k not in POPEN_ALLOWED]
    native = witnesses.native if witnesses is not None else None
    if requirement not in ("required", "optional"):
        reasons.append("unknown witness requirement")
    elif native is None and requirement == "required":
        reasons.append("no native egress witness for a required spawn")
    elif native is None and len((unwitnessed_reason or "").strip()) < 12:
        reasons.append("an unwitnessed optional spawn needs a written reason")
    if int(popen_kw.get("creationflags") or 0) & CREATE_BREAKAWAY_FROM_JOB:
        reasons.append("creationflags ask to break away from the job")
    try:
        binary = resolve_binary(c, argv[0])
    except ContractViolation as e:
        binary = None
        reasons += list(e.reasons)                 # added to, never replacing, the reasons already found
    reasons += check_cwd(c, cwd)
    reasons += assert_env(c, env, parent_env=parent_env, catcher_url=catcher_url, token_names=token_names,
                          claude_names=claude_names, env_exception=env_exception, canaries=canaries)
    reasons += assert_argv(c, argv, argv_exception=argv_exception)
    # §2.6.6 / §2.6.9 (the auditor's L2): a Claude Code spawn - by arm, by role, or by the binary's own name, so no
    # caller label switches this off - runs only fully locked down.
    pinned_cc = c.claude_code_binary
    node_form = len(argv) > 1 and _in_claude_package(str(argv[1]))
    is_claude = (record.get("arm") == "claude-code-memory" or record.get("role") == "claude-code"
                 or Path(str(argv[0])).name.lower() in ("claude", "claude.exe", "claude.cmd")
                 or any(_in_claude_package(str(a)) for a in argv)                          # D6: node + cli.js
                 or (pinned_cc is not None and any(_norm(str(a)) == _norm(pinned_cc) for a in argv)))
    claude_rec = None
    if is_claude:
        unit_home = Path(os.path.abspath(os.fspath(cwd)) + ".home")
        memdir = unit_home / "memory"
        sp = argv[list(argv).index("--settings") + 1] if "--settings" in argv[:-1] else None
        try:
            settings = json.loads(Path(sp).read_text(encoding="utf-8")) if sp else None
        except (OSError, ValueError):
            settings = None
        if settings is None or not sp or not _within(sp, unit_home):
            reasons.append("the Claude Code settings file is missing or outside this unit's fake home")
        reasons += check_claude_code(argv, settings or {}, memdir=memdir,
                                     launcher=list(argv[:2]) if node_form else None)
        if check_ancestors_for_claude(c)["found"]:
            reasons.append("a CLAUDE.md, CLAUDE.local.md or .claude sits in an ancestor of the runs tree")
        cfg = env.get("CLAUDE_CONFIG_DIR")
        if not cfg or not _within(cfg, unit_home) or not os.path.isdir(cfg):
            reasons.append("CLAUDE_CONFIG_DIR is not a directory inside this unit's fake home")
        elif set(os.listdir(cfg)) != CLAUDE_CONFIG_ALLOWED:
            reasons.append("CLAUDE_CONFIG_DIR holds something other than exactly our settings.json")   # D4/D5
        cc_file = str(argv[1]) if node_form else str(argv[0])
        if (pinned_cc is None or _norm(cc_file) != _norm(pinned_cc) or _norm(_real(cc_file)) != _norm(_real(pinned_cc))
                or (not node_form and (binary is None or _norm(binary.path) != _norm(pinned_cc)))):
            reasons.append("the binary is not the polygon-pinned Claude Code")
        cc_sha = _sha256_file(cc_file) if os.path.isfile(cc_file) else None
        if not _OFFERED.get("tools") or _OFFERED.get("sha256") != cc_sha:                          # D7
            reasons.append("no A8 record of the tools this pinned Claude Code offers")
        claude_rec = {"version": c.claude_code_version, "binary_pinned": pinned_cc is not None,
                      "form": "node+cli.js" if node_form else "binary", "sha256": cc_sha}
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
        "witness": {"native": "on" if native is not None else "off", "requirement": requirement,
                    "unwitnessed_reason": None if native is not None else unwitnessed_reason},
        "claude_code": claude_rec,
    }
    _append_jsonl(spawns_log(c), entry)
    if reasons:
        raise ContractViolation(reasons)
    _FRESH.discard(_norm(cwd))                     # handed to a child: no longer fresh
    jobs = native.jobs if native is not None else None
    if os.name == "nt":
        popen_kw["creationflags"] = int(popen_kw.get("creationflags") or 0) | subprocess.CREATE_NEW_PROCESS_GROUP \
            | (CREATE_SUSPENDED if jobs is not None else 0)
    proc = popen([binary.path, *argv[1:]], env=dict(env), cwd=os.fspath(cwd), **popen_kw)
    if native is not None:
        handle = getattr(proc, "_handle", None) if jobs is not None else None
        ok = native.register(proc.pid, handle=int(handle) if handle is not None else None, label=spawn_id)
        if jobs is not None and handle is not None:
            if not ok or not jobs.resume(int(handle)):      # W1: never let a child run outside its job
                try:
                    proc.kill()
                except OSError:
                    pass
                _append_jsonl(spawns_log(c), {"spawn_id": spawn_id, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                              time.gmtime()), "role": record.get("role"), "stand": record.get("stand"),
                              "arm": record.get("arm"), "refused": True,
                              "reasons": ["the child could not be put in its job object"],
                              "witness": {"native": "on", "requirement": requirement}})
                raise ContractViolation(["the child could not be put in its job object"])
        if window is not None:
            window.roots.add(proc.pid)
    return Child(spawn_id=spawn_id, process=proc, witness=native)


# ── witnesses (A2.2): egress, filesystem, canaries, the ancestor check, fetch windows ──

class WitnessUnavailable(RuntimeError):
    """A witness cannot run (psutil missing, docker unreachable). Never read as 'nothing seen'."""


def _is_loopback(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    return bool(addr.is_loopback or (mapped is not None and mapped.is_loopback))


@dataclass(frozen=True)
class Conn:
    local_ip: str
    local_port: int
    remote_ip: str
    remote_port: int
    state: str


_TCP_STATES = {"01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1", "05": "FIN_WAIT2",
               "06": "TIME_WAIT", "07": "CLOSE", "08": "CLOSE_WAIT", "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING"}


def _hex_addr(field_: str, v6: bool) -> tuple[str, int]:
    host, port = field_.split(":")
    raw = bytes.fromhex(host)
    if v6:
        raw = b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4))   # four little-endian 32-bit words
        ip = str(ipaddress.IPv6Address(raw))
    else:
        ip = str(ipaddress.IPv4Address(raw[::-1]))                     # one little-endian 32-bit word
    return ip, int(port, 16)


def parse_proc_net_tcp(text: str, *, v6: bool | None = None) -> list[Conn]:
    """Lines of /proc/net/tcp or /proc/net/tcp6 (header lines skipped). ``v6=None`` decides per line by length."""
    out = []
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 4 or not cols[0].rstrip(":").isdigit():
            continue
        six = v6 if v6 is not None else len(cols[1].split(":")[0]) == 32
        lip, lport = _hex_addr(cols[1], six)
        rip, rport = _hex_addr(cols[2], six)
        out.append(Conn(lip, lport, rip, rport, _TCP_STATES.get(cols[3].upper(), cols[3])))
    return out


#: The sampling period of the egress witnesses. §2.6.9's "every second" is a floor, not a ceiling (the auditor's W5);
#: the measured cost of one native sample is recorded beside every check.
DEFAULT_TICK_S = 0.5
#: W5: what a sampling witness cannot see, published beside every egress count.
LIMIT_TEXT = ("sampled every {tick} s; a connection shorter than a tick is not seen (on Windows a closed "
              "connection's TIME_WAIT row belongs to pid 0); the catcher covers clients that honour HTTP(S)_PROXY")
LIMIT_NO_JOBS = "; off Windows, a process re-parented to init before a tick sees it is not tracked"
#: W7: a loopback destination is judged by its listener as seen in the same tick's socket table.
LIMIT_LOOPBACK = ("; a loopback destination is judged by the listener of its port in the same tick (TCP LISTEN, or "
                  "an unconnected UDP socket); a listener that is gone or has no readable pid fails the sample")
#: W7: the Ollama server's port. A tree's direct connections to it are allowed, and counted per tree
#: (``ollama_direct_conns``) so the reconciliation can flag an arm that should have gone through the proxy's leg.
OLLAMA_PORT = 11434


@dataclass
class EgressResult:
    hits: int = 0
    hit_remotes: set = field(default_factory=set)       # "ip:port" - addresses, never owner data
    window_hosts: set = field(default_factory=set)
    listen_nonloopback: int = 0
    samples: int = 0
    failed_samples: int = 0
    process_errors: int = 0
    rows_seen: int = 0                                  # connections of tracked processes read, loopback included
    unwitnessed_registrations: int = 0                  # W2: a child whose identity could not be read
    loopback_hits: int = 0                              # W7: loopback connections to a listener nobody allowed
    undetermined_loopback: int = 0                      # W7: loopback rows whose listener could not be read
    ollama_direct: dict = field(default_factory=dict)   # W7: tree label -> {(local port, 11434)}
    allowed_listeners: list = field(default_factory=list)   # W7: [(reason, pid)]
    revoked_listeners: list = field(default_factory=list)   # W7: [(reason, pid)] revoked, e.g. at a window's END
    allowed_ports: list = field(default_factory=list)       # W7: declared container gateway ports
    tick_s: float = DEFAULT_TICK_S
    elapsed_s: float = 0.0
    sample_cost_ms: float = 0.0
    limit: str = ""

    @property
    def expected_samples(self) -> int:
        return max(1, int(self.elapsed_s / self.tick_s)) if self.tick_s > 0 else 1

    @property
    def complete(self) -> bool:
        """W4: a sample, none failed, coverage >= 0.9 of the ticks the check lasted, and no child unwitnessed.
        An incomplete check never reads as zero hits."""
        return (self.samples > 0 and self.failed_samples == 0 and self.unwitnessed_registrations == 0
                and self.samples >= 0.9 * self.expected_samples)

    def as_record(self) -> dict:
        return {"hits": self.hits, "hit_remotes": sorted(self.hit_remotes), "window_hosts": sorted(self.window_hosts),
                "listen_nonloopback": self.listen_nonloopback, "samples": self.samples,
                "failed_samples": self.failed_samples, "process_errors": self.process_errors,
                "rows_seen": self.rows_seen, "unwitnessed_registrations": self.unwitnessed_registrations,
                "loopback_hits": self.loopback_hits, "undetermined_loopback": self.undetermined_loopback,
                "ollama_direct_conns": {k: len(v) for k, v in sorted(self.ollama_direct.items())},
                "allowed_listeners": [{"reason": r, "pid": pid} for r, pid in self.allowed_listeners],
                "revoked_listeners": [{"reason": r, "pid": pid} for r, pid in self.revoked_listeners],
                "allowed_ports": sorted(self.allowed_ports),
                "tick_s": self.tick_s, "elapsed_s": round(self.elapsed_s, 3),
                "expected_samples": self.expected_samples, "sample_cost_ms": round(self.sample_cost_ms, 2),
                "limit": self.limit, "complete": self.complete}


class _Ticker:
    """Samples on an absolute schedule (start + k x tick) until stopped, then once more. The thread never dies: any
    exception in a sample - a parse error included - is a failed sample (W4). Coverage is judged at stop."""

    def __init__(self, tick_s: float):
        self.tick_s = tick_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0: float | None = None
        self._cost = 0.0
        self._costn = 0
        self.windows: dict[str, set] = {}               # W3: window name -> the tree roots it covers

    def start(self) -> None:
        self._stop.clear()
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        k = 1
        while True:
            delay = self._t0 + k * self.tick_s - time.monotonic()
            if delay > 0 and self._stop.wait(delay):
                return
            if self._stop.is_set():
                return
            self._safe_sample()
            k = max(k + 1, int((time.monotonic() - self._t0) / self.tick_s) + 1)   # missed ticks stay missed

    def _safe_sample(self) -> None:
        t = time.perf_counter()
        try:
            self.sample()
        except Exception:                                # noqa: BLE001 - a failed sample, never a dead thread
            self.result.failed_samples += 1
        self._cost += time.perf_counter() - t
        self._costn += 1

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, 3 * self.tick_s))
        self._safe_sample()
        r = self.result
        r.tick_s = self.tick_s
        r.elapsed_s = (time.monotonic() - self._t0) if self._t0 is not None else 0.0
        r.sample_cost_ms = 1000 * self._cost / self._costn if self._costn else 0.0
        return r

    def open_window(self, name: str, roots: set | None = None) -> None:
        """A window covers only the trees rooted at ``roots`` (W3); any other tree's egress stays a hit."""
        self.windows[name] = roots if roots is not None else set()

    def close_window(self, name: str | None = None) -> None:
        if name is None:
            self.windows.clear()
        else:
            self.windows.pop(name, None)

    def sample(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class PsutilSampler:
    """What the native witness reads each tick. Constructing it without psutil raises WitnessUnavailable."""

    def __init__(self):
        try:
            import psutil  # noqa: PLC0415 - optional: declared in the research extra (AQ2)
        except ImportError as e:
            raise WitnessUnavailable("psutil is not installed in this interpreter") from e
        self.psutil = psutil
        # The first pass over every process is slow on Windows (measured: 4.3 s for 511 processes, then 0.07 s):
        # paid here, before a check starts, so the first tick of a check is not seconds late.
        self.processes()

    def identity(self, pid: int) -> float | None:
        try:
            return float(self.psutil.Process(pid).create_time())
        except self.psutil.Error:
            return None

    def processes(self) -> list[tuple[int, int, float]]:
        out = []
        for p in self.psutil.process_iter(["pid", "ppid", "create_time"]):
            i = p.info
            if i.get("create_time") is not None:
                out.append((i["pid"], i.get("ppid") or 0, float(i["create_time"])))
        return out

    def connections(self, pids: set[int]) -> tuple[list[tuple[int, str, int, str | None, int | None, str]], int]:
        """(connections of ``pids`` as (pid, local ip, local port, remote ip, remote port, status), process errors).
        The same socket table gives ``listeners()`` for this tick (W7); the per-process fallback leaves it unknown."""
        ps, rows, errors = self.psutil, [], 0
        self._listeners = None
        try:
            conns = ps.net_connections(kind="inet")
            for c in conns:
                if c.pid in pids:
                    rows.append((c.pid, c.laddr.ip if c.laddr else "", c.laddr.port if c.laddr else 0,
                                 c.raddr.ip if c.raddr else None, c.raddr.port if c.raddr else None, c.status))
            self._listeners = listener_map((c.pid, c.laddr.ip if c.laddr else "", c.laddr.port if c.laddr else 0,
                                            bool(c.raddr), c.status) for c in conns)
            return rows, 0
        except ps.AccessDenied:
            pass
        for pid in pids:
            try:
                proc = ps.Process(pid)
                get = getattr(proc, "net_connections", None) or proc.connections
                for c in get(kind="inet"):
                    rows.append((pid, c.laddr.ip if c.laddr else "", c.laddr.port if c.laddr else 0,
                                 c.raddr.ip if c.raddr else None, c.raddr.port if c.raddr else None, c.status))
            except (ps.AccessDenied, ps.NoSuchProcess, ps.ZombieProcess):
                errors += 1
        return rows, errors

    def listeners(self) -> dict | None:
        """W7: {(proto, port): {pid or None}} from the last ``connections`` call's table; None when it was not read."""
        return getattr(self, "_listeners", None)


def _proto(status: str) -> str:
    """psutil reports a UDP socket with status NONE; every TCP socket has a TCP state."""
    return "udp" if status == "NONE" else "tcp"


def listener_map(entries) -> dict:
    """W7: from (pid, local ip, local port, has_remote, status) rows, who can be reached at 127.0.0.1:<port> - a TCP
    LISTEN socket or an unconnected UDP socket bound to a loopback or unspecified address - as
    {(proto, port): {pid, ...}}. A pid psutil could not read stays as None."""
    out: dict = {}
    for pid, lip, lport, has_remote, status in entries:
        bindable = _is_loopback(lip) or lip in ("0.0.0.0", "::", "")
        if bindable and lport and (status == "LISTEN" or (status == "NONE" and not has_remote)):
            out.setdefault((_proto(status), lport), set()).add(pid)
    return out


# ── Windows job objects (W1): the whole tree, orphans included ──────────

class WinJobs:
    """One Job Object per registered tree root (W1). A process assigned to a job keeps every descendant in it -
    an orphan whose parent exited at once included - and no breakaway is allowed (neither BREAKAWAY_OK nor
    SILENT_BREAKAWAY). The tree is read from JobObjectBasicProcessIdList; TerminateJobObject kills it whole.
    KILL_ON_JOB_CLOSE: if the scheduler dies, its arms die with it. Standard-library ctypes; no machine setting
    changes. Construct only on Windows."""

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    PROCESS_SET_QUOTA, PROCESS_TERMINATE, PROCESS_QUERY_LIMITED = 0x0100, 0x0001, 0x1000

    def __init__(self):
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415
        self.ct, self.wt = ctypes, wintypes
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.ntdll = ctypes.WinDLL("ntdll")
        k = self.k32
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                ctypes.POINTER(wintypes.DWORD)]
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, ctypes.c_uint]
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        self.jobs: dict[int, int] = {}                  # root pid -> job handle

    def _new_job(self) -> int:
        ct, wt = self.ct, self.wt

        class BASIC(ct.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ct.c_int64), ("PerJobUserTimeLimit", ct.c_int64),
                        ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ct.c_size_t),
                        ("MaximumWorkingSetSize", ct.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                        ("Affinity", ct.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]

        class IO(ct.Structure):
            _fields_ = [(n, ct.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class EXT(ct.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ct.c_size_t),
                        ("JobMemoryLimit", ct.c_size_t), ("PeakProcessMemoryUsed", ct.c_size_t),
                        ("PeakJobMemoryUsed", ct.c_size_t)]

        job = self.k32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ct.get_last_error(), "CreateJobObject failed")
        info = EXT()
        info.Basic.LimitFlags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE      # and no breakaway flag of any kind
        if not self.k32.SetInformationJobObject(job, 9, ct.byref(info), ct.sizeof(info)):
            raise OSError(ct.get_last_error(), "SetInformationJobObject failed")
        return job

    def _open(self, pid: int, access: int) -> int:
        return self.k32.OpenProcess(access, False, pid) or 0

    def created_at(self, handle: int) -> float | None:
        """The process's creation time, epoch seconds - readable after exit while a handle is open (W2)."""
        ft = [self.wt.FILETIME() for _ in range(4)]
        if not self.k32.GetProcessTimes(handle, *[self.ct.byref(f) for f in ft]):
            return None
        v = (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime
        return (v - 116444736000000000) / 1e7

    def identity(self, pid: int) -> float | None:
        h = self._open(pid, self.PROCESS_QUERY_LIMITED)
        if not h:
            return None
        try:
            return self.created_at(h)
        finally:
            self.k32.CloseHandle(h)

    def adopt(self, pid: int, handle: int | None = None) -> float | None:
        """Put ``pid`` (by its handle when we hold one) in a job of its own; returns its creation time, or None."""
        own = handle is None
        h = self._open(pid, self.PROCESS_SET_QUOTA | self.PROCESS_TERMINATE | self.PROCESS_QUERY_LIMITED) if own else handle
        if not h:
            return None
        try:
            job = self._new_job()
            if not self.k32.AssignProcessToJobObject(job, h):
                self.k32.CloseHandle(job)
                return None
            self.jobs[pid] = job
            return self.created_at(h)
        finally:
            if own:
                self.k32.CloseHandle(h)

    def resume(self, handle: int) -> bool:
        return self.ntdll.NtResumeProcess(handle) == 0

    def members(self, root: int) -> list[int]:
        job = self.jobs.get(root)
        if not job:
            return []
        ct, wt = self.ct, self.wt
        n = 4096

        class PIDLIST(ct.Structure):
            _fields_ = [("assigned", wt.DWORD), ("inlist", wt.DWORD), ("ids", ct.c_size_t * n)]

        lst = PIDLIST()
        if not self.k32.QueryInformationJobObject(job, 3, ct.byref(lst), ct.sizeof(lst), None):
            raise OSError(ct.get_last_error(), "QueryInformationJobObject failed")
        return [int(lst.ids[i]) for i in range(lst.inlist)]

    def terminate(self, root: int) -> bool:
        job = self.jobs.get(root)
        return bool(job) and bool(self.k32.TerminateJobObject(job, 1))


class NativeEgressWitness(_Ticker):
    """§2.6.9: each tick, the connections of every process in the registered trees. On Windows a tree is its Job
    Object (W1), so an orphaned grandchild stays in it; elsewhere it is followed by parent identity and create time,
    with that limit declared. A child whose identity cannot be read is never a pid wildcard: it is counted as an
    unwitnessed registration and the check is incomplete (W2). Loopback is ignored; a non-loopback remote is a hit
    unless a window covering that tree is open (W3); a listener on a non-loopback interface is a hit (AQ16)."""

    def __init__(self, *, sampler=None, tick_s: float = DEFAULT_TICK_S, jobs: "WinJobs | None | bool" = True):
        super().__init__(tick_s)
        self.sampler = sampler if sampler is not None else PsutilSampler()
        if jobs is True:
            try:
                jobs = WinJobs() if os.name == "nt" else None
            except (OSError, AttributeError):
                jobs = None
        self.jobs = jobs or None
        self.tracked: dict[int, float] = {}          # pid -> create_time
        self.root_of: dict[int, int] = {}            # pid -> the root of its tree
        self.label_of: dict[int, str] = {}           # root pid -> its spawn id (W7's per-tree counters)
        self.allowed: dict[int, tuple[float, str]] = {}   # W7: listener pid -> (create_time, reason)
        self.allowed_ports: set[int] = set()         # W7: declared container gateway ports on loopback
        self.result = EgressResult(tick_s=tick_s, limit=LIMIT_TEXT.format(tick=tick_s)
                                   + ("" if self.jobs else LIMIT_NO_JOBS) + LIMIT_LOOPBACK)
        self._lock = threading.Lock()

    def _identity(self, pid: int) -> float | None:
        if self.jobs is not None:
            ct = self.jobs.identity(pid)
            if ct is not None:
                return ct
        ident = getattr(self.sampler, "identity", None)
        return ident(pid) if ident else None

    def register(self, pid: int, handle: int | None = None, label: str | None = None) -> bool:
        """Track a tree from its root. With job objects the root is assigned to its own job here (spawn creates it
        suspended first, so nothing escapes before). No identity, no tracking - never a wildcard (W2)."""
        with self._lock:
            ct = self.jobs.adopt(pid, handle) if self.jobs is not None else None
            if ct is None:
                ct = self._identity(pid)
            if ct is None:
                self.result.unwitnessed_registrations += 1
                return False
            self.tracked[pid] = ct
            self.root_of[pid] = pid
            self.label_of[pid] = label or str(pid)
            return True

    def allow_listener(self, pid: int, reason: str) -> bool:
        """W7: a process whose listening ports a witnessed tree may reach on loopback (the v3 proxy). Held by
        (pid, create time), so a reused pid is not allowed. No identity, not allowed."""
        with self._lock:
            ct = self._identity(pid)
            if ct is None:
                return False
            self.allowed[pid] = (ct, reason)
            self.result.allowed_listeners.append((reason, pid))
            return True

    def revoke_listener(self, pid: int) -> bool:
        """W7: end an allowance - the owner's hop allowed only inside a declared window is revoked at its END."""
        with self._lock:
            got = self.allowed.pop(pid, None)
            if got is None:
                return False
            self.result.revoked_listeners.append((got[1], pid))
            return True

    def allow_port(self, port: int) -> None:
        """W7: a declared container gateway port on loopback (e.g. a published Letta or FalkorDB port)."""
        with self._lock:
            self.allowed_ports.add(int(port))
            self.result.allowed_ports = sorted(self.allowed_ports)

    def _loopback_verdict(self, pid: int, lport: int, rport: int | None, status: str, listeners: dict | None,
                          ct_of: dict) -> str:
        """W7: "inbound" (an accepted connection on this process's own listener), "ok", "ollama", "hit" or
        "undetermined". Never "ok" without a listener that is allowed, in the same tree, or on an allowed port."""
        if listeners is None:
            return "undetermined"
        proto = _proto(status)
        if pid in listeners.get((proto, lport), ()):
            return "inbound"
        if rport == OLLAMA_PORT and proto == "tcp":
            return "ollama"
        if rport in self.allowed_ports:
            return "ok"
        owners = listeners.get((proto, rport))
        if not owners:
            return "undetermined"
        root = self.root_of.get(pid)
        for lp in owners:
            if lp is None:
                continue
            if lp in self.tracked and self.root_of.get(lp) == root and self._same(ct_of.get(lp, -1.0),
                                                                                self.tracked[lp]):
                return "ok"
            if lp in self.allowed and self._same(ct_of.get(lp, -1.0), self.allowed[lp][0]):
                return "ok"
        return "undetermined" if None in owners else "hit"

    def kill_tree(self, root: int) -> bool:
        return self.jobs is not None and self.jobs.terminate(root)

    @staticmethod
    def _same(a: float, b: float) -> bool:
        return abs(a - b) < 0.01

    def sample(self) -> None:
        with self._lock:
            try:
                procs = self.sampler.processes()
                ct_of = {pid: ct for pid, _pp, ct in procs}
                if self.jobs is not None:                # W1: the job is the tree, orphans included
                    for root in list(self.jobs.jobs):
                        for pid in self.jobs.members(root):
                            if pid not in self.tracked:
                                ct = ct_of.get(pid) or self._identity(pid)
                                if ct is not None:
                                    self.tracked[pid] = ct
                                    self.root_of[pid] = self.root_of.get(root, root)
                grew = True
                while grew:                              # parent identity and birth order (all platforms)
                    grew = False
                    for pid, ppid, ct in procs:
                        if (pid not in self.tracked and ppid in self.tracked and ct >= self.tracked[ppid]
                                and ppid in ct_of and self._same(ct_of[ppid], self.tracked[ppid])):
                            self.tracked[pid] = ct
                            self.root_of[pid] = self.root_of.get(ppid, ppid)
                            grew = True
                alive = {pid for pid, _pp, ct in procs if pid in self.tracked and self._same(self.tracked[pid], ct)}
                rows, errors = self.sampler.connections(alive)
                get_listeners = getattr(self.sampler, "listeners", None)
                listeners = get_listeners() if get_listeners else None
            except WitnessUnavailable:
                raise
            except Exception:                            # noqa: BLE001 - a failed sample is failed, never clean
                self.result.failed_samples += 1
                return
            self.result.samples += 1
            self.result.process_errors += errors
            self.result.rows_seen += len(rows)
            undetermined = 0
            for pid, lip, _lport, rip, rport, status in rows:
                if status == "LISTEN":
                    if not _is_loopback(lip):
                        self.result.listen_nonloopback += 1
                        self.result.hits += 1
                    continue
                if not rip:
                    continue
                if _is_loopback(rip):
                    verdict = self._loopback_verdict(pid, _lport, rport, status, listeners, ct_of)
                    if verdict == "hit":                 # W7: e.g. a local HTTP/SOCKS proxy nobody allowed
                        self.result.hits += 1
                        self.result.loopback_hits += 1
                        self.result.hit_remotes.add(f"{rip}:{rport}")
                    elif verdict == "undetermined":
                        undetermined += 1
                    elif verdict == "ollama":
                        label = self.label_of.get(self.root_of.get(pid, pid), str(pid))
                        self.result.ollama_direct.setdefault(label, set()).add((_lport, rport))
                    continue
                root = self.root_of.get(pid)
                if any(root in roots for roots in self.windows.values()):
                    self.result.window_hosts.add(f"{rip}:{rport}")
                else:
                    self.result.hits += 1
                    self.result.hit_remotes.add(f"{rip}:{rport}")
            if undetermined:                             # W7: never read an unknown listener as clean
                self.result.undetermined_loopback += undetermined
                self.result.failed_samples += 1


class ContainerEgressWitness(_Ticker):
    """§2.6.9, containers (Letta, FalkorDB): each tick, /proc/net/tcp{,6} read through ``docker exec``. Not a hit:
    a listener, in-container loopback, the host gateway on an allowed port. A failed docker call or an unparsable
    line is a failed sample, never 'no connections'. A window covers a container only if its name is among the
    window's roots. TCP only: UDP and DNS are a declared limit."""

    def __init__(self, container: str, *, gateway_ip: str, allowed_ports: Sequence[int],
                 run_docker: Callable[[list[str]], tuple[int, str]], tick_s: float = 5.0):
        super().__init__(tick_s)
        self.container, self.gateway_ip, self.allowed_ports = container, gateway_ip, set(allowed_ports)
        self.run_docker = run_docker
        self.result = EgressResult(tick_s=tick_s, limit=LIMIT_TEXT.format(tick=tick_s) + "; TCP only (UDP and DNS "
                                   "are not read)")

    def sample(self) -> None:
        rc, text = self.run_docker(["exec", self.container, "cat", "/proc/net/tcp", "/proc/net/tcp6"])
        if rc != 0:
            self.result.failed_samples += 1
            return
        conns = parse_proc_net_tcp(text)                 # raises on a malformed line: a failed sample
        self.result.samples += 1
        covered = any(self.container in roots for roots in self.windows.values())
        for conn in conns:
            if conn.state == "LISTEN" or conn.remote_port == 0 or _is_loopback(conn.remote_ip):
                continue
            if conn.remote_ip == self.gateway_ip and conn.remote_port in self.allowed_ports:
                continue
            if covered:
                self.result.window_hosts.add(f"{conn.remote_ip}:{conn.remote_port}")
            else:
                self.result.hits += 1
                self.result.hit_remotes.add(f"{conn.remote_ip}:{conn.remote_port}")


# ── the filesystem witness ──────────────────────────────────────────────

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class WatchSpec:
    """One label of the fixed watched set. ``mode``: "tree" (walked, no link or junction followed), "path" (a file
    or a tree, absent allowed), "stat_known" (only the root and its already-known top-level names are stat-ed;
    nothing is listed - the quarantine and the Conservation directory)."""
    label: str
    root: Path
    mode: str = "tree"
    known: tuple[str, ...] = ()
    exclude_rel: tuple[str, ...] = ()        # relative paths, "/"-separated, dirs or files
    exclude_names: tuple[str, ...] = ()      # directory names skipped at any depth


class OsFs:
    """The file-system calls the witness makes; a seam, so a test can log every call."""

    def scandir(self, path: str):
        return os.scandir(path)

    def stat(self, path: str):
        return os.stat(path, follow_symlinks=False)


def _is_link_like(entry) -> bool:
    try:
        if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
            return True
        st = entry.stat(follow_symlinks=False)
        return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return True


def _row(name: str, kind: str, size: int, mtime_ns: int) -> bytes:
    return f"{name}\0{kind}\0{size}\0{mtime_ns}".encode("utf-8", "surrogatepass")


def _digest(rows: list[bytes]) -> str:
    return hashlib.sha256(b"\n".join(sorted(rows))).hexdigest()


class FsWitness:
    """§2.6.9 [RULING] G1: names, sizes and mtimes before and after a check; no content read. Only per-directory
    digests leave memory, rolled up per label; names never do."""

    def __init__(self, specs: Sequence[WatchSpec], *, fs: OsFs | None = None):
        self.specs = tuple(specs)
        self.fs = fs or OsFs()

    def snapshot(self) -> dict[str, dict[str, str]]:
        return {s.label: self._label(s) for s in self.specs}

    def _label(self, s: WatchSpec) -> dict[str, str]:
        if s.mode == "stat_known":
            rows = []
            for name, p in (("", s.root), *((n, s.root / n) for n in s.known)):
                try:
                    st = self.fs.stat(os.fspath(p))
                    rows.append(_row(name, "e", st.st_size, st.st_mtime_ns))
                except OSError:
                    rows.append(_row(name, "absent", 0, 0))
            return {"": _digest(rows)}
        out: dict[str, str] = {}
        try:
            st = self.fs.stat(os.fspath(s.root))
        except OSError:
            return {"": _digest([_row("", "absent", 0, 0)])}
        import stat as _stat  # noqa: PLC0415
        if not _stat.S_ISDIR(st.st_mode):
            return {"": _digest([_row("", "f", st.st_size, st.st_mtime_ns)])}
        self._walk(s, s.root, "", out)
        return out

    def _walk(self, s: WatchSpec, path: Path, rel: str, out: dict[str, str]) -> None:
        rows, subdirs = [], []
        try:
            with self.fs.scandir(os.fspath(path)) as it:
                entries = list(it)
        except OSError:
            out[rel] = _digest([_row("", "unreadable", 0, 0)])
            return
        for e in entries:
            child_rel = f"{rel}/{e.name}" if rel else e.name
            if child_rel in s.exclude_rel:
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                rows.append(_row(e.name, "unreadable", 0, 0))
                continue
            if _is_link_like(e):
                rows.append(_row(e.name, "link", st.st_size, st.st_mtime_ns))    # recorded, never followed
                continue
            if e.is_dir(follow_symlinks=False):
                if e.name in s.exclude_names:
                    continue
                rows.append(_row(e.name, "d", 0, 0))
                subdirs.append((Path(e.path), child_rel))
            else:
                rows.append(_row(e.name, "f", st.st_size, st.st_mtime_ns))
        out[rel] = _digest(rows)
        for p, r in subdirs:
            self._walk(s, p, r, out)

    @staticmethod
    def persistable(snap: Mapping[str, Mapping[str, str]]) -> dict[str, dict]:
        """Per label, the number of directories and one digest over their digests - no name leaves memory."""
        return {label: {"dirs": len(d), "digest": hashlib.sha256("\n".join(sorted(
            hashlib.sha256(k.encode("utf-8", "surrogatepass")).hexdigest() + v for k, v in d.items())).encode()
        ).hexdigest()} for label, d in snap.items()}

    @staticmethod
    def diff(before: Mapping[str, Mapping[str, str]], after: Mapping[str, Mapping[str, str]]) -> dict:
        changed: dict[str, int] = {}
        for label in set(before) | set(after):
            b, a = before.get(label, {}), after.get(label, {})
            n = sum(1 for k in set(b) | set(a) if b.get(k) != a.get(k))
            if n:
                changed[label] = n
        return {"fs_hits": sum(changed.values()), "changed_labels": sorted(changed)}


def watched_set(c: Contract) -> list[WatchSpec]:
    """The fixed watched set of §2.6.9, AQ15 applied: the repository minus .git, .loop, research/v3/results, every
    __pycache__ and .claude/settings.local.json (the one file live sessions write); the owner's fixed config paths;
    the polygon's idle entries; the secrets; the worktrees; the quarantine and Conservation by root and known names."""
    h = c.owner_home
    specs = [WatchSpec("repo", c.repo_root, exclude_rel=(".git", ".loop", "research/v3/results",
                                                         ".claude/settings.local.json"),
                       exclude_names=("__pycache__",))]
    for i, p in enumerate((h / ".claude" / "settings.json", h / ".claude" / "CLAUDE.md", h / ".claude" / "rules",
                           h / ".claude" / "hooks", h / ".claude" / "scripts", h / ".codex" / "config.toml",
                           h / ".mem0", h / ".gitconfig", h / ".npmrc", h / ".config" / "git")):
        specs.append(WatchSpec(f"owner_config_{i}", p, mode="path"))
    for name in c.polygon_idle:
        specs.append(WatchSpec(f"polygon_{name}", c.polygon_root / name, mode="path"))
    specs.append(WatchSpec("secrets", c.secrets_dir, mode="path"))
    for i, w in enumerate(c.worktrees):
        specs.append(WatchSpec(f"worktree_{i}", w, mode="path"))
    specs.append(WatchSpec("quarantine", c.quarantine_root, mode="stat_known", known=c.quarantine_known))
    specs.append(WatchSpec("conservation", c.conservation_root, mode="stat_known", known=c.conservation_known))
    return specs


# ── canaries and the ancestor check ─────────────────────────────────────

@dataclass(frozen=True)
class Canaries:
    values: Mapping[str, str]

    @classmethod
    def generate(cls) -> "Canaries":
        return cls({k: f"nvt3c-{k}-{secrets.token_hex(16)}" for k in
                    ("decoy_env", "decoy_claude_md", "decoy_credentials", "ancestor")})

    def hashes(self) -> dict[str, str]:
        return {k: hashlib.sha256(v.encode()).hexdigest() for k, v in self.values.items()}


#: W6: the decoy secret in the scheduler's own environment, under a secret-shaped name. build_env never copies it;
#: assert_env refuses it if it ever reaches a child, and the proxy counts it if it ever reaches a request body.
DECOY_ENV_NAME = "NVT3_DECOY_TOKEN"


def plant_decoy_env(canaries: Canaries, environ: dict | None = None) -> str:
    """Plant the decoy_env canary in this (the scheduler's) process environment; returns its name."""
    value = canaries.values["decoy_env"]
    if environ is None:
        os.environ[DECOY_ENV_NAME] = value
    else:
        environ[DECOY_ENV_NAME] = value
    return DECOY_ENV_NAME


def plant_canaries(unit: UnitDirs, canaries: Canaries) -> None:
    """A decoy CLAUDE.md and credentials file in the unit's fake home: read only if a child ignores its config."""
    d = unit.home / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / "CLAUDE.md").write_bytes(f"Project note {canaries.values['decoy_claude_md']}\n".encode())
    (d / ".credentials.json").write_bytes(json.dumps(
        {"claudeAiOauth": {"accessToken": canaries.values["decoy_credentials"]}}).encode())


def plant_runs_root_decoy(c: Contract, canaries: Canaries) -> None:
    """The decoy at the runs-tree root that shows whether ancestor discovery is active (§2.6.9)."""
    c.runs_root.mkdir(parents=True, exist_ok=True)
    (c.runs_root / "CLAUDE.md").write_bytes(f"Runs note {canaries.values['ancestor']}\n".encode())


def check_ancestors_for_claude(c: Contract) -> dict[str, int]:
    """By name only: CLAUDE.md, CLAUDE.local.md or .claude in any ancestor of the runs tree."""
    checked = found = 0
    for anc in c.runs_root.parents:
        checked += 1
        found += sum(os.path.lexists(anc / n) for n in ("CLAUDE.md", "CLAUDE.local.md", ".claude"))
    return {"checked": checked, "found": found}


# ── checks and windows ──────────────────────────────────────────────────

class Witnesses:
    """One boundary check: the file system before and after, the egress witnesses across it."""

    def __init__(self, c: Contract, *, native: NativeEgressWitness | None, containers: Sequence = (),
                 fs: FsWitness | None = None, canaries: Canaries | None = None):
        self.c, self.native, self.containers, self.fs = c, native, tuple(containers), fs
        self.canaries = canaries
        self._before: dict | None = None
        self._check: str | None = None

    def begin_check(self, check_id: str, *, tags: dict | None = None) -> None:
        """``tags`` (a window's name, run and arm) are written into the witness record, so a reader finds a window's
        own check by what it says, not by its id alone."""
        self._check = check_id
        self._tags = dict(tags or {})
        self._begin_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._before = self.fs.snapshot() if self.fs else None
        for w in (self.native, *self.containers):
            if w is not None:
                w.start()

    def end_check(self, check_id: str) -> dict:
        egress = [w.stop().as_record() if w is not None else None for w in (self.native, *self.containers)]
        record = {"check_id": check_id, "tags": getattr(self, "_tags", {}), "begin_utc": getattr(self, "_begin_utc", None),
                  "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "native": egress[0], "containers": egress[1:],
                  "canary_hashes": self.canaries.hashes() if self.canaries else {},
                  "ancestor_claude": check_ancestors_for_claude(self.c)}
        complete = all(e is None or e["complete"] for e in egress) and self.native is not None
        if self.fs is not None:
            after = self.fs.snapshot()
            if self._before is None:
                complete = False
                record["fs"] = {"fs_hits": None, "complete": False}
            else:
                record["fs"] = {**FsWitness.diff(self._before, after), "labels": FsWitness.persistable(after)}
        else:
            complete = False
            record["fs"] = {"fs_hits": None, "complete": False}
        record["complete"] = complete
        path = self.c.runs_root / "_witness" / f"{check_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps(record, sort_keys=True, indent=1).encode("utf-8"))
        return record


@dataclass
class Window:
    """A declared fetch or install window. ``roots`` are the pids of the spawns made inside it (spawn(window=...)
    adds them); only those trees' egress is filed as window hosts (W3)."""
    name: str
    hosts: tuple
    roots: set = field(default_factory=set)


@contextlib.contextmanager
def fetch_window(c: Contract, name: str, hosts: Sequence[str], *, witnesses: Witnesses | None = None,
                 proxy_control: Callable[[str, str, Sequence[str]], None] | None = None, via: str | None = None):
    """A declared fetch or install window (§2.6.5, AQ1): START and END are recorded; the catcher tunnels only to
    ``hosts`` while it is open; and the egress witnesses file non-loopback remotes as window hosts only for the
    trees of the spawns made inside it (spawn(..., window=w)). Every other tree's egress stays a hit (W3).
    ``via`` names the declared hop ("127.0.0.1:<port>", the R4 ruling) in the START record next to the hosts."""
    if via is not None and not re.fullmatch(r"127\.0\.0\.1:\d{1,5}", via):
        raise ContractViolation(["a window's hop must be 127.0.0.1:<port>"])
    log = c.runs_root / "_launch" / "windows.jsonl"
    win = Window(name=name, hosts=tuple(hosts))
    _append_jsonl(log, {"event": "START", "window": name, "hosts": list(hosts), "via": via,
                        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    ws = [w for w in ((witnesses.native, *witnesses.containers) if witnesses else ()) if w is not None]
    for w in ws:
        w.open_window(name, win.roots)
    if proxy_control:
        proxy_control("open", name, hosts)
    try:
        yield win
    finally:
        for w in ws:
            w.close_window(name)
        if proxy_control:
            proxy_control("close", name, hosts)
        _append_jsonl(log, {"event": "END", "window": name, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})


def network_via_port(c: Contract) -> int | None:
    """The R4 ruling's hop as ONE declared value, <runs>\\_config\\network.json = {"via": {"host": "127.0.0.1",
    "port": N}}: the proxy's config and every instrument's fetch take the port from here, never from the
    environment. No file, no hop (None). Anything but exactly that shape is refused."""
    p = c.runs_root / "_config" / "network.json"
    if not p.is_file():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    via = raw.get("via") if isinstance(raw, dict) and set(raw) == {"via"} else None
    if not isinstance(via, dict) or set(via) != {"host", "port"} or via["host"] != "127.0.0.1" \
            or isinstance(via["port"], bool) or not isinstance(via["port"], int) or not 1 <= via["port"] <= 65535:
        raise ContractViolation(["network.json must be exactly {\"via\": {\"host\": \"127.0.0.1\", \"port\": N}}"])
    return via["port"]


def hop_listener_pid(port: int, *, sampler=None) -> int | None:
    """The pid listening on 127.0.0.1:<port> (TCP), read from the socket table - the hop process a window may allow."""
    s = sampler if sampler is not None else PsutilSampler()
    s.connections(set())
    owners = (s.listeners() or {}).get(("tcp", port)) or set()
    pids = [p for p in owners if p is not None]
    return pids[0] if len(pids) == 1 else None


# ── tool lockdown and Claude Code (A2.3, §2.6.6) ────────────────────────

#: §2.6.6, fixed 2026-09-26 from vendor docs and source; A8 may only remove names.
TOOLS_ALLOWED_BASELINE: dict[str, frozenset[str]] = {
    "nevertwice": frozenset(),
    "mem0": frozenset(),
    "zep-graphiti": frozenset(),
    "langmem": frozenset({"Memory", "PatchDoc", "RemoveDoc", "PatchFunctionErrors", "PatchFunctionName"}),
    "a-mem": frozenset(),
    "cognee": frozenset(),          # or its installed response-model names, fixed by rule before the pilot (C4)
    "letta": frozenset({"send_message", "conversation_search", "archival_memory_insert", "archival_memory_search",
                        "core_memory_append", "core_memory_replace", "memory_insert", "memory_replace",
                        "memory_rethink", "memory_finish_edits"}),
    "supermemory-local": frozenset(),
    "claude-code-memory": frozenset({"Read", "Write", "Edit", "MultiEdit", "memory"}),
    "reader": frozenset(),
    "judge": frozenset(),
}
#: §2.6.6: forbidden regardless of any list, case-insensitively, as substrings; plus the mcp__ prefix.
FORBIDDEN_TOOL_SUBSTRINGS = ("bash", "shell", "powershell", "cmd", "terminal", "exec", "run_code", "code_interpreter",
                             "python", "computer", "browser", "web", "fetch", "http", "url", "download", "task",
                             "agent", "notebook", "kill")
FORBIDDEN_TOOL_PREFIX = "mcp__"
#: §2.6.6: the harness refuses a spawn whose argv or settings name any of these, in any case, anywhere.
BYPASS_TOKENS = ("bypasspermissions", "acceptedits", "--dangerously-skip-permissions", "dangerously-skip-permissions")
CLAUDE_CODE_FILE_TOOLS = ("Read", "Write", "Edit", "MultiEdit")
#: L1: until A8 records the tools the pinned binary offers, these must at least be disallowed.
MIN_DISALLOWED = frozenset({"Bash", "WebFetch", "Task"})
#: What a unit's CLAUDE_CONFIG_DIR holds at spawn, exactly (D4/D5): our settings file. A CLAUDE.md there is loaded
#: as user memory and a .credentials.json is used as credentials; the decoys live in <unit>.home/.claude instead,
#: where they show whether CLAUDE_CONFIG_DIR is honoured.
CLAUDE_CONFIG_ALLOWED = frozenset({"settings.json"})
_CLAUDE_PACKAGE = ("@anthropic-ai", "claude-code")


def _in_claude_package(arg: str) -> bool:
    """D6: an argument inside the @anthropic-ai/claude-code package (the npm install's cli.js run by node)."""
    parts = [p.lower() for p in Path(arg).parts] if arg else []
    return any(parts[i:i + 2] == list(_CLAUDE_PACKAGE) for i in range(len(parts) - 1))
_OFFERED: dict = {}


def record_offered_tools(binary_sha256: str, tools: Sequence[str]) -> None:
    """A8: the tool names the pinned binary offers, read from it and recorded; --disallowedTools must then equal
    them minus the four file tools, exactly (L1). Never passed in by a spawn's caller."""
    _OFFERED["sha256"], _OFFERED["tools"] = binary_sha256, tuple(tools)


def make_claude_config(unit: "UnitDirs") -> tuple[Path, Path]:
    """A fresh CLAUDE_CONFIG_DIR inside the unit's fake home holding only our settings.json, and the empty MCP
    config beside it. Returns (config dir, settings path)."""
    cfg = unit.home / "claude_config"
    cfg.mkdir(parents=True, exist_ok=False)
    settings = cfg / "settings.json"
    settings.write_bytes(json.dumps(claude_code_settings(unit.memdir), sort_keys=True).encode("utf-8"))
    (unit.home / "empty-mcp.json").write_bytes(b'{"mcpServers": {}}')
    return cfg, settings


def tool_violation(name: str, allowed: frozenset[str] | set[str]) -> bool:
    """A tool name offered or called outside the arm's allowed set, or matching a forbidden pattern (zero tolerance)."""
    low = name.lower()
    if low.startswith(FORBIDDEN_TOOL_PREFIX) or any(s in low for s in FORBIDDEN_TOOL_SUBSTRINGS):
        return True
    return name not in allowed


def _bypass_in(text: str) -> bool:
    low = text.lower()
    return any(tok in low for tok in BYPASS_TOKENS)


def claude_code_settings(memdir: Path) -> dict:
    """The unit's settings.json: hooks off, the default permission mode, nothing else (L1: exactly this)."""
    return {"disableAllHooks": True, "permissions": {"defaultMode": "default"}}


def claude_code_argv(binary: str | os.PathLike, *, settings_path: Path, empty_mcp_path: Path, memdir: Path,
                     offered_tools: Sequence[str]) -> list[str]:
    """The fixed argv of §2.6.6. The prompt goes on stdin. The rule path form is the one A8 confirms against the
    pinned binary; a rejected flag or form blocks the arm (blocked:unsupported-surface), nothing is substituted.
    L3: A8 also runs a functional control on the real binary, because a rule form that is accepted but matches
    nothing would silently stop the arm writing its own memory - an asymmetry against it. Positive: a Write then a
    Read in the memory directory succeed. Negative: a Write outside it is refused, and the decoy CLAUDE.md in the
    fake home is not read - and READS outside it too: a Read of a decoy file in <unit>.home and of the runs-root
    decoy is refused, and so are Glob and Grep if the binary offers them at all. If the positive control fails, or a
    read outside the memory directory is not refused, in every rule form the docs allow, the arm is
    blocked:unsupported-surface and is not scored."""
    root = memdir.as_posix().rstrip("/")
    allowed_rules = [f"{t}({root}/**)" for t in CLAUDE_CODE_FILE_TOOLS]
    disallowed = sorted(t for t in offered_tools if t not in CLAUDE_CODE_FILE_TOOLS)
    return [os.fspath(binary), "-p", "--output-format", "json", "--permission-mode", "default",
            "--setting-sources", "user", "--settings", os.fspath(settings_path), "--strict-mcp-config",
            "--mcp-config", os.fspath(empty_mcp_path), "--allowedTools", *allowed_rules,
            "--disallowedTools", *disallowed]


def _walk_values(obj):
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            yield str(k)
            yield from _walk_values(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_values(v)
    else:
        yield str(obj)


def check_claude_code(argv: Sequence[str], settings: Mapping, *, memdir: Path,
                      offered_tools: Sequence[str] | None = None, launcher: Sequence[str] | None = None) -> list[str]:
    """Everything §2.6.6 fixes for the Claude Code arm; each finding names a rule, never a value. L1: the argv must
    EQUAL the canonical argv and the settings must EQUAL the unit settings - a flag added on top (a second
    --permission-mode, --add-dir, a second --mcp-config or --settings, an alias like --allowed-tools) or a key added
    to the settings (permissions.allow, apiKeyHelper, statusLine, env, additionalDirectories) is refused. The
    offered-tool list is A8's record of the pinned binary; before it exists, MIN_DISALLOWED must be disallowed."""
    out = []
    args0 = list(argv[1:])

    def first_after(flag: str) -> str | None:
        return args0[args0.index(flag) + 1] if flag in args0 and args0.index(flag) + 1 < len(args0) else None

    offered = list(offered_tools) if offered_tools is not None else list(_OFFERED.get("tools") or [])
    if not offered:
        dis = []
        if "--disallowedTools" in args0:
            for a in args0[args0.index("--disallowedTools") + 1:]:
                if a.startswith("--"):
                    break
                dis.append(a)
        missing = sorted(MIN_DISALLOWED - set(dis))
        if missing:
            out.append("--disallowedTools misses tools the binary always offers: " + ", ".join(missing))
        offered = list(CLAUDE_CODE_FILE_TOOLS) + dis
    canonical = claude_code_argv(argv[0], settings_path=first_after("--settings") or "",
                                 empty_mcp_path=first_after("--mcp-config") or "", memdir=memdir, offered_tools=offered)
    if launcher:                                          # node + cli.js: the same flags after the two-word launcher
        canonical = list(launcher) + canonical[1:]
    if list(argv) != canonical:
        out.append("argv is not exactly the §2.6.6 argv")
    if dict(settings) != claude_code_settings(memdir):
        out.append("settings are not exactly the unit settings")
    if any(_bypass_in(a) for a in argv):
        out.append("argv names a bypass permission mode")
    if any(_bypass_in(v) for v in _walk_values(settings)):
        out.append("settings name a bypass permission mode")
    args = list(argv[1:])

    def after(flag: str) -> str | None:
        return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None

    for flag, want in (("--output-format", "json"), ("--permission-mode", "default"), ("--setting-sources", "user")):
        if after(flag) != want:
            out.append(f"{flag} is not {want}")
    if any(a.startswith("--permission-mode=") for a in args):
        out.append("--permission-mode in = form")
    for flag in ("-p", "--strict-mcp-config", "--settings", "--mcp-config", "--allowedTools", "--disallowedTools"):
        if flag not in args:
            out.append(f"missing {flag}")
    if "--allowedTools" in args:
        i = args.index("--allowedTools") + 1
        got = []
        while i < len(args) and not args[i].startswith("--"):
            got.append(args[i])
            i += 1
        root = memdir.as_posix().rstrip("/")
        if sorted(got) != sorted(f"{t}({root}/**)" for t in CLAUDE_CODE_FILE_TOOLS):
            out.append("--allowedTools is not exactly the four memory-directory rules")
    if settings.get("disableAllHooks") is not True:
        out.append("settings do not disable all hooks")
    modes = [v for k, v in _walk_items(settings) if k == "defaultMode"]
    if any(m != "default" for m in modes):
        out.append("settings carry a defaultMode other than default")
    return out


def _walk_items(obj):
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            yield str(k), v
            yield from _walk_items(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_items(v)


# ── the proxy process (A2.4) ────────────────────────────────────────────

def spawn_proxy(c: Contract, python: str | os.PathLike, *, script: Path, config_path: Path, key_file: Path | None = None,
                stdin_secrets: Mapping, unit: UnitDirs, parent_env: Mapping[str, str], ready_timeout: float = 30.0,
                popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                witnesses: "Witnesses | None" = None, catcher_only: bool = False) -> tuple[Child, dict]:
    """Start research/_llm_proxy.py under the contract (§2.6.1): the only spawn whose argv may name the key file.

    Its read exceptions are exact paths at exact argv indexes (X6): its own script (1) and the key file (6) - never
    the repository or the secrets directory as a root; the repository is on no import path (the proxy imports only
    the standard library). Its environment carries no key, no token and no proxy variable; the tokens arrive as
    one JSON line on stdin, then EOF. It is started unwitnessed by the arm witness, with that reason recorded: its own upstream is the cloud,
    and every call it makes is its own record. With ``witnesses``, its pid becomes an allowed loopback listener for
    the witnessed trees (W7) - its write, reader and catcher ports. Returns (child, ports) once READY names the
    ports file's sha256.

    ``catcher_only`` (A3.a, the auditor's O3a) starts the `catch` mode for fetch windows: no key file in argv (its only
    read exception is its script), no arm write port, no upstream probe; its catcher tunnels a window's hosts
    through the declared hop."""
    if catcher_only == (key_file is not None):
        raise ContractViolation(["the proxy takes the key file exactly when it is not catcher-only"])
    env = build_env(c, parent_env=parent_env, unit=unit, path_dirs=[Path(python).parent], declared={},
                    catcher_url="", proxies=False)
    if catcher_only:
        argv = [os.fspath(python), os.fspath(script), "catch", "--config", os.fspath(config_path)]
        exc = {1: Path(script)}
        reason = "the catcher-only proxy is the instrument: it reads no key and tunnels declared window hosts"
    else:
        argv = [os.fspath(python), os.fspath(script), "serve", "--config", os.fspath(config_path),
                "--key-file", os.fspath(key_file)]
        exc = {1: Path(script), 6: Path(key_file)}
        reason = "the proxy is the instrument: its upstream is the cloud and it records each call"
    child = spawn(c, argv, env=env, cwd=unit.cwd, record={"role": "proxy"}, parent_env=parent_env, catcher_url="",
                  argv_exception=exc, popen=popen, requirement="optional", unwitnessed_reason=reason,
                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child.process.stdin.write((json.dumps(dict(stdin_secrets)) + "\n").encode("utf-8"))
    child.process.stdin.close()
    line: list[bytes] = []
    reader = threading.Thread(target=lambda: line.append(child.process.stdout.readline()), daemon=True)
    reader.start()
    reader.join(ready_timeout)
    got = line[0].decode("utf-8", "replace").strip() if line else ""
    if not got.startswith("READY "):
        child.kill_tree()
        raise ContractViolation(["the proxy did not report READY in time"])
    run_dir = Path(json.loads(Path(config_path).read_text(encoding="utf-8"))["run_dir"])
    data = (run_dir / "ports.json").read_bytes()
    if hashlib.sha256(data).hexdigest() != got.split(" ", 1)[1]:
        child.kill_tree()
        raise ContractViolation(["the ports file does not match the READY line"])
    if witnesses is not None and witnesses.native is not None and not witnesses.native.allow_listener(
            child.process.pid, "the v3 proxy"):
        child.kill_tree()
        raise ContractViolation(["the proxy's identity could not be read, so its ports cannot be allowed (W7)"])
    return child, json.loads(data)
