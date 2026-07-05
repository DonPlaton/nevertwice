#!/usr/bin/env python3
"""RESEARCH - the eff-vs-capability curve for the Active Memory live validation.

Reads the saved `live_validation*.json` runs across models of increasing capability and plots how
much of a delivered memory the agent actually *applies* (`eff`) as a function of model strength.
The finding: memory's payoff scales monotonically with the agent - and, on project-specific
knowledge, jumps sharply around ~7B, i.e. a small model often cannot apply a fact even when told.
Memory removes the knowledge bottleneck, not the capability one.

    python research/eff_curve_figure.py        # → eff_curve.png

Reads numbers, never recomputes; matplotlib optional.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (label, file, approx params for the x-axis ordering). deepseek is 'strong' (671B MoE, ~37B active).
POINTS = [
    ("qwen2.5:3b", "live_validation_weak.json", 3),
    ("qwen3.5:4b", "live_validation_4b.json", 4),
    ("qwen2.5:7b", "live_validation_7b.json", 7),
    ("deepseek-chat", "live_validation_results.json", 37),
]


def _load(f):
    return json.loads((HERE / f).read_text(encoding="utf-8"))["summary"]


def main():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"[eff_curve skipped: matplotlib unavailable - {e}]")
        return

    labels, eff, effp, base = [], [], [], []
    for name, f, _p in POINTS:
        try:
            s = _load(f)
        except Exception:
            continue
        labels.append(name)
        eff.append(s["measured_eff"])
        effp.append(s["eff_project"])
        base.append(s["mean_rate_without"])
    x = list(range(len(labels)))
    green, blue, grey = "#2ea043", "#1f6feb", "#8b949e"

    fig, ax = plt.subplots(figsize=(8.6, 5.0))
    ax.plot(x, eff, "-o", color=green, lw=2.4, ms=9, label="eff - overall (memory applied)")
    ax.plot(x, effp, "--s", color=blue, lw=2.2, ms=8,
            label="eff - project-specific (unknowable facts)")
    ax.plot(x, base, ":^", color=grey, lw=1.6, ms=7, label="base error rate (no memory)")
    for xi, v in zip(x, eff):
        ax.annotate(f"{v:.2f}", (xi, v), textcoords="offset points", xytext=(0, 9),
                    ha="center", fontsize=9, fontweight="bold", color=green)
    for xi, v in zip(x, effp):
        ax.annotate(f"{v:.2f}", (xi, v), textcoords="offset points", xytext=(0, -15),
                    ha="center", fontsize=9, color=blue)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("fraction (higher = memory helps more)")
    ax.set_xlabel("agent capability  →")
    ax.set_title("Memory's payoff scales with the agent - and jumps ~7B on project knowledge\n"
                 "(memory removes the knowledge bottleneck, not the capability one)", fontsize=11)
    ax.legend(fontsize=9, loc="center right")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    out = HERE / "eff_curve.png"
    fig.savefig(out, dpi=130)
    print(f"[eff_curve] wrote {out}")


if __name__ == "__main__":
    main()
