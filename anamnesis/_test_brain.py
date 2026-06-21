#!/usr/bin/env python3
"""Regression tests for the Brain layer — F1 (onboarding profiles + typed-entity layer).

Guards: profile gating (coding default = brain OFF), the entity_types normalizer
(ontology-gated WRITE vs lenient profile-independent READ), the extraction prompt block
(empty unless a brain profile is on), the storage round-trip, the graph type index, and —
critically — the SEPARATION invariant: entity content under Entities/ never enters the
default recall pool. No embedder / LLM / network needed.

    python _test_brain.py
"""
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import config as cfg              # noqa: E402
import memory_hook as m           # noqa: E402  (no profile is cached at import; brain fns read live)

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


def set_profile(val):
    if val is None:
        os.environ.pop("ANAMNESIS_PROFILE", None)
        os.environ.pop("CLAUDE_MEMORY_PROFILE", None)
    else:
        os.environ["ANAMNESIS_PROFILE"] = val


# ── profiles (config): default coding, brain OFF ────────────────────────────────
print("profiles / brain gate")
set_profile(None)
check("default profile is coding", cfg.profiles() == {"coding"})
check("brain OFF by default", cfg.brain_enabled() is False)
check("no entity types when coding-only", cfg.entity_types() == [])

set_profile("research")
check("research profile parsed", cfg.profiles() == {"research"})
check("brain ON for research", cfg.brain_enabled() is True)
check("research ontology exposed", {"paper", "method", "dataset"} <= set(cfg.entity_types()))

set_profile("coding,general")
check("multi-select profiles parsed", cfg.profiles() == {"coding", "general"})
check("brain ON when general present", cfg.brain_enabled() is True)
check("general ontology exposed", {"topic", "idea"} <= set(cfg.entity_types()))
check("coding composes with general without crashing", "paper" not in cfg.entity_types())

# ── entity_types normalizer: write-gating vs lenient read ───────────────────────
print("entity_types normalizer")
set_profile("research")
wt = m._norm_entity_types({"GEARS": "method", "ImageNet": "dataset", "foo.py": "file"})
check("write-gate keeps ontology types + normalises names",
      wt == {"gears": "method", "imagenet": "dataset"})
check("write-gate drops a non-ontology type ('file' absent from research ontology)",
      "foo-py" not in wt)
check("non-dict → {}", m._norm_entity_types(["x"]) == {} and m._norm_entity_types(None) == {})
check("junk/injection in the type slot is dropped by the gate",
      m._norm_entity_types({"x-ent": "rm -rf /"}) == {})

set_profile("coding")
check("coding-only write-gate yields nothing (brain off)",
      m._norm_entity_types({"gears": "method"}) == {})
check("lenient read keeps stored type regardless of current profile",
      m._norm_entity_types({"gears": "method"}, gate=False) == {"gears": "method"})
set_profile("research")

# ── extraction prompt block: present only when brain on ─────────────────────────
print("extraction prompt block")
set_profile("coding")
check("brain_block empty for coding (prompt byte-for-byte unchanged)", m._brain_prompt_block() == "")
set_profile("research")
blk = m._brain_prompt_block()
check("brain_block present + lists the ontology for research",
      "entity_types" in blk and "method" in blk and "paper" in blk)
formatted = m.EXTRACTION_PROMPT.format(
    transcript="x", project_hint="p", tag_vocab="t",
    existing_patterns="-", existing_mistakes="-", existing_decisions="-", brain_block=blk)
check("EXTRACTION_PROMPT formats with brain_block (no KeyError, placeholder consumed)",
      "{brain_block}" not in formatted and "entity_types" in formatted)
check("prompt with empty brain_block carries no entity_types ask (coding parity)",
      "entity_types" not in m.EXTRACTION_PROMPT.format(
          transcript="x", project_hint="p", tag_vocab="t",
          existing_patterns="-", existing_mistakes="-", existing_decisions="-", brain_block=""))

# ── storage round-trip + graph type index ───────────────────────────────────────
print("storage round-trip + graph type index")
with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)
    set_profile("research")
    stem = m.write_typed_note("Patterns",
        {"title": "Train GEARS with bf16", "description": "mixed precision works",
         "entities": ["gears", "bfloat16"],
         "entity_types": {"gears": "method", "bfloat16": "concept"}},   # concept ∉ ontology → dropped
        "demo", "2026-06-10", [], "pattern")
    fm = m._read_frontmatter_file(m.VAULT / "Patterns" / f"{stem}.md")
    check("entity_types written, ontology-filtered (bfloat16/concept dropped)",
          fm.get("entity_types") == {"gears": "method"})
    meta = m._note_meta(m.VAULT / "Patterns" / f"{stem}.md", "pattern", m.parse_typed_stem(stem))
    check("_note_meta reads entity_types back", meta.get("entity_types") == {"gears": "method"})

    m.write_typed_note("Decisions",
        {"title": "GEARS is our architecture", "entities": ["gears"],
         "entity_types": {"gears": "method"}}, "demo", "2026-06-12", [], "decision")
    ti = m.entity_types_index("demo")
    check("entity_types_index maps gears -> method", ti.get("gears") == "method")
    check("entities_by_type('method') lists gears", "gears" in m.entities_by_type("method", "demo"))
    check("entities_by_type('paper') empty here", m.entities_by_type("paper", "demo") == [])

    s2 = m.write_typed_note("Patterns", {"title": "plain note", "entities": ["x-thing"]},
                            "demo", "2026-06-13", [], "pattern")
    check("no entity_types → no key written (behaviour unchanged)",
          "entity_types" not in m._read_frontmatter_file(m.VAULT / "Patterns" / f"{s2}.md"))

# ── SEPARATION invariant: Entities/ never enters the recall pool ─────────────────
print("separation invariant (Entities/ excluded from recall)")
with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)
    m.write_typed_note("Patterns", {"title": "real lesson", "entities": ["e1"]},
                       "demo", "2026-06-01", [], "pattern")
    (m.VAULT / "Entities").mkdir(parents=True, exist_ok=True)   # F2 will generate these cards
    (m.VAULT / "Entities" / "method-gears.md").write_text(
        "---\ntype: entity\nentity_type: method\nproject: demo\n---\n# GEARS\nbody\n",
        encoding="utf-8")
    pool = m._iter_all_notes()
    check("only the typed note is in the cross-project pool — Entities/ excluded",
          len(pool) == 1 and pool[0]["title"] == "real lesson")
    check("_iter_project_notes also excludes Entities/",
          all(n["ntype"] in ("pattern", "mistake", "decision") for n in m._iter_project_notes("demo")))

# ── F2: entity cards ────────────────────────────────────────────────────────────
print("entity cards (F2)")
import api                       # noqa: E402  (shares the same memory_hook module object as m)
with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)
    set_profile("research")
    m.write_typed_note("Mistakes",
        {"title": "GEARS checkpoint drops morph flag", "description": "bool not a registered buffer",
         "entities": ["gears", "checkpoint"], "entity_types": {"gears": "method"},
         "relations": [{"rel": "fixed-by", "target": "buffer-registration"}]},
        "gears_experiments", "2026-05-26", [], "mistake")
    m.write_typed_note("Patterns",
        {"title": "GEARS scales to 30M params", "description": "modular pillars work",
         "entities": ["gears", "scaling"], "entity_types": {"gears": "method"}},
        "nexus", "2026-06-12", [], "pattern")
    m.write_typed_note("Decisions",
        {"title": "ImageNet as the eval set", "description": "standard benchmark",
         "entities": ["imagenet"], "entity_types": {"imagenet": "dataset"}},
        "gears_experiments", "2026-06-01", [], "decision")

    n = m.refresh_entity_cards()
    check("refresh_entity_cards writes one card per typed entity (gears, imagenet)", n == 2)
    cdir = m.VAULT / "Entities"
    check("cards live under Entities/",
          cdir.is_dir() and (cdir / "method-gears.md").exists() and (cdir / "dataset-imagenet.md").exists())

    card = (cdir / "method-gears.md").read_text(encoding="utf-8")
    cfm = m._read_frontmatter_file(cdir / "method-gears.md")
    check("card frontmatter: type=entity, entity_type=method, name=gears",
          cfm.get("type") == "entity" and cfm.get("entity_type") == "method" and cfm.get("name") == "gears")
    check("card aggregates BOTH projects (cross-project rollup)",
          set(cfm.get("projects") or []) == {"gears_experiments", "nexus"})
    check("card spans first/last seen", cfm.get("first_seen") == "2026-05-26"
          and cfm.get("last_seen") == "2026-06-12")
    check("card body lists the lessons + the typed neighbour",
          "GEARS checkpoint drops morph flag" in card and "fixed-by" in card)

    pool = m._iter_all_notes()
    check("entity card is NOT in the recall pool — only the 3 typed notes (separation)",
          len(pool) == 3 and all(p["ntype"] in ("pattern", "mistake", "decision") for p in pool))

    check("entity_card() reads the cached card", "🧠 gears · method" in m.entity_card("gears"))
    check("api.entity_card surface works (case-normalised)", "method" in api.entity_card("GEARS"))
    check("api.entities_by_type lists typed entities", api.entities_by_type("method") == ["gears"])

    mtime = (cdir / "method-gears.md").stat().st_mtime
    m.refresh_entity_cards()
    check("re-refresh is idempotent (no rewrite when unchanged)",
          (cdir / "method-gears.md").stat().st_mtime == mtime)
    check("unknown entity → empty card", m.entity_card("nonexistent-xyz") == "")
    check("untyped entity (checkpoint) gets no card file", not (cdir / "entity-checkpoint.md").exists())

# ── F2: cards are OFF under a coding-only profile (opt-in) ───────────────────────
print("entity cards opt-in (coding = none)")
with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)
    set_profile("coding")
    check("refresh_entity_cards is a no-op under coding", m.refresh_entity_cards(["gears"]) == 0)
    check("write_entity_card is a no-op under coding", m.write_entity_card("gears", "method") == "")
    check("no Entities/ folder created under coding", not (m.VAULT / "Entities").exists())

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
