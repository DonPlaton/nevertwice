#!/usr/bin/env python3
"""Universal v1, step 1: fetch maximally diverse EXTERNAL source material into data/raw/.

Every fetch is best-effort: a dead URL or blocked clone is reported and skipped - corpus
diversity degrades gracefully instead of the pipeline dying on one source. Output layout:

    data/raw/repos/<name>/...            shallow git clones (docs+code)
    data/raw/books/<id>.txt              Project Gutenberg plain text
    data/raw/papers/<cat>.jsonl          arXiv abstracts {title, abstract, cat}
    data/raw/datasets/<name>.md          HuggingFace dataset cards
"""
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

RAW = Path(__file__).parent / "data" / "raw"

REPOS = [  # medium-size, diverse languages/domains; docs-heavy where possible
    ("flask", "https://github.com/pallets/flask"),
    ("fastapi", "https://github.com/tiangolo/fastapi"),          # multilingual docs incl. RU
    ("vue-core", "https://github.com/vuejs/core"),
    ("redis", "https://github.com/redis/redis"),
    ("ripgrep", "https://github.com/BurntSushi/ripgrep"),
    ("ggml", "https://github.com/ggml-org/ggml"),
    ("ml-for-beginners", "https://github.com/microsoft/ML-For-Beginners"),  # course/slides
    ("rust-book", "https://github.com/rust-lang/book"),          # HELD-OUT domain
]

BOOKS = [  # (gutenberg id, note) - plain-text URLs tried in two common layouts
    ("1228", "On the Origin of Species (Darwin)"),
    ("132", "The Art of War (Sun Tzu)"),                          # HELD-OUT domain
    ("5001", "Relativity (Einstein)"),
    ("2600", "War and Peace (Tolstoy, EN)"),
]

ARXIV = [("cs.LG", 60), ("q-bio.NC", 40), ("quant-ph", 40)]       # quant-ph = HELD-OUT

HF_DATASETS = ["squad", "glue", "ag_news", "wikitext", "openwebtext",
               "cnn_dailymail", "xnli"]


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "nevertwice-research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    ok = fail = 0

    rd = RAW / "repos"
    rd.mkdir(exist_ok=True)
    for name, url in REPOS:
        dest = rd / name
        if dest.exists():
            print(f"repo {name}: already present")
            ok += 1
            continue
        r = subprocess.run(["git", "clone", "--depth", "1", "--quiet", url, str(dest)],
                           capture_output=True, text=True, timeout=600)
        print(f"repo {name}: {'OK' if r.returncode == 0 else 'FAIL ' + r.stderr[:120]}")
        ok += r.returncode == 0
        fail += r.returncode != 0

    bd = RAW / "books"
    bd.mkdir(exist_ok=True)
    for gid, note in BOOKS:
        dest = bd / f"{gid}.txt"
        if dest.exists():
            ok += 1
            continue
        got = None
        for u in (f"https://www.gutenberg.org/files/{gid}/{gid}-0.txt",
                  f"https://www.gutenberg.org/cache/epub/{gid}/pg{gid}.txt"):
            try:
                got = fetch(u, timeout=120).decode("utf-8", "replace")
                break
            except Exception:
                continue
        if got and len(got) > 10000:
            dest.write_text(got, encoding="utf-8")
            print(f"book {gid} ({note}): OK ({len(got)//1000}KB)")
            ok += 1
        else:
            print(f"book {gid} ({note}): FAIL")
            fail += 1

    pd = RAW / "papers"
    pd.mkdir(exist_ok=True)
    for cat, n in ARXIV:
        dest = pd / f"{cat}.jsonl"
        if dest.exists():
            ok += 1
            continue
        try:
            xml = fetch("http://export.arxiv.org/api/query?search_query=cat:" + cat +
                        f"&start=0&max_results={n}&sortBy=submittedDate&sortOrder=descending",
                        timeout=120).decode("utf-8", "replace")
            entries = re.findall(r"<entry>(.*?)</entry>", xml, re.S)
            rows = []
            for e in entries:
                t = re.search(r"<title>(.*?)</title>", e, re.S)
                a = re.search(r"<summary>(.*?)</summary>", e, re.S)
                if t and a:
                    rows.append({"title": re.sub(r"\s+", " ", t.group(1)).strip(),
                                 "abstract": re.sub(r"\s+", " ", a.group(1)).strip(),
                                 "cat": cat})
            dest.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                            encoding="utf-8")
            print(f"arxiv {cat}: OK ({len(rows)} abstracts)")
            ok += 1
        except Exception as ex:
            print(f"arxiv {cat}: FAIL {type(ex).__name__}")
            fail += 1

    dd = RAW / "datasets"
    dd.mkdir(exist_ok=True)
    for name in HF_DATASETS:
        dest = dd / f"{name}.md"
        if dest.exists():
            ok += 1
            continue
        got = None
        for u in (f"https://huggingface.co/datasets/{name}/raw/main/README.md",
                  f"https://huggingface.co/datasets/{name}/resolve/main/README.md"):
            try:
                got = fetch(u, timeout=60).decode("utf-8", "replace")
                break
            except Exception:
                continue
        if got and len(got) > 500:
            dest.write_text(got, encoding="utf-8")
            print(f"dataset card {name}: OK")
            ok += 1
        else:
            print(f"dataset card {name}: FAIL")
            fail += 1

    print(f"\nSOURCES DONE: {ok} ok, {fail} failed")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
