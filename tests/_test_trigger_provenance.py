#!/usr/bin/env python3
"""`trigger` records which pipeline path wrote a note, not what the hook payload said.

Two different vocabularies were being written into one field. `trigger` is meant to say
HOW a note got here — the hook event, or `watch`/`process_now`/`ingest` for the other
entry points. But the Claude Code payload has its own `trigger`, with entirely different
values: PreCompact sends auto|manual, and a session payload can carry a `reason`. Reading
the payload FIRST let it win.

Vault-wide the field ended up holding watch=578 and process_now=498 beside auto=3,
manual=1 and clear=1, and it flipped in OPPOSITE directions for two session notes
produced in one batch. A field that answers two questions answers neither.
"""
import _env_guard  # noqa: F401
import sys, re
from pathlib import Path
import _engine_source  # noqa: E402  the engine's text, one path for every suite

SRC = _engine_source.SRC

RUN, FAILED = [], []


def check(name, cond, detail=""):
    RUN.append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}   {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def resolve(session, event):
    """The shipped precedence, read straight out of the source so the test cannot drift."""
    m = re.search(r"^\s*trigger = (.+)$", SRC, re.M)
    assert m, "the trigger assignment moved"
    hook_trigger = (session.get("trigger") or session.get("reason") or "").strip()
    return eval(m.group(1), {}, {"event": event, "hook_trigger": hook_trigger})


print("\n- the hook event wins, because it is the provenance -")
check("a PreCompact payload does not overwrite the event",
      resolve({"trigger": "auto"}, "PreCompact") == "PreCompact")
check("a payload `reason` does not overwrite it either",
      resolve({"reason": "clear"}, "SessionStart") == "SessionStart")

print("\n- the payload value is the fallback, not the winner -")
check("with no event, the payload value is used",
      resolve({"trigger": "watch"}, "") == "watch")
check("`reason` also serves as a fallback", resolve({"reason": "manual"}, "") == "manual")

print("\n- neither present still yields something -")
check("the last resort is 'manual'", resolve({}, "") == "manual")

print("\n- the payload value is kept, not discarded -")
# The payload value must be READ, into a name of its own. Matched on the name rather than on
# the spelling of the read: the fields are coerced through `_payload_str` now, because an
# explicit JSON null crashed the log line below them, and a check that pins how the value is
# fetched goes red on a fix that changes nothing about this property.
check("hook_trigger is captured separately",
      re.search(r"^[ ]*hook_trigger = .*trigger", SRC, re.M) is not None)
check("and it is read from the payload, not invented",
      re.search(r'^\s*hook_trigger = .*"trigger".*"reason"', SRC, re.M | re.S) is not None)
check("and trigger no longer reads the payload first",
      'trigger = session.get("trigger")' not in SRC)

print(f"\ntrigger provenance: {len(RUN) - len(FAILED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
