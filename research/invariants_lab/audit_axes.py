"""F3: audit the ratchet's three unaudited axes, then re-measure what they cost.

Gates, sample sizes and consequences are in [`AXES_F3.md`](AXES_F3.md), written before this
ran. This file executes them and decides nothing the document did not already decide.

Four parts:

* **F3-A** -- exact agreement for `nesting` (`PLR1702`) and `returns` (`PLR0911`), over this
  repository *and* a seeded 300-file sample of `corpus_dev`, which is the harder set;
* **F3-L1 / F3-L2** -- the directional check for `length` against `PLR0915`'s statement
  count, which measures a different number and can only ever support a directional claim;
* **F3-S** -- the flag rate in a 2x2: all four axes against the audited ones, every changed
  file against shipped code only.

    python research/invariants_lab/audit_axes.py --agree     # F3-A alone (fast)
    python research/invariants_lab/audit_axes.py             # everything
    python research/invariants_lab/audit_axes.py --print
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from pathlib import Path

from scipy import stats
from statsmodels.stats.proportion import proportion_confint

sys.path.insert(0, str(Path(__file__).resolve().parent))

import complexity as C  # noqa: E402
import ratchet as R  # noqa: E402
from corpora import dev_repos  # noqa: E402
from corpusio import BlobReader, progress  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CENSUS = Path(__file__).with_name("corpus_census.json")
ARTIFACT = Path(__file__).with_name("axes_f3.json")

SEED = 20260828
AGREE_CORPUS_FILES = 300     # AXES_F3.md section 3, F3-A
L1_MIN_FIRINGS = 85          # AXES_F3.md section 3, F3-L1 -- withdrawn below this
L1_NULL = 0.50
ALPHA = 0.05

#: AXES_F3.md section 3, F3-S. Written before the run so it cannot be tuned to a number.
NOT_SHIPPED = ("test", "tests", "testing", "docs", "doc", "examples", "example",
               "benchmarks", "bench", "scripts", "tools", ".github")


def wilson(k: int, n: int) -> tuple[float, float, float]:
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    lo, hi = proportion_confint(k, n, alpha=ALPHA, method="wilson")
    return (k / n, float(lo), float(hi))


def is_shipped(path: str) -> bool:
    """Is this path part of what the project ships, by the rule declared in AXES_F3.md?"""
    parts = path.replace("\\", "/").split("/")
    name = parts[-1]
    if name in ("setup.py", "conftest.py") or name.startswith("test_"):
        return False
    if name.endswith("_test.py"):
        return False
    return not any(p in NOT_SHIPPED for p in parts[:-1])


# ---------------------------------------------------------------------------
# F3-A -- exact agreement on nesting and returns
# ---------------------------------------------------------------------------


def _compare_one(source: str, path: Path) -> dict | None:
    """This project against `ruff`, on one file. None when `ruff` did not run.

    `returns` is compared on every callable. `nesting` is compared only on the ones
    `PLR1702` can express -- see `complexity.auditable_nesting`: the rule counts a block
    chain without resetting at a `def` and reports it at the chain's outermost block, so
    for a callable that contains or sits inside another callable its output is not that
    callable's own depth in either direction. The excluded count is reported, not hidden.
    """
    mine = C.scan_file(source)
    if not mine:
        return {"callables": 0, "nesting_comparable": 0,
                "nesting_disagree": [], "returns_disagree": []}
    nest = C.ruff_nesting(path)
    rets = C.ruff_returns(path)
    if nest is None or rets is None:
        return None
    comparable = C.auditable_nesting(source)
    nd, rd = [], []
    for q, m in mine.items():
        if q in comparable and nest.get(q, 0) != m.nesting:
            nd.append({"qualname": q, "mine": m.nesting, "ruff": nest.get(q, 0)})
        if rets.get(q, 0) != m.returns:
            rd.append({"qualname": q, "mine": m.returns, "ruff": rets.get(q, 0)})
    return {"callables": len(mine), "nesting_comparable": len(comparable),
            "nesting_disagree": nd, "returns_disagree": rd}


def agree_on_this_repository() -> dict:
    files = sorted((ROOT / "nevertwice").rglob("*.py"))
    return _agree_over([(p, p.read_text(encoding="utf-8", errors="replace"))
                        for p in files], "nevertwice")


def agree_on_the_corpus(tmp: Path) -> dict:
    """A seeded, evenly-spread sample of `corpus_dev` files at their pinned HEADs."""
    repos = dev_repos()
    if not repos:
        return {"set": "corpus_dev", "skipped": "polygon absent"}
    per_repo = max(1, AGREE_CORPUS_FILES // max(len(repos), 1))
    rng = random.Random(SEED)
    picked: list[tuple[Path, str]] = []
    for repo in repos:
        with BlobReader(repo) as blobs:
            listing = _repo_py_files(repo)
            if not listing:
                continue
            take = rng.sample(listing, min(per_repo, len(listing)))
            for i, rel in enumerate(sorted(take)):
                src = blobs.read("HEAD", rel)
                if src is None:
                    continue
                out = tmp / f"{repo.name}_{i}.py"
                out.write_text(src, encoding="utf-8")
                picked.append((out, src))
    return _agree_over(picked, "corpus_dev")


def _repo_py_files(repo: Path) -> list[str]:
    import subprocess
    proc = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"],
                          cwd=str(repo), capture_output=True, check=False)
    return [ln for ln in proc.stdout.decode("utf-8", "replace").splitlines()
            if ln.endswith(".py")]


def _agree_over(items: list[tuple[Path, str]], label: str) -> dict:
    files = callables = comparable = 0
    unavailable = 0
    nesting_bad: list[dict] = []
    returns_bad: list[dict] = []
    for i, (path, src) in enumerate(items):
        if i % 50 == 0:
            progress(f"  agreement {label}: {i}/{len(items)}")
        got = _compare_one(src, path)
        if got is None:
            unavailable += 1
            continue
        files += 1
        callables += got["callables"]
        comparable += got["nesting_comparable"]
        for row in got["nesting_disagree"]:
            nesting_bad.append({"file": str(path), **row})
        for row in got["returns_disagree"]:
            returns_bad.append({"file": str(path), **row})
    return {
        "set": label, "files": files, "callables": callables,
        "ruff_unavailable_on": unavailable,
        "nesting": {"comparable": comparable,
                    "excluded_as_inexpressible": callables - comparable,
                    "disagreements": len(nesting_bad), "examples": nesting_bad[:12]},
        "returns": {"disagreements": len(returns_bad), "examples": returns_bad[:12]},
        "exact": not nesting_bad and not returns_bad,
    }


# ---------------------------------------------------------------------------
# F3-L / F3-S -- the corpus pass
# ---------------------------------------------------------------------------


AUDITED_AXES = ("cyclomatic", "nesting", "returns")


def _regressed_axes(before_metrics: dict, after_metrics: dict) -> dict[str, list[str]]:
    """Per axis, the qualnames the ratchet says got worse. The stored-baseline rule."""
    out: dict[str, list[str]] = {a: [] for a in R.AXES}
    for q, now in after_metrics.items():
        was = before_metrics.get(q)
        if was is None:
            continue  # new code has no baseline to be worse than
        for axis in R.AXES:
            if getattr(now, axis) > getattr(was, axis) + R.SLACK.get(axis, 0):
                out[axis].append(q)
    return out


def corpus_pass(tmp: Path) -> dict:
    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    pool = [(r["repo"], e) for r in census["repos"]
            for e in (r.get("eligible", []) + r.get("silent", []))]
    pool.sort(key=lambda x: (x[0], x[1]["sha"]))
    progress(f"corpus pass over the whole pool: {len(pool)} commits")

    repos = {r.name: r for r in dev_repos()}
    readers: dict[str, BlobReader] = {}

    length_rows: list[dict] = []     # one per function-diff with a statement reading
    commits: list[dict] = []         # one per commit, for F3-S
    try:
        for i, (repo_name, entry) in enumerate(pool):
            if i % 100 == 0:
                progress(f"  corpus {i}/{len(pool)}")
            repo = repos.get(repo_name)
            if repo is None:
                continue
            blobs = readers.setdefault(repo_name, BlobReader(repo))
            paths = sorted({d["path"] for d in entry["deltas"]}
                           | {h["path"] for h in entry.get("caller_updates", [])})
            fired = {"all_any": False, "all_shipped": False,
                     "audited_any": False, "audited_shipped": False}
            for p in paths:
                if not p.endswith(".py"):
                    continue
                old = blobs.read(entry["parent"], p)
                new = blobs.read(entry["sha"], p)
                if old is None or new is None:
                    continue
                mo, mn = C.scan_file(old), C.scan_file(new)
                if not mo and not mn:
                    continue
                regressed = _regressed_axes(mo, mn)
                any_axis = any(regressed[a] for a in R.AXES)
                aud_axis = any(regressed[a] for a in AUDITED_AXES)
                shipped = is_shipped(p)
                fired["all_any"] |= any_axis
                fired["audited_any"] |= aud_axis
                if shipped:
                    fired["all_shipped"] |= any_axis
                    fired["audited_shipped"] |= aud_axis

                # F3-L: statement counts, only for files where the length axis has
                # something to say. Running ruff on every file of every commit would
                # cost hours and buy nothing -- the gate conditions on the firing.
                if not regressed["length"]:
                    continue
                fo = tmp / "old_stmt.py"
                fn = tmp / "new_stmt.py"
                fo.write_text(old, encoding="utf-8")
                fn.write_text(new, encoding="utf-8")
                so = C.ruff_statements(fo)
                sn = C.ruff_statements(fn)
                if so is None or sn is None:
                    continue
                for q in regressed["length"]:
                    length_rows.append({
                        "repo": repo_name, "sha": entry["sha"], "path": p,
                        "qualname": q, "fired": True,
                        "lines_before": mo[q].length, "lines_after": mn[q].length,
                        "stmts_before": so.get(q, 0), "stmts_after": sn.get(q, 0),
                        "stmts_rose": sn.get(q, 0) > so.get(q, 0),
                    })
            commits.append({"repo": repo_name, "sha": entry["sha"], **fired})
    finally:
        for r in readers.values():
            r.close()
    return {"length_rows": length_rows, "commits": commits, "pool": len(pool)}


L2_COMMITS = 400          # AXES_F3.md section 3, F3-L2: a seeded sample, see below
L2_MIN_PER_ARM = 152      # the largest n the power table asks for, at a 2x ratio


def discrimination_pass(tmp: Path) -> dict:
    """F3-L2: does the length axis fire more often where statements rose?

    L1 conditions on the ratchet firing and needs a statement count only there. L2
    conditions on `PLR0915`, so it needs one for **every** function-diff -- about 93,000
    of them across the pool, at two `ruff` invocations each. That is hours for a
    corroborating gate, so it runs on a seeded sample of commits instead, and the
    realised per-arm counts are reported against the power table's requirement rather
    than assumed sufficient.
    """
    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    pool = [(r["repo"], e) for r in census["repos"]
            for e in (r.get("eligible", []) + r.get("silent", []))]
    pool.sort(key=lambda x: (x[0], x[1]["sha"]))
    rng = random.Random(SEED)
    sample = rng.sample(pool, L2_COMMITS) if len(pool) > L2_COMMITS else pool
    progress(f"F3-L2 discrimination sample: {len(sample)} commits")

    repos = {r.name: r for r in dev_repos()}
    readers: dict[str, BlobReader] = {}
    rose_fired = rose_quiet = flat_fired = flat_quiet = 0
    try:
        for i, (repo_name, entry) in enumerate(sample):
            if i % 50 == 0:
                progress(f"  L2 {i}/{len(sample)}")
            repo = repos.get(repo_name)
            if repo is None:
                continue
            blobs = readers.setdefault(repo_name, BlobReader(repo))
            paths = sorted({d["path"] for d in entry["deltas"]}
                           | {h["path"] for h in entry.get("caller_updates", [])})
            for p_ in paths:
                if not p_.endswith(".py"):
                    continue
                old_src = blobs.read(entry["parent"], p_)
                new_src = blobs.read(entry["sha"], p_)
                if old_src is None or new_src is None:
                    continue
                mo, mn = C.scan_file(old_src), C.scan_file(new_src)
                shared = set(mo) & set(mn)
                if not shared:
                    continue
                fo = tmp / "l2_old.py"
                fn = tmp / "l2_new.py"
                fo.write_text(old_src, encoding="utf-8")
                fn.write_text(new_src, encoding="utf-8")
                so, sn = C.ruff_statements(fo), C.ruff_statements(fn)
                if so is None or sn is None:
                    continue
                for q in shared:
                    fired = mn[q].length > mo[q].length + R.SLACK.get("length", 0)
                    if sn.get(q, 0) > so.get(q, 0):
                        rose_fired += fired
                        rose_quiet += not fired
                    else:
                        flat_fired += fired
                        flat_quiet += not fired
    finally:
        for r in readers.values():
            r.close()

    n_rose, n_flat = rose_fired + rose_quiet, flat_fired + flat_quiet
    p_rose = rose_fired / n_rose if n_rose else float("nan")
    p_flat = flat_fired / n_flat if n_flat else float("nan")
    try:
        _odds, fisher_p = stats.fisher_exact(
            [[rose_fired, rose_quiet], [flat_fired, flat_quiet]], alternative="greater")
    except ValueError:
        fisher_p = float("nan")
    ratio = (p_rose / p_flat) if p_flat else float("inf")
    resolvable = min(n_rose, n_flat) >= L2_MIN_PER_ARM
    return {
        "commits_sampled": len(sample),
        "statements_rose": {"n": n_rose, "fired": rose_fired, "rate": p_rose},
        "statements_flat": {"n": n_flat, "fired": flat_fired, "rate": p_flat},
        "ratio": ratio,
        "fisher_p_one_sided": float(fisher_p),
        "required_per_arm": L2_MIN_PER_ARM,
        "resolvable": resolvable,
        "verdict": (
            "withdrawn -- an arm smaller than the declared sample size"
            if not resolvable
            else ("pass" if (ratio >= 2.0 and fisher_p < 0.01) else "fail")
        ),
    }


def _length_gates(rows: list[dict]) -> dict:
    fired = [r for r in rows if r["fired"]]
    n = len(fired)
    k = sum(1 for r in fired if r["stmts_rose"])
    point, lo, hi = wilson(k, n)
    resolvable = n >= L1_MIN_FIRINGS
    p = (float(stats.binomtest(k, n, L1_NULL, alternative="greater").pvalue)
         if n else float("nan"))
    return {
        "n_firings": n,
        "required_for_80pct_power": L1_MIN_FIRINGS,
        "resolvable": resolvable,
        "also_rose_by_statements": k,
        "point": point, "ci": [lo, hi],
        "p_vs_0.50_one_sided": p,
        "verdict": (
            "withdrawn -- fewer firings than the declared sample size, and "
            "AXES_F3.md section 3 says a gate the corpus cannot resolve is withdrawn "
            "before it is scored"
            if not resolvable else ("pass" if (point > L1_NULL and p < ALPHA) else "fail")
        ),
    }


def _flag_rates(commits: list[dict]) -> dict:
    n = len(commits)
    out = {}
    for key, label in (("all_any", "all four axes, every changed file"),
                       ("all_shipped", "all four axes, shipped code only"),
                       ("audited_any", "audited axes, every changed file"),
                       ("audited_shipped", "audited axes, shipped code only")):
        k = sum(1 for c in commits if c[key])
        point, lo, hi = wilson(k, n)
        out[key] = {"label": label, "flagged": k, "n": n,
                    "rate": point, "ci": [lo, hi]}
    return out


def summarise(agree_repo: dict, agree_corpus: dict, raw: dict,
              discrimination: dict) -> dict:
    length = _length_gates(raw["length_rows"])
    rates = _flag_rates(raw["commits"])
    both_exact = agree_repo.get("exact") and agree_corpus.get("exact", True)
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": "F3",
        "corpus": "corpus_dev",
        "in_sample": True,
        "F3_A_agreement": {"nevertwice": agree_repo, "corpus_dev": agree_corpus,
                           "verdict": "pass" if both_exact else "fail"},
        "F3_L1_length_direction": length,
        "F3_L2_length_discrimination": discrimination,
        "F3_S_flag_rates": rates,
        "surviving_axes": (
            list(R.AXES) if (both_exact and length["verdict"] == "pass")
            else ([a for a in AUDITED_AXES] if both_exact else [])
        ),
        "note": (
            "length is checked against PLR0915, which counts STATEMENTS and not lines. "
            "It supports a directional claim and never an equality one. See AXES_F3.md "
            "section 2."
        ),
    }


def _print(data: dict) -> None:
    a = data["F3_A_agreement"]
    print("F3-A  exact agreement, nesting and returns")
    for key in ("nevertwice", "corpus_dev"):
        blk = a[key]
        if blk.get("skipped"):
            print(f"  {key:12s} SKIPPED -- {blk['skipped']}")
            continue
        print(f"  {key:12s} {blk['files']:4d} files, {blk['callables']:6d} callables, "
              f"nesting {blk['nesting']['disagreements']} disagreements, "
              f"returns {blk['returns']['disagreements']}")
    print(f"  verdict: {a['verdict'].upper()}")
    print()
    lg = data["F3_L1_length_direction"]
    print("F3-L1 length against PLR0915 statement count (DIRECTIONAL, not agreement)")
    print(f"  firings {lg['n_firings']} (needed {lg['required_for_80pct_power']}), "
          f"of which statements also rose: {lg['also_rose_by_statements']}")
    if lg["n_firings"]:
        print(f"  rate {lg['point']:.3f} [{lg['ci'][0]:.2f}, {lg['ci'][1]:.2f}]  "
              f"p vs 0.50 = {lg['p_vs_0.50_one_sided']:.3g}")
    print(f"  verdict: {lg['verdict'].upper()}")
    print()
    l2 = data.get("F3_L2_length_discrimination") or {}
    if l2:
        print("F3-L2 length discrimination against PLR0915 (sampled)")
        print(f"  statements rose: {l2['statements_rose']['fired']}/"
              f"{l2['statements_rose']['n']} fired ({l2['statements_rose']['rate']:.4f})")
        print(f"  statements flat: {l2['statements_flat']['fired']}/"
              f"{l2['statements_flat']['n']} fired ({l2['statements_flat']['rate']:.4f})")
        print(f"  ratio {l2['ratio']:.2f}x  Fisher p = {l2['fisher_p_one_sided']:.3g}  "
              f"verdict: {l2['verdict'].upper()}")
        print()
    print("F3-S  flag rate, re-measured")
    for row in data["F3_S_flag_rates"].values():
        print(f"  {row['label']:38s} {row['rate']:.3f} "
              f"[{row['ci'][0]:.2f}, {row['ci'][1]:.2f}]  {row['flagged']}/{row['n']}")
    print()
    print("surviving axes: " + (", ".join(data["surviving_axes"]) or "(none)"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", action="store_true", dest="show")
    ap.add_argument("--agree", action="store_true",
                    help="run F3-A only and print it, without writing the artifact")
    ap.add_argument("--l2", action="store_true",
                    help="run F3-L2 alone and merge it into the committed artifact, "
                         "so the 40-minute corpus pass is not repeated for a "
                         "corroborating gate")
    args = ap.parse_args()
    if args.show:
        _print(json.loads(ARTIFACT.read_text(encoding="utf-8")))
        return 0
    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="axes_f3_"))
    if args.l2:
        data = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        data["F3_L2_length_discrimination"] = discrimination_pass(tmp)
        data["seconds_l2"] = round(time.time() - t0, 1)
        ARTIFACT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        _print(data)
        return 0
    agree_repo = agree_on_this_repository()
    agree_corpus = agree_on_the_corpus(tmp)
    if args.agree:
        print(json.dumps({"nevertwice": agree_repo, "corpus_dev": agree_corpus},
                         indent=1)[:6000])
        return 0
    raw = corpus_pass(tmp)
    data = summarise(agree_repo, agree_corpus, raw, discrimination_pass(tmp))
    data["seconds"] = round(time.time() - t0, 1)
    ARTIFACT.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    _print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
