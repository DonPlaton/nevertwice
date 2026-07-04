#!/usr/bin/env python3
"""Regression tests for the audit fixes — the LLM-output -> disk path that the
original suite never covered (audit C6). Mocks call_ollama and asserts on the
files actually written to a throwaway vault.

    python _test_memory_v2.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import memory_hook as m

# Shipped default has no configured project root (git-detection only); pin an
# OS-appropriate one so the process_session fixtures are tracked on any platform.
import os
_ROOT = r"D:\Projects" if os.name == "nt" else "/projects"
m.PROJECT_ROOTS = [_ROOT]
m._ROOTS_NORM = [m._norm_path(_ROOT)]

P = F = 0


def check(name, cond):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}")


print("# slug / translit hardening")
check("cyrillic title -> ascii", m.slugify("Итеративное Улучшение").isascii())
check("translit content", m.slugify("кодирование") == "kodirovanie")
check("slug_project '..' -> general", m.slug_project("..") == "general")
check("slug_project 'CON' -> project_con", m.slug_project("CON") == "project_con")
check("slug_project '' -> general", m.slug_project("") == "general")

print("# prune_processed_db tolerates a corrupt (non-dict) entry (F6)")
m.save_processed = lambda db: None  # don't touch the real DB
_db = {"a": {"processed_at": "2000-01-01T00:00:00"}, "b": "corrupt-string", "c": None}
pruned = m.prune_processed_db(_db, days=1)
check("did not raise + dropped bad entries", "b" not in _db and "c" not in _db)

print("# _split_context round-trips a compacted file")
sample = ("---\np: x\n---\n\n# Context: x\n\nintro\n\n---\n\n"
          "## 2026-05-01 10:00\nu1\n\n## 2026-05-02 11:00\nu2\n")
head, entries = m._split_context(sample)
check("head keeps frontmatter+intro", head.startswith("---") and "intro" in head)
check("two entries parsed", len(entries) == 2)

print("# write_atomic is crash-safe (temp+replace) and writes content")
with tempfile.TemporaryDirectory() as td:
    fp = Path(td) / "x.md"
    m.write_atomic(fp, "héllo \U0001f9e0")
    check("atomic write content", fp.read_text(encoding="utf-8") == "héllo \U0001f9e0")
    check("no leftover .tmp", not (Path(td) / "x.md.tmp").exists())

print("# FULL pipeline: mocked Ollama -> disk (C3 + F5 + F40 in one)")
with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)
    m.collect_existing_tags.cache_clear()
    m.collect_existing_titles.cache_clear()
    m.update_embeddings = lambda notes: None        # no Ollama embed in test
    m.generate_json = lambda prompt, project=None: {  # intercept BOTH backends
        "project": "..",                              # path-traversal attempt (C3)
        "patterns": [{"title": "Итеративное Улучшение", "description": "d"}],  # cyrillic (F40)
        "mistakes": [{"title": "m1", "description": "x", "prevention": "do y"}],
        "decisions": [],
        "tags": "pytorch, cuda",                      # STRING, not a list (F5)
        "session_summary": "s",
        "context_update": "ctx update",
    }
    tp = Path(td) / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"content": "hi"},
                              "cwd": os.path.join(_ROOT, "testproj"),
                              "timestamp": "2026-06-01T10:00:00"}) + "\n",
                  encoding="utf-8")
    ok = m.process_session("sid12345", os.path.join(_ROOT, "testproj"), str(tp), "test", {})
    check("process_session succeeded", ok)
    ctx = list((Path(td) / "Context").glob("*.md"))
    check("C3: project '..' neutralized (general.md, no '..')",
          bool(ctx) and all(".." not in f.name for f in ctx)
          and any(f.stem == "general" for f in ctx))
    pats = list((Path(td) / "Patterns").glob("*.md"))
    check("F40: cyrillic title -> ASCII filename",
          bool(pats) and all(p.stem.isascii() for p in pats))
    ptext = pats[0].read_text(encoding="utf-8") if pats else ""
    check("F5: tag-string did NOT become per-char body tags",
          "#p #y #t" not in ptext and "tags: []" in ptext)
    mist = list((Path(td) / "Mistakes").glob("*.md"))
    check("F46: prevention rendered in mistake note",
          bool(mist) and "Как избежать" in mist[0].read_text(encoding="utf-8"))

print()
print(f"v2: {P} passed, {F} failed")
sys.exit(1 if F else 0)
