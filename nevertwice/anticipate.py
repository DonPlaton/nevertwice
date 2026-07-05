#!/usr/bin/env python3
"""Active memory, axis B - anticipatory memory (predict the error BEFORE the action).

A guard (axis A) fires on an exact known code pattern: a sniper, high precision, low recall -
it only catches the *literal* repeat. Anticipatory memory is the early-warning radar: it fires
when the agent's **current trajectory resembles a past failure situation**, catching a *novel*
manifestation of a known failure mode that no regex would match. Given a description of what
the agent is about to do (its plan, the files it is touching, the last few steps), it scores
similarity to every past mistake and, only if the top risk clears an **adaptive threshold**,
surfaces ONE precise warning - never a dump. See `research/ACTIVE_MEMORY.md`.

Token economy is the design center: below threshold it is **silent (0 tokens)**; above, it
spends a single line (the single most-likely failure). Spend is proportional to predicted
risk, not paid every turn. The threshold is Popperian - a failure-mode that keeps crying wolf
has its bar raised (its false alarms are recorded and it goes quiet), while one that keeps
helping stays sensitive. Lexical by default (0-dep, no GPU); an optional embedding path
sharpens the score when the local embedder is free.

    python -m nevertwice.anticipate "refactoring the orchestrator, touching prism_orchestrator.py" --project p
    python -m nevertwice.anticipate feedback <mistake-stem> false_alarm

Reads mistake notes from the vault; the small adaptation state lives in
`<vault>/anticipate.json` (atomic). Off the always-inject path entirely.
"""
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m          # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_TAU = float(os.environ.get("NEVERTWICE_ANTICIPATE_TAU", "0.22"))   # min risk to fire
# ↑ calibrated on the real vault: generic trajectories top out ~0.19 (kept silent) while strong
# failure-resemblances clear it. Lexical B is precision-first (few false alarms) with moderate
# recall; the adaptive threshold silences any leak, and the embedding blend lifts recall.
FP_STEP = float(os.environ.get("NEVERTWICE_ANTICIPATE_FP_STEP", "0.06"))  # bar raise per false alarm
MAX_CHECK_CHARS = 20000
_MIN_TOKLEN = 4              # only contentful tokens count toward similarity (drop noise)


def _state_path() -> Path:
    return m.VAULT / "anticipate.json"


def load_state() -> dict:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    m.VAULT.mkdir(parents=True, exist_ok=True)
    m.write_atomic(_state_path(), json.dumps(state, ensure_ascii=False, indent=1))


def _content_tokens(text: str) -> set:
    """Contentful tokens (length ≥ _MIN_TOKLEN) - drops short noise so similarity keys on the
    distinctive terms of a failure, not on 'the'/'a'/'is'."""
    return {t for t in m._tokens(text or "") if len(t) >= _MIN_TOKLEN}


def build_signatures(project=None) -> list[dict]:
    """A lexical signature per past mistake: `{stem, project, recurrence, tokens, text}`. Built
    from the vault's mistake notes (title + desc + prevention + entities). Cheap; no embedder."""
    notes = m._iter_project_notes(m.slug_project(project)) if project else m._iter_all_notes()
    sigs = []
    for n in notes:
        if n.get("ntype") != "mistake":
            continue
        text = " ".join(str(x) for x in (n.get("title", ""), n.get("desc", ""),
                        n.get("prevention", ""), " ".join(n.get("entities", []) or [])))
        toks = _content_tokens(text)
        if not toks:
            continue
        sigs.append({"stem": n.get("stem", ""), "project": n.get("project", ""),
                     "recurrence": n.get("recurrence", 1), "tokens": toks,
                     "title": n.get("title", ""), "prevention": n.get("prevention", "")})
    return sigs


def _recur_weight(recurrence: int) -> float:
    """A failure that recurred is more worth a warning - a gentle, capped multiplier."""
    return 1.0 + 0.08 * min(max(recurrence - 1, 0), 5)


def build_idf(sigs) -> dict:
    """Inverse document frequency of each token across the failure signatures. Down-weights
    terms that are common *in this vault* (e.g. a project name that tags half the notes) so a
    shared token counts for what it is worth as evidence HERE, not in the abstract."""
    n = len(sigs) or 1
    df = {}
    for s in sigs:
        for t in s["tokens"]:
            df[t] = df.get(t, 0) + 1
    return {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}


def risk_score(traj_tokens: set, sig: dict, idf: dict | None = None) -> float:
    """Similarity of a trajectory to one failure signature, in [0,1]. IDF-weighted overlap
    *coverage* - the share of the failure signature's distinctive mass the trajectory hits -
    softly damped so a lone-token coincidence can't fire, and recurrence-weighted. Coverage
    (not cosine) keeps short trajectories stable and the radar precise. Lexical: the
    always-available floor; the embedding blend in `anticipate()` is what lifts recall."""
    st = sig["tokens"]
    if not traj_tokens or not st:
        return 0.0
    shared = traj_tokens & st
    if not shared:
        return 0.0
    w = (lambda t: idf.get(t, 1.0)) if idf else (lambda t: 1.0)
    covered = sum(w(t) for t in shared)
    sig_mass = sum(w(t) for t in st) or 1.0
    coverage = covered / sig_mass                          # fraction of the failure's mass hit
    inter = len(shared)
    damp = inter / (inter + 1.0)                           # 1 token → 0.5, 3 → 0.75 (kills coincidences)
    return max(0.0, min(1.0, coverage * damp * _recur_weight(sig["recurrence"]) * 3.0))


def _effective_tau(state: dict, stem: str) -> float:
    """Popperian bar: base threshold raised by this failure-mode's recorded false alarms, so a
    predictor that cries wolf goes quiet; capped so it can always be re-triggered by a strong
    signal."""
    s = state.get(stem, {})
    return min(0.9, BASE_TAU + FP_STEP * s.get("false_alarms", 0))


def anticipate(trajectory: str, project=None, *, k: int = 1, sigs=None, state=None,
               use_embeddings: bool = False) -> list[dict]:
    """Predict the most likely failure the current `trajectory` is heading toward. Returns up
    to `k` `{stem, risk, title, message}` whose risk clears the adaptive threshold - or `[]`
    (SILENT, 0 tokens) when nothing does. `k` defaults to 1: one precise warning, never a dump.
    Lexical by default; `use_embeddings=True` blends a semantic cosine when the embedder+cache
    are available (sharper, still 0-context-token). This is the radar; keep it quiet."""
    if not trajectory:
        return []
    traj = trajectory[:MAX_CHECK_CHARS]
    traj_toks = _content_tokens(traj)
    sigs = build_signatures(project) if sigs is None else sigs
    state = load_state() if state is None else state
    idf = build_idf(sigs)
    emb_blend = _embedding_blend(traj, sigs) if use_embeddings else None
    scored = []
    for sig in sigs:
        r = risk_score(traj_toks, sig, idf)
        # blend the semantic signal only when there is SOME lexical overlap - otherwise bge-m3's
        # ~0.42 background cosine on unrelated text could fire a warning at zero lexical evidence
        # (code-review, 2026-07). The blend sharpens a real lexical hit; it never creates one.
        if emb_blend and r > 0 and sig["stem"] in emb_blend:
            r = max(r, 0.5 * r + 0.5 * emb_blend[sig["stem"]])   # blend, never lower a lexical hit
        if r >= _effective_tau(state, sig["stem"]):
            scored.append((r, sig))
    scored.sort(key=lambda x: -x[0])
    out = []
    for r, sig in scored[:k]:
        prev = (sig.get("prevention") or "").strip()
        msg = f"resembles a past failure ({sig['title'][:60]}); risk {r:.2f}"
        if prev:
            msg += f" - {prev[:120]}"
        out.append({"stem": sig["stem"], "risk": round(r, 3),
                    "title": sig["title"], "message": msg})
    return out


def _embedding_blend(trajectory: str, sigs) -> dict | None:
    """Optional semantic sharpening: cosine of the trajectory vs each mistake's cached vector.
    Returns {stem: cosine01} or None if the embedder/cache are unavailable - the lexical path
    always stands alone, so this never becomes a hard dependency."""
    try:
        if not m.embedder_available(2):
            return None
        qv = m.embed_text(trajectory, kind=m.query_embed_kind())
        if not qv:
            return None
        cache = m.load_embed_cache()
    except Exception:
        return None
    out = {}
    for sig in sigs:
        rec = cache.get(sig["stem"])
        v = rec.get("vec") if isinstance(rec, dict) else None
        if isinstance(v, list):
            out[sig["stem"]] = max(0.0, min(1.0, (m.cosine(qv, v) + 1) / 2))   # cos[-1,1]→[0,1]
    return out or None


def feedback(stem: str, outcome: str, *, state=None, persist: bool = True) -> dict:
    """Adapt the predictor. `outcome`: 'helped' (a real, avoided failure - keep it sensitive)
    or 'false_alarm' (fired but the situation was fine - raise its bar). Returns the updated
    per-failure state. This is how a cry-wolf predictor goes quiet without a human editing a
    threshold."""
    owns = state is None
    state = load_state() if owns else state
    s = state.setdefault(stem, {"helped": 0, "false_alarms": 0})
    if outcome == "helped":
        s["helped"] += 1
    elif outcome == "false_alarm":
        s["false_alarms"] += 1
    if persist and owns:
        save_state(state)
    return s


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    argv = sys.argv[1:]
    if not argv:
        print("usage: anticipate \"<trajectory text>\" [--project P] [--k N] [--embed]\n"
              "       anticipate feedback <mistake-stem> <helped|false_alarm>")
        return
    if argv[0] == "feedback":
        stem = argv[1] if len(argv) > 1 else ""
        outcome = argv[2] if len(argv) > 2 else ""
        s = feedback(stem, outcome)
        print(f"{stem}: helped={s['helped']} false_alarms={s['false_alarms']} "
              f"→ effective_tau={_effective_tau(load_state(), stem):.2f}")
        return
    traj = argv[0]
    hits = anticipate(traj, project=m.argval(argv, "project"),
                      k=int(m.argval(argv, "k", "1")), use_embeddings="--embed" in argv)
    if not hits:
        print("ok - no anticipated failure above threshold (0 tokens spent).")
        return
    for h in hits:
        print(f"  ⚠ (risk {h['risk']}) {h['message']}")
        print(f"    if this was a real catch: anticipate feedback {h['stem']} helped")


if __name__ == "__main__":
    main()
