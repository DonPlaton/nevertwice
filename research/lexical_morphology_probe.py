#!/usr/bin/env python3
"""Does morphology (stop words + stems) help the lexical signal on a REAL store?

The external corpora answer this for English chat (LoCoMo, LongMemEval); a Nevertwice store is
bilingual notes of about a thousand characters, written by the extractor, and nobody else's
benchmark looks like it. This probe runs the two lexical variants over a populated vault,
read-only, and prints recall by language half:

* **session protocol** (the one that decides): the summary of a Session note is the query,
  the typed notes extracted from that session are the relevant set, the pool is the project's
  typed notes. That is the direction production runs in - a situation, then the lessons
  about it.
* **sibling protocol** (reported, not decisive): a typed note's prose is the query and its
  wikilinked typed neighbours are relevant. Measured 2026-09-06 to reward the session's
  phrasing fingerprint - even a thirty-word stop list cost it three points - which no user
  prompt will ever share; kept so the number is on the record rather than in a scratchpad.

Both variants use the engine's own tokenizer and its own BM25 (`memory_hook._bm25_scores`) with
the morphology switch forced off and on, so what is measured is the shipped code path, not a
copy of it. Nothing from `research/` that isolates the store is imported: the store here is the
one named on the command line, read and never written.

    NEVERTWICE_VAULT=/path/to/store python research/lexical_morphology_probe.py
    NEVERTWICE_VAULT=... python research/lexical_morphology_probe.py --protocol sibling
    NEVERTWICE_VAULT=... python research/lexical_morphology_probe.py --out research/results/lexical_morphology_vault.json

Nothing is written to the store. The optional artifact holds rates and counts only - no note
text - which is what lets it be committed beside a private vault's numbers.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402

sandbox_guard.allow_live("reads the notes of a real store to score lexical recall; writes nothing to it")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402

KS = (1, 3, 5, 10)
FM = re.compile(r"^---\r?\n.*?\r?\n---\r?\n", re.S)
LINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
TAG = re.compile(r"(?<![\w/#\-])#[\w/\-]+")
CODE = re.compile(r"`[^`\n]*`")
URL = re.compile(r"https?://\S+")
META = re.compile(r"^\*\*(Проект|Дата|Сессия|Project|Date|Session|Directory|Trigger)[^\n]*$", re.M)
CUTS = ("## Связанные заметки", "## Related notes", "**Patterns:**", "**Mistakes:**",
        "**Decisions:**")


def prose(text: str) -> str:
    body = FM.sub("", text, count=1)
    for cut in CUTS:
        body = body.split(cut)[0]
    for rx in (LINK, TAG, CODE, URL, META):
        body = rx.sub(" ", body)
    return body


def lang_of(pr: str) -> str:
    letters = [ch for ch in pr if ch.isalpha()]
    cyr = sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ")
    return "ru" if letters and cyr / len(letters) > 0.3 else "en"


def project_of(text: str) -> str:
    fm = FM.match(text)
    mm = re.search(r"^project:\s*(.+)$", fm.group(0), re.M) if fm else None
    return mm.group(1).strip().strip('"') if mm else ""


def load_store(vault: Path):
    notes, sessions = {}, []
    for folder in ("Patterns", "Mistakes", "Decisions"):
        for p in (vault / folder).glob("*.md"):
            t = p.read_text(encoding="utf-8", errors="replace")
            pr = prose(t)
            notes[p.stem] = {"project": project_of(t), "prose": pr, "lang": lang_of(pr),
                             "links": set(LINK.findall(t))}
    for p in list((vault / "Sessions").glob("*.md")) + list((vault / "Sessions" / "Archive").glob("*.md")):
        t = p.read_text(encoding="utf-8", errors="replace")
        proj = project_of(t)
        pr = prose(t)
        rel = {l for l in LINK.findall(t) if l in notes and notes[l]["project"] == proj}
        if rel and len(pr.split()) >= 5:
            sessions.append({"project": proj, "query": pr, "rel": rel, "lang": lang_of(pr)})
    return notes, sessions


def _score(qtext: str, cands: list, morph: bool) -> dict:
    """The engine's BM25 over the candidate notes, tokenizer switched as asked for both sides."""
    saved = m.LEXICAL_MORPHOLOGY
    m.LEXICAL_MORPHOLOGY = morph
    try:
        qt = m._tokens(qtext)
        return m._bm25_scores(qt, cands) if qt else {}
    finally:
        m.LEXICAL_MORPHOLOGY = saved


def _recall(ranked, rel):
    return {k: (1.0 if set(ranked[:k]) & rel else 0.0) for k in KS}


def run(protocol: str, notes: dict, sessions: list) -> dict:
    by_proj = collections.defaultdict(list)
    for slug, n in notes.items():
        by_proj[n["project"]].append(slug)
    queries = []
    if protocol == "session":
        for s in sessions:
            if len(by_proj.get(s["project"], [])) >= 3:
                queries.append((s["project"], None, s["query"], s["rel"], s["lang"]))
    else:
        for slug, n in notes.items():
            pool = by_proj[n["project"]]
            rel = {l for l in n["links"] if l in notes and notes[l]["project"] == n["project"]
                   and l != slug}
            if rel and len(pool) >= 3:
                queries.append((n["project"], slug, n["prose"], rel, n["lang"]))
    variants = {"raw": False, "morph": True}
    cands_of: dict = {}
    agg = {lang: {v: {k: 0.0 for k in KS} for v in variants} for lang in ("ru", "en")}
    mrr = {lang: {v: 0.0 for v in variants} for lang in ("ru", "en")}
    nq: collections.Counter = collections.Counter()
    for proj, self_slug, qtext, rel, lang in queries:
        if proj not in cands_of:
            cands_of[proj] = [(x, {"desc": notes[x]["prose"]}) for x in by_proj[proj]]
        cands = [c for c in cands_of[proj] if c[0] != self_slug]
        nq[lang] += 1
        for v, on in variants.items():
            bm = _score(qtext, cands, on)
            ranked = sorted(bm, key=lambda d: (-bm[d], d))
            for k, r in _recall(ranked, rel).items():
                agg[lang][v][k] += r
            for i, d in enumerate(ranked[:20]):
                if d in rel:
                    mrr[lang][v] += 1 / (i + 1)
                    break
    out = {"protocol": protocol, "halves": {}}
    for lang in ("ru", "en"):
        n = nq[lang]
        if not n:
            continue
        out["halves"][lang] = {"n": n}
        for v in variants:
            out["halves"][lang][v] = {f"recall@{k}": round(agg[lang][v][k] / n, 4) for k in KS}
            out["halves"][lang][v]["mrr"] = round(mrr[lang][v] / n, 4)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--protocol", default="session", choices=["session", "sibling", "both"])
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    vault = Path(os.environ.get("NEVERTWICE_VAULT") or os.environ.get("NEVERTWICE_HOME") or "")
    if not vault or not (vault / "Sessions").is_dir():
        print("point NEVERTWICE_VAULT at a populated store (it needs Sessions/ and the typed folders)")
        return 2
    notes, sessions = load_store(vault)
    print(f"store: {len(notes)} typed notes "
          f"({collections.Counter(n['lang'] for n in notes.values())}), "
          f"{len(sessions)} sessions with extracted notes")
    protocols = ["session", "sibling"] if args.protocol == "both" else [args.protocol]
    res = {"store_notes": len(notes), "store_sessions": len(sessions),
           "embedder": "none (lexical only)", "protocols": {}}
    for prot in protocols:
        r = run(prot, notes, sessions)
        res["protocols"][prot] = r
        print(f"\n[{prot}]")
        for lang, h in r["halves"].items():
            print(f"  {lang.upper()} n={h['n']}")
            for v in ("raw", "morph"):
                print(f"    {v:5s} " + "  ".join(f"R@{k} {h[v][f'recall@{k}']:.3f}" for k in KS)
                      + f"  MRR {h[v]['mrr']:.3f}")
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
