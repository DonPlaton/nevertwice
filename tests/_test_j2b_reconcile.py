#!/usr/bin/env python3
"""J2b: archive-aware reconcile closes a belief interval when the replaced note has
already aged into Archive/, and every retirement records how it was decided.

The defect this pins (ledger J2b): a fact older than the 90-day window is in Archive/
when its replacement arrives; the old reconcile globbed only the live folder, so
`valid_to` was never stamped and `as_of` kept showing the stale fact as current. On the
owner store that is a quarter of the knowledge. Pure logic + disk; no LLM, no embedder.

    python _test_j2b_reconcile.py
"""
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import _env_guard  # noqa: F401  hermetic: scrub store env before the package bakes path constants
import memory_hook as m

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sandbox import make_sandbox

P = F = 0


def check(name, cond):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}")


def _archive(stem: str, ntype: str = "pattern") -> Path:
    """Move a just-written live note into its folder's Archive/, as archive_old_typed would."""
    folder = m.VAULT / m.TYPE_FOLDER[ntype]
    live = folder / f"{stem}.md"
    arch = folder / "Archive"
    arch.mkdir(exist_ok=True)
    dest = arch / f"{stem}.md"
    os.replace(live, dest)
    return dest


# ── the path helpers: the live glob skips the archive, the reconcile glob does not ──────────
print("# _live vs _reconcilable typed paths")
make_sandbox(m, "j2b_", offline=True)
folder = m.VAULT / m.TYPE_FOLDER["pattern"]
old_stem = m.write_typed_note("Patterns", {"title": "retry policy", "description": "retry only on timeouts"},
                              "proj", "2026-01-01", ["t"], "pattern")
slug = m.parse_typed_stem(old_stem)["slug"]
arch_path = _archive(old_stem)
check("the archived note is gone from the live folder", not (folder / f"{old_stem}.md").exists())
check("the archived note is under Archive/", arch_path.exists())
check("_live_typed_paths does NOT see the archived note (the defect)",
      m._live_typed_paths(folder, "proj", "pattern", slug) == [])
recon = m._reconcilable_typed_paths(folder, "proj", "pattern", slug)
check("_reconcilable_typed_paths DOES see it (the fix)", [p.name for p in recon] == [arch_path.name])
check("_archived_typed_paths returns only archived notes", m._archived_typed_paths(folder, "proj", "pattern", slug) == recon)


# ── supersede_note stamps superseded_via and moves an archived note to Archive/Superseded/ ──
print("\n# supersede_note(via=...) on an archived note")
ok = m.supersede_note(arch_path, "2026-09-10-proj-pattern-retry-policy", via="slug")
sup = folder / "Archive" / "Superseded"
moved = list(sup.glob("*.md")) if sup.exists() else []
check("supersede_note returned True", ok)
check("the note moved to Archive/Superseded/", len(moved) == 1)
if moved:
    body = moved[0].read_text(encoding="utf-8")
    check("status is superseded", "status: superseded" in body)
    check("superseded_via records the mechanism", "superseded_via: slug" in body)
    check("valid_to closes at the replacement's date", "valid_to: '2026-09-10'" in body or "valid_to: 2026-09-10" in body)


# ── end to end: a same-slug replacement closes an archived interval (the whole point) ───────
print("\n# end-to-end: replacing an archived fact closes its interval")
make_sandbox(m, "j2b_", offline=True)
folder = m.VAULT / m.TYPE_FOLDER["decision"]
s_old = m.write_typed_note("Decisions", {"title": "cache backend", "description": "use redis for the cache"},
                           "proj", "2026-01-01", ["t"], "decision")
_archive(s_old, "decision")
s_new = m.write_typed_note("Decisions", {"title": "cache backend", "description": "use memcached for the cache"},
                           "proj", "2026-09-10", ["t"], "decision")
live_now = folder / f"{s_new}.md"
arch_old = folder / "Archive" / f"{s_old}.md"
check("the replacement is live", live_now.exists() and s_new != "")
# K8: "memcached" against "redis" is not proven the same fact by the item alone, so the archived note
# is reached in Archive/ (the J2b reach) and stamped contested rather than retired on the spot
check("the archived fact is reached in Archive/ and stamped contested (K8)",
      arch_old.exists() and m._read_frontmatter_file(arch_old).get("contested") == [s_new])
import consolidate_memory as cm   # noqa: E402
adj = cm.adjudicate_contested(apply=True, has_llm=True, judge=lambda *a, **k: True)
sup = folder / "Archive" / "Superseded"
retired = list(sup.glob(f"{s_old}*.md")) if sup.exists() else []
check("the judge's `replaces` retires the archived fact into Archive/Superseded/",
      adj["replaces"] == 1 and len(retired) == 1)
if retired:
    rbody = retired[0].read_text(encoding="utf-8")
    check("its interval is closed (valid_to stamped)", "valid_to:" in rbody)
    check("the closure is attributed (superseded_via: judge)", "superseded_via: judge" in rbody)
new_body = live_now.read_text(encoding="utf-8") if live_now.exists() else ""
check("the replacement records what it supersedes", "supersedes" in new_body)

# an item that NAMES the title it replaces (rule 1) still closes an archived interval at write time
print("\n# end-to-end: an explicit supersedes closes an archived interval on the spot")
make_sandbox(m, "j2b_", offline=True)
folder = m.VAULT / m.TYPE_FOLDER["decision"]
s_old = m.write_typed_note("Decisions", {"title": "queue backend", "description": "jobs go through sqs"},
                           "proj", "2026-01-01", ["t"], "decision")
_archive(s_old, "decision")
s_new = m.write_typed_note("Decisions", {"title": "queue backend", "description": "jobs go through nats",
                                         "supersedes": "queue backend"},
                           "proj", "2026-09-10", ["t"], "decision")
sup = folder / "Archive" / "Superseded"
retired = list(sup.glob(f"{s_old}*.md")) if sup.exists() else []
check("the named replacement retires the archived fact at write time", len(retired) == 1)
if retired:
    rbody = retired[0].read_text(encoding="utf-8")
    check("attributed to the explicit path", "superseded_via: explicit" in rbody)
    check("its interval is closed", "valid_to:" in rbody)


print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
