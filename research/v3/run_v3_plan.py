#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A4: the stand plan the scheduler runs - its blocks, each arm's write operations and reads, the one
date converter, the session text, and the byte-copied code each competitor child imports (rev1 §2.2, §5.3, §5.6; the
auditor's Q9, Q-45-4, Q-46-6, R-TOOLS and the Q-A4 rulings).

Part 1 (this file so far):
* blocks_for (§5.6): a stand's units in its committed order, cut into blocks of the stand's size - S1 10, S4 5, S5 10,
  S7 12 (S6's tiers are not counts: refused here); only the last block may be short; every unit id is a valid unit
  path component and STATUS id; block ids b01, b02, ...
* the date converter (§5.3, one for every arm, declared in arm_decl.date_route): a session date as the dataset gives it
  - LME "2023/05/20 (Sat) 02:21", LoCoMo "1:56 pm on 8 May, 2023", BEAM "March-15-2024" or an ISO date - to ISO
  (iso_datetime "2023-05-20T02:21:00" for a date field, iso_day "2023-05-20" for the header). A date it cannot read
  is refused by name, never guessed.
* session_text (Q-45-4): one line "<role|speaker>: <text>" per item, joined by "\\n" - the dataset's role (LME, BEAM)
  or speaker name (LoCoMo) as it is; header(day) is the uniform "Conversation from <YYYY-MM-DD>:" line (§5.3).
* read_plan (§4.3a): one read per question and point, k = points.K_AT[point]; the smoke reads point B only.
* CodeStager (Q9): the files a competitor child imports - its adapter, arms/base.py and the rest - copied byte for byte
  into <runs>/<stand>/<run>/<arm>/_code/<block>/ at the start of a block, each copy's sha256 checked against its source
  and recorded; the directory must be fresh. The repository is then on no competitor child's path.

Part 2 - each arm's write operations (§2.2 write units, §5.3, Q-45-4, Q-46-6, Q-A4-1..4):
* ARMS: every arm with an adapter - its write unit (session, message, turn, session-thread, item), its date route
  (field:<name> - the date goes in that write field; header - the adapter writes §5.3's header from date=; in-bytes -
  the header is part of the item's bytes, the retrieval tier), its store and whether its reads take a point. An arm
  without an adapter (cognee, supermemory-local, claude-code-memory) is refused by name: that is A8.
* authors (Q-A4-1, Q-A4-2): an item's role is the dataset's, or - LoCoMo, which has names only - the role its speaker
  has in the pinned sample's map {speaker_a: user, speaker_b: assistant}, the same map for every arm and its sha in the
  run record; a name outside the two is refused. Its speaker is the dataset's name, or - LME, BEAM - its role as it is.
  An arm that takes a role gets the role; one that takes a name gets the name.
* dates (Q-A4-4): naive local time without a zone, as the dataset gives it; on a stand with dates every session needs
  one the converter reads (else the unit is refused by name); a stand without dates (FC, AMA) gives None and no header.
* the retrieval tier's item bytes (Q-A4-3): "<role|speaker>: <text>" - with §5.3's header line first on a dated stand -
  cut as one string by the §5.1 truncator (<= 2,048 bge-m3 tokens, specials included); the same bytes for every
  retrieval arm, "#<index>" never in them (the harness adds it at read time).
* write_ops -> the ops the scheduler sends ({"item": ..., "date": ...}); item_shas -> the sha256 of the bytes each op
  hands over, for the run record (Q-45-4: equal across arms, checked).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
BLOCK_SIZE = {"S1": 10, "S4": 5, "S5": 10, "S7": 12}           # §5.6
_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")                     # launch._UNIT_PART and status_log._SEG, both
_DATE_FORMATS = (
    ("%Y/%m/%d (%a) %H:%M", True),                             # LME haystack_dates
    ("%I:%M %p on %d %B, %Y", True),                           # LoCoMo session_<n>_date_time
    ("%B-%d-%Y", False),                                       # BEAM time_anchor
    ("%Y-%m-%d", False),                                       # an ISO date
    ("%Y-%m-%dT%H:%M:%S", True),                               # an ISO date and time
)


class PlanError(ValueError):
    """A plan the preregistration does not allow, or an input the plan cannot read; nothing was scheduled."""


def _load(name: str, path: Path):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def base_stand(stand: str) -> str:
    """The preregistered stand a stand id belongs to: S1-smoke-2 -> S1 (Q-12-7's smoke ids)."""
    return stand.split("-", 1)[0]


# ── blocks (§5.6) ──────────────────────────────────────────────────────────────────────────────────────────────

def blocks_for(stand: str, unit_ids: Sequence[str], *, block_plan: Any) -> list:
    """The stand's blocks: ``block_plan`` is the scheduler's BlockPlan class (the scheduler's own module decides it)."""
    base = base_stand(stand)
    if base not in BLOCK_SIZE:
        raise PlanError(f"{stand}: no block size for {base} (§5.6; S6's tiers are not counts)")
    units = list(unit_ids)
    if not units:
        raise PlanError(f"{stand}: no unit to plan")
    bad = [u for u in units if not (isinstance(u, str) and _ID.fullmatch(u) and u not in (".", ".."))]
    if bad:
        raise PlanError(f"{stand}: unit ids {bad[:3]} are not [A-Za-z0-9._-]{{1,128}} (a unit path and a STATUS id)")
    if len(set(units)) != len(units):
        raise PlanError(f"{stand}: a unit id repeats")
    size = BLOCK_SIZE[base]
    return [block_plan(block=f"b{i // size + 1:02d}", units=tuple(units[i:i + size])) for i in range(0, len(units), size)]


# ── dates (§5.3) ───────────────────────────────────────────────────────────────────────────────────────────────

def _parse_date(s: Any) -> dt.datetime:
    if not isinstance(s, str) or not s.strip():
        raise PlanError(f"a session date {s!r} is not a date")
    for fmt, _has_time in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    raise PlanError(f"a session date {s!r} is in no form the plan reads - never guessed (§5.3)")


def iso_datetime(s: Any) -> str:
    """A session date for a date field: YYYY-MM-DDTHH:MM:SS (a date without a time reads as midnight)."""
    return _parse_date(s).strftime("%Y-%m-%dT%H:%M:%S")


def iso_day(s: Any) -> str:
    """A session date for the uniform header: YYYY-MM-DD."""
    return _parse_date(s).strftime("%Y-%m-%d")


def header(day: str) -> str:
    """§5.3's uniform header, the first line of a write unit's content for an arm without a date field."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
        raise PlanError(f"the header's day {day!r} is not YYYY-MM-DD")
    return f"Conversation from {day}:"


# ── the session text (Q-45-4) ──────────────────────────────────────────────────────────────────────────────────

def item_line(item: Any) -> str:
    """One item as "<role|speaker>: <text>" - the dataset's role, or its speaker name when there is no role."""
    label = item.role if item.role is not None else item.speaker
    if not isinstance(label, str) or not label:
        raise PlanError(f"item {item.item_id}: neither a role nor a speaker to name its line (Q-45-4)")
    return f"{label}: {item.text}"


def session_text(session: Any) -> str:
    """A session's text for every arm with a text API: the same bytes for all of them (Q-45-4)."""
    if not session.items:
        raise PlanError(f"session {session.session_id} has no item")
    return "\n".join(item_line(i) for i in session.items)


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── reads (§4.3a) ──────────────────────────────────────────────────────────────────────────────────────────────

def read_plan(unit: Any, *, points: Sequence[str] = ("B",), read_req: Any, k_at: Mapping[str, int]) -> list:
    """One read per question and point, in the unit's question order (``read_req``: the scheduler's ReadReq)."""
    bad = [p for p in points if p not in k_at]
    if bad:
        raise PlanError(f"points {bad} have no k (points.K_AT: {sorted(k_at)})")
    if not unit.questions:
        raise PlanError(f"{unit.unit_id}: a unit with no question")
    return [read_req(qid=q.qid, query=q.text, point=p, k=k_at[p]) for q in unit.questions for p in points]


# ── the code a competitor child imports (Q9) ───────────────────────────────────────────────────────────────────

def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class CodeStager:
    """Q9: byte copies of a competitor child's imports, per (stand, run, arm, block), each sha256 checked and kept."""

    def __init__(self, runs_root: str | os.PathLike) -> None:
        self.runs_root = Path(runs_root)
        self.records: list[dict] = []

    def stage(self, stand: str, run: str, arm: str, block: str, files: Mapping[str, str | os.PathLike]) -> dict:
        """Copy ``files`` (name in the copy dir -> source path) into a fresh _code/<block> dir; {name: {path, sha256}}."""
        for part in (stand, run, arm, block):
            if not (isinstance(part, str) and _ID.fullmatch(part) and part not in (".", "..")):
                raise PlanError(f"code dir component {part!r} is not [A-Za-z0-9._-]{{1,128}}")
        dest = self.runs_root / stand / run / arm / "_code" / block
        if dest.exists():
            raise PlanError(f"{dest} already exists - a block's code copy is made once, fresh (Q9)")
        bad = [n for n in files if not re.fullmatch(r"[A-Za-z0-9_]+\.py", n)]
        if bad:
            raise PlanError(f"copy names {bad} are not plain module files")
        dest.mkdir(parents=True)
        out = {}
        for name, src in files.items():
            src = Path(src)
            want = _sha256_file(src)
            shutil.copyfile(src, dest / name)
            got = _sha256_file(dest / name)
            if got != want:
                raise PlanError(f"{dest / name}: the copy's sha256 is not its source's (Q9)")
            out[name] = {"path": str(dest / name), "sha256": got, "source": src.name}
        rec = {"stand": stand, "run": run, "arm": arm, "block": block, "dir": str(dest), "files": out}
        self.records.append(rec)
        return out


def unit_ids(units: Iterable[Any]) -> list[str]:
    return [u.unit_id for u in units]


# ── part 2: the arms and their write operations ────────────────────────────────────────────────────────────────

COMPETITOR_CODE = ("base.py", "_http_count.py", "_ollama_pacer.py")   # Q9: beside the adapter in its copy dir
LETTA_CODE = ("base.py", "_rest.py")
GRANULARITIES = ("session", "message", "turn", "session-thread", "item")


@dataclass(frozen=True)
class ArmSpec:
    """How the plan writes to one arm (from its adapter: research/v3/arms/<adapter>)."""
    name: str
    adapter: str               # file name under research/v3/arms/
    granularity: str           # one of GRANULARITIES (§2.2 write unit)
    date_route: str            # field:<name> | header | in-bytes (arm_decl.date_route)
    takes: str                 # "role", "speaker" or "role+speaker": how an item names its author
    store_persistence: str     # disk | memory (Q25(4)); a memory arm's spec stage is "both"
    reads_point: bool          # its read takes a point (K/B)
    ours: bool                 # our arm: it runs from the repository (Q9); a competitor runs from its code copy
    code: tuple = ()           # the files copied beside the adapter (Q9)


def _nw(name: str) -> ArmSpec:
    return ArmSpec(name, "runner_nevertwice.py", "session", "field:date", "role", "disk", False, True)


def _store(name: str, adapter: str, *, ours: bool = False) -> ArmSpec:
    return ArmSpec(name, adapter, "item", "in-bytes", "role", "disk", False, ours, () if ours else COMPETITOR_CODE)


ARMS: dict[str, ArmSpec] = {
    **{n: _nw(n) for n in ("nevertwice", "nevertwice-rawtext", "nevertwice-ablation")},
    "nevertwice-ranker": _store("nevertwice-ranker", "runner_nevertwice.py", ours=True),
    "bm25-floor": _store("bm25-floor", "bm25_floor.py", ours=True),
    "chroma-store": _store("chroma-store", "arm_chroma.py"),
    "mem0-store": _store("mem0-store", "arm_mem0.py"),
    "langmem-store": _store("langmem-store", "arm_langmem.py"),
    "mem0": ArmSpec("mem0", "arm_mem0.py", "message", "header", "role+speaker", "disk", False, False, COMPETITOR_CODE),
    "langmem": ArmSpec("langmem", "arm_langmem.py", "session-thread", "header", "role+speaker", "memory", False, False,
                       COMPETITOR_CODE),
    "a-mem": ArmSpec("a-mem", "arm_amem.py", "turn", "field:time", "speaker", "memory", True, False, COMPETITOR_CODE),
    "zep-graphiti": ArmSpec("zep-graphiti", "arm_graphiti.py", "message", "field:reference_time", "speaker", "disk",
                            True, False, COMPETITOR_CODE),
    "letta": ArmSpec("letta", "arm_letta.py", "message", "header", "speaker", "disk", True, False, LETTA_CODE),
}
NO_ADAPTER = ("cognee", "supermemory-local", "claude-code-memory")      # A8


def arm_spec(name: str) -> ArmSpec:
    if name in NO_ADAPTER:
        raise PlanError(f"{name} has no adapter yet - no op is written for it (A8)")
    if name not in ARMS:
        raise PlanError(f"{name} is not an arm of the plan")
    return ARMS[name]


def speaker_map(sample: Mapping[str, Any]) -> dict[str, str]:
    """Q-A4-1: {speaker_a: user, speaker_b: assistant} from a pinned LoCoMo(-format) sample's conversation."""
    conv = sample.get("conversation") if isinstance(sample, Mapping) else None
    a = conv.get("speaker_a") if isinstance(conv, Mapping) else None
    b = conv.get("speaker_b") if isinstance(conv, Mapping) else None
    if not (isinstance(a, str) and a and isinstance(b, str) and b and a != b):
        raise PlanError(f"sample {sample.get('sample_id') if isinstance(sample, Mapping) else '?'}: no two distinct "
                        f"speakers to map (Q-A4-1)")
    return {a: "user", b: "assistant"}


def map_sha256(m: Mapping[str, str] | None) -> str | None:
    """The sha of a speaker map, for the run record (Q-A4-1)."""
    if m is None:
        return None
    return hashlib.sha256(repr(sorted(m.items())).encode("utf-8")).hexdigest()


def author(item: Any, smap: Mapping[str, str] | None) -> tuple[str, str]:
    """(role, speaker) of an item (Q-A4-1, Q-A4-2)."""
    if item.role is not None:
        role = item.role
    else:
        if smap is None or item.speaker not in smap:
            raise PlanError(f"item {item.item_id}: speaker {item.speaker!r} is neither of the sample's two (Q-A4-1)")
        role = smap[item.speaker]
    speaker = item.speaker if item.speaker is not None else item.role
    if not (isinstance(role, str) and role and isinstance(speaker, str) and speaker):
        raise PlanError(f"item {item.item_id}: no role or no speaker")
    return role, speaker


def _date(session: Any, dated: bool) -> tuple[str | None, str | None]:
    """(iso datetime for a field or the adapter's header, the day for an in-bytes header) - None on undated stands."""
    if not dated:
        return None, None
    return iso_datetime(session.date), iso_day(session.date)


def _check_unit_dates(unit: Any, dated: bool) -> None:
    for s in unit.sessions:
        try:
            _date(s, dated)
        except PlanError as e:
            raise PlanError(f"{unit.unit_id}: session {s.session_id}: {e} - the unit is refused (Q-A4-4)") from None


def retrieval_bytes(item: Any, day: str | None, *, truncate: Callable[[str], Any]) -> tuple[str, bool]:
    """Q-A4-3: the one string every retrieval arm gets - the §5.3 header (dated stands) and the item's line, cut as a
    whole by the §5.1 truncator; (text, truncated)."""
    line = item_line(item)
    text = f"{header(day)}\n{line}" if day is not None else line
    cut = truncate(text)
    return cut.text, cut.truncated


def write_ops(spec: ArmSpec, unit: Any, *, dated: bool, smap: Mapping[str, str] | None = None,
              truncate: Callable[[str], Any] | None = None) -> list[dict]:
    """The write ops of one (arm, unit), in the unit's session and item order (see part 2 of the module docstring)."""
    _check_unit_dates(unit, dated)
    ops: list[dict] = []
    if spec.granularity == "session":
        for s in unit.sessions:
            iso, _day = _date(s, dated)
            ops.append({"item": {"item_id": s.session_id, "session_id": s.session_id, "text": session_text(s)},
                        "date": iso})
    elif spec.granularity in ("message", "turn"):
        for s in unit.sessions:
            iso, _day = _date(s, dated)
            for it in s.items:
                role, speaker = author(it, smap)
                item = {"item_id": it.item_id, "session_id": s.session_id, "speaker": speaker, "text": it.text}
                if "role" in spec.takes:
                    item["role"] = role
                ops.append({"item": item, "date": iso})
    elif spec.granularity == "session-thread":
        for s in unit.sessions:
            iso, _day = _date(s, dated)
            msgs = []
            for it in s.items:
                role, speaker = author(it, smap)
                msgs.append({"role": role, "speaker": speaker, "text": it.text})
            ops.append({"item": {"item_id": s.session_id, "session_id": s.session_id, "messages": msgs}, "date": iso})
    elif spec.granularity == "item":
        if truncate is None:
            raise PlanError(f"{spec.name}: a retrieval arm's items need the §5.1 truncator")
        n = 0
        for s in unit.sessions:
            _iso, day = _date(s, dated)
            for it in s.items:
                author(it, smap)                          # the same refusal as every other arm's
                text, _cut = retrieval_bytes(it, day, truncate=truncate)
                ops.append({"item": {"item_id": it.item_id, "index": n, "text": text}, "date": None})
                n += 1
    else:
        raise PlanError(f"{spec.name}: write unit {spec.granularity!r} is not one of {GRANULARITIES}")
    return ops


def item_shas(ops: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """{op item id: sha256 of the bytes it hands over} - text, or the thread's messages as canonical JSON."""
    import json  # noqa: PLC0415
    out = {}
    for op in ops:
        item = op["item"]
        payload = item.get("text") if "text" in item else json.dumps(item.get("messages"), sort_keys=True,
                                                                      ensure_ascii=False)
        out[item["item_id"]] = text_sha256(payload)
    return out
