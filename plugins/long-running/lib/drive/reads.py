import json
import re
import shlex
import subprocess
from pathlib import Path

LOG = re.compile(r"LOG\s+(\S+)")
READ = re.compile(r"READ\s+(.+)")
READ_VERBS = (
    ("cci", "show"),
    ("cci", "grep"),
    ("cci", "find"),
    ("cci", "digest"),
    ("cci", "state"),
    ("ccn", "search"),
    ("ccn", "show"),
    ("ccn", "relevant"),
    ("ccn", "answer", "search"),
    ("ccn", "answer", "show"),
    ("ccn", "answer", "list"),
    ("ccn", "note", "search"),
    ("ccn", "note", "show"),
    ("ccn", "doc", "search"),
    ("ccn", "doc", "show"),
)
BUILDKITE_BUILD = re.compile(r"buildkite\.com/[^/\s]+/([^/\s]+)/builds/(\d+)")
BUILDKITE_JOB = re.compile(r"(?:#|jid=|/jobs/)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
ACTIONS_JOB = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/actions/runs/\d+/jobs?/(\d+)")
MARKUP = re.compile(r"\x1b_[^\x07]*\x07|\x1b\[[0-9;]*[A-Za-z]|\r")
COMMAND_START = "~~~ Running commands"
COMMAND_END = "~~~ Running repository post-command hook"
FAILED = ("failed", "timed_out")
BUDGET = 12000
CHARS_PER_TOKEN = 4
READ_SECONDS = 120


class ReadError(Exception):
    pass


def request(body: str) -> str | None:
    """A remote lane's read question, `LOG <url>` or `READ <cci or ccn read>`; None for a report."""
    line = body.strip()
    return line if LOG.fullmatch(line) or READ.fullmatch(line) else None


def answer(question: str, checkout: Path) -> str:
    """Answer a read question with this Mac's credentials and records, or say why it could not."""
    if log := LOG.fullmatch(question):
        return read_log(log[1], checkout)
    return read_records(READ.fullmatch(question)[1], checkout)


def read_log(url: str, checkout: Path) -> str:
    try:
        if build := BUILDKITE_BUILD.search(url):
            job = BUILDKITE_JOB.search(url)
            return buildkite(build[1], build[2], job[1] if job else None, checkout)
        if job := ACTIONS_JOB.search(url):
            budget = str(BUDGET // CHARS_PER_TOKEN)
            return run(["ccx", "vcs", "job-log", job[2], "-R", job[1], "--budget", budget], checkout)
    except ReadError as error:
        return f"LOG {url}: {error}"
    return f"LOG {url}: not a Buildkite build or job URL, or a GitHub Actions job URL"


def read_records(command: str, checkout: Path) -> str:
    try:
        argv = shlex.split(command)
    except ValueError as error:
        return f"READ {command}: {error}"
    if not any(tuple(argv[: len(verb)]) == verb for verb in READ_VERBS):
        allowed = ", ".join(" ".join(verb) for verb in READ_VERBS)
        return f"READ {command}: only these reads run here: {allowed}"
    try:
        output = run(argv, checkout).strip()
    except ReadError as error:
        return f"READ {command}: {error}"
    if len(output) <= BUDGET:
        return output
    return f"[first {BUDGET} of {len(output)} characters]\n{output[:BUDGET]}"


def buildkite(pipeline: str, number: str, job_id: str | None, checkout: Path) -> str:
    ci = str(checkout / "tools" / "ci")
    build = json.loads(run([ci, "bk", "api", f"/pipelines/{pipeline}/builds/{number}"], checkout))
    jobs = [
        job
        for job in build["jobs"]
        if job["type"] == "script" and (job["id"] == job_id if job_id else job["state"] in FAILED)
    ]
    if not jobs:
        missing = f"no job {job_id}" if job_id else "no failed job"
        return f"{build['web_url']} is {build['state']} with {missing}"
    share = BUDGET // len(jobs)
    return "\n\n".join(
        f"== {job['name']}: {job['state']}, exit {job['exit_status']}, {job['web_url']}\n"
        + excerpt(run([ci, "bk", "log", pipeline, number, job["id"]], checkout), share)
        for job in jobs
    )


def excerpt(log: str, limit: int) -> str:
    """The job's own command output, without Buildkite's hooks and markup, cut to its last `limit` characters."""
    output = MARKUP.sub("", log).split(COMMAND_START, 1)[-1].split(COMMAND_END, 1)[0].strip()
    if len(output) <= limit:
        return output
    return f"[last {limit} of {len(output)} characters]\n{output[-limit:]}"


def run(argv: list[str], checkout: Path) -> str:
    named = " ".join([Path(argv[0]).name, *argv[1:3]])
    try:
        done = subprocess.run(
            argv,
            cwd=checkout,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=READ_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReadError(f"{named}: {error}") from None
    if done.returncode:
        said = done.stderr.strip().splitlines()
        raise ReadError(f"{named} exited {done.returncode}: {said[-1] if said else 'no output'}")
    return done.stdout
