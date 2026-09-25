#!/usr/bin/env python3
"""Why the extractor writes nothing for a two-sentence session (ledger K3): diagnosis, no code.

Eleven of the thirteen residual as-of old-day misses are `never_written` - the replacing session
cannot close an interval that was never opened - and the as-of stand cannot say why: it records
what the store holds, not what the extractor returned. This probe takes every first session the
as-of artifact marks as never written, runs the extraction prompt on it exactly as
`process_session` builds it, and records what came back: no items at all (the lesson compressor
declined a bare fact), items the writer then rejected or merged, or an extraction failure. It also
captures the session through the public API into a sandbox and counts the notes written, so the
two readings can be compared per case.

    python research/silence_probe.py                     # report
    python research/silence_probe.py --save              # + research/results/silence_probe.json

Local Ollama only; sandbox store only; the engine is not touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import sandbox_guard  # noqa: E402
sandbox_guard.isolate()

import supersession_bench as sb  # noqa: E402
import _provenance as prov  # noqa: E402 - measured_at: {commit, utc, dirty} on the artifact
import _ollama_pacer as pacer  # noqa: E402 - R-v2-ports item 9A: pace/retry/count this stand's own traffic

ASOF = ROOT / "research" / "results" / "asof_v1.json"
DATASET = ROOT / "research" / "data" / "supersession_v1.json"
OUT = ROOT / "research" / "results" / "silence_probe.json"


def silent_cases(asof: Path) -> list[str]:
    art = json.loads(asof.read_text(encoding="utf-8"))
    rows = art["arms"]["nevertwice"]["rows"]
    return sorted({r["id"] for r in rows if (r.get("store") or {}).get("s0", {}).get("written", 0) == 0})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--asof", default=str(ASOF))
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    os.environ["NEVERTWICE_CLOUD"] = "none"
    os.environ["NEVERTWICE_MODEL"] = sb.LLM
    os.environ.setdefault("NEVERTWICE_EMBED_MODEL", sb.EMBED_MODEL)
    os.environ.setdefault("NEVERTWICE_EXTRACT_TEMP", "0")
    from nevertwice import api                                   # noqa: PLC0415
    import memory_hook as m                                      # noqa: PLC0415

    pacer.install()          # R-v2-ports item 9A: pace/retry/count this stand's own traffic
    snap = pacer.snapshot()

    raw = Path(args.dataset).read_bytes()
    data = json.loads(raw.decode("utf-8"))
    cases = {c["id"]: c for c in data["cases"]}
    ids = silent_cases(Path(args.asof))
    if args.limit:
        ids = ids[:args.limit]
    print(f"silent first sessions in {Path(args.asof).name}: {len(ids)}\nstore {sandbox_guard.store()}\n")

    rows = []
    t0 = time.time()
    for i, cid in enumerate(ids):
        case = cases[cid]
        s0 = "\n".join(case["sessions"][0])
        project = f"sil{i:03d}"
        # 1. the extraction prompt as process_session builds it, and what the model returns
        body = m.strip_injected_boilerplate(s0)
        transcript_full = m.truncate_smart(m.redact_secrets(f"Working directory: {ROOT}\nTrigger: ingest\n\n{body}"),
                                           m.MAX_TRANSCRIPT_CHARS)
        existing = m.collect_existing_titles(project, for_date=datetime.now().strftime("%Y-%m-%d"))
        prompt = m.EXTRACTION_PROMPT.format(
            transcript=transcript_full, project_hint=project,
            tag_vocab="(empty - pick freely)",
            existing_patterns=", ".join(existing["pattern"]) or "(none)",
            existing_mistakes=", ".join(existing["mistake"]) or "(none)",
            existing_decisions=", ".join(existing["decision"]) or "(none)",
            brain_block=m._brain_prompt_block(), language_rule=m.language_rule(transcript_full),
            principle_rubric=m._principle_prompt_rubric(),   # A1 (Q5)
            principle_schema=m._principle_schema_field())    # A1 (Q5)
        extraction = m.generate_json(prompt, project=project)
        raw_counts = {}
        if isinstance(extraction, dict):
            for key in ("patterns", "mistakes", "decisions"):
                v = extraction.get(key)
                raw_counts[key] = len(v) if isinstance(v, list) else 0
        raw_total = sum(raw_counts.values())
        # 2. the public path: what the writer keeps of it
        api.capture_session(s0, project=project, session_id=f"{project}-s0", trigger="ingest")
        root = Path(sandbox_guard.store())
        note_paths = [p for folder in ("Patterns", "Mistakes", "Decisions") if (root / folder).exists()
                      for p in (root / folder).glob(f"*-{project}-*.md")]
        written = [p.name for p in note_paths]
        # session one's fact is the as-of case's *superseded* marker - the old truth
        marker_in_note = any(sb._hit(case["superseded"], p.read_text(encoding="utf-8", errors="replace"))
                             for p in note_paths)
        kind = ("extraction failed" if extraction is None or extraction == {} else
                "no items returned" if raw_total == 0 else
                "items returned, none written" if not written else
                "written without the marker" if not marker_in_note else "written with the marker")
        rows.append({"id": cid, "shape": case["shape"], "chars": len(s0), "sentences": len(case["sessions"][0]),
                     "raw_items": raw_counts, "raw_total": raw_total, "notes_written": len(written),
                     "marker_in_note": marker_in_note, "kind": kind,
                     "extraction_keys": sorted(extraction.keys()) if isinstance(extraction, dict) else None})
        print(f"  {cid:<26} {case['shape']:<26} raw {raw_counts} -> written {len(written)} :: {kind}", flush=True)

    # Every kind, zeros included. A tally built by counting what occurred leaves the kinds that
    # did NOT occur out of the file - and those are exactly the ones the register publishes as
    # "0 of 8". `tools/remeasure.py --restore` then has no pointer to read and answers "restore
    # by hand" for a number the stand did measure (audit 2026-09-22). An absent key is not a zero.
    KINDS = ("extraction failed", "no items returned", "items returned, none written",
             "written without the marker", "written with the marker")
    kinds = {k: 0 for k in KINDS}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(f"\nby kind: {kinds}   ({round(time.time() - t0, 1)}s)")
    if args.save:
        out = {"asof": str(Path(args.asof).resolve().relative_to(ROOT)).replace("\\", "/"),
               "dataset": {"name": data["name"], "sha256": hashlib.sha256(raw).hexdigest()},
               "llm": sb.LLM, "extract_temperature": os.environ.get("NEVERTWICE_EXTRACT_TEMP"),
               "silent_cases": len(ids), "by_kind": kinds, "rows": rows}
        # R-v2-ports/K16(2): attached at the artifact's own root - this stand writes exactly
        # ONE artifact (research/results/silence_probe.json), so this IS the container every
        # registered claim's pointer (`by_kind[...]`, `silent_cases`) resolves through.
        pacer.attach(out, since=snap)
        out["llm"] = prov.running_llm(out.get("llm"))   # (б): the model that ran, not the one meant
        prov.stamp(out)
        Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
