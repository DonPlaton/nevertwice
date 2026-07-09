#!/usr/bin/env python3
"""Token-savings ledger and activity view - proof, in numbers, that the memory is working.

Every recall injects the *relevant* slice of the store instead of what a passive memory does:
re-paste the whole store into every prompt. The difference, summed across turns, is real tokens
you did not spend. This module records that (and guard fires, counterfactuals) into a tiny JSON
ledger next to the notes, and renders a terminal panel with an activity sparkline so you can watch
the memory earn its keep.

    python -m nevertwice.stats            # the panel
    python -m nevertwice.stats --json     # machine-readable

Recording is best-effort and off the critical path: a failure here never affects recall. The
ledger is `<vault>/savings.json` (atomic writes). Numbers are estimates (a token is ~4 chars, and
the baseline is "a memory that dumps the whole store every turn"); they are labelled as such.
Stdlib only.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m          # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_SPARK = "▁▂▃▄▅▆▇█"


def _ledger_path() -> Path:
    return m.VAULT / "savings.json"


def est_tokens(s: str) -> int:
    """Rough token count: ~4 characters per token (the usual back-of-envelope)."""
    return max(0, len(s or "") // 4)


def load() -> dict:
    p = _ledger_path()
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except (OSError, ValueError):
            pass
    return {"created": date.today().isoformat(), "store_tokens": 0,
            "totals": {"tokens_saved": 0, "recalls": 0, "guards_fired": 0,
                       "counterfactuals": 0, "interventions": 0},
            "by_day": {}}


def _save(d: dict) -> None:
    try:
        m.VAULT.mkdir(parents=True, exist_ok=True)
        m.write_atomic(_ledger_path(), json.dumps(d, ensure_ascii=False, indent=1))
    except Exception:
        pass                     # a cosmetic counter must never break a real operation


def record(kind: str, saved: int = 0) -> None:
    """Log one intervention. `kind` in {'recall','guard','counterfactual'}; `saved` is the
    estimated tokens it avoided. Best-effort: any failure is swallowed. An atomic write keeps the
    file uncorrupted under the rare concurrent hook; a lost update at worst under-counts."""
    try:
        d = load()
        t = d["totals"]
        t["tokens_saved"] = t.get("tokens_saved", 0) + max(0, int(saved or 0))
        t["interventions"] = t.get("interventions", 0) + 1
        key = {"recall": "recalls", "guard": "guards_fired",
               "counterfactual": "counterfactuals"}.get(kind)
        if key:
            t[key] = t.get(key, 0) + 1
        today = date.today().isoformat()
        day = d.setdefault("by_day", {}).setdefault(
            today, {"saved": 0, "recalls": 0, "guards": 0, "counterfactuals": 0})
        day["saved"] += max(0, int(saved or 0))
        day[{"recall": "recalls", "guard": "guards",
             "counterfactual": "counterfactuals"}.get(kind, "recalls")] += 1
        # keep the daily log bounded (a year is plenty for the sparkline)
        if len(d["by_day"]) > 400:
            for k in sorted(d["by_day"])[:-400]:
                d["by_day"].pop(k, None)
        _save(d)
    except Exception:
        pass


def refresh_store_tokens() -> int:
    """Cache the size of the live store in tokens, so the hot path can price 'dump the whole
    store' without re-scanning every note. Called at sleep-time (SessionEnd / consolidation)."""
    try:
        total = 0
        for n in m._iter_all_notes():
            total += est_tokens(f"{n.get('title','')} {n.get('desc','')} {n.get('prevention','')}")
        d = load()
        d["store_tokens"] = total
        _save(d)
        return total
    except Exception:
        return 0


def store_tokens() -> int:
    v = int(load().get("store_tokens", 0) or 0)
    if v <= 0:                           # cold start: price the baseline on first read so a
        v = refresh_store_tokens()       # session-1 recall records a real saving, not ~0
    return v


def recall_saving(injected_text: str) -> int:
    """Tokens a passive 'dump the whole store each turn' memory would have spent here, minus what
    this recall actually injected. Floored at 0."""
    return max(0, store_tokens() - est_tokens(injected_text))


# ── rendering ─────────────────────────────────────────────────────────

def sparkline(values: list) -> str:
    if not values:
        return ""
    hi = max(values) or 1
    return "".join(_SPARK[min(len(_SPARK) - 1, int(v / hi * (len(_SPARK) - 1)))] for v in values)


def _human(n: int) -> str:
    n = int(n)
    for unit, div in (("M", 1_000_000), ("k", 1_000)):
        if round(n / div, 1) >= 1:       # promote 999_999 to 1.0M, not 1000.0k
            return f"{n / div:.1f}{unit}"
    return str(n)


def _plural(n: int, noun: str, suffix: str = "") -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}{suffix}"


def last_days(d: dict, days: int = 14) -> list:
    today = date.today()
    out = []
    for i in range(days - 1, -1, -1):
        k = (today - timedelta(days=i)).isoformat()
        out.append(d.get("by_day", {}).get(k, {}))
    return out


def render_panel(d: dict | None = None) -> str:
    d = load() if d is None else d
    t = d.get("totals", {})
    span = last_days(d, 14)
    saved_series = [x.get("saved", 0) for x in span]
    act_series = [x.get("recalls", 0) + x.get("guards", 0) + x.get("counterfactuals", 0)
                  for x in span]
    W = 60

    def row(text: str = "") -> str:
        # pad/truncate to exactly W visible chars so the right border always lines up
        if len(text) > W:
            text = text[:W]
        return f"│{text}{' ' * (W - len(text))}│"

    def _c(n, noun, suffix=""):          # human count + correct plural, so a 5-digit count fits
        return f"{_human(n)} {noun}{'' if n == 1 else 's'}{suffix}"
    counts = (f"  {_c(t.get('recalls', 0), 'recall')}   "
              f"{_c(t.get('guards_fired', 0), 'guard', ' fired')}   "
              f"{_c(t.get('counterfactuals', 0), 'counterfactual')}")
    out = [
        "╭" + "─" * W + "╮",
        row("  Nevertwice - what the memory bought you"),
        "├" + "─" * W + "┤",
        row(f"  ~{_human(t.get('tokens_saved', 0))} tokens saved"),
        row("  vs re-injecting the whole store into every prompt"),
        row(),
        row(counts),
        row(),
        row(f"  activity (14d)  {sparkline(act_series)}"),
        row(f"  tokens   (14d)  {sparkline(saved_series)}"),
        "╰" + "─" * W + "╯",
    ]
    return "\n".join(out)


def summary_line(d: dict | None = None) -> str:
    """One line for a digest / the user's terminal - no agent tokens spent."""
    d = load() if d is None else d
    t = d.get("totals", {})
    if not t.get("interventions"):
        return ""
    return (f"Nevertwice: ~{_human(t.get('tokens_saved', 0))} tokens saved so far vs re-injecting "
            f"the whole store each turn ({_plural(t.get('interventions', 0), 'intervention')}, "
            f"{_plural(t.get('guards_fired', 0), 'guard', ' fired')}).")


def main() -> None:
    argv = sys.argv[1:]
    d = load()
    if "--json" in argv:
        print(json.dumps({"totals": d.get("totals", {}), "store_tokens": d.get("store_tokens", 0),
                          "by_day": d.get("by_day", {})}, ensure_ascii=False, indent=1))
        return
    if not d.get("totals", {}).get("interventions"):
        print("Nevertwice has not recorded any interventions yet - use it for a session or two, "
              "then check back. (The counter fills as recall fires, guards catch repeats, and "
              "counterfactuals answer.)")
        return
    print(render_panel(d))


if __name__ == "__main__":
    main()
