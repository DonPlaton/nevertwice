#!/usr/bin/env python3
"""K5: one retry, differently framed, when a relevant non-empty session yields no item.

Ledger K3 read the extractor's "silence" on a bare two-sentence fact: of eight first sessions the
as-of stand marked never written, three were silent in both runs and five in one run only; captured
alone, six of eight wrote the note. Same text, temperature zero - the output turns on incidental
prompt context. The mechanism is therefore a second call with the prompt framed as a second pass,
not a prompt rewrite: the first call is byte-identical to before, the frame is what makes the second
call a different sample. Bounded to one extra call, only on silence, never on an off-topic session.
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


EMPTY = {"project_relevant": True, "patterns": [], "mistakes": [], "decisions": [],
         "session_summary": "short", "context_update": ""}
ONE = {**EMPTY, "decisions": [{"title": "timeout", "description": "the HTTP client timeout is 5 seconds",
                               "facts": ["5 seconds"]}]}
OFFTOPIC = {**EMPTY, "project_relevant": False}
BODY = "Came back to the API client. That earlier decision is off: the HTTP client timeout is 5 seconds."
CALLS: list[str] = []


def backend(*answers):
    seq = list(answers)

    def fake(prompt, project=None):
        CALLS.append(prompt)
        return seq.pop(0) if seq else {}
    return fake


def fresh():
    CALLS.clear()
    d = make_sandbox(m, "k5_", offline=True)
    m.update_embeddings = lambda notes: None
    m.EXTRACT_RETRY = 1
    return d


print("\n- the item count reads the three lists -")
check("empty is zero", m._item_count(EMPTY) == 0)
check("one decision is one", m._item_count(ONE) == 1)
check("garbage is zero", m._item_count(None) == 0 and m._item_count({"patterns": "x"}) == 0)

print("\n- silence on a relevant session is retried once, and the second answer wins -")
d = fresh()
m.generate_json = backend(EMPTY, ONE)
ok = m.process_session("k5a", r"D:\Coding\x", "", "ingest", {}, transcript_text=BODY, project_override="k5proj")
notes = list((d / "Decisions").glob("*k5proj-decision-*.md"))
check("two calls were made", len(CALLS) == 2, str(len(CALLS)))
check("the first call is the unchanged prompt", not CALLS[0].startswith(m._RETRY_FRAME))
check("the second call carries the second-pass frame before the same prompt",
      CALLS[1].startswith(m._RETRY_FRAME) and CALLS[1][len(m._RETRY_FRAME):] == CALLS[0])
check("the note from the second pass is on disk", ok and len(notes) == 1, str([n.name for n in notes]))
check("the retry is counted", m._LLM_STATS.get("retry", 0) >= 1 and m._LLM_STATS.get("retry_hit", 0) >= 1)

print("\n- a second silence stands: no third call, no note, the session is still processed -")
d = fresh()
m.generate_json = backend(EMPTY, EMPTY)
db = {}
ok = m.process_session("k5b", r"D:\Coding\x", "", "ingest", db, transcript_text=BODY, project_override="k5proj")
check("exactly two calls", len(CALLS) == 2, str(len(CALLS)))
check("no typed note", not list((d / "Decisions").glob("*.md")))
check("the session is marked processed (a session note is still written)", ok and "k5b" in db)

print("\n- what is not retried -")
d = fresh()
m.generate_json = backend(OFFTOPIC, ONE)
m.process_session("k5c", r"D:\Coding\x", "", "ingest", {}, transcript_text=BODY, project_override="k5proj")
check("an off-topic session: one call, empty beats wrong", len(CALLS) == 1, str(len(CALLS)))
check("and nothing written", not list((d / "Decisions").glob("*.md")))

d = fresh()
m.generate_json = backend(EMPTY, ONE)
m.process_session("k5d", r"D:\Coding\x", "", "ingest", {}, transcript_text="ok thanks", project_override="k5proj")
check("a body under the floor is not retried", len(CALLS) == 1, str(len(CALLS)))

d = fresh()
m.generate_json = backend(ONE, ONE)
m.process_session("k5e", r"D:\Coding\x", "", "ingest", {}, transcript_text=BODY, project_override="k5proj")
check("a first pass with an item is never retried", len(CALLS) == 1, str(len(CALLS)))

print("\n- mutation check: the switch off restores one call and the silence -")
d = fresh()
m.EXTRACT_RETRY = 0
m.generate_json = backend(EMPTY, ONE)
m.process_session("k5f", r"D:\Coding\x", "", "ingest", {}, transcript_text=BODY, project_override="k5proj")
check("with the retry off, one call", len(CALLS) == 1, str(len(CALLS)))
check("and the fact is lost - the K3 silence", not list((d / "Decisions").glob("*.md")))
m.EXTRACT_RETRY = 1

print("")
print("- 'nothing was produced' and 'everything produced was refused' are different zeros -")
#: The run log carried only what was WRITTEN, so both cases reported `P=0 M=0 D=0` and a caller
#: could not tell them apart. They lead to opposite conclusions: the first is a property of the
#: workload - this transcript held no lesson - and the second is a defect in the write path. A
#: stand measuring the cost of memory prints zero only when it can say which; a zero it cannot
#: explain sells a bug as a design. Asked for by the auditing session while writing
#: `research/token_floor.py`, whose first run printed "0 chars, 0 found" and looked like free.


def _run(sid, extraction, refuse_writes):
    """One session through the real write path, with the model and the writer stubbed."""
    fresh()
    m.generate_json = lambda *a, **k: dict(extraction)
    real_write = m.write_typed_note
    if refuse_writes:
        m.write_typed_note = lambda *a, **k: ""      # every proposal refused, as M-10 does
    try:
        log: list[dict] = []
        m.process_session(sid, CWD_FOR_ZEROS, "", "ingest", {}, run_log=log,
                          transcript_text=TRANSCRIPT_FOR_ZEROS, project_override="zerosproj")
        return log[-1] if log else {}
    finally:
        m.write_typed_note = real_write


CWD_FOR_ZEROS = "D:" + chr(92) + "Coding" + chr(92) + "x"
TRANSCRIPT_FOR_ZEROS = ("user: fix the loader\n"
                        "assistant: pinned the temperature, it was sampling\n") * 20
_TWO = {"patterns": [{"title": "pin the temperature", "description": "sampling was on"}],
        "mistakes": [{"title": "read the page", "description": "asked the doc not the source"}],
        "decisions": []}

_none = _run("zeros-a", {"patterns": [], "mistakes": [], "decisions": []}, False)
_ref = _run("zeros-b", _TWO, True)

_written = ("patterns", "mistakes", "decisions")
check("both cases write nothing, which is why the old log could not tell them apart",
      all(_none.get(k) == 0 for k in _written) and all(_ref.get(k) == 0 for k in _written),
      f"none {[_none.get(k) for k in _written]} refused {[_ref.get(k) for k in _written]}")
check("the run log says how many the extractor PROPOSED",
      sum((_none.get("proposed") or {}).values()) == 0
      and sum((_ref.get("proposed") or {}).values()) == 2,
      f"none {_none.get('proposed')} refused {_ref.get('proposed')}")
check("and how many the write path REFUSED",
      sum((_none.get("refused") or {}).values()) == 0
      and sum((_ref.get("refused") or {}).values()) == 2,
      f"none {_none.get('refused')} refused {_ref.get('refused')}")
check("so the two zeros are distinguishable by a number, not by reading the log",
      (_none.get("proposed") or {}) != (_ref.get("proposed") or {}))
print("")
print("- the relevance gate and quarantine are not 'proposed nothing' or 'refused' (review #2, #12) -")
#: The /code-review xhigh of 2026-09-23. `proposed` was counted through the gated item list, so a
#: session the relevance gate called off-topic reported that the extractor proposed nothing - the
#: exact confusion the field was added to remove (token_floor treats an all-zero `proposed` as an
#: honest regime). And `refused = proposed - written` counted a quarantined note and the crash-retry
#: skip of one as refusals. Each outcome now has its own count, and they add up to `proposed`.
_TWO_OFF = {**_TWO, "project_relevant": False}
_off = _run("gate-a", _TWO_OFF, False)
check("an off-topic session reports what the extractor proposed, not zero (#2)",
      sum((_off.get("proposed") or {}).values()) == 2, str(_off.get("proposed")))
check("and says the relevance gate dropped it, not the write path",
      sum((_off.get("off_topic") or {}).values()) == 2
      and sum((_off.get("refused") or {}).values()) == 0, f"{_off.get('off_topic')} {_off.get('refused')}")

_SURE = {"patterns": [], "mistakes": [],
         "decisions": [{"title": "quarantine me", "description": "the pool size is 64",
                        "confidence": 0.99}]}
m_q = m.QUARANTINE_MODE
m.QUARANTINE_MODE = True
try:
    fresh()
    m.generate_json = lambda *a, **k: dict(_SURE)
    qlog: list[dict] = []
    for _ in range(2):          # the second pass is the crash retry: the same session again
        m.process_session("quar-a", CWD_FOR_ZEROS, "", "ingest", {}, run_log=qlog,
                          transcript_text=TRANSCRIPT_FOR_ZEROS, project_override="zerosproj")
finally:
    m.QUARANTINE_MODE = m_q
_q1, _q2 = (qlog + [{}, {}])[:2]
check("a quarantined note is counted as quarantined, not refused (#12)",
      sum((_q1.get("quarantined") or {}).values()) == 1
      and sum((_q1.get("refused") or {}).values()) == 0, f"{_q1.get('quarantined')} {_q1.get('refused')}")
check("the crash retry's idempotent skip is counted as skipped, not refused",
      sum((_q2.get("skipped") or {}).values()) == 1
      and sum((_q2.get("refused") or {}).values()) == 0, f"{_q2.get('skipped')} {_q2.get('refused')}")
_keys = ("patterns", "mistakes", "decisions")
for _name, _r in (("off-topic", _off), ("quarantine", _q1), ("retry", _q2), ("refused", _ref)):
    _parts = sum(sum((_r.get(k) or {}).values()) for k in ("refused", "quarantined", "skipped", "off_topic"))
    check(f"every outcome adds up to what was proposed ({_name})",
          sum((_r.get("proposed") or {}).values()) == sum(_r.get(k, 0) for k in _keys) + _parts, str(_r))

print("")
print("- a model that OBEYED the off-topic instruction still leaves a mark (auditor, e0e6924) -")
#: The prompt tells the model to return empty lists when project_relevant is false. A model that
#: obeys gives proposed 0 and off_topic 0 - the same numbers as an on-topic session with nothing
#: durable in it. So the SESSION carries the gate's verdict, through the call token_floor makes.
import api  # noqa: E402
m.llm_available = lambda *a, **k: True
_verdicts = {}
for _name, _ex in (("on-topic, nothing durable", {"patterns": [], "mistakes": [], "decisions": []}),
                   ("off-topic, model obeyed", {"project_relevant": False, "patterns": [],
                                                "mistakes": [], "decisions": []})):
    fresh()
    m.generate_json = lambda *a, _e=_ex, **k: dict(_e)
    _verdicts[_name] = api.capture_session(TRANSCRIPT_FOR_ZEROS, project="zerosproj",
                                           session_id=f"gate-{len(_verdicts)}")
_on, _off2 = _verdicts["on-topic, nothing durable"], _verdicts["off-topic, model obeyed"]
check("the counts alone cannot tell these two apart - the case the auditor probed",
      all(_on.get(k) == _off2.get(k) for k in ("proposed", "refused", "off_topic")), f"{_on} {_off2}")
check("capture_session says which session the relevance gate called off-topic",
      _on.get("relevant") is True and _off2.get("relevant") is False,
      f"{_on.get('relevant')!r} {_off2.get('relevant')!r}")

print("")
print("- every stand's stand-in for the writer takes the writer's keywords (third review, 5961f38) -")
#: process_session passes `why=` since R14. research/k8_collisions.py wrapped write_typed_note with a
#: fixed signature, so every capture through it raised TypeError, which the stand's `except
#: Exception` turned into an error row per case: a stand measuring nothing and saying so nowhere.
#: Swept by signature, not by the one stand the review named.
import ast  # noqa: E402
import inspect  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_writer_kw = [p.name for p in inspect.signature(m.write_typed_note).parameters.values()
              if p.default is not inspect.Parameter.empty]
_wrappers = []
for _py in sorted(list((_ROOT / "research").rglob("*.py")) + list((_ROOT / "tools").glob("*.py"))):
    try:
        _tree = ast.parse(_py.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        continue
    _defs = {n.name: n for n in ast.walk(_tree) if isinstance(n, ast.FunctionDef)}
    for _n in ast.walk(_tree):
        if (isinstance(_n, ast.Assign) and isinstance(_n.value, ast.Name)
                and any(isinstance(t, ast.Attribute) and t.attr == "write_typed_note" for t in _n.targets)
                and _n.value.id in _defs):
            _fn = _defs[_n.value.id]
            _names = {a.arg for a in _fn.args.args + _fn.args.kwonlyargs}
            _missing = [] if _fn.args.kwarg else [k for k in _writer_kw if k not in _names]
            _wrappers.append((f"{_py.relative_to(_ROOT)}:{_fn.name}", _missing))
check("the sweep finds the stand-ins (k8_collisions' recorder among them)",
      any("k8_collisions" in w for w, _ in _wrappers), str(_wrappers))
check("each accepts every keyword the writer takes - " + ", ".join(_writer_kw),
      all(not miss for _, miss in _wrappers), str([w for w in _wrappers if w[1]]))

print(f"\nextraction retry: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
