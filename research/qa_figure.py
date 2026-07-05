#!/usr/bin/env python3
"""RESEARCH - figure for the end-to-end QA-accuracy study (QA_ACCURACY.md / the paper).

Two panels, both built straight from the saved `qa_results*.json` so the figure can never
drift from the numbers:
  (left)  the reader sweep - oracle answer-accuracy climbs monotonically as the reader is
          upgraded while the memory is held fixed (qwen3:30b → deepseek-chat → +CoT →
          deepseek-reasoner), the visual proof that the memory is not the bottleneck.
  (right) per-question-type accuracy, oracle (reasoner) vs our harder global-pool retrieved,
          localizing where the work remains (temporal / multi-session reasoning).

    python nevertwice/research/qa_figure.py            # → qa_accuracy.png

Research dep: matplotlib (optional; the script no-ops with a message if it is absent). The
numbers are read, never recomputed, so this never touches the network or a model.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
QTYPES = ["single-session-user", "single-session-assistant", "single-session-preference",
          "multi-session", "temporal-reasoning", "knowledge-update"]
SHORT = ["ss-user", "ss-asst", "ss-pref", "multi", "temporal", "know-upd"]


def _oracle(fname):
    return json.loads((HERE / fname).read_text(encoding="utf-8"))["settings"]["oracle"]


def _setting(fname, which):
    return json.loads((HERE / fname).read_text(encoding="utf-8"))["settings"][which]


def main():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"[qa_figure skipped: matplotlib unavailable - {e}]")
        return

    # ── the reader sweep (oracle overall) ──
    sweep = [
        ("qwen3:30b\n(terse)", _oracle("qa_results.json")["accuracy"]),
        ("deepseek-chat\n(terse)", _oracle("qa_results_deepseek.json")["accuracy"]),
        ("deepseek-chat\n(+CoT)", _oracle("qa_results_deepseek_cot.json")["accuracy"]),
        ("deepseek-reasoner", _oracle("qa_results_reasoner.json")["accuracy"]),
    ]
    labels = [s[0] for s in sweep]
    vals = [s[1] for s in sweep]

    # ── per-type: oracle (reasoner) vs retrieved (cot) ──
    orc = _setting("qa_results_reasoner.json", "oracle")["by_type"]
    ret = _setting("qa_results_deepseek_cot.json", "retrieved")["by_type"]
    orc_v = [orc.get(t, {}).get("acc", 0) for t in QTYPES]
    ret_v = [ret.get(t, {}).get("acc", 0) for t in QTYPES]

    green, grey = "#2ea043", "#8b949e"
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6))

    # left - monotone climb, memanto headline as a reference line
    ax1.plot(range(len(vals)), vals, "-o", color=green, lw=2.4, ms=9, zorder=3)
    for i, v in enumerate(vals):
        ax1.annotate(f"{v:.3f}", (i, v), textcoords="offset points", xytext=(0, 10),
                     ha="center", fontsize=10, fontweight="bold", color=green)
    ax1.axhline(0.898, ls="--", color=grey, lw=1.3)
    ax1.text(len(vals) - 1, 0.905, "memanto 0.898 (closed engine)", ha="right",
             va="bottom", fontsize=9, color=grey)
    ax1.set_xticks(range(len(labels)))
    ax1.set_xticklabels(labels, fontsize=9)
    ax1.set_ylim(0.55, 0.95)
    ax1.set_ylabel("LongMemEval-oracle answer-accuracy")
    ax1.set_title("Reader sweep - memory held fixed, only the reader changes", fontsize=11)
    ax1.grid(axis="y", alpha=0.25)

    # right - per-type oracle vs retrieved
    x = range(len(QTYPES))
    w = 0.38
    ax2.bar([i - w / 2 for i in x], orc_v, w, label="oracle (reasoner)", color=green)
    ax2.bar([i + w / 2 for i in x], ret_v, w, label="retrieved (global pool)", color=grey)
    ax2.set_xticks(list(x))
    ax2.set_xticklabels(SHORT, fontsize=9, rotation=20)
    ax2.set_ylim(0, 1.05)
    ax2.set_ylabel("answer-accuracy")
    ax2.set_title("By question type - where the work remains", fontsize=11)
    ax2.legend(fontsize=9, loc="upper right")
    ax2.grid(axis="y", alpha=0.25)

    fig.suptitle("Nevertwice end-to-end QA accuracy on LongMemEval (500 questions)",
                 fontsize=12, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    out = HERE / "qa_accuracy.png"
    fig.savefig(out, dpi=130)
    print(f"[qa_figure] wrote {out}")


if __name__ == "__main__":
    main()
