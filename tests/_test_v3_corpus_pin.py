#!/usr/bin/env python3
"""PREREG-V3 plan step A3.c/A3.d: the v3 pin table, the fetch manifest and the smoke rules - declared before data.

* The table: every pin but the reused v2 ones is unfilled (no revision, sha256 or size) until its window; the v2 pins
  are copied by value and equal research/corpus_pin.CORPORA; the rules hold (no ND licence anywhere, the found licence
  must equal the declared one, a smoke pin serves only smoke stands, no absolute path); a pin is filled once, with a
  64-hex sha256; verify() raises on an unfilled pin, a missing file, a wrong size or a wrong sha256, and passes on the
  pinned file; the FREEZE fragment lists every pin once, in its group.
* The manifest: every pin with a window is listed in exactly that window, and every listed pin exists; the window
  hosts are the declared exact names (a3-hf holds only huggingface.co until discovery fixes its CDN hosts).
* The smoke rules (§9.4 with the auditor's Q-A3-4 interpretation): S5 keeps the sessions that END within 128K tokens
  and drops a straddling one; S6 and S7 pick the closest, ties to the earlier in dataset order; S7's length is the
  compact, non-ASCII-preserving json.dumps of the trajectory; S1 is positions 481-500 of the nested order.

    python tests/_test_v3_corpus_pin.py
"""
from __future__ import annotations

import copy
import importlib.util
import json
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


CP = _load("v3_corpus_pin", ROOT / "research" / "v3" / "corpus_pin_v3.py")
SR = _load("v3_smoke_rules", ROOT / "research" / "v3" / "smoke_rules.py")
V2 = _load("v2_corpus_pin_ro", ROOT / "research" / "corpus_pin.py")
MAN = json.loads((ROOT / "research" / "v3" / "fetch_manifest.json").read_text(encoding="utf-8"))

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def refused(fn, exc, words: str = "") -> bool:
    """``fn`` raises ``exc`` - and, when ``words`` is given, its message names them (the rule that fired)."""
    try:
        fn()
    except exc as e:
        return words in str(e)
    except Exception:  # noqa: BLE001 - the wrong exception is a failure too
        return False
    return False


TMP = Path(tempfile.mkdtemp(prefix="nvt3_pin_"))

print("\n- the table, before any data -")
v3_only = {n: p for n, p in CP.PINS.items() if p["source"] != "local-v2"}
check("no v3 pin is filled before its window: revision, sha256 and size are all None",
      all(p["revision"] is None and p["sha256"] is None and p["bytes"] is None for p in v3_only.values()),
      str([n for n, p in v3_only.items() if p["sha256"] is not None]))
check("the reused v2 pins equal research/corpus_pin.CORPORA, value for value",
      all(CP.V2_PINS[n][k] == V2.CORPORA[n][k] for n in CP.V2_PINS for k in ("sha256", "bytes", "path"))
      and CP.PINS["locomo10"]["sha256"] == V2.CORPORA["locomo10"]["sha256"]
      and CP.PINS["longmemeval_s"]["sha256"] == V2.CORPORA["longmemeval_s"]["sha256"])
check("the declared table obeys its rules", refused(CP.check_rules, CP.PinRefused) is False)
check("the stands the PREREG names are all covered",
      {s for p in CP.PINS.values() for s in p["stands"]} >= {"S1", "S2", "S3", "S4", "S5", "S6", "S6L", "S7", "S9",
                                                             "S5-smoke", "S6-smoke", "S7-smoke"})
check("no declared licence is ND, and ND is not on the list", not any(CP._ND.search(x) for x in CP.LICENCES))


def mutated(name, **change):
    pins = copy.deepcopy(CP.PINS)
    pins[name].update(change)
    return pins


check("an ND licence is refused", refused(lambda: CP.check_rules(mutated("lme_s_cleaned", licence="CC-BY-NC-ND-4.0")),
                                          CP.PinRefused))
check("a licence off the declared list is refused",
      refused(lambda: CP.check_rules(mutated("lme_s_cleaned", licence="proprietary")), CP.PinRefused))
check("a smoke pin on a scored stand is refused",
      refused(lambda: CP.check_rules(mutated("beam_500k", stands=("S5",))), CP.PinRefused))
check("an absolute path is refused", refused(lambda: CP.check_rules(mutated("lme_s_cleaned", path="D:/data/x.json")),
                                             CP.PinRefused))

print("\n- fill once, verify raises -")
pins = copy.deepcopy(CP.PINS)
data = b'{"synthetic": true}\n' * 100
f = TMP / "x.json"
f.write_bytes(data)
import hashlib  # noqa: E402

sha = hashlib.sha256(data).hexdigest()
check("verify raises on an unfilled pin, saying it is not pinned",
      refused(lambda: CP.verify("lme_s_cleaned", f, pins=pins), CP.PinMismatch, "not pinned yet"))
check("a found licence that differs from the declared one is refused",
      refused(lambda: CP.fill("lme_s_cleaned", revision="r", sha256=sha, size=len(data), licence_found="Apache-2.0",
                              pins=pins), CP.PinRefused))
check("an ND licence found at fetch is refused",
      refused(lambda: CP.fill("lme_s_cleaned", revision="r", sha256=sha, size=len(data), licence_found="CC-BY-ND-4.0",
                              pins=pins), CP.PinRefused))
check("... by the ND rule itself, even where no licence was declared (a pin whose licence comes from the fetch)",
      refused(lambda: CP.fill("locomo_j_prompt", revision="r", sha256=sha, size=len(data), licence_found="CC-BY-NC-ND-4.0",
                              pins=copy.deepcopy(CP.PINS)), CP.PinRefused, "ND"))
check("a malformed sha256 is refused",
      refused(lambda: CP.fill("lme_s_cleaned", revision="r", sha256="ABC", size=len(data), licence_found="MIT", pins=pins),
              CP.PinRefused))
CP.fill("lme_s_cleaned", revision="rev1", sha256=sha, size=len(data), licence_found="MIT", pins=pins)
check("a pin is filled once: a second fill is refused",
      refused(lambda: CP.fill("lme_s_cleaned", revision="rev2", sha256=sha, size=len(data), licence_found="MIT",
                              pins=pins), CP.PinRefused))
check("verify passes on the pinned file", CP.verify("lme_s_cleaned", f, pins=pins)["sha256"] == sha)
g = TMP / "y.json"
g.write_bytes(data[:-1] + b"X")
check("verify raises on a wrong sha256 of the right size", refused(lambda: CP.verify("lme_s_cleaned", g, pins=pins),
                                                                   CP.PinMismatch))
g.write_bytes(data + b"\n")
check("verify raises on a wrong size", refused(lambda: CP.verify("lme_s_cleaned", g, pins=pins), CP.PinMismatch))
check("verify raises on a missing file", refused(lambda: CP.verify("lme_s_cleaned", TMP / "none", pins=pins), CP.PinMismatch))
frag = CP.freeze_fragment()
listed = [n for grp in frag.values() for n in grp]
check("the FREEZE fragment lists every pin exactly once, in its group",
      sorted(listed) == sorted(CP.PINS) and set(frag) == {"datasets", "tokenizers", "prompt_files", "arm_sources"}
      and "tiktoken_cl100k_base" in frag["tokenizers"] and "amem_source" in frag["arm_sources"])

print("\n- the manifest agrees with the table -")
win_pins = {w: set(v.get("pins", ())) for w, v in MAN["windows"].items()}
for name, p in CP.PINS.items():
    if p["window"]:
        check(f"{name} is listed in exactly its window {p['window']}",
              name in win_pins.get(p["window"], set()) and sum(name in s for s in win_pins.values()) == 1)
check("every manifest pin exists in the table", set().union(*win_pins.values()) <= set(CP.PINS))
check("the window hosts are the declared exact names",
      {w: sorted(v["hosts"]) for w, v in MAN["windows"].items()} == {
          "a3-discovery": ["api.github.com", "huggingface.co"], "a3-hf": ["huggingface.co"],
          "a3-github": ["api.github.com", "raw.githubusercontent.com"],
          "a3-tiktoken": ["openaipublic.blob.core.windows.net"], "a3-git": ["github.com"]})
check("a3-hf's CDN hosts wait for discovery; discovery follows no redirect",
      MAN["windows"]["a3-hf"]["cdn_hosts_from_discovery"] is True and MAN["windows"]["a3-discovery"]["max_redirects"] == 0)
check("the disk rule is the auditor's: 100 GB floor, 3x the window, stop at a 10 GB file",
      MAN["disk"] == {"volume": "D:", "floor_gb": 100, "multiple_of_window_total": 3, "stop_single_file_gb": 10})

print("\n- the smoke rules (§9.4, Q-A3-4) -")
words = lambda s: len(s.split())  # noqa: E731 - a stand-in counter; the real one is cl100k, passed in the same way
check("S5 keeps the sessions that end within the limit",
      SR.s5_cut(["a " * 40, "b " * 40, "c " * 30], words, limit=100) == 2)
check("S5 drops a session that straddles the limit, and everything after it",
      SR.s5_cut(["a " * 60, "b " * 50, "c " * 5], words, limit=100) == 1)
check("S5 with a first session over the limit is an error, not an empty cut",
      refused(lambda: SR.s5_cut(["a " * 200], words, limit=100), ValueError))
check("S6 picks the row closest to the target", SR.s6_pick(["a " * 10, "a " * 29, "a " * 50], words, target=30) == 1)
check("S6 breaks a tie by dataset order (the earlier row)", SR.s6_pick(["a " * 25, "a " * 35], words, target=30) == 0)
check("S7's length is the compact, non-ASCII-preserving json.dumps of the trajectory",
      SR.trajectory_chars([{"role": "tool", "text": "привет"}]) == len('[{"role":"tool","text":"привет"}]'))
swe = [["x" * 10], ["x" * 20], ["x" * 30]]                     # median length = len(json of ["x"*20])
target = SR.trajectory_chars(["x" * 20])
check("S7 picks the non-SWE trajectory closest to the SWE median",
      SR.s7_pick([["y" * 5], ["y" * 21], ["y" * 40]], swe) == 1 and target == len('["' + "x" * 20 + '"]'))
check("S7 breaks a tie by dataset order", SR.s7_pick([["y" * 19], ["y" * 21]], swe) == 0)
order = list(range(1, 501))
check("S1's smoke units are positions 481-500 of the nested order", SR.s1_tail(order) == list(range(481, 501)))
check("S1 needs the full nested order", refused(lambda: SR.s1_tail(order[:499]), ValueError))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nv3 corpus pins: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
