#!/usr/bin/env python3
"""What the README promises about where a transcript goes, against where the code sends it.

The premortem run for exit criterion 5 asked what a launch would be killed by, and one answer was
the read-and-send path rather than the write path: a tool that ships a developer's session to a
model. Checking it found the promise and the path disagreeing.

    README.md:134   "Extraction is local-first and an optional cloud key only speeds it up."
    _engine_store.generate_json   tries `call_cloud` FIRST and falls back to Ollama
    _engine_config.py:322         "The cloud backend is the recommended primary."

And the key is not chosen for this tool. `_resolve_cloud()` defaults to `auto`, which means "any
of CEREBRAS_API_KEY, GROQ_API_KEY, DEEPSEEK_API_KEY, GEMINI_API_KEY that happens to be in the
environment". Measured by running it in a clean subprocess rather than by reading it:

    no keys                       ACTIVE_CLOUD = none
    GEMINI_API_KEY=x              ACTIVE_CLOUD = gemini
    GROQ_API_KEY=x                ACTIVE_CLOUD = groq
    NEVERTWICE_CLOUD=none + key   ACTIVE_CLOUD = none

So a developer with a Gemini key exported for something else installs this and their sessions go
to Gemini. That is documented - `docs/CONFIG.md:24` says `auto` "picks whichever key is present" -
and it is a defensible design. What is not defensible is the README sentence, which tells the
reader the opposite of what the code does, on the one subject where a reader cannot afford to be
told the opposite: where their code goes.

The behaviour is the owner's call and is NOT changed here; changing where a user's transcript is
sent is an architecture decision, not a typo. The prose is corrected, and this suite keeps the two
from drifting apart again: if someone makes extraction genuinely local-first, this fails and the
README can say so.

    python tests/_test_privacy_prose_matches_the_path.py
"""
import _env_guard  # noqa: F401
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


STORE = (ROOT / "nevertwice" / "_engine_store.py").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")

print("\n- the extraction path is read from the code, in the order the code runs it -")
tree = ast.parse(STORE)
fn = next((n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name == "generate_json"), None)
check("generate_json is where extraction chooses a backend", fn is not None)
#: Ordered by LINE, not by `ast.walk`: walk is breadth-first, so it reported `call_ollama` before
#: `call_cloud` when the source has the opposite order - a check answering a question adjacent to
#: the one it asks, caught here by its own red line.
calls = [name for _, name in sorted(
    (n.lineno, n.func.id) for n in ast.walk(fn)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name))] if fn else []
check("it calls both a cloud backend and the local one", {"call_cloud", "call_ollama"} <= set(calls),
      str(sorted(set(calls))))
cloud_first = calls.index("call_cloud") < calls.index("call_ollama") if (
    "call_cloud" in calls and "call_ollama" in calls) else None
check("and the cloud one comes first, which is the fact the prose must match",
      cloud_first is True, f"order {calls}")

print("\n- the README describes that order rather than its opposite -")
#: The exact sentence that was wrong, kept as a literal so a rewrite that reintroduces it fails.
check("the README no longer calls extraction local-first",
      "Extraction is local-first" not in README)
para = re.search(r"There is \*\*no telemetry\*\*.{0,400}", README, re.S)
check("the privacy paragraph is still there to be checked", para is not None)
if para and cloud_first:
    window = README[max(0, para.start() - 700):para.end()]
    check("and it says a cloud key routes extraction rather than merely speeding it up",
          re.search(r"cloud (backend|extraction).{0,80}(first|primary|before)", window, re.I)
          is not None,
          window[:200].replace("\n", " "))

print("\n- and the security policy does not call an inherited key an opt-in -")
#: SECURITY.md said "nothing leaves your computer unless you opt into a cloud backend with your
#: own key". A key exported years ago for another tool is not an opt-in taken here, and this is
#: the one document where a reader most needs the sentence to be literally true.
SEC = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
check("the opt-in phrasing is gone from the security policy",
      "unless you opt" not in SEC and "unless you opt into a cloud backend" not in SEC)
check("and it names the default that actually decides", "NEVERTWICE_CLOUD" in SEC)

print("\n- the provider is resolved by RUNNING it, with the environment a stranger would have -")
PROBE = ("import os, sys; sys.path.insert(0, r'%s'); "
         "os.environ.setdefault('NEVERTWICE_VAULT', r'%s'); "
         "import memory_hook as m; print(m.ACTIVE_CLOUD)"
         % (ROOT / "nevertwice", ROOT / "research" / "results" / "_privacy_probe_vault"))
KEYS = ("CEREBRAS_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
        "NEVERTWICE_CLOUD")


def resolve(**extra) -> str:
    env = {k: v for k, v in os.environ.items() if k not in KEYS}
    env.update(extra)
    env["PYTHONIOENCODING"] = "utf-8"
    out = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", env=env, timeout=120)
    return (out.stdout or out.stderr).strip().splitlines()[-1] if (out.stdout or out.stderr) else ""


check("with no key at all, nothing leaves the machine", resolve() == "none", resolve())
check("a key exported for ANOTHER tool selects that vendor - this is the finding",
      resolve(GEMINI_API_KEY="x") == "gemini", resolve(GEMINI_API_KEY="x"))
check("and the documented opt-out really opts out",
      resolve(GEMINI_API_KEY="x", NEVERTWICE_CLOUD="none") == "none",
      resolve(GEMINI_API_KEY="x", NEVERTWICE_CLOUD="none"))
#: The NEGATIVE case bounds the exposure, and the prose must not be wider than it. An OpenAI or
#: Anthropic key does NOT select a backend - `_resolve_cloud` tries exactly four, in the order
#: Cerebras, Groq, DeepSeek, Gemini. The first draft of the security note said "any cloud key",
#: which was wider than the code; the auditing session measured the two that do nothing.
check("a key outside the four supported ones selects nothing",
      resolve(OPENAI_API_KEY="x") == "none" and resolve(ANTHROPIC_API_KEY="x") == "none",
      f"openai {resolve(OPENAI_API_KEY='x')}, anthropic {resolve(ANTHROPIC_API_KEY='x')}")
SEC_TXT = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
check("and the security note names four keys rather than 'any cloud key'",
      "any cloud key" not in SEC_TXT and "four keys" in SEC_TXT)

print("\n- and the opt-outs the docs promise exist in the code, not only in the docs -")
check("per-project local-only routing is a function extraction consults",
      "is_local_only(project)" in STORE)
check("a local-only project never reaches the cloud branch",
      re.search(r"if cloud_on and not _CLOUD_DEAD and not local_only", STORE) is not None)
#: Redaction happens BEFORE the prompt is built, not only before a note is written - checked at
#: the call site, because "secrets are redacted before anything is written or sent" is a promise
#: about the send path too.
cards = (ROOT / "nevertwice" / "_engine_cards.py").read_text(encoding="utf-8")
check("the transcript is redacted before it becomes a prompt",
      re.search(r"transcript_full\s*=\s*truncate_smart\(\s*redact_secrets\(", cards) is not None)

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
