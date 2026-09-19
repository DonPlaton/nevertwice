#!/usr/bin/env python3
"""The external held-out set is frozen, external, and usable (M0).

The shipped embedding headline was measured on the owner's private vault, so nobody outside this
machine can check it. `research/embed_universal/heldout/external_heldout_v1.json` is the
replacement, and it is only worth having if three things stay true:

* **frozen** - the bytes match the hash in its manifest, so "the benchmark moved" cannot be
  confused with "the model improved";
* **external** - every record descends from a held-out public domain and none from the store.
  This is the property the whole set exists for, and the one nobody would notice breaking;
* **usable** - the answer keys point inside the corpus, the two query shapes are genuinely
  different, and both languages the product serves are represented.

Run:  python tests/_test_heldout_set.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before any project import

SET_DIR = ROOT / "research" / "embed_universal" / "heldout"
FROZEN = SET_DIR / "external_heldout_v1.json"
MANIFEST = SET_DIR / "MANIFEST.json"
BUILDER = ROOT / "research" / "embed_universal" / "heldout_set.py"

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


DATA = json.loads(FROZEN.read_text(encoding="utf-8")) if FROZEN.is_file() else {}
META = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {}


def test_the_set_is_committed_and_frozen() -> None:
    print("\n- frozen -")
    check("the set is committed", FROZEN.is_file())
    check("its manifest is committed", MANIFEST.is_file())
    proc = subprocess.run([sys.executable, str(BUILDER), "--verify"], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=300, check=False)
    check("the builder's own verification passes", proc.returncode == 0,
          (proc.stdout + proc.stderr)[-400:])
    check("verification needs nothing outside the repository",
          "data/lessons.jsonl" not in proc.stderr)


def test_nothing_here_came_from_the_owners_store() -> None:
    """The property the set exists for. Everything else is detail."""
    print("\n- external -")
    domains = set(DATA.get("heldout_domains", []))
    check("the held-out domains are the public ones",
          domains == {"rust-book", "arxiv:quant-ph", "book:132"}, str(sorted(domains)))
    stray = {d["domain"] for d in DATA.get("corpus", [])} - domains
    check("every corpus document is from a held-out domain", not stray, str(stray))
    stray = {r["domain"] for r in DATA.get("axes", {}).get("twin", [])} - domains
    check("every twin pair is from a held-out domain", not stray, str(stray))
    for axis in ("retrieval_title", "retrieval_situation"):
        stray = {q["domain"] for q in DATA.get("axes", {}).get(axis, [])} - domains
        check(f"every {axis} query is from a held-out domain", not stray, str(stray))


def test_the_answer_keys_are_usable() -> None:
    print("\n- usable -")
    corpus = DATA.get("corpus", [])
    size = len(corpus)
    check("the corpus is not trivially small", size >= 300, str(size))
    check("document ids are the corpus indices",
          all(d["doc_id"] == i for i, d in enumerate(corpus)))
    for axis in ("retrieval_title", "retrieval_situation"):
        queries = DATA["axes"][axis]
        check(f"{axis} has queries", len(queries) > 0, str(len(queries)))
        check(f"{axis} answer keys all point inside the corpus",
              all(0 <= q["gold"] < size for q in queries))
        check(f"{axis} query ids are dense", [q["qid"] for q in queries] == list(range(len(queries))))
    twin = DATA["axes"]["twin"]
    check("the twin axis has both labels",
          {r["label"] for r in twin} == {0, 1}, str({r["label"] for r in twin}))
    positives = sum(1 for r in twin if r["label"] == 1)
    check("the twin axis is imbalanced towards negatives, as the real task is",
          0 < positives < len(twin) / 2, f"{positives}/{len(twin)}")


def test_the_two_query_shapes_are_actually_different() -> None:
    """A second shape that restates the first measures nothing twice."""
    print("\n- the shapes differ -")
    corpus = {d["doc_id"]: d["text"] for d in DATA["corpus"]}
    titles = {q["gold"]: q["query"] for q in DATA["axes"]["retrieval_title"]}
    situations = DATA["axes"]["retrieval_situation"]
    restated = [q for q in situations
                if q["query"].strip().lower() == titles.get(q["gold"], "").strip().lower()]
    check("no situation query merely restates its title", not restated, str(len(restated)))
    inside = [q for q in situations if q["query"].strip() and q["query"].strip() in corpus[q["gold"]]]
    check("a situation query is not a substring of the document it must find",
          len(inside) < len(situations) * 0.1, f"{len(inside)}/{len(situations)}")
    verbatim = [q for q in DATA["axes"]["retrieval_title"]
                if q["query"].strip() and corpus[q["gold"]].startswith(q["query"].strip())]
    check("the title shape IS near-verbatim, which is why it is the easy control",
          len(verbatim) > len(DATA["axes"]["retrieval_title"]) * 0.8,
          f"{len(verbatim)}/{len(DATA['axes']['retrieval_title'])}")


def test_both_languages_the_product_serves_are_present() -> None:
    print("\n- bilingual -")
    languages = META.get("corpus_languages", {})
    check("Russian is represented", languages.get("ru", 0) >= 50, str(languages))
    check("English is represented", languages.get("en", 0) >= 50, str(languages))
    check("neither language is a token presence",
          min(languages.get("ru", 0), languages.get("en", 0))
          >= 0.2 * sum(languages.values()), str(languages))


def test_the_manifest_records_how_it_was_made() -> None:
    print("\n- provenance -")
    check("the manifest names the file it describes",
          META.get("file", "").endswith("external_heldout_v1.json"))
    check("it carries a content hash", len(META.get("sha256", "")) == 64)
    check("it hashes the generators that wrote the corpus",
          all(META.get("generator_sha256", {}).get(name)
              for name in ("gen_corpus.py", "gen_pairs.py", "sources.py")))
    check("it states plainly what cannot be regenerated from a fresh clone",
          "not reproducible from a fresh clone" in META.get("regeneration", "").lower())
    check("it names the command that checks it",
          "--verify" in META.get("verify_with", ""))


def test_zz_every_check_passed() -> None:
    """Bare pytest must reach the same verdict as this suite's exit code.

    Without this, `python -m pytest <this file>` collects the checks above, runs them,
    and reports them passed while `check()` printed FAIL and the script would exit 1.
    Enforced for every counting suite by `tests/_test_the_harness_agrees_with_itself.py`.
    """
    assert FAILED == 0, f"{FAILED} check(s) failed - see the FAIL lines above"


def main() -> int:
    for fn in (test_the_set_is_committed_and_frozen,
               test_nothing_here_came_from_the_owners_store,
               test_the_answer_keys_are_usable,
               test_the_two_query_shapes_are_actually_different,
               test_both_languages_the_product_serves_are_present,
               test_the_manifest_records_how_it_was_made):
        fn()
    print(f"\nexternal held-out set: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
