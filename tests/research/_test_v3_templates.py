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

import ast
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
    check("no stand is pending any more - S1, S3 and S7 are built from their pins (Q-TPL-1, Q-TPL-4)", TP.PENDING == {},
          str(TP.PENDING))
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
    #: BEAM's src/prompts.py and MAB's utils/templates.py in their shapes (made up here; the vendors' texts are not
    #: committed, Q-A3-6): another constant with the same markers, the dict of query templates by agent
    BEAM = (b'nugget_prompt = """Judge <context> against <question>."""\n'
            b'answer_generation_for_rag = """\nAnswer ONLY from the context below.\n\nCONTEXT:\n<context>\n\nQUESTION:\n'
            b'<question>\n\nANSWER REQUIREMENTS:\n- Be concise \n\nRESPONSE:\n"""\n')
    BEAM2 = BEAM.replace(b"Answer ONLY from the context below.", b"Answer ONLY from <context> below.")
    MAB_Q = ("Pretend you are a knowledge management system. Answer from the pool. \n\nFor example:\n Question: who? "
             "\nAnswer: Someone \n\n Now Answer the Question: Based on the provided Knowledge Pool, {question} \nAnswer:")
    MAB_A = MAB_Q.replace("the pool", "the Archival Memory")

    def mab_src(long_q: str) -> bytes:
        return ("SYSTEM_MESSAGE = 'You are a helpful assistant.'\nBASE_TEMPLATES = {'factconsolidation': {"
                "'system': SYSTEM_MESSAGE, 'query': {'long_context_agent': %s, 'rag_agent': %s, "
                "'agentic_memory_agent': %s}}}\n" % (json.dumps(long_q), json.dumps(MAB_Q), json.dumps(MAB_A))).encode()
    MAB = mab_src(MAB_Q)
    bf, mf = TMP / "prompts.py", TMP / "templates_mab.py"
    bf.write_bytes(BEAM)
    mf.write_bytes(MAB)

    #: LME's run_generation.py and run_generation.sh, AMA's longcontext.py - in their shapes (made up here, Q-A3-6)
    LMEF = (b"def prepare_prompt(entry, retriever_type, cot, merge):\n"
            b"    if retriever_type == 'no-retrieval':\n"
            b"        answer_prompt_template = '{}'\n"
            b"        if cot:\n"
            b"            answer_prompt_template += 'Answer step by step.'\n"
            b"    elif merge == 'none':\n"
            b"        if cot:\n"
            b"            answer_prompt_template = 'I will give you chats. Please answer based on the relevant chat history. "
            b"Answer the question step by step: extract, then reason.\\n\\n\\nHistory Chats:\\n\\n{}\\n\\nCurrent Date: {}\\n"
            b"Question: {}\\nAnswer (step by step):'\n"
            b"        else:\n"
            b"            answer_prompt_template = 'I will give you chats. Please answer based on the relevant chat history.\\n\\n\\n"
            b"History Chats:\\n\\n{}\\n\\nCurrent Date: {}\\nQuestion: {}\\nAnswer:'\n"
            b"    elif merge == 'merge':\n"
            b"        if cot:\n"
            b"            answer_prompt_template = 'I will give you chats and facts. Please answer based on the relevant chat "
            b"history and the user facts. Answer the question step by step: x.\\n\\n{}\\n\\n{}\\n\\nCurrent Date: {}\\n"
            b"Question: {}\\nAnswer (step by step):'\n"
            b"    return answer_prompt_template\n")
    LMESH = (b'#!/bin/bash\nreading_method=${7:-"con"}\nif [[ $reading_method == "direct" ]]; then\n'
             b'    reading_flags="--cot false"\nelif [[ $reading_method == "con" ]]; then\n    reading_flags="--cot true"\n'
             b'elif [[ $reading_method == "con-separate" ]]; then\n    reading_flags="--cot true --con true"\nfi\n')
    INTRO = ("Please answer the following questions based on the task description and agent trajectory above. For each "
             "question, provide a direct and concise answer.")
    INSTR = "Please provide answers in the following format:"
    LCF_SRC = (
        "def prompt(memory, questions, mcq_mode):\n"
        "    questions_block = \"\\n\".join(\n"
        "        f\"Question {i}: {q}\\n\"\n"
        "        for i, q in enumerate(questions, 1)\n"
        "    )\n"
        "    if mcq_mode:\n"
        "        section_intro = (\n"
        "            \"Please answer the following multiple-choice questions based on \"\n"
        "            \"the task description and agent trajectory above.\"\n"
        "        )\n"
        "        instructions = (\"For each question, select all correct options.\")\n"
        "        answer_slots = \"\\n\".join(f\"Answer[{i}]: [(A)/(B)/(C)/(D)]\" for i in range(1, len(questions) + 1))\n"
        "    else:\n"
        "        section_intro = (\n"
        "            \"Please answer the following questions based on the task description \"\n"
        "            \"and agent trajectory above. For each question, provide a direct and \"\n"
        "            \"concise answer.\"\n"
        "        )\n"
        "        instructions = \"Please provide answers in the following format:\"\n"
        "        answer_slots = \"\\n\".join(f\"Answer[{i}]: [your answer here]\" for i in range(1, len(questions) + 1))\n"
        "    suffix = (\n"
        "        f\"\\n\\n## Questions\\n{section_intro}\\n\\n\"\n"
        "        f\"{questions_block}\\n\"\n"
        "        f\"## Instructions\\n{instructions}\\n\\n\"\n"
        "        f\"{answer_slots}\"\n"
        "    )\n"
        "    return memory + suffix\n")
    LCF = LCF_SRC.encode()
    lf, shf, lcf = TMP / "run_generation.py", TMP / "run_generation.sh", TMP / "longcontext.py"
    lf.write_bytes(LMEF)
    shf.write_bytes(LMESH)
    lcf.write_bytes(LCF)

    def fakes(**over):
        """_pinned for the test: each pin its own made-up file, by name (``over``: pin -> bytes)."""
        files = {"locomo_answer_prompt": gf, "beam_prompts": bf, "mab_templates": mf, "lme_answer_prompt": lf,
                 "lme_run_generation_sh": shf, "ama_method_longcontext": lcf}
        def pinned(pin, root):
            if pin in over:
                f_ = TMP / f"over_{pin}.py"
                f_.write_bytes(over[pin])
                return f_, hashlib.sha256(over[pin]).hexdigest()
            return files[pin], hashlib.sha256(files[pin].read_bytes()).hexdigest()
        return pinned
    real_pinned = TP._pinned
    TP._pinned = fakes()
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
        s5 = safe(lambda: TP.stand_template("S5", pins_root=TMP), blank)
        s5f = safe(lambda: TP.stand_template("S5-full", pins_root=TMP), blank)
        want5 = ("\nAnswer ONLY from the context below.\n\nCONTEXT:\n{context}\n\nQUESTION:\n{question}\n\n"
                 "ANSWER REQUIREMENTS:\n- Be concise \n" + TP.SHORT_ANSWER + "\n\nRESPONSE:\n")
        check("T5-1 (Q-TPL-2): S5 = BEAM's own answer_generation_for_rag (never another constant with the same markers), "
              "its <context> and <question> the slots, the SHORT ANSWER line before RESPONSE: - the short-answer abilities",
              s5.text == want5 and s5.slots == ("context", "question"), repr(s5.text))
        check("T5-2 (§8.4): S5-full = the same without the SHORT ANSWER line - long form and event ordering",
              s5f.text == want5.replace(TP.SHORT_ANSWER + "\n", "") and s5f.slots == ("context", "question"), repr(s5f.text))
        TP._pinned = fakes(beam_prompts=BEAM2)
        check("T5-3: a BEAM prompt whose <context> occurs twice is refused by name - each marker becomes a slot exactly once",
              "2 times" in err(lambda: TP.stand_template("S5", pins_root=TMP))
              and "2 times" in err(lambda: TP.stand_template("S5-full", pins_root=TMP)))
        TP._pinned = fakes()
        s6 = safe(lambda: TP.stand_template("S6", pins_root=TMP), blank)
        s6l = safe(lambda: TP.stand_template("S6L", pins_root=TMP), blank)
        want6 = "{context}\n" + MAB_Q.replace("{question} \nAnswer:", "{question} \n" + TP.SHORT_ANSWER + "\nAnswer:")
        check("T6-1 (Q-TPL-3): S6 = MAB's BASE_TEMPLATES[factconsolidation][query][rag_agent], the arm's block and a "
              "newline before it as the driver joins them (agent.py:317), the SHORT ANSWER line before the last 'Answer:'",
              s6.text == want6 and s6.slots == ("context", "question"), repr(s6.text))
        check("T6-2: S6L = S6, read from long_context_agent - S6 and S6L never differ",
              s6l.text == s6.text == want6 and s6l.slots == s6.slots and s6l.sha256 == s6.sha256, repr(s6l.text))
        TP._pinned = fakes(mab_templates=mab_src(MAB_Q.replace("Answer from the pool.", "Answer from the long pool.")))
        e6l = err(lambda: TP.stand_template("S6L", pins_root=TMP))
        check("T6-3: when long_context_agent is not rag_agent's text, S6L is refused by name - S6 still builds",
              "S6L" in e6l and "rag_agent" in e6l and safe(lambda: TP.stand_template("S6", pins_root=TMP), blank).text == want6, e6l)
        TP._pinned = fakes()
        r6 = safe(lambda: TP.render(s6, {"context": "Memory 1:\nfact {x}", "question": "who?"}))
        check("T6-4: rendered, the arm's block comes first, verbatim, then a newline and the benchmark's query",
              r6.startswith("Memory 1:\nfact {x}\nPretend you are a knowledge management system.") and "who? \n" in r6, repr(r6[:120]))
        s1 = safe(lambda: TP.stand_template("S1", pins_root=TMP), blank)
        s3 = safe(lambda: TP.stand_template("S3", pins_root=TMP), blank)
        want1 = ("I will give you chats. Please answer based on the relevant chat history. Answer the question step by step: "
                 "extract, then reason.\n\n\nHistory Chats:\n\n{}\n\nCurrent Date: {}\nQuestion: {}\n" + getattr(TP, "LME_UNANSWERABLE", "(missing)")
                 + "\n" + TP.SHORT_ANSWER + "\nAnswer (step by step):")
        check("T1-1 (Q-TPL-1): S1 = LME's cot template (none merge) - the documented command's READING_METHOD 'con' passes "
              "--cot true; three slots (the arm's block, the date, the question); the unanswerable line and the SHORT "
              "ANSWER line before 'Answer (step by step):'", s1.text == want1 and s1.slots == ("", "", ""), repr(s1.text))
        check("T1-2: S3 (the oracle bracket) reads S1's template", s3.text == s1.text == want1 and s3.sha256 == s1.sha256, repr(s3.text[:80]))
        TP._pinned = fakes(lme_run_generation_sh=LMESH.replace(b'reading_flags="--cot true"\nelif', b'reading_flags="--cot false"\nelif'))
        e1 = err(lambda: TP.stand_template("S1", pins_root=TMP))
        check("T1-3: when the pinned run_generation.sh does not map 'con' to --cot true, S1 is refused by name - the cot "
              "template rests on that line", "S1" in e1 and "lme_run_generation_sh" in e1 and "does not occur" in e1, e1)
        TP._pinned = fakes(lme_run_generation_sh=LMESH.replace(b'${7:-"con"}', b'${7:-"direct"}'))
        e1b = err(lambda: TP.stand_template("S1", pins_root=TMP))
        check("T1-4: ... and when its default READING_METHOD is not 'con'", "S1" in e1b and "does not occur" in e1b, e1b)
        TP._pinned = fakes(lme_answer_prompt=LMEF.replace(b"relevant chat history and the user facts. Answer",
                                                          b"relevant chat history. Answer"))
        check("T1-5: two constants carrying S1's marker are refused by name - exactly one is its source",
              "exactly one" in err(lambda: TP.stand_template("S1", pins_root=TMP)))
        TP._pinned = fakes()
        s7 = safe(lambda: TP.stand_template("S7", pins_root=TMP), blank)
        want7 = ("{context}\n\n## Questions\n" + INTRO + "\n\nQuestion 1: {question}\n\n## Instructions\n" + INSTR + "\n\n"
                 + TP.SHORT_ANSWER)
        check("T7-1 (Q-TPL-4 = O-d): S7 = the arm's block and AMA's list-mode suffix for one question - its non-MCQ "
              "section_intro and instructions, the question line, the answer slot turned into the SHORT ANSWER line",
              s7.text == want7 and s7.slots == ("context", "question"), repr(s7.text))
        bad7 = {
            "the suffix's layout": (LCF_SRC.replace('f\"## Instructions\\n{instructions}\\n\\n\"', 'f\"## Notes\\n{instructions}\\n\\n\"'),
                                    "the suffix"),
            "the question line": (LCF_SRC.replace('f\"Question {i}: {q}\\n\"', 'f\"Q{i}: {q}\\n\"'), "a question line"),
            "the answer slot": (LCF_SRC.replace("[your answer here]", "[answer]"), "an answer slot"),
            "the non-MCQ intro": (LCF_SRC.replace("provide a direct and ", "be "), "section_intro"),
        }
        for label, (src_, words) in bad7.items():
            TP._pinned = fakes(ama_method_longcontext=src_.encode())
            e7 = err(lambda: TP.stand_template("S7", pins_root=TMP))
            check(f"T7-2 ({label}): a pinned longcontext.py whose {label} is not the vendor's as read is refused by name",
                  src_ != LCF_SRC and words in e7 and "S7" in e7 or (words == "section_intro" and "exactly one" in e7), e7)
        TP._pinned = fakes()
        r7 = safe(lambda: TP.render(s7, {"context": "trajectory {x}", "question": "What did the agent do?"}))
        check("T7-3: rendered, the arm's block, then the vendor's questions section with our one question",
              r7.startswith("trajectory {x}\n\n## Questions\n") and "Question 1: What did the agent do?\n" in r7, repr(r7[:160]))
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
        check("T6-5: the FREEZE fragment names, for S6 and S6L, the system message the MAB driver sends and the stand's reader "
              "does not (SYSTEM_MESSAGE); S4 carries no note; S5, S5-full, S6 and S6L are built, S1, S3 and S7 pending",
              all("SYSTEM_MESSAGE" in fr["templates"].get(s, {}).get("note", "") for s in ("S6", "S6L"))
              and "note" not in fr["templates"].get("S4", {})
              and {"S5", "S5-full", "S6", "S6L"} <= set(fr["templates"]) and set(fr["pending"]) == set(),
              str({s: fr["templates"].get(s, {}).get("note") for s in ("S4", "S6", "S6L")}))
        check("T1-6/T7-4: the FREEZE notes - S1 and S3 name --cot true from run_generation.sh's 'con' and the reader's "
              "max_tokens against the vendor's 800; S7 names the dead ANSWER_* constants and n = 1; nothing is pending",
              all("--cot true" in fr["templates"].get(s, {}).get("note", "") and "800" in fr["templates"][s]["note"]
                  for s in ("S1", "S3"))
              and "ANSWER_WITH_RETRIEVAL_PROMPT_TEMPLATE" in fr["templates"].get("S7", {}).get("note", "")
              and "n = 1" in fr["templates"]["S7"]["note"] and fr["pending"] == {}, str({s: fr["templates"].get(s, {}).get("note")
                                                                                      for s in ("S1", "S7")})[:500])
        _rj = ast.parse((ROOT / "research" / "v3" / "reader_judge.py").read_text(encoding="utf-8"))
        _rp = next((ast.literal_eval(n.value) for n in ast.walk(_rj) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "READER_PARAMS" for t in n.targets)), {})
        check("T1-7 (Q-TPL-1): the reader's max_tokens is at least the vendor's 800 for S1/S3 (gen_length with cot, "
              "run_generation.py:342) - the same for every arm", _rp.get("max_tokens", 0) >= 800, str(_rp))
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
        r5, r5f, r6_, r6l = (safe(lambda s=s: TP.stand_template(s, pins_root=REAL), None) for s in ("S5", "S5-full", "S6", "S6L"))
        r1, r3, r7_ = (safe(lambda s=s: TP.stand_template(s, pins_root=REAL), None) for s in ("S1", "S3", "S7"))
        check("local: S1 and S3 build from the real pins (run_generation.py's cot line, run_generation.sh's 'con' mapping) and "
              "are one text - three slots, 'Answer (step by step):' last",
              r1 is not None and r3 is not None and r1.text == r3.text and r1.slots == ("", "", "")
              and r1.text.endswith(TP.SHORT_ANSWER + "\nAnswer (step by step):"))
        check("local: S7 builds from the real longcontext.py pin - the block first, the vendor's questions section, the "
              "SHORT ANSWER line last", r7_ is not None and r7_.slots == ("context", "question")
              and r7_.text.startswith("{context}\n\n## Questions\n") and r7_.text.endswith(TP.SHORT_ANSWER))
        check("local: S5 and S5-full build from the real beam_prompts pin - two slots, no '<context>' left, the SHORT ANSWER line "
              "only in S5", r5 is not None and r5f is not None and r5.slots == r5f.slots == ("context", "question")
              and "<context>" not in r5.text and TP.SHORT_ANSWER in r5.text and TP.SHORT_ANSWER not in r5f.text)
        check("local: S6 and S6L build from the real mab_templates pin and are one text - the block first, the SHORT ANSWER "
              "line before the last 'Answer:'", r6_ is not None and r6l is not None and r6_.text == r6l.text
              and r6_.text.startswith("{context}\nPretend you are a knowledge management system.")
              and r6_.text.endswith(TP.SHORT_ANSWER + "\nAnswer:"))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 templates: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
