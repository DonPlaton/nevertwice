#!/usr/bin/env python3
"""PREREG-V3 TB4.8 (A8): research/v3/arms/arm_claude_code.py - the claude-code-memory arm, driven as a real child laid
out as Q9 lays out an arm directory, against a hermetic fake claude script that records every argv, environment variable
and stdin prompt and writes MEMORY.md and topic files into CLAUDE_COWORK_MEMORY_PATH_OVERRIDE.

    python tests/research/_test_v3_arm_claude_code.py
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

ARMS_DIR = ROOT / "research" / "v3" / "arms"
ADAPTER = ARMS_DIR / "arm_claude_code.py"
_spec = importlib.util.spec_from_file_location("v3_arm_base_for_cc", ARMS_DIR / "base.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)
_cc_spec = importlib.util.spec_from_file_location("v3_arm_cc_mod", ADAPTER)
CC = importlib.util.module_from_spec(_cc_spec)
_cc_spec.loader.exec_module(CC)

PASSED = FAILED = 0
TOKEN = "nvt3-claude-code-memory-" + "d" * 32
HOME_CANARY = "c" * 32


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def safely(fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return default if default is not None else repr(e)


TMP = Path(tempfile.mkdtemp(prefix="nvt3_arm_cc_"))
LOG = TMP / "fake_claude_calls.jsonl"
FAKE_BIN_DIR = TMP / "poly" / "claude_code_v3" / "package"
FAKE_BIN_DIR.mkdir(parents=True, exist_ok=True)

FAKE_SCRIPT = FAKE_BIN_DIR / "fake_claude_impl.py"
FAKE_SCRIPT.write_text(
    "import json, os, pathlib, sys\n"
    "prompt = sys.stdin.read()\n"
    "memdir = pathlib.Path(os.environ['CLAUDE_COWORK_MEMORY_PATH_OVERRIDE'])\n"
    "memdir.mkdir(parents=True, exist_ok=True)\n"
    "idx = memdir / 'MEMORY.md'\n"
    "prev = idx.read_text(encoding='utf-8') if idx.is_file() else ''\n"
    "n = len([p for p in memdir.glob('topic_*.md')]) + 1\n"
    "topic = f'topic_{n:02d}.md'\n"
    "(memdir / topic).write_text(f'Fact {n}: {prompt.splitlines()[-1]}\\n', encoding='utf-8')\n"
    "idx.write_text(prev + f'- [Topic {n}]({topic}) — summary {n}\\n', encoding='utf-8')\n"
    "rec = {'argv': sys.argv[1:], 'cwd': os.getcwd(), 'prompt': prompt,\n"
    "       'env': {k: os.environ.get(k) for k in (\n"
    "           'CLAUDE_CONFIG_DIR', 'CLAUDE_COWORK_MEMORY_PATH_OVERRIDE',\n"
    "           'CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS', 'MAX_THINKING_TOKENS',\n"
    "           'ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_MODEL',\n"
    "           'ANTHROPIC_SMALL_FAST_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',\n"
    "           'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',\n"
    "           'CLAUDE_CODE_SUBAGENT_MODEL')}}\n"
    f"with open({str(LOG)!r}, 'ab') as f:\n"
    "    f.write((json.dumps(rec) + '\\n').encode('utf-8'))\n"
    "out = {'stop_reason': 'end_turn', 'session_id': f'sess-{n}',\n"
    "       'usage': {'input_tokens': 100, 'cache_read_input_tokens': 50,\n"
    "                 'cache_creation_input_tokens': 0, 'output_tokens': 25},\n"
    "       'iterations': [{'n': 1}, {'n': 2}]}\n"
    "sys.stdout.write(json.dumps(out))\n",
    encoding="utf-8",
)

if os.name == "nt":
    FAKE_BIN = FAKE_BIN_DIR / "claude.cmd"
    FAKE_BIN.write_text(f'@"{sys.executable}" -B "{FAKE_SCRIPT}" %*\r\n', encoding="utf-8")
else:
    FAKE_BIN = FAKE_BIN_DIR / "claude"
    FAKE_BIN.write_text(f'#!/bin/sh\nexec "{sys.executable}" -B "{FAKE_SCRIPT}" "$@"\n', encoding="utf-8")
    FAKE_BIN.chmod(0o755)

FAKE_SHA = hashlib.sha256(FAKE_BIN.read_bytes()).hexdigest()
OFFERED = ["Bash", "Edit", "MultiEdit", "Read", "Task", "WebFetch", "Write"]
children: list[subprocess.Popen] = []


def arm_dir(name: str) -> Path:
    d = TMP / f"code_{name}"
    d.mkdir(parents=True, exist_ok=True)
    for fn in ("arm_claude_code.py", "base.py", "_http_count.py"):
        shutil.copyfile(ARMS_DIR / fn, d / fn)
    return d


def make_unit(name: str, *, home_canary: str = HOME_CANARY) -> Path:
    udir = TMP / "units" / name
    uhome = Path(f"{udir}.home")
    udir.mkdir(parents=True, exist_ok=True)
    cfg = uhome / "claude_config"
    cfg.mkdir(parents=True, exist_ok=True)
    settings = {"disableAllHooks": True, "permissions": {"defaultMode": "default"},
                "env": {"ANTHROPIC_CUSTOM_HEADERS": f"x-nvt3-home-canary: {home_canary}"}}
    (cfg / "settings.json").write_text(json.dumps(settings, sort_keys=True), encoding="utf-8")
    (uhome / "empty-mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    return udir


def spec_for(name: str, stage: str, udir: Path, *, dated: bool = True, sha: str = FAKE_SHA,
             binary: Path = FAKE_BIN) -> dict:
    return {"arm": "claude-code-memory", "stage": stage, "stand": "S1", "run": "r1", "unit": "u1",
            "unit_dir": str(udir), "port": 41999, "dated": dated, "claude_binary": str(binary),
            "claude_sha256": sha, "claude_version": "2.1.304", "offered_tools": OFFERED,
            "record_path": str(TMP / f"{name}.{stage}.start.json")}


def start(name: str, spec: dict, *, token: str | None = TOKEN, adir: Path | None = None):
    sp = TMP / f"{name}.{spec['stage']}.spec.json"
    sp.write_text(json.dumps(spec), encoding="utf-8")
    d = adir or arm_dir(name)
    errf = TMP / f"{name}.{spec['stage']}.err.txt"
    wd = TMP / f"cwd_{name}_{spec['stage']}"
    wd.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("NEVERTWICE_", "CLAUDE")) and k not in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    if token is not None:
        env["ANTHROPIC_AUTH_TOKEN"] = token
    p = subprocess.Popen([sys.executable, "-B", str(d / "arm_claude_code.py"), str(sp)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(errf, "wb"),
                         cwd=str(wd), env=env)
    children.append(p)
    return B.ArmClient(p, default_timeout=60), errf


def hello_error(name: str, spec: dict, **kw) -> str:
    c, _ = start(name, spec, **kw)
    try:
        c.request("hello")
        return "no refusal"
    except B.ArmError as e:
        return str(e)
    finally:
        c.close()


try:
    print("\n- Q-46-1 and Q9: the adapter's imports -")
    tree = ast.parse(ADAPTER.read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    check("arm_claude_code imports the standard library, base and _http_count only",
          mods <= {"__future__", "hashlib", "json", "os", "re", "subprocess", "sys", "time", "pathlib", "typing",
                   "base", "_http_count"}, str(sorted(mods)))

    print("\n- write stage: two sessions, dated header, locked argv and env, write seal -")
    u1 = make_unit("u1")
    cw, err_w = start("u1", spec_for("u1", "write", u1))
    hw = safely(lambda: cw.request("hello"), {})
    check("hello: system claude-code-memory, version 2.1.304, deepseek-flash label, no embedder",
          hw.get("system") == "claude-code-memory" and hw.get("version") == "2.1.304"
          and hw.get("llm_label") == "deepseek:deepseek-flash" and hw.get("embedder") is None, str(hw)[:300])
    w1 = safely(lambda: cw.request("write", item={"item_id": "s1", "session_id": "s1", "text": "Alice likes tea"},
                                   date="2023-05-20T10:00:00"), {})
    w2 = safely(lambda: cw.request("write", item={"item_id": "s2", "session_id": "s2", "text": "Bob likes coffee"},
                                   date="2023-05-21T12:00:00"), {})
    ew = safely(lambda: cw.request("end_write"), {})
    ct_w = safely(lambda: cw.request("counters"), {})
    cw.close()

    calls = [json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines() if x.strip()]
    check("two claude -p calls ran, with the dated 'Conversation from YYYY-MM-DD:' header on stdin",
          len(calls) == 2 and calls[0]["prompt"] == "Conversation from 2023-05-20:\nAlice likes tea"
          and calls[1]["prompt"] == "Conversation from 2023-05-21:\nBob likes coffee", str(calls))
    c0_env = calls[0]["env"]
    check("child env locks CLAUDE_CONFIG_DIR, CLAUDE_COWORK_MEMORY_PATH_OVERRIDE=<unit>/store, experimental betas off, "
          "MAX_THINKING_TOKENS=0, ANTHROPIC_BASE_URL=/u/r1.u1/anthropic, and all 6 model vars to deepseek-flash",
          c0_env["CLAUDE_COWORK_MEMORY_PATH_OVERRIDE"] == str(u1 / "store")
          and c0_env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] == "1"
          and c0_env["MAX_THINKING_TOKENS"] == "0"
          and c0_env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:41999/u/r1.u1/anthropic"
          and c0_env["ANTHROPIC_AUTH_TOKEN"] == TOKEN
          and all(c0_env[k] == "deepseek-flash" for k in CC.MODEL_VARS), str(c0_env))
    check("end_write sealed MEMORY.md + 2 topic files (retrievable=3) and counters report 2 sessions, 4 llm_calls",
          (ew.get("footprint") or {}).get("retrievable") == 3
          and isinstance((ew.get("seal") or {}).get("sha256"), str)
          and ct_w.get("sessions") == 2 and ct_w.get("llm_calls") == 4
          and ct_w.get("input_tokens") == 300 and ct_w.get("output_tokens") == 50, f"{ew} {ct_w}")

    print("\n- read stage: seal checked, Point B (R-all), Point V (R-index + v_spans), Point K refused -")
    cr, err_r = start("u1", spec_for("u1", "read", u1))
    hr = safely(lambda: cr.request("hello"), {})
    rb = safely(lambda: cr.request("read", qid="q1", query="drinks?", k=200, point="B"), {})
    rv = safely(lambda: cr.request("read", qid="q1", query="drinks?", k=200, point="V"), {})
    rk_err = safely(lambda: cr.request("read", qid="q1", query="drinks?", k=10, point="K"))
    cr.close()
    b_items = rb.get("items") or []
    v_items = rv.get("items") or []
    check("read stage verified the write stage's seal before reading",
          ((hr.get("start") or {}).get("seal") or {}).get("sha256") == (ew.get("seal") or {}).get("sha256"), str(hr))
    check("Point B returns MEMORY.md first, then topic_01.md and topic_02.md in lexicographic order",
          len(b_items) == 3 and "Topic 1" in b_items[0]["text"] and "Alice likes tea" in b_items[1]["text"]
          and "Bob likes coffee" in b_items[2]["text"], str(b_items))
    check("Point V returns one rendered item (MEMORY.md R-index) with 2 line spans in v_spans.facts",
          len(v_items) == 1 and v_items[0].get("kind") == "rendered" and rv.get("edges") == 2 and rv.get("nodes") == 0
          and len((rv.get("v_spans") or {}).get("facts") or []) == 2, str(rv))
    check("Point K is refused as competitor-lacks-capability:k",
          "competitor-lacks-capability:k" in str(rk_err), str(rk_err))

    print("\n- refusals: bad token, wrong binary sha256, tampered settings, tampered store between stages -")
    u_bad = make_unit("u_bad")
    check("missing or non-nvt3 token is refused",
          "ANTHROPIC_AUTH_TOKEN" in hello_error("notok", spec_for("notok", "write", u_bad), token="sk-wrong"))
    check("wrong binary sha256 is refused",
          "sha256" in hello_error("badsha", spec_for("badsha", "write", u_bad, sha="0" * 64)))
    u_bad_set = make_unit("u_bad_set", home_canary="not-hex")
    check("settings.json without a valid 32-hex home canary header is refused",
          "x-nvt3-home-canary" in hello_error("badcan", spec_for("badcan", "write", u_bad_set)))
    (u1 / "store" / "MEMORY.md").write_text("tampered\n", encoding="utf-8")
    check("a store modified between write and read stages is refused by check_seal",
          "changed between the stages" in hello_error("tampered", spec_for("tampered", "read", u1)))
finally:
    for p in children:
        if p.poll() is None:
            p.kill()
            p.wait()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 arm claude code: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
