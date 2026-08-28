"""S1-S2: a migration plan that survives a session boundary, and refuses a red step.

The failure this addresses is specific, and it is not a detection failure:

> An agent attempts the migration in one piece, the intermediate states are red, it panics
> and props the result up with workarounds.

So this mechanism is not a detector and has no precision. It is a **format plus a rule**:
a target architecture, an ordered list of seams each leading from a green state to a green
state, and a refusal to attempt a step whose predecessor is not green.

## Why the journal is a file and not a conversation

A plan held in context dies at the session boundary, and the migration outlives the session
by construction -- that is what makes it a migration rather than an edit. So the journal is
written to disk after every seam, carries which seam is next, and can be reloaded by a
process that has never seen the earlier ones. `S2` is exactly that test: reload from disk
with no other state and identify the next step.

## What a seam is allowed to be

Each seam names the change, the verification command, and the state it leaves behind. The
one hard rule is that **a seam's verification must pass before the next seam is attempted**.
Not "should": `next_seam()` returns nothing while the tree is red, so the plan cannot be
advanced past a failure. An agent that wants to keep going has to fix the failure or edit
the journal deliberately -- both of which are visible afterwards, which the panicked
workaround is not.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

GREEN = "green"
RED = "red"
PENDING = "pending"


@dataclass
class Seam:
    """One step from a green state to a green state."""

    name: str
    intent: str                  # what this step changes, in one sentence
    verify: list[str]            # the command that decides green
    status: str = PENDING
    attempts: int = 0
    last_output: str = ""
    finished: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict) -> "Seam":
        return cls(
            name=str(data.get("name", "")),
            intent=str(data.get("intent", "")),
            verify=list(data.get("verify", [])),
            status=str(data.get("status", PENDING)),
            attempts=int(data.get("attempts", 0)),
            last_output=str(data.get("last_output", "")),
            finished=str(data.get("finished", "")),
        )


@dataclass
class Journal:
    """The target, the seams, and where the work has got to."""

    target: str
    seams: list[Seam] = field(default_factory=list)
    started: str = ""
    notes: list[str] = field(default_factory=list)

    # -- persistence ------------------------------------------------------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "kind": "seam_journal",
            "target": self.target,
            "started": self.started or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "notes": self.notes,
            "seams": [s.to_dict() for s in self.seams],
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Journal | None":
        """A journal or nothing. A corrupt one is not half a plan."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("kind") != "seam_journal":
            return None
        return cls(
            target=str(data.get("target", "")),
            seams=[Seam.from_dict(s) for s in data.get("seams", [])],
            started=str(data.get("started", "")),
            notes=list(data.get("notes", [])),
        )

    # -- the rule ---------------------------------------------------------

    def next_seam(self) -> Seam | None:
        """The seam to attempt now, or None.

        None means one of two things and the caller must distinguish them with
        `blocked()`: everything is done, or the previous seam is red and this plan
        does not advance past a failure.
        """
        if self.blocked():
            return None
        return next((s for s in self.seams if s.status != GREEN), None)

    def blocked(self) -> Seam | None:
        """The red seam holding everything up, if there is one."""
        return next((s for s in self.seams if s.status == RED), None)

    def done(self) -> bool:
        return bool(self.seams) and all(s.status == GREEN for s in self.seams)

    def progress(self) -> tuple[int, int]:
        return sum(1 for s in self.seams if s.status == GREEN), len(self.seams)

    # -- delivery ---------------------------------------------------------

    def brief(self) -> str:
        """What a session loading this from cold needs, and nothing else.

        Not the whole plan: the target, where the work is, and the one next step.
        A migration with forty seams should not cost forty seams of context on every
        session start, and the seam after next is not actionable yet.
        """
        if not self.seams:
            return ""
        stuck = self.blocked()
        done, total = self.progress()
        head = f"migration: {self.target} ({done}/{total} seams green)"
        if stuck:
            return (f"{head}\n"
                    f"BLOCKED at {stuck.name}: {stuck.intent}\n"
                    f"  verify: {' '.join(stuck.verify)}\n"
                    f"  the plan does not advance past a red seam; fix it or edit the "
                    f"journal deliberately")
        nxt = self.next_seam()
        if nxt is None:
            return f"{head}\ncomplete"
        return (f"{head}\n"
                f"next: {nxt.name} - {nxt.intent}\n"
                f"  verify: {' '.join(nxt.verify)}")


# --------------------------------------------------------------------------
# S3: executing one
# --------------------------------------------------------------------------


def run_seam(journal: Journal, seam: Seam, *, cwd: Path,
             apply: object = None, timeout: int = 300) -> Seam:
    """Apply one seam and verify it. Green or red; there is no third answer.

    `apply` is whatever performs the change. It is passed in rather than described in
    the journal because a plan that carries executable steps is a plan that runs
    itself, and the point of this mechanism is that a person or an agent performs the
    step and the journal decides whether it counts.
    """
    seam.attempts += 1
    if callable(apply):
        apply()
    proc = subprocess.run(seam.verify, cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)
    seam.last_output = (proc.stdout + proc.stderr)[-2000:]
    seam.status = GREEN if proc.returncode == 0 else RED
    seam.finished = time.strftime("%Y-%m-%dT%H:%M:%S")
    return seam
