#!/usr/bin/env python3
"""A grown transcript is mined over its NEW region, not re-read from zero.

The 2026-09 vault review traced seven findings to one chain: any growth re-triggered a
full re-mine, `truncate_smart` kept a head+tail window anchored to EOF, so growth slid
the tail, the extractor saw a different document and invented different titles, and the
slug-keyed absorb missed - retiring the old notes as superseded by their own rename.

Two properties close it: a sliver of growth does not re-trigger at all, and a re-mine
that does happen reads only what was added.
"""
import _env_guard  # noqa: F401
import sys, tempfile, json, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import memory_hook as m  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def evt(text):
    return json.dumps({"type": "user", "message": {"role": "user", "content": text},
                       "cwd": "/tmp/p", "timestamp": "2026-09-02T00:00:00Z"}) + "\n"


with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "t.jsonl"
    p.write_text("".join(evt(f"old line {i}") for i in range(50)), encoding="utf-8")
    first = p.stat().st_size

    print("\n- a sliver of growth does not re-trigger a re-mine -")
    with p.open("a", encoding="utf-8") as f:
        f.write(evt("tiny"))
    check("growth below the floor is not 'grown'",
          m._transcript_grew({"bytes": first}, str(p)) is False,
          f"grew by {p.stat().st_size - first}B, floor {m.REMINE_MIN_GROWTH_BYTES}")
    check("the floor is a real number, not zero", m.REMINE_MIN_GROWTH_BYTES > 0)

    print("\n- growth past the floor does re-trigger -")
    with p.open("a", encoding="utf-8") as f:
        f.write("".join(evt("N" * 200) for _ in range(200)))
    check("growth past the floor is 'grown'", m._transcript_grew({"bytes": first}, str(p)) is True)
    check("a legacy entry with no watermark never re-triggers",
          m._transcript_grew({}, str(p)) is False)

    print("\n- a re-mine reads only the new region -")
    full = m.read_transcript(str(p))["body"]
    delta = m.read_transcript(str(p), from_byte=first)["body"]
    check("the delta is shorter than the whole", 0 < len(delta) < len(full))
    check("the delta excludes the old content", "old line 0" not in delta)
    check("the delta keeps the new content", "NNN" in delta)
    check("the full read still sees everything", "old line 0" in full and "NNN" in full)

    print("\n- a watermark landing mid-line yields whole events only -")
    mid = m.read_transcript(str(p), from_byte=first + 7)["body"]
    check("a mid-line offset does not produce a partial event", "{" not in mid.split("\n")[0])

print(f"\nremine delta: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
