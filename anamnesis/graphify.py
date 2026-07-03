#!/usr/bin/env python3
"""
Graphify — a code-graph generator for coding agents.
Writes graph.json at the project root: a light index of files with their imports and
exports, which an agent reads for navigation instead of scanning every file. The real
token saving is computed on the fly (stats.savings_*) and depends on the project — on
large codebases the graph only pays off if it stays small, hence the hard caps below
(audit F20/F21/F22: a bloated graph.json costs more than reading the right files).
Run: python graphify.py [project_path] [--incremental]
  --incremental: rebuild only when sources are newer than graph.json
"""

import os, sys, json, ast, re
from pathlib import Path
from datetime import datetime

try:                                      # never crash printing non-ASCII on a cp1251 console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Refuse to graph the whole projects root (audit F22: that produced an 8.6 MB
# whole-disk dump). Only individual project subdirs are valid targets. Default to the
# current working directory (the project you're in) — never a hard-coded machine path.
PROJECT_ROOT = Path(os.environ.get("ANAMNESIS_PROJECT_ROOT") or os.getcwd())

SKIP_DIRS = {'.git','__pycache__','node_modules','.venv','venv','env','dist','build',
             '.next','.nuxt','target','.claude','.idea','.vscode','.pytest_cache',
             'data','datasets','models','checkpoints','logs','wandb','outputs','runs',
             'cache','.cache','.mypy_cache','.ruff_cache','htmlcov','.tox','site-packages'}
SKIP_EXTS = {'.pyc','.pyo','.pyd','.so','.dll','.exe','.bin','.jpg','.jpeg','.png','.gif',
             '.ico','.woff','.ttf','.lock','.sum','.zip','.tar','.gz','.log','.csv','.tsv',
             '.qasm','.npy','.npz','.pt','.pth','.ckpt','.h5','.hdf5','.parquet','.bak',
             '.tmp','.pkl','.pickle','.onnx','.safetensors','.wav','.mp4','.pdf'}
TEXT_EXTS  = {'.py','.js','.ts','.tsx','.jsx','.rs','.go','.java','.cpp','.c','.h','.cu',
              '.cuh','.cs','.rb','.php','.md','.json','.yaml','.yml','.toml','.sh','.bat',
              '.ps1','.env','.r','.jl','.v','.sv','.vhd'}
CODE_EXTS  = {'.py','.js','.ts','.tsx','.jsx','.rs','.go','.java','.cpp','.c','.h','.cu',
              '.cuh','.cs','.rb','.php','.sh','.ps1','.r','.jl','.v','.sv','.vhd'}
MAX_SIZE   = 50_000
MAX_FILES  = int(os.environ.get("ANAMNESIS_GRAPH_MAX_FILES", "800"))
# ~30k tokens at /4 — graph above this stops saving tokens vs reading files.
MAX_GRAPH_BYTES = int(os.environ.get("ANAMNESIS_GRAPH_MAX_BYTES", "120000"))


def py_imports(src):
    try:
        t = ast.parse(src)
        r = []
        for n in ast.walk(t):
            if isinstance(n, ast.Import): r += [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module: r.append(n.module)
        return r[:20]
    except Exception:
        return re.findall(r'^(?:import|from)\s+([\w.]+)', src, re.M)[:20]


def js_imports(src):
    hits = re.findall(r"(?:import|require)\s*\(?['\"]([^'\"]+)['\"]", src)
    hits += re.findall(r"from\s+['\"]([^'\"]+)['\"]", src)
    return list(set(hits))[:20]


def analyze(path: Path, root: Path) -> dict:
    rel  = str(path.relative_to(root)).replace("\\", "/")
    size = path.stat().st_size
    node = {"path": rel, "size": size, "ext": path.suffix, "imports": [], "exports": [], "summary": ""}
    if size > MAX_SIZE or path.suffix not in TEXT_EXTS:
        return node
    try:
        src   = path.read_text(encoding="utf-8", errors="ignore")
        lines = src.count('\n') + 1
        node["lines"] = lines
        if path.suffix == '.py':
            node["imports"] = py_imports(src)
            defs = re.findall(r'^(?:class|def|async def)\s+(\w+)', src, re.M)
            node["exports"] = defs[:15]
            node["summary"] = f"{lines}L, {len(defs)} defs"
        elif path.suffix in ('.js','.ts','.tsx','.jsx'):
            node["imports"] = js_imports(src)
            exports = re.findall(r'export\s+(?:default\s+)?(?:class|function|const|let|var)\s+(\w+)', src)
            node["exports"] = exports[:15]
            node["summary"] = f"{lines}L"
        elif path.suffix == '.md':
            headers = re.findall(r'^#{1,3}\s+(.+)', src, re.M)
            node["summary"] = " | ".join(headers[:5]) or f"{lines}L"
    except Exception:
        pass
    return node


def load_extra_skips(root: Path) -> set:
    """Read .graphifyignore from project root: one dir name per line, # for comments."""
    fp = root / ".graphifyignore"
    if not fp.exists():
        return set()
    extra = set()
    for line in fp.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            extra.add(line)
    return extra


def _newest_source_mtime(root: Path):
    """Newest mtime among indexable files — drives --incremental (audit F39)."""
    skip_dirs = SKIP_DIRS | load_extra_skips(root)
    newest = None
    for r, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in skip_dirs and not d.startswith('.')]
        for fn in fns:
            fp = Path(r) / fn
            if fp.suffix in SKIP_EXTS:
                continue
            try:
                mt = fp.stat().st_mtime
            except OSError:
                continue
            if newest is None or mt > newest:
                newest = mt
    return newest


def build(root: Path) -> dict:
    skip_dirs = SKIP_DIRS | load_extra_skips(root)
    files, dirs, total = [], {}, 0
    for r, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in skip_dirs and not d.startswith('.')]
        rp  = Path(r)
        rel = str(rp.relative_to(root)).replace("\\", "/")
        df  = []
        for fn in sorted(fns):
            fp = rp / fn
            try:
                if fp.suffix in SKIP_EXTS or fp.stat().st_size > 5_000_000: continue
            except OSError:
                continue
            n = analyze(fp, root)
            files.append(n); df.append(n["path"]); total += n["size"]
        if df: dirs[rel] = df

    # Hard byte cap (audit F20): bound the graph so it ALWAYS costs less than
    # reading the sources. Keep code files (navigation value) first, then fill
    # the remaining budget with the smallest non-code files; drop the rest.
    full_count = len(files)
    code = [f for f in files if f["ext"] in CODE_EXTS]
    noncode = sorted((f for f in files if f["ext"] not in CODE_EXTS),
                     key=lambda f: f.get("size", 0))
    kept, budget = [], 0
    for f in code + noncode:
        node_sz = len(json.dumps(f, ensure_ascii=False)) + 2
        if kept and (len(kept) >= MAX_FILES or budget + node_sz > MAX_GRAPH_BYTES):
            continue
        kept.append(f)
        budget += node_sz
    truncated = len(kept) < full_count
    files = kept
    keptpaths = {f["path"] for f in files}
    dirs = {d: [p for p in ps if p in keptpaths] for d, ps in dirs.items()}
    dirs = {d: ps for d, ps in dirs.items() if ps}

    ext_counts = {}
    for f in files:
        ext_counts[f["ext"]] = ext_counts.get(f["ext"], 0) + 1
    code_bytes = sum(f["size"] for f in files if f["ext"] in CODE_EXTS)

    result = {
        "generated": datetime.now().isoformat(),
        "project":   root.name,
        "stats":     {"total_files": len(files), "total_size_kb": round(total/1024,1),
                      "top_extensions": sorted(ext_counts.items(), key=lambda x:-x[1])[:5]},
        "structure": {d: len(ps) for d, ps in dirs.items()},
        "files":     files,
        "claude_instructions": (
            "Use this graph to navigate the project. "
            "files[] lists each file's path, imports, and exports. "
            "structure{} maps folders to their file counts. "
            "Read a specific file only when you need its full code."
        )
    }
    # Honest savings ratio on the actual serialized graph (audit F21: the
    # hard-coded "71x" claim was unsubstantiated and misleading).
    graph_bytes = len(json.dumps(result, ensure_ascii=False))
    result["stats"]["graph_kb"] = round(graph_bytes / 1024, 1)
    result["stats"]["savings_vs_full_read"] = round(total / graph_bytes, 1) if graph_bytes else None
    result["stats"]["savings_vs_code_only"] = round(code_bytes / graph_bytes, 1) if graph_bytes else None
    if truncated:
        result["stats"]["truncated_to"] = MAX_FILES
        result["stats"]["full_file_count"] = full_count
    return result


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    root = Path(args[0]).resolve() if args else Path.cwd()

    # Guard: never graph the projects root itself (audit F22 — 8.6 MB dump).
    if str(root).rstrip("\\/").lower() == str(PROJECT_ROOT.resolve()).rstrip("\\/").lower():
        print(f"[graphify] refusing to graph the projects root {root} — pass a "
              f"project subdir", file=sys.stderr)
        sys.exit(2)

    # Guard: never graph the memory vault itself — it's notes, not code, and a
    # self-graph is pure noise (audit M6).
    if (root / ".processed_sessions.json").exists() or (
            (root / "Index.md").exists() and (root / "Context").is_dir()
            and (root / "Sessions").is_dir()):
        print(f"[graphify] refusing to graph the memory vault {root}", file=sys.stderr)
        sys.exit(2)

    out = root / "graph.json"
    if "--incremental" in flags and out.exists():
        newest = _newest_source_mtime(root)
        if newest is not None and out.stat().st_mtime >= newest:
            print(f"[graphify] {root.name}: up to date — skipped", file=sys.stderr)
            return

    print(f"[graphify] Scanning: {root}", file=sys.stderr)
    graph = build(root)
    tmp = out.with_name(out.name + ".tmp")  # atomic write — no corrupt graph on kill (B7)
    tmp.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)
    s = graph["stats"]
    note = f" (truncated from {s['full_file_count']})" if s.get("truncated_to") else ""
    print(f"[graphify] Done: {s['total_files']} files{note}, {s['graph_kb']} KB, "
          f"~{s['savings_vs_full_read']}x vs full read → graph.json", file=sys.stderr)

if __name__ == "__main__":
    main()
