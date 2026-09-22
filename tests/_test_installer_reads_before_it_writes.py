#!/usr/bin/env python3
"""The POSIX installer never replaces a crontab it could not read.

`crontab -` REPLACES the user's crontab; it does not append. So the merge in
`install._register_tasks_cron` is only safe if the read that precedes it actually succeeded, and
`crontab -l` fails with an empty stdout for two entirely different reasons:

* the user has no crontab yet - fine, the list really is empty;
* it could not be read - no Full Disk Access on macOS, a locked spool, a hardened container.

The first version read `current.stdout or ""` and never looked at the exit code, so both cases
produced an empty list and the installer wrote its three jobs over whatever was there. That is
data loss on a developer's own machine, on the first-run path the README documents, in the half
of `register_tasks` that `os.name == "nt"` makes unreachable on the only machine where this
project's suites have ever run. No suite touched this function; `git grep crontab tests/`
returned nothing.

This suite exercises the read through a fake `crontab` on PATH, so it tests the branch rather
than the wording of the source.

    python tests/_test_installer_reads_before_it_writes.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import install  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


USER_JOBS = "0 2 * * * /usr/local/bin/backup.sh\n@reboot /home/me/bin/tunnel\n"


def _with_crontab(read: _Result):
    """Run `_register_tasks_cron` with `crontab -l` answering `read`, and capture any write."""
    wrote: dict[str, str] = {}

    def fake_run(argv, *a, **kw):
        if argv[:2] == ["crontab", "-l"]:
            return read
        if argv[:2] == ["crontab", "-"]:
            wrote["input"] = kw.get("input", "")
            return _Result(0)
        raise AssertionError(f"unexpected command {argv}")

    with tempfile.TemporaryDirectory() as td, \
         mock.patch.dict(os.environ, {"NEVERTWICE_HOME": td, "NEVERTWICE_VAULT": td}), \
         mock.patch.object(install.shutil, "which", lambda _n: "/usr/bin/crontab"), \
         mock.patch.object(install, "DRY", False), \
         mock.patch.object(install.subprocess, "run", fake_run):
        install._register_tasks_cron()
    return wrote


print("# a crontab that cannot be read is never replaced")

# The case that loses data: `crontab -l` fails, says nothing on stdout, and the reason is not
# "there is no crontab". The user's jobs exist; the installer simply cannot see them.
wrote = _with_crontab(_Result(1, "", "crontab: you are not authorized to use cron"))
check("an unreadable crontab is not overwritten", "input" not in wrote,
      f"the installer wrote: {wrote.get('input', '')!r}")

wrote = _with_crontab(_Result(126, "", ""))
check("and neither is one whose failure says nothing at all", "input" not in wrote,
      f"the installer wrote: {wrote.get('input', '')!r}")


print("# but the two cases that ARE safe still work")

# No crontab yet: every implementation says so, and an empty list is the truth.
wrote = _with_crontab(_Result(1, "", "no crontab for me"))
check("a user with no crontab yet gets the three jobs", "input" in wrote)
check("and nothing else is in the file",
      wrote.get("input", "").count(install._CRON_MARK) == 3
      and "backup.sh" not in wrote.get("input", ""))

# An existing crontab: the user's lines survive, ours are added once.
wrote = _with_crontab(_Result(0, USER_JOBS))
written = wrote.get("input", "")
check("an existing crontab keeps every line the user had",
      "backup.sh" in written and "tunnel" in written, written)
check("and gains exactly the three tagged jobs",
      written.count(install._CRON_MARK) == 3, written)

# Re-running replaces our own lines rather than stacking them - the property the docstring claims.
already = USER_JOBS + "".join(
    f'0 */4 * * * python x {install._CRON_MARK}\n' for _ in range(3))
wrote = _with_crontab(_Result(0, already))
written = wrote.get("input", "")
check("a second run does not stack duplicates",
      written.count(install._CRON_MARK) == 3, written)
check("and still keeps the user's lines", "backup.sh" in written, written)


print("# the read itself distinguishes the three outcomes")

with mock.patch.object(install.subprocess, "run", lambda *a, **k: _Result(0, USER_JOBS)):
    lines, problem = install._existing_crontab()
check("a successful read returns the lines and no problem", lines and not problem, str(problem))

with mock.patch.object(install.subprocess, "run",
                       lambda *a, **k: _Result(1, "", "crontab: no crontab for me")):
    lines, problem = install._existing_crontab()
check("no crontab yet is an empty list and no problem", lines == [] and not problem, str(problem))

with mock.patch.object(install.subprocess, "run",
                       lambda *a, **k: _Result(1, "", "operation not permitted")):
    lines, problem = install._existing_crontab()
check("an error is reported as a problem, not as emptiness", lines == [] and bool(problem),
      str(problem))

print()
print(f"installer reads before it writes: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
