import datetime as dt
import html
import json
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
WHO = {"user": "User", "assistant": "Claude", "tool": "Tool"}
TURNS = ("user", "assistant")
PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark"><title>{title}</title><style>
html, body {{ margin: 0; background: #0e0e0e; color: #d8d8de; font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
header {{ padding: 16px 18px 12px; border-bottom: 1px solid #242529; }}
h1 {{ margin: 0 0 4px; font-size: 16px; font-weight: 600; }}
header p {{ margin: 0; color: #8a8a94; font-size: 12px; }}
pre, code {{ font: 12.5px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace; }}
.code {{ margin: 0; padding: 12px 0; overflow-x: auto; counter-reset: line; }}
.code code {{ display: block; width: max-content; min-width: 100%; }}
.code span {{ position: relative; display: block; min-height: 1.6em; padding: 0 18px 0 calc(4ch + 28px); white-space: pre; tab-size: 4; }}
.code span::before {{ counter-increment: line; content: counter(line); position: absolute; left: 0; width: calc(4ch + 14px); text-align: right; color: #55555f; user-select: none; }}
.turn {{ padding: 12px 18px; border-bottom: 1px solid #1b1a20; }}
.turn h2 {{ margin: 0 0 6px; font-size: 11px; font-weight: 650; letter-spacing: .6px; text-transform: uppercase; color: #8a8a94; }}
.turn.user h2 {{ color: #7c9cff; }}
.turn.tool h2 {{ color: #66666f; }}
.turn h2 time {{ margin-left: 8px; font-weight: 400; letter-spacing: 0; text-transform: none; color: #66666f; }}
.said {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
details {{ margin: 6px 0; border-left: 2px solid #2b2b33; padding-left: 10px; }}
summary {{ cursor: pointer; color: #8a8a94; font-size: 12px; }}
details pre {{ margin: 6px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; color: #a8a8b2; }}
details .said {{ margin-top: 6px; color: #a8a8b2; }}
</style><header><h1>{title}</h1><p>{facts}</p></header><main>{body}</main></html>"""


def code_body(text):
    lines = text.removesuffix("\n").split("\n")
    return f'<pre class="code"><code>{"".join(f"<span>{html.escape(line)}</span>" for line in lines)}</code></pre>'


def folded(summary, inner):
    return f"<details><summary>{html.escape(summary)}</summary>{inner}</details>"


def tool_result_text(content):
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", json.dumps(part)) for part in content or [])


def block_html(block):
    kind = block.get("type")
    if kind == "text":
        return f'<div class="said">{html.escape(block["text"])}</div>'
    if kind == "thinking":
        return folded("Thinking", f'<div class="said">{html.escape(block["thinking"])}</div>') if block.get("thinking") else ""
    if kind == "tool_use":
        return folded(block.get("name", "Tool"), f"<pre>{html.escape(json.dumps(block.get('input'), indent=2))}</pre>")
    if kind == "tool_result":
        return folded("Result", f"<pre>{html.escape(tool_result_text(block.get('content')))}</pre>")
    return folded(str(kind), f"<pre>{html.escape(json.dumps(block, indent=2))}</pre>")


def speaker(event):
    blocks = event.get("blocks") or []
    return "tool" if blocks and all(block.get("type") == "tool_result" for block in blocks) else event["kind"]


def turn_html(event):
    blocks = "".join(block_html(block) for block in event.get("blocks") or [])
    said = blocks or (f'<div class="said">{html.escape(event["text"])}</div>' if event.get("text") else "")
    if not said:
        return ""
    stamp = (event.get("meta") or {}).get("timestamp")
    when = f"<time>{dt.datetime.fromisoformat(stamp).astimezone(PACIFIC):%b %-d %-I:%M:%S %p}</time>" if stamp else ""
    who = speaker(event)
    return f'<article class="turn {who}"><h2>{WHO[who]}{when}</h2>{said}</article>'


def transcript_body(text):
    events = (json.loads(line) for line in text.splitlines() if line.strip())
    return "".join(turn_html(event) for event in events if event.get("kind") in TURNS)


def page(artifact, text):
    body = code_body(text) if artifact["kind"] == "code" else transcript_body(text)
    facts = " · ".join(fact for fact in (artifact.get("language"), artifact.get("size")) if fact)
    return PAGE.format(title=html.escape(artifact["title"]), facts=html.escape(facts), body=body)
