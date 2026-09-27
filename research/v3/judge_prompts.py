#!/usr/bin/env python3
"""PREREG-V3 TB4.9b (A6): the judges' official prompts, read from the pinned files - parsed with ast, never imported
or executed (rev1 §8.3; the auditor's Q-49-1..4; Q-A3-6: no prompt text is committed, it is read at run time).

* LME (evaluate_qa.py @9e0b455): get_anscheck_prompt's if-chain gives each question type its template and the
  abstention branch its own; the mapping is read from the pinned code's conditions and must equal the committed
  LME_TASKS table (Q-49-1) - a pin whose conditions differ refuses. A prompt is template.format(question, answer,
  response), as the official code builds it; an unknown question type refuses (the official NotImplementedError).
* LoCoMo J (mem0 evaluation/metrics/llm_judge.py @b3ede5b): ACCURACY_PROMPT.format(question=, gold_answer=,
  generated_answer=).
* BEAM (src/prompts.py @b2da22e): unified_llm_judge_base_prompt with "<rubric_item>" then "<llm_response>" replaced,
  in that order, every occurrence - compute_metrics.py's own .replace chain.
* AMA (utils/evaluation_metrics.py @ddfd319): the judge prompt is an f-string inside compute_llm_as_judge; it is
  rebuilt from its ast - literal parts and one slot per name - and filled exactly as the function does: context_str
  is "Task Type: ...", "Episode ID: ...", "Task Context: ..." for each one given, joined by LF (Q-49-4).
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Mapping

HERE = Path(__file__).resolve().parent


def _sibling(name: str):
    key = f"v3_{name}_for_judge_prompts"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return sys.modules[key]


TP = _sibling("templates")
TemplateError = TP.TemplateError

#: Q-49-1: each LME question type's template, by the key its if-branch gets below; checked against the pin's code.
LME_TASKS = {"single-session-user": "base", "single-session-assistant": "base", "multi-session": "base",
             "temporal-reasoning": "temporal", "knowledge-update": "update", "single-session-preference": "preference"}
PINS = {"lme": "lme_evaluate_qa", "locomo_j": "locomo_j_prompt", "beam": "beam_prompts", "ama": "ama_evaluation_metrics"}


def _read_pin(pin: str, pins_root: Path) -> bytes:
    path, pin_sha = TP._pinned(pin, pins_root)
    raw = Path(path).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != pin_sha:
        raise TemplateError(f"{pin}: the pinned file is {got}, not the pin {pin_sha}")
    return raw


def _template_of(body: list) -> str:
    for node in body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "template" for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    raise TemplateError("an LME branch assigns no string constant to template")


def _tasks_of(test: ast.AST) -> list[str]:
    if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "task"
            and len(test.ops) == 1 and len(test.comparators) == 1):
        op, comp = test.ops[0], test.comparators[0]
        if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) and isinstance(comp.value, str):
            return [comp.value]
        if isinstance(op, ast.In) and isinstance(comp, (ast.List, ast.Tuple)) and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in comp.elts):
            return [e.value for e in comp.elts]
    raise TemplateError(f"an LME condition is not task == '...' or task in [...]: {ast.unparse(test)[:80]}")


def lme_from_source(source: bytes) -> tuple[dict, str]:
    """({question type: template text}, the abstention template) as get_anscheck_prompt's if-chain gives them."""
    tree = ast.parse(source.decode("utf-8"))
    fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "get_anscheck_prompt"]
    if len(fns) != 1:
        raise TemplateError(f"{len(fns)} get_anscheck_prompt functions in the pinned file, not one")
    top = next((n for n in fns[0].body if isinstance(n, ast.If)), None)
    if not (top is not None and isinstance(top.test, ast.UnaryOp) and isinstance(top.test.op, ast.Not)
            and isinstance(top.test.operand, ast.Name) and top.test.operand.id == "abstention"):
        raise TemplateError("get_anscheck_prompt does not open with `if not abstention:`")
    abstention = _template_of(top.orelse)
    chain = next((n for n in top.body if isinstance(n, ast.If)), None)
    mapping: dict[str, str] = {}
    while chain is not None:
        text = _template_of(chain.body)
        for task in _tasks_of(chain.test):
            if task in mapping:
                raise TemplateError(f"the LME task {task!r} appears in two branches")
            mapping[task] = text
        nxt = chain.orelse
        chain = nxt[0] if len(nxt) == 1 and isinstance(nxt[0], ast.If) else None
        if chain is None and not (len(nxt) == 1 and isinstance(nxt[0], ast.Raise)):
            raise TemplateError("the LME if-chain does not end in a raise for an unknown task")
    return mapping, abstention


def lme_templates(source: bytes) -> dict:
    """{key: template} for the committed LME_TASKS keys plus "abstention" - the pin's mapping must BE the table."""
    mapping, abstention = lme_from_source(source)
    if set(mapping) != set(LME_TASKS):
        raise TemplateError(f"the pinned code maps the LME tasks {sorted(mapping)}, the table {sorted(LME_TASKS)}")
    by_key: dict[str, str] = {}
    for task, key in LME_TASKS.items():
        if by_key.setdefault(key, mapping[task]) != mapping[task]:
            raise TemplateError(f"the table gives {task!r} the key {key!r}, but its pinned template differs")
    if len(set(by_key.values())) != len(by_key):
        raise TemplateError("two table keys share one pinned template - the table is finer than the code")
    for key, text in {**by_key, "abstention": abstention}.items():
        if TP.slots_of(text) != ("", "", ""):
            raise TemplateError(f"the LME {key} template does not take (question, answer, response)")
    return {**by_key, "abstention": abstention}


def lme_prompt(templates: Mapping[str, str], task: str, question: str, answer: str, response: str,
               abstention: bool) -> str:
    if abstention:
        return templates["abstention"].format(question, answer, response)
    if task not in LME_TASKS:
        raise TemplateError(f"the LME question type {task!r} has no judge template (the official NotImplementedError)")
    return templates[LME_TASKS[task]].format(question, answer, response)


def locomo_j_template(source: bytes) -> str:
    text = TP.extract(source, TP.Source(PINS["locomo_j"], "ACCURACY_PROMPT"))
    if TP.slots_of(text) != ("question", "gold_answer", "generated_answer"):
        raise TemplateError(f"ACCURACY_PROMPT's slots are {TP.slots_of(text)}")
    return text


def locomo_j_prompt(template: str, question: str, gold_answer: str, generated_answer: str) -> str:
    return template.format(question=question, gold_answer=gold_answer, generated_answer=generated_answer)


def beam_template(source: bytes) -> str:
    text = TP.extract(source, TP.Source(PINS["beam"], "unified_llm_judge_base_prompt"))
    for ph in ("<rubric_item>", "<llm_response>"):
        if ph not in text:
            raise TemplateError(f"the BEAM rubric prompt has no {ph}")
    return text


def beam_prompt(template: str, rubric_item: str, llm_response: str) -> str:
    """compute_metrics.py's own chain: every <rubric_item>, then every <llm_response> (in what the first left)."""
    return template.replace("<rubric_item>", rubric_item).replace("<llm_response>", llm_response)


def ama_parts(source: bytes) -> tuple:
    """compute_llm_as_judge's judge_prompt f-string as parts: str literals and ("slot", name) - rebuilt from ast."""
    tree = ast.parse(source.decode("utf-8"))
    fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "compute_llm_as_judge"]
    if len(fns) != 1:
        raise TemplateError(f"{len(fns)} compute_llm_as_judge functions in the pinned file, not one")
    found = [n.value for n in ast.walk(fns[0]) if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "judge_prompt" for t in n.targets)]
    if len(found) != 1 or not isinstance(found[0], ast.JoinedStr):
        raise TemplateError("compute_llm_as_judge assigns judge_prompt not exactly once as an f-string")
    parts: list = []
    for v in found[0].values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            parts.append(v.value)
        elif (isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) and v.conversion == -1
              and v.format_spec is None):
            parts.append(("slot", v.value.id))
        else:
            raise TemplateError("the AMA judge prompt has a slot that is not a plain name")
    names = [p[1] for p in parts if isinstance(p, tuple)]
    if sorted(names) != sorted(["context_str", "question", "golden_answer", "predicted_answer"]):
        raise TemplateError(f"the AMA judge prompt's slots are {names}")
    return tuple(parts)


def ama_prompt(parts: tuple, *, question: str, golden_answer: str, predicted_answer: str, task_type: str = "",
               episode_id: str = "", task_description: str = "") -> str:
    """The prompt exactly as compute_llm_as_judge builds it (context parts only when given, joined by LF)."""
    ctx = []
    if task_type:
        ctx.append(f"Task Type: {task_type}")
    if episode_id:
        ctx.append(f"Episode ID: {episode_id}")
    if task_description:
        ctx.append(f"Task Context: {task_description}")
    values = {"context_str": "\n".join(ctx) if ctx else "", "question": question, "golden_answer": golden_answer,
              "predicted_answer": predicted_answer}
    return "".join(p if isinstance(p, str) else str(values[p[1]]) for p in parts)


def load_all(pins_root: Path) -> dict:
    """Every judge prompt family from its pin (each pin's sha256 checked)."""
    return {"lme": lme_templates(_read_pin(PINS["lme"], pins_root)),
            "locomo_j": locomo_j_template(_read_pin(PINS["locomo_j"], pins_root)),
            "beam": beam_template(_read_pin(PINS["beam"], pins_root)),
            "ama": ama_parts(_read_pin(PINS["ama"], pins_root))}
