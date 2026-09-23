# -- engine part 5 of 8: the contested/sibling machinery, what a hit is served, write_typed_note, write_session_note, context compaction --
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
# Lines 4104-5416 of the pre-split `_engine.py`, whose body these parts reproduce byte for byte
# (sha256 bddf5d32a8883f0fdcfbd420de58e66fc9151be1ef05c696dedb44eefd0ac40f). Order is
# load-bearing: module-level code below runs after every earlier part and before every later
# one. `tests/_engine_source.py` reconstructs the whole body from these files.
#<<<ENGINE-PART-BODY>>>
#: K8 - the same slug from ANOTHER session is a sibling unless the replacement is proven (ledger K8,
#: 2026-09-16). The slug is a title the extractor composes - a topic, not a fact's identity - and two
#: facts on one topic share it. Until K8 the same-day collision was absorbed in place (the earlier
#: statement kept under `## Previous statement` and no longer served) and the other-day collision
#: retired the earlier note by the slug alone; on the supersession stand's controls that was 5 of 40
#: explicit and 14 of 40 implicit still-true facts lost (K1b/K7), and 7 of 17 on the two-day dating.
#: Layer 1 decides with no model call and only on what the item and the note already carry: an
#: explicit `supersedes` / `contradicts` naming this title, the old literals all present in the new
#: `[facts]` block, or the old statement's text contained in the new. Everything else is a `-2`
#: sibling - both statements keep being served, newest first (`pair_siblings`) - and the earlier note
#: is stamped `contested` for the sleep-time judge (consolidate_memory.py). Nothing is retired or
#: rewritten on a presumption: the skeleton similarity of the two statements read AUC 0.63-0.66 on
#: the extractor's descriptions and 0.76-0.81 on the facts block (research/results/k8_step0.json),
#: so it is not a rule here. K7's hook judge (NEVERTWICE_ABSORB_JUDGE) is gone: the judge lives at
#: sleep, and its honest number is the accuracy of its verdicts.
CONTESTED_KEY = "contested"
#: A pair the judge ruled `replaces` on and the sleep-time guard refused (the proof is missing: no
#: literal in the newer statement against literals in the earlier, or a value the session never
#: said). Off the judge's queue, both served, visible to a human in conflicts() / integrity().
DISPUTED_KEY = "disputed"
#: A3 (Q5): fields an absorb rewrite (a same-session refresh or a same-day same-slug re-
#: statement) carries forward from the note it is overwriting, UNLESS the new extraction
#: supplies its own value (see the `fm.setdefault` loop below - "newer wins, older survives
#: what newer left unsaid"). A module-level tuple, not an inline literal, so
#: `tests/_test_principle_write.py`'s mutation can drop "principle" from it with a monkeypatch
#: rather than an on-disk edit, and so the next field added here has one place to add it.
_ABSORB_CARRY_FIELDS = ("status", "resolved_by", "resolves", "relations", "salience",
                       "supersedes", "valid_to", "confidence", "principle",
                       CONTESTED_KEY, DISPUTED_KEY)
#: How an item's explicit `supersedes` / `contradicts` naming ANOTHER title is acted on. `write` (the
#: default, rule 1 of ledger K8 as written: the extractor's own statement replaces on the spot, the
#: M-2 path since 2026-08). `judge`: the named note is stamped contested and the sleep-time judge
#: confirms - on the first K8 fast cycles the extractor filled `contradicts: traces exported to
#: Tempo` on "logs go to Loki" and retired the still-true Tempo note on both corpora (ctl-obs), the
#: one write-time loss the same-slug rule left; the switch is the owner's call, not a default change.
EXPLICIT_RETIRE = os.environ.get("NEVERTWICE_EXPLICIT_RETIRE", "write").strip().lower()


def _norm_statement(desc: str) -> str:
    """A description without its `[facts]` block, whitespace folded, lower-cased - the statement."""
    return _norm_ws(re.sub(r"\s*\[facts\].*$", "", desc or "", flags=re.DOTALL))


def _same_replacement(old_path: Path, title: str, desc: str,
                      supersedes_title: str = "", contradicts_title: str = "") -> tuple[bool, str]:
    """K8 layer 1: may the item about to be written under `title` REPLACE the same-slug note at
    `old_path` - absorb it in place on the same day, retire it on another? `(True, rule)` with rule
    `explicit` (the item's supersedes/contradicts names this title), `literals` (both sides carry
    literals, the new block carries every old one AND at least one of them names a value, and the
    new prose names no OTHER, unverified value) or `restated` (the old statement's text stands inside
    the new one, with the facts blocks agreeing where both have one, and the same check); `(False,
    reason)` otherwise - `no_literals_in_new` (a lesson without literals never absorbs a note with
    them), `unverified_value` (the extraction's own prose names a value its `[facts]` block does not
    back), `unproven`, `unreadable`. A False verdict costs nothing: the pair becomes two live siblings
    and a contested stamp.

    xhigh review F3: rule 2 used to accept ANY shared literal, including a bare CONTEXT (a filename,
    a path) that proves the two notes touch the same thing but not that they state the same fact - a
    different fact about `docker-compose.yml` "proved" replacement by sharing nothing but where it
    lives. Rule 2 now also requires the old side to carry a VALUE-shaped literal (`_has_value_literal`).
    Rule 2' used to run on disagreeing facts blocks too - the old statement's boilerplate prose can be
    a substring of the new prose even when the values in their `[facts]` blocks contradict each other
    - so it now requires the old block to be absent or a subset of the new one. Both rules also run
    `_unverified_values` on the new extraction before returning True - the same hallucination check
    the sleep-time judge already had (`_replacement_guard`), moved here so a changed or hallucinated
    value can no longer replace the true note at write time either. Only that one check of the guard
    applies here (not `no_value_in_new`): rule 2/2' already proved the OLD value literal is present
    VERBATIM in the new block, a stronger guarantee than the judge's own verdict ever has, so the
    guard's weaker "does the new PROSE also restate it" check would reject a legitimate refinement
    whose new prose adds a detail without repeating the unchanged value."""
    try:
        _, d_old, _ = _parse_note_body(old_path.read_text(encoding="utf-8", errors="replace").split("\n"))
    except Exception:                                    # noqa: BLE001 - keep what cannot be read
        return False, "unreadable"
    slug = slugify(title)
    for other in (supersedes_title, contradicts_title):
        if other and slugify(other) == slug:
            return True, "explicit"                      # rule 1: the extractor itself names the replacement
    old_f, new_f = _facts_in(d_old or ""), _facts_in(desc or "")
    if old_f and not new_f:
        return False, "no_literals_in_new"               # rule 4: a lesson never absorbs a note with facts
    if old_f and new_f and old_f <= new_f and _has_value_literal(old_f):
        if _unverified_values(desc):
            return False, "unverified_value"
        return True, "literals"                          # rule 2: the same fact restated or refined
    o, n = _norm_statement(d_old or ""), _norm_statement(desc or "")
    if not o and not old_f:
        # also-fix (xhigh review): an EMPTY old statement with no facts (api.remember / MCP
        # memory_remember / remember_lessons writing a bare title with no description) carries
        # nothing to preserve - empty-vs-empty or empty-vs-anything used to fall through to
        # "unproven" (rules 2/2' both need SOME old content to compare against), minting a
        # contested '-2' twin and a sleep-time judge call for a pair with nothing to adjudicate.
        # Pre-K8 a second same-title write absorbed unconditionally with recurrence carried.
        return True, "restated"
    if o and n and o in n and (not old_f or old_f <= new_f):
        if _unverified_values(desc):
            return False, "unverified_value"
        return True, "restated"                          # rule 2': the same words, said again
    return False, "unproven"


def _mark_contested(old_path: Path, new_stem: str) -> bool:
    """Stamp `contested: [new_stem, ...]` on the earlier note of a kept-apart pair - what the
    sleep-time judge reads and `conflicts()` prints (K8). Appends without duplicating; a failure
    leaves the note as it was (the sibling is on disk regardless)."""
    try:
        text = old_path.read_text(encoding="utf-8", errors="replace")
        fm, _ = _read_frontmatter(text)
        #: Read through `_contested_of`, the one reader of this field - K9 listed "two stamp
        #: writers" and the duplicated part was never the write (both go through
        #: `_stamp_frontmatter`) but this line, a private copy of the reader that kept falsy items
        #: the canonical one drops. Measured on every shape the frontmatter parser can yield, the
        #: two agreed, so this removes a copy that could drift rather than a live divergence.
        cur = _contested_of(fm)
        if new_stem in cur:
            return True
        write_atomic(old_path, _stamp_frontmatter(text, {CONTESTED_KEY: cur + [new_stem]}))
        return True
    except Exception as e:                               # noqa: BLE001 - a stamp must never fail a write
        log(f"contested stamp failed for {old_path.name} ({type(e).__name__}: {e})")
        return False


#: Value-shaped tokens: numbers with or without a unit, versions. The shape a replacement changes
#: ("30 seconds" -> "5 seconds", "PostgreSQL 14" -> "16") and the shape an extractor hallucinates.
_VALUE_RE = _lazy_re(
    r"[~+\-]?\b\d+(?:[.,]\d+)*\s?(?:MB|GB|KB|TB|ms|s|GHz|MHz|px|%|seconds?|minutes?|hours?|days?)?\b"
    r"|\bv\d+(?:\.\d+)*\b", re.I)

#: F14 (xhigh review): the context a BARE integer (no unit, no version prefix) needs to count as
#: a value at all - port/PR/#/version, immediately before it. Without one it is far more likely an
#: ISO-date piece, a year or a plain count than a value a replacement actually changed.
_BARE_VALUE_CTX_RE = _lazy_re(r"(?:\bport|\bpr|\bversion)\s*#?\s*$|#\s*$", re.I)


def _bare_int_has_value_context(statement: str, pos: int) -> bool:
    return bool(_BARE_VALUE_CTX_RE.search(statement[max(0, pos - 15):pos]))


def _unverified_values(desc: str) -> list[str]:
    """Value-shaped tokens in a description's statement that its `[facts]` block does not carry -
    values the extractor wrote that the session was never seen to say (the block holds only literals
    verified verbatim against the session). A replacement may not rest on one (K8, the sleep-time
    guard): on the first fast cycle the judge retired "the upload size limit is 25 MB" for a note
    saying "100 MB" when the session had said "100 per minute".

    Whole-token membership (F4, xhigh review): `facts` is the SET of value-shaped tokens the block
    itself carries, not the block's raw text - a plain substring scan ('tok not in facts' over the
    joined text) let "5 mb" pass against a block that only ever said "25 mb", "v2" against "v2.0",
    "80" against "8080".

    F14 (xhigh review): a BARE integer (no unit) only counts as a value with a value-shaped
    CONTEXT (port/PR/#/version) - every ISO-date piece, year, PR number and plain count used to
    flag, and the harvester never captures those (no date/year/bare-int pattern in
    `_LIT_PATTERNS`), so they can never be verified - `_replacement_guard` vetoed true
    replacements on nothing but a bare year or a date fragment and parked them `disputed`."""
    statement = _norm_statement(desc)
    facts = {_norm_ws(mo.group(0).strip("~+- ")) for mo in _VALUE_RE.finditer(" ".join(_facts_in(desc)))}
    out = []
    for mo in _VALUE_RE.finditer(statement):
        tok = _norm_ws(mo.group(0).strip("~+- "))
        if tok and not tok.isdigit() and tok not in facts and tok not in out:
            out.append(tok)
        elif (tok and tok.isdigit() and len(tok) >= 2 and _bare_int_has_value_context(statement, mo.start())
              and tok not in facts and tok not in out):
            out.append(tok)
    return out


#: A bare hex identifier (a commit sha, a build id) - the "identifier" shape rule 2 (F3) also
#: accepts as proof, alongside a value-shaped literal from `_VALUE_RE`.
_IDENTIFIER_RE = _lazy_re(r"\b[0-9a-f]{7,40}\b", re.I)


def _has_value_literal(facts: set) -> bool:
    """True when at least one literal in a `[facts]` block NAMES A VALUE (a number, a unit, a
    version or a hex identifier) rather than merely a shared CONTEXT (a filename, a path, a bare
    word). Rule 2 (K8, xhigh review F3) may only prove replacement on a value match - a different
    fact that happens to touch the same file ("docker-compose.yml") shares nothing but where it
    lives, and `old_f <= new_f` alone cannot tell the two apart."""
    return any(_VALUE_RE.search(f) or _IDENTIFIER_RE.search(f) for f in facts)


def _replacement_guard(old_desc: str, new_desc: str) -> str:
    """Why a `replaces` verdict may NOT be acted on (K8; "" when it may): `no_literals_in_new` - a
    note with verified literals is never retired for one without (rule 4, the write-time rule kept
    here too: the judge ruled a boilerplate restatement a replacement on the first fast cycle);
    `unverified_value` - the new statement's value is not in its `[facts]` block, so the session was
    never seen to say it (a hallucinated "100 MB" retired a true "25 MB"); `no_value_in_new` - the
    earlier note's verified literals carry a value and the new statement carries none at all ("the
    upload size limit check is working correctly" over "25 MB").

    Shared by both callers (xhigh review F3): the sleep-time judge (consolidate_memory.py) and, since
    this pair moved here, rules 2/2' of `_same_replacement` themselves - a changed or hallucinated
    value used to replace the true note at write time, because this guard ran only at sleep. A vetoed
    pair stays two live notes; at sleep it also leaves the judge's queue and is stamped `disputed` so
    a human can still see it."""
    old_f, new_f = _facts_in(old_desc), _facts_in(new_desc)
    if old_f and not new_f:
        return "no_literals_in_new"
    if old_f and _unverified_values(new_desc):
        return "unverified_value"
    if _VALUE_RE.search(" ".join(old_f)) and not _VALUE_RE.search(_norm_statement(new_desc)):
        # a valued fact is not replaced by a STATEMENT that names no value - the block beside it may
        # carry a different fact's literal ("the request rate limit is 100 per minute" under a
        # boilerplate "the upload size limit check is working correctly", the third fast cycle)
        return "no_value_in_new"
    return ""


#: One whole wiki-link and nothing else: `[[stem]]` or `[[stem|alias]]`. Neither part takes a
#: bracket, so no start position can run past the next `[[` - the pattern is linear on any input
#: (an alias part that took `[` read 48 kB of unclosed links in 1.7 s - auditing session, ea9272c).
#: The target stops at `#` or `^`: `[[stem#Heading]]` and `[[stem^block]]` link INTO the note
#: `stem`, and integrity.py's reader stops there too (sixth review).
_LONE_LINK_RE = _lazy_re(r"\[\[([^\[\]|#^]+)(?:[#^][^\[\]|]*)?(?:\|[^\[\]]*)?\]\]")

_QUOTES = "\"'"


def _list_field(v: object) -> list[str]:
    """A frontmatter list field, read by a small grammar that is stated here in full.

    `_read_frontmatter` returns the JSON list the engine writes as a list, and anything written by
    hand as ONE string. The readers used to guess at that string - `_contested_of` took it as one
    stem, the consolidator iterated it a character at a time (review 2026-09-23, #3). This reads:

    - a list: each item a string, quotes stripped, and an item that is one whole link its target;
    - a string that is one whole link (`[[stem]]`, `[[stem|alias]]`): its target;
    - a string `[a, b]` with no `[[` inside: split on commas; with quotes in it, read as JSON
      or not at all (`[a, "b, c"]` is one entry);
    - any other string: one entry, quotes stripped (`'stem'` is `stem`).

    Nothing else is parsed - links in a row, links mixed with plain items, other separators. Three
    review rounds each found new defects in a reader that tried (mixed strings, an exponential and
    a quadratic pattern, stray whitespace), and on the owner's store there are no hand-written list
    fields at all; `doctor check_list_fields` reports every shape this does not read cleanly, for
    a person to rewrite as a JSON list. Linear on any input."""
    if isinstance(v, list):
        return [x for x in (_list_item(i) for i in v if i not in (None, "")) if x]
    if not isinstance(v, str):
        return []
    s = v.strip().strip(_QUOTES).strip()
    if not s:
        return []
    if (mt := _LONE_LINK_RE.fullmatch(s)):
        return [mt.group(1).strip()]
    if s.startswith("[") and s.endswith("]") and "[[" not in s:
        if any(q in s for q in _QUOTES):
            #: quoted items: read as JSON, or not at all - splitting `[a, "b, c"]` on every comma
            #: made three names of two (sixth review)
            try:
                items = json.loads(s)
            except ValueError:
                return [s]
            return [x for x in (_list_item(i) for i in items if i not in (None, "")) if x] \
                if isinstance(items, list) and all(isinstance(i, (str, int, float)) for i in items) else [s]
        return [x for x in (p.strip() for p in s[1:-1].split(",")) if x]
    return [s]


def _list_item(x: object) -> str:
    """One item of a list: a string, quotes stripped; one whole link read as its target."""
    s = str(x).strip().strip(_QUOTES).strip()
    mt = _LONE_LINK_RE.fullmatch(s)
    return mt.group(1).strip() if mt else s


def _contested_of(fm: dict) -> list[str]:
    """The stems a `contested` stamp names, read through `_list_field` (one reader, R11)."""
    return _list_field(fm.get(CONTESTED_KEY))


def _iter_contested(project: str | None = None, key: str | None = None) -> list[dict]:
    """Every live or archived typed note carrying a `contested` stamp (or, with `key`, a `disputed`
    one), with the sibling stems it names: `[{stem, path, project, ntype, date, title, archived,
    new_stems}]`. A header-only scan of the type folders (Superseded/ skipped - a retired note's
    stamp is settled).

    One walk, `_iter_contested_both`'s. This function carried its own copy of the same loop and
    the same row until K9 folded it in: two bodies of one scan are two places for the skip rule,
    the date or the title to drift apart, and the digest reads one while the judge reads the
    other. The cost of taking both keys from a walk that wants one is the second field of a
    frontmatter already parsed."""
    # Resolved HERE, not in the signature: a default argument is evaluated once at
    # def time, so a module constant frozen there stops answering to the module.
    key = CONTESTED_KEY if key is None else key
    if key not in (CONTESTED_KEY, DISPUTED_KEY):
        raise ValueError(f"_iter_contested reads {CONTESTED_KEY!r} or {DISPUTED_KEY!r}, not {key!r}")
    contested, disputed = _iter_contested_both(project)
    return contested if key == CONTESTED_KEY else disputed


def _iter_contested_both(project: str | None = None) -> tuple[list[dict], list[dict]]:
    """`(contested, disputed)` - both of `_iter_contested`'s keys from ONE filesystem walk
    (also-fix, xhigh review): `compute_conflicts` used to call `_iter_contested` once per key,
    so a single digest/inbox build walked every type folder twice for the same set of files."""
    out_c, out_d = [], []
    for ntype, folder in TYPE_FOLDER.items():
        base = VAULT / folder
        if not base.exists():
            continue
        for p in base.rglob("*.md"):
            if "Superseded" in p.parts[len(base.parts):]:
                continue
            parsed = parse_typed_stem(p.stem)
            if not parsed or (project and parsed["project"] != project):
                continue
            fm = _read_frontmatter_file(p)
            row = {"stem": p.stem, "path": str(p), "project": parsed["project"], "ntype": ntype,
                  "date": parsed["date"], "title": parsed["slug"].replace("-", " "),
                  "archived": p.parent.name == "Archive"}
            cs = _contested_of(fm)
            if cs:
                out_c.append({**row, "new_stems": cs})
            ds = _contested_of({CONTESTED_KEY: fm.get(DISPUTED_KEY)})
            if ds:
                out_d.append({**row, "new_stems": ds})
    key_fn = lambda r: (r["ntype"], r["stem"])                # noqa: E731
    return sorted(out_c, key=key_fn), sorted(out_d, key=key_fn)


_JUDGE_PROMPT = (
    "Two statements were recorded for one project under the same title, by two different sessions.\n\n"
    "OLD (recorded first): {old}\n\nNEW (recorded later): {new}\n\n"
    "First name, in a few words, WHAT each statement settles - which parameter, component, destination, "
    "tool or rule (for example: 'the upload size limit', 'where traces are exported', 'the HTTP client "
    "timeout'). Then decide:\n"
    '  "replaces" - both settle the SAME thing and NEW states it again, changes its value, narrows it or '
    "retracts it (a timeout of 30 seconds then 5 seconds; PostgreSQL 14 then 16; a flag that gated the UI, "
    "then deleted). OLD should no longer be served as current truth.\n"
    '  "separate" - they settle DIFFERENT things, even on the same topic (an upload size limit and a '
    "request rate limit; where traces go and where logs go; the embedder and the reranker; the CLI and the "
    "desktop app). Both hold at once and OLD stays true.\n"
    "A shared topic, title or week is not the same thing; a different parameter or component is separate.\n"
    'Answer with ONE JSON object and nothing else: {{"old_settles": "...", "new_settles": "...", '
    '"relation": "replaces" or "separate"}}\n'
    "Return ONLY valid JSON.")


def _same_fact_verdict(old_title: str, old_desc: str, new_desc: str, project: str):
    """The same-fact judge (K7's prompt, K8's sleep-time step): `True` (the new statement replaces
    the old), `False` (a different fact - both hold), `None` (no answer). Since K8 it is called only by
    `consolidate_memory.adjudicate_contested`, never from the hook: one call a contested pair, in a
    batch, outside the chain of sessions - no cache confound, no hook millisecond. Measured on the K7
    store (204 pairs of known truth, research/results/k8_judge_eval.json): accuracy 0.956, `replaces`
    precision 1.000 / recall 0.953, `separate` precision 0.571 / recall 1.000, 414 tokens a pair."""
    prompt = _JUDGE_PROMPT.format(old=f"{old_title} - {(old_desc or '')[:600]}", new=(new_desc or "")[:600])
    try:
        res = generate_json(prompt, project=project)
    except Exception as e:                                   # noqa: BLE001 - the judge must never break a write
        log(f"absorb judge skipped ({type(e).__name__}: {e})")
        return None
    rel = str((res or {}).get("relation", "")).strip().lower() if isinstance(res, dict) else ""
    if rel.startswith("replace"):
        return True
    if rel.startswith("separate") or rel.startswith("different"):
        return False
    return None



#: K8 layer 2 - the merge as a representation at read time, re-decided for free. Two live notes of
#: one slug among the hits are one topic with two statements: the newest leads, the earlier one is
#: attached to it as a compact line (its `[facts]` block, else the head of its description) and
#: leaves the list as a separate hit. Nothing is hidden and nothing is demoted below k on a
#: presumption - both facts are served and the agent sees which is newer. `as_of` sees both files.
EARLIER_MAX_CHARS = env_int("NEVERTWICE_EARLIER_MAX_CHARS", 100)
_SIB_SUFFIX_RE = _lazy_re(r"-[2-9]$")


def _sibling_key(stem: str):
    """The read-time grouping key `pair_siblings` folds hits by (F7, xhigh review): the STEM's own
    `sibling_of` stamp names the base it was minted beside; a pre-stamp legacy note falls back to
    the name pattern only when a same-day base of the exact stripped slug actually exists. A slug
    that merely ends in a digit ('python-3') is its own family otherwise - see `_slug_family`,
    whose fallback rule this mirrors for the read path (no `folder_path` here: the type folder is
    derived from `ntype` instead)."""
    parsed = parse_typed_stem(stem or "")
    if not parsed:
        return None
    folder = TYPE_FOLDER.get(parsed["ntype"])
    if folder:
        for sub in (VAULT / folder, VAULT / folder / "Archive"):
            fp = sub / f"{stem}.md"
            if fp.exists():
                stamped = _read_frontmatter_file(fp).get(SIBLING_KEY)
                if stamped:
                    base_parsed = parse_typed_stem(str(stamped))
                    if base_parsed:
                        return base_parsed["project"], base_parsed["ntype"], base_parsed["slug"]
                break
    stripped = _SIB_SUFFIX_RE.sub("", parsed["slug"])
    if stripped != parsed["slug"] and folder:
        base_stem = f"{parsed['date']}-{parsed['project']}-{parsed['ntype']}-{stripped}"
        for sub in (VAULT / folder, VAULT / folder / "Archive"):
            if (sub / f"{base_stem}.md").exists():
                return parsed["project"], parsed["ntype"], stripped
    return parsed["project"], parsed["ntype"], parsed["slug"]


def _earlier_text(stem: str, ntype: str, max_chars: int | None = None) -> str:
    """The compact form of an earlier sibling: its literals when they carry a value (the fact itself,
    in the session's words), else the head of its statement, plus its Prevention when it has one.
    Bounded.

    xhigh review (also-fix): Prevention used to be discarded here - the write-time absorb's OWN
    inheritance (a fresh note with no Prevention keeps the note-being-absorbed's) never runs for
    a K8 sibling, because siblings are deliberately never absorbed into each other; the only place
    left to surface an earlier mistake's "how to avoid it" is this read-time summary."""
    cap = EARLIER_MAX_CHARS if max_chars is None else max_chars
    folder = TYPE_FOLDER.get(ntype or "")
    if not folder:
        return ""
    fp = VAULT / folder / f"{stem}.md"
    try:
        lines = fp.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return ""
    _, desc, prevention = _parse_note_body(lines)
    desc = desc or ""
    facts = desc.split(_FACTS_MARK.strip(), 1)[1].strip() if _FACTS_MARK.strip() in desc else ""
    statement = re.sub(r"\s*\[facts\].*$", "", desc, flags=re.DOTALL).strip()
    # the block is the fact in the session's words only when it carries a value; a harvested
    # distractor ("rolled it out behind the usual staged release") is not, and the statement is
    text = facts if (facts and _VALUE_RE.search(facts)) else (statement or facts)
    prevention = (prevention or "").strip()
    if prevention:
        text = f"{text} - {prevention}" if text else prevention
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= cap else text[:max(0, cap - 1)].rstrip() + "…"


def pair_siblings(hits: list[dict], attach: bool = False) -> list[dict]:
    """Fold same-slug live siblings among ranked hits into their newest note (K8 layer 2). The
    lead keeps the group's best rank and gains `earlier: [stems]` (newest first); with `attach`
    the compact earlier text is also appended to its `description` (the API result shape - the
    hook renders `earlier` itself in `_fact_line`). Hits without a typed stem pass through."""
    groups: dict = {}
    for i, h in enumerate(hits):
        key = _sibling_key(h.get("stem", ""))
        if key:
            groups.setdefault(key, []).append(i)
    if not attach and not any(len(v) > 1 for v in groups.values()):
        # `not attach`: with nothing to fold there is nothing to do and the list is handed
        # back untouched - but the `attach` pass also serves each hit through `_served_text`,
        # and returning early skipped that for EVERY hit whenever no group happened to have
        # two members. So one unrelated sibling pair anywhere in the result changed the text
        # a reader got for notes that had nothing to do with it, and track N's switch was
        # measuring the kinship of the rest of the result rather than the change it names.
        return hits
    lead_of, drop = {}, set()
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        order = sorted(idxs, key=lambda i: ((parse_typed_stem(hits[i]["stem"]) or {}).get("date", ""),
                                            hits[i]["stem"]), reverse=True)
        lead, earlier = order[0], order[1:]
        lead_of[min(idxs)] = (lead, [hits[i]["stem"] for i in earlier])
        drop.update(idxs)
    out = []
    for i, h in enumerate(hits):
        if i in lead_of:
            lead, earlier = lead_of[i]
            lh = dict(hits[lead])
            lh["earlier"] = earlier
            if attach:
                texts = [t for t in (_earlier_text(e, lh.get("ntype", "")) for e in earlier) if t]
                #: Track N applies HERE as well as in the hook's `_fact_line`, and the distinction
                #: cost a campaign to learn: the first implementation changed only the hook, while
                #: `research/supersession_bench.py` measures `api.recall`, whose text is assembled
                #: right here. Fifteen minutes of the run read 473.4 against a base of 473.3 and
                #: the run was stopped. Two surfaces hand text to a reader; a weight change that
                #: reaches one of them has not been made.
                base = _served_text(lh.get("description", "") or "")
                texts = [t for t in texts if _earlier_is_informative(t, base)]
                texts = [_earlier_delta(t, base) or t for t in texts]
                lh["description"] = base
                if texts:
                    joined = " ; ".join(texts)
                    lh["description"] = (f"{base} | earlier: " + joined).strip(" |")
                    # F12 (xhigh review): a SEPARATE field, not just the description suffix above -
                    # the CLI and the MCP tool render the on-disk snippet or a caller-supplied
                    # description, either of which can shadow this text; a field of its own is
                    # always there for a renderer to check regardless of what it shows instead.
                    lh["earlier_text"] = joined
            out.append(lh)
        elif i not in drop:
            #: a hit with no sibling still carries the literal list in its description, and it is
            #: the majority of hits - about 100 characters a query across the whole result set,
            #: not only across the paired ones
            if attach:
                h = dict(h, description=_served_text(h.get("description", "") or ""))
            out.append(h)
    return out


#: Track N (part 3.1). The payload was decomposed from the strings the supersession stand returned:
#: about 100 characters a query of `[facts]`, 104-130 of attached earlier statement, and the note.
#: Both switches default to the lighter behaviour and exist so a missed gate is reverted, not argued
#: with - the same shape K5 and K7 were left in.
#: H2 - "the literal list is weight the reader does not need" - was REFUTED on the stand, not
#: reasoned away. Six cases, everything else held: serving the block reads stale 0.000 and
#: current 1.000 at 398.5 characters; stripping it reads stale 0.167 and current 0.833 at
#: 326.5. The literals are not a second copy of the sentence - they are what the reader (and
#: the scorer) matches the current fact on, even where the same values appear in the prose.
#: So the default serves them, and the switch is here for whoever wants to re-open it with a
#: mechanism that keeps findability.
SERVE_FACTS_BLOCK = os.environ.get("NEVERTWICE_SERVE_FACTS", "1") != "0"
ATTACH_EARLIER_ALWAYS = os.environ.get("NEVERTWICE_ATTACH_EARLIER_ALWAYS", "0") != "0"


def _served_text(desc: str) -> str:
    """A note's description as the READER gets it: the sentence, without the literal list.

    The `[facts]` block is a list of verified literals the extractor harvested out of the very
    sentence it is appended to, so serving both hands the reader the same words twice - about 100
    characters a query, in every reading, including after the night. The block stays in the note
    and in the frontmatter, where rules 2 and 2' and the shape rule read it; only the copy that
    goes out over the wire is dropped. K1 measured this block's effect on ranking and did not
    confirm it, which is the reason to suspect it is weight rather than signal - and `R@5` on the
    retrieval stands is the measurement that would refute that, so it is in track N's gate.
    """
    if SERVE_FACTS_BLOCK or not desc:
        return desc
    mark = _FACTS_MARK.strip()
    if mark not in desc:
        return desc
    statement, block = desc.split(mark, 1)
    #: The block is a duplicate only when the sentence already says what it says. The extractor
    #: does not always put the value in both - `_earlier_text` prefers the block for exactly that
    #: reason - and a six-case smoke caught it: `current` fell to 0.833 because one fact lived in
    #: the block alone. Dropping it there would be losing a fact to save characters, which is the
    #: one trade this part is forbidden to make.
    have = {v.strip().lower() for v in _VALUE_RE.findall(statement)}
    need = {v.strip().lower() for v in _VALUE_RE.findall(block)}
    return statement.rstrip() if need <= have else desc


def _earlier_delta(earlier: str, current: str) -> str:
    """What the older statement carries that the newer one does not - its values, not its sentence.

    H1 of track N predicted that most attached lines are duplicates and could be dropped. Measured
    on the 212 lines the stand recorded, that is false: only 11 are, because a corpus of real
    replacements is one where the old value genuinely exists nowhere else. The prediction was wrong
    and the line has to stay.

    What does not have to stay is the sentence around the value. The reader needs "30 seconds", not
    "The HTTP client timeout was configured to 30 seconds. This ensures that API requests do not
    hang indefinitely." Measured on the same strings, carrying the values alone takes the explicit
    corpus from 473.3 to 352.8 characters a query and the implicit from 530.4 to 408.8, with the
    replaced value still in the payload every time.

    Returns "" when nothing verifiable differs, and the caller then keeps the sentence: an unproven
    pair is never resolved against the reader, which is the rule the whole K8 package turns on.
    """
    if ATTACH_EARLIER_ALWAYS:
        return ""
    have = {v.strip().lower() for v in _VALUE_RE.findall(current or "")}
    gone = [v.strip() for v in _VALUE_RE.findall(earlier or "") if v.strip().lower() not in have]
    return ", ".join(dict.fromkeys(gone))


def _earlier_is_informative(earlier: str, current: str) -> bool:
    """Does the older statement tell the reader something the newer one does not?

    The attached line is what buys zero loss, and it is not free: 104 characters a query on the
    explicit corpus, 130 on the implicit. The K8-C audit measured that it carries the ONLY copy of
    the fact in 3 of 20 explicit and 7 of 20 implicit controls - so in most pairs it repeats the
    newer note, and a repeat is weight with no reader.

    The test is the one that scored `replaces` precision 1.000 on 222 recorded pairs this morning:
    compare the verified literals. Different literal - the line stays, because the old value cannot
    be recovered from the new note. Same literal - it goes. **No literal at all - it stays**, and
    that asymmetry is deliberate: silence is not evidence of a duplicate, and an unproven pair is
    never resolved against the reader. That rule is what keeps control miss from moving, which is
    the one thing this track may not trade for weight.
    """
    if ATTACH_EARLIER_ALWAYS:
        return bool((earlier or "").strip())
    e = (earlier or "").strip()
    if not e:
        return False
    #: `_earlier_text` hands over the compact statement, not a `[facts]` block, so the comparison
    #: is on the value literals themselves - the same `_VALUE_RE` the write path's rules use.
    a = {v.strip().lower() for v in _VALUE_RE.findall(e)}
    b = {v.strip().lower() for v in _VALUE_RE.findall(current or "")}
    if not a or not b:
        return True                      # nothing verified to compare - keep it
    return bool(a - b)                   # the older statement carries a value the newer lacks


def _append_facts(desc: str, facts: list[str]) -> str:
    """Append verified literals to the one-line description, replacing any prior facts block so a
    re-mine never stacks them. Kept on the single description line so `_parse_note_body` reads it
    back as the description - which is what puts the literals into recall and the embedding."""
    base = re.sub(r"\s*\[facts\].*$", "", desc or "", flags=re.DOTALL).rstrip()
    if not facts:
        return base
    return f"{base}{_FACTS_MARK}" + " \u00b7 ".join(facts)


def _record_why(why: list | None, reason: str) -> None:
    """Append `reason` to a caller's `why` list, if it passed one (see write_typed_note)."""
    if why is not None:
        why.append(reason)


def _cut_word_boundary(text: str, limit: int) -> str:
    """Cap `text` at `limit` characters without splitting a word - used only for `principle`
    (A3, Q5). A local helper rather than reusing `_engine_cards.py`'s `_one_line` (the same
    word-boundary discipline, plus a sentence-boundary preference and a truncation marker):
    that helper lives in a LATER engine part, and a two-line write-path utility does not
    justify a forward reference across the engine's part ordering for a shared namespace this
    file does not otherwise reach into."""
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    head = s[:limit]
    sp = head.rfind(" ")
    return (head[:sp] if sp > 0 else head).rstrip()


def write_typed_note(folder: str, item, project: str, date: str,
                     tags: list, ntype: str,
                     session_stem_: str | None = None,
                     siblings: list[str] | None = None,
                     why: list | None = None) -> str:
    #: `why`, when given, receives the reason this call returned what it did: "written",
    #: "refused" (the M-10/W8 unsafe-payload screen), "quarantined" (W7: on disk for review, not
    #: served) or "skipped" (a crash retry of a note this session already quarantined). All three
    #: non-writes return "", and counting them as one number called a quarantine a refusal
    #: (review 2026-09-23, #12). A list the caller owns rather than a module-level record: nothing
    #: to prune, nothing shared between threads (R14). A caller that finds it empty - a stand-in
    #: writer in a suite - reads a refusal, the conservative reading.
    if isinstance(item, dict):
        # title goes through the same scrub as desc/prevention: it lands in the heading AND
        # the filename slug, so a secret-shaped string there would be baked in twice
        title = redact_secrets(_strip_lead_icon(item.get("title", "untitled")))
        desc = redact_secrets(item.get("description", ""))        # audit B11
        prevention = redact_secrets(item.get("prevention", ""))
        supersedes_title = (item.get("supersedes") or "").strip()
        contradicts_title = (item.get("contradicts") or "").strip()   # M-2
        resolves_title = (item.get("resolves") or "").strip()
        confidence = item.get("confidence")                            # M-10
        entities = _norm_entities(item.get("entities"))                # entity graph (Phase 1)
        relations = _norm_relations(item.get("relations"))             # typed edges (Phase 2)
        entity_types = _norm_entity_types(item.get("entity_types"))    # Brain layer (F1): {entity: type}
        principle_raw = item.get("principle")                          # A3 (Q5): cross-project layer
    else:
        title = _strip_lead_icon(str(item))
        desc = prevention = supersedes_title = contradicts_title = resolves_title = ""
        confidence = None
        entities = []
        relations = []
        entity_types = {}
        principle_raw = None

    # Every relation target becomes reachable BY CONSTRUCTION (review 2026-08; the
    # integrity checker measured 73% of live edges pointing at entities no note carried,
    # so relation_expand / notes_for_entity silently returned nothing for them). A note
    # that asserts an edge about X IS evidence about X - tagging the target here gives
    # every edge at least one anchor note. Both lists are already normalized to the same
    # safe token space; bounded at 8 entities + up to 8 targets = 16 max.
    for _edge in relations:
        _tgt = _edge.get("target")
        if _tgt and _tgt not in entities:
            entities.append(_tgt)

    # M-10/W8 memory-poisoning guard: refuse to persist extracted "knowledge" that looks like an
    # injection payload OR a bare dangerous imperative (exfiltration/destruction/security-bypass) -
    # defense-in-depth beyond secret redaction. Negation-gated so cautionary lessons survive.
    if _looks_unsafe(f"{title} {desc} {prevention}"):
        log(f"Rejected note (unsafe payload): {title[:50]!r}")
        _record_why(why, "refused")
        return ""

    # A3 (Q5): the `principle` field's own gate, applied independently of the title/desc/
    # prevention check above - a bad or over-long `principle` degrades to "" (dropped from the
    # frontmatter below) and NEVER refuses or alters the rest of the note. Order matches the
    # plan: not-a-string -> "", redact, cap at a word boundary, the same W8 unsafe-payload gate
    # (on the principle alone, not mixed into the whole-note check), then `principle_scan`
    # against the project slug plus this note's own entities - the write-time half of A3's
    # de-identification; A5's promoter re-scans against the whole project vocabulary later.
    principle = principle_raw if isinstance(principle_raw, str) else ""
    principle = redact_secrets(principle).strip()
    if len(principle) > PRINCIPLE_MAX_CHARS:
        principle = _cut_word_boundary(principle, PRINCIPLE_MAX_CHARS)
    if principle and _looks_unsafe(principle):
        principle = ""
    if principle:
        principle = principle_scan(principle, {project} | set(entities))

    p = VAULT / folder
    p.mkdir(exist_ok=True)
    slug = slugify(title)
    base_stem = typed_stem(date, project, ntype, title)

    # Per-session idempotency (audit C5) + same-day same-stem ABSORB (review 2026-08).
    # Idempotency: if THIS session already wrote a live note with the same identity -
    # e.g. a prior run crashed after writing it but before marking the session
    # processed - return that note instead of creating a -2 duplicate. This is what
    # lets process_session mark the session AFTER the writes (so a crash retries)
    # without the retry duplicating notes.
    absorb_into = None
    absorb_recur, absorb_sources = 0, set()
    absorb_self = False                 # W7: a same-session refresh is the note's own history
    contested_olds: list = []           # K8: same-slug notes kept apart as siblings, stamped after the write
    for old in _live_typed_paths(p, project, ntype, slug):
        try:
            prev_fm, _ = _read_frontmatter(old.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if session_stem_ and prev_fm.get("session") == session_stem_:
            # Same-session re-encounter (crash retry OR the B1 grown-transcript
            # re-process): REFRESH the note in place instead of the old stamp-only
            # skip. The caller pushes THIS extraction into the embedding cache and
            # SQLite, so returning without rewriting left stale text on disk while
            # retrieval ranked on text the note did not contain - the same
            # split-brain the absorb branch below already fixes (review 2026-08).
            # A pure crash retry rewrites identical content - harmless.
            r_old, s_old = _note_recur_sources(old)
            absorb_into = old
            absorb_recur, absorb_sources = r_old, set(s_old)
            absorb_self = True
            log(f"Same-session refresh (absorb): {old.stem}")
            break
        if old.stem == base_stem:
            # Same day, same slug, different (or unknown) session: the same lesson
            # re-encountered. supersede_note refuses self-stem retirement and
            # _unique_path would mint a '-2' twin standing as a second current truth.
            # ABSORB: the note is REWRITTEN IN PLACE below with the NEW statement and
            # carried recurrence/sources. Two review-2026-08 defects died here: the old
            # stamp-only absorb kept stale text on disk while update_embeddings pushed
            # the new text into cache/index (a split-brain no incremental path healed),
            # and session-less writers (api.remember retries, weekly distill re-runs)
            # bypassed absorb entirely, minting '-2' twins.
            _ok, _rule = _same_replacement(old, title, desc, supersedes_title, contradicts_title)
            if not _ok:
                # K8: not proven the same fact - a SIBLING (`-2`), the earlier statement keeps being
                # served, and the pair is stamped contested for the sleep-time judge.
                log(f"Same-stem sibling kept (K8, {_rule}): {old.stem}")
                contested_olds.append(old)
                continue
            log(f"Same-stem absorb (K8, {_rule}): {old.stem}")
            r_old, s_old = _note_recur_sources(old)
            absorb_into = old
            absorb_recur, absorb_sources = r_old, set(s_old)
    if session_stem_ and QUARANTINE_MODE:
        # a crash-retry must not duplicate a note already quarantined this session
        for old in _live_typed_paths(p / "Quarantine", project, ntype, slug):
            try:
                qfm, _ = _read_frontmatter(old.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if qfm.get("session") == session_stem_:
                log(f"Idempotent skip (already quarantined this session): {old.stem}")
                _record_why(why, "skipped")
                return ""

    # F9 (xhigh review): the note actually being written may be a `-2` sibling, not `base_stem` -
    # a same-session refresh (crash retry / grown transcript) absorbs into whichever live note this
    # session already owns, which can be a sibling minted because ANOTHER session took the base slug
    # first. Every "is this old note really MYSELF" check below must compare against that real
    # identity, not the freshly-slugified base: comparing against base_stem let the near-duplicate
    # gate return the absorb target itself (retired against its own stem, a spurious "supersede
    # failed") and silently dropped a legitimate explicit `supersedes: T` whose target happened to
    # BE base_stem (filtered out as if it were self-reference).
    write_stem = absorb_into.stem if absorb_into is not None else base_stem

    # Reconcile (audit H1 + M-2): retire prior versions / contradicted notes so current truth stays
    # single. (a) older same-slug note = a re-statement; (b) explicit `supersedes`/`contradicts`.
    # Retirement is DEFERRED into `to_retire` and executed only AFTER the W7 quarantine decision, so
    # a quarantined (uncorroborated, suspicious) note never retires a corroborated true one.
    retired: list[str] = []
    prior_recur = absorb_recur          # the absorbed note's history carries forward
    prior_sources: set = set(absorb_sources)
    to_retire: list = []
    retire_via: dict = {}               # J2b: how each retirement was decided (slug|explicit|twin)
    retire_hist: dict = {}              # W7: (recurrence, sources) of each note to retire, as read
    for old in _reconcilable_typed_paths(p, project, ntype, slug):
        if absorb_into is not None and old == absorb_into:
            continue                    # the absorb target is refreshed in place, never retired
        if old.stem != base_stem:
            if old in contested_olds:
                continue                # a same-day sibling already kept apart above
            _ok, _rule = _same_replacement(old, title, desc, supersedes_title, contradicts_title)
            if not _ok:
                # K8, the `r` branch: the same slug on another day used to retire the earlier note by
                # the title alone (7 of 17 still-true control notes on the two-day dating, ledger K8).
                # Now the earlier note stays live beside the new one and the pair is contested.
                log(f"Same-slug sibling kept (K8, {_rule}): {old.stem}")
                contested_olds.append(old)
                continue
            # A same-slug note is THIS lesson recurring: read its count + contributing sessions
            # BEFORE supersede drops it from the cache, then carry forward below - otherwise
            # recurrence is pinned at 1 forever and the recurrence-boost signal is dead (audit A3).
            # (old.stem == base_stem - same day, same slug, different session - never reaches
            # here: the idempotency block above ABSORBS it into the existing note, because
            # supersede_note refuses self-stem retirement; review 2026-08.)
            r_old, s_old = _note_recur_sources(old)        # one frontmatter read for both
            prior_recur = max(prior_recur, r_old)
            prior_sources |= s_old
            to_retire.append(old)
            retire_via[old] = "explicit" if _rule == "explicit" else "slug"
            retire_hist[old] = (r_old, s_old)
    _retire_slugs_seen: set = set()
    for other_title in (supersedes_title, contradicts_title):
        if not other_title:
            continue
        o_slug = slugify(other_title)
        # Dedup between the two fields (review 2026-08): the LLM often fills
        # supersedes AND contradicts with the SAME title - the double append made
        # supersede_note run twice on one path (second call fails: file already
        # moved), firing a spurious 'supersede failed' warning and listing the
        # stem twice in the frontmatter/body.
        if o_slug and o_slug != slug and o_slug not in _retire_slugs_seen:
            _retire_slugs_seen.add(o_slug)
            for old in _reconcilable_typed_paths(p, project, ntype, o_slug):
                if old in contested_olds:
                    continue                # F7: already kept apart above - do not also retire it
                if EXPLICIT_RETIRE == "judge":
                    # K8 switch: the extractor's claim is a hint for the sleep-time judge, not a proof
                    log(f"Explicit {('supersedes' if other_title == supersedes_title else 'contradicts')} "
                        f"held for the judge (K8): {old.stem}")
                    contested_olds.append(old)
                    continue
                # An explicit supersede/contradict (incl. the M-2 write-time semantic path) is ALSO a
                # re-encounter of that lesson - carry its recurrence + sources forward, else
                # recurrence only grows on the rare exact-slug re-statement (measured: 328/328 were 1).
                r_old, s_old = _note_recur_sources(old)
                prior_recur = max(prior_recur, r_old)
                prior_sources |= s_old
                to_retire.append(old)
                retire_via[old] = "explicit"
                retire_hist[old] = (r_old, s_old)

    # Near-duplicate reconcile (review 2026-08): catch the re-statements exact-slug matching
    # cannot see - the LLM re-mining the same work titles the lesson slightly differently
    # ('...-with-verification' vs '...-and-verification') and both stand as current truth,
    # sometimes with CONTRADICTORY resolutions. A found sibling joins to_retire exactly like
    # a same-slug older note: its recurrence/sources carry into the new note, so the lesson
    # RECURS instead of fragmenting into twins.
    if WRITE_DEDUP_SIM > 0:
        # F9: exclude the absorb target's REAL stem too (write_stem), not just base_stem - else a
        # same-session refresh into a `-2` sibling found itself as its own "near duplicate" and was
        # written as superseding its own stem.
        _nd_exclude = ({base_stem, write_stem} | {o.stem for o in to_retire}
                       | {o.stem for o in contested_olds})
        for old in _near_duplicate_paths(p, project, ntype, title, desc, prevention,
                                         _nd_exclude, entities=entities):
            r_old, s_old = _note_recur_sources(old)
            prior_recur = max(prior_recur, r_old)
            prior_sources |= s_old
            to_retire.append(old)
            retire_via[old] = "twin"
            retire_hist[old] = (r_old, s_old)
            log(f"Near-duplicate reconcile: {old.stem} retires in favor of {base_stem}")

    # W7 corroboration-gated quarantine (opt-in; see QUARANTINE_MODE). Divert a single-source note
    # that is also suspicious to <folder>/Quarantine/ instead of trusting it - retiring NOTHING.
    dest = p
    quarantine_reason = ""
    if QUARANTINE_MODE:
        #: One rule for every note this write absorbs or retires, however it was found (same slug,
        #: explicit supersedes, near-duplicate twin, same-day absorb): a note PROVEN to state the
        #: same fact - `_same_replacement`'s literal/restated rules, asked without the explicit
        #: title - corroborates the new statement with its sessions; a corroborated note that is
        #: NOT the same fact is being overturned. A session's refresh of its own note is its own
        #: history. Five review rounds each found a case the earlier per-path rules got wrong -
        #: the explicit rule firing before a verbatim restatement, a contradicting twin, an honest
        #: twin (third to sixth reviews, 2026-09-23); the path a note came by decides none of it.
        same_lesson, overturned = set(), False
        judged = ([(absorb_into, absorb_recur, absorb_sources)] if absorb_into is not None else [])
        judged += [(o, *retire_hist[o]) for o in to_retire if o in retire_hist]
        for note, r_x, s_x in judged:
            if (note is absorb_into and absorb_self) or _same_replacement(note, title, desc, "", "")[0]:
                same_lesson |= set(s_x)
            elif r_x >= 2:
                overturned = True
        n_sources = len(same_lesson | ({session_stem_} if session_stem_ else set())) or 1
        qconf = _coerce_confidence(confidence)
        if n_sources < 2 and qconf is not None and qconf >= QUARANTINE_CONF:
            quarantine_reason = "single-source near-max confidence"
        elif n_sources < 2 and overturned:
            quarantine_reason = "single-source supersedes a corroborated note"
        if quarantine_reason:
            dest = p / "Quarantine"
            dest.mkdir(exist_ok=True)

    if not quarantine_reason:
        # PLAN the retirement here (the stems feed the new note's frontmatter/body
        # links) but EXECUTE it only after the replacement is durably on disk - a
        # hook-timeout kill between retire and write used to leave the lesson retired
        # with a dangling superseded_by and nothing live, resetting its recurrence
        # provenance forever (review 2026-08 B5).
        # F9: compare against write_stem (this note's REAL identity), not base_stem - see above.
        retired = [old.stem for old in to_retire if old.stem != write_stem]

    if absorb_into is not None and not quarantine_reason:
        fp = absorb_into                # rewrite in place: same stem, no '-2' twin
        log(f"Absorbing same-stem re-encounter into {absorb_into.stem}")
    else:
        fp = _unique_path(dest, base_stem)
    stem = fp.stem

    # Link a resolving pattern/decision to the mistake it fixes (audit I-18): the
    # mistake is flagged resolved (no longer an active warning) but kept.
    # PLAN here, EXECUTE after the write (same B5 deferral as retirement): calling
    # mark_resolved before the resolver was durably on disk meant a hook-timeout
    # kill left the mistake de-weighted with resolved_by pointing at a note that
    # never landed (review 2026-08).
    resolved: list[str] = []
    resolve_targets: list[Path] = []
    if resolves_title and ntype in ("pattern", "decision") and not quarantine_reason:
        r_slug = slugify(resolves_title)
        if r_slug:
            # F8 (xhigh review): the EXACT slug only (pre-K8 behaviour), not the whole slug family -
            # `_live_typed_paths` now folds in `-N` siblings too, which used to stamp `status:
            # resolved` on every contested sibling of the named mistake, not just the one this
            # decision/pattern actually names. A contested sibling is resolved only when named.
            resolve_targets = [mp for mp in _live_typed_paths(VAULT / TYPE_FOLDER["mistake"],
                                                              project, "mistake", r_slug)
                               if (parse_typed_stem(mp.stem) or {}).get("slug") == r_slug]
            resolved = [mp.stem for mp in resolve_targets]

    # An absorb rewrite used to rebuild frontmatter from scratch, carrying forward only
    # recurrence and sources. Everything else was silently dropped - `status`,
    # `resolved_by`, `resolves`, `relations`, `salience`, `supersedes` - which REOPENED
    # shipped fixes: the mistake lost `status: resolved` while the decision that fixed it
    # still carried `resolves:` pointing at it, so a dead bug was re-injected at
    # SessionStart against a fix that was committed and tested (vault review 2026-09).
    # These are carried unless the new extraction supplies its own value.
    # F1 (xhigh review): CONTESTED_KEY/DISPUTED_KEY belong on this list too - an absorb rewrite
    # (a same-session refresh) dropped the earlier note's `contested`/`disputed` stamp exactly the
    # same way, and the reconcile loop below then re-discovered the kept-apart sibling and stamped
    # IT contested against the refreshed note - the sleep-time judge read OLD/NEW inverted and
    # retired the still-true sibling.
    _carried: dict = {}
    if absorb_into is not None:
        try:
            _old_fm = _read_frontmatter_file(absorb_into)
        except Exception:                       # a corrupt prior must not lose the new note
            _old_fm = {}
        for _k in _ABSORB_CARRY_FIELDS:
            if _old_fm.get(_k) not in (None, "", [], {}):
                _carried[_k] = _old_fm[_k]

    fm = {"date": date, "project": project, "tags": tags, "type": ntype}
    # M-5. Unlike every other frontmatter field, valid_from used to pass the raw LLM
    # value through - a crafted multi-line string could close the YAML fence early
    # (review 2026-08). Only a bare ISO date is accepted; anything else → today.
    _vf = item.get("valid_from") if isinstance(item, dict) else None
    _vf = _vf.strip() if isinstance(_vf, str) else ""
    fm["valid_from"] = _vf if re.fullmatch(r"\d{4}-\d{2}-\d{2}", _vf) else date
    if session_stem_:
        fm["session"] = session_stem_          # provenance (M-10)
    cval = _coerce_confidence(confidence)
    if cval is not None:
        fm["confidence"] = round(cval, 2)   # M-10 (read back in ranking - H2)
    if entities:
        fm["entities"] = entities           # entity graph (Phase 1): faceted recall + co-occurrence
    if relations:
        fm["relations"] = relations         # typed edges (Phase 2): relation-aware multi-hop
    if entity_types:
        fm["entity_types"] = entity_types   # Brain layer (F1): {entity: paper|method|...} for entity cards
    if principle:
        fm["principle"] = principle         # A3 (Q5): the cross-project, de-identified restatement
    if quarantine_reason:
        fm["quarantine_reason"] = quarantine_reason       # W7 provenance (kept out of recall)
    # Recurrence carry-forward, hardened against recurrence-GAMING (3B): the count is the
    # number of DISTINCT contributing sessions, so re-stating a false lesson from ONE
    # session can't inflate its salience. No provenance (session=None) → legacy +1 (an
    # anonymous source can't be deduped). A known session already in the set adds nothing.
    # A quarantined note must NOT inherit the trust history (recurrence/sources) of notes it did
    # not retire - else, if later promoted, it arrives with corroboration it never earned (W7 audit).
    if (prior_recur or prior_sources) and not quarantine_reason:
        if session_stem_ is None:
            fm["recurrence"] = prior_recur + 1                  # anonymous source: legacy +1
            if prior_sources:                                   # keep provenance across an absorb rewrite
                fm["sources"] = sorted(prior_sources)[-RECUR_SOURCES_CAP:]
        else:
            sources = prior_sources | {session_stem_}
            grew = session_stem_ not in prior_sources           # a known session adds nothing
            fm["recurrence"] = max(prior_recur + (1 if grew else 0), len(sources))
            # Cap keeps the NEWEST stems (date-prefixed → lexicographic = chronological):
            # sorted()[:cap] used to drop the newest, so the absorb idempotency check
            # ('session already in sources') could never fire again at the cap and every
            # crash-retry re-bumped recurrence (review 2026-08).
            fm["sources"] = sorted(sources)[-RECUR_SOURCES_CAP:]
    if retired:
        fm["supersedes"] = retired
    if resolved:
        fm["resolves"] = resolved
    icon = TYPE_ICON.get(ntype, "")
    # Apply what the absorb carried, WITHOUT overwriting anything this extraction set:
    # a fresh `resolves` or a new `status` is newer information and wins. Everything the
    # new pass simply did not mention keeps the value the note already had.
    for _k, _v in _carried.items():
        fm.setdefault(_k, _v)
    if absorb_into is None and stem != base_stem:
        # F7 (xhigh review): sibling identity is a STAMP, not a name pattern - `_unique_path`
        # just minted `stem` because `base_stem` collided (K8: a same-slug note kept apart, or a
        # plain same-day title collision). Name it explicitly so `_slug_family`/`_sibling_key`
        # never have to guess from the suffix alone.
        fm[SIBLING_KEY] = base_stem

    body_tags = render_body_tags(tags, [f"project/{project}", ntype])

    # Absorb must not DISCARD the previous statement (review 2026-08): the rewrite
    # replaced the whole file with the new extraction, so a worse (hallucinated)
    # re-extraction silently destroyed the earlier description/prevention with no
    # Superseded/ copy. Carry differing old fragments under a bounded block, the
    # same idea as consolidation's 'Merged from duplicates'.
    absorbed_prev: list[str] = []
    if absorb_into is not None and not quarantine_reason:
        try:
            _old_lines = absorb_into.read_text(encoding="utf-8",
                                               errors="replace").split("\n")
            _, _d_old, _p_old = _parse_note_body(_old_lines)
        except OSError:
            _d_old = _p_old = ""
        # If the new extraction supplied no "how to avoid", inherit the one the note
        # already had instead of demoting it to a historical fragment. A mistake note
        # whose prevention half is missing tells an agent that something broke and not
        # what to do differently - which is the failure this whole store exists to
        # prevent, and 659 of 1549 live mistakes were in that state (review 2026-09).
        if not (prevention or "").strip() and (_p_old or "").strip():
            prevention = _p_old.strip()
        for _frag in (_d_old, _p_old):
            _frag = (_frag or "").strip()
            if _frag and _frag not in desc and _frag not in prevention:
                absorbed_prev.append(_frag[:400])

    body = [fm_block(fm), "", f"# {icon} {title}".strip(), ""]
    if desc:
        body += [desc, ""]
    if prevention:
        body += [f"**Prevention:** {prevention}", ""]
    if absorbed_prev:
        body += ["## Previous statement", *(f"- {f}" for f in absorbed_prev), ""]
    body += [f"**Project:** [[{project}]]", f"**Date:** {date}"]
    if session_stem_:
        body.append(f"**Session:** [[{session_stem_}]]")
    if retired:
        body += ["", "_Supersedes: " + ", ".join(f"[[{s}]]" for s in retired) + "_"]
    if resolved:
        body += ["", "_Resolves: " + ", ".join(f"[[{s}]]" for s in resolved) + "_"]

    related = [s for s in (siblings or []) if s and s != stem]
    if related:
        body += ["", "## Related notes", *(f"- [[{s}]]" for s in related)]

    body += ["", body_tags]
    write_atomic(fp, "\n".join(body))
    if quarantine_reason:
        log(f"Quarantined note ({quarantine_reason}): {folder}/Quarantine/{fp.name}")
        _record_why(why, "quarantined")
        return ""                       # on disk for review, but NOT embedded/recalled (W7)
    # Deferred retirement (B5): the replacement is on disk - now the old truth may go.
    # A failure here leaves the old note live BESIDE the new one (recoverable by the
    # weekly consolidator), never a retired lesson with no live successor.
    failed_retire = [old.stem for old in to_retire
                     if old.stem != write_stem                    # F9: this note's real identity
                     and not supersede_note(old, stem, via=retire_via.get(old, "slug"))]
    if failed_retire:
        log(f"WARNING: supersede failed after write for {', '.join(failed_retire[:3])}"
            f"{'…' if len(failed_retire) > 3 else ''} - old note(s) still live beside {stem}")
    # Deferred resolve-marking (same B5 rationale): the resolver is on disk - now the
    # mistakes may be de-weighted. A failure leaves the warning active (safe side).
    failed_resolve = [mp.stem for mp in resolve_targets if not mark_resolved(mp, stem)]
    if failed_resolve:
        log(f"WARNING: mark_resolved failed for {', '.join(failed_resolve[:3])} - "
            f"mistake(s) stay active despite resolver {stem}")
    # K8: the earlier notes this one was kept apart from carry the pair for the sleep-time judge.
    # F1 (xhigh review): never invert the stamp. When THIS note's own (carried-forward) `contested`
    # already names `old`, the pair is already recorded in the correct direction - earlier note
    # contested against later - and stamping `old` contested against `stem` here would ALSO record
    # it backwards: a same-session refresh absorbing the earlier note of a kept-apart pair back into
    # itself used to reach this loop with the later sibling in `contested_olds`, and would otherwise
    # stamp the later sibling `contested: [stem]` - the judge then reads OLD/NEW swapped.
    _already_contests = set(_contested_of(fm)) if fm.get(CONTESTED_KEY) else set()
    for old in contested_olds:
        if old.stem != stem and old.stem not in _already_contests and _mark_contested(old, stem):
            log(f"Contested: {old.stem} <- {stem}")
    _ndup_register(stem, project, ntype, title, desc, prevention, entities)
    log(f"Written: {folder}/{fp.name}")
    _record_why(why, "written")
    return stem


def write_session_note(project: str, date: str, time_str: str, summary: str,
                       cwd: str, session_id: str, tags: list,
                       links: dict[str, list[str]], trigger: str,
                       agent: str | None = None, stem: str | None = None) -> str:
    # Resolved HERE, not in the signature: a default argument is evaluated once at
    # def time, so a module constant frozen there stops answering to the module.
    agent = DEFAULT_AGENT if agent is None else agent
    p = VAULT / "Sessions"
    p.mkdir(exist_ok=True)
    if stem:
        # The caller reserved the collision-free stem BEFORE typed notes stamped it as
        # provenance (reserve_session_stem) - writing to exactly that name keeps the
        # frontmatter `session` links true. If the file already exists it is this same
        # session's crash-retry (the reservation reuses a stem only when the on-disk
        # note carries our session_id), so overwriting refreshes rather than duplicates.
        fp = p / f"{stem}.md"
    else:
        # Legacy path (no reservation): _unique_path so two distinct sessions never
        # silently overwrite each other on a stem collision (audit 2026-06-18 HIGH).
        fp = _unique_path(p, session_stem(date, time_str, project, session_id))
    stem = fp.stem

    # session_id is stored WHOLE: the 8-char slice made notes untraceable to their source
    # transcript ('ingest-f' x34) and broke the identity checks built on it (review 2026-08).
    fm = {"date": date, "project": project, "tags": tags, "type": "session",
          "session_id": session_id, "trigger": trigger, "agent": agent}
    body_tags = render_body_tags(tags, [f"project/{project}", "session"])

    sections = [
        fm_block(fm), "",
        f"# Session - {project} ({time_str})", "",
        summary, "",
        f"**Directory:** `{cwd}`",
        f"**Trigger:** {trigger}",
        f"**Project:** [[{project}]]",
        "",
    ]
    # MERGE with what the note already lists instead of replacing it. The lists were
    # rebuilt from the LATEST extraction only, so a re-mine orphaned everything the
    # previous pass had written: one session note was rewritten four times in a day and
    # ended up indexing a fifth, unrelated cluster, while 18 notes carrying that same
    # session id appeared in neither the note nor the project Context (review 2026-09).
    # An agent following Index -> Context -> note could not reach them at all, while
    # entity pages still asserted the conclusions those notes were the source for.
    merged: dict[str, list[str]] = {nt: list(links.get(nt, []) or []) for nt in TYPED_TYPES}
    if fp.exists():
        try:
            _prior_text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            _prior_text = ""
        for _lk in re.findall(r"\[\[([^]|#]+)", _prior_text):
            _lk = _lk.strip()
            _parsed = parse_typed_stem(_lk)
            if not _parsed:
                continue                       # project/session links are not typed notes
            _nt = _parsed.get("ntype")
            if _nt in merged and _lk not in merged[_nt]:
                merged[_nt].append(_lk)        # keep this pass first, then what was there
    for nt in TYPED_TYPES:
        block = _link_section(merged.get(nt, []), NTYPE_LABEL[nt])
        if block:
            sections += [block, ""]
    sections.append(body_tags)

    write_atomic(fp, "\n".join(sections))
    log(f"Written: Sessions/{fp.name}")
    return stem


def _split_context(text: str) -> tuple[str, list[str]]:
    """Split a Context file into (head, entries). `head` is the frontmatter +
    title + intro; `entries` are the per-session '## <date>' blocks plus any
    existing compressed-state block."""
    lines = text.split("\n")
    idx = None
    for i, ln in enumerate(lines):
        if re.match(r"^##\s+(\d{4}-\d{2}-\d{2}|Накопленное состояние|Accumulated state)", ln):
            idx = i
            break
    if idx is None:
        return text.rstrip(), []
    head = "\n".join(lines[:idx]).rstrip()
    entries, cur = [], []
    for ln in lines[idx:]:
        # anchor boundaries to REAL entry headers only (date or state), so a
        # '## subheading' inside an entry/state body stays attached (audit D4)
        if re.match(r"^##\s+(\d{4}-\d{2}-\d{2}|Накопленное состояние|Accumulated state)", ln):
            if cur:
                entries.append("\n".join(cur).rstrip())
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        entries.append("\n".join(cur).rstrip())
    return head, entries


CONTEXT_STATE_MIN_BYTES = 200   # smallest room worth writing a state block into


def _spill_context_entries(project: str, entries: list) -> str:
    """Append entries VERBATIM to Context/Archive/<project>-overflow.md and return the
    archive filename. The escape hatch for every case where content does not fit the
    cap: knowledge is MOVED, never dropped, and no LLM is involved."""
    arch_dir = VAULT / "Context" / "Archive"
    arch_dir.mkdir(parents=True, exist_ok=True)
    afp = arch_dir / f"{project}-overflow.md"
    try:
        prev = afp.read_text(encoding="utf-8", errors="replace") if afp.exists() else ""
    except OSError:
        prev = ""
    write_atomic(afp, (prev.rstrip() + "\n\n" if prev.strip() else "")
                 + "\n\n".join(entries) + "\n")
    return afp.name


def _fit_context_tail(project: str, head: str, recent: list, reserve: int) -> list:
    """Shrink the KEPT tail until `reserve` bytes remain for a state/summary block.

    Entries that have to go are spilled verbatim to the overflow archive; a single
    entry too large to fit alone is truncated in place with a marker pointing at the
    archive copy. Writing over budget instead (review 2026-08-24) handed enforcement
    to the blind whole-file truncate, which chops the file TAIL - i.e. amputates the
    NEWEST entries, the exact failure the fitted-block change exists to prevent."""
    def _room(entries: list) -> int:
        tail = "\n\n" + "\n\n".join(entries) + "\n" if entries else "\n"
        return (CONTEXT_MAX_BYTES - len(head.encode("utf-8"))
                - len(tail.encode("utf-8")) - 2)

    spilled = []
    while len(recent) > 1 and _room(recent) < reserve:
        spilled.append(recent.pop(0))          # oldest KEPT entry goes to the archive
    if recent and _room(recent) < reserve:
        # One entry alone crowds the cap. Keep a truncated head of it in the file so
        # the tail stays readable, with the full text preserved in the archive.
        name = _spill_context_entries(project, spilled + [recent[0]])
        spilled = []
        marker = f"\n\n_(entry truncated - full text in Context/Archive/{name})_"
        room = (CONTEXT_MAX_BYTES - len(head.encode("utf-8")) - reserve
                - len(marker.encode("utf-8")) - 4)
        recent = [_truncate_utf8_bytes(recent[0], max(200, room)).rstrip() + marker]
    if spilled:
        name = _spill_context_entries(project, spilled)
        log(f"Context tail did not fit for {project}: {len(spilled)} entr(ies) "
            f"spilled verbatim to Context/Archive/{name}")
    return recent


_STATE_HEADINGS = ("## Accumulated state", "## Накопленное состояние")


def _accumulated_header(old: list[str], state: str) -> str:
    """Build the compacted block so its counter and span are CUMULATIVE.

    Both used to describe only the current pass. The counter reported the entries folded
    by THIS run, so a second compaction wrote "compacted from 2" over "compacted from 8"
    -- and because the next pass then believed only 2 had ever been folded, the hole was
    undetectable. The span collapsed the same way: (2026-05-04 → 2026-08-28) became
    (2026-05-04 → 2026-05-04). Both measured in the 2026-09 vault review, in projects
    that had never been re-extracted.

    A previous accumulated block is ONE entry in `old` but stands for the count it
    carries; counting it as one is what made the number shrink.
    """
    prior_count, prior_first = 0, ""
    for e in old:
        if e.startswith(_STATE_HEADINGS):
            c = re.search(r"ompacted from (\d+)", e)
            if c:
                prior_count = max(prior_count, int(c.group(1)))
            sp = re.search(r"\((\d{4}-\d{2}-\d{2})\s*[→-]", e)
            if sp:
                prior_first = sp.group(1)
    m_old = re.search(r"(\d{4}-\d{2}-\d{2})", old[0]) if old else None
    m_new = re.search(r"(\d{4}-\d{2}-\d{2})", old[-1]) if old else None
    first = prior_first or (m_old.group(1) if m_old else "")
    last = m_new.group(1) if m_new else ""
    span = f" ({first} → {last})" if (first and last) else ""
    folded = prior_count + len([e for e in old if not e.startswith(_STATE_HEADINGS)])
    return (f"## Accumulated state (compacted){span}\n\n{state.strip()}\n\n"
            f"_Compacted from {folded} earlier entries._")


def compact_context_if_needed(fp: Path, project: str, allow_llm: bool = True):
    """When a Context file outgrows CONTEXT_MAX_BYTES, fold the oldest entries into
    one rolling 'state' block and keep only the most recent verbatim (audit
    F4/F23/F37). Idempotent: a no-op on an already-small file.

    `allow_llm=False` (the LIVE hook path) makes this a DEFERRED no-op: no model
    call is ever made under the vault lock (audit C4). The actual summary+compaction
    runs in scheduled maintenance (`maintain_contexts` from process_now/consolidate,
    `allow_llm=True`). Compaction only rewrites the file when a real summary is
    produced; on a backend failure it leaves the file UNCHANGED rather than dropping
    entry bodies - no data loss (failure-injection probes). The SessionStart payload
    reads the bounded project card, not the raw file, so a briefly-oversized Context
    never bloats injection."""
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if len(text.encode("utf-8")) <= CONTEXT_MAX_BYTES:
        return
    if not allow_llm:
        # Deferred to scheduled maintenance - never call the LLM under the lock.
        # But the cap must not depend on those tasks being alive (review 2026-08:
        # with the safety net down the file grew without bound): at 4x the cap,
        # spill the oldest entries VERBATIM to Context/Archive/<project>-overflow.md
        # - no LLM, no data loss - and keep the newest under the cap.
        if len(text.encode("utf-8")) <= CONTEXT_MAX_BYTES * 4:
            return
        head, entries = _split_context(text)
        if len(entries) < 2:
            return
        budget = max(0, CONTEXT_MAX_BYTES - len(head.encode("utf-8")) - 200)
        recent, used = [], 0
        for e in reversed(entries):
            b = len(e.encode("utf-8")) + 2
            if recent and used + b > budget:
                break
            recent.append(e)
            used += b
        recent.reverse()
        old = entries[:len(entries) - len(recent)]
        if not old:
            return
        afp_name = _spill_context_entries(project, old)
        # The keep loop admits the newest entry unconditionally, so it can still
        # exceed the cap on its own - and this branch had no size guard at all
        # (review 2026-08-24: measured 20173 B written against a 12000 cap, then
        # untouched by the live path until it re-crossed 4x). Fit the tail.
        recent = _fit_context_tail(project, head, recent, 0)
        write_atomic(fp, head + "\n\n" + "\n\n".join(recent) + "\n")
        log(f"Context emergency-capped for {project}: {len(old)} entr(ies) spilled "
            f"to Context/Archive/{afp_name} (LLM compaction still pending)")
        return
    head, entries = _split_context(text)
    if len(entries) < 2:
        return  # nothing to compress

    # Choose how many trailing entries fit verbatim under the cap, reserving room
    # for the head and the regenerated state block. The byte cap is HARD now:
    # keep shrinks toward CONTEXT_KEEP_MIN, so a few large entries can't blow past
    # it (audit M2). Any prior state block is the oldest entry → folded back in.
    reserve = 1800  # state block + archive-links budget
    budget = max(0, CONTEXT_MAX_BYTES - len(head.encode("utf-8")) - reserve)
    recent, used = [], 0
    for e in reversed(entries):
        b = len(e.encode("utf-8")) + 2
        if recent and used + b > budget:
            break
        recent.append(e)
        used += b
    recent.reverse()
    lo = min(CONTEXT_KEEP_MIN, len(entries) - 1)        # continuity floor
    hi = min(CONTEXT_KEEP_RECENT, len(entries) - 1)     # ceiling
    n_fit = len(recent)                                 # newest entries that FIT the budget
    n_keep = max(lo, min(n_fit, hi))
    # The floor is a preference; the byte cap is the contract. Forcing the floor
    # past the budget made the final hard-truncate chop the NEWEST kept entries'
    # tails without summarization (review 2026-08) - shrink below the floor instead.
    if n_keep > n_fit:
        n_keep = max(1, n_fit)
    recent = entries[len(entries) - n_keep:] if n_keep else []
    old = entries[:len(entries) - n_keep]
    if not old:
        old, recent = entries[:1], entries[1:]

    prompt = (
        f"Compact the history of project '{project}' into a brief accumulated state.\n"
        f"Keep: key decisions with dates, the current status, open questions, "
        f"important pitfalls. Drop filler and duplicates. 8-15 lines, markdown "
        f"bullets, in the language the history is written in. Return ONLY JSON: "
        f'{{"state": "<compacted text>"}}.\n\n'
        f"HISTORY:\n" + truncate_smart(redact_secrets("\n\n".join(old)),
                                       MAX_TRANSCRIPT_CHARS)
    )
    res = generate_json(prompt, project=project)
    state = res.get("state") if isinstance(res, dict) else None
    if not isinstance(state, str) or not state.strip():
        # leave the file UNCHANGED on a backend failure - never drop entry bodies
        # without a summary (no data loss); the next maintenance run retries.
        log(f"Context compaction skipped for {project} (no summary)")
        return
    # preserve wikilinks from compacted entries (bounded) so compaction never
    # orphans a note from the graph yet can't grow without limit (fuzz PROBE 2 / M2)
    old_links, seen_l = [], set()
    for e in old:
        for lnk in re.findall(r"\[\[([^]|#]+)", e):
            lnk = lnk.strip()
            if lnk and lnk not in seen_l:
                seen_l.add(lnk)
                old_links.append(lnk)
    if len(old_links) > CONTEXT_LINK_ARCHIVE_MAX:
        old_links = old_links[-CONTEXT_LINK_ARCHIVE_MAX:]
    base = _accumulated_header(old, state)

    def _with_links(links):
        return base + ("\n\n**Link archive:** "
                       + " ".join(f"[[{lk}]]" for lk in links) if links else "")

    # Fit the COMPRESSED block to the room actually left by head + kept entries.
    # The flat `reserve` above is only a split heuristic: a long summary or a large
    # link archive overflowed it, and the old cap guard then truncated the file
    # TAIL - amputating the NEWEST entries (review 2026-08 A4). Overflow order:
    # first make room in the tail itself (oldest kept entries spill verbatim to the
    # archive), then drop archive links oldest-first (the linked notes still exist
    # on disk), then trim the state text.
    recent = _fit_context_tail(project, head, recent, CONTEXT_STATE_MIN_BYTES)
    tail = "\n\n" + "\n\n".join(recent) + "\n"
    comp_budget = (CONTEXT_MAX_BYTES - len(head.encode("utf-8"))
                   - len(tail.encode("utf-8")) - 2)
    compressed = _with_links(old_links)
    while old_links and len(compressed.encode("utf-8")) > comp_budget:
        old_links = old_links[1:]
        compressed = _with_links(old_links)
    if len(compressed.encode("utf-8")) > comp_budget:
        compressed = _truncate_utf8_bytes(compressed, max(comp_budget, 0)).rstrip()
    new_text = head + "\n\n" + compressed + tail
    # Belt-and-braces cap guard (audit M2/M-g), now genuinely unreachable after the
    # tail fit above. CAP-1 before re-appending the newline: truncating to exactly
    # the cap and then adding "\n" wrote cap+1 bytes, and since the entry gate is
    # `<= cap` the file never re-qualified - every scheduled maintenance pass
    # recompacted it forever, re-summarizing its own summary (review 2026-08-24).
    if len(new_text.encode("utf-8")) > CONTEXT_MAX_BYTES:
        new_text = _truncate_utf8_bytes(new_text, CONTEXT_MAX_BYTES - 1).rstrip() + "\n"
    write_atomic(fp, new_text)
    log(f"Context compacted: {project} ({len(old)} entries → state, kept {len(recent)})")


def maintain_contexts(allow_llm: bool = True) -> None:
    """Scheduled maintenance over all Context files: LLM-summary compaction of
    oversized files + project-card refresh. Kept OFF the live hook path (where
    compaction is GPU-free), so a model call is never made under the vault lock
    (audit C4). Called by process_now (4-hourly) and consolidate (weekly)."""
    cdir = VAULT / "Context"
    if not cdir.exists():
        return
    for cf in cdir.glob("*.md"):
        proj = cf.stem
        try:
            compact_context_if_needed(cf, proj, allow_llm=allow_llm)
            refresh_project_card(proj, cf)
        except Exception as e:
            log(f"context maintenance skipped for {proj}: {e}")
        refresh_lock()      # F6, per unit: consolidate --apply holds the lock across every file


