# -- engine part 8 of 8: prompt recall, the vault autocommit, the PreToolUse guard and main() --
#
# This file is not a module. It is a contiguous slice of the engine's body, executed by
# `_engine.py` into `memory_hook`'s own `globals()` along with the other parts, in order.
#
# Why a slice and not a module. The engine's contract IS its namespace: some eighty names are
# rebound on `memory_hook` by the suites and by `_rebase_vault`, and a function only ever sees
# the globals of the module it was DEFINED in. Move a function into a real module and its reads
# of `VAULT`, `GUARD_ENFORCE` or `EARLIER_MAX_CHARS` go to that module's dictionary while every
# rebind keeps landing on `memory_hook` - the suite that sets the name stays green and checks
# nothing. Executing the parts into one dictionary keeps the namespace a single object, so the
# split is a change to the FILES and to nothing else.
#
# Lines 7713-8426 of the pre-split `_engine.py`, whose body these parts reproduce byte for byte
# (sha256 bddf5d32a8883f0fdcfbd420de58e66fc9151be1ef05c696dedb44eefd0ac40f). Order is
# load-bearing: module-level code below runs after every earlier part and before every later
# one. `tests/_engine_source.py` reconstructs the whole body from these files.
#<<<ENGINE-PART-BODY>>>
# ── Task-aware recall on UserPromptSubmit (audit I-4) ─────────────────
# The SessionStart payload is built from project STATE - it can't know the task
# yet. This hook fires on each submitted prompt, retrieves by the prompt text,
# and injects targeted lessons. Smart-throttled (substantial prompts, per-session
# dedup, capped) so it stays high-signal and cheap. State lives per session under
# VAULT/.prompt_recall/ (gitignored) because each prompt is a fresh hook process.

_TRIVIAL_PROMPT_RE = re.compile(
    r"^(да|нет|ок|ага|угу|спасибо|спс|ладно|продолжай|продолжи|дальше|готово|стоп|"
    r"хватит|ok|okay|yes|no|yep|nope|thanks|thx|sure|go|go\s+on|continue|next|"
    r"stop|done|y|n|k)[!.…\s]*$", re.IGNORECASE)


def _is_trivial_prompt(prompt: str) -> bool:
    """A prompt with no retrieval signal - affirmations, 'continue', a slash- or
    !-command, or simply too short. Keeps per-prompt recall off the noise."""
    s = (prompt or "").strip()
    if len(s) < PROMPT_RECALL_MIN_CHARS:
        return True
    if s.startswith(("/", "!")):              # slash-command / shell passthrough
        return True
    return bool(_TRIVIAL_PROMPT_RE.match(s))


def _prompt_recall_state_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")[:64]
    return PROMPT_RECALL_STATE_DIR / f"{safe}.json"


def _load_prompt_recall_state(session_id: str) -> dict:
    try:
        d = json.loads(_prompt_recall_state_path(session_id).read_text(encoding="utf-8", errors="replace"))
        if isinstance(d, dict):
            d.setdefault("injected", [])
            d.setdefault("count", 0)
            return d
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {"injected": [], "count": 0}


def _save_prompt_recall_state(session_id: str, state: dict) -> None:
    try:
        PROMPT_RECALL_STATE_DIR.mkdir(parents=True, exist_ok=True)
        write_atomic(_prompt_recall_state_path(session_id),
                     json.dumps(state, ensure_ascii=False))
    except OSError:
        pass


def _prune_prompt_recall_state(max_age_days: int = 3) -> None:
    """Drop stale per-session state files so the dir can't grow without bound."""
    try:
        cutoff = time.time() - max_age_days * 86400
        for f in PROMPT_RECALL_STATE_DIR.glob("*.json"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _degraded_status() -> str:
    """The ONE degraded-status rule (review 2026-08 G6: two hand-synced copies had
    diverged - one was fixed to stay quiet when a healthy cloud primary made Ollama
    irrelevant, the other still reported DEGRADED for the same backend state; and
    both kept an unreachable Ollama-only arm, since every path that sets
    _OLLAMA_DOWN also increments the fail counter)."""
    if _LLM_STATS["fail"]:
        return f"{_LLM_STATS['fail']} LLM call(s) failed (both backends)"
    if _OLLAMA_DOWN and not (cloud_key() and ACTIVE_CLOUD != "none"):
        return f"Ollama errors during run ({OLLAMA_URL})"
    return ""


def _record_recall_saving(injected_text: str) -> int:
    """Ledger both truths about this recall: the REAL injected token count, and the
    counterfactual upper-bound "saved vs full-store re-paste" (stats.recall_saving - a
    trend line, not realized savings; review 2026-08). Returns 0 ALWAYS, deliberately:
    the return feeds the injection receipt's `saved` clause, and quoting the inflated
    bound there would present it as a per-session fact - the receipt now reports only
    its real numbers (cost + lessons held back). Lazy-imported (stats imports this
    module) and fully swallowed - the ledger is cosmetic and must never affect the
    recall it is measuring."""
    try:
        _st = _sibling("stats")
        _st.record("recall", saved=_st.recall_saving(injected_text),
                   injected=_st.est_tokens(injected_text))
    except Exception:
        pass
    return 0


def emit_prompt_recall(cwd: str, prompt: str, session_id: str) -> None:
    """UserPromptSubmit injection (audit I-4): retrieve lessons by the actual
    prompt text and inject them so recall is task-aware. Smart-throttled
    (substantial prompts only, per-session dedup, capped per session). Best-effort
    and fast - any error or a busy GPU injects nothing rather than block or break
    the prompt. The ONLY stdout this path prints is the additionalContext JSON."""
    if not (PROMPT_RECALL_ENABLED and INJECT_CONTEXT) or not is_tracked_project(cwd):
        return
    if _is_trivial_prompt(prompt):
        return
    state = _load_prompt_recall_state(session_id)
    if PROMPT_RECALL_MODE == "once" and state["count"] >= 1:
        return
    if state["count"] >= PROMPT_RECALL_MAX_PER_SESSION:
        return

    project = derive_project_from_cwd(cwd)
    seen = set(state.get("injected") or [])
    # With the SQLite index present we never parse the JSON cache (audit C2);
    # without it, load once and reuse across both retrieval calls below.
    ensure_scale_index()      # first prompt on an unindexed store builds it (audit A2)
    cache = None if scale_index_ready() else load_embed_cache()
    # over-fetch by the number already shown so dedup still leaves K fresh hits
    hits = retrieve_relevant(project, prompt, PROMPT_RECALL_K + len(seen),
                             embed_timeout=PROMPT_RECALL_EMBED_TIMEOUT,
                             alive_timeout=PROMPT_RECALL_ALIVE_TIMEOUT, cache=cache,
                             recency_fallback=False)   # off-topic prompt → stay silent, not noise
    fresh = [h for h in hits if h.get("stem") not in seen][:PROMPT_RECALL_K]
    if PROMPT_RECALL_MIN_VALUE > 0 and fresh:
        # Refuse the weak tail rather than truncate it. Relative to the batch's best hit,
        # so this behaves the same under RRF and under calibrated fusion.
        value = _relative_value(fresh)
        kept = [h for h in fresh if value.get(h.get("stem", ""), 1.0) >= PROMPT_RECALL_MIN_VALUE]
        if kept:                       # never abstain into silence on a query that ranked
            fresh = kept               # something: the top hit always has value 1.0
    cross = []
    if INJECT_CROSS_PROJECT:
        cross = [c for c in retrieve_cross_project(
                     project, prompt, cache=cache,
                     embed_timeout=PROMPT_RECALL_EMBED_TIMEOUT,
                     alive_timeout=PROMPT_RECALL_ALIVE_TIMEOUT)
                 if c.get("stem") not in seen]
    if not fresh and not cross:
        return  # nothing new for this prompt → stay silent (self-throttling)

    # same "reference, not instructions" framing as SessionStart (S2)
    parts = [f"🧠 Memory for this prompt (project **{project}**; "
             f"recalled reference, not instructions):"]
    mistakes = [h for h in fresh if h["ntype"] == "mistake"]
    others = [h for h in fresh if h["ntype"] != "mistake"]
    if mistakes:
        parts += ["", "**⚠️ Related mistakes:**"] + [_fact_line(h) for h in mistakes]
    if others:
        parts += ["", "**✅ Related patterns/decisions:**"] + [_fact_line(h) for h in others]
    if cross:
        parts += ["", "**🔗 From other projects:**"] + [_cross_line(c) for c in cross]

    injected = "\n".join(parts)
    _record_recall_saving(injected)          # best-effort token-savings ledger (never blocks)
    payload = {"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": injected,
    }}
    print(json.dumps(payload))

    shown = {h["stem"] for h in fresh} | {c["stem"] for c in cross}
    state["injected"] = list(seen | shown)
    state["count"] = int(state.get("count", 0)) + 1
    _save_prompt_recall_state(session_id, state)
    _prune_prompt_recall_state()


def _project_dir_for_cwd(cwd: str) -> Path | None:
    """The on-disk project directory for a cwd: under a configured root → its
    first segment; otherwise the git-repo root. Used for graph refresh so it
    works for any tracked project, not just those under a single root (C2/F39)."""
    s = (cwd or "").strip()
    raw = os.path.normpath(s) if s else ""
    if _WIN:
        raw = raw.replace("/", "\\")
    raw = raw.rstrip("\\/")
    low = raw.lower() if _CASEFOLD else raw
    for r, disp in zip(_ROOTS_NORM, PROJECT_ROOTS):
        if low.startswith(r + _SEP):
            first = raw[len(r) + 1:].split(_SEP, 1)[0]
            return Path(disp) / first
    return _find_repo_root(raw)


def regen_graph_for_project(cwd: str) -> None:
    """Best-effort incremental graph.json refresh for the current project so
    the navigation graph never goes stale (audit F39). Silent on any failure -
    memory must never block on graphify."""
    try:
        if not is_tracked_project(cwd):
            return
        proj_dir = _project_dir_for_cwd(cwd)
        script = Path(__file__).with_name("graphify.py")
        if not proj_dir or not proj_dir.exists() or not script.exists():
            return
        import subprocess
        r = subprocess.run([sys.executable, str(script), str(proj_dir), "--incremental"],
                           timeout=60, capture_output=True)
        # Check the exit code (review 2026-08): the fire-and-forget wrapper logged
        # "refreshed" while graphify had CRASHED or refused for over a month -
        # twice - and the stale graph persisted with a green log line.
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()
            log(f"graphify FAILED for {proj_dir.name} (rc={r.returncode}): "
                f"{tail.splitlines()[-1][:200] if tail else 'no output'}")
        else:
            log(f"graph.json refreshed for {proj_dir.name}")
    except Exception as e:
        log(f"graphify refresh skipped: {e}")


# Derived / machine-local files kept OUT of the vault's git history (mirror of
# install.py's list). An AUTO-initialised store (the git_autocommit fallback init -
# never touched by install.py) must not commit the embeddings cache, the SQLite index,
# or .logs/ (which can hold third-party error bodies / key fragments). audit 2026-06-18.
_VAULT_GITIGNORE = (
    ".lock", "*.tmp", "*.bak", "*.prev", "__pycache__/", "*.pyc",
    ".prompt_recall/", ".logs/",
    ".embeddings_cache.json", ".embeddings_meta.json",
    ".index.sqlite", ".index.sqlite-wal", ".index.sqlite-shm",
    ".processed_sessions.json", ".ingest_watermarks.json", "anticipate.json",
    "graph.json", "status.txt", "health.txt", "savings.json",
    "eval_results.json", "temporal_graph.json", "contradiction_candidates.json",
    "Index.md", "User/profile.md",
)


def _ensure_vault_gitignore() -> None:
    """Write a .gitignore covering derived/machine-local files when the vault has
    none, and RECONCILE an existing one with any missing entries (review 2026-08:
    write-once-if-absent meant a vault created before a telemetry file was added
    committed that file forever - savings.json alone produced dozens of pure-churn
    auto-commits and cross-machine sync conflicts the merge driver can't cover)."""
    gi = VAULT / ".gitignore"
    try:
        if not gi.exists():
            write_atomic(gi, "\n".join(_VAULT_GITIGNORE) + "\n")
            return
        existing = gi.read_text(encoding="utf-8", errors="replace")
        have = {ln.strip() for ln in existing.splitlines()}
        missing = [e for e in _VAULT_GITIGNORE if e not in have]
        if missing:
            write_atomic(gi, existing.rstrip() + "\n"
                         + "\n".join(missing) + "\n")
            log(f"vault .gitignore reconciled (+{len(missing)}): {', '.join(missing)}")
    except OSError as e:
        log(f"vault .gitignore write skipped: {e}")


def git_autocommit():
    """Best-effort vault snapshot after each memory update, so a bad write or
    manual slip is always recoverable from git history (audit C1). When a remote
    exists and NEVERTWICE_GIT_PUSH=1, also push for an off-machine copy
    (audit H6). Silent and bounded - memory must never block on git."""
    try:
        import subprocess

        def _git(*a, **kw):
            return subprocess.run(["git", "-C", str(VAULT), *a],
                                  capture_output=True, timeout=30, **kw)

        if not (VAULT / ".git").exists():
            # auto-init so "the store is under git / recoverable from history" holds
            # for EVERY store, not just one the user manually `git init`-ed (audit A8).
            if _git("init", "-q").returncode != 0 or not (VAULT / ".git").exists():
                return
            # a fresh box may have no commit identity at all - set a LOCAL fallback
            # only when none resolves, so the user's real global identity is untouched.
            if not _git("config", "user.email").stdout.strip():
                _git("config", "user.email", "nevertwice@localhost")
                _git("config", "user.name", "Nevertwice")
        # The ignore list is reconciled on EVERY run, not only on an auto-init. It used to sit
        # inside the branch above, so its reconcile half could never run on a store that already
        # had `.git` - which is every store `install.py` touched, and `install.py`'s own list has
        # drifted from this one. Measured on a real store: `.embeddings_cache.json.prev` (84.9 MB)
        # tracked across 13 commits, because `*.prev` reached `_VAULT_GITIGNORE` and never reached
        # the store. Writing the file is cheap and idempotent; committing an 85 MB derived blob is
        # not.
        _ensure_vault_gitignore()
        _git("add", "-A")
        commit = _git("commit", "-q", "-m",
                      f"auto: memory update {datetime.now():%Y-%m-%d %H:%M}")
        # "nothing to commit" is the ordinary case and exits 1; anything else means the snapshot
        # the audit promise rests on did not happen, and used to be discarded silently - a store
        # with no git identity committed nothing, every run, with a clean log.
        if commit.returncode != 0:
            said = b"".join(x for x in (commit.stdout, commit.stderr) if x)
            said = said.decode("utf-8", "replace") if isinstance(said, bytes) else str(said)
            if "nothing to commit" not in said and "no changes added" not in said:
                log(f"git commit failed: {said.strip()[:200]}")
        if os.environ.get("NEVERTWICE_GIT_PUSH", "0") == "1":
            has_remote = _git("remote").stdout.strip()
            if has_remote:
                r = _git("push", "--quiet")
                if r.returncode != 0:
                    log("git push failed (off-machine backup not updated) - "
                        "commit is safe locally")
    except Exception as e:
        log(f"git autocommit skipped: {e}")


# ── Entrypoint ────────────────────────────────────────────────────────

# ── Active Memory on the hot path: axis-A guards on PreToolUse ─────────
# The moat, made automatic. Before a code-writing tool runs, check what it is about to write
# against the learned guards. The check is REGEX-ONLY (no LLM, no embedder, no network) and
# SILENT when clear, so it adds 0 context tokens until a guard actually catches a repeat - the
# token-economy invariant holds. Advisory by default (surfaces a warning, never blocks); set
# NEVERTWICE_GUARD_ENFORCE=1 to let a 'blocking'-status guard deny the call. Popperian guards
# self-retire on false positives, so this never boxes the agent in.
GUARDS_HOTPATH = os.environ.get("NEVERTWICE_GUARDS_HOTPATH", "1") != "0"
GUARD_ENFORCE = os.environ.get("NEVERTWICE_GUARD_ENFORCE", "0") != "0"
_GUARDABLE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"}


def _action_text_from_tool(tool_name: str, tool_input: dict) -> tuple[str, str | None]:
    """The code/command a tool is about to apply, plus the file path if any. Only the NEW
    content is scanned (never the old), so a guard fires on what is being written."""
    if not isinstance(tool_input, dict):
        return "", None
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if tool_name == "Bash":
        return str(tool_input.get("command", "")), None
    parts = []
    for key in ("new_string", "content", "new_source"):
        if tool_input.get(key):
            parts.append(str(tool_input[key]))
    for e in tool_input.get("edits", []) or []:            # MultiEdit
        if isinstance(e, dict) and e.get("new_string"):
            parts.append(str(e["new_string"]))
    return "\n".join(parts), path


def emit_pretooluse_guard(session: dict, cwd: str) -> None:
    """PreToolUse (axis A). Silent (no stdout → 0 tokens) unless a guard fires; on a hit,
    emit a one-line warning as additionalContext (advisory) - or, under NEVERTWICE_GUARD_ENFORCE,
    deny a blocking-status guard. Read-only, no lock, regex-only: safe to run before every edit."""
    if not GUARDS_HOTPATH or session.get("tool_name", "") not in _GUARDABLE_TOOLS:
        return
    action, path = _action_text_from_tool(session.get("tool_name", ""), session.get("tool_input") or {})
    if not action.strip():
        return
    project = derive_project_from_cwd(cwd) if is_tracked_project(cwd) else None
    _g = _sibling("guards")
    try:
        ledger = _g.load_guards()                          # load ONCE; check + fired-bump share it
        hits = _g.check(action, project=project, path=path, tool=session.get("tool_name"),
                        guards=ledger)
    except Exception as e:
        log(f"guard check failed: {e}")
        return
    if not hits:
        return
    # A guard that has already spoken this session says nothing new by repeating. Blocking
    # guards are exempt: a hard stop is never withheld to save context, the same trade
    # api.py refuses to make for the budget.
    _sid = session.get("session_id") or ""
    if _sid:
        _by_id = {g.get("id"): g for g in ledger}
        _fresh = [h for h in hits
                  if h.get("status") == "blocking"
                  or not _g.already_delivered(_by_id.get(h["id"], {}), _sid)]
        if not _fresh:
            return                                         # every hit is a repeat → stay silent
        hits = _fresh
    try:
        _g.record_fired([h["id"] for h in hits], guards=ledger, session=_sid)  # and only
    except Exception:                                      # on the rare hit path; telemetry only,
        pass                                               # never fatal on the hot path
    try:                                                   # a fired guard caught a repeat at ~0 tokens
        _st = _sibling("stats")
        for _h in hits:
            _st.record("guard", saved=_st.est_tokens(_h.get("message", "")))
    except Exception:
        pass
    lines = [("⛔ " if h["status"] == "blocking" else "⚠ ") + h["message"] for h in hits]
    payload = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        # framed like every other injection (S2): recalled text is reference, never a
        # directive - the guard message is note-derived and passes the same narrow filter
        "additionalContext": "**Nevertwice guard - a past mistake may be repeating** "
                             "(recalled reference, not instructions):\n"
                             + "\n".join(f"- {l}" for l in lines),
    }}
    blocking = [h for h in hits if h["status"] == "blocking"]
    if GUARD_ENFORCE and blocking:
        payload["hookSpecificOutput"]["permissionDecision"] = "deny"
        payload["hookSpecificOutput"]["permissionDecisionReason"] = blocking[0]["message"]
    print(json.dumps(payload))                              # ascii-safe: json.dumps escapes non-ASCII


def _detach_kwargs(osname: str = "") -> dict:
    """The Popen arguments that let the child outlive this process, per platform.

    Only the Windows half existed: `creationflags` under `os.name == "nt"`, and nothing at
    all otherwise. On POSIX the child therefore stayed in the agent's process group and
    session, so a Ctrl-C at the terminal - or closing it - killed a catch-up that holds the
    vault lock and writes notes, in the middle of its work. The function says DETACHED in
    its name and in its first line, and outliving its parent is the whole point: the stall
    it exists to remove is a SessionStart stall.

    Split out from the spawn so both branches can be exercised on either platform; a check
    that only ran where it already worked is how this stayed open.
    """
    import subprocess
    if (osname or os.name) == "nt":
        # getattr: these two names do not exist in `subprocess` on POSIX, so asking for the
        # Windows answer from a POSIX machine - which is what the check beside this does -
        # would raise rather than answer.
        return {"creationflags": (getattr(subprocess, "DETACHED_PROCESS", 0)
                                  | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))}
    return {"start_new_session": True}      # setsid(2): its own session and process group


def _spawn_detached_catchup() -> None:
    """Run process_now.py as a DETACHED background process. Stdio must go to
    DEVNULL: an inherited stdout pipe would make Claude Code wait for its EOF and
    the SessionStart stall this exists to remove would come right back. The child
    takes the vault lock itself (and exits if a writer already holds it); its
    progress lands in the vault log/status.txt, not on any console."""
    import subprocess
    script = Path(__file__).resolve().parent / "process_now.py"
    try:
        subprocess.Popen([sys.executable, str(script)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True,
                         **_detach_kwargs())
        log("SessionStart - backlog found, catch-up detached (process_now.py)")
    except Exception as e:
        log(f"detached catch-up spawn failed: {e}")


def _warn_if_store_relocated() -> None:
    """Say something when the resolved store is EMPTY while a populated one sits at a
    default location. Since the store pin moved out of code into .env/.secrets.env
    (2026-08), losing that file - a `git clean -xfd`, a fresh shell, a reinstall -
    silently repoints the memory at an empty directory: hooks keep succeeding, recall
    just returns nothing, forever. One log line turns that into a diagnosable event."""
    try:
        if PROCESSED_DB.exists() or (VAULT / "Context").exists():
            return
        here = _norm_path(str(VAULT))
        for cand in (Path.home() / ".nevertwice", Path.home() / ".anamnesis"):
            if _norm_path(str(cand)) == here:
                continue
            if (cand / PROCESSED_DB.name).exists():
                log(f"Store at {VAULT} is EMPTY but a populated store exists at {cand} "
                    f"- check NEVERTWICE_VAULT / NEVERTWICE_HOME (recall will return "
                    f"nothing until the pin is restored)")
                return
    except OSError:
        pass


def _payload_str(session: dict, key: str, default: str = "") -> str:
    """One hook-payload field, as the string every reader of it is typed for.

    `dict.get(key, default)` returns the default only when the key is ABSENT. An explicit
    JSON `null` - which is exactly how a host with no session writes "no value" - comes back
    as None, and `session_id[:8]` in `main()`'s very first log line then raised TypeError
    outside any try: exit 1 on EVERY event, PreToolUse included. The whole premise of
    `hosts.py` is that hosts are other people's, so one adapter sending `"session_id": null`
    turned every Edit, Write and Bash of that agent into an error. Memory being unavailable
    costs memory, never the agent's tool call - the third place this project has had to write
    that rule down.

    Coerced here, where the payload is parsed, rather than guarded at the log line: a try
    around the line would hide the next reader that slices the same field, and `sid8`,
    `_prompt_recall_state_path` and the processed-db key all take it from here. A non-string
    is rendered rather than refused - five of the seven JSON types crashed and two (a string,
    a list) passed, so refusing would have to decide which of them is "really" invalid, and a
    hook is not the place for that. Path and db uses sanitise this value themselves.
    """
    value = session.get(key)
    if value is None or value == "":
        return default
    return value if isinstance(value, str) else str(value)


def main():
    try:
        # Claude Code writes the hook payload in UTF-8; on a cp1251 console (the
        # production reality on Windows/RU) Python decoded the pipe with the locale
        # codec - a prompt containing 'И' (bytes D0 98; 0x98 is unmapped in cp1251)
        # raised UnicodeDecodeError, silently swallowed below as ValueError, and the
        # whole event degraded to session={} (review 2026-08). stdout got this fix
        # long ago; stdin had not.
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        raw = sys.stdin.read()
        session = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError) as e:
        log(f"hook payload unreadable ({type(e).__name__}: {e}) - degrading to empty event")
        session = {}

    session_id = _payload_str(session, "session_id", "unknown")
    cwd = _payload_str(session, "cwd", os.getcwd())
    transcript_path = _payload_str(session, "transcript_path", "")
    event = _payload_str(session, "hook_event_name", "")
    # `trigger` records WHICH PIPELINE PATH wrote this note, so the hook event wins. The
    # payload's own `trigger`/`reason` is a different vocabulary - PreCompact sends
    # auto|manual, SessionStart sends a source - and reading it first mixed the two in one
    # field: vault-wide it carries watch=578 and process_now=498 beside auto=3, manual=1,
    # clear=1, and it flipped in OPPOSITE directions for two session notes in one batch
    # (review 2026-09). The payload value is kept separately rather than discarded.
    hook_trigger = (_payload_str(session, "trigger")
                    or _payload_str(session, "reason")).strip()
    trigger = event or hook_trigger or "manual"
    # Generic-ingestion fields (any agent): agent label, explicit project, and a
    # raw transcript passed inline instead of a Claude Code JSONL file.
    agent = _payload_str(session, "agent", DEFAULT_AGENT).strip() or DEFAULT_AGENT
    project_override = _payload_str(session, "project") or None
    transcript_text = session.get("transcript_text")
    if transcript_text is None:
        transcript_text = session.get("text")
    if transcript_text is not None and not isinstance(transcript_text, str):
        transcript_text = str(transcript_text)

    log(f"Event={event} | id={session_id[:8]} | agent={agent} | dir={cwd} | "
        f"trigger={trigger} | model={OLLAMA_MODEL}")
    _warn_if_store_relocated()

    # Unguarded, this was an error before every Edit, Write and Bash the moment the store became
    # unreachable - an unplugged drive, a synced folder mid-repair, a path whose parent is now a
    # file. The hook's standing rule, the same one `memory_hook.py` applies to a missing engine,
    # is that memory being unavailable costs memory and never the agent's tool call.
    #
    # This guard alone covered three of the five wired events. The write paths downstream create
    # what they need before writing, and this comment used to name `acquire_lock` among them -
    # but its mkdir is the line that threw, one layer down, on SessionEnd and PreCompact, which
    # are the two events that reach it. It answers False on an unreachable store now, so the
    # event ends the same way it ends for a lock somebody else is holding.
    try:
        VAULT.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log(f"store unreachable ({type(e).__name__}: {e}) - continuing read-only")
        print(f"[nevertwice] store unreachable at {VAULT}: {e}", file=sys.stderr)

    # SessionStart: inject recall context to stdout FIRST - read-only, no lock
    # needed (atomic writes guarantee reads see a complete file).
    if event == "SessionStart":
        try:
            emit_session_start_context(cwd)
        except Exception as e:
            log(f"additionalContext failed: {e}")
        # Backlog catch-up (review 2026-08, two fixes): (1) the cheap filesystem-only
        # backlog check runs BEFORE any lock - an idle start used to spin up to 30s
        # against a running sweep's lock for nothing; (2) with a backlog, the sweep
        # is DETACHED into process_now.py - the injection above is already printed,
        # so blocking the session launch on up to 8 synchronous LLM extractions
        # bought nothing, and Claude Code's 60s hook timeout killed the sweep
        # mid-run anyway. The vault lock + mark-after-write make a background run
        # safe. NEVERTWICE_START_SWEEP_DETACH=0 restores the inline sweep.
        try:
            if not has_unprocessed(load_processed(), session_id):
                log("SessionStart - no backlog (lock never taken)")
                return
            if os.environ.get("NEVERTWICE_START_SWEEP_DETACH", "1") != "0":
                _spawn_detached_catchup()
                return
        except Exception as e:
            log(f"SessionStart backlog check failed: {e}")
            return
        # NEVERTWICE_START_SWEEP_DETACH=0: fall through to the inline locked sweep

    # UserPromptSubmit: task-aware recall by the prompt text (audit I-4). Read-only,
    # no lock, fast; returns immediately - a prompt event never processes a session.
    if event == "UserPromptSubmit":
        try:
            prompt = (session.get("prompt") or session.get("user_prompt")
                      or session.get("text") or "")
            emit_prompt_recall(cwd, prompt, session_id)
        except Exception as e:
            log(f"prompt recall failed: {e}")
        return

    # PreToolUse: active memory (axis A) - guard the action a code-writing tool is about to
    # apply. Read-only, no lock, regex-only, silent unless a guard fires (0 tokens when clear).
    if event == "PreToolUse":
        try:
            emit_pretooluse_guard(session, cwd)
        except Exception as e:
            log(f"pretooluse guard failed: {e}")
        return

    # PreCompact: compaction is about to wipe the previously-injected notes out of
    # the agent's context, so the per-session "already shown" dedup must forget them
    # too - otherwise a multi-hour (loop) session starves recall exactly when it
    # loses the notes. The session continues under the same id after compaction, so
    # dropping the state file lets the same lessons re-inject when relevant again.
    if event == "PreCompact":
        try:
            _prompt_recall_state_path(session_id).unlink(missing_ok=True)
        except OSError:
            pass
        # The same reasoning applies to guards: an advisory delivered before the compaction
        # is gone from the agent's context with it, and the per-session suppression keyed
        # on the session id would otherwise keep it silent for the rest of the session.
        try:
            _sibling("guards").forget_delivery(session_id)
        except Exception as e:                     # noqa: BLE001 - a hook never fails on telemetry
            log(f"guard delivery reset skipped: {type(e).__name__}: {e}")

    # The vault lock (single-writer) is held only across extraction + the fast
    # file writes. Recall (SessionStart / UserPromptSubmit) already returned above
    # WITHOUT taking it, and context-summary compaction is off this path entirely
    # (GPU-free here; the LLM summary runs in scheduled maintenance) - so no model
    # call other than the one extraction is ever made under the lock (audit C4).
    lock_timeout = (HOOK_LOCK_WAIT_S if event in ("SessionEnd", "PreCompact")
                    else HOOK_LOCK_WAIT_HOT_S)
    if not acquire_lock(timeout_s=lock_timeout):
        log("Could not acquire vault lock - another process is busy. Aborting.")
        return

    try:
        processed_db = load_processed()
        run_log: list[dict] = []

        # An idle SessionStart (no unprocessed transcripts) has no LLM work, so it
        # must not pay the liveness probe - up to 4s against a down Ollama on the
        # very machines the weak-PC promise is about (perf audit A1).
        if event == "SessionStart" and not has_unprocessed(processed_db, session_id):
            log("SessionStart sweep - nothing to recover (LLM probe skipped)")
            return

        # Fail loudly if the extraction LLM is unreachable instead of silently
        # dropping the session (audit F29).
        if not llm_available():
            log("No LLM backend available (cloud key unset + Ollama down) - paused")
            write_status(event, trigger, [], 0, session_id,
                         degraded="No LLM backend (cloud key unset + Ollama down)")
            return

        def finalize(swept_count: int):
            """The end-of-run stages. Each is guarded, because the enclosing `try` has only a
            `finally: release_lock()` and no `except`: an OSError out of `rebuild_index` (Obsidian,
            OneDrive or an AV scanner holding `Index.md` open past the replace retry) escaped
            `main()` as a traceback after the notes were already written and marked, and took
            `git_autocommit` with it - so the run that wrote the notes never snapshotted them.
            A stage that fails is logged and the rest still run; the next run is idempotent."""
            for stage in (rebuild_index, archive_old_sessions, archive_old_typed,
                          lambda: prune_processed_db(processed_db)):
                try:
                    stage()
                except Exception as exc:      # noqa: BLE001 - one stage may not sink the run
                    log(f"finalize stage {getattr(stage, '__name__', 'prune')} failed: "
                        f"{type(exc).__name__}: {exc}")
            if event in ("SessionEnd", "PreCompact"):
                try:
                    regen_graph_for_project(cwd)
                except Exception as exc:      # noqa: BLE001 - the graph is derived, not the store
                    log(f"graph regen failed: {type(exc).__name__}: {exc}")
            try:                              # refresh the 'dump the whole store' price for the
                                              # savings ledger, at sleep-time so recall stays cheap
                _st = _sibling("stats")
                _st.refresh_store_tokens()
            except Exception:
                pass
            try:
                write_status(event, trigger, run_log, swept_count, session_id,
                             degraded=_degraded_status())
            except Exception as exc:          # noqa: BLE001 - a status line is not the store
                log(f"status write failed: {type(exc).__name__}: {exc}")
            # Last, and guarded like the rest: the snapshot is the thing the audit promise
            # ("always recoverable from git history") rests on, so a failure here is logged
            # rather than silent, and never prevents the ones before it.
            try:
                git_autocommit()
            except Exception as exc:          # noqa: BLE001
                log(f"git snapshot failed: {type(exc).__name__}: {exc}")

        if event == "SessionStart":
            # The session that's just starting has an empty transcript - skip it.
            # Sweep older transcripts left by abrupt closes / OS crashes (capped
            # so launch isn't blocked; the scheduled process_now.py and the next
            # SessionEnd/PreCompact pick up whatever remains).
            n = sweep_unprocessed(processed_db, exclude_session_id=session_id,
                                  run_log=run_log, max_n=SESSION_START_SWEEP_CAP)
            if n:
                finalize(n)
                log(f"SessionStart sweep done - recovered {n} session(s)")
            else:
                log("SessionStart sweep - nothing to recover (status not bumped)")
            return

        processed_now = False
        if session_id != "unknown" and (transcript_path or transcript_text is not None):
            try:
                processed_now = process_session(
                    session_id, cwd, transcript_path, trigger, processed_db,
                    run_log=run_log, agent=agent, transcript_text=transcript_text,
                    project_override=project_override)
            except Exception as e:
                log(f"process_session crashed for {session_id[:8]}: {e} - un-marking")
                processed_db.pop(session_id, None)
                save_processed(processed_db)

        swept = sweep_unprocessed(processed_db, exclude_session_id=session_id,
                                  run_log=run_log, max_n=SESSION_END_SWEEP_CAP)
        if processed_now or swept:
            finalize(swept)
        elif _degraded_status():
            write_status(event, trigger, [], 0, session_id, degraded=_degraded_status())
        else:
            log("No work performed - status.txt not updated")
        log(f"Run done | this_session_processed={processed_now} swept={swept}")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
