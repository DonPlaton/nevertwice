#!/usr/bin/env python3
"""Nevertwice - a serverless, single-file HTML dashboard for the memory store.

The deliberate non-answer to "where's the web UI?". A hosted dashboard (a `serve`
process, a localhost port, an account) would contradict the whole premise - no server,
no daemon, your data in plain files you own - and would duplicate Obsidian, which already
renders the vault. So this is the opposite: one command writes **one self-contained
`.html` file** (inline CSS, no JS framework, no external asset, no network) that you open
in a browser. It is a snapshot you can mail, commit, or read offline on any machine - the
file IS the UI, exactly as the notes ARE the database.

    python -m nevertwice.dashboard                      # → memory_dashboard.html (+ tries to open it)
    python -m nevertwice.dashboard --project myproj --days 30
    python -m nevertwice.dashboard --out ~/report.html --no-open

Pure frontmatter scan (reuses digest.compute_digest / compute_conflicts) - no embedder,
no LLM, no network. `nevertwice.api.dashboard()` returns the HTML string for embedding.
"""
import html
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_hook as m          # noqa: E402
import digest as _digest         # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Premium dark developer-tool aesthetic (design-studio: OLED slate + brand green, layered
# elevation, hairline borders, tabular numerals, CSS-only staggered entrance). All inline -
# the file must render with zero external requests and open straight off disk on any machine.
_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
 --bg:#080b14; --surface:#0f1524; --surface-2:#141c30; --raised:#182137;
 --line:rgba(148,163,184,.10); --line-2:rgba(148,163,184,.16);
 --fg:#e9eef8; --muted:#8b96ad; --faint:#5a6478;
 --accent:#34d399; --accent-2:#22c55e; --accent-dim:rgba(52,211,153,.13);
 --danger:#f87171; --amber:#fbbf24; --violet:#c084fc; --blue:#7ca9ff;
 --r:16px; --r-sm:11px; --r-xs:8px;
 --sh-sm:0 1px 2px rgba(2,6,16,.4);
 --sh-md:0 6px 24px -8px rgba(2,6,16,.7),0 2px 6px rgba(2,6,16,.4);
 --ease:cubic-bezier(.22,1,.36,1);
 --mono:ui-monospace,"SF Mono","Cascadia Code","JetBrains Mono",Menlo,Consolas,monospace;
 --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,"Helvetica Neue",Arial,sans-serif;
}
html{-webkit-text-size-adjust:100%}
body{
 background:var(--bg);color:var(--fg);font-family:var(--sans);
 font-size:16.5px;line-height:1.62;letter-spacing:-.006em;
 -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
 background-image:
  radial-gradient(900px 480px at 82% -8%,rgba(52,211,153,.10),transparent 60%),
  radial-gradient(760px 420px at 8% -12%,rgba(124,169,255,.07),transparent 55%);
 background-attachment:fixed;min-height:100vh;
}
.wrap{max-width:1120px;margin:0 auto;padding:60px 32px 100px}
a{color:var(--accent);text-decoration:none}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.02em}

/* entrance: staggered fade-up, pure CSS, killed under reduced-motion */
.rv{animation:fadeUp .6s var(--ease) both;animation-delay:calc(var(--i,0)*70ms)}
@keyframes fadeUp{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.rv{animation:none}}
/* never let the entrance reveal trap content invisible in a static capture: this file is meant
   to be mailed / committed / printed to PDF, so print contexts show everything immediately. */
@media print{.rv{animation:none}}

/* header */
header{display:flex;align-items:center;gap:16px;margin-bottom:8px}
.mark{width:52px;height:52px;border-radius:13px;flex:none;display:grid;place-items:center;
 background:linear-gradient(145deg,var(--accent),var(--accent-2));
 box-shadow:0 8px 22px -6px rgba(34,197,94,.5),inset 0 1px 0 rgba(255,255,255,.35)}
.mark svg{width:28px;height:28px;color:#06210f}
.brand{display:flex;flex-direction:column;gap:2px}
.brand h1{font-size:28px;font-weight:700;letter-spacing:-.02em;line-height:1}
.brand h1 b{background:linear-gradient(90deg,var(--accent),#7ef0c0);
 -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;font-weight:800}
.brand .sub{color:var(--muted);font-size:15px;letter-spacing:0}
.rule{height:1px;background:linear-gradient(90deg,var(--line-2),transparent);margin:22px 0 26px}

/* stat cards */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:16px}
.card{position:relative;background:linear-gradient(180deg,var(--surface-2),var(--surface));
 border:1px solid var(--line);border-radius:var(--r);padding:22px 22px 20px;overflow:hidden;
 box-shadow:var(--sh-sm);transition:transform .28s var(--ease),border-color .28s var(--ease),box-shadow .28s var(--ease)}
.card::before{content:"";position:absolute;inset:0 0 auto 0;height:1px;
 background:linear-gradient(90deg,transparent,var(--line-2),transparent)}
.card:hover{transform:translateY(-3px);border-color:var(--line-2);box-shadow:var(--sh-md)}
.card .l{color:var(--muted);font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.07em}
.card .n{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:44px;font-weight:650;
 line-height:1.02;margin-top:14px;letter-spacing:-.03em}
.card.hero .n{background:linear-gradient(120deg,var(--accent),#8ff0c6);
 -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.card .foot{color:var(--faint);font-size:13.5px;margin-top:7px}

/* section heads */
h2{display:flex;align-items:center;gap:10px;font-size:15px;font-weight:600;letter-spacing:.03em;
 text-transform:uppercase;color:var(--muted);margin:38px 0 14px}
h2 svg{width:18px;height:18px;color:var(--accent);opacity:.9}
h2 .c{margin-left:auto;font-family:var(--mono);font-size:13.5px;color:var(--faint);
 text-transform:none;letter-spacing:0;font-weight:400}

/* tables */
.panel{background:linear-gradient(180deg,var(--surface),rgba(15,21,36,.6));
 border:1px solid var(--line);border-radius:var(--r);overflow:hidden}
table{width:100%;border-collapse:collapse;font-size:15.5px}
th{text-align:left;color:var(--faint);font-weight:600;font-size:12.5px;text-transform:uppercase;
 letter-spacing:.05em;padding:14px 18px;border-bottom:1px solid var(--line)}
td{padding:14px 18px;border-bottom:1px solid var(--line);vertical-align:middle;color:var(--fg)}
tr:last-child td{border-bottom:0}
tbody tr{transition:background .18s var(--ease)}
tbody tr:hover td{background:rgba(148,163,184,.045)}
.proj{font-weight:600;letter-spacing:-.01em}
.count{font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--fg)}
.dim{color:var(--muted);font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:14px}

/* project bars */
.bar-cell{width:180px}
.bar-track{height:8px;border-radius:6px;background:rgba(148,163,184,.10);overflow:hidden}
.bar{height:100%;border-radius:6px;background:linear-gradient(90deg,var(--accent-2),var(--accent));
 box-shadow:0 0 12px -2px rgba(52,211,153,.5)}

/* type pills */
.pill{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--muted);
 font-family:var(--mono);margin-right:9px}
.pill i{width:6px;height:6px;border-radius:50%;display:inline-block}
.dot-mistake{background:var(--danger)} .dot-pattern{background:var(--accent)} .dot-decision{background:var(--violet)}
.t-mistake{color:var(--danger)} .t-pattern{color:var(--accent)} .t-decision{color:var(--violet)}

/* entity chips */
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{display:inline-flex;align-items:center;gap:7px;background:var(--surface-2);
 border:1px solid var(--line);border-radius:999px;padding:7px 15px;font-size:14px;
 transition:border-color .2s var(--ease),transform .2s var(--ease)}
.chip:hover{border-color:var(--accent-dim);transform:translateY(-1px)}
.chip b{color:var(--fg);font-weight:600} .chip span{color:var(--faint);font-family:var(--mono);
 font-variant-numeric:tabular-nums;font-size:13px}

/* ledger */
.arrow{color:var(--faint);margin:0 8px}
.was{color:var(--muted)} .now{color:var(--fg);font-weight:500}
.tag{display:inline-block;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;
 color:var(--amber);background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.22);
 border-radius:999px;padding:2px 9px}
.empty{color:var(--muted);padding:20px 18px;font-size:15.5px}

footer{margin-top:52px;padding-top:22px;border-top:1px solid var(--line);
 display:flex;align-items:center;gap:8px;color:var(--faint);font-size:14px}
footer .g{width:6px;height:6px;border-radius:50%;background:var(--accent);
 box-shadow:0 0 8px var(--accent)}
footer a{color:var(--muted)} footer a:hover{color:var(--accent)}
"""

# Inline feather-style icons (no xmlns - HTML5 inline SVG renders without it, and it keeps the
# file free of any "http" reference so it stays provably offline/self-contained).
_ICONS = {
    "mark": '<path d="M12 2 2 7l10 5 10-5-10-5Z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
    "folder": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2Z"/>',
    "hash": '<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "alert": '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/>'
             '<path d="M12 9v4M12 17h.01"/>',
}


def _svg(name: str, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return (f'<svg{c} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round">{_ICONS[name]}</svg>')


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _type_class(nt: str) -> str:
    return {"mistake": "t-mistake", "pattern": "t-pattern", "decision": "t-decision"}.get(nt, "")


def build_html(project=None, days=30, conflicts_limit=40) -> str:
    """Render the whole dashboard to one self-contained, premium HTML string."""
    d = _digest.compute_digest(project, days=days, top_entities=20, recent_n=20)
    conflicts = _digest.compute_conflicts(m.slug_project(project) if project else None,
                                          limit=conflicts_limit)
    t = d["totals"]
    scope = d["project"]
    ri = [0]                                              # stagger index for entrance reveals

    def rv():
        ri[0] += 1
        return f' class="rv" style="--i:{ri[0]}"'

    parts = [f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nevertwice - {_e(scope)}</title><style>{_CSS}</style></head>
<body><div class="wrap">
<header{rv()}><div class="mark">{_svg('mark')}</div>
<div class="brand"><h1><b>Nevertwice</b> memory</h1>
<span class="sub">{_e(scope)} · {_e(d['generated'])} · last {days} days</span></div></header>
<div class="rule"></div>"""]

    # ── stat cards ──
    cards = [(t["live_notes"], "live notes", "across the store", True),
             (t["projects"], "projects", "tracked", False),
             (t["superseded_notes"], "superseded", "history kept", False),
             (t["added_in_window"], f"added · {days}d", "new lessons", False),
             (t["revised_in_window"], f"revised · {days}d", "contradictions resolved", False)]
    parts.append(f'<div class="cards"{rv()}>')
    for n, lbl, foot, hero in cards:
        parts.append(f'<div class="card{" hero" if hero else ""}"><div class="l">{_e(lbl)}</div>'
                     f'<div class="n">{n}</div><div class="foot">{_e(foot)}</div></div>')
    parts.append("</div>")

    # ── per-project ──
    bp = d["by_project"]
    if bp:
        mx = max((v["total"] for v in bp.values()), default=1) or 1
        parts.append(f'<h2{rv()}>{_svg("folder")} By project <span class="c">{len(bp)}</span></h2>'
                     f'<div class="panel"{rv()}><table><thead><tr><th>project</th><th>notes</th>'
                     '<th></th><th>added</th><th>revised</th><th>types</th></tr></thead><tbody>')
        for p, v in sorted(bp.items(), key=lambda kv: -kv[1]["total"]):
            w = max(4, round(100 * v["total"] / mx))
            pills = " ".join(f'<span class="pill"><i class="dot-{_e(k)}"></i>{_e(k)} {c}</span>'
                             for k, c in sorted(v["by_type"].items()))
            parts.append(f'<tr><td class="proj">{_e(p)}</td><td class="count">{v["total"]}</td>'
                         f'<td class="bar-cell"><div class="bar-track"><div class="bar" '
                         f'style="width:{w}%"></div></div></td>'
                         f'<td class="dim">+{v["added"]}</td><td class="dim">{v["superseded"]}</td>'
                         f'<td>{pills}</td></tr>')
        parts.append("</tbody></table></div>")

    # ── top entities ──
    if d["top_entities"]:
        parts.append(f'<h2{rv()}>{_svg("hash")} Most-connected entities</h2>'
                     f'<div class="chips"{rv()}>')
        for e in d["top_entities"]:
            parts.append(f'<span class="chip"><b>{_e(e["entity"])}</b><span>{e["notes"]}</span></span>')
        parts.append("</div>")

    # ── recently added ──
    if d["recent"]:
        parts.append(f'<h2{rv()}>{_svg("activity")} Recently added '
                     f'<span class="c">{len(d["recent"])}</span></h2>'
                     f'<div class="panel"{rv()}><table><thead><tr><th>date</th><th>project</th>'
                     '<th>type</th><th>title</th></tr></thead><tbody>')
        for n in d["recent"]:
            parts.append(f'<tr><td class="dim">{_e(n["date"])}</td><td class="proj">{_e(n["project"])}</td>'
                         f'<td class="{_type_class(n["ntype"])}">{_e(n["ntype"])}</td>'
                         f'<td>{_e(n["title"])}</td></tr>')
        parts.append("</tbody></table></div>")

    # ── contradiction ledger ──
    parts.append(f'<h2{rv()}>{_svg("alert")} Contradiction ledger '
                 f'<span class="c">{len(conflicts)} revised · write-time supersession</span></h2>')
    if conflicts:
        parts.append(f'<div class="panel"{rv()}><table><thead><tr><th>date</th><th>project</th>'
                     '<th>was → now</th><th></th></tr></thead><tbody>')
        for c in conflicts:
            tag = '' if c["resolved"] else '<span class="tag">evolving</span>'
            now = (f'<span class="now">{_e(c["new_title"])}</span>' if c["new_stem"]
                   else '<span class="dim">(archived)</span>')
            parts.append(f'<tr><td class="dim">{_e(c["new_date"] or c["old_date"])}</td>'
                         f'<td class="proj">{_e(c["project"])}</td>'
                         f'<td><span class="was">{_e(c["old_title"])}</span>'
                         f'<span class="arrow">→</span>{now}</td><td>{tag}</td></tr>')
        parts.append("</tbody></table></div>")
    else:
        parts.append('<div class="panel"><div class="empty">Nothing superseded yet - '
                     'no contradictions on record.</div></div>')

    parts.append('<footer><span class="g"></span>Generated by Nevertwice · plain files, no server · '
                 '<a href="https://github.com/DonPlaton/nevertwice">github.com/DonPlaton/nevertwice</a>'
                 '</footer></div></body></html>')
    return "\n".join(parts)


def main():
    argv = sys.argv[1:]
    project = m.argval(argv, "project")
    days = int(m.argval(argv, "days", "30"))
    out = m.argval(argv, "out", "memory_dashboard.html")
    no_open = "--no-open" in argv
    htmls = build_html(project, days=days)
    p = Path(out).expanduser().resolve()
    p.write_text(htmls, encoding="utf-8")
    print(f"[dashboard] wrote {p}  ({len(htmls)//1024} KB, self-contained, no server)")
    if not no_open:
        try:
            webbrowser.open(p.as_uri())
        except Exception:
            pass


if __name__ == "__main__":
    main()
