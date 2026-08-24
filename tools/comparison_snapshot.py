#!/usr/bin/env python3
"""Regenerate the dated parts of `docs/COMPARISON.md`.

A comparison table is the fastest-rotting thing in a repository. Star counts move,
projects get archived, architectures get rewritten, and a hand-maintained table keeps
asserting last year's landscape with no date attached. So the volatile parts are
generated:

* **repository activity** - stars, forks, last push, archived - from the GitHub API,
  cached into `docs/comparison_snapshot.json` with the date it was fetched;
* **the capability matrix** - from `docs/comparison_data.json`, rendered as what each
  vendor *documents*, with the sources, at a stated survey date;
* **what was actually verified here** - from `research/head_to_head.json`, which is a
  measurement, not a claim, including the systems that could not be run at all;
* **the retrieval table** - from `research/evidence_manifest.json`, the same source the
  README and BENCHMARKS use, so the three cannot disagree.

Vendor claim and verified measurement are rendered as separate tables on purpose. So are
retrieval recall and end-to-end answer accuracy: they are different axes, and ranking
them together would compare a retrieval pipeline against a retrieval pipeline plus an
LLM reader.

Usage:

    python tools/comparison_snapshot.py            # check: fail if a region is stale
    python tools/comparison_snapshot.py --write    # regenerate the regions in place
    python tools/comparison_snapshot.py --fetch    # refresh the GitHub snapshot (network)

`--fetch` is the only mode that touches the network, and it is never run in CI: the
snapshot is committed so that checking and rendering stay offline and deterministic.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                      # noqa: BLE001 - a redirected stream may refuse
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "comparison_data.json"
SNAPSHOT = ROOT / "docs" / "comparison_snapshot.json"
DOC = ROOT / "docs" / "COMPARISON.md"
MANIFEST = ROOT / "research" / "evidence_manifest.json"
HEAD_TO_HEAD = ROOT / "research" / "head_to_head.json"

REGION = "<!-- comparison:{id} -->"
REGION_END = "<!-- /comparison:{id} -->"

AXES = [("substrate", "Substrate"), ("retrieval", "Retrieval"),
        ("temporal", "Temporal & contradictions"), ("agnostic", "Agent-agnostic"),
        ("local", "Local & privacy"), ("deploy", "Deploy")]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


# ------------------------------------------------------------------- fetching

def fetch_snapshot(today: str) -> dict:
    """Read stars/forks/last-push/archived for every system that has a repository."""
    data = load(DATA)
    repos = [s["repo"] for s in data["systems"] if s["repo"]]
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "nevertwice-comparison-snapshot"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    out: dict[str, dict] = {}
    for repo in repos:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}",
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.load(resp)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f"  ERROR: {repo}: {exc}")
            return {}
        out[repo] = {
            "stars": body["stargazers_count"],
            "forks": body["forks_count"],
            "pushed_at": body["pushed_at"][:10],
            "archived": body["archived"],
            "license": (body.get("license") or {}).get("spdx_id") or "none",
        }
        print(f"  {repo}: {out[repo]['stars']} stars, last push {out[repo]['pushed_at']}")
    return {"fetched": today, "repos": out}


# ----------------------------------------------------------------- renderers

def render_snapshot_note(data: dict, snap: dict, claims: dict) -> str:
    return (
        f"> **Snapshot.** Repository activity below was fetched from the GitHub API on "
        f"**{snap['fetched']}**. Capability rows are what each vendor documents, "
        f"surveyed **{data['surveyed']}**. The measured rows come from the head-to-head "
        f"run in `research/head_to_head.json`, recorded "
        f"**{data['head_to_head_recorded']}** - {data['head_to_head_note']} "
        f"Regenerate with `python tools/comparison_snapshot.py --fetch --write`.\n>\n"
        f"> {data['axis_rule']}"
    )


def render_activity(data: dict, snap: dict, claims: dict) -> str:
    rows = []
    for system in data["systems"]:
        if not system["repo"]:
            continue
        r = snap["repos"][system["repo"]]
        rows.append([
            f"[{system['name']}](https://github.com/{system['repo']})",
            f"{r['stars']:,}", f"{r['forks']:,}", r["pushed_at"],
            "**archived**" if r["archived"] else "active", r["license"],
        ])
    rows.sort(key=lambda r: int(r[1].replace(",", "")), reverse=True)
    return table(["repository", "stars", "forks", "last push", "state", "license"], rows)


def render_vendor_matrix(data: dict, snap: dict, claims: dict) -> str:
    rows = []
    for system in data["systems"]:
        name = f"**{system['name']}**" if system["id"] == "nevertwice" else system["name"]
        cells = [name] + [system["vendor"][key] for key, _ in AXES]
        cells.append(" · ".join(f"[{i + 1}]({url})"
                                for i, url in enumerate(system["sources"])) or "-")
        rows.append(cells)
    return table(["System"] + [label for _, label in AXES] + ["source"], rows)


def render_verified(data: dict, snap: dict, claims: dict) -> str:
    hh = load(HEAD_TO_HEAD)
    by_id = {s["id"]: s for s in data["systems"]}
    rows = []
    for entry in data["verified"]:
        raw = hh.get(entry["id"], {})
        name = by_id[entry["id"]]["name"]
        if entry["outcome"] == "ran":
            ingest = f"{raw['ingest_s']:.0f} s" if raw.get("ingest_s") else "n/a"
            query = f"{raw['query_s']:.0f} s" if raw.get("query_s") else "n/a"
            rows.append([f"**{name}**" if entry["id"] == "nevertwice" else name,
                         "ran here", ingest, query, entry["note"]])
        elif entry["outcome"] == "blocked":
            rows.append([name, "**could not be run**", "-", "-", entry["note"]])
        else:
            # A system nobody tried is not the same as one that refused to start, and
            # printing them the same way would overstate what this stand covers.
            rows.append([name, "*not attempted*", "-", "-", entry["note"]])
    return table(["System", "outcome", "ingest", "query", "what that means"], rows)


HEAD_TO_HEAD_ROWS = [
    ("**Nevertwice** (calibrated fusion, shipped default, 0 deps)", "nevertwice"),
    ("Mem0 (`infer=False`, dense+BM25)", "mem0"),
    ("LangMem (LangGraph InMemoryStore)", "langmem"),
    ("A-MEM (ChromaDB)", "amem"),
]


def render_head_to_head(data: dict, snap: dict, claims: dict) -> str:
    """Retrieval recall only, and only from the one head-to-head run.

    The opt-in cross-encoder is deliberately absent: it was measured in the retrieval
    study, not on this stand, and splicing two runs into one table is what made the
    earlier version of this comparison unsound.
    """
    metrics = ("recall_at_1", "recall_at_5", "recall_at_10", "mrr")
    rows = [[label] + [claims[f"head_to_head.{slug}.{m}"] for m in metrics]
            for label, slug in HEAD_TO_HEAD_ROWS]
    formatted = [[r[0]] + [f"{v:.3f}" for v in r[1:]] for r in rows]
    for col in range(1, len(metrics) + 1):
        best = max(r[col] for r in rows)
        for i, r in enumerate(rows):
            if r[col] == best:
                formatted[i][col] = f"**{formatted[i][col]}**"
    return table(["System (same bge-m3, same 500 questions, one run)",
                  "R@1", "R@5", "R@10", "MRR"], formatted)


RENDERERS = {
    "snapshot-note": render_snapshot_note,
    "activity": render_activity,
    "vendor-matrix": render_vendor_matrix,
    "verified": render_verified,
    "head-to-head": render_head_to_head,
}


# ---------------------------------------------------------------- region logic

def region_span(text: str, region_id: str) -> tuple[int, int] | None:
    start = text.find(REGION.format(id=region_id))
    if start < 0:
        return None
    end = text.find(REGION_END.format(id=region_id), start)
    if end < 0:
        raise ValueError(f"region {region_id!r} is opened but never closed")
    return start, end


def apply_regions(text: str, data: dict, snap: dict, claims: dict) -> tuple[str, list]:
    changed = []
    for region_id, renderer in RENDERERS.items():
        span = region_span(text, region_id)
        if span is None:
            continue
        start, end = span
        body_start = start + len(REGION.format(id=region_id))
        fresh = "\n" + renderer(data, snap, claims) + "\n"
        if text[body_start:end] != fresh:
            changed.append(region_id)
            text = text[:body_start] + fresh + text[end:]
    return text, changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Regenerate docs/COMPARISON.md.")
    ap.add_argument("--fetch", action="store_true",
                    help="refresh docs/comparison_snapshot.json from the GitHub API")
    ap.add_argument("--write", action="store_true",
                    help="regenerate the document instead of checking it")
    ap.add_argument("--today", metavar="YYYY-MM-DD",
                    help="date to stamp the fetched snapshot with (default: UTC today)")
    args = ap.parse_args(argv)

    if args.fetch:
        from datetime import datetime, timezone
        today = args.today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        snapshot = fetch_snapshot(today)
        if not snapshot:
            print("fetch failed; the committed snapshot was left untouched")
            return 1
        SNAPSHOT.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {SNAPSHOT.relative_to(ROOT).as_posix()} ({today})")

    data, snap = load(DATA), load(SNAPSHOT)
    claims = {c["id"]: c["value"] for c in load(MANIFEST)["claims"]}

    missing_repo = sorted(s["repo"] for s in data["systems"]
                          if s["repo"] and s["repo"] not in snap["repos"])
    if missing_repo:
        for repo in missing_repo:
            print(f"  ERROR: {repo} is in the data file but not in the snapshot; "
                  f"run --fetch")
        return 1

    text = DOC.read_text(encoding="utf-8")
    rendered, changed = apply_regions(text, data, snap, claims)
    missing = sorted(r for r in RENDERERS if not region_span(text, r))
    for region_id in missing:
        print(f"  ERROR: renderer {region_id!r} has no region in "
              f"{DOC.relative_to(ROOT).as_posix()}")

    if args.write:
        if changed:
            DOC.write_text(rendered, encoding="utf-8", newline="")
        print(f"rewrote {len(changed)} region(s)" if changed
              else "all regions were current")
    else:
        for region_id in changed:
            print(f"  STALE: {region_id} does not match the data")
        if changed:
            print("run `python tools/comparison_snapshot.py --write` to regenerate")

    if missing:
        return 1
    if changed and not args.write:
        return 1
    if not args.write:
        print(f"all {len(RENDERERS)} generated regions match the data "
              f"(snapshot {snap['fetched']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
