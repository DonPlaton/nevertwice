#!/usr/bin/env python3
"""Self-check for the token-savings ledger + activity view (stats.py).

Verifies: recording accumulates totals + daily buckets; the recall-saving is
store_tokens - injected (floored); the panel renders aligned; and - the load-bearing
guarantee - a broken ledger never raises out of record()/recall_saving(). Temp vault,
no network, no model."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import memory_hook as m          # noqa: E402
import stats as st               # noqa: E402

P = F = 0


def check(name, cond):
    global P, F
    print(("  ok  " if cond else "  FAIL ") + name)
    P += 1 if cond else 0
    F += 0 if cond else 1


def test_record_accumulates():
    with tempfile.TemporaryDirectory() as t:
        m.VAULT = Path(t)
        st.record("guard", saved=15)
        st.record("recall", saved=100)
        st.record("recall", saved=50)
        st.record("counterfactual", saved=60)
        tot = st.load()["totals"]
        check("tokens_saved sums", tot["tokens_saved"] == 225)
        check("recalls counted", tot["recalls"] == 2)
        check("guards counted", tot["guards_fired"] == 1)
        check("counterfactuals counted", tot["counterfactuals"] == 1)
        check("interventions total", tot["interventions"] == 4)
        day = list(st.load()["by_day"].values())[0]
        check("daily bucket saved", day["saved"] == 225)


def test_recall_saving_is_store_minus_injected():
    with tempfile.TemporaryDirectory() as t:
        m.VAULT = Path(t)
        d = st.load()
        d["store_tokens"] = 1000
        st._save(d)
        check("saving = store - injected", st.recall_saving("x" * 80) == 1000 - 20)
        check("saving floored at 0", st.recall_saving("x" * 8000) == 0)


def test_panel_rows_are_aligned():
    with tempfile.TemporaryDirectory() as t:
        m.VAULT = Path(t)
        st.record("recall", saved=123456)
        rows = st.render_panel().splitlines()
        widths = {len(r) for r in rows}
        check("every panel row is the same width", len(widths) == 1)
        check("sparkline renders", "activity (14d)" in st.render_panel())


def test_broken_ledger_never_raises():
    with tempfile.TemporaryDirectory() as t:
        m.VAULT = Path(t)
        (Path(t) / "savings.json").write_text("{ not json ][", encoding="utf-8")
        # load() must recover to a fresh dict and record() must not raise
        st.record("recall", saved=10)
        check("corrupt ledger recovered, record didn't raise", st.load()["totals"]["recalls"] >= 0)
        check("recall_saving on empty ledger is 0", st.recall_saving("abc") == 0)


if __name__ == "__main__":
    print("=== stats self-checks ===")
    test_record_accumulates()
    test_recall_saving_is_store_minus_injected()
    test_panel_rows_are_aligned()
    test_broken_ledger_never_raises()
    print(f"\nstats: {P} passed, {F} failed")
    sys.exit(1 if F else 0)
