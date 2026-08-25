# Recorded migration fixtures

One small export per source, hand-written from each tool's documented shape, so
`tests/_test_migrate.py` can round-trip all five without an account, a network call, or a copy
of anyone's real memory.

Each carries provenance the importer has to preserve - an author, a timestamp, a reference -
and each is deliberately awkward in one way, because a parser that only handles the obvious
shape is a parser that works once:

* `claude-memory/`     - Markdown with frontmatter; one file omits the author.
* `claude-mem.sqlite`  - columns named `label`/`observation`/`kind`/`user`/`ts`, none of them
  the names a reader would guess, so the importer has to find the content column rather than
  assume it.
* `mem0-export.json`   - wrapped in `results`, with the type hidden in `metadata`.
* `letta-archive.json` - core blocks and archival passages together, which are different kinds
  of claim and must not be flattened into one.
* `generic/`           - Markdown bullets and JSONL side by side, the fallback shape.
