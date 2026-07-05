#!/usr/bin/env python3
"""Learned user / working model (I-6) - distil a cross-project profile of HOW the
user works from the whole vault, beyond what CLAUDE.md states by hand. Writes
User/profile.md with a compact brief the hook injects at SessionStart.

100% local, GPU-free, privacy-safe: pure structural analysis (tags, cross-project
mistake themes, pattern themes, session signals) - no embeddings, no LLM, no cloud,
so it is safe even over local-only research projects.

    python build_user_model.py            # rebuild User/profile.md
"""
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import memory_hook as m

# A small function-word stoplist; the real generic-word filter is the document-
# frequency ceiling in main() (language-agnostic: any token in >DF_CEIL of notes
# is noise, so 'использование'/'проверка' drop out while 'windows'/'cuda' stay).
STOP = {"the", "and", "for", "use", "using", "used", "code", "file", "files",
        "test", "tests", "with", "from", "this", "that", "their", "into", "via",
        "для", "при", "из", "по", "что", "как", "это", "был", "была", "были",
        "после", "если", "через", "без", "она", "его", "their", "вместо", "также",
        "использование", "использования", "использовать", "проверка", "проверки",
        "позволяет", "обеспечивает", "например", "привело", "приводит", "данных",
        "данные", "кода", "результат", "реализация", "исправление", "создание",
        "важно", "текущий", "новый", "чтобы", "только", "более", "очень",
        # common narrative verbs/nouns that slip past the DF gate (M-15 cleanup)
        "требует", "требуется", "требуют", "эксперимент", "экспериментов",
        "эксперименты", "необходимо", "следует", "нужно", "может", "можно",
        "была", "было", "будет", "этот", "этого", "которые", "которая",
        "ошибка", "ошибки", "проблема", "проблемы", "функция", "функции",
        "метод", "методы", "значение", "значения", "should", "would", "could",
        "must", "need", "needs", "than", "then", "when", "where", "which",
        "value", "values", "error", "errors", "issue", "method", "function"}
DF_CEIL = 0.22  # token in >22% of notes ⇒ generic narrative word, not a signal


def _notes():
    out = []
    for ntype, folder in m.TYPE_FOLDER.items():
        d = m.VAULT / folder
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            parsed = m.parse_typed_stem(p.stem)
            if not parsed:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # Parse tags via the shared frontmatter reader (audit M-h): the
            # round-1 `re.findall('"([^"]+)"')` only saw QUOTED tags, so an
            # unquoted YAML list `tags: [a, b]` yielded nothing and the note was
            # silently dropped from the profile statistics.
            fm_dict, _body = m._read_frontmatter(text)
            raw_tags = fm_dict.get("tags", [])
            if isinstance(raw_tags, str):
                raw_tags = [t for t in re.split(r"[,\s]+", raw_tags) if t]
            tags = m._norm_tags(raw_tags)
            desc = ""
            seen = False
            for ln in text.split("\n"):
                s = ln.strip()
                if s.startswith("# "):
                    seen = True
                    continue
                if seen and s and not s.startswith(("**", "#", "-", "_", "[[", "|")):
                    desc = s
                    break
            out.append({"project": parsed["project"], "ntype": ntype,
                        "slug": parsed["slug"], "date": parsed["date"],
                        "tags": tags, "desc": desc})
    return out


def _toks(s):
    return {t for t in re.findall(r"[^\W\d_]{4,}", (s or "").lower()) if t not in STOP}


def main():
    notes = _notes()
    if not notes:
        print("[user-model] no notes", file=sys.stderr)
        return
    projects = sorted({n["project"] for n in notes})

    # 1) stack/themes - top tags overall
    tagc = Counter(t for n in notes for t in n["tags"] if t)
    top_tags = [t for t, _ in tagc.most_common(14)]

    # document frequency over all notes → IDF gate: a token in >DF_CEIL of notes
    # is a generic narrative word, not a signal. Language-agnostic filter.
    df = Counter()
    for n in notes:
        df.update(_toks(f"{n['slug']} {n['desc']}"))
    ceil = max(3, int(DF_CEIL * len(notes)))

    def content(s):
        return {t for t in _toks(s) if df[t] <= ceil}

    # 2) recurring CROSS-PROJECT gotchas - content tokens of mistakes spanning ≥2 projects
    tok_projects = defaultdict(set)
    tok_examples = {}
    for n in notes:
        if n["ntype"] != "mistake":
            continue
        for t in content(f"{n['slug']} {n['desc']}"):
            tok_projects[t].add(n["project"])
            tok_examples.setdefault(t, n["slug"])
    cross = sorted(((len(p), t) for t, p in tok_projects.items() if len(p) >= 2),
                   reverse=True)
    gotchas = [(t, tok_examples[t], n) for n, t in cross[:8]]

    # 3) working-style - recurring pattern content themes (across ≥3 patterns)
    pat_tok = Counter()
    for n in notes:
        if n["ntype"] == "pattern":
            pat_tok.update(content(f"{n['slug']} {n['desc']}"))
    style = [t for t, c in pat_tok.most_common(40) if c >= 3][:10]

    # 4) per-project note counts + last activity
    by_proj = defaultdict(lambda: [0, ""])
    for n in notes:
        by_proj[n["project"]][0] += 1
        if n["date"] > by_proj[n["project"]][1]:
            by_proj[n["project"]][1] = n["date"]

    counts = Counter(n["ntype"] for n in notes)
    brief = (f"Стек: {', '.join(top_tags[:8])}. "
             f"Повторяющиеся грабли (≥2 проектов): "
             f"{', '.join(t for t, _, _ in gotchas[:5]) or '-'}. "
             f"Рабочий стиль: {', '.join(style[:5]) or '-'}.")

    lines = [
        m.fm_block({"type": "user_model", "date": datetime.now().strftime("%Y-%m-%d"),
                    "tags": ["user", "profile"]}),
        "", "# Learned User Model",
        "", "_Выведено структурно из всего vault (теги, кросс-проектные темы). " +
        "Дополняет CLAUDE.md выученными паттернами. Обновить: `python build_user_model.py`._",
        "", "## Кратко (для инъекции)", "", brief,
        "", f"## Стек и темы (топ-{len(top_tags)} тегов)", "",
        ", ".join(top_tags) or "-",
        "", "## Повторяющиеся грабли (кросс-проектные)", "",
    ]
    lines += [f"- **{t}** - в {n} проектах (напр. `{ex}`)" for t, ex, n in gotchas] or ["-"]
    lines += ["", "## Рабочий стиль (повторяющиеся паттерны)", "",
              ", ".join(style) or "-",
              "", "## Проекты", ""]
    lines += [f"- [[{p}]] - {by_proj[p][0]} заметок, активность до {by_proj[p][1]}"
              for p in sorted(by_proj, key=lambda p: -by_proj[p][0])]
    lines += ["", f"_Всего: {len(notes)} заметок "
              f"(P={counts['pattern']} M={counts['mistake']} D={counts['decision']}), "
              f"{len(projects)} проектов._"]

    fp = m.VAULT / "User" / "profile.md"
    m.write_atomic(fp, "\n".join(lines))
    print(f"[user-model] wrote {fp}")
    print(f"  brief: {brief}")


if __name__ == "__main__":
    main()
