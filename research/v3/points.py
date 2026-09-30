#!/usr/bin/env python3
"""PREREG-V3 TB4.8a (A6): the read points' context, identical for every arm (rev1 §5.2, §8.2; Q-47-4, Q28).

* fill: an arm's rendered items, in the arm's rank order, into the reader's context - joined by one newline, counted
  with the pinned cl100k_base, up to the ONE budget of 7,000 tokens; the first item that does not fit is cut at the cap
  (its longest prefix that keeps the whole within the budget) and nothing after it is read. There is no per-arm
  budget: the budget is a constant of the point, not a parameter.
* the points: B asks every arm for up to 200 ranked items, K for 10 (claude-code-memory is
  competitor-lacks-capability:k).
* Row V (rev1 §5.2 :1337-1347; the auditor 2026-09-30: Q1..Q6 = (a), the V≡ rule): S1 only, secondary, never a
  headline; declared_axes ["k"]; each product's documented retrieval (V_DEFAULTS), under the same 7,000-token cap. An
  arm whose V row cannot be built yet is refused by its own name; an arm whose vendor default IS its B or K read (the
  same k, the same shape, the same cap) is not read again - V≡B / V≡K, and the artifact names it equivalent_to.
* Claude Code's reads (Q-47-4, Q28): R-all is MEMORY.md, then every other file of the memory directory in
  lexicographic order of its relative path, each file one item; R-index (the V row) is MEMORY.md's first 200 lines or
  25 KB, whichever ends first - a line ends at LF only. A symlink, a non-file or a file that is not UTF-8 text refuses by name - the reader never
  follows a link out of the memory directory or guesses an encoding.

``count(text) -> int`` and ``cut(text, n) -> str`` (the longest prefix of at most n tokens) come from research/v3/tokens.py
in a run; the suite drives them with a fake tokenizer.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

BUDGET = 7000
K_AT = {"B": 200, "K": 10}
SEP = "\n"
R_INDEX_LINES = 200
R_INDEX_BYTES = 25 * 1024
MEMORY_INDEX = "MEMORY.md"
CC_LACKS_K = "competitor-lacks-capability:k"

#: Row V: S1 alone (rev1 :1337), never in a headline (the headline point is B); the axes each point declares (:1338)
V_STANDS = ("S1",)
HEADLINE_POINT = "B"
DECLARED_AXES = {"B": (), "K": (), "V": ("k",)}
#: Zep's Point V template (templates.ARM_TEMPLATES["zep-graphiti:V"]): the sha256 of its text, which is never committed
ZEP_V_TEMPLATE_SHA256 = "1f38010000254c1fcdc7bf2a64b88326023c1094eea6ac6fe2446d263193a9b7"
#: Row V, one entry per rev1 row (:1339-1347): k, shape ("items", or one vendor-"rendered" item - Q1 = (a)), the
#: parameters, and either the refusal an arm carries until its row can be built, or the point its default equals (V≡)
V_DEFAULTS = {
    "nevertwice": {"k": 3, "shape": "items", "params": {"xrerank": "off"}, "rev1": ":1347 PROMPT_RECALL_K"},
    "mem0": {"k": 200, "shape": "items", "params": {"top_k": 200, "threshold": 0.1}, "equivalent_to": "B",
             "rev1": ":1339"},
    "zep-graphiti": {"k": 20, "shape": "rendered", "params": {"edges": 20, "nodes": 20, "template": "zep-graphiti:V"},
                     "template_sha256": ZEP_V_TEMPLATE_SHA256, "rev1": ":1340"},
    "supermemory-local": {"k": 30, "shape": "items", "params": {"limit": 30, "threshold": 0.3},
                          "refused": "V:no-adapter (A8)", "rev1": ":1341"},
    "cognee": {"k": 20, "shape": "rendered", "params": {"chunks": 20, "entities": 20}, "refused": "V:no-adapter (A8)",
               "rev1": ":1342"},
    "langmem": {"k": 10, "shape": "items", "params": {"limit": 10}, "equivalent_to": "K", "rev1": ":1343"},
    "a-mem": {"k": 10, "shape": "items", "params": {"retrieve_k": 10, "output": "search_agentic whole"},
              "refused": "V:adapter", "rev1": ":1344"},
    "letta": {"k": None, "shape": "items", "params": {"core": "all blocks", "archival": "its default page"},
              "refused": "V:page-size-unpinned (Q-47-8h)", "rev1": ":1345"},
    "claude-code-memory": {"k": None, "shape": "rendered", "params": {"read": "R-index"},
                           "refused": "V:no-adapter (A8)", "rev1": ":1346"},
}


class PointError(ValueError):
    """A context that cannot be built as the preregistration says."""


@dataclass(frozen=True)
class Context:
    text: str
    tokens: int
    items_offered: int
    items_used: int          # whole items, plus the cut one when it kept anything
    last_cut: bool
    budget: int = BUDGET


def v_equivalent(arm: str, *, defaults: dict | None = None) -> str | None:
    """V≡ (the auditor, 2026-09-30): the point whose read an arm's V default is - its declared equivalent_to, held
    only while the k is that point's and the shape is items (the cap is one constant); a declaration that does not
    hold refuses by name. None: the arm has its own V read (or none)."""
    e = (V_DEFAULTS if defaults is None else defaults).get(arm) or {}
    eq = e.get("equivalent_to")
    if eq is None:
        return None
    if eq not in K_AT or e.get("k") != K_AT[eq] or e.get("shape") != "items":
        raise PointError(f"{arm}: V is declared equivalent to {eq} but is not equivalent (k {e.get('k')} against "
                         f"{K_AT.get(eq)}, shape {e.get('shape')!r} against 'items') - neither read nor borrowed")
    return eq


def k_for(point: str, arm: str, *, defaults: dict | None = None) -> int:
    """The k an arm is asked for at a point; Claude Code has no k (it reads R-all at B). At V, the arm's documented
    default (V_DEFAULTS) - refused by the arm's own name while its row cannot be built, and by V≡B / V≡K when its
    default is that read."""
    if point == "V":
        table = V_DEFAULTS if defaults is None else defaults
        e = table.get(arm)
        if e is None:
            raise PointError(f"{arm}: no Row V - rev1 §5.2 (:1339-1347) gives a vendor default to each product, not to "
                             "it")
        if e.get("refused"):
            raise PointError(f"{arm}: {e['refused']}")
        eq = v_equivalent(arm, defaults=table)
        if eq is not None:
            raise PointError(f"{arm}: V≡{eq} - its vendor default is its {eq} read (k {e['k']}, the same shape and cap), "
                             f"so V is not read again; the artifact names it equivalent_to {eq}")
        return e["k"]
    if point not in K_AT:
        raise PointError(f"point {point!r} has no k (B or K; V is each arm's vendor default)")
    if arm == "claude-code-memory" and point == "K":
        raise PointError(CC_LACKS_K)
    return K_AT[point]


def fill(items: Sequence[str], *, count: Callable[[str], int], cut: Callable[[str, int], str]) -> Context:
    """The items in their order, whole while the joined text stays within BUDGET, the next one cut to the cap."""
    text = ""
    used = 0
    last_cut = False
    for item in items:
        if not isinstance(item, str):
            raise PointError(f"an item is text, got {type(item).__name__}")
        prefix = text + SEP if used else ""
        whole = prefix + item
        if count(whole) <= BUDGET:
            text, used = whole, used + 1
            continue
        room = BUDGET - count(prefix)
        kept = cut(item, room) if room > 0 else ""
        if not item.startswith(kept):
            raise PointError("the cut item is not a prefix of the item")
        while kept and count(prefix + kept) > BUDGET:        # a merge across the join can cost a token
            shorter = cut(kept, max(count(kept) - 1, 0))
            if len(shorter) >= len(kept) or not kept.startswith(shorter):
                raise PointError("the cut did not return a shorter prefix - the context cannot be fitted")
            kept = shorter
        if kept:
            text, used, last_cut = prefix + kept, used + 1, True
        break
    tokens = count(text)
    if tokens > BUDGET:
        raise PointError(f"the context holds {tokens} tokens, over the budget of {BUDGET}")
    return Context(text=text, tokens=tokens, items_offered=len(items), items_used=used, last_cut=last_cut)


def _read_text(path: Path, rel: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise PointError(f"{rel} in the memory directory is not a regular file (a link is never followed)")
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise PointError(f"{rel} in the memory directory is not UTF-8 text") from None


def _memory_files(memdir: Path) -> list[tuple[str, Path]]:
    memdir = Path(memdir)
    if memdir.is_symlink() or not memdir.is_dir():
        raise PointError(f"the memory directory {memdir} is not a directory")
    out = []
    for p in memdir.rglob("*"):
        rel = p.relative_to(memdir).as_posix()
        if p.is_symlink():
            raise PointError(f"{rel} in the memory directory is a link (never followed)")
        if p.is_dir():
            continue
        out.append((rel, p))
    return sorted(out, key=lambda rp: rp[0])


def claude_r_all(memdir: Path) -> list[str]:
    """Point B for claude-code-memory: MEMORY.md, then every other file in lexicographic relative-path order (Q28)."""
    files = _memory_files(memdir)
    index = [(r, p) for r, p in files if r == MEMORY_INDEX]
    rest = [(r, p) for r, p in files if r != MEMORY_INDEX]
    return [_read_text(p, r) for r, p in index + rest]


def claude_r_index(memdir: Path) -> str:
    """The V row for claude-code-memory: MEMORY.md's first 200 lines or 25 KB, whichever ends first ("" without one)."""
    files = dict(_memory_files(memdir))
    if MEMORY_INDEX not in files:
        return ""
    text = _read_text(files[MEMORY_INDEX], MEMORY_INDEX)
    # A line ends at LF only - never str.splitlines(), which also cuts at U+2028, U+2029, U+0085, CR, VT, FF and
    # \x1c-\x1e inside a line and would hand the reader fewer than 200 of the competitor's lines (PT-SL, the A6 j4 class).
    lines = text.split("\n")
    head = "\n".join(lines[:R_INDEX_LINES]) + ("\n" if len(lines) > R_INDEX_LINES else "")
    raw = head.encode("utf-8")
    if len(raw) > R_INDEX_BYTES:
        head = raw[:R_INDEX_BYTES].decode("utf-8", "ignore")        # never half a character
    return head
