# -- engine part 6 of 8: note reading, the project card, the entity graph and cards, the index, the core pipeline and the status file --
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
# Lines 5417-6687 of the pre-split `_engine.py`, whose body these parts reproduce byte for byte
# (sha256 bddf5d32a8883f0fdcfbd420de58e66fc9151be1ef05c696dedb44eefd0ac40f). Order is
# load-bearing: module-level code below runs after every earlier part and before every later
# one. `tests/_engine_source.py` reconstructs the whole body from these files.
#<<<ENGINE-PART-BODY>>>
# ── Structured project card (audit I-15) ──────────────────────────────
# A distilled, regenerated rollup of a project's live notes, kept at the top of
# Context/<project>.md. Replaces the raw journal tail as the SessionStart
# injection surface: organised by KIND of fact (status / stack / open gotchas /
# decisions / recurring) instead of chronologically - bounded, deterministic,
# GPU-free (no LLM). Cheaper to inject, higher signal per token than the journal.

def _one_line(s: str, limit: int) -> str:
    """Collapse whitespace to a single line and cap at `limit` - at a SENTENCE boundary
    when one exists past 40% of the budget, else at a word boundary with a truncation
    marker. The old hard `s[:limit]` slice amputated card Status lines mid-word and
    mid-noun-phrase («подтверждена работа всех пяти» - five WHAT?), and nothing told the
    reader the final clause carried no information (review 2026-08). A sentence-boundary
    cut needs no marker: what is shown is a complete statement; a word-boundary cut gets
    '…' so an amputated line can never masquerade as a complete one."""
    s = re.sub(r"\s+", " ", (s or "")).strip()
    if len(s) <= limit:
        return s
    head = s[:limit - 1]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "), head.rfind("; "))
    if cut >= int(limit * 0.4):
        return head[:cut + 1]
    sp = head.rfind(" ")
    return (head[:sp] if sp >= int(limit * 0.4) else head).rstrip() + "…"


def _read_frontmatter(text: str) -> tuple[dict, str]:
    """(frontmatter dict, body). Inline-JSON values are parsed back to their type -
    arrays to lists (entities/relations), maps to dicts (entity_types) - and double-quoted
    scalars unquoted; everything else stays a raw string. Tolerant of a missing/short
    frontmatter - used by the project-card distillation."""
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
            except (ValueError, RecursionError):
                pass                        # a deeply-nested value is not parseable metadata; keep
                                            # the raw string rather than let a stack overflow abort
                                            # every vault-wide scan that reads this note (critic R3)
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


#: Lines that are a typed note's STRUCTURE rather than its statement. The bold arm is the reason
#: this exists: the exclusion list used to hold a bare `"**"`, which reads every emphasised opening
#: as markup - a lesson beginning "**Never** commit on a red suite" parsed with an EMPTY
#: description. `_same_replacement` then reached its empty-old-statement shortcut, concluded the
#: note "carries nothing to preserve", and let the next note on the same topic absorb it
#: unconditionally: a still-true statement overwritten because the parser would not read it
#: (T1 review 2026-09-19). Only a `**Label:**` lead is structure - `**Prevention:**`,
#: `**Project:**`, `**Date:**`, the legacy `**Как избежать:**`. A statement that genuinely opens
#: `**Always:** ...` is still excluded, which is the narrow price of not being able to tell it
#: apart from a label.
_STRUCTURAL_LEAD = _lazy_re(r"(?:\*\*[^*]{1,40}:\*\*)|[#\-_|]|\[\[")


def _parse_note_body(lines) -> tuple[str, str, str]:
    """The ONE body parser for a typed note → (title, desc, prevention). Title is the first
    `# ` heading (icon-stripped, "" if none); desc is the first plain line after it; prevention
    is the `**Prevention:**` line (the legacy `**Как избежать:**` marker from pre-2.2.1 notes
    is accepted forever - the store is the user's data, never migrated). _note_meta,
    _note_snippet, and embed_index.note_fields all ride this so the copies can't drift again
    (code-review 2026-07: _note_snippet's exclude-tuple had already lost "---" and could pick
    a horizontal rule as the description)."""
    title, desc, prevention, seen = "", "", "", False
    for ln in lines:
        s = ln.strip()
        if s.startswith("# "):
            title = _strip_lead_icon(s.lstrip("# "))
            seen = True
            continue
        if not seen or not s:
            continue
        if s.startswith("**Prevention:**"):
            prevention = s.replace("**Prevention:**", "").strip()
        elif s.startswith("**Как избежать:**"):          # legacy marker, dual-read
            prevention = s.replace("**Как избежать:**", "").strip()
        elif not desc and not _STRUCTURAL_LEAD.match(s):
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
        # `_coerce_recurrence` is THE one parse rule, and it carries RECUR_COUNT_CAP - the
        # anti-poisoning ceiling. Reading the field raw here let one hand-edited or imported
        # note claiming `recurrence: 999999999` outrank the whole store on every metadata
        # surface: the project card's three slot sorts, its `x N` label, api.notes, causal.
        rec = _coerce_recurrence(fm.get("recurrence", 1))
    except ValueError:
        rec = 1
    return {"stem": p.stem, "ntype": ntype, "date": parsed["date"],
            "title": title.strip(), "desc": desc, "prevention": prevention,
            "tags": _norm_tags(raw_tags), "resolved": resolved, "recurrence": rec,
            # cap=16, NOT the default 8: the write path stores up to 8 extracted
            # entities PLUS up to 8 appended relation targets ('reachable by
            # construction'); re-capping at 8 on read silently dropped exactly the
            # appended targets, resurrecting the dangling-edge LAW-1 violation the
            # write-side merge exists to prevent (review 2026-08)
            "entities": _norm_entities(fm.get("entities"), cap=16),
            "relations": _norm_relations(fm.get("relations")),
            "entity_types": _norm_entity_types(fm.get("entity_types"), gate=False),
            "salience": _coerce_salience(fm.get("salience")),
            # M-10 stamps a per-note confidence and the RANKER reads it off the embed-cache
            # record, but the metadata layer dropped it - so every read surface built on note
            # metadata (digest, dashboard, graph, lenses) was blind to how sure the memory is
            # of what it knows. None = unstated, which callers treat as fully confident.
            "confidence": _coerce_confidence(fm.get("confidence")),
            # the writing session, so a restatement can be told from a recurrence
            # (`_collapse_restatements`); "" on notes written before the field existed
            "session": str(fm.get("session") or ""),
            "superseded_by": str(fm.get("superseded_by") or ""),    # "" for live notes (F3 timeline)
            "contested": _contested_of(fm)}                          # K8: sibling stems the judge must read


def _coerce_salience(v) -> float:
    """A stamped salience (Brain F5) clamped to [0,1]; 0.0 when absent/garbage so an unstamped
    note is neutral in ranking - the boost is inert until consolidation scores the graph."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(f) else max(0.0, min(1.0, f))   # NaN-safe clamp


def _note_meta_for_stem(stem: str) -> dict | None:
    """Note meta for a bare stem - the SQLite graph upsert (F4) reads only the touched files
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
    superseded_by - the history live recall hides, for the F3 entity timeline. O(superseded)
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
    """entity -> [superseded note metas], one scan - shared across an entity-card refresh so a
    bulk pass reads the Superseded/ folders once, not once per entity (F3)."""
    idx: dict = {}
    for n in _iter_superseded_notes(project):
        for e in n.get("entities") or []:
            idx.setdefault(e, []).append(n)
    return idx


def _iter_project_notes(project: str) -> list[dict]:
    """All live (non-archived, non-superseded) typed notes for a project, as
    metadata dicts. Flat glob → Superseded/ and Archive/ subdirs are skipped.
    The project arg is slugged here - every WRITER normalizes via slug_project, so a
    raw caller value ('My-App') matched nothing on disk ('my_app') and the entity/
    graph read surfaces silently returned empty (review 2026-08 C3)."""
    project = slug_project(project)
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
# The graph lives in nevertwice/graph.py (kept out of this module to keep it lean). It is
# re-exported here so `m.entity_index(...)` etc. keep working; graph.py imports memory_hook
# lazily-safe (only inside function bodies, never at import time), so this re-export does
# not deadlock the circular import.
_graph = _sibling("graph")
entity_index = _graph.entity_index
entity_types_index = _graph.entity_types_index     # Brain layer (F1): entity -> type
entities_by_type = _graph.entities_by_type
entity_timeline = _graph.entity_timeline           # Brain layer (F3): live+superseded history
salience_index = _graph.salience_index             # Brain layer (F5): graph-centrality salience
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
    body = f" - {_one_line(snip, 160)}" if snip else ""
    return f"- {mark}**{_one_line(n.get('title', ''), 80)}**{rec}{body}"


def build_project_card(project: str, status_hint: str = "") -> str:
    """Distil a project's live notes into a structured card (audit I-15):
    status · stack · open gotchas · key decisions · recurring lessons. Pure
    structural rollup - deterministic, GPU-free, no LLM. Returns the full block
    (START/END markers included) or '' when there is nothing worth showing."""
    notes = _iter_project_notes(project)
    section = []

    status = _one_line(status_hint, 200)
    if not status and notes:
        status = _one_line(max(notes, key=lambda n: (n["date"], n["stem"]))["title"], 200)
    if status:
        section.append(f"**Status:** {status}")

    freq = {}
    for n in notes:
        for t in n["tags"]:
            if t in _CARD_TAG_STOP or t.startswith("project/"):
                continue
            freq[t] = freq.get(t, 0) + 1
    stack = [t for t, _ in sorted(freq.items(), key=lambda x: (-x[1], x[0]))[:8]]
    if stack:
        section.append("**Stack/topics:** " + ", ".join(stack))

    # Card-slot ranking (review 2026-08): dates are day-granular, so a date-only key over
    # unsorted glob order made the five decision slots "the alphabetically-first five of
    # today" - a busy day (13 same-day decisions) evicted the deliberate hostname-filter
    # decision in favor of a bullet topically redundant with the slot above it, and the
    # card churned to a new alphabetical top-5 every commit. Ties now break by how EARNED
    # a note is (recurrence, then stated confidence), and finally by stem so the order is
    # deterministic rather than filesystem-dependent.
    def _slot_key(n):
        conf = n.get("confidence")
        return (n["date"], n.get("recurrence", 1) or 1,
                conf if conf is not None else 1.0, n["stem"])

    open_gotchas = sorted((n for n in notes if n["ntype"] == "mistake" and not n["resolved"]),
                          key=lambda n: (n.get("recurrence", 1) or 1, n["date"],
                                         n.get("confidence") if n.get("confidence") is not None
                                         else 1.0, n["stem"]),
                          reverse=True)
    decisions = sorted((n for n in notes if n["ntype"] == "decision"),
                       key=_slot_key, reverse=True)
    open_stems = {n["stem"] for n in open_gotchas[:CARD_MAX_ITEMS]}
    recurring = sorted((n for n in notes if n["recurrence"] >= 2 and n["stem"] not in open_stems),
                       key=lambda n: (n.get("recurrence", 1) or 1, n["date"], n["stem"]),
                       reverse=True)

    blocks = []
    if open_gotchas:
        blocks.append(["", "**⚠️ Open pitfalls (do not repeat):**"]
                      + [_card_item(n) for n in open_gotchas[:CARD_MAX_ITEMS]])
    if decisions:
        blocks.append(["", "**🎯 Key decisions:**"]
                      + [_card_item(n) for n in decisions[:CARD_MAX_ITEMS]])
    if recurring:
        blocks.append(["", "**🔁 Recurring (recurrence≥2):**"]
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
    mt = re.search(r"^##\s+(\d{4}-\d{2}-\d{2}|Накопленное состояние|Accumulated state)", text, flags=re.M)
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
    labels = tuple(NTYPE_LABEL.values()) + _NTYPE_LABEL_LEGACY
    hint = ""
    for entry in reversed(entries):
        if not re.match(r"^##\s+\d{4}-\d{2}-\d{2}", entry):
            continue
        for ln in entry.split("\n")[1:]:
            s = ln.strip()
            if s and not s.startswith(("Session:", "Сессия:", "[[", "**", "#")) \
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
# GPU-free rollup as the project card - but stored as a standalone file under Entities/, which
# is NOT a TYPE_FOLDER, so a card NEVER enters the recall pool (separation invariant). Pull-only:
# read via api.entity_card / the MCP surface / an explicit request, never auto-injected.
ENTITIES_FOLDER = "Entities"


def _entity_card_stem(entity: str, etype: str) -> str:
    """Filename stem for an entity card: '<type>-<entity>' (both already kebab tokens), so a
    regenerated card overwrites its predecessor instead of duplicating. '' for a junk entity."""
    e = _norm_entities([entity])
    t = re.sub(r"[^a-z0-9-]", "", (etype or "entity").lower()) or "entity"
    return f"{t}-{e[0]}" if e else ""


def _existing_entity_card(entity: str) -> Path | None:
    """An existing card for this entity under ANY type prefix, or None.

    The stem bakes the CURRENT type label, and the type is re-resolved on every refresh,
    so a re-label -- or a typo -- used to mint a NEW card and orphan the old one forever.
    The docstring's promise that "a regenerated card overwrites its predecessor instead of
    duplicating" was false: one Dart class ended up as concept-, tool- AND method- cards
    with disjoint edges and contradictory resolution state, and refresh_entity_cards
    iterates one type per entity, so every orphan stayed frozen (review 2026-09).

    Reusing the existing path keeps one card per entity across a re-label. The type shown
    inside the card still follows the index; only the filename stops multiplying.
    """
    d = VAULT / "Entities"
    if not d.exists():
        return None
    norm = _norm_entities([entity])
    if not norm:
        return None
    # Split on the FIRST hyphen: the type is one token, the entity may contain hyphens.
    # A plain endswith() would let the entity "service" hijack "report-service"'s card.
    hits = sorted(q for q in d.glob(f"*-{norm[0]}.md")
                  if q.stem.split("-", 1)[1:] == [norm[0]])
    return hits[0] if hits else None


def _collapse_restatements(notes: list[dict]) -> list[dict]:
    """One observation stored twice is one observation, not two corroborations.

    An entity card's note count is read as strength of evidence. The vault review found
    the same fact filed as BOTH a pattern and a decision -- same slug, same date, same
    session -- and counted twice; and three live decisions asserting the identical result
    with no supersede between them, which the card then presented as three confirmations.
    For a project being prepared for arXiv, where independent confirmation is
    load-bearing, duplication inflated into corroboration is the more dangerous direction
    of error, and this vault already holds a mistake note about exactly that.

    Collapse on (project, slug, date, session): together they identify one observation
    written more than once. Notes that merely share a slug across different sessions are
    genuine recurrences and are left alone -- that signal is what recurrence exists for.
    The project is in the key because the entity pool is cross-project: two projects that
    hit the same lesson on the same day are two observations, and a key without the
    project silently dropped one of them from the card (review 2026-09-05). The session
    comes from the note's own frontmatter, which `_note_meta` now reads; without it the
    third element was always "" and the discriminator the docstring promised never existed.
    """
    seen: set[tuple] = set()
    out: list[dict] = []
    for n in notes:
        stem = n.get("stem") or ""
        parsed = parse_typed_stem(stem) or {}
        key = (parsed.get("project") or n.get("project") or "",
               parsed.get("slug") or stem, parsed.get("date") or "", n.get("session") or "")
        if key[1] and key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


def build_entity_card(entity: str, etype: str | None = None, idx: dict | None = None,
                      sup: dict | None = None) -> str:
    """Distil every live note tagged with `entity` (across ALL projects) into a standalone
    markdown card: type · where-used · typed neighbours · co-occurring entities · lessons grouped
    by kind, a first/last-seen line, and (F3) the EVOLUTION of the take - where an earlier note was
    later superseded. Deterministic structural rollup - no LLM, no embedder. Returns the full file
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
    # A card's note count reads as strength of evidence, so one observation stored twice
    # must not read as two. See _collapse_restatements.
    notes = _collapse_restatements(notes)
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
        rel_bits.append("nearby: " + ", ".join(cooc[:6]))

    by_type = {"mistake": [], "pattern": [], "decision": []}
    for n in notes:
        if n["ntype"] in by_type:
            by_type[n["ntype"]].append(n)
    lines = [f"# 🧠 {ent} · {etype}", "",
             f"**Where it appears:** {', '.join(projects)}  ({len(notes)} live notes)"
             if projects else f"**Live notes:** {len(notes)}"]
    if rel_bits:
        lines.append("**Related:** " + "  ·  ".join(rel_bits))
    if first_seen:
        span = f"_First seen: {first_seen} · last seen: {last_seen}"
        # `mentions` counts live AND superseded notes (graph.py), while the note count
        # above counts live ones only. Printed side by side unlabelled they read as one
        # quantity, and 202 of 2739 cards disagreed - one was born at "3 notes /
        # 9 mentions" (review 2026-09). Each label now says what it counts.
        _mentions = tl.get("count", len(notes)) if tl else len(notes)
        _incl = " incl. superseded" if tl and _mentions > len(notes) else ""
        span += f" · mentions: {_mentions}{_incl}_" if tl else "_"
        lines.append(span)
    for kind, label in (("mistake", "**⚠️ Pitfalls:**"), ("pattern", "**✅ Patterns:**"),
                        ("decision", "**🎯 Decisions:**")):
        if by_type[kind]:
            lines += ["", label] + [_card_item(n) for n in by_type[kind][:CARD_MAX_ITEMS]]
    if evo:                                              # F3: how the understanding changed
        lines += ["", "**🕓 How the understanding evolved:**"]
        for r in evo[-CARD_MAX_ITEMS:]:
            nd = (parse_typed_stem(r.get("superseded_by", "")) or {}).get("date", "")
            arrow = f" → revised {nd}" if nd else " → revised later"
            lines.append(f"- {r.get('date', '')}: «{_one_line(r.get('title', ''), 70)}»{arrow}")
    return fm_block(fm) + "\n" + "\n".join(lines) + "\n"


def write_entity_card(entity: str, etype: str | None = None, idx: dict | None = None,
                      sup: dict | None = None) -> str:
    """(Re)generate ONE entity card under Entities/ and write it atomically, only on change.
    Returns the stem when a card was actually WRITTEN, or '' when there was nothing to write -
    unchanged (idempotent no-op), no notes, junk entity, or no brain profile active. The 'only
    on change' return is what keeps a full refresh from churning git on a no-op pass."""
    if not _cfg.brain_enabled():
        return ""
    norm = _norm_entities([entity])
    if not norm:
        return ""
    etype = etype or entity_types_index().get(norm[0], "entity")
    # Reuse the card this entity already has, whatever prefix it was minted under, so a
    # re-label updates one file instead of minting a third and orphaning two.
    _prior = _existing_entity_card(norm[0])
    stem = _prior.stem if _prior is not None else _entity_card_stem(norm[0], etype)
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
    typed entities (the ones a session touched - the per-session path); otherwise refresh EVERY
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
    yet. '' when the entity has no notes. The pull-only read surface for api / MCP / search -
    this is how Brain knowledge reaches an agent: by explicit request, never by injection."""
    norm = _norm_entities([entity])
    if not norm:
        return ""
    ent = norm[0]
    # Cache hit: a card file is named "<type>-<entity>.md". Glob by the entity suffix and confirm
    # via its `name` so we don't pay a corpus-wide entity_types_index() scan just to locate a file
    # already on disk (the glob alone is ambiguous - "method-gears" vs an entity "some-gears").
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
        # Per-session idempotency (review 2026-08): the B1 grown-transcript
        # re-process (PreCompact then SessionEnd) called this twice for the same
        # session, appending duplicate '## date time' blocks. Replace this
        # session's earlier entry instead - matched on the exact 'Session: [[..]]'
        # marker line, so the Accumulated-state link archive is never touched.
        marker = f"Session: [[{session_link}]]"
        if marker in existing:
            try:
                head_, entries_ = _split_context(existing)
                kept = [e for e in entries_ if marker not in e]
                if len(kept) != len(entries_):
                    existing = (head_.rstrip() + "\n\n"
                                + ("\n\n".join(kept) + "\n" if kept else ""))
                    log(f"Context entry refreshed (same session): {project}")
            except Exception:
                pass
    else:
        body_tags = render_body_tags(tags, [f"project/{project}", "context"])
        existing = "\n".join([
            fm_block({"project": project, "tags": tags, "type": "context",
                      "date": date}),
            "",
            f"# Context: {project}",
            "",
            "Living project context. Updated automatically by the hook after every session.",
            "",
            body_tags,
            "",
            "---",
            "",
        ])

    # Bound a single entry: nothing capped `update`, so one oversized LLM output
    # could blow the whole context budget in one append (review 2026-08).
    if len(update) > CONTEXT_ENTRY_MAX_CHARS:
        update = truncate_smart(update, CONTEXT_ENTRY_MAX_CHARS)
    entry = ["", f"## {date} {time_str}", update, "", f"Session: [[{session_link}]]"]
    for nt in TYPED_TYPES:
        items = links.get(nt, [])
        if items:
            entry.append(f"{NTYPE_LABEL[nt]}: " + ", ".join(f"[[{x}]]" for x in items))

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
        fm_block({"type": "index", "title": "Nevertwice memory store",
                  "description": "Long-term agent memory - typed notes (patterns/"
                                 "mistakes/decisions), per-project cards, sessions."}),
        "",
        "# Claude Memory Vault - Index",
        "",
        "> Entry point. An agent reads ONLY this file at session start.",
        "> Do not scan every folder - navigate via the wikilinks from this index.",
        "> Open Knowledge Format bundle: markdown + YAML frontmatter, every " +
        "note carries `type`; navigate via `[[wikilinks]]` and `graph.json`.",
        "",
        "## Layout",
        "",
        "| Folder | What lives there |",
        "|---|---|",
        "| Patterns/ | Approaches that worked |",
        "| Mistakes/ | Mistakes, bugs, antipatterns - what to avoid |",
        "| Decisions/ | Architectural decisions with reasoning |",
        "| Context/ | Each project's state (one file = one project) |",
        "| Sessions/ | Session auto-logs (last 30 days) |",
        "",
        "## Active projects",
        "",
    ]
    if projects:
        lines += ["| Project | Updated |", "|---|---|"]
        lines += [f"| [[{name}]] | {mtime} |" for name, mtime in projects]
    else:
        lines.append("_(no projects yet)_")
    lines += ["", "## Recent sessions", ""]
    if sessions:
        lines += [f"- **{mtime}** - [[{name}]]" for name, mtime in sessions]
    else:
        lines.append("_(no sessions yet)_")
    lines += ["", "#index"]

    write_atomic(fp, "\n".join(lines))
    log("Index rebuilt")


# ── Core pipeline ─────────────────────────────────────────────────────

#: K5 - extra extraction calls when a non-empty, relevant session came back with zero items. The
#: extractor's output on a bare two-sentence fact is unstable at temperature zero: the same text
#: yields a note or nothing depending on incidental prompt context (ledger K3: of eight silent
#: first sessions, three were silent in both runs and five in one; captured alone six of eight
#: wrote the note). A retry with a differently seeded frame is one more call, only on silence.
#: Off by default since the campaign at d07375e (2026-09-12): the retry took the as-of stand's never-
#: written first sessions from 11 to 8 of 120 against a gate of 5 - a third of the silence, not the
#: half the gate asked for - so K5's own rule reverts it. Opt in with NEVERTWICE_EXTRACT_RETRY=1.
EXTRACT_RETRY = env_int("NEVERTWICE_EXTRACT_RETRY", 0)
_RETRY_MIN_CHARS = 40            # a shorter body has nothing to extract; silence is the right answer
_RETRY_FRAME = (
    "Second pass. The first pass over this session returned no pattern, mistake or decision. A short "
    "session that states one concrete fact, choice or lesson still yields ONE item: record it as a "
    "decision (a fact or choice that now holds) or a pattern (a lesson), with its literal in `facts`. "
    "Return the same JSON shape as specified below.\n\n")


def _item_count(extraction) -> int:
    if not isinstance(extraction, dict):
        return 0
    return sum(len(v) for nt in TYPED_TYPES if isinstance((v := extraction.get(f"{nt}s")), list))


def _retry_if_silent(extraction: dict, prompt: str, body: str, project_hint: str) -> dict:
    """K5: when a relevant, non-trivial session came back with zero items, ask once more with the
    prompt framed as a second pass. Not a prompt rewrite - the first call is unchanged, and the frame
    is only what makes the second call a different sample at temperature zero. An off-topic session
    (`project_relevant` false) is not retried: empty beats wrong. The second answer replaces the
    first only when it carries an item; otherwise the silence stands, counted."""
    if EXTRACT_RETRY <= 0 or not isinstance(extraction, dict) or _item_count(extraction) > 0:
        return extraction
    if not _is_relevant(extraction.get("project_relevant", True)) or len((body or "").strip()) < _RETRY_MIN_CHARS:
        return extraction
    _LLM_STATS["retry"] = _LLM_STATS.get("retry", 0) + 1
    second = generate_json(_RETRY_FRAME + prompt, project=project_hint)
    if _item_count(second) > 0:
        log(f"Extraction retry (empty first pass): {_item_count(second)} item(s) on the second pass")
        _LLM_STATS["retry_hit"] = _LLM_STATS.get("retry_hit", 0) + 1
        return second
    log("Extraction retry (empty first pass): still no item - the silence stands")
    return extraction


def process_session(session_id: str, cwd: str, transcript_path: str,
                    trigger: str, processed_db: dict,
                    run_log: list | None = None, agent: str | None = None,
                    transcript_text: str | None = None,
                    project_override: str | None = None,
                    timestamp: str | None = None) -> bool:
    refresh_lock()      # every long lock holder runs per-transcript through here:
    #                     keep the mtime fresh so a live sweep is never "stale-stolen"
    # Resolved HERE, not in the signature: a default argument is evaluated once at
    # def time, so a module constant frozen there stops answering to the module.
    agent = DEFAULT_AGENT if agent is None else agent
    prior = processed_db.get(session_id)
    if prior is not None:
        # Growth check (review 2026-08 / B1): a PreCompact-marked session continues
        # under the same id; if its transcript grew past the recorded size, re-process
        # instead of skipping. The re-extraction is additive, not duplicating: typed
        # notes hit the per-session idempotency/absorb in write_typed_note, and the
        # session note refreshes in place via reserve_session_stem.
        if transcript_path and transcript_text is None \
                and _transcript_grew(prior, transcript_path):
            log(f"Re-processing {session_id[:8]} - transcript grew past the "
                f"{prior.get('bytes')}-byte mark (post-compaction tail)")
        else:
            log(f"Skip {session_id[:8]} - already processed at "
                f"{prior.get('processed_at') if isinstance(prior, dict) else '?'}")
            return False

    # Project resolution. An explicit override (generic ingestion from any agent)
    # bypasses the cwd-based gate; otherwise the cwd must be a tracked project
    # (under a configured root or a git repo) - audit C2.
    if project_override:
        project_hint = slug_project(project_override)
    elif is_tracked_project(cwd):
        project_hint = derive_project_from_cwd(cwd)
    else:
        log(f"Skip {session_id[:8]} - cwd '{cwd}' is not a tracked project")
        mark_processed(processed_db, session_id, transcript_path)
        return False

    if transcript_text is not None:        # generic ingestion path (raw text)
        # `timestamp` lets an importer of old transcripts - or a bench placing facts in time -
        # say when the session happened; without it the ingest is dated today (ledger I6).
        parsed = {"body": transcript_text.strip(), "cwd": cwd, "timestamp": timestamp}
        # No watermark of our own: this run read no file. The sid is a content hash, so growth
        # cannot re-trigger it - but the PATH recorded beside it is a real transcript on
        # ingest's sweep, and a fabricated 0 there means every later event sees the whole file
        # as growth and re-mines the session through the extractor for good. None lets
        # `mark_processed` stat the path it was given, which is the size actually on disk.
        t_size = None
    else:
        # Size BEFORE reading: if the transcript grows during extraction, the smaller
        # recorded size makes the tail re-processable rather than silently skipped.
        try:
            t_size = os.path.getsize(transcript_path)
        except OSError:
            # `mark_processed`'s own rule for a file it cannot read is to record NO watermark;
            # a 0 invented here overrides that with "we read an empty file", and every byte
            # already on disk becomes growth.
            t_size = None
        # On a re-mine, read only what was added since the watermark. Reading from zero
        # is what let a slid window re-title the same session's notes (vault review 2026-09).
        _from = 0
        if isinstance(prior, dict) and isinstance(prior.get("bytes"), int):
            _from = max(0, int(prior["bytes"]))
        parsed = read_transcript(transcript_path, from_byte=_from)
    if not parsed["body"]:
        log(f"Empty transcript for {session_id[:8]} - marked, nothing to extract")
        mark_processed(processed_db, session_id, transcript_path)
        return False

    body = strip_injected_boilerplate(parsed["body"])   # anti-confabulation (review 2026-08)
    transcript_full = truncate_smart(
        redact_secrets(f"Working directory: {cwd}\nTrigger: {trigger}\n\n{body}"),
        MAX_TRANSCRIPT_CHARS,
    )
    facts_source = _facts_source(transcript_full)     # K6: literals come from the session, not the frame

    # Scoped to THIS project: a batch run used to hand one project the signature tags of
    # whichever project had the most notes in the vault.
    tag_vocab = collect_existing_tags(project=project_hint)
    # Ground dedup on the notes ALREADY WRITTEN FOR THIS SESSION'S DAY, not on the
    # globally newest forty. On a re-mine the newest forty can contain none of this
    # session's own notes, which is how one session forked into 72 live + 41 retired.
    _dt = _parse_iso(parsed.get("timestamp"))
    _for_date = (_dt or datetime.now()).strftime("%Y-%m-%d")
    existing = collect_existing_titles(project_hint, for_date=_for_date)

    prompt = EXTRACTION_PROMPT.format(
        transcript=transcript_full,
        project_hint=project_hint,
        tag_vocab=", ".join(tag_vocab) if tag_vocab else "(empty - pick freely)",
        existing_patterns=", ".join(existing["pattern"]) or "(none)",
        existing_mistakes=", ".join(existing["mistake"]) or "(none)",
        existing_decisions=", ".join(existing["decision"]) or "(none)",
        brain_block=_brain_prompt_block(),     # F1: typed-entity ask, "" unless a brain profile is on
        language_rule=language_rule(transcript_full),
    )
    extraction = generate_json(prompt, project=project_hint)
    if not extraction:
        log(f"Extraction failed for {session_id[:8]} - left for retry")
        # The counter whose docstring says "the 2026 stall showed up here as a flat store and
        # nowhere else". It could not have: nothing called it (T1 review 2026-09-19).
        try:
            from . import telemetry as _tel
            _tel.record_extraction_failure("no_extraction")
        except Exception:       # noqa: BLE001 - a hook never fails on telemetry
            pass
        return False
    extraction = _retry_if_silent(extraction, prompt, body, project_hint)

    # The session is marked processed at the END, AFTER its notes are durably
    # written (audit C5): a crash mid-write then RETRIES instead of losing the
    # notes. write_typed_note is idempotent per-session (it reuses a note this
    # same session already wrote), so the retry can't spawn -2/-3 duplicates -
    # which is the failure mode the round-1 "mark first" ordering guarded against.

    # .astimezone() so BOTH branches are aware - _parse_iso returns aware datetimes,
    # and mixing the naive fallback in would TypeError on any future arithmetic
    # between the two (review 2026-08 P6)
    started = _parse_iso(parsed["timestamp"]) or datetime.now().astimezone()
    date = started.strftime("%Y-%m-%d")
    time_str = started.strftime("%H:%M")

    # The extractor's `project` field is UNTRUSTED model output over untrusted
    # transcript text - honoring it let a steered (or merely confused) model file
    # notes and Context updates into another project's canon AND run the LOCAL_ONLY
    # embedding privacy gate against the renamed identity (review 2026-08 A2). The
    # cwd-derived hint (or the caller's explicit override) IS the identity; a
    # differing LLM value is logged and ignored.
    project = slug_project(project_hint)
    _llm_proj = slug_project(extraction.get("project") or "")
    if _llm_proj and _llm_proj != project:
        log(f"Extractor proposed project '{_llm_proj}' - keeping '{project}'")
    tags = extraction.get("tags")
    tags = tags if isinstance(tags, list) else []  # qwen3 may return a str (audit F5)
    tags = _norm_tags(tags)                          # canonical vocabulary (audit M5)
    summary = extraction.get("session_summary") or "Session completed"
    if not isinstance(summary, str):
        summary = str(summary)

    # Relevance gate (audit C1): file project knowledge ONLY when the session is
    # genuinely about this project. Off-topic sessions (personal troubleshooting,
    # model switches, empty chats) still get a Session note for the record, but
    # contribute NO typed notes and NO context update - zero contamination.
    relevant = _is_relevant(extraction.get("project_relevant", True))

    # The FINAL (collision-resolved) session stem, fixed before any typed note stamps it
    # as provenance - see reserve_session_stem for why pre-uniquified stems conflated
    # same-minute transcripts (review 2026-08).
    sess_stem = reserve_session_stem(date, time_str, project, session_id)

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
    outcome = {nt: {"refused": 0, "quarantined": 0, "skipped": 0} for nt in TYPED_TYPES}
    brain = _cfg.brain_enabled()
    for nt in TYPED_TYPES:
        for item in items_of(nt):
            # J3 (held-out fact_survival): carry the literal the summary drops. Verified as a
            # verbatim substring of THIS session, then appended to the description so the note,
            # the embedding and every reader keep it - a paraphrase is not a substring and vanishes.
            if isinstance(item, dict):
                _vf = _note_facts(item, facts_source)
                if _vf:
                    item["description"] = _append_facts(item.get("description", ""), _vf)
            _set_write_outcome(None)
            stem = write_typed_note(TYPE_FOLDER[nt], item, project, date, tags, nt,
                                    session_stem_=sess_stem, siblings=all_siblings)
            if not stem:                 # refused (M-10), quarantined (W7) or a retry's skip
                why = last_write_outcome()
                outcome[nt][why if why in ("quarantined", "skipped") else "refused"] += 1
                continue
            links[nt].append(stem)
            # redact BEFORE the embed path too: write_typed_note redacts what lands in the .md,
            # but these raw fields feed update_embeddings → the embeddings cache (plaintext on
            # disk) and, with a cloud embedder, the provider - the same secret the note path
            # just scrubbed would leak through the side channel (code-review 2026-07, HIGH).
            desc = redact_secrets(item.get("description", "")) if isinstance(item, dict) else ""
            prevention = redact_secrets(item.get("prevention", "")) if isinstance(item, dict) else ""
            conf = item.get("confidence") if isinstance(item, dict) else None
            # the TITLE takes the same scrub: it is embedded, cached in plaintext,
            # FTS-indexed and injected verbatim by _fact_line - the one field that
            # skipped redaction on this side channel (review 2026-08)
            new_notes.append((stem, nt, project, redact_secrets(title_of(item)),
                              desc, prevention, conf))
            if brain and isinstance(item, dict):
                touched_entities |= set(_norm_entity_types(item.get("entity_types")))

    sess_link = write_session_note(
        project, date, time_str, summary, cwd, session_id, tags, links, trigger,
        agent=agent, stem=sess_stem,
    )

    cu = extraction.get("context_update")
    if relevant and isinstance(cu, str) and cu.strip() and not _is_noise_update(cu):
        if _looks_unsafe(cu):
            # The one write surface that skipped the M-10/W8 injection screen (review
            # 2026-08 S1): context_update feeds the project card, which _context_brief
            # injects as the FIRST block of every future SessionStart - the exact
            # persistence vector the typed-note gate exists to close.
            log(f"Rejected context_update (unsafe payload): {cu[:60]!r}")
        else:
            update_context(project, redact_secrets(cu), tags, date, time_str,
                           links, sess_link)

    if new_notes:
        update_embeddings(new_notes)

    # distil the structured project card from this session's writes (audit I-15).
    # Runs whenever the session was project-relevant - independent of whether a
    # context_update fired - so a notes-only session still refreshes the card.
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
    # so the retry reuses them instead of duplicating. The size recorded is the one
    # captured at read time (B1: growth during extraction stays re-processable).
    mark_processed(processed_db, session_id, transcript_path, size=t_size)

    # fold this session's writes into the grounding caches so the next session in
    # a multi-session sweep needs no full disk rescan (audit M-k)
    register_written_notes(project, tags, links)

    counts = {nt: len(links[nt]) for nt in TYPED_TYPES}
    #: What the extractor PROPOSED, beside what was written. Without it a caller cannot tell
    #: "the extractor returned nothing" from "it returned items and every one was refused", and
    #: those lead to opposite conclusions: the first is a property of the workload, the second is
    #: a defect. A stand measuring the cost of memory has to print zero only when it can say
    #: which - a zero it cannot explain sells a bug as a design (asked for by the auditing
    #: session while writing `research/token_floor.py`, 2026-09-22).
    #: Counted from the extraction itself, not through `items_of`: the relevance gate empties
    #: that list, and an off-topic session then read as "the extractor proposed nothing" - the
    #: confusion this field exists to remove (review 2026-09-23, #2). What the gate dropped is
    #: `off_topic`; what the write path did not write is split by why (#12). The five add up to
    #: `proposed` - written + refused + quarantined + skipped + off_topic.
    proposed = {nt: (len(v) if isinstance(v := extraction.get(f"{nt}s"), list) else 0)
                for nt in TYPED_TYPES}
    off_topic = {nt: (0 if relevant else proposed[nt]) for nt in TYPED_TYPES}
    refused = {nt: outcome[nt]["refused"] for nt in TYPED_TYPES}
    quarantined = {nt: outcome[nt]["quarantined"] for nt in TYPED_TYPES}
    skipped = {nt: outcome[nt]["skipped"] for nt in TYPED_TYPES}
    _unwritten = [(k, sum(d.values())) for k, d in (("refused", refused), ("quarantined", quarantined),
                                                    ("skipped", skipped), ("off-topic", off_topic))]
    log(f"Done {session_id[:8]} | P={counts['pattern']} "
        f"M={counts['mistake']} D={counts['decision']}"
        + "".join(f" | {k} {n} of {sum(proposed.values())}" for k, n in _unwritten if n))
    if run_log is not None:
        run_log.append({
            "session_id": session_id, "project": project, "time": time_str,
            "patterns": counts["pattern"],
            "mistakes": counts["mistake"],
            "decisions": counts["decision"],
            "proposed": dict(proposed),
            "refused": dict(refused),
            "quarantined": dict(quarantined),
            "skipped": dict(skipped),
            "off_topic": dict(off_topic),
            #: The gate's verdict on the SESSION. The prompt tells the model to return empty lists
            #: for an off-topic session, and a model that obeys leaves proposed 0 and off_topic 0 -
            #: the numbers of an on-topic session with nothing durable. Only this tells them apart
            #: (the auditing session's probe on e0e6924, through api.capture_session).
            "relevant": bool(relevant),
        })
    return True


def sweep_unprocessed(processed_db: dict,
                      exclude_session_id: str | None = None,
                      run_log: list | None = None,
                      max_n: int | None = None) -> int:
    """Find unprocessed transcripts and run them through process_session.

    Processes ANY tracked transcript not yet in processed_db, regardless of
    age - the old SWEEP_DAYS hard cutoff silently lost sessions that aged past
    7 days while still on disk (audit F28). `max_n` caps how many get extracted
    per call (Ollama-bound) so SessionStart need not hold the lock for ages.
    """
    if not PROJECTS_ROOT.exists():
        log(f"Projects root not found: {PROJECTS_ROOT}")
        return 0

    candidates: list[tuple[Path, float]] = []
    now = time.time()
    settle_s = env_int("NEVERTWICE_SWEEP_SETTLE_S", 120)
    for jl in PROJECTS_ROOT.rglob("*.jsonl"):   # recursive: don't miss nested (audit LOW)
        sid = jl.stem
        if sid == exclude_session_id:
            continue
        prior = processed_db.get(sid)
        # a processed transcript that GREW since its mark is a sweep candidate again -
        # the post-compaction tail of a session whose SessionEnd never fired (B1)
        if prior is not None and not _transcript_grew(prior, jl):
            continue
        try:
            mtime = jl.stat().st_mtime
        except OSError:
            continue
        # Settle guard (review 2026-08): a transcript modified seconds ago is a LIVE
        # session. exclude_session_id alone did not protect it - a malformed hook
        # payload (e.g. an stdin decode failure) left session_id="unknown", and the
        # sweep then mined the active session mid-conversation and marked it
        # processed, so its real SessionEnd was skipped and everything after the
        # sweep point was never extracted. Recently-written transcripts wait for
        # the next cycle.
        if now - mtime < settle_s:
            continue
        candidates.append((jl, mtime))

    candidates.sort(key=lambda x: x[1])  # oldest first
    n = attempts = 0
    for jl, jl_mtime in candidates:
        sid = jl.stem
        meta_cwd = read_session_meta(str(jl)).get("cwd")
        cwd = meta_cwd or str(jl.parent)
        if not is_tracked_project(cwd):
            # A missing cwd can be a TRANSIENT read failure (AV/indexer lock →
            # _iter_events swallows the OSError and yields nothing) - and the
            # jl.parent fallback lives under ~/.claude/projects, which is NEVER
            # tracked, so marking here lost the session permanently on one glitch
            # (review 2026-08). Defer marking for recent files; only a week-old
            # transcript that still yields no cwd is genuinely untracked.
            try:
                _sz = jl.stat().st_size
            except OSError:
                _sz = 0
            if meta_cwd is None and _sz > 0 and now - jl_mtime < 7 * 86400:
                log(f"Sweep: {sid[:8]} meta unreadable - left for retry")
                continue
            mark_processed(processed_db, sid, str(jl))
            continue
        # cap on ATTEMPTS, not successes - a slow/failing backend must not let a
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
            # - un-mark so it retries next run instead (audit B18)
            log(f"process_session crashed for {sid[:8]}: {e} - un-marking for retry")
            processed_db.pop(sid, None)
            save_processed(processed_db)
    return n


def has_unprocessed(processed_db: dict, exclude_session_id: str | None = None) -> bool:
    """True if at least one transcript would be a sweep candidate. This is the cheap
    filesystem-only gate SessionStart uses to skip the LLM liveness probe when the
    backlog is empty - an idle start must not wait on a network timeout (perf audit A1;
    measured 2.2s per start against a down Ollama before the gate). Fail-open: a
    candidate that sweep_unprocessed later filters out just means the probe ran once.

    The settle guard is the sweep's, applied here for the same reason it exists there: a
    transcript written seconds ago is a LIVE session, and `exclude_session_id` only covers
    OUR session - a second agent's transcript, or one whose session ended a minute ago, made
    an idle SessionStart answer "backlog" where the sweep then found nothing. That answer is
    not free: at one call site it is the liveness probe this gate exists to skip, and at the
    other it spawns a detached catch-up that takes the vault lock and does no work. The guard
    costs one `stat` per file, unmeasurable beside the walk itself.

    The sweep's OTHER filter - a cwd that is not a tracked project - is deliberately not
    applied, and the reason is a measurement rather than a preference: it means parsing every
    transcript instead of stat-ing it, 25.11 ms against 0.12 ms on 200 settled 103 KB
    transcripts, and the case self-heals because `sweep_unprocessed` marks such a transcript
    processed on its first pass. Fail-open with a price that is paid once.
    """
    if not PROJECTS_ROOT.exists():
        return False
    now = time.time()
    settle_s = env_int("NEVERTWICE_SWEEP_SETTLE_S", 120)
    for jl in PROJECTS_ROOT.rglob("*.jsonl"):
        sid = jl.stem
        if sid == exclude_session_id:
            continue
        prior = processed_db.get(sid)
        if prior is not None and not _transcript_grew(prior, jl):   # grown = candidate (B1)
            continue
        try:
            if now - jl.stat().st_mtime < settle_s:
                continue                       # a live session: the sweep would skip it too
        except OSError:
            continue
        return True
    return False


# ── Status file ───────────────────────────────────────────────────────

def write_status(event: str, trigger: str, sessions_processed: list[dict],
                 swept_count: int, current_session_id: str, degraded: str = ""):
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    total = len(sessions_processed)

    lines = [
        "=== Claude Memory Vault - Status ===",
        f"Last update    : {ts}",
        f"Trigger        : {event or 'manual'} ({trigger})",
        f"Extraction LLM : {llm_backend_desc()}",
        f"LLM this run   : cloud={_LLM_STATS['cloud']}({ACTIVE_CLOUD}) "
        f"ollama={_LLM_STATS['ollama']} failed={_LLM_STATS['fail']}",
        f"Routing        : {local_routing_desc()}",
        f"Vault          : {VAULT}",
        f"Sessions saved : {total} (current run)",
        f"Swept (extra)  : {swept_count}",
        f"Health         : {('DEGRADED - ' + degraded) if degraded else 'OK'}",
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
        new_entry = f"[{ts}] {event or 'manual'} - DEGRADED: {degraded}"
    elif sessions_processed:
        ids = ", ".join(f"{s['project']}:{s['session_id'][:8]}" for s in sessions_processed)
        new_entry = f"[{ts}] {event or 'manual'} - {total} session(s): {ids}"
    else:
        new_entry = (f"[{ts}] {event or 'manual'} - no new sessions "
                     f"(current_id={current_session_id[:8]})")

    history = ([new_entry] + history)[:STATUS_HISTORY_LIMIT]

    lines += ["--- History ---", *history, ""]
    write_atomic(STATUS_FILE, "\n".join(lines))
    log(f"Status written: {STATUS_FILE.name}")


