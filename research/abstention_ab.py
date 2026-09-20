"""Do the three abstention mechanisms earn their place, or only their tests?

Three switches went into the engine in one sitting with tests proving each *works* and no
measurement of whether any of them *helps*. That is the fault this project keeps writing
lessons about, so it gets measured the same way anything else here does: a baseline fixed
first, a threshold written down before the run, and a stated consequence for missing it. The
thresholds live in `.loop/GOAL-CLOSE.md`; this file produces the numbers they are judged on.

    python research/abstention_ab.py --part remine      # no model, no GPU, seconds
    python research/abstention_ab.py --part recall      # builds a store once, then sweeps
    python research/abstention_ab.py --part inject
    python research/abstention_ab.py --part all --out results.json

**`remine`** - re-mining a grown transcript from byte zero versus from the recorded
watermark. Bytes read is the obvious axis; coverage is the gate, because a byte saved by
skipping new material is a byte stolen. Running it is what found the defect it was built to
rule out: the delta reader seeked to the watermark and then discarded a line unconditionally,
on the assumption that a byte offset lands mid-line. A watermark is recorded as the file size
after a completed write, so it lands on a newline nearly every time, and the discard was
eating the first complete event of every re-mine.

**`recall`** and **`inject`** - one retrieval run, every policy evaluated against the same
captured hit lists. Re-running retrieval per threshold would let ranking noise masquerade as
a policy effect, and the sweep is over a curve rather than the two points the default sits
between, because a default that is only ever compared against *off* cannot be shown to be
the right default.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import sandbox_guard  # noqa: E402 - must precede any nevertwice import

sandbox_guard.isolate(prefix="nevertwice_abstention_")

DATASET = HERE / "data" / "supersession_v1.json"

THRESHOLDS = [0.0, 0.10, 0.20, 0.30, 0.35, 0.40, 0.50, 0.60, 0.75, 0.90]


# ── part 1: delta re-mining ───────────────────────────────────────────────────────────────

def _event(i: int, text: str) -> str:
    """One transcript event in the shape the reader actually parses.

    Assistant content is a *list of blocks*, not a string. The first version of this fixture
    passed a string and `_assistant_lines` dropped every one of them, so coverage read 0.50
    for both arms and the number looked like a property of the engine rather than of the
    fixture. A measurement stand that silently discards half its own input is worse than no
    stand at all, so the shapes are exact here.
    """
    if i % 2 == 0:
        msg = {"role": "user", "content": text}
        kind = "user"
    else:
        msg = {"role": "assistant", "content": [{"type": "text", "text": text}]}
        kind = "assistant"
    return json.dumps({"type": kind, "message": msg, "cwd": "D:/Coding/example",
                       "timestamp": f"2026-09-02T10:{i % 60:02d}:00Z"})


def _remine(stages: int, per_stage: int) -> dict:
    """A long session that keeps growing, mined both ways at every trigger.

    `per_stage` events of a few hundred characters each put the transcript past the
    extractor's window by the third stage, which is the regime the mechanism exists for and
    the only one in which the two policies can differ at all.
    """
    sys.path.insert(0, str(ROOT / "nevertwice"))
    import memory_hook as m                                    # noqa: PLC0415

    tmp = Path(sandbox_guard.store()) / "transcripts"
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / "session.jsonl"
    rng = random.Random(20260902)
    words = ("refactor index shard retry timeout cache token embed vault guard commit "
             "branch rollback migrate schema deploy replica latency budget").split()

    all_texts: list[str] = []
    full_bytes = delta_bytes = 0
    full_seen: set[str] = set()
    delta_seen: set[str] = set()
    watermark = 0
    per_stage_rows = []

    with path.open("w", encoding="utf-8") as fh:
        for stage in range(stages):
            for j in range(per_stage):
                marker = f"EV{stage:02d}_{j:04d}"
                text = marker + " " + " ".join(rng.choice(words) for _ in range(40))
                all_texts.append(marker)
                fh.write(_event(stage * per_stage + j, text) + "\n")
            fh.flush()
            size = path.stat().st_size

            # arm A - what shipped before: re-read the whole file every time
            body_full = m.read_transcript(str(path), 0)["body"]
            full_bytes += size
            full_seen |= {t for t in all_texts if t in body_full}

            # arm B - read only what was appended since the watermark
            body_delta = m.read_transcript(str(path), watermark)["body"]
            this_delta = max(0, size - watermark)
            delta_bytes += this_delta
            delta_seen |= {t for t in all_texts if t in body_delta}
            watermark = size

            per_stage_rows.append({
                "stage": stage, "file_bytes": size,
                "full_read_bytes": size, "delta_read_bytes": this_delta,
                "full_cumulative_coverage": round(len(full_seen) / len(all_texts), 4),
                "delta_cumulative_coverage": round(len(delta_seen) / len(all_texts), 4),
            })

    n = len(all_texts)
    return {
        "events": n, "stages": stages, "final_bytes": path.stat().st_size,
        "window_chars": m.MAX_TRANSCRIPT_CHARS,
        "full_read_bytes_total": full_bytes,
        "delta_read_bytes_total": delta_bytes,
        "byte_reduction": round(1 - delta_bytes / full_bytes, 4) if full_bytes else None,
        "full_read_coverage": round(len(full_seen) / n, 4),
        "delta_read_coverage": round(len(delta_seen) / n, 4),
        "events_only_delta_saw": sorted(delta_seen - full_seen)[:5],
        "events_only_full_saw": sorted(full_seen - delta_seen)[:5],
        "per_stage": per_stage_rows,
    }


# ── the shared store for parts 2 and 3 ────────────────────────────────────────────────────

def build_store(cases: list[dict]) -> None:
    """One project per case, two sessions each, through the public capture path.

    The store is whatever `sandbox_guard` pinned at import. Both sweeps read the same build,
    so the extraction cost is paid once; pointing this at a second, longer-lived directory
    would mean placing a store the guard did not place, which is the one thing it exists to
    prevent.
    """
    os.environ["NEVERTWICE_CLOUD"] = "none"
    # Set, not defaulted: the shell on this machine exports NEVERTWICE_MODEL for the live hook,
    # and `setdefault` let that model build the store while the register named another
    # (found 2026-09-10). The engine binds the name at import, so it is also bound explicitly.
    llm = os.environ.get("SUPERSESSION_LLM", "qwen3-coder:30b")
    os.environ["NEVERTWICE_MODEL"] = llm
    from nevertwice import api                                  # noqa: PLC0415
    api.m.OLLAMA_MODEL = llm
    t0 = time.time()
    for i, case in enumerate(cases):
        project = f"abs{i:03d}"
        for j, session in enumerate(case["sessions"]):
            api.capture_session("\n".join(session), project=project,
                                session_id=f"{project}-s{j}", trigger="ingest")
        if (i + 1) % 10 == 0:
            print(f"    built {i + 1}/{len(cases)} ({round(time.time() - t0)}s)", flush=True)


def capture_hits(cases: list[dict], k: int) -> list[dict]:
    """Retrieve once per case and keep every hit with its score and its labels."""
    from nevertwice import api                                  # noqa: PLC0415
    out = []
    for i, case in enumerate(cases):
        hits = api.recall(case["query"], project=f"abs{i:03d}", k=k)
        rows = []
        for h in hits:
            text = " ".join(str(h.get(f) or "") for f in ("title", "description", "prevention"))
            low = text.lower()
            rows.append({
                "stem": h.get("stem", ""), "score": float(h.get("score") or 0.0),
                "chars": len(text),
                "is_current": any(mk.lower() in low for mk in case["current"]),
                "is_stale": (any(mk.lower() in low for mk in case.get("superseded", []))
                             and not any(mk.lower() in low for mk in case["current"])),
            })
        out.append({"id": case["id"], "shape": case["shape"], "hits": rows})
    return out


def _relative_value(hits: list[dict]) -> dict[str, float]:
    """The engine's own rule, restated here so the sweep cannot drift from it silently."""
    scores = [h["score"] for h in hits if h["score"]]
    best = max(scores) if scores else 0.0
    if best <= 0:
        return {h["stem"]: 1.0 for h in hits}
    return {h["stem"]: h["score"] / best for h in hits}


def sweep(captured: list[dict], budget_chars: int | None = None) -> list[dict]:
    """Apply every threshold to the same captured hit lists.

    `budget_chars` models the injection path, which truncates to a character cap after the
    policy has run; leaving it None models prompt recall, which has no such cap.
    """
    rows = []
    for thr in THRESHOLDS:
        kept_chars, kept_n, with_current, with_stale, cases = 0, 0, 0, 0, 0
        for c in captured:
            hits = c["hits"]
            if not hits:
                continue
            cases += 1
            if thr > 0 and len(hits) > 1:
                val = _relative_value(hits)
                keep = [h for h in hits if val.get(h["stem"], 1.0) >= thr] or hits[:1]
            else:
                keep = hits
            if budget_chars is not None:
                acc, trimmed = 0, []
                for h in keep:
                    if acc + h["chars"] > budget_chars:
                        break
                    trimmed.append(h)
                    acc += h["chars"]
                keep = trimmed or keep[:1]
            kept_chars += sum(h["chars"] for h in keep)
            kept_n += len(keep)
            with_current += any(h["is_current"] for h in keep)
            with_stale += any(h["is_stale"] for h in keep)
        rows.append({
            "threshold": thr, "cases": cases,
            "mean_chars": round(kept_chars / cases, 1) if cases else 0.0,
            "mean_hits": round(kept_n / cases, 2) if cases else 0.0,
            "current_rate": round(with_current / cases, 4) if cases else 0.0,
            "stale_rate": round(with_stale / cases, 4) if cases else 0.0,
        })
    base = rows[0]
    for r in rows:
        r["char_reduction_vs_off"] = (round(1 - r["mean_chars"] / base["mean_chars"], 4)
                                      if base["mean_chars"] else 0.0)
        r["current_delta_vs_off"] = round(r["current_rate"] - base["current_rate"], 4)
    return rows


def _print_sweep(title: str, rows: list[dict]) -> None:
    print(f"\n  {title}")
    print(f"    {'thr':>5} {'chars':>8} {'hits':>6} {'current':>8} {'stale':>7} "
          f"{'-chars':>8} {'Δcurrent':>9}")
    for r in rows:
        print(f"    {r['threshold']:>5.2f} {r['mean_chars']:>8.1f} {r['mean_hits']:>6.2f} "
              f"{r['current_rate']:>8.3f} {r['stale_rate']:>7.3f} "
              f"{r['char_reduction_vs_off']:>8.3f} {r['current_delta_vs_off']:>9.3f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--part", default="all", choices=["all", "remine", "recall", "inject"])
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--inject-budget", type=int, default=2200,
                    help="the SessionStart character cap the injection path applies")
    ap.add_argument("--stages", type=int, default=8)
    ap.add_argument("--per-stage", type=int, default=400)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    out: dict = {"store": str(sandbox_guard.store())}

    if args.part in ("all", "remine"):
        print("- C3  delta re-mining")
        r = _remine(args.stages, args.per_stage)
        out["remine"] = r
        print(f"    {r['events']} events, {r['final_bytes']} bytes, "
              f"window {r['window_chars']} chars")
        print(f"    bytes read   full {r['full_read_bytes_total']:,}  "
              f"delta {r['delta_read_bytes_total']:,}  "
              f"reduction {r['byte_reduction']}")
        print(f"    coverage     full {r['full_read_coverage']}  "
              f"delta {r['delta_read_coverage']}")

    if args.part in ("all", "recall", "inject"):
        cases = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
        print(f"\n- building the store ({len(cases)} cases x 2 sessions)")
        build_store(cases)
        print("- retrieving once, then sweeping the policy offline")
        captured = capture_hits(cases, args.k)
        out["captured_cases"] = len(captured)
        out["llm"] = getattr(sys.modules.get("memory_hook"), "OLLAMA_MODEL", os.environ.get("NEVERTWICE_MODEL"))
        out["embedder"] = os.environ.get("NEVERTWICE_EMBED_MODEL", "bge-m3")
        out["mean_hits_returned"] = round(
            sum(len(c["hits"]) for c in captured) / max(1, len(captured)), 2)
        if args.part in ("all", "recall"):
            rows = sweep(captured, budget_chars=None)
            out["recall_sweep"] = rows
            _print_sweep("C1  prompt recall - no character cap", rows)
        if args.part in ("all", "inject"):
            rows = sweep(captured, budget_chars=args.inject_budget)
            out["inject_sweep"] = rows
            _print_sweep(f"C2  session start - capped at {args.inject_budget} chars", rows)

    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
        print("\nwrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
