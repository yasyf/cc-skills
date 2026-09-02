#!/usr/bin/env python3
"""Render a design project's registers.json into its design-doc.pdf.

Usage: python3 build-pdf.py [target]
`target` is the design project directory (default: the current one) or a
path to its registers.json. The executive summary (summary.html) and the
system diagram (sysd.svg) are read from the same directory when they exist,
and the PDF lands there too, so the doc's "PDF" button serves it. Rerun
after editing the registers. Needs Chrome or Chromium for the print step;
set CHROME=/path/to/chrome if discovery misses yours.
"""
import argparse, datetime, json, os, re, html, shutil, subprocess, sys
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("target", nargs="?", default=".", help="design project directory, or its registers.json")
target = Path(ap.parse_args().target)
if target.suffix == ".json":
    HERE, registers = target.parent, target
else:
    HERE, registers = target, target / "registers.json"
if not registers.exists():
    print(f"build-pdf.py: {registers} not found.", file=sys.stderr)
    sys.exit(1)

R = json.loads(registers.read_text())
META = R.get("meta", {})
TITLE = META.get("title", "Untitled design")
SUBTITLE = META.get("subtitle", "Design proposal")
DRAFT_NOTE = "This document is in progress and will change. It is not ready for review."
DECISION_LABEL = {"resolved": "Decided", "superseded": "Replaced", "open": "Still open"}
ASSUMPTION_LABEL = {"working": "Assumed", "validate": "Needs someone to confirm"}

TITLES = {}
for collection, title_key in (("decisions", "t"), ("assumptions", "t"), ("arch", "t"),
                              ("numbers", "t"), ("paths", "name"), ("open", "t")):
    for e in R.get(collection) or []:
        if e.get("id"):
            TITLES.setdefault(e["id"], e.get(title_key) or e["id"])

ID_TOKEN = re.compile(r"\b(DQ\d+|A\d+|Q\d+|V\d+|c-[a-z0-9-]+)\b")
TAG_NAME = re.compile(r"</?\s*([a-zA-Z0-9]+)")


def find_chrome():
    if os.environ.get("CHROME"):
        c = os.environ["CHROME"]
        if Path(c).exists() or shutil.which(c):
            return c
        print(f"build-pdf.py: CHROME={c} does not exist.", file=sys.stderr)
        sys.exit(2)
    for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium"):
        if Path(c).exists():
            return c
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    return None


def ref(i: str) -> str:
    title = TITLES.get(i)
    if not title:
        return f'<span class="id">{html.escape(str(i))}</span>'
    return f'<a href="#reg-{i}">{html.escape(title)} <span class="id">{i}</span></a>'


def link_ids(s: str) -> str:
    out, inside = [], 0
    for tok in re.split(r"(<[^>]+>)", s):
        if tok.startswith("<"):
            m = TAG_NAME.match(tok)
            name = m.group(1).lower() if m else ""
            if name in ("a", "code"):
                inside = max(0, inside - 1) if tok.startswith("</") else inside + 1
        elif not inside:
            tok = ID_TOKEN.sub(lambda m: f'<a href="#reg-{m.group(1)}">{m.group(1)}</a>'
                               if m.group(1) in TITLES else m.group(1), tok)
        out.append(tok)
    return "".join(out)


def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\[\^(\d+)\]", r'<sup><a href="#fn-\1">\1</a></sup>', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", s)
    return link_ids(s)


def fmt(ms: float) -> str:
    if ms >= 1000:
        return f"{ms/1000:.0f}s" if ms >= 10000 else f"{ms/1000:.1f}s"
    if ms >= 1:
        return f"{ms:g}ms"
    return f"{ms*1000:g}µs"


def spell_date(s) -> str:
    try:
        return datetime.date.fromisoformat(str(s)).strftime("%-d %B %Y")
    except ValueError:
        return str(s)


def read(name: str) -> str:
    p = HERE / name
    return p.read_text() if p.exists() else ""


svg = read("sysd.svg")
summary = read("summary.html")

out = []
w = out.append
w(f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(TITLE)} — {html.escape(SUBTITLE)}</title><style>
:root{{--bg:#fff;--bg2:#F1EFE9;--ink:#1E2227;--ink2:#5A6068;--line:#DDD9CF;--accent:#0B7568;--accent-soft:#0B75681A;--warn:#A66308;--crit:#B3362B;--ok:#2E7D46;--dead:#7A828C;--dead-soft:#7A828C1A;--card:#fff;
--mono:ui-monospace,"SF Mono",Menlo,monospace;--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
@page{{size:letter;margin:22mm 19mm}}
body{{font:10.5pt/1.55 var(--sans);color:var(--ink);margin:0}}
h1{{font-size:24pt;letter-spacing:-.02em;margin:0 0 2pt}}
h2{{font-size:15pt;letter-spacing:-.01em;margin:22pt 0 6pt;padding-top:8pt;border-top:1px solid var(--line)}}
h3{{font-size:11.5pt;margin:12pt 0 3pt}}
p{{margin:5pt 0}}
.date{{font-size:9.5pt;color:var(--ink2);margin:0 0 3pt}}
.datenote{{font-size:9pt;color:var(--ink2);margin:0 0 4pt;max-width:72ch}}
.datenote:last-of-type{{margin-bottom:14pt}}
.draftpill{{border:.5pt solid var(--dead);border-radius:8pt;padding:0 5pt;color:var(--dead);font-size:8.5pt}}
code{{font-family:var(--mono);font-size:.88em;background:var(--bg2);border-radius:3px;padding:0 3px}}
a{{color:var(--accent);text-decoration:none}}
a .id{{color:var(--ink2)}}
sup{{font-size:7pt}}
ul{{margin:5pt 0;padding-left:16pt}}
li{{margin:3pt 0}}
table{{border-collapse:collapse;width:100%;font-size:8.8pt;margin:6pt 0}}
th{{font-size:8.8pt;font-weight:600;color:var(--ink2);text-align:left;padding:4pt 7pt;border-bottom:1px solid var(--ink2)}}
td{{padding:3.5pt 7pt;border-bottom:.5pt solid var(--line);vertical-align:top}}
.num{{font-family:var(--mono);white-space:nowrap}}
.id{{font-family:var(--mono);font-size:8.5pt;color:var(--ink2)}}
.meta{{font-size:9pt;color:var(--ink2);margin:2pt 0 10pt}}
.legend{{font-size:9pt;color:var(--ink2);margin:2pt 0 8pt}}
.rests{{font-size:8.5pt;color:var(--ink2)}}
.status{{font-size:8.5pt;padding:1pt 6pt;border-radius:8pt;background:var(--bg2);color:var(--ink2)}}
.entry{{margin:0 0 9pt;page-break-inside:avoid}}
.entry .hd{{font-weight:600}}
.entry p{{margin:2pt 0;font-size:9.5pt}}
.entry .rej{{color:var(--ink2)}}
.star{{color:var(--accent)}}
.banner{{border:1px solid var(--warn);border-radius:6pt;padding:7pt 10pt;font-size:9.5pt;margin:10pt 0;page-break-inside:avoid}}
.fnote{{font-size:9pt;margin:0 0 7pt;display:flex;gap:8pt}}
.fnote .n{{font-family:var(--mono);color:var(--accent);flex:0 0 14pt;text-align:right}}
.diagram{{margin:12pt 0;page-break-inside:avoid}}
.sysd svg{{display:block;width:100%;height:auto}}
.sysd .grp{{fill:var(--bg2);stroke:var(--line);stroke-width:1.2}}
.sysd .bx{{fill:#fff;stroke:var(--line);stroke-width:1.2}}
.sysd .bxo{{fill:var(--bg2);stroke:var(--line);stroke-width:1.2}}
.sysd .dur{{stroke:var(--accent);stroke-width:1.4}}
.sysd text{{font-family:var(--mono);font-size:11px;fill:var(--ink2)}}
.sysd .tt{{font-size:12px;font-weight:600;fill:var(--ink)}}
.sysd .ac{{fill:var(--accent)}}
.sysd .sm{{font-size:10.5px}}
.sysd .tag{{font-style:italic;font-size:10.5px}}
.sysd .ln{{stroke:var(--ink2);stroke-width:1.3;fill:none}}
.sysd .ln.acc{{stroke:var(--accent)}}
.sysd .ln.dash{{stroke-dasharray:5 4}}
.xs-lede{{font-size:12pt;line-height:1.5;margin:6pt 0 10pt}}
.xs-tiles{{display:flex;gap:10pt;margin:10pt 0;page-break-inside:avoid}}
.xs-tile{{flex:1;border:.5pt solid var(--line);border-radius:6pt;padding:8pt 10pt}}
.xs-tile b{{display:block;font-size:9pt;color:var(--ink2);margin-bottom:3pt}}
.xs-tile .xs-before{{display:block;font-family:var(--mono);font-size:9.5pt;color:var(--ink2)}}
.xs-tile .xs-after,.xs-tile .xs-value{{display:block;font-family:var(--mono);font-size:13pt;color:var(--accent)}}
.xs-tile .xs-after::before{{content:"→ ";color:var(--ink2)}}
.xs-tile small{{display:block;margin-top:3pt;font-size:8.5pt;color:var(--ink2)}}
.xs-figure{{margin:10pt 0;page-break-inside:avoid}}
.xs-figure figcaption{{font-size:8.5pt;color:var(--ink2);margin-top:3pt}}
.xs-cols{{display:flex;gap:12pt;margin:10pt 0;page-break-inside:avoid}}
.xs-cols>*{{flex:1;min-width:0;margin:0}}
.xs-compare{{margin:10pt 0}}
.xs-row{{display:flex;flex-wrap:wrap;gap:8pt;padding:5pt 0;border-bottom:.5pt solid var(--line);font-size:9.5pt;page-break-inside:avoid}}
.xs-row .xs-topic{{flex:0 0 84pt;font-weight:600}}
.xs-row .xs-before{{flex:1;color:var(--ink2)}}
.xs-row .xs-after{{flex:1}}
.xs-row .xs-why{{flex:0 0 100%;padding-left:92pt;font-size:8.5pt;color:var(--ink2)}}
.capn{{font-size:8.5pt;color:var(--ink2);margin-top:3pt}}
.pathnote{{font-size:9pt;color:var(--ink2);margin:2pt 0 10pt}}
section{{page-break-inside:auto}}
.avoid{{page-break-inside:avoid}}
</style></head><body>""")

w(f"<h1>{html.escape(TITLE)}</h1>")
dateline = [html.escape(SUBTITLE)]
if META.get("phase") and not META.get("draft"):
    dateline.append(html.escape(str(META["phase"])))
if META.get("rev"):
    dateline.append(f'revision {META["rev"]}')
if META.get("date"):
    dateline.append(spell_date(META["date"]))
pill = ' <span class="draftpill">draft</span>' if META.get("draft") else ""
w(f'<p class="date">{", ".join(dateline)}{pill}</p>')
for note in ([META.get("draftNote") or DRAFT_NOTE] if META.get("draft") else []) + [META.get("footerNote")]:
    if note:
        w(f'<p class="datenote">{inline(note)}</p>')

if summary.strip():
    w('<section class="summary sysd"><h2>Executive summary</h2>')
    w(summary)
    w("</section>")

if R.get("tldr"):
    w("<h2>In short</h2><ul>")
    for t in R["tldr"]:
        w(f"<li>{inline(t)}</li>")
    w("</ul>")

if svg.strip():
    w('<div class="diagram sysd avoid"><h3>The system</h3>' + svg)
    if META.get("diagramCaption"):
        w(f'<div class="capn">{inline(META["diagramCaption"])}</div>')
    w("</div>")

banner = META.get("banner") or {}
if banner.get("text"):
    lead = f"<b>⚠</b> {ref(banner['assumption'])} — " if banner.get("assumption") else "<b>⚠</b> "
    w(f'<div class="banner">{lead}{inline(banner["text"])}</div>')

if R.get("constraints") or R.get("terms"):
    w("<h2>Ground rules</h2>")
    if R.get("constraints"):
        w("<ul>")
        for c in R["constraints"]:
            rests = ", ".join(ref(x) for x in str(c.get("a", "")).split())
            w(f"<li>{inline(c['t'])} <span class='rests'>rests on {rests}</span></li>")
        w("</ul>")
    if R.get("terms"):
        w("<h3>Terms</h3><ul>")
        for t in R["terms"]:
            w(f"<li><b>{html.escape(t['k'])}</b> — {inline(t['v'])}</li>")
        w("</ul>")

if R.get("arch"):
    w("<h2>How it works</h2>")
    if R.get("pipe"):
        line = " → ".join(f"{n['t']} ({n['chip']})" for n in R["pipe"])
        if R.get("pipeBg"):
            line += "; in the background: " + " → ".join(f"{b['t']} ({b['chip']})" for b in R["pipeBg"])
        w(f'<p class="meta">{html.escape(line)}</p>')
    for c in R["arch"]:
        w(f'<div class="avoid" id="reg-{c["id"]}"><h3>{html.escape(c["t"])}</h3>')
        for para in c["b"]:
            w(f"<p>{inline(para)}</p>")
        meta_bits = []
        if c.get("dq"):
            meta_bits.append("What we decided: " + ", ".join(ref(x) for x in c["dq"]))
        if c.get("a"):
            meta_bits.append("This rests on: " + ", ".join(ref(x) for x in c["a"]))
        w(f'<p class="meta">{". ".join(meta_bits)}.</p></div>' if meta_bits else "</div>")

if R.get("paths"):
    w("<h2>Where the time goes</h2>")
    w('<p class="legend">p50 is the typical request and p95 the slow one. Numbers marked (E) are '
      "estimates until the spike behind them runs.</p>")
    for p in R["paths"]:
        t50 = sum(g[1] for g in p["segs"]); t95 = sum(g[2] for g in p["segs"])
        budget = f" · budget {p['budget']}ms" if p.get("budget") else ""
        w(f'<div class="avoid" id="reg-{p["id"]}"><h3>{html.escape(p["name"])} <span class="num" style="font-weight:400;color:var(--ink2)">p50 {fmt(t50)} · p95 {fmt(t95)}{budget}</span></h3>')
        w("<table><tr><th>Step</th><th>p50</th><th>p95</th><th>What happens</th></tr>")
        for g in p["segs"]:
            w(f"<tr><td>{html.escape(g[0])}</td><td class='num'>{fmt(g[1])}</td><td class='num'>{fmt(g[2])}</td><td>{html.escape(g[3] if len(g)>3 else '')}</td></tr>")
        w(f"</table><p class='pathnote'>{inline(p['note'])}</p></div>")

if R.get("numbers"):
    w("<h2>Numbers</h2>")
    for nt in R["numbers"]:
        w(f'<div class="avoid" id="reg-{nt["id"]}"><h3>{inline(nt["t"])}</h3>')
        if nt.get("sub"):
            w(f"<p class='pathnote'>{inline(nt['sub'])}</p>")
        w("<table><tr>" + "".join(f"<th>{inline(c)}</th>" for c in nt["cols"]) + "</tr>")
        for row in nt["rows"]:
            w("<tr>" + "".join(f"<td>{'<b>' + inline(c) + '</b>' if i == 0 else inline(c)}</td>" for i, c in enumerate(row)) + "</tr>")
        w("</table>")
        if nt.get("note"):
            w(f"<p class='pathnote'>{inline(nt['note'])}</p>")
        w("</div>")

if R.get("ceilings"):
    w("<h2>Where it breaks</h2>")
    w("<table><tr><th>Resource</th><th>Ceiling (estimated)</th><th>What you'd see first</th><th>What stops it</th></tr>")
    for r_, c_, s_, g_ in R["ceilings"]:
        w(f"<tr><td>{html.escape(r_)}</td><td>{html.escape(c_)}</td><td>{html.escape(s_)}</td><td>{html.escape(g_)}</td></tr>")
    w("</table>")
    if R.get("ceilingsNote"):
        w(f"<p class='pathnote'>{inline(R['ceilingsNote'])}</p>")

if R.get("decisions"):
    w("<h2>Decisions</h2>")
    for d in R["decisions"]:
        status = DECISION_LABEL.get(d["s"], d["s"])
        w(f'<div class="entry" id="reg-{d["id"]}"><div class="hd">{html.escape(d["t"])} '
          f'<span class="status">{status}</span> <span class="id">{d["id"]}</span></div>')
        if d.get("by"):
            w(f'<p class="rej">Replaced by {ref(d["by"])}.</p>')
        w(f"<p>{inline(d['r'])}</p>")
        if d.get("x"):
            w(f"<p class='rej'>{inline(d['x'])}</p>")
        w("</div>")

if R.get("assumptions"):
    w("<h2>Assumptions</h2>")
    for a in R["assumptions"]:
        status = ASSUMPTION_LABEL.get(a["s"], a["s"])
        star = ' <span class="star">★</span>' if a.get("star") else ""
        w(f'<div class="entry" id="reg-{a["id"]}"><div class="hd">{html.escape(a["t"])}{star} '
          f'<span class="status">{status}</span> <span class="id">{a["id"]}</span></div>')
        w(f"<p>{inline(a['b'])}</p>")
        if a.get("n"):
            w(f"<p class='rej'>{inline(a['n'])}</p>")
        w("</div>")

if R.get("open"):
    w("<h2>Still open</h2>")
    for g, label in R.get("openGroups", {}).items():
        items = [o for o in R["open"] if o["g"] == g]
        if not items:
            continue
        w(f'<div class="avoid"><h3>{html.escape(label)}</h3><ul>')
        for o in items:
            w(f'<li id="reg-{o["id"]}">{inline(o["t"])} <span class="id">{o["id"]}</span></li>')
        w("</ul></div>")

if R.get("footnotes"):
    w("<h2>Footnotes</h2>")
    for f in R["footnotes"]:
        w(f'<div class="fnote" id="fn-{f["n"]}"><span class="n">{f["n"]}</span><span>{inline(f["b"])}</span></div>')

w("</body></html>")

chrome = find_chrome()
if not chrome:
    print("build-pdf.py: no Chrome or Chromium found. Install Google Chrome, or set "
          "CHROME=/path/to/chrome and rerun.", file=sys.stderr)
    sys.exit(2)

tmp = HERE.resolve() / f".design-doc-pdf.{os.getpid()}.html"
tmp.write_text("\n".join(out))
pdf = HERE / "design-doc.pdf"
try:
    res = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                          f"--print-to-pdf={pdf}", tmp.as_uri()], capture_output=True, text=True)
finally:
    tmp.unlink(missing_ok=True)
if pdf.exists():
    print(f"wrote {pdf} ({pdf.stat().st_size//1024} KB)")
else:
    print(res.stderr, file=sys.stderr)
    sys.exit(1)
