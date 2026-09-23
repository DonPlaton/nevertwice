#!/usr/bin/env python3
"""Candidates for the owner to mark by hand, and a page to mark them on (ledger J3, held-out).

`research/code_heldout.py` keeps a question only when three automatic checks agree: the quoted
sentence is verbatim, the answer sits inside it, and a second pass reaches the same answer. On
the owner's own transcripts that kept 14 of 93 slices - and the drops are mostly the model
rephrasing a sentence it read correctly, which a human repairs in two seconds by looking at the
transcript. Loosening the checks would buy questions nobody can trust; showing a person the
slice and letting them judge buys questions nobody can dispute.

So this builder keeps **every** candidate, accepted or dropped, records which checks passed and
why it fell, carries the surrounding transcript text for the reviewer to read, and writes a
self-contained HTML page with the data inlined - no server, no fetch, opened straight from disk.

Nothing of the transcripts enters the repository: the candidate file and the page are written
to the polygon, and the repository gets only this code. Do not publish either file, do not
paste their contents into a chat, and do not open them anywhere but locally.

    python research/heldout_review.py --sessions 120            # build candidates + page
    python research/heldout_review.py --sessions 120 --dry      # count slices, call no model
    python research/heldout_review.py --page-only               # rebuild the page from the file
    python research/heldout_review.py --collect marks.json      # marks -> a held-out corpus
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402 - the engine parser is imported; nothing is written to any store

sandbox_guard.isolate(prefix="nevertwice_heldout_review_")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))
import code_heldout as ch  # noqa: E402 - the slices, the prompts, the checks
import gen_code_sessions as gcs  # noqa: E402 - the foreign model call

OUT_DIR = Path(os.environ.get("CODE_HELDOUT_DIR", r"D:\Coding\_nevertwice_polygon\code_heldout"))
CANDIDATES = OUT_DIR / "review_candidates.json"
PAGE = OUT_DIR / "review.html"
CORPUS = OUT_DIR / "code_heldout_v2.json"
MANIFEST = HERE / "data" / "code_heldout_review_manifest.json"
SEED = 20260911
CONTEXT_CHARS = 1400          # around the quote, what the reviewer reads to judge


def _window(body: str, quote: str) -> tuple[str, int, int]:
    """The passage around the quote, and where the quote sits inside it.

    An exact match when the model quoted faithfully; otherwise the best anchor we can find from
    the quote's longest words, so the reviewer lands near the right place instead of reading
    forty thousand characters. Returns (passage, start, end) with -1,-1 when nothing anchors.
    """
    at = body.find(quote)
    if at < 0 and quote:
        words = sorted((w for w in re.findall(r"[\w./:-]{5,}", quote)), key=len, reverse=True)[:4]
        for w in words:
            at = body.find(w)
            if at >= 0:
                break
    if at < 0:
        return body[:CONTEXT_CHARS], -1, -1
    half = CONTEXT_CHARS // 2
    lo = max(0, at - half)
    hi = min(len(body), at + len(quote) + half)
    return body[lo:hi], at - lo, at - lo + len(quote)


def _checks(question: str, answer: str, quote: str, body: str) -> dict:
    """The three automatic checks, reported individually rather than as one verdict."""
    return {
        "quote_verbatim": bool(quote) and gcs.hit([quote], body),
        "answer_in_quote": bool(answer) and bool(quote) and gcs.hit([answer], quote),
        "answer_short": bool(answer) and len(answer.split()) <= 8,
        "answer_in_transcript": bool(answer) and gcs.hit([answer], body),
    }


def build(paths: list[Path], limit: int, chunks: int, verbose: bool = True) -> list[dict]:
    rng = random.Random(SEED)
    slices = []
    for path in paths:
        for sess in ch.read_session(path, chunks):
            slices.append((path, sess))
    rng.shuffle(slices)
    slices = slices[:limit]
    out, t0 = [], time.time()
    for i, (path, sess) in enumerate(slices, 1):
        body = sess["body"]
        try:
            q = ch._call(gcs.ollama_json, ch.ASK.format(transcript=body), SEED + i)
        except Exception as e:                                    # noqa: BLE001
            if verbose:
                print(f"  [{i}/{len(slices)}] generation error: {type(e).__name__}", flush=True)
            continue
        question = str(q.get("question") or "").strip()
        answer = str(q.get("answer") or "").strip()
        quote = str(q.get("evidence_quote") or "").strip()
        if not question or not answer:
            if verbose:
                print(f"  [{i}/{len(slices)}] incomplete", flush=True)
            continue
        checks = _checks(question, answer, quote, body)
        second = ""
        if checks["quote_verbatim"] and checks["answer_in_quote"]:
            try:
                second = str(ch._call(gcs.ollama_json,
                                      ch.ANSWER.format(question=question, transcript=body),
                                      SEED + i + 500).get("answer") or "").strip()
            except Exception:                                     # noqa: BLE001
                second = ""
        checks["second_pass_agrees"] = bool(second) and ch._agree(answer, second)
        passage, qs, qe = _window(body, quote)
        src = hashlib.sha256(body.encode("utf-8")).hexdigest()
        out.append({
            "id": f"rv{src[:8]}-{sess['chunk']}",
            "question": question, "answer": answer, "quote": quote, "second_answer": second,
            "checks": checks, "passed": sum(1 for v in checks.values() if v),
            "auto_accepted": all(checks.values()),
            "passage": passage, "quote_start": qs, "quote_end": qe,
            "source": {"transcript_sha256": src, "chars": sess["chars"], "offset": sess["offset"],
                       "chunk": sess["chunk"], "project_dir": path.parent.name,
                       "day": sess.get("day") or ""},
        })
        if verbose and i % 10 == 0:
            done = len(out)
            print(f"  [{i}/{len(slices)}] candidates {done}, auto-accepted "
                  f"{sum(1 for c in out if c['auto_accepted'])}, {time.time() - t0:.0f}s", flush=True)
    # Most likely to be good first: the reviewer stops as soon as the target is met.
    out.sort(key=lambda c: (-c["passed"], not c["auto_accepted"]))
    return out


PAGE_TEMPLATE = r"""<!doctype html>
<meta charset="utf-8">
<title>Held-out: разметка вопросов</title>
<style>
 :root { --bg:#faf9f7; --fg:#1c1a17; --dim:#6b6660; --line:#e2ddd6; --ok:#1f7a4d; --no:#b03030;
         --warn:#a76a00; --card:#fff; }
 @media (prefers-color-scheme: dark) { :root { --bg:#17161a; --fg:#eae7e2; --dim:#9a948c;
   --line:#332f36; --ok:#57c98a; --no:#f08a8a; --warn:#e0a94a; --card:#1f1e23; } }
 * { box-sizing:border-box }
 body { margin:0; background:var(--bg); color:var(--fg);
        font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif; }
 header { position:sticky; top:0; background:var(--bg); border-bottom:1px solid var(--line);
          padding:10px 18px; display:flex; gap:18px; align-items:baseline; flex-wrap:wrap; z-index:5 }
 header b { font-size:15px } header span { color:var(--dim); font-size:13px }
 .bar { height:4px; background:var(--line); border-radius:2px; flex:1 1 200px; min-width:120px; overflow:hidden }
 .bar i { display:block; height:100%; background:var(--ok) }
 main { max-width:900px; margin:0 auto; padding:18px }
 .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:18px; margin-bottom:14px }
 .q { font-size:19px; font-weight:600; margin:0 0 10px }
 .a { font-size:16px; margin:0 0 14px } .a code, .quote code { background:transparent }
 .k { color:var(--dim); font-size:12px; text-transform:uppercase; letter-spacing:.06em; margin:14px 0 4px }
 .quote { border-left:3px solid var(--line); padding:8px 12px; margin:0 0 10px; color:var(--dim);
          font-family:ui-monospace,Consolas,monospace; font-size:13px; white-space:pre-wrap; word-break:break-word }
 .passage { max-height:260px; overflow:auto; border:1px solid var(--line); border-radius:8px;
            padding:10px 12px; font-family:ui-monospace,Consolas,monospace; font-size:12.5px;
            white-space:pre-wrap; word-break:break-word; color:var(--fg) }
 mark { background:#ffe58a; color:#1c1a17; padding:1px 0 }
 .flags { display:flex; gap:8px; flex-wrap:wrap; margin:10px 0 0; font-size:12px }
 .flag { border:1px solid var(--line); border-radius:999px; padding:2px 9px; color:var(--dim) }
 .flag.on { color:var(--ok); border-color:var(--ok) } .flag.off { color:var(--no); border-color:var(--no) }
 .btns { display:flex; gap:10px; flex-wrap:wrap; margin-top:16px }
 button { font:inherit; padding:9px 16px; border-radius:8px; border:1px solid var(--line);
          background:var(--card); color:var(--fg); cursor:pointer }
 button:hover { border-color:var(--fg) }
 button.yes { border-color:var(--ok); color:var(--ok) } button.no { border-color:var(--no); color:var(--no) }
 button.gen { border-color:var(--warn); color:var(--warn) }
 .edit { margin-top:14px; display:none; gap:8px; flex-direction:column }
 .edit.open { display:flex }
 .edit label { font-size:12px; color:var(--dim) }
 .edit input { font:inherit; padding:8px 10px; border-radius:8px; border:1px solid var(--line);
               background:var(--bg); color:var(--fg); width:100% }
 .done { text-align:center; padding:60px 20px }
 textarea { width:100%; height:220px; font:12px/1.5 ui-monospace,Consolas,monospace; padding:10px;
            border-radius:8px; border:1px solid var(--line); background:var(--card); color:var(--fg) }
 .hint { color:var(--dim); font-size:12.5px; margin-top:10px }
 .badge { display:inline-block; border-radius:999px; padding:2px 10px; font-size:12px;
          border:1px solid var(--line); color:var(--dim); margin-bottom:10px }
 .badge.yes { color:var(--ok); border-color:var(--ok) }
 .badge.no { color:var(--no); border-color:var(--no) }
 .nomark { color:var(--warn); font-size:12.5px; margin:0 0 6px }
 .rule { background:var(--card); border:1px dashed var(--line); border-radius:8px;
         padding:10px 14px; margin-bottom:14px; color:var(--dim); font-size:13px }
 kbd { border:1px solid var(--line); border-bottom-width:2px; border-radius:5px; padding:0 5px; font-size:12px }
</style>
<header>
  <b id="pos"></b>
  <span id="tally"></span>
  <div class="bar"><i id="fill" style="width:0"></i></div>
  <span><kbd>1</kbd> годится · <kbd>2</kbd> нет · <kbd>3</kbd> и без текста ясно · <kbd>4</kbd> поправить · <kbd>←</kbd> назад</span>
  <button id="export" style="padding:4px 10px">Готово / выгрузить</button>
</header>
<main id="main"></main>
<div style="max-width:900px;margin:0 auto;padding:0 18px 40px">
<div class="rule"><b style="color:var(--fg)">Решает нижний блок — твой текст.</b>
Вопрос и ответ — это то, что предложила модель; жёлтым подсвечено место, откуда она их взяла.
<kbd>1</kbd> только если: ответ <b style="color:var(--fg)">дословно</b> есть во фрагменте ·
вопрос понятен без пояснений · без фрагмента на него не ответить.<br>
Ответ почти верный, но переформулирован или обрезан — <kbd>4</kbd>, впиши то, что буквально
написано в тексте, и <kbd>Enter</kbd>. Общее программирование — <kbd>3</kbd>.</div>
</div>
<script>
const DATA = __DATA__;
const TARGET = __TARGET__;
const KEY = "heldout-marks-v1";
let marks = {};
try { marks = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { marks = {}; }
let i = DATA.findIndex(c => !marks[c.id]);
if (i < 0) i = DATA.length;

const esc = s => (s || "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(marks)); } catch (e) {} };
const accepted = () => Object.values(marks).filter(m => m.verdict === "yes").length;

function passage(c) {
  if (c.quote_start < 0) return esc(c.passage);
  return esc(c.passage.slice(0, c.quote_start)) + "<mark>" +
         esc(c.passage.slice(c.quote_start, c.quote_end)) + "</mark>" +
         esc(c.passage.slice(c.quote_end));
}

const LABEL = { quote_verbatim: "цитата дословна", answer_in_quote: "ответ внутри цитаты",
  answer_short: "ответ короткий", answer_in_transcript: "ответ есть в тексте",
  second_pass_agrees: "второй проход согласен" };

function render() {
  const main = document.getElementById("main");
  document.getElementById("tally").textContent =
    "принято " + accepted() + " из " + TARGET + " · размечено " + Object.keys(marks).length + " из " + DATA.length;
  document.getElementById("fill").style.width = Math.min(100, accepted() * 100 / TARGET) + "%";
  if (i >= DATA.length) {
    document.getElementById("pos").textContent = "всё";
    main.innerHTML = '<div class="card done"><p>Кандидаты кончились. Принято ' + accepted() +
      '.</p><p class="hint">Нажми «Готово / выгрузить» вверху.</p></div>';
    return;
  }
  const c = DATA[i];
  document.getElementById("pos").textContent = (i + 1) + " / " + DATA.length;
  const flags = Object.entries(c.checks).map(([k, v]) =>
    '<span class="flag ' + (v ? "on" : "off") + '">' + (v ? "✓ " : "✕ ") + (LABEL[k] || k) + "</span>").join("");
  const prev = marks[c.id];
  const BADGE = { yes: "уже отмечено: годится", no: "уже отмечено: не годится",
                  generic: "уже отмечено: ясно и без текста" };
  main.innerHTML =
    '<div class="card">' +
      (prev ? '<div class="badge ' + (prev.verdict === "yes" ? "yes" : "no") + '">' +
              (BADGE[prev.verdict] || "уже отмечено") + (prev.edited ? " (с правкой)" : "") +
              " — нажми заново, чтобы изменить</div>" : "") +
      '<p class="q">' + esc(c.question) + "</p>" +
      '<p class="a">Ответ: <b>' + esc(c.answer) + "</b>" +
        (c.second_answer && c.second_answer !== c.answer ? ' <span class="hint">(второй проход: ' + esc(c.second_answer) + ")</span>" : "") +
      "</p>" +
      '<div class="k">1 · что модель считает цитатой из сессии</div>' +
      '<div class="quote">' + esc(c.quote || "(нет)") + "</div>" +
      '<div class="k">2 · твой текст — он и решает · ' + esc(c.source.project_dir) + (c.source.day ? " · " + esc(c.source.day) : "") + "</div>" +
      (c.quote_start < 0 ? '<p class="nomark">Цитата не найдена в тексте дословно — подсветки нет. Найди ответ глазами; если его тут нет, это 2.</p>' : "") +
      '<div class="passage" id="passage">' + passage(c) + "</div>" +
      '<div class="flags">' + flags + "</div>" +
      '<div class="btns">' +
        '<button class="yes" onclick="mark(\'yes\')">1 · Годится</button>' +
        '<button class="no" onclick="mark(\'no\')">2 · Не годится</button>' +
        '<button class="gen" onclick="mark(\'generic\')">3 · Ясно и без текста</button>' +
        '<button onclick="openEdit()">4 · Поправить</button>' +
      "</div>" +
      '<div class="edit" id="edit">' +
        '<label>вопрос</label><input id="eq" value="' + esc(c.question).replace(/"/g, "&quot;") + '">' +
        '<label>ответ — одно-восемь слов, скопированных из текста как есть</label><input id="ea" value="' + esc(c.answer).replace(/"/g, "&quot;") + '">' +
        '<div class="btns"><button class="yes" onclick="mark(\'edited\')">Сохранить и дальше</button></div>' +
      "</div>" +
    "</div>" +
    '<p class="hint">Годится = ответ буквально есть в этом фрагменте, вопрос осмысленный, и без фрагмента на него не ответить. ' +
    'Фрагмент прокручивается — подсветка уже подведена к центру.</p>';
  const hit = document.querySelector("#passage mark");
  if (hit) {
    const box = document.getElementById("passage");
    box.scrollTop = Math.max(0, hit.offsetTop - box.clientHeight / 2 + hit.offsetHeight / 2);
  }
}

function openEdit() { document.getElementById("edit").classList.add("open"); document.getElementById("ea").focus(); }

function mark(verdict) {
  const c = DATA[i];
  const rec = { verdict: verdict, at: new Date().toISOString() };
  if (verdict === "edited") {
    rec.verdict = "yes";
    rec.question = document.getElementById("eq").value.trim();
    rec.answer = document.getElementById("ea").value.trim();
    rec.edited = true;
  }
  marks[c.id] = rec;
  save();
  i++;
  render();
}

document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") {
    if (e.key === "Enter") mark("edited");
    else if (e.key === "Escape") { document.getElementById("edit").classList.remove("open"); e.target.blur(); }
    return;
  }
  if (e.key === "1") mark("yes");
  else if (e.key === "2") mark("no");
  else if (e.key === "3") mark("generic");
  else if (e.key === "4") openEdit();
  else if (e.key === "ArrowLeft") { i = Math.max(0, i - 1); render(); }
  else if (e.key === "ArrowRight") { i = Math.min(DATA.length, i + 1); render(); }
});

document.getElementById("export").onclick = () => {
  const out = { marked_at: new Date().toISOString(), target: TARGET, marks: marks };
  const text = JSON.stringify(out, null, 1);
  const blob = new Blob([text], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "heldout_marks.json";
  a.click();
  document.getElementById("main").innerHTML =
    '<div class="card"><p>Файл <b>heldout_marks.json</b> ушёл в загрузки. Если браузер его не сохранил — скопируй текст ниже в файл с этим именем.</p>' +
    "<textarea>" + esc(text) + "</textarea></div>";
};

render();
</script>
"""


def write_page(cands: list[dict], target: int, page: Path | None = None) -> Path:
    page = page or PAGE
    html = (PAGE_TEMPLATE
            .replace("__DATA__", json.dumps(cands, ensure_ascii=False))
            .replace("__TARGET__", str(target)))
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(html, encoding="utf-8", newline="\n")
    return page


def merged_marks(paths: list[Path]) -> dict:
    """Several marks files as one: a later file wins where two mark the same candidate.

    The first review stopped at its target (73 marked of 200, the easiest first), which is what
    makes its number a dev-set figure. The rest is marked in a second sitting, on a page of only
    the unmarked cards, and both sittings are collected together."""
    out: dict = {}
    for p in paths:
        out.update(json.loads(p.read_text(encoding="utf-8"))["marks"])
    return out


def unmarked(cands: list[dict], marks: dict) -> list[dict]:
    """The candidates no marks file has decided yet, in the page's usual order."""
    return [c for c in cands if c["id"] not in marks]


def _paths(name: str) -> tuple[Path, Path]:
    """The corpus file and its manifest for a corpus name; the dev-set name keeps its old paths."""
    if name == "code_heldout_v2":
        return CORPUS, MANIFEST
    return OUT_DIR / f"{name}.json", MANIFEST.parent / f"{name}_review_manifest.json"


def collect(marks_path: Path | list[Path], name: str = "code_heldout_v2") -> dict:
    """Marks + candidates -> a corpus in the shape `code_sessions_eval.py --corpus` reads.

    `name` picks the corpus file and its manifest. The default is the dev-set corpus the
    registered claims were measured on; the clean corpus, built from every card, is written beside
    it under another name, so the dev-set figure stays reproducible."""
    corpus_path, manifest_path = _paths(name)
    cands = {c["id"]: c for c in json.loads(CANDIDATES.read_text(encoding="utf-8"))["candidates"]}
    marks = merged_marks(marks_path if isinstance(marks_path, list) else [marks_path])
    projects, kept, edited = [], 0, 0
    for cid, mk in marks.items():
        if mk.get("verdict") != "yes" or cid not in cands:
            continue
        c = cands[cid]
        question = mk.get("question") or c["question"]
        answer = mk.get("answer") or c["answer"]
        edited += bool(mk.get("edited"))
        kept += 1
        pid = c["id"]
        projects.append({
            "id": pid, "slug": pid, "stack": "a real coding session (held-out, hand-marked)",
            "source": c["source"], "facts": [], "replaced": [], "lessons": [],
            "sessions": [{"id": f"{pid}-s0", "idx": 0, "day": c["source"].get("day") or "2026-01-01",
                          "text": c["passage"], "attempts": 1, "fallback_sentences": [],
                          "states": ["fact"], "replaces": [], "lessons": [], "noise": False}],
            "questions": [{"id": f"{pid}-fact-0", "type": "fact", "question": question,
                           "answer": answer, "markers": [answer], "stale_markers": [],
                           "quote": c["quote"], "gold_sessions": [f"{pid}-s0"],
                           "hand_marked": True, "edited": bool(mk.get("edited"))}],
        })
    corpus = {"name": name, "schema_version": 1,
              "generator_model": "glm-4.7-flash + owner review", "seed": SEED,
              "purpose": "held-out questions over the owner's own sessions, accepted by hand",
              "projects": projects}
    corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    verdicts: dict = {}
    for mk in marks.values():
        verdicts[mk.get("verdict", "?")] = verdicts.get(mk.get("verdict", "?"), 0) + 1
    manifest = {
        "corpus": {"name": name, "path_outside_repo": str(corpus_path),
                   "sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
                   "questions": kept, "edited_by_hand": edited},
        "review": {"candidates": len(cands), "marked": len(marks), "verdicts": verdicts},
        "note": ("Questions over the owner's own transcripts, every one accepted by a person. "
                 "The automatic checks are reported per candidate but do not decide: the model "
                 "rephrasing a sentence it read correctly is a drop the reviewer repairs. "
                 "Neither the questions nor the transcripts are in the repository."),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8", newline="\n")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sessions", type=int, default=120, help="transcripts to read")
    ap.add_argument("--chunks", type=int, default=3, help="slices per transcript")
    ap.add_argument("--limit", type=int, default=200, help="candidates to generate at most")
    ap.add_argument("--target", type=int, default=45, help="questions the reviewer is aiming for")
    ap.add_argument("--dry", action="store_true", help="count slices, call no model")
    ap.add_argument("--page-only", action="store_true", help="rebuild the page from the saved file")
    ap.add_argument("--collect", nargs="*", default=[],
                    help="marks file(s) -> corpus + manifest; a later file wins on the same card")
    ap.add_argument("--skip-marked", nargs="*", default=[],
                    help="with --page-only: a page of only the cards these marks files leave "
                         "undecided, written to review_remaining.html")
    ap.add_argument("--name", default="code_heldout_v2",
                    help="with --collect: the corpus name (its file and manifest); the dev-set "
                         "default keeps its paths, a clean corpus goes beside it")
    args = ap.parse_args()

    if args.collect:
        man = collect([Path(p) for p in args.collect], args.name)
        corpus_path, manifest_path = _paths(args.name)
        print(json.dumps(man, ensure_ascii=False, indent=1))
        print(f"\ncorpus -> {corpus_path}\nmanifest -> {manifest_path}")
        return 0

    if args.page_only:
        cands = json.loads(CANDIDATES.read_text(encoding="utf-8"))["candidates"]
        if args.skip_marked:
            left = unmarked(cands, merged_marks([Path(p) for p in args.skip_marked]))
            page = write_page(left, len(left), OUT_DIR / "review_remaining.html")
            print(f"page -> {page}  ({len(left)} unmarked of {len(cands)} candidates)")
            return 0
        write_page(cands, args.target)
        print(f"page -> {PAGE}  ({len(cands)} candidates)")
        return 0

    paths = ch.candidates(ch.TRANSCRIPTS)
    rng = random.Random(SEED)
    rng.shuffle(paths)
    paths = paths[:args.sessions]
    if args.dry:
        n = sum(len(ch.read_session(p, args.chunks)) for p in paths)
        print(f"{len(paths)} transcripts -> {n} readable slices (>= {ch.MIN_CHARS} chars)")
        return 0

    print(f"reading {len(paths)} transcripts, up to {args.limit} candidates, "
          f"model {gcs.MODEL if hasattr(gcs, 'MODEL') else 'gen_code_sessions default'}", flush=True)
    cands = build(paths, args.limit, args.chunks)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CANDIDATES.write_text(json.dumps({"built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                      "seed": SEED, "candidates": cands}, ensure_ascii=False),
                          encoding="utf-8", newline="\n")
    write_page(cands, args.target)
    auto = sum(1 for c in cands if c["auto_accepted"])
    print(f"\ncandidates {len(cands)}  auto-accepted {auto}  "
          f"one check short {sum(1 for c in cands if c['passed'] == 4)}")
    print(f"candidates -> {CANDIDATES}\npage       -> {PAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
