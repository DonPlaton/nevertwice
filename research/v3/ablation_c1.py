#!/usr/bin/env python3
"""PREREG-V3 TB3(d) (A5): the gate-off ablation arm - research side, no product diff (the auditor's O3).

§2.2: the ablation runs the shipped extraction prompt "minus EXACTLY its project_relevant section and its trivial-session
rule", with the code-gate bypassed. The auditor's Q1: the TEXT governs, not line numbers - exactly two fragments are cut,
the trivial-session sentence and the whole "FIELD project_relevant ..." block through "... empty beats wrong." (with its
one blank separator line); "All tags lowercase. Empty categories = []." stays, and not one other byte changes.

* derive(template) cuts the two fragments, each found exactly once, or refuses by name;
* the shipped template and the variant are pinned by sha256 over the in-memory template string (UTF-8, unformatted,
  {{ intact - Q3); this file's own sha256 is the other half of "both pinned" (Q2), both go to FREEZE-V3;
* enable(m) installs the variant and the gate bypass in the engine's namespace (memory_hook runs the engine in ONE
  namespace, memory_hook.py:45) and returns the arm_decl record; the bypass counts how often the shipped gate would have
  refused - gate_would_refuse, descriptive only, outside K76's inputs (Q5). Nothing happens at import.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

#: The shipped EXTRACTION_PROMPT at the anchor (K67) and the variant derived from it.
SHIPPED_TEMPLATE_SHA256 = "34a192eb3b44972dad01411ee8e5030d7c7e393ed4be166d4e13f3fc24b02e59"
VARIANT_SHA256 = "86d8ea1ec0437ad0099d78f26be88e6ad496d5bf69c2ba368c677a407626f345"
#: The two fragments of §2.2, verbatim (the first with its leading space, so the sentence before it keeps its end).
CUT_TRIVIAL = " If the session is trivial (reading/discussion),\nreturn everything empty and fill only session_summary."
CUT_FIELD = ("FIELD project_relevant - CRITICAL for memory hygiene:\n"
             "  - true  - the session is genuinely about project {project_hint} (its code/research/tasks).\n"
             "  - false - offtopic: an unrelated question, personal troubleshooting (games, OS, hardware\n"
             "            off-topic), a different project, a model switch, an empty dialog. THEN return\n"
             "            patterns/mistakes/decisions = [] and context_update = \"\", fill ONLY session_summary.\n"
             "  Never pollute a project's knowledge with offtopic material - empty beats wrong.\n\n")
STATS = {"gate_would_refuse": 0}


class AblationRefused(RuntimeError):
    pass


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def module_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def derive(template: str) -> str:
    """The variant: exactly the two fragments cut, each found exactly once."""
    for name, cut in (("the trivial-session sentence", CUT_TRIVIAL), ("the project_relevant block", CUT_FIELD)):
        n = template.count(cut)
        if n != 1:
            raise AblationRefused(f"{name} is found {n} times in the template, not once - refused")
    return template.replace(CUT_TRIVIAL, "", 1).replace(CUT_FIELD, "", 1)


def enable(m) -> dict:
    """Install the variant and the gate bypass into the engine namespace ``m``; the record for arm_decl."""
    if getattr(m, "EXTRACT_RETRY", 0) != 0:
        raise AblationRefused("EXTRACT_RETRY is not 0 - the retry path reads the gate too")
    shipped = m.EXTRACTION_PROMPT
    if sha(shipped) != SHIPPED_TEMPLATE_SHA256:
        raise AblationRefused(f"the shipped template is {sha(shipped)[:12]}, not the pinned {SHIPPED_TEMPLATE_SHA256[:12]}")
    variant = derive(shipped)
    if sha(variant) != VARIANT_SHA256:
        raise AblationRefused(f"the variant is {sha(variant)[:12]}, not the pinned {VARIANT_SHA256[:12]}")
    gate = m._is_relevant

    def bypass(flag) -> bool:
        """The code-gate off: every session is relevant; how often the shipped gate would have refused is counted."""
        if not gate(flag):
            STATS["gate_would_refuse"] += 1
        return True

    m.EXTRACTION_PROMPT = variant
    m._is_relevant = bypass
    return {"ablation": "c1-gate-off", "template_sha256": SHIPPED_TEMPLATE_SHA256, "variant_sha256": VARIANT_SHA256,
            "module_sha256": module_sha256()}
