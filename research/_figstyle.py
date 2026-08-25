"""One look for every published figure, and no figure that cannot say where it came from.

Eleven benches each called `fig.savefig(path, dpi=130)` with default Matplotlib styling. That
produced three problems a reader can see and one they cannot:

* **default styling** - the tab10 palette is not colourblind-safe, and several charts encoded
  a pass/fail distinction in green versus red alone, which about one man in twelve cannot
  separate;
* **raster only, at 130 dpi** - fine on the screen it was made on, blurred on any other;
* **no provenance** - a chart travels further than the page it sits in, and every one of these
  arrived at its destination with no sample size, no dataset, no model and no command;
* **negative results drawn quieter than positive ones** - this project publishes the
  mechanisms it deleted, and a chart that renders them in a thinner line is arguing.

So: `apply()` for the look, and `save()` which refuses to write a figure that has no evidence
line. The evidence comes from `research/evidence_manifest.json` where the number is registered
(`save(..., claim="longmem.fusion.recall_at_5")`) and is written out by hand where it is not
(`save(..., evidence="n=200 tasks · synthetic · ...")`), so the honest state is visible either
way rather than absent.

Colours are the Okabe-Ito palette, which stays distinguishable under all three common colour
vision deficiencies. `NEGATIVE` is paired with a hatch so the meaning survives greyscale
printing and a screenshot run through a filter.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Okabe-Ito. Ordered so the first two - blue and orange - carry the most common two-series
# comparison, and are the pair with the largest separation for every deficiency type.
PALETTE = ["#0072B2", "#E69F00", "#009E73", "#CC79A7",
           "#56B4E9", "#D55E00", "#F0E442", "#000000"]

INK = "#1A1A1A"          # 16.1:1 on white - WCAG AAA for body text
MUTED = "#555555"        #  7.5:1 on white - AAA for the footer, AA for anything smaller
GRID = "#D6D6D6"
PAPER = "#FFFFFF"

# Status colours. Never used alone: the shipped/deleted distinction is carried by the hatch as
# well, so it survives greyscale, a colourblind reader, and a compressed screenshot.
POSITIVE = "#0072B2"
NEGATIVE = "#D55E00"
NEGATIVE_HATCH = "//"
NEUTRAL = "#8C8C8C"

_STYLE = {
    "figure.facecolor": PAPER,
    "figure.edgecolor": PAPER,
    "savefig.facecolor": PAPER,
    "savefig.edgecolor": PAPER,
    "axes.facecolor": PAPER,
    "axes.edgecolor": "#B0B0B0",
    "axes.labelcolor": INK,
    "axes.titlecolor": INK,
    "axes.titlesize": 11,
    "axes.titleweight": "bold",
    "axes.labelsize": 9.5,
    "axes.grid": True,
    "axes.axisbelow": True,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "grid.color": GRID,
    "grid.linewidth": 0.7,
    "text.color": INK,
    "xtick.color": INK,
    "ytick.color": INK,
    "xtick.labelsize": 8.5,
    "ytick.labelsize": 8.5,
    "legend.frameon": False,
    "legend.fontsize": 8.5,
    "lines.linewidth": 2.0,
    "lines.markersize": 5.0,
    "font.size": 9.5,
}

# Where the footer sits, as a fraction of the figure height reserved below the axes.
_FOOTER_BAND = 0.055


def apply() -> None:
    """Set the shared look. Idempotent; safe to call from every bench."""
    import matplotlib as mpl
    from cycler import cycler

    mpl.rcParams.update(_STYLE)
    mpl.rcParams["axes.prop_cycle"] = cycler(color=PALETTE)


def footer_for(claim_id: str) -> str:
    """The manifest's own evidence line for a registered claim.

    Delegates to `tools/render_claims.py`, which is the single renderer for these strings, so
    a caption and a chart footer can never disagree about what a number means.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    import render_claims                                # noqa: PLC0415 - deliberately late
    return render_claims.footer(render_claims.Claims(render_claims.load_manifest()), claim_id)


def save(fig, path, *, claim: str | None = None, evidence: str | None = None,
         dpi: int = 260) -> list:
    """Write `path` as SVG **and** PNG at 2x, with the evidence line drawn into the figure.

    Exactly one of `claim` (a manifest claim id) or `evidence` (a written line) is required.
    A figure with neither is not saved - `ValueError` - because the failure this prevents is
    a chart circulating with no way back to what produced it, and a warning would not prevent
    it.
    """
    if (claim is None) == (evidence is None):
        raise ValueError(
            "save() needs exactly one of claim=<manifest id> or evidence=<written line>: "
            "a published figure has to carry its provenance")
    line = footer_for(claim) if claim is not None else evidence

    path = Path(path)
    fig.subplots_adjust(bottom=max(fig.subplotpars.bottom, _FOOTER_BAND + 0.06))
    fig.text(0.006, 0.010, line, fontsize=6.5, color=MUTED, ha="left", va="bottom",
             wrap=True)

    written = []
    for suffix, kwargs in ((".svg", {}), (".png", {"dpi": dpi})):
        target = path.with_suffix(suffix)
        fig.savefig(target, **kwargs)
        written.append(target)
    return written
