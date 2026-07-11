#!/usr/bin/env python3
"""Sleep-time memory consolidation (audit F38/F42, research: offline
consolidation). Run weekly from Task Scheduler.

Does three things:
  1. Embeds any not-yet-cached notes (keeps the retrieval index complete).
  2. Finds near-duplicate typed notes within a (project, ntype) and merges
     each cluster: keeps the newest, moves the rest to <folder>/Archive/
     (still reachable by Obsidian), and stamps `recurrence: N` on the keeper
     so recurring lessons outrank one-offs.
  3. Compacts oversized Context files and archives aged notes/sessions.

Safe by default - prints a plan and changes NOTHING. Pass --apply to execute.

    python consolidate_memory.py            # dry-run
    python consolidate_memory.py --apply
"""
import heapq
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import memory_hook as m

try:                                      # never crash printing → / Cyrillic on a cp1251 console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SIM_THRESHOLD = m.env_float("NEVERTWICE_DEDUP_SIM", 0.92)   # safe-cast: a mistyped env var
# Per-project live-note cap (improvement P2). 0 = OFF - a memory store must not shed
# memory without being told to. When >0, the lowest-salience excess is archived.
MAX_LIVE_PER_PROJECT = m.env_int("NEVERTWICE_MAX_LIVE_PER_PROJECT", 0)  # degrades, never crashes


def _int1(x) -> int:
    """recurrence as a positive int, preserving `int(x or 1)` semantics but never crashing on a
    non-numeric CACHE value. Note frontmatter is validated on write; the embeddings cache can hold
    a foreign/merge-conflicted/hand-edited value, and one bad entry used to abort the entire
    consolidation run - dedup, archival, guard-gen, index rebuild, all of it (critic R3)."""
    try:
        return int(x or 1)
    except (TypeError, ValueError):
        return 1


def set_recurrence(p: Path, n: int):
    # one frontmatter-stamp implementation, shared with supersession (launch-round dedup):
    # _stamp_frontmatter does exact-key replace-or-append and leaves the body untouched.
    text = p.read_text(encoding="utf-8", errors="replace")
    new = m._stamp_frontmatter(text, {"recurrence": n})
    if new != text:
        m.write_atomic(p, new)


def date_of(stem: str) -> str:
    parsed = m.parse_typed_stem(stem)
    return parsed["date"] if parsed else stem[:10]


def _fields(p: Path) -> tuple[str, str]:
    """(description, prevention) from a typed note body - the shared parser (m._parse_note_body)
    so it can't drift from the other note-body readers."""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return "", ""
    _, desc, prevention = m._parse_note_body(lines)
    return desc, prevention


def _cluster_recurrence(cache: dict, cluster: list) -> int:
    """The recurrence a merged keeper inherits: the cluster size OR the HIGHEST recurrence
    of any member (a merged dup may have recurred more than the newest keeper), whichever is
    larger - so a near-duplicate merge never drops the recall-boosting count (W15)."""
    recs = [_int1((cache.get(s) or {}).get("recurrence", 1)) for s in cluster]
    return max(len(cluster), max(recs) if recs else 1)


def merge_into_keeper(keep_fp: Path, dup_fps: list[Path]) -> None:
    """Fold any unique description/prevention from the duplicates INTO the keeper
    before they are archived - so 'merge' no longer silently drops a better
    older wording (audit H3). Idempotent: fragments already present are skipped."""
    try:
        ktext = keep_fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    # Compare by normalized whole-line equality, NOT substring containment (audit
    # M-i): the round-1 `frag not in ktext` dropped a unique fragment whenever it
    # happened to occur as a substring of an unrelated line (e.g. inside a wikilink
    # stem), silently losing content during dedup.
    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip().lower()

    existing = {_norm(ln) for ln in ktext.splitlines() if ln.strip()}
    extra = []
    for d in dup_fps:
        for frag in _fields(d):
            frag = frag.strip()
            nf = _norm(frag)
            if nf and nf not in existing:
                existing.add(nf)
                extra.append(frag)
    if not extra:
        return
    header = ("" if ("## Merged from duplicates" in ktext
                     or "## Слито из дублей" in ktext)      # legacy header, dual-read
              else "\n\n## Merged from duplicates\n")
    block = header + "\n".join(f"- {e}" for e in extra) + "\n"
    m.write_atomic(keep_fp, ktext.rstrip() + "\n" + block)


def _union_meta_into_keeper(keep_fp: Path, member_fps: list[Path]) -> None:
    """Union the graph-bearing frontmatter of every cluster member into the keeper before the
    duplicates are archived. Recurrence already carries forward as the cluster max, but its
    siblings - tags, entities, entity_types, relations, sources, confidence - were keeper-only,
    so each merge silently shrank the entity graph and dropped provenance (critic R3, same class
    as the recurrence fix, left open for every other field). List fields are de-duplicated
    (relations by identity); confidence takes the max. Idempotent."""
    try:
        ktext = keep_fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    kfm, _ = m._read_frontmatter(ktext)
    fms = [kfm]
    for fp in member_fps:
        try:
            fms.append(m._read_frontmatter(fp.read_text(encoding="utf-8", errors="replace"))[0])
        except OSError:
            continue

    def _as_list(v):
        return v if isinstance(v, list) else ([v] if v not in (None, "") else [])

    merged = {}
    for field in ("tags", "entities", "sources"):        # plain list fields
        seen, out = set(), []
        for fm in fms:
            for item in _as_list(fm.get(field)):
                key = str(item)
                if key not in seen:
                    seen.add(key)
                    out.append(item)
        if out and out != _as_list(kfm.get(field)):
            merged[field] = out
    # entity_types is a MAP ({name: type}), not a list: union as a dict (keeper wins on a
    # conflict) - folding it through _as_list produced a list-of-dicts that every later reader
    # then silently reset to {} (critic R3, the fix-review found this in its own fix).
    et: dict = {}
    for fm in reversed(fms):                              # kfm is fms[0] → applied last → wins
        v = fm.get("entity_types")
        if isinstance(v, dict):
            et.update(v)
    if et and et != (kfm.get("entity_types") if isinstance(kfm.get("entity_types"), dict) else {}):
        merged["entity_types"] = et
    # relations are list-of-maps: de-dup by canonical JSON
    seen_rel, rels = set(), []
    for fm in fms:
        for r in _as_list(fm.get("relations")):
            key = __import__("json").dumps(r, sort_keys=True, ensure_ascii=False) if isinstance(r, dict) else str(r)
            if key not in seen_rel:
                seen_rel.add(key)
                rels.append(r)
    if rels and rels != _as_list(kfm.get("relations")):
        merged["relations"] = rels
    # confidence: the cluster's strongest
    confs = [m._coerce_confidence(fm.get("confidence")) for fm in fms]
    confs = [c for c in confs if c is not None]
    if confs and max(confs) != m._coerce_confidence(kfm.get("confidence")):
        merged["confidence"] = round(max(confs), 3)

    if merged:
        m.write_atomic(keep_fp, m._stamp_frontmatter(ktext, merged))


def _cluster_tokens(rec: dict) -> set:
    return set(m._tokens(f"{rec.get('title','')} {rec.get('desc','')} {rec.get('prevention','')}"))


def find_clusters(cache: dict) -> list[list[str]]:
    """Greedy near-duplicate clusters within the same (project, ntype). Cosine is
    computed only between notes that share lexical tokens (found via a token→stems
    inverted index), not across all M² pairs in a bucket (audit A6): near-duplicates
    always share vocabulary, so the weekly scan that was quadratic (10k notes in one
    bucket ≈ 87 min of Python cosine) becomes roughly linear and finds the same
    clusters."""
    MIN_SHARED = 2          # a candidate pair must share ≥2 content tokens before cosine
    groups: dict[tuple, list[str]] = {}
    for stem, rec in cache.items():
        # only valid-ntype records - a malformed/legacy entry must not crash the
        # later TYPE_FOLDER[rec['ntype']] lookup (audit C4)
        if not isinstance(rec, dict) or rec.get("ntype") not in m.TYPE_FOLDER:
            continue
        groups.setdefault((rec.get("project"), rec.get("ntype")), []).append(stem)

    clusters = []
    for stems in groups.values():
        # seed clustering from the highest-recurrence note, tie-broken by stem, so the merge
        # is deterministic run-to-run and the most-proven note anchors its cluster (audit)
        stems = sorted(stems, key=lambda s: (-_int1(cache[s].get("recurrence", 1)), s))
        toks = {s: _cluster_tokens(cache[s]) for s in stems}
        inv: dict = {}                          # token → stems sharing it
        for s in stems:
            for t in toks[s]:
                inv.setdefault(t, []).append(s)
        used = set()
        for a in stems:
            if a in used:
                continue
            shared: dict = {}                   # sibling stem → shared-token count
            for t in toks[a]:
                for b in inv.get(t, ()):
                    if b != a and b not in used:
                        shared[b] = shared.get(b, 0) + 1
            cluster = [a]
            for b, ov in shared.items():
                if ov >= MIN_SHARED and \
                        m.cosine(cache[a].get("vec") or [], cache[b].get("vec") or []) >= SIM_THRESHOLD:
                    cluster.append(b)
                    used.add(b)
            if len(cluster) > 1:
                used.add(a)
                clusters.append(cluster)
    return clusters


AUTO_LINK_HEADER = "## Related (auto)"


def link_related_notes(cache: dict, apply: bool, k: int = 3, min_overlap: int = 3) -> int:
    """Dynamic Zettelkasten linking (M-7): for each note, add `[[links]]` to its
    top-k lexically-related siblings in the same project, so the store becomes a
    navigable network instead of isolated cards. GPU-free (token overlap). Runs
    once per note (skips notes already carrying the auto-link section)."""
    by_proj: dict = {}
    for stem, r in cache.items():
        if isinstance(r, dict) and r.get("ntype") in m.TYPE_FOLDER:
            by_proj.setdefault(r.get("project"), []).append((stem, r))
    linked = 0
    for _proj, notes in by_proj.items():
        toks = {s: m._tokens(f"{r.get('title','')} {r.get('desc','')} {r.get('prevention','')}")
                for s, r in notes}
        # Inverted index token→stems so each note only scores siblings it actually
        # shares a token with, instead of all N pairs (audit H3): the round-1
        # all-pairs intersection was ~quadratic (N=800 → 3 s, 5000 → ~2 min EVERY
        # weekly run). The per-token postings count IS the overlap size.
        inv: dict = {}
        for s, ts in toks.items():
            for t in ts:
                inv.setdefault(t, []).append(s)
        for s, r in notes:
            counts: dict = {}
            for t in toks[s]:
                for s2 in inv.get(t, ()):
                    if s2 != s:
                        counts[s2] = counts.get(s2, 0) + 1
            scored = sorted(((ov, s2) for s2, ov in counts.items() if ov >= min_overlap),
                            reverse=True)
            related = [s2 for ov, s2 in scored][:k]
            if not related:
                continue
            fp = m.VAULT / m.TYPE_FOLDER[r["ntype"]] / f"{s}.md"
            if not fp.exists():
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if AUTO_LINK_HEADER in text or "## Связанные (авто)" in text:
                continue                           # already auto-linked (either generation)
            fresh = [x for x in related if f"[[{x}]]" not in text]
            if not fresh:
                continue
            if apply:
                block = f"\n\n{AUTO_LINK_HEADER}\n" + "\n".join(f"- [[{x}]]" for x in fresh) + "\n"
                m.write_atomic(fp, text.rstrip() + "\n" + block)
            linked += 1
    return linked


def distill_patterns(cache: dict, apply: bool, max_distill: int = 3) -> int:
    """Sleep-time reflection (M-1): distil recurring mistakes (episodic) into a
    general prevention pattern (semantic) via the LLM. Same-slug supersession
    keeps it idempotent across weekly runs. The flagship 'leader' feature
    (cf. Letta sleep-time, ChatGPT Dreaming)."""
    cand = [(s, r) for s, r in cache.items()
            if isinstance(r, dict) and r.get("ntype") == "mistake"
            and _int1(r.get("recurrence", 1)) >= 2]
    cand.sort(key=lambda sr: -_int1(sr[1].get("recurrence", 1)))
    made = 0
    for s, r in cand[:max_distill]:
        print(f"  distil recurring mistake -> pattern: {s}")
        if not apply:
            made += 1
            continue
        prompt = ("From this RECURRING mistake derive the GENERAL durable pattern-rule for "
                  "avoiding it in the future, in the language the mistake is written in. "
                  'Return ONLY JSON {"title": "short rule", "description": "1-2 sentences"}.\n\n'
                  f"MISTAKE (recurred {r.get('recurrence')}x): {r.get('title','')} - "
                  f"{r.get('desc','')} {r.get('prevention','')}")
        res = m.generate_json(prompt, project=r.get("project"))
        title = (res.get("title") or "").strip() if isinstance(res, dict) else ""
        desc = (res.get("description") or "").strip() if isinstance(res, dict) else ""
        # Quality gate (audit M-e): only accept a REAL distilled rule, not model
        # junk. The round-1 code accepted anything with a title and stamped it as a
        # learned pattern that RESOLVES the mistake - silently poisoning recall and
        # muting a real warning. Require a substantive title + description, and
        # reject a verbatim echo of the source mistake.
        src_title = (r.get("title", "") or "").strip().lower()
        src_desc = (r.get("desc", "") or "").strip().lower()
        if len(title) < 6 or len(desc) < 15:
            print(f"    [skip] distilled pattern failed quality gate: {title[:40]!r}")
            continue
        if title.lower() == src_title or desc.lower() in (src_desc, src_title):
            print(f"    [skip] distilled pattern echoes the source mistake")
            continue
        date = m.datetime.now().strftime("%Y-%m-%d")
        stem = m.write_typed_note(
            m.TYPE_FOLDER["pattern"],
            {"title": title, "description": desc,
             "resolves": r.get("title", "")}, r.get("project"), date,
            ["distilled"], "pattern")
        if stem:
            made += 1
    return made


def select_coreset(ids, budget, utility_of, tokens_of):
    """Choose `budget` items maximizing the facility-location coverage
    F(S) = Σ_m u(m)·max_{s∈S} sim(m, s), sim = token Jaccard - a monotone submodular
    objective, so lazy greedy (CELF) is within (1−1/e) of optimal (1C). Keeps a
    diverse, high-utility coreset: it won't hoard near-duplicates of one cluster while
    forgetting another (which a pure salience sort does). Pure stdlib; sparse via an
    inverted index, so cost scales with shared tokens, not N²·dim. Returns the kept set."""
    ids = list(ids)
    if len(ids) <= budget:
        return set(ids)
    toks = {i: tokens_of(i) for i in ids}
    u = {i: max(0.0, float(utility_of(i))) for i in ids}
    inv: dict = {}                                   # token -> items (sparse neighbours)
    for i in ids:
        for t in toks[i]:
            inv.setdefault(t, []).append(i)
    nbr = {i: set() for i in ids}
    for lst in inv.values():
        for a in lst:
            nbr[a].update(lst)

    def sim(a, b):
        ta, tb = toks[a], toks[b]
        inter = len(ta & tb)
        return inter / (len(ta) + len(tb) - inter) if inter else 0.0

    cov = {i: 0.0 for i in ids}                      # current max sim of i to S
    S: set = set()

    def gain(s):                                     # marginal coverage gain of adding s
        g = u[s] * (1.0 - cov[s])                    # s covers itself (sim 1)
        for mm in nbr[s]:
            if mm != s:
                g += u[mm] * max(0.0, sim(mm, s) - cov[mm])
        return g

    # lazy greedy (CELF): (−gain, −utility, item, round stamp); −utility breaks coverage
    # ties toward the higher-utility representative (else, among near-duplicates whose
    # marginal coverage is equal/zero, the kept one would be arbitrary, not the best).
    heap = [(-gain(i), -u[i], i, 0) for i in ids]
    heapq.heapify(heap)
    rnd = 0
    while len(S) < budget and heap:
        neg_g, neg_u, s, stamp = heapq.heappop(heap)
        if s in S:
            continue
        if stamp == rnd:                             # gain is fresh ⇒ optimal pick
            S.add(s)
            cov[s] = 1.0
            for mm in nbr[s]:
                cov[mm] = max(cov[mm], sim(mm, s))
            rnd += 1
        else:                                        # stale ⇒ recompute and reinsert
            heapq.heappush(heap, (-gain(s), -u[s], s, rnd))
    return S


def cap_project_notes(cache: dict, apply: bool) -> int:
    """Bound the live-note count per (project, ntype) - the storage counterpart to
    the retrieval prefilter (P1), so a single project can't grow the cache/index/
    build time without bound. OFF by default (cap=0): a memory store should not
    silently shed memory. When NEVERTWICE_MAX_LIVE_PER_PROJECT>0, only the
    *lowest-salience* excess is archived into <folder>/Archive/ (still on disk, just
    out of active recall) - high recurrence, then unresolved, then newest are kept.
    A kept note's [[wikilinks]] to an archived one don't dangle: Obsidian resolves
    links by stem regardless of folder, and graph-hop recall skips non-cached stems."""
    if MAX_LIVE_PER_PROJECT <= 0:
        return 0
    buckets: dict = {}
    for stem, r in cache.items():
        if isinstance(r, dict) and r.get("ntype") in m.TYPE_FOLDER:
            buckets.setdefault((r.get("project"), r.get("ntype")), []).append((stem, r))
    archived = 0
    sal = m.salience_index()                         # F5: one corpus-wide scan, reused for every bucket
    for (proj, nt), items in buckets.items():
        if len(items) <= MAX_LIVE_PER_PROJECT:
            continue
        rec_of = dict(items)
        def _util(stem, _r=rec_of, _s=sal):          # query-independent value (1A frequency prior)
            r = _r[stem]
            n = _int1(r.get("recurrence", 1))
            base = n * (m.RETRIEVAL_RESOLVED_WEIGHT if r.get("resolved") else 1.0)
            return base * (1.0 + _s.get(stem, 0.0))  # a graph-central note is kept over a peripheral one
        def _toks(stem, _r=rec_of):
            r = _r[stem]
            return m._tokens(f"{r.get('title','')} {r.get('desc','')} {r.get('prevention','')} {stem}")
        keep = select_coreset(list(rec_of), MAX_LIVE_PER_PROJECT, _util, _toks)
        excess = [(s, r) for s, r in items if s not in keep]
        folder = m.VAULT / m.TYPE_FOLDER[nt]
        print(f"  cap {proj}/{nt}: {len(items)} live > {MAX_LIVE_PER_PROJECT} "
              f"-> archive {len(excess)} (utility-coverage coreset keeps the diverse {len(keep)})")
        if not apply:
            archived += len(excess)
            continue
        arch = folder / "Archive"
        arch.mkdir(exist_ok=True)
        for stem, _ in excess:
            src = folder / f"{stem}.md"
            if src.exists():
                target = arch / src.name
                if target.exists():
                    target.unlink()
                src.rename(target)
            cache.pop(stem, None)
            archived += 1
    return archived


def stamp_salience(apply: bool) -> int:
    """Brain F5: score every note's graph SALIENCE (pure centrality - inbound edges + degree;
    recurrence is applied separately by the ranker) and stamp it into frontmatter, so retrieval
    applies the gentle centrality nudge and the coreset/cards can prefer central notes. Sleep-time,
    GPU-free, idempotent (writes only on a meaningful change). Returns the count (re)stamped. Inert
    on an entity-less store (salience {}) AND on a coding-only install: the brain layer is opt-in,
    so a default install must not accumulate salience frontmatter the ranker then (previously)
    applied on the hot path (critic R3)."""
    if not m._cfg.brain_enabled():
        return 0
    sal = m.salience_index()                   # {stem: [0,1]}; {} → nothing central → no-op
    if not sal:
        return 0
    stamped = 0
    for ntype, folder in m.TYPE_FOLDER.items():
        d = m.VAULT / folder
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            s = sal.get(p.stem)
            if s is None:
                continue
            new = round(s, 3)
            cur = m._read_frontmatter_file(p).get("salience")
            try:
                if cur is not None and abs(float(cur) - new) < 1e-3:
                    continue                   # unchanged → skip (no churn)
            except (TypeError, ValueError):
                pass
            if new <= 0 and cur is None:
                continue                       # don't stamp a 0 onto a peripheral note
            if not apply:
                stamped += 1
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                m.write_atomic(p, m._stamp_frontmatter(text, {"salience": new}))
                stamped += 1
            except OSError:
                pass
    return stamped


def main():
    apply = "--apply" in sys.argv
    mode = "APPLY" if apply else "DRY-RUN"
    # Don't abort without a backend (audit LOW): the GPU-free steps - near-dup
    # merge, dynamic linking, archival, card refresh - still run. Only the
    # LLM-dependent steps (distillation, context summaries) are skipped.
    has_llm = m.llm_available()
    if not has_llm:
        print("[consolidate] No LLM backend - running GPU-free steps only "
              "(dedup · link · archival · cards); skipping distillation + "
              "context summaries.", file=sys.stderr)
    # Single-writer invariant: the APPLY path mutates the vault (merges, archival, context
    # compaction, index rebuilds), so it must hold the SAME lock the live hook takes - else a
    # concurrent SessionEnd write races this run and one clobbers the other (launch-round
    # audit). DRY-RUN is read-only and needs no lock.
    if apply and not m.acquire_lock(timeout_s=120):
        print("[consolidate] vault lock busy - another writer is active; aborting",
              file=sys.stderr)
        sys.exit(2)
    try:
        _run_consolidation(apply, mode, has_llm)
    finally:
        if apply:
            m.release_lock()


def _run_consolidation(apply, mode, has_llm):
    # 1) load the embedding index (kept fresh by the hook; run embed_index.py
    #    first if you bulk-edited notes)
    cache = m.load_embed_cache()
    if not cache:
        # First install / freshly cloned vault: no vectors yet, so the cosine dedup has
        # nothing to do - but the GPU-free aging/archival/compaction below still should,
        # so warn and continue instead of aborting ALL maintenance (launch-round audit).
        print("[consolidate] empty embedding cache - skipping near-dup merge (run "
              "embed_index.py to enable it); continuing with archival/compaction.",
              file=sys.stderr)

    # 2) near-duplicate merge
    clusters = find_clusters(cache) if cache else []
    print(f"[consolidate] {mode} | {len(clusters)} near-duplicate cluster(s) "
          f"(sim>={SIM_THRESHOLD})")
    merged = 0
    for cluster in clusters:
        cluster.sort(key=date_of, reverse=True)
        keep, dups = cluster[0], cluster[1:]
        rec = cache[keep]
        folder = m.VAULT / m.TYPE_FOLDER[rec["ntype"]]
        print(f"  cluster ({rec['project']}/{rec['ntype']}) x{len(cluster)} "
              f"-> keep {keep}")
        for d in dups:
            print(f"      archive {d}")
        if apply:
            keep_fp = folder / f"{keep}.md"
            dup_fps = [folder / f"{d}.md" for d in dups]
            if keep_fp.exists():
                live_dups = [d for d in dup_fps if d.exists()]
                merge_into_keeper(keep_fp, live_dups)
                _union_meta_into_keeper(keep_fp, live_dups)   # keep the graph edges + provenance
                # carry the cluster's HIGHEST recurrence forward, not just the keeper's - a
                # merged older dup may have recurred more than the (newest) keeper, and
                # archiving it would otherwise SILENTLY DROP that count the recall boost
                # depends on (extends the round-3 max fix; matters now that recurrence
                # actually grows via supersession - W15).
                rec_n = _cluster_recurrence(cache, cluster)
                set_recurrence(keep_fp, rec_n)
                if isinstance(cache.get(keep), dict):
                    cache[keep]["recurrence"] = rec_n     # so recall boosts it (H4)
            arch = folder / "Archive"
            arch.mkdir(exist_ok=True)
            for src in dup_fps:
                if src.exists():
                    target = arch / src.name
                    if target.exists():
                        target.unlink()
                    src.rename(target)
                    cache.pop(src.stem, None)
                    merged += 1
    if apply and (merged or clusters):
        m.save_embed_cache(cache)

    # 2b) per-project cap (P2): bound live notes per project so none grows unbounded
    #     (opt-in; archives lowest-salience excess). The scale-index rebuild below
    #     reflects the archival.
    capped = cap_project_notes(cache, apply)
    if capped:
        print(f"[consolidate] per-project cap: {capped} low-salience note(s) archived")
        if apply:
            m.save_embed_cache(cache)

    # 3) compaction + aging. This is the designated heavy/non-interactive window,
    #    so LLM context summaries are allowed here (unlike the live hook path,
    #    which is GPU-free to keep the vault lock off any model call - audit C4).
    print("[consolidate] context compaction + archival "
          f"({'applying' if apply else 'skipped in dry-run'})")
    if apply:
        m.maintain_contexts(allow_llm=has_llm)   # compact (LLM) + refresh cards (GPU-free)
        m.archive_old_typed()
        m.archive_old_sessions()

    # 4) sleep-time reflection (M-1) + dynamic linking (M-7) - episodic→semantic
    #    distillation and a navigable note network. Reload cache (links/distil read it).
    distilled = distill_patterns(cache, apply) if has_llm else 0
    linked = link_related_notes(m.load_embed_cache() if apply else cache, apply)
    print(f"[consolidate] reflection: {distilled} pattern(s) distilled, "
          f"{linked} note(s) auto-linked ({'applied' if apply else 'dry-run'})")

    # 4b) salience scoring (Brain F5): stamp graph-centrality salience BEFORE the index rebuild
    #     so retrieval reads the fresh nudge. GPU-free; inert on an entity-less store.
    salstamp = stamp_salience(apply)
    if salstamp:
        print(f"[consolidate] salience: {salstamp} note(s) "
              f"{'stamped' if apply else 'would be stamped'} (graph centrality)")

    # 4c) active memory (axis A): distil high-recurrence mistakes into executable guards so the
    #     PreToolUse hot path has something to fire on. Sleep-time only (may use the LLM); the
    #     hot path only ever READS the resulting ledger. Idempotent (dedup by born_from).
    if apply:
        try:
            import guards as _guards
            added = _guards.generate_from_vault(min_recurrence=2, use_llm=has_llm)
            if added:
                print(f"[consolidate] active memory: {added} new guard(s) distilled from mistakes")
        except Exception as e:
            print(f"[consolidate] guard generation skipped: {e}", file=sys.stderr)

    if apply:
        m.rebuild_index()    # Index.md is itself OKF-valid now (type: index - audit H1/M-14)
        # the merges/archival/distillation above changed the note set → rebuild the
        # SQLite scale index so retrieval stays consistent (audit C2/C3)
        m.rebuild_scale_index()
        # refresh the learned user model (I-6) as part of sleep-time work
        try:
            import build_user_model
            build_user_model.main()
        except Exception as e:
            print(f"[consolidate] user-model refresh skipped: {e}", file=sys.stderr)

    print(f"[consolidate] done ({mode}): {merged} note(s) archived as duplicates")


if __name__ == "__main__":
    main()
