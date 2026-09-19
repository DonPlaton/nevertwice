#!/usr/bin/env python3
"""A comparison is normalised on both sides, or it is not a comparison.

Two places compared a project name that had been through `slug_project()` against one that had not.

`is_local_only` is the worse of the two, because it is a privacy control the README sells: "pins
projects local for good". Its sets are built from the environment with `.strip().lower()`, while
every caller passes a slug - lower-cased, transliterated, punctuation folded to `_`, truncated at 40.
So `NEVERTWICE_LOCAL_ONLY=my-secret-research`, written exactly as `docs/CONFIG.md` describes it,
never matches the slug `my_secret_research`, the gate reads False, and the session goes to the cloud
with no warning and no log line. A Cyrillic directory name cannot match at all: it transliterates.
The allowlist fails the other way - `NEVERTWICE_CLOUD_ONLY=my-public-project` matches nothing, so
every project stays local and the extractor silently drops to Ollama forever.

The second is `memory_search --as-of`, which hands the raw argument to `m.as_of` while `search_core`
190 lines above slugs it, with a comment naming the review that made it. `nevertwice-search q My-App
--as-of=...` reads zero beliefs for a project that has history.

    python tests/_test_slug_both_sides.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

P = F = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global P, F
    if ok:
        P += 1
        print(f"  ok   {label}")
    else:
        F += 1
        print(f"  FAIL {label}" + (f" - {detail}" if detail else ""))


print("# the local-only denylist matches the name the user wrote, however it slugs")
CASES = [
    ("my-secret-research", "a hyphen, the shape docs/CONFIG.md uses"),
    ("My Secret Research", "spaces and capitals, the shape a directory has"),
    ("dead-pixel-detector", "a real project from the owner's tree"),
    ("Квантовый_призм", "Cyrillic, which the slug transliterates away entirely"),
]
saved_local, saved_cloud = set(m.LOCAL_ONLY_PROJECTS), set(m.CLOUD_ONLY_PROJECTS)
try:
    m.CLOUD_ONLY_PROJECTS = set()
    for raw, why in CASES:
        m.LOCAL_ONLY_PROJECTS = m._normalise_project_set([raw])
        slug = m.slug_project(raw)
        check(f"{raw!r} stays local ({why})", m.is_local_only(slug),
              f"slug is {slug!r} and the configured set is {m.LOCAL_ONLY_PROJECTS}")

    print("# the allowlist lets the named project through, and nothing else")
    m.LOCAL_ONLY_PROJECTS = set()
    m.CLOUD_ONLY_PROJECTS = m._normalise_project_set(["my-public-project"])
    check("the allowlisted project may use the cloud",
          not m.is_local_only(m.slug_project("my-public-project")))
    check("an unlisted project still stays local",
          m.is_local_only(m.slug_project("some-other-project")))

    print("# an empty or missing project is local in both modes - the fail-safe direction")
    check("empty under an allowlist", m.is_local_only(""))
    m.CLOUD_ONLY_PROJECTS = set()
    check("empty under a denylist is not forced local", not m.is_local_only(""))
finally:
    m.LOCAL_ONLY_PROJECTS, m.CLOUD_ONLY_PROJECTS = saved_local, saved_cloud

print("# and as_of answers the same for a raw project name as for its slug")
src = (ROOT / "nevertwice" / "memory_search.py").read_text(encoding="utf-8")
check("the --as-of branch passes a slug too, as search_core does",
      "m.as_of(m.slug_project(" in src or "as_of(slug_project(" in src,
      "the raw argument reaches as_of, which compares it against slugged stems")

from _sandbox import make_sandbox  # noqa: E402

make_sandbox(m, offline=True)
m.write_typed_note("Decisions",
                   {"title": "queue backend", "description": "RabbitMQ is the queue backend."},
                   "my_app", "2026-02-01", [], "decision")
raw = m.as_of("My-App", "2026-03-01")
slugged = m.as_of("my_app", "2026-03-01")
check("the slug form finds the belief", len(slugged) == 1, f"{slugged}")
check("and the name as a human would type it finds the same one",
      len(raw) == len(slugged), f"raw {len(raw)} vs slugged {len(slugged)}")

print("# the recurrence ceiling holds on the metadata surfaces, not only in the ranker")
POISON = m.VAULT / "Mistakes" / "2026-02-02-my_app-mistake-poisoned.md"
POISON.parent.mkdir(parents=True, exist_ok=True)
POISON.write_text("\n".join(["---", "project: my_app", "type: mistake",
                             "recurrence: 999999999", "---", "", "# poisoned", "",
                             "something broke.", ""]), encoding="utf-8")
meta = m._note_meta_for_stem(POISON.stem) or {}
check("_note_meta caps a claimed recurrence at RECUR_COUNT_CAP",
      meta.get("recurrence") == m.RECUR_COUNT_CAP,
      f"read {meta.get('recurrence')!r} where the ranker reads "
      f"the cap is {m.RECUR_COUNT_CAP}")
POISON.write_text(POISON.read_text(encoding="utf-8").replace("999999999", "-5"), encoding="utf-8")
neg = m._note_meta_for_stem(POISON.stem) or {}
check("and a negative one does not read back negative", neg.get("recurrence", 0) >= 1,
      f"{neg.get('recurrence')!r}")
POISON.unlink()

print("# a session's own tags reach the counter the next session is grounded on")
m.collect_existing_tags.cache_clear()
m.collect_existing_tags(project="my_app")            # build the caches from disk, as a sweep does
before = dict(m._TAG_COUNTS_BY_PROJECT.get("my_app") or {})
m.register_written_notes("my_app", ["brand-new-tag", "another-new-tag", "third-new"], {})
after = dict(m._TAG_COUNTS_BY_PROJECT.get("my_app") or {})
new_tags = set(after) - set(before)
check("the fold reaches the per-project counter production reads", len(new_tags) == 3,
      f"before {sorted(before)} -> after {sorted(after)}; `collect_existing_tags(project=...)` "
      f"answers from this dict and never consults the global one once it has five entries")
check("and the global counter still moves too",
      all(t in (m._TAG_COUNTS or {}) for t in new_tags), f"{sorted(m._TAG_COUNTS or {})}")

print()
print(f"slug both sides: {P} passed, {F} failed")
sys.exit(1 if F else 0)
