#!/usr/bin/env python3
"""Agent self-write (I-5) - let an agent (or you) commit a memory mid-task or
retire a wrong one, instead of only receiving memory passively. The universal
interface is this CLI, so ANY agent can call it (like ingest.py / memory_search.py).

GPU-free: writing a note never embeds (embedding is a separate step). The note is
live and human-readable immediately, found via lexical/recency recall right away,
and folded into semantic recall on the next `embed_index.py` run (free GPU time).

    # remember a lesson now
    python remember.py --project project_delta --type mistake \
        --title "stale cache after schema change" \
        --desc "old rows survived the migration and poisoned reads" \
        --prevention "bump cache version on every schema change"

    # retire a note that turned out wrong (moves it to Superseded/)
    python remember.py --forget 2026-06-10-project_delta-mistake-stale-cache-after-schema-change
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import memory_hook as m
import api


def _find_note(stem: str):
    parsed = m.parse_typed_stem(stem)
    if not parsed:
        return None, None
    folder = m.TYPE_FOLDER.get(parsed["ntype"])
    if not folder:
        return None, None
    fp = m.VAULT / folder / f"{stem}.md"
    return (fp, folder) if fp.exists() else (None, folder)


def do_forget(stem: str) -> int:
    fp, folder = _find_note(stem)
    if not fp:
        print(f"[remember] live note not found: {stem}", file=sys.stderr)
        return 1
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"[remember] read failed: {e}", file=sys.stderr)
        return 1
    text = m._stamp_frontmatter(text, {"status": "forgotten",
                                       "forgotten_at": datetime.now().isoformat(timespec="seconds")})
    dest = fp.parent / "Superseded"
    dest.mkdir(exist_ok=True)
    m.write_atomic(dest / fp.name, text)
    fp.unlink(missing_ok=True)
    cache = m.load_embed_cache()
    if cache.pop(stem, None) is not None:
        m.save_embed_cache(cache)
    m.sync_scale_index(delete=[stem])     # drop from the SQLite index too (C2/C3)
    m.rebuild_index()
    m.git_autocommit()
    print(f"[remember] forgotten → {folder}/Superseded/{fp.name}")
    return 0


def do_remember(a) -> int:
    # Single write path: delegate to the in-process library API (nevertwice.api),
    # mapping its result/exceptions back to this CLI's return-code contract so the
    # lock/embed/commit sequence lives in exactly one place (no CLI/lib drift).
    try:
        stem = api.remember(a.title, project=a.project, type=a.type,
                            description=a.desc or "", prevention=a.prevention or "",
                            tags=a.tags or "", supersedes=a.supersedes or "")
    except ValueError as e:
        print(f"[remember] {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"[remember] {e}", file=sys.stderr)
        return 3
    if not stem:
        # write_typed_note returns "" for EITHER an injection-shaped payload OR (when
        # NEVERTWICE_QUARANTINE=1) a single uncorroborated note diverted to Quarantine/.
        # Don't assert "injection" - that misreports a quarantined write (audit 2026-06-18).
        print("[remember] not written to active memory: content is injection-shaped, or was "
              "quarantined as a single uncorroborated source (NEVERTWICE_QUARANTINE)",
              file=sys.stderr)
        return 2
    print(f"[remember] wrote {a.type} → {stem}")
    # ground truth: a note is semantically recallable only once it's in the embed
    # cache (un-embedded notes are invisible to recall - audit A15). Report honestly.
    print("  (recallable now)" if stem in m.load_embed_cache() else
          "  (written - run `python -m nevertwice.embed_index` to make it searchable)")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Write or retire a memory note (agent self-write).")
    ap.add_argument("--project")
    ap.add_argument("--type", default="pattern", help="pattern|mistake|decision")
    ap.add_argument("--title")
    ap.add_argument("--desc", default="")
    ap.add_argument("--prevention", default="")
    ap.add_argument("--tags", default="", help="comma-separated")
    ap.add_argument("--supersedes", default="", help="title of a note this replaces")
    ap.add_argument("--agent", default=m.DEFAULT_AGENT)
    ap.add_argument("--forget", metavar="STEM", help="retire a note by stem")
    a = ap.parse_args()
    sys.exit(do_forget(a.forget) if a.forget else do_remember(a))


if __name__ == "__main__":
    main()
