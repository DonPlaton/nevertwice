#!/usr/bin/env python3
"""Does the `[facts]` block dilute ranking? The rank of the correct note with and without it.

Ledger K1 (2026-09-11). The literal-fact channel appends the verified literals of a session to a
note's description, and finding 5a read the cost in the supersession artifact under another name:
on the implicit corpus the control misses that were *written, live and ranked below the top five*
went from three per run to seven per run when the channel arrived, while the write path's own
causes fell to zero. The sum had been reported as "over-retraction moving the wrong way"; the
split says the write path got strictly better and every point of the rise moved into ranking.

This stand measures that directly and cheaply, before the wide probe. It ingests the control
cases of a supersession corpus through the public API - the same path the bench takes - and asks
each control query twice against the same store: once as written, and once with the `[facts]`
block stripped from every note's cached text and the vectors re-embedded from the stripped text.
Nothing in the engine is touched: the variant is an index, built from the same notes.

The gate was written in `.loop/GOAL-CLOSE.md` (K1) before this ran: on the seven named cases of
finding 5a, dilution is *confirmed* if the correct note ranks worse with the block on at least six
and better on none (one-sided sign test, p <= 0.0625); anything short of that is *not confirmed*
and published as such. Every control case is ranked too, and the artifact carries both.

    python research/facts_dilution_probe.py                      # implicit corpus, controls, report
    python research/facts_dilution_probe.py --save               # + research/results/facts_dilution.json
    python research/facts_dilution_probe.py --limit 2            # smoke

Local Ollama for extraction and embedding; nothing billed, nothing leaves the machine. Sandbox
store only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import sandbox_guard  # noqa: E402 - one store sandbox for the whole repo
sandbox_guard.isolate()

import supersession_bench as sb  # noqa: E402 - the corpus loader, the marker test, the hit text

DATASET = ROOT / "research" / "data" / "supersession_v1_implicit.json"
NAMED_FROM = ROOT / "research" / "results" / "supersession_v1_implicit.json"
OUT = ROOT / "research" / "results" / "facts_dilution.json"
#: The gate as written before the run (ledger K1): worse on at least this many of the named
#: cases, and better on none.
GATE_WORSE_AT_LEAST = 6
GATE_BETTER_AT_MOST = 0


def named_cases(artifact: Path) -> list[str]:
    """The control cases of finding 5a: written, live, and not returned in either engine run."""
    if not artifact.is_file():
        return []
    art = json.loads(artifact.read_text(encoding="utf-8"))
    ids: set[str] | None = None
    for arm, res in art.get("arms", {}).items():
        if not arm.startswith("nevertwice") or res.get("blocked"):
            continue
        here = {r["id"] for r in res.get("rows", [])
                if r.get("shape") == "control" and r.get("current_live") and not r.get("current_returned")}
        ids = here if ids is None else ids & here
    return sorted(ids or [])


def rank_of_current(case: dict, hits: list[dict]) -> int | None:
    """1-based rank of the first returned note that carries a current marker; None if absent."""
    for i, h in enumerate(hits, start=1):
        if sb._hit(case.get("current", []), sb.hit_text(h)):
            return i
    return None


def strip_facts_variant(m) -> int:
    """Rewrite the embedding cache in place: the `[facts]` block removed from every description
    that carries one, the vector re-embedded from the stripped text with the cache's own document
    prefix. Returns how many records changed. The note files are not touched - recall ranks from
    the cache, and this is an index variant, not a store edit."""
    cache = m.load_embed_cache()
    kind = m.doc_embed_kind()
    changed: dict[str, dict] = {}
    for stem, rec in cache.items():
        if not isinstance(rec, dict):
            continue
        desc = rec.get("desc") or ""
        if m._FACTS_MARK.strip() not in desc:
            continue
        base = m._append_facts(desc, [])
        rec["desc"] = base
        text = f"{rec.get('title', '')}\n{base}\n{rec.get('prevention', '')}".strip()
        vec = m.embed_text(text, kind=kind, project=rec.get("project"))
        if vec:
            rec["vec"] = vec
        changed[stem] = rec
    if changed:
        m.save_embed_cache(cache)
        try:
            m.sync_scale_index(records=changed)
        except Exception as e:                                  # noqa: BLE001 - the FTS mirror is not on the ranking path here
            print(f"  (FTS mirror not refreshed: {type(e).__name__}: {e})", flush=True)
    return len(changed)


def sign_test_one_sided(worse: int, better: int) -> float:
    """P(X >= worse | n = worse + better, p = 1/2): ties carry no information."""
    n = worse + better
    if n == 0:
        return 1.0
    return sum(math.comb(n, x) for x in range(worse, n + 1)) / 2 ** n


def summarise(rows: list[dict], ids: list[str] | None = None) -> dict:
    sel = [r for r in rows if ids is None or r["id"] in ids]
    def key(r):                                                  # None ranks worst
        return r if r is not None else 10 ** 6
    worse = sum(1 for r in sel if key(r["rank_with"]) > key(r["rank_without"]))
    better = sum(1 for r in sel if key(r["rank_with"]) < key(r["rank_without"]))
    same = len(sel) - worse - better
    top_with = sum(1 for r in sel if r["rank_with"] is not None and r["rank_with"] <= r["k"])
    top_without = sum(1 for r in sel if r["rank_without"] is not None and r["rank_without"] <= r["k"])
    return {"n": len(sel), "worse_with_block": worse, "better_with_block": better, "same": same,
            "in_top_k_with": top_with, "in_top_k_without": top_without,
            "p_sign_one_sided": round(sign_test_one_sided(worse, better), 4)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--named-from", default=str(NAMED_FROM),
                    help="the pooled supersession artifact whose engine rows name the cases of finding 5a")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--depth", type=int, default=50, help="how deep to look for the correct note's rank")
    ap.add_argument("--limit", type=int, default=0, help="first N control cases only (a smoke run)")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    os.environ["NEVERTWICE_CLOUD"] = "none"
    os.environ["NEVERTWICE_MODEL"] = sb.LLM
    os.environ.setdefault("NEVERTWICE_EMBED_MODEL", sb.EMBED_MODEL)
    os.environ.setdefault("NEVERTWICE_EXTRACT_TEMP", "0")
    from nevertwice import api                                   # noqa: PLC0415 - after the sandbox
    import memory_hook as m                                      # noqa: PLC0415

    raw = Path(args.dataset).read_bytes()
    data = json.loads(raw.decode("utf-8"))
    controls = [c for c in data["cases"] if c["shape"] == "control"]
    if args.limit:
        controls = controls[:args.limit]
    named = named_cases(Path(args.named_from))
    print(f"dataset {data['name']}  controls {len(controls)}  named cases of finding 5a: {len(named)}")
    print(f"store   {sandbox_guard.store()}\n")

    t0 = time.time()
    projects: dict[str, str] = {}
    for i, case in enumerate(controls):
        project = f"dil{i:03d}"
        projects[case["id"]] = project
        for j, session in enumerate(case["sessions"]):
            api.capture_session("\n".join(session), project=project, session_id=f"{project}-s{j}", trigger="ingest")
        print(f"  ingested [{i + 1}/{len(controls)}] {case['id']}", flush=True)
    ingest_s = round(time.time() - t0, 1)

    def ranks() -> dict[str, tuple[int | None, list[str]]]:
        out = {}
        for case in controls:
            hits = api.recall(case["query"], project=projects[case["id"]], k=args.depth)
            out[case["id"]] = (rank_of_current(case, hits), [sb.hit_text(h) for h in hits[:args.k]])
        return out

    with_block = ranks()
    cache = m.load_embed_cache()
    notes_with_block = sum(1 for r in cache.values()
                           if isinstance(r, dict) and m._FACTS_MARK.strip() in (r.get("desc") or ""))
    changed = strip_facts_variant(m)
    without_block = ranks()

    rows = []
    for case in controls:
        rw, top_w = with_block[case["id"]]
        ro, top_o = without_block[case["id"]]
        rows.append({"id": case["id"], "named": case["id"] in named, "k": args.k,
                     "rank_with": rw, "rank_without": ro,
                     "returned_with": top_w, "returned_without": top_o})
        flag = " <- named" if case["id"] in named else ""
        print(f"  {case['id']:<28} with {str(rw):>4}   without {str(ro):>4}{flag}")

    named_summary = summarise(rows, named) if named else None
    all_summary = summarise(rows)
    verdict = None
    if named_summary and named_summary["n"] >= GATE_WORSE_AT_LEAST:
        verdict = ("confirmed" if named_summary["worse_with_block"] >= GATE_WORSE_AT_LEAST
                   and named_summary["better_with_block"] <= GATE_BETTER_AT_MOST else "not confirmed")
    print(f"\nnotes carrying a [facts] block: {notes_with_block} of {len(cache)}; re-embedded {changed}")
    if named_summary:
        print(f"named cases ({named_summary['n']}): worse with the block {named_summary['worse_with_block']}, "
              f"better {named_summary['better_with_block']}, same {named_summary['same']}; "
              f"in top {args.k}: {named_summary['in_top_k_with']} with, {named_summary['in_top_k_without']} without; "
              f"sign test p = {named_summary['p_sign_one_sided']} -> dilution {verdict}")
    print(f"all controls ({all_summary['n']}): worse {all_summary['worse_with_block']}, better "
          f"{all_summary['better_with_block']}, same {all_summary['same']}; in top {args.k}: "
          f"{all_summary['in_top_k_with']} with, {all_summary['in_top_k_without']} without; "
          f"p = {all_summary['p_sign_one_sided']}")

    if args.save:
        out = {"dataset": {"name": data["name"], "sha256": hashlib.sha256(raw).hexdigest(),
                           "path": str(Path(args.dataset).resolve().relative_to(ROOT)).replace("\\", "/"),
                           "control_cases": len(controls)},
               "llm": sb.LLM, "embedder": sb.EMBED_MODEL, "extract_temperature": os.environ.get("NEVERTWICE_EXTRACT_TEMP"),
               "k": args.k, "depth": args.depth, "ingest_seconds": ingest_s,
               "notes": len(cache), "notes_with_facts_block": notes_with_block, "notes_reembedded": changed,
               "named_cases": named, "named_from": str(Path(args.named_from).resolve().relative_to(ROOT)).replace("\\", "/"),
               "gate": {"worse_at_least": GATE_WORSE_AT_LEAST, "better_at_most": GATE_BETTER_AT_MOST,
                        "written": ".loop/GOAL-CLOSE.md item K1, before the run"},
               "named": named_summary, "all_controls": all_summary, "verdict": verdict, "rows": rows}
        Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
