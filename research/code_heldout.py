#!/usr/bin/env python3
"""The real held-out: questions over the owner's own coding sessions, read-only (ledger J3).

The synthetic corpus is what one model thinks a coding session reads like. The check on that is
a set of questions over sessions nobody generated: the owner's Claude Code transcripts on this
machine. This builder reads them - and only reads them - through the engine's own transcript
parser, so a session looks exactly as the capture hook sees it (boilerplate stripped, secrets
redacted), asks a model from a foreign family for one literal-fact question per session with the
sentence it comes from, and keeps the question only when

1. the quoted sentence is verbatim in the transcript (alignment, the same rule as evidence spans),
2. the answer is inside that sentence, and
3. a second pass with a different prompt - answer the question from the transcript alone -
   agrees with the first answer.

Nothing of the transcripts enters the repository. The questions, answers and quotes are written
**outside** the repository (`--out`, default in the polygon), in the same shape as
`research/data/code_sessions_v1.json` so `research/code_sessions_eval.py --corpus <that file>`
runs unchanged; the repository gets a manifest - counts, drop reasons, a hash per kept question
and per source transcript, the generator model - so the numbers published against it can be
traced without exposing a line of the sessions.

The held-out is touched once, at the end of the campaign, never during development.

    python research/code_heldout.py --sessions 60 --questions 40
    python research/code_heldout.py --dry     # list candidate sessions and sizes, call no model
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
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import sandbox_guard  # noqa: E402 - the engine parser is imported; nothing is written to any store

sandbox_guard.isolate(prefix="nevertwice_heldout_")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))
import memory_hook as m  # noqa: E402 - read_transcript, strip_injected_boilerplate, redact_secrets
import gen_code_sessions as gcs  # noqa: E402 - the foreign model call, markers, the corpus shape

TRANSCRIPTS = Path(os.environ.get("CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects")))
POLYGON_OUT = Path(os.environ.get("CODE_HELDOUT_OUT", r"D:\Coding\_nevertwice_polygon\code_heldout\code_heldout_v1.json"))
MANIFEST = HERE / "data" / "code_heldout_manifest.json"
SEED = 20260910
MIN_CHARS = 6000
MAX_CHARS = 40000                 # what the model reads (~10k tokens inside a 32k window); the engine's own extraction cap is 12k

ASK = """You read a transcript of a coding-agent session and write ONE question a developer on this project might ask a month later, whose answer is a LITERAL fact stated in the session: a value, a version, a path, a flag, a command, a port, a name, a decision. Not an opinion, not a summary.

Rules: the answer must be short (one to eight words) and must appear inside one sentence of the transcript; quote that sentence exactly. Prefer project-specific facts over generic programming knowledge.

TRANSCRIPT:
{transcript}

Return ONLY JSON: {{"question": "...", "answer": "...", "evidence_quote": "<the exact sentence from the transcript>"}}"""

ANSWER = """Answer the question using ONLY the transcript below. If the transcript does not state the answer, say "unknown". Keep the answer to at most eight words.

QUESTION: {question}

TRANSCRIPT:
{transcript}

Return ONLY JSON: {{"answer": "..."}}"""


def candidates(root: Path) -> list[Path]:
    """Top-level session transcripts (sub-agent transcripts sit deeper and are skipped)."""
    out = []
    for proj in sorted(root.iterdir()) if root.exists() else []:
        if proj.is_dir():
            out += sorted(p for p in proj.glob("*.jsonl") if p.is_file())
    return out


def read_session(path: Path, chunks: int = 1) -> list[dict]:
    """The transcript as the hook sees it: parsed, boilerplate out, secrets redacted - cut into
    `chunks` slices of at most MAX_CHARS, evenly spaced, each a session of its own. The owner's
    transcripts run to millions of characters; one question per whole transcript would read
    only its first hour."""
    try:
        parsed = m.read_transcript(str(path))
    except Exception:                                    # noqa: BLE001 - a malformed file is skipped
        return []
    body = m.redact_secrets(m.strip_injected_boilerplate(parsed.get("body") or ""))
    if len(body) < MIN_CHARS:
        return []
    ts = parsed.get("timestamp")
    day = None
    if ts:
        try:
            day = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            day = None
    n = max(1, min(chunks, len(body) // MAX_CHARS + 1))
    out = []
    for i in range(n):
        start = (len(body) - MAX_CHARS) * i // max(1, n - 1) if n > 1 else 0
        start = max(0, min(start, max(0, len(body) - MAX_CHARS)))
        piece = body[start:start + MAX_CHARS]
        if len(piece) >= MIN_CHARS:
            out.append({"body": piece, "day": day, "chars": len(body), "offset": start, "chunk": i})
    return out


def _agree(a: str, b: str) -> bool:
    """Two answers agree when one contains the other after folding, or they share most tokens."""
    na, nb = gcs.norm(a).strip(), gcs.norm(b).strip()
    if not na or not nb or nb == "unknown":
        return False
    if na in nb or nb in na:
        return True
    ta, tb = set(na.split()), set(nb.split())
    return len(ta & tb) / max(1, min(len(ta), len(tb))) >= 0.6


NUM_CTX = 32768


def _call(call, prompt: str, seed: int) -> dict:
    """The foreign model with a window wide enough for a 40k-character slice; a fake caller
    (tests) takes the two positional arguments only."""
    try:
        return call(prompt, seed, num_ctx=NUM_CTX, temperature=0.2)
    except TypeError:
        return call(prompt, seed)


def build_one(sess: dict, call, seed: int) -> tuple[dict | None, str]:
    """(question record, drop reason); the reason is '' when kept."""
    try:
        q = _call(call, ASK.format(transcript=sess["body"]), seed)
    except Exception as e:                               # noqa: BLE001
        return None, f"generation error {type(e).__name__}"
    question, answer, quote = (str(q.get("question") or "").strip(), str(q.get("answer") or "").strip(),
                               str(q.get("evidence_quote") or "").strip())
    if not question or not answer or not quote:
        return None, "incomplete"
    if not gcs.hit([quote], sess["body"]):
        return None, "quote not verbatim in transcript"
    if not gcs.hit([answer], quote):
        return None, "answer not inside the quote"
    if len(answer.split()) > 8:
        return None, "answer too long"
    try:
        second = _call(call, ANSWER.format(question=question, transcript=sess["body"]), seed + 1)
    except Exception as e:                               # noqa: BLE001
        return None, f"second pass error {type(e).__name__}"
    ans2 = str(second.get("answer") or "").strip()
    if not _agree(answer, ans2):
        return None, "second pass disagrees"
    return {"question": question, "answer": answer, "markers": sorted({answer, ans2} if _agree(answer, ans2) else {answer}),
            "quote": quote, "second_answer": ans2}, ""


def build(paths: list[Path], n_questions: int, call=gcs.ollama_json, verbose: bool = True,
          chunks: int = 3) -> tuple[dict, dict]:
    rng = random.Random(SEED)
    slices = []
    for path in paths:
        got = read_session(path, chunks)
        if not got:
            slices.append((path, None))
        for sess in got:
            slices.append((path, sess))
    rng.shuffle(slices)
    projects, drops, scanned, t0 = [], {}, 0, time.time()
    for path, sess in slices:
        if len(projects) >= n_questions:
            break
        scanned += 1
        if sess is None:
            drops["too short or unreadable"] = drops.get("too short or unreadable", 0) + 1
            continue
        rec, why = build_one(sess, call, SEED + scanned)
        if rec is None:
            drops[why] = drops.get(why, 0) + 1
            if verbose:
                print(f"  drop {path.parent.name[:24]}/{path.stem[:8]}: {why}", flush=True)
            continue
        src_hash = hashlib.sha256(sess["body"].encode("utf-8")).hexdigest()
        pid = f"ho{src_hash[:8]}"                     # the slice's own hash: two slices of one transcript differ
        projects.append({
            "id": pid, "slug": pid, "stack": "a real coding session (held-out)",
            "source": {"transcript_sha256": src_hash, "chars": sess["chars"], "offset": sess["offset"],
                       "chunk": sess["chunk"], "project_dir": path.parent.name},
            "facts": [], "replaced": [], "lessons": [],
            "sessions": [{"id": f"{pid}-s0", "idx": 0, "day": sess["day"] or "2026-01-01", "text": sess["body"],
                          "attempts": 1, "fallback_sentences": [], "states": ["fact"], "replaces": [], "lessons": [], "noise": False}],
            "questions": [{"id": f"{pid}-fact-0", "type": "fact", "question": rec["question"], "answer": rec["answer"],
                           "markers": rec["markers"], "stale_markers": [], "gold_sessions": [f"{pid}-s0"],
                           "quote": rec["quote"], "second_answer": rec["second_answer"]}],
        })
        if verbose:
            print(f"  kept {len(projects)}/{n_questions}  {path.parent.name[:24]}/{path.stem[:8]}  ({time.time() - t0:.0f}s)", flush=True)
    corpus = {"name": "code_heldout_v1", "schema_version": 1, "generator_model": gcs.MODEL, "seed": SEED,
              "generator_runtime": gcs.runtime_stamp(call),
              "purpose": ("One literal-fact question per real coding session of the owner's, drafted by a foreign model, "
                          "kept only when its quote is verbatim, its answer is inside the quote and a second pass agrees. "
                          "Private: lives outside the repository; research/data/code_heldout_manifest.json is its public record."),
              "days": [], "projects": projects,
              "counts": {"projects": len(projects), "sessions": len(projects), "questions": len(projects),
                         "by_type": {"fact": len(projects)}, "sessions_with_fallback": 0, "retries": 0}}
    manifest = {"name": "code_heldout_v1", "built": datetime.now().strftime("%Y-%m-%d"), "generator_model": gcs.MODEL,
                "generator_runtime": corpus["generator_runtime"], "seed": SEED,
                "transcripts_available": len(paths), "slices_scanned": scanned, "questions_kept": len(projects),
                "drops": drops, "min_chars": MIN_CHARS, "max_chars_read": MAX_CHARS,
                "items": [{"id": p["id"], "transcript_sha256": p["source"]["transcript_sha256"],
                           "question_sha256": hashlib.sha256((p["questions"][0]["question"] + "|" + p["questions"][0]["answer"]).encode("utf-8")).hexdigest()}
                          for p in projects],
                "note": ("No transcript text, question or answer is in this repository. The private file's SHA-256 is "
                         "recorded so a number published against it can be checked on the machine that holds it.")}
    return corpus, manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sessions", type=int, default=80, help="candidate transcripts to consider (largest first)")
    ap.add_argument("--questions", type=int, default=40)
    ap.add_argument("--out", default=str(POLYGON_OUT))
    ap.add_argument("--chunks", type=int, default=3, help="slices per transcript, each at most MAX_CHARS")
    ap.add_argument("--dry", action="store_true", help="list candidates, call no model, write nothing")
    args = ap.parse_args()
    paths = candidates(TRANSCRIPTS)
    paths = sorted(paths, key=lambda p: -p.stat().st_size)[:args.sessions]
    print(f"transcripts: {len(paths)} candidates under {TRANSCRIPTS}")
    if args.dry:
        for p in paths[:20]:
            got = read_session(p, args.chunks)
            print(f"  {p.parent.name[:28]:28s} {p.stem[:8]}  {p.stat().st_size / 1e6:6.1f} MB  "
                  f"{'readable ' + str(got[0]['chars']) + ' chars, ' + str(len(got)) + ' slices, ' + str(got[0]['day']) if got else 'skipped (short/unreadable)'}")
        return 0
    out = Path(args.out)
    if out.resolve().is_relative_to(ROOT.resolve()):
        print("refusing to write the held-out inside the repository")
        return 2
    corpus, manifest = build(paths, args.questions, chunks=args.chunks)
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = gcs.dump(corpus)
    out.write_bytes(raw)
    manifest["private_file_sha256"] = hashlib.sha256(raw).hexdigest()
    MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}  questions {manifest['questions_kept']}  drops {manifest['drops']}\nmanifest -> {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
