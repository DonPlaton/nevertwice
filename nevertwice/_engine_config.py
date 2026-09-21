# -- engine part 1 of 8: environment, paths, provider settings, the extraction prompt, log and argv --
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
# Lines 1-798 of the pre-split `_engine.py`, whose body these parts reproduce byte for byte
# (sha256 bddf5d32a8883f0fdcfbd420de58e66fc9151be1ef05c696dedb44eefd0ac40f). Order is
# load-bearing: module-level code below runs after every earlier part and before every later
# one. `tests/_engine_source.py` reconstructs the whole body from these files.
#<<<ENGINE-PART-BODY>>>
#!/usr/bin/env python3
"""Claude Code Memory Hook - extracts session knowledge into an Obsidian vault.

Triggers:
  SessionEnd / PreCompact: process the just-finished session.
  SessionStart:            inject project context + top lessons; on a backlog,
                           detach a catch-up sweep (transcripts left behind by
                           abrupt closes - VS Code killed, SessionEnd never fired).
  UserPromptSubmit:        task-aware recall - inject lessons matching the prompt.
  PreToolUse:              active-memory guards (axis A) - warn/block on a
                           learned failure pattern in the pending tool action.

Pipeline per session:
  read_transcript → call_ollama(format=json) → write Patterns / Mistakes /
  Decisions → write Session note → update Context → rebuild Index → archive
  old sessions, prune old DB rows.
"""

import importlib
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


def env_int(name: str, default: int) -> int:
    """int(os.environ[name]) with a safe fallback. One mistyped env var (the primary
    configuration surface, docs/CONFIG.md) must degrade to the default with a warning,
    not crash every import in the pipeline (critic 2026-07: ~38 bare int() conversions
    made NEVERTWICE_RETRIEVAL_K=notanumber an import-time ValueError)."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        sys.stderr.write(f"[memory_hook] {name}={raw!r} is not an integer - using {default}\n")
        return default


def env_float(name: str, default: float) -> float:
    """float twin of env_int - same rationale. NaN/Inf parse without raising, so they are
    rejected explicitly: a NaN weight silently poisons every downstream comparison (NaN loses
    every < and >), which is exactly the "degrade, never surprise" contract this helper exists
    to keep (critic 2026-07 round 3: NEVERTWICE_FUSION_SEM_WEIGHT=nan collapsed all ranking)."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        v = float(raw.strip())
    except ValueError:
        sys.stderr.write(f"[memory_hook] {name}={raw!r} is not a number - using {default}\n")
        return default
    if not math.isfinite(v):
        sys.stderr.write(f"[memory_hook] {name}={raw!r} is not finite - using {default}\n")
        return default
    return v


# ── Config ────────────────────────────────────────────────────────────
# Paths come from config.py (cross-platform, env-overridable) so the same code
# runs on any machine. Importable both as a package and as a flat script dir.
try:
    from . import config as _cfg
except ImportError:  # run directly (sys.path includes this dir)
    import config as _cfg


def _sibling(name: str):
    """Import a sibling module in whichever shape this install runs: package
    (nevertwice.<name>) or flat scripts dir (<name> on sys.path). One resolver for
    every deferred import site instead of a try/except pair at each - the copies
    had already drifted once (a bare `import stats` was dead in the pip install,
    review 2026-08 A7)."""
    if __package__:
        try:
            return importlib.import_module(f".{name}", __package__)
        except ImportError:
            pass
    return importlib.import_module(name)

# Injection accounting (pure, dependency-free - see receipt.py). Imported here rather than
# lazily because it is on the SessionStart path and costs nothing: no I/O, no package imports.
# MISSING receipt.py must not kill the hook (review 2026-08): the live deployment is a flat
# selective-copy of scripts, and a cosmetic feature crashing SessionStart/SessionEnd/guards
# would violate its own contract ("must never affect the recall it is measuring") - so a
# flat dir without receipt.py degrades to no-receipt instead of ModuleNotFoundError.
try:
    _receipt = _sibling("receipt")
except ImportError:
    _receipt = None


class _NoReceipt:
    """Inert stand-in when receipt.py is absent: assembly calls show/hold unconditionally."""
    def show(self, n: int = 1) -> None:
        pass

    def hold(self, n: int = 1) -> None:
        pass

_cfg.load_dotenv()                 # idempotent - config already ran it at its own import

VAULT = _cfg.VAULT
# no mkdir at import time: read-only consumers (install.py --print, search on a fresh box)
# must not create the store as a side effect. Every write path mkdirs at its use site
# (write_atomic creates parents; acquire_lock/save_processed/main mkdir explicitly).
PROCESSED_DB = VAULT / ".processed_sessions.json"
STATUS_FILE = VAULT / "status.txt"
# NB: the vault lock has no module-level path constant - see _lock_file() (call-time derived)
EMBED_CACHE = VAULT / ".embeddings_cache.json"
EMBED_META = VAULT / ".embeddings_meta.json"   # records whether the cache is prefixed
LOG_FILE = VAULT / ".logs" / "memory_hook.log"

PROJECTS_ROOT = _cfg.PROJECTS_ROOT


def _rebase_vault(path) -> None:
    """Point VAULT and EVERY vault-derived module constant at `path` in one call.
    FOR TESTS/TOOLING ONLY. This exists because sandboxing by patching `m.VAULT`
    alone is a trap: the sibling constants above are baked at import from the
    REAL vault, so a test that patched only VAULT still wrote the LIVE embedding
    cache and log - which is exactly how the 2026-08-13 hermeticity incident
    clobbered the production cache, and how a partial patch did it AGAIN on
    2026-08-18. One call, no forgotten constants.

    The path-classification constants below (`_VAULT_NORM`, `_PROJECTS_ROOT_NORM` and the
    `_EXCLUDE_PREFIXES` built from them) were themselves forgotten until 2026-09-18: every file
    went to the sandbox while every *question* about a path was still answered about the store
    the process imported with, so `_is_excluded_path` kept the real store excluded and the
    sandbox merely tracked. Same incident shape, one layer up - pinned by
    `tests/_test_entry_and_rebase.py`.

    The third repeat, 2026-09-20, was the grounding CACHES rather than the constants: the tag
    vocabulary, the title window and the near-duplicate memo are the previous store's content
    and were still answering after the move. Constants and caches both, in this one call."""
    global VAULT, PROCESSED_DB, STATUS_FILE, EMBED_CACHE, EMBED_META, LOG_FILE, \
        PROMPT_RECALL_STATE_DIR, _VAULT_NORM, _PROJECTS_ROOT_NORM, _EXCLUDE_PREFIXES
    VAULT = Path(path)
    PROCESSED_DB = VAULT / ".processed_sessions.json"
    STATUS_FILE = VAULT / "status.txt"
    EMBED_CACHE = VAULT / ".embeddings_cache.json"
    EMBED_META = VAULT / ".embeddings_meta.json"
    LOG_FILE = VAULT / ".logs" / "memory_hook.log"
    PROMPT_RECALL_STATE_DIR = VAULT / ".prompt_recall"
    _VAULT_NORM = _norm_path(str(VAULT))
    _PROJECTS_ROOT_NORM = _norm_path(str(PROJECTS_ROOT))
    # rebuilt, not appended to: the list must stop naming the store we just left
    _EXCLUDE_PREFIXES = [_norm_path(p) for p in
                         (*_SYS_DIRS, _VAULT_NORM, _PROJECTS_ROOT_NORM) if p]
    _EMBED_CACHE_MEMO["sig"] = None
    _EMBED_CACHE_MEMO["data"] = None
    # The constants above answer "where"; the caches below answer "what is in there", and they
    # are built from the notes of whatever store was mounted when they were first asked. Left
    # standing, all three describe the store we just left: the extractor is grounded on the old
    # vault's tag vocabulary, shown the old vault's recent titles as its dedup window, and
    # near-duplicate detection compares against the old vault's embedding cache. Same incident
    # shape as the two this docstring names, one layer further in.
    _clear_tag_counts()
    _TITLE_SLUGS.clear()
    _NDUP_MEMO[0] = None
    _NDUP_MEMO[1] = None


def _split_roots(raw: str) -> list[str]:
    out = []
    for chunk in (raw or "").replace(os.pathsep, ",").split(","):
        c = chunk.strip().rstrip("\\/")
        if c:
            # expanduser HERE, in the one shared parser: watch.py expanded '~'
            # locally while this module compared cwd against the literal '~/...'
            # string - the same config value silently meant different things in
            # the two consumers (review 2026-08).
            out.append(os.path.expanduser(c))
    return out


# OS-aware path normalisation (P-2: cross-platform). Case-folded on Windows/macOS
# (case-insensitive filesystems), separator-normalised, and '..' collapsed so a
# traversal can't slip past the root/exclusion checks (audit B12). Used wherever a
# path is compared, so tracking behaves identically on Windows, Linux and macOS.
_WIN = os.name == "nt"
_CASEFOLD = _WIN or sys.platform == "darwin"


def _norm_path(p: str) -> str:
    if not p or not p.strip():
        return ""
    q = os.path.normpath(p.strip())
    if _WIN:
        q = q.replace("/", "\\")
    if _CASEFOLD:
        q = q.lower()
    return q.rstrip("\\/")


# Tracked-project roots. The system is agent-agnostic and machine-wide: a
# "project" is any directory under a configured root OR (when TRACK_ANY_PROJECT)
# any git repository - excluding system, agent-internal (~/.claude), the vault
# itself, and transient (Temp/Recycle) paths. Multiple roots via
# NEVERTWICE_PROJECT_ROOTS (os.pathsep- or comma-separated); the legacy
# single NEVERTWICE_PROJECT_ROOT still works as a fallback. (audit C2)
PROJECT_ROOTS = _split_roots(
    os.environ.get("NEVERTWICE_PROJECT_ROOTS", "").strip()
    or os.environ.get("NEVERTWICE_PROJECT_ROOT", ""))   # empty default → rely on
#   git-repo detection below; set NEVERTWICE_PROJECT_ROOTS to also track a flat root
PROJECT_ROOT_DISPLAY = PROJECT_ROOTS[0] if PROJECT_ROOTS else ""
# (PROJECT_ROOT_FILTER deleted - zero readers; it invited new code back into the
# single-root assumption _split_roots removed; review 2026-08 G8)
_ROOTS_NORM = [_norm_path(r) for r in PROJECT_ROOTS]

# Track any git repo (not just configured roots) - the core of "memory for any
# agent on the machine". Set NEVERTWICE_TRACK_ANY_PROJECT=0 to restrict to
# configured roots only (the old behaviour).
TRACK_ANY_PROJECT = os.environ.get("NEVERTWICE_TRACK_ANY_PROJECT", "1") != "0"

# Label of the agent a session came from. Generic ingestion overrides per call
# via the "agent" field on stdin; Claude Code's hook has no such field.
DEFAULT_AGENT = os.environ.get("NEVERTWICE_AGENT", "claude-code")

# Messages produced before log() is defined (this module logs from module-level
# config code). Calling log() here raised NameError at IMPORT for any scheme-less
# URL env var, killing every hook event (review 2026-08, reproduced). Flushed by
# the first log() call.
_EARLY_WARNINGS: list[str] = []

def _http_url(val: str, default: str) -> str:
    """Accept an outbound base URL only if it is http(s); otherwise fall back to the
    default. Blocks an env override (or a planted config) from redirecting a request
    carrying transcript content or an API key at a local file (file://) or another
    scheme (SSRF / LFI defence-in-depth). Loopback http stays valid - that is the
    legitimate local Ollama target. Launch-round security pass 2026-06-20."""
    v = (val or "").strip()
    low = v.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return v
    if v:
        # an override with a non-http(s) scheme is refused loudly, never honoured
        _EARLY_WARNINGS.append(
            f"Refusing non-http(s) outbound URL override ({v[:24]}…) - using default")
    return default

OLLAMA_URL = _http_url(os.environ.get("OLLAMA_URL"), "http://127.0.0.1:11434/api/generate")
OLLAMA_TAGS_URL = _http_url(os.environ.get("OLLAMA_TAGS_URL"), "http://127.0.0.1:11434/api/tags")
OLLAMA_EMBED_URL = _http_url(os.environ.get("OLLAMA_EMBED_URL"), "http://127.0.0.1:11434/api/embed")
OLLAMA_MODEL = os.environ.get("NEVERTWICE_MODEL", "qwen3:8b")   # a real public Ollama tag - the
# "just works" local default. Bigger tags (qwen3:14b, qwen3:30b-a3b, qwen3:32b) extract better if
# you have the VRAM; set NEVERTWICE_MODEL to pick one. The cloud backend is the recommended primary.
# Embedding backend selector. Default: local Ollama (bge-m3). Set
# NEVERTWICE_EMBED_PROVIDER=openai|voyage|cohere|gemini (or point
# NEVERTWICE_EMBED_BASE_URL at any OpenAI-compatible /v1/embeddings host) to get
# semantic recall with NO local model - one API key. Switching provider/model
# self-invalidates the cache via embed_signature() (stale vectors live in a
# different space; cosine across spaces is meaningless), so retrieval abstains
# until `embed_index.py --rebuild` instead of ranking against them.
EMBED_PROVIDER = os.environ.get("NEVERTWICE_EMBED_PROVIDER", "ollama").strip().lower()
# Per-provider default embedding model; NEVERTWICE_EMBED_MODEL overrides any.
# bge-m3: multilingual - on this RU/EN bilingual vault it beat nomic-embed-text by
# +0.06 semantic R@5 / +0.044 MRR (ablation 2026-06-13, I-1); symmetric (no query/
# doc task prefixes), so EMBED_USE_PREFIX defaults off.
_EMBED_DEFAULT_MODEL = {"ollama": "bge-m3", "openai": "text-embedding-3-small",
                        "voyage": "voyage-3", "cohere": "embed-v4.0",
                        "gemini": "gemini-embedding-001"}
EMBED_MODEL = (os.environ.get("NEVERTWICE_EMBED_MODEL")
               or _EMBED_DEFAULT_MODEL.get(EMBED_PROVIDER, "bge-m3"))
# Custom OpenAI-compatible embeddings endpoint (together/deepinfra/localai/…).
# Empty → resolved per provider in _embed_cloud().
EMBED_BASE_URL = _http_url(os.environ.get("NEVERTWICE_EMBED_BASE_URL"), "")  # "" → per-provider
# Task-prefix support (for nomic-style embedders that need search_query:/
# search_document:). Tracked in EMBED_META so the query side always matches the
# stored docs - switching models is safe via `embed_index.py --rebuild`.
EMBED_USE_PREFIX = os.environ.get("NEVERTWICE_EMBED_PREFIX", "0") != "0"
EMBED_DOC_PREFIX = os.environ.get("NEVERTWICE_EMBED_DOC_PREFIX", "search_document: ")
EMBED_QUERY_PREFIX = os.environ.get("NEVERTWICE_EMBED_QUERY_PREFIX", "search_query: ")
OLLAMA_TIMEOUT = env_int("NEVERTWICE_TIMEOUT", 120)
EMBED_TIMEOUT = env_int("NEVERTWICE_EMBED_TIMEOUT", 20)
OLLAMA_RETRIES = env_int("NEVERTWICE_RETRIES", 2)
OLLAMA_RETRY_BACKOFF = env_float("NEVERTWICE_RETRY_BACKOFF", 1.5)
# How long a hook waits for the vault lock before giving up and continuing read-only. Not a
# crash: that session's writes are simply not made, which is why anything that holds the lock
# across an extraction has to be able to compare its own hold against these two numbers.
HOOK_LOCK_WAIT_S = env_float("NEVERTWICE_HOOK_LOCK_WAIT_S", 180.0)       # SessionEnd/PreCompact
HOOK_LOCK_WAIT_HOT_S = env_float("NEVERTWICE_HOOK_LOCK_WAIT_HOT_S", 30.0)  # every other event

# Cloud API keys are loaded from .env by _cfg.load_dotenv() above (kept out of
# git/code). See .env.example for the supported keys.

# Cloud LLM backend (primary, fast, off-GPU) with local Ollama fallback. Pick
# via NEVERTWICE_CLOUD=cerebras|groq|gemini|deepseek|none. Cerebras/Groq/
# DeepSeek are OpenAI-compatible; Gemini uses its own REST shape. All fall back
# to Ollama on error.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "").strip()
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()

GEMINI_MODEL = os.environ.get("NEVERTWICE_GEMINI_MODEL", "gemini-2.5-flash")
GROQ_MODEL = os.environ.get("NEVERTWICE_GROQ_MODEL", "llama-3.3-70b-versatile")
CEREBRAS_MODEL = os.environ.get("NEVERTWICE_CEREBRAS_MODEL", "gpt-oss-120b")
# V4 Flash: cheap/fast non-Pro tier, ample for short-transcript JSON extraction.
DEEPSEEK_MODEL = os.environ.get("NEVERTWICE_DEEPSEEK_MODEL", "deepseek-v4-flash")

GEMINI_URL = _http_url(os.environ.get("GEMINI_URL"),
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent")
GROQ_URL = _http_url(os.environ.get("GROQ_URL"), "https://api.groq.com/openai/v1/chat/completions")
CEREBRAS_URL = _http_url(os.environ.get("CEREBRAS_URL"),
                         "https://api.cerebras.ai/v1/chat/completions")
DEEPSEEK_URL = _http_url(os.environ.get("DEEPSEEK_URL"),
                         "https://api.deepseek.com/v1/chat/completions")

GEMINI_TIMEOUT = env_int("NEVERTWICE_GEMINI_TIMEOUT", 60)
GEMINI_RETRIES = env_int("NEVERTWICE_GEMINI_RETRIES", 2)
GEMINI_RETRY_BACKOFF = env_float("NEVERTWICE_GEMINI_BACKOFF", 2.0)

# Cerebras sits behind Cloudflare and 403s the default Python-urllib UA.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _resolve_cloud() -> str:
    choice = os.environ.get("NEVERTWICE_CLOUD", "auto").strip().lower()
    if choice in ("cerebras", "groq", "gemini", "deepseek", "none"):
        return choice
    if CEREBRAS_API_KEY:
        return "cerebras"
    if GROQ_API_KEY:
        return "groq"
    if DEEPSEEK_API_KEY:
        return "deepseek"
    if GEMINI_API_KEY:
        return "gemini"
    return "none"


ACTIVE_CLOUD = _resolve_cloud()
#: Which environment variable each provider's key comes from. The names, not the values: a
#: key is a credential that rotates, and it is read on every call (see `provider_key`).
#: `ACTIVE_CLOUD` stays resolved once on purpose - choosing a backend is a configuration
#: decision taken at start-up, not something that changes under a running process.
_CLOUD_KEY_ENV = {"cerebras": "CEREBRAS_API_KEY", "groq": "GROQ_API_KEY",
                  "gemini": "GEMINI_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}
_CLOUD_MODELS = {"cerebras": CEREBRAS_MODEL, "groq": GROQ_MODEL,
                 "gemini": GEMINI_MODEL, "deepseek": DEEPSEEK_MODEL}


def provider_key(provider: str) -> str:
    """One provider's API key, read from the environment on this call.

    The four keys were captured into a dict at import, so a key exported AFTER the module was
    imported was invisible and a rotated one kept its old value until the process restarted.
    On the hook path that costs nothing - a process per event - but `mcp_server` is a stdio
    server that lives for the whole client session and `watch` is a daemon, and on those
    rotation silently did nothing and said nothing. The same reason `_twin_file_path` and
    `_lock_file` resolve on call rather than at import.
    """
    return os.environ.get(_CLOUD_KEY_ENV.get(provider, ""), "").strip()


def cloud_key() -> str:
    return provider_key(ACTIVE_CLOUD)

# Projects that must NEVER use a cloud backend (sensitive/novel research). Their
# transcripts are extracted ONLY by local Ollama, nothing leaves the machine.
LOCAL_ONLY_PROJECTS = {p.strip().lower() for p in
                       os.environ.get("NEVERTWICE_LOCAL_ONLY", "").split(",")
                       if p.strip()}
# Fail-safe allowlist: if set, ONLY these projects may use the cloud; every
# other project - INCLUDING any new/unknown one - stays local. Takes precedence
# over LOCAL_ONLY_PROJECTS so a forgotten project can never leak by default.
CLOUD_ONLY_PROJECTS = {p.strip().lower() for p in
                       os.environ.get("NEVERTWICE_CLOUD_ONLY", "").split(",")
                       if p.strip()}


def is_local_only(project) -> bool:
    """Decide if a project must stay local. Allowlist mode (CLOUD_ONLY set):
    only listed projects use cloud, everything else (incl. unknown/empty) is
    local - fail-safe. Else denylist mode: listed projects are local.

    Both sides are normalised, which they were not. The sets are built from the environment with
    `.strip().lower()` while every caller passes the output of `slug_project()` - which also
    transliterates Cyrillic, folds spaces and punctuation to `_` and truncates at 40. So
    `NEVERTWICE_LOCAL_ONLY=my-secret-research`, written exactly as `docs/CONFIG.md` describes it,
    never matched the slug `my_secret_research`: the denylist failed OPEN and the transcript went to
    the cloud with no warning, and the allowlist failed the other way and pinned every project local
    forever. A privacy control that compares two different normalisations is not a control.

    Each configured name is matched in both shapes, and so is the argument, because callers are not
    consistent either: the engine passes a slug, a library user may pass the directory name."""
    p = (project or "").strip().lower()
    forms = {p, slug_project(p)} if p else {p}
    if CLOUD_ONLY_PROJECTS:
        return not (forms & _normalise_project_set(CLOUD_ONLY_PROJECTS))
    return bool(forms & _normalise_project_set(LOCAL_ONLY_PROJECTS))


def local_routing_desc() -> str:
    if CLOUD_ONLY_PROJECTS:
        return "allowlist - cloud ONLY for: " + ", ".join(sorted(CLOUD_ONLY_PROJECTS))
    if LOCAL_ONLY_PROJECTS:
        return "local-only: " + ", ".join(sorted(LOCAL_ONLY_PROJECTS))
    return "all tracked projects use cloud"

MAX_TRANSCRIPT_CHARS = env_int("NEVERTWICE_MAX_TRANSCRIPT", 12000)
# Minimum transcript growth, in JSONL bytes, before a processed session is mined again.
# The default is one byte: any growth. It was one extractor window for two days, which
# lost the end of every session that finished within 12 kB of its PreCompact mark - the
# SessionEnd hook, both sweeps and process_now gate on the same predicate, so nothing
# ever came back for that tail (review 2026-09-05). The fork the window was meant to
# prevent is closed at its source instead: a re-mine reads only the new region and keeps
# the session's own start date. Raise this only to trade completeness for extractor calls.
REMINE_MIN_GROWTH_BYTES = env_int("NEVERTWICE_REMINE_MIN_GROWTH", 1)
# Head share of the budget when a long transcript is split head+tail (audit M3:
# a fixed 2000-char head lost the project setup on long sessions). 0-override via
# NEVERTWICE_TRUNCATE_HEAD_CHARS; else derived from the fraction.
TRUNCATE_HEAD_FRAC = env_float("NEVERTWICE_TRUNCATE_HEAD_FRAC", 0.4)
TRUNCATE_HEAD_CHARS = env_int("NEVERTWICE_TRUNCATE_HEAD_CHARS", 0)
MAX_MESSAGE_CHARS = 2000  # per-event cap before global truncation

# Sweep: this window is used ONLY for performance ordering, NEVER to skip a
# never-processed transcript. Disk retention is ~30d, so the old 7d hard
# cutoff silently lost memory (audit F28) - sweep_unprocessed now processes
# any tracked-but-unprocessed transcript regardless of age.
SWEEP_ORDER_DAYS = env_int("NEVERTWICE_SWEEP_DAYS", 30)
SESSION_START_SWEEP_CAP = env_int("NEVERTWICE_SWEEP_CAP", 8)
SESSION_END_SWEEP_CAP = env_int("NEVERTWICE_SWEEP_CAP_END", 25)
ARCHIVE_AFTER_DAYS = env_int("NEVERTWICE_ARCHIVE_DAYS", 30)
TYPED_ARCHIVE_AFTER_DAYS = env_int("NEVERTWICE_TYPED_ARCHIVE_DAYS", 90)
PRUNE_DB_AFTER_DAYS = env_int("NEVERTWICE_PRUNE_DAYS", 90)

# Context compaction (audit F4/F23/F37): cap unbounded append-only growth.
CONTEXT_MAX_BYTES = env_int("NEVERTWICE_CONTEXT_MAX_BYTES", 12000)
# One journal entry's cap (chars): a single unbounded LLM context_update could
# otherwise blow the whole file budget in one append (review 2026-08).
CONTEXT_ENTRY_MAX_CHARS = env_int("NEVERTWICE_CONTEXT_ENTRY_MAX", 4000)
CONTEXT_KEEP_RECENT = env_int("NEVERTWICE_CONTEXT_KEEP_RECENT", 12)
# Floor for how many recent entries to keep verbatim when the byte cap forces
# aggressive compaction (audit M2: the cap is now hard, not "12 entries of any size").
CONTEXT_KEEP_MIN = env_int("NEVERTWICE_CONTEXT_KEEP_MIN", 3)
CONTEXT_LINK_ARCHIVE_MAX = env_int("NEVERTWICE_CONTEXT_LINKS_MAX", 60)

# SessionStart retrieval injection (audit F35/F36)
RETRIEVAL_TOP_K = env_int("NEVERTWICE_RETRIEVAL_K", 5)
INJECT_CONTEXT = os.environ.get("NEVERTWICE_INJECT", "1") != "0"
# Budget-aware injection (M-15): cap the SessionStart payload so it never bloats
# the context window. Sections are added by priority (card → mistakes → patterns →
# cross-project) until the budget is hit. ~2200 chars ≈ 550 tokens.
INJECT_BUDGET_CHARS = env_int("NEVERTWICE_INJECT_BUDGET_CHARS", 2200)
# Abstention on the session-start payload, as a FRACTION of the best item in the same
# section. A lesson weaker than this is refused even when there is room for it - which
# truncation, being a size check, can never do.
#
# DEFAULT 0 - OFF - on the measurement, not on doubt. It shipped at 0.35 with tests proving
# the mechanism works and nothing measuring whether it helps. `research/abstention_ab.py`
# swept it over the labelled corpus in `research/data/supersession_v1.json`: at 0.35 the
# payload is 26.6% smaller and the wanted fact is present 8.7 points less often. The gate
# written before the run asked for a 20% saving with at most 2 points of loss, and no
# threshold on the curve clears both - the cheapest useful one, 0.10, costs 1.5 points for a
# 5% saving. Sixty characters is not worth an eight-point drop in finding the right lesson.
# Left in as an opt-in switch, because the trade may well go the other way on a store where
# recall returns ten hits rather than one and a half.
INJECT_MIN_VALUE = env_float("NEVERTWICE_INJECT_MIN_VALUE", 0.0)
# The injection reports its own cost, what the budget refused, and what it saved (receipt.py).
# NO room is reserved: the payload is assembled exactly as without a receipt and the line is
# appended only into leftover slack (degrading/vanishing rather than displacing a lesson) -
# so 0 disables it and the payload is byte-identical to a receipt-less build either way.
INJECT_RECEIPT = os.environ.get("NEVERTWICE_INJECT_RECEIPT", "1") != "0"
# Minimum cosine for a semantic hit to count, and how much a recurring lesson is
# boosted in ranking (audit H4/LOW: recurrence was computed but never used).
# 0.40 (was 0.30, which sat below the bge-m3 background and never fired): measured on the live
# vault the real note↔note top-1 minimum is 0.418 (p1=0.46), while gibberish tops out ~0.43 - so
# 0.40 is the highest floor with ZERO false-negatives on real notes, making the floor a non-inert
# defense-in-depth layer that catches the lowest-scoring noise. It is NOT raised to ~0.45 (the
# audit's suggestion) because real/noise OVERLAP at the boundary (the W2 compression ceiling), so a
# higher floor would abstain on genuinely-weak-but-real queries; the corpus-adaptive margin gate
# below stays the PRIMARY abstention mechanism (it caught 6/6 gibberish where the floor can't).
RETRIEVAL_SIM_FLOOR = env_float("NEVERTWICE_SIM_FLOOR", 0.40)
# The nearest-neighbour inclusion floor for INTERACTIVE search (CLI / MCP / api.recall) and the
# SQLite diagnostic path - deliberately permissive and DISTINCT from the confident-injection floor
# above. A user-initiated query returns the closest notes even when weak (the caller labels them
# low-confidence); auto-injection on the hook path still uses RETRIEVAL_SIM_FLOOR. Named here so the
# two floors stay in one place instead of a magic 0.15 copied across modules (audit 2026-06-18).
RETRIEVAL_NEAR_FLOOR = env_float("NEVERTWICE_NEAR_FLOOR", 0.15)
# Confidence gate (dogfood W1/W3): bge-m3 cosines bunch near a high background (~0.42 on a
# real vault), so the absolute floor alone never fires - a nonsense query scores like a real
# one. A confident match must ALSO stand this far above the per-query MEDIAN similarity; below
# it, the semantic signal is dropped so the hook injects lexical/nothing, not arbitrary notes.
RETRIEVAL_CONFIDENT_MARGIN = env_float("NEVERTWICE_CONFIDENT_MARGIN", 0.15)
# Two recurrence boosts on two scales (research/longitudinal_bench.py calibrates both):
#   RECUR_BOOST (~0.03) is added to raw COSINE in single-signal paths (cf. _recur_boost);
#   RECUR_RRF_BOOST (~0.0003) is added to fused RRF scores in retrieve_relevant, whose
#   adjacent-rank gap is ~1/60 - so it is a deliberate gentle TIEBREAKER there (the 3A
#   benchmark finds this small value Pareto-optimal; larger values hurt crisp queries).
RETRIEVAL_RECUR_BOOST = env_float("NEVERTWICE_RECUR_BOOST", 0.03)
RETRIEVAL_RECUR_RRF_BOOST = env_float("NEVERTWICE_RECUR_RRF_BOOST", 0.0003)
# Calibrated fusion produces logistic scores in (0,1) (vs RRF's ~1/60 gaps), so the
# recurrence tiebreak needs a proportionally larger constant to stay a gentle tiebreak.
# Inert on a no-recurrence corpus (log(1)=0), so it never moves the benchmark.
RETRIEVAL_RECUR_FUSION_BOOST = env_float("NEVERTWICE_RECUR_FUSION_BOOST", 0.02)
# Ambiguity-adaptive recurrence (research/ABLATION_RESULTS.md): the recurrence prior
# is scaled by how ambiguous the relevance signal is (bunched top sims → up; a clear
# leader → down), so recurrence helps exactly when relevance can't decide and never
# displaces a crisp match. Inert when recurrence=1 (confirmed no-harm on LongMemEval).
ADAPTIVE_RECUR = os.environ.get("NEVERTWICE_ADAPTIVE_RECUR", "1") != "0"
AMBIGUITY_K = env_float("NEVERTWICE_AMBIGUITY_K", 15)
# Time-decay + salience (M-3): gently favour recent lessons without burying old gold.
# A note keeps at least DECAY_FLOOR of its score; half-life in days (0 disables).
# Resolved mistakes are down-weighted (no longer active warnings).
RETRIEVAL_DECAY_HALFLIFE = env_float("NEVERTWICE_DECAY_HALFLIFE", 365)
RETRIEVAL_DECAY_FLOOR = env_float("NEVERTWICE_DECAY_FLOOR", 0.5)
RETRIEVAL_RESOLVED_WEIGHT = env_float("NEVERTWICE_RESOLVED_WEIGHT", 0.6)
# Confidence-aware ranking (H2): the per-note confidence (M-10) was stamped into
# frontmatter and asked of the LLM but never READ - a write-only dead field. Now
# a low-confidence lesson is gently down-weighted in recall, floored so it's never
# buried; a note without confidence is treated as fully confident (neutral).
RETRIEVAL_CONF_FLOOR = env_float("NEVERTWICE_CONF_FLOOR", 0.6)
# Salience nudge (Brain F5): a note central to the knowledge graph (its entities referenced by
# the rest of the store) gets a gentle recall boost - recurrence generalised to centrality. The
# score is stamped sleep-time by consolidation; UNSTAMPED notes read 0 → ×1.0, so this is INERT
# on an entity-less/benchmark corpus and never moves the calibrated ranking there. Max +SALIENCE_BOOST.
RETRIEVAL_SALIENCE_BOOST = env_float("NEVERTWICE_SALIENCE_BOOST", 0.1)
# Graph multi-hop expansion (M-6): after ranking, pull in notes linked from the
# top hits (RESOLVES/SUPERSEDES/[[wikilinks]]) so "A→B→C" chains are reachable.
# 0 = off (keeps injection lean); set NEVERTWICE_GRAPH_HOPS=1 to enable by default.
GRAPH_HOPS = env_int("NEVERTWICE_GRAPH_HOPS", 0)
# Relation-aware injection (Phase 2b on the hot path): after ranking, append up to N
# graph-connected lessons reached by the top hits' typed edges (a bug surfaces its fix).
# 0 = off (default - keeps SessionStart injection precise + token-lean); applied ONLY at
# SessionStart (a frontmatter scan, once per session), never on the per-prompt path.
RELATION_EXPAND = env_int("NEVERTWICE_RELATION_EXPAND", 0)
# Fact-vs-code staleness check (M-4): annotate injected notes whose referenced
# file paths no longer exist. Off by default (a heuristic - opt in per project).
STALE_CHECK = os.environ.get("NEVERTWICE_STALE_CHECK", "0") != "0"
# Weight of the semantic ranking in the hybrid RRF fusion. With a strong
# multilingual embedder (bge-m3) semantic alone beats equal-weight hybrid, so we
# let it lead while lexical still backs it up (ablation 2026-06-13).
RETRIEVAL_SEM_WEIGHT = env_float("NEVERTWICE_SEM_WEIGHT", 2.0)
# Fusion of the semantic + lexical signals. "calibrated" (default) z-normalises each
# signal's SCORES over the candidate set and combines the magnitudes - measured to beat
# rank-fusion decisively (RRF throws the magnitudes away, so it trails even plain BM25;
# calibrated fusion lifts LongMemEval R@5 0.66→0.80 and overtakes Mem0). "rrf" keeps the
# legacy reciprocal-rank fusion as a fallback. See research/RETRIEVAL_FUSION.md.
RETRIEVAL_FUSION = os.environ.get("NEVERTWICE_FUSION", "calibrated").strip().lower()
# Dense (semantic) weight in calibrated fusion; the lexical (BM25) weight is fixed at 1.0.
# 0.5 was tuned when the stand embedded the first 2,000 characters of a session and the lexical
# arm scored raw tokens. With whole-session vectors and stemmed, stop-word-free BM25 the sweep
# moved (research/fusion_sweep.py, 2026-09-06): 1.0 beat 0.5 by 0.012 R@5 on the oracle pool
# and by 0.014 on LoCoMo, clearing the gate written first (ledger I1: >= 0.01 on one corpus,
# no more than 0.005 lost on the other); 0.75 cleared it on one corpus only, 1.5 lost on the
# oracle. The whole curve is on research/RETRIEVAL_FUSION.md.
FUSION_SEM_WEIGHT = env_float("NEVERTWICE_FUSION_SEM_WEIGHT", 1.0)
# Ranker selector (research/posterior_model.py, 1A). "hybrid" (default) = the shipped
# additive-recurrence + multiplicative-salience tail. "posterior" = the same signals as
# an explicit log-linear posterior: w_rel·log(rrf) + w_freq·log(n) + w_sal·log(salience),
# each prior a separable, reweightable term (the static form 1B then learns online). The
# research module showed the FITTED posterior beats the hand-tuned heuristic in-distribution;
# defaults here keep relevance dominant and recurrence a frequency prior.
RANKER = os.environ.get("NEVERTWICE_RANKER", "hybrid").strip().lower()
POST_W = {k: env_float(f"NEVERTWICE_POST_W_{k.upper()}", d)
          for k, d in (("rel", 1.0), ("freq", 0.3), ("sal", 1.0))}
# Divergent/serendipitous recall (research/divergent.py, 2B): >0 re-ranks the top
# candidates by Maximal Marginal Relevance, trading a little relevance for diversity
# (fewer near-duplicates, more cross-topic surfacing). 0 (default) = convergent, no change.
RETRIEVAL_DIVERGENCE = max(0.0, min(1.0, env_float("NEVERTWICE_DIVERGENCE", 0)))
# Short embed timeout for interactive retrieval: fail fast to lexical when the
# GPU is busy instead of stalling SessionStart up to EMBED_TIMEOUT (audit H5).
RETRIEVAL_EMBED_TIMEOUT = env_int("NEVERTWICE_RETRIEVAL_EMBED_TIMEOUT", 5)
# Above this many candidates, retrieval FTS-prefilters to the top-N lexical matches
# before cosine, so one huge project can't stall a prompt with a full brute-force
# scan (improvement P1). Smaller projects keep an exact full scan - no recall loss.
RETRIEVAL_PREFILTER_LIMIT = env_int("NEVERTWICE_PREFILTER_LIMIT", 600)
# Cross-project transfer (I-7): surface a few lessons from OTHER projects that
# are highly relevant (shared stack → transferable gotchas). Higher bar to keep
# noise out. Toggle off with NEVERTWICE_CROSS_PROJECT=0.
INJECT_CROSS_PROJECT = os.environ.get("NEVERTWICE_CROSS_PROJECT", "1") != "0"
CROSS_PROJECT_K = env_int("NEVERTWICE_CROSS_K", 2)
CROSS_PROJECT_SIM_FLOOR = env_float("NEVERTWICE_CROSS_SIM_FLOOR", 0.5)
# Learned user model (I-6): inject a short cross-project working profile (built
# by build_user_model.py → User/profile.md). Off with NEVERTWICE_USER_MODEL=0.
INJECT_USER_MODEL = os.environ.get("NEVERTWICE_USER_MODEL", "1") != "0"
# Cloud-as-judge rerank (I-3): reorder retrieval candidates with a free cloud
# model. Opt-in (adds cloud latency, marginal over bge-m3) and never on the hot
# injection paths - only deliberate on-demand search. On with NEVERTWICE_RERANK=1.
RERANK_ENABLED = os.environ.get("NEVERTWICE_RERANK", "0") != "0"
RERANK_POOL = env_int("NEVERTWICE_RERANK_POOL", 15)
# Structured project card (audit I-15): distil the project's live notes into a
# high-signal block (status · stack · open gotchas · key decisions · recurring)
# kept at the top of Context/<project>.md and injected instead of the raw journal
# tail - cheaper to inject, higher signal. Off with NEVERTWICE_PROJECT_CARD=0.
PROJECT_CARD_ENABLED = os.environ.get("NEVERTWICE_PROJECT_CARD", "1") != "0"
CARD_MAX_ITEMS = env_int("NEVERTWICE_CARD_MAX_ITEMS", 5)
CARD_START = "<!-- PROJECT-CARD:START -->"
CARD_END = "<!-- PROJECT-CARD:END -->"
CARD_HEADER = "## 🗂 Project card"
# Task-aware recall (I-4): on UserPromptSubmit, retrieve by the actual PROMPT
# text (not just project state) and inject targeted lessons. The single biggest
# recall-quality win - the start-of-session injection can't know the task yet.
# Off with NEVERTWICE_PROMPT_RECALL=0.
PROMPT_RECALL_ENABLED = os.environ.get("NEVERTWICE_PROMPT_RECALL", "1") != "0"
# Policy: 'smart' (substantial prompts, per-session dedup, capped) | 'once'
# (first substantial prompt only) | 'every' (every non-trivial prompt).
PROMPT_RECALL_MODE = os.environ.get("NEVERTWICE_PROMPT_RECALL_MODE", "smart").strip().lower()
PROMPT_RECALL_K = env_int("NEVERTWICE_PROMPT_RECALL_K", 3)
# Soft ceiling on injections per session, so a long session can't keep paying the
# recall cost indefinitely (dedup already self-throttles).
PROMPT_RECALL_MAX_PER_SESSION = env_int("NEVERTWICE_PROMPT_RECALL_MAX", 6)
# Prompts shorter than this (after trimming) are treated as trivial → skipped.
PROMPT_RECALL_MIN_CHARS = env_int("NEVERTWICE_PROMPT_RECALL_MIN_CHARS", 16)
# Abstention on the per-turn path. A hit weaker than this FRACTION of the batch's best hit
# is refused even when there is room for it - the distinction `budget.py` exists to make and
# the one truncation can never make. 0 disables and restores take-the-top-K behaviour.
# DEFAULT 0 - OFF, for the reason recorded at INJECT_MIN_VALUE above: measured, missed its
# pre-declared gate, kept as an opt-in switch rather than deleted.
PROMPT_RECALL_MIN_VALUE = env_float("NEVERTWICE_PROMPT_RECALL_MIN_VALUE", 0.0)
# Tight budget so recall never noticeably delays an interactive prompt: a busy
# GPU fails the ping fast and the path drops to lexical-only.
PROMPT_RECALL_EMBED_TIMEOUT = env_int("NEVERTWICE_PROMPT_RECALL_EMBED_TIMEOUT", 2)
PROMPT_RECALL_ALIVE_TIMEOUT = env_int("NEVERTWICE_PROMPT_RECALL_ALIVE_TIMEOUT", 1)
PROMPT_RECALL_STATE_DIR = VAULT / ".prompt_recall"

STATUS_HISTORY_LIMIT = 50
LOG_MAX_BYTES = 1_000_000
LOCK_STALE_S = 600
LOCK_RETRY_S = 0.5

# Windows reserved device names - must never become a file stem (audit C3)
WIN_RESERVED = {"con", "prn", "aux", "nul",
                *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10))}

TYPED_TYPES = ("pattern", "mistake", "decision")
TYPE_FOLDER = {"pattern": "Patterns", "mistake": "Mistakes", "decision": "Decisions"}
TYPE_ICON = {"pattern": "✅", "mistake": "⚠️", "decision": "🎯"}

EXTRACTION_PROMPT = """Analyze this agent session and extract the durable knowledge.

RULE - injected boilerplate is NOT session content. Transcripts often carry the agent's
configuration re-sent every turn: global instructions (CLAUDE.md), "Active Projects"
lists, system reminders, tool definitions. NEVER extract knowledge from such blocks and
NEVER attribute work to a project merely because its name appears in them - project
attribution comes only from the actual commands, files and paths the session worked on.

Known parameters:
  project (use exactly this value): {project_hint}
  preferred tags (pick from this list when one fits, lowercase; invent a new one only if nothing fits):
    {tag_vocab}
  notes that already exist in this project (do NOT duplicate - skip an item whose substance matches):
    patterns: {existing_patterns}
    mistakes: {existing_mistakes}
    decisions: {existing_decisions}

SESSION:
{transcript}

Return ONLY valid JSON. No markdown, no commentary.

Schema:
{{
  "project": "{project_hint}",
  "project_relevant": true,
  "patterns": [
    {{"title": "short title (3-7 words, kebab-case or a phrase)",
      "description": "what worked and why (1-3 sentences)",
      "facts": ["literal token copied VERBATIM from the session, [] if none"],
      "supersedes": "", "contradicts": "", "resolves": "",
      "entities": ["key-entity", "another-one"],
      "relations": [{{"rel": "fixes", "target": "entity"}}], "confidence": 0.9}}
  ],
  "mistakes": [
    {{"title": "short title of the mistake",
      "description": "what went wrong",
      "facts": ["literal token copied VERBATIM from the session, [] if none"],
      "prevention": "one line: the concrete action/check that avoids a repeat",
      "supersedes": "", "contradicts": "",
      "entities": ["key-entity", "another-one"],
      "relations": [{{"rel": "caused-by", "target": "entity"}}], "confidence": 0.9}}
  ],
  "decisions": [
    {{"title": "short title of the decision",
      "description": "what was decided and the reasoning",
      "facts": ["literal token copied VERBATIM from the session, [] if none"],
      "supersedes": "", "contradicts": "", "resolves": "",
      "entities": ["key-entity", "another-one"],
      "relations": [{{"rel": "alternative-to", "target": "entity"}}], "confidence": 0.9}}
  ],
  "context_update": "project-context update (1-3 sentences on the current state)",
  "session_summary": "what the session accomplished (2-4 sentences)",
  "tags": ["tag1", "tag2", "tag3"]
}}

LANGUAGE: {language_rule} Tags and entities stay lowercase ASCII kebab-case.

All tags lowercase. Empty categories = []. If the session is trivial (reading/discussion),
return everything empty and fill only session_summary.

FIELD project_relevant - CRITICAL for memory hygiene:
  - true  - the session is genuinely about project {project_hint} (its code/research/tasks).
  - false - offtopic: an unrelated question, personal troubleshooting (games, OS, hardware
            off-topic), a different project, a model switch, an empty dialog. THEN return
            patterns/mistakes/decisions = [] and context_update = "", fill ONLY session_summary.
  Never pollute a project's knowledge with offtopic material - empty beats wrong.

FIELD supersedes (on every item) - keeps the memory current:
  If an item REPLACES or REFUTES a note already listed above (status changed, decision
  revised, mistake eliminated) - put that note's EXACT title. Otherwise leave "".
  That way the old note can never contradict the new one.

FIELD resolves (pattern/decision only) - link a fix to the mistake it removes:
  If this pattern/decision ELIMINATES a specific mistake from the lists above - put that
  mistake's EXACT title. Otherwise "". A resolved mistake stops being an active warning
  (it is marked solved) but stays in the history.

FIELD contradicts - contradiction detection:
  If an item DIRECTLY CONTRADICTS an existing note above (an incompatible claim/choice,
  not a mere update) - put that note's EXACT title. The contradicted old note is retired
  and the current truth remains.

FIELD confidence (0.0-1.0) - how durable this knowledge is, versus a one-off detail.
  High (0.8-1.0) for verified facts; low (<0.5) for guesses.

FIELD facts (on every item) - the LITERAL tokens a future question will ask for:
  Copy VERBATIM from the session, character for character - a command, a file path, a git hash,
  a flag, an exact number or list, a version, an identifier, an error string. Do NOT paraphrase,
  normalise, translate or summarise them, and do NOT invent. If the session says
  `nvcc -arch=sm_120`, write "nvcc -arch=sm_120", never "sm_120 support". Only strings that
  appear in the session verbatim; [] when the item has no such literal. Any value that is not an
  exact substring of the session is dropped automatically, so a paraphrase simply vanishes.

FIELDS entities/relations (optional) - the knowledge graph:
  entities - 2-5 key entities of the lesson (tools/concepts/files), lowercase kebab-case,
  no versions. relations - edges {{"rel": type, "target": entity}}, target in the same style;
  rel from: causes, caused-by, fixes, fixed-by, depends-on, requires, part-of, alternative-to,
  related-to. Every relations target must ALSO appear in some lesson's entities (add it to
  this lesson's entities if nothing else carries it) - an edge to an entity no lesson is
  tagged with cannot be followed. E.g. for a CUDA OOM: entities ["cuda","batch-size"],
  relations [{{"rel":"fixed-by","target":"gradient-checkpointing"}}]. Unclear - [].{brain_block}"""


def log(msg):
    global _EARLY_WARNINGS
    if _EARLY_WARNINGS:                 # config-time messages queued before log existed
        pending, _EARLY_WARNINGS = _EARLY_WARNINGS, []
        for w in pending:
            log(w)
    line = f"[memory_hook {datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, file=sys.stderr)
    try:  # also persist to a rotating file so hook failures are debuggable
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
            LOG_FILE.replace(LOG_FILE.with_name("memory_hook.log.1"))
        # newline="" here too: text mode rewrote every line of the hook log as CRLF, which
        # is the same door as `write_text` one spelling further on (review 2026-09-21).
        with open(LOG_FILE, "a", encoding="utf-8", newline="") as f:
            f.write(line + "\n")
    except OSError:
        pass


def argval(argv, name: str, default=None):
    """One CLI flag reader for every satellite CLI (digest, dashboard, guards, …):
    accepts both `--name=value` and `--name value`, returns `default` when absent.
    Replaces six hand-rolled copies that each understood only one of the two forms."""
    for a in argv:
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    if f"--{name}" in argv:
        i = argv.index(f"--{name}") + 1
        if i < len(argv) and not argv[i].startswith("--"):
            return argv[i]
    return default


def argint(argv, name: str, default: int, *, minimum: int = 1) -> int:
    """`argval` for a flag that must be a whole number, refusing at the door.

    Four satellite CLIs wrapped `argval` in a bare `int()`, so `--days=last-week` came out
    of `main()` as a ValueError traceback - the one output that tells a user nothing about
    what to type instead - and `--days=-5` was accepted into arithmetic that silently
    answered a different question. Exit 2 with the flag named, like every other usage error
    in the package.
    """
    raw = argval(argv, name, None)
    if raw is None:
        return default
    try:
        value = int(str(raw))
    except ValueError:
        value = minimum - 1
    if value < minimum:
        print(f"--{name}= needs an integer >= {minimum}, got {str(raw)!r}", file=sys.stderr)
        sys.exit(2)
    return value


