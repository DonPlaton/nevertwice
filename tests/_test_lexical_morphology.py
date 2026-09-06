"""Morphology on the lexical signal: the engine tokenizer, BM25, the FTS5 index and its
rebuild stamp all agree on stop words and stems (`NEVERTWICE_LEXICAL_MORPHOLOGY`, default on).

The measurement behind the default is in research/LEXICAL_MORPHOLOGY.md; this suite pins the
mechanics: an inflected query finds a note that used another inflection on every lexical path,
the switch turns all of it off at once, digits are never stemmed, and an index built under the
other setting is rebuilt once rather than searched with stems against raw text.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import _env_guard  # noqa: E402,F401 - hermetic store before any project import
import memory_hook as m  # noqa: E402
import index_sqlite as idx  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


print("\n- the engine tokenizer -")
check("morphology is on by default", m.LEXICAL_MORPHOLOGY is True)
check("stop words out, stems in, order kept",
      m._token_list("The embeddings were cached twice") == ["embed", "cach", "twice"],
      str(m._token_list("The embeddings were cached twice")))
check("_tokens is the set of the same", m._tokens("caching the cache") == {"cach"})
check("digit runs are tokens and are never stemmed",
      m._token_list("error 12345 after retries") == ["error", "12345", "retri"])
check("Russian: stop words out, Snowball stems in",
      m._token_list("ошибки при запуске тестов") == ["ошибк", "запуск", "тест"],
      str(m._token_list("ошибки при запуске тестов")))
check("identifiers are split before stemming, so both halves stem",
      m._token_list("cache_index caches indexes") == ["cach", "index", "cach", "index"],
      str(m._token_list("cache_index caches indexes")))
saved = m.LEXICAL_MORPHOLOGY
m.LEXICAL_MORPHOLOGY = False
try:
    check("the switch restores raw tokens everywhere",
          m._token_list("The embeddings were cached") == ["the", "embeddings", "were", "cached"]
          and m._tokens("caching") == {"caching"})
finally:
    m.LEXICAL_MORPHOLOGY = saved

print("\n- BM25 over candidate notes -")
cands = [("2026-06-01-proj-mistake-cold", {"title": "cold cache", "desc": "the cache was cold after restart",
                                           "prevention": "warm it"}),
         ("2026-06-01-proj-mistake-other", {"title": "other", "desc": "unrelated timeout", "prevention": ""})]
sc = m._bm25_scores(m._tokens("caching restarts"), cands)
check("an inflected query scores the note that used another inflection",
      "2026-06-01-proj-mistake-cold" in sc and "2026-06-01-proj-mistake-other" not in sc, str(sc))
m.LEXICAL_MORPHOLOGY = False
try:
    check("...and does not with morphology off (the old behaviour)",
          not m._bm25_scores(m._tokens("caching restarts"), cands))
finally:
    m.LEXICAL_MORPHOLOGY = saved

print("\n- the FTS5 index carries the same stems and says which -")
d = make_sandbox(m, "morph_")
m.save_embed_cache({
    "2026-06-01-proj-mistake-oom": {"vec": [1.0, 0.0], "ntype": "mistake", "project": "proj",
        "title": "oom", "desc": "vram exhausted when subprocesses were spawned", "prevention": "reuse",
        "recurrence": 1, "confidence": 0.9},
    "2026-06-01-proj-pattern-x": {"vec": [0.0, 1.0], "ntype": "pattern", "project": "proj",
        "title": "x", "desc": "unrelated pattern", "recurrence": 1},
})
n = idx.build()
check("index builds", n == 2, str(n))
meta = idx.index_meta()
check("the index stamps the tokenisation it was built with", meta.get("lex_format") == "morph1"
      and idx.LEX_FORMAT == "morph1", str(meta))
check("scale_index_ready() accepts a matching stamp", m.scale_index_ready())
check("MATCH terms go through the same tokenizer",
      idx._safe_terms("spawning subprocesses") == ["spawn", "subprocess"],
      str(idx._safe_terms("spawning subprocesses")))
check("the indexed text is stems", "spawn" in idx._fts_text(
    {"title": "oom", "desc": "subprocesses were spawned", "prevention": ""}, "s").split()
    and "were" not in idx._fts_text({"title": "", "desc": "subprocesses were spawned"}, "s"))
if idx._fts_ok(idx._connect()):
    hits = idx.iter_candidates("proj", query="spawning subprocesses", limit=1)
    check("the FTS5 prefilter finds the note through another inflection",
          len(hits) == 1 and hits[0][0].endswith("oom"), str([h[0] for h in hits]))
else:
    print("       (sqlite without FTS5 here - prefilter check skipped)")
_alive = m.ollama_alive
m.ollama_alive = lambda timeout_s=4: False
try:
    res = m.retrieve_relevant("proj", "spawning subprocesses", 5)
    check("retrieve_relevant (embedder down, lexical only) returns the hit",
          bool(res) and res[0]["stem"].endswith("oom"), str([r.get("stem") for r in res]))
finally:
    m.ollama_alive = _alive

print("\n- an index built the other way is rebuilt once, not searched -")
con = idx._connect()
con.execute("INSERT OR REPLACE INTO meta VALUES ('lex_format', 'raw')")
con.commit()
con.close()
check("a raw-tokenised index is not ready under morphology", not m.scale_index_ready())
m.ensure_scale_index()
check("ensure_scale_index() rebuilt it and restamped", idx.index_meta().get("lex_format") == "morph1"
      and m.scale_index_ready())
con = idx._connect()
con.execute("DELETE FROM meta WHERE key = 'lex_format'")
con.commit()
con.close()
check("a legacy index without the stamp counts as raw and is rebuilt too",
      not m.scale_index_ready() and (m.ensure_scale_index() or m.scale_index_ready()))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
