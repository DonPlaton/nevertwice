#!/usr/bin/env python3
"""A grown transcript is mined over its NEW region, and never loses its tail or its date.

The 2026-09 vault review traced seven findings to one chain: any growth re-triggered a
full re-mine, `truncate_smart` kept a head+tail window anchored to EOF, so growth slid
the tail, the extractor saw a different document and invented different titles, and the
slug-keyed absorb missed - retiring the old notes as superseded by their own rename.

The first fix added a growth floor of one extractor window. That lost the end of every
session that finished within 12 kB of its PreCompact mark, for good: the SessionEnd hook,
both sweeps and process_now gate on the same predicate (review 2026-09-05). The floor is
gone; the fork is closed at its source instead, by reading only the new region and by
keeping the session's own start for the date and the stem.
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


def evt(text, ts="2026-09-02T23:10:00Z", cwd="/tmp/p"):
    return json.dumps({"type": "user", "message": {"role": "user", "content": text},
                       "cwd": cwd, "timestamp": ts}) + "\n"


with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "t.jsonl"
    p.write_text("".join(evt(f"old line {i}") for i in range(50)), encoding="utf-8")
    first = p.stat().st_size

    print("\n- no growth is not a re-mine; any growth is -")
    check("an unchanged transcript is not 'grown'",
          m._transcript_grew({"bytes": first}, str(p)) is False)
    with p.open("a", encoding="utf-8") as f:
        f.write(evt("tiny"))
    check("one appended event is 'grown' - a lost tail is lost for good",
          m._transcript_grew({"bytes": first}, str(p)) is True,
          f"grew by {p.stat().st_size - first}B, floor {m.REMINE_MIN_GROWTH_BYTES}")
    check("the floor is one byte by default", m.REMINE_MIN_GROWTH_BYTES == 1,
          str(m.REMINE_MIN_GROWTH_BYTES))
    check("a legacy entry with no watermark never re-triggers",
          m._transcript_grew({}, str(p)) is False)

    print("\n- a re-mine reads only the new region -")
    with p.open("a", encoding="utf-8") as f:
        f.write("".join(evt("N" * 200, ts="2026-09-03T00:40:00Z", cwd="/tmp/other")
                        for _ in range(200)))
    full = m.read_transcript(str(p))["body"]
    tail = m.read_transcript(str(p), from_byte=first)
    delta = tail["body"]
    check("the delta is shorter than the whole", 0 < len(delta) < len(full))
    check("the delta excludes the old content", "old line 0" not in delta)
    check("the delta keeps the new content", "NNN" in delta)
    check("the full read still sees everything", "old line 0" in full and "NNN" in full)

    print("\n- a re-mine keeps the session's own start -")
    check("the timestamp is the FIRST event's, not the first after the watermark",
          (tail["timestamp"] or "").startswith("2026-09-02T23:10"), str(tail["timestamp"]))
    check("the cwd is the session's, not the tail's", tail["cwd"] == "/tmp/p", str(tail["cwd"]))
    check("a read from zero is unchanged",
          m.read_transcript(str(p))["timestamp"].startswith("2026-09-02T23:10"))

    print("\n- a watermark landing mid-line yields whole events only -")
    mid = m.read_transcript(str(p), from_byte=first + 7)["body"]
    check("a mid-line offset does not produce a partial event", "{" not in mid.split("\n")[0])

print(f"\nremine delta: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
