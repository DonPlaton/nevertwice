#!/usr/bin/env python3
"""A broken host contract looks exactly like an idle run, and the price of fixing that is 81 claims.

The premortem for exit criterion 5 named hook-contract drift: the host agent renames a payload
field, every installation stops working at once, and the user experiences it as "my agent broke
after the update" - filing with the vendor, not here. The question worth asking was not whether
that could happen but what it LOOKS like, so this measured it by running the entry point with
three payloads:

    {"hook_event_name": "SessionStart", ...}   exit 0   "SessionStart - no backlog"
    {"event_type": "init", ...}                exit 0   "No work performed - status.txt not updated"
    {}                                         exit 0   "No work performed - status.txt not updated"

A renamed contract is byte-identical to an empty invocation, and both are indistinguishable from a
legitimately quiet run. Exit 0 is CORRECT and must stay: a hook that fails breaks the user's agent,
which is the one thing this project must never do. What is missing is a line that says "I did not
recognise this payload" - and that line lives in `_engine_hooks.py`.

**Which is why this suite pins the behaviour instead of fixing it.** Measured before writing it:
81 of the 101 live claims name `_engine_hooks.py` in their closure, so the one-line diagnostic
withdraws four fifths of the register's live set. That is not a reason never to do it; it is a
reason to do it inside a re-measure window rather than as a drive-by. Until then the silence is a
number someone decided, not a default nobody noticed - and this suite fails the moment the
behaviour moves in either direction, which is what makes the decision conscious.

    python tests/_test_hook_contract_break_is_silent.py
"""
import _env_guard  # noqa: F401
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "nevertwice" / "memory_hook.py"
FAILS = 0


def check(name, cond, detail=""):
    global FAILS
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS += 1


def run(payload: dict) -> tuple[int, str]:
    """The entry point, driven exactly as a host drives it: one JSON object on stdin.

    **Why this cannot wake the extraction model, stated correctly.** A recognised `SessionStart`
    on a store with a backlog detaches a catch-up that takes the vault lock and extracts - the
    auditing session triggered exactly that twice today, once loading 22 GB onto the GPU. The
    gate is `has_unprocessed`, and it is a filesystem walk over the TRANSCRIPTS root filtered by
    the processed database: an empty `NEVERTWICE_PROJECTS_ROOT` has no candidates, so there is no
    backlog to chase. An empty VAULT alone would do the opposite - an empty processed database
    makes every transcript look unprocessed, which is the worst case, and the first version of
    this docstring justified the isolation by the wrong half of the pair (the auditing session
    read the call graph and corrected it: the `return` at `_engine_hooks.py:468` leaves the
    HELPER, not `main`). The claim is asserted below rather than argued, because an argument that
    was wrong once can be wrong again.
    """
    with tempfile.TemporaryDirectory(prefix="nw_hookpayload_") as td:
        env = dict(os.environ)
        env.update({"NEVERTWICE_VAULT": td, "NEVERTWICE_PROJECTS_ROOT": td,
                    "NEVERTWICE_START_SWEEP_DETACH": "0",
                    #: PREVENTION, not detection. The assertions below run AFTER the subprocess
                    #: returns, so on a machine where the isolation fails they would report a
                    #: sweep that has already spent five minutes of GPU. Pointing the extractor
                    #: at a closed port makes the worst case the one measured in the premortem:
                    #: `{}` after 10.7 s, "Ollama unreachable after 3 tries", cloud=0 ollama=0
                    #: fail=1 - loud, cheap, and the same on any machine (auditing session).
                    "OLLAMA_URL": "http://127.0.0.1:1",
                    "NEVERTWICE_CLOUD": "none",
                    "PYTHONIOENCODING": "utf-8"})
        for key in ("CEREBRAS_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY"):
            env.pop(key, None)
        p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=300)
        return p.returncode, ((p.stdout or "") + (p.stderr or ""))


print("\n- a hook never fails the host, whatever it is handed -")
TODAY = {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": ".", "transcript_path": ""}
RENAMED = {"event_type": "init", "session_id": "s1", "cwd": ".", "transcript": ""}
rc_today, out_today = run(TODAY)
rc_renamed, out_renamed = run(RENAMED)
rc_empty, out_empty = run({})
for label, rc in (("today's contract", rc_today), ("a renamed contract", rc_renamed),
                  ("an empty payload", rc_empty)):
    check(f"{label} exits 0 - a failing hook would break the user's agent", rc == 0, str(rc))

print("\n- and none of the three woke anything: the property is measured, not argued -")
#: A detached catch-up takes the vault lock and calls the extraction model. If this suite ever
#: starts one - on a runner whose paths differ, or after a change to the backlog gate - it must
#: say so here rather than quietly spend a GPU. The line it would print is fixed in the engine
#: (`_engine_hooks.py:446`).
for label, out in (("today", out_today), ("renamed", out_renamed), ("empty", out_empty)):
    check(f"{label}: no catch-up was detached", "catch-up detached" not in out,
          out.strip()[-140:].replace("\n", " | "))
    #: The run must SAY that nothing was swept, in one of the two wordings the engine uses - the
    #: recognised-SessionStart path reports "no backlog (lock never taken)", the others close with
    #: "swept=0". An earlier draft accepted `"swept" not in out`, which passes for any log that
    #: merely lacks the word: absence taken as evidence, and it would have survived a change of
    #: format (auditing session). Requiring one of the two named sentences is the positive form.
    check(f"{label}: the run states that it swept nothing",
          ("swept=0" in out) or ("no backlog (lock never taken)" in out),
          out.strip()[-140:].replace("\n", " | "))

print("\n- today's contract is recognised, and says which event it was -")
check("the event name reaches the log", "SessionStart" in out_today,
      out_today.strip()[-120:].replace("\n", " | "))

print("\n- and a broken contract is INDISTINGUISHABLE from an idle run: the pinned defect -")
#: Compared on the shape of the message, not byte-for-byte: the log carries a timestamp.
def shape(text: str) -> set[str]:
    return {line.split("] ", 1)[-1].strip() for line in text.splitlines() if line.strip()}


#: Compared on the fields that carry the CONTRACT, not on the whole line: a renamed payload still
#: supplies `session_id` and `cwd`, so `id=` and `dir=` legitimately differ between the two runs
#: and requiring the lines to match measured something adjacent - caught by this check's own red
#: line on the first run.
def event_field(text: str) -> list[str]:
    return [ln.split("Event=", 1)[1].split("|", 1)[0].strip()
            for ln in text.splitlines() if "Event=" in ln]


check("the event is empty under a renamed contract, exactly as under an empty payload",
      event_field(out_renamed) == event_field(out_empty) == [""],
      f"renamed {event_field(out_renamed)} | empty {event_field(out_empty)}")
check("and both end in the same sentence, which is what an idle run also prints",
      ("No work performed" in out_renamed) and ("No work performed" in out_empty)
      and ("No work performed" not in out_today))
check("and neither says anything about an unrecognised payload",
      not any("unrecognis" in ln.lower() or "unknown payload" in ln.lower()
              or "contract" in ln.lower() for ln in shape(out_renamed)),
      str(sorted(shape(out_renamed))[:3]))
#: The line that WOULD be the fix, named so the next reader can find it rather than rediscover it.
check("the fix belongs at the dispatch that reads the event name",
      "hook_event_name" in (ROOT / "nevertwice" / "_engine_hooks.py").read_text(encoding="utf-8"))

print("\n- the price of the fix, measured rather than guessed -")
manifest = json.loads((ROOT / "research" / "evidence_manifest.json").read_text(encoding="utf-8"))
_c = manifest["claims"]
claims = list(_c.values() if isinstance(_c, dict) else _c)
live = [c for c in claims if not (c.get("stale") or c.get("pending_remeasure"))]
hit = [c for c in live if any("_engine_hooks.py" in p for p in (c.get("produced_by") or []))]
check("there are live claims to lose", len(live) >= 50, str(len(live)))
check(f"{len(hit)} of {len(live)} live claims name the module the fix touches",
      len(hit) >= 50, f"{len(hit)} of {len(live)}")
check("so the diagnostic belongs in a re-measure window, not in a drive-by commit",
      len(hit) / max(len(live), 1) > 0.5, f"{len(hit)}/{len(live)}")

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
