"""A6.1 - which memory domain a session belongs to, and the rubric the extractor gets for it.

`nevertwice` was built for one domain: an agent working on a codebase. Its extraction prompt
asks a single question - is this session about THIS project's code, research or tasks - and an
honest "no" discards the session's content on purpose (audit C1: empty beats wrong). That answer
is correct for a coding session and wrong for the same engine pointed at a life: "I graduated
with a degree in Business Administration", "my sister had a baby in April", "I now work as a
logistics coordinator" are exactly the durable facts a personal memory exists to keep, and the
project gate reads every one of them as offtopic.

Two modes, resolved here and nowhere else:

* **code** - today's behaviour, byte for byte: the extraction prompt is unchanged and the
  project relevance gate keeps its meaning. A coding install never pays for the personal path.
* **all** - the same pipeline, plus a compact rubric appended to the extraction prompt that
  tells the model personal, biographical, conversational and technical facts are all relevant,
  and that they belong in the existing `decisions` / `patterns` lists with their speaker, exact
  numbers and exact dates. No new note type, no new store layout, no second writer.

`auto` reads the transcript itself: a session that contains code markers (`def `, `import `,
`.py`, `git `, a fenced block, the engine's own `Working directory:` header) is a coding
session; everything else is treated as all-domain dialogue. `NEVERTWICE_EXTRACT_MODE` overrides
either way, so an operator who knows their store is personal or is working does not depend on a
regex.

Standard library only (`os`, `re`): this module is imported lazily by the engine through
`_sibling("modes")` and must never drag the store, the embedder or a network client in.
"""
from __future__ import annotations

import os
import re

__all__ = ["VALID_MODES", "CODE_MARKERS_RE", "ALL_MODE_RUBRIC",
           "resolve_mode", "mode_prompt_rubric"]

#: The explicit spellings `NEVERTWICE_EXTRACT_MODE` accepts, beside the default `auto`.
VALID_MODES = ("code", "all", "auto")

#: What makes a transcript a CODING session when the mode is `auto`. Each arm is a shape the
#: extractor's own coding prompt is written for: a function or class definition, an import, a
#: source filename, a git command, a fenced block, or the `Working directory:` header the hook
#: itself prepends to every transcript it reads. The extension list is deliberately the source
#: files a coding session names, not every extension: `.md`, `.txt` and `.log` appear in prose
#: about notes and logs and would classify a conversation as code.
CODE_MARKERS_RE = re.compile(
    r"(?:^|\n)\s*(?:def |import |from \S+ import |class |git |Working directory:)"
    r"|\b\w+\.(?:py|ts|js|rs|go|cpp|c|h|java|toml|yaml|json)\b|```")

#: Appended to the extraction prompt in all-domain mode (`brain_block`'s slot, which is the
#: prompt's last field - a later instruction outranks the earlier `project_relevant` rule).
#:
#: Bounded on purpose: the prompt is paid for on every session, and the rubric has to fit in
#: well under a kilobyte to stay cheap beside the transcript it accompanies. It names the
#: existing output fields (`decisions`, `patterns`, `facts`, `supersedes`) rather than inventing
#: new ones, because a schema change would fork the writer and every reader with it.
ALL_MODE_RUBRIC = (
    "\nMODE: ALL-DOMAIN MEMORY (personal, biographical, conversational & technical):\n"
    "- Always set \"project_relevant\": true if the session contains ANY user facts, "
    "biographical details, events, dates, quantities, numbers, preferences, plans, or state updates.\n"
    "- Map durable facts, events, dates, quantities, and state updates into \"decisions\" "
    "(and recurring habits, routines, preferences, or multi-item lists into \"patterns\").\n"
    "- In every description, include the speaker/subject name, exact numbers/quantities, "
    "and exact dates, and copy key literals into \"facts\".\n"
    "- Epistemic modality: tag confirmed user/speaker facts with [Confirmed fact], and tag "
    "assistant explanations, estimates, or hypothetical options with [Hypothetical / Assistant suggestion] so both are recallable without confusion.\n"
    "- When a user updates a count or state, set \"supersedes\" to the earlier note's title.\n"
)


def resolve_mode(mode_env: str | None = None, transcript: str = "") -> str:
    """`"code"` or `"all"` for this session: the explicit mode wins, `auto` reads the text.

    `mode_env` is the caller's value when it has one; None or "" falls back to
    `NEVERTWICE_EXTRACT_MODE` and then to `auto`. An unrecognised value is treated as `auto`
    rather than refused: a typo in an environment variable must not stop a session being
    captured, and `auto` is the conservative reading - it only opens the all-domain prompt for
    text that carries none of the coding markers.
    """
    if not mode_env:
        mode_env = os.environ.get("NEVERTWICE_EXTRACT_MODE", "auto")
    m = (mode_env or "auto").strip().lower()
    if m == "code":
        return "code"
    if m == "all":
        return "all"
    return "code" if CODE_MARKERS_RE.search(transcript or "") else "all"


def mode_prompt_rubric(mode: str) -> str:
    """The extra prompt text for `mode`: "" in code mode, the all-domain rubric otherwise.

    "" is the contract for code mode, not "a small nudge": the coding prompt is the one every
    existing store was measured with, and a byte added to it is a change to that measurement.
    """
    return "" if (mode or "").strip().lower() == "code" else ALL_MODE_RUBRIC
