"""R2: the rule. A diff may not make a file worse than it already was.

**Differential, not absolute.** "Complexity 47, bad" is a number everyone ignores, because
it is true of code nobody is touching and says nothing about the change in front of them.
The claim a ratchet can actually enforce is narrower and enforceable: *this diff must not
degrade this file against its own baseline.*

Three consequences follow, and each is a decision rather than an implementation detail.

**The baseline is stored, not recomputed.** If the rule compared *after* to *before* it
would permit a slow climb: +1 every commit is never a degradation against the previous
commit and is a disaster against last quarter. The baseline is a note, it only ever moves
**down**, and moving it down is automatic -- that is what makes it a ratchet rather than a
threshold.

**Exemption is one command.** `exempt(note, qualname, reason)`. The spec is blunt about why:
if raising complexity legitimately requires an argument with a tool, the tool gets disabled
the first time complexity legitimately has to rise, and after that being right does not
matter. An exemption carries a reason and is visible in the note; a mechanism whose escape
hatch is undocumented has an escape hatch nobody can review.

**It reports the axis, not a verdict.** "`f` got worse" is not actionable. "`f`: cyclomatic
12 -> 15" is.
"""

from __future__ import annotations

from dataclasses import dataclass

from complexity import FuncMetrics, scan_file
from invariant_notes import Finding

#: The axes a ratchet holds. Ordered: the first one that regressed is the one reported,
#: because one finding per diff is the delivery contract and a list of four is a list
#: nobody reads.
AXES = ("cyclomatic", "nesting", "returns", "length")

#: Slack per axis. Zero for the structural axes -- a branch is a branch -- and a small
#: allowance for `length`, because reformatting, a docstring or a type annotation can add
#: lines without adding anything a reader has to hold in their head. Without it the
#: ratchet fires on `black`.
SLACK = {"cyclomatic": 0, "nesting": 0, "returns": 0, "length": 10}


@dataclass(frozen=True)
class Regression:
    path: str
    qualname: str
    axis: str
    baseline: int
    now: int

    def describe(self) -> str:
        return f"{self.qualname}: {self.axis} {self.baseline} -> {self.now}"


# --------------------------------------------------------------------------
# the baseline note
# --------------------------------------------------------------------------


def snapshot(sources: dict[str, str]) -> dict[str, dict[str, dict[str, int]]]:
    """The metrics a baseline note stores: path -> qualname -> axis -> value."""
    out: dict[str, dict[str, dict[str, int]]] = {}
    for path, src in sources.items():
        if not path.endswith(".py"):
            continue
        metrics = scan_file(src)
        if metrics:
            out[path] = {
                q: {axis: getattr(m, axis) for axis in AXES}
                for q, m in metrics.items()
            }
    return out


def make_baseline(sources: dict[str, str], *, project: str | None = None) -> dict:
    return {
        "kind": "ratchet_baseline",
        "project": project,
        "exemptions": {},
        "metrics": snapshot(sources),
    }


def exempt(baseline: dict, qualname: str, reason: str) -> dict:
    """One call, a reason required. The escape hatch has to be cheaper than the argument."""
    if not reason.strip():
        raise ValueError("an exemption without a reason is an exemption nobody can review")
    baseline.setdefault("exemptions", {})[qualname] = reason.strip()[:240]
    return baseline


def is_exempt(baseline: dict, qualname: str) -> bool:
    return qualname in (baseline.get("exemptions") or {})


# --------------------------------------------------------------------------
# the rule
# --------------------------------------------------------------------------


def regressions(baseline: dict, after: dict[str, str]) -> list[Regression]:
    """Every axis on which a callable is worse than its stored baseline.

    A callable with no baseline entry is **new**, and new code has no baseline to be
    worse than. Reporting it would turn the ratchet into an absolute threshold wearing
    a differential's clothes, which is the thing this rule exists not to be.
    """
    stored = baseline.get("metrics") or {}
    out: list[Regression] = []
    for path, src in after.items():
        if not path.endswith(".py"):
            continue
        was = stored.get(path) or {}
        for qualname, metrics in scan_file(src).items():
            old = was.get(qualname)
            if old is None or is_exempt(baseline, qualname):
                continue
            for axis in AXES:
                before, now = old.get(axis), getattr(metrics, axis)
                if before is None:
                    continue
                if now > before + SLACK.get(axis, 0):
                    out.append(Regression(path, qualname, axis, before, now))
    return out


def tighten(baseline: dict, after: dict[str, str]) -> int:
    """Click the ratchet: where a callable improved, its baseline follows it down.

    Only downward. An improvement that does not lower the baseline is an improvement
    the next commit may silently spend, which is exactly the slow climb the stored
    baseline exists to prevent.
    """
    stored = baseline.setdefault("metrics", {})
    moved = 0
    for path, src in after.items():
        if not path.endswith(".py"):
            continue
        was = stored.setdefault(path, {})
        for qualname, metrics in scan_file(src).items():
            old = was.get(qualname)
            if old is None:
                was[qualname] = {axis: getattr(metrics, axis) for axis in AXES}
                continue
            for axis in AXES:
                now = getattr(metrics, axis)
                if old.get(axis) is None or now < old[axis]:
                    old[axis] = now
                    moved += 1
    return moved


def checker(before: dict[str, str], after: dict[str, str], note: dict) -> list[Finding]:
    """The `invariant_notes` interface: at most one finding, the worst axis first.

    The baseline travels in the note itself, so the hot path stays a pure function of
    (diff, note) and the mechanism has nowhere to hide state the reader cannot see.
    """
    baseline = note.get("baseline") or {}
    found = regressions(baseline, after)
    if not found:
        return []
    order = {axis: i for i, axis in enumerate(AXES)}
    found.sort(key=lambda r: (order.get(r.axis, 99), r.path, r.qualname))
    worst = found[0]
    return [Finding(
        note["id"],
        note.get("message") or "this diff makes a file worse than its own baseline",
        subject=f"{worst.path}::{worst.describe()}",
        rank=1.0 - order.get(worst.axis, 99) / 100,
        evidence=f"{len(found)} regression(s) in this diff; the baseline only moves down",
    )]
