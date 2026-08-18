#!/usr/bin/env python3
"""Universal v1, step 3: mint training pairs and the held-out synthetic test.

TRAIN (non-heldout domains only) -> data/train_pairs.jsonl:
    twin        same-language rewrite of a lesson (the phenomenon the vault measured:
                same lesson, different words) - ~45% of train lessons
    supersede   a revision of the lesson (conclusion updated, topic identical) - ~15%
TEST (HELD-OUT domains only, never trained) -> data/test_synth.jsonl:
    label 1     twins of held-out lessons
    label 0     random distinct same-domain pairs
plus data/guard_synth.jsonl: retrieval queries (title -> lesson) over held-out lessons.

Same-language only, by measured design (cross-lingual pairs regressed round 2 to baseline).
"""
import json
import random
import re
import sys
import urllib.request
from pathlib import Path

random.seed(37)
HERE = Path(__file__).parent
OLLAMA = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen2.5:7b"


def ntext(n):
    return f"{n['title']}\n{n['desc']}".strip()


def gen(prompt, n_predict=350):
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0.7, "num_predict": n_predict}}).encode()
    try:
        req = urllib.request.Request(OLLAMA, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            out = json.loads(r.read().decode("utf-8", "replace")).get("response", "").strip()
        out = re.sub(r"^(here is|вот|rewritten:?|revised:?)\s*", "", out, flags=re.I).strip()
        return out if 25 <= len(out) <= 1200 else None
    except Exception:
        return None


def twin_of(n):
    lang = "Russian" if n["lang"] == "ru" else "English"
    return gen(f"Rewrite this memory note in different words, same meaning, same "
               f"language ({lang}), roughly the same length. Keep the title-then-"
               f"description shape. Output ONLY the rewritten note.\n\n{ntext(n)}")


def revision_of(n):
    lang = "Russian" if n["lang"] == "ru" else "English"
    return gen(f"This memory note was later REVISED: the team learned more and the "
               f"conclusion or recommended action changed materially, but it is still "
               f"about the exact same subject. Write the revised note in {lang}, "
               f"title-then-description shape. Output ONLY the revised note.\n\n{ntext(n)}")


def main():
    lessons = [json.loads(l) for l in (HERE / "data" / "lessons.jsonl")
               .read_text(encoding="utf-8").splitlines() if l.strip()]
    train_l = [n for n in lessons if not n["heldout"]]
    held_l = [n for n in lessons if n["heldout"]]
    print(f"lessons: {len(lessons)} (train {len(train_l)}, heldout {len(held_l)})",
          flush=True)

    random.shuffle(train_l)
    n_twin = int(len(train_l) * 0.45)
    n_sup = int(len(train_l) * 0.15)
    pairs = []
    for i, n in enumerate(train_l[:n_twin]):
        t = twin_of(n)
        if t:
            pairs.append({"anchor": ntext(n), "positive": t, "source": "twin"})
        if (i + 1) % 100 == 0:
            print(f"  twins {i + 1}/{n_twin} (ok {len(pairs)})", flush=True)
    base = len(pairs)
    for i, n in enumerate(train_l[n_twin:n_twin + n_sup]):
        t = revision_of(n)
        if t:
            pairs.append({"anchor": ntext(n), "positive": t, "source": "supersede"})
        if (i + 1) % 100 == 0:
            print(f"  revisions {i + 1}/{n_sup} (ok {len(pairs) - base})", flush=True)
    with (HERE / "data" / "train_pairs.jsonl").open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    # held-out synthetic test: twins (pos) + distinct same-domain (neg)
    random.shuffle(held_l)
    test_pos_src = held_l[:90]
    test = []
    for i, n in enumerate(test_pos_src):
        t = twin_of(n)
        if t:
            test.append({"a": ntext(n), "b": t, "label": 1, "domain": n["domain"]})
    by_dom = {}
    for n in held_l:
        by_dom.setdefault(n["domain"], []).append(n)
    seen = set()
    want = min(600, sum(len(v) * (len(v) - 1) // 2 for v in by_dom.values()))
    tries = 0
    while len(test) - len(test_pos_src) < want and tries < want * 40:
        tries += 1
        dom = random.choice([d for d, v in by_dom.items() if len(v) >= 2])
        a, b = random.sample(by_dom[dom], 2)
        k = tuple(sorted((id(a), id(b))))
        if k in seen or a["title"] == b["title"]:
            continue
        seen.add(k)
        test.append({"a": ntext(a), "b": ntext(b), "label": 0, "domain": dom})
    with (HERE / "data" / "test_synth.jsonl").open("w", encoding="utf-8") as f:
        for t in test:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    with (HERE / "data" / "guard_synth.jsonl").open("w", encoding="utf-8") as f:
        for i, n in enumerate(held_l):
            f.write(json.dumps({"qid": i, "query": n["title"], "text": ntext(n)},
                               ensure_ascii=False) + "\n")

    from collections import Counter
    print(f"PAIRS DONE: train {len(pairs)} {dict(Counter(p['source'] for p in pairs))}; "
          f"test {sum(1 for t in test if t['label'])} pos / "
          f"{sum(1 for t in test if not t['label'])} neg; guard {len(held_l)} docs",
          flush=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
