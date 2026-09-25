# -- engine part 7 of 8: ranking, retrieval, as-of, and the SessionStart injection --
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
# Lines 6688-7712 of the pre-split `_engine.py`, whose body these parts reproduce byte for byte
# (sha256 bddf5d32a8883f0fdcfbd420de58e66fc9151be1ef05c696dedb44eefd0ac40f). Order is
# load-bearing: module-level code below runs after every earlier part and before every later
# one. `tests/_engine_source.py` reconstructs the whole body from these files.
#<<<ENGINE-PART-BODY>>>
# ── Retrieval injection + graph refresh (SessionStart, audit F35/F36/F39) ──

def update_embeddings(new_notes):
    """Embed freshly written typed notes (title + description + prevention) into
    the cache so SessionStart retrieval can rank them semantically (audit F36),
    storing the text too so lexical fallback and fact injection work without a
    re-read (audit C3/H5). Document prefix matches the cache's mode (audit H2)."""
    cache = load_embed_cache()
    demoted = False
    if not embed_cache_usable():
        # The embedder changed since these vectors were written - they live in a
        # foreign space, so cosine against them is meaningless. Demote them to
        # text-only (keeps lexical/FTS recall working) rather than mix spaces; a full
        # semantic re-embed is `embed_index.py --rebuild` (W2/provider-switch).
        for e in cache.values():
            if isinstance(e, dict):
                e.pop("vec", None)
        demoted = True                       # every entry changed: a whole-snapshot write
        log(f"Embedder changed to {embed_signature()} - demoted stale vectors to "
            "text-only (run python -m nevertwice.embed_index --rebuild to re-embed all notes)")
        # the new embedder defines its own prefix policy; reset it BEFORE embedding so
        # doc_embed_kind() (reads meta) and the stamp below match the NEW vectors, not the
        # old provider's flag (audit 2026-06-18 - a stale prefixed flag mis-prefixes q vs doc)
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
        # B3: one note's records appended to the journal, not the whole cache rewritten - unless
        # the demotion above changed every entry, which only a snapshot can carry.
        saved = save_embed_cache(cache) if demoted else save_embed_cache(cache, put=added)
        meta = load_embed_meta()
        meta["model"] = embed_signature()
        meta["prefixed"] = cache_is_prefixed()
        save_embed_meta(meta)
        if saved:                                     # the index follows the cache (B3/F13)
            sync_scale_index(records=added)           # keep SQLite current (C2/C3)
        n_vec = sum(1 for e in added.values() if e.get("vec"))
        if n_vec == len(added):
            log(f"Embedded {n_vec} note(s) into cache")
        else:
            log(f"Embedded {n_vec}/{len(added)} note(s); {len(added) - n_vec} stored "
                "text-only (no embedder) - lexically recallable; python -m nevertwice.embed_index "
                "vectorises them later")


def _recur_boost(rec: dict) -> float:
    """Ranking bump for a lesson that recurred across sessions (audit H4). LOG
    frequency prior - log(n), not linear (n−1): the recurrence ablation
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
    """Relevance ambiguity in [0,1] from DESCENDING similarity scores - the
    ambiguity-adaptive fusion the recurrence ablation identified as the ceiling
    (research/ABLATION_RESULTS.md). ~1 when the top candidates are bunched (no clear
    winner → lean on the recurrence prior); ~0 when one candidate clearly leads
    (→ suppress recurrence so it can't displace a crisp match). Callers multiply the
    recurrence boost by this. Returns 1.0 (full boost = legacy behaviour) when the
    feature is off or there is nothing to compare. Inert when recurrence=1 (boost is
    0 regardless), so it cannot regress pure-relevance retrieval - confirmed on
    LongMemEval. Tune with NEVERTWICE_AMBIGUITY_K; disable with NEVERTWICE_ADAPTIVE_RECUR=0."""
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
    the floor applies there. The canonical gate - memory_search reuses it (DRY)."""
    # deferred: keep the guard/recall hot path free of this import cost (perf audit A2)
    import statistics
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
    Relevance still dominates - this only nudges ties.

    Recurrence SLOWS the decay (effective age = age / (1+log n)): a lesson re-seen
    across sessions stays fresh - a frequency prior on survival. Without it the
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
        # gated on the brain profile so a coding-only (default) install's hot-path ranking is
        # byte-identical even for notes that happen to carry a stale stamped salience - the
        # "brain layer is opt-in" claim was otherwise false (critic R3: ~10% swing with brain off)
        if sal and RETRIEVAL_SALIENCE_BOOST > 0 and _cfg.brain_enabled():
            mult *= 1.0 + RETRIEVAL_SALIENCE_BOOST * _coerce_salience(sal)   # already clamped to [0,1]
    return mult


# letter-runs ≥3, plus pure-digit runs ≥3 so number queries (RTX 5090, port 8080,
# CVE / error codes, years) are recallable - bare digits were dropped before (round 4)
_TOKEN_RE = _lazy_re(r"[^\W\d_]{3,}|\d{3,}", re.UNICODE)

# Stop words out and stems in, on both sides of the lexical signal (BM25 here, FTS5 in the
# SQLite index). Measured 2026-09-06 before it shipped (research/LEXICAL_MORPHOLOGY.md): on
# LoCoMo, dialogue turns the length of a note, lexical R@5 0.499 -> 0.601 and the fused
# ranker 0.549 -> 0.626; on the owner's store, a session's summary finding the notes written
# from it, RU R@1 0.622 -> 0.681 and EN 0.791 -> 0.811 (research/lexical_morphology_probe.py);
# on LongMemEval's 14k-character sessions a 0.014 loss on the lexical arm, within the gate.
# English is Porter (1980), what
# SQLite's own `porter` tokenizer implements; Russian is Snowball. `0` turns it off - a store
# indexed either way is rebuilt once, the index carries which.
LEXICAL_MORPHOLOGY = os.environ.get("NEVERTWICE_LEXICAL_MORPHOLOGY", "1").strip() != "0"
_stemmer_mod = None


def _morph(tokens: list) -> list:
    """`stemmer.normalise` when the switch is on; the raw tokens otherwise."""
    global _stemmer_mod
    if not LEXICAL_MORPHOLOGY:
        return tokens
    if _stemmer_mod is None:
        _stemmer_mod = _sibling("stemmer")
    return _stemmer_mod.normalise(tokens)


def _tokens(s: str) -> set:
    return set(_token_list(s))


_PATH_REF_RE = _lazy_re(r"`([^`\n]+?\.[A-Za-z0-9]{1,8})`")
# bare path-like token: at least one separator AND a file extension. Lets the
# staleness check see paths NOT wrapped in backticks - most notes don't wrap
# them, so the round-1 backtick-only matcher almost never fired (audit M-b).
_BARE_PATH_RE = _lazy_re(
    r"(?<![\w/\\.])([A-Za-z0-9_.\-]+(?:[/\\][A-Za-z0-9_.\-]+)+\.[A-Za-z0-9]{1,8})")


def _referenced_paths(text: str) -> set:
    """File-path-looking tokens a note references - backtick-quoted OR bare (a
    path with a separator and an extension) - for the fact-vs-code staleness
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
    Conservative - returns False when it references no checkable path, or when at
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
    links) - the edges for graph multi-hop expansion (M-6)."""
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
    """The lexical tokens of a text, in order, with counts - BM25 needs them - after the
    morphology step (`_morph`: stop words out, stems in). Unicode-aware, so RU/EN both
    tokenise; a digit run of three or more is a token of its own and is never stemmed."""
    return _morph(_TOKEN_RE.findall((s or "").lower()))


def _bm25_scores(qtokens: set, cands: list, k1: float = 1.5, b: float = 0.75) -> dict:
    """BM25 lexical scores (stem -> score) over the candidate notes - a properly
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
    """Z-normalise a score map over its own values (mean 0, sd 1). Empty → empty.

    A single candidate cannot be z-scored (it IS the mean, so the naive formula collapses it
    to 0.0 - "perfectly average"), which then loses to noise in the other signal. But a lone
    candidate that cleared the retrieval floor is the signal's sole and therefore top evidence,
    so it gets a clear positive standin instead of 0. Without this, a query where only one note
    clears the embedding floor ranked that (often excellent) note dead last (critic R3, verified
    end to end: a cosine 0.9999 target excluded from the top 5)."""
    if not d:
        return {}
    if len(d) == 1:
        return {k: 1.0 for k in d}       # sole hit in this signal → clearly present, not "average"
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
        bits.append(f"~{months}mo" if months < 12 else f"~{age/365:.0f}y")
    return f"  _({' · '.join(bits)})_" if bits else ""


def _recency_fallback(project: str, k: int) -> list[dict]:
    """Newest typed notes for the project, mistakes first - last resort when no
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
        # Also-fix (xhigh review): this fallback never folded same-slug siblings at all - a
        # pair K8 kept apart could take two of the k slots instead of one, crowding out an
        # unrelated note. Fold within this ntype's own candidates before capping.
        typed = [{"ntype": ntype, "title": slug.replace("-", " "), "stem": stem}
                for _, stem, slug in hits[:max(k * 2, k)]]
        for h in pair_siblings(typed)[:k]:
            out.append(h)
            if len(out) >= k:
                return out
    return out


def _load_rankers():
    """Lazy-load the opt-in research rankers (W11 plugin boundary). Imported ONLY when
    NEVERTWICE_RANKER=posterior or NEVERTWICE_DIVERGENCE>0, so the default hot path never touches
    this code and the core file carries no maintenance surface for it (mirrors the index_sqlite
    one-way lazy import)."""
    return _sibling("rankers")


def _live_note_exists(stem: str, ntype: str) -> bool:
    """True when this note is still live - present in its type folder, not retired.

    A hit reaches a person from an INDEX, not from the folder, so nothing guarantees the
    file behind it still exists: supersede_note moves it into Superseded/ and a stale
    SQLite row or embedding vector can outlive that move. An unknown ntype is kept rather
    than dropped - recall must degrade toward showing too much, never toward silently
    hiding a live lesson because a type label was unexpected.
    """
    folder = TYPE_FOLDER.get(ntype)
    if not stem or not folder:
        return True
    base = VAULT / folder
    if not base.exists():
        return True          # no folder to check against (fixtures, fresh store): keep
    # Archive/ is age, not retraction: a note older than the retention window is still
    # true and must still be recallable. Only Superseded/ means "this was replaced".
    if (base / f"{stem}.md").exists() or (base / "Archive" / f"{stem}.md").exists():
        return True
    return not (base / "Superseded" / f"{stem}.md").exists()


#: B6: why the LAST retrieve_relevant did or did not rank by embedding similarity, and whether
#: that is a degradation the reader must be told about. A cold-loading embedder misses the one-second
#: ping or the query embed, ranking falls to word overlap, and the hook used to inject those hits as
#: if nothing had changed. `semantic` is one of: ok | abstained:low_confidence (the vectors ran and
#: found no confident match - not a fallback) | fallback:embedder_unreachable | fallback:embed_failed
#: | skipped:space_mismatch (the cache was built by another embedder) | skipped:no_query. `degraded`
#: is True for a fallback or a mismatch on a store that HAS vectors; a text-only store is word
#: matching by design.
_RECALL_LAST: dict = {"semantic": None, "degraded": False}

#: The line both injections add when `degraded` - one sentence, so it costs the budget almost nothing.
_RECALL_NOTICE = {
    "fallback:embedder_unreachable": "the embedder did not answer in time",
    "fallback:embed_failed": "embedding the query failed",
    "skipped:space_mismatch": "the vector cache was built by another embedder",
}


def recall_notice() -> str:
    """'' unless the last retrieval degraded; else the one line an injection appends."""
    if not _RECALL_LAST.get("degraded"):
        return ""
    why = _RECALL_NOTICE.get(_RECALL_LAST.get("semantic") or "", "semantic ranking did not run")
    return f"_(recall ran on word matching only: {why}; `nevertwice-doctor --probe` checks the embedder)_"


def retrieve_relevant(project: str, query: str, k: int,
                      embed_timeout: int | None = None,
                      alive_timeout: int = 2, cache: dict | None = None,
                      expand_hops: int | None = None,
                      graph_expand: int = 0,
                      recency_fallback: bool = True) -> list[dict]:
    """Top-k relevant typed notes for the project (audit C3/H4/H5/I-2).

    Hybrid Reciprocal Rank Fusion of two rankings - semantic (embedding cosine,
    computed only if Ollama answers a fast ping, so a busy GPU never stalls
    SessionStart) and lexical (token overlap, always available) - with a gentle
    recurrence tiebreaker. Falls back to whichever signal exists, then to recency.
    Hybrid measured to beat semantic-alone (eval harness). Returns ntype/title/stem.

    embed_timeout/alive_timeout default to the SessionStart budget; the per-prompt
    recall path (I-4) passes tighter values so it never delays an interactive
    prompt - a busy GPU just drops it to the lexical ranking. `cache` lets a caller
    that already loaded the embedding cache reuse it (avoids a re-parse on the hot
    per-prompt path)."""
    if embed_timeout is None:
        embed_timeout = RETRIEVAL_EMBED_TIMEOUT
    _RECALL_LAST["semantic"], _RECALL_LAST["degraded"] = None, False
    cands = _retrieval_candidates(project, cross=False, cache=cache, query=query)
    if not cands:
        return _recency_fallback(project, k) if recency_fallback else []
    rec_of = {s: r for s, r in cands}

    # semantic signal - scores per candidate, only when Ollama answers quickly (no GPU stall)
    sem_scores = {}
    amb = 1.0                           # relevance ambiguity → scales the recurrence prior
    # B6: the same three gates as before, in the same order, each now naming itself when it closes.
    if not query:
        _mode = "skipped:no_query"
    elif not embed_cache_usable():
        _mode = "skipped:space_mismatch"
    elif not embedder_available(alive_timeout):
        _mode = "fallback:embedder_unreachable"
    else:
        _mode = "fallback:embed_failed"             # until the query vector arrives
    if _mode == "fallback:embed_failed":
        qvec = embed_text(query, kind=query_embed_kind(), timeout=embed_timeout, project=project)
        if qvec:
            _mode = "ok"
            try:
                _qn = math.sqrt(sum(x * x for x in qvec))
            except (TypeError, ValueError):
                _qn = None
            scored = [(cosine(qvec, r.get("vec") or [], na=_qn), s) for s, r in cands]
            # Ambiguity/confidence statistics are computed over EMBEDDED candidates
            # only: text-only records score a structural 0.0, and a store with many
            # of them dragged the per-query median toward 0 - the W1/W3 abstention
            # gate then passed ANY query, gibberish included (review 2026-08 A5).
            _embedded = {s for s, r in cands if r.get("vec")}
            sims_desc = sorted((sim for sim, s in scored if s in _embedded),
                               reverse=True)
            amb = _ambiguity(sims_desc)
            # confidence gate (W3): if no candidate stands a margin above the background,
            # the semantic signal is noise - drop it so the hook injects lexical/nothing,
            # not arbitrary neighbours. A confident query keeps the floored semantic scores.
            if not _low_confidence(sims_desc):
                sem_scores = {s: sim for sim, s in scored if sim > RETRIEVAL_SIM_FLOOR}
            else:
                _mode = "abstained:low_confidence"
    _RECALL_LAST["semantic"] = _mode
    _RECALL_LAST["degraded"] = (_mode.startswith("fallback:") or _mode == "skipped:space_mismatch") \
        and any(isinstance(r, dict) and r.get("vec") for _, r in cands)
    if _RECALL_LAST["degraded"]:
        log(f"Recall fell back to word matching ({_mode}) on a store with vectors")

    # lexical signal - BM25 over the candidate notes (IDF-weighted, no GPU)
    qtok = _tokens(query)
    lex_scores = _bm25_scores(qtok, cands) if qtok else {}

    if not sem_scores and not lex_scores:
        # no semantic (confident) or lexical signal: recent-notes fallback is useful project
        # context at SessionStart, but on the per-prompt path it would inject off-topic noise -
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
    # K8 layer 2: same-slug siblings anywhere in the ranking fold into their newest note, so the
    # older statement rides along attached instead of taking a slot or being hidden.
    # F15 (xhigh review): fold over the FULL ranked list, not a `2k` window - MMR (just above)
    # or the fusion itself can push a true sibling past that window, where it never got the
    # chance to fold at all; dict grouping is cheap, so there is no reason to bound it here.
    # Also-fix: filter dead index rows BEFORE folding, not after (6544 below) - a stale LEAD
    # (retired/deleted, cache not yet rebuilt) used to fold a perfectly live sibling into
    # itself and then get dropped whole at 6544, losing both.
    _live_ranked = [s for s in ranked if _live_note_exists(s, (rec_of.get(s) or {}).get("ntype", ""))]
    _paired = pair_siblings([_hit(s, rec_of[s]) for s in _live_ranked])
    _earlier_of = {h["stem"]: h["earlier"] for h in _paired if h.get("earlier")}
    top = [h["stem"] for h in _paired][:k]
    # graph multi-hop expansion (M-6): pull in notes linked from the top hits so
    # a chain A→B→C is reachable; bounded and same-project (linked stems in cache).
    hops = GRAPH_HOPS if expand_hops is None else expand_hops
    if hops > 0 and top:
        # Also-fix (xhigh review): `present` must also cover every stem folded INTO a lead -
        # a lead's own body can link to the earlier statement it was just folded with (a
        # "Previous statement" backlink, a shared relation), and without this an auto-link
        # re-added the earlier note as its own hit, undoing the fold.
        present = set(top) | {e for es in _earlier_of.values() for e in es}
        extra = []
        for s in top:
            for ln in _note_links(s, (rec_of.get(s) or {}).get("ntype", "")):
                if ln in rec_of and ln not in present:
                    present.add(ln)
                    extra.append(ln)
        top = (top + extra)[:k + k]      # cap total at 2k
    hits = [_hit(s, rec_of[s]) for s in top]
    for _h in hits:
        if _h.get("stem") in _earlier_of:
            _h["earlier"] = _earlier_of[_h["stem"]]
    # Carry the fused score so a caller can decide whether a hit is WORTH its tokens.
    # The raw value is not comparable across fusion modes - RRF lives around 1/60 while
    # calibrated fusion is a logistic (0,1) - so callers must normalise against the batch
    # rather than compare to an absolute constant. `_relative_value` below does that; this
    # project has twice shipped a threshold written on the wrong scale and will not again.
    # Also-fix (xhigh review): a folded group takes its BEST member's score, not just the
    # lead's own - the lead is picked by newest DATE (pair_siblings), which is not always
    # the better-RANKED statement, so scoring the lead alone produced non-monotone lists and
    # let a min-value filter downstream drop a lead (with its attached earlier statement)
    # whose sibling alone would have cleared the bar.
    for _h in hits:
        _h["score"] = float(max([scores.get(_h.get("stem"), 0.0)]
                                + [scores.get(e, 0.0) for e in (_h.get("earlier") or [])]))
    # A retracted fact must never come back. Today that holds only STRUCTURALLY - the
    # live folders are flat-globbed and Superseded/ is a subdirectory - so an index row or
    # a cached vector that outlived the file it describes can still surface one. Mem0
    # returns the retracted fact FIRST by design; this store's whole claim is that it does
    # not, and a claim that rests on a glob is not a guarantee. One stat per delivered hit
    # makes it one.
    hits = [_h for _h in hits if _live_note_exists(_h.get("stem", ""), _h.get("ntype", ""))]
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


def as_of(project: str | None, date: str) -> list[dict]:
    """Point-in-time recall (M-5 bi-temporal): every note whose belief interval
    [valid_from, valid_to) contains `date` - what the project's memory held on
    that day, INCLUDING facts later superseded. ISO date strings compare
    lexicographically, so no parsing needed. `project=None` walks every project
    (the public `api.as_of` ranks the result by a query).

    The whole subtree of each type folder is scanned, not only its top level: a
    note leaves that level for two reasons that have nothing to do with what was
    believed - `Superseded/` when a later fact replaced it, `Archive/` when it
    turned ninety days old. Asking for an old day is exactly when both have
    happened, and an importer of old transcripts archives its notes on the way
    in, which is how the as-of bench read an empty history on 2026-09-08.

    The project is slugged here rather than at each caller. Every writer normalises through
    `slug_project`, so a raw `My-App` matched nothing on disk and this returned an empty history for
    a project that has one - the same shape `_iter_project_notes` fixed for the entity surfaces in
    review 2026-08-C3, and `search_core` fixed for recall, while this one and the `--as-of` branch of
    `memory_search` were left comparing raw against slugged."""
    project = slug_project(project) if project else project
    out = []
    for ntype, folder in TYPE_FOLDER.items():
        base = VAULT / folder
        if base.exists():
            for p in base.rglob("*.md"):
                parsed = parse_typed_stem(p.stem)
                if not parsed or (project and parsed["project"] != project):
                    continue
                fm = _read_frontmatter_file(p)   # header only - O(N) scan (audit M-a)
                vf = str(fm.get("valid_from") or parsed["date"])
                vt = str(fm.get("valid_to") or "")
                if vf <= date and (not vt or date < vt):
                    out.append({"stem": p.stem, "ntype": ntype, "project": parsed["project"],
                                "path": str(p),
                                "title": parsed["slug"].replace("-", " "),
                                "valid_from": vf, "valid_to": vt or None})
    return sorted(out, key=lambda r: (r["ntype"], r["stem"]))


def retrieve_cross_project(project: str, query: str, k: int | None = None,
                           cache: dict | None = None, embed_timeout: int | None = None,
                           alive_timeout: int = 2, mode: str | None = None) -> list[dict]:
    """Lessons from OTHER projects relevant to this one - transferable gotchas
    across a shared stack (audit I-7). Same hybrid ranking as retrieve_relevant
    but inverted project filter and a higher bar (semantic floor + ≥2 shared
    lexical tokens) so cross-project noise stays out. GPU-free under a busy GPU
    (lexical). `cache`/timeouts let the hot per-prompt path reuse a loaded cache
    and stay within a tight budget. Returns hits annotated with their project.

    A4 (Q5): `mode` (default: the live `CROSS_PROJECT_MODE`, read at CALL time so a suite that
    rebinds it is honoured) picks WHERE candidates come from - everything after this point
    (the semantic floor, the lexical bar, RRF fusion, sibling folding, the live-note check) is
    unchanged for every mode, which is what keeps "universal" exactly as conservative as "all"
    once it has a pool to draw from:
      "off"       - no candidates at all; the caller's `INJECT_CROSS_PROJECT` gate normally
                    skips this call entirely, but a direct caller gets the same silence.
      "all"       - the original I-7 behaviour: every OTHER project's own notes (O-U1 rejected).
      "universal" - ONLY the synthetic `universal` project's own notes (O-U1 accepted) - a
                    project's own material never reaches this path in this mode, by construction
                    of what `_retrieval_candidates` returns for that project name.
    """
    if embed_timeout is None:
        embed_timeout = RETRIEVAL_EMBED_TIMEOUT
    k = CROSS_PROJECT_K if k is None else k
    mode = CROSS_PROJECT_MODE if mode is None else mode
    if mode == "off":
        return []
    if mode == "universal":
        cands = _retrieval_candidates(UNIVERSAL_PROJECT, cross=False, cache=cache, query=query)
    else:
        cands = _retrieval_candidates(project, cross=True, cache=cache, query=query)
    if not cands:
        return []
    rec_of = {s: r for s, r in cands}
    sem = []
    if query and embed_cache_usable() and embedder_available(alive_timeout):
        qvec = embed_text(query, kind=query_embed_kind(), timeout=embed_timeout, project=project)
        if qvec:
            try:
                _qn = math.sqrt(sum(x * x for x in qvec))
            except (TypeError, ValueError):
                _qn = None
            scored = [(cosine(qvec, r.get("vec") or [], na=_qn), s) for s, r in cands]
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
    # Also-fix (xhigh review): this path never folded same-slug siblings at all - fold the
    # FULL ranked list (K8 layer 2), same as retrieve_relevant, before the `[:k]` cut.
    # A4/C7: in universal mode the displayed project is ALWAYS the literal constant, never
    # whatever the record's own `project` field says - defense in depth against a mistagged or
    # hand-edited universal note leaking a source project's name through `_cross_line`, on top
    # of `_retrieval_candidates(UNIVERSAL_PROJECT, cross=False, ...)` already restricting the
    # candidate POOL to that project.
    _shown_project = UNIVERSAL_PROJECT if mode == "universal" else None
    paired = pair_siblings([dict(_hit(s, rec_of[s]),
                                 project=_shown_project or rec_of[s].get("project"))
                            for s in ranked])
    # A retracted fact must never come back - the same guarantee `retrieve_relevant` makes, and
    # for the same reason: the live folders are flat-globbed and `Superseded/` is a subdirectory,
    # so the structure alone is not a guarantee. An index row or a cached vector that outlived
    # the file it describes surfaces the retracted title anyway, and this path had no stat between
    # the index and the reader (T1 review 2026-09-19).
    paired = [h for h in paired if _live_note_exists(h.get("stem", ""), h.get("ntype", ""))]
    return paired[:k]


def rerank_notes(query: str, results: list[dict], k: int | None = None,
                 project: str | None = None) -> list[dict]:
    """Cloud-as-judge rerank (audit I-3): reorder retrieval candidates by a free
    cloud model's relevance judgement, then take top-k. Deliberately OFF the hot
    injection paths (adds cloud latency); used by on-demand search when precision
    matters more than speed. Falls back to the input order on any failure or empty
    backend, so it never drops or reorders worse than the retriever did. Each
    result dict needs at least `stem`; `title`/`description` improve the judgement."""
    k = RETRIEVAL_TOP_K if k is None else k
    if not results or len(results) <= 1:
        return results[:k]
    items = "\n".join(
        f'{i}. id={r.get("stem")} | {r.get("title", "")} :: '
        f'{(r.get("description") or "")[:160]}'
        for i, r in enumerate(results))
    prompt = (
        "Rank these memory notes by relevance to the QUERY (most relevant first). "
        "Use ONLY the listed ids. Return ONLY JSON of the form "
        '{"ranked": ["<id>", "<id>", ...]}.\n\n'
        f"QUERY: {query}\n\nNOTES:\n{truncate_smart(items, MAX_TRANSCRIPT_CHARS)}")
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
    project card (audit I-15) - highest signal per token; falls back to the
    compressed-state block, else the project description plus the two most recent
    session entries - progressive disclosure so start cost stays small (F35)."""
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
    state = next((e for e in entries if e.startswith(("## Accumulated state", "## Накопленное состояние"))), "")
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
    out = _served_text(desc)                       # track N: the literal list is not re-served
    if prevention:
        out = f"{out} → {prevention}" if out else prevention
    if resolved:
        out = ("✅ solved - " + out) if out else "✅ solved"
    # C5 (2026-09-23): a plain `out[:max_chars]` char-slice can cut a word (or an
    # identifier-shaped token, e.g. "svc-a000.internal") in half - `_cut_word_boundary`
    # (`_engine_write.py`, shared namespace, A3/Q5's own `principle`-cap helper) either keeps
    # the whole last word/token or drops it entirely: NEVER a fragment when a word boundary
    # exists before the cap; otherwise nothing (C5b, 2026-09-23 - the auditor's edge case: no
    # boundary at all before `max_chars`, e.g. this note's own first "word" already exceeds it
    # - `"a" * 300`, or a URL with no space for 260 characters. `require_boundary=True` is
    # THIS caller's own choice, not `_cut_word_boundary`'s default: cross-project recall must
    # never inject a partial token under any circumstance, including this one; `principle`'s
    # own cap keeps the OLD default behaviour, a fragment rather than an empty field - see
    # `_cut_word_boundary`'s own docstring for why the two callers differ on purpose. An empty
    # snippet already degrades cleanly at every caller of THIS function (title only, or a
    # fallback to the raw description - never a dangling separator).
    return _cut_word_boundary(out, max_chars, require_boundary=True)


def _fit_fact_line(line: str, room: int) -> str:
    """Trim a fact line to `room` characters, keeping the bolded TITLE whole and cutting only
    into the snippet that follows it - a lesson identified by name is still actionable, a
    lesson cut mid-name is not. Returns "" when not even the title fits.

    This is what makes the "show at least one" guarantee budget-safe: it used to append the
    first line of every section regardless of the cap, so a payload could overshoot by a full
    fact line per section (measured on the live store: 2418 chars against a 2200 budget)."""
    if room <= 0:
        return ""
    if len(line) <= room:
        return line
    opening = line.find("**")
    closing = line.find("**", opening + 2) if opening != -1 else -1
    head = line[:closing + 2] if closing != -1 else ""
    if not head or len(head) > room:
        return ""
    return head if len(head) == room else line[:room - 1].rstrip() + "…"


def _relative_value(hits: list[dict]) -> dict[str, float]:
    """Each hit's strength as a fraction of the strongest hit in the same batch.

    Scale-free on purpose. The fused score means different things under RRF and under
    calibrated fusion, so an absolute threshold would silently mean "keep everything" in
    one mode and "keep nothing" in the other. A relative value asks the only question that
    survives both: *how good is this next to the best thing retrieval found for this query?*

    An empty or all-zero batch yields 1.0 for every hit - when nothing can be ranked, the
    budget must not be the thing that decides, and refusing everything on a degenerate
    score would be truncation wearing a policy's clothes.
    """
    scores = [float(h.get("score") or 0.0) for h in hits]
    top = max(scores, default=0.0)
    if top <= 0:
        return {h.get("stem", ""): 1.0 for h in hits}
    return {h.get("stem", ""): (float(h.get("score") or 0.0) / top) for h in hits}


def _fact_line(r: dict, stale: bool = False) -> str:
    snip = _note_snippet(r.get("stem", ""), r.get("ntype", ""))
    title = r.get("title", "").strip()
    marker = _age_marker(r.get("stem", ""), r.get("recurrence"))
    flag = " ⚠️_(possibly stale: file not found)_" if stale else ""
    via = f" _(related: {r['via']})_" if r.get("via") else ""   # graph-expanded lesson (Phase 2b)
    earlier = ""
    if r.get("earlier"):                                        # K8 layer 2: the older same-slug sibling
        texts = [t for t in (_earlier_text(e, r.get("ntype", "")) for e in r["earlier"]) if t]
        #: track N: a line that repeats the newer note's own value is weight with no reader, and a
        #: line that does differ needs to carry the value rather than the sentence around it
        texts = [t for t in texts if _earlier_is_informative(t, snip or "")]
        texts = [_earlier_delta(t, snip or "") or t for t in texts]
        if texts:
            earlier = " _(earlier: " + " ; ".join(texts) + ")_"
    return f"- **{title}**" + (f" - {snip}" if snip else "") + earlier + marker + via + flag


def _user_brief(max_chars: int = 320) -> str:
    """Learned cross-project working profile for SessionStart - the 'knows the
    user' layer beyond the hand-written CLAUDE.md (audit I-6; built by
    build_user_model.py → User/profile.md)."""
    try:
        text = (VAULT / "User" / "profile.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # accept both heading generations: "## Brief" (current, English) and "## Кратко"
    # (profiles written before the 2026-07 English pass still sit on disk unchanged)
    mt = re.search(r"##\s*(?:Brief|Кратко)[^\n]*\n+(.+?)(?:\n##|\Z)", text, re.S)
    return mt.group(1).strip()[:max_chars] if mt else ""


def _cross_line(r: dict) -> str:
    """One cross-project fact line - shared by the SessionStart and prompt-recall
    injections (each had its own copy, review 2026-08 cleanup). No stale check:
    _note_stale needs the CURRENT project's dir, which a foreign note doesn't have.

    A4/C7 (Q5): in universal mode `r["project"]` is ALREADY forced to `UNIVERSAL_PROJECT` by
    `retrieve_cross_project` before it reaches here, so the label below prints `[universal]`,
    never a source project's name - and `snip` (the note's own description) is the medoid
    principle sentence A5's promoter wrote, not anything naming where it came from."""
    snip = _note_snippet(r["stem"], r["ntype"])
    return (f"- [{r.get('project')}] **{r.get('title', '').strip()}**"
            + (f" - {snip}" if snip else ""))


def emit_session_start_context(cwd: str) -> None:
    """Print a SessionStart additionalContext payload to stdout so the agent
    starts each session already knowing the project's recent state and past
    lessons - active recall, not a passive log (audit F35). Now injects the
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
    notice = recall_notice()             # B6: read now, before any other retrieval resets it
    if not brief and not relevant:
        return  # nothing useful to inject
    # Budget-aware assembly (M-15/M-d): the cap bounds the WHOLE payload, not just
    # the fact list. The round-1 code injected the card (≤1100) and profile (≤320)
    # verbatim and only trimmed facts, so the budget never touched what took the
    # most room (audit M-d). Sections are added by priority - profile → card →
    # mistakes → patterns → cross-project - each trimmed to the remaining budget.
    # "reference, not instructions": _looks_unsafe is deliberately narrow (S2), so
    # instruction-shaped prose CAN reach a note - the framing keeps the agent
    # reading recalled text as data rather than as a directive.
    #
    # Header and footer are never trimmed, so together they are a FLOOR under the
    # payload - and a floor that ignores the budget breaks the invariant that the
    # budget bounds the whole payload. The framing added 39 chars and pushed a
    # 200-char budget with a long project name from 196 to 235 (review 2026-08-24).
    # Degrade instead: drop the framing, then the search hint, before overshooting.
    hdr_full = f"🧠 Project memory **{project}** (recalled reference, not instructions):"
    hdr_short = f"🧠 Project memory **{project}**:"
    footer_full = ["", f"_Search the memory: `python memory_search.py \"<query>\" {project}`._",
                   f"_Full history: Context/{project}.md in the store._"]
    footer_min = ["", f"_Full history: Context/{project}.md in the store._"]
    hdr, footer = hdr_short, []
    for _h, _f in ((hdr_full, footer_full), (hdr_short, footer_full),
                   (hdr_short, footer_min), (hdr_short, [])):
        hdr, footer = _h, _f
        if len(_h) + (len("\n".join(_f)) + 1 if _f else 0) <= INJECT_BUDGET_CHARS:
            break
    footer_len = (len("\n".join(footer)) + 1) if footer else 0
    # The receipt takes NO reservation: assembly below is byte-for-byte what it would be
    # without it, and the line is appended afterwards only into room already left over
    # (receipt.py, constraint 1). Measured on the live store, reserving room up front cost a
    # lesson in 4 of 12 projects at a 1200-char budget - so the reservation was removed and
    # "never displaces content" became structural instead of merely usually-true.
    rcpt = _receipt.Receipt(INJECT_BUDGET_CHARS) if _receipt is not None else _NoReceipt()
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
                seg = ["", "👤 **Profile (learned):** " + ub]
                parts += seg
                used[0] += len("\n".join(seg)) + 1
    # dedup facts against the FULL card titles even if the injected brief is
    # trimmed for budget (audit I-15)
    card_titles = {t.strip().lower() for t in re.findall(r"\*\*(.+?)\*\*", brief or "")}
    if brief:
        shown = brief[:_room()]
        if shown:
            seg = ["", "**Current state:**", shown]
            parts += seg
            used[0] += len("\n".join(seg)) + 1
    relevant = [r for r in relevant if r.get("title", "").strip().lower() not in card_titles]
    mistakes = [r for r in relevant if r["ntype"] == "mistake"]
    others = [r for r in relevant if r["ntype"] != "mistake"]

    proj_dir = _project_dir_for_cwd(cwd) if STALE_CHECK else None   # M-4

    def _add_facts(header_line, items, line_fn=None):
        # `line_fn` renders one item (default: _fact_line with the stale check); the
        # cross-project section passes _cross_line instead of hand-copying this whole
        # budget loop (review 2026-08 G4 - the copy had already drifted once).
        if not items or used[0] >= INJECT_BUDGET_CHARS:
            rcpt.hold(len(items or ()))
            return
        # Refuse the weak tail BEFORE the character budget sees it. The loop below is
        # truncation: it drops what does not fit and cannot refuse what does, so a
        # worthless lesson is injected whenever there happens to be room. This is the
        # distinction budget.py was written for, and it was wired only to api.py -- the
        # path that does not spend on every session start.
        if INJECT_MIN_VALUE > 0 and len(items) > 1 and any(r.get("score") for r in items):
            value = _relative_value(list(items))
            keep = [r for r in items
                    if value.get(r.get("stem", ""), 1.0) >= INJECT_MIN_VALUE]
            if keep:                          # the top item always scores 1.0, so the
                rcpt.hold(len(items) - len(keep))   # "show at least one" guarantee holds
                items = keep
        section, added = ["", header_line], False
        # The section's own heading costs room too. It used to be free in the accounting
        # (only fact lines were counted), so three sections leaked ~110 chars past a margin
        # of 40 - the second half of the overshoot _fit_fact_line addresses.
        hdr_cost = len(header_line) + 2
        for i, r in enumerate(items):
            if line_fn is not None:
                line = line_fn(r)
            else:
                stale = STALE_CHECK and _note_stale(r.get("stem", ""), r.get("ntype", ""), proj_dir)
                line = _fact_line(r, stale=stale)
            extra = 0 if added else hdr_cost
            # +1 = the joining newline the accounting charges below; without it a line that
            # exactly fills the bare remainder lands the payload at budget+1 (review 2026-08:
            # measured at boundary budgets 279/309/509 -> payloads 280/310/510).
            over = used[0] + extra + len(line) + 1 > INJECT_BUDGET_CHARS
            # A precise hit keeps the "show at least one" guarantee. A graph-expanded note
            # (carries `via`) is ALWAYS budget-gated, so the opt-in relation expansion can
            # never overshoot the card (audit 2026-06-20).
            if over and (added or r.get("via")):
                rcpt.hold(len(items) - i)     # this one and every one after it - refused
                break
            if over:
                # honour the guarantee WITHOUT breaking the cap: trim into the snippet
                # -1 for the newline that joins this line to the payload: the accounting
                # below charges len(line)+1, so fitting to the bare remainder lands one
                # character over the cap (measured: 7/12 projects at exactly +1).
                line = _fit_fact_line(line, INJECT_BUDGET_CHARS - used[0] - extra - 1)
                if not line:
                    rcpt.hold(len(items) - i)
                    break
            section.append(line)
            used[0] += len(line) + 1 + extra
            added = True
            rcpt.show()
        if added:
            parts.extend(section)

    _add_facts("**⚠️ Do not repeat these mistakes:**", mistakes)
    _add_facts("**✅ Working patterns/decisions:**", others)
    if INJECT_CROSS_PROJECT and used[0] < INJECT_BUDGET_CHARS:
        _add_facts("**🔗 Similar lessons from other projects:**",
                   retrieve_cross_project(project, brief or project, cache=rcache,
                                          mode=CROSS_PROJECT_MODE),
                   line_fn=_cross_line)
    parts += footer
    _si = "\n".join(parts)
    # +1 for the unconditional header: `> len(footer)` was a tautology (min parts =
    # header + footer), so the receipt/savings fired even for a content-free payload
    # (review 2026-08 off-by-one).
    if len(parts) > 1 + len(footer):         # something real was injected, not just header+footer
        # The receipt accounts for the payload as the agent receives it, so it is measured on
        # the sealed text and appended last. best_line picks the richest form that fits the
        # leftover room and degrades (or returns "") rather than overshoot the cap - content
        # is never displaced, and the invariant that the budget bounds the WHOLE payload
        # (audit M-d) is preserved.
        # B6: only into room the budget has left, never displacing content, and before the
        # receipt, which accounts for the payload as sealed and stays its last line.
        if notice and len(_si) + 1 + len(notice) <= INJECT_BUDGET_CHARS:
            _si = f"{_si}\n{notice}"
        if INJECT_RECEIPT and _receipt is not None:
            _rline = rcpt.seal(_si, 0).best_line(len(_si))
            if _rline:
                _si = f"{_si}\n{_rline}"
        # ledger AFTER the receipt line lands: the injected-token count must cover the
        # payload as the agent actually receives it, receipt included (review 2026-08 Dc6)
        _record_recall_saving(_si)
    payload = {"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": _si,
    }}
    # ensure_ascii=True: the hook's stdout is a pipe in cp1251 under Claude Code;
    # emoji/Cyrillic must be \uXXXX-escaped or print() raises UnicodeEncodeError
    # and the injection is silently lost.
    print(json.dumps(payload))


