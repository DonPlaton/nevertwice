#!/usr/bin/env python3
"""PREREG-V3 TB4.8b (A6): the stand reader's templates, built at run time from pinned files (rev1 §8.1; the auditor's
Q17: "templates are built at launch from the pinned file plus a committed transform, their sha in FREEZE-V3").

The mechanics, for every stand (the per-stand sources and transforms follow the auditor's Q-48 rulings):

* extract: the pinned file is checked against its pin (sha256), parsed with ast - never imported or executed - and the
  ONE string constant assigned to a named variable (optionally at a key path inside a dict literal) that carries a
  marker and none of the excluded substrings is the base text; zero or several candidates refuse by name. An f-string
  is never a candidate (its text is not in the file as written); adjacent literals are one constant, as Python reads
  them.
* transform: a committed list of exact (old, new) replacements, each applied to exactly one occurrence - a
  replacement that matches nothing or more than once refuses, so a changed pin cannot be transformed silently.
* FREEZE-V3 (committed) gets the pin, our replacements, the slots and the template's sha256 - never the text, which
  quotes the pinned file (Q-A3-6); the full texts go to a runs-tree file whose sha256 the fragment records.
* render: the template's slots ("{}" positional or "{name}") are filled in order by the stand, each value inserted
  verbatim - a context block full of braces is never parsed as a format string; the slots must be exactly the ones the
  stand declares.
"""
from __future__ import annotations

import ast
import hashlib
import json
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class TemplateError(ValueError):
    """A template that cannot be built or filled exactly as declared."""


@dataclass(frozen=True)
class Source:
    """Where a template's base text is in its pinned file: the string assigned to ``assign`` (at ``path`` in a dict
    literal), or - ``assign`` None - any string constant of the file; filtered by ``marker`` and ``exclude``."""
    pin: str
    assign: str | None
    path: tuple = ()
    marker: str | None = None
    exclude: tuple = ()


@dataclass(frozen=True)
class Template:
    stand: str
    text: str
    sha256: str
    source_pin: str
    source_sha256: str
    slots: tuple


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _at_path(node: ast.AST, path: Sequence[str]) -> ast.AST | None:
    for key in path:
        if not isinstance(node, ast.Dict):
            return None
        hits = [v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and k.value == key]
        if len(hits) != 1:
            return None
        node = hits[0]
    return node


def extract(source: bytes, spec: Source) -> str:
    """The one string constant the spec names in a pinned Python file (parsed, never run)."""
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError) as e:
        raise TemplateError(f"{spec.pin}: the pinned file does not parse ({type(e).__name__})") from None
    found: list[str] = []
    for node in ast.walk(tree):
        if spec.assign is None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.append(node.value)
            continue
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == spec.assign for t in targets):
            continue
        v = _at_path(value, spec.path)
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            found.append(v.value)
    if spec.marker is not None:
        found = [s for s in found if spec.marker in s]
    found = [s for s in found if not any(x in s for x in spec.exclude)]
    if len(found) != 1:
        raise TemplateError(f"{spec.pin}: {len(found)} string constants assigned to {spec.assign or '(any name)'}"
                            f"{list(spec.path) or ''} match the marker and exclusions - exactly one is required")
    return found[0]


def transform(text: str, replacements: Sequence[tuple[str, str]], *, stand: str = "") -> str:
    """Each (old, new) applied in order to exactly one occurrence."""
    for i, (old, new) in enumerate(replacements):
        n = text.count(old)
        if n != 1:
            raise TemplateError(f"{stand}: transform {i} matches {n} times, not exactly once: {old[:60]!r}")
        text = text.replace(old, new)
    return text


def slots_of(text: str) -> tuple:
    """The template's replacement fields in order ("" for a positional one); a conversion or a format spec refuses."""
    out = []
    try:
        for _literal, field, spec, conv in string.Formatter().parse(text):
            if field is None:
                continue
            if spec or conv:
                raise TemplateError(f"a slot with a format spec or conversion: {{{field}!{conv}:{spec}}}")
            out.append(field)
    except ValueError as e:
        raise TemplateError(f"the template's braces do not parse: {e}") from None
    return tuple(out)


def build(stand: str, pinned: Path, pin_sha256: str, spec: Source, replacements: Sequence[tuple[str, str]],
          slots: Sequence[str], base_of=None) -> Template:
    """The stand's template from its pinned file: the pin checked, the base extracted, the transform applied, the slots
    required to be exactly the declared ones."""
    raw = Path(pinned).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != pin_sha256:
        raise TemplateError(f"{stand}: the pinned file {Path(pinned).name} is {got}, not the pin {pin_sha256}")
    text = transform(base_of(raw) if base_of is not None else extract(raw, spec), replacements, stand=stand)
    found = slots_of(text)
    if found != tuple(slots):
        raise TemplateError(f"{stand}: the template's slots are {list(found)}, not the declared {list(slots)}")
    return Template(stand=stand, text=text, sha256=sha256_text(text), source_pin=spec.pin, source_sha256=got,
                    slots=found)


def render(template: Template, values: Sequence[str] | Mapping[str, str]) -> str:
    """The template with each slot filled, in order, by its value inserted verbatim."""
    if isinstance(values, Mapping):
        if set(values) != set(template.slots) or "" in template.slots:
            raise TemplateError(f"{template.stand}: values for {sorted(values)}, slots {list(template.slots)}")
        ordered = [values[s] for s in template.slots]
    else:
        if len(values) != len(template.slots) or any(template.slots):
            raise TemplateError(f"{template.stand}: {len(values)} values for the slots {list(template.slots)}")
        ordered = list(values)
    parts = []
    k = 0
    for literal, field, _spec, _conv in string.Formatter().parse(template.text):
        parts.append(literal)
        if field is not None:
            v = ordered[k]
            if not isinstance(v, str):
                raise TemplateError(f"{template.stand}: slot {k} takes text, got {type(v).__name__}")
            parts.append(v)
            k += 1
    return "".join(parts)


# -- the stands (the auditor's Q-48 rulings) --

HERE = Path(__file__).resolve().parent
#: rev1 §8.1: every short-answer template ends with this instruction.
SHORT_ANSWER = "Finish your reply with one line: SHORT ANSWER: <at most 15 words>"
#: rev1 §8.1 / Q-48-2: LoCoMo's own cat-5 instruction - in the pinned gpt_utils.py it is the comment line 49; it must
#: occur there verbatim, so the text is the benchmark's, not ours.
LOCOMO_NO_INFO = "If no information is available to answer the question, write 'No information available'."
#: Q-48-2 O-a2: the arm's block replaces the whole history block (CONV_START_PROMPT and the conversation), then the
#: benchmark's own "\n\n" + QA_PROMPT; "Short answer:" becomes the SHORT ANSWER line.
_LOCOMO_BASE = (("\nBased on the above context", "{context}\n\n\nBased on the above context"),)


#: Q-48-4 (Q-TPL-2 = O-a): BEAM's own RAG answer prompt; its "<context>" and "<question>" markers become the slots.
_BEAM_SLOTS = (("<context>", "{context}"), ("<question>", "{question}"))
#: Q-48-3 (Q-TPL-3 = O-a): the MAB driver joins the context block, a newline and its query (agent.py:317); the query
#: template's last "Answer:" gets the SHORT ANSWER line before it (its example has an "Answer:" of its own).
_MAB_QUERY = ("factconsolidation", "query", "rag_agent")
_MAB_BASE = (("Pretend you are a knowledge management system.", "{context}\nPretend you are a knowledge management system."),
             ("{question} \nAnswer:", "{question} \n" + SHORT_ANSWER + "\nAnswer:"))
MAB_NOTE = ("the MAB driver sends its SYSTEM_MESSAGE (a name in the pinned file, not a constant) as a system turn; the "
            "stand's reader sends no system message (Q-TPL-3)")


#: Q-48-1, Q-TPL-1 (the auditor, from the pins): LME's documented command runs run_generation.sh, whose default
#: READING_METHOD "con" passes --cot true (run_generation.sh:14, :64-65); con stays false, merge none - so the source
#: is prepare_prompt's cot template (run_generation.py:55). Our one insertion before "Answer (step by step):" allows
#: "unanswerable" (rev1 8.1) and asks the SHORT ANSWER line.
LME_UNANSWERABLE = "If the chat history does not hold the answer, say that the question is unanswerable."
LME_EVIDENCE = (("lme_run_generation_sh", 'reading_method=${7:-"con"}\n'),
                ("lme_run_generation_sh", 'elif [[ $reading_method == "con" ]]; then\n    reading_flags="--cot true"\n'))
LME_NOTE = ("--cot true: README's documented command runs run_generation.sh, whose default READING_METHOD 'con' passes "
            "--cot true (run_generation.sh:14, :64-65); con false, merge none (run_generation.py:55). The vendor's "
            "gen_length with cot is 800 (run_generation.py:342 - '500 if not args.cot else 800' tests a non-empty string, "
            "so it is always 800); the reader's max_tokens, 1024 (rev1 8.1), is at least that, the same for every arm.")
#: Q-TPL-4 = O-d (the auditor, from the pins): no pinned code uses AMA's ANSWER_WITH/WITHOUT_RETRIEVAL_PROMPT_TEMPLATE;
#: the vendor asks its external memory methods (bm25, embedding_mem, longcontext) with longcontext.py's list-mode prompt
#: (181-217). One question per call (8), so n = 1: {context}, then the non-MCQ suffix, its answer slot turned into the
#: SHORT ANSWER line. The layout is checked against the pinned f-strings (the parts, in order); any difference refuses.
_AMA_INTRO = Source("ama_method_longcontext", "section_intro", marker="provide a direct and concise answer")
_AMA_INSTR = Source("ama_method_longcontext", "instructions", marker="Please provide answers in the following format")
_AMA_SUFFIX = (("s", "\n\n## Questions\n"), ("v", "section_intro"), ("s", "\n\n"), ("v", "questions_block"),
               ("s", "\n## Instructions\n"), ("v", "instructions"), ("s", "\n\n"), ("v", "answer_slots"))
_AMA_QLINE = (("s", "Question "), ("v", "i"), ("s", ": "), ("v", "q"), ("s", "\n"))
_AMA_SLOT = (("s", "Answer["), ("v", "i"), ("s", "]: [your answer here]"))
AMA_NOTE = ("AMA's ANSWER_WITH_RETRIEVAL_PROMPT_TEMPLATE and ANSWER_WITHOUT_RETRIEVAL_PROMPT_TEMPLATE are used by no "
            "pinned code; the vendor asks its external memory methods with longcontext.py's list-mode prompt (181-217). "
            "One question per call (8), so n = 1; its 'Answer[1]: [your answer here]' slot is the SHORT ANSWER line.")


def _joined_parts(node: ast.JoinedStr) -> tuple | None:
    """An f-string's parts in order: ("s", its text) for a literal piece, ("v", a name) for a plain {name}; None when a
    piece is anything else (a call, a conversion, a format spec)."""
    parts = []
    for v in node.values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            parts.append(("s", v.value))
        elif (isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) and v.conversion == -1
              and v.format_spec is None):
            parts.append(("v", v.value.id))
        else:
            return None
    return tuple(parts)


def _fill_parts(parts: tuple, values: Mapping[str, str]) -> str:
    return "".join(p if k == "s" else values[p] for k, p in parts)


def ama_list_base(raw: bytes) -> str:
    """S7's base from the pinned longcontext.py: {context}, then the non-MCQ list-mode suffix for one question."""
    try:
        tree = ast.parse(raw.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError) as e:
        raise TemplateError(f"S7: the pinned longcontext.py does not parse ({type(e).__name__})") from None
    for label, want in (("the suffix", _AMA_SUFFIX), ("a question line", _AMA_QLINE), ("an answer slot", _AMA_SLOT)):
        n = sum(1 for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) and _joined_parts(node) == want)
        if n != 1:
            raise TemplateError(f"S7: {label}'s layout occurs {n} times in the pinned longcontext.py - exactly once is the "
                                "vendor's prompt as read")
    intro, instr = extract(raw, _AMA_INTRO), extract(raw, _AMA_INSTR)
    qline = _fill_parts(_AMA_QLINE, {"i": "1", "q": "{question}"})
    slot = _fill_parts(_AMA_SLOT, {"i": "1"})
    return "{context}" + _fill_parts(_AMA_SUFFIX, {"section_intro": intro, "questions_block": qline,
                                                   "instructions": instr, "answer_slots": slot})


@dataclass(frozen=True)
class StandSpec:
    source: Source
    replacements: tuple
    slots: tuple
    must_occur: tuple = ()           # committed text that must occur verbatim in the pinned file (its provenance)
    same_as: Source | None = None    # another constant of the same file whose text must be the source's (S6L = S6)
    note: str = ""                   # what FREEZE-V3 names beside the template (a difference from the vendor's run)
    evidence: tuple = ()             # (pin, text): text that must occur verbatim in another pinned file (the choice's basis)
    compose: str | None = None       # a composer of the base text from the pinned file, instead of one constant


STANDS = {
    "S4": StandSpec(Source("locomo_answer_prompt", "QA_PROMPT"),
                    _LOCOMO_BASE + (("Question: {} Short answer:", "Question: {question}\n" + SHORT_ANSWER),),
                    ("context", "question")),
    "S4-cat5": StandSpec(Source("locomo_answer_prompt", "QA_PROMPT"),
                         _LOCOMO_BASE + (("Question: {} Short answer:",
                                          "Question: {question}\n" + LOCOMO_NO_INFO + "\n" + SHORT_ANSWER),),
                         ("context", "question"), must_occur=(LOCOMO_NO_INFO,)),
    "S5": StandSpec(Source("beam_prompts", "answer_generation_for_rag"),
                    _BEAM_SLOTS + (("\n\nRESPONSE:\n", "\n" + SHORT_ANSWER + "\n\nRESPONSE:\n"),), ("context", "question")),
    #: rev1 §8.4: long form and event ordering are judged or scored on the full answer - no SHORT ANSWER line
    "S5-full": StandSpec(Source("beam_prompts", "answer_generation_for_rag"), _BEAM_SLOTS, ("context", "question")),
    "S6": StandSpec(Source("mab_templates", "BASE_TEMPLATES", _MAB_QUERY), _MAB_BASE, ("context", "question"),
                    note=MAB_NOTE),
    "S6L": StandSpec(Source("mab_templates", "BASE_TEMPLATES", ("factconsolidation", "query", "long_context_agent")),
                     _MAB_BASE, ("context", "question"), same_as=Source("mab_templates", "BASE_TEMPLATES", _MAB_QUERY),
                     note=MAB_NOTE),
    "S1": StandSpec(Source("lme_answer_prompt", "answer_prompt_template",
                           marker="relevant chat history. Answer the question step by step"),
                    (("\nAnswer (step by step):", "\n" + LME_UNANSWERABLE + "\n" + SHORT_ANSWER + "\nAnswer (step by step):"),),
                    ("", "", ""), note=LME_NOTE, evidence=LME_EVIDENCE),
    "S7": StandSpec(Source("ama_method_longcontext", None), (("Answer[1]: [your answer here]", SHORT_ANSWER),),
                    ("context", "question"), note=AMA_NOTE, compose="ama_list"),
}
#: S3, the oracle bracket, reads S1's template (Q-48-1).
STANDS["S3"] = STANDS["S1"]
COMPOSERS = {"ama_list": ama_list_base}
#: Stands whose template waits for a pin (the auditor's Q-48): named, never guessed.
PENDING: dict = {}
#: Text the stand appends to a LoCoMo cat-2 question: the pinned file's own constant, found by it.
LOCOMO_CAT2 = Source("locomo_answer_prompt", None, marker="Use DATE of CONVERSATION")


def _cp():
    import importlib.util  # noqa: PLC0415
    import sys  # noqa: PLC0415
    name = "v3_corpus_pin_for_templates"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE / "corpus_pin_v3.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


def _table(pin: str) -> dict:
    """The table that holds the pin: A3's PINS, or A7's PINS_A7 (a7-github-2's LME and AMA files)."""
    cp = _cp()
    return cp.PINS if pin in cp.PINS else cp.PINS_A7


def _pinned(pin: str, pins_root: Path) -> tuple[Path, str]:
    cp = _cp()
    table = _table(pin)
    p = table[pin]
    return cp.location(pin, hf_hub=Path(pins_root).parent / "hf_cache_unused", pins_root=pins_root,
                       pins=table), p["sha256"]


def stand_template(stand: str, *, pins_root: Path) -> Template:
    """The template of a stand - a function of the stand alone (Q-48-6: never of an arm or a bracket)."""
    if stand in PENDING:
        raise TemplateError(f"{stand}: no template yet - {PENDING[stand]}")
    if stand not in STANDS:
        raise TemplateError(f"{stand}: not a stand with a reader template")
    spec = STANDS[stand]
    path, pin_sha = _pinned(spec.source.pin, pins_root)
    tpl = build(stand, path, pin_sha, spec.source, spec.replacements, spec.slots,
                base_of=COMPOSERS[spec.compose] if spec.compose else None)
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8")
    for pin, s in spec.evidence:                         # the choice's basis, in its own pinned file
        epath, esha = _pinned(pin, pins_root)
        eraw = Path(epath).read_bytes()
        if hashlib.sha256(eraw).hexdigest() != esha:
            raise TemplateError(f"{stand}: the pinned file of {pin} is not the pin")
        if s not in eraw.decode("utf-8"):
            raise TemplateError(f"{stand}: the evidence {s[:50]!r} does not occur in the pinned file of {pin}")
    if spec.same_as is not None and extract(raw, spec.same_as) != extract(raw, spec.source):
        raise TemplateError(f"{stand}: its base text is not the one {'/'.join(spec.same_as.path)} gives - the two "
                            "stands would read different templates")
    for s in spec.must_occur:
        if s not in text:
            raise TemplateError(f"{stand}: the committed text {s[:50]!r} does not occur in the pinned file")
    return tpl


def _locomo_category(category) -> int | None:
    """B-CAT: the loader's own reading of a category (loaders.locomo_category) - "2" and 2 alike."""
    import importlib.util  # noqa: PLC0415
    import sys  # noqa: PLC0415
    name = "v3_loaders_for_templates"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE / "loaders.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    try:
        return sys.modules[name].locomo_category(category)
    except ValueError as e:
        raise TemplateError(f"S4: {e}") from None


def locomo_question(question: str, category: int | str | None, *, pins_root: Path) -> str:
    """A LoCoMo question as the benchmark asks it: cat 2 gets the pinned file's own DATE suffix; cat 5 is asked as is
    (Q-48-2: never as the pinned multiple choice, which names the gold answer). The category as the loader keeps it
    ("2") or as the file has it (2) - B-CAT; one that is no category number is refused by name."""
    if _locomo_category(category) == 2:
        path, pin_sha = _pinned(LOCOMO_CAT2.pin, pins_root)
        raw = Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != pin_sha:
            raise TemplateError("S4: the pinned file is not the pin")
        return question + extract(raw, LOCOMO_CAT2)
    return question


def freeze_fragment(*, pins_root: Path, texts_path: Path) -> dict:
    """FREEZE-V3's templates (the auditor on Q-48-6 and Q-A3-6). The COMMITTED fragment holds, per stand, the pin (its
    path and sha256), our committed replacements, the slots and the final template's sha256 - never the text, which
    quotes the pinned file. The full final texts go to ``texts_path`` in the runs tree (runs/v3/freeze_templates.json,
    never committed; the auditor reviews it there), and that file's sha256 goes into the fragment."""
    cp = _cp()
    committed, texts = {}, {}
    for stand in sorted(STANDS):
        spec = STANDS[stand]
        t = stand_template(stand, pins_root=pins_root)
        pin = _table(spec.source.pin)[spec.source.pin]
        committed[stand] = {"source_pin": t.source_pin, "source_path": f"{pin['repo']}@{pin['revision']}:{pin['path']}",
                            "source_sha256": t.source_sha256, "replacements": [list(r) for r in spec.replacements],
                            "must_occur": list(spec.must_occur), "slots": list(t.slots), "sha256": t.sha256}
        if spec.note:
            committed[stand]["note"] = spec.note
        texts[stand] = {"text": t.text, "sha256": t.sha256}
    raw = (json.dumps({"templates": texts}, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")
    Path(texts_path).parent.mkdir(parents=True, exist_ok=True)
    Path(texts_path).write_bytes(raw)
    return {"templates": committed, "pending": dict(sorted(PENDING.items())),
            "texts_file": {"path": str(texts_path), "sha256": hashlib.sha256(raw).hexdigest()}}
