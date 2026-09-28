#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A4: the stand plan the scheduler runs - its blocks, each arm's write operations and reads, the one
date converter, the session text, and the byte-copied code each competitor child imports (rev1 §2.2, §5.3, §5.6; the
auditor's Q9, Q-45-4, Q-46-6, R-TOOLS and the Q-A4 rulings).

Part 1:
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

Part 3 - launching the arms (Q-A4-5, Q-A4-6):
* python_decl (Q-A4-5): what an arm's interpreter is, asked of the interpreter itself in isolated mode (-I: no
  PYTHONPATH, no user site) - its version, the base python.exe it was made from and that file's sha256, whether it is
  a venv, and the distributions importlib.metadata sees. Our arms (runner_nevertwice, bm25_floor) and letta's adapter
  need nothing outside the standard library: they run on a venv with no package but pip, and any other - or the bare
  base interpreter, whose site-packages nothing pins - is refused by name. The declaration goes in arm_decl.launch.
* check_ids (B-P2): a run or unit id is every adapter's [A-Za-z0-9_-]{1,64} - no dot, which splits /u/<run>.<unit>
  (Q3) - and never starts with "_", which names the harness's own directories beside the units (_code, _pycache).
* PlanLauncher (Q-A4-6): one arm's children on a stand, for the scheduler's ChildArmLauncher (O1). For every unit:
  the spec with exactly its adapter's SPEC_KEYS (read from the adapter's source, never imported); unit_dir the write
  stage's directory in both stages; a memory arm's one child is its "both" stage. Its environment: the proxy token
  under the one name the adapter checks (none for the arms without an LLM, and none for letta, whose token is its
  server's), and the values that name the unit - the runner's own declared_env (DEEPSEEK_URL, the ablation's window),
  a-mem's OPENAI_BASE_URL or OLLAMA_API_BASE - so (1) the names are the same in every unit, (2) every URL to the proxy
  is the arm's own port with /u/<run>.<unit> of the unit it runs (an adapter that builds its URL from the spec gets
  the arm's port and the unit's run and unit), and (3) the environment passes launch.assert_env with the tokens
  run_v3_proxy.build_secrets makes. (4) Our arms run from the repository - argv [python, -B, <script>, <spec>] and the
  one argv exception {2: <script>}; a competitor runs from its CodeStager copy for (run, block), staged at its first
  spawn in the block and its sha256 checked again at every spawn, with no exception, no repository path in its argv
  and no PYTHONPATH - a competitor given an exception is refused by name. (5) path_dirs is the arm's python's
  directory alone. record() is the launch record for the run record: names, never a token.
* Answerer (§4.3a, §5.2, §8.1, Q8): StandPlan.answer. An arm's read -> its item texts in the order it returned them
  -> points.fill (the §5.2 budget, cl100k) -> the question's template (a function of the stand and the question's
  category, never of the arm) with the question as the benchmark asks it -> reader_judge.read through the arm's reader
  port with /u/<run>.<unit>. A reader that answers nothing usable (no reply, a status other than 200, a reply without
  choices) is ReaskableError: the scheduler's W3 re-asks it. The row keeps ids, sha256 values, token counts and flags,
  never text (Q8); the texts go to one fresh file per answer, <runs>/<stand>/_answers/<run>/<arm>/<unit>/<sha256 of
  qid and point>.json, its sha256 in the row. key_question maps (unit, the proxy's request key of every reader
  request, re-asks included, failed or not) to the qid - accounting's map; a key two questions share is counted, and
  kept for the first.
* truncation (the auditor's A5 condition): for every arm-run, the retrieval tier's items cut by §5.1 and all it wrote
  - {items, truncated, share}; an arm whose product embeds its own text gets "not-applicable".
* stand_plan: the scheduler's StandPlan - each arm's launcher, the write ops per (arm, run, unit) (recording the
  truncation and each op's sha256 per arm-run), the reads per (arm, unit) (Q-12-3), the Answerer - and the plan's
  record for the run records (truncation, item shas, each unit's speaker map sha, the launch records). B-S4-SMAP
  (Q-A4-1, "the map is built from the pinned sample by unit_id"): one speaker map PER UNIT - LoCoMo's conversations
  each have their own two speakers - speaker_maps(samples) builds them, a unit without one (LME: roles given) None.
"""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
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
    "mem0": ArmSpec("mem0", "arm_mem0.py", "message", "header", "role+speaker", "disk", False, False, COMPETITOR_CODE),
    "langmem": ArmSpec("langmem", "arm_langmem.py", "session-thread", "header", "role+speaker", "memory", False, False,
                       COMPETITOR_CODE),
    # B-P1: langmem-store is the same adapter, its store in the process too - a "both" stage, never a disk arm
    "langmem-store": ArmSpec("langmem-store", "arm_langmem.py", "item", "in-bytes", "role", "memory", False, False,
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


def speaker_maps(samples: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    """B-S4-SMAP: {sample_id: its speaker map} - one per LoCoMo(-format) unit, never one for a whole stand."""
    out: dict[str, dict[str, str]] = {}
    for s in samples:
        sid = str(s.get("sample_id")) if isinstance(s, Mapping) else None
        if not sid or sid in out:
            raise PlanError(f"a sample without an id, or one repeated: {sid!r}")
        out[sid] = speaker_map(s)
    return out


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
              truncate: Callable[[str], Any] | None = None, cuts: list | None = None) -> list[dict]:
    """The write ops of one (arm, unit), in the unit's session and item order (see part 2 of the module docstring);
    ``cuts`` gets one bool per retrieval item: whether §5.1 cut it."""
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
                text, was_cut = retrieval_bytes(it, day, truncate=truncate)
                if cuts is not None:
                    cuts.append(bool(was_cut))
                ops.append({"item": {"item_id": it.item_id, "index": n, "text": text}, "date": None})
                n += 1
    else:
        raise PlanError(f"{spec.name}: write unit {spec.granularity!r} is not one of {GRANULARITIES}")
    return ops


# ── part 3: the arm's interpreter (Q-A4-5) ─────────────────────────────────────────────────────────────────────

STDLIB_ARMS = frozenset({"nevertwice", "nevertwice-rawtext", "nevertwice-ablation", "nevertwice-ranker", "bm25-floor",
                         "letta"})
ONLY_PIP = frozenset({"pip"})
_PY_PROBE = (
    "import sys, json, importlib.metadata as md\n"
    "names = sorted({(d.metadata['Name'] or '').strip().lower().replace('_', '-') for d in md.distributions()})\n"
    "print(json.dumps({'version': sys.version.split()[0], 'executable': sys.executable,\n"
    "                  'base_executable': getattr(sys, '_base_executable', sys.executable),\n"
    "                  'prefix': sys.prefix, 'base_prefix': sys.base_prefix, 'packages': names}))\n"
)


def python_decl(python: str | os.PathLike, *, arm: str, run: Callable[..., Any] = subprocess.run) -> dict:
    """Q-A4-5: the declaration of the interpreter ``arm`` runs on (see part 3 of the module docstring); PlanError by
    name when it cannot be read, or when an arm that needs only the standard library would run on anything but a venv
    with no package besides pip."""
    p = Path(python)
    if not p.is_absolute() or not p.is_file():
        raise PlanError(f"{arm}: the interpreter {python!r} is not an absolute path to a file")
    env = {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows")} if os.name == "nt" else {}
    try:
        out = run([str(p), "-I", "-c", _PY_PROBE], capture_output=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PlanError(f"{arm}: the interpreter {p} did not answer: {type(e).__name__}") from None
    if out.returncode != 0:
        raise PlanError(f"{arm}: the interpreter {p} exited {out.returncode} on the declaration probe")
    try:
        d = json.loads(out.stdout)
    except ValueError:
        raise PlanError(f"{arm}: the interpreter {p} gave no declaration") from None
    base = Path(d["base_executable"])
    if not base.is_file():
        raise PlanError(f"{arm}: the base interpreter {base} of {p} is not a file")
    decl = {"python": str(p), "version": d["version"], "base_executable": str(base), "base_sha256": _sha256_file(base),
            "venv": d["prefix"] != d["base_prefix"], "packages": list(d["packages"])}
    if arm in STDLIB_ARMS:
        if not decl["venv"]:
            raise PlanError(f"{arm}: {p} is a base interpreter, not a venv - its site-packages is pinned by nothing "
                            f"(Q-A4-5: a venv with no package but pip)")
        extra = sorted(set(decl["packages"]) - ONLY_PIP)
        if extra:
            raise PlanError(f"{arm}: its venv {p} holds packages besides pip: {extra} (Q-A4-5: the arm needs only the "
                            f"standard library)")
    return decl


# ── part 3: launching an arm (Q-A4-6) ──────────────────────────────────────────────────────────────────────────

SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")        # B-P2: every adapter's run and unit id
ARMS_DIR = HERE / "arms"
CODE_SOURCES = {"base.py": ARMS_DIR / "base.py", "_http_count.py": ARMS_DIR / "_http_count.py",
                "_ollama_pacer.py": HERE.parent / "_ollama_pacer.py", "_rest.py": ARMS_DIR / "_rest.py"}
RUNNER_LLM = ("nevertwice", "nevertwice-rawtext", "nevertwice-ablation")
PROXY_TOKEN_ARMS = {**{a: "DEEPSEEK_API_KEY" for a in (*RUNNER_LLM, "mem0", "langmem", "zep-graphiti")}}
LETTA_CONTAINER_HOST = "host.docker.internal"
EXTRA_NEEDED = {**{a: ("extract_temp",) for a in RUNNER_LLM}, "a-mem": ("llm", "product_pin"),
                "zep-graphiti": ("falkor_host", "falkor_port"), "letta": ("server_url", "openapi_sha256", "agent")}


def check_ids(runs: Iterable[str], units: Iterable[str]) -> None:
    """B-P2 (see part 3 of the module docstring)."""
    bad = [x for x in (*runs, *units) if not (isinstance(x, str) and SAFE_ID.fullmatch(x)) or x.startswith("_")]
    if bad:
        raise PlanError(f"run or unit ids {bad[:3]} are not [A-Za-z0-9_-]{{1,64}} without a leading '_' - every "
                        f"adapter's id (no dot: /u/<run>.<unit>, Q3); '_' names the harness's own dirs")


def adapter_constant(adapter: str, name: str) -> Any:
    """A literal constant of an adapter, read from its source - never imported (a competitor's entry point)."""
    tree = ast.parse((ARMS_DIR / adapter).read_bytes().decode("utf-8"))
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == name for t in n.targets):
            return ast.literal_eval(n.value)
    raise PlanError(f"{adapter} declares no {name}")


_RUNNER: list = []


def _runner():
    """runner_nevertwice's own declared_env - the harness builds its children's environment with the child's function.
    Loaded under a private name; the sys.path entries and the bare "base" module its import adds are taken back."""
    if not _RUNNER:
        path_before, had_base = list(sys.path), "base" in sys.modules
        try:
            _RUNNER.append(_load("v3_runner_nevertwice_for_plan", ARMS_DIR / "runner_nevertwice.py"))
        finally:
            sys.path[:] = path_before
            if not had_base:
                sys.modules.pop("base", None)
    return _RUNNER[0]


class PlanLauncher:
    """Q-A4-6: one arm's children on one stand (see part 3 of the module docstring). ``proxy``: the run's ProxyHandle
    (its ports and tokens); ``unit_block``: each unit's block; ``unit_chars``: each unit's length in characters (the
    ablation's window, Q14); ``extra``: the run config's values the arm needs (EXTRA_NEEDED) and nothing else."""

    def __init__(self, arm: str, *, stand: str, python: str | os.PathLike, proxy: Any, stager: CodeStager,
                 unit_block: Mapping[str, str], embed_tag: str, dated: bool, unit_chars: Mapping[str, int] | None = None,
                 extra: Mapping[str, Any] | None = None) -> None:
        self.spec = arm_spec(arm)
        self.arm, self.stand, self.python = arm, stand, Path(python)
        if not self.python.is_absolute():
            raise PlanError(f"{arm}: its interpreter {python!r} is not an absolute path")
        self.proxy, self.stager, self.unit_block = proxy, stager, dict(unit_block)
        check_ids((), self.unit_block)
        self.embed_tag, self.dated, self.unit_chars = embed_tag, bool(dated), dict(unit_chars or {})
        self.extra = dict(extra or {})
        need = EXTRA_NEEDED.get(arm, ())
        wrong = sorted(set(need) ^ set(self.extra))
        if wrong:
            raise PlanError(f"{arm}: the run config's values for it are {sorted(self.extra)}, it takes {list(need)}")
        if arm == "a-mem" and self.extra["llm"] not in ("deepseek", "ollama"):
            raise PlanError(f"a-mem: llm {self.extra['llm']!r} is deepseek or ollama")
        self.spec_keys = tuple(adapter_constant(self.spec.adapter, "SPEC_KEYS"))
        self.copies: dict[tuple[str, str], tuple[Path, dict]] = {}

    # the proxy's side
    def _port(self, role: str) -> int | None:
        return ((getattr(self.proxy, "ports", {}) or {}).get("arms", {}).get(self.arm) or {}).get(role)

    def _need_port(self, role: str) -> int:
        p = self._port(role)
        if not isinstance(p, int):
            raise PlanError(f"{self.arm}: the proxy has no {role} port for it")
        return p

    def _url(self, role: str, run: str, unit: str, suffix: str = "") -> str:
        rp = _load("v3_run_proxy_for_plan", HERE / "run_v3_proxy.py")
        return rp.url(self.proxy, self.arm, run, unit, role=role, suffix=suffix)

    def token_name(self) -> str | None:
        if self.arm == "a-mem":
            return "OPENAI_API_KEY" if self.extra["llm"] == "deepseek" else None
        return PROXY_TOKEN_ARMS.get(self.arm)

    def _max_transcript(self, unit: str) -> int | None:
        if self.arm != "nevertwice-ablation":
            return None
        n = self.unit_chars.get(unit)
        if not (isinstance(n, int) and n > 0):
            raise PlanError(f"nevertwice-ablation: no length in characters for unit {unit} (its window, Q14)")
        return n

    # the child
    def spec_for(self, stage: str, *, stand: str, run: str, unit: str, dirs: Any, write_dirs: Any) -> dict:
        check_ids((run,), (unit,))
        if stand != self.stand:
            raise PlanError(f"{self.arm}: its launcher is for {self.stand}, not {stand}")
        memory = self.spec.store_persistence == "memory"
        if stage == "read" and memory:
            raise PlanError(f"{self.arm}: a memory arm reads in its write process - no read child (Q25(4))")
        store_dirs = dirs if stage == "write" else write_dirs
        if store_dirs is None:
            raise PlanError(f"{self.arm}: a read stage without its write stage's directories")
        a, ad = self.arm, self.spec.adapter
        # R-EMBED-PATH (the auditor): every arm reaches Ollama through its own proxy leg, tagged with the unit - one
        # witness of calls, embed_at_cap, the unit and the pacing for all; an arm without a leg is refused, never direct
        leg = (lambda: (self._need_port("ollama"), self._url("ollama", run, unit))[1]) if ad != "arm_letta.py" else None
        s: dict[str, Any] = {"arm": a, "stage": "both" if memory else stage, "stand": stand, "run": run, "unit": unit,
                             "unit_dir": str(Path(store_dirs.cwd)),
                             "record_path": str(Path(dirs.home) / f"start.{stage}.json")}
        if ad == "runner_nevertwice.py":
            llm = a in RUNNER_LLM
            s.update(port=self._need_port("write") if llm else None, ollama_port=self._need_port("ollama"),
                     embed_tag=self.embed_tag,
                     extract_temp=self.extra.get("extract_temp") if llm else None,
                     max_transcript=self._max_transcript(unit), s7=base_stand(stand) == "S7" and a == "nevertwice")
        elif ad == "arm_chroma.py":
            s.update(embed_tag=self.embed_tag, ollama_url=leg())
        elif ad in ("arm_mem0.py", "arm_langmem.py"):
            s.update(port=self._need_port("write") if a in ("mem0", "langmem") else None, embed_tag=self.embed_tag,
                     ollama_url=leg(), dated=self.dated)
        elif ad == "arm_amem.py":
            llm = self.extra["llm"]
            s.update(llm=llm, llm_model=adapter_constant(ad, "LLMS")[llm],
                     port=self._need_port("write") if llm == "deepseek" else None,
                     ollama_leg_url=self._url("ollama", run, unit) if llm == "ollama" else None,
                     embed_tag=self.embed_tag, ollama_url=leg(), dated=self.dated,
                     product_pin=self.extra["product_pin"])
        elif ad == "arm_graphiti.py":
            s.update(port=self._need_port("write"), embed_tag=self.embed_tag, ollama_url=leg(),
                     falkor_host=self.extra["falkor_host"], falkor_port=self.extra["falkor_port"], dated=self.dated)
        elif ad == "arm_letta.py":
            s.update(server_url=self.extra["server_url"], openapi_sha256=self.extra["openapi_sha256"],
                     port=self._need_port("write"), ollama_leg_port=self._need_port("ollama"),
                     container_host=LETTA_CONTAINER_HOST, embed_tag=self.embed_tag, dated=self.dated,
                     agent=self.extra["agent"])
        if set(s) != set(self.spec_keys):
            raise PlanError(f"{a}: the plan's spec keys differ from {ad}'s SPEC_KEYS by {sorted(set(s) ^ set(self.spec_keys))}")
        return s

    def declared(self) -> dict:
        """The arm's constant values: its proxy token under the one name its adapter checks."""
        n = self.token_name()
        if n is None:
            return {}
        tok = (getattr(self.proxy, "tokens", {}) or {}).get(self.arm)
        if not tok:
            raise PlanError(f"{self.arm}: the proxy has no token for it")
        return {n: tok}

    def declared_for(self, stage: str, *, stand: str, run: str, unit: str, dirs: Any, write_dirs: Any) -> dict:
        """The values that name the unit (condition 2: the arm's own port, /u/<run>.<unit> of this unit)."""
        check_ids((run,), (unit,))
        if self.spec.adapter == "runner_nevertwice.py":
            llm = self.arm in RUNNER_LLM
            return _runner().declared_env(self.arm, run=run, unit=unit,
                                          port=self._need_port("write") if llm else None, embed_tag=self.embed_tag,
                                          ollama_port=self._need_port("ollama"),
                                          extract_temp=self.extra.get("extract_temp") if llm else None,
                                          max_transcript=self._max_transcript(unit))
        if self.arm == "a-mem":
            if self.extra["llm"] == "deepseek":
                return {"OPENAI_BASE_URL": self._url("write", run, unit, "/v1")}
            return {"OLLAMA_API_BASE": self._url("ollama", run, unit)}
        return {}

    def script(self) -> Path:
        return ARMS_DIR / self.spec.adapter

    def argv_exception(self) -> dict | None:
        """Q9, condition 4: our arm's script by its exact path at its exact index; a competitor has none."""
        return {2: str(self.script())} if self.spec.ours else None

    def _copy(self, run: str, unit: str) -> Path:
        block = self.unit_block.get(unit)
        if block is None:
            raise PlanError(f"{self.arm}: unit {unit} is in no block of the plan")
        key = (run, block)
        if key not in self.copies:
            files = {self.spec.adapter: self.script(), **{n: CODE_SOURCES[n] for n in self.spec.code}}
            out = self.stager.stage(self.stand, run, self.arm, block, files)
            self.copies[key] = (Path(out[self.spec.adapter]["path"]).parent, {n: v["sha256"] for n, v in out.items()})
        d, shas = self.copies[key]
        changed = [n for n, want in shas.items() if not (d / n).is_file() or _sha256_file(d / n) != want]
        if changed:
            raise PlanError(f"{self.arm}: its code copy {changed} in {d} changed since it was staged (Q9)")
        return d

    def argv_for(self, spec_path: str | os.PathLike, *, stage: str, stand: str, run: str, unit: str) -> list[str]:
        if self.spec.ours:
            return [str(self.python), "-B", str(self.script()), str(spec_path)]
        return [str(self.python), "-B", str(self._copy(run, unit) / self.spec.adapter), str(spec_path)]

    def launcher(self, child_arm_launcher: Any) -> Any:
        """The scheduler's ChildArmLauncher for this arm (``child_arm_launcher``: the class, the scheduler's own)."""
        exc = self.argv_exception()
        if (exc is not None) != self.spec.ours:
            raise PlanError(f"{self.arm}: an argv exception is for our arms alone (Q9, Q-A4-6 (4)) - "
                            f"{'our arm without its own' if self.spec.ours else 'a competitor given one'}")
        n = self.token_name()
        return child_arm_launcher(self.arm, argv_for=self.argv_for, spec_for=self.spec_for,
                                  store_persistence=self.spec.store_persistence, path_dirs=(str(self.python.parent),),
                                  declared=self.declared(), token_names=(n,) if n else (),
                                  reads_point=self.spec.reads_point, declared_for=self.declared_for, argv_exception=exc)

    def record(self, *, interpreter: Mapping[str, Any] | None = None) -> dict:
        """The arm's launch record for the run record: names and paths, never a token (``interpreter``: python_decl)."""
        n = self.token_name()
        return {"arm": self.arm, "adapter": self.spec.adapter, "ours": self.spec.ours, "python": str(self.python),
                "interpreter": dict(interpreter) if interpreter else None, "path_dirs": [str(self.python.parent)],
                "token_names": [n] if n else [], "declared_names": sorted(self.declared()),
                "argv_exception": self.argv_exception(), "spec_keys": list(self.spec_keys),
                "ports": {r: self._port(r) for r in ("write", "reader", "ollama")},
                "ollama_route": (f"http://127.0.0.1:{self._port('ollama')}/u/<run>.<unit>"          # R-EMBED-PATH
                                 if isinstance(self._port("ollama"), int) else None),
                "store_persistence": self.spec.store_persistence,
                "code_copies": [r for r in self.stager.records if r["arm"] == self.arm and r["stand"] == self.stand]}


# ── part 3: the answer (§4.3a, §5.2, §8.1, Q8) ──────────────────────────────────────────────────────────────────

def _mod(name: str, rel: str):
    return _load(name, HERE / rel)


class Answerer:
    """StandPlan.answer (see part 3 of the module docstring). ``questions``: (unit, qid) -> (the question's template,
    the question as the benchmark asks it); ``reader(arm, run, unit)`` -> (the arm's reader port, the chat path under
    /u/<run>.<unit>, the arm's token); ``post(port, path, body, token, timeout=)`` -> (status, parsed body) - run_v3_proxy's;
    ``count`` and ``cut`` the §5.2 cl100k counter and cut; ``reaskable`` the scheduler's ReaskableError."""

    def __init__(self, stand: str, *, questions: Mapping[tuple[str, str], tuple[Any, str]],
                 reader: Callable[[str, str, str], tuple[int, str, str]], post: Callable[..., tuple[int | None, Any]],
                 count: Callable[[str], int], cut: Callable[[str, int], str], answers_root: str | os.PathLike,
                 reaskable: type, long_form: bool = False, timeout: float = 120.0) -> None:
        self.stand, self.questions, self.reader, self.post = stand, dict(questions), reader, post
        self.count, self.cut, self.reaskable, self.long_form, self.timeout = count, cut, reaskable, long_form, timeout
        self.root = Path(answers_root) / stand / "_answers"
        self.key_question: dict[tuple[str, str], str] = {}
        self.key_collisions = 0
        self._lock = threading.Lock()                     # the scheduler answers a turn's units at once

    def _keep_keys(self, unit: str, qid: str, keys: Sequence[str]) -> None:
        with self._lock:
            for k in keys:
                prev = self.key_question.setdefault((unit, k), qid)
                if prev != qid:
                    self.key_collisions += 1

    def __call__(self, arm: str, run: str, unit: str, req: Any, got: Mapping[str, Any]) -> dict:
        check_ids((run,), (unit,))
        if (unit, req.qid) not in self.questions:
            raise PlanError(f"{self.stand}/{unit}: question {req.qid!r} is not in the plan")
        template, question = self.questions[(unit, req.qid)]
        items = got.get("items") if isinstance(got, Mapping) else None
        texts = [it.get("text") for it in items] if isinstance(items, list) and all(
            isinstance(it, Mapping) for it in items) else None
        if texts is None or not all(isinstance(x, str) for x in texts):
            raise PlanError(f"{arm}/{run}/{unit}: the read of {req.qid!r} returned no list of item texts")
        pts, tpl = _mod("v3_points_for_plan", "points.py"), _mod("v3_templates_for_plan", "templates.py")
        rj = _mod("v3_reader_judge_for_plan", "reader_judge.py")
        request_key = _load("v3_llm_proxy_for_plan", HERE.parent / "_llm_proxy.py").request_key
        ctx = pts.fill(texts, count=self.count, cut=self.cut)
        prompt = tpl.render(template, {"context": ctx.text, "question": question})
        port, path, token = self.reader(arm, run, unit)
        keys: list[str] = []

        def post(body: Mapping[str, Any]) -> Mapping[str, Any]:
            keys.append(request_key(body, arm))
            status, resp = self.post(port, path, body, token, timeout=self.timeout)
            if status != 200 or not isinstance(resp, Mapping):
                raise self.reaskable(f"reader: {arm}/{run}/{unit}/{req.qid}: status {status}")
            return resp

        try:
            res = rj.read(prompt, post, long_form=self.long_form)
        except rj.ReaderJudgeError as e:
            raise self.reaskable(f"reader: {arm}/{run}/{unit}/{req.qid}: {e}") from None
        finally:
            self._keep_keys(unit, req.qid, keys)
        d = self.root / run / arm / unit
        d.mkdir(parents=True, exist_ok=True)
        f = d / (hashlib.sha256((req.qid + "\0" + req.point).encode("utf-8")).hexdigest() + ".json")
        blob = json.dumps({"stand": self.stand, "run": run, "arm": arm, "unit": unit, "qid": req.qid,
                           "point": req.point, "question": question, "context": ctx.text, "reply": res.text,
                           "short_answer": res.short_answer, "request_keys": keys}, ensure_ascii=False,
                          sort_keys=True).encode("utf-8")
        try:
            with open(f, "xb") as fh:
                fh.write(blob)
        except FileExistsError:
            raise PlanError(f"{f}: an answer for {req.qid!r} at point {req.point} was already written") from None
        return {"qid": req.qid, "point": req.point, "context_sha256": text_sha256(ctx.text),
                "context_tokens": ctx.tokens, "items_offered": ctx.items_offered, "items_used": ctx.items_used,
                "last_cut": ctx.last_cut, "prompt_sha256": text_sha256(prompt), "reply_sha256": text_sha256(res.text),
                "short_answer_sha256": text_sha256(res.short_answer),
                "short_answer_words": len(res.short_answer.split()), "reasked": res.reasked,
                "format_failure": res.format_failure, "overlong": res.overlong, "thinking_seen": res.thinking_seen,
                "usage": dict(res.usage), "request_keys": keys, "text_file": str(f.relative_to(self.root.parent)),
                "text_sha256": hashlib.sha256(blob).hexdigest()}


# ── part 3: the stand plan ─────────────────────────────────────────────────────────────────────────────────────

class PlanState:
    """What the plan's callbacks record during a run, for the run records: per (arm, run, unit) the §5.1 cuts and the
    sha256 of every op's bytes (Q-45-4); record_for is StandPlan.record_extra."""

    def __init__(self, *, smap_sha256: Mapping[str, str | None] | None = None,
                 bodies_dir: str | os.PathLike | None = None) -> None:
        self._lock = threading.Lock()
        self.cuts: dict[tuple[str, str, str], list] = {}
        self.shas: dict[tuple[str, str, str], dict] = {}
        self.smap_sha256 = dict(smap_sha256 or {})            # B-S4-SMAP: unit -> its speaker map's sha256
        self.bodies_dir = Path(bodies_dir) if bodies_dir is not None else None

    def truncation(self, arm: str, run: str, units: Iterable[str] | None = None) -> dict | str:
        """{items, truncated, share} over the arm-run's units (all of them, or ``units``)."""
        if ARMS[arm].granularity != "item":
            return "not-applicable: the product embeds its own text"
        with self._lock:
            c = [x for (a, r, u), v in self.cuts.items() if (a, r) == (arm, run) and (units is None or u in units)
                 for x in v]
        return {"items": len(c), "truncated": sum(c), "share": (sum(c) / len(c)) if c else 0.0}

    def bodies_sha256(self, arm: str, run: str, units: Sequence[str]) -> dict | None:
        """Q-A5-1: each unit's bodies file (the proxy's bodies/<arm>/<run>.<unit>.jsonl) by its sha256, None where the
        unit's writer sent nothing; None when the plan was given no proxy run directory."""
        if self.bodies_dir is None:
            return None
        out = {}
        for u in units:
            f = self.bodies_dir / "bodies" / arm / f"{run}.{u}.jsonl"
            out[u] = _sha256_file(f) if f.is_file() else None
        return out

    def record_for(self, arm: str, run: str, units: Sequence[str]) -> dict:
        """The plan's record of one arm-run in a block: the truncation over the block's units, each op's sha256, the
        speaker map's sha (Q-A4-1), the bodies files' sha256 (Q-A5-1) - the write stage is over when it is called."""
        with self._lock:
            shas = {u: dict(self.shas.get((arm, run, u), {})) for u in units}
        return {"truncation": self.truncation(arm, run, set(units)), "item_sha256": shas,
                "speaker_map_sha256": {u: self.smap_sha256.get(u) for u in units},
                "bodies_sha256": self.bodies_sha256(arm, run, units)}


def stand_plan(stand: str, units: Sequence[Any], launchers: Mapping[str, Any], *, standplan: Any, read_req: Any,
               runs: Sequence[str], campaign_seed: int, unit_tokens: Mapping[str, int], medians: Mapping[tuple, float],
               answer: Callable[..., dict], embed_tag: str | None, dated: bool, points: Callable[[str], Sequence[str]],
               k_at: Mapping[str, int], smaps: Mapping[str, Mapping[str, str]] | None = None,
               truncate: Callable[[str], Any] | None = None,
               bodies_dir: str | os.PathLike | None = None) -> tuple[Any, PlanState]:
    """(the scheduler's StandPlan, the PlanState its callbacks fill). ``standplan``/``read_req``: the scheduler's
    StandPlan and ReadReq classes; ``points(arm)``: the points the arm reads on this stand (the smoke: B);
    ``smaps``: unit id -> its speaker map (speaker_maps; B-S4-SMAP) - a unit not in it has none."""
    check_ids(runs, [u.unit_id for u in units])
    by_id = {u.unit_id: u for u in units}
    missing = sorted(set(launchers) - set(ARMS))
    if missing:
        raise PlanError(f"{stand}: arms {missing} have no plan (A8: no adapter yet, or not an arm)")
    for u in units:
        _check_unit_dates(u, dated)
    smaps = dict(smaps or {})
    stray = sorted(set(smaps) - set(by_id))
    if stray:
        raise PlanError(f"{stand}: speaker maps for units the stand does not have: {stray[:3]}")
    st = PlanState(smap_sha256={u: map_sha256(smaps.get(u)) for u in by_id}, bodies_dir=bodies_dir)

    def write_ops_for(arm: str, run: str, unit: str) -> list[dict]:
        cuts: list = []
        ops = write_ops(ARMS[arm], by_id[unit], dated=dated, smap=smaps.get(unit), truncate=truncate, cuts=cuts)
        with st._lock:
            st.cuts[(arm, run, unit)] = cuts
            st.shas[(arm, run, unit)] = item_shas(ops)
        return ops

    def read_plan_for(arm: str, unit: str) -> list:
        return read_plan(by_id[unit], points=tuple(points(arm)), read_req=read_req, k_at=k_at)

    sp = standplan(stand=stand, runs=tuple(runs), launchers=dict(launchers), campaign_seed=campaign_seed,
                   unit_tokens=dict(unit_tokens), medians=dict(medians), write_ops=write_ops_for,
                   read_plan=read_plan_for, answer=answer, embed_tag=embed_tag, commit="", dirty=True,
                   record_extra=st.record_for)
    return sp, st


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
