import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from drive import brief, install, state

PLUGIN = "codex@skills"
MODELS = ("sol", "luna", "astra")
ENDED = ("completed", "failed", "died")
SAID_CHARS = 500
LOG_TAIL = 4096
COLLECT_SECONDS = 60
DISPATCH_SECONDS = 120
SCHEMA_FILE = "codex-result.json"
REPRO = re.compile(r"^REPRO:[ \t]*(?P<path>\S+)", re.MULTILINE)
GUARD = Path(__file__).resolve().parents[2] / "hooks" / "landing_guard.py"
GUARD_MATCHER = "^Bash$"
GUARD_SECONDS = 60
RESULT = {
    "type": "object",
    "properties": {
        "state": {
            "type": "string",
            "enum": [*state.RESULT_STATES, "BUILT"],
            "description": (
                "The result your run ends on: DONE, finished and checked where it is used; READY or PUSHED, a PR is open; WAITING, it waits on a"
                " release or PR; ASK, a choice his words leave open; DESIGN, a design decision for his card; BUILT, the recommended option built"
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
                " `ASK: <file>` line for a choice's block, the FIX line an Incident: line asks for, and on a DONE a `REPRO: <path>` line naming"
                " an executable, read-only proof file that exits 0 once the work holds where it is used, printing a line for each check it passed,"
                " which the drive keeps and runs, since only its exit 0 delivers the work; the drive keeps it as your job's report."
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


def ask(name: str, job_id: str, question: str, incident: bool, model: str | None) -> Path:
    """Start a codex job's codex-ask run on its brief, detached, its final message held by `--schema` to the drive's result, so no reply lacks its state: the run folder under the job's scratch folder, which a finished run's next launch reuses, and codex-ask refuses while a run in it still goes; an incident's run takes codex's fast tier, and a job added with `--model` runs that model, any other codex-ask's own default, sol. The run carries its drive and job in its environment, so the hook guarded() writes holds each of its Bash calls as landing_guard holds a drive job's."""
    scratch = state.drive_dir(name) / "scratch" / job_id
    scratch.mkdir(parents=True, exist_ok=True)
    schema = scratch / SCHEMA_FILE
    schema.write_text(json.dumps(RESULT, indent=2) + "\n")
    folder = scratch / "codex"
    guarded()
    argv = [str(launcher()), "--dispatch", "-s", str(folder), "--schema", str(schema), *(["-m", model] if model else []), *(["--incident"] if incident else []), "-"]
    try:
        done = subprocess.run(argv, input=question, env=os.environ | {state.DRIVE_ENV: name, state.OWN_JOB_ENV: job_id}, check=False, capture_output=True, text=True, timeout=DISPATCH_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CodexError(str(error)) from None
    if done.returncode:
        raise CodexError(" ".join(done.stderr.split()) or f"codex-ask --dispatch exited {done.returncode}")
    return folder


def guarded() -> None:
    """Hold each codex run's Bash calls to landing_guard, as capt-hook holds a Claude session's: a PreToolUse group in codex's hooks.json runs this install's landing_guard on every call of a run that carries its job, trusted in config.toml as codex keeps the hash of a hook it may run, so the next run starts with it; the group an older install wrote is replaced in place."""
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    hooks = home / "hooks.json"
    handler = {"type": "command", "command": f'[ -z "${state.OWN_JOB_ENV}" ] || exec {shlex.quote(sys.executable)} -I {shlex.quote(str(GUARD))}', "timeout": GUARD_SECONDS}
    group = {"matcher": GUARD_MATCHER, "hooks": [handler]}
    written = json.loads(hooks.read_text()) if hooks.exists() else {"hooks": {}}
    groups = written["hooks"].setdefault("PreToolUse", [])
    at = next((index for index, kept in enumerate(groups) if any(f"/hooks/{GUARD.name}" in hook.get("command", "") for hook in kept["hooks"])), len(groups))
    if groups[at : at + 1] != [group]:
        groups[at : at + 1] = [group]
        replaced(hooks, json.dumps(written, indent=2) + "\n")
    shown = json.dumps({"event_name": "pre_tool_use", "hooks": [handler | {"async": False}], "matcher": GUARD_MATCHER}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    trusted = f"sha256:{hashlib.sha256(shown.encode()).hexdigest()}"
    config = home / "config.toml"
    text = config.read_text() if config.exists() else ""
    key = f"{hooks}:pre_tool_use:{at}:0"
    if (entry := tomllib.loads(text).get("hooks", {}).get("state", {}).get(key)) is None:
        replaced(config, f'{text}\n[hooks.state.{json.dumps(key)}]\ntrusted_hash = "{trusted}"\n')
    elif entry["trusted_hash"] != trusted:
        replaced(config, text.replace(entry["trusted_hash"], trusted, 1))


def replaced(path: Path, text: str) -> None:
    """Write a codex file whole in one rename, keeping its mode, since config.toml holds credentials only its user may read."""
    fresh = path.with_name(f".{path.name}.{os.getpid()}")
    fresh.write_text(text)
    if path.exists():
        fresh.chmod(path.stat().st_mode)
    os.replace(fresh, path)


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


def take(name: str, data: dict, job_id: str, run: Run) -> str | None:
    """Set a running codex job from its run once it ended: a completed run's reply is the JSON its schema requires, whose state and line open the job's said line and whose report is kept under that line in its history, its report, so `drive job report` shows each codex answer newest first and `drive job replies` each one oldest first, unless state.result_refusal refuses it as `drive job report` refuses any job's return, such as one tracing a live bug's cause that names no fix, or brief.unproven a DONE on a surface he sees that its brief names, whose line is then `BLOCKED codex-ask completed, its reply refused:` and why; a reply that is not that JSON is kept whole and refused the same way; a failed or dead run's line is `BLOCKED codex-ask <state>:` and its log's last line. Returns the builder state.fix_shipper added to ship an incident fix job's work, if any."""
    if data["jobs"][job_id]["state"] != "running":
        return None
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
        if line.startswith("DONE") and (repro := REPRO.search(report)) and Path(repro["path"]).expanduser().is_file():
            from drive import cli

            job["repro"] = cli.keep_repro(name, job_id, repro["path"])
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
    return state.fix_shipper(data, job_id)


def last_line(log: Path) -> str:
    """A codex-ask log's last line, read from its end, since a log can run to gigabytes; a run that died before codex started wrote none."""
    if not log.is_file():
        return "it wrote no log"
    with log.open("rb") as handle:
        handle.seek(max(0, handle.seek(0, os.SEEK_END) - LOG_TAIL))
        lines = handle.read().decode(errors="replace").splitlines()
    return next((line.strip() for line in reversed(lines) if line.strip()), "its log is empty")
