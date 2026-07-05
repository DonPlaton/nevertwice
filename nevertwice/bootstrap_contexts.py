#!/usr/bin/env python3
"""
Bootstrap Context/<project>.md для существующих проектов.
Читает README + ключевые конфиги + структуру верхнего уровня,
просит локальную модель построить структурированный контекст,
пишет в Markdown vault с wikilinks и тегами.

Запуск: python bootstrap_contexts.py <project_path> [<project_path>...]
"""

import sys, json, re, os
from pathlib import Path
from datetime import datetime
import urllib.request
import urllib.error

try:                                      # never crash printing → / Cyrillic on a cp1251 console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Prefer the shared Gemini→Ollama backend from memory_hook (fast, off-GPU);
# fall back to this script's own Ollama call if it can't be imported.
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from memory_hook import generate_json as _shared_generate, redact_secrets
except Exception:
    _shared_generate = None

    def redact_secrets(t):  # fallback no-op if memory_hook can't be imported
        return t


def _backend(prompt, project=None):
    # respect per-project local-only routing so a sensitive project's config
    # files are never sent to the cloud during bootstrap
    if _shared_generate:
        try:
            r = _shared_generate(prompt, project=project)
            if r:
                return r
        except Exception:
            pass
    return call_ollama(prompt)


try:
    from . import config as _cfg
except ImportError:
    import config as _cfg
VAULT = _cfg.VAULT
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
# Model default from the shared config (audit L-b): the round-1 hardcoded
# "qwen3:32b" had drifted from the system default and left two different model
# defaults in one package. Pull it from memory_hook so there is one source.
try:
    from memory_hook import OLLAMA_MODEL
except Exception:
    OLLAMA_MODEL = os.environ.get("NEVERTWICE_MODEL", "qwen3:8b")
OLLAMA_TIMEOUT = 240

CONFIG_FILES = [
    "README.md", "README.rst", "README.txt", "README",
    "pyproject.toml", "setup.py", "requirements.txt", "Pipfile",
    "package.json", "tsconfig.json",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "Dockerfile", "docker-compose.yml",
    ".env.example", "config.yaml", "config.yml",
    "Makefile",
]
SKIP_DIRS = {'.git','__pycache__','node_modules','.venv','venv','env','dist','build',
             '.next','.nuxt','target','.claude','.idea','.vscode','.pytest_cache',
             'data','datasets','models','checkpoints','logs','wandb','outputs',
             '.mypy_cache','.ruff_cache','htmlcov','.tox'}
TEXT_EXTS = {'.py','.js','.ts','.tsx','.jsx','.rs','.go','.java','.cpp','.c','.h','.cs',
             '.rb','.php','.md','.json','.yaml','.yml','.toml','.sh','.bat','.ps1',
             '.swift','.kt','.scala','.r','.jl','.cu','.cuh','.v','.sv','.vhd','.vhdl'}

EXTRACTION_PROMPT = """Проанализируй структуру проекта и извлеки контекст в JSON.

ПРОЕКТ: {project}
ПУТЬ: {path}

КОНФИГИ И README:
{configs}

СТРУКТУРА (top-level):
{structure}

СТАТИСТИКА:
{stats}

Верни ТОЛЬКО валидный JSON:
{{
  "description": "что это за проект (1-2 предложения, по существу)",
  "stack": ["Python 3.12", "PyTorch", "CUDA", "..."],
  "purpose": "цель и контекст (исследовательский / продуктовый / учебный, что решает)",
  "current_state": "текущее состояние (если упомянуто в README/коде, иначе 'in development')",
  "structure_overview": "что где лежит (2-4 предложения о структуре)",
  "key_files": ["относительный/путь/к/важному/файлу.py", "..."],
  "publication_target": "если research-проект - куда планируется (Zenodo/arXiv/конференция), иначе ''",
  "tags": ["ml", "research", "pytorch", "..."],
  "next_steps": ["что логично делать дальше", "..."]
}}

Никакого markdown, никаких пояснений вне JSON. Будь конкретен и краток."""


def slugify(s, max_len=55):
    s = re.sub(r'[<>:"/\\|?*\n\r\t]', ' ', s or "")
    s = re.sub(r'\s+', '-', s.strip())
    s = re.sub(r'-+', '-', s).strip('-')
    return s[:max_len].lower() or "untitled"


def slug_project(name):
    """Match memory_hook.slug_project: lowercase, '_'-joined, never '-'. Keeps
    bootstrap-created Context filenames consistent with the hook so they never
    case-split (audit F9)."""
    s = slugify(name, 40)
    if s == "untitled":
        return "general"
    return s.replace('-', '_').strip('._') or "general"


def slug_tag(t):
    t = (t or "").strip().replace(' ', '_')
    return re.sub(r'[^\w/]', '', t)


def render_body_tags(*groups):
    seen, out = set(), []
    for g in groups:
        for t in (g or []):
            tag = slug_tag(t)
            if tag and tag not in seen:
                seen.add(tag)
                out.append(f"#{tag}")
    return " ".join(out)


_YAML_NEEDS_QUOTE = re.compile(r'''[:#&*!|>'"%@`{}\[\],]|^\s|\s$''')


def _yaml_scalar(v):
    """Quote YAML scalars that would otherwise be ambiguous - notably Windows
    paths whose drive-letter colon broke parsing (audit F8). Mirrors the hook."""
    s = str(v)
    if s == "" or _YAML_NEEDS_QUOTE.search(s):
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def fm_block(fm):
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        else:
            lines.append(f"{k}: {_yaml_scalar(v)}")
    lines.append("---")
    return "\n".join(lines)


def collect_configs(root: Path) -> str:
    parts = []
    for name in CONFIG_FILES:
        fp = root / name
        if fp.exists() and fp.is_file():
            try:
                content = fp.read_text(encoding="utf-8", errors="ignore")
                if len(content) > 3000:
                    content = content[:3000] + "\n[truncated]"
                parts.append(f"=== {name} ===\n{content}")
            except Exception:
                pass
    return "\n\n".join(parts) if parts else "(no recognized config files)"


def collect_structure(root: Path) -> tuple[str, dict]:
    """Top-level dirs + sample of code files."""
    lines = []
    ext_counts = {}
    total_files = 0
    total_size = 0

    try:
        entries = sorted(root.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except Exception:
        return "(unreadable root)", {}

    dirs, files = [], []
    for e in entries:
        if e.name.startswith('.') or e.name in SKIP_DIRS:
            continue
        if e.is_dir():
            dirs.append(e.name)
        else:
            files.append(e.name)

    if dirs:
        lines.append("Папки: " + ", ".join(dirs[:30]))
    if files:
        lines.append("Файлы: " + ", ".join(files[:25]))

    # walk for code-file sample
    code_samples = []
    for r, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith('.')]
        rp = Path(r)
        for fn in fns:
            fp = rp / fn
            ext = fp.suffix
            try:
                size = fp.stat().st_size
            except Exception:
                continue
            if size > 5_000_000:
                continue
            total_files += 1
            total_size += size
            if ext in TEXT_EXTS:
                ext_counts[ext] = ext_counts.get(ext, 0) + 1
                if ext in {'.py','.js','.ts','.rs','.go','.cpp'} and len(code_samples) < 20:
                    rel = str(fp.relative_to(root)).replace('\\','/')
                    code_samples.append(rel)
        if total_files > 5000:
            break  # safety

    if code_samples:
        lines.append("Примеры кода: " + ", ".join(code_samples[:15]))

    stats = {
        "total_files_scanned": total_files,
        "total_size_mb": round(total_size / 1024 / 1024, 1),
        "top_extensions": sorted(ext_counts.items(), key=lambda x: -x[1])[:6],
    }
    return "\n".join(lines), stats


def call_ollama(prompt: str) -> dict:
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.2, "num_ctx": 16384}
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as r:
            data = json.loads(r.read())
            raw = data.get("response", "").strip()
            if not raw:
                return {}
            raw = re.sub(r"^```json|^```|```$", "", raw, flags=re.M).strip()
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
    except Exception as e:
        print(f"  [error] Ollama: {e}", file=sys.stderr)
        return {}


def write_context(project_name: str, project_path: Path, ctx: dict):
    fp = VAULT / "Context" / f"{project_name}.md"
    fp.parent.mkdir(exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H:%M")

    tags = ctx.get("tags", []) or []
    body_tags = render_body_tags(tags, [f"project/{project_name}", "context"])
    description = ctx.get("description", "")
    stack = ctx.get("stack", []) or []
    purpose = ctx.get("purpose", "")
    state = ctx.get("current_state", "")
    overview = ctx.get("structure_overview", "")
    key_files = ctx.get("key_files", []) or []
    pub = ctx.get("publication_target", "")
    next_steps = ctx.get("next_steps", []) or []

    fm = {
        "project": project_name,
        "path": str(project_path),
        "tags": tags,
        "type": "context",
        "bootstrapped": date,
    }

    sections = [
        fm_block(fm),
        "",
        f"# {project_name}",
        "",
        description or "_(описание не извлечено)_",
        "",
        f"**Путь:** `{project_path}`",
        f"**Bootstrapped:** {date} {time_str}",
        "",
    ]

    if purpose:
        sections += ["## Цель и контекст", "", purpose, ""]

    if stack:
        sections += ["## Стек", "", "- " + "\n- ".join(stack), ""]

    if overview:
        sections += ["## Структура", "", overview, ""]

    if key_files:
        sections += ["## Ключевые файлы", ""]
        for f in key_files:
            sections.append(f"- `{f}`")
        sections.append("")

    if state:
        sections += ["## Текущее состояние", "", state, ""]

    if pub:
        sections += ["## Публикация", "", pub, ""]

    if next_steps:
        sections += ["## Следующие шаги", "", "- " + "\n- ".join(next_steps), ""]

    sections += [
        "---",
        "",
        "## История сессий",
        "",
        "_(автоматически обновляется hook'ом после каждой сессии)_",
        "",
        body_tags,
    ]

    fp.write_text("\n".join(sections), encoding="utf-8")
    print(f"  [ok] Context/{project_name}.md")


def rebuild_index():
    """Перестроить Index.md из текущего vault."""
    fp = VAULT / "Index.md"
    ctx_dir = VAULT / "Context"
    sess_dir = VAULT / "Sessions"

    projects = []
    if ctx_dir.exists():
        for cf in sorted(ctx_dir.glob("*.md")):
            mtime = datetime.fromtimestamp(cf.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            projects.append((cf.stem, mtime))

    sessions = []
    if sess_dir.exists():
        files = sorted(sess_dir.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)[:20]
        for sf in files:
            mtime = datetime.fromtimestamp(sf.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            sessions.append((sf.stem, mtime))

    lines = [
        "# Claude Memory Vault - Index",
        "",
        "> Точка входа. Claude Code читает ТОЛЬКО этот файл при старте сессии.",
        "> Не сканируй все папки - переходи через wikilinks из этого индекса.",
        "",
        "## Структура",
        "",
        "| Папка | Что хранится |",
        "|---|---|",
        "| Patterns/ | Паттерны и подходы которые сработали |",
        "| Mistakes/ | Ошибки, баги, антипаттерны - чего избегать |",
        "| Decisions/ | Архитектурные решения с обоснованием |",
        "| Context/ | Состояние каждого проекта (один файл = один проект) |",
        "| Sessions/ | Автологи сессий (последние 30 дней) |",
        "",
        "## Активные проекты",
        "",
    ]
    if projects:
        lines += ["| Проект | Обновлён |", "|---|---|"]
        for name, mtime in projects:
            lines.append(f"| [[{name}]] | {mtime} |")
    else:
        lines.append("_(пока нет проектов)_")
    lines += ["", "## Последние сессии", ""]
    if sessions:
        for name, mtime in sessions:
            lines.append(f"- **{mtime}** - [[{name}]]")
    else:
        lines.append("_(пока нет сессий)_")
    lines += ["", "#index"]

    fp.write_text("\n".join(lines), encoding="utf-8")
    print(f"[ok] Index rebuilt ({len(projects)} projects)")


def process(project_path: Path):
    name = slug_project(project_path.name)  # lowercase slug - consistent with hook (F9)
    print(f"\n=== {project_path.name} → {name} ===")
    if not project_path.exists():
        print(f"  [skip] not found")
        return
    # NEVER clobber an accumulated Context card: bootstrap is a one-time SEED, but the hook
    # then rolls real session history into Context/<project>.md (the per-project source of
    # truth). Re-running bootstrap over it would overwrite that memory - so skip an existing
    # card unless --force is given.
    ctx_fp = VAULT / "Context" / f"{name}.md"
    if ctx_fp.exists() and "--force" not in sys.argv:
        print(f"  [skip] {name}.md already exists - not overwriting accumulated memory "
              f"(pass --force to re-seed)")
        return

    # configs (.env/docker-compose/Makefile/...) routinely carry credentials and
    # now go to Gemini cloud - scrub before they leave the machine (audit C2)
    configs = redact_secrets(collect_configs(project_path))
    structure, stats = collect_structure(project_path)
    structure = redact_secrets(structure)

    prompt = EXTRACTION_PROMPT.format(
        project=name,
        path=str(project_path),
        configs=configs[:8000],
        structure=structure,
        stats=json.dumps(stats, ensure_ascii=False),
    )

    print(f"  [info] configs={len(configs)}ch, files_scanned={stats.get('total_files_scanned')}")
    print(f"  [info] calling {OLLAMA_MODEL}...")

    ctx = _backend(prompt, project=name)
    if not ctx:
        print(f"  [error] no extraction")
        return
    write_context(name, project_path, ctx)


def main():
    paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not paths:
        print("Usage: python bootstrap_contexts.py <project_path> [<project_path>...] [--force]")
        sys.exit(1)

    for arg in paths:
        process(Path(arg).resolve())

    rebuild_index()
    print("\n[done]")


if __name__ == "__main__":
    main()
