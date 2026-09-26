#!/usr/bin/env python3
"""(б) b-c: every script the register names as an artifact's writer stamps `measured_at` on it.

Restore #2 had to date twelve unstamped artifacts by their file mtime against the campaign's STATUS
log (the auditor's stamp gate): serving_check, heldout baseline_v1, guards_pack, longmem, locomo,
k8_step0 and others wrote no `measured_at`, so nothing in the file said which commit produced it
or when. `research/_provenance.stamp` writes `{commit, utc, dirty}`; this suite asks the class
question - for every claim with a `raw` artifact, does the script its `command` runs call it? - so
the next stand is covered on the day it is registered, not when a restore trips over it.

Exempt, each with its reason: the tools that read the owner's live store, which stage D does not run
(LOCAL-TASK-D §3.2), and one derived tool whose provenance is its input closure. The product's own
`guards pack --count` is run through `research/guards_pack.py`, which stamps what it wrote (G5). A DETERMINISTIC artifact is stamped too; `reproduce.py`
compares artifacts with `measured_at` stripped, because it describes the run, not the result.

    python tests/research/_test_every_writer_stamps.py
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "research"))

import _env_guard  # noqa: F401,E402
import reproduce as R  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


EXEMPT = {
    "research/lexical_morphology_probe.py": "reads the owner's live store - not run in stage D (§3.2)",
    "research/k8_vault_dryrun.py": "reads the owner's live store - not run in stage D (§3.2)",
    "tools/draw_divergence.py": "a derived tool over committed artifacts, reproduced byte for byte; its "
                                "provenance is its input closure ((б) b-b, which must land before the "
                                "campaign-v3 anchor), not a clock",
}


def script_of(command: str) -> str | None:
    toks = command.split()
    if "-m" in toks:
        mod = toks[toks.index("-m") + 1]
        return mod.replace(".", "/") + ".py"
    return next((t.replace("\\", "/") for t in toks if t.endswith(".py")), None)


def stamps(path: Path) -> bool:
    """A call to `<x>.stamp(...)`/`stamp(...)` (the whole artifact) or `<x>.measured_at()` (per row -
    head_to_head merges rows across runs, so each row carries its own) - `_provenance`'s two doors."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    #: the names `_provenance` goes by in this file (`prov`, `pv`, ...), and its functions imported bare
    mods = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
            for a in n.names if a.name == "_provenance"}
    bare = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
            and n.module == "_provenance" for a in n.names if a.name in ("stamp", "measured_at")}
    return any(isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Attribute) and n.func.attr in ("stamp", "measured_at")
         and ast.unparse(n.func.value) in mods)
        or (isinstance(n.func, ast.Name) and n.func.id in bare)) for n in ast.walk(tree))


manifest = json.loads((ROOT / "research" / "evidence_manifest.json").read_text(encoding="utf-8"))
writers: dict[str, int] = {}
for c in manifest["claims"]:
    if c.get("raw") and c.get("command"):
        s = script_of(c["command"])
        if s:
            writers[s] = writers.get(s, 0) + 1

print("\n- every register writer stamps its artifact -")
check("there are writers to ask", len(writers) >= 20, str(len(writers)))
missing = {s: n for s, n in sorted(writers.items())
           if s not in EXEMPT and (ROOT / s).exists() and not stamps(ROOT / s)}
check("no writer the register names leaves its artifact unstamped", not missing,
      "; ".join(f"{s} ({n} claims)" for s, n in missing.items()))
check("every exemption is a writer the register actually names (none outlives its script)",
      set(EXEMPT) <= set(writers), str(sorted(set(EXEMPT) - set(writers))))
check("the ones restore #2 had to date by mtime are stamped now",
      all(stamps(ROOT / s) for s in ("research/embed_universal/serving_check.py",
                                     "research/embed_universal/heldout_eval.py", "research/longmem_eval.py",
                                     "research/locomo_eval.py", "research/k8_skeleton.py",
                                     "research/guards_pack.py",
                                     "research/abstention_ab.py")))

print("\n- reproduction compares results, not the run's own stamp -")
import tempfile  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    a, b = Path(td) / "a.json", Path(td) / "b.json"
    a.write_text(json.dumps({"x": 1, "measured_at": {"commit": "a", "utc": "2026-09-26T00:00:00Z"}}), encoding="utf-8")
    b.write_text(json.dumps({"x": 1, "measured_at": {"commit": "b", "utc": "2026-09-27T00:00:00Z"}}), encoding="utf-8")
    check("two runs of a deterministic artifact that differ only in measured_at canonicalise equal",
          R.canonical(a, []) == R.canonical(b, []))
    b.write_text(json.dumps({"x": 2, "measured_at": {"commit": "a", "utc": "2026-09-26T00:00:00Z"}}), encoding="utf-8")
    check("while a changed result still differs", R.canonical(a, []) != R.canonical(b, []))

print(f"\nevery writer stamps: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
