#!/usr/bin/env python3
"""The intervention inbox - one screen for everything the memory is currently asserting.

A memory that acts needs a place where a human can see what it is asserting *on their
behalf* and change it. Without that, the Popperian loop is a promise: guards promote and
retire themselves, contradictions resolve at write time, and the person whose repository it
is has no seat at the table until something fires at a bad moment.

This is that seat. One screen:

* **guards** by status - blocking, advisory, retired - each with what it has actually earned
  (precision and override rate with intervals, from `outcomes.py`, never from how often it
  fired);
* **unresolved contradictions** - facts the store revised where the successor was itself
  revised, so the chain is still moving;
* **stale facts** - two kinds, both actionable: a guard whose source note has left the live
  store, so its evidence can no longer be read, and a live note nobody has re-confirmed in
  months that has never recurred.

And five actions: approve, edit, override, retire, and trace-to-source.

**Operator decisions are recorded as operator decisions.** `approve` records one honest
`accepted` outcome - it does not promote, because promotion needs K *distinct sessions* and
one person clicking approve is one session. `--promote` and `retire` do force the status, and
they stamp `promoted_by`/`retired_by: operator` with a reason so the change is legible as a
human overriding the machine rather than smuggled in as evidence the guard never earned.
Corrupting the evidence channel to express an opinion is exactly how a feedback loop stops
meaning anything.

**Every action lands in the store and shows up in `git diff`.** Guard actions rewrite
`guards.json`; `confirm` writes a `reviewed:` line into the note's own frontmatter. Each
action returns the paths it touched, and the CLI prints them, so the round-trip is something
you can check rather than something you are told.

    nevertwice-inbox                                  # the screen
    nevertwice-inbox --project myproj --json
    nevertwice-inbox approve g-1a2b
    nevertwice-inbox override g-1a2b --reason "this call site is a fixed literal"
    nevertwice-inbox retire g-1a2b --reason "superseded by the linter rule"
    nevertwice-inbox edit g-1a2b --message "pass values as query parameters"
    nevertwice-inbox confirm 2026-03-01-acme-mistake-sql-built-by-fstring
    nevertwice-inbox trace g-1a2b

Standard library only. No server, no daemon: the store is the state.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import memory_hook as m         # noqa: E402
import guards as _guards        # noqa: E402
import outcomes as _outcomes    # noqa: E402
import digest as _digest        # noqa: E402
import why_fired as _why        # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:               # noqa: BLE001 - a redirected stream may not support it
    pass

SCHEMA_VERSION = 1

#: A live note nobody has re-confirmed in this long, which has never recurred, is asserting
#: something on stale evidence. Env-overridable because "stale" is a property of the domain,
#: not of the code - a fast-moving repo and a settled one disagree about half a year.
STALE_DAYS = m.env_int("NEVERTWICE_STALE_DAYS", 180)

ACTIONS = ("approve", "edit", "override", "retire", "confirm", "trace")


# ── the screen ──────────────────────────────────────────────────────────

def _guard_row(guard: dict) -> dict:
    summary = _outcomes.summary(guard)
    return {
        "id": guard["id"],
        "status": guard.get("status", "advisory"),
        "message": guard.get("message", ""),
        "project": (guard.get("scope") or {}).get("project"),
        "pack": bool(guard.get("pack")),
        "fired": int(guard.get("fired") or 0),
        "born_from": list(guard.get("born_from") or []),
        "born_date": guard.get("born_date"),
        "demotions": int(guard.get("demotions") or 0),
        # What it earned, never how often it fired: a fire count is not evidence.
        "precision": summary["precision"],
        "override_rate": summary["override_rate"],
        "distinct_sessions": summary["distinct_sessions"],
        "unresolved": summary["unknown"],
        "verdict": _outcomes.verdict(guard, promote_at=_guards.K_PROMOTE,
                                     retire_at=_guards.M_RETIRE),
    }


def _orphaned(guards: list[dict]) -> list[dict]:
    """Guards whose source note has left the live store.

    Not a bug on its own - notes are superseded and archived - but it is precisely how a
    guard becomes unfalsifiable: it keeps interrupting on evidence nobody can read any more.
    It belongs in front of a human.
    """
    out = []
    for guard in guards:
        if guard.get("status") == "retired" or not guard.get("born_from"):
            continue
        missing = [stem for stem in guard["born_from"]
                   if m._note_meta_for_stem(stem) is None]
        if missing:
            out.append({"id": guard["id"], "message": guard.get("message", ""),
                        "missing": missing, "status": guard.get("status")})
    return out


def _note_path(note: dict) -> Path | None:
    folder = m.TYPE_FOLDER.get(note.get("ntype") or "")
    if not folder or not note.get("stem"):
        return None
    path = m.VAULT / folder / f"{note['stem']}.md"
    return path if path.exists() else None


def _reviewed_on(note: dict) -> str:
    """The `reviewed:` stamp `confirm()` writes, read from the note's own header."""
    path = _note_path(note)
    if path is None:
        return ""
    try:
        return str(m._read_frontmatter_file(path).get("reviewed") or "").strip()
    except Exception:           # noqa: BLE001 - an unreadable header is simply not reviewed
        return ""


def _unconfirmed(project=None, *, days: int = STALE_DAYS, limit: int = 20) -> list[dict]:
    """Live notes older than `days` that never recurred and were never reviewed."""
    cutoff = (datetime.now() - timedelta(days=max(0, days))).strftime("%Y-%m-%d")
    out = []
    for note in m._iter_all_notes():
        if project and note.get("project") != m.slug_project(project):
            continue
        date = (note.get("date") or "")[:10]
        if not date or date >= cutoff:
            continue
        if int(note.get("recurrence") or 1) > 1 or note.get("resolved"):
            continue
        # `_note_meta` does not carry `reviewed`, and adding it there would mean editing
        # memory_hook.py for something that is not a seam extraction (GOAL rule 8). Read the
        # header directly instead - and only for the handful of notes that survived the age
        # and recurrence filters, so this stays a few reads rather than a vault scan.
        if _reviewed_on(note):
            continue
        out.append({"stem": note["stem"], "title": note.get("title", ""),
                    "ntype": note.get("ntype", ""), "project": note.get("project", ""),
                    "date": date,
                    "age_days": (datetime.now() - datetime.strptime(date, "%Y-%m-%d")).days})
    out.sort(key=lambda n: n["date"])
    return out[:limit]


def build(project: str | None = None, *, limit: int = 40) -> dict:
    """The whole screen, as data. Every surface renders this rather than re-deriving it."""
    guards = _guards.load_guards()
    if project:
        slug = m.slug_project(project)
        guards = [g for g in guards
                  if not (g.get("scope") or {}).get("project")
                  or (g.get("scope") or {}).get("project") == slug]
    by_status: dict[str, list] = {"blocking": [], "advisory": [], "retired": []}
    for guard in guards:
        by_status.setdefault(guard.get("status", "advisory"), []).append(_guard_row(guard))
    for rows in by_status.values():
        rows.sort(key=lambda r: (-r["fired"], r["id"]))

    contradictions = [c for c in _digest.compute_conflicts(project, limit=limit)
                      if not c.get("resolved")]
    orphaned = _orphaned(guards)
    unconfirmed = _unconfirmed(project, limit=limit)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "project": project or "(all)",
        "guards": by_status,
        "contradictions": contradictions,
        "stale": {"orphaned_guards": orphaned, "unconfirmed_notes": unconfirmed,
                  "stale_after_days": STALE_DAYS},
        "counts": {"blocking": len(by_status["blocking"]),
                   "advisory": len(by_status["advisory"]),
                   "retired": len(by_status["retired"]),
                   "contradictions": len(contradictions),
                   "orphaned_guards": len(orphaned),
                   "unconfirmed_notes": len(unconfirmed)},
        "actions": list(ACTIONS),
    }


# ── the actions ─────────────────────────────────────────────────────────

def _result(ok: bool, detail: str, changed=()) -> dict:
    """Every action answers the same three questions: did it work, what happened, and which
    files moved - the last so the round-trip into the store is checkable, not asserted."""
    return {"ok": ok, "detail": detail, "changed": [str(p) for p in changed]}


def _ledger() -> Path:
    return m.VAULT / "guards.json"


def approve(guard_id: str, *, session_id: str | None = None, promote: bool = False,
            reason: str | None = None) -> dict:
    """Record that a guard was right. One honest `accepted` outcome, from one session.

    It does **not** promote: promotion needs K distinct sessions, and one person approving is
    one session. `promote=True` forces the status and stamps `promoted_by: operator` with the
    reason, so a human decision is recorded as a human decision instead of as evidence the
    guard never earned.
    """
    guard = _guards.feedback(guard_id, "accepted",
                             session_id=session_id or f"inbox:{datetime.now():%Y-%m-%d}")
    if guard is None:
        return _result(False, f"no such guard: {guard_id}")
    if promote:
        if guard.get("pack"):
            return _result(False, "a cold-start pack guard is pinned advisory by design; "
                                  "it can be retired but never promoted to blocking")
        guard["status"] = "blocking"
        guard["promoted_by"] = {"who": "operator", "when": datetime.now().strftime("%Y-%m-%d"),
                                "reason": (reason or "approved in the inbox")[:200]}
        _persist(guard)
    return _result(True, f"{guard_id}: accepted recorded"
                         + (" and promoted to blocking by the operator" if promote else "")
                         + f"; status={guard['status']}", [_ledger()])


def override(guard_id: str, reason: str, *, session_id: str | None = None) -> dict:
    """Record that a guard was proceeded through, with the reason kept as a learned exception.

    An override is a statement about **burden**, not correctness - see `outcomes.py`. Use
    `override` when the guard was reasonable but wrong here; the reason narrows it.
    """
    if not (reason or "").strip():
        return _result(False, "an override needs a reason - it is the learned exception that "
                              "narrows the guard, and without one the feedback teaches nothing")
    guard = _guards.feedback(guard_id, "overridden", reason=reason,
                             session_id=session_id or f"inbox:{datetime.now():%Y-%m-%d}")
    if guard is None:
        return _result(False, f"no such guard: {guard_id}")
    return _result(True, f"{guard_id}: override recorded; status={guard['status']}", [_ledger()])


def retire(guard_id: str, *, reason: str | None = None) -> dict:
    """Retire a guard outright. The operator overruling the lifecycle, recorded as such."""
    guards = _guards.load_guards()
    guard = next((g for g in guards if g.get("id") == guard_id), None)
    if guard is None:
        return _result(False, f"no such guard: {guard_id}")
    was = guard.get("status")
    guard["status"] = "retired"
    guard["retired_by"] = {"who": "operator", "when": datetime.now().strftime("%Y-%m-%d"),
                           "reason": (reason or "retired in the inbox")[:200]}
    _guards.save_guards(guards)
    return _result(True, f"{guard_id}: {was} -> retired by the operator", [_ledger()])


def edit(guard_id: str, message: str) -> dict:
    """Rewrite a guard's message. The pattern is deliberately not editable here.

    A message is prose a human reads; a pattern is a regex that runs before every tool call,
    and `make_guard` refuses unsafe ones for reasons this project has been bitten by four
    review rounds running. Editing it would need the same validation, and an inbox is the
    wrong place to be authoring regexes.
    """
    message = (message or "").strip()
    if not message:
        return _result(False, "a guard with no message is a warning that says nothing")
    guards = _guards.load_guards()
    guard = next((g for g in guards if g.get("id") == guard_id), None)
    if guard is None:
        return _result(False, f"no such guard: {guard_id}")
    before = guard.get("message", "")
    guard["message"] = message[:240]
    guard.setdefault("edits", []).append(
        {"when": datetime.now().strftime("%Y-%m-%d"), "was": before[:240]})
    _guards.save_guards(guards)
    return _result(True, f"{guard_id}: message rewritten", [_ledger()])


def confirm(stem: str) -> dict:
    """Mark a stale note as still true, by writing `reviewed:` into its own frontmatter.

    This is the action that puts a human decision into the **Markdown** rather than into a
    side-car: the note file changes, and `git diff` in the vault shows the line. The edit is a
    single-line splice rather than a re-serialisation of the frontmatter, because the reader
    here is deliberately tolerant and lossy - round-tripping it would quietly drop whatever it
    did not understand.
    """
    meta = m._note_meta_for_stem(stem)
    if meta is None:
        return _result(False, f"no live note with stem {stem!r}")
    parsed = m.parse_typed_stem(stem)
    path = m.VAULT / m.TYPE_FOLDER[parsed["ntype"]] / f"{stem}.md"
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return _result(False, f"{stem} has no frontmatter to record the review in")
    end = text.find("\n---", 3)
    if end == -1:
        return _result(False, f"{stem} has an unterminated frontmatter block")

    today = datetime.now().strftime("%Y-%m-%d")
    header = text[3:end]
    lines = [ln for ln in header.split("\n") if not ln.strip().startswith("reviewed:")]
    lines = [ln for ln in lines if ln.strip() != ""]
    lines.append(f"reviewed: {today}")
    m.write_atomic(path, "---" + "\n".join([""] + lines) + text[end:])
    return _result(True, f"{stem}: reviewed {today}", [path])


def trace(guard_id: str, *, action_text: str = "", deep: bool = False) -> dict | None:
    """Trace-to-source: the full `WhyFired` object, unchanged - read-only by construction."""
    return _why.explain(guard_id, action_text, deep=deep)


def _persist(guard: dict) -> None:
    """Write one already-mutated guard back into the ledger by id."""
    guards = _guards.load_guards()
    for i, existing in enumerate(guards):
        if existing.get("id") == guard.get("id"):
            guards[i] = guard
            break
    _guards.save_guards(guards)


# ── rendering ───────────────────────────────────────────────────────────

def _earned(row: dict) -> str:
    precision = row["precision"]
    if precision.get("point") is None:
        return "no outcome yet"
    return (f"precision {precision['point']} [{precision['low']}-{precision['high']}, "
            f"n={precision['n']}]")


def render(screen: dict) -> str:
    counts = screen["counts"]
    out = [
        "",
        f"  Intervention inbox - {screen['project']} - {screen['generated']}",
        "  " + "-" * 74,
        f"  {counts['blocking']} blocking · {counts['advisory']} advisory · "
        f"{counts['retired']} retired · {counts['contradictions']} unresolved "
        f"contradiction(s) · {counts['orphaned_guards']} orphaned · "
        f"{counts['unconfirmed_notes']} unconfirmed",
        "",
    ]
    for status in ("blocking", "advisory", "retired"):
        rows = screen["guards"][status]
        if not rows:
            continue
        out.append(f"  {status.upper()}  ({len(rows)})")
        for row in rows[:20]:
            pack = " [pack]" if row["pack"] else ""
            out.append(f"    ({row['id']}){pack} {row['message'][:62]}")
            out.append(f"        fired {row['fired']}x · {_earned(row)} · "
                       f"{row['distinct_sessions']['support']}+/"
                       f"{row['distinct_sessions']['against']}- sessions")
            out.append(f"        {row['verdict']['because']}")
        if len(rows) > 20:
            out.append(f"    ... and {len(rows) - 20} more")
        out.append("")

    if screen["contradictions"]:
        out.append(f"  UNRESOLVED CONTRADICTIONS  ({len(screen['contradictions'])})")
        for c in screen["contradictions"][:10]:
            out.append(f"    {c['old_title'][:34]} -> {c['new_title'][:34] or '(archived)'}"
                       f"  [{c['project']}]")
        out.append("")

    stale = screen["stale"]
    if stale["orphaned_guards"]:
        out.append(f"  GUARDS WHOSE SOURCE IS GONE  ({len(stale['orphaned_guards'])})")
        for o in stale["orphaned_guards"][:10]:
            out.append(f"    ({o['id']}) {o['message'][:52]}")
            out.append(f"        missing: {', '.join(o['missing'][:2])}")
        out.append("")
    if stale["unconfirmed_notes"]:
        out.append(f"  UNCONFIRMED FOR OVER {stale['stale_after_days']} DAYS  "
                   f"({len(stale['unconfirmed_notes'])})")
        for n in stale["unconfirmed_notes"][:10]:
            out.append(f"    {n['date']}  {n['title'][:48]}  ({n['age_days']}d)")
            out.append(f"        confirm: nevertwice-inbox confirm {n['stem']}")
        out.append("")

    if counts["blocking"] + counts["advisory"] + counts["contradictions"] == 0:
        out.append("  Nothing is being asserted on your behalf right now.")
        out.append("")
    else:
        out.append("  approve <id> · edit <id> --message .. · override <id> --reason .. · "
                   "retire <id> · trace <id> · confirm <stem>")
        out.append("")
    return "\n".join(out)


def main() -> None:
    argv = sys.argv[1:]
    as_json = "--json" in argv
    project = m.argval(argv, "project")
    reason = m.argval(argv, "reason")
    message = m.argval(argv, "message")

    cmd = argv[0] if argv and not argv[0].startswith("--") else ""
    target = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else ""

    if not cmd:
        screen = build(project)
        print(json.dumps(screen, indent=2, ensure_ascii=False) if as_json else render(screen))
        return

    if cmd not in ACTIONS:
        print(f"unknown action: {cmd}\nusage: inbox [--project P] [--json] | "
              f"{' | '.join(ACTIONS)} <id>")
        raise SystemExit(2)
    if not target:
        print(f"{cmd} needs an id")
        raise SystemExit(2)

    if cmd == "trace":
        why = trace(target, deep="--deep" in argv)
        if why is None:
            print(f"no such guard: {target}")
            raise SystemExit(1)
        print(json.dumps(why, indent=2, ensure_ascii=False) if as_json else _why.render(why))
        return

    if cmd == "approve":
        result = approve(target, promote="--promote" in argv, reason=reason)
    elif cmd == "override":
        result = override(target, reason or "")
    elif cmd == "retire":
        result = retire(target, reason=reason)
    elif cmd == "edit":
        result = edit(target, message or "")
    else:
        result = confirm(target)

    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(("  ok   " if result["ok"] else "  FAILED  ") + result["detail"])
        for path in result["changed"]:
            print(f"       wrote {path}  (git diff in the store will show it)")
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
