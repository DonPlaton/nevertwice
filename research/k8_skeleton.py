#!/usr/bin/env python3
"""K8 step 0 - can a skeleton test tell a replacement from a different fact on the same topic?

Input: the same-slug collision pairs `research/k8_collisions.py` recorded (both statements, the
case's truth). For each pair the two descriptions are reduced to their *skeleton* - the `[facts]`
block and every literal-shaped span removed (paths, versions, numbers, identifiers), digits
dropped, stop words and the extractor's boilerplate dropped, the rest stemmed - and the two
skeletons are compared. A replacement ("traces go to Tempo" then "traces go to Jaeger") keeps
its skeleton and changes a slot; a different fact ("traces to Tempo" beside "logs to Loki") has
another skeleton. Whether that holds on what the extractor actually writes is what this measures:
the ROC curve and its AUC per corpus and per branch (`d` same day, `r` another day), the
threshold at which no different-fact pair would be absorbed (over-retraction 0 by construction),
and the stale rate the sixty supersession cases would read at that threshold. The AUC decides how
K8's stale cap is worded (ledger K8, step 0).

Truth is read per pair, not per case: on a control the extractor of session two often re-emits
session one's fact from the project context, and absorbing that *restatement* serves the same
fact again - only a session-two item that does not carry the still-true fact is the different
fact whose absorb costs the metric. Several skeleton variants and similarity measures are scored
side by side, and the threshold read on one corpus is applied to the other. CPU only, seconds.

    python research/k8_skeleton.py research/results/k8_collisions_explicit.json \
        research/results/k8_collisions_implicit.json research/results/k8_collisions_asof.json \
        --out research/results/k8_step0.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402 - the declaration the sandbox lint reads, before any nevertwice import
sandbox_guard.isolate(prefix="nevertwice_k8_")     # a read-only script: no store path may leak into the engine
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402
import stemmer  # noqa: E402

# Words the extractor puts into most descriptions regardless of the fact: the frame around a
# statement, not its subject. A skeleton keeps subject words, and these are not subject words.
BOILERPLATE = frozenset("""
this that these those the a an and or but of to in on for with from into by as at is are was were
be been being has have had do does did will would should could may might must can shall not no
it its they their them we our us you your he she his her
pattern decision mistake lesson note session project team work working configuration configured
config setting settings set setup ensure ensures ensured ensuring provide provides provided
providing allow allows allowed allowing make makes made making use uses used using
confirm confirmed confirms confirming validate validated validates validation verify verified
verifies verification document documented documents documenting record recorded records
implement implemented implementation approach practice practices process change changed changes
current currently now previously prior earlier later new old existing specific specified
important critical key main primary standard consistent consistency proper properly correct
correctly appropriate reliable reliability performance improve improved improves improvement
support supported supports future avoid prevent prevents reduce reduces help helps
""".split())

_LIT_SPACE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
_FACTS_MARK = "[facts]"
MEASURES = ("jaccard", "jaccard_idf", "overlap_min", "shared", "shared_idf")
VARIANTS = ("plain", "noprop", "facts", "both")


def strip_facts(desc: str) -> str:
    return re.sub(r"\s*\[facts\].*$", "", desc or "", flags=re.DOTALL)


def strip_literals(text: str) -> str:
    """Remove every literal-shaped span the facts harvester would have quoted: the slot values."""
    for rx in m._LIT_PATTERNS:
        text = rx.sub(" ", text)
    return _LIT_SPACE.sub(" ", text)


def _capitalised(text: str) -> set:
    """Tokens written with a capital letter other than at a sentence start: proper nouns, the
    product and place names that fill a slot ("Tempo", "Jaeger", "Redis")."""
    out = set()
    for mo in re.finditer(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{2,})\b", text):
        out.add(mo.group(1).lower())
    return out


def facts_text(desc: str) -> str:
    """The `[facts]` block of a description as one string: the session's own words, quoted
    verbatim, not the extractor's paraphrase."""
    if _FACTS_MARK not in (desc or ""):
        return ""
    return (desc or "").split(_FACTS_MARK, 1)[1].replace("·", " ")


def skeleton(desc: str, variant: str = "plain") -> set:
    """The stemmed content words of a description with literals, digits, stop words and the
    extractor's boilerplate removed. `noprop` also drops mid-sentence capitalised words; `facts`
    reads the `[facts]` block alone when the description has one (the description otherwise);
    `both` reads description and block together."""
    if variant == "facts":
        text = facts_text(desc) or strip_facts(desc)
    elif variant == "both":
        text = f"{strip_facts(desc)} {facts_text(desc)}"
    else:
        text = strip_facts(desc)
    text = strip_literals(text)
    drop = _capitalised(text) if variant == "noprop" else set()
    toks = [t.lower() for t in _WORD_RE.findall(text)]
    toks = [t for t in toks if t not in BOILERPLATE and t not in m._FACT_STOP
            and not stemmer.is_stop(t) and t not in drop]
    return set(m._morph(toks))


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


def overlap_min(a: set, b: set) -> float:
    """Shared words over the shorter skeleton: a long filler-laden description does not dilute a
    short one it agrees with."""
    return len(a & b) / min(len(a), len(b)) if (a and b) else 0.0


def shared_idf(a: set, b: set, idf: dict) -> float:
    """The mass of what the two skeletons share, in IDF: two rare subject words in common weigh
    more than five common ones; not normalised by the union."""
    return sum(idf.get(t, 1.0) for t in a & b)


def weighted_jaccard(a: set, b: set, idf: dict) -> float:
    u = a | b
    if not u:
        return 0.0
    return sum(idf.get(t, 1.0) for t in a & b) / sum(idf.get(t, 1.0) for t in u)


def similarity(a: set, b: set, measure: str, idf: dict) -> float:
    if measure == "jaccard":
        return jaccard(a, b)
    if measure == "jaccard_idf":
        return weighted_jaccard(a, b, idf)
    if measure == "overlap_min":
        return overlap_min(a, b)
    if measure == "shared":
        return float(len(a & b))
    return shared_idf(a, b, idf)


def auc(pos: list[float], neg: list[float]) -> float | None:
    """Mann-Whitney: P(score of a same-fact pair > score of a different-fact pair), ties half."""
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            wins += 1.0 if p > n else (0.5 if p == n else 0.0)
    return round(wins / (len(pos) * len(neg)), 4)


def roc_points(pos: list[float], neg: list[float]) -> list[dict]:
    pts = []
    for t in sorted(set(pos + neg), reverse=True):
        tpr = sum(1 for p in pos if p >= t) / len(pos)
        fpr = sum(1 for n in neg if n >= t) / len(neg)
        pts.append({"t": round(t, 4), "tpr": round(tpr, 4), "fpr": round(fpr, 4)})
    return pts


def _hit(markers: list[str], text: str) -> bool:
    low = re.sub(r"[\s_\-]+", " ", text.lower())
    return any(re.sub(r"[\s_\-]+", " ", mk.lower()) in low for mk in markers)


def label_pairs(pairs: list[dict], corpus: dict) -> None:
    """Pair-level truth, finer than the case's. On a control the extractor of session two often
    re-emits session one's fact from the project context (the item carries the control's own
    marker): a *restatement*, whose absorb serves the same fact again - harmless. Only a
    session-two item that does NOT carry the still-true fact is a different fact whose absorb costs
    the metric. On a supersession case the pair is the replacement only when the old side carries
    the retracted marker and the new side the current one; anything else is a boilerplate item
    under the fact's title (`unclear`)."""
    for p in pairs:
        c = corpus.get(p["case"], {})
        if p["truth"] == "separate":
            restates = _hit(c.get("current", []), f"{p['new_title']} {p['new_desc']}")
            p["pair_truth"] = "restates" if restates else "separate"
        else:
            p["pair_truth"] = "replaces" if (p["old_marked"] and p["new_marked"]) else "unclear"


def evaluate(pairs: list[dict], rows: list[dict], variant: str, measure: str, idf: dict) -> dict:
    key = f"sim_{variant}_{measure}"
    for p in pairs:
        a, b = skeleton(p["old_desc"], variant), skeleton(p["new_desc"], variant)
        p[key] = round(similarity(a, b, measure, idf), 4)
    rep = [p[key] for p in pairs if p["pair_truth"] == "replaces"]
    res = [p[key] for p in pairs if p["pair_truth"] == "restates"]
    sep = [p[key] for p in pairs if p["pair_truth"] == "separate"]
    out = {"n": {"replaces": len(rep), "restates": len(res), "separate": len(sep),
                 "unclear": sum(1 for p in pairs if p["pair_truth"] == "unclear")},
           "auc_same_vs_separate": auc(rep + res, sep),      # same fact (replaced or restated) vs a different one
           "auc_replaces_vs_separate": auc(rep, sep)}
    if sep:
        t0 = max(sep)                          # absorb only above the highest different-fact pair
        out["t0"] = round(t0, 4)
        out["replaces_kept_apart_at_t0"] = sum(1 for s in rep if s <= t0)
        out["restates_kept_apart_at_t0"] = sum(1 for s in res if s <= t0)
        # stale on the supersession cases at t0: a case whose replacement pair(s) all read <= t0
        # keeps session one's note live and served (two or three notes a project, k = 5)
        sup_rows = [r for r in rows if r.get("shape") != "control"]
        base_stale = {r["id"] for r in sup_rows if r.get("stale_returned")}
        by_case: dict[str, list[float]] = {}
        for p in pairs:
            if p["pair_truth"] == "replaces":
                by_case.setdefault(p["case"], []).append(p[key])
        kept = {c for c, ss in by_case.items() if all(s <= t0 for s in ss)}
        out["stale_at_t0"] = {"baseline_stale_cases": len(base_stale), "kept_apart_cases": len(kept),
                              "stale_cases": len(base_stale | kept), "n_cases": len(sup_rows),
                              "rate": round(len(base_stale | kept) / len(sup_rows), 4) if sup_rows else None}
    out["roc"] = roc_points(rep + res, sep) if (rep or res) and sep else []
    return out


def cross_check(src: list[dict], dst: list[dict], key: str) -> dict:
    """The threshold read on one corpus applied to another: different-fact pairs it would absorb
    (over-retraction) and replacement pairs it would keep apart (stale)."""
    sep_src = [p[key] for p in src if p["pair_truth"] == "separate"]
    if not sep_src:
        return {}
    t0 = max(sep_src)
    return {"t0_from_source": round(t0, 4),
            "separate_absorbed": sum(1 for p in dst if p["pair_truth"] == "separate" and p[key] > t0),
            "separate_total": sum(1 for p in dst if p["pair_truth"] == "separate"),
            "replaces_kept_apart": sum(1 for p in dst if p["pair_truth"] == "replaces" and p[key] <= t0),
            "replaces_total": sum(1 for p in dst if p["pair_truth"] == "replaces")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default="")
    ap.add_argument("--examples", type=int, default=4, help="hardest pairs to print per side")
    ap.add_argument("--show", default="plain:overlap_min", help="variant:measure the examples are ranked by")
    a = ap.parse_args()
    stands: dict[str, dict] = {}
    corpus: dict[str, dict] = {}
    all_desc: list[str] = []
    for f in a.files:
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        name = Path(f).stem.replace("k8_collisions_", "")
        stands[name] = d
        ds = ROOT / d["dataset"]["path"]
        if ds.exists():
            for c in json.loads(ds.read_text(encoding="utf-8"))["cases"]:
                corpus[c["id"]] = c
        for p in d["pairs"]:
            all_desc += [p["old_desc"], p["new_desc"]]
    # IDF over every recorded description (the pool a store's own BM25 statistics would give)
    df: Counter = Counter()
    for desc in all_desc:
        for t in skeleton(desc):
            df[t] += 1
    n_doc = max(1, len(all_desc))
    idf = {t: math.log((n_doc + 1) / (c + 1)) + 1.0 for t, c in df.items()}
    show_variant, show_measure = a.show.split(":")
    show_key = f"sim_{show_variant}_{show_measure}"
    report = {"variants": list(VARIANTS), "measures": list(MEASURES), "stands": {},
              "boilerplate_top_df": df.most_common(30)}
    for name, d in stands.items():
        pairs, rows = d["pairs"], d["rows"]
        label_pairs(pairs, corpus)
        res = {"engine_commit": d.get("engine_commit"), "n_cases": d["n_cases"], "n_pairs": len(pairs),
               "by_truth": d["by_truth"], "by_branch": d["by_branch"],
               "by_pair_truth": dict(Counter(p["pair_truth"] for p in pairs)),
               "facts_on_both_sides": {t: sum(1 for p in pairs if p["pair_truth"] == t and p["old_facts"] and p["new_facts"])
                                       for t in ("replaces", "restates", "separate")},
               "baseline": {k: d["score"].get(k) for k in
                            ("stale_rate", "current_rate", "over_retraction_rate", "control_miss_rate",
                             "both_correct_rate", "old_day_rate", "new_day_rate", "old_day_failures_by_kind",
                             "s0_retired_rate", "s0_retired_via") if d["score"].get(k) is not None},
               "results": {}}
        ctl_rows = [r for r in rows if r.get("shape") == "control"]
        if ctl_rows and any("store" in r for r in ctl_rows):
            res["controls_r_branch"] = {
                "n": len(ctl_rows),
                "s0_written": sum(1 for r in ctl_rows if ((r.get("store") or {}).get("s0") or {}).get("written")),
                "s0_retired": sum(1 for r in ctl_rows if ((r.get("store") or {}).get("s0") or {}).get("retired")),
                "s0_absorbed": sum(1 for r in ctl_rows if ((r.get("store") or {}).get("s0") or {}).get("absorbed")),
                "s0_retired_via": dict(Counter(v for r in ctl_rows
                                               for v in ((r.get("store") or {}).get("s0") or {}).get("superseded_via", [])))}
        elif ctl_rows:
            res["controls"] = {"n": len(ctl_rows),
                               "current_retired": sum(1 for r in ctl_rows if r.get("current_retired")),
                               "current_demoted": sum(1 for r in ctl_rows if r.get("current_demoted")),
                               "current_live": sum(1 for r in ctl_rows if r.get("current_live"))}
        for variant in VARIANTS:
            for measure in MEASURES:
                r = evaluate(pairs, rows, variant, measure, idf)
                k2 = f"sim_{variant}_{measure}"
                for br in ("d", "r"):
                    sub = [p for p in pairs if p["branch"] == br]
                    if sub:
                        r[f"auc_branch_{br}"] = auc([p[k2] for p in sub if p["pair_truth"] in ("replaces", "restates")],
                                                    [p[k2] for p in sub if p["pair_truth"] == "separate"])
                res["results"][f"{variant}:{measure}"] = r
        report["stands"][name] = res
        hard = {"separate_most_similar": sorted((p for p in pairs if p["pair_truth"] == "separate"),
                                                key=lambda p: -p[show_key])[:a.examples],
                "replaces_least_similar": sorted((p for p in pairs if p["pair_truth"] == "replaces"),
                                                 key=lambda p: p[show_key])[:a.examples]}
        res["examples"] = {k: [{"case": p["case"], "sim": p[show_key], "branch": p["branch"],
                                "old": strip_facts(p["old_desc"])[:160], "new": strip_facts(p["new_desc"])[:160],
                                "old_skel": sorted(skeleton(p["old_desc"], show_variant)),
                                "new_skel": sorted(skeleton(p["new_desc"], show_variant))}
                               for p in v] for k, v in hard.items()}
    # thresholds carried across corpora
    names = list(stands)
    report["cross"] = {}
    for variant in VARIANTS:
        for measure in MEASURES:
            key = f"sim_{variant}_{measure}"
            for src in names:
                for dst in names:
                    if src != dst:
                        cc = cross_check(stands[src]["pairs"], stands[dst]["pairs"], key)
                        if cc:
                            report["cross"][f"{variant}:{measure} {src}->{dst}"] = cc
    # print
    for name, res in report["stands"].items():
        print(f"\n== {name}: {res['n_cases']} cases, {res['n_pairs']} pairs; by case {res['by_truth']}; "
              f"by pair {res['by_pair_truth']}; branches {res['by_branch']}")
        print(f"   baseline: {json.dumps(res['baseline'])}")
        print(f"   facts on both sides: {res['facts_on_both_sides']}")
        for k2 in ("controls", "controls_r_branch"):
            if k2 in res:
                print(f"   {k2}: {res[k2]}")
        for lab, r in res["results"].items():
            st = r.get("stale_at_t0", {})
            print(f"   {lab:22s} AUC same/sep {r['auc_same_vs_separate']!s:6.6} repl/sep {r['auc_replaces_vs_separate']!s:6.6}"
                  + "".join(f" {br}:{r.get(f'auc_branch_{br}')!s:6.6}" for br in ("d", "r") if r.get(f"auc_branch_{br}") is not None)
                  + f" | t0 {r.get('t0')} kept apart repl {r.get('replaces_kept_apart_at_t0')}/{r['n']['replaces']}"
                    f" rest {r.get('restates_kept_apart_at_t0')}/{r['n']['restates']}"
                  + f" | stale@t0 {st.get('rate')} ({st.get('baseline_stale_cases')}+{st.get('kept_apart_cases')} of {st.get('n_cases')})")
        for side, exs in res["examples"].items():
            print(f"   -- {side} ({a.show})")
            for e in exs:
                print(f"      {e['case']} [{e['branch']}] sim {e['sim']}\n         old: {e['old']}\n         new: {e['new']}"
                      f"\n         skel old {e['old_skel']}\n         skel new {e['new_skel']}")
    if report["cross"]:
        print("\n== thresholds carried across corpora")
        for k2, v in report["cross"].items():
            print(f"   {k2:45s} t0 {v['t0_from_source']}: separate absorbed {v['separate_absorbed']}/{v['separate_total']},"
                  f" replaces kept apart {v['replaces_kept_apart']}/{v['replaces_total']}")
    print("\nboilerplate by document frequency:", report["boilerplate_top_df"][:30])
    if a.out:
        import _provenance as prov  # noqa: PLC0415 - (б) b-c: measured_at on every register artifact
        prov.stamp(report)
        Path(a.out).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print("written", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
