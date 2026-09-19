#!/usr/bin/env python3
"""A note stem names a note. It is not a path, and nothing may treat it as one.

`parse_typed_stem` splits on `-` with `maxsplit=5` and hands back whatever the sixth field holds.
Callers then build `VAULT/<folder>/<stem>.md` from it. A stem whose slug carries `../` therefore
addresses a file anywhere on disk, and `inbox.confirm` - reachable from the CLI and from
`api.inbox_action` - rewrote an arbitrary Markdown file outside the store, stamping `reviewed:` into
it. The reviewer proved it against a temp vault with a victim file three directories up.

The fix belongs in the parser, because the parser is what every caller trusts: a stem that is not a
plain stem is not a stem. `confirm` additionally checks that the path it built is inside the folder
it meant, so a future caller that builds a path some other way still cannot escape.

While proving it, the reviewer also found that `confirm` drops every blank line in the frontmatter
while calling itself "a single-line splice". That is the same function lying about the same write,
so it is fixed and pinned here too.

    python tests/_test_stem_is_not_a_path.py
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

from _sandbox import make_sandbox  # noqa: E402

P = F = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global P, F
    if ok:
        P += 1
        print(f"  ok   {label}")
    else:
        F += 1
        print(f"  FAIL {label}" + (f" - {detail}" if detail else ""))


print("# the parser refuses a stem that is really a path")
ESCAPES = [
    "2026-01-01-proj-mistake-x/../../../victim",
    "2026-01-01-proj-mistake-..\\..\\victim",
    "2026-01-01-proj-mistake-sub/dir/name",
    "2026-01-01-proj-mistake-..",
    "2026-01-01-proj-mistake-x\x00y",
]
for bad in ESCAPES:
    check(f"refused: {bad!r}", m.parse_typed_stem(bad) is None,
          f"parsed to {m.parse_typed_stem(bad)!r}")

print("# and still parses every stem the writer actually mints")
GOOD = ["2026-01-01-proj-mistake-a-long-slug-with-dashes",
        "2026-09-19-my_app-decision-queue-backend",
        "2026-09-19-my_app-pattern-retry-with-backoff-2"]
for stem in GOOD:
    parsed = m.parse_typed_stem(stem)
    check(f"parsed: {stem}", parsed is not None and parsed["ntype"] in m.TYPED_TYPES, f"{parsed!r}")

make_sandbox(m, offline=True)
import inbox  # noqa: E402

print("# confirm cannot reach a file outside the store")
outside = m.VAULT.parent / "victim.md"
outside.write_text("---\ntitle: not ours\n---\n\nbody\n", encoding="utf-8")
before = outside.read_text(encoding="utf-8")
res = inbox.confirm("2026-01-01-proj-mistake-x/../../victim")
check("the escape is refused", not res.get("ok"), f"{res}")
check("and the file outside the store is untouched",
      outside.read_text(encoding="utf-8") == before)

print("# confirm stamps a real note, and splices one line rather than rewriting the header")
m.write_typed_note("Mistakes",
                   {"title": "flaky migration", "description": "The migration ran twice."},
                   "my_app", "2026-02-01", [], "mistake")
stem = "2026-02-01-my_app-mistake-flaky-migration"
path = m.VAULT / "Mistakes" / f"{stem}.md"
raw = path.read_text(encoding="utf-8")
head, sep, body = raw.partition("\n---")
# a header with a blank line between fields, the shape Obsidian's Properties panel leaves
path.write_text(head + "\n" + sep + body if False else
                raw.replace("\ntype:", "\n\ntype:", 1), encoding="utf-8")
fields_before = [ln for ln in path.read_text(encoding="utf-8").split("\n---")[0].split("\n") if ln.strip()]
res = inbox.confirm(stem)
check("a real stem is accepted", res.get("ok"), f"{res}")
after = path.read_text(encoding="utf-8")
fields_after = [ln for ln in after.split("\n---")[0].split("\n") if ln.strip()]
check("the review line is written", "reviewed:" in after)
check("every field that was there is still there",
      all(f in fields_after for f in fields_before),
      f"{[f for f in fields_before if f not in fields_after]} went missing")
check("and the blank line the user's editor left is not eaten",
      "\n\ntype:" in after.split("\n---")[0] or "\n\n" in after.split("\n---")[0],
      "the splice removed a blank line it did not need to touch")

print()
print(f"stem is not a path: {P} passed, {F} failed")
sys.exit(1 if F else 0)
