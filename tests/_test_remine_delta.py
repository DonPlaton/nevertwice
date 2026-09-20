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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

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

# ── a watermark is a size that was READ, never a fabricated zero ───────────────────────
# `_transcript_grew` measures a file against the size recorded when its content was read, so a
# recorded 0 means "we read an empty file" and every later byte is growth. Two callers wrote a 0
# they had not measured: the generic-ingestion path ("sid is a content hash - growth cannot
# occur", which is true of the sid and not of the PATH recorded beside it, and ingest's sweep
# passes a real file there), and the OSError arm of the pre-read stat - where `mark_processed`'s
# own rule is to record NO watermark. Either one marks a live transcript as processed-at-zero,
# and then every event re-mines the whole session through the extractor, for good.
print("\n- a watermark is a size that was read -")
_d = make_sandbox(m, "rmd_", offline=True)
m.update_embeddings = lambda notes: None
m.generate_json = lambda prompt, project=None: {
    "project_relevant": True, "patterns": [], "mistakes": [], "decisions": [],
    "session_summary": "a short session", "context_update": ""}

with tempfile.TemporaryDirectory() as td:
    tp = Path(td) / "grown.jsonl"
    tp.write_text("".join(evt(f"line {i}") for i in range(20)), encoding="utf-8")
    real_size = tp.stat().st_size

    db: dict = {}
    m.process_session("w-text", r"D:\Coding\x", str(tp), "ingest", db,
                      transcript_text=tp.read_text(encoding="utf-8"),
                      project_override="rmdproj")
    entry = db.get("w-text") or {}
    check("text supplied with a real path does not record a zero watermark for it",
          entry.get("bytes") != 0, json.dumps(entry))
    check("so the same transcript does not read as grown forever",
          m._transcript_grew(entry, str(tp)) is False, json.dumps(entry))

    db = {}
    real_getsize = os.path.getsize

    def _stat_fails(path):
        raise OSError("transient")

    os.path.getsize = _stat_fails
    try:
        m.process_session("w-stat", r"D:\Coding\x", str(tp), "ingest", db,
                          project_override="rmdproj")
    finally:
        os.path.getsize = real_getsize
    entry = db.get("w-stat") or {}
    check("a transcript whose size could not be read records no watermark, not a zero",
          entry.get("bytes") != 0, json.dumps(entry))
    check("and it does not re-mine itself on every later event",
          m._transcript_grew(entry, str(tp)) is False, json.dumps(entry))
    db = {}
    m.process_session("w-ok", r"D:\Coding\x", str(tp), "ingest", db,
                      project_override="rmdproj")
    entry = db.get("w-ok") or {}
    check("while a size that WAS read is still recorded", entry.get("bytes") == real_size,
          json.dumps(entry))
    with tp.open("a", encoding="utf-8") as f:
        f.write(evt("a post-compaction tail"))
    check("and still detects the growth it exists for",
          m._transcript_grew(entry, str(tp)) is True)

print(f"\nremine delta: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
