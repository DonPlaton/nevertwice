"""Morphology for the lexical signal: stop words and stemming, pure Python, no dependencies.

Why this exists (probe of 2026-09-06, CPU, cached vectors, engine unchanged). The lexical arm
ranked raw tokens: `running` and `run` were different words, and so were `ошибка` and
`ошибки`. On LoCoMo - dialogue turns, the length of a real note - dropping stop words lifted
lexical recall@5 from 0.499 to 0.557 and stemming on top of that to 0.599; the fused ranker
went from 0.549 to 0.627. On LongMemEval's long sessions (median 14k characters) the same
change cost 0.010, at the edge of the declared gate, because a long document already contains
most inflections of its own words. Mem0's local search stems (fastembed's Snowball) and that
was the whole of its lead on the turn-scale pool.

Two stemmers, chosen by script:

* **English: Porter (1980)**, the algorithm SQLite's FTS5 `porter` tokenizer implements, so
  the in-process BM25 and the FTS5 index agree on every token. Ported from the published
  description; checked against NLTK's `ORIGINAL_ALGORITHM` mode on 23,000 tokens.
* **Russian: Snowball**, Porter's own Russian stemmer (the RV/R2 regions, the perfective
  gerund / adjectival / verb / noun endings, `и` removal, `ость`, `нн` undoubling). Checked
  against `py_rust_stemmers`' Snowball on the vault's Cyrillic vocabulary; the one deliberate
  difference is that `ё` is folded to `е` first, so `отчёт` and `отчет` meet at one stem.

A token with a digit, or in neither script, passes through unchanged; identifiers are split
by the tokenizer before they get here, so `embed_text` arrives as `embed` and `text`.

    from stemmer import normalise
    normalise(["the", "embeddings", "were", "cached"])  ->  ["embed", "cach"]
"""
from __future__ import annotations

from functools import lru_cache

__all__ = ["STOP_EN", "STOP_RU", "stem", "stem_en", "stem_ru", "normalise", "is_stop"]

STOP_EN = frozenset("""
a about above after again against all am an and any are as at be because been before being
below between both but by can could did do does doing don down during each few for from further
had has have having he her here hers herself him himself his how i if in into is it its itself
just may me might more most must my myself no nor not now of off on once only or other ought our
ours ourselves out over own s same shall she should so some such t than that the their theirs
them themselves then there these they this those through to too under until up very was we were
what when where which while who whom why will with would you your yours yourself yourselves
""".split())

STOP_RU = frozenset("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне
было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до
вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя
их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому этого
какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно
при наконец два об другой хоть после над больше тот через эти нас про всего них какая много разве
три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда
конечно всю между это этот эта эти
""".split())


def is_stop(token: str) -> bool:
    return token in STOP_EN or token in STOP_RU


# ── English: Porter 1980 ─────────────────────────────────────────────────────────────────

_VOWELS = frozenset("aeiou")


def _cons(word: str, i: int) -> bool:
    """Consonant at position i. `y` is a consonant unless it follows a consonant."""
    ch = word[i]
    if ch in _VOWELS:
        return False
    if ch == "y":
        return True if i == 0 else not _cons(word, i - 1)
    return True


def _measure(stem: str) -> int:
    """The number of VC sequences: m in Porter's notation."""
    m = 0
    prev_v = False
    for i in range(len(stem)):
        v = not _cons(stem, i)
        if prev_v and not v:
            m += 1
        prev_v = v
    return m


def _has_vowel(stem: str) -> bool:
    return any(not _cons(stem, i) for i in range(len(stem)))


def _double_cons(word: str) -> bool:
    return len(word) >= 2 and word[-1] == word[-2] and _cons(word, len(word) - 1)


def _ends_cvc(word: str) -> bool:
    return (len(word) >= 3 and _cons(word, len(word) - 3) and not _cons(word, len(word) - 2)
            and _cons(word, len(word) - 1) and word[-1] not in "wxy")


def _m_gt0(stem: str) -> bool:
    return _measure(stem) > 0


def _m_gt1(stem: str) -> bool:
    return _measure(stem) > 1


def _apply(word: str, rules) -> str:
    """Porter's rule sets: the first matching suffix decides, and if its condition fails no
    other rule in the set is tried (the lists are ordered longest-overlap first)."""
    for suffix, repl, cond in rules:
        if suffix == "*d":
            if _double_cons(word):
                stem = word[:-2]
                return stem + repl if cond is None or cond(stem) else word
            continue
        if word.endswith(suffix):
            stem = word[:len(word) - len(suffix)] if suffix else word
            return stem + repl if cond is None or cond(stem) else word
    return word


_STEP2 = [("ational", "ate", _m_gt0), ("tional", "tion", _m_gt0), ("enci", "ence", _m_gt0),
          ("anci", "ance", _m_gt0), ("izer", "ize", _m_gt0), ("abli", "able", _m_gt0),
          ("alli", "al", _m_gt0), ("entli", "ent", _m_gt0), ("eli", "e", _m_gt0),
          ("ousli", "ous", _m_gt0), ("ization", "ize", _m_gt0), ("ation", "ate", _m_gt0),
          ("ator", "ate", _m_gt0), ("alism", "al", _m_gt0), ("iveness", "ive", _m_gt0),
          ("fulness", "ful", _m_gt0), ("ousness", "ous", _m_gt0), ("aliti", "al", _m_gt0),
          ("iviti", "ive", _m_gt0), ("biliti", "ble", _m_gt0)]
_STEP3 = [("icate", "ic", _m_gt0), ("ative", "", _m_gt0), ("alize", "al", _m_gt0),
          ("iciti", "ic", _m_gt0), ("ical", "ic", _m_gt0), ("ful", "", _m_gt0),
          ("ness", "", _m_gt0)]
_STEP4 = [("al", "", _m_gt1), ("ance", "", _m_gt1), ("ence", "", _m_gt1), ("er", "", _m_gt1),
          ("ic", "", _m_gt1), ("able", "", _m_gt1), ("ible", "", _m_gt1), ("ant", "", _m_gt1),
          ("ement", "", _m_gt1), ("ment", "", _m_gt1), ("ent", "", _m_gt1),
          ("ion", "", lambda s: _measure(s) > 1 and s[-1:] in ("s", "t")),
          ("ou", "", _m_gt1), ("ism", "", _m_gt1), ("ate", "", _m_gt1), ("iti", "", _m_gt1),
          ("ous", "", _m_gt1), ("ive", "", _m_gt1), ("ize", "", _m_gt1)]


def _step1ab(word: str) -> str:
    # 1a
    word = _apply(word, [("sses", "ss", None), ("ies", "i", None), ("ss", "ss", None),
                         ("s", "", None)])
    # 1b
    if word.endswith("eed"):
        stem = word[:-3]
        return stem + "ee" if _measure(stem) > 0 else word
    for suffix in ("ed", "ing"):
        if word.endswith(suffix):
            stem = word[:-len(suffix)]
            if _has_vowel(stem):
                last = stem[-1:]
                return _apply(stem, [("at", "ate", None), ("bl", "ble", None),
                                     ("iz", "ize", None),
                                     ("*d", last, lambda s, last=last: last not in "lsz"),
                                     ("", "e", lambda s: _measure(s) == 1 and _ends_cvc(s))])
            return word
    return word


@lru_cache(maxsize=65536)
def stem_en(word: str) -> str:
    """Porter (1980) on a lower-case ASCII-letter word."""
    w = _step1ab(word)
    if w.endswith("y") and _has_vowel(w[:-1]):                                     # 1c
        w = w[:-1] + "i"
    w = _apply(w, _STEP2)
    w = _apply(w, _STEP3)
    w = _apply(w, _STEP4)
    if w.endswith("e"):                                                            # 5a
        stem = w[:-1]
        m = _measure(stem)
        if m > 1 or (m == 1 and not _ends_cvc(stem)):
            w = stem
    if _measure(w) > 1 and _double_cons(w) and w.endswith("l"):                    # 5b
        w = w[:-1]
    return w


# ── Russian: Snowball ────────────────────────────────────────────────────────────────────

_RU_VOWELS = frozenset("аеиоуыэюя")
_PERFECTIVE_1 = ("вшись", "вши", "в")                     # preceded by а or я
_PERFECTIVE_2 = ("ившись", "ывшись", "ивши", "ывши", "ив", "ыв")
_ADJECTIVE = ("ими", "ыми", "его", "ого", "ему", "ому", "ее", "ие", "ые", "ое", "ей", "ий",
              "ый", "ой", "ем", "им", "ым", "ом", "их", "ых", "ую", "юю", "ая", "яя", "ою",
              "ею")
_PARTICIPLE_1 = ("ем", "нн", "вш", "ющ", "щ")             # preceded by а or я
_PARTICIPLE_2 = ("ивш", "ывш", "ующ")
_REFLEXIVE = ("ся", "сь")
_VERB_1 = ("ете", "йте", "ешь", "нно", "ла", "на", "ли", "ем", "ло", "но", "ет", "ют", "ны",
           "ть", "й", "л", "н")                            # preceded by а or я
_VERB_2 = ("ейте", "уйте", "ила", "ыла", "ена", "ите", "или", "ыли", "ило", "ыло", "ено",
           "ует", "уют", "ены", "ить", "ыть", "ишь", "ей", "уй", "ил", "ыл", "им", "ым", "ен",
           "ят", "ит", "ыт", "ую", "ю")
_NOUN = ("иями", "ями", "ами", "иях", "иям", "ией", "ием", "ев", "ов", "ие", "ье", "еи", "ии",
         "ей", "ой", "ий", "ям", "ем", "ам", "ом", "ах", "ях", "ию", "ью", "ия", "ья", "а",
         "е", "и", "й", "о", "у", "ы", "ь", "ю", "я")
_DERIVATIONAL = ("ость", "ост")
_SUPERLATIVE = ("ейше", "ейш")


def _rv_start(word: str) -> int:
    """RV: after the first vowel; the end of the word when there is none."""
    for i, ch in enumerate(word):
        if ch in _RU_VOWELS:
            return i + 1
    return len(word)


def _r2_start(word: str) -> int:
    """R2: the region after the first non-vowel following a vowel, applied twice."""
    def region(start: int) -> int:
        seen_vowel = False
        for i in range(start, len(word)):
            if word[i] in _RU_VOWELS:
                seen_vowel = True
            elif seen_vowel:
                return i + 1
        return len(word)
    return region(region(0))


def _strip(word: str, rv: int, endings, preceded=None) -> str | None:
    """Remove the first matching ending that lies inside RV; `preceded` demands the letter
    before the ending be one of those, and that letter must itself lie inside RV (Snowball
    tests it under the same region limit), while it stays in the stem."""
    for e in endings:
        cut = len(word) - len(e)
        if cut >= rv and word.endswith(e):
            if preceded and (cut - 1 < rv or word[cut - 1] not in preceded):
                continue
            return word[:cut]
    return None


@lru_cache(maxsize=65536)
def stem_ru(word: str) -> str:
    """Snowball Russian on a lower-case Cyrillic word."""
    word = word.replace("ё", "е")
    rv = _rv_start(word)
    if rv >= len(word):
        return word
    # step 1
    w = _strip(word, rv, _PERFECTIVE_1, "ая") or _strip(word, rv, _PERFECTIVE_2)
    if w is None:
        w = _strip(word, rv, _REFLEXIVE) or word
        adj = _strip(w, rv, _ADJECTIVE)
        if adj is not None:
            w = (_strip(adj, rv, _PARTICIPLE_1, "ая") or _strip(adj, rv, _PARTICIPLE_2)
                 or adj)
        else:
            verb = _strip(w, rv, _VERB_1, "ая") or _strip(w, rv, _VERB_2)
            if verb is not None:
                w = verb
            else:
                w = _strip(w, rv, _NOUN) or w
    # step 2
    if w.endswith("и") and len(w) - 1 >= rv:
        w = w[:-1]
    # step 3
    r2 = _r2_start(w)
    d = _strip(w, r2, _DERIVATIONAL)
    if d is not None:
        w = d
    # step 4
    if w.endswith("нн") and len(w) - 1 >= rv:
        w = w[:-1]
    else:
        s = _strip(w, rv, _SUPERLATIVE)
        if s is not None:
            w = s
            if w.endswith("нн") and len(w) - 1 >= rv:
                w = w[:-1]
        elif w.endswith("ь") and len(w) - 1 >= rv:
            w = w[:-1]
    return w


# ── dispatch ─────────────────────────────────────────────────────────────────────────────

def stem(token: str) -> str:
    """Stem one lower-case token by its script; anything else passes through unchanged."""
    if not token or not token.isalpha():
        return token
    first = token[0]
    if "a" <= first <= "z":
        return stem_en(token) if token.isascii() else token
    if "Ѐ" <= first <= "ӿ":
        return stem_ru(token)
    return token


def normalise(tokens, *, stop: bool = True, stemming: bool = True) -> list[str]:
    """Stop words out, stems in, order and multiplicity kept (BM25 needs counts)."""
    out = []
    for t in tokens:
        if stop and (t in STOP_EN or t in STOP_RU):
            continue
        out.append(stem(t) if stemming else t)
    return out
