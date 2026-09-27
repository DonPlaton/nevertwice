#!/usr/bin/env python3
"""PREREG-V3 TB4.3a (A6): research/v3/loaders.py and research/v3/tokens.py on synthetic fixtures (real field names,
invented content; no dataset is read here).

* LME: the first `prefix` questions of the nested order, in order; a scored prefix never reaches the smoke split
  (481-500), which is exactly smoke_rules.s1_tail; empty turns skipped and counted; chars and the unit sha; no gold on
  the run path (the gold door is separate and refuses a question it cannot answer);
* LoCoMo: sessions in numeric order with their dates, speakers kept, a shared image carried as its caption, category 5
  as abstention with adversarial_answer in the gold door, evidence namespaced by sample;
* S4 smoke (Q18 O-a): one LoCoMo-format conversation per S1 smoke question - user -> speaker_a, assistant ->
  speaker_b, dates converted mechanically, the LME question itself;
* read_pinned: nothing unverified is read;
* Truncator (§5.1, A6 Q29 O-a): the cap is the whole sequence, specials included - 2,046 content tokens fit, 2,047 are
  cut to 2,046; the kept text is an exact prefix; a prefix that tokenizes longer is shrunk, never grown; counts per item.

    python tests/research/_test_v3_loaders.py
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


LD = _load("v3_loaders", ROOT / "research" / "v3" / "loaders.py")
TK = _load("v3_tokens", ROOT / "research" / "v3" / "tokens.py")
SR = _load("v3_smoke_rules_for_loaders", ROOT / "research" / "v3" / "smoke_rules.py")
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
    except LD.LoadRefused as e:
        return words in str(e)
    except Exception:  # noqa: BLE001
        return False
    return False


def lme_rec(i: int) -> dict:
    qid = f"q{i:03d}" + ("_abs" if i % 50 == 7 else "")
    return {"question_id": qid, "question_type": "multi-session", "question": f"what about {i}?",
            "answer": f"answer {i}", "question_date": "2023/05/30 (Tue) 23:40",
            "haystack_session_ids": [f"s{i}a", f"s{i}b"], "haystack_dates": ["2023/05/20 (Sat) 02:21", "2023/05/21 (Sun) 14:05"],
            "haystack_sessions": [[{"role": "user", "content": f"hello {i}"}, {"role": "assistant", "content": "hi there"}],
                                  [{"role": "user", "content": "   "}, {"role": "assistant", "content": f"note {i}",
                                                                          "has_answer": True}]],
            "answer_session_ids": [f"s{i}b"]}


LME = [lme_rec(i) for i in range(500)]
ORDER = [r["question_id"] for r in reversed(LME)]          # the nested order is not the file order

print("\n- LongMemEval -")
units = LD.lme_units(LME, ORDER, stand="S1", prefix=10)
check("S1: the first `prefix` questions of the nested order, in that order", [u.unit_id for u in units] == ORDER[:10])
u0 = units[0]
check("a unit is a haystack of its sessions with their dates; the question carries its date and type",
      u0.kind == "haystack" and [s.date for s in u0.sessions] == ["2023/05/20 (Sat) 02:21", "2023/05/21 (Sun) 14:05"]
      and u0.questions[0].question_date == "2023/05/30 (Tue) 23:40" and u0.questions[0].category == "multi-session")
check("an empty turn is skipped and counted (a corpus property, not an error)",
      u0.empty_skipped == 1 and sum(len(s.items) for s in u0.sessions) == 3)
check("chars = the characters of the unit's items (K76's denominator, Q14)",
      u0.chars == sum(len(i.text) for s in u0.sessions for i in s.items))
flat = json.dumps([dataclasses.asdict(u) for u in units])
check("no gold on the run path: no answer, no answer_session_ids, no has_answer in any unit (R6)",
      "answer" not in flat.replace("has_answer", "") and "answer_session_ids" not in flat and "has_answer" not in flat)
check("the abstention flag comes from the _abs question id", [u.questions[0].abstention for u in LD.lme_units(
    LME, [f"q{7:03d}_abs", "q000"], stand="S1", prefix=2)] == [True, False])
u_again = LD.lme_units(LME, ORDER, stand="S1", prefix=1)[0]
changed = LD.lme_units([{**LME[-1], "answer": "another"}], ORDER[:1], stand="S1", prefix=1)[0]
edited = LD.lme_units([{**LME[-1], "question": "other?"}], ORDER[:1], stand="S1", prefix=1)[0]
check("the unit sha is deterministic, blind to the gold answer, and moves with the content",
      u_again.input_sha256 == units[0].input_sha256 == changed.input_sha256 != edited.input_sha256)
check("a scored prefix that reaches the smoke split is refused", refused(
    lambda: LD.lme_units(LME, ORDER, stand="S1", prefix=481), "smoke split"))
check("an order that repeats an id is refused", refused(lambda: LD.lme_units(LME, ORDER[:5] + ORDER[:1], stand="S1",
                                                                            prefix=6), "repeats"))
check("an ordered question missing from the file is refused",
      refused(lambda: LD.lme_units(LME[:-1], ORDER, stand="S1", prefix=480), "not in the file"))
smoke = LD.lme_smoke_units(LME, ORDER)
check("S1 smoke = positions 481-500 of the nested order = smoke_rules.s1_tail",
      [u.unit_id for u in smoke] == SR.s1_tail(ORDER) == ORDER[480:500])
check("the smoke split is disjoint from the largest scored prefix (480)",
      not {u.unit_id for u in smoke} & {u.unit_id for u in LD.lme_units(LME, ORDER, stand="S1", prefix=480)})
g = LD.lme_gold(LME, ["q001", "q002"])
check("the gold door: answers and evidence sessions for exactly the asked questions",
      set(g) == {"q001", "q002"} and g["q001"] == LD.Gold("q001", "answer 1", ("s1b",)))
check("the gold door refuses a question it has no answer for", refused(lambda: LD.lme_gold(LME, ["nope"]), "no gold"))

print("\n- LoCoMo -")
LOCOMO = [{"sample_id": f"conv-{k}", "conversation": {
    "speaker_a": "Ann", "speaker_b": "Bob",
    "session_2_date_time": "1:56 pm on 8 May, 2023", "session_10_date_time": "9:00 am on 9 June, 2023",
    "session_2": [{"speaker": "Ann", "dia_id": "D2:1", "text": "I moved"}, {"speaker": "Bob", "dia_id": "D2:2", "text": "",
                                                                          "blip_caption": "a photo of a dog"}],
    "session_10": [{"speaker": "Ann", "dia_id": "D10:1", "text": "hi"}, {"speaker": "Bob", "dia_id": "D10:2", "text": " "}]},
    "qa": [{"question": "where?", "answer": "Paris", "evidence": ["D2:1"], "category": 1},
           {"question": "cat?", "adversarial_answer": "not mentioned", "evidence": [], "category": 5}]}
    for k in range(3)]
lu = LD.locomo_units(LOCOMO, ["conv-2", "conv-0", "conv-1"], prefix=2)
c = lu[0]
check("S4: the first `prefix` conversations of the committed order", [u.unit_id for u in lu] == ["conv-2", "conv-0"])
check("sessions in numeric order (session_2 before session_10) with their dates",
      [s.session_id for s in c.sessions] == ["conv-2:session_2", "conv-2:session_10"]
      and c.sessions[0].date == "1:56 pm on 8 May, 2023")
check("speakers are kept on the item; a shared image is carried as its caption",
      c.sessions[0].items[0].speaker == "Ann" and len(c.sessions[0].items) == 2
      and c.sessions[0].items[1].text == "[image: a photo of a dog]", str([i.text for i in c.sessions[0].items]))
check("an empty turn is skipped and counted", c.empty_skipped == 1)
check("questions: ids by sample, category as text, category 5 is abstention",
      [(q.qid, q.category, q.abstention) for q in c.questions] == [("conv-2:q0", "1", False), ("conv-2:q1", "5", True)])
check("B-CAT: locomo_category reads 2 and \"2\" (and \" 5 \") as the int, None as None; a word or a bool is refused",
      (LD.locomo_category(2), LD.locomo_category("2"), LD.locomo_category(" 5 "), LD.locomo_category(None))
      == (2, 2, 5, None) and all(refused(lambda v=v: LD.locomo_category(v), "category") for v in ("two", True, 2.0, "")),
      "")
LOCOMO_S = [{**s, "qa": [{**q, "category": str(q["category"])} for q in s["qa"]]} for s in LOCOMO]
cs = LD.locomo_units(LOCOMO_S, ["conv-2", "conv-0", "conv-1"], prefix=2)[0]
gs = LD.locomo_gold(LOCOMO_S, ["conv-2:q0", "conv-2:q1"])
check("B-CAT: a file that writes its categories as text gives the same abstention flags and the same gold",
      [(q.category, q.abstention) for q in cs.questions] == [("1", False), ("5", True)]
      and gs["conv-2:q1"].answer == "not mentioned" and gs["conv-2:q0"].answer == "Paris",
      str([(q.category, q.abstention) for q in cs.questions]))
lg = LD.locomo_gold(LOCOMO, ["conv-2:q0", "conv-2:q1"])
check("the gold door: evidence namespaced by sample; category 5 answers with adversarial_answer",
      lg["conv-2:q0"].evidence == ("conv-2:D2:1",) and lg["conv-2:q1"].answer == "not mentioned")
check("no gold on the LoCoMo run path", "Paris" not in json.dumps([dataclasses.asdict(u) for u in lu])
      and "not mentioned" not in json.dumps([dataclasses.asdict(u) for u in lu]))

print("\n- S4 smoke (Q18 O-a) -")
for src, want in (("2023/05/20 (Sat) 02:21", "2:21 am on 20 May, 2023"), ("2023/05/21 (Sun) 14:05", "2:05 pm on 21 May, 2023"),
                  ("2023/01/02 (Mon) 00:07", "12:07 am on 2 January, 2023"), ("2023/01/02 (Mon) 12:30", "12:30 pm on 2 January, 2023")):
    check(f"LME date {src!r} -> LoCoMo {want!r}", LD.lme_date_to_locomo(src) == want, LD.lme_date_to_locomo(src))
check("an LME date in another form is refused", refused(lambda: LD.lme_date_to_locomo("2023-05-20"), "YYYY/MM/DD"))
ss = LD.s4_smoke_samples(LME, ORDER)
first = ss[0]
check("one LoCoMo-format sample per S1 smoke question, in the smoke order",
      [s["sample_id"] for s in ss] == [f"smoke-{q}" for q in ORDER[480:500]])
check("user -> speaker_a, assistant -> speaker_b, dia_ids D<session>:<turn>",
      first["conversation"]["speaker_a"] == "user" and first["conversation"]["speaker_b"] == "assistant"
      and [(t["speaker"], t["dia_id"]) for t in first["conversation"]["session_1"]] == [("user", "D1:1"), ("assistant", "D1:2")])
check("the haystack dates converted to LoCoMo's form, the LME question itself",
      first["conversation"]["session_2_date_time"] == "2:05 pm on 21 May, 2023"
      and first["qa"] == [{"question": LME[19]["question"], "category": None}], str(first["qa"]))
su = LD.locomo_units(ss, [s["sample_id"] for s in ss], prefix=20, stand="S4-smoke")
check("the smoke samples load through the same LoCoMo path", len(su) == 20 and su[0].kind == "conversation")
check("a turn role that is neither user nor assistant is refused",
      refused(lambda: LD.s4_smoke_samples([{**r, "haystack_sessions": [[{"role": "system", "content": "x"}], []]}
                                           for r in LME], ORDER), "neither user nor assistant"))

print("\n- read_pinned -")


class FakeCP:
    def __init__(self, ok: bool, path: Path):
        self.ok, self.path, self.verified = ok, path, []

    def location(self, name, *, hf_hub, pins_root):
        return self.path

    def verify(self, name, path):
        self.verified.append(name)
        if not self.ok:
            raise RuntimeError("size 1 is not the pinned 2")


with tempfile.TemporaryDirectory(prefix="v3load_") as td:
    f = Path(td) / "locomo10.json"
    f.write_text(json.dumps(LOCOMO), encoding="utf-8")
    cp = FakeCP(True, f)
    check("a verified pin is read and parsed", LD.read_pinned("locomo", hf_hub=Path(td), pins_root=Path(td), cp=cp) == LOCOMO
          and cp.verified == ["locomo"])
    jl = Path(td) / "ama.jsonl"
    jl.write_bytes((json.dumps({"n": 1, "t": "a" + chr(0x2028) + "b"}, ensure_ascii=False) + "\n"
                    + json.dumps({"n": 2}) + "\n").encode("utf-8"))
    try:
        jrows = [r["n"] for r in LD.read_pinned("ama", hf_hub=Path(td), pins_root=Path(td), cp=FakeCP(True, jl), fmt="jsonl")]
    except Exception as e:  # noqa: BLE001
        jrows = repr(e)
    check("B2: read_pinned(fmt=jsonl) verifies, then reads one record per LF line (U+2028 inside a string)",
          jrows == [1, 2], str(jrows)[:100])
    import types as _types  # noqa: E402
    saved_pa = {k: sys.modules.get(k) for k in ("pyarrow", "pyarrow.parquet")}
    fake_pq = _types.SimpleNamespace(read_table=lambda path: _types.SimpleNamespace(to_pylist=lambda: [{"path": path}]))
    sys.modules["pyarrow"] = _types.SimpleNamespace(parquet=fake_pq)
    sys.modules["pyarrow.parquet"] = fake_pq
    try:
        pq_rows = LD.read_pinned("beam", hf_hub=Path(td), pins_root=Path(td), cp=FakeCP(True, jl), fmt="parquet")
        pq_refused = refused(lambda: LD.read_pinned("beam", hf_hub=Path(td), pins_root=Path(td), cp=FakeCP(False, jl),
                                                    fmt="parquet"), "not the pinned")
    finally:
        for k, v in saved_pa.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    check("B2: read_pinned(fmt=parquet) verifies, then reads through pyarrow; unverified is refused before pyarrow",
          pq_rows == [{"path": str(jl)}] and pq_refused)
    check("B2: a format other than json, jsonl or parquet is refused",
          refused(lambda: LD.read_pinned("x", hf_hub=Path(td), pins_root=Path(td), cp=FakeCP(True, jl), fmt="csv"),
                  "json, jsonl or parquet"))
    check("an unverified pin is refused and not read", refused(
        lambda: LD.read_pinned("locomo", hf_hub=Path(td), pins_root=Path(td), cp=FakeCP(False, f)), "not the pinned"))

print("\n- Truncator (§5.1, A6 Q29 O-a) -")


def ws_spans(text: str):
    out, i = [], 0
    for w in text.split(" "):
        if w:
            out.append((i, i + len(w)))
        i += len(w) + 1
    return out


t = TK.Truncator(ws_spans)
short = t.cut("a b c")
check("a short text is untouched: 3 content tokens + 2 specials", short == TK.Cut("a b c", 5, 5, False))
exact = " ".join(f"w{i}" for i in range(2046))
c1 = t.cut(exact)
check("2,046 content tokens + 2 specials = 2,048 fit the cap: not truncated", c1.truncated is False and c1.kept_tokens == 2048)
over = " ".join(f"w{i}" for i in range(2047))
try:
    c2, err2 = t.cut(over), None
except Exception as e:  # noqa: BLE001 - a crashing cut is a named FAIL
    c2, err2 = TK.Cut("", 0, 0, False), repr(e)
check("2,047 content tokens are cut to 2,046 (+2 = 2,048), specials included in the cap",
      c2.truncated and c2.original_tokens == 2049 and c2.kept_tokens == 2048 and len(ws_spans(c2.text)) == 2046,
      str(err2 or (c2.original_tokens, c2.kept_tokens, c2.truncated)))
check("the kept text is an exact prefix of the original", bool(c2.text) and over.startswith(c2.text)
      and c2.text.endswith("w2045"))
check("the Truncator counts items and cuts", t.stats() == {"items": 3, "truncated": 1, "cap": 2048, "specials": 2})


def greedy_spans(text: str):
    """A tokenizer whose prefix can tokenize LONGER: a trailing 'ab' splits into two tokens only when it ends the text."""
    s = ws_spans(text)
    if text.endswith("ab") and s:
        a, b = s[-1]
        s = s[:-1] + [(a, a + 1), (a + 1, b)]
    return s


tg = TK.Truncator(greedy_spans, cap=6, specials=2)
cg = tg.cut("x y z ab q r")
check("a prefix that tokenizes longer at its edge is shrunk by whole tokens, never grown",
      cg.truncated and cg.text == "x y z" and cg.kept_tokens == 5, str(cg))
try:
    TK.Truncator(ws_spans, cap=2, specials=2)
    ok_refuse = False
except ValueError:
    ok_refuse = True
check("Truncator(cap <= specials) raises", ok_refuse)

print("\n- N1: the §5.2 cut for points.fill -")
cutw = TK.prefix_cut(ws_spans)
TXT = "alpha beta  gamma delta epsilon"
got_c = {n: cutw(TXT, n) for n in range(0, 7)}
check("prefix_cut: the longest exact prefix with at most n tokens, cut at a token's end; the whole text when it fits",
      got_c == {0: "", 1: "alpha", 2: "alpha beta", 3: "alpha beta  gamma", 4: "alpha beta  gamma delta",
                5: TXT, 6: TXT} and all(TXT.startswith(v) and len(ws_spans(v)) <= n for n, v in got_c.items()),
      str(got_c))
cutg = TK.prefix_cut(greedy_spans)
check("prefix_cut: a prefix that tokenizes longer at its edge is shrunk by whole tokens, never grown",
      cutg("x y z ab q r", 4) == "x y z" and len(greedy_spans(cutg("x y z ab q r", 4))) <= 4, cutg("x y z ab q r", 4))

print("\n- TB4.3b S5 BEAM (keys from j4) -")


def beam_row(cid, n_sess=3, with_empty=True):
    chat = []
    for j in range(n_sess):
        sess = [{"id": 10 * j + k, "index": k, "role": "user" if k % 2 == 0 else "assistant",
                 "content": f"c{cid} s{j} m{k}", "question_type": "x", "time_anchor": f"March-{j + 1}-2024" if k == 0 else ""}
                for k in range(3)]
        if with_empty and j == 0:
            sess.append({"id": 99, "index": 3, "role": "user", "content": "   ", "question_type": "x", "time_anchor": ""})
        chat.append(sess)
    probing = {"abstention": [{"question": f"Q-abs-{cid}", "ideal_response": "not said", "rubric": ["r1"],
                               "difficulty": "easy", "abstention_type": "a", "plan_reference": "p", "why_unanswerable": "w"}],
               "temporal_reasoning": [{"question": f"Q-tr-{cid}", "answer": "two weeks", "rubric": ["r2", "r3"],
                                       "source_chat_ids": [10, [11, 20]], "difficulty": "hard"}]}
    return {"conversation_id": cid, "chat": chat, "probing_questions": repr(probing), "conversation_plan": "x",
            "conversation_seed": 1, "narratives": "n", "user_profile": {}, "user_questions": []}


BEAM = [beam_row(i) for i in (3, 1, 2)]
bu = LD.beam_units(BEAM, [3, 2, 1], prefix=2)
b0 = next(u for u in bu if u.unit_id == "2")
check("S5: the first `prefix` conversations of the committed order (not sorted), by conversation_id",
      [u.unit_id for u in bu] == ["3", "2"] and b0.kind == "conversation")
check("S5: sessions in chat order, dated by their first message's time_anchor",
      [s.session_id for s in b0.sessions] == ["2:s0", "2:s1", "2:s2"] and [s.date for s in b0.sessions]
      == ["March-1-2024", "March-2-2024", "March-3-2024"])
check("S5: message items namespaced by conversation; an empty message skipped and counted",
      b0.sessions[0].items[0].item_id == "2:0" and b0.empty_skipped == 1 and sum(len(s.items) for s in b0.sessions) == 9)
check("S5: questions per (ability, position); abstention flagged; no gold on the run path",
      [(q.qid, q.category, q.abstention) for q in b0.questions]
      == [("2:abstention:0", "abstention", True), ("2:temporal_reasoning:0", "temporal_reasoning", False)]
      and "two weeks" not in json.dumps([dataclasses.asdict(u) for u in bu]) and "rubric" not in json.dumps(
          [dataclasses.asdict(u) for u in bu]))
try:
    bg = LD.beam_gold(BEAM, ["2:abstention:0", "2:temporal_reasoning:0"])
except Exception as e:  # noqa: BLE001 - a refusing gold door is a named FAIL of the row below
    print(f"       (beam_gold raised {e!r})")
    _none = LD.Gold("", "", ())
    bg = {"2:abstention:0": _none, "2:temporal_reasoning:0": _none}
check("S5 gold door: answer by the Q31 table (ideal_response for abstention), int source ids namespaced, the rubric first-class",
      bg["2:abstention:0"].answer == "not said" and bg["2:temporal_reasoning:0"].answer == "two weeks"
      and bg["2:temporal_reasoning:0"].evidence == ("2:10", "2:11", "2:20")
      and bg["2:temporal_reasoning:0"].rubric == ("r2", "r3") and bg["2:abstention:0"].rubric == ("r1",)
      and bg["2:abstention:0"].evidence == ())
check("S5: probing_questions that is not a literal is refused, never evaluated",
      refused(lambda: LD.beam_units([{**BEAM[0], "probing_questions": "__import__('os').getcwd()"}], [3], prefix=1),
              "never evaluated"))
check("S5: an ordered conversation missing from the file is refused",
      refused(lambda: LD.beam_units(BEAM, [9], prefix=1), "not in the file"))


def count_words(text):
    return len(text.split())


big = beam_row(7, n_sess=4, with_empty=False)
big["chat"][1] = [{"id": 10 + k, "index": k, "role": "user", "content": " ".join(["w"] * 60), "question_type": "x",
                   "time_anchor": "March-2-2024" if k == 0 else ""} for k in range(3)]
old_cut = LD._smoke_rules().s5_cut
seen_limit = {}


def s5_cut_small(texts, count, limit=128_000):
    seen_limit["limit"] = limit
    return old_cut(texts, count, limit=100)


LD._smoke_rules().s5_cut = s5_cut_small
try:
    su5, inside = LD.s5_smoke([big, beam_row(8)], count_words)
finally:
    LD._smoke_rules().s5_cut = old_cut
check("S5 smoke: the 500K split's FIRST conversation, cut after the last session inside the limit (s5_cut)",
      su5.unit_id == "7" and len(su5.sessions) == 1 and su5.stand == "S5-smoke" and seen_limit.get("limit") == 128_000,
      str((su5.unit_id, len(su5.sessions), seen_limit)))
check("S5 smoke: all its questions for the pilot; FA/FR only those whose source ids lie inside the kept messages",
      len(su5.questions) == 2 and inside == [], str(inside))
part = beam_row(7, n_sess=4, with_empty=False)
part["chat"][1] = big["chat"][1]
part["probing_questions"] = repr({
    "information_extraction": [{"question": "all inside", "answer": "a", "rubric": ["r"], "source_chat_ids": [0, 1]}],
    "knowledge_update": [{"question": "one in, one out", "answer": "b", "rubric": ["r"], "source_chat_ids": [1, 10]}]})
LD._smoke_rules().s5_cut = s5_cut_small
try:
    _, inside2 = LD.s5_smoke([part], count_words)
finally:
    LD._smoke_rules().s5_cut = old_cut
check("S5 smoke FA/FR: a question with one id inside the cut and one outside is excluded; all-inside is kept",
      inside2 == ["7:information_extraction:0"], str(inside2))

print("\n- Q31: the BEAM gold door by ability -")
ALL10 = {"abstention": "ideal_response", "contradiction_resolution": "ideal_answer",
         "instruction_following": "expected_compliance", "preference_following": "expected_compliance",
         "summarization": "ideal_summary", "event_ordering": "answer", "information_extraction": "answer",
         "knowledge_update": "answer", "multi_session_reasoning": "answer", "temporal_reasoning": "answer"}


def gold_row(ab_fields: dict, **q_over):
    probing = {ab: [{"question": f"q-{ab}", fld: f"REF-{ab}", "rubric": [f"rub-{ab}"], **q_over}] for ab, fld in ab_fields.items()}
    return {**beam_row(50), "probing_questions": repr(probing)}


try:
    g10 = LD.beam_gold([gold_row(ALL10)], [f"50:{ab}:0" for ab in ALL10])
except Exception as e:  # noqa: BLE001 - a refusing table is a named FAIL of the rows below, not a crash
    print(f"       (beam_gold over the ten abilities raised {e!r})")
    g10 = {f"50:{ab}:0": LD.Gold("", "", ()) for ab in ALL10}
check("Q31: every ability's display answer comes from its declared field, the rubric from 'rubric'",
      all(g10[f"50:{ab}:0"].answer == f"REF-{ab}" and g10[f"50:{ab}:0"].rubric == (f"rub-{ab}",) for ab in ALL10)
      and LD.BEAM_ANSWER_FIELD == ALL10)
#: j4's recorded key-set variants of BEAM 100K's probing questions (n, keys) - labels only, equal to the auditor's
#: blind key sets (13/13). Each variant holds exactly one answer field; 40 questions per ability.
J4_BEAM_VARIANTS = [
    (40, ["abstention_type", "difficulty", "ideal_response", "plan_reference", "question", "rubric", "why_unanswerable"]),
    (40, ["answer", "calculation_required", "conversation_references", "difficulty", "question", "rubric", "source_chat_ids",
          "temporal_type", "time_points"]),
    (18, ["answer", "conversation_reference", "difficulty", "extraction_challenge", "key_facts_tested", "question",
          "question_type", "rubric", "source_chat_ids"]),
    (22, ["answer", "conversation_reference", "difficulty", "key_facts_tested", "question", "question_type", "rubric",
          "source_chat_ids"]),
    (40, ["answer", "conversation_references", "difficulty", "ordering_tested", "ordering_type", "question", "rubric",
          "source_chat_ids", "total_mentions"]),
    (40, ["answer", "conversation_references", "difficulty", "potential_confusion", "question", "rubric", "source_chat_ids",
          "tests_retention_of", "update_type"]),
    (40, ["answer", "conversation_references", "difficulty", "question", "reasoning_steps", "reasoning_type", "rubric",
          "sessions_required", "source_chat_ids"]),
    (36, ["bullet_points_covered", "conversation_sessions", "difficulty", "ideal_summary", "key_elements_tested", "question",
          "rubric", "source_chat_ids", "summarization_type", "synthesis_required"]),
    (4, ["bullet_points_covered", "difficulty", "ideal_summary", "key_elements_tested", "question", "rubric",
         "source_chat_ids", "summarization_type", "synthesis_required"]),
    (40, ["compliance_indicators", "difficulty", "expected_compliance", "instruction_being_tested", "instruction_type",
          "non_compliance_signs", "question", "rubric", "source_chat_ids"]),
    (40, ["compliance_indicators", "difficulty", "expected_compliance", "non_compliance_signs", "preference_being_tested",
          "preference_type", "question", "rubric", "source_chat_ids"]),
    (40, ["contradiction_type", "conversation_references", "difficulty", "ideal_answer", "question", "rubric",
          "source_chat_ids", "tests_for", "topic_questioned"]),
]
FIELDS = set(LD.BEAM_ANSWER_FIELD.values())
check("Q31 vs j4: every recorded key set holds question, rubric and exactly one answer field of the table",
      all("question" in k and "rubric" in k and len(FIELDS & set(k)) == 1 for _, k in J4_BEAM_VARIANTS))
per_field = {f: sum(n for n, k in J4_BEAM_VARIANTS if f in k) for f in FIELDS}
want_field = {f: 40 * sum(1 for v in LD.BEAM_ANSWER_FIELD.values() if v == f) for f in FIELDS}
check("Q31 vs j4: each table field is carried by exactly 40 questions per ability mapped to it (400 in all)",
      per_field == want_field and sum(per_field.values()) == 400, str((per_field, want_field)))
check("Q31: detail carries neither the rubric nor the ability's own answer field",
      all(not ({"rubric", ALL10[ab]} & set(json.loads(g10[f"50:{ab}:0"].detail or "{}"))) for ab in ALL10))
check("Q31: an ability outside the declared table is refused",
      refused(lambda: LD.beam_gold([gold_row({"time_travel": "answer"})], ["50:time_travel:0"]), "outside the declared"))
check("Q31: a missing answer field is refused, never an empty answer",
      refused(lambda: LD.beam_gold([gold_row({"summarization": "answer"})], ["50:summarization:0"]), "is missing"))
for label, rub in (("missing", None), ("empty", []), ("not strings", [1, 2]), ("blank", ["  "])):
    check(f"Q31: a rubric that is {label} is refused",
          refused(lambda rub=rub: LD.beam_gold([gold_row({"event_ordering": "answer"}, rubric=rub)], ["50:event_ordering:0"]),
                  "rubric"))
check("Q31.3: a source_chat_ids leaf that is not an int is refused by name",
      refused(lambda: LD.beam_gold([gold_row({"temporal_reasoning": "answer"}, source_chat_ids=[1, "x"])],
                                   ["50:temporal_reasoning:0"]), "is not a message id"))
check("Q31.3: a bool leaf is refused too (bool is not a message id)",
      refused(lambda: LD.beam_gold([gold_row({"temporal_reasoning": "answer"}, source_chat_ids=[True])],
                                   ["50:temporal_reasoning:0"]), "is not a message id"))

print("\n- TB4.3b S7 AMA (keys from j4; Q16) -")


def ama_row(ep, domain="SOFTWARE", steps=2, none_at=None):
    traj = [{"turn_idx": i, "action": f"cmd{ep}-{i}", "observation": f"out{ep}-{i}"} for i in range(steps)]
    if none_at is not None:
        traj[none_at]["observation"] = None
    return {"episode_id": ep, "domain": domain, "task": f"task {ep}", "task_type": "t", "success": True,
            "total_tokens": 10, "num_turns": steps, "trajectory": traj,
            "qa_pairs": [{"question": f"q{ep}", "answer": f"ANSWER{ep}", "question_uuid": f"u{ep}", "type": "recall"}]}


AMA = [ama_row(1), ama_row(2, "TEXT2SQL", none_at=0), ama_row(3), ama_row(4, "WEB", steps=5)]
au = LD.ama_units(AMA, [3, 1], prefix=2)
a0 = au[0]
check("S7: the first `prefix` SOFTWARE trajectories of the order, by episode_id",
      [u.unit_id for u in au] == ["3", "1"] and a0.kind == "trajectory")
check("S7: items are the task, then each step's action and observation (the unit's characters, Q14)",
      [i.item_id for i in a0.sessions[0].items] == ["3:task", "3:0:action", "3:0:observation", "3:1:action", "3:1:observation"]
      and a0.chars == sum(len(i.text) for i in a0.sessions[0].items))
check("S7: questions from qa_pairs (question_uuid, question, type); answers never on the run path",
      [(q.qid, q.text, q.category) for q in a0.questions] == [("u3", "q3", "recall")]
      and "ANSWER3" not in json.dumps([dataclasses.asdict(u) for u in au]))
check("S7: the run record handed to an arm carries no qa_pairs", "qa_pairs" not in LD.ama_run_record(AMA[0])
      and set(LD.ama_run_record(AMA[0])) == {"episode_id", "task", "trajectory", "num_turns"})
check("S7 gold door: the answer by question_uuid", LD.ama_gold(AMA, ["u3"])["u3"].answer == "ANSWER3")
check("S7: an ordered episode outside SOFTWARE is refused by name",
      refused(lambda: LD.ama_units(AMA, [2], prefix=1), "outside the SOFTWARE domain"))
bad_order = ama_row(6, steps=3)
bad_order["trajectory"][1]["turn_idx"] = 2
bad_order["trajectory"][2]["turn_idx"] = 1
check("S7: a step whose turn_idx is not its position is refused",
      refused(lambda: LD._ama_unit("S7", bad_order), "turn_idx"))
nokey = {k: v for k, v in ama_row(12).items() if k != "num_turns"}
check("S7: a record without num_turns is refused by name (the run record keys are required)",
      refused(lambda: LD._ama_unit("S7", nokey), "lacks"))
dup = ama_row(7)
dup["qa_pairs"] = dup["qa_pairs"] * 2
check("S7: a repeated question_uuid in one trajectory is refused", refused(lambda: LD._ama_unit("S7", dup), "repeats"))
check("B3: the loader refuses what the renderer refuses - num_turns other than the steps",
      refused(lambda: LD._ama_unit("S7", {**ama_row(8), "num_turns": 9}), "num_turns"))
extra = ama_row(9)
extra["trajectory"][0]["reasoning"] = "think"
check("B3: ... a step with a key outside {action, observation, turn_idx}", refused(lambda: LD._ama_unit("S7", extra),
                                                                                   "not exactly"))
nontext = ama_row(11)
nontext["trajectory"][0]["action"] = 5
check("B3: ... a non-text action (never str() of it)", refused(lambda: LD._ama_unit("S7", nontext), "not text"))
check("S7: a None observation is refused by name, never dropped (the smoke path too)",
      refused(lambda: LD._ama_unit("S7", ama_row(5, none_at=1)), "observation is None"))
sm7 = LD.s7_smoke([ama_row(10, steps=3), ama_row(11, steps=3), ama_row(12, "WEB", steps=9), ama_row(13, "GAME", steps=3)])
check("S7 smoke: the non-SOFTWARE trajectory closest to the SOFTWARE median (s7_pick)", sm7.unit_id == "13"
      and sm7.stand == "S7-smoke", sm7.unit_id)
with tempfile.TemporaryDirectory(prefix="v3ama_") as td:
    jf = Path(td) / "a.jsonl"
    jf.write_bytes((json.dumps({**ama_row(20), "task": "x\u2028y\x85z"}, ensure_ascii=False) + "\n"
                    + json.dumps(ama_row(21)) + "\n").encode("utf-8"))
    try:
        rj = LD._read_jsonl(jf)
    except Exception as e:  # noqa: BLE001
        rj = [repr(e)]
    check("read_jsonl splits on LF only: U+2028 and U+0085 inside a task keep the record whole",
          [r.get("episode_id") if isinstance(r, dict) else r for r in rj] == [20, 21], str(rj)[:100])

print("\n- the pinned-tokenizer glue, on fake libraries (the real ones come with their install window) -")
import hashlib  # noqa: E402
import os  # noqa: E402
import types  # noqa: E402


class FakeEnc:
    def __init__(self, n_special: int):
        self.offsets = [(0, 0), (0, 5), (6, 11), (0, 0)][:3] + ([(0, 0)] if n_special == 2 else [])
        self.special_tokens_mask = [1, 0, 0] + ([1] if n_special == 2 else [])


class FakeTokenizer:
    n_special = 2

    @classmethod
    def from_file(cls, path):
        return cls()

    def encode(self, text, add_special_tokens=True):
        assert add_special_tokens is True
        return FakeEnc(self.n_special)


with tempfile.TemporaryDirectory(prefix="v3tok_") as td:
    tj = Path(td) / "tokenizer.json"
    tj.write_bytes(b"{}")
    good = hashlib.sha256(b"{}").hexdigest()
    saved = {k: sys.modules.get(k) for k in ("tokenizers", "tiktoken")}
    try:
        sys.modules["tokenizers"] = types.SimpleNamespace(Tokenizer=FakeTokenizer)
        try:
            TK.bge_m3_spans(tj, expected_sha256="0" * 64)
            off_pin = False
        except ValueError:
            off_pin = True
        check("bge_m3_spans refuses a tokenizer.json that is not the pinned file", off_pin)
        sp = TK.bge_m3_spans(tj, expected_sha256=good)
        check("bge_m3_spans returns the content tokens' spans only (special tokens dropped)",
              sp("hello world") == [(0, 5), (6, 11)], str(sp("hello world")))
        FakeTokenizer.n_special = 1
        try:
            sp("hello world")
            wrong_specials = False
        except ValueError:
            wrong_specials = True
        FakeTokenizer.n_special = 2
        check("a tokenizer that adds other than 2 special tokens is refused (A6 Q29 counts 2)", wrong_specials)

        bpe = Path(td) / "cl100k_base.tiktoken"
        bpe.write_bytes(b"ranks")
        seen = {}

        def get_encoding(name):
            cache = Path(os.environ["TIKTOKEN_CACHE_DIR"])
            f = cache / hashlib.sha1(TK.CL100K_URL.encode()).hexdigest()
            seen.update(name=name, cached=f.read_bytes() if f.exists() else None)
            return types.SimpleNamespace(encode=lambda text, disallowed_special=(): text.split())

        sys.modules["tiktoken"] = types.SimpleNamespace(get_encoding=get_encoding)
        os.environ["TIKTOKEN_CACHE_DIR"] = "sentinel"
        cnt = TK.cl100k_counter(bpe, expected_sha256=hashlib.sha256(b"ranks").hexdigest())
        check("cl100k_counter hands tiktoken the pinned file as its cache entry (sha1 of the URL) and counts with it",
              seen == {"name": "cl100k_base", "cached": b"ranks"} and cnt("a b c") == 3, str(seen))
        check("... and restores TIKTOKEN_CACHE_DIR afterwards", os.environ.get("TIKTOKEN_CACHE_DIR") == "sentinel")
        os.environ.pop("TIKTOKEN_CACHE_DIR", None)
        try:
            TK.cl100k_counter(bpe, expected_sha256="0" * 64)
            off_pin2 = False
        except ValueError:
            off_pin2 = True
        check("cl100k_counter refuses a .tiktoken file that is not the pinned one", off_pin2)
        import re as _re  # noqa: PLC0415

        def enc_chunks(text, disallowed_special=()):
            return _re.findall(r"\S+\s*|\s+", text)          # ids that decode back to the text exactly

        def dec_offsets(ids):
            offs, pos = [], 0
            for i in ids:
                offs.append(pos)
                pos += len(i)
            return "".join(ids), offs

        sys.modules["tiktoken"] = types.SimpleNamespace(get_encoding=lambda name: types.SimpleNamespace(
            encode=enc_chunks, decode_with_offsets=dec_offsets))
        cnt2, cut2 = TK.cl100k_pair(bpe, expected_sha256=hashlib.sha256(b"ranks").hexdigest())
        T2 = "one two  three four"
        check("N1: cl100k_pair counts and cuts with ONE encoding - a cut is an exact prefix of at most n tokens",
              cnt2(T2) == 4 and cut2(T2, 2) == "one two  " and T2.startswith(cut2(T2, 3)) and cnt2(cut2(T2, 3)) <= 3
              and cut2(T2, 9) == T2, f"{cnt2(T2)} {cut2(T2, 2)!r} {cut2(T2, 3)!r}")
        sys.modules["tiktoken"] = types.SimpleNamespace(get_encoding=lambda name: types.SimpleNamespace(
            encode=enc_chunks, decode_with_offsets=lambda ids: ("".join(ids).upper(), [0] * len(ids))))
        _, cut3 = TK.cl100k_pair(bpe, expected_sha256=hashlib.sha256(b"ranks").hexdigest())
        try:
            cut3("abc def", 1)
            no_rt = "accepted"
        except ValueError as e:
            no_rt = str(e)
        check("N1: a text the encoding does not round-trip is refused - no exact prefix could be cut",
              "round-trip" in no_rt, no_rt)
        try:
            TK.cl100k_pair(bpe, expected_sha256="0" * 64)
            off_pin3 = False
        except ValueError:
            off_pin3 = True
        check("N1: cl100k_pair refuses a .tiktoken file that is not the pinned one", off_pin3)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

print(f"\nv3 loaders: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
