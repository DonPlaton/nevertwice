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
  6. TOKEN PROVENANCE (owner review, 2026-09-23, closing W17): before a cluster is promoted,
     every content token of the candidate sentence must occur in the vocabulary of at least
     TOKEN_PROVENANCE_MIN_PROJECTS of the cluster's OWN source projects - the same "repetition
     proves universality" idea the >=2-project cluster rule already applies to the whole
     sentence, now applied per token. This is the actual cross-project BOUNDARY for an entity
     the write-time scanner missed (W17): `principle_scan` only catches a product name when the
     extractor declared it as an entity; this check does not depend on what was declared at
     all, because it asks the CORPUS, not the extraction. The medoid is tried first; on failure
     the next-most-central member is tried, in order, until one passes or none does - a cluster
     with no passing candidate is not promoted, counted as `rejected_single_project_token`, and
     the offending tokens are reported (never silently dropped - a rejection is the boundary
     doing its job and has to be visible, same principle as the write-time scan's degradation
     contract);
  7. a cluster spanning >= 2 distinct projects, WITH a provenance-passing candidate, is written
     as one `universal` note per cluster - description = the chosen sentence, `sources` = every
     member stem, `recurrence` = the number of DISTINCT projects (not the member count - two
     notes from one project must not inflate it); a mistake cluster also mints a global
     advisory guard;
  8. an existing `universal` note whose live cluster has fallen to one project, lost its
     principle entirely, or stopped passing token provenance is retired to Archive/, mirroring
     `archive_old_typed`'s own move.

    python -m nevertwice.principles --dry     # print what WOULD be promoted, write nothing
    python -m nevertwice.principles --apply   # write (normally called from consolidate_memory.py)

Standard library + the engine only - no third-party dependency.
"""
from __future__ import annotations

import ipaddress
import json
import re
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

#: C8 (2026-09-24), G5.6: measured by research/principle_twins.py (A6) against 40 same-rule /
#: 40 different-rule pairs, sweeping T over [0.75, 0.95] and picking the lowest T with zero
#: false merges on the negatives - the floor of the range, not a value chosen from the middle
#: of it. G5.6: 0/40 false merges, Wilson upper bound 0.088, merge recall 15/40; 0.75 is the
#: floor of the swept range [0.75, 0.95]; the max negative cosine was not recorded. Cited from
#: .loop/explore/principle_twins.json (chosen_T: 0.75, sweep[T=0.75]: false_merges=0/40,
#: true_merges=15/40). At T=0.90 (the OLD default) the same sweep merges 0/40 paraphrases -
#: no benefit was ever measured at that threshold, only at 0.75.
T_PRINCIPLE = m.env_float("NEVERTWICE_PRINCIPLE_T", 0.75)

MIN_CLUSTER_PROJECTS = 2
PRINCIPLES_CACHE_PATH = "Universal/principles_cache.json"
#: The typed folders a `principle` can live on (A1 scopes the field to pattern/mistake).
_PRINCIPLE_TYPES = ("pattern", "mistake")

#: How many of a cluster's DISTINCT source projects must independently carry a content token
#: before a candidate sentence is allowed to promote (W17 closure, owner review 2026-09-23) - a
#: module-level name, not an inline literal, so tests/_test_principle_promote.py's mutation can
#: monkeypatch it without editing this file.
TOKEN_PROVENANCE_MIN_PROJECTS = 2

#: Grammatical stopwords only - a TECHNICAL word (python, docker, cache, retry) is exactly the
#: kind of token the provenance check is supposed to let through when both projects use it, so
#: this list stays short and does not try to filter jargon.
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "it", "its", "not",
    "this", "that", "so", "before", "than", "with", "at", "by", "be", "as", "are", "was",
    "were", "will", "would", "should", "could", "can", "do", "does", "did", "has", "have",
    "had", "but", "if", "then", "else", "when", "while", "from", "into", "onto", "out", "up",
    "down", "over", "under", "again", "once", "here", "there", "all", "any", "both", "each",
    "few", "more", "most", "other", "some", "such", "no", "nor", "own", "same", "too", "very",
    "just", "one", "two", "you", "your", "we", "our", "they", "their", "what", "which", "who",
    "whom", "these", "those", "am", "been", "being",
})
_MIN_TOKEN_LEN = 3


def _content_tokens(text: str) -> set[str]:
    """Lowercased alnum runs, length >= 3, stopwords dropped. A hyphen/underscore/space inside
    a token is a SPLIT point, not part of it - `queue-shard-a000` yields three tokens
    (`queue`, `shard`, `a000`), each independently provable - "each part of a code-like token
    counts" (the plan's own wording). A plain `re.findall`, not `_lazy_re`: this runs only at
    promotion time (sleep-time consolidation), never on a hot path a PreToolUse call shares."""
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
           if len(t) >= _MIN_TOKEN_LEN and t not in _STOPWORDS}


def _project_token_vocabulary(project: str) -> set[str]:
    """Every content token across `project`'s own live typed notes - title, description,
    principle, entities and tags, all three folders (unlike `_project_vocabulary` above, which
    is the write-time re-scan's FORBIDDEN-TOKEN set of literal entity/tag strings; this is a
    tokenized bag of words for the provenance check below, a different question).

    FAIL CLOSED (owner instruction): a note, or a whole project, that cannot be read
    contributes NOTHING to its own vocabulary - never lets a read error masquerade as "this
    project already contains every token", which is the direction that would defeat the
    check. `_read_frontmatter_file`/`parse_typed_stem` already degrade to {}/None on a bad
    file; the `try` here additionally covers the full-text read this function needs for the
    title/description that frontmatter-only reads skip."""
    vocab: set[str] = set()
    for ntype in m.TYPED_TYPES:
        folder = m.VAULT / m.TYPE_FOLDER[ntype]
        if not folder.exists():
            continue
        for p in sorted(folder.glob("*.md")):
            parsed = m.parse_typed_stem(p.stem)
            if not parsed or parsed["project"] != project:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, body = m._read_frontmatter(text)
            title, desc, _prevention = m._parse_note_body(body.split("\n"))
            vocab |= _content_tokens(title)
            vocab |= _content_tokens(desc)
            principle = fm.get("principle")
            if isinstance(principle, str):
                vocab |= _content_tokens(principle)
            ents = fm.get("entities")
            if isinstance(ents, list):
                for e in ents:
                    vocab |= _content_tokens(str(e))
            tags = fm.get("tags")
            if isinstance(tags, list):
                for t in tags:
                    vocab |= _content_tokens(str(t))
    return vocab


def _identifier_shaped_words(sentence: str) -> set[str]:
    """The whitespace-separated words of `sentence` that look identifier-shaped, by ALL FIVE
    shapes (2026-09-23, H6) - not just the three the write/rescan gate keeps after option (A).
    camelCase/PascalCase and hyphen-infra moved OFF the write gate specifically BECAUSE
    provenance (corpus corroboration) is the mechanism that tells a public name (PostgreSQL,
    corroborated by many projects' own corpus) from a private one (UserRepository, corroborated
    by none) - so provenance has to check those two shapes too, or a private camel/kebab name
    would cross with NO defense at all once write time stopped catching it.

    Checked on the RAW WORD, before `_content_tokens`'s own lowercasing and hyphen/underscore
    split destroy exactly the shape information these checks need (case, hyphen position,
    underscore) - a digit survives tokenization, the other four do not."""
    words: set[str] = set()
    for raw in re.findall(r"\S+", sentence or ""):
        word = raw.strip(".,;:!?()[]{}\"'")
        if not word:
            continue
        if (m._has_digit_dot_or_slash(word) or m._has_underscore(word)
                or m._is_screaming_snake(word) or m._has_camel_transition(word)
                or m._hyphen_part_is_infra(word)):
            words.add(word)
    return words


def _normalize_shaped_word(word: str) -> str:
    """The identity a compound identifier is corroborated BY (B1, coordinator decision
    2026-09-23): lowercased, with "-" and "_" UNIFIED - the same name spelled kebab by one
    project and snake by another ("payments-api" / "payments_api") is still the same name, and
    treating them as different would let a private identifier cross just by respelling it. "."
    and "/" are left AS-IS, never unified with "-"/"_": they carry host and path STRUCTURE
    ("payments-api" is not "payments.api", a different name in a different shape), where a
    hyphen is not interchangeable with either."""
    return word.strip(".,;:!?()[]{}\"'").lower().replace("_", "-")


def _project_shaped_word_vocabulary(project: str) -> set[str]:
    """Every identifier-shaped WORD (2026-09-23, H6/B1 - `_identifier_shaped_words`, normalized
    whole by `_normalize_shaped_word`) across `project`'s own live typed notes - title,
    description, principle, entities and tags, the same fields `_project_token_vocabulary`
    reads.

    B1 fix: a compound is corroborated as the WHOLE compound it is, never split into
    `_content_tokens` parts. The pre-fix code ran a shaped word through `_content_tokens`
    (`payments-api` -> {"payments", "api"}) and looked each PART up separately in a project's
    ordinary (also-split) vocabulary - so a project that merely used the words "payments" and
    "api" somewhere in ordinary prose, and never the compound "payments-api" at all, silently
    corroborated it. This vocabulary is built from WHOLE normalized shaped words instead, kept
    in `vocab_cache` under a DISTINCT key from `_project_token_vocabulary`'s (different
    identity space - a compound and its own parts are not interchangeable lookups).

    FAIL CLOSED (same discipline `_project_token_vocabulary` follows): a note, or a whole
    project, that cannot be read contributes NOTHING."""
    vocab: set[str] = set()
    for ntype in m.TYPED_TYPES:
        folder = m.VAULT / m.TYPE_FOLDER[ntype]
        if not folder.exists():
            continue
        for p in sorted(folder.glob("*.md")):
            parsed = m.parse_typed_stem(p.stem)
            if not parsed or parsed["project"] != project:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, body = m._read_frontmatter(text)
            title, desc, _prevention = m._parse_note_body(body.split("\n"))
            for field_text in (title, desc):
                vocab |= {_normalize_shaped_word(w) for w in _identifier_shaped_words(field_text)}
            principle = fm.get("principle")
            if isinstance(principle, str):
                vocab |= {_normalize_shaped_word(w) for w in _identifier_shaped_words(principle)}
            ents = fm.get("entities")
            if isinstance(ents, list):
                for e in ents:
                    vocab |= {_normalize_shaped_word(w)
                             for w in _identifier_shaped_words(str(e))}
            tags = fm.get("tags")
            if isinstance(tags, list):
                for t in tags:
                    vocab |= {_normalize_shaped_word(w)
                             for w in _identifier_shaped_words(str(t))}
    return vocab


#: THIRD correction (2026-09-23, C1b, the coordinator's decision): EMPTY. A hand-curated
#: "definitely ordinary" word list cannot be grown without repeating the SAME defect twice
#: over (140 words -> 18 -> 7, each cut still found partly bench-contaminated by the next
#: audit - see the FIRST and SECOND correction notes preserved below) - the list's only
#: possible sources were this project's OWN unit-test fixtures or hand-picked "generic"
#: vocabulary, and both are a curator's guess about what counts as ordinary, not evidence.
#: The corroboration rule (`_is_uncorroborated_private_word`, `_token_provenance`) needs NO
#: exemption list at all: an ordinary word used by a genuine cross-project paraphrase is, by
#: construction, either shared with the other side directly or coincidentally rare enough that
#: a REAL corroborating mention (a second note, anywhere in that project's own corpus) settles
#: it - real corpus evidence, not a guess about which words are "common enough". The cost this
#: accepts is unchanged from the SECOND correction's own KNOWN LIMITATION: a genuinely ordinary
#: word that happens to be unique to one project's SMALL corpus is asked for corroboration it
#: may not have. `tests/_test_principle_promote.py`'s own guard test now asserts this list
#: stays empty (not merely that no entry is bench-contaminated, since there is no entry) - if
#: it is ever grown back, that test's mechanical bench-corpus scan still runs against whatever
#: is added.
#:
#: FIRST correction (140 -> 18 words, B2 fix, kept only unit-test-fixture words) and SECOND
#: correction (18 -> 7 words, C1, `.loop/DECISION-Q5-H6-TRIGGER-2026-09-23.md`, removed
#: anything/bound/cap/disk/limit/load/measure/parameter/redact/secrets/writing - all still
#: found >=3x in the real `research/` bench corpus) are both superseded by this THIRD
#: correction, not re-described here - see git history for their own reasoning if needed.
_COMMON_WORDS: frozenset[str] = frozenset()


def _all_live_projects() -> set[str]:
    """Every project name with at least one live pattern/mistake note - used only by the
    corpus-uniqueness check below, which needs to know about EVERY project in the vault, not
    only the current cluster's members."""
    projects: set[str] = set()
    for ntype in _PRINCIPLE_TYPES:
        folder = m.VAULT / m.TYPE_FOLDER[ntype]
        if not folder.exists():
            continue
        for p in folder.glob("*.md"):
            parsed = m.parse_typed_stem(p.stem)
            if parsed:
                projects.add(parsed["project"])
    return projects


def _is_uncorroborated_private_word(tok: str, source_project: str, vocab_cache: dict) -> bool:
    """The coordinator's rule (2026-09-23, the second H6 residual): a plain lowercase word
    ("phoenix") or a hyphenated one whose parts are not infra nouns ("acme-corp") has NO shape
    this layer can key on at all - `_identifier_shaped_words` cannot flag either. So flag by
    CORPUS EVIDENCE instead: `tok` counts as a private word when it appears in
    `source_project`'s OWN vocabulary, appears in NO OTHER live project's vocabulary anywhere in
    the vault (not only this cluster's members), AND is not on the `_COMMON_WORDS` list. A token
    failing only the first two conditions but ON `_COMMON_WORDS` is treated as an ordinary word
    this project's corpus simply happens to be the only one using yet - the same reasoning
    `_token_provenance` already applies to every non-identifier-shaped token.

    Only called on tokens that are NOT part of any identifier-shaped word (B1) - a shaped
    compound's own parts ("payments"/"api" from "payments-api") are corroborated as the WHOLE
    compound by `_project_shaped_word_vocabulary` instead; checking them AGAIN here individually
    would apply a second, unrelated identity space to the same text and could flag an ordinary
    word this list happens not to cover for reasons that have nothing to do with the compound."""
    if tok in _COMMON_WORDS:
        return False
    if source_project not in vocab_cache:
        vocab_cache[source_project] = _project_token_vocabulary(source_project)
    if tok not in vocab_cache[source_project]:
        return False
    for proj in _all_live_projects():
        if proj == source_project or proj == m.UNIVERSAL_PROJECT:
            continue
        if proj not in vocab_cache:
            vocab_cache[proj] = _project_token_vocabulary(proj)
        if tok in vocab_cache[proj]:
            return False
    return True


#: C6 (2026-09-23, the coordinator's amended decision): an EXPLICIT network list, checked with
#: `ipaddress.ip_address(x) in ipaddress.ip_network(n)` - deliberately NOT `.is_private`/
#: `.is_global`, whose semantics changed between CPython 3.12.4 and 3.13 (gh-113171) and this
#: repo's support matrix is 3.10-3.14, so a stdlib property that answers differently per
#: interpreter cannot be the gate. Two private-address RFCs plus IPv6 ULA - every one of these
#: is near-certain to be reused, unrelated, by two different private networks, so seeing the
#: SAME address in two projects' corpora is coincidence, not evidence of a shared public
#: identity, however the ordinary corroboration rule would read it:
#:   10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16   - RFC 1918 (classic private IPv4)
#:   100.64.0.0/10                                - RFC 6598 (CGNAT; also Tailscale's own range)
#:   fc00::/7                                      - RFC 4193 (IPv6 unique local addresses)
#: Deliberately NOT on this list, so corroboration still lets them cross:
#:   127.0.0.0/8 (loopback) and 169.254.0.0/16 (link-local) - everyone's own box or own segment,
#:     not evidence of a SHARED private network the way an RFC1918 address is;
#:   192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24 (RFC 5737 documentation ranges) - the
#:     coordinator's decision: these are public EXAMPLE addresses (man pages, RFCs, tutorials),
#:     not evidence of anything private, so two projects both using one is unremarkable, not
#:     suspicious.
_NEVER_PUBLIC_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7",
))
#: Hostnames ending in an internal-only TLD/pseudo-TLD - never a public DNS name, whatever else
#: corroborates it (RFC 6762 §, RFC 8375, and common ops convention for `.corp`/`.intra`).
_NEVER_PUBLIC_HOST_SUFFIXES = (".internal", ".local", ".lan", ".corp", ".intra", ".home.arpa")


#: C6b (2026-09-23, the auditor's probe through the real pipeline): `_is_never_public_identifier`
#: checked the BARE value only - `ip_address("10.0.0.5:5432")` and `ip_address("db.internal:5432")`
#: both raise ValueError (a port suffix is not part of an address), `"db.internal:5432".endswith(
#: ".internal")` is False (the suffix is BEFORE the port, not at the string's end), `ip_address(
#: "10.0.0.0/8")` raises ValueError too (CIDR notation needs `ip_network`, not `ip_address`), and a
#: full URL's host is buried after a scheme and before a path. On the auditor's own sentence
#: ("...pin 10.0.0.5:5432 and db.internal:5432 behind 10.0.0.0/8, reach [fd00::1]:443 or
#: https://db.internal/x and printer.local.") the bare check caught 1 of 6. Fixed by extracting
#: the HOST first, in this order: strip a URL scheme (`scheme://`) and everything from the first
#: "/" after it; resolve a bracketed IPv6 host (`[addr]:port`, or the half-stripped `addr]:port` -
#: `_identifier_shaped_words`' own boundary strip already eats a leading "[" but has no matching
#: "]" at the string's end to eat too) on the "]" boundary, WITHOUT a further port strip (an IPv6
#: address's own trailing ":<hex>" must never be mistaken for ":<port>"); only once neither scheme
#: nor bracket applies, and only after confirming the text does NOT already parse as a bare
#: address on its own (a bare IPv6 address's trailing ":1" is not a port either), strip a trailing
#: ":<port>"; finally drop one trailing FQDN dot. A separate CIDR path (`a.b.c.d/n`) uses
#: `ip_network(x, strict=False)` and checks NETWORK OVERLAP, not point membership - "10.0.0.0/8"
#: mentioned in prose IS the whole private range, not one address inside it.
_NEVER_PUBLIC_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_NEVER_PUBLIC_PORT_RE = re.compile(r":\d+$")
_NEVER_PUBLIC_CIDR_RE = re.compile(r"^(.+)/(\d{1,3})$")


def _never_public_host(norm: str) -> str:
    """Extract the HOST `norm` really names, before the IP/suffix checks below run - see the
    C6b comment above `_NEVER_PUBLIC_SCHEME_RE` for the exact ordering and why each step is
    ordered where it is."""
    host = norm
    scheme_match = _NEVER_PUBLIC_SCHEME_RE.match(host)
    if scheme_match:
        host = host[scheme_match.end():]
        slash = host.find("/")
        if slash != -1:
            host = host[:slash]
        port_match = _NEVER_PUBLIC_PORT_RE.search(host)
        if port_match:
            host = host[:port_match.start()]
        return host.rstrip(".")
    if host.startswith("["):
        host = host[1:]
    if "]" in host:
        return host[:host.index("]")].rstrip(".")
    try:
        ipaddress.ip_address(host)
        return host                                    # already a bare, valid address as-is
    except ValueError:
        pass
    return _NEVER_PUBLIC_PORT_RE.sub("", host).rstrip(".")


def _is_never_public_identifier(norm: str) -> bool:
    """True when `norm` (an already `_normalize_shaped_word`-normalized identifier) must be
    treated as private REGARDLESS of how many projects' vocabularies it appears in - ordinary
    corroboration (`seen_in >= TOKEN_PROVENANCE_MIN_PROJECTS`) proves "at least two projects
    wrote this exact string", which is good evidence of a genuinely shared PUBLIC identity for
    almost everything this layer sees (PostgreSQL, a consumer-group concept) - but not for an
    RFC1918/CGNAT/ULA address or an internal-TLD hostname, which two UNRELATED private networks
    are likely to reuse independently by pure convention (every home router is someone's
    192.168.1.1). Called only on the shaped-word path (`_token_provenance`), only after the
    ordinary corroboration check already ran - this is an override that can turn a PASS into a
    FAIL, never the reverse."""
    cidr_match = _NEVER_PUBLIC_CIDR_RE.match(norm)
    if cidr_match:
        try:
            candidate_net = ipaddress.ip_network(norm, strict=False)
        except ValueError:
            candidate_net = None
        if candidate_net is not None:
            # C6c (2026-09-23, the coordinator's finding in the auditor's suggested overlaps
            # rule): `.overlaps()` flags any SUPERNET of a never-public range too -
            # "0.0.0.0/0" (or "::/0") overlaps every
            # network that exists, including 10.0.0.0/8, but "never open a security group to
            # 0.0.0.0/0" is itself a public, universal principle, not a private address. The
            # right question is containment, not intersection: never-public only when the
            # MENTIONED range is entirely INSIDE a never-public net (`subnet_of`, same IP
            # version - mixing v4/v6 in one comparison raises TypeError, so the version check
            # comes first and short-circuits it). Accepted consequence: a CIDR only PARTLY
            # private (10.0.0.0/7 = the private 10.0.0.0/8 plus the public 11.0.0.0/8) is NOT
            # flagged either - the same reasoning as 0.0.0.0/0, and equally accepted, not a
            # gap to close: a range that is not ENTIRELY private is not evidence of a shared
            # private network the way a range that IS entirely private is.
            return any(candidate_net.version == net.version and candidate_net.subnet_of(net)
                      for net in _NEVER_PUBLIC_NETWORKS)
    host = _never_public_host(norm)
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    if addr is not None:
        return any(addr in net for net in _NEVER_PUBLIC_NETWORKS)
    return host.endswith(_NEVER_PUBLIC_HOST_SUFFIXES)


def _token_provenance(sentence: str, source_project: str, cluster_projects: set,
                      vocab_cache: dict) -> tuple[bool, list]:
    """Two independent checks over `sentence`, each against `TOKEN_PROVENANCE_MIN_PROJECTS` of
    `cluster_projects` - "repetition proves universality" (the same idea the >=2-project CLUSTER
    rule already applies to the whole sentence):

      1. every identifier-shaped WORD (2026-09-23, H6/B1 - `_identifier_shaped_words`,
         corroborated as the WHOLE normalized compound via `_project_shaped_word_vocabulary`,
         never split into parts - see that function's own docstring for why the split version
         was wrong);
      2. every OTHER content token (not part of any shaped word) that is an uncorroborated
         private word with no shape at all (`_is_uncorroborated_private_word` - the
         coordinator's rule for "phoenix"/"acme-corp", 2026-09-23).

    A client's product name that only ONE project ever wrote cannot pass this, whether or not
    the extractor happened to declare it as an entity - unlike the write-time scanner
    (`principle_scan`, gated on declared entities), this check asks the CORPUS, not the
    extraction, so nothing the extractor did or did not declare can defeat it. `vocab_cache` is
    built once per `promote()` run and shared across every cluster's checks AND across the
    whole-vault uniqueness scan (the plan's own "build it once per promote run and cache it by
    project") - the shaped-word vocabulary is cached under a DISTINCT key
    (`(project, "shaped")`) from the plain content-token vocabulary, since they are different
    identity spaces over the same notes.

    BEFORE H6 (2026-09-23), EVERY content token needed >=2-project corroboration, including the
    ordinary English words a genuine cross-project PARAPHRASE is full of ("cap", "resource",
    "workload", "scaling") - each project's own vocabulary is usually just its one note's own
    wording, so two honest paraphrases of the same rule almost never share enough exact words to
    pass, and the check rejected the layer's whole PURPOSE along with the identifiers it was
    built to catch.

    Returns (passes, offending_tokens) - offending is empty exactly when it passes."""
    shaped_words = _identifier_shaped_words(sentence)
    shaped_subtokens = {t for w in shaped_words for t in _content_tokens(w)}
    offending: list = []

    for word in shaped_words:
        norm = _normalize_shaped_word(word)
        seen_in = 0
        for proj in cluster_projects:
            cache_key = (proj, "shaped")
            if cache_key not in vocab_cache:
                vocab_cache[cache_key] = _project_shaped_word_vocabulary(proj)
            if norm in vocab_cache[cache_key]:
                seen_in += 1
        # C6 (2026-09-23): a never-public shape (RFC1918/CGNAT/ULA address, internal-TLD host)
        # overrides an otherwise-passing corroboration count - two projects both writing the
        # SAME private-range address is not evidence they share a public identity.
        if seen_in < TOKEN_PROVENANCE_MIN_PROJECTS or _is_never_public_identifier(norm):
            offending.append(norm)

    for tok in _content_tokens(sentence):
        if tok in shaped_subtokens:
            continue           # corroborated (or not) as a WHOLE compound above, not again here
        if not _is_uncorroborated_private_word(tok, source_project, vocab_cache):
            continue           # an ordinary, corroborated-or-common word needs nothing further
        seen_in = 0
        for proj in cluster_projects:
            if proj not in vocab_cache:
                vocab_cache[proj] = _project_token_vocabulary(proj)
            if tok in vocab_cache[proj]:
                seen_in += 1
        if seen_in < TOKEN_PROVENANCE_MIN_PROJECTS:
            offending.append(tok)
    return not offending, offending


def _ranked_by_centrality(cluster: list[dict], vecs: dict[str, list]) -> list[dict]:
    """Cluster members ordered by average cosine to every OTHER member, highest first - the
    medoid is the head. Used both for the description text (as before A9's widening) and now
    for the provenance fallback order: "if the medoid fails, try the other members in order of
    their average cosine" (owner review, 2026-09-23)."""
    if len(cluster) == 1:
        return list(cluster)
    scored = []
    for cand in cluster:
        vc = vecs[cand["stem"]]
        others = [m2 for m2 in cluster if m2["stem"] != cand["stem"]]
        score = sum(m.cosine(vc, vecs[m2["stem"]]) for m2 in others) / len(others)
        scored.append((score, cand))
    scored.sort(key=lambda pair: -pair[0])
    return [c for _score, c in scored]


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
    """Every IDENTIFIER-SHAPED entity and tag on `project`'s own live typed notes (all three
    folders) - the project-wide forbidden set A5's re-scan uses, stronger than the write-time
    scan (which only forbade the ONE note's own entities).

    FILTERED through `m._looks_like_identifier` (2026-09-23) - this used to forbid every
    declared entity/tag VERBATIM, the SAME defect finding 1 fixed at write time, silently
    reintroduced here because this promotion-time rescan never got the same filter. A note's
    own principle routinely USES one of its own generic entity words ("storage" declared as an
    entity, then used in the principle text: "...persistent storage..."), so the unfiltered
    version self-rejected almost every real candidate before clustering ever got a chance to
    run - found on the v2 100-case real run (--extract, T=0.75): 53 of 57 cosine >= 0.75 pairs
    never became CANDIDATES together at all, because one side (sometimes both) rejected its OWN
    principle against its OWN vocabulary. `candidates` still read 1 or 2 in those cases only
    because `build_distractor` never declares entities, so the always-vocabulary-free
    distractor silently padded the count."""
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
                vocab.update(str(e) for e in ents
                            if e and m._looks_like_identifier(str(e), project))
            tags = fm.get("tags")
            if isinstance(tags, list):
                vocab.update(str(t) for t in tags
                            if t and m._looks_like_identifier(str(t), project))
    return vocab


def _rescan(candidates: list[dict]) -> list[dict]:
    """Re-run `principle_scan` per candidate against that candidate's project vocabulary - a
    second, stronger de-identification pass at promotion time (the plan's own description of
    A5). A candidate the scanner now rejects is dropped, never silently kept.

    The per-candidate `entities` contribution is ALSO filtered through `m._looks_like_identifier`
    (2026-09-23, same fix as `_project_vocabulary` above) - a candidate's own declared entities
    are exactly the vocabulary `_project_vocabulary` would find for it one call later anyway
    (once it is itself a live note), so leaving this one unfiltered would just move the same
    self-rejection to the FIRST promotion run instead of the second."""
    vocab_cache: dict[str, set[str]] = {}
    kept = []
    for c in candidates:
        proj = c["project"]
        if proj not in vocab_cache:
            vocab_cache[proj] = _project_vocabulary(proj)
        own_entities = {e for e in (c.get("entities") or ())
                        if e and m._looks_like_identifier(str(e), proj)}
        forbidden = {proj} | vocab_cache[proj] | own_entities
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


def _promote_cluster(cluster: list[dict], vecs: dict[str, list], apply: bool,
                     vocab_cache: dict) -> dict:
    ntype = cluster[0]["ntype"]
    member_stems = {c["stem"] for c in cluster}
    projects = sorted({c["project"] for c in cluster})
    ranked = _ranked_by_centrality(cluster, vecs)

    # TOKEN PROVENANCE (W17 closure): the medoid is tried first; on failure the next-most-
    # central member is tried, in that order, until one candidate's sentence passes or none
    # does. When NONE passes, the offending tokens are the UNION over every attempt, not just
    # the medoid's - a reader asking "why did this cluster fail" deserves every reason found,
    # not only the first one tried.
    chosen = None
    all_offending: set = set()
    for cand in ranked:
        ok, offending = _token_provenance(cand["principle"], cand["project"], set(projects),
                                         vocab_cache)
        if ok:
            chosen = cand
            break
        all_offending.update(offending)
    if chosen is None:
        return {"action": "rejected_single_project_token", "ntype": ntype, "title": "",
               "projects": projects, "members": sorted(member_stems),
               "offending_tokens": sorted(all_offending)}
    medoid = chosen

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

    # BUG FOUND while widening A9's --dry to exercise real retrieval (2026-09-23): write_typed_
    # note does NOT embed - that is a separate pipeline step everywhere else it is called
    # (process_session follows it with update_embeddings; consolidate_memory's own near-dup
    # merge relies on the caller having already embedded). This module was the one write path
    # that skipped it, so a freshly promoted universal note sat on disk with no cache/index
    # entry and was invisible to retrieve_cross_project(mode="universal") until something else
    # (embed_index.py, or a later session's incremental sync) caught it up - the pool A4 exists
    # to serve stayed silent for however long that took. Embedding here, right after the write,
    # closes that gap the same way every other writer already does it.
    m.update_embeddings([(stem, ntype, m.UNIVERSAL_PROJECT, title, medoid["principle"], "")])

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
    #: Built ONCE per run, shared across every cluster's provenance check (the plan's own
    #: "build it once per promote run and cache it by project") - re-walking a project's whole
    #: note corpus per cluster would be quadratic in the number of clusters sharing a project.
    vocab_cache: dict[str, set] = {}
    results = [_promote_cluster(c, vecs, apply, vocab_cache) for c in clusters]
    # A cluster that failed token provenance was never actually promoted - its members must
    # NOT count as "current" for retirement purposes, or an existing universal note from a
    # PRIOR run (before this cluster started failing provenance) would wrongly look "still
    # represented" and survive. Only a genuinely promoted/refreshed cluster keeps its sources
    # alive for `_retire_dropped` below.
    all_current_members: set = set()
    for c, r in zip(clusters, results):
        if r["action"] in ("promoted", "refreshed"):
            all_current_members.update(m2["stem"] for m2 in c)
    retired = _retire_dropped(all_current_members, apply)
    return {
        "candidates": len(candidates),
        "clusters": len(clusters),
        "promoted": sum(1 for r in results if r["action"] == "promoted"),
        "refreshed": sum(1 for r in results if r["action"] == "refreshed"),
        "refused": sum(1 for r in results if r["action"] == "refused"),
        "rejected_single_project_token": sum(
            1 for r in results if r["action"] == "rejected_single_project_token"),
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
         f"refused {summary['refused']}, "
         f"rejected_single_project_token {summary['rejected_single_project_token']}, "
         f"retired {summary['retired']}")
    for r in summary["results"]:
        extra = (f" offending={r['offending_tokens']}"
                if r["action"] == "rejected_single_project_token" else "")
        print(f"  {r['action']:28} [{r['ntype']}] {r['title']!r} <- "
             f"{', '.join(r['projects'])}{extra}")
    for stem in summary["retired_stems"]:
        print(f"  retired   {stem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
