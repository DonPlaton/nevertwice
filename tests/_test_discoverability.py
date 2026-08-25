#!/usr/bin/env python3
"""A stranger can install it, wire their agent to it, and file a good issue - without asking.

Discoverability rots quietly. A config block goes stale when a command is renamed, a starter
issue points at a file that no longer exists, a security policy keeps quoting a version the
project passed two releases ago (this one said "pre-1.0 in spirit even at v1.0.0" while
shipping 2.3.0), and a citation file offers a version number to Zenodo and to other people's
bibliographies that stopped being true.

None of those break a test by themselves, so this suite makes them break one:

* **the config blocks are executable truth** - every JSON block parses, every TOML block
  parses, and every command they tell a reader to run is a console script this package
  actually installs;
* **the starter issues are real work** - each names files, and each named file exists;
* **the templates ask what triage needs** - a repro and an environment, not just a title;
* **SECURITY.md is one page** and does not name a version;
* **CITATION.cff agrees with the package**, checked by `tools/check_version.py` so it fails
  in CI rather than in someone's reference list.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import _env_guard  # noqa: F401, E402 - must run before any project import

CONFIGS = ROOT / "docs" / "AGENT_CONFIGS.md"
STARTERS = sorted((ROOT / "docs" / "starter-issues").glob("*.md"))
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
PR_TEMPLATE = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
SECURITY = ROOT / "SECURITY.md"
CITATION = ROOT / "CITATION.cff"

# "One page" as something a machine can hold you to. A printed page is about 500 words.
SECURITY_MAX_LINES = 60
SECURITY_MAX_WORDS = 500

# The three hosts C4 names by name. Others may be documented; these may not be missing.
REQUIRED_HOSTS = ("Claude Code", "Cursor", "Codex")

# Labels a draft may carry. A draft asking for a label nobody will create is not fileable.
KNOWN_LABELS = {"bug", "documentation", "enhancement", "good first issue", "help wanted",
                "research", "question"}

FENCE = re.compile(r"```(\w+)\n(.*?)```", re.S)

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    suffix = f"  [{detail}]" if detail and not condition else ""
    print(("  ok   " if condition else "  FAIL ") + name + suffix)
    PASSED += int(condition)
    FAILED += int(not condition)


def console_scripts() -> set:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = text.split("[project.scripts]", 1)[1].split("\n[", 1)[0]
    return {ln.split("=", 1)[0].strip() for ln in block.splitlines()
            if "=" in ln and not ln.strip().startswith("#")}


def frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    body = text.split("---", 2)[1]
    out = {}
    for line in body.splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip().strip('"')
    return out


def test_the_config_blocks_are_executable_truth() -> None:
    print("\n- every pasteable config parses, and names a real command -")
    check("the configs page exists", CONFIGS.exists())
    if not CONFIGS.exists():
        return
    text = CONFIGS.read_text(encoding="utf-8")
    for host in REQUIRED_HOSTS:
        check(f"{host} has a section", f"## {host}" in text)

    scripts = console_scripts()
    blocks = FENCE.findall(text)
    check("the page carries fenced blocks", len(blocks) >= 5, str(len(blocks)))

    bad_json, commands = [], set()
    for lang, body in blocks:
        if lang != "json":
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            bad_json.append(f"{body.strip()[:40]}: {exc}")
            continue
        for section in ("mcpServers", "context_servers"):
            for entry in data.get(section, {}).values():
                command = entry.get("command")
                if isinstance(command, dict):        # Zed nests it
                    command = command.get("path")
                if command:
                    commands.add(command)
    check("every JSON block parses", not bad_json, "; ".join(bad_json[:2]))
    check("every JSON block names a command", bool(commands), str(commands))

    toml_bodies = [b for lang, b in blocks if lang == "toml"]
    check("a TOML block exists for the Codex config", bool(toml_bodies))
    for body in toml_bodies:
        for match in re.finditer(r'^\s*command\s*=\s*"([^"]+)"', body, re.M):
            commands.add(match.group(1))

    unknown = sorted(c for c in commands if c not in scripts)
    check("every command is a console script this package installs", not unknown,
          f"{unknown} not in {sorted(scripts)[:4]}...")

    # A config page that tells you to paste something and never how to tell it worked is
    # where most integration reports come from.
    check("the page says how to verify it took",
          "nevertwice-stats" in text and "PATH" in text)


def test_the_starter_issues_describe_real_work() -> None:
    print("\n- the starter drafts point at files that exist -")
    check("there are three drafts", len(STARTERS) == 3,
          ", ".join(p.name for p in STARTERS))
    for path in STARTERS:
        name = path.name
        text = path.read_text(encoding="utf-8")
        meta = frontmatter(text)
        check(f"{name} has a title", bool(meta.get("title")))
        labels = {label.strip() for label in meta.get("labels", "").split(",")
                  if label.strip()}
        check(f"{name} carries labels", bool(labels))
        check(f"{name} uses known labels", labels <= KNOWN_LABELS,
              f"unknown: {sorted(labels - KNOWN_LABELS)}")
        check(f"{name} states a closing condition", "## Done when" in text)
        check(f"{name} says where to look", "## Where to look" in text)

        # Only the "Where to look" section: the body legitimately names files the work is
        # supposed to *create* (a results file, a new module), and a draft that could not
        # mention them would be a worse draft.
        where = text.split("## Where to look", 1)[-1].split("\n## ", 1)[0]
        referenced = set(re.findall(r"`?\[?`?(research/[\w./-]+|docs/[\w./-]+"
                                    r"|nevertwice/[\w./-]+|tests/[\w./-]+"
                                    r"|examples/[\w./-]+)", where))
        check(f"{name} names somewhere to start", len(referenced) >= 3, str(referenced))
        missing = sorted(r for r in referenced
                         if not (ROOT / r.rstrip("/.,)")).exists())
        check(f"{name}: every file it sends you to exists", not missing,
              ", ".join(missing))

    check("a contributor is pointed at them",
          "starter-issues" in (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8"))


def test_the_templates_ask_what_triage_needs() -> None:
    print("\n- the issue and PR templates ask the useful questions -")
    bug = TEMPLATES / "bug_report.md"
    feature = TEMPLATES / "feature_request.md"
    for path in (bug, feature, PR_TEMPLATE):
        check(f"{path.name} exists", path.exists())
    if not (bug.exists() and feature.exists() and PR_TEMPLATE.exists()):
        return

    bug_text = bug.read_text(encoding="utf-8").lower()
    for want, label in (("repro", "a reproduction"), ("os:", "the operating system"),
                        ("python", "the Python version")):
        check(f"the bug template asks for {label}", want in bug_text)

    feature_text = feature.read_text(encoding="utf-8").lower()
    check("the feature template asks for the problem, not just the solution",
          "problem" in feature_text or "what are you trying" in feature_text)

    pr_text = PR_TEMPLATE.read_text(encoding="utf-8").lower()
    check("the PR template asks about tests", "test" in pr_text)


def test_the_security_policy_is_one_page_and_not_stale() -> None:
    print("\n- SECURITY.md is a page, and does not quote a version -")
    text = SECURITY.read_text(encoding="utf-8")
    lines, words = len(text.splitlines()), len(text.split())
    check(f"at most {SECURITY_MAX_LINES} lines", lines <= SECURITY_MAX_LINES, str(lines))
    check(f"at most {SECURITY_MAX_WORDS} words", words <= SECURITY_MAX_WORDS, str(words))

    # The exact rot this file had: a hardcoded release number in the support statement.
    versions = re.findall(r"\bv?\d+\.\d+\.\d+\b", text)
    check("no hardcoded release number", not versions, ", ".join(versions))
    check("it names the private reporting path", "security/advisories/new" in text)
    check("it says how to find your version", "__version__" in text or "--version" in text)


def test_the_citation_agrees_with_the_package() -> None:
    print("\n- CITATION.cff is part of the version contract -")
    text = CITATION.read_text(encoding="utf-8")
    for field in ("cff-version", "title", "authors", "version", "date-released",
                  "url", "license"):
        check(f"CITATION.cff declares {field}", re.search(rf"^{field}:", text, re.M)
              is not None)

    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "check_version.py")],
                          cwd=ROOT, capture_output=True, text=True, timeout=300,
                          encoding="utf-8", errors="replace")
    check("the version contract holds", proc.returncode == 0,
          (proc.stdout + proc.stderr).strip()[-300:])
    check("the contract reports on CITATION.cff", "CITATION.cff" in proc.stdout)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except Exception as exc:            # noqa: BLE001 - report, keep going
                FAILED += 1
                print(f"  ERR  {_name}: {type(exc).__name__}: {exc}")
    print(f"\ndiscoverability: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
