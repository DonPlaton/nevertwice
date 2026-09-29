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

print("\n- the manifest agrees with the tables (A3's PINS and A7's PINS_A7, as one union) -")
A7 = getattr(CP, "PINS_A7", {})
win_pins = {w: set(v.get("pins", ())) for w, v in MAN["windows"].items()}
for name, p in {**CP.PINS, **A7}.items():
    if p["window"]:
        check(f"{name} is listed in exactly its window {p['window']}",
              name in win_pins.get(p["window"], set()) and sum(name in s for s in win_pins.values()) == 1)
check("every manifest pin exists in the union of the tables (Q-A7-P2-1 (1))",
      set().union(*win_pins.values()) <= set(CP.PINS) | set(A7), str(set().union(*win_pins.values()) - set(CP.PINS) - set(A7)))
check("the window hosts are the declared exact names",
      {w: sorted(v["hosts"]) for w, v in MAN["windows"].items()} == {
          "a3-discovery": ["api.github.com", "huggingface.co"],
          "a3-hf": ["cdn-lfs-us-1.hf.co", "huggingface.co", "us.aws.cdn.hf.co"],
          "a3-github": ["raw.githubusercontent.com"],
          "a3-tiktoken": ["openaipublic.blob.core.windows.net"], "a3-git": ["github.com"],
          "a3-pyarrow": ["files.pythonhosted.org", "pypi.org"], "a7-npm": ["registry.npmjs.org"],
          "a7-npm-d": ["registry.npmjs.org"], "a7-discovery": ["api.github.com", "huggingface.co"],
          "a7-docs": ["api.github.com"], "a7-cognee-tag": ["api.github.com"], "a7-github": ["raw.githubusercontent.com"],
          "a7-arxiv": ["export.arxiv.org"],
          "py-base-312": ["api.nuget.org"], "a8-pypi-mem0_v3": ["files.pythonhosted.org", "pypi.org"],
          "a8-pypi-graphiti_v3": ["files.pythonhosted.org", "pypi.org"],
          "a8-pypi-langmem_v3": ["files.pythonhosted.org", "pypi.org"],
          "a8-pypi-cognee_v3": ["files.pythonhosted.org", "pypi.org"],
          "a8-docker-letta": ["auth.docker.io", "production.cloudflare.docker.com", "registry-1.docker.io"],
          "a8-docker-falkordb": ["auth.docker.io", "production.cloudflare.docker.com", "registry-1.docker.io"],
          "a8-spacy-model": ["api.github.com", "github.com", "objects.githubusercontent.com", "raw.githubusercontent.com",
                             "release-assets.githubusercontent.com"],
          "a8-supermemory-bin": ["api.github.com", "github.com", "objects.githubusercontent.com",
                                 "release-assets.githubusercontent.com"]})
check("a3-hf's hosts come from the discovery record d1; discovery follows no redirect",
      MAN["windows"]["a3-hf"]["hosts_from_record"] == CP.DISCOVERY_D1 and MAN["windows"]["a3-discovery"]["max_redirects"] == 0)

print("\n- A7 phase 2 (the auditor's Q-A7-P2-1 O-a): PINS_A7, a table of its own for the window a7-github -")
LME_R, MAB_R = "9e0b455f4ef0e2ab8f2e582289761153549043fc", "538026089d1a8a8eff05121d0db89b388f360eba"
AMA_R, BEAM_R = "ddfd319e0be33424288c13806f1eafc63e625b59", "b2da22eac88bb0874c64665f13457eb99835774a"
LME_G, MAB_G, BEAM_G, AMA_G = "xiaowu0162/LongMemEval", "HUST-AI-HYZ/MemoryAgentBench", "mohammadtavakoli78/BEAM", "AMA-Bench/AMA-Bench"
BEAM_Q = "src/answer_probing_questions/"
WANT_A7 = {                                  # the 17 the auditor fixed (2026-09-29 23:2x): name -> (repo, revision, path, role)
    "lme_readme": (LME_G, LME_R, "README.md", "prompt"),
    "mab_agent": (MAB_G, MAB_R, "agent.py", "prompt"), "mab_main": (MAB_G, MAB_R, "main.py", "prompt"),
    "mab_init": (MAB_G, MAB_R, "initialization.py", "prompt"),
    "mab_conv": (MAB_G, MAB_R, "conversation_creator.py", "prompt"), "mab_readme": (MAB_G, MAB_R, "README.md", "prompt"),
    "beam_answer_generation": (BEAM_G, BEAM_R, BEAM_Q + "answer_generation.py", "prompt"),
    "beam_ltm_methods": (BEAM_G, BEAM_R, BEAM_Q + "long_term_memory_methods.py", "prompt"),
    "beam_light": (BEAM_G, BEAM_R, BEAM_Q + "light.py", "prompt"),
    "beam_answer_sh": (BEAM_G, BEAM_R, BEAM_Q + "answer_generation.sh", "prompt"),
    "beam_readme": (BEAM_G, BEAM_R, "README.md", "prompt"),
    "ama_harness": (AMA_G, AMA_R, "src/agent_harness.py", "prompt"), "ama_run": (AMA_G, AMA_R, "src/run.py", "prompt"),
    "ama_agent_prompt": (AMA_G, AMA_R, "src/method/ama_agent_core/prompt.py", "prompt"),
    "ama_extract_answer": (AMA_G, AMA_R, "utils/extract_final_answer.py", "scoring"),
    "ama_agent_conf": (AMA_G, AMA_R, "configs/ama_agent.yaml", "prompt"), "ama_readme": (AMA_G, AMA_R, "README.md", "prompt")}
#: the auditor's cognee pins (2026-09-30 00:43): 35 of the 48 files a7-cognee-tag d1 selected, at the tag v1.6.1's
#: commit - 17 arm-source (the vendor's BEAM harness and its own two summaries), 15 prompt (the answer prompts and the
#: vendor-recommended configs), 3 scoring; the nine raw *.json.gz runs and the four empty __init__.py are not pinned
COG_G, COG_R, COG_P = "topoteretes/cognee", "eb90d03740755f5252b8b12cce91fd09970f2d81", "cognee/eval_framework/"
COG_ARTS, COG_PRE = "beam/report_artifacts/", "beam/preprocessing/"
COGNEE = {
    "cognee_run_beam_eval": ("run_beam_eval.py", "arm-source"),
    "cognee_beam_adapter": ("benchmark_adapters/beam_adapter.py", "arm-source"),
    "cognee_beam_router": ("answer_generation/beam_router.py", "arm-source"),
    "cognee_local_ingest": ("beam/local_ingest.py", "arm-source"),
    "cognee_session_io": ("beam/session_io.py", "arm-source"),
    "cognee_preprocess": (COG_PRE + "preprocess.py", "arm-source"),
    "cognee_conversation_preprocessing": (COG_PRE + "conversation_preprocessing.py", "arm-source"),
    "cognee_compression": (COG_PRE + "compression.py", "arm-source"),
    "cognee_loaders": (COG_PRE + "loaders.py", "arm-source"),
    "cognee_eval_adapter": ("beam/eval/beam_eval_adapter.py", "arm-source"),
    "cognee_eval_registry": ("beam/eval/registry.py", "arm-source"),
    "cognee_run_sweep": ("beam/eval/run_sweep.py", "arm-source"),
    "cognee_sweep": ("beam/eval/sweep.py", "arm-source"),
    "cognee_report": ("beam/REPORT.md", "arm-source"),
    "cognee_artifacts_readme": (COG_ARTS + "README.md", "arm-source"),
    "cognee_100k_summary": (COG_ARTS + "100k_fixed/hybrid_completion_20_20_qa_v1_cross_run_summary.json", "arm-source"),
    "cognee_10m_summary": (COG_ARTS + "10m_routed/routed_by_question_type_cross_run_summary.json", "arm-source"),
    **{f"cognee_qa_{s}": (COG_ARTS + f"qa_prompts/{s}.txt", "prompt")
       for s in ("abstention", "contradiction_resolution", "default", "event_ordering", "information_extraction",
                 "instruction_following", "knowledge_update", "multi_session_reasoning", "preference_following",
                 "summarization", "temporal_reasoning")},
    "cognee_turn_compression_prompt": (COG_PRE + "prompts/beam_turn_compression_prompt.txt", "prompt"),
    "cognee_100k_config": (COG_ARTS + "100k_fixed/beam_hybrid_completion_20_20_qa_v1_config.json", "prompt"),
    "cognee_10m_routing_configs": (COG_ARTS + "10m_routed/beam_qa_v1_hybrid_routing_configs.json", "prompt"),
    "cognee_10m_routing": (COG_ARTS + "10m_routed/routing.json", "prompt"),
    "cognee_beam_rubric": ("beam/eval/metrics/beam_rubric.py", "scoring"),
    "cognee_kendall_tau": ("beam/eval/metrics/kendall_tau.py", "scoring"),
    "cognee_aggregate_cross_run": ("beam/eval/aggregate_cross_run.py", "scoring")}
WANT_A7.update({n: (COG_G, COG_R, COG_P + path, role) for n, (path, role) in COGNEE.items()})
check("A7-1: PINS_A7 holds exactly the 52 pins the auditor fixed - the 17 of phase 2 and the 35 cognee files - each its "
      "repository, commit, path and role", {n: (p["repo"], p["revision"], p["path"], p["role"]) for n, p in A7.items()}
      == WANT_A7 and len(WANT_A7) == 52 and len(COGNEE) == 35
      and [sum(r == x for _p, r in COGNEE.values()) for x in ("arm-source", "prompt", "scoring")] == [17, 15, 3],
      str(sorted(set(A7) ^ set(WANT_A7))))
COG_FROM = "a7-cognee-tag d1 1b5d58bfb9f4"
check("A7-2: every A7 pin is a GitHub file of the window a7-github, unfilled until it (no sha256, no size), its revision "
      "from a discovery record that holds its tree - phase 2's from a3-discovery d1 or d2, cognee's from a7-cognee-tag d1",
      bool(A7) and all(
          p["source"] == "github" and p["window"] == "a7-github" and p["sha256"] is None and p["bytes"] is None
          and (str(p["revision_from"]) == COG_FROM if n in COGNEE
               else str(p["revision_from"]).startswith(("a3-discovery d1 ", "a3-discovery d2 "))) for n, p in A7.items())
      and {n for n, p in A7.items() if p["revision_from"] == COG_FROM} == set(COGNEE),
      str([(n, p["revision_from"]) for n, p in A7.items() if p["sha256"] is not None or p["window"] != "a7-github"
           or (n in COGNEE) != (p["revision_from"] == COG_FROM)]))
check("A7-2b: the cognee pins serve BEAM (S5) only, under the licence cognee's repository states (Apache-2.0), and "
      "their record is the auditor's a7-cognee-tag d1 (d5_report.json 1b5d58bf...6620) at the tag v1.6.1",
      all(tuple(A7[n]["stands"]) == ("S5",) and A7[n]["licence"] == "Apache-2.0" for n in COGNEE if n in A7)
      and all(n in A7 for n in COGNEE)
      and getattr(CP, "COGNEE_TAG_D1", None) == "1b5d58bfb9f484249ba0ac32f14c673c0db27f140a0f07825426ae34934e6620"
      and getattr(CP, "COGNEE_TAG", None) == "v1.6.1"
      and getattr(CP, "A7_DISCOVERY_D1", None) == "1689534abb103971d502774dfd2a4566d6947c3efaeda552dbe3704b72ed037e",
      str([(n, A7[n]["stands"], A7[n]["licence"]) for n in COGNEE if n in A7][:3]))
tf, tferr = (None, None)
try:                                          # a missing function FAILs these rows by name, never the suite
    tf = (CP.table_for("a7-github") is CP.PINS_A7, CP.table_for("a3-github") is CP.PINS, CP.table_for("a3-hf") is CP.PINS)
except Exception as e:  # noqa: BLE001
    tferr = f"{type(e).__name__}: {e}"
check("A7-3: table_for(window) - a7-github's table is PINS_A7, the A3 windows' is PINS", tf == (True, True, True),
      str(tferr or tf))
pt, pterr = (None, None)
try:
    real = CP.pinned_twice()
    dup_name = CP.pinned_twice((CP.PINS, {**A7, "mab_templates": copy.deepcopy(A7["lme_readme"])}))   # another file
    dup_file = CP.pinned_twice((CP.PINS, {**A7, "mab_eval_again": {**copy.deepcopy(CP.PINS["mab_eval_data_utils"]),
                                                                   "window": "a7-github"}}))
    pt = (real, dup_name, dup_file)
except Exception as e:  # noqa: BLE001
    pterr = f"{type(e).__name__}: {e}"
check("A7-4: pinned_twice() - none across the real tables; a name in both tables is named (even for another file), and "
      "so is a file (repository, commit, path) pinned in both under another name (Q-A7-P2-1 (1): a file is pinned once)",
      pt is not None and pt[0] == [] and pt[1] == ["mab_templates: in table 0 and table 1"]
      and any("mab_eval_again" in x and "mab_eval_data_utils" in x for x in pt[2]), str(pterr or pt))
fr, frerr = (None, None)
try:
    CP.check_rules(A7)
    fr = CP.freeze_fragment(A7)
except Exception as e:  # noqa: BLE001
    frerr = f"{type(e).__name__}: {e}"
mr, mrerr = (None, None)
try:
    ok_rc = CP.main([])
    saved_a7 = CP.PINS_A7
    CP.PINS_A7 = {**saved_a7, "mab_again": {**copy.deepcopy(CP.PINS["mab_eval_data_utils"]), "window": "a7-github"}}
    try:
        CP.main([])
        dup_rc = "accepted"
    except CP.PinRefused as e:
        dup_rc = f"refused: {e}"
    finally:
        CP.PINS_A7 = saved_a7
    mr = (ok_rc, dup_rc)
except Exception as e:  # noqa: BLE001
    mrerr = f"{type(e).__name__}: {e}"
check("A7-4b: the table's own command refuses a file pinned twice across the tables (PinRefused, named) and passes the "
      "real ones", mr is not None and mr[0] == 0 and mr[1].startswith("refused") and "mab_again" in mr[1], str(mrerr or mr))
nb, nberr = (None, None)
try:                                          # the auditor (2026-09-30 00:43, AP1): the refusal is checked both ways
    other_rev = "0123456789abcdef0123456789abcdef01234567"
    neighbour = {**copy.deepcopy(CP.PINS["mab_eval_data_utils"]), "window": "a7-github", "revision": other_rev}
    twice_nb = CP.pinned_twice((CP.PINS, {**A7, "mab_eval_other_commit": neighbour}))
    saved_a7 = CP.PINS_A7
    CP.PINS_A7 = {**saved_a7, "mab_eval_other_commit": neighbour}
    try:
        main_nb = CP.main([])
    except CP.PinRefused as e:
        main_nb = f"refused: {e}"
    finally:
        CP.PINS_A7 = saved_a7
    nb = (twice_nb, main_nb, neighbour["revision"] != CP.PINS["mab_eval_data_utils"]["revision"])
except Exception as e:  # noqa: BLE001
    nberr = f"{type(e).__name__}: {e}"
check("A7-4c: an honest neighbour - the same source, repository and path at ANOTHER commit, in the other table - is not "
      "a file pinned twice: pinned_twice() names nothing and the table's own command passes (a commit is part of a file's "
      "identity)", nb is not None and nb == ([], 0, True), str(nberr or nb))
check("A7-5: the table's rules hold for PINS_A7, and its fragment files the 35 prompt and scoring pins under "
      "prompt_files and the 17 cognee arm-source pins under arm_sources, nothing elsewhere",
      fr is not None and sorted(fr["prompt_files"]) == sorted(n for n, v in WANT_A7.items() if v[3] in ("prompt", "scoring"))
      and len(fr["prompt_files"]) == 35
      and sorted(fr["arm_sources"]) == sorted(n for n, (_p, r) in COGNEE.items() if r == "arm-source")
      and not any(fr[g] for g in fr if g not in ("prompt_files", "arm_sources")),
      str(frerr or {g: sorted(v) for g, v in (fr or {}).items()}))

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
