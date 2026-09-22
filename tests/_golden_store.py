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
import tempfile
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
#: Masking a temporary path by its SHAPE needs every shape, and there is always one more. This
#: knew Windows and `/tmp/`; a GitHub runner uses `/home/runner/work/_temp/`, and macOS uses
#: `/var/folders/<hash>/<hash>/T/`, which carries no `tmp`, `temp` or `_temp` segment at all.
#: So the host's ANSWER is masked instead of a list of the answers it might give:
#: `tempfile.gettempdir()` is what this suite already calls to place the store, and a form nobody
#: has seen is covered the day a host returns it. The shape patterns stay as a fallback, for a
#: path recorded on ANOTHER machine and carried in a fixture, but they are no longer the rule.
#: The failure on every Linux and macOS job of CI's first matrix run and the macOS one the
#: auditing session named next are the same defect: a list where a computation belongs.
_TMPDIR = tempfile.gettempdir()
_TMP_FORMS = [
    re.escape(_TMPDIR),                          # as the host spells it
    re.escape(_TMPDIR.replace("\\", "\\\\")),    # as JSON escapes it
    re.escape(_TMPDIR.replace("\\", "/")),       # as a POSIX-ified copy spells it
]
TMP_RE = re.compile(
    "|".join([form + r'[^"\n]*' for form in _TMP_FORMS] + [
        r'[A-Za-z]:\\\\?[^"\n]*?[Tt]e?mp\\\\?[^"\n]*',
        r'/[^"\n]*?/(?:_temp|tmp|temp|Temp|TEMP|T)/[^"\n]*',
        r'/tmp/[^"\n]*',
    ])
)
#: Byte offsets into those transcripts move with them, for the same reason.
NUM_RE = re.compile(r'"(from_byte|size|bytes|mtime)":\s*-?\d+')


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _vec(text: str, dims: int = 48) -> list[float]:
    """A deterministic stand-in for the embedder: a pure function of the text, unit length.

    Real arithmetic downstream is the point. A stub returning None (what the offline sandbox
    does) skips the semantic tier entirely, so fusion, the similarity floor and the twin gate
    are never exercised and a refactor of any of them certifies clean.

    Deterministic is not enough on its own. This used to be `sha256` of the WHOLE text, which
    made every pair of texts orthogonal noise: the similarity floor then rejected every
    candidate and the semantic tier contributed nothing, so the branches above were exercised
    only in their rejecting arm. Measured 2026-09-19 - a query sharing four words with a note
    scored 0.005 against it while an unrelated sentence scored 0.134. Signed feature hashing
    over tokens keeps the function pure and gives real lexical similarity, so a note and a
    query about the same thing are actually near each other.
    """
    out = [0.0] * dims
    for tok in _TOKEN_RE.findall((text or "").lower()):
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        out[int.from_bytes(digest[:4], "big") % dims] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in out))
    if not norm:                    # no tokens at all: a fixed direction, still unit length
        out[0], norm = 1.0, 1.0
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
    #: the one EMBEDDING door: `embed_text` still runs - prefixes, cache keys, signature, the
    #: lot. The model door above and `ollama_alive` are separate doors with separate stubs;
    #: `_refuse_every_socket` below is what covers the engine as a whole.
    m._embed_http = lambda url, payload, headers, timeout=None: {
        "embeddings": [_vec(payload.get("input") or payload.get("prompt") or "")]}
    m._embed_cloud = lambda text, kind=None, timeout=None: _vec(text)
    m.embedder_available = lambda timeout_s=4: True
    m.git_autocommit = lambda *a, **k: None
    _refuse_every_socket()
    return extractor


def _refuse_every_socket() -> None:
    """The tripwire that makes the two stubs above provable rather than asserted.

    `install` names two doors, and for a year one of them was not a door: with the default
    `NEVERTWICE_EMBED_PROVIDER=ollama`, `embed_text` built its own `urllib` request and went to
    127.0.0.1:11434, so `_embed_http` was stubbed and never called. Measured 2026-09-19 under
    this harness: `embed_text` returned 1024 live bge-m3 dimensions where `_vec` returns 48.
    The golden proof was ranked by a running model, and it failed the same day when that model
    was busy - `Embed failed: TimeoutError`, snapshot mismatch, on an unchanged tree.

    Naming doors is a claim; refusing sockets is a measurement. Anything that reaches out from
    here now raises with the URL in the message instead of quietly borrowing the machine.
    """
    import urllib.request

    def refuse(req, *a, **k):                       # noqa: ANN001,ANN002
        url = getattr(req, "full_url", req)
        raise AssertionError(f"the golden store reached the network: {url!r}. Every model and "
                             "embedder call must go through a door `install()` replaces.")

    urllib.request.urlopen = refuse


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


#: The backreference template, as its own constant: a `` inside an edited line is the first thing
#: a shell heredoc eats, and it did, twice. Written from `chr(92)` so no editor or tool
#: between here and the file can lose it.
PLACE_N = '"' + chr(92) + '1": <N>'


def mask(text: str, store: Path) -> str:
    """The text a snapshot hashes: machine-specific spellings replaced by markers."""
    text = TS_RE.sub("<TS>", text.replace(str(store), "<STORE>"))
    text = SID_RE.sub("<SID>", text)
    return NUM_RE.sub(PLACE_N, TMP_RE.sub("<TMP>", text))


def masked_text(path: Path, store: Path) -> str:
    return mask(path.read_text(encoding="utf-8"), store)


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
        files[rel] = hashlib.sha256(mask(text, store).encode("utf-8")).hexdigest()[:16]
    return {"files": files, **(extra or {})}


def diff(a: dict, b: dict, store: Path | None = None) -> list[str]:
    """Human-readable first differences between two snapshots - the whole point on a miss.

    When `store` is given, a file whose hash moved is also shown MASKED, truncated. A snapshot
    entry is a hash, so a mismatch on another machine says only "this file differs" and the next
    step is a guess; on 2026-09-22 that cost two matrix runs guessing at
    `.processed_sessions.json`. The masked text is what the hash is taken of, so printing it says
    what actually differs rather than that something does.
    """
    out = []
    fa, fb = a.get("files", {}), b.get("files", {})
    for k in sorted(set(fa) | set(fb)):
        if fa.get(k) != fb.get(k):
            out.append(f"  {k}: {fa.get(k, '<absent>')} -> {fb.get(k, '<absent>')}")
            if store is not None:
                try:
                    out.append("      here: " + masked_text(store / k, store)[:400].replace("\n", " "))
                except OSError as exc:
                    out.append(f"      here: <unreadable: {type(exc).__name__}>")
    for k in sorted(set(a) | set(b)):
        if k == "files":
            continue
        if a.get(k) != b.get(k):
            out.append(f"  {k}: {a.get(k)!r} -> {b.get(k)!r}")
    return out
