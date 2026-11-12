import datetime as dt
import json
from collections.abc import Iterator
from pathlib import Path
from zoneinfo import ZoneInfo

from drive import install, state
from drive.card import without_records

PACIFIC = ZoneInfo("America/Los_Angeles")
FILES = {"brief": "Brief", "report": "Report", "question": "Question", "repro": "Reproduction", "transcript": "Transcript"}
PROSE = {".md", ".txt"}
WHO = {"user": "Coordinator", "assistant": "Claude", "tool": "Tool"}
TURNS = ("user", "assistant")
SOURCE_CONTEXT = 3
TAIL_BYTES = 256 * 1024


def kept(job_id: str, job: dict, reports: set[str]) -> list[str]:
    """The files the drive keeps for a job, in reading order: its brief, its report, the questions it asked back, its reproduction, and its session's transcript when it ran on this Mac, whose first turn is the brief of a job drive job brief wrote none for."""
    on_mac = job.get("where") != "remote" and job.get("agent") and job.get("session")
    held = {
        "brief": job.get("brief") or on_mac,
        "report": job.get("report") or job_id in reports,
        "question": any(question["file"] for question in job["questions"]),
        "repro": job.get("repro"),
        "transcript": on_mac,
    }
    return [kind for kind in FILES if held[kind]]


def newest_line(name: str, job_id: str, job: dict) -> dict | None:
    """A lane's newest progress line and where it came from: its transcript's newest words while it runs on this Mac, else the line it last said, else its report's first line. The newest line that still says something with its record ids taken out wins; when none does, the newest line as the lane wrote it."""
    lines = list(progress(name, job_id, job))
    return next((line | {"line": words} for line in lines if (words := " ".join(without_records(line["line"]).split()))), lines[0] if lines else None)


def progress(name: str, job_id: str, job: dict) -> Iterator[dict]:
    if job["state"] == "running" and (path := transcript(job)):
        yield from said_in(path)
    if job["said"]:
        yield {"line": job["said"], "from": "said", "at": job["updated"]}
    if report := state.report_text(name, job_id, job).strip():
        yield {"line": report.splitlines()[0], "from": "report", "at": job["updated"]}


def said_in(path: Path) -> Iterator[dict]:
    with path.open("rb") as handle:
        start = max(0, handle.seek(0, 2) - TAIL_BYTES)
        handle.seek(start)
        lines = handle.read().decode(errors="replace").splitlines()[1 if start else 0 :]
    for entry in map(json.loads, reversed(lines)):
        texts = [block["text"].strip() for block in blocks_of(entry) if block.get("type") == "text"] if entry.get("type") == "assistant" else []
        for text in reversed(texts):
            if text:
                yield {"line": text.splitlines()[0], "from": "transcript", "at": entry.get("timestamp")}


def transcript(job: dict) -> Path | None:
    """A local job's transcript on this Mac, when its session still holds it; a handler's is its own session's."""
    if own := job.get("transcript"):
        return Path(own)
    if not (job.get("agent") and job.get("session")):
        return None
    return next((install.config_dir() / "projects").glob(f"*/{job['session']}/subagents/agent-{job['agent']}.jsonl"), None)


def read(name: str, job_id: str, job: dict, kind: str) -> dict:
    """One of a job's kept files as the dashboard's reader draws it: its title, a line of facts, and its markdown, its code, or a transcript's turns."""
    title = f"{job_id} {FILES[kind].lower()}: {job['goal']}"
    if kind == "transcript" or (kind == "brief" and not job.get("brief")):
        if not (path := transcript(job)):
            return {"title": title, "facts": "", "docs": [f"This Mac no longer holds the transcript of agent {job['agent']}."]}
        turns = [shaped for entry in turns_of(path.read_text(errors="replace")) if (shaped := turn_of(entry))["blocks"]]
        if kind == "brief":
            return {"title": title, "facts": f"the first turn of agent {job['agent']}'s transcript · {path}", "turns": turns[:1]}
        return {"title": title, "facts": f"{len(turns)} {'turn' if len(turns) == 1 else 'turns'} · {path}", "turns": turns}
    if kind == "question":
        asked = [Path(question["file"]) for question in job["questions"] if question["file"]]
        held = [path.read_text(errors="replace") if path.is_file() else f"This Mac no longer holds {path}." for path in asked]
        return {"title": title, "facts": f"{len(asked)} {'question' if len(asked) == 1 else 'questions'}, oldest first", "docs": held}
    path = state.drive_dir(name) / "reports" / f"{job_id}.md" if kind == "report" else Path(job[kind])
    if kind == "report" and not path.is_file():
        return {"title": title, "facts": "the lane's latest report", "docs": [job["report"]]}
    if not path.is_file():
        return {"title": title, "facts": "", "docs": [f"This Mac no longer holds {path}."]}
    text = path.read_text(errors="replace")
    lines = text.removesuffix("\n").count("\n") + 1
    facts = f"{lines} {'line' if lines == 1 else 'lines'} · {path}"
    if path.suffix in PROSE:
        return {"title": title, "facts": facts, "docs": [text]}
    return {"title": title, "facts": facts, "code": text, "language": path.suffix.removeprefix(".")}


def tool_result_text(content: str | list[dict] | None) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(part["text"] if part.get("type") == "text" else f"[{part.get('type')}]" for part in content or [])


def block_of(block: dict) -> dict | None:
    kind = block.get("type")
    if kind == "text":
        return {"text": block["text"]} if block["text"].strip() else None
    if kind == "thinking":
        return {"fold": "Thinking", "text": block["thinking"]} if block.get("thinking") else None
    if kind == "tool_use":
        return {"fold": block.get("name", "Tool"), "code": json.dumps(block.get("input"), indent=2)}
    if kind == "tool_result":
        return {"fold": "Result", "code": tool_result_text(block.get("content"))}
    if kind in ("image", "redacted_thinking"):
        return {"fold": str(kind), "code": ""}
    return {"fold": str(kind), "code": json.dumps(block, indent=2)}


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


def turn_of(entry: dict) -> dict:
    blocks = blocks_of(entry)
    stamp = entry.get("timestamp")
    who = speaker(entry, blocks)
    when = f"{dt.datetime.fromisoformat(stamp).astimezone(PACIFIC):%b %-d %-I:%M:%S %p}" if stamp else ""
    return {"who": WHO[who], "tone": who, "at": when, "blocks": [shaped for block in blocks if (shaped := block_of(block))]}


def source_window(lines: list[str], line: int, to: int) -> list[dict]:
    first, last = max(1, line - SOURCE_CONTEXT), min(len(lines), to + SOURCE_CONTEXT)
    return [{"n": n, "text": lines[n - 1]} for n in range(first, last + 1)]
