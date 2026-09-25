# -- engine part 4 of 8: note writers, near-duplicate detection, supersession, recurrence, literal-fact preservation --
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
# Lines 3485-4103 of the pre-split `_engine.py`, whose body these parts reproduce byte for byte
# (sha256 bddf5d32a8883f0fdcfbd420de58e66fc9151be1ef05c696dedb44eefd0ac40f). Order is
# load-bearing: module-level code below runs after every earlier part and before every later
# one. `tests/_engine_source.py` reconstructs the whole body from these files.
#<<<ENGINE-PART-BODY>>>
# ── Note writers (Zettelkasten edition) ───────────────────────────────
# Each note links to:
#   - parent project   (Context/<project>.md)
#   - source session   (Sessions/<session>.md)
#   - sibling notes    (other extractions from the same session)

NTYPE_LABEL = {"pattern": "Patterns", "mistake": "Mistakes", "decision": "Decisions"}
# legacy labels written by pre-2.2.1 builds - still recognized when compacting old entries
_NTYPE_LABEL_LEGACY = ("Паттерны", "Ошибки", "Решения")


def _unique_path(folder: Path, base_stem: str) -> Path:
    """Collision-free .md path: appends -2, -3, … and finally a 6-char
    fingerprint when all numeric suffixes are taken, so we never silently
    overwrite an existing note.

    A stem is taken when it exists in the live folder **or in `Superseded/`**. Checking
    only the live folder let a retired stem be re-minted live, so the vault asserted both
    "retired, use the replacement" and "current" for one name - five basenames collided
    that way in the 2026-09 review, and every consumer keyed on stem (recall's Superseded
    filter, the embedding index, memory_search, Obsidian's own link resolution) then
    picked one arbitrarily. That defeats supersession, which is the one mechanism this
    store has that an ADD-only competitor does not.

    `Archive/` is deliberately NOT included: an archived note is old, not retracted, and
    a new note legitimately reuses its name.
    """
    retired = folder / "Superseded"
    for n in range(1, 10):
        suffix = "" if n == 1 else f"-{n}"
        name = f"{base_stem}{suffix}.md"
        fp = folder / name
        if not fp.exists() and not (retired / name).exists():
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
    leaving the body untouched. Shared by supersession and consolidation.

    F11 (xhigh review): a BOM-saved note (readers strip it) used to fail the plain `---` check
    below, so every stamp writer silently no-oped on it - `_mark_contested`/`_set_contested`
    rewrote identical bytes and reported success, `supersede_note`'s own stamp no-oped too. The
    BOM is recognised, stripped before processing and re-emitted on the result."""
    bom = "﻿" if text.startswith("﻿") else ""
    if bom:
        text = text[1:]
    if not text.startswith("---"):
        return bom + text
    end = text.find("\n---", 3)
    if end == -1:
        return bom + text
    body = text[end:]
    pending = dict(fields)
    out = []
    lines = text[:end].split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        i += 1
        # TOP-LEVEL only, as the docstring says. Matching an indented line too meant a stamp of
        # `recurrence` rewrote `  recurrence: method` INSIDE an `entity_types:` block - losing the
        # nested value, and leaving the real top-level key untouched, so the write silently did
        # nothing it was asked to do and one thing it was not (T1).
        indented = ln[:1] in (" ", "\t")
        key = ln.split(":", 1)[0].strip() if ":" in ln and not indented else ""
        if key not in pending:
            out.append(ln)
            continue
        out.append(f"{key}: {_yaml_scalar(pending.pop(key))}")
        # A block-style value lives on the lines UNDER its key - what Obsidian writes when a
        # human edits a note's properties, and what an import can carry in. Replacing only the
        # key line left `  - alpha` orphaned under a key that no longer described it, and the
        # result was YAML no parser accepts: a strict reader rejects the whole block, which costs
        # the note every other field, not just the list.
        while i < len(lines) and (lines[i][:1] in (" ", "\t")
                                  or lines[i].lstrip().startswith("- ")):
            i += 1
    for k, v in pending.items():
        out.append(f"{k}: {_yaml_scalar(v)}")
    return bom + "\n".join(out) + body


_NDUP_MEMO: list = [None, None]        # [cache-file mtime, parsed cache] - reload on change
# Notes written by THIS process whose vectors are not in the on-disk cache yet -
# update_embeddings refreshes the cache once AFTER the whole write loop, so without
# this registry the gate was blind to a twin written seconds earlier in the SAME
# extraction (review 2026-08: one LLM output emitting two titles for one lesson is
# the gate's most common trigger). Entries carry the query vec the gate already
# computed, so scoring them costs no extra embed call; entries visible in a newer
# cache generation are pruned on reload.
_NDUP_PENDING: list[dict] = []
_NDUP_LAST_VEC: list = [None]          # vec of the most recent gate query (for registration)


def _ndup_register(stem: str, project: str, ntype: str, title: str,
                   desc: str, prevention: str, entities) -> None:
    """Record a just-written live note for same-process near-dup gating."""
    vec = _NDUP_LAST_VEC[0]
    _NDUP_LAST_VEC[0] = None
    if vec:
        _NDUP_PENDING[:] = [d for d in _NDUP_PENDING if d["stem"] != stem]
        _NDUP_PENDING.append({"stem": stem, "project": project, "ntype": ntype,
                              "title": title, "desc": desc, "prevention": prevention,
                              "entities": list(entities or ()), "vec": vec})


def _near_duplicate_paths(folder_path: Path, project: str, ntype: str,
                          title: str, desc: str, prevention: str,
                          exclude: set, entities=None) -> list[Path]:
    """Live sibling notes (same project + type) that restate the lesson about to be
    written - the twins the exact-slug reconcile cannot see (review 2026-08: three live
    patterns for one lesson, twin mistakes with contradictory resolutions).

    Two-stage in the default "twin" mode: a cheap cosine PREFILTER over the cache, then
    the learned twin-classifier (_twin_probability) on the survivors - candidate entities
    are read from frontmatter only for prefilter-passers, so the extra IO is a handful of
    reads per genuinely-suspicious note. "cosine" mode restores the plain calibrated
    threshold (the kill-switch). Embeds with the SAME text shape and document prefix as
    update_embeddings, so the cosine is computed in the cache's space. Best-effort and
    fail-open: no embedder, a changed embedding space, or an unreadable cache returns []
    and the write proceeds exactly as before the gate existed."""
    _NDUP_LAST_VEC[0] = None            # never let a previous note's vec leak into registration
    if WRITE_DEDUP_SIM <= 0:
        return []
    try:
        if not embed_cache_usable():
            return []
        vec = embed_text(f"{title}\n{desc}\n{prevention}".strip(),
                         kind=doc_embed_kind(), project=project)
        if not vec:
            return []
        # B3: keyed on the snapshot AND its journal - the snapshot's mtime alone does not move
        # when a capture appends, and this memo would go on answering from before the append.
        mtime = _embed_cache_sig()
        if mtime is None:
            return []
        if _NDUP_MEMO[0] != mtime:              # one parse per cache generation, not per note
            _NDUP_MEMO[0] = mtime
            _NDUP_MEMO[1] = load_embed_cache()
            if _NDUP_PENDING:                   # entries now visible in the cache are done
                _NDUP_PENDING[:] = [d for d in _NDUP_PENDING
                                    if d["stem"] not in _NDUP_MEMO[1]]
        _NDUP_LAST_VEC[0] = vec                 # for _ndup_register after a successful write
        twin_mode = WRITE_DEDUP_MODE != "cosine" and _twin_space_ok()
        floor = WRITE_DEDUP_PREFILTER if twin_mode else WRITE_DEDUP_SIM
        scored = []
        cache_gen = _NDUP_MEMO[1] or {}
        pending = [d for d in _NDUP_PENDING
                   if d["project"] == project and d["ntype"] == ntype
                   and d["stem"] not in cache_gen]
        for stem, e in cache_gen.items():
            if (not isinstance(e, dict) or e.get("project") != project
                    or e.get("ntype") != ntype or stem in exclude or not e.get("vec")):
                continue
            fp = folder_path / f"{stem}.md"
            if not fp.exists():                             # retired/archived cache leftovers
                continue
            sim = cosine(vec, e["vec"])
            if sim < floor:
                continue
            if twin_mode:
                cand_ents = _read_frontmatter_file(fp).get("entities") or []
                p_twin = _twin_probability(sim, title, desc, entities,
                                           e.get("title", ""), e.get("desc", ""),
                                           cand_ents)
                if p_twin >= WRITE_DEDUP_TWIN_P:
                    scored.append((p_twin, stem))
            else:
                scored.append((sim, stem))
        for d in pending:                       # same-process writes the cache can't see yet
            stem = d["stem"]
            if stem in exclude or not (folder_path / f"{stem}.md").exists():
                continue
            sim = cosine(vec, d["vec"])
            if sim < floor:
                continue
            if twin_mode:
                p_twin = _twin_probability(sim, title, desc, entities,
                                           d["title"], d["desc"], d["entities"])
                if p_twin >= WRITE_DEDUP_TWIN_P:
                    scored.append((p_twin, stem))
            else:
                scored.append((sim, stem))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [folder_path / f"{s}.md" for _, s in scored[:WRITE_DEDUP_MAX_RETIRE]]
    except Exception as e:
        log(f"near-dup gate skipped ({type(e).__name__}: {e})")
        return []


#: F7 (xhigh review): sibling identity is a STAMP, not a name pattern. Written on a `-N` note
#: when `_unique_path` mints it for a same-slug collision, naming the base stem it collided with.
SIBLING_KEY = "sibling_of"


def _slug_family(p: Path, parsed: dict, slug: str, folder_path: Path) -> bool:
    """Is `p` (parsed as `parsed`) in the slug family of `slug` - the base itself, or one of its
    `-N` siblings? A note minted beside a same-title note carries the suffix in its stem, and
    every slug lookup - the same-session refresh, the reconcile, the contested pairing - must see
    the whole family, not the base alone. (Before K8 a crash-retry of the session that wrote a
    `-2` minted a `-3`, and a third statement never met the second.)

    xhigh review F7: `_SIB_SUFFIX_RE` used to accept `<slug>-N` for ANY base - 'Python 3', 'Use
    HTTP/2', 'Sprint 2'/'Sprint 3' were folded, contested or retired against unrelated titles.
    Family membership now comes from `p`'s OWN `sibling_of` stamp (written when `_unique_path`
    minted it); the name pattern is a legacy fallback ONLY for a pre-stamp note, and only when a
    base note of the exact stripped slug and the SAME day actually exists in this same folder -
    `python-3` is not a sibling of `python` merely by looking like one."""
    if parsed["slug"] == slug:
        return True
    # Exact reject BEFORE any I/O. `_unique_path` builds a sibling's stem as the base stem plus
    # a suffix and `write_typed_note` stamps it in the same breath, so a stamped sibling's slug
    # always STARTS with the base slug. A note whose slug is not an extension of this one is
    # therefore not in its family, whatever its frontmatter says - and saying so costs nothing.
    # Without this the stamp check opened and parsed every note in the folder with a matching
    # project and type: ~3000 opens per `write_typed_note` in a 1500-note folder, under the
    # vault lock. The test does not weaken: the stamp still decides every candidate that passes.
    if not parsed["slug"].startswith(slug + "-"):
        return False
    stamped = _read_frontmatter_file(p).get(SIBLING_KEY)
    if stamped:
        base_parsed = parse_typed_stem(str(stamped))
        return bool(base_parsed) and base_parsed["slug"] == slug
    stripped = _SIB_SUFFIX_RE.sub("", parsed["slug"])
    if stripped == slug and stripped != parsed["slug"]:
        base_stem = f"{parsed['date']}-{parsed['project']}-{parsed['ntype']}-{slug}"
        return (folder_path / f"{base_stem}.md").exists()
    return False


def _live_typed_paths(folder_path: Path, project: str, ntype: str,
                      slug: str) -> list[Path]:
    """Live (non-archived, non-superseded) notes of the slug family (project+ntype+slug and its
    `-N` siblings), newest first. Superseded/ and Archive/ are subdirs, so a flat glob skips them."""
    hits = []
    for p in folder_path.glob("*.md"):
        parsed = parse_typed_stem(p.stem)
        if parsed and parsed["project"] == project \
                and parsed["ntype"] == ntype and _slug_family(p, parsed, slug, folder_path):
            hits.append(p)
    return sorted(hits, key=lambda p: p.stem, reverse=True)


def _archived_typed_paths(folder_path: Path, project: str, ntype: str,
                          slug: str) -> list[Path]:
    """Archived notes matching project+ntype+slug, newest first (J2b). A note older
    than the 90-day window has been moved to `Archive/` and dropped from the embed
    cache and the index; the reconcile that closes a belief interval must still see
    it, because a fact replaced more than ninety days after it was stated is exactly
    the long-history case a memory exists for."""
    arch = folder_path / "Archive"
    if not arch.exists():
        return []
    hits = []
    for p in arch.glob("*.md"):
        parsed = parse_typed_stem(p.stem)
        if parsed and parsed["project"] == project \
                and parsed["ntype"] == ntype and _slug_family(p, parsed, slug, arch):
            hits.append(p)
    return sorted(hits, key=lambda p: p.stem, reverse=True)


def _reconcilable_typed_paths(folder_path: Path, project: str, ntype: str,
                              slug: str) -> list[Path]:
    """Live notes plus archived ones, newest first (J2b). Used by the same-slug and
    explicit-supersedes reconcile passes so retirement closes an interval whether the
    old note is still live or has aged into `Archive/`. The idempotency/absorb pass
    and the near-duplicate twin classifier stay live-only: an absorb rewrites a recent
    same-session note in place (never an archived one), and the twin classifier ranks
    on the embedding cache, which an archived note has left by construction."""
    return sorted(_live_typed_paths(folder_path, project, ntype, slug)
                  + _archived_typed_paths(folder_path, project, ntype, slug),
                  key=lambda p: p.stem, reverse=True)


def supersede_note(p: Path, new_stem: str, via: str = "slug", extra_fields: dict | None = None,
                   cache: dict | None = None) -> bool:
    """Retire a superseded note: stamp status, move into <folder>/Superseded/
    (Obsidian still resolves [[stem]]), drop it from the embedding cache so
    recall surfaces only current truth - contradictory facts no longer coexist
    (audit H1).

    `via` records HOW the retirement was decided - `slug` (an older same-slug
    re-statement), `explicit` (the LLM filled supersedes/contradicts) or `twin`
    (the near-duplicate classifier). Stamped as `superseded_via` so the as-of
    stand can split interval closures by mechanism (ledger J2b). An archived note
    (`p` under `Archive/`) moves to `Archive/Superseded/` by the same
    `p.parent / "Superseded"` rule below - the reconcile now reaches it (J2b).

    `extra_fields` (F5, xhigh review), when given, is merged into the SAME stamp the
    archived copy gets - a caller that also wants to clear its own frontmatter field
    (the sleep-time judge clearing `contested`) rides along atomically with the
    retirement instead of writing it separately BEFORE this call. A separate pre-write
    left the field cleared on the ORIGINAL live note even when the retirement below then
    failed (Windows: Obsidian/AV/sync holding it open) - orphaned, the stamp already gone
    and nothing pointing back at it. Nothing here ever touches `p` on disk before the
    unlink at the end, so a failure (this function returning False) always leaves `p`
    exactly as it was."""
    if p.stem == new_stem:
        return False
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    fields = {"status": "superseded", "superseded_by": new_stem, "superseded_via": via}
    new_date = (parse_typed_stem(new_stem) or {}).get("date", "")
    if new_date:
        fields["valid_to"] = new_date    # M-5: belief held until the replacement
    if extra_fields:
        fields.update(extra_fields)
    text = _stamp_frontmatter(text, fields)
    dest_dir = p.parent / "Superseded"
    dest_dir.mkdir(exist_ok=True)
    # Collision-safe destination (review 2026-08): a stem retired before can be
    # re-minted live (the live-folder-only _unique_path check) and retired again -
    # overwriting the EARLIER retired note's history, and the Windows rollback
    # below then deleted that pre-existing file outright (a note this call never
    # created). Write to a suffixed name instead so the rollback can only ever
    # remove OUR copy.
    dest_fp = dest_dir / p.name
    if dest_fp.exists():
        i = 2
        while (dest_dir / f"{p.stem}-{i}{p.suffix}").exists():
            i += 1
        dest_fp = dest_dir / f"{p.stem}-{i}{p.suffix}"
    try:
        write_atomic(dest_fp, text)
    except OSError as e:
        log(f"Supersede failed for {p.name}: {e}")
        return False
    try:
        p.unlink(missing_ok=True)
    except OSError as e:
        # the copy landed in Superseded/ but the original would not delete (Windows: Obsidian /
        # AV / OneDrive holding it open). Roll the copy back - leaving BOTH would mean two
        # contradictory "live" notes, exactly what this mechanism exists to prevent
        # (code-review 2026-07).
        try:
            dest_fp.unlink(missing_ok=True)
        except OSError:
            pass
        log(f"Supersede failed for {p.name} (unlink: {e}); rolled back the Superseded/ copy")
        return False
    _unregister_slug(p.stem)              # keep the grounding cache honest (audit A16)
    index_follows = True                  # B3/F13: the index follows the cache, never ahead of it
    if cache is not None:
        #: The caller holds the vector cache and writes it once when its loop is done. Writing it
        #: here as well cost one FULL rewrite per retired note - 2 093 ms a write on the owner's
        #: 107 MB cache, so ~7 minutes for two hundred retirements in one consolidation, for a
        #: file the caller was about to write anyway (K9, measured by the auditing session).
        cache.pop(p.stem, None)
    else:
        cache = load_embed_cache()
        if cache.pop(p.stem, None) is not None:
            index_follows = save_embed_cache(cache, delete=[p.stem])      # B3: one record, journalled
    if index_follows:
        sync_scale_index(delete=[p.stem])     # drop from the SQLite index too (C2/C3)
    log(f"Superseded {p.stem} → {new_stem}")
    return True


def mark_resolved(mistake_fp: Path, by_stem: str) -> bool:
    """Flag a mistake as resolved by a later decision/pattern (audit I-18). The
    mistake stays LIVE (still history, still searchable), but recall stops
    treating it as an active 'do-not-repeat' warning - see _note_snippet/emit."""
    try:
        text = mistake_fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    try:
        write_atomic(mistake_fp, _stamp_frontmatter(
            text, {"status": "resolved", "resolved_by": by_stem}))
    except OSError as exc:
        # The one deferred post-write call that raised instead of returning False, unlike its
        # sibling `supersede_note`. It runs AFTER the new note is durably on disk and after the
        # retirement loop, so on `api.remember` - where nothing catches it - the exception escaped
        # with the note written, its predecessors retired, and `update_embeddings`, `rebuild_index`
        # and `git_autocommit` all skipped: a note that exists, is invisible to recall, and is
        # uncommitted, while the caller is told the write failed.
        log(f"Could not mark {mistake_fp.stem} resolved: {exc}")
        return False
    # Propagate the flag to the retrieval substrate NOW so the salience de-weight
    # actually applies - it was otherwise dead until the next full embed --rebuild,
    # because the live cache/index never carried `resolved` (critic round 3).
    cache = load_embed_cache()
    rec = cache.get(mistake_fp.stem)
    if isinstance(rec, dict) and not rec.get("resolved"):
        rec["resolved"] = True
        if save_embed_cache(cache, put={mistake_fp.stem: rec}):   # B3: one record, journalled
            sync_scale_index(records={mistake_fp.stem: rec})      # the index follows the cache
    log(f"Resolved {mistake_fp.stem} ← {by_stem}")
    return True


#: Ceiling on a recurrence count read from frontmatter. Recurrence counts DISTINCT
#: contributing sessions and its provenance set is capped at RECUR_SOURCES_CAP, so a count
#: orders of magnitude above that is not evidence - it is an assertion nothing can back. Left
#: unbounded, one hand-edited or imported note claiming `recurrence: 999999999` outranks the
#: entire store forever, which is memory poisoning through arithmetic rather than through
#: content (GOAL E2). The ceiling is far above any real note and far below anything that can
#: dominate the ranking.
RECUR_COUNT_CAP = 1000


def _coerce_recurrence(v) -> int:
    """Recurrence as a positive, bounded int - THE one parse rule (review 2026-08 R2: this
    logic had drifted between embed_index and the live readers, so a note's count
    depended on which path last touched it: round(float) tolerates "2.7" where
    int() raised and reset the count to 1; the floor keeps a stray negative from
    down-weighting recall; isfinite guards round(inf) -> OverflowError). The ceiling is
    E2's: an unbounded count read from a file is a ranking-poisoning vector."""
    try:
        f = float(v if v not in (None, "") else 1)
        return min(RECUR_COUNT_CAP, max(1, round(f))) if math.isfinite(f) else 1
    except (TypeError, ValueError):
        return 1


def _note_recurrence(p: Path) -> int:
    """Recurrence count from a note's YAML header (default 1, tolerant of junk)."""
    return _coerce_recurrence(_read_frontmatter_file(p).get("recurrence", 1))


RECUR_SOURCES_CAP = 25      # bound the stored provenance set; the count is the ranking signal


def _note_recur_sources(p: Path) -> tuple[int, set]:
    """Recurrence count + the DISTINCT sessions that contributed to a note, from ONE
    frontmatter read (the supersede loop needs both - reading once avoids a double parse).
    Recurrence counts distinct sources, so re-stating a false lesson from one session can't
    inflate its salience past 1 - anti-gaming defence-in-depth beyond write idempotency (C5)."""
    fm = _read_frontmatter_file(p)
    n = _coerce_recurrence(fm.get("recurrence", 1))
    #: Through the engine's one list reader: a hand-written `sources: [s1, s2]` or `[[s1]]` reads as
    #: its sessions, not as no list at all. This fallback dropped them on every write-path absorb and
    #: retirement, and the consolidator had patched only its own call (third review, 2026-09-23).
    sources = set(_list_field(fm.get("sources")))
    if not sources:
        s = fm.get("session")
        sources = {str(s)} if s else set()
    return n, sources


def _embed_recurrence(stem: str, ntype: str, cache: dict) -> int:
    """Recurrence for a freshly embedded note: the note's own frontmatter wins
    (write_typed_note carries the count forward on a re-statement - audit A3),
    else the prior cache entry for that stem."""
    folder = TYPE_FOLDER.get(ntype)
    if folder and (r := _note_recurrence(VAULT / folder / f"{stem}.md")) > 1:
        return r
    return _coerce_recurrence((cache.get(stem) or {}).get("recurrence", 1))


def _note_resolved(stem: str, ntype: str) -> bool:
    """True when the note's frontmatter marks it resolved - read at embed time so the
    live update_embeddings path carries `resolved` like embed_index.py does, instead
    of leaving the salience de-weight dead until a --rebuild (critic round 3)."""
    folder = TYPE_FOLDER.get(ntype)
    if not folder:
        return False
    fm = _read_frontmatter_file(VAULT / folder / f"{stem}.md")
    # .lower(): embed_index and _note_meta compare case-insensitively - a note with
    # `status: Resolved` was de-weighted by the batch path but kept full salience on
    # this live path (review 2026-08 R2)
    return str(fm.get("status", "")).lower() == "resolved" or bool(fm.get("resolved_by"))


# ── write-path literal-fact preservation (ledger J3 held-out; fact_survival) ──────────
# The extractor summarises, and summarising drops the one token a future question asks for:
# `nvcc -arch=sm_120` becomes "sm_120 support", `87c8b17` vanishes into "reproducibility". On the
# owner's own hand-marked held-out the answer survived into the returned notes for 5 of 52 questions
# - lost at write, not at retrieval. Two channels put the literal back, both verified against THIS
# session so a paraphrase or an invention (never an exact substring) cannot enter:
#   1. the extractor names the literal per item (prompt FIELD facts), verified verbatim;
#   2. a deterministic harvester salvages literals from the session text near the note's own topic,
#      for the facts the summary omitted entirely (the note exists, the value was never written).
# The survivors are appended to the note's description, so recall, the embedding and every reader
# carry them with no read-path change. Bounded per note so the store-bytes cost gate holds.
_FACTS_MARK = "  [facts] "
_FACTS_MAX_N = 10                # at most this many literals per note (LLM-named first, then harvested)
_FACTS_MAX_CHARS = 360           # and at most this many characters of them together
_FACTS_MIN_LEN = 2               # a one-char "fact" carries nothing and matches everything

# High-value literal shapes a coding question asks for: commands, hashes, image tags, flags, paths,
# versions, numbers with units, bracketed lists, hyphen-camel identifiers, host:port, backtick spans.
_LIT_PATTERNS = [re.compile(p) for p in (
    r"`[^`\n]{2,60}`",
    r"\b[0-9a-f]{7,40}\b",
    # Segment-by-segment, and BOUNDED. The first class used to contain the `/` it then
    # required, so on path-like text with no colon the engine re-split the whole run from
    # every start position: 0.26 s at 12 kB, 4.09 s at 48 kB, 16.3 s at 96 kB - quadratic,
    # on the write path, under the vault lock. Slashes are explicit here and each segment
    # is capped, so the work per start position is bounded and the scan is linear (0.031 s
    # at 48 kB). No real image reference is longer than this; the probes pin the shapes.
    r"/?[A-Za-z0-9_.-]{1,60}(?:/[A-Za-z0-9_.-]{1,60}){1,6}:[A-Za-z0-9_.-]{1,60}",
    r"\bpython [\w./-]+\.py[\w\s./=-]{0,40}",
    r"\b(?:nvcc|docker|git|pip|npm|cargo|make|cmake|gcc|claude|ollama|kubectl|curl|wget)\b[^\n]{0,50}",
    r"--?[A-Za-z][\w-]*=[^\s]+",
    r"\bseed=\d+\b",
    r"\b\d{1,3}(?:\.\d{1,3}){3}:\d+\b",
    r"\b\w+(?:\.\w+)+:\w+\b",
    r"[A-Za-z]:\\[\w\\.-]+",
    # F14 (xhigh review): the unit list lacked plain time units - a value stated in seconds/
    # minutes/hours/days (the commonest shape for a timeout/backoff/TTL) was never harvested,
    # so the write-time facts block never verified it and the sleep-time guard vetoed a true
    # replacement, parking it `disputed` on nothing but a missing unit word.
    r"[~+\-]?\b\d+(?:\.\d+)?\s?(?:MB|GB|KB|TB|ms|GHz|MHz|px|%|s|seconds?|minutes?|hours?|days?)\b",
    r"\b\d+\.\d+\b",
    r"\[[^\]\n]{1,40}\]",
    r"\b[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b",
    r"[A-Za-z0-9_./-]+\.[A-Za-z]{1,5}\b",
    r"\bv\d+(?:\.\d+)*\b",
    r"\b\d+\.\d+(?:\.\d+)+\b",
    r"\bsm_\d+\b",
)]
_FACT_STOP = frozenset(
    "the a an of to in on for and or is are was were be been this that with from into your their "
    "which what when where value used using set new one two run runs code file note session".split())


def _norm_ws(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip().lower()


def _content_words(t: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (t or "").lower()) if len(w) >= 4 and w not in _FACT_STOP}


def _verbatim_facts(raw, source: str) -> list[str]:
    """The extractor-named literals that appear VERBATIM in `source` (whitespace-normalised, case
    insensitive). Paraphrase and invention are not substrings and vanish. Deduped, len-bounded and
    secret-redacted - they land in the .md and the plaintext embedding cache like any description."""
    if not isinstance(raw, list):
        return []
    src = _norm_ws(source)
    out, seen = [], set()
    for f in raw:
        if not isinstance(f, str):
            continue
        f = redact_secrets(f.strip())
        if not (_FACTS_MIN_LEN <= len(f) <= 120):
            continue
        key = _norm_ws(f)
        if key and key not in seen and key in src:
            seen.add(key)
            out.append(f)
    return out


def _harvest_literals(source: str, near: str, want: int, exclude: set) -> list[str]:
    """Deterministic salvage: literal tokens in `source` whose local context shares a content word
    with `near` (the note's title + description). Tying each literal to the note's own topic keeps a
    CUDA-build note carrying `nvcc -arch=sm_120` and not an unrelated seed. Bounded by `want`."""
    nw = _content_words(near)
    if not nw or want <= 0:
        return []
    out, seen = [], set(exclude)
    for rx in _LIT_PATTERNS:
        for mo in rx.finditer(source):
            lit = redact_secrets(mo.group(0).strip("`\"' ").strip())
            if not (_FACTS_MIN_LEN <= len(lit) <= 80):
                continue
            key = _norm_ws(lit)
            if not key or key in seen:
                continue
            ctx = source[max(0, mo.start() - 80): mo.start() + len(lit) + 80]
            if nw & _content_words(ctx):
                seen.add(key)
                out.append(lit)
                if len(out) >= want:
                    return out
    return out


_PREAMBLE_RE = _lazy_re(r"\A(?:(?:Working directory|Trigger):[^\n]*\n)+\n*")


def _facts_source(transcript: str) -> str:
    """The text the literal channel may quote from: the session body without the two lines the
    hook prepends (`Working directory:`, `Trigger:`). Those are the frame around the session, not a
    fact of it - and because the harvester read them, the working directory landed in nearly every
    note's `[facts]` block as a "fact" (27 of 31 notes in the K1 store; ledger K6): noise in every
    note, bytes in every store, one literal shared by every note of a project. A path the session
    itself mentions is still in the body and still quotable."""
    return _PREAMBLE_RE.sub("", transcript or "", count=1)


def _note_facts(item: dict, source: str) -> list[str]:
    """The literals to preserve for one note: the extractor's named facts first (semantic pick),
    then harvested ones to fill up to the per-note cap. Deduped against each other and against the
    description, bounded by count and characters so the store-bytes gate holds."""
    desc = item.get("description", "") or ""
    dl = _norm_ws(desc)
    named = _verbatim_facts(item.get("facts"), source)
    excl = {_norm_ws(f) for f in named}
    harvested = _harvest_literals(source, f"{item.get('title', '')} {desc}",
                                  want=_FACTS_MAX_N, exclude=excl)
    out, seen, used = [], set(), 0
    for f in named + harvested:
        key = _norm_ws(f)
        if not key or key in seen or key in dl:
            continue
        if used + len(f) > _FACTS_MAX_CHARS:
            break
        seen.add(key)
        out.append(f)
        used += len(f)
        if len(out) >= _FACTS_MAX_N:
            break
    return out


def _facts_in(desc: str) -> set:
    """The literals a description's `[facts]` block carries, whitespace-normalised and lower-cased."""
    if not desc or _FACTS_MARK.strip() not in desc:
        return set()
    tail = desc.split(_FACTS_MARK.strip(), 1)[1]
    return {_norm_ws(f) for f in tail.split("\u00b7") if _norm_ws(f)}


