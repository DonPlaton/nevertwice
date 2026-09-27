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
* TB4.3b, with the field names j4 recorded (13/13 against the auditor's blind keys):
  - S5 BEAM: a unit per `conversation_id`; `chat` sessions of messages {content, id, index, question_type, role,
    time_anchor}, a session dated by its first message's time_anchor; questions from `probing_questions`, a Python
    literal read with ast.literal_eval (never eval), one per (ability, position). The gold door (the auditor's Q31):
    the rubric - the only thing the vendor scorer reads, for every ability - as a first-class field, refused when absent,
    empty or not a list of strings; the answer for display only, from the declared table BEAM_ANSWER_FIELD (an ability
    outside it, or its field missing, is refused - never ""); the source_chat_ids leaves, each an int message id
    (anything else refused), namespaced by conversation, an absent key being empty evidence. S5 smoke (§9.4): the 500K
    split's first conversation cut by smoke_rules.s5_cut; all its questions for the pilot, and for FA/FR only those
    whose source ids all lie inside the kept messages (Q19 O-a).
  - S7 AMA: SOFTWARE records only, by name; a unit per `episode_id`: the task, then per step its action and its
    observation as items (the unit's characters, Q14); questions from `qa_pairs` (question_uuid, question, type), the
    answers only through the gold door; the run record handed to an arm (and to render_ama_jsonl) is stripped of
    qa_pairs. S7 smoke: the non-SOFTWARE trajectory closest to the SOFTWARE median (smoke_rules.s7_pick).
  - A JSONL is read one LF-terminated line at a time, never with str.splitlines() (U+2028/U+0085 inside AMA strings).
"""
from __future__ import annotations

import ast
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
    detail: str = ""               # any other gold field as canonical JSON (display only)
    rubric: tuple[str, ...] = ()   # BEAM (Q31): the vendor scorer reads ONLY the rubric, for every ability


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
                                  locomo_category(cat) == LOCOMO_ABSTAIN_CATEGORY))
    return _unit(stand, sid, "conversation", sessions, questions, empty)


def locomo_category(v: Any) -> int | None:
    """B-CAT: a LoCoMo category as the int the benchmark numbers it by - an int, or a string of digits (this loader
    keeps it as text in its questions); None stays None; anything else - a bool, a word - is refused by name. Every
    comparison of a category goes through here: a string compared with an int is never equal, and silently so."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise LoadRefused(f"a LoCoMo category {v!r} is a bool, not a category number")
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    raise LoadRefused(f"a LoCoMo category {v!r} is not a category number")


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
                ans = (q.get("adversarial_answer") if locomo_category(q.get("category")) == LOCOMO_ABSTAIN_CATEGORY
                       else q.get("answer"))
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


# ── S5 BEAM ─────────────────────────────────────────────────────────────────────────────────────────────────────

AMA_SWE = "SOFTWARE"
BEAM_ABSTAIN = "abstention"
#: Q31: where each ability keeps its reference answer (display only; the scorer reads the rubric).
BEAM_ANSWER_FIELD = {"abstention": "ideal_response", "contradiction_resolution": "ideal_answer",
                     "instruction_following": "expected_compliance", "preference_following": "expected_compliance",
                     "summarization": "ideal_summary", "event_ordering": "answer", "information_extraction": "answer",
                     "knowledge_update": "answer", "multi_session_reasoning": "answer", "temporal_reasoning": "answer"}


def _probing(rec: Mapping) -> dict:
    try:
        obj = ast.literal_eval(rec["probing_questions"])
    except (ValueError, SyntaxError, KeyError, TypeError) as e:
        raise LoadRefused(f"conversation {rec.get('conversation_id')}: probing_questions is not a literal "
                          f"({type(e).__name__}) - never evaluated") from None
    if not isinstance(obj, dict):
        raise LoadRefused(f"conversation {rec.get('conversation_id')}: probing_questions is not a mapping of abilities")
    return obj


def _source_ids(v, where: str) -> list[int]:
    """source_chat_ids flattened over lists and dicts; every leaf an int message id (Q31.3), anything else refused."""
    if v is None:
        return []
    if isinstance(v, dict):
        return [x for y in v.values() for x in _source_ids(y, where)]
    if isinstance(v, (list, tuple)):
        return [x for y in v for x in _source_ids(y, where)]
    if isinstance(v, int) and not isinstance(v, bool):
        return [v]
    raise LoadRefused(f"{where}: a source_chat_ids leaf {v!r} is not a message id (int)")


def _beam_unit(stand: str, rec: Mapping, *, keep_sessions: int | None = None) -> EvalUnit:
    cid = str(rec["conversation_id"])
    chat = rec.get("chat")
    if not isinstance(chat, list):
        raise LoadRefused(f"conversation {cid}: chat is not a list of sessions")
    sessions, empty = [], 0
    for j, sess in enumerate(chat[:keep_sessions] if keep_sessions is not None else chat):
        items = []
        for n, msg in enumerate(sess or []):
            text = msg.get("content")
            if not isinstance(text, str) or not text.strip():
                empty += 1
                continue
            items.append(Item(f"{cid}:{msg.get('id')}", text, msg.get("role"), None, n))
        first = (sess or [{}])[0] if sess else {}
        date = str(first.get("time_anchor")).strip() if first.get("time_anchor") not in (None, "") else None
        sessions.append(Session(f"{cid}:s{j}", date, tuple(items)))
    questions = []
    for ability, qs in _probing(rec).items():
        for i, q in enumerate(qs or []):
            questions.append(Question(f"{cid}:{ability}:{i}", str(q.get("question") or ""), None, str(ability),
                                      str(ability) == BEAM_ABSTAIN))
    return _unit(stand, cid, "conversation", sessions, questions, empty)


def beam_units(rows: Iterable[Mapping], order: Sequence, *, prefix: int, stand: str = "S5") -> list[EvalUnit]:
    """The first `prefix` conversations of the committed order (S5 = conversation_id)."""
    order = [str(x) for x in order]
    if len(order) < prefix or len(set(order)) != len(order) or prefix < 1:
        raise LoadRefused("the order is shorter than the prefix, repeats an id, or the prefix is empty")
    by_id = {str(r.get("conversation_id")): r for r in rows}
    missing = [u for u in order[:prefix] if u not in by_id]
    if missing:
        raise LoadRefused(f"ordered conversations not in the file: {missing[:3]}")
    return [_beam_unit(stand, by_id[u]) for u in order[:prefix]]


def beam_gold(rows: Iterable[Mapping], qids: Iterable[str]) -> dict[str, Gold]:
    """The scorer's door (Q31): the rubric (first-class, required), the answer by BEAM_ANSWER_FIELD (display only),
    the int source ids namespaced by conversation, and every other gold field as canonical JSON."""
    want, out = set(qids), {}
    for r in rows:
        cid = str(r.get("conversation_id"))
        for ability, qs in _probing(r).items():
            for i, q in enumerate(qs or []):
                qid = f"{cid}:{ability}:{i}"
                if qid not in want:
                    continue
                field = BEAM_ANSWER_FIELD.get(str(ability))
                if field is None:
                    raise LoadRefused(f"{qid}: ability {ability!r} is outside the declared answer table")
                if q.get(field) is None:
                    raise LoadRefused(f"{qid}: the answer field {field!r} of {ability} is missing - never \"\"")
                rubric = q.get("rubric")
                if not (isinstance(rubric, list) and rubric and all(isinstance(x, str) and x.strip() for x in rubric)):
                    raise LoadRefused(f"{qid}: the rubric is missing, empty or not a list of strings (Q31)")
                ans = q[field] if isinstance(q[field], str) else json.dumps(q[field], sort_keys=True, ensure_ascii=False)
                rest = {k: v for k, v in q.items() if k not in ("question", field, "rubric", "source_chat_ids")}
                out[qid] = Gold(qid, ans, tuple(f"{cid}:{x}" for x in _source_ids(q.get("source_chat_ids"), qid)),
                                json.dumps(rest, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str),
                                tuple(rubric))
    if set(out) != want:
        raise LoadRefused(f"no gold for {sorted(want - set(out))[:3]}")
    return out


def s5_smoke(rows_500k: Sequence[Mapping], count) -> tuple[EvalUnit, list[str]]:
    """§9.4 S5 smoke: the 500K split's first conversation in dataset order, cut by smoke_rules.s5_cut over its
    sessions' text; returns the unit (all its questions, for the pilot) and the question ids FA/FR may use - those
    whose source ids all lie inside the kept messages (Q19 O-a)."""
    sr = _smoke_rules()
    if not rows_500k:
        raise LoadRefused("the 500K split holds no conversation")
    first = rows_500k[0]
    texts = ["\n".join(str(m.get("content") or "") for m in (s or [])) for s in first.get("chat") or []]
    kept = sr.s5_cut(texts, count)
    unit = _beam_unit("S5-smoke", first, keep_sessions=kept)
    cid = str(first["conversation_id"])
    kept_ids = {f"{cid}:{m.get('id')}" for s in (first.get("chat") or [])[:kept] for m in (s or [])}
    gold = beam_gold([first], [q.qid for q in unit.questions])
    inside = [q.qid for q in unit.questions if gold[q.qid].evidence and set(gold[q.qid].evidence) <= kept_ids]
    return unit, inside


# ── S7 AMA ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _read_jsonl(path) -> list[dict]:
    """One record per LF-terminated line - never str.splitlines() (U+2028/U+0085 inside AMA strings, A6 j4). Internal:
    a pinned file is read through read_pinned(fmt="jsonl"), which verifies it first."""
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


AMA_RUN_KEYS = ("episode_id", "task", "trajectory", "num_turns")


def ama_run_record(rec: Mapping) -> dict:
    """What an arm (and render_ama_jsonl) receives for a trajectory: no qa_pairs, so no answer on the run path."""
    missing = [k for k in AMA_RUN_KEYS if k not in rec]
    if missing:
        raise LoadRefused(f"episode {rec.get('episode_id')}: the record lacks {missing}")
    return {k: rec[k] for k in AMA_RUN_KEYS}


def _render():
    import importlib.util  # noqa: PLC0415
    import sys  # noqa: PLC0415
    mod = sys.modules.get("v3_render_ama_for_loaders")
    if mod is None:
        spec = importlib.util.spec_from_file_location("v3_render_ama_for_loaders",
                                                      Path(__file__).with_name("render_ama_jsonl.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["v3_render_ama_for_loaders"] = mod
        spec.loader.exec_module(mod)
    return mod


def _ama_unit(stand: str, rec: Mapping) -> EvalUnit:
    """A trajectory unit - validated by render_ama_jsonl.render itself (B3: the loader refuses exactly what the renderer
    refuses, so every arm gets the same units)."""
    ep = str(rec.get("episode_id"))
    run = ama_run_record(rec)
    R = _render()
    try:
        R.render(run, unit_dir=Path("."), ingest_utc="")
    except R.RenderRefused as e:
        raise LoadRefused(str(e)) from None
    items = [Item(f"{ep}:task", run["task"], "user", None, 0)]
    for i, step in enumerate(run["trajectory"]):
        for k, role in (("action", "assistant"), ("observation", "tool")):
            items.append(Item(f"{ep}:{i}:{k}", step[k], role, None, len(items)))
    questions = [Question(str(q.get("question_uuid")), str(q.get("question") or ""), None, str(q.get("type")), False)
                 for q in rec.get("qa_pairs") or []]
    if len({q.qid for q in questions}) != len(questions):
        raise LoadRefused(f"episode {ep}: a question_uuid repeats")
    return _unit(stand, ep, "trajectory", [Session(ep, None, tuple(items))], questions, 0)


def ama_units(rows: Iterable[Mapping], order: Sequence, *, prefix: int, stand: str = "S7") -> list[EvalUnit]:
    """The first `prefix` SOFTWARE trajectories of the committed order (S7 = episode_id); a non-SOFTWARE id is refused."""
    order = [str(x) for x in order]
    if len(order) < prefix or len(set(order)) != len(order) or prefix < 1:
        raise LoadRefused("the order is shorter than the prefix, repeats an id, or the prefix is empty")
    rows = list(rows)
    by_id = {str(r.get("episode_id")): r for r in rows if r.get("domain") == AMA_SWE}
    others = {str(r.get("episode_id")) for r in rows if r.get("domain") != AMA_SWE}
    bad = [u for u in order[:prefix] if u in others]
    if bad:
        raise LoadRefused(f"ordered episodes outside the {AMA_SWE} domain: {bad[:3]}")
    missing = [u for u in order[:prefix] if u not in by_id]
    if missing:
        raise LoadRefused(f"ordered episodes not in the file: {missing[:3]}")
    return [_ama_unit(stand, by_id[u]) for u in order[:prefix]]


def ama_gold(rows: Iterable[Mapping], qids: Iterable[str]) -> dict[str, Gold]:
    want, out = set(qids), {}
    for r in rows:
        for q in r.get("qa_pairs") or []:
            qid = str(q.get("question_uuid"))
            if qid in want:
                out[qid] = Gold(qid, str(q.get("answer")), ())
    if set(out) != want:
        raise LoadRefused(f"no gold for {sorted(want - set(out))[:3]}")
    return out


def s7_smoke(rows: Sequence[Mapping]) -> EvalUnit:
    """§9.4 S7 smoke: the non-SOFTWARE trajectory closest to the SOFTWARE median (smoke_rules.s7_pick)."""
    sr = _smoke_rules()
    swe = [r for r in rows if r.get("domain") == AMA_SWE]
    others = [r for r in rows if r.get("domain") != AMA_SWE]
    i = sr.s7_pick([r["trajectory"] for r in others], [r["trajectory"] for r in swe])
    return _ama_unit("S7-smoke", others[i])


def _smoke_rules():
    import importlib.util  # noqa: PLC0415
    import sys  # noqa: PLC0415
    mod = sys.modules.get("v3_smoke_rules_for_loaders")
    if mod is None:
        spec = importlib.util.spec_from_file_location("v3_smoke_rules_for_loaders", Path(__file__).with_name("smoke_rules.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["v3_smoke_rules_for_loaders"] = mod
        spec.loader.exec_module(mod)
    return mod


# ── files ───────────────────────────────────────────────────────────────────────────────────────────────────────

def read_pinned(name: str, *, hf_hub: Path, pins_root: Path, cp=None, fmt: str = "json"):
    """A pinned file's records - verified against its pin first (size, then sha256), then read by its format (B2):
    json (json.loads), jsonl (one record per LF line), parquet (pyarrow, the v3_data venv only). Nothing else is read."""
    if fmt not in ("json", "jsonl", "parquet"):
        raise LoadRefused(f"format {fmt!r} is json, jsonl or parquet")
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
    if fmt == "jsonl":
        return _read_jsonl(path)
    if fmt == "parquet":
        import pyarrow.parquet as pq  # noqa: PLC0415 - the v3_data venv only
        return pq.read_table(str(path)).to_pylist()
    return json.loads(Path(path).read_bytes().decode("utf-8"))
