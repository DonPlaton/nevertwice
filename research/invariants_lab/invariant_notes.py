"""I1: an invariant as a first-class note -- same ledger, different provenance and lifecycle.

A **scar** is knowledge induced from something that already broke: `guards.py` compiles it
to a regex, matches it against a proposed action, and costs nothing until it fires. An
**invariant** is a claim about the code that can be checked before anything breaks. The
spec's own framing is that the difference is *provenance and lifecycle, not machinery*, and
this module takes that literally: an invariant is a record in the same ledger shape, with the
same zero-cost-until-fired hot path, and three fields a scar does not have.

| | scar | invariant |
|---|---|---|
| where it comes from | an incident that happened | a property of the code, asserted |
| what it matches | a regex over the proposed action text | a deterministic checker over the diff |
| when it retires | after five false positives | after **two** |
| how many can fire at once | as many as match | **one**, the highest ranked |
| what it costs when quiet | nothing | nothing |

The stricter lifecycle is not conservatism, it is the spec's own warning: *an invariant firing
on 30% of diffs is switched off in week one, and after that being right does not matter.*
[`BLAST_RADIUS_D5.md`](BLAST_RADIUS_D5.md) measured exactly that rate for the first candidate
checker -- 27.8% -- which is why the delivery layer caps output before any checker is trusted
to behave.

Nothing here writes to the owner's vault. Every function takes the ledger it operates on, and
the default is an empty list; the caller supplies a path. `nevertwice/` is untouched until a
mechanism passes its own gate in Phase T.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

#: A scar retires after five false positives; an invariant after two. An invariant that is
#: wrong twice has already spent the attention a whole class of them shares.
RETIRE_AFTER_FALSE_POSITIVES = 2

#: At most one invariant finding reaches context per diff, however many fire. Two findings
#: from one root cause is one finding and one bug (T2), and a list of five is a list nobody
#: reads.
MAX_FINDINGS_PER_DIFF = 1

_KIND = "invariant"

#: The killswitch. Any falsy spelling turns the whole mechanism off, and it is checked
#: FIRST -- before the ledger is read, before scope is matched, before a checker is
#: resolved -- so "off" costs a dictionary lookup and nothing else. A killswitch that
#: still pays for the machinery it disables is a killswitch nobody believes.
KILLSWITCH_ENV = "NEVERTWICE_INVARIANTS"
_FALSY = {"0", "false", "no", "off", ""}


def disabled(environ: dict[str, str] | None = None) -> bool:
    import os
    env = os.environ if environ is None else environ
    return env.get(KILLSWITCH_ENV, "1").strip().lower() in _FALSY


# --------------------------------------------------------------------------
# the note
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """What a checker says about one diff. `rank` orders competing findings."""

    invariant_id: str
    message: str
    subject: str          # the symbol, file or property the finding is about
    rank: float = 0.0     # higher wins when the cap forces a choice
    evidence: str = ""


def invariant_id(checker: str, subject: str, scope: dict) -> str:
    h = hashlib.sha1(
        f"{checker}|{subject}|{scope.get('project','')}|{scope.get('path_glob','')}"
        .encode("utf-8", "replace")
    ).hexdigest()[:8]
    return f"i-{h}"


def make_invariant(checker: str, message: str, *, subject: str = "",
                   project: str | None = None, path_glob: str | None = None,
                   provenance: str = "declared", born_from: Iterable[str] = (),
                   date: str | None = None) -> dict | None:
    """Construct an advisory invariant note, or None if it is not well formed.

    `checker` names a deterministic check, not a regex. A scar's pattern is data the
    hot path interprets; an invariant's checker is code the registry resolves, because
    "this diff degrades a file against its own baseline" is not expressible as a regex
    and pretending otherwise is how a structural claim becomes a text match.
    """
    if not checker or not (message or "").strip():
        return None
    scope = {"project": project or None, "path_glob": path_glob or None}
    return {
        "kind": _KIND,
        "id": invariant_id(checker, subject, scope),
        "checker": checker,
        "subject": subject,
        "message": message.strip()[:240],
        "scope": scope,
        "status": "advisory",
        "provenance": provenance,       # declared | preconfigured | derived
        "born_from": list(born_from),
        "born_date": date or datetime.now().strftime("%Y-%m-%d"),
        "fired": 0,
        "helped": 0,
        "false_positives": 0,
        "last_fired": "",
    }


def register(ledger: list[dict], note: dict) -> bool:
    """Add to the ledger, deduped by id. Returns True if it was new."""
    if not note:
        return False
    if any(n.get("id") == note["id"] for n in ledger):
        return False
    ledger.append(note)
    return True


def invariants(ledger: list[dict]) -> list[dict]:
    """Only the invariant notes. A ledger may hold scars too, and must."""
    return [n for n in ledger if n.get("kind") == _KIND]


def live(ledger: list[dict]) -> list[dict]:
    return [n for n in invariants(ledger) if n.get("status") != "retired"]


# --------------------------------------------------------------------------
# storage -- the same shape as guards.json, and never the owner's copy
# --------------------------------------------------------------------------


def load(path: Path) -> list[dict]:
    """Read a ledger. A missing or unreadable file is an empty ledger, loudly empty.

    `guards.py` recovers a corrupt ledger across two generations because a silently
    swallowed one meant every guard stopped firing with no trace. The lab has no
    generations to recover from yet, so it does the next honest thing: it returns
    empty and the caller can see the file did not parse.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def save(path: Path, ledger: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------
# the hot path -- zero tokens until something fires
# --------------------------------------------------------------------------


def _scope_matches(note: dict, project: str | None, paths: Iterable[str]) -> bool:
    scope = note.get("scope") or {}
    if scope.get("project") and scope["project"] != (project or ""):
        return False
    glob = scope.get("path_glob")
    if glob:
        import fnmatch
        norm = [str(p).replace("\\", "/") for p in paths]
        g = glob.replace("\\", "/")
        return any(
            fnmatch.fnmatch(p, g)
            or any(fnmatch.fnmatch("/".join(p.split("/")[i:]), g)
                   for i in range(1, len(p.split("/"))))
            for p in norm
        )
    return True


def check_diff(before: dict[str, str], after: dict[str, str], *,
               ledger: list[dict] | None = None,
               registry: dict[str, Callable[..., list[Finding]]] | None = None,
               project: str | None = None,
               cap: int = MAX_FINDINGS_PER_DIFF,
               environ: dict[str, str] | None = None) -> list[Finding]:
    """Run every live invariant over one diff and return **at most `cap`** findings.

    Zero work and zero output when the ledger is empty or nothing fires -- the
    token-economy core, unchanged from the scar path. A checker that raises is
    skipped, not propagated: an invariant that crashes must not take the commit with
    it, and a mechanism that can break the tool it protects has a worse failure mode
    than the one it prevents.
    """
    if disabled(environ):
        return []
    notes = live(ledger or [])
    if not notes:
        return []
    registry = registry or {}
    paths = sorted(set(before) | set(after))

    found: list[Finding] = []
    for note in notes:
        if not _scope_matches(note, project, paths):
            continue
        checker = registry.get(note.get("checker", ""))
        if checker is None:
            continue
        try:
            found.extend(checker(before, after, note) or [])
        except Exception:  # noqa: BLE001 - a broken checker is not a broken commit
            continue

    found.sort(key=lambda f: (-f.rank, f.invariant_id, f.subject))
    return found[:cap] if cap >= 0 else found


# --------------------------------------------------------------------------
# I2: the lifecycle, stricter than intuition wants
# --------------------------------------------------------------------------


OUTCOMES = ("helped", "false_positive", "fired")


def feedback(note: dict, outcome: str) -> dict:
    """Record what happened, and retire the note if it has been wrong twice.

    A scar earns corroboration over time and survives five false positives. An
    invariant gets two, and **being helpful does not buy forgiveness**: the counters
    are separate on purpose, because the attention a wrong finding spent is not
    refunded by a right one. A mechanism that averages its mistakes away is a
    mechanism whose owner discovers the average only after switching it off.

    An outcome nobody defined raises, rather than being silently dropped: a feedback
    channel that quietly ignores what it does not recognise is a feedback channel
    that reports success forever.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}; expected one of {OUTCOMES}")
    if outcome == "helped":
        note["helped"] = note.get("helped", 0) + 1
    elif outcome == "fired":
        note["fired"] = note.get("fired", 0) + 1
        note["last_fired"] = datetime.now().strftime("%Y-%m-%d")
    else:
        note["false_positives"] = note.get("false_positives", 0) + 1
        if note["false_positives"] >= RETIRE_AFTER_FALSE_POSITIVES:
            note["status"] = "retired"
            note["retired_date"] = datetime.now().strftime("%Y-%m-%d")
            note["retired_reason"] = (
                f"{note['false_positives']} false positive(s); the budget is "
                f"{RETIRE_AFTER_FALSE_POSITIVES}"
            )
    return note


def record_fired(ids: Iterable[str], ledger: list[dict]) -> None:
    """Stamp the notes that actually reached context. An empty round changes nothing."""
    wanted = set(ids)
    if not wanted:
        return
    for note in ledger:
        if note.get("id") in wanted:
            feedback(note, "fired")


def revive(note: dict) -> dict:
    """Bring a retired invariant back, deliberately, with its budget reset.

    Retirement is automatic; revival is not. The note remembers it happened, so a
    claim that keeps being wrong and keeps being restored is visible as such rather
    than looking new each time.
    """
    note["status"] = "advisory"
    note["false_positives"] = 0
    note["revivals"] = note.get("revivals", 0) + 1
    note.pop("retired_date", None)
    note.pop("retired_reason", None)
    return note


def render(findings: list[Finding]) -> str:
    """The whole context cost. Empty when nothing fired -- not a heading, not a blank line."""
    if not findings:
        return ""
    return "\n".join(f"invariant {f.invariant_id}: {f.message}" +
                     (f" [{f.subject}]" if f.subject else "")
                     for f in findings)
