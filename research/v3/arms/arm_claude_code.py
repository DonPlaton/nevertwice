#!/usr/bin/env python3
"""PREREG-V3 TB4.8 (A8): the claude-code-memory adapter - one child per (run, unit) and stage (Q25), speaking base.py's
protocol. It runs on the polygon's stdlib venv (arms314) from the arm's runs-tree directory beside byte-identical copies
of base.py and _http_count.py (Q9 O-b), imports the standard library only, and drives the polygon-pinned Claude Code
binary (@anthropic-ai/claude-code-win32-x64 2.1.304) against the arm's proxy port (/u/<run>.<unit>/anthropic) in the
write stage, then reads MEMORY.md and topic files from the sealed unit store in the read stage.

    <arms314 python> -B arm_claude_code.py <spec.json>

rev1 §2.2, §2.6.6, §2.6.8, §2.6.9:
* Writer: the polygon-pinned binary (sha256 checked at every bind) against DeepSeek's Anthropic endpoint through its
  proxy port (/u/<run>.<unit>/anthropic); ANTHROPIC_MODEL, ANTHROPIC_SMALL_FAST_MODEL and every tier and subagent model
  variable set to deepseek-flash; thinking disabled via MAX_THINKING_TOKENS=0 and the proxy's branch (b) fallback;
  experimental betas disabled (CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1) so only the allowed file tools are offered;
* Write unit: one fresh `claude -p` per session, with the prompt "Conversation from <YYYY-MM-DD>:\\n<transcript>" on
  dated stands (or "<transcript>" on undated stands); cwd = an empty working directory inside the unit directory;
  CLAUDE_CONFIG_DIR = the unit's fresh config directory in <unit>.home/claude_config holding only our settings.json
  with disableAllHooks=True, defaultMode="default" and the unit's x-nvt3-home-canary header (R-CC-WIT);
  CLAUDE_COWORK_MEMORY_PATH_OVERRIDE = <unit>/store (the unit's memory directory, carried across sessions and sealed by
  end_write); tools locked by --allowedTools Read/Write/Edit/MultiEdit(<store>/**) and --disallowedTools for every other
  offered tool (§2.6.6);
* Read, by the stand reader:
  - Point B ("R-all"): MEMORY.md, then every other file in lexicographic relative-path order (Q28);
  - Point K: refused as competitor-lacks-capability:k;
  - Point V ("R-index"): MEMORY.md's first 200 LF-terminated lines or 25 KB, whichever ends first.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import base as B  # noqa: E402 - the protocol module, copied beside this file
import _http_count as HC  # noqa: E402 - the counting-only HTTP wrapper, copied beside this file

ARM = "claude-code-memory"
STAGES = ("write", "read")
SPEC_KEYS = ("arm", "stage", "stand", "run", "unit", "unit_dir", "port", "dated", "claude_binary", "claude_sha256",
             "claude_version", "offered_tools", "record_path")
DEEPSEEK_MODEL = "deepseek-flash"
TOKEN_NAME = "ANTHROPIC_AUTH_TOKEN"
TOKEN_NAMES = ("ANTHROPIC_AUTH_TOKEN", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")
PIN_VERSION = "2.1.304"
PIN_SHA256 = "019e5174d5d954585edb1a946fe280e44a973050b4897034adf0e16306f85fdd"
FILE_TOOLS = ("Read", "Write", "Edit", "MultiEdit")
OFFERED_TOOLS = ("AskUserQuestion", "Bash", "CronCreate", "CronDelete", "CronList", "Edit", "EnterWorktree",
                 "ExitWorktree", "Glob", "Grep", "JavaScript", "ListAgents", "NotebookEdit", "REPL", "Read",
                 "ReportFindings", "ScheduleWakeup", "SendMessage", "SendUserMessage", "Skill", "Task", "TaskCreate",
                 "TaskGet", "TaskList", "TaskStop", "TaskUpdate", "TodoWrite", "ToolSearch", "WebFetch", "WebSearch",
                 "Workflow", "Write")
MODEL_VARS = ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
              "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")
HOME_CANARY_HEADER = "x-nvt3-home-canary"
_HOME_CANARY_RE = re.compile(re.escape(HOME_CANARY_HEADER) + r": ([0-9a-f]{32})")
MEMORY_INDEX = "MEMORY.md"
R_INDEX_LINES = 200
R_INDEX_BYTES = 25 * 1024
POINTS = ("B", "V")
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Refused(RuntimeError):
    """The arm may not start, or may not take this request."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_spec(path: str | os.PathLike) -> dict:
    spec = json.loads(Path(path).read_bytes().decode("utf-8"))
    missing = [k for k in SPEC_KEYS if k not in spec]
    extra = sorted(set(spec) - set(SPEC_KEYS))
    if missing or extra:
        raise Refused(f"the spec lacks {missing} or carries unknown keys {extra}")
    if spec["arm"] != ARM or spec["stage"] not in STAGES:
        raise Refused(f"unknown arm {spec['arm']!r} or stage {spec['stage']!r}")
    if not (_ID.fullmatch(str(spec["run"])) and _ID.fullmatch(str(spec["unit"]))):
        raise Refused("run and unit ids are [A-Za-z0-9_-]{1,64}, no dots (Q3)")
    if not Path(spec["unit_dir"]).is_absolute():
        raise Refused("unit_dir is not an absolute path")
    if not isinstance(spec["port"], int):
        raise Refused("claude-code-memory needs its proxy port in the spec")
    if not isinstance(spec["offered_tools"], list) or not spec["offered_tools"]:
        raise Refused("offered_tools must be a non-empty list of tool names")
    return spec


def proxy_base(spec: Mapping[str, Any]) -> str:
    return f"http://127.0.0.1:{spec['port']}/u/{spec['run']}.{spec['unit']}/anthropic"


def claude_argv(binary: Path, *, settings_path: Path, empty_mcp_path: Path, store: Path,
                offered_tools: Sequence[str]) -> list[str]:
    root = store.as_posix().rstrip("/")
    allowed_rules = [f"{t}({root}/**)" for t in FILE_TOOLS]
    disallowed = sorted(t for t in offered_tools if t not in FILE_TOOLS)
    return [os.fspath(binary), "-p", "--output-format", "json", "--permission-mode", "default",
            "--setting-sources", "user", "--settings", os.fspath(settings_path), "--strict-mcp-config",
            "--mcp-config", os.fspath(empty_mcp_path), "--allowedTools", *allowed_rules,
            "--disallowedTools", *disallowed]


def check_settings_file(settings_path: Path, empty_mcp_path: Path) -> None:
    if not settings_path.is_file() or settings_path.is_symlink():
        raise Refused("the unit's claude_config/settings.json is missing or a symlink")
    if not empty_mcp_path.is_file() or empty_mcp_path.is_symlink():
        raise Refused("the unit's empty-mcp.json is missing or a symlink")
    try:
        s = json.loads(settings_path.read_text(encoding="utf-8"))
        m = json.loads(empty_mcp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refused(f"settings or empty-mcp is unreadable ({type(e).__name__})") from None
    if not isinstance(s, dict) or set(s) != {"disableAllHooks", "permissions", "env"}:
        raise Refused("settings.json keys are not exactly {disableAllHooks, permissions, env}")
    if s.get("disableAllHooks") is not True or s.get("permissions") != {"defaultMode": "default"}:
        raise Refused("settings.json does not lock disableAllHooks=True and defaultMode='default'")
    env_blk = s.get("env")
    hdr = env_blk.get("ANTHROPIC_CUSTOM_HEADERS") if isinstance(env_blk, dict) and set(env_blk) == {"ANTHROPIC_CUSTOM_HEADERS"} else None
    if not isinstance(hdr, str) or not _HOME_CANARY_RE.fullmatch(hdr):
        raise Refused("settings.json lacks the unit's x-nvt3-home-canary header (R-CC-WIT)")
    if m != {"mcpServers": {}}:
        raise Refused("empty-mcp.json is not {'mcpServers': {}}")


def declared(spec: Mapping[str, Any]) -> dict:
    return {"date_route": "header" if spec["dated"] else "none",
            "renderer": {"name": "Point B: MEMORY.md then topic files in lexicographic order; Point V: MEMORY.md first 200 lines or 25 KB"},
            "threshold": "n/a",
            "k": "Point B all files, Point K competitor-lacks-capability:k, Point V R-index",
            "namespace": "fresh CLAUDE_CONFIG_DIR and <unit>/store memory directory per unit",
            "write_granularity": "one fresh `claude -p` per session",
            "llm_params": {"model": DEEPSEEK_MODEL, "temperature": "product default (not sent)",
                           "max_tokens": "32000 (the binary's default for deepseek-flash)",
                           "thinking_route": "MAX_THINKING_TOKENS=0 + proxy fallback {'thinking': {'type': 'disabled'}}"},
            "deviations": [],
            "tools_allowed": list(FILE_TOOLS), "store_persistence": "disk"}


def bind(spec: Mapping[str, Any], env: Mapping[str, str]) -> tuple[dict, dict]:
    unit_dir = Path(spec["unit_dir"])
    store = unit_dir / "store"
    work_dir = unit_dir / "work"
    unit_home = Path(f"{unit_dir}.home")
    cfg_dir = unit_home / "claude_config"
    settings_path = cfg_dir / "settings.json"
    empty_mcp_path = unit_home / "empty-mcp.json"
    rec: dict[str, Any] = {"arm": ARM, "stage": spec["stage"], "stand": spec["stand"], "run": spec["run"],
                           "unit": spec["unit"], "unit_store": str(store), "declared": declared(spec)}
    binary = Path(spec["claude_binary"])
    if not binary.is_absolute() or not binary.is_file() or binary.is_symlink():
        raise Refused("claude_binary is not a regular file at an absolute path")
    if HERE in binary.resolve().parents:
        raise Refused("claude_binary points inside the arm's code directory, not the polygon-pinned binary")
    actual_sha = _sha256_file(binary)
    if actual_sha != spec["claude_sha256"]:
        raise Refused(f"claude_binary sha256 {actual_sha} does not match spec's {spec['claude_sha256']}")
    rec["product"] = {"name": "@anthropic-ai/claude-code-win32-x64", "version": str(spec["claude_version"]),
                      "file": str(binary.resolve()), "sha256": actual_sha}
    token = env.get(TOKEN_NAME)
    if not token or not token.startswith("nvt3-"):
        raise Refused(f"{TOKEN_NAME} does not hold the arm's proxy token (nvt3-...)")
    if spec["stage"] == "write":
        if store.exists():
            raise Refused("the write stage needs a fresh store, and the unit store already exists")
        check_settings_file(settings_path, empty_mcp_path)
        store.mkdir(parents=True, exist_ok=False)
        work_dir.mkdir(parents=True, exist_ok=True)
        rec["http_doors"] = HC.install(proxy_port=spec["port"], ollama_ports=())
        rec["proxy_base"] = proxy_base(spec)
    else:
        if not store.is_dir():
            raise Refused("the read stage opens the write stage's store, and there is none")
        rec["seal"] = B.check_seal(unit_dir, store)
        rec["http_doors"] = HC.install(proxy_port=None, ollama_ports=())
    rec["env_names"] = sorted(env)
    rec["python"] = sys.version.split()[0]
    return {"binary": binary, "store": store, "work_dir": work_dir, "cfg_dir": cfg_dir,
            "settings_path": settings_path, "empty_mcp_path": empty_mcp_path, "env": dict(env)}, rec


def _memory_files(memdir: Path) -> list[tuple[str, Path]]:
    if not memdir.is_dir():
        raise Refused(f"the memory directory {memdir} is not a directory")
    out = []
    for p in memdir.rglob("*"):
        rel = p.relative_to(memdir).as_posix()
        if p.is_symlink():
            raise Refused(f"{rel} in the memory directory is a link (never followed)")
        if p.is_dir():
            continue
        out.append((rel, p))
    return sorted(out, key=lambda rp: rp[0])


def _read_text(path: Path, label: str) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as e:
        raise Refused(f"{label} is not valid UTF-8 ({e})") from None


def read_r_all(memdir: Path) -> list[str]:
    files = _memory_files(memdir)
    index = [(r, p) for r, p in files if r == MEMORY_INDEX]
    rest = [(r, p) for r, p in files if r != MEMORY_INDEX]
    return [_read_text(p, r) for r, p in index + rest]


def read_r_index(memdir: Path) -> tuple[str, list[list[int]]]:
    files = dict(_memory_files(memdir))
    if MEMORY_INDEX not in files:
        return "", []
    text = _read_text(files[MEMORY_INDEX], MEMORY_INDEX)
    lines = text.split("\n")
    head = "\n".join(lines[:R_INDEX_LINES]) + ("\n" if len(lines) > R_INDEX_LINES else "")
    raw = head.encode("utf-8")
    if len(raw) > R_INDEX_BYTES:
        head = raw[:R_INDEX_BYTES].decode("utf-8", "ignore")
    spans: list[list[int]] = []
    pos = 0
    for ln in head.split("\n"):
        end = pos + len(ln)
        if ln.strip():
            spans.append([pos, end])
        pos = end + 1
    return head, spans


class Handler:
    def __init__(self, spec: Mapping[str, Any], ns: Mapping[str, Any], rec: Mapping[str, Any]) -> None:
        self.spec, self.rec = spec, rec
        self.binary: Path = ns["binary"]
        self.store: Path = ns["store"]
        self.work_dir: Path = ns["work_dir"]
        self.cfg_dir: Path = ns["cfg_dir"]
        self.settings_path: Path = ns["settings_path"]
        self.empty_mcp_path: Path = ns["empty_mcp_path"]
        self.env: dict[str, str] = ns["env"]
        self.stage, self.unit = spec["stage"], spec["unit"]
        self.sessions = {"sessions": 0, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.reads = {"reads": 0, "items_returned": 0}

    def _stage(self, want: str, op: str) -> None:
        if self.stage != want:
            raise RuntimeError(f"{op} belongs to the {want} stage; this process is the {self.stage} stage (Q25)")

    def hello(self) -> dict:
        return {"protocol": B.PROTOCOL, "system": ARM, "arm": ARM, "stage": self.stage,
                "version": self.rec["product"]["version"], "python": self.rec["python"],
                "llm_label": f"deepseek:{DEEPSEEK_MODEL}" if self.stage == "write" else None,
                "embedder": None, "env_names": self.rec["env_names"], "start": self.rec}

    def write(self, item: dict, date: str | None = None) -> dict:
        self._stage("write", "write")
        t0 = time.time()
        text = item["text"]
        if self.spec["dated"]:
            if not date or len(str(date)) < 10:
                raise ValueError("a dated stand's write carries its date (YYYY-MM-DD)")
            day = str(date)[:10]
            prompt = f"Conversation from {day}:\n{text}"
        else:
            prompt = text
        argv = claude_argv(self.binary, settings_path=self.settings_path, empty_mcp_path=self.empty_mcp_path,
                           store=self.store, offered_tools=self.spec["offered_tools"])
        child_env = dict(self.env)
        child_env.update({
            "CLAUDE_CONFIG_DIR": str(self.cfg_dir),
            "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE": str(self.store),
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
            "MAX_THINKING_TOKENS": "0",
            "ANTHROPIC_BASE_URL": proxy_base(self.spec),
            "ANTHROPIC_AUTH_TOKEN": self.env[TOKEN_NAME],
            **{k: DEEPSEEK_MODEL for k in MODEL_VARS},
        })
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=str(self.work_dir), env=child_env)
        try:
            out, err = proc.communicate(input=prompt.encode("utf-8"), timeout=300)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise RuntimeError(f"claude -p timed out on session {item.get('item_id')!r}") from None
        if proc.returncode != 0:
            tail = (err or out or b"").decode("utf-8", "replace")[-240:]
            raise RuntimeError(f"claude -p exited with {proc.returncode}: {tail}")
        try:
            res = json.loads(out.decode("utf-8"))
        except ValueError as e:
            raise RuntimeError(f"claude -p stdout is not JSON ({e})") from None
        if not isinstance(res, dict):
            raise RuntimeError("claude -p stdout is not a JSON object")
        usage = res.get("usage") if isinstance(res.get("usage"), dict) else {}
        inp = int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        iters = res.get("iterations")
        turns = len(iters) if isinstance(iters, list) and iters else 1
        self.sessions["sessions"] += 1
        self.sessions["llm_calls"] += turns
        self.sessions["input_tokens"] += inp
        self.sessions["output_tokens"] += out_tok
        t1 = time.time()
        return {"op_id": item["item_id"], "text_sha256": B.text_sha256(prompt), "t0": t0, "t1": t1}

    def end_write(self) -> dict:
        self._stage("write", "end_write")
        t0 = time.time()
        files = _memory_files(self.store)
        chars = sum(len(_read_text(p, r)) for r, p in files)
        seal = B.write_seal(self.spec["unit_dir"], self.store, arm=ARM, run=self.spec["run"], unit=self.unit)
        return {"footprint": {"retrievable": len(files), "chars": chars}, "seal": seal, "t0": t0, "t1": time.time()}

    def read(self, qid: str, query: str, k: int, point: str | None = None) -> dict:
        self._stage("read", "read")
        if point == "K":
            raise Refused("competitor-lacks-capability:k - claude-code-memory has no top-k search (§2.2)")
        if point not in POINTS:
            raise ValueError(f"point {point!r} is not one of {POINTS}")
        t0 = time.time()
        if point == "V":
            head, spans = read_r_index(self.store)
            items = [{"kind": "rendered", "text": head, "template_sha256": None, "rank": 1}]
            self.reads["reads"] += 1
            self.reads["items_returned"] += 1
            return {"qid": qid, "point": "V", "items": items, "items_returned": 1,
                    "edges": len(spans), "nodes": 0, "v_spans": {"facts": spans, "entities": []},
                    "t0": t0, "t1": time.time()}
        texts = read_r_all(self.store)
        items = [{"text": t, "rank": idx} for idx, t in enumerate(texts, 1)]
        self.reads["reads"] += 1
        self.reads["items_returned"] += len(items)
        return {"qid": qid, "point": "B", "items": items, "items_returned": len(items), "t0": t0, "t1": time.time()}

    def counters(self) -> dict:
        snap = HC.snapshot()
        return {"http": snap, "llm_calls": self.sessions["llm_calls"], **self.sessions, **self.reads}


class RefusedHandler:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __getattr__(self, op: str):
        if op not in B.OPS:
            raise AttributeError(op)

        def refuse(**_kw):
            raise Refused(f"the arm refused to start: {self.reason}")
        return refuse


def _record(path: str, rec: Mapping[str, Any]) -> None:
    Path(path).write_bytes((json.dumps(rec, ensure_ascii=False, sort_keys=True, indent=1, default=str) + "\n")
                           .encode("utf-8"))


def build(spec_path: str, env: Mapping[str, str]) -> Any:
    try:
        spec = load_spec(spec_path)
    except (Refused, OSError, ValueError, KeyError) as e:
        return RefusedHandler(f"{type(e).__name__}: {e}")
    try:
        ns, rec = bind(spec, env)
        handler = Handler(spec, ns, rec)
    except Exception as e:  # noqa: BLE001 - every failure to bind is a named refusal, recorded
        reason = f"{type(e).__name__}: {e}"
        _record(spec["record_path"], {"ok": False, "error": reason[:B.ERROR_MAX], "arm": ARM,
                                      "stage": spec["stage"], "unit": spec["unit"]})
        return RefusedHandler(reason)
    _record(spec["record_path"], {"ok": True, **rec})
    return handler


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("usage: arm_claude_code.py <spec.json>\n")
        sys.exit(2)
    ENV_AT_START = dict(os.environ)
    sys.exit(B.main_with(lambda: build(sys.argv[1], ENV_AT_START)))
