#!/usr/bin/env python3
"""PREREG-V3 plan step A3.c/A3.d: the v3 pin table, the fetch manifest and the smoke rules - declared before data.

* The table: every pin but the reused v2 ones has no sha256 or size until its window, and a revision only when the
  a3-discovery record d1 declared it (a 40-hex sha, its source named); the v2 pins
  are copied by value and equal research/corpus_pin.CORPORA; the rules hold (no ND licence anywhere, the found licence
  must equal the declared one, a smoke pin serves only smoke stands, no absolute path); a pin is filled once, with a
  64-hex sha256; verify() raises on an unfilled pin, a missing file, a wrong size or a wrong sha256, and passes on the
  pinned file; the FREEZE fragment lists every pin once, in its group.
* The manifest: every pin with a window is listed in exactly that window, and every listed pin exists; the window
  hosts are the declared exact names - a3-hf's CDN hosts are the ones the discovery record d1 names (P5).
* The auditor's P3/P7/P8/P9: BEAM's scored pin is the HF split 100K and its smoke pin 500K; LoCoMo's LICENSE.txt is a
  licence-evidence pin; the prompt and scoring files are named at their discovery commits; both AMA pins name the one
  file; the bge-m3 weights are never a pin; the pins still waiting (Mem0 J, AMA judge, BEAM scoring) name no path.
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
v3_only = {n: p for n, p in CP.PINS_DECLARED.items() if p["source"] != "local-v2"}
import re  # noqa: E402

check("no v3 pin carries a sha256 or a size before its window - but the oracle, which is the v2 pin by value (Q-A3F-8)",
      all(p["sha256"] is None and p["bytes"] is None for n, p in v3_only.items() if n != "lme_oracle_cleaned"),
      str([n for n, p in v3_only.items() if p["sha256"] is not None]))
check("Q-A3F-8: lme_oracle_cleaned is ONE pin - the v2 oracle's sha256 and size, marked as its alias, never filled again",
      (CP.PINS["lme_oracle_cleaned"]["sha256"], CP.PINS["lme_oracle_cleaned"]["bytes"])
      == (V2.CORPORA["longmemeval_oracle"]["sha256"], V2.CORPORA["longmemeval_oracle"]["bytes"])
      and CP.PINS["lme_oracle_cleaned"].get("alias_of") == "v2:longmemeval_oracle"
      and [n for n, p in CP.PINS.items() if p.get("alias_of")] == ["lme_oracle_cleaned"]
      and refused(lambda: CP.fill("lme_oracle_cleaned", revision="r", sha256="0" * 64, size=1, licence_found="MIT",
                                  pins=copy.deepcopy(CP.PINS_DECLARED)), CP.PinRefused, "already pinned"))
check("a v3 pin's revision is None or a 40-hex sha declared by a discovery record (d1 or d2), which it names",
      all(p["revision"] is None or (re.fullmatch(r"[0-9a-f]{40}", p["revision"]) and p["revision_from"] in (
          f"a3-discovery d1 {CP.DISCOVERY_D1[:12]}", f"a3-discovery d2 {CP.DISCOVERY_D2[:12]}"))
          for p in v3_only.values()), str([(n, p["revision"]) for n, p in v3_only.items() if p["revision"]][:3]))
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
    pins = copy.deepcopy(CP.PINS_DECLARED)
    pins[name].update(change)
    return pins


check("an ND licence is refused - by the ND rule itself, declared or found",
      refused(lambda: CP.check_rules(mutated("lme_s_cleaned", licence="CC-BY-NC-ND-4.0")), CP.PinRefused, "an ND licence")
      and refused(lambda: CP.check_rules(mutated("lme_s_cleaned", licence_found="CC-BY-ND-4.0")), CP.PinRefused, "an ND licence"))
check("a licence off the declared list is refused",
      refused(lambda: CP.check_rules(mutated("lme_s_cleaned", licence="proprietary")), CP.PinRefused))
check("a smoke pin on a scored stand is refused",
      refused(lambda: CP.check_rules(mutated("beam_500k", stands=("S5",))), CP.PinRefused))
check("an absolute path is refused", refused(lambda: CP.check_rules(mutated("lme_s_cleaned", path="D:/data/x.json")),
                                             CP.PinRefused))
for label, ap in (("a POSIX absolute path", "/data/x.json"), ("a UNC path", "\\\\server\\share\\x.json"),
                  ("a drive-relative path", "D:x.json"), ("a backslash-rooted path", "\\data\\x.json")):
    check(f"C1: {label} is refused on every OS", refused(lambda a=ap: CP.check_rules(mutated("lme_s_cleaned", path=a)),
                                                           CP.PinRefused), ap)
check("C1: ... while a relative repository path is not", not refused(
    lambda: CP.check_rules(mutated("lme_s_cleaned", path="data/lme/x.json")), CP.PinRefused))

print("\n- P6: the licence found is judged against the declared one, declared rules -")
for declared, found, role, want in (("MIT", "mit", "evaluation", True), ("MIT", "Apache-2.0", "evaluation", False),
                                    ("CC-BY-SA-4.0 (data); MIT (code)", "cc-by-sa-4.0", "evaluation", True),
                                    ("CC-BY-SA-4.0 (data); MIT (code)", "cc-by-sa-4.0", "scoring", False),
                                    ("CC-BY-SA-4.0 (data); MIT (code)", "MIT", "scoring", True),
                                    ("CC-BY-SA-4.0 (data); MIT (code)", "MIT", "smoke", False),
                                    ("CC-BY-NC-4.0", "cc-by-nc-4.0", "evaluation", True),
                                    ("CC-BY-NC-4.0", "CC-BY-NC-ND-4.0", "evaluation", False),
                                    ("CC-BY-ND-4.0", "cc-by-nd-4.0", "evaluation", False),
                                    ("MIT", "NOASSERTION", "prompt", False)):
    check(f"licence {found!r} against {declared!r} for a {role} pin: {'match' if want else 'no match'}",
          CP.licence_matches(declared, found, role) is want)

print("\n- fill once, verify raises -")
pins = copy.deepcopy(CP.PINS_DECLARED)
data = b'{"synthetic": true}\n' * 100
f = TMP / "x.json"
f.write_bytes(data)
import hashlib  # noqa: E402

sha = hashlib.sha256(data).hexdigest()
check("verify raises on an unfilled pin, saying it is not pinned",
      refused(lambda: CP.verify("lme_s_cleaned", f, pins=pins), CP.PinMismatch, "not pinned yet"))
check("a card's lower-case SPDX id fills a pin declared in upper case",
      CP.fill("lme_m_cleaned", revision="r", sha256=sha, size=len(data), licence_found="mit",
              pins=copy.deepcopy(CP.PINS_DECLARED))["licence_found"] == "mit")
check("a found licence that differs from the declared one is refused",
      refused(lambda: CP.fill("lme_s_cleaned", revision="r", sha256=sha, size=len(data), licence_found="Apache-2.0",
                              pins=pins), CP.PinRefused))
check("an ND licence found at fetch is refused",
      refused(lambda: CP.fill("lme_s_cleaned", revision="r", sha256=sha, size=len(data), licence_found="CC-BY-ND-4.0",
                              pins=pins), CP.PinRefused))
check("... by the ND rule itself, even where no licence was declared (a pin whose licence comes from the fetch)",
      refused(lambda: CP.fill("locomo_j_prompt", revision="r", sha256=sha, size=len(data), licence_found="CC-BY-NC-ND-4.0",
                              pins=mutated("locomo_j_prompt", licence=None)), CP.PinRefused, "the licence found at fetch is ND"))
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
      sorted(listed) == sorted(CP.PINS)
      and set(frag) == {"datasets", "tokenizers", "prompt_files", "arm_sources", "licence_evidence"}
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
          "a3-discovery": ["api.github.com", "huggingface.co"],
          "a3-hf": ["cdn-lfs-us-1.hf.co", "huggingface.co", "us.aws.cdn.hf.co"],
          "a3-github": ["raw.githubusercontent.com"],
          "a3-tiktoken": ["openaipublic.blob.core.windows.net"], "a3-git": ["github.com"],
          "a3-pyarrow": ["files.pythonhosted.org", "pypi.org"], "a7-npm": ["registry.npmjs.org"],
          "a7-npm-d": ["registry.npmjs.org"], "a7-discovery": ["api.github.com", "huggingface.co"],
          "py-base-312": ["api.nuget.org"], "a8-pypi-mem0_v3": ["files.pythonhosted.org", "pypi.org"],
          "a8-docker-letta": ["auth.docker.io", "production.cloudflare.docker.com", "registry-1.docker.io"],
          "a8-docker-falkordb": ["auth.docker.io", "production.cloudflare.docker.com", "registry-1.docker.io"],
          "a8-spacy-model": ["api.github.com", "github.com", "objects.githubusercontent.com", "raw.githubusercontent.com",
                             "release-assets.githubusercontent.com"]})
check("a3-hf's hosts come from the discovery record d1; discovery follows no redirect",
      MAN["windows"]["a3-hf"]["hosts_from_record"] == CP.DISCOVERY_D1 and MAN["windows"]["a3-discovery"]["max_redirects"] == 0)

print("\n- the auditor's P3, P7, P8, P9 -")
P_ = CP.PINS
check("P3: BEAM's scored pin is the HF split 100K, its smoke pin 500K",
      P_["beam_128k"]["path"] == "data/100K-00000-of-00001.parquet" and P_["beam_500k"]["path"] == "data/500K-00000-of-00001.parquet"
      and P_["beam_128k"]["revision"] == P_["beam_500k"]["revision"] == CP.REV["beam"])
check("P7: LoCoMo's LICENSE.txt is a licence-evidence pin at the pinned commit",
      P_["locomo_licence_txt"]["role"] == "licence-evidence" and P_["locomo_licence_txt"]["path"] == "LICENSE.txt"
      and P_["locomo_licence_txt"]["revision"] == CP.REV["gh_locomo"] == P_["locomo_answer_prompt"]["revision"])
check("P8: the prompt and scoring files are named at their discovery commits",
      (P_["lme_evaluate_qa"]["repo"], P_["lme_evaluate_qa"]["path"]) == ("xiaowu0162/LongMemEval", "src/evaluation/evaluate_qa.py")
      and P_["locomo_evaluation"]["path"] == "task_eval/evaluation.py" and P_["mab_templates"]["path"] == "utils/templates.py"
      and sum(1 for n in P_ if n.startswith("mab_fc_")) == 8
      and P_["mab_fc_mh_262k"]["path"] == "configs/data_conf/Conflict_Resolution/Factconsolidation_mh_262k.yaml")
check("P9: both AMA pins name the one file at one revision",
      P_["ama_swe"]["path"] == P_["ama_non_swe"]["path"] == "test/open_end_qa_set.jsonl"
      and P_["ama_swe"]["revision"] == P_["ama_non_swe"]["revision"])
check("the bge-m3 weights are never a pin; its four tokenizer files are",
      not any(str(p["path"]).endswith((".bin", ".onnx", ".onnx_data", ".pt")) for p in P_.values())
      and sorted(p["path"] for n, p in P_.items() if n.startswith("bge_m3_"))
      == ["sentencepiece.bpe.model", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json"])
check("the pins cite the two discovery records the auditor received, by value",
      (CP.DISCOVERY_D1, CP.DISCOVERY_D2) == ("db131b4983a91629ef3f8906e5b1f6659a7c3f17b4713a2b84e4b9afdff18210",
                                             "f793c59a6ec980a94255a8abe0530a3c72d3c782d213df30375adabbd8a360d4"))
check("no pin is left waiting: every fetched pin names a repository (or URL), a path and a revision",
      all((p["repo"] or p["source"] == "url") and (p["path"] or p["source"] == "git") and (p["revision"] or p["source"] == "url")
          for n, p in P_.items() if p["source"] != "local-v2"),
      str([n for n, p in P_.items() if p["source"] != "local-v2" and not p["revision"] and p["source"] != "url"]))
check("d2: Mem0 J at the deletion's first parent b3ede5b7, llm_judge.py and prompts.py",
      (P_["locomo_j_prompt"]["repo"], P_["locomo_j_prompt"]["revision"], P_["locomo_j_prompt"]["path"])
      == ("mem0ai/mem0", "b3ede5b7c0ac0e847b03786a603c107ac943b3ee", "evaluation/metrics/llm_judge.py")
      and P_["locomo_j_prompts"]["path"] == "evaluation/prompts.py")
check("d2: AMA's judge files at AMA-Bench/AMA-Bench ddfd319e - the three configs, evaluate.py, the twin, LICENSE",
      sorted(p["path"] for n, p in P_.items() if n.startswith("ama_") and p["source"] == "github")
      == sorted(["configs/llm_judge.yaml", "configs/llm_judge_api.yaml", "configs/llm_judge_gpt5_mini.yaml",
                 "src/evaluate.py", "utils/evaluation_metrics.py", "LICENSE"])
      and all(p["revision"] == "ddfd319e0be33424288c13806f1eafc63e625b59" for n, p in P_.items()
              if n.startswith("ama_") and p["source"] == "github"))
check("d2: BEAM's official scoring at mohammadtavakoli78/BEAM b2da22ea - compute_metrics, prompts, run_evaluation",
      sorted(p["path"] for n, p in P_.items() if n.startswith("beam_") and p["source"] == "github")
      == ["src/evaluation/compute_metrics.py", "src/evaluation/run_evaluation.py", "src/prompts.py"]
      and all(p["repo"] == "mohammadtavakoli78/BEAM" for n, p in P_.items() if n.startswith("beam_") and p["source"] == "github"))
check("P6: a pin that declares no licence accepts only a known one",
      refused(lambda: CP.fill("amem_source", revision="r", sha256=sha, size=len(data), licence_found="WTFPL",
                              pins=mutated("amem_source", licence=None)), CP.PinRefused, "unknown")
      and CP.fill("amem_source", revision="r", sha256=sha, size=len(data), licence_found="mit",
                  pins=mutated("amem_source", licence=None))["licence_found"] == "mit")
check("Q-A3F-6: mem0's LICENSE at the pinned commit b3ede5b7 is licence evidence for its two scoring pins",
      (P_["mem0_licence"]["role"], P_["mem0_licence"]["repo"], P_["mem0_licence"]["path"], P_["mem0_licence"]["revision"])
      == ("licence-evidence", "mem0ai/mem0", "LICENSE", "b3ede5b7c0ac0e847b03786a603c107ac943b3ee")
      and P_["mem0_licence"]["revision"] == P_["locomo_j_prompt"]["revision"] == P_["locomo_j_prompts"]["revision"]
      and P_["mem0_licence"]["licence"] == "Apache-2.0")
check("A3.g: a3-git runs on its own arm fetch-git, declared at the top; every other window on fetch",
      MAN["windows"]["a3-git"].get("arms") == ["fetch-git"] and MAN.get("arms") == ["fetch", "fetch-git"]
      and all("arms" not in w for k, w in MAN["windows"].items() if k != "a3-git"))
HUB, PR = Path("H:/hub"), Path("R:/runs/_pins")
check("location(): each source has one place - the hub layout, _pins/github/<commit>, _pins/url/<sha256>, "
      "_pins/git/<commit>/ls-tree.txt, a v2 pin _pins/local-v2/<sha256> (O2: never the code's own tree)",
      CP.location("beam_128k", hf_hub=HUB, pins_root=PR)
      == HUB / "datasets--Mohammadta--BEAM" / "snapshots" / CP.REV["beam"] / "data" / "100K-00000-of-00001.parquet"
      and CP.location("bge_m3_tokenizer_json", hf_hub=HUB, pins_root=PR)
      == HUB / "models--BAAI--bge-m3" / "snapshots" / P_["bge_m3_tokenizer_json"]["revision"] / "tokenizer.json"
      and CP.location("mab_fc_sh_6k", hf_hub=HUB, pins_root=PR)
      == PR / "github" / P_["mab_fc_sh_6k"]["revision"] / "configs" / "data_conf" / "Conflict_Resolution" / "Factconsolidation_sh_6k.yaml"
      and CP.location("tiktoken_cl100k_base", hf_hub=HUB, pins_root=PR, sha256="1" * 64)
      == PR / "url" / ("1" * 64) / "cl100k_base.tiktoken"
      and CP.location("amem_source", hf_hub=HUB, pins_root=PR) == PR / "git" / P_["amem_source"]["revision"] / "ls-tree.txt"
      and CP.location("locomo10", hf_hub=HUB, pins_root=PR) == PR / "local-v2" / P_["locomo10"]["sha256"] / "locomo10.json")
check("location(): a URL pin with no sha256 has no place yet",
      refused(lambda: CP.location("tiktoken_cl100k_base", hf_hub=HUB, pins_root=PR, pins=CP.PINS_DECLARED), CP.PinMismatch,
              "sha256"))
check("every pin declares its licence before fill() - none is left to the fetch",
      [n for n, p in CP.PINS.items() if p["licence"] is None] == [])
check("A-mem: the source declares MIT (GitHub's spdx in d1), and its LICENSE at the same commit is licence evidence",
      P_["amem_source"]["licence"] == P_["amem_licence"]["licence"] == "MIT"
      and (P_["amem_licence"]["role"], P_["amem_licence"]["path"], P_["amem_licence"]["repo"])
      == ("licence-evidence", "LICENSE", "agiresearch/A-mem")
      and P_["amem_licence"]["revision"] == P_["amem_source"]["revision"] == "ceffb860f0712bbae97b184d440df62bc910ca8d")
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
