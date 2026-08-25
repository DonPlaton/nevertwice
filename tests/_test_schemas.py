#!/usr/bin/env python3
"""The declared boundaries describe what the code actually passes.

`nevertwice/schemas.py` is worth nothing if it is a wish. A shape file that drifts from the
code is worse than none, because the next reader trusts it and writes against a key that has
not existed for two releases.

So this suite does not check the declarations against themselves. It **drives the real
engine** on a throwaway store - write a lesson, distil a guard, recall it, fire the guard - and
requires every value that crosses a seam to conform to the shape declared for that seam. When
the engine changes a key, this goes red on the same commit, which is the only thing that keeps
a contract file honest.

It also pins the one rename that has caused bugs: `NoteMeta.desc` becomes
`RetrievalHit.description` at the public boundary, and nowhere is that written except here.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

sys.path.insert(0, str(ROOT / "nevertwice"))
import api            # noqa: E402
import guards as G    # noqa: E402
import schemas        # noqa: E402

LESSON = {
    "type": "mistake", "title": "sql-built-by-fstring",
    "description": "A filter was interpolated into the SQL string - an injection hole.",
    "prevention": "Never build SQL by f-string - pass values as query parameters.",
    "entities": ["database", "security"],
}
REPEAT = "cursor.execute(f\"SELECT * FROM users WHERE name = '{name}'\")"

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def test_the_validator_does_its_job() -> None:
    print("\n- conforms() catches what it claims to -")
    good = {"id": "g-1", "message": "past mistake: ...", "status": "advisory"}
    check("a conforming value has no problems", schemas.conforms(good, "Intervention") == [])

    missing = schemas.conforms({"status": "advisory"}, "Intervention")
    check("a missing required key is reported",
          any("missing required key 'id'" in p for p in missing), str(missing))
    check("every missing key is reported, not just the first", len(missing) >= 2, str(missing))

    unknown = schemas.conforms({**good, "colour": "blue"}, "Intervention")
    check("an unknown key is reported", any("unknown key 'colour'" in p for p in unknown),
          str(unknown))

    wrong = schemas.conforms({**good, "scope": "everything"}, "Intervention")
    check("a wrong type is reported", any("'scope' is str" in p for p in wrong), str(wrong))

    check("a bool is not accepted as an int",
          schemas.conforms({"ntype": "mistake", "title": "t", "stem": "s",
                            "recurrence": True}, "NoteMeta") != [])
    check("a non-dict is rejected", schemas.conforms(["not", "a", "dict"], "NoteMeta") != [])
    check("an unknown shape name is rejected", schemas.conforms({}, "Nonsense") != [])


def test_the_engine_produces_what_the_file_declares() -> None:
    """The characterization run: real values, from the real write and read paths."""
    print("\n- the real engine's values conform -")
    stems = api.remember_lessons([LESSON], project="schemaproj", embed=False)
    check("a lesson was written", bool(stems), str(stems))

    # `recall` is the semantic path and, with no embedder and a one-note store, the
    # calibrated abstention gate correctly returns nothing rather than a confident wrong
    # answer. So the public shape is characterized from the faceted path, which is
    # documented to return "recall-shaped dicts" and works with no embedder - and any hit
    # `recall` does produce is held to the same shape.
    hits = api.notes_for_entity("database", "schemaproj")
    check("the faceted read returned something", bool(hits), str(hits)[:200])
    bad = [p for hit in hits for p in schemas.conforms(hit, "RetrievalHit")]
    check("every public hit conforms to RetrievalHit", not bad, "; ".join(bad[:4]))
    semantic = api.recall("sql injection f-string", project="schemaproj", k=5)
    bad = [p for hit in semantic for p in schemas.conforms(hit, "RetrievalHit")]
    check("recall hits, when there are any, use the same shape", not bad,
          "; ".join(bad[:4]))

    G.generate_from_vault("schemaproj", min_recurrence=1, use_llm=False)
    fired = api.guards_check(REPEAT, project="schemaproj")
    check("a guard fired", bool(fired), str(fired)[:200])
    bad = [p for hit in fired for p in schemas.conforms(hit, "Intervention")]
    check("every guard hit conforms to Intervention", not bad, "; ".join(bad[:4]))

    # What the values look like, from the values themselves - the check that keeps the
    # declaration describing production rather than intent.
    shape = schemas.characterize(hits)
    check("recall hits always carry the identity keys",
          set(schemas.REQUIRED["RetrievalHit"]) <= set(shape["always"]),
          f"always={shape['always']}")
    check("characterize() reports the types it saw",
          all(isinstance(v, list) and v for v in shape["types"].values()),
          str(shape["types"])[:200])
    check("characterize() on nothing does not explode",
          schemas.characterize([])["n"] == 0)


def test_the_rename_at_the_public_boundary_is_pinned() -> None:
    """`desc` inside, `description` outside. Undocumented, and the source of real bugs."""
    print("\n- the one rename that crosses the public seam -")
    check("NoteMeta carries desc", "desc" in schemas.NoteMeta.__annotations__)
    check("RetrievalHit carries description",
          "description" in schemas.RetrievalHit.__annotations__)
    check("RetrievalHit does not also carry desc",
          "desc" not in schemas.RetrievalHit.__annotations__)
    check("NoteMeta does not carry description",
          "description" not in schemas.NoteMeta.__annotations__)

    hits = api.notes_for_entity("database", "schemaproj")
    if hits:
        check("a real hit uses the outside name", "description" in hits[0],
              str(sorted(hits[0]))[:160])
        check("a real hit does not leak the inside name", "desc" not in hits[0])


def test_every_declared_shape_is_reachable() -> None:
    print("\n- the file declares seven boundaries, and no orphans -")
    declared = set(schemas.REQUIRED)
    check("seven shapes are declared", len(declared) == 7, str(sorted(declared)))
    exported = {n for n in schemas.__all__ if n[0].isupper() and n != "REQUIRED"}
    check("every exported shape has a required-key entry", declared == exported,
          str(sorted(declared ^ exported)))
    for shape in sorted(declared):
        check(f"{shape} declares at least one field",
              bool(getattr(schemas, shape).__annotations__))

    text = (ROOT / "nevertwice" / "schemas.py").read_text(encoding="utf-8")
    check("the module takes no third-party dependency",
          "import pydantic" not in text and "import attrs" not in text)
    check("the module imports nothing from the engine",
          "import memory_hook" not in text and "import api" not in text)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\nschemas: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
