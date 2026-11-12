import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from drive import install, state

PLUGIN = "codex@skills"
REPLY_FILE = re.compile(r"REPLY_FILE: (/\S+)$", re.MULTILINE)
PRINTED = b"REPLY_FILE: /"
ENDED = ("completed", "failed", "died")
SAID_CHARS = 500
LOG_TAIL = 4096
COLLECT_SECONDS = 60


class CodexError(Exception):
    pass


@dataclass(frozen=True)
class Run:
    """The newest codex-ask run a codex job's relay asked, in the state codex-ask's own `--collect` record gives it: completed, failed, died, running or pending."""

    state: str
    reply: Path
    log: Path

    @property
    def ended(self) -> bool:
        return self.state in ENDED


def newest_run(transcript: Path) -> Run | None:
    """The run of the newest reply file codex-ask printed into the relay's tool results whose folder holds codex-ask's `meta` file, read by `codex-ask --collect` on that run folder; a printed path in any other folder, such as a reply codex wrote into a job's scratch folder and the relay read back, is passed over. None when the relay asked codex nothing, or its `-l` lane folder now holds another relay's later run."""
    reply = None
    for row in reversed(transcript.read_bytes().splitlines()):
        if PRINTED not in row:
            continue
        try:
            entry = json.loads(row)
        except ValueError:
            continue
        if found := [path for path in printed(entry) if (path.parent / "meta").is_file()]:
            reply = found[-1]
            break
    if reply is None or not reply.is_file():
        return None
    launcher = Path(install.installed(PLUGIN)["installPath"]) / "bin" / "codex-ask"
    try:
        result = subprocess.run([str(launcher), "--collect", str(reply.parent)], check=False, capture_output=True, text=True, timeout=COLLECT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CodexError(str(error)) from None
    if result.returncode:
        raise CodexError(" ".join(result.stderr.split()) or f"codex-ask --collect exited {result.returncode}")
    try:
        records = [json.loads(line) for line in result.stdout.splitlines()]
    except ValueError:
        raise CodexError(f"codex-ask --collect printed no record: {' '.join(result.stdout.split())[:200]}") from None
    record = next((record for record in records if record["reply_file"] == str(reply)), None)
    return Run(record["state"], reply, Path(record["log_file"])) if record else None


def printed(entry: dict) -> list[Path]:
    """The reply file each codex-ask call in a transcript row's tool results names, in their order: codex-ask opens its output with `REPLY_FILE: <path>` as a run starts and as an `--await` ends, so a result opening otherwise, such as a reply read back that quotes one, the relay's own words or a message it was sent, names none."""
    content = (entry.get("message") or {}).get("content")
    results = [block.get("content") or "" for block in content if isinstance(block, dict) and block.get("type") == "tool_result"] if isinstance(content, list) else []
    texts = (result if isinstance(result, str) else "\n".join(part.get("text", "") for part in result) for result in results)
    return [Path(opened[1]) for text in texts if (opened := REPLY_FILE.match(text))]


def take(name: str, data: dict, job_id: str, run: Run) -> None:
    """Set a running codex job from the run its relay asked, once it ended: a completed run's reply opens the job's said line and is kept whole in its history, its report, so `drive job report` shows each codex answer newest first and `drive job replies` each one oldest first, unless state.fix_refusal refuses a reply tracing a live bug's cause, whose line is then `BLOCKED codex-ask completed, its reply refused:` and why, since nothing resumes a codex run; a failed or dead run's line is `BLOCKED codex-ask <state>:` and its log's last line."""
    if data["jobs"][job_id]["state"] != "running":
        return
    reply = ""
    if run.state == "completed":
        reply = run.reply.read_text()
        line = next((line.strip() for line in reply.splitlines() if line.strip()), "")
        state.keep_report(name, job_id, line, reply)
        if refused := state.fix_refusal(data, job_id, line, reply):
            line = f"BLOCKED codex-ask completed, its reply refused: {refused}"
    else:
        line = f"BLOCKED codex-ask {run.state}: {last_line(run.log)}"
    state.take_result(data, job_id, line[:SAID_CHARS], reply)


def last_line(log: Path) -> str:
    """A codex-ask log's last line, read from its end, since a log can run to gigabytes; a run that died before codex started wrote none."""
    if not log.is_file():
        return "it wrote no log"
    with log.open("rb") as handle:
        handle.seek(max(0, handle.seek(0, os.SEEK_END) - LOG_TAIL))
        lines = handle.read().decode(errors="replace").splitlines()
    return next((line.strip() for line in reversed(lines) if line.strip()), "its log is empty")
