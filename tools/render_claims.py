#!/usr/bin/env python3
"""Render the published tables from `research/evidence_manifest.json`.

Every table below is data that already lives in the manifest, which in turn is checked
against the raw result files. Writing those numbers by hand is how a document ends up
quoting a run that no longer exists - which is exactly what the manifest found in five
places. So the tables are generated, and CI re-renders them and fails on any difference.

A generated region is delimited in the markdown by a pair of comments:

    <!-- claims:longmem-readme -->
    ...generated table...
    <!-- /claims:longmem-readme -->

Usage:

    python tools/render_claims.py             # check: fail if a region is stale
    python tools/render_claims.py --write     # regenerate the regions in place
    python tools/render_claims.py --footer longmem.hybrid.recall_at_5
                                              # one evidence line for a chart caption

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:                                   # a Windows console defaults to cp1251 here, and
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # footers carry `·`
except Exception:                      # noqa: BLE001 - a redirected stream may not support it
    pass

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "research" / "evidence_manifest.json"

REGION = "<!-- claims:{id} -->"
REGION_END = "<!-- /claims:{id} -->"


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


class Claims:
    """Lookup over the manifest, so a renderer names a claim id, never a number."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self._by_id = {c["id"]: c for c in manifest["claims"]}

    def value(self, claim_id: str):
        try:
            return self._by_id[claim_id]["value"]
        except KeyError:
            raise KeyError(f"no claim {claim_id!r} in the manifest") from None

    def get(self, claim_id: str) -> dict:
        return self._by_id[claim_id]


def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def _apply_bold(rows: list[list], header: list[str], places: int) -> str:
    """Format the numeric columns and bold the best value in each.

    Emphasis follows a rule the generator can state ("this column's best"), rather
    than being applied by hand - hand emphasis is how a table ends up highlighting a
    number that stopped being the best two runs ago.
    """
    formatted = [[r[0]] + [f"{v:.{places}f}" for v in r[1:]] for r in rows]
    for col in range(1, len(header)):
        best = max(row[col] for row in rows)
        for i, row in enumerate(rows):
            if row[col] == best:
                formatted[i][col] = f"**{formatted[i][col]}**"
    return _table(header, formatted)


# --------------------------------------------------------------- renderers

RETRIEVAL_ROWS = [
    ("semantic (bge-m3)", "semantic"),
    ("lexical (BM25)", "lexical"),
    ("**calibrated fusion (shipped default, 0 deps)**", "hybrid"),
    ("**+ trained cross-encoder (opt-in)**", "hybrid_xrerank"),
]


def _retrieval_table(c: Claims, places: int) -> str:
    rows = [[label,
             c.value(f"longmem.{slug}.recall_at_1"),
             c.value(f"longmem.{slug}.recall_at_5"),
             c.value(f"longmem.{slug}.recall_at_10"),
             round(c.value(f"longmem.{slug}.mrr"), places)]
            for label, slug in RETRIEVAL_ROWS]
    return _apply_bold(rows, ["method", "R@1", "R@5", "R@10", "MRR"], places)


def render_longmem_benchmarks(c: Claims) -> str:
    return _retrieval_table(c, 3)


HEAD_TO_HEAD_ROWS = [("**Nevertwice (calibrated fusion)**", "nevertwice"),
                     ("Mem0", "mem0"), ("LangMem", "langmem"), ("A-MEM", "amem")]


def render_head_to_head(c: Claims) -> str:
    """Every row comes from the one head-to-head run, never spliced across runs -
    that is the only thing that makes 'the same stand' mean anything."""
    rows = [[label,
             c.value(f"head_to_head.{slug}.recall_at_1"),
             c.value(f"head_to_head.{slug}.recall_at_5")]
            for label, slug in HEAD_TO_HEAD_ROWS]
    return _apply_bold(rows, ["system", "R@1", "R@5"], places=3)


LATENCY_ROWS = [
    ("PreToolUse end-to-end", "latency.pretooluse_end_to_end",
     "every tool call (interpreter start included)"),
    ("UserPromptSubmit end-to-end", "latency.userpromptsubmit",
     "per prompt (task-aware recall)"),
    ("SessionStart end-to-end, idle", "latency.sessionstart",
     "per session start with no backlog"),
    ("cold import of the engine", "latency.cold",
     "once per hook process (inside the numbers above)"),
    ("`guards.check()`, 61 guards, 2 KB", "latency.guards_check",
     "the actual guard match, pure regex"),
    ("lexical recall, no embedder", "latency.lexical_recall_floor",
     "the zero-model floor recall falls back to"),
]


def render_latency(c: Claims) -> str:
    rows = []
    for label, claim_id, when in LATENCY_ROWS:
        v = c.value(claim_id)
        text = f"{v:g} ms"
        rows.append([label, f"**{text}**" if claim_id.endswith("pretooluse_end_to_end")
                     else text, when])
    return _table(["hot path", "cost", "when it is paid"], rows)


TASK_A_ROWS = [("semantic (bge-m3)", "semantic"), ("lexical", "lexical"),
               ("hybrid (RRF)", "hybrid_rrf")]


def render_task_a(c: Claims) -> str:
    rows = [[label,
             c.value(f"task_a.{slug}.recall_at_1"),
             c.value(f"task_a.{slug}.recall_at_3"),
             c.value(f"task_a.{slug}.recall_at_5"),
             c.value(f"task_a.{slug}.mrr")]
            for label, slug in TASK_A_ROWS]
    return _apply_bold(rows, ["method", "R@1", "R@3", "R@5", "MRR"], places=3)


def render_task_b(c: Claims) -> str:
    return _table(["", "accuracy"], [
        ["bi-temporal graph", f"**{c.value('task_b.bitemporal_accuracy'):.3f}**"],
        ['flat "use newest"', f"{c.value('task_b.flat_newest_accuracy'):.3f}"],
    ])


def render_task_c(c: Claims) -> str:
    ratios = {p: c.value(f"task_c.{p}.ratio")
              for p in ("project_alpha", "project_beta", "project_delta")}
    best = max(ratios.values())
    rows = []
    for project, ratio in ratios.items():
        text = f"{ratio:g}×"
        rows.append([project,
                     str(c.value(f"task_c.{project}.card")),
                     str(c.value(f"task_c.{project}.full_context")),
                     f"**{text}**" if ratio == best else text])
    return _table(["project", "card", "full Context", "ratio"], rows)


def render_token_ab_raw(c: Claims) -> str:
    rows = []
    for k in (3, 5, 10):
        rows.append([
            str(k),
            f"{c.value(f'token_ab.raw.k{k}.recall_at_k'):.3f}",
            f"{c.value(f'token_ab.raw.k{k}.mean_topk_tok'):,}",
            f"**{c.value(f'token_ab.raw.k{k}.net_vs_curated_haystack_tok'):+,}**",
            f"{c.value(f'token_ab.raw.k{k}.net_vs_full_history_tok'):+,}",
        ])
    return _table(["k", "recall@k", "top-k cost (tok)",
                   "net vs a curated small haystack", "net vs the full history"], rows)


def render_token_ab_distill(c: Claims) -> str:
    rows = []
    for k in (3, 5, 10):
        rows.append([
            str(k),
            f"{c.value(f'token_ab.distill.k{k}.recall_at_k'):.3f}",
            f"{c.value(f'token_ab.distill.k{k}.raw_topk_tok'):,}",
            f"**{c.value(f'token_ab.distill.k{k}.distilled_topk_tok'):,}**",
            f"{c.value(f'token_ab.distill.k{k}.net_raw_vs_curated'):+,}",
            f"**{c.value(f'token_ab.distill.k{k}.net_distilled_vs_curated'):+,}**",
        ])
    return _table(["k", "recall@k", "raw top-k (tok)", "**distilled top-k (tok)**",
                   "net raw vs curated", "**net distilled vs curated**"], rows)


def render_token_ab_live(c: Claims) -> str:
    return _table(["arm", "mean input tokens", "answer-match (crude)"], [
        ["no memory (full haystack)",
         f"**{c.value('token_ab.live_two_arm.mean_prompt_tok_no_memory'):,}**",
         f"{c.value('token_ab.live_two_arm.answer_match_no_memory'):.3f}"],
        ["with memory (top-3 distilled)",
         f"**{c.value('token_ab.live_two_arm.mean_prompt_tok_with_memory'):,}**",
         f"{c.value('token_ab.live_two_arm.answer_match_with_memory'):.3f}"],
    ])


VERDICT_MARK = {"beats": "**beats**", "ties": "ties", "loses_to": "**LOSES**",
                "not_compared": "not compared", "not_applicable": "n/a"}


def render_baselines_conditions(c: Claims) -> str:
    policy = c.manifest["baseline_policy"]
    lines = [policy["rule"], ""]
    lines += [f"{i + 1}. {cond}" for i, cond in
              enumerate(policy["matched_conditions"])]
    lines += ["", policy["evidence_rule"]]
    return "\n".join(lines)


def render_baselines_registry(c: Claims) -> str:
    rows = []
    for bid, b in c.manifest["baselines"].items():
        rows.append([f"`{bid}`", b["name"] + (" *(additional)*" if b.get("additional")
                                              else ""),
                     b["definition"], b["why"], b["how"]])
    return _table(["id", "baseline", "what it is", "why it is the test", "how to run it"],
                  rows)


def _headline_claims(c: Claims) -> list[dict]:
    return [x for x in c.manifest["claims"] if x.get("headline")]


def render_baselines_matrix(c: Claims) -> str:
    baselines = list(c.manifest["baselines"])
    rows = []
    for claim in _headline_claims(c):
        verdicts = claim["baseline_verdicts"]
        rows.append([f"`{claim['id']}`"] +
                    [VERDICT_MARK[verdicts[b]["verdict"]] if b in verdicts else "**missing**"
                     for b in baselines])
    return _table(["headline claim"] + [f"`{b}`" for b in baselines], rows)


def render_baselines_summary(c: Claims) -> str:
    counts: dict[str, int] = {}
    for claim in _headline_claims(c):
        for entry in claim["baseline_verdicts"].values():
            counts[entry["verdict"]] = counts.get(entry["verdict"], 0) + 1
    order = ("beats", "ties", "loses_to", "not_compared", "not_applicable")
    total = sum(counts.values())
    rows = [[f"`{v}`", str(counts.get(v, 0)),
             c.manifest["baseline_policy"]["verdicts"][v]] for v in order]
    head = _table(["verdict", "count", "what it means"], rows)
    return (f"{len(_headline_claims(c))} headline claims x "
            f"{len(c.manifest['baselines'])} baselines = {total} pairs.\n\n{head}")


RENDERERS = {
    "longmem-benchmarks": render_longmem_benchmarks,
    "head-to-head": render_head_to_head,
    "latency": render_latency,
    "task-a": render_task_a,
    "task-b": render_task_b,
    "task-c": render_task_c,
    "token-ab-raw": render_token_ab_raw,
    "token-ab-distill": render_token_ab_distill,
    "token-ab-live": render_token_ab_live,
    "baselines-conditions": render_baselines_conditions,
    "baselines-registry": render_baselines_registry,
    "baselines-matrix": render_baselines_matrix,
    "baselines-summary": render_baselines_summary,
}


# ------------------------------------------------------------------ footers

def footer(c: Claims, claim_id: str) -> str:
    """One evidence line for a chart caption: n, dataset, model, commit, command.

    Task C5 puts one of these under every published figure; the renderer lives here
    so a caption cannot say something the manifest does not.
    """
    claim = c.get(claim_id)
    manifest = load_manifest()
    bits = []
    if claim["n"]:
        bits.append(f"n={claim['n']}")
    if claim["dataset"]:
        bits.append(manifest["datasets"][claim["dataset"]]["name"])
    bits.append(claim["unit"])
    env = manifest["environments"].get(claim["environment"] or "", {})
    for key in ("reader", "embedder"):
        if env.get(key):
            bits.append(f"{key}: {env[key]}")
    if claim["ci"]:
        bits.append(f"95% CI {claim['ci']['low']:.3f}-{claim['ci']['high']:.3f}")
    bits.append(f"commit {claim['commit'][:7]}")
    bits.append(f"`{claim['command']}`")
    return " · ".join(bits)


# ------------------------------------------------------------- region logic

def region_span(text: str, region_id: str) -> tuple[int, int] | None:
    start = text.find(REGION.format(id=region_id))
    if start < 0:
        return None
    end = text.find(REGION_END.format(id=region_id), start)
    if end < 0:
        raise ValueError(f"region {region_id!r} is opened but never closed")
    return start, end


def apply_regions(text: str, c: Claims) -> tuple[str, list[str]]:
    """Return (rendered text, ids of regions whose content changed)."""
    changed = []
    for region_id, renderer in RENDERERS.items():
        span = region_span(text, region_id)
        if span is None:
            continue
        start, end = span
        open_tag = REGION.format(id=region_id)
        body_start = start + len(open_tag)
        current = text[body_start:end]
        fresh = "\n" + renderer(c) + "\n"
        if current != fresh:
            changed.append(region_id)
            text = text[:body_start] + fresh + text[end:]
    return text, changed


def docs_with_regions(manifest: dict) -> list[Path]:
    return [ROOT / d for d in manifest["scope"]["docs"]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Render published tables from the evidence manifest.")
    ap.add_argument("--write", action="store_true",
                    help="regenerate the regions in place instead of checking")
    ap.add_argument("--footer", metavar="CLAIM_ID",
                    help="print one evidence footer line and exit")
    args = ap.parse_args(argv)

    manifest = load_manifest()
    claims = Claims(manifest)

    if args.footer:
        print(footer(claims, args.footer))
        return 0

    stale: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()
    for doc in docs_with_regions(manifest):
        text = doc.read_text(encoding="utf-8")
        rendered, changed = apply_regions(text, claims)
        for region_id in RENDERERS:
            if region_span(text, region_id):
                seen.add(region_id)
        if changed:
            stale += [f"{doc.relative_to(ROOT).as_posix()}:{r}" for r in changed]
            if args.write:
                doc.write_text(rendered, encoding="utf-8", newline="")

    missing = sorted(set(RENDERERS) - seen)

    if args.write:
        print(f"rewrote {len(stale)} region(s)" if stale else "all regions were current")
    else:
        for s in stale:
            print(f"  STALE: {s} does not match the manifest")
    for m in missing:
        print(f"  ERROR: renderer {m!r} has no region in any document in scope")

    if missing:
        return 1
    if stale and not args.write:
        print("run `python tools/render_claims.py --write` to regenerate")
        return 1
    if not args.write:
        print(f"all {len(seen)} generated regions match the manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
