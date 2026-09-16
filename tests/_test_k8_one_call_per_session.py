#!/usr/bin/env python3
"""K8 price gate: exactly one extraction-model call per session in the hook, collision or not.

Two sessions of one project state two facts under one title. Before K8 the collision was decided
by the title alone (no call); K7 put an adjudication call into the hook; K8 keeps the pair apart
with no call and sends it to the sleep-time judge. So the whole hook pipeline must make exactly one
model call per session - the extraction - and the write path none. The extractor is a fake that
counts; a second kind of call would be a second prompt in the list.

    python tests/_test_k8_one_call_per_session.py
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

RUN, FAILED = [], []
CALLS: list[str] = []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def backend(*answers):
    seq = list(answers)

    def fake(prompt, project=None, **kw):
        CALLS.append(prompt)
        return seq.pop(0) if seq else {}
    return fake


BASE = {"project_relevant": True, "patterns": [], "mistakes": [], "session_summary": "short", "context_update": ""}
S1 = {**BASE, "decisions": [{"title": "http client timeout",
                             "description": "the HTTP client timeout is 30 seconds", "facts": ["30 seconds"]}]}
S2 = {**BASE, "decisions": [{"title": "http client timeout",
                             "description": "the HTTP client timeout is 5 seconds", "facts": ["5 seconds"]}]}
B1 = "Spent the session on the API client. Settled it: the HTTP client timeout is 30 seconds."
B2 = "Picked up the API client thread again. Confirmed that the HTTP client timeout is 5 seconds."

d = make_sandbox(m, "k8one_", offline=True)
m.update_embeddings = lambda notes: None
m.EXTRACT_RETRY = 0
m.generate_json = backend(S1, S2)
db: dict = {}
ok1 = m.process_session("k8s1", r"D:\Coding\x", "", "ingest", db, transcript_text=B1, project_override="k8proj")
n1 = len(CALLS)
ok2 = m.process_session("k8s2", r"D:\Coding\x", "", "ingest", db, transcript_text=B2, project_override="k8proj")
notes = sorted(p.stem for p in (d / "Decisions").glob("*.md"))

print("\n- one call a session, none on the write path -")
check("both sessions processed", ok1 and ok2)
check("session one made exactly one model call", n1 == 1, str(n1))
check("session two made exactly one model call - the collision cost none", len(CALLS) == 2, str(len(CALLS)))
check("every call is the extraction prompt (no judge prompt in the hook)",
      all("recorded for one project under the same title" not in c for c in CALLS))
check("the collision produced a '-2' sibling, both statements on disk",
      len(notes) == 2 and notes[1] == f"{notes[0]}-2", str(notes))
fm_old = m._read_frontmatter_file(d / "Decisions" / f"{notes[0]}.md")
check("the earlier note is stamped contested", fm_old.get("contested") == [notes[1]], str(fm_old.get("contested")))
check("no judge counter moved", not m._LLM_STATS.get("absorb_judge"))

print("\n- mutation check: a call sneaked into the write path is caught -")
CALLS.clear()
d = make_sandbox(m, "k8one_", offline=True)
m.update_embeddings = lambda notes: None
_real = m._same_replacement


def leaky(*a, **k):
    m.generate_json("Two statements were recorded for one project under the same title", project="k8proj")
    return _real(*a, **k)


m._same_replacement = leaky
m.generate_json = backend(S1, S2, {"relation": "separate"})
m.process_session("k8s3", r"D:\Coding\x", "", "ingest", {}, transcript_text=B1, project_override="k8proj")
m.process_session("k8s4", r"D:\Coding\x", "", "ingest", {}, transcript_text=B2, project_override="k8proj")
check("three calls for two sessions would fail the gate", len(CALLS) == 3, str(len(CALLS)))
m._same_replacement = _real

print(f"\nK8 one call a session: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
