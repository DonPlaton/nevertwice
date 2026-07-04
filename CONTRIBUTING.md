# Contributing

Thanks for your interest! Nevertwice aims to stay **local-first, dependency-light, and
simple to deploy**. Please keep changes in that spirit.

## Principles

- **No required runtime dependencies.** The core is standard-library Python; Ollama and
  cloud APIs are reached over plain HTTP. Don't add a hard dependency without a strong
  reason.
- **Markdown + git is the source of truth.** Any index (e.g. SQLite) must be *derived*
  and rebuildable from the markdown, never the authority.
- **Cross-platform.** Use `pathlib` / `os.path`; route paths through `config.py`. Avoid
  hard-coded separators or absolute paths.
- **Fail safe.** Memory must never block or break the agent. Best-effort everywhere;
  on error, do less, not crash.

## Dev setup

```bash
git clone https://github.com/DonPlaton/nevertwice && cd nevertwice
python install.py --print     # see what install would do (writes nothing)
```

## Tests

Standard-library only, fully mocked (no network, no GPU). Run all three:

```bash
python nevertwice/_test_memory_hook.py
python nevertwice/_test_memory_v2.py
python nevertwice/_test_memory_v3.py
```

CI runs them on Linux, Windows and macOS across Python 3.10/3.12/3.13. Add a test for
any behaviour change. The suites assert on real files written to a throwaway store.

## Commits

`type(scope): description` (feat / fix / refactor / docs / test / chore). Keep commits
small and atomic.
