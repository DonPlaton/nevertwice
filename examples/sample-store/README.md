# Sample memory store

A tiny example of what Nevertwice writes to disk, so you can see the format before
running it. A real store lives at `~/.nevertwice` (override with `NEVERTWICE_HOME`).

Layout:

```
Mistakes/   Patterns/   Decisions/   - typed lesson notes (one fact per file)
Context/    - per-project "card" (status · stack · open gotchas · decisions)
Sessions/   - one note per processed session (not shown here)
graph.json  - navigable link graph (generated)
```

Each note is plain markdown with YAML frontmatter and `[[wikilinks]]` between
related notes (Obsidian renders these as a graph, but nothing requires Obsidian).
Filenames are `YYYY-MM-DD-<project>-<type>-<slug>.md`.

> Note: structural labels in the body (e.g. *«Как избежать»* = "how to avoid") are
> currently Russian. Author-language i18n is on the roadmap. The content itself
> can be any language; recall is multilingual (bge-m3).
