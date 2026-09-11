#!/usr/bin/env python3
"""K6: the literal channel quotes the session, never the hook's own preamble.

`process_session` prepends `Working directory: <cwd>` and `Trigger: <trigger>` to the transcript it
hands the extractor. The literal harvester read that whole text, so the working directory landed in
nearly every note's `[facts]` block as a "fact" - 27 of 31 notes in the K1 store, 101 of 110 served
blocks in an explicit-corpus run (ledger K6): noise in every note, bytes in every store, one literal
shared by every note of a project. The harvester now reads the session body only; an extractor-named
"fact" that exists only in the frame is dropped by the same verbatim check as any invention. A path
the session itself mentions is still in the body and still quotable.
"""
import _env_guard  # noqa: F401
import sys
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


CWD = r"D:\Coding\nevertwice"
BODY = "Cleared the open question on models: the embedder is bge-m3.\nRan the full test suite afterwards and it stayed green."
FULL = f"Working directory: {CWD}\nTrigger: ingest\n\n{BODY}"

print("\n- the frame is stripped, the session is kept -")
src = m._facts_source(FULL)
check("both preamble lines are gone", not src.startswith("Working directory") and "Trigger:" not in src, src[:60])
check("the body is intact", src == BODY, repr(src[:80]))
check("a text without a frame is untouched", m._facts_source(BODY) == BODY)
check("a body that mentions the path keeps it",
      CWD in m._facts_source(f"Working directory: {CWD}\nTrigger: ingest\n\nedited {CWD}\\README.md today"))

print("\n- the per-note literals come from the session -")
# the description names no literal, so every literal on the note must come through the facts channel
item = {"title": "embedder choice", "description": "the open question on models is settled", "facts": [CWD, "bge-m3"]}
facts = m._note_facts(item, m._facts_source(FULL))
check("the extractor-named cwd is dropped (not in the session)", not any(CWD.lower() in f.lower() for f in facts), str(facts))
check("the session's own literal survives", any("bge-m3" in f for f in facts), str(facts))

print("\n- mutation check: fed the framed text, the harvester keeps the cwd -")
facts_framed = m._note_facts(item, FULL)
check("the old source yields the cwd literal - what the gate removes",
      any(CWD.lower() in f.lower() for f in facts_framed), str(facts_framed))

print("\n- through process_session: the note on disk carries no cwd literal -")
d = make_sandbox(m, "k6_", offline=True)
m.update_embeddings = lambda notes: None
m.generate_json = lambda *a, **k: {
    "project_relevant": True, "patterns": [], "mistakes": [],
    "decisions": [{"title": "embedder choice", "description": "the open question on models is settled",
                   "facts": [CWD, "bge-m3"], "confidence": 0.9}],
    "session_summary": "models settled", "context_update": ""}
ok = m.process_session("k6sid", CWD, "", "ingest", {}, transcript_text=BODY, project_override="k6proj")
notes = list((d / "Decisions").glob("*k6proj-decision-*.md"))
check("one decision written", ok and len(notes) == 1, str([n.name for n in notes]))
text = notes[0].read_text(encoding="utf-8") if notes else ""
check("the served description carries the session's literal", "bge-m3" in text)
check("and not the working directory", CWD.lower() not in text.lower(), text[:300])

print(f"\nharvester skips the preamble: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
