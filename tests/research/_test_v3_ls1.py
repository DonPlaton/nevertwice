#!/usr/bin/env python3
"""PREREG-V3 A6 step 1: research/v3/ls1.py - the S1/S2 nested order from ids and question types only, built by a real
child under a temporary launch contract (this suite spawns children) on a 500-question fixture with §3.4's published
counts and session text that must never leave the child:

* LS-ok: the pin verified first; the child's argv [python, -I, -B, ls1.py, --child, <file>, dataset_facts.py] with the
  two repository files as its exceptions; lists/S1.json = subsample.list_record of subsample.nested_order over the
  file's (id, type) pairs - ids only, the seed, the rule, the sha256 of the canonical ids; no text anywhere written;
* LS-fresh: a run label once, a list once - never over an existing one;
* LS-pin, LS-counts, LS-leak: a file off its pin stops the run before the child; counts other than the published ones
  stop the build; a child that prints a text string is refused - and none of them writes a list.

    python tests/research/_test_v3_ls1.py
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


LS = _load("v3_ls1_t", ROOT / "research" / "v3" / "ls1.py")
SS = _load("v3_subsample_ls1", ROOT / "research" / "v3" / "subsample.py")
L = _load("v3_launch_ls1_t", ROOT / "research" / "v3" / "launch.py")
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


TMP = Path(tempfile.mkdtemp(prefix="nvt3_ls1_"))
TEXT = "What did the user say about the cat at the lake last spring?"
COUNTS = dict(SS.PUBLISHED)


def fixture(name: str, counts: dict) -> Path:
    recs = []
    for ti, (t, n) in enumerate(sorted(counts.items())):
        for i in range(n):
            qid = f"q{ti}-{i:03d}" + ("_abs" if t == "multi-session" and i < SS.PUBLISHED_ABS else "")
            recs.append({"question_id": qid, "question_type": t, "question": TEXT,
                         "haystack_sessions": [[{"role": "user", "content": TEXT + " " + "x" * 50}]]})
    p = TMP / "data" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
    return p


class FakeCP:
    PinMismatch = type("PinMismatch", (RuntimeError,), {})

    def __init__(self, path: Path, *, bad: bool = False):
        self.path, self.bad = path, bad

    def location(self, name, **kw):
        assert name == "lme_s_cleaned", name
        return self.path

    def verify(self, name, path):
        if self.bad:
            raise self.PinMismatch(f"{name}: sha256 is not the pinned one")
        return {"pin": name, "sha256": "d" * 64}


def contract(tag: str):
    base = TMP / tag
    (base / "watched").mkdir(parents=True)
    (base / "watched" / "idle.txt").write_bytes(b"idle")
    exc = {sys.executable: "the test interpreter"}
    if getattr(sys, "_base_executable", sys.executable) != sys.executable:
        exc[sys._base_executable] = "the test interpreter's base"
    c = L.Contract(polygon_root=base / "polygon", runs_root=base / "polygon" / "runs" / "v3", repo_root=ROOT,
                   owner_home=base / "owner", secrets_dir=base / "secrets", quarantine_root=base / "quarantine",
                   conservation_root=base / "conservation", binary_exceptions=exc, system_dirs=(Path(sys.executable).parent,))
    return c, base


def run(tag: str, data: Path, *, cp=None, script=None, run_label="ls1-a", lists_dir=None, c_base=None):
    c, base = c_base or contract(tag)
    lists_dir = lists_dir or base / "lists"
    rec = LS.run_ls1(c, L, run=run_label, python=Path(sys.executable), parent_env=os.environ, CP=cp or FakeCP(data),
                     native=L.NativeEgressWitness(sampler=Quiet(), tick_s=60, jobs=None),
                     fs=L.FsWitness([L.WatchSpec("watched", base / "watched")]), script=script, lists_dir=lists_dir)
    return rec, c, base, lists_dir


def refused(fn) -> str:
    try:
        fn()
        return "accepted"
    except LS.ListsRefused as e:
        return str(e)
    except Exception as e:  # noqa: BLE001 - not ls1's named refusal: the row FAILs by name
        return f"not refused by ls1: {type(e).__name__}: {e}"


try:
    print("- LS-ok -")
    GOOD = fixture("lme_s.json", COUNTS)
    pairs = [[r["question_id"], r["question_type"]] for r in json.loads(GOOD.read_text(encoding="utf-8"))]
    want = SS.nested_order([tuple(p) for p in pairs])
    rec, c, base, ld = run("ok", GOOD)
    s1 = json.loads((ld / "S1.json").read_text(encoding="utf-8")) if (ld / "S1.json").exists() else {}
    canon = json.dumps(want, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    check("LS-ok: the child's pairs, ordered by subsample.nested_order, written as lists/S1.json - ids only, the seed, "
          "the rule, the sha256 of the canonical ids", rec["problems"] == [] and s1.get("ids") == want
          and s1.get("stand") == "S1" and s1.get("seed") == SS.SEED and s1.get("rule") == SS.RULE and s1.get("n") == 500
          and s1.get("ids_sha256") == hashlib.sha256(canon).hexdigest() == rec["list"]["ids_sha256"], str(rec)[:300])
    facts_py = ROOT / "research" / "v3" / "dataset_facts.py"
    check("LS-ok: the child's argv is [python, -I, -B, ls1.py, --child, <file>, dataset_facts.py] - the two repository "
          "files its named exceptions", rec["argv"] == [sys.executable, "-I", "-B", str(Path(LS.__file__)), "--child",
                                                       str(GOOD), str(facts_py)], str(rec.get("argv")))
    written = [p for p in (ld / "S1.json", c.runs_root / "_lists" / "ls1-a" / "ls1.json") if p.exists()]
    check("LS-ok: no question or session text in anything written - the list and the run's record",
          len(written) == 2 and not any(b"What did the user say" in p.read_bytes() or b"xxxxx" in p.read_bytes()
                                        for p in written), str(written))
    check("LS-ok: the child's check is clean and the pairs are the file's 500", rec["check"] == {
        "complete": True, "native_hits": 0, "fs_hits": 0} and rec["n_pairs"] == 500, str(rec.get("check")))
    check("LS-child: the child's work alone - the file's (id, type) pairs, in the file's order",
          LS.child(GOOD, facts_py) == pairs)

    print("\n- LS-fresh -")
    again = refused(lambda: run("ok", GOOD, c_base=(c, base), lists_dir=ld))
    other = refused(lambda: run("ok", GOOD, c_base=(c, base), lists_dir=ld, run_label="ls1-b"))
    check("LS-fresh: a run label is used once, and a list is built once - never over an existing one",
          "used before" in again and "never over another" in other and not (c.runs_root / "_lists" / "ls1-b").exists(),
          f"{again} | {other}")

    print("\n- refusals -")
    rp, _c, _b, ldp = run("pin", GOOD, cp=FakeCP(GOOD, bad=True))
    check("LS-pin: a file off its pin stops the run before the child - no argv, no list",
          any(p.startswith("reads_unverified_file refused") for p in rp["problems"]) and "argv" not in rp
          and not (ldp / "S1.json").exists(), str(rp["problems"]))
    BAD = fixture("lme_s_bad.json", {**COUNTS, "single-session-user": 69})
    rb, _c, _b, ldb = run("counts", BAD)
    check("LS-counts: counts other than §3.4's published ones stop the build - no list",
          any("nested_order refused" in p and "single-session-user" in p for p in rb["problems"])
          and not (ldb / "S1.json").exists(), str(rb["problems"]))
    leak = TMP / "leak_child.py"
    leak.write_text("import json\nprint(json.dumps([['q1', 'What did the user say about the cat?']]))\n", encoding="utf-8")
    rl, _c, _b, ldl = run("leak", GOOD, script=leak)
    check("LS-leak: a child that prints a text string is refused - no list",
          any(p.startswith("prints_answers refused") for p in rl["problems"]) and not (ldl / "S1.json").exists(),
          str(rl["problems"]))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 ls1: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
