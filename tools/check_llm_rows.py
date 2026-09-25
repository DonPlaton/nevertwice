#!/usr/bin/env python3
"""Do the rows a run would overwrite (or just wrote) name EXACTLY the model it runs?

    python tools/check_llm_rows.py research/results/head_to_head_v2.json qwen2.5-7b-64k:latest \\
        --arms mem0_infer,langmem_full,amem_full [--rev <commit>]

K47 (campaign v2, 2026-09-25): the runner did not export H2H_LLM, head_to_head fell back to its
default qwen2.5:3b, and the rows it was about to overwrite named qwen2.5-7b-64k:latest - two models
in one table. The runner's guard that caught it asked `llm in row["mode"]`, a SUBSTRING test on a
free-text sentence: `qwen2.5-7b` passes against `qwen2.5-7b-64k:latest`, and so would `qwen2.5`
or `7b`. (б), stage D: the comparison is exact, on a token.

A row's model is its `llm` field when it has one (head_to_head writes it since stage D). An older
row has only the sentence in `mode`; there the model is every whitespace/paren/comma-delimited
token that looks like a model tag, and the row matches only if exactly one such token equals the
expected tag. A row naming no model, or naming two, is a mismatch - the check cannot vouch for it.

Exit 0 when every named arm matches; 1 otherwise (each mismatch printed); 2 on a usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: A model tag in free text: name[:tag] of letters, digits and .-_/ - `qwen2.5-7b-64k:latest`,
#: `qwen3-coder:30b`, `deepseek:deepseek-v4-flash`. Split on whitespace, parentheses, commas,
#: semicolons; a token counts only if it carries a digit or a `:tag` (every model tag does).
_TOKEN_SPLIT = re.compile(r"[\s(),;]+")
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*(:[A-Za-z0-9._-]+)?$")


def models_named(row: dict) -> list[str]:
    """The model tag(s) a row names: its `llm` field, else the tag-shaped tokens of its `mode`."""
    if "llm" in row:
        return [row["llm"]] if row["llm"] else []
    mode = str(row.get("mode") or "")
    out = []
    for tok in _TOKEN_SPLIT.split(mode):
        tok = tok.strip(".")
        if not tok or tok.isdigit() or not _TAG.match(tok):
            continue
        if not any(ch.isdigit() for ch in tok) and ":" not in tok:
            continue                                  # a plain word: every model tag carries a size or a tag
        out.append(tok)
    return out


def mismatches(rows: dict, expected: str, arms: list[str]) -> dict[str, str]:
    """{arm: why} for every arm whose row does not name exactly `expected`."""
    bad = {}
    for arm in arms:
        row = rows.get(arm)
        if not isinstance(row, dict):
            bad[arm] = "no row"
            continue
        if "blocked" in row:
            continue                                  # a blocked row carries no numbers to mislabel
        named = models_named(row)
        if named != [expected]:
            bad[arm] = f"names {named or 'no model'}, expected exactly {expected!r}"
    return bad


def _load(path: str, rev: str | None) -> dict:
    if rev:
        out = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", check=True).stdout
        return json.loads(out)
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("artifact", help="repo-relative JSON path")
    ap.add_argument("expected", help="the model tag the run uses, compared exactly")
    ap.add_argument("--arms", required=True, help="comma-separated row keys to check")
    ap.add_argument("--rev", default=None, help="read the artifact at this commit instead of the tree")
    a = ap.parse_args(argv)
    arms = [x for x in a.arms.split(",") if x]
    if not arms:
        return 2
    bad = mismatches(_load(a.artifact, a.rev), a.expected, arms)
    where = f"{a.artifact}@{a.rev[:7]}" if a.rev else a.artifact
    for arm, why in bad.items():
        print(f"MISMATCH {where} {arm}: {why}")
    print(f"{where}: {len(arms) - len(bad)}/{len(arms)} row(s) name exactly {a.expected!r}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
