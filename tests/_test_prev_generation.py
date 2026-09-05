#!/usr/bin/env python3
"""The rollback generation never reaches git, and the embeddings cache never gets one.

`store_state` writes `<file>.prev` beside every JSON state file so a destructive re-mine
can be undone. The review of 2026-09-05 found the copy of the 90 MB embeddings cache
tracked by the live vault's git - `*.prev` was in no ignore list - six commits deep at
97 MB each, one commit away from GitHub's hard limit; and found each save reading the
primary and writing a third copy under the vault lock for a file a rebuild regenerates.
"""
import _env_guard  # noqa: F401
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


print("\n- a state file keeps a rollback generation; a cache does not -")
state = m.VAULT / "_probe_state.json"
prev = state.with_name(state.name + ".prev")
m._save_json_generations(state, "one")
m._save_json_generations(state, "two")
check("the second write keeps the first as .prev", prev.exists() and prev.read_text(encoding="utf-8") == "one")
m._save_json_generations(state, "three", prev=False)
check("prev=False leaves the older generation alone", prev.read_text(encoding="utf-8") == "one")
for f in (state, prev, state.with_name(state.name + ".bak")):
    f.unlink(missing_ok=True)

cache_prev = m.EMBED_CACHE.with_name(m.EMBED_CACHE.name + ".prev")
cache_prev.unlink(missing_ok=True)
m.save_embed_cache({"a": {"vec": [1.0]}})
m.save_embed_cache({"a": {"vec": [2.0]}})
check("the embeddings cache never writes a .prev", not cache_prev.exists())
check("but its primary and .bak are written",
      m.EMBED_CACHE.exists() and m.EMBED_CACHE.with_name(m.EMBED_CACHE.name + ".bak").exists())

print("\n- every ignore list knows the suffix -")
check("the engine's vault ignore list has *.prev", "*.prev" in m._VAULT_GITIGNORE)
tree = ast.parse((ROOT / "install.py").read_text(encoding="utf-8"))
lines = None
for node in tree.body:
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_GITIGNORE_LINES" for t in node.targets):
        lines = ast.literal_eval(node.value)
check("install.py's list has *.prev", isinstance(lines, list) and "*.prev" in lines, str(lines)[:80])

print("\n- an existing vault .gitignore is reconciled -")
gi = m.VAULT / ".gitignore"
gi.write_text("*.bak\n.embeddings_cache.json\n", encoding="utf-8")
m._ensure_vault_gitignore()
check("the reconciliation adds *.prev", "*.prev" in gi.read_text(encoding="utf-8").splitlines())

print(f"\nprev generation: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
