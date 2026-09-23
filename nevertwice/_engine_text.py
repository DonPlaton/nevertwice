# -- engine part 2 of 8: arithmetic and text helpers, slugs, stems, tags, the twin gate, project derivation --
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
# Lines 799-2003 of the pre-split `_engine.py`, whose body these parts reproduce byte for byte
# (sha256 bddf5d32a8883f0fdcfbd420de58e66fc9151be1ef05c696dedb44eefd0ac40f). Order is
# load-bearing: module-level code below runs after every earlier part and before every later
# one. `tests/_engine_source.py` reconstructs the whole body from these files.
#<<<ENGINE-PART-BODY>>>
# ── Shared low-level helpers ──────────────────────────────────────────

# Extracted to `store_state.py` (GOAL E4, seam `store/state`) and re-exported here so every
# caller's `from memory_hook import write_atomic` keeps working unchanged.
# `tests/_test_characterize_store_state.py` was written against this code BEFORE it moved and
# runs unchanged after, which is what makes the move checkable rather than merely plausible.
_store_state = _sibling("store_state")
write_atomic = _store_state.write_atomic
_replace_with_retry = _store_state._replace_with_retry
_REPLACE_RETRY_S = _store_state._REPLACE_RETRY_S


def cosine(a, b, na: float | None = None) -> float:
    # robust to corrupt cache entries: non-list, mismatched length, or
    # non-numeric elements all score 0.0 instead of crashing (fuzz PROBE 5).
    # `na` = precomputed norm of `a`: the scoring loops compare ONE query vector
    # against hundreds of candidates, and recomputing its norm per candidate was
    # a third of the whole scoring cost (perf review 2026-08).
    if not isinstance(a, list) or not isinstance(b, list) or len(a) != len(b):
        return 0.0
    try:
        s = sum(x * y for x, y in zip(a, b))
        if na is None:
            na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
    except (TypeError, ValueError):
        return 0.0
    # a NaN/inf in a (malformed) cloud embedding would make every comparison False and
    # so slip past the confidence gate as a phantom top hit - treat a non-finite vector
    # as no signal (0.0) instead (launch-round audit 2026-06-20).
    if not (math.isfinite(na) and math.isfinite(nb) and math.isfinite(s)):
        return 0.0
    return s / (na * nb) if na and nb else 0.0


def _strip_json_fence(raw: str) -> str:
    """Strip an outer ```json / ``` code fence from a model response - WITHOUT
    re.MULTILINE (audit M-j). The round-1 `re.sub(r"^```...|```$", flags=re.M)`
    matched a fence on ANY line, so a JSON whose string value contained a ```
    code block was truncated mid-document. Here only the single outermost fence
    wrapping the whole payload is removed; fences inside string values survive."""
    s = (raw or "").strip()
    if s.startswith("```"):                 # drop the opening fence line (```json / ```)
        nl = s.find("\n")
        s = s[nl + 1:] if nl != -1 else s[3:]
    if s.endswith("```"):                   # drop the closing fence
        s = s[:-3]
    return s.strip()


def _truncate_utf8_bytes(text: str, max_bytes: int) -> str:
    """Truncate `text` to at most `max_bytes` UTF-8 bytes on a CHARACTER boundary
    (audit M-g). Slicing encoded bytes then decoding with errors='ignore' drops a
    partial trailing code point - corrupting the tail of multibyte (e.g. Cyrillic)
    text. This walks back to the last whole character that fits."""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    # binary-search the longest character prefix whose UTF-8 length fits
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(text[:mid].encode("utf-8")) <= max_bytes:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


# Cyrillic → Latin so auto-generated note filenames stay ASCII and portable
# across case-sensitive / non-UTF-8 filesystems and git remotes (audit F40).
_CYR = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
}


def translit(s: str) -> str:
    out = []
    for ch in s or "":
        low = ch.lower()
        if low in _CYR:
            t = _CYR[low]
            out.append(t.upper() if (ch.isupper() and t) else t)
        else:
            out.append(ch)
    return "".join(out)


# ── Slug helpers ──────────────────────────────────────────────────────

def slugify(s: str, max_len: int = 55) -> str:
    s = translit(s or "")
    s = re.sub(r'[^\w\s-]', ' ', s)   # drop punctuation (parens/dots/…), not just reserved (audit A20)
    s = re.sub(r'\s+', '-', s.strip())
    s = re.sub(r'-+', '-', s).strip('-')
    s = s.encode('ascii', 'ignore').decode('ascii')  # guarantee portable stem
    s = re.sub(r'-+', '-', s).strip('-')
    return s[:max_len].lower() or "untitled"


def slug_tag(t: str) -> str:
    t = (t or "").strip().lower().replace(' ', '_')
    return re.sub(r'[^\w\-/]', '', t)


def _normalise_project_set(names) -> set:
    """Configured project names in both shapes a caller can arrive in: as written, and as a slug.

    Used by `is_local_only`, whose two sets come from the environment as the user typed them while
    its callers pass `slug_project()` output. Cheap enough to run per call - the sets hold a handful
    of names and the gate runs once per extraction, not per tool call."""
    out = set()
    for raw in names:
        n = (raw or "").strip()
        if not n:
            continue
        out.add(n.lower())
        out.add(slug_project(n))
    return out


def slug_project(name: str) -> str:
    """Project slug: alnum + underscore, lowercase, never '-'. Hardened
    against '', '.', '..' and Windows reserved device names so an LLM- or
    injection-controlled project value cannot escape Context/ (audit C3)."""
    s = slugify(name, 40)
    if s == "untitled":  # slugify's empty-input fallback → no real project name
        return "general"
    s = s.replace('-', '_').strip('._')
    if s in WIN_RESERVED:
        s = "project_" + s
    if not s or s in {'.', '..'}:
        s = "general"
    return s


def _strip_lead_icon(t: str) -> str:
    """Drop a leading type-icon the LLM sometimes echoes into a title, so the
    note heading doesn't render the icon twice (audit C5)."""
    # char class holds the icons the LLM tends to echo plus real dashes/bullets. Dashes are
    # escaped individually (\-) NOT as a range: a bare '-' between two chars in a class is a
    # set-difference/range and Python 3.14 warns on it (FutureWarning). Keep hyphen, en dash,
    # em dash, bullets.
    # str() coercion first: the LLM occasionally emits a non-string title (int/list),
    # and re.sub over it raised TypeError - crashing process_session BEFORE the
    # session was marked, so every later hook event re-extracted and re-crashed
    # (review 2026-08, same type-drift class as the tags 'may return a str' fix).
    if not isinstance(t, str):
        t = "" if t is None else str(t)
    t = re.sub('^[\\s✅⚠️\U0001f3af•·\\-–—]+', '', t or '')
    return t.strip() or "untitled"


# ── Stem format (single source of truth) ──────────────────────────────
# Typed   : YYYY-MM-DD-{project}-{ntype}-{slug}        (project has no '-')
# Session : YYYY-MM-DD-HHMM-{project}-session-{id8}

def typed_stem(date: str, project: str, ntype: str, title: str) -> str:
    return f"{date}-{project}-{ntype}-{slugify(title)}"


def _sid8(session_id: str) -> str:
    """8 stable chars of session identity - a HASH of the whole id, never a positional
    slice. Claude Code ids are UUIDs (entropy at the head) but watch/ingest ids are
    prefix-constant ('ingest-file-<hash>-...', entropy at the TAIL), so `id[:8]` collapsed
    every ingest ever to the literal constant 'ingest-f' (34 live notes shared it; review
    2026-08) - conflating same-minute transcripts and letting the per-session idempotency
    guard silently drop a same-slug lesson mined from a DIFFERENT transcript."""
    #: Imported here, not at the top. `hashlib` costs 3.0 ms of every hook process and this
    #: is its only caller in the module, on the write path - while PreToolUse, which pays
    #: that cost on every tool call the customer makes, never reaches it. Re-gated as M1b:
    #: the first gate asked for disjoint millisecond ranges, which an effect smaller than
    #: the host's own spread cannot produce at any sample size.
    import hashlib                                              # noqa: PLC0415

    return hashlib.sha1((session_id or "unknown").encode("utf-8", "replace")).hexdigest()[:8]


def session_stem(date: str, time_str: str, project: str, session_id: str) -> str:
    return f"{date}-{time_str.replace(':','')}-{project}-session-{_sid8(session_id)}"


def reserve_session_stem(date: str, time_str: str, project: str, session_id: str) -> str:
    """The FINAL session-note stem, resolved BEFORE any typed note is written. The round-1
    flow computed a base stem, stamped it into typed notes' `session` frontmatter, and only
    then let write_session_note uniquify a collision to '-2' - so same-minute transcripts
    conflated: their typed notes all claimed the first transcript's session, and the
    idempotency guard dropped same-slug lessons across DIFFERENT transcripts (review
    2026-08, manifest in-tree). Crash-retry safe: a session note already on disk carrying
    THIS exact session_id wins, so a retried session reuses its stem instead of minting -2."""
    base = session_stem(date, time_str, project, session_id)
    p = VAULT / "Sessions"
    if p.exists():
        for cand in sorted(p.glob(base + "*.md")):
            try:
                fm, _ = _read_frontmatter(cand.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if fm.get("session_id") in (session_id, session_id[:8]):   # full id; [:8] = legacy notes
                return cand.stem
        # Same DAY, different minute: transcript_text ingestion stamps time from
        # datetime.now() at processing time, so a crash-retry minutes later missed the
        # minute-keyed glob above, minted a fresh stem, and the absorb path counted one
        # real session as two distinct sources (review 2026-08). The session_id (a
        # content hash for ingested text) is the stable identity - match it day-wide.
        for cand in sorted(p.glob(f"{date}-*.md")):
            meta = parse_session_stem(cand.stem)
            if not meta or meta["project"] != project:
                continue
            try:
                fm, _ = _read_frontmatter(cand.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if fm.get("session_id") == session_id:
                return cand.stem
    return _unique_path(p, base).stem


#: A stem names a note; callers build `VAULT/<folder>/<stem>.md` from it. Anything that makes it
#: address a different file - a separator, a parent reference, a NUL - is not a stem, whoever sent
#: it. `inbox.confirm` was reachable from the CLI and from `api.inbox_action` with a slug carrying
#: `../`, and rewrote a Markdown file outside the store.
_STEM_UNSAFE = ("/", chr(92), chr(0), ":")


def _stem_is_safe(stem: str) -> bool:
    """True when every part of a stem is a plain name rather than a path fragment."""
    if not stem or any(ch in stem for ch in _STEM_UNSAFE):
        return False
    return all(part not in (".", "..") for part in stem.split("-"))


def parse_typed_stem(stem: str) -> dict | None:
    """Parse typed stem. Returns {date, project, ntype, slug} or None.

    None also for a stem that is really a path: the callers of this function turn what it returns
    into a filesystem path, so the check belongs here rather than in each of them."""
    if not _stem_is_safe(stem):
        return None
    parts = stem.split("-", 5)
    if len(parts) < 6 or parts[4] not in TYPED_TYPES:
        return None
    return {"date": "-".join(parts[:3]), "project": parts[3],
            "ntype": parts[4], "slug": parts[5]}


def parse_session_stem(stem: str) -> dict | None:
    """Parse session stem. Returns {date, time, project, id8} or None - and None for a stem that is
    really a path, for the same reason `parse_typed_stem` refuses one."""
    if not _stem_is_safe(stem):
        return None
    parts = stem.split("-", 6)
    if len(parts) < 7 or parts[5] != "session":
        return None
    return {"date": "-".join(parts[:3]), "time": parts[3],
            "project": parts[4], "id8": parts[6]}


# ── Tag / frontmatter helpers ─────────────────────────────────────────

def render_body_tags(*tag_groups) -> str:
    seen, out = set(), []
    for group in tag_groups:
        for t in (group or []):
            tag = slug_tag(t)
            if tag and tag not in seen:
                seen.add(tag)
                out.append(f"#{tag}")
    return " ".join(out)


def _norm_tags(tags) -> list:
    """Canonical, deduped tag list (audit M5): one vocabulary for frontmatter and
    body. Spaces AND hyphens collapse to '_' so 'quantum computing' and
    'quantum-computing' both become 'quantum_computing' (slashes kept for
    hierarchical tags like 'project/foo')."""
    out, seen = [], set()
    for t in (tags or []):
        if not isinstance(t, str):
            continue
        s = slug_tag(t).replace("-", "_").strip("_")
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


_ENTITY_BAD_RE = _lazy_re(r"[^\w\s-]", re.UNICODE)


def _norm_entities(raw, cap: int = 8) -> list:
    """Canonical entity tags for the knowledge graph (Phase 1): lowercase kebab-case,
    deduped, length-bounded, capped. Strips everything but word chars / spaces / hyphens,
    so junk or an injection payload smuggled through the `entities` field can only ever
    survive as a harmless short token. Unicode-aware, so Cyrillic entities are kept."""
    if not isinstance(raw, (list, tuple)):
        return []
    out, seen = [], set()
    for e in raw:
        if not isinstance(e, str):
            continue
        s = _ENTITY_BAD_RE.sub(" ", e).strip().lower()
        s = re.sub(r"[\s_]+", "-", s).strip("-")
        if not (2 <= len(s) <= 40) or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= cap:
            break
    return out


def _norm_relations(raw, cap: int = 8) -> list:
    """Canonical typed edges for the knowledge graph (Phase 2): [{rel, target}] dicts
    with rel and target normalised to lowercase kebab tokens (reusing _norm_entities, so
    an injection payload can only survive as a harmless token, 'Caused By' merges with
    'caused-by', and the target is the SAME token space as `entities` - edges connect to
    the entity graph). Drops malformed / self-edges, dedups (rel,target), caps."""
    if not isinstance(raw, (list, tuple)):
        return []
    out, seen = [], set()
    for r in raw:
        if not isinstance(r, dict):
            continue
        rel = _norm_entities([r.get("rel")])
        tgt = _norm_entities([r.get("target")])
        if not rel or not tgt:
            continue
        key = (rel[0], tgt[0])
        if key in seen:
            continue
        seen.add(key)
        out.append({"rel": rel[0], "target": tgt[0]})
        if len(out) >= cap:
            break
    return out


def _norm_entity_types(raw, cap: int = 8, gate: bool = True) -> dict:
    """Canonical {entity: type} map for the Brain layer (F1). Keys normalised to the SAME
    token space as `entities`, so a typed entity matches its graph node.

    gate=True  (extraction / WRITE): values restricted to the ACTIVE profile's ontology
               (config.entity_types()); a coding-only install therefore writes none, and
               junk / an injection payload in the type slot is dropped.
    gate=False (reading a note BACK): the stored type is kept as a clean token regardless of
               which profile is active now - recall must not depend on the current profile,
               since the type was already validated at write time."""
    if not isinstance(raw, dict):
        return {}
    allowed = set(_cfg.entity_types())
    if gate and not allowed:
        return {}
    out: dict = {}
    for name, typ in raw.items():
        if not isinstance(name, str) or not isinstance(typ, str):
            continue
        key = _norm_entities([name])
        t = re.sub(r"[^a-z0-9-]", "", typ.strip().lower())
        if not key or not (2 <= len(t) <= 24) or (gate and t not in allowed) or key[0] in out:
            continue
        out[key[0]] = t
        if len(out) >= cap:
            break
    return out


def _brain_prompt_block() -> str:
    """The extra extraction instruction that asks the model to TYPE the entities it tags
    (paper/method/dataset/...). Empty string for a coding-only install, so the prompt - and
    the model's job - is byte-for-byte unchanged unless a brain profile is on."""
    if not _cfg.brain_enabled():
        return ""
    types = ", ".join(_cfg.entity_types())
    hints = _cfg.relation_hints()
    rel_line = (' For relations edges prefer the knowledge relations: ' + ", ".join(hints) + "."
                if hints else "")
    # Inserted as a .format() VALUE (not itself re-formatted), so braces are single here.
    return (
        "\n\nKNOWLEDGE GRAPH (a second-brain profile is on): in every category, for the "
        "entities that are real OBJECTS OF KNOWLEDGE (not files/variables/code), add an "
        '"entity_types" field - a dict {"entity": "type"}. Allowed types: ' + types + ". "
        'Example: "entity_types": {"gears": "method", "imagenet": "dataset"}.' + rel_line +
        " Do NOT type code entities - skip them."
    )


def _is_relevant(flag) -> bool:
    """Interpret the LLM's project_relevant flag; default True when absent so a
    backend that omits it never silently drops knowledge (audit C1)."""
    if isinstance(flag, bool):
        return flag
    if isinstance(flag, str):
        return flag.strip().lower() not in ("false", "0", "no", "нет", "")
    return True if flag is None else bool(flag)


_NOISE_UPDATE_RE = _lazy_re(
    r"не\s+содержит\s+полезн|только\s+метаданны|(?<![\w-])тривиальн|нет\s+полезн|"
    r"не\s+предоставил|пуст(ой|ая)\s+(диалог|сесси)|только\s+(что\s+)?стартова|"
    r"no\s+useful|nothing\s+to\s+(extract|report)|(?<![\w-])trivial|session\s+just\s+started|"
    r"empty\s+(session|transcript|chat)", re.IGNORECASE)

# Above this length an update that merely CONTAINS a noise phrase is real content:
# unanchored substrings ('non-trivial CUDA OOM', 'API не предоставляет batch
# endpoint') were silently discarding legitimate engineering prose (review 2026-08).
_NOISE_MAX_CHARS = 160


def _is_noise_update(text: str) -> bool:
    """A 'nothing happened' context_update the LLM sometimes emits despite being
    told to leave it empty - keep it out of the living context (audit M4). Only a
    SHORT update is classified by phrase match; a long one is content by definition."""
    text = text or ""
    return len(text) <= _NOISE_MAX_CHARS and bool(_NOISE_UPDATE_RE.search(text))


# Prompt-injection signatures. Tightened (audit C1): every alternative requires
# an injection-specific OBJECT (instructions/rules/system-prompt/a jailbreak
# persona), never a bare imperative verb. The round-1 guard matched ordinary
# engineering prose - "disregard the warning about the deprecated flag", "act as
# a thin wrapper", "you are now able to batch" - and silently dropped legitimate
# knowledge, which is worse than no guard. These patterns fire only on a genuine
# override attempt while leaving normal lessons untouched.
_INJECTION_RE = _lazy_re(
    # "ignore/disregard … (previous/all/your/the/above) instructions|prompts|rules|context"
    r"\b(?:ignore|disregard|forget|bypass|override)\s+"
    # up to four single-word modifiers between verb and object. Bounded {0,4} of
    # \w-word + single \s beats the old starred alternation of words each ending in
    # \s+ (a py/redos backtracking shape on adversarial text) and also catches
    # modifiers we never thought to list.
    r"(?:[A-Za-z]{1,20}\s){0,4}"
    r"(?:instructions?|prompts?|rules?|directives?|guidelines?|guardrails?|context|"
    r"everything\s+(?:above|before)|all\s+of\s+the\s+above)\b|"
    # reveal/leak/print the system prompt / initial instructions
    r"\b(?:reveal|leak|expose|exfiltrate|print|show|repeat|reproduce|divulge)\s+"
    r"(?:[A-Za-z]{1,20}\s){0,4}"
    r"(?:system\s+prompt|system\s+instructions?|initial\s+instructions?|"
    r"the\s+prompt\s+above|prompt\s+verbatim)\b|"
    # role-override / jailbreak personas
    r"\byou\s+are\s+now\s+(?:a\s+|an\s+|in\s+)?(?:dan\b|jailbroken|jailbreak|unrestricted|"
    r"unfiltered|uncensored|developer\s+mode|free\s+(?:from|of)\b|no\s+longer\s+bound|"
    r"allowed\s+to\s+ignore)|"
    r"\bact\s+as\s+(?:an?\s+|the\s+)?(?:dan\b|jailbroken|jailbreak|unrestricted|unfiltered|"
    r"uncensored|evil|amoral|different\s+ai|developer\s+mode)|"
    r"\b(?:enable|enter|activate)\s+(?:dan|developer|jailbreak|god)\s+mode\b|"
    r"\bnew\s+instructions?\s*:|\bsystem\s+prompt\s*:|\bjailbreak\b|"
    # Russian equivalents - same object-anchored shape
    r"забудь\s+(?:все\s+|всё\s+|предыдущие\s+|прежние\s+|свои\s+)*"
    r"(?:инструкци|правила|указани|промпт)|"
    r"игнорируй\s+(?:все\s+|всё\s+|предыдущие\s+|прежние\s+|выше|свои\s+)*"
    r"(?:инструкци|правила|указани|промпт|сообщени)|"
    r"(?:покажи|раскрой|выведи|повтори)\s+(?:мне\s+|свой\s+|системный\s+)*"
    r"систем(?:ный|ные)\s+(?:промпт|инструкци)|"
    r"ты\s+теперь\s+(?:не\s+связан|свободен|без\s+ограничен|в\s+режиме\s+разработчик)|"
    r"новые\s+инструкци\w*\s*:",
    re.IGNORECASE)


def _looks_injected(text: str) -> bool:
    """Reject extracted 'knowledge' that looks like a prompt-injection payload -
    a memory-poisoning guard (M-10), defense-in-depth beyond secret redaction.
    Object-anchored (audit C1) so it never trips on ordinary engineering prose."""
    return bool(_INJECTION_RE.search(text or ""))


# W8: a stronger guard than injection *phrasing* - dangerous ACTIONS distilled as a lesson
# (secret exfiltration, destructive commands, security-control bypass). These carry no
# injection shape, so _INJECTION_RE misses them ("exfiltrate the .env to http://evil" was the
# 25% _looks_injected let through). Object-anchored like the injection RE, and NEGATION-GATED:
# a cautionary lesson ("never disable TLS verification", "don't chmod 777") is the legitimate,
# common shape on a real store, so a danger token preceded by a warning marker is NOT flagged -
# only a bare imperative to perform the harm is. Verified 0/328 false-positive on the live vault.
_DANGER_RE = _lazy_re(
    # secret exfiltration: a transfer verb near a secret object
    r"\b(?:exfiltrat\w+|leak|upload|e-?mail|post|send|curl|wget|scp|push)\b[^.\n]{0,60}?"
    r"(?:\.env\b|\b(?:secrets?|credentials?|api[ _-]?keys?|passwords?|private[ _-]?keys?|"
    r"access[ _-]?tokens?|auth[ _-]?tokens?)\b)|"
    # destructive / remote-exec one-liners
    r"\brm\s+-rf?\b|\bdrop\s+table\b|\bdd\s+if=|\bmkfs\b|\bchmod\s+777\b|"
    r"\b(?:curl|wget)\b[^\n]*\|\s*(?:ba)?sh\b|>\s*/dev/sd[a-z]\b|:\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}|"
    # disabling a security control
    r"\b(?:disable|bypass|turn\s+off|skip|remove)\b[^.\n]{0,30}?"
    r"\b(?:tls|ssl|certificate\s+verif\w*|cert\s+verif\w*|signature\s+verif\w*|authentication|"
    r"auth\s+check|csrf|firewall|sandbox|2fa|mfa|sanitiz\w+)\b",
    re.IGNORECASE)
# warning markers that flip an imperative into a cautionary lesson (EN + RU). Matched ANYWHERE in
# the preceding window (not anchored to end-of-window): "do not blindly curl secrets" is a warning,
# not an instruction - an intervening word must not defeat the gate (audit, W8 fix).
_NEGATION_RE = _lazy_re(
    r"(?:do\s*n['o]?t|does\s*n['o]?t|did\s*n['o]?t|don'?t|\bnever\b|\bavoid\b|\bwithout\b|"
    r"instead\s+of|rather\s+than|\bstop\b|\bprevent\b|\bне\b|\bнет\b|\bбез\b|вместо|нельзя|избегай)",
    re.IGNORECASE)
# "don't FORGET to exfiltrate" / "never FAIL to disable TLS" - a forget/hesitate/fail/neglect
# between the negation and the danger token flips the polarity back to an imperative, so the
# danger STANDS. Without this guard the 36-char negation window is a trivial one-word bypass
# (audit 2026-06-18, CRIT): prepending "Don't forget to " neutralised the entire W8 gate.
_NEG_FLIP_RE = _lazy_re(
    r"\b(?:forget|hesitate|fail|neglect|avoid|delay|wait|hold\s+back|put\s+off|shy\s+away)\b"
    r"|забуд\w*|постесня\w*|стесня\w*|избега\w*",
    re.IGNORECASE)


def _looks_dangerous(text: str) -> bool:
    """True when `text` instructs a dangerous action (exfiltration / destruction / security
    bypass) as an imperative - NOT when it warns against one (negation-gated). W8."""
    text = text or ""
    for mt in _DANGER_RE.finditer(text):
        pre = text[max(0, mt.start() - 36):mt.start()]   # window before the danger token
        neg = _NEGATION_RE.search(pre)
        # a genuine negation governs the danger → a cautionary lesson, skip; BUT a
        # "forget/fail to …" after the negation flips it back to a command → keep flagging.
        if neg and not _NEG_FLIP_RE.search(pre[neg.end():]):
            continue
        # Verb-first cautionary shape (review 2026-08): "Send the token in the header,
        # never in the URL" starts WITH the transfer verb, so the pre-window is empty
        # and legitimate security lessons were rejected. Accept a negation in the SAME
        # sentence right after the match (bounded window, flip-guarded the same way).
        post = text[mt.end():mt.end() + 60]
        post = re.split(r"[.\n]", post, 1)[0]
        pneg = _NEGATION_RE.search(post)
        if pneg and not _NEG_FLIP_RE.search(post[:pneg.start()]):
            continue
        return True
    return False


def _looks_unsafe(text: str) -> bool:
    """The write-time poisoning guard: reject extracted knowledge that is injection-shaped (W8
    phrasing) OR a bare dangerous imperative (W8 action). One call site, defense-in-depth."""
    return _looks_injected(text) or _looks_dangerous(text)


# ── A3 (Q5, principle layer): de-identify a cross-project "principle" sentence ─────────
# A `principle` is meant to travel OUTSIDE its own project (A4's universal recall pool), so it
# must carry no clue about where it came from. Grouped exactly as the plan's classes: an ablated
# group is what `tests/_test_principle_scan.py` mutates to prove each one is load-bearing BY
# NAME. Every pattern here is a BOUNDED, single-pass shape - no nested unbounded quantifier over
# the same character class - because this repository was bitten once by a regex that
# backtracked exponentially on a hostile input (`_DANGER_RE`'s history above), and a
# de-identification gate that stalls a session-end write is worse than the identifier it was
# built to catch. `_lazy_re`: this scan runs once per pattern/mistake item at write time, never
# on PreToolUse, so compiling on first use (not at import) costs nothing that matters.
_PRINCIPLE_IP_RE = _lazy_re(
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b"                                    # IPv4
    r"|\b(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}\b"                 # IPv6, full/compressed form
    r"|\b(?:[0-9A-Fa-f]{1,4}:){1,7}:(?:[0-9A-Fa-f]{1,4})?\b")          # IPv6, trailing '::' shorthand
_PRINCIPLE_URL_RE = _lazy_re(
    r"\b[a-zA-Z][a-zA-Z0-9+.-]{1,15}://[^\s<>\"']+"                    # scheme://... (URL)
    r"|\b(?:[a-zA-Z0-9][a-zA-Z0-9-]{0,61}\.){1,}[a-zA-Z]{2,24}\b")     # bare FQDN (word.word.tld)
_PRINCIPLE_PATH_RE = _lazy_re(
    r"\b[A-Za-z]:[\\/][^\s\"'<>]{1,200}"                               # C:\... / C:/...
    r"|\\\\[^\s\\\"'<>]+\\[^\s\"'<>]{1,200}"                           # \\host\share UNC path
    r"|(?<![\w./])(?:/[\w.-]+){2,}"                                    # /abs/posix/path
    r"|(?<![\w./])~(?:/[\w.-]+)+"                                      # ~/posix/path
    r"|\b[\w-]+(?:/[\w-]+)+\.[A-Za-z0-9]{1,8}\b")                      # rel/path/with-a.ext
_PRINCIPLE_EMAIL_RE = _lazy_re(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\b")    # user@host or user@a.b.tld -
                                                                        # the dotted TLD is OPTIONAL
                                                                        # (an intranet "user@host"
                                                                        # address is still an
                                                                        # identifier worth catching)
_PRINCIPLE_PORT_RE = _lazy_re(
    r"\b[a-zA-Z][\w.-]*:\d{2,5}\b"                                     # host:port (host starts with a
                                                                        # letter - a bare "12:30" is a
                                                                        # clock, not an endpoint)
    r"|\bport\s*[:=]?\s*\d{2,5}\b", re.IGNORECASE)                     # "port 8080" / "port: 8080"
# {1,2}, not {1,3}: a plain IPv4 address is ALWAYS four dotted octets (3 repeats) and is
# already caught by `_PRINCIPLE_IP_RE` - letting the version pattern also swallow that exact
# shape would double-classify every IPv4 literal as "a version string", which breaks the
# per-class mutation test's isolation (ablating either pattern alone would leave the other
# still rejecting an IPv4 address). Capped at three numbers total, which is every example the
# plan gives (3.12.1, v2.0) and stays a version, not a four-octet address.
#: `(?!\.\d)` after the boundary and `(?<!\d\.)` before it: without both, `{1,2}` still matches
#: a THREE-octet slice of a genuine four-octet IPv4 address, and `re.search` tries every start
#: position - blocking only the forward continuation ("10.0.0" out of "10.0.0.5") still leaves
#: "0.0.5", starting one octet in, matching on its own. The trailing lookahead blocks a match
#: that continues into another ".digit"; the leading lookbehind blocks a match that STARTS
#: right after one - together no three-number slice of a four-number run can match at all.
_PRINCIPLE_VERSION_RE = _lazy_re(r"(?<!\d\.)\bv?\d+(?:\.\d+){1,2}\b(?!\.\d)")   # 3.12.1, v2.0

#: Named groups, walked in order by `principle_scan` and by its own mutation test - ablating one
#: entry (a test-only monkeypatch of this dict, never a file edit) must fail exactly the check
#: for that class and no other.
PRINCIPLE_IDENTIFIER_GROUPS: dict = {
    "ip": (_PRINCIPLE_IP_RE,),
    "url_fqdn": (_PRINCIPLE_URL_RE,),
    "path": (_PRINCIPLE_PATH_RE,),
    "email": (_PRINCIPLE_EMAIL_RE,),
    "host_port": (_PRINCIPLE_PORT_RE,),
    "version": (_PRINCIPLE_VERSION_RE,),
}


def principle_scan(text: str, forbidden: set[str]) -> str:
    """De-identification gate for the `principle` field: "" when `text` carries a source
    identifier, otherwise the cleaned (whitespace-collapsed) text. The write path
    (`_engine_write.py`) calls this on the item's own `principle`, forbidding the project slug
    and this note's own entities; the promoter (`principles.py`, A5) calls it again at
    promotion time forbidding the project's whole vocabulary - a fact that only shows up once a
    principle is compared against a project's full note corpus is still caught before it ever
    reaches another project's recall.

    Errs toward REJECTING: a false positive here costs a "" principle, which the write path
    already treats as simply absent - the rest of the note is untouched either way (the
    degradation contract, A3). A false negative would leak a project's identity into another
    project's memory through A4's universal pool, which the read path has no way to catch
    afterwards. So a borderline match (a timestamp that parses as IPv6-shaped, a filename
    extension that parses as an FQDN) is deliberately left rejected rather than tuned away.
    """
    s = (text or "").strip()
    if not s:
        return ""
    for group in PRINCIPLE_IDENTIFIER_GROUPS.values():
        for pat in group:
            if pat.search(s):
                return ""
    toks = sorted({t.strip() for t in (forbidden or ()) if t and t.strip()},
                  key=len, reverse=True)
    if toks:
        alt = "|".join(re.escape(t) for t in toks)
        if re.search(rf"\b(?:{alt})\b", s, re.IGNORECASE):
            return ""
    return re.sub(r"\s+", " ", s)


# W7 corroboration-gated quarantine - OFF by default. On a single-user store the user owns every
# session, so the threat it defends (adversarial sessions planting a lone false "lesson") does not
# apply and quarantine would only risk hiding legitimate memory. For a MULTI-TENANT / shared-store /
# untrusted-content deployment set NEVERTWICE_QUARANTINE=1: a single-source note that is ALSO
# suspicious (near-max self-declared confidence, or superseding a corroborated multi-session note)
# is diverted to <folder>/Quarantine/ - on disk, out of active recall - so one uncorroborated actor
# cannot spoof trust or displace corroborated truth. Two genuine sessions still establish a lesson.
QUARANTINE_MODE = os.environ.get("NEVERTWICE_QUARANTINE", "0") != "0"
QUARANTINE_CONF = env_float("NEVERTWICE_QUARANTINE_CONF", 0.95)
# Write-time near-duplicate reconcile (review 2026-08): the LLM routinely re-states the
# same lesson under a different title (slugs differing by one word), which the exact-slug
# reconcile in write_typed_note cannot see - measured on a live store: THREE live patterns
# for one lesson, twin mistakes carrying CONTRADICTORY resolutions, and the recurrence
# signal starved because each re-encounter minted a fresh note at recurrence 1. A new note
# whose embedding is at least this cosine-similar to an existing live sibling (same
# project + type) is treated exactly like a same-slug re-statement: the old note retires
# to Superseded/ and its recurrence/sources carry forward. 0 disables the gate;
# embedder down = gate skipped.
# 0.80 is CALIBRATED on a live 4.2k-vector store (2026-08-18): random DISTINCT
# same-project/type pairs top out at cosine 0.737 (p99.9 = 0.735, n=1326), while
# exact-slug twin pairs have median 0.833 and the hidden-twin tail sits at 0.84-0.90.
# 0.80 clears the distinct maximum by +0.06 and catches the twin majority. The first
# shipped default copied the consolidator's 0.92 - measured NEAR-INERT: real bge-m3
# re-phrasings of one lesson score ~0.78-0.83, so 0.92 caught only near-verbatim copies
# (the reviewer predicted exactly this; a 164-note flood confirmed it empirically).
WRITE_DEDUP_SIM = env_float("NEVERTWICE_WRITE_DEDUP_SIM", 0.80)
WRITE_DEDUP_MAX_RETIRE = 3   # safety valve: never retire more than this many per new note
# Gate mode: "twin" scores candidates with the learned twin-classifier below; "cosine"
# restores the plain threshold gate (the kill-switch). In twin mode candidates first pass
# a cheap cosine PREFILTER, then the full feature score decides.
WRITE_DEDUP_MODE = os.environ.get("NEVERTWICE_WRITE_DEDUP_MODE", "twin").strip().lower()
WRITE_DEDUP_TWIN_P = env_float("NEVERTWICE_WRITE_DEDUP_TWIN_P", 0.90)
WRITE_DEDUP_PREFILTER = env_float("NEVERTWICE_WRITE_DEDUP_PREFILTER", 0.70)

# ── Learned twin-gate (stage 0 of embedding specialization, 2026-08-18) ──────────────
# Logistic regression over 5 write-time-computable pair features, trained on pairs mined
# from the memory's OWN LIFECYCLE: 207 positives (99 explicit supersede pairs + 108
# same-slug '-N' twins) vs 800 random distinct same-project/type pairs, embeddings by the
# production bge-m3. Held-out test (302 pairs): AUC 0.998 vs 0.991 for cosine alone, and
# at the default 0.90 operating point precision 1.000 / twin-recall 0.852 - versus the
# calibrated cosine@0.80 gate's 0.977 / 0.689. The interesting learned fact: word-overlap
# carries a NEGATIVE weight given cosine - at matched cosine, true twins are re-PHRASINGS
# (same meaning, different words) while high word overlap signals template-similar but
# distinct notes. Weights are baked so the production gate stays stdlib-only; retraining
# lives in the research script (see research/TWIN_GATE.md).
_TWIN_WORD_RE = _lazy_re(r"[a-zа-я0-9]{3,}")


_TWIN_BAKED_SPACE = "bge-m3"
_TWIN_SD_MIN = 1e-6      # sd is a divisor: a near-zero one saturates the sigmoid
_TWIN_ABS_MAX = 1e3      # sane magnitude for weights/means of five [0,1]-ish features
#: The logit clamp `_twin_probability` applies. Named, because the bounds check below is
#: written against it: a calibration whose logit cannot fit inside the clamp is one where the
#: clamp, not the weights, decides the answer at the edges of the feature box.
_TWIN_LOGIT_CLAMP = 60.0


def _twin_logit_range(w, b, mu, sd) -> tuple[float, float]:
    """The logit's range over the feature box [0,1]^5 - exact, because the logit is monotone
    in each feature, so both extremes sit at corners. All five features are ratios in [0,1]
    by construction (`_twin_probability`), and the cosine is additionally floored by the
    prefilter."""
    lo = hi = float(b)
    for wi, mi, si in zip(w, mu, sd):
        a, z = wi * (0.0 - mi) / si, wi * (1.0 - mi) / si
        lo += min(a, z)
        hi += max(a, z)
    return lo, hi


def _twin_calibration_usable(w, b, mu, sd) -> bool:
    """Is this calibration a probability over the feature box, or a switch wearing one?

    The per-parameter bounds were each satisfiable while their combination produced exactly
    the failure their own docstring described. `sd >= 1e-6` and `|w| <= 1e3` admit sd=1e-6
    with w=1e3: the standardized feature reaches 1e6, the logit spans +-2e9, the sigmoid is
    pinned at 1.0 for every candidate that clears the cosine prefilter, and up to
    WRITE_DEDUP_MAX_RETIRE live notes are retired per write. `|b| <= 1e3` reaches the same
    place with no weights at all. The bounds have to be joint, because the harm is.

    Two rules, both about what the gate can SAY rather than what its numbers look like:

    * It fits the clamp. The shipped bge-m3 calibration spans 48.7 (-31.9 to +16.9) of the
      120 the implementation allows - 41%, so a retrained gate has real room - while the
      degenerate above spans 5e9 and is a step function the clamp is rendering.
    * It can refuse. A gate whose minimum probability over the whole feature box is already
      >= 0.5 has no input it would call "not a twin"; it is a note-retiring gate that always
      says yes, which is the one outcome this whole check exists to prevent.
    """
    lo, hi = _twin_logit_range(w, b, mu, sd)
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return False
    return (hi - lo) <= 2 * _TWIN_LOGIT_CLAMP and lo < 0.0


def _twin_file_path() -> Path:
    """Where the machine-local calibration lives: $NEVERTWICE_TWIN_FILE, or next to this
    module. Read at call time, not baked, so a probe and the loader agree about the file."""
    return Path(os.environ.get("NEVERTWICE_TWIN_FILE", "").strip()
                or Path(__file__).resolve().parent / "twin_calibration.json")


def _twin_field_fault(d) -> str:
    """Why this JSON document is not a calibration - by KEY and TYPE, never by value.

    `float("<the value>")` puts the value INTO the ValueError, and the reason string ends up
    in `nevertwice-doctor` output: reporting the exception text publishes the very file this
    module refuses to echo. The bounds branch below quotes only its own constants, so it was
    leak-free by accident; this path was the one where a malformed file spoke for itself.
    A type name cannot carry a value, so the fault is described with one.

    A numeric STRING still converts, as it always did: this is a fix to what is said about a
    refusal, not a tightening of what is accepted. The one exception is `space`, which IS
    echoed by design as the label the weights are keyed to - so it has to be a string, or
    a dict parked there would be printed as the gate's embedding space.
    """
    if not isinstance(d, dict):
        return f"top level is a {type(d).__name__}, not an object"
    for k in ("w", "mu", "sd", "b"):
        seq = d.get(k) if k != "b" else [d.get("b")]
        if k != "b" and not isinstance(seq, (list, tuple)):
            return f"{k} is a {type(seq).__name__}, not a list of numbers"
        for x in seq:
            if not isinstance(x, (int, float, str)):
                return f"{k} holds a {type(x).__name__} where a number belongs"
            try:
                float(x)
            except ValueError:
                return f"{k} holds a str that is not a number"
    sp = d.get("space")
    if sp is not None and not isinstance(sp, str):
        return f"space is a {type(sp).__name__}, not a string"
    return ""


def _twin_file_verdict(fp: Path) -> tuple:
    """(present, (space, w, b, mu, sd) or None, reason) for the calibration file.

    The one place the file is validated, so the loader and `twin_calibration_status` cannot
    grow two opinions about the same file. `reason` is empty on acceptance and NEVER carries
    a value read from the file: the calibration is machine-local data, and a diagnostic that
    echoes it publishes it. See `_twin_field_fault` for how that promise is kept on the path
    where the file's own contents would otherwise do the explaining.
    """
    try:
        if not fp.is_file():
            return False, None, f"no calibration file at {fp}"
        d = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return True, None, f"unreadable ({fp}): {type(e).__name__}"
    fault = _twin_field_fault(d)
    if fault:
        return True, None, f"malformed ({fp}): {fault}"
    # Total after the fault gate: every value here is an int, a float, or a numeric string.
    cw, cmu, csd = (tuple(float(x) for x in d[k]) for k in ("w", "mu", "sd"))
    cb = float(d["b"])
    cspace = (d.get("space") or "").strip() or _TWIN_BAKED_SPACE
    if (len(cw) == len(cmu) == len(csd) == 5
            and all(math.isfinite(v) for v in (*cw, *cmu, *csd, cb))
            and all(v >= _TWIN_SD_MIN for v in csd)
            and all(abs(v) <= _TWIN_ABS_MAX for v in (*cw, *cmu, cb))
            and _twin_calibration_usable(cw, cb, cmu, csd)):
        return True, (cspace, cw, cb, cmu, csd), ""
    return True, None, (
        f"out of bounds ({fp}) - need 5 finite w/mu/sd, sd >= {_TWIN_SD_MIN}, "
        f"|w|,|mu|,|b| <= {_TWIN_ABS_MAX:g}, and a logit that spans no more than "
        f"{2 * _TWIN_LOGIT_CLAMP:g} over [0,1]^5 with some input it would call not-a-twin")


def twin_calibration_status() -> dict:
    """Which twin-gate weights a fresh load would use, and why not the file's when not.

    The fallback to the baked weights is announced once, at import, into `_EARLY_WARNINGS`
    and from there into the hook's log - so an operator updating an install learns that a
    note-RETIRING gate quietly changed by reading log tails, if at all. `doctor` prints this
    instead. The verdict only: no weight, mean or deviation ever appears in the result.
    """
    fp = _twin_file_path()
    present, vals, why = _twin_file_verdict(fp)
    return {"path": str(fp), "present": present, "accepted": vals is not None,
            "source": "file" if vals is not None else "baked",
            "space": vals[0] if vals is not None else _TWIN_BAKED_SPACE,
            "reason": why or "accepted"}


def _load_twin_calibration() -> tuple:
    """(space, w, b, mu, sd) for the twin gate: the baked bge-m3 calibration unless a
    machine-local `twin_calibration.json` overrides it ($NEVERTWICE_TWIN_FILE, or next
    to this module). A retrained gate (research/TWIN_GATE.md) is thereby DATA, not a
    code fork of this file - the live-install pin every dev→live sync had to re-apply
    by hand until the 2026-08 cleanup.

    `space` is the embedding space the weights were calibrated on: cosines from a
    different embedder pushed through this standardization are off-distribution for a
    note-RETIRING gate, so on a mismatch _twin_space_ok falls back to the plain cosine
    threshold, loudly, once.

    Two rules keep that safety property honest (review 2026-08-24):
      * File values are BOUNDS-CHECKED, not merely finite. `sd=1e-12` with a large
        weight clamps _twin_probability at p=1.0 for every candidate that clears the
        cosine prefilter - i.e. up to WRITE_DEDUP_MAX_RETIRE live notes retired per
        write, silently; a negative sd inverts the cosine feature outright.
      * The returned label always describes the weights ACTUALLY loaded, so
        NEVERTWICE_TWIN_SPACE can no longer re-enable the gate for weights from a
        different space (the file's own `space` wins; a contradicting override is
        refused loudly). That combination - stale env label plus a calibration file
        that was deleted or belongs to another embedder - is the one way this gate
        silently retires correct notes."""
    space, from_file = _TWIN_BAKED_SPACE, False
    w = (3.684473, -1.879536, 2.19829, 0.21404, -0.122378)
    b = -3.057724
    mu = (0.621251, 0.196803, 0.183766, 0.095122, 0.88883)
    sd = (0.133751, 0.145667, 0.364025, 0.250373, 0.086981)
    present, vals, why = _twin_file_verdict(_twin_file_path())
    if vals is not None:
        space, w, b, mu, sd, from_file = (*vals, True)
    elif present:
        # _EARLY_WARNINGS, not log(): this runs at IMPORT, and log() mkdirs the store -
        # a read-only consumer (install.py --print) must not materialize a vault as a
        # side effect of a half-written calibration file (review 2026-08-24). One line in a
        # log is also the whole reason `twin_calibration_status` exists: see it there.
        _EARLY_WARNINGS.append(f"twin_calibration.json {why} - using baked "
                               f"{_TWIN_BAKED_SPACE} weights")
    env_space = os.environ.get("NEVERTWICE_TWIN_SPACE", "").strip()
    if env_space and env_space != space:
        _EARLY_WARNINGS.append(
            f"NEVERTWICE_TWIN_SPACE={env_space!r} contradicts the "
            f"{'calibration file' if from_file else 'baked'} space {space!r} - "
            f"ignoring the override so the gate stays keyed to the weights it actually "
            f"has. Ship a twin_calibration.json calibrated for {env_space!r} to enable "
            f"the learned gate there (research/TWIN_GATE.md).")
    return space, w, b, mu, sd


_TWIN_SPACE, _TWIN_W, _TWIN_B, _TWIN_MU, _TWIN_SD = _load_twin_calibration()
_TWIN_SPACE_WARNED = [False]


def _twin_space_ok() -> bool:
    # Compare through the same normalization the vector cache uses: accept the bare
    # model name OR the full provider:model signature, and treat an Ollama ':latest'
    # tag as the untagged name - the bare string equality either kept the gate on in
    # a foreign space (same model name, different provider) or silently downgraded it
    # on a mere tag variant (review 2026-08).
    def _norm(s: str) -> str:
        s = (s or "").strip()
        return s[:-len(":latest")] if s.endswith(":latest") else s
    if _norm(_TWIN_SPACE) in (_norm(EMBED_MODEL), _norm(embed_signature())):
        return True
    if not _TWIN_SPACE_WARNED[0]:
        _TWIN_SPACE_WARNED[0] = True
        log(f"twin-gate weights calibrated for '{_TWIN_SPACE}' but the embed model is "
            f"'{EMBED_MODEL}' - using the cosine gate instead (retrain + set "
            f"NEVERTWICE_TWIN_SPACE to re-enable; research/TWIN_GATE.md)")
    return False


def _twin_words(s: str) -> set:
    return set(_TWIN_WORD_RE.findall((s or "").lower()))


def _twin_probability(cos_sim: float, title_a: str, desc_a: str, ents_a,
                      title_b: str, desc_b: str, ents_b) -> float:
    """P(same lesson) for a candidate pair - the five features mirror the training script
    exactly: cosine, word-jaccard of title+desc, title-token jaccard, entity overlap
    (|A∩B|/min, 0 when either side is untagged), and length ratio."""
    wa, wb = _twin_words(f"{title_a} {desc_a}"), _twin_words(f"{title_b} {desc_b}")
    ta, tb = _twin_words(title_a), _twin_words(title_b)
    ea, eb = set(ents_a or ()), set(ents_b or ())
    x = (cos_sim,
         len(wa & wb) / len(wa | wb) if (wa or wb) else 0.0,
         len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0,
         len(ea & eb) / min(len(ea), len(eb)) if ea and eb else 0.0,
         (lambda ca, cb: min(ca, cb) / max(ca, cb))(
             max(1, len(title_a) + len(desc_a)), max(1, len(title_b) + len(desc_b))))
    zsum = _TWIN_B + sum(w * (v - mu) / sd for w, v, mu, sd
                         in zip(_TWIN_W, x, _TWIN_MU, _TWIN_SD))
    return 1.0 / (1.0 + math.exp(-max(-_TWIN_LOGIT_CLAMP, min(_TWIN_LOGIT_CLAMP, zsum))))


_YAML_NEEDS_QUOTE = _lazy_re(r'[:#&*!|>\'"%@`{}\[\],]|^\s|\s$')


def _yaml_scalar(v) -> str:
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)   # inline JSON is valid YAML (lists + maps, e.g. entity_types)
    s = str(v)
    if "\n" in s or "\r" in s:
        # Every frontmatter field is single-line by design. An embedded newline in an
        # LLM-controlled value (e.g. valid_from) would otherwise be emitted raw -
        # closing the frontmatter fence early / injecting lines (review 2026-08).
        s = " ".join(s.replace("\r", "\n").split()).strip()
    if s == "" or _YAML_NEEDS_QUOTE.search(s):
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def fm_block(fm: dict) -> str:
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {_yaml_scalar(v)}")
    lines.append("---")
    return "\n".join(lines)


# ── Path filter / project derivation ──────────────────────────────────
# (_norm_path / _WIN / _CASEFOLD are defined near the config block above.)

_VAULT_NORM = _norm_path(str(VAULT))
_PROJECTS_ROOT_NORM = _norm_path(str(PROJECTS_ROOT))
try:
    _HOME_NORM = _norm_path(str(Path.home()))
except Exception:
    _HOME_NORM = ""

# Prefixes whose subtrees are never a "project": OS / installed software, the
# store itself, and the agent's transcript dir. The agent-internal ~/.claude tree
# and transient dirs are matched separately. OS-aware so machine-wide tracking
# stays clean on Windows, Linux and macOS (audit C2 / P-2).
if _WIN:
    _SYS_DIRS = (os.environ.get("SystemRoot") or r"C:\Windows",
                 os.environ.get("ProgramFiles") or r"C:\Program Files",
                 os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)",
                 os.environ.get("ProgramData") or r"C:\ProgramData")
else:
    _SYS_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt",
                 "/proc", "/sys", "/dev", "/run", "/boot",
                 "/System", "/Library", "/Applications", "/private")  # last 4: macOS
_EXCLUDE_PREFIXES = [_norm_path(p) for p in
                     (*_SYS_DIRS, _VAULT_NORM, _PROJECTS_ROOT_NORM) if p]

# Transient/agent-internal path fragments (matched anywhere in the path), sep-aware.
_SEP = os.sep
_EXCLUDE_FRAGMENTS = [f"{_SEP}.claude{_SEP}", f"{_SEP}.trash{_SEP}"]
if _WIN:
    _EXCLUDE_FRAGMENTS += [r"\appdata\local\temp\\".rstrip("\\") + "\\",
                           r"\$recycle.bin\\".rstrip("\\") + "\\"]
else:
    _EXCLUDE_FRAGMENTS += ["/tmp/", "/var/folders/", "/var/tmp/"]  # incl. macOS tmp


def _is_excluded_path(norm: str) -> bool:
    """norm = output of _norm_path (OS-normalised, no trailing sep)."""
    if not norm:
        return True
    if _HOME_NORM and norm == _HOME_NORM:        # bare home root (not its subdirs)
        return True
    tail = norm + _SEP
    if any(frag in tail for frag in _EXCLUDE_FRAGMENTS):
        return True
    for pre in _EXCLUDE_PREFIXES:
        if norm == pre or norm.startswith(pre + _SEP):
            return True
    return False


def _find_repo_root(cwd: str) -> Path | None:
    """Nearest ancestor (incl. cwd) containing a .git entry, else None. Pure
    filesystem walk - no subprocess, safe to call in the hot path and in tests."""
    raw = (cwd or "").strip()
    if not raw:
        return None
    try:
        p = Path(raw)
        cur = p if p.is_dir() else p.parent
    except OSError:
        return None
    for _ in range(40):
        try:
            if (cur / ".git").exists():
                return cur
        except OSError:
            pass
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def is_tracked_project(cwd: str) -> bool:
    """True if cwd belongs to a real project worth remembering.

    Strictly under a configured root, OR (TRACK_ANY_PROJECT) inside a git repo -
    excluding system / installed-software / agent-internal (~/.claude) / the
    vault / transient (Temp, Recycle) paths. A configured root *container* itself
    is never a project - you must be in a subdirectory of it (audit C2)."""
    norm = _norm_path(cwd)
    if not norm or _is_excluded_path(norm):
        return False
    for r in _ROOTS_NORM:
        if norm == r:
            return False
        if norm.startswith(r + _SEP):
            return True
    if TRACK_ANY_PROJECT and _find_repo_root(cwd) is not None:
        return True
    return False


def derive_project_from_cwd(cwd: str) -> str:
    """Project name for `cwd`.

    Under a configured root → first segment beneath that root. Otherwise → the
    git-repo directory name (so a repo is one project regardless of which subdir
    the agent ran in), falling back to the leaf directory name.

        D:\\Code\\MyProject\\src\\foo           → myproject
        D:\\repos\\acme (git root) \\pkg\\api   → acme
        D:\\Other\\proj  (no repo)              → proj
    """
    s = (cwd or "").strip()
    raw = os.path.normpath(s) if s else ""
    if _WIN:
        raw = raw.replace("/", "\\")
    raw = raw.rstrip("\\/")
    low = raw.lower() if _CASEFOLD else raw    # case-preserving raw + folded low
    for r in _ROOTS_NORM:
        if low.startswith(r + _SEP):
            rest = raw[len(r) + 1:]            # len matches: casing never changes length
            return slug_project(rest.split(_SEP, 1)[0])
    rr = _find_repo_root(raw)
    first_seg = rr.name if rr else Path(raw).name
    return slug_project(first_seg)


# ── Vault introspection (for prompt grounding) ────────────────────────

# Vault-introspection grounding for the extraction prompt: existing tags +
# per-project note titles. Cached per-process and FOLDED FORWARD with each
# session's writes (audit M-k) - the round-1 code cleared the whole cache after
# every session in a sweep, re-scanning all notes O(N×sessions). The vault is
# locked during processing, so the snapshot can't drift mid-run.
_TAG_COUNTS: dict[str, int] | None = None
# Per-project tag counts, so an extraction is grounded on ITS OWN project's vocabulary
# rather than on whichever project happens to hold the most notes in the vault.
_TAG_COUNTS_BY_PROJECT: dict[str, dict[str, int]] = {}
# Below this, a project's own vocabulary is too thin to ground on and is padded from the
# global one - a new project must not be left with no grounding, which invites a fresh
# invented tag per note.
MIN_PROJECT_TAG_VOCAB = env_int("NEVERTWICE_MIN_PROJECT_TAGS", 5)
# project -> ntype -> [(date, slug)]. The date half is what lets `collect_existing_titles`
# put a session's own day at the front of the window, and every writer below has to keep
# that pair shape: a bare slug read back as a pair raises, and read back as a membership
# test silently misses. Both happened once the window became date-aware (2026-09-02).
_TITLE_SLUGS: dict[str, dict[str, list[tuple[str, str]]]] = {}
_TAG_SKIP = {*TYPED_TYPES, "session", "context", "index"}


def collect_existing_tags(min_count: int = 2, top_k: int = 30,
                          project: str | None = None) -> tuple[str, ...]:
    """Top tags in use, lowercase canonical (grounds the extraction prompt).

    `project` scopes the vocabulary to that project's own notes. Without it the prompt is
    grounded on the WHOLE vault, so a batch run hands one project the signature tags of
    whichever project has the most notes: nine `gears_experiments` notes -- an ML
    architecture project with nothing quantum in it -- were re-tagged `quantum_computing`
    that way, losing their own `qa`/`testing` tags in the process, so they stopped
    matching their own queries while surfacing for the other project's (review 2026-09).

    A project with too small a vocabulary of its own falls back to the global one: a new
    project must not be left with no grounding at all, which would invite the model to
    invent a fresh tag per note.
    """
    global _TAG_COUNTS, _TAG_COUNTS_BY_PROJECT
    if _TAG_COUNTS is None:
        counter: dict[str, int] = {}
        by_project: dict[str, dict[str, int]] = {}
        for folder in ("Patterns", "Mistakes", "Decisions", "Sessions"):
            d = VAULT / folder
            if not d.exists():
                continue
            for p in d.glob("*.md"):
                # The note's DECLARED tags, from its frontmatter - not every hash-prefixed token
                # in its text. `#([\w/-]+)` over the whole file swept up whatever a session
                # quoted: `#include` and `#define` from C, `#ff00aa` from CSS, `#1234` from an
                # issue reference. Those became part of the vocabulary the extraction prompt is
                # grounded on, and came back as tags on new notes. Frontmatter is the list
                # `_note_meta` reads and the one the body line is rendered FROM, so this is the
                # same set without the noise - and a header-only read instead of a whole-file one.
                fm = _read_frontmatter_file(p)
                owner = (parse_typed_stem(p.stem) or {}).get("project") or ""
                for tag in (fm.get("tags") or []):
                    if not isinstance(tag, str):
                        continue
                    t = slug_tag(tag).lower()
                    if not t or t.startswith("project/") or t in _TAG_SKIP:
                        continue
                    counter[t] = counter.get(t, 0) + 1
                    if owner:
                        pc = by_project.setdefault(owner, {})
                        pc[t] = pc.get(t, 0) + 1
        _TAG_COUNTS = counter
        _TAG_COUNTS_BY_PROJECT = by_project

    def _top(counts: dict[str, int]) -> list[str]:
        return sorted([t for t, n in counts.items() if n >= min_count],
                      key=lambda t: -counts[t])[:top_k]

    if project:
        own = _top(_TAG_COUNTS_BY_PROJECT.get(project, {}))
        if len(own) >= MIN_PROJECT_TAG_VOCAB:
            return tuple(own)
        # too thin to ground on alone: pad with the global vocabulary, own tags first
        pad = [t for t in _top(_TAG_COUNTS) if t not in own]
        return tuple((own + pad)[:top_k])
    return tuple(_top(_TAG_COUNTS))


def _clear_tag_counts():
    global _TAG_COUNTS, _TAG_COUNTS_BY_PROJECT
    _TAG_COUNTS = None
    _TAG_COUNTS_BY_PROJECT = {}


collect_existing_tags.cache_clear = _clear_tag_counts   # back-compat with callers


#: A note is "in Cyrillic" once this many of its letters are; below it, a borrowed word or a
#: quoted error message is not a language change.
_CYR_MIN_SHARE = 0.12


def dominant_script(text: str) -> str:
    """`"cyrillic"`, `"latin"`, or `""` when the sample is too short or too mixed to call.

    Counts letters, not bytes: a Russian session is full of Latin identifiers and paths, so a
    simple majority vote reads almost every bilingual transcript as English. A share of
    Cyrillic letters above a low floor is the signal - Russian prose cannot happen by
    accident, while Latin can, being what code is written in.
    """
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04ff")
    lat = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    total = cyr + lat
    if total < 200:
        return ""                          # too little to judge; say nothing rather than guess
    if cyr / total >= _CYR_MIN_SHARE:
        return "cyrillic"
    return "latin"


def language_rule(transcript: str) -> str:
    """The LANGUAGE line, with the answer already worked out.

    The prompt used to state the rule - *write in the dominant language of the session* - and
    leave the model to apply it. Measured on `supersession_v1`, a corpus that is entirely
    English, the local model wrote 17 of 123 notes in Russian: a drift of 0.138. An explicit
    instruction naming one language is a different kind of ask, and costs nothing extra.
    """
    script = dominant_script(transcript)
    if script == "cyrillic":
        return ("write title/description/prevention/context_update/session_summary in "
                "RUSSIAN. The session is in Russian; do not answer in English.")
    if script == "latin":
        return ("write title/description/prevention/context_update/session_summary in "
                "ENGLISH. The session is in English; do not answer in any other language.")
    return ("write title/description/prevention/context_update/session_summary in the "
            "dominant language of the SESSION content above.")


TITLE_WINDOW = 40                 # slugs shown to the extractor as "already exists"


def collect_existing_titles(project: str, for_date: str | None = None
                            ) -> dict[str, tuple[str, ...]]:
    """Existing typed-note title-slugs for `project` (grounds LLM dedup).

    `for_date` puts the notes ALREADY WRITTEN FOR THAT DAY at the front of the window.
    Without it the window is the globally newest 40 in chronological glob order, which
    is exactly wrong when a session is re-mined: a project with 227 live patterns, 127 of
    them newer than the session's own date, gave a window containing ZERO notes from that
    date - so the extractor was told to avoid duplicates while being shown none of the
    ones it was about to create (vault review 2026-09).
    """
    if project not in _TITLE_SLUGS:
        out: dict[str, list[tuple[str, str]]] = {nt: [] for nt in TYPED_TYPES}
        for ntype in TYPED_TYPES:
            d = VAULT / TYPE_FOLDER[ntype]
            if not d.exists():
                continue
            for p in d.glob("*.md"):
                parsed = parse_typed_stem(p.stem)
                if parsed and parsed["project"] == project and parsed["ntype"] == ntype:
                    out[ntype].append((p.stem[:10], parsed["slug"]))
        _TITLE_SLUGS[project] = out

    picked: dict[str, tuple[str, ...]] = {}
    for nt, rows in _TITLE_SLUGS[project].items():
        if for_date:
            same = [sl for dt, sl in rows if dt == for_date]
            other = [sl for dt, sl in rows if dt != for_date]
            # The day's own notes first, then the newest others fill the remainder. `fill` is
            # bound to its own name because `other[-0:]` is the WHOLE list, not none of it: once
            # the day had TITLE_WINDOW notes of its own the remainder was every other-day note,
            # and the trailing slice then kept the tail - which was all of `other`. A busy day
            # showed the extractor no note from that day at all, the case the window exists for
            # (T1 review 2026-09-19).
            fill = max(0, TITLE_WINDOW - len(same))
            window = same[-TITLE_WINDOW:] + (other[-fill:] if fill else [])
            picked[nt] = tuple(window[-TITLE_WINDOW:] if len(window) > TITLE_WINDOW else window)
        else:
            picked[nt] = tuple(sl for _dt, sl in rows[-TITLE_WINDOW:])
    return picked


def _clear_title_slugs():
    """Clear the title-grounding cache by NAME, the way `_clear_tag_counts` beside it does.

    This was `_TITLE_SLUGS.clear` - a bound method of whichever dict existed at import - so
    rebinding the name (a reset written the obvious way, a test monkeypatching the cache)
    left `cache_clear` scrubbing a dict nobody reads any more, and said nothing about it.
    """
    global _TITLE_SLUGS
    _TITLE_SLUGS = {}


collect_existing_titles.cache_clear = _clear_title_slugs   # back-compat with callers


def _unregister_slug(stem: str) -> None:
    """Drop a retired note's slug from the grounding cache so a later session in the
    same sweep isn't told a superseded title still exists (audit A16). A same-slug
    re-statement re-registers it right after, so only genuinely-gone titles vanish."""
    parsed = parse_typed_stem(stem)
    if not parsed:
        return
    bucket = _TITLE_SLUGS.get(parsed["project"], {}).get(parsed["ntype"])
    if not bucket:
        return
    # Drop every entry carrying this slug, whatever date it was written under: the retired
    # note is gone from the live folder, so no date of it should still ground the extractor.
    kept = [row for row in bucket if row[1] != parsed["slug"]]
    if len(kept) != len(bucket):
        bucket[:] = kept


def register_written_notes(project: str, tags, links: dict) -> None:
    """Fold one session's writes into the grounding caches so the NEXT session in
    a sweep sees them without a full disk rescan (audit M-k). Only updates caches
    already built this run; an unbuilt cache will include the writes when first
    populated from disk."""
    clean = [t for t in _norm_tags(tags) if not t.startswith("project/") and t not in _TAG_SKIP]
    if _TAG_COUNTS is not None:
        for t in clean:
            _TAG_COUNTS[t] = _TAG_COUNTS.get(t, 0) + 1
    # The per-project counter is the one production reads: `collect_existing_tags(project=...)`
    # returns the project's own vocabulary as soon as it has MIN_PROJECT_TAG_VOCAB entries and never
    # consults the global one. Folding only into the global counter made this whole function inert
    # for any project with a vocabulary of its own - the second session of a sweep was grounded on a
    # list frozen at the first disk scan, and re-invented the tags the first session had just
    # created, which is exactly what audit M-k added it to prevent.
    if project and _TAG_COUNTS is not None:
        # `_TAG_COUNTS is not None` is the signal that a scan has happened this run; the project's
        # own slot may still be absent, because a project with no tags yet has nothing on disk -
        # and that is exactly the project whose first session should ground the second.
        slot = _TAG_COUNTS_BY_PROJECT.setdefault(project, {})
        for t in clean:
            slot[t] = slot.get(t, 0) + 1
    if project in _TITLE_SLUGS:
        slot = _TITLE_SLUGS[project]
        for nt, stems in (links or {}).items():
            bucket = slot.setdefault(nt, [])
            seen = {sl for _dt, sl in bucket}
            for stem in stems:
                parsed = parse_typed_stem(stem)
                if parsed and parsed["slug"] not in seen:
                    bucket.append((parsed["date"], parsed["slug"]))
                    seen.add(parsed["slug"])


