#!/usr/bin/env python3
"""PREREG-V3 TB4.4 (A6): research/v3/render_ama_jsonl.py - the ruled Q16 mapping, read back by the engine's own reader.

* F-R1: a synthetic trajectory (real key names from j4: task, trajectory of {action, observation, turn_idx}, num_turns,
  episode_id, qa_pairs) renders to exactly: user(task), then per step assistant tool_use Bash {command} and user
  tool_result; no reasoning is synthesised; qa_pairs, success, task_type, domain, total_tokens are not rendered;
* F-R2: nevertwice's own read_transcript over the file yields USER <task>, one TOOL[Bash] line per step in order, no
  observation text, every line <= MAX_MESSAGE_CHARS + its prefix, and the unit dir and ingest time as cwd/timestamp;
  a raw U+2028 inside a command stays inside its line and reaches the reader whole;
* F-R3: every event carries the unit dir and the one ingest time;
* refusals: a None action or observation (by name), turn_idx out of order, num_turns != len, a step with other keys,
  an empty task; the file is never overwritten.

    python tests/research/_test_v3_render_ama.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "nevertwice"))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

_spec = importlib.util.spec_from_file_location("v3_render_ama", ROOT / "research" / "v3" / "render_ama_jsonl.py")
R = importlib.util.module_from_spec(_spec)
sys.modules["v3_render_ama"] = R
_spec.loader.exec_module(R)
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def refused(fn, words: str = "") -> bool:
    try:
        fn()
    except R.RenderRefused as e:
        return words in str(e)
    except Exception:  # noqa: BLE001
        return False
    return False


OBS = "SECRET_OBSERVATION_TEXT the file listing"
LONG = "x" * 5000
REC = {"episode_id": 17, "domain": "SOFTWARE", "task": "Fix the failing test in utils.py", "task_type": "bugfix",
       "success": True, "total_tokens": 999, "num_turns": 3,
       "qa_pairs": [{"question": "QA_QUESTION_TEXT", "answer": "QA_ANSWER_TEXT", "question_uuid": "u1", "type": "t"}],
       "trajectory": [{"turn_idx": 0, "action": "ls -la", "observation": OBS},
                      {"turn_idx": 1, "action": "grep -n 'a b' utils.py", "observation": "no match"},
                      {"turn_idx": 2, "action": LONG, "observation": "done"}]}
UNIT = Path(tempfile.gettempdir()) / "nvt3_unit_S7_17"
T0 = "2026-10-01T09:00:00.000000+00:00"

print("\n- F-R1: the ruled mapping (Q16) -")
ev = R.render(REC, unit_dir=UNIT, ingest_utc=T0)
kinds = [(e["type"], (e["message"]["content"][0]["type"] if isinstance(e["message"]["content"], list) else "text"))
         for e in ev]
check("user(task), then per step: assistant tool_use, user tool_result - nothing else",
      kinds == [("user", "text")] + [("assistant", "tool_use"), ("user", "tool_result")] * 3, str(kinds))
check("the first user message is the task, verbatim", ev[0]["message"]["content"] == REC["task"])
tu = [next((c for c in e["message"]["content"] if c.get("type") == "tool_use"), {}) for e in ev if e["type"] == "assistant"]
check("each action is a tool_use named Bash with {command: <action>}, in list order",
      [t.get("name") for t in tu] == ["Bash"] * 3 and [t.get("input") for t in tu] == [{"command": s["action"]} for s in REC["trajectory"]])
tr = [e["message"]["content"][0] for e in ev if e["type"] == "user" and isinstance(e["message"]["content"], list)]
check("each observation is a tool_result answering its tool_use", [t["content"] for t in tr]
      == [s["observation"] for s in REC["trajectory"]] and [t["tool_use_id"] for t in tr] == [t.get("id") for t in tu])
flat = json.dumps(ev, ensure_ascii=False)
check("no reasoning is synthesised: no assistant text block at all", not any(
    c.get("type") == "text" for e in ev if e["type"] == "assistant" for c in e["message"]["content"]))
check("qa_pairs, success, task_type, domain and total_tokens are never rendered",
      "QA_QUESTION_TEXT" not in flat and "QA_ANSWER_TEXT" not in flat and "bugfix" not in flat and "SOFTWARE" not in flat
      and '"success"' not in flat and "999" not in flat)

print("\n- F-R3: cwd and the ingest time on every event -")
check("every event carries the unit dir as cwd and the one ingest time",
      all(e["cwd"] == str(UNIT) and e["timestamp"] == T0 for e in ev))

print("\n- F-R2: the engine's own reader over the file -")
with tempfile.TemporaryDirectory(prefix="v3render_") as td:
    p = Path(td) / "t.jsonl"
    sha = R.write_jsonl(ev, p)
    raw = p.read_bytes()
    check("the file is UTF-8, one event per LF line, and write_jsonl returns its sha256",
          b"\r" not in raw and raw.count(b"\n") == len(ev) and sha == hashlib.sha256(raw).hexdigest())
    got = m.read_transcript(str(p))
    lines = got["body"].split("\n")
    check("the reader yields USER <task> first", lines[0] == f"USER: {REC['task']}", lines[0])
    tool_lines = [ln for ln in lines if ln.startswith("TOOL[Bash]: ")]
    check("one TOOL[Bash] line per step, in order", len(tool_lines) == 3 and '"ls -la"' in tool_lines[0]
          and "grep" in tool_lines[1], str(tool_lines[:2]))
    check("a raw U+2028 inside a command stays inside its line and reaches the reader whole",
          "a b" in tool_lines[1] and len(lines) == 4, repr(tool_lines[1][:60]))
    check("no observation text reaches the reader (tool_result is dropped)", OBS not in got["body"]
          and "no match" not in got["body"])
    check("every line is capped at MAX_MESSAGE_CHARS plus its prefix",
          all(len(ln) <= m.MAX_MESSAGE_CHARS + len("TOOL[Bash]: ") for ln in lines)
          and len(tool_lines[2]) == m.MAX_MESSAGE_CHARS + len("TOOL[Bash]: "), str(len(tool_lines[2])))
    check("the reader takes the unit dir and the ingest time as the session's cwd and timestamp",
          got["cwd"] == str(UNIT) and got["timestamp"] == T0)
    try:
        R.write_jsonl(ev, p)
        over = False
    except FileExistsError:
        over = True
    check("a rendered file is never overwritten", over)

print("\n- render_text: the rawtext row's input (Q15 O-a) -")
txt = R.render_text(REC)
tl = txt.split("\n")
check("render_text: the reader's line format with each observation kept as an OBSERVATION: line",
      tl[0] == f"USER: {REC['task']}" and tl[1].startswith('TOOL[Bash]: {"command": "ls -la"}') and tl[2] == f"OBSERVATION: {OBS}"
      and len(tl) == 1 + 2 * 3, str(tl[:3]))
check("render_text's tool lines equal the reader's own for the same file (same format, same cap)",
      [ln for ln in tl if ln.startswith("TOOL[")] == tool_lines)
check("the renderer's line cap equals the engine reader's MAX_MESSAGE_CHARS", R.MAX_MESSAGE_CHARS == m.MAX_MESSAGE_CHARS)

print("\n- refusals -")


def with_step(i, **kw):
    r = json.loads(json.dumps(REC))
    r["trajectory"][i].update(kw)
    return r


check("a None action is refused by name, never dropped",
      refused(lambda: R.render(with_step(1, action=None), unit_dir=UNIT, ingest_utc=T0), "action is None - refused"))
check("a None observation is refused by name", refused(
    lambda: R.render(with_step(0, observation=None), unit_dir=UNIT, ingest_utc=T0), "observation is None - refused"))
check("a turn_idx that is not its position is refused",
      refused(lambda: R.render(with_step(2, turn_idx=5), unit_dir=UNIT, ingest_utc=T0), "turn_idx"))
check("num_turns other than the number of steps is refused",
      refused(lambda: R.render({**REC, "num_turns": 4}, unit_dir=UNIT, ingest_utc=T0), "num_turns"))
check("a step with a key outside {action, observation, turn_idx} is refused (e.g. a reasoning field would need a ruling)",
      refused(lambda: R.render(with_step(0, reasoning="think"), unit_dir=UNIT, ingest_utc=T0), "not exactly"))
check("an empty task is refused", refused(lambda: R.render({**REC, "task": "  "}, unit_dir=UNIT, ingest_utc=T0), "task"))

check("render_text refuses what render refuses (a None observation)",
      refused(lambda: R.render_text(with_step(0, observation=None)), "observation is None"))

print(f"\nv3 render ama: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
