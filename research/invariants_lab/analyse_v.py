"""Phase V: every in-sample number against its out-of-sample twin, and what governs precision.

Two analyses, both read-only over artifacts the frozen code already wrote.

**The delta.** `GOAL-SHIP.md` §0.3 said the finding this run exists to produce is the
difference between a number measured on the eight repositories a mechanism was tuned on and
the same number measured on twenty-seven it never saw. Every pair is assembled here, with the
gate it was judged against and whether the verdict moved.

**The between-block model.** `BLAST_RADIUS_D5.md` found precision ranging 0.174 to 0.836 across
eight blocks and concluded "whatever governs precision is a property of the codebase, not of the
checker". Eight blocks cannot test that. Twenty-seven can, and the manifest carries the
predictors: contributors, commit count, Python-commit share, size on disk and domain.

Declared exploratory in `PREREGISTRATION-SHIP.md` §9 before the corpus was cloned: this ranks
hypotheses, it does not test one.

    python research/invariants_lab/analyse_v.py
    python research/invariants_lab/analyse_v.py --print
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from scipy import stats

HERE = Path(__file__).resolve().parent
ARTIFACT = HERE / "v_delta.json"


def _load(name: str) -> dict:
    path = HERE / name
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    """Wilson score interval -- the one that behaves at the extremes."""
    if n <= 0:
        return [float("nan"), float("nan")]
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, centre - half), min(1.0, centre + half)]


def _point(node: object) -> float | None:
    """A measurement written as {'point': x}, as {'rate': x}, or as a bare number.

    The frozen scripts spell the same idea three ways -- `point` for a proportion with an
    interval, `rate` for a flag rate, and a bare float where neither was thought needed.
    Reading all three here is cheaper than editing eleven frozen call sites, and safer.
    """
    if isinstance(node, dict):
        for key in ("point", "rate"):
            v = node.get(key)
            if isinstance(v, (int, float)):
                return float(v)
        return None
    return float(node) if isinstance(node, (int, float)) else None


def deltas() -> list[dict]:
    """Every in-sample / out-of-sample pair this run can form."""
    br_d, br_h = _load("blast_radius_d5.json"), _load("blast_radius_d5_heldout.json")
    ab_d, ab_h = _load("abstention_f1.json"), _load("abstention_f1_heldout.json")
    ra_d, ra_h = _load("ratchet_r3.json"), _load("ratchet_r3_heldout.json")
    sc_d, sc_h = _load("scale_x4.json"), _load("scale_x4_heldout.json")
    qu_d, qu_h = _load("quadratic_f4.json"), _load("quadratic_f4_heldout.json")
    to_d, to_h = _load("together_t1.json"), _load("together_t1_heldout.json")

    def rung(art: dict, name: str) -> dict | None:
        for r in art.get("ladder", []) or []:
            if isinstance(r, dict) and r.get("policy") == name:
                return r
        return None

    ab_rd, ab_rh = rung(ab_d, "decidable-only"), rung(ab_h, "decidable-only")

    rows: list[dict] = [
        {"mechanism": "blast_radius", "metric": "recall", "gate": "> 0.50",
         "dev": _point(br_d.get("recall")), "held": _point(br_h.get("recall")),
         "direction": "higher is better"},
        {"mechanism": "blast_radius", "metric": "precision (decidable)", "gate": ">= 0.50",
         "dev": _point(br_d.get("precision")), "held": _point(br_h.get("precision")),
         "direction": "higher is better"},
        {"mechanism": "blast_radius", "metric": "fires on the fixed tree", "gate": "-- (paired control)",
         "dev": _point(br_d.get("paired_negative")) or (br_d.get("paired_negative") or {}).get("rate"),
         "held": (br_h.get("paired_negative") or {}).get("rate"),
         "direction": "lower is better"},
        {"mechanism": "blast_radius", "metric": "silence-pool flag rate", "gate": "<= 0.05",
         "dev": _point(br_d.get("silence")), "held": _point(br_h.get("silence")),
         "direction": "lower is better"},
        {"mechanism": "abstention decidable-only", "metric": "recall", "gate": "reported",
         "dev": _point((ab_rd or {}).get("recall")), "held": _point((ab_rh or {}).get("recall")),
         "direction": "higher is better"},
        {"mechanism": "abstention decidable-only", "metric": "flag rate", "gate": "<= 0.05",
         "dev": (ab_d.get("outcome") or {}).get("flag_rate"),
         "held": (ab_h.get("outcome") or {}).get("flag_rate"),
         "direction": "lower is better"},
        {"mechanism": "complexity ratchet", "metric": "flag rate", "gate": "<= 0.05",
         "dev": (ra_d.get("silence") or {}).get("rate"),
         "held": (ra_h.get("silence") or {}).get("rate"),
         "direction": "lower is better"},
        {"mechanism": "complexity ratchet", "metric": "agreement with ruff", "gate": "> 0.50",
         "dev": _point((ra_d.get("agreement") or {}).get("fires_on_rose")),
         "held": _point((ra_h.get("agreement") or {}).get("fires_on_rose")),
         "direction": "higher is better"},
        {"mechanism": "complexity ratchet", "metric": "fires on ruff-flat", "gate": "-- (contrast)",
         "dev": _point((ra_d.get("agreement") or {}).get("fires_on_flat")),
         "held": _point((ra_h.get("agreement") or {}).get("fires_on_flat")),
         "direction": "lower is better"},
        {"mechanism": "scale", "metric": "corpus flag rate", "gate": "exploratory",
         "dev": (sc_d.get("corpus_silence") or {}).get("rate"),
         "held": (sc_h.get("corpus_silence") or {}).get("rate"),
         "direction": "lower is better"},
        {"mechanism": "scale", "metric": "recall on found quadratics", "gate": "n >= 20 to score",
         "dev": _point(qu_d.get("recall_on_found_quadratics")),
         "held": _point(qu_h.get("recall_on_found_quadratics")),
         "direction": "higher is better"},
        {"mechanism": "scale", "metric": "still fires after the fix", "gate": "-- (paired control)",
         "dev": (qu_d.get("paired_arm") or {}).get("rate"),
         "held": (qu_h.get("paired_arm") or {}).get("rate"),
         "direction": "lower is better"},
        {"mechanism": "the union", "metric": "flag rate", "gate": "<= 0.05",
         "dev": _point(to_d.get("union_flag_rate")), "held": _point(to_h.get("union_flag_rate")),
         "direction": "lower is better"},
    ]

    for r in rows:
        dev, held = r.get("dev"), r.get("held")
        if isinstance(dev, (int, float)) and isinstance(held, (int, float)):
            r["delta"] = round(held - dev, 4)
            r["ratio"] = round(held / dev, 3) if dev else None
            better = r["direction"].startswith("higher")
            r["moved"] = ("better" if (held > dev) == better else "worse") \
                if abs(held - dev) > 1e-9 else "flat"
        r["dev"] = round(dev, 4) if isinstance(dev, (int, float)) else None
        r["held"] = round(held, 4) if isinstance(held, (int, float)) else None
    return rows


def between_blocks() -> dict:
    """What property of a codebase governs precision? Twenty-seven blocks, ranked."""
    br = _load("blast_radius_d5_heldout.json").get("by_repo") or {}
    manifest = _load("heldout_manifest.json").get("kept") or []
    meta = {m["dir"]: m for m in manifest if isinstance(m, dict) and "dir" in m}

    blocks: list[dict] = []
    for name, node in br.items():
        m = meta.get(name)
        prec = node.get("precision") if isinstance(node, dict) else None
        rec = node.get("recall") if isinstance(node, dict) else None
        if not m or not isinstance(prec, list) or not prec:
            continue
        blocks.append({
            "block": name,
            "domain": m.get("domain"),
            "n": node.get("n"),
            "precision": prec[0],
            "precision_ci": prec[1:],
            "recall": rec[0] if isinstance(rec, list) and rec else None,
            "contributors": m.get("contributors"),
            "total_commits": m.get("total_commits"),
            "py_commits": m.get("py_commits"),
            "py_share": (m["py_commits"] / m["total_commits"])
                        if m.get("total_commits") else None,
            "disk_mb": m.get("disk_mb"),
        })

    predictors = ("contributors", "total_commits", "py_commits", "py_share", "disk_mb", "n")
    correlations = []
    for p in predictors:
        pairs = [(b[p], b["precision"]) for b in blocks
                 if isinstance(b.get(p), (int, float)) and isinstance(b["precision"], float)]
        if len(pairs) < 8:
            continue
        x, y = np.array([a for a, _ in pairs]), np.array([b for _, b in pairs])
        rho, p_value = stats.spearmanr(x, y)
        correlations.append({
            "predictor": p, "n_blocks": len(pairs),
            "spearman_rho": round(float(rho), 3),
            "p": float(f"{p_value:.4g}"),
            "reading": "monotone with precision" if abs(rho) >= 0.5 else "weak or none",
        })
    correlations.sort(key=lambda c: -abs(c["spearman_rho"]))

    by_domain: dict[str, list[float]] = {}
    for b in blocks:
        if b.get("domain") and isinstance(b["precision"], float):
            by_domain.setdefault(b["domain"], []).append(b["precision"])
    domains = [{"domain": d, "n_blocks": len(v),
                "median_precision": round(float(np.median(v)), 3),
                "spread": [round(min(v), 3), round(max(v), 3)]}
               for d, v in sorted(by_domain.items())]

    kruskal = None
    groups = [v for v in by_domain.values() if len(v) >= 2]
    if len(groups) >= 3:
        h, p_value = stats.kruskal(*groups)
        kruskal = {"h": round(float(h), 3), "p": float(f"{p_value:.4g}"),
                   "groups": len(groups),
                   "reading": "domain separates precision" if p_value < 0.05
                              else "domain does not separate precision"}

    # The obvious confound: the size predictors are collinear with each other AND with
    # domain -- the data and scientific blocks are numpy, pandas, scipy, the largest
    # repositories in the corpus. Reporting "domain separates precision" without asking
    # whether domain is size wearing a label would be the same error as reporting a
    # ratchet's recall against its own metric.
    confound = None
    sized = [b for b in blocks
             if isinstance(b.get("disk_mb"), (int, float)) and b["disk_mb"] > 0
             and isinstance(b["precision"], float) and b.get("domain")]
    if len(sized) >= 12:
        x = np.log10(np.array([b["disk_mb"] for b in sized]))
        y = np.array([b["precision"] for b in sized])
        slope, intercept = np.polyfit(x, y, 1)
        residual = y - (slope * x + intercept)
        by_dom_res: dict[str, list[float]] = {}
        for b, r in zip(sized, residual):
            by_dom_res.setdefault(b["domain"], []).append(float(r))
        groups_res = [v for v in by_dom_res.values() if len(v) >= 2]
        if len(groups_res) >= 3:
            h, p_value = stats.kruskal(*groups_res)
            confound = {
                "control": "precision residualised on log10(disk_mb)",
                "size_slope": round(float(slope), 4),
                "h": round(float(h), 3), "p": float(f"{p_value:.4g}"),
                "reading": "domain survives the size control" if p_value < 0.05
                           else "domain does NOT survive the size control -- it was size",
            }

    prec = [b["precision"] for b in blocks if isinstance(b["precision"], float)]
    return {
        "n_blocks": len(blocks),
        "precision_spread": {"min": round(min(prec), 3), "max": round(max(prec), 3),
                             "median": round(float(np.median(prec)), 3),
                             "fold": round(max(prec) / min(prec), 1) if min(prec) else None},
        "correlations": correlations,
        "collinearity_note": "disk_mb, py_commits and total_commits are three spellings of "
                             "size and must not be read as three independent findings",
        "by_domain": domains,
        "kruskal_across_domains": kruskal,
        "domain_after_size_control": confound,
        "blocks": sorted(blocks, key=lambda b: -(b["precision"] or 0)),
    }


def analyse() -> dict:
    return {"generated": "2026-08-29", "task": "V1/V2 analysis",
            "deltas": deltas(), "between_blocks": between_blocks()}


def _print(d: dict) -> None:
    print("The delta -- in sample against out of sample\n")
    print(f"  {'mechanism':26s} {'metric':28s} {'gate':18s} "
          f"{'dev':>7s} {'held':>7s} {'move':>7s}")
    for r in d["deltas"]:
        dev = f"{r['dev']:.4f}" if isinstance(r["dev"], float) else "--"
        held = f"{r['held']:.4f}" if isinstance(r["held"], float) else "--"
        print(f"  {r['mechanism']:26s} {r['metric']:28s} {r['gate']:18s} "
              f"{dev:>7s} {held:>7s} {r.get('moved', '--'):>7s}")

    b = d["between_blocks"]
    print(f"\nBetween blocks -- {b['n_blocks']} held-out repositories")
    s = b["precision_spread"]
    print(f"  precision {s['min']} to {s['max']} (median {s['median']}, "
          f"{s['fold']}-fold)")
    print("\n  predictor        rho      p      n   reading")
    for c in b["correlations"]:
        print(f"  {c['predictor']:14s} {c['spearman_rho']:6.3f} {c['p']:8.4g} "
              f"{c['n_blocks']:3d}   {c['reading']}")
    print("\n  domain            blocks  median  spread")
    for dm in b["by_domain"]:
        print(f"  {dm['domain']:18s} {dm['n_blocks']:4d}  {dm['median_precision']:6.3f}  "
              f"{dm['spread']}")
    if b["kruskal_across_domains"]:
        k = b["kruskal_across_domains"]
        print(f"\n  Kruskal-Wallis across domains: H = {k['h']}, p = {k['p']} -- {k['reading']}")
    if b.get("domain_after_size_control"):
        c = b["domain_after_size_control"]
        print(f"  after controlling for size:     H = {c['h']}, p = {c['p']} -- {c['reading']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)
    if args.show and ARTIFACT.exists():
        _print(json.loads(ARTIFACT.read_text(encoding="utf-8")))
        return 0
    data = analyse()
    ARTIFACT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8", newline="\n")
    _print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
