#!/usr/bin/env python3
"""PREREG-V3 TB4.9a (A6): research/v3/reader_judge.py - the stand reader and the judges' mechanics, on a fake transport
(rev1 §8.1, §8.3, §8.4; the auditor's Q-49).

* the reader's request: deepseek-flash, temperature 0, max_tokens 1,024, thinking disabled; the SHORT ANSWER line is
  the last one (LF lines only, case and stars ignored); missing - one second turn with the reader's own reply and our
  instruction; still missing - empty and a format failure; over 15 words - kept, counted overlong; long form - no line
  needed and no re-ask; usage summed over both calls;
* a judge's request: think false, temperature 0, num_predict = the cap, format when given; thinking output, a cut at
  the cap and an unparsable answer each get ONE identical re-ask, then invalid with the reason; the lenient reading
  beside;
* the parses: LME ^(yes|no)\\b vs "yes in"; LoCoMo json label vs its lenient reading; BEAM's parse_json_response
  branches and the rubric score; AMA exactly yes/no vs the last yes/no;
* the twin (SQuAD EM/F1), tau_b against a golden table taken from scipy's kendalltau variant b, BEAM's ordering score;
* CL5 and resolve_j2 over the ordered list.

    python tests/research/_test_v3_reader_judge.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

_spec = importlib.util.spec_from_file_location("v3_reader_judge", ROOT / "research" / "v3" / "reader_judge.py")
RJ = importlib.util.module_from_spec(_spec)
sys.modules["v3_reader_judge"] = RJ
_spec.loader.exec_module(RJ)
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


class Fake:
    """A transport that answers from a script and records every request."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, req):
        self.requests.append(req)
        return self.answers.pop(0)


def chat(content, reasoning=None, usage=None):
    msg = {"role": "assistant", "content": content}
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"message": msg}], "usage": usage or {"prompt_tokens": 100, "completion_tokens": 10}}


def ollama(content, thinking=None, done_reason="stop"):
    msg = {"role": "assistant", "content": content}
    if thinking is not None:
        msg["thinking"] = thinking
    return {"message": msg, "done_reason": done_reason}


print("- the SHORT ANSWER line -")
for text, want in (("reasoning\nSHORT ANSWER: Paris", "Paris"),
                   ("SHORT ANSWER: first\nmore\nshort answer: second", "second"),
                   ("**SHORT ANSWER:** Oslo, Norway", "Oslo, Norway"),
                   ("Short Answer : 42  ", "42"),
                   ("the short answer: is not a line start... SHORT ANSWER: x", None),
                   ("SHORT ANSWER:", None), ("no line at all", None),
                   ("line a SHORT ANSWER: y", None)):
    got = RJ.short_answer(text)
    check(f"short_answer({text[:30]!r}) = {want!r}", got == want, repr(got))

print("\n- the reader: its request, one re-ask, failures, overlong, long form -")
f = Fake([chat("Because...\nSHORT ANSWER: Paris")])
r = RJ.read("PROMPT", f)
req = f.requests[0]
check("the reader's request: deepseek-flash, temperature 0, max_tokens 1024, thinking disabled, the prompt as the "
      "only user turn", req == {"model": "deepseek-flash", "messages": [{"role": "user", "content": "PROMPT"}],
                                "temperature": 0, "max_tokens": 1024, "thinking": {"type": "disabled"}}, str(req))
check("a present line: no re-ask", r.short_answer == "Paris" and not r.reasked and not r.format_failure and len(f.requests) == 1)
f = Fake([chat("I think it is Paris.", usage={"prompt_tokens": 100, "completion_tokens": 7}),
          chat("SHORT ANSWER: Paris", usage={"prompt_tokens": 120, "completion_tokens": 4})])
r = RJ.read("PROMPT", f)
check("Q-49-5 a missing line: ONE second turn - the prompt, the reader's own reply, then our instruction",
      len(f.requests) == 2 and f.requests[1]["messages"] == [
          {"role": "user", "content": "PROMPT"}, {"role": "assistant", "content": "I think it is Paris."},
          {"role": "user", "content": "Finish your reply with one line: SHORT ANSWER: <at most 15 words>"}]
      and f.requests[1]["temperature"] == 0 and f.requests[1]["thinking"] == {"type": "disabled"}, str(f.requests[-1]))
check("... answered: the re-asked short answer, the usage of both calls summed",
      r.reasked and r.short_answer == "Paris" and not r.format_failure
      and r.usage == {"prompt_tokens": 220, "completion_tokens": 11}, str(r.usage))
f = Fake([chat("no line"), chat("still none")])
r = RJ.read("PROMPT", f)
check("still missing after the one re-ask: empty and a reader_format_failure, never a third call",
      r.format_failure and r.short_answer == "" and len(f.requests) == 2)
f = Fake([chat("SHORT ANSWER: " + " ".join(["w"] * 16))])
r = RJ.read("PROMPT", f)
check("over 15 words: kept and counted overlong", r.overlong and r.short_answer.count("w") == 16 and not r.format_failure)
f = Fake([chat("SHORT ANSWER: " + " ".join(["w"] * 15))])
check("exactly 15 words is not overlong", not RJ.read("PROMPT", f).overlong)
f = Fake([chat("A long summary with no line."), chat("SHORT ANSWER: should never be asked for")])
r = RJ.read("PROMPT", f, long_form=True)
check("long form (§8.4): no line needed, no re-ask", len(f.requests) == 1 and not r.reasked and not r.format_failure
      and r.text == "A long summary with no line.")
f = Fake([chat("SHORT ANSWER: x", reasoning="thought")])
check("reasoning content in the reader's answer is recorded (thinking must be off)", RJ.read("P", f).thinking_seen)
try:
    RJ.read("P", Fake([{"error": "x"}]))
    bad = "no error"
except RJ.ReaderJudgeError as e:
    bad = str(e)
check("an answer without choices refuses by name", "choices" in bad, bad)

print("\n- a judge: its request, the one identical re-ask, invalid with the reason -")
f = Fake([ollama("yes")])
v = RJ.judge("JP", 10, RJ.lme_strict, f, model="qwen3.6:27b", lenient=RJ.lme_official)
check("the judge's request: think false, temperature 0, num_predict the cap, no stream, the prompt only",
      f.requests[0] == {"model": "qwen3.6:27b", "messages": [{"role": "user", "content": "JP"}], "stream": False,
                        "think": False, "options": {"temperature": 0, "num_predict": 10}}, str(f.requests[0]))
check("a clean verdict: no re-ask, the lenient reading beside", v.value is True and not v.reasked and v.invalid is None
      and v.lenient is True)
f = Fake([ollama('{"label": "CORRECT"}')])
RJ.judge("JP", 64, RJ.locomo_strict, f, model="m", fmt="json")
check("Q-49-2 format json when given", f.requests[0].get("format") == "json")
for label, first, second, want_value, want_why in (
        ("thinking output", ollama("yes", thinking="hmm"), ollama("yes"), True, None),
        ("a cut at the cap", ollama("ye", done_reason="length"), ollama("no"), False, None),
        ("an unparsable answer", ollama("maybe"), ollama("yes"), True, None),
        ("unparsable twice", ollama("maybe"), ollama("perhaps"), None, "unparsable"),
        ("thinking twice", ollama("yes", thinking="a"), ollama("yes", thinking="b"), None, "thinking output"),
        ("cut twice", ollama("y", done_reason="length"), ollama("y", done_reason="length"), None, "cut at its cap")):
    f = Fake([first, second])
    v = RJ.judge("JP", 10, RJ.lme_strict, f, model="m")
    check(f"Q-49-6 {label}: one IDENTICAL re-ask, then {want_why or 'accepted'}",
          len(f.requests) == 2 and f.requests[0] == f.requests[1] and v.reasked and v.value == want_value
          and v.invalid == want_why, f"{v}")
v = RJ.judge("JP", 10, RJ.lme_strict, Fake([ollama("yes", thinking="x"), ollama("yes")]), model="m")
check("a re-asked verdict remembers that thinking was seen", v.thinking_seen and v.value is True)
try:
    RJ.judge_request("m", "p", 0)
    bad = "no error"
except RJ.ReaderJudgeError as e:
    bad = str(e)
check("a non-positive cap refuses", "cap" in bad, bad)

print("\n- the parses, strict and lenient -")
for text, strict, lenient in (("yes", True, True), ("No.", False, False), ("  YES, it is", True, True),
                              ("yesterday", None, True), ("The answer is yes", None, True), ("no yes", False, True)):
    check(f"LME {text!r}: strict {strict}, lenient {lenient}",
          RJ.lme_strict(text) == strict and RJ.lme_official(text) == lenient, f"{RJ.lme_strict(text)} {RJ.lme_official(text)}")
for text, strict, lenient in (('{"label": "CORRECT"}', True, True), ('{"label": "WRONG"}', False, False),
                              ('Reasoning. {"label": "CORRECT"}', None, True), ('```json\n{"label": "WRONG"}\n```', None, False),
                              ('{"label": "correct"}', None, False), ("CORRECT", None, None)):
    check(f"LoCoMo J {text[:28]!r}: strict {strict}, lenient {lenient}",
          RJ.locomo_strict(text) == strict and RJ.locomo_official(text) == lenient,
          f"{RJ.locomo_strict(text)} {RJ.locomo_official(text)}")
for text, want in (('{"score": 1}', 1.0), ('```json\n{"score": 0.5}\n```', 0.5), ('noise {"score": 0} more', 0.0),
                   ('{"score": 0.7}', None), ('{"score": true}', None), ("no json", None), ('[{"score": 1}]', None)):
    check(f"BEAM rubric score {text[:26]!r} = {want}", RJ.beam_score(text) == want, repr(RJ.beam_score(text)))
check("BEAM's parse: a fenced list, then the first non-greedy object",
      RJ.beam_json("```\n[1, 2]\n```") == [1, 2] and RJ.beam_json('x {"a": 1} y {"b": 2}') == {"a": 1})
for text, strict, official in (("yes", True, True), ("<think>hmm</think>no", False, False), ("Yes.", None, True),
                               ("no, but yes", None, True), ("maybe", None, None), ("NO", False, False)):
    check(f"AMA {text!r}: strict {strict}, official {official}",
          RJ.ama_strict(text) == strict and RJ.ama_official(text) == official, f"{RJ.ama_strict(text)} {RJ.ama_official(text)}")

print("\n- the twin, tau_b, the ordering score -")
for pred, gold, em, f1 in (("The Eiffel Tower!", "eiffel tower", 1.0, 1.0), ("Paris France", "Paris", 0.0, 2 / 3),
                           ("London", "Paris", 0.0, 0.0), ("", "", 1.0, 1.0), ("x", "", 0.0, 0.0),
                           ("a", "", 1.0, 1.0)):              # SQuAD drops the article: "a" normalises to ""
    got = RJ.twin(pred, gold)
    check(f"twin({pred!r}, {gold!r}) = ({em}, {f1:.3f})", got[0] == em and abs(got[1] - f1) < 1e-9, str(got))
# golden values computed by scipy.stats.kendalltau(x, y, variant="b").statistic - scipy 1.18.1 in the polygon's
# mem0_eval venv, run once on 2026-09-27 (scipy is not a dependency of this module or this suite)
for x, y, want in (([1, 2, 3, 4], [1, 2, 3, 4], 1.0), ([1, 2, 3, 4], [4, 3, 2, 1], -1.0),
                   ([1, 2, 3, 4, 5], [1, 3, 2, 5, 4], 0.6), ([1, 2, 2, 3], [1, 2, 3, 3], 0.7999999999999999),
                   ([1, 2, 3, 6, 6], [2, 1, 3, 6, 6], 0.7777777777777778),
                   ([1, 1, 2, 2, 3], [1, 2, 1, 3, 3], 0.49999999999999994),
                   ([2, 1, 5, 5, 4], [1, 2, 5, 4, 4], 0.6666666666666666)):
    got = RJ.tau_b(x, y)
    check(f"tau_b({x}, {y}) = {want:.4f} (scipy's variant b)", got is not None and abs(got - want) < 1e-9, repr(got))
check("tau_b is None when one side is constant", RJ.tau_b([1, 1, 1], [1, 2, 3]) is None)
s = RJ.event_ordering_score(["a", "b", "c"], ["a", "b", "c"])
check("BEAM ordering: identical lists score 1", s["final_score"] == 1.0 and s["f1"] == 1.0)
s = RJ.event_ordering_score(["a", "b", "c"], ["c", "b", "a"])
check("BEAM ordering: reversed lists - f1 1, tau_norm 0, score 0", s["f1"] == 1.0 and s["tau_norm"] == 0.0 and s["final_score"] == 0.0)
# golden values: BEAM's event_ordering_score body (compute_metrics.py @b2da22e) run with scipy 1.18.1 in the polygon's
# mem0_eval venv on 2026-09-27, over lists already canonicalised
for ref, sysl, want in ((["a", "b", "c", "d"], ["a", "b", "x"],
                         {"f1": 0.5714285714285715, "tau_norm": 0.7635231383473648, "final_score": 0.43629893619849425}),
                        (["a", "b", "c", "d", "e"], ["b", "a", "e", "q"],
                         {"f1": 0.6666666666666665, "tau_norm": 0.6380131118684709, "final_score": 0.4253420745789805})):
    s = RJ.event_ordering_score(ref, sysl)
    check(f"BEAM ordering {ref} vs {sysl}: missing and extra items lower f1, a missing item ranks last (tie_rank) - "
          "equal to the official score", all(abs(s[k] - v) < 1e-9 for k, v in want.items()), str(s))

print("\n- CL5 and J2 -")
ok, why = RJ.cl5(0.01, True, 0.10, 200)
check("CL5 passes at its bounds (1 % invalid, 10 % flips, 200 re-judged)", ok and why == [])
for label, args in (("1.1 % invalid", (0.011, True, 0.05, 200)), ("thinking on an accepted verdict", (0.0, False, 0.0, 200)),
                    ("an 11 % flip rate", (0.0, True, 0.11, 200)), ("199 re-judged", (0.0, True, 0.0, 199))):
    check(f"CL5 fails on {label}", not RJ.cl5(*args)[0])
good = {"invalid_rate": 0.0, "thinking_off_all": True, "flip_rate": 0.02, "retest_n": 200}
bad = dict(good, invalid_rate=0.05)
model, why = RJ.resolve_j2({"glm-4.7-flash:q4_K_M": bad, "gemma3n:e4b": good, "hermes3-8b": good})
check("J2 is the FIRST model of the ordered list that passes", model == "gemma3n:e4b"
      and why == {"glm-4.7-flash:q4_K_M": ["invalid 5.000% > 1 %"]}, f"{model} {why}")
model, why = RJ.resolve_j2({"hermes3-8b": good})
check("a model with no result is failed by name, not skipped", model == "hermes3-8b"
      and why.get("glm-4.7-flash:q4_K_M") == ["no contract result"] and "gemma3n:e4b" in why, str(why))
model, why = RJ.resolve_j2({m: bad for m, _ in RJ.J2_ORDER})
check("none passes: judge-dependent (no J2)", model == "judge-dependent (no J2)" and len(why) == 4)
check("J1 and the J2 order are rev1 §8.3's", RJ.J1 == ("qwen3.6:27b", "a50eda8ed977")
      and [m for m, _ in RJ.J2_ORDER] == ["glm-4.7-flash:q4_K_M", "gemma3n:e4b", "hermes3-8b", "llama3"]
      and RJ.CAPS == {"lme": 10, "locomo_j": 64, "ama": 2})

print(f"\nv3 reader judge: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
