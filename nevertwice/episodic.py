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
from who said it. A6.6 keeps the SPEAKER run open across an assistant reply (the reply has its own
buffer) and caps assistant chunks at two per session, so one verbose model cannot flood the index.

A6.5 adds question-context carryover: when a turn CONTAINS a question and the next turn is a
speaker's answer, the answer's body is prefixed with `(re: <last question>)` before it is chunked,
so an answer split into the next chunk still says what it was answering:

    [Date: 2023-05-25 | Speaker: Caroline] (re: Any fun plans for the summer?) Researching ...

The prefix is bounded (`_REPLY_CONTEXT_MAX`) and only travels one turn, so a question cannot
attach itself to an unrelated later statement.

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
_USER_ROLES = frozenset(("user",))
_ASSISTANT_ROLES = frozenset(("assistant",))
_SKIP_ROLES = frozenset(("system",))
_CONTEXT_ROLES = _ASSISTANT_ROLES | _SKIP_ROLES

#: Markdown labels that sit inside an assistant turn (`Cons: ...`, `Cost: ...`) and match the
#: capitalised named-speaker arm of `_TURN_RE`. They are not speakers: in a `user:`/`assistant:`
#: dialogue the turn parser must keep them in the current turn's body, or the label's line
#: becomes a separate "speaker" whose words are the model's own list item.
_NON_SPEAKER_LABELS = frozenset({
    "pros", "cons", "note", "notes", "cost", "price", "summary", "example", "tip", "tips",
    "warning", "step", "option", "options", "result", "conclusion", "source", "status",
    "time", "date", "location", "http", "https", "pros and cons",
})

#: A6.5 - how much of a question travels with the answer that follows it. A chunk can end
#: between a question and its answer (600 characters is a paragraph, and a dialogue crosses it),
#: and "Researching adoption agencies" read alone is a fragment whose subject was lost. The last
#: question of the immediately preceding turn is prepended as `(re: <question>)`, bounded so a
#: long question cannot crowd out the answer it is attached to.
_REPLY_CONTEXT_MAX = 90

#: Sentence terminators the question extractor walks back from the final `?`. A newline ends a
#: sentence too: a turn body is multi-line, and the last question of one line must not swallow
#: the line before it.
_SENTENCE_BREAKS = ".!?\n"


def _last_question(body: str) -> str:
    """The last question sentence in `body`, trimmed to `_REPLY_CONTEXT_MAX`, or "".

    Walks back from the final `?` to the previous sentence terminator, so a body that both
    explains and asks contributes only the question. Deterministic: string search only.
    """
    idx = (body or "").rfind("?")
    if idx < 0:
        return ""
    start = 0
    for i in range(idx - 1, -1, -1):
        if body[i] in _SENTENCE_BREAKS:
            start = i + 1
            break
    return body[start:idx + 1].strip()[:_REPLY_CONTEXT_MAX].strip()


def _turns(transcript: str) -> list[tuple[str, str]]:
    """`[(role, body)]` in transcript order.

    A line that starts a new `<role>:` opens a turn; anything else is a continuation of the
    current one (a multi-line turn is one statement). A6.6: inside a `user:`/`assistant:` dialogue
    a capitalised `Cons: ...` is a label, not a speaker, and stays in the current turn's body.
    Prose with no role prefix at all - a raw dialogue blob, or an ingested document - is read as
    the USER's own words: the alternative is silently dropping it, and this tier exists so nothing
    is dropped.
    """
    turns: list[tuple[str, str]] = []
    role, buf = "", []
    lines = (transcript or "").splitlines()
    #: A6.6: a `user:`/`assistant:` transcript is a dialogue with a KNOWN role vocabulary.
    #: In that shape a capitalised line (`Cons: ...`) is a markdown label or prose inside
    #: the current turn, not a named speaker; in a named-speaker transcript (`Caroline: ...`)
    #: it still is one. `has_ua` decides which reading applies.
    _ua_roles = _USER_ROLES | _ASSISTANT_ROLES
    has_ua = any((m := _TURN_RE.match(ln.strip())) is not None
                 and m.group(1).strip().lower() in _ua_roles for ln in lines)

    def flush() -> None:
        body = "\n".join(buf).strip()
        if role and body:
            turns.append((role, body))

    for raw in lines:
        line = raw.strip()
        if _HEADER_RE.match(line):
            continue
        mo = _TURN_RE.match(line)
        if mo:
            cand_role = mo.group(1).strip()
            cand_low = cand_role.lower()
            if cand_low not in _NON_SPEAKER_LABELS and (
                    not has_ua
                    or cand_low in (_USER_ROLES | _ASSISTANT_ROLES | _SKIP_ROLES)):
                flush()
                role, buf = cand_role, [mo.group(2)]
                continue
            # Not a header after all: a label (`Cons:`) or a named speaker inside a
            # `user:`/`assistant:` dialogue. Fall through so the line stays in this turn.
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
    # Two independent buffers. A speaker and an assistant turn can never share a chunk, but
    # an assistant reply must not force the SPEAKER buffer to flush either - otherwise every
    # short `user:` turn in a user/assistant session is split off around each reply, and one
    # session becomes a dozen one-line chunks. Speaker chunks therefore pack across the
    # whole session. Assistant chunks are capped at two per session: the first replies carry
    # the gist, and a verbose model must not flood the index with near-duplicate chunks.
    bufs: dict[str, str] = {"speaker": "", "context": ""}
    outs: dict[str, list[str]] = {"speaker": [], "context": []}
    prev_q = ""
    for role, body in _turns(text):
        kind = "context" if role.strip().lower() in _CONTEXT_ROLES else "speaker"
        #: A6.5: a speaker's answer carries the question it answers. The question is read from
        #: the turn's ORIGINAL text, before the carryover prefix is added, so a re-issued
        #: context cannot chain itself forward; and only a SPEAKER turn is prefixed, because a
        #: question attached to the assistant's own advice would look like the user's words.
        asked = _last_question(body)
        if kind == "speaker" and prev_q and prev_q.lower() not in body.lower():
            body = f"(re: {prev_q}) {body}"
        prev_q = asked
        prefix = (f"[Date: {day}] [Assistant context] " if kind == "context"
                  else f"[Date: {day} | Speaker: {role}] ")
        #: Every piece repeats the role tag: a piece must never be separable from who said it,
        #: and packing is what decides whether two pieces share a chunk, not the split.
        room = limit - len(prefix)
        if room < 1:                 # a limit shorter than the tag cannot hold a tagged chunk
            continue
        for piece in _split_body(body, room):
            if kind == "context" and len(outs["context"]) >= 2:
                break                # the per-session assistant cap is already met
            item = prefix + piece
            if bufs[kind] and len(bufs[kind]) + 1 + len(item) <= limit:
                bufs[kind] = f"{bufs[kind]}\n{item}"
                continue
            if bufs[kind]:
                outs[kind].append(bufs[kind])
            bufs[kind] = item
    if bufs["speaker"]:
        outs["speaker"].append(bufs["speaker"])
    if bufs["context"] and len(outs["context"]) < 2:
        outs["context"].append(bufs["context"])
    chunks = outs["speaker"] + outs["context"]
    return [c for c in chunks if c.strip()]
