#!/usr/bin/env python3
"""A5 (Q5, the principle layer): promote a de-identified `principle` that recurs across two or
more DIFFERENT projects into a single note of the synthetic `universal` project - the pool
A4's `retrieve_cross_project(..., mode="universal")` reads from.

Imported ONLY by `nevertwice/consolidate_memory.py` (the sleep-time pass), never by the engine
itself: this module is NOT reached through `_engine.py`'s part list or `_sibling()`, so it joins
`consolidate_memory.py`'s closure (~282 claims) rather than the engine's (~832) - checked with
`python tools/produced_by.py "python nevertwice/consolidate_memory.py"` (must list this file)
and `python tools/produced_by.py "python -m nevertwice.memory_hook"` (must NOT).

Pipeline (`promote`):
  1. walk every live pattern/mistake note that carries a `principle` (decisions never get one -
     A1 scopes the field);
  2. re-scan each principle with `principle_scan`, forbidding the project slug PLUS that
     project's own vocabulary (every entity and tag on its live notes) - a stronger bar than the
     write-time scan, which only forbade the one note's own entities;
  3. embed every surviving principle with the engine's own `embed_text`, per-candidate, under
     THAT candidate's own project identity (C12: `is_local_only` is per-project, and a principle
     embedded under its own project's routing is embedded under the MOST restrictive identity
     that call could possibly need - `is_local_only(UNIVERSAL_PROJECT)` is false by default,
     which is exactly the identity confusion C12 warns against using instead);
  4. cache the vectors in `Universal/principles_cache.json`, stamped with the embedder's
     identity - a stamp mismatch refuses the whole cache rather than mixing vector spaces;
  5. cluster pairwise by cosine >= T_PRINCIPLE, but ONLY across candidates from DIFFERENT
     projects and the SAME note type (a pattern's principle never merges with a mistake's);
  6. a cluster spanning >= 2 distinct projects is written as one `universal` note per cluster -
     description = the medoid sentence, `sources` = every member stem, `recurrence` = the
     number of DISTINCT projects (not the member count - two notes from one project must not
     inflate it); a mistake cluster also mints a global advisory guard;
  7. an existing `universal` note whose live cluster has fallen to one project (or lost its
     principle entirely) is retired to Archive/, mirroring `archive_old_typed`'s own move.

    python -m nevertwice.principles --dry     # print what WOULD be promoted, write nothing
    python -m nevertwice.principles --apply   # write (normally called from consolidate_memory.py)

Standard library + the engine only - no third-party dependency.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from . import memory_hook as m
except ImportError:                 # run as a script, not as a package
    import memory_hook as m

try:
    from . import guards as _guards
except ImportError:
    import guards as _guards

# Guards the whole step (wired into `consolidate_memory.py::_run_consolidation`). Off leaves a
# store's behaviour byte-for-byte what it was before the principle layer existed: no promotion,
# no universal pool, and A4's universal mode stays silent on an empty pool by construction.
PRINCIPLE_PROMOTE_ENABLED = m.os.environ.get("NEVERTWICE_PRINCIPLE_PROMOTE", "1") != "0"

#: PLACEHOLDER - calibrated by research/principle_twins.py (A6), NOT YET MEASURED. A6 sweeps
#: T in [0.75, 0.95] against 40 same-rule / 40 different-rule pairs and picks the lowest T with
#: zero false merges on the negatives; until that script has been run for real (it deliberately
#: has not - see the plan, A6 is "written and NOT run"), this is a conservative guess, not a
#: measurement, and is named as such everywhere it is read.
T_PRINCIPLE = m.env_float("NEVERTWICE_PRINCIPLE_T", 0.90)

MIN_CLUSTER_PROJECTS = 2
PRINCIPLES_CACHE_PATH = "Universal/principles_cache.json"
#: The typed folders a `principle` can live on (A1 scopes the field to pattern/mistake).
_PRINCIPLE_TYPES = ("pattern", "mistake")


def _live_principle_candidates() -> list[dict]:
    """Every live pattern/mistake note (any project) that carries a non-empty `principle`.

    "Live" mirrors `archive_old_typed`'s own scope: the top level of each type folder, not
    `Archive/`, `Superseded/` or `Quarantine/` - a retired or aged-out note's principle must
    not keep contributing to a cluster it no longer represents."""
    out = []
    for ntype in _PRINCIPLE_TYPES:
        folder = m.VAULT / m.TYPE_FOLDER[ntype]
        if not folder.exists():
            continue
        for p in sorted(folder.glob("*.md")):
            parsed = m.parse_typed_stem(p.stem)
            if not parsed:
                continue
            fm = m._read_frontmatter_file(p)
            principle = fm.get("principle")
            if not isinstance(principle, str) or not principle.strip():
                continue
            out.append({"stem": p.stem, "ntype": ntype, "project": parsed["project"],
                       "principle": principle.strip(),
                       "entities": fm.get("entities") if isinstance(fm.get("entities"), list) else []})
    return out


def _project_vocabulary(project: str) -> set[str]:
    """Every entity and tag on `project`'s own live typed notes (all three folders) - the
    project-wide forbidden set A5's re-scan uses, stronger than the write-time scan (which only
    forbade the ONE note's own entities)."""
    vocab: set[str] = set()
    for ntype in m.TYPED_TYPES:
        folder = m.VAULT / m.TYPE_FOLDER[ntype]
        if not folder.exists():
            continue
        for p in folder.glob("*.md"):
            parsed = m.parse_typed_stem(p.stem)
            if not parsed or parsed["project"] != project:
                continue
            fm = m._read_frontmatter_file(p)
            ents = fm.get("entities")
            if isinstance(ents, list):
                vocab.update(str(e) for e in ents if e)
            tags = fm.get("tags")
            if isinstance(tags, list):
                vocab.update(str(t) for t in tags if t)
    return vocab


def _rescan(candidates: list[dict]) -> list[dict]:
    """Re-run `principle_scan` per candidate against that candidate's project vocabulary - a
    second, stronger de-identification pass at promotion time (the plan's own description of
    A5). A candidate the scanner now rejects is dropped, never silently kept."""
    vocab_cache: dict[str, set[str]] = {}
    kept = []
    for c in candidates:
        proj = c["project"]
        if proj not in vocab_cache:
            vocab_cache[proj] = _project_vocabulary(proj)
        forbidden = {proj} | vocab_cache[proj] | set(c.get("entities") or ())
        cleaned = m.principle_scan(c["principle"], forbidden)
        if not cleaned:
            continue
        kept.append({**c, "principle": cleaned})
    return kept


def _cache_path() -> Path:
    return m.VAULT / PRINCIPLES_CACHE_PATH


def _load_cache() -> dict:
    """{} on any read failure or a stamp mismatch - a cache in a foreign embedding space would
    make the cosine clustering below meaningless, the same reasoning `embed_cache_usable()`
    applies to the main embed cache."""
    p = _cache_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    if raw.get("model") != m.embed_signature():
        return {}
    vectors = raw.get("vectors")
    return vectors if isinstance(vectors, dict) else {}


def _save_cache(vectors: dict) -> None:
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"model": m.embed_signature(), "vectors": vectors}
    m.write_atomic(p, json.dumps(payload, ensure_ascii=False, indent=1))


def _embed(candidates: list[dict]) -> dict[str, list]:
    """stem -> vector, from the cache where the stamp still matches, embedded fresh otherwise.
    C12: each candidate is embedded under ITS OWN project's identity (`project=c["project"]`),
    so `embed_text`'s existing local-only routing applies per note - never `UNIVERSAL_PROJECT`,
    whose own `is_local_only` is False by default and would silently route a note that should
    have stayed local out to a configured cloud embedder."""
    cache = _load_cache()
    kind = m.doc_embed_kind() if hasattr(m, "doc_embed_kind") else None
    out: dict[str, list] = {}
    changed = False
    for c in candidates:
        vec = cache.get(c["stem"])
        if not (isinstance(vec, list) and vec):
            vec = m.embed_text(c["principle"], kind=kind, project=c["project"])
            if vec:
                cache[c["stem"]] = vec
                changed = True
        if vec:
            out[c["stem"]] = vec
    if changed:
        _save_cache(cache)
    return out


def _cluster(candidates: list[dict], vecs: dict[str, list]) -> list[list[dict]]:
    """Connected components under pairwise cosine >= T_PRINCIPLE, restricted to pairs from
    DIFFERENT projects and the SAME note type - union-find over candidates that HAVE a vector."""
    have = [c for c in candidates if c["stem"] in vecs]
    idx = {c["stem"]: i for i, c in enumerate(have)}
    parent = list(range(len(have)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(len(have)):
        for j in range(i + 1, len(have)):
            a, b = have[i], have[j]
            if a["project"] == b["project"] or a["ntype"] != b["ntype"]:
                continue
            sim = m.cosine(vecs[a["stem"]], vecs[b["stem"]])
            if sim >= T_PRINCIPLE:
                union(idx[a["stem"]], idx[b["stem"]])

    groups: dict[int, list[dict]] = {}
    for i, c in enumerate(have):
        groups.setdefault(find(i), []).append(c)
    return [g for g in groups.values()
           if len({m2["project"] for m2 in g}) >= MIN_CLUSTER_PROJECTS]


def _medoid(cluster: list[dict], vecs: dict[str, list]) -> dict:
    """The member whose average cosine to every OTHER member is highest - the sentence that
    best represents the cluster, rather than an arbitrary pick (first/newest)."""
    if len(cluster) == 1:
        return cluster[0]
    best, best_score = cluster[0], -2.0
    for cand in cluster:
        vc = vecs[cand["stem"]]
        others = [m2 for m2 in cluster if m2["stem"] != cand["stem"]]
        score = sum(m.cosine(vc, vecs[m2["stem"]]) for m2 in others) / len(others)
        if score > best_score:
            best, best_score = cand, score
    return best


def _find_existing_universal(ntype: str, member_stems: set[str]) -> Path | None:
    """An existing LIVE `universal` note of this ntype whose `sources` already shares at least
    one of this cluster's current members - "the same cluster, seen again" (the plan's
    "refresh in place"). Reusing its exact on-disk title lets `write_typed_note`'s own same-
    slug reconcile do the refresh (supersede the old dated file, carry the identity forward) -
    one write path, not a second one invented for this module."""
    folder = m.VAULT / m.TYPE_FOLDER[ntype]
    if not folder.exists():
        return None
    for p in sorted(folder.glob("*.md")):
        parsed = m.parse_typed_stem(p.stem)
        if not parsed or parsed["project"] != m.UNIVERSAL_PROJECT:
            continue
        fm = m._read_frontmatter_file(p)
        srcs = set(fm.get("sources") or ())
        if srcs & member_stems:
            return p
    return None


def _promote_cluster(cluster: list[dict], vecs: dict[str, list], apply: bool) -> dict:
    ntype = cluster[0]["ntype"]
    member_stems = {c["stem"] for c in cluster}
    projects = sorted({c["project"] for c in cluster})
    medoid = _medoid(cluster, vecs)

    existing = _find_existing_universal(ntype, member_stems)
    if existing is not None:
        try:
            _, lines_title, _ = m._parse_note_body(
                existing.read_text(encoding="utf-8", errors="replace").split("\n"))
        except OSError:
            lines_title = ""
        title = lines_title or medoid["principle"][:60]
        action = "refreshed"
    else:
        title = medoid["principle"]
        action = "promoted"

    if not apply:
        return {"action": action, "ntype": ntype, "title": title[:60],
               "projects": projects, "members": sorted(member_stems)}

    date = m.datetime.now().strftime("%Y-%m-%d")
    folder_name = m.TYPE_FOLDER[ntype]
    item = {"title": title, "description": medoid["principle"]}
    stem = m.write_typed_note(folder_name, item, m.UNIVERSAL_PROJECT, date, [], ntype)
    if not stem:
        return {"action": "refused", "ntype": ntype, "title": title[:60],
               "projects": projects, "members": sorted(member_stems)}

    # The write path's own recurrence/sources math is session-provenance arithmetic (+1 per
    # anonymous re-encounter) - not what a cluster recomputed from scratch every run needs.
    # Overwrite both to the numbers THIS run actually found, the same stamp-in-place mechanism
    # consolidation already uses elsewhere (`_stamp_frontmatter` + `write_atomic`).
    fp = m.VAULT / folder_name / f"{stem}.md"
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
        sources = sorted(member_stems)[-m.RECUR_SOURCES_CAP:]
        m.write_atomic(fp, m._stamp_frontmatter(
            text, {"recurrence": len(projects), "sources": sources}))
    except OSError:
        pass

    if ntype == "mistake":
        _mint_global_guard(stem, title, medoid["principle"])

    return {"action": action, "ntype": ntype, "title": title[:60], "stem": stem,
           "projects": projects, "members": sorted(member_stems)}


def _mint_global_guard(stem: str, title: str, principle: str) -> None:
    """A global (project=None) advisory guard from a promoted mistake-cluster - a de-identified
    lesson corroborated by >=2 projects is exactly the corroboration bar `guards.py` already
    asks a single project's mistakes to clear before minting one. Best-effort, never blocking:
    a lock timeout here must not fail the promotion that already succeeded."""
    note = {"title": title, "desc": principle, "prevention": principle,
           "stem": stem, "project": None}
    try:
        guard = _guards.propose_from_mistake(note)
    except Exception as e:                      # noqa: BLE001 - a guard is an enhancement, never load-bearing
        m.log(f"principles: guard proposal failed for {stem}: {e}")
        return
    if guard is None:
        return
    try:
        _guards.persist_under_lock(lambda fresh: _guards.register(fresh, guard),
                                   timeout_s=30, required=False)
    except Exception as e:                       # noqa: BLE001 - never blocking (LedgerBusy included)
        m.log(f"principles: guard persist skipped for {stem}: {e}")


def _retire_dropped(cluster_member_stems: set, apply: bool) -> list[str]:
    """A live `universal` note whose `sources` no longer intersects ANY current candidate's
    stem at all - its contributing notes were retired, archived, or lost their principle - is
    archived exactly like `archive_old_typed` archives a note that aged out: moved to
    `<folder>/Archive/`, dropped from the embed cache so it stops surfacing in recall."""
    retired = []
    for ntype in _PRINCIPLE_TYPES:
        folder = m.VAULT / m.TYPE_FOLDER[ntype]
        if not folder.exists():
            continue
        for p in sorted(folder.glob("*.md")):
            parsed = m.parse_typed_stem(p.stem)
            if not parsed or parsed["project"] != m.UNIVERSAL_PROJECT:
                continue
            fm = m._read_frontmatter_file(p)
            srcs = set(fm.get("sources") or ())
            if srcs and (srcs & cluster_member_stems):
                continue          # still represented by at least one live current source
            retired.append(p.stem)
            if apply:
                arch = folder / "Archive"
                arch.mkdir(exist_ok=True)
                try:
                    m.os.replace(p, m._archive_dest(arch, p.name))
                except OSError as e:
                    m.log(f"principles: retire failed for {p.name}: {e}")
                    continue
                cache = m.load_embed_cache()
                if p.stem in cache:
                    cache.pop(p.stem, None)
                    m.save_embed_cache(cache)
    return retired


def promote(apply: bool = False) -> dict:
    """Run the whole A5 pipeline once. `apply=False` (the default, and `--dry`) computes and
    reports everything but writes nothing - the same dry-run discipline
    `consolidate_memory.py` itself uses."""
    candidates = _rescan(_live_principle_candidates())
    vecs = _embed(candidates) if candidates else {}
    clusters = _cluster(candidates, vecs)
    results = [_promote_cluster(c, vecs, apply) for c in clusters]
    all_current_members: set = set()
    for c in clusters:
        all_current_members.update(m2["stem"] for m2 in c)
    retired = _retire_dropped(all_current_members, apply)
    return {
        "candidates": len(candidates),
        "clusters": len(clusters),
        "promoted": sum(1 for r in results if r["action"] == "promoted"),
        "refreshed": sum(1 for r in results if r["action"] == "refreshed"),
        "refused": sum(1 for r in results if r["action"] == "refused"),
        "retired": len(retired),
        "results": results,
        "retired_stems": retired,
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    apply = "--apply" in argv
    summary = promote(apply=apply)
    verb = "would promote" if not apply else "promoted"
    print(f"[principles] {summary['candidates']} candidate(s), {summary['clusters']} cluster(s) "
         f"- {verb} {summary['promoted']}, refreshed {summary['refreshed']}, "
         f"refused {summary['refused']}, retired {summary['retired']}")
    for r in summary["results"]:
        print(f"  {r['action']:9} [{r['ntype']}] {r['title']!r} <- {', '.join(r['projects'])}")
    for stem in summary["retired_stems"]:
        print(f"  retired   {stem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
