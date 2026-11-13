import argparse
import configparser
import contextlib
import itertools
import json
import os
import re
import select
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from drive import procs, state
from drive.render import clock

LOG = re.compile(r"LOG\s+(https?://\S+)")
READ = re.compile(r"READ\s+(.+)")
DRIVE = re.compile(r"DRIVE\s+(.+)")
DRIVE_BIN = Path(__file__).resolve().parents[2] / "bin" / "drive"
DRIVE_SECONDS = 540
DRIVE_EXIT = "drive exited"
SPRITE_LANE = "LONG_RUNNING_SPRITE_LANE"
LANE_VERBS = (
    ("job", "report"),
    ("job", "ask"),
    ("job", "set"),
    ("job", "replies"),
    ("job", "held"),
    ("pr", "add"),
    ("plan", "search"),
    ("manual",),
    ("status",),
)
LANE_WRITES = (("job", "ask"), ("job", "set"), ("pr", "add"))
NO_SPRITE_GRANT = 'his pick "No Sprite lane runs break-glass: a step needing a grant moves to a lane on this Mac"'
JOB_WORD = re.compile(r"j\d+")
BREAK_GLASS_PIPELINE = "forge/break-glass"
BREAK_GLASS_BRANCH = "dev"
GRANT_ENDS = "break-glass-expires-at"
STS_SHORTEST = timedelta(minutes=15)
STS_CLOCK_MARGIN = timedelta(minutes=1)
STOP_SIGNALS = {signal.SIGTERM, signal.SIGHUP, signal.SIGINT}
STOP_GRACE = 10
GRANT_ENDED = 124
ASSUMED_KEYS = ("role_arn", "source_profile", "role_session_name")
SESSION_NAME = re.compile(r"[^\w+=,.@-]+")
STACK = re.compile(r"[a-z0-9-]+/([a-z0-9_-]+)")
BUILD_NUMBER = re.compile(r"https://buildkite\.com/\S+/builds/(\d+)")
ENVS = Path("infra") / "envs"
CLOUD = re.compile(r"^cloud:\n((?:[ \t]+\S.*(?:\n|$))+)", re.MULTILINE)
CLOUD_KEY = re.compile(r"^[ \t]+(account|namespace):[ \t]*(\S+)", re.MULTILINE)
AWS_CONFIG = "~/.aws/config"
JOB_ID = re.compile(r"^j\d+[\s:,]+")
READ_MARK = re.compile(r"^READ\b[\s:,]*")
READ_WORD = re.compile(r"""(?:"([^"]*)"|'(.*?)'|(\S+))(?=\s|$)""")
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
PLANS = 5
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


@dataclass(frozen=True)
class Stops:
    reader: int
    signals: list[int]


@dataclass(frozen=True)
class Grant:
    link: str
    profile: str
    ends: datetime
    env: dict[str, str]
    stops: Stops


def request(body: str) -> str | None:
    """The read a remote lane's question asks for: a `DRIVE` question whole, else, when its first line, past the lane's job id, opens with one, every `LOG <url>` the question names, with `READ:` before the first and any other text aside, or `READ <command>`; None for a report or a question only the coordinator can answer."""
    if DRIVE.match(body.strip()):
        return body.strip()
    line = opening(body)
    if LOG.match(READ_MARK.sub("", line, count=1)):
        return logs(body)
    return line if READ.match(line) else None


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


def answer(question: str, checkout: Path, drive: str, job_id: str) -> str:
    """Answer a read question with this Mac's credentials and records, never with nothing, a `LOG` read splitting the budget across its URLs and a `DRIVE` question running its verb for the asking lane's job; ReadError says why a `LOG` or `READ` could not."""
    if question.startswith("DRIVE"):
        return lane_drive(question, checkout, drive, job_id)
    if question.startswith("LOG "):
        urls = question.split()[1:]
        said = "\n\n".join(read_log(url, checkout, BUDGET // len(urls)) for url in urls)
    else:
        said = read_records(READ.fullmatch(question)[1], checkout)
    return said or f"{question}: empty, the read printed nothing"


def lane_drive(question: str, checkout: Path, drive: str, job_id: str) -> str:
    """Run a Sprite lane's drive command here, where the drive's records live, as `drive` would on this Mac: its first line `DRIVE <its words>`, the rest the JSON the Sprite's drive sent with it, `stdin` for a `-` and the text of each file a word names under that word, each written to a folder of its own here for the run and named by its Sprite path in what it prints. It runs in the coordinator's checkout with no session of this Mac's, as the asking lane's job's own lane, marked as a SPRITE_LANE, so the drive it runs refuses, as lane_refusal says, a verb no Sprite lane runs, a write acting on another job and any verb acting on another drive, and reads a bare `status` as the lane's own job, and answers `drive exited <status>` over all it printed, as a Mac lane reads it, which the Sprite's drive prints and exits with."""
    line, _, rest = question.partition("\n")
    try:
        argv = shlex.split(DRIVE.fullmatch(line)[1])
        sent = json.loads(rest) if rest.strip() else {}
    except ValueError as error:
        return lane_exit(2, f"{line}: {error}")
    session = {key: value for key, value in os.environ.items() if key != "CLAUDE_CODE_SESSION_ID"}
    with tempfile.TemporaryDirectory(prefix="drive-lane-") as held:
        here = {}
        for number, (path, file) in enumerate(sent.get("files", {}).items()):
            copy = Path(held) / str(number) / Path(path).name
            copy.parent.mkdir()
            copy.write_text(file["text"])
            copy.chmod(0o755 if file["executable"] else 0o644)
            here[path] = str(copy)
        try:
            done = subprocess.run(
                [str(DRIVE_BIN), *(here.get(word, word) for word in argv)],
                cwd=checkout,
                env=session | {"LONG_RUNNING_DRIVE": drive, state.OWN_JOB_ENV: job_id, SPRITE_LANE: "1"},
                input=sent.get("stdin", ""),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=DRIVE_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return lane_exit(124, f"drive {' '.join(argv[:2])} ran past {DRIVE_SECONDS} seconds, stopped here; what it changed before then stays")
    printed = "\n".join(part.strip() for part in (done.stdout, done.stderr) if part.strip())
    for path, copy in here.items():
        printed = printed.replace(copy, path)
    return lane_exit(done.returncode, printed)


def lane_refusal(args: argparse.Namespace, parse: Callable[[list[str]], argparse.Namespace], drive: str, job_id: str) -> str | None:
    """Why a Sprite lane's drive command, as drive parsed it to run, may not: a `break-glass`, whose command would run on this Mac as its user, so the step moves to a lane on this Mac; a verb no builder runs, such as a tap or an order verb; a write, a `job report` keeping stdin or one of LANE_WRITES, naming a job other than its own in any value it parsed, while a read, such as another job's `job report`, `job replies` or `status`, reads it as a Mac lane does; a `job set` other than its `--repro`; or another drive. It reads the parsed values, so an abbreviated flag, a `--flag=value` and a word after `--` mean here what they mean to the verb."""
    words = tuple(word for word in (args.verb, getattr(args, "action", None)) if word)
    verb = next((verb for verb in LANE_VERBS if words[: len(verb)] == verb), None)
    refused = f"drive {' '.join(words)}: refused, nothing ran:"
    if words[:1] == ("break-glass",):
        return f"{refused} no Sprite lane runs break-glass, by {NO_SPRITE_GRANT}: return BLOCKED naming the step and its command, and the coordinator moves it to a lane on this Mac"
    if verb is None:
        return f"{refused} a lane runs only these verbs: {', '.join(' '.join(verb) for verb in LANE_VERBS)}"
    parsed = {key: value for key, value in vars(args).items() if key != "run"}
    values = {word for value in parsed.values() for word in (value if isinstance(value, list | tuple) else [value]) if isinstance(word, str)}
    writes = verb in LANE_WRITES or verb == ("job", "report") and args.stdin
    if writes and (others := sorted({word for word in values if JOB_WORD.fullmatch(word)} - {job_id})):
        return f"{refused} it names {', '.join(others)}, and a lane's drive writes act on its own job, {job_id}, alone, while its reads read any job"
    if verb == ("job", "set") and (
        flags := sorted(f"--{key.replace('_', '-')}" for key, value in vars(parse(["job", "set", job_id])).items() if key in parsed and key not in ("repro", "json", "drive") and parsed[key] != value)
    ):
        return f"{refused} a lane's job set takes only --repro, and it names {', '.join(flags)}"
    if parsed.get("drive") not in (None, drive):
        return f"{refused} it names drive {parsed['drive']}, and a lane acts on its own drive, {drive}, alone"
    return None


def lane_exit(status: int, printed: str) -> str:
    return f"{DRIVE_EXIT} {status}\n{printed}"


def read_log(url: str, checkout: Path, budget: int) -> str:
    if build := BUILDKITE_BUILD.search(url):
        job = BUILDKITE_JOB.search(url)
        return buildkite(build[1], build[2], job[1] if job else None, checkout, budget)
    if job := ACTIONS_JOB.search(url):
        return run(["ccx", "vcs", "job-log", job[2], "-R", job[1], "--budget", str(budget // CHARS_PER_TOKEN)], checkout)
    raise ReadError("not a Buildkite build or job URL, or a GitHub Actions job URL")


def read_records(command: str, checkout: Path) -> str:
    """Run one listed record read; a search or grep takes its words up to the first flag as one query, quoted or not."""
    argv = read_words(command)
    verb = next((verb for verb in READ_VERBS if tuple(argv[: len(verb)]) == verb), None)
    if verb is None:
        allowed = ", ".join(" ".join(verb) for verb in READ_VERBS)
        raise ReadError(f"only these reads run here: {allowed}")
    query = list(itertools.takewhile(lambda word: not word.startswith("-"), argv[len(verb) :]))
    if verb[-1] in QUERY_VERBS and query:
        argv = [*verb, " ".join(query), *argv[len(verb) + len(query) :]]
    return head(run(argv, checkout).strip())


def read_words(command: str) -> list[str]:
    """A read's words as a lane writes them in free text: a run in double quotes, or in single quotes closed where a word ends, is one word without its quotes, and any other quote, such as the apostrophe of "a chat's pin", is a letter of its word."""
    return [next(part for part in found.groups() if part is not None) for found in READ_WORD.finditer(command)]


def head(output: str) -> str:
    """The output whole when it fits the budget, else its first BUDGET characters under a line saying how much it cut."""
    if len(output) <= BUDGET:
        return output
    return f"[first {BUDGET} of {len(output)} characters]\n{output[:BUDGET]}"


def break_glass_options(options: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """The options with the flags and command `drive break-glass` takes after its job."""
    options.add_argument(
        "--stack",
        required=True,
        metavar="DOMAIN/ENV",
        help="the stack the grant names, such as sandsql/plat-usw2-prod; its env picks the account whose admin role the command runs as",
    )
    options.add_argument(
        "--reason",
        required=True,
        type=grant_reason,
        help="why, as #releases shows it beside the grant, such as: count the table copies a restore stranded, read only",
    )
    options.add_argument(
        "command",
        nargs="+",
        help="after --, the command to run holding only the env's admin credentials, minted on this Mac's machine profile to end with the grant, and stopped with every process it started at the grant's end",
    )
    return options


def grant_reason(words: str) -> str:
    """The reason a grant posts beside its link in #releases, refused when blank, since a grant nobody can account for is the one break-glass forbids."""
    if not words.strip():
        raise argparse.ArgumentTypeError("is blank: say why, as #releases shows it beside the grant, such as: count rows, read only")
    return words.strip()


def held(granted: Grant) -> str:
    """What a command under the grant held, as its run says it."""
    return f"holding only the {granted.profile} profile's credentials, which end at {clock(granted.ends.astimezone())}"


@contextlib.contextmanager
def stop_signals() -> Iterator[Stops]:
    """Record SIGTERM, SIGHUP and SIGINT in the block rather than act on them, each one, as each SIGCHLD, waking whatever waits on the stops' reader, so a drive told to stop, once or again, still ends its command's processes and deletes its grant's credentials before it exits."""
    reader, writer = os.pipe()
    os.set_blocking(reader, False)
    os.set_blocking(writer, False)
    stops = Stops(reader, [])
    kept = {signum: signal.signal(signum, lambda signum, _frame: stops.signals.append(signum)) for signum in STOP_SIGNALS}
    kept[signal.SIGCHLD] = signal.signal(signal.SIGCHLD, lambda _signum, _frame: None)
    woken = signal.set_wakeup_fd(writer)
    try:
        yield stops
    finally:
        signal.set_wakeup_fd(woken)
        for signum, handler in kept.items():
            signal.signal(signum, handler)
        os.close(reader)
        os.close(writer)


def run_granted(command: list[str], granted: Grant) -> int:
    """Run the command under the grant as a process group of its own, its exit status the run's, and end every process left in that group: at once when the command exits; with SIGTERM STOP_GRACE seconds before the grant ends and SIGKILL at its end, exiting GRANT_ENDED; and with SIGTERM once drive is told to stop and SIGKILL STOP_GRACE seconds on or at the grant's end if that comes first, exiting 128 plus the signal. A drive told to stop before the command starts, or whose grant has ended by then, never starts it. A process that leaves the group, as a daemon does with setsid, is out of its reach."""
    if granted.stops.signals:
        print(f"break-glass stopped by {signal.Signals(granted.stops.signals[0]).name} before its command started, so nothing ran", file=sys.stderr, flush=True)
        return 128 + granted.stops.signals[0]
    ends = time.monotonic() + (granted.ends - state.now()).total_seconds()
    if ends <= time.monotonic():
        print(f"the break-glass grant {granted.link} ended at {clock(granted.ends.astimezone(), seconds=True)} before its command started, so nothing ran", file=sys.stderr, flush=True)
        return GRANT_ENDED
    group = subprocess.Popen(command, env=granted.env, start_new_session=True)
    stopping = select.poll()
    stopping.register(granted.stops.reader, select.POLLIN)
    killed_by = time.monotonic()
    try:
        while (status := group.poll()) is None and not granted.stops.signals and (left := ends - STOP_GRACE - time.monotonic()) > 0:
            if stopping.poll(left * 1000):
                os.read(granted.stops.reader, 512)
        if status is not None:
            return status
        killed_by = min(time.monotonic() + STOP_GRACE, ends) if granted.stops.signals else ends
    finally:
        end(group, killed_by)
    if granted.stops.signals:
        print(f"break-glass stopped by {signal.Signals(granted.stops.signals[0]).name}, so its command was stopped with every process left in its group", file=sys.stderr, flush=True)
        return 128 + granted.stops.signals[0]
    print(f"the break-glass grant {granted.link} ended at {clock(granted.ends.astimezone(), seconds=True)}, so its command was stopped with every process left in its group", file=sys.stderr, flush=True)
    return GRANT_ENDED


def end(group: subprocess.Popen, killed_by: float) -> None:
    """End every process left in the command's group: SIGTERM, then SIGKILL once the command exits or the time.monotonic() clock reaches killed_by, and SIGKILL alone once it has."""
    if killed_by > time.monotonic() and signal_group(group.pid, signal.SIGTERM):
        with contextlib.suppress(subprocess.TimeoutExpired):
            group.wait(timeout=killed_by - time.monotonic())
    signal_group(group.pid, signal.SIGKILL)
    group.wait()


def signal_group(group: int, signum: int) -> bool:
    """Send the signal to every process in the group; False when none is left to get it, none at all or only exited ones their parent has yet to reap, which macOS refuses to signal with EPERM, so a live process it may not signal still raises."""
    try:
        os.killpg(group, signum)
    except ProcessLookupError:
        return False
    except PermissionError:
        if live(group):
            raise
        return False
    return True


def live(group: int) -> bool:
    """Whether a process in the group is still running rather than exited and waiting to be reaped."""
    return any(process.pgid == group and not process.zombie for process in procs.table())


@contextlib.contextmanager
def break_glass(stack: str, reason: str, asker: str, checkout: Path) -> Iterator[Grant]:
    """The forge/break-glass grant for the stack, started in the asker's name and passed, with the environment a command under it runs in: the env's admin credentials, minted on this Mac's machine profile so nobody signs in and ending no later than the grant, as the one profile its AWS config names, every other AWS setting of this Mac left out, so a command naming another stack's profile finds none. A stack with no machine profile is refused before any grant posts; the credentials go when the block ends, and from their writing to their deletion drive's stop signals are recorded for run_granted rather than acted on, so a drive told to stop deletes them."""
    if not (named := STACK.fullmatch(stack)):
        raise ReadError(f'"{stack}" is no stack: name it as <domain>/<env>, such as sandsql/plat-usw2-prod')
    profile = machine_profile(named[1], checkout)
    link, ends = grant(stack, f"{asker}: {reason}", checkout)
    minted = mint(profile, SESSION_NAME.sub("-", asker)[:64], link, ends, checkout)
    name = profile.name.removeprefix("profile ")
    settings = {key: value for key, value in profile.items() if key not in ASSUMED_KEYS}
    with stop_signals() as stops, tempfile.TemporaryDirectory(prefix="drive-break-glass-") as kept:
        config = configparser.RawConfigParser()
        config[f"profile {name}"] = {
            **settings,
            "aws_access_key_id": minted["AccessKeyId"],
            "aws_secret_access_key": minted["SecretAccessKey"],
            "aws_session_token": minted["SessionToken"],
        }
        path = Path(kept) / "config"
        with path.open("w") as written:
            config.write(written)
        isolated = {key: value for key, value in os.environ.items() if not key.startswith("AWS_")}
        yield Grant(link, name, ends, {**isolated, "AWS_CONFIG_FILE": str(path), "AWS_SHARED_CREDENTIALS_FILE": os.devnull, "AWS_PROFILE": name}, stops)


def grant(stack: str, why: str, checkout: Path) -> tuple[str, datetime]:
    """The link of the forge/break-glass grant started with the reason once it passed, and the end its build records, an hour after it granted."""
    created = run(
        ["bk", "build", "create", "-p", BREAK_GLASS_PIPELINE, "-b", BREAK_GLASS_BRANCH, "-m", why]
        + ["-e", f"BREAK_GLASS_REASON={why}", "-e", f"BREAK_GLASS_STACK={stack}", "--no-input", "-y"],
        checkout,
    )
    if not (build := BUILD_NUMBER.search(created)):
        raise ReadError(f"bk build create printed no build link: {created.strip()}")
    run(["bk", "build", "watch", build[1], "-p", BREAK_GLASS_PIPELINE, "--interval", "2"], checkout, bounded=False)
    viewed = json.loads(run(["bk", "build", "view", build[1], "-p", BREAK_GLASS_PIPELINE, "-o", "json"], checkout))
    if viewed["state"] != "passed":
        raise ReadError(f"the break-glass grant {build[0]} {viewed['state']}, so nothing ran")
    return build[0], state.parse_time(viewed["meta_data"][GRANT_ENDS])


def mint(profile: configparser.SectionProxy, session: str, link: str, ends: datetime, checkout: Path) -> dict[str, str]:
    """Credentials for the profile's role, assumed from its source profile, lasting until STS_CLOCK_MARGIN before the grant ends, since STS stamps their end by its own clock; refused when that leaves less than the STS_SHORTEST STS mints, as it does once the grant ended, and when STS still stamps an end past the grant's."""
    lasting = ends - STS_CLOCK_MARGIN - state.now()
    if lasting < STS_SHORTEST:
        raise ReadError(
            f"the break-glass grant {link} ends at {clock(ends.astimezone(), seconds=True)}, too soon for the {STS_SHORTEST.seconds // 60} minutes"
            " the shortest AWS credentials last, so nothing ran: start a fresh grant"
        )
    assumed = run(
        ["aws", "sts", "assume-role", "--profile", profile["source_profile"], "--role-arn", profile["role_arn"]]
        + ["--role-session-name", session, "--duration-seconds", str(int(lasting.total_seconds())), "--output", "json"],
        checkout,
    )
    minted = json.loads(assumed)["Credentials"]
    if state.parse_time(minted["Expiration"]) > ends:
        raise ReadError(f"STS minted credentials ending at {minted['Expiration']}, past the end of the break-glass grant {link} at {ends.isoformat()}, so nothing ran")
    return minted


def machine_profile(env: str, checkout: Path) -> configparser.SectionProxy:
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
        config[section]
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


def plan_sections(phrases: list[str], checkout: Path) -> list[dict]:
    """The sections of the cc-notes plans in the checkout whose headings or lines carry one of the phrases whole, in any case, plans best first, each with its plan, the headings above it and only the lines carrying one."""
    phrase = re.compile("|".join(rf"(?<!\w){re.escape(each)}(?!\w)" for each in phrases), re.IGNORECASE)
    plans = json.loads(run(["ccn", "plan", "search", " ".join(phrases), "--limit", str(PLANS), "--json"], checkout))
    return [
        {"plan": plan["id"][:7], "title": plan["title"], "status": plan["status"], "heading": section["heading"], "text": "\n".join(lines)}
        for plan in plans
        for section in sections(json.loads(run(["ccn", "plan", "show", plan["id"], "--json"], checkout))["body"])
        if (lines := [line for line in section["text"].splitlines() if phrase.search(line)]) or phrase.search(section["heading"])
    ]


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


def run(argv: list[str], checkout: Path, env: dict[str, str] | None = None, bounded: bool = True) -> str:
    named = " ".join([Path(argv[0]).name, *argv[1:3]])
    try:
        done = subprocess.run(
            argv,
            cwd=checkout,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=READ_SECONDS if bounded else None,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReadError(f"{named}: {error}") from None
    if done.returncode:
        said = done.stderr.strip().splitlines()
        raise ReadError(f"{named} exited {done.returncode}: {said[-1] if said else 'no output'}")
    return done.stdout
