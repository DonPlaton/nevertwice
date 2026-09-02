"""Redraw `docs/benchmarks.png` from live claims, and refuse to draw a withdrawn one.

The figure this replaces was last changed on 2026-07-07 and had **no generator**. It rendered
LongMemEval-oracle bars - Nevertwice ahead of Mem0, LangMem and A-MEM - every one of which was
withdrawn in 2026-08, and it was embedded in `docs/BENCHMARKS.md` with alt-text promising
three more retracted results. A chart travels further than the page it sits on, so the
retraction on the page never reached anyone who saw only the image. It is exactly the failure
`research/_figstyle.py` exists to prevent, sitting in the same repository as the module that
prevents it.

So: four panels, every number pulled from the evidence register at draw time, and a hard stop
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
    "supersession": ["supersession.nevertwice.stale_rate", "supersession.mem0.stale_rate",
                     "supersession.naive.stale_rate"],
    "latency": ["latency.cold", "latency.sessionstart", "latency.userpromptsubmit",
                "latency.pretooluse_end_to_end"],
    "poisoning": ["poisoning.injection_blocked", "poisoning.block_rate",
                  "poisoning.precision", "poisoning.false_fact_blocked"],
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

    _figstyle.apply()
    fig, axes = plt.subplots(2, 2, figsize=(11.0, 6.6))
    v = {cid: claims[cid]["value"] for ids in PANELS.values() for cid in ids}

    # ── the headline: who hands back a fact that was retracted ────────────────────────────
    ax = axes[0][0]
    names = ["Nevertwice", "Mem0 2.0.19", "append-only\n+ BM25"]
    vals = [v["supersession.nevertwice.stale_rate"], v["supersession.mem0.stale_rate"],
            v["supersession.naive.stale_rate"]]
    cis = [claims[c]["ci"] for c in PANELS["supersession"]]
    err = [[val - ci["low"] for val, ci in zip(vals, cis)],
           [ci["high"] - val for val, ci in zip(vals, cis)]]
    bars = ax.bar(names, vals, color=[_figstyle.POSITIVE, _figstyle.NEUTRAL, _figstyle.NEUTRAL],
                  width=0.62)
    ax.errorbar(names, vals, yerr=err, fmt="none", ecolor=_figstyle.INK, capsize=4, lw=1.1)
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("returns the retracted fact")
    ax.set_title("Supersession: lower is better")
    # above the whisker, not above the bar: at 0.07 the label landed inside its own interval
    for bar, val, ci in zip(bars, vals, cis):
        ax.text(bar.get_x() + bar.get_width() / 2, ci["high"] + 0.035, f"{val:.2f}",
                ha="center", fontsize=9, fontweight="bold")

    # ── what it costs to run ──────────────────────────────────────────────────────────────
    ax = axes[0][1]
    labels = ["cold import", "SessionStart", "UserPromptSubmit", "PreToolUse"]
    vals = [v["latency.cold"], v["latency.sessionstart"], v["latency.userpromptsubmit"],
            v["latency.pretooluse_end_to_end"]]
    ax.barh(labels[::-1], vals[::-1], color=_figstyle.PALETTE[4], height=0.6)
    ax.set_xlabel("milliseconds, interpreter start included")
    ax.set_title("Hot paths on a modest machine")
    for i, val in enumerate(vals[::-1]):
        ax.text(val + 2, i, f"{val} ms", va="center", fontsize=9)
    ax.set_xlim(0, max(vals) * 1.28)

    # ── what it refuses to store ──────────────────────────────────────────────────────────
    ax = axes[1][0]
    labels = ["prompt\ninjection", "all acceptance\nattacks", "precision on\nbenign notes",
              "plausible\nfalse fact"]
    vals = [v["poisoning.injection_blocked"], v["poisoning.block_rate"],
            v["poisoning.precision"], v["poisoning.false_fact_blocked"]]
    # the weakest cell is drawn in the negative colour and hatched, because a chart that
    # renders its own bad news quieter than its good news is arguing rather than reporting
    colours = [_figstyle.POSITIVE] * 3 + [_figstyle.NEGATIVE]
    bars = ax.bar(labels, vals, color=colours, width=0.62)
    bars[-1].set_hatch(_figstyle.NEGATIVE_HATCH)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("blocked / precision")
    ax.set_title("Poisoning defence, including where it is weak")
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 0.04, f"{val:.2f}",
                ha="center", fontsize=9)

    # ── what was measured and thrown away ─────────────────────────────────────────────────
    ax = axes[1][1]
    labels = ["hard-negative\nmining", "distillation", "256-d\ntruncation", "64x capacity"]
    vals = [v["embed.hard_negatives.situation.delta_recall_at_5"],
            v["embed.distillation.situation.delta_recall_at_5"],
            v["embed.truncation.shipped.256d_recall_at_5_cost"],
            v["embed.capacity.r64_vs_shipped"]]
    bars = ax.bar(labels, vals, color=_figstyle.NEGATIVE, width=0.62,
                  hatch=_figstyle.NEGATIVE_HATCH)
    ax.axhline(0, color=_figstyle.INK, lw=1.0)
    ax.set_ylabel("change in recall")
    ax.set_title("Four ideas that were measured and deleted")
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, val - 0.008, f"{val:+.3f}",
                ha="center", va="top", fontsize=9)
    ax.set_ylim(min(vals) * 1.5, 0.02)

    fig.suptitle("Nevertwice: what is measured, what it costs, and what was thrown away",
                 fontsize=12.5, fontweight="bold", y=0.985)
    fig.tight_layout(rect=(0, 0.055, 1, 0.955))
    return fig


EVIDENCE = ("supersession n=60 (+20 controls), supersession_v1 sha256 0e3114ea, "
            "bge-m3 + qwen3-coder:30b local, `python research/supersession_bench.py` | "
            "latency: median of 7 runs, Windows 11 / Python 3.14 / Ryzen 7 7700, "
            "`python research/latency_bench.py --save` | poisoning n=16 attacks + 10 benign, "
            "`python research/poisoning.py --save` | deleted mechanisms: external held-out set, "
            "`python research/embedder_ab.py`. Every value is registered live in "
            "research/evidence_manifest.json; this figure refuses to draw a withdrawn one.")


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
