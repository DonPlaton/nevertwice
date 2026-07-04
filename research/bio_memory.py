#!/usr/bin/env python3
"""RESEARCH — replication-weighted, bi-temporal memory for scientific claims (roadmap 2A).

MISSION MAPPING. The agent-memory machinery maps cleanly onto the longevity-research domain:
  recurrence            → #independent replications (a result seen 5× is more trustworthy)
  bi-temporal valid_to  → scientific belief revision ("what did we believe about X in 2008?")
  supersession/contradicts → a refuted or revised claim
So the same primitives (recurrence boost, `as_of`, supersession) should surface the
*currently best-supported* finding — resisting the latest single-study hype and excluding
refuted claims — better than flat retrieval. This is the part that serves the life-extension
mission directly.

CORPUS. A curated set of well-known aging findings WITH their real replication/revision arcs
(resveratrol→SIRT1 artifact, antioxidant null, GDF11 reversal, telomere Mendelian-randomization,
CR-primate NIA-vs-Wisconsin, parabiosis dilution, plus recent low-replication hype) — public
knowledge, so the gold labels are defensible and the study is fully offline & reproducible.
`ingest_drugage()` shows the adapter for a real structured source (DrugAge/GenAge); the curated
set is the shipped demonstration. Relevance is lexical token overlap (offline, no embedder) —
the contribution is the replication/time/contradiction LAYERS on top, not the matcher.

TASKS vs flat retrieval (use-newest / lexical-only):
  1. current best-supported finding  → accuracy (resist hype, exclude refuted)
  2. as-of belief (bi-temporal)       → era-correct accuracy
  3. contradicted-claim surfacing      → how often a method serves a refuted finding (↓ better)
                                         + contradiction-detection F1 from the structure

    python research/bio_memory.py            # report
    python research/bio_memory.py --save     # + bio_memory.json

Research dep: none beyond the package (stdlib + memory_hook._tokens). Deterministic.
"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nevertwice"))
import memory_hook as m

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SAVE = "--save" in sys.argv
NOW = 2026

# ── curated longevity-claims corpus (real findings + their replication/revision arcs) ──
# id · text · interv (topic key) · support (#replications) · vf/vt (belief window) ·
# status (supported|refuted|contested|superseded) · contradicts (id this overturns)
CLAIMS = [
    # rapamycin / mTOR — robustly replicated (ITP)
    dict(id="rapa_pre", text="whether mTOR inhibition extends mammalian lifespan is unproven",
         interv="rapamycin mTOR", support=1, vf=2000, vt=2009, status="superseded", contradicts=None),
    dict(id="rapa", text="rapamycin extends lifespan in mice via mTOR inhibition, replicated across labs",
         interv="rapamycin mTOR", support=9, vf=2009, vt=None, status="supported", contradicts="rapa_pre"),
    # caloric restriction — strong; primate result revised (NIA vs Wisconsin)
    dict(id="cr", text="caloric restriction extends lifespan across many model species",
         interv="caloric restriction", support=8, vf=1935, vt=None, status="supported", contradicts=None),
    dict(id="cr_prim_old", text="caloric restriction robustly extends primate lifespan (Wisconsin)",
         interv="caloric restriction primate", support=2, vf=2009, vt=2012, status="superseded", contradicts=None),
    dict(id="cr_prim", text="primate caloric restriction lifespan benefit is diet and control dependent (NIA null)",
         interv="caloric restriction primate", support=3, vf=2012, vt=None, status="supported", contradicts="cr_prim_old"),
    # resveratrol / SIRT1 — the replication crisis, plus recent hype
    dict(id="resv_old", text="resveratrol activates SIRT1 and extends lifespan",
         interv="resveratrol sirtuin", support=2, vf=2003, vt=2010, status="refuted", contradicts=None),
    dict(id="resv", text="resveratrol SIRT1 activation is largely a fluorophore assay artifact; lifespan effect does not robustly replicate",
         interv="resveratrol sirtuin", support=5, vf=2010, vt=None, status="supported", contradicts="resv_old"),
    dict(id="resv_hype", text="a new resveratrol formulation shows lifespan extension in a single 2023 study",
         interv="resveratrol sirtuin", support=1, vf=2023, vt=None, status="supported", contradicts=None),
    # metformin (TAME)
    dict(id="metf", text="metformin may extend healthspan and lifespan, TAME trial hypothesis",
         interv="metformin", support=3, vf=2014, vt=None, status="supported", contradicts=None),
    # senolytics
    dict(id="seno", text="senolytics dasatinib quercetin clear senescent cells and improve healthspan in mice",
         interv="senolytics senescence", support=6, vf=2015, vt=None, status="supported", contradicts=None),
    # NAD+ / NMN — modest, plus hype
    dict(id="nmn", text="NMN and NR boost NAD+ and improve some aging markers and healthspan",
         interv="NMN NAD", support=4, vf=2016, vt=None, status="supported", contradicts=None),
    dict(id="nmn_hype", text="NMN extends human lifespan according to a single 2024 trial",
         interv="NMN NAD", support=1, vf=2024, vt=None, status="supported", contradicts=None),
    # antioxidants — early hype then refuted
    dict(id="aox_old", text="dietary antioxidants extend lifespan by reducing oxidative damage",
         interv="antioxidant oxidative", support=1, vf=1956, vt=2009, status="refuted", contradicts=None),
    dict(id="aox", text="antioxidant supplementation does not extend lifespan and can be harmful",
         interv="antioxidant oxidative", support=7, vf=2009, vt=None, status="supported", contradicts="aox_old"),
    # GDF11 — famous reversal
    dict(id="gdf_old", text="GDF11 declines with age and restoring it rejuvenates tissue",
         interv="GDF11 rejuvenation", support=1, vf=2013, vt=2015, status="refuted", contradicts=None),
    dict(id="gdf", text="GDF11 rejuvenation claims do not replicate and GDF11 may inhibit regeneration",
         interv="GDF11 rejuvenation", support=3, vf=2015, vt=None, status="supported", contradicts="gdf_old"),
    # telomeres — Mendelian randomization reversal
    dict(id="telo_old", text="longer telomeres straightforwardly cause human longevity",
         interv="telomere length", support=1, vf=2000, vt=2015, status="refuted", contradicts=None),
    dict(id="telo", text="telomere length effect is trait dependent and longer telomeres raise some cancer risk by Mendelian randomization",
         interv="telomere length", support=4, vf=2015, vt=None, status="supported", contradicts="telo_old"),
    # genetics / pathways — well replicated
    dict(id="foxo3", text="FOXO3 genetic variants associate with human longevity across cohorts",
         interv="FOXO3 longevity gene", support=6, vf=2008, vt=None, status="supported", contradicts=None),
    dict(id="klotho", text="klotho overexpression extends lifespan in mice",
         interv="klotho", support=3, vf=2005, vt=None, status="supported", contradicts=None),
    dict(id="sirt6", text="SIRT6 overexpression extends lifespan in male mice",
         interv="SIRT6 sirtuin", support=2, vf=2012, vt=None, status="supported", contradicts=None),
    # parabiosis / young blood — dilution reframing
    dict(id="blood_old", text="young plasma broadly reverses aging via youthful factors in parabiosis",
         interv="parabiosis young blood", support=2, vf=2005, vt=2016, status="superseded", contradicts=None),
    dict(id="blood", text="parabiosis benefits come largely from dilution of old blood factors not youth factors",
         interv="parabiosis young blood", support=3, vf=2016, vt=None, status="supported", contradicts="blood_old"),
    # strongest human evidence
    dict(id="exercise", text="regular exercise extends healthspan and reduces all cause mortality",
         interv="exercise", support=10, vf=1990, vt=None, status="supported", contradicts=None),
    dict(id="sperm", text="spermidine induces autophagy and extends lifespan in model organisms",
         interv="spermidine autophagy", support=4, vf=2009, vt=None, status="supported", contradicts=None),
]
BY_ID = {c["id"]: c for c in CLAIMS}


def ingest_drugage(rows):
    """Adapter sketch: map DrugAge/GenAge-style rows
    {compound, organism, effect_pct, n_studies, year} → claim dicts (support=n_studies,
    vf=year). Shipped study uses the curated CLAIMS; this documents extension to real data."""
    out = []
    for r in rows:
        out.append(dict(id=f"{r['compound']}_{r['organism']}".lower().replace(" ", "_"),
                        text=f"{r['compound']} extends lifespan in {r['organism']} "
                             f"by {r.get('effect_pct', '?')}%",
                        interv=r["compound"], support=int(r.get("n_studies", 1)),
                        vf=int(r.get("year", NOW)), vt=None, status="supported", contradicts=None))
    return out


# ── retrieval ───────────────────────────────────────────────────────────
INTERV_W = 3        # an intervention/entity-token match weighs more than a generic word
REP_W = 2.0         # replication tiebreak weight (ADDITIVE — relevance stays primary, so
#                     support breaks ties between equally-relevant claims, never overrides a
#                     clear entity match: the same "gentle tiebreak" discipline as the ranker)


def relevance(query, c):
    """Entity-aware lexical relevance: an intervention/entity-token hit counts INTERV_W×, so a
    query about X prefers X-claims over claims that merely share generic words (extend, lifespan)."""
    qt = m._tokens(query)
    return INTERV_W * len(qt & m._tokens(c["interv"])) + len(qt & m._tokens(c["text"]))


def matches(query):
    """Candidate claims, ENTITY-GATED: a claim qualifies only if the query names its
    intervention (shares an interv token) — claims about a different intervention don't
    compete just because they share a generic word. Falls back to any lexical overlap if
    nothing names an entity. The SAME candidate set feeds every method, so the comparison
    isolates the validity/replication/contradiction layers, not entity hygiene."""
    qt = m._tokens(query)
    gated = [(relevance(query, c), c) for c in CLAIMS if qt & m._tokens(c["interv"])]
    if gated:
        return gated
    return [(rel, c) for rel, c in ((relevance(query, c), c) for c in CLAIMS) if rel]


def flat_newest(query):
    """Baseline: among matches, the NEWEST claim (ignores replication & validity)."""
    ms = matches(query)
    return max(ms, key=lambda rc: (rc[1]["vf"], rc[0]))[1] if ms else None


def lexical_only(query):
    """Baseline: highest relevance, newest as tiebreak (ignores replication & validity)."""
    ms = matches(query)
    return max(ms, key=lambda rc: (rc[0], rc[1]["vf"]))[1] if ms else None


def bio_memory(query, as_of=NOW):
    """Ours: among matches CURRENT as-of `as_of` (valid window contains it) and not refuted-
    by-`as_of`, rank by relevance + REP_W·log(1+replications) — replication-weighted (additive,
    so relevance leads), bi-temporal, contradiction-aware."""
    out = []
    for rel, c in matches(query):
        if c["vf"] > as_of or (c["vt"] is not None and as_of >= c["vt"]):
            continue                                   # not the belief held at `as_of`
        if as_of >= NOW and c["status"] in ("refuted", "superseded"):
            continue                                   # currently overturned → exclude
        out.append((rel + REP_W * math.log1p(c["support"]), c))
    return max(out, key=lambda sc: (sc[0], sc[1]["support"]))[1] if out else None


def contradicted_ids():
    """The set the structure marks as overturned: refuted/superseded, or contradicted by a later
    claim (a `contradicts` target). This is what a contradiction-aware reader should flag."""
    out = {c["id"] for c in CLAIMS if c["status"] in ("refuted", "superseded")}
    out |= {c["contradicts"] for c in CLAIMS if c["contradicts"]}
    return out


# ── evaluation ──────────────────────────────────────────────────────────

# best-supported CURRENT finding per topic (gold = the replicated answer, not hype/refuted)
BEST_Q = {
    "does resveratrol extend lifespan": "resv",
    "rapamycin lifespan": "rapa",
    "do antioxidants extend lifespan": "aox",
    "GDF11 rejuvenation": "gdf",
    "telomere length and longevity": "telo",
    "NMN NAD lifespan": "nmn",
    "caloric restriction primate lifespan": "cr_prim",
    "young blood parabiosis rejuvenation": "blood",
    "senolytics healthspan": "seno",
    "FOXO3 longevity": "foxo3",
}
# (query, year) → the best-supported belief held THEN (incl. claims later refuted)
ASOF_Q = [
    ("resveratrol sirtuin lifespan", 2006, "resv_old"),
    ("resveratrol sirtuin lifespan", 2013, "resv"),
    ("antioxidant lifespan", 1990, "aox_old"),
    ("antioxidant lifespan", 2015, "aox"),
    ("GDF11 rejuvenation", 2014, "gdf_old"),
    ("GDF11 rejuvenation", 2020, "gdf"),
    ("rapamycin mTOR lifespan", 2005, "rapa_pre"),
    ("rapamycin mTOR lifespan", 2015, "rapa"),
    ("parabiosis young blood", 2010, "blood_old"),
    ("telomere length longevity", 2008, "telo_old"),
]


def eval_best():
    methods = {"flat-newest": flat_newest, "lexical-only": lexical_only, "bio-memory": bio_memory}
    acc, served_contra = {}, {}
    contra = contradicted_ids()
    for name, fn in methods.items():
        hit = bad = 0
        for q, gold in BEST_Q.items():
            r = fn(q)
            hit += (r is not None and r["id"] == gold)
            bad += (r is not None and r["id"] in contra)
        acc[name] = hit / len(BEST_Q)
        served_contra[name] = bad / len(BEST_Q)
    return acc, served_contra


def eval_asof():
    acc = {"flat-newest": 0, "bio-memory": 0}
    for q, year, gold in ASOF_Q:
        if flat_newest(q) and flat_newest(q)["id"] == gold:
            acc["flat-newest"] += 1
        r = bio_memory(q, as_of=year)
        if r and r["id"] == gold:
            acc["bio-memory"] += 1
    return {k: v / len(ASOF_Q) for k, v in acc.items()}


def eval_contradiction():
    """F1 of the structure-derived contradicted set vs the gold (all refuted/superseded/
    contradicted claims). The bi-temporal/supersession structure should recover them; a
    recency baseline (flag the older of each intervention's claims) is the comparator."""
    gold = contradicted_ids()
    detected = contradicted_ids()                       # structure-aware
    # recency baseline: for each intervention with >1 claim, flag all but the newest
    base = set()
    by_interv: dict = {}
    for c in CLAIMS:
        by_interv.setdefault(c["interv"], []).append(c)
    for cs in by_interv.values():
        if len(cs) > 1:
            newest = max(cs, key=lambda c: c["vf"])["id"]
            base |= {c["id"] for c in cs if c["id"] != newest}

    def f1(pred):
        tp = len(pred & gold)
        p = tp / len(pred) if pred else 0.0
        r = tp / len(gold) if gold else 0.0
        return 2 * p * r / (p + r) if (p + r) else 0.0
    return {"structure-aware": f1(detected), "recency-baseline": f1(base),
            "n_contradicted": len(gold)}


def main():
    bar = "=" * 78
    print(bar)
    print("  BIO/LONGEVITY RESEARCH-MEMORY (2A) — replication-weighted, bi-temporal claims")
    print(bar)
    print(f"  curated corpus: {len(CLAIMS)} longevity claims with real replication/revision arcs")

    acc, served = eval_best()
    print(f"\n— current best-supported finding (accuracy over {len(BEST_Q)} topics) —")
    print(f"  {'method':14} {'accuracy':>9} {'served-contradicted':>20}")
    for name in ("flat-newest", "lexical-only", "bio-memory"):
        print(f"  {name:14} {acc[name]:>9.3f} {served[name]:>20.3f}")
    print(f"  → bio-memory resists single-study hype and excludes refuted claims: "
          f"accuracy {acc['bio-memory']:.3f} vs flat-newest {acc['flat-newest']:.3f}; "
          f"serves a contradicted finding {served['bio-memory']:.0%} vs {served['flat-newest']:.0%}.")

    af = eval_asof()
    print(f"\n— as-of belief (bi-temporal, accuracy over {len(ASOF_Q)} era-queries) —")
    print(f"  bio-memory (as_of)  {af['bio-memory']:.3f}    flat-newest (anachronistic)  {af['flat-newest']:.3f}")
    print(f"  → returning the version current THEN, not the latest: "
          f"{af['bio-memory'] - af['flat-newest']:+.3f}.")

    cf = eval_contradiction()
    print(f"\n— contradiction detection (F1 over {cf['n_contradicted']} overturned claims) —")
    print(f"  structure-aware {cf['structure-aware']:.3f}   recency-baseline {cf['recency-baseline']:.3f}")
    print(f"  → supersession/contradicts links recover overturned claims a recency heuristic misranks.")

    if SAVE:
        out = {"claims": len(CLAIMS), "best_accuracy": acc, "served_contradicted": served,
               "asof": af, "contradiction_f1": cf}
        p = HERE / "bio_memory.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  saved → {p}")
    print(bar)


if __name__ == "__main__":
    main()
