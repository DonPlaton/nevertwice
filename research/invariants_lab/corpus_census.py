"""C1 exit criterion: does each cloned repository actually contain the raw material?

A repository that never breaks a caller adds nothing that 150 commits of this project
did not already supply. So before any mechanism is measured, each block is counted:

* commits in the census window,
* how many changed a callable's parameter list or removed a symbol,
* and -- the number that decides whether the repository stays -- how many did that
  **and updated a caller in a different file in the same commit**.

That last class is the seed of the positive class. C2 reverts the caller half of such
a commit and gets a breakage whose answer key was written by the repository's own
authors, not by a checker.

    python research/invariants_lab/corpus_census.py            # run and write the artifact
    python research/invariants_lab/corpus_census.py --print     # summarise the committed one
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpusio import (  # noqa: E402
    BlobReader,
    CommitFiles,
    corpus_repos,
    head_sha,
    log_commits,
    progress,
    total_commits,
)
import sigscan  # noqa: E402
from sigscan import scan_refs, signature_deltas  # noqa: E402

ARTIFACT = Path(__file__).with_name("corpus_census.json")

# Declared before the run, uniform across blocks, so no repository's window is
# chosen for what it contains.
WINDOW = 10 ** 9        # the entire history; see POWER.md on why enlargement is not tuning
MAX_PY_FILES = 40      # a commit touching more is a bulk rewrite, not a contract change
MIN_PY_FILES = 2       # a same-commit caller update needs at least two files
SILENCE_KEEP = 250     # per repository; 2000 total clears the 308 gate D5-P4 needs
SILENCE_SEED = 20260827


def _skip(path: str) -> bool:
    """Vendored and generated trees, which are neither authored nor callers."""
    low = path.replace("\\", "/").lower()
    return any(
        seg in low
        for seg in (
            "/vendor/", "vendor/", "/_vendor/", "third_party/",
            "/migrations/", "site-packages/", "/node_modules/",
        )
    )


def census_repo(repo: Path, window: int = WINDOW) -> dict:
    started = time.time()
    commits = log_commits(repo, limit=None)
    in_window = commits[:window]

    candidates = [
        c for c in in_window
        if MIN_PY_FILES <= len([p for p in c.py_paths if not _skip(p)]) <= MAX_PY_FILES
    ]

    n_sig = 0
    n_eligible = 0
    eligible: list[dict] = []
    silent: list[dict] = []          # contract changed, no in-repo caller updated
    rng = random.Random(SILENCE_SEED)
    sigscan.PARSE_FAILURES.clear()
    sigscan.PARSE_ATTEMPTS[0] = 0

    with BlobReader(repo) as blobs:
        for i, c in enumerate(candidates):
            if i % 250 == 0:
                progress(f"  {repo.name}: {i}/{len(candidates)} candidates")
            paths = [p for p in c.py_paths if not _skip(p)]
            sources: dict[str, tuple[str | None, str | None]] = {}
            for p in paths:
                sources[p] = (blobs.read(c.parent, p), blobs.read(c.sha, p))

            deltas = []
            for p, (old, new) in sources.items():
                if old is None or new is None:
                    continue  # added or deleted file: no parameter list to compare
                deltas.extend(signature_deltas(old, new, p))
            if not deltas:
                continue
            n_sig += 1

            watched = {d.qualname.rsplit(".", 1)[-1] for d in deltas}
            defining = {d.path for d in deltas}
            hits: list[dict] = []
            for p, (old, new) in sources.items():
                if p in defining or old is None or new is None:
                    continue
                before = scan_refs(old, watched)
                after = scan_refs(new, watched)
                if not before or not after:
                    continue
                # The caller must have been *edited*, not merely present: compare the
                # call shapes, so a file that happens to mention the name but was
                # changed elsewhere does not count as a caller update.
                shape_before = sorted(
                    (s.name, s.n_pos, tuple(sorted(s.kwnames))) for s in before if s.is_call
                )
                shape_after = sorted(
                    (s.name, s.n_pos, tuple(sorted(s.kwnames))) for s in after if s.is_call
                )
                if shape_before == shape_after:
                    continue
                touched = {n for n, _, _ in shape_before} ^ {n for n, _, _ in shape_after}
                touched |= {
                    n for n in {s.name for s in before if s.is_call}
                    if [x for x in shape_before if x[0] == n]
                    != [x for x in shape_after if x[0] == n]
                }
                hits.append({"path": p, "symbols": sorted(touched)})

            if not hits:
                # The silence pool, gate D5-P4: a contract changed and nothing in
                # the repository called it. Reservoir-sampled so the pool is a fair
                # draw from the whole history rather than its most recent slice.
                record = {
                    "sha": c.sha, "parent": c.parent, "date": c.author_date,
                    "subject": c.subject, "n_py": len(paths),
                    "deltas": [
                        {"qualname": d.qualname, "path": d.path, "kind": d.kind,
                         "before": d.before, "after": d.after}
                        for d in deltas
                    ],
                    "caller_updates": [],
                }
                if len(silent) < SILENCE_KEEP:
                    silent.append(record)
                else:
                    j = rng.randrange(n_sig)
                    if j < SILENCE_KEEP:
                        silent[j] = record
                continue

            if hits:
                n_eligible += 1
                eligible.append(
                    {
                        "sha": c.sha,
                        "parent": c.parent,
                        "date": c.author_date,
                        "subject": c.subject,
                        "n_py": len(paths),
                        "deltas": [
                            {
                                "qualname": d.qualname,
                                "path": d.path,
                                "kind": d.kind,
                                "before": d.before,
                                "after": d.after,
                            }
                            for d in deltas
                        ],
                        "caller_updates": hits,
                    }
                )

    return {
        "repo": repo.name,
        "head": head_sha(repo),
        "total_commits": total_commits(repo),
        "py_commits": len(commits),
        "window": window,
        "in_window": len(in_window),
        "candidates": len(candidates),
        "signature_change_commits": n_sig,
        "eligible_commits": n_eligible,
        "eligible_rate": round(n_eligible / len(candidates), 5) if candidates else 0.0,
        "parse_failures": dict(sigscan.PARSE_FAILURES),
        "parse_attempts": sigscan.PARSE_ATTEMPTS[0],
        "seconds": round(time.time() - started, 1),
        "eligible": eligible,
        "silent_kept": len(silent),
        "silent": silent,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="show", action="store_true")
    ap.add_argument("--only", default=None, help="census one repository by directory name")
    ap.add_argument("--window", type=int, default=WINDOW)
    args = ap.parse_args(argv)

    if args.show:
        if not ARTIFACT.exists():
            progress("no artifact yet")
            return 1
        data = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        head = f"{'repo':<22}{'commits':>9}{'window':>8}{'cand':>7}{'sig':>7}{'elig':>7}{'rate':>8}"
        print(head)
        print("-" * len(head))
        for r in data["repos"]:
            print(
                f"{r['repo']:<22}{r['total_commits']:>9}{r['in_window']:>8}"
                f"{r['candidates']:>7}{r['signature_change_commits']:>7}"
                f"{r['eligible_commits']:>7}{r['eligible_rate']:>8.3f}"
            )
        print("-" * len(head))
        print(f"eligible commits, total: {data['totals']['eligible_commits']}")
        return 0

    repos = corpus_repos()
    if args.only:
        repos = [r for r in repos if r.name == args.only]
    if not repos:
        progress("no cloned repositories found")
        return 1

    out = []
    for repo in repos:
        progress(f"== {repo.name}")
        out.append(census_repo(repo, window=args.window))
        progress(f"   eligible={out[-1]['eligible_commits']} in {out[-1]['seconds']}s")

    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "window": args.window,
        "max_py_files": MAX_PY_FILES,
        "repos": out,
        "totals": {
            "silent_kept": sum(r["silent_kept"] for r in out),
            "candidates": sum(r["candidates"] for r in out),
            "signature_change_commits": sum(r["signature_change_commits"] for r in out),
            "eligible_commits": sum(r["eligible_commits"] for r in out),
        },
    }
    ARTIFACT.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    progress(f"wrote {ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
