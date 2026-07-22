#!/usr/bin/env python3
"""Render registers.json into design-doc.pdf — a clean, linear design doc.

Usage: python3 build-pdf.py
Rerun after editing registers.json so the PDF the doc's "PDF" button opens
stays current. Needs Chrome or Chromium for the print step; set
CHROME=/path/to/chrome if discovery misses yours.
"""
import json, os, re, html, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).parent
R = json.load(open(HERE / "registers.json"))
META = R.get("meta", {})
TITLE = META.get("title", "Untitled design")
SUBTITLE = META.get("subtitle", "Design proposal")


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


def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\[\^(\d+)\]", r'<sup><a href="#fn-\1">\1</a></sup>', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", s)
    return s


def fmt(ms: float) -> str:
    if ms >= 1000:
        return f"{ms/1000:.0f}s" if ms >= 10000 else f"{ms/1000:.1f}s"
    if ms >= 1:
        return f"{ms:g}ms"
    return f"{ms*1000:g}µs"


svg = re.search(r"<!--SYSD-->(.*?)<!--/SYSD-->", (HERE / "design-doc.html").read_text(), re.S)
svg = svg.group(1) if svg else ""

out = []
w = out.append
w(f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(TITLE)} — {html.escape(SUBTITLE)}</title><style>
:root{{--bg:#fff;--bg2:#F1EFE9;--ink:#1E2227;--ink2:#5A6068;--line:#DDD9CF;--accent:#0B7568;--warn:#A66308;--crit:#B3362B;--ok:#2E7D46;--card:#fff;
--mono:ui-monospace,"SF Mono",Menlo,monospace;--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
@page{{size:letter;margin:22mm 19mm}}
body{{font:10.5pt/1.55 var(--sans);color:var(--ink);margin:0}}
h1{{font-size:24pt;letter-spacing:-.02em;margin:0 0 2pt}}
h2{{font-size:15pt;letter-spacing:-.01em;margin:22pt 0 6pt;padding-top:8pt;border-top:1px solid var(--line)}}
h3{{font-size:11.5pt;margin:12pt 0 3pt}}
p{{margin:5pt 0}}
.date{{font-family:var(--mono);font-size:8.5pt;color:var(--ink2);margin:0 0 14pt}}
code{{font-family:var(--mono);font-size:.88em;background:var(--bg2);border-radius:3px;padding:0 3px}}
a{{color:var(--accent);text-decoration:none}}
sup{{font-size:7pt}}
ul{{margin:5pt 0;padding-left:16pt}}
li{{margin:3pt 0}}
table{{border-collapse:collapse;width:100%;font-size:8.8pt;margin:6pt 0}}
th{{font-family:var(--mono);font-size:7.5pt;text-transform:uppercase;letter-spacing:.08em;color:var(--ink2);text-align:left;padding:4pt 7pt;border-bottom:1px solid var(--ink2)}}
td{{padding:3.5pt 7pt;border-bottom:.5pt solid var(--line);vertical-align:top}}
.num{{font-family:var(--mono);white-space:nowrap}}
.meta{{font-size:8.5pt;color:var(--ink2);font-family:var(--mono);margin:2pt 0 10pt}}
.status{{font-family:var(--mono);font-size:8pt;padding:1pt 6pt;border-radius:8pt;background:var(--bg2);color:var(--ink2)}}
.entry{{margin:0 0 9pt;page-break-inside:avoid}}
.entry .hd{{font-weight:600}}
.entry .id{{font-family:var(--mono);color:var(--accent)}}
.entry p{{margin:2pt 0;font-size:9.5pt}}
.entry .rej{{color:var(--ink2)}}
.banner{{border:1px solid var(--warn);border-radius:6pt;padding:7pt 10pt;font-size:9.5pt;margin:10pt 0;page-break-inside:avoid}}
.fnote{{font-size:9pt;margin:0 0 7pt;display:flex;gap:8pt}}
.fnote .n{{font-family:var(--mono);color:var(--accent);flex:0 0 14pt;text-align:right}}
.diagram{{margin:12pt 0;page-break-inside:avoid}}
.diagram svg{{width:100%;height:auto}}
.diagram .grp{{fill:var(--bg2);stroke:var(--line);stroke-width:1.2}}
.diagram .bx{{fill:#fff;stroke:var(--line);stroke-width:1.2}}
.diagram .bxo{{fill:var(--bg2);stroke:var(--line);stroke-width:1.2}}
.diagram .dur{{stroke:var(--accent);stroke-width:1.4}}
.diagram text{{font-family:var(--mono);font-size:11px;fill:var(--ink2)}}
.diagram .tt{{font-size:12px;font-weight:600;fill:var(--ink)}}
.diagram .ac{{fill:var(--accent)}}
.diagram .sm{{font-size:10.5px}}
.diagram .tag{{font-style:italic;font-size:10.5px}}
.diagram .ln{{stroke:var(--ink2);stroke-width:1.3;fill:none}}
.diagram .ln.acc{{stroke:var(--accent)}}
.diagram .ln.dash{{stroke-dasharray:5 4}}
.capn{{font-size:8.5pt;color:var(--ink2);margin-top:3pt}}
.pathnote{{font-size:9pt;color:var(--ink2);margin:2pt 0 10pt}}
section{{page-break-inside:auto}}
.avoid{{page-break-inside:avoid}}
</style></head><body>""")

w(f"<h1>{html.escape(TITLE)}</h1>")
dateline = " · ".join(str(x) for x in (SUBTITLE, META.get("phase"), META.get("date"), META.get("footerNote")) if x)
w(f'<p class="date">{html.escape(dateline)}</p>')

if R.get("tldr"):
    w("<h2>TL;DR</h2><ul>")
    for t in R["tldr"]:
        w(f"<li>{inline(t)}</li>")
    w("</ul>")
if META.get("tagline"):
    w(f"<p style='color:var(--ink2)'>{inline(META['tagline'])}</p>")

if svg.strip():
    w('<div class="diagram avoid"><h3>The system</h3>' + svg)
    if META.get("diagramCaption"):
        w(f'<div class="capn">{inline(META["diagramCaption"])}</div>')
    w("</div>")

banner = META.get("banner") or {}
if banner.get("text"):
    chip = f"<b>{html.escape(banner['assumption'])} ⚠</b> " if banner.get("assumption") else "<b>⚠</b> "
    w(f'<div class="banner">{chip}{inline(banner["text"])}</div>')

if R.get("constraints") or R.get("terms"):
    w("<h2>Ground rules</h2>")
    if R.get("constraints"):
        w("<ul>")
        for c in R["constraints"]:
            w(f"<li>{inline(c['t'])} <span class='num' style='color:var(--ink2)'>({c['a']})</span></li>")
        w("</ul>")
    if R.get("terms"):
        w("<h3>Terms</h3><ul>")
        for t in R["terms"]:
            w(f"<li><b>{html.escape(t['k'])}</b> — {inline(t['v'])}</li>")
        w("</ul>")

if R.get("arch"):
    w("<h2>Architecture</h2>")
    if R.get("pipe"):
        line = " → ".join(f"{n['t']} ({n['chip']})" for n in R["pipe"])
        if R.get("pipeBg"):
            line += " &nbsp;·&nbsp; background: " + " → ".join(f"{b['t']} ({b['chip']})" for b in R["pipeBg"])
        w(f'<p class="meta">{line}</p>')
    for c in R["arch"]:
        w(f'<div class="avoid"><h3>{html.escape(c["t"])}</h3>')
        for para in c["b"]:
            w(f"<p>{inline(para)}</p>")
        w(f'<p class="meta">decisions {", ".join(c["dq"])} · assumptions {", ".join(c["a"])}</p></div>')

if R.get("paths"):
    w("<h2>Request paths (E)</h2>")
    for p in R["paths"]:
        t50 = sum(g[1] for g in p["segs"]); t95 = sum(g[2] for g in p["segs"])
        budget = f" · budget {p['budget']}ms" if p.get("budget") else ""
        w(f'<div class="avoid"><h3>{html.escape(p["name"])} <span class="num" style="font-weight:400;color:var(--ink2)">p50 {fmt(t50)} · p95 {fmt(t95)}{budget}</span></h3>')
        w("<table><tr><th>Step</th><th>p50</th><th>p95</th><th>What happens</th></tr>")
        for g in p["segs"]:
            w(f"<tr><td>{html.escape(g[0])}</td><td class='num'>{fmt(g[1])}</td><td class='num'>{fmt(g[2])}</td><td>{html.escape(g[3] if len(g)>3 else '')}</td></tr>")
        w(f"</table><p class='pathnote'>{html.escape(p['note'])}</p></div>")

if R.get("ceilings"):
    w("<h2>Load ceilings (E)</h2>")
    w("<table><tr><th>Resource</th><th>Ceiling (E)</th><th>First symptom</th><th>Guard</th></tr>")
    for r_, c_, s_, g_ in R["ceilings"]:
        w(f"<tr><td>{html.escape(r_)}</td><td>{html.escape(c_)}</td><td>{html.escape(s_)}</td><td>{html.escape(g_)}</td></tr>")
    w("</table>")
    if R.get("ceilingsNote"):
        w(f"<p class='pathnote'>{html.escape(R['ceilingsNote'])}</p>")

if R.get("decisions"):
    w("<h2>Decisions</h2>")
    for d in R["decisions"]:
        status = d["s"] + (f" → {d['by']}" if d.get("by") else "")
        w(f'<div class="entry"><div class="hd"><span class="id">{d["id"]}</span> {html.escape(d["t"])} <span class="status">{status}</span></div>')
        w(f"<p>{inline(d['r'])}</p>")
        if d.get("x"):
            w(f"<p class='rej'>{inline(d['x'])}</p>")
        w("</div>")

if R.get("assumptions"):
    w("<h2>Assumptions</h2>")
    for a in R["assumptions"]:
        status = "needs validation" if a["s"] == "validate" else "working"
        star = " ★" if a.get("star") else ""
        w(f'<div class="entry"><div class="hd"><span class="id">{a["id"]}{star}</span> {html.escape(a["t"])} <span class="status">{status}</span></div>')
        w(f"<p>{inline(a['b'])}</p>")
        if a.get("n"):
            w(f"<p class='rej'>{inline(a['n'])}</p>")
        w("</div>")

if R.get("open"):
    w("<h2>Open items</h2>")
    for g, label in R.get("openGroups", {}).items():
        items = [o for o in R["open"] if o["g"] == g]
        if not items:
            continue
        w(f'<div class="avoid"><h3>{html.escape(label)}</h3><ul>')
        for o in items:
            w(f"<li><span class='num' style='color:var(--accent)'>{o['id']}</span> {inline(o['t'])}</li>")
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

tmp = Path(tempfile.mkstemp(suffix=".html")[1])
tmp.write_text("\n".join(out))
pdf = HERE / "design-doc.pdf"
res = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                      f"--print-to-pdf={pdf}", tmp.as_uri()], capture_output=True, text=True)
tmp.unlink()
if pdf.exists():
    print(f"wrote {pdf} ({pdf.stat().st_size//1024} KB)")
else:
    print(res.stderr, file=sys.stderr)
    sys.exit(1)
