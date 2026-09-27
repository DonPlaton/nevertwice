#!/usr/bin/env python3
"""PREREG-V3 TB4.9a (A6): the stand reader and the judges' mechanics (rev1 §8.1, §8.3, §8.4; the auditor's Q-49).

Transport is injected (``post(request) -> response``): the reader's is the arm's reader port on the proxy
(OpenAI-shaped chat completions), the judges' is the local Ollama chat API. Nothing here opens a socket.

* the reader: deepseek-flash, temperature 0, max_tokens 1,024, "thinking": {"type": "disabled"}; the SHORT ANSWER
  line is the LAST line that reads "SHORT ANSWER: ..." (LF-terminated lines only; case and markdown stars ignored);
  missing, the reader is asked once more in a second turn - its own reply, then our instruction (Q-49-5) - and still
  missing, the answer is empty and a reader_format_failure; over 15 words it is kept and counted reader_overlong; a
  long-form ability needs no line (§8.4);
* a judge (J1, J2): the Ollama chat API with think:false, temperature 0 and the prompt's output cap as num_predict;
  a verdict that carries thinking, is cut at its cap (done_reason "length") or does not parse strictly is re-asked
  ONCE with the identical request (Q-49-6); still so, it is invalid, with its reason;
* the strict parses (§8.4) and, beside each, the official lenient one;
* the twin: SQuAD EM and token F1; tau_b (Kendall's tau-b with ties) and BEAM's event-ordering score
  f1 x (tau_b + 1) / 2 over canonicalised lists (the canonicalisation is Q-49-3's alignment, applied before);
* J2: the first of the ordered list whose CL5 contract passes; none - "judge-dependent (no J2)".
"""
from __future__ import annotations

import json
import re
import string
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

READER_MODEL = "deepseek-flash"
READER_PARAMS = {"temperature": 0, "max_tokens": 1024, "thinking": {"type": "disabled"}}
SHORT_ANSWER_INSTRUCTION = "Finish your reply with one line: SHORT ANSWER: <at most 15 words>"
SHORT_ANSWER_MAX_WORDS = 15
_SA = re.compile(r"^\s*\**\s*short answer\s*\**\s*:\s*\**\s*(.*?)\s*\**\s*$", re.I)
#: rev1 §8.3: the judges on disk, with the digests FREEZE-V3 records; J2's candidates in their order.
J1 = ("qwen3.6:27b", "a50eda8ed977")
J2_ORDER = (("glm-4.7-flash:q4_K_M", "d1a8a26252f1"), ("gemma3n:e4b", "15cb39fd9394"), ("hermes3-8b", None),
            ("llama3", None))
#: rev1 §8.3: output caps by prompt family (BEAM's is set from its official examples; AMA's = 2 by Q-49-4).
CAPS = {"lme": 10, "locomo_j": 64, "ama": 2}
#: CL5 (rev1 §8.3): the judge contract.
CL5 = {"max_invalid": 0.01, "max_flip": 0.10, "retest_n": 200}
NO_J2 = "judge-dependent (no J2)"


class ReaderJudgeError(ValueError):
    """A request or an answer that cannot be handled as the preregistration says."""


# -- the reader --

def short_answer(text: str) -> str | None:
    """The text of the LAST 'SHORT ANSWER:' line (a line ends at LF only), or None."""
    found = None
    for line in (text or "").split("\n"):
        m = _SA.match(line.rstrip("\r"))
        if m:
            found = m.group(1).strip()
    return found if found else None


def reader_request(prompt: str) -> dict:
    return {"model": READER_MODEL, "messages": [{"role": "user", "content": prompt}], **READER_PARAMS}


def reader_reask(prompt: str, first_reply: str) -> dict:
    """Q-49-5: the second turn - the reader's own first reply, then our SHORT ANSWER instruction."""
    return {"model": READER_MODEL, "messages": [{"role": "user", "content": prompt},
                                                {"role": "assistant", "content": first_reply},
                                                {"role": "user", "content": SHORT_ANSWER_INSTRUCTION}],
            **READER_PARAMS}


def _chat_text(resp: Mapping[str, Any]) -> tuple[str, bool, dict]:
    """(content, thinking seen, usage) of an OpenAI-shaped chat completion."""
    try:
        msg = resp["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise ReaderJudgeError("the reader's answer has no choices[0].message") from None
    content = msg.get("content") or ""
    thinking = bool(msg.get("reasoning_content"))
    return content, thinking, dict(resp.get("usage") or {})


@dataclass
class ReaderResult:
    text: str
    short_answer: str
    reasked: bool = False
    format_failure: bool = False
    overlong: bool = False
    thinking_seen: bool = False
    usage: dict = field(default_factory=dict)


def _add_usage(a: dict, b: dict) -> dict:
    return {k: int(a.get(k) or 0) + int(b.get(k) or 0) for k in set(a) | set(b)
            if isinstance(a.get(k, 0), int) and isinstance(b.get(k, 0), int)}


def read(prompt: str, post: Callable[[dict], Mapping[str, Any]], *, long_form: bool = False) -> ReaderResult:
    """One reader answer, with its one re-ask when the SHORT ANSWER line is missing."""
    content, thinking, usage = _chat_text(post(reader_request(prompt)))
    if long_form:
        return ReaderResult(text=content, short_answer="", thinking_seen=thinking, usage=usage)
    sa = short_answer(content)
    res = ReaderResult(text=content, short_answer=sa or "", thinking_seen=thinking, usage=usage)
    if sa is None:
        c2, t2, u2 = _chat_text(post(reader_reask(prompt, content)))
        res.reasked, res.thinking_seen, res.usage = True, thinking or t2, _add_usage(usage, u2)
        res.text = content + "\n" + c2
        sa = short_answer(c2)
        if sa is None:
            res.format_failure, res.short_answer = True, ""
            return res
        res.short_answer = sa
    res.overlong = len(res.short_answer.split()) > SHORT_ANSWER_MAX_WORDS
    return res


# -- the judges --

def judge_request(model: str, prompt: str, cap: int, *, fmt: str | None = None) -> dict:
    if not (isinstance(cap, int) and cap > 0):
        raise ReaderJudgeError(f"a judge's output cap is a positive int, not {cap!r}")
    req = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False, "think": False,
           "options": {"temperature": 0, "num_predict": cap}}
    if fmt is not None:
        req["format"] = fmt
    return req


@dataclass
class Verdict:
    value: Any                   # the strict parse, or None when invalid
    raw: str
    reasked: bool = False
    invalid: str | None = None   # the reason, when the verdict is invalid
    lenient: Any = None          # the official parse beside it
    thinking_seen: bool = False


def _judge_once(resp: Mapping[str, Any], parse: Callable[[str], Any]) -> tuple[Any, str, str | None, bool]:
    msg = resp.get("message") if isinstance(resp, Mapping) else None
    if not isinstance(msg, Mapping):
        return None, "", "no message in the judge's answer", False
    content = msg.get("content") or ""
    thinking = bool(msg.get("thinking"))          # think:false - the model's thinking field must stay empty
    if thinking:
        return None, content, "thinking output", True
    if resp.get("done_reason") == "length":
        return None, content, "cut at its cap", False
    value = parse(content)
    if value is None:
        return None, content, "unparsable", False
    return value, content, None, False


def judge(prompt: str, cap: int, parse: Callable[[str], Any], post: Callable[[dict], Mapping[str, Any]], *,
          model: str, fmt: str | None = None, lenient: Callable[[str], Any] | None = None) -> Verdict:
    """A verdict with its one identical re-ask (Q-49-6)."""
    req = judge_request(model, prompt, cap, fmt=fmt)
    value, raw, why, thinking = _judge_once(post(req), parse)
    reasked = False
    if why is not None:
        reasked = True
        value, raw, why, t2 = _judge_once(post(req), parse)
        thinking = thinking or t2
    return Verdict(value=value if why is None else None, raw=raw, reasked=reasked, invalid=why,
                   lenient=lenient(raw) if lenient else None, thinking_seen=thinking)


# -- the parses (§8.4), strict and, beside, the official lenient one --

def lme_strict(text: str) -> bool | None:
    m = re.match(r"^(yes|no)\b", (text or "").strip().lower())
    return None if m is None else m.group(1) == "yes"


def lme_official(text: str) -> bool:
    return "yes" in (text or "").lower()


def locomo_strict(text: str) -> bool | None:
    try:
        obj = json.loads((text or "").strip())
    except ValueError:
        return None
    label = obj.get("label") if isinstance(obj, dict) else None
    return {"CORRECT": True, "WRONG": False}.get(label) if isinstance(label, str) else None


def locomo_official(text: str) -> bool | None:
    """The first JSON object in the text (fenced or not), its label - the lenient reading beside the strict one."""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S) or re.search(r"(\{.*?\})", t, re.S)
    try:
        obj = json.loads(m.group(1)) if m else json.loads(t)
    except ValueError:
        return None
    return obj.get("label") == "CORRECT" if isinstance(obj, dict) else None


def beam_json(response: str):
    """BEAM's parse_json_response (compute_metrics.py @b2da22e), re-written (Q-49-3 O-a, never executed from the pin):
    a fenced block first, then the whole text, then the first non-greedy {...} or [...]; else ValueError."""
    r = (response or "").strip()
    if r.startswith("```"):
        m = re.search(r"```(?:json)?\s*(\[.*\]|\{.*\})\s*```", r, re.DOTALL)
        if m:
            r = m.group(1).strip()
    try:
        return json.loads(r)
    except json.JSONDecodeError:
        pass
    m = re.search(r"(\{.*?\}|\[.*?\])", r, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception as e:  # noqa: BLE001 - the official message
            raise ValueError(f"Found possible JSON but failed to parse it: {e}") from None
    raise ValueError("No valid JSON found in response.")


def beam_score(text: str) -> float | None:
    """A rubric item's score (0, 0.5 or 1), or None."""
    try:
        obj = beam_json(text)
    except ValueError:
        return None
    s = obj.get("score") if isinstance(obj, dict) else None
    return float(s) if isinstance(s, (int, float)) and not isinstance(s, bool) and float(s) in (0.0, 0.5, 1.0) else None


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def ama_strict(text: str) -> bool | None:
    t = _THINK.sub("", text or "").strip().lower()
    return {"yes": True, "no": False}.get(t)


def ama_official(text: str) -> bool | None:
    """AMA's own reading: the LAST yes/no as a whole word after the think tags go; None where it falls back to F1."""
    t = _THINK.sub("", text or "").strip().lower()
    y = [m.start() for m in re.finditer(r"\byes\b", t)]
    n = [m.start() for m in re.finditer(r"\bno\b", t)]
    ly, ln = (y[-1] if y else -1), (n[-1] if n else -1)
    return True if ly > ln else False if ln > ly else None


# -- the twin: SQuAD EM and token F1 --

def normalize_answer(s: str) -> str:
    s = (s or "").lower()
    s = "".join(ch for ch in s if ch not in set(string.punctuation))
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    return " ".join(s.split())


def twin(prediction: str, gold: str) -> tuple[float, float]:
    """(EM, token F1) as SQuAD's evaluation computes them."""
    p, g = normalize_answer(prediction).split(), normalize_answer(gold).split()
    em = float(p == g)
    common = Counter(p) & Counter(g)
    same = sum(common.values())
    if not p or not g:
        return em, float(p == g)
    if same == 0:
        return em, 0.0
    precision, recall = same / len(p), same / len(g)
    return em, 2 * precision * recall / (precision + recall)


# -- Kendall's tau-b and BEAM's event-ordering score --

def tau_b(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Kendall's tau-b with ties (as scipy.stats.kendalltau variant "b"); None when a side is constant."""
    if len(x) != len(y):
        raise ReaderJudgeError("tau_b takes two rankings of the same length")
    n = len(x)
    conc = disc = tx = ty = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx, dy = x[i] - x[j], y[i] - y[j]
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                tx += 1
            elif dy == 0:
                ty += 1
            elif (dx > 0) == (dy > 0):
                conc += 1
            else:
                disc += 1
    denom = ((conc + disc + tx) * (conc + disc + ty)) ** 0.5
    return None if denom == 0 else (conc - disc) / denom


def event_ordering_score(reference_canon: Sequence[str], system_canon: Sequence[str]) -> dict:
    """BEAM's score over lists already canonicalised by the alignment (Q-49-3): f1 of the item sets times
    (tau_b + 1) / 2 over the union, a missing item ranked last (tie_rank) - compute_metrics.py @b2da22e."""
    ref, sysl = list(reference_canon), list(system_canon)
    tp = len(set(ref) & set(sysl))
    fp = len([x for x in sysl if x not in ref])
    fn = len([x for x in ref if x not in sysl])
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    union = list(dict.fromkeys(ref + sysl))
    tie_rank = len(union) + 1

    def to_rank(seq):
        r = {item: i + 1 for i, item in enumerate(seq)}
        return [r.get(u, tie_rank) for u in union]

    t = tau_b(to_rank(ref), to_rank(sysl))
    tau_norm = (t + 1) / 2 if t is not None else 0
    return {"precision": precision, "recall": recall, "f1": f1, "tau_norm": tau_norm, "final_score": tau_norm * f1}


# -- J2 --

def cl5(invalid_rate: float, thinking_off_all: bool, flip_rate: float, retest_n: int) -> tuple[bool, list[str]]:
    """The judge contract: <= 1 % invalid after the re-ask, thinking off on every accepted verdict, a test-retest flip
    rate <= 10 % on 200 re-judged verdicts."""
    why = []
    if invalid_rate > CL5["max_invalid"]:
        why.append(f"invalid {invalid_rate:.3%} > 1 %")
    if not thinking_off_all:
        why.append("thinking on an accepted verdict")
    if retest_n < CL5["retest_n"]:
        why.append(f"test-retest on {retest_n} verdicts, not {CL5['retest_n']}")
    if flip_rate > CL5["max_flip"]:
        why.append(f"flip rate {flip_rate:.1%} > 10 %")
    return not why, why


def resolve_j2(contract_results: Mapping[str, Mapping[str, Any]]) -> tuple[str, dict]:
    """J2 = the first model of J2_ORDER whose CL5 passes; (NO_J2, reasons) when none does. A model with no result is
    not passed over silently: it fails with that reason."""
    reasons = {}
    for model, _digest in J2_ORDER:
        r = contract_results.get(model)
        if r is None:
            reasons[model] = ["no contract result"]
            continue
        ok, why = cl5(r["invalid_rate"], r["thinking_off_all"], r["flip_rate"], r["retest_n"])
        if ok:
            return model, reasons
        reasons[model] = why
    return NO_J2, reasons
