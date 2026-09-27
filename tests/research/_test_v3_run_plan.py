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
* PL-read-plan, PL-code-copy (Q9), PL-no-adapter (A8), PL-map-sha.

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
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nv3 run plan: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
