"""Redraw `docs/benchmarks.png` from live claims, and refuse to draw a withdrawn one.

The figure this replaces was last changed on 2026-07-07 and had **no generator**. It rendered
LongMemEval-oracle bars - Nevertwice ahead of Mem0, LangMem and A-MEM - every one of which was
withdrawn in 2026-08, and it was embedded in `docs/BENCHMARKS.md` with alt-text promising
three more retracted results. A chart travels further than the page it sits on, so the
retraction on the page never reached anyone who saw only the image. It is exactly the failure
`research/_figstyle.py` exists to prevent, sitting in the same repository as the module that
prevents it.

So: six panels, every number pulled from the evidence register at draw time, and a hard stop
if any of them is withdrawn. The provenance line is drawn into the figure by `_figstyle.save`.

    python research/gen_benchmarks_figure.py
    python research/gen_benchmarks_figure.py --check    # fail if a panel would draw a withdrawal
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _figstyle  # noqa: E402

MANIFEST = ROOT / "research" / "evidence_manifest.json"
OUT = ROOT / "docs" / "benchmarks"

#: Panel -> the claim ids it draws. Every one is checked against the register before anything
#: is plotted, so adding a bar to this figure means registering the number first.
PANELS = {
    "head_to_head": ["h2h_pinned.nevertwice.recall_at_5", "h2h_pinned.mem0.recall_at_5",
                     "h2h_pinned.langmem.recall_at_5", "h2h_pinned.amem.recall_at_5",
                     "h2h_locomo.nevertwice.recall_at_5", "h2h_locomo.mem0.recall_at_5",
                     "h2h_locomo.langmem.recall_at_5", "h2h_locomo.amem.recall_at_5"],
    "haystack": ["longmem_pinned.semantic.recall_at_5", "longmem_pinned.lexical.recall_at_5",
                 "longmem_pinned.hybrid.recall_at_5", "longmem_s.semantic.recall_at_5",
                 "longmem_s.lexical.recall_at_5", "longmem_s.hybrid.recall_at_5"],
    "supersession": ["supersession.nevertwice.stale_rate", "supersession.mem0.stale_rate",
                     "supersession.naive.stale_rate"],
    "poisoning": ["poisoning.injection_blocked", "poisoning.block_rate",
                  "poisoning.precision", "poisoning.false_fact_blocked"],
    "latency": ["latency.cold", "latency.sessionstart", "latency.userpromptsubmit",
                "latency.pretooluse_end_to_end"],
    "deleted": ["embed.hard_negatives.situation.delta_recall_at_5",
                "embed.distillation.situation.delta_recall_at_5",
                "embed.truncation.shipped.256d_recall_at_5_cost",
                "embed.capacity.r64_vs_shipped"],
}


def load() -> dict[str, dict]:
    claims = json.loads(MANIFEST.read_text(encoding="utf-8"))["claims"]
    return {c["id"]: c for c in claims}


def verify(claims: dict[str, dict]) -> list[str]:
    """Every id this figure draws must exist and must be live. No exceptions, no warnings."""
    problems = []
    for panel, ids in PANELS.items():
        for cid in ids:
            c = claims.get(cid)
            if c is None:
                problems.append(f"{panel}: {cid} is not registered")
            elif c.get("withdrawn_on") or c.get("stale"):
                problems.append(f"{panel}: {cid} is withdrawn "
                                f"({c.get('withdrawn_on') or 'stale'})")
    return problems


def draw(claims: dict[str, dict]):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    _figstyle.apply()
    fig, axes = plt.subplots(2, 3, figsize=(16.0, 7.4))
    v = {cid: claims[cid]["value"] for ids in PANELS.values() for cid in ids}
    w = 0.38

    # ── 1. four systems, two external benchmarks, one stand each ──────────────────────────
    ax = axes[0][0]
    systems = ["Nevertwice", "Mem0\n2.0.19", "LangMem", "A-MEM"]
    slugs = ["nevertwice", "mem0", "langmem", "amem"]
    lme = [v[f"h2h_pinned.{s}.recall_at_5"] for s in slugs]
    loc = [v[f"h2h_locomo.{s}.recall_at_5"] for s in slugs]
    x = np.arange(len(systems))
    ax.bar(x - w / 2, lme, w, label="LongMemEval", color=_figstyle.POSITIVE)
    ax.bar(x + w / 2, loc, w, label="LoCoMo", color=_figstyle.PALETTE[1])
    ax.set_xticks(x, systems)
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("recall@5, annotated evidence")
    ax.set_title("External retrieval: one pool, one embedder")
    ax.legend(loc="upper right")
    for i, (a, b) in enumerate(zip(lme, loc)):
        ax.text(i - w / 2, a + 0.02, f"{a:.2f}", ha="center", fontsize=8)
        ax.text(i + w / 2, b + 0.02, f"{b:.2f}", ha="center", fontsize=8)

    # ── 2. the same benchmark at twenty-one times the haystack ────────────────────────────
    ax = axes[0][1]
    meths = ["semantic", "lexical", "hybrid"]
    small = [v[f"longmem_pinned.{s}.recall_at_5"] for s in meths]
    big = [v[f"longmem_s.{s}.recall_at_5"] for s in meths]
    x = np.arange(len(meths))
    ax.bar(x - w / 2, small, w, label="940 sessions", color=_figstyle.PALETTE[4])
    ax.bar(x + w / 2, big, w, label="19,206 sessions", color=_figstyle.PALETTE[5])
    ax.set_xticks(x, ["semantic", "lexical", "fusion\n(shipped)"])
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("recall@5")
    ax.set_title("Twenty-one times the haystack")
    ax.legend(loc="upper right")
    for i, (a, b) in enumerate(zip(small, big)):
        ax.text(i - w / 2, a + 0.02, f"{a:.2f}", ha="center", fontsize=8)
        ax.text(i + w / 2, b + 0.02, f"{b:.2f}", ha="center", fontsize=8)

    # ── 3. the axis nobody else measures ──────────────────────────────────────────────────
    ax = axes[0][2]
    names = ["Nevertwice", "Mem0\n2.0.19", "append-only\n+ BM25"]
    vals = [v["supersession.nevertwice.stale_rate"], v["supersession.mem0.stale_rate"],
            v["supersession.naive.stale_rate"]]
    cis = [claims[c]["ci"] for c in PANELS["supersession"]]
    err = [[val - ci["low"] for val, ci in zip(vals, cis)],
           [ci["high"] - val for val, ci in zip(vals, cis)]]
    bars = ax.bar(names, vals, width=0.6,
                  color=[_figstyle.POSITIVE, _figstyle.NEUTRAL, _figstyle.NEUTRAL])
    ax.errorbar(names, vals, yerr=err, fmt="none", ecolor=_figstyle.INK, capsize=4, lw=1.1)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("returns the retracted fact")
    ax.set_title("Supersession: lower is better")
    for bar, val, ci in zip(bars, vals, cis):
        ax.text(bar.get_x() + bar.get_width() / 2, ci["high"] + 0.035, f"{val:.2f}",
                ha="center", fontsize=9, fontweight="bold")

    # ── 4. what it refuses to store, including where it is weak ───────────────────────────
    ax = axes[1][0]
    labels = ["prompt\ninjection", "all acceptance\nattacks", "precision on\nbenign notes",
              "plausible\nfalse fact"]
    vals = [v["poisoning.injection_blocked"], v["poisoning.block_rate"],
            v["poisoning.precision"], v["poisoning.false_fact_blocked"]]
    colours = [_figstyle.POSITIVE] * 3 + [_figstyle.NEGATIVE]
    bars = ax.bar(labels, vals, color=colours, width=0.62)
    bars[-1].set_hatch(_figstyle.NEGATIVE_HATCH)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("blocked / precision")
    ax.set_title("Poisoning defence, and where it is weak")
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 0.04, f"{val:.2f}",
                ha="center", fontsize=9)

    # ── 5. what it costs to be there ──────────────────────────────────────────────────────
    ax = axes[1][1]
    labels = ["cold import", "SessionStart", "UserPromptSubmit", "PreToolUse"]
    vals = [v["latency.cold"], v["latency.sessionstart"], v["latency.userpromptsubmit"],
            v["latency.pretooluse_end_to_end"]]
    ax.barh(labels[::-1], vals[::-1], color=_figstyle.PALETTE[4], height=0.6)
    ax.set_xlabel("milliseconds, interpreter start included")
    ax.set_title("Hot paths on a modest machine")
    for i, val in enumerate(vals[::-1]):
        ax.text(val + 2, i, f"{val} ms", va="center", fontsize=9)
    ax.set_xlim(0, max(vals) * 1.30)

    # ── 6. what was measured and thrown away ──────────────────────────────────────────────
    ax = axes[1][2]
    labels = ["hard-negative\nmining", "distillation", "256-d\ntruncation", "64x capacity"]
    vals = [v["embed.hard_negatives.situation.delta_recall_at_5"],
            v["embed.distillation.situation.delta_recall_at_5"],
            v["embed.truncation.shipped.256d_recall_at_5_cost"],
            v["embed.capacity.r64_vs_shipped"]]
    bars = ax.bar(labels, vals, color=_figstyle.NEGATIVE, width=0.62,
                  hatch=_figstyle.NEGATIVE_HATCH)
    ax.axhline(0, color=_figstyle.INK, lw=1.0)
    ax.set_ylabel("change in recall")
    ax.set_title("Four ideas measured and deleted")
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, val - 0.008, f"{val:+.3f}",
                ha="center", va="top", fontsize=9)
    ax.set_ylim(min(vals) * 1.5, 0.02)

    fig.suptitle("Nevertwice: what is measured, what it costs, and what was thrown away",
                 fontsize=13.5, fontweight="bold", y=0.985)
    fig.tight_layout(rect=(0, 0.055, 1, 0.955))
    return fig


EVIDENCE = (
    "external retrieval: LongMemEval-oracle 940 sessions / 500 questions (sha256 821a2034) and "
    "LoCoMo 5,882 turns / 1,977 questions (sha256 79fa87e9), both hash-pinned in "
    "research/corpus_pin.py and verified before each run; every system on one local stand with "
    "bge-m3. The haystack panel adds LongMemEval-S, 19,206 sessions (sha256 08d8dad4). "
    "Supersession: n=60 plus 20 controls, supersession_v1 (sha256 0e3114ea), Nevertwice pooled "
    "over two runs. Poisoning: 16 attacks and 10 benign notes. Latency: minimum of five repeats "
    "on an idle machine. Deleted mechanisms: external held-out set. Every value is registered "
    "live in research/evidence_manifest.json; this figure refuses to draw a withdrawn one.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify the claims, draw nothing")
    args = ap.parse_args()

    claims = load()
    problems = verify(claims)
    if problems:
        print("refusing to draw:")
        for line in problems:
            print("  ", line)
        return 1
    print(f"all {sum(len(v) for v in PANELS.values())} claims are registered and live")
    if args.check:
        return 0

    fig = draw(claims)
    written = _figstyle.save(fig, OUT, evidence=EVIDENCE)
    for path in written:
        print("wrote", path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
