#!/usr/bin/env python3
"""Backfill / rebuild the embeddings cache for every live typed note in the vault.

Enables semantic + lexical SessionStart retrieval (audit F36/C3/H5). Stores the
note text (title + description + prevention) alongside each vector so lexical
fallback and fact injection work without re-reading files, and records the
prefix mode in .embeddings_meta.json so the query side always matches (audit H2).

    python embed_index.py            # incremental - only notes not yet cached
    python embed_index.py --rebuild  # from scratch (use after changing the model
                                     #   or to upgrade a legacy unprefixed cache)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import memory_hook as m

try:                                      # never crash printing → / Cyrillic on a cp1251 console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def note_fields(p: Path) -> tuple[str, str, str]:
    """Parse (title, description, prevention) from a typed note for embedding - the shared
    body parser (m._parse_note_body), so this can't drift from _note_meta/_note_snippet."""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return p.stem, "", ""
    title, desc, prevention = m._parse_note_body(lines)
    return title or p.stem, desc, prevention


def main():
    if not m.embedder_available():
        if m.EMBED_PROVIDER == "ollama":
            print("[embed_index] Ollama unreachable - aborting", file=sys.stderr)
        else:
            print(f"[embed_index] embedding provider {m.EMBED_PROVIDER!r} has no API key "
                  f"({m._EMBED_KEY_ENV.get(m.EMBED_PROVIDER, '?')}) - set it or "
                  "NEVERTWICE_EMBED_PROVIDER=ollama", file=sys.stderr)
        sys.exit(1)
    rebuild = "--rebuild" in sys.argv
    if not rebuild and not m.embed_cache_usable():
        # provider/model changed since the cache was built → old vectors live in a
        # different space; an incremental run would mix them, so force a full rebuild
        print(f"[embed_index] embedder changed to {m.embed_signature()} - forcing "
              "--rebuild (old vectors are in a different space)", file=sys.stderr)
        rebuild = True
    # single-writer lock around the whole cache read-modify-write, matching
    # consolidate_memory.py --apply (which locks the SAME .embeddings_cache.json). Without it
    # the two racing on one vault corrupted the cache: embed_index finished on a stale pre-merge
    # snapshot and resurrected an archived duplicate + reverted a merged recurrence (critic R3,
    # reproduced). A normal run is incremental (few new notes); a --rebuild is a deliberate,
    # rare operator action.
    if not m.acquire_lock(timeout_s=120):
        print("[embed_index] vault lock busy - another writer is active; aborting", file=sys.stderr)
        sys.exit(2)
    try:
        _run_embed(rebuild)
    finally:
        m.release_lock()


def _checkpoint(cache: dict, rebuild: bool, stage_fp: Path) -> None:
    """Periodic progress save. A partial REBUILD must never touch the live cache:
    the old code's first 25-note checkpoint atomically overwrote both the primary
    and its .bak with a 25-entry cache, so a kill mid-rebuild left no valid
    generation anywhere (review 2026-08 B8). Rebuilds stage to a sibling file and
    swap only on completion; incremental runs still checkpoint in place (their
    cache is a superset of the live one)."""
    if rebuild:
        try:
            m.write_atomic(stage_fp, json.dumps(cache, ensure_ascii=False))
        except OSError:
            pass
    else:
        m.save_embed_cache(cache)


def _run_embed(rebuild: bool):
    cache = {} if rebuild else m.load_embed_cache()
    stage_fp = m.EMBED_CACHE.with_name(m.EMBED_CACHE.name + ".rebuild")
    # On rebuild we (re)embed everything with the configured prefix mode; on an
    # incremental run we MATCH whatever the existing cache already uses so query
    # and document vectors never end up in different spaces (audit H2).
    prefixed = m.EMBED_USE_PREFIX if rebuild else m.cache_is_prefixed()
    kind = "document" if prefixed else None
    added = skipped = failed = 0
    for ntype, folder in m.TYPE_FOLDER.items():
        d = m.VAULT / folder
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md")):          # flat glob skips Superseded/Archive
            stem = p.stem
            # skip only if already VECTORISED; a text-only entry (a no-embedder
            # write, #32) is re-processed so it gets upgraded to a real vector
            if isinstance(cache.get(stem), dict) and cache[stem].get("vec"):
                skipped += 1
                continue
            parsed = m.parse_typed_stem(stem)
            project = parsed["project"] if parsed else "general"
            title, desc, prevention = note_fields(p)
            raw = ""
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
            fm, _ = m._read_frontmatter(raw)
            resolved = bool(fm.get("resolved_by")) \
                or str(fm.get("status", "")).lower() == "resolved"
            conf = m._coerce_confidence(fm.get("confidence"))   # H2: read back in ranking
            # recurrence lives in the NOTE frontmatter (the source of truth, like resolved/
            # confidence) - read it from there, not from the cache, else `--rebuild` (cache={})
            # silently RESETS every recurrence to 1 (W15). Parsed by the ONE shared rule
            # (m._coerce_recurrence) - this block had drifted from the live readers, so a
            # note's count depended on which path last touched it (review 2026-08 R2).
            recur = m._coerce_recurrence(fm.get("recurrence")
                                         or (cache.get(stem) or {}).get("recurrence", 1))
            # project gates the cloud embedder: a local-only project is never shipped
            # to a cloud provider (audit 2026-06-18) - it stays text-only/lexical here
            vec = m.embed_text(f"{title}\n{desc}\n{prevention}".strip(), kind=kind, project=project)
            if vec:
                entry = {"vec": vec, "ntype": ntype, "project": project,
                         "title": title, "desc": desc, "prevention": prevention,
                         "resolved": resolved,   # M-3: down-weight solved mistakes
                         "recurrence": recur}
                if conf is not None:
                    entry["confidence"] = conf
                cache[stem] = entry
                added += 1
                if added % 25 == 0:
                    _checkpoint(cache, rebuild, stage_fp)
                    print(f"  ... {added} embedded", file=sys.stderr)
            else:
                failed += 1
                print(f"  [warn] no embedding for {stem}", file=sys.stderr)
    if rebuild and failed and failed >= added:
        # The embedder died mid-rebuild: swapping in a mostly-empty cache would make
        # every unreached note invisible to retrieval AND rebuild SQLite from the
        # shrunken set (review 2026-08 B8). Keep the previous generation; the staging
        # file is discarded and the operator re-runs after fixing the embedder.
        print(f"[embed_index] ABORT: {failed} failures vs {added} embedded - the "
              f"previous cache generation is left untouched; fix the embedder and "
              f"re-run --rebuild", file=sys.stderr)
        try:
            stage_fp.unlink(missing_ok=True)
        except OSError:
            pass
        sys.exit(3)
    m.save_embed_cache(cache)
    try:
        stage_fp.unlink(missing_ok=True)
    except OSError:
        pass
    meta = m.load_embed_meta()
    meta["model"] = m.embed_signature()
    meta["prefixed"] = prefixed
    m.save_embed_meta(meta)
    # rebuild the SQLite scale index so the retrieval hot path reflects the
    # refreshed cache (audit C2/C3) - derived & rebuildable, failures are ignored
    m.rebuild_scale_index()
    print(f"[embed_index] done: +{added} new, {skipped} cached, {failed} failed, "
          f"{len(cache)} total (prefixed={prefixed})", file=sys.stderr)


if __name__ == "__main__":
    main()
