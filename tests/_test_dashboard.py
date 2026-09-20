#!/usr/bin/env python3
"""Self-check for dashboard.py - verifies the HTML is well-formed, self-contained
(no external asset / network reference), reflects the store counts, and HTML-escapes
note titles (an injection-shaped title must not break the page). Mocks the iterators."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))
import _env_guard  # noqa: F401  hermetic: scrub store env BEFORE package imports bake path constants (incidents 2026-08-13 / 2026-08-18)
import memory_hook as m          # noqa: E402
import dashboard as dash         # noqa: E402

TODAY = datetime.now().strftime("%Y-%m-%d")

SUP = [{"stem": "old1", "superseded_by": "new1", "project": "proj", "ntype": "decision",
        "title": "Old policy", "date": TODAY, "status": "superseded"}]
LIVE = [
    {"stem": "new1", "project": "proj", "ntype": "decision", "title": "New policy", "date": TODAY},
    {"stem": "x", "project": "proj", "ntype": "pattern",
     "title": "<script>alert(1)</script> & friends", "date": TODAY},   # XSS-shaped title
]


def _install():
    m._iter_superseded_notes = lambda project=None: list(SUP)
    m._iter_all_notes = lambda: list(LIVE)
    m._iter_project_notes = lambda project: [n for n in LIVE if n["project"] == project]
    m.entity_graph = lambda project=None, top=20: {"cuda": {"notes": 3, "links": []}}
    m.slug_project = lambda s: (s or "").lower()


def test_html_is_wellformed_and_selfcontained():
    _install()
    h = dash.build_html(None, days=30)
    assert h.startswith("<!DOCTYPE html>") and h.rstrip().endswith("</html>")
    # self-contained: no external stylesheet/script/img/font fetch
    for bad in ("<link", "src=\"http", "href=\"http://", "<script src", "@import"):
        assert bad not in h, f"external reference leaked: {bad}"
    # the one allowed href is the repo footer link (https), nothing else fetches
    assert h.count("http") == h.count("https://github.com/DonPlaton/nevertwice"), "unexpected URL"
    print("ok test_html_is_wellformed_and_selfcontained")


def test_counts_and_sections_present():
    _install()
    h = dash.build_html(None, days=30)
    assert ">2<" in h          # 2 live notes card
    assert "By project" in h and "Contradiction ledger" in h and "Most-connected entities" in h
    assert "Old policy" in h and "New policy" in h    # the supersession pair is rendered
    assert "cuda" in h
    print("ok test_counts_and_sections_present")


def test_title_is_escaped():
    _install()
    h = dash.build_html(None, days=30)
    assert "<script>alert(1)</script>" not in h          # raw payload must NOT appear
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in h  # escaped form must
    assert "&amp; friends" in h
    print("ok test_title_is_escaped")


def test_a_bad_days_is_a_usage_error_not_a_traceback():
    """`int(m.argval(argv, "days", "30"))` raised ValueError straight out of main(), so
    `nevertwice-dashboard --days=last-week` printed a traceback: the one output that tells a
    user nothing about what to type instead. Every other CLI in the package answers a bad
    flag with a line and an exit code."""
    import io
    from contextlib import redirect_stderr

    for bad in ("abc", "", "-5", "0", "3.5"):
        err = io.StringIO()
        argv = sys.argv
        sys.argv = ["nevertwice-dashboard", f"--days={bad}", "--no-open"]
        try:
            with redirect_stderr(err):
                dash.main()
        except SystemExit as e:
            rc = int(e.code or 0)
        except BaseException as e:                 # noqa: BLE001 - a traceback IS the defect
            raise AssertionError(f"--days={bad!r} raised {type(e).__name__}: {e}") from None
        else:
            rc = 0
        finally:
            sys.argv = argv
        assert rc == 2, f"--days={bad!r} exited {rc}"
        assert "--days" in err.getvalue(), (bad, err.getvalue())
    print("ok test_a_bad_days_is_a_usage_error_not_a_traceback")


def test_api_surface():
    _install()
    import api
    h = api.dashboard(None, days=14)
    assert h.startswith("<!DOCTYPE html>")
    print("ok test_api_surface")


if __name__ == "__main__":
    test_html_is_wellformed_and_selfcontained()
    test_counts_and_sections_present()
    test_title_is_escaped()
    test_a_bad_days_is_a_usage_error_not_a_traceback()
    test_api_surface()
    print("\nall dashboard self-checks passed")
