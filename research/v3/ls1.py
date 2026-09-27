#!/usr/bin/env python3
"""PREREG-V3 A6 step 1: the ls1 run - the S1/S2 nested order (§3.4; the auditor's A7 rulings Q-T5-1..5), built from
the pinned LME-S by a child that reads question ids and question types only (§3.1 T30: never data).

* the pin is verified (sha256, size) before anything reads it - a file off its pin stops the run before the child;
* the child - `<python> -I -B ls1.py --child <file> <dataset_facts.py>`, spawned under the launch contract as an
  offline step, the witnesses checking it, the two repository files by path as the named argv exceptions - streams
  the JSON array element by element (dataset_facts.iter_json_array, so the 277 MB file is never held whole) and prints one JSON line, [[question_id, question_type], ...]; every string it prints
  must be a label ([A-Za-z0-9_.:-]{1,64}), so no question, answer or session text can leave it - the harness refuses
  its output otherwise;
* the harness orders the pairs by subsample.nested_order (the published counts checked first; a difference or a
  prefix outside floor/ceil stops the build - no other rule is chosen) and writes research/v3/lists/S1.json with
  subsample.list_record: ids only, the seed, the rule, the Python version and the sha256 of the canonical ids. The
  file is written once, never over an existing one. The auditor checks that sha256 against his independent
  implementation before the list is used (a difference is a stop, never a new reference).
* the run's record - the pin, the child's argv, its check, the id count, the list's sha256, the problems - goes to
  <runs>/_lists/<run>/ls1.json.

    <python> research\\v3\\ls1.py --run <label>      (the harness: Contract.default(), the polygon's python)
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PIN = "lme_s_cleaned"
STAND = "S1"


class ListsRefused(ValueError):
    """An ls1 run the rules do not allow; nothing was written."""


def _load(name: str, path: Path):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def child(path: Path, facts_path: Path) -> list:
    """The child's work: [[question_id, question_type], ...] of the file, in its own order."""
    facts = _load("v3_dataset_facts_for_ls1", Path(facts_path))
    out = []
    for rec in facts.iter_json_array(path):
        out.append([str(rec["question_id"]), str(rec["question_type"])])
    return out


def run_ls1(c, L, *, run: str, python: Path, parent_env, CP=None, native=None, fs=None, script: Path | None = None,
            lists_dir: Path | None = None, timeout_s: float = 3600.0) -> dict:
    """The harness (see the module docstring); returns the run's record, problems named in it. A child that does not
    finish within ``timeout_s`` is killed with its whole tree and named - never left running."""
    CP = CP or _load("v3_corpus_pin_ls1", HERE / "corpus_pin_v3.py")
    SS = _load("v3_subsample_ls1", HERE / "subsample.py")
    facts = _load("v3_dataset_facts_for_ls1", HERE / "dataset_facts.py")
    lists_dir = Path(lists_dir) if lists_dir is not None else HERE / "lists"
    out_file = lists_dir / f"{STAND}.json"
    base = c.runs_root / "_lists" / run
    if base.exists():
        raise ListsRefused(f"the ls1 run label {run!r} was used before")
    if out_file.exists():
        raise ListsRefused(f"{out_file} exists - a list is built once, never over another")
    base.mkdir(parents=True)
    record: dict = {"run": run, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "problems": [],
                    "stand": STAND}
    path = CP.location(PIN, hf_hub=c.polygon_root / "hf_cache" / "hub", pins_root=c.runs_root / "_pins")
    try:
        record["pin"] = CP.verify(PIN, path)
    except CP.PinMismatch as e:
        record["problems"].append(f"reads_unverified_file refused: {e}")
        return _write(base, record)
    script = Path(script) if script is not None else Path(__file__)
    facts_path = HERE / "dataset_facts.py"
    argv = [os.fspath(python), "-I", "-B", os.fspath(script), "--child", os.fspath(path), os.fspath(facts_path)]
    record["argv"] = argv
    unit = L.make_unit_dirs(c, "_lists", run, "child", "l1")
    env = L.build_env(c, parent_env=parent_env, unit=unit, path_dirs=[Path(python).parent], declared={},
                      catcher_url="", proxies=False)
    W = L.Witnesses(c, native=native if native is not None else L.NativeEgressWitness(),
                    fs=fs if fs is not None else L.FsWitness(L.watched_set(c)))
    cid = f"ls1-{run}"
    W.begin_check(cid, tags={"window": "ls1", "run": run, "arm": "child"})
    timed_out = False
    try:
        ch = L.spawn(c, argv, env=env, cwd=unit.cwd,
                     record={"role": "ls1", "stand": STAND, "run": run, "arm": "child", "unit": "l1"},
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
        pairs = json.loads(out)
    except ValueError:
        record["problems"].append("the child's output is not JSON")
        return _write(base, record)
    if not (isinstance(pairs, list) and all(isinstance(p, list) and len(p) == 2 for p in pairs)):
        record["problems"].append("the child's output is not a list of [question_id, question_type] pairs")
        return _write(base, record)
    leaks = facts.scan_labels(pairs)
    if leaks:
        record["problems"].append(f"prints_answers refused: {len(leaks)} string(s) that are not labels, e.g. {leaks[:3]}")
        return _write(base, record)
    record["n_pairs"] = len(pairs)
    if record["problems"]:
        return _write(base, record)
    try:
        order = SS.nested_order([tuple(p) for p in pairs])
    except SS.SubsampleRefused as e:
        record["problems"].append(f"nested_order refused: {e}")
        return _write(base, record)
    lst = SS.list_record(STAND, order, seed=SS.SEED, rule=SS.RULE)
    lists_dir.mkdir(parents=True, exist_ok=True)
    with open(out_file, "xb") as f:
        f.write((json.dumps(lst, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    record["list"] = {"path": str(out_file), "n": lst["n"], "ids_sha256": lst["ids_sha256"], "seed": lst["seed"],
                      "python": lst["python"]}
    return _write(base, record)


def _write(base: Path, record: dict) -> dict:
    (base / "ls1.json").write_bytes((json.dumps(record, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return record


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 3 and args[0] == "--child":
        print(json.dumps(child(Path(args[1]), Path(args[2])), separators=(",", ":")))
        return 0
    if len(args) == 2 and args[0] == "--run":
        L = _load("v3_launch_ls1", HERE / "launch.py")
        c = L.Contract.default()
        rec = run_ls1(c, L, run=args[1], python=c.polygon_root / "py314" / "python.exe", parent_env=os.environ)
        print(json.dumps({k: rec.get(k) for k in ("run", "problems", "n_pairs", "list")}, indent=1))
        return 1 if rec["problems"] else 0
    sys.stderr.write("usage: ls1.py --run <label> | --child <file> <dataset_facts.py>\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
