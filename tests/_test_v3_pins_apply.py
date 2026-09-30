#!/usr/bin/env python3
"""PREREG-V3 plan step A3: research/v3/pins_apply.py on a temporary copy of the pin table, offline.

* a pin_fill.json is taken only when its sha256 is the one given (the auditor's), and its window and run are
  recorded with every value;
* every value passes fill()'s own rules on a copy first; one refusal writes nothing (the table stays byte-identical);
* an alias is only confirmed, never written; a pin already filled is refused;
* the table applies FILLED through fill() at every import, so a hand-edited value that breaks a rule fails the import;
* the real table has its markers exactly once; its FILLED holds values only from the four window runs the auditor
  cleared (a3-hf h2, a3-github g1, a3-tiktoken t1, a3-git r1), each bound to its pin_fill sha256 - and, once filled,
  every v3 pin but the alias.

    python tests/_test_v3_pins_apply.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic like every suite


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


A = _load("v3_pins_apply", ROOT / "research" / "v3" / "pins_apply.py")
CP = _load("v3_corpus_pin_pa", ROOT / "research" / "v3" / "corpus_pin_v3.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def refused(fn, words: str = "") -> bool:
    try:
        fn()
    except A.ApplyRefused as e:
        return words in str(e)
    except Exception:  # noqa: BLE001
        return False
    return False


TMP = Path(tempfile.mkdtemp(prefix="nvt3_pinsapply_"))


def table_copy(tag: str) -> Path:
    d = TMP / tag / "research" / "v3"
    d.mkdir(parents=True)
    text = (ROOT / "research" / "v3" / "corpus_pin_v3.py").read_bytes().decode("utf-8")
    text = re.sub(re.escape(A.BEGIN) + r"\n.*?\n" + re.escape(A.END), A.BEGIN + "\nFILLED: dict[str, dict] = {\n}\n" + A.END,
                  text, count=1, flags=re.S)                  # an empty FILLED, whatever the real table holds
    (d / "corpus_pin_v3.py").write_bytes(text.encode("utf-8"))
    return d / "corpus_pin_v3.py"


def fill_file(tag: str, window: str, run: str, pins: dict, *, problems=(), place_problems=(), rec_run=None) -> tuple[Path, str]:
    """A pin_fill.json at <tmp>/<tag>/_fetch/<window>/<run>/, beside the window run's record and place record."""
    d = TMP / tag / "_fetch" / (window or "w") / (run or "r")
    d.mkdir(parents=True)
    (d / "record.json").write_text(json.dumps({"window": window, "run": rec_run or run, "problems": list(problems)}), encoding="utf-8")
    (d / "place_record.json").write_text(json.dumps({"window": window, "problems": list(place_problems)}), encoding="utf-8")
    f = d / "pin_fill.json"
    f.write_bytes((json.dumps({"window": window, "run": run, "pins": pins}, indent=1) + "\n").encode("utf-8"))
    return f, hashlib.sha256(f.read_bytes()).hexdigest()


def whole_window(window: str, *, base: dict, override: dict | None = None) -> dict:
    """Values for every pin the table gives ``window`` (so the pin_fill covers it), ``override`` on top."""
    out = {}
    for n, p in P_.items():
        if p["window"] != window:
            continue
        if p.get("alias_of"):
            out[n] = {"alias_confirmed": p["alias_of"], "sha256": p["sha256"], "bytes": p["bytes"]}
        else:
            lic = {"CC-BY-SA-4.0 (data); MIT (code)": "MIT" if p["role"] not in ("evaluation", "bracket", "smoke") else "CC-BY-SA-4.0"
                   }.get(p["licence"], p["licence"])
            out[n] = {"revision": p["revision"] or "r", "sha256": hashlib.sha256(n.encode()).hexdigest(), "bytes": 1,
                      "licence_found": lic, "licence_source": "test"}
    out.update(override or {})
    return out


P_ = CP.PINS_DECLARED
GH_VALUES = whole_window("a3-github", base={}, override={"mab_fc_sh_6k": {
    "revision": P_["mab_fc_sh_6k"]["revision"], "sha256": "a" * 64, "bytes": 303, "licence_found": "MIT",
    "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench"}})
GH = fill_file("gh", "a3-github", "g1", GH_VALUES)
TK = fill_file("tk", "a3-tiktoken", "t1", {"tiktoken_cl100k_base": {"revision": "b" * 64, "sha256": "b" * 64, "bytes": 10,
                                                                    "licence_found": "MIT", "licence_source": "METADATA x"}})
AL = fill_file("al", "a3-hf", "h2", whole_window("a3-hf", base={}))
TK_VALUE = {"revision": "b" * 64, "sha256": "b" * 64, "bytes": 10, "licence_found": "MIT", "licence_source": "METADATA x"}

print("\n- the real table -")
real = (ROOT / "research" / "v3" / "corpus_pin_v3.py").read_bytes().decode("utf-8")
check("the real table carries the FILLED markers exactly once", real.count(A.BEGIN) == 1 and real.count(A.END) == 1)
CLEARED = {'a3-hf h2': 'c23ea9cd3549', 'a3-github g1': '0949dd56da65', 'a3-tiktoken t1': 'ab54c52a1117', 'a3-git r1': 'ba0f9248f430'}
check("FILLED holds values only from the four cleared window runs, each by its pin_fill sha256",
      all(any(v["from"] == f"{w} pin_fill {s}" for w, s in CLEARED.items()) for v in CP.FILLED.values()),
      str(sorted({v["from"] for v in CP.FILLED.values()})))
check("once filled, FILLED covers every v3 pin but the alias (44), and neither the alias nor a v2 pin",
      (not CP.FILLED or (len(CP.FILLED) == 44 and all(p["sha256"] is not None for p in CP.PINS.values())))
      and not any(n in CP.FILLED for n, p in CP.PINS.items() if p.get("alias_of") or p["source"] == "local-v2"),
      str(len(CP.FILLED)))

print("\n- FILLED re-derived from the cleared pin_fill files, committed as evidence (FD4) -")
#: The four files the auditor cleared, bytes as they came out of their window runs (runs\v3\_fetch\<window>\<run>\), by
#: their full sha256 as the auditor named them. A value edited by hand in FILLED that keeps its "from" goes red here,
#: in CI, with no polygon data.
FIX = ROOT / "tests" / "fixtures" / "v3_pin_fill"
CLEARED_FILES = {("a3-hf", "h2"): "c23ea9cd354963e63305ccc280a553ac1870a4fdf9942575540dec9ba587a3eb",
                 ("a3-github", "g1"): "0949dd56da65c36adae21dfa1613b6d8af3d559bade22c9348ac7460f536c4b0",
                 ("a3-tiktoken", "t1"): "ab54c52a11176332a72d10851d83a5cad9133cc0b646f395ee8520c7b5459a97",
                 ("a3-git", "r1"): "ba0f9248f430b15878c975ed3295059e11e7747a1f43902a6da2e8dafc4e8ab6"}
evidence = {}
for (w, r), sha in CLEARED_FILES.items():
    raw = (FIX / w / r / "pin_fill.json").read_bytes()
    d = json.loads(raw)
    check(f"FD4: the fixture {w}/{r}/pin_fill.json is the cleared file, by its full sha256, of its own window run",
          hashlib.sha256(raw).hexdigest() == sha and (d["window"], d["run"]) == (w, r), hashlib.sha256(raw).hexdigest()[:12])
    evidence[f"{w} {r} pin_fill {sha[:12]}"] = (d, sha)
#: A7's cleared files (the auditor, 2026-09-30 03:0x: window a7-github g1 accepted; 05:3x: window a7-hf h1 accepted -
#: m6 --window, m6 --pins-rev, m5 --launch-dir --set-aside, secret_scan 0 on each)
A7_CLEARED_FILES = {("a7-github", "g1"): "acae9e52bc935a5752f384fbcc0cd26521e20c97878056d3318963f782296472",
                    ("a7-hf", "h1"): "449a89fca362ab4aa85d6408236cb8ba37ab56e04c2a6ddfeea75f59e8094afe"}
check("FD4: the fixtures are exactly the four cleared A3 files and the two cleared A7 ones",
      sorted(p_.relative_to(FIX).as_posix() for p_ in FIX.rglob("*") if p_.is_file())
      == sorted(f"{w}/{r}/pin_fill.json" for w, r in {**CLEARED_FILES, **A7_CLEARED_FILES}), str(sorted(FIX.rglob("*"))))
KEYS_ = ("revision", "sha256", "bytes", "licence_found", "licence_source")
drift = [n for n, v in CP.FILLED.items()
         if v["from"] not in evidence or {k: v[k] for k in KEYS_}
         != {k: (int(x) if k == "bytes" else x) for k, x in ((k, evidence[v["from"]][0]["pins"].get(n, {}).get(k)) for k in KEYS_)}]
check("FD4: every FILLED value equals the value its own source file gives - revision, sha256, bytes, licence", drift == [], str(drift))
import types  # noqa: E402
_declared = types.SimpleNamespace(PINS=CP.PINS_DECLARED, fill=CP.fill, PinRefused=CP.PinRefused)
try:
    rederived, realiases = A.plan_values([evidence[k] for k in sorted(evidence)], _declared)
except A.ApplyRefused as e:
    rederived, realiases = {"refused": str(e)}, []
check("FD4: FILLED is exactly what pins_apply derives from the four files on the declared table - nothing more, nothing less",
      CP.FILLED == rederived, str(sorted(set(CP.FILLED) ^ set(rederived)) or [n for n in CP.FILLED if CP.FILLED[n] != rederived.get(n)]))
check("FD4: ... and the one alias it confirms is the oracle, from a3-hf h2",
      realiases == ["lme_oracle_cleaned = v2:longmemeval_oracle (a3-hf h2 pin_fill c23ea9cd3549)"], str(realiases))

print("\n- FILLED_A7 re-derived from the cleared a7-github pin_fill, committed as evidence (FD4 for A7) -")
ev7, ev7err = {}, None
try:
    for (w, r), sha in A7_CLEARED_FILES.items():
        raw7 = (FIX / w / r / "pin_fill.json").read_bytes()
        ev7[f"{w} {r} pin_fill {sha[:12]}"] = (json.loads(raw7), sha, hashlib.sha256(raw7).hexdigest(), (w, r))
except Exception as e:  # noqa: BLE001 - a missing fixture FAILs the rows below by name
    ev7err = f"{type(e).__name__}: {e}"
FILLED_A7 = getattr(CP, "FILLED_A7", {})
check("FD4-A7-1: the fixtures a7-github/g1 and a7-hf/h1 pin_fill.json are the cleared files, by their full sha256, "
      "each of its own window run", ev7err is None and len(ev7) == 2
      and all(got == sha and (d["window"], d["run"]) == wr for d, sha, got, wr in ev7.values()),
      str(ev7err or [got[:12] for _d, _s, got, _wr in ev7.values()]))
gh7 = sorted(n for n, p in getattr(CP, "PINS_A7_DECLARED", {}).items() if p["window"] == "a7-github")
hf7 = sorted(n for n, p in getattr(CP, "PINS_A7_DECLARED", {}).items() if p["window"] == "a7-hf")
check("FD4-A7-2: FILLED_A7 holds every A7 pin, each from its own window's cleared run by its pin_fill sha256 - the 52 of "
      "a7-github from g1 (acae9e52bc93), the 10 MiniLM files of a7-hf from h1 (449a89fca362)",
      sorted(FILLED_A7) == sorted(gh7 + hf7) and len(gh7) == 52 and len(hf7) == 10
      and all(FILLED_A7[n]["from"] == "a7-github g1 pin_fill acae9e52bc93" for n in gh7)
      and all(FILLED_A7[n]["from"] == "a7-hf h1 pin_fill 449a89fca362" for n in hf7)
      and all(CP.PINS_A7[n]["sha256"] is not None and CP.PINS_A7[n]["bytes"] is not None for n in gh7 + hf7),
      str((len(FILLED_A7), sorted({v.get("from") for v in FILLED_A7.values()}))))
try:
    _declared7 = types.SimpleNamespace(PINS_A7=CP.PINS_A7_DECLARED, fill=CP.fill, PinRefused=CP.PinRefused)
    rederived7, realiases7 = A.plan_values([(d, sha) for d, sha, _g, _wr in ev7.values()], _declared7, table="PINS_A7")
except Exception as e:  # noqa: BLE001
    rederived7, realiases7 = {"refused": f"{type(e).__name__}: {e}"}, ["?"]
check("FD4-A7-3: FILLED_A7 is exactly what pins_apply derives from those two files on the declared A7 table - no alias",
      bool(FILLED_A7) and FILLED_A7 == rederived7 and realiases7 == [],
      str(sorted(set(FILLED_A7) ^ set(rederived7))[:3] or [n for n in FILLED_A7 if FILLED_A7[n] != rederived7.get(n)][:3]))

print("\n- a clean apply -")
t1 = table_copy("t1")
try:
    out = A.apply(t1, [GH, TK, AL])
except Exception as e:  # noqa: BLE001 - a refusal here fails the checks below by name
    out = {"written": [], "aliases_confirmed": [], "filled_total": 0, "error": f"{type(e).__name__}: {e}"}
n_gh = sum(1 for p in P_.values() if p["window"] == "a3-github")
n_hf = sum(1 for p in P_.values() if p["window"] == "a3-hf")
check("the values are written, the alias only confirmed",
      "mab_fc_sh_6k" in out["written"] and "tiktoken_cl100k_base" in out["written"] and "lme_oracle_cleaned" not in out["written"]
      and len(out["aliases_confirmed"]) == 1 and out["filled_total"] == n_gh + 1 + n_hf - 1, str(out)[:300])
M1 = _load("v3_cp_t1", t1)
check("the table applies them through fill(): sha256, size, licence found, and where they came from",
      (M1.PINS["mab_fc_sh_6k"]["sha256"], M1.PINS["mab_fc_sh_6k"]["bytes"], M1.PINS["mab_fc_sh_6k"]["licence_found"])
      == ("a" * 64, 303, "MIT") and M1.PINS["mab_fc_sh_6k"]["filled_from"] == f"a3-github g1 pin_fill {GH[1][:12]}")
check("a URL pin's revision is its sha256", M1.PINS["tiktoken_cl100k_base"]["revision"] == "b" * 64)
check("the alias keeps its v2 value and is not in FILLED", "lme_oracle_cleaned" not in M1.FILLED
      and M1.PINS["lme_oracle_cleaned"]["sha256"] == P_["lme_oracle_cleaned"]["sha256"])
check("the written block is sorted and parses back to the same values",
      list(M1.FILLED) == sorted(M1.FILLED) and M1.FILLED["mab_fc_sh_6k"]["bytes"] == 303)

print("\n- refusals write nothing -")
for i_case, (label, fills, words) in enumerate((
        ("a pin_fill whose sha256 is not the named one", [(GH[0], "0" * 64)], "is not the named"),
        ("(1) a pin_fill from a window run with problems", [fill_file("dirty", "a3-github", "g9", GH_VALUES,
                                                                      problems=["request x: status 404"])], "has problems"),
        ("(1) a pin_fill whose placement had problems", [fill_file("dirtyp", "a3-github", "g8", GH_VALUES,
                                                                   place_problems=["P7 x"])], "has problems"),
        ("(1) a pin_fill beside another run's record", [fill_file("other", "a3-github", "g7", GH_VALUES, rec_run="g6")],
         "another window run"),
        ("(2) a partial pin_fill (one pin of its window)", [fill_file("partial", "a3-github", "g5",
                                                                      {"mab_fc_sh_6k": GH_VALUES["mab_fc_sh_6k"]})], "partial set"),
        ("(2) a pin_fill naming a pin of another window", [fill_file("cross", "a3-github", "g4",
                                                                     {**GH_VALUES, "tiktoken_cl100k_base": TK_VALUE})], "partial set"),
        ("a found licence the table refuses (P6)", [fill_file("bad", "a3-github", "g2", {**GH_VALUES, "mab_fc_sh_6k": {
            "revision": P_["mab_fc_sh_6k"]["revision"], "sha256": "c" * 64, "bytes": 303, "licence_found": "Apache-2.0"}})],
         "PinRefused"),
        ("a malformed sha256", [fill_file("bad2", "a3-github", "g3", {**GH_VALUES, "mab_fc_sh_6k": {
            "revision": "r", "sha256": "XYZ", "bytes": 1, "licence_found": "MIT"}})], "PinRefused"),
        ("an alias confirmation that is not its pinned value", [fill_file("bad3", "a3-hf", "h3", whole_window("a3-hf", base={}, override={
            "lme_oracle_cleaned": {"alias_confirmed": "v2:longmemeval_oracle", "sha256": "d" * 64, "bytes": 1}}))], "alias"),
        ("a name the table does not hold (beyond its window)", [fill_file("bad4", "a3-github", "g10",
                                                                          {**GH_VALUES, "nope": {"revision": "r"}})], "partial set"),
        ("a record that is not a pin_fill", [fill_file("bad5", "", "", {})], "not a pin_fill record"))):
    t = table_copy(f"r{i_case}")
    before = t.read_bytes()
    check(f"{label}: refused, the table byte-identical", refused(lambda t=t, f=fills: A.apply(t, f), words)
          and t.read_bytes() == before)
import shutil as _sh  # noqa: E402

moved = TMP / "moved" / "elsewhere"
_sh.copytree(GH[0].parent, moved)
t_mv = table_copy("moved_t")
check("(1) a pin_fill that is not at <runs>/_fetch/<window>/<run>/ is refused",
      refused(lambda: A.apply(t_mv, [(moved / "pin_fill.json", GH[1])]), "not at"))
before1 = t1.read_bytes()
check("a pin already filled is refused - a pin is filled once", refused(lambda: A.apply(t1, [GH]), "filled once")
      and t1.read_bytes() == before1)
t_twice = table_copy("twice")
t_twice.write_bytes(t_twice.read_bytes() + ("\n" + A.BEGIN + "\nFILLED: dict[str, dict] = {\n}\n" + A.END + "\n").encode())
before_twice = t_twice.read_bytes()
check("PA8: a table with the FILLED markers twice is refused, its bytes unchanged (a stale block would shadow the written one)",
      refused(lambda: A.apply(t_twice, [GH]), "markers") and t_twice.read_bytes() == before_twice)
gh_noside = fill_file("noside", "a3-github", "g11", GH_VALUES)
(gh_noside[0].parent / "place_record.json").unlink()
t_ns = table_copy("noside_t")
before_ns = t_ns.read_bytes()
check("PA9: a pin_fill whose place_record.json is missing is refused, nothing written",
      refused(lambda: A.apply(t_ns, [gh_noside]), "has no place_record.json") and t_ns.read_bytes() == before_ns)
t_nomark = table_copy("nomark")
t_nomark.write_bytes(t_nomark.read_bytes().replace(A.END.encode(), b"# gone"))
check("a table whose markers are not there exactly once is refused", refused(lambda: A.apply(t_nomark, [GH]), "markers"))

print("\n- A7 (the auditor, 2026-09-30 00:43): a7-* windows fill PINS_A7 through their own block, # >>> A7 FILLED -")
P7 = getattr(CP, "PINS_A7_DECLARED", {})
A7B, A7E = "# >>> A7 FILLED", "# <<< A7 FILLED"


def whole_a7(window: str, override: dict | None = None) -> dict:
    """Values for every pin PINS_A7 gives ``window`` (so the pin_fill covers it), ``override`` on top."""
    out = {n: {"revision": p["revision"], "sha256": hashlib.sha256(("a7" + n).encode()).hexdigest(), "bytes": 7,
               "licence_found": {"CC-BY-SA-4.0 (data); MIT (code)": "MIT"}.get(p["licence"], p["licence"]),
               "licence_source": "test"} for n, p in P7.items() if p["window"] == window}
    out.update(override or {})
    return out


def table_copy7(tag: str) -> Path:
    """A table copy with BOTH blocks empty, whatever the real table holds."""
    t = table_copy(tag)
    text = t.read_bytes().decode("utf-8")
    text = re.sub(re.escape(A7B) + r"\n.*?\n" + re.escape(A7E), A7B + "\nFILLED_A7: dict[str, dict] = {\n}\n" + A7E,
                  text, count=1, flags=re.S)
    t.write_bytes(text.encode("utf-8"))
    return t


def a3_block(text: str) -> str:
    m = re.search(re.escape(A.BEGIN) + r"\n.*?\n" + re.escape(A.END), text, flags=re.S)
    return m.group(0) if m else ""


A7V = whole_a7("a7-github")
G7 = fill_file("a7gh", "a7-github", "g1", A7V)
check("PA-A7-1: the real table carries the A7 FILLED markers exactly once, and pins_apply names them",
      real.count(A7B) == 1 and real.count(A7E) == 1 and getattr(A, "A7_BEGIN", None) == A7B and getattr(A, "A7_END", None) == A7E)
t7 = table_copy7("a7t1")
before7 = t7.read_bytes().decode("utf-8")
try:
    out7, err7 = A.apply(t7, [G7]), None
except Exception as e:  # noqa: BLE001 - a refusal here fails the rows below by name
    out7, err7 = None, f"{type(e).__name__}: {e}"
after7 = t7.read_bytes().decode("utf-8")
try:
    M7 = _load("v3_cp_a7t1", t7)
except Exception as e:  # noqa: BLE001
    M7, err7 = None, f"{err7} / load: {type(e).__name__}: {e}"
try:
    ok7_2 = (err7 is None and M7 is not None and sorted(M7.FILLED_A7) == sorted(A7V) == list(M7.FILLED_A7)
             and len(M7.FILLED_A7) == 52 and a3_block(after7) == a3_block(before7) and M7.FILLED == {})
    ok7_3 = M7 is not None and all(
        (M7.PINS_A7[n]["sha256"], M7.PINS_A7[n]["bytes"], M7.PINS_A7[n]["filled_from"])
        == (v["sha256"], 7, f"a7-github g1 pin_fill {G7[1][:12]}") for n, v in A7V.items()) and all(
        p["sha256"] == CP.PINS_DECLARED[n]["sha256"] for n, p in M7.PINS.items())
except Exception as e:  # noqa: BLE001
    ok7_2 = ok7_3 = False
    err7 = f"{err7} / rows: {type(e).__name__}: {e}"
check("PA-A7-2: an a7-github pin_fill is written into FILLED_A7 - all 52 pins, sorted - and the A3 block stays "
      "byte-identical", ok7_2, str(err7))
check("PA-A7-3: the table applies them through fill() into PINS_A7: sha256, size, where they came from; PINS untouched",
      ok7_3, str(err7))
t7b = table_copy7("a7t2")
try:
    out7b = A.apply(t7b, [G7, GH])
    M7b = _load("v3_cp_a7t2", t7b)
    ok7_4, err7b = (set(M7b.FILLED) == set(GH_VALUES) - {n for n, p in P_.items() if p.get("alias_of")}
                    and set(M7b.FILLED_A7) == set(A7V)
                    and out7b["written"] == sorted(set(M7b.FILLED) | set(M7b.FILLED_A7))), None
except Exception as e:  # noqa: BLE001
    ok7_4, err7b = False, f"{type(e).__name__}: {e}"
check("PA-A7-4: one call with an A3 and an A7 pin_fill writes each into its own block", ok7_4, str(err7b))
cases7 = (
    ("PA-A7-5: a partial a7-github pin_fill (3 of its 52)", [fill_file("a7part", "a7-github", "g2",
                                                                       {k: A7V[k] for k in list(A7V)[:3]})], "(3 of 52)"),
    ("PA-A7-7: a cognee licence the table refuses (MIT found, Apache-2.0 declared - P6)",
     [fill_file("a7lic", "a7-github", "g4", whole_a7("a7-github", {"cognee_run_beam_eval": {
         **A7V.get("cognee_run_beam_eval", {}), "licence_found": "MIT"}}))], "PinRefused"))
for label, fills, words in cases7:
    t = table_copy7(label.split(":")[0])
    b = t.read_bytes()
    check(f"{label}: refused, the table byte-identical", refused(lambda t=t, f=fills: A.apply(t, f), words) and t.read_bytes() == b)
b7 = t7.read_bytes()
check("PA-A7-9: an A7 pin already filled is refused - a pin is filled once", err7 is None
      and refused(lambda: A.apply(t7, [G7]), "filled once") and t7.read_bytes() == b7)
t7x = table_copy7("a7twice")
t7x.write_bytes(t7x.read_bytes() + ("\n" + A7B + "\nFILLED_A7: dict[str, dict] = {\n}\n" + A7E + "\n").encode())
b7x = t7x.read_bytes()
check("PA-A7-10: a table with the A7 markers twice is refused for an A7 pin_fill, its bytes unchanged",
      refused(lambda: A.apply(t7x, [G7]), "A7 FILLED markers") and t7x.read_bytes() == b7x)
t11 = table_copy7("a7seq")
try:
    A.apply(t11, [TK])
    A.apply(t11, [GH])
    gh11 = t11.read_bytes().decode("utf-8")
    M11 = _load("v3_cp_a7seq", t11)
    A.apply(t11, [G7])
    end11 = t11.read_bytes().decode("utf-8")
    M11b = _load("v3_cp_a7seq2", t11)
    ok11 = set(M11.FILLED) == {"tiktoken_cl100k_base"} | set(GH_VALUES) and M11.FILLED_A7 == {}
    ok12 = (a3_block(end11) == a3_block(gh11) != "" and M11b.FILLED == M11.FILLED and set(M11b.FILLED_A7) == set(A7V))
    err11 = None
except Exception as e:  # noqa: BLE001
    ok11 = ok12 = False
    err11 = f"{type(e).__name__}: {e}"
check("PA-A7-11: applies one after another keep what the block already holds (tiktoken, then a3-github)", ok11, str(err11))
check("PA-A7-12: an A7 apply after them leaves FILLED as it was and fills FILLED_A7", ok12, str(err11))

print("\n- a hand edit cannot pass -")
t_hand = table_copy("hand")
txt = t_hand.read_bytes().decode("utf-8").replace(
    'FILLED: dict[str, dict] = {\n}',
    'FILLED: dict[str, dict] = {\n    "mab_fc_sh_6k": {"revision": "r", "sha256": "' + "e" * 64 + '", "bytes": 1, "licence_found": '
    '"Apache-2.0", "licence_source": null, "from": "hand"},\n}'.replace("null", "None"))
t_hand.write_bytes(txt.encode("utf-8"))
try:
    _load("v3_cp_hand", t_hand)
    ok_hand = False
except Exception as e:  # noqa: BLE001 - the copy's own PinRefused class differs from ours by load
    ok_hand = type(e).__name__ == "PinRefused" and "is not the declared" in str(e)
check("a hand-edited FILLED value that breaks P6 fails the table's import", ok_hand)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 pins apply: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
