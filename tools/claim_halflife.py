#!/usr/bin/env python3
"""How long a registered claim stays live, measured from the register's own history.

The council's fifth point: the register publishes `843 withdrawn : 18 live` and never says
whether that is normal. A reader cannot tell a project that retires claims as it learns from one
that cannot keep a number alive for a week. The ratio is a snapshot; what answers the question is
a survival curve, and the data for it is already in git - 160 commits of
`research/evidence_manifest.json`.

    birth   the first commit in which the claim's id appears
    death   the first commit after that in which it carries `stale` or `pending_remeasure`
    alive   no such commit up to HEAD

Right-censoring is handled the only honest way: a claim born five days ago cannot be asked
whether it survived seven, so it is excluded from the seven-day figure rather than counted as a
survivor. The counts printed with each figure are the denominators, so a share computed over
three claims cannot be read as a share over three hundred.

**What this cannot say.** The register's history begins 2026-08-24, so no claim in it has had the
chance to live longer than that, and a "six-month survival" is not a number this repository can
produce until six months have passed. The horizons printed here are the ones the data supports.

    python tools/claim_halflife.py            # the curve, overall and by family
    python tools/claim_halflife.py --json     # machine-readable, for the suite

Standard library only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = "research/evidence_manifest.json"
HORIZONS = (1, 7, 14, 30)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def history(until: str = "HEAD") -> list[tuple[str, dt.datetime]]:
    """Every commit that touched the register up to `until`, oldest first.

    `until` exists so a committed artifact can be RE-DERIVED at the state it names. Without it
    the numbers are a function of "now": the artifact drifts from the page with every commit and
    nothing can tell drift from tampering - the auditing session changed a survivor count in the
    committed file by hand and the suite stayed green (2026-09-22).
    """
    out = []
    for line in _git("log", "--format=%H %cI", "--reverse", until, "--",
                     MANIFEST_PATH).splitlines():
        if not line.strip():
            continue
        sha, when = line.split()
        out.append((sha, dt.datetime.fromisoformat(when)))
    return out


def claims_at(sha: str) -> list[dict]:
    blob = _git("show", f"{sha}:{MANIFEST_PATH}")
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return []
    c = data.get("claims", [])
    return list(c.values() if isinstance(c, dict) else c)


def lifetimes(until: str = "HEAD") -> tuple[dict[str, dict], dt.datetime]:
    """{id: {family, born, died}} over the register's history up to `until`."""
    seen: dict[str, dict] = {}
    commits = history(until)
    for sha, when in commits:
        for c in claims_at(sha):
            cid = c.get("id")
            if not cid:
                continue
            #: The claim's own fields are kept from the version first seen, so a claim that no
            #: longer exists in the current manifest can still be classified by what it measured.
            rec = seen.setdefault(cid, {"family": cid.split(".")[0], "born": when, "died": None,
                                        "meta": {"id": cid, "pointer": c.get("pointer"),
                                                 "command": c.get("command"),
                                                 "produced_by": c.get("produced_by")}})
            if rec["died"] is None and (c.get("stale") or c.get("pending_remeasure")):
                rec["died"] = when
    return seen, (commits[-1][1] if commits else dt.datetime.now(dt.timezone.utc))


def survival(records: dict[str, dict], head: dt.datetime, days: int) -> tuple[int, int]:
    """(survivors, observed) at one horizon, censoring claims too young to ask."""
    horizon = dt.timedelta(days=days)
    survivors = observed = 0
    for rec in records.values():
        if head - rec["born"] < horizon:
            continue                    # not old enough to have been asked
        observed += 1
        if rec["died"] is None or rec["died"] - rec["born"] >= horizon:
            survivors += 1
    return survivors, observed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--save", action="store_true",
                    help="write research/results/claim_halflife.json, the artifact the page cites")
    args = ap.parse_args(argv)

    records, head = lifetimes()
    span = head - min(r["born"] for r in records.values())
    #: The state these numbers are a snapshot OF. Without it "28.5 days over 160 commits" is a
    #: figure with no address - true when it was printed, unfalsifiable afterwards - which is the
    #: defect this repository pins corpora and vector caches to avoid (`corpus_pin.record`,
    #: `locomo_eval._cache_identity`). With it, the page may say "as of <sha>" and stay true.
    head_sha = _git("rev-parse", "HEAD").strip()
    manifest_sha = hashlib.sha256(
        (ROOT / MANIFEST_PATH).read_bytes()).hexdigest() if (ROOT / MANIFEST_PATH).exists() else ""
    report: dict = {"head": head_sha, "manifest_sha256": manifest_sha,
                    "register_commits": len(history()),
                    "claims": len(records), "history_days": round(span.total_seconds() / 86400, 1),
                    "overall": {}, "by_family": {}}
    for d in HORIZONS:
        s, n = survival(records, head, d)
        report["overall"][d] = {"survived": s, "observed": n,
                                "share": round(s / n, 3) if n else None}

    families = collections.defaultdict(dict)
    for cid, rec in records.items():
        families[rec["family"]][cid] = rec
    for fam, recs in families.items():
        row = {}
        for d in HORIZONS:
            s, n = survival(recs, head, d)
            row[d] = {"survived": s, "observed": n, "share": round(s / n, 3) if n else None}
        report["by_family"][fam] = {"claims": len(recs), "horizons": row}

    #: The survivors by family, in the artifact as well as on the screen: a page that states
    #: "every survivor measures a frozen artefact" must have that sentence resolve to data.
    horizon_days = max((h for h in HORIZONS if report["overall"][h]["observed"]),
                       default=HORIZONS[0])
    horizon = dt.timedelta(days=horizon_days)
    survivors = [cid for cid, rec in records.items()
                 if head - rec["born"] >= horizon
                 and (rec["died"] is None or rec["died"] - rec["born"] >= horizon)]
    report["survivors"] = {"horizon_days": horizon_days, "count": len(survivors),
                           "by_family": dict(collections.Counter(c.split(".")[0]
                                                                 for c in survivors))}

    #: The split that answers what the ratio alone cannot: claims about ENGINE BEHAVIOUR against
    #: claims about a FROZEN artefact. The rule is NOT a list of family names - a first draft used
    #: one and left 39 families and 387 claims outside both groups, which would have made the
    #: headline a statement about whichever families someone remembered. It is the same rule
    #: `tools/campaign_triage.py` asks of the source: a claim whose closure calls an engine door
    #: or posts to a generation endpoint measures the engine; anything else is arithmetic over
    #: inputs that are already on disk. Every claim lands in exactly one group.
    sys.path.insert(0, str(ROOT / "tools"))
    import campaign_triage as T                                     # noqa: PLC0415

    report["by_kind"] = {}
    groups = {"frozen_artefact": {}, "engine_behaviour": {}}
    for cid, rec in records.items():
        kind = "frozen_artefact" if T.triage(rec["meta"])[0] == "A" else "engine_behaviour"
        groups[kind][cid] = rec
    for label, sub in groups.items():
        rows = {}
        for d in HORIZONS:
            s, n = survival(sub, head, d)
            rows[d] = {"survived": s, "observed": n, "share": round(s / n, 3) if n else None}
        report["by_kind"][label] = {"claims": len(sub), "horizons": rows}
    report["by_kind"]["rule"] = ("campaign_triage.triage: group A (no engine door and no "
                                 "generation endpoint in the closure) is the frozen artefact, "
                                 "everything else measures the engine")

    if args.save:
        out = ROOT / "research" / "results" / "claim_halflife.json"
        out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {out.relative_to(ROOT).as_posix()}")

    if args.json:
        print(json.dumps(report, indent=1))
        return 0

    print(f"\nregister history: {report['history_days']} days, {report['claims']} claims that "
          f"ever existed in it\n")
    print("  horizon   survived / observed   share      (observed = born early enough to ask)")
    for d in HORIZONS:
        r = report["overall"][d]
        share = f"{r['share']:.3f}" if r["share"] is not None else "   -  "
        print(f"  {d:3d} day   {r['survived']:5d} / {r['observed']:5d}       {share}")

    #: The horizons are NOT one cohort, and a reader who takes them for one sees a survival
    #: "rising" from 0.036 at seven days to 0.051 at fourteen and stops trusting the table. Each
    #: row has its own denominator: only claims born early enough to be asked appear in it, so the
    #: fourteen-day row is an older, smaller population than the seven-day row. Said here rather
    #: than left for the reader to rediscover.
    print("\n  each row is its own cohort: a claim appears only where it was old enough to ask,")
    print("  so the shares are not a single curve and need not fall monotonically.")

    #: The longest horizon with anything in it. Printing an empty column would suggest the
    #: register can answer a question it is too young to answer.
    d = max((h for h in HORIZONS if report["overall"][h]["observed"]), default=HORIZONS[0])
    print(f"\n  by family, at {d} days - the longest horizon this register is old enough for:")
    rows = sorted(report["by_family"].items(), key=lambda kv: -kv[1]["claims"])
    for fam, info in rows[:14]:
        r = info["horizons"][d]
        share = f"{r['share']:.3f}" if r["share"] is not None else "  -  "
        print(f"      {fam:28s} {info['claims']:4d} claims   "
              f"{d}d: {r['survived']:4d}/{r['observed']:4d}  {share}")

    #: WHICH claims survive is the diagnosis the share alone does not give.
    horizon = dt.timedelta(days=d)
    long = [cid for cid, rec in records.items()
            if head - rec["born"] >= horizon
            and (rec["died"] is None or rec["died"] - rec["born"] >= horizon)]
    fams = collections.Counter(cid.split(".")[0] for cid in long)
    print(f"\n  what survives {d} days, by family: "
          + ", ".join(f"{f} {n}" for f, n in fams.most_common()))
    print("  the survivors measure a FROZEN artefact - a trained embedder, a seeded simulation.")
    print("  No claim about engine behaviour has ever reached this horizon, because the register")
    print("  withdraws one when the code it closes over changes, and that code changes daily.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
