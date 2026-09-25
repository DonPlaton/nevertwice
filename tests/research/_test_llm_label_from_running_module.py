#!/usr/bin/env python3
"""(б) The `llm` an artifact names is the model the engine RAN, not the one the stand meant to set.

abstention_ab labelled its artifact from `sys.modules["memory_hook"]` - a second, bare engine
object imported before the stand pinned the model, which had inherited the owner's model from
`.secrets.env` - while the extraction ran on `api.m` with another model (campaign v2 erratum).
Four stands wrote `sb.LLM`, the name they set in the environment, which is the running model only
if the engine had not been imported earlier in the process: it binds the name at import.

`_provenance.running_llm()` reads the model off `nevertwice.api.m`, the object every stand captures
through; every stand that stamps an artifact carrying an `llm` label now takes it from there. With
a cloud backend and its key, the model that ran is the cloud one (`generate_json` tries it first).

    python tests/research/_test_llm_label_from_running_module.py
"""
from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "research"))

import _env_guard  # noqa: F401,E402
import _provenance as prov  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


print("\n- the label comes from the object that ran -")
saved = {k: sys.modules.get(k) for k in ("nevertwice.api", "memory_hook")}
try:
    api = types.ModuleType("nevertwice.api")
    api.m = types.SimpleNamespace(OLLAMA_MODEL="the-model-that-ran")
    sys.modules["nevertwice.api"] = api
    sys.modules["memory_hook"] = types.SimpleNamespace(OLLAMA_MODEL="a-stale-second-object")
    check("running_llm reads api.m, not a stale bare memory_hook and not the configured name",
          prov.running_llm("the-configured-name") == "the-model-that-ran", prov.running_llm("x"))

    #: G4 (auditor): generate_json sends extraction to the cloud first when a backend is configured
    #: and its key is present - the label must say so, from the backend's own model global.
    api.m = types.SimpleNamespace(OLLAMA_MODEL="qwen3-coder:30b", ACTIVE_CLOUD="deepseek",
                                  DEEPSEEK_MODEL="deepseek-v4-flash",
                                  _CLOUD_MODELS={"deepseek": "an-import-time-copy"},
                                  cloud_key=lambda: "present")
    check("a cloud backend with its key: the label is 'deepseek:deepseek-v4-flash'",
          prov.running_llm("the-configured-name") == "deepseek:deepseek-v4-flash", prov.running_llm("x"))
    api.m.cloud_key = lambda: ""
    check("the same backend configured but no key: extraction runs on Ollama, the label says 'qwen3-coder:30b'",
          prov.running_llm("the-configured-name") == "qwen3-coder:30b", prov.running_llm("x"))
    api.m.cloud_key, api.m.ACTIVE_CLOUD = (lambda: "present"), "none"
    check("cloud 'none' with a key lying around: still Ollama, 'qwen3-coder:30b'",
          prov.running_llm("the-configured-name") == "qwen3-coder:30b", prov.running_llm("x"))

    del sys.modules["nevertwice.api"]
    check("with no engine loaded (competitor arms only) it answers with the configured name",
          prov.running_llm("the-configured-name") == "the-configured-name")
finally:
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v

print("\n- no stand labels its artifact from a copy -")
stale, missing = [], []
for p in sorted((ROOT / "research").glob("*.py")):
    src = p.read_text(encoding="utf-8")
    rel = p.relative_to(ROOT).as_posix()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        # out["llm"] = <anything reading sys.modules.get("memory_hook")> - the erratum's shape
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and t.slice.value == "llm"
                for t in node.targets):
            if "memory_hook" in ast.unparse(node.value) and "running_llm" not in ast.unparse(node.value):
                stale.append(f"{rel}:{node.lineno}")
    labels_constant = any(isinstance(n, ast.Dict) and any(
        isinstance(k, ast.Constant) and k.value == "llm" and isinstance(v, (ast.Name, ast.Attribute))
        and ast.unparse(v).split(".")[-1] == "LLM" for k, v in zip(n.keys, n.values)) for n in ast.walk(tree))
    captures_engine = "from nevertwice import api" in src or "import api" in src
    if labels_constant and captures_engine and "prov.stamp(" in src and "running_llm(" not in src:
        missing.append(rel)
check("no artifact's llm label is read off sys.modules['memory_hook']", not stale, str(stale))
check("every engine stand that labels with its configured LLM re-reads the running model before stamping",
      not missing, str(missing))
fixed = ["research/abstention_ab.py", "research/supersession_bench.py", "research/asof_bench.py",
         "research/facts_dilution_probe.py", "research/silence_probe.py"]
check("the five stands named in the erratum call running_llm",
      all("running_llm(" in (ROOT / f).read_text(encoding="utf-8") for f in fixed),
      str([f for f in fixed if "running_llm(" not in (ROOT / f).read_text(encoding="utf-8")]))

print(f"\nllm label from running module: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
