import datetime as dt
import html
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from drive import install, state

PACIFIC = ZoneInfo("America/Los_Angeles")
FILES = {"brief": "Brief", "report": "Report", "question": "Question", "repro": "Reproduction", "transcript": "Transcript"}
PROSE = {".md", ".txt"}
WHO = {"user": "Coordinator", "assistant": "Claude", "tool": "Tool"}
TURNS = ("user", "assistant")
SOURCE_CONTEXT = 3
PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark"><title>{title}</title><style>
html, body {{ margin: 0; background: #11151b; color: #eceff3; font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
header {{ padding: 16px 18px 12px; border-bottom: 1px solid #1c222a; }}
h1 {{ margin: 0 0 4px; font-size: 16px; font-weight: 600; }}
header p {{ margin: 0; color: #9aa3b0; font-size: 12px; }}
pre, code {{ font: 12.5px/1.6 ui-monospace, "SF Mono", SFMono-Regular, Menlo, monospace; }}
.doc {{ max-width: 860px; padding: 14px 18px 28px; white-space: pre-wrap; overflow-wrap: anywhere; }}
.code {{ margin: 0; padding: 12px 0; overflow-x: auto; counter-reset: line; }}
.code code {{ display: block; width: max-content; min-width: 100%; }}
.code span {{ position: relative; display: block; min-height: 1.6em; padding: 0 18px 0 calc(4ch + 28px); white-space: pre; tab-size: 4; }}
.code span::before {{ counter-increment: line; content: counter(line); position: absolute; left: 0; width: calc(4ch + 14px); text-align: right; color: #6b7480; user-select: none; }}
.turn {{ padding: 12px 18px; border-bottom: 1px solid #1c222a; }}
.turn h2 {{ margin: 0 0 6px; font-size: 11px; font-weight: 650; letter-spacing: .6px; text-transform: uppercase; color: #9aa3b0; }}
.turn.user h2 {{ color: #8ab8ff; }}
.turn.tool h2 {{ color: #6b7480; }}
.turn h2 time {{ margin-left: 8px; font-weight: 400; letter-spacing: 0; text-transform: none; color: #6b7480; }}
.said {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
details {{ margin: 6px 0; border-left: 2px solid #343c47; padding-left: 10px; }}
summary {{ cursor: pointer; color: #9aa3b0; font-size: 12px; }}
details pre {{ margin: 6px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; color: #b6bcc6; }}
details .said {{ margin-top: 6px; color: #b6bcc6; }}
</style><header><h1>{title}</h1><p>{facts}</p></header><main>{body}</main></html>"""


def kept(job_id: str, job: dict, reports: set[str]) -> list[str]:
    """The files the drive keeps for a job, in reading order: its brief, its report, the questions it asked back, its reproduction, and its session's transcript when it ran on this Mac."""
    held = {
        "brief": job.get("brief"),
        "report": job.get("report") or job_id in reports,
        "question": any(question["file"] for question in job["questions"]),
        "repro": job.get("repro"),
        "transcript": job.get("where") != "remote" and job.get("agent") and job.get("session"),
    }
    return [kind for kind in FILES if held[kind]]


def page(name: str, job_id: str, job: dict, kind: str) -> str:
    """The reader page for one of a job's kept files: a transcript turn by turn, prose as text, anything else as code with line numbers."""
    title = f"{job_id} {FILES[kind].lower()}: {job['goal']}"
    if kind == "transcript":
        path = next((install.config_dir() / "projects").glob(f"*/{job['session']}/subagents/agent-{job['agent']}.jsonl"), None)
        if not path:
            return drawn(title, "", prose_body(f"This Mac no longer holds the transcript of agent {job['agent']}."))
        turns = turns_of(path.read_text(errors="replace"))
        return drawn(title, f"{len(turns)} {'turn' if len(turns) == 1 else 'turns'} · {path}", "".join(turn_html(entry) for entry in turns))
    if kind == "question":
        asked = [Path(question["file"]) for question in job["questions"] if question["file"]]
        held = "".join(prose_body(path.read_text(errors="replace") if path.is_file() else f"This Mac no longer holds {path}.") for path in asked)
        return drawn(title, f"{len(asked)} {'question' if len(asked) == 1 else 'questions'}, oldest first", held)
    path = state.drive_dir(name) / "reports" / f"{job_id}.md" if kind == "report" else Path(job[kind])
    if kind == "report" and not path.is_file():
        return drawn(title, "the lane's latest report", prose_body(job["report"]))
    if not path.is_file():
        return drawn(title, "", prose_body(f"This Mac no longer holds {path}."))
    text = path.read_text(errors="replace")
    lines = text.removesuffix("\n").count("\n") + 1
    return drawn(title, f"{lines} {'line' if lines == 1 else 'lines'} · {path}", prose_body(text) if path.suffix in PROSE else code_body(text))


def drawn(title: str, facts: str, body: str) -> str:
    return PAGE.format(title=html.escape(title), facts=html.escape(facts), body=body)


def prose_body(text: str) -> str:
    return f'<div class="doc">{html.escape(text.strip())}</div>'


def code_body(text: str) -> str:
    lines = text.removesuffix("\n").split("\n")
    return f'<pre class="code"><code>{"".join(f"<span>{html.escape(line)}</span>" for line in lines)}</code></pre>'


def folded(summary: str, inner: str) -> str:
    return f"<details><summary>{html.escape(summary)}</summary>{inner}</details>"


def tool_result_text(content: str | list[dict] | None) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(part["text"] if part.get("type") == "text" else f"[{part.get('type')}]" for part in content or [])


def block_html(block: dict) -> str:
    kind = block.get("type")
    if kind == "text":
        return f'<div class="said">{html.escape(block["text"])}</div>' if block["text"].strip() else ""
    if kind == "thinking":
        return folded("Thinking", f'<div class="said">{html.escape(block["thinking"])}</div>') if block.get("thinking") else ""
    if kind == "tool_use":
        return folded(block.get("name", "Tool"), f"<pre>{html.escape(json.dumps(block.get('input'), indent=2))}</pre>")
    if kind == "tool_result":
        return folded("Result", f"<pre>{html.escape(tool_result_text(block.get('content')))}</pre>")
    if kind in ("image", "redacted_thinking"):
        return folded(str(kind), "")
    return folded(str(kind), f"<pre>{html.escape(json.dumps(block, indent=2))}</pre>")


def turns_of(text: str) -> list[dict]:
    """A session's turns, each assistant message whose blocks Claude Code wrote as separate lines joined back into one."""
    turns: list[dict] = []
    for entry in map(json.loads, text.splitlines()):
        if entry.get("type") not in TURNS or entry.get("isMeta"):
            continue
        said = entry["message"].get("id")
        if said and turns and turns[-1]["message"].get("id") == said:
            turns[-1]["message"]["content"] = [*blocks_of(turns[-1]), *blocks_of(entry)]
            continue
        turns.append(entry)
    return turns


def blocks_of(entry: dict) -> list[dict]:
    content = entry["message"]["content"]
    return [{"type": "text", "text": content}] if isinstance(content, str) else content


def speaker(entry: dict, blocks: list[dict]) -> str:
    return "tool" if blocks and all(block.get("type") == "tool_result" for block in blocks) else entry["type"]


def turn_html(entry: dict) -> str:
    blocks = blocks_of(entry)
    said = "".join(block_html(block) for block in blocks)
    if not said:
        return ""
    stamp = entry.get("timestamp")
    when = f"<time>{dt.datetime.fromisoformat(stamp).astimezone(PACIFIC):%b %-d %-I:%M:%S %p}</time>" if stamp else ""
    who = speaker(entry, blocks)
    return f'<article class="turn {who}"><h2>{WHO[who]}{when}</h2>{said}</article>'


def source_window(lines: list[str], line: int, to: int) -> list[dict]:
    first, last = max(1, line - SOURCE_CONTEXT), min(len(lines), to + SOURCE_CONTEXT)
    return [{"n": n, "text": lines[n - 1]} for n in range(first, last + 1)]
