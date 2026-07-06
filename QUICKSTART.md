# Quickstart: zero to recall in 5 minutes

One path, no decisions. Tuning lives in [docs/CONFIG.md](docs/CONFIG.md); you don't
need any of it yet.

## 1. Get it

```bash
git clone https://github.com/DonPlaton/nevertwice && cd nevertwice
```

Nothing to `pip install`. The core is standard-library Python (3.10+).

## 2. Install: one command

```bash
python install.py
```

This creates your memory store at `~/.nevertwice` (plain Markdown + Git), wires the four
Claude Code hooks, and **prints the backend it auto-detected**. For example:

```
[backends] auto-detected (zero config - override in .env only if you want to):
  extraction : local Ollama qwen3:8b  (no key needed)
  recall     : local Ollama bge-m3  (semantic + lexical, hybrid)
```

That's the whole setup. You did not edit a single config value.

Want local semantic recall and don't have [Ollama](https://ollama.com) yet? Install it,
then:

```bash
python install.py --ollama     # pulls bge-m3 (embedder) + an extraction model
```

**No Ollama and no cloud key?** Nevertwice still works: recall runs on lexical full-text
search (FTS5), and extraction *pauses* (sessions are kept and retried, never dropped)
until a backend appears. Nothing to configure either way.

## 3. Use it: there is nothing to do

Open Claude Code and work as usual. Nevertwice captures each session automatically and, at
the start of the next one, injects the project card plus the lessons relevant to what
you're doing. The agent just already knows.

Memory builds from what you do, so the first session or two mostly *records*. Recall kicks in
as soon as there's something relevant to recall, and the guards that catch a repeat mistake
appear once that mistake has happened once. Give it a few sessions and it starts paying you back.

## 4. Watch it remember

See the whole loop in 25 seconds on a throwaway store (your real vault is untouched), on any OS:

```bash
python examples/demo.py
```

It seeds three lessons, then recalls the right one from a fresh prompt and abstains on nonsense.
For the whole machine at once - a guard firing before a repeat mistake, anticipation, the causal
graph, supersession, every number measured live - run `python examples/scenario_demo.py`.
Once you have captured real sessions, search the memory by hand any time:

```bash
python nevertwice/memory_search.py "that bug with the database connection" myproject
```

```
⚠ 1 relevant lesson recalled (confidence 0.71):
  • [mistake] connection pool exhausted under load
    → set pool_pre_ping + a max_overflow ceiling; it recurred twice.
```

If nothing confident matches, you get *"no confident match"*, never a confident wrong
answer.

---

That's the happy path. When you want more:

- **A different agent** (Cursor, Aider, Cline, Codex, Windsurf…)? → [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md),
  including `nevertwice watch`, which makes auto-capture always-on for any of them.
- **Tune anything** (cloud backend, embedder, retrieval, retention)? → [docs/CONFIG.md](docs/CONFIG.md)
- **How and why it works?** → [README.md](README.md) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
