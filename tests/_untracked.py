"""Checks that can only be made where an untracked artifact exists - and how to say so.

Four lab suites assert that a sweep did not clobber the shipped embedder
(`research/embed_universal/models/universal_v1_merged`). The weights are deliberately not in
git - they are gigabytes, and `research/embed_universal/models/.gitignore` says so - which means
the directory exists on the machine that trained them and nowhere else.

Written as a plain `check`, that turns into a FAILURE on every other machine: a fresh clone, a
colleague's laptop, all three legs of CI. And a failure is a specific claim - "this property was
tested and does not hold" - which is not what happened. What happened is that the property was
not testable there. The difference is not pedantry: a red suite sends the next person hunting a
defect that is not in the code, and after the second false hunt people stop reading the colour.

Skipping is the other wrong answer, and the one this repository already refuses elsewhere: a
check that quietly returns green when it did nothing is how a contract shrinks to nothing while
the board stays clean. So an abstention is LOUD and COUNTED - printed on its own line with its
reason, tallied apart from passed and failed, and reported in the summary so a run that proved
less than usual says how much less.

    from _untracked import Gate

    gate = Gate("the trained embedder", MODELS / "universal_v1_merged",
                "gigabytes of weights, ignored by research/embed_universal/models/.gitignore")
    if gate.present:
        check("the shipped model is untouched", gate.path.is_dir())
    else:
        gate.abstain("the shipped model is untouched")

    ...
    print(f"capacity sweep: {PASSED} passed, {FAILED} failed{gate.summary}")
"""
from __future__ import annotations

from pathlib import Path


class Gate:
    """One untracked artifact, the checks that need it, and the count of what it cost."""

    def __init__(self, what: str, path: Path, why_untracked: str):
        self.what = what
        self.path = Path(path)
        self.why_untracked = why_untracked
        self.abstained = 0

    @property
    def present(self) -> bool:
        return self.path.exists()

    def abstain(self, name: str) -> None:
        """Record that `name` could not be judged here, and say why on its own line."""
        self.abstained += 1
        print(f"  --   {name}  [not judged here: {self.what} is absent - "
              f"{self.why_untracked}; run the training first, or judge this on the machine "
              f"that has it]")

    @property
    def summary(self) -> str:
        """The tail of a suite's summary line, empty when nothing was abstained."""
        return f", {self.abstained} not judged here" if self.abstained else ""
