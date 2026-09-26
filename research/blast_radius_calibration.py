#!/usr/bin/env python3
"""RESEARCH - calibrate the blast-radius checker against this repository's history (I1).

The checker shipped with budgets that were guessed. A twelve-commit spot check flagged 10 of
12 and produced zero dependency findings; every flag was `over-reach: L0 allows 3 file(s)` or
`L2 requires a written plan`. This replays the checker over the real history and publishes the
distribution the budgets should have come from.

The thresholds this run is judged against were written first, in
``research/BLAST_RADIUS_THRESHOLDS.md``, and committed before this file existed.

Three arms, because any one alone would prove nothing:

* **shipped** - the pre-I1 policy, restored from three module constants rather than a forked
  copy. This is the baseline the change is measured against, and it stays reproducible.
* **undeclared** - what a repository that never declares a scope actually sees. T1 (flag rate)
  and T2 (composition) are judged here, exactly as declared.
* **declared** - every commit replayed with its scope declared as the class the checker itself
  infers. This is the strongest honest test of a budget: an agent that declares precisely what
  it is doing must not then be charged for it. Without this arm T2 would be vacuous once
  budgets became declaration-only, so it is reported alongside rather than instead.

Nothing is checked out. Sources are read straight from the object database through one
``git cat-file --batch`` process, so a replay of 150 commits does not touch the working tree and
cannot leave it on a past revision. ``--worktree`` additionally runs every git command inside a
detached worktree, which is what ``.loop/GOAL-NEXT.md`` asks for.

    python research/blast_radius_calibration.py                    # replay, write the artifact
    python research/blast_radius_calibration.py --commits 50       # shorter run
    python research/blast_radius_calibration.py --print            # summarise the artifact

Standard library only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "research" / "blast_radius_calibration.json"

#: The set the thresholds document froze: the most recent commits reachable from this ref.
DEFAULT_BASE = "7ef8ad2"
DEFAULT_COMMITS = 150

#: Extensions the reference scan covers. The checker parses .py with ast and treats anything
#: else as low-confidence text; the calibration measures the default surface only.
SCAN_SUFFIX = ".py"


def _load_checker():
    """Import the checker by path, exactly as its own suite does."""
    path = ROOT / "research" / "invariants_lab" / "blast_radius_deleted.py"
    spec = importlib.util.spec_from_file_location("_nt_blast_radius_cal", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# git, without checking anything out
# ---------------------------------------------------------------------------


class Repo:
    """Read-only access to blobs and diffs, with one long-lived cat-file process.

    Reading 150 revisions of ~150 files one ``git show`` at a time is 22,500 process
    spawns; on Windows that alone outweighs everything being measured. ``cat-file --batch``
    answers all of them down one pipe, and an oid cache collapses the ~97% of blobs that two
    neighbouring commits share.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._batch = subprocess.Popen(
            ["git", "-C", str(path), "cat-file", "--batch"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0,
        )
        self._cache: dict[str, str] = {}

    # -- lifecycle --------------------------------------------------------
    def close(self) -> None:
        if self._batch.poll() is None:
            try:
                self._batch.stdin.close()
                self._batch.wait(timeout=30)
            except Exception:  # pragma: no cover - best effort
                self._batch.kill()

    def __enter__(self) -> "Repo":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- plumbing ---------------------------------------------------------
    def run(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout

    def blob(self, oid: str) -> str:
        """Blob *oid* decoded as text. Binary and unreadable blobs come back empty."""
        hit = self._cache.get(oid)
        if hit is not None:
            return hit
        self._batch.stdin.write((oid + "\n").encode())
        header = self._batch.stdout.readline().decode("utf-8", "replace").split()
        if len(header) < 3:
            self._cache[oid] = ""
            return ""
        size = int(header[2])
        payload = b""
        while len(payload) < size:
            chunk = self._batch.stdout.read(size - len(payload))
            if not chunk:  # pragma: no cover - truncated pipe
                break
            payload += chunk
        self._batch.stdout.read(1)  # trailing newline git always emits
        text = payload.decode("utf-8", "replace")
        self._cache[oid] = text
        return text

    def tree(self, sha: str, suffix: str = SCAN_SUFFIX) -> dict[str, str]:
        """path -> blob oid for every tracked file with *suffix* at *sha*."""
        out: dict[str, str] = {}
        for line in self.run("ls-tree", "-r", sha).splitlines():
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) < 3 or parts[1] != "blob":
                continue
            if suffix and not path.endswith(suffix):
                continue
            out[path] = parts[2]
        return out

    def commits(self, base: str, limit: int) -> list[str]:
        listing = self.run("rev-list", "--no-merges", f"--max-count={limit}", base)
        return listing.split()

    def subject(self, sha: str) -> str:
        return self.run("log", "-1", "--format=%s", sha).strip()

    def changed(self, sha: str) -> list[tuple[str, str]]:
        """(status, path) for *sha* against its first parent. Empty for a root commit."""
        parents = self.run("rev-list", "--parents", "-n", "1", sha).split()
        if len(parents) < 2:
            return []
        listing = self.run("diff", "--name-status", "--no-renames", f"{sha}^", sha)
        out = []
        for line in listing.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                out.append((parts[0].strip(), parts[-1].strip()))
        return out


# ---------------------------------------------------------------------------
# classifying what the checker said
# ---------------------------------------------------------------------------

#: The one problem shape that is a dependency finding. Fixed in the thresholds document
#: before the run so it cannot be renegotiated afterwards.
DEPENDENCY = re.compile(r"^[\w.]+: contract changed, \d+ reference\(s\) left untouched$")


def classify(problem: str) -> str:
    if DEPENDENCY.match(problem):
        return "dependency"
    if problem.startswith("over-reach:"):
        return "over_reach"
    if "requires a written plan" in problem:
        return "plan"
    if problem.startswith("declared "):
        return "declared_mismatch"
    return "other"


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile. Deterministic, no interpolation, no numpy."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100.0 * len(ordered)))
    return float(ordered[min(rank, len(ordered)) - 1])


def summarise(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50": percentile(values, 50),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values) if values else 0.0,
    }


# ---------------------------------------------------------------------------
# the replay
# ---------------------------------------------------------------------------


#: The budgets the checker shipped with, before I1 measured anything.
SHIPPED_BUDGETS = {"L0": (3, 1, 150), "L1": (12, 4, 500), "L2": (None, None, None)}


class shipped_behaviour:
    """Context manager restoring the pre-I1 policy, so the baseline stays reproducible.

    A negative result that can only be quoted from a commit message is not evidence. Three
    module constants carry the whole policy change, so putting them back is the entire
    baseline - no fork of the checker, no second copy to drift.
    """

    def __init__(self, br) -> None:
        self.br = br

    def __enter__(self):
        self.saved = (self.br.BUDGETS, self.br.BUDGET_SCOPE, self.br.PLAN_ALWAYS_REQUIRED)
        self.br.BUDGETS = dict(SHIPPED_BUDGETS)
        self.br.BUDGET_SCOPE = "always"
        self.br.PLAN_ALWAYS_REQUIRED = True
        return self.br

    def __exit__(self, *exc):
        self.br.BUDGETS, self.br.BUDGET_SCOPE, self.br.PLAN_ALWAYS_REQUIRED = self.saved


def replay(repo: Repo, shas: list[str], br, *, declare: bool) -> list[dict]:
    rows = []
    for sha in shas:
        changed = repo.changed(sha)
        if not changed:
            continue
        parent_tree = repo.tree(f"{sha}^", suffix="")
        tree = repo.tree(sha, suffix="")
        before, after = {}, {}
        for status, path in changed:
            if not status.startswith("A") and path in parent_tree:
                before[path] = repo.blob(parent_tree[path])
            if not status.startswith("D") and path in tree:
                after[path] = repo.blob(tree[path])
        scan = {p: repo.blob(o) for p, o in tree.items() if p.endswith(SCAN_SUFFIX)}

        started = time.perf_counter()
        verdict = br.check_sources(before, after, scan=scan, ignore=[])
        declared = verdict.inferred if declare else None
        if declare:
            verdict = br.check_sources(before, after, scan=scan, declared=declared, ignore=[])
        elapsed = time.perf_counter() - started

        kinds = [classify(p) for p in verdict.problems]
        rows.append({
            "sha": sha,
            "subject": repo.subject(sha)[:90],
            "declared": declared,
            "inferred": verdict.inferred,
            "ok": verdict.ok,
            "stats": dict(verdict.stats),
            "problems": list(verdict.problems),
            "problem_kinds": kinds,
            "unhandled_symbols": len(verdict.unhandled),
            "unhandled_refs": sum(len(v) for v in verdict.unhandled.values()),
            "notes": len(verdict.notes),
            "seconds": round(elapsed, 4),
        })
    return rows


def aggregate(rows: list[dict]) -> dict:
    flagged = [r for r in rows if not r["ok"]]
    kinds: dict[str, int] = {}
    for row in flagged:
        for kind in row["problem_kinds"]:
            kinds[kind] = kinds.get(kind, 0) + 1
    by_class: dict[str, dict] = {}
    for cls in ("L0", "L1", "L2"):
        members = [r for r in rows if r["inferred"] == cls]
        if not members:
            continue
        by_class[cls] = {
            "commits": len(members),
            "files": summarise([r["stats"]["files"] for r in members]),
            "dirs": summarise([r["stats"]["dirs"] for r in members]),
            "lines": summarise([r["stats"]["lines"] for r in members]),
            "contract_changes": summarise([r["stats"]["contract_changes"] for r in members]),
        }
    seconds = [r["seconds"] for r in rows]
    return {
        "commits": len(rows),
        "flagged": len(flagged),
        "flag_rate": round(len(flagged) / len(rows), 4) if rows else 0.0,
        "problem_kinds": kinds,
        "commits_with_a_dependency_finding": sum(
            1 for r in flagged if "dependency" in r["problem_kinds"]
        ),
        "commits_flagged_only_by_budget_or_plan": sum(
            1 for r in flagged
            if r["problem_kinds"] and all(k in ("over_reach", "plan", "declared_mismatch")
                                          for k in r["problem_kinds"])
        ),
        "by_inferred_class": by_class,
        "seconds": {
            "total": round(sum(seconds), 2),
            "median": round(percentile(seconds, 50), 4),
            "p95": round(percentile(seconds, 95), 4),
            "max": round(max(seconds), 4) if seconds else 0.0,
        },
    }


def proposed_budgets(agg: dict) -> dict:
    """T3: the 95th percentile of each class's own distribution, rounded up.

    Rounding is to the next multiple of 1 for files and dirs and of 25 for lines, so the
    published budget is a number a human can hold rather than a percentile artefact. L2 stays
    unbounded: it is the class that means "architectural", and a ceiling on it would only
    restate the class.
    """
    out: dict[str, list | None] = {}
    for cls in ("L0", "L1"):
        stats = agg["by_inferred_class"].get(cls)
        if not stats:
            out[cls] = None
            continue
        out[cls] = [
            int(math.ceil(stats["files"]["p95"])),
            int(math.ceil(stats["dirs"]["p95"])),
            int(math.ceil(stats["lines"]["p95"] / 25.0) * 25),
        ]
    out["L2"] = [None, None, None]
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _make_worktree(base: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix="nt_blast_cal_"))
    target = path / "wt"
    subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(target), base],
        capture_output=True, text=True, check=True,
    )
    return target


def _drop_worktree(target: Path) -> None:
    subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "remove", "--force", str(target)],
        capture_output=True, text=True, check=False,
    )
    shutil.rmtree(target.parent, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--commits", type=int, default=DEFAULT_COMMITS)
    parser.add_argument("--worktree", action="store_true", default=True,
                        help="run every git command inside a detached worktree (default)")
    parser.add_argument("--no-worktree", dest="worktree", action="store_false")
    parser.add_argument("--out", default=str(ARTIFACT))
    parser.add_argument("--print", dest="show", action="store_true",
                        help="summarise the committed artifact and exit")
    args = parser.parse_args(argv)

    if args.show:
        payload = json.loads(Path(args.out).read_text(encoding="utf-8"))
        report(payload)
        return 0

    br = _load_checker()
    target = _make_worktree(args.base) if args.worktree else ROOT
    try:
        with Repo(target) as repo:
            shas = repo.commits(args.base, args.commits)
            if len(shas) < args.commits:
                # A shallow clone cannot reach the 150th ancestor. Replaying whatever it does
                # have would quietly produce a different calibration set under the same name,
                # which is worse than not running: the artifact would look regenerated.
                raise SystemExit(
                    f"only {len(shas)} commits reachable from {args.base}, "
                    f"{args.commits} requested - this looks like a shallow clone. "
                    f"Run `git fetch --unshallow` first."
                )
            arms = {}
            with shipped_behaviour(br):
                rows = replay(repo, shas, br, declare=False)
            arms["shipped"] = {"rows": rows, "summary": aggregate(rows)}
            for name, declare in (("undeclared", False), ("declared", True)):
                rows = replay(repo, shas, br, declare=declare)
                arms[name] = {"rows": rows, "summary": aggregate(rows)}
    finally:
        if args.worktree:
            _drop_worktree(target)

    payload = {
        "generated_by": "research/blast_radius_calibration.py",
        "thresholds": "research/BLAST_RADIUS_THRESHOLDS.md",
        "base": args.base,
        "requested_commits": args.commits,
        "shas": shas,
        "scan_suffix": SCAN_SUFFIX,
        "budgets_in_effect": {k: list(v) for k, v in br.BUDGETS.items()},
        "budget_scope": br.BUDGET_SCOPE,
        "shipped_budgets": {k: list(v) for k, v in SHIPPED_BUDGETS.items()},
        "arms": arms,
        "proposed_budgets": proposed_budgets(arms["declared"]["summary"]),
    }
    import _provenance as prov  # noqa: PLC0415 - (б) b-c: measured_at on every register artifact
    prov.stamp(payload)
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    report(payload)
    return 0


def report(payload: dict) -> None:
    print(f"base {payload['base']}  commits {len(payload['shas'])}"
          f"  budgets {payload['budgets_in_effect']}")
    for name, arm in payload["arms"].items():
        s = arm["summary"]
        print(f"\n-- {name} --")
        print(f"  flagged {s['flagged']}/{s['commits']} = {s['flag_rate']:.1%}"
              f"   dependency findings on {s['commits_with_a_dependency_finding']} commit(s)"
              f"   budget/plan only on {s['commits_flagged_only_by_budget_or_plan']}")
        print(f"  problem kinds: {s['problem_kinds'] or '{}'}")
        print(f"  seconds: median {s['seconds']['median']}  p95 {s['seconds']['p95']}"
              f"  max {s['seconds']['max']}  total {s['seconds']['total']}")
        for cls, stats in s["by_inferred_class"].items():
            print(f"  {cls}: {stats['commits']} commits  "
                  f"files p50/p95/max {stats['files']['p50']}/{stats['files']['p95']}/{stats['files']['max']}  "
                  f"dirs {stats['dirs']['p50']}/{stats['dirs']['p95']}/{stats['dirs']['max']}  "
                  f"lines {stats['lines']['p50']}/{stats['lines']['p95']}/{stats['lines']['max']}")
    print(f"\nproposed budgets (T3): {payload['proposed_budgets']}")


if __name__ == "__main__":
    raise SystemExit(main())
