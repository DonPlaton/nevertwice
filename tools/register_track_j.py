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
    ap.add_argument("--manifest", default=str(MANIFEST_PATH))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    existing = {c["id"] for c in manifest["claims"]}
    head = _git("rev-parse", "HEAD")
    code_time = int(_git("log", "-1", "--format=%ct", head))
    import produced_by as pb                                     # noqa: PLC0415
    dirty = _dirty_files()
    new: list[dict] = []

    def add(cid, statement, value, printed, unit, n, ci, pointer, *, dataset, env, command, raw, note=None):
        if cid in existing:
            return
        closure = pb.closure(command)
        bad = sorted(p for p in closure if p in dirty)
        if bad:
            raise SystemExit(f"working tree modifies {bad[0]} - commit first")
        new.append({"id": cid, "statement": statement, "value": value, "printed": list(printed), "unit": unit,
                    "n": n, "pointer": pointer, "dataset": dataset, "environment": env, "command": command,
                    "raw": raw, "cited_in": [], "commit": head, "produced_by": closure,
                    "ci": ({"method": "wilson", "level": 0.95, "low": ci[0], "high": ci[1]} if ci else None),
                    **({"note": note} if note else {})})

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
            "unranked": "a first-session note existed but nothing came back for the old day",
            "paraphrase": "the old note came back but its wording carried no marker",
            "leak": "the new fact came back for the old day"}
    for kind, desc in (what.items() if art else []):
        k = int(kinds.get(kind, 0))
        add(f"asof.nevertwice.old_day_miss.{kind}",
            f"of the as-of case-runs that missed the old day, {k} missed because {desc}",
            k, [str(k)], "case-runs", n, None, f'arms["nevertwice"].old_day_failures_by_kind.{kind}', **base)
    r = a.get("s0_retired_rate")
    if r is not None:
        add("asof.nevertwice.s0_retired_rate",
            f"the replacing session closed the first session's belief interval in {r * 100:.1f}% of the case-runs "
            f"where the first session had written a note",
            r, [f"{r:.3f}", f"{r:.2f}"], "rate", n - int(a.get("s0_never_written", 0)), None, 'arms["nevertwice"].s0_retired_rate',
            note=("Read from the store per case (J2 instrumentation). 0.0 at the campaign of 2026-09-10: the stand dates "
                  "the first session 193 days back, `archive_old_typed` moves a note older than 90 days out of the live "
                  "folder on every capture, and the write path reconciles only against live notes."), **base)
    zep = ((art or {}).get("arms") or {}).get("zep") or {}
    if zep and not zep.get("blocked") and zep.get("n_cases"):
        nz = int(zep["n_cases"])
        for key, what in (("both_correct_rate", "answers BOTH days correctly"), ("old_day_rate", "answers the day the superseded fact still held"),
                          ("new_day_rate", "answers the day after the replacement")):
            v = zep[key]
            add(f"asof.zep.{key.replace('_rate', '')}",
                f"Zep/Graphiti (graphiti-core, FalkorDB, edges filtered by their own valid_at/invalid_at/expired_at) {what} on "
                f"{v * 100:.1f}% of the supersession cases asked as of a day",
                v, [f"{v:.3f}"], "rate", nz, list(wilson(int(round(v * nz)), nz)), f'arms["zep"].{key}', **base)
        g = zep.get("graphiti") or {}
        if g.get("llm_calls_per_episode") is not None:
            add("asof.zep.llm_calls_per_episode",
                f"Graphiti spends {g['llm_calls_per_episode']} LLM calls per episode on the as-of stand, measured",
                g["llm_calls_per_episode"], [f"{g['llm_calls_per_episode']:.2f}", f"{g['llm_calls_per_episode']:.1f}"], "calls",
                int(g.get("episodes", 0)), None, 'arms["zep"].graphiti.llm_calls_per_episode', **base)

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
