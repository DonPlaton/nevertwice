#!/usr/bin/env python3
"""Tests for research/bio_memory.py (2A). Pins the claim-memory mapping: bio-memory
excludes overturned claims, resists single-study hype, returns the era-correct belief
under `as_of`, and entity-gates off-topic claims. Pure stdlib."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "nevertwice"))
sys.path.insert(0, str(HERE.parent.parent / "research"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import bio_memory as b

P = F = 0


def ok(cond, label):
    global P, F
    P, F = P + (1 if cond else 0), F + (0 if cond else 1)
    print(f"  [{'OK ' if cond else 'FAIL'}] {label}")


contra = b.contradicted_ids()
ok(b.bio_memory("rapamycin mTOR lifespan") is b.bio_memory("rapamycin mTOR lifespan"),
   "bio_memory is deterministic")

# never serves an overturned (refuted/superseded) claim as the current best
served_overturned = [q for q in b.BEST_Q if (r := b.bio_memory(q)) and r["id"] in contra]
ok(not served_overturned, f"current best is never an overturned claim ({served_overturned})")

# resists single-study hype: the replicated claim, not the latest single study
ok(b.bio_memory("does resveratrol extend lifespan")["id"] == "resv",
   "resveratrol → the replicated null, not the 2023 hype")
ok(b.bio_memory("NMN NAD lifespan")["id"] == "nmn",
   "NMN → the replicated finding, not the 2024 single-trial hype")

# flat-newest IS fooled by hype (the failure bio-memory fixes)
ok(b.flat_newest("does resveratrol extend lifespan")["id"] == "resv_hype",
   "flat-newest returns the newest (hype) claim - the baseline failure")

# entity gating: an antioxidant query must not return a caloric-restriction claim
ok("caloric" not in b.bio_memory("antioxidant lifespan")["interv"],
   "entity gate: antioxidant query doesn't leak to caloric-restriction")

# bi-temporal: as-of returns the belief held THEN, including a since-refuted one
ok(b.bio_memory("resveratrol sirtuin lifespan", as_of=2006)["id"] == "resv_old",
   "as_of 2006 → the then-current resveratrol→SIRT1 belief (later refuted)")
ok(b.bio_memory("rapamycin mTOR lifespan", as_of=2005)["id"] == "rapa_pre",
   "as_of 2005 → the pre-ITP 'unproven' belief")

# contradiction set = refuted ∪ superseded ∪ contradicted-targets, all well-formed
ok(contra and all(cid in b.BY_ID for cid in contra), "contradicted ids are all real claims")

# ingest adapter produces well-formed claims
rows = [{"compound": "Aspirin", "organism": "mouse", "n_studies": 3, "year": 2018}]
ic = b.ingest_drugage(rows)
ok(ic and ic[0]["support"] == 3 and ic[0]["interv"] == "Aspirin", "ingest_drugage maps a row")

print(f"\nbio_memory: {P} passed, {F} failed")
sys.exit(1 if F else 0)
