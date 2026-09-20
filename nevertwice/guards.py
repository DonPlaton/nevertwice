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
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from . import memory_hook as m
except ImportError:                 # run as a script, not as a package
    import memory_hook as m  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Popperian lifecycle constants (env-overridable for experiments).
import os
K_PROMOTE = m.env_int("NEVERTWICE_GUARD_PROMOTE", 3)   # distinct-session corroborations → blocking
SEEN_SESSIONS_CAP = m.env_int("NEVERTWICE_GUARD_SEEN_CAP", 50)  # bounded: a ledger entry is not a log
M_RETIRE = m.env_int("NEVERTWICE_GUARD_RETIRE", 3)     # false positives → demote/retire
MAX_PATTERN = 200            # ReDoS guard: cap LLM-authored pattern length
MAX_CHECK_CHARS = 20000      # cap the text we scan, so a huge diff can't stall the hot path
MAX_LINE_CHARS = 200         # and cap each LINE: backtracking is quadratic in the string a
                             # pattern runs against, so the line is the real unit of cost
STATUSES = ("advisory", "blocking", "retired")


def _sibling(name: str):
    """Import an optional sibling module, or None.

    Deferred on purpose: `why_fired` imports this module, so importing it at the top would be
    a cycle, and the hot path must not pay for an explanation it is not going to print. The
    live deployment is also a flat selective copy of scripts, where a module may simply be
    absent - `--why` degrades to the plain line rather than failing the check.
    """
    try:
        return __import__(name)
    except Exception:               # noqa: BLE001 - any import failure is the same answer here
        return None


def _ledger_path() -> Path:
    return m.VAULT / "guards.json"


def load_guards() -> list[dict]:
    """Two-generation load, LOUDLY on recovery (review 2026-08 I3): guards are
    active protection - a silently-swallowed corrupt ledger meant every guard
    stopped firing with no trace, the exact silent-reset the processed-db already
    defends against."""
    data = m._load_json_generations(_ledger_path(), "guards ledger", expect=list)
    return data if data is not None else []


def save_guards(guards: list[dict]) -> None:
    m.VAULT.mkdir(parents=True, exist_ok=True)
    m._save_json_generations(_ledger_path(),
                             json.dumps(guards, ensure_ascii=False, indent=1))


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


#: The probe builds its inputs at the size `check()` actually scans, not at 96 characters.
#: Exponential backtracking shows up at any length; POLYNOMIAL backtracking does not - it is
#: invisible at 96 and fatal at twenty thousand. `open\(.*\).*encoding`, an ordinary shape for a
#: model-written guard, passed at 96 characters and then took 16.46 seconds inside `check()` on one
#: full-length line, with `emit_pretooluse_guard` looping every guard in the ledger before every
#: Edit, Write, MultiEdit and Bash. Four rounds of this file chased new SHAPES; the mismatch was
#: this number.
_REDOS_PROBE = r"""
import re, sys
p = sys.stdin.buffer.read().decode("utf-8", "replace")
rx = re.compile(p)
N = {cap}
SHORT = {line}
# Two families of input, at two sizes, because they fail differently.
#
# A uniform run of one character is the classic exponential trigger, and it is NOT what the hot
# path scans - real text breaks every eighty characters or so. Run at the full cap it condemns
# ordinary patterns: `\w+\s*=\s*\w+` takes 2.5 seconds on twenty thousand "a"s and is a
# perfectly good guard on code. So the uniform runs stay short, where an exponential shape still
# blows up and a linear one costs nothing.
#
# The realistic killer is made OF the pattern: many repetitions of a near-match whose last piece
# is missing, so every repetition is a fresh starting position that scans on and fails.
# `open\(.*\).*encoding` is instant on one long line and takes forty seconds on
# "open(x)" * 2857. A literal seed is recovered from the pattern - regex constructs dropped,
# ESCAPED characters kept, because the escaped parens are what make the near-match - and repeated
# to the full cap along with two truncations of it. The probe still never needs to know which
# construct is slow.
seed = re.sub(r"\\(.)|[(){}|^$?*+\[\]]", lambda mo: mo.group(1) or "", p)
seed = (seed.replace(".", "x").strip() or "ax")[:64]
cases = ["a" * SHORT + "!", "ab" * (SHORT // 2) + "!", "0" * SHORT + "!", " " * SHORT + "x",
         "x" * SHORT, "a" * (SHORT // 2) + " " * (SHORT // 2), "a " * (SHORT // 2) + "!"]
for h in (seed, seed[: max(1, len(seed) // 2)], seed[: max(1, 2 * len(seed) // 3)]):
    cases.append(h * (SHORT // len(h)))
    cases.append((h + " ") * (SHORT // (len(h) + 1)))
for s in cases:
    rx.search(s[:SHORT])
""".replace("{cap}", str(MAX_CHECK_CHARS)).replace("{line}", str(MAX_LINE_CHARS))


#: What the MATCH is allowed to take, once the child interpreter is actually running. This is
#: the number the probe was always about; it is not a budget for starting Python.
REDOS_MATCH_BUDGET_S = 0.6

#: This machine's bare-interpreter startup cost, measured once. `None` until first needed.
_STARTUP_COST: float | None = None


def _startup_cost() -> float:
    """How long a bare `python -c pass` takes HERE, measured rather than assumed.

    The probe below used to give the whole subprocess 0.6 seconds, which silently included
    interpreter startup. On this machine startup is ~30 ms and the budget was never in danger;
    on a loaded Windows CI runner it exceeded 0.6 s on its own, so a perfectly safe pattern
    timed out, `make_guard` returned None, and guard creation failed with no message. The same
    thing would happen on a user's busy laptop.

    So the startup cost is measured and the match budget is added on top. Cached: the
    measurement costs one spawn per process, and guard creation is a sleep-time path.
    """
    global _STARTUP_COST
    if _STARTUP_COST is None:
        best = None
        for _ in range(2):                       # best of two: one may hit a scheduling hiccup
            started = time.perf_counter()
            try:
                subprocess.run([sys.executable, "-c", "pass"], capture_output=True, timeout=30)
            except Exception:                    # noqa: BLE001 - cannot measure, so be generous
                _STARTUP_COST = 2.0
                return _STARTUP_COST
            elapsed = time.perf_counter() - started
            best = elapsed if best is None else min(best, elapsed)
        # Capped: a machine that cannot start Python in five seconds has a bigger problem than
        # this probe, and an unbounded budget would let a real backtracker run forever.
        _STARTUP_COST = min(best or 0.5, 5.0)
    return _STARTUP_COST


def _redos_safe(pat: str) -> bool:
    """Empirically confirm `pat` runs fast on adversarial input. Static ReDoS denylists are a
    losing game - one has now missed a fresh catastrophic shape FOUR review rounds running
    (nested `(a+)+`, quantified-alternation `(a|aa)+`, bounded-repeat `(a{1,2}){38}`, and
    paren-less adjacent quantifiers `a+a+...b`). So instead of enumerating shapes, RUN the
    compiled pattern under a HARD timeout. A thread can't be timed out - CPython's `re` holds the
    GIL through a catastrophic match - but a subprocess can: the OS kills it. This is called only
    at guard CREATION (sleep-time consolidation); the PreToolUse hot path never calls safe_pattern,
    so the spawn is irrelevant.

    The timeout is `startup + REDOS_MATCH_BUDGET_S`, not a flat budget: see `_startup_cost`.
    A rejection is LOGGED, because "your guard was silently not created" is the failure this
    whole function is supposed to prevent, not cause.
    """
    budget = _startup_cost() + REDOS_MATCH_BUDGET_S
    try:
        r = subprocess.run([sys.executable, "-c", _REDOS_PROBE], input=pat.encode("utf-8"),
                           capture_output=True, timeout=budget)
        return r.returncode == 0                 # finished within the budget → safe
    except subprocess.TimeoutExpired:
        m.log(f"guard pattern rejected: still backtracking after {budget:.2f}s "
              f"(match budget {REDOS_MATCH_BUDGET_S}s + measured startup "
              f"{_startup_cost():.2f}s): {pat[:60]!r}")
        return False                             # still backtracking → reject
    except Exception:                            # noqa: BLE001
        return True                              # a spawn hiccup must not block guard creation


#: The smallest possible texts. A pattern that fires on one of these carries no information: it
#: says nothing about what it matched. A guard is allowed to fire often - `\w+\s*=\s*\w+`
#: matches every assignment and that is a real guard - so the check is deliberately narrow:
#: match-anything, not match-frequently.
_NEUTRAL_CONTROLS = (" ", "\n", "x", "\t")


def _too_broad(pat: str) -> bool:
    """True when the pattern fires on text that carries no mistake at all.

    `safe_pattern` checked length, ReDoS shape and compilability, and nothing about what the
    pattern SAYS: a match-anything dot-star, a bare dot, a whitespace class and an
    empty group all passed. `propose_from_mistake` accepts whatever
    the model returns, so one generation emitting `.*` instead of the documented empty-pattern
    escape hatch mints a project-scoped guard that fires on every tool call, spends a context line
    each time, and promotes itself to `blocking` after three distinct sessions - which under
    `NEVERTWICE_GUARD_ENFORCE=1` denies every edit in the project."""
    try:
        rx = re.compile(pat)
    except re.error:
        return True
    if rx.search(""):                    # matches the empty string, so it matches anything
        return True
    return any(rx.search(ctrl) for ctrl in _NEUTRAL_CONTROLS)


def safe_pattern(pat: str) -> bool:
    if not pat or len(pat) > MAX_PATTERN:
        return False
    if _NESTED_QUANT.search(pat):        # cheap fast-reject for the obvious nested/alternation shapes
        return False
    try:
        re.compile(pat)
    except re.error:
        return False
    if _too_broad(pat):
        m.log(f"guard pattern rejected: it fires on ordinary text: {pat[:60]!r}")
        return False
    return _redos_safe(pat)              # authoritative, shape-agnostic: reject any real backtracker


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
        "seen_sessions": [], "delivered_sessions": [], "last_fired": "",
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

def _glob_matches(path: str, glob: str) -> bool:
    """Separator-normalized glob match against the full path, its basename, and every
    relative tail. The old full-absolute-path fnmatch could never match a relative
    glob like `src/*.py` on Windows (`D:\\proj\\src\\x.py`), so directory-scoped
    guards existed, cost a check per PreToolUse, and protected nothing (review
    2026-08 P7)."""
    p = (path or "").replace("\\", "/")
    g = (glob or "").replace("\\", "/")
    if fnmatch.fnmatch(p, g):
        return True
    parts = [x for x in p.split("/") if x]
    # every relative tail: a/b/c.py -> b/c.py -> c.py
    return any(fnmatch.fnmatch("/".join(parts[i:]), g) for i in range(1, len(parts)))


def _scope_matches(g: dict, project, path, tool) -> bool:
    sc = g.get("scope", {})
    if sc.get("project") and sc["project"] != (project or ""):
        return False
    if sc.get("path_glob") and not (path and _glob_matches(str(path), sc["path_glob"])):
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
    # Matched line by line, each line capped. Backtracking cost is quadratic in the length of the
    # string a pattern is run against, not in the total, so scanning one 20,000-character blob let
    # an ordinary pattern cost seconds: `open\(.*\).*encoding` measured 16 s on a long line, and
    # even `\w+\s*=\s*\w+` - a perfectly good guard - takes 2.5 s on twenty thousand word
    # characters. Per line, the same patterns cost a millisecond or two, and the worst case is
    # bounded by MAX_LINE_CHARS x MAX_CHECK_CHARS instead of MAX_CHECK_CHARS squared. A guard
    # pattern describes a construct, which lives on a line; a pattern that needs to span lines
    # would have to say so, and none of the generated ones do.
    lines = [ln[:MAX_LINE_CHARS] for ln in text.splitlines()] or [""]
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
            if any(re.search(g["pattern"], ln) for ln in lines):
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
    """Record what happened after a guard fired and run the lifecycle.

    `outcome` is one of the five in `outcomes.OUTCOMES`:
      * 'prevented_failure' - it fired and a real repeat was demonstrably avoided.
      * 'accepted'          - the agent heeded it and changed course.
      * 'overridden'        - the agent proceeded anyway. A statement about **burden**.
      * 'false_positive'    - it fired on a case that was fine. About **correctness**.
      * 'unknown'           - recorded, counts for nothing, and stays visible as unresolved.
    The pre-D4 names `helped` and `corroborated` still work and map onto `accepted`.
    A `reason` given with an override or a false positive is stored as a learned exception.

    Both directions are calibrated on **distinct sessions**. Until D4 only promotion was: one
    frustrated session could retire a guard it could not have promoted, and a caller passing
    no session id could promote by repeating itself. Falsification must be at least as hard to
    fake as confirmation, so an outcome with no session id counts toward the rates and toward
    neither threshold.

    Returns the updated guard, or None if the id is unknown. An unrecognised outcome returns
    the guard unchanged rather than being silently filed as `unknown` - feedback that went
    nowhere should be visible to the caller.
    """
    owns = guards is None
    guards = load_guards() if owns else guards
    g = _find(guards, guard_id)
    if not g:
        return None

    _outcomes = _sibling("outcomes")
    if _outcomes is None:              # a flat install without outcomes.py keeps working
        return _legacy_feedback(g, outcome, session_id, reason, guards, persist and owns)

    name = _outcomes.record(g, outcome, session_id=session_id)
    if name is None:
        return g                       # unrecognised: nothing recorded, nothing decided
    if reason and name in ("overridden", "false_positive"):
        g["overrides"].append(reason.strip()[:200])

    _mirror_legacy_counters(g, _outcomes)
    decision = _outcomes.verdict(g, promote_at=K_PROMOTE, retire_at=M_RETIRE)
    if decision["action"] == "promote":
        g["status"] = decision["to"]
    elif decision["action"] == "demote":
        g["status"] = decision["to"]
        # A demotion consumes the evidence on BOTH sides, not just the opposing half.
        #
        # Clearing only the overrides looked right and produced a guard that could never
        # retire: the supporting sessions that earned `blocking` were still standing, so the
        # very next override re-promoted it, and the guard oscillated advisory<->blocking
        # forever. Demotion has to mean the corroborations were falsified - the guard proved
        # it did not deserve that rung, and it re-earns promotion from zero. Keeping the raw
        # counts is what preserves the history.
        sessions = _outcomes.block(g)["sessions"]
        sessions["against"] = []
        sessions["support"] = []
        g["demotions"] = int(g.get("demotions") or 0) + 1
    g["last_decision"] = decision

    g["confidence"] = _confidence(g)
    if persist and owns:
        # The LIFECYCLE write - an outcome, a promotion, a demotion - went straight to
        # `save_guards`, so the one writer in this module whose loss is a lost decision was the
        # one writer not serialised. Re-load inside the lock and put this guard back by id, so a
        # concurrent change to any OTHER guard survives; `LedgerBusy` reaches the surface rather
        # than letting it report a write that did not happen.
        def _put_back(rows) -> bool:
            target = _find(rows, guard_id)
            if target is None:
                return False
            target.clear()
            target.update(g)
            return True

        persist_under_lock(_put_back, LIFECYCLE_LOCK_S, required=True)
    return g


def _mirror_legacy_counters(g: dict, _outcomes) -> None:
    """Keep the pre-D4 fields agreeing with the outcome block.

    `helped`, `false_positives`, `corroborations` and `seen_sessions` are read by the CLI
    listing, the dashboard, `why_fired` and any third-party script written against the old
    shape. Deriving them here rather than maintaining two tallies is what stops the two from
    disagreeing - which is the failure this whole task exists to prevent one level up.
    """
    acc = _outcomes.block(g)
    counts = acc["counts"]
    g["helped"] = sum(counts[k] for k in _outcomes.SUPPORT)
    g["false_positives"] = counts["false_positive"] + counts["overridden"]
    g["corroborations"] = len(acc["sessions"]["support"])
    g["seen_sessions"] = list(acc["sessions"]["support"])


def _legacy_feedback(g, outcome, session_id, reason, guards, persist):
    """The pre-D4 lifecycle, kept for an install that ships guards.py without outcomes.py."""
    sid = session_id or ""
    if outcome in ("helped", "corroborated", "accepted", "prevented_failure"):
        g["helped"] += 1 if outcome != "corroborated" else 0
        if sid and sid not in g["seen_sessions"]:
            g["seen_sessions"].append(sid)
            g["corroborations"] += 1
        elif not sid:
            g["corroborations"] += 1
        if (g["status"] == "advisory" and g["corroborations"] >= K_PROMOTE
                and not g.get("pack")):
            g["status"] = "blocking"
    elif outcome in ("false_positive", "overridden"):
        g["false_positives"] += 1
        if reason:
            g["overrides"].append(reason.strip()[:200])
        if g["false_positives"] >= M_RETIRE:
            g["status"] = {"blocking": "advisory",
                           "advisory": "retired"}.get(g["status"], "retired")
            g["false_positives"] = 0
    else:
        return g
    g["confidence"] = _confidence(g)
    if persist:
        save_guards(guards)
    return g


def _confidence(g: dict) -> float:
    """The point estimate of 'fires correctly', for display and ranking only - never to gate
    the lifecycle, which the distinct-session counts do.

    It said "Wilson-ish" and was Laplace. It is Wilson now, over the *correctness* outcomes
    only: an override means the guard went unheeded, not that it was wrong, and folding the
    two together is how a guard that is right but annoying ends up looking inaccurate. The
    full interval and the override rate are in `outcomes.summary`; this stays a single float
    because the CLI listing, the dashboard and `why_fired` all read it as one.

    0.5 with no evidence is a deliberate prior, not a measurement - `summary()` reports that
    case as an undefined precision rather than as a number.
    """
    _outcomes = _sibling("outcomes")
    if _outcomes is None:                            # flat install: the old Laplace estimate
        pos, neg = g["helped"], g["false_positives"]
        n = pos + neg
        return 0.5 if n == 0 else round((pos + 1) / (n + 2), 3)
    counts = _outcomes.block(g)["counts"]
    pos = sum(counts[k] for k in _outcomes.SUPPORT)
    interval = _outcomes.wilson(pos, pos + counts["false_positive"])
    return 0.5 if interval["point"] is None else round(interval["point"], 3)


#: How long a ledger writer waits for the vault lock. `record_fired` runs on the PreToolUse path
#: before every Edit, Write, MultiEdit and Bash, so it waits briefly and gives up: `fired` is
#: telemetry by this module's own rule - it never touches the lifecycle and `support()` cannot see
#: it - and telemetry that blocks the agent is worse than a telemetry value that is one low.
#: `forget_delivery` runs at PreCompact, off the hot path, and can afford to wait.
FIRED_LOCK_S = 2.0
FORGET_LOCK_S = 15.0
#: The lifecycle and the inbox: a human or an agent recording a decision, never the hot path.
#: A lost write here is a lost decision, so these callers pass `required=True` and are told.
LIFECYCLE_LOCK_S = 15.0


class LedgerBusy(RuntimeError):
    """The vault lock could not be taken, so a write that must not be lost was not attempted.

    Raised only for `required=True` callers - the ones whose write is a decision rather than
    telemetry. `record_fired` keeps the boolean: a counter that is one low beats blocking the
    agent before every tool call.
    """


def persist_under_lock(mutate, timeout_s: float, *, required: bool = False) -> bool:
    """Apply `mutate` to a FRESHLY loaded ledger and write it, holding the vault lock.

    Every writer here was a read-modify-write over one JSON file with no lock: the caller loaded
    the ledger, mutated its own copy, and saved it whole, so two tool calls in flight lost one of
    the two updates - a delivery record silently dropped, and the same advisory re-injected for the
    rest of the session. Re-loading INSIDE the lock is what makes the other writer's change
    survive; mutating the caller's copy as well is what keeps the value it already holds correct.

    Returns False when the lock could not be taken in time, so the caller can say nothing was
    persisted rather than assume it was - or raises `LedgerBusy` when `required=True`, because a
    caller recording a DECISION must not report success for a write that never happened.
    """
    if not m.acquire_lock(timeout_s=timeout_s):
        m.log("guards ledger: vault lock busy, nothing persisted this call")
        if required:
            raise LedgerBusy("vault lock busy - nothing was recorded; another writer is active")
        return False
    try:
        fresh = load_guards()
        if mutate(fresh):
            save_guards(fresh)
        return True
    finally:
        m.release_lock()


def record_fired(guard_ids, guards=None, persist=True, session=None) -> None:
    """Bump the fired counters for a set of hits in ONE pass and (at most) one atomic write -
    the telemetry behind `guards list`'s fired= column, distinct from the helped/false-positive
    verdict. Every check surface (the PreToolUse hot path, api.guards_check, the MCP tool)
    funnels through this, so there is a single implementation and no per-hit write amplification."""
    ids = set(guard_ids)
    if not ids:
        return
    guards = load_guards() if guards is None else guards
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    def _bump(rows) -> bool:
        touched = False
        for g in rows:
            if g.get("id") in ids:
                g["fired"] = g.get("fired", 0) + 1
                g["last_fired"] = stamp
            # Record WHICH session it was delivered to, so the same advisory is not
            # re-injected on every matching PreToolUse for the rest of the session. This is
            # delivery, not corroboration: a guard that merely MATCHED in K sessions has
            # earned nothing, and writing these ids into `seen_sessions` (the support list
            # `outcomes` seeds from) let the first feedback of any kind - a false positive
            # included - promote such a guard to blocking (review 2026-09-05). Support
            # sessions come only from feedback; `_mirror_legacy_counters` derives them.
                if session:
                    seen = g.get("delivered_sessions") or []
                    if session not in seen:
                        seen.append(session)
                        g["delivered_sessions"] = seen[-SEEN_SESSIONS_CAP:]
                touched = True
        return touched

    hit = _bump(guards)                 # the caller's own copy, so what it holds stays right
    if hit and persist:
        # persist regardless of who loaded the list: every check surface hands us the
        # ledger it already read (one load per event, critic 2026-07), and none of them
        # writes it back themselves - pass persist=False to batch externally. The write
        # re-reads under the lock so a concurrent tool call's delivery record is not lost.
        persist_under_lock(_bump, FIRED_LOCK_S)


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
# with `NEVERTWICE_GUARD_PACK=1` the pack is seeded at the next consolidation (or right away
# via `python -m nevertwice.guards pack`), pure stdlib, zero network. Pack guards are advisory and NEVER promote to blocking (they must never box the
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
        # F6, per unit of work: consolidate --apply runs this whole loop under the vault lock
        # and one call can cost ~33 s on the Ollama fallback, so a store with a large
        # undistilled backlog crosses the LOCK_STALE_S*10 ceiling and a concurrent hook
        # reclaims a lock whose holder is still working. refresh_lock() is a no-op when we
        # do not hold the lock, so the hot-path and CLI callers are unaffected.
        m.refresh_lock()
        if register(guards, g):
            added += 1
    if added:
        save_guards(guards)
    return added


# ── CLI ───────────────────────────────────────────────────────────────

def _print_hits(hits, *, why=False, text="", project=None, deep=False, as_json=False):
    """Render the hits. `why=True` renders the full WhyFired object through
    `why_fired.render`, which the MCP surface also calls - one formatter, so the two cannot
    drift into giving different answers to the same question (GOAL D3)."""
    if as_json:
        import json
        wf = _sibling("why_fired")
        print(json.dumps(wf.explain_hits(hits, text, project=project, deep=deep)
                         if (wf and hits) else hits, indent=2, ensure_ascii=False))
        return
    if not hits:
        print("ok - no guard fires for this action.")
        return
    wf = _sibling("why_fired") if why else None
    explained = (wf.explain_hits(hits, text, project=project, deep=deep)
                 if wf is not None else [])
    if explained:
        for entry in explained:
            print(wf.render(entry))
    else:
        for h in hits:
            tag = "BLOCK" if h["status"] == "blocking" else "warn "
            print(f"  [{tag}] ({h['id']}) {h['message']}")
        if why and wf is None:
            print("  (--why is unavailable: why_fired.py is not present in this install)")
    if any(h["status"] == "blocking" for h in hits):
        print("  → a blocking guard fired. Comply, or override: "
              "guards feedback <id> false_positive --reason \"...\"")


def main():
    argv = sys.argv[1:]
    if not argv:
        print("usage: guards check <text> [--why] [--json] [--deep] | list | "
              "feedback <id> <helped|false_positive> [--reason ..] | "
              "generate [--project P] [--limit N] | pack [--count]")
        return
    cmd = argv[0]
    if cmd == "pack" and "--count" in argv:
        # read-only: the size of the shipped pack, for the evidence register. `pack` alone
        # installs it into the live ledger, which is not something a claim's command may do.
        print(len(_UNIVERSAL_GUARDS))
        return
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
        project = m.argval(argv, "project")
        hits = check(text, project=project, path=m.argval(argv, "path"))
        _print_hits(hits, why="--why" in argv, text=text, project=project,
                    deep="--deep" in argv, as_json="--json" in argv)
    elif cmd == "list":
        guards = load_guards()
        live = [g for g in guards if g["status"] != "retired"]
        print(f"{len(live)} live guard(s) ({len(guards)} total incl. retired):")
        _outcomes = _sibling("outcomes")
        for g in sorted(guards, key=lambda x: (x["status"], -x["fired"])):
            earned = ""
            if _outcomes is not None:
                s = _outcomes.summary(g)
                p, o = s["precision"], s["override_rate"]
                # `fired` stays on the line but never enters `earned`: how often a pattern
                # matched is not evidence that the warning was worth reading.
                earned = (f" prec={p['point']}[{p['low']}-{p['high']}]"
                          if p["point"] is not None else " prec=-")
                earned += f" override={o['point']}" if o["point"] is not None else " override=-"
                earned += (f" sessions={s['distinct_sessions']['support']}+"
                           f"/{s['distinct_sessions']['against']}-")
            print(f"  ({g['id']}) [{g['status']:8}] fired={g['fired']}{earned}"
                  f"  {g['message'][:60]}")
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


def already_delivered(guard: dict, session: str | None) -> bool:
    """True when this guard already fired for this session.

    Callers use it to stay silent on a repeat. A guard that has said its piece once has
    said it; repeating the same advisory on every matching tool call in a session is pure
    token cost with no new information. Read from `delivered_sessions`, never from the
    support list: the two mean different things (see `record_fired`).
    """
    if not session:
        return False
    return session in (guard.get("delivered_sessions") or ())


def forget_delivery(session: str | None, guards=None, persist=True) -> int:
    """Drop `session` from every guard's delivery record; returns how many changed.

    PreCompact calls this: compaction wipes an advisory out of the agent's context, so a
    suppression keyed on the session id would keep the guard silent for the rest of the
    session precisely when its warning is no longer in front of the agent.
    """
    if not session:
        return 0
    guards = load_guards() if guards is None else guards

    def _drop(rows) -> int:
        n = 0
        for g in rows:
            seen = g.get("delivered_sessions") or []
            if session in seen:
                g["delivered_sessions"] = [s for s in seen if s != session]
                n += 1
        return n

    changed = _drop(guards)             # the caller's own copy
    if changed and persist:
        persist_under_lock(_drop, FORGET_LOCK_S)
    return changed
