#!/usr/bin/env python3
"""Tests for the cloud embedding backend (#31) and embedder-free lexical recall
(#32). Fully offline: every network call is mocked, so no Ollama, no cloud key, and
no real vault are needed. The live cloud paths are runtime-blocked in this env (no
keys) - these prove the request/response wiring and the self-invalidation logic the
runtime would exercise once a key is present."""
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nevertwice"))
import memory_hook as m
import memory_search as ms
import index_sqlite as idx


class _Resp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen_returning(payload, capture=None):
    def _f(req, timeout=None):
        if capture is not None:
            capture["url"] = req.full_url
            capture["headers"] = dict(req.headers)
            capture["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(payload)
    return _f


# ── #31 signature / self-invalidation ────────────────────────────────────────────

def test_signature_format():
    with mock.patch.object(m, "EMBED_PROVIDER", "openai"), \
         mock.patch.object(m, "EMBED_MODEL", "text-embedding-3-small"):
        assert m.embed_signature() == "openai:text-embedding-3-small"


def test_sig_current_legacy_bare_is_ollama():
    # a pre-provider cache stamped just "bge-m3" must keep working without a rebuild
    with mock.patch.object(m, "EMBED_PROVIDER", "ollama"), \
         mock.patch.object(m, "EMBED_MODEL", "bge-m3"):
        assert m._embed_sig_current("bge-m3") is True
        assert m._embed_sig_current("ollama:bge-m3") is True
        assert m._embed_sig_current(None) is True          # unstamped legacy → current
        assert m._embed_sig_current("openai:x") is False   # provider switch → stale


def test_embed_cache_usable_tracks_meta():
    with mock.patch.object(m, "EMBED_PROVIDER", "openai"), \
         mock.patch.object(m, "EMBED_MODEL", "text-embedding-3-small"):
        with mock.patch.object(m, "load_embed_meta",
                               return_value={"model": "openai:text-embedding-3-small"}):
            assert m.embed_cache_usable() is True
        with mock.patch.object(m, "load_embed_meta", return_value={"model": "ollama:bge-m3"}):
            assert m.embed_cache_usable() is False          # stale provider → abstain
        with mock.patch.object(m, "load_embed_meta", return_value={}):
            assert m.embed_cache_usable() is True            # fresh store → usable


# ── #31 embedder availability ─────────────────────────────────────────────────────

def test_embedder_available_ollama_pings():
    with mock.patch.object(m, "EMBED_PROVIDER", "ollama"), \
         mock.patch.object(m, "ollama_alive", return_value=True) as ping:
        assert m.embedder_available(2) is True
        ping.assert_called_once()


def test_embedder_available_cloud_needs_key():
    with mock.patch.object(m, "EMBED_PROVIDER", "openai"), \
         mock.patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("OPENAI_API_KEY", None)
        assert m.embedder_available() is False
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            assert m.embedder_available() is True


# ── #31 provider request/response wiring ──────────────────────────────────────────

def test_embed_openai_compatible_parses():
    cap = {}
    with mock.patch.object(m, "EMBED_PROVIDER", "openai"), \
         mock.patch.object(m, "EMBED_MODEL", "text-embedding-3-small"), \
         mock.patch.object(m, "EMBED_BASE_URL", ""), \
         mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}), \
         mock.patch("urllib.request.urlopen",
                    _urlopen_returning({"data": [{"embedding": [0.1, 0.2, 0.3]}]}, cap)):
        v = m.embed_text("hello world")
    assert v == [0.1, 0.2, 0.3]
    assert cap["url"] == "https://api.openai.com/v1/embeddings"
    assert cap["headers"]["Authorization"] == "Bearer sk-test"
    assert cap["body"] == {"model": "text-embedding-3-small", "input": "hello world"}


def test_embed_custom_base_url():
    cap = {}
    with mock.patch.object(m, "EMBED_PROVIDER", "openai"), \
         mock.patch.object(m, "EMBED_BASE_URL", "https://api.deepinfra.com/v1/openai/embeddings"), \
         mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-x"}), \
         mock.patch("urllib.request.urlopen",
                    _urlopen_returning({"data": [{"embedding": [1.0]}]}, cap)):
        assert m.embed_text("x") == [1.0]
    assert cap["url"] == "https://api.deepinfra.com/v1/openai/embeddings"


def test_embed_voyage_parses():
    with mock.patch.object(m, "EMBED_PROVIDER", "voyage"), \
         mock.patch.object(m, "EMBED_MODEL", "voyage-3"), \
         mock.patch.dict("os.environ", {"VOYAGE_API_KEY": "pa-test"}), \
         mock.patch("urllib.request.urlopen",
                    _urlopen_returning({"data": [{"embedding": [0.5, 0.6]}]})):
        assert m.embed_text("doc") == [0.5, 0.6]


def test_embed_gemini_parses():
    cap = {}
    with mock.patch.object(m, "EMBED_PROVIDER", "gemini"), \
         mock.patch.object(m, "EMBED_MODEL", "gemini-embedding-001"), \
         mock.patch.dict("os.environ", {"GEMINI_API_KEY": "g-test"}), \
         mock.patch("urllib.request.urlopen",
                    _urlopen_returning({"embedding": {"values": [0.7, 0.8, 0.9]}}, cap)):
        v = m.embed_text("hi")
    assert v == [0.7, 0.8, 0.9]
    assert "gemini-embedding-001:embedContent" in cap["url"]
    assert cap["headers"]["X-goog-api-key"] == "g-test"


def test_embed_cohere_parses_and_sets_input_type():
    cap = {}
    with mock.patch.object(m, "EMBED_PROVIDER", "cohere"), \
         mock.patch.object(m, "EMBED_MODEL", "embed-v4.0"), \
         mock.patch.dict("os.environ", {"COHERE_API_KEY": "co-test"}), \
         mock.patch("urllib.request.urlopen",
                    _urlopen_returning({"embeddings": {"float": [[0.1, 0.2]]}}, cap)):
        # query side → search_query; document side → search_document
        assert m.embed_text("q", kind="query") == [0.1, 0.2]
        assert cap["body"]["input_type"] == "search_query"
        m.embed_text("d", kind="document")
        assert cap["body"]["input_type"] == "search_document"


def test_embed_cloud_no_key_returns_none():
    import os
    with mock.patch.object(m, "EMBED_PROVIDER", "openai"), \
         mock.patch.dict("os.environ", {}, clear=False):
        os.environ.pop("OPENAI_API_KEY", None)
        assert m.embed_text("hello") is None       # runtime-blocked, honestly None


def test_embed_cloud_http_error_returns_none():
    def _boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
    with mock.patch.object(m, "EMBED_PROVIDER", "openai"), \
         mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-bad"}), \
         mock.patch("urllib.request.urlopen", _boom):
        assert m.embed_text("hello") is None        # falls back to lexical, never crashes


def test_embed_ollama_still_default():
    cap = {}
    with mock.patch.object(m, "EMBED_PROVIDER", "ollama"), \
         mock.patch.object(m, "EMBED_MODEL", "bge-m3"), \
         mock.patch("urllib.request.urlopen",
                    _urlopen_returning({"embeddings": [[0.3, 0.4]]}, cap)):
        assert m.embed_text("x") == [0.3, 0.4]
    assert cap["url"] == m.OLLAMA_EMBED_URL          # local path unchanged


# ── #32 lexical recall without an embedder ────────────────────────────────────────

def _seed_textonly_vault(tmp):
    """A vault whose embed cache holds ONLY text-only entries (no vectors) - the
    state of a store where no embedder ever ran."""
    cache = {
        "2026-06-18-proj-pattern-gpu-leak": {
            "ntype": "pattern", "project": "proj",
            "title": "GPU memory leak after each epoch",
            "desc": "call torch.cuda.empty_cache() between epochs", "prevention": "",
            "recurrence": 1},
        "2026-06-18-proj-mistake-batch": {
            "ntype": "mistake", "project": "proj", "title": "Batch size too large",
            "desc": "OOM on 32GB card", "prevention": "halve the batch", "recurrence": 1},
    }
    with mock.patch.object(m, "EMBED_CACHE", tmp / ".embeddings_cache.json"), \
         mock.patch.object(m, "EMBED_META", tmp / ".embeddings_meta.json"):
        m.save_embed_cache(cache)
    return cache


def test_index_indexes_text_only_notes_for_fts():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cache = _seed_textonly_vault(tmp)
        with mock.patch.object(m, "VAULT", tmp), \
             mock.patch.object(m, "EMBED_CACHE", tmp / ".embeddings_cache.json"), \
             mock.patch.object(m, "EMBED_META", tmp / ".embeddings_meta.json"):
            n = idx.build()
            assert n == len(cache)              # text-only rows are indexed, not skipped
            con = idx._connect()
            try:
                if idx._has_fts(con):
                    cnt = con.execute("SELECT COUNT(*) FROM notes_fts").fetchone()[0]
                    assert cnt == len(cache)    # FTS populated despite zero vectors
            finally:
                con.close()
            # the embedder is unavailable → search must use the FTS lexical path
            with mock.patch.object(m, "embedder_available", return_value=False):
                hits, mode = idx.search("memory leak", "proj", 5)
            assert mode == "lexical(fts)" or "lexical" in mode
            assert any("leak" in (h.get("title", "").lower()) for h in hits)


def test_search_core_lexical_fallback_no_embedder():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cache = _seed_textonly_vault(tmp)
        # force the JSON-cache token-overlap branch (no SQLite index in this test)
        with mock.patch.object(m, "load_embed_cache", return_value=cache), \
             mock.patch.object(m, "_scale_index", return_value=None):
            results, mode = ms.search_core("gpu memory leak", "proj", k=5)
        assert mode == "lexical (no embedder)"
        assert results and results[0]["low_confidence"] is True
        assert any("leak" in r["title"].lower() for r in results)


def test_search_core_empty_when_no_notes_at_all():
    with mock.patch.object(m, "load_embed_cache", return_value={}), \
         mock.patch.object(m, "_scale_index", return_value=None):
        results, mode = ms.search_core("anything", "proj", k=5)
    assert results == [] and mode == "empty"


def test_update_embeddings_stores_text_only_when_no_embedder():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with mock.patch.object(m, "EMBED_CACHE", tmp / ".embeddings_cache.json"), \
             mock.patch.object(m, "EMBED_META", tmp / ".embeddings_meta.json"), \
             mock.patch.object(m, "VAULT", tmp), \
             mock.patch.object(m, "embed_text", return_value=None), \
             mock.patch.object(m, "_note_resolved", return_value=False), \
             mock.patch.object(m, "sync_scale_index"):
            m.update_embeddings([("2026-06-18-proj-pattern-x", "pattern", "proj",
                                  "Title here", "a description", "")])
            cache = m.load_embed_cache()
        assert "2026-06-18-proj-pattern-x" in cache
        e = cache["2026-06-18-proj-pattern-x"]
        assert "vec" not in e                    # text-only - no vector
        assert e["title"] == "Title here" and e["desc"] == "a description"


def test_update_embeddings_demotes_stale_vectors_on_provider_switch():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # an old cache from a different embedder
        old = {"old-note": {"ntype": "pattern", "project": "proj", "title": "Old",
                            "desc": "x", "prevention": "", "recurrence": 1,
                            "vec": [0.1, 0.2, 0.3]}}
        with mock.patch.object(m, "EMBED_CACHE", tmp / ".embeddings_cache.json"), \
             mock.patch.object(m, "EMBED_META", tmp / ".embeddings_meta.json"), \
             mock.patch.object(m, "VAULT", tmp), \
             mock.patch.object(m, "_note_resolved", return_value=False), \
             mock.patch.object(m, "sync_scale_index"):
            m.save_embed_cache(old)
            m.save_embed_meta({"model": "ollama:bge-m3"})
            # now the live embedder is a different provider, and it returns a vector
            with mock.patch.object(m, "EMBED_PROVIDER", "openai"), \
                 mock.patch.object(m, "EMBED_MODEL", "text-embedding-3-small"), \
                 mock.patch.object(m, "embed_text", return_value=[0.9, 0.8]):
                m.update_embeddings([("new-note", "pattern", "proj", "New", "y", "")])
                cache = m.load_embed_cache()
        assert "vec" not in cache["old-note"]     # stale vector demoted to text-only
        assert cache["new-note"]["vec"] == [0.9, 0.8]   # new vector kept (no space-mix)


# ── audit 2026-06-18 fixes: privacy gate, SSRF, log scrub, mixed-store visibility ──

def test_embed_text_skips_cloud_for_local_only_project():
    # CRIT privacy regression fix: a local-only project must NEVER be sent to a cloud embedder
    with mock.patch.object(m, "EMBED_PROVIDER", "openai"), \
         mock.patch.object(m, "LOCAL_ONLY_PROJECTS", {"secret"}), \
         mock.patch.object(m, "CLOUD_ONLY_PROJECTS", set()), \
         mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-x"}), \
         mock.patch("urllib.request.urlopen") as uo:
        assert m.embed_text("secret note text", project="secret") is None
        uo.assert_not_called()                 # nothing left the machine
    # a non-local-only project still embeds normally
    with mock.patch.object(m, "EMBED_PROVIDER", "openai"), \
         mock.patch.object(m, "LOCAL_ONLY_PROJECTS", {"secret"}), \
         mock.patch.object(m, "CLOUD_ONLY_PROJECTS", set()), \
         mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-x"}), \
         mock.patch("urllib.request.urlopen",
                    _urlopen_returning({"data": [{"embedding": [0.1]}]})):
        assert m.embed_text("public note", project="public") == [0.1]


def test_safe_model_seg_blocks_url_injection():
    assert m._safe_model_seg("gemini-embedding-001") == "gemini-embedding-001"
    for bad in ("../../etc/passwd", "model?key=leak", "a@evil.com", "x/../../internal"):
        seg = m._safe_model_seg(bad)
        assert not any(c in seg for c in "/?#@")


def test_scrub_for_log_redacts_bearer_token():
    s = m._scrub_for_log('{"error":"invalid: Bearer sk-secret12345 rejected"}')
    assert "sk-secret12345" not in s and "redacted" in s.lower()


def test_search_core_surfaces_text_only_in_mixed_store():
    # CRIT: one embedded note + one text-only note. With the embedder UP the semantic
    # path runs over the embedded note, but the text-only note must STILL be reachable
    # (it used to be invisible whenever the store held any embedded note).
    cache = {
        "vec-note": {"ntype": "pattern", "project": "proj", "title": "async retry backoff",
                     "desc": "x", "prevention": "", "recurrence": 1, "vec": [0.1, 0.2, 0.3]},
        "text-note": {"ntype": "mistake", "project": "proj",
                      "title": "forgot to close the db pool", "desc": "connection leak",
                      "prevention": "use a context manager", "recurrence": 1},
    }
    with mock.patch.object(m, "load_embed_cache", return_value=cache), \
         mock.patch.object(m, "embed_cache_usable", return_value=True), \
         mock.patch.object(m, "embedder_available", return_value=True), \
         mock.patch.object(m, "embed_text", return_value=[0.1, 0.2, 0.3]), \
         mock.patch.object(m, "_scale_index", return_value=None):
        results, mode = ms.search_core("db pool connection leak", "proj", k=5)
    stems = [r["stem"] for r in results]
    assert "text-note" in stems                # the text-only note is NOT invisible anymore
    tn = next(r for r in results if r["stem"] == "text-note")
    assert tn["low_confidence"] is True        # flagged as the weaker lexical signal


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
