import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from drive import brief, install, state

PLUGIN = "codex@skills"
ENDED = ("completed", "failed", "died")
SAID_CHARS = 500
LOG_TAIL = 4096
COLLECT_SECONDS = 60
DISPATCH_SECONDS = 120
SCHEMA_FILE = "codex-result.json"
RESULT = {
    "type": "object",
    "properties": {
        "state": {
            "type": "string",
            "enum": [*(word for word in state.RESULT_STATES if word != "DESIGN"), "BUILT"],
            "description": (
                "The result your run ends on: DONE, finished and checked where it is used; READY or PUSHED, a PR is open; WAITING, it waits on a"
                " release or PR; ASK, a choice his words leave open; BUILT, the recommended option built"
                " on a branch while his card is open; BLOCKED, no route works; HANDOFF, the work is left for a builder to ship, such as edits left uncommitted."
            ),
        },
        "line": {
            "type": "string",
            "description": (
                "The rest of the result line after the state word, never repeating it, at most 400 characters: the proof for DONE, each PR link for"
                " READY or PUSHED, `<branch>, waits on <cards>` for BUILT, the ASK block's file for ASK, what is in the way for BLOCKED, the worktree"
                " and branch holding your edits and what is left for HANDOFF."
            ),
        },
        "report": {
            "type": "string",
            "description": (
                "Your whole return below the result line, one fact a line, such as its PR:, CHECK:, CHANGE:, UNPROVEN: and NEXT: lines, an"
                " `ASK: <file>` line for a choice's block, and the FIX line an Incident: line asks for; the drive keeps it as your job's report."
            ),
        },
    },
    "required": ["state", "line", "report"],
    "additionalProperties": False,
}


class CodexError(Exception):
    pass


@dataclass(frozen=True)
class Run:
    """The codex-ask run `drive job launch` started for a codex job, in the state codex-ask's own `--collect` record gives it: completed, failed, died, running or pending."""

    state: str
    reply: Path
    log: Path

    @property
    def ended(self) -> bool:
        return self.state in ENDED


def launcher() -> Path:
    return Path(install.installed(PLUGIN)["installPath"]) / "bin" / "codex-ask"


def ask(name: str, job_id: str, question: str, incident: bool) -> Path:
    """Start a codex job's codex-ask run on its brief, detached, its final message held by `--schema` to the drive's result, so no reply lacks its state: the run folder under the job's scratch folder, which a finished run's next launch reuses, and codex-ask refuses while a run in it still goes; an incident's run takes codex's fast tier."""
    scratch = state.drive_dir(name) / "scratch" / job_id
    scratch.mkdir(parents=True, exist_ok=True)
    schema = scratch / SCHEMA_FILE
    schema.write_text(json.dumps(RESULT, indent=2) + "\n")
    folder = scratch / "codex"
    argv = [str(launcher()), "--dispatch", "-s", str(folder), "--schema", str(schema), *(["--incident"] if incident else []), "-"]
    try:
        done = subprocess.run(argv, input=question, check=False, capture_output=True, text=True, timeout=DISPATCH_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CodexError(str(error)) from None
    if done.returncode:
        raise CodexError(" ".join(done.stderr.split()) or f"codex-ask --dispatch exited {done.returncode}")
    return folder


def run_of(job: dict) -> Run:
    """The codex job's run, read by `codex-ask --collect` on the run folder its launch recorded."""
    try:
        result = subprocess.run([str(launcher()), "--collect", job["codex_run"]], check=False, capture_output=True, text=True, timeout=COLLECT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CodexError(str(error)) from None
    if result.returncode:
        raise CodexError(" ".join(result.stderr.split()) or f"codex-ask --collect exited {result.returncode}")
    try:
        record = json.loads(result.stdout.splitlines()[0])
    except (IndexError, ValueError):
        raise CodexError(f"codex-ask --collect printed no record: {' '.join(result.stdout.split())[:200]}") from None
    return Run(record["state"], Path(record["reply_file"]), Path(record["log_file"]))


def take(name: str, data: dict, job_id: str, run: Run) -> None:
    """Set a running codex job from its run once it ended: a completed run's reply is the JSON its schema requires, whose state and line open the job's said line and whose report is kept under that line in its history, its report, so `drive job report` shows each codex answer newest first and `drive job replies` each one oldest first, unless state.result_refusal refuses it as `drive job report` refuses any job's return, such as one tracing a live bug's cause that names no fix, or brief.unproven a DONE on a surface he sees that its brief names, whose line is then `BLOCKED codex-ask completed, its reply refused:` and why; a reply that is not that JSON is kept whole and refused the same way; a failed or dead run's line is `BLOCKED codex-ask <state>:` and its log's last line."""
    if data["jobs"][job_id]["state"] != "running":
        return
    report = ""
    if run.state == "completed":
        text = run.reply.read_text()
        try:
            reply = json.loads(text)
            line = f"{reply['state']} {reply['line'].strip()}".strip()
            report = f"{line}\n{reply['report'].strip()}\n"
        except (ValueError, KeyError, TypeError, AttributeError):
            line, report = "", text
        state.keep_report(name, job_id, line or state.opening(text), report)
        job = data["jobs"][job_id]
        refused = (
            "it is not the JSON its schema requires, a state, line and report"
            if not line
            else state.result_refusal(data, job_id, line, report) or (line.startswith("DONE") and brief.surface(job) and brief.unproven(report, job_id, job))
        )
        if refused:
            line = f"BLOCKED codex-ask completed, its reply refused: {refused}"
    else:
        line = f"BLOCKED codex-ask {run.state}: {last_line(run.log)}"
    state.take_result(data, job_id, line[:SAID_CHARS], report)


def last_line(log: Path) -> str:
    """A codex-ask log's last line, read from its end, since a log can run to gigabytes; a run that died before codex started wrote none."""
    if not log.is_file():
        return "it wrote no log"
    with log.open("rb") as handle:
        handle.seek(max(0, handle.seek(0, os.SEEK_END) - LOG_TAIL))
        lines = handle.read().decode(errors="replace").splitlines()
    return next((line.strip() for line in reversed(lines) if line.strip()), "its log is empty")
