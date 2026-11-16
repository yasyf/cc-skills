import argparse
import contextlib
import hashlib
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
from datetime import datetime
from pathlib import Path

from drive import limits, picture, procs, slack, state
from drive.render import clock

READ = re.compile(r"READ\s+(.+)")
READ_MARK = re.compile(r"^READ\b[\s:,]*")
LOG = re.compile(r"LOG\s+https?://\S+")
LOG_RULE = "Read a red check's whole log here"
DRIVE = re.compile(r"DRIVE\s+(.+)")
CREDENTIALS = [
    ("a GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", re.ASCII)),
    ("a GitHub fine-grained token", re.compile(r"\bgithub_pat_\w{20,}\b", re.ASCII)),
    ("an sk- API key", re.compile(r"\bsk-[\w-]{20,}\b", re.ASCII)),
    ("a Slack token", re.compile(r"\bxox[abprs]-[\w-]{10,}\b", re.ASCII)),
    ("an AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", re.ASCII)),
]
DRIVE_BIN = Path(__file__).resolve().parents[2] / "bin" / "drive"
DRIVE_SECONDS = 540
LANE_GIVES_UP = 240
DRIVE_EXIT = "drive exited"
SPRITE_LANE = "LONG_RUNNING_SPRITE_LANE"
LANE_VERBS = (
    ("job", "report"),
    ("job", "ask"),
    ("job", "set"),
    ("job", "replies"),
    ("job", "held"),
    ("pr", "add"),
    ("pr", "set"),
    ("work", "pull"),
    ("work", "done"),
    ("work", "back"),
    ("plan", "search"),
    ("datadog",),
    ("fde-scan",),
    ("manual",),
    ("page-owner",),
    ("status",),
    ("wait",),
)
LANE_WRITES = (("job", "ask"), ("job", "set"), ("pr", "add"), ("pr", "set"), ("work", "pull"), ("work", "done"), ("work", "back"))
LANE_FLAGS = {("job", "set"): "repro", ("pr", "set"): "flaky"}
LANE_CARDS = (("job", "ask"), ("job", "report"))
NO_SPRITE_GRANT = 'his pick "No Sprite lane runs break-glass: a step needing a grant moves to a lane on this Mac"'
JOB_WORD = re.compile(r"j\d+")
DRIVE_RECORD = re.compile(r"[oj]\d+")
NAMED_COMMAND = re.compile(r"\bdrive ((?:(?!\bdrive |\. )[^,;\n])+)")
STOP_SIGNALS = {signal.SIGTERM, signal.SIGHUP, signal.SIGINT}
STOP_GRACE = 10
GRANT_ENDED = 124
JOB_ID = re.compile(r"^j\d+[\s:,]+")
READ_WORD = re.compile(r"""(?:"([^"]*)"|'(.*?)'|(\S+))(?=\s|$)""")
SLACK_READS = (("cc-slack", "thread"), ("cc-slack", "history"))
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
    ("ccn", "answer", "match"),
    ("ccn", "answer", "show"),
    ("ccn", "answer", "list"),
    ("ccn", "note", "search"),
    ("ccn", "note", "show"),
    ("ccn", "doc", "search"),
    ("ccn", "doc", "show"),
    *SLACK_READS,
)
PLAN_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
PLANS = 5
BUDGET = 12000
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
    """The read a remote lane's question asks for: a `DRIVE` question whole, else its first line, past the lane's job id, when it opens with `READ <command>`, or its first `LOG <url>` when that line opens with one, past any `READ:`, as a lane started under the builder rules from before the Mac's log read was deleted still asks; None for a report or a question only the coordinator can answer."""
    if DRIVE.match(body.strip()):
        return body.strip()
    line = opening(body)
    if log := LOG.match(READ_MARK.sub("", line, count=1)):
        return log[0]
    return line if READ.match(line) else None


def asks(body: str) -> bool:
    """Whether a remote lane's question is its `ASK` block for his card."""
    return opening(body).startswith("ASK ")


def opening(body: str) -> str:
    """A question's first line, past the lane's job id when it writes one."""
    return JOB_ID.sub("", body.strip().partition("\n")[0], count=1)


def answer(question: str, checkout: Path, drive: str, job_id: str) -> str:
    """Answer a read question with this Mac's records, never with nothing, a `READ` naming files of the drive's folder reading them, a `DRIVE` question running its verb for the asking lane's job and a `LOG` question getting log_rule; ReadError says why a `READ` could not."""
    if question.startswith("DRIVE"):
        return lane_drive(question, checkout, drive, job_id)
    if LOG.fullmatch(question):
        return log_rule(question)
    return read_records(READ.fullmatch(question)[1], checkout, drive) or f"{question}: empty, the read printed nothing"


def log_rule(question: str) -> str:
    """What a `LOG <url>` question gets in place of the log this Mac no longer reads: the builder rule that has a lane read it on its own Sprite, as agents/builder.md words it, and `BLOCKED` when its Sprite's CI tool refuses."""
    from drive import orca

    rule = next(bullet for bullet in orca.builder_body().split("\n- ") if bullet.startswith(LOG_RULE))
    return (
        f"{question}: this Mac reads no build log for a lane; read it on your Sprite, by the builder rule that replaced the LOG read:\n\n"
        f"{' '.join(rule.split())}\n\n"
        "When the repository's CI tool refuses on your Sprite for want of its AWS session, return BLOCKED naming the read and its command."
    )


def lane_drive(question: str, checkout: Path, drive: str, job_id: str) -> str:
    """Run a Sprite lane's drive command here, where the drive's records live, as `drive` would on this Mac: its first line `DRIVE <its words>`, the rest the JSON the Sprite's drive sent with it, `stdin` for a `-` and the text of each file a word names under that word, each written to a folder of its own here for the run; for one of LANE_CARDS, the verbs a card is drawn from, each picture they show by a path on the Sprite, and the proof file a job report's REPRO: line names, is copied off it first, as lane_files says, and named by its copy here, so a card this Mac draws from them opens it and the report keeps that proof file, while any other verb, such as `job set --repro`, gets its files and stdin as written; each is named by its Sprite path in what it prints. It runs in the coordinator's checkout with no session of this Mac's, as the asking lane's job's own lane, marked as a SPRITE_LANE, so the drive it runs refuses, as lane_refusal says, a verb no Sprite lane runs, a write acting on another job and any verb acting on another drive, and reads a bare `status` as the lane's own job, and answers `drive exited <status>` over all it printed, as a Mac lane reads it, which the Sprite's drive prints and exits with."""
    line, _, rest = question.partition("\n")
    try:
        argv = shlex.split(DRIVE.fullmatch(line)[1])
        sent = json.loads(rest) if rest.strip() else {}
    except ValueError as error:
        return lane_exit(2, f"{line}: {error}")
    try:
        shown = lane_files(sent, drive, job_id, tuple(argv[:2]))
    except (state.DriveError, OSError, subprocess.TimeoutExpired) as error:
        return lane_exit(2, f"drive {' '.join(argv[:2])}: refused, nothing ran: the files it shows or names did not copy off this Sprite: {error}")
    session = {key: value for key, value in os.environ.items() if key != "CLAUDE_CODE_SESSION_ID"}
    with tempfile.TemporaryDirectory(prefix="drive-lane-") as held:
        here = {}
        for number, (path, file) in enumerate(sent.get("files", {}).items()):
            copy = Path(held) / str(number) / Path(path).name
            copy.parent.mkdir()
            copy.write_text(shown_here(file["text"], shown))
            copy.chmod(0o755 if file["executable"] else 0o644)
            here[path] = str(copy)
        try:
            done = subprocess.run(
                [str(DRIVE_BIN), *(here.get(word, word) for word in argv)],
                cwd=checkout,
                env=session | {state.DRIVE_ENV: drive, state.OWN_JOB_ENV: job_id, SPRITE_LANE: "1"},
                input=state.REPRO.sub(lambda named: named[0].replace(named["path"], shown.get(named["path"], named["path"])), shown_here(sent.get("stdin", ""), shown)),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=DRIVE_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return lane_exit(124, f"drive {' '.join(argv[:2])} ran past {DRIVE_SECONDS} seconds, stopped here; what it changed before then stays")
    printed = "\n".join(part.strip() for part in (done.stdout, done.stderr) if part.strip())
    for path, copy in (here | shown).items():
        printed = printed.replace(copy, path)
    return lane_exit(done.returncode, printed)


def lane_files(sent: dict, drive: str, job_id: str, verb: tuple[str, ...]) -> dict[str, str]:
    """For one of LANE_CARDS, each picture the files and stdin a Sprite lane's drive sent show on a line of its own, `![caption](/absolute/path)` as a card draws it, and for a job report the proof file its REPRO: line names, each by a path on its Sprite, copied off that Sprite as off_sprite copies it; the copy here by the Sprite path."""
    if verb not in LANE_CARDS:
        return {}
    stdin = sent.get("stdin", "")
    texts = [file["text"] for file in sent.get("files", {}).values()] + [stdin]
    pictures = [shown["path"] for text in texts for line in text.splitlines() if (shown := limits.POSTED_PICTURE.fullmatch(line.strip()))]
    proofs = [named["path"] for named in [state.REPRO.search(stdin)] if verb == ("job", "report") and named and named["path"].startswith("/")]
    return off_sprite(drive, job_id, list(dict.fromkeys([*pictures, *proofs])))


def off_sprite(drive: str, job_id: str, paths: list[str]) -> dict[str, str]:
    """Each file at its path on the job's Sprite copied off it under the job's scratch folder here, at that path below its `sprite` folder, which outlives the run since a card drawn later from what names it opens it; the copy here by the Sprite path."""
    if not paths:
        return {}
    from drive import orca

    into = sprite_folder(drive, job_id)
    orca.take_off_sprite(drive, state.load(drive)["jobs"][job_id]["workspace"], paths, into)
    return {path: str(into / path.lstrip("/")) for path in paths}


def sprite_folder(drive: str, job_id: str) -> Path:
    return state.drive_dir(drive) / "scratch" / job_id / "sprite"


def shown_here(text: str, shown: dict[str, str]) -> str:
    """The text with each picture it shows by a Sprite path naming its copy here instead."""
    return picture.IMAGE.sub(lambda image: image[0].replace(path := image[1] or image[2], shown.get(path, path)), text)


def shown_off_sprite(drive: str, jobs: list[str], text: str) -> str:
    """The text with each picture it shows by a CI artifact link named by its download here, by artifacts_here, and each it shows by a path that is no file here named by its copy off a Sprite, as off_sprite kept it under the first of the jobs holding one, such as the PR's builder before a handoff; a picture with no copy is left as it is."""
    text = artifacts_here(drive, text)
    copies = {path: [sprite_folder(drive, job_id) / path.lstrip("/") for job_id in jobs] for path in unfound(text)}
    return shown_here(text, {path: str(held[0]) for path, kept in copies.items() if (held := [copy for copy in kept if copy.is_file()])})


def artifacts_here(drive: str, text: str) -> str:
    """The text with each picture it shows by a link to one of the repository's CI artifacts, such as a picture a Sprite lane uploaded from its build, named by its download under the drive's pictures, fetched once through the repository's build provider; one that does not download is left as its link, which then names why."""
    from drive import providers

    links = [source for image in picture.IMAGE.finditer(text) if (source := image[1] or image[2]).startswith("https://")]
    if not links:
        return text
    build = providers.of(state.load(drive)).build
    shown = {}
    for source in dict.fromkeys(links):
        if not (link := build.artifact_link(source)):
            continue
        out = state.drive_dir(drive) / "pictures" / "artifacts" / f"{hashlib.sha256(source.encode()).hexdigest()[:16]}.png"
        if not out.is_file():
            out.parent.mkdir(parents=True, exist_ok=True)
            partial = out.with_suffix(".part")
            try:
                build.artifact(link, partial)
            except providers.ProviderError as error:
                print(f"drive pictures: the CI artifact {source} did not download: {error}", file=sys.stderr)
                continue
            partial.replace(out)
        shown[source] = str(out)
    return shown_here(text, shown)


def unfound(text: str) -> list[str]:
    """Each picture the text shows by an absolute path that is no file here, such as one a Sprite lane shot."""
    return list(dict.fromkeys(path for image in picture.IMAGE.finditer(text) if (path := image[1] or image[2]).startswith("/") and not Path(path).is_file()))


def claimed(drive: str, worker_id: str) -> set[str]:
    """The items a worker of the drive's work queue holds a claim on, which its lane's work verbs may name beside its own job."""
    from drive import work

    data = state.load(drive)
    return {job_id for job_id, job in data["jobs"].items() if work.claimed_by(data, job) == worker_id}


def lane_refusal(args: argparse.Namespace, parse: Callable[[list[str]], argparse.Namespace], drive: str, job_id: str) -> str | None:
    """Why a Sprite lane's drive command, as drive parsed it to run, may not: a `break-glass`, whose command would run on this Mac as its user, so the step moves to a lane on this Mac; a verb no builder runs, such as a tap or an order verb; a write, a `job report` keeping stdin or one of LANE_WRITES, naming a job other than its own or an item it holds as a worker of the queue in any value it parsed, while a read, such as another job's `job report`, `job replies` or `status`, reads it as a Mac lane does; a `job set` other than its `--repro`; a `pr set` other than its `--flaky`, or on a PR neither its own job nor an item it holds carries; or another drive. It reads the parsed values, so an abbreviated flag, a `--flag=value` and a word after `--` mean here what they mean to the verb."""
    words = tuple(word for word in (args.verb, getattr(args, "action", None)) if word)
    verb = next((verb for verb in LANE_VERBS if words[: len(verb)] == verb), None)
    refused = f"drive {' '.join(words)}: refused, nothing ran:"
    if words[:1] == ("break-glass",):
        return f"{refused} no Sprite lane runs break-glass, by {NO_SPRITE_GRANT}: return BLOCKED naming the step and its command, and the coordinator moves it to a lane on this Mac"
    if verb is None:
        return (
            f"{refused} a lane runs only these verbs: {', '.join(' '.join(verb) for verb in LANE_VERBS)}. When the step needs this Mac, a machine the repository grants or a Mac box, return BLOCKED naming it,"
            f' and the coordinator moves you to this Mac with `drive job launch {job_id} --local --mac "<the step>"`; for a proof only this Mac runs, return DONE with the proof pending, naming its file, and the coordinator runs `drive job prove {job_id}`'
        )
    parsed = {key: value for key, value in vars(args).items() if key != "run"}
    values = {word for value in parsed.values() for word in (value if isinstance(value, list | tuple) else [value]) if isinstance(word, str)}
    writes = verb in LANE_WRITES or verb == ("job", "report") and args.stdin
    if writes and (others := sorted({word for word in values if JOB_WORD.fullmatch(word)} - {job_id} - claimed(drive, job_id))):
        return f"{refused} it names {', '.join(others)}, and a lane's drive writes act on its own job, {job_id}, alone, or on the items it holds as a worker of the queue, while its reads read any job"
    if verb in LANE_FLAGS and (
        flags := sorted(
            f"--{key.replace('_', '-')}"
            for key, value in vars(parse([*verb, args.url if verb == ("pr", "set") else job_id])).items()
            if key in parsed and key not in (LANE_FLAGS[verb], "url", "json", "drive") and parsed[key] != value
        )
    ):
        return f"{refused} a lane's {' '.join(verb)} takes only --{LANE_FLAGS[verb]}, and it names {', '.join(flags)}"
    if verb == ("pr", "set") and (state.load(drive)["prs"].get(state.pr_key(args.url)) or {}).get("job") not in {job_id, *claimed(drive, job_id)}:
        return f"{refused} a lane's pr set acts on a PR its own job, {job_id}, or an item it holds as a worker of the queue carries, and {args.url} is none of them"
    if parsed.get("drive") not in (None, drive):
        return f"{refused} it names drive {parsed['drive']}, and a lane acts on its own drive, {drive}, alone"
    return None


def mac_steps(refusal: str, parser: argparse.ArgumentParser, job_id: str) -> str:
    """What a Sprite lane of the job does with a refusal naming drive commands no Sprite lane runs, as lane_refusal would refuse them by their verb, their LANE_FLAGS and the jobs a write names: return BLOCKED naming them, as a break-glass step goes, for the coordinator to run here; "" when it names none, prose such as "the drive tracks" naming no verb."""
    verbs = subverbs(parser)
    steps = []
    for named in NAMED_COMMAND.finditer(refusal):
        words = named[1].split()
        actions = subverbs(verbs[words[0]]) if words[0] in verbs else None
        if actions is None or actions and (len(words) < 2 or words[1] not in actions):
            continue
        verb = tuple(words[: 2 if actions else 1])
        lane = next((lane for lane in LANE_VERBS if verb[: len(lane)] == lane), None)
        writes = lane in LANE_WRITES or lane == ("job", "report") and "-" in words
        if (
            lane is None
            or verb in LANE_FLAGS and {word[2:] for word in words if word.startswith("--")} - {LANE_FLAGS[verb], "json"}
            or writes and {word for word in words if JOB_WORD.fullmatch(word)} - {job_id}
        ):
            steps.append(" ".join(["drive", *verb, *(word for word in words[len(verb) :] if word.startswith("--") or JOB_WORD.fullmatch(word))]))
    if not steps:
        return ""
    return f"No Sprite lane runs {', '.join(dict.fromkeys(steps))}: return BLOCKED naming each step and its command above, and the coordinator runs it on this Mac"


def subverbs(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    return next((action.choices for action in parser._actions if isinstance(action, argparse._SubParsersAction)), {})


def lane_exit(status: int, printed: str) -> str:
    return f"{DRIVE_EXIT} {status}\n{printed}"


def read_records(command: str, checkout: Path, drive: str) -> str:
    """Run one listed record read with its words as the lane wrote them, a search, grep or match joining several into one query itself, else read the drive files the read names; a ccn show, which takes one ID, runs once for each ID, its outputs joined, and a Slack read runs through this Mac's cc-slack and answers whole, since sprite_answer sends a long answer as a file on the lane's Sprite."""
    argv = read_words(command)
    verb = next((verb for verb in READ_VERBS if tuple(argv[: len(verb)]) == verb), None)
    if verb is None:
        return read_drive_files(command, drive)
    if verb in SLACK_READS:
        return run([slack.launcher(), *argv[1:]], checkout).strip()
    ids = list(itertools.takewhile(lambda word: not word.startswith("-"), argv[len(verb) :]))
    flags = argv[len(verb) + len(ids) :]
    runs = [[*verb, record, *flags] for record in ids] if verb[0] == "ccn" and verb[-1] == "show" and len(ids) > 1 else [argv]
    return head("\n\n".join(record_read(each, checkout) for each in runs))


def record_read(argv: list[str], checkout: Path) -> str:
    """A listed read's output, or its tool's refusal naming the words it ran as, quoted as a shell splits them, and, for each order or job those words name, the drive read that reads it, since orders and jobs are the drive's records and no cci kind."""
    try:
        return run(argv, checkout).strip()
    except ReadError as error:
        named = [word for word in argv if DRIVE_RECORD.fullmatch(word)]
        drive_read = f"; the drive keeps its orders and jobs, so drive status {' '.join(named)} on your Sprite reads them" if named else ""
        raise ReadError(f"{error}; it ran as these {len(argv)} words: {shlex.join(argv)}{drive_read}") from None


def read_drive_files(command: str, drive: str) -> str:
    """Each file of the drive's folder the read names whole under its heading, each folder it names as the list of files under it, each carried folder it names itself as carried_folders lists it, or saying the drive keeps none; a read naming none is refused with the reads that run."""
    from drive import orca

    if not (files := orca.drive_files(drive, command) | carried_folders(command, drive)):
        allowed = ", ".join(" ".join(verb) for verb in READ_VERBS)
        raise ReadError(
            f"only these reads run here: {allowed}; a file or folder of the drive's folder under {orca.CARRIED.replace('|', '/, ')}/ reads as READ <its path>,"
            " each of those folders itself, such as READ jobs/, lists what it holds, and a drive verb such as drive status or drive job report runs as that drive command on the Sprite"
        )
    return "\n\n".join(f"# {heading}\n\n{text if text is not None else 'the drive keeps no such file inside its folder'}" for heading, text in files.items())


def carried_folders(command: str, drive: str) -> dict[str, str | None]:
    """Each of the drive's carried folders a word of the read names itself, as `jobs`, `asks/` or its path on this Mac, absolute or from `~`, under its `Drive folder <name>/` heading, listing what it holds one level deep, a file by its path from the drive's folder and its size and a folder by that path and a `/`, which a READ of it lists at any depth; an entry resolving outside the drive's folder is left out, and a carried folder the drive keeps none of has no text."""
    from drive import orca

    folder = state.drive_dir(drive)
    inside = folder.resolve()
    listed: dict[str, str | None] = {}
    for word in read_words(command):
        named = folder / os.path.expanduser(orca.PATH_END.sub("", word))
        if named.parent != folder or named.name not in orca.CARRIED.split("|"):
            continue
        if not (named.is_dir() and named.resolve().is_relative_to(inside)):
            listed[f"Drive folder {named.name}/"] = None
            continue
        held = sorted(entry for entry in named.iterdir() if (entry.is_file() or entry.is_dir()) and entry.resolve().is_relative_to(inside))
        lines = [f"{entry.relative_to(folder)}/" if entry.is_dir() else f"{entry.relative_to(folder)} ({entry.stat().st_size:,} bytes)" for entry in held]
        listed[f"Drive folder {named.name}/"] = "\n".join(lines) or "an empty folder"
    return listed


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
        help="the stack the grant names, as <domain>/<env>, as the repository's access provider reads it",
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
        help="after --, the command to run holding only the credentials the grant mints, which end with the grant, and stopped with every process it started at the grant's end",
    )
    return options


def grant_reason(words: str) -> str:
    """The reason a grant posts beside its link in #releases, refused when blank, since a grant nobody can account for is the one break-glass forbids."""
    if not words.strip():
        raise argparse.ArgumentTypeError("is blank: say why, as #releases shows it beside the grant, such as: count rows, read only")
    return words.strip()


def held(granted: Grant) -> str:
    """What a command under the grant held, as its run says it."""
    return f"holding only the {granted.profile} profiles' credentials, which end at {clock(granted.ends.astimezone())}"


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


def end(group: subprocess.Popen, killed_by: float, stop: signal.Signals = signal.SIGTERM) -> tuple[str | None, str | None]:
    """End every process left in the command's group: `stop`, then SIGKILL once the command exits and closes the pipes it holds or the time.monotonic() clock reaches killed_by, and SIGKILL alone once it has; what the command wrote to those pipes, all of it, what an earlier communicate that timed out read included."""
    if killed_by > time.monotonic() and signal_group(group.pid, stop):
        with contextlib.suppress(subprocess.TimeoutExpired):
            group.communicate(timeout=killed_by - time.monotonic())
    signal_group(group.pid, signal.SIGKILL)
    return group.communicate()


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


def plan_sections(phrases: list[str], checkout: Path) -> list[dict]:
    """The sections of the cc-notes plans in the checkout whose headings or lines carry one of the phrases whole, in any case, plans best first, each with its plan, the headings above it and only the lines carrying one."""
    phrase = re.compile("|".join(rf"(?<!\w){re.escape(each)}(?!\w)" for each in phrases), re.IGNORECASE)
    plans = json.loads(run(["ccn", "plan", "search", "--limit", str(PLANS), "--json", "--", " ".join(phrases)], checkout))
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


def run(argv: list[str], checkout: Path, env: dict[str, str] | None = None, bounded: bool = True, stdin: str | None = None) -> str:
    named = " ".join([Path(argv[0]).name, *argv[1:3]])
    try:
        done = subprocess.run(
            argv,
            cwd=checkout,
            env=env,
            input=stdin,
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


def posting() -> dict[str, str]:
    """The environment every drive `cci post` runs in: this process's own with `CCI_PARENT_SESSION` set, which cci reads only for whether it is set, as a post made for another session, so it binds the session running the drive to no lane, whatever lane the post names, and never refuses it as the second binder of a lane a live session holds."""
    return os.environ | {"CCI_PARENT_SESSION": "drive"}


def withheld(text: str) -> str:
    """The text with each credential cci refuses to keep named in its place, by the shapes its store.CredentialShape reads, so a post carrying one goes up once rather than being refused unchanged on every pass."""
    for name, shape in CREDENTIALS:
        text = shape.sub(f"<{name} withheld>", text)
    return text
