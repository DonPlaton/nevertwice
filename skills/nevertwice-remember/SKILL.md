---
name: nevertwice-remember
description: >-
  Record durable lessons to long-term memory at the end of a task, or when the
  user says "remember this". Use when you have learned something that would save a
  FUTURE session from repeating a mistake or re-discovering a fix: a non-obvious
  gotcha, a fix that worked, an architectural decision and its rationale. Do NOT
  use for ephemeral facts, restating the obvious, or anything secret.
---

# Self-extraction into Nevertwice

You are the extractor. No separate model runs. *You* decide what was worth keeping
and write it through the existing memory write path. Nevertwice stores it as markdown
+ git and recalls it in future sessions.

## When to record

Record a lesson when, and only when, it is **durable and non-obvious**:

- **mistake**: something went wrong and a future session must avoid it. Always
  include `prevention` (the one line that would have saved you).
- **pattern**: a fix / approach / idiom that worked and is reusable.
- **decision**: a choice made and *why*, so it isn't relitigated.

Skip: transient state, restating the task, anything you'd write in a normal reply,
secrets/keys/tokens, and lessons already in memory (check first, see below).

## How to record

1. **Dedup first.** Call `memory_search` for the lesson's topic. If a near-identical
   note exists, don't duplicate it (supersede it instead via the `supersedes` field
   if your new understanding replaces it).
2. **Write each lesson** with `memory_remember`:
   - `project`: the repo/project slug (required)
   - `type`: `mistake` | `pattern` | `decision` (required)
   - `title`: a specific, searchable one-liner (required)
   - `description`: what it is, concretely
   - `prevention`: for a mistake, the avoidance rule (one line)
   - `tags`: comma-separated, optional
   - `supersedes`: title of a note this replaces, optional
3. **One lesson per call.** Two or three per task is normal; ten is a smell. Keep
   only the durable ones.

If `memory_remember`/`memory_search` aren't available as tools, fall back to the CLI
`python -m nevertwice.remember --project P --type T --title "..."` or the Python API
`nevertwice.api.remember_lessons([...], project="P")` (see docs/SELF_EXTRACTION.md).

## Example

User just spent an hour finding that a CUDA OOM came from not freeing the cache
between epochs. At end of task:

- `memory_search("cuda oom between epochs", project="myproj")` → no close hit.
- `memory_remember(project="myproj", type="mistake",
   title="CUDA OOM accumulates across epochs",
   description="VRAM grew each epoch until OOM; cause was the autograd graph kept alive",
   prevention="call torch.cuda.empty_cache() + detach metrics each epoch",
   tags="cuda,memory,training")`

Good lessons are specific enough that the future you, searching, would actually find
and trust them. Vague titles ("fixed a bug") are worse than nothing.
