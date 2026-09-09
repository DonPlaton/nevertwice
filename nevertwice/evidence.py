#!/usr/bin/env python3
"""Evidence spans - the verbatim transcript lines a note came from, chosen by alignment (J1).

A typed note keeps the lesson and drops the fact: the extractor writes "the client retries only
on 5xx" as a decision, and the literal sentence it came from - with its number, its flag name,
its exact wording - is gone. On the frontier stand that made our own extractor the worst arm
(accuracy 0.013 where Mem0's sentence-keeping pipeline reads 0.333), and on the as-of stand a
marker the note paraphrased ("gates the new UI" against a title that says "gates new ui") reads
as a memory failure.

This module attaches one to three **verbatim** lines of the source transcript to each note,
chosen by **alignment** - stemmed-token overlap between the note's title, description and
prevention and every line of the transcript. No second LLM call, so a span cannot be
hallucinated; a span is a substring of what the session actually said. Spans are written into
the note (frontmatter `evidence`, a `## Evidence` block) and read back by `api.recall`,
`api.as_of` and `api.format_note`. They are deliberately **not** embedded and **not** indexed:
the ranker is untouched by construction, and the cost caps in `.loop/GOAL-CLOSE.md` measure
that anyway.

The engine module is not modified: attachment runs as a post-step of `api.capture_session`,
after `process_session` has written the notes and the session note that links them. That keeps
the evidence register's closure small - a change to `memory_hook.py` withdraws every retrieval
number in the repository, and the only numbers this layer can move are the ones its stands
re-measure.

    NEVERTWICE_EVIDENCE_SPANS        spans per note (default 1; 0 disables the layer)
    NEVERTWICE_EVIDENCE_SPAN_CHARS   longest span kept, verbatim prefix at a word boundary (240)
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m  # noqa: E402

#: One span by default. The smoke run of 2026-09-10 with two spans put the supersession stand at
#: 485 characters per query on three cases, over the cost cap of Mem0's 457; the best-aligned
#: line is what carries the literal value, and a second one is an option, not the default.
MAX_SPANS = m.env_int("NEVERTWICE_EVIDENCE_SPANS", 1)
SPAN_CHARS = m.env_int("NEVERTWICE_EVIDENCE_SPAN_CHARS", 240)
#: A line qualifies only when it shares at least this many stems with the note AND at least
#: this fraction of the note's stems - the two floors written in the ledger before any run.
MIN_SHARED = 2
MIN_FRACTION = 0.25
#: Lines shorter than this carry no fact worth quoting ("ok", "done", a lone path).
MIN_LINE_CHARS = 12
#: The transcript header `process_session` prepends, and turn labels a hook transcript carries.
_HEADER_RE = re.compile(r"^(Working directory|Trigger|Directory):", re.IGNORECASE)
_ROLE_RE = re.compile(r"^(user|assistant|system|tool|human|ai)\s*[:>-]\s*", re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*(```|~~~|---+|\*\*\*+|===+)\s*$")

BLOCK_HEADING = "## Evidence"

#: Running cost of the layer in this process - the stands read it for the latency cap.
STATS = {"sessions": 0, "notes": 0, "spans": 0, "ms": 0.0}


def enabled() -> bool:
    return MAX_SPANS > 0 and SPAN_CHARS > 0


# ── alignment ─────────────────────────────────────────────────────────────────────────────

def candidate_lines(text: str) -> list[str]:
    """The transcript lines a span may be quoted from: non-empty, long enough to carry a fact,
    not the ingest header, not a fence. A turn label ("user: ...") is stripped so the quote is
    what was said, not who said it."""
    out = []
    for raw in (text or "").splitlines():
        ln = raw.strip()
        if not ln or _FENCE_RE.match(ln) or _HEADER_RE.match(ln):
            continue
        ln = _ROLE_RE.sub("", ln, count=1).strip()
        if len(ln) >= MIN_LINE_CHARS:
            out.append(ln)
    return out


def _cut(line: str, max_chars: int) -> str:
    """A verbatim prefix of the line, cut at a word boundary. Never an ellipsis: the span is
    quoted as evidence, and evidence does not get an editor's mark."""
    if len(line) <= max_chars:
        return line
    head = line[:max_chars]
    sp = head.rfind(" ")
    return (head[:sp] if sp >= max_chars // 2 else head).rstrip(" ,;:")


def align(fields: str, lines: list[str], *, max_spans: int | None = None,
          max_chars: int | None = None, tokens=None,
          line_tokens: list[set] | None = None) -> list[str]:
    """The transcript lines that best restate the note, best first.

    `fields` is the note's title, description and prevention joined; `lines` the output of
    `candidate_lines`. Score = shared stems (the engine's own tokenizer, stop words out, stems
    in); ties go to the earlier line, then the shorter one. A line below the two floors
    (`MIN_SHARED`, `MIN_FRACTION`) is not a span at all - better no evidence than a line that
    merely shares a word. `line_tokens` lets a caller tokenise the transcript once for many
    notes. Duplicate lines collapse to one span."""
    max_spans = MAX_SPANS if max_spans is None else max_spans
    max_chars = SPAN_CHARS if max_chars is None else max_chars
    if max_spans <= 0 or not lines:
        return []
    tok = tokens or m._token_list
    note = set(tok(fields or ""))
    if not note:
        return []
    need = max(MIN_SHARED, int(MIN_FRACTION * len(note) + 0.999))
    if line_tokens is None:
        line_tokens = [set(tok(ln)) for ln in lines]
    scored = []
    for i, (ln, lt) in enumerate(zip(lines, line_tokens)):
        shared = len(note & lt)
        if shared >= need:
            scored.append((-shared, i, len(ln), ln))
    scored.sort()
    out: list[str] = []
    seen: set[str] = set()
    for _, _, _, ln in scored:
        span = m.redact_secrets(_cut(ln, max_chars))
        if span and span not in seen:
            seen.add(span)
            out.append(span)
        if len(out) >= max_spans:
            break
    return out


# ── the note on disk ──────────────────────────────────────────────────────────────────────

def _coerce(v) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if isinstance(x, (str, int, float)) and str(x).strip()]
    return []


def read(path: Path | str) -> list[str]:
    """The spans a note carries, from its frontmatter only (a header read, as `as_of` does)."""
    try:
        return _coerce(m._read_frontmatter_file(Path(path)).get("evidence"))
    except Exception:                                    # noqa: BLE001 - a read surface never raises
        return []


def note_path(stem: str, ntype: str) -> Path | None:
    """Where a typed note lives now: its live folder, else `Superseded/`, else `Archive/`
    (and `Archive/Superseded/`) - the same places `as_of` walks."""
    folder = m.TYPE_FOLDER.get(ntype or "")
    if not folder or not stem:
        return None
    base = m.VAULT / folder
    for sub in ("", "Superseded", "Archive", "Archive/Superseded", "Quarantine"):
        p = base / sub / f"{stem}.md" if sub else base / f"{stem}.md"
        if p.exists():
            return p
    return None


def for_hit(hit: dict) -> list[str]:
    """Spans for a recall-shaped dict (`stem`, `ntype`)."""
    p = note_path(str(hit.get("stem") or ""), str(hit.get("ntype") or ""))
    return read(p) if p else []


def render_block(spans: list[str]) -> list[str]:
    """The body block: a heading and one list item per span. List items start with `-`, which
    the body parser (`_parse_note_body`) never mistakes for a description - so a note whose
    extraction had no description does not acquire a quote as one, and a rebuilt index never
    embeds a span."""
    return [BLOCK_HEADING, *(f'- "{s}"' for s in spans)]


def _strip_block(lines: list[str]) -> list[str]:
    """The body without any previous `## Evidence` block (heading and its list items)."""
    out, skipping = [], False
    for ln in lines:
        s = ln.strip()
        if s == BLOCK_HEADING:
            skipping = True
            continue
        if skipping:
            if s.startswith("- "):
                continue
            if not s:
                continue                       # the blank line that closed the block
            skipping = False
        out.append(ln)
    return out


def stamp(path: Path | str, spans: list[str]) -> bool:
    """Write `spans` into a note: frontmatter `evidence` and a `## Evidence` block placed
    before the `**Project:**` line (after the lesson, before the provenance). Replaces a
    previous block. Atomic write. False when the file cannot be read or has no frontmatter."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if not text.startswith("---"):
        return False
    spans = [str(s) for s in spans if str(s).strip()]
    text = m._stamp_frontmatter(text, {"evidence": spans})
    end = text.find("\n---", 3)
    head, body = text[:end + 4], text[end + 4:]
    lines = _strip_block(body.split("\n"))
    if spans:
        block = render_block(spans)
        anchor = next((i for i, ln in enumerate(lines) if ln.startswith("**Project:**")), None)
        if anchor is None:
            lines = lines + [""] + block
        else:
            # keep exactly one blank line on each side of the block
            before = lines[:anchor]
            while before and not before[-1].strip():
                before.pop()
            lines = before + ["", *block, ""] + lines[anchor:]
    try:
        m.write_atomic(p, head + "\n".join(lines))
    except OSError:
        return False
    return True


# ── the post-step of capture_session ──────────────────────────────────────────────────────

def _session_note(session_id: str) -> Path | None:
    """The session note carrying `session_id`. Its stem ends in the engine's 8-char identity
    hash, so the glob is narrow - a 940-session ingest must not rescan every header per
    capture. The frontmatter is still checked (a legacy note stores the first 8 characters)."""
    d = m.VAULT / "Sessions"
    if not d.exists():
        return None
    try:
        cands = sorted(d.glob(f"*-session-{m._sid8(session_id)}*.md"))
    except Exception:                                    # noqa: BLE001 - an odd id falls back to the scan
        cands = []
    if not cands:
        cands = sorted(d.glob("*.md"))
    for cand in cands:
        fm = m._read_frontmatter_file(cand)
        if fm.get("session_id") in (session_id, session_id[:8]):
            return cand
    return None


def _linked_typed_stems(session_note: Path) -> list[str]:
    try:
        text = session_note.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out, seen = [], set()
    for lk in re.findall(r"\[\[([^]|#]+)", text):
        lk = lk.strip()
        if lk in seen or not m.parse_typed_stem(lk):
            continue
        seen.add(lk)
        out.append(lk)
    return out


def attach_for_session(session_id: str, transcript_text: str, *, project: str | None = None,
                       only_stems: list[str] | None = None) -> dict:
    """Align every typed note this session wrote against its transcript and stamp the spans.

    The notes are found through the session note's wikilinks (the same links a reader follows
    from Index -> Context -> note), so nothing about the write path has to be known here. The
    transcript is scrubbed the way the extractor saw it (injected boilerplate out, secrets
    redacted) before any line can become a quote. Returns `{notes, stamped, spans, ms}`;
    best-effort - a note that cannot be read is skipped, never raised on."""
    t0 = time.perf_counter()
    res = {"notes": 0, "stamped": 0, "spans": 0, "ms": 0.0}
    if not enabled() or not (transcript_text or "").strip():
        return res
    stems = list(only_stems or [])
    if not stems:
        sn = _session_note(session_id)
        stems = _linked_typed_stems(sn) if sn else []
    if not stems:
        return res
    body = m.redact_secrets(m.strip_injected_boilerplate(transcript_text))
    lines = candidate_lines(body)
    if not lines:
        return res
    line_tokens = [set(m._token_list(ln)) for ln in lines]
    for stem in stems:
        parsed = m.parse_typed_stem(stem)
        if not parsed or (project and parsed.get("project") != m.slug_project(project)):
            continue
        p = note_path(stem, parsed["ntype"])
        if p is None:
            continue
        try:
            note_lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue
        title, desc, prevention = m._parse_note_body(note_lines)
        res["notes"] += 1
        spans = align(f"{title}\n{desc}\n{prevention}", lines, line_tokens=line_tokens)
        if spans and stamp(p, spans):
            res["stamped"] += 1
            res["spans"] += len(spans)
    res["ms"] = round((time.perf_counter() - t0) * 1000, 2)
    STATS["sessions"] += 1
    STATS["notes"] += res["notes"]
    STATS["spans"] += res["spans"]
    STATS["ms"] += res["ms"]
    return res
