#!/usr/bin/env python3
"""Universal v1, step 2: turn raw source material into synthetic MEMORY LESSONS.

A local LLM plays "the engineer/researcher who just worked with this material" and writes
typed memory notes (mistake/pattern/decision - the exact shape Nevertwice extracts), each
grounded in a concrete chunk of a repo/book/paper/dataset-card. ~30% of calls write in
Russian: the bilingual store is a product reality, and round 2 proved bilingualism must
come from NATIVE generation, never translation pairs.

Output: data/lessons.jsonl [{domain, heldout, lang, type, title, desc, prevention,
entities, chunk_id}]. Held-out domains produce lessons too - they feed ONLY the synthetic
test set (gen_pairs.py enforces the split).
"""
import json
import random
import re
import sys
import urllib.request
from pathlib import Path

random.seed(31)
HERE = Path(__file__).parent
RAW = HERE / "data" / "raw"
OUT = HERE / "data" / "lessons.jsonl"
OLLAMA = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen2.5:7b"
HELDOUT_DOMAINS = {"rust-book", "arxiv:quant-ph", "book:132"}
CHUNK = 1500
PER_REPO = 110          # chunk caps keep domains balanced
PER_BOOK = 50
PER_CARD = 6
RU_SHARE = 0.30

DOC_EXT = {".md", ".rst", ".txt"}
CODE_EXT = {".py", ".rs", ".c", ".h", ".ts", ".js", ".go", ".rb"}


def chunks_of(text, n=CHUNK, cap=3):
    text = re.sub(r"\n{3,}", "\n\n", text)
    out = []
    for i in range(0, len(text), n):
        seg = text[i:i + n].strip()
        if len(seg) > 300:
            out.append(seg)
        if len(out) >= cap:
            break
    return out


def collect_chunks():
    items = []                     # (domain, chunk_text)
    for repo in sorted((RAW / "repos").iterdir()) if (RAW / "repos").exists() else []:
        files = [p for p in repo.rglob("*")
                 if p.is_file() and p.suffix.lower() in DOC_EXT | CODE_EXT
                 and 1000 < p.stat().st_size < 60_000 and ".git" not in p.parts]
        random.shuffle(files)
        got = []
        for p in files:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            take = 2 if p.suffix.lower() in DOC_EXT else 1
            got += [(f"{repo.name}", c) for c in chunks_of(text, cap=take)]
            if len(got) >= PER_REPO:
                break
        items += got[:PER_REPO]
    for book in sorted((RAW / "books").glob("*.txt")) if (RAW / "books").exists() else []:
        text = book.read_text(encoding="utf-8", errors="replace")
        core = text[len(text) // 10: -len(text) // 10]      # skip Gutenberg header/footer
        segs = [core[i:i + CHUNK * 2] for i in range(0, len(core), CHUNK * 6)]
        random.shuffle(segs)
        items += [(f"book:{book.stem}", s.strip()) for s in segs[:PER_BOOK]
                  if len(s.strip()) > 500]
    for pf in sorted((RAW / "papers").glob("*.jsonl")) if (RAW / "papers").exists() else []:
        for line in pf.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            items.append((f"arxiv:{r['cat']}", f"{r['title']}\n\n{r['abstract']}"))
    for card in sorted((RAW / "datasets").glob("*.md")) if (RAW / "datasets").exists() else []:
        text = card.read_text(encoding="utf-8", errors="replace")
        items += [(f"hfds:{card.stem}", c) for c in chunks_of(text, cap=PER_CARD)]
    random.shuffle(items)
    return items


PROMPT = """You are an engineer or researcher who just spent a working session with the \
material below - reading it, integrating it, debugging against it, or applying it to a \
project. Write exactly 3 realistic MEMORY NOTES capturing durable lessons from that work.

Rules:
- Each note is one of: "mistake" (something that went wrong and why), "pattern" (an \
approach that worked), "decision" (a choice made and its rationale). Use at least two \
different types.
- Ground every note in the SPECIFICS of the material (name the actual functions, \
concepts, chapters, methods - not generic advice).
- {lang_rule}
- Output ONLY a JSON array:
[{{"type":"mistake|pattern|decision","title":"5-9 words","description":"1-2 sentences",\
"prevention":"one imperative sentence (empty string for decisions is fine)",\
"entities":["2-4","lowercase-kebab","terms"]}}]

MATERIAL:
{chunk}"""


def gen(chunk, lang_ru):
    rule = ("Write ALL text fields in Russian (natural technical Russian)." if lang_ru
            else "Write ALL text fields in English.")
    body = json.dumps({"model": MODEL,
                       "prompt": PROMPT.format(lang_rule=rule, chunk=chunk[:2400]),
                       "stream": False,
                       "options": {"temperature": 0.8, "num_predict": 600}}).encode()
    try:
        req = urllib.request.Request(OLLAMA, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            out = json.loads(r.read().decode("utf-8", "replace")).get("response", "")
        m = re.search(r"\[.*\]", out, re.S)
        if not m:
            return []
        rows = json.loads(m.group(0))
        good = []
        for x in rows if isinstance(rows, list) else []:
            if (isinstance(x, dict) and x.get("type") in ("mistake", "pattern", "decision")
                    and 10 <= len(str(x.get("title", ""))) <= 120
                    and len(str(x.get("description", ""))) >= 30):
                good.append({"type": x["type"], "title": str(x["title"]).strip(),
                             "desc": str(x["description"]).strip()[:500],
                             "prevention": str(x.get("prevention", "")).strip()[:300],
                             "entities": [str(e).strip().lower()[:40]
                                          for e in (x.get("entities") or [])[:4]]})
        return good
    except Exception:
        return []


def main():
    items = collect_chunks()
    from collections import Counter
    print(f"chunks: {len(items)} | domains: {dict(Counter(d for d, _ in items))}",
          flush=True)
    n_notes = 0
    with OUT.open("w", encoding="utf-8") as f:
        for i, (domain, chunk) in enumerate(items):
            lang_ru = random.random() < RU_SHARE
            for note in gen(chunk, lang_ru):
                note.update({"domain": domain, "heldout": domain in HELDOUT_DOMAINS,
                             "lang": "ru" if lang_ru else "en", "chunk_id": i})
                f.write(json.dumps(note, ensure_ascii=False) + "\n")
                n_notes += 1
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(items)} chunks -> {n_notes} lessons", flush=True)
    print(f"CORPUS DONE: {n_notes} lessons -> {OUT}", flush=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
