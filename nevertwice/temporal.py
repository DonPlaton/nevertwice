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
  quote the user's own words and still carry the absolute date. A6.5 extends the same contract
  to calendar phrases: weekdays resolve to their ISO day, months to `[March 2023]`, years to
  `[2022]`, and the negative lookahead is widened with them so a resolved phrase is final.
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

__all__ = ["MONTH_NAMES", "resolve_relative_dates", "stamp_date_anchor"]

#: A bare ISO day. Anything else - a full timestamp, a malformed date, None - is not an anchor,
#: and a caller that passes one gets its text back unchanged rather than an exception: this runs
#: on the write path, where a bad date must cost the annotation, never the session.
_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: "not already annotated". The negative lookahead is what makes the whole module idempotent:
#: the second pass sees what the first pass wrote and declines to write it again. It accepts
#: ANY bracketed annotation, not only an ISO day: A6.5 resolves month and year phrases to
#: `[March 2023]` / `[2022]`, and a pass that only recognised `[YYYY-MM-DD]` would annotate its
#: own output again on every re-mine.
_NOT_ANNOTATED = r"(?!\s*\[[^\]]+\])"

_YESTERDAY_RE = re.compile(r"\b(yesterday)\b" + _NOT_ANNOTATED, re.I)
_TOMORROW_RE = re.compile(r"\b(tomorrow)\b" + _NOT_ANNOTATED, re.I)
_AGO_RE = re.compile(r"\b(\d+)\s+(day|days|week|weeks)\s+ago\b" + _NOT_ANNOTATED, re.I)

#: A6.5 - calendar phrases whose answer is a weekday, a month name or a year rather than a day.
#: Resolved against the same anchor and carrying the same "keep the phrase, append the answer"
#: contract as the day offsets above, so a reader sees both what was said and what it meant.
_WEEKDAY_RE = re.compile(
    r"\b(last|next)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    + _NOT_ANNOTATED, re.I)
_MONTH_RE = re.compile(r"\b(last|next)\s+month\b" + _NOT_ANNOTATED, re.I)
_WEEK_RE = re.compile(r"\b(last|next)\s+week\b" + _NOT_ANNOTATED, re.I)
_YEAR_RE = re.compile(r"\b(last|next)\s+year\b" + _NOT_ANNOTATED, re.I)
_YEARS_AGO_RE = re.compile(r"\b(\d+)\s+(year|years)\s+ago\b" + _NOT_ANNOTATED, re.I)

#: 0=Monday .. 6=Sunday, the order `date.weekday()` answers in - mapping through this dict is
#: what keeps the weekday arithmetic below a direct comparison rather than an off-by-one.
_WEEKDAYS = {name: i for i, name in enumerate(
    ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}

#: English month names, indexed `month - 1`. A month phrase resolves to a NAME, not a day: the
#: transcript said "last month", and the honest annotation is the month it named, not a date
#: the speaker never gave.
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December")


def resolve_relative_dates(text: str, anchor_date: str | None) -> str:
    """`text` with relative phrases resolved against `anchor_date`.

    Day offsets (`yesterday`, `tomorrow`, `N days|weeks ago`) resolve to a full ISO day; A6.5
    adds `last|next <weekday>` (the nearest such weekday strictly past the anchor, as ISO),
    `last|next month` (`[March 2023]`), `last|next year` and `N years ago` (`[2022]`).

    The phrase is KEPT and the resolved value is appended in brackets (`yesterday [2023-05-19]`):
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

    out = _AGO_RE.sub(_ago, out)
    out = _YEARS_AGO_RE.sub(
        lambda m: f"{m.group(0)} [{base.year - int(m.group(1))}]", out)

    def _weekday(m: "re.Match") -> str:
        target_wd = _WEEKDAYS[m.group(2).lower()]
        if m.group(1).lower() == "last":
            delta = (base.weekday() - target_wd) % 7 or 7
            target = (base - timedelta(days=delta)).isoformat()
        else:
            delta = (target_wd - base.weekday()) % 7 or 7
            target = (base + timedelta(days=delta)).isoformat()
        return f"{m.group(0)} [{target}]"

    out = _WEEKDAY_RE.sub(_weekday, out)

    def _month(m: "re.Match") -> str:
        if m.group(1).lower() == "last":
            m_num, yr = (12, base.year - 1) if base.month == 1 else (base.month - 1, base.year)
        else:
            m_num, yr = (1, base.year + 1) if base.month == 12 else (base.month + 1, base.year)
        return f"{m.group(0)} [{MONTH_NAMES[m_num - 1]} {yr}]"

    out = _MONTH_RE.sub(_month, out)
    out = _WEEK_RE.sub(
        lambda m: (f"{m.group(0)} [the week before {base.isoformat()}]"
                   if m.group(1).lower() == "last"
                   else f"{m.group(0)} [the week after {base.isoformat()}]"),
        out)
    return _YEAR_RE.sub(
        lambda m: f"{m.group(0)} "
                  f"[{base.year - 1 if m.group(1).lower() == 'last' else base.year + 1}]",
        out)


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
