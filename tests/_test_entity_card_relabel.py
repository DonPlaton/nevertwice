#!/usr/bin/env python3
"""A re-labelled entity updates its existing card instead of minting a third.

_entity_card_stem bakes the CURRENT type into the filename and write_entity_card
re-resolved the type on every refresh, with no lookup for an existing card under another
prefix. So any re-label — or a typo — minted a new card and orphaned the old one forever,
and refresh_entity_cards iterates one type per entity, freezing every orphan.

Measured in the 2026-09 review: one Dart class existed simultaneously as
concept-report-service.md (8 notes), tool-report-service.md (9) and
method-report-service.md (7), with disjoint edges and CONTRADICTORY resolution state —
two marked a pitfall resolved, the third listed it open.
"""
import _env_guard  # noqa: F401
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
import memory_hook as m  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


orig_vault = m.VAULT
with tempfile.TemporaryDirectory() as td:
    m.VAULT = Path(td)
    ents = m.VAULT / "Entities"
    ents.mkdir()

    print("\n- with no card yet, the type-derived stem is used -")
    check("no prior card is found", m._existing_entity_card("report-service") is None)

    print("\n- an existing card is found whatever prefix minted it -")
    (ents / "concept-report-service.md").write_text("card", encoding="utf-8")
    found = m._existing_entity_card("report-service")
    check("the concept- card is found", found is not None and found.name == "concept-report-service.md",
          str(found))
    check("a re-label reuses that stem rather than minting method-",
          found.stem == "concept-report-service")

    print("\n- a different entity is not matched by suffix accident -")
    check("a longer name does not match a shorter one",
          m._existing_entity_card("service") is None
          or m._existing_entity_card("service").name.endswith("-service.md")
          and m._existing_entity_card("service").name != "concept-report-service.md")

    print("\n- a junk entity yields nothing rather than an arbitrary card -")
    check("junk finds no card", m._existing_entity_card("   ") is None)

    print("\n- the lookup is deterministic when several already exist -")
    (ents / "tool-report-service.md").write_text("card", encoding="utf-8")
    a = m._existing_entity_card("report-service")
    b = m._existing_entity_card("report-service")
    check("repeated lookups agree", a == b, f"{a} vs {b}")

m.VAULT = orig_vault
print(f"\nentity re-label: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
