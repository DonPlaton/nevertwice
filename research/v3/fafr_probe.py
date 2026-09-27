#!/usr/bin/env python3
"""PREREG-V3 TB4.12 (A6): the per-prompt FA/FR probe (rev1 §8.5; the auditor's Q22 and F-1..F-3,
.loop/FAFR-SPEC-2026-09-27.md).

* the templated classes are code with a specification table (Q22); each answer is built deterministically from
  (question, gold, distractor), where the distractor is ANOTHER source question's gold, chosen by the seed;
* the labels (F-2', the auditor): every error class rev1 §8.5 names is WRONG for every family; the only way out is a
  family's pinned prompt naming the form correct in so many words (EXCLUDED, with its quote from POLICY) - those items
  leave that family's FA pool and are counted; the earlier literal-policy table (LABELS_LITERAL) gives a sensitivity
  FA published beside, never in the rule; frozen by sha256 before any run;
* the judge sees no context, so the templated FA is computed once per judged prompt (F-3: per-arm copies would give the
  judge identical inputs); the per-arm path is the reader path (A), descriptive only;
* path (A), labelled by the ANSWER (F-1): a distractor context whose short answer does not hold the gold -> an
  "accept" is FA; a gold context whose short answer is the gold (EM 1) -> a "reject" is FR; a distractor context whose
  answer names the gold anyway, and a gold context with EM 0, are excluded, and their count published;
* the rule (§8.5): pooled templated FA > 30 % -> the prompt gives no verdicts; free-form items (hermes3-8b) never
  enter it; items unfrozen or drawn from a scored list refuse.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

FAMILIES = ("lme", "locomo_j", "beam", "ama")
N_PER_PROMPT = 200
FA_MAX = 0.30

#: F-2: each family's own policy, as its pinned prompt words it (quoted in .loop/FAFR-SPEC-2026-09-27.md at the pin).
POLICY = {
    "lme": "answer yes if the response contains the correct answer ... If the response only contains a subset of the "
           "information required by the answer, answer no",
    "locomo_j": "be generous with your grading - as long as it touches on the same topic as the gold answer, it should "
                "be counted as CORRECT ... relative time references ... be generous",
    "ama": "determine if the predicted answer is correct based on: 1. Factual correctness ... 2. Completeness ... "
           "3. Relevance",
    "beam": "the rubric item is judged by meaning; paraphrases accepted; the response must satisfy the rubric item",
}


def _near_miss(gold: str) -> str:
    """A deterministic near miss: the first digit shifted by one, else the longest word's last letter moved on."""
    m = re.search(r"\d", gold)
    if m:
        d = str((int(m.group()) + 1) % 10)
        return gold[:m.start()] + d + gold[m.end():]
    words = re.findall(r"[A-Za-z]+", gold)
    if not words:
        return gold + "x"
    w = max(words, key=len)
    last = w[-1]
    repl = "a" if last.lower() == "z" else chr(ord(last) + 1)
    return gold.replace(w, w[:-1] + repl, 1)


_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}\s+(January|February|March|April|May|June|July|August|September|"
                   r"October|November|December)\s+\d{4}|(January|February|March|April|May|June|July|August|September|"
                   r"October|November|December)\s+\d{1,2},?\s+\d{4})\b", re.I)


@dataclass(frozen=True)
class Klass:
    name: str
    build: Callable[[str, str, str], str | None]      # (question, gold, distractor) -> answer, or None if n/a
    doc: str


CLASSES = (
    Klass("gold-plain", lambda q, g, d: g, "the gold, verbatim"),
    Klass("gold-verbose", lambda q, g, d: f"Based on the conversation, the answer is {g}.", "the gold in a sentence"),
    Klass("enumeration-with-gold", lambda q, g, d: f"It is one of {d}, {g} or {_near_miss(d)}.",
          "an enumeration that contains the gold among wrong options"),
    Klass("old-plus-new", lambda q, g, d: f"It was {g}, but later changed to {d}.",
          "the gold named as the OLD value, the distractor asserted as current"),
    Klass("hedge-gold", lambda q, g, d: f"I am not sure; possibly {g}.", "the gold under a hedge"),
    Klass("relative-date", lambda q, g, d: "last Tuesday" if _DATE.search(g) else None,
          "a relative date instead of a dated gold (only for dated golds)"),
    Klass("near-miss-entity", lambda q, g, d: _near_miss(g), "the gold with one deterministic near-miss change"),
    Klass("distractor-plain", lambda q, g, d: d, "another question's gold"),
)
ERROR_CLASSES = ("enumeration-with-gold", "old-plus-new", "hedge-gold", "relative-date", "near-miss-entity",
                 "distractor-plain")
#: F-2' (the auditor): rev1 §8.5 names these classes ERRORS, so each is WRONG for every family; the gold classes are
#: right.
LABELS = {(k.name, f): k.name not in ERROR_CLASSES for k in CLASSES for f in FAMILIES}
#: F-2': the ONLY way out - a family's pinned prompt names the form correct in so many words (the quote, from POLICY).
#: Such items are EXCLUDED from that family's FA pool (never relabelled right), and counted.
EXCLUDED = {("relative-date", "locomo_j"): "relative time references"}
#: The earlier literal-policy reading, kept as a sensitivity table: its FA is published beside, never in the 30 % rule.
LABELS_LITERAL = dict(LABELS) | {
    ("enumeration-with-gold", "lme"): True, ("enumeration-with-gold", "locomo_j"): True,
    ("old-plus-new", "locomo_j"): True, ("hedge-gold", "lme"): True, ("hedge-gold", "locomo_j"): True,
    ("hedge-gold", "ama"): True, ("hedge-gold", "beam"): True, ("relative-date", "locomo_j"): True}


class ProbeError(ValueError):
    """An item set or a rule input the preregistration does not allow."""


def check_sources(sources: Iterable[Mapping[str, Any]], scored_ids: Iterable[str]) -> None:
    """The probe's questions lie outside every scored list (§8.5)."""
    clash = sorted({s["id"] for s in sources} & set(scored_ids))
    if clash:
        raise ProbeError(f"probe questions in a scored list: {clash[:5]}")


def templated_items(family: str, sources: Sequence[Mapping[str, Any]], *, seed: int, n: int = N_PER_PROMPT) -> list[dict]:
    """n templated items for one judged prompt family: every applicable (source, class) pair, drawn by the seed,
    stratified by class (as even as the applicable pairs allow). Each source: {id, question, gold}."""
    if family not in FAMILIES:
        raise ProbeError(f"unknown judge family {family!r}")
    if len(sources) < 2:
        raise ProbeError("a distractor needs at least two source questions")
    rng = random.Random(f"{seed}:{family}")
    by_class: dict[str, list] = {k.name: [] for k in CLASSES}
    for i, s in enumerate(sources):
        others = [k for k in range(len(sources)) if k != i and _norm(sources[k]["gold"]) != _norm(s["gold"])]
        if not others:
            raise ProbeError(f"source {s['id']}: every other source's gold is its own - no distractor")
        d = sources[others[rng.randrange(len(others))]]["gold"]   # another source's gold, never one equal to its own
        for k in CLASSES:
            ans = k.build(s["question"], s["gold"], d)
            if ans is not None:
                by_class[k.name].append({"family": family, "source": s["id"], "class": k.name, "question": s["question"],
                                         "gold": s["gold"], "answer": ans, "label": LABELS[(k.name, family)],
                                         "label_literal": LABELS_LITERAL[(k.name, family)],
                                         "excluded": (k.name, family) in EXCLUDED, "free_form": False})
    for pool in by_class.values():
        rng.shuffle(pool)
    out: list[dict] = []
    while len(out) < n and any(by_class.values()):
        for name in [k.name for k in CLASSES]:
            if by_class[name] and len(out) < n:
                out.append(by_class[name].pop())
    if len(out) < n:
        raise ProbeError(f"{family}: only {len(out)} applicable items, not {n}")
    return out


def freeze(items: Sequence[Mapping[str, Any]]) -> str:
    """The items' sha256 over their canonical JSON (the texts stay in the runs tree; the sha goes to FREEZE-V3)."""
    raw = json.dumps(list(items), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def label_reader(context_kind: str, short_answer: str, gold: str, em: float) -> str:
    """F-1: path (A) by the ANSWER - "fa-candidate", "fr-candidate" or "excluded"."""
    if context_kind not in ("gold", "distractor"):
        raise ProbeError(f"unknown context kind {context_kind!r}")
    holds_gold = em >= 1.0 or _norm(gold) in _norm(short_answer)
    if context_kind == "distractor":
        return "excluded" if holds_gold else "fa-candidate"
    return "fr-candidate" if em >= 1.0 else "excluded"


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (s or "").lower()).split())


def rule(items: Sequence[Mapping[str, Any]], accepted: Mapping[int, bool], *, frozen_sha: str) -> dict:
    """§8.5 over ONE judged prompt: pooled templated FA and FR; > 30 % FA -> no verdicts. accepted[i] is the judge's
    verdict on items[i] (True = judged correct), a bool for EVERY templated item (B-FA1: a missing or invalid verdict
    is never read as a reject - the judge's invalid verdicts are re-asked and resolved before the rule). Free-form
    items never enter; unfrozen items refuse."""
    if freeze(items) != frozen_sha:
        raise ProbeError("the items are not the frozen set (their sha256 differs)")
    templ = [(i, it) for i, it in enumerate(items) if not it.get("free_form")]
    missing = [i for i, _ in templ if i not in accepted]
    if missing:
        raise ProbeError(f"verdicts missing for {len(missing)} of {len(templ)} templated items")
    bad = [i for i, _ in templ if not isinstance(accepted[i], bool)]
    if bad:
        raise ProbeError(f"the verdict of {len(bad)} templated item(s) is not a bool (first: item {bad[0]}: "
                         f"{accepted[bad[0]]!r})")
    pool = [(i, it) for i, it in templ if not it.get("excluded")]
    wrong = [i for i, it in pool if it["label"] is False]
    right = [i for i, it in pool if it["label"] is True]
    fa = sum(1 for i in wrong if accepted.get(i)) / len(wrong) if wrong else 0.0
    fr = sum(1 for i in right if accepted.get(i) is False) / len(right) if right else 0.0
    lit_wrong = [i for i, it in templ if it.get("label_literal") is False]
    fa_literal = sum(1 for i in lit_wrong if accepted.get(i)) / len(lit_wrong) if lit_wrong else 0.0
    return {"fa": fa, "fr": fr, "wrong_items": len(wrong), "right_items": len(right),
            "verdicts_allowed": fa <= FA_MAX, "free_form_items_excluded": len(items) - len(templ),
            "policy_excluded": len(templ) - len(pool),
            "sensitivity": {"fa_literal_policy": fa_literal, "in_rule": False}}
