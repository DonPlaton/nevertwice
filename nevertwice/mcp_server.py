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
        "nevertwice": {
          "command": "python",
          "args": ["/path/to/nevertwice/nevertwice/mcp_server.py"]
        }
      }
    }

Tools:
  memory_search    (query, project?, k?)                     — recall, read-only
  memory_remember  (project, type, title, description?, …)   — write a lesson
  memory_ingest    (project, text, agent?)                   — extract+store a chat
  memory_entities  (entity?, relations?, project?, k?)       — entity / relation graph, read-only
  memory_conflicts (project?, limit?)                        — supersession ledger, read-only
  memory_digest    (project?, days?)                         — what's new rollup, read-only
  memory_guard_check   (action_text, project?, path?)        — active memory A: guard a proposed action
  memory_anticipate    (trajectory, project?)                — active memory B: predict the failure ahead
  memory_what_breaks   (entity, project?)                    — active memory C: counterfactual impact
  memory_why           (entity, project?)                    — active memory C: upstream causes
  memory_guard_feedback     (guard_id, outcome, reason?)     — Popperian loop: train the guard
  memory_anticipate_feedback (stem, outcome)                 — cry-wolf damping for warnings

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
import digest as _digest         # noqa: E402  (conflicts + digest review commands)
import guards as _guards         # noqa: E402  (active memory A — executable guards)
import anticipate as _anticipate # noqa: E402  (active memory B — anticipatory warning)
import causal as _causal         # noqa: E402  (active memory C — counterfactual)

SERVER_NAME = "nevertwice"
SERVER_VERSION = "1.1.0"
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
                "expand_relations": {"type": "boolean",
                                     "description": "Also surface lessons reached by the "
                                                    "hits' typed relation edges (a bug query "
                                                    "surfaces its fixed-by fix). Optional."},
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
                        "`entity`: every lesson tagged with it, its co-occurring entities, "
                        "and its typed relation edges (caused-by / fixed-by / depends-on / "
                        "...). Without: the entity graph, or the typed-relation graph when "
                        "`relations` is true. Read-only."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string",
                           "description": "One entity to facet by (optional)."},
                "relations": {"type": "boolean",
                              "description": "Show the typed-relation graph instead of the "
                                             "entity graph (when no `entity`)."},
                "project": {"type": "string", "description": "Limit to one project (optional)."},
                "k": {"type": "integer", "description": "Max results (default 12)."},
            },
        },
    },
    {
        "name": "memory_conflicts",
        "description": ("The contradiction / supersession ledger — facts the memory revised "
                        "(an old note and the note that superseded it), newest first. Nevertwice "
                        "resolves contradictions at write time, so this is the audit trail of "
                        "what changed. Read-only."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Limit to one project (optional)."},
                "limit": {"type": "integer", "description": "Max rows (default 25)."},
            },
        },
    },
    {
        "name": "memory_digest",
        "description": ("A rollup of the memory store: what was added and revised in the last "
                        "N days, per project and type, plus the most-connected entities. The "
                        "daily/weekly 'what's new' view. Read-only."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Limit to one project (optional)."},
                "days": {"type": "integer", "description": "Window in days (default 7)."},
            },
        },
    },
    {
        "name": "memory_guard_check",
        "description": ("Active memory (A) — check a proposed action (a diff, command, or code "
                        "you are about to write) against learned guards. Returns any that FIRE, "
                        "each a one-line risk from a past mistake; a 'blocking' hit means stop and "
                        "comply or override. Silent (nothing) when clear — costs no tokens unless "
                        "it catches a real repeat. Run it BEFORE risky edits."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_text": {"type": "string",
                                "description": "The code/command/diff about to be applied."},
                "project": {"type": "string", "description": "Limit to one project (optional)."},
                "path": {"type": "string", "description": "File path being touched (optional)."},
            },
            "required": ["action_text"],
        },
    },
    {
        "name": "memory_anticipate",
        "description": ("Active memory (B) — predict the failure the CURRENT plan is heading "
                        "toward, by resemblance to past mistakes. Give it what you are about to do "
                        "(the plan / files / recent steps); returns one precise warning if risk is "
                        "high, or nothing. Catches novel forms a static guard would miss. Silent "
                        "below threshold (no tokens)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "trajectory": {"type": "string",
                               "description": "What the agent is about to do (plan/files/steps)."},
                "project": {"type": "string", "description": "Limit to one project (optional)."},
            },
            "required": ["trajectory"],
        },
    },
    {
        "name": "memory_what_breaks",
        "description": ("Active memory (C) — counterfactual: what may break if you change/touch "
                        "an entity (a file, module, or concept). Returns downstream impacts (from "
                        "the induced causal graph) plus known failure modes, synthesized in a few "
                        "lines — not an episode dump. Ask before a risky refactor."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string",
                           "description": "The thing you are about to change (file/module/concept)."},
                "project": {"type": "string", "description": "Limit to one project (optional)."},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "memory_why",
        "description": ("Active memory (C) — the reverse causal question: what CAUSES or underpins "
                        "an entity, from the induced causal graph. Use it to understand why "
                        "something is the way it is before changing its upstream."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "The entity to explain."},
                "project": {"type": "string", "description": "Limit to one project (optional)."},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "memory_guard_feedback",
        "description": ("Close the Popperian loop on a guard that fired: report what actually "
                        "happened. outcome='helped' (it caught a real repeat — corroborates; 3 "
                        "distinct sessions promote advisory→blocking), 'false_positive' (it was "
                        "wrong here — 3 demote/retire it), or 'corroborated'. ALWAYS send this "
                        "after acting on a memory_guard_check hit; it is how guards learn."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "guard_id": {"type": "string", "description": "The id from the guard hit."},
                "outcome": {"type": "string",
                            "enum": ["helped", "false_positive", "corroborated"]},
                "reason": {"type": "string",
                           "description": "Why (required in spirit for false_positive)."},
                "session_id": {"type": "string",
                               "description": "Distinct-session key for corroboration counting."},
            },
            "required": ["guard_id", "outcome"],
        },
    },
    {
        "name": "memory_anticipate_feedback",
        "description": ("Close the loop on an anticipation warning: 'helped' if it flagged a real "
                        "risk, 'false_alarm' if not. False alarms raise that mistake's firing "
                        "threshold (cry-wolf damping), so the warnings stay precise."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "stem": {"type": "string", "description": "The mistake stem from the warning."},
                "outcome": {"type": "string", "enum": ["helped", "false_alarm"]},
            },
            "required": ["stem", "outcome"],
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
    if args.get("expand_relations"):                              # Phase 2b graph expansion
        results = results + m.relation_expand(results, project, max_add=max(1, min(k, 25)))
    lines = [f"{len(results)} hit(s) for {query!r}"
             + (f" [{project}]" if project else "") + f"  ({mode}):"]
    for r in results:
        via = f"  (via {r['via']})" if r.get("via") else ""
        head = f"- [{r.get('project')}/{r.get('ntype')}] {r.get('title')}  (score {r['score']}){via}"
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
    # drop the engine's internal [memory_hook ...] log lines — the client should get the
    # human message, not a 5-line log blob (code-review 2026-07)
    out = "\n".join(l for l in buf.getvalue().splitlines()
                    if l.strip() and not l.startswith("[memory_hook")).strip()
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
            idx = m.entity_index(project)     # one scan, shared across the three facets
            notes = m.notes_for_entity(entity, project, k, idx=idx)
            co = m.co_occurring(entity, project, idx=idx)
            edges = m.related_by(entity, project=project, idx=idx)
            lines = [f"{len(notes)} note(s) tagged {entity!r}"
                     + (f" [{project}]" if project else "") + ":"]
            lines += [f"  [{n['ntype']}] {n['title']}  ({n['stem']})" for n in notes]
            if co:
                lines.append("co-occurs: " + ", ".join(f"{e} x{c}" for e, c in co))
            if edges:
                lines.append("edges: " + ", ".join(
                    f"--{e['rel']}--> {e['target']}" for e in edges))
            return "\n".join(lines), False
        if args.get("relations"):
            rg = m.relation_graph(project)
            if not rg:
                return "no typed relations yet (lessons declare them as they are captured)", False
            lines = [f"relation graph{(' [' + project + ']') if project else ''}:"]
            for src, es in rg.items():
                lines += [f"  {src} --{e['rel']}--> {e['target']} (x{e['notes']})" for e in es]
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


def _tool_memory_conflicts(args: dict) -> tuple[str, bool]:
    project = (args.get("project") or "").strip() or None
    try:
        limit = int(args.get("limit") or 25)
    except (TypeError, ValueError):
        limit = 25
    try:
        rows = _digest.compute_conflicts(m.slug_project(project) if project else None,
                                         limit=max(1, min(limit, 200)))
    except Exception as exc:
        return f"error: {type(exc).__name__}", True
    if not rows:
        return "No revised facts on record — nothing has been superseded yet.", False
    lines = [f"{len(rows)} revised fact(s), newest first:"]
    for r in rows:
        evo = "" if r["resolved"] else "  (still evolving)"
        lines.append(f"- [{r['new_date'] or r['old_date']}] {r['project']}/{r['ntype']}{evo}: "
                     f"{r['old_title']} → {r['new_title'] or '(archived)'}")
    return "\n".join(lines), False


def _tool_memory_digest(args: dict) -> tuple[str, bool]:
    project = (args.get("project") or "").strip() or None
    try:
        days = int(args.get("days") or 7)
    except (TypeError, ValueError):
        days = 7
    try:
        d = _digest.compute_digest(project, days=max(1, days))
    except Exception as exc:
        return f"error: {type(exc).__name__}", True
    t = d["totals"]
    lines = [f"Digest [{d['project']}] last {d['window_days']}d — {t['live_notes']} live notes, "
             f"{t['projects']} project(s), +{t['added_in_window']} added, "
             f"{t['revised_in_window']} revised."]
    for p, v in sorted(d["by_project"].items(), key=lambda kv: -kv[1]["total"])[:12]:
        lines.append(f"  {p}: {v['total']} (+{v['added']}/~{v['superseded']} revised)")
    if d["top_entities"]:
        lines.append("entities: " + ", ".join(f"{e['entity']}({e['notes']})"
                                               for e in d["top_entities"]))
    return "\n".join(lines), False


def _tool_memory_guard_check(args: dict) -> tuple[str, bool]:
    text = (args.get("action_text") or "").strip()
    if not text:
        return "error: 'action_text' is required", True
    project = (args.get("project") or "").strip() or None
    path = (args.get("path") or "").strip() or None
    try:
        hits = _guards.check(text, project=project, path=path)
    except Exception as exc:
        return f"error: {type(exc).__name__}", True
    if not hits:
        return "clear — no guard fires for this action.", False
    try:
        _guards.record_fired([h["id"] for h in hits])       # telemetry (guards list fired=)
    except Exception:
        pass
    lines = []
    for h in hits:
        tag = "BLOCK" if h["status"] == "blocking" else "warn"
        lines.append(f"[{tag}] {h['message']}  (id {h['id']})")
    lines.append("(after acting on this, call memory_guard_feedback with helped/false_positive)")
    return "\n".join(lines), False


def _tool_memory_anticipate(args: dict) -> tuple[str, bool]:
    traj = (args.get("trajectory") or "").strip()
    if not traj:
        return "error: 'trajectory' is required", True
    project = (args.get("project") or "").strip() or None
    try:
        hits = _anticipate.anticipate(traj, project=project, k=1)
    except Exception as exc:
        return f"error: {type(exc).__name__}", True
    if not hits:
        return "no anticipated failure above threshold.", False
    h = hits[0]
    return f"(risk {h['risk']}) {h['message']}", False


def _tool_memory_what_breaks(args: dict) -> tuple[str, bool]:
    entity = (args.get("entity") or "").strip()
    if not entity:
        return "error: 'entity' is required", True
    project = (args.get("project") or "").strip() or None
    try:
        out = _causal.counterfactual(entity, project)
    except Exception as exc:
        return f"error: {type(exc).__name__}", True
    return (out or f"no causal footprint recorded for `{entity}`."), False


def _tool_memory_why(args: dict) -> tuple[str, bool]:
    entity = (args.get("entity") or "").strip()
    if not entity:
        return "error: 'entity' is required", True
    project = (args.get("project") or "").strip() or None
    try:
        r = _causal.why(entity, project)
    except Exception as exc:
        return f"error: {type(exc).__name__}", True
    if not r.get("causes"):
        return f"no upstream causes recorded for `{entity}`.", False
    lines = [f"`{entity}` is caused / underpinned by:"]
    lines += [f"  <- {c['effect']}  (via {c['via']})" for c in r["causes"]]
    return "\n".join(lines), False


def _tool_memory_guard_feedback(args: dict) -> tuple[str, bool]:
    gid = (args.get("guard_id") or "").strip()
    outcome = (args.get("outcome") or "").strip()
    if not gid or outcome not in ("helped", "false_positive", "corroborated"):
        return "error: 'guard_id' and outcome in {helped,false_positive,corroborated} required", True
    try:
        g = _guards.feedback(gid, outcome, session_id=(args.get("session_id") or None),
                             reason=(args.get("reason") or None))
    except Exception as exc:
        return f"error: {type(exc).__name__}", True
    if not g:
        return f"no such guard: {gid}", True
    return (f"updated {gid}: status={g['status']} corroborations={g['corroborations']} "
            f"fp={g['false_positives']}"), False


def _tool_memory_anticipate_feedback(args: dict) -> tuple[str, bool]:
    stem = (args.get("stem") or "").strip()
    outcome = (args.get("outcome") or "").strip()
    if not stem or outcome not in ("helped", "false_alarm"):
        return "error: 'stem' and outcome in {helped,false_alarm} required", True
    try:
        s = _anticipate.feedback(stem, outcome)
    except Exception as exc:
        return f"error: {type(exc).__name__}", True
    return f"recorded {outcome} for {stem}: helped={s['helped']} false_alarms={s['false_alarms']}", False


_DISPATCH = {
    "memory_search": _tool_memory_search,
    "memory_remember": _tool_memory_remember,
    "memory_ingest": _tool_memory_ingest,
    "memory_entities": _tool_memory_entities,
    "memory_conflicts": _tool_memory_conflicts,
    "memory_digest": _tool_memory_digest,
    "memory_guard_check": _tool_memory_guard_check,
    "memory_anticipate": _tool_memory_anticipate,
    "memory_what_breaks": _tool_memory_what_breaks,
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
            # JSON-RPC 2.0: a parse failure must be ANSWERED (-32700, id null), not silently
            # dropped — a synchronous client awaiting its id would otherwise hang until its
            # own timeout (code-review 2026-07). Never crash the server either way.
            _error(None, -32700, "parse error")
            continue
        if not isinstance(msg, dict):
            _error(None, -32600, "invalid request: expected a JSON object")
            continue
        try:
            _handle(msg)
        except Exception as exc:
            rid = msg.get("id") if isinstance(msg, dict) else None
            if rid is not None:
                _error(rid, -32603, f"internal error: {exc}")


if __name__ == "__main__":
    main()
