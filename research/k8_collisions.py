#!/usr/bin/env python3
"""K8 step 0 - the K7 store made durable: every same-slug collision the write path meets.

The supersession stands write into a temporary sandbox that is deleted when the run ends, so the
notes the K7 campaign extracted no longer exist; the artifacts keep what recall returned, not the
two statements the write path had to choose between. This script re-extracts the corpora once
through the public path (`api.capture_session`, the engine as shipped - absorb by title) and
records, at the moment `write_typed_note` is called, every earlier note of the same project, type
and slug together with the item about to be written: both titles, both descriptions, both `[facts]`
blocks, which session wrote which, and the case's truth - a supersession case's pair is a
replacement (`replaces`), a control case's pair is a different fact on one topic (`separate`).
Two branches of the same key collision are told apart: `d` (same day, same stem - the in-place
absorb) and `r` (another day - the same-slug retirement into `Superseded/`), which is what the
as-of dating produces.

Nothing in the engine is edited: the recorder wraps `write_typed_note` from outside and calls the
original. The pairs are the calibration set for the skeleton test of K8 layer 1 (ledger K8, step 0)
and the truth set for the layer-3 judge's precision (step 3).

    python research/k8_collisions.py --stand supersession --out research/results/k8_collisions_explicit.json
    python research/k8_collisions.py --stand supersession --dataset research/data/supersession_v1_implicit.json \
        --out research/results/k8_collisions_implicit.json
    python research/k8_collisions.py --stand asof --out research/results/k8_collisions_asof.json
    python research/k8_collisions.py --stand supersession --limit 2      # smoke
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402 - must precede any nevertwice import

sandbox_guard.isolate(prefix="nevertwice_k8_")
sys.path.insert(0, str(HERE))
import supersession_bench as sb  # noqa: E402 - the corpora, the markers, the row shape
import asof_bench as ab  # noqa: E402 - the dating and the per-session store state

PAIRS: list[dict] = []
CUR: dict = {}          # the case being ingested: id, shape, truth, the two marker lists, sid8 tags


def _session_idx(stem: str | None) -> int | None:
    for tag, j in (CUR.get("sid8") or {}).items():
        if stem and tag in stem:
            return j
    return None


def install_recorder(m) -> None:
    """Wrap `write_typed_note`: before the original runs, record every reconcilable note of the
    same project, type and slug (live or archived) beside the item about to be written."""
    orig = m.write_typed_note

    def recorder(folder: str, item: object, project: str, date: str, tags: list, ntype: str,
                 session_stem_: str | None = None, siblings: list[str] | None = None,
                 why: list | None = None) -> str:
        #: `why` is the writer's reason channel (review 2026-09-23, R14): process_session passes
        #: it, and a recorder without it raised TypeError on every capture, which the stand's
        #: `except Exception` turned into an error row per case - a stand measuring nothing.
        if isinstance(item, dict):
            title = m.redact_secrets(m._strip_lead_icon(item.get("title", "untitled")))
            desc = m.redact_secrets(item.get("description", "") or "")
            prevention = m.redact_secrets(item.get("prevention", "") or "")
        else:
            title, desc, prevention = m._strip_lead_icon(str(item)), "", ""
        slug = m.slugify(title)
        base_stem = m.typed_stem(date, project, ntype, title)
        folder_path = m.VAULT / folder
        for old in m._reconcilable_typed_paths(folder_path, project, ntype, slug):
            try:
                text = old.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, _ = m._read_frontmatter(text)
            if session_stem_ and fm.get("session") == session_stem_:
                continue                        # the same session re-encountering its own note
            lines = text.split("\n")
            _, d_old, p_old = m._parse_note_body(lines)
            old_title = next((m._strip_lead_icon(ln.lstrip("# ").strip())
                              for ln in lines if ln.startswith("# ")), "")
            PAIRS.append({
                "case": CUR["id"], "shape": CUR["shape"], "truth": CUR["truth"],
                "branch": "d" if old.stem == base_stem else "r",
                "ntype": ntype, "slug": slug,
                "old_stem": old.stem, "old_archived": old.parent.name == "Archive",
                "old_session_idx": _session_idx(str(fm.get("session") or "")),
                "old_sources": fm.get("sources") or [],
                "old_title": old_title, "old_desc": d_old or "", "old_prevention": p_old or "",
                "old_facts": sorted(m._facts_in(d_old or "")),
                "new_session_idx": _session_idx(session_stem_),
                "new_title": title, "new_desc": desc, "new_prevention": prevention,
                "new_facts": sorted(m._facts_in(desc)),
                "new_entities": item.get("entities") if isinstance(item, dict) else None,
                "new_supersedes": (item.get("supersedes") or "") if isinstance(item, dict) else "",
                "new_contradicts": (item.get("contradicts") or "") if isinstance(item, dict) else "",
                # does each side carry the marker the case expects of it (old: the fact session one
                # stated; new: the replacement) - the pair the case is about, not a boilerplate item
                "old_marked": sb._hit(CUR["old_markers"], f"{old_title} {d_old or ''}"),
                "new_marked": sb._hit(CUR["new_markers"], f"{title} {desc}"),
            })
        return orig(folder, item, project, date, tags, ntype,
                    session_stem_=session_stem_, siblings=siblings, why=why)

    m.write_typed_note = recorder


def _set_case(case: dict, project: str, m) -> None:
    sup = case["shape"] != "control"
    CUR.clear()
    CUR.update({"id": case["id"], "shape": case["shape"],
                "truth": "replaces" if sup else "separate",
                "old_markers": case["superseded"] if sup else case["current"],
                "new_markers": case["current"] if sup else [],
                "sid8": {f"-session-{m._sid8(f'{project}-s{j}')}": j for j in (0, 1)}})


def run(stand: str, cases: list[dict], k: int) -> dict:
    os.environ["NEVERTWICE_CLOUD"] = "none"
    os.environ["NEVERTWICE_MODEL"] = sb.LLM
    os.environ.setdefault("NEVERTWICE_EMBED_MODEL", sb.EMBED_MODEL)
    # The extractor samples: the engine's default is 0.2, right for the live hook and
    # wrong for a measurement. Pinning the MODEL and leaving the TEMPERATURE loose is
    # what `supersession_bench` did for a year, publishing a run-to-run spread it blamed
    # on the model (fixed 2026-09-22: at 0.2 every one of 80 cases served different text
    # between runs of one commit, at 0 exactly one did).
    os.environ["NEVERTWICE_EXTRACT_TEMP"] = "0"
    from nevertwice import api                                    # noqa: PLC0415
    m = api.m
    m.OLLAMA_MODEL = sb.LLM                                       # J7: bind, then record
    install_recorder(m)
    rows, t0 = [], time.time()
    for i, case in enumerate(cases):
        project = f"k8{stand[0]}{i:03d}"
        _set_case(case, project, m)
        n_before = len(PAIRS)
        try:
            if stand == "asof":
                api.capture_session("\n".join(case["sessions"][0]), project=project,
                                    session_id=f"{project}-s0", trigger="ingest", date=ab.DAY_FIRST)
                api.capture_session("\n".join(case["sessions"][1]), project=project,
                                    session_id=f"{project}-s1", trigger="ingest", date=ab.DAY_SECOND)
            else:
                for j, session in enumerate(case["sessions"]):
                    api.capture_session("\n".join(session), project=project,
                                        session_id=f"{project}-s{j}", trigger="ingest")
        except Exception as e:                                    # noqa: BLE001 - reported per case
            rows.append({"id": case["id"], "shape": case["shape"], "error": f"{type(e).__name__}: {e}"})
            continue
        try:
            if stand == "asof":
                old = ab._items(api.as_of(case["query"], ab.DAY_BETWEEN, project, k=k))
                new = ab._items(api.as_of(case["query"], ab.DAY_AFTER, project, k=k))
                row = ab._row(case, old, new)
                row["leak"] = bool(sb._hit(case["current"], " ".join(old)))
                state = ab._session_state(project, case)
                row["store"] = state
                row["old_fail_kind"] = ab.old_fail_kind(row, state)
            else:
                hits = api.recall(case["query"], project=project, k=k)
                row = {**sb._row(case, [sb.hit_text(h) for h in hits]), **sb._store_state(project, case)}
        except OSError as e:
            # also-fix (xhigh review): `_session_state`/`_store_state` read the vault's own files
            # (a transient OSError, not a scoring bug) - one case's read failure must cost that
            # case, not the whole recording run.
            rows.append({"id": case["id"], "shape": case["shape"], "error": f"{type(e).__name__}: {e}"})
            continue
        row["pairs"] = len(PAIRS) - n_before
        rows.append(row)
        print(f"  [{i + 1}/{len(cases)}] {case['id']}  pairs={row['pairs']}"
              + (f"  old {row['old_day_correct']} new {row['new_day_correct']}" if stand == "asof"
                 else f"  stale={row['stale_returned']} current={row['current_returned']}"), flush=True)
    scored = ab.score(rows) if stand == "asof" else sb.score(rows)
    return {"rows": rows, "score": scored, "seconds": round(time.time() - t0, 1),
            "llm": m.OLLAMA_MODEL, "embedder": sb.EMBED_MODEL, "k": k,
            "dates": [ab.DAY_FIRST, ab.DAY_SECOND] if stand == "asof" else None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stand", choices=("supersession", "asof"), default="supersession")
    ap.add_argument("--dataset", default=str(sb.DATASET if hasattr(sb, "DATASET") else ab.DATASET))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    data = sb.load_dataset(Path(a.dataset))
    cases = data["cases"][:a.limit] if a.limit else data["cases"]
    # The as-of stand proper ingests the supersession cases only; here the controls go through the
    # same two-day dating too, so the `r` branch (same slug, another day - the slug retirement) has
    # `separate` pairs to calibrate against as well as `replaces` ones. `asof_bench.score` skips
    # the control rows by itself; their store state says whether session one's note was retired.
    res = run(a.stand, cases, a.k)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                            cwd=ROOT).stdout.strip()
    out = {"stand": a.stand, "engine_commit": commit,
           "dataset": {"name": data.get("name"), "sha256": data["sha256"], "path": data["path"]},
           "n_cases": len(cases), "n_pairs": len(PAIRS),
           "by_truth": {t: sum(1 for p in PAIRS if p["truth"] == t) for t in ("replaces", "separate")},
           "by_branch": {b: sum(1 for p in PAIRS if p["branch"] == b) for b in ("d", "r")},
           "pairs": PAIRS, **res}
    print(json.dumps({k: v for k, v in out.items() if k not in ("pairs", "rows")}, indent=1)[:2000])
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print("written", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
