#!/usr/bin/env python3
"""(б) b-h: the guard that a run's rows name its model compares a TOKEN exactly, not a substring.

K47 (campaign v2): head_to_head fell back to its default model because the runner did not export
H2H_LLM; the guard that caught it tested `llm in row["mode"]`, and `qwen2.5-7b` is a substring of
`qwen2.5-7b-64k:latest`. `tools/check_llm_rows.py` reads the row's `llm` field (written by
head_to_head since stage D) or, for an older row, the tag-shaped tokens of its `mode` sentence, and
accepts exactly one model equal to the expected tag.

    python tests/_test_check_llm_rows.py
"""
from __future__ import annotations

import ast
import contextlib
import io
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

import _env_guard  # noqa: F401,E402
import check_llm_rows as cl  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


TAG = "qwen2.5-7b-64k:latest"
#: The three `mode` sentences head_to_head writes, verbatim from research/results/head_to_head_v2.json.
MEM0 = f"infer=True (LLM {TAG})"
LANGMEM = (f"full pipeline: create_memory_store_manager with {TAG} (num_ctx 16384); "
           "store search over the extracted memories")
AMEM = (f"full pipeline: agentic_memory with {TAG} via litellm (Ollama's own context default); "
        "search_agentic over its notes")

print("\n- the K47 near miss is a mismatch, on the field and on the sentence -")
for label, row in (("the llm field", {"llm": TAG}), ("mem0's sentence", {"mode": MEM0}),
                   ("langmem's sentence", {"mode": LANGMEM}), ("amem's sentence", {"mode": AMEM})):
    rows = {"arm": row}
    check(f"{label}: the exact tag matches", cl.mismatches(rows, TAG, ["arm"]) == {},
          str(cl.mismatches(rows, TAG, ["arm"])))
    check(f"{label}: 'qwen2.5-7b' - a prefix of the tag - does NOT (the substring guard passed it)",
          "arm" in cl.mismatches(rows, "qwen2.5-7b", ["arm"]))
    check(f"{label}: 'qwen2.5' and '7b' do not either",
          "arm" in cl.mismatches(rows, "qwen2.5", ["arm"]) and "arm" in cl.mismatches(rows, "7b", ["arm"]))

print("\n- a row the check cannot vouch for is a mismatch, not a pass -")
check("a row that names no model (mem0 retrieval-only) is a mismatch",
      "a" in cl.mismatches({"a": {"mode": "infer=False (retrieval-only, 1 memory/session)"}}, TAG, ["a"]))
check("a row that names two models is a mismatch",
      "a" in cl.mismatches({"a": {"mode": f"judge qwen3:8b with {TAG}"}}, TAG, ["a"]))
check("a missing row is a mismatch", cl.mismatches({}, TAG, ["a"]) == {"a": "no row"})
check("a blocked row carries no numbers to mislabel and is skipped",
      cl.mismatches({"a": {"blocked": "import failed"}}, TAG, ["a"]) == {})
check("the llm field wins over the sentence (the field is what head_to_head writes now)",
      cl.models_named({"llm": "qwen3-coder:30b", "mode": MEM0}) == ["qwen3-coder:30b"])

print("\n- on the committed artifact and through the command line -")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc_ok = cl.main(["research/results/head_to_head_v2.json", TAG, "--arms", "mem0_infer,langmem_full,amem_full"])
check("the committed h2h rows name exactly the campaign's model: exit 0", rc_ok == 0, buf.getvalue())
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc_bad = cl.main(["research/results/head_to_head_v2.json", "qwen2.5-7b", "--arms", "mem0_infer,langmem_full,amem_full"])
check("the same rows against the prefix: exit 1, every arm printed",
      rc_bad == 1 and buf.getvalue().count("MISMATCH") == 3, buf.getvalue())

print("\n- head_to_head writes the field for every LLM arm -")
#: Every function that describes its arm's pipeline with COMP_LLM in `sc["mode"]` must also set
#: `sc["llm"]`, or its rows fall back to the sentence the substring guard was fooled by.
tree = ast.parse((ROOT / "research" / "head_to_head.py").read_text(encoding="utf-8"))
llm_arms, missing = [], []
for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
    keys = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) \
                        and ast.unparse(t.value) == "sc":
                    keys[t.slice.value] = ast.unparse(node.value)
    if "COMP_LLM" in keys.get("mode", ""):
        llm_arms.append(fn.name)
        if "llm" not in keys:
            missing.append(fn.name)
check("the three LLM arms are found (mem0, langmem full, a-mem full)", len(llm_arms) == 3, str(llm_arms))
check("each of them sets sc['llm']", not missing, str(missing))

print(f"\ncheck llm rows: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
