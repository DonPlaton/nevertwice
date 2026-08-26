#!/usr/bin/env python3
"""Local operational telemetry - what this install is doing, in numbers, on your machine only.

`stats.py` is the *token* ledger: what recall cost and what it plausibly avoided. This is the
operational half, and the questions are different ones. Is capture keeping up? Is extraction
failing? Is search getting slower? Are the interventions being accepted or overridden? How big
has the store got? Every failure this project has had in production was silent, and four of the
five counters here are the ones whose movement would have made a silent failure visible.

    nevertwice-telemetry                 # the panel
    nevertwice-telemetry --json          # the same numbers, machine-readable
    nevertwice-telemetry --export t.json # a documented, versioned file you can send onwards

**Local-only is structural here, not a promise.** This module contains no networking code at
all - no socket, no urllib, no http, no requests - so there is no code path that could transmit
anything, whatever a config file said. `--export` writes a **file**; sending it is an action a
person takes. `tests/_test_telemetry.py` proves both halves: it walks this module's AST for any
networking import or call, and it runs the whole lifecycle in a child process with sockets
disabled, asserting it completes.

That is a stronger guarantee than an opt-in flag, and it is deliberately less flexible. A flag
can be flipped by a config file, an environment variable, or a future patch that means well; the
absence of a transport cannot. If remote telemetry is ever wanted, it belongs in a separate
module that imports this one - so that the thing which *can* send is a file you can read in one
sitting, and this file stays a thing that cannot.

Recording is best-effort and off the critical path: a failure here must never affect recall.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import memory_hook as m         # noqa: E402
import outcomes                 # noqa: E402 - the closed outcome vocabulary, and
#                                 nothing else: it imports only `math`.

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:               # noqa: BLE001 - a redirected stream may not support it
    pass

SCHEMA_VERSION = 1

#: How many latency samples to keep per operation. A bounded reservoir, because a telemetry
#: file that grows without limit becomes the operational problem it was meant to report.
SAMPLE_CAP = 200

#: The counters, named once. `tests/_test_telemetry.py` requires the export to carry all of
#: them, so adding one here without exporting it fails rather than quietly shrinking the file.
COUNTERS = ("capture_lag", "extraction_failures", "search_latency", "intervention_outcomes",
            "store_size")

#: The documented export schema. Published as data rather than prose so a consumer can check a
#: file against it, and so "documented schema" is something with a version rather than a
#: paragraph that drifts.
EXPORT_SCHEMA = {
    "schema_version": SCHEMA_VERSION,
    "fields": {
        "generated": "ISO date and time the export was written, local clock",
        "schema_version": "integer; bump when a field changes meaning",
        "capture_lag": "seconds between the newest session on disk and the newest note "
                       "written from one. Rising means capture is falling behind.",
        "extraction_failures": "count of extraction attempts that returned nothing usable, "
                               "by reason. The 2026 stall showed up here as a flat store and "
                               "nowhere else.",
        "search_latency": "milliseconds per search: count, p50, p95, max, over a bounded "
                          "sample. Percentiles rather than a mean, because a mean hides the "
                          "tail a user actually notices.",
        "intervention_outcomes": "counts per outcome from outcomes.OUTCOMES - accepted, "
                                 "overridden, false_positive, prevented_failure, unknown.",
        "store_size": "notes, bytes and projects. The slowest-moving counter and the one "
                      "that answers 'is this still growing'.",
    },
    "contains_no": ["note content", "titles", "queries", "file paths outside the store name",
                    "identifiers of any kind"],
    "transmission": "none. This project has no code that sends this file anywhere; an export "
                    "is a file, and sending it is something a person does.",
}


def _path() -> Path:
    return m.VAULT / "telemetry.json"


def _blank() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "created": datetime.now().strftime("%Y-%m-%d"),
        "capture_lag": {"last_session": None, "last_note": None, "lag_seconds": None},
        "extraction_failures": {"total": 0, "by_reason": {}},
        "search_latency": {"samples": [], "count": 0},
        "intervention_outcomes": {},
        "store_size": {"notes": 0, "bytes": 0, "projects": 0, "measured": None},
    }


def load() -> dict:
    """The ledger, or a blank one. Never raises: telemetry that can break the hook is worse
    than no telemetry."""
    try:
        data = m._load_json_generations(_path(), "telemetry")
    except Exception:           # noqa: BLE001 - see above
        data = None
    if not isinstance(data, dict):
        return _blank()
    blank = _blank()
    for key, value in blank.items():
        data.setdefault(key, value)
    return data


def _save(data: dict) -> None:
    try:
        m.VAULT.mkdir(parents=True, exist_ok=True)
        m._save_json_generations(_path(), json.dumps(data, ensure_ascii=False, indent=1))
    except Exception:           # noqa: BLE001 - best-effort by contract
        pass


# ── recording ───────────────────────────────────────────────────────────

def record_search(ms: float) -> None:
    """One search's latency. Kept as a bounded sample so percentiles stay meaningful.

    A junk measurement is dropped rather than raised. Every recorder here is called from the
    hook path, and telemetry that can break the thing it measures is worse than no telemetry.
    """
    try:
        value = round(float(ms), 2)
    except (TypeError, ValueError):
        return
    if value != value or value in (float("inf"), float("-inf")) or value < 0:
        return
    data = load()
    bucket = data["search_latency"]
    samples = list(bucket.get("samples") or [])
    samples.append(value)
    # Drop from the FRONT: the recent tail is what a user is experiencing now, and a reservoir
    # that discards new samples to protect old ones reports last month's performance forever.
    bucket["samples"] = samples[-SAMPLE_CAP:]
    bucket["count"] = int(bucket.get("count") or 0) + 1
    _save(data)


def record_extraction_failure(reason: str) -> None:
    """One extraction that produced nothing usable, and why.

    `reason` is a short stable slug, not a message: the point of counting these is to
    aggregate them, and a free-text reason is a reason nobody can group.
    """
    reason = (str(reason or "unknown").strip().lower().replace(" ", "_"))[:40] or "unknown"
    data = load()
    bucket = data["extraction_failures"]
    bucket["total"] = int(bucket.get("total") or 0) + 1
    bucket["by_reason"][reason] = int(bucket["by_reason"].get(reason) or 0) + 1
    _save(data)


def record_outcome(outcome: str) -> None:
    """One intervention outcome, using D4's vocabulary and only D4's vocabulary.

    An unrecognised outcome is dropped, not counted under a name of its own. Telemetry that
    invents a category the lifecycle does not have produces a dashboard that disagrees with
    `outcomes.py` about what happened, and the dashboard is the one people read.
    """
    key = str(outcome or "").strip().lower()
    if key not in outcomes.OUTCOMES:
        return
    data = load()
    data["intervention_outcomes"][key] = int(data["intervention_outcomes"].get(key) or 0) + 1
    _save(data)


def refresh_capture_lag(now: float | None = None) -> dict:
    """How far behind the newest note is from the newest session.

    This is the counter that would have made the extraction stall visible: sessions kept
    arriving, notes stopped being written, and nothing anywhere reported the gap.
    """
    now = time.time() if now is None else now
    sessions = _newest(m.VAULT / "Sessions", "*.md")
    # Filter before max(): an absent folder yields None, and `max` comparing None to a float
    # raises - in a function whose whole contract is that it never does. A store with no
    # Patterns directory yet is the ordinary case, not an edge one.
    note_times = [t for t in (_newest(m.VAULT / folder, "*.md")
                              for folder in ("Mistakes", "Patterns", "Decisions"))
                  if t is not None]
    notes = max(note_times) if note_times else None
    data = load()
    data["capture_lag"] = {
        "last_session": _iso(sessions),
        "last_note": _iso(notes),
        "lag_seconds": (round(sessions - notes) if sessions and notes and sessions > notes
                        else 0 if sessions and notes else None),
        "measured": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"),
    }
    _save(data)
    return data["capture_lag"]


def refresh_store_size() -> dict:
    """Notes, bytes and projects. The slowest counter, and the one that answers 'still growing'."""
    notes = bytes_ = 0
    projects = set()
    for folder in ("Mistakes", "Patterns", "Decisions", "Sessions"):
        directory = m.VAULT / folder
        if not directory.is_dir():
            continue
        for path in directory.glob("*.md"):
            notes += 1
            try:
                bytes_ += path.stat().st_size
            except OSError:
                pass
            parsed = m.parse_typed_stem(path.stem)
            if parsed and parsed.get("project"):
                projects.add(parsed["project"])
    data = load()
    data["store_size"] = {"notes": notes, "bytes": bytes_, "projects": len(projects),
                          "measured": datetime.now().strftime("%Y-%m-%d %H:%M")}
    _save(data)
    return data["store_size"]


def _newest(directory: Path, pattern: str) -> float | None:
    try:
        times = [p.stat().st_mtime for p in directory.glob(pattern)]
    except OSError:
        return None
    return max(times) if times else None


def _iso(stamp: float | None) -> str | None:
    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M") if stamp else None


# ── reading ─────────────────────────────────────────────────────────────

def _percentile(sorted_values: list, fraction: float) -> float | None:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, max(0, round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


def snapshot() -> dict:
    """Every counter, with the latency samples reduced to percentiles.

    The raw samples are deliberately left out: they are the only field that could grow without
    bound, and a p50/p95/max answers the operational question without shipping a list.
    """
    data = load()
    samples = sorted(data["search_latency"].get("samples") or [])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "capture_lag": data["capture_lag"],
        "extraction_failures": data["extraction_failures"],
        "search_latency": {"count": data["search_latency"].get("count", 0),
                           "sampled": len(samples),
                           "p50_ms": _percentile(samples, 0.50),
                           "p95_ms": _percentile(samples, 0.95),
                           "max_ms": samples[-1] if samples else None},
        "intervention_outcomes": dict(data["intervention_outcomes"]),
        "store_size": data["store_size"],
    }


def export(path: Path | str) -> dict:
    """Write a documented, versioned export. Returns what was written.

    A file, and only a file. Nothing in this module sends it anywhere, and the schema it
    carries says so in the export itself rather than only in this docstring - a consumer
    reading the file learns the transmission policy from the file.
    """
    payload = {**snapshot(), "schema": EXPORT_SCHEMA}
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    return payload


def render(snap: dict | None = None) -> str:
    snap = snap or snapshot()
    lag = snap["capture_lag"]
    latency = snap["search_latency"]
    size = snap["store_size"]
    # Not `outcomes`: that name is the module, and shadowing it here would work
    # today and break the first time this function needs the vocabulary.
    interventions = snap["intervention_outcomes"]
    failures = snap["extraction_failures"]

    lag_line = ("never captured" if not lag.get("last_session")
                else "keeping up" if not lag.get("lag_seconds")
                else f"{lag['lag_seconds'] // 60} min behind the newest session")
    lines = [
        "",
        f"  Local telemetry - {snap['generated']}  (this file never leaves your machine)",
        "  " + "-" * 68,
        f"  capture        {lag_line}",
        f"  extraction     {failures.get('total', 0)} failure(s)"
        + (f": {', '.join(f'{k} x{v}' for k, v in failures.get('by_reason', {}).items())}"
           if failures.get("by_reason") else ""),
        f"  search         {latency['count']} search(es)"
        + (f" · p50 {latency['p50_ms']} ms · p95 {latency['p95_ms']} ms · "
           f"max {latency['max_ms']} ms" if latency["sampled"] else " · no samples yet"),
        "  interventions  " + (", ".join(f"{k} {v}"
                                         for k, v in sorted(interventions.items()))
                               or "none recorded"),
        f"  store          {size.get('notes', 0)} notes · {size.get('bytes', 0)} bytes · "
        f"{size.get('projects', 0)} project(s)",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(prog="nevertwice-telemetry",
                                 description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="the snapshot, machine-readable")
    ap.add_argument("--export", metavar="PATH", help="write a documented export file")
    ap.add_argument("--refresh", action="store_true",
                    help="re-measure capture lag and store size before reporting")
    ap.add_argument("--schema", action="store_true", help="print the export schema and exit")
    args = ap.parse_args()

    if args.schema:
        print(json.dumps(EXPORT_SCHEMA, indent=2, ensure_ascii=False))
        return 0
    if args.refresh:
        refresh_capture_lag()
        refresh_store_size()
    if args.export:
        payload = export(args.export)
        print(f"wrote {args.export} ({len(json.dumps(payload))} bytes). "
              f"Nothing was sent: this project has no code that transmits it.")
        return 0
    snap = snapshot()
    print(json.dumps(snap, indent=2, ensure_ascii=False) if args.json else render(snap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
