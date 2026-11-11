import itertools
import json
import re
import shlex
import subprocess
from pathlib import Path

LOG = re.compile(r"LOG\s+(https?://\S+)")
READ = re.compile(r"READ\s+(.+)")
JOB_ID = re.compile(r"^j\d+[\s:,]+")
READ_MARK = re.compile(r"^READ\b[\s:,]*")
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
QUERY_VERBS = ("search", "grep")
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
    """The read a remote lane's question asks for, from its first line past the lane's job id: `LOG <url>`, `READ:` before it and whatever follows the URL aside, or `READ <command>`; None for a report or a question only the coordinator can answer."""
    line = JOB_ID.sub("", body.strip().partition("\n")[0], count=1)
    if log := LOG.match(READ_MARK.sub("", line, count=1)):
        return f"LOG {log[1]}"
    return line if READ.match(line) else None


def answer(question: str, checkout: Path) -> str:
    """Answer a read question with this Mac's credentials and records, never with nothing; ReadError says why it could not."""
    log = LOG.fullmatch(question)
    said = read_log(log[1], checkout) if log else read_records(READ.fullmatch(question)[1], checkout)
    return said or f"{question}: empty, the read printed nothing"


def read_log(url: str, checkout: Path) -> str:
    if build := BUILDKITE_BUILD.search(url):
        job = BUILDKITE_JOB.search(url)
        return buildkite(build[1], build[2], job[1] if job else None, checkout)
    if job := ACTIONS_JOB.search(url):
        budget = str(BUDGET // CHARS_PER_TOKEN)
        return run(["ccx", "vcs", "job-log", job[2], "-R", job[1], "--budget", budget], checkout)
    raise ReadError("not a Buildkite build or job URL, or a GitHub Actions job URL")


def read_records(command: str, checkout: Path) -> str:
    """Run one listed record read; a search or grep takes its words up to the first flag as one query, quoted or not."""
    try:
        argv = shlex.split(command)
    except ValueError as error:
        raise ReadError(str(error)) from None
    verb = next((verb for verb in READ_VERBS if tuple(argv[: len(verb)]) == verb), None)
    if verb is None:
        allowed = ", ".join(" ".join(verb) for verb in READ_VERBS)
        raise ReadError(f"only these reads run here: {allowed}")
    query = list(itertools.takewhile(lambda word: not word.startswith("-"), argv[len(verb) :]))
    if verb[-1] in QUERY_VERBS and query:
        argv = [*verb, " ".join(query), *argv[len(verb) + len(query) :]]
    output = run(argv, checkout).strip()
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
