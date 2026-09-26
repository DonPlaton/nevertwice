#!/usr/bin/env python3
"""PREREG-V3 plan step A3.j: research/v3/dataset_facts.py - the rules D-J1..D-J8 on hand-made rows, offline.

* D-J1: SWE is the domain NAMED "SOFTWARE"; a count outside 34|36 or a question total other than 432 is a named
  problem, never a reason to pick another domain; a second software-like value is named;
* D-J2: BEAM's probing questions parse with ast.literal_eval only; the counts; each ability placed on §8.4 or long-form;
* D-J3: dates per SESSION - present, absent, or a named problem when mixed;
* D-J4..D-J6: FC sources and counts; LoCoMo's per-category multiset; gold evidence per stand, BEAM's source_chat_ids
  flattened from a list or a dict of lists and checked against the chat's message ids;
* the child's output: every string a label, or the run is refused (prints_answers); a file that fails verify() is
  never read (reads_unverified_file); the big LME files are read element by element.

    python tests/_test_v3_dataset_facts.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


D = _load("v3_dataset_facts", ROOT / "research" / "v3" / "dataset_facts.py")
SR = _load("v3_smoke_rules_df", ROOT / "research" / "v3" / "smoke_rules.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="nvt3_facts_"))


def ama_row(domain: str, n_qa: int, tlen: int = 3, ep: int = 0) -> dict:
    return {"domain": domain, "episode_id": ep, "trajectory": [{"turn_idx": i, "action": "a" * tlen} for i in range(2)],
            "qa_pairs": [{"question": "q?", "answer": "a"}] * n_qa}


print("\n- D-J1 AMA: SWE by name -")
rows = [ama_row("SOFTWARE", 12, ep=i) for i in range(36)] + [ama_row("WEB", 5, ep=100 + i) for i in range(3)]
f1, p1 = D.ama_facts(rows)
check("SOFTWARE by name: 36 trajectories, 432 questions, the census, no problem",
      p1 == [] and f1["swe_trajectories"] == 36 and f1["swe_questions"] == 432 and f1["domain_census"] == {"SOFTWARE": 36, "WEB": 3}
      and f1["gold_evidence"]["verdict"] == "absent", str((p1, f1)))
_, p2 = D.ama_facts(rows[:35] + rows[36:])
check("35 SOFTWARE trajectories is a named problem - never a reason to pick another domain",
      any("35 SOFTWARE trajectories is not 34 or 36" in p for p in p2), str(p2))
_, p3 = D.ama_facts([ama_row("SOFTWARE", 11, ep=i) for i in range(36)])
check("a question total other than 432 is a named problem", any("396 SOFTWARE questions is not 432" in p for p in p3), str(p3))
_, p4 = D.ama_facts([ama_row("SWE", 12)])
check("a domain named 'SWE' is not SOFTWARE: absent SOFTWARE and a second software-like value, both named",
      any("no domain named 'SOFTWARE'" in p for p in p4) and any("second software-like" in p for p in p4), str(p4))
_, p5 = D.ama_facts(rows + [ama_row("SOFTWARE_ENG", 1)])
check("a second software-like value beside SOFTWARE is named", any("SOFTWARE_ENG" in p for p in p5), str(p5))
sm = D.s7_smoke(rows, SR.s7_pick)
check("D-J7: the S7 smoke is the non-SOFTWARE trajectory closest to the SOFTWARE median (smoke_rules), by identity",
      sm == {"episode_id": 100, "domain": "WEB", "index_in_non_software": 0}, str(sm))

print("\n- D-J2/D-J3/D-J6 BEAM -")
check("probing_questions parse as a Python literal", D.parse_probing("{'a': [{'x': 1}]}") == {"a": [{"x": 1}]})
for label, text in (("a call", "__import__('os').getcwd()"), ("a name", "evil"), ("not a mapping", "[1, 2]")):
    try:
        D.parse_probing(text)
        ok = False
    except D.FactsRefused as e:
        ok = "D-J2" in str(e)
    except Exception:  # noqa: BLE001 - anything but the named refusal fails the check by its name
        ok = False
    check(f"probing_questions that are {label} are refused by name - never evaluated", ok)


def conv(questions: dict, anchors=(True, True), first=True) -> dict:
    sessions = []
    for si, has in enumerate(anchors):
        msgs = [{"id": si * 10 + k, "role": "user", "content": "c", "time_anchor": ""} for k in range(3)]
        if has:
            msgs[0 if first else 1]["time_anchor"] = "2024-01-0%d" % (si + 1)
        sessions.append(msgs)
    return {"probing_questions": repr(questions), "chat": sessions}


Q = {"abstention": [{"question": "q", "source_chat_ids": []}] * 2,
     "contradiction_resolution": [{"question": "q", "source_chat_ids": {"first": [0], "second": [10]}}] * 2,
     "event_ordering": [{"question": "q", "source_chat_ids": [1, 2]}] * 16}
b_rows = [conv(Q) for _ in range(20)]
fb, pb = D.beam_facts(b_rows)
check("BEAM: 20 conversations, 400 questions, per ability, no problem",
      pb == [] and fb["conversations"] == 20 and fb["questions"] == 400
      and fb["per_ability"] == {"abstention": 40, "contradiction_resolution": 40, "event_ordering": 320}, str((pb, fb["per_ability"])))
check("D-J3: an anchor on each session (message 0) is present; anchors per session recorded",
      fb["dates"] == {"verdict": "present", "sessions": 40, "sessions_anchored": 40, "anchor_on_message_0": 40, "messages": 120})
check("D-J6: source_chat_ids from a list and from a dict of lists are flattened and found in the chat",
      fb["gold_evidence"]["verdict"] == "present" and fb["gold_evidence"]["ids"] == 20 * (0 + 2 * 2 + 16 * 2)
      and fb["gold_evidence"]["ids_found_in_chat"] == fb["gold_evidence"]["ids"]
      and fb["gold_evidence"]["coverage"]["abstention"] == "0/40" and len(fb["gold_evidence"]["without_ids"]) == 40,
      str(fb["gold_evidence"]["coverage"]))
check("each ability is placed on §8.4 by name", fb["abilities_84"] == {"abstention": "short",
                                                                        "contradiction_resolution": "short", "event_ordering": "ordering"})
fu, _ = D.beam_facts([conv({"new_skill": [{"source_chat_ids": [0]}]})])
check("an ability on no §8.4 list is long-form (C7), listed", fu["abilities_84"] == {"new_skill": "long (not on §8.4, C7)"})
fa, pa = D.beam_facts([conv(Q, anchors=(False, False))] * 20)
check("D-J3: no session anchored is absent, with no problem", fa["dates"]["verdict"] == "absent" and not any("D-J3" in p for p in pa))
fm, pm = D.beam_facts([conv(Q, anchors=(True, False))] * 20)
check("D-J3: some sessions anchored and some not is a named problem", fm["dates"]["verdict"] == "mixed"
      and any(p.startswith("D-J3: 20 of 40 sessions") for p in pm), str(pm))
fn, _ = D.beam_facts([conv(Q, first=False)] * 20)
check("D-J3: an anchor that is not on message 0 still counts, and the record says so",
      fn["dates"]["verdict"] == "present" and fn["dates"]["anchor_on_message_0"] == 0)
_, pw = D.beam_facts([conv({"event_ordering": [{"source_chat_ids": [999]}]})])
check("D-J6: an id that points at no chat message is named", any("point at no chat message" in p for p in pw), str(pw))
_, pc = D.beam_facts(b_rows[:19])
check("D-J2: a count other than 20 conversations / 400 questions is named",
      any("19 conversations is not 20" in p for p in pc) and any("380 questions is not 400" in p for p in pc), str(pc))

print("\n- D-J4 FC, D-J5 LoCoMo, D-J8 LME -")
fc_rows = [{"metadata": {"source": s, "haystack_sessions": None}, "questions": ["q"] * 100} for s in D.FC_SOURCES]
ff, pf = D.fc_facts(fc_rows)
check("FC: 100 per source, the 8 sources, haystack_sessions null -> absent",
      pf == [] and set(ff["questions_per_row"].values()) == {100} and ff["gold_evidence"]["verdict"] == "absent")
_, pf2 = D.fc_facts(fc_rows[:7])
check("FC: a missing source is named", any(p.startswith("D-J4") for p in pf2))
_, pf3 = D.fc_facts(fc_rows[:7] + [{"metadata": {"source": fc_rows[7]["metadata"]["source"], "haystack_sessions": [[]]},
                                    "questions": []}])
check("FC: haystack_sessions null in some rows only is named", any("some FC rows only" in p for p in pf3))
cats = [(1, 282), (2, 321), (3, 96), (4, 841), (5, 446)]          # by category number, not the PREREG's order
samples = [{"qa": [{"category": c, "evidence": ["D1:1"]} for c, n in cats for _ in range(n)]}]
samples[0]["qa"][5]["evidence"] = []
fl, pl = D.locomo_facts(samples)
check("LoCoMo: the per-category multiset is 841/282/321/96/446 whatever the category order; the missing evidence named",
      pl == [] and fl["questions"] == 1986 and fl["gold_evidence"]["with_evidence"] == 1985
      and fl["gold_evidence"]["without"] == ["s0:qa5"], str(pl))
_, pl2 = D.locomo_facts([{"qa": samples[0]["qa"][:-1]}])
check("LoCoMo: another multiset is a named problem (re-pin before the anchor)", any("re-pin" in p for p in pl2))
fe = D.lme_facts([{"question_type": "temporal", "answer_session_ids": ["s1"]}, {"question_type": "temporal", "answer_session_ids": []}])
check("LME: questions, by type, and how many carry answer_session_ids",
      fe == {"questions": 2, "by_question_type": {"temporal": 2}, "gold_evidence": {"field": "answer_session_ids",
                                                                                   "verdict": "present", "with_ids": 1}})

print("\n- the reader and the output guard -")
big = [{"question_type": "x", "answer_session_ids": [str(i)], "pad": "é" * (i % 7)} for i in range(500)]
jf = TMP / "big.json"
jf.write_text(" [\n" + ",\n".join(json.dumps(x, ensure_ascii=False) for x in big) + "\n] ", encoding="utf-8")
check("the JSON array is read element by element, across chunk boundaries", list(D.iter_json_array(jf, chunk=37)) == big)
(TMP / "trunc.json").write_text(json.dumps(big)[:-40], encoding="utf-8")
try:
    list(D.iter_json_array(TMP / "trunc.json", chunk=64))
    ok_tr = False
except D.FactsRefused as e:
    ok_tr = "truncated" in str(e)
check("a truncated JSON array is refused by name", ok_tr)
check("the guard passes labels, numbers and booleans",
      D.scan_labels({"S5": {"per_ability": {"event_ordering": 40}, "dates": {"verdict": "present"}, "ok": True,
                            "without_ids": ["c0:abstention:1"]}}) == [])
check("the guard names any string that is not a label - a question or an answer",
      D.scan_labels({"S7": {"smoke": {"domain": "WEB", "leak": "What did the user say about the cat?"}}}) == ["/S7/smoke/leak"])

print("\n- the harness: verify first, the child's output guarded -")
L = _load("v3_launch_df", ROOT / "research" / "v3" / "launch.py")


class Quiet:
    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return {}


class FakeCP:
    """Every file verified (or one refused), at a temp location."""
    PinMismatch = type("PinMismatch", (RuntimeError,), {})

    def __init__(self, bad: str | None = None):
        self.bad = bad

    def location(self, name, **kw):
        return TMP / "data" / name

    def verify(self, name, path):
        if name == self.bad:
            raise self.PinMismatch(f"{name}: sha256 is not the pinned one")
        return {"pin": name, "sha256": "0" * 64}


def contract(tag):
    base = TMP / tag
    (base / "watched").mkdir(parents=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    exc = {sys.executable: "the test interpreter"}
    if getattr(sys, "_base_executable", sys.executable) != sys.executable:
        exc[sys._base_executable] = "the test interpreter's base"
    return L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=base / "repo",
                      owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                      conservation_root=base / "conservation", binary_exceptions=exc,
                      system_dirs=(Path(sys.executable).parent,)), base


def fake_child(tag: str, payload: dict) -> Path:
    s = TMP / f"child_{tag}.py"
    s.write_text("import json, sys\nprint(json.dumps(" + repr(payload) + "))\n", encoding="utf-8")
    return s


def run(tag, payload, cp=None):
    c, base = contract(tag)
    return D.run_facts(c, L, run="f1", python=Path(sys.executable), parent_env=os.environ, CP=cp or FakeCP(),
                       native=L.NativeEgressWitness(sampler=Quiet(), tick_s=60, jobs=None),
                       fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]), script=fake_child(tag, payload)), c


GOOD = {"facts": {"S7": {"swe_trajectories": 36}}, "problems": []}
rec, c1 = run("ok", GOOD)
check("a clean run: every file verified first, the child's labels accepted, the record written with the SWE sentence",
      rec["problems"] == [] and len(rec["files"]) == len(D.FILES) and rec["facts"] == GOOD["facts"]
      and "no pinned upstream source names the SWE domain" in rec["swe_identification"]
      and json.loads((c1.runs_root / "_facts" / "f1" / "facts.json").read_bytes())["facts"] == GOOD["facts"], str(rec["problems"]))
rec2, _ = run("leak", {"facts": {"S7": {"q": "What did the user say?"}}, "problems": []})
check("prints_answers: a child that prints a content string is refused, and no fact is recorded",
      any(p.startswith("prints_answers refused") for p in rec2["problems"]) and "facts" not in rec2, str(rec2["problems"]))
rec3, _ = run("unverified", GOOD, cp=FakeCP(bad="lme_m_cleaned"))
check("reads_unverified_file: a file that fails verify() stops the run before the child starts",
      any(p.startswith("reads_unverified_file refused: lme_m_cleaned") for p in rec3["problems"]) and "check" not in rec3,
      str(rec3["problems"]))
rec4, _ = run("problems", {"facts": {"S7": {"swe_trajectories": 35}}, "problems": ["D-J1: 35 SOFTWARE trajectories is not 34 or 36"]})
check("the child's named problems reach the record", rec4["problems"] == ["D-J1: 35 SOFTWARE trajectories is not 34 or 36"])
try:
    D.run_facts(c1, L, run="f1", python=Path(sys.executable), parent_env=os.environ, CP=FakeCP(),
                script=fake_child("again", GOOD))
    ok_re = False
except D.FactsRefused as e:
    ok_re = "used before" in str(e)
except Exception:  # noqa: BLE001
    ok_re = False
check("a used facts run label is refused", ok_re)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 dataset facts: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
