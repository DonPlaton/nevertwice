"""Two corpora with different rights, and a seal between them.

`GOAL-SHIP.md` §0.3 is the whole reason this module exists. The eight repositories the
previous run used were the set on which the defect classes D1-D7 were *found* and the set
on which every gate was *scored*. That is legitimate development and it is not evidence:
a checker tuned until it stops making the mistakes you can see on eight repositories has
been fitted to those eight repositories, and its numbers say so.

So the eight become `dev`, frozen, named, and free to tune against. Everything a public
claim rests on is measured once on `heldout` -- repositories cloned after the code is
frozen and never opened before.

The seal is the mechanism that makes "never opened before" a fact rather than an
intention. `heldout_repos()` raises while it is closed; `heldout_seal.json` records when
and against which code freeze it opened. `tests/_test_corpus_freeze.py` adds the other
half -- no module outside `HELDOUT_READERS` may spell the held-out path at all, so the
runtime guard cannot be stepped around with a path literal.

Nothing here reads a repository. It resolves directories and answers questions about
which of them a caller is entitled to see.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The polygon lives outside git: multi-gigabyte, disposable, regenerable from a manifest.
POLYGON = Path("D:/Coding/_nevertwice_polygon")
DEV_ROOT = POLYGON / "corpus"
HELDOUT_ROOT = POLYGON / "corpus_heldout"

DEV_FREEZE_PATH = HERE / "corpus_dev.json"
SEAL_PATH = HERE / "heldout_seal.json"

#: Modules entitled to name the held-out corpus. A Phase-H or Phase-V script joins this
#: list by being written and added here -- never by a glob widening underneath it.
HELDOUT_READERS: tuple[str, ...] = ("corpora.py",)


class HeldOutSealed(RuntimeError):
    """Raised when something asks for the held-out corpus before it is allowed to."""


# ---------------------------------------------------------------------------
# the development set
# ---------------------------------------------------------------------------

def dev_freeze() -> dict:
    """The frozen record of the eight development repositories.

    Pinned HEADs, licences, and the positives each contributes to the answer key. The
    counts are asserted against `mutants.json` by the suite, so a regenerated answer key
    that no longer matches this file is a red test rather than a silent drift.
    """
    return json.loads(DEV_FREEZE_PATH.read_text(encoding="utf-8"))


def dev_repos(root: Path | None = None) -> list[Path]:
    """Cloned development repositories, in a stable order so runs are comparable.

    Returns an empty list when the polygon is absent, exactly as the old `corpusio` helper
    did: a suite that needs the corpus to exist would be red on every fresh clone.
    """
    base = root or DEV_ROOT
    if not base.exists():
        return []
    return sorted(p for p in base.iterdir() if (p / ".git").exists())


def dev_slugs() -> set[str]:
    """The eight `owner/name` slugs, so Phase H can prove it selected none of them."""
    return {r["slug"] for r in dev_freeze()["repos"]}


# ---------------------------------------------------------------------------
# the held-out set, and the seal in front of it
# ---------------------------------------------------------------------------

def heldout_seal() -> dict:
    """The seal record: whether the held-out corpus may be read, and against what."""
    if not SEAL_PATH.exists():  # pragma: no cover - the file is committed
        return {"phase": "unknown", "open": False, "opened_at": None, "frozen_code": {}}
    return json.loads(SEAL_PATH.read_text(encoding="utf-8"))


def seal_is_open() -> bool:
    return bool(heldout_seal().get("open"))


def heldout_repos(root: Path | None = None) -> list[Path]:
    """Cloned held-out repositories -- only once the seal has been opened.

    Raising rather than returning an empty list is deliberate. An empty list looks like
    "the corpus is not built yet" and a caller will happily carry on and report a number
    computed over nothing; an exception cannot be mistaken for a measurement.
    """
    seal = heldout_seal()
    if not seal.get("open"):
        raise HeldOutSealed(
            "the held-out corpus is sealed until Phase V "
            "(seal phase: " + str(seal.get("phase")) + "). "
            "H1 freezes the code and preregisters the thresholds; the seal opens there "
            "and records the frozen hashes it was opened against. "
            "Development measurements read corpora.dev_repos()."
        )
    base = root or HELDOUT_ROOT
    if not base.exists():
        return []
    return sorted(p for p in base.iterdir() if (p / ".git").exists())


# ---------------------------------------------------------------------------
# disk accounting -- §2 asks for corpus size at every phase boundary
# ---------------------------------------------------------------------------

def disk_gb(path: Path) -> float:
    """Bytes on disk under `path`, in GB. Missing directories are 0.0, not an error."""
    if not path.exists():
        return 0.0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:  # pragma: no cover - transient on Windows during a clone
            continue
    return round(total / 1024**3, 3)


def _print() -> None:
    freeze = dev_freeze()
    seal = heldout_seal()
    print("dev corpus (frozen, in-sample -- tuning is legitimate here)")
    print("  root        " + str(DEV_ROOT))
    print("  frozen      " + freeze["frozen"])
    print("  repos       " + str(len(freeze["repos"])) + " on disk: "
          + str(len(dev_repos())))
    print("  positives   " + str(freeze["positives"]) + " in "
          + str(freeze["source_commits"]) + " source commits")
    print()
    print("held-out corpus (out-of-sample -- every published number)")
    print("  root        " + str(HELDOUT_ROOT))
    print("  seal        " + ("OPEN" if seal["open"] else "CLOSED")
          + "  phase=" + str(seal["phase"]))
    if seal["open"]:
        print("  opened      " + str(seal["opened_at"]))
    print()
    print("polygon disk " + str(disk_gb(POLYGON)) + " GB of a 100 GB budget")


if __name__ == "__main__":
    _print()
