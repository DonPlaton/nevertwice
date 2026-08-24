#!/usr/bin/env python3
"""The version contract enforced by tools/check_version.py.

`_test_memory_hook.py` already asserts pyproject == config.VERSION == mcp. This
suite covers the two places a running process cannot see - the git tag and the
built distribution's metadata - by driving the checker's pure functions with
synthetic inputs, and asserts the real repository satisfies the contract too.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401, E402 - must run before package imports

import check_version as cv  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    print(("  ok   " if condition else "  FAIL ") + name + (f"  [{detail}]" if detail and not condition else ""))
    PASSED += int(condition)
    FAILED += int(not condition)


# A wheel's METADATA as setuptools>=77 emits it for this project.
GOOD_META = """Metadata-Version: 2.4
Name: nevertwice
Version: 2.3.0
Summary: Proactive, local-first memory for AI coding agents
License-Expression: MIT
License-File: LICENSE
Classifier: Development Status :: 4 - Beta
Requires-Python: >=3.10

Long description follows, and it mentions Version: 9.9.9 in prose.
"""


def _meta(**overrides: str) -> str:
    """GOOD_META with header lines replaced or removed (value None removes)."""
    out = []
    for line in GOOD_META.splitlines():
        key = line.partition(": ")[0]
        if key in overrides:
            value = overrides.pop(key)
            if value is not None:
                out.append(f"{key}: {value}")
            continue
        out.append(line)
    out = [f"{k}: {v}" for k, v in overrides.items() if v is not None] + out
    return "\n".join(out)


def test_pyproject_version_is_parsed() -> None:
    print("\n- pyproject version parsing -")
    text = '[project]\nname = "x"\nversion = "1.2.3"\ndescription = "y"\n'
    check("reads the declared version", cv.read_pyproject_version(text) == "1.2.3")
    try:
        cv.read_pyproject_version('[project]\nname = "x"\n')
        check("missing version raises", False)
    except ValueError:
        check("missing version raises", True)
    # A version line belonging to another table must not be mistaken for the
    # project's: only a line starting at column 0 counts.
    nested = '[project]\nversion = "1.2.3"\n[tool.x]\n  version = "9.9.9"\n'
    check("indented version lines are ignored",
          cv.read_pyproject_version(nested) == "1.2.3")


def test_version_syntax() -> None:
    print("\n- release version syntax -")
    for good in ("2.3.0", "2.3.0rc1", "10.0.1.post1", "2.4.0.dev1"):
        check(f"accepts {good}", cv.check_version_syntax(good) == [])
    for bad in ("v2.3.0", "2.3", "2.3.0+local", "2.3.0-rc1", ""):
        check(f"rejects {bad!r}", cv.check_version_syntax(bad) != [])


def test_runtime_drift_is_caught() -> None:
    print("\n- runtime / mcp drift -")
    check("agreement passes", cv.check_runtime("2.3.0", "2.3.0", "2.3.0") == [])
    check("stale __version__ fails",
          len(cv.check_runtime("2.3.0", "2.2.1", "2.3.0")) == 1)
    check("stale SERVER_VERSION fails",
          len(cv.check_runtime("2.3.0", "2.3.0", "2.2.1")) == 1)
    check("both stale reports both",
          len(cv.check_runtime("2.3.0", "2.2.1", "2.2.1")) == 2)


def test_tag_contract() -> None:
    print("\n- git tag contract -")
    errs, notes = cv.check_tags("2.3.0", ["v2.3.0"], ["v2.2.1", "v2.3.0"])
    check("matching tag at HEAD passes", errs == [] and notes == [])

    errs, _ = cv.check_tags("2.3.0", ["v2.2.1"], ["v2.2.1"])
    check("mismatched tag at HEAD fails", len(errs) == 1)

    errs, _ = cv.check_tags("2.3.0", ["v2.3.0", "v2.3.1"], ["v2.3.0", "v2.3.1"])
    check("two release tags at HEAD fail", len(errs) == 1)

    errs, _ = cv.check_tags("2.3.0", ["nightly"], ["nightly"])
    check("a non-release tag at HEAD is ignored", errs == [])

    errs, notes = cv.check_tags("2.3.0", [], ["v2.3.0"])
    check("untagged commit on a released version is a note, not an error",
          errs == [] and len(notes) == 1)
    check("the note names the already-published version",
          bool(notes) and "v2.3.0" in notes[0])

    errs, _ = cv.check_tags("2.4.0", [], ["v2.3.0"], require_tag=True)
    check("release mode demands a tag on HEAD", len(errs) == 1)

    errs, _ = cv.check_tags("2.3.0", [], ["v2.3.0"], require_tag=True)
    check("release mode rejects re-releasing an existing tag", len(errs) == 1)

    errs, _ = cv.check_tags("2.4.0", ["v2.4.0"], ["v2.3.0", "v2.4.0"],
                            require_tag=True)
    check("release mode passes on a correctly tagged HEAD", errs == [])


def test_metadata_header_parsing() -> None:
    print("\n- metadata header parsing -")
    fields = cv._metadata_fields(GOOD_META)
    check("collects repeated keys", fields["Classifier"] == ["Development Status :: 4 - Beta"])
    check("stops at the description body", fields["Version"] == ["2.3.0"])
    folded = "Name: nevertwice\nSummary: one\n  continued\nVersion: 2.3.0\n\nbody\n"
    check("folded continuations are not parsed as fields",
          cv._metadata_fields(folded).get("Version") == ["2.3.0"])


def test_dist_metadata_contract() -> None:
    print("\n- built distribution metadata -")
    whl = ("nevertwice-2.3.0-py3-none-any.whl", GOOD_META)
    sdist = ("nevertwice-2.3.0.tar.gz", GOOD_META)

    check("a correct pair passes", cv.check_dist("2.3.0", [whl, sdist]) == [])
    check("no artifacts at all fails", cv.check_dist("2.3.0", []) != [])
    check("a wheel with no sdist fails", cv.check_dist("2.3.0", [whl]) != [])
    check("an sdist with no wheel fails", cv.check_dist("2.3.0", [sdist]) != [])

    errs = cv.check_dist("2.4.0", [("nevertwice-2.4.0-py3-none-any.whl", GOOD_META),
                                   ("nevertwice-2.4.0.tar.gz", GOOD_META)])
    check("metadata Version behind the declared version fails",
          any("metadata Version" in e for e in errs))

    errs = cv.check_dist("2.3.0", [("nevertwice-2.2.1-py3-none-any.whl", GOOD_META),
                                   sdist])
    check("a stale artifact filename fails",
          any("filename does not start with" in e for e in errs))

    errs = cv.check_dist("2.3.0", [(whl[0], _meta(**{"License-Expression": None})), sdist])
    check("a missing SPDX License-Expression fails",
          any("License-Expression" in e for e in errs))

    errs = cv.check_dist("2.3.0", [(whl[0], _meta(**{"License-Expression": "Apache-2.0"})),
                                   sdist])
    check("the wrong SPDX expression fails",
          any("License-Expression" in e for e in errs))

    errs = cv.check_dist("2.3.0", [(whl[0], _meta(**{"License-File": None})), sdist])
    check("a missing License-File fails", any("License-File" in e for e in errs))

    stale = GOOD_META.replace("Classifier: Development Status :: 4 - Beta",
                              "Classifier: License :: OSI Approved :: MIT License")
    errs = cv.check_dist("2.3.0", [(whl[0], stale), sdist])
    check("the deprecated license classifier fails",
          any("deprecated license classifier" in e for e in errs))

    errs = cv.check_dist("2.3.0", [(whl[0], _meta(Name="nevertwice-memory")), sdist])
    check("the wrong distribution name fails", any("metadata Name" in e for e in errs))


def test_this_repository_satisfies_the_contract() -> None:
    print("\n- the repository itself -")
    import config as cfg

    version = cv.read_pyproject_version(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    check("pyproject version is a releasable string",
          cv.check_version_syntax(version) == [], version)
    check("config.VERSION matches pyproject", cfg.VERSION == version,
          f"{cfg.VERSION} vs {version}")
    at_head, all_tags = cv.git_tags()
    errs, _ = cv.check_tags(version, at_head, all_tags)
    check("the checked-out commit satisfies the tag contract", errs == [],
          "; ".join(errs))


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\nversion contract: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
