#!/usr/bin/env python3
"""PREREG-V3 A7 C2: research/v3/lists_build.py - the unit orders after S1, each built by a real child under a
temporary launch contract (this suite spawns children) from fixture files shaped like the pinned ones, with text that
must never leave the child:

* LB-S3: no list; S1[:480] over the oracle file - all present, the extras named; a missing id refuses;
* LB-S4, LB-S5, LB-S7: unit_order of the file's ids, typed as the file types them (S5/S7 ints); S5's smoke file read
  too - an id both files hold refuses; S7 keeps SOFTWARE only, a repeated episode id across domains refuses;
* LB-S6: both hop lists by tier; a repeated source refuses and writes nothing; a list that appears during the run
  makes the run remove the one it wrote (and only that one);
* LB-S9: S1's order when the id sets are equal; a missing id refuses;
* LB-argv, LB-pin, LB-leak, LB-fresh, LB-check: the child's argv and exceptions, a pin off its sha stopping the run
  before the child, a child printing text refused, a label and a list once, a dirty check writing no list.

    python tests/research/_test_v3_lists_build.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


LB = _load("v3_lists_build_t", ROOT / "research" / "v3" / "lists_build.py")
SS = _load("v3_subsample_lb_t", ROOT / "research" / "v3" / "subsample.py")
L = _load("v3_launch_lb_t", ROOT / "research" / "v3" / "launch.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


class Quiet:
    def processes(self):
        return []

    def identity(self, pid):
        return 1.0

    def connections(self, pids):
        return [], 0

    def listeners(self):
        return {}


def _parquet_python() -> Path | None:
    """An interpreter that sees pyarrow in ISOLATED mode (the child runs -I): this one if it does (a CI runner with
    pyarrow installed), else the polygon's v3_data venv - the one the S5/S6 runs use - else none (the rows are skipped
    by name, never passed)."""
    import subprocess  # noqa: PLC0415
    for py in (Path(sys.executable), Path(r"D:\Coding\_nevertwice_polygon\v3_data\Scripts\python.exe")):
        try:
            if py.is_file() and subprocess.run([str(py), "-I", "-c", "import pyarrow.parquet"], capture_output=True,
                                               timeout=120).returncode == 0:
                return py
        except (OSError, subprocess.SubprocessError):
            continue
    return None


PARQ_PY = _parquet_python()
SKIPPED: list[str] = []
TMP = Path(tempfile.mkdtemp(prefix="nvt3_lists_"))
TEXT = "What did the user say about the cat at the lake last spring?"
ORDER = [f"q{i:03d}" for i in range(500)][::-1]
S1REC = SS.list_record("S1", ORDER, seed=SS.SEED, rule=SS.RULE)
DATA = TMP / "data"
DATA.mkdir()


def lists_dir(tag: str) -> Path:
    d = TMP / tag / "lists"
    d.mkdir(parents=True, exist_ok=True)
    (d / "S1.json").write_text(json.dumps(S1REC), encoding="utf-8")
    return d


def write_json(name: str, obj) -> Path:
    p = DATA / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


def write_jsonl(name: str, rows) -> Path:
    p = DATA / name
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return p


def write_parquet(name: str, rows) -> Path:
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pq  # noqa: PLC0415
    p = DATA / name
    pq.write_table(pa.Table.from_pylist(rows), p)
    return p


class FakeCP:
    PinMismatch = type("PinMismatch", (RuntimeError,), {})

    def __init__(self, files: dict, *, bad: str | None = None):
        self.files, self.bad = files, bad

    def location(self, name, **kw):
        return self.files[name]

    def verify(self, name, path):
        if name == self.bad:
            raise self.PinMismatch(f"{name}: sha256 is not the pinned one")
        return {"pin": name, "sha256": "d" * 64}


def contract(tag: str):
    base = TMP / tag
    (base / "watched").mkdir(parents=True, exist_ok=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    exc = {sys.executable: "the test interpreter"}
    if getattr(sys, "_base_executable", sys.executable) != sys.executable:
        exc[sys._base_executable] = "the test interpreter's base"
    if PARQ_PY is not None and str(PARQ_PY) != sys.executable:
        exc[str(PARQ_PY)] = "the parquet stands' interpreter (pyarrow)"
        cfg = PARQ_PY.parent.parent / "pyvenv.cfg"
        home = next((ln.split("=", 1)[1].strip() for ln in cfg.read_text(encoding="utf-8").splitlines()
                     if ln.split("=", 1)[0].strip().lower() == "home"), None) if cfg.is_file() else None
        if home:
            exc[str(Path(home) / "python.exe")] = "the parquet venv's base"
    return L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                      owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                      conservation_root=base / "conservation", binary_exceptions=exc,
                      system_dirs=(Path(sys.executable).parent,)), base


def run(tag: str, stand: str, files: dict, *, bad=None, script=None, label=None, ld=None, cb=None):
    c, base = cb or contract(tag)
    ld = ld or lists_dir(tag)
    py = PARQ_PY if stand in ("S5", "S6") else Path(sys.executable)
    try:
        rec = LB.run_lists(c, L, stand=stand, run=label or f"ls-{tag}", python=py,
                           parent_env=os.environ, CP=FakeCP(files, bad=bad),
                           native=L.NativeEgressWitness(sampler=Quiet(), tick_s=60, jobs=None),
                           fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]), script=script, lists_dir=ld,
                           s1_sha256=S1REC["ids_sha256"])
    except LB.ListsRefused as e:
        rec = {"refused": str(e), "problems": [f"refused: {e}"]}
    except Exception as e:  # noqa: BLE001 - a crash FAILs the rows by name
        rec = {"problems": [f"crash {type(e).__name__}: {e}"]}
    return rec, c, base, ld


def listed(ld: Path, name: str) -> dict:
    p = ld / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def no_text(c, ld) -> bool:
    files = [*ld.glob("*.json"), *(c.runs_root / "_lists").rglob("lists.json")]
    return bool(files) and not any(b"What did the user say" in p.read_bytes() for p in files)


try:
    print("- per stand -")
    oracle = write_json("oracle.json", [{"question_id": q, "question": TEXT} for q in [*ORDER[:480], "extra1"]])
    r3, c3, _b, ld3 = run("s3", "S3", {"lme_oracle_cleaned": oracle})
    check("LB-S3: no list; S1[:480] all in the oracle file, the extras named", r3.get("problems") == []
          and r3.get("extra") == {"covered": 480, "extra": ["extra1"]} and sorted(p.name for p in ld3.glob("*.json")) == ["S1.json"],
          str(r3.get("problems")))
    oracle_short = write_json("oracle_short.json", [{"question_id": q, "question": TEXT} for q in ORDER[:479]])
    r3b, *_ = run("s3b", "S3", {"lme_oracle_cleaned": oracle_short})
    check("LB-S3: an oracle file without all of S1[:480] refuses by name", any("S3 refused" in p and "lacks 1" in p
                                                                             for p in r3b["problems"]), str(r3b["problems"]))

    samples = [{"sample_id": f"conv-{i}", "conversation": {"text": TEXT}} for i in (26, 30, 41, 42, 43, 44, 47, 48, 49, 50)]
    loc = write_json("locomo.json", samples)
    r4, c4, _b, ld4 = run("s4", "S4", {"locomo10": loc})
    l4 = listed(ld4, "S4.json")
    check("LB-S4: unit_order of the 10 sample ids (SEED+4) - the committed golden order", r4.get("problems") == []
          and l4.get("ids") == ["conv-30", "conv-49", "conv-26", "conv-50", "conv-43", "conv-41", "conv-44", "conv-42",
                                "conv-47", "conv-48"] and l4.get("seed") == SS.SEED + 4 and l4.get("rule") == SS.UNIT_RULE
          and no_text(c4, ld4), f"{r4.get('problems')} {l4.get('ids')}")

    r4b, _c, _b, ld4b = run("s4b", "S4", {"locomo10": write_json("locomo_smoke.json", [
        *samples[:9], {"sample_id": f"smoke-{ORDER[480]}", "conversation": {"text": TEXT}}])})
    r4c, _c, _b, ld4c = run("s4c", "S4", {"locomo10": write_json("locomo9.json", samples[:9])})
    check("LB-S4: a unit that is also an S4 smoke unit refuses, and a file with 9 conversations (not 10) refuses - "
          "no list either time", any("share ids" in p for p in r4b["problems"])
          and any("9 units, not the 10" in p for p in r4c["problems"])
          and not (ld4b / "S4.json").exists() and not (ld4c / "S4.json").exists(),
          f"{r4b['problems']} | {r4c['problems']}")

    if PARQ_PY is None:
        SKIPPED.append("LB-S5 (2 rows), LB-S6 (3 rows)")
        print("       SKIP LB-S5, LB-S6: no interpreter here sees pyarrow in isolated mode (the child runs -I) "
              "- not passed")
    else:
        def beam_row(cid, n):
            return {"conversation_id": cid, "chat": [[{"role": "user", "content": f"{TEXT} chat {n}"}]],
                    "probing_questions": repr({"ability": [{"question": f"question {n}?"}]})}
        beam = write_parquet("beam.parquet", [beam_row(str(i + 1), i) for i in range(20)])
        smoke_ok = write_parquet("beam500.parquet", [beam_row(str(i + 1), 100 + i) for i in range(3)])
        r5, c5, _b, ld5 = run("s5", "S5", {"beam_128k": beam, "beam_500k": smoke_ok})
        l5 = listed(ld5, "S5.json")
        check("LB-S5 (Q-A7-5'): the same conversation ids in both splits with different content pass - unit_order of the "
              "20 ids as the file types them (strings, SEED+5); the smoke conversation is 500K's first, checked by content",
              r5.get("problems") == [] and l5.get("ids") == SS.unit_order("S5", [str(i + 1) for i in range(20)])
              and all(isinstance(x, str) for x in l5.get("ids", [0])) and r5.get("n_ids") == [20, 1]
              and (r5.get("extra") or {}).get("smoke", {}).get("conversation_id") == "1" and no_text(c5, ld5),
              f"{r5.get('problems')} {l5.get('ids')} {r5.get('extra')}")
        smoke_q = write_parquet("beam500q.parquet", [{**beam_row("1", 100), "probing_questions":
                                                      repr({"other": [{"question": "question 7?"}]})}])
        r5b, _c, _b, ld5b = run("s5b", "S5", {"beam_128k": beam, "beam_500k": smoke_q})
        smoke_c = write_parquet("beam500c.parquet", [{**beam_row("1", 100), "chat": [[{"role": "user",
                                                                                       "content": f"{TEXT} chat 3"}]]}])
        r5c, _c, _b, ld5c = run("s5c", "S5", {"beam_128k": beam, "beam_500k": smoke_c})
        check("LB-S5 (Q-A7-5'): a probing question the smoke conversation shares with the stand refuses by name, and so "
              "does a chat that starts as one of theirs - no list either time",
              any("shares 1 probing question" in p for p in r5b["problems"])
              and any("chat starts as one of the stand's" in p for p in r5c["problems"])
              and not (ld5b / "S5.json").exists() and not (ld5c / "S5.json").exists(),
              f"{r5b['problems']} | {r5c['problems']}")

        fc_rows = [{"metadata": {"source": s, "haystack_sessions": None}, "context": TEXT} for s in
                   sorted(f"factconsolidation_{h}_{t}" for h in ("sh", "mh") for t in ("262k", "6k", "64k", "32k"))]
        r6, c6, _b, ld6 = run("s6", "S6", {"mab_conflict_resolution": write_parquet("fc.parquet", fc_rows)})
        sh, mh = listed(ld6, "S6-SH.json"), listed(ld6, "S6-MH.json")
        check("LB-S6: both hop lists by tier ascending, seed null, the S6 rule", r6.get("problems") == []
              and sh.get("ids") == [f"factconsolidation_sh_{t}" for t in ("6k", "32k", "64k", "262k")]
              and mh.get("ids") == [f"factconsolidation_mh_{t}" for t in ("6k", "32k", "64k", "262k")]
              and sh.get("seed") is None and sh.get("rule") == SS.S6_RULE, str(r6.get("problems")))
        r6b, _c, _b, ld6b = run("s6b", "S6", {"mab_conflict_resolution": write_parquet("fc_dup.parquet", [*fc_rows, fc_rows[0]])})
        check("LB-S6: a repeated source refuses by name and writes neither list (Q-A7-4)",
              any("S6 refused" in p and "one row each" in p for p in r6b["problems"])
              and not (ld6b / "S6-SH.json").exists() and not (ld6b / "S6-MH.json").exists(), str(r6b["problems"]))
        cb6 = contract("s6c")
        ld6c = lists_dir("s6c")
        theirs = ld6c / "S6-MH.json"
        race = TMP / "race6.py"
        race.write_text("import sys\nsys.argv[0:1] = []\nimport runpy\nopen(" + repr(str(theirs)) + ", 'wb').write(b'theirs')\n"
                        "sys.argv = [" + repr(str(ROOT / "research" / "v3" / "lists_build.py")) + "] + sys.argv\n"
                        "runpy.run_path(sys.argv[0], run_name='__main__')\n", encoding="utf-8")
        r6c, *_ = run("s6c", "S6", {"mab_conflict_resolution": ld6c.parent / "none"} | {"mab_conflict_resolution": DATA / "fc.parquet"},
                      script=race, cb=cb6, ld=ld6c)
        check("LB-S6 (Q-A7-6): a hop list that appears during the run - the SH this run wrote is removed, the foreign MH "
              "left as it was, both named", any("appeared during the run" in p for p in r6c["problems"])
              and any("S6-SH.json, written by this run, removed" in p for p in r6c["problems"])
              and not (ld6c / "S6-SH.json").exists() and theirs.read_bytes() == b"theirs" and "lists" not in r6c,
              str(r6c["problems"]))

    ama = [*({"episode_id": 1000 + i, "domain": "SOFTWARE", "trajectory": TEXT} for i in range(34)),
           {"episode_id": 5, "domain": "WEB", "trajectory": TEXT}, {"episode_id": 6, "domain": "GAME", "trajectory": TEXT}]
    r7, c7, _b, ld7 = run("s7", "S7", {"ama_swe": write_jsonl("ama.jsonl", ama)})
    l7 = listed(ld7, "S7.json")
    check("LB-S7: the SOFTWARE episode ids only, as ints, unit_order (SEED+7); every row's domain read",
          r7.get("problems") == [] and l7.get("ids") == SS.unit_order("S7", [1000 + i for i in range(34)])
          and r7.get("n_ids") == [36], f"{r7.get('problems')} {l7.get('n')}")
    r7b, _c, _b, ld7b = run("s7b", "S7", {"ama_swe": write_jsonl("ama_dup.jsonl", [*ama, {"episode_id": 1000,
                                                                                           "domain": "WEB", "trajectory": TEXT}])})
    check("LB-S7: an episode id repeated across domains refuses (the smoke comes from the same file) - no list",
          any("repeats across" in p for p in r7b["problems"]) and not (ld7b / "S7.json").exists(), str(r7b["problems"]))

    lme_s = write_json("lme_s.json", [{"question_id": q, "question": TEXT} for q in sorted(ORDER)])
    r9, c9, _b, ld9 = run("s9", "S9", {"longmemeval_s": lme_s})
    check("LB-S9: S1's order when the file's ids are exactly S1's", r9.get("problems") == []
          and listed(ld9, "S9.json").get("ids") == ORDER and listed(ld9, "S9.json").get("rule") == SS.S9_RULE,
          str(r9.get("problems")))
    r9b, _c, _b, ld9b = run("s9b", "S9", {"longmemeval_s": write_json("lme_s_short.json",
                                                                      [{"question_id": q} for q in ORDER[1:]])})
    check("LB-S9: a file missing one of S1's ids refuses by name - no list", any("1 missing" in p for p in r9b["problems"])
          and not (ld9b / "S9.json").exists(), str(r9b["problems"]))

    print("\n- the run -")
    facts_py = ROOT / "research" / "v3" / "dataset_facts.py"
    check("LB-argv: [python, -I, -B, lists_build.py, --child, <stand>, dataset_facts.py, <files>...] - the two "
          "repository files the named exceptions at 3 and 6", r4.get("argv") == [sys.executable, "-I", "-B",
                                                                             str(Path(LB.__file__)), "--child", "S4",
                                                                             str(facts_py), str(loc)],
          str(r4.get("argv")))
    rp, _c, _b, ldp = run("pin", "S4", {"locomo10": DATA / "locomo.json"}, bad="locomo10")
    check("LB-pin: a file off its pin stops the run before the child - no argv, no list",
          any(p.startswith("reads_unverified_file refused") for p in rp["problems"]) and "argv" not in rp
          and not (ldp / "S4.json").exists(), str(rp["problems"]))
    leak = TMP / "leak.py"
    leak.write_text("import json\nprint(json.dumps([['conv-1', 'What did the user say about the cat?']]))\n",
                    encoding="utf-8")
    rl, _c, _b, ldl = run("leak", "S4", {"locomo10": DATA / "locomo.json"}, script=leak)
    check("LB-leak: a child that prints a text string is refused - no list",
          any(p.startswith("prints_answers refused") for p in rl["problems"]) and not (ldl / "S4.json").exists(),
          str(rl["problems"]))
    again = run("s4", "S4", {"locomo10": DATA / "locomo.json"}, cb=(c4, TMP / "s4"), ld=ld4)[0]
    other = run("s4", "S4", {"locomo10": DATA / "locomo.json"}, cb=(c4, TMP / "s4"), ld=ld4, label="ls-s4-b")[0]
    check("LB-fresh: a run label once, a list once - never over an existing one",
          "used before" in again.get("refused", "") and "never over another" in other.get("refused", ""),
          f"{again.get('refused')} | {other.get('refused')}")
    dc, dbase = contract("dirty")
    dirty = TMP / "dirty.py"
    dirty.write_text("import json\nopen(" + repr(str(dbase / "watched" / "touched.txt")) + ", 'w').write('x')\n"
                     "print(json.dumps([['conv-1', 'conv-2']]))\n", encoding="utf-8")
    rd, _c, _b, ldd = run("dirty", "S4", {"locomo10": DATA / "locomo.json"}, script=dirty, cb=(dc, dbase))
    check("LB-check: a child that touched a watched file - the check is not clean, named, and no list",
          any("check is not clean" in p and "'fs_hits': 1" in p for p in rd["problems"]) and not (ldd / "S4.json").exists(),
          str(rd["problems"]))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 lists build: {PASSED} passed, {FAILED} failed, {len(SKIPPED)} skipped by name {SKIPPED}")
sys.exit(1 if FAILED else 0)
