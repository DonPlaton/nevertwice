"""A6.2 - relative dates become absolute dates, and every note carries the day it was said.

A session transcript says "yesterday the build failed", "2 weeks ago it passed". Written to the
store as-is, the note means something different on every day it is read, and it is unanswerable
three weeks later: the reader has no anchor, and the model that reads the memory has no way to
recover one. That is the temporal arm of the A6 report - langmem answered 20% of the Temporal
questions and mem0 reported "28 days" where the session had said "29", both because the day a
relative phrase was spoken never survived extraction.

Two mechanisms, both deterministic and both applied at WRITE time, so a read never has to guess:

* `resolve_relative_dates` - rewrites the transcript the extractor is shown, keeping the phrase
  and appending the date it resolved to: `yesterday` -> `yesterday [2023-05-19]`. Both
  representations stay in the text on purpose - the requirement asks for that - so the note can
  quote the user's own words and still carry the absolute date.
* `stamp_date_anchor` - appends `[Date: YYYY-MM-DD]` to an extracted description, the same tag
  the episodic chunks carry, so a note read on its own says when it was true.

Idempotence is part of the contract, not a nicety: extraction runs twice on a grown transcript
(`process_session` re-mines the tail), and a pass that re-annotated its own annotations would
turn `yesterday [2023-05-19]` into `yesterday [2023-05-19] [2023-05-19]` on every retry.

Standard library only (`re`, `datetime`): imported lazily through `_sibling("temporal")`.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

__all__ = ["resolve_relative_dates", "stamp_date_anchor"]

#: A bare ISO day. Anything else - a full timestamp, a malformed date, None - is not an anchor,
#: and a caller that passes one gets its text back unchanged rather than an exception: this runs
#: on the write path, where a bad date must cost the annotation, never the session.
_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: "not already annotated". The negative lookahead is what makes the whole module idempotent:
#: the second pass sees what the first pass wrote and declines to write it again.
_NOT_ANNOTATED = r"(?!\s*\[\d{4}-\d{2}-\d{2}\])"

_YESTERDAY_RE = re.compile(r"\b(yesterday)\b" + _NOT_ANNOTATED, re.I)
_TOMORROW_RE = re.compile(r"\b(tomorrow)\b" + _NOT_ANNOTATED, re.I)
_AGO_RE = re.compile(r"\b(\d+)\s+(day|days|week|weeks)\s+ago\b" + _NOT_ANNOTATED, re.I)


def resolve_relative_dates(text: str, anchor_date: str | None) -> str:
    """`text` with yesterday / tomorrow / N days|weeks ago resolved against `anchor_date`.

    The phrase is KEPT and the resolved day is appended in brackets (`yesterday [2023-05-19]`):
    the requirement wants both representations, and keeping the original words means the
    extractor can still quote the speaker. An already-annotated phrase is left alone, so
    resolving twice changes nothing the second time.

    Returns `text` unchanged when there is no text, no usable anchor (`YYYY-MM-DD`), or no
    relative phrase - the common case, and the cheap one.
    """
    if not text or not anchor_date:
        return text
    day = str(anchor_date).strip()[:10]
    if not _DAY_RE.fullmatch(day):
        return text
    try:
        base = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return text                              # 2023-02-30: shaped like a date, is not one

    out = _YESTERDAY_RE.sub(
        lambda m: f"{m.group(1)} [{(base - timedelta(days=1)).isoformat()}]", text)
    out = _TOMORROW_RE.sub(
        lambda m: f"{m.group(1)} [{(base + timedelta(days=1)).isoformat()}]", out)

    def _ago(m: "re.Match") -> str:
        n = int(m.group(1))
        days = n * 7 if m.group(2).lower().startswith("week") else n
        return f"{m.group(0)} [{(base - timedelta(days=days)).isoformat()}]"

    return _AGO_RE.sub(_ago, out)


def stamp_date_anchor(desc: str, anchor_date: str | None) -> str:
    """`desc` with `[Date: YYYY-MM-DD]` appended once, or `desc` itself when there is nothing
    to stamp with (not a string, no anchor, a malformed anchor).

    Appends at the END and only when that exact tag is absent: idempotent on its own output, so
    the grown-transcript re-mine cannot stack the same day twice. A description already carrying
    ANOTHER day's tag gains this one as well - each tag is the record of a pass that extracted
    the statement, and dropping either would be a claim about history nobody made.
    """
    if not isinstance(desc, str) or not anchor_date:
        return desc
    day = str(anchor_date).strip()[:10]
    if not _DAY_RE.fullmatch(day):
        return desc
    tag = f"[Date: {day}]"
    if tag in desc:
        return desc
    return f"{desc.rstrip()} {tag}" if desc.strip() else tag
