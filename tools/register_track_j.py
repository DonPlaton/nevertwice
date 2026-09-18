#!/usr/bin/env python3
"""Register the Track J diagnostics the campaign artifacts carry beyond their existing claims.

The as-of, supersession and frontier families already have claims that `remeasure.py --restore`
re-reads; the 2026-09-10 campaign added fields those claims do not cover and the ledger reads
its decisions from: the as-of miss decomposition (never written / unranked / paraphrase / leak),
how often session two closed session one's interval, the evidence layer's own cost, the store
bytes, and the frontier's extractor-silence fraction. This tool registers them once, from the
artifacts, under the same freshness rules as the other register tools.

    python tools/register_track_j.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "research" / "evidence_manifest.json"
sys.path.insert(0, str(ROOT / "tools"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                      # noqa: BLE001
    pass

ASOF_CMD = "python research/asof_bench.py --arms nevertwice,naive --runs 2 --out research/results/asof_v1.json"
SUP_CMD = "python research/supersession_bench.py"
FRONTIER_CMD = "python research/frontier_eval.py judge --save"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _dirty_files() -> set[str]:
    return {line[3:].strip().replace("\\", "/") for line in _git("status", "--porcelain").splitlines() if len(line) > 3}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    import math
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(max(0.0, (c - r) / d), 4), round(min(1.0, (c + r) / d), 4)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--at", metavar="COMMIT", default="",
                    help="register further fields of artifacts the register already carries at COMMIT (the "
                         "tool learned them after the run): COMMIT must be an ancestor of HEAD and no file in "
                         "a command's closure may have changed after it")
    ap.add_argument("--manifest", default=str(MANIFEST_PATH))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    existing = {c["id"] for c in manifest["claims"]}
    head = _git("rev-parse", args.at or "HEAD")
    if args.at and subprocess.run(["git", "merge-base", "--is-ancestor", head, "HEAD"], cwd=ROOT).returncode != 0:
        raise SystemExit(f"{args.at} is not an ancestor of HEAD")
    code_time = int(_git("log", "-1", "--format=%ct", head))
    import produced_by as pb                                     # noqa: PLC0415
    dirty = _dirty_files()
    new: list[dict] = []

    def add(cid, statement, value, printed, unit, n, ci, pointer, *, dataset, env, command, raw, note=None,
            derivation=None):
        if cid in existing:
            return
        closure = pb.closure(command)
        bad = sorted(p for p in closure if p in dirty)
        if bad:
            raise SystemExit(f"working tree modifies {bad[0]} - commit first")
        if args.at:
            moved = [p for p in closure
                     if subprocess.run(["git", "merge-base", "--is-ancestor",
                                        _git("log", "-1", "--format=%H", "HEAD", "--", p), head],
                                       cwd=ROOT).returncode != 0]
            if moved:
                raise SystemExit(f"{moved[0]} changed after {args.at} - the artifact no longer describes HEAD's code")
        new.append({"id": cid, "statement": statement, "value": value, "printed": list(printed), "unit": unit,
                    "n": n, "pointer": pointer, "dataset": dataset, "environment": env, "command": command,
                    "raw": raw, "cited_in": [], "commit": head, "produced_by": closure,
                    "ci": ({"method": "wilson", "level": 0.95, "low": ci[0], "high": ci[1]} if ci else None),
                    **({"note": note} if note else {}), **({"derivation": derivation} if derivation else {})})

    # ── as-of ────────────────────────────────────────────────────────────────
    raw = "research/results/asof_v1.json"
    art = json.loads((ROOT / raw).read_text(encoding="utf-8"))
    if (ROOT / raw).stat().st_mtime < code_time:
        print(f"  {raw} predates HEAD - its diagnostics were registered at an earlier commit or wait for a re-run")
        art = None
    a = (art or {}).get("arms", {}).get("nevertwice") or {}
    n = int(a.get("n_cases") or 0)
    kinds = a.get("old_day_failures_by_kind") or {}
    base = dict(dataset="supersession_v1", env="local_supersession_stand", command=ASOF_CMD, raw=raw)
    what = {"never_written": "the extractor wrote no note for the first session",
            "absorbed": "the first session's note was absorbed into the second session's (the twin gate judged the "
                        "different fact a twin; the old statement survives only under Previous statement)",
            "unranked": "a first-session note existed but nothing came back for the old day",
            "paraphrase": "the old note came back but its wording carried no marker",
            "leak": "the new fact came back for the old day"}
    for kind, desc in (what.items() if art else []):
        k = int(kinds.get(kind, 0))
        add(f"asof.nevertwice.old_day_miss.{kind}",
            f"of the as-of case-runs that missed the old day, {k} missed because {desc}",
            k, [str(k)], "case-runs", n, None, f'arms["nevertwice"].old_day_failures_by_kind.{kind}', **base)
    if a.get("s0_absorbed") is not None:
        add("asof.nevertwice.s0_absorbed",
            f"in {int(a['s0_absorbed'])} of the {n} as-of case-runs every note the first session wrote was absorbed "
            f"into the second session's note - written, then rewritten to the new fact",
            int(a["s0_absorbed"]), [str(int(a["s0_absorbed"]))], "case-runs", n, None,
            'arms["nevertwice"].s0_absorbed', **base)
    r = a.get("s0_retired_rate")
    if r is not None:
        add("asof.nevertwice.s0_retired_rate",
            f"the replacing session closed the first session's belief interval in {r * 100:.1f}% of the case-runs "
            f"where the first session had written a note",
            r, [f"{r:.3f}", f"{r:.2f}"], "rate", n - int(a.get("s0_never_written", 0)), None, 'arms["nevertwice"].s0_retired_rate',
            note=("Read from the store per case (J2 instrumentation). 0.0 at the campaign of 2026-09-10: the stand dates "
                  "the first session 193 days back, `archive_old_typed` moves a note older than 90 days out of the live "
                  "folder on every capture, and the write path reconciles only against live notes."), **base)
    # J2b: how each retirement was decided (`superseded_via`); the twin pass stays live-only, so an
    # archived note is retired only by slug or by an explicit link
    via = a.get("s0_retired_via") or {}
    n_via = int(sum(int(v) for v in via.values())) if via else 0
    for how, desc in (("slug", "the same slug - the replacing note carries the old note's name"),
                      ("explicit", "an explicit `supersedes`/`contradicts` named by the extractor")):
        if via.get(how) is not None:
            add(f"asof.nevertwice.s0_retired_via.{how}",
                f"of the first-session notes the replacing session retired, {int(via[how])} were retired via {desc}",
                int(via[how]), [str(int(via[how]))], "retirements", n_via, None,
                f'arms["nevertwice"].s0_retired_via.{how}', **base)
    # the two runs behind the pooled both-days figure: the caption under the as-of table names them,
    # because a pooled 0.800 against a gate of 0.80 is a boundary and the spread says how wide it is
    for i, word in enumerate(("one", "two")):
        per_run = a.get("per_run") or []
        if len(per_run) > i:
            add(f"asof.nevertwice.per_run.{word}",
                f"run {word} of the as-of stand read both-days-correct {per_run[i]:.3f} on its {n // max(len(per_run), 1)} cases",
                per_run[i], [f"{per_run[i]:.3f}"], "rate", n // max(len(per_run), 1), None,
                f'arms["nevertwice"].per_run[{i}]', **base)
    # the old-day half of the J2 gate (ledger J2: both-correct >= 0.80, old day >= 0.85, two runs pooled);
    # a decision, not a measurement, so it carries a raw_gap like `asof.gate.threshold`
    if art and "asof.gate.old_day_threshold" not in existing:
        closure = pb.closure(ASOF_CMD)
        new.append({"id": "asof.gate.old_day_threshold",
                    "statement": "the gate written for the old day before the as-of re-run (ledger J2) was 0.85 "
                                 "of case-runs correct on the day the superseded fact still held",
                    "value": 0.85, "printed": ["0.85"], "unit": "rate", "n": None, "pointer": None,
                    "dataset": "supersession_v1", "environment": "local_supersession_stand", "command": ASOF_CMD,
                    "raw": None, "cited_in": [], "commit": head, "produced_by": closure, "ci": None,
                    "raw_gap": "a threshold is a decision, not a measurement: it is quoted from .loop/GOAL-CLOSE.md "
                               "item J2, written before the campaign of 2026-09-10 ran; the both-days half is "
                               "`asof.gate.threshold`"})
    # the --recent control: session one dated inside the 90-day window, never archived - the rate the
    # J2b gate is measured against (shipped >= 0.80 x this and >= 0.60 absolute)
    raw_r = "research/results/asof_recent.json"
    if (ROOT / raw_r).exists() and (ROOT / raw_r).stat().st_mtime >= code_time:
        ar = (json.loads((ROOT / raw_r).read_text(encoding="utf-8")).get("arms") or {}).get("nevertwice") or {}
        rr = ar.get("s0_retired_rate")
        if rr is not None:
            add("asof.nevertwice.recent.s0_retired_rate",
                f"with session one dated inside the 90-day window (never archived), the replacing session closed its "
                f"belief interval in {rr * 100:.1f}% of the case-runs - the control the J2b gate is measured against",
                rr, [f"{rr:.3f}", f"{rr:.2f}"], "rate",
                int(ar.get("n_cases") or 0) - int(ar.get("s0_never_written", 0)), None,
                'arms["nevertwice"].s0_retired_rate',
                note=("The `--recent` control isolates 'does the write path close an interval at all' from 'does it "
                      "close one after the note was archived'."),
                dataset="supersession_v1", env="local_supersession_stand",
                command="python research/asof_bench.py --recent --arms nevertwice --runs 2 --out research/results/asof_recent.json",
                raw=raw_r)
    # ── K8: the second reading of the as-of stand - the same store after the sleep-time judge ──
    # Between nights a user sees the first reading; after consolidation, this one. Both are the
    # product, so both are registered, and the page prints them side by side rather than choosing.
    slp = ((art or {}).get("arms") or {}).get("nevertwice_after_sleep") or {}
    if slp and not slp.get("blocked") and slp.get("n_cases"):
        ns = int(slp["n_cases"])
        for key, what in (("both_correct_rate", "answers BOTH days correctly"),
                          ("old_day_rate", "answers the day the superseded fact still held"),
                          ("new_day_rate", "answers the day after the replacement")):
            v = slp[key]
            add(f"asof.nevertwice_after_sleep.{key.replace('_rate', '')}",
                f"after the sleep-time adjudication on the same store, Nevertwice {what} on {v * 100:.1f}% of "
                f"the {ns} as-of case-runs",
                v, [f"{v:.3f}"], "rate", ns, list(wilson(int(round(v * ns)), ns)),
                f'arms["nevertwice_after_sleep"].{key}',
                note=("The second of two readings declared in ledger K8: each store is asked once after the "
                      "replacing session and once after consolidation has judged its contested pairs. The "
                      "first reading is what a user sees between nights."), **base)
        r = slp.get("s0_retired_rate")
        if r is not None:
            add("asof.nevertwice_after_sleep.s0_retired_rate",
                f"after the sleep-time adjudication the first session's belief interval is closed in "
                f"{r * 100:.1f}% of the case-runs where the first session wrote a note",
                r, [f"{r:.3f}", f"{r:.2f}"], "rate", int(slp.get("s0_written_pairs") or ns), None,
                'arms["nevertwice_after_sleep"].s0_retired_rate',
                note=("The J2b metric read where K8 moved its closure: layer 1 leaves an unproven pair "
                      "contested instead of retiring on the title, so the interval closes at consolidation. "
                      "The before-sleep figure is `asof.nevertwice.s0_retired_rate`."), **base)
        via = slp.get("s0_retired_via") or {}
        n_via = int(sum(int(v) for v in via.values())) if via else 0
        for how, desc in (("judge", "the sleep-time judge ruling `replaces` on a contested pair"),
                          ("slug", "the same slug - the replacing note carries the old note's name"),
                          ("explicit", "an explicit `supersedes`/`contradicts` named by the extractor")):
            if via.get(how) is not None:
                add(f"asof.nevertwice_after_sleep.s0_retired_via.{how}",
                    f"of the first-session notes retired by the end of the second reading, {int(via[how])} "
                    f"were retired via {desc}",
                    int(via[how]), [str(int(via[how]))], "retirements", n_via, None,
                    f'arms["nevertwice_after_sleep"].s0_retired_via.{how}', **base)
        adj = slp.get("adjudication") or {}
        if adj.get("judged") is not None:
            add("asof.nevertwice_after_sleep.judge_calls",
                f"the adjudication step spent {int(adj['judged'])} judge calls on the {int(adj['pairs'])} "
                f"contested pairs of this store, leaving {int(adj.get('left', 0))} unjudged",
                int(adj["judged"]), [str(int(adj["judged"]))], "calls", int(adj["pairs"]), None,
                'arms["nevertwice_after_sleep"].adjudication.judged',
                note=(f"Under a token budget of {int(adj.get('budget', 0)):,} a run, oldest contested pair "
                      f"first; {int(adj.get('tokens_spent', 0)):,} tokens were spent, and "
                      f"{int(adj.get('vetoed', 0))} verdicts were vetoed by the replacement guards."), **base)

    zep = ((art or {}).get("arms") or {}).get("zep") or {}
    if zep and not zep.get("blocked") and zep.get("n_cases"):
        nz = int(zep["n_cases"])
        zruns = int(zep.get("runs") or 1)
        unit = f"supersession case-runs (pooled over {zruns} runs of the arm)" if zruns > 1 else "supersession cases"
        for key, what in (("both_correct_rate", "answers BOTH days correctly"), ("old_day_rate", "answers the day the superseded fact still held"),
                          ("new_day_rate", "answers the day after the replacement")):
            v = zep[key]
            add(f"asof.zep.{key.replace('_rate', '')}",
                f"Zep/Graphiti (graphiti-core, FalkorDB, edges filtered by their own valid_at/invalid_at/expired_at) {what} on "
                f"{v * 100:.1f}% of the {unit} asked as of a day",
                v, [f"{v:.3f}"], "rate", nz, list(wilson(int(round(v * nz)), nz)), f'arms["zep"].{key}', **base)
        # K2 parity: the Zep arm pooled over its runs carries the per-run values like ours
        for i, word in enumerate(("one", "two", "three")[:int(zep.get("runs") or 1)]):
            pr = zep.get("per_run") or []
            if len(pr) > i and pr[i] is not None:
                add(f"asof.zep.per_run.{word}",
                    f"run {word} of the Zep/Graphiti as-of arm read both-days-correct {pr[i]:.3f} on its "
                    f"{nz // max(int(zep.get('runs') or 1), 1)} cases",
                    pr[i], [f"{pr[i]:.3f}"], "rate", nz // max(int(zep.get("runs") or 1), 1), None,
                    f'arms["zep"].per_run[{i}]', **base)
        g = zep.get("graphiti") or {}
        if g.get("llm_calls_per_episode") is not None:
            add("asof.zep.llm_calls_per_episode",
                f"Graphiti spends {g['llm_calls_per_episode']} LLM calls per episode on the as-of stand, measured",
                g["llm_calls_per_episode"], [f"{g['llm_calls_per_episode']:.2f}", f"{g['llm_calls_per_episode']:.1f}"], "calls",
                int(g.get("episodes", 0)), None, 'arms["zep"].graphiti.llm_calls_per_episode', **base)

    # ── K3: the extractor alone on the sessions the as-of stand had marked never written ──
    raw_s = "research/results/silence_probe.json"
    if (ROOT / raw_s).exists() and (ROOT / raw_s).stat().st_mtime >= code_time:
        sp = json.loads((ROOT / raw_s).read_text(encoding="utf-8"))
        n_s = int(sp.get("silent_cases") or 0)
        kinds_s = sp.get("by_kind") or {}
        sil_cmd = "python research/silence_probe.py --save --out research/results/silence_probe.json"
        for kind, slug in (("written with the marker", "written_with_marker"),
                           ("written without the marker", "written_without_marker"),
                           ("no items returned", "no_items"),
                           ("items returned, none written", "items_none_written"),
                           ("extraction failed", "extraction_failed")):
            v = int(kinds_s.get(kind, 0))
            present = kind in kinds_s
            add(f"silence.{slug}",
                f"of the {n_s} first sessions the as-of stand had marked never written, captured alone the extractor "
                f"{kind} for {v}",
                v, [str(v)], "sessions", n_s, None, f'by_kind["{kind}"]' if present else None,
                dataset="supersession_v1", env="local_supersession_stand", command=sil_cmd, raw=raw_s,
                derivation=None if present else f"by_kind carries no entry for {kind!r}: the count is zero")
        add("silence.cases", f"the as-of stand had marked {n_s} distinct first sessions as never written",
            n_s, [str(n_s)], "sessions", n_s, None, "silent_cases",
            dataset="supersession_v1", env="local_supersession_stand", command=sil_cmd, raw=raw_s)

    # ── supersession (pooled artifact) ──────────────────────────────────────────────────
    raw = "research/results/supersession_v1.json"
    art = json.loads((ROOT / raw).read_text(encoding="utf-8"))
    if (ROOT / raw).stat().st_mtime < code_time:
        print(f"  {raw} predates HEAD - its diagnostics were registered at an earlier commit or wait for a re-run")
        art = None
    base = dict(dataset="supersession_v1", env="local_supersession_stand", command=SUP_CMD, raw=raw)
    for arm_key, run_label in ((("nevertwice", "run one"), ("nevertwice_run2", "run two")) if art else ()):
        a = art["arms"].get(arm_key) or {}
        if a.get("store_bytes") is not None:
            add(f"supersession.{arm_key}.store_bytes",
                f"the typed notes of {run_label} occupy {a['store_bytes']:,} bytes on disk",
                a["store_bytes"], [f"{a['store_bytes']:,}", str(a["store_bytes"])], "bytes", int(a.get("notes_written", 0)),
                None, f'arms["{arm_key}"].store_bytes', **base)

    # ── frontier: the extractor's silence ceiling ───────────────────────────
    raw = "research/results/frontier.json"
    art = json.loads((ROOT / raw).read_text(encoding="utf-8"))
    if (ROOT / raw).stat().st_mtime < code_time:
        print(f"  {raw} predates HEAD - the frontier diagnostics wait for its re-run")
        art = {}
    sil = art.get("extractor_silence") or {}
    if sil.get("fraction") is not None:
        q = int(sil["questions"])
        k = int(sil["questions_gold_without_notes"])
        add("frontier.nevertwice_full.extractor_silence",
            f"for {sil['fraction'] * 100:.1f}% of the frontier questions the extractor wrote no note from any gold evidence "
            f"session - the extractor's silence ceiling, which no retrieval layer can lift",
            sil["fraction"], [f"{sil['fraction']:.3f}", f"{sil['fraction'] * 100:.1f}"], "fraction of questions", q,
            wilson(k, q), "extractor_silence.fraction", dataset="longmemeval_oracle_pinned", env="local_frontier_stand",
            command=FRONTIER_CMD, raw=raw)
        add("frontier.nevertwice_full.sessions_with_zero_notes",
            f"{sil['sessions_with_zero_notes']} of the {sil['sessions']} pool sessions produced no note at all",
            int(sil["sessions_with_zero_notes"]), [str(sil["sessions_with_zero_notes"])], "sessions", int(sil["sessions"]),
            None, "extractor_silence.sessions_with_zero_notes", dataset="longmemeval_oracle_pinned",
            env="local_frontier_stand", command=FRONTIER_CMD, raw=raw)

    for c in new:
        print(f"  + {c['id']} = {c['value']}")
    if args.dry_run or not new:
        print(f"{'would register' if args.dry_run else 'registered'} {len(new)} claim(s)")
        return 0
    manifest["claims"].extend(new)
    Path(args.manifest).write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"registered {len(new)} claim(s) at {head[:7]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
