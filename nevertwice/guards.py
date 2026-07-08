#!/usr/bin/env python3
"""Active memory, axis A - experience compiled into executable guards (Popperian).

A *mistake* is not just a note to recall; it is a hypothesis about a failure pattern. This
module turns high-recurrence mistakes into **guards**: tiny scoped checks that fire when the
agent is about to repeat the pattern. The point is token economy - a guard costs **zero
context tokens until it fires** (it lives in a JSON ledger, not in the prompt), then spends
one line. Memory stops taxing every turn and instead acts only when it has something worth
saying. See `research/ACTIVE_MEMORY.md`.

The danger of a memory that can constrain the agent is ossification, so **no guard is a law**:

  * born **advisory** (warns, never blocks),
  * promoted advisory→blocking only after K *distinct-session* corroborations,
  * **self-retires** after M false positives (overrides or fired-but-fine),
  * always overridable with a reason - and the override is feedback that narrows the guard,
    not defiance.

Reality is allowed to kill a wrong guard. Memory proposes; reality disposes.

    python -m nevertwice.guards check "model = torch.device('cpu')" --project myproj
    python -m nevertwice.guards list
    python -m nevertwice.guards feedback g-1a2b helped
    python -m nevertwice.guards feedback g-1a2b false_positive --reason "cpu is intended here"

Stdlib-only; the ledger is `<vault>/guards.json` (atomic writes). Guard *generation* from
mistakes uses the same cloud/Ollama router as extraction, with a deterministic fallback, and
runs at consolidation time (sleep-time) - never on the hot path.
"""
import fnmatch
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m          # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Popperian lifecycle constants (env-overridable for experiments).
import os
K_PROMOTE = m.env_int("NEVERTWICE_GUARD_PROMOTE", 3)   # distinct-session corroborations → blocking
M_RETIRE = m.env_int("NEVERTWICE_GUARD_RETIRE", 3)     # false positives → demote/retire
MAX_PATTERN = 200            # ReDoS guard: cap LLM-authored pattern length
MAX_CHECK_CHARS = 20000      # cap the text we scan, so a huge diff can't stall the hot path
STATUSES = ("advisory", "blocking", "retired")


def _ledger_path() -> Path:
    return m.VAULT / "guards.json"


def load_guards() -> list[dict]:
    p = _ledger_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_guards(guards: list[dict]) -> None:
    m.VAULT.mkdir(parents=True, exist_ok=True)
    m.write_atomic(_ledger_path(), json.dumps(guards, ensure_ascii=False, indent=1))


# ── ReDoS-safe pattern validation ─────────────────────────────────────
# Patterns can come from an LLM and run against agent-authored text, so a catastrophic
# backtracking pattern would be a self-inflicted DoS on the pre-tool hot path. We reject the
# dangerous shapes and cap length, then require the regex to compile. Two families of
# exponential backtracking are refused:
#   * nested quantifiers on a group/class:      (ab+)+  ,  [ab]+{
#   * a quantified group containing alternation: (a|aa)+ ,  (foo|foobar)*  - the classic
#     overlapping-alternation blowup the round-1 check missed (code-review, 2026-07).
# stdlib `re` has no match timeout, so validation-at-creation is the only guard; combined with
# the 20k input cap in check(), a guard that passes here cannot stall the agent.
_NESTED_QUANT = re.compile(
    r"(\([^)]*[+*?][^)]*\)[+*])"           # nested quantifier: (…+…)+ , (a?)+
    r"|(\[[^\]]*\][+*]\{)"                 # class then +/* then {
    r"|(\([^)]*\|[^)]*\)\s*[+*])"          # quantified alternation group: (a|aa)+
    # bounded repetition of a group that itself contains a quantifier: (x{1,2}){38} grows
    # Fibonacci-style (measured 4s at N=38 on CPython 3.14) yet fits every length cap, so the
    # round-2 check missed it (critic 2026-07). {N} over a quantified group is never needed by
    # a real guard; reject the shape outright.
    r"|(\([^)]*(?:[+*?]|\{\d[^}]*\})[^)]*\)\s*\{\d)"
)


def _quantified_group_count(pat: str) -> int:
    """How many capturing groups contain an unbounded quantifier (`+`/`*`), escape-aware.
    Two or more such groups adjacent (`(a+)(a+)...(a+)b`) are the catastrophic-backtracking
    shape the round-1/2 checks miss: each group can match the same run of input, so the engine
    explores exponentially many partitions. A real distilled-mistake guard never needs two
    separately-quantified groups, so rejecting them costs nothing and stops shape after shape
    without another denylist round (critic R3: this was the 3rd ReDoS shape found in 3 rounds)."""
    stripped = re.sub(r"\\.", "", pat)       # drop escaped chars so `\(`/`\+` aren't miscounted
    return len(re.findall(r"\([^)]*[+*]", stripped))


def safe_pattern(pat: str) -> bool:
    if not pat or len(pat) > MAX_PATTERN:
        return False
    if _NESTED_QUANT.search(pat):        # nested quantifier / quantified alternation / bounded-repeat
        return False
    if _quantified_group_count(pat) >= 2:   # N adjacent quantified groups (round-3 shape)
        return False
    try:
        re.compile(pat)
        return True
    except re.error:
        return False


def _guard_id(pattern: str, scope: dict) -> str:
    h = hashlib.sha1(f"{pattern}|{scope.get('project','')}|{scope.get('path_glob','')}"
                     .encode("utf-8", "replace")).hexdigest()[:8]
    return f"g-{h}"


def make_guard(pattern: str, message: str, *, project=None, path_glob=None, tool=None,
               born_from=(), date=None) -> dict | None:
    """Construct an advisory guard, or None if the pattern is unsafe/uncompilable."""
    if not safe_pattern(pattern) or not (message or "").strip():
        return None
    scope = {"project": (project or None), "path_glob": (path_glob or None),
             "tool": (tool or None)}
    return {
        "id": _guard_id(pattern, scope),
        "pattern": pattern,
        "message": message.strip()[:240],
        "scope": scope,
        "status": "advisory",
        "born_from": list(born_from),
        "born_date": date or datetime.now().strftime("%Y-%m-%d"),
        "corroborations": 0, "fired": 0, "helped": 0, "false_positives": 0,
        "seen_sessions": [], "last_fired": "",
        "overrides": [],            # learned exceptions: (reason) the agent gave when overriding
    }


def register(guards: list[dict], guard: dict) -> bool:
    """Add a guard to the ledger, deduped by id (pattern+scope). Returns True if new."""
    if guard is None:
        return False
    if any(g["id"] == guard["id"] for g in guards):
        return False
    guards.append(guard)
    return True


# ── the hot path: 0-token-until-fired check ───────────────────────────

def _scope_matches(g: dict, project, path, tool) -> bool:
    sc = g.get("scope", {})
    if sc.get("project") and sc["project"] != (project or ""):
        return False
    if sc.get("path_glob") and not (path and fnmatch.fnmatch(path, sc["path_glob"])):
        return False
    if sc.get("tool") and sc["tool"] != (tool or ""):
        return False
    return True


def check(action_text: str, *, project=None, path=None, tool=None,
          guards: list[dict] | None = None) -> list[dict]:
    """The hot path. Return the guards that fire for a proposed action - and NOTHING reaches
    context unless one matches (the token-economy core). Retired guards never fire. Each hit
    is `{id, status, message, scope}`; a `blocking` hit means the agent should stop and either
    comply or override-with-reason. Pure regex+scope match, input length-capped (no LLM, no
    network), so it is cheap enough to run before every action."""
    if not action_text:
        return []
    text = action_text[:MAX_CHECK_CHARS]
    if project:
        project = m.slug_project(project)      # guards are stored under the slugged project name;
                                               # slug the arg so a raw name ("svc-000") still matches
                                               # ("svc_000"). Idempotent - slug(slug(x)) == slug(x).
    guards = load_guards() if guards is None else guards
    hits = []
    for g in guards:
        if g.get("status") == "retired":
            continue
        if not _scope_matches(g, project, path, tool):
            continue
        try:
            if re.search(g["pattern"], text):
                hits.append({"id": g["id"], "status": g["status"],
                             "message": g["message"], "scope": g["scope"]})
        except re.error:
            continue          # a corrupt pattern never breaks the hot path
    # blocking before advisory, so the caller sees the hard stops first
    hits.sort(key=lambda h: 0 if h["status"] == "blocking" else 1)
    return hits


# ── the Popperian feedback loop ───────────────────────────────────────

def _find(guards, guard_id):
    return next((g for g in guards if g["id"] == guard_id), None)


def feedback(guard_id: str, outcome: str, *, session_id=None, reason=None,
             guards: list[dict] | None = None, persist: bool = True) -> dict | None:
    """Record what happened after a guard fired and run the lifecycle. `outcome`:
      * 'helped'         - the agent heeded it / it prevented the mistake. Counts as a
                           distinct-session corroboration; K of them promote advisory→blocking.
      * 'false_positive' - overridden, or fired on a case that was actually fine. M of them
                           demote (blocking→advisory→retired). `reason` is stored as a learned
                           exception that narrows the guard.
      * 'corroborated'   - confirmed relevant without a heed/override decision (a soft +).
    Returns the updated guard (or None if unknown). Reality disposes: a guard that keeps
    misfiring retires itself; one that keeps helping hardens."""
    owns = guards is None
    guards = load_guards() if owns else guards
    g = _find(guards, guard_id)
    if not g:
        return None
    sid = session_id or ""
    if outcome in ("helped", "corroborated"):
        g["helped"] += 1 if outcome == "helped" else 0
        if sid and sid not in g["seen_sessions"]:
            g["seen_sessions"].append(sid)
            g["corroborations"] += 1
        elif not sid:
            g["corroborations"] += 1
        if (g["status"] == "advisory" and g["corroborations"] >= K_PROMOTE
                and not g.get("pack")):              # pack guards stay advisory - never block on a heuristic
            g["status"] = "blocking"                 # earned the right to block
    elif outcome == "false_positive":
        g["false_positives"] += 1
        if reason:
            g["overrides"].append(reason.strip()[:200])
        if g["false_positives"] >= M_RETIRE:
            # demote one rung per breach: blocking→advisory→retired
            g["status"] = {"blocking": "advisory", "advisory": "retired"}.get(g["status"], "retired")
            g["false_positives"] = 0                 # reset the counter at each demotion
    else:
        return g
    g["confidence"] = _confidence(g)
    if persist and owns:
        save_guards(guards)
    return g


def _confidence(g: dict) -> float:
    """A Wilson-ish point estimate of 'fires correctly', from helped vs false positives.
    Cheap and monotone; only used for display/ranking, never to gate the lifecycle (the
    counts do that)."""
    pos, neg = g["helped"], g["false_positives"]
    n = pos + neg
    if n == 0:
        return 0.5
    return round((pos + 1) / (n + 2), 3)             # Laplace-smoothed


def record_fired(guard_ids, guards=None, persist=True) -> None:
    """Bump the fired counters for a set of hits in ONE pass and (at most) one atomic write -
    the telemetry behind `guards list`'s fired= column, distinct from the helped/false-positive
    verdict. Every check surface (the PreToolUse hot path, api.guards_check, the MCP tool)
    funnels through this, so there is a single implementation and no per-hit write amplification."""
    ids = set(guard_ids)
    if not ids:
        return
    guards = load_guards() if guards is None else guards
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    hit = False
    for g in guards:
        if g.get("id") in ids:
            g["fired"] = g.get("fired", 0) + 1
            g["last_fired"] = stamp
            hit = True
    if hit and persist:
        # persist regardless of who loaded the list: every check surface hands us the
        # ledger it already read (one load per event, critic 2026-07), and none of them
        # writes it back themselves - pass persist=False to batch externally.
        save_guards(guards)


# ── generation from mistakes (sleep-time, off the hot path) ───────────

# Well-known coding anti-patterns: if a mistake's text matches all the keywords, use the precise
# regex that matches the BUGGY construct (not the fix). This lifts the no-LLM generator from
# "coarse code-token" to real repeat-matching on the universal pitfalls, without any model. Each
# regex is ReDoS-safe (validated by safe_pattern before use). The LLM path still handles the
# long tail of project-specific mistakes; this just makes the offline floor genuinely useful.
_ANTIPATTERN_RULES = [
    (("sql", "f-string"), r"execute\s*\(\s*f[\"']"),
    (("sql", "format"), r"execute\s*\(\s*[\"'].*%[s(]"),
    (("float", "money"), r"\bfloat\s*\("),
    (("float", "price"), r"\bfloat\s*\("),
    (("json", "status"), r"\.json\(\)"),
    (("json", "response.ok"), r"\.json\(\)"),
    (("bare", "except"), r"except\s*:"),
    (("mutable", "default"), r"def\s+\w+\([^)]*=\s*(\[\s*\]|\{\s*\})"),
    (("iterat", "modif"), r"\.(remove|pop|insert|discard)\s*\("),   # the mutating call, not any for-loop
    (("eval",), r"\beval\s*\("),
    (("shell", "true"), r"shell\s*=\s*True"),
    (("== none",), r"[!=]=\s*None\b"),
    (("isinstance",), r"\btype\s*\([^)]{1,40}\)\s*=="),
    (("os.system",), r"\bos\.system\s*\("),
    (("pickle",), r"pickle\.loads?\s*\("),
    (("yaml", "load"), r"yaml\.load\s*\("),
    (("verify", "false"), r"verify\s*=\s*False"),
]


def _antipattern_for(note: dict) -> str | None:
    """Match the mistake against the known anti-pattern rules (all keywords present in the
    combined text). Returns the precise buggy-construct regex, or None."""
    blob = f"{note.get('title','')} {note.get('desc','')} {note.get('prevention','')}".lower()
    for keywords, pat in _ANTIPATTERN_RULES:
        if all(kw in blob for kw in keywords) and safe_pattern(pat):
            return pat
    return None


# ── universal cold-start guard pack (weak-PC / cloud-agent, opt-in) ────
# High-precision pitfalls that are almost always a smell, so they are safe to fire on ANY
# project with NO history and NO model behind them. This is the weak-PC / cloud-agent story:
# `NEVERTWICE_GUARD_PACK=1` and common footguns are guarded from the first session, pure stdlib,
# zero network. Pack guards are advisory and NEVER promote to blocking (they must never box the
# agent in on a heuristic), and self-retire like any guard if they cry wolf. Every pattern here
# is ReDoS-safe (asserted by a test).
_UNIVERSAL_GUARDS = [
    (r"[!=]=\s*None\b", "compare with `is None` / `is not None`, not `==` / `!=`"),
    (r"\btype\s*\([^)]{1,40}\)\s*==", "use isinstance() for type checks, not `type(x) ==`"),
    (r"except\s*:", "catch a specific exception, not a bare `except:`"),
    (r"\beval\s*\(", "eval() on any dynamic input is remote code execution - avoid it"),
    (r"\bexec\s*\(", "exec() on any dynamic input is remote code execution - avoid it"),
    (r"\bos\.system\s*\(", "os.system runs a shell; use subprocess.run([...]) with a list"),
    (r"pickle\.loads?\s*\(", "pickle on untrusted data is remote code execution; prefer json"),
    (r"shell\s*=\s*True", "subprocess with shell=True and a string command is injection-prone"),
    (r"verify\s*=\s*False", "verify=False disables TLS certificate validation"),
    (r"yaml\.load\s*\(", "use yaml.safe_load(); yaml.load can construct arbitrary objects"),
    (r"hashlib\.(md5|sha1)\s*\(", "md5/sha1 are unsafe for passwords/signatures; use a strong KDF"),
]


def universal_pack() -> list[dict]:
    """Build the global cold-start guard pack (advisory, project-wide, non-promoting). Stdlib
    only, no model, no history - instant value on a fresh install or a weak machine."""
    out = []
    for pat, msg in _UNIVERSAL_GUARDS:
        g = make_guard(pat, msg, born_from=["universal-pack"])
        if g:
            g["pack"] = True                     # advisory-only: feedback() never promotes it
            out.append(g)
    return out


def ensure_universal_pack(guards: list[dict]) -> int:
    """Add any missing universal-pack guards to the ledger (idempotent). Returns count added."""
    return sum(1 for g in universal_pack() if register(guards, g))


# Distinctive code-like tokens that make a usable literal pattern, in preference order: a
# backtick-quoted symbol, a dotted call (`torch.device`), a quoted string literal ('cpu'),
# then an assert target. Case-insensitive. Used only when no LLM is up - the LLM path is the
# real generator (it is prompted to match the mistake-REPEAT; this fallback just matches the
# most distinctive code token mentioned, a coarser but safe net).
_CODEISH = re.compile(
    r"`([^`]{2,60})`"                                     # `backtick`
    r"|\b([A-Za-z_][\w]*\.[A-Za-z_][\w.]+)\b"             # dotted.call
    r"|'([A-Za-z_][\w./-]{1,40})'|\"([A-Za-z_][\w./-]{1,40})\""   # 'literal' / "literal"
    r"|\b(assert\s+[A-Za-z_][\w.\(\)\s=<>!]{2,40})",     # assert expr
    re.IGNORECASE)


def _deterministic_pattern(note: dict) -> str | None:
    """A no-LLM fallback: first try the known anti-pattern rules (which match the BUGGY
    construct precisely), then fall back to lifting the most distinctive code-like token from
    the mistake. The LLM path produces the precise mistake-repeat pattern for the long tail."""
    rule = _antipattern_for(note)
    if rule:
        return rule
    for field in (note.get("desc", ""), note.get("title", ""), note.get("prevention", "")):
        best = None
        for mobj in _CODEISH.finditer(field or ""):
            cand = next((g for g in mobj.groups() if g), "").strip()
            if cand and (best is None or len(cand) > len(best)):
                best = cand
        if best and len(best) >= 3:
            pat = re.escape(best)
            if safe_pattern(pat):
                return pat
    return None


_GEN_PROMPT = """You convert a past coding MISTAKE into a guard that fires when an agent is about to repeat it. Output ONLY JSON: {{"pattern": "<a SHORT safe Python regex matching the about-to-be-written code/command that would repeat the mistake>", "message": "<one sentence: the risk + what to do instead>"}}. The regex must be specific (avoid matching unrelated code), under 120 chars, with NO nested quantifiers. If you cannot make a precise pattern, output {{"pattern": "", "message": ""}}.

MISTAKE: {title}
WHAT HAPPENED: {desc}
PREVENTION: {prevention}
"""


def propose_from_mistake(note: dict, *, use_llm: bool = True) -> dict | None:
    """Turn one mistake-note meta into an advisory guard. Tries the LLM (specific regex +
    message) and falls back to a deterministic literal pattern. Returns None if neither
    yields a safe pattern. Off the hot path - called at consolidation."""
    project = note.get("project")
    if use_llm and m.llm_available() and not m.is_local_only(project):
        try:
            res = m.generate_json(_GEN_PROMPT.format(
                title=note.get("title", ""), desc=(note.get("desc") or "")[:400],
                prevention=(note.get("prevention") or "")[:400]), project=project)
        except Exception:
            res = {}
        pat = (res or {}).get("pattern", "").strip()
        msg = (res or {}).get("message", "").strip()
        if pat and msg and safe_pattern(pat):
            return make_guard(pat, msg, project=project, born_from=[note.get("stem", "")])
    # deterministic fallback
    pat = _deterministic_pattern(note)
    if pat:
        msg = (note.get("prevention") or note.get("title") or "past mistake")[:200]
        return make_guard(pat, f"past mistake: {msg}", project=project,
                          born_from=[note.get("stem", "")])
    return None


def generate_from_vault(project=None, *, min_recurrence=1, limit=None, use_llm=True) -> int:
    """Build guards from the vault's mistake notes (highest-recurrence first) and add any new
    ones to the ledger. Returns the count added. Idempotent (dedup by id). This is the
    sleep-time pass; the hot-path `check()` only ever reads the resulting ledger."""
    notes = m._iter_project_notes(m.slug_project(project)) if project else m._iter_all_notes()
    mistakes = [n for n in notes if n.get("ntype") == "mistake"
                and n.get("recurrence", 1) >= min_recurrence
                and (n.get("prevention") or n.get("desc"))]
    mistakes.sort(key=lambda n: -n.get("recurrence", 1))
    if limit:
        mistakes = mistakes[:limit]
    guards = load_guards()
    added = 0
    if os.environ.get("NEVERTWICE_GUARD_PACK", "").strip() not in ("", "0", "false", "no"):
        added += ensure_universal_pack(guards)        # opt-in cold-start pack (weak-PC / no model)
    for n in mistakes:
        if any(n.get("stem", "") in g.get("born_from", []) for g in guards):
            continue                                  # already distilled this mistake
        g = propose_from_mistake(n, use_llm=use_llm)
        if register(guards, g):
            added += 1
    if added:
        save_guards(guards)
    return added


# ── CLI ───────────────────────────────────────────────────────────────

def _print_hits(hits):
    if not hits:
        print("ok - no guard fires for this action.")
        return
    for h in hits:
        tag = "BLOCK" if h["status"] == "blocking" else "warn "
        print(f"  [{tag}] ({h['id']}) {h['message']}")
    if any(h["status"] == "blocking" for h in hits):
        print("  → a blocking guard fired. Comply, or override: "
              "guards feedback <id> false_positive --reason \"...\"")


def main():
    argv = sys.argv[1:]
    if not argv:
        print("usage: guards check <text> | list | feedback <id> <helped|false_positive> "
              "[--reason ..] | generate [--project P] [--limit N] | pack")
        return
    cmd = argv[0]
    if cmd == "pack":
        guards = load_guards()
        n = ensure_universal_pack(guards)
        if n:
            save_guards(guards)
        print(f"universal guard pack: {n} added, {len(_UNIVERSAL_GUARDS)} total "
              f"(advisory, global) → {_ledger_path()}")
        return
    if cmd == "check":
        text = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else ""
        hits = check(text, project=m.argval(argv, "project"), path=m.argval(argv, "path"))
        _print_hits(hits)
    elif cmd == "list":
        guards = load_guards()
        live = [g for g in guards if g["status"] != "retired"]
        print(f"{len(live)} live guard(s) ({len(guards)} total incl. retired):")
        for g in sorted(guards, key=lambda x: (x["status"], -x["fired"])):
            print(f"  ({g['id']}) [{g['status']:8}] fired={g['fired']} helped={g['helped']} "
                  f"fp={g['false_positives']} conf={g.get('confidence',0.5)}  {g['message'][:70]}")
    elif cmd == "feedback":
        gid = argv[1] if len(argv) > 1 else ""
        outcome = argv[2] if len(argv) > 2 else ""
        g = feedback(gid, outcome, reason=m.argval(argv, "reason"))
        print(f"updated {gid}: status={g['status']} corroborations={g['corroborations']} "
              f"fp={g['false_positives']}" if g else f"no such guard: {gid}")
    elif cmd == "generate":
        lim = m.argval(argv, "limit")
        n = generate_from_vault(m.argval(argv, "project"),
                                limit=int(lim) if lim else None)
        print(f"added {n} new guard(s) → {_ledger_path()}")
    else:
        print(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
