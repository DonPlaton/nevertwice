#!/usr/bin/env python3
"""Claude Code Memory Hook — extracts session knowledge into an Obsidian vault.

Triggers:
  SessionEnd / PreCompact: process the just-finished session.
  SessionStart:            sweep transcripts left behind by abrupt closes
                           (VS Code killed, OS crash — SessionEnd never fired).

Pipeline per session:
  read_transcript → call_ollama(format=json) → write Patterns / Mistakes /
  Decisions → write Session note → update Context → rebuild Index → archive
  old sessions, prune old DB rows.
"""

import json
import math
import statistics
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────
# Paths come from config.py (cross-platform, env-overridable) so the same code
# runs on any machine. Importable both as a package and as a flat script dir.
try:
    from . import config as _cfg
except ImportError:  # run directly (sys.path includes this dir)
    import config as _cfg

_cfg.load_dotenv()                 # pull cloud keys from .env if present

VAULT = _cfg.VAULT
VAULT.mkdir(parents=True, exist_ok=True)
PROCESSED_DB = VAULT / ".processed_sessions.json"
STATUS_FILE = VAULT / "status.txt"
LOCK_FILE = VAULT / ".lock"
EMBED_CACHE = VAULT / ".embeddings_cache.json"
EMBED_META = VAULT / ".embeddings_meta.json"   # records whether the cache is prefixed
LOG_FILE = VAULT / ".logs" / "memory_hook.log"

PROJECTS_ROOT = _cfg.PROJECTS_ROOT


def _split_roots(raw: str) -> list[str]:
    out = []
    for chunk in (raw or "").replace(os.pathsep, ",").split(","):
        c = chunk.strip().rstrip("\\/")
        if c:
            out.append(c)
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
# any git repository — excluding system, agent-internal (~/.claude), the vault
# itself, and transient (Temp/Recycle) paths. Multiple roots via
# ANAMNESIS_PROJECT_ROOTS (os.pathsep- or comma-separated); the legacy
# single ANAMNESIS_PROJECT_ROOT still works as a fallback. (audit C2)
PROJECT_ROOTS = _split_roots(
    os.environ.get("ANAMNESIS_PROJECT_ROOTS", "").strip()
    or os.environ.get("ANAMNESIS_PROJECT_ROOT", ""))   # empty default → rely on
#   git-repo detection below; set ANAMNESIS_PROJECT_ROOTS to also track a flat root
PROJECT_ROOT_DISPLAY = PROJECT_ROOTS[0] if PROJECT_ROOTS else ""
PROJECT_ROOT_FILTER = PROJECT_ROOT_DISPLAY.lower()  # back-compat: first root
_ROOTS_NORM = [_norm_path(r) for r in PROJECT_ROOTS]

# Track any git repo (not just configured roots) — the core of "memory for any
# agent on the machine". Set ANAMNESIS_TRACK_ANY_PROJECT=0 to restrict to
# configured roots only (the old behaviour).
TRACK_ANY_PROJECT = os.environ.get("ANAMNESIS_TRACK_ANY_PROJECT", "1") != "0"

# Label of the agent a session came from. Generic ingestion overrides per call
# via the "agent" field on stdin; Claude Code's hook has no such field.
DEFAULT_AGENT = os.environ.get("ANAMNESIS_AGENT", "claude-code")

def _http_url(val: str, default: str) -> str:
    """Accept an outbound base URL only if it is http(s); otherwise fall back to the
    default. Blocks an env override (or a planted config) from redirecting a request
    carrying transcript content or an API key at a local file (file://) or another
    scheme (SSRF / LFI defence-in-depth). Loopback http stays valid — that is the
    legitimate local Ollama target. Launch-round security pass 2026-06-20."""
    v = (val or "").strip()
    low = v.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return v
    if v:
        # an override with a non-http(s) scheme is refused loudly, never honoured
        log(f"Refusing non-http(s) outbound URL override ({v[:24]}…) — using default")
    return default

OLLAMA_URL = _http_url(os.environ.get("OLLAMA_URL"), "http://127.0.0.1:11434/api/generate")
OLLAMA_TAGS_URL = _http_url(os.environ.get("OLLAMA_TAGS_URL"), "http://127.0.0.1:11434/api/tags")
OLLAMA_EMBED_URL = _http_url(os.environ.get("OLLAMA_EMBED_URL"), "http://127.0.0.1:11434/api/embed")
OLLAMA_MODEL = os.environ.get("ANAMNESIS_MODEL", "qwen3:8b")   # a real public Ollama tag — the
# "just works" local default. Bigger tags (qwen3:14b, qwen3:30b-a3b, qwen3:32b) extract better if
# you have the VRAM; set ANAMNESIS_MODEL to pick one. The cloud backend is the recommended primary.
# Embedding backend selector. Default: local Ollama (bge-m3). Set
# ANAMNESIS_EMBED_PROVIDER=openai|voyage|cohere|gemini (or point
# ANAMNESIS_EMBED_BASE_URL at any OpenAI-compatible /v1/embeddings host) to get
# semantic recall with NO local model — one API key. Switching provider/model
# self-invalidates the cache via embed_signature() (stale vectors live in a
# different space; cosine across spaces is meaningless), so retrieval abstains
# until `embed_index.py --rebuild` instead of ranking against them.
EMBED_PROVIDER = os.environ.get("ANAMNESIS_EMBED_PROVIDER", "ollama").strip().lower()
# Per-provider default embedding model; ANAMNESIS_EMBED_MODEL overrides any.
# bge-m3: multilingual — on this RU/EN bilingual vault it beat nomic-embed-text by
# +0.06 semantic R@5 / +0.044 MRR (ablation 2026-06-13, I-1); symmetric (no query/
# doc task prefixes), so EMBED_USE_PREFIX defaults off.
_EMBED_DEFAULT_MODEL = {"ollama": "bge-m3", "openai": "text-embedding-3-small",
                        "voyage": "voyage-3", "cohere": "embed-v4.0",
                        "gemini": "gemini-embedding-001"}
EMBED_MODEL = (os.environ.get("ANAMNESIS_EMBED_MODEL")
               or _EMBED_DEFAULT_MODEL.get(EMBED_PROVIDER, "bge-m3"))
# Custom OpenAI-compatible embeddings endpoint (together/deepinfra/localai/…).
# Empty → resolved per provider in _embed_cloud().
EMBED_BASE_URL = _http_url(os.environ.get("ANAMNESIS_EMBED_BASE_URL"), "")  # "" → per-provider
# Task-prefix support (for nomic-style embedders that need search_query:/
# search_document:). Tracked in EMBED_META so the query side always matches the
# stored docs — switching models is safe via `embed_index.py --rebuild`.
EMBED_USE_PREFIX = os.environ.get("ANAMNESIS_EMBED_PREFIX", "0") != "0"
EMBED_DOC_PREFIX = os.environ.get("ANAMNESIS_EMBED_DOC_PREFIX", "search_document: ")
EMBED_QUERY_PREFIX = os.environ.get("ANAMNESIS_EMBED_QUERY_PREFIX", "search_query: ")
OLLAMA_TIMEOUT = int(os.environ.get("ANAMNESIS_TIMEOUT", "120"))
EMBED_TIMEOUT = int(os.environ.get("ANAMNESIS_EMBED_TIMEOUT", "20"))
OLLAMA_RETRIES = int(os.environ.get("ANAMNESIS_RETRIES", "2"))
OLLAMA_RETRY_BACKOFF = float(os.environ.get("ANAMNESIS_RETRY_BACKOFF", "1.5"))

# Cloud API keys are loaded from .env by _cfg.load_dotenv() above (kept out of
# git/code). See .env.example for the supported keys.

# Cloud LLM backend (primary, fast, off-GPU) with local Ollama fallback. Pick
# via ANAMNESIS_CLOUD=cerebras|groq|gemini|deepseek|none. Cerebras/Groq/
# DeepSeek are OpenAI-compatible; Gemini uses its own REST shape. All fall back
# to Ollama on error.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "").strip()
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()

GEMINI_MODEL = os.environ.get("ANAMNESIS_GEMINI_MODEL", "gemini-2.5-flash")
GROQ_MODEL = os.environ.get("ANAMNESIS_GROQ_MODEL", "llama-3.3-70b-versatile")
CEREBRAS_MODEL = os.environ.get("ANAMNESIS_CEREBRAS_MODEL", "gpt-oss-120b")
# V4 Flash: cheap/fast non-Pro tier, ample for short-transcript JSON extraction.
DEEPSEEK_MODEL = os.environ.get("ANAMNESIS_DEEPSEEK_MODEL", "deepseek-v4-flash")

GEMINI_URL = _http_url(os.environ.get("GEMINI_URL"),
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent")
GROQ_URL = _http_url(os.environ.get("GROQ_URL"), "https://api.groq.com/openai/v1/chat/completions")
CEREBRAS_URL = _http_url(os.environ.get("CEREBRAS_URL"),
                         "https://api.cerebras.ai/v1/chat/completions")
DEEPSEEK_URL = _http_url(os.environ.get("DEEPSEEK_URL"),
                         "https://api.deepseek.com/v1/chat/completions")

GEMINI_TIMEOUT = int(os.environ.get("ANAMNESIS_GEMINI_TIMEOUT", "60"))
GEMINI_RETRIES = int(os.environ.get("ANAMNESIS_GEMINI_RETRIES", "2"))
GEMINI_RETRY_BACKOFF = float(os.environ.get("ANAMNESIS_GEMINI_BACKOFF", "2.0"))

# Cerebras sits behind Cloudflare and 403s the default Python-urllib UA.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _resolve_cloud() -> str:
    choice = os.environ.get("ANAMNESIS_CLOUD", "auto").strip().lower()
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
_CLOUD_KEYS = {"cerebras": CEREBRAS_API_KEY, "groq": GROQ_API_KEY,
               "gemini": GEMINI_API_KEY, "deepseek": DEEPSEEK_API_KEY}
_CLOUD_MODELS = {"cerebras": CEREBRAS_MODEL, "groq": GROQ_MODEL,
                 "gemini": GEMINI_MODEL, "deepseek": DEEPSEEK_MODEL}


def cloud_key() -> str:
    return _CLOUD_KEYS.get(ACTIVE_CLOUD, "")

# Projects that must NEVER use a cloud backend (sensitive/novel research). Their
# transcripts are extracted ONLY by local Ollama, nothing leaves the machine.
LOCAL_ONLY_PROJECTS = {p.strip().lower() for p in
                       os.environ.get("ANAMNESIS_LOCAL_ONLY", "").split(",")
                       if p.strip()}
# Fail-safe allowlist: if set, ONLY these projects may use the cloud; every
# other project — INCLUDING any new/unknown one — stays local. Takes precedence
# over LOCAL_ONLY_PROJECTS so a forgotten project can never leak by default.
CLOUD_ONLY_PROJECTS = {p.strip().lower() for p in
                       os.environ.get("ANAMNESIS_CLOUD_ONLY", "").split(",")
                       if p.strip()}


def is_local_only(project) -> bool:
    """Decide if a project must stay local. Allowlist mode (CLOUD_ONLY set):
    only listed projects use cloud, everything else (incl. unknown/empty) is
    local — fail-safe. Else denylist mode: listed projects are local."""
    p = (project or "").strip().lower()
    if CLOUD_ONLY_PROJECTS:
        return p not in CLOUD_ONLY_PROJECTS
    return p in LOCAL_ONLY_PROJECTS


def local_routing_desc() -> str:
    if CLOUD_ONLY_PROJECTS:
        return "allowlist — cloud ONLY for: " + ", ".join(sorted(CLOUD_ONLY_PROJECTS))
    if LOCAL_ONLY_PROJECTS:
        return "local-only: " + ", ".join(sorted(LOCAL_ONLY_PROJECTS))
    return "all tracked projects use cloud"

MAX_TRANSCRIPT_CHARS = int(os.environ.get("ANAMNESIS_MAX_TRANSCRIPT", "12000"))
# Head share of the budget when a long transcript is split head+tail (audit M3:
# a fixed 2000-char head lost the project setup on long sessions). 0-override via
# ANAMNESIS_TRUNCATE_HEAD_CHARS; else derived from the fraction.
TRUNCATE_HEAD_FRAC = float(os.environ.get("ANAMNESIS_TRUNCATE_HEAD_FRAC", "0.4"))
TRUNCATE_HEAD_CHARS = int(os.environ.get("ANAMNESIS_TRUNCATE_HEAD_CHARS", "0"))
MAX_MESSAGE_CHARS = 2000  # per-event cap before global truncation

# Sweep: this window is used ONLY for performance ordering, NEVER to skip a
# never-processed transcript. Disk retention is ~30d, so the old 7d hard
# cutoff silently lost memory (audit F28) — sweep_unprocessed now processes
# any tracked-but-unprocessed transcript regardless of age.
SWEEP_ORDER_DAYS = int(os.environ.get("ANAMNESIS_SWEEP_DAYS", "30"))
SESSION_START_SWEEP_CAP = int(os.environ.get("ANAMNESIS_SWEEP_CAP", "8"))
SESSION_END_SWEEP_CAP = int(os.environ.get("ANAMNESIS_SWEEP_CAP_END", "25"))
ARCHIVE_AFTER_DAYS = int(os.environ.get("ANAMNESIS_ARCHIVE_DAYS", "30"))
TYPED_ARCHIVE_AFTER_DAYS = int(os.environ.get("ANAMNESIS_TYPED_ARCHIVE_DAYS", "90"))
PRUNE_DB_AFTER_DAYS = int(os.environ.get("ANAMNESIS_PRUNE_DAYS", "90"))

# Context compaction (audit F4/F23/F37): cap unbounded append-only growth.
CONTEXT_MAX_BYTES = int(os.environ.get("ANAMNESIS_CONTEXT_MAX_BYTES", "12000"))
CONTEXT_KEEP_RECENT = int(os.environ.get("ANAMNESIS_CONTEXT_KEEP_RECENT", "12"))
# Floor for how many recent entries to keep verbatim when the byte cap forces
# aggressive compaction (audit M2: the cap is now hard, not "12 entries of any size").
CONTEXT_KEEP_MIN = int(os.environ.get("ANAMNESIS_CONTEXT_KEEP_MIN", "3"))
CONTEXT_LINK_ARCHIVE_MAX = int(os.environ.get("ANAMNESIS_CONTEXT_LINKS_MAX", "60"))

# SessionStart retrieval injection (audit F35/F36)
RETRIEVAL_TOP_K = int(os.environ.get("ANAMNESIS_RETRIEVAL_K", "5"))
INJECT_CONTEXT = os.environ.get("ANAMNESIS_INJECT", "1") != "0"
# Budget-aware injection (M-15): cap the SessionStart payload so it never bloats
# the context window. Sections are added by priority (card → mistakes → patterns →
# cross-project) until the budget is hit. ~2200 chars ≈ 550 tokens.
INJECT_BUDGET_CHARS = int(os.environ.get("ANAMNESIS_INJECT_BUDGET_CHARS", "2200"))
# Minimum cosine for a semantic hit to count, and how much a recurring lesson is
# boosted in ranking (audit H4/LOW: recurrence was computed but never used).
# 0.40 (was 0.30, which sat below the bge-m3 background and never fired): measured on the live
# vault the real note↔note top-1 minimum is 0.418 (p1=0.46), while gibberish tops out ~0.43 — so
# 0.40 is the highest floor with ZERO false-negatives on real notes, making the floor a non-inert
# defense-in-depth layer that catches the lowest-scoring noise. It is NOT raised to ~0.45 (the
# audit's suggestion) because real/noise OVERLAP at the boundary (the W2 compression ceiling), so a
# higher floor would abstain on genuinely-weak-but-real queries; the corpus-adaptive margin gate
# below stays the PRIMARY abstention mechanism (it caught 6/6 gibberish where the floor can't).
RETRIEVAL_SIM_FLOOR = float(os.environ.get("ANAMNESIS_SIM_FLOOR", "0.40"))
# The nearest-neighbour inclusion floor for INTERACTIVE search (CLI / MCP / api.recall) and the
# SQLite diagnostic path — deliberately permissive and DISTINCT from the confident-injection floor
# above. A user-initiated query returns the closest notes even when weak (the caller labels them
# low-confidence); auto-injection on the hook path still uses RETRIEVAL_SIM_FLOOR. Named here so the
# two floors stay in one place instead of a magic 0.15 copied across modules (audit 2026-06-18).
RETRIEVAL_NEAR_FLOOR = float(os.environ.get("ANAMNESIS_NEAR_FLOOR", "0.15"))
# Confidence gate (dogfood W1/W3): bge-m3 cosines bunch near a high background (~0.42 on a
# real vault), so the absolute floor alone never fires — a nonsense query scores like a real
# one. A confident match must ALSO stand this far above the per-query MEDIAN similarity; below
# it, the semantic signal is dropped so the hook injects lexical/nothing, not arbitrary notes.
RETRIEVAL_CONFIDENT_MARGIN = float(os.environ.get("ANAMNESIS_CONFIDENT_MARGIN", "0.15"))
# Two recurrence boosts on two scales (research/longitudinal_bench.py calibrates both):
#   RECUR_BOOST (~0.03) is added to raw COSINE in single-signal paths (cf. _recur_boost);
#   RECUR_RRF_BOOST (~0.0003) is added to fused RRF scores in retrieve_relevant, whose
#   adjacent-rank gap is ~1/60 — so it is a deliberate gentle TIEBREAKER there (the 3A
#   benchmark finds this small value Pareto-optimal; larger values hurt crisp queries).
RETRIEVAL_RECUR_BOOST = float(os.environ.get("ANAMNESIS_RECUR_BOOST", "0.03"))
RETRIEVAL_RECUR_RRF_BOOST = float(os.environ.get("ANAMNESIS_RECUR_RRF_BOOST", "0.0003"))
# Calibrated fusion produces logistic scores in (0,1) (vs RRF's ~1/60 gaps), so the
# recurrence tiebreak needs a proportionally larger constant to stay a gentle tiebreak.
# Inert on a no-recurrence corpus (log(1)=0), so it never moves the benchmark.
RETRIEVAL_RECUR_FUSION_BOOST = float(os.environ.get("ANAMNESIS_RECUR_FUSION_BOOST", "0.02"))
# Ambiguity-adaptive recurrence (research/ABLATION_RESULTS.md): the recurrence prior
# is scaled by how ambiguous the relevance signal is (bunched top sims → up; a clear
# leader → down), so recurrence helps exactly when relevance can't decide and never
# displaces a crisp match. Inert when recurrence=1 (confirmed no-harm on LongMemEval).
ADAPTIVE_RECUR = os.environ.get("ANAMNESIS_ADAPTIVE_RECUR", "1") != "0"
AMBIGUITY_K = float(os.environ.get("ANAMNESIS_AMBIGUITY_K", "15"))
# Time-decay + salience (M-3): gently favour recent lessons without burying old gold.
# A note keeps at least DECAY_FLOOR of its score; half-life in days (0 disables).
# Resolved mistakes are down-weighted (no longer active warnings).
RETRIEVAL_DECAY_HALFLIFE = float(os.environ.get("ANAMNESIS_DECAY_HALFLIFE", "365"))
RETRIEVAL_DECAY_FLOOR = float(os.environ.get("ANAMNESIS_DECAY_FLOOR", "0.5"))
RETRIEVAL_RESOLVED_WEIGHT = float(os.environ.get("ANAMNESIS_RESOLVED_WEIGHT", "0.6"))
# Confidence-aware ranking (H2): the per-note confidence (M-10) was stamped into
# frontmatter and asked of the LLM but never READ — a write-only dead field. Now
# a low-confidence lesson is gently down-weighted in recall, floored so it's never
# buried; a note without confidence is treated as fully confident (neutral).
RETRIEVAL_CONF_FLOOR = float(os.environ.get("ANAMNESIS_CONF_FLOOR", "0.6"))
# Salience nudge (Brain F5): a note central to the knowledge graph (its entities referenced by
# the rest of the store) gets a gentle recall boost — recurrence generalised to centrality. The
# score is stamped sleep-time by consolidation; UNSTAMPED notes read 0 → ×1.0, so this is INERT
# on an entity-less/benchmark corpus and never moves the calibrated ranking there. Max +SALIENCE_BOOST.
RETRIEVAL_SALIENCE_BOOST = float(os.environ.get("ANAMNESIS_SALIENCE_BOOST", "0.1"))
# Graph multi-hop expansion (M-6): after ranking, pull in notes linked from the
# top hits (RESOLVES/SUPERSEDES/[[wikilinks]]) so "A→B→C" chains are reachable.
# 0 = off (keeps injection lean); set ANAMNESIS_GRAPH_HOPS=1 to enable by default.
GRAPH_HOPS = int(os.environ.get("ANAMNESIS_GRAPH_HOPS", "0"))
# Relation-aware injection (Phase 2b on the hot path): after ranking, append up to N
# graph-connected lessons reached by the top hits' typed edges (a bug surfaces its fix).
# 0 = off (default — keeps SessionStart injection precise + token-lean); applied ONLY at
# SessionStart (a frontmatter scan, once per session), never on the per-prompt path.
RELATION_EXPAND = int(os.environ.get("ANAMNESIS_RELATION_EXPAND", "0"))
# Fact-vs-code staleness check (M-4): annotate injected notes whose referenced
# file paths no longer exist. Off by default (a heuristic — opt in per project).
STALE_CHECK = os.environ.get("ANAMNESIS_STALE_CHECK", "0") != "0"
# Weight of the semantic ranking in the hybrid RRF fusion. With a strong
# multilingual embedder (bge-m3) semantic alone beats equal-weight hybrid, so we
# let it lead while lexical still backs it up (ablation 2026-06-13).
RETRIEVAL_SEM_WEIGHT = float(os.environ.get("ANAMNESIS_SEM_WEIGHT", "2.0"))
# Fusion of the semantic + lexical signals. "calibrated" (default) z-normalises each
# signal's SCORES over the candidate set and combines the magnitudes — measured to beat
# rank-fusion decisively (RRF throws the magnitudes away, so it trails even plain BM25;
# calibrated fusion lifts LongMemEval R@5 0.66→0.80 and overtakes Mem0). "rrf" keeps the
# legacy reciprocal-rank fusion as a fallback. See research/RETRIEVAL_FUSION.md.
RETRIEVAL_FUSION = os.environ.get("ANAMNESIS_FUSION", "calibrated").strip().lower()
# Dense (semantic) weight in calibrated fusion; the lexical (BM25) weight is fixed at 1.0.
# Robust across 0.4–1.0 (every setting beat Mem0 in the sweep); 0.5 is the near-optimum.
FUSION_SEM_WEIGHT = float(os.environ.get("ANAMNESIS_FUSION_SEM_WEIGHT", "0.5"))
# Ranker selector (research/posterior_model.py, 1A). "hybrid" (default) = the shipped
# additive-recurrence + multiplicative-salience tail. "posterior" = the same signals as
# an explicit log-linear posterior: w_rel·log(rrf) + w_freq·log(n) + w_sal·log(salience),
# each prior a separable, reweightable term (the static form 1B then learns online). The
# research module showed the FITTED posterior beats the hand-tuned heuristic in-distribution;
# defaults here keep relevance dominant and recurrence a frequency prior.
RANKER = os.environ.get("ANAMNESIS_RANKER", "hybrid").strip().lower()
POST_W = {k: float(os.environ.get(f"ANAMNESIS_POST_W_{k.upper()}", d))
          for k, d in (("rel", "1.0"), ("freq", "0.3"), ("sal", "1.0"))}
# Divergent/serendipitous recall (research/divergent.py, 2B): >0 re-ranks the top
# candidates by Maximal Marginal Relevance, trading a little relevance for diversity
# (fewer near-duplicates, more cross-topic surfacing). 0 (default) = convergent, no change.
RETRIEVAL_DIVERGENCE = max(0.0, min(1.0, float(os.environ.get("ANAMNESIS_DIVERGENCE", "0"))))
# Short embed timeout for interactive retrieval: fail fast to lexical when the
# GPU is busy instead of stalling SessionStart up to EMBED_TIMEOUT (audit H5).
RETRIEVAL_EMBED_TIMEOUT = int(os.environ.get("ANAMNESIS_RETRIEVAL_EMBED_TIMEOUT", "5"))
# Above this many candidates, retrieval FTS-prefilters to the top-N lexical matches
# before cosine, so one huge project can't stall a prompt with a full brute-force
# scan (improvement P1). Smaller projects keep an exact full scan — no recall loss.
RETRIEVAL_PREFILTER_LIMIT = int(os.environ.get("ANAMNESIS_PREFILTER_LIMIT", "600"))
# Cross-project transfer (I-7): surface a few lessons from OTHER projects that
# are highly relevant (shared stack → transferable gotchas). Higher bar to keep
# noise out. Toggle off with ANAMNESIS_CROSS_PROJECT=0.
INJECT_CROSS_PROJECT = os.environ.get("ANAMNESIS_CROSS_PROJECT", "1") != "0"
CROSS_PROJECT_K = int(os.environ.get("ANAMNESIS_CROSS_K", "2"))
CROSS_PROJECT_SIM_FLOOR = float(os.environ.get("ANAMNESIS_CROSS_SIM_FLOOR", "0.5"))
# Learned user model (I-6): inject a short cross-project working profile (built
# by build_user_model.py → User/profile.md). Off with ANAMNESIS_USER_MODEL=0.
INJECT_USER_MODEL = os.environ.get("ANAMNESIS_USER_MODEL", "1") != "0"
# Cloud-as-judge rerank (I-3): reorder retrieval candidates with a free cloud
# model. Opt-in (adds cloud latency, marginal over bge-m3) and never on the hot
# injection paths — only deliberate on-demand search. On with ANAMNESIS_RERANK=1.
RERANK_ENABLED = os.environ.get("ANAMNESIS_RERANK", "0") != "0"
RERANK_POOL = int(os.environ.get("ANAMNESIS_RERANK_POOL", "15"))
# Structured project card (audit I-15): distil the project's live notes into a
# high-signal block (status · stack · open gotchas · key decisions · recurring)
# kept at the top of Context/<project>.md and injected instead of the raw journal
# tail — cheaper to inject, higher signal. Off with ANAMNESIS_PROJECT_CARD=0.
PROJECT_CARD_ENABLED = os.environ.get("ANAMNESIS_PROJECT_CARD", "1") != "0"
CARD_MAX_ITEMS = int(os.environ.get("ANAMNESIS_CARD_MAX_ITEMS", "5"))
CARD_START = "<!-- PROJECT-CARD:START -->"
CARD_END = "<!-- PROJECT-CARD:END -->"
CARD_HEADER = "## 🗂 Карточка проекта"
# Task-aware recall (I-4): on UserPromptSubmit, retrieve by the actual PROMPT
# text (not just project state) and inject targeted lessons. The single biggest
# recall-quality win — the start-of-session injection can't know the task yet.
# Off with ANAMNESIS_PROMPT_RECALL=0.
PROMPT_RECALL_ENABLED = os.environ.get("ANAMNESIS_PROMPT_RECALL", "1") != "0"
# Policy: 'smart' (substantial prompts, per-session dedup, capped) | 'once'
# (first substantial prompt only) | 'every' (every non-trivial prompt).
PROMPT_RECALL_MODE = os.environ.get("ANAMNESIS_PROMPT_RECALL_MODE", "smart").strip().lower()
PROMPT_RECALL_K = int(os.environ.get("ANAMNESIS_PROMPT_RECALL_K", "3"))
# Soft ceiling on injections per session, so a long session can't keep paying the
# recall cost indefinitely (dedup already self-throttles).
PROMPT_RECALL_MAX_PER_SESSION = int(os.environ.get("ANAMNESIS_PROMPT_RECALL_MAX", "6"))
# Prompts shorter than this (after trimming) are treated as trivial → skipped.
PROMPT_RECALL_MIN_CHARS = int(os.environ.get("ANAMNESIS_PROMPT_RECALL_MIN_CHARS", "16"))
# Tight budget so recall never noticeably delays an interactive prompt: a busy
# GPU fails the ping fast and the path drops to lexical-only.
PROMPT_RECALL_EMBED_TIMEOUT = int(os.environ.get("ANAMNESIS_PROMPT_RECALL_EMBED_TIMEOUT", "2"))
PROMPT_RECALL_ALIVE_TIMEOUT = int(os.environ.get("ANAMNESIS_PROMPT_RECALL_ALIVE_TIMEOUT", "1"))
PROMPT_RECALL_STATE_DIR = VAULT / ".prompt_recall"

STATUS_HISTORY_LIMIT = 50
LOG_MAX_BYTES = 1_000_000
LOCK_STALE_S = 600
LOCK_RETRY_S = 0.5

# Windows reserved device names — must never become a file stem (audit C3)
WIN_RESERVED = {"con", "prn", "aux", "nul",
                *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10))}

TYPED_TYPES = ("pattern", "mistake", "decision")
TYPE_FOLDER = {"pattern": "Patterns", "mistake": "Mistakes", "decision": "Decisions"}
TYPE_ICON = {"pattern": "✅", "mistake": "⚠️", "decision": "🎯"}

EXTRACTION_PROMPT = """Проанализируй эту Claude Code сессию и извлеки знания.

Известные параметры:
  project (используй ровно это значение): {project_hint}
  предпочитаемые теги (бери из списка где можно, lowercase, новый — только если ничего не подходит):
    {tag_vocab}
  уже существующие в проекте заметки (НЕ дублируй — пропусти если суть совпадает):
    patterns: {existing_patterns}
    mistakes: {existing_mistakes}
    decisions: {existing_decisions}

СЕССИЯ:
{transcript}

Верни ТОЛЬКО валидный JSON. Никакого markdown, никаких пояснений.

Схема:
{{
  "project": "{project_hint}",
  "project_relevant": true,
  "patterns": [
    {{"title": "короткий заголовок (3-7 слов, kebab-case или фраза)",
      "description": "что именно сработало и почему (1-3 предложения)",
      "supersedes": "", "contradicts": "", "resolves": "",
      "entities": ["ключевая-сущность", "ещё-одна"],
      "relations": [{{"rel": "fixes", "target": "сущность"}}], "confidence": 0.9}}
  ],
  "mistakes": [
    {{"title": "короткий заголовок ошибки",
      "description": "что было неправильно",
      "prevention": "одна строка: конкретное действие/проверка чтобы не повторить",
      "supersedes": "", "contradicts": "",
      "entities": ["ключевая-сущность", "ещё-одна"],
      "relations": [{{"rel": "caused-by", "target": "сущность"}}], "confidence": 0.9}}
  ],
  "decisions": [
    {{"title": "короткий заголовок решения",
      "description": "что решено и обоснование",
      "supersedes": "", "contradicts": "", "resolves": "",
      "entities": ["ключевая-сущность", "ещё-одна"],
      "relations": [{{"rel": "alternative-to", "target": "сущность"}}], "confidence": 0.9}}
  ],
  "context_update": "обновление контекста проекта (1-3 предложения о текущем состоянии)",
  "session_summary": "что сделано за сессию (2-4 предложения)",
  "tags": ["тег1", "тег2", "тег3"]
}}

Все теги в lowercase. Пустые категории = []. Если сессия тривиальная (чтение/обсуждение) — всё пусто, только session_summary.

ПОЛЕ project_relevant — КРИТИЧНО для чистоты памяти:
  - true  — сессия реально про проект {project_hint} (его код/исследование/задачи).
  - false — offtopic: посторонний вопрос, личный траблшутинг (игры, ОС, железо не по теме),
            другой проект, смена модели, пустой диалог. ТОГДА верни patterns/mistakes/
            decisions = [] и context_update = "", заполни ТОЛЬКО session_summary.
  Не загрязняй знания проекта посторонним — лучше пусто, чем мимо темы.

ПОЛЕ supersedes (для каждого пункта) — для актуальности памяти:
  Если пункт ЗАМЕНЯЕТ или ОПРОВЕРГАЕТ уже существующую заметку из списков выше
  (статус изменился, решение пересмотрено, ошибка устранена) — впиши ТОЧНЫЙ
  заголовок той заметки. Иначе оставь "". Так старое не будет противоречить новому.

ПОЛЕ resolves (только у pattern/decision) — связать решение с устранённой ошибкой:
  Если этот паттерн/решение УСТРАНЯЕТ конкретную ошибку из списка mistakes выше —
  впиши ТОЧНЫЙ заголовок той ошибки. Иначе "". Решённая ошибка перестаёт быть
  активным предупреждением (помечается «решено»), но остаётся в истории.

ПОЛЕ contradicts — детект противоречий (M-2):
  Если пункт ПРЯМО ПРОТИВОРЕЧИТ существующей заметке выше (несовместимое
  утверждение/выбор, не просто обновление) — впиши ТОЧНЫЙ заголовок той заметки.
  Противоречащая старая заметка будет ретайрнута, останется текущая истина.

ПОЛЕ confidence (0.0–1.0) — насколько это устойчивое знание, а не разовая деталь.
  Высокое (0.8–1.0) для проверенных фактов; низкое (<0.5) для догадок.

ПОЛЯ entities/relations (опционально) — граф знаний:
  entities — 2-5 ключевых сущностей урока (инструменты/концепты/файлы), lowercase kebab-case,
  без версий. relations — рёбра {{"rel": тип, "target": сущность}}, target в том же стиле; rel
  из набора: causes, caused-by, fixes, fixed-by, depends-on, requires, part-of, alternative-to,
  related-to. Напр. для CUDA-OOM: entities ["cuda","batch-size"], relations
  [{{"rel":"fixed-by","target":"gradient-checkpointing"}}]. Неясно — [].{brain_block}"""


def log(msg):
    line = f"[memory_hook {datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, file=sys.stderr)
    try:  # also persist to a rotating file so hook failures are debuggable
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
            LOG_FILE.replace(LOG_FILE.with_name("memory_hook.log.1"))
        with open(LOG_FILE, "a", encoding="utf-8") as f:
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


# ── Shared low-level helpers ──────────────────────────────────────────

def write_atomic(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Crash-safe write: temp file in the same dir + os.replace (atomic on
    NTFS). A crash mid-write can no longer truncate the live file (audit
    F1/F3/F30 — the corruption that triggered mass re-processing)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # pid in the temp name avoids collisions between concurrent hook processes;
    # cleanup on failure leaves no orphaned .tmp in the synced vault (audit D3)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding=encoding)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def cosine(a, b) -> float:
    # robust to corrupt cache entries: non-list, mismatched length, or
    # non-numeric elements all score 0.0 instead of crashing (fuzz PROBE 5)
    if not isinstance(a, list) or not isinstance(b, list) or len(a) != len(b):
        return 0.0
    try:
        s = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
    except (TypeError, ValueError):
        return 0.0
    # a NaN/inf in a (malformed) cloud embedding would make every comparison False and
    # so slip past the confidence gate as a phantom top hit — treat a non-finite vector
    # as no signal (0.0) instead (launch-round audit 2026-06-20).
    if not (math.isfinite(na) and math.isfinite(nb) and math.isfinite(s)):
        return 0.0
    return s / (na * nb) if na and nb else 0.0


def _strip_json_fence(raw: str) -> str:
    """Strip an outer ```json / ``` code fence from a model response — WITHOUT
    re.MULTILINE (audit M-j). The round-1 `re.sub(r"^```...|```$", flags=re.M)`
    matched a fence on ANY line, so a JSON whose string value contained a ```
    code block was truncated mid-document. Here only the single outermost fence
    wrapping the whole payload is removed; fences inside string values survive."""
    s = (raw or "").strip()
    if s.startswith("```"):                 # drop the opening fence line (```json / ```)
        nl = s.find("\n")
        s = s[nl + 1:] if nl != -1 else s[3:]
    if s.endswith("```"):                   # drop the closing fence
        s = s[:-3]
    return s.strip()


def _truncate_utf8_bytes(text: str, max_bytes: int) -> str:
    """Truncate `text` to at most `max_bytes` UTF-8 bytes on a CHARACTER boundary
    (audit M-g). Slicing encoded bytes then decoding with errors='ignore' drops a
    partial trailing code point — corrupting the tail of multibyte (e.g. Cyrillic)
    text. This walks back to the last whole character that fits."""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    # binary-search the longest character prefix whose UTF-8 length fits
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(text[:mid].encode("utf-8")) <= max_bytes:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


# Cyrillic → Latin so auto-generated note filenames stay ASCII and portable
# across case-sensitive / non-UTF-8 filesystems and git remotes (audit F40).
_CYR = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
}


def translit(s: str) -> str:
    out = []
    for ch in s or "":
        low = ch.lower()
        if low in _CYR:
            t = _CYR[low]
            out.append(t.upper() if (ch.isupper() and t) else t)
        else:
            out.append(ch)
    return "".join(out)


# ── Slug helpers ──────────────────────────────────────────────────────

def slugify(s: str, max_len: int = 55) -> str:
    s = translit(s or "")
    s = re.sub(r'[^\w\s-]', ' ', s)   # drop punctuation (parens/dots/…), not just reserved (audit A20)
    s = re.sub(r'\s+', '-', s.strip())
    s = re.sub(r'-+', '-', s).strip('-')
    s = s.encode('ascii', 'ignore').decode('ascii')  # guarantee portable stem
    s = re.sub(r'-+', '-', s).strip('-')
    return s[:max_len].lower() or "untitled"


def slug_tag(t: str) -> str:
    t = (t or "").strip().lower().replace(' ', '_')
    return re.sub(r'[^\w\-/]', '', t)


def slug_project(name: str) -> str:
    """Project slug: alnum + underscore, lowercase, never '-'. Hardened
    against '', '.', '..' and Windows reserved device names so an LLM- or
    injection-controlled project value cannot escape Context/ (audit C3)."""
    s = slugify(name, 40)
    if s == "untitled":  # slugify's empty-input fallback → no real project name
        return "general"
    s = s.replace('-', '_').strip('._')
    if s in WIN_RESERVED:
        s = "project_" + s
    if not s or s in {'.', '..'}:
        s = "general"
    return s


def _strip_lead_icon(t: str) -> str:
    """Drop a leading type-icon the LLM sometimes echoes into a title, so the
    note heading doesn't render the icon twice (audit C5)."""
    t = re.sub(r'^[\s✅⚠️\U0001f3af•·\-–—]+', '',
               t or '')
    return t.strip() or "untitled"


# ── Stem format (single source of truth) ──────────────────────────────
# Typed   : YYYY-MM-DD-{project}-{ntype}-{slug}        (project has no '-')
# Session : YYYY-MM-DD-HHMM-{project}-session-{id8}

def typed_stem(date: str, project: str, ntype: str, title: str) -> str:
    return f"{date}-{project}-{ntype}-{slugify(title)}"


def session_stem(date: str, time_str: str, project: str, session_id: str) -> str:
    return f"{date}-{time_str.replace(':','')}-{project}-session-{session_id[:8]}"


def parse_typed_stem(stem: str) -> dict | None:
    """Parse typed stem. Returns {date, project, ntype, slug} or None."""
    parts = stem.split("-", 5)
    if len(parts) < 6 or parts[4] not in TYPED_TYPES:
        return None
    return {"date": "-".join(parts[:3]), "project": parts[3],
            "ntype": parts[4], "slug": parts[5]}


def parse_session_stem(stem: str) -> dict | None:
    """Parse session stem. Returns {date, time, project, id8} or None."""
    parts = stem.split("-", 6)
    if len(parts) < 7 or parts[5] != "session":
        return None
    return {"date": "-".join(parts[:3]), "time": parts[3],
            "project": parts[4], "id8": parts[6]}


# ── Tag / frontmatter helpers ─────────────────────────────────────────

def render_body_tags(*tag_groups) -> str:
    seen, out = set(), []
    for group in tag_groups:
        for t in (group or []):
            tag = slug_tag(t)
            if tag and tag not in seen:
                seen.add(tag)
                out.append(f"#{tag}")
    return " ".join(out)


def _norm_tags(tags) -> list:
    """Canonical, deduped tag list (audit M5): one vocabulary for frontmatter and
    body. Spaces AND hyphens collapse to '_' so 'quantum computing' and
    'quantum-computing' both become 'quantum_computing' (slashes kept for
    hierarchical tags like 'project/foo')."""
    out, seen = [], set()
    for t in (tags or []):
        if not isinstance(t, str):
            continue
        s = slug_tag(t).replace("-", "_").strip("_")
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


_ENTITY_BAD_RE = re.compile(r"[^\w\s-]", re.UNICODE)


def _norm_entities(raw, cap: int = 8) -> list:
    """Canonical entity tags for the knowledge graph (Phase 1): lowercase kebab-case,
    deduped, length-bounded, capped. Strips everything but word chars / spaces / hyphens,
    so junk or an injection payload smuggled through the `entities` field can only ever
    survive as a harmless short token. Unicode-aware, so Cyrillic entities are kept."""
    if not isinstance(raw, (list, tuple)):
        return []
    out, seen = [], set()
    for e in raw:
        if not isinstance(e, str):
            continue
        s = _ENTITY_BAD_RE.sub(" ", e).strip().lower()
        s = re.sub(r"[\s_]+", "-", s).strip("-")
        if not (2 <= len(s) <= 40) or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= cap:
            break
    return out


def _norm_relations(raw, cap: int = 8) -> list:
    """Canonical typed edges for the knowledge graph (Phase 2): [{rel, target}] dicts
    with rel and target normalised to lowercase kebab tokens (reusing _norm_entities, so
    an injection payload can only survive as a harmless token, 'Caused By' merges with
    'caused-by', and the target is the SAME token space as `entities` — edges connect to
    the entity graph). Drops malformed / self-edges, dedups (rel,target), caps."""
    if not isinstance(raw, (list, tuple)):
        return []
    out, seen = [], set()
    for r in raw:
        if not isinstance(r, dict):
            continue
        rel = _norm_entities([r.get("rel")])
        tgt = _norm_entities([r.get("target")])
        if not rel or not tgt:
            continue
        key = (rel[0], tgt[0])
        if key in seen:
            continue
        seen.add(key)
        out.append({"rel": rel[0], "target": tgt[0]})
        if len(out) >= cap:
            break
    return out


def _norm_entity_types(raw, cap: int = 8, gate: bool = True) -> dict:
    """Canonical {entity: type} map for the Brain layer (F1). Keys normalised to the SAME
    token space as `entities`, so a typed entity matches its graph node.

    gate=True  (extraction / WRITE): values restricted to the ACTIVE profile's ontology
               (config.entity_types()); a coding-only install therefore writes none, and
               junk / an injection payload in the type slot is dropped.
    gate=False (reading a note BACK): the stored type is kept as a clean token regardless of
               which profile is active now — recall must not depend on the current profile,
               since the type was already validated at write time."""
    if not isinstance(raw, dict):
        return {}
    allowed = set(_cfg.entity_types())
    if gate and not allowed:
        return {}
    out: dict = {}
    for name, typ in raw.items():
        if not isinstance(name, str) or not isinstance(typ, str):
            continue
        key = _norm_entities([name])
        t = re.sub(r"[^a-z0-9-]", "", typ.strip().lower())
        if not key or not (2 <= len(t) <= 24) or (gate and t not in allowed) or key[0] in out:
            continue
        out[key[0]] = t
        if len(out) >= cap:
            break
    return out


def _brain_prompt_block() -> str:
    """The extra extraction instruction that asks the model to TYPE the entities it tags
    (paper/method/dataset/...). Empty string for a coding-only install, so the prompt — and
    the model's job — is byte-for-byte unchanged unless a brain profile is on."""
    if not _cfg.brain_enabled():
        return ""
    types = ", ".join(_cfg.entity_types())
    hints = _cfg.relation_hints()
    rel_line = (' Для рёбер relations предпочитай связи знания: ' + ", ".join(hints) + "."
                if hints else "")
    # Inserted as a .format() VALUE (not itself re-formatted), so braces are single here.
    return (
        "\n\nKNOWLEDGE-ГРАФ (профиль второго мозга включён): в каждой категории, для тех "
        "entities, что являются реальными ОБЪЕКТАМИ ЗНАНИЯ (не файлы/переменные/код), добавь "
        'поле "entity_types" — словарь {"сущность": "тип"}. Допустимые типы: ' + types + ". "
        'Пример: "entity_types": {"gears": "method", "imagenet": "dataset"}.' + rel_line +
        " Код-сущности НЕ типизируй — пропусти их."
    )


def _is_relevant(flag) -> bool:
    """Interpret the LLM's project_relevant flag; default True when absent so a
    backend that omits it never silently drops knowledge (audit C1)."""
    if isinstance(flag, bool):
        return flag
    if isinstance(flag, str):
        return flag.strip().lower() not in ("false", "0", "no", "нет", "")
    return True if flag is None else bool(flag)


_NOISE_UPDATE_RE = re.compile(
    r"не\s+содержит\s+полезн|только\s+метаданны|тривиальн|нет\s+полезн|"
    r"не\s+предостав|пуст(ой|ая)\s+(диалог|сесси)|только\s+(что\s+)?стартова|"
    r"no\s+useful|nothing\s+to\s+(extract|report)|trivial|session\s+just\s+started|"
    r"empty\s+(session|transcript|chat)", re.IGNORECASE)


def _is_noise_update(text: str) -> bool:
    """A 'nothing happened' context_update the LLM sometimes emits despite being
    told to leave it empty — keep it out of the living context (audit M4)."""
    return bool(_NOISE_UPDATE_RE.search(text or ""))


# Prompt-injection signatures. Tightened (audit C1): every alternative requires
# an injection-specific OBJECT (instructions/rules/system-prompt/a jailbreak
# persona), never a bare imperative verb. The round-1 guard matched ordinary
# engineering prose — "disregard the warning about the deprecated flag", "act as
# a thin wrapper", "you are now able to batch" — and silently dropped legitimate
# knowledge, which is worse than no guard. These patterns fire only on a genuine
# override attempt while leaving normal lessons untouched.
_INJECTION_RE = re.compile(
    # "ignore/disregard … (previous/all/your/the/above) instructions|prompts|rules|context"
    r"\b(?:ignore|disregard|forget|bypass|override)\s+"
    r"(?:all\s+|the\s+|your\s+|any\s+|these\s+|previous\s+|prior\s+|earlier\s+|above\s+|"
    r"the\s+above\s+)*"
    r"(?:instructions?|prompts?|rules?|directives?|guidelines?|guardrails?|context|"
    r"everything\s+(?:above|before)|all\s+of\s+the\s+above)\b|"
    # reveal/leak/print the system prompt / initial instructions
    r"\b(?:reveal|leak|expose|exfiltrate|print|show|repeat|reproduce|divulge)\s+"
    r"(?:me\s+|us\s+|the\s+|your\s+|all\s+|its\s+)*"
    r"(?:system\s+prompt|system\s+instructions?|initial\s+instructions?|"
    r"the\s+prompt\s+above|prompt\s+verbatim)\b|"
    # role-override / jailbreak personas
    r"\byou\s+are\s+now\s+(?:a\s+|an\s+|in\s+)?(?:dan\b|jailbroken|jailbreak|unrestricted|"
    r"unfiltered|uncensored|developer\s+mode|free\s+(?:from|of)\b|no\s+longer\s+bound|"
    r"allowed\s+to\s+ignore)|"
    r"\bact\s+as\s+(?:an?\s+|the\s+)?(?:dan\b|jailbroken|jailbreak|unrestricted|unfiltered|"
    r"uncensored|evil|amoral|different\s+ai|developer\s+mode)|"
    r"\b(?:enable|enter|activate)\s+(?:dan|developer|jailbreak|god)\s+mode\b|"
    r"\bnew\s+instructions?\s*:|\bsystem\s+prompt\s*:|\bjailbreak\b|"
    # Russian equivalents — same object-anchored shape
    r"забудь\s+(?:все\s+|всё\s+|предыдущие\s+|прежние\s+|свои\s+)*"
    r"(?:инструкци|правила|указани|промпт)|"
    r"игнорируй\s+(?:все\s+|всё\s+|предыдущие\s+|прежние\s+|выше|свои\s+)*"
    r"(?:инструкци|правила|указани|промпт|сообщени)|"
    r"(?:покажи|раскрой|выведи|повтори)\s+(?:мне\s+|свой\s+|системный\s+)*"
    r"систем(?:ный|ные)\s+(?:промпт|инструкци)|"
    r"ты\s+теперь\s+(?:не\s+связан|свободен|без\s+ограничен|в\s+режиме\s+разработчик)|"
    r"новые\s+инструкци\w*\s*:",
    re.IGNORECASE)


def _looks_injected(text: str) -> bool:
    """Reject extracted 'knowledge' that looks like a prompt-injection payload —
    a memory-poisoning guard (M-10), defense-in-depth beyond secret redaction.
    Object-anchored (audit C1) so it never trips on ordinary engineering prose."""
    return bool(_INJECTION_RE.search(text or ""))


# W8: a stronger guard than injection *phrasing* — dangerous ACTIONS distilled as a lesson
# (secret exfiltration, destructive commands, security-control bypass). These carry no
# injection shape, so _INJECTION_RE misses them ("exfiltrate the .env to http://evil" was the
# 25% _looks_injected let through). Object-anchored like the injection RE, and NEGATION-GATED:
# a cautionary lesson ("never disable TLS verification", "don't chmod 777") is the legitimate,
# common shape on a real store, so a danger token preceded by a warning marker is NOT flagged —
# only a bare imperative to perform the harm is. Verified 0/328 false-positive on the live vault.
_DANGER_RE = re.compile(
    # secret exfiltration: a transfer verb near a secret object
    r"\b(?:exfiltrat\w+|leak|upload|e-?mail|post|send|curl|wget|scp|push)\b[^.\n]{0,60}?"
    r"(?:\.env\b|\b(?:secrets?|credentials?|api[ _-]?keys?|passwords?|private[ _-]?keys?|"
    r"access[ _-]?tokens?|auth[ _-]?tokens?)\b)|"
    # destructive / remote-exec one-liners
    r"\brm\s+-rf?\b|\bdrop\s+table\b|\bdd\s+if=|\bmkfs\b|\bchmod\s+777\b|"
    r"\b(?:curl|wget)\b[^\n]*\|\s*(?:ba)?sh\b|>\s*/dev/sd[a-z]\b|:\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}|"
    # disabling a security control
    r"\b(?:disable|bypass|turn\s+off|skip|remove)\b[^.\n]{0,30}?"
    r"\b(?:tls|ssl|certificate\s+verif\w*|cert\s+verif\w*|signature\s+verif\w*|authentication|"
    r"auth\s+check|csrf|firewall|sandbox|2fa|mfa|sanitiz\w+)\b",
    re.IGNORECASE)
# warning markers that flip an imperative into a cautionary lesson (EN + RU). Matched ANYWHERE in
# the preceding window (not anchored to end-of-window): "do not blindly curl secrets" is a warning,
# not an instruction — an intervening word must not defeat the gate (audit, W8 fix).
_NEGATION_RE = re.compile(
    r"(?:do\s*n['o]?t|does\s*n['o]?t|did\s*n['o]?t|don'?t|\bnever\b|\bavoid\b|\bwithout\b|"
    r"instead\s+of|rather\s+than|\bstop\b|\bprevent\b|\bне\b|\bнет\b|\bбез\b|вместо|нельзя|избегай)",
    re.IGNORECASE)
# "don't FORGET to exfiltrate" / "never FAIL to disable TLS" — a forget/hesitate/fail/neglect
# between the negation and the danger token flips the polarity back to an imperative, so the
# danger STANDS. Without this guard the 36-char negation window is a trivial one-word bypass
# (audit 2026-06-18, CRIT): prepending "Don't forget to " neutralised the entire W8 gate.
_NEG_FLIP_RE = re.compile(
    r"\b(?:forget|hesitate|fail|neglect|avoid|delay|wait|hold\s+back|put\s+off|shy\s+away)\b"
    r"|забуд\w*|постесня\w*|стесня\w*|избега\w*",
    re.IGNORECASE)


def _looks_dangerous(text: str) -> bool:
    """True when `text` instructs a dangerous action (exfiltration / destruction / security
    bypass) as an imperative — NOT when it warns against one (negation-gated). W8."""
    text = text or ""
    for mt in _DANGER_RE.finditer(text):
        pre = text[max(0, mt.start() - 36):mt.start()]   # window before the danger token
        neg = _NEGATION_RE.search(pre)
        # a genuine negation governs the danger → a cautionary lesson, skip; BUT a
        # "forget/fail to …" after the negation flips it back to a command → keep flagging.
        if neg and not _NEG_FLIP_RE.search(pre[neg.end():]):
            continue
        return True
    return False


def _looks_unsafe(text: str) -> bool:
    """The write-time poisoning guard: reject extracted knowledge that is injection-shaped (W8
    phrasing) OR a bare dangerous imperative (W8 action). One call site, defense-in-depth."""
    return _looks_injected(text) or _looks_dangerous(text)


# W7 corroboration-gated quarantine — OFF by default. On a single-user store the user owns every
# session, so the threat it defends (adversarial sessions planting a lone false "lesson") does not
# apply and quarantine would only risk hiding legitimate memory. For a MULTI-TENANT / shared-store /
# untrusted-content deployment set ANAMNESIS_QUARANTINE=1: a single-source note that is ALSO
# suspicious (near-max self-declared confidence, or superseding a corroborated multi-session note)
# is diverted to <folder>/Quarantine/ — on disk, out of active recall — so one uncorroborated actor
# cannot spoof trust or displace corroborated truth. Two genuine sessions still establish a lesson.
QUARANTINE_MODE = os.environ.get("ANAMNESIS_QUARANTINE", "0") != "0"
QUARANTINE_CONF = float(os.environ.get("ANAMNESIS_QUARANTINE_CONF", "0.95"))


_YAML_NEEDS_QUOTE = re.compile(r'[:#&*!|>\'"%@`{}\[\],]|^\s|\s$')


def _yaml_scalar(v) -> str:
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)   # inline JSON is valid YAML (lists + maps, e.g. entity_types)
    s = str(v)
    if s == "" or _YAML_NEEDS_QUOTE.search(s):
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def fm_block(fm: dict) -> str:
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {_yaml_scalar(v)}")
    lines.append("---")
    return "\n".join(lines)


# ── Path filter / project derivation ──────────────────────────────────
# (_norm_path / _WIN / _CASEFOLD are defined near the config block above.)

_VAULT_NORM = _norm_path(str(VAULT))
_PROJECTS_ROOT_NORM = _norm_path(str(PROJECTS_ROOT))
try:
    _HOME_NORM = _norm_path(str(Path.home()))
except Exception:
    _HOME_NORM = ""

# Prefixes whose subtrees are never a "project": OS / installed software, the
# store itself, and the agent's transcript dir. The agent-internal ~/.claude tree
# and transient dirs are matched separately. OS-aware so machine-wide tracking
# stays clean on Windows, Linux and macOS (audit C2 / P-2).
if _WIN:
    _SYS_DIRS = (os.environ.get("SystemRoot") or r"C:\Windows",
                 os.environ.get("ProgramFiles") or r"C:\Program Files",
                 os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)",
                 os.environ.get("ProgramData") or r"C:\ProgramData")
else:
    _SYS_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt",
                 "/proc", "/sys", "/dev", "/run", "/boot",
                 "/System", "/Library", "/Applications", "/private")  # last 4: macOS
_EXCLUDE_PREFIXES = [_norm_path(p) for p in
                     (*_SYS_DIRS, _VAULT_NORM, _PROJECTS_ROOT_NORM) if p]

# Transient/agent-internal path fragments (matched anywhere in the path), sep-aware.
_SEP = os.sep
_EXCLUDE_FRAGMENTS = [f"{_SEP}.claude{_SEP}", f"{_SEP}.trash{_SEP}"]
if _WIN:
    _EXCLUDE_FRAGMENTS += [r"\appdata\local\temp\\".rstrip("\\") + "\\",
                           r"\$recycle.bin\\".rstrip("\\") + "\\"]
else:
    _EXCLUDE_FRAGMENTS += ["/tmp/", "/var/folders/", "/var/tmp/"]  # incl. macOS tmp


def _is_excluded_path(norm: str) -> bool:
    """norm = output of _norm_path (OS-normalised, no trailing sep)."""
    if not norm:
        return True
    if _HOME_NORM and norm == _HOME_NORM:        # bare home root (not its subdirs)
        return True
    tail = norm + _SEP
    if any(frag in tail for frag in _EXCLUDE_FRAGMENTS):
        return True
    for pre in _EXCLUDE_PREFIXES:
        if norm == pre or norm.startswith(pre + _SEP):
            return True
    return False


def _find_repo_root(cwd: str) -> Path | None:
    """Nearest ancestor (incl. cwd) containing a .git entry, else None. Pure
    filesystem walk — no subprocess, safe to call in the hot path and in tests."""
    raw = (cwd or "").strip()
    if not raw:
        return None
    try:
        p = Path(raw)
        cur = p if p.is_dir() else p.parent
    except OSError:
        return None
    for _ in range(40):
        try:
            if (cur / ".git").exists():
                return cur
        except OSError:
            pass
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def is_tracked_project(cwd: str) -> bool:
    """True if cwd belongs to a real project worth remembering.

    Strictly under a configured root, OR (TRACK_ANY_PROJECT) inside a git repo —
    excluding system / installed-software / agent-internal (~/.claude) / the
    vault / transient (Temp, Recycle) paths. A configured root *container* itself
    is never a project — you must be in a subdirectory of it (audit C2)."""
    norm = _norm_path(cwd)
    if not norm or _is_excluded_path(norm):
        return False
    for r in _ROOTS_NORM:
        if norm == r:
            return False
        if norm.startswith(r + _SEP):
            return True
    if TRACK_ANY_PROJECT and _find_repo_root(cwd) is not None:
        return True
    return False


def derive_project_from_cwd(cwd: str) -> str:
    """Project name for `cwd`.

    Under a configured root → first segment beneath that root. Otherwise → the
    git-repo directory name (so a repo is one project regardless of which subdir
    the agent ran in), falling back to the leaf directory name.

        D:\\Code\\MyProject\\src\\foo           → myproject
        D:\\repos\\acme (git root) \\pkg\\api   → acme
        D:\\Other\\proj  (no repo)              → proj
    """
    s = (cwd or "").strip()
    raw = os.path.normpath(s) if s else ""
    if _WIN:
        raw = raw.replace("/", "\\")
    raw = raw.rstrip("\\/")
    low = raw.lower() if _CASEFOLD else raw    # case-preserving raw + folded low
    for r in _ROOTS_NORM:
        if low.startswith(r + _SEP):
            rest = raw[len(r) + 1:]            # len matches: casing never changes length
            return slug_project(rest.split(_SEP, 1)[0])
    rr = _find_repo_root(raw)
    first_seg = rr.name if rr else Path(raw).name
    return slug_project(first_seg)


# ── Vault introspection (for prompt grounding) ────────────────────────

# Vault-introspection grounding for the extraction prompt: existing tags +
# per-project note titles. Cached per-process and FOLDED FORWARD with each
# session's writes (audit M-k) — the round-1 code cleared the whole cache after
# every session in a sweep, re-scanning all notes O(N×sessions). The vault is
# locked during processing, so the snapshot can't drift mid-run.
_TAG_COUNTS: dict[str, int] | None = None
_TITLE_SLUGS: dict[str, dict[str, list[str]]] = {}
_TAG_SKIP = {*TYPED_TYPES, "session", "context", "index"}


def collect_existing_tags(min_count: int = 2, top_k: int = 30) -> tuple[str, ...]:
    """Top tags in use, lowercase canonical (grounds the extraction prompt)."""
    global _TAG_COUNTS
    if _TAG_COUNTS is None:
        counter: dict[str, int] = {}
        for folder in ("Patterns", "Mistakes", "Decisions", "Sessions"):
            d = VAULT / folder
            if not d.exists():
                continue
            for p in d.glob("*.md"):
                try:
                    txt = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for tag in re.findall(r"#([\w/\-]+)", txt):
                    t = tag.lower()
                    if t.startswith("project/") or t in _TAG_SKIP:
                        continue
                    counter[t] = counter.get(t, 0) + 1
        _TAG_COUNTS = counter
    eligible = [t for t, n in _TAG_COUNTS.items() if n >= min_count]
    return tuple(sorted(eligible, key=lambda t: -_TAG_COUNTS[t])[:top_k])


def _clear_tag_counts():
    global _TAG_COUNTS
    _TAG_COUNTS = None


collect_existing_tags.cache_clear = _clear_tag_counts   # back-compat with callers


def collect_existing_titles(project: str) -> dict[str, tuple[str, ...]]:
    """Existing typed-note title-slugs for `project` (grounds LLM dedup)."""
    if project not in _TITLE_SLUGS:
        out: dict[str, list[str]] = {nt: [] for nt in TYPED_TYPES}
        for ntype in TYPED_TYPES:
            d = VAULT / TYPE_FOLDER[ntype]
            if not d.exists():
                continue
            for p in d.glob("*.md"):
                parsed = parse_typed_stem(p.stem)
                if parsed and parsed["project"] == project and parsed["ntype"] == ntype:
                    out[ntype].append(parsed["slug"])
        _TITLE_SLUGS[project] = out
    return {nt: tuple(slugs[-40:]) for nt, slugs in _TITLE_SLUGS[project].items()}


collect_existing_titles.cache_clear = _TITLE_SLUGS.clear   # back-compat with callers


def _unregister_slug(stem: str) -> None:
    """Drop a retired note's slug from the grounding cache so a later session in the
    same sweep isn't told a superseded title still exists (audit A16). A same-slug
    re-statement re-registers it right after, so only genuinely-gone titles vanish."""
    parsed = parse_typed_stem(stem)
    if not parsed:
        return
    bucket = _TITLE_SLUGS.get(parsed["project"], {}).get(parsed["ntype"])
    if bucket and parsed["slug"] in bucket:
        bucket.remove(parsed["slug"])


def register_written_notes(project: str, tags, links: dict) -> None:
    """Fold one session's writes into the grounding caches so the NEXT session in
    a sweep sees them without a full disk rescan (audit M-k). Only updates caches
    already built this run; an unbuilt cache will include the writes when first
    populated from disk."""
    if _TAG_COUNTS is not None:
        for t in _norm_tags(tags):
            if t.startswith("project/") or t in _TAG_SKIP:
                continue
            _TAG_COUNTS[t] = _TAG_COUNTS.get(t, 0) + 1
    if project in _TITLE_SLUGS:
        slot = _TITLE_SLUGS[project]
        for nt, stems in (links or {}).items():
            bucket = slot.setdefault(nt, [])
            for stem in stems:
                parsed = parse_typed_stem(stem)
                if parsed and parsed["slug"] not in bucket:
                    bucket.append(parsed["slug"])


# ── Advisory lock (sentinel file, race-safe via O_EXCL) ───────────────

def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check for a lock-holder PID, cross-platform. Returns True when
    uncertain so we never steal a lock from a process that might be alive.

    POSIX (macOS/Linux): `os.kill(pid, 0)` — ESRCH means dead, EPERM means alive-but-not-ours
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


def acquire_lock(timeout_s: float = 30) -> bool:
    VAULT.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode())
            finally:
                os.close(fd)
            # Confirm ownership: on filesystems where O_EXCL isn't fully atomic
            # (some NTFS/SMB shares) two processes can both "create" the file; the
            # pid that survives in it is the real owner, the other backs off
            # instead of both proceeding into the critical section (audit LOW).
            try:
                if (LOCK_FILE.read_text() or "").strip() != str(os.getpid()):
                    time.sleep(LOCK_RETRY_S)
                    continue
            except OSError:
                pass
            return True
        except FileExistsError:
            # Only reclaim a stale lock whose holder PID is actually dead —
            # avoids two processes both deleting a fresh lock (audit F12 TOCTOU).
            try:
                age = time.time() - LOCK_FILE.stat().st_mtime
                try:
                    holder = int((LOCK_FILE.read_text() or "0").strip() or 0)
                except (ValueError, OSError):
                    holder = 0
                # Steal a lock whose holder is provably dead IMMEDIATELY — don't wait
                # out LOCK_STALE_S, or a crashed holder with a fresh mtime wedges every
                # writer for 10 min (audit A19). An unknown/empty pid (holder crashed
                # between create and pid-write) still falls back to the age guard. The
                # outer age ceiling (10× stale) breaks the one remaining wedge: a crashed
                # holder whose PID was RE-USED by an unrelated live process would otherwise
                # hold the lock forever (code-review 2026-07) — no legitimate hook run
                # lasts 100 minutes.
                reclaim = ((not _pid_alive(holder)) if holder > 0 else age > LOCK_STALE_S) \
                    or age > LOCK_STALE_S * 10
                if reclaim:
                    LOCK_FILE.unlink(missing_ok=True)
                    continue
            except (FileNotFoundError, OSError):
                pass
            time.sleep(LOCK_RETRY_S)
    return False


def release_lock():
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ── Gardening: archive old session notes, prune old DB entries ────────

def archive_old_sessions(days: int = ARCHIVE_AFTER_DAYS) -> int:
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
            target = arch / p.name
            os.replace(p, target)   # atomic overwrite on POSIX and Windows (no unlink+rename race)
            moved += 1
        except OSError as e:
            log(f"Archive failed for {p.name}: {e}")
    if moved:
        log(f"Archived {moved} session note(s) older than {days}d")
    return moved


def archive_old_typed(days: int = TYPED_ARCHIVE_AFTER_DAYS) -> int:
    """Move typed notes (Patterns/Mistakes/Decisions) older than `days` into a
    per-folder Archive/ subdir. Knowledge is preserved (moved, never deleted),
    but the live folders — and the dedup-grounding glob that scans them — stop
    growing without bound (audit F24). Obsidian resolves [[stem]] regardless of
    folder, so existing wikilinks keep working after the move."""
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
                target = arch / p.name
                os.replace(p, target)   # atomic overwrite on POSIX and Windows (no unlink+rename race)
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


def prune_processed_db(db: dict, days: int = PRUNE_DB_AFTER_DAYS) -> int:
    cutoff = datetime.now() - timedelta(days=days)
    pruned = 0
    for sid in list(db.keys()):
        entry = db[sid]
        if not isinstance(entry, dict):  # corrupt/legacy value — drop it
            del db[sid]
            pruned += 1
            continue
        try:
            t = datetime.fromisoformat(entry.get("processed_at", ""))
        except (ValueError, TypeError):
            continue
        if t < cutoff:
            del db[sid]
            pruned += 1
    if pruned:
        save_processed(db)
        log(f"Pruned {pruned} old DB entries (>{days}d)")
    return pruned


# ── Processed-sessions DB ─────────────────────────────────────────────

def load_processed() -> dict:
    """Load the processed-session DB, falling back to the .bak generation if
    the primary file is missing or corrupt. Without this fallback a single
    truncated write made every session look unprocessed → reprocess storm
    with mass duplicate notes (audit F1/F30)."""
    for f in (PROCESSED_DB, PROCESSED_DB.with_name(PROCESSED_DB.name + ".bak")):
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            if f is not PROCESSED_DB:
                log("Primary processed-DB unreadable — recovered from .bak")
            return data
    return {}


def save_processed(db: dict):
    VAULT.mkdir(parents=True, exist_ok=True)
    text = json.dumps(db, ensure_ascii=False, indent=2)
    # Both copies are written from the KNOWN-GOOD in-memory db (never a copy of the
    # possibly-corrupt on-disk primary) so a good copy always survives a crash
    # mid-save (audit D1). Primary FIRST, then .bak: each write is atomic, and on a
    # crash between them load_processed() reads the already-updated primary, so the
    # latest snapshot is never silently lost to a stale primary (audit LOW).
    write_atomic(PROCESSED_DB, text)
    write_atomic(PROCESSED_DB.with_name(PROCESSED_DB.name + ".bak"), text)


def mark_processed(db: dict, session_id: str, transcript_path: str):
    db[session_id] = {
        "transcript": transcript_path,
        "processed_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_processed(db)


# ── Transcript reading ────────────────────────────────────────────────

# Defense-in-depth: scrub common secret shapes BEFORE the transcript reaches
# Ollama or any written note, so a pasted key can't leak into the (Obsidian-
# Sync'd) vault (audit C2). Not full DLP — high-confidence patterns only.
# key=value / key: value form — redact the value, keep the key (group sub)
_SECRET_KV = re.compile(
    r'(?i)(api[_-]?key|secret[_-]?access[_-]?key|access[_-]?key[_-]?id|'
    r'secret[_-]?key|private[_-]?key|client[_-]?secret|secret|password|'
    r'passwd|access[_-]?token|token)'
    r'(\s*["\']?\s*[:=]\s*["\']?)([^\s"\',]{8,})')
# connection string — redact only the password between user: and @, keep host/db
_SECRET_CONN = re.compile(
    r'(?i)((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqps?)://[^:\s/@]+:)'
    r'([^@\s/]{3,})(@)')
# full-redact, high-confidence token shapes (no entropy heuristics → no false
# positives on legitimate hashes/ids in code) — audit M1/I-14
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


def truncate_smart(text: str, max_chars: int) -> str:
    """Keep head (project setup) + tail (final decisions) — middle is least useful."""
    if len(text) <= max_chars:
        return text
    sep = "\n\n[...середина транскрипта вырезана...]\n\n"
    head_cap = TRUNCATE_HEAD_CHARS or int(max_chars * TRUNCATE_HEAD_FRAC)
    head_len = min(head_cap, max_chars - len(sep) - 100)
    if head_len <= 0:
        return text[:max_chars]
    tail_len = max_chars - head_len - len(sep)
    return text[:head_len] + sep + text[-tail_len:]


def _iter_events(path: str):
    """Yield parsed events from a JSONL transcript (resilient to partial lines)."""
    if not path or not Path(path).exists():
        return
    try:
        # errors="replace": one bad byte in a transcript must degrade (per-line
        # JSON parse skips the mojibake line), never raise UnicodeDecodeError —
        # which is a ValueError, slips past `except OSError`, and crashed the hook
        # mid-sweep, aborting every later session in the batch (audit A1).
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        log(f"Transcript read error: {e}")


def _evt_meta(evt: dict) -> tuple:
    """Pluck (cwd, timestamp) out of an event — either is may be None."""
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


def read_transcript(path: str) -> dict:
    """Single full pass — returns {body, cwd, timestamp}.

    Body lines are individually capped at MAX_MESSAGE_CHARS so one giant paste
    cannot starve the rest; the global budget is then applied by truncate_smart.
    """
    lines: list[str] = []
    cwd = ts = None
    cap = MAX_MESSAGE_CHARS
    for evt in _iter_events(path):
        ec, et = _evt_meta(evt)
        cwd = cwd or ec
        ts = ts or et
        lines.extend(_format_event(evt, cap))
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


def ollama_alive(timeout_s: float = 4) -> bool:
    """Cheap liveness ping so the hook fails loudly instead of silently
    dropping a session when Ollama is down / reloading a model (audit F29)."""
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=timeout_s) as r:
            return getattr(r, "status", 200) == 200
    except Exception:
        return False


def call_ollama(prompt: str) -> dict:
    global _OLLAMA_DOWN
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "think": False,  # qwen3.x "thinking" mode leaks structured output
        "options": {"temperature": 0.2, "num_ctx": 16384},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    for attempt in range(OLLAMA_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as r:
                data = json.loads(r.read())
            raw = (data.get("response") or "").strip()
            if not raw:
                log("Ollama returned empty response")
                return {}
            parsed = json.loads(_strip_json_fence(raw))
            return parsed if isinstance(parsed, dict) else {}
        except urllib.error.HTTPError as e:  # real HTTP response — don't retry
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            # 404 / "model not found" means the tag was never pulled — give the exact
            # fix instead of a bare HTTP code (the #1 first-run stumble, launch audit).
            if e.code == 404 or "not found" in body.lower():
                log(f"Ollama model {OLLAMA_MODEL!r} not found — run: ollama pull {OLLAMA_MODEL} "
                    f"(or set ANAMNESIS_MODEL / a cloud key)")
            else:
                log(f"Ollama HTTP {e.code} {e.reason} | {_scrub_for_log(body)}")
            return {}
        except (urllib.error.URLError, TimeoutError) as e:
            # transient connection/timeout — retry with backoff before giving up.
            # Only fires on a path that would otherwise have failed outright.
            if attempt < OLLAMA_RETRIES:
                time.sleep(OLLAMA_RETRY_BACKOFF * (attempt + 1))
                continue
            _OLLAMA_DOWN = True
            log(f"Ollama unreachable after {attempt + 1} tries "
                f"({OLLAMA_URL}): {getattr(e, 'reason', e)}")
            return {}
        except json.JSONDecodeError as e:
            log(f"JSON parse failed: {e}")
            return {}
        except Exception as e:
            log(f"Ollama error: {type(e).__name__}: {e}")
            return {}
    return {}


# ── Gemini primary backend + unified generate_json (Gemini → Ollama) ──

def call_gemini(prompt: str) -> dict:
    """Gemini generateContent in JSON mode. Returns {} on any failure so the
    caller can fall back to Ollama. Retries transient 503/429/500/timeout."""
    global _CLOUD_DEAD
    url = GEMINI_URL.format(model=_safe_model_seg(GEMINI_MODEL))   # SSRF guard on the model seg
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2,
                             "responseMimeType": "application/json"},
    }).encode("utf-8")
    # key in a header, never the URL, so it can't leak via HTTPError.url / logs
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    for attempt in range(GEMINI_RETRIES + 1):
        last = attempt >= GEMINI_RETRIES
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT) as r:
                data = json.loads(r.read())
            cands = data.get("candidates") or []
            if not cands:
                block = (data.get("promptFeedback") or {}).get("blockReason")
                if block:
                    log(f"Gemini blocked: {block}")
                    return {}  # deterministic content block — don't retry
                if not last:  # transient empty-candidates — retry (audit B1)
                    time.sleep(GEMINI_RETRY_BACKOFF * (attempt + 1))
                    continue
                log(f"Gemini: no candidates ({str(data)[:120]})")
                return {}
            fin = cands[0].get("finishReason", "STOP")
            parts = (cands[0].get("content") or {}).get("parts") or []
            txt = "".join(p.get("text", "") for p in parts
                          if isinstance(p, dict)).strip()
            if not txt:
                if not last:
                    time.sleep(GEMINI_RETRY_BACKOFF * (attempt + 1))
                    continue
                log(f"Gemini: empty text (finishReason={fin})")
                return {}
            if fin and fin != "STOP":  # MAX_TOKENS/SAFETY — log for diagnosability
                log(f"Gemini finishReason={fin} — response may be truncated")
            parsed = json.loads(_strip_json_fence(txt))
            return parsed if isinstance(parsed, dict) else {}
        except urllib.error.HTTPError as e:
            msg = ""
            try:
                msg = e.read().decode("utf-8", "replace")[:150]
            except Exception:
                pass
            if e.code in (500, 503, 429) and not last:
                time.sleep(GEMINI_RETRY_BACKOFF * (attempt + 1))
                continue
            log(f"Gemini HTTP {e.code}: {_scrub_for_log(msg)}")
            if e.code in (401, 403, 429, 500, 503):
                _CLOUD_DEAD = True  # bad key or exhausted transient — skip rest of run
            return {}
        except (urllib.error.URLError, TimeoutError) as e:
            if not last:
                time.sleep(GEMINI_RETRY_BACKOFF * (attempt + 1))
                continue
            log(f"Gemini unreachable: {getattr(e, 'reason', e)}")
            _CLOUD_DEAD = True  # network down — skip Gemini for the rest of this run
            return {}
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            log(f"Gemini parse failed: {e}")
            return {}
        except Exception as e:
            log(f"Gemini error: {type(e).__name__}: {e}")
            return {}
    return {}


def _call_openai_chat(prompt: str, base_url: str, api_key: str, model: str,
                      label: str) -> dict:
    """OpenAI-compatible chat completion in JSON mode (Cerebras, Groq). Returns
    {} on any failure so the caller falls back to Ollama. Browser UA because
    Cerebras sits behind Cloudflare. Retries transient 503/429/500/timeout."""
    global _CLOUD_DEAD
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {api_key}", "User-Agent": _UA}
    for attempt in range(GEMINI_RETRIES + 1):
        last = attempt >= GEMINI_RETRIES
        req = urllib.request.Request(base_url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT) as r:
                data = json.loads(r.read())
            choices = data.get("choices") or []
            if not choices:
                if not last:
                    time.sleep(GEMINI_RETRY_BACKOFF * (attempt + 1))
                    continue
                log(f"{label}: no choices ({str(data)[:120]})")
                return {}
            msg = choices[0].get("message") or {}
            txt = (msg.get("content") or "").strip()
            fin = choices[0].get("finish_reason")
            if not txt:
                if not last:
                    time.sleep(GEMINI_RETRY_BACKOFF * (attempt + 1))
                    continue
                log(f"{label}: empty content (finish_reason={fin})")
                return {}
            if fin and fin not in ("stop", "length"):
                log(f"{label} finish_reason={fin}")
            parsed = json.loads(_strip_json_fence(txt))
            return parsed if isinstance(parsed, dict) else {}
        except urllib.error.HTTPError as e:
            emsg = ""
            try:
                emsg = e.read().decode("utf-8", "replace")[:150]
            except Exception:
                pass
            if e.code in (500, 503, 429) and not last:
                time.sleep(GEMINI_RETRY_BACKOFF * (attempt + 1))
                continue
            log(f"{label} HTTP {e.code}: {_scrub_for_log(emsg)}")
            if e.code in (401, 403, 429, 500, 503):
                _CLOUD_DEAD = True
            return {}
        except (urllib.error.URLError, TimeoutError) as e:
            if not last:
                time.sleep(GEMINI_RETRY_BACKOFF * (attempt + 1))
                continue
            log(f"{label} unreachable: {getattr(e, 'reason', e)}")
            _CLOUD_DEAD = True
            return {}
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            log(f"{label} parse failed: {e}")
            return {}
        except Exception as e:
            log(f"{label} error: {type(e).__name__}: {e}")
            return {}
    return {}


def call_cerebras(prompt: str) -> dict:
    return _call_openai_chat(prompt, CEREBRAS_URL, CEREBRAS_API_KEY,
                             CEREBRAS_MODEL, "Cerebras")


def call_groq(prompt: str) -> dict:
    return _call_openai_chat(prompt, GROQ_URL, GROQ_API_KEY, GROQ_MODEL, "Groq")


def call_deepseek(prompt: str) -> dict:
    return _call_openai_chat(prompt, DEEPSEEK_URL, DEEPSEEK_API_KEY,
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
        log(f"Cloud ({ACTIVE_CLOUD}) failed — falling back to local Ollama")
    if not local_only and _OLLAMA_DOWN and cloud_on:
        # Ollama already failed this run and a cloud backend is primary — don't
        # burn another timeout; leave this session for a later retry.
        _LLM_STATS["fail"] += 1
        return {}
    res = call_ollama(prompt)
    if res:
        _LLM_STATS["ollama"] += 1
    else:
        _LLM_STATS["fail"] += 1
    return res


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
    """Human-readable summary of the backends auto-detected *right now* — what
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
        lines.append("  extraction : paused — no cloud key and Ollama is down; sessions are kept"
                     " and retried (start Ollama or add one key to begin)")
    if EMBED_PROVIDER != "ollama":
        if _embed_key():
            lines.append(f"  recall     : cloud embedder {EMBED_PROVIDER}:{EMBED_MODEL}  (semantic + lexical)")
        else:
            lines.append(f"  recall     : lexical only (FTS5) — {EMBED_PROVIDER} selected but"
                         f" {_EMBED_KEY_ENV.get(EMBED_PROVIDER, 'its key')} is unset")
    elif embedder_available(timeout_s):
        lines.append(f"  recall     : local Ollama {EMBED_MODEL}  (semantic + lexical, hybrid)")
    else:
        lines.append("  recall     : lexical only (FTS5) — start Ollama (bge-m3) for semantic recall")
    # Surface the opt-in precision lever when its deps are already on the machine, so the
    # cross-encoder that closes most of the W2/W4 embedding-compression gap is discoverable
    # instead of hidden behind an env var nobody knows to set.
    try:
        try:
            from . import reranker_ce as _rc
        except ImportError:
            import reranker_ce as _rc
        if _rc.available() and not _rc.ENABLED:
            lines.append("  precision  : trained cross-encoder available (torch detected); set "
                         "ANAMNESIS_XRERANK=1 for a measured +0.06 recall@1")
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
    space — self-invalidation, not silent garbage. (Lexical/FTS recall, being
    provider-independent, keeps answering meanwhile.)"""
    return _embed_sig_current(load_embed_meta().get("model"))


# Cloud embedding providers. OpenAI-compatible /v1/embeddings covers openai|voyage
# and any custom host (ANAMNESIS_EMBED_BASE_URL); gemini and cohere use their own
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


_SAFE_MODEL_RE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_model_seg(model: str) -> str:
    """Sanitise a model name before it is interpolated into a provider URL — defence
    against SSRF / path & query smuggling via ANAMNESIS_EMBED_MODEL / ANAMNESIS_GEMINI_MODEL
    (drop anything outside [A-Za-z0-9._-], so '/', '?', '#', '@' can't redirect the request
    to an internal host). audit 2026-06-18."""
    return _SAFE_MODEL_RE.sub("", model or "")


_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")
# Provider key headers/fields a hostile or misconfigured endpoint could echo back into
# its error body (Gemini uses x-goog-api-key, OpenAI-compat hosts sometimes reflect an
# api_key/api-key field). Launch-round security pass 2026-06-20.
_KEYHDR_RE = re.compile(r"(?i)(x-goog-api-key|api[-_]?key)[\"'\s:=]+[A-Za-z0-9._\-]+")


def _scrub_for_log(s: str) -> str:
    """Strip bearer tokens and provider key headers from a third-party error body
    before it is logged (and git-committed) — a hostile endpoint could echo the
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
        log(f"Embed ({EMBED_PROVIDER}) failed: {type(e).__name__}: {e}")
        return None


def _embed_cloud(text: str, kind: str | None, timeout: int | None):
    """Dispatch to the configured cloud embedding provider. Returns a vector or
    None (None → caller treats it like a busy GPU and drops to lexical)."""
    key = _embed_key()
    if not key:
        log(f"Embedding provider {EMBED_PROVIDER!r} selected but {_EMBED_KEY_ENV.get(EMBED_PROVIDER, '?')}"
            " is unset — semantic recall blocked (set the key or ANAMNESIS_EMBED_PROVIDER=ollama)")
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
        # variants return a flat {"embedding": [...]} — accept both (launch-round audit).
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


def embed_text(text: str, kind: str | None = None, timeout: int | None = None,
               project: str | None = None):
    """Return an embedding vector for `text`, or None on failure. Dispatches on
    ANAMNESIS_EMBED_PROVIDER: local Ollama (default) or a cloud provider (OpenAI-
    compatible, Voyage, Cohere, Gemini) so semantic recall can run with no local NN.
    `kind` selects the nomic task prefix (Ollama only); `timeout` lets interactive
    retrieval fail fast to lexical when the backend is slow (audit H5).

    `project` enforces the SAME privacy boundary as extraction (audit 2026-06-18 CRIT):
    when a CLOUD embedder is configured, a project in LOCAL_ONLY_PROJECTS is NEVER sent
    to it — embedding is skipped (→ text-only / lexical recall) so local-only note text
    and query prompts can't leave the machine. Ollama is local, so it needs no gate."""
    raw = (text or "")[:2000]
    if EMBED_PROVIDER != "ollama":
        if project is not None and is_local_only(project):
            log(f"Cloud embedding skipped for local-only project {project!r} "
                f"(provider {EMBED_PROVIDER}) — data stays on the machine; recall is "
                "lexical here (use ANAMNESIS_EMBED_PROVIDER=ollama for local semantic recall)")
            return None
        return _embed_cloud(raw, kind, timeout)
    payload = json.dumps({"model": EMBED_MODEL,
                          "input": _embed_prefix(kind) + raw}).encode("utf-8")
    req = urllib.request.Request(OLLAMA_EMBED_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout or EMBED_TIMEOUT) as r:
            data = json.loads(r.read())
    except Exception as e:
        log(f"Embed failed: {type(e).__name__}: {e}")
        return None
    embs = data.get("embeddings")
    if isinstance(embs, list) and embs and isinstance(embs[0], list):
        return embs[0]
    one = data.get("embedding")
    return one if isinstance(one, list) else None


def load_embed_cache() -> dict:
    """Load the embedding cache, falling back to the .bak generation and logging
    LOUDLY on corruption (audit M-f). The round-1 code swallowed a JSONDecodeError
    into {} — a half-written cache silently disabled semantic recall (dropping to
    recency) with no signal. Now a corrupt primary is recovered from .bak, and an
    unrecoverable cache is announced so it gets rebuilt instead of degrading mutely."""
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
                log("Primary embed cache corrupt — recovered from .bak")
            return data
    if EMBED_CACHE.exists():
        log("Embed cache corrupt and no valid .bak — semantic recall DISABLED "
            "until `embed_index.py --rebuild`")
    return {}


def save_embed_cache(cache: dict):
    # primary then .bak, both from the in-memory dict, so a crash mid-save always
    # leaves one valid copy and the latest write is preferred on reload (audit M-f)
    text = json.dumps(cache, ensure_ascii=False)
    try:
        write_atomic(EMBED_CACHE, text)
        write_atomic(EMBED_CACHE.with_name(EMBED_CACHE.name + ".bak"), text)
    except OSError as e:
        log(f"Embed cache save failed: {e}")


def load_embed_meta() -> dict:
    try:
        d = json.loads(EMBED_META.read_text(encoding="utf-8", errors="replace"))
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_embed_meta(meta: dict):
    try:
        write_atomic(EMBED_META, json.dumps(meta, ensure_ascii=False))
    except OSError as e:
        log(f"Embed meta save failed: {e}")


def cache_is_prefixed() -> bool:
    """Whether stored vectors used task prefixes. Explicit meta wins; otherwise
    an empty cache adopts the configured default and a populated legacy cache is
    treated as unprefixed — so we never query a cache in a mismatched mode."""
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
# reads candidates from a derived SQLite index — opened instantly, project-
# filtered in SQL — which the hook keeps current incrementally. Lazy import keeps
# the dependency one-way (index_sqlite imports memory_hook, not vice-versa).

def _scale_index():
    try:
        try:
            from . import index_sqlite
        except ImportError:
            import index_sqlite
        return index_sqlite
    except Exception as e:
        log(f"scale-index unavailable: {e}")
        return None


def scale_index_ready() -> bool:
    """True when the SQLite index exists AND was built for the live embedding
    model — only then can the hot path skip the whole-cache JSON parse without
    ranking the query against stale-model vectors (audit A5). An unstamped legacy
    index (no meta) is accepted as current so an upgrade doesn't force a rebuild."""
    idx = _scale_index()
    try:
        if not idx:
            return False
        # A current index always carries a stamped `meta`, so its presence doubles as
        # the existence check — one connection, not index_exists()+index_meta() (round 3).
        meta = idx.index_meta()
        if not meta or meta.get("vec_format") != idx.VEC_FORMAT:
            return False        # absent/legacy/stale-format → ensure() rebuilds; cache meanwhile
        return _embed_sig_current(meta.get("model"))
    except Exception:
        return False


def ensure_scale_index() -> None:
    """Build the SQLite index on first need so the fast retrieval path is actually
    taken (audit A2): the accelerator otherwise stayed dormant until the next write,
    leaving every prompt to parse the whole JSON cache. Builds only when ABSENT —
    a model-stale index is left to `scale_index_ready()`/`embed_index --rebuild`,
    never rebuilt here (that would loop every session on an un-re-embedded store)."""
    idx = _scale_index()
    if not idx:
        return
    try:
        meta = idx.index_meta()      # {} when absent/legacy — one connection (round 3)
        # present AND current pack format → nothing to do. A model-stale index is
        # deliberately NOT rebuilt here (it needs a re-embed, not a rebuild — that
        # would loop every session); only absence or a format change triggers a
        # build, and a format change is always fixable from the float32 cache (P3).
        if meta and meta.get("vec_format") == idx.VEC_FORMAT:
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
        # (P3 reads float16 — a misread of a legacy float32 BLOB is garbage). ensure()
        # normally rebuilds first, but if its rebuild failed we must still not read it.
        if not scale_index_ready():
            return None
        limit = None
        if query and idx.candidate_count(project, cross) > RETRIEVAL_PREFILTER_LIMIT:
            limit = RETRIEVAL_PREFILTER_LIMIT
        return idx.iter_candidates(project, cross=cross, query=query, limit=limit)
    except Exception as e:
        log(f"scale-index read failed — falling back to JSON cache: {e}")
        return None


def sync_scale_index(records: dict | None = None, delete: list | None = None) -> None:
    """Keep the SQLite accelerator current: build it from the cache if missing,
    else upsert `records` (stem->record) and delete `delete` stems. Derived &
    rebuildable, so any failure is logged and swallowed — the JSON cache remains
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
    """Full rebuild of the SQLite index from the current cache — for after bulk
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
    (no whole-cache parse — audit C2; FTS-prefiltered on large projects — P1), else
    the in-memory/JSON cache (small stores, or a passed-in cache the caller holds)."""
    rows = _scale_candidates(project, cross=cross, query=query)
    if rows is not None:
        return rows
    if cache is None:
        cache = load_embed_cache()
    if cross:
        return [(s, r) for s, r in cache.items()
                if isinstance(r, dict) and isinstance(r.get("vec"), list)
                and r.get("project") and r.get("project") != project]
    return [(s, r) for s, r in cache.items()
            if isinstance(r, dict) and isinstance(r.get("vec"), list)
            and r.get("project") == project]


# ── Note writers (Zettelkasten edition) ───────────────────────────────
# Each note links to:
#   - parent project   (Context/<project>.md)
#   - source session   (Sessions/<session>.md)
#   - sibling notes    (other extractions from the same session)

NTYPE_LABEL_RU = {"pattern": "Паттерны", "mistake": "Ошибки", "decision": "Решения"}


def _unique_path(folder: Path, base_stem: str) -> Path:
    """Collision-free .md path: appends -2, -3, … and finally a 6-char
    fingerprint when all numeric suffixes are taken, so we never silently
    overwrite an existing note."""
    for n in range(1, 10):
        suffix = "" if n == 1 else f"-{n}"
        fp = folder / f"{base_stem}{suffix}.md"
        if not fp.exists():
            return fp
    import secrets
    fp = folder / f"{base_stem}-{secrets.token_hex(3)}.md"
    log(f"Stem collisions ≥9 for {base_stem}; using random suffix → {fp.name}")
    return fp


def _link_section(items: list[str], label: str) -> str:
    if not items:
        return ""
    return f"**{label}:**\n" + "\n".join(f"- [[{x}]]" for x in items)


def _stamp_frontmatter(text: str, fields: dict) -> str:
    """Insert or replace top-level scalar keys in a note's YAML frontmatter,
    leaving the body untouched. Shared by supersession and consolidation."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    body = text[end:]
    pending = dict(fields)
    out = []
    for ln in text[:end].split("\n"):
        key = ln.split(":", 1)[0].strip() if ":" in ln else ""
        if key in pending:
            out.append(f"{key}: {_yaml_scalar(pending.pop(key))}")
        else:
            out.append(ln)
    for k, v in pending.items():
        out.append(f"{k}: {_yaml_scalar(v)}")
    return "\n".join(out) + body


def _live_typed_paths(folder_path: Path, project: str, ntype: str,
                      slug: str) -> list[Path]:
    """Live (non-archived, non-superseded) notes matching project+ntype+slug,
    newest first. Superseded/ and Archive/ are subdirs, so a flat glob skips them."""
    hits = []
    for p in folder_path.glob("*.md"):
        parsed = parse_typed_stem(p.stem)
        if parsed and parsed["project"] == project \
                and parsed["ntype"] == ntype and parsed["slug"] == slug:
            hits.append(p)
    return sorted(hits, key=lambda p: p.stem, reverse=True)


def supersede_note(p: Path, new_stem: str) -> bool:
    """Retire a superseded note: stamp status, move into <folder>/Superseded/
    (Obsidian still resolves [[stem]]), drop it from the embedding cache so
    recall surfaces only current truth — contradictory facts no longer coexist
    (audit H1)."""
    if p.stem == new_stem:
        return False
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    fields = {"status": "superseded", "superseded_by": new_stem}
    new_date = (parse_typed_stem(new_stem) or {}).get("date", "")
    if new_date:
        fields["valid_to"] = new_date    # M-5: belief held until the replacement
    text = _stamp_frontmatter(text, fields)
    dest_dir = p.parent / "Superseded"
    dest_dir.mkdir(exist_ok=True)
    try:
        write_atomic(dest_dir / p.name, text)
    except OSError as e:
        log(f"Supersede failed for {p.name}: {e}")
        return False
    try:
        p.unlink(missing_ok=True)
    except OSError as e:
        # the copy landed in Superseded/ but the original would not delete (Windows: Obsidian /
        # AV / OneDrive holding it open). Roll the copy back — leaving BOTH would mean two
        # contradictory "live" notes, exactly what this mechanism exists to prevent
        # (code-review 2026-07).
        try:
            (dest_dir / p.name).unlink(missing_ok=True)
        except OSError:
            pass
        log(f"Supersede failed for {p.name} (unlink: {e}); rolled back the Superseded/ copy")
        return False
    _unregister_slug(p.stem)              # keep the grounding cache honest (audit A16)
    cache = load_embed_cache()
    if cache.pop(p.stem, None) is not None:
        save_embed_cache(cache)
    sync_scale_index(delete=[p.stem])     # drop from the SQLite index too (C2/C3)
    log(f"Superseded {p.stem} → {new_stem}")
    return True


def mark_resolved(mistake_fp: Path, by_stem: str) -> bool:
    """Flag a mistake as resolved by a later decision/pattern (audit I-18). The
    mistake stays LIVE (still history, still searchable), but recall stops
    treating it as an active 'do-not-repeat' warning — see _note_snippet/emit."""
    try:
        text = mistake_fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    write_atomic(mistake_fp, _stamp_frontmatter(
        text, {"status": "resolved", "resolved_by": by_stem}))
    # Propagate the flag to the retrieval substrate NOW so the salience de-weight
    # actually applies — it was otherwise dead until the next full embed --rebuild,
    # because the live cache/index never carried `resolved` (critic round 3).
    cache = load_embed_cache()
    rec = cache.get(mistake_fp.stem)
    if isinstance(rec, dict) and not rec.get("resolved"):
        rec["resolved"] = True
        save_embed_cache(cache)
        sync_scale_index(records={mistake_fp.stem: rec})
    log(f"Resolved {mistake_fp.stem} ← {by_stem}")
    return True


def _note_recurrence(p: Path) -> int:
    """Recurrence count from a note's YAML header (default 1, tolerant of junk)."""
    try:
        return int(_read_frontmatter_file(p).get("recurrence", 1) or 1)
    except (TypeError, ValueError):
        return 1


RECUR_SOURCES_CAP = 25      # bound the stored provenance set; the count is the ranking signal


def _note_recur_sources(p: Path) -> tuple[int, set]:
    """Recurrence count + the DISTINCT sessions that contributed to a note, from ONE
    frontmatter read (the supersede loop needs both — reading once avoids a double parse).
    Recurrence counts distinct sources, so re-stating a false lesson from one session can't
    inflate its salience past 1 — anti-gaming defence-in-depth beyond write idempotency (C5)."""
    fm = _read_frontmatter_file(p)
    try:
        n = int(fm.get("recurrence", 1) or 1)
    except (TypeError, ValueError):
        n = 1
    src = fm.get("sources")
    if isinstance(src, list):
        sources = {str(s) for s in src if s}
    else:
        s = fm.get("session")
        sources = {str(s)} if s else set()
    return n, sources


def _embed_recurrence(stem: str, ntype: str, cache: dict) -> int:
    """Recurrence for a freshly embedded note: the note's own frontmatter wins
    (write_typed_note carries the count forward on a re-statement — audit A3),
    else the prior cache entry for that stem."""
    folder = TYPE_FOLDER.get(ntype)
    if folder and (r := _note_recurrence(VAULT / folder / f"{stem}.md")) > 1:
        return r
    return int((cache.get(stem) or {}).get("recurrence", 1) or 1)


def _note_resolved(stem: str, ntype: str) -> bool:
    """True when the note's frontmatter marks it resolved — read at embed time so the
    live update_embeddings path carries `resolved` like embed_index.py does, instead
    of leaving the salience de-weight dead until a --rebuild (critic round 3)."""
    folder = TYPE_FOLDER.get(ntype)
    if not folder:
        return False
    fm = _read_frontmatter_file(VAULT / folder / f"{stem}.md")
    return fm.get("status") == "resolved" or bool(fm.get("resolved_by"))


def write_typed_note(folder: str, item, project: str, date: str,
                     tags: list, ntype: str,
                     session_stem_: str | None = None,
                     siblings: list[str] | None = None) -> str:
    if isinstance(item, dict):
        # title goes through the same scrub as desc/prevention: it lands in the heading AND
        # the filename slug, so a secret-shaped string there would be baked in twice
        title = redact_secrets(_strip_lead_icon(item.get("title", "untitled")))
        desc = redact_secrets(item.get("description", ""))        # audit B11
        prevention = redact_secrets(item.get("prevention", ""))
        supersedes_title = (item.get("supersedes") or "").strip()
        contradicts_title = (item.get("contradicts") or "").strip()   # M-2
        resolves_title = (item.get("resolves") or "").strip()
        confidence = item.get("confidence")                            # M-10
        entities = _norm_entities(item.get("entities"))                # entity graph (Phase 1)
        relations = _norm_relations(item.get("relations"))             # typed edges (Phase 2)
        entity_types = _norm_entity_types(item.get("entity_types"))    # Brain layer (F1): {entity: type}
    else:
        title = _strip_lead_icon(str(item))
        desc = prevention = supersedes_title = contradicts_title = resolves_title = ""
        confidence = None
        entities = []
        relations = []
        entity_types = {}

    # M-10/W8 memory-poisoning guard: refuse to persist extracted "knowledge" that looks like an
    # injection payload OR a bare dangerous imperative (exfiltration/destruction/security-bypass) —
    # defense-in-depth beyond secret redaction. Negation-gated so cautionary lessons survive.
    if _looks_unsafe(f"{title} {desc} {prevention}"):
        log(f"Rejected note (unsafe payload): {title[:50]!r}")
        return ""

    p = VAULT / folder
    p.mkdir(exist_ok=True)
    slug = slugify(title)
    base_stem = typed_stem(date, project, ntype, title)

    # Per-session idempotency (audit C5): if THIS session already wrote a live
    # note with the same identity — e.g. a prior run crashed after writing it but
    # before marking the session processed — return that note instead of creating
    # a -2 duplicate. This is what lets process_session mark the session AFTER the
    # writes (so a crash retries) without the retry duplicating notes.
    if session_stem_:
        for old in _live_typed_paths(p, project, ntype, slug):
            try:
                fm, _ = _read_frontmatter(old.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if fm.get("session") == session_stem_:
                log(f"Idempotent skip (already written this session): {old.stem}")
                return old.stem
        if QUARANTINE_MODE:        # a crash-retry must not duplicate a note already quarantined this session
            for old in _live_typed_paths(p / "Quarantine", project, ntype, slug):
                try:
                    qfm, _ = _read_frontmatter(old.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                if qfm.get("session") == session_stem_:
                    log(f"Idempotent skip (already quarantined this session): {old.stem}")
                    return ""

    # Reconcile (audit H1 + M-2): retire prior versions / contradicted notes so current truth stays
    # single. (a) older same-slug note = a re-statement; (b) explicit `supersedes`/`contradicts`.
    # Retirement is DEFERRED into `to_retire` and executed only AFTER the W7 quarantine decision, so
    # a quarantined (uncorroborated, suspicious) note never retires a corroborated true one.
    retired: list[str] = []
    prior_recur = 0
    prior_sources: set = set()
    to_retire: list = []
    superseded_corroborated = False
    for old in _live_typed_paths(p, project, ntype, slug):
        if old.stem != base_stem:
            # A same-slug note is THIS lesson recurring: read its count + contributing sessions
            # BEFORE supersede drops it from the cache, then carry forward below — otherwise
            # recurrence is pinned at 1 forever and the recurrence-boost signal is dead (audit A3).
            r_old, s_old = _note_recur_sources(old)        # one frontmatter read for both
            prior_recur = max(prior_recur, r_old)
            prior_sources |= s_old
            to_retire.append(old)
    for other_title in (supersedes_title, contradicts_title):
        if not other_title:
            continue
        o_slug = slugify(other_title)
        if o_slug and o_slug != slug:
            for old in _live_typed_paths(p, project, ntype, o_slug):
                # An explicit supersede/contradict (incl. the M-2 write-time semantic path) is ALSO a
                # re-encounter of that lesson — carry its recurrence + sources forward, else
                # recurrence only grows on the rare exact-slug re-statement (measured: 328/328 were 1).
                r_old, s_old = _note_recur_sources(old)
                prior_recur = max(prior_recur, r_old)
                prior_sources |= s_old
                if r_old >= 2:
                    superseded_corroborated = True         # W7: a lone note retiring corroborated truth
                to_retire.append(old)

    # W7 corroboration-gated quarantine (opt-in; see QUARANTINE_MODE). Divert a single-source note
    # that is also suspicious to <folder>/Quarantine/ instead of trusting it — retiring NOTHING.
    dest = p
    quarantine_reason = ""
    if QUARANTINE_MODE:
        n_sources = len(prior_sources | ({session_stem_} if session_stem_ else set())) or 1
        qconf = _coerce_confidence(confidence)
        if n_sources < 2 and qconf is not None and qconf >= QUARANTINE_CONF:
            quarantine_reason = "single-source near-max confidence"
        elif n_sources < 2 and superseded_corroborated:
            quarantine_reason = "single-source supersedes a corroborated note"
        if quarantine_reason:
            dest = p / "Quarantine"
            dest.mkdir(exist_ok=True)

    if not quarantine_reason:                              # trusted → execute the deferred retirement
        for old in to_retire:
            if supersede_note(old, base_stem):
                retired.append(old.stem)

    fp = _unique_path(dest, base_stem)
    stem = fp.stem

    # Link a resolving pattern/decision to the mistake it fixes (audit I-18): the
    # mistake is flagged resolved (no longer an active warning) but kept.
    resolved: list[str] = []
    if resolves_title and ntype in ("pattern", "decision") and not quarantine_reason:
        r_slug = slugify(resolves_title)
        if r_slug:
            for mp in _live_typed_paths(VAULT / TYPE_FOLDER["mistake"],
                                        project, "mistake", r_slug):
                if mark_resolved(mp, stem):
                    resolved.append(mp.stem)

    fm = {"date": date, "project": project, "tags": tags, "type": ntype}
    fm["valid_from"] = (item.get("valid_from") if isinstance(item, dict) else None) or date  # M-5
    if session_stem_:
        fm["session"] = session_stem_          # provenance (M-10)
    cval = _coerce_confidence(confidence)
    if cval is not None:
        fm["confidence"] = round(cval, 2)   # M-10 (read back in ranking — H2)
    if entities:
        fm["entities"] = entities           # entity graph (Phase 1): faceted recall + co-occurrence
    if relations:
        fm["relations"] = relations         # typed edges (Phase 2): relation-aware multi-hop
    if entity_types:
        fm["entity_types"] = entity_types   # Brain layer (F1): {entity: paper|method|...} for entity cards
    if quarantine_reason:
        fm["quarantine_reason"] = quarantine_reason       # W7 provenance (kept out of recall)
    # Recurrence carry-forward, hardened against recurrence-GAMING (3B): the count is the
    # number of DISTINCT contributing sessions, so re-stating a false lesson from ONE
    # session can't inflate its salience. No provenance (session=None) → legacy +1 (an
    # anonymous source can't be deduped). A known session already in the set adds nothing.
    # A quarantined note must NOT inherit the trust history (recurrence/sources) of notes it did
    # not retire — else, if later promoted, it arrives with corroboration it never earned (W7 audit).
    if (prior_recur or prior_sources) and not quarantine_reason:
        if session_stem_ is None:
            fm["recurrence"] = prior_recur + 1                  # anonymous source: legacy +1
        else:
            sources = prior_sources | {session_stem_}
            grew = session_stem_ not in prior_sources           # a known session adds nothing
            fm["recurrence"] = max(prior_recur + (1 if grew else 0), len(sources))
            fm["sources"] = sorted(sources)[:RECUR_SOURCES_CAP]
    if retired:
        fm["supersedes"] = retired
    if resolved:
        fm["resolves"] = resolved
    icon = TYPE_ICON.get(ntype, "")
    body_tags = render_body_tags(tags, [f"project/{project}", ntype])

    body = [fm_block(fm), "", f"# {icon} {title}".strip(), ""]
    if desc:
        body += [desc, ""]
    if prevention:
        body += [f"**Как избежать:** {prevention}", ""]
    body += [f"**Проект:** [[{project}]]", f"**Дата:** {date}"]
    if session_stem_:
        body.append(f"**Сессия:** [[{session_stem_}]]")
    if retired:
        body += ["", "_Заменяет: " + ", ".join(f"[[{s}]]" for s in retired) + "_"]
    if resolved:
        body += ["", "_Решает: " + ", ".join(f"[[{s}]]" for s in resolved) + "_"]

    related = [s for s in (siblings or []) if s and s != stem]
    if related:
        body += ["", "## Связанные заметки", *(f"- [[{s}]]" for s in related)]

    body += ["", body_tags]
    write_atomic(fp, "\n".join(body))
    if quarantine_reason:
        log(f"Quarantined note ({quarantine_reason}): {folder}/Quarantine/{fp.name}")
        return ""                       # on disk for review, but NOT embedded/recalled (W7)
    log(f"Written: {folder}/{fp.name}")
    return stem


def write_session_note(project: str, date: str, time_str: str, summary: str,
                       cwd: str, session_id: str, tags: list,
                       links: dict[str, list[str]], trigger: str,
                       agent: str = DEFAULT_AGENT) -> str:
    p = VAULT / "Sessions"
    p.mkdir(exist_ok=True)
    # _unique_path (not a bare {stem}.md) so two distinct sessions never silently
    # overwrite each other on a stem collision — the 8-char id slice is weak for
    # prefixed ids ("ingest-<hex>" varies in ~1 hex char), audit 2026-06-18 HIGH.
    fp = _unique_path(p, session_stem(date, time_str, project, session_id))
    stem = fp.stem

    fm = {"date": date, "project": project, "tags": tags, "type": "session",
          "session_id": session_id[:8], "trigger": trigger, "agent": agent}
    body_tags = render_body_tags(tags, [f"project/{project}", "session"])

    sections = [
        fm_block(fm), "",
        f"# Session — {project} ({time_str})", "",
        summary, "",
        f"**Каталог:** `{cwd}`",
        f"**Триггер:** {trigger}",
        f"**Проект:** [[{project}]]",
        "",
    ]
    for nt in TYPED_TYPES:
        block = _link_section(links.get(nt, []), NTYPE_LABEL_RU[nt])
        if block:
            sections += [block, ""]
    sections.append(body_tags)

    write_atomic(fp, "\n".join(sections))
    log(f"Written: Sessions/{fp.name}")
    return stem


def _split_context(text: str) -> tuple[str, list[str]]:
    """Split a Context file into (head, entries). `head` is the frontmatter +
    title + intro; `entries` are the per-session '## <date>' blocks plus any
    existing compressed-state block."""
    lines = text.split("\n")
    idx = None
    for i, ln in enumerate(lines):
        if re.match(r"^##\s+(\d{4}-\d{2}-\d{2}|Накопленное состояние)", ln):
            idx = i
            break
    if idx is None:
        return text.rstrip(), []
    head = "\n".join(lines[:idx]).rstrip()
    entries, cur = [], []
    for ln in lines[idx:]:
        # anchor boundaries to REAL entry headers only (date or state), so a
        # '## subheading' inside an entry/state body stays attached (audit D4)
        if re.match(r"^##\s+(\d{4}-\d{2}-\d{2}|Накопленное состояние)", ln):
            if cur:
                entries.append("\n".join(cur).rstrip())
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        entries.append("\n".join(cur).rstrip())
    return head, entries


def compact_context_if_needed(fp: Path, project: str, allow_llm: bool = True):
    """When a Context file outgrows CONTEXT_MAX_BYTES, fold the oldest entries into
    one rolling 'state' block and keep only the most recent verbatim (audit
    F4/F23/F37). Idempotent: a no-op on an already-small file.

    `allow_llm=False` (the LIVE hook path) makes this a DEFERRED no-op: no model
    call is ever made under the vault lock (audit C4). The actual summary+compaction
    runs in scheduled maintenance (`maintain_contexts` from process_now/consolidate,
    `allow_llm=True`). Compaction only rewrites the file when a real summary is
    produced; on a backend failure it leaves the file UNCHANGED rather than dropping
    entry bodies — no data loss (failure-injection probes). The SessionStart payload
    reads the bounded project card, not the raw file, so a briefly-oversized Context
    never bloats injection."""
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if len(text.encode("utf-8")) <= CONTEXT_MAX_BYTES:
        return
    if not allow_llm:
        return  # defer to scheduled maintenance — never call the LLM under the lock
    head, entries = _split_context(text)
    if len(entries) < 2:
        return  # nothing to compress

    # Choose how many trailing entries fit verbatim under the cap, reserving room
    # for the head and the regenerated state block. The byte cap is HARD now:
    # keep shrinks toward CONTEXT_KEEP_MIN, so a few large entries can't blow past
    # it (audit M2). Any prior state block is the oldest entry → folded back in.
    reserve = 1800  # state block + archive-links budget
    budget = max(0, CONTEXT_MAX_BYTES - len(head.encode("utf-8")) - reserve)
    recent, used = [], 0
    for e in reversed(entries):
        b = len(e.encode("utf-8")) + 2
        if recent and used + b > budget:
            break
        recent.append(e)
        used += b
    recent.reverse()
    lo = min(CONTEXT_KEEP_MIN, len(entries) - 1)        # continuity floor
    hi = min(CONTEXT_KEEP_RECENT, len(entries) - 1)     # ceiling
    n_keep = max(lo, min(len(recent), hi))
    recent = entries[len(entries) - n_keep:] if n_keep else []
    old = entries[:len(entries) - n_keep]
    if not old:
        old, recent = entries[:1], entries[1:]

    prompt = (
        f"Сожми историю проекта '{project}' в краткое накопленное состояние.\n"
        f"Сохрани: ключевые решения с датами, текущий статус, нерешённые "
        f"вопросы, важные грабли. Убери воду и дубли. 8-15 строк, маркдаун-"
        f"буллеты. Верни ТОЛЬКО JSON: {{\"state\": \"<сжатый текст>\"}}.\n\n"
        f"ИСТОРИЯ:\n" + truncate_smart(redact_secrets("\n\n".join(old)),
                                       MAX_TRANSCRIPT_CHARS)
    )
    res = generate_json(prompt, project=project)
    state = res.get("state") if isinstance(res, dict) else None
    if not isinstance(state, str) or not state.strip():
        # leave the file UNCHANGED on a backend failure — never drop entry bodies
        # without a summary (no data loss); the next maintenance run retries.
        log(f"Context compaction skipped for {project} (no summary)")
        return
    # preserve wikilinks from compacted entries (bounded) so compaction never
    # orphans a note from the graph yet can't grow without limit (fuzz PROBE 2 / M2)
    old_links, seen_l = [], set()
    for e in old:
        for lnk in re.findall(r"\[\[([^]|#]+)", e):
            lnk = lnk.strip()
            if lnk and lnk not in seen_l:
                seen_l.add(lnk)
                old_links.append(lnk)
    if len(old_links) > CONTEXT_LINK_ARCHIVE_MAX:
        old_links = old_links[-CONTEXT_LINK_ARCHIVE_MAX:]
    m_old = re.search(r"(\d{4}-\d{2}-\d{2})", old[0])
    m_new = re.search(r"(\d{4}-\d{2}-\d{2})", old[-1])
    span = f" ({m_old.group(1)} → {m_new.group(1)})" if (m_old and m_new) else ""
    compressed = (f"## Накопленное состояние (сжато){span}\n\n{state.strip()}\n\n"
                  f"_Сжато из {len(old)} ранних записей._")
    if old_links:
        compressed += ("\n\n**Архив ссылок:** "
                       + " ".join(f"[[{lk}]]" for lk in old_links))
    new_text = head + "\n\n" + compressed + "\n\n" + "\n\n".join(recent) + "\n"
    # Hard guard: if the summary came back long, truncate the file to the cap
    # rather than silently exceed it (audit M2) — on a character boundary so the
    # multibyte tail is never corrupted (audit M-g).
    if len(new_text.encode("utf-8")) > CONTEXT_MAX_BYTES:
        new_text = _truncate_utf8_bytes(new_text, CONTEXT_MAX_BYTES).rstrip() + "\n"
    write_atomic(fp, new_text)
    log(f"Context compacted: {project} ({len(old)} entries → state, kept {len(recent)})")


def maintain_contexts(allow_llm: bool = True) -> None:
    """Scheduled maintenance over all Context files: LLM-summary compaction of
    oversized files + project-card refresh. Kept OFF the live hook path (where
    compaction is GPU-free), so a model call is never made under the vault lock
    (audit C4). Called by process_now (4-hourly) and consolidate (weekly)."""
    cdir = VAULT / "Context"
    if not cdir.exists():
        return
    for cf in cdir.glob("*.md"):
        proj = cf.stem
        try:
            compact_context_if_needed(cf, proj, allow_llm=allow_llm)
            refresh_project_card(proj, cf)
        except Exception as e:
            log(f"context maintenance skipped for {proj}: {e}")


# ── Structured project card (audit I-15) ──────────────────────────────
# A distilled, regenerated rollup of a project's live notes, kept at the top of
# Context/<project>.md. Replaces the raw journal tail as the SessionStart
# injection surface: organised by KIND of fact (status / stack / open gotchas /
# decisions / recurring) instead of chronologically — bounded, deterministic,
# GPU-free (no LLM). Cheaper to inject, higher signal per token than the journal.

def _one_line(s: str, limit: int) -> str:
    """Collapse whitespace to a single line and hard-truncate (card fields)."""
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s[:limit].rstrip()


def _read_frontmatter(text: str) -> tuple[dict, str]:
    """(frontmatter dict, body). Inline-JSON values are parsed back to their type —
    arrays to lists (entities/relations), maps to dicts (entity_types) — and double-quoted
    scalars unquoted; everything else stays a raw string. Tolerant of a missing/short
    frontmatter — used by the project-card distillation."""
    if text[:1] == "﻿":          # a BOM-writing editor must not blank the header (audit A7)
        text = text[1:]
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm = {}
    for ln in text[3:end].split("\n"):
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        if (v.startswith("[") and v.endswith("]")) or (v.startswith("{") and v.endswith("}")):
            try:
                v = json.loads(v)          # JSON array OR map (e.g. entity_types) → list/dict
            except ValueError:
                pass
        elif len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        fm[k] = v
    return fm, text[end + 4:]


def _read_frontmatter_file(p: Path) -> dict:
    """Frontmatter dict read from ONLY the YAML header of a note, not the whole
    file (audit M-a): point-in-time scans (`as_of`) open every note, so per-note
    I/O must stay tiny. Returns {} if the file has no leading frontmatter."""
    head = []
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            if f.readline().lstrip("﻿").rstrip("\n") != "---":   # tolerate BOM (audit A7)
                return {}
            for ln in f:
                if ln.rstrip("\n") == "---":
                    break
                head.append(ln)
    except OSError:
        return {}
    fm, _ = _read_frontmatter("---\n" + "".join(head) + "---\n")
    return fm


def _parse_note_body(lines) -> tuple[str, str, str]:
    """The ONE body parser for a typed note → (title, desc, prevention). Title is the first
    `# ` heading (icon-stripped, "" if none); desc is the first plain line after it; prevention
    is the `**Как избежать:**` line. _note_meta, _note_snippet, and embed_index.note_fields all
    ride this so the three copies can't drift again (code-review 2026-07: _note_snippet's
    exclude-tuple had already lost "---" and could pick a horizontal rule as the description)."""
    title, desc, prevention, seen = "", "", "", False
    for ln in lines:
        s = ln.strip()
        if s.startswith("# "):
            title = _strip_lead_icon(s.lstrip("# "))
            seen = True
            continue
        if not seen or not s:
            continue
        if s.startswith("**Как избежать:**"):
            prevention = s.replace("**Как избежать:**", "").strip()
        elif not desc and not s.startswith(("**", "#", "-", "_", "---", "[[", "|")):
            desc = s
    return title, desc, prevention


def _note_meta(p: Path, ntype: str, parsed: dict) -> dict | None:
    """Read one typed note in a single pass → the fields the project card needs:
    date (from stem), title/desc/prevention (body), tags/resolved/recurrence
    (frontmatter). None on read failure."""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm, body = _read_frontmatter(text)
    title, desc, prevention = _parse_note_body(body.split("\n"))
    title = title or p.stem
    raw_tags = fm.get("tags", [])
    if isinstance(raw_tags, str):
        raw_tags = [t for t in re.split(r"[,\s]+", raw_tags) if t]
    resolved = bool(fm.get("resolved_by")) or str(fm.get("status", "")).lower() == "resolved"
    try:
        rec = int(str(fm.get("recurrence", "1")).strip() or "1")
    except ValueError:
        rec = 1
    return {"stem": p.stem, "ntype": ntype, "date": parsed["date"],
            "title": title.strip(), "desc": desc, "prevention": prevention,
            "tags": _norm_tags(raw_tags), "resolved": resolved, "recurrence": rec,
            "entities": _norm_entities(fm.get("entities")),
            "relations": _norm_relations(fm.get("relations")),
            "entity_types": _norm_entity_types(fm.get("entity_types"), gate=False),
            "salience": _coerce_salience(fm.get("salience")),
            "superseded_by": str(fm.get("superseded_by") or "")}    # "" for live notes (F3 timeline)


def _coerce_salience(v) -> float:
    """A stamped salience (Brain F5) clamped to [0,1]; 0.0 when absent/garbage so an unstamped
    note is neutral in ranking — the boost is inert until consolidation scores the graph."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f != f else max(0.0, min(1.0, f))   # NaN-safe clamp


def _note_meta_for_stem(stem: str) -> dict | None:
    """Note meta for a bare stem — the SQLite graph upsert (F4) reads only the touched files
    by stem. None if the stem is unparseable or its live file is gone (superseded/archived →
    the caller just drops the graph rows for it)."""
    parsed = parse_typed_stem(stem)
    if not parsed:
        return None
    folder = TYPE_FOLDER.get(parsed["ntype"])
    if not folder:
        return None
    p = VAULT / folder / f"{stem}.md"
    if not p.exists():
        return None
    meta = _note_meta(p, parsed["ntype"], parsed)
    if meta:
        meta.setdefault("project", parsed["project"])
    return meta


def _iter_superseded_notes(project: str | None = None) -> list[dict]:
    """Superseded notes (retired to <folder>/Superseded/) as metas carrying status +
    superseded_by — the history live recall hides, for the F3 entity timeline. O(superseded)
    scan; superseded notes are a small minority, and this runs only on the pull-only path."""
    out = []
    for ntype, folder in TYPE_FOLDER.items():
        d = VAULT / folder / "Superseded"
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            parsed = parse_typed_stem(p.stem)
            if not parsed or (project and parsed["project"] != project):
                continue
            meta = _note_meta(p, ntype, parsed)            # carries superseded_by (single read)
            if not meta:
                continue
            meta["status"] = "superseded"
            meta.setdefault("project", parsed["project"])
            out.append(meta)
    return out


def _superseded_index(project: str | None = None) -> dict:
    """entity -> [superseded note metas], one scan — shared across an entity-card refresh so a
    bulk pass reads the Superseded/ folders once, not once per entity (F3)."""
    idx: dict = {}
    for n in _iter_superseded_notes(project):
        for e in n.get("entities") or []:
            idx.setdefault(e, []).append(n)
    return idx


def _iter_project_notes(project: str) -> list[dict]:
    """All live (non-archived, non-superseded) typed notes for a project, as
    metadata dicts. Flat glob → Superseded/ and Archive/ subdirs are skipped."""
    out = []
    for ntype, folder in TYPE_FOLDER.items():
        d = VAULT / folder
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            parsed = parse_typed_stem(p.stem)
            if not parsed or parsed["project"] != project:
                continue
            meta = _note_meta(p, ntype, parsed)
            if meta:
                meta.setdefault("project", project)
                out.append(meta)
    return out


def _iter_all_notes() -> list[dict]:
    """Every live typed note across all projects, as metadata dicts (with `project`).
    The cross-project read source for the entity graph."""
    out = []
    for ntype, folder in TYPE_FOLDER.items():
        d = VAULT / folder
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            parsed = parse_typed_stem(p.stem)
            if not parsed:
                continue
            meta = _note_meta(p, ntype, parsed)
            if meta:
                meta["project"] = parsed["project"]
                out.append(meta)
    return out


# ── Entity + typed-relation knowledge graph ──────────────────────────
# The graph lives in anamnesis/graph.py (kept out of this module to keep it lean). It is
# re-exported here so `m.entity_index(...)` etc. keep working; graph.py imports memory_hook
# lazily-safe (only inside function bodies, never at import time), so this re-export does
# not deadlock the circular import.
try:
    from . import graph as _graph
except ImportError:
    import graph as _graph
entity_index = _graph.entity_index
entity_types_index = _graph.entity_types_index     # Brain layer (F1): entity -> type
entities_by_type = _graph.entities_by_type
entity_timeline = _graph.entity_timeline           # Brain layer (F3): live+superseded history
salience_index = _graph.salience_index             # Brain layer (F5): graph-centrality salience
_edge_counts = _graph._edge_counts
_edges_sorted = _graph._edges_sorted
notes_for_entity = _graph.notes_for_entity
co_occurring = _graph.co_occurring
entity_graph = _graph.entity_graph
related_by = _graph.related_by
relation_graph = _graph.relation_graph
relation_expand = _graph.relation_expand
graph_export = _graph.graph_export


# tags that carry no topical signal in the "stack/themes" line
_CARD_TAG_STOP = {"context", "session", *TYPED_TYPES}


def _card_item(n: dict) -> str:
    snip = n.get("desc", "")
    if n.get("prevention"):
        snip = f"{snip} → {n['prevention']}" if snip else n["prevention"]
    mark = "✅ " if n.get("resolved") else ""
    rec = f" ×{n['recurrence']}" if n.get("recurrence", 1) >= 2 else ""
    body = f" — {_one_line(snip, 160)}" if snip else ""
    return f"- {mark}**{_one_line(n.get('title', ''), 80)}**{rec}{body}"


def build_project_card(project: str, status_hint: str = "") -> str:
    """Distil a project's live notes into a structured card (audit I-15):
    status · stack · open gotchas · key decisions · recurring lessons. Pure
    structural rollup — deterministic, GPU-free, no LLM. Returns the full block
    (START/END markers included) or '' when there is nothing worth showing."""
    notes = _iter_project_notes(project)
    section = []

    status = _one_line(status_hint, 200)
    if not status and notes:
        status = _one_line(max(notes, key=lambda n: (n["date"], n["stem"]))["title"], 200)
    if status:
        section.append(f"**Статус:** {status}")

    freq = {}
    for n in notes:
        for t in n["tags"]:
            if t in _CARD_TAG_STOP or t.startswith("project/"):
                continue
            freq[t] = freq.get(t, 0) + 1
    stack = [t for t, _ in sorted(freq.items(), key=lambda x: (-x[1], x[0]))[:8]]
    if stack:
        section.append("**Стек/темы:** " + ", ".join(stack))

    open_gotchas = sorted((n for n in notes if n["ntype"] == "mistake" and not n["resolved"]),
                          key=lambda n: (n["recurrence"], n["date"]), reverse=True)
    decisions = sorted((n for n in notes if n["ntype"] == "decision"),
                       key=lambda n: n["date"], reverse=True)
    open_stems = {n["stem"] for n in open_gotchas[:CARD_MAX_ITEMS]}
    recurring = sorted((n for n in notes if n["recurrence"] >= 2 and n["stem"] not in open_stems),
                       key=lambda n: n["recurrence"], reverse=True)

    blocks = []
    if open_gotchas:
        blocks.append(["", "**⚠️ Открытые грабли (не повтори):**"]
                      + [_card_item(n) for n in open_gotchas[:CARD_MAX_ITEMS]])
    if decisions:
        blocks.append(["", "**🎯 Ключевые решения:**"]
                      + [_card_item(n) for n in decisions[:CARD_MAX_ITEMS]])
    if recurring:
        blocks.append(["", "**🔁 Повторяется (recurrence≥2):**"]
                      + [_card_item(n) for n in recurring[:3]])

    if not section and not blocks:
        return ""
    lines = [CARD_START, CARD_HEADER, ""] + section
    for b in blocks:
        lines += b
    lines.append(CARD_END)
    return "\n".join(lines)


def _strip_card(text: str) -> str:
    """Remove any existing project-card block (between markers), tidily."""
    return re.sub(re.escape(CARD_START) + r".*?" + re.escape(CARD_END) + r"\n*",
                  "", text, flags=re.S)


def _insert_card(text: str, card: str) -> str:
    """Place the card just before the first journal/state entry (its home in the
    file head). Assumes any prior card was already stripped."""
    block = card.rstrip() + "\n"
    mt = re.search(r"^##\s+(\d{4}-\d{2}-\d{2}|Накопленное состояние)", text, flags=re.M)
    if mt:
        i = mt.start()
        return text[:i].rstrip() + "\n\n" + block + "\n" + text[i:].lstrip("\n")
    return text.rstrip() + "\n\n" + block


def refresh_project_card(project: str, fp: Path | None = None) -> None:
    """Regenerate the structured card at the top of Context/<project>.md from the
    project's current notes (audit I-15). Idempotent: strips the old card and
    re-inserts a fresh one; writes only on change. Never raises into the hook."""
    if not PROJECT_CARD_ENABLED:
        return
    fp = fp or (VAULT / "Context" / f"{project}.md")
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return  # no Context file yet → nothing to host the card
    # status hint = the most recent journal entry's first real content line
    body = text
    if body.startswith("---"):
        e = body.find("\n---", 3)
        if e != -1:
            body = body[e + 4:]
    _head, entries = _split_context(_strip_card(body))
    labels = tuple(NTYPE_LABEL_RU.values())
    hint = ""
    for entry in reversed(entries):
        if not re.match(r"^##\s+\d{4}-\d{2}-\d{2}", entry):
            continue
        for ln in entry.split("\n")[1:]:
            s = ln.strip()
            if s and not s.startswith(("Сессия:", "[[", "**", "#")) \
                    and not any(s.startswith(lbl + ":") for lbl in labels):
                hint = s
                break
        if hint:
            break
    card = build_project_card(project, status_hint=hint)
    new = _strip_card(text)
    if card:
        new = _insert_card(new, card)
    if new != text:
        try:
            write_atomic(fp, new)
            log(f"Project card refreshed: {project}")
        except OSError as exc:
            log(f"Project card write skipped for {project}: {exc}")


# ── Entity cards (Brain layer, F2) ───────────────────────────────────────────────
# A distilled, regenerated card per first-class (TYPED) entity: where it is used across ALL
# projects, its typed neighbours, and the lessons grouped by kind. The SAME deterministic,
# GPU-free rollup as the project card — but stored as a standalone file under Entities/, which
# is NOT a TYPE_FOLDER, so a card NEVER enters the recall pool (separation invariant). Pull-only:
# read via api.entity_card / the MCP surface / an explicit request, never auto-injected.
ENTITIES_FOLDER = "Entities"


def _entity_card_stem(entity: str, etype: str) -> str:
    """Filename stem for an entity card: '<type>-<entity>' (both already kebab tokens), so a
    regenerated card overwrites its predecessor instead of duplicating. '' for a junk entity."""
    e = _norm_entities([entity])
    t = re.sub(r"[^a-z0-9-]", "", (etype or "entity").lower()) or "entity"
    return f"{t}-{e[0]}" if e else ""


def build_entity_card(entity: str, etype: str | None = None, idx: dict | None = None,
                      sup: dict | None = None) -> str:
    """Distil every live note tagged with `entity` (across ALL projects) into a standalone
    markdown card: type · where-used · typed neighbours · co-occurring entities · lessons grouped
    by kind, a first/last-seen line, and (F3) the EVOLUTION of the take — where an earlier note was
    later superseded. Deterministic structural rollup — no LLM, no embedder. Returns the full file
    text (frontmatter + body), or '' when nothing references it. `idx` reuses a pre-built
    entity_index and `sup` a pre-built superseded index, so a full refresh scans the vault once."""
    norm = _norm_entities([entity])
    if not norm:
        return ""
    ent = norm[0]
    # notes_for_entity / related_by / co_occurring each take the SQLite fast path when idx is
    # None and the graph index is built (F4); a caller doing a bulk refresh passes a shared
    # markdown idx instead, so the helpers reuse it and the vault is scanned once, not per card.
    notes = notes_for_entity(ent, None, k=500, idx=idx)
    if not notes:
        return ""
    etype = etype or entity_types_index().get(ent, "entity")
    projects = sorted({n.get("project") for n in notes if n.get("project")})
    tl = entity_timeline(ent, None, sup=sup, idx=idx)     # F3: spans live + superseded history
    first_seen, last_seen = tl.get("first_seen", ""), tl.get("last_seen", "")
    evo = tl.get("evolution", [])
    edges = related_by(ent, project=None, k=8, idx=idx)            # typed neighbours
    cooc = [e for e, _ in co_occurring(ent, None, k=8, idx=idx) if e != ent]

    fm = {"type": "entity", "entity_type": etype, "name": ent, "projects": projects,
          "first_seen": first_seen, "last_seen": last_seen}
    rel_bits = []
    if edges:
        rel_bits.append("  ·  ".join(f"{e['rel']} → {e['target']}" for e in edges[:5]))
    if cooc:
        rel_bits.append("рядом: " + ", ".join(cooc[:6]))

    by_type = {"mistake": [], "pattern": [], "decision": []}
    for n in notes:
        if n["ntype"] in by_type:
            by_type[n["ntype"]].append(n)
    lines = [f"# 🧠 {ent} · {etype}", "",
             f"**Где встречается:** {', '.join(projects)}  ({len(notes)} заметок)"
             if projects else f"**Заметок:** {len(notes)}"]
    if rel_bits:
        lines.append("**Связано:** " + "  ·  ".join(rel_bits))
    if first_seen:
        span = f"_Впервые: {first_seen} · последний раз: {last_seen}"
        span += f" · упоминаний: {tl.get('count', len(notes))}_" if tl else "_"
        lines.append(span)
    for kind, label in (("mistake", "**⚠️ Грабли:**"), ("pattern", "**✅ Паттерны:**"),
                        ("decision", "**🎯 Решения:**")):
        if by_type[kind]:
            lines += ["", label] + [_card_item(n) for n in by_type[kind][:CARD_MAX_ITEMS]]
    if evo:                                              # F3: how the understanding changed
        lines += ["", "**🕓 Эволюция понимания:**"]
        for r in evo[-CARD_MAX_ITEMS:]:
            nd = (parse_typed_stem(r.get("superseded_by", "")) or {}).get("date", "")
            arrow = f" → пересмотрено {nd}" if nd else " → пересмотрено позже"
            lines.append(f"- {r.get('date', '')}: «{_one_line(r.get('title', ''), 70)}»{arrow}")
    return fm_block(fm) + "\n" + "\n".join(lines) + "\n"


def write_entity_card(entity: str, etype: str | None = None, idx: dict | None = None,
                      sup: dict | None = None) -> str:
    """(Re)generate ONE entity card under Entities/ and write it atomically, only on change.
    Returns the stem when a card was actually WRITTEN, or '' when there was nothing to write —
    unchanged (idempotent no-op), no notes, junk entity, or no brain profile active. The 'only
    on change' return is what keeps a full refresh from churning git on a no-op pass."""
    if not _cfg.brain_enabled():
        return ""
    norm = _norm_entities([entity])
    if not norm:
        return ""
    etype = etype or entity_types_index().get(norm[0], "entity")
    stem = _entity_card_stem(norm[0], etype)
    card = build_entity_card(norm[0], etype, idx=idx, sup=sup)
    if not stem or not card:
        return ""
    d = VAULT / ENTITIES_FOLDER
    try:
        d.mkdir(exist_ok=True)
        fp = d / f"{stem}.md"
        if fp.exists() and fp.read_text(encoding="utf-8", errors="replace") == card:
            return ""                                      # unchanged → not a write, don't count/churn
        write_atomic(fp, card)
    except OSError as exc:
        log(f"Entity card write skipped for {stem}: {exc}")
        return ""
    return stem


def refresh_entity_cards(entities: list | None = None) -> int:
    """Regenerate entity cards (Brain layer, F2). With `entities` given, refresh just those
    typed entities (the ones a session touched — the per-session path); otherwise refresh EVERY
    typed entity in the store (the sleep-time pass). Off entirely unless a brain profile is
    active. With the SQLite graph built the cards query it directly (F4); otherwise ONE markdown
    scan is shared across all cards. Returns the count written; never raises."""
    if not _cfg.brain_enabled():
        return 0
    type_idx = entity_types_index()
    if not type_idx:
        return 0
    _sx = _scale_index()
    idx = None if (_sx and _sx.graph_index_ready()) else entity_index(None)
    sup = _superseded_index()                # F3: scan Superseded/ once, shared across all cards
    if entities is None:
        targets = list(type_idx.items())
    else:
        wanted = set(_norm_entities(list(entities), cap=64))
        targets = [(e, type_idx[e]) for e in wanted if e in type_idx]
    n = 0
    for ent, etype in targets:
        try:
            if write_entity_card(ent, etype, idx=idx, sup=sup):
                n += 1
        except Exception as exc:                           # one bad card must not abort the pass
            log(f"Entity card error for {ent}: {exc}")
    if n:
        log(f"Entity cards refreshed: {n}")
    return n


def entity_card(entity: str) -> str:
    """Read an entity's card text (Brain layer, F2), building it on the fly if no file exists
    yet. '' when the entity has no notes. The pull-only read surface for api / MCP / search —
    this is how Brain knowledge reaches an agent: by explicit request, never by injection."""
    norm = _norm_entities([entity])
    if not norm:
        return ""
    ent = norm[0]
    # Cache hit: a card file is named "<type>-<entity>.md". Glob by the entity suffix and confirm
    # via its `name` so we don't pay a corpus-wide entity_types_index() scan just to locate a file
    # already on disk (the glob alone is ambiguous — "method-gears" vs an entity "some-gears").
    d = VAULT / ENTITIES_FOLDER
    try:
        for fp in d.glob(f"*-{ent}.md"):
            txt = fp.read_text(encoding="utf-8", errors="replace")
            if _read_frontmatter(txt)[0].get("name") == ent:
                return txt
    except OSError:
        pass
    return build_entity_card(ent)    # miss: build_entity_card resolves the type itself


def update_context(project: str, update: str, tags: list, date: str, time_str: str,
                   links: dict[str, list[str]], session_link: str):
    fp = VAULT / "Context" / f"{project}.md"
    fp.parent.mkdir(exist_ok=True)

    if fp.exists():
        existing = fp.read_text(encoding="utf-8", errors="replace")
    else:
        body_tags = render_body_tags(tags, [f"project/{project}", "context"])
        existing = "\n".join([
            fm_block({"project": project, "tags": tags, "type": "context",
                      "date": date}),
            "",
            f"# Context: {project}",
            "",
            "Живой контекст проекта. Обновляется автоматически hook'ом после каждой сессии.",
            "",
            body_tags,
            "",
            "---",
            "",
        ])

    entry = ["", f"## {date} {time_str}", update, "", f"Сессия: [[{session_link}]]"]
    for nt in TYPED_TYPES:
        items = links.get(nt, [])
        if items:
            entry.append(f"{NTYPE_LABEL_RU[nt]}: " + ", ".join(f"[[{x}]]" for x in items))

    write_atomic(fp, existing + "\n".join(entry) + "\n")
    log(f"Context updated: {project}")
    # GPU-free on the live path: bound the file now WITHOUT an LLM call under the
    # vault lock; the readable summary is produced by scheduled maintenance (C4).
    compact_context_if_needed(fp, project, allow_llm=False)


def rebuild_index():
    fp = VAULT / "Index.md"

    ctx_dir = VAULT / "Context"
    projects = []
    if ctx_dir.exists():
        for cf in sorted(ctx_dir.glob("*.md")):
            mtime = datetime.fromtimestamp(cf.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            projects.append((cf.stem, mtime))

    sess_dir = VAULT / "Sessions"
    sessions = []
    if sess_dir.exists():
        files = sorted(sess_dir.glob("*.md"),
                       key=lambda x: x.stat().st_mtime, reverse=True)[:20]
        for sf in files:
            mtime = datetime.fromtimestamp(sf.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            sessions.append((sf.stem, mtime))

    # OKF frontmatter (M-14): the single Index.md is BOTH the human entry point
    # and a valid Open Knowledge Format bundle index. The round-1 code wrote a
    # SEPARATE lowercase `index.md` from interop, which collided with this file on
    # case-insensitive filesystems and clobbered the human index every weekly
    # consolidation (audit H1). One file, both roles, no collision.
    lines = [
        fm_block({"type": "index", "title": "Anamnesis memory store",
                  "description": "Long-term agent memory — typed notes (patterns/"
                                 "mistakes/decisions), per-project cards, sessions."}),
        "",
        "# Claude Memory Vault — Index",
        "",
        "> Точка входа. Claude Code читает ТОЛЬКО этот файл при старте сессии.",
        "> Не сканируй все папки — переходи через wikilinks из этого индекса.",
        "> Open Knowledge Format bundle: markdown + YAML frontmatter, каждая "
        "заметка несёт `type`; навигация через `[[wikilinks]]` и `graph.json`.",
        "",
        "## Структура",
        "",
        "| Папка | Что хранится |",
        "|---|---|",
        "| Patterns/ | Паттерны и подходы которые сработали |",
        "| Mistakes/ | Ошибки, баги, антипаттерны — чего избегать |",
        "| Decisions/ | Архитектурные решения с обоснованием |",
        "| Context/ | Состояние каждого проекта (один файл = один проект) |",
        "| Sessions/ | Автологи сессий (последние 30 дней) |",
        "",
        "## Активные проекты",
        "",
    ]
    if projects:
        lines += ["| Проект | Обновлён |", "|---|---|"]
        lines += [f"| [[{name}]] | {mtime} |" for name, mtime in projects]
    else:
        lines.append("_(пока нет проектов)_")
    lines += ["", "## Последние сессии", ""]
    if sessions:
        lines += [f"- **{mtime}** — [[{name}]]" for name, mtime in sessions]
    else:
        lines.append("_(пока нет сессий)_")
    lines += ["", "#index"]

    write_atomic(fp, "\n".join(lines))
    log("Index rebuilt")


# ── Core pipeline ─────────────────────────────────────────────────────

def process_session(session_id: str, cwd: str, transcript_path: str,
                    trigger: str, processed_db: dict,
                    run_log: list | None = None, agent: str = DEFAULT_AGENT,
                    transcript_text: str | None = None,
                    project_override: str | None = None) -> bool:
    if session_id in processed_db:
        log(f"Skip {session_id[:8]} — already processed at "
            f"{processed_db[session_id].get('processed_at')}")
        return False

    # Project resolution. An explicit override (generic ingestion from any agent)
    # bypasses the cwd-based gate; otherwise the cwd must be a tracked project
    # (under a configured root or a git repo) — audit C2.
    if project_override:
        project_hint = slug_project(project_override)
    elif is_tracked_project(cwd):
        project_hint = derive_project_from_cwd(cwd)
    else:
        log(f"Skip {session_id[:8]} — cwd '{cwd}' is not a tracked project")
        mark_processed(processed_db, session_id, transcript_path)
        return False

    if transcript_text is not None:        # generic ingestion path (raw text)
        parsed = {"body": transcript_text.strip(), "cwd": cwd, "timestamp": None}
    else:
        parsed = read_transcript(transcript_path)
    if not parsed["body"]:
        log(f"Empty transcript for {session_id[:8]} — marked, nothing to extract")
        mark_processed(processed_db, session_id, transcript_path)
        return False

    transcript_full = truncate_smart(
        redact_secrets(f"Working directory: {cwd}\nTrigger: {trigger}\n\n{parsed['body']}"),
        MAX_TRANSCRIPT_CHARS,
    )

    tag_vocab = collect_existing_tags()
    existing = collect_existing_titles(project_hint)

    prompt = EXTRACTION_PROMPT.format(
        transcript=transcript_full,
        project_hint=project_hint,
        tag_vocab=", ".join(tag_vocab) if tag_vocab else "(пусто — выбирай свободно)",
        existing_patterns=", ".join(existing["pattern"]) or "(нет)",
        existing_mistakes=", ".join(existing["mistake"]) or "(нет)",
        existing_decisions=", ".join(existing["decision"]) or "(нет)",
        brain_block=_brain_prompt_block(),     # F1: typed-entity ask, "" unless a brain profile is on
    )
    extraction = generate_json(prompt, project=project_hint)
    if not extraction:
        log(f"Extraction failed for {session_id[:8]} — left for retry")
        return False

    # The session is marked processed at the END, AFTER its notes are durably
    # written (audit C5): a crash mid-write then RETRIES instead of losing the
    # notes. write_typed_note is idempotent per-session (it reuses a note this
    # same session already wrote), so the retry can't spawn -2/-3 duplicates —
    # which is the failure mode the round-1 "mark first" ordering guarded against.

    started = _parse_iso(parsed["timestamp"]) or datetime.now()
    date = started.strftime("%Y-%m-%d")
    time_str = started.strftime("%H:%M")

    project = slug_project(extraction.get("project") or project_hint)
    tags = extraction.get("tags")
    tags = tags if isinstance(tags, list) else []  # qwen3 may return a str (audit F5)
    tags = _norm_tags(tags)                          # canonical vocabulary (audit M5)
    summary = extraction.get("session_summary") or "Сессия завершена"
    if not isinstance(summary, str):
        summary = str(summary)

    # Relevance gate (audit C1): file project knowledge ONLY when the session is
    # genuinely about this project. Off-topic sessions (personal troubleshooting,
    # model switches, empty chats) still get a Session note for the record, but
    # contribute NO typed notes and NO context update — zero contamination.
    relevant = _is_relevant(extraction.get("project_relevant", True))

    sess_stem = session_stem(date, time_str, project, session_id)

    def items_of(nt: str) -> list:
        if not relevant:
            return []
        v = extraction.get(f"{nt}s")
        return v if isinstance(v, list) else []

    def title_of(item) -> str:
        t = item.get("title", "untitled") if isinstance(item, dict) else str(item)
        return _strip_lead_icon(t)

    # dedup sibling stems so a same-title collision can't produce duplicate or
    # missing sibling wikilinks (audit F7)
    seen_sib, all_siblings = set(), []
    for nt in TYPED_TYPES:
        for i in items_of(nt):
            st = typed_stem(date, project, nt, title_of(i))
            if st not in seen_sib:
                seen_sib.add(st)
                all_siblings.append(st)

    links: dict[str, list[str]] = {nt: [] for nt in TYPED_TYPES}
    new_notes = []
    touched_entities: set = set()       # F2: typed entities of notes ACTUALLY written (skips rejects)
    brain = _cfg.brain_enabled()
    for nt in TYPED_TYPES:
        for item in items_of(nt):
            stem = write_typed_note(TYPE_FOLDER[nt], item, project, date, tags, nt,
                                    session_stem_=sess_stem, siblings=all_siblings)
            if not stem:                 # rejected (injection-shaped, M-10) — skip
                continue
            links[nt].append(stem)
            # redact BEFORE the embed path too: write_typed_note redacts what lands in the .md,
            # but these raw fields feed update_embeddings → the embeddings cache (plaintext on
            # disk) and, with a cloud embedder, the provider — the same secret the note path
            # just scrubbed would leak through the side channel (code-review 2026-07, HIGH).
            desc = redact_secrets(item.get("description", "")) if isinstance(item, dict) else ""
            prevention = redact_secrets(item.get("prevention", "")) if isinstance(item, dict) else ""
            conf = item.get("confidence") if isinstance(item, dict) else None
            new_notes.append((stem, nt, project, title_of(item), desc, prevention, conf))
            if brain and isinstance(item, dict):
                touched_entities |= set(_norm_entity_types(item.get("entity_types")))

    sess_link = write_session_note(
        project, date, time_str, summary, cwd, session_id, tags, links, trigger,
        agent=agent,
    )

    cu = extraction.get("context_update")
    if relevant and isinstance(cu, str) and cu.strip() and not _is_noise_update(cu):
        update_context(project, redact_secrets(cu), tags, date, time_str,
                       links, sess_link)

    if new_notes:
        update_embeddings(new_notes)

    # distil the structured project card from this session's writes (audit I-15).
    # Runs whenever the session was project-relevant — independent of whether a
    # context_update fired — so a notes-only session still refreshes the card.
    if relevant:
        refresh_project_card(project)

    # F2 (Brain layer): refresh entity cards for the TYPED entities this session actually wrote
    # (collected in the write loop above, so injection-rejected items are excluded). Only when a
    # brain profile is active (coding-only store unchanged), off the hot path, deterministic.
    if relevant and touched_entities:
        refresh_entity_cards(list(touched_entities))

    # All of this session's notes/session-note/context are now durably written →
    # mark it processed LAST (audit C5). A crash before this point leaves the
    # session unmarked so it retries; the writes above are per-session idempotent,
    # so the retry reuses them instead of duplicating.
    mark_processed(processed_db, session_id, transcript_path)

    # fold this session's writes into the grounding caches so the next session in
    # a multi-session sweep needs no full disk rescan (audit M-k)
    register_written_notes(project, tags, links)

    counts = {nt: len(links[nt]) for nt in TYPED_TYPES}
    log(f"Done {session_id[:8]} | P={counts['pattern']} "
        f"M={counts['mistake']} D={counts['decision']}")
    if run_log is not None:
        run_log.append({
            "session_id": session_id, "project": project, "time": time_str,
            "patterns": counts["pattern"],
            "mistakes": counts["mistake"],
            "decisions": counts["decision"],
        })
    return True


def sweep_unprocessed(processed_db: dict,
                      exclude_session_id: str | None = None,
                      run_log: list | None = None,
                      max_n: int | None = None) -> int:
    """Find unprocessed transcripts and run them through process_session.

    Processes ANY tracked transcript not yet in processed_db, regardless of
    age — the old SWEEP_DAYS hard cutoff silently lost sessions that aged past
    7 days while still on disk (audit F28). `max_n` caps how many get extracted
    per call (Ollama-bound) so SessionStart need not hold the lock for ages.
    """
    if not PROJECTS_ROOT.exists():
        log(f"Projects root not found: {PROJECTS_ROOT}")
        return 0

    candidates: list[tuple[Path, float]] = []
    for jl in PROJECTS_ROOT.rglob("*.jsonl"):   # recursive: don't miss nested (audit LOW)
        sid = jl.stem
        if sid == exclude_session_id or sid in processed_db:
            continue
        try:
            mtime = jl.stat().st_mtime
        except OSError:
            continue
        candidates.append((jl, mtime))

    candidates.sort(key=lambda x: x[1])  # oldest first
    n = attempts = 0
    for jl, _ in candidates:
        sid = jl.stem
        cwd = read_session_meta(str(jl)).get("cwd") or str(jl.parent)
        if not is_tracked_project(cwd):
            mark_processed(processed_db, sid, str(jl))
            continue
        # cap on ATTEMPTS, not successes — a slow/failing backend must not let a
        # backlog hold the vault lock unbounded (audit C3/B16)
        if max_n is not None and attempts >= max_n:
            log(f"Sweep cap reached ({max_n} attempts); leaving the rest for later")
            break
        attempts += 1
        log(f"Sweep: processing leftover {sid[:8]} ({jl.parent.name})")
        try:
            if process_session(sid, cwd, str(jl), "sweep_unprocessed",
                               processed_db, run_log=run_log):
                n += 1
        except Exception as e:
            # a write crash must not abort the sweep or silently lose the session
            # — un-mark so it retries next run instead (audit B18)
            log(f"process_session crashed for {sid[:8]}: {e} — un-marking for retry")
            processed_db.pop(sid, None)
            save_processed(processed_db)
    return n


# ── Status file ───────────────────────────────────────────────────────

def write_status(event: str, trigger: str, sessions_processed: list[dict],
                 swept_count: int, current_session_id: str, degraded: str = ""):
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    total = len(sessions_processed)

    lines = [
        "=== Claude Memory Vault — Status ===",
        f"Last update    : {ts}",
        f"Trigger        : {event or 'manual'} ({trigger})",
        f"Extraction LLM : {llm_backend_desc()}",
        f"LLM this run   : cloud={_LLM_STATS['cloud']}({ACTIVE_CLOUD}) "
        f"ollama={_LLM_STATS['ollama']} failed={_LLM_STATS['fail']}",
        f"Routing        : {local_routing_desc()}",
        f"Vault          : {VAULT}",
        f"Sessions saved : {total} (current run)",
        f"Swept (extra)  : {swept_count}",
        f"Health         : {('DEGRADED — ' + degraded) if degraded else 'OK'}",
        "",
    ]
    if sessions_processed:
        lines.append("Processed in this run:")
        for s in sessions_processed:
            lines.append(
                f"  - [{s['time']}] {s['project']:<28} "
                f"session={s['session_id'][:8]}  "
                f"P={s['patterns']} M={s['mistakes']} D={s['decisions']}"
            )
    else:
        lines.append("Processed in this run: (none)")
    lines.append("")

    history: list[str] = []
    if STATUS_FILE.exists():
        try:
            old = STATUS_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            if "--- History ---" in old:
                idx = old.index("--- History ---")
                history = [l for l in old[idx + 1:] if l.strip()]
        except OSError:
            history = []

    if degraded:
        new_entry = f"[{ts}] {event or 'manual'} — DEGRADED: {degraded}"
    elif sessions_processed:
        ids = ", ".join(f"{s['project']}:{s['session_id'][:8]}" for s in sessions_processed)
        new_entry = f"[{ts}] {event or 'manual'} — {total} session(s): {ids}"
    else:
        new_entry = (f"[{ts}] {event or 'manual'} — no new sessions "
                     f"(current_id={current_session_id[:8]})")

    history = ([new_entry] + history)[:STATUS_HISTORY_LIMIT]

    lines += ["--- History ---", *history, ""]
    write_atomic(STATUS_FILE, "\n".join(lines))
    log(f"Status written: {STATUS_FILE.name}")


# ── Retrieval injection + graph refresh (SessionStart, audit F35/F36/F39) ──

def update_embeddings(new_notes):
    """Embed freshly written typed notes (title + description + prevention) into
    the cache so SessionStart retrieval can rank them semantically (audit F36),
    storing the text too so lexical fallback and fact injection work without a
    re-read (audit C3/H5). Document prefix matches the cache's mode (audit H2)."""
    cache = load_embed_cache()
    if not embed_cache_usable():
        # The embedder changed since these vectors were written — they live in a
        # foreign space, so cosine against them is meaningless. Demote them to
        # text-only (keeps lexical/FTS recall working) rather than mix spaces; a full
        # semantic re-embed is `embed_index.py --rebuild` (W2/provider-switch).
        for e in cache.values():
            if isinstance(e, dict):
                e.pop("vec", None)
        log(f"Embedder changed to {embed_signature()} — demoted stale vectors to "
            "text-only (run embed_index.py --rebuild to re-embed all notes)")
        # the new embedder defines its own prefix policy; reset it BEFORE embedding so
        # doc_embed_kind() (reads meta) and the stamp below match the NEW vectors, not the
        # old provider's flag (audit 2026-06-18 — a stale prefixed flag mis-prefixes q vs doc)
        _meta0 = load_embed_meta()
        _meta0["prefixed"] = (EMBED_PROVIDER == "ollama") and EMBED_USE_PREFIX
        save_embed_meta(_meta0)
    kind = doc_embed_kind()
    added: dict[str, dict] = {}
    for rec in new_notes:
        stem, ntype, project, title, desc, prevention = rec[:6]
        conf = _coerce_confidence(rec[6]) if len(rec) > 6 else None
        vec = embed_text(f"{title}\n{desc}\n{prevention}".strip(), kind=kind, project=project)
        # No embedder (busy GPU / no cloud key) → still store the TEXT so the note is
        # lexically recallable via FTS instead of invisible until something embeds it
        # (#32). embed_index.py upgrades a text-only entry to a vector entry later.
        entry = {"ntype": ntype, "project": project, "title": title,
                 "desc": desc, "prevention": prevention,
                 "recurrence": _embed_recurrence(stem, ntype, cache)}
        if vec:
            entry["vec"] = vec
        if _note_resolved(stem, ntype):
            entry["resolved"] = True           # so the salience de-weight fires (round 3)
        if conf is not None:
            entry["confidence"] = conf                # read back in ranking (H2)
        cache[stem] = entry
        added[stem] = entry
    if added:
        save_embed_cache(cache)
        meta = load_embed_meta()
        meta["model"] = embed_signature()
        meta["prefixed"] = cache_is_prefixed()
        save_embed_meta(meta)
        sync_scale_index(records=added)               # keep SQLite current (C2/C3)
        n_vec = sum(1 for e in added.values() if e.get("vec"))
        if n_vec == len(added):
            log(f"Embedded {n_vec} note(s) into cache")
        else:
            log(f"Embedded {n_vec}/{len(added)} note(s); {len(added) - n_vec} stored "
                "text-only (no embedder) — lexically recallable, run embed_index.py to vectorise")


def _recur_boost(rec: dict) -> float:
    """Ranking bump for a lesson that recurred across sessions (audit H4). LOG
    frequency prior — log(n), not linear (n−1): the recurrence ablation
    (research/ABLATION_RESULTS.md) shows log fuses better (avg recall@1 0.81 vs 0.69)
    because frequency evidence is log-scaled (cf. IDF) and linear (n−1) lets one very
    frequent lesson dominate a cluster regardless of relevance. n≥1 → log(1)=0, so a
    one-off contributes nothing."""
    try:
        n = int(rec.get("recurrence", 1) or 1)
    except (TypeError, ValueError):
        n = 1
    return RETRIEVAL_RECUR_BOOST * math.log(max(1, n))


def _ambiguity(sims_desc) -> float:
    """Relevance ambiguity in [0,1] from DESCENDING similarity scores — the
    ambiguity-adaptive fusion the recurrence ablation identified as the ceiling
    (research/ABLATION_RESULTS.md). ~1 when the top candidates are bunched (no clear
    winner → lean on the recurrence prior); ~0 when one candidate clearly leads
    (→ suppress recurrence so it can't displace a crisp match). Callers multiply the
    recurrence boost by this. Returns 1.0 (full boost = legacy behaviour) when the
    feature is off or there is nothing to compare. Inert when recurrence=1 (boost is
    0 regardless), so it cannot regress pure-relevance retrieval — confirmed on
    LongMemEval. Tune with ANAMNESIS_AMBIGUITY_K; disable with ANAMNESIS_ADAPTIVE_RECUR=0."""
    if not ADAPTIVE_RECUR or len(sims_desc) < 2:
        return 1.0
    margin = max(0.0, sims_desc[0] - sims_desc[1])
    return max(0.0, min(1.0, 1.0 / (1.0 + AMBIGUITY_K * margin)))   # clamp guards a bad (negative) K


def _low_confidence(sims_desc) -> bool:
    """The adaptive confidence/abstention gate (dogfood W1/W3). True when the top
    similarity is no better than the corpus background: it fails the absolute floor, OR
    it doesn't stand RETRIEVAL_CONFIDENT_MARGIN above the per-query MEDIAN. bge-m3 cosines
    bunch near a high background, so the absolute floor alone never fires. `sims_desc` is
    the descending similarity list; a tiny pool (<4) can't estimate a background, so only
    the floor applies there. The canonical gate — memory_search reuses it (DRY)."""
    if not sims_desc:
        return True
    if sims_desc[0] < RETRIEVAL_SIM_FLOOR:
        return True
    if len(sims_desc) < 4:
        return False
    return sims_desc[0] - statistics.median(sims_desc) < RETRIEVAL_CONFIDENT_MARGIN


def _note_age_days(stem: str) -> float:
    parsed = parse_typed_stem(stem)
    if not parsed:
        return 0.0
    try:
        return max(0.0, (datetime.now() - datetime.strptime(parsed["date"], "%Y-%m-%d")).days)
    except (ValueError, KeyError):
        return 0.0


def _coerce_confidence(v) -> float | None:
    """A confidence value clamped to [0,1], or None when absent/unparseable.
    NaN/inf are rejected, not clamped: max(0, min(1, nan)) is 1.0 in CPython, so a
    poisoned `confidence: .nan` would otherwise read as fully trusted (audit A12)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, f)) if math.isfinite(f) else None


def _salience_mult(stem: str, rec: dict) -> float:
    """Gentle multiplicative re-weight: recency decay (M-3, floored so old gold
    isn't buried) × a down-weight for resolved mistakes (no longer active
    warnings) × confidence (H2, low-confidence lessons nudged down, floored).
    Relevance still dominates — this only nudges ties.

    Recurrence SLOWS the decay (effective age = age / (1+log n)): a lesson re-seen
    across sessions stays fresh — a frequency prior on survival. Without it the
    decay buries old-but-recurring lessons (creation-dated, but repeatedly relevant)
    and the tiny additive recurrence tiebreak cannot rescue them (3A bench finding F2)."""
    mult = 1.0
    n = 1
    if isinstance(rec, dict):
        try:
            n = max(1, int(rec.get("recurrence", 1) or 1))
        except (TypeError, ValueError):
            n = 1
    if RETRIEVAL_DECAY_HALFLIFE > 0:
        age = _note_age_days(stem) / (1.0 + math.log(n))
        mult *= max(RETRIEVAL_DECAY_FLOOR, 0.5 ** (age / RETRIEVAL_DECAY_HALFLIFE))
    if isinstance(rec, dict) and rec.get("resolved"):
        mult *= RETRIEVAL_RESOLVED_WEIGHT
    if isinstance(rec, dict):
        c = _coerce_confidence(rec.get("confidence"))
        if c is not None:
            mult *= RETRIEVAL_CONF_FLOOR + (1.0 - RETRIEVAL_CONF_FLOOR) * c
        sal = rec.get("salience")        # Brain F5: gentle centrality boost, inert when unstamped (0)
        if sal and RETRIEVAL_SALIENCE_BOOST > 0:
            mult *= 1.0 + RETRIEVAL_SALIENCE_BOOST * _coerce_salience(sal)   # already clamped to [0,1]
    return mult


# letter-runs ≥3, plus pure-digit runs ≥3 so number queries (RTX 5090, port 8080,
# CVE / error codes, years) are recallable — bare digits were dropped before (round 4)
_TOKEN_RE = re.compile(r"[^\W\d_]{3,}|\d{3,}", re.UNICODE)


def _tokens(s: str) -> set:
    return set(_TOKEN_RE.findall((s or "").lower()))


_PATH_REF_RE = re.compile(r"`([^`\n]+?\.[A-Za-z0-9]{1,8})`")
# bare path-like token: at least one separator AND a file extension. Lets the
# staleness check see paths NOT wrapped in backticks — most notes don't wrap
# them, so the round-1 backtick-only matcher almost never fired (audit M-b).
_BARE_PATH_RE = re.compile(
    r"(?<![\w/\\.])([A-Za-z0-9_.\-]+(?:[/\\][A-Za-z0-9_.\-]+)+\.[A-Za-z0-9]{1,8})")


def _referenced_paths(text: str) -> set:
    """File-path-looking tokens a note references — backtick-quoted OR bare (a
    path with a separator and an extension) — for the fact-vs-code staleness
    check (M-4). Skips URLs and wikilinks (audit M-b)."""
    out = set()
    text = text or ""
    for cand in _PATH_REF_RE.findall(text):
        cand = cand.strip()
        if "/" in cand or "\\" in cand:        # only path-like (has a separator)
            out.add(cand.replace("\\", "/"))
    for cand in _BARE_PATH_RE.findall(text):
        cand = cand.strip().strip(".,;:)(")
        if "://" in cand or cand.startswith(("http", "www.")):
            continue                            # a URL, not a local file
        out.add(cand.replace("\\", "/"))
    return out


def _note_stale(stem: str, ntype: str, project_dir) -> bool:
    """M-4 fact-vs-code validation: True if the note references code paths that no
    longer exist under project_dir (a refactor likely made the lesson stale).
    Conservative — returns False when it references no checkable path, or when at
    least one referenced path still resolves (so it only flags clear misses)."""
    if not project_dir:
        return False
    folder = TYPE_FOLDER.get(ntype)
    if not folder:
        return False
    try:
        text = (VAULT / folder / f"{stem}.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    pd = Path(project_dir)
    checked = False
    for r in _referenced_paths(text):
        checked = True
        if (pd / r).exists():
            return False                       # a referenced path is live → fresh
    return checked                             # referenced paths, none resolved → stale


def _note_links(stem: str, ntype: str) -> list[str]:
    """The `[[wikilinks]]` a note points at (siblings, RESOLVES/SUPERSEDES, auto
    links) — the edges for graph multi-hop expansion (M-6)."""
    folder = TYPE_FOLDER.get(ntype)
    if not folder:
        return []
    try:
        text = (VAULT / folder / f"{stem}.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out, seen = [], set()
    for lnk in re.findall(r"\[\[([^]|#]+)", text):
        lnk = lnk.strip()
        if lnk and lnk != stem and lnk not in seen:
            seen.add(lnk)
            out.append(lnk)
    return out


def _rrf_scores(rankings: list, k0: int = 60, weights: list | None = None) -> dict:
    """Weighted Reciprocal Rank Fusion of ranked id-lists → one score map. Hybrid
    of semantic + lexical is robust (degrades to lexical on a busy GPU); the
    semantic list is weighted higher so a strong embedder leads (audit I-2)."""
    score = {}
    for j, rk in enumerate(rankings):
        w = weights[j] if (weights and j < len(weights)) else 1.0
        for i, sid in enumerate(rk):
            score[sid] = score.get(sid, 0.0) + w / (k0 + i + 1)
    return score


def _token_list(s: str) -> list:
    """Same tokenisation as `_tokens` but keeps term frequencies (a list, not a set) —
    BM25 needs counts. Unicode-aware, so RU/EN both tokenise."""
    return _TOKEN_RE.findall((s or "").lower())


def _bm25_scores(qtokens: set, cands: list, k1: float = 1.5, b: float = 0.75) -> dict:
    """BM25 lexical scores (stem -> score) over the candidate notes — a properly
    IDF-weighted lexical signal, far stronger than raw token-overlap. IDF is computed over
    the candidate set; a note's searchable text is title+desc+prevention+stem (the same
    fields the overlap path scored). Pure stdlib for the in-memory path; at scale the FTS5
    index supplies the equivalent signal."""
    if not qtokens:
        return {}
    docs = {s: _token_list(f"{r.get('title','')} {r.get('desc','')} "
                           f"{r.get('prevention','')} {s}") for s, r in cands}
    nd = len(docs) or 1
    df = {}
    for toks in docs.values():
        for w in set(toks):
            df[w] = df.get(w, 0) + 1
    avgdl = (sum(len(t) for t in docs.values()) / nd) or 1.0
    q = set(qtokens)
    out = {}
    for s, toks in docs.items():
        if not toks:
            continue
        dl = len(toks)
        tf = {}
        for w in toks:
            if w in q:
                tf[w] = tf.get(w, 0) + 1
        sc = 0.0
        for w, f in tf.items():
            idf = math.log(1 + (nd - df.get(w, 0) + 0.5) / (df.get(w, 0) + 0.5))
            sc += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        if sc > 0:
            out[s] = sc
    return out


def _zscore_map(d: dict) -> dict:
    """Z-normalise a score map over its own values (mean 0, sd 1). Empty → empty."""
    if not d:
        return {}
    vals = list(d.values())
    mu = sum(vals) / len(vals)
    sd = (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
    return {kk: (v - mu) / sd for kk, v in d.items()}


def _calibrated_fusion(sem_scores: dict, lex_scores: dict, sem_w: float = None) -> dict:
    """Calibrated score fusion: z-normalise each signal over the candidates and combine the
    MAGNITUDES, instead of discarding them with reciprocal-rank fusion. A candidate absent
    from one signal simply lacks that evidence (a low standin). The combined z is mapped
    through a logistic to a positive (0,1) score so the downstream recurrence/salience tail
    (which expects positive scores) keeps working unchanged. The measured win over RRF
    (research/RETRIEVAL_FUSION.md)."""
    if sem_w is None:
        sem_w = FUSION_SEM_WEIGHT
    zs, zl = _zscore_map(sem_scores), _zscore_map(lex_scores)
    LOW = -3.0                                   # a candidate missing from a signal
    out = {}
    for s in set(zs) | set(zl):
        z = sem_w * zs.get(s, LOW) + 1.0 * zl.get(s, LOW)
        out[s] = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
    return out


def _hit(stem: str, rec: dict) -> dict:
    return {"ntype": rec.get("ntype"),
            "title": _strip_lead_icon(rec.get("title", stem)), "stem": stem,
            "recurrence": rec.get("recurrence")}


def _age_marker(stem: str, recurrence=None) -> str:
    """Compact 'how fresh / how often' marker for an injected fact (M-12), so the
    agent can weigh a stale one-off against a recent recurring lesson."""
    bits = []
    try:
        n = int(recurrence or 1)
    except (TypeError, ValueError):
        n = 1
    if n >= 2:
        bits.append(f"×{n}")
    age = _note_age_days(stem)
    if age >= 90:
        months = int(age // 30)
        bits.append(f"~{months}мес" if months < 12 else f"~{age/365:.0f}г")
    return f"  _({' · '.join(bits)})_" if bits else ""


def _recency_fallback(project: str, k: int) -> list[dict]:
    """Newest typed notes for the project, mistakes first — last resort when no
    embeddings/lexical signal is available."""
    out = []
    for ntype in ("mistake", "pattern", "decision"):
        d = VAULT / TYPE_FOLDER[ntype]
        if not d.exists():
            continue
        hits = []
        for p in d.glob("*.md"):
            parsed = parse_typed_stem(p.stem)
            if parsed and parsed["project"] == project and parsed["ntype"] == ntype:
                hits.append((parsed["date"], p.stem, parsed["slug"]))
        hits.sort(reverse=True)
        for _, stem, slug in hits[:k]:
            out.append({"ntype": ntype, "title": slug.replace("-", " "),
                        "stem": stem})
            if len(out) >= k:
                return out
    return out


def _load_rankers():
    """Lazy-load the opt-in research rankers (W11 plugin boundary). Imported ONLY when
    ANAMNESIS_RANKER=posterior or ANAMNESIS_DIVERGENCE>0, so the default hot path never touches
    this code and the core file carries no maintenance surface for it (mirrors the index_sqlite
    one-way lazy import)."""
    try:
        from . import rankers
    except ImportError:
        import rankers
    return rankers


def retrieve_relevant(project: str, query: str, k: int,
                      embed_timeout: int | None = None,
                      alive_timeout: int = 2, cache: dict | None = None,
                      expand_hops: int | None = None,
                      graph_expand: int = 0,
                      recency_fallback: bool = True) -> list[dict]:
    """Top-k relevant typed notes for the project (audit C3/H4/H5/I-2).

    Hybrid Reciprocal Rank Fusion of two rankings — semantic (embedding cosine,
    computed only if Ollama answers a fast ping, so a busy GPU never stalls
    SessionStart) and lexical (token overlap, always available) — with a gentle
    recurrence tiebreaker. Falls back to whichever signal exists, then to recency.
    Hybrid measured to beat semantic-alone (eval harness). Returns ntype/title/stem.

    embed_timeout/alive_timeout default to the SessionStart budget; the per-prompt
    recall path (I-4) passes tighter values so it never delays an interactive
    prompt — a busy GPU just drops it to the lexical ranking. `cache` lets a caller
    that already loaded the embedding cache reuse it (avoids a re-parse on the hot
    per-prompt path)."""
    if embed_timeout is None:
        embed_timeout = RETRIEVAL_EMBED_TIMEOUT
    cands = _retrieval_candidates(project, cross=False, cache=cache, query=query)
    if not cands:
        return _recency_fallback(project, k) if recency_fallback else []
    rec_of = {s: r for s, r in cands}

    # semantic signal — scores per candidate, only when Ollama answers quickly (no GPU stall)
    sem_scores = {}
    amb = 1.0                           # relevance ambiguity → scales the recurrence prior
    if query and embed_cache_usable() and embedder_available(alive_timeout):
        qvec = embed_text(query, kind=query_embed_kind(), timeout=embed_timeout, project=project)
        if qvec:
            scored = [(cosine(qvec, r.get("vec") or []), s) for s, r in cands]
            sims_desc = sorted((sim for sim, _ in scored), reverse=True)
            amb = _ambiguity(sims_desc)
            # confidence gate (W3): if no candidate stands a margin above the background,
            # the semantic signal is noise — drop it so the hook injects lexical/nothing,
            # not arbitrary neighbours. A confident query keeps the floored semantic scores.
            if not _low_confidence(sims_desc):
                sem_scores = {s: sim for sim, s in scored if sim > RETRIEVAL_SIM_FLOOR}

    # lexical signal — BM25 over the candidate notes (IDF-weighted, no GPU)
    qtok = _tokens(query)
    lex_scores = _bm25_scores(qtok, cands) if qtok else {}

    if not sem_scores and not lex_scores:
        # no semantic (confident) or lexical signal: recent-notes fallback is useful project
        # context at SessionStart, but on the per-prompt path it would inject off-topic noise —
        # so that caller opts out (recency_fallback=False) and we stay silent instead (W3).
        return _recency_fallback(project, k) if recency_fallback else []
    # Calibrated score fusion (default) keeps the signal magnitudes; RRF (legacy / the input
    # shape the posterior ranker expects) discards them. Both degrade to whichever signal exists.
    if RETRIEVAL_FUSION == "rrf" or RANKER == "posterior":
        sem_rank = [s for s, _ in sorted(sem_scores.items(), key=lambda x: -x[1])]
        lex_rank = [s for s, _ in sorted(lex_scores.items(), key=lambda x: -x[1])]
        weighted = ([(sem_rank, RETRIEVAL_SEM_WEIGHT)] if sem_rank else []) + \
                   ([(lex_rank, 1.0)] if lex_rank else [])
        scores = _rrf_scores([r for r, _ in weighted], weights=[w for _, w in weighted])
    else:
        scores = _calibrated_fusion(sem_scores, lex_scores)
    # gentle recurrence tiebreak + time-decay/salience (M-3): nudges ties, never
    # overrides relevance. The recurrence term is scaled by ambiguity (ABLATION):
    # full weight when relevance can't decide, suppressed when one note clearly leads.
    if RANKER == "posterior":
        scores = _load_rankers().posterior_rerank(scores, rec_of)   # explicit log-linear posterior (1A, W11 plugin)
    else:
        # the recurrence tiebreak constant is scaled to the score range in use: tiny for RRF's
        # ~1/60 gaps, larger for calibrated fusion's (0,1) logistic scores.
        recur_boost = (RETRIEVAL_RECUR_RRF_BOOST if RETRIEVAL_FUSION == "rrf"
                       else RETRIEVAL_RECUR_FUSION_BOOST)
        for s in scores:
            try:
                n = int((rec_of.get(s) or {}).get("recurrence", 1) or 1)
            except (TypeError, ValueError):
                n = 1
            scores[s] += recur_boost * math.log(max(1, n)) * amb
            scores[s] *= _salience_mult(s, rec_of.get(s) or {})
    ranked = sorted(scores, key=lambda s: (-scores[s], s))
    if RETRIEVAL_DIVERGENCE > 0 and len(ranked) > 1:     # 2B: diverse/serendipitous recall
        window = ranked[:max(k * 4, k)]                  # MMR the head; tail keeps its order
        ranked = _load_rankers().mmr_rerank(window, scores, rec_of, RETRIEVAL_DIVERGENCE) + ranked[len(window):]
    top = ranked[:k]
    # graph multi-hop expansion (M-6): pull in notes linked from the top hits so
    # a chain A→B→C is reachable; bounded and same-project (linked stems in cache).
    hops = GRAPH_HOPS if expand_hops is None else expand_hops
    if hops > 0 and top:
        present, extra = set(top), []
        for s in top:
            for ln in _note_links(s, (rec_of.get(s) or {}).get("ntype", "")):
                if ln in rec_of and ln not in present:
                    present.add(ln)
                    extra.append(ln)
        top = (top + extra)[:k + k]      # cap total at 2k
    hits = [_hit(s, rec_of[s]) for s in top]
    # Relation-aware expansion (Phase 2b on the hot path): append a TIGHTLY bounded set of
    # lessons reached by the precise hits' typed edges, so a session-start card about a bug
    # also carries its fix. Opt-in (graph_expand>0, SessionStart only) and purely additive:
    # the precise hits keep their order and the budget-aware injector truncates the tail, so
    # graph notes never displace a precise one. relation_expand reads frontmatter (a scan),
    # which is why this is off the per-prompt path.
    if graph_expand > 0 and hits:
        present = {h["stem"] for h in hits}
        for ex in relation_expand(hits, project, max_add=graph_expand):
            if ex["stem"] not in present:
                hits.append({"ntype": ex["ntype"], "title": _strip_lead_icon(ex.get("title", "")),
                             "stem": ex["stem"], "recurrence": ex.get("recurrence"),
                             "via": ex.get("via")})
    return hits


def as_of(project: str, date: str) -> list[dict]:
    """Point-in-time recall (M-5 bi-temporal): every note whose belief interval
    [valid_from, valid_to) contains `date` — what the project's memory held on
    that day, INCLUDING facts later superseded. Scans live + Superseded/. ISO
    date strings compare lexicographically, so no parsing needed."""
    out = []
    for ntype, folder in TYPE_FOLDER.items():
        base = VAULT / folder
        for d in (base, base / "Superseded"):
            if not d.exists():
                continue
            for p in d.glob("*.md"):
                parsed = parse_typed_stem(p.stem)
                if not parsed or parsed["project"] != project:
                    continue
                fm = _read_frontmatter_file(p)   # header only — O(N) scan (audit M-a)
                vf = str(fm.get("valid_from") or parsed["date"])
                vt = str(fm.get("valid_to") or "")
                if vf <= date and (not vt or date < vt):
                    out.append({"stem": p.stem, "ntype": ntype,
                                "title": parsed["slug"].replace("-", " "),
                                "valid_from": vf, "valid_to": vt or None})
    return sorted(out, key=lambda r: (r["ntype"], r["stem"]))


def retrieve_cross_project(project: str, query: str, k: int = CROSS_PROJECT_K,
                           cache: dict | None = None, embed_timeout: int | None = None,
                           alive_timeout: int = 2) -> list[dict]:
    """Lessons from OTHER projects relevant to this one — transferable gotchas
    across a shared stack (audit I-7). Same hybrid ranking as retrieve_relevant
    but inverted project filter and a higher bar (semantic floor + ≥2 shared
    lexical tokens) so cross-project noise stays out. GPU-free under a busy GPU
    (lexical). `cache`/timeouts let the hot per-prompt path reuse a loaded cache
    and stay within a tight budget. Returns hits annotated with their project."""
    if embed_timeout is None:
        embed_timeout = RETRIEVAL_EMBED_TIMEOUT
    cands = _retrieval_candidates(project, cross=True, cache=cache, query=query)
    if not cands:
        return []
    rec_of = {s: r for s, r in cands}
    sem = []
    if query and embed_cache_usable() and embedder_available(alive_timeout):
        qvec = embed_text(query, kind=query_embed_kind(), timeout=embed_timeout, project=project)
        if qvec:
            scored = [(cosine(qvec, r.get("vec") or []), s) for s, r in cands]
            sem = [s for sim, s in sorted(scored, key=lambda x: -x[0])
                   if sim > CROSS_PROJECT_SIM_FLOOR]
    lex = []
    qtok = _tokens(query)
    if qtok:
        scored = []
        for s, r in cands:
            ov = len(qtok & _tokens(f"{r.get('title','')} {r.get('desc','')} "
                                    f"{r.get('prevention','')} {s}"))
            if ov >= 2:                       # cross-project needs a stronger signal
                scored.append((ov, s))
        lex = [s for _, s in sorted(scored, key=lambda x: -x[0])]
    rankings = [r for r in (sem, lex) if r]
    if not rankings:
        return []
    scores = _rrf_scores(rankings)
    ranked = sorted(scores, key=lambda s: (-scores[s], s))
    return [dict(_hit(s, rec_of[s]), project=rec_of[s].get("project"))
            for s in ranked[:k]]


def rerank_notes(query: str, results: list[dict], k: int = RETRIEVAL_TOP_K,
                 project: str | None = None) -> list[dict]:
    """Cloud-as-judge rerank (audit I-3): reorder retrieval candidates by a free
    cloud model's relevance judgement, then take top-k. Deliberately OFF the hot
    injection paths (adds cloud latency); used by on-demand search when precision
    matters more than speed. Falls back to the input order on any failure or empty
    backend, so it never drops or reorders worse than the retriever did. Each
    result dict needs at least `stem`; `title`/`description` improve the judgement."""
    if not results or len(results) <= 1:
        return results[:k]
    items = "\n".join(
        f'{i}. id={r.get("stem")} | {r.get("title", "")} :: '
        f'{(r.get("description") or "")[:160]}'
        for i, r in enumerate(results))
    prompt = (
        "Ранжируй заметки памяти по релевантности к ЗАПРОСУ (самые релевантные "
        "первыми). Используй ТОЛЬКО перечисленные id. Верни ТОЛЬКО JSON вида "
        '{"ranked": ["<id>", "<id>", ...]}.\n\n'
        f"ЗАПРОС: {query}\n\nЗАМЕТКИ:\n{truncate_smart(items, MAX_TRANSCRIPT_CHARS)}")
    try:
        res = generate_json(prompt, project=project)
    except Exception:
        return results[:k]
    order = res.get("ranked") if isinstance(res, dict) else None
    if not isinstance(order, list) or not order:
        return results[:k]
    by_stem = {r.get("stem"): r for r in results}
    ranked = [by_stem[s] for s in order if s in by_stem]
    seen = {r.get("stem") for r in ranked}
    ranked += [r for r in results if r.get("stem") not in seen]  # keep any omitted
    return ranked[:k]


def _context_brief(fp: Path, max_chars: int = 1100) -> str:
    """Compact 'current state' snippet for SessionStart. Prefers the structured
    project card (audit I-15) — highest signal per token; falls back to the
    compressed-state block, else the project description plus the two most recent
    session entries — progressive disclosure so start cost stays small (F35)."""
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    mc = re.search(re.escape(CARD_START) + r"\n(.*?)\n" + re.escape(CARD_END),
                   text, flags=re.S)
    if mc:
        return mc.group(1).strip()[:max_chars]
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    head, entries = _split_context(text)
    state = next((e for e in entries if e.startswith("## Накопленное состояние")), "")
    if state:
        brief = state
    else:
        desc = ""
        for ln in head.split("\n"):
            s = ln.strip()
            if s and not s.startswith(("#", "**", "-", "_", "|", "---")):
                desc = s
                break
        brief = "\n\n".join(([desc] if desc else []) + entries[-2:])
    return brief.strip()[:max_chars]


def _note_snippet(stem: str, ntype: str, max_chars: int = 220) -> str:
    """The actual lesson body (description + 'how to avoid') read straight from
    the note file, so recall injects FACTS, not just a title (audit C3). Works
    for every existing note without re-embedding."""
    folder = TYPE_FOLDER.get(ntype)
    if not folder:
        return ""
    fp = VAULT / folder / f"{stem}.md"
    try:
        lines = fp.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return ""
    resolved = any(ln.strip().startswith("resolved_by:") for ln in lines[:20])   # audit I-18
    _, desc, prevention = _parse_note_body(lines)
    out = desc
    if prevention:
        out = f"{out} → {prevention}" if out else prevention
    if resolved:
        out = ("✅ решено — " + out) if out else "✅ решено"
    return out[:max_chars].rstrip()


def _fact_line(r: dict, stale: bool = False) -> str:
    snip = _note_snippet(r.get("stem", ""), r.get("ntype", ""))
    title = r.get("title", "").strip()
    marker = _age_marker(r.get("stem", ""), r.get("recurrence"))
    flag = " ⚠️_(возможно устарело: файл не найден)_" if stale else ""
    via = f" _(связано: {r['via']})_" if r.get("via") else ""   # graph-expanded lesson (Phase 2b)
    return f"- **{title}**" + (f" — {snip}" if snip else "") + marker + via + flag


def _user_brief(max_chars: int = 320) -> str:
    """Learned cross-project working profile for SessionStart — the 'knows the
    user' layer beyond the hand-written CLAUDE.md (audit I-6; built by
    build_user_model.py → User/profile.md)."""
    try:
        text = (VAULT / "User" / "profile.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    mt = re.search(r"##\s*Кратко[^\n]*\n+(.+?)(?:\n##|\Z)", text, re.S)
    return mt.group(1).strip()[:max_chars] if mt else ""


def emit_session_start_context(cwd: str) -> None:
    """Print a SessionStart additionalContext payload to stdout so the agent
    starts each session already knowing the project's recent state and past
    lessons — active recall, not a passive log (audit F35). Now injects the
    lesson body, not just titles (audit C3). Best-effort; the only stdout the
    hook ever prints."""
    if not INJECT_CONTEXT or not is_tracked_project(cwd):
        return
    project = derive_project_from_cwd(cwd)
    ctx_fp = VAULT / "Context" / f"{project}.md"
    brief = _context_brief(ctx_fp) if ctx_fp.exists() else ""
    # SQLite index → no JSON parse (audit C2); else load once for both rankers
    ensure_scale_index()      # build on first need so the fast path is taken (audit A2)
    rcache = None if scale_index_ready() else load_embed_cache()
    relevant = retrieve_relevant(project, brief or project, RETRIEVAL_TOP_K, cache=rcache,
                                 graph_expand=RELATION_EXPAND)   # SessionStart-only, opt-in
    if not brief and not relevant:
        return  # nothing useful to inject
    # Budget-aware assembly (M-15/M-d): the cap bounds the WHOLE payload, not just
    # the fact list. The round-1 code injected the card (≤1100) and profile (≤320)
    # verbatim and only trimmed facts, so the budget never touched what took the
    # most room (audit M-d). Sections are added by priority — profile → card →
    # mistakes → patterns → cross-project — each trimmed to the remaining budget.
    hdr = f"🧠 Память проекта **{project}** (Obsidian-vault):"
    # The footer is fixed and essential; reserve its room UP FRONT so the budget bounds the WHOLE
    # payload (audit: cross-project + footer used to be appended past the cap, overshooting ~3-17%).
    footer = ["", f"_Поиск по памяти: `python memory_search.py \"<запрос>\" {project}`._",
              f"_Полная история: Context/{project}.md в vault._"]
    footer_len = len("\n".join(footer)) + 1
    parts = [hdr]
    used = [len(hdr) + footer_len]
    margin = 40  # headroom for labels/separators

    def _room() -> int:
        return max(0, INJECT_BUDGET_CHARS - used[0] - margin)

    if INJECT_USER_MODEL:
        ub = _user_brief()
        if ub:
            ub = ub[:_room()]
            if ub:
                seg = ["", "👤 **Профиль (выучено):** " + ub]
                parts += seg
                used[0] += len("\n".join(seg)) + 1
    # dedup facts against the FULL card titles even if the injected brief is
    # trimmed for budget (audit I-15)
    card_titles = {t.strip().lower() for t in re.findall(r"\*\*(.+?)\*\*", brief or "")}
    if brief:
        shown = brief[:_room()]
        if shown:
            seg = ["", "**Текущее состояние:**", shown]
            parts += seg
            used[0] += len("\n".join(seg)) + 1
    relevant = [r for r in relevant if r.get("title", "").strip().lower() not in card_titles]
    mistakes = [r for r in relevant if r["ntype"] == "mistake"]
    others = [r for r in relevant if r["ntype"] != "mistake"]

    proj_dir = _project_dir_for_cwd(cwd) if STALE_CHECK else None   # M-4

    def _add_facts(header_line, items):
        if not items or used[0] >= INJECT_BUDGET_CHARS:
            return
        section, added = ["", header_line], False
        for r in items:
            stale = STALE_CHECK and _note_stale(r.get("stem", ""), r.get("ntype", ""), proj_dir)
            line = _fact_line(r, stale=stale)
            # A precise hit keeps the "show at least one" guarantee (the first item bypasses
            # the cap). A graph-expanded note (carries `via`) is ALWAYS budget-gated, so the
            # opt-in relation expansion can never overshoot the card (audit 2026-06-20).
            if (added or r.get("via")) and used[0] + len(line) > INJECT_BUDGET_CHARS:
                break
            section.append(line)
            used[0] += len(line) + 1
            added = True
        if added:
            parts.extend(section)

    _add_facts("**⚠️ Не повтори эти ошибки:**", mistakes)
    _add_facts("**✅ Рабочие паттерны/решения:**", others)
    if INJECT_CROSS_PROJECT and used[0] < INJECT_BUDGET_CHARS:
        cross = retrieve_cross_project(project, brief or project, cache=rcache)
        if cross:
            xs, added = ["", "**🔗 Похожие уроки из других проектов:**"], False
            for r in cross:
                snip = _note_snippet(r["stem"], r["ntype"])
                line = (f"- [{r.get('project')}] **{r.get('title','').strip()}**"
                        + (f" — {snip}" if snip else ""))
                if added and used[0] + len(line) > INJECT_BUDGET_CHARS:   # budget-aware (audit)
                    break
                xs.append(line)
                used[0] += len(line) + 1
                added = True
            if added:
                parts.extend(xs)
    parts += footer
    payload = {"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "\n".join(parts),
    }}
    # ensure_ascii=True: the hook's stdout is a pipe in cp1251 under Claude Code;
    # emoji/Cyrillic must be \uXXXX-escaped or print() raises UnicodeEncodeError
    # and the injection is silently lost.
    print(json.dumps(payload))


# ── Task-aware recall on UserPromptSubmit (audit I-4) ─────────────────
# The SessionStart payload is built from project STATE — it can't know the task
# yet. This hook fires on each submitted prompt, retrieves by the prompt text,
# and injects targeted lessons. Smart-throttled (substantial prompts, per-session
# dedup, capped) so it stays high-signal and cheap. State lives per session under
# VAULT/.prompt_recall/ (gitignored) because each prompt is a fresh hook process.

_TRIVIAL_PROMPT_RE = re.compile(
    r"^(да|нет|ок|ага|угу|спасибо|спс|ладно|продолжай|продолжи|дальше|готово|стоп|"
    r"хватит|ok|okay|yes|no|yep|nope|thanks|thx|sure|go|go\s+on|continue|next|"
    r"stop|done|y|n|k)[!.…\s]*$", re.IGNORECASE)


def _is_trivial_prompt(prompt: str) -> bool:
    """A prompt with no retrieval signal — affirmations, 'continue', a slash- or
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


def emit_prompt_recall(cwd: str, prompt: str, session_id: str) -> None:
    """UserPromptSubmit injection (audit I-4): retrieve lessons by the actual
    prompt text and inject them so recall is task-aware. Smart-throttled
    (substantial prompts only, per-session dedup, capped per session). Best-effort
    and fast — any error or a busy GPU injects nothing rather than block or break
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
    cross = []
    if INJECT_CROSS_PROJECT:
        cross = [c for c in retrieve_cross_project(
                     project, prompt, cache=cache,
                     embed_timeout=PROMPT_RECALL_EMBED_TIMEOUT,
                     alive_timeout=PROMPT_RECALL_ALIVE_TIMEOUT)
                 if c.get("stem") not in seen]
    if not fresh and not cross:
        return  # nothing new for this prompt → stay silent (self-throttling)

    parts = [f"🧠 Память по запросу (проект **{project}**):"]
    mistakes = [h for h in fresh if h["ntype"] == "mistake"]
    others = [h for h in fresh if h["ntype"] != "mistake"]
    if mistakes:
        parts += ["", "**⚠️ Связанные ошибки:**"] + [_fact_line(h) for h in mistakes]
    if others:
        parts += ["", "**✅ Связанные паттерны/решения:**"] + [_fact_line(h) for h in others]
    if cross:
        parts += ["", "**🔗 Из других проектов:**"]
        for c in cross:
            snip = _note_snippet(c["stem"], c["ntype"])
            parts.append(f"- [{c.get('project')}] **{c.get('title', '').strip()}**"
                         + (f" — {snip}" if snip else ""))

    payload = {"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "\n".join(parts),
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
    the navigation graph never goes stale (audit F39). Silent on any failure —
    memory must never block on graphify."""
    try:
        if not is_tracked_project(cwd):
            return
        proj_dir = _project_dir_for_cwd(cwd)
        script = Path(__file__).with_name("graphify.py")
        if not proj_dir or not proj_dir.exists() or not script.exists():
            return
        import subprocess
        subprocess.run([sys.executable, str(script), str(proj_dir), "--incremental"],
                       timeout=60, capture_output=True)
        log(f"graph.json refreshed for {proj_dir.name}")
    except Exception as e:
        log(f"graphify refresh skipped: {e}")


# Derived / machine-local files kept OUT of the vault's git history (mirror of
# install.py's list). An AUTO-initialised store (the git_autocommit fallback init —
# never touched by install.py) must not commit the embeddings cache, the SQLite index,
# or .logs/ (which can hold third-party error bodies / key fragments). audit 2026-06-18.
_VAULT_GITIGNORE = (
    ".lock", "*.tmp", "*.bak", "__pycache__/", "*.pyc",
    ".prompt_recall/", ".logs/",
    ".embeddings_cache.json", ".embeddings_meta.json",
    ".index.sqlite", ".index.sqlite-wal", ".index.sqlite-shm",
    ".processed_sessions.json",
    "graph.json", "status.txt", "health.txt",
    "eval_results.json", "temporal_graph.json", "contradiction_candidates.json",
    "Index.md", "User/profile.md",
)


def _ensure_vault_gitignore() -> None:
    """Write a .gitignore covering derived/machine-local files when the vault has none —
    so an auto-initialised store never commits caches, the index, or .logs/."""
    gi = VAULT / ".gitignore"
    try:
        if not gi.exists():
            write_atomic(gi, "\n".join(_VAULT_GITIGNORE) + "\n")
    except OSError as e:
        log(f"vault .gitignore write skipped: {e}")


def git_autocommit():
    """Best-effort vault snapshot after each memory update, so a bad write or
    manual slip is always recoverable from git history (audit C1). When a remote
    exists and ANAMNESIS_GIT_PUSH=1, also push for an off-machine copy
    (audit H6). Silent and bounded — memory must never block on git."""
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
            # a fresh box may have no commit identity at all — set a LOCAL fallback
            # only when none resolves, so the user's real global identity is untouched.
            if not _git("config", "user.email").stdout.strip():
                _git("config", "user.email", "anamnesis@localhost")
                _git("config", "user.name", "Anamnesis")
            # an auto-init store never ran install.py → write the ignore list ourselves
            # so caches / .logs / the index don't get committed (and possibly pushed).
            _ensure_vault_gitignore()
        _git("add", "-A")
        _git("commit", "-q", "-m",
             f"auto: memory update {datetime.now():%Y-%m-%d %H:%M}")
        if os.environ.get("ANAMNESIS_GIT_PUSH", "0") == "1":
            has_remote = _git("remote").stdout.strip()
            if has_remote:
                r = _git("push", "--quiet")
                if r.returncode != 0:
                    log("git push failed (off-machine backup not updated) — "
                        "commit is safe locally")
    except Exception as e:
        log(f"git autocommit skipped: {e}")


# ── Entrypoint ────────────────────────────────────────────────────────

# ── Active Memory on the hot path: axis-A guards on PreToolUse ─────────
# The moat, made automatic. Before a code-writing tool runs, check what it is about to write
# against the learned guards. The check is REGEX-ONLY (no LLM, no embedder, no network) and
# SILENT when clear, so it adds 0 context tokens until a guard actually catches a repeat — the
# token-economy invariant holds. Advisory by default (surfaces a warning, never blocks); set
# ANAMNESIS_GUARD_ENFORCE=1 to let a 'blocking'-status guard deny the call. Popperian guards
# self-retire on false positives, so this never boxes the agent in.
GUARDS_HOTPATH = os.environ.get("ANAMNESIS_GUARDS_HOTPATH", "1") != "0"
GUARD_ENFORCE = os.environ.get("ANAMNESIS_GUARD_ENFORCE", "0") != "0"
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
    emit a one-line warning as additionalContext (advisory) — or, under ANAMNESIS_GUARD_ENFORCE,
    deny a blocking-status guard. Read-only, no lock, regex-only: safe to run before every edit."""
    if not GUARDS_HOTPATH or session.get("tool_name", "") not in _GUARDABLE_TOOLS:
        return
    action, path = _action_text_from_tool(session.get("tool_name", ""), session.get("tool_input") or {})
    if not action.strip():
        return
    project = derive_project_from_cwd(cwd) if is_tracked_project(cwd) else None
    try:
        from . import guards as _g
    except ImportError:
        import guards as _g
    try:
        ledger = _g.load_guards()                          # load ONCE; check + fired-bump share it
        hits = _g.check(action, project=project, path=path, tool=session.get("tool_name"),
                        guards=ledger)
    except Exception as e:
        log(f"guard check failed: {e}")
        return
    if not hits:
        return
    try:
        _g.record_fired([h["id"] for h in hits])           # one load, one atomic write — and only
    except Exception:                                      # on the rare hit path; telemetry only,
        pass                                               # never fatal on the hot path
    lines = [("⛔ " if h["status"] == "blocking" else "⚠ ") + h["message"] for h in hits]
    payload = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": "**Anamnesis guard — a past mistake may be repeating:**\n"
                             + "\n".join(f"- {l}" for l in lines),
    }}
    blocking = [h for h in hits if h["status"] == "blocking"]
    if GUARD_ENFORCE and blocking:
        payload["hookSpecificOutput"]["permissionDecision"] = "deny"
        payload["hookSpecificOutput"]["permissionDecisionReason"] = blocking[0]["message"]
    print(json.dumps(payload))                              # ascii-safe: json.dumps escapes non-ASCII


def main():
    try:
        raw = sys.stdin.read()
        session = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        session = {}

    session_id = session.get("session_id", "unknown")
    cwd = session.get("cwd", os.getcwd())
    transcript_path = session.get("transcript_path", "")
    event = session.get("hook_event_name", "")
    trigger = session.get("trigger") or session.get("reason") or event or "manual"
    # Generic-ingestion fields (any agent): agent label, explicit project, and a
    # raw transcript passed inline instead of a Claude Code JSONL file.
    agent = (session.get("agent") or DEFAULT_AGENT).strip() or DEFAULT_AGENT
    project_override = session.get("project") or None
    transcript_text = session.get("transcript_text")
    if transcript_text is None:
        transcript_text = session.get("text")

    log(f"Event={event} | id={session_id[:8]} | agent={agent} | dir={cwd} | "
        f"trigger={trigger} | model={OLLAMA_MODEL}")

    VAULT.mkdir(parents=True, exist_ok=True)

    # SessionStart: inject recall context to stdout FIRST — read-only, no lock
    # needed (atomic writes guarantee reads see a complete file).
    if event == "SessionStart":
        try:
            emit_session_start_context(cwd)
        except Exception as e:
            log(f"additionalContext failed: {e}")

    # UserPromptSubmit: task-aware recall by the prompt text (audit I-4). Read-only,
    # no lock, fast; returns immediately — a prompt event never processes a session.
    if event == "UserPromptSubmit":
        try:
            prompt = (session.get("prompt") or session.get("user_prompt")
                      or session.get("text") or "")
            emit_prompt_recall(cwd, prompt, session_id)
        except Exception as e:
            log(f"prompt recall failed: {e}")
        return

    # PreToolUse: active memory (axis A) — guard the action a code-writing tool is about to
    # apply. Read-only, no lock, regex-only, silent unless a guard fires (0 tokens when clear).
    if event == "PreToolUse":
        try:
            emit_pretooluse_guard(session, cwd)
        except Exception as e:
            log(f"pretooluse guard failed: {e}")
        return

    # The vault lock (single-writer) is held only across extraction + the fast
    # file writes. Recall (SessionStart / UserPromptSubmit) already returned above
    # WITHOUT taking it, and context-summary compaction is off this path entirely
    # (GPU-free here; the LLM summary runs in scheduled maintenance) — so no model
    # call other than the one extraction is ever made under the lock (audit C4).
    lock_timeout = 180 if event in ("SessionEnd", "PreCompact") else 30
    if not acquire_lock(timeout_s=lock_timeout):
        log("Could not acquire vault lock — another process is busy. Aborting.")
        return

    try:
        # Fail loudly if the extraction LLM is unreachable instead of silently
        # dropping the session (audit F29).
        if not llm_available():
            log("No LLM backend available (cloud key unset + Ollama down) — paused")
            write_status(event, trigger, [], 0, session_id,
                         degraded="No LLM backend (cloud key unset + Ollama down)")
            return

        processed_db = load_processed()
        run_log: list[dict] = []

        def finalize(swept_count: int):
            rebuild_index()
            archive_old_sessions()
            archive_old_typed()
            prune_processed_db(processed_db)
            if event in ("SessionEnd", "PreCompact"):
                regen_graph_for_project(cwd)
            if _LLM_STATS["fail"]:
                degraded = f"{_LLM_STATS['fail']} LLM call(s) failed (both backends)"
            elif _OLLAMA_DOWN and not (cloud_key() and ACTIVE_CLOUD != "none"):
                degraded = f"Ollama errors during run ({OLLAMA_URL})"
            else:
                degraded = ""
            write_status(event, trigger, run_log, swept_count, session_id,
                         degraded=degraded)
            git_autocommit()

        if event == "SessionStart":
            # The session that's just starting has an empty transcript — skip it.
            # Sweep older transcripts left by abrupt closes / OS crashes (capped
            # so launch isn't blocked; the scheduled process_now.py and the next
            # SessionEnd/PreCompact pick up whatever remains).
            n = sweep_unprocessed(processed_db, exclude_session_id=session_id,
                                  run_log=run_log, max_n=SESSION_START_SWEEP_CAP)
            if n:
                finalize(n)
                log(f"SessionStart sweep done — recovered {n} session(s)")
            else:
                log("SessionStart sweep — nothing to recover (status not bumped)")
            return

        processed_now = False
        if session_id != "unknown" and (transcript_path or transcript_text is not None):
            try:
                processed_now = process_session(
                    session_id, cwd, transcript_path, trigger, processed_db,
                    run_log=run_log, agent=agent, transcript_text=transcript_text,
                    project_override=project_override)
            except Exception as e:
                log(f"process_session crashed for {session_id[:8]}: {e} — un-marking")
                processed_db.pop(session_id, None)
                save_processed(processed_db)

        swept = sweep_unprocessed(processed_db, exclude_session_id=session_id,
                                  run_log=run_log, max_n=SESSION_END_SWEEP_CAP)
        if processed_now or swept:
            finalize(swept)
        elif _LLM_STATS["fail"] or _OLLAMA_DOWN:
            degraded = (f"{_LLM_STATS['fail']} LLM call(s) failed (both backends)"
                        if _LLM_STATS["fail"]
                        else f"Ollama errors during run ({OLLAMA_URL})")
            write_status(event, trigger, [], 0, session_id, degraded=degraded)
        else:
            log("No work performed — status.txt not updated")
        log(f"Run done | this_session_processed={processed_now} swept={swept}")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
