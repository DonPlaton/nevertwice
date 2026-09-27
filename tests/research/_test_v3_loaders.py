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
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

print(f"\nv3 loaders: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
