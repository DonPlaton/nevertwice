#!/usr/bin/env python3
"""PREREG-V3 TB4.3a (A6): the stand loaders for LongMemEval (S1, S2, S3) and LoCoMo (S4), with their smoke units.

Pure functions over the parsed records (dataset field names as research/v3/dataset_facts.py found them), so the core
suite drives them with small synthetic fixtures; the pinned files are read only through read_pinned(), which verifies
each file against its pin first (corpus_pin_v3.verify) and reads nothing else.

* The run path never carries a gold answer: load_*() builds EvalUnits of sessions, items and questions; gold answers
  and gold evidence come only through the separate gold doors (lme_gold, locomo_gold), which the scorer and the oracle
  bracket use (R6).
* Units follow the committed nested order: `prefix` of them, in order - never a selection (§3.4). The S1 smoke units
  are positions 481-500 of the same order (smoke_rules.s1_tail), disjoint from every scored prefix <= 480 by
  construction; a scored load refuses an order whose prefix reaches into them.
* S4 smoke (§9.4, ruling Q18 O-a): one LoCoMo-format conversation per S1 smoke question, built mechanically from its
  haystack - user -> speaker_a, assistant -> speaker_b, the haystack dates converted to LoCoMo's date form, and the
  LME smoke question itself.
* Empty items (no text) are a corpus property: skipped and counted per unit (empty_skipped), never an error (P0 c).
* Every unit carries its characters (K76's coverage denominator, ruling Q14 O-a) and the sha256 of its canonical
  content (no gold) - the input every arm is fed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

SMOKE_FIRST, SMOKE_LAST = 481, 500          # §3.4 / §9.4: the S1 smoke split, 1-based positions of the nested order
LME_STANDS = ("S1", "S2", "S3")
LOCOMO_ABSTAIN_CATEGORY = 5


class LoadRefused(ValueError):
    """A load the rules forbid (an unverified file, a scored prefix reaching the smoke split, a bad record)."""


@dataclass(frozen=True)
class Item:
    item_id: str
    text: str
    role: str | None
    speaker: str | None
    index: int


@dataclass(frozen=True)
class Session:
    session_id: str
    date: str | None
    items: tuple[Item, ...]


@dataclass(frozen=True)
class Question:
    qid: str
    text: str
    question_date: str | None
    category: str | None
    abstention: bool


@dataclass(frozen=True)
class EvalUnit:
    stand: str
    unit_id: str
    kind: str                      # haystack | conversation (K76's evaluation unit)
    sessions: tuple[Session, ...]
    questions: tuple[Question, ...]
    chars: int
    empty_skipped: int
    input_sha256: str


@dataclass(frozen=True)
class Gold:
    qid: str
    answer: str
    evidence: tuple[str, ...]


def _unit_sha(sessions: Sequence[Session], questions: Sequence[Question]) -> str:
    body = {"sessions": [asdict(s) for s in sessions], "questions": [asdict(q) for q in questions]}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def _unit(stand: str, unit_id: str, kind: str, sessions: list[Session], questions: list[Question], empty: int) -> EvalUnit:
    chars = sum(len(i.text) for s in sessions for i in s.items)
    return EvalUnit(stand, unit_id, kind, tuple(sessions), tuple(questions), chars, empty,
                    _unit_sha(sessions, questions))


# ── LongMemEval ─────────────────────────────────────────────────────────────────────────────────────────────────

def _lme_unit(stand: str, rec: Mapping) -> EvalUnit:
    for k in ("question_id", "question_type", "question", "haystack_session_ids", "haystack_dates",
              "haystack_sessions"):
        if k not in rec:
            raise LoadRefused(f"an LME record lacks {k}")
    ids, dates, sess = rec["haystack_session_ids"], rec["haystack_dates"], rec["haystack_sessions"]
    if not (len(ids) == len(dates) == len(sess)):
        raise LoadRefused(f"{rec['question_id']}: haystack ids, dates and sessions differ in length")
    sessions, empty = [], 0
    for sid, date, turns in zip(ids, dates, sess):
        items = []
        for n, t in enumerate(turns):
            text = (t.get("content") or "").strip()
            if not text:
                empty += 1
                continue
            items.append(Item(f"{sid}:{n}", t.get("content"), t.get("role"), None, n))
        sessions.append(Session(str(sid), date, tuple(items)))
    qid = str(rec["question_id"])
    q = Question(qid, rec["question"], rec.get("question_date"), rec["question_type"], qid.endswith("_abs"))
    return _unit(stand, qid, "haystack", sessions, [q], empty)


def lme_units(records: Iterable[Mapping], order: Sequence[str], *, stand: str, prefix: int) -> list[EvalUnit]:
    """The first `prefix` questions of the nested order, as haystack units (S1 and S2; S3 is the oracle file over S1's
    list). A scored prefix never reaches the smoke split."""
    if stand not in LME_STANDS:
        raise LoadRefused(f"{stand} is not an LME stand {LME_STANDS}")
    if prefix < 1 or prefix >= SMOKE_FIRST:
        raise LoadRefused(f"a scored prefix of {prefix} reaches into the smoke split (positions {SMOKE_FIRST}-{SMOKE_LAST})")
    if len(order) < prefix or len(set(order)) != len(order):
        raise LoadRefused("the order is shorter than the prefix or repeats an id")
    want = list(order[:prefix])
    by_id = {str(r.get("question_id")): r for r in records if str(r.get("question_id")) in set(want)}
    missing = [q for q in want if q not in by_id]
    if missing:
        raise LoadRefused(f"{len(missing)} ordered questions are not in the file: {missing[:3]}")
    return [_lme_unit(stand, by_id[q]) for q in want]


def lme_smoke_units(records: Iterable[Mapping], order: Sequence[str]) -> list[EvalUnit]:
    """S1 smoke: positions 481-500 of the nested order (smoke_rules.s1_tail), outside every scored list."""
    if len(order) < SMOKE_LAST:
        raise LoadRefused("the nested order must hold 500 questions")
    want = list(order[SMOKE_FIRST - 1:SMOKE_LAST])
    by_id = {str(r.get("question_id")): r for r in records if str(r.get("question_id")) in set(want)}
    if len(by_id) != len(want):
        raise LoadRefused("a smoke question is not in the file")
    return [_lme_unit("S1-smoke", by_id[q]) for q in want]


def lme_gold(records: Iterable[Mapping], qids: Iterable[str]) -> dict[str, Gold]:
    """The scorer's door: answers and gold evidence sessions (answer_session_ids) for the given questions only."""
    want = set(qids)
    out = {}
    for r in records:
        q = str(r.get("question_id"))
        if q in want:
            out[q] = Gold(q, str(r.get("answer")), tuple(str(s) for s in r.get("answer_session_ids") or ()))
    if set(out) != want:
        raise LoadRefused(f"no gold for {sorted(want - set(out))[:3]}")
    return out


# ── LoCoMo ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _session_numbers(conv: Mapping) -> list[int]:
    nums = []
    for k, v in conv.items():
        if k.startswith("session_") and not k.endswith("_date_time") and isinstance(v, list):
            tail = k[len("session_"):]
            if not tail.isdigit():
                raise LoadRefused(f"a LoCoMo session key {k!r} is not session_<n>")
            nums.append(int(tail))
    return sorted(nums)


def _item_text(t: Mapping) -> str:
    """A LoCoMo turn's text; a shared image is carried as its caption, as v2 did, identically for every arm."""
    text = t.get("text") or ""
    if t.get("blip_caption"):
        text = f"{text} [image: {t['blip_caption']}]".strip()
    return text


def _locomo_unit(stand: str, sample: Mapping) -> EvalUnit:
    sid = str(sample.get("sample_id"))
    conv = sample.get("conversation")
    if not isinstance(conv, Mapping):
        raise LoadRefused(f"{sid}: no conversation")
    sessions, empty = [], 0
    for n in _session_numbers(conv):
        items = []
        for i, t in enumerate(conv[f"session_{n}"]):
            text = _item_text(t)
            if not t.get("dia_id") or not text.strip():
                empty += 1
                continue
            items.append(Item(f"{sid}:{t['dia_id']}", text, None, t.get("speaker"), i))
        sessions.append(Session(f"{sid}:session_{n}", conv.get(f"session_{n}_date_time"), tuple(items)))
    questions = []
    for i, q in enumerate(sample.get("qa") or []):
        cat = q.get("category")
        questions.append(Question(f"{sid}:q{i}", q.get("question") or "", None, None if cat is None else str(cat),
                                  cat == LOCOMO_ABSTAIN_CATEGORY))
    return _unit(stand, sid, "conversation", sessions, questions, empty)


def locomo_units(samples: Iterable[Mapping], order: Sequence[str], *, prefix: int, stand: str = "S4") -> list[EvalUnit]:
    """The first `prefix` conversations of the committed unit order (§3.4), one store each."""
    if len(order) < prefix or len(set(order)) != len(order) or prefix < 1:
        raise LoadRefused("the order is shorter than the prefix, repeats an id, or the prefix is empty")
    by_id = {str(s.get("sample_id")): s for s in samples}
    missing = [u for u in order[:prefix] if u not in by_id]
    if missing:
        raise LoadRefused(f"ordered conversations not in the file: {missing[:3]}")
    return [_locomo_unit(stand, by_id[u]) for u in order[:prefix]]


def locomo_gold(samples: Iterable[Mapping], qids: Iterable[str]) -> dict[str, Gold]:
    """The scorer's door: answer (adversarial_answer for category 5) and the evidence dia_ids, namespaced by sample."""
    want, out = set(qids), {}
    for s in samples:
        sid = str(s.get("sample_id"))
        for i, q in enumerate(s.get("qa") or []):
            qid = f"{sid}:q{i}"
            if qid in want:
                ans = q.get("adversarial_answer") if q.get("category") == LOCOMO_ABSTAIN_CATEGORY else q.get("answer")
                ev = q.get("evidence") or []
                ev = [ev] if isinstance(ev, str) else ev
                out[qid] = Gold(qid, "" if ans is None else str(ans), tuple(f"{sid}:{e}" for e in ev))
    if set(out) != want:
        raise LoadRefused(f"no gold for {sorted(want - set(out))[:3]}")
    return out


# ── S4 smoke: LoCoMo-format conversations from the S1 smoke haystacks (Q18 O-a) ──────────────────────────────────

def lme_date_to_locomo(s: str) -> str:
    """'2023/05/20 (Sat) 02:21' -> '2:21 am on 20 May, 2023' (LoCoMo's session date form), mechanically."""
    try:
        d = dt.datetime.strptime(s, "%Y/%m/%d (%a) %H:%M")
    except (TypeError, ValueError):
        raise LoadRefused(f"LME date {s!r} is not 'YYYY/MM/DD (Day) HH:MM'") from None
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d} {'am' if d.hour < 12 else 'pm'} on {d.day} {d.strftime('%B')}, {d.year}"


def s4_smoke_samples(records: Iterable[Mapping], order: Sequence[str]) -> list[dict]:
    """One LoCoMo-format sample per S1 smoke question: user -> speaker_a, assistant -> speaker_b; the haystack's
    sessions in order with their dates converted; the LME question as the sample's one question. Gold stays in the
    LME gold door (the smoke has no scorer)."""
    roles = {"user": "user", "assistant": "assistant"}
    out = []
    for unit_rec in _smoke_records(records, order):
        conv: dict = {"speaker_a": roles["user"], "speaker_b": roles["assistant"]}
        for n, (date, turns) in enumerate(zip(unit_rec["haystack_dates"], unit_rec["haystack_sessions"]), 1):
            conv[f"session_{n}_date_time"] = lme_date_to_locomo(date)
            session = []
            for i, t in enumerate(turns, 1):
                role = t.get("role")
                if role not in roles:
                    raise LoadRefused(f"{unit_rec['question_id']}: a turn role {role!r} is neither user nor assistant")
                session.append({"speaker": roles[role], "dia_id": f"D{n}:{i}", "text": t.get("content") or ""})
            conv[f"session_{n}"] = session
        out.append({"sample_id": f"smoke-{unit_rec['question_id']}", "conversation": conv,
                    "qa": [{"question": unit_rec["question"], "category": None}]})
    return out


def _smoke_records(records: Iterable[Mapping], order: Sequence[str]) -> list[Mapping]:
    if len(order) < SMOKE_LAST:
        raise LoadRefused("the nested order must hold 500 questions")
    want = list(order[SMOKE_FIRST - 1:SMOKE_LAST])
    by_id = {str(r.get("question_id")): r for r in records if str(r.get("question_id")) in set(want)}
    if len(by_id) != len(want):
        raise LoadRefused("a smoke question is not in the file")
    return [by_id[q] for q in want]


# ── files ───────────────────────────────────────────────────────────────────────────────────────────────────────

def read_pinned(name: str, *, hf_hub: Path, pins_root: Path, cp=None):
    """The parsed JSON of a pinned file - verified against its pin first (size, then sha256); nothing else is read."""
    if cp is None:
        import importlib.util  # noqa: PLC0415
        import sys  # noqa: PLC0415
        cp = sys.modules.get("v3_corpus_pin_for_loaders")
        if cp is None:
            spec = importlib.util.spec_from_file_location("v3_corpus_pin_for_loaders",
                                                          Path(__file__).with_name("corpus_pin_v3.py"))
            cp = importlib.util.module_from_spec(spec)
            sys.modules["v3_corpus_pin_for_loaders"] = cp
            spec.loader.exec_module(cp)
    path = cp.location(name, hf_hub=hf_hub, pins_root=pins_root)
    try:
        cp.verify(name, path)
    except Exception as e:  # noqa: BLE001 - PinMismatch or its kin: nothing unverified is read
        raise LoadRefused(f"{name}: {e}") from None
    return json.loads(Path(path).read_bytes().decode("utf-8"))
