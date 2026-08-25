#!/usr/bin/env python3
"""The host-adapter contract - one shape for "where does a session come from".

Support for a new agent used to be spread across three modules that did not know about each
other. Discovery was a hardcoded registry in `watch.known_targets()`. Normalisation was a
heuristic inside `ingest._flatten_agent_jsonl` that sniffed the payload shape. Cursoring was a
watermark dict keyed by path. Install lived in `install.py` and knew only about Claude Code,
and there was no way at all to ask "is this host wired up?" or to undo it.

So adding a host meant editing three files, and *removing* one meant knowing which three. This
module states the five things a host adapter has to answer, once:

* `discover()`      - where this host's sessions live on **this** machine, or nothing.
* `read(cursor)`    - the events since `cursor`, plus the cursor to pass next time. Cursors are
                      opaque to callers and comparable only to themselves.
* `normalize(raw)`  - the host's own on-disk shape turned into `schemas.EpisodeEvent`s. This is
                      the only place a host's quirks are allowed to exist.
* `install_status()`- wired / not wired / not installable, with a reason a human can act on.
* `uninstall()`     - undo exactly what install did, and nothing else. Reversibility is what
                      makes trying a memory system a low-risk decision.

Four adapters ship: Claude Code, Codex, Cursor and a generic JSONL fallback. The last one
matters most - it is the honest answer to "my agent is not on your list", and it is what any
new host starts as before it earns a specialised adapter.

**Everything here is offline.** Discovery reads the filesystem, normalisation is pure, and the
suite drives all four from recorded fixtures. No account, no network, no live agent - a
contributor can add an adapter and prove it works without installing the agent it is for.

    from nevertwice import hosts
    for adapter in hosts.registry():
        print(adapter.name, adapter.install_status()["state"])

Standard library only.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import memory_hook as m         # noqa: E402

SCHEMA_VERSION = 1

#: What `install_status()["state"]` may be. `unavailable` is not a failure - it is the honest
#: answer on a machine where the host simply is not installed, and it is different from
#: `not_wired`, which means the host is here and Nevertwice is not attached to it.
STATES = ("wired", "not_wired", "unavailable")

#: Cap on how much of one transcript is read. A rollout log can be tens of megabytes, and the
#: measured failure was a single 25KB scaffolding line eating the whole budget.
MAX_BYTES = m.env_int("NEVERTWICE_HOST_MAX_BYTES", 2_000_000)


# ── the normalized event ────────────────────────────────────────────────

def event(hook_event_name: str, **fields) -> dict:
    """One normalized episode event, in the shape `schemas.EpisodeEvent` declares.

    Every adapter emits through here rather than building dicts, so "equivalent normalized
    events" is a property of one function instead of a coincidence across four.
    """
    out = {"hook_event_name": hook_event_name}
    for key, value in fields.items():
        if value is not None and value != "":
            out[key] = value
    return out


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("input_text") or ""))
            elif item is not None:
                parts.append(str(item))
        return " ".join(p for p in parts if p).strip()
    return "" if value is None else str(value)


def _read_capped(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(MAX_BYTES)
    except OSError:
        return ""


def _jsonl(text: str):
    """Yield the parsed objects of a JSONL blob, skipping anything that is not one.

    A truncated final line is normal - `_read_capped` cuts at a byte boundary - so a parse
    failure is a skip, never an error. Refusing the whole file because its tail was clipped
    would drop the entire session for the sake of its last turn.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


# ── the contract ────────────────────────────────────────────────────────

class HostAdapter:
    """The five questions. Subclasses override what differs and inherit the rest.

    Deliberately a base class rather than a Protocol: three of the five answers are identical
    for every file-backed host, and the point of the contract is that a new adapter is a small
    diff rather than a re-implementation.
    """

    name = "abstract"
    #: A human-facing label for the log format, used in status output.
    fmt = ""
    #: Globs applied inside each discovered directory.
    globs: tuple = ("*.jsonl",)
    #: True when this host is captured by a hook and must NOT also be swept - double-mining
    #: the same session is how one conversation became two contradictory notes.
    hook_captured = False

    # -- discovery -------------------------------------------------------

    def roots(self) -> list[Path]:
        """Candidate directories, whether or not they exist."""
        return []

    def discover(self) -> list[Path]:
        """The directories that actually exist here. Empty is a normal answer."""
        return [r for r in self.roots() if r.is_dir()]

    def sessions(self) -> list[Path]:
        """Every transcript file this host has, newest last."""
        found: list[Path] = []
        for root in self.discover():
            for glob in self.globs:
                found += [p for p in root.rglob(glob) if p.is_file()]
        return sorted(set(found), key=lambda p: (_mtime(p), str(p)))

    # -- cursoring -------------------------------------------------------

    def read(self, cursor: dict | None = None, *, limit: int = 50) -> dict:
        """Events since `cursor`, and the cursor to pass next time.

        The cursor is `{path: (mtime, size)}`. Not a timestamp: clocks jump, and a file
        rewritten in place within the same second is invisible to a mtime-only cursor. Not a
        byte offset either, because these hosts rewrite whole files rather than appending.
        It is opaque to callers by contract, so it can change shape without breaking them.
        """
        cursor = dict(cursor or {})
        events, seen = [], {}
        for path in self.sessions()[-limit:]:
            key = str(path)
            stamp = [_mtime(path), _size(path)]
            seen[key] = stamp
            if cursor.get(key) == stamp:
                continue                      # unchanged since the last read
            events += self.normalize(_read_capped(path), source=path)
        # Files that vanished drop out of the cursor rather than being remembered forever.
        return {"events": events, "cursor": seen,
                "read": len(seen), "new": len([k for k, v in seen.items()
                                               if cursor.get(k) != v])}

    # -- normalization ---------------------------------------------------

    def normalize(self, raw: str, *, source: Path | None = None) -> list[dict]:
        raise NotImplementedError

    # -- install / uninstall ---------------------------------------------

    def install_status(self) -> dict:
        """`{state, detail, evidence}`. Never raises: a status call is what you run *because*
        something is wrong, so it has to work on a broken install."""
        if not self.discover():
            return {"host": self.name, "state": "unavailable",
                    "detail": f"no {self.name} session directory on this machine",
                    "evidence": [str(r) for r in self.roots()]}
        return {"host": self.name, "state": "not_wired",
                "detail": (f"{self.name} sessions are here and are swept by "
                           f"`nevertwice-watch`; nothing to install"),
                "evidence": [str(d) for d in self.discover()]}

    def uninstall(self, *, dry_run: bool = True) -> dict:
        """Undo exactly what `install` did. A sweep-only host has nothing to undo, and saying
        so is better than pretending an action happened."""
        return {"host": self.name, "ok": True, "changed": [],
                "detail": f"{self.name} is sweep-only - nothing was installed, nothing to undo",
                "dry_run": dry_run}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ── Claude Code ─────────────────────────────────────────────────────────

class ClaudeCodeAdapter(HostAdapter):
    """Claude Code writes `~/.claude/projects/<slug>/<session>.jsonl`, and Nevertwice attaches
    to it through hooks in `~/.claude/settings.json` rather than by sweeping.

    `hook_captured` is the load-bearing flag: sweeping this host *as well* would mine every
    session twice and produce two notes for one conversation. The adapter still normalises the
    files, because that is what makes the format testable from a fixture and what a migration
    or a backfill needs.
    """

    name = "claude-code"
    fmt = "JSONL, one message per line, content under `message`"
    globs = ("*.jsonl",)
    hook_captured = True

    def roots(self) -> list[Path]:
        env = os.environ.get("NEVERTWICE_CLAUDE_PROJECTS") or os.environ.get("CLAUDE_PROJECTS_ROOT")
        return [Path(env)] if env else [Path.home() / ".claude" / "projects"]

    def settings_path(self) -> Path:
        return Path(os.environ.get("NEVERTWICE_CLAUDE_SETTINGS")
                    or Path.home() / ".claude" / "settings.json")

    def normalize(self, raw: str, *, source: Path | None = None) -> list[dict]:
        out = []
        session = source.stem if source else ""
        cwd = ""
        for obj in _jsonl(raw):
            cwd = obj.get("cwd") or cwd
            message = obj.get("message") if isinstance(obj.get("message"), dict) else obj
            role = message.get("role") or obj.get("type")
            body = _text(message.get("content"))
            if role == "user" and body:
                out.append(event("UserPromptSubmit", session_id=session, cwd=cwd, prompt=body))
            elif role == "assistant" and body:
                out.append(event("AssistantMessage", session_id=session, cwd=cwd, prompt=body))
            for call in _tool_calls(message.get("content")):
                out.append(event("PreToolUse", session_id=session, cwd=cwd,
                                 tool_name=call["name"], tool_input=call["input"]))
        return out

    def install_status(self) -> dict:
        settings = self.settings_path()
        if not self.discover() and not settings.exists():
            return {"host": self.name, "state": "unavailable",
                    "detail": "Claude Code is not installed for this user",
                    "evidence": [str(r) for r in self.roots()] + [str(settings)]}
        hooks, foreign = _claude_hooks(settings)
        if hooks:
            return {"host": self.name, "state": "wired",
                    "detail": f"{len(hooks)} Nevertwice hook(s) in {settings}",
                    "evidence": sorted(hooks)}
        if foreign:
            # A hand-rolled flat copy under ~/.claude/scripts is a supported way to run this,
            # and telling its owner to "run install.py" would be wrong - it would repoint a
            # deployment they chose. Report what is actually there instead.
            return {"host": self.name, "state": "not_wired",
                    "detail": (f"{len(foreign)} hook(s) in {settings} run a copy of this "
                               f"engine from outside the installed package. Nevertwice did "
                               f"not wire them and will not remove them; `python install.py` "
                               f"would add a second, packaged hook alongside."),
                    "evidence": sorted(foreign)}
        return {"host": self.name, "state": "not_wired",
                "detail": f"Claude Code is here but no Nevertwice hook is in {settings}; "
                          f"run `python install.py`",
                "evidence": [str(settings)]}

    def uninstall(self, *, dry_run: bool = True) -> dict:
        """Remove only the hook entries whose command mentions this package.

        Rewriting `settings.json` wholesale would be the easy implementation and would eat
        every hook the user configured themselves. The uninstall a person can trust is the one
        that touches exactly what the install added.
        """
        settings = self.settings_path()
        if not settings.exists():
            return {"host": self.name, "ok": True, "changed": [],
                    "detail": "no Claude Code settings file - nothing to undo",
                    "dry_run": dry_run}
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"host": self.name, "ok": False, "changed": [],
                    "detail": f"could not read {settings}: {exc}", "dry_run": dry_run}

        removed = []
        hooks = data.get("hooks")
        if isinstance(hooks, dict):
            for event_name, groups in list(hooks.items()):
                if not isinstance(groups, list):
                    continue
                kept_groups = []
                for group in groups:
                    entries = group.get("hooks") if isinstance(group, dict) else None
                    if not isinstance(entries, list):
                        kept_groups.append(group)
                        continue
                    keep = [e for e in entries if not _is_ours(e)]
                    removed += [f"{event_name}: {e.get('command', '')}"
                                for e in entries if _is_ours(e)]
                    if keep:
                        kept_groups.append({**group, "hooks": keep})
                if kept_groups:
                    hooks[event_name] = kept_groups
                else:
                    hooks.pop(event_name, None)
            if not hooks:
                data.pop("hooks", None)

        if removed and not dry_run:
            backup = settings.with_suffix(".json.nevertwice-backup")
            backup.write_text(settings.read_text(encoding="utf-8"), encoding="utf-8")
            m.write_atomic(settings, json.dumps(data, indent=2) + "\n")
            return {"host": self.name, "ok": True, "changed": [str(settings), str(backup)],
                    "detail": f"removed {len(removed)} hook entry(ies); "
                              f"previous settings kept at {backup.name}",
                    "removed": removed, "dry_run": False}
        return {"host": self.name, "ok": True,
                "changed": [] if dry_run else [],
                "detail": (f"would remove {len(removed)} hook entry(ies)" if removed
                           else "no Nevertwice hook entries to remove"),
                "removed": removed, "dry_run": dry_run}


#: The exact marker `install.py` uses, and for its reason: a **path suffix**, never a bare
#: filename. A filename match would also claim a hand-rolled `~/.claude/scripts/memory_hook.py`
#: - which is a real deployment shape, the one this project's own author runs - and uninstall
#: would then delete a hook it never installed. Being narrow here means status, install and
#: uninstall all answer with the same definition of "ours".
OUR_HOOK_SUFFIXES = ("nevertwice/memory_hook.py", "nevertwice/mcp_server.py")

#: Any command that runs a script by one of our names, wherever it lives. Used only to tell a
#: *foreign or hand-rolled copy* apart from "nothing is wired at all", never to remove anything.
OUR_SCRIPT_NAMES = ("memory_hook.py", "mcp_server.py")


def _command(entry) -> str:
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("command", "")).replace("\\", "/").lower()


def _is_ours(entry) -> bool:
    cmd = _command(entry)
    return any(suffix in cmd for suffix in OUR_HOOK_SUFFIXES)


def _is_a_foreign_copy(entry) -> bool:
    """Runs one of our scripts, but not from the installed package."""
    cmd = _command(entry)
    return (not _is_ours(entry)) and any(name in cmd for name in OUR_SCRIPT_NAMES)


def _claude_hooks(settings: Path) -> tuple[list[str], list[str]]:
    """(ours, foreign copies of our scripts) - the two are never conflated."""
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []
    ours, foreign = [], []
    for event_name, groups in (data.get("hooks") or {}).items():
        for group in groups if isinstance(groups, list) else []:
            for entry in (group.get("hooks") or []) if isinstance(group, dict) else []:
                label = f"{event_name}: {entry.get('command', '')}"
                if _is_ours(entry):
                    ours.append(label)
                elif _is_a_foreign_copy(entry):
                    foreign.append(label)
    return ours, foreign


def _tool_calls(content) -> list[dict]:
    """Tool invocations inside a message body, in either of the two shapes hosts use."""
    calls = []
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type") in ("tool_use", "function_call"):
            calls.append({"name": str(item.get("name") or ""),
                          "input": item.get("input") if isinstance(item.get("input"), dict)
                          else {"arguments": _text(item.get("arguments"))}})
    return calls


# ── Codex CLI ───────────────────────────────────────────────────────────

class CodexAdapter(HostAdapter):
    """Codex writes rollout JSONL under `~/.codex`, each line `{timestamp, type, payload}`.

    One `session_meta` line is 10-25KB of scaffolding. Treating the file as flat text let that
    single line eat the whole truncation budget and mine zero real content - measured on a
    57MB corpus. Normalising per line rather than per file is what fixes that, and it is why
    normalisation belongs in an adapter instead of in a text heuristic.
    """

    name = "codex"
    fmt = "JSONL rollout, `{timestamp, type, payload}` per line"
    globs = ("*.jsonl",)

    def roots(self) -> list[Path]:
        env = os.environ.get("NEVERTWICE_CODEX_HOME")
        base = Path(env) if env else Path.home() / ".codex"
        return [base / "sessions", base / "history"]

    def normalize(self, raw: str, *, source: Path | None = None) -> list[dict]:
        out = []
        session = source.stem if source else ""
        cwd = ""
        for obj in _jsonl(raw):
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
            ptype = payload.get("type") or obj.get("type")
            if ptype in ("session_meta", "turn_context"):
                cwd = payload.get("cwd") or payload.get("workdir") or cwd
                continue                      # scaffolding, never content
            role = payload.get("role")
            body = _text(payload.get("content"))
            if role == "user" and body:
                out.append(event("UserPromptSubmit", session_id=session, cwd=cwd, prompt=body))
            elif role == "assistant" and body:
                out.append(event("AssistantMessage", session_id=session, cwd=cwd, prompt=body))
            elif ptype == "function_call":
                out.append(event("PreToolUse", session_id=session, cwd=cwd,
                                 tool_name=str(payload.get("name") or ""),
                                 tool_input={"arguments": _text(payload.get("arguments"))}))
            elif ptype in ("function_call_output", "tool_result"):
                result = _text(payload.get("output") or payload.get("result")
                               or payload.get("content"))
                if result:
                    out.append(event("PostToolUse", session_id=session, cwd=cwd,
                                     tool_name=str(payload.get("name") or ""),
                                     tool_input={"output": result}))
        return out


# ── Cursor ──────────────────────────────────────────────────────────────

class CursorAdapter(HostAdapter):
    """Cursor keeps its chat in a `state.vscdb` SQLite blob, not in files a sweep can read.

    So this adapter is honest about what it can and cannot do. It discovers the workspace
    storage, reports `unavailable` with the reason and the export path rather than pretending,
    and normalises the **exported** JSON that `docs/INTEGRATIONS.md` tells people to produce -
    which is the format a fixture can actually pin. An adapter that quietly returned nothing
    would look identical to one that was working.
    """

    name = "cursor"
    fmt = "exported chat JSON (`{messages: [{role, content}]}`)"
    globs = ("*.json",)

    def roots(self) -> list[Path]:
        env = os.environ.get("NEVERTWICE_CURSOR_EXPORT")
        if env:
            return [Path(env)]
        home = Path.home()
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        elif sys.platform == "darwin":
            base = home / "Library" / "Application Support"
        else:
            base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        return [base / "Cursor" / "User" / "workspaceStorage"]

    def normalize(self, raw: str, *, source: Path | None = None) -> list[dict]:
        session = source.stem if source else ""
        try:
            data = json.loads(raw)
        except ValueError:
            return []
        cwd = str(data.get("workspace") or data.get("cwd") or "")
        messages = data.get("messages")
        if not isinstance(messages, list):
            messages = data.get("conversation") if isinstance(data.get("conversation"), list) else []
        out = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role") or message.get("type")
            body = _text(message.get("content") or message.get("text"))
            if role in ("user", "human") and body:
                out.append(event("UserPromptSubmit", session_id=session, cwd=cwd, prompt=body))
            elif role in ("assistant", "ai", "bot") and body:
                out.append(event("AssistantMessage", session_id=session, cwd=cwd, prompt=body))
            for call in _tool_calls(message.get("content")):
                out.append(event("PreToolUse", session_id=session, cwd=cwd,
                                 tool_name=call["name"], tool_input=call["input"]))
        return out

    def install_status(self) -> dict:
        found = self.discover()
        if os.environ.get("NEVERTWICE_CURSOR_EXPORT") and found:
            return {"host": self.name, "state": "not_wired",
                    "detail": "an export directory is configured and will be swept",
                    "evidence": [str(d) for d in found]}
        return {"host": self.name, "state": "unavailable",
                "detail": ("Cursor stores chat in a state.vscdb SQLite blob, which a file sweep "
                           "cannot read. Export the chat to JSON and point "
                           "NEVERTWICE_CURSOR_EXPORT at the directory, or use the MCP server "
                           "(nevertwice-mcp), which needs no sweep at all."),
                "evidence": [str(d) for d in found] or [str(r) for r in self.roots()]}


# ── generic JSONL ───────────────────────────────────────────────────────

class GenericJsonlAdapter(HostAdapter):
    """The answer to "my agent is not on your list".

    It accepts any JSONL whose lines carry a role and some content, under any of the key names
    the ecosystem actually uses, and it is what every new host is before it earns a
    specialised adapter. Being explicit about the fallback is what stops the specialised
    adapters from slowly growing into one.
    """

    name = "generic-jsonl"
    fmt = "any JSONL with a role and content per line"
    globs = ("*.jsonl",)

    ROLE_KEYS = ("role", "speaker", "author", "from", "type")
    BODY_KEYS = ("content", "text", "message", "body", "value")

    def roots(self) -> list[Path]:
        env = os.environ.get("NEVERTWICE_GENERIC_JSONL")
        return [Path(p) for p in env.split(os.pathsep) if p.strip()] if env else []

    def normalize(self, raw: str, *, source: Path | None = None) -> list[dict]:
        session = source.stem if source else ""
        out = []
        for obj in _jsonl(raw):
            inner = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
            role = next((str(inner[k]).lower() for k in self.ROLE_KEYS if inner.get(k)), "")
            body = ""
            for key in self.BODY_KEYS:
                if key in inner:
                    body = _text(inner[key])
                    if body:
                        break
            if not body:
                continue
            cwd = str(inner.get("cwd") or inner.get("workspace") or "")
            if role in ("user", "human", "prompt"):
                out.append(event("UserPromptSubmit", session_id=session, cwd=cwd, prompt=body))
            elif role in ("assistant", "ai", "bot", "model"):
                out.append(event("AssistantMessage", session_id=session, cwd=cwd, prompt=body))
        return out


# ── the registry ────────────────────────────────────────────────────────

_ADAPTERS = (ClaudeCodeAdapter, CodexAdapter, CursorAdapter, GenericJsonlAdapter)


def registry() -> list[HostAdapter]:
    return [cls() for cls in _ADAPTERS]


def get(name: str) -> HostAdapter | None:
    return next((a for a in registry() if a.name == name), None)


def status_report() -> dict:
    """Every host, its state and what to do about it - the read-only half of the contract."""
    hosts = [a.install_status() for a in registry()]
    return {"schema_version": SCHEMA_VERSION,
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "hosts": hosts,
            "counts": {state: sum(1 for h in hosts if h["state"] == state)
                       for state in STATES}}


def main() -> None:
    argv = sys.argv[1:]
    if "--json" in argv:
        print(json.dumps(status_report(), indent=2, ensure_ascii=False))
        return
    report = status_report()
    print(f"\n  Host adapters - {report['generated']}\n  " + "-" * 62)
    for host in report["hosts"]:
        mark = {"wired": "[wired]   ", "not_wired": "[not wired]",
                "unavailable": "[absent]  "}[host["state"]]
        print(f"  {mark} {host['host']}")
        print(f"              {host['detail']}")
    print()


if __name__ == "__main__":
    main()
