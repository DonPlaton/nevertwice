#!/usr/bin/env python3
"""Regression tests for the Brain layer - F1 (onboarding profiles + typed-entity layer).

Guards: profile gating (coding default = brain OFF), the entity_types normalizer
(ontology-gated WRITE vs lenient profile-independent READ), the extraction prompt block
(empty unless a brain profile is on), the storage round-trip, the graph type index, and -
critically - the SEPARATION invariant: entity content under Entities/ never enters the
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
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
        os.environ.pop("NEVERTWICE_PROFILE", None)
        os.environ.pop("CLAUDE_MEMORY_PROFILE", None)
    else:
        os.environ["NEVERTWICE_PROFILE"] = val


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

# wide research ontology + research relation hints
set_profile("research")
check("research ontology is WIDE (>= 14 types)", len(cfg.entity_types()) >= 14)
check("wide ontology adds architecture/benchmark/metric/task/concept/tool",
      {"architecture", "benchmark", "metric", "task", "concept", "tool"} <= set(cfg.entity_types()))
check("research relation hints exposed (cites / evaluated-on)",
      {"cites", "evaluated-on"} <= set(cfg.relation_hints()))
set_profile("coding")
check("coding-only has no relation hints", cfg.relation_hints() == [])
set_profile("research")
check("brain_block surfaces a research relation hint", "cites" in m._brain_prompt_block())

# ── entity_types normalizer: write-gating vs lenient read ───────────────────────
print("entity_types normalizer")
set_profile("research")
wt = m._norm_entity_types({"ResNet": "method", "ImageNet": "dataset", "foo.py": "file"})
check("write-gate keeps ontology types + normalises names",
      wt == {"resnet": "method", "imagenet": "dataset"})
check("write-gate drops a non-ontology type ('file' absent from research ontology)",
      "foo-py" not in wt)
check("non-dict → {}", m._norm_entity_types(["x"]) == {} and m._norm_entity_types(None) == {})
check("junk/injection in the type slot is dropped by the gate",
      m._norm_entity_types({"x-ent": "rm -rf /"}) == {})

set_profile("coding")
check("coding-only write-gate yields nothing (brain off)",
      m._norm_entity_types({"resnet": "method"}) == {})
check("lenient read keeps stored type regardless of current profile",
      m._norm_entity_types({"resnet": "method"}, gate=False) == {"resnet": "method"})
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
        {"title": "Train ResNet with bf16", "description": "mixed precision works",
         "entities": ["resnet", "bfloat16"],
         "entity_types": {"resnet": "method", "bfloat16": "codevar"}},   # codevar ∉ ontology → dropped
        "demo", "2026-06-10", [], "pattern")
    fm = m._read_frontmatter_file(m.VAULT / "Patterns" / f"{stem}.md")
    check("entity_types written, ontology-filtered (non-ontology type dropped)",
          fm.get("entity_types") == {"resnet": "method"})
    meta = m._note_meta(m.VAULT / "Patterns" / f"{stem}.md", "pattern", m.parse_typed_stem(stem))
    check("_note_meta reads entity_types back", meta.get("entity_types") == {"resnet": "method"})

    m.write_typed_note("Decisions",
        {"title": "ResNet is our architecture", "entities": ["resnet"],
         "entity_types": {"resnet": "method"}}, "demo", "2026-06-12", [], "decision")
    ti = m.entity_types_index("demo")
    check("entity_types_index maps resnet -> method", ti.get("resnet") == "method")
    check("entities_by_type('method') lists resnet", "resnet" in m.entities_by_type("method", "demo"))
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
    (m.VAULT / "Entities" / "method-resnet.md").write_text(
        "---\ntype: entity\nentity_type: method\nproject: demo\n---\n# ResNet\nbody\n",
        encoding="utf-8")
    pool = m._iter_all_notes()
    check("only the typed note is in the cross-project pool - Entities/ excluded",
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
        {"title": "ResNet checkpoint drops morph flag", "description": "bool not a registered buffer",
         "entities": ["resnet", "checkpoint"], "entity_types": {"resnet": "method"},
         "relations": [{"rel": "fixed-by", "target": "buffer-registration"}]},
        "vision", "2026-05-26", [], "mistake")
    m.write_typed_note("Patterns",
        {"title": "ResNet scales to 30M params", "description": "modular pillars work",
         "entities": ["resnet", "scaling"], "entity_types": {"resnet": "method"}},
        "speech", "2026-06-12", [], "pattern")
    m.write_typed_note("Decisions",
        {"title": "ImageNet as the eval set", "description": "standard benchmark",
         "entities": ["imagenet"], "entity_types": {"imagenet": "dataset"}},
        "vision", "2026-06-01", [], "decision")

    n = m.refresh_entity_cards()
    check("refresh_entity_cards writes one card per typed entity (resnet, imagenet)", n == 2)
    cdir = m.VAULT / "Entities"
    check("cards live under Entities/",
          cdir.is_dir() and (cdir / "method-resnet.md").exists() and (cdir / "dataset-imagenet.md").exists())

    card = (cdir / "method-resnet.md").read_text(encoding="utf-8")
    cfm = m._read_frontmatter_file(cdir / "method-resnet.md")
    check("card frontmatter: type=entity, entity_type=method, name=resnet",
          cfm.get("type") == "entity" and cfm.get("entity_type") == "method" and cfm.get("name") == "resnet")
    check("card aggregates BOTH projects (cross-project rollup)",
          set(cfm.get("projects") or []) == {"vision", "speech"})
    check("card spans first/last seen", cfm.get("first_seen") == "2026-05-26"
          and cfm.get("last_seen") == "2026-06-12")
    check("card body lists the lessons + the typed neighbour",
          "ResNet checkpoint drops morph flag" in card and "fixed-by" in card)

    pool = m._iter_all_notes()
    check("entity card is NOT in the recall pool - only the 3 typed notes (separation)",
          len(pool) == 3 and all(p["ntype"] in ("pattern", "mistake", "decision") for p in pool))

    check("entity_card() reads the cached card", "🧠 resnet · method" in m.entity_card("resnet"))
    check("api.entity_card surface works (case-normalised)", "method" in api.entity_card("ResNet"))
    check("api.entities_by_type lists typed entities", api.entities_by_type("method") == ["resnet"])

    mtime = (cdir / "method-resnet.md").stat().st_mtime
    m.refresh_entity_cards()
    check("re-refresh is idempotent (no rewrite when unchanged)",
          (cdir / "method-resnet.md").stat().st_mtime == mtime)
    check("unknown entity → empty card", m.entity_card("nonexistent-xyz") == "")
    check("untyped entity (checkpoint) gets no card file", not (cdir / "entity-checkpoint.md").exists())

# ── F2: cards are OFF under a coding-only profile (opt-in) ───────────────────────
print("entity cards opt-in (coding = none)")
with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)
    set_profile("coding")
    check("refresh_entity_cards is a no-op under coding", m.refresh_entity_cards(["resnet"]) == 0)
    check("write_entity_card is a no-op under coding", m.write_entity_card("resnet", "method") == "")
    check("no Entities/ folder created under coding", not (m.VAULT / "Entities").exists())

# ── F3: temporal / evolution ─────────────────────────────────────────────────────
print("temporal / evolution (F3)")
with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)
    set_profile("research")
    m.write_typed_note("Decisions",
        {"title": "Use RNN for sequence model", "description": "rnn baseline",
         "entities": ["seqmodel"], "entity_types": {"seqmodel": "method"}},
        "proj", "2026-05-01", [], "decision")
    m.write_typed_note("Decisions",                          # later take SUPERSEDES the RNN one
        {"title": "Switch to Transformer for sequence model", "description": "attention wins",
         "entities": ["seqmodel"], "entity_types": {"seqmodel": "method"},
         "supersedes": "Use RNN for sequence model"},
        "proj", "2026-06-10", [], "decision")
    m.write_typed_note("Patterns",                           # a plain later mention
        {"title": "Transformer scaling law", "entities": ["seqmodel"],
         "entity_types": {"seqmodel": "method"}}, "proj", "2026-06-15", [], "pattern")

    check("the RNN take was retired to Superseded/",
          (m.VAULT / "Decisions" / "Superseded").exists())
    tl = m.entity_timeline("seqmodel")
    check("timeline spans live + superseded (3 mentions)", tl.get("count") == 3)
    check("first_seen is the EARLIEST incl. superseded", tl.get("first_seen") == "2026-05-01")
    check("last_seen is the latest", tl.get("last_seen") == "2026-06-15")
    evo = tl.get("evolution", [])
    check("evolution captures the superseded take", len(evo) == 1 and "RNN" in evo[0]["title"])
    check("evolution points to the successor",
          evo[0]["superseded_by"].endswith("switch-to-transformer-for-sequence-model"))

    m.refresh_entity_cards()
    card = (m.VAULT / "Entities" / "method-seqmodel.md").read_text(encoding="utf-8")
    check("card surfaces the evolution section", "Эволюция понимания" in card and "RNN" in card)
    check("card span counts the full history", "2026-05-01" in card and "упоминаний: 3" in card)
    check("api.entity_timeline works", api.entity_timeline("seqmodel").get("count") == 3)

    m.write_typed_note("Patterns", {"title": "Adam optimizer", "entities": ["adam"],
        "entity_types": {"adam": "method"}}, "proj", "2026-06-01", [], "pattern")
    tl2 = m.entity_timeline("adam")
    check("no-supersession entity → empty evolution, count 1",
          tl2.get("evolution") == [] and tl2.get("count") == 1)
    check("unknown entity → empty timeline", m.entity_timeline("nonexistent-zzz") == {})

# ── F5: salience (graph centrality, sleep-time) ──────────────────────────────────
print("salience (F5)")
import consolidate_memory as cm     # noqa: E402
import index_sqlite as sxi          # noqa: E402
with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)
    set_profile("research")
    a = m.write_typed_note("Patterns", {"title": "Hub method note", "entities": ["hub", "x"],
        "entity_types": {"hub": "method"}}, "proj", "2026-06-01", [], "pattern")
    m.write_typed_note("Mistakes", {"title": "B refs hub", "entities": ["b"],
        "relations": [{"rel": "uses", "target": "hub"}]}, "proj", "2026-06-02", [], "mistake")
    m.write_typed_note("Mistakes", {"title": "C refs hub", "entities": ["c"],
        "relations": [{"rel": "uses", "target": "hub"}]}, "proj", "2026-06-03", [], "mistake")
    d = m.write_typed_note("Patterns", {"title": "Lonely note", "entities": ["lonely"]},
        "proj", "2026-06-04", [], "pattern")

    sal = m.salience_index()
    check("central note (entity referenced by 2 edges) is most salient", sal.get(a) == 1.0)
    check("isolated note has 0 salience", sal.get(d) == 0.0)

    n = cm.stamp_salience(apply=True)
    check("stamp_salience stamps the central note", n >= 1)
    afm = m._read_frontmatter_file(m.VAULT / "Patterns" / f"{a}.md")
    check("central note got a salience stamp > 0.5", afm.get("salience") and float(afm["salience"]) > 0.5)
    dfm = m._read_frontmatter_file(m.VAULT / "Patterns" / f"{d}.md")
    check("peripheral note NOT stamped (kept clean)", dfm.get("salience") is None)
    check("stamp_salience is idempotent (no re-stamp)", cm.stamp_salience(apply=True) == 0)

    base = m._salience_mult(a, {"recurrence": 1})
    check("_salience_mult boosts a salient note above neutral",
          m._salience_mult(a, {"recurrence": 1, "salience": 1.0}) > base)
    check("_salience_mult inert when salience absent/0",
          m._salience_mult(a, {"recurrence": 1, "salience": 0}) == base)

    # SQLite carries the stamped salience into the ranking candidates
    m.save_embed_cache({a: {"ntype": "pattern", "project": "proj", "title": "Hub method note",
                            "desc": "", "prevention": ""}})
    sxi.build()
    cands = dict(sxi.iter_candidates("proj"))
    check("SQLite candidate carries the stamped salience", cands.get(a, {}).get("salience", 0) > 0.5)

# ── F5: inert on an entity-less / benchmark-like store ───────────────────────────
print("salience inert on a flat store")
with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)
    set_profile("coding")
    m.write_typed_note("Patterns", {"title": "plain note", "description": "no entities"},
                       "p", "2026-06-01", [], "pattern")
    check("entity-less store → salience all 0 (inert)",
          all(v == 0 for v in m.salience_index().values()))
    check("stamp_salience is a no-op on a flat store", cm.stamp_salience(apply=True) == 0)

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
