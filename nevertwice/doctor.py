#!/usr/bin/env python3
"""`nevertwice doctor` - what is wrong with this install, and the safe way to fix it.

Every failure this project has had in production was silent. The graph generator died with a
`NameError` on import and its fire-and-forget wrapper logged "graph.json refreshed" on every
crashed run, for over a month. Extraction stalled behind an unreachable backend while sessions
piled up, and the only symptom was memory that had stopped getting better. An embedding cache
built by one model was queried by another, and retrieval quietly abstained rather than
returning a wrong answer - the correct behaviour, and indistinguishable from an empty store.

A memory system that fails silently is worse than one that fails loudly, because you keep
trusting it. So this module asks every question whose answer would have made those three
visible, and each answer carries a **repair you could run**, printed rather than executed:
this is a diagnostic, and a diagnostic that edits your store is not one.

    nevertwice-doctor              # a human-readable report
    nevertwice-doctor --json       # the same thing, schema-stable, for a script or an agent
    nevertwice-doctor --probe      # additionally reach the extractor and the embedder

`--probe` is opt-in because the other modes touch nothing but the filesystem: no model is
loaded, no endpoint is called, and the default report runs on a machine with no network.

Every check returns the same shape - `id`, `title`, `status`, `detail`, `repair` - so
`--json` stays parseable across versions, and a caller can add a check without the consumer
changing. `status` is one of ok / warn / fail / skip, and only `fail` sets the exit code.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as _cfg

SCHEMA_VERSION = 1

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
STATUSES = (OK, WARN, FAIL, SKIP)
CHECK_KEYS = ("id", "title", "status", "detail", "repair")

# A store whose newest note is older than this has stopped being written to. Not an error -
# a quiet week is a quiet week - but worth saying out loud next to "everything is fine".
STALE_CAPTURE_DAYS = 14
# An index older than the newest note it indexes is stale by definition; this is the slack
# for a note written seconds ago while the index rebuild is still queued.
INDEX_SLACK_S = 300


def _check(cid: str, title: str, status: str, detail: str, repair: str = "") -> dict:
    return {"id": cid, "title": title, "status": status, "detail": detail, "repair": repair}


def _vault(override=None) -> Path:
    return Path(override) if override else Path(_cfg.VAULT)


# ── the checks ────────────────────────────────────────────────────────
# Each one is a plain function of a path so a fixture can drive it, and none of them import
# `memory_hook`: the doctor has to work on an install where importing the engine is exactly
# what is broken.

def check_store_writable(vault: Path) -> dict:
    if not vault.exists():
        return _check("store_writable", "store exists and is writable", FAIL,
                      f"{vault} does not exist",
                      f"mkdir -p {vault}  # or set NEVERTWICE_VAULT to your real store")
    probe = vault / ".nevertwice-doctor-probe"
    try:
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return _check("store_writable", "store exists and is writable", FAIL,
                      f"{vault} is not writable: {exc}",
                      f"check the permissions on {vault}")
    return _check("store_writable", "store exists and is writable", OK, str(vault))


def check_store_schema(vault: Path) -> dict:
    """The store declares which layout it is in, so a future migration knows what it has."""
    marker = vault / ".nevertwice_schema.json"
    if not marker.is_file():
        return _check("store_schema", "store schema version is recorded", WARN,
                      "no .nevertwice_schema.json - the store predates schema stamping",
                      "nevertwice-doctor --json  # capture this report first, then run the "
                      "migration planner when it lands (task D10)")
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _check("store_schema", "store schema version is recorded", FAIL,
                      f"unreadable: {exc}",
                      f"inspect {marker}; it is small enough to fix by hand")
    version = data.get("schema_version")
    if not isinstance(version, int):
        return _check("store_schema", "store schema version is recorded", FAIL,
                      f"schema_version is {version!r}, not an integer",
                      f"set an integer schema_version in {marker}")
    return _check("store_schema", "store schema version is recorded", OK, f"v{version}")


def check_hook_registration(settings: Path) -> dict:
    """Claude Code's native path. Absent is fine - MCP and the watch daemon do not use it."""
    if not settings.is_file():
        return _check("hook_registration", "Claude Code hooks are wired", SKIP,
                      f"{settings} not found - not a Claude Code install, or hooks are not "
                      f"the integration path here",
                      "python install.py  # only if you use Claude Code")
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _check("hook_registration", "Claude Code hooks are wired", FAIL,
                      f"settings.json is unreadable: {exc}",
                      f"restore {settings} from the backup install.py wrote beside it")
    hooks = data.get("hooks") or {}
    wired = sorted(event for event, entries in hooks.items()
                   if "memory_hook" in json.dumps(entries))
    if not wired:
        return _check("hook_registration", "Claude Code hooks are wired", WARN,
                      "settings.json has no memory_hook entry",
                      "python install.py  # idempotent; it backs settings.json up first")
    return _check("hook_registration", "Claude Code hooks are wired", OK,
                  f"{len(wired)} events: {', '.join(wired)}")


def check_capture_freshness(vault: Path, now: float | None = None) -> dict:
    """Extraction stalling is invisible: nothing errors, the store simply stops growing."""
    now = time.time() if now is None else now
    notes = [p for folder in ("Mistakes", "Patterns", "Decisions", "Sessions")
             for p in (vault / folder).glob("*.md")] if vault.exists() else []
    if not notes:
        return _check("capture_freshness", "the store is still being written to", WARN,
                      "no notes yet - nothing has been captured",
                      "nevertwice-ingest --help  # or start a session with the hooks wired")
    newest = max(p.stat().st_mtime for p in notes)
    days = (now - newest) / 86400
    if days > STALE_CAPTURE_DAYS:
        return _check("capture_freshness", "the store is still being written to", WARN,
                      f"newest note is {days:.0f} days old ({len(notes)} notes)",
                      "nevertwice-stats  # then check the extractor and scheduler checks below")
    return _check("capture_freshness", "the store is still being written to", OK,
                  f"{len(notes)} notes, newest {days:.1f} days old")


def check_extractor(probe: bool = False) -> dict:
    """Which backend would run, and - only under --probe - whether it answers."""
    cloud = (_cfg.env("CLOUD", "") or "").strip().lower()
    if cloud in ("none", "off", "0"):
        return _check("extractor", "an extraction backend is selected", OK,
                      "cloud disabled; local Ollama or self-extraction only",
                      "")
    keys = [name for name in ("CEREBRAS_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY",
                              "GEMINI_API_KEY") if os.environ.get(name)]
    if keys:
        return _check("extractor", "an extraction backend is selected", OK,
                      f"cloud key present: {', '.join(keys)}",
                      "")
    if not probe:
        return _check("extractor", "an extraction backend is selected", WARN,
                      "no cloud key set; extraction falls back to local Ollama",
                      "nevertwice-doctor --probe  # to check whether Ollama answers")
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    try:
        import urllib.request                            # noqa: PLC0415 - only under --probe
        with urllib.request.urlopen(f"{host}/api/tags", timeout=4) as resp:
            models = len(json.loads(resp.read()).get("models", []))
        return _check("extractor", "an extraction backend is selected", OK,
                      f"Ollama at {host} answered, {models} models",
                      "")
    except Exception as exc:                             # noqa: BLE001 - any failure is one
        return _check("extractor", "an extraction backend is selected", FAIL,
                      f"no cloud key and Ollama at {host} did not answer: "
                      f"{type(exc).__name__}",
                      "ollama serve  # or set one of CEREBRAS/GROQ/DEEPSEEK/GEMINI_API_KEY. "
                      "Sessions are kept and retried, so nothing is lost meanwhile.")


def check_embedding_space(vault: Path) -> dict:
    """The 2026 failure: a cache built by one model, queried by another, abstaining forever."""
    cache = vault / ".embeddings_cache.json"
    meta = vault / ".embeddings_meta.json"
    if not cache.is_file():
        return _check("embedding_space", "the embedding cache matches the current model", WARN,
                      "no embedding cache - recall falls back to lexical search",
                      "python -m nevertwice.embed_index --rebuild")
    if not meta.is_file():
        return _check("embedding_space", "the embedding cache matches the current model", FAIL,
                      "a cache exists but no .embeddings_meta.json says which model built it",
                      "python -m nevertwice.embed_index --rebuild  # re-stamps the space")
    try:
        recorded = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _check("embedding_space", "the embedding cache matches the current model", FAIL,
                      f"unreadable meta: {exc}",
                      "python -m nevertwice.embed_index --rebuild")
    built = str(recorded.get("model") or recorded.get("space") or "").strip()
    current = str(_cfg.env("EMBED_MODEL", "") or "").strip()
    if not built:
        return _check("embedding_space", "the embedding cache matches the current model", FAIL,
                      "the meta file names no model",
                      "python -m nevertwice.embed_index --rebuild")
    if current and built != current:
        return _check("embedding_space", "the embedding cache matches the current model", FAIL,
                      f"cache was built by {built!r}, NEVERTWICE_EMBED_MODEL is {current!r} - "
                      f"cosines across two spaces are meaningless, so recall abstains and "
                      f"looks like an empty store",
                      f"python -m nevertwice.embed_index --rebuild  # or set "
                      f"NEVERTWICE_EMBED_MODEL={built} to keep the existing cache")
    return _check("embedding_space", "the embedding cache matches the current model", OK,
                  f"built by {built}")


def check_index_age(vault: Path, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    index = vault / ".index.sqlite"
    notes = [p for folder in ("Mistakes", "Patterns", "Decisions", "Sessions")
             for p in (vault / folder).glob("*.md")] if vault.exists() else []
    if not index.is_file():
        return _check("index_age", "the search index is current", WARN,
                      "no .index.sqlite - search falls back to a full scan",
                      "python -m nevertwice.index_sqlite --rebuild")
    if not notes:
        return _check("index_age", "the search index is current", OK, "index present, no notes")
    newest = max(p.stat().st_mtime for p in notes)
    lag = newest - index.stat().st_mtime
    if lag > INDEX_SLACK_S:
        return _check("index_age", "the search index is current", WARN,
                      f"index is {lag / 3600:.1f} h older than the newest note",
                      "python -m nevertwice.index_sqlite --rebuild")
    return _check("index_age", "the search index is current", OK,
                  f"index is current within {INDEX_SLACK_S}s")


def check_graph_generator(package_dir=None) -> dict:
    """The dead-graphify failure, exactly: it died on *import* and the wrapper logged success.

    Imported in a child process so a broken module cannot take the doctor down with it - the
    one diagnostic you run when things are broken must not itself be fragile. `package_dir`
    exists so a test can point it at a deliberately broken copy.
    """
    where = str(Path(package_dir) if package_dir else Path(__file__).resolve().parent)
    code = "import sys; sys.path.insert(0, %r); import graphify; print(graphify.__name__)" % (
        where,)
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return _check("graph_generator", "the graph generator imports", FAIL,
                      f"could not run the import probe: {exc}", "")
    if proc.returncode != 0:
        last = (proc.stderr.strip().splitlines() or ["(no output)"])[-1]
        return _check("graph_generator", "the graph generator imports", FAIL,
                      f"graphify fails on import: {last}",
                      "pip install -e .  # a partial install is the usual cause; the wrapper "
                      "around it logs success even when the run crashed, so this check is "
                      "the only place it shows")
    return _check("graph_generator", "the graph generator imports", OK, "imports cleanly")


def check_orphaned_temp(vault: Path) -> dict:
    """Half-written files from an interrupted write. Harmless individually, a symptom in bulk."""
    if not vault.exists():
        return _check("orphaned_temp", "no half-written files are left behind", SKIP,
                      "no store", "")
    orphans = [p for pattern in ("*.tmp", "*.tmp.*", "*.partial")
               for p in vault.rglob(pattern)]
    if not orphans:
        return _check("orphaned_temp", "no half-written files are left behind", OK, "none")
    return _check("orphaned_temp", "no half-written files are left behind", WARN,
                  f"{len(orphans)} temporary files, oldest "
                  f"{min(p.name for p in orphans)}",
                  "review them, then delete: they are writes that were interrupted, and the "
                  "real note was either written or retried")


def check_package_matches_repo() -> dict:
    """A pip-installed copy and a checkout on the same machine drift, and the hook may run
    either one. Naming both is the whole check."""
    runtime = getattr(_cfg, "VERSION", "unknown")
    module = Path(_cfg.__file__).resolve()
    in_site = "site-packages" in module.parts
    return _check("package_source", "the running package is the one you think", OK,
                  f"version {runtime} from {'the installed package' if in_site else module.parent}",
                  "" if in_site else "pip install -e .  # if you meant to run the installed "
                                     "copy instead of this checkout")


def check_scheduler(vault: Path, now: float | None = None) -> dict:
    """The catch-up sweep runs from Task Scheduler or cron. Its own heartbeat is health.txt."""
    now = time.time() if now is None else now
    health = vault / "health.txt"
    if not health.is_file():
        return _check("scheduler", "the background sweep is running", WARN,
                      "no health.txt - the sweep has never run, or is not scheduled",
                      "python -m nevertwice.health_check  # then schedule it (see "
                      "docs/CONFIG.md) if the output looks right")
    age_h = (now - health.stat().st_mtime) / 3600
    if age_h > 48:
        return _check("scheduler", "the background sweep is running", WARN,
                      f"health.txt is {age_h:.0f} h old - the sweep has stopped",
                      "check the scheduled task, then python -m nevertwice.health_check")
    return _check("scheduler", "the background sweep is running", OK,
                  f"last heartbeat {age_h:.1f} h ago")


# ── the report ────────────────────────────────────────────────────────

def run(vault=None, *, settings=None, probe: bool = False, now: float | None = None) -> dict:
    """Every check, in a fixed order, as a schema-stable dictionary."""
    store = _vault(vault)
    settings_path = Path(settings) if settings else Path.home() / ".claude" / "settings.json"
    checks = [
        check_store_writable(store),
        check_store_schema(store),
        check_hook_registration(settings_path),
        check_capture_freshness(store, now),
        check_extractor(probe),
        check_embedding_space(store),
        check_index_age(store, now),
        check_scheduler(store, now),
        check_graph_generator(),
        check_orphaned_temp(store),
        check_package_matches_repo(),
    ]
    summary = {status: sum(1 for c in checks if c["status"] == status)
               for status in STATUSES}
    return {"schema_version": SCHEMA_VERSION, "vault": str(store), "probed": bool(probe),
            "checks": checks, "summary": summary}


_MARK = {OK: "ok  ", WARN: "warn", FAIL: "FAIL", SKIP: "skip"}


def render(report: dict) -> str:
    lines = [f"nevertwice doctor - {report['vault']}", ""]
    for check in report["checks"]:
        lines.append(f"  [{_MARK[check['status']]}] {check['title']}")
        if check["detail"]:
            lines.append(f"         {check['detail']}")
        if check["repair"] and check["status"] in (WARN, FAIL):
            lines.append(f"         fix: {check['repair']}")
    summary = report["summary"]
    lines += ["", f"  {summary[OK]} ok · {summary[WARN]} warn · {summary[FAIL]} fail · "
                  f"{summary[SKIP]} skipped"]
    return "\n".join(lines)


def main(argv: list | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--help" in argv or "-h" in argv:
        print(__doc__.strip())
        return 0
    report = run(probe="--probe" in argv)
    print(json.dumps(report, ensure_ascii=False, indent=1) if "--json" in argv
          else render(report))
    return 1 if report["summary"][FAIL] else 0


if __name__ == "__main__":
    raise SystemExit(main())
