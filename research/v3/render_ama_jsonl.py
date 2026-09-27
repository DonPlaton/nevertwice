#!/usr/bin/env python3
"""PREREG-V3 TB4.4 (A6): research/v3/render_ama_jsonl.py - an AMA-Bench trajectory as a Claude Code JSONL, for S7's
headline row through the hook's own reader (§2.2), with the field mapping the auditor ruled from the confirmed keys (Q16,
j4 = his blind key sets):

* the record's `task` -> the first user message;
* each step's reasoning -> assistant text: the dataset HAS NO such field (a step is exactly action, observation,
  turn_idx), so it is omitted under rev1's own words ("A role that the dataset's schema lacks is omitted") - nothing is
  synthesised, and the action is never reused as reasoning;
* each `action` -> an assistant tool_use block named "Bash", input {"command": <action>};
* each `observation` -> a user tool_result block with that text (the reader drops it: a named reader and schema layer);
* the step order is the list order, asserted equal to turn_idx 0..n-1, and num_turns == len(trajectory); a None action
  or observation is refused by name, never dropped;
* qa_pairs go to the question stage and are never rendered; episode_id is the unit id; success, total_tokens,
  task_type and domain are not rendered;
* every event carries the unit directory as cwd and the one ingestion wall-clock time; the file is UTF-8, one event
  per LF-terminated line (a raw U+2028 inside a string stays inside its line), never overwritten;
* render_text() is nevertwice-rawtext's input (§2.2, ruling Q15 O-a): the reader's own line format - "USER: ",
  "TOOL[Bash]: " + the tool input as JSON, each line capped at MAX_MESSAGE_CHARS - but KEEPING each observation as an
  "OBSERVATION: " line, which is the point of that row.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

TOOL_NAME = "Bash"
STEP_KEYS = ("action", "observation", "turn_idx")
#: The engine reader's per-line cap (_engine_config MAX_MESSAGE_CHARS); the suite asserts they are equal.
MAX_MESSAGE_CHARS = 2000


class RenderRefused(ValueError):
    """A trajectory the ruled mapping cannot render faithfully; nothing is written."""


def _event(kind: str, content, *, unit_dir: str, ingest_utc: str, session: str, n: int) -> dict:
    return {"type": kind, "message": {"role": kind, "content": content}, "cwd": unit_dir, "timestamp": ingest_utc,
            "sessionId": session, "uuid": f"{session}:{n}"}


def render(record: Mapping, *, unit_dir: Path, ingest_utc: str) -> list[dict]:
    """The events of one trajectory, in order."""
    for k in ("episode_id", "task", "trajectory", "num_turns"):
        if k not in record:
            raise RenderRefused(f"the record lacks {k}")
    task, steps = record["task"], record["trajectory"]
    if not isinstance(task, str) or not task.strip():
        raise RenderRefused(f"episode {record['episode_id']}: the task is not a non-empty string")
    if not isinstance(steps, list):
        raise RenderRefused(f"episode {record['episode_id']}: the trajectory is not a list")
    if record["num_turns"] != len(steps):
        raise RenderRefused(f"episode {record['episode_id']}: num_turns {record['num_turns']} != {len(steps)} steps")
    session = str(record["episode_id"])
    ud = os.fspath(unit_dir)
    out = [_event("user", task, unit_dir=ud, ingest_utc=ingest_utc, session=session, n=0)]
    for i, step in enumerate(steps):
        if not isinstance(step, Mapping) or set(step) != set(STEP_KEYS):
            raise RenderRefused(f"episode {session} step {i}: keys {sorted(step) if isinstance(step, Mapping) else step!r} "
                                f"are not exactly {list(STEP_KEYS)}")
        if step["turn_idx"] != i:
            raise RenderRefused(f"episode {session} step {i}: turn_idx {step['turn_idx']} is not its position")
        for k in ("action", "observation"):
            if step[k] is None:
                raise RenderRefused(f"episode {session} step {i}: {k} is None - refused, never dropped")
            if not isinstance(step[k], str):
                raise RenderRefused(f"episode {session} step {i}: {k} is {type(step[k]).__name__}, not text")
        tid = f"toolu_{session}_{i:04d}"
        out.append(_event("assistant", [{"type": "tool_use", "id": tid, "name": TOOL_NAME,
                                         "input": {"command": step["action"]}}],
                          unit_dir=ud, ingest_utc=ingest_utc, session=session, n=2 * i + 1))
        out.append(_event("user", [{"type": "tool_result", "tool_use_id": tid, "content": step["observation"]}],
                          unit_dir=ud, ingest_utc=ingest_utc, session=session, n=2 * i + 2))
    return out


def render_text(record: Mapping) -> str:
    """nevertwice-rawtext's session text: the same events as render() (so the same refusals), in the reader's line
    format, with the observations kept as OBSERVATION: lines."""
    cap = MAX_MESSAGE_CHARS
    lines = []
    for e in render(record, unit_dir=Path("."), ingest_utc=""):
        content = e["message"]["content"]
        if isinstance(content, str):
            lines.append(f"USER: {content[:cap]}")
            continue
        block = content[0]
        if block["type"] == "tool_use":
            lines.append(f"TOOL[{block['name']}]: {json.dumps(block['input'], ensure_ascii=False)[:cap]}")
        else:
            lines.append(f"OBSERVATION: {block['content'][:cap]}")
    return "\n".join(lines)


def write_jsonl(events: list[dict], path: Path) -> str:
    """One event per LF-terminated line, UTF-8; never overwrites; returns the file's sha256."""
    data = "".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n" for e in events).encode("utf-8")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as f:
        f.write(data)
    return hashlib.sha256(data).hexdigest()
