"""One provenance stamp, shared by every research stand that writes an artifact.

The auditor's ask: an artifact should prove its own commit, not lean on a time window (the same
gap the F6 restamp trap exploits at the cache layer, one level up - `frontier_eval.py`'s own
`check_engine_freshness`). `stamp(payload)` adds one key, `measured_at`, carrying the commit the
run's Python process actually had checked out, the UTC moment it ran, and whether the working
tree was dirty at that moment - a dirty run's numbers are NOT reproducible from the commit alone,
and the artifact says so rather than looking identical to a clean one.

    import sys
    sys.path.insert(0, str(ROOT))                 # research/ itself, wherever ROOT resolves to
    import _provenance as prov
    ...
    out = {...}
    prov.stamp(out)
    Path(args.out).write_text(json.dumps(out, ...), ...)

Standard library only - every research script already pays for a `subprocess` import somewhere
in this tree, and this module adds no new dependency to it.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: The two directories `dirty` watches - the engine and the stands that measure it. A change
#: outside both (docs, .loop/, tests/) does not make a MEASUREMENT non-reproducible from its
#: commit, so it is not asked about here.
WATCHED_DIRS = ("nevertwice/", "research/")
#: C4b (2026-09-23, the auditor's finding on 7f40807): a campaign writes its OWN artifact
#: outputs under research/ as it runs - `is_dirty()`'s plain `git diff --quiet` over
#: WATCHED_DIRS could not tell "the code changed" from "an earlier stand in THIS campaign
#: already wrote its own output file here" - with only research/results/guards_pack.json
#: touched, it returned True, and from the second stand of a campaign on, every artifact would
#: be stamped dirty on otherwise-clean code. These are OUTPUTS, not source that changes what a
#: later run would measure - the same set frozen against measurement (not against writing)
#: elsewhere in this tree.
_EXCLUDED_DIR_PREFIXES = ("research/results/",)
_EXCLUDED_TOP_LEVEL_SUFFIXES = (".svg", ".png")   # research/*.svg, research/*.png - direct
                                                   # children of research/ only, not recursive


#: K5 (2026-09-24, the auditor's finding on 0328eab): a `raw` path is excluded only when it
#: is a DATA file - a future claim's `raw` pointing at a `.py` (a generator script committed as
#: its own "raw" for some other reason) must never let a source-file CHANGE hide behind this
#: exclusion. `raw` paths in this manifest are always result data, never source, but the check
#: does not trust that by convention alone.
_RAW_DATA_SUFFIXES = (".json", ".jsonl")


def _excluded_raw_paths() -> frozenset[str]:
    """Every claim's own `raw` path in research/evidence_manifest.json, restricted to `.json`/
    `.jsonl` data files (`_RAW_DATA_SUFFIXES`) - the committed result file a claim's number was
    read from, which is itself a campaign OUTPUT, not source. Failing to read the manifest
    (missing, unparsable) excludes nothing, the conservative direction: an empty exclusion set
    can only make `is_dirty` MORE likely to (correctly) report dirty, never silently hide a real
    change."""
    try:
        manifest = json.loads((ROOT / "research" / "evidence_manifest.json")
                              .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    return frozenset(c["raw"] for c in manifest.get("claims", [])
                     if c.get("raw") and str(c["raw"]).endswith(_RAW_DATA_SUFFIXES))


def _is_output_path(rel_path: str, excluded_raw: frozenset[str]) -> bool:
    """True when `rel_path` (git's own `/`-separated relative path) is a campaign OUTPUT this
    module excludes from dirtiness, not source."""
    if rel_path.startswith(_EXCLUDED_DIR_PREFIXES):
        return True
    if rel_path in excluded_raw:
        return True
    if rel_path.startswith("research/") and "/" not in rel_path[len("research/"):]:
        if rel_path.endswith(_EXCLUDED_TOP_LEVEL_SUFFIXES):
            return True
    return False


def running_llm(configured: str | None = None) -> str | None:
    """The extraction model the engine in THIS process actually runs, read off the object the
    stands capture through - `nevertwice.api.m` - never off a copy of the name a stand MEANT to set.

    (б), stage D: abstention_ab labelled its artifact from `sys.modules["memory_hook"]`, a second,
    bare engine object imported before the stand pinned the model, which had inherited the owner's
    model from `.secrets.env`; the extraction had run on `api.m` with another model. And four stands
    wrote `sb.LLM`, the name they set in the environment - true only if the engine had not been
    imported earlier in the process (the engine binds the name at import). `configured` is what is
    returned when no engine was loaded at all (a stand that ran only competitor arms).

    The same decision `generate_json` makes (the auditor's G4): with a cloud backend configured
    and its key present, extraction goes to the cloud first, so the label is
    `<ACTIVE_CLOUD>:<that backend's model>` - read off the backend's own module global
    (`DEEPSEEK_MODEL`, ...), which is what `call_<backend>` sends, not the import-time
    `_CLOUD_MODELS` table. Local Ollama is then only the fallback on a failed cloud call; how many
    sessions fell back is `_LLM_STATS`, not this label (PREREG-V3 trap T28)."""
    api = sys.modules.get("nevertwice.api")
    engine = getattr(api, "m", None)
    if engine is None:
        return configured
    cloud = getattr(engine, "ACTIVE_CLOUD", "none")
    cloud_key = getattr(engine, "cloud_key", None)
    if cloud and cloud != "none" and callable(cloud_key) and cloud_key():
        model = getattr(engine, f"{cloud.upper()}_MODEL", None) \
            or getattr(engine, "_CLOUD_MODELS", {}).get(cloud, "?")
        return f"{cloud}:{model}"
    return getattr(engine, "OLLAMA_MODEL", None) or configured


def record_inputs(payload: dict, paths) -> dict:
    """Name every committed file a DERIVED artifact was computed from, with its sha256, as
    `payload["inputs"] = [{"path", "sha256"}]` - and return `payload`.

    (б) b-b, stage D: a derived artifact (a pool over per-run files, a `--with` merge, a tool that
    reads other artifacts) goes stale when an INPUT is re-measured, and its claims' `produced_by`
    closure - the code - cannot see that. `tools/check_freshness.inputs_moved` reads this record:
    a live claim whose artifact's inputs no longer hash the same is stale, and `remeasure
    --restore` refuses it. Paths are repository-relative when they are inside the repository."""
    import hashlib  # noqa: PLC0415
    rec = []
    for p in paths:
        path = Path(p)
        ab = path if path.is_absolute() else ROOT / path
        try:
            rel = ab.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            rel = str(p).replace("\\", "/")
        rec.append({"path": rel, "sha256": hashlib.sha256(ab.read_bytes()).hexdigest() if ab.is_file() else None})
    payload["inputs"] = rec
    return payload


def git_commit() -> str:
    """`HEAD`'s full SHA, or a `?(...)` placeholder naming the failure - never raises, because a
    stamp that crashes a run over a missing `git` binary is a worse failure than an honest '?'."""
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True,
                             text=True, timeout=10, check=True)
        return res.stdout.strip()
    except (OSError, ValueError) as e:
        return f"?({type(e).__name__})"
    except Exception:                                              # noqa: BLE001 - CalledProcessError etc.
        return "?"


def is_dirty(paths: tuple[str, ...] = WATCHED_DIRS) -> bool:
    """True iff any TRACKED file under `paths` differs from `HEAD` - staged or not - EXCLUDING
    campaign OUTPUTS (research/results/, a research/*.svg or *.png figure, every claim's own
    `raw` path in research/evidence_manifest.json - see `_is_output_path`, C4b). An untracked
    new file is deliberately invisible here: `git diff` never sees one, and a stand's own scratch
    output living beside its source (a `*_cache.json`, a `.tmp`) must not read as "dirty" for
    every run that ever writes one. A git failure (no repository, no `git` on PATH) reads as
    dirty: an unprovable "clean" is not a clean, and this is the conservative default the
    freshness checks elsewhere in this tree already use.

    Lists the changed files (`git diff --name-only`) rather than asking `--quiet` for a single
    yes/no, specifically so each one can be checked against the output exclusion before deciding
    - a plain `--quiet` cannot distinguish "only an output changed" from "source changed too"."""
    try:
        res = subprocess.run(["git", "diff", "--name-only", "HEAD", "--", *paths],
                             cwd=str(ROOT), timeout=30, capture_output=True, text=True)
        # `--name-only` (unlike `--quiet`) does not use its exit code to say "a diff exists" -
        # it prints the (possibly empty) list and exits 0 on success regardless of content, so
        # any NON-zero exit here is a genuine error (bad revision, not a repository), not "no
        # diff" - read as dirty either way, the same conservative default as a raised exception.
        if res.returncode != 0:
            return True
    except (OSError, ValueError):
        return True
    changed = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    excluded_raw = _excluded_raw_paths()
    return any(not _is_output_path(f, excluded_raw) for f in changed)


def measured_at() -> dict:
    """`{commit, utc, dirty}` - the shape `stamp` writes under `measured_at`, built separately so
    a caller that wants the dict WITHOUT mutating anything (a check, a comparison) can ask for it
    without owning a payload to stamp."""
    return {"commit": git_commit(), "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "dirty": is_dirty()}


def stamp(payload: dict) -> dict:
    """Add `measured_at: {commit, utc, dirty}` to `payload` and return it (mutated in place, for
    a one-line call at the write site: `Path(out).write_text(json.dumps(stamp(res)))`).

    A stand that already has its OWN `measured_at` (research/ride_along_judge_eval.py,
    research/principle_twins.py) or a differently-named provenance field it writes for a
    different reason (`supersession_bench.py`'s `code_sha`, a hash of the SOURCE FILES for its
    own `--runs N` cross-process identity check, not a git commit) keeps that field - this adds
    or overwrites `measured_at` specifically, the one key name every stand is asked to share, and
    touches nothing else in `payload`.

    Prints a warning to stderr when the tree was dirty: silence would let a dirty artifact look
    exactly like a clean one to anyone who does not think to check the flag."""
    m = measured_at()
    payload["measured_at"] = m
    if m["dirty"]:
        print(f"[_provenance] WARNING: the working tree is dirty (nevertwice/ or research/ "
             f"differs from HEAD {m['commit'][:12]}) - this artifact's numbers are not "
             f"reproducible from the commit alone", file=sys.stderr)
    return payload
