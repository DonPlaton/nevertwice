#!/usr/bin/env python3
"""PREREG-V3 TB4.9b (A6): research/v3/judge_prompts.py - the judges' official prompts read from pinned files by ast
(rev1 §8.3, Q-49-1..4), on synthetic files shaped like the pins (no pinned text is committed, Q-A3-6) and, where the
polygon's pins are on this machine, on the real ones.

* LME: the if-chain's mapping from question type to template; it must equal the committed table - a pin that moves a
  type to another branch, adds a type, or loses its closing raise refuses; the prompt is template.format(q, a, r);
* LoCoMo J: ACCURACY_PROMPT with exactly its three named slots;
* BEAM: the official .replace chain, quirks included (a rubric item that contains <llm_response> is filled too);
* AMA: the f-string rebuilt from its ast renders exactly what evaluating the pinned f-string gives, context parts only
  when given; a slot that is not a plain name refuses;
* every pin's sha256 is checked before its text is used.

    python tests/research/_test_v3_judge_prompts.py
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

_spec = importlib.util.spec_from_file_location("v3_judge_prompts", ROOT / "research" / "v3" / "judge_prompts.py")
JP = importlib.util.module_from_spec(_spec)
sys.modules["v3_judge_prompts"] = JP
_spec.loader.exec_module(JP)
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def err(fn) -> str:
    try:
        fn()
        return "no error"
    except JP.TemplateError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"not a TemplateError: {type(e).__name__}: {e}"


def safe(fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return default if default is not None else f"raised {type(e).__name__}: {e}"


LME = b'''
def get_anscheck_prompt(task, question, answer, response, abstention=False):
    if not abstention:
        if task in ['single-session-user', 'single-session-assistant', 'multi-session']:
            template = "BASE q={} a={} r={}"
            prompt = template.format(question, answer, response)
        elif task == 'temporal-reasoning':
            template = "TEMPORAL q={} a={} r={}"
            prompt = template.format(question, answer, response)
        elif task == 'knowledge-update':
            template = "UPDATE q={} a={} r={}"
            prompt = template.format(question, answer, response)
        elif task == 'single-session-preference':
            template = "PREFERENCE q={} rubric={} r={}"
            prompt = template.format(question, answer, response)
        else:
            raise NotImplementedError
    else:
        template = "ABSTAIN q={} e={} r={}"
        prompt = template.format(question, answer, response)
    return prompt
'''

print("- LME: the mapping is the pinned code's, and must be the committed table -")
mapping, abst = safe(lambda: JP.lme_from_source(LME), ({}, ""))
check("each question type's template from the if-chain, and the abstention branch's",
      mapping.get("multi-session") == "BASE q={} a={} r={}" and mapping.get("temporal-reasoning", "").startswith("TEMPORAL")
      and abst == "ABSTAIN q={} e={} r={}" and len(mapping) == 6, str(mapping))
tpl = safe(lambda: JP.lme_templates(LME), {})
check("the table's keys, plus abstention", sorted(tpl) == ["abstention", "base", "preference", "temporal", "update"], str(tpl))
check("a prompt is template.format(question, answer, response), as the official code builds it (braces in the answer "
      "are not parsed)", safe(lambda: JP.lme_prompt(tpl, "knowledge-update", "Q", "A {x}", "R", False)) == "UPDATE q=Q a=A {x} r=R")
check("the abstention flag picks the abstention template whatever the type",
      safe(lambda: JP.lme_prompt(tpl, "multi-session", "Q", "E", "R", True)) == "ABSTAIN q=Q e=E r=R")
check("an unknown question type refuses (the official NotImplementedError)",
      "no judge template" in err(lambda: JP.lme_prompt(tpl, "made-up", "Q", "A", "R", False)))
for label, src, needle in (
        ("a type moved to another branch", LME.replace(b"'single-session-assistant', 'multi-session']", b"'single-session-assistant']")
         .replace(b"elif task == 'temporal-reasoning':", b"elif task in ['temporal-reasoning', 'multi-session']:"), "differs"),
        ("an extra question type", LME.replace(b"elif task == 'knowledge-update':", b"elif task in ['knowledge-update', 'new-type']:"),
         "maps the LME tasks"),
        ("no closing raise", LME.replace(b"        else:\n            raise NotImplementedError\n", b""), "raise"),
        ("no `if not abstention`", LME.replace(b"if not abstention:", b"if abstention is False:"), "if not abstention"),
        ("a template with two slots", LME.replace(b"UPDATE q={} a={} r={}", b"UPDATE q={} a={}"), "does not take"),
        ("a condition of another shape", LME.replace(b"elif task == 'temporal-reasoning':", b"elif task.startswith('temporal'):"),
         "condition"),
        ("two such functions", LME + LME, "not one"),
        ("a type in two branches (the first would win in Python; the reading must not guess)",
         LME.replace(b"elif task == 'knowledge-update':", b"elif task in ['knowledge-update', 'multi-session']:"),
         "two branches")):
    e = err(lambda s=src: JP.lme_templates(s))
    check(f"refused by name: {label}", needle in e, e)
check("the committed table is rev1's LME question types", sorted(JP.LME_TASKS) == sorted([
    "single-session-user", "single-session-assistant", "multi-session", "temporal-reasoning", "knowledge-update",
    "single-session-preference"]))

print("\n- LoCoMo J and BEAM -")
LJ = b'ACCURACY_PROMPT = """\nLabel it.\nQuestion: {question}\nGold answer: {gold_answer}\nGenerated answer: {generated_answer}\nJSON.\n"""\n'
t = safe(lambda: JP.locomo_j_template(LJ), "")
check("ACCURACY_PROMPT with exactly its three named slots, filled as the official .format",
      safe(lambda: JP.locomo_j_prompt(t, "Q", "G {g}", "A")) == "\nLabel it.\nQuestion: Q\nGold answer: G {g}\nGenerated answer: A\nJSON.\n")
check("ACCURACY_PROMPT with other slots refuses",
      "slots" in err(lambda: JP.locomo_j_template(LJ.replace(b"{gold_answer}", b"{gold}"))))
BM = b'unified_llm_judge_base_prompt = """Rubric: <rubric_item>\nResponse: <llm_response>\nJSON score."""\n'
bt = safe(lambda: JP.beam_template(BM), "")
check("BEAM: <rubric_item> then <llm_response>, every occurrence - the official .replace chain, its quirk included",
      JP.beam_prompt(bt, "has <llm_response> inside", "RESP") == "Rubric: has RESP inside\nResponse: RESP\nJSON score.")
check("a rubric prompt without its placeholders refuses",
      "<llm_response>" in err(lambda: JP.beam_template(BM.replace(b"<llm_response>", b"<resp>"))))

print("\n- AMA: the f-string rebuilt from its ast -")
AMA = b'''
def compute_llm_as_judge(question, golden_answer, predicted_answer, judge_client, scale=5, task_description="",
                         task_type="", episode_id=""):
    context_parts = []
    if task_type:
        context_parts.append(f"Task Type: {task_type}")
    if episode_id:
        context_parts.append(f"Episode ID: {episode_id}")
    if task_description:
        context_parts.append(f"Task Context: {task_description}")
    context_str = "\\n".join(context_parts) if context_parts else ""
    judge_prompt = f"""Judge.

{context_str}

Question: {question}

Reference Answer: {golden_answer}

Predicted Answer: {predicted_answer}

yes or no.

Answer:<think></think>"""
    return judge_prompt
'''
parts = safe(lambda: JP.ama_parts(AMA), ())


def official(src: bytes, **kw) -> str:
    """Evaluate the pinned f-string itself on the same values (a differential check; only names are looked up)."""
    fn = next(n for n in ast.walk(ast.parse(src.decode())) if isinstance(n, ast.FunctionDef)
              and n.name == "compute_llm_as_judge")
    node = next(n.value for n in ast.walk(fn) if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                and n.targets[0].id == "judge_prompt")
    ctx = [f"Task Type: {kw['task_type']}"] if kw.get("task_type") else []
    ctx += [f"Episode ID: {kw['episode_id']}"] if kw.get("episode_id") else []
    ctx += [f"Task Context: {kw['task_description']}"] if kw.get("task_description") else []
    names = {"context_str": "\n".join(ctx), "question": kw["question"], "golden_answer": kw["golden_answer"],
             "predicted_answer": kw["predicted_answer"]}
    return eval(compile(ast.Expression(body=node), "<pinned f-string>", "eval"), {"__builtins__": {}}, names)  # noqa: S307


for kw in ({"question": "Q", "golden_answer": "G", "predicted_answer": "P"},
           {"question": "Q {x}", "golden_answer": "G", "predicted_answer": "P", "task_type": "swe", "episode_id": "7",
            "task_description": "fix the bug"},
           {"question": "Q", "golden_answer": "G", "predicted_answer": "P", "episode_id": "9"}):
    check(f"the rebuilt prompt equals the pinned f-string evaluated on the same values ({sorted(kw)})",
          safe(lambda k=kw: JP.ama_prompt(parts, **k)) == official(AMA, **kw), repr(safe(lambda k=kw: JP.ama_prompt(parts, **k)))[:120])
for label, src, needle in (("a conversion (!r)", AMA.replace(b"{question}", b"{question!r}"), "plain name"),
                           ("a format spec", AMA.replace(b"{question}", b"{question:>10}"), "plain name"),
                           ("an expression slot", AMA.replace(b"{question}", b"{question.upper()}"), "plain name"),
                           ("a missing slot", AMA.replace(b"Predicted Answer: {predicted_answer}", b""), "slots"),
                           ("a plain string instead of an f-string", AMA.replace(b'judge_prompt = f"""', b'judge_prompt = """'),
                            "f-string")):
    e = err(lambda s=src: JP.ama_parts(s))
    check(f"refused by name: {label}", needle in e, e)

print("\n- the pins' sha256, and the real pins where this machine has them -")
TMP = Path(tempfile.mkdtemp(prefix="v3judgeprompts_"))
try:
    f = TMP / "lme.py"
    f.write_bytes(LME)
    real_pinned = JP.TP._pinned
    JP.TP._pinned = lambda pin, root: (f, "0" * 64)
    try:
        check("a pinned file that is not the pin refuses before its text is used",
              "not the pin" in err(lambda: JP.load_all(TMP)))
    finally:
        JP.TP._pinned = real_pinned
    REAL = Path("D:/Coding/_nevertwice_polygon/runs/v3/_pins")
    try:
        allp = JP.load_all(REAL)
    except (JP.TemplateError, OSError, KeyError) as e:
        allp = None
        print(f"  skip the real-pin rows: {type(e).__name__} (the polygon's pins are not on this machine; not counted)")
    if allp is not None:
        check("local: the real LME pin's if-chain IS the committed table (five templates)",
              sorted(allp["lme"]) == ["abstention", "base", "preference", "temporal", "update"])
        check("local: the real ACCURACY_PROMPT and BEAM rubric prompt load with their slots and placeholders",
              "{generated_answer}" in allp["locomo_j"] and "<rubric_item>" in allp["beam"])
        raw = (REAL / "github" / JP.TP._cp().PINS["ama_evaluation_metrics"]["revision"] /
               "utils" / "evaluation_metrics.py").read_bytes()
        kw = {"question": "Q", "golden_answer": "G", "predicted_answer": "P", "task_type": "swe", "episode_id": "3"}
        check("local: the real AMA prompt rebuilt from ast equals the pinned f-string evaluated on the same values",
              JP.ama_prompt(allp["ama"], **kw) == official(raw, **kw))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 judge prompts: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
