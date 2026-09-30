#!/usr/bin/env python3
"""PREREG-V3 TB5 (plan step A3.c): the v3 pin table - every file the v3 stands read, pinned by revision and sha256.

Committed BEFORE any data byte arrives: the list of files, their sources, roles, stands and declared licences is
fixed here with ``revision``/``sha256``/``bytes`` left None; the A3 fetch windows fill those in, and a pin is only
ever filled once. research/corpus_pin.py (v2) is not touched (the auditor's O2a: the evidence manifest cites it); its
three pins that v3 reuses are copied here BY VALUE, and a test holds the copies to the old module.

* ``verify(name, path)`` raises unless the pin is filled and the file's size and sha256 are the pin's - never a
  warning (the v2 module's rule).
* Licences (PREREG-V3 §3.1 T30): no ND-licensed file is ever accepted; the licence found at fetch (the card's own
  field) must equal the declared one.
* Roles: a smoke pin serves only "-smoke" stands (§3.1: no smoke unit comes from a list a scored cell scores).
* Prompts, scoring files, templates and the MAB chunker tokenizer are pinned by sha only and never committed
  (the auditor's Q-A3-6); the harness reads them from their pinned download location.
* Data lands in the polygon (the HF cache for HF files, <runs>\\_pins for the rest), never in the repository.

    python research/v3/corpus_pin_v3.py --list
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

ROLES = ("evaluation", "bracket", "smoke", "tokenizer", "prompt", "scoring", "arm-source", "licence-evidence")
SOURCES = ("hf-dataset", "hf-model", "url", "github", "git", "local-v2")
#: The licences the declared pins may carry (§3.1). Anything with ND is refused wherever it appears.
LICENCES = frozenset({"MIT", "Apache-2.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-NC-4.0", "BSD-3-Clause",
                      "CC-BY-SA-4.0 (data); MIT (code)"})
#: The single licences a fetch may find on a pin that declares none (P6: anything else is refused).
SINGLE_LICENCES = frozenset(x for x in LICENCES if "(" not in x)
_ND = re.compile(r"(?i)(\bND\b|no[-\s]?deriv)")


def _pin(role, stands, source, repo, path, licence, window, prereg, note="", cross_check=None):
    return {"role": role, "stands": tuple(stands), "source": source, "repo": repo, "path": path,
            "revision": None, "revision_from": None, "sha256": None, "bytes": None, "licence": licence,
            "licence_found": None,
            "window": window, "prereg": prereg, "note": note, "cross_check": cross_check}


#: The v2 pins v3 reuses, copied by value from research/corpus_pin.CORPORA (S4, S9; the v2 oracle for S3's
#: "one pin if byte-identical" rule).
V2_PINS = {
    "locomo10": {"sha256": "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4", "bytes": 2805274,
                 "path": "research/data/locomo10.json"},
    "longmemeval_s": {"sha256": "08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894", "bytes": 278025796,
                      "path": "research/data/longmemeval_s.json"},
    "longmemeval_oracle": {"sha256": "821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c",
                           "bytes": 15388478, "path": "research/data/longmemeval_oracle.json"},
}

#: The a3-discovery record the revisions and paths below come from (runs\\_fetch\\a3-discovery\\d1\\record.json), with the
#: auditor's rulings P3/P5/P7/P8/P9 (2026-09-26). A sha256 and a size are filled only by a fetch window.
DISCOVERY_D1 = "db131b4983a91629ef3f8906e5b1f6659a7c3f17b4713a2b84e4b9afdff18210"
REV = {"lme": "98d7416c24c778c2fee6e6f3006e7a073259d48f", "beam": "3205395e897e7318c7b094ef4e6047b9b82dbb03",
       "mab": "7ea066982b140a19337e17e60d45d4076e042faf", "ama": "a5777378066f53229a94557a7b192435cd027909",
       "bge": "5617a9f61b028005a4858fdac845db406aefb181", "gh_lme": "9e0b455f4ef0e2ab8f2e582289761153549043fc",
       "gh_locomo": "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376", "gh_mab": "538026089d1a8a8eff05121d0db89b388f360eba",
       "gh_amem": "ceffb860f0712bbae97b184d440df62bc910ca8d"}
#: The follow-up record d2 (the auditor's P1/P4/P10): AMA-Hub moved to AMA-Bench/AMA-Bench; mem0's evaluation/ was
#: deleted in 9315e303, so its pin is that commit's first parent; BEAM's official repository, named from the paper.
DISCOVERY_D2 = "f793c59a6ec980a94255a8abe0530a3c72d3c782d213df30375adabbd8a360d4"
REV_D2 = {"gh_mem0": "b3ede5b7c0ac0e847b03786a603c107ac943b3ee", "gh_ama": "ddfd319e0be33424288c13806f1eafc63e625b59",
          "gh_beam": "b2da22eac88bb0874c64665f13457eb99835774a"}
#: A7's records (the auditor's fixing): cognee's tag v1.6.1 - the pinned cognee==1.6.1 - and the tree at its commit,
#: from which its BEAM harness files were selected (a7-cognee-tag d1, d5_report.json); cognee's repository and its
#: licence (a7-discovery d1, record.json), which also holds all-MiniLM-L6-v2's revision, card and tree (its jobs 1 and 3).
COGNEE_TAG, COGNEE_TAG_D1 = "v1.6.1", "1b5d58bfb9f484249ba0ac32f14c673c0db27f140a0f07825426ae34934e6620"
A7_DISCOVERY_D1 = "1689534abb103971d502774dfd2a4566d6947c3efaeda552dbe3704b72ed037e"
REV_A7 = {"gh_cognee": "eb90d03740755f5252b8b12cce91fd09970f2d81", "hf_minilm": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"}
_RECORDS = {"d1": ("a3-discovery d1", DISCOVERY_D1), "d2": ("a3-discovery d2", DISCOVERY_D2),
            "cognee-tag": ("a7-cognee-tag d1", COGNEE_TAG_D1), "a7-discovery": ("a7-discovery d1", A7_DISCOVERY_D1)}


def _at(pin: dict, revision: str, path: str | None = None, *, record: str = "d1") -> dict:
    """A pin whose revision (and path) a discovery record declares (d1, d2, or A7's cognee-tag and a7-discovery)."""
    label, rec_sha = _RECORDS[record]
    pin.update(revision=revision, revision_from=f"{label} {rec_sha[:12]}")
    if path is not None:
        pin["path"] = path
    return pin


LME = "xiaowu0162/longmemeval-cleaned"
BEAM, MABD, AMAD, BGE = "Mohammadta/BEAM", "ai-hyz/MemoryAgentBench", "AMA-bench/AMA-bench", "BAAI/bge-m3"
GH_LME, GH_LOCOMO, GH_MAB = "xiaowu0162/LongMemEval", "snap-research/locomo", "HUST-AI-HYZ/MemoryAgentBench"
GH_MEM0, GH_AMA, GH_BEAM = "mem0ai/mem0", "AMA-Bench/AMA-Bench", "mohammadtavakoli78/BEAM"
GH_COGNEE = "topoteretes/cognee"
HF_MINILM = "sentence-transformers/all-MiniLM-L6-v2"
BEAM_LIC = "CC-BY-SA-4.0 (data); MIT (code)"
PINS: dict[str, dict] = {
    # ── evaluation and bracket data (§3.1, lines 814-823) ──
    "lme_s_cleaned": _at(_pin("evaluation", ["S1"], "hf-dataset", LME, "longmemeval_s_cleaned.json", "MIT", "a3-hf", 814),
                         REV["lme"]),
    "lme_m_cleaned": _at(_pin("evaluation", ["S2"], "hf-dataset", LME, "longmemeval_m_cleaned.json", "MIT", "a3-hf", 815),
                         REV["lme"]),
    # Q-A3F-8: one pin, the v2 oracle's value (S3: "one pin if byte-identical"; d1's LFS oid is 821a2034) - the a3-hf
    # window still fetches it and records the byte identity; fill() never runs on it.
    "lme_oracle_cleaned": {**_at(_pin("bracket", ["S3"], "hf-dataset", LME, "longmemeval_oracle.json", "MIT", "a3-hf", 816,
                                      note="the v2 longmemeval_oracle pin (821a2034), byte-identical per d1; confirmed at fetch"),
                                 REV["lme"]),
                           "sha256": V2_PINS["longmemeval_oracle"]["sha256"], "bytes": V2_PINS["longmemeval_oracle"]["bytes"],
                           "alias_of": "v2:longmemeval_oracle"},
    "beam_128k": _at(_pin("evaluation", ["S5"], "hf-dataset", BEAM, None, BEAM_LIC, "a3-hf", 818,
                          note="§3.1's 128K split is the HF split 100K (README 128K, HF 100K; P3)"),
                     REV["beam"], "data/100K-00000-of-00001.parquet"),
    "mab_conflict_resolution": _at(_pin("evaluation", ["S6", "S6L"], "hf-dataset", MABD, None, "MIT", "a3-hf", 819,
                                        note="Conflict_Resolution: FC-SH and FC-MH x 6K/32K/64K/262K"),
                                   REV["mab"], "data/Conflict_Resolution-00000-of-00001.parquet"),
    "ama_swe": _at(_pin("evaluation", ["S7"], "hf-dataset", AMAD, None, "MIT", "a3-hf", 821,
                        note="one file for both domains (P9): the domain rule selects SWE (34 or 36 trajectories, 432 QA)"),
                   REV["ama"], "test/open_end_qa_set.jsonl"),
    # ── smoke data, outside every scored list (§9.4, lines 1801-1806) ──
    "beam_500k": _at(_pin("smoke", ["S5-smoke"], "hf-dataset", BEAM, None, BEAM_LIC, "a3-hf", 1801,
                          note="the 500K split, whole file; its first conversation, cut at 128K cl100k tokens"),
                     REV["beam"], "data/500K-00000-of-00001.parquet"),
    "mab_accurate_retrieval": _at(_pin("smoke", ["S6-smoke"], "hf-dataset", MABD, None, "MIT", "a3-hf", 1803,
                                       note="the Accurate_Retrieval row closest to 32K cl100k tokens"),
                                  REV["mab"], "data/Accurate_Retrieval-00000-of-00001.parquet"),
    "ama_non_swe": _at(_pin("smoke", ["S7-smoke"], "hf-dataset", AMAD, None, "MIT", "a3-hf", 1805,
                            note="the same file as ama_swe (P9); the non-SWE domains only, disjoint by trajectory id"),
                       REV["ama"], "test/open_end_qa_set.jsonl"),
    # ── tokenizers (§5.1 line 1302, §5.2 line 1325); the bge-m3 weights are never fetched ──
    "bge_m3_tokenizer_json": _at(_pin("tokenizer", ["all"], "hf-model", BGE, None, "MIT", "a3-hf", 1302), REV["bge"],
                                 "tokenizer.json"),
    "bge_m3_sentencepiece": _at(_pin("tokenizer", ["all"], "hf-model", BGE, None, "MIT", "a3-hf", 1302), REV["bge"],
                                "sentencepiece.bpe.model"),
    "bge_m3_tokenizer_config": _at(_pin("tokenizer", ["all"], "hf-model", BGE, None, "MIT", "a3-hf", 1302), REV["bge"],
                                   "tokenizer_config.json"),
    "bge_m3_special_tokens": _at(_pin("tokenizer", ["all"], "hf-model", BGE, None, "MIT", "a3-hf", 1302), REV["bge"],
                                 "special_tokens_map.json"),
    "tiktoken_cl100k_base": _pin("tokenizer", ["all"], "url", None,
                                 "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken", "MIT",
                                 "a3-tiktoken", 1325,
                                 cross_check={"sha256": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
                                              "source": "tiktoken's openai_public.py expected_hash [to confirm at fetch]"}),
    # ── official prompts and scoring files: pinned by sha only, never committed (Q-A3-6; P8) ──
    "lme_evaluate_qa": _at(_pin("scoring", ["S1", "S2", "S3"], "github", GH_LME, None, "MIT", "a3-github", 1608,
                                note="the per-type judge prompts"), REV["gh_lme"], "src/evaluation/evaluate_qa.py"),
    "lme_answer_prompt": _at(_pin("prompt", ["S1", "S2", "S3"], "github", GH_LME, None, "MIT", "a3-github", 1557),
                             REV["gh_lme"], "src/generation/run_generation.py"),
    "locomo_evaluate_qa": _at(_pin("scoring", ["S4"], "github", GH_LOCOMO, None, "CC-BY-NC-4.0", "a3-github", 1557),
                              REV["gh_locomo"], "task_eval/evaluate_qa.py"),
    "locomo_evaluation": _at(_pin("scoring", ["S4"], "github", GH_LOCOMO, None, "CC-BY-NC-4.0", "a3-github", 1557,
                                  note="the official token F1 and the cat-5 string check"),
                             REV["gh_locomo"], "task_eval/evaluation.py"),
    "locomo_answer_prompt": _at(_pin("prompt", ["S4"], "github", GH_LOCOMO, None, "CC-BY-NC-4.0", "a3-github", 1557,
                                     note="the answer prompt with the cat-5 'No information available' instruction"),
                                REV["gh_locomo"], "task_eval/gpt_utils.py"),
    "locomo_licence_txt": _at(_pin("licence-evidence", ["S4"], "github", GH_LOCOMO, None, "CC-BY-NC-4.0", "a3-github", 817,
                                   note="P7: GitHub reports NOASSERTION; this file at the pinned commit is the evidence"),
                              REV["gh_locomo"], "LICENSE.txt"),
    "locomo_j_prompt": _at(_pin("scoring", ["S4"], "github", GH_MEM0, None, "Apache-2.0", "a3-github", 1609,
                                note="the Mem0-paper J prompt; evaluation/ deleted in 9315e303, pinned at its parent"),
                           REV_D2["gh_mem0"], "evaluation/metrics/llm_judge.py", record="d2"),
    "mem0_licence": _at(_pin("licence-evidence", ["S4"], "github", GH_MEM0, None, "Apache-2.0", "a3-github", 1609,
                             note="Q-A3F-6: the LICENSE at the pinned commit; the repository's current licence is not evidence"),
                        REV_D2["gh_mem0"], "LICENSE", record="d2"),
    "locomo_j_prompts": _at(_pin("scoring", ["S4"], "github", GH_MEM0, None, "Apache-2.0", "a3-github", 1609,
                                 note="needed if llm_judge.py takes its prompt from here - confirmed at fetch"),
                            REV_D2["gh_mem0"], "evaluation/prompts.py", record="d2"),
    "beam_compute_metrics": _at(_pin("scoring", ["S5"], "github", GH_BEAM, None, BEAM_LIC, "a3-github", 818,
                                     note="nugget-judge averaging and kendalltau(variant='b') - confirmed at fetch"),
                                REV_D2["gh_beam"], "src/evaluation/compute_metrics.py", record="d2"),
    "beam_prompts": _at(_pin("scoring", ["S5"], "github", GH_BEAM, None, BEAM_LIC, "a3-github", 818,
                             note="the nugget rubric; the 0/0.5/1 scale confirmed at fetch"),
                        REV_D2["gh_beam"], "src/prompts.py", record="d2"),
    "beam_run_evaluation": _at(_pin("scoring", ["S5"], "github", GH_BEAM, None, BEAM_LIC, "a3-github", 818,
                                    note="the entry point: how the rubric and tau-b are applied"),
                               REV_D2["gh_beam"], "src/evaluation/run_evaluation.py", record="d2"),
    "mab_templates": _at(_pin("prompt", ["S6", "S6L"], "github", GH_MAB, None, "MIT", "a3-github", 819,
                              note="the FC template"), REV["gh_mab"], "utils/templates.py"),
    "mab_eval_other_utils": _at(_pin("scoring", ["S6", "S6L"], "github", GH_MAB, None, "MIT", "a3-github", 819,
                                     note="the exact-match scorer"), REV["gh_mab"], "utils/eval_other_utils.py"),
    "mab_eval_data_utils": _at(_pin("scoring", ["S6", "S6L"], "github", GH_MAB, None, "MIT", "a3-github", 819,
                                    note="the 512-token chunker; its tokenizer confirmed at A3.j"),
                               REV["gh_mab"], "utils/eval_data_utils.py"),
    **{f"mab_fc_{hop}_{size}": _at(_pin("scoring", ["S6", "S6L"], "github", GH_MAB, None, "MIT", "a3-github", 819,
                                        note="the row's config: chunk size and tokenizer, confirmed at A3.j"),
                                   REV["gh_mab"], f"configs/data_conf/Conflict_Resolution/Factconsolidation_{hop}_{size}.yaml")
       for hop in ("sh", "mh") for size in ("6k", "32k", "64k", "262k")},
    **{f"ama_judge_{v}": _at(_pin("scoring", ["S7"], "github", GH_AMA, None, "MIT", "a3-github", 1611,
                                  note="which config carries the judge prompt text is confirmed at fetch; different texts = stop"),
                             REV_D2["gh_ama"], f"configs/{f}.yaml", record="d2")
       for v, f in (("config", "llm_judge"), ("config_api", "llm_judge_api"), ("config_gpt5_mini", "llm_judge_gpt5_mini"))},
    "ama_evaluate": _at(_pin("scoring", ["S7"], "github", GH_AMA, None, "MIT", "a3-github", 1611,
                             note="the evaluation entry point: which judge config is the default"),
                        REV_D2["gh_ama"], "src/evaluate.py", record="d2"),
    "ama_evaluation_metrics": _at(_pin("scoring", ["S7"], "github", GH_AMA, None, "MIT", "a3-github", 1611,
                                       note="the EM/F1 twin"), REV_D2["gh_ama"], "utils/evaluation_metrics.py", record="d2"),
    "ama_licence": _at(_pin("licence-evidence", ["S7"], "github", GH_AMA, None, "MIT", "a3-github", 821),
                       REV_D2["gh_ama"], "LICENSE", record="d2"),
    # ── arm source (lines 229, 2306) ──
    "amem_source": _at(_pin("arm-source", ["arm:a-mem"], "git", "agiresearch/A-mem", None, "MIT", "a3-git", 229,
                            note="the head commit at discovery; the clone must resolve to it; GitHub's spdx in d1: MIT"),
                       REV["gh_amem"]),
    "amem_licence": _at(_pin("licence-evidence", ["arm:a-mem"], "github", "agiresearch/A-mem", None, "MIT", "a3-github", 229,
                             note="the LICENSE at the pinned commit (d1 tree: 1068 bytes) is the evidence, as LoCoMo's"),
                        REV["gh_amem"], "LICENSE"),
    # ── v2 pins reused by value (S4, S9) ──
    "locomo10": {**_pin("evaluation", ["S4"], "local-v2", "snap-research/locomo", V2_PINS["locomo10"]["path"],
                        "CC-BY-NC-4.0", None, 817), "sha256": V2_PINS["locomo10"]["sha256"],
                 "bytes": V2_PINS["locomo10"]["bytes"], "revision": "v2-pin"},
    "longmemeval_s": {**_pin("evaluation", ["S9"], "local-v2", "xiaowu0162/longmemeval",
                             V2_PINS["longmemeval_s"]["path"], "MIT", None, 823),
                      "sha256": V2_PINS["longmemeval_s"]["sha256"], "bytes": V2_PINS["longmemeval_s"]["bytes"],
                      "revision": "v2-pin"},
}


#: Which part of a compound declaration ("X (data); Y (code)") a pin's role is judged on (P6).
DATA_ROLES = frozenset({"evaluation", "bracket", "smoke"})


def _norm_licence(s: str) -> str:
    return re.sub(r"[\s_]+", "-", s.strip()).casefold()


def licence_matches(declared: str, found: str, role: str) -> bool:
    """P6: the licence a card or repository states against the declared one - case-insensitive SPDX identifiers
    ("mit" == "MIT"); a compound declaration "X (data); Y (code)" is judged on its data part for data files and on
    its code part for code, prompt and tokenizer files. An ND licence never matches."""
    if _ND.search(found) or _ND.search(declared):
        return False
    parts = dict((m.group(2).casefold(), _norm_licence(m.group(1)))
                 for m in re.finditer(r"([^;()]+?)\s*\((data|code)\)", declared))
    want = parts.get("data" if role in DATA_ROLES else "code") if parts else _norm_licence(declared)
    return want is not None and want == _norm_licence(found)


class PinMismatch(RuntimeError):
    """The file is not the pinned file, or the pin is not filled yet."""


class PinRefused(ValueError):
    """A pin the rules do not allow (an ND licence, a licence found at fetch that differs, a smoke pin on a scored
    stand, a pin filled twice)."""


def check_rules(pins: dict | None = None) -> None:
    """Every declared pin obeys the table's rules; raises PinRefused naming the first that does not."""
    for name, p in (pins or PINS).items():
        if p["role"] not in ROLES or p["source"] not in SOURCES:
            raise PinRefused(f"{name}: unknown role or source")
        for lic in (p["licence"], p["licence_found"]):
            if lic is not None and _ND.search(lic):
                raise PinRefused(f"{name}: an ND licence is never used (§3.1)")
        if p["licence"] is not None and p["licence"] not in LICENCES:
            raise PinRefused(f"{name}: licence {p['licence']!r} is not on the declared list")
        if p["role"] == "smoke" and not all(s.endswith("-smoke") for s in p["stands"]):
            raise PinRefused(f"{name}: a smoke pin serves only smoke stands (§3.1)")
        if p["source"] != "local-v2" and p["path"] and not p["path"].startswith("https://") and _absolute_anywhere(p["path"]):
            raise PinRefused(f"{name}: a pin names a repository path, never an absolute one")


def _absolute_anywhere(path: str) -> bool:
    """Absolute on ANY OS, not only the running one (CI 76cb0e9, class C1): a drive ("D:/x", "D:x"), a UNC share, or a
    leading slash of either kind."""
    w = PureWindowsPath(path)
    return bool(w.drive) or w.is_absolute() or PurePosixPath(path).is_absolute() or path.startswith(("/", "\\"))


def fill(name: str, *, revision: str, sha256: str, size: int, licence_found: str | None, path: str | None = None,
         pins: dict | None = None) -> dict:
    """Fill a pin once, from a verified fetch record. A second fill, or a found licence that differs from the declared
    one, is refused; so is a name the table does not hold (B-FILL-KEY)."""
    table = pins if pins is not None else PINS
    if name not in table:
        raise PinRefused(f"{name}: not a pin of this table")
    p = table[name]
    if p["sha256"] is not None:
        raise PinRefused(f"{name}: already pinned; a pin is filled once")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
        raise PinRefused(f"{name}: sha256 must be 64 lower-case hex characters")
    if licence_found is not None and _ND.search(licence_found):
        raise PinRefused(f"{name}: the licence found at fetch is ND")
    if p["licence"] is None and licence_found is not None and not any(
            _norm_licence(licence_found) == _norm_licence(x) for x in SINGLE_LICENCES):
        raise PinRefused(f"{name}: licence found {licence_found!r} is unknown - refused (P6)")
    if p["licence"] is not None and licence_found is not None and not licence_matches(p["licence"], licence_found,
                                                                                       p["role"]):
        raise PinRefused(f"{name}: licence found {licence_found!r} is not the declared {p['licence']!r}")
    p.update(revision=revision, sha256=sha256, bytes=int(size), licence_found=licence_found,
             **({"path": path} if path is not None else {}))
    return p


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify(name: str, path: str | Path, *, pins: dict | None = None) -> dict:
    """Raise PinMismatch unless the pin is filled and ``path`` is exactly its file (size, then sha256)."""
    p = (pins or PINS)[name]
    if p["sha256"] is None or p["bytes"] is None:
        raise PinMismatch(f"{name}: not pinned yet - nothing may read it")
    path = Path(path)
    if not path.is_file():
        raise PinMismatch(f"{name}: no file at the given path")
    if path.stat().st_size != p["bytes"]:
        raise PinMismatch(f"{name}: size {path.stat().st_size} is not the pinned {p['bytes']}")
    got = _sha256_file(path)
    if got != p["sha256"]:
        raise PinMismatch(f"{name}: sha256 {got[:12]}... is not the pinned {p['sha256'][:12]}...")
    return {"pin": name, "sha256": got, "bytes": p["bytes"], "revision": p["revision"]}


def location(name: str, *, hf_hub: Path, pins_root: Path, sha256: str | None = None, pins: dict | None = None) -> Path:
    """Where a fetched pin's file lives after its window (the A3.f placement), so a reader never imports fetch code:
    an HF file in the hub cache layout at its revision, a GitHub file under <pins_root>/github/<commit>, the tiktoken
    file under <pins_root>/url/<its sha256>, and the A-MEM source's ls-tree listing (Q-A3F-1) under
    <pins_root>/git/<commit>. A local-v2 pin is placed once from its repository path (gitignored: only the main working
    tree has it) to <pins_root>/local-v2/<sha256>/<basename> by place_local_v2.py (O2), so a clean worktree finds it."""
    p = (pins or PINS)[name]
    if p["source"] == "local-v2":
        return Path(pins_root) / "local-v2" / p["sha256"] / p["path"].rsplit("/", 1)[-1]
    if p["source"] in ("hf-dataset", "hf-model"):
        kind = "datasets" if p["source"] == "hf-dataset" else "models"
        return Path(hf_hub) / f"{kind}--{p['repo'].replace('/', '--')}" / "snapshots" / p["revision"] / Path(*p["path"].split("/"))
    if p["source"] == "github":
        return Path(pins_root) / "github" / p["revision"] / Path(*p["path"].split("/"))
    if p["source"] == "url":
        sha = sha256 or p["sha256"]
        if not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)):
            raise PinMismatch(f"{name}: a URL pin is located by its sha256, and none is given")
        return Path(pins_root) / "url" / sha / p["path"].rsplit("/", 1)[-1]
    return Path(pins_root) / "git" / p["revision"] / "ls-tree.txt"


#: The values the A3 windows found: each from a window's pin_fill.json that the auditor verified, written by
#: research/v3/pins_apply.py between these markers - never by hand - with its window, run and pin_fill sha256.
# >>> FILLED
FILLED: dict[str, dict] = {
    "ama_evaluate": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "32c5b33e63fc79c9e9e6973484d515d1ea14c65c4f1ab27197a074ba0d111cd9", "bytes": 10350, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "ama_evaluation_metrics": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "0586637d76b0547f09189078f500b5d0b27ca90ce5b722f4278d8d26f5d783b6", "bytes": 9129, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "ama_judge_config": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "d4a1d483220128c1de9bdcf147f480169c7e4c6263514415ded48c8dc9e9d153", "bytes": 314, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "ama_judge_config_api": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "2b2d7157701bdb837afd506285499e8618f673929f2f95ea3cc0dca7c606677f", "bytes": 287, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "ama_judge_config_gpt5_mini": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "5e75845da7b3d1a89f2b1ef255131f6905b999fe5faba9edaf5573368892ade4", "bytes": 291, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "ama_licence": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "3c8bff7214b9a3f79e2b1e76413104fff6df8b315d6598b2e8d16a9cadd2e244", "bytes": 1071, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "ama_non_swe": {"revision": "a5777378066f53229a94557a7b192435cd027909", "sha256": "45c36052e1520d87ad9de4114f71c9df42d4aac9cf158c0c353e800b653d65ff", "bytes": 50451919, "licence_found": "mit", "licence_source": "d1 card AMA-bench/AMA-bench", "from": "a3-hf h2 pin_fill c23ea9cd3549"},
    "ama_swe": {"revision": "a5777378066f53229a94557a7b192435cd027909", "sha256": "45c36052e1520d87ad9de4114f71c9df42d4aac9cf158c0c353e800b653d65ff", "bytes": 50451919, "licence_found": "mit", "licence_source": "d1 card AMA-bench/AMA-bench", "from": "a3-hf h2 pin_fill c23ea9cd3549"},
    "amem_licence": {"revision": "ceffb860f0712bbae97b184d440df62bc910ca8d", "sha256": "c8a51c83455b8afc3a11b085ce57c7b9b34c6725d6b2329ce3dd9a273f0add6d", "bytes": 1068, "licence_found": "MIT", "licence_source": "d1 repo agiresearch/A-mem", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "amem_source": {"revision": "ceffb860f0712bbae97b184d440df62bc910ca8d", "sha256": "ca8e0a6373f9e9c27a0952657fe403816d4b20125156b9d485976a1174f37eb9", "bytes": 1409, "licence_found": "MIT", "licence_source": "d1 repo agiresearch/A-mem", "from": "a3-git r1 pin_fill ba0f9248f430"},
    "beam_128k": {"revision": "3205395e897e7318c7b094ef4e6047b9b82dbb03", "sha256": "c0519be25907005ba873c927c50877471d550873039d96c041554d0075a78ace", "bytes": 5429768, "licence_found": "cc-by-sa-4.0", "licence_source": "d1 card Mohammadta/BEAM", "from": "a3-hf h2 pin_fill c23ea9cd3549"},
    "beam_500k": {"revision": "3205395e897e7318c7b094ef4e6047b9b82dbb03", "sha256": "af05921c979355038e1761b7cde3d2dd713200dd3071b278de0200f6c7f30122", "bytes": 33956263, "licence_found": "cc-by-sa-4.0", "licence_source": "d1 card Mohammadta/BEAM", "from": "a3-hf h2 pin_fill c23ea9cd3549"},
    "beam_compute_metrics": {"revision": "b2da22eac88bb0874c64665f13457eb99835774a", "sha256": "e72bf25d36d521d166862063ec94f8e7875328193142d1edddb9b4677d47780d", "bytes": 19830, "licence_found": "MIT", "licence_source": "d2 repo mohammadtavakoli78/BEAM", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "beam_prompts": {"revision": "b2da22eac88bb0874c64665f13457eb99835774a", "sha256": "81fe51ab2bcd7e8ad218b3a9a969a340c38d31f90ac2d93fa78a3bb890ba157b", "bytes": 633361, "licence_found": "MIT", "licence_source": "d2 repo mohammadtavakoli78/BEAM", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "beam_run_evaluation": {"revision": "b2da22eac88bb0874c64665f13457eb99835774a", "sha256": "518894ab65860043a24259457ddf3b1ab8ae18427b6e01e5aa90cd29c4256a3b", "bytes": 10458, "licence_found": "MIT", "licence_source": "d2 repo mohammadtavakoli78/BEAM", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "bge_m3_sentencepiece": {"revision": "5617a9f61b028005a4858fdac845db406aefb181", "sha256": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865", "bytes": 5069051, "licence_found": "mit", "licence_source": "d1 card BAAI/bge-m3", "from": "a3-hf h2 pin_fill c23ea9cd3549"},
    "bge_m3_special_tokens": {"revision": "5617a9f61b028005a4858fdac845db406aefb181", "sha256": "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835", "bytes": 964, "licence_found": "mit", "licence_source": "d1 card BAAI/bge-m3", "from": "a3-hf h2 pin_fill c23ea9cd3549"},
    "bge_m3_tokenizer_config": {"revision": "5617a9f61b028005a4858fdac845db406aefb181", "sha256": "a62b2b6784f990259fddef5f16388693a8043be4f69179e6a5257eeb3f9abac4", "bytes": 444, "licence_found": "mit", "licence_source": "d1 card BAAI/bge-m3", "from": "a3-hf h2 pin_fill c23ea9cd3549"},
    "bge_m3_tokenizer_json": {"revision": "5617a9f61b028005a4858fdac845db406aefb181", "sha256": "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08", "bytes": 17098108, "licence_found": "mit", "licence_source": "d1 card BAAI/bge-m3", "from": "a3-hf h2 pin_fill c23ea9cd3549"},
    "lme_answer_prompt": {"revision": "9e0b455f4ef0e2ab8f2e582289761153549043fc", "sha256": "4f1eb3c69d7ad40f04065b9c0bc86f6582441018fc6ff751d162d66c95baf672", "bytes": 21608, "licence_found": "MIT", "licence_source": "d1 repo xiaowu0162/LongMemEval", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "lme_evaluate_qa": {"revision": "9e0b455f4ef0e2ab8f2e582289761153549043fc", "sha256": "ecce9c4c79dc89d99534ac17b383a5cbb5b9f0c69ee98adaf0684742e3d95251", "bytes": 7436, "licence_found": "MIT", "licence_source": "d1 repo xiaowu0162/LongMemEval", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "lme_m_cleaned": {"revision": "98d7416c24c778c2fee6e6f3006e7a073259d48f", "sha256": "9d79e5524794a2e6900a3aa9cb7d9152c5a3e8319c9a87c25494ba1eacee495f", "bytes": 2737100077, "licence_found": "mit", "licence_source": "d1 card xiaowu0162/longmemeval-cleaned", "from": "a3-hf h2 pin_fill c23ea9cd3549"},
    "lme_s_cleaned": {"revision": "98d7416c24c778c2fee6e6f3006e7a073259d48f", "sha256": "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442", "bytes": 277383467, "licence_found": "mit", "licence_source": "d1 card xiaowu0162/longmemeval-cleaned", "from": "a3-hf h2 pin_fill c23ea9cd3549"},
    "locomo_answer_prompt": {"revision": "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376", "sha256": "5fc977375878199735acd28fba5ae6f4d657fa0e000c0d2918a90c07b6035793", "bytes": 15800, "licence_found": "CC-BY-NC-4.0", "licence_source": "evidence locomo_licence_txt", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "locomo_evaluate_qa": {"revision": "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376", "sha256": "dde7c1c6b5501486f96ce31398d6e49de76abbfa656e980b137e54fc69e9f6ee", "bytes": 4381, "licence_found": "CC-BY-NC-4.0", "licence_source": "evidence locomo_licence_txt", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "locomo_evaluation": {"revision": "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376", "sha256": "8e3be5d57ff2ff9ec5cd05939592f468c5f3f1fd95d13e431932bdf6bf0fd6fd", "bytes": 9441, "licence_found": "CC-BY-NC-4.0", "licence_source": "evidence locomo_licence_txt", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "locomo_j_prompt": {"revision": "b3ede5b7c0ac0e847b03786a603c107ac943b3ee", "sha256": "ac2e242f6ea0b817ba1b229ff9e5271f6846b7b8a9aefa8a5a5a81ea58df3b3c", "bytes": 4872, "licence_found": "Apache-2.0", "licence_source": "evidence mem0_licence", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "locomo_j_prompts": {"revision": "b3ede5b7c0ac0e847b03786a603c107ac943b3ee", "sha256": "407bea1c1453dc6832c234bb1ed936d401d796fabcc0b28337a47ba63517d3ba", "bytes": 7266, "licence_found": "Apache-2.0", "licence_source": "evidence mem0_licence", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "locomo_licence_txt": {"revision": "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376", "sha256": "41003d4a74749c0220e33dd415042164b5a1093ed401f36277234f772d22d3d0", "bytes": 19347, "licence_found": "CC-BY-NC-4.0", "licence_source": "evidence locomo_licence_txt", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "mab_accurate_retrieval": {"revision": "7ea066982b140a19337e17e60d45d4076e042faf", "sha256": "56c3cd80fb6731a3e53cd1a6be3148f54df60ff2d290ee50e28f8acebf9655c1", "bytes": 20024386, "licence_found": "mit", "licence_source": "d1 card ai-hyz/MemoryAgentBench", "from": "a3-hf h2 pin_fill c23ea9cd3549"},
    "mab_conflict_resolution": {"revision": "7ea066982b140a19337e17e60d45d4076e042faf", "sha256": "24d5c3f09ce0ce15625cb9f8a98f44f0d864ca6c94d7b4ad04eb697ca3a5ff45", "bytes": 1491588, "licence_found": "mit", "licence_source": "d1 card ai-hyz/MemoryAgentBench", "from": "a3-hf h2 pin_fill c23ea9cd3549"},
    "mab_eval_data_utils": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "f5d564996d63a07454999e94ab01087d4d07939f69936045c700fd57d338cb68", "bytes": 13034, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "mab_eval_other_utils": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "d77976be409298970614d477a9d8003850caddb0510e56a7e821a037d98493a2", "bytes": 23895, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "mab_fc_mh_262k": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "c90ba80205a4fb0e7aecbc35e8e11638adb606a6514f5cd4b45b46eab6a877a8", "bytes": 306, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "mab_fc_mh_32k": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "3687a04532e89859b10ec864162eeea8edf48ef0b7d513a93dd39884da5e4b4e", "bytes": 304, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "mab_fc_mh_64k": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "893ba410975471b8f8663f936f380f1c5aba0e025777aa0067ae0917a38b02c4", "bytes": 304, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "mab_fc_mh_6k": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "63312c1dd54a06490f3747e97632d39284ec8eeba2128ee5c599dae72defa6fc", "bytes": 302, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "mab_fc_sh_262k": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "c11528e6b316bdd3d8608ea7e5c6e5ca3944cc7655d0d43a589473eb5ba0f14d", "bytes": 306, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "mab_fc_sh_32k": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "e58f475b2a0ee7382b88f2299ea70302048231a832e92de6938a24f9e2779c5b", "bytes": 304, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "mab_fc_sh_64k": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "003eddd0b9454354ae2bd0afbb90168b34c7a9fbf08151bd9bf42d5cb2c40896", "bytes": 304, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "mab_fc_sh_6k": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "814bf2eca1d07018262a860819db8fc7ddcb7d1a866839caec53ef2993c625e6", "bytes": 303, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "mab_templates": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "148c40d48d19f155ae845482c4417ba59cfa7ae4e194019509e023bd3a8755dd", "bytes": 11961, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "mem0_licence": {"revision": "b3ede5b7c0ac0e847b03786a603c107ac943b3ee", "sha256": "0bbcbe931c353293a2fafce08326181dfeea0e568c566afd4ce8337a70f5e219", "bytes": 11349, "licence_found": "Apache-2.0", "licence_source": "evidence mem0_licence", "from": "a3-github g1 pin_fill 0949dd56da65"},
    "tiktoken_cl100k_base": {"revision": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7", "sha256": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7", "bytes": 1681126, "licence_found": "MIT", "licence_source": "METADATA METADATA sha256 2dab7998e235", "from": "a3-tiktoken t1 pin_fill ab54c52a1117"},
}
# <<< FILLED


def _apply_filled(pins: dict | None = None, filled: dict | None = None) -> None:
    """FILLED into the table through fill() itself, so its rules hold at every import: a 64-hex sha256, the licence
    found against the declared one (P6), a pin filled once. ``filled``/``pins``: A7's block into A7's table."""
    for name, v in (FILLED if filled is None else filled).items():
        fill(name, revision=v["revision"], sha256=v["sha256"], size=v["bytes"], licence_found=v["licence_found"], pins=pins)
        (pins or PINS)[name]["filled_from"] = v["from"]


#: The table as declared, before FILLED - the rule tests fill and verify copies of this one.
PINS_DECLARED = __import__("copy").deepcopy(PINS)
_apply_filled()

# ── A7 phase 2 (the auditor's Q-A7-P2-1 O-a, 2026-09-29): the window a7-github's own table ─────────────────────
#: research/v3/freeze_a3.json is the A3 table as filled and is never rewritten (Q-C5e-3), so A7's pins are not merged
#: into PINS: they live here, fill through the same fill() from their own block below, and get a freeze fragment of
#: their own after their window; FREEZE-V3 (A10) pins both fragments. Phase 2's 17 pins have their trees in a3-discovery
#: d1 or d2 (the auditor verified the blobs and sizes against them), cognee's 35 in a7-cognee-tag d1 (the tree at the tag
#: v1.6.1's commit); a file is pinned once across both tables (pinned_twice).
#: Line 1557 of revision 1: one template per benchmark, the benchmark's own answer prompt - these files say what it is.
PINS_A7: dict[str, dict] = {
    "lme_readme": _at(_pin("prompt", ["S1", "S2", "S3"], "github", GH_LME, None, "MIT", "a7-github", 1557,
                           note="Q-48-1: the documented generation command, the cot switch"), REV["gh_lme"], "README.md"),
    **{f"mab_{k}": _at(_pin("prompt", ["S6", "S6L"], "github", GH_MAB, None, "MIT", "a7-github", 1557,
                            note=f"Q-48-3, Q30: {n}"), REV["gh_mab"], path)
       for k, path, n in (("agent", "agent.py", "where the context is inserted"), ("main", "main.py", "the driver"),
                          ("init", "initialization.py", "the agent's setup"),
                          ("conv", "conversation_creator.py", "how a conversation is built"),
                          ("readme", "README.md", "the documented run"))},
    **{f"beam_{k}": _at(_pin("prompt", ["S5"], "github", GH_BEAM, None, BEAM_LIC, "a7-github", 1557,
                             note=f"Q-48-4: {n}"), REV_D2["gh_beam"], path, record="d2")
       for k, path, n in (("answer_generation", "src/answer_probing_questions/answer_generation.py", "the answer prompt"),
                          ("ltm_methods", "src/answer_probing_questions/long_term_memory_methods.py", "the memory methods"),
                          ("light", "src/answer_probing_questions/light.py", "the LIGHT variant"),
                          ("answer_sh", "src/answer_probing_questions/answer_generation.sh", "the documented command"),
                          ("readme", "README.md", "the documented run"))},
    **{f"ama_{k}": _at(_pin(role, ["S7"], "github", GH_AMA, None, "MIT", "a7-github", 1557, note=f"Q17, Q-48-5: {n}"),
                       REV_D2["gh_ama"], path, record="d2")
       for k, role, path, n in (("harness", "prompt", "src/agent_harness.py", "the agent harness"),
                                ("run", "prompt", "src/run.py", "the run entry point"),
                                ("agent_prompt", "prompt", "src/method/ama_agent_core/prompt.py", "the answer prompt"),
                                ("extract_answer", "scoring", "utils/extract_final_answer.py", "the answer parse"),
                                ("agent_conf", "prompt", "configs/ama_agent.yaml", "the agent's config"),
                                ("readme", "prompt", "README.md", "the documented run"))},
    # the window a7-github-2 (the auditor's Q-TPL-1 = O-b and Q-TPL-4, 2026-09-30 05:4x): how LME's documented command
    # maps READING_METHOD to --cot/--con, and which of AMA's two answer templates the vendor's code chooses (and in which
    # mode its external memory methods run) - read before S1's and S7's templates are chosen
    "lme_run_generation_sh": _at(_pin("prompt", ["S1", "S2", "S3"], "github", GH_LME, None, "MIT", "a7-github-2", 1557,
                                      note="Q-TPL-1: READING_METHOD to --cot/--con"),
                                 REV["gh_lme"], "src/generation/run_generation.sh"),
    **{n: _at(_pin("prompt", ["S7"], "github", GH_AMA, None, "MIT", "a7-github-2", 1557, note=f"Q-TPL-4: {t}"),
              REV_D2["gh_ama"], path, record="d2")
       for n, path, t in (
           ("ama_core_construct", "src/method/ama_agent_core/construct.py", "the agent's memory construction"),
           ("ama_core_retrieve", "src/method/ama_agent_core/retrieve.py", "retrieval, where the answer template is chosen"),
           ("ama_core_tool", "src/method/ama_agent_core/tool.py", "the agent's tools"),
           ("ama_core_utils", "src/method/ama_agent_core/utils.py", "the agent's helpers"),
           ("ama_method_ama_agent", "src/method/ama_agent.py", "the AMA agent method"),
           ("ama_method_agent", "src/method/agent_method.py", "the agent method base"),
           ("ama_method_base", "src/method/base_method.py", "every method's base"),
           ("ama_method_bm25", "src/method/bm25.py", "an external memory method: BM25"),
           ("ama_method_embedding", "src/method/embedding_mem.py", "an external memory method: embeddings"),
           ("ama_method_longcontext", "src/method/longcontext.py", "the long-context baseline"),
           ("ama_method_register", "src/method_register.py", "which method runs under which name"),)},
    # cognee's own BEAM harness at the tag v1.6.1 (Q-46b-6; the auditor's choice 2026-09-30 00:43: 35 of the 48 files
    # a7-cognee-tag d1 selected; the nine raw *.json.gz runs - the two summaries cover them - and the four empty
    # __init__.py are not pinned). The two cross-run summaries are the vendor's own numbers: E5 checks our cognee arm
    # against them, so a trimmed competitor shows (§3.4). Row 230 of revision 1: the vendor-recommended configuration.
    **{f"cognee_{k}": _at(_pin(role, ["S5"], "github", GH_COGNEE, None, "Apache-2.0", "a7-github", 230,
                               note=f"Q-46b-6: {n}"), REV_A7["gh_cognee"], "cognee/eval_framework/" + path,
                          record="cognee-tag")
       for k, role, path, n in (
           ("run_beam_eval", "arm-source", "run_beam_eval.py", "the vendor's BEAM entry point"),
           ("beam_adapter", "arm-source", "benchmark_adapters/beam_adapter.py", "the benchmark adapter"),
           ("beam_router", "arm-source", "answer_generation/beam_router.py", "the answer router"),
           ("local_ingest", "arm-source", "beam/local_ingest.py", "the ingestion"),
           ("session_io", "arm-source", "beam/session_io.py", "the session reader"),
           ("preprocess", "arm-source", "beam/preprocessing/preprocess.py", "the preprocessing"),
           ("conversation_preprocessing", "arm-source", "beam/preprocessing/conversation_preprocessing.py",
            "the conversation preprocessing"),
           ("compression", "arm-source", "beam/preprocessing/compression.py", "the turn compression"),
           ("loaders", "arm-source", "beam/preprocessing/loaders.py", "the loaders"),
           ("eval_adapter", "arm-source", "beam/eval/beam_eval_adapter.py", "the evaluation adapter"),
           ("eval_registry", "arm-source", "beam/eval/registry.py", "the evaluation registry"),
           ("run_sweep", "arm-source", "beam/eval/run_sweep.py", "the sweep driver"),
           ("sweep", "arm-source", "beam/eval/sweep.py", "the sweep"),
           ("report", "arm-source", "beam/REPORT.md", "the vendor's report"),
           ("artifacts_readme", "arm-source", "beam/report_artifacts/README.md", "the artefacts' index"),
           ("100k_summary", "arm-source", "beam/report_artifacts/100k_fixed/hybrid_completion_20_20_qa_v1_cross_run_summary.json",
            "the vendor's own 100K numbers"),
           ("10m_summary", "arm-source", "beam/report_artifacts/10m_routed/routed_by_question_type_cross_run_summary.json",
            "the vendor's own 10M numbers"),
           *((f"qa_{s}", "prompt", f"beam/report_artifacts/qa_prompts/{s}.txt", f"the {s} answer prompt")
             for s in ("abstention", "contradiction_resolution", "default", "event_ordering", "information_extraction",
                       "instruction_following", "knowledge_update", "multi_session_reasoning", "preference_following",
                       "summarization", "temporal_reasoning")),
           ("turn_compression_prompt", "prompt", "beam/preprocessing/prompts/beam_turn_compression_prompt.txt",
            "the turn compression prompt"),
           ("100k_config", "prompt", "beam/report_artifacts/100k_fixed/beam_hybrid_completion_20_20_qa_v1_config.json",
            "the vendor-recommended 100K config"),
           ("10m_routing_configs", "prompt", "beam/report_artifacts/10m_routed/beam_qa_v1_hybrid_routing_configs.json",
            "the vendor-recommended 10M routing configs"),
           ("10m_routing", "prompt", "beam/report_artifacts/10m_routed/routing.json", "the vendor's routing"),
           ("beam_rubric", "scoring", "beam/eval/metrics/beam_rubric.py", "the vendor's rubric"),
           ("kendall_tau", "scoring", "beam/eval/metrics/kendall_tau.py", "the vendor's event-ordering metric"),
           ("aggregate_cross_run", "scoring", "beam/eval/aggregate_cross_run.py", "the vendor's aggregation"))},
    # all-MiniLM-L6-v2 for BEAM's event ordering (Q-49-3 O-b: the official semantic alignment; the auditor's Q-A7-P3 =
    # O-a): the model at the revision a7-discovery d1 found, the A7 plan's nine files and config_sentence_transformers.json
    # (the vendor's similarity_fn); fetched by the window a7-hf on the hosts a7-hf-d d1 showed. Row 1627 of revision 1.
    **{f"minilm_{k}": _at(_pin("scoring", ["S5"], "hf-model", HF_MINILM, None, "Apache-2.0", "a7-hf", 1627,
                               note=f"Q-49-3, Q-A7-P3: {path}"), REV_A7["hf_minilm"], path, record="a7-discovery")
       for k, path in (("modules", "modules.json"), ("config", "config.json"), ("st_config", "sentence_bert_config.json"),
                       ("st_model_config", "config_sentence_transformers.json"), ("tokenizer", "tokenizer.json"),
                       ("tokenizer_config", "tokenizer_config.json"), ("vocab", "vocab.txt"),
                       ("special_tokens", "special_tokens_map.json"), ("pooling", "1_Pooling/config.json"),
                       ("safetensors", "model.safetensors"))},
}
#: The values the windows a7-github and a7-hf found, written by research/v3/pins_apply.py between these markers, never
#: by hand.
# >>> A7 FILLED
FILLED_A7: dict[str, dict] = {
    "ama_agent_conf": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "ab5dbdb22c7d0756a7a34469c8530fbc073e630e24d3811b2ca491da704f4788", "bytes": 893, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "ama_agent_prompt": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "8fddc6d4fcde390f899d82a8d3bcaaa1a162df95897738a31141e12760eadab5", "bytes": 12732, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "ama_core_construct": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "d8292fd4d21931b70f5bbc393668dbfa9c546625d39a3e5234a716dc3d0a42aa", "bytes": 19613, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github-2 g1 pin_fill fa5375b66785"},
    "ama_core_retrieve": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "53c3f512f8ca9ae1a99a0057e5dfb71fe518f0551605e93e956eda611087ce26", "bytes": 18074, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github-2 g1 pin_fill fa5375b66785"},
    "ama_core_tool": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "fc92c419c83bf6dcc9ecebe005bfba49aa88bdc8222f1fbcdbebbcaa92ef301d", "bytes": 10656, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github-2 g1 pin_fill fa5375b66785"},
    "ama_core_utils": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "ba679ca357908c1b0f051772ab4e968b13ed1ce11120268efc9f91597e9e262f", "bytes": 29667, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github-2 g1 pin_fill fa5375b66785"},
    "ama_extract_answer": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "1e925a00c0065b568d037bd93198d53a475dfd3504c222093b8015c24be436b7", "bytes": 1085, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "ama_harness": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "48bae01384a6a7e837367c6eb05f1beb9a5d50d081be814ff8826afe0cccbc2e", "bytes": 10272, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "ama_method_agent": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "ffba4afa974ccaf8354770638619fb565cd648c7ccc0f59bfaa60d40b1578de1", "bytes": 3137, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github-2 g1 pin_fill fa5375b66785"},
    "ama_method_ama_agent": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "e5527c3b1e1a6f3416aee25ac9855e419d1150f6076ed85d5e22032569e4d050", "bytes": 5305, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github-2 g1 pin_fill fa5375b66785"},
    "ama_method_base": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "2d00b8992015e5617a16a7a68cc0dffd6f3d2456760f737ae075210e39775acb", "bytes": 2088, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github-2 g1 pin_fill fa5375b66785"},
    "ama_method_bm25": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "370b5391371a1e12496272b39c064cb95aa68e294f5efe18c0cef78f591e5034", "bytes": 4247, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github-2 g1 pin_fill fa5375b66785"},
    "ama_method_embedding": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "3b463a03676fddfcb9a7609b609a948c00689da4d1be48aa8ebe319ed4b9cdfe", "bytes": 7770, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github-2 g1 pin_fill fa5375b66785"},
    "ama_method_longcontext": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "1ca39ff291ae22c533a540513ac0b52a07ce367d96f07c9c93351d0ddfa39103", "bytes": 9195, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github-2 g1 pin_fill fa5375b66785"},
    "ama_method_register": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "6e5e039012a8b7d8667df8d52117cbbaccd7b4b654dcf4f630ec1ebba0c33ce3", "bytes": 3081, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github-2 g1 pin_fill fa5375b66785"},
    "ama_readme": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "f68fecb130e68cb4450981758af21a999832b13c2d6f4d6a854d8b36cb985b43", "bytes": 15131, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "ama_run": {"revision": "ddfd319e0be33424288c13806f1eafc63e625b59", "sha256": "4530d182e2cab26edb95e30a2a35a6a4161a8bcd18a93afac68730bc7263a52b", "bytes": 17241, "licence_found": "MIT", "licence_source": "d2 repo AMA-Bench/AMA-Bench", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "beam_answer_generation": {"revision": "b2da22eac88bb0874c64665f13457eb99835774a", "sha256": "b8117f64146ebfe5693b0ce6f3478fed7584bca6023c25059f2def49341a1f42", "bytes": 16548, "licence_found": "MIT", "licence_source": "d2 repo mohammadtavakoli78/BEAM", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "beam_answer_sh": {"revision": "b2da22eac88bb0874c64665f13457eb99835774a", "sha256": "93db25374f18f45e17ac4152ff2a44239c84f9e6b403fab5eb4546f55875ab1e", "bytes": 1853, "licence_found": "MIT", "licence_source": "d2 repo mohammadtavakoli78/BEAM", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "beam_light": {"revision": "b2da22eac88bb0874c64665f13457eb99835774a", "sha256": "6bb4a8cbcaca154a647c14671f8d5e2eac500dc0dd5db258f04b2b25837fc67c", "bytes": 22629, "licence_found": "MIT", "licence_source": "d2 repo mohammadtavakoli78/BEAM", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "beam_ltm_methods": {"revision": "b2da22eac88bb0874c64665f13457eb99835774a", "sha256": "d3fd96786d2d58a4b0b60ff726ebf00d1adf84f7bb8487abfd8768f3519cffd8", "bytes": 24223, "licence_found": "MIT", "licence_source": "d2 repo mohammadtavakoli78/BEAM", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "beam_readme": {"revision": "b2da22eac88bb0874c64665f13457eb99835774a", "sha256": "6eb38ece9322cdd96dba99a50f882187688e55e80b594068ad8827db5bc08020", "bytes": 14549, "licence_found": "MIT", "licence_source": "d2 repo mohammadtavakoli78/BEAM", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_100k_config": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "ef196c18d03d49ed589dfbb5b92cd842a29c39530c15580f06bf4d020632562d", "bytes": 1439, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_100k_summary": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "4207b31e1b7b5475356bacea30a49d207168d96701ff786ba96230235db01e40", "bytes": 2942, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_10m_routing": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "24a801aa0d28de4d28c8e390ccb9e821942fd65f09bd5ccbdad975e6cde57006", "bytes": 817, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_10m_routing_configs": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "7085fd3561cd7310813944e06ccd9c0feb4631ae1b35e0ac040db2f7089bacc1", "bytes": 9390, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_10m_summary": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "4d5392f9e20b10a28726b555740faf9ac63e2ce98781be7d07d639768360ed12", "bytes": 3057, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_aggregate_cross_run": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "8d1833678e8281ea55fa9a94d0645f69a4953766aefb06cec2b4d1e6b97648e2", "bytes": 5914, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_artifacts_readme": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "b33643d9cfbb964a2b7a39bac9771cb07b2279e2aad27d8487eb74a5869a6db7", "bytes": 400, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_beam_adapter": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "86feaad3ace89d0557b2af055e0e70757c7c9c9833dca11c35230f28a46d55ba", "bytes": 9111, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_beam_router": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "b0ee1af7dbf292acb066ece25959e1da6db41aec1e56c7982fa59ef37c5e0233", "bytes": 8008, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_beam_rubric": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "db226c038d24c27d3ae82f7c580113b94c246f05cb0115e6e050d8fdfddbfdf1", "bytes": 9470, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_compression": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "648dcb6519bbc710745b38ef02f691d32ba27004e91230db50f1845f773ece4d", "bytes": 20013, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_conversation_preprocessing": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "ee12c757f8bf805a9435d13a108950d6023c8869c7413e5f2b3a489b1008a378", "bytes": 30642, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_eval_adapter": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "62d69fadd633938c51f014cb8d251dc9975a674c46727cc314a5e9793e16f88f", "bytes": 4172, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_eval_registry": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "09b42e5468ae2137cbeddd2353a00496e3f02608a4fb5edf665ae5459f3abd77", "bytes": 3956, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_kendall_tau": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "f18ddc8a77eb788618fb6a02d606d524ba6fd05f3583b4d2bec04dd5acd22cb6", "bytes": 9143, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_loaders": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "9651628d0c18a09aa833c827c0c3d848687f1be19086362ee9b5f52123cf6fa7", "bytes": 3173, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_local_ingest": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "472b1a6198e10eeb9712cd90d5c9f6a74fdd73dbab49d582e151f5774ed532c9", "bytes": 21615, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_preprocess": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "2dd7b43a4057536ec19538636324e2054ee98558aeb4398da809dc40e65f73d5", "bytes": 40478, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_qa_abstention": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "90487f81172d34953cd213e7032e1c1b2669941bbcf742f0d0c821210132a41f", "bytes": 356, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_qa_contradiction_resolution": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "127e5ab066deac7e09ba72e82d749ed4abb67318e803f6c200dd264ba24ba4d1", "bytes": 389, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_qa_default": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "41eb561a9104fd756f333bf37a6e0d0f9fbd7c8806454350622846e03a5ad4ee", "bytes": 104, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_qa_event_ordering": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "ea7019908f29d09b5dc0c2ffbd589a11e4d752c399e360746e9319c9dc260087", "bytes": 361, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_qa_information_extraction": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "b646547f91d7610221b32a4663a1cde68a7cfc3ccebaefc0b8b7edeed6badf01", "bytes": 305, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_qa_instruction_following": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "720a4962d8727c67b98c75a6c71c3222a0502488c18eb0857650916a62d479de", "bytes": 411, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_qa_knowledge_update": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "8679bfd36b33877c50735c14c109bf4003d8a4018e07ced0b02a92f2267c730d", "bytes": 367, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_qa_multi_session_reasoning": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "2c5ed0d451b52a0ea468617854968380a12f7b4d9a7ca7aea9ba6b1a24155a06", "bytes": 365, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_qa_preference_following": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "e51df6b42ca84186b6c1e57a4fadf348adaeaee553476ebc039a188155a83b4a", "bytes": 334, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_qa_summarization": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "de25b0272ebd818484d184571dc12635d15cb5fcf44db532583e7c610f34f870", "bytes": 345, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_qa_temporal_reasoning": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "85d2a704582d7319324c221c606f96f5070121d10a91865fcf0bbb91f9a7ab69", "bytes": 264, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_report": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "34a77be18107d115cf98b89b56ab7be5888a57d2f43a29dc5dac9b69998dcd42", "bytes": 25233, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_run_beam_eval": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "351c7800eae4a5ab03c2e5b098fd24765cee33e7c787c804cb5c73f151b6d456", "bytes": 2704, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_run_sweep": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "fab0578b5aaf4049e5cadb59b930f7c62997b6f18ecaa2f92d020fb0859a5dc5", "bytes": 16453, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_session_io": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "9e0a53fd3c0e4f10ab01ac9f5106e850c72bf5a31a131c2b9009f6a45b7b538f", "bytes": 3000, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_sweep": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "9a0b62137f0ec1052a7021cf7df0c10bd0995c57981f45561a5385d4686794ed", "bytes": 8239, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "cognee_turn_compression_prompt": {"revision": "eb90d03740755f5252b8b12cce91fd09970f2d81", "sha256": "91f455bee8f305d9d2e0a1d241c6aebdf7585bd36ff7f6283c255b2a6b5d750b", "bytes": 2390, "licence_found": "Apache-2.0", "licence_source": "a7d1 repo topoteretes/cognee", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "lme_readme": {"revision": "9e0b455f4ef0e2ab8f2e582289761153549043fc", "sha256": "c4ff45676683d9e2f7cf7d9099d26426f14635ec110dbb1da818d1019a142573", "bytes": 15970, "licence_found": "MIT", "licence_source": "d1 repo xiaowu0162/LongMemEval", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "lme_run_generation_sh": {"revision": "9e0b455f4ef0e2ab8f2e582289761153549043fc", "sha256": "6602147b866eca4a80acdf5e6689389586086216c9198fce7b8380b7495c5422", "bytes": 3308, "licence_found": "MIT", "licence_source": "d1 repo xiaowu0162/LongMemEval", "from": "a7-github-2 g1 pin_fill fa5375b66785"},
    "mab_agent": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "41b1ccff3e8676cace8b3d23377e8d8468aab88dd34ebc0b1d3efe4a21282b60", "bytes": 55655, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "mab_conv": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "9a527e88c9791b37d378b59adac50d9308aac49e714f1d8ddaf3e14bb3f3bec5", "bytes": 12924, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "mab_init": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "86abdbd5442bcff6762a5f90cc45b8cf07bead9415f02aafaca036f2a8302ad7", "bytes": 14159, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "mab_main": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "f33b87cbdac4cb45f4e3510a08fecc53970bd03de9b8e14578219198c61aa5a7", "bytes": 8802, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "mab_readme": {"revision": "538026089d1a8a8eff05121d0db89b388f360eba", "sha256": "a71d5730d1ada9e0471cbb3e2cd2d36e343d751298a69e3d368af878753eab7f", "bytes": 6905, "licence_found": "MIT", "licence_source": "d1 repo HUST-AI-HYZ/MemoryAgentBench", "from": "a7-github g1 pin_fill acae9e52bc93"},
    "minilm_config": {"revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41", "sha256": "953f9c0d463486b10a6871cc2fd59f223b2c70184f49815e7efbcab5d8908b41", "bytes": 612, "licence_found": "apache-2.0", "licence_source": "a7d1 card sentence-transformers/all-MiniLM-L6-v2", "from": "a7-hf h1 pin_fill 449a89fca362"},
    "minilm_modules": {"revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41", "sha256": "84e40c8e006c9b1d6c122e02cba9b02458120b5fb0c87b746c41e0207cf642cf", "bytes": 349, "licence_found": "apache-2.0", "licence_source": "a7d1 card sentence-transformers/all-MiniLM-L6-v2", "from": "a7-hf h1 pin_fill 449a89fca362"},
    "minilm_pooling": {"revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41", "sha256": "4be450dde3b0273bb9787637cfbd28fe04a7ba6ab9d36ac48e92b11e350ffc23", "bytes": 190, "licence_found": "apache-2.0", "licence_source": "a7d1 card sentence-transformers/all-MiniLM-L6-v2", "from": "a7-hf h1 pin_fill 449a89fca362"},
    "minilm_safetensors": {"revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41", "sha256": "53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db", "bytes": 90868376, "licence_found": "apache-2.0", "licence_source": "a7d1 card sentence-transformers/all-MiniLM-L6-v2", "from": "a7-hf h1 pin_fill 449a89fca362"},
    "minilm_special_tokens": {"revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41", "sha256": "303df45a03609e4ead04bc3dc1536d0ab19b5358db685b6f3da123d05ec200e3", "bytes": 112, "licence_found": "apache-2.0", "licence_source": "a7d1 card sentence-transformers/all-MiniLM-L6-v2", "from": "a7-hf h1 pin_fill 449a89fca362"},
    "minilm_st_config": {"revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41", "sha256": "fc1993fde0a95c24ec6c022539d41cf6e2f7c9721e5415d6fb6897472a9cd4b7", "bytes": 53, "licence_found": "apache-2.0", "licence_source": "a7d1 card sentence-transformers/all-MiniLM-L6-v2", "from": "a7-hf h1 pin_fill 449a89fca362"},
    "minilm_st_model_config": {"revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41", "sha256": "061ca9d39661d6c6d6de5ba27f79a1cd5770ea247f8d46412a68a498dc5ac9f3", "bytes": 116, "licence_found": "apache-2.0", "licence_source": "a7d1 card sentence-transformers/all-MiniLM-L6-v2", "from": "a7-hf h1 pin_fill 449a89fca362"},
    "minilm_tokenizer": {"revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41", "sha256": "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037", "bytes": 466247, "licence_found": "apache-2.0", "licence_source": "a7d1 card sentence-transformers/all-MiniLM-L6-v2", "from": "a7-hf h1 pin_fill 449a89fca362"},
    "minilm_tokenizer_config": {"revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41", "sha256": "acb92769e8195aabd29b7b2137a9e6d6e25c476a4f15aa4355c233426c61576b", "bytes": 350, "licence_found": "apache-2.0", "licence_source": "a7d1 card sentence-transformers/all-MiniLM-L6-v2", "from": "a7-hf h1 pin_fill 449a89fca362"},
    "minilm_vocab": {"revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41", "sha256": "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3", "bytes": 231508, "licence_found": "apache-2.0", "licence_source": "a7d1 card sentence-transformers/all-MiniLM-L6-v2", "from": "a7-hf h1 pin_fill 449a89fca362"},
}
# <<< A7 FILLED
PINS_A7_DECLARED = __import__("copy").deepcopy(PINS_A7)
_apply_filled(PINS_A7, FILLED_A7)


def table_for(window: str | None) -> dict:
    """The table that holds ``window``'s pins: A7's windows (a7-*) PINS_A7, every other window PINS."""
    return PINS_A7 if isinstance(window, str) and window.startswith("a7-") else PINS


def pinned_twice(tables: tuple | None = None) -> list[str]:
    """Q-A7-P2-1 (1): a name in more than one table, or a file - (source, repository, commit, path) - of a later table
    already pinned under another name in an earlier one; each named. [] when every file is pinned once across them."""
    tables = tables if tables is not None else (PINS, PINS_A7)
    out, seen_names, seen_files = [], {}, {}
    for i, table in enumerate(tables):
        mine = {}
        for name, p in table.items():
            if name in seen_names:
                out.append(f"{name}: in table {seen_names[name]} and table {i}")
            key = (p["source"], p["repo"], p["revision"], p["path"])
            if key in seen_files and seen_files[key][0] != i:
                out.append(f"{name}: its file {p['repo']}@{str(p['revision'])[:12]}:{p['path']} is table "
                           f"{seen_files[key][0]}'s {seen_files[key][1]}")
            mine.setdefault(key, (i, name))
        for name in table:
            seen_names.setdefault(name, i)
        for key, v in mine.items():
            seen_files.setdefault(key, v)
    return out


def freeze_fragment(pins: dict | None = None) -> dict:
    """The FREEZE-V3 ``datasets`` shape: per role group, name -> {repo, path, revision, sha256, bytes, licence}."""
    groups = {"datasets": ("evaluation", "bracket", "smoke"), "tokenizers": ("tokenizer",),
              "prompt_files": ("prompt", "scoring"), "arm_sources": ("arm-source",),
              "licence_evidence": ("licence-evidence",)}
    out: dict = {g: {} for g in groups}
    for name, p in sorted((pins or PINS).items()):
        g = next(k for k, roles in groups.items() if p["role"] in roles)
        out[g][name] = {k: p[k] for k in ("repo", "path", "revision", "sha256", "bytes", "licence", "stands")}
        out[g][name]["stands"] = list(p["stands"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the v3 pin table")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    check_rules()
    check_rules(PINS_A7)
    twice = pinned_twice()
    if twice:
        raise PinRefused("a file is pinned once across the tables: " + "; ".join(twice))
    if args.list:
        for name, p in sorted({**PINS, **PINS_A7}.items()):
            state = p["sha256"][:12] if p["sha256"] else "unpinned"
            print(f"{name:26} {p['role']:10} {','.join(p['stands']):14} {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
