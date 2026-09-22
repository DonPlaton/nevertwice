# -- engine part 3 of 8: the advisory lock, gardening, the processed-session DB, transcripts, the model and embedder calls, the scale index --
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
# Lines 2004-3484 of the pre-split `_engine.py`, whose body these parts reproduce byte for byte
# (sha256 bddf5d32a8883f0fdcfbd420de58e66fc9151be1ef05c696dedb44eefd0ac40f). Order is
# load-bearing: module-level code below runs after every earlier part and before every later
# one. `tests/_engine_source.py` reconstructs the whole body from these files.
#<<<ENGINE-PART-BODY>>>
# ── Advisory lock (sentinel file, race-safe via O_EXCL) ───────────────

def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check for a lock-holder PID, cross-platform. Returns True when
    uncertain so we never steal a lock from a process that might be alive.

    POSIX (macOS/Linux): `os.kill(pid, 0)` - ESRCH means dead, EPERM means alive-but-not-ours
    (still alive). Windows: OpenProcess. Without the POSIX branch a crashed holder's lock could
    only be reclaimed by the age guard (up to LOCK_STALE_S), wedging every writer on Mac/Linux
    for minutes after a crash."""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True                    # signal delivered → alive
        except ProcessLookupError:
            return False                   # ESRCH → no such process (safe to reclaim)
        except PermissionError:
            return True                    # EPERM → exists, owned by another user
        except OSError:
            return True                    # uncertain → don't steal
    try:
        import ctypes
        PROCESS_QUERY_LIMITED = 0x1000
        k = ctypes.windll.kernel32
        h = k.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
        if not h:
            return False  # no such process
        k.CloseHandle(h)
        return True
    except Exception:
        return True


def _lock_file() -> Path:
    # Derived from VAULT at CALL time, never snapshotted at import: an import-time
    # `VAULT / ".lock"` constant diverges from a VAULT that tests (or embedders of the
    # library) repoint after import - the mkdir then targets the new vault while the
    # lock opens inside the old, missing one (FileNotFoundError on any fresh box).
    return VAULT / ".lock"


def acquire_lock(timeout_s: float = 30) -> bool:
    try:
        VAULT.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # An unreachable store cannot be locked, and this mkdir - not the guarded one in
        # `main()` - is the line that threw OSError out of SessionEnd and PreCompact when the
        # drive was gone. A lock we cannot take is already a "no" every caller handles, so say
        # no: the event ends with "could not acquire", the agent's tool call is untouched.
        log(f"vault lock: store unreachable ({type(e).__name__}: {e})")
        return False
    lock = _lock_file()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode())
            finally:
                os.close(fd)
            # Confirm ownership: on filesystems where O_EXCL isn't fully atomic
            # (some NTFS/SMB shares) two processes can both "create" the file; the
            # pid that survives in it is the real owner, the other backs off
            # instead of both proceeding into the critical section (audit LOW).
            try:
                if (lock.read_text() or "").strip() != str(os.getpid()):
                    time.sleep(LOCK_RETRY_S)
                    continue
            except OSError:
                pass
            return True
        except FileExistsError:
            # Only reclaim a stale lock whose holder PID is actually dead -
            # avoids two processes both deleting a fresh lock (audit F12 TOCTOU).
            try:
                age = time.time() - lock.stat().st_mtime
                try:
                    holder = int((lock.read_text() or "0").strip() or 0)
                except (ValueError, OSError):
                    holder = 0
                # Steal a lock whose holder is provably dead IMMEDIATELY - don't wait
                # out LOCK_STALE_S, or a crashed holder with a fresh mtime wedges every
                # writer for 10 min (audit A19). An unknown/empty pid (holder crashed
                # between create and pid-write) still falls back to the age guard. The
                # outer age ceiling (10× stale) breaks the one remaining wedge: a crashed
                # holder whose PID was RE-USED by an unrelated live process would otherwise
                # hold the lock forever (code-review 2026-07) - no legitimate hook run
                # lasts 100 minutes.
                reclaim = ((not _pid_alive(holder)) if holder > 0 else age > LOCK_STALE_S) \
                    or age > LOCK_STALE_S * 10
                if reclaim:
                    # Reclaim by atomic RENAME, never a blind unlink: between our
                    # stat/read and the delete another waiter may have already
                    # reclaimed and re-created the lock - a delayed unlink would then
                    # destroy the NEW owner's fresh lock and let two writers into the
                    # critical section (review 2026-08 TOCTOU). Exactly one contender
                    # wins the rename; losers see FileNotFoundError and re-loop.
                    stale = lock.with_name(f"{lock.name}.stale.{os.getpid()}")
                    try:
                        os.replace(lock, stale)     # replace: survives a leftover .stale
                    except OSError:
                        time.sleep(LOCK_RETRY_S)
                        continue
                    try:
                        stale.unlink(missing_ok=True)
                    except OSError:
                        pass
                    continue
            except (FileNotFoundError, OSError):
                pass
            time.sleep(LOCK_RETRY_S)
    return False


def _lock_holder_pid() -> str | None:
    """The pid the lock file records: "" when there is no file or it is empty, None when it
    holds something that is not a pid.

    One reader for the three that ask - `holds_lock`, `release_lock`, `refresh_lock`. Each
    called a bare `read_text()` under `except OSError`, and `UnicodeDecodeError` is a
    ValueError, not an OSError: a lock file holding bytes the platform codec cannot decode
    (the cp1251 default on Windows/RU, the same trap already fixed for stdin) escaped all
    three. The cost is not a bad log line - `guards.persist_under_lock` opens with
    `mine = not holds_lock()`, so every serialised ledger write died there before attempting
    anything, and died as a UnicodeDecodeError rather than the `LedgerBusy` each surface
    catches. `acquire_lock` writes only ASCII, so such a file has an outside cause: a write
    cut short, a syncing folder, another tool.

    Three answers, not two, because the doors that ask mean different things by them. "" is
    an ABSENT or EMPTY file, which `release_lock` and `refresh_lock` tolerate for the reason
    written beside them: a holder can crash between creating the file and writing its pid.
    None is a file that is there and is not a pid - not ours, and not to be touched. Folding
    the second onto the first put a file that is neither empty nor ours into the tolerant
    branch, so a process holding no lock UNLINKED it: two writers in one critical section,
    which is the failure the ownership rules exist to prevent, reached through a different
    door than the stale-steal of review 2026-08. The stale ceiling frees such a file, which
    is what that ceiling is for.

    A rule in one place rather than three patches, so the fourth reader gets it.
    """
    try:
        text = (_lock_file().read_text(encoding="utf-8", errors="replace") or "").strip()
    except OSError:
        return ""                    # absent, or unreadable at the OS level: as before
    if not text:
        return ""
    return text if text.isdigit() else None


def holds_lock() -> bool:
    """Does THIS process hold the vault lock right now?

    `acquire_lock` is not reentrant - it spins out its timeout against our own live pid - so a
    writer reached from inside a holder (the guard-generating pass under `consolidate --apply`)
    must take the lock only when it does not already have it, and must not release a critical
    section it did not open. Strict on purpose: a missing or unreadable lock is not ours, unlike
    `release_lock`/`refresh_lock`, which tolerate an empty file to stay compatible with a holder
    that crashed between create and pid-write.
    """
    return _lock_holder_pid() == str(os.getpid())


def release_lock():
    lock = _lock_file()
    try:
        # Only release a lock we still OWN: after a stale-steal the file belongs to
        # the new holder, and the old holder's unconditional unlink admitted a third
        # writer into the critical section (review 2026-08). A positively-foreign
        # pid means back off; an unreadable file keeps the old unlink behavior.
        held = _lock_holder_pid()
        if held is None or held not in ("", str(os.getpid())):
            return
    except OSError:
        pass
    try:
        lock.unlink(missing_ok=True)
    except OSError:
        pass


def refresh_lock() -> None:
    """Touch the lock's mtime, but ONLY while we still own it.

    Long LEGITIMATE holds exist (a 25-transcript sweep on the Ollama fallback, process_now over
    a big backlog) and the holder never updated the mtime, so past the 10x-stale ceiling a
    concurrent hook STOLE the lock from a live process (review 2026-08). Every per-transcript
    unit calls this.

    The ownership test is `release_lock`'s, and for the same reason: touching a lock we do not
    hold keeps somebody else's alive. `acquire_lock` frees a dead holder immediately by PID, so
    the one wedge a foreign refresh can create is also the one the `LOCK_STALE_S * 10` ceiling
    exists to break - a crashed holder whose PID has been re-used by an unrelated live process.
    A stray refresh resets that clock forever. `consolidate_memory`'s judge loop reaches this on
    a DRY RUN, holding no lock at all."""
    lock = _lock_file()
    try:
        held = _lock_holder_pid()
        if held is None or held not in ("", str(os.getpid())):
            return
    except OSError:
        pass
    try:
        os.utime(lock)
    except OSError:
        pass


# ── Gardening: archive old session notes, prune old DB entries ────────

def _archive_dest(arch: Path, name: str) -> Path:
    """Collision-safe destination in an Archive/-style folder: a bare
    os.replace(p, arch/p.name) silently OVERWROTE an already-archived note with
    the same stem (a re-mined old transcript re-creates old stems dated by the
    ORIGINAL session date) - destroying the earlier copy's merged fragments and
    provenance (review 2026-08). Suffix -2, -3, ... instead."""
    t = arch / name
    if not t.exists():
        return t
    stem, ext = os.path.splitext(name)
    for i in range(2, 100):
        t = arch / f"{stem}-{i}{ext}"
        if not t.exists():
            return t
    return arch / name


def archive_old_sessions(days: int | None = None) -> int:
    # Resolved HERE, not in the signature: a default argument is evaluated once at
    # def time, so a module constant frozen there stops answering to the module.
    days = ARCHIVE_AFTER_DAYS if days is None else days
    sess = VAULT / "Sessions"
    if not sess.exists():
        return 0
    arch = sess / "Archive"
    arch.mkdir(exist_ok=True)
    cutoff = (datetime.now() - timedelta(days=days)).date()
    moved = 0
    for p in sess.glob("*.md"):
        try:
            note_date = datetime.strptime(p.stem[:10], "%Y-%m-%d").date()
        except ValueError:
            try:
                note_date = datetime.fromtimestamp(p.stat().st_mtime).date()
            except OSError:
                continue
        if note_date >= cutoff:
            continue
        try:
            os.replace(p, _archive_dest(arch, p.name))   # atomic; collision-safe name
            moved += 1
        except OSError as e:
            log(f"Archive failed for {p.name}: {e}")
    if moved:
        log(f"Archived {moved} session note(s) older than {days}d")
    return moved


def archive_old_typed(days: int | None = None) -> int:
    """Move typed notes (Patterns/Mistakes/Decisions) older than `days` into a
    per-folder Archive/ subdir. Knowledge is preserved (moved, never deleted),
    but the live folders - and the dedup-grounding glob that scans them - stop
    growing without bound (audit F24). Obsidian resolves [[stem]] regardless of
    folder, so existing wikilinks keep working after the move."""
    # Resolved HERE, not in the signature: a default argument is evaluated once at
    # def time, so a module constant frozen there stops answering to the module.
    days = TYPED_ARCHIVE_AFTER_DAYS if days is None else days
    moved = 0
    archived_stems = []
    cutoff = (datetime.now() - timedelta(days=days)).date()
    for folder in TYPE_FOLDER.values():
        d = VAULT / folder
        if not d.exists():
            continue
        arch = d / "Archive"
        arch.mkdir(exist_ok=True)
        for p in d.glob("*.md"):
            try:
                note_date = datetime.strptime(p.stem[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if note_date >= cutoff:
                continue
            try:
                os.replace(p, _archive_dest(arch, p.name))   # atomic; collision-safe name
                moved += 1
                archived_stems.append(p.stem)
            except OSError as e:
                log(f"Typed archive failed for {p.name}: {e}")
    # keep the embedding cache in sync with live notes so archived titles stop
    # surfacing in SessionStart recall and the cache stays bounded (audit D2)
    if archived_stems:
        cache = load_embed_cache()
        if any(s in cache for s in archived_stems):
            for s in archived_stems:
                cache.pop(s, None)
            save_embed_cache(cache)
        sync_scale_index(delete=archived_stems)   # keep the SQLite index in sync
    if moved:
        log(f"Archived {moved} typed note(s) older than {days}d")
    return moved


def prune_processed_db(db: dict, days: int | None = None) -> int:
    # Resolved HERE, not in the signature: a default argument is evaluated once at
    # def time, so a module constant frozen there stops answering to the module.
    days = PRUNE_DB_AFTER_DAYS if days is None else days
    cutoff = datetime.now() - timedelta(days=days)
    pruned = 0
    stamped = 0
    for sid in list(db.keys()):
        entry = db[sid]
        if not isinstance(entry, dict):  # corrupt/legacy value - drop it
            del db[sid]
            pruned += 1
            continue
        try:
            t = datetime.fromisoformat(entry.get("processed_at", ""))
            # a tz-suffixed stamp (hand-edit / foreign writer) parses AWARE; comparing
            # it against the naive cutoff raised TypeError OUTSIDE the guard, and the
            # poisoned entry then wedged every finalize permanently (review 2026-08)
            if t.tzinfo is not None:
                t = t.astimezone().replace(tzinfo=None)
            stale = t < cutoff
        except (ValueError, TypeError):
            # No parseable date, so this entry cannot be aged out on evidence it does not
            # carry - and `continue` made it immortal, so the records that outlive a store are
            # exactly the ones nothing can reason about, in the DB the hook reads before every
            # session. Give it today's: kept this pass, ordinary from here on.
            entry["processed_at"] = datetime.now().isoformat(timespec="seconds")
            stamped += 1
            continue
        if stale:
            del db[sid]
            pruned += 1
    if pruned or stamped:
        save_processed(db)
        log(f"Pruned {pruned} old DB entries (>{days}d)"
            + (f"; dated {stamped} entry(ies) that carried none" if stamped else ""))
    return pruned


# ── Two-generation JSON state files (<name> + <name>.bak) ─────────────
# The shared shape of every state file that must survive a truncated write: processed-DB,
# embed meta, ingest watermarks, guards ledger, anticipate state. Each carried its own copy
# of this pair until the 2026-08 cleanup; both now live in `store_state.py` (GOAL E4) and are
# re-exported here, because these two names are imported by name across the package.

_load_json_generations = _store_state._load_json_generations
_save_json_generations = _store_state._save_json_generations


# ── Processed-sessions DB ─────────────────────────────────────────────

def load_processed() -> dict:
    """Falls back to the .bak generation if the primary file is missing or
    corrupt. Without this fallback a single truncated write made every session
    look unprocessed → reprocess storm with mass duplicate notes (audit F1/F30)."""
    return _load_json_generations(PROCESSED_DB, "processed-DB") or {}


def save_processed(db: dict):
    VAULT.mkdir(parents=True, exist_ok=True)
    _save_json_generations(PROCESSED_DB, json.dumps(db, ensure_ascii=False, indent=2))


def mark_processed(db: dict, session_id: str, transcript_path: str,
                   size: int | None = None):
    """Record a session as processed. `size` is the transcript's byte size AT THE
    MOMENT ITS CONTENT WAS READ (not at mark time - it may have grown during
    extraction); it is what lets _transcript_grew detect a post-compaction tail.
    None → best-effort stat now (callers that never re-visit, e.g. untracked cwd)."""
    entry = {
        "transcript": transcript_path,
        "processed_at": datetime.now().isoformat(timespec="seconds"),
    }
    if size is None and transcript_path:
        try:
            size = os.path.getsize(transcript_path)
        except OSError:
            size = None                      # file unreadable NOW - record no watermark
    # `if size:` (falsy-zero) never recorded a 0-byte watermark, so a session first
    # seen with an empty transcript could NEVER re-trigger once it grew - the whole
    # session's knowledge was permanently lost (review 2026-08). Store 0 explicitly;
    # _transcript_grew treats any recorded size, including 0, as the growth baseline.
    if size is not None:
        entry["bytes"] = int(size)
    db[session_id] = entry
    save_processed(db)


def _transcript_grew(entry, path) -> bool:
    """True when a processed transcript has grown past the byte size recorded at
    processing time. This is how the pipeline sees the post-compaction half of a
    long session: PreCompact processes the transcript-so-far and marks the sid,
    the session continues under the SAME id, and the blanket 'already processed'
    skip silently lost everything after the first compaction - on exactly the
    longest, richest sessions (review 2026-08). Legacy entries without `bytes`
    never re-trigger (no mass re-mining on upgrade)."""
    if not isinstance(entry, dict) or "bytes" not in entry:
        return False                         # legacy entry: no watermark, never re-trigger
    try:
        rec = int(entry.get("bytes") or 0)
        # Any growth counts (REMINE_MIN_GROWTH_BYTES defaults to 1). The re-mine reads
        # only the appended region, so a small tail costs one small extraction, and an
        # empty body is caught before any model call. Skipping it lost the tail for good.
        return (os.path.getsize(path) - rec) >= max(1, REMINE_MIN_GROWTH_BYTES)
    except (OSError, ValueError, TypeError):
        return False


# ── Transcript reading ────────────────────────────────────────────────

# Defense-in-depth: scrub common secret shapes BEFORE the transcript reaches
# Ollama or any written note, so a pasted key can't leak into the (Obsidian-
# Sync'd) vault (audit C2). Not full DLP - high-confidence patterns only.
# key=value / key: value form - redact the value, keep the key (group sub)
_SECRET_KV = _lazy_re(
    r'(?i)(api[_-]?key|secret[_-]?access[_-]?key|access[_-]?key[_-]?id|'
    r'secret[_-]?key|private[_-]?key|client[_-]?secret|secret|password|'
    r'passwd|access[_-]?token|token)'
    r'(\s*["\']?\s*[:=]\s*["\']?)([^\s"\',]{8,})')
# connection string - redact only the password between user: and @, keep host/db
_SECRET_CONN = _lazy_re(
    r'(?i)((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqps?)://[^:\s/@]+:)'
    r'([^@\s/]{3,})(@)')
# full-redact, high-confidence token shapes (no entropy heuristics → no false
# positives on legitimate hashes/ids in code) - audit M1/I-14
_SECRET_PATTERNS = [
    re.compile(r'-----BEGIN[\s\S]{1,4000}?-----END[ A-Z]*-----'),
    re.compile(r'\bsk-[A-Za-z0-9_-]{20,}'),       # OpenAI/Anthropic/OpenRouter sk-…
    re.compile(r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}'),  # GitHub
    re.compile(r'\bglpat-[A-Za-z0-9_\-]{20,}'),   # GitLab PAT
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),          # AWS access key id
    re.compile(r'\bAIza[0-9A-Za-z_\-]{30,}'),     # Google API key (legacy)
    re.compile(r'\bAQ\.[A-Za-z0-9_\-]{20,}'),     # Google AI Studio key (new)
    re.compile(r'\bya29\.[A-Za-z0-9_\-]{20,}'),   # Google OAuth access token
    re.compile(r'\bcsk-[A-Za-z0-9]{20,}'),        # Cerebras
    re.compile(r'\bgsk_[A-Za-z0-9]{20,}'),        # Groq
    re.compile(r'\bhf_[A-Za-z0-9]{20,}'),         # HuggingFace
    re.compile(r'\bnpm_[A-Za-z0-9]{30,}'),        # npm
    re.compile(r'\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}'),  # Stripe
    re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,}'),  # Slack
    re.compile(r'https://hooks\.slack\.com/services/[A-Za-z0-9/]{20,}'),
    re.compile(r'https://discord(?:app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_\-]{20,}'),
    re.compile(r'\b\d{8,10}:[A-Za-z0-9_\-]{35,}\b'),  # Telegram bot token
    re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}'),  # Authorization: Bearer …
    re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'),  # JWT
]


def redact_secrets(text: str) -> str:
    if not text:
        return text
    out = _SECRET_KV.sub(r'\1\2[REDACTED]', text)        # key=value → keep key
    out = _SECRET_CONN.sub(r'\1[REDACTED]\3', out)       # conn string → keep host
    for pat in _SECRET_PATTERNS:
        out = pat.sub('[REDACTED]', out)
    return out


_SYSREM_RE = _lazy_re(r"<system-reminder>.*?</system-reminder>", re.S)


def strip_injected_boilerplate(body: str, min_len: int = 200, min_repeats: int = 3) -> str:
    """Remove harness-injected scaffolding before extraction: <system-reminder> blocks,
    and any paragraph of >= min_len chars that repeats VERBATIM >= min_repeats times
    (injected global instructions - CLAUDE.md, 'Active Projects' rosters - are re-sent
    with every turn, so they dominate the transcript without being session content).
    Repeated blocks keep their FIRST occurrence, so genuinely-quoted config is still
    visible once. This is the mechanical half of the anti-confabulation defense; the
    extraction prompt's boilerplate RULE is the semantic half (review 2026-08: a live
    extractor credited one project's work to an unrelated project whose name occurred
    only in per-turn instruction boilerplate)."""
    body = _SYSREM_RE.sub(" ", body or "")
    paras = body.split("\n\n")
    if len(paras) < min_repeats:
        return body
    counts: dict = {}
    for para in paras:
        if len(para) >= min_len:
            counts[para] = counts.get(para, 0) + 1
    seen: set = set()
    out = []
    for para in paras:
        if len(para) >= min_len and counts.get(para, 0) >= min_repeats:
            if para in seen:
                continue                    # drop the 2nd..Nth verbatim copy
            seen.add(para)
        out.append(para)
    return "\n\n".join(out)


def truncate_smart(text: str, max_chars: int) -> str:
    """Keep head (project setup) + tail (final decisions) - middle is least useful."""
    if len(text) <= max_chars:
        return text
    sep = "\n\n[...middle of the transcript trimmed...]\n\n"
    head_cap = TRUNCATE_HEAD_CHARS or int(max_chars * TRUNCATE_HEAD_FRAC)
    head_len = min(head_cap, max_chars - len(sep) - 100)
    if head_len <= 0:
        # Distinguish "the user asked for tail-only" (HEAD_FRAC=0 → honor it: keep
        # the END, where the final decisions live) from "max_chars is too tiny for
        # a split" (fall back to a head slice). The old blanket `text[:max_chars]`
        # returned the exact INVERSE of a requested tail-only split, with no
        # truncation marker (review 2026-08).
        if head_cap <= 0 and max_chars > len(sep) + 100:
            return sep.lstrip() + text[-(max_chars - len(sep)):]
        return text[:max_chars]
    tail_len = max_chars - head_len - len(sep)
    return text[:head_len] + sep + text[-tail_len:]


def _iter_events(path: str, from_byte: int = 0):
    """Yield parsed events from a JSONL transcript (resilient to partial lines).

    `from_byte` mines only the region added since a recorded watermark. Re-reading a
    grown transcript from zero is what forks a session's notes: `truncate_smart` keeps a
    head+tail window anchored to EOF, so growth slides the tail, the extractor sees a
    different document, invents different titles, and the slug-keyed absorb misses - the
    old notes are then retired as superseded by their own rename. Reading only the new
    region removes the slide at its source.
    """
    if not path or not Path(path).exists():
        return
    try:
        # errors="replace": one bad byte in a transcript must degrade (per-line
        # JSON parse skips the mojibake line), never raise UnicodeDecodeError -
        # which is a ValueError, slips past `except OSError`, and crashed the hook
        # mid-sweep, aborting every later session in the batch (audit A1).
        with open(path, encoding="utf-8", errors="replace") as f:
            if from_byte > 0:
                # Seek by BYTES but resume on a line boundary: a watermark can land
                # mid-line, and half a JSON object is not an event. Whether it landed
                # mid-line has to be CHECKED, not assumed - the watermark is recorded as
                # the file size after a completed write, so the common case is that it sits
                # exactly on a newline, and discarding unconditionally threw away the first
                # complete event of every re-mine. Measured 2026-09-02: eight events lost
                # across eight growth stages, one per trigger, silently.
                f.buffer.seek(max(0, from_byte - 1))
                mid_line = f.buffer.read(1) != b"\n"        # buffer now sits at from_byte
                if mid_line:
                    f.readline()           # discard the partial remainder of that line
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # A line can be valid JSON and still not be an event: `[1, 2]`, `"text"`, `null`
                # and `7` all decode cleanly, and the first consumer to call `.get()` on one
                # raises AttributeError - which is not an OSError, so it escaped this function's
                # own guard and aborted `sweep_unprocessed` at `read_session_meta`, BEFORE its
                # per-candidate try, skipping every session sorted behind the bad transcript.
                # That is the outcome this function's comment above says it prevents, reached by
                # the shape rather than the bytes (T1 review 2026-09-19).
                if isinstance(evt, dict):
                    yield evt
    except OSError as e:
        log(f"Transcript read error: {e}")


def _evt_meta(evt: dict) -> tuple:
    """Pluck (cwd, timestamp) out of an event - either is may be None."""
    cwd = evt.get("cwd") or (evt.get("metadata") or {}).get("cwd")
    ts = evt.get("timestamp") or (evt.get("snapshot") or {}).get("timestamp")
    return cwd, ts


def read_session_meta(path: str) -> dict:
    """Short-circuit pass: first cwd + first timestamp. Used by sweep."""
    cwd = ts = None
    for evt in _iter_events(path):
        ec, et = _evt_meta(evt)
        cwd = cwd or ec
        ts = ts or et
        if cwd and ts:
            break
    return {"cwd": cwd, "timestamp": ts}


def _user_lines(content, cap: int) -> list[str]:
    if isinstance(content, str):
        return [f"USER: {content[:cap]}"] if content.strip() else []
    if isinstance(content, list):
        return [f"USER: {c.get('text', '')[:cap]}"
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"]
    return []


def _assistant_lines(content, cap: int) -> list[str]:
    if not isinstance(content, list):
        return []
    out = []
    for c in content:
        if not isinstance(c, dict):
            continue
        kind = c.get("type")
        if kind == "text":
            txt = c.get("text", "").strip()
            if txt:
                out.append(f"ASSISTANT: {txt[:cap]}")
        elif kind == "tool_use":
            inp = json.dumps(c.get("input", {}), ensure_ascii=False)
            out.append(f"TOOL[{c.get('name','')}]: {inp[:cap]}")
    return out


def _format_event(evt: dict, cap: int) -> list[str]:
    msg = evt.get("message", {})
    if evt.get("type") == "user":
        return _user_lines(msg.get("content", ""), cap)
    if evt.get("type") == "assistant":
        return _assistant_lines(msg.get("content", []), cap)
    return []


def read_transcript(path: str, from_byte: int = 0) -> dict:
    """Single full pass - returns {body, cwd, timestamp}.

    Body lines are individually capped at MAX_MESSAGE_CHARS so one giant paste
    cannot starve the rest; the global budget is then applied by truncate_smart.
    """
    lines: list[str] = []
    cwd = ts = None
    cap = MAX_MESSAGE_CHARS
    for evt in _iter_events(path, from_byte):
        ec, et = _evt_meta(evt)
        cwd = cwd or ec
        ts = ts or et
        lines.extend(_format_event(evt, cap))
    if from_byte > 0:
        # A re-mine reads the tail, but the session started where the FILE starts. Taking
        # the timestamp from the first event after the watermark re-dated a session that
        # crossed midnight: a new date, a new session stem, typed notes filed under the
        # wrong day, and the same-session absorb missing its own earlier notes - the fork
        # the delta read exists to prevent, reintroduced one level up (review 2026-09-05).
        head = read_session_meta(path)
        cwd = head.get("cwd") or cwd
        ts = head.get("timestamp") or ts
    return {"body": "\n".join(lines), "cwd": cwd, "timestamp": ts}


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    except (ValueError, TypeError):
        return None


# ── Ollama ────────────────────────────────────────────────────────────

_OLLAMA_DOWN = False  # set on a connectivity/timeout error this run (audit F29)
_CLOUD_DEAD = False   # set when the active cloud backend exhausts this run → skip
_LLM_STATS = {"cloud": 0, "ollama": 0, "fail": 0}  # backend usage this run


def _record_usage(data: dict) -> None:
    """Add one response's reported token counts to this run's totals, in whichever shape it
    came in.

    The sleep-time judge spends a budget in TOKENS, because that is the unit the price is
    quoted in, and it charges each verdict the difference these counters moved across the
    call. Only `call_ollama` ever moved them: Gemini and the OpenAI-compatible backends return
    their counts in the same body the extractor already parses, and nobody read them. So every
    cloud verdict fell through to `TOKENS_PER_PAIR_EST = 415` - the documented estimate - and a
    run reporting "38,180 of 100,000 tokens" was reporting 92 x 415 with no measurement in it.
    `estimated_calls` was the honest half and was equal to `judged` on every cloud run.

    Recorded before the response's shape is checked, exactly as the Ollama path does it: a
    provider bills for an answer it then truncated or blocked, and a counter that only counts
    the calls that went well understates what was spent.

    An absent block adds nothing, which keeps "the backend did not say" distinguishable from
    "the backend said zero" - the estimate stays the fallback for providers that really are
    silent, rather than becoming the path.
    """
    if not isinstance(data, dict):
        return
    gem = data.get("usageMetadata")
    if isinstance(gem, dict):                                # Gemini generateContent
        _LLM_STATS["prompt_tokens"] = (_LLM_STATS.get("prompt_tokens", 0)
                                       + int(gem.get("promptTokenCount") or 0))
        _LLM_STATS["eval_tokens"] = (_LLM_STATS.get("eval_tokens", 0)
                                     + int(gem.get("candidatesTokenCount") or 0)
                                     + int(gem.get("thoughtsTokenCount") or 0))
        return
    oai = data.get("usage")
    if isinstance(oai, dict):                                # Cerebras, Groq, DeepSeek
        _LLM_STATS["prompt_tokens"] = (_LLM_STATS.get("prompt_tokens", 0)
                                       + int(oai.get("prompt_tokens") or 0))
        _LLM_STATS["eval_tokens"] = (_LLM_STATS.get("eval_tokens", 0)
                                     + int(oai.get("completion_tokens") or 0))


def ollama_alive(timeout_s: float = 4) -> bool:
    """Cheap liveness ping so the hook fails loudly instead of silently
    dropping a session when Ollama is down / reloading a model (audit F29)."""
    # deferred: keep the guard/recall hot path free of this import cost (perf audit A2)
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=timeout_s) as r:
            return getattr(r, "status", 200) == 200
    except Exception:
        return False


def _json_api_call(url: str, body: bytes, headers: dict, *, timeout: float,
                   retries: int, backoff: float, label: str, extract,
                   transient_http: tuple = (), cloud: bool = True,
                   http_error_log=None) -> dict:
    """The one HTTP/JSON retry loop behind every LLM backend - Ollama, Gemini and
    the OpenAI-compatible providers each carried a drifting copy until the 2026-08
    cleanup. `extract(data, last)` maps the decoded response to ("ok", dict) /
    ("retry", None) (transient empty answer - backoff unless `last`) /
    ("fail", msg). Network errors retry with linear backoff; HTTP statuses in
    `transient_http` too. On giving up the backend is marked dead for the rest of
    the run (_CLOUD_DEAD, or _OLLAMA_DOWN when cloud=False) so later calls don't
    burn more timeouts. Always returns a dict ({} on any failure)."""
    # deferred: keep the guard/recall hot path free of this import cost (perf audit A2)
    import urllib.error
    import urllib.request
    global _CLOUD_DEAD, _OLLAMA_DOWN
    for attempt in range(retries + 1):
        last = attempt >= retries
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
            status, out = extract(data, last)
            if status == "ok":
                return out
            if status == "retry" and not last:
                time.sleep(backoff * (attempt + 1))
                continue
            if out:
                log(f"{label}: {out}")
            return {}
        except urllib.error.HTTPError as e:
            msg = ""
            try:
                msg = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            if e.code in transient_http and not last:
                time.sleep(backoff * (attempt + 1))
                continue
            if http_error_log is not None:
                http_error_log(e, msg)
            else:
                log(f"{label} HTTP {e.code}: {_scrub_for_log(msg)}")
            if cloud and e.code in (401, 403, 429, 500, 503):
                _CLOUD_DEAD = True  # bad key or exhausted transient - skip rest of run
            return {}
        except (urllib.error.URLError, TimeoutError) as e:
            if not last:
                time.sleep(backoff * (attempt + 1))
                continue
            if cloud:
                _CLOUD_DEAD = True
            else:
                _OLLAMA_DOWN = True
            log(f"{label} unreachable after {attempt + 1} tries "
                f"({_scrub_for_log(url)}): {getattr(e, 'reason', e)}")
            return {}
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            log(f"{label} parse failed: {e}")
            return {}
        except Exception as e:
            log(f"{label} error: {type(e).__name__}: {e}")
            return {}
    return {}


def call_ollama(prompt: str) -> dict:
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "think": False,  # qwen3.x "thinking" mode leaks structured output
        # temperature overridable so a benchmark can pin extraction deterministically (seeds/repro):
        # default 0.2 keeps the live hook's behaviour unchanged; a stand sets NEVERTWICE_EXTRACT_TEMP=0.
        "options": {"temperature": float(os.environ.get("NEVERTWICE_EXTRACT_TEMP", "0.2")), "num_ctx": 16384},
    }).encode("utf-8")

    def _extract(data, last):
        # the price in the market's units: prompt and answer tokens of every call this run (K8)
        _LLM_STATS["prompt_tokens"] = _LLM_STATS.get("prompt_tokens", 0) + int(data.get("prompt_eval_count") or 0)
        _LLM_STATS["eval_tokens"] = _LLM_STATS.get("eval_tokens", 0) + int(data.get("eval_count") or 0)
        raw = (data.get("response") or "").strip()
        if not raw:
            return "fail", "returned empty response"
        parsed = json.loads(_strip_json_fence(raw))
        return "ok", parsed if isinstance(parsed, dict) else {}

    def _http_err(e, body):
        # A real HTTP response - never retried. 404 / "model not found" means the
        # tag was never pulled - give the exact fix instead of a bare HTTP code
        # (the #1 first-run stumble, launch audit).
        if e.code == 404 or "not found" in body.lower():
            log(f"Ollama model {OLLAMA_MODEL!r} not found - run: ollama pull {OLLAMA_MODEL} "
                f"(or set NEVERTWICE_MODEL / a cloud key)")
        else:
            log(f"Ollama HTTP {e.code} {e.reason} | {_scrub_for_log(body)}")

    return _json_api_call(OLLAMA_URL, payload, {"Content-Type": "application/json"},
                          timeout=OLLAMA_TIMEOUT, retries=OLLAMA_RETRIES,
                          backoff=OLLAMA_RETRY_BACKOFF, label="Ollama",
                          extract=_extract, cloud=False, http_error_log=_http_err)


# ── Gemini primary backend + unified generate_json (Gemini → Ollama) ──

def call_gemini(prompt: str) -> dict:
    """Gemini generateContent in JSON mode. Returns {} on any failure so the
    caller can fall back to Ollama. Retries transient 503/429/500/timeout."""
    url = GEMINI_URL.format(model=_safe_model_seg(GEMINI_MODEL))   # SSRF guard on the model seg
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2,
                             "responseMimeType": "application/json"},
    }).encode("utf-8")
    # key in a header, never the URL, so it can't leak via HTTPError.url / logs
    headers = {"Content-Type": "application/json",
               "x-goog-api-key": provider_key("gemini")}

    def _extract(data, last):
        _record_usage(data)                      # what this call cost, in the market's units
        cands = data.get("candidates") or []
        if not cands:
            block = (data.get("promptFeedback") or {}).get("blockReason")
            if block:
                return "fail", f"blocked: {block}"   # deterministic content block - don't retry
            if not last:                             # transient empty-candidates - retry (audit B1)
                return "retry", None
            return "fail", f"no candidates ({str(data)[:120]})"
        fin = cands[0].get("finishReason", "STOP")
        parts = (cands[0].get("content") or {}).get("parts") or []
        txt = "".join(p.get("text", "") for p in parts
                      if isinstance(p, dict)).strip()
        if not txt:
            if not last:
                return "retry", None
            return "fail", f"empty text (finishReason={fin})"
        if fin and fin != "STOP":  # MAX_TOKENS/SAFETY - log for diagnosability
            log(f"Gemini finishReason={fin} - response may be truncated")
        parsed = json.loads(_strip_json_fence(txt))
        return "ok", parsed if isinstance(parsed, dict) else {}

    return _json_api_call(url, body, headers, timeout=GEMINI_TIMEOUT,
                          retries=GEMINI_RETRIES, backoff=GEMINI_RETRY_BACKOFF,
                          label="Gemini", extract=_extract,
                          transient_http=(500, 503, 429))


def _call_openai_chat(prompt: str, base_url: str, api_key: str, model: str,
                      label: str) -> dict:
    """OpenAI-compatible chat completion in JSON mode (Cerebras, Groq). Returns
    {} on any failure so the caller falls back to Ollama. Browser UA because
    Cerebras sits behind Cloudflare. Retries transient 503/429/500/timeout."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {api_key}", "User-Agent": _UA}

    def _extract(data, last):
        _record_usage(data)                      # what this call cost, in the market's units
        choices = data.get("choices") or []
        if not choices:
            if not last:
                return "retry", None
            return "fail", f"no choices ({str(data)[:120]})"
        msg = choices[0].get("message") or {}
        txt = (msg.get("content") or "").strip()
        fin = choices[0].get("finish_reason")
        if not txt:
            if not last:
                return "retry", None
            return "fail", f"empty content (finish_reason={fin})"
        if fin and fin not in ("stop", "length"):
            log(f"{label} finish_reason={fin}")
        parsed = json.loads(_strip_json_fence(txt))
        return "ok", parsed if isinstance(parsed, dict) else {}

    return _json_api_call(base_url, body, headers, timeout=GEMINI_TIMEOUT,
                          retries=GEMINI_RETRIES, backoff=GEMINI_RETRY_BACKOFF,
                          label=label, extract=_extract,
                          transient_http=(500, 503, 429))


def call_cerebras(prompt: str) -> dict:
    return _call_openai_chat(prompt, CEREBRAS_URL, provider_key("cerebras"),
                             CEREBRAS_MODEL, "Cerebras")


def call_groq(prompt: str) -> dict:
    return _call_openai_chat(prompt, GROQ_URL, provider_key("groq"), GROQ_MODEL, "Groq")


def call_deepseek(prompt: str) -> dict:
    return _call_openai_chat(prompt, DEEPSEEK_URL, provider_key("deepseek"),
                             DEEPSEEK_MODEL, "DeepSeek")


def call_cloud(prompt: str) -> dict:
    """Dispatch to the configured cloud backend (ACTIVE_CLOUD)."""
    if ACTIVE_CLOUD == "cerebras":
        return call_cerebras(prompt)
    if ACTIVE_CLOUD == "groq":
        return call_groq(prompt)
    if ACTIVE_CLOUD == "gemini":
        return call_gemini(prompt)
    if ACTIVE_CLOUD == "deepseek":
        return call_deepseek(prompt)
    return {}


def generate_json(prompt: str, project: str | None = None) -> dict:
    """Unified extraction: the configured cloud backend first (fast, off-GPU),
    local Ollama fallback on any cloud failure. A per-run circuit breaker skips a
    cloud backend already known down this run. Projects in LOCAL_ONLY_PROJECTS
    NEVER touch the cloud. Returns {} if no backend produced output."""
    local_only = is_local_only(project)
    cloud_on = bool(cloud_key()) and ACTIVE_CLOUD != "none"
    if cloud_on and not _CLOUD_DEAD and not local_only:
        res = call_cloud(prompt)
        if res:
            _LLM_STATS["cloud"] += 1
            return res
        log(f"Cloud ({ACTIVE_CLOUD}) failed - falling back to local Ollama")
    if not local_only and _OLLAMA_DOWN and cloud_on:
        # Ollama already failed this run and a cloud backend is primary - don't
        # burn another timeout; leave this session for a later retry.
        _LLM_STATS["fail"] += 1
        return {}
    res = call_ollama(prompt)
    if res:
        _LLM_STATS["ollama"] += 1
    else:
        _LLM_STATS["fail"] += 1
    return res


def extraction_ceiling_s() -> float:
    """Worst-case wall clock of ONE `generate_json` call, derived from this module's own
    timeouts rather than written into a comment somewhere.

    Anything that holds the vault lock across an extraction needs this number to say what its
    hold can cost: `watch` bounds its mining LOOP by wall clock, but the check runs before a
    transcript is started, so the hold is that budget plus however long the extraction already
    running takes - and the only honest bound on that term is this one. A call that times out
    everywhere pays the cloud chain and then the Ollama chain in series, each with its retries
    and its linear backoff (`_json_api_call` sleeps `backoff * (attempt + 1)` between tries).
    Both cloud backends share the GEMINI_* timeout constants, so one term covers either.
    """
    def _chain(timeout: float, retries: int, backoff: float) -> float:
        tries = max(1, int(retries) + 1)
        return timeout * tries + backoff * sum(range(1, tries))

    local = _chain(OLLAMA_TIMEOUT, OLLAMA_RETRIES, OLLAMA_RETRY_BACKOFF)
    if not (cloud_key() and ACTIVE_CLOUD != "none"):
        return float(local)
    return float(_chain(GEMINI_TIMEOUT, GEMINI_RETRIES, GEMINI_RETRY_BACKOFF) + local)


def llm_available() -> bool:
    """A backend exists if a cloud key is configured (per-call failures fall
    back) or local Ollama is up."""
    return (bool(cloud_key()) and ACTIVE_CLOUD != "none") or ollama_alive()


def llm_backend_desc() -> str:
    if cloud_key() and ACTIVE_CLOUD != "none":
        return (f"{ACTIVE_CLOUD}:{_CLOUD_MODELS.get(ACTIVE_CLOUD, '?')} (primary) "
                f"+ ollama {OLLAMA_MODEL} (fallback)")
    return f"ollama {OLLAMA_MODEL}"


def backend_report(timeout_s: float = 2.0) -> str:
    """Human-readable summary of the backends auto-detected *right now* - what
    extraction and recall will actually use with zero config. install.py prints this
    so a newcomer sees the chosen default instead of editing env vars to find out.
    Pure detection (no writes, short timeouts); safe to call anywhere."""
    lines = []
    if cloud_key() and ACTIVE_CLOUD != "none":
        lines.append(f"  extraction : cloud {ACTIVE_CLOUD}:{_CLOUD_MODELS.get(ACTIVE_CLOUD, '?')}"
                     f"  (local Ollama {OLLAMA_MODEL} as fallback)")
    elif ollama_alive(timeout_s):
        lines.append(f"  extraction : local Ollama {OLLAMA_MODEL}  (no key needed)")
    else:
        lines.append("  extraction : paused - no cloud key and Ollama is down; sessions are kept"
                     " and retried (start Ollama or add one key to begin)")
    if EMBED_PROVIDER != "ollama":
        if _embed_key():
            lines.append(f"  recall     : cloud embedder {EMBED_PROVIDER}:{EMBED_MODEL}  (semantic + lexical)")
        else:
            lines.append(f"  recall     : lexical only (FTS5) - {EMBED_PROVIDER} selected but"
                         f" {_EMBED_KEY_ENV.get(EMBED_PROVIDER, 'its key')} is unset")
    elif embedder_available(timeout_s):
        lines.append(f"  recall     : local Ollama {EMBED_MODEL}  (semantic + lexical, hybrid)")
    else:
        lines.append("  recall     : lexical only (FTS5) - start Ollama (bge-m3) for semantic recall")
    # Surface the opt-in precision lever when its deps are already on the machine, so the
    # cross-encoder that closes most of the W2/W4 embedding-compression gap is discoverable
    # instead of hidden behind an env var nobody knows to set.
    try:
        _rc = _sibling("reranker_ce")
        if _rc.available():
            if _rc.enabled():
                lines.append("  precision  : trained cross-encoder active "
                             "(NEVERTWICE_XRERANK=0 turns it off)")
            elif not _rc._model_cached():
                lines.append("  precision  : cross-encoder deps detected; run once with "
                             "NEVERTWICE_XRERANK=1 (downloads ~2 GB) - it then stays on")
            else:
                lines.append("  precision  : trained cross-encoder switched off "
                             "(NEVERTWICE_XRERANK=0)")
    except Exception:
        pass
    return "\n".join(lines)


# ── Embeddings (semantic retrieval over the vault, audit F36) ─────────

def _embed_prefix(kind: str | None) -> str:
    """Task prefix for nomic-style embedders. kind: 'query' | 'document' | None."""
    if kind == "query":
        return EMBED_QUERY_PREFIX
    if kind == "document":
        return EMBED_DOC_PREFIX
    return ""


def embed_signature() -> str:
    """Identity of the live embedder, `provider:model`. Stamped into the cache/
    index meta so a provider OR model change self-invalidates stale vectors instead
    of silently ranking the query against a different vector space."""
    return f"{EMBED_PROVIDER}:{EMBED_MODEL}"


def _embed_sig_current(stored) -> bool:
    """Does a stored embedding-model stamp match the live embedder? An unstamped
    (legacy) cache is accepted as current so an upgrade doesn't force a rebuild; a
    bare model name (a pre-provider stamp) is read as the ollama provider, so
    existing bge-m3 caches keep working without a re-embed."""
    if not stored:
        return True
    s = str(stored)
    if ":" not in s:
        s = f"ollama:{s}"
    return s == embed_signature()


def embed_cache_usable() -> bool:
    """True when the persisted vectors were produced by the live embedder. After a
    provider/model switch this is False until `embed_index.py --rebuild`, so the
    semantic paths abstain rather than cosine the query against a foreign vector
    space - self-invalidation, not silent garbage. (Lexical/FTS recall, being
    provider-independent, keeps answering meanwhile.)"""
    return _embed_sig_current(load_embed_meta().get("model"))


# Cloud embedding providers. OpenAI-compatible /v1/embeddings covers openai|voyage
# and any custom host (NEVERTWICE_EMBED_BASE_URL); gemini and cohere use their own
# request/response shapes. Keys are read at call time (so a late .env load / a test
# env is honoured) from the provider's conventional variable.
_EMBED_PROVIDER_URL = {
    "openai": "https://api.openai.com/v1/embeddings",
    "voyage": "https://api.voyageai.com/v1/embeddings",
    "cohere": "https://api.cohere.com/v2/embeddings",
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/"
               "models/{model}:embedContent"),
}
_EMBED_KEY_ENV = {"openai": "OPENAI_API_KEY", "voyage": "VOYAGE_API_KEY",
                  "cohere": "COHERE_API_KEY", "gemini": "GEMINI_API_KEY"}


def _embed_key() -> str:
    """API key for the active cloud embedding provider, '' if unset."""
    return os.environ.get(_EMBED_KEY_ENV.get(EMBED_PROVIDER, ""), "").strip()


_SAFE_MODEL_RE = _lazy_re(r"[^A-Za-z0-9._-]")


def _safe_model_seg(model: str) -> str:
    """Sanitise a model name before it is interpolated into a provider URL - defence
    against SSRF / path & query smuggling via NEVERTWICE_EMBED_MODEL / NEVERTWICE_GEMINI_MODEL
    (drop anything outside [A-Za-z0-9._-], so '/', '?', '#', '@' can't redirect the request
    to an internal host). audit 2026-06-18."""
    return _SAFE_MODEL_RE.sub("", model or "")


_BEARER_RE = _lazy_re(r"(?i)bearer\s+[A-Za-z0-9._\-]+")
# Provider key headers/fields a hostile or misconfigured endpoint could echo back into
# its error body (Gemini uses x-goog-api-key, OpenAI-compat hosts sometimes reflect an
# api_key/api-key field). Launch-round security pass 2026-06-20.
_KEYHDR_RE = _lazy_re(r"(?i)(x-goog-api-key|api[-_]?key)[\"'\s:=]+[A-Za-z0-9._\-]+")


def _scrub_for_log(s: str) -> str:
    """Strip bearer tokens and provider key headers from a third-party error body
    before it is logged (and git-committed) - a hostile endpoint could echo the
    Authorization / x-goog-api-key header back to leak the key into the log. audit
    2026-06-18, extended 2026-06-20."""
    s = _BEARER_RE.sub("Bearer <redacted>", s or "")
    return _KEYHDR_RE.sub(r"\1 <redacted>", s)


def embedder_available(timeout_s: float = 4) -> bool:
    """Is the configured embedder usable right now? Ollama → a liveness ping; a
    cloud provider → a key is configured (per-call HTTP failures fall back to the
    lexical path, exactly as a busy GPU does)."""
    if EMBED_PROVIDER == "ollama":
        return ollama_alive(timeout_s)
    return bool(_embed_key())


def _embed_http(url: str, payload: dict, headers: dict, timeout: int | None):
    """POST JSON to a cloud embeddings endpoint, return the parsed dict or None.
    One place for the UA/timeout/error logging shared by every cloud provider."""
    # deferred: keep the guard/recall hot path free of this import cost (perf audit A2)
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": _UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout or EMBED_TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        emsg = ""
        try:
            emsg = e.read().decode("utf-8", "replace")[:150]
        except Exception:
            pass
        log(f"Embed ({EMBED_PROVIDER}) HTTP {e.code}: {_scrub_for_log(emsg)}")
        return None
    except Exception as e:
        log(f"Embed ({EMBED_PROVIDER}) failed: {type(e).__name__}: {str(e)!a}")
        #: ascii-escape: an OS-localized error text (a Russian WinError, say) must not
        #: depend on the log reader's codepage. Came from the ollama branch, which used
        #: to build its own request and carried this fix alone.
        return None


def _embed_cloud(text: str, kind: str | None, timeout: int | None):
    """Dispatch to the configured cloud embedding provider. Returns a vector or
    None (None → caller treats it like a busy GPU and drops to lexical)."""
    key = _embed_key()
    if not key:
        log(f"Embedding provider {EMBED_PROVIDER!r} selected but {_EMBED_KEY_ENV.get(EMBED_PROVIDER, '?')}"
            " is unset - semantic recall blocked (set the key or NEVERTWICE_EMBED_PROVIDER=ollama)")
        return None
    if EMBED_PROVIDER == "gemini":
        seg = _safe_model_seg(EMBED_MODEL)        # SSRF guard: no '/?#@' into the URL
        body = {"model": f"models/{seg}", "content": {"parts": [{"text": text}]}}
        if kind in ("query", "document"):         # asymmetric retrieval task hint (quality)
            body["taskType"] = "RETRIEVAL_QUERY" if kind == "query" else "RETRIEVAL_DOCUMENT"
        data = _embed_http(_EMBED_PROVIDER_URL["gemini"].format(model=seg), body,
                           {"x-goog-api-key": key}, timeout)
        emb = (data or {}).get("embedding")
        # {"embedding": {"values": [...]}} is the documented shape, but some model
        # variants return a flat {"embedding": [...]} - accept both (launch-round audit).
        v = emb.get("values") if isinstance(emb, dict) else (emb if isinstance(emb, list) else None)
        return v if isinstance(v, list) else None
    if EMBED_PROVIDER == "cohere":
        itype = "search_query" if kind == "query" else "search_document"
        data = _embed_http(_EMBED_PROVIDER_URL["cohere"],
                           {"model": EMBED_MODEL, "texts": [text],
                            "input_type": itype, "embedding_types": ["float"]},
                           {"Authorization": f"Bearer {key}"}, timeout)
        fl = ((data or {}).get("embeddings") or {})
        fl = fl.get("float") if isinstance(fl, dict) else None
        return fl[0] if isinstance(fl, list) and fl and isinstance(fl[0], list) else None
    # openai | voyage | custom OpenAI-compatible host
    url = EMBED_BASE_URL or _EMBED_PROVIDER_URL.get(EMBED_PROVIDER, _EMBED_PROVIDER_URL["openai"])
    data = _embed_http(url, {"model": EMBED_MODEL, "input": text},
                       {"Authorization": f"Bearer {key}"}, timeout)
    arr = (data or {}).get("data")
    if isinstance(arr, list) and arr and isinstance(arr[0], dict):
        v = arr[0].get("embedding")
        return v if isinstance(v, list) else None
    return None


_EMBED_TEXT_MEMO: dict = {"key": None, "vec": None}


def embed_text(text: str, kind: str | None = None, timeout: int | None = None,
               project: str | None = None):
    """Return an embedding vector for `text`, or None on failure. Dispatches on
    NEVERTWICE_EMBED_PROVIDER: local Ollama (default) or a cloud provider (OpenAI-
    compatible, Voyage, Cohere, Gemini) so semantic recall can run with no local NN.
    `kind` selects the nomic task prefix (Ollama only); `timeout` lets interactive
    retrieval fail fast to lexical when the backend is slow (audit H5).

    `project` enforces the SAME privacy boundary as extraction (audit 2026-06-18 CRIT):
    when a CLOUD embedder is configured, a project in LOCAL_ONLY_PROJECTS is NEVER sent
    to it - embedding is skipped (→ text-only / lexical recall) so local-only note text
    and query prompts can't leave the machine. Ollama is local, so it needs no gate."""
    raw = (text or "")[:2000]
    # One-slot memo: retrieve_relevant and retrieve_cross_project embed the IDENTICAL
    # query back-to-back on every injection event - two Ollama round-trips (0.2-2s)
    # for bitwise-identical vectors (perf review 2026-08). Same text+kind+backend →
    # the previous vector.
    memo_key = (raw, kind, EMBED_PROVIDER, EMBED_MODEL)
    if _EMBED_TEXT_MEMO["key"] == memo_key and _EMBED_TEXT_MEMO["vec"] is not None:
        return _EMBED_TEXT_MEMO["vec"]
    if EMBED_PROVIDER != "ollama":
        # Default-DENY when the caller has no project context (review 2026-08): with
        # a local-only boundary configured, `project=None` used to fall straight
        # through to the cloud call - so callers that simply didn't thread the
        # kwarg (trajectory/query text) bypassed the exact boundary this gate
        # exists to enforce. Privacy is now a property of the resolver, not of
        # caller diligence.
        if (project is None and LOCAL_ONLY_PROJECTS) or (
                project is not None and is_local_only(project)):
            log(f"Cloud embedding skipped for {'unattributed text' if project is None else f'local-only project {project!r}'} "
                f"(provider {EMBED_PROVIDER}) - data stays on the machine; recall is "
                "lexical here (use NEVERTWICE_EMBED_PROVIDER=ollama for local semantic recall)")
            return None
        vec = _embed_cloud(raw, kind, timeout)
        if vec:
            _EMBED_TEXT_MEMO["key"], _EMBED_TEXT_MEMO["vec"] = memo_key, vec
        return vec
    #: Ollama goes through the same door as every cloud provider. It used to build its own
    #: `urllib` request here, which meant a harness that stubs `_embed_http` - the golden
    #: store - did not cover the configured provider, and its "deterministic" proof was
    #: ranked by the live model on 127.0.0.1 (measured 2026-09-19: 1024 real dimensions
    #: where the stub returns 48). One EMBEDDING door - the engine still opens sockets of
    #: its own for `ollama_alive` and `_json_api_call`, which are separate doors with
    #: separate stubs; this comment is not a claim about the engine as a whole.
    data = _embed_http(OLLAMA_EMBED_URL,
                       {"model": EMBED_MODEL, "input": _embed_prefix(kind) + raw},
                       {}, timeout)
    if data is None:
        return None
    embs = data.get("embeddings")
    vec = None
    if isinstance(embs, list) and embs and isinstance(embs[0], list):
        vec = embs[0]
    else:
        one = data.get("embedding")
        vec = one if isinstance(one, list) else None
    if vec:
        _EMBED_TEXT_MEMO["key"], _EMBED_TEXT_MEMO["vec"] = memo_key, vec
    return vec


# In-process memo for the (large) embed cache: one run re-parsed and re-serialized
# the same ~90MB JSON once PER PROCESSED SESSION (~1s parse + ~1.6s dumps each,
# measured) though the vault lock makes this process the only writer. Keyed by
# (path, mtime_ns, size) so an external change - another process between our runs -
# still forces a real reload. save_embed_cache refreshes the memo after writing.
_EMBED_CACHE_MEMO: dict = {"sig": None, "data": None}


def _embed_cache_sig():
    try:
        st = EMBED_CACHE.stat()
        return (str(EMBED_CACHE), st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def load_embed_cache() -> dict:
    """Load the embedding cache, falling back to the .bak generation and logging
    LOUDLY on corruption (audit M-f). The round-1 code swallowed a JSONDecodeError
    into {} - a half-written cache silently disabled semantic recall (dropping to
    recency) with no signal. Now a corrupt primary is recovered from .bak, and an
    unrecoverable cache is announced so it gets rebuilt instead of degrading mutely."""
    sig = _embed_cache_sig()
    if sig is not None and sig == _EMBED_CACHE_MEMO["sig"] \
            and _EMBED_CACHE_MEMO["data"] is not None:
        return _EMBED_CACHE_MEMO["data"]
    bak = EMBED_CACHE.with_name(EMBED_CACHE.name + ".bak")
    for f in (EMBED_CACHE, bak):
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError) as e:
            log(f"Embed cache unreadable ({f.name}): {e}")
            continue
        if isinstance(data, dict):
            if f is not EMBED_CACHE:
                log("Primary embed cache corrupt - recovered from .bak")
            elif sig is not None:
                _EMBED_CACHE_MEMO["sig"], _EMBED_CACHE_MEMO["data"] = sig, data
            return data
    if EMBED_CACHE.exists():
        log("Embed cache corrupt and no valid .bak - semantic recall DISABLED "
            "until `embed_index.py --rebuild`")
    return {}


def save_embed_cache(cache: dict):
    if not cache:
        # F13 (xhigh review): refuse to overwrite a NON-empty on-disk cache with an empty one.
        # `cache` reaches {} in the caller's hands when both generations were transiently
        # unreadable (a lock steal, a crash between file ops) - load_embed_cache's own fallback -
        # and a caller that then saves it back turns a transient read failure into permanent
        # loss, indistinguishable on disk from a genuinely empty vault. `embed_index.py --rebuild`
        # is the deliberate way to write a real empty cache.
        try:
            if EMBED_CACHE.exists() and EMBED_CACHE.stat().st_size > 2:
                log("Embed cache save refused: in-memory cache is empty but the on-disk cache is "
                    "not - run embed_index.py --rebuild if the vault is genuinely empty")
                return
        except OSError:
            pass
    try:
        # prev=False: a 90 MB rebuildable cache earns no rollback copy (store_state).
        _save_json_generations(EMBED_CACHE, json.dumps(cache, ensure_ascii=False), prev=False)
        _EMBED_CACHE_MEMO["sig"], _EMBED_CACHE_MEMO["data"] = _embed_cache_sig(), cache
    except OSError as e:
        log(f"Embed cache save failed: {e}")


def load_embed_meta() -> dict:
    """Two-generation load, loudly on recovery: the meta stamp is the ONLY thing
    standing between a stale-space cache and cross-model cosine garbage (both
    bge-m3 and its successors are 1024-dim, so no dimension guard can catch it) -
    silently mapping a corrupt meta to {} made _embed_sig_current fail open."""
    d = _load_json_generations(EMBED_META, "embed meta")
    if d is None and EMBED_META.exists():
        log("Embed meta corrupt and no valid .bak - the cache's embedding space is "
            "UNKNOWN; run embed_index.py --rebuild to re-stamp it")
    return d or {}


def save_embed_meta(meta: dict):
    try:
        _save_json_generations(EMBED_META, json.dumps(meta, ensure_ascii=False))
    except OSError as e:
        log(f"Embed meta save failed: {e}")


def cache_is_prefixed() -> bool:
    """Whether stored vectors used task prefixes. Explicit meta wins; otherwise
    an empty cache adopts the configured default and a populated legacy cache is
    treated as unprefixed - so we never query a cache in a mismatched mode."""
    meta = load_embed_meta()
    if "prefixed" in meta:
        return bool(meta["prefixed"])
    return EMBED_USE_PREFIX if not EMBED_CACHE.exists() else False


def doc_embed_kind() -> str | None:
    return "document" if cache_is_prefixed() else None


def query_embed_kind() -> str | None:
    return "query" if cache_is_prefixed() else None


# ── SQLite scale-index integration (audit C2/C3) ──────────────────────
# The JSON embedding cache stays the durable, human-diffable rebuild source and
# the consolidation substrate. But parsing it whole on every prompt does not
# scale (63 MB / ~0.7 s at 3k notes, linear). The retrieval hot path therefore
# reads candidates from a derived SQLite index - opened instantly, project-
# filtered in SQL - which the hook keeps current incrementally. Lazy import keeps
# the dependency one-way (index_sqlite imports memory_hook, not vice-versa).

def _scale_index():
    try:
        return _sibling("index_sqlite")
    except Exception as e:
        log(f"scale-index unavailable: {e}")
        return None


def scale_index_ready() -> bool:
    """True when the SQLite index exists AND was built for the live embedding
    model - only then can the hot path skip the whole-cache JSON parse without
    ranking the query against stale-model vectors (audit A5). An unstamped legacy
    index (no meta) is accepted as current so an upgrade doesn't force a rebuild."""
    idx = _scale_index()
    try:
        if not idx:
            return False
        # A current index always carries a stamped `meta`, so its presence doubles as
        # the existence check - one connection, not index_exists()+index_meta() (round 3).
        meta = idx.index_meta()
        if not meta or meta.get("vec_format") != idx.VEC_FORMAT:
            return False        # absent/legacy/stale-format → ensure() rebuilds; cache meanwhile
        if meta.get("lex_format", "raw") != idx.LEX_FORMAT:
            return False        # FTS text tokenised the other way → ensure() rebuilds it
        return _embed_sig_current(meta.get("model"))
    except Exception:
        return False


def ensure_scale_index() -> None:
    """Build the SQLite index on first need so the fast retrieval path is actually
    taken (audit A2): the accelerator otherwise stayed dormant until the next write,
    leaving every prompt to parse the whole JSON cache. Builds only when ABSENT -
    a model-stale index is left to `scale_index_ready()`/`embed_index --rebuild`,
    never rebuilt here (that would loop every session on an un-re-embedded store)."""
    idx = _scale_index()
    if not idx:
        return
    try:
        meta = idx.index_meta()      # {} when absent/legacy - one connection (round 3)
        # present AND current pack format → nothing to do. A model-stale index is
        # deliberately NOT rebuilt here (it needs a re-embed, not a rebuild - that
        # would loop every session); only absence or a format change triggers a
        # build, and a format change is always fixable from the float32 cache (P3).
        if meta and meta.get("vec_format") == idx.VEC_FORMAT \
                and meta.get("lex_format", "raw") == idx.LEX_FORMAT:
            return
        if not load_embed_cache():
            return
        idx.build()
    except Exception as e:
        log(f"scale-index ensure skipped: {e}")


def _scale_candidates(project: str, cross: bool = False, query: str | None = None):
    """Project-filtered candidate records from the SQLite index, or None when no
    index exists / a read fails (caller then falls back to the JSON cache). On a
    large project, FTS-prefilters to the top candidates so cosine cost stays bounded
    (improvement P1); small/normal projects keep an exact full scan."""
    idx = _scale_index()
    if not idx:
        return None
    try:
        # Gate on scale_index_ready(), NOT just index_exists(): a stale-format or
        # stale-model index must fall through to the JSON cache instead of being read
        # (P3 reads float16 - a misread of a legacy float32 BLOB is garbage). ensure()
        # normally rebuilds first, but if its rebuild failed we must still not read it.
        if not scale_index_ready():
            return None
        limit = None
        if query and idx.candidate_count(project, cross) > RETRIEVAL_PREFILTER_LIMIT:
            limit = RETRIEVAL_PREFILTER_LIMIT
        return idx.iter_candidates(project, cross=cross, query=query, limit=limit)
    except Exception as e:
        log(f"scale-index read failed - falling back to JSON cache: {e}")
        return None


def sync_scale_index(records: dict | None = None, delete: list | None = None) -> None:
    """Keep the SQLite accelerator current: build it from the cache if missing,
    else upsert `records` (stem->record) and delete `delete` stems. Derived &
    rebuildable, so any failure is logged and swallowed - the JSON cache remains
    the source of truth and retrieval falls back to it."""
    idx = _scale_index()
    if not idx:
        return
    try:
        if idx.index_exists():
            meta = idx.index_meta()
            if meta is None or meta.get("vec_format") != idx.VEC_FORMAT:
                idx.build()        # present but stale/unstamped pack format → full rebuild
                return
            if records:
                idx.upsert(records)
                idx.upsert_graph(list(records))   # F4: keep the entity/relation graph rows current
            if delete:
                idx.delete(delete)                # delete() prunes the graph rows too (F4)
        elif records:
            # No index yet → build it, but ONLY when there is content to add. A bare delete has
            # nothing to prune from a non-existent index, and a build triggered mid-write (e.g. a
            # supersede firing before its replacement note is on disk) would snapshot a partial
            # store and leave a stale graph index live. The next note write builds it complete.
            idx.build()
    except Exception as e:
        log(f"scale-index sync skipped: {e}")


def rebuild_scale_index() -> None:
    """Full rebuild of the SQLite index from the current cache - for after bulk
    mutations (consolidation merges/archival, embed_index --rebuild) where an
    incremental sync can't track every change. Derived & rebuildable: failures
    are logged and ignored."""
    idx = _scale_index()
    if not idx:
        return
    try:
        idx.build()
    except Exception as e:
        log(f"scale-index rebuild skipped: {e}")


def _retrieval_candidates(project: str, cross: bool, cache: dict | None,
                          query: str | None = None):
    """Unified candidate source for the rankers: the SQLite index when present
    (no whole-cache parse - audit C2; FTS-prefiltered on large projects - P1), else
    the in-memory/JSON cache (small stores, or a passed-in cache the caller holds)."""
    rows = _scale_candidates(project, cross=cross, query=query)
    if rows is not None:
        return rows
    if cache is None:
        cache = load_embed_cache()
    # vec-less (text-only) records STAY candidates: update_embeddings deliberately
    # stores them when the embedder is down so notes remain lexically recallable via
    # BM25. The old isinstance(vec, list) filter silently dropped exactly those notes
    # whenever retrieval fell back to the JSON cache (review 2026-08 A3); the SQLite
    # path has always included them.
    if cross:
        return [(s, r) for s, r in cache.items()
                if isinstance(r, dict)
                and r.get("project") and r.get("project") != project]
    return [(s, r) for s, r in cache.items()
            if isinstance(r, dict) and r.get("project") == project]


