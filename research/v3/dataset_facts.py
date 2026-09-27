#!/usr/bin/env python3
"""PREREG-V3 plan step A3.j: the dataset facts (the rev-2 slot "dataset-facts"), by the auditor's rulings D-J1..D-J8.

Recorded from the pinned files, each verified (sha256, size) before it is read. The reading runs in a child on the
v3_data venv (pyarrow for BEAM's and MAB's parquet), spawned under the contract as an offline step; the child prints
only counts, labels, ids and booleans - every string it prints must be a label ([A-Za-z0-9_.:-]{1,64}), so no
question, answer or content can leave it - and the harness refuses its output otherwise. The facts are written to
<runs>/_facts/<run>/facts.json; revision 2 of the PREREG takes them from there (A10).

The rules, fixed before any fact was computed (the auditor's rulings of 2026-09-26):
* D-J1 AMA SWE = the domain named "SOFTWARE" (by name; no pinned upstream source names the SWE domain - identified by
  name among the domain census); its trajectories must be 34 or 36 and its questions 432, else a named problem -
  never a reason to pick another domain; a second software-like value is a named problem.
* D-J2 BEAM (the 100K parquet, the 128K split, config label "100K"): conversations = rows; probing_questions is a
  Python literal (ast.literal_eval, never eval); questions = the sum over abilities; ability names sorted, as found,
  each placed on the §8.4 lists or listed as long-form (C7).
* D-J3 BEAM dates: present if every session carries at least one non-empty time_anchor, absent if none does, else a
  named problem; anchors per session and whether each sits on message 0 are recorded.
* D-J4 FC: the question count per metadata.source; the sources must be exactly the 8.
* D-J5 LoCoMo (the v2 pin): the per-category counts must be the multiset {841, 282, 321, 96, 446}.
* D-J6 gold evidence per stand: S2/S1 LME answer_session_ids; S4 LoCoMo qa evidence; S5 BEAM source_chat_ids (lists
  and dicts at any depth, flattened to their int/str leaves - any other leaf named - pointing at chat message ids,
  coverage per ability); S6 FC metadata.haystack_sessions (null -> absent); S7 AMA none (absent). Questions without ids
  are named for §9.3. P-J11 (j2 stopped on a nested list): the reading is DECLARED, not proved - every leaf is a message
  id and a nested list is a group of ids. D-J6 cannot tell [session, id] pairs from groups, because BEAM's message ids
  start at 0 in every conversation, so the record carries the evidence instead: the shape label counts, and for each
  question with a nested list its inner lengths and whether an inner list starts below that conversation's session
  count.
* D-J7 smoke: S7 only - the non-SOFTWARE trajectory closest to the SOFTWARE median by compact-JSON length.
* D-J8 LME: questions per file, by question_type, and how many carry answer_session_ids.

    python research/v3/dataset_facts.py --run f1
"""
from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import importlib.util
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOFTWARE = "SOFTWARE"
SWE_TRAJECTORIES = (34, 36)
AMA_QUESTIONS = 432
BEAM_EXPECT = {"conversations": 20, "questions": 400}
FC_SOURCES = sorted(f"factconsolidation_{k}_{n}" for k in ("sh", "mh") for n in ("6k", "32k", "64k", "262k"))
LOCOMO_COUNTS = (841, 282, 321, 96, 446)
#: §8.4, by name (lower case, "_"/"-" as spaces).
LIST_84 = {"short": {"abstention", "contradiction resolution", "information extraction", "knowledge update",
                     "multi session reasoning", "temporal reasoning"},
           "ordering": {"event ordering"}, "long": {"instruction following", "preference following", "summarization"}}
LABEL = re.compile(r"[A-Za-z0-9_.:\-]{1,64}")
#: The pins read, by stand.
FILES = {"ama": "ama_swe", "beam": "beam_128k", "fc": "mab_conflict_resolution", "locomo": "locomo10",
         "lme_s": "lme_s_cleaned", "lme_m": "lme_m_cleaned", "lme_oracle": "lme_oracle_cleaned", "lme_v2": "longmemeval_s"}
#: The pins the keys-only mode (j4, A6 TB4.3b / Q16) reads.
KEYS_FILES = {"beam": "beam_128k", "fc": "mab_conflict_resolution", "ama": "ama_swe"}
SHAPE_DEPTH = 8


class FactsRefused(RuntimeError):
    pass


def _norm(name: str) -> str:
    return re.sub(r"[_\-\s]+", " ", name).strip().lower()


# ── the rules, pure ───────────────────────────────────────────────────────────────────────────────────

def ama_facts(rows: list[dict]) -> tuple[dict, list[str]]:
    census = collections.Counter(r.get("domain") for r in rows)
    problems = []
    software_like = sorted(d for d in census if isinstance(d, str) and d != SOFTWARE and re.search(r"(?i)software|\bswe\b", d))
    if software_like:
        problems.append(f"D-J1: a second software-like domain value: {software_like}")
    swe = [r for r in rows if r.get("domain") == SOFTWARE]
    if not swe:
        problems.append(f"D-J1: no domain named {SOFTWARE!r} among {sorted(map(str, census))}")
    n_q = sum(len(r.get("qa_pairs") or []) for r in swe)
    if swe and len(swe) not in SWE_TRAJECTORIES:
        problems.append(f"D-J1: {len(swe)} {SOFTWARE} trajectories is not 34 or 36")
    if swe and n_q != AMA_QUESTIONS:
        problems.append(f"D-J1: {n_q} {SOFTWARE} questions is not {AMA_QUESTIONS}")
    facts = {"domain_census": {str(k): v for k, v in sorted(census.items(), key=lambda kv: str(kv[0]))},
             "swe_domain": SOFTWARE, "swe_trajectories": len(swe), "swe_questions": n_q,
             "gold_evidence": {"field": "qa_pairs.evidence", "verdict": "absent"}}
    return facts, problems


def s7_smoke(rows: list[dict], pick) -> dict:
    """D-J7: the non-SOFTWARE trajectory closest to the SOFTWARE median (``pick`` is smoke_rules.s7_pick)."""
    swe = [r["trajectory"] for r in rows if r.get("domain") == SOFTWARE]
    others = [r for r in rows if r.get("domain") != SOFTWARE]
    i = pick([r["trajectory"] for r in others], swe)
    return {"episode_id": others[i].get("episode_id"), "domain": others[i].get("domain"), "index_in_non_software": i}


def parse_probing(text: str) -> dict:
    """D-J2: BEAM's probing_questions is a Python literal - ast.literal_eval, never eval."""
    try:
        obj = ast.literal_eval(text)
    except (ValueError, SyntaxError) as e:
        raise FactsRefused(f"D-J2: probing_questions does not parse as a literal: {type(e).__name__}") from None
    if not isinstance(obj, dict):
        raise FactsRefused("D-J2: probing_questions is not a mapping of abilities")
    return obj


#: The reading of BEAM's source_chat_ids, declared in the record (P-J11) - D-J6 cannot prove it (see the docstring).
BEAM_GOLD_READING = ("every source_chat_ids leaf is a message id and a nested list is a group of ids; D-J6 cannot tell "
                     "[session, id] pairs from groups, because BEAM's message ids start at 0 in every conversation")


def _flat_ids(v) -> tuple[list, int, str, list[list]]:
    """source_chat_ids flattened over lists and dicts at any depth: (the id leaves - int or str, never bool; the number
    of other leaves; the shape label - each leaf's container chain such as "list" or "dict.list.list", "_"-joined, or
    "empty"; the inner lists - every list that sits inside a list)."""
    ids, chains, inner, bad = [], set(), [], 0

    def walk(x, chain: str, in_list: bool) -> None:
        nonlocal bad
        if isinstance(x, dict):
            for y in x.values():
                walk(y, f"{chain}.dict" if chain else "dict", False)
        elif isinstance(x, (list, tuple)):
            if in_list:
                inner.append(list(x))
            for y in x:
                walk(y, f"{chain}.list" if chain else "list", True)
        elif isinstance(x, (int, str)) and not isinstance(x, bool):
            ids.append(x)
            chains.add(chain or "scalar")
        else:
            bad += 1
            chains.add(chain or "scalar")

    if v is not None:
        walk(v, "", False)
    return ids, bad, "_".join(sorted(chains)) or "empty", inner


def beam_facts(rows: list[dict]) -> tuple[dict, list[str]]:
    problems, per_ability, names = [], collections.Counter(), set()
    sessions = anchored = on_first = messages = 0
    covered, total_by, ids_found, ids_total, missing = collections.Counter(), collections.Counter(), 0, 0, []
    shapes, nested = collections.Counter(), {}
    for ci, row in enumerate(rows):
        pq = parse_probing(row["probing_questions"])
        chat = row.get("chat") or []
        msg_ids = {m.get("id") for s in chat for m in (s or [])}
        for s in chat:
            s = s or []
            sessions += 1
            messages += len(s)
            anchors = [m for m in s if str(m.get("time_anchor") or "").strip()]
            anchored += bool(anchors)
            on_first += bool(s) and bool(str(s[0].get("time_anchor") or "").strip())
        for ability, qs in pq.items():
            names.add(ability)
            per_ability[ability] += len(qs or [])
            for qi, q in enumerate(qs or []):
                total_by[ability] += 1
                ids, n_bad, shape, inner = _flat_ids((q or {}).get("source_chat_ids"))
                shapes[shape] += 1
                if n_bad:
                    problems.append(f"D-J6: c{ci}:{ability}:{qi} has {n_bad} source_chat_ids leaf/leaves that are not an "
                                    "id (int or str)")
                if inner:
                    nested[f"c{ci}:{ability}:{qi}"] = {
                        "inner_lengths": [len(x) for x in inner],
                        "first_below_sessions": any(bool(x) and isinstance(x[0], int) and not isinstance(x[0], bool)
                                                    and x[0] < len(chat) for x in inner)}
                if ids:
                    covered[ability] += 1
                else:
                    missing.append(f"c{ci}:{ability}:{qi}")
                ids_total += len(ids)
                ids_found += sum(1 for x in ids if x in msg_ids)
    if sessions and 0 < anchored < sessions:
        problems.append(f"D-J3: {anchored} of {sessions} sessions carry a time_anchor - neither all nor none")
    dates = "present" if sessions and anchored == sessions else ("absent" if anchored == 0 else "mixed")
    q_total = sum(per_ability.values())
    for k, want in (("conversations", len(rows)), ("questions", q_total)):
        if want != BEAM_EXPECT[k]:
            problems.append(f"D-J2: {want} {k} is not {BEAM_EXPECT[k]}")
    if ids_found != ids_total:
        problems.append(f"D-J6: {ids_total - ids_found} source_chat_ids point at no chat message")
    placed = {n: next((lst for lst, members in LIST_84.items() if _norm(n) in members), "long (not on §8.4, C7)")
              for n in sorted(names)}
    facts = {"config_label": "100K", "conversations": len(rows), "questions": q_total,
             "per_ability": dict(sorted(per_ability.items())), "abilities_84": placed,
             "dates": {"verdict": dates, "sessions": sessions, "sessions_anchored": anchored,
                       "anchor_on_message_0": on_first, "messages": messages},
             "gold_evidence": {"field": "source_chat_ids", "verdict": "present" if ids_total else "absent",
                               "ids": ids_total, "ids_found_in_chat": ids_found,
                               "coverage": {a: {"covered": covered[a], "total": total_by[a]} for a in sorted(total_by)},
                               "without_ids": missing, "reading": "leaf_is_message_id.nested_list_is_group",
                               "shapes": dict(sorted(shapes.items())), "nested": dict(sorted(nested.items()))}}
    return facts, problems


def fc_facts(rows: list[dict]) -> tuple[dict, list[str]]:
    counts = {r["metadata"]["source"]: len(r.get("questions") or []) for r in rows}
    problems = [] if sorted(counts) == FC_SOURCES else [f"D-J4: the sources are {sorted(counts)}, not the 8"]
    hay = [r["metadata"].get("haystack_sessions") for r in rows]
    verdict = "absent" if all(h is None for h in hay) else "present" if all(h is not None for h in hay) else "mixed"
    if verdict == "mixed":
        problems.append("D-J6: haystack_sessions is null in some FC rows only")
    return {"questions_per_row": dict(sorted(counts.items())),
            "gold_evidence": {"field": "metadata.haystack_sessions.has_answer", "verdict": verdict}}, problems


def locomo_facts(samples: list[dict]) -> tuple[dict, list[str]]:
    by_cat, missing, n = collections.Counter(), [], 0
    for si, s in enumerate(samples):
        for qi, q in enumerate(s.get("qa") or []):
            n += 1
            by_cat[q.get("category")] += 1
            if not q.get("evidence"):
                missing.append(f"s{si}:qa{qi}")
    counts = sorted(by_cat.values(), reverse=True)
    problems = [] if counts == sorted(LOCOMO_COUNTS, reverse=True) else [
        f"D-J5: the category counts {dict(sorted(by_cat.items(), key=lambda kv: str(kv[0])))} are not {LOCOMO_COUNTS} - re-pin before the anchor"]
    return {"questions": n, "per_category": {str(k): v for k, v in sorted(by_cat.items(), key=lambda kv: str(kv[0]))},
            "gold_evidence": {"field": "qa.evidence", "verdict": "present" if n - len(missing) else "absent",
                              "with_evidence": n - len(missing), "without": missing}}, problems


def lme_facts(items: list[dict]) -> dict:
    by_type = collections.Counter(q.get("question_type") for q in items)
    with_ids = sum(1 for q in items if q.get("answer_session_ids"))
    return {"questions": len(items), "by_question_type": {str(k): v for k, v in sorted(by_type.items(), key=lambda kv: str(kv[0]))},
            "gold_evidence": {"field": "answer_session_ids", "verdict": "present" if with_ids else "absent",
                              "with_ids": with_ids}}


def iter_json_array(path, chunk: int = 1 << 22):
    """The elements of a top-level JSON array, one at a time (LME-M is 2.7 GB: json.load would hold ~15 GB)."""
    dec = json.JSONDecoder()
    buf, pos, started = "", 0, False
    with open(path, encoding="utf-8") as f:
        eof = False
        while True:
            while pos < len(buf) and buf[pos] in " \t\r\n,":
                pos += 1
            if not started:
                if pos < len(buf):
                    if buf[pos] != "[":
                        raise FactsRefused(f"{Path(path).name}: not a JSON array")
                    started, pos = True, pos + 1
                    continue
            elif pos < len(buf) and buf[pos] == "]":
                return
            elif pos < len(buf):
                try:
                    obj, end = dec.raw_decode(buf, pos)
                    yield obj
                    buf, pos = buf[end:], 0
                    continue
                except json.JSONDecodeError:
                    if eof:
                        raise FactsRefused(f"{Path(path).name}: a truncated JSON array") from None
            if eof:
                raise FactsRefused(f"{Path(path).name}: a truncated JSON array")
            more = f.read(chunk)
            if not more:
                eof = True
            buf = buf[pos:] + more
            pos = 0


def scan_labels(obj, path: str = "") -> list[str]:
    """Every string leaf and key of the child's output must be a label - never a question, answer or content."""
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not LABEL.fullmatch(str(k)):
                bad.append(f"{path}/<key>")
            bad += scan_labels(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad += scan_labels(v, f"{path}[{i}]")
    elif isinstance(obj, str) and not LABEL.fullmatch(obj):
        bad.append(path)
    return bad


# ── keys only (j4): the structure of a record, never a value ────────────────────────────────────────

def shape(obj, depth: int = 0) -> dict:
    """A value's structure with no value in it: a dict's sorted keys, each with its value's shape; a list's length and
    its first element's shape; a leaf's type name. (The auditor's j4 form: dict -> sorted keys, list -> the first
    element's type, plus counts.)"""
    if depth >= SHAPE_DEPTH:
        return {"type": "depth-limit"}
    if isinstance(obj, dict):
        return {"type": "dict", "n": len(obj), "keys": {str(k): shape(obj[k], depth + 1) for k in sorted(obj, key=str)}}
    if isinstance(obj, (list, tuple)):
        return {"type": "list", "n": len(obj), "first": shape(obj[0], depth + 1) if len(obj) else None}
    return {"type": type(obj).__name__}


def variants(items) -> list[dict]:
    """The distinct key sets among the dicts of ``items``, each with how many dicts carry it (a key present in only some
    elements is visible here, where the first element's shape alone would hide it)."""
    c = collections.Counter(tuple(sorted(map(str, d))) for d in items if isinstance(d, dict))
    return [{"keys": list(k), "n": n} for k, n in sorted(c.items())]


def _elements(x) -> list:
    return list(x) if isinstance(x, (list, tuple)) else ([x] if isinstance(x, dict) else [])


def keys_of(beam: list[dict], fc: list[dict], ama: list[dict]) -> dict:
    """j4's record, pure: the BEAM rows, their chat messages and parsed probing questions; the MAB Conflict_Resolution
    rows and their metadata; the AMA rows, their trajectory steps and QA pairs - shapes and key-set variants only."""
    msgs = [m for r in beam for s in _elements(r.get("chat")) for m in _elements(s)]
    probing = [parse_probing(r["probing_questions"]) for r in beam if isinstance(r.get("probing_questions"), str)]
    pq_items = [q for pr in probing for qs in pr.values() for q in _elements(qs)]
    steps = [s for r in ama for s in _elements(r.get("trajectory"))]
    qa = [q for r in ama for q in _elements(r.get("qa_pairs"))]
    return {
        "S5": {"rows": len(beam), "row": shape(beam[0]) if beam else None, "row_variants": variants(beam),
               "messages": len(msgs), "message_variants": variants(msgs),
               "probing": shape(probing[0]) if probing else None, "probing_question_variants": variants(pq_items)},
        "S6": {"rows": len(fc), "row": shape(fc[0]) if fc else None, "row_variants": variants(fc),
               "metadata_variants": variants([r.get("metadata") for r in fc])},
        "S7": {"rows": len(ama), "row": shape(ama[0]) if ama else None, "row_variants": variants(ama),
               "steps": len(steps), "step_variants": variants(steps), "qa_variants": variants(qa)},
    }


def read_jsonl(path) -> list[dict]:
    """One record per LF-terminated line. Never str.splitlines(): AMA's strings carry U+0085 and U+2028, on which
    splitlines() cuts a record in two (the auditor's finding, A6 j4); a text file's iteration splits on LF/CR only."""
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def child_keys(paths: dict) -> dict:
    import pyarrow.parquet as pq  # noqa: PLC0415 - only the v3_data venv has it
    ama_rows = read_jsonl(paths["ama"])
    return {"keys": keys_of(pq.read_table(paths["beam"]).to_pylist(), pq.read_table(paths["fc"]).to_pylist(), ama_rows),
            "problems": []}


# ── the child (runs on the v3_data venv) ──────────────────────────────────────────────────────────────

def child(paths: dict, smoke_rules: Path) -> dict:
    import pyarrow.parquet as pq  # noqa: PLC0415 - only the v3_data venv has it
    spec = importlib.util.spec_from_file_location("v3_smoke_rules", smoke_rules)
    SR = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(SR)
    out, problems = {}, []
    ama_rows = read_jsonl(paths["ama"])
    out["S7"], p = ama_facts(ama_rows)
    problems += p
    if not p:
        out["S7"]["smoke"] = s7_smoke(ama_rows, SR.s7_pick)
    try:
        out["S5"], p = beam_facts(pq.read_table(paths["beam"]).to_pylist())
        problems += p
    except FactsRefused as e:
        problems.append(str(e))
    out["S6"], p = fc_facts(pq.read_table(paths["fc"]).to_pylist())
    problems += p
    out["S4"], p = locomo_facts(list(iter_json_array(paths["locomo"])))
    problems += p
    for k in ("lme_s", "lme_m", "lme_oracle", "lme_v2"):
        out[k] = lme_facts([{"question_type": q.get("question_type"), "answer_session_ids": q.get("answer_session_ids")}
                            for q in iter_json_array(paths[k])])
    return {"facts": out, "problems": problems}


# ── the harness ───────────────────────────────────────────────────────────────────────────────────────

def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_facts(c, L, *, run: str, python: Path, parent_env, CP=None, native=None, fs=None, script: Path | None = None,
              mode: str = "facts") -> dict:
    """verify() every file first, then the child, its output scanned, then the record. ``mode`` "keys" is j4: the three
    KEYS_FILES only, the child's --child-keys, and the record's "keys" (structure, never a value)."""
    if mode not in ("facts", "keys"):
        raise FactsRefused(f"mode {mode!r} is facts or keys")
    CP = CP or _load("v3_corpus_pin_facts", HERE / "corpus_pin_v3.py")
    base = c.runs_root / "_facts" / run
    if base.exists():
        raise FactsRefused("this facts run label was used before")
    base.mkdir(parents=True)
    record: dict = {"run": run, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "problems": [], "files": {},
                    "swe_identification": "no pinned upstream source names the SWE domain; identified by name among the census",
                    "beam_gold_reading": BEAM_GOLD_READING}
    paths = {}
    for key, name in (FILES if mode == "facts" else KEYS_FILES).items():
        path = CP.location(name, hf_hub=c.polygon_root / "hf_cache" / "hub", pins_root=c.runs_root / "_pins")
        try:
            record["files"][key] = CP.verify(name, path)
        except CP.PinMismatch as e:
            record["problems"].append(f"reads_unverified_file refused: {e}")
            continue
        paths[key] = str(path)
    if record["problems"]:
        return _write(base, record)
    unit = L.make_unit_dirs(c, "_facts", run, "child", "f1")
    env = L.build_env(c, parent_env=parent_env, unit=unit, path_dirs=[Path(python).parent], declared={}, catcher_url="",
                      proxies=False)
    W = L.Witnesses(c, native=native if native is not None else L.NativeEgressWitness(),
                    fs=fs if fs is not None else L.FsWitness(L.watched_set(c)))
    cid = f"facts-{run}"
    W.begin_check(cid, tags={"window": "facts", "run": run, "arm": "child"})
    try:
        ch = L.spawn(c, [os.fspath(python), "-I", "-B", os.fspath(script or Path(__file__)),
                         "--child" if mode == "facts" else "--child-keys",
                         json.dumps(paths), os.fspath(HERE / "smoke_rules.py")],
                     env=env, cwd=unit.cwd, record={"role": "facts", "stand": None, "run": run, "arm": "child", "unit": "f1"},
                     parent_env=parent_env, catcher_url="", witnesses=W, requirement="required",
                     argv_exception={3: script or Path(__file__), 6: HERE / "smoke_rules.py"},
                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = ch.process.communicate(timeout=1800)
        rc = ch.process.returncode
    finally:
        chk = W.end_check(cid)
    native_rec = chk.get("native") or {}
    record["check"] = {"complete": chk.get("complete"), "native_hits": native_rec.get("hits"),
                       "fs_hits": (chk.get("fs") or {}).get("fs_hits")}
    if record["check"] != {"complete": True, "native_hits": 0, "fs_hits": 0}:
        record["problems"].append(f"the child's check is not clean: {record['check']}")
    if rc != 0:
        record["problems"].append(f"the child exited with {rc}: " + err.decode("utf-8", "replace")[-200:].replace("\n", " "))
        return _write(base, record)
    try:
        got = json.loads(out)
    except ValueError:
        record["problems"].append("the child's output is not JSON")
        return _write(base, record)
    leaks = scan_labels(got.get(mode))
    if leaks:
        record["problems"].append(f"prints_answers refused: {len(leaks)} string(s) that are not labels, e.g. {leaks[:3]}")
        return _write(base, record)
    record[mode] = got[mode]
    record["problems"] += got.get("problems") or []
    return _write(base, record)


def _write(base: Path, record: dict) -> dict:
    (base / "facts.json").write_bytes((json.dumps(record, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return record


def main(argv: list[str] | None = None) -> int:
    if argv is None and len(sys.argv) > 1 and sys.argv[1] == "--child":
        print(json.dumps(child(json.loads(sys.argv[2]), Path(sys.argv[3]))))
        return 0
    if argv is None and len(sys.argv) > 1 and sys.argv[1] == "--child-keys":
        print(json.dumps(child_keys(json.loads(sys.argv[2]))))
        return 0
    ap = argparse.ArgumentParser(description="the dataset facts (A3.j), or with --keys the keys-only record (j4)")
    ap.add_argument("--run", required=True)
    ap.add_argument("--keys", action="store_true", help="j4: key structure of BEAM, MAB CR and AMA - never a value")
    args = ap.parse_args(argv)
    L = _load("v3_launch", HERE / "launch.py")
    c = L.Contract.default()
    mode = "keys" if args.keys else "facts"
    rec = run_facts(c, L, run=args.run, python=c.polygon_root / "v3_data" / "Scripts" / "python.exe", parent_env=os.environ,
                    mode=mode)
    print(json.dumps({"problems": rec["problems"], mode: rec.get(mode)}, indent=1))
    return 0 if not rec["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
