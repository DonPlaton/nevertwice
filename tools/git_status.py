#!/usr/bin/env python3
r"""One reading of `git status`, shared by every tool that refuses to record on a dirty tree.

Ten registrars used to carry their own copy of three lines over `git status --porcelain`:

    {line[3:].strip().replace("\\", "/") for line in out.splitlines() if len(line) > 3}

Three things are wrong with that text, and the suite
(`tests/_test_git_status_parsing.py`) measured all three on a real repository:

* a **rename** is reported as `R  old -> new` on ONE line, so the parsed "path" is the string
  `"old -> new"` and matches nothing. `git mv` a file inside a claim's command closure and the
  registrar sees a clean tree;
* a path holding a **space** or any **byte outside ASCII** is wrapped in double quotes by git,
  so the parsed path keeps its quotes and matches nothing;
* inside such a quoted path the bytes are written as **octal escapes**, and the `replace` meant
  for Windows separators turns each one into a slash: a Cyrillic name becomes `/320/267...`.

`--porcelain -z` removes the problem instead of parsing around it: fields are NUL-terminated,
paths are emitted raw (no quoting, no escaping - the `-z` form has no way to need it), and a
rename's original name arrives as its own field rather than glued on with an arrow.

The status code itself is deliberately not interpreted beyond R/C: for this question every
record means the same thing - that path is not what HEAD says it is.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

NUL = chr(0)


def parse(porcelain_z: str) -> set[str]:
    """Every repo-relative path named by `git status --porcelain -z` output.

    A record is two status characters, a space, then the path. When the status is a rename or a
    copy the ORIGINAL path follows as the next field and is dirty too - the rename emptied it.
    Consuming that field is also what keeps the parser in step for every record after it.
    """
    fields = porcelain_z.split(NUL)
    if fields and fields[-1] == "":
        fields.pop()                       # -z terminates every field, including the last
    out: set[str] = set()
    i = 0
    while i < len(fields):
        record = fields[i]
        i += 1
        if len(record) < 4:                # "XY p" is the shortest a record can be
            continue
        status, path = record[:2], record[3:]
        out.add(path)
        if ("R" in status or "C" in status) and i < len(fields):
            out.add(fields[i])
            i += 1
    return out


def dirty_files(root: Path | str | None = None) -> set[str]:
    """Every path `git` reports as changed in the tree at `root` (default: the current one).

    The bytes are decoded as UTF-8 explicitly, NOT by `text=True`. `text=True` decodes with the
    locale's preferred encoding, which on the machine this was written on is cp1251: git hands
    over the path's bytes as they are on disk, so a Cyrillic filename came back as mojibake and
    matched no closure path. That was the same defect as the quoting one, one layer down, and it
    was in all ten copies as well.
    """
    proc = subprocess.run(["git", "status", "--porcelain", "-z"], cwd=root,
                          capture_output=True, check=True)
    return parse(proc.stdout.decode("utf-8", errors="surrogateescape"))


if __name__ == "__main__":
    for p in sorted(dirty_files()):
        print(p)
