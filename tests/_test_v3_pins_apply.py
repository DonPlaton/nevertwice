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
