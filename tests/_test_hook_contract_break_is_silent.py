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
    #: A THIRD wording, added 2026-09-22 after CI's first matrix run: on a runner with no model
    #: at all the engine never reaches the sweep decision and closes with "No LLM backend
    #: available (cloud key unset + Ollama down) - paused". That is the same statement in a
    #: stronger form - nothing was swept, and the log says why - so it is named here rather
    #: than the check being widened to "the run said something". The suite already closes the
    #: Ollama port and strips the cloud keys; this machine still reaches a backend and CI does
    #: not, which is exactly the difference the matrix exists to show.
    check(f"{label}: the run states that it swept nothing",
          ("swept=0" in out) or ("no backlog (lock never taken)" in out)
          or ("No LLM backend available" in out and "paused" in out),
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
#: The property is that a renamed contract closes the way an IDLE RUN closes, and that today's
#: contract does not. Naming the sentence - "No work performed" - pinned one backend's wording:
#: on a runner with no model at all the engine pauses earlier and both runs close with "No LLM
#: backend available (cloud key unset + Ollama down) - paused" instead, so the check went red on
#: every Linux and macOS job of CI's first matrix run while the property it names held perfectly.
#: Comparing the two closing lines to EACH OTHER states the property without naming a wording,
#: and keeps the half that matters: today's run must not close the same way.
def closing(text: str) -> str:
    lines = [ln.split("] ", 1)[-1].strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


check("and both end in the same sentence, which is what an idle run also prints",
      closing(out_renamed) == closing(out_empty) != "",
      f"renamed {closing(out_renamed)!r} | empty {closing(out_empty)!r}")
check("while today's contract closes differently, or the two would be indistinguishable "
      "for the wrong reason",
      closing(out_today) != closing(out_renamed),
      f"today {closing(out_today)!r}")
check("and neither says anything about an unrecognised payload",
      not any("unrecognis" in ln.lower() or "unknown payload" in ln.lower()
              or "contract" in ln.lower() for ln in shape(out_renamed)),
      str(sorted(shape(out_renamed))[:3]))
#: The line that WOULD be the fix, named so the next reader can find it rather than rediscover it.
check("the fix belongs at the dispatch that reads the event name",
      "hook_event_name" in (ROOT / "nevertwice" / "_engine_hooks.py").read_text(encoding="utf-8"))

print("\n- the price of the fix, measured rather than guessed -")
#: This block used to pin the price as a pair of counts - "81 of 101 live claims name the module"
#: - and on 2026-09-22 the bill came due for a different reason: a cross-platform fix to
#: `_detach_kwargs` in that same module (c63786f) withdrew all 81, leaving 20 live. The pinned
#: numbers then read as a failure of this suite, which is backwards: the price did not change,
#: it was PAID. So the property is stated instead of the snapshot - every live claim whose
#: closure holds this module is lost the day the module moves, whatever the count happens to be -
#: and the counts are printed, not asserted.
manifest = json.loads((ROOT / "research" / "evidence_manifest.json").read_text(encoding="utf-8"))
_c = manifest["claims"]
claims = list(_c.values() if isinstance(_c, dict) else _c)
live = [c for c in claims if not (c.get("stale") or c.get("pending_remeasure"))]
hit = [c for c in live if any("_engine_hooks.py" in p for p in (c.get("produced_by") or []))]
#: The register must still be readable, or the two counts below are both zero for a third reason.
check(f"the register is readable and holds claims ({len(claims)})", len(claims) >= 500,
      str(len(claims)))
print(f"    live now: {len(live)};  of them closing over _engine_hooks.py: {len(hit)}")
#: The property, which does not depend on today's counts: the module is IN the closure of claims
#: the register knows about, so editing it withdraws them. Asked of every claim, not just the
#: live ones, because the live set is exactly what an edit empties.
_ever = [c for c in claims if any("_engine_hooks.py" in p for p in (c.get("produced_by") or []))]
check(f"the register knows this module as load-bearing ({len(_ever)} claims close over it)",
      len(_ever) >= 50, str(len(_ever)))
check("so a change here is a re-measure window, not a drive-by commit - whatever is live today",
      len(_ever) / max(len(claims), 1) > 0.05, f"{len(_ever)}/{len(claims)}")
#: And the fact the original pair was there to establish, kept as history rather than as a gate:
#: on 2026-09-22 the module moved once and 81 of 101 live claims went with it.
check("the recorded instance of that price is still the right shape",
      len(_ever) >= 81, f"{len(_ever)} claims close over it; 81 were live when it last moved")

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
