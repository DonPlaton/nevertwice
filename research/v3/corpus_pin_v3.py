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

ROLES = ("evaluation", "bracket", "smoke", "tokenizer", "prompt", "scoring", "arm-source")
SOURCES = ("hf-dataset", "hf-model", "url", "github", "git", "local-v2")
#: The licences the declared pins may carry (§3.1). Anything with ND is refused wherever it appears.
LICENCES = frozenset({"MIT", "Apache-2.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-NC-4.0", "BSD-3-Clause",
                      "CC-BY-SA-4.0 (data); MIT (code)"})
_ND = re.compile(r"(?i)(\bND\b|no[-\s]?deriv)")


def _pin(role, stands, source, repo, path, licence, window, prereg, note="", cross_check=None):
    return {"role": role, "stands": tuple(stands), "source": source, "repo": repo, "path": path,
            "revision": None, "sha256": None, "bytes": None, "licence": licence, "licence_found": None,
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

LME = "xiaowu0162/longmemeval-cleaned"
PINS: dict[str, dict] = {
    # ── evaluation and bracket data (§3.1, lines 814-823) ──
    "lme_s_cleaned": _pin("evaluation", ["S1"], "hf-dataset", LME, "longmemeval_s_cleaned.json", "MIT", "a3-hf", 814),
    "lme_m_cleaned": _pin("evaluation", ["S2"], "hf-dataset", LME, "longmemeval_m_cleaned.json", "MIT", "a3-hf", 815),
    "lme_oracle_cleaned": _pin("bracket", ["S3"], "hf-dataset", LME, "longmemeval_oracle.json", "MIT", "a3-hf", 816,
                               note="one pin if byte-identical to the v2 longmemeval_oracle pin (821a2034)"),
    "beam_128k": _pin("evaluation", ["S5"], "hf-dataset", "Mohammadta/BEAM", None, "CC-BY-SA-4.0 (data); MIT (code)",
                      "a3-hf", 818, note="the 128K split (README 128K, card 100K); path and HF config name at discovery"),
    "mab_conflict_resolution": _pin("evaluation", ["S6", "S6L"], "hf-dataset", "ai-hyz/MemoryAgentBench", None, "MIT",
                                    "a3-hf", 819, note="Conflict_Resolution: FC-SH and FC-MH x 6K/32K/64K/262K"),
    "ama_swe": _pin("evaluation", ["S7"], "hf-dataset", "AMA-bench/AMA-bench", None, "MIT", "a3-hf", 821,
                    note="the SWE domain; 34 or 36 trajectories and 432 questions, confirmed at fetch"),
    # ── smoke data, outside every scored list (§9.4, lines 1801-1806) ──
    "beam_500k": _pin("smoke", ["S5-smoke"], "hf-dataset", "Mohammadta/BEAM", None, "CC-BY-SA-4.0 (data); MIT (code)",
                      "a3-hf", 1801, note="the 500K split, whole file; its first conversation, cut at 128K cl100k tokens"),
    "mab_accurate_retrieval": _pin("smoke", ["S6-smoke"], "hf-dataset", "ai-hyz/MemoryAgentBench", None, "MIT", "a3-hf",
                                   1803, note="the Accurate_Retrieval row closest to 32K cl100k tokens"),
    "ama_non_swe": _pin("smoke", ["S7-smoke"], "hf-dataset", "AMA-bench/AMA-bench", None, "MIT", "a3-hf", 1805,
                        note="non-SWE domains; the trajectory closest to the SWE median character length"),
    # ── tokenizers (§5.1 line 1302, §5.2 line 1325) ──
    "bge_m3_tokenizer": _pin("tokenizer", ["all"], "hf-model", "BAAI/bge-m3", None, "MIT", "a3-hf", 1302,
                             note="the tokenizer files only (tokenizer.json, sentencepiece.bpe.model, configs)"),
    "tiktoken_cl100k_base": _pin("tokenizer", ["all"], "url", None,
                                 "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken", "MIT",
                                 "a3-tiktoken", 1325,
                                 cross_check={"sha256": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
                                              "source": "tiktoken's openai_public.py expected_hash [to confirm at fetch]"}),
    # ── official prompts and scoring files: pinned by sha only, never committed (Q-A3-6; lines 1557-1563, 1607-1612) ──
    "lme_evaluate_qa": _pin("scoring", ["S1", "S2", "S3"], "github", None, None, "MIT", "a3-github", 1608,
                            note="LongMemEval evaluate_qa.py per-type judge prompts, at a pinned commit; repo from discovery"),
    "lme_answer_prompt": _pin("prompt", ["S1", "S2", "S3"], "github", None, None, "MIT", "a3-github", 1557,
                              note="the benchmark's own answer prompt; repo and path from discovery"),
    "locomo_j_prompt": _pin("scoring", ["S4"], "github", None, None, None, "a3-github", 1609,
                            note="the Mem0-paper J prompt; repo, path and licence from discovery"),
    "locomo_answer_prompt": _pin("prompt", ["S4"], "github", None, None, "CC-BY-NC-4.0", "a3-github", 1557,
                                 note="LoCoMo's answer prompt with the cat-5 'No information available' instruction"),
    "beam_scoring": _pin("scoring", ["S5"], "github", None, None, "CC-BY-SA-4.0 (data); MIT (code)", "a3-github", 818,
                         note="the official scoring code and nugget rubric; repo from the HF card at discovery"),
    "mab_fc_files": _pin("scoring", ["S6", "S6L"], "github", None, None, "MIT", "a3-github", 819,
                         note="the FC template, the exact-match scorer and the 512-token chunker (and its tokenizer)"),
    "ama_judge_prompt": _pin("scoring", ["S7"], "github", None, None, "MIT", "a3-github", 1611,
                             note="the AMA-Bench judge prompt; repo from the HF card at discovery"),
    # ── arm source (lines 229, 2306) ──
    "amem_source": _pin("arm-source", ["arm:a-mem"], "git", "agiresearch/A-mem", None, None, "a3-git", 229,
                        note="the upstream commit fixed at fetch; licence from the repository at that commit"),
    # ── v2 pins reused by value (S4, S9) ──
    "locomo10": {**_pin("evaluation", ["S4"], "local-v2", "snap-research/locomo", V2_PINS["locomo10"]["path"],
                        "CC-BY-NC-4.0", None, 817), "sha256": V2_PINS["locomo10"]["sha256"],
                 "bytes": V2_PINS["locomo10"]["bytes"], "revision": "v2-pin"},
    "longmemeval_s": {**_pin("evaluation", ["S9"], "local-v2", "xiaowu0162/longmemeval",
                             V2_PINS["longmemeval_s"]["path"], "MIT", None, 823),
                      "sha256": V2_PINS["longmemeval_s"]["sha256"], "bytes": V2_PINS["longmemeval_s"]["bytes"],
                      "revision": "v2-pin"},
}


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
    if p["licence"] is not None and licence_found is not None and licence_found != p["licence"]:
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


def freeze_fragment(pins: dict | None = None) -> dict:
    """The FREEZE-V3 ``datasets`` shape: per role group, name -> {repo, path, revision, sha256, bytes, licence}."""
    groups = {"datasets": ("evaluation", "bracket", "smoke"), "tokenizers": ("tokenizer",),
              "prompt_files": ("prompt", "scoring"), "arm_sources": ("arm-source",)}
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
