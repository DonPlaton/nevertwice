#!/usr/bin/env python3
"""`_rebase_vault` moves EVERY vault-derived constant, including the ones that classify paths.

The call is the one thing a test or a tool uses to move the store into a sandbox, and its
docstring has promised "no forgotten constants" since two incidents (2026-08-13, 2026-08-18)
wrote into the owner's live vault because a partial patch left a constant pointing at it.

Three were still forgotten: `_VAULT_NORM`, `_PROJECTS_ROOT_NORM` and the `_EXCLUDE_PREFIXES`
built from them are computed at import from `VAULT`/`PROJECTS_ROOT`. Every file went to the
sandbox while every *question* about a path was still answered about the store the process
imported with - `_is_excluded_path` kept excluding the real store and merely tracking the
sandbox. No suite caught it because they all rebase and then ask about files, never about
classification.

    python tests/_test_rebase_total.py
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

import _env_guard  # noqa: F401,E402  hermetic: scrub store env before the engine bakes its paths
import memory_hook as m  # noqa: E402

from _sandbox import make_sandbox  # noqa: E402

P = F = 0


def check(name, cond):
    global P, F
    if cond:
        P += 1
        print(f"  [OK ] {name}")
    else:
        F += 1
        print(f"  [FAIL] {name}")


print("# _rebase_vault is total")

before = m._VAULT_NORM
sandbox = make_sandbox(m, "rebase_")
norm = m._norm_path(str(sandbox))

check("VAULT moved", m._norm_path(str(m.VAULT)) == norm)
check("_VAULT_NORM moved", m._VAULT_NORM == norm)
check("_EXCLUDE_PREFIXES holds the sandbox", norm in m._EXCLUDE_PREFIXES)

# The leak this pins: the store the module was IMPORTED with stays in the exclusion list for the
# rest of the process, so every path question after a rebase is still answered about a store the
# caller has left. That is the shape of both 2026-08 incidents.
check("the pre-rebase store is gone from _EXCLUDE_PREFIXES",
      before != norm and before not in m._EXCLUDE_PREFIXES)

other = Path(tempfile.mkdtemp(prefix="rebase_projects_"))
m.PROJECTS_ROOT = other
m._rebase_vault(sandbox)
check("_PROJECTS_ROOT_NORM follows PROJECTS_ROOT",
      m._PROJECTS_ROOT_NORM == m._norm_path(str(other)))

# ── and the grounding caches, which are the store's CONTENT rather than its paths ─────
# The constants above answer "where"; these three answer "what is in there", and they are built
# from the notes of whatever store was mounted when they were first asked. A rebase left all
# three describing the store we just left: the extractor was shown the old vault's tag
# vocabulary and its recent titles as the dedup window, and near-duplicate detection compared
# against the old vault's embedding cache. Same incident shape as the two the docstring names,
# one layer further in - the previous store's content is not this store's content.
print("# the grounding caches are the store's content, and they move with it")
first = Path(tempfile.mkdtemp(prefix="rebase_ground_a_"))
m._rebase_vault(first)
for folder in ("Mistakes", "Patterns", "Decisions"):
    (first / folder).mkdir(parents=True, exist_ok=True)
for n in (1, 2):
    (first / "Mistakes" / f"2026-05-0{n}-alpha-mistake-only-in-the-first-store-{n}.md").write_text(
        "---\nproject: alpha\ndate: 2026-05-0" + str(n) + "\n"
        # the tag vocabulary is built from what a note DECLARES, as every engine-written
        # note does - the body line is rendered from this list, not the other way round.
        + 'tags: ["only_in_the_first_store"]\ntype: mistake\n---\n\n'
        "# only in the first store\n\n#only_in_the_first_store\n", encoding="utf-8")

tags_a = m.collect_existing_tags()
titles_a = m.collect_existing_titles("alpha")
m._NDUP_MEMO[0] = 1234.5
m._NDUP_MEMO[1] = {"a-stem-from-the-first-store": {"vec": [0.0]}}
check("the first store grounded the tag vocabulary",
      any("only_in_the_first_store" in t for t in tags_a))
check("and the title window", any("only-in-the-first-store" in s
                                 for slugs in titles_a.values() for s in slugs))

second = Path(tempfile.mkdtemp(prefix="rebase_ground_b_"))
for folder in ("Mistakes", "Patterns", "Decisions"):
    (second / folder).mkdir(parents=True, exist_ok=True)
m._rebase_vault(second)
check("the tag vocabulary stops naming the store we left",
      not any("only_in_the_first_store" in t for t in m.collect_existing_tags()))
check("the title window stops naming it too",
      not any("only-in-the-first-store" in s
              for slugs in m.collect_existing_titles("alpha").values() for s in slugs))
check("and the near-duplicate memo is not the previous store's cache",
      m._NDUP_MEMO[0] is None and m._NDUP_MEMO[1] is None)

print()
print(f"rebase: {P} passed, {F} failed")
sys.exit(1 if F else 0)
