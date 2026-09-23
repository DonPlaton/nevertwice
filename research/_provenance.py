"""One provenance stamp, shared by every research stand that writes an artifact.

The auditor's ask: an artifact should prove its own commit, not lean on a time window (the same
gap the F6 restamp trap exploits at the cache layer, one level up - `frontier_eval.py`'s own
`check_engine_freshness`). `stamp(payload)` adds one key, `measured_at`, carrying the commit the
run's Python process actually had checked out, the UTC moment it ran, and whether the working
tree was dirty at that moment - a dirty run's numbers are NOT reproducible from the commit alone,
and the artifact says so rather than looking identical to a clean one.

    import sys
    sys.path.insert(0, str(ROOT))                 # research/ itself, wherever ROOT resolves to
    import _provenance as prov
    ...
    out = {...}
    prov.stamp(out)
    Path(args.out).write_text(json.dumps(out, ...), ...)

Standard library only - every research script already pays for a `subprocess` import somewhere
in this tree, and this module adds no new dependency to it.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: The two directories `dirty` watches - the engine and the stands that measure it. A change
#: outside both (docs, .loop/, tests/) does not make a MEASUREMENT non-reproducible from its
#: commit, so it is not asked about here.
WATCHED_DIRS = ("nevertwice/", "research/")


def git_commit() -> str:
    """`HEAD`'s full SHA, or a `?(...)` placeholder naming the failure - never raises, because a
    stamp that crashes a run over a missing `git` binary is a worse failure than an honest '?'."""
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True,
                             text=True, timeout=10, check=True)
        return res.stdout.strip()
    except (OSError, ValueError) as e:
        return f"?({type(e).__name__})"
    except Exception:                                              # noqa: BLE001 - CalledProcessError etc.
        return "?"


def is_dirty(paths: tuple[str, ...] = WATCHED_DIRS) -> bool:
    """True iff any TRACKED file under `paths` differs from `HEAD` - staged or not. An untracked
    new file is deliberately invisible here: `git diff` never sees one, and a stand's own scratch
    output living beside its source (a `*_cache.json`, a `.tmp`) must not read as "dirty" for
    every run that ever writes one. A git failure (no repository, no `git` on PATH) reads as
    dirty: an unprovable "clean" is not a clean, and this is the conservative default the
    freshness checks elsewhere in this tree already use."""
    try:
        res = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *paths],
                             cwd=str(ROOT), timeout=30, capture_output=True)
        return res.returncode != 0
    except (OSError, ValueError):
        return True


def measured_at() -> dict:
    """`{commit, utc, dirty}` - the shape `stamp` writes under `measured_at`, built separately so
    a caller that wants the dict WITHOUT mutating anything (a check, a comparison) can ask for it
    without owning a payload to stamp."""
    return {"commit": git_commit(), "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "dirty": is_dirty()}


def stamp(payload: dict) -> dict:
    """Add `measured_at: {commit, utc, dirty}` to `payload` and return it (mutated in place, for
    a one-line call at the write site: `Path(out).write_text(json.dumps(stamp(res)))`).

    A stand that already has its OWN `measured_at` (research/ride_along_judge_eval.py,
    research/principle_twins.py) or a differently-named provenance field it writes for a
    different reason (`supersession_bench.py`'s `code_sha`, a hash of the SOURCE FILES for its
    own `--runs N` cross-process identity check, not a git commit) keeps that field - this adds
    or overwrites `measured_at` specifically, the one key name every stand is asked to share, and
    touches nothing else in `payload`.

    Prints a warning to stderr when the tree was dirty: silence would let a dirty artifact look
    exactly like a clean one to anyone who does not think to check the flag."""
    m = measured_at()
    payload["measured_at"] = m
    if m["dirty"]:
        print(f"[_provenance] WARNING: the working tree is dirty (nevertwice/ or research/ "
             f"differs from HEAD {m['commit'][:12]}) - this artifact's numbers are not "
             f"reproducible from the commit alone", file=sys.stderr)
    return payload
