"""A deterministic store: the same sessions in, the same bytes out, with no GPU and no network.

This is the harness the proof of a pure refactor rests on. It is deliberately NOT the sandbox the
other suites use: `tests/_sandbox.py` makes a run deterministic by *disabling* the extractor and
the embedder, and disabling them cuts out exactly the branches that carry this project's headline
numbers - the write-path displacement rules, the sleep-time judge, the as-of walk, the semantic
tier. A coverage run of a stubbed-out ingest enters 54% of the engine's statements and **none** of
`as_of`, `_same_fact_verdict`, `_replacement_guard`, `supersede_note`, `_same_replacement`,
`_mark_contested`, `_earlier_text` or `embed_text`. A proof that never enters the code it certifies
is not a proof.

So here the model and the embedder are *replaced by deterministic implementations* rather than
switched off: the extractor answers from a fixed table keyed by the session, and the embedder is a
pure function of the text. Every arithmetic path downstream - cosine, fusion, BM25, the abstention
floor, the twin gate - runs for real on values that are the same on every machine and every run.

What it produces is a snapshot: every note's path and content, the frontmatter of each, the index,
and the ordered result of a fixed set of queries, with the clock and the sandbox path masked out.
Two runs of the same code must give the same snapshot, and a refactor that changes one byte of it
has changed behaviour, whatever it says on the tin.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "nevertwice"))

#: Volatile by nature: the run's clock, the sandbox's path, and the host's own name. Masked
#: rather than removed, so a snapshot still proves that SOMETHING was written there.
#: A bare date counts too. The guard ledger stamps `born` with today's date and no clock, so a
#: snapshot recorded on one day failed on the next - a proof that cries wolf at midnight is worse
#: than no proof, because the next real difference gets read as the calendar again.
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?")
SID_RE = re.compile(r"\b[0-9a-f]{8}\b")
#: Bookkeeping files key on absolute transcript paths, and a sandbox lands somewhere new every
#: run. Where the sandbox landed is a property of the host, not a decision the engine made.
TMP_RE = re.compile(r"[A-Za-z]:\\\\?[^\"\n]*?[Tt]e?mp\\\\?[^\"\n]*|/tmp/[^\"\n]*")
#: Byte offsets into those transcripts move with them, for the same reason.
NUM_RE = re.compile(r'"(from_byte|size|bytes|mtime)":\s*-?\d+')


def _vec(text: str, dims: int = 48) -> list[float]:
    """A deterministic stand-in for the embedder: a pure function of the text, unit length.

    Real arithmetic downstream is the point. A stub returning None (what the offline sandbox
    does) skips the semantic tier entirely, so fusion, the similarity floor and the twin gate
    are never exercised and a refactor of any of them certifies clean.
    """
    out: list[float] = []
    seed = text.strip().lower().encode("utf-8")
    i = 0
    while len(out) < dims:
        digest = hashlib.sha256(seed + str(i).encode()).digest()
        out += [(b - 127.5) / 127.5 for b in digest]
        i += 1
    out = out[:dims]
    norm = math.sqrt(sum(x * x for x in out)) or 1.0
    return [x / norm for x in out]


class Extractor:
    """A fixed extraction per session, answered by matching the session's marker in the prompt.

    Keyed by content rather than by call order so the table stays readable and a change in the
    number of calls shows up as a missing key instead of a silently shifted answer.
    """

    def __init__(self, table: dict[str, dict], judge=None):
        self.table = table
        self.judge = judge
        self.calls: list[str] = []

    def __call__(self, prompt: str, project: str | None = None) -> dict:
        #: the same-fact prompt is answered in the judge's own schema, so the engine's
        #: `_same_fact_verdict` parses a real verdict rather than being replaced by one
        if '"relation"' in prompt and self.judge is not None:
            self.calls.append("<judge>")
            #: the verdict is a function of the whole prompt, not of a layout this harness would
            #: have to keep in step with the engine's wording - the first draft parsed "OLD:"/"NEW:"
            #: lines, got empty strings, answered "separate" for everything, and left the three
            #: replacement guards cold while looking like it had judged
            return {"old_settles": "the earlier statement", "new_settles": "the later statement",
                    "relation": self.judge(prompt)}
        for marker, answer in self.table.items():
            if marker in prompt:
                self.calls.append(marker)
                return json.loads(json.dumps(answer))      # a fresh copy: callers mutate it
        self.calls.append("<none>")
        return {"project_relevant": True, "patterns": [], "mistakes": [], "decisions": [],
                "session_summary": "nothing durable", "context_update": ""}


def install(m, table: dict[str, dict], *, judge=None) -> Extractor:
    """Make the run deterministic by replacing the PROCESS boundary, not the engine's functions.

    The distinction is the whole value of this harness. Setting `m.embed_text` or
    `m._same_fact_verdict` directly is easier and silently excises the code a proof is supposed to
    certify: measured on the first draft of this file, stubbing those two left `embed_text`,
    `_same_fact_verdict`, `_replacement_guard` and `_same_replacement` cold, so a canary in any of
    them survived. Replacing the HTTP call and the JSON-returning model call instead leaves every
    engine function on the path, running for real over values that are the same on every machine.

    `judge` answers the same-fact prompt; it is reached through the engine's own
    `_same_fact_verdict`, which builds the prompt and parses the verdict.
    """
    extractor = Extractor(table, judge=judge)
    #: the one model door: `generate_json` routes cloud-or-local and every caller goes through it
    m.call_ollama = extractor
    m.call_cloud = extractor
    m.llm_available = lambda: True
    m.ollama_alive = lambda timeout_s=4: True
    #: the one embedder door: `embed_text` still runs - prefixes, cache keys, signature, the lot
    m._embed_http = lambda url, payload, headers, timeout=None: {
        "embeddings": [_vec(payload.get("input") or payload.get("prompt") or "")]}
    m._embed_cloud = lambda text, kind=None, timeout=None: _vec(text)
    m.embedder_available = lambda timeout_s=4: True
    m.git_autocommit = lambda *a, **k: None
    return extractor


def session(path: Path, *, cwd: str, marker: str, day: str, turns: int = 3) -> str:
    """Write one transcript. The marker is what the extractor table keys on."""
    lines = []
    for i in range(turns):
        stamp = f"{day}T1{i}:00:00"
        lines.append(json.dumps({"type": "user", "message": {"content": f"{marker} step {i}"},
                                 "cwd": cwd, "timestamp": stamp}))
        lines.append(json.dumps({"type": "assistant",
                                 "message": {"content": [{"type": "text",
                                                          "text": f"noted: {marker} {i}"}]},
                                 "cwd": cwd, "timestamp": stamp}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def snapshot(store: Path, extra: dict | None = None) -> dict:
    """Every file in the store, masked and hashed, plus whatever the caller measured beside it."""
    files: dict[str, str] = {}
    for p in sorted(store.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(store).as_posix()
        if rel.startswith(".logs/") or rel.endswith(".bak"):
            continue                                       # a log is a diary, not a state
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            files[rel] = f"<binary {p.stat().st_size}>"
            continue
        text = TS_RE.sub("<TS>", text.replace(str(store), "<STORE>"))
        text = SID_RE.sub("<SID>", text)
        text = NUM_RE.sub(r'"\1": <N>', TMP_RE.sub("<TMP>", text))
        files[rel] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return {"files": files, **(extra or {})}


def diff(a: dict, b: dict) -> list[str]:
    """Human-readable first differences between two snapshots - the whole point on a miss."""
    out = []
    fa, fb = a.get("files", {}), b.get("files", {})
    for k in sorted(set(fa) | set(fb)):
        if fa.get(k) != fb.get(k):
            out.append(f"  {k}: {fa.get(k, '<absent>')} -> {fb.get(k, '<absent>')}")
    for k in sorted(set(a) | set(b)):
        if k == "files":
            continue
        if a.get(k) != b.get(k):
            out.append(f"  {k}: {a.get(k)!r} -> {b.get(k)!r}")
    return out
