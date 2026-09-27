#!/usr/bin/env python3
"""PREREG-V3 TB3(d) (A5): research/v3/ablation_c1.py - the gate-off ablation, no product diff (the auditor's A5 rulings).

* Q1: the variant is the shipped template with exactly two fragments cut - the trivial-session sentence and the
  project_relevant block (with its one blank separator line) - and not one other byte changed; "All tags lowercase.
  Empty categories = []." stays; the published diff (research/v3/ablation_prompt_c1.diff) is the diff;
* Q3/K67: both templates are pinned by sha256 over the in-memory string; the module's own sha256 is in the record (Q2);
* derive() refuses a fragment that is missing or doubled; enable() refuses a moved template or EXTRACT_RETRY != 0;
* without enable() the engine is untouched (the default path unchanged by construction);
* Q2 (i): through api.capture_session, a session the shipped gate refuses is NOT written by default and IS written under
  the ablation - the prompt the extractor received carries neither cut fragment - and gate_would_refuse counts it (Q5);
* Q2 (ii): every reference to _is_relevant and EXTRACTION_PROMPT in nevertwice/, found by AST, is exactly the recorded
  set - a new by-name call site (or an import-from that binds its own copy) would bypass the rebinding, and turns red.

    python tests/research/_test_v3_ablation_c1.py
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import importlib.util
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402
import api  # noqa: E402
from _sandbox import make_sandbox  # noqa: E402

PASSED = FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SHIPPED = m.EXTRACTION_PROMPT  # taken BEFORE the ablation module is imported: its import must change nothing
GATE = m._is_relevant
A = _load("v3_ablation_c1", ROOT / "research" / "v3" / "ablation_c1.py")


def refused(fn, words: str) -> bool:
    try:
        fn()
    except A.AblationRefused as e:
        return words in str(e)
    except Exception:  # noqa: BLE001
        return False
    return False


print("\n- the pins (K67, Q3) -")
check("the shipped template's sha256 over the in-memory string is the pinned one", A.sha(SHIPPED) == A.SHIPPED_TEMPLATE_SHA256,
      A.sha(SHIPPED))
variant = A.derive(SHIPPED)
check("the derived variant's sha256 is the pinned one", A.sha(variant) == A.VARIANT_SHA256, A.sha(variant))

print("\n- Q1: exactly the two fragments -")
check("the variant is the shipped template with exactly the two fragments cut, not one other byte",
      variant == SHIPPED.replace(A.CUT_TRIVIAL, "", 1).replace(A.CUT_FIELD, "", 1)
      and len(SHIPPED) - len(variant) == len(A.CUT_TRIVIAL) + len(A.CUT_FIELD))
check("'All tags lowercase. Empty categories = [].' stays; neither fragment remains",
      "All tags lowercase. Empty categories = []." in variant and "If the session is trivial" not in variant
      and "FIELD project_relevant" not in variant and "empty beats wrong" not in variant)
check("the schema's project_relevant key stays (only its FIELD section is cut)", '"project_relevant"' in variant)
diff = "".join(difflib.unified_diff(SHIPPED.splitlines(keepends=True), variant.splitlines(keepends=True),
                                    "EXTRACTION_PROMPT (shipped)", "EXTRACTION_PROMPT (ablation c1)", n=3))
published = (ROOT / "research" / "v3" / "ablation_prompt_c1.diff").read_bytes()
check("the published diff is the diff, byte for byte (LF)", published == diff.encode("utf-8") and b"\r" not in published)
fields = {x for x in __import__("re").findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", variant)}
check("the variant's format fields are the shipped template's minus the cut block's own (project_hint)",
      fields | {"project_hint"} == {x for x in __import__("re").findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", SHIPPED)})

print("\n- refusals -")
check("derive refuses a template without the trivial-session sentence",
      refused(lambda: A.derive(SHIPPED.replace(A.CUT_TRIVIAL, "")), "the trivial-session sentence is found 0 times"))
check("derive refuses a template with the project_relevant block twice",
      refused(lambda: A.derive(SHIPPED + A.CUT_FIELD), "the project_relevant block is found 2 times"))


class NS:
    """An engine namespace stand-in for enable()'s refusals."""
    def __init__(self, prompt, retry=0):
        self.EXTRACTION_PROMPT, self.EXTRACT_RETRY, self._is_relevant = prompt, retry, GATE


check("enable refuses a shipped template that moved by one character",
      refused(lambda: A.enable(NS(SHIPPED.replace("Analyze", "Analyse", 1))), "the shipped template is"))
check("enable refuses when EXTRACT_RETRY is not 0 (the retry path reads the gate)",
      refused(lambda: A.enable(NS(SHIPPED, retry=1)), "EXTRACT_RETRY"))

print("\n- without enable(): the engine is untouched -")
check("importing the module changes nothing: the engine's own template and gate function",
      m.EXTRACTION_PROMPT is SHIPPED and m._is_relevant is GATE and m._is_relevant.__code__.co_name == "_is_relevant"
      and A.STATS["gate_would_refuse"] == 0)

print("\n- Q2 (i): through api.capture_session -")
OFFTOPIC_WITH_ONE = {"project_relevant": False, "patterns": [], "mistakes": [],
                     "decisions": [{"title": "timeout", "description": "the HTTP client timeout is 5 seconds",
                                    "facts": ["5 seconds"]}],
                     "session_summary": "short", "context_update": ""}
BODY = ("Came back to the API client after the break. That earlier decision is off: the HTTP client timeout is "
        "5 seconds now, and the retry wrapper keeps its three attempts.")
PROMPTS: list[str] = []


def fake_extract(prompt, project=None):
    PROMPTS.append(prompt)
    return dict(OFFTOPIC_WITH_ONE)


def capture(tag: str) -> tuple[dict, list]:
    d = make_sandbox(m, tag, offline=True)
    PROMPTS.clear()
    with mock.patch.object(m, "generate_json", fake_extract), mock.patch.object(m, "llm_available", lambda: True), \
            mock.patch.object(m, "update_embeddings", lambda notes: None):
        res = api.capture_session(BODY, project="abproj", agent="claude-code", session_id=f"s-{tag}")
    return res, list((d / "Decisions").glob("*abproj-decision-*.md"))


r0, n0 = capture("abl_off_")
check("default: a session the shipped gate refuses writes no typed note (the Session note only)",
      r0["decisions"] == 0 and n0 == [] and r0["stored"], str((r0.get("decisions"), n0)))
rec = A.enable(m)
r1, n1 = capture("abl_on_")
check("ablation: the same session IS written - the rebinding takes effect through the public API",
      r1["decisions"] == 1 and len(n1) == 1, str((r1.get("decisions"), n1)))
check("... the prompt the extractor received carries neither cut fragment",
      PROMPTS and "If the session is trivial" not in PROMPTS[-1] and "FIELD project_relevant" not in PROMPTS[-1]
      and "All tags lowercase. Empty categories = []." in PROMPTS[-1], str(len(PROMPTS)))
check("... and gate_would_refuse counts the refusal the shipped gate would have made (descriptive, Q5)",
      A.STATS["gate_would_refuse"] == 1, str(A.STATS))
check("enable() returns the arm_decl record: both template shas and this module's file sha256",
      rec == {"ablation": "c1-gate-off", "template_sha256": A.SHIPPED_TEMPLATE_SHA256, "variant_sha256": A.VARIANT_SHA256,
              "module_sha256": hashlib.sha256((ROOT / "research" / "v3" / "ablation_c1.py").read_bytes()).hexdigest()})

print("\n- Q2 (ii): the call sites, by AST -")
EXPECTED = {("nevertwice/_engine_cards.py", "process_session", "_is_relevant", "Load"),
            ("nevertwice/_engine_cards.py", "process_session", "EXTRACTION_PROMPT", "Load"),
            ("nevertwice/_engine_cards.py", "_retry_if_silent", "_is_relevant", "Load"),
            ("nevertwice/_engine_config.py", "<module>", "EXTRACTION_PROMPT", "Store"),
            ("nevertwice/_engine_text.py", "<module>", "_is_relevant", "def"),
            ("nevertwice/bootstrap_contexts.py", "<module>", "EXTRACTION_PROMPT", "Store"),
            ("nevertwice/bootstrap_contexts.py", "process", "EXTRACTION_PROMPT", "Load")}
NAMES = {"_is_relevant", "EXTRACTION_PROMPT"}
found = set()
for f in sorted((ROOT / "nevertwice").rglob("*.py")):
    tree = ast.parse(f.read_text(encoding="utf-8"))
    parents = {ch: node for node in ast.walk(tree) for ch in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        hit = None
        if isinstance(node, ast.Name) and node.id in NAMES:
            hit = (node.id, type(node.ctx).__name__)
        elif isinstance(node, ast.Attribute) and node.attr in NAMES:
            hit = (node.attr, "attr")
        elif isinstance(node, ast.ImportFrom) and any(a.name in NAMES for a in node.names):
            hit = ("import-from", ",".join(a.name for a in node.names if a.name in NAMES))
        elif isinstance(node, ast.FunctionDef) and node.name in NAMES:
            hit = (node.name, "def")
        if hit:
            p, fn = node, "<module>"
            while p in parents:
                p = parents[p]
                if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn = p.name
                    break
            found.add((f.relative_to(ROOT).as_posix(), fn, *hit))
check("every reference to _is_relevant and EXTRACTION_PROMPT in nevertwice/ is the recorded set (no new call site, no "
      "import-from binding its own copy)", found == EXPECTED, str(sorted(found ^ EXPECTED)))
check("... and the engine reads both only as namespace globals (no attribute or import-from access)",
      not any(k in ("attr", "import-from") for *_, k in found) and not any(h == "import-from" for _, _, h, _ in found))

print(f"\nv3 ablation c1: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
