#!/usr/bin/env python3
"""Self-check for guards.py (active memory, axis A). Verifies the 0-token-until-fired hot
path, scope matching, the Popperian lifecycle (advisory→blocking→retired), ReDoS-safe
pattern validation, override-as-feedback, and deterministic generation from a mistake.
Points the ledger at a temp dir and mocks the note iterators - no network, no real vault."""
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nevertwice"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import memory_hook as m          # noqa: E402
import guards as G               # noqa: E402


def _isolate(tmp):
    m.VAULT = Path(tmp)
    G.K_PROMOTE = 3
    G.M_RETIRE = 3


def test_safe_pattern_rejects_redos_and_junk():
    assert G.safe_pattern(r"device\s*=\s*['\"]cpu['\"]")
    assert not G.safe_pattern(r"(a+)+")              # nested quantifier → ReDoS
    assert not G.safe_pattern(r"(a|aa)+")            # quantified alternation → ReDoS (code-review)
    assert not G.safe_pattern(r"(foo|foobar)*")      # overlapping alternation blowup
    assert G.safe_pattern(r"(cpu|gpu)")              # legit alternation (no outer quantifier) is fine
    assert not G.safe_pattern("(" * 60)              # too long / uncompilable
    assert not G.safe_pattern("")
    print("ok test_safe_pattern_rejects_redos_and_junk")


def test_check_is_silent_until_a_match():
    with tempfile.TemporaryDirectory() as t:
        _isolate(t)
        g = G.make_guard(r"\.to\(['\"]cpu['\"]\)", "CPU fallback halved throughput", project="p")
        gs = []
        G.register(gs, g)
        G.save_guards(gs)
        assert G.check("model = build()", project="p") == []          # 0 tokens: nothing fires
        hits = G.check("model.to('cpu')", project="p")
        assert len(hits) == 1 and hits[0]["status"] == "advisory"
        # scope gates it: wrong project → silent
        assert G.check("model.to('cpu')", project="other") == []
    print("ok test_check_is_silent_until_a_match")


def test_lifecycle_promote_then_retire():
    with tempfile.TemporaryDirectory() as t:
        _isolate(t)
        g = G.make_guard(r"eval\(", "avoid eval", project="p")
        gs = [g]
        G.save_guards(gs)
        gid = g["id"]
        # 3 distinct-session corroborations → advisory promotes to blocking
        for s in ("s1", "s2", "s3"):
            G.feedback(gid, "helped", session_id=s)
        assert G.load_guards()[0]["status"] == "blocking"
        # same session again must NOT over-count
        G.feedback(gid, "helped", session_id="s1")
        assert G.load_guards()[0]["corroborations"] == 3
        # 3 false positives → demote blocking→advisory (first breach), counter resets
        for _ in range(3):
            G.feedback(gid, "false_positive", reason="intended here")
        cur = G.load_guards()[0]
        assert cur["status"] == "advisory", cur["status"]
        assert "intended here" in cur["overrides"]
        # 3 more → advisory→retired, and a retired guard never fires again
        for _ in range(3):
            G.feedback(gid, "false_positive")
        assert G.load_guards()[0]["status"] == "retired"
        assert G.check("eval(x)", project="p") == []
    print("ok test_lifecycle_promote_then_retire")


def test_blocking_sorts_before_advisory():
    with tempfile.TemporaryDirectory() as t:
        _isolate(t)
        a = G.make_guard(r"foo", "advisory one", project="p")
        b = G.make_guard(r"bar", "blocking one", project="p")
        b["status"] = "blocking"
        G.save_guards([a, b])
        hits = G.check("foo and bar", project="p")
        assert [h["status"] for h in hits] == ["blocking", "advisory"]
    print("ok test_blocking_sorts_before_advisory")


def test_deterministic_generation_from_mistake():
    with tempfile.TemporaryDirectory() as t:
        _isolate(t)
        note = {"stem": "2026-01-01-p-mistake-cpu", "ntype": "mistake", "project": "p",
                "title": "model-left-on-cpu",
                "desc": "training silently used torch.device('cpu') and halved throughput",
                "prevention": "Assert device == 'cuda' before the training loop"}
        g = G.propose_from_mistake(note, use_llm=False)     # no LLM → deterministic path
        assert g is not None and G.safe_pattern(g["pattern"]), g
        assert "torch" in g["pattern"]                       # lifted the dotted call from desc
        assert g["born_from"] == ["2026-01-01-p-mistake-cpu"]
        assert g["status"] == "advisory"
    print("ok test_deterministic_generation_from_mistake")


def test_generate_from_vault_dedups():
    with tempfile.TemporaryDirectory() as t:
        _isolate(t)
        notes = [
            {"stem": "n1", "ntype": "mistake", "project": "p", "recurrence": 5,
             "title": "t1", "desc": "d", "prevention": "Assert foo.bar() is valid"},
            {"stem": "n2", "ntype": "pattern", "project": "p", "title": "ignore me"},
        ]
        m._iter_all_notes = lambda: list(notes)
        m._iter_project_notes = lambda p: [n for n in notes if n["project"] == p]
        m.slug_project = lambda s: (s or "").lower()
        m.llm_available = lambda: False
        n1 = G.generate_from_vault(use_llm=False)
        assert n1 == 1, n1                              # only the mistake becomes a guard
        n2 = G.generate_from_vault(use_llm=False)
        assert n2 == 0, n2                              # idempotent: already distilled
    print("ok test_generate_from_vault_dedups")


def test_antipattern_rules_match_the_bug():
    # a mistake about f-string SQL → a guard whose pattern matches the BUGGY construct,
    # not the fix (the deterministic anti-pattern rules, no LLM)
    note = {"stem": "s1", "ntype": "mistake", "project": "p", "title": "sql-built-by-fstring",
            "desc": "a filter was interpolated into the SQL string",
            "prevention": "never build SQL by f-string - use query parameters"}
    g = G.propose_from_mistake(note, use_llm=False)
    assert g is not None
    assert re.search(g["pattern"], "cursor.execute(f\"SELECT * FROM t WHERE id='{x}'\")"), g["pattern"]
    assert not re.search(g["pattern"], "cursor.execute('SELECT 1', (x,))")   # fix code stays silent
    # float-money
    fm = {"stem": "s2", "ntype": "mistake", "project": "p", "title": "float money drift",
          "desc": "storing prices as float caused rounding drift", "prevention": "use Decimal"}
    gf = G.propose_from_mistake(fm, use_llm=False)
    assert gf and re.search(gf["pattern"], "total = float(request.form['amount'])")
    print("ok test_antipattern_rules_match_the_bug")


def test_check_slugs_project_argument():
    with tempfile.TemporaryDirectory() as t:
        _isolate(t)
        m.slug_project = lambda s: (s or "").lower().replace("-", "_")   # real slug behaviour
        g = G.make_guard(r"eval\(", "avoid eval", project="svc_000")     # stored slugged
        G.save_guards([g])
        # a caller passing the RAW (unslugged) project name must still match
        assert G.check("eval(x)", project="svc-000")
        assert G.check("eval(x)", project="svc_000")
        print("ok test_check_slugs_project_argument")


def test_pretooluse_hotpath_silent_and_fires():
    import io
    with tempfile.TemporaryDirectory() as t:
        _isolate(t)
        G.save_guards([G.make_guard(r"eval\(", "avoid eval() on untrusted input")])

        def run(session):
            buf, old = io.StringIO(), sys.stdout
            sys.stdout = buf
            try:
                m.emit_pretooluse_guard(session, t)
            finally:
                sys.stdout = old
            return buf.getvalue().strip()

        # clean action → SILENT (0 tokens)
        assert run({"tool_name": "Edit", "tool_input": {"new_string": "x = 1 + 1"}}) == ""
        # non-guardable tool → SILENT (hook only scans code-writing tools)
        assert run({"tool_name": "Read", "tool_input": {"file_path": "x"}}) == ""
        # matching action → warning as additionalContext, not a block (advisory default)
        out = run({"tool_name": "Bash", "tool_input": {"command": "eval(x)"}})
        assert "additionalContext" in out and "guard" in out.lower()
        assert "permissionDecision" not in out              # advisory: never blocks by default
    print("ok test_pretooluse_hotpath_silent_and_fires")


def test_pretooluse_enforce_denies_blocking():
    import io
    with tempfile.TemporaryDirectory() as t:
        _isolate(t)
        g = G.make_guard(r"rm -rf", "destructive command")
        g["status"] = "blocking"
        G.save_guards([g])
        m.GUARD_ENFORCE = True                              # opt-in enforcement
        try:
            buf, old = io.StringIO(), sys.stdout
            sys.stdout = buf
            try:
                m.emit_pretooluse_guard({"tool_name": "Bash",
                                         "tool_input": {"command": "rm -rf /tmp/x"}}, t)
            finally:
                sys.stdout = old
            out = buf.getvalue()
            assert '"permissionDecision": "deny"' in out
        finally:
            m.GUARD_ENFORCE = False
    print("ok test_pretooluse_enforce_denies_blocking")


if __name__ == "__main__":
    test_safe_pattern_rejects_redos_and_junk()
    test_check_is_silent_until_a_match()
    test_lifecycle_promote_then_retire()
    test_blocking_sorts_before_advisory()
    test_deterministic_generation_from_mistake()
    test_generate_from_vault_dedups()
    test_antipattern_rules_match_the_bug()
    test_check_slugs_project_argument()
    test_pretooluse_hotpath_silent_and_fires()
    test_pretooluse_enforce_denies_blocking()
    print("\nall guards self-checks passed")
