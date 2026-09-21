#!/usr/bin/env python3
"""Update a hand-rolled flat install under ~/.claude/scripts, safely and reversibly.

`hosts.py` documents that flat copy as a supported way to run this, and nothing shipped a
way to UPDATE one. So the engine fixes in this repository reach such an install only by
someone copying files by hand -- which is how a live store spent a fortnight running
defects that were already fixed here.

Three properties, in order of importance:

* **it does nothing by default.** Without `--apply` it prints what would change and exits.
  A tool that writes to a live install on its first invocation is a tool nobody dares run;
* **it backs up before it writes**, into a timestamped directory beside the target, and
  prints the one command that undoes the whole operation;
* **it verifies after**, by importing the installed engine and running a recall against a
  throwaway store. A copy that lands broken is worse than a copy that never happened, and
  "the files are newer" is not evidence that anything works.

It refuses a target that does not look like an install of this project, and it never
touches the memory store itself -- only the engine files beside it.

    python tools/sync_install.py                 # what would change
    python tools/sync_install.py --apply         # back up, copy, verify
    python tools/sync_install.py --target PATH   # a different install
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "nevertwice"
DEFAULT_TARGET = Path.home() / ".claude" / "scripts"

#: Never copied: machine-local state and secrets live in the install, not in the repo,
#: and overwriting them is how a sync turns into a data loss.
NEVER_COPY = {".secrets.env", "twin_calibration.json", ".processed_sessions.json",
              ".processed_sessions.json.bak", "guards.json", "guards.json.bak",
              "anticipate_state.json", "__pycache__", ".pytest_cache", ".git"}

#: A target must contain these to be recognised as an install of this project.
FINGERPRINT = ("memory_hook.py", "guards.py")


def _digest(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    except OSError:
        return ""


def _candidates() -> list[Path]:
    return sorted(p for p in SOURCE.glob("*.py") if p.name not in NEVER_COPY)


def _plan(target: Path) -> list[tuple[str, Path, str, str]]:
    """(action, source_file, source_digest, target_digest) for every candidate."""
    rows = []
    for src in _candidates():
        dst = target / src.name
        if not dst.exists():
            rows.append(("new", src, _digest(src), ""))
        elif not filecmp.cmp(src, dst, shallow=False):
            rows.append(("update", src, _digest(src), _digest(dst)))
    return rows


def _verify(target: Path) -> tuple[bool, str]:
    """Import the INSTALLED engine and recall against a throwaway store.

    Run in a subprocess with a sandbox vault: the check must never touch the real store,
    and importing an engine into this process would leave its module state behind.

    The verdict is the MARKER on stdout, not the exit code, and the difference is the whole
    point of this function. The engine's entry point answers a half-copied install with
    `SystemExit(0)` - deliberately, because the hooks run it before every tool call and memory
    being unavailable must never cost the agent its Edit - so a directory missing one file exits
    0 with an explanation on stderr and nothing on stdout. Reading the exit code alone, this
    function reported PASS on an install that does not run at all. Measured on a copy of
    `72f7fb8` with `_engine_recall.py` removed: returncode 0, stdout empty, stderr naming the
    missing part; the old check said PASS.

    The hole is older than the split - the loader answered a missing `_engine.py` the same way -
    but the split raises the stakes from "one file has to arrive" to "ten files have to arrive",
    and this is the step where a wrong PASS costs the most.
    """
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, NEVERTWICE_VAULT=td, NEVERTWICE_EMBED_PROVIDER="none")
        code = (
            "import sys; sys.path.insert(0, r'%s')\n"
            "import memory_hook as m\n"
            "assert callable(m.retrieve_relevant)\n"
            "m.retrieve_relevant('p', 'a query', 3)\n"
            "print('OK', getattr(m, '__version__', 'no __version__'))\n" % target
        )
        try:
            r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                               text=True, timeout=180, env=env, cwd=td)
        except subprocess.TimeoutExpired:
            return False, "the installed engine did not answer a recall within 180 s"
    ok = r.returncode == 0 and r.stdout.startswith("OK")
    detail = (r.stdout or r.stderr).strip()[-500:]
    if r.returncode == 0 and not r.stdout.startswith("OK"):
        detail = ("the installed engine exited 0 without completing the recall - a half-copied "
                  "install degrades quietly by design: " + detail)
    return ok, detail


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--apply", action="store_true",
                    help="actually back up and copy; without it nothing is written")
    args = ap.parse_args(argv)
    target: Path = args.target

    if not target.exists():
        print(f"target does not exist: {target}")
        return 2
    missing = [f for f in FINGERPRINT if not (target / f).exists()]
    if missing:
        print(f"refusing: {target} is missing {', '.join(missing)} - "
              "that does not look like an install of this project")
        return 2

    rows = _plan(target)
    print(f"source {SOURCE}\ntarget {target}\n")
    if not rows:
        print("nothing to do: every engine file already matches.")
        return 0
    for action, src, sd, td_ in rows:
        print(f"  {action:6s} {src.name:34s} {td_ or '(absent)':>12s} -> {sd}")
    print(f"\n{len(rows)} file(s) would change.")

    if not args.apply:
        print("\nDRY RUN - nothing was written. Re-run with --apply to back up and copy.")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = target.parent / f"scripts-backup-{stamp}"
    backup.mkdir(parents=True, exist_ok=False)
    for _action, src, _sd, _td in rows:
        dst = target / src.name
        if dst.exists():
            shutil.copy2(dst, backup / src.name)
    print(f"\nbacked up {len(list(backup.iterdir()))} file(s) to {backup}")
    print(f"UNDO: copy every file from {backup} back over {target}")

    for _action, src, _sd, _td in rows:
        shutil.copy2(src, target / src.name)
    print(f"copied {len(rows)} file(s)")

    ok, detail = _verify(target)
    print(f"\nverify: {'PASS' if ok else 'FAIL'}  {detail}")
    if not ok:
        print(f"\nThe installed engine does not answer a recall. Restore from {backup} "
              "before using it.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
