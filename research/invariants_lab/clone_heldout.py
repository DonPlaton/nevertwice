"""H3: clone the repositories `HELDOUT_H2.md` selected, and nothing it did not.

Written **after** the freeze and deliberately outside it: this file creates the corpus, it
does not measure anything, so its hash is not among the 29 in `heldout_seal.json`.

The manifest is the one committed in H2, before any clone ran. Nothing here chooses a
repository; it reads the list, checks each against the criteria that can be checked from a
clone, and records what it found. A repository that fails a criterion is **recorded as
rejected with the reason** rather than quietly dropped -- a corpus whose exclusions are
invisible is a corpus somebody selected.

    python research/invariants_lab/clone_heldout.py --plan     # what it would do
    python research/invariants_lab/clone_heldout.py            # do it
    python research/invariants_lab/clone_heldout.py --print
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import corpora  # noqa: E402
from corpusio import progress  # noqa: E402

MANIFEST = Path(__file__).with_name("heldout_manifest.json")
ARTIFACT = Path(__file__).with_name("heldout_clone_log.json")

#: HELDOUT_H2.md section 2. Checked from the clone, not taken on trust.
MIN_CONTRIBUTORS = 100
MIN_PY_COMMITS = 1_000
BUDGET_GB = 100.0

#: HELDOUT_H2.md section 4, verbatim. Thirty candidates, six domains.
CANDIDATES: tuple[tuple[str, str], ...] = (
    ("scientific", "numpy/numpy"),
    ("scientific", "scipy/scipy"),
    ("scientific", "sympy/sympy"),
    ("scientific", "astropy/astropy"),
    ("scientific", "networkx/networkx"),
    ("data", "pandas-dev/pandas"),
    ("data", "sqlalchemy/sqlalchemy"),
    ("data", "apache/airflow"),
    ("data", "dask/dask"),
    ("data", "pola-rs/polars"),
    ("infrastructure", "ansible/ansible"),
    ("infrastructure", "pallets/click"),
    ("infrastructure", "psf/black"),
    ("infrastructure", "pypa/virtualenv"),
    ("infrastructure", "saltstack/salt"),
    ("async", "aio-libs/aiohttp"),
    ("async", "python-trio/trio"),
    ("async", "encode/starlette"),
    ("async", "paramiko/paramiko"),
    ("async", "celery/celery"),
    ("ml", "scikit-learn/scikit-learn"),
    ("ml", "huggingface/transformers"),
    ("ml", "pytorch/vision"),
    ("ml", "keras-team/keras"),
    ("ml", "optuna/optuna"),
    ("packaging", "pypa/pip"),
    ("packaging", "pypa/setuptools"),
    ("packaging", "python/mypy"),
    ("packaging", "pytest-dev/tox"),
    ("packaging", "PyCQA/pylint"),
)


def _git(repo: Path | None, args: list[str], timeout: int = 3600) -> tuple[int, str]:
    proc = subprocess.run(["git", *args], cwd=str(repo) if repo else None,
                          capture_output=True, timeout=timeout, check=False)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def dev_slug_guard() -> set[str]:
    """The eight. A held-out corpus containing one of them is not held out."""
    return corpora.dev_slugs()


def inspect(repo: Path) -> dict:
    """Everything H2's criteria need, read from the clone rather than taken on trust."""
    _rc, head = _git(repo, ["rev-parse", "HEAD"])
    _rc, py = _git(repo, ["rev-list", "--count", "HEAD", "--", "*.py"])
    _rc, total = _git(repo, ["rev-list", "--count", "HEAD"])
    _rc, authors = _git(repo, ["shortlog", "-sne", "HEAD"])
    _rc, last = _git(repo, ["log", "-1", "--format=%aI"])
    return {
        "head": head.strip(),
        "total_commits": int(total.strip() or 0),
        "py_commits": int(py.strip() or 0),
        "contributors": len([ln for ln in authors.splitlines() if ln.strip()]),
        "last_commit": last.strip(),
        "disk_mb": round(corpora.disk_gb(repo) * 1024, 1),
    }


def verdict(info: dict) -> str | None:
    """None when the repository is kept; otherwise the criterion it failed."""
    if info["contributors"] < MIN_CONTRIBUTORS:
        return f"only {info['contributors']} contributors, needs {MIN_CONTRIBUTORS}"
    if info["py_commits"] < MIN_PY_COMMITS:
        return f"only {info['py_commits']} commits touch .py, needs {MIN_PY_COMMITS}"
    return None


def run(plan_only: bool) -> dict:
    root = corpora.HELDOUT_ROOT
    dev = dev_slug_guard()
    overlap = [s for _d, s in CANDIDATES if s in dev]
    if overlap:
        raise SystemExit(
            "a candidate is in the development set: " + ", ".join(overlap)
            + ". A held-out corpus containing one of the eight is not held out."
        )
    if plan_only:
        return {"plan": True, "root": str(root), "candidates": len(CANDIDATES),
                "domains": sorted({d for d, _s in CANDIDATES}),
                "slugs": [s for _d, s in CANDIDATES]}

    root.mkdir(parents=True, exist_ok=True)
    kept: list[dict] = []
    rejected: list[dict] = []
    for i, (domain, slug) in enumerate(CANDIDATES, 1):
        name = slug.replace("/", "_")
        dest = root / name
        progress(f"  {i}/{len(CANDIDATES)}  {slug}")
        if corpora.disk_gb(root) > BUDGET_GB:
            rejected.append({"slug": slug, "domain": domain,
                             "why": f"budget: {BUDGET_GB} GB reached before this clone"})
            continue
        if not (dest / ".git").exists():
            rc, _out = _git(None, ["clone", "--quiet",
                                   f"https://github.com/{slug}.git", str(dest)])
            if rc != 0 or not (dest / ".git").exists():
                rejected.append({"slug": slug, "domain": domain,
                                 "why": "clone failed (network or repository gone)"})
                continue
        info = inspect(dest)
        why = verdict(info)
        row = {"slug": slug, "domain": domain, "dir": name, **info}
        if why:
            rejected.append({**row, "why": why})
        else:
            kept.append(row)

    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": "H3", "corpus": "heldout",
        "root": str(root),
        "criteria": {"min_contributors": MIN_CONTRIBUTORS,
                     "min_py_commits": MIN_PY_COMMITS,
                     "budget_gb": BUDGET_GB},
        "kept": kept, "rejected": rejected,
        "n_kept": len(kept), "n_rejected": len(rejected),
        "domains_kept": sorted({r["domain"] for r in kept}),
        "disk_gb": corpora.disk_gb(root),
        "meets_h2_targets": len(kept) >= 25 and len({r["domain"] for r in kept}) >= 6,
    }
    MANIFEST.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    ARTIFACT.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return payload


def _print(data: dict) -> None:
    if data.get("plan"):
        print(f"would clone {data['candidates']} repositories into {data['root']}")
        print("domains: " + ", ".join(data["domains"]))
        for slug in data["slugs"]:
            print("  " + slug)
        return
    print(f"H3 clone log -- {data['n_kept']} kept, {data['n_rejected']} rejected, "
          f"{data['disk_gb']} GB of {data['criteria']['budget_gb']}")
    print("domains kept: " + ", ".join(data["domains_kept"]))
    for r in data["kept"]:
        print(f"  keep   {r['slug']:32s} {r['contributors']:5d} contributors  "
              f"{r['py_commits']:6d} py commits  {r['disk_mb']:8.1f} MB")
    for r in data["rejected"]:
        print(f"  REJECT {r['slug']:32s} {r['why']}")
    print()
    print("meets H2's >= 25 repositories across >= 6 domains: "
          + ("yes" if data["meets_h2_targets"] else "NO"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", action="store_true",
                    help="print what would be cloned and stop")
    ap.add_argument("--print", dest="show", action="store_true")
    args = ap.parse_args(argv)
    if args.show:
        _print(json.loads(ARTIFACT.read_text(encoding="utf-8")))
        return 0
    _print(run(args.plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
