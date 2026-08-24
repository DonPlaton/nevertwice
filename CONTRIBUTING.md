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
python -m pip install -e ".[dev]"
python install.py --print     # see what install would do (writes nothing)
```

## Tests

The 50 standalone suites are hermetic: network, LLMs, embedders, the optional
cross-encoder and GPU execution are disabled or mocked by default. Run all of them with:

```bash
python -m pytest -q
```

Each underlying suite remains directly runnable, for example
`python tests/_test_memory_v3.py`. CI runs the core suites on Linux, Windows and
macOS across Python 3.10/3.12/3.13/3.14, the research suites on Python 3.13, and a
`packaging` job that installs the dev extra, runs the command above, and checks the
version contract. Add a regression check for every behaviour change. The suites assert
on real files written to a throwaway store.

Every suite's first project import must be `_env_guard` - it pins the store to a fresh
temp directory, so a shell that exports `NEVERTWICE_VAULT` cannot make a test write into
a live memory store. `tests/_test_hermeticity.py` fails if a suite forgets.

## Releases

One version string lives in `nevertwice/config.py`; `pyproject.toml`, the MCP server, the
git tag and the built distribution's metadata must all agree with it. Check before tagging:

```bash
python tools/check_version.py            # pyproject / runtime / MCP / tag
python -m build && python tools/check_version.py --dist dist
```

`--release` additionally demands that HEAD carry the matching `vX.Y.Z` tag.

## Commits

`type(scope): description` (feat / fix / perf / refactor / docs / test / chore /
research - the last one for work under `research/`). Keep commits small and atomic.
