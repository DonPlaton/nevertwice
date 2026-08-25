---
title: "The structural labels inside a note are Russian, whatever language you write in"
labels: documentation, good first issue
---

<!--
A draft, not a filed issue. Filing these is a maintainer action; the text is kept here so the
work is described even before the issue is opened.
-->

## The problem

Nevertwice recall is multilingual - `bge-m3` handles that - and the *content* of a note is in
whatever language the session was in. But the **structural labels** the writer emits inside
each note body are hardcoded Russian, because the author works in Russian. An English-speaking
user gets a note whose content is English and whose section headings are not.

`examples/sample-store/README.md` admits this in a footnote today. A footnote is the right
place for a known limit and the wrong place for one that has a clear fix.

## What to do

1. Find the labels. They are emitted where the note body is composed, not where it is read, so
   grep the writer path rather than the whole package.
2. Move them into one mapping keyed by language code, with English as the default and Russian
   as the second entry - not a translation framework, just a dictionary and a lookup.
3. Pick the language from configuration, not from guesswork about the content: a
   `NEVERTWICE_NOTE_LANG` variable documented in `docs/CONFIG.md`, defaulting to `en`.
4. Leave existing notes alone. They are the user's data, and silently rewriting them is
   exactly what a memory store must never do. If a migration is wanted it is a separate,
   opt-in command with a dry run - a second issue, not this one.
5. Add a test that a note written under each configured language carries that language's
   labels, and that an unknown code falls back to English rather than raising.

## Done when

- a default install writes English structural labels;
- `NEVERTWICE_NOTE_LANG=ru` reproduces today's output byte for byte, so nothing regresses for
  the existing store;
- `docs/CONFIG.md` documents the variable and the footnote in
  `examples/sample-store/README.md` is replaced by a statement of what it does;
- `python -m pytest -q` is green.

## Where to look

- `examples/sample-store/` - four real notes, and the shape the labels have to keep.
- `docs/CONFIG.md` - where every environment variable is documented, with its default.
- `nevertwice/config.py` - how a variable is read, including the legacy-prefix bridge you get
  for free.

## Why this is worth doing

It is the smallest change in the repository with the largest effect on whether a stranger
believes the project was built for them. Everything else about the store is
language-agnostic; this is the one place it is not.
