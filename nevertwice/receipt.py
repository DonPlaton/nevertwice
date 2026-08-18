#!/usr/bin/env python3
"""Injection receipt - the SessionStart payload accounts for itself.

A memory that spends the user's context window on their behalf should say what it spent
and what it refused. The payload is assembled under a hard character budget
(`INJECT_BUDGET_CHARS`): sections are added by priority and the rest is dropped. Until now
that drop was SILENT - a session could be missing the one lesson that mattered and nothing
said so.

This module turns the budget decision into a one-line receipt carried by the injection
itself:

    _memory: ~430 tok · 5 lessons (2 held back) · saved ~11.2k_

Three numbers, each answering a question a size gauge cannot:

    ~430 tok      what this cost      (the price, in the unit that is actually billed)
    2 held back   what it refused     (truncation transparency - the budget's silent half)
    saved ~11.2k  what it bought      (OPTIONAL - shown only when the caller supplies an
                  honest per-recall figure; the hook passes 0 since review 2026-08, because
                  the only available number was an upper bound vs a full-store re-paste,
                  and quoting a counterfactual bound as a per-session fact overstated it)

Design constraints, in order of precedence:

1. The receipt is a DIAGNOSTIC and must never displace CONTENT. It takes no reservation:
   the payload is assembled exactly as it would be without this module, and the receipt is
   appended only into room that is already left over. So "never costs a lesson" is a
   property of the construction, not a hope.
   (The first version DID reserve room up front. Measured on the live store that was fine
   at the default 2200-char budget - 0 lessons lost across 12 projects - but cost a lesson
   in 4 of 12 projects at a 1200-char budget. A guarantee that holds only at the default is
   not a guarantee, so the reservation was removed.)
2. It is counted INSIDE the budget, preserving the audited invariant that the cap bounds
   the WHOLE payload (audit M-d).
3. When the leftover room is tight the receipt DEGRADES instead of vanishing: it drops the
   least important clause first, so the one fact worth knowing - that lessons were held
   back - survives on exactly the crowded injections where it matters most.
4. Pure and dependency-free: no imports from the package (stats imports memory_hook, so
   importing stats here would close a cycle). `saved` is passed in by the caller.

One consequence is worth stating plainly: on a payload that FILLS its budget there is no
leftover room, so the receipt is dropped entirely - exactly the case where a refusal notice
would be most interesting. That is the correct trade, not a gap: with room for one more
line, a lesson is worth more than a note saying a lesson was omitted. The durable fix is to
put the count where it costs nothing - `as_dict()` is already the machine-readable twin, so
recording it into the savings ledger would let `nevertwice-stats` report how often
injections are truncating without spending a single character of context. Not built here:
nothing consumes it yet.

The natural extension - also unbuilt - is attribution: record which injected lessons the
session actually used, and the receipt stops being a report and becomes the feedback signal
for a self-calibrating budget.
"""

# The project-wide back-of-envelope: ~4 characters per token (mirrors stats.est_tokens,
# duplicated rather than imported to keep this module cycle-free).
CHARS_PER_TOKEN = 4


def est_tokens(s: str) -> int:
    """Rough token count for a string: ~4 characters per token, floored at 0."""
    return max(0, len(s or "") // CHARS_PER_TOKEN)


def human(n: int) -> str:
    """Compact magnitude: 11200 -> '11.2k', 950 -> '950', 999_999 -> '1M'. The round-then-
    compare mirrors stats._human: a plain >= 1 test formats 999_999 as '1000k' (review
    2026-08 - the exact bug stats.py documents fixing)."""
    n = int(n)
    for unit, div in (("M", 1_000_000), ("k", 1_000)):
        if round(abs(n) / div, 1) >= 1:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + unit
    return str(n)




class Receipt:
    """Accounting for one injection: what got in, what the budget refused, what it saved.

    Mutated during assembly (`shown` / `held`), then sealed with the finished payload. Every
    method is pure arithmetic - no I/O - so the whole thing is unit-testable without running
    a hook."""

    __slots__ = ("budget", "shown", "held", "saved", "chars")

    def __init__(self, budget: int = 0):
        self.budget = max(0, int(budget or 0))
        self.shown = 0      # lessons/facts that made it into the payload
        self.held = 0       # lessons the budget refused (the silent half, now visible)
        self.saved = 0      # tokens this recall avoided vs dumping the whole store
        self.chars = 0      # final payload size

    def show(self, n: int = 1) -> None:
        self.shown += max(0, int(n))

    def hold(self, n: int = 1) -> None:
        self.held += max(0, int(n))

    def seal(self, payload: str, saved: int = 0) -> "Receipt":
        """Record the finished payload's size and what it saved. Returns self so a caller can
        chain `.seal(...).line()`."""
        self.chars = len(payload or "")
        self.saved = max(0, int(saved or 0))
        return self

    def line(self) -> str:
        """The one-line receipt, in Obsidian/markdown italics so it reads as metadata rather
        than as another lesson. Sections appear only when they carry information: no
        'held back' on a payload that fit, no 'saved' before the ledger knows the store size."""
        return self.variants()[0]

    def variants(self) -> list:
        """The receipt at descending levels of detail, richest first.

        Degradation order is chosen by what a reader loses least by losing: the savings
        estimate (nice to know) goes first, then the cost (visible elsewhere in the ledger),
        leaving the refusal count - the one thing NOTHING else in the system reports. A
        payload with nothing held back has no minimal form worth printing, so it simply runs
        out of variants and prints nothing."""
        cost = f"~{human(est_tokens('x' * self.chars))} tok"
        lessons = f"{self.shown} lesson" + ("" if self.shown == 1 else "s")
        if self.held:
            lessons += f" ({self.held} held back)"
        out = []
        if self.saved:
            out.append(f"_memory: {cost} · {lessons} · saved ~{human(self.saved)}_")
        out.append(f"_memory: {cost} · {lessons}_")
        if self.held:
            out.append(f"_memory: {self.held} lesson"
                       + ("" if self.held == 1 else "s") + " held back by budget_")
        return out

    def best_line(self, payload_len: int) -> str:
        """The richest variant that fits in the room the payload left over, or "" when even
        the shortest does not. This is the whole no-displacement guarantee in one method: the
        receipt only ever consumes slack, so it can never push content out."""
        for v in self.variants():
            if self.fits(payload_len, len(v)):
                return v
        return ""

    def fits(self, payload_len: int, line_len: int) -> bool:
        """True when payload + receipt (on its own line) still respect the budget. With no
        budget set, everything fits."""
        return self.budget <= 0 or payload_len + line_len + 1 <= self.budget

    def as_dict(self) -> dict:
        """Machine-readable twin, for a ledger or a test."""
        return {"budget": self.budget, "shown": self.shown, "held_back": self.held,
                "chars": self.chars, "tokens": est_tokens("x" * self.chars),
                "saved_tokens": self.saved}
