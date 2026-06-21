#!/usr/bin/env python3
"""SQLite scale-index for the retrieval hot path (P-7, audit C2/C3).

The markdown files remain the single source of truth and the JSON embedding
cache remains the rebuild source; this is a *derived, rebuildable* accelerator.
The round-1 version was a dead standalone CLI nothing called (audit C3), while
the live `retrieve_relevant` parsed the whole 63 MB JSON cache on EVERY prompt
(audit C2). Now the hook keeps this index current (incremental upsert on write,
delete on supersede/archive) and the retrieval paths read candidates straight
from it — project-filtered IN SQL, so only the relevant subset's vectors are
unpacked instead of re-parsing the entire cache per prompt.

FTS5 lexical + packed float32 vector BLOBs, zero dependencies (stdlib `sqlite3`
+ `array`). Never replaces the markdown; delete the `.sqlite` file and nothing
is lost — the next write rebuilds it from the cache.

    python index_sqlite.py build                 # (re)build from the cache
    python index_sqlite.py "cuda oom" myproject  # query it
"""
import math
import os
import sqlite3
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from . import memory_hook as m
except ImportError:
    import memory_hook as m

SIM_FLOOR = m.RETRIEVAL_NEAR_FLOOR   # shared nearest-neighbour floor, not a private 0.15 (audit)
# columns selected for ranking candidates — kept in one place so the SQL and the
# row→record mapping never drift apart
_CAND_COLS = ("stem", "project", "ntype", "title", "descr", "prevention",
              "recurrence", "resolved", "confidence", "salience", "vec")


def db_path() -> Path:
    return m.VAULT / ".index.sqlite"


# Default pack: struct half-float (float16) — half the index size of float32 with
# cosine-negligible precision loss (improvement P3). The `array` module has no
# float16, so vectors are packed via `struct`.
#
# Opt-in 1-bit quantization (improvement A2, launch round): ANAMNESIS_EMBED_QUANT=binary
# packs sign-bit codes — 16x smaller than float16, 32x smaller than the float32 cache.
# The ranker cosines the float query against the unpacked {-1,+1} doc, so a binary
# candidate scores as ASYMMETRIC binary cosine, measured ~lossless on LongMemEval
# (R@5 0.802 → 0.796, R@1 0.550 → 0.548; research/QUANTIZATION.md). Default OFF; the
# JSON cache stays float32 (the rebuild source), so flipping the env just rebuilds the
# index in the new format — vec_format below stamps it, and a mismatch self-migrates.
_BINARY = os.environ.get("ANAMNESIS_EMBED_QUANT", "").strip().lower() in ("binary", "bin", "1bit")
VEC_FORMAT = "b1" if _BINARY else "e"   # "e"=float16 (default); "b1"=sign-bit binary
_VEC_SIZE = 0 if _BINARY else struct.calcsize(VEC_FORMAT)


def _pack(vec) -> bytes:
    if _BINARY:
        d = len(vec)
        bits = bytearray((d + 7) // 8)
        for i, x in enumerate(vec):
            if x >= 0.0:                          # 1-bit sign code (1 = non-negative)
                bits[i >> 3] |= 1 << (i & 7)
        return struct.pack("<H", d) + bytes(bits)   # 2-byte dim header → self-describing unpack
    return struct.pack(f"<{len(vec)}{VEC_FORMAT}", *vec)


def _unpack(b: bytes) -> list:
    if _BINARY:
        if len(b) < 2:
            return []
        (d,) = struct.unpack_from("<H", b, 0)
        bits = b[2:]
        if len(bits) < (d + 7) // 8:        # truncated/corrupt code → no signal, never IndexError
            return []
        return [1.0 if (bits[i >> 3] >> (i & 7)) & 1 else -1.0 for i in range(d)]
    if not b or _VEC_SIZE == 0:             # empty blob (text-only) or mis-set size → no vector
        return []
    try:
        return list(struct.unpack(f"<{len(b) // _VEC_SIZE}{VEC_FORMAT}", b))
    except struct.error:                    # stale-format / truncated blob → no signal, never raise
        return []


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(db_path())
    # WAL lets lock-free retrieval read while the hook upserts under the vault
    # lock; busy_timeout absorbs the brief overlap instead of erroring.
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=2000")
    except sqlite3.Error:
        pass
    return con


def _fts_ok(con: sqlite3.Connection) -> bool:
    try:
        con.execute("CREATE VIRTUAL TABLE _fts_probe USING fts5(x)")
        con.execute("DROP TABLE _fts_probe")
        return True
    except sqlite3.Error:        # any FTS-less build, not just OperationalError (audit A11)
        return False


def _has_fts(con: sqlite3.Connection) -> bool:
    try:
        con.execute("SELECT 1 FROM notes_fts LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def _conf(r: dict):
    c = m._coerce_confidence(r.get("confidence")) if hasattr(m, "_coerce_confidence") \
        else (r.get("confidence") if isinstance(r.get("confidence"), (int, float)) else None)
    return c


def _row(stem: str, r: dict, salience: float = 0.0) -> tuple:
    """A note record → a `notes` table row (column order = the CREATE below). `salience`
    (Brain F5) is sourced from frontmatter at build time, 0 for a fresh/unstamped note. Clamped
    via m._coerce_salience (NaN-safe) exactly as _conf delegates confidence — one clamp rule."""
    vec = r.get("vec") or []
    sal = m._coerce_salience(r.get("salience") or salience)
    return (stem, r.get("project"), r.get("ntype"), r.get("title"),
            r.get("desc", ""), r.get("prevention", ""),
            int(r.get("recurrence", 1) or 1),
            1 if r.get("resolved") else 0, _conf(r), sal,
            len(vec), _pack(vec))


def _fts_text(r: dict, stem: str) -> str:
    return f"{r.get('title','')} {r.get('desc','')} {r.get('prevention','')} {stem}"


def _create_schema(con: sqlite3.Connection) -> bool:
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS notes(
        stem TEXT PRIMARY KEY, project TEXT, ntype TEXT, title TEXT,
        descr TEXT, prevention TEXT, recurrence INTEGER, resolved INTEGER,
        confidence REAL, salience REAL, dim INTEGER, vec BLOB)""")
    try:
        cur.execute("ALTER TABLE notes ADD COLUMN salience REAL")   # migrate a pre-F5 index in place
    except sqlite3.OperationalError:
        pass                                                        # column already present
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notes_project ON notes(project)")
    cur.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    fts = _fts_ok(con)
    if fts:
        cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(stem, text)")
    return fts


def index_exists() -> bool:
    """True if a usable index file with the `notes` table is present."""
    if not db_path().exists():
        return False
    try:
        con = _connect()
        try:
            con.execute("SELECT 1 FROM notes LIMIT 1")
            return True
        finally:
            con.close()
    except sqlite3.Error:
        return False


def index_meta() -> dict:
    """The embed model + vector dim the index was built with (audit A5). Empty
    for no index or a legacy/unstamped one — the caller treats that as 'current'
    so an upgrade doesn't force an immediate rebuild."""
    if not db_path().exists():
        return {}
    try:
        con = _connect()
        try:
            return {k: v for k, v in con.execute("SELECT key, value FROM meta")}
        except sqlite3.Error:
            return {}
        finally:
            con.close()
    except sqlite3.Error:
        return {}


def build(verbose: bool = False) -> int:
    """(Re)build the index from the embedding cache. Returns the note count.
    Stamps the embed model + vector dim into `meta` so retrieval can refuse a
    stale-model index instead of silently ranking against incompatible vectors
    (audit A5); a single garbage vector skips its row instead of aborting the
    whole build and leaving the accelerator permanently unbuilt (audit A10).
    Atomic: the content swap is one transaction (DELETE + re-insert, NOT DROP
    TABLE), so a concurrent lock-free reader — retrieval takes no vault lock —
    keeps the OLD complete index until COMMIT and never sees an empty/partial
    table mid-rebuild (which silently returned zero hits). A crash mid-build rolls
    back, leaving the previous index intact (critic round 3)."""
    cache = m.load_embed_cache()
    con = _connect()
    con.isolation_level = None       # manual transaction control for the atomic swap
    try:
        cur = con.cursor()
        fts = _create_schema(con)    # CREATE IF NOT EXISTS (autocommitted; tables persist)
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("DELETE FROM notes")
        if fts:
            cur.execute("DELETE FROM notes_fts")
        cur.execute("DELETE FROM meta")
        # Brain F5: salience lives in note frontmatter (stamped sleep-time), not the embed cache,
        # so source it from the markdown here. ONE scan, reused for the graph rebuild below;
        # {} → every row salience 0 (inert).
        try:
            all_notes = m._iter_all_notes()
        except Exception:
            all_notes = []
        sal_map = {nt["stem"]: nt.get("salience", 0.0) for nt in all_notes}
        n = dim = 0
        for stem, r in cache.items():
            if not isinstance(r, dict):
                continue
            v = r.get("vec")
            if v is not None and not isinstance(v, list):
                continue        # malformed vec field — drop the row
            try:
                # vec=None → empty blob / dim 0: a text-only entry, FTS-indexed for
                # lexical recall but skipped by the semantic scan (no-embedder, #32)
                row = _row(stem, r, sal_map.get(stem, 0.0))
            except (TypeError, ValueError, OverflowError, struct.error):
                continue        # poisoned vector (bad type / out of range) — drop the row
            cur.execute(f"INSERT OR REPLACE INTO notes VALUES ({','.join('?' * 12)})", row)
            if fts:
                cur.execute("INSERT INTO notes_fts (stem, text) VALUES (?, ?)",
                            (stem, _fts_text(r, stem)))
            dim = dim or row[10]      # dim moved to index 10 after salience was inserted at 9 (F5)
            n += 1
        model = (m.load_embed_meta() or {}).get("model") or m.embed_signature()
        cur.execute("INSERT OR REPLACE INTO meta VALUES ('model', ?)", (str(model),))
        cur.execute("INSERT OR REPLACE INTO meta VALUES ('dim', ?)", (str(dim),))
        cur.execute("INSERT OR REPLACE INTO meta VALUES ('vec_format', ?)", (VEC_FORMAT,))
        cur.execute("COMMIT")
        g = reindex_graph(all_notes, full=True)   # F4: graph tables from the SAME scan (no 2nd read)
        if verbose:
            print(f"[index] built {db_path().name}: {n} notes, {g} graph rows "
                  f"(fts={fts}, model={model})", file=sys.stderr)
        return n
    finally:
        con.close()


def upsert(records: dict) -> int:
    """Insert/replace `records` (stem -> cache record) into an existing index —
    the incremental write path the hook calls after embedding new notes."""
    if not records:
        return 0
    con = _connect()
    try:
        cur = con.cursor()
        fts = _has_fts(con)
        n = 0
        for stem, r in records.items():
            if not isinstance(r, dict):
                continue
            v = r.get("vec")
            if v is not None and not isinstance(v, list):
                continue
            try:
                row = _row(stem, r)   # vec=None → text-only FTS row (no-embedder, #32)
            except (TypeError, ValueError, OverflowError, struct.error):
                continue        # poisoned/out-of-range vector — skip the row, not the
                                # whole batch (mirror build()'s A10 guard; P3 struct.pack
                                # raises OverflowError where the old array('f') never did)
            cur.execute(f"INSERT OR REPLACE INTO notes VALUES ({','.join('?' * 12)})", row)
            if fts:
                cur.execute("DELETE FROM notes_fts WHERE stem = ?", (stem,))
                cur.execute("INSERT INTO notes_fts (stem, text) VALUES (?, ?)",
                            (stem, _fts_text(r, stem)))
            n += 1
        con.commit()
        return n
    finally:
        con.close()


def delete(stems) -> int:
    """Drop notes from the index (supersede / archive / forget keep it in sync)."""
    if not stems:
        return 0
    con = _connect()
    try:
        cur = con.cursor()
        fts = _has_fts(con)
        n = 0
        for stem in stems:
            try:
                cur.execute("DELETE FROM notes WHERE stem = ?", (stem,))
                n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                if fts:
                    cur.execute("DELETE FROM notes_fts WHERE stem = ?", (stem,))
            except sqlite3.Error:
                pass            # notes/fts absent on a graph-only index — only graph rows to prune
            for tbl in ("note_entities", "note_relations", "note_etype"):   # F4: keep the graph in sync
                try:
                    cur.execute(f"DELETE FROM {tbl} WHERE stem = ?", (stem,))
                except sqlite3.Error:
                    pass            # graph tables absent on a pre-F4 index — nothing to prune
        con.commit()
        return n
    finally:
        con.close()


# ── Entity / relation graph index (Brain layer, F4) ──────────────────────────────
# The graph queries (typed-entity enumeration, faceted recall, co-occurrence, typed edges)
# read note FRONTMATTER — an O(all-notes) markdown scan per call. At Brain-layer scale (many
# typed entities, frequent card refreshes) that stalls. These derived tables index the entity
# facets so the SAME queries run in SQL. Sourced from the markdown notes (the truth), NOT the
# embed cache (which carries no entities); rebuilt by reindex_graph()/build(), kept current by
# upsert_graph(stems)/delete(stems). Drop the .sqlite and nothing is lost.


def _create_graph_schema(con: sqlite3.Connection) -> None:
    # Self-sufficient: each row carries its own project/date, so the graph queries never join
    # the `notes` table — the graph index works even on a store with no embeddings yet.
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")  # may run before build()
    cur.execute("CREATE TABLE IF NOT EXISTS note_entities(stem TEXT, entity TEXT, project TEXT)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ne_entity ON note_entities(entity)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ne_stem ON note_entities(stem)")
    cur.execute("CREATE TABLE IF NOT EXISTS note_relations(stem TEXT, rel TEXT, target TEXT, project TEXT)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nr_stem ON note_relations(stem)")
    cur.execute("CREATE TABLE IF NOT EXISTS note_etype(stem TEXT, entity TEXT, etype TEXT, project TEXT, date TEXT)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_net_entity ON note_etype(entity)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_net_type ON note_etype(etype)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_net_stem ON note_etype(stem)")


def _graph_rows(meta: dict):
    """A note meta → (entity rows, relation rows, etype rows) for the graph tables."""
    stem = meta.get("stem")
    proj = meta.get("project") or ""
    date = meta.get("date") or ""
    e_rows = [(stem, e, proj) for e in (meta.get("entities") or []) if e]
    r_rows = [(stem, ed.get("rel"), ed.get("target"), proj) for ed in (meta.get("relations") or [])
              if ed.get("rel") and ed.get("target")]
    t_rows = [(stem, e, t, proj, date) for e, t in (meta.get("entity_types") or {}).items() if e and t]
    return e_rows, r_rows, t_rows


def reindex_graph(metas: list | None = None, full: bool = False) -> int:
    """(Re)build the entity/relation graph tables from note frontmatter (the truth).
    metas=None → FULL scan of every live note (clears + repopulates). metas given + full=True →
    FULL rebuild from the GIVEN notes (lets build() reuse its single vault scan). metas given +
    full=False → incremental (replace rows for just those stems). Derived & rebuildable — any
    failure is logged and swallowed, leaving the markdown scan as the correct fallback. Returns
    rows written."""
    con = _connect()
    try:
        _create_graph_schema(con)
        cur = con.cursor()
        if metas is None:
            metas = m._iter_all_notes()
            full = True
        if full:
            cur.execute("DELETE FROM note_entities")
            cur.execute("DELETE FROM note_relations")
            cur.execute("DELETE FROM note_etype")
        else:
            stems = [mt.get("stem") for mt in metas if mt.get("stem")]
            for i in range(0, len(stems), 400):          # chunked: stay under SQLite's host-param cap
                chunk = stems[i:i + 400]
                q = ",".join("?" * len(chunk))
                for tbl in ("note_entities", "note_relations", "note_etype"):
                    cur.execute(f"DELETE FROM {tbl} WHERE stem IN ({q})", chunk)
        e_all, r_all, t_all = [], [], []
        for mt in metas:
            er, rr, tr = _graph_rows(mt)
            e_all += er
            r_all += rr
            t_all += tr
        cur.executemany("INSERT INTO note_entities VALUES (?,?,?)", e_all)
        cur.executemany("INSERT INTO note_relations VALUES (?,?,?,?)", r_all)
        cur.executemany("INSERT INTO note_etype VALUES (?,?,?,?,?)", t_all)
        if full:
            cur.execute("INSERT OR REPLACE INTO meta VALUES ('graph_built', '1')")
        con.commit()
        return len(e_all) + len(r_all) + len(t_all)
    except sqlite3.Error as e:
        m.log(f"graph reindex skipped: {e}")
        return 0
    finally:
        con.close()


def upsert_graph(stems) -> int:
    """Refresh the graph rows for `stems` by reading their note frontmatter — the incremental
    path the hook calls after writing notes. Reads only the touched files (bounded), so it is
    cheap even on a huge store. Skips silently if the graph index was never built."""
    if not stems or not graph_index_ready():
        return 0
    metas = []
    for stem in stems:
        meta = m._note_meta_for_stem(stem)
        if meta:
            metas.append(meta)
    return reindex_graph(metas) if metas else 0


def graph_index_ready() -> bool:
    """True when the graph tables exist AND a full reindex has stamped them — only then is the
    SQLite graph authoritative. A never-built or partially-built index falls back to markdown."""
    if not db_path().exists():
        return False
    try:
        con = _connect()
        try:
            built = con.execute("SELECT value FROM meta WHERE key='graph_built'").fetchone()
            return bool(built and built[0] == "1")
        except sqlite3.Error:
            return False
        finally:
            con.close()
    except sqlite3.Error:
        return False


def _pfilter(project, col="project"):
    """(SQL fragment, params) for the project filter on a graph table's OWN project column —
    no join with `notes`, so the graph index works without any embeddings. '' = all projects."""
    return (f" AND {col} = ?", [project]) if project else ("", [])


def sql_etype_index(project: str | None = None) -> dict:
    """entity -> newest type, from note_etype (Brain F4)."""
    con = _connect()
    try:
        frag, params = _pfilter(project)
        rows = con.execute(
            f"SELECT entity, etype FROM note_etype WHERE 1=1{frag} ORDER BY date", params).fetchall()
        out = {}
        for ent, typ in rows:           # ascending date → last write (newest) wins
            out[ent] = typ
        return out
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def sql_entities_by_type(etype: str, project: str | None = None) -> list:
    """Entities whose NEWEST type == etype (consistent with the markdown semantics)."""
    et = etype.strip().lower()
    return sorted(e for e, t in sql_etype_index(project).items() if t == et)


def sql_stems_for_entity(entity: str, project: str | None = None) -> list:
    """Stems of live notes tagged with `entity` — the fast filter behind notes_for_entity."""
    con = _connect()
    try:
        frag, params = _pfilter(project)
        rows = con.execute(
            f"SELECT stem FROM note_entities WHERE entity = ?{frag}", [entity] + params).fetchall()
        return [r[0] for r in rows]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def sql_co_occurring(entity: str, project: str | None = None, k: int = 10) -> list:
    """[(entity, shared_notes)] entities sharing a note with `entity`, strongest first."""
    con = _connect()
    try:
        frag, params = _pfilter(project, "a.project")
        rows = con.execute(
            "SELECT b.entity, COUNT(*) c FROM note_entities a "
            "JOIN note_entities b ON a.stem = b.stem AND b.entity <> a.entity "
            f"WHERE a.entity = ?{frag} GROUP BY b.entity ORDER BY c DESC, b.entity LIMIT ?",
            [entity] + params + [k]).fetchall()
        return [(e, c) for e, c in rows]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def sql_related_by(entity: str, rel: str | None = None, project: str | None = None,
                   k: int = 20) -> list:
    """[{rel, target, notes}] typed edges declared by notes about `entity`, self-edges
    excluded (target != entity), optionally filtered to one `rel`. Mirrors graph._edge_counts."""
    con = _connect()
    try:
        frag, params = _pfilter(project, "ne.project")
        rel_sql, rel_p = (" AND r.rel = ?", [rel]) if rel else ("", [])
        rows = con.execute(
            "SELECT r.rel, r.target, COUNT(*) c FROM note_entities ne "
            "JOIN note_relations r ON r.stem = ne.stem "
            f"WHERE ne.entity = ? AND r.target <> ?{rel_sql}{frag} "
            "GROUP BY r.rel, r.target ORDER BY c DESC, r.rel, r.target LIMIT ?",
            [entity, entity] + rel_p + params + [k]).fetchall()
        return [{"rel": rl, "target": tg, "notes": c} for rl, tg, c in rows]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _where(cross: bool, alias: str = "") -> str:
    """The project filter, optionally table-qualified (for the FTS JOIN)."""
    p = f"{alias}." if alias else ""
    return (f"{p}project IS NOT NULL AND {p}project <> '' AND {p}project <> ?" if cross
            else f"{p}project = ?")


def _rows_to_cands(rows) -> list:
    """Rows in `_CAND_COLS` order → [(stem, record)] shaped like embed-cache entries
    so the retrieval ranker is identical to the JSON path."""
    out = []
    for (stem, proj, nt, title, descr, prev, rec, resolved, conf, salience, vec) in rows:
        r = {"vec": _unpack(vec), "ntype": nt, "project": proj, "title": title,
             "desc": descr or "", "prevention": prev or "",
             "recurrence": rec or 1, "resolved": bool(resolved)}
        if conf is not None:
            r["confidence"] = conf
        if salience:
            r["salience"] = salience          # Brain F5: read back as the ranking nudge
        out.append((stem, r))
    return out


def candidate_count(project: str, cross: bool = False) -> int:
    """How many candidates the project filter selects — lets the caller decide
    whether to FTS-prefilter (improvement P1) without unpacking a single vector."""
    con = _connect()
    try:
        return con.execute(f"SELECT COUNT(*) FROM notes WHERE {_where(cross)}",
                           (project,)).fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def iter_candidates(project: str, cross: bool = False,
                    query: str | None = None, limit: int | None = None) -> list:
    """[(stem, rec)] candidate notes for ranking, rec shaped exactly like an
    embed-cache record so the retrieval code is identical to the JSON path.
    Project filtering happens IN SQL (audit C2). When `query`+`limit` are given and
    FTS5 is present, only the top-`limit` lexical (bm25) matches are unpacked as
    cosine candidates (improvement P1), so per-prompt cost is bounded by `limit`
    instead of the project's full size. Recall tradeoff: a purely-semantic hit with
    no shared query token is excluded — the caller only enables this once a project
    is large enough that a full scan would stall the prompt. Without FTS, `limit` is
    ignored and a full (correct, slower) scan runs."""
    cols = ", ".join(_CAND_COLS)
    con = _connect()      # busy_timeout: ride out a concurrent write, don't error (audit A9)
    try:
        has_fts = bool(query and limit) and _has_fts(con)
        if has_fts:
            colsn = ", ".join("n." + c for c in _CAND_COLS)
            terms = " OR ".join(_safe_terms(query)) or '""'
            sql = (f"SELECT {colsn} FROM notes_fts JOIN notes n ON n.stem = notes_fts.stem "
                   f"WHERE notes_fts MATCH ? AND {_where(cross, 'n')} "
                   f"ORDER BY bm25(notes_fts) LIMIT ?")
            try:
                rows = con.execute(sql, (terms, project, limit)).fetchall()
            except sqlite3.Error:
                rows = []
            if rows:
                return _rows_to_cands(rows)
            # query had no lexical match → bounded, deterministic fallback (most
            # recurring first) instead of an arbitrary cap or a full scan
            rows = con.execute(
                f"SELECT {cols} FROM notes WHERE {_where(cross)} "
                "ORDER BY recurrence DESC LIMIT ?", (project, limit)).fetchall()
            return _rows_to_cands(rows)
        rows = con.execute(f"SELECT {cols} FROM notes WHERE {_where(cross)}",
                           (project,)).fetchall()
    finally:
        con.close()
    return _rows_to_cands(rows)


def _hit(row, score):
    return {"score": round(score, 3), "stem": row[0], "project": row[1],
            "ntype": row[2], "title": row[3], "description": row[4],
            "prevention": row[5]}


def search(query: str, project: str | None = None, k: int = 10):
    """Semantic (cosine over project-filtered BLOBs) with an FTS5 lexical
    fallback when the embedder is unavailable. Mirrors memory_search.search_core,
    backed by SQLite. Returns (results, mode). The CLI/diagnostic entry point;
    the hook's hot path uses iter_candidates() + the shared ranker instead."""
    if not index_exists():
        return [], "no-index"
    # Self-migrate a stale-format index before _unpack reads it (CLI path) — but only
    # when the cache can repopulate it; never rebuild to empty and throw away working
    # lexical (FTS) data. If we can't migrate, skip the (garbage) semantic branch and
    # let the format-independent FTS lexical fallback answer (critic round 3).
    stale = (index_meta() or {}).get("vec_format") != VEC_FORMAT
    if stale and m.load_embed_cache():
        build()
        stale = False
    qvec = (m.embed_text(query, kind=m.query_embed_kind())
            if (not stale and m.embed_cache_usable() and m.embedder_available(2)) else None)
    con = _connect()      # busy_timeout, and the semantic branch is guarded (audit A9)
    try:
        if qvec:
            try:
                sql = ("SELECT stem, project, ntype, title, descr, prevention, "
                       "recurrence, confidence, vec FROM notes")
                params = ()
                if project:
                    sql += " WHERE project = ?"
                    params = (project,)
                sims = [(m.cosine(qvec, _unpack(r[8])), r) for r in con.execute(sql, params)]
                amb = m._ambiguity(sorted((s for s, _ in sims), reverse=True))  # adaptive recurrence
                scored = []
                for sim, row in sims:
                    if sim > SIM_FLOOR:
                        boost = 0.0003 * math.log(max(1, int(row[6] or 1))) * amb  # log prior × ambiguity
                        conf = row[7]
                        mult = 1.0 if conf is None else (0.6 + 0.4 * max(0.0, min(1.0, conf)))
                        scored.append(((sim + boost) * mult, row))
                scored.sort(key=lambda x: -x[0])
                return [_hit(r, s) for s, r in scored[:k]], "semantic"
            except sqlite3.Error:
                return [], "semantic-unavailable"
        # lexical fallback via FTS5
        try:
            terms = " OR ".join(t for t in _safe_terms(query)) or '""'
            sql = ("SELECT n.stem, n.project, n.ntype, n.title, n.descr, n.prevention, "
                   "n.recurrence, bm25(notes_fts) FROM notes_fts "
                   "JOIN notes n ON n.stem = notes_fts.stem "
                   "WHERE notes_fts MATCH ?")
            params = [terms]
            if project:
                sql += " AND n.project = ?"
                params.append(project)
            sql += " ORDER BY bm25(notes_fts) LIMIT ?"
            params.append(k)
            rows = con.execute(sql, params).fetchall()
            return [_hit(r, -float(r[7])) for r in rows], "lexical(fts)"
        except sqlite3.Error:        # any FTS error (incl. an edge build choking on '""'), not just Operational
            return [], "lexical-unavailable"
    finally:
        con.close()


def _safe_terms(query: str):
    """Alphanumeric tokens for an FTS5 MATCH (avoids syntax errors on punctuation)."""
    return list(m._tokens(query))[:24]      # m._tokens is always defined (dropped dead fallback, audit)


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "build":
        build(verbose=True)
        return 0
    if not args:
        print('usage: index_sqlite.py build | index_sqlite.py "<query>" [project]',
              file=sys.stderr)
        return 1
    query = args[0]
    project = args[1] if len(args) > 1 else None
    results, mode = search(query, project)
    if mode == "no-index":
        print("[index] no .index.sqlite — run: python index_sqlite.py build", file=sys.stderr)
        return 1
    print(f"{len(results)} hit(s) for {query!r} ({mode}):")
    for r in results:
        print(f"  {r['score']:6.3f} [{r['project']}/{r['ntype']}] {r['title']}  ({r['stem']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
