#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A4: research/v3/run_v3_plan.py - the stand plan (pure: synthetic units from loaders' dataclasses,
the §5.1 truncator with a whitespace tokenizer, a temporary runs tree):

* PL-blocks: blocks of the stand's size in the committed order, only the last one short; S6 and bad or repeated unit
  ids refused; a smoke stand id takes its stand's size;
* PL-dates (Q-A4-4): LME, LoCoMo, BEAM and ISO forms to ISO; a date that does not exist (31 Feb) or an unknown form
  is refused - a unit with one is refused by name;
* PL-text (Q-45-4): one "<role|speaker>: <text>" line per item, joined by "\\n";
* PL-authors (Q-A4-1, Q-A4-2): LoCoMo roles from the sample's speaker_a/speaker_b map, a name outside the two
  refused; LME speaker = role; role arms get the role, name arms the name;
* PL-ops: each arm's write unit and fields - sessions for ours, messages for mem0 and graphiti, turns for a-mem, one
  thread per session for langmem, items for the retrieval tier; dates in the arm's route;
* PL-retrieval (Q-A4-3): the same bytes for every retrieval arm, with the role/name prefix and, on a dated stand, the
  §5.3 header; the whole string cut by the §5.1 truncator;
* PL-read-plan, PL-code-copy (Q9), PL-no-adapter (A8), PL-map-sha;
* PL-python (Q-A4-5): a real venv made from this interpreter's base (``-m venv --without-pip``: this suite spawns
  children) and probed for real - its version, base and base sha256; with no package or only pip it carries our arms
  and letta, with any other package they are refused by name (a competitor's list is recorded); the bare base
  interpreter is refused for them; a distribution in the probe's working directory is not seen (-I);
* PLL (Q-A4-6, O1 (c), B-P1, B-P2): PlanLauncher on the plan's real specs for all 13 arms - the adapter's SPEC_KEYS,
  a memory arm's "both", the read stage's unit_dir; (1) the same env names in every unit; (2) the arm's own port and
  /u/<run>.<unit> of the unit; (3) launch.build_env + assert_env clean with build_secrets' tokens; (4) our arms by
  script with {2: script}, competitors from their _code copy with none, a competitor given one refused; (5) path_dirs
  the python's directory alone; the copy staged once per (run, block) and re-checked; the launch record holds no
  token; ids outside [A-Za-z0-9_-]{1,64} or with a leading '_' refused; through the scheduler's ChildArmLauncher;
* AN (§4.3a, §5.2, §8.1, Q8): the Answerer - the reader request is READER_PARAMS exactly, on the arm's reader port
  under /u/<run>.<unit>; the row holds sha256 values and counts, never text; the texts in one fresh file per answer
  named by sha256(qid, point) (a ":" in a qid is fine); no reply, a non-200 or a reply without choices is the
  scheduler's ReaskableError; key_question maps (unit, request_key) of every reader request - the re-ask and a failed
  one included - to the qid; the prompt is the same for every arm on the same read; a read without item texts refused;
* TR, SP: the §5.1 truncation per arm-run ({items, truncated, share}; "not-applicable" for a product that embeds its
  own text); stand_plan - the scheduler's StandPlan, ops per (arm, run, unit) with their shas, reads per (arm, unit)
  with the arm's points (Q-12-3), unknown arms and bad ids refused.

    python tests/research/_test_v3_run_plan.py
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
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


PL = _load("v3_run_plan_t", ROOT / "research" / "v3" / "run_v3_plan.py")
LD = _load("v3_loaders_for_plan_t", ROOT / "research" / "v3" / "loaders.py")
TK = _load("v3_tokens_for_plan_t", ROOT / "research" / "v3" / "tokens.py")
SC = _load("v3_scheduler_for_plan_t", ROOT / "research" / "v3" / "scheduler.py")
PT = _load("v3_points_for_plan_t", ROOT / "research" / "v3" / "points.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def refused(fn) -> str:
    try:
        fn()
        return "accepted"
    except PL.PlanError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001 - not the plan's named refusal: the row FAILs by name
        return f"not refused by the plan: {type(e).__name__}: {e}"


def spans(text: str):
    """A whitespace tokenizer: each word is one token (its character span)."""
    out, i = [], 0
    for w in text.split():
        j = text.index(w, i)
        out.append((j, j + len(w)))
        i = j + len(w)
    return out


def unit(uid, sessions, questions=(("q1", "what?"),)):
    ss = tuple(LD.Session(sid, date, tuple(LD.Item(iid, text, role, speaker, n) for n, (iid, text, role, speaker)
                                            in enumerate(items))) for sid, date, items in sessions)
    qs = tuple(LD.Question(f"{uid}:{q}", text, None, None, False) for q, text in questions)
    return LD.EvalUnit("S1", uid, "haystack", ss, qs, 0, 0, "0" * 64)


LME = unit("u1", [("s1", "2023/05/20 (Sat) 02:21", [("u1:1", "hi", "user", None), ("u1:2", "ok", "assistant", None)]),
                  ("s2", "2023/05/21 (Sun) 10:00", [("u1:3", "more", "user", None)])])
LOC = unit("conv-1", [("conv-1:session_1", "1:56 pm on 8 May, 2023",
                       [("conv-1:D1:1", "hey Mel", None, "Caroline"), ("conv-1:D1:2", "hi Caroline", None, "Mel")])])
LOC_BAD = unit("conv-2", [("conv-2:session_1", "1:56 pm on 8 May, 2023", [("conv-2:D1:1", "x", None, "Bob")])])
SMAP = PL.speaker_map({"sample_id": "conv-1", "conversation": {"speaker_a": "Caroline", "speaker_b": "Mel"}})
TMP = Path(tempfile.mkdtemp(prefix="nvt3_plan_"))
try:
    print("- blocks (§5.6) -")
    ids = [f"u{i:02d}" for i in range(21)]
    bl = PL.blocks_for("S1", ids, block_plan=SC.BlockPlan)
    check("PL-blocks: blocks of 10 in the committed order, only the last short; ids b01..b03",
          [b.block for b in bl] == ["b01", "b02", "b03"] and [len(b.units) for b in bl] == [10, 10, 1]
          and [u for b in bl for u in b.units] == ids, str([(b.block, len(b.units)) for b in bl]))
    check("PL-blocks: a smoke stand id takes its stand's size (S4-smoke-1: 5)",
          [len(b.units) for b in PL.blocks_for("S4-smoke-1", ids[:7], block_plan=SC.BlockPlan)] == [5, 2])
    r6 = refused(lambda: PL.blocks_for("S6", ids, block_plan=SC.BlockPlan))
    rid = refused(lambda: PL.blocks_for("S1", ["ok", "bad/id"], block_plan=SC.BlockPlan))
    rdup = refused(lambda: PL.blocks_for("S1", ["a", "a"], block_plan=SC.BlockPlan))
    check("PL-blocks: S6 (tiers, not counts), a unit id that is no path component, a repeated id - each refused",
          "S6" in r6 and "unit ids" in rid and "repeats" in rdup, f"{r6} | {rid} | {rdup}")

    print("\n- dates (§5.3, Q-A4-4) -")
    def safe(fn, *a):
        try:
            return fn(*a)
        except Exception as e:  # noqa: BLE001 - the row's FAIL, by name
            return f"raised {type(e).__name__}: {e}"
    got = (safe(PL.iso_datetime, "2023/05/20 (Sat) 02:21"), safe(PL.iso_day, "2023/05/20 (Sat) 02:21"),
           safe(PL.iso_datetime, "1:56 pm on 8 May, 2023"), safe(PL.iso_datetime, "March-15-2024"),
           safe(PL.iso_day, "2024-01-02"))
    check("PL-dates: LME, LoCoMo, BEAM and ISO forms to ISO (naive local time, as the dataset gives it)",
          got == ("2023-05-20T02:21:00", "2023-05-20", "2023-05-08T13:56:00", "2024-03-15T00:00:00", "2024-01-02"),
          str(got))
    bad = [refused(lambda s=s: PL.iso_datetime(s)) for s in ("2023/02/31 (Fri) 10:00", "1:56 pm on 31 February, 2023",
                                                              "yesterday", None, "")]
    check("PL-dates: 31 February, an unknown form, no date - each refused, never guessed",
          all(x != "accepted" and not x.startswith("not refused") for x in bad), str(bad))
    FEB = unit("u9", [("s1", "2023/02/31 (Fri) 10:00", [("u9:1", "x", "user", None)])])
    rf = refused(lambda: PL.write_ops(PL.arm_spec("mem0"), FEB, dated=True))
    check("PL-dates: a unit with a date that does not exist is refused by name, for every arm",
          "u9" in rf and "refused" in rf and "u9" in refused(lambda: PL.write_ops(PL.arm_spec("nevertwice"), FEB,
                                                                                  dated=True)), rf)

    print("\n- the text, the authors (Q-45-4, Q-A4-1, Q-A4-2) -")
    check("PL-text: one '<role|speaker>: <text>' line per item - LME by role, LoCoMo by name",
          PL.session_text(LME.sessions[0]) == "user: hi\nassistant: ok"
          and PL.session_text(LOC.sessions[0]) == "Caroline: hey Mel\nMel: hi Caroline")
    def safe_author(it, m):
        try:
            return PL.author(it, m)
        except Exception as e:  # noqa: BLE001 - the row's FAIL, by name
            return repr(e)
    got_a = [safe_author(LOC.sessions[0].items[0], SMAP), safe_author(LOC.sessions[0].items[1], SMAP),
             safe_author(LME.sessions[0].items[1], None)]
    check("PL-authors: LoCoMo roles from the sample's map (speaker_a user, speaker_b assistant); LME speaker = role",
          got_a == [("user", "Caroline"), ("assistant", "Mel"), ("assistant", "assistant")], str(got_a))
    rb = refused(lambda: PL.write_ops(PL.arm_spec("mem0"), LOC_BAD, dated=True, smap=SMAP))
    check("PL-authors: a LoCoMo speaker outside the sample's two is refused by name (Q-A4-1)",
          "Bob" in rb and "Q-A4-1" in rb, rb)
    check("PL-map-sha: one map in two insertion orders has one sha",
          PL.map_sha256({"Caroline": "user", "Mel": "assistant"}) == PL.map_sha256({"Mel": "assistant", "Caroline": "user"}))
    check("PL-map-sha: the map's sha is stable and names which speaker is which",
          PL.map_sha256(SMAP) == PL.map_sha256(dict(SMAP))
          and PL.map_sha256(SMAP) != PL.map_sha256({"Caroline": "assistant", "Mel": "user"}))

    print("\n- each arm's write ops -")
    nw = PL.write_ops(PL.arm_spec("nevertwice"), LME, dated=True)
    check("PL-ops: ours - one op per session, the session text, the date in its field",
          nw == [{"item": {"item_id": "s1", "session_id": "s1", "text": "user: hi\nassistant: ok"},
                  "date": "2023-05-20T02:21:00"},
                 {"item": {"item_id": "s2", "session_id": "s2", "text": "user: more"}, "date": "2023-05-21T10:00:00"}],
          str(nw))
    m0 = PL.write_ops(PL.arm_spec("mem0"), LOC, dated=True, smap=SMAP)
    check("PL-ops: mem0 - one op per message, its role (mapped) and name, the date for its header",
          [(o["item"].get("role"), o["item"].get("speaker"), o["item"]["text"], o["date"]) for o in m0]
          == [("user", "Caroline", "hey Mel", "2023-05-08T13:56:00"), ("assistant", "Mel", "hi Caroline",
                                                                        "2023-05-08T13:56:00")], str(m0))
    am = PL.write_ops(PL.arm_spec("a-mem"), LOC, dated=True, smap=SMAP)
    zg = PL.write_ops(PL.arm_spec("zep-graphiti"), LME, dated=True)
    check("PL-ops: a-mem and graphiti - a name, no role, the date in the op (their time= / reference_time=)",
          all("role" not in o["item"] for o in am + zg) and am[0]["item"]["speaker"] == "Caroline"
          and zg[0]["item"]["speaker"] == "user" and zg[0]["date"] == "2023-05-20T02:21:00", f"{am[0]} {zg[0]}")
    lg = PL.write_ops(PL.arm_spec("langmem"), LOC, dated=True, smap=SMAP)
    check("PL-ops: langmem - one thread per session, each message with its role and name",
          len(lg) == 1 and lg[0]["item"]["messages"] == [{"role": "user", "speaker": "Caroline", "text": "hey Mel"},
                                                         {"role": "assistant", "speaker": "Mel", "text": "hi Caroline"}],
          str(lg))
    import copy  # noqa: PLC0415
    lg2 = copy.deepcopy(lg)
    lg2[0]["item"]["messages"][1]["text"] = "hi Caroline!"
    want_sha = hashlib.sha256(json.dumps(lg[0]["item"]["messages"], sort_keys=True, ensure_ascii=False)
                              .encode("utf-8")).hexdigest()
    check("PL-ops: a thread op's sha is its messages' canonical JSON, and one changed message changes it",
          PL.item_shas(lg) == {lg[0]["item"]["item_id"]: want_sha} and PL.item_shas(lg2) != PL.item_shas(lg),
          f"{PL.item_shas(lg)} {want_sha}")
    check("PL-ops: a memory-store arm's spec is 'both'; an arm without an adapter is refused by name (A8)",
          PL.arm_spec("langmem").store_persistence == "memory" and PL.arm_spec("a-mem").store_persistence == "memory"
          and "A8" in refused(lambda: PL.arm_spec("cognee")) and "not an arm" in refused(lambda: PL.arm_spec("zz")))

    print("\n- the retrieval tier's bytes (Q-A4-3, §5.1) -")
    tr = TK.Truncator(spans)
    b25 = PL.write_ops(PL.arm_spec("bm25-floor"), LOC, dated=True, smap=SMAP, truncate=tr.cut)
    chr_ = PL.write_ops(PL.arm_spec("chroma-store"), LOC, dated=True, smap=SMAP, truncate=tr.cut)
    check("PL-retrieval: the same bytes for two retrieval arms, with the §5.3 header and the name prefix",
          [o["item"]["text"] for o in b25] == [o["item"]["text"] for o in chr_]
          and b25[0]["item"]["text"] == "Conversation from 2023-05-08:\nCaroline: hey Mel"
          and [o["item"]["index"] for o in b25] == [0, 1] and PL.item_shas(b25) == PL.item_shas(chr_), str(b25))
    und = PL.write_ops(PL.arm_spec("bm25-floor"), LME, dated=False, truncate=tr.cut)
    check("PL-retrieval: an undated stand - the prefixed line alone, no header, no date; indices run over the unit",
          und[0]["item"]["text"] == "user: hi" and all(o["date"] is None for o in und)
          and [o["item"]["index"] for o in und] == [0, 1, 2], str(und))
    small = TK.Truncator(spans, cap=7, specials=2)          # 5 content tokens
    LONG = unit("u5", [("s1", "2023/05/20 (Sat) 02:21", [("u5:1", "one two three four five", "user", None)])])
    cut = PL.write_ops(PL.arm_spec("bm25-floor"), LONG, dated=True, truncate=small.cut)[0]["item"]["text"]
    full = "Conversation from 2023-05-20:\nuser: one two three four five"
    check("PL-retrieval: a header-and-prefix string over the cap is cut as ONE string by §5.1 (a prefix of it, the "
          "header kept)", cut == "Conversation from 2023-05-20:\nuser: one" and full.startswith(cut)
          and small.truncated == 1, repr(cut))
    rbr = refused(lambda: PL.write_ops(PL.arm_spec("bm25-floor"), LOC_BAD, dated=True, smap=SMAP, truncate=tr.cut))
    check("PL-retrieval: a LoCoMo speaker outside the sample's two is refused for the retrieval tier too - the same "
          "refusal for every arm", "Bob" in rbr and "Q-A4-1" in rbr, rbr)
    check("PL-retrieval: no truncator, no retrieval ops", "truncator" in refused(
        lambda: PL.write_ops(PL.arm_spec("bm25-floor"), LME, dated=True)))

    ARMS_DIR = ROOT / "research" / "v3" / "arms"
    LOCALS = {p_.stem: p_ for p_ in ARMS_DIR.glob("*.py")} | {"_ollama_pacer": ROOT / "research" / "_ollama_pacer.py"}

    def local_imports(path: Path, seen: set) -> set:
        """The local modules a file imports, transitively (AST: import X / from X import ..., at any depth)."""
        out = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else []
            for n in names:
                top = n.split(".")[0]
                if top in LOCALS and top not in seen:
                    seen.add(top)
                    out |= {top} | local_imports(LOCALS[top], seen)
        return out

    gaps = {}
    for name, spec in PL.ARMS.items():
        if spec.ours:
            continue
        need = {f"{m}.py" for m in local_imports(ARMS_DIR / spec.adapter, {Path(spec.adapter).stem})}
        missing = sorted(need - set(spec.code) - {spec.adapter})
        if missing:
            gaps[name] = missing
    check("PL-code-copy: every local module a competitor adapter imports, transitively, is in its Q9 copy",
          gaps == {}, str(gaps))

    print("\n- reads and code -")
    rp = PL.read_plan(LME, points=("B", "K"), read_req=SC.ReadReq, k_at=PT.K_AT)
    check("PL-read-plan: one read per question and point, k from points.K_AT",
          [(r.qid, r.point, r.k, r.query) for r in rp] == [("u1:q1", "B", PT.K_AT["B"], "what?"),
                                                           ("u1:q1", "K", PT.K_AT["K"], "what?")], str(rp))
    check("PL-read-plan: an unknown point is refused", "no k" in refused(
        lambda: PL.read_plan(LME, points=("V",), read_req=SC.ReadReq, k_at=PT.K_AT)))
    src = TMP / "src"
    src.mkdir()
    (src / "arm_x.py").write_text("print('adapter')\n", encoding="utf-8")
    (src / "base.py").write_text("P = 1\n", encoding="utf-8")
    cs = PL.CodeStager(TMP / "runs")
    got = cs.stage("S1", "r1", "mem0", "b01", {"arm_x.py": src / "arm_x.py", "base.py": src / "base.py"})
    ok_copy = all(Path(v["path"]).read_bytes() == (src / k).read_bytes() for k, v in got.items())
    check("PL-code-copy: byte copies in <runs>/<stand>/<run>/<arm>/_code/<block>/, each sha recorded (Q9)",
          ok_copy and Path(got["base.py"]["path"]).parent == TMP / "runs" / "S1" / "r1" / "mem0" / "_code" / "b01"
          and len(cs.records) == 1, str(got))
    again = refused(lambda: cs.stage("S1", "r1", "mem0", "b01", {"base.py": src / "base.py"}))
    badn = refused(lambda: cs.stage("S1", "r1", "mem0", "b02", {"../x.py": src / "base.py"}))
    check("PL-code-copy: a block's copy is made once (fresh), and a copy name is a plain module file",
          "already exists" in again and "plain module" in badn, f"{again} | {badn}")
    _real_copy = PL.shutil.copyfile
    PL.shutil.copyfile = lambda s_, d_: Path(d_).write_bytes(Path(s_).read_bytes() + b"#")   # a copy that differs
    try:
        bad_copy = refused(lambda: cs.stage("S1", "r1", "mem0", "b03", {"base.py": src / "base.py"}))
    finally:
        PL.shutil.copyfile = _real_copy
    check("PL-code-copy: a copy whose sha256 is not its source's is refused (Q9)", "sha256" in bad_copy, bad_copy)

    print("\n- PL-python (Q-A4-5): the interpreter an arm runs on, declared -")
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    base_py = Path(getattr(sys, "_base_executable", sys.executable))
    venv = TMP / "arms-venv"
    mk = subprocess.run([str(base_py), "-m", "venv", "--without-pip", str(venv)], capture_output=True, timeout=300)
    vpy = next((q for q in (venv / "Scripts" / "python.exe", venv / "bin" / "python") if q.is_file()), None)
    site = next(iter(sorted(venv.rglob("site-packages"))), None)
    check("PL-python: a venv made from this interpreter's base", mk.returncode == 0 and vpy is not None
          and site is not None, f"rc={mk.returncode} {mk.stderr[-300:]!r}")

    def dist(where: Path, name: str, version: str = "1.0") -> Path:
        d = where / f"{name}-{version}.dist-info"
        d.mkdir(parents=True, exist_ok=True)
        (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")
        return d

    decl = PL.python_decl(vpy, arm="bm25-floor") if vpy else {}
    want_sha = hashlib.sha256(base_py.read_bytes()).hexdigest()
    check("PL-python: the declaration is the interpreter's own - its version, base python.exe and that file's sha256, "
          "a venv, no package", decl.get("version") == ".".join(map(str, sys.version_info[:3]))
          and Path(decl.get("base_executable", "")).resolve() == base_py.resolve()
          and decl.get("base_sha256") == want_sha and decl.get("venv") is True and decl.get("packages") == [],
          str({k: decl.get(k) for k in ("version", "base_executable", "venv", "packages")}))
    if site is not None:
        dist(site, "pip", "26.0.1")
    only_pip = {a: (PL.python_decl(vpy, arm=a) if vpy else {}).get("packages") for a in sorted(PL.STDLIB_ARMS)}
    check("PL-python: a venv with only pip carries every arm that needs only the standard library - ours and letta",
          only_pip and all(v == ["pip"] for v in only_pip.values()), str(only_pip))
    here = Path.cwd()
    os.chdir(TMP)
    try:
        dist(TMP, "stray_pkg")                          # beside the probe's working directory
        stray = refused(lambda: PL.python_decl(vpy, arm="nevertwice"))
    finally:
        os.chdir(here)
    check("PL-python: a distribution in the probe's working directory is not seen - the probe runs isolated (-I)",
          stray == "accepted", stray)
    if site is not None:
        dist(site, "psutil", "7.0.0")
    extra = {a: refused(lambda a=a: PL.python_decl(vpy, arm=a)) for a in ("nevertwice", "bm25-floor", "letta")}
    comp = PL.python_decl(vpy, arm="mem0") if vpy else {}
    check("PL-python: a package besides pip in the venv refuses our arms and letta by name - and a competitor's list is "
          "recorded as it is", all("psutil" in m and "besides pip" in m for m in extra.values())
          and comp.get("packages") == ["pip", "psutil"], f"{extra} {comp.get('packages')}")
    bare = refused(lambda: PL.python_decl(base_py, arm="nevertwice-ranker"))
    check("PL-python: the bare base interpreter is refused for our arms - its site-packages is pinned by nothing",
          "not a venv" in bare, bare)
    gone = refused(lambda: PL.python_decl(TMP / "no" / "python.exe", arm="mem0"))
    check("PL-python: an interpreter that is not there is refused by name", "not an absolute path to a file" in gone,
          gone)

    print("\n- PLL (Q-A4-6): PlanLauncher on the plan's real specs, all 13 arms -")
    from types import SimpleNamespace  # noqa: PLC0415
    L = _load("v3_launch_for_plan_t", ROOT / "research" / "v3" / "launch.py")
    RP = _load("v3_run_proxy_for_plan_t", ROOT / "research" / "v3" / "run_v3_proxy.py")
    CT = L.Contract(polygon_root=TMP / "poly", runs_root=TMP / "poly" / "runs" / "v3", repo_root=ROOT,
                    owner_home=TMP / "owner", secrets_dir=TMP / "secrets", quarantine_root=TMP / "quarantine",
                    conservation_root=TMP / "conservation")
    ALL = sorted(PL.ARMS)
    PY = TMP / "poly" / "arms314" / "Scripts" / "python.exe"
    LLM_ARMS = (*PL.RUNNER_LLM, "mem0", "langmem", "a-mem", "zep-graphiti", "letta")
    ports = {"arms": {a: {"reader": 42000 + i, **({"write": 41000 + i} if a in LLM_ARMS else {}),
                          **({"ollama": 43000 + i} if a in ("letta", "a-mem") else {})} for i, a in enumerate(ALL)}}
    sec = RP.build_secrets(ALL)
    PX = SimpleNamespace(ports=ports, tokens=sec["tokens"])
    EXTRA = {**{a: {"extract_temp": 0.0} for a in PL.RUNNER_LLM}, "a-mem": {"llm": "deepseek",
             "product_pin": {"commit": "c" * 40, "blobs": {}}}, "zep-graphiti": {"falkor_host": "127.0.0.1",
             "falkor_port": 6380}, "letta": {"server_url": "http://127.0.0.1:8283", "openapi_sha256": "0" * 64,
             "agent": {"agent_type": "memgpt_agent"}}}
    UB = {"u1": "b01", "u2": "b01", "u3": "b02"}
    stager = PL.CodeStager(CT.runs_root)

    def pll(a, **kw):
        return PL.PlanLauncher(a, stand="S4", python=PY, proxy=PX, stager=stager, unit_block=UB,
                               embed_tag="nvt3-bge-m3-d1:latest", dated=True, unit_chars={u: 5000 for u in UB},
                               extra=EXTRA.get(a, {}), **kw)

    PLS = {a: pll(a) for a in ALL}
    DIRS: dict = {}

    def dirs(a, r, u, q=False):
        k = (a, r, u, q)
        if k not in DIRS:
            DIRS[k] = L.make_unit_dirs(CT, "S4", r, a, f"{u}.q" if q else u)
        return DIRS[k]

    def env_of(a, r, u, stage="write"):
        d = dirs(a, r, u, stage == "read")
        return {**PLS[a].declared(), **PLS[a].declared_for(stage, stand="S4", run=r, unit=u, dirs=d,
                                                            write_dirs=dirs(a, r, u) if stage == "read" else None)}

    def spec_of(a, r, u, stage="write"):
        return PLS[a].spec_for(stage, stand="S4", run=r, unit=u, dirs=dirs(a, r, u, stage == "read"),
                               write_dirs=dirs(a, r, u) if stage == "read" else None)

    specs = {a: None for a in ALL}
    bad_keys = {}
    for a in ALL:
        try:
            specs[a] = spec_of(a, "r1", "u1")
            if set(specs[a]) != set(PL.adapter_constant(PL.ARMS[a].adapter, "SPEC_KEYS")):
                bad_keys[a] = sorted(set(specs[a]))
        except Exception as e:  # noqa: BLE001 - a refused spec is this row's FAIL, by name
            bad_keys[a] = f"{type(e).__name__}: {e}"
    check("PLL: every arm's spec has exactly its adapter's SPEC_KEYS (read from the adapter's source)", not bad_keys,
          str(bad_keys))
    mem = {a: (specs[a] or {}).get("stage") for a in ALL if PL.ARMS[a].store_persistence == "memory"}
    check("PLL (B-P1): langmem-store is a memory arm like langmem and a-mem - its one child is the 'both' stage",
          PL.ARMS["langmem-store"].store_persistence == "memory" and mem == {"a-mem": "both", "langmem": "both",
                                                                              "langmem-store": "both"}
          and all((specs[a] or {}).get("stage") == "write" for a in ALL if a not in mem), str(mem))
    disk = [a for a in ALL if PL.ARMS[a].store_persistence == "disk"]
    rd = {a: spec_of(a, "r1", "u1", "read") for a in disk}
    check("PLL: a disk arm's read stage opens the write stage's directory (unit_dir) and writes its record in its own "
          "home", all(rd[a]["unit_dir"] == str(dirs(a, "r1", "u1").cwd) and rd[a]["stage"] == "read"
                      and Path(rd[a]["record_path"]).parent == Path(dirs(a, "r1", "u1", True).home) for a in disk),
          str({a: rd[a]["unit_dir"] for a in disk[:2]}))
    mread = refused(lambda: spec_of("a-mem", "r1", "u1", "read"))
    check("PLL: a memory arm has no read child - asked for one, the plan refuses by name", "no read child" in mread,
          mread)
    names = {a: {frozenset(env_of(a, r, u)) for r, u in (("r1", "u1"), ("r1", "u2"), ("r2", "u3"))} for a in ALL}
    check("PLL (1): every arm's environment names are the same in every unit", all(len(v) == 1 for v in names.values()),
          str({a: [sorted(x) for x in v] for a, v in names.items() if len(v) != 1}))
    want_url = {}
    for a in PL.RUNNER_LLM:
        w = ports["arms"][a]["write"]
        want_url[a] = [(env_of(a, r, u).get("DEEPSEEK_URL"), f"http://127.0.0.1:{w}/u/{r}.{u}/v1/chat/completions")
                       for r, u in (("r1", "u1"), ("r2", "u3"))]
    w = ports["arms"]["a-mem"]["write"]
    want_url["a-mem"] = [(env_of("a-mem", r, u).get("OPENAI_BASE_URL"), f"http://127.0.0.1:{w}/u/{r}.{u}/v1")
                         for r, u in (("r1", "u1"), ("r2", "u3"))]
    for a in ("mem0", "langmem", "zep-graphiti", "letta"):
        want_url[a] = []
        for r, u in (("r1", "u1"), ("r2", "u3")):
            s_ = spec_of(a, r, u)
            want_url[a].append(((s_["port"], s_["run"], s_["unit"]), (ports["arms"][a]["write"], r, u)))
    want_url["letta-ollama"] = [(specs["letta"]["ollama_leg_port"], ports["arms"]["letta"]["ollama"])]
    off = {a: v for a, v in want_url.items() if any(got != want for got, want in v)}
    check("PLL (2): every URL to the proxy is the arm's own port with /u/<run>.<unit> of the unit it runs - in the "
          "environment (the runner's DEEPSEEK_URL, a-mem's OPENAI_BASE_URL) or from the spec's port, run and unit",
          not off, str(off))
    nollm = [a for a in ALL if a not in LLM_ARMS]
    check("PLL (2): an arm without an LLM gets no proxy port in its spec and no URL in its environment",
          all((specs[a] or {}).get("port") is None and not any("URL" in k for k in env_of(a, "r1", "u1"))
              for a in nollm), str({a: specs[a].get("port") for a in nollm}))
    probs = {}
    PENV = {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows")} if os.name == "nt" else {}
    for a in ALL:
        ln = PLS[a]
        env = L.build_env(CT, parent_env=PENV, unit=dirs(a, "r1", "u1"), path_dirs=(str(PY.parent),),
                          declared=env_of(a, "r1", "u1"), catcher_url="http://127.0.0.1:47001/", hf_offline=True)
        n_ = ln.token_name()
        pr = L.assert_env(CT, env, parent_env=PENV, catcher_url="http://127.0.0.1:47001/",
                          token_names=(n_,) if n_ else ())
        if pr:
            probs[a] = pr
    check("PLL (3): every arm's environment passes launch.build_env and assert_env with build_secrets' tokens", not probs,
          str(probs))
    tok = {a: PLS[a].declared() for a in ALL}
    check("PLL (3): the proxy token goes under the one name the adapter checks - DeepSeek's for ours, mem0, langmem and "
          "zep; OpenAI's for a-mem; none for letta (its server's) or the arms without an LLM",
          {a: sorted(v) for a, v in tok.items() if v} == {**{a: ["DEEPSEEK_API_KEY"] for a in (*PL.RUNNER_LLM, "mem0",
                                                                                             "langmem", "zep-graphiti")},
                                                          "a-mem": ["OPENAI_API_KEY"]}
          and all(v_ == sec["tokens"][a] for a, d_ in tok.items() for v_ in d_.values()), str({a: sorted(v) for a, v in tok.items()}))
    argvs = {a: PLS[a].argv_for(Path(dirs(a, "r1", "u1").home) / "spec.write.json", stage="write", stand="S4",
                                run="r1", unit="u1") for a in ALL}
    ours = [a for a in ALL if PL.ARMS[a].ours]
    comp = [a for a in ALL if not PL.ARMS[a].ours]
    check("PLL (4): our arms run by their script in the repository, with that one argv exception {2: script}",
          sorted(ours) == sorted([*PL.RUNNER_LLM, "nevertwice-ranker", "bm25-floor"])
          and all(argvs[a][2] == str(PL.ARMS_DIR / PL.ARMS[a].adapter) and PLS[a].argv_exception() == {2: argvs[a][2]}
                  and L.assert_argv(CT, argvs[a], argv_exception={2: Path(argvs[a][2])}) == [] for a in ours),
          str({a: argvs[a][1:3] for a in ours[:2]}))
    root_s = str(ROOT).lower()
    check("PLL (4): a competitor runs from its _code copy - no repository path in its argv, no argv exception, no "
          "PYTHONPATH - and launch.assert_argv passes it with no exception",
          all(Path(argvs[a][2]).parent == CT.runs_root / "S4" / "r1" / a / "_code" / "b01"
              and not any(root_s in str(x).lower() for x in argvs[a]) and PLS[a].argv_exception() is None
              and "PYTHONPATH" not in env_of(a, "r1", "u1") and L.assert_argv(CT, argvs[a]) == [] for a in comp),
          str({a: argvs[a][2] for a in comp[:2]}))
    gave = pll("mem0")
    gave.argv_exception = lambda: {2: str(PL.ARMS_DIR / "arm_mem0.py")}
    lost = pll("bm25-floor")
    lost.argv_exception = lambda: None
    gave0 = pll("mem0")
    gave0.argv_exception = lambda: {}                                   # an empty one is still one (the auditor's note)
    r_gave, r_lost = refused(lambda: gave.launcher(SC.ChildArmLauncher)), refused(lambda: lost.launcher(SC.ChildArmLauncher))
    r_gave0 = refused(lambda: gave0.launcher(SC.ChildArmLauncher))
    check("PLL (4, O1 (c)): a competitor given an argv exception - even an empty one - is refused by name before any "
          "spawn, and so is our arm without its own; each message says which", "competitor given one" in r_gave
          and "competitor given one" in r_gave0 and "without its own" in r_lost, f"{r_gave} | {r_gave0} | {r_lost}")
    lns = {a: PLS[a].launcher(SC.ChildArmLauncher) for a in ALL}
    check("PLL (5): every arm's path_dirs is its python's directory alone", all(lns[a].path_dirs == (str(PY.parent),)
                                                                                 for a in ALL),
          str({a: lns[a].path_dirs for a in ALL if lns[a].path_dirs != (str(PY.parent),)}))
    check("PLL: each launcher carries the plan's persistence, point reads, token name and argv exception",
          all(lns[a].store_persistence == PL.ARMS[a].store_persistence and lns[a].reads_point == PL.ARMS[a].reads_point
              and lns[a].argv_exception == PLS[a].argv_exception() and lns[a].declared == PLS[a].declared()
              for a in ALL), "")
    def argv_or_err(spec_path, run, unit):
        """argv_for, or a placeholder argv naming the error - a refusal is this row's FAIL by name."""
        try:
            return PLS["mem0"].argv_for(spec_path, stage="write", stand="S4", run=run, unit=unit)
        except Exception as e:  # noqa: BLE001
            return ["", "", f"{type(e).__name__}: {e}"]

    a2, a3, a4 = argv_or_err("s2", "r1", "u2"), argv_or_err("s3", "r1", "u3"), argv_or_err("s4", "r2", "u1")
    mem0_copies = [r_ for r_ in stager.records if r_["arm"] == "mem0"]
    check("PLL: a competitor's copy is staged once per (run, block) - two units of a block share it, another block or "
          "run gets its own", a2[2] == argvs["mem0"][2] and Path(a3[2]).parent.name == "b02"
          and Path(a4[2]).parents[3].name == "r2" and len(mem0_copies) == 3, f"{[a2[2], a3[2], a4[2]]} {len(mem0_copies)}")
    if Path(a3[2]).is_file() and CT.runs_root in Path(a3[2]).parents:   # only ever the staged copy, never a source
        Path(a3[2]).write_bytes(Path(a3[2]).read_bytes() + b"# changed")
    tampered = refused(lambda: PLS["mem0"].argv_for("s5", stage="read", stand="S4", run="r1", unit="u3"))
    check("PLL (Q9): a copy changed after it was staged is refused at the next spawn", "changed since it was staged"
          in tampered, tampered)
    recs = {a: PLS[a].record(interpreter={"packages": ["pip"]}) for a in ALL}
    blob = json.dumps(recs, sort_keys=True)
    check("PLL: the launch record names the token, never holds it - and records the argv exception, spec keys, ports "
          "and the arm's copies", "nvt3-" not in blob and recs["mem0"]["declared_names"] == ["DEEPSEEK_API_KEY"]
          and recs["mem0"]["code_copies"] and recs["bm25-floor"]["argv_exception"] == {2: argvs["bm25-floor"][2]},
          blob[:200])
    ids = {x: refused(lambda x=x: PL.check_ids((), (x,))) for x in ("u.1", "x" * 65, "_code", "", "conv-26",
                                                                    "gpt4_2655b836", "x" * 64)}
    check("PLL (B-P2): a run or unit id is [A-Za-z0-9_-]{1,64} without a leading '_' - a dot, 65 characters, '_code' "
          "or an empty id is refused by name", all("not [A-Za-z0-9_-]" in ids[x] for x in ("u.1", "x" * 65, "_code", ""))
          and all(ids[x] == "accepted" for x in ("conv-26", "gpt4_2655b836", "x" * 64)), str(ids))
    dot = refused(lambda: spec_of("mem0", "r.1", "u1"))
    check("PLL (B-P2): a dotted run id is refused before a spec is written (it would split /u/<run>.<unit>)",
          "not [A-Za-z0-9_-]" in dot, dot)
    ex = {"missing": refused(lambda: PL.PlanLauncher("zep-graphiti", stand="S4", python=PY, proxy=PX, stager=stager,
                                                     unit_block=UB, embed_tag="t", dated=True, extra={})),
          "unknown": refused(lambda: PL.PlanLauncher("mem0", stand="S4", python=PY, proxy=PX, stager=stager,
                                                     unit_block=UB, embed_tag="t", dated=True, extra={"llm": "x"}))}
    check("PLL: the run config's values for an arm are exactly what it takes - a missing or a stray one is refused",
          all("the run config's values" in v for v in ex.values()), str(ex))

    class Built(Exception):
        pass

    class Cap:
        def spawn_child(self, build, **kw):
            d = L.make_unit_dirs(CT, "S4", "r9", kw["arm"], kw["unit"])
            self.spec = build(d)
            raise Built()

    def via_sched(a):
        c_ = Cap()
        try:
            lns[a].open("write", sched=c_, stand="S4", run="r9", unit="u1")
        except Built:
            return c_.spec
        return None

    ls_ = {a: via_sched(a) for a in ("nevertwice-ablation", "a-mem", "mem0", "bm25-floor")}
    def decl(a, name):
        """A LaunchSpec's declared value, or None - a scheduler that drops declared_for FAILs this row by name."""
        return str(((ls_.get(a) and ls_[a].declared) or {}).get(name))

    check("PLL: through the scheduler's ChildArmLauncher - the unit's own URL and window, the token, our exception, the "
          "competitor's copy", all(ls_.get(a) is not None for a in ls_)
          and decl("nevertwice-ablation", "NEVERTWICE_MAX_TRANSCRIPT") == "5000"
          and "/u/r9.u1/" in decl("nevertwice-ablation", "DEEPSEEK_URL")
          and decl("a-mem", "OPENAI_BASE_URL").endswith("/u/r9.u1/v1")
          and ls_["mem0"].argv_exception is None and "_code" in ls_["mem0"].argv[2]
          and ls_["bm25-floor"].argv_exception == {2: ls_["bm25-floor"].argv[2]}, str(ls_))
    fewer, more = pll("mem0"), pll("mem0")
    fewer.spec_keys = tuple(k for k in fewer.spec_keys if k != "dated")     # the plan gives a key the adapter lacks
    more.spec_keys = (*more.spec_keys, "extra_key")                          # the adapter wants a key the plan lacks
    r_few = refused(lambda: fewer.spec_for("write", stand="S4", run="r1", unit="u1", dirs=dirs("mem0", "r1", "u1"),
                                           write_dirs=None))
    r_more = refused(lambda: more.spec_for("write", stand="S4", run="r1", unit="u1", dirs=dirs("mem0", "r1", "u1"),
                                           write_dirs=None))
    check("PLL (PP16): a spec whose keys differ from the adapter's SPEC_KEYS either way - a key the adapter lacks, or "
          "one it wants that the plan does not give - is refused by name before any spawn",
          "differ from arm_mem0.py's SPEC_KEYS" in r_few and "'dated'" in r_few
          and "differ from arm_mem0.py's SPEC_KEYS" in r_more and "'extra_key'" in r_more, f"{r_few} | {r_more}")

    print("\n- AN (§4.3a, §5.2, §8.1, Q8): the Answerer -")
    TPL = _load("v3_templates_for_plan_t", ROOT / "research" / "v3" / "templates.py")
    RJ = _load("v3_reader_judge_for_plan_t", ROOT / "research" / "v3" / "reader_judge.py")
    PXM = _load("v3_llm_proxy_for_plan_t", ROOT / "research" / "_llm_proxy.py")
    TEXT = "{context}\n\nQuestion: {question}\n" + RJ.SHORT_ANSWER_INSTRUCTION
    T4 = TPL.Template(stand="S4", text=TEXT, sha256=hashlib.sha256(TEXT.encode()).hexdigest(), source_pin="p",
                      source_sha256="0" * 64, slots=("context", "question"))
    QS = {("u1", "u1:q1"): (T4, "what did I say?"), ("u1", "u1:q2"): (T4, "and then?")}
    calls = []
    script = {"replies": []}

    def fake_post(port, path, body, token, *, timeout):
        calls.append((port, path, body, token))
        r = script["replies"].pop(0) if script["replies"] else (200, "fine\nSHORT ANSWER: hi")
        if isinstance(r, tuple) and isinstance(r[1], str):
            return r[0], {"choices": [{"message": {"content": r[1]}}], "usage": {"prompt_tokens": 7}}
        return r

    def reader(arm, run, unit):
        return 45000, f"/u/{run}.{unit}/v1/chat/completions", f"tok-{arm}"

    def wcount(s):
        return len(s.split())

    def wcut(s, n):
        sp_ = spans(s)
        return s[:sp_[n - 1][1]] if n > 0 and sp_ else ""

    ans = PL.Answerer("S4", questions=QS, reader=reader, post=fake_post, count=wcount, cut=wcut,
                      answers_root=TMP / "ans", reaskable=SC.ReaskableError)
    READ = {"items": [{"text": "hi from the store", "index": 0}, {"text": "second item", "index": 1}]}
    req1 = SC.ReadReq(qid="u1:q1", query="what did I say?", point="B", k=200)
    row = ans("mem0", "r1", "u1", req1, READ)
    port_, path_, body_, tok_ = calls[0]
    check("AN: the reader request is READER_PARAMS exactly, the arm's reader port and token, /u/<run>.<unit>",
          body_ == RJ.reader_request(body_["messages"][0]["content"]) and body_["model"] == "deepseek-flash"
          and {k: body_[k] for k in RJ.READER_PARAMS} == RJ.READER_PARAMS and port_ == 45000
          and path_ == "/u/r1.u1/v1/chat/completions" and tok_ == "tok-mem0", str(calls[0])[:300])
    prompt = body_["messages"][0]["content"]
    check("AN: the prompt is the template filled with the arm's items in their order and the question as asked",
          prompt == TPL.render(T4, {"context": "hi from the store\nsecond item", "question": "what did I say?"}),
          prompt[:120])
    blob = json.dumps(row, sort_keys=True)
    fpath = TMP / "ans" / "S4" / row["text_file"]
    saved = json.loads(fpath.read_bytes().decode("utf-8")) if fpath.is_file() else {}
    check("AN (Q8): the row holds sha256 values and counts, never the context, the reply or the question",
          "hi from the store" not in blob and "SHORT ANSWER" not in blob and "what did I say" not in blob
          and row["context_sha256"] == PL.text_sha256(saved.get("context", "")) and row["reply_sha256"]
          == PL.text_sha256(saved.get("reply", "")) and row["short_answer_sha256"] == PL.text_sha256("hi")
          and row["context_tokens"] == 6 and row["items_used"] == 2, blob[:300])
    name = hashlib.sha256(("u1:q1" + "\0" + "B").encode("utf-8")).hexdigest() + ".json"
    check("AN (Q8): the texts are in one fresh file per answer - <answers>/S4/_answers/<run>/<arm>/<unit>/"
          "<sha256(qid, point)>.json - its sha256 in the row", fpath == TMP / "ans" / "S4" / "_answers" / "r1" / "mem0"
          / "u1" / name and row["text_sha256"] == hashlib.sha256(fpath.read_bytes()).hexdigest()
          and saved.get("qid") == "u1:q1", str(fpath))
    again = refused(lambda: ans("mem0", "r1", "u1", req1, READ))
    check("AN: a second answer for the same question and point is refused (the file is fresh)", "already written" in again,
          again)
    k1 = PXM.request_key(body_, "mem0")
    check("AN: key_question maps (unit, the proxy's request key) to the qid - accounting's map",
          ans.key_question.get(("u1", k1)) == "u1:q1" and row["request_keys"] == [k1], str(ans.key_question))
    calls.clear()
    script["replies"] = [(200, "no line here"), (200, "SHORT ANSWER: later")]
    req2 = SC.ReadReq(qid="u1:q2", query="and then?", point="B", k=200)
    row2 = ans("mem0", "r1", "u1", req2, READ)
    check("AN: the reader's re-ask (no SHORT ANSWER line) is keyed too - both requests map to the question",
          row2["reasked"] and len(calls) == 2 and all(ans.key_question.get(("u1", PXM.request_key(c[2], "mem0")))
                                                      == "u1:q2" for c in calls), str(row2)[:200])
    outs = {}
    for label, reply in (("none", (None, {"error": "ConnectionRefusedError"})), ("502", (502, {"error": "x"})),
                         ("no-choices", (200, {"choices": []})),
                         ("500 with choices", (500, {"choices": [{"message": {"content": "SHORT ANSWER: x"}}]}))):
        script["replies"] = [reply]
        calls.clear()
        try:
            ans("bm25-floor", "r1", "u1", SC.ReadReq(qid="u1:q1", query="q", point="K", k=10), READ)
            outs[label] = "answered"
        except SC.ReaskableError as e:
            outs[label] = ("reaskable", ans.key_question.get(("u1", PXM.request_key(calls[0][2], "bm25-floor"))))
        except Exception as e:  # noqa: BLE001 - not the scheduler's re-ask: the row's FAIL
            outs[label] = f"{type(e).__name__}: {e}"
    check("AN: no reply, a 502, a reply without choices, a 500 that carries choices (PP11: never taken for an answer) "
          "- each the scheduler's ReaskableError (W3 re-asks it), its request keyed all the same",
          len(outs) == 4 and all(v == ("reaskable", "u1:q1") for v in outs.values()), str(outs))
    script["replies"] = []
    ra = PL.Answerer("S4", questions=QS, reader=reader, post=fake_post, count=wcount, cut=wcut,
                     answers_root=TMP / "ans2", reaskable=SC.ReaskableError)
    calls.clear()
    rows = {a: ra(a, "r1", "u1", req1, READ) for a in ("mem0", "a-mem", "letta")}
    check("AN: the prompt is the stand's - the same for every arm on the same read (never a template per arm)",
          len({r_["prompt_sha256"] for r_ in rows.values()}) == 1 and len({c[3] for c in calls}) == 3, str(rows)[:200])
    nolist = refused(lambda: ra("mem0", "r2", "u1", req1, {"items": "text"}))
    notext = refused(lambda: ra("mem0", "r2", "u1", req1, {"items": [{"index": 0}]}))
    check("AN: a read that returned no list of item texts is refused by name - never answered from nothing",
          "no list of item texts" in nolist and "no list of item texts" in notext, f"{nolist} | {notext}")

    print("\n- TR, SP: the truncation per arm-run, stand_plan -")
    trunc = TK.Truncator(spans, cap=9, specials=2)            # 7 words: the dated LME lines (5) fit, u5:1 (11) does not
    LONG = unit("u5", [("s1", "2023/05/20 (Sat) 02:21", [("u5:1", "one two three four five six seven", "user", None),
                                                         ("u5:2", "short", "assistant", None)])])
    pts_of = {"a-mem": ("B", "K")}
    sp, st = PL.stand_plan("S1", [LME, LONG], {a: lns[a] for a in ("bm25-floor", "chroma-store", "mem0", "a-mem")},
                           standplan=SC.StandPlan, read_req=SC.ReadReq, runs=("r1", "r2"), campaign_seed=7,
                           unit_tokens={"u1": 10, "u5": 20}, medians={}, answer=ra, embed_tag="e", dated=True,
                           points=lambda a: pts_of.get(a, ("B",)), k_at=PT.K_AT, truncate=trunc.cut)
    for a in ("bm25-floor", "chroma-store", "mem0"):
        for u in ("u1", "u5"):
            sp.write_ops(a, "r1", u)
    tr = {a: st.truncation(a, "r1") for a in ("bm25-floor", "chroma-store", "mem0")}
    check("TR (A5 condition): each retrieval arm-run records its items cut by §5.1 - {items, truncated, share} - and a "
          "product that embeds its own text is not-applicable", tr["bm25-floor"] == {"items": 5, "truncated": 1,
                                                                                     "share": 0.2}
          and tr["chroma-store"] == tr["bm25-floor"] and str(tr["mem0"]).startswith("not-applicable")
          and st.truncation("bm25-floor", "r2") == {"items": 0, "truncated": 0, "share": 0.0}, str(tr))
    check("SP: the ops of one (arm, run, unit) are the plan's write_ops, their shas kept per arm-run - the same bytes "
          "for every retrieval arm (Q-45-4)", sp.write_ops("mem0", "r2", "u1") == PL.write_ops(PL.arm_spec("mem0"), LME,
                                                                                               dated=True)
          and st.shas[("bm25-floor", "r1", "u5")] == st.shas[("chroma-store", "r1", "u5")]
          and st.shas[("mem0", "r2", "u1")], str(sorted(st.shas))[:200])
    rp_ = {a: sp.read_plan(a, "u1") for a in ("a-mem", "bm25-floor")}
    check("SP (Q-12-3): the reads are per (arm, unit) - the arm's own points, k from K_AT",
          [(r_.point, r_.k) for r_ in rp_["a-mem"]] == [("B", 200), ("K", 10)]
          and [(r_.point, r_.k) for r_ in rp_["bm25-floor"]] == [("B", 200)], str(rp_))
    rec5 = sp.record_extra("bm25-floor", "r1", ["u5"]) if sp.record_extra else {"truncation": None, "item_sha256": {}}
    bdir = TMP / "px-run"
    (bdir / "bodies" / "mem0").mkdir(parents=True)
    (bdir / "bodies" / "mem0" / "r1.u1.jsonl").write_bytes(b'{"strings": ["x"]}\n')
    st_b = PL.PlanState(bodies_dir=bdir)
    bsha = st_b.bodies_sha256("mem0", "r1", ["u1", "u5"])
    check("Q-A5-1: the run record's plan part names each unit's bodies file by its sha256 - None where the writer sent "
          "nothing, and no field at all without the proxy's run directory",
          bsha == {"u1": hashlib.sha256(b'{"strings": ["x"]}\n').hexdigest(), "u5": None}
          and st_b.record_for("mem0", "r1", ["u1"])["bodies_sha256"] == {"u1": bsha["u1"]}
          and PL.PlanState().bodies_sha256("mem0", "r1", ["u1"]) is None, str(bsha))
    check("TR (A5 condition): the run record's plan part - the truncation over the BLOCK's units, each op's sha256, "
          "the speaker map's sha - is StandPlan.record_extra", rec5["truncation"] == {"items": 2, "truncated": 1,
                                                                                     "share": 0.5}
          and set(rec5["item_sha256"]) == {"u5"} and rec5["item_sha256"]["u5"] == st.shas[("bm25-floor", "r1", "u5")]
          and rec5["speaker_map_sha256"] == {"u5": None}, str(rec5)[:300])
    LOC2 = unit("conv-3", [("conv-3:session_1", "2:00 pm on 9 May, 2023",
                            [("conv-3:D1:1", "hello Joanna", None, "Nate"), ("conv-3:D1:2", "hi Nate", None, "Joanna")])])
    samples = [{"sample_id": "conv-1", "conversation": {"speaker_a": "Caroline", "speaker_b": "Mel"}},
               {"sample_id": "conv-3", "conversation": {"speaker_a": "Nate", "speaker_b": "Joanna"}}]
    smaps = PL.speaker_maps(samples)
    sp2, st2 = PL.stand_plan("S4", [LOC, LOC2], {"mem0": lns["mem0"]}, standplan=SC.StandPlan, read_req=SC.ReadReq,
                             runs=("r1",), campaign_seed=7, unit_tokens={"conv-1": 1, "conv-3": 1}, medians={},
                             answer=ra, embed_tag="e", dated=True, points=lambda a: ("B",), k_at=PT.K_AT, smaps=smaps)
    w1 = refused(lambda: sp2.write_ops("mem0", "r1", "conv-1"))
    w3 = sp2.write_ops("mem0", "r1", "conv-3") if w1 == "accepted" else []
    ops1, ops3 = sp2.write_ops("mem0", "r1", "conv-1"), w3
    rec_s = sp2.record_extra("mem0", "r1", ["conv-1", "conv-3"])
    check("B-S4-SMAP: two conversations with different speakers both write - each unit with its own map, each map's "
          "sha in the run record", w1 == "accepted" and [o["item"]["role"] for o in ops1] == ["user", "assistant"]
          and [o["item"]["role"] for o in ops3] == ["user", "assistant"] and [o["item"]["speaker"] for o in ops3]
          == ["Nate", "Joanna"] and rec_s["speaker_map_sha256"] == {"conv-1": PL.map_sha256(smaps["conv-1"]),
                                                                   "conv-3": PL.map_sha256(smaps["conv-3"])}
          and smaps["conv-1"] != smaps["conv-3"], f"{w1} {rec_s.get('speaker_map_sha256')}")
    one_map = {"conv-1": smaps["conv-1"], "conv-3": smaps["conv-1"]}           # one stand-wide map, as before
    sp3, _st3 = PL.stand_plan("S4", [LOC, LOC2], {"mem0": lns["mem0"]}, standplan=SC.StandPlan, read_req=SC.ReadReq,
                              runs=("r1",), campaign_seed=7, unit_tokens={"conv-1": 1, "conv-3": 1}, medians={},
                              answer=ra, embed_tag="e", dated=True, points=lambda a: ("B",), k_at=PT.K_AT,
                              smaps=one_map)
    stranger = refused(lambda: sp3.write_ops("mem0", "r1", "conv-3"))
    stray = refused(lambda: PL.stand_plan("S4", [LOC], {}, standplan=SC.StandPlan, read_req=SC.ReadReq, runs=("r1",),
                                          campaign_seed=7, unit_tokens={"conv-1": 1}, medians={}, answer=ra,
                                          embed_tag="e", dated=True, points=lambda a: ("B",), k_at=PT.K_AT,
                                          smaps=smaps))
    check("B-S4-SMAP: a speaker outside the unit's own map is refused by name; a map for a unit the stand lacks too",
          "neither of the sample's two" in stranger and "Nate" in stranger and "units the stand does not have" in stray,
          f"{stranger} | {stray}")
    check("SP: the StandPlan is the scheduler's, its answer the Answerer, its tree fields open until the tree check",
          isinstance(sp, SC.StandPlan) and sp.answer is ra and sp.runs == ("r1", "r2") and sp.commit == ""
          and sp.dirty is True and set(sp.launchers) == {"bm25-floor", "chroma-store", "mem0", "a-mem"}, str(sp)[:150])
    unk = refused(lambda: PL.stand_plan("S1", [LME], {"cognee": object()}, standplan=SC.StandPlan,
                                        read_req=SC.ReadReq, runs=("r1",), campaign_seed=7, unit_tokens={"u1": 1},
                                        medians={}, answer=ra, embed_tag="e", dated=True, points=lambda a: ("B",),
                                        k_at=PT.K_AT))
    bad_run = refused(lambda: PL.stand_plan("S1", [LME], {}, standplan=SC.StandPlan, read_req=SC.ReadReq,
                                            runs=("r.1",), campaign_seed=7, unit_tokens={"u1": 1}, medians={},
                                            answer=ra, embed_tag="e", dated=True, points=lambda a: ("B",),
                                            k_at=PT.K_AT))
    check("SP: an arm with no plan (cognee: no adapter, A8) and a dotted run id are refused by name",
          "no plan" in unk and "not [A-Za-z0-9_-]" in bad_run, f"{unk} | {bad_run}")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 run plan: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
