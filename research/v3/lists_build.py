#!/usr/bin/env python3
"""PREREG-V3 A7 C2: the unit orders after S1 (§3.4, K64; the auditor's A7 rulings Q-T5-1..5 and Q-A7-1..8), each
built from its pinned file by a child that prints ids - and S7's domains - only (§3.1 T30: never data). ls1.py, which
built the verified S1 list, is not touched: this is its pattern for the other stands (subsample.LIST_STANDS).

Per stand:
* S3 - no list of its own: S1[:480] (lists/S1.json, verified against run_v3.S1_IDS_SHA256) over lme_oracle_cleaned;
  every one must be in the file (subsample.s3_coverage), the ids beyond them are named. The run's record only.
* S4 - locomo10's sample_ids, unit_order("S4"); 10 conversations; none meets an S4 smoke unit (smoke-<qid> of S1's
  tail).
* S5 - beam_128k's conversation_ids, unit_order("S5"); 20 conversations; the smoke pin beam_500k is read too, and the
  two files' conversation ids must not meet (Q-A7-5: by the data, not by the pins).
* S6 - mab_conflict_resolution's metadata.source per row: exactly the 8 sources, one row each (Q-A7-4), then
  s6_order("sh") and s6_order("mh"); both lists are built in memory and written only when both pass (Q-A7-6).
* S7 - ama_swe's [episode_id, domain] for EVERY row (the smoke trajectory comes from the same file): episode ids
  unique across every domain, the SOFTWARE ones (34 or 36, D-J1), unit_order("S7").
* S9 - longmemeval_s's question_ids: subsample.s9_order - S1's order, only when the id sets are equal (Q-A7-2).

Ids keep the file's type (Q-A7-1: an int stays an int; mixed types refuse). Everything else is ls1's: each pin verified
(sha256, size) before the child; the child `<python> -I -B lists_build.py --child <stand> dataset_facts.py <file>...`
spawned under the launch contract with the witnesses checking it, the two repository files by path as the named argv
exceptions (3 and 6); its printed strings must be labels ([A-Za-z0-9_.:-]{1,64}, dataset_facts.scan_labels) or the
output is refused; a child that does not finish in time is killed with its tree; a check that is not clean writes no
list; a run label once, a list file once ("xb", never over another - a file that appears during the run is a named
problem, B-LS1X); the record - pins, argv, check, counts, each list's sha256, the problems - in
<runs>/_lists/<run>/lists.json.

    <python> research\\v3\\lists_build.py --run <label> --stand S4      (Contract.default(), the polygon's python)
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
#: The pinned files each stand's child reads, in argv order (the stand's own first; S5's smoke pin second).
PINS = {"S3": ("lme_oracle_cleaned",), "S4": ("locomo10",), "S5": ("beam_128k", "beam_500k"),
        "S6": ("mab_conflict_resolution",), "S7": ("ama_swe",), "S9": ("longmemeval_s",)}
#: The polygon interpreter per stand (the plan the auditor accepted): pyarrow's stands on the v3_data venv.
PYTHON = {"S5": "v3_data", "S6": "v3_data"}
EXPECT_N = {"S4": 10, "S5": 20}


class ListsRefused(ValueError):
    """A lists run the rules do not allow (a label used before, a list file that exists); nothing was written."""


def _load(name: str, path: Path):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


# ── the child ─────────────────────────────────────────────────────────────────────────────────────────

def _parquet_column(path: Path, column: str) -> list:
    import pyarrow.parquet as pq  # noqa: PLC0415 - the child's interpreter has it
    return pq.read_table(path, columns=[column]).column(column).to_pylist()


def child(stand: str, facts_path: Path, files: Sequence[Path]) -> list:
    """The child's work: per file, the ids the stand needs, in the file's own order - nothing else."""
    facts = _load("v3_dataset_facts_for_lists", Path(facts_path))
    out = []
    for f in files:
        if stand in ("S3", "S9"):
            out.append([rec["question_id"] for rec in facts.iter_json_array(f)])
        elif stand == "S4":
            out.append([rec["sample_id"] for rec in facts.iter_json_array(f)])
        elif stand == "S5":
            out.append(_parquet_column(f, "conversation_id"))
        elif stand == "S6":
            out.append([(m or {}).get("source") for m in _parquet_column(f, "metadata")])
        elif stand == "S7":
            out.append([[rec.get("episode_id"), rec.get("domain")] for rec in facts.read_jsonl(f)])
        else:
            raise SystemExit(f"no child reader for {stand}")
    return out


# ── the harness ───────────────────────────────────────────────────────────────────────────────────────

def build(stand: str, got: Sequence[Sequence], *, SS: Any, facts: Any, s1_ids: Sequence[str] | None) -> dict:
    """The stand's rule over the child's ids: {"lists": {file name: list_record}, "extra": {...}} - or SubsampleRefused
    / ValueError naming what stopped it. Nothing is written here."""
    spec = SS.LIST_STANDS[stand]
    if stand == "S3":
        return {"lists": {}, "extra": SS.s3_coverage(s1_ids, got[0])}
    if stand == "S9":
        return {"lists": {"S9.json": SS.list_record("S9", SS.s9_order(s1_ids, got[0]), seed=None, rule=spec["rule"])},
                "extra": {}}
    if stand == "S4":
        ids = list(got[0])
        SS.check_disjoint("S4", ids, [f"smoke-{q}" for q in list(s1_ids)[480:500]])
        order = SS.unit_order("S4", ids)
    elif stand == "S5":
        ids = list(got[0])
        SS.check_disjoint("S5", ids, got[1])
        order = SS.unit_order("S5", ids)
    elif stand == "S7":
        pairs = [tuple(p) for p in got[0]]
        eps = [e for e, _d in pairs]
        if len(set(eps)) != len(eps):
            raise ValueError("S7: an episode_id repeats across the file's rows (the smoke comes from the same file)")
        swe = [e for e, d in pairs if d == facts.SOFTWARE]
        if len(swe) not in facts.SWE_TRAJECTORIES:
            raise ValueError(f"S7: {len(swe)} {facts.SOFTWARE} trajectories is not one of {facts.SWE_TRAJECTORIES} (D-J1)")
        order = SS.unit_order("S7", swe)
    elif stand == "S6":
        sources = list(got[0])
        if sorted(set(sources)) != sorted(facts.FC_SOURCES) or len(sources) != len(facts.FC_SOURCES):
            raise ValueError(f"S6: the rows' sources {sorted(map(str, sources))} are not the 8, one row each (Q-A7-4)")
        return {"lists": {f"S6-{h.upper()}.json": SS.list_record(f"S6-{h.upper()}", SS.s6_order(sources, h), seed=None,
                                                                  rule=spec["rule"]) for h in ("sh", "mh")},
                "extra": {}}
    else:
        raise ValueError(f"{stand}: not a stand lists_build builds")
    if stand in EXPECT_N and len(order) != EXPECT_N[stand]:
        raise ValueError(f"{stand}: {len(order)} units, not the {EXPECT_N[stand]} the stand declares")
    return {"lists": {spec["files"][0]: SS.list_record(stand, order, seed=spec["seed"], rule=spec["rule"])}, "extra": {}}


def _bytes(rec: Mapping) -> bytes:
    return (json.dumps(rec, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def write_lists(lists: Mapping[str, Mapping], lists_dir: Path, record: dict) -> None:
    """Every list file once, "xb"; when one cannot be written, the files THIS run wrote (their bytes checked against
    what it wrote) are removed and the failure named - a file it did not write is never touched (Q-A7-6, B-LS1X)."""
    lists_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[Path, str]] = []
    for name, rec in lists.items():
        p = lists_dir / name
        data = _bytes(rec)
        try:
            with open(p, "xb") as f:
                f.write(data)
        except FileExistsError:
            record["problems"].append(f"{p} appeared during the run - a list is built once, never over another")
            for q, sha in written:
                if q.is_file() and hashlib.sha256(q.read_bytes()).hexdigest() == sha:
                    q.unlink()
                    record["problems"].append(f"{q.name}, written by this run, removed - the stand's lists go together")
            return
        written.append((p, hashlib.sha256(data).hexdigest()))
    record["lists"] = {name: {"path": str(lists_dir / name), "n": rec["n"], "ids_sha256": rec["ids_sha256"],
                              "seed": rec["seed"], "python": rec["python"]} for name, rec in lists.items()}


def run_lists(c, L, *, stand: str, run: str, python: Path, parent_env, CP=None, native=None, fs=None,
              script: Path | None = None, lists_dir: Path | None = None, s1_sha256: str | None = None,
              timeout_s: float = 3600.0) -> dict:
    """The harness (see the module docstring); returns the run's record, problems named in it."""
    CP = CP or _load("v3_corpus_pin_lists", HERE / "corpus_pin_v3.py")
    SS = _load("v3_subsample_lists", HERE / "subsample.py")
    facts = _load("v3_dataset_facts_for_lists", HERE / "dataset_facts.py")
    if stand not in PINS:
        raise ListsRefused(f"{stand}: not a stand lists_build builds ({sorted(PINS)})")
    lists_dir = Path(lists_dir) if lists_dir is not None else HERE / "lists"
    base = c.runs_root / "_lists" / run
    if base.exists():
        raise ListsRefused(f"the lists run label {run!r} was used before")
    there = [n for n in SS.LIST_STANDS[stand]["files"] if (lists_dir / n).exists()]
    if there:
        raise ListsRefused(f"{there} exist in {lists_dir} - a list is built once, never over another")
    base.mkdir(parents=True)
    record: dict = {"run": run, "stand": stand, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "problems": []}
    s1_ids = None
    if stand in ("S3", "S4", "S9"):
        RV = _load("v3_run_v3_for_lists", HERE / "run_v3.py")
        try:
            s1_ids = RV.s1_order(lists_dir, expected_sha256=s1_sha256 or RV.S1_IDS_SHA256)
        except RV.CLIError as e:
            record["problems"].append(f"the S1 order is not the verified one: {e}")
            return _write(base, record)
    paths = [CP.location(n, hf_hub=c.polygon_root / "hf_cache" / "hub", pins_root=c.runs_root / "_pins")
             for n in PINS[stand]]
    record["pins"] = {}
    for n, p in zip(PINS[stand], paths):
        try:
            record["pins"][n] = CP.verify(n, p)
        except CP.PinMismatch as e:
            record["problems"].append(f"reads_unverified_file refused: {e}")
            return _write(base, record)
    script = Path(script) if script is not None else Path(__file__)
    facts_path = HERE / "dataset_facts.py"
    argv = [os.fspath(python), "-I", "-B", os.fspath(script), "--child", stand, os.fspath(facts_path),
            *map(os.fspath, paths)]
    record["argv"] = argv
    unit = L.make_unit_dirs(c, "_lists", run, "child", "l1")
    env = L.build_env(c, parent_env=parent_env, unit=unit, path_dirs=[Path(python).parent], declared={},
                      catcher_url="", proxies=False)
    W = L.Witnesses(c, native=native if native is not None else L.NativeEgressWitness(),
                    fs=fs if fs is not None else L.FsWitness(L.watched_set(c)))
    cid = f"lists-{run}"
    W.begin_check(cid, tags={"window": "lists", "run": run, "arm": "child", "stand": stand})
    timed_out = False
    try:
        ch = L.spawn(c, argv, env=env, cwd=unit.cwd,
                     record={"role": "lists", "stand": stand, "run": run, "arm": "child", "unit": "l1"},
                     parent_env=parent_env, catcher_url="", witnesses=W, requirement="required",
                     argv_exception={3: script, 6: facts_path}, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE)
        try:
            out, err = ch.process.communicate(timeout=timeout_s)
            rc = ch.process.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            ch.kill_tree()                               # never a child left running after its run
            try:
                ch.process.communicate(timeout=30)
            except (subprocess.TimeoutExpired, OSError):
                pass
    finally:
        chk = W.end_check(cid)
    if timed_out:
        record["problems"].append(f"the child did not finish within {timeout_s} s - killed with its tree")
        return _write(base, record)
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
    if not (isinstance(got, list) and len(got) == len(paths) and all(isinstance(x, list) for x in got)):
        record["problems"].append("the child's output is not one list of ids per file")
        return _write(base, record)
    leaks = facts.scan_labels(got)
    if leaks:
        record["problems"].append(f"prints_answers refused: {len(leaks)} string(s) that are not labels, e.g. {leaks[:3]}")
        return _write(base, record)
    record["n_ids"] = [len(x) for x in got]
    if record["problems"]:
        return _write(base, record)
    try:
        built = build(stand, got, SS=SS, facts=facts, s1_ids=s1_ids)
    except (SS.SubsampleRefused, ValueError) as e:
        record["problems"].append(f"{stand} refused: {e}")
        return _write(base, record)
    record["extra"] = built["extra"]
    write_lists(built["lists"], lists_dir, record)
    return _write(base, record)


def _write(base: Path, record: dict) -> dict:
    (base / "lists.json").write_bytes((json.dumps(record, indent=1, sort_keys=True, default=str) + "\n").encode("utf-8"))
    return record


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 4 and args[0] == "--child":
        print(json.dumps(child(args[1], Path(args[2]), [Path(a) for a in args[3:]]), separators=(",", ":")))
        return 0
    if len(args) == 4 and args[0] == "--run" and args[2] == "--stand":
        L = _load("v3_launch_lists", HERE / "launch.py")
        c = L.Contract.default()
        py = c.polygon_root / ("v3_data" if PYTHON.get(args[3]) == "v3_data" else "py314")
        python = py / "Scripts" / "python.exe" if PYTHON.get(args[3]) == "v3_data" else py / "python.exe"
        rec = run_lists(c, L, stand=args[3], run=args[1], python=python, parent_env=os.environ)
        print(json.dumps({k: rec.get(k) for k in ("run", "stand", "problems", "n_ids", "lists", "extra")}, indent=1,
                         default=str))
        return 1 if rec["problems"] else 0
    sys.stderr.write("usage: lists_build.py --run <label> --stand <S3|S4|S5|S6|S7|S9> | --child <stand> <facts> <file>...\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
