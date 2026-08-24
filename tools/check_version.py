#!/usr/bin/env python3
"""The release version contract: one version string, five places, no drift.

`config.VERSION` is the single source. A test already asserts that `pyproject.toml`
and `mcp_server.SERVER_VERSION` agree with it. This script adds the two places a
suite cannot see - the **git tag** and the **built distribution's metadata** - and
runs them in CI, so a release cannot be cut with a tag that disagrees with the code
or a wheel whose metadata disagrees with either.

Usage:

    python tools/check_version.py               # repo checks (pyproject / runtime / tag)
    python tools/check_version.py --dist dist   # also check built sdist + wheel metadata
    python tools/check_version.py --release     # tag-time strictness: HEAD must be tagged

Exit code 0 when the contract holds, 1 otherwise. Errors are printed one per line;
notes are advisory and never fail the run.

Everything is standard library, and the runtime probe imports the package in a
scrubbed child process - this script never touches the caller's memory store.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_NAME = "nevertwice"

# The subset of PEP 440 this project releases: X.Y.Z, optionally a pre/post/dev
# suffix. Anything else (a local version, an epoch, a v-prefix) is a mistake here.
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?$")


# -- inputs (pure parsing, so the tests can drive them directly) --------------

def read_pyproject_version(text: str) -> str:
    """The `version = "..."` line from `[project]`. Raises if it is absent."""
    m = re.search(r'^version = "([^"]+)"', text, re.M)
    if m is None:
        raise ValueError("pyproject.toml has no top-level version assignment")
    return m.group(1)


def read_metadata(path: Path) -> tuple[str, str]:
    """(artifact name, RFC-822 metadata text) for one built sdist or wheel."""
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
            return path.name, z.read(name).decode("utf-8", "replace")
    with tarfile.open(path) as t:
        member = next(m for m in t.getmembers()
                      if m.name.count("/") == 1 and m.name.endswith("/PKG-INFO"))
        fh = t.extractfile(member)
        if fh is None:
            raise ValueError(f"{path.name}: PKG-INFO is not a regular file")
        return path.name, fh.read().decode("utf-8", "replace")


def runtime_versions() -> tuple[str, str]:
    """`nevertwice.__version__` and `mcp_server.SERVER_VERSION`, read from a child
    process with a scrubbed environment.

    Importing the package resolves store paths from the environment and loads the
    caller's env file. Doing that in-process would make this check depend on the
    developer's machine; pointing the store at a throwaway directory keeps it
    hermetic and keeps a live store untouched.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("NEVERTWICE_", "ANAMNESIS_", "CLAUDE_MEMORY_"))}
    with tempfile.TemporaryDirectory(prefix="nevertwice_versioncheck_") as tmp:
        env["NEVERTWICE_HOME"] = tmp
        env["NEVERTWICE_VAULT"] = tmp
        env["NEVERTWICE_ENV_FILE"] = str(Path(tmp) / "absent.env")
        # mcp_server rebinds sys.stdout to stderr at import (it speaks stdio JSON-RPC),
        # so keep the real stream and print through it afterwards.
        code = (
            "import sys, nevertwice;"
            "v = nevertwice.__version__;"
            "_out = sys.stdout;"
            "import nevertwice.mcp_server as m;"
            "sys.stdout = _out;"
            "print(v);print(m.SERVER_VERSION)"
        )
        proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"importing the package failed:\n{proc.stderr.strip()}")
    values = proc.stdout.split()
    if len(values) != 2:
        raise RuntimeError(f"unexpected runtime probe output: {proc.stdout!r}")
    return values[0], values[1]


def git_tags() -> tuple[list[str], list[str]]:
    """(tags pointing at HEAD, every tag in the repo). Empty when git is unavailable."""
    def run(*args: str) -> list[str]:
        try:
            out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                 text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return []
        return [] if out.returncode != 0 else out.stdout.split()
    return run("tag", "--points-at", "HEAD"), run("tag", "--list")


# -- checks (pure: values in, error strings out) ------------------------------

def check_version_syntax(version: str) -> list[str]:
    if not _VERSION_RE.match(version):
        return [f"version {version!r} is not the X.Y.Z[pre][.post][.dev] form "
                f"this project releases"]
    return []


def check_runtime(version: str, runtime: str, server: str) -> list[str]:
    errors = []
    if runtime != version:
        errors.append(f"nevertwice.__version__ is {runtime!r}, "
                      f"pyproject declares {version!r}")
    if server != version:
        errors.append(f"mcp_server.SERVER_VERSION is {server!r}, "
                      f"pyproject declares {version!r}")
    return errors


def check_tags(version: str, tags_at_head: list[str], all_tags: list[str],
               *, require_tag: bool = False) -> tuple[list[str], list[str]]:
    """Release tags are `v` + the version. Returns (errors, notes)."""
    expected = f"v{version}"
    at_head = sorted(t for t in tags_at_head if t.startswith("v"))
    errors: list[str] = []
    notes: list[str] = []

    if len(at_head) > 1:
        errors.append(f"more than one release tag points at HEAD: {', '.join(at_head)}")
    elif at_head and at_head[0] != expected:
        errors.append(f"HEAD is tagged {at_head[0]!r} but the code declares "
                      f"{version!r} (expected tag {expected!r})")

    if require_tag:
        if not at_head:
            errors.append(f"release mode: HEAD carries no release tag - "
                          f"expected {expected!r}")
        elif expected in all_tags and expected not in at_head:
            errors.append(f"release mode: {expected!r} already exists on another commit")
    elif not at_head and expected in all_tags:
        notes.append(f"{expected} is already tagged at an earlier commit - commits "
                     f"since then identify as {version} without being that release; "
                     f"bump the version before publishing again")
    elif not at_head:
        notes.append(f"untagged development commit at version {version}")
    return errors, notes


def check_dist(version: str, artifacts: list[tuple[str, str]]) -> list[str]:
    """`artifacts` is [(filename, metadata text)] for the built sdist and wheel."""
    errors: list[str] = []
    if not artifacts:
        return ["no sdist or wheel found to check"]

    kinds = {".whl" if name.endswith(".whl") else ".tar.gz" for name, _ in artifacts}
    for missing in sorted({".whl", ".tar.gz"} - kinds):
        errors.append(f"no {missing} artifact was built")

    for name, meta in artifacts:
        prefix = (f"{DIST_NAME}-{version}-" if name.endswith(".whl")
                  else f"{DIST_NAME}-{version}.")
        if not name.startswith(prefix):
            errors.append(f"{name}: filename does not start with {prefix!r}")

        fields = _metadata_fields(meta)
        if fields.get("Version", []) != [version]:
            errors.append(f"{name}: metadata Version is "
                          f"{fields.get('Version') or ['(absent)']}, "
                          f"expected {version!r}")
        if fields.get("Name", []) != [DIST_NAME]:
            errors.append(f"{name}: metadata Name is "
                          f"{fields.get('Name') or ['(absent)']}, "
                          f"expected {DIST_NAME!r}")
        # setuptools>=77 emits the SPDX expression; the deprecated License table and
        # the License classifier must both be gone, or a later setuptools drops them
        # for us in the middle of a release.
        if fields.get("License-Expression", []) != ["MIT"]:
            errors.append(f"{name}: metadata has no `License-Expression: MIT` "
                          f"(got {fields.get('License-Expression') or ['(absent)']})")
        if not fields.get("License-File"):
            errors.append(f"{name}: metadata carries no `License-File` entry")
        stale = [c for c in fields.get("Classifier", []) if c.startswith("License ::")]
        if stale:
            errors.append(f"{name}: deprecated license classifier still present: {stale}")
    return errors


def _metadata_fields(meta: str) -> dict[str, list[str]]:
    """Parse the RFC-822 header block of a METADATA/PKG-INFO file.

    Stops at the blank line that starts the long description, and skips folded
    continuation lines - only the header values matter here.
    """
    fields: dict[str, list[str]] = {}
    for line in meta.splitlines():
        if not line.strip():
            break                       # end of headers, description follows
        if line[0].isspace():
            continue                    # folded continuation of the previous value
        key, sep, value = line.partition(": ")
        if not sep:
            continue
        fields.setdefault(key, []).append(value.strip())
    return fields


# -- driver ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check that every place carrying the version agrees.")
    ap.add_argument("--dist", metavar="DIR", type=Path,
                    help="also check the sdist and wheel built into DIR")
    ap.add_argument("--release", action="store_true",
                    help="tag-time strictness: HEAD must carry the matching release tag")
    args = ap.parse_args(argv)

    version = read_pyproject_version(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    errors = check_version_syntax(version)

    runtime, server = runtime_versions()
    errors += check_runtime(version, runtime, server)

    at_head, all_tags = git_tags()
    tag_errors, notes = check_tags(version, at_head, all_tags, require_tag=args.release)
    errors += tag_errors

    artifacts: list[Path] = []
    if args.dist is not None:
        artifacts = sorted(p for p in args.dist.iterdir()
                           if p.suffix == ".whl" or p.name.endswith(".tar.gz"))
        errors += check_dist(version, [read_metadata(p) for p in artifacts])

    print(f"declared version: {version}")
    print(f"  runtime __version__ : {runtime}")
    print(f"  mcp SERVER_VERSION  : {server}")
    print(f"  tags at HEAD        : {', '.join(at_head) or '(none)'}")
    if args.dist is not None:
        print(f"  artifacts           : "
              f"{', '.join(p.name for p in artifacts) or '(none)'}")
    for note in notes:
        print(f"  note: {note}")
    for err in errors:
        print(f"  ERROR: {err}")
    print("version contract holds" if not errors
          else f"version contract broken: {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
