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
from pathlib import Path

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


def _at(pin: dict, revision: str, path: str | None = None, *, record: str = "d1") -> dict:
    """A pin whose revision (and path) a discovery record declares (d1 or d2)."""
    rec_sha = DISCOVERY_D1 if record == "d1" else DISCOVERY_D2
    pin.update(revision=revision, revision_from=f"a3-discovery {record} {rec_sha[:12]}")
    if path is not None:
        pin["path"] = path
    return pin


LME = "xiaowu0162/longmemeval-cleaned"
BEAM, MABD, AMAD, BGE = "Mohammadta/BEAM", "ai-hyz/MemoryAgentBench", "AMA-bench/AMA-bench", "BAAI/bge-m3"
GH_LME, GH_LOCOMO, GH_MAB = "xiaowu0162/LongMemEval", "snap-research/locomo", "HUST-AI-HYZ/MemoryAgentBench"
GH_MEM0, GH_AMA, GH_BEAM = "mem0ai/mem0", "AMA-Bench/AMA-Bench", "mohammadtavakoli78/BEAM"
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
        if p["source"] != "local-v2" and p["path"] and not p["path"].startswith("https://") and Path(p["path"]).is_absolute():
            raise PinRefused(f"{name}: a pin names a repository path, never an absolute one")


def fill(name: str, *, revision: str, sha256: str, size: int, licence_found: str | None, path: str | None = None,
         pins: dict | None = None) -> dict:
    """Fill a pin once, from a verified fetch record. A second fill, or a found licence that differs from the declared
    one, is refused."""
    table = pins if pins is not None else PINS
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
    <pins_root>/git/<commit>. A local-v2 pin has its own path."""
    p = (pins or PINS)[name]
    if p["source"] == "local-v2":
        return REPO / p["path"]
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


def _apply_filled(pins: dict | None = None) -> None:
    """FILLED into the table through fill() itself, so its rules hold at every import: a 64-hex sha256, the licence
    found against the declared one (P6), a pin filled once."""
    for name, v in FILLED.items():
        fill(name, revision=v["revision"], sha256=v["sha256"], size=v["bytes"], licence_found=v["licence_found"], pins=pins)
        (pins or PINS)[name]["filled_from"] = v["from"]


#: The table as declared, before FILLED - the rule tests fill and verify copies of this one.
PINS_DECLARED = __import__("copy").deepcopy(PINS)
_apply_filled()


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
    if args.list:
        for name, p in sorted(PINS.items()):
            state = p["sha256"][:12] if p["sha256"] else "unpinned"
            print(f"{name:26} {p['role']:10} {','.join(p['stands']):14} {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
