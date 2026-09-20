#!/usr/bin/env python3
"""K8 layer 2: two live notes of one slug fold into the newest at read time - nothing hidden.

The merge exists as a representation at read time and is re-decided for free (ledger K8): among the
hits, same-slug siblings collapse into their newest note, which keeps the group's best rank and
carries `earlier: [stems]`; the earlier statement is attached as one compact line - its `[facts]`
block when it has one, else the head of its statement, bounded by NEVERTWICE_EARLIER_MAX_CHARS -
and the older hit leaves the list. Both facts reach the agent, newest first, and nothing is demoted
below k on a presumption. Applied on the hook's injection path (`retrieve_relevant` -> `_fact_line`)
and on the API path (`search_core`, the description carries the attachment). No embedder, no LLM.

    python tests/_test_k8_read_pairing.py
"""
import _env_guard  # noqa: F401
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nevertwice"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m  # noqa: E402
import memory_search as ms  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


A0 = "2026-06-01-p-decision-timeout"
A1 = "2026-06-01-p-decision-timeout-2"
A2 = "2026-06-09-p-decision-timeout"
B0 = "2026-06-01-p-decision-cache"

# F7 (xhigh review): `_sibling_key`'s legacy fallback (a pre-stamp note) now requires the
# stripped base to actually exist on disk, same day, same folder - a bare synthetic hit dict
# with no backing file no longer folds on the name pattern alone. A0 is the base these `-2`/
# cross-day checks fold against, so its file needs to be real; the OTHERS (A1/A2/B0) do not -
# the fallback only ever reads the STRIPPED BASE's path, never the sibling's own.
d = make_sandbox(m, "k8l2_", offline=True)
(m.VAULT / "Decisions").mkdir(exist_ok=True)
(m.VAULT / "Decisions" / f"{A0}.md").write_text(
    "---\ndate: 2026-06-01\nproject: p\ntype: decision\n---\n\n# timeout\n\nplaceholder\n", encoding="utf-8")


def H(stem, **kw):
    return {"stem": stem, "ntype": "decision", "title": stem.split("-decision-", 1)[-1], **kw}


print("\n- pairing on ranked hits -")
out = m.pair_siblings([H(A0), H(B0), H(A1)])
check("the same-day '-2' sibling is the newest and leads", [h["stem"] for h in out] == [A1, B0], str([h["stem"] for h in out]))
check("the lead keeps the group's best rank (position 0)", out[0]["stem"] == A1)
check("the lead carries the earlier stem", out[0].get("earlier") == [A0])
out = m.pair_siblings([H(B0), H(A0), H(A2), H(A1)])
check("across days the later date leads; earlier ones listed newest first",
      [h["stem"] for h in out] == [B0, A2] and out[1]["earlier"] == [A1, A0], str(out))
check("hits without siblings are untouched", m.pair_siblings([H(A0), H(B0)]) == [H(A0), H(B0)])
check("hits without a typed stem pass through",
      m.pair_siblings([{"stem": "not-a-typed-stem"}, H(A0)])[0] == {"stem": "not-a-typed-stem"})
check("different projects or types never pair",
      len(m.pair_siblings([H(A0), {"stem": "2026-06-01-q-decision-timeout", "ntype": "decision"},
                           {"stem": "2026-06-01-p-pattern-timeout", "ntype": "pattern"}])) == 3)
check("a slug that merely ends in -2 pairs only with its own base",
      len(m.pair_siblings([H("2026-06-01-p-decision-python-2"), H(B0)])) == 2)
src = [H(A0), H(A1)]
m.pair_siblings(src)
check("the input hits are not mutated", "earlier" not in src[0] and "earlier" not in src[1])

print("\n- the attached earlier statement -")
d = make_sandbox(m, "k8l2_", offline=True)
F = m._FACTS_MARK
SA = "2026-06-01-1000-k8p-session-aaaaaaaa"
SB = "2026-06-01-1100-k8p-session-bbbbbbbb"
OLD = f"The HTTP client timeout is 30 seconds, which proved sufficient.{F}the HTTP client timeout is 30 seconds"
NEW = f"The HTTP client timeout is 5 seconds.{F}the HTTP client timeout is 5 seconds"
old = m.write_typed_note("Decisions", {"title": "http client timeout", "description": OLD}, "k8p", "2026-06-01", ["t"], "decision", session_stem_=SA)
new = m.write_typed_note("Decisions", {"title": "http client timeout", "description": NEW}, "k8p", "2026-06-01", ["t"], "decision", session_stem_=SB)
check("the pair is on disk as siblings", new == f"{old}-2")
check("the earlier text is the facts block when the note has one",
      m._earlier_text(old, "decision") == "the HTTP client timeout is 30 seconds", m._earlier_text(old, "decision"))
plain = m.write_typed_note("Patterns", {"title": "cache backend", "description": "Use redis for the cache, it proved fast enough for every route we tried."},
                           "k8p", "2026-06-01", ["t"], "pattern", session_stem_=SA)
check("without a facts block the head of the statement is attached, bounded",
      m._earlier_text(plain, "pattern", max_chars=30) == "Use redis for the cache, it p…"
      and len(m._earlier_text(plain, "pattern", max_chars=30)) == 30, m._earlier_text(plain, "pattern", max_chars=30))
check("the bound defaults to NEVERTWICE_EARLIER_MAX_CHARS", m.EARLIER_MAX_CHARS == 100
      and len(m._earlier_text(plain, "pattern")) <= 100)
check("a missing note attaches nothing", m._earlier_text("2026-06-01-k8p-decision-nope", "decision") == "")
junk = m.write_typed_note("Decisions", {"title": "pull request approval policy",
                                        "description": f"Every pull request needs two approvals before merge.{F}rolled it out behind the usual staged release"},
                          "k8p", "2026-06-01", ["t"], "decision", session_stem_=SA)
check("a facts block without a value is a harvested distractor: the statement is attached instead",
      m._earlier_text(junk, "decision") == "Every pull request needs two approvals before merge.", m._earlier_text(junk, "decision"))

print("\n- the hook's line -")
line = m._fact_line({"stem": new, "ntype": "decision", "title": "http client timeout", "earlier": [old]})
#: Track N (part 3.1): the line still carries both statements, newest first, but the earlier half
#: is now the value that differs rather than the sentence around it - measured at 473.3 -> 352.8
#: characters a query on the explicit corpus, with the replaced value present every time.
check("the fact line carries the newest statement and the earlier value, in that order",
      "5 seconds" in line and "earlier: 30 seconds" in line
      and line.index("5 seconds") < line.index("earlier: 30 seconds")
      and line.index("5 seconds") < line.index("30 seconds"), line)
check("a hit without `earlier` renders as before",
      "earlier:" not in m._fact_line({"stem": new, "ntype": "decision", "title": "http client timeout"}))

print("\n- the API result -")
res = m.pair_siblings([{"stem": old, "ntype": "decision", "title": "http client timeout", "description": OLD},
                       {"stem": new, "ntype": "decision", "title": "http client timeout", "description": NEW}], attach=True)
#: Track N: the description now starts with the SERVED statement - the sentence without the
#: `[facts]` list, which the reader was being handed twice - and ends with the value that differs.
check("one result, the newest, with the earlier value appended to its served description",
      len(res) == 1 and res[0]["stem"] == new
      and res[0]["description"].startswith(m._served_text(NEW))
      and "earlier: 30 seconds" in res[0]["description"], str(res))
check("the attachment is bounded: at most the cap plus the label",
      len(res[0]["description"]) <= len(NEW) + len(" | earlier: ") + m.EARLIER_MAX_CHARS)

print("\n- through the ranking paths, no embedder -")
m.save_embed_cache({
    old: {"ntype": "decision", "project": "k8p", "title": "http client timeout", "desc": OLD, "prevention": "", "recurrence": 1},
    new: {"ntype": "decision", "project": "k8p", "title": "http client timeout", "desc": NEW, "prevention": "", "recurrence": 1},
    plain: {"ntype": "pattern", "project": "k8p", "title": "cache backend", "desc": "Use redis for the cache.", "prevention": "", "recurrence": 1},
})
hits = m.retrieve_relevant("k8p", "http client timeout seconds", 5, cache=m.load_embed_cache())
stems = [h["stem"] for h in hits]
check("the hook's ranking returns the newest sibling once, with the earlier attached",
      new in stems and old not in stems and next(h for h in hits if h["stem"] == new).get("earlier") == [old], str(hits))
results, mode = ms.search_core("http client timeout seconds", "k8p", 5)
rs = [r["stem"] for r in results]
check(f"search_core ({mode}) pairs them too and attaches the earlier text",
      new in rs and old not in rs and "earlier:" in next(r for r in results if r["stem"] == new)["description"], str(results))
check("the earlier note itself is still live and reachable by as_of",
      (m.VAULT / "Decisions" / f"{old}.md").exists() and any(r["stem"] == old for r in m.as_of("k8p", "2026-06-02")))

print("\n- mutation check -")
_real = m.pair_siblings
m.pair_siblings = lambda hits, attach=False: hits
hits = m.retrieve_relevant("k8p", "http client timeout seconds", 5, cache=m.load_embed_cache())
check("with the pairing removed both siblings take a slot each - the check would catch it",
      old in [h["stem"] for h in hits] and new in [h["stem"] for h in hits])
m.pair_siblings = _real


# ── what a hit is served must not depend on whom it was served with ────
#
# `pair_siblings` returns `hits` UNTOUCHED when no group has two members, so the whole
# `attach` pass - and with it `_served_text` - never runs on a result that happens to contain
# no siblings. One unrelated pair anywhere in the result turns it on for everybody. So the
# text a reader gets for a given note depended on the kinship of the OTHER hits beside it,
# and track N's switch was measuring that rather than the change it names.
#
# Two traps this check has to respect, both paid for: `_served_text` only strips the block
# when the sentence already carries its values (`need <= have`) - otherwise stripping would
# lose a fact - so the fixture puts them in both halves; and siblings do not fold on the name
# alone, which is why A0 above is a real file on disk.
print("\n- the served text does not depend on the company -")

NL = chr(10)
# The sandbox was rebuilt further up, so the base A0 folds against has to exist in the store
# that is current NOW - `_sibling_key`'s legacy fallback reads the stripped base's path.
(m.VAULT / "Decisions").mkdir(parents=True, exist_ok=True)
(m.VAULT / "Decisions" / (A0 + ".md")).write_text(
    "---" + NL + "date: 2026-06-01" + NL + "project: p" + NL + "type: decision" + NL
    + "---" + NL + NL + "# timeout" + NL + NL + "placeholder" + NL,
    encoding="utf-8", newline="")

_serve = m.SERVE_FACTS_BLOCK
try:
    m.SERVE_FACTS_BLOCK = False                  # track N: serve the sentence, not the block
    DESC = "pool_size=5 on port=5432" + m._FACTS_MARK + "pool_size=5, port=5432"
    _alone = m.pair_siblings([H(B0, description=DESC)], attach=True)
    _beside = m.pair_siblings([H(B0, description=DESC), H(A0), H(A1)], attach=True)
    _a = _alone[0]["description"]
    _b = next(h["description"] for h in _beside if h["stem"] == B0)
    check("an unrelated pair in the result folds", len(_beside) == 2, str(len(_beside)))
    check("the same hit is served the same text either way", _a == _b, f"{_a!r} vs {_b!r}")
    check("and the switch is honoured when it is alone in the result",
          m._FACTS_MARK.strip() not in _a, repr(_a))
    check("as it already was beside a pair", m._FACTS_MARK.strip() not in _b, repr(_b))

    # ... and the switch still has an off position, or the checks above would pass on a
    # function that strips unconditionally.
    m.SERVE_FACTS_BLOCK = True
    _on_alone = m.pair_siblings([H(B0, description=DESC)], attach=True)[0]["description"]
    _on_beside = next(h["description"] for h in
                      m.pair_siblings([H(B0, description=DESC), H(A0), H(A1)], attach=True)
                      if h["stem"] == B0)
    check("with the block served, it is served in both shapes",
          m._FACTS_MARK.strip() in _on_alone and m._FACTS_MARK.strip() in _on_beside,
          f"{_on_alone!r} vs {_on_beside!r}")

    # attach=False keeps the fast path: nothing to attach, nothing to walk.
    _untouched = m.pair_siblings([H(B0, description=DESC)])
    check("without attach the list is handed back as it came",
          _untouched[0]["description"] == DESC, repr(_untouched[0]["description"]))
finally:
    m.SERVE_FACTS_BLOCK = _serve

print(f"\nK8 layer 2: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
