"""Read a git history efficiently, without a subprocess per blob.

Every later phase reads commits the same way, so the census, the mutation generator
and the detector harnesses all share one implementation. Two design points matter:

* **One `git log` pass per repository.** ``git show`` per commit is ~30 ms on Windows;
  over a 3000-commit window that alone is 90 s per repository before anything is parsed.
* **A persistent ``git cat-file --batch``** for blob content. Spawning a process per
  file turns a 20 000-blob census into an hour of process creation.

Nothing here writes to a repository. Clones are read-only fixtures; a census that
mutates its own corpus cannot be re-run.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SEP = "\x01"


@dataclass
class CommitFiles:
    """One commit and the paths it touched, from a single ``git log`` pass."""

    sha: str
    parent: str
    subject: str
    author_date: str
    py_paths: list[str] = field(default_factory=list)
    other_paths: list[str] = field(default_factory=list)

    @property
    def n_py(self) -> int:
        return len(self.py_paths)


def _run(repo: Path, args: list[str], *, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args[:3])} failed in {repo.name}: "
            f"{proc.stderr.decode('utf-8', 'replace')[:400]}"
        )
    return proc.stdout.decode("utf-8", "replace")


def head_sha(repo: Path) -> str:
    return _run(repo, ["rev-parse", "HEAD"]).strip()


def total_commits(repo: Path) -> int:
    return int(_run(repo, ["rev-list", "--count", "HEAD"]).strip() or 0)


def log_commits(repo: Path, limit: int | None = None) -> list[CommitFiles]:
    """Every non-merge commit that touched a ``.py`` file, newest first.

    A merge has two parents, so "the state before this commit" is ambiguous and the
    diff a checker would see is not well defined. Merges are excluded rather than
    resolved to the first parent, because a first-parent diff of a merge attributes
    the whole branch to one commit and would inflate every count in the census.
    """
    args = [
        "log",
        "--no-merges",
        "--name-only",
        f"--format={_SEP}%H{_SEP}%P{_SEP}%aI{_SEP}%s",
        "--diff-filter=ACMR",  # added, copied, modified, renamed -- not deleted
    ]
    if limit is not None:
        args.append(f"-n{limit}")
    args += ["--", "*.py"]
    raw = _run(repo, args)

    out: list[CommitFiles] = []
    current: CommitFiles | None = None
    for line in raw.split("\n"):
        if line.startswith(_SEP):
            parts = line.split(_SEP)
            # parts == ['', sha, parents, date, subject...]
            if len(parts) < 5:
                current = None
                continue
            parents = parts[2].split()
            current = CommitFiles(
                sha=parts[1],
                parent=parents[0] if parents else "",
                subject=_SEP.join(parts[4:])[:200],
                author_date=parts[3],
            )
            out.append(current)
            continue
        path = line.strip()
        if not path or current is None:
            continue
        if path.endswith(".py"):
            current.py_paths.append(path)
        else:
            current.other_paths.append(path)
    return [c for c in out if c.py_paths and c.parent]


class BlobReader:
    """A persistent ``git cat-file --batch`` bound to one repository."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self._proc = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=str(repo),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

    def read(self, rev: str, path: str) -> str | None:
        """Source of ``path`` at ``rev``; ``None`` if it did not exist there.

        Returning ``None`` rather than raising is deliberate: a file that did not
        exist in the parent is an *addition*, which is a perfectly ordinary state
        the census has to count, not an error.
        """
        assert self._proc.stdin is not None and self._proc.stdout is not None
        spec = f"{rev}:{path}\n".encode("utf-8", "surrogateescape")
        try:
            self._proc.stdin.write(spec)
            self._proc.stdin.flush()
            header = self._proc.stdout.readline().decode("utf-8", "replace").strip()
        except (BrokenPipeError, OSError):
            return None
        if not header or header.endswith((" missing", " ambiguous")):
            return None
        parts = header.split()
        if len(parts) != 3 or parts[1] != "blob":
            return None
        size = int(parts[2])
        buf = bytearray()
        while len(buf) < size:
            chunk = self._proc.stdout.read(size - len(buf))
            if not chunk:
                break
            buf.extend(chunk)
        self._proc.stdout.read(1)  # trailing newline git appends after the payload
        return bytes(buf).decode("utf-8", "replace")

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.close()
            except OSError:
                pass
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                self._proc.kill()

    def __enter__(self) -> "BlobReader":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# Where the clones live is deliberately *not* known here. `corpora.py` owns that, and it
# owns it because there are two corpora with different rights: a development set anyone
# may tune against, and a held-out set sealed until Phase V. A module that could hand out
# "the corpus" without naming which one is the exact ambiguity F0 removed.


def progress(msg: str) -> None:
    """ASCII-safe progress to stderr.

    This project already lost an MCP server to a cp1251 console; a research script
    that dies on its own progress line is a research script that produced nothing.
    """
    enc = getattr(sys.stderr, "encoding", None) or "ascii"
    try:
        msg.encode(enc)
    except (UnicodeEncodeError, LookupError):
        msg = msg.encode("ascii", "replace").decode("ascii")
    print(msg, file=sys.stderr, flush=True)
