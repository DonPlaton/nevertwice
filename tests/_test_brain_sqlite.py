#!/usr/bin/env python3
"""F4 - SQLite entity/relation graph scale-index.

Proves the SQLite fast path returns results IDENTICAL to the markdown scan (the source of
truth), that incremental upsert/delete keep it in sync, and that it stays fast as the store
grows. The graph index is self-sufficient (its rows carry project/date), so it works without
any embeddings - these tests build it via reindex_graph(), no embed cache needed.

    python _test_brain_sqlite.py
"""
import os
import sys
import time
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
os.environ["NEVERTWICE_PROFILE"] = "research"
import memory_hook as m          # noqa: E402
import index_sqlite as sx        # noqa: E402

m.git_autocommit = lambda *a, **k: None

P = F = 0


def check(name, cond):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}")


with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)

    def W(folder, item, proj, date, nt):
        return m.write_typed_note(folder, item, proj, date, [], nt)

    W("Mistakes", {"title": "ResNet OOM", "description": "vram", "entities": ["resnet", "cuda"],
      "entity_types": {"resnet": "method"},
      "relations": [{"rel": "fixed-by", "target": "checkpointing"}]},
      "vision", "2026-05-26", "mistake")
    W("Patterns", {"title": "ResNet scaling", "entities": ["resnet", "scaling"],
      "entity_types": {"resnet": "method"}}, "speech", "2026-06-12", "pattern")
    W("Decisions", {"title": "Use ImageNet", "entities": ["imagenet", "cuda"],
      "entity_types": {"imagenet": "dataset"}}, "vision", "2026-06-01", "decision")

    # ── markdown baseline (no index yet) ────────────────────────────────────────
    print("markdown baseline (no index)")
    check("no index file → graph falls back to markdown", not sx.graph_index_ready())
    md_etypes = dict(m.entity_types_index())
    md_methods = list(m.entities_by_type("method"))
    md_resnet = sorted(n["stem"] for n in m.notes_for_entity("resnet"))
    md_cooc = dict(m.co_occurring("cuda"))
    md_rel = m.related_by("resnet")
    check("markdown etype index correct", md_etypes == {"resnet": "method", "imagenet": "dataset"})

    # ── build the SQLite graph index ────────────────────────────────────────────
    print("build graph index")
    rows = sx.reindex_graph()
    check("reindex_graph wrote rows", rows > 0)
    check("graph_index_ready() now True", sx.graph_index_ready())

    # ── parity: the SQLite path returns IDENTICAL results ───────────────────────
    print("parity (SQLite == markdown)")
    check("etype-index parity", dict(m.entity_types_index()) == md_etypes)
    check("entities_by_type parity", list(m.entities_by_type("method")) == md_methods == ["resnet"])
    check("notes_for_entity parity (same stems)",
          sorted(n["stem"] for n in m.notes_for_entity("resnet")) == md_resnet and len(md_resnet) == 2)
    check("co_occurring parity", dict(m.co_occurring("cuda")) == md_cooc)
    check("related_by parity", m.related_by("resnet") == md_rel
          and md_rel == [{"rel": "fixed-by", "target": "checkpointing", "notes": 1}])
    check("project scoping in SQL (cuda absent from speech)",
          m.notes_for_entity("cuda", "speech") == [])
    check("sql helper used directly returns data", sx.sql_etype_index().get("resnet") == "method")

    # ── incremental upsert + delete ─────────────────────────────────────────────
    print("incremental maintenance")
    new = W("Patterns", {"title": "LoRA finetune", "entities": ["lora"],
            "entity_types": {"lora": "method"}}, "speech", "2026-06-15", "pattern")
    sx.upsert_graph([new])
    check("upsert_graph adds the new typed entity", "lora" in sx.sql_etype_index())
    check("new entity queryable through the graph API", "lora" in m.entities_by_type("method"))
    sx.delete([new])
    check("delete prunes the graph rows", "lora" not in sx.sql_etype_index())

    # ── scale smoke-test (5000 synthetic notes) ─────────────────────────────────
    print("scale (5000 synthetic notes)")
    big = [{"stem": f"2026-06-01-proj{i % 10}-pattern-syn-{i}", "project": f"proj{i % 10}",
            "date": "2026-06-01", "entities": [f"ent-{i}", "common"],
            "relations": [{"rel": "uses", "target": f"tool-{i % 50}"}],
            "entity_types": ({f"ent-{i}": "method"} if i % 3 == 0 else {})} for i in range(5000)]
    sx.reindex_graph(big)
    t0 = time.perf_counter(); ti = m.entity_types_index(); dt_e = time.perf_counter() - t0
    t0 = time.perf_counter(); co = m.co_occurring("common", k=10); dt_c = time.perf_counter() - t0
    t0 = time.perf_counter(); rb = m.related_by("common", k=10); dt_r = time.perf_counter() - t0
    check(f"entity_types_index fast at scale ({dt_e * 1000:.0f}ms, {len(ti)} typed)",
          dt_e < 1.0 and len(ti) > 1000)
    check(f"co_occurring fast on a 5000-note entity ({dt_c * 1000:.0f}ms)", dt_c < 1.0 and len(co) == 10)
    check(f"related_by fast at scale ({dt_r * 1000:.0f}ms)", dt_r < 1.0 and len(rb) > 0)

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
