#!/usr/bin/env python3
"""Brain layer — the INVARIANT guards (docs/BRAIN_LAYER_DESIGN.md §7). These are the tests
that keep the philosophy honest in CI:

  1. SEPARATION — entity/Brain notes (under Entities/) NEVER enter the default recall pool.
  2. BUDGET     — the SessionStart injection stays within INJECT_BUDGET_CHARS and is byte-for-byte
                  UNCHANGED whether the Brain layer is on or off (entity cards must not leak in).
  3. PRIVACY    — extraction for a LOCAL_ONLY project/agent NEVER calls the cloud, brain on or off.
  4. OPT-IN     — a coding-only profile writes no entity_types and no Entities/ — today's system.

No embedder / LLM / network needed.

    python _test_brain_invariants.py
"""
import io
import os
import sys
import contextlib
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
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


def set_profile(v):
    if v is None:
        os.environ.pop("NEVERTWICE_PROFILE", None)
    else:
        os.environ["NEVERTWICE_PROFILE"] = v


def seed_vault(td):
    """A small vault: real typed notes + generated entity cards (brain on)."""
    m.VAULT = Path(td)
    set_profile("research")
    m.write_typed_note("Mistakes", {"title": "ResNet OOM at batch 64", "description": "vram blew up",
        "entities": ["resnet", "cuda"], "entity_types": {"resnet": "method"},
        "relations": [{"rel": "fixed-by", "target": "checkpointing"}]},
        "demo", "2026-06-01", [], "mistake")
    m.write_typed_note("Patterns", {"title": "Cap the batch size", "description": "use grad accum",
        "entities": ["cuda", "batch-size"], "entity_types": {"cuda": "method"}},
        "demo", "2026-06-02", [], "pattern")
    m.write_typed_note("Decisions", {"title": "Adopt AMP everywhere", "description": "mixed precision",
        "entities": ["amp"], "entity_types": {"amp": "method"}},
        "demo", "2026-06-03", [], "decision")
    m.refresh_entity_cards()            # builds Entities/ cards
    return "demo"


# ── 1. SEPARATION ────────────────────────────────────────────────────────────────
print("INVARIANT 1 — separation (Entities/ never in the recall pool)")
with tempfile.TemporaryDirectory() as td:
    proj = seed_vault(td)
    card_dir = m.VAULT / "Entities"
    cards = list(card_dir.glob("*.md")) if card_dir.exists() else []
    check("entity cards were generated under Entities/", len(cards) >= 1)

    pool_all = m._iter_all_notes()
    pool_proj = m._iter_project_notes(proj)
    card_stems = {c.stem for c in cards}
    check("no entity card in _iter_all_notes (cross-project pool)",
          card_stems.isdisjoint({n["stem"] for n in pool_all}))
    check("no entity card in _iter_project_notes (per-project pool)",
          card_stems.isdisjoint({n["stem"] for n in pool_proj}))
    check("the pool is exactly the 3 typed notes",
          len(pool_all) == 3 and all(n["ntype"] in ("mistake", "pattern", "decision") for n in pool_all))

    # the SQLite candidate path must also exclude cards
    m.save_embed_cache({n["stem"]: {"ntype": n["ntype"], "project": proj, "title": n["title"],
                                    "desc": n.get("desc", ""), "prevention": ""} for n in pool_all})
    sx.build()
    cand_stems = {s for s, _ in sx.iter_candidates(proj)}
    check("no entity card in the SQLite candidate pool",
          card_stems.isdisjoint(cand_stems) and len(cand_stems) == 3)


# ── 2. BUDGET (injection ≤ budget, brain-on == brain-off) ────────────────────────
print("INVARIANT 2 — budget (injection bounded + unchanged on/off)")
os.environ["NEVERTWICE_SALIENCE_BOOST"] = "0"     # isolate from the F5 nudge for an exact comparison
with tempfile.TemporaryDirectory() as td:
    proj = seed_vault(td)
    (m.VAULT / "Context").mkdir(exist_ok=True)
    (m.VAULT / "Context" / f"{proj}.md").write_text(
        "---\ntype: context\n---\n## Накопленное состояние\nResNet training.\n", encoding="utf-8")

    _orig = {n: getattr(m, n) for n in ("is_tracked_project", "derive_project_from_cwd",
                                        "retrieve_relevant", "retrieve_cross_project")}
    try:
        m.is_tracked_project = lambda cwd: True
        m.derive_project_from_cwd = lambda cwd: proj
        m.retrieve_cross_project = lambda *a, **k: []
        # the REAL pool (which excludes Entities/) drives the injection
        m.retrieve_relevant = lambda project, q, k, **kw: m._iter_project_notes(project)[:k]

        def emit(cwd="D:\\Coding\\demo"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                m.emit_session_start_context(cwd)
            return buf.getvalue().strip()

        set_profile("research")
        on = emit()
        set_profile("coding")
        off = emit()

        for label, payload in (("brain-ON", on), ("brain-OFF", off)):
            check(f"{label}: an injection was produced", bool(payload))
            ctx = payload  # the JSON string; size bound applies to the whole stdout payload
            check(f"{label}: payload within INJECT_BUDGET_CHARS ({len(ctx)}≤{m.INJECT_BUDGET_CHARS})",
                  len(ctx) <= m.INJECT_BUDGET_CHARS)
            check(f"{label}: no entity-card header leaked into the injection",
                  "· method" not in ctx and "\\u00b7 method" not in ctx)
        check("BUDGET INVARIANT: injection byte-for-byte identical brain-ON vs brain-OFF",
              on == off)
    finally:
        for n, v in _orig.items():
            setattr(m, n, v)
os.environ.pop("NEVERTWICE_SALIENCE_BOOST", None)


# ── 3. PRIVACY (LOCAL_ONLY never reaches the cloud) ──────────────────────────────
print("INVARIANT 3 — privacy (LOCAL_ONLY extraction stays local)")
set_profile("research")                          # brain on — entity typing rides the SAME call
# LOCAL_ONLY_PROJECTS is resolved at import from the env (set before process start in production);
# set the live module value directly here since we're mutating it after import.
_p = {n: getattr(m, n) for n in ("call_cloud", "call_ollama", "ACTIVE_CLOUD", "cloud_key",
                                 "_CLOUD_DEAD", "LOCAL_ONLY_PROJECTS", "CLOUD_ONLY_PROJECTS")}
cloud_calls, ollama_calls = [], []
try:
    m.LOCAL_ONLY_PROJECTS = {"secretproj", "codex"}
    m.CLOUD_ONLY_PROJECTS = set()
    m.ACTIVE_CLOUD = "cerebras"
    m.cloud_key = lambda: "fake-key-not-used"
    m._CLOUD_DEAD = False
    m.call_cloud = lambda prompt: (cloud_calls.append(1), {"patterns": []})[1]
    m.call_ollama = lambda prompt: (ollama_calls.append(1), {"patterns": []})[1]

    cloud_calls.clear(); ollama_calls.clear()
    m.generate_json("extract this", project="secretproj")
    check("LOCAL_ONLY project: cloud NEVER called", not cloud_calls and bool(ollama_calls))

    cloud_calls.clear(); ollama_calls.clear()
    m.generate_json("extract this", project="codex")       # watched-agent label
    check("LOCAL_ONLY agent label (codex): cloud NEVER called", not cloud_calls)

    cloud_calls.clear(); ollama_calls.clear()
    m.generate_json("extract this", project="public_proj")  # not gated → cloud allowed
    check("non-gated project: cloud IS used (gate is selective, not blanket)", bool(cloud_calls))
finally:
    for n, v in _p.items():
        setattr(m, n, v)
    os.environ.pop("NEVERTWICE_LOCAL_ONLY", None)


# ── 4. OPT-IN (coding profile = today's system, byte-for-byte) ───────────────────
print("INVARIANT 4 — opt-in (coding writes no Brain artefacts)")
with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)
    set_profile("coding")
    stem = m.write_typed_note("Patterns",
        {"title": "Plain lesson", "entities": ["cuda"], "entity_types": {"cuda": "method"}},
        "demo", "2026-06-01", [], "pattern")
    fm = m._read_frontmatter_file(m.VAULT / "Patterns" / f"{stem}.md")
    check("coding: entity_types NOT written even if the model emits them", "entity_types" not in fm)
    check("coding: refresh_entity_cards is a no-op", m.refresh_entity_cards() == 0)
    check("coding: no Entities/ folder", not (m.VAULT / "Entities").exists())
    check("coding: salience scoring inert (no stamps)", m.salience_index().get(stem, 0) == 0)

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
