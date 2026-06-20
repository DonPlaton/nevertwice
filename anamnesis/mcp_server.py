#!/usr/bin/env python3
"""MCP server exposing the memory system as native tools (audit I-8) — the step
that completes "memory for ANY agent": Cursor, Claude Desktop, Cline, Zed and any
other MCP client get search / remember / ingest as first-class tools, not a CLI
they have to shell out to.

Zero dependencies: a minimal, correct Model Context Protocol stdio server over
newline-delimited JSON-RPC 2.0 — no SDK to install, in keeping with the project's
local-first, install-nothing ethos.

Register it with an MCP client, e.g. Claude Desktop / Cursor config:

    {
      "mcpServers": {
        "anamnesis": {
          "command": "python",
          "args": ["/path/to/anamnesis/anamnesis/mcp_server.py"]
        }
      }
    }

Tools:
  memory_search   (query, project?, k?)                      — recall, read-only
  memory_remember (project, type, title, description?, …)    — write a lesson
  memory_ingest   (project, text, agent?)                    — extract+store a chat

stdout carries ONLY JSON-RPC: every library print is redirected to stderr so it
can never corrupt the protocol stream.
"""
import io
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

# ── stdout discipline: protocol on the real stdout, everything else on stderr ──
_REAL_STDOUT = sys.stdout
for _stream in (_REAL_STDOUT, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
sys.stdout = sys.stderr        # any stray print() from imported modules → stderr

sys.path.insert(0, str(Path(__file__).parent))
import memory_hook as m          # noqa: E402
import memory_search             # noqa: E402  (search_core, shared ranker)
import remember as _remember     # noqa: E402  (do_remember validation/lock path)

SERVER_NAME = "anamnesis"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL = "2025-06-18"

TOOLS = [
    {
        "name": "memory_search",
        "description": ("Search long-term memory for relevant past lessons "
                        "(mistakes, patterns, decisions). Use BEFORE starting a "
                        "task to recall what was learned. Read-only."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to recall."},
                "project": {"type": "string",
                            "description": "Limit to one project (optional)."},
                "k": {"type": "integer", "description": "Max results (default 8)."},
                "rerank": {"type": "boolean",
                           "description": "Cloud-judge rerank for higher precision "
                                          "(slower; optional)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_remember",
        "description": ("Persist a lesson to long-term memory so future sessions "
                        "recall it. Use after learning something worth keeping."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "type": {"type": "string", "enum": list(m.TYPED_TYPES),
                         "description": "pattern | mistake | decision"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "prevention": {"type": "string",
                               "description": "For a mistake: how to avoid it."},
                "tags": {"type": "string", "description": "Comma-separated."},
                "supersedes": {"type": "string",
                               "description": "Title of a note this replaces."},
            },
            "required": ["project", "type", "title"],
        },
    },
    {
        "name": "memory_ingest",
        "description": ("Extract and store knowledge from a finished conversation "
                        "or notes blob (runs the same extraction pipeline as the "
                        "session hook). Use to teach memory from outside Claude Code."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "text": {"type": "string", "description": "Transcript / notes."},
                "agent": {"type": "string", "description": "Agent label (optional)."},
            },
            "required": ["project", "text"],
        },
    },
    {
        "name": "memory_entities",
        "description": ("Faceted view of memory by entity (tools, concepts, files). With "
                        "`entity`: every lesson tagged with it, plus related entities. "
                        "Without: the entity graph (most-connected entities and their "
                        "co-occurring neighbours). Read-only."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string",
                           "description": "One entity to facet by (optional)."},
                "project": {"type": "string", "description": "Limit to one project (optional)."},
                "k": {"type": "integer", "description": "Max results (default 12)."},
            },
        },
    },
]


# ── tool implementations ──────────────────────────────────────────────
# Each returns (text, is_error). Success/failure is decided STRUCTURALLY by the
# tool's own control flow — never guessed from the text (audit H8): the round-1
# code flagged isError when the text merely contained "failed"/"error", so a
# perfectly-good note titled "failed deployment pattern" was returned as an error.
# Printing is forbidden here (stdout carries only JSON-RPC).

def _tool_memory_search(args: dict) -> tuple[str, bool]:
    query = (args.get("query") or "").strip()
    if not query:
        return "error: 'query' is required", True
    project = (args.get("project") or "").strip() or None
    try:
        k = int(args.get("k") or 8)
    except (TypeError, ValueError):
        k = 8
    rerank = True if args.get("rerank") else None
    results, mode = memory_search.search_core(query, project, max(1, min(k, 25)),
                                              rerank=rerank)
    if mode == "empty":
        return "Memory index is empty — run embed_index.py first.", False
    if not results:
        return (f"No memory hits for {query!r}"
                + (f" in {project}" if project else ""), False)   # empty ≠ error
    lines = [f"{len(results)} hit(s) for {query!r}"
             + (f" [{project}]" if project else "") + f"  ({mode}):"]
    for r in results:
        head = f"- [{r.get('project')}/{r.get('ntype')}] {r.get('title')}  (score {r['score']})"
        body = m._note_snippet(r["stem"], r.get("ntype", "")) or r.get("description", "")
        lines.append(head + (f"\n    {body}" if body else ""))
        lines.append(f"    id: {r['stem']}")
    return "\n".join(lines), False


def _tool_memory_remember(args: dict) -> tuple[str, bool]:
    ns = SimpleNamespace(
        project=(args.get("project") or "").strip(),
        type=(args.get("type") or "pattern").strip(),
        title=(args.get("title") or "").strip(),
        desc=args.get("description") or "",
        prevention=args.get("prevention") or "",
        tags=args.get("tags") or "",
        supersedes=args.get("supersedes") or "",
        agent=m.DEFAULT_AGENT,
    )
    # capture BOTH streams so a success message printed to stderr isn't lost and
    # an empty stdout doesn't masquerade as a failure (audit H8)
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = _remember.do_remember(ns)
    out = buf.getvalue().strip()
    if rc != 0:
        return f"remember failed: {out or 'invalid arguments'}", True
    return out or f"Saved {ns.type} '{ns.title}' to project {ns.project}.", False


def _tool_memory_ingest(args: dict) -> tuple[str, bool]:
    project = (args.get("project") or "").strip()
    text = args.get("text") or ""
    agent = (args.get("agent") or "mcp").strip() or "mcp"
    if not project or not text.strip():
        return "error: 'project' and non-empty 'text' are required", True
    if not m.llm_available():
        return "error: no extraction backend (cloud key unset and Ollama down).", True
    session_id = "mcp-" + datetime.now().strftime("%Y%m%d%H%M%S")
    if not m.acquire_lock(timeout_s=60):
        return "error: memory store is busy (lock) — try again.", True
    try:
        db = m.load_processed()
        ok = m.process_session(session_id, "", "", "mcp-ingest", db,
                               agent=agent, transcript_text=text,
                               project_override=m.slug_project(project))
        m.rebuild_index()
        m.git_autocommit()
    except Exception as exc:
        return f"ingest failed: {exc}", True
    finally:
        m.release_lock()
    return ((f"Ingested into '{project}'.", False) if ok
            else ("Nothing stored (no project-relevant knowledge found).", False))


def _tool_memory_entities(args: dict) -> tuple[str, bool]:
    project = (args.get("project") or "").strip() or None
    try:
        k = int(args.get("k") or 12)
    except (TypeError, ValueError):
        k = 12
    entity = (args.get("entity") or "").strip()
    try:
        if entity:
            notes = m.notes_for_entity(entity, project, k)
            co = m.co_occurring(entity, project)
            lines = [f"{len(notes)} note(s) tagged {entity!r}"
                     + (f" [{project}]" if project else "") + ":"]
            lines += [f"  [{n['ntype']}] {n['title']}  ({n['stem']})" for n in notes]
            if co:
                lines.append("related: " + ", ".join(f"{e} x{c}" for e, c in co))
            return "\n".join(lines), False
        g = m.entity_graph(project)
        if not g:
            return "no entities yet (lessons get entity-tagged as they are captured)", False
        lines = [f"entity graph{(' [' + project + ']') if project else ''} ({len(g)} entities):"]
        for e, info in g.items():
            links = ", ".join(f"{le} x{lc}" for le, lc in info["links"])
            lines.append(f"  {e} ({info['notes']} notes)" + (f" -> {links}" if links else ""))
        return "\n".join(lines), False
    except Exception as exc:
        return f"error: {type(exc).__name__}", True


_DISPATCH = {
    "memory_search": _tool_memory_search,
    "memory_remember": _tool_memory_remember,
    "memory_ingest": _tool_memory_ingest,
    "memory_entities": _tool_memory_entities,
}


# ── JSON-RPC plumbing ─────────────────────────────────────────────────

def _send(msg: dict) -> None:
    """Write one JSON-RPC message to the real stdout (ASCII-escaped so a cp125x
    pipe can never raise on Cyrillic/emoji) followed by a newline."""
    _REAL_STDOUT.write(json.dumps(msg, ensure_ascii=True) + "\n")
    _REAL_STDOUT.flush()


def _result(req_id, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _handle(msg: dict) -> None:
    method = msg.get("method")
    req_id = msg.get("id")
    is_request = "id" in msg          # JSON-RPC: a request HAS an id (even null); a notification has none

    if method == "initialize":
        proto = (msg.get("params") or {}).get("protocolVersion") or DEFAULT_PROTOCOL
        _result(req_id, {
            "protocolVersion": proto,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    elif method == "tools/list":
        _result(req_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = _DISPATCH.get(name)
        if fn is None:
            _error(req_id, -32602, f"unknown tool: {name}")
            return
        try:
            text, is_error = fn(args)          # tools report their own status (H8)
        except Exception as exc:
            # log the detail (paths, types) to stderr; never leak it to the client
            print(f"[mcp] tool {name!r} crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
            text, is_error = "internal tool error", True
        _result(req_id, {"content": [{"type": "text", "text": text}],
                         "isError": is_error})
    elif method == "ping":
        _result(req_id, {})
    elif is_request:
        # any other request method we don't implement
        _error(req_id, -32601, f"method not found: {method}")
    # notifications (no id) — initialized, cancelled, etc. — need no response


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue          # malformed line — skip, never crash the server
        try:
            _handle(msg)
        except Exception as exc:
            rid = msg.get("id") if isinstance(msg, dict) else None
            if rid is not None:
                _error(rid, -32603, f"internal error: {exc}")


if __name__ == "__main__":
    main()
