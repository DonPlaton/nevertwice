#!/usr/bin/env python3
"""PREREG-V3 TB4.12 A5: the smoke's summary - plumbing counters only (rev1 §9.4: "--smoke has no scorer. It prints
plumbing counters only: calls, failures, items, tokens, seconds, yield and coverage").

Per arm-run of a smoke stand, from the scheduler's stand result and the recording proxy's log (accounting's functions,
never an adapter's own counts):
* calls: the arm-run's forwarded calls (accounting.cloud_counters; its cloud_bypass is not measured on a smoke and is
  never printed, Q-12-6);
* failures: transport_lost, failed_outcomes (never a question of a unit the stand did not record - its block ABORTed
  by the gate's halt, B-REASK-HALT) and upstream_errors (the proxy's log), unit_aborts (the scheduler's unit
  records, write and question stages), reader_format_failures (the answer rows), background_writes (R9: accounting's
  background_writes, a questions-stage write-port call that never succeeded included - Q1, reported, never raised);
* items: the retrievable items the end_write footprints report, over the units that were not aborted;
* tokens: by phase, from the proxy's log;
* seconds: the units' active time, write and question stages;
* yield and coverage: artifact.yield_block(scored=False) - no label ever (labels come from scored runs only, rev1
  §9.4) - over the units that were not aborted: each unit's retrievable items, and its coverage input by the auditor's
  Q-A5-1 measure (accounting.unit_coverage over the proxy's bodies/<arm>/<run>.<unit>.jsonl, the requests sent up to
  the unit's end_write; the denominator the unit's normalized item characters). An arm with no writer LLM - the
  retrieval tier and the lexical floor - prints "n/a: no writer LLM" for both (Q-A5-1 O-d). C-3: which arms those are
  is the plan's (run_v3_plan.ARMS, the write unit "item"), never the caller's: a no_writer that differs from the
  plan's - a writer arm named in it - refuses the summary by name.
A log with a problem refuses the summary by name. No key anywhere in a row names a score, verdict, gold, twin, judge,
label or accuracy: the smoke has no scorer. write() puts the summary in the runs tree (<runs>/<stand id>/_smoke/),
never under research/v3/results, and never over an existing file.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

HERE = Path(__file__).resolve().parent
SMOKE_FIELDS = ("calls", "failures", "items", "tokens", "seconds", "yield", "coverage")
FAILURE_FIELDS = ("transport_lost", "failed_outcomes", "upstream_errors", "unit_aborts", "reader_format_failures",
                  "background_writes")
NO_WRITER = "n/a: no writer LLM"
RESULTS_DIR = HERE / "results"
SCORE_WORDS = re.compile(r"score|verdict|gold|twin|judge|label|accura|correct|exact_match|(^|_)(em|f1)($|_)", re.I)


class SmokeError(ValueError):
    """A smoke summary the preregistration does not allow, or an input it cannot read; nothing was printed."""


def _load(name: str, path: Path):
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


def _accounting():
    return _load("v3_accounting_for_smoke", HERE / "accounting.py")


def _artifact():
    return _load("v3_artifact_for_smoke", HERE / "artifact.py")


def _plan():
    return _load("v3_run_plan_for_smoke", HERE / "run_v3_plan.py")


def no_writer_arms(arms: Iterable[str]) -> set[str]:
    """C-3: of ``arms``, those without a writer LLM - the plan's retrieval tier (write unit "item")."""
    plan_arms = _plan().ARMS
    unknown = sorted(a for a in set(arms) if a not in plan_arms)
    if unknown:
        raise SmokeError(f"arms {unknown} are not the plan's - whether they write is unknown")
    return {a for a in arms if plan_arms[a].granularity == "item"}


def base_stand(stand: str) -> str:
    return stand.split("-", 1)[0]


def arm_runs(result: Mapping[str, Any]) -> dict:
    """{(arm, run): {"write": {unit: UnitRecord}, "questions": {unit: its question record}}} over the stand's blocks."""
    out: dict = {}
    for b in result.get("blocks") or []:
        for arm, recs in (b.get("write") or {}).items():
            for (run, unit), w in recs.items():
                ar = out.setdefault((arm, run), {"write": {}, "questions": {}})
                if unit in ar["write"]:
                    raise SmokeError(f"{arm}/{run}: unit {unit} was written in two blocks")
                ar["write"][unit] = w
                ar["questions"][unit] = ((b.get("questions") or {}).get(arm) or {}).get((run, unit)) or {}
    return out


def retrievable(footprint: Any) -> int:
    """The retrievable items an end_write footprint reports (its "retrievable", or the count itself)."""
    v = footprint.get("retrievable") if isinstance(footprint, Mapping) else footprint
    if not (isinstance(v, int) and not isinstance(v, bool) and v >= 0):
        raise SmokeError(f"a footprint without a retrievable count: {footprint!r}")
    return v


def score_keys(obj: Any, path: str = "") -> list[str]:
    """Every key, at any depth, that names a score - the smoke has none."""
    out = []
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else str(k)
            if SCORE_WORDS.search(str(k)):
                out.append(here)
            out += score_keys(v, here)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out += score_keys(v, f"{path}[{i}]")
    return out


def check_row(row: Mapping[str, Any]) -> None:
    want = {"arm", "run", *SMOKE_FIELDS}
    if set(row) != want:
        raise SmokeError(f"a smoke row has {sorted(set(row) ^ want)} beyond or short of its fields {SMOKE_FIELDS}")
    if not isinstance(row["failures"], Mapping) or set(row["failures"]) != set(FAILURE_FIELDS):
        raise SmokeError(f"a smoke row's failures are not exactly {FAILURE_FIELDS}")
    bad = score_keys(row)
    if bad:
        raise SmokeError(f"a smoke row names a score: {bad} - the smoke has no scorer (rev1 §9.4)")


def _yield(stand: str, items: Mapping[str, int], cover: Mapping[str, Mapping[str, Any]]) -> tuple[Any, Any]:
    if not items:
        return "unmeasured: every unit aborted", "unmeasured: every unit aborted"
    units = [{"retrievable": items[u], "chars_in": cover[u]["covered"], "chars": cover[u]["chars"]}
             for u in sorted(items)]
    y = _artifact().yield_block(unit=_accounting().UNIT_OF_STAND[base_stand(stand)], units=units, scored=False)
    if y["labels"]:
        raise SmokeError(f"a smoke yield carries labels {y['labels']} - labels come from scored runs only")
    return round(y["retrievable_unit_share"], 4), round(y["coverage"], 4)


def summarize(result: Mapping[str, Any], log: Any, *, stand: str, key_question: Mapping[tuple[str, str], str],
              item_texts: Mapping[str, Any], run_dir: str | os.PathLike,
              no_writer: Iterable[str] | None = None) -> dict:
    """{"stand": stand, "arm_runs": [row, ...], "unrecorded": [...]} - one row per arm-run, exactly its SMOKE_FIELDS
    (see the module docstring); "unrecorded" names, per arm-run with any, the question keys of units the stand never
    recorded (B-REASK-HALT: counted apart from failed_outcomes, never dropped silently) and their units. ``item_texts``: unit -> its items' texts (the coverage denominator); ``run_dir``: the proxy's run
    directory (its bodies); ``no_writer``: only a check - the arms the caller expects to have no writer LLM, which
    must be the plan's (C-3)."""
    if log.problems:
        raise SmokeError(f"the proxy's log has problems {log.problems[:3]} - no smoke summary from it")
    A = _accounting()
    runs = arm_runs(result)
    plan_no_writer = no_writer_arms({a for a, _r in runs})
    if no_writer is not None and set(no_writer) != plan_no_writer:
        raise SmokeError(f"no_writer {sorted(set(no_writer))} is not the plan's {sorted(plan_no_writer)} - a writer arm "
                         f"named in it would print its coverage as n/a (C-3)")
    no_writer = plan_no_writer
    rows, unrecorded = [], []
    for (arm, run), ar in sorted(runs.items()):
        w, q = ar["write"], ar["questions"]
        reads = [r for u in w for r in (q.get(u) or {}).get("reads") or []]
        dropped = [r["qid"] for r in reads if r.get("unrecovered")]
        bw = A.background_writes(log.calls, arm=arm, run=run,
                                 end_write_at={u: rec.end_write_utc for u, rec in w.items() if rec.end_write_utc},
                                 read_windows={u: [(r["t0"], r["t1"]) for r in (q.get(u) or {}).get("reads") or []
                                                   if r.get("t0") and r.get("t1")] for u in w})
        cc = A.cloud_counters(log.calls, arm=arm, run=run, stand=base_stand(stand), cloud_bypass=None,
                              background_writes=bw["count"], ollama=log.ollama, key_question=key_question,
                              dropped=dropped, unrecorded_units=sorted({u for u, _k in key_question} - set(w)))
        items = {u: retrievable(rec.footprint) for u, rec in w.items() if rec.aborted is None}
        if arm in no_writer:
            y = cov = NO_WRITER
        else:
            cover = {}
            for u in items:
                bodies, problems = A.load_bodies(Path(run_dir), arm=arm, run=run, unit=u)
                if problems:
                    raise SmokeError(f"{arm}/{run}/{u}: its bodies file has problems {problems[:2]}")
                cover[u] = A.unit_coverage(list(item_texts[u]), bodies, end_write_at=w[u].end_write_utc)
            y, cov = _yield(stand, items, cover)
        row = {"arm": arm, "run": run, "calls": cc["calls"],
               "failures": {"transport_lost": cc["transport_lost"], "failed_outcomes": cc["failed_outcomes"],
                            "upstream_errors": cc["upstream_errors"],
                            "unit_aborts": sum(1 for rec in w.values() if rec.aborted)
                            + sum(1 for u in w if (q.get(u) or {}).get("aborted")),
                            "reader_format_failures": sum(1 for r in reads if (r.get("answer") or {}).get("format_failure")),
                            "background_writes": bw["count"]},
               "items": sum(items.values()), "tokens": cc["tokens"],
               "seconds": round(sum(rec.active_s for rec in w.values())
                                + sum(float((q.get(u) or {}).get("active_s") or 0.0) for u in w), 3),
               "yield": y, "coverage": cov}
        check_row(row)
        rows.append(row)
        if cc["unrecorded_keys"]:
            unrecorded.append({"arm": arm, "run": run, "unrecorded_keys": cc["unrecorded_keys"],
                               "units": cc["unrecorded_units"]})
    if not rows:
        raise SmokeError(f"{stand}: the stand result holds no arm-run")
    return {"stand": stand, "arm_runs": rows, "unrecorded": unrecorded}


def render(summary: Mapping[str, Any]) -> str:
    """One JSON line per arm-run: the stand, the arm and run, its SMOKE_FIELDS - nothing else."""
    lines = []
    for row in summary["arm_runs"]:
        check_row(row)
        lines.append(json.dumps({"stand": summary["stand"], **row}, sort_keys=True, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def _inside(path: Path, root: Path) -> bool:
    """B-SEAL83: canonical paths (realpath) - by its 8.3 name a path under the results dir is still under it."""
    p, r = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def write(summary: Mapping[str, Any], path: str | os.PathLike, *, results_dir: str | os.PathLike = RESULTS_DIR) -> Path:
    """The summary as JSON at ``path`` - fresh, in the runs tree, never under research/v3/results."""
    p = Path(path)
    if _inside(p, Path(results_dir)):
        raise SmokeError(f"{p}: a smoke writes nothing under {results_dir} (rev1 §9.4: no results from a smoke)")
    for row in summary["arm_runs"]:
        check_row(row)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "xb") as f:
            f.write((json.dumps(summary, sort_keys=True, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    except FileExistsError:
        raise SmokeError(f"{p} already exists - a smoke summary is written once") from None
    return p


def rows_from_lines(text: str) -> list[dict]:
    """The rows a render() printed (the CLI's own check that every line is a smoke row)."""
    out = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        obj = json.loads(line)
        stand = obj.pop("stand", None)
        if not isinstance(stand, str):
            raise SmokeError("a smoke line without its stand")
        check_row(obj)
        out.append(obj)
    return out


def iter_problems(summary: Mapping[str, Any]) -> Iterable[str]:
    """The rows' failures that are not zero, by name - for the CLI's exit code, never counted as a score."""
    for row in summary["arm_runs"]:
        for k in FAILURE_FIELDS:
            if row["failures"][k]:
                yield f"{row['arm']}/{row['run']}: {k}={row['failures'][k]}"
