#!/usr/bin/env python3
"""The per-commit chains (the owner's P1, 2026-09-27): .github/chains_plan.py on real temporary git repositories, and
.github/workflows/chains.yml read as text.

* CP-range: the push's commits in order, merges excluded;
* CP-new-branch, CP-unknown: a push that creates the branch, and a before that is no commit here, are refused by name;
* CP-backlog: the backlog's SHAs join only when the push changes the backlog file - deduplicated against the range; a
  line that is not a full SHA, or a commit that is not an ancestor of the head, is refused;
* CP-cap: more commits than the cap is refused by name;
* CP-empty: a push without a new commit plans 0 jobs and says so - main() writes count=0 and a warning;
* CP-matrix: every commit x {windows-latest 3.14, ubuntu-latest 3.12};
* CW: the workflow runs on push to invariants/v3 only, reads no secret, chains every job with fail-fast off, checks
  that HEAD is the chained commit, runs the commit's own `python -m pytest -q` with pipefail and keeps its log as
  chain-<sha7>-<os>.

    python tests/_test_chains_plan.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import _env_guard  # noqa: F401,E402  hermetic like every suite


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CP = _load("chains_plan_t", ROOT / ".github" / "chains_plan.py")
PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def refused(fn) -> str:
    try:
        fn()
        return "accepted"
    except CP.PlanRefused as e:
        return str(e)
    except Exception as e:  # noqa: BLE001 - not the plan's named refusal: the row FAILs by name
        return f"not refused by the plan: {type(e).__name__}: {e}"


def safe_plan(*args) -> list | str:
    """The plan, or what it raised - a refusal or a crash is the row's FAIL, by name."""
    try:
        return CP.plan(*args)
    except Exception as e:  # noqa: BLE001
        return f"raised {type(e).__name__}: {e}"


GENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args], check=True, env=GENV,
                          capture_output=True, text=True).stdout.strip()


def commit(repo: Path, name: str, text: str = "x") -> str:
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


TMP = Path(tempfile.mkdtemp(prefix="nvt_chains_"))
try:
    R = TMP / "repo"
    R.mkdir()
    git(R, "init", "-q")
    git(R, "checkout", "-q", "-b", "main")
    a = commit(R, "a.txt")
    b = commit(R, "b.txt")
    git(R, "checkout", "-q", "-b", "side")
    s1 = commit(R, "s1.txt")
    git(R, "checkout", "-q", "main")
    c = commit(R, "c.txt")
    git(R, "merge", "-q", "--no-ff", "side", "-m", "merge side")
    m = git(R, "rev-parse", "HEAD")

    print("- the range of a push -")
    got = safe_plan(R, a, m)
    check("CP-range: the push's commits in order, the merge excluded, the merged branch's commit included",
          got[0] == b and set(got) == {b, c, s1} and m not in got and len(got) == 3, str(got))
    rn = refused(lambda: CP.plan(R, CP.ZERO, m))
    ru = refused(lambda: CP.plan(R, "f" * 40, m))
    check("CP-new-branch, CP-unknown: a push that creates the branch, and a before that is no commit here, are refused",
          "creates the branch" in rn and "not a commit" in ru, f"{rn} | {ru}")
    check("CP-empty: a push that brings no commit plans none", CP.plan(R, m, m) == [])

    print("\n- the one-off backlog -")
    (R / ".github").mkdir()
    d = commit(R, ".github/chains-backlog.txt", f"# control\n{a}  # green\n{b}\n")
    e = commit(R, "e.txt")
    with_backlog = safe_plan(R, m, e)
    check("CP-backlog: a push that changes the backlog chains its commits too, after its own, deduplicated",
          with_backlog == [d, e, a, b], str(with_backlog))
    without = safe_plan(R, d, e)
    check("CP-backlog: a push that does not change the backlog does not chain it", without == [e], str(without))
    f = commit(R, ".github/chains-backlog.txt", f"{e}\n{e}\n{a}\n")
    check("CP-backlog: a SHA already in the push's range, or listed twice, is chained once",
          safe_plan(R, e, f) == [f, e, a], str(safe_plan(R, e, f)))
    g_bad = commit(R, ".github/chains-backlog.txt", "abc123\n")
    rb = refused(lambda: CP.plan(R, f, g_bad))
    git(R, "checkout", "-q", "-b", "other", a)
    stray = commit(R, "stray.txt")
    git(R, "checkout", "-q", "main")
    g_stray = commit(R, ".github/chains-backlog.txt", f"{stray}\n")
    rs = refused(lambda: CP.plan(R, g_bad, g_stray))
    check("CP-backlog: a line that is not a full SHA, and a commit that is not an ancestor of the head, are refused",
          "not a full 40-hex SHA" in rb and "not an ancestor" in rs, f"{rb} | {rs}")

    print("\n- the cap, the matrix, main() -")
    saved = CP.MAX_SHAS
    CP.MAX_SHAS = 2
    try:
        rc = refused(lambda: CP.plan(R, a, m))
    finally:
        CP.MAX_SHAS = saved
    check("CP-cap: more commits than the cap is refused by name", "exceed the cap" in rc, rc)
    mx = CP.matrix([b, c])
    check("CP-matrix: every commit x {windows-latest 3.14, ubuntu-latest 3.12}",
          mx["include"] == [{"sha": b, "sha7": b[:7], "os": "windows-latest", "python": "3.14"},
                            {"sha": b, "sha7": b[:7], "os": "ubuntu-latest", "python": "3.12"},
                            {"sha": c, "sha7": c[:7], "os": "windows-latest", "python": "3.14"},
                            {"sha": c, "sha7": c[:7], "os": "ubuntu-latest", "python": "3.12"}], str(mx))

    def run_main(before: str, after: str) -> tuple[int, str, str]:
        out_file = TMP / "gh_output.txt"
        out_file.write_text("", encoding="utf-8")
        saved_env = {k: os.environ.get(k) for k in ("BEFORE", "AFTER", "GITHUB_OUTPUT")}
        saved_cwd = Path.cwd()
        os.environ.update(BEFORE=before, AFTER=after, GITHUB_OUTPUT=str(out_file))
        os.chdir(R)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc_ = CP.main()
        finally:
            os.chdir(saved_cwd)
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return rc_, buf.getvalue(), out_file.read_text(encoding="utf-8")

    rc0, printed0, out0 = run_main(m, m)
    check("CP-empty: main() on a push with no new commit - exit 0, count=0, and a warning that says so",
          rc0 == 0 and "count=0" in out0 and "::warning::" in printed0, f"{rc0} {printed0!r} {out0!r}")
    rc1, printed1, out1 = run_main(a, m)
    mline = next((ln for ln in out1.splitlines() if ln.startswith("matrix=")), "matrix={}")
    check("main() writes the matrix and the count to $GITHUB_OUTPUT",
          rc1 == 0 and "count=3" in out1 and len(json.loads(mline[len("matrix="):])["include"]) == 6, out1)
    rc2, printed2, _o = run_main(CP.ZERO, m)
    check("main() fails a refused plan by name - never green", rc2 == 1 and "::error::chains plan refused" in printed2,
          printed2)

    print("\n- the workflow, as text -")
    W = (ROOT / ".github" / "workflows" / "chains.yml").read_text(encoding="utf-8")
    on_block = W.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    check("CW: a push to invariants/v3 is its only trigger (no dispatch, no pull request, no schedule)",
          on_block.strip() == "push:\n    branches: [invariants/v3]", repr(on_block))
    check("CW: no secret is read and the token only reads", "secrets." not in W and "contents: read" in W
          and "write" not in W.split("permissions:", 1)[1].split("jobs:", 1)[0])
    chain = W.split("\n  chain:\n", 1)[1]
    check("CW: every chain job runs, fail-fast off, one per commit and platform, named by sha7 and os",
          "fail-fast: false" in chain and "matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}" in chain
          and "name: chain ${{ matrix.sha7 }} ${{ matrix.os }}" in chain)
    check("CW: the checkout is the chained commit with its history, and HEAD is checked against it",
          "ref: ${{ matrix.sha }}" in chain and "fetch-depth: 0" in chain
          and re.search(r'got=\$\(git rev-parse HEAD\)\n\s+if \[ "\$got" != "\$SHA" \]; then', chain) is not None)
    check("CW: the commit's own battery with pipefail, its log kept as chain-<sha7>-<os>",
          "set -o pipefail" in chain and "python -m pytest -q 2>&1 | tee chain.log" in chain
          and "name: chain-${{ matrix.sha7 }}-${{ matrix.os }}" in chain and "if: always()" in chain)
    check("CW: the research extra when the commit declares one, else .[dev] with a notice",
          'extras="dev,research"' in chain and 'extras="dev"' in chain and "::notice::" in chain)
    check("CW: 0 planned commits run no chain job", "if: needs.plan.outputs.count != '0'" in chain)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nchains plan: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
