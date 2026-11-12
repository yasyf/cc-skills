import argparse
import configparser
import itertools
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

LOG = re.compile(r"LOG\s+(https?://\S+)")
READ = re.compile(r"READ\s+(.+)")
BREAK_GLASS = re.compile(r"BREAK-GLASS\s+(.+)")
BREAK_GLASS_PIPELINE = "forge/break-glass"
BREAK_GLASS_BRANCH = "dev"
STACK = re.compile(r"[a-z0-9-]+/([a-z0-9_-]+)")
BUILD_NUMBER = re.compile(r"https://buildkite\.com/\S+/builds/(\d+)")
ENVS = Path("infra") / "envs"
CLOUD = re.compile(r"^cloud:\n((?:[ \t]+\S.*(?:\n|$))+)", re.MULTILINE)
CLOUD_KEY = re.compile(r"^[ \t]+(account|namespace):[ \t]*(\S+)", re.MULTILINE)
AWS_CONFIG = "~/.aws/config"
JOB_ID = re.compile(r"^j\d+[\s:,]+")
READ_MARK = re.compile(r"^READ\b[\s:,]*")
REFUSED = "ASK refused, fix each line and ask again:"
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
PLAN_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
PLAN_WORD = re.compile(r"[^\W_]+")
PLANS = 5
PLAN_SECTIONS = 6
BUILDKITE_BUILD = re.compile(r"buildkite\.com/[^/\s]+/([^/\s]+)/builds/(\d+)")
BUILDKITE_JOB = re.compile(r"(?:#|jid=|/jobs/)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
ACTIONS_JOB = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/actions/runs/\d+/jobs?/(\d+)")
MARKUP = re.compile(r"\x1b_[^\x07]*\x07|\x1b\[[0-9;]*[A-Za-z]|\r")
FAILURE = re.compile(r"--- FAIL: |^\s*(?:FAIL\s|[✖✕●] )|panicked at |error TS\d+")
GO_FAIL = re.compile(r"--- FAIL: (\S+)")
GO_START = re.compile(r"=== (?:RUN|CONT|NAME)\s+(\S+)")
FAILURE_AFTER = 2
COMMAND_START = "~~~ Running commands"
COMMAND_END = "~~~ Running repository post-command hook"
FAILED = ("failed", "timed_out")
BUDGET = 12000
CHARS_PER_TOKEN = 4
READ_SECONDS = 120


class ReadError(Exception):
    pass


def request(body: str) -> str | None:
    """The read a remote lane's question asks for when its first line, past the lane's job id, opens with one: every `LOG <url>` the question names, with `READ:` before the first and any other text aside, `READ <command>` or `BREAK-GLASS <its flags> -- <command>`; None for a report or a question only the coordinator can answer."""
    line = opening(body)
    if LOG.match(READ_MARK.sub("", line, count=1)):
        return logs(body)
    return line if READ.match(line) or BREAK_GLASS.match(line) else None


def asks(body: str) -> bool:
    """Whether a remote lane's question is its `ASK` block for his card."""
    return opening(body).startswith("ASK ")


def opening(body: str) -> str:
    """A question's first line, past the lane's job id when it writes one."""
    return JOB_ID.sub("", body.strip().partition("\n")[0], count=1)


def logs(body: str) -> str | None:
    """Every `LOG <url>` a question names, wherever it names them, as one read; None when it names none."""
    urls = dict.fromkeys(LOG.findall(body))
    return f"LOG {' '.join(urls)}" if urls else None


def answer(question: str, checkout: Path, asker: str) -> str:
    """Answer a read question with this Mac's credentials and records, never with nothing, a `LOG` read splitting the budget across its URLs, a `BREAK-GLASS` read granted in the asker's name, and an `ASK` the watch refused with its refusal; ReadError says why it could not."""
    if question.startswith(REFUSED):
        return question
    if question.startswith("LOG "):
        urls = question.split()[1:]
        said = "\n\n".join(read_log(url, checkout, BUDGET // len(urls)) for url in urls)
    elif found := BREAK_GLASS.fullmatch(question):
        said = read_break_glass(found[1], checkout, asker)
    else:
        said = read_records(READ.fullmatch(question)[1], checkout)
    return said or f"{question}: empty, the read printed nothing"


def read_log(url: str, checkout: Path, budget: int) -> str:
    if build := BUILDKITE_BUILD.search(url):
        job = BUILDKITE_JOB.search(url)
        return buildkite(build[1], build[2], job[1] if job else None, checkout, budget)
    if job := ACTIONS_JOB.search(url):
        return run(["ccx", "vcs", "job-log", job[2], "-R", job[1], "--budget", str(budget // CHARS_PER_TOKEN)], checkout)
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
    return head(run(argv, checkout).strip())


def head(output: str) -> str:
    """The output whole when it fits the budget, else its first BUDGET characters under a line saying how much it cut."""
    if len(output) <= BUDGET:
        return output
    return f"[first {BUDGET} of {len(output)} characters]\n{output[:BUDGET]}"


def break_glass_options(options: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """The options with the flags and command `drive break-glass` takes after its job, which a remote lane's `BREAK-GLASS` read takes whole."""
    options.add_argument(
        "--stack",
        required=True,
        metavar="DOMAIN/ENV",
        help="the stack the grant names, such as sandsql/plat-usw2-prod; its env picks the account whose admin role the command runs as",
    )
    options.add_argument(
        "--reason", required=True, help="why, as #releases shows it beside the grant, such as: count the table copies a restore stranded, read only"
    )
    options.add_argument("command", nargs="+", help="after --, the command to run with this Mac's machine-chained profile for the env as AWS_PROFILE")
    return options


def read_break_glass(words: str, checkout: Path, asker: str) -> str:
    """Run a remote lane's command in the checkout under this Mac's machine profile once its break-glass grant passes, its output cut as a record read's is."""
    try:
        asked = break_glass_options(argparse.ArgumentParser(prog="BREAK-GLASS", add_help=False, exit_on_error=False)).parse_args(shlex.split(words))
    except (ValueError, argparse.ArgumentError) as error:
        raise ReadError(f'BREAK-GLASS --stack <domain>/<env> --reason "<why>" -- <command>: {error}') from None
    granted, profile = break_glass(asked.stack, asked.reason, asker, checkout)
    output = run(asked.command, checkout, {**os.environ, "AWS_PROFILE": profile}).strip()
    return f"break-glass grant {granted} passed; {Path(asked.command[0]).name} ran under the {profile} profile\n{head(output)}"


def break_glass(stack: str, reason: str, asker: str, checkout: Path) -> tuple[str, str]:
    """The forge/break-glass grant's build, started in the asker's name and passed, and the profile a command for the stack's env runs under on this Mac: the machine-chained one, so nobody signs in. A stack with no such profile is refused before any grant posts."""
    if not (named := STACK.fullmatch(stack)):
        raise ReadError(f'"{stack}" is no stack: name it as <domain>/<env>, such as sandsql/plat-usw2-prod')
    profile = machine_profile(named[1], checkout)
    why = f"{asker}: {reason}"
    created = run(
        ["bk", "build", "create", "-p", BREAK_GLASS_PIPELINE, "-b", BREAK_GLASS_BRANCH, "-m", why]
        + ["-e", f"BREAK_GLASS_REASON={why}", "-e", f"BREAK_GLASS_STACK={stack}", "--no-input", "-y"],
        checkout,
    )
    if not (build := BUILD_NUMBER.search(created)):
        raise ReadError(f"bk build create printed no build link: {created.strip()}")
    run(["bk", "build", "watch", build[1], "-p", BREAK_GLASS_PIPELINE, "--interval", "2"], checkout)
    ended = json.loads(run(["bk", "build", "view", build[1], "-p", BREAK_GLASS_PIPELINE, "-o", "json"], checkout))["state"]
    if ended != "passed":
        raise ReadError(f"the break-glass grant {build[0]} {ended}, so nothing ran")
    return build[0], profile


def machine_profile(env: str, checkout: Path) -> str:
    """The profile in this Mac's AWS config that assumes the admin role of the env's account, its role the account's name under its namespace, and reaches it through each source_profile from a credential_process, the machine certificate, never an SSO sign-in."""
    declared = checkout / ENVS / f"{env}.yaml"
    if not declared.is_file():
        raise ReadError(f"{env} is no env {checkout} declares in {ENVS}")
    cloud = dict(CLOUD_KEY.findall(block[1])) if (block := CLOUD.search(declared.read_text())) else {}
    if not {"account", "namespace"} <= cloud.keys():
        raise ReadError(f"{declared} names no cloud account and namespace, so no admin role runs it")
    role = f"{cloud['namespace']}-{cloud['account']}-admin"
    path = Path(os.environ.get("AWS_CONFIG_FILE", AWS_CONFIG)).expanduser()
    config = configparser.RawConfigParser()
    config.read(path)
    chained = (
        section.removeprefix("profile ")
        for section in config.sections()
        if config[section].get("role_arn", "").endswith(f":role/{role}") and machine_chained(config, section)
    )
    if not (profile := next(chained, None)):
        raise ReadError(f"no profile in {path} assumes {role} through source_profile from a credential_process, so {env} has no machine profile")
    return profile


def machine_chained(config: configparser.RawConfigParser, section: str) -> bool:
    """Whether the profile's source_profile chain starts at a credential_process, this Mac's machine certificate, rather than an SSO sign-in."""
    while "credential_process" not in config[section]:
        if not (source := config[section].get("source_profile")):
            return False
        section = source if source == "default" else f"profile {source}"
    return True


def plan_sections(words: str, checkout: Path) -> list[dict]:
    """The sections of the cc-notes plans in the checkout that carry the most of the words, best first, each with its plan and the headings above it."""
    terms = set(PLAN_WORD.findall(words.casefold()))
    plans = json.loads(run(["ccn", "plan", "search", words, "--limit", str(PLANS), "--json"], checkout))
    found = [
        {"plan": plan["id"][:7], "title": plan["title"], "status": plan["status"], **section}
        for plan in plans
        for section in sections(json.loads(run(["ccn", "plan", "show", plan["id"], "--json"], checkout))["body"])
    ]
    scored = [(sum(term in f"{section['heading']}\n{section['text']}".casefold() for term in terms), section) for section in found]
    return [section for score, section in sorted(scored, key=lambda item: -item[0]) if score][:PLAN_SECTIONS]


def sections(body: str) -> list[dict]:
    """A plan's text split at each markdown heading outside a code fence, each part under the path of headings above it."""
    path: list[tuple[int, str]] = []
    parts: list[dict] = [{"heading": "", "lines": []}]
    fenced = False
    for line in body.splitlines():
        fenced ^= line.lstrip().startswith("```")
        if heading := None if fenced else PLAN_HEADING.match(line):
            path = [*(kept for kept in path if kept[0] < len(heading[1])), (len(heading[1]), heading[2])]
            parts.append({"heading": " › ".join(title for _, title in path), "lines": []})
        else:
            parts[-1]["lines"].append(line)
    return [{"heading": part["heading"], "text": text} for part in parts if (text := "\n".join(part["lines"]).strip())]


def buildkite(pipeline: str, number: str, job_id: str | None, checkout: Path, budget: int) -> str:
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
    share = budget // len(jobs)
    return "\n\n".join(
        f"== {job['name']}: {job['state']}, exit {job['exit_status']}, {job['web_url']}\n"
        + excerpt(run([ci, "bk", "log", pipeline, number, job["id"]], checkout), share)
        for job in jobs
    )


def excerpt(log: str, limit: int) -> str:
    """The job's own command output, without Buildkite's hooks and markup, whole when it fits in `limit` characters, else its failure lines, then each failure's own output in an even share of what they leave, then its last characters in the rest."""
    output = MARKUP.sub("", log).split(COMMAND_START, 1)[-1].split(COMMAND_END, 1)[0].strip()
    if len(output) <= limit:
        return output
    found, own = failures(output)
    if not found:
        return f"[no failure lines matched]\n[last {limit} of {len(output)} characters]\n{output[-limit:]}"
    named = "\n".join(found)
    if len(named) >= limit:
        return f"[first {limit} of {len(named)} characters of the {len(found)} failure lines]\n{named[:limit]}"
    share = (limit - len(named)) // len(own)
    kept = [block[max(len(block) - share, 0) :] for block in own]
    said = (
        f"[failure lines, {len(found)} matched]\n{named}\n"
        f"[each failure's own output: a Go test's lines from its === RUN, else the failure line and the {FAILURE_AFTER} after it,"
        f" each cut to its last {share} characters]\n"
        + "\n--\n".join(kept)
    )
    rest = limit - len(named) - sum(map(len, kept))
    return f"{said}\n[last {rest} of {len(output)} characters]\n{output[-rest:]}" if rest else said


def failures(output: str) -> tuple[list[str], list[str]]:
    """The output's non-blank lines that match FAILURE, matched anywhere in a `--- FAIL:` line, and each one's own output, overlapping ones joined: a Go test's lines from its last `=== RUN`, `=== CONT` or `=== NAME` line, else the failure line, through the FAILURE_AFTER lines after it."""
    lines = [line for line in output.splitlines() if line.strip()]
    began: dict[str, int] = {}
    found: list[str] = []
    spans: list[tuple[int, int]] = []
    for at, line in enumerate(lines):
        if test := GO_START.search(line):
            began[test[1]] = at
        if not FAILURE.search(line):
            continue
        found.append(line)
        test = GO_FAIL.search(line)
        spans.append((began.get(test[1], at) if test else at, at + FAILURE_AFTER))
    joined: list[list[int]] = []
    for first, last in sorted(spans):
        if joined and first <= joined[-1][1] + 1:
            joined[-1][1] = max(joined[-1][1], last)
        else:
            joined.append([first, last])
    return found, ["\n".join(lines[first : last + 1]) for first, last in joined]


def run(argv: list[str], checkout: Path, env: dict[str, str] | None = None) -> str:
    named = " ".join([Path(argv[0]).name, *argv[1:3]])
    try:
        done = subprocess.run(
            argv,
            cwd=checkout,
            env=env,
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
