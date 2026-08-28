"""D5: precision AND recall for `blast_radius`, against `git grep`, on a powered corpus.

Every threshold, the n it is evaluated at, and the test used are fixed in
[`PREREGISTRATION.md`](PREREGISTRATION.md) §3 and are not read from here. This file
runs them.

## The four arms

* **mutant** -- one commit per source commit (seed 20260827), with the caller half of
  the maintainer's edit reverted. A stale caller is present and its identity is known.
* **paired real** -- the same commit, unmutated. Same repository, same diff, same
  symbols, same size; differs only in whether the caller was updated. A checker that
  names the symbol in both trees is responding to the contract change, not to the
  stale caller.
* **silence pool** -- commits that changed a contract and updated no in-repo caller.
  The checker must stay quiet.
* **`git grep`** -- the named cheap baseline, handed the symbol list for free and
  thresholded so its flag rate matches the checker's.

## What counts

A **finding** is what the checker reports as a *problem* -- a high-confidence
unhandled reference. Low-confidence references become notes, and counting a note as
a finding would measure the checker's diagnostics rather than its verdict.

A finding is **TRUE** when the symbol it names really has, in that tree, a call that
cannot bind or an import that cannot resolve. That is decided by `sigscan` plus the
arity rule CPython's binder agreed with 868 times out of 868 -- never by hand. The
previous census hand-labelled 26 of 162 sites and found five defects in its own
labeller while doing it.

    python research/invariants_lab/measure_blast_radius.py
    python research/invariants_lab/measure_blast_radius.py --print
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy import stats
from statsmodels.stats.proportion import proportion_confint

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpusio import BlobReader, corpus_repos, progress  # noqa: E402
import mutate  # noqa: E402
import sigscan  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CENSUS = Path(__file__).with_name("corpus_census.json")
MUTANTS = Path(__file__).with_name("mutants.json")
ARTIFACT = Path(__file__).with_name("blast_radius_d5.json")

SEED = 20260827          # PREREGISTRATION.md section 1
SILENCE_SAMPLE = 1000    # PREREGISTRATION.md section 3, gate D5-P4
ALPHA = 0.05

_spec = importlib.util.spec_from_file_location(
    "_nt_blast_radius_d5", ROOT / "research" / "invariants_lab" / "blast_radius_deleted.py"
)
br = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = br
_spec.loader.exec_module(br)


# --------------------------------------------------------------------------
# running one tree
# --------------------------------------------------------------------------


def _sources(blobs: BlobReader, entry: dict, revert: str | None) -> tuple[dict, dict]:
    """(before, after) for one commit, optionally holding one file at its parent."""
    paths = sorted(
        {d["path"] for d in entry["deltas"]}
        | {h["path"] for h in entry.get("caller_updates", [])}
    )
    before: dict[str, str] = {}
    after: dict[str, str] = {}
    for p in paths:
        old = blobs.read(entry["parent"], p)
        new = blobs.read(entry["sha"], p)
        if old is None or new is None:
            continue
        before[p] = old
        after[p] = old if p == revert else new
    return before, after


def findings(verdict) -> list[tuple[str, str, int]]:
    """(qualname, path, lineno) for each high-confidence unhandled reference."""
    out = []
    for qualname, refs in verdict.unhandled.items():
        for ref in refs:
            if ref.confidence == "high":
                out.append((qualname, ref.path, ref.lineno))
    return out


def is_true_finding(qualname: str, ref_path: str, lineno: int,
                    after: dict, changes_by_qual: dict, blobs, sha: str,
                    filtered: bool = False) -> bool | None:
    """Does this finding name a real breakage in this tree? None when undecidable."""
    change = changes_by_qual.get(qualname)
    if change is None:
        return None
    short = qualname.rsplit(".", 1)[-1]
    src = after.get(ref_path)
    if src is None:
        src = blobs.read(sha, ref_path)
    if src is None:
        return None

    if change.reason == "removed":
        defining = after.get(change.path, "")
        if "." in qualname:
            # A method or a nested name. `scan_defs` keys are qualnames, so asking
            # whether the SHORT name is absent says "yes" for every method that
            # still exists -- and even with that fixed, whether an arbitrary
            # receiver reaches THIS class's member is not decidable from a name.
            # Undecidable, and counted as such rather than as a point for either side.
            return None
        gone = (
            qualname not in sigscan.scan_defs(defining)
            and qualname not in sigscan.import_bindings(defining)
        )
        if not gone:
            return False
        # A mention is not a breakage. The finding is true only when this file
        # imports the symbol *from the module it left* -- the import that will
        # actually raise -- or when the reference is inside that module.
        if ref_path == change.path:
            return True
        return any(mutate._defines(s, change.path)
                   for s in mutate._import_sources(src, short))

    if change.reason != "signature":
        return None
    new_def = sigscan.scan_defs(after.get(change.path, "")).get(qualname)
    if new_def is None or new_def.kind != "func":
        return None
    sites = [s for s in sigscan.scan_refs(src, {short})
             if s.is_call and s.lineno == lineno and not s.has_star]
    if not sites:
        return None
    return any(mutate.call_fails(s, new_def) is not None for s in sites)


# --------------------------------------------------------------------------
# the cheap baseline
# --------------------------------------------------------------------------


def grep_hits(before: dict, after: dict, changed: list[str]) -> dict[str, int]:
    """`git grep` for each changed symbol: mentions on lines the diff did not touch.

    Given the symbol list for free, which is generous -- deriving it is most of what
    the checker does. This is the same construction `research/BLAST_RADIUS_DECISION.md`
    used, so the two runs compare.
    """
    out: dict[str, int] = {}
    for name in changed:
        short = name.rsplit(".", 1)[-1]
        n = 0
        for path, src in after.items():
            touched = br.changed_lines(before.get(path, ""), src)
            for i, line in enumerate(src.splitlines(), 1):
                if i in touched:
                    continue
                if re.search(rf"\b{re.escape(short)}\b", line):
                    n += 1
        out[name] = n
    return out


# --------------------------------------------------------------------------
# statistics, all declared in PREREGISTRATION.md
# --------------------------------------------------------------------------


def wilson(k: int, n: int) -> tuple[float, float, float]:
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    lo, hi = proportion_confint(k, n, alpha=ALPHA, method="wilson")
    return (k / n, float(lo), float(hi))


def binom_p(k: int, n: int, p0: float) -> float:
    if n == 0:
        return float("nan")
    return float(stats.binomtest(k, n, p0).pvalue)


def mcnemar_exact(b01: int, b10: int) -> float:
    """Exact McNemar: b01 = checker only, b10 = baseline only."""
    m = b01 + b10
    if m == 0:
        return 1.0
    return float(stats.binomtest(b01, m, 0.5).pvalue)


def cluster_bootstrap(per_cluster: list[tuple[int, int]], reps: int = 4000,
                      seed: int = SEED) -> tuple[float, float]:
    """95% interval for a ratio, resampling whole source commits."""
    if not per_cluster:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(per_cluster)
    out = []
    for _ in range(reps):
        pick = [per_cluster[rng.randrange(n)] for _ in range(n)]
        num = sum(k for k, _ in pick)
        den = sum(d for _, d in pick)
        if den:
            out.append(num / den)
    if not out:
        return (float("nan"), float("nan"))
    out.sort()
    return (out[int(0.025 * len(out))], out[int(0.975 * len(out))])


# --------------------------------------------------------------------------


def run() -> dict:
    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    by_repo_entries = {
        r["repo"]: {e["sha"]: e for e in r["eligible"]} for r in census["repos"]
    }
    mutants = [m for m in json.loads(MUTANTS.read_text(encoding="utf-8"))["mutants"]
               if m["confirmed"]]

    # PREREGISTRATION section 1: one mutant per source commit, seeded.
    by_commit: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for m in mutants:
        by_commit[(m["repo"], m["sha"])].append(m)
    rng = random.Random(SEED)
    primary = [rng.choice(sorted(v, key=lambda x: x["mid"]))
               for _k, v in sorted(by_commit.items())]
    progress(f"primary sample: {len(primary)} clusters from {len(mutants)} mutants")

    rows: list[dict] = []
    repos = {r.name: r for r in corpus_repos()}
    readers: dict[str, BlobReader] = {}
    try:
        for i, m in enumerate(primary):
            if i % 50 == 0:
                progress(f"  {i}/{len(primary)}")
            repo = repos.get(m["repo"])
            if repo is None:
                continue
            blobs = readers.setdefault(m["repo"], BlobReader(repo))
            entry = by_repo_entries[m["repo"]].get(m["sha"])
            if entry is None:
                continue

            row = {"mid": m["mid"], "repo": m["repo"], "sha": m["sha"],
                   "symbol": m["symbol"], "qualname": m["qualname"],
                   "breakage": m["breakage"], "caller_path": m["caller_path"],
                   "caller_is_test": ("/tests/" in "/" + m["caller_path"]
                                      or m["caller_path"].split("/")[-1].startswith("test_"))}

            for arm, revert in (("mutant", m["caller_path"]), ("real", None)):
                before, after = _sources(blobs, entry, revert)
                if not before:
                    continue
                v = br.check_sources(before, after, scan=after)
                by_qual = {c.qualname: c for c in v.contract_changes}
                found = findings(v)
                row[f"{arm}_findings"] = len(found)
                row[f"{arm}_hit"] = any(
                    q == m["qualname"] or q.rsplit(".", 1)[-1] == m["symbol"]
                    for q, _p, _l in found
                )
                truths = [
                    is_true_finding(q, p, l, after, by_qual, blobs, m["sha"])
                    for q, p, l in found
                ]
                row[f"{arm}_true"] = sum(1 for t in truths if t is True)
                row[f"{arm}_false"] = sum(1 for t in truths if t is False)
                row[f"{arm}_undecidable"] = sum(1 for t in truths if t is None)

                # PREREGISTRATION section 9, deviation of 2026-08-28: the exploratory,
                # non-gating arm. A finding survives only when the site cannot bind.
                # Its precision is agreement with the answer key's own rule and is NOT
                # evidence; its RECALL and its FLAG RATE are, because neither is
                # implied by the filter.
                kept = [f for f, tr in zip(found, truths) if tr is not False]
                row[f"{arm}_filtered_findings"] = len(kept)
                row[f"{arm}_filtered_hit"] = any(
                    q == m["qualname"] or q.rsplit(".", 1)[-1] == m["symbol"]
                    for q, _p, _l in kept
                )

                if arm == "mutant":
                    changed = [c.qualname for c in v.contract_changes]
                    row["grep"] = grep_hits(before, after, changed)
                    row["grep_symbol"] = row["grep"].get(m["qualname"], 0)
            rows.append(row)
    finally:
        for r in readers.values():
            r.close()

    # --- silence pool, gate D5-P4 -----------------------------------------
    progress("silence pool")
    # PREREGISTRATION section 3 declares 1,000 sampled commits, seeded. The census
    # kept more; the sample is taken down to the declared size rather than up to the
    # available one, because a plan followed only when it is convenient is not a plan.
    pool_all = [(r["repo"], e) for r in census["repos"] for e in r.get("silent", [])]
    pool_all.sort(key=lambda x: (x[0], x[1]["sha"]))
    rng_pool = random.Random(SEED)
    pool_sample = (rng_pool.sample(pool_all, SILENCE_SAMPLE)
                   if len(pool_all) > SILENCE_SAMPLE else pool_all)
    progress(f"silence pool: {len(pool_sample)} of {len(pool_all)} kept by the census")
    silence: list[dict] = []
    pool_readers: dict[str, BlobReader] = {}
    try:
        for i, (repo_name, entry) in enumerate(pool_sample):
            if i % 100 == 0:
                progress(f"  {i}/{len(pool_sample)}")
            repo = repos.get(repo_name)
            if repo is None:
                continue
            blobs = pool_readers.setdefault(repo_name, BlobReader(repo))
            before, after = _sources(blobs, entry, None)
            if not before:
                continue
            v = br.check_sources(before, after, scan=after)
            found = findings(v)
            by_qual = {c.qualname: c for c in v.contract_changes}
            truths = [is_true_finding(q, pth, ln, after, by_qual, blobs, entry["sha"])
                      for q, pth, ln in found]
            silence.append({
                "repo": repo_name, "sha": entry["sha"],
                "findings": len(found),
                "true": sum(1 for tr in truths if tr is True),
                "false": sum(1 for tr in truths if tr is False),
                "undecidable": sum(1 for tr in truths if tr is None),
                # the exploratory arm keeps a finding unless it is decidably false
                "filtered_findings": sum(1 for tr in truths if tr is not False),
            })
    finally:
        for rd in pool_readers.values():
            rd.close()

    return {"rows": rows, "silence": silence, "silence_pool_total": len(pool_all),
            "n_mutants": len(mutants), "n_clusters": len(primary)}


def _silence_summary(silence: list[dict]) -> dict:
    n = len(silence)
    flagged = sum(1 for s in silence if s["findings"] > 0)
    filt = sum(1 for s in silence if s.get("filtered_findings", 0) > 0)
    fp, flo, fhi = wilson(filt, n)
    point, lo, hi = wilson(flagged, n)
    return {
        "n": n, "flagged": flagged, "rate": point, "ci": [lo, hi],
        "findings": sum(s["findings"] for s in silence),
        "true": sum(s.get("true", 0) for s in silence),
        "false": sum(s.get("false", 0) for s in silence),
        "undecidable": sum(s.get("undecidable", 0) for s in silence),
        "filtered": {"flagged": filt, "rate": fp, "ci": [flo, fhi],
                     "p_vs_0.05": binom_p(filt, n, 0.05) if n else float("nan")},
        "p_vs_0.05": binom_p(flagged, n, 0.05) if n else float("nan"),
        "by_repo": {
            rep: {"n": sum(1 for s in silence if s["repo"] == rep),
                  "flagged": sum(1 for s in silence
                                 if s["repo"] == rep and s["findings"] > 0)}
            for rep in sorted({s["repo"] for s in silence})
        },
    }


def summarise(raw: dict) -> dict:
    rows = raw["rows"]
    n = len(rows)
    hits = sum(1 for r in rows if r.get("mutant_hit"))
    recall = wilson(hits, n)

    # PREREGISTRATION section 3, gate D5-P3: `git grep` thresholded so its FLAG RATE
    # matches the checker's. At a threshold of one mention grep fires on nearly every
    # commit and "wins" recall by having no precision at all, which is exactly why
    # the gate was written as a matched comparison rather than a raw one.
    checker_flag_rate = sum(1 for r in rows if r.get("mutant_findings", 0) > 0) / max(n, 1)
    thresholds = sorted({v for r in rows for v in r.get("grep", {}).values()} | {1})
    grep_threshold, matched_rate = thresholds[-1] if thresholds else 1, 0.0
    for th in thresholds:
        rate = sum(1 for r in rows
                   if any(v >= th for v in r.get("grep", {}).values())) / max(n, 1)
        if rate <= checker_flag_rate:
            grep_threshold, matched_rate = th, rate
            break
    grep_hit = [r for r in rows if r.get("grep_symbol", 0) >= grep_threshold]
    b01 = sum(1 for r in rows
              if r.get("mutant_hit") and r.get("grep_symbol", 0) < grep_threshold)
    b10 = sum(1 for r in rows
              if not r.get("mutant_hit") and r.get("grep_symbol", 0) >= grep_threshold)
    grep_recall = wilson(len(grep_hit), n)

    t = sum(r.get("mutant_true", 0) + r.get("real_true", 0) for r in rows)
    f = sum(r.get("mutant_false", 0) + r.get("real_false", 0) for r in rows)
    u = sum(r.get("mutant_undecidable", 0) + r.get("real_undecidable", 0) for r in rows)
    precision = wilson(t, t + f)
    per_cluster = [
        (r.get("mutant_true", 0) + r.get("real_true", 0),
         r.get("mutant_true", 0) + r.get("real_true", 0)
         + r.get("mutant_false", 0) + r.get("real_false", 0))
        for r in rows
    ]

    by_repo = {}
    for repo in sorted({r["repo"] for r in rows}):
        sub = [r for r in rows if r["repo"] == repo]
        h = sum(1 for r in sub if r.get("mutant_hit"))
        tt = sum(r.get("mutant_true", 0) + r.get("real_true", 0) for r in sub)
        ff = sum(r.get("mutant_false", 0) + r.get("real_false", 0) for r in sub)
        by_repo[repo] = {"n": len(sub), "recall": wilson(h, len(sub)),
                         "precision": wilson(tt, tt + ff)}

    strata = {}
    for label, pick in (
        ("arity", lambda r: r["breakage"] == "arity"),
        ("vanished", lambda r: r["breakage"] == "vanished"),
        ("caller is a test", lambda r: r["caller_is_test"]),
        ("caller is package code", lambda r: not r["caller_is_test"]),
    ):
        sub = [r for r in rows if pick(r)]
        h = sum(1 for r in sub if r.get("mutant_hit"))
        strata[label] = {"n": len(sub), "recall": wilson(h, len(sub))}

    paired_real_hits = sum(1 for r in rows if r.get("real_hit"))
    d01 = sum(1 for r in rows if r.get("mutant_hit") and not r.get("real_hit"))
    d10 = sum(1 for r in rows if not r.get("mutant_hit") and r.get("real_hit"))

    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_clusters": n,
        "n_mutants": raw["n_mutants"],
        "recall": {"hits": hits, "n": n, "point": recall[0],
                   "ci": [recall[1], recall[2]],
                   "p_vs_0.50": binom_p(hits, n, 0.50)},
        "precision": {"true": t, "false": f, "undecidable": u,
                      "n_findings": t + f, "point": precision[0],
                      "ci": [precision[1], precision[2]],
                      "cluster_ci": list(cluster_bootstrap(per_cluster))},
        "baseline": {"grep_threshold": grep_threshold,
                     "checker_flag_rate": checker_flag_rate,
                     "grep_flag_rate_at_threshold": matched_rate,
                     "grep_flagged": len(grep_hit),
                     "grep_recall": {"point": grep_recall[0],
                                     "ci": [grep_recall[1], grep_recall[2]]},
                     "checker_only": b01, "grep_only": b10,
                     "mcnemar_p": mcnemar_exact(b01, b10)},
        "paired_negative": {"real_arm_named_the_symbol": paired_real_hits,
                            "n": n,
                            "rate": paired_real_hits / n if n else float("nan"),
                            "mutant_only": d01, "real_only": d10,
                            "mcnemar_p": mcnemar_exact(d01, d10)},
        "filtered_arm": {
            "note": ("Exploratory, non-gating. Declared in PREREGISTRATION.md section 9 "
                     "before this run. Its precision is agreement with the answer key's "
                     "own rule and is not evidence; recall and flag rate are."),
            "recall": {
                "hits": sum(1 for r in rows if r.get("mutant_filtered_hit")),
                "n": n,
                "point": wilson(sum(1 for r in rows if r.get("mutant_filtered_hit")), n)[0],
                "ci": list(wilson(sum(1 for r in rows if r.get("mutant_filtered_hit")), n)[1:]),
            },
            "findings_kept": sum(r.get("mutant_filtered_findings", 0)
                                 + r.get("real_filtered_findings", 0) for r in rows),
            "findings_before": sum(r.get("mutant_findings", 0)
                                   + r.get("real_findings", 0) for r in rows),
            "paired_negative_rate": (
                sum(1 for r in rows if r.get("real_filtered_findings", 0) > 0) / n
                if n else float("nan")),
        },
        "by_repo": by_repo,
        "strata": strata,
        "silence": dict(_silence_summary(raw["silence"]),
                        pool_total=raw.get("silence_pool_total")),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)

    if args.show:
        d = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        r, p, b = d["recall"], d["precision"], d["baseline"]
        print(f"clusters {d['n_clusters']}  (of {d['n_mutants']} mutants)")
        print(f"recall     {r['point']:.3f}  [{r['ci'][0]:.3f}, {r['ci'][1]:.3f}]"
              f"   {r['hits']}/{r['n']}   p vs 0.50 = {r['p_vs_0.50']:.3g}")
        print(f"precision  {p['point']:.3f}  [{p['ci'][0]:.3f}, {p['ci'][1]:.3f}]"
              f"   {p['true']}/{p['n_findings']}  ({p['undecidable']} undecidable)")
        print(f"           cluster-bootstrap [{p['cluster_ci'][0]:.3f}, "
              f"{p['cluster_ci'][1]:.3f}]")
        print(f"baseline   grep at threshold {b['grep_threshold']} mention(s) "
              f"-- flag rate {b['grep_flag_rate_at_threshold']:.3f} vs the checker's "
              f"{b['checker_flag_rate']:.3f}")
        print(f"           grep recall {b['grep_recall']['point']:.3f} "
              f"[{b['grep_recall']['ci'][0]:.3f}, {b['grep_recall']['ci'][1]:.3f}]   "
              f"checker-only {b['checker_only']}  grep-only {b['grep_only']}  "
              f"McNemar p = {b['mcnemar_p']:.3g}")
        pn = d["paired_negative"]
        print(f"paired negative: the real arm named the symbol on "
              f"{pn['real_arm_named_the_symbol']}/{pn['n']} ({pn['rate']:.3f})"
              f"  mutant-only {pn['mutant_only']}  real-only {pn['real_only']}  "
              f"McNemar p = {pn['mcnemar_p']:.3g}")
        s = d.get("silence") or {}
        if s.get("n"):
            print(f"silence pool: {s['flagged']}/{s['n']} flagged ({s['rate']:.3f}) "
                  f"[{s['ci'][0]:.3f}, {s['ci'][1]:.3f}]  "
                  f"p vs 0.05 = {s['p_vs_0.05']:.3g}")
        print()
        print(f"{'block':<22}{'n':>5}{'recall':>18}{'precision':>18}")
        for repo, v in d["by_repo"].items():
            print(f"{repo:<22}{v['n']:>5}"
                  f"{v['recall'][0]:>10.3f} [{v['recall'][1]:.2f},{v['recall'][2]:.2f}]"
                  f"{v['precision'][0]:>8.3f} [{v['precision'][1]:.2f},{v['precision'][2]:.2f}]")
        print()
        fa = d.get("filtered_arm") or {}
        if fa:
            print()
            print("EXPLORATORY, non-gating -- the compatibility filter:")
            print(f"  recall     {fa['recall']['point']:.3f} "
                  f"[{fa['recall']['ci'][0]:.3f}, {fa['recall']['ci'][1]:.3f}]  "
                  f"({fa['recall']['hits']}/{fa['recall']['n']})")
            print(f"  findings   {fa['findings_kept']} kept of {fa['findings_before']}")
            sf = (d.get("silence") or {}).get("filtered") or {}
            if sf:
                print(f"  silence    {sf['flagged']}/{d['silence']['n']} flagged "
                      f"({sf['rate']:.3f}) [{sf['ci'][0]:.3f}, {sf['ci'][1]:.3f}]  "
                      f"p vs 0.05 = {sf['p_vs_0.05']:.3g}")
        print()
        for label, v in d["strata"].items():
            print(f"  {label:<26}{v['n']:>5}  recall {v['recall'][0]:.3f} "
                  f"[{v['recall'][1]:.3f}, {v['recall'][2]:.3f}]")
        return 0

    raw = run()
    out = summarise(raw)
    out["rows"] = raw["rows"]
    ARTIFACT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    progress(f"wrote {ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
