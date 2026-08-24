"""Central configuration for Nevertwice - de-personalized and cross-platform.

Every path and tunable resolves from an environment variable with a sensible
default, so the same code runs unchanged on any machine. `NEVERTWICE_*` overrides
its default; the legacy `CLAUDE_MEMORY_*` names are still read for back-compat,
so an existing install keeps working after the rename.

The memory store is plain markdown + JSON under git. Obsidian can open it for a
graph/backlinks GUI, but nothing here requires Obsidian - it is fully optional.
"""
import os
from pathlib import Path

# Single source of truth for the runtime version. __init__.py and mcp_server.py both read this,
# and a test asserts it matches pyproject.toml, so the three can never drift again.
VERSION = "2.3.0"

# ── v2.0 rename bridge (Anamnesis → Nevertwice) ───────────────────────
# Mirror every legacy-prefixed variable into its NEVERTWICE_* twin (never overriding an
# explicitly-set new name). This module is imported before any config read, so EVERY
# `os.environ.get("NEVERTWICE_X")` across the codebase transparently honours an existing
# ANAMNESIS_X / CLAUDE_MEMORY_X - one release of painless back-compat, one block of code.
def _bridge_legacy_prefixes() -> None:
    """Mirror ANAMNESIS_*/CLAUDE_MEMORY_* into NEVERTWICE_* (setdefault - an explicit
    new-prefix value always wins). Runs at import AND again after load_dotenv(): a
    legacy-prefixed key that exists only in .env/.secrets.env enters the environment
    after this module was imported, so without the re-run it was never mirrored
    (review 2026-08: the local-only privacy pins and the Ollama model pin in
    .secrets.env were silently ignored by every NEVERTWICE_* reader)."""
    for _k in list(os.environ):
        for _old in ("ANAMNESIS_", "CLAUDE_MEMORY_"):
            if _k.startswith(_old):
                os.environ.setdefault("NEVERTWICE_" + _k[len(_old):], os.environ[_k])


_bridge_legacy_prefixes()


def env(name: str, default: str | None = None) -> str | None:
    """Read NEVERTWICE_<name>, then the legacy prefixes (bridged above), then default."""
    return os.environ.get(f"NEVERTWICE_{name}",
                          os.environ.get(f"CLAUDE_MEMORY_{name}", default))


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(p)))


def _default_vault() -> str:
    """~/.nevertwice - but if only the pre-rename ~/.anamnesis exists, keep using it, so a
    v1 install upgrades in place without a data move (the store is the user's data; we never
    relocate it silently)."""
    new, old = Path.home() / ".nevertwice", Path.home() / ".anamnesis"
    if not new.exists() and old.exists():
        return str(old)
    return str(new)


def load_dotenv() -> None:
    """Load KEY=VALUE pairs from a .env so cloud API keys stay out of git/code.
    Searched (FIXED, trusted locations only): $NEVERTWICE_ENV_FILE, then .env /
    .secrets.env next to the package and at the repo root (where .env.example says to
    put it). Never overrides an already-set var.

    Deliberately NOT the current working directory: the hook and the --dir sweep often
    run with cwd inside an untrusted repo, where a planted `./.env` could inject an
    attacker's API key / relay endpoint and observe what gets sent (audit 2026-06-18).
    Point NEVERTWICE_ENV_FILE at a custom location if you need one elsewhere."""
    here = Path(__file__).resolve().parent
    candidates = []
    custom = os.environ.get("NEVERTWICE_ENV_FILE")
    if custom:
        candidates.append(Path(custom))
    candidates += [here / ".env", here / ".secrets.env",
                   here.parent / ".env", here.parent / ".secrets.env"]
    for fp in candidates:
        try:
            if not fp.is_file():
                continue
            for ln in fp.read_text(encoding="utf-8", errors="replace").splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#") and "=" in ln:
                    k, v = ln.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except OSError:
            pass
    # Legacy-prefixed keys loaded from the files above must reach the NEVERTWICE_*
    # readers too - the import-time bridge ran before these keys existed.
    _bridge_legacy_prefixes()


# The env files load BEFORE the paths below resolve: a vault location that lives only
# in .env/.secrets.env must shape VAULT itself, not just readers that run later. Until
# 2026-08 the call sat in memory_hook AFTER `config.VAULT` was already computed, so
# pinning the store per-machine required editing this file - the exact
# hardcode-in-a-synced-file trap the live install kept having to re-apply.
load_dotenv()

# ── Core paths (cross-platform defaults under the user's home) ────────
# The memory store. Default: ~/.nevertwice (an existing ~/.anamnesis is honoured - see
# _default_vault). Override with NEVERTWICE_HOME (or legacy ANAMNESIS_HOME / *_VAULT).
VAULT = _expand(env("VAULT") or os.environ.get("NEVERTWICE_HOME")
                or _default_vault())

# Where the host agent keeps session transcripts, for the catch-up sweep. Claude
# Code uses ~/.claude/projects. Other agents push via ingest.py / the MCP server
# and don't need this at all.
PROJECTS_ROOT = _expand(os.environ.get("NEVERTWICE_PROJECTS_ROOT")
                        or os.environ.get("CLAUDE_PROJECTS_ROOT")
                        or str(Path.home() / ".claude" / "projects"))


# ── Knowledge profiles: the opt-in "Brain layer" ─────────────────────
# What the user uses Nevertwice for. "coding" (default) = today's lean operational
# memory only - sessions become mistakes/patterns/decisions, nothing else. A brain
# profile ("research"/"general") additionally turns on the PULL-ONLY entity layer
# with the matching ontology (see docs/BRAIN_LAYER_DESIGN.md). The layer is OFF unless
# a brain profile is listed, so a default install is byte-for-byte the operational
# system: no entity extraction, no entity notes, the hot-path injection unchanged.
# Read as functions (not import-time constants) so a value set by load_dotenv() or a
# test's monkeypatch is honoured live.
ONTOLOGY = {
    # The researcher's world (wide): publications, the methods/models/architectures under study,
    # what they are trained and evaluated on, the numbers, the tools, and the people/venues around
    # them. A richer ontology lets the self-wiring graph mirror how a researcher actually thinks.
    "research": ["paper", "method", "architecture", "model", "dataset", "benchmark",
                 "metric", "task", "concept", "experiment", "result", "tool", "venue", "person"],
    # The generalist's world: a lighter personal-knowledge layer.
    "general":  ["topic", "person", "place", "work", "idea"],
}

# Suggested typed edges per profile - guides extraction toward a graph that actually connects the
# entities above (the model may still emit the generic rel set; nothing is allow-listed away). The
# research edges are the scholarly relations a literature/experiment graph is built from.
RELATION_HINTS = {
    "research": ["cites", "builds-on", "extends", "evaluated-on", "trained-on",
                 "reproduces", "refutes", "outperforms", "authored-by", "submitted-to"],
    "general":  ["relates-to", "part-of", "influenced-by"],
}


def profiles() -> set:
    """Active profiles from NEVERTWICE_PROFILE (comma-separated). Defaults to {'coding'}."""
    raw = env("PROFILE", "coding") or "coding"
    got = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return got or {"coding"}


def brain_enabled() -> bool:
    """True iff any knowledge (brain) profile is active - the master gate for the
    entity layer. False for a coding-only install (the default)."""
    return bool(profiles() & set(ONTOLOGY))


def entity_types() -> list:
    """Distinct entity types for the active brain profiles, in declaration order.
    Empty for a coding-only install."""
    active = profiles()
    out: list = []
    # `profiles()` is a set. Iterating it made prompt bytes depend on
    # PYTHONHASHSEED when more than one Brain profile was enabled. Walk the
    # declared ontology instead so extraction is reproducible across processes.
    for p, types in ONTOLOGY.items():
        if p not in active:
            continue
        for t in types:
            if t not in out:
                out.append(t)
    return out


def relation_hints() -> list:
    """Suggested typed-edge names for the active brain profiles (e.g. cites / evaluated-on),
    in declaration order. Empty for a coding-only install - the generic rel set is used then."""
    active = profiles()
    out: list = []
    for p, hints in RELATION_HINTS.items():
        if p not in active:
            continue
        for r in hints:
            if r not in out:
                out.append(r)
    return out
