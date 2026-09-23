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
import re
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
        probe.write_text("probe", encoding="utf-8", newline="")
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


def _mtimes(paths) -> list:
    """`st_mtime` for the paths that are still there.

    Every caller globs a folder and then stats what the glob returned. A sweep archiving a note
    between those two steps raised `FileNotFoundError` out of a generator inside `max(...)`, and
    it escaped the check - so a diagnostic run concurrent with the maintenance it exists to
    watch reported nothing at all. A note that moved while we counted is the store working.
    """
    out = []
    for p in paths:
        try:
            out.append(p.stat().st_mtime)
        except OSError:
            continue
    return out


def check_capture_freshness(vault: Path, now: float | None = None) -> dict:
    """Extraction stalling is invisible: nothing errors, the store simply stops growing."""
    now = time.time() if now is None else now
    notes = [p for folder in ("Mistakes", "Patterns", "Decisions", "Sessions")
             for p in (vault / folder).glob("*.md")] if vault.exists() else []
    if not notes:
        return _check("capture_freshness", "the store is still being written to", WARN,
                      "no notes yet - nothing has been captured",
                      "nevertwice-ingest --help  # or start a session with the hooks wired")
    stamps = _mtimes(notes)
    if not stamps:
        return _check("capture_freshness", "the store is still being written to", WARN,
                      f"{len(notes)} note(s) were listed and none could be read",
                      "nevertwice-doctor  # re-run: a sweep may have been moving them")
    newest = max(stamps)
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


def check_twin_calibration() -> dict:
    """Which twin-gate weights this install runs on - the shipped calibration, or the baked
    fallback because the file was refused.

    The twin gate RETIRES live notes, so a calibration silently reverting to the baked bge-m3
    weights changes what the store forgets. The fallback is announced once, at import, into
    the hook's log; an operator updating an install has no reason to be reading log tails at
    that moment. Verdict only: no weight, mean or deviation is ever printed here - the file is
    machine-local data, and a diagnostic that echoes it publishes it.
    """
    title = "the twin gate is running the calibration you shipped"
    try:
        import memory_hook as _m                        # noqa: PLC0415 - CLI-only, not hot
        status = _m.twin_calibration_status()
    except Exception as exc:                            # noqa: BLE001 - any failure is one
        return _check("twin_calibration", title, SKIP,
                      f"could not read the gate's state: {type(exc).__name__}", "")
    if not status["present"]:
        return _check("twin_calibration", title, SKIP,
                      f"no calibration file; the baked {status['space']} weights are in force",
                      "")
    if status["accepted"]:
        return _check("twin_calibration", title, OK,
                      f"{status['path']} accepted, keyed to {status['space']}", "")
    return _check("twin_calibration", title, WARN,
                  f"the calibration file was refused ({status['reason']}) - the gate fell "
                  f"back to the baked {status['space']} weights, so it retires on different "
                  f"evidence than the file you shipped",
                  "retrain per research/TWIN_GATE.md, or remove the file to make the "
                  "fallback deliberate")


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
    stamps = _mtimes(notes)
    if not stamps:
        return _check("index_age", "the search index is current", SKIP,
                      "the notes moved while they were being read", "")
    newest = max(stamps)
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
    # "oldest" was `min(p.name ...)`, the alphabetically first name. The repair points the
    # operator at one file to look at first; it should be the one that has been sitting there
    # longest, not the one whose name happens to sort first.
    oldest = min(orphans, key=lambda q: (_mtimes([q]) or [float("inf")])[0]).name
    return _check("orphaned_temp", "no half-written files are left behind", WARN,
                  f"{len(orphans)} temporary files, oldest {oldest}",
                  "review them, then delete: they are writes that were interrupted, and the "
                  "real note was either written or retried")


#: A bare wiki-link and nothing else: `[[note]]`. It reads as the link text, which is right if
#: the key holds a link and wrong if it holds a list - the doctor cannot know which, so it counts
#: it and the repair names both readings.
_LONE_WIKILINK = re.compile(r"\[\[[^\[\],]+\]\]")


def _frontmatter_lines(path: Path) -> list[str] | None:
    """The frontmatter header's lines, read up to the closing fence and no further.

    The doctor used to read every typed note whole; on a 17k-note store that made this one
    check most of the doctor's runtime while it only ever looks at the header (review
    2026-09-23)."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            first = fh.readline().lstrip("\ufeff")
            if first.strip() != "---":
                return None
            out = []
            for ln in fh:
                if ln.strip() == "---":
                    return out
                out.append(ln.rstrip("\n"))
    except OSError:
        return None
    return None


#: The frontmatter keys the engine reads through `_list_field` (see check_list_fields). A claim
#: about the engine, held by tests/_test_k8_adjudicate.py: each key is fed `[[stem]]` through the
#: reader the engine really uses for it, so the day one of them stops going through `_list_field`,
#: the claim fails instead of the doctor silently exempting a real misread (auditing session, 63eb7b2).
_READ_AS_LIST = ("contested", "disputed", "supersedes", "sources")


_NAME_LIKE = re.compile(r"[^\s,;\[\]()]+")


def _reads_cleanly(items: list[str], raw: object) -> bool:
    """True when the engine's list reader took `raw` apart cleanly: something came out, the
    brackets of the raw value balance, and every entry looks like the name of a note or a session
    - no space, comma, semicolon, bracket or parenthesis. 'a, b', 'a; b' and '(manual)' name
    nothing, and the engine would carry them as if they did (fifth review, 2026-09-23)."""
    text = raw if isinstance(raw, str) else ""
    balanced = text.count("[") == text.count("]")
    return bool(items) and balanced and all(_NAME_LIKE.fullmatch(i) for i in items)


def check_list_fields(vault: Path) -> dict:
    """Frontmatter lists written in a form the engine's parser reads as something else.

    The parser reads JSON-style lists - `tags: ["a", "b"]`, the form the engine writes - and
    reads an unquoted flow list `tags: [a, b]` as ONE string and a block list (`- a` lines under
    the key) as an empty string. The engine's own notes round-trip; a note edited by hand does
    not, and its tags or its `contested` stamp are gone without a word. On the owner's store
    (2026-09-23) there were none in 17 192 notes with Obsidian's property editor switched on, so
    the risk is latent - and this is what makes the first real one a number in a report instead
    of a lost stamp found a month later.

    Compared, not re-implemented: a key whose RAW value is shaped like a list is looked up in
    what the engine's own `_read_frontmatter` returned for it, so the rule for "a list the parser
    reads" is the parser's, asked each time.

    What counts as list-shaped, after the engine review of 2026-09-23:
    - a value that starts with `[` AND ends with `]`, or a block list. A leading bracket alone
      is a scalar - `status: [WIP] reviewing`, `source: [doc](url)` - and the first version
      reported those with a repair that would have turned them into lists;
    - a bare wiki-link `[[note]]` is list-shaped too.

    Which keys are then reported: every key the parser does not return as a list, EXCEPT the
    keys in `_READ_AS_LIST` when the engine's own list reader (`_list_field`) takes the value
    cleanly. That reader's grammar is small and stated in its docstring - a JSON list, one whole
    link, `[a, b]` without links inside, a quoted scalar - and everything else (links in a row,
    links mixed with plain items) comes out as one entry that names nothing. "Cleanly" is
    therefore checked, not assumed: brackets balanced and every entry name-like (no space, comma,
    semicolon, bracket or parenthesis), so those shapes are still reported on these keys too. A
    block list reads as empty on every key and is always reported. The list of keys is a claim
    about the engine, held by tests/_test_k8_adjudicate.py.
    """
    title = "frontmatter lists are in a form the engine reads as lists"
    if not vault.exists():
        return _check("list_fields", title, SKIP, "no store", "")
    try:
        import memory_hook as _m                        # noqa: PLC0415 - CLI-only, not hot
    except Exception as exc:                            # noqa: BLE001 - any failure is one
        return _check("list_fields", title, SKIP,
                      f"could not load the engine's parser: {type(exc).__name__}", "")
    bad: list[str] = []
    links: list[str] = []
    notes = 0
    for folder in _m.TYPE_FOLDER.values():
        base = vault / folder
        if not base.is_dir():
            continue
        for p in base.rglob("*.md"):
            if "Superseded" in p.parts[len(base.parts):]:
                continue                                # retired: nothing reads it any more
            lines = _frontmatter_lines(p)
            if not lines:
                continue
            list_keys, link_keys = [], []
            for i, ln in enumerate(lines):
                if not ln or ln[:1] in (" ", "\t", "-") or ":" not in ln:
                    continue
                key, val = (s.strip() for s in ln.split(":", 1))
                block = (val == "" and i + 1 < len(lines)
                         and lines[i + 1].lstrip().startswith("- "))
                if _LONE_WIKILINK.fullmatch(val):
                    link_keys.append(key)
                elif (val.startswith("[") and val.endswith("]")) or block:
                    list_keys.append(key)
            if not (list_keys or link_keys):
                continue
            fm, _ = _m._read_frontmatter("---\n" + "\n".join(lines) + "\n---\n")
            #: Keys the engine reads through its one list reader (`_list_field`; the two stamps via
            #: `_contested_of`) are asked of THAT reader, not of the parser: since 5961f38 it reads
            #: a link, links in a row and a flow list as intended, so reporting them sent people to
            #: repair what was not broken (auditing session, probe C). Every other key keeps the
            #: parser's answer. `sources` joined the list when its recurrence reader moved onto
            #: `_list_field` (third review, 2026-09-23).
            read_ok = {k for k in list_keys + link_keys
                       if k in _READ_AS_LIST and _reads_cleanly(_m._list_field(fm.get(k)), fm.get(k))}
            misread = [k for k in list_keys if k not in read_ok and not isinstance(fm.get(k), list)]
            bare = [k for k in link_keys if k not in read_ok and not isinstance(fm.get(k), list)]
            if misread or bare:
                notes += 1
                bad += [f"{p.name}: {k}" for k in misread]
                links += [f"{p.name}: {k}" for k in bare]
    if not (bad or links):
        return _check("list_fields", title, OK, "none")
    detail = f"{len(bad) + len(links)} list field(s) in {notes} note(s) are read as text"
    if links:
        detail += f" ({len(links)} of them a bare [[link]])"
    detail += f", e.g. {'; '.join((bad + links)[:3])}"
    return _check("list_fields", title, WARN, detail,
                  'rewrite a list as a JSON list on one line - tags: ["a", "b"] - which is the '
                  "form the engine writes and reads back. A bare [[link]] reads as text either way. "
                  f"The keys nevertwice reads as note lists - {', '.join(_READ_AS_LIST)} - also take "
                  "one whole link or an unquoted flow list of plain names, and are reported only "
                  "in a shape that would lose an entry (links in a row, a block list). Only for "
                  'an Obsidian link property, quote it - related: "[[note]]"')


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
        check_twin_calibration(),
        check_index_age(store, now),
        check_scheduler(store, now),
        check_graph_generator(),
        check_orphaned_temp(store),
        check_list_fields(store),
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
