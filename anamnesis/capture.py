#!/usr/bin/env python3
"""Generic capture — give ANY agent long-term memory, not just Claude Code.

Claude Code feeds sessions in through the hook; everyone else uses this. Collect an
agent's turns in a `MemorySession`, and on close the whole session is run through the
same extraction → Patterns/Mistakes/Decisions pipeline (via `api.capture_session`).
For OpenAI-style chat agents, the `capture_chat` decorator wires that up in one line.

    from anamnesis.capture import MemorySession, recall, remember

    # collect turns, extract once at the end
    with MemorySession(project="myproj", agent="my-bot") as mem:
        mem.log_user(prompt)
        mem.log_assistant(reply)
    # → salient lessons are now in memory; recall("…", "myproj") finds them

    # or decorate an existing chat function
    from anamnesis.capture import capture_chat
    @capture_chat(project="myproj", agent="my-bot")
    def chat(messages): ...
    chat([{ "role": "user", "content": "…" }])
    chat.memory.flush()        # extract what was learned
"""
import atexit
import functools
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api as _api
from api import recall, remember          # re-export the read/write helpers (convenience)

__all__ = ["MemorySession", "capture_chat", "auto_capture", "recall", "remember"]


def _text_of(content) -> str:
    """Best-effort plain text from a message content: str, an object with `.content`,
    a dict {'content': …}, or OpenAI-style list-of-parts [{'type':'text','text':…}]."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return _text_of(content.get("content") or content.get("text") or "")
    if isinstance(content, (list, tuple)):
        return " ".join(_text_of(p) for p in content).strip()
    inner = getattr(content, "content", None)
    return _text_of(inner) if inner is not None else str(content)


def _last_user(messages) -> str:
    """The latest user message text from an OpenAI-style messages list."""
    if isinstance(messages, str):
        return messages
    try:
        for msg in reversed(list(messages)):
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
            if role == "user":
                content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
                return _text_of(content)
    except (AttributeError, TypeError):
        pass
    return ""


class MemorySession:
    """Accumulate an agent's turns; extract memory from the whole session on close.

    `with MemorySession(...) as mem:` extracts on a clean exit (unless extract=False).
    `flush()` extracts on demand and returns api.capture_session's summary dict. The
    session is agent-agnostic — feed it turns from any framework or none."""

    def __init__(self, project: str | None = None, agent: str | None = None,
                 session_id: str | None = None, *, extract: bool = True,
                 auto_flush: bool = False):
        self.project = project
        self.agent = agent
        self.session_id = session_id
        self.extract = extract
        self.turns: list[tuple[str, str]] = []
        self.result: dict | None = None
        # auto_flush: mine whatever is buffered at interpreter exit, so an API agent never
        # has to remember to call flush() — capture becomes hands-off. flush() resets the
        # buffer, so an explicit flush at a conversation boundary makes the atexit one a no-op.
        if auto_flush:
            atexit.register(self._flush_quietly)

    def _flush_quietly(self) -> None:
        """flush(), but swallow everything — runs at interpreter shutdown where a raised
        exception (no LLM, lock busy) would be noise, not signal. Turns are kept on failure
        by flush() itself, so nothing is silently lost mid-run."""
        try:
            if self.turns:
                self.flush()
        except Exception:
            pass

    def log(self, role: str, content) -> "MemorySession":
        text = _text_of(content).strip()
        if text:
            self.turns.append((str(role), text))
        return self

    def log_user(self, content) -> "MemorySession":
        return self.log("user", content)

    def log_assistant(self, content) -> "MemorySession":
        return self.log("assistant", content)

    @property
    def transcript(self) -> str:
        return "\n\n".join(f"{role}: {text}" for role, text in self.turns)

    def flush(self) -> dict:
        """Extract from the collected turns, then RESET — so a later flush() does not
        re-extract the same turns and duplicate notes in the vault (audit 2026-06-18 CRIT).
        No-op (stored=False) when empty. If extraction fails — whether it raises (no LLM /
        lock busy) or returns stored=False without raising (malformed LLM JSON, a transient
        cloud error) — the turns are KEPT so the caller (or the atexit auto-flush) can retry
        without losing the conversation (code-review 2026-07, HIGH: the non-raising failure
        used to wipe the buffer with nothing left to retry)."""
        if not self.turns:
            self.result = {"stored": False, "reason": "no turns"}
            return self.result
        self.result = _api.capture_session(self.transcript, project=self.project,
                                           agent=self.agent, session_id=self.session_id)
        if self.result.get("stored"):
            self.turns = []      # consumed — a second flush must not re-mine the same turns
        return self.result

    def __enter__(self) -> "MemorySession":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # only auto-extract on a clean exit — a crashing agent shouldn't have its
        # half-session mined for lessons; the caller can still flush() explicitly.
        if self.extract and exc_type is None and self.turns:
            self.flush()
        return False


def capture_chat(project: str | None = None, agent: str | None = None, *,
                 session_id: str | None = None, auto_flush: bool = False):
    """Decorator for an OpenAI-style chat fn `f(messages, ...) -> reply`. Each call logs the
    latest user turn and the reply into a single shared `wrapper.memory` (a MemorySession).
    Call `wrapper.memory.flush()` at each conversation boundary to extract durable lessons —
    flush() resets the buffer, so the next conversation starts clean. For a short-lived
    script, pass `auto_flush=True` to mine whatever is buffered at process exit so you don't
    have to call anything (off by default — a long-running service should flush per
    conversation, not dump a whole day's turns as one session at shutdown). One shared
    session is deliberate (one decorated bot = one rolling memory); for concurrent or
    multi-user chats, give each conversation its own MemorySession. One line, no rewrite."""
    sess = MemorySession(project=project, agent=agent, session_id=session_id,
                         extract=False, auto_flush=auto_flush)

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(messages, *args, **kwargs):
            user = _last_user(messages)
            reply = fn(messages, *args, **kwargs)
            if user:
                sess.log("user", user)
            sess.log("assistant", _text_of(reply))
            return reply
        wrapper.memory = sess
        return wrapper

    return deco


# ── Drop-in proxy for OpenAI-style clients (the "magic" for API agents) ───────

def _reply_text(resp) -> str:
    """Best-effort assistant text from an OpenAI-style response — chat.completions
    (`.choices[0].message.content`) or the Responses API (`.output_text`). Anything we
    can't parse returns '' so capture is skipped, never wrong."""
    try:
        choices = getattr(resp, "choices", None)
        if choices is None and isinstance(resp, dict):
            choices = resp.get("choices")
        if choices:
            first = choices[0]
            msg = (first.get("message") if isinstance(first, dict)
                   else getattr(first, "message", None))
            if msg is not None:
                return _text_of(msg.get("content") if isinstance(msg, dict)
                                else getattr(msg, "content", ""))
    except (AttributeError, IndexError, TypeError, KeyError):
        pass
    ot = getattr(resp, "output_text", None)
    if isinstance(ot, str) and ot:
        return ot
    return _text_of(getattr(resp, "content", None) or "")


# The terminal call paths we intercept on an OpenAI-style client.
_CREATE_PATHS = {("chat", "completions", "create"), ("responses", "create")}


class _CapturingProxy:
    """Transparent attribute proxy over an OpenAI-style client. It intercepts only the
    `<client>.chat.completions.create(...)` and `<client>.responses.create(...)` calls —
    logging the user turn + the reply into a shared MemorySession, then returning the real
    response untouched. Every other attribute passes straight through. Capture is
    best-effort: any parsing error is swallowed, so the proxy can never break a real call."""
    __slots__ = ("_t", "_sess", "_path")

    def __init__(self, target, sess, path=()):
        object.__setattr__(self, "_t", target)
        object.__setattr__(self, "_sess", sess)
        object.__setattr__(self, "_path", path)

    def __getattr__(self, name):
        target = object.__getattribute__(self, "_t")
        sess = object.__getattribute__(self, "_sess")
        path = object.__getattribute__(self, "_path") + (name,)
        # `proxy.memory` → the MemorySession (for an explicit flush()). Only at the top
        # level, and only if the wrapped client has no real `.memory` — so we never
        # silently shadow a genuine client attribute (audit 2026-06-18).
        if (name == "memory" and not object.__getattribute__(self, "_path")
                and not hasattr(target, "memory")):
            return sess
        attr = getattr(target, name)
        if path in _CREATE_PATHS and callable(attr):
            return self._wrap_create(attr, sess)
        # descend through namespace objects toward a create() path. Gate on PATH being a
        # strict prefix of a create path — not on callability (some SDK resource objects
        # are callable, which the old check wrongly skipped).
        if any(len(cp) > len(path) and cp[:len(path)] == path for cp in _CREATE_PATHS):
            return _CapturingProxy(attr, sess, path)
        return attr

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_t"), name, value)

    @staticmethod
    def _wrap_create(create, sess):
        @functools.wraps(create)
        def wrapped(*args, **kwargs):
            resp = create(*args, **kwargs)
            try:
                msgs = (kwargs.get("messages") or kwargs.get("input")
                        or (args[0] if args else None))   # tolerate a positional messages arg
                if isinstance(msgs, str):
                    sess.log("user", msgs)
                elif msgs:
                    user = _last_user(msgs)
                    if user:
                        sess.log("user", user)
                sess.log("assistant", _reply_text(resp))
            except Exception:
                pass                            # capture must never break the real call
            return resp
        return wrapped


def auto_capture(client, project: str | None = None, agent: str | None = None, *,
                 session_id: str | None = None, auto_flush: bool = False):
    """Wrap an OpenAI-style client so every chat/responses call is captured automatically —
    no decorator, no per-call logging, no rewrite. Works with any client that exposes the
    OpenAI shape (`openai`, Azure OpenAI, Groq, Together, DeepSeek, Ollama's OpenAI-compat …):

        from openai import OpenAI
        from anamnesis.capture import auto_capture
        client = auto_capture(OpenAI(), project="myproj", agent="my-bot")
        client.chat.completions.create(model="gpt-4o", messages=[...])   # captured

    The wrapped client behaves exactly like the original — every attribute passes through;
    only the create() calls are observed. Call `client.memory.flush()` at a conversation
    boundary to extract lessons, or pass `auto_flush=True` for a short script to mine the
    buffer at process exit automatically."""
    sess = MemorySession(project=project, agent=agent, session_id=session_id,
                         extract=False, auto_flush=auto_flush)
    return _CapturingProxy(client, sess)
