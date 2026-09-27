#!/usr/bin/env python3
"""PREREG-V3 TB4.8b (A6): research/v3/templates.py - the mechanics every stand's reader template is built with (Q17,
Q-48-6), on an artificial pinned file (the stands' own sources and transforms follow the Q-48 rulings).

* extract: parsed, never run (a file whose import would fail is still read); exactly one string constant assigned to
  the named variable, at a dict-literal key path when given, carrying the marker and none of the exclusions; zero or
  several refuse by name; an f-string is never a candidate; adjacent literals are one;
* transform: each replacement exactly once, in order; none or twice refuses;
* build: the pin's sha256 checked first; the slots required to be exactly the declared ones; the template's sha256;
* render: slots filled in order, values verbatim (braces in a context block are not a format string); a wrong number
  or name of values refuses; "{{" in the template stays one literal brace.

    python tests/research/_test_v3_templates.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

_spec = importlib.util.spec_from_file_location("v3_templates", ROOT / "research" / "v3" / "templates.py")
TP = importlib.util.module_from_spec(_spec)
sys.modules["v3_templates"] = TP
_spec.loader.exec_module(TP)
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def safe(fn, default=""):
    """The value, or the error's text - a crash of the code under test is a named FAIL of the row that reads it."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return default if default != "" else f"raised {type(e).__name__}: {e}"


def err(fn) -> str:
    try:
        fn()
        return "no error"
    except TP.TemplateError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001 - not the named refusal
        return f"not a TemplateError: {type(e).__name__}: {e}"


PINNED = b'''import module_that_does_not_exist  # the file is parsed, never imported
QA_PROMPT = """
Based on the above context, answer. Question: {} Short answer:
"""
def prepare(cot):
    if cot:
        answer_prompt_template = 'I will give you history. Answer step by step.\\n\\nHistory:\\n\\n{}\\n\\nDate: {}\\nQuestion: {}\\nAnswer:'
    else:
        answer_prompt_template = 'I will give you history.\\n\\nHistory:\\n\\n{}\\n\\nDate: {}\\nQuestion: {}\\nAnswer:'
    answer_prompt_template = f"I will give you history {cot}"
    other = 'I will give you history. Not assigned to the name.'
    return answer_prompt_template
BASE = {"fc": {"system": "sys", "query": {"rag_agent": "Pretend. " "Based on the pool, {question} \\nAnswer:",
                                          "long_context_agent": "Long. {question}"}}}
TWICE = "a"
TWICE = "a"
DUP = {"k": "one", "k": "two"}
'''
S = TP.Source

print("- extract: parsed, one constant, marker and exclusions -")
lme = safe(lambda: TP.extract(PINNED, S("p", "answer_prompt_template", marker="I will give you history",
                                         exclude=("step by step",))))
check("the non-CoT variant is the one candidate (the CoT one excluded, the f-string never a candidate)",
      lme == "I will give you history.\n\nHistory:\n\n{}\n\nDate: {}\nQuestion: {}\nAnswer:", repr(lme))
check("a file whose import would fail is still read (parsed, never executed)", "module_that_does_not_exist" in
      PINNED.decode() and lme.startswith("I will give"))
check("a module-level constant by its name", safe(lambda: TP.extract(PINNED, S("p", "QA_PROMPT"))).strip().startswith("Based on"))
rag = safe(lambda: TP.extract(PINNED, S("p", "BASE", path=("fc", "query", "rag_agent"))))
check("a string inside a dict literal, by its key path; adjacent literals are one constant",
      rag == "Pretend. Based on the pool, {question} \nAnswer:", repr(rag))
for label, spec, needle in (
        ("both variants (no exclusion)", S("p", "answer_prompt_template", marker="I will give you history"), "2 string"),
        ("a marker nothing carries", S("p", "QA_PROMPT", marker="nowhere"), "0 string"),
        ("a name assigned the same string twice", S("p", "TWICE"), "2 string"),
        ("a key path that is not there", S("p", "BASE", path=("fc", "query", "memory_agent")), "0 string"),
        ("a key path into a non-dict", S("p", "BASE", path=("fc", "system", "x")), "0 string"),
        ("a name not assigned at all", S("p", "NOPE"), "0 string"),
        ("a key the dict literal repeats (ambiguous)", S("p", "DUP", path=("k",)), "0 string")):
    e = err(lambda s=spec: TP.extract(PINNED, s))
    check(f"refused by name: {label}", needle in e and "exactly one" in e, e)
check("a pinned file that does not parse refuses by name", "does not parse" in err(lambda: TP.extract(b"def (:", S("p", "X"))))
check("a pinned file that is not UTF-8 refuses by name", "does not parse" in err(lambda: TP.extract(b"\xff\xfe", S("p", "X"))))

print("\n- transform: each replacement exactly once, in order -")
t = safe(lambda: TP.transform(lme, [("\nAnswer:", "\nFinish with one line: SHORT ANSWER: <at most 15 words>\nAnswer:")],
                               stand="s1"))
check("a replacement applied once", t.endswith("SHORT ANSWER: <at most 15 words>\nAnswer:") and t.count("Answer:") == 1, repr(t))
check("a replacement that matches nothing refuses", "0 times" in err(lambda: TP.transform(lme, [("zzz", "y")], stand="s1")))
check("a replacement that matches twice refuses", "2 times" in err(lambda: TP.transform("a a", [("a", "b")], stand="s1")))
check("replacements apply in order (the second sees the first's result)",
      TP.transform("x", [("x", "yz"), ("z", "w")]) == "yw")

print("\n- build: the pin first, the declared slots, the template's sha256 -")
TMP = Path(tempfile.mkdtemp(prefix="v3templates_"))
try:
    f = TMP / "pinned.py"
    f.write_bytes(PINNED)
    pin = hashlib.sha256(PINNED).hexdigest()
    spec = S("p", "answer_prompt_template", marker="I will give you history", exclude=("step by step",))
    tpl = safe(lambda: TP.build("s1", f, pin, spec, [("\nAnswer:", "\nSHORT ANSWER line.\nAnswer:")], ["", "", ""]),
               TP.Template("s1", "", "", "", "", ()))
    check("the template is built with its pin, its source sha256 and its own sha256",
          tpl.source_sha256 == pin and tpl.sha256 == hashlib.sha256(tpl.text.encode()).hexdigest()
          and tpl.slots == ("", "", "") and tpl.source_pin == "p")
    check("a pinned file that is not the pin refuses, naming both digests",
          pin in err(lambda: TP.build("s1", f, "0" * 64, spec, [], ["", "", ""])))
    check("slots other than the declared ones refuse",
          "slots" in err(lambda: TP.build("s1", f, pin, spec, [], ["history", "date", "question"])))
    ragt = safe(lambda: TP.build("s6", f, pin, S("p", "BASE", path=("fc", "query", "rag_agent")), [], ["question"]),
                TP.Template("s6", "", "", "", "", ("question",)))

    print("\n- render: in order, verbatim -")
    ctx = "fact {0} with {braces} and }{ odd ones"
    out = safe(lambda: TP.render(tpl, [ctx, "2023-05-08", "Where?"]))
    check("positional slots filled in order, the context's braces inserted verbatim",
          out == f"I will give you history.\n\nHistory:\n\n{ctx}\n\nDate: 2023-05-08\nQuestion: Where?\nSHORT ANSWER line.\nAnswer:",
          repr(out))
    check("a named slot", safe(lambda: TP.render(ragt, {"question": "who {x}?"})) == "Pretend. Based on the pool, who {x}? \nAnswer:")
    for label, fn in (("too few values", lambda: TP.render(tpl, ["a", "b"])),
                      ("too many values", lambda: TP.render(tpl, ["a", "b", "c", "d"])),
                      ("names for positional slots", lambda: TP.render(tpl, {"a": "1"})),
                      ("a wrong name", lambda: TP.render(ragt, {"q": "1"})),
                      ("positional values for a named slot", lambda: TP.render(ragt, ["1"])),
                      ("a non-text value", lambda: TP.render(tpl, ["a", 2, "c"]))):
        check(f"render refuses {label}", err(fn) != "no error" and not err(fn).startswith("not a"), err(fn))
    esc = TP.Template("x", "a {{literal}} {} b", "", "p", "", ("",))
    check("a doubled brace in the template stays one literal brace", safe(lambda: TP.render(esc, ["v"])) == "a {literal} v b")
    check("a slot with a format spec or conversion refuses", "format spec" in err(lambda: TP.slots_of("a {:>10} b"))
          and "format spec" in err(lambda: TP.slots_of("a {!r} b")))
    check("unbalanced braces refuse", "do not parse" in err(lambda: TP.slots_of("a { b")))

    print("\n- the stands (Q-48): a function of the stand alone, pending ones named, S4 from a gpt_utils-shaped file -")
    import inspect  # noqa: PLC0415
    params = inspect.signature(TP.stand_template).parameters
    check("Q-48-6 stand_template takes the stand (and where the pins are) - never an arm, a bracket or **kwargs",
          list(params) == ["stand", "pins_root"] and all(p.kind != p.VAR_KEYWORD for p in params.values()), str(list(params)))
    for s in ("S1", "S3", "S5", "S6", "S6L", "S7"):
        e = err(lambda s=s: TP.stand_template(s, pins_root=TMP))
        check(f"{s} is pending with its named reason, never guessed", "no template yet" in e and "A7" in e or "as S" in e, e)
    check("a stand without a reader template refuses", "not a stand" in err(lambda: TP.stand_template("S2", pins_root=TMP)))
    GPT = (b'''QA_PROMPT = """
Based on the above context, write a short phrase. Use exact words.

Question: {} Short answer:
"""
QA_PROMPT_CAT_5 = """
Based on the above context, answer the following question.

Question: {} Short answer:
"""
# If no information is available to answer the question, write 'No information available'.
def ask(qa):
    return qa["question"] + " Use DATE of CONVERSATION to answer with an approximate date."
''')
    gf = TMP / "gpt_utils.py"
    gf.write_bytes(GPT)
    real_pinned = TP._pinned
    TP._pinned = lambda pin, root: (gf, hashlib.sha256(GPT).hexdigest())
    try:
        blank = TP.Template("x", "", "", "", "", ())
        s4 = safe(lambda: TP.stand_template("S4", pins_root=TMP), blank)
        s5c = safe(lambda: TP.stand_template("S4-cat5", pins_root=TMP), blank)
        want = ("{context}\n\n\nBased on the above context, write a short phrase. Use exact words.\n\nQuestion: {question}\n"
                "Finish your reply with one line: SHORT ANSWER: <at most 15 words>\n")
        check("S4 = the arm's block, the benchmark's own blank lines and QA_PROMPT, the SHORT ANSWER line for 'Short answer:'",
              s4.text == want and s4.slots == ("context", "question"), repr(s4.text))
        check("S4-cat5 = the same with LoCoMo's own 'No information available' line before the SHORT ANSWER line",
              s5c.text == want.replace("Question: {question}\n", "Question: {question}\n" + TP.LOCOMO_NO_INFO + "\n"),
              repr(s5c.text))
        check("the SHORT ANSWER instruction is rev1 §8.1's", TP.SHORT_ANSWER.endswith("SHORT ANSWER: <at most 15 words>"))
        rendered = safe(lambda: TP.render(s4, {"context": "- item one\n- item {two}", "question": "Where?"}))
        check("rendered: the block verbatim where the conversation stood", rendered.startswith("- item one\n- item {two}\n\n\nBased"))
        check("cat 2: the pinned file's own DATE suffix is appended; cat 1 and cat 5 are asked as is",
              TP.locomo_question("When?", 2, pins_root=TMP) == "When? Use DATE of CONVERSATION to answer with an approximate date."
              and TP.locomo_question("When?", 1, pins_root=TMP) == "When?" and TP.locomo_question("When?", 5, pins_root=TMP) == "When?")
        check("B-CAT: the category as the loader keeps it (\"2\") gets the DATE suffix exactly as 2 does; \"5\" and None "
              "are asked as is; a word or a bool is refused by name",
              TP.locomo_question("When?", "2", pins_root=TMP) == TP.locomo_question("When?", 2, pins_root=TMP)
              != "When?" and TP.locomo_question("When?", "5", pins_root=TMP) == "When?"
              and TP.locomo_question("When?", None, pins_root=TMP) == "When?"
              and "not a category number" in err(lambda: TP.locomo_question("When?", "two", pins_root=TMP))
              and "bool" in err(lambda: TP.locomo_question("When?", True, pins_root=TMP)))
        tf = TMP / "runs" / "freeze_templates.json"
        fr = safe(lambda: TP.freeze_fragment(pins_root=TMP, texts_path=tf),
                  {"templates": {"S4": {}, "S4-cat5": {}}, "pending": {}, "texts_file": {}})
        committed = json.dumps(fr, ensure_ascii=False)
        check("the committed FREEZE fragment holds the pin, our replacements, the slots and the sha256 - and NO template "
              "text (the auditor on Q-A3-6)",
              fr["templates"]["S4"].get("sha256") == s4.sha256 and fr["templates"]["S4"].get("source_pin") == "locomo_answer_prompt"
              and fr["templates"]["S4"].get("replacements") == [list(r) for r in TP.STANDS["S4"].replacements]
              and fr["templates"]["S4"].get("slots") == ["context", "question"] and "text" not in fr["templates"]["S4"]
              and "write a short phrase" not in committed and "Use exact words" not in committed
              and set(fr["pending"]) == set(TP.PENDING), committed[:300])
        texts = json.loads(tf.read_bytes()) if tf.is_file() else {}
        check("the full texts go to the runs-tree file, whose sha256 the fragment records",
              texts.get("templates", {}).get("S4", {}).get("text") == s4.text
              and texts["templates"]["S4-cat5"]["text"] == s5c.text
              and fr["texts_file"].get("sha256") == hashlib.sha256(tf.read_bytes()).hexdigest(), str(fr.get("texts_file")))
        GPT2 = GPT.replace(b"# If no information is available to answer the question, write 'No information available'.\n", b"")
        gf.write_bytes(GPT2)
        TP._pinned = lambda pin, root: (gf, hashlib.sha256(GPT2).hexdigest())
        check("S4-cat5 refuses when LoCoMo's own line does not occur in the pinned file (the text is the benchmark's)",
              "does not occur" in err(lambda: TP.stand_template("S4-cat5", pins_root=TMP)))
        GPT3 = GPT + b"X = 'Use DATE of CONVERSATION twice'\n"
        gf.write_bytes(GPT3)
        TP._pinned = lambda pin, root: (gf, hashlib.sha256(GPT3).hexdigest())
        check("cat 2's suffix must be exactly one constant of the pinned file",
              "exactly one" in err(lambda: TP.locomo_question("q", 2, pins_root=TMP)))
        TP._pinned = lambda pin, root: (gf, "0" * 64)
        check("cat 2's suffix is read only from the pinned file", "not the pin" in err(lambda: TP.locomo_question("q", 2, pins_root=TMP)))
    finally:
        TP._pinned = real_pinned
    REAL = Path("D:/Coding/_nevertwice_polygon/runs/v3/_pins")
    try:
        real = TP.stand_template("S4", pins_root=REAL)
    except (TP.TemplateError, OSError, KeyError) as e:
        real = None
        print(f"  skip the real-pin rows: {type(e).__name__} (the polygon's pins are not on this machine; not counted)")
    if real is not None:
        check("local: S4 builds from the real pin - the block first, QA_PROMPT's text, the SHORT ANSWER line last",
              real.text.startswith("{context}\n\n\nBased on the above context") and real.text.endswith(TP.SHORT_ANSWER + "\n")
              and real.slots == ("context", "question") and "Short answer:" not in real.text)
        check("local: the build is deterministic (the same sha256 twice)", TP.stand_template("S4", pins_root=REAL).sha256 == real.sha256)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 templates: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
