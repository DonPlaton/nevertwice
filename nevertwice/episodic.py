"""A6.4, level 2 - the episodic tier: dated, role-tagged chunks of what was actually said.

Level 1 is the extractor: a model reads the session and writes durable notes. It is compact,
dated and ranked well, and it is lossy by construction - a detail the extractor judged secondary
is gone, and `K76 yield` on the A6 report measures exactly that (0.0-0.2 for writer-gated
pipelines). Level 2 is the coverage the extractor cannot lose: every speaker turn, chunked and
dated at write time, with no model call at all.

The one thing this module is strict about is ROLE. The failure the A6 report names - abstention
questions answered with the assistant's own hypothetical advice, read back as a user fact - is a
tagging failure, not a retrieval one. So an assistant turn is NEVER emitted under a speaker tag:
it carries `[Assistant context]`, the caller can filter it out of the fact index entirely (the
engine does), and no chunk mixes the two roles, which is what makes "was this the user's own
words" answerable from the chunk text alone.

Format of one chunk::

    [Date: 2023-05-20 | Speaker: user] I moved from Lisbon to Berlin in March.
    [Date: 2023-05-20 | Assistant context] Berlin has a strong startup scene...

Consecutive turns of the same role are packed into one chunk up to `MAX_CHUNK_CHARS` so a session
of twenty short statements does not become twenty index entries; a long turn is split at sentence
then word boundaries, and the role tag is repeated on every piece so a piece is never orphaned
from who said it.

Standard library only (`re`): imported through `_sibling("episodic")`, and the chunker runs
inline on the session's write path, where an import of the store or the embedder would be a cost
paid on every capture.
"""
from __future__ import annotations

import re

__all__ = ["MAX_CHUNK_CHARS", "extract_episodic_chunks"]

#: The size ceiling of one indexed chunk. Retrieval reads a chunk as one unit and the embedding
#: model truncates at its own limit, so a chunk has to be small enough to be one coherent
#: episode: 600 characters is roughly a long paragraph or a few short turns.
MAX_CHUNK_CHARS = 600

#: Transcript headers that are not speech. Skipped before the turn parser looks at the line,
#: because `Working directory:` matches the named-speaker shape (a capitalised word, a colon).
_HEADER_RE = re.compile(
    r"^(?:conversation transcript\b"
    r"|conversation from\s+\d{4}-\d{2}-\d{2}\s*:?"
    r"|working directory:"
    r"|trigger:)",
    re.I)

#: `<role>: <body>`. `user`/`assistant`/`system` are the agent-hook roles; the capitalised arm
#: is a NAMED speaker - a dialogue transcript ("Caroline: I went to the museum") uses a name in
#: the role slot, and the name is what the note has to preserve.
_TURN_RE = re.compile(r"^(user|assistant|system|[A-Z][a-zA-Z0-9_-]{1,24}):\s*(.*)$")

#: Where a session date can be recovered when the caller did not supply one.
_DATE_IN_TRANSCRIPT_RE = re.compile(r"Conversation from (\d{4}-\d{2}-\d{2})")

#: Roles whose words are the assistant's own reasoning or advice, not a fact about the speaker.
#: `system` belongs here rather than in the speaker arm: a system prompt is context, and
#: presenting it as the user's own words is the exact confusion this module exists to prevent.
_CONTEXT_ROLES = frozenset(("assistant", "system"))


def _turns(transcript: str) -> list[tuple[str, str]]:
    """`[(role, body)]` in transcript order.

    A line that starts a new `<role>:` opens a turn; anything else is a continuation of the
    current one (a multi-line turn is one statement). Prose with no role prefix at all - a raw
    dialogue blob, or an ingested document - is read as the USER's own words: the alternative is
    silently dropping it, and this tier exists so nothing is dropped.
    """
    turns: list[tuple[str, str]] = []
    role, buf = "", []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if role and body:
            turns.append((role, body))

    for raw in (transcript or "").splitlines():
        line = raw.strip()
        if _HEADER_RE.match(line):
            continue
        mo = _TURN_RE.match(line)
        if mo:
            flush()
            role, buf = mo.group(1), [mo.group(2)]
            continue
        if not line:
            continue
        if not role:
            role = "user"
        buf.append(line)
    flush()
    return turns


def _split_body(body: str, limit: int) -> list[str]:
    """`body` cut into pieces of at most `limit` characters, at a sentence then a word boundary.

    A cut inside a word is the last resort: a piece that ends mid-token reads as a broken fact,
    so the sentence terminator wins, then the space, and only then the hard character cut (a URL
    or a hashed identifier with no spaces at all).
    """
    body = (body or "").strip()
    if len(body) <= limit:
        return [body] if body else []
    pieces: list[str] = []
    rest = body
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
        if cut > 0:
            pieces.append(rest[:cut + 1].strip())
            rest = rest[cut + 1:].lstrip()
            continue
        cut = window.rfind(" ")
        if cut > 0:
            pieces.append(rest[:cut].strip())
            rest = rest[cut + 1:].lstrip()
            continue
        pieces.append(rest[:limit])
        rest = rest[limit:].lstrip()
    if rest:
        pieces.append(rest)
    return [p for p in pieces if p]


def extract_episodic_chunks(transcript: str, session_date: str = "", session_id: str = "",
                            max_chars: int | None = None) -> list[str]:
    """The transcript as dated, role-tagged chunks, each at most `max_chars` characters.

    `session_date` is the session's own day and drives every `[Date: ...]` tag; when it is empty
    the date is recovered from a `Conversation from YYYY-MM-DD:` header, and `unknown` is used
    when the transcript carries none. `session_id` is accepted for provenance but does not enter
    the chunk text: the caller names the index entry with it, and keeping it out of the text
    keeps two re-mines of one session byte-identical.

    `max_chars` defaults to `MAX_CHUNK_CHARS`, resolved HERE rather than in the signature - a
    default freezes the constant at def time and the package forbids that (a rebound limit would
    keep answering the old number).

    Deterministic for the same input: no sets, no dict iteration, no clock. Empty string or no
    parseable turns gives `[]`, never a chunk of pure boilerplate.
    """
    limit = MAX_CHUNK_CHARS if max_chars is None else int(max_chars)
    if limit <= 0:
        return []
    text = transcript or ""
    day = str(session_date or "").strip()[:10]
    if not day:
        mo = _DATE_IN_TRANSCRIPT_RE.search(text)
        day = mo.group(1) if mo else "unknown"

    chunks: list[str] = []
    buf, buf_kind = "", ""
    for role, body in _turns(text):
        kind = "context" if role.strip().lower() in _CONTEXT_ROLES else "speaker"
        prefix = (f"[Date: {day}] [Assistant context] " if kind == "context"
                  else f"[Date: {day} | Speaker: {role}] ")
        #: Every piece repeats the role tag: a piece must never be separable from who said it,
        #: and packing is what decides whether two pieces share a chunk, not the split.
        room = limit - len(prefix)
        if room < 1:                 # a limit shorter than the tag cannot hold a tagged chunk
            continue
        for piece in _split_body(body, room):
            item = prefix + piece
            if buf and kind == buf_kind and len(buf) + 1 + len(item) <= limit:
                buf = f"{buf}\n{item}"
                continue
            if buf:
                chunks.append(buf)
            buf, buf_kind = item, kind
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c.strip()]
