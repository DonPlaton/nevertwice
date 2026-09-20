#!/usr/bin/env python3
"""RESEARCH - is the blast-radius checker worth having? (I4)

I1 showed it can be made to fire on dependencies rather than on paperwork. It did not show that
firing is useful. This labels every finding it made on 150 commits and compares it against the
cheap baseline the task names: ``git grep`` for the changed symbol.

The thresholds this run is judged against were written first, in
``research/BLAST_RADIUS_DECISION.md``, and committed before this file existed.

**Labelling is mechanical wherever it can be**, because the author of a checker is not a neutral
judge of its output. Three checks decide a site with no opinion involved:

* **resolution** - does the site's module reference *this* symbol, or a same-named one of its
  own? A lexical collision is a false positive and the import graph says so;
* **arity** - does a call site satisfy the new parameter list, allowing for ``*args``/``**kwargs``?
* **resolvability** - after a removal, does the name still resolve in that module?

Anything the three cannot decide is left ``undecided`` and labelled by hand in
``research/blast_radius_labels.json``, whose reasons are committed with it.

    python research/blast_radius_precision.py            # label, compare, write the artifact
    python research/blast_radius_precision.py --print    # summarise the committed artifact

Standard library only. Python 3.10+. Needs full history.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CALIBRATION = ROOT / "research" / "blast_radius_calibration.json"
LABELS = ROOT / "research" / "blast_radius_labels.json"
ARTIFACT = ROOT / "research" / "blast_radius_precision.json"

sys.path.insert(0, str(ROOT / "research"))
from blast_radius_calibration import Repo, _make_worktree, _drop_worktree  # noqa: E402

DEPENDENCY = re.compile(r"^(?P<symbol>[\w.]+): contract changed, (?P<n>\d+) reference")


def _load_checker():
    path = ROOT / "research" / "invariants_lab" / "blast_radius_deleted.py"
    spec = importlib.util.spec_from_file_location("_nt_br_precision", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BR = _load_checker()


# ---------------------------------------------------------------------------
# mechanical labelling
# ---------------------------------------------------------------------------


def _imports_from(source: str, symbol: str) -> set[str]:
    """Modules this file imports *symbol* from, as dotted hints (may be relative)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return set()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == symbol:
                    found.add((node.module or "").lstrip("."))
    return found


def _defines(source: str, symbol: str) -> bool:
    return symbol in BR.extract_symbols(source)


def _all_module_aliases(tree: ast.AST) -> dict[str, str]:
    """Every name that stands for a module, wherever the import happens to sit.

    The checker's own version reads top-level statements only, which is right for a facade -
    a facade IS a module-level binding. It is wrong here: `import numpy as np` lives inside a
    try/except in several research modules, and missing it makes `np.where(...)` look like a
    call into this project.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                # `from pkg import mod as m` also binds a module name.
                aliases.setdefault(alias.asname or alias.name, f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            args = node.value.args
            if len(args) == 1 and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases[target.id] = args[0].value
    return aliases


def _alias_owner(site_source: str, symbol: str, line: int) -> str | None:
    """The module an ``alias.symbol`` reference at *line* goes through, if it goes through one."""
    try:
        tree = ast.parse(site_source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    aliases = _all_module_aliases(tree)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == symbol
                and isinstance(node.value, ast.Name)
                and getattr(node, "lineno", None) == line):
            return aliases.get(node.value.id)
    return None


def resolution_verdict(site_source: str, symbol: str, owner_module: str, line: int,
                       same_file: bool = False) -> str | None:
    """`false` when the site cannot be talking about the changed symbol; else None.

    Three decidable cases, all from the import graph rather than from an opinion. The site
    imports the name from a module that is not the one whose contract changed; it defines a
    symbol of that name itself, so the reference binds locally; or it reaches the name through a
    module alias that resolves somewhere else. Each is a word match, not a dependency.
    """
    if same_file:
        # The site sits in the file the contract changed in. The definition this rule would
        # find is the changed symbol itself, so "it defines its own" is the opposite of true.
        return None
    owner_tail = owner_module.split(".")[-1]
    sources = _imports_from(site_source, symbol)
    if sources:
        if not any(hint.split(".")[-1] == owner_tail for hint in sources if hint):
            return "false"
        return None
    through = _alias_owner(site_source, symbol, line)
    if through is not None:
        return None if through.split(".")[-1] == owner_tail else "false"
    if _defines(site_source, symbol):
        return "false"
    return None


def _parameter_text(signature: str) -> str | None:
    """The parameter list out of a rendered signature, or None when it cannot be trusted.

    The renderer emits `@decorator name(params) -> Return`, truncating any piece longer than its
    limit with an ellipsis. Decorators and the return annotation are stripped; a truncated
    signature is refused outright, because a parameter list missing its tail would produce a
    confident and wrong arity verdict.
    """
    text = signature.strip()
    while text.startswith("@"):
        _, _, text = text.partition(" ")
        text = text.strip()
    if text.startswith("class ") or " :: " in text:
        # `class Claims() :: get, is_withdrawn, value` lists MEMBERS, not constructor
        # parameters. Reading its empty parentheses as "takes nothing" made every
        # construction look over-supplied.
        return None
    start = text.find("(")
    if start < 0 or "\u2026" in text:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        depth += (ch == "(") - (ch == ")")
        if depth == 0:
            return text[start + 1:i]
    return None


def _parameters(signature: str) -> tuple[int, int, set[str], bool, bool] | None:
    """(min positional, max positional, keyword names, has *args, has **kwargs) from a rendered
    signature such as ``f(a, b=1, *, c=2) -> None``. None when it cannot be parsed."""
    params = _parameter_text(signature)
    if params is None:
        return None
    try:
        tree = ast.parse(f"def _f({params}): pass")
    except SyntaxError:
        return None
    args = tree.body[0].args
    positional = args.posonlyargs + args.args
    defaults = len(args.defaults)
    names = {a.arg for a in positional} | {a.arg for a in args.kwonlyargs}
    required_kwonly = {a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is None}
    return (
        len(positional) - defaults,
        len(positional),
        names | required_kwonly,
        args.vararg is not None,
        args.kwarg is not None,
    )


def _takes_a_receiver(qualname: str, new_signature: str) -> bool:
    """True only for a method whose rendered signature still names self/cls first.

    `api.guards_check(x)` and `obj.method(x)` are both Attribute calls, and only the second
    supplies an implicit first argument. Telling them apart needs the SYMBOL, not the call: a
    method's qualname is dotted, and its rendered signature carries the receiver.
    """
    if "." not in qualname:
        return False
    params = _parameter_text(new_signature)
    if not params:
        return False
    first = params.split(",")[0].strip().split(":")[0].split("=")[0].strip()
    return first in {"self", "cls"}


def arity_verdict(site_source: str, line: int, symbol: str, new_signature: str,
                  qualname: str = "") -> str | None:
    """`true` when the call at *line* cannot satisfy *new_signature*; `false` when it can."""
    shape = _parameters(new_signature)
    if shape is None:
        return None
    low, high, names, star, kwstar = shape
    receiver = _takes_a_receiver(qualname or symbol, new_signature)
    try:
        tree = ast.parse(site_source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or getattr(node, "lineno", None) != line:
            continue
        func = node.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if called != symbol:
            continue
        implicit = 1 if (receiver and isinstance(func, ast.Attribute)) else 0
        positional = len(node.args) + implicit
        if any(isinstance(a, ast.Starred) for a in node.args):
            return None
        keywords = {k.arg for k in node.keywords if k.arg}
        if any(k.arg is None for k in node.keywords):
            return None
        if positional > high and not star:
            return "true"
        if positional + len(keywords) < low:
            return "true"
        unknown = keywords - names
        if unknown and not kwstar:
            return "true"
        return "false"
    return None


def resolvability_verdict(site_source: str, symbol: str, after_owner: str | None,
                          owner_module: str) -> str | None:
    """After a removal: does the name still resolve AT THE SITE?

    The question is about the site's imports, not the owner's definitions. A removal is usually
    a move, and the site - the owner's own file included - very often imports the name back from
    wherever it went. Only a site whose sole source for the name is the owner is exposed.
    """
    owner_tail = owner_module.split(".")[-1]
    sources = {h for h in _imports_from(site_source, symbol) if h}
    if sources:
        if any(h.split(".")[-1] != owner_tail for h in sources):
            return "false"          # it comes in from somewhere the removal did not touch
        if after_owner is not None:
            return "false" if _defines(after_owner, symbol) else "true"
        return None                 # the owner's post-commit state is not visible here
    if _defines(site_source, symbol):
        return "false"              # the site has its own definition of the name
    return None                     # reached some other way - a module attribute, a star import


# ---------------------------------------------------------------------------
# the two arms
# ---------------------------------------------------------------------------


def grep_arm(repo: Repo, sha: str, symbols: list[str], changed_lines: dict[str, set[int]]) -> dict:
    """B1: lexical mentions of each changed symbol, minus the lines the diff touched.

    Given the symbol list for free, which makes it stronger than a real one-liner and therefore
    a fairer test of what the ast machinery adds on top.
    """
    per_symbol: dict[str, list[list]] = {}
    for symbol in symbols:
        # `git grep` exits 1 when nothing matches. For a baseline, "no mentions" is the
        # answer, so the exit code is read rather than raised on.
        proc = subprocess.run(
            ["git", "-C", str(repo.path), "grep", "-n", "--fixed-strings",
             "--word-regexp", "-I", symbol, sha],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        if proc.returncode not in (0, 1):
            raise RuntimeError(f"git grep failed for {symbol!r}: {proc.stderr.strip()}")
        out = proc.stdout
        hits = []
        for line in out.splitlines():
            # git grep <rev> prints "<rev>:<path>:<line>:<text>"
            body = line.split(":", 1)[1] if line.startswith(sha) else line
            parts = body.split(":", 2)
            if len(parts) < 3 or not parts[1].isdigit():
                continue
            path, lineno = parts[0], int(parts[1])
            if not path.endswith(".py"):
                continue
            touched = changed_lines.get(path)
            if touched and any(abs(lineno - t) <= BR.LINE_FUZZ for t in touched):
                continue
            hits.append([path, lineno])
        per_symbol[symbol] = hits
    return per_symbol


def collect(repo: Repo, sha: str) -> dict:
    """Everything both arms need for one commit, from the object database."""
    changed = repo.changed(sha)
    parent_tree = repo.tree(f"{sha}^", suffix="")
    tree = repo.tree(sha, suffix="")
    before, after = {}, {}
    for status, path in changed:
        if not status.startswith("A") and path in parent_tree:
            before[path] = repo.blob(parent_tree[path])
        if not status.startswith("D") and path in tree:
            after[path] = repo.blob(tree[path])
    scan = {p: repo.blob(o) for p, o in tree.items() if p.endswith(".py")}
    verdict = BR.check_sources(before, after, scan=scan, ignore=[])
    changed_lines = {p: BR.changed_lines(before.get(p, ""), after.get(p, "")) for p in
                     set(before) | set(after)}
    contracts = {c.qualname.rsplit(".", 1)[-1]: {"qualname": c.qualname, "reason": c.reason,
                                                 "path": c.path, "before": c.before,
                                                 "after": c.after}
                 for c in verdict.contract_changes}
    return {"verdict": verdict, "before": before, "after": after, "scan": scan,
            "changed_lines": changed_lines, "contracts": contracts}


def label_findings(sha: str, state: dict, manual: dict) -> list[dict]:
    verdict = state["verdict"]
    findings = []
    for problem in verdict.problems:
        match = DEPENDENCY.match(problem)
        if not match:
            continue
        symbol = match.group("symbol")
        change = next((c for c in verdict.contract_changes if c.qualname == symbol), None)
        owner_path = change.path if change else ""
        owner_module = Path(owner_path).stem
        after_owner = state["after"].get(owner_path)
        sites = []
        for ref in verdict.unhandled.get(symbol, []):
            site_source = state["scan"].get(ref.path) or state["after"].get(ref.path) or ""
            key = f"{sha[:9]}|{symbol}|{ref.path}:{ref.lineno}"
            verdict_for_site, why = None, ""
            if site_source:
                verdict_for_site = resolution_verdict(site_source, symbol.split(".")[-1],
                                                      owner_module, ref.lineno,
                                                      same_file=(ref.path == owner_path))
                if verdict_for_site:
                    why = "resolution: the site does not reference this symbol"
            if verdict_for_site is None and change and change.reason == "signature" and site_source:
                verdict_for_site = arity_verdict(site_source, ref.lineno, symbol.split(".")[-1],
                                                 change.after, symbol)
                if verdict_for_site:
                    why = f"arity: call vs new signature {change.after}"
            if verdict_for_site is None and change and change.reason == "removed" and site_source:
                verdict_for_site = resolvability_verdict(site_source, symbol.split(".")[-1],
                                                         after_owner, owner_module)
                if verdict_for_site:
                    why = "resolvability: the name after removal"
            source = "mechanical"
            if verdict_for_site is None:
                entry = manual.get(key)
                if entry:
                    verdict_for_site, why, source = entry["label"], entry["reason"], "manual"
                else:
                    verdict_for_site, why, source = "undecided", "", "unlabelled"
            sites.append({"key": key, "path": ref.path, "line": ref.lineno,
                          "kind": ref.kind, "confidence": ref.confidence,
                          "label": verdict_for_site, "why": why, "source": source})
        label = ("true" if any(s["label"] == "true" for s in sites)
                 else "undecided" if any(s["label"] == "undecided" for s in sites)
                 else "false")
        findings.append({
            "sha": sha, "symbol": symbol, "reason": change.reason if change else "?",
            "path": owner_path, "before": change.before if change else "",
            "after": change.after if change else "",
            "sites": sites, "label": label,
        })
    return findings


def label_site(sha: str, symbol: str, path: str, line: int, kind: str,
               contract: dict, state: dict, manual: dict) -> dict:
    """One site, labelled by the same three rules the checker's own findings get.

    Shared deliberately: a comparison where each arm is judged by a different standard is not a
    comparison. The only asymmetry left is that the baseline has no `kind`, so every site is
    treated as a call when the change was a signature change.
    """
    owner_path = contract.get("path", "")
    owner_module = Path(owner_path).stem
    site_source = state["scan"].get(path) or state["after"].get(path) or ""
    key = f"{sha[:9]}|{symbol}|{path}:{line}"
    label, why, source = None, "", "mechanical"
    if site_source:
        label = resolution_verdict(site_source, symbol, owner_module, line,
                                   same_file=(path == owner_path))
        if label:
            why = "resolution: the site does not reference this symbol"
    if label is None and contract.get("reason") == "signature" and site_source:
        label = arity_verdict(site_source, line, symbol, contract.get("after", ""),
                              contract.get("qualname", symbol))
        if label:
            why = "arity: call vs the new signature"
    if label is None and contract.get("reason") == "removed" and site_source:
        label = resolvability_verdict(site_source, symbol,
                                      state["after"].get(owner_path), owner_module)
        if label:
            why = "resolvability: the name after removal"
    if label is None:
        entry = manual.get(key)
        if entry:
            label, why, source = entry["label"], entry["reason"], "manual"
        else:
            label, why, source = "undecided", "", "unlabelled"
    return {"key": key, "path": path, "line": line, "kind": kind,
            "label": label, "why": why, "source": source}


def label_grep(rows: list[dict], states: dict, manual: dict, threshold: int) -> list[dict]:
    findings = []
    for row in rows:
        hits = {s: v for s, v in row.get("sites", {}).items() if len(v) >= threshold}
        if not hits:
            continue
        state = states[row["sha"]]
        for symbol, sites in hits.items():
            contract = state["contracts"].get(symbol, {})
            labelled = [label_site(row["sha"], symbol, path, line, "grep",
                                   contract, state, manual)
                        for path, line in sites]
            label = ("true" if any(s["label"] == "true" for s in labelled)
                     else "undecided" if any(s["label"] == "undecided" for s in labelled)
                     else "false")
            findings.append({"sha": row["sha"], "symbol": symbol,
                             "reason": contract.get("reason", "?"),
                             "path": contract.get("path", ""),
                             "sites": labelled, "label": label})
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--print", dest="show", action="store_true")
    parser.add_argument("--out", default=str(ARTIFACT))
    args = parser.parse_args(argv)

    if args.show:
        report(json.loads(Path(args.out).read_text(encoding="utf-8")))
        return 0

    calibration = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    shas = calibration["shas"]
    flagged = [r["sha"] for r in calibration["arms"]["undeclared"]["rows"] if not r["ok"]]
    manual = json.loads(LABELS.read_text(encoding="utf-8"))["labels"] if LABELS.is_file() else {}

    target = _make_worktree(calibration["base"])
    try:
        with Repo(target) as repo:
            findings, grep_rows, states = [], [], {}
            for sha in shas:
                state = collect(repo, sha)
                states[sha] = state
                verdict = state["verdict"]
                if sha in flagged:
                    findings.extend(label_findings(sha, state, manual))
                symbols = sorted({c.qualname.rsplit(".", 1)[-1]
                                  for c in verdict.contract_changes})
                hits = grep_arm(repo, sha, symbols, state["changed_lines"]) if symbols else {}
                grep_rows.append({"sha": sha, "symbols": symbols,
                                  "hits": {k: len(v) for k, v in hits.items()},
                                  "sites": hits,
                                  "contract_changes": len(verdict.contract_changes),
                                  "checker_flagged": sha in flagged})
    finally:
        _drop_worktree(target)

    payload = {
        "generated_by": "research/blast_radius_precision.py",
        "thresholds": "research/BLAST_RADIUS_DECISION.md",
        "base": calibration["base"],
        "commits": len(shas),
        "checker": {
            "flagged_commits": len(flagged),
            "findings": findings,
        },
        "grep": {"rows": grep_rows},
    }
    payload["summary"] = summarise(payload)
    payload["summary"]["why_false"] = taxonomy(payload["checker"]["findings"])
    payload["summary"]["site_rules"] = site_rules(payload["checker"]["findings"])
    threshold = payload["summary"]["grep_matched"]["threshold"]
    payload["grep"]["findings"] = label_grep(grep_rows, states, manual, threshold)
    payload["grep"]["threshold"] = threshold
    payload["summary"]["grep_labels"] = _counts(payload["grep"]["findings"])
    decided = payload["summary"]["grep_labels"]["true"] + payload["summary"]["grep_labels"]["false"]
    payload["summary"]["grep_precision"] = (
        round(payload["summary"]["grep_labels"]["true"] / decided, 4) if decided else None)
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    report(payload)
    return 0


def _accepts_everything(old_signature: str, new_signature: str) -> bool:
    """True when every call the old signature accepted, the new one still accepts."""
    a, b = _parameters(old_signature), _parameters(new_signature)
    if a is None or b is None:
        return False
    a_low, a_high, a_names, a_star, a_kw = a
    b_low, b_high, b_names, b_star, b_kw = b
    if b_low > a_low:
        return False                       # something became required
    if not b_star and b_high < a_high:
        return False                       # positional room shrank
    if not b_kw and not a_names <= b_names:
        return False                       # a keyword stopped being accepted
    return True


def _bare(signature: str) -> list[str] | None:
    """Parameter names and defaults, with annotations stripped, for an annotation-only test."""
    params = _parameter_text(signature)
    if params is None:
        return None
    try:
        tree = ast.parse(f"def _f({params}): pass")
    except SyntaxError:
        return None
    args = tree.body[0].args
    out = [a.arg for a in args.posonlyargs + args.args]
    out += ["*" + args.vararg.arg] if args.vararg else []
    out += [a.arg for a in args.kwonlyargs]
    out += ["**" + args.kwarg.arg] if args.kwarg else []
    out += [f"={ast.unparse(d)}" for d in args.defaults]
    out += [f"={ast.unparse(d)}" for d in args.kw_defaults if d is not None]
    return out


def classify(finding: dict) -> str:
    """Why this finding is not a break, in one word."""
    reason, before, after = finding["reason"], finding["before"], finding["after"]
    if " :: " in before or before.startswith("class "):
        return "class_member_changed"
    if reason == "signature":
        if _bare(before) is not None and _bare(before) == _bare(after):
            return "annotation_only"
        if _accepts_everything(before, after):
            return "signature_widened"
        return "signature_changed"
    if reason == "removed":
        whys = " ".join(s["why"] for s in finding["sites"])
        if "tuple-unpacking" in whys or "TUPLE-UNPACKING" in whys:
            return "rebound_by_tuple_unpacking"
        return "moved_and_still_resolves"
    return reason or "other"


def taxonomy(findings: list[dict]) -> dict:
    out: dict[str, int] = {}
    for finding in findings:
        key = classify(finding)
        out[key] = out.get(key, 0) + 1
    return out


def site_rules(findings: list[dict]) -> dict:
    out: dict[str, int] = {}
    for finding in findings:
        for site in finding["sites"]:
            key = site["why"].split(":")[0] if site["source"] == "mechanical" else "hand-labelled"
            out[key] = out.get(key, 0) + 1
    return out


def _counts(findings: list[dict]) -> dict:
    counts = {"true": 0, "false": 0, "undecided": 0}
    for finding in findings:
        counts[finding["label"]] += 1
    return counts


def summarise(payload: dict) -> dict:
    findings = payload["checker"]["findings"]
    counts = {"true": 0, "false": 0, "undecided": 0}
    for finding in findings:
        counts[finding["label"]] += 1
    decided = counts["true"] + counts["false"]
    sites = [s for f in findings for s in f["sites"]]
    site_counts = {"true": 0, "false": 0, "undecided": 0}
    for site in sites:
        site_counts[site["label"]] += 1

    # B1 at a matched flag rate: raise the "unhandled mentions" threshold until it flags no
    # more commits than the checker did.
    rows = payload["grep"]["rows"]
    sweep = []
    for threshold in range(1, 201):
        flagged = [r for r in rows if any(n >= threshold for n in r["hits"].values())]
        sweep.append({"threshold": threshold, "flagged": len(flagged),
                      "overlap": sum(1 for r in flagged if r["checker_flagged"])})
    target = payload["checker"]["flagged_commits"]
    matched = next((s for s in sweep if s["flagged"] <= target), sweep[-1])
    b0 = sum(1 for r in rows if r["contract_changes"] > 0)
    return {
        "findings": len(findings),
        "labels": counts,
        "sites": site_counts,
        "precision": round(counts["true"] / decided, 4) if decided else None,
        "decided": decided,
        "grep_matched": matched,
        "grep_sweep_head": sweep[:6],
        "b0_flagged_commits": b0,
    }


def report(payload: dict) -> None:
    s = payload["summary"]
    print(f"base {payload['base']}  commits {payload['commits']}  "
          f"checker flagged {payload['checker']['flagged_commits']}")
    print(f"findings {s['findings']}  labels {s['labels']}  sites {s['sites']}")
    print(f"P1 precision: {s['precision']} over {s['decided']} decided finding(s)")
    print(f"B1 matched: {s['grep_matched']}")
    print(f"why false: {s.get('why_false')}")
    print(f"site rules: {s.get('site_rules')}")
    print(f"P2 grep: labels {s.get('grep_labels')}  precision {s.get('grep_precision')}")
    print(f"B0 (any contract change) flags {s['b0_flagged_commits']} commits")


if __name__ == "__main__":
    raise SystemExit(main())
