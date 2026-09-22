#!/usr/bin/env python3
"""A page that says its figures must not be quoted must not have them quoted on the way in.

`03bef3c` fixed one instance: README's evidence column sent the reader who checks the project's
single live number to a page opening **"Withdrawn: figures on this page must not be quoted."**
The council's fourth point was that the same silhouette had not been checked anywhere else. It had
not, and it is not rare:

    pages that open with a withdrawal              45
    rows on reader-facing pages linking to one     59
    of those, quoting a figure without saying so   10   (fixed here: 4 in research/README.md,
                                                         6 in docs/README.md)

The fix is not to delete the numbers. An index may say what a study concluded - that is what an
index is for. What it may not do is print `R@5 0.80` or `0.36→0.05 (-86%)` as a current result and
link to the page that retracted it, because the reader who follows the link is the one doing
exactly what this project asks of them.

So the rule is narrow and mechanical: on a line that links to a withdrawn page AND prints a
number, the word "withdrawn" must appear on that line too. It is the same shape as the register's
own rule - a claim may be withdrawn, but it may not be withdrawn *silently*.

What this does NOT see, stated rather than implied: a paragraph that quotes the number on one line
and links on another, and prose that quotes a withdrawn figure with no link at all. The second is
`tools/check_prose_numbers.py`'s question - it asks whether a number matches its artifact, which
is a different question from whether the artifact's page is quotable.

    python tests/_test_withdrawn_pages_are_not_quoted.py
"""
import _env_guard  # noqa: F401
import posixpath
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


#: The withdrawal is at the top of the page or it is not a warning - a reader who has scrolled has
#: already read the figures. 600 characters is the first screen.
HEAD = 600
WD = re.compile(r"withdrawn|must not be quoted", re.I)
LINK = re.compile(r"\]\(([^)]+)\)")
#: A decimal or a percentage. Bare integers are excluded on purpose: "150 commits" and "four
#: mechanisms" are counts of the work, not results, and a rule that flagged them would be noise.
NUM = re.compile(r"\d+\.\d+|\d+%")

listed = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                        encoding="utf-8", errors="replace").stdout.split()
pages = [f for f in listed if f.endswith(".md")]


def text_of(rel: str) -> str:
    try:
        return (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


withdrawn = {f for f in pages if WD.search(text_of(f)[:HEAD])}


def resolve(base: str, target: str) -> str | None:
    """A link target as a repo-relative POSIX path, or None when it leaves the repository.

    POSIX arithmetic on purpose: the first version joined with `pathlib.Path`, which on Windows
    produced `docs\\..\\research\\X.md`, so splitting on "/" found one segment, nothing resolved,
    and the whole check passed with ZERO offenders - green because it was looking at nothing.
    The line below it, the one that exercises the rule on a built row, is what caught that.
    """
    target = target.split("#")[0].strip()
    if not target or target.startswith(("http", "mailto:")):
        return None
    parts: list[str] = []
    for p in posixpath.join(base, target).split("/"):
        if p == "..":
            if parts:
                parts.pop()
        elif p not in (".", ""):
            parts.append(p)
    return "/".join(parts)


def offenders(rel: str, body: str | None = None) -> list[str]:
    """Lines of one page that quote a figure and link to a withdrawn page without saying so."""
    base = posixpath.dirname(rel)
    out = []
    for i, line in enumerate((body if body is not None else text_of(rel)).split("\n"), 1):
        targets = [t for t in (resolve(base, m.group(1)) for m in LINK.finditer(line))
                   if t in withdrawn]
        if targets and NUM.search(line) and not WD.search(line):
            out.append(f"{rel}:{i} -> {targets[0]}")
    return out


print("\n- the withdrawn pages are found by their own first screen -")
check("there are withdrawn pages to check", len(withdrawn) >= 40, str(len(withdrawn)))
check("ACTIVE_MEMORY.md, the one 03bef3c was about, is among them",
      "research/ACTIVE_MEMORY.md" in withdrawn)

print("\n- no reader-facing page quotes a figure from one without saying it is withdrawn -")
facing = [f for f in pages if f not in withdrawn]
bad: list[str] = []
for f in facing:
    bad += offenders(f)
check("every such line names the withdrawal", not bad, " | ".join(bad[:4]))

linking = sum(1 for f in facing
              for line in text_of(f).split("\n")
              if any(t in withdrawn
                     for t in (resolve(posixpath.dirname(f), m.group(1))
                               for m in LINK.finditer(line))))
#: Linking to a withdrawn page is fine and common - the map of the research is made of such links.
#: The count is pinned so that the rule above is known to be looking at something.
check("and the rule has something to look at: rows that link to a withdrawn page",
      linking >= 50, str(linking))

print("\n- the rule bites, rather than passing because nothing matches -")
probe = "| a study | [`ACTIVE_MEMORY.md`](research/ACTIVE_MEMORY.md) | recall 0.80 |"
check("a row quoting 0.80 beside a withdrawn page is refused",
      offenders("README.md", probe))
check("the same row passes once it says the figure is withdrawn",
      not offenders("README.md", probe.replace("recall 0.80", "recall 0.80, withdrawn")))
check("and a row with no figure is left alone",
      not offenders("README.md", "| a study | [`A`](research/ACTIVE_MEMORY.md) | the method |"))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
