import contextlib
import functools
import itertools
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from drive import github, limits, reads, settled, state
from drive.render import cut
from drive.shell import simple_commands

BRIEF_JOB = re.compile(r"^Drive:[^\n]*?\bjob (j\d+)\b", re.MULTILINE)
BRIEF_FILE = re.compile(r"(?<![\w.~-])~?/[^\s`'\"()<>,;]+\.md\b")
HANDOFF = re.compile(r"^Handoff:", re.MULTILINE)
SERVES_TOOLING = re.compile(r"^Serves:[ \t]*drive tooling\b", re.MULTILINE | re.IGNORECASE)
PLANNED = re.compile(r"^(Goal|Done when|Where|Runs as|Native|Interactions|Would he ask|Plan|Incident):[ \t]*(.+)$", re.MULTILINE)
RUNS_AS = re.compile(r"^Runs as:(?![ \t]*none\b)[ \t]*(.+)$", re.MULTILINE | re.IGNORECASE)
FROM_THIS_MAC = re.compile(r"\bbreak[- ]?glass\w*|\bmachine[- ]profile\b", re.IGNORECASE)
FIELD = re.compile(
    r"^(Order \(his words\)|From|Linked messages|Serves|Goal|Done when|Where|Runs as|Release|Release build|Mac|Handoff|Decisions that bind|Repo rules|Research|Researched|Answer"
    r"|Native|Interactions|Would he ask|Plan|Incident|Scratch|Earlier parts|Transcripts|Drive):",
    re.MULTILINE,
)
RESEARCHED = re.compile(r"^[ \t]*Researched:.*$", re.MULTILINE)
RESEARCHED_LINE = re.compile(r"^Researched:", re.MULTILINE)
RESEARCHED_BY = re.compile(r"^Researched:[ \t]*(j\d+)\b", re.MULTILINE)
REUSES = re.compile(r"^REUSES:[ \t]*\S", re.MULTILINE)
CHECK = re.compile(r"^CHECK:(.*)$", re.MULTILINE)
NATIVE = re.compile(r"^NATIVE:[ \t]*(.*)$", re.MULTILINE)
PROOF = re.compile(rf"{limits.PAGE_LINK.pattern}|(?<![\w.])/[^\s,;)]+")
SEEN_PROOF = re.compile(r"slack\.com/archives/|\.(?:mov|mp4|webm|gif|png|jpe?g)$", re.IGNORECASE)
SEEN = "a picture or recording of it, the live post's link its proof file reads back, or, for a command, the saved output of its run"
STORY_LINE = re.compile(r"(?P<kind>STORY|CRITERION|PASS|FAIL|GAP|SEEN) (?P<story>S\d+)(?: (?P<label>[^:]+?))?:(?P<rest>.*)")
STORY_KINDS = ("STORY", "CRITERION", "PASS", "FAIL", "GAP", "SEEN")
CRITERIA = ("first", "empty state", "error state")
UNMET = ("FAIL", "GAP")
STORY_LINES = (
    "each user story's lines, S1 on in the order its brief's Interactions: line names them: STORY Sn: <the story>; CRITERION Sn <label>: <what must hold>"
    " for each of its strict acceptance criteria, first, empty state and error state among the labels; PASS Sn <label>: <what the run saw> or"
    " FAIL Sn <label>: <why> for each criterion by its label; and SEEN Sn: <the picture or recording it saved, the live post's link it read back,"
    " or, for a command, the file holding the output of its run>, which opens; a story lacking any of these lines, or printing a FAIL or GAP line, falls short"
)
OPENED_AGE = 24 * 60 * 60
GITHUB_PAGES = (
    (re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)"), "repos/{0}/pulls/{1}"),
    (re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/commit/([0-9a-f]{7,40})"), "repos/{0}/commits/{1}"),
    (re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/actions/runs/(\d+)"), "repos/{0}/actions/runs/{1}"),
    (re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/releases/tag/([^/?#\s]+)"), "repos/{0}/releases/tags/{1}"),
)
NO_SURFACE = re.compile(r"\s*none\b", re.IGNORECASE)
COMMAND_SURFACE = re.compile(r"(?<!slash )\b(?:CLI|commands?)\b(?![ -](?:palette|menu|bar|k)\b)", re.IGNORECASE)
STORIES_RULING = (
    '"you\'re supposed to proactively view and click through every user story and have strict acceptance critera for yourself and'
    ' instad are just doing the bare minumum based on what I say"'
)
LEFT = re.compile(r"^(?:NEXT|UNPROVEN):.*$", re.MULTILINE)
STEPS = r"(?:runs?|re-?runs?|fills?|back-?fills?|migrates?|applies|apply|executes?|triggers?|kicks?\s+off|flips?|enables?|patch(?:es)?|repairs?)"
UNLANDED_STEPS = r"(?:redraws?|resumes?|closes?|pipes?|proves?)"
MODAL = r"(?:must\s+|should\s+|needs?\s+to\s+|has\s+to\s+|will\s+|then\s+)?"
MAC_LANE = r"(?:(?:a|the)\s+)?(?:Mac|local)\s+lane|(?:a\s+)?lane\s+on\s+(?:this|the)\s+Mac"
LEFT_FOR = re.compile(
    r"\b(?:until|unless|once|after|when)\s+(?:the\s+)?(?:coordinator|communicator|meta\s+session|handler|owner|someone|another\s+(?:lane|agent|job))\b"
    rf"|\b(?:until|unless|once|after|when)\s+(?:{MAC_LANE})\b"
    rf"|\b(?:coordinator|communicator|meta\s+session|handler|someone(?:\s+else)?)\s+{MODAL}{STEPS}\b"
    rf"|\blander\s+{MODAL}{UNLANDED_STEPS}\b"
    rf"|\b(?:{MAC_LANE})(?:\s+that\s+[^,;.]*?)?\s+{MODAL}(?:{STEPS}|{UNLANDED_STEPS}|posts?)\b",
    re.IGNORECASE,
)
RETURN_OPENING = re.compile(r"\bresult word\b|\b(?:open|opens|opening|start|starts|starting|begin|begins|beginning)\s+(?:with\s+|on\s+)?[\"“'`]", re.IGNORECASE)
INSTALLED = re.compile(r"\b(?:install|merg)\w*", re.IGNORECASE)
AFTER_WAY = (
    "a yasyf/* merge reaches no lane, so drive job prove runs each After install: command on this Mac once the job's proof file"
    " passes on the installed build, the newest install's drive first on PATH, and takes DONE only when each exits 0: each is a"
    " drive verb that exits nonzero when its step did not happen, such as drive cards redraw --waits <waits id> --post, drive job"
    " prove <j>, drive job resume <j> or drive order set <o>"
)
LIMIT = re.compile(r"\b(?:caps?|capped|quotas?|capacity|ceilings?|throttl\w*|rate[- ]limit\w*|limit\w*|max(?:imum)?)\b", re.IGNORECASE)
LEFT_STANDING = re.compile(r"\b(?:left\s+as[\s-]is|unchanged|hit(?:s|ting)?|exceed(?:s|ed|ing)?)\b", re.IGNORECASE)
STATEMENT = re.compile(r"(?:(?![.;:!?]+(?=\s|$)).)+")
CHOICE_OPENINGS = ("ASK", "DESIGN")
READY_OPENINGS = ("READY", "PUSHED")
DESIGN_PART = re.compile(r"DESIGN\s+\S")
KEYS = ("rulings", "exists", "open")
BLOCK_KEYS = (*KEYS, "build")
RESEARCHED_KINDS = ("builder", "design", "codex")
RESEARCH_KINDS = ("reader", "design")
FOLLOW_UP_RULING = '"start deisgnng the follow p right awya motherfucker dont wait"'
LANE_KINDS = ("reader", "builder", "design")
LIGHT_MODELS = ("sonnet", "haiku")
OPENINGS = ("DONE", "READY", "PUSHED", "BUILT", "WAITING", "ASK", "BLOCKED", "DESIGN", "HANDOFF")
WORDED_KINDS = ("builder", "design")
DESIGN_LINES = ("Root cause:", "Native:", "Change:", "Facts:")
CITED = re.compile(r"[\w.~-]*[/.][\w./-]*:\d+|https?://\S+")
OWNED = re.compile(r"\b(?:extended|reused|deleted) by change (\d+)\b", re.IGNORECASE)
SEARCHED = re.compile(r"\b(?:ccx|rg|grep|ccn|answer_search|search_plan)\b")
DUPLICATION_RULING = '"how _agents was allowed to get creted in the irst place sonce its a terieble duplicatgion and was clerarly not throught through"'
UNDESIGNED = ("HANDOFF", "BLOCKED")
BLOCK = "a Researched: line and under it rulings:, exists: and open: lines"
CHOICE_WORDS = r"\b(?:options?|choices?|choose|lost|picks?|decisions?|forks?)\b"
STOPS_ON_CHOICE = re.compile(rf"[^.;]*(?:(?<!never )\bBLOCKED\b[^.;]*{CHOICE_WORDS}|{CHOICE_WORDS}[^.;]*(?<!never )\bBLOCKED\b)[^.;]*")
STOPPED_RULING = '"fix ur tooling if it stopped on that it shoukd. have brought a design question card to me asap not just stopped int eh middle of the incident"'
RELEASE = re.compile(r"^Release:[ \t]*(.+)$", re.MULTILINE)
MAC = re.compile(r"^Mac:[ \t]*(.+)$", re.MULTILINE)
AFTER = re.compile(r"^After install:", re.MULTILINE)
CI_BINARIES = frozenset({"ci", "ci.dev"})
RELEASE_STARTS = ("start", "hotfix")
RELEASE_FOLLOWS = ("approve", "resume", "rollback", "rerun")
RECORD_SECONDS = 45
PRESENT_CUTS = ("start", "hotfix", "resume", "approve", "api")
HOLD_PASSES = ("approve", "api")
REHEARSAL_CLUSTERS = frozenset({state.STAGING_ENV, "plat-usw2-sandbox"})
RELEASE_VALUED = frozenset({"groups", "targets", "hotfix", "landed", "exclude", "only", "without", "env", "from", "order", "stack", "commit"})
RELEASE_ASIDE = frozenset({"json"})
REHEARSAL_SAID = '"You never have to ask for sandbox"'
UNSURE_READING = (
    "When Opus is unsure whether his words order it, or gave no reading, the call is the top coordinator's, never his: from its own"
    " session, read them yourself and, when they order it,"
)
READING = re.compile(r"\breading:", re.IGNORECASE)
RELEASE_READING = (
    f'{UNSURE_READING} start it with `drive job add --release "<its ci release start or hotfix>" --reading "<why his words order it, quoting them>"`,'
    " which its job keeps for the release's resume, approve, rerun and rollback; a confident no still goes to him"
)
RELEASE_APPROVAL = (
    "A release reaching any place but plat-usw2-staging and plat-usw2-sandbox starts only on words of his ordering it, whatever card or thread"
    " they answer, or on an incident's order, the answer labelled incident, whose words approve the incident's own cut. Name in `--order` the"
    " answer `drive answer` recorded for those words, or read its `tools/ci release plan` and ask him with `drive job ask` whether to start that"
    " exact command. "
    + RELEASE_READING
    + f". One on those two places alone starts with no --order and no card, by his words {REHEARSAL_SAID}"
)
RELEASE_HELD = (
    'refused: he holds every release reaching past plat-usw2-staging and plat-usw2-sandbox, by his words "{said}" ({where}), and only he grants an'
    " exception, one release at a time, so `{release}` runs only on an answer of his recorded since that approves it: {why}. Ask him with `drive job ask`"
    " whether to release it, record his answer with `drive answer`, and name it in the release's `--order`, which a job's resume,"
    " approve, rerun or rollback reads from its brief's `Release:` line; a release on those two places alone runs with none. "
    + RELEASE_READING
    + ". Once he says releases can go again, his answer saying so supersedes the hold: `ccn answer supersede {hold} --by <id>`"
)
UNCARRIED = (
    'refused: `--order {order}` names his answer to the card asking "{asked}", which names {named}, and {why}: start it from a commit'
    " holding each merged pull request that card names, such as `--from {newest}`, the newest of their merge commits, or name each in `--landed`"
)
JOB_HELD = (
    'refused: he holds every release, by his words "{said}" ({where}), and only he grants an exception, so the {cronjob} Job on {cluster},'
    " a production cluster, starts only on an answer of his recorded since that orders that Job there now: {why}. Start, unblock and rerun"
    " nothing of it: ask him with `drive job ask` whether to start it, record his answer with `drive answer`, and start the Job naming"
    f" that answer's id in its `--reason`, which its build keeps for its gate's unblock to read. {UNSURE_READING} start the Job with"
    ' that reading in its `--reason`: `tools/ci cluster-job ... --reason "<answer id> reading: <why his words order it, quoting them>"`;'
    " a confident no still goes to him"
)
CLAIMED_JOB = (
    "refused: {order} of drive {drive} claims staging until {until} in {link}, and the repository's staging-claim rule holds staging for"
    " the test each claim names until its thread says it is done, so the {cronjob} Job on {cluster} starts inside that window only for"
    " that claim's own test. Start, unblock and rerun nothing of it: return WAITING staging:<what you test>/{cluster}@<its commit>, which"
    " the drive holds behind that claim until it ends, then post your own claim as that rule says and start the Job once its thread"
    " stood 5 minutes with no objection"
)
RELEASE_NOTE_LABELS = ("release-order", "release")
NOTE_REFS = re.compile(r"#(\d+)\b|\b((?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40})\b")
NOTED = (
    'Jev reads release note {id}, "{title}", as speaking to {starts}. Nothing is refused: follow its words before you offer or'
    " start one, and `ccn note show {id}` prints them whole: {body}"
)
UNNOTED = (
    "the drive's release notes were not read against {starts} ({why}): read each note `ccn note list --label drive:{name}"
    " --label release-order` and `--label release` list before you offer or start one"
)
RELEASE_CLAIM = re.compile(r"\b(?:releases?|cut)\b[^.;\n]{0,60}?\bcan (?:start|go|run|ship)\b|\breleases? (?:start|go) (?:as usual|again)\b", re.IGNORECASE)
PULL_WORDS = re.compile(r"(?:#?\d+,?)+")
NAMES = re.compile(r"[,\s]+")
ABSTRACTION_RULING = '"we should buiold the abstraction first, make sure it works by imaginign, then implement the strategies"'


def brief_file(prompt: str) -> tuple[Path, str] | None:
    """The brief file an agent's prompt reads in, and its text: the first file a path on any of its lines names whose Drive line names the prompt's own job, or any job when the prompt names none, wherever it is kept, such as the one a `Read your brief first:` or `Brief:` line names or the Orca task brief a Sprite lane's first message names."""
    own = BRIEF_JOB.search(prompt)
    for named in BRIEF_FILE.findall(prompt):
        path = Path(named).expanduser()
        if os.path.isfile(path) and (job := BRIEF_JOB.search(text := path.read_text())) and (own is None or job[1] == own[1]):
            return path, text
    return None


def returned_mac(name: str, job_id: str) -> str | None:
    """The step only this Mac runs that a job's newest return holding a Mac: line names, by MAC, which places the jobs its PRs hand on: the prover install.add_prover adds at their merge and the tester cli.write_brief briefs on their staging test."""
    return next((step[1].strip() for taken in state.returns(name, job_id) if (step := MAC.search(taken["body"]))), None)


def carried(prompt: str) -> str:
    """An agent's prompt with its brief file, by brief_file, read in after it."""
    return f"{prompt}\n{read[1]}" if (read := brief_file(prompt)) else prompt


def fields(text: str) -> list[tuple[str, str]]:
    """Each field of a brief drive job brief wrote, its label and its text up to the next field's label."""
    starts = list(FIELD.finditer(text))
    ends = [found.start() for found in starts[1:]] + [len(text)]
    return [(found[1], text[found.end() : end]) for found, end in zip(starts, ends, strict=True)]


def release_flags(words: list[str]) -> dict[str, tuple[str, ...]] | None:
    """What a `ci release start` or `hotfix` ships and from where, as `ci release` parses its words, a hotfix's pull requests as its --hotfix and bare targets as --targets; None for any other command."""
    if Path(words[0]).name not in CI_BINARIES or words[1:2] != ["release"] or words[2:3] not in (["start"], ["hotfix"]):
        return None
    flags: dict[str, list[str]] = {}
    rest = iter(words[3:])
    for word in rest:
        name, given, value = word.removeprefix("--").partition("=")
        if not word.startswith("--"):
            name, value = "hotfix" if words[2] == "hotfix" and PULL_WORDS.fullmatch(word) else "targets", word
        elif not given:
            value = next(rest, "") if name in RELEASE_VALUED else "true"
        flags.setdefault("targets" if name == "groups" else name, []).extend(filter(None, NAMES.split(value.replace("#", ""))))
    return {name: tuple(sorted(values)) for name, values in flags.items() if name not in RELEASE_ASIDE}


def tested_release(release: str) -> str:
    """The production release a `ci release start --for-production` tests, the staging release go/ci starts on every staging env through every staging freeze, a teammate's included: the same start without --for-production and its --env, so it runs on production."""
    [words] = [words for words in simple_commands(release) if release_flags(words) is not None]
    kept, rest = [], iter(words)
    for word in rest:
        flag, given, _ = word.partition("=")
        if flag == "--env" and not given:
            next(rest, None)
        elif flag not in ("--env", f"--{settled.FOR_PRODUCTION}"):
            kept.append(word)
    return shlex.join(kept)


def started(command: str) -> list[dict[str, tuple[str, ...]]]:
    """The flags of each `ci release start` or `hotfix` a command runs."""
    return [flags for words in simple_commands(command) if (flags := release_flags(words)) is not None]


def card_releases(text: str) -> list[str]:
    """Each release a card's words name in backticks, one `ci release start` or `hotfix` each, the shorthand `release start …` its options write read as the ci binary's."""
    named = []
    for quoted in limits.BACKTICKED.findall(text):
        command = quoted.strip("`")
        command = f"ci {command}" if command.startswith("release ") else command
        try:
            if len(started(command)) == 1:
                named.append(command)
        except ValueError:
            continue
    return named


def release_refusal(command: str) -> str | None:
    """Why `drive job add --release` may not start a command: it is not one `ci release start` or `hotfix` alone, naming in --order the answer of his it starts on unless it is a rehearsal(), or it deploys `--local`, which runs in the shell that starts it and starts no build its job could follow."""
    commands = simple_commands(command)
    if len(commands) != 1 or (flags := release_flags(commands[0])) is None:
        return f"--release `{command}` is not one `tools/ci release start` or `tools/ci release hotfix` alone: name the one release to start, with its flags as it runs"
    if "local" in flags:
        return f"--release `{command}` deploys `--local`, whose plan and apply run in the shell that starts it and start no build its job could follow, so no job starts it: a `--manual` or regular start names the build its job follows"
    if not flags.get("order") and not rehearsal(command):
        return f"--release names no --order. {RELEASE_APPROVAL}"
    return None


def rehearsal(release: str) -> bool:
    """Whether a command starts releases only on REHEARSAL_CLUSTERS, each `ci release start` or `hotfix` it runs naming places, as settled.shipped() reads them, every one of them staging or sandbox, and none marked --for-production, which go/ci starts on every staging env through every staging freeze, a teammate's included, so only his exception for the production release it tests starts it, by settled.unordered(): such a release needs no word of his, by his words REHEARSAL_SAID (716e70d) on sandbox and 33de0fc and 23eefb9 on staging, so no release gate reads an answer of his for it and `drive job ask` keeps a card on it decided."""
    reads = [settled.shipped(flags) for flags in started(release)]
    return bool(reads) and all(settled.FOR_PRODUCTION not in read and (places := set(read.get("env", ())) - set(read.get("without", ()))) and places <= REHEARSAL_CLUSTERS for read in reads)


def approval_refusal(command: str, data: dict, reading: str | None = None) -> str | None:
    """Why the one release a command starts may not start on the answer of his its --order names: none for a rehearsal(); while he holds every release, hold_refusal() refuses it; else it is no incident's order, and `settled.unordered` reads it as ordering another release, or Opus is unsure or gave no answer and the top coordinator gave no reading of its own."""
    if rehearsal(command):
        return None
    cwd = data["roles"]["coordinator"]["cwd"]
    if hold := settled.hold(cwd):
        return hold_refusal(command, data, hold, reading)
    [flags] = started(command)
    answer_id = flags["order"][-1]
    try:
        answer = settled.shown(answer_id, cwd)
        if settled.INCIDENT in answer["tags"] or not (why := settled.unordered(answer, command, data)):
            return None
    except state.DriveError as error:
        return f"refused: `--order {answer_id}` names no answer of his that could be read: {error}. {RELEASE_APPROVAL}"
    except settled.Unheard as failure:
        if reading:
            return None
        return f"refused: Opus did not settle whether his answer {answer_id} approves this release ({failure}). {RELEASE_APPROVAL}"
    asked, said = cut(answer["title"], 200), cut(settled.his(answer["body"]), settled.QUOTED_CHARS)
    return f'refused: `--order {answer_id}` names his answer to "{asked}", "{said}", and {why}. {RELEASE_APPROVAL}'


def followed(words: list[str]) -> str | None:
    """The release a `ci release approve`, `resume`, `rollback` or `rerun` names, its first word past the verb that is no flag: the release's name or build."""
    if Path(words[0]).name not in CI_BINARIES or words[1:2] != ["release"] or words[2:3] not in ([verb] for verb in RELEASE_FOLLOWS):
        return None
    return next((word for word in words[3:] if not word.startswith("-")), None)


def release_record(release: str, cwd: str) -> dict | None:
    """The record go/ci keeps of a release, by `tools/ci release status <release> --json` in the drive's checkout: its build, the --order approving it, the commit and the targets the build carries, its picks and each target go/ci pulled in; None when go/ci reads none."""
    done = subprocess.run([str(Path(cwd) / "tools" / "ci"), "release", "status", release, "--json"], cwd=cwd, capture_output=True, text=True, timeout=RECORD_SECONDS)
    return json.loads(done.stdout)["record"] if done.returncode == 0 else None


def recorded_job(record: dict, data: dict) -> dict | None:
    """The job that started the release a record names: the one whose release build is the record's build, else the newest whose Release line names the record's --order and a --from its commit starts with, since a start's first answer can name the cut build before the release build it triggers."""
    number = (found := github.BUILD.match(record.get("build") or "")) and found["number"]
    cutting = [job for job in data["jobs"].values() if state.starts_cut(job)]
    if built := next((job for job in cutting if number and job["release_build"] and (kept := github.BUILD.match(job["release_build"])) and kept["number"] == number), None):
        return built
    ordered = [job for job in cutting for flags in started(job["brief_flags"]["release"]) if record.get("order") in flags.get("order", ()) and any(record.get("commit", "").startswith(given) for given in flags.get("from", ()))]
    return max(ordered, key=lambda job: state.parse_time(job["created"]), default=None)


def scope_refusal(release: str, record: dict, data: dict, said: str) -> str | None:
    """Why a production release of the whole platform may go on no further, by the targets its build carries as go/ci's record lists them, its picks and each target go/ci pulled in, never the flags it started with: it leaves out a target, by settled.unexcused, that said, the goal and brief of the job that started it, neither records as `<target> fails: <the failing test>` nor as `<target> left out by his <id>: "<his words>"` quoting the answer of his its --order names, his ruling on this release; none for a rehearsal() or a release his words name target by target, by his words settled.SCOPE_WORDS."""
    if rehearsal(release):
        return None
    cwd = data["roles"]["coordinator"]["cwd"]
    rulings = []
    if order := record.get("order"):
        with contextlib.suppress(state.DriveError):
            rulings.append({"answer": order, "words": settled.his(settled.shown(order, cwd)["body"])})
    if not (dropped := settled.unexcused({*(record.get("picks") or ()), *(record.get("targets") or ())}, cwd, said, rulings)):
        return None
    return (
        f"refused: release {record.get('name')} ({record.get('build')}) carries no {', '.join(dropped)}, and a release ships every target his latest ruling on it does not exclude, read fresh from his words,"
        f" never from an earlier plan or a reader's choice: start it again carrying each, or record in its job's goal `<target> fails: <the failing test>`, or"
        f' `<target> left out by his <its --order answer>: "<his words leaving it out>"` ({settled.SCOPE_WORDS})'
    )


def uncarried_refusal(command: str, data: dict) -> str | None:
    """Why the one release a command starts may not start from its commit: the card his `--order` answer answers, by settled.answered_card, names a merged PR of the drive's checkout's repository, in a link of its words or by link or #N on its `Holds:` line, that its `--landed` does not name, and its `--from` or `--commit` lacks that PR's merge commit, as contained() reads it once each commit the checkout lacks is fetched by its sha, or it names no commit, so go/ci picks one; refused naming the newest of those merge commits to pass as `--from`, and naming the read when GitHub or git could not read one. A hotfix or rollback, a rehearsal() naming no `--order`, a PR the card names that has not merged, and an answer no kept card holds pass."""
    from drive import github

    [flags] = map(settled.shipped, started(command))
    cwd = data["roles"]["coordinator"]["cwd"]
    if {"hotfix", "rollback"} & flags.keys() or "order" not in flags or not (asked := settled.answered_card(data, settled.shown(flags["order"][-1], cwd))):
        return None
    holds = asked.holds or ""
    linked = [state.pr_key(found["link"] or found[0]) for found in limits.PR_LINK.finditer("\n".join([asked.title, *asked.summary, asked.cause, *asked.options, *asked.hints.values(), *asked.show_me, holds]))]
    numbered = limits.PR_NUMBER.findall(holds)
    if not linked and not numbered:
        return None
    try:
        repo = github.checkout_repo(cwd)
        keys = [key for key in dict.fromkeys([*linked, *(f"{repo}#{number[1:]}" for number in numbered)]) if key.rpartition("#")[0].casefold() == repo.casefold()]
        reads, refused = github.fetch(keys) if keys else ({}, None)
        if unread := [f"#{key.rpartition('#')[2]}" for key in keys if key not in reads]:
            raise github.GithubError(f"GitHub did not read {', '.join(unread)}" + (f": {refused}" if refused else ""))
        landed = flags.get("landed", ())
        merges = {f"#{number}": pull["merged_sha"] for key, read in reads.items() if (pull := github.landing(read))["state"] == "merged" and (number := key.rpartition("#")[2]) not in landed}
        if not merges:
            return None
        for sha in merges.values():
            github.fetch_commit(cwd, sha)
        newest = github.run(["git", "-C", cwd, "log", "--no-walk", "--format=%H", *merges.values()]).split()[0]
        if commit := (flags.get("from") or (None,))[-1]:
            github.fetch_commit(cwd, commit)
            found = [contained(cwd, number, sha, commit) for number, sha in merges.items()]
            if all(held for held, _ in found):
                return None
            why = f"at `{commit}` it {'; '.join(said for held, said in found if not held)}"
        else:
            why = "this start names no `--from` or `--commit`, so go/ci picks the commit it ships, which may lack one"
    except github.GithubError as error:
        return f"refused: whether this release's commit holds each merged pull request the card his `--order` answer answers names could not be read: {error}; run it again"
    return UNCARRIED.format(order=flags["order"][-1], asked=cut(asked.title, 200), named=", ".join(merges), why=why, newest=newest)


def away_refusal(verb: str, release: str, data: dict, moment: datetime) -> str | None:
    """Why a production release may not start, resume or pass its approve hold now, by a release verb or a `ci bk api` write to its build such as unblocking that hold: he is not present, by state.absence, and it is dangerous by the root AGENTS.md's rule, as every approve hold is, since it holds a migration, delete or replace, and every cut but harmless() ones, since a cut of the default branch carries changes nobody read; a release naming only places off production, by settled.off_production(), such as a staging or sandbox step, a rollback, a rerun or any other verb runs."""
    if verb not in PRESENT_CUTS or not (away := state.absence(data, moment)):
        return None
    if (releases := started(release)) and all(settled.off_production(settled.shipped(flags), data["roles"]["coordinator"]["cwd"]) for flags in releases):
        return None
    if verb not in HOLD_PASSES and harmless(release, data):
        return None
    return (
        f"refused: {away}, and while he is not present a production release starts or resumes only as an incident's hotfix of pull requests"
        f" that change nothing dangerous, and passes no approve hold, by his words {state.PRESENCE_WORDS}: return WAITING his word with the release held; a rollback runs now"
    )


def harmless(release: str, data: dict) -> bool:
    """Whether a release may run while he is not present: one `ci release start` or `hotfix` of pull requests, which carries no other change, on an --order of his labelled incident, none of whose pull requests land.danger reads dangerous."""
    from drive import land

    named = started(release)
    if len(named) != 1 or not (pulls := named[0].get("hotfix")) or not (orders := named[0].get("order")):
        return False
    if settled.INCIDENT not in settled.shown(orders[-1], data["roles"]["coordinator"]["cwd"])["tags"]:
        return False
    return not any(land.danger(data, f"{state.MONOREPO}#{number}") for number in pulls)


def cluster_job_refusal(job: dict, data: dict, moment: datetime, top: bool = False) -> str | None:
    """Why a Job may not start now from a suspended CronJob, by `tools/ci cluster-job`, a `ci bk api` write creating the forge/release build it starts or writing to it, such as unblocking its gate, or a rerun of that build, each its cluster, CronJob and reason as the build's CLUSTER_JOB keeps them: claimed_job_refusal() inside another drive's staging claim, job_hold_refusal() while he holds every release, a reading in its reason counting only in the top coordinator's session, top, else away_job_refusal() while he is not present; DriveError when his hold could not be read."""
    if claimed := claimed_job_refusal(job, data["name"], moment):
        return claimed
    hold = settled.hold(data["roles"]["coordinator"]["cwd"])
    return hold and job_hold_refusal(job, data, hold, moment, top) or away_job_refusal(job["cluster"], data, moment)


def claimed_job_refusal(job: dict, name: str, moment: datetime) -> str | None:
    """Why a Job may not start on state.STAGING_ENV now: another drive on this Mac holds staging by a claim whose end is still to come, the first of state.others_claims(), latest end first, as stall.behind() reads every claim as staging's and the freeze its repository's staging-claim rule posts beside it."""
    if job["cluster"] != state.STAGING_ENV or not (claims := state.others_claims(name, moment)):
        return None
    claim = claims[0]
    return CLAIMED_JOB.format(drive=claim["drive"], order=claim["order"], until=claim["until"], link=claim["link"], cronjob=job["cronJob"], cluster=job["cluster"])


def job_hold_refusal(job: dict, data: dict, hold: dict, moment: datetime, top: bool = False) -> str | None:
    """Why a Job may not start on a production cluster, any but REHEARSAL_CLUSTERS, under his hold on every release, as hold_refusal() refuses a release: no answer of his recorded after the hold, read by settled.job_unordered(), orders that CronJob on that cluster at this moment, or his answers could not be read, or Opus is unsure or gave no reading and the top coordinator's session, top, put no reading of its own in the Job's reason."""
    if job["cluster"] in REHEARSAL_CLUSTERS:
        return None
    try:
        if not (why := settled.job_unordered(job, hold, data, moment)):
            return None
    except state.DriveError as error:
        why = f"his answers could not be read: {error}"
    except settled.Unheard as failure:
        if top and READING.search(job["reason"]):
            return None
        why = f"Opus did not settle whether his answers since order it ({failure})"
    return JOB_HELD.format(said=hold_words(hold), where=hold_where(hold, data), cronjob=job["cronJob"], cluster=job["cluster"], why=why)


def away_job_refusal(cluster: str, data: dict, moment: datetime) -> str | None:
    """Why a Job may not start now from a suspended CronJob on this cluster, by `tools/ci cluster-job`, a `ci bk api` write creating the forge/release build it starts or writing to it, such as unblocking its gate, or a rerun of that build: he is not present, by state.absence, and the cluster is a production one, any but REHEARSAL_CLUSTERS, where every such Job is a pass, backfill, repair or delete of a team's data that the root AGENTS.md calls dangerous, an incident's repair included."""
    if cluster in REHEARSAL_CLUSTERS or not (away := state.absence(data, moment)):
        return None
    return (
        f"refused: {away}, and a Job on {cluster}, a production cluster, writes a team's data, so it starts only while he is present, an incident's repair included,"
        f" by his words {state.PRESENCE_WORDS}: return WAITING his word with the Job unstarted; the watch pages him for an open incident"
    )


def hold_refusal(release: str, data: dict, hold: dict, reading: str | None = None) -> str | None:
    """Why a release may not run under his hold on every release reaching past REHEARSAL_CLUSTERS: it is no rehearsal() and not a `ci release start` or `hotfix` naming one --order, his exception for that one release, recorded after the hold that `settled.unordered` reads as ordering it, never a tap on a PR card, nor one whose Opus read is unsure or failed given the top coordinator's reading; an incident's order passes on no label, the hold included. A release a job keeps a pass for since the hold, by kept_pass(), runs on it unread, so its resume, approve, rerun and rollback never read his words again."""
    if rehearsal(release) or kept_pass(release, data, hold):
        return None
    cwd = data["roles"]["coordinator"]["cwd"]
    named = started(release)
    orders = named[0].get("order") if len(named) == 1 else None
    try:
        if not orders:
            why = "it names no `--order`, and neither does a `Release:` line of its job's brief"
        elif (answer := settled.shown(orders[-1], cwd))["created_at"] <= hold["created_at"]:
            why = f'`--order {orders[-1]}` names his answer to "{cut(answer["title"], 200)}", recorded before the hold'
        elif not (unordered := settled.unordered(answer, release, data)):
            return None
        else:
            why = f'`--order {orders[-1]}` names his answer to "{cut(answer["title"], 200)}", and {unordered}'
    except state.DriveError as error:
        why = f"his answers could not be read: {error}"
    except settled.Unheard as failure:
        if reading:
            return None
        why = f"Opus did not settle whether his answer {orders[-1]} approves it ({failure})"
    return RELEASE_HELD.format(said=hold_words(hold), where=hold_where(hold, data), hold=hold["id"][:7], release=release, why=why)


def kept_pass(release: str, data: dict, hold: dict) -> bool:
    """Whether a job keeps a pass for this very release, Opus's or the top coordinator's reading, from `drive job add --release` since his hold."""
    return any((kept := job["release_pass"]) and kept["release"] == release and state.parse_time(kept["at"]) > state.parse_time(hold["created_at"]) for job in data["jobs"].values())


def hold_where(hold: dict, data: dict) -> str:
    """Where his hold on every release stands, for a refusal to quote: its id and the link the drive kept for it."""
    return ", ".join(filter(None, [hold["id"][:7], (data["answers"].get(hold["id"][:7]) or {}).get("link")]))


def hold_words(hold: dict) -> str:
    """His words in his hold on every release, cut to settled.QUOTED_CHARS."""
    return cut(settled.his(hold["body"]), settled.QUOTED_CHARS)


def release_notes(releases: list[str], data: dict) -> list[str]:
    """What the drive's release notes, by kept_notes(), say of these releases, each a `ci release start` or `hotfix`, by his ruling 17f763e that the tooling only helps the agent follow the prose rules: Jev reads each release against each note in its range, by note_facts(), in SPEAKS_TO_START Nouls, in as many requests as settled.batched packs them into, so no note goes unread however many the drive keeps and none on work the release does not ship crowds the agent's read, and each note Jev does not read as silent on one, its unsure band included since a note missed is a cut it forbids offered or started, comes back as one NOTED line naming each release it speaks to with what git says of the PRs and commits its title names, and the note's words whole, so the agent offering or starting it follows them; one UNNOTED line when cc-notes or Jev gave no reading, or naming each note too large for any request alone. It refuses nothing."""
    from drive import github

    if not releases:
        return []
    named = ", ".join(f"`{release}`" for release in releases)
    try:
        notes = kept_notes(data)
    except state.DriveError as error:
        return [UNNOTED.format(starts=named, why=error, name=data["name"])]
    if not notes:
        return []
    try:
        facts = note_facts(set(releases), {note["id"]: note["title"] for note in notes}, data["roles"]["coordinator"]["cwd"])
    except github.GithubError as error:
        facts = dict.fromkeys(itertools.product(releases, [note["id"] for note in notes]), f" (git read no commit it cuts: {error})")
    starts = {f"s{number}": release for number, release in enumerate(releases, 1)}
    ranged = [note for note in notes if any((release, note["id"]) in facts for release in releases)]
    slots = {note["id"]: f"n{number}" for number, note in enumerate(ranged, 1)}
    items = [
        (
            {"notes": {slots[note["id"]]: {"id": note["id"][:7], "title": note["title"], "body": note["body"]}}},
            {f"speaks.{slots[note['id']]}.{start}": settled.asking(settled.SPEAKS_TO_START, note=slots[note["id"]], start=start) for start, release in starts.items() if (release, note["id"]) in facts},
        )
        for note in ranged
    ]
    try:
        read = settled.jev_all(settled.batched({"starts": starts, "notes": {}}, {}, items))
    except settled.Unheard as failure:
        return [UNNOTED.format(starts=named, why=f"Jev gave no reading: {failure}", name=data["name"])]
    left = [note["id"][:7] for note, (_, asked) in zip(ranged, items) if not asked.keys() <= read.keys()]
    skipped = [UNNOTED.format(starts=named, why="no room in a Jev request for " + ", ".join(left), name=data["name"])] if left else []
    speaking: dict[str, list[str]] = {}
    for note in ranged:
        for start, release in starts.items():
            if (key := f"speaks.{slots[note['id']]}.{start}") in read and settled.reading(read[key]) is not False:
                speaking.setdefault(note["id"], []).append(release)
    return skipped + [
        NOTED.format(id=note["id"][:7], title=note["title"], starts="; ".join(f"`{release}`{facts[release, note['id']]}" for release in speaking[note["id"]]), body=note["body"].strip())
        for note in ranged
        if note["id"] in speaking
    ]


def kept_notes(data: dict) -> list[dict]:
    """The drive's release notes: each cc-notes note labelled drive:<drive> and release-order or release that no later note superseded, with its title and body."""
    found: dict[str, dict] = {}
    for label in RELEASE_NOTE_LABELS:
        listed = settled.ccn(
            ["ccn", "note", "list", "--label", f"drive:{data['name']}", "--label", label, "--limit", "0", "--full", "--json"],
            "",
            data["roles"]["coordinator"]["cwd"],
            f"could not read the drive's notes labelled {label}",
        )
        found |= {note["id"]: note for note in json.loads(listed)}
    return list(found.values())


def note_facts(releases: set[str], titles: dict[str, str], cwd: str) -> dict[tuple[str, str], str]:
    """What git says, by release and note id, of each PR and commit a note's title names, for each note in the release's range: a note naming none; for a hotfix, a note naming a PR it hotfixes or that PR's merge commit; for a start, a note naming a PR or commit the commit it cuts holds, or one git or GitHub cannot place, at its --from or --commit, else its repository's default branch head, which one fetch brings into the drive's checkout, a PR at the merge commit `github.landing` reads for it in one `github.fetch`. GithubError when the checkout's repository or default branch head could not be read."""
    from drive import github

    refs = {note_id: NOTE_REFS.findall(title) for note_id, title in titles.items()}
    if not any(refs.values()):
        return dict.fromkeys(itertools.product(releases, titles), "")
    flagged = {release: settled.shipped(started(release)[0]) for release in releases}
    repo = github.checkout_repo(cwd)
    branch = github.default_branch(repo)
    head = github.fetched(cwd, branch)
    keys = sorted({f"{repo}#{number}" for found in refs.values() for number, _ in found if number} | {f"{repo}#{number}" for flags in flagged.values() for number in flags.get("hotfix", ())})
    reads, refused = github.fetch(keys) if keys else ({}, None)
    landed = {key: github.landing(read) for key, read in reads.items()}
    facts = {}
    for (release, flags), note_id in itertools.product(flagged.items(), titles):
        if not refs[note_id]:
            facts[release, note_id] = ""
            continue
        if "hotfix" in flags:
            merges = [pull["merged_sha"] for number in flags["hotfix"] if (pull := landed.get(f"{repo}#{number}")) and pull["merged_sha"]]
            if hotfixed := [f"#{number}" if number else sha for number, sha in refs[note_id] if number in flags["hotfix"] or sha and any(merge.startswith(sha) for merge in merges)]:
                facts[release, note_id] = f" (it hotfixes {', '.join(hotfixed)})"
            continue
        given = (flags.get("from") or (None,))[-1]
        commit = given or head
        found = []
        for number, sha in refs[note_id]:
            pull = landed.get(f"{repo}#{number}") if number else None
            if sha:
                found.append(contained(cwd, sha, sha, commit))
            elif pull is None:
                found.append((None, f"GitHub did not read #{number}" + (f": {refused}" if refused else "")))
            elif pull["state"] != "merged":
                found.append((False, f"lacks #{number}, which is {pull['state']}" + (f" into {pull['into']}" if pull["into"] else "")))
            else:
                found.append(contained(cwd, f"#{number}", pull["merged_sha"], commit))
        where = given or f"{branch}'s head {head[:12]}"
        if any(held is not False for held, _ in found):
            facts[release, note_id] = f" (at {where}: {'; '.join(said for _, said in found)})"
    return facts


def contained(cwd: str, named: str, sha: str, commit: str) -> tuple[bool | None, str]:
    """Whether commit holds sha, as `git merge-base --is-ancestor` answers in the drive's checkout, None when git cannot place it, beside what that says of named, its PR or the sha itself."""
    shown = named if named == sha else f"{named} ({sha[:12]})"
    ran = subprocess.run(["git", "-C", cwd, "merge-base", "--is-ancestor", sha, commit], capture_output=True, text=True, check=False)
    if ran.returncode in (0, 1):
        return ran.returncode == 0, f"{'holds' if ran.returncode == 0 else 'lacks'} {shown}"
    return None, f"git cannot place {shown}: {' '.join(ran.stderr.split())}"


def freezes(name: str, data: dict) -> list[dict]:
    """Each release freeze standing now, as `tools/ci release freezes --json` reads the release ledger from the drive's checkout at the head of its default branch: its ledger row under freeze and its line under text."""
    from drive import orca

    checkout = orca.checkout(name, data["roles"]["coordinator"]["cwd"])
    return json.loads(reads.run([str(checkout / "tools" / "ci"), "release", "freezes", "--json"], checkout))


def standing(name: str, data: dict) -> list[tuple[str | None, str]]:
    """Each release freeze the release ledger holds standing now and his hold on every release reaching past REHEARSAL_CLUSTERS, by id with its line, read at each use and never kept, so no brief or wait states a freeze that lifted or misses one set since, and no post says a release may start past one; a ledger or a hold that could not be read, as cc-notes reads none in a coordinator folder no git repository holds, stands under no id, which no words name."""
    try:
        held = [(hold["id"][:7], f'his hold {hold["id"][:7]} on every release reaching past plat-usw2-staging and plat-usw2-sandbox stands: "{hold_words(hold)}"')] if (hold := settled.hold(data["roles"]["coordinator"]["cwd"])) else []
    except state.DriveError as error:
        held = [(None, f"his hold on every release could not be read ({error}), so read `ccn answer list --label {settled.RELEASE_HOLD}` before saying a release may start")]
    try:
        return [*((each["freeze"]["id"], each["text"]) for each in freezes(name, data)), *held]
    except (reads.ReadError, state.DriveError) as error:
        return [(None, f"the release ledger could not be read ({error}), so read `tools/ci release freezes` before saying a release is frozen or may start"), *held]


def releases(name: str, data: dict) -> str:
    """What stands on releases now, as the one line a job's brief, a handler's prompt and drive status carry."""
    return "; ".join(line for _, line in standing(name, data)) or "no release freeze or release hold of his stands"


def claim_refusal(text: str, name: str, data: dict) -> str | None:
    """Why words saying a release may start may not go out: they leave out, by its id, a freeze or his hold standing now; None for words that say no release may start, such as a staging test saying the freeze or hold it laid on staging ended, which starts nothing, or for words naming each that stands."""
    if not RELEASE_CLAIM.search(text):
        return None
    if unnamed := [line for stands, line in standing(name, data) if stands is None or stands not in text]:
        return f"these words say a release may start, and these stand now: {'; '.join(unnamed)}. Name each by its id and what it holds, or leave the claim out"
    return None


def gives_tooling(job_id: str, order_id: str) -> str:
    """The verb that gives an order to the meta session: a brief whose builder serves drive tooling, which only the meta session starts."""
    return f'`drive job brief {job_id} --serves "drive tooling"` gives {order_id} to the meta session'


def block_lines(text: str) -> list[str]:
    """The lines of the Researched: block a reader or design returned under the first Researched: line, at any indentation and with any words after it, that has such lines under it, bulleted or not, up to the first line that is none of rulings, exists, open and build."""
    for found in RESEARCHED.finditer(text):
        taken = []
        for line in text[found.end() :].lstrip("\n").splitlines():
            key, colon, _ = line.strip().removeprefix("- ").partition(":")
            if not colon or key.strip().casefold() not in BLOCK_KEYS:
                break
            taken.append(line.strip())
        if taken:
            return taken
    return []


def researched(text: str) -> dict[str, str]:
    """The Researched: block a reader or design returned: its rulings, exists, open and build lines, by key."""
    block = {}
    for line in block_lines(text):
        key, _, value = line.removeprefix("- ").partition(":")
        block[key.strip().casefold()] = value.strip()
    return block


def filled(block: dict[str, str]) -> bool:
    return all(block.get(key) for key in KEYS)


def researches(name: str, data: dict, order_id: str, parts: list[str], besides: str | None = None) -> Iterator[tuple[str, str]]:
    """Each job readers names for these parts of the order but the job besides names, with its kept report: each reader or design whose report carries a filled Researched: block, newest first, then each builder whose return listed one as a change, that return as it stands, so a builder of a change a builder left unbuilt starts on that return when nothing researched the work it came from."""
    found = [(job_id, text) for job_id in reversed(readers(data, order_id, parts)) if job_id != besides and (filled(researched(text := state.report_text(name, job_id))) or data["jobs"][job_id]["kind"] not in RESEARCH_KINDS)]
    yield from sorted(found, key=lambda pair: not filled(researched(pair[1])))


def research(name: str, data: dict, order_id: str, parts: list[str], besides: str | None = None) -> tuple[str, str] | None:
    """The newest reader or design of these parts of the order but the job besides names, whose kept report carries a filled Researched: block, with that report."""
    return next(researches(name, data, order_id, parts, besides), None)


def research_links(data: dict, job: dict, text: str) -> list[str]:
    """The readers and designs a job was built on, beside the one this brief names on its `Researched:` line, by built_on."""
    return built_on(data, job, RESEARCHED_BY.findall(text))


def built_on(data: dict, job: dict, named: list[str]) -> list[str]:
    """The readers and designs a job was built on: each its briefs so far named on their `Researched:` line, each of these, and each job of the drive one of these was built on, so a reader's open questions hold the builders of the design built on it, by state.holds_work."""
    return list(dict.fromkeys([*job["researched"], *named, *(linked for job_id in named if job_id in data["jobs"] for linked in data["jobs"][job_id]["researched"])]))


def bound(name: str, data: dict, job: dict) -> list[str]:
    """The ids of his recorded answers binding the job, newest first, by spec: none a newer answer of his replaces, and none of his orders or words heard with no answer recorded."""
    return [entry["id"] for entry in unreplaced(spec(name, data, job)) if entry["id"] and entry["id"] not in data["orders"]]


def binding(name: str, data: dict, job: dict) -> tuple[list[str], list[str], list[dict]]:
    """What bound reads before it asks cc-notes which ids keep an answer, so a watch pass reads it with no subprocess: the ids the job's brief's rulings and the settled questions of its feature's readers and designs name, the ids their rulings: lines cite with each answer `drive answer` kept on the feature by on_feature, so a PR's check is judged again once he gives one, and his words on each question of theirs he answered with no answer recorded, with the question, when and where."""
    from drive import served

    orders = state.job_feature(data, job)
    jobs = state.orders_jobs(data, orders)
    found = list({reader_id: text for order_id in orders for reader_id, text in researches(name, data, order_id, list(data["orders"][order_id]["parts"]))}.items())
    closed = [(question["title"], answer) for reader_id, _ in found for question in data["jobs"][reader_id]["questions"] if (answer := question["closed"]) and answer["how"] != "withdrawn"]
    cited = list(dict.fromkeys([*(answer_id for _, text in found for kind, answer_id in served.named(researched(text).get("rulings", "")) if kind == "answer"), *(answer_id for answer_id, answer in data["answers"].items() if on_feature(data, answer, orders, jobs))]))
    ruled = [*((job.get("brief_flags") or {}).get("ruling") or []), *(answer["ruling"] for _, answer in closed if answer["ruling"])]
    heard = {answer["words"]: {"id": None, "asked": title or "his answer", "said": answer["words"], "at": answer["at"], "link": answer["link"], "by": []} for title, answer in closed if not answer["ruling"] and answer["words"]}
    return ruled, cited, list(heard.values())


def spec(name: str, data: dict, job: dict) -> list[dict]:
    """His words on the job's feature, newest first, the one list every brief's Decisions that bind and drive job ask's judge read beside the words of the job's own orders: each answer of his binding names, each one `drive answer` kept whose marks or heard jobs name an order of the feature or a job on one, each standing ruling of his on the whole project or workstream an order of the feature is filed under, by settled.area_rulings, his words on each question of its readers and designs he answered with no answer recorded, and each other order of the feature, each whole with when and where he gave it. An answer cc-notes marks superseded keeps its place with the newer answers replacing it under `by`, so no brief or judge reads it as standing; an id a brief's rulings name that cc-notes keeps no answer under refuses the brief, any other is left out."""
    orders = state.job_feature(data, job)
    ruled, cited, heard = binding(name, data, job)
    areal = [ruling["id"] for ruling in settled.area_rulings(data, orders)]
    cwd = data["roles"]["coordinator"]["cwd"]
    found: dict[str, dict] = {}
    for answer_id in dict.fromkeys([*ruled, *cited, *areal]):
        try:
            answer = settled.shown(answer_id, cwd)
        except state.DriveError:
            if answer_id in ruled:
                raise
            continue
        found.setdefault(
            answer["id"][:7],
            {
                "id": answer["id"][:7],
                "asked": answer["title"],
                "said": settled.his(answer["body"]),
                "at": answer["created_at"],
                "link": settled.given_at(data, answer),
                "by": [newer[:7] for newer in answer.get("superseded_by") or []],
            },
        )
    ordered = [{"id": order_id, "asked": "his order", "said": data["orders"][order_id]["text"], "at": data["orders"][order_id]["created"], "link": None, "by": []} for order_id in orders if order_id not in {job["order"], *job["carries"]}]
    return sorted([*found.values(), *heard, *ordered], key=lambda entry: state.parse_time(entry["at"]), reverse=True)


def on_feature(data: dict, answer: dict, orders: list[str], jobs: dict[str, dict]) -> bool:
    """Whether an answer `drive answer` kept speaks to this feature: a card, order, part or PR it marks or lists is of one of its orders or jobs, or a job it told is on one."""
    marked = {key.partition(":")[0] for key in answer["cards"]} | {mark["job"] for mark in answer["cards"].values() if mark and mark.get("job")}
    waits = {found[2] for key in answer["cards"] if (found := state.WAITS_ID.fullmatch(key))}
    named = {data["prs"][key]["order"] for key in answer["cards"] if key in data["prs"]}
    return bool((marked | named) & set(orders) or (marked | waits | set(answer["jobs"])) & jobs.keys())


def unreplaced(words: list[dict]) -> list[dict]:
    """The entries of his words no newer answer of his replaces."""
    return [entry for entry in words if not entry["by"]]


def spec_lines(words: list[dict]) -> list[str]:
    """His words as a brief's Decisions that bind lists them, newest first: each with its question, id or link and when, his words whole beneath, and one replaced by newer answers named by them alone."""
    return [
        f"{entry['asked']} ({entry['id'] or entry['link']}, {entry['at']})"
        + (f": replaced by his newer answer {', '.join(entry['by'])}" if entry["by"] else f"\n{textwrap.indent(entry['said'], '  ')}")
        for entry in words
    ]


def judging(words: list[dict]) -> list[dict]:
    """His standing words as a judge's items, each keyed by its answer or order id, or else by its place for words he gave with none recorded, with when he gave them."""
    return [{"id": entry["id"] or f"heard{number}", "asked": entry["asked"], "said": entry["said"], "link": entry["link"], "at": entry["at"]} for number, entry in enumerate(unreplaced(words), 1)]


def readers(data: dict, order_id: str, parts: list[str]) -> list[str]:
    """The order's jobs, oldest first, whose kept report can research these parts: each job whose return listed one as a change, a design's DESIGN or a builder's return by state.left_changes, the parts that job worked on counting as these, and so on up to the first work, and each reader or design that read or designed one of those parts, on the order or carrying it, so a listed change starts on its lister's return as a reader's part starts on the reader's."""
    order = data["orders"][order_id]
    read, listers = set(parts), set()
    while listed := {held["design"] for part, held in order["parts"].items() if part in read and held["design"]} - listers:
        listers |= listed
        read |= {part for lister in listed for part in data["jobs"][lister]["parts"]}
    return [job_id for job_id in order["jobs"] if job_id in listers or (job := data["jobs"][job_id])["kind"] in RESEARCH_KINDS and read & set(state.job_parts(job, order_id))]


def unstarted_builders(data: dict, reader_id: str) -> list[str]:
    """Each builder of a part this reader reads, on its order or one it carries, that no agent or lane took and that opened no PR, so the brief it starts on can still change to name the newest research of its parts."""
    reader = data["jobs"][reader_id]
    return [
        job_id
        for order_id in [reader["order"], *reader["carries"]]
        for job_id in data["orders"][order_id]["jobs"]
        if (job := data["jobs"][job_id])["kind"] == "builder" and job["order"] == order_id and state.untaken(job) and reader_id in readers(data, order_id, job["parts"])
    ]


def name_goals(name: str, data: dict, builder_ids: list[str], report: str) -> None:
    """Give each of these builders whose goal its parts or an earlier reader's build: line named the goal this reader's build: line names, so its brief's Goal: names the change the newest research of its parts recommends, while a goal its coordinator or a design gave it stays."""
    if not (goal := build_goal(report)):
        return
    for job_id in builder_ids:
        job = data["jobs"][job_id]
        if job["goal"] in {state.parts_goal(job["parts"]), *(build_goal(text) for _, text in researches(name, data, job["order"], job["parts"]))}:
            job["goal"] = goal


def build_goal(report: str) -> str | None:
    return f"build {build}" if (build := researched(report).get("build")) and not state.NO_CHANGE.match(build) else None


def waits_on_research(order: dict, kind: str, parts: list[str]) -> bool:
    """Whether a job of this kind on these parts starts only on its reader's research: a build order's builder, designer or codex lane, but on an incident, on the immediate fix, on the page the drive adds when he names a goal or asks where work stands, on a `staging test of #N before it lands` part, testing a built PR, or as a follow-up's design, each of which starts at once."""
    return (
        order["kind"] == "build"
        and not order["incident"]
        and kind in RESEARCHED_KINDS
        and not set(state.AT_ONCE) & set(parts)
        and not any(state.STAGING_PART.fullmatch(part) for part in parts)
        and not (kind == "design" and state.FOLLOW_UP in parts)
    )


def job_refusal(data: dict, order_id: str, kind: str, parts: list[str], lands: bool = False, waits: list[str] | None = None) -> str | None:
    """Why a job of this kind may not be added on the order: an order he limited to options gets no builder or codex job until he picks, and an answer order is one reader's. A job of any kind may wait on his card, a drive PR or a release, which check_waits reads, and on any job still at work, a reader that just started included, which state.job_wait_refusal reads, ending once that job registers a PR, by state.change_pr. A build order's research is read once, when the job's agent starts, by brief_refusal, so a builder proving a merged PR live, retrying its proof or rebasing it is added like any other. The immediate fix of a bug hurting someone now, a red default branch's fix-forward among them, a builder landing PRs he approved, one starting a release, one marked --builds-nothing, an open PR's builder added on the branch it pushed and the page the drive adds when he names a goal or asks where work stands start at once past the options-only refusal, by his words: "always do the immediate fix (or rollback if prudent) in parallel to giving me options for the proper fix". A follow-up he orders to a PR gets its design job at once, doing its own research and waiting on nothing, beside landing that PR, by his words "start deisgnng the follow p right awya motherfucker dont wait"."""
    order = data["orders"][order_id]
    if order["options_only"] and kind in ("builder", "codex") and not (set(state.AT_ONCE) & set(parts) or lands):
        return (
            f"{order_id} is limited to options: he picks one before the proper fix is built, so no {kind} starts on it; start a reader or a designer"
            f" until he picks, drive job add design --order {order_id}, and once he picks, drive order set {order_id} --no-options-only; the immediate"
            f' fix of a bug hurting someone now, or a red default branch\'s fix-forward, starts at once on its own "{state.IMMEDIATE_FIX}" part, added'
            f' with drive order set {order_id} --add-part "{state.IMMEDIATE_FIX}", a builder landing PRs he approved with --lands <each PR>,'
            " and an open PR's builder with --branch <the branch it pushed>"
        )
    if order["kind"] == "answer" and kind != "reader":
        return (
            f"{order_id} is an answer order: one reader researches it and answers him, with no {kind} beside it; work its"
            f" answer finds to build is a build order: drive order set {order_id} --kind build"
        )
    if kind == "design" and state.FOLLOW_UP in parts and waits:
        return (
            f'--waits {", ".join(waits)}: a "{state.FOLLOW_UP}" design starts now, beside landing the PR it follows, never once that PR'
            f" lands or a reader returns, by his words {FOLLOW_UP_RULING}: add it with no --waits, and its designer does its own research"
        )
    return None


def removed_parts(data: dict, order_id: str, parts: list[str]) -> dict[str, str]:
    """Why no job starts on each of these parts that a standing ruling of his on the whole project or workstream the order is filed under removes, as Jev reads it by settled.removed, so work his ruling on an area stopped never starts on an order outside the feature it was said on."""
    rulings = settled.area_rulings(data, [order_id]) if parts else []
    return {part: settled.REMOVED.format(id=ruling["id"], area=ruling["area"], said=cut(ruling["said"], 200), part=part) for part, ruling in (settled.removed(parts, rulings) if rulings else {}).items()}


def part_flags(parts: list[str]) -> str:
    return " ".join(f'--part "{part}"' for part in parts)


def carried_research(order_id: str, parts: list[str]) -> str:
    """The drive job set that makes another order's reader or design whose kept report already holds the block research of these parts, by readers, with no second reader restating it: it carries them."""
    return (
        f"a reader or design of another order that already kept that block on this work is this part's research once it carries the part, with no second reader:"
        f" drive job set <that reader or design> --carries {order_id} {part_flags(parts)}"
    )


def unresearched(name: str, data: dict, order_id: str, kind: str, parts: list[str]) -> str | None:
    """Why a build order's builder, designer or codex lane on these parts has no research to start on: no reader or design of its part, on its order or carrying the part from another, kept the Researched: block, and no design or builder listed it as a change, whose return is that change's research, by researches, with the reader to add; none on an order drive order pilot runs with no reader, by state.readers_piloted."""
    if not waits_on_research(data["orders"][order_id], kind, parts) or state.readers_piloted(data, data["orders"][order_id]) or research(name, data, order_id, parts):
        return None
    return (
        f"{order_id} is a build order, so its {kind} starts only once an Opus reader or a design of its part, a change a design listed counting"
        f" as that design's part, has kept {BLOCK} in its report: drive job add reader --order {order_id} {part_flags(parts)}"
        ' --goal "research: <what>", then a long-running:reader whose brief asks for that block; its rulings line holds his recorded answers'
        " on the order's words and area, its exists line what already does part of it, and its open line each choice between readings of his words"
    )


def brief_refusal(name: str, data: dict, job_id: str, brief: str) -> str | None:
    """Why this brief may not start its job's agent: its Runs as line, outside his double-quoted words and unless it opens none, proposes break-glass or the machine profile, which run on this Mac, for a write of a team's data, which runs only as a service account in the team's cluster; the lines its writer plans the job with ask him for a sign-in, Touch ID or cc-sudo on an AWS action this Mac's machine profile runs; a drive tooling brief aside from both, whose lines name the routes and sign-ins the drive's checks refuse, but whose builder needs an After install: line for a step its Goal names after the install, by after_install; an incident's codex lane or builder needs its kind's rule on a choice, by incident_refusal; a build order's builder, designer or codex lane needs the Researched: line naming a reader or design of its own part, on its order or carrying the part from another, the one check of that research, since drive job add adds such a job at once, its refusal naming the reader to add when none kept it, unless its brief carries a Handoff: line, as a builder taking over a PR the drive tracks does, it ships the immediate fix or builds the page the drive adds, builds nothing as a --builds-nothing builder, tests a built PR on a `staging test of #N before it lands` part, starts a release, builds on the branch an open PR already pushed, or designs a follow-up he ordered, which starts at once doing its own research, an answer order's one lane does its own research, and a build order drive order pilot runs with no reader, by state.readers_piloted, has its lane research in its own session, a Researched: line it names anyway still checked."""
    tooling = SERVES_TOOLING.search(brief)
    runs = None if tooling else RUNS_AS.search(brief)
    if runs and (route := limits.proposed(FROM_THIS_MAC, limits.SAID.sub(lambda said: limits.MASK * len(said[0]), runs[1]))):
        return (
            f'{job_id}\'s Runs as line writes a team\'s data under "{route[0]}", which runs on this Mac, and'
            f" {limits.TEAM_DATA_RULE}: a pass or hand repair {limits.CLUSTER_JOB}, and a write no CronJob makes {limits.UNMADE};"
            f' write the line again with `drive job brief {job_id} --runs-as "<service account, how it starts>"`'
        )
    if not tooling and (asked := limits.sign_in_problems([(f"its {found[1]} line", found[2]) for found in PLANNED.finditer(brief)])):
        return f"{job_id}'s brief plans a sign-in of his: {asked[0]}"
    job = data["jobs"][job_id]
    if tooling and job["kind"] == "builder" and not (HANDOFF.search(brief) or AFTER.search(brief) or RELEASE.search(brief)) and (unplaced := after_install(job_id, brief)):
        return unplaced
    order_id = job["order"]
    order = data["orders"][order_id]
    if order["incident"] and (rule := state.INCIDENT_CHOICE.get(job["kind"])):
        return incident_refusal(job_id, order_id, brief, rule)
    if HANDOFF.search(brief) or order["incident"] or set(state.AT_ONCE) & set(job["parts"]):
        return None
    if job["kind"] == "design" and state.FOLLOW_UP in job["parts"]:
        return None
    if order["kind"] == "answer":
        if RESEARCHED_LINE.search(brief):
            return (
                f"{job_id} answers {order_id}, an answer order: its one lane does its own research, reading code,"
                " transcripts, records and his answers itself, so its brief carries no Researched: line"
            )
        return None
    if not waits_on_research(order, job["kind"], job["parts"]) or job.get("builds_nothing") or state.starts_cut(job) or job["kind"] == "builder" and job["branch"]:
        return None
    named = RESEARCHED_BY.search(brief)
    if named is None and state.readers_piloted(data, order):
        return None
    if named is None:
        missing = unresearched(name, data, order_id, job["kind"], job["parts"])
        return (
            f"{job_id} builds on {order_id}, a build order: its brief carries the line Researched: <its reader's or design's job>,"
            " naming the reader or design whose kept report holds the order's rulings, what exists and its open questions, which"
            f" drive job brief {job_id} writes once one has kept it"
            + (f"; none has: {missing}, and drive job set {job_id} --waits <the reader> starts it on the reader's FREE line" if missing else "")
            + f"; {carried_research(order_id, job['parts'])}. A builder taking over a PR the drive tracks, such as one proving a merged PR live,"
            " retrying its proof or rebasing it, starts on a Handoff: brief, which needs no reader"
        )
    if named[1] not in (found for found, _ in researches(name, data, order_id, job["parts"], job_id)):
        return f"Researched: {named[1]} names no reader or design of {job_id}'s part of {order_id} that kept {BLOCK} in its report; {carried_research(order_id, job['parts'])}"
    return None


def after_install(job_id: str, brief: str) -> str | None:
    """Why a drive tooling builder's brief with no After install: or Release: line may not start it: Jev reads its Goal, once it speaks of an install or a merge, as naming a step to take once its change is installed beyond proving it, at settled.YES or more, a step no lane takes after a yasyf/* merge, so drive job prove runs it from that line; a reading in the band between settled.NO and settled.YES, or none, lets it out, said on stderr."""
    goal = " ".join(dict(fields(brief)).get("Goal", "").split())
    if not INSTALLED.search(goal):
        return None
    try:
        after = settled.reading(settled.jev({"goal": goal}, {"after": settled.AFTER_INSTALL})["after"])
    except settled.Unheard as failure:
        print(f"drive: Jev did not read {job_id}'s Goal for a step after the install, so its brief goes out unchecked: {failure}", file=sys.stderr)
        return None
    if after is None:
        print(f'drive: Jev is unsure whether {job_id}\'s Goal "{cut(goal, 160)}" names a step to take once its change is installed, so its brief goes out: name each with drive job brief {job_id} --after "<drive command>" when it does', file=sys.stderr)
    if not after:
        return None
    return (
        f'{job_id}\'s Goal "{cut(goal, 160)}" names a step to take once its change is installed, as Jev reads it, and no After install: line declares it:'
        f' {AFTER_WAY}. Name each with drive job brief {job_id} --after "<drive command>"'
    )


def unworded_return(kind: str, lines: dict[str, str | None]) -> str | None:
    """Why a builder's or designer's Goal or Done when may not be kept: Jev reads a line of them that speaks of a return's opening as ordering one that opens with no result word, at settled.YES or more, which the one_line_result stop hook sends back and drive job report refuses, so the job could never keep it, as six staging test briefs ending on "ready for the freeze:" could not; a reading in the band between settled.NO and settled.YES, or none, lets it out, said on stderr."""
    if kind not in WORDED_KINDS:
        return None
    for flag, line in lines.items():
        if not (line and RETURN_OPENING.search(line)):
            continue
        try:
            unworded = settled.reading(settled.jev({"line": line}, {"unworded": settled.UNWORDED_RETURN})["unworded"])
        except settled.Unheard as failure:
            print(f"drive: Jev did not read {flag} for a return opening with no result word, so it is kept unchecked: {failure}", file=sys.stderr)
            continue
        if unworded is None:
            print(f'drive: Jev is unsure whether {flag} "{cut(line, 160)}" orders a return opening with no result word, so it is kept', file=sys.stderr)
        if unworded:
            return (
                f'its {flag} "{cut(line, 160)}" orders a return opening with no result word, as Jev reads it, which the one_line_result stop hook'
                f" sends back and drive job report refuses, so the job could never keep it: a {kind}'s return opens with {', '.join(OPENINGS[:-1])} or {OPENINGS[-1]},"
                f" and a pause is a WAITING naming what frees it, a staging test's staging:<target>/<env>@<commit> for its soak commit; give {flag} again so it does"
            )
    return None


def incident_refusal(job_id: str, order_id: str, brief: str, rule: str) -> str | None:
    """Why an incident lane's brief may not start it: a choice never stops an incident, so its brief carries the rule its kind follows on one, word for word, and no line of it orders a BLOCKED return on a loss or a choice."""
    if rule not in " ".join(brief.split()):
        return f'{job_id} works on {order_id}, an incident, which a choice never stops, by his words {STOPPED_RULING}: its brief\'s Incident: line carries, word for word, "{rule}"'
    if stops := next((found for line in brief.splitlines() if (found := STOPS_ON_CHOICE.search(" ".join(line.split())))), None):
        return f'{job_id}\'s brief stops it on a choice, "{cut(stops[0].strip(), 160)}", and an incident never stops on one, by his words {STOPPED_RULING}: drop that line, since its Incident: line says what it does instead'
    return None


def heavier(data: dict, job_id: str, brief: str, model: str | None) -> bool:
    """Whether a Sonnet or Haiku passed for the job's lane gives way to its agent definition's own model: a build order's reader, builder and designer run on Opus or Fable, and so does an answer order's lane when the communicator marked the order complex."""
    job = data["jobs"][job_id]
    order = data["orders"][job["order"]]
    if HANDOFF.search(brief) or order["incident"] or job["kind"] not in LANE_KINDS or not any(word in (model or "") for word in LIGHT_MODELS):
        return False
    if order["kind"] != "answer":
        return True
    return bool(order["complex"])


def report_refusal(name: str, data: dict, job_id: str, text: str) -> str | None:
    """Why a job's return may not be kept: a builder's or designer's opens with a result word, a report naming a limit in its own words as a finding left standing is an ASK or DESIGN, a design lane's is the design in its shape, building all his words name and the abstraction several strategies share before any of them, by narrowed_design, a research reader's carries its Researched: block, a reader's DESIGN is a choice for him with its options, starting from the root cause and recommending the option that removes it, a DONE, READY or PUSHED leaves no step for someone else, nor a builder's a piece of its plan to a follow-up that no UNBUILT: line of it names, by left_piece, a builder's UNBUILT: lines naming their pieces as a design's Change: lines must and none naming work the drive already holds, by held_piece, a builder's or codex job's DONE on a staging test part carries a CASE line for each case, a design's DESIGN of several keeping the words of each change it listed before that a live builder carries, by state.renamed_change, a builder's DONE names its proof, and a picture, recording or live post when he sees the result, a DONE whose brief names user stories refused, since only drive job prove proves them from what its proof file prints, a repair keeping a proof file told to prove it with drive job prove instead, and a builder's READY or PUSHED names PRs whose tap card draws, saying how his words are met on each reworked to them, and says how it builds on what its research found."""
    job = data["jobs"][job_id]
    order = data["orders"][job["order"]]
    lines = [line.strip() for line in text.strip().splitlines()]
    opening = re.match(r"[A-Z]*", lines[0])[0]
    if job["kind"] in WORDED_KINDS and opening not in OPENINGS:
        return (
            f'its first line opens "{cut(lines[0], 60)}", and a {job["kind"]}\'s report opens with {", ".join(OPENINGS[:-1])}'
            f" or {OPENINGS[-1]}, the word that sets the job's state; start it with the one that says where the job is"
        )
    if opening not in CHOICE_OPENINGS and (finding := limit_finding(lines)):
        if order["text"].startswith(state.TOOLING):
            return (
                f'it reports a limit as a finding left standing: "{finding}". On a drive tooling order the job decides it, by his ruling'
                f" {state.TOOLING_DETAILS_RULING}: build the option that removes it and report that, or return ASK or DESIGN with two to four"
                " options, one recommended, which drive job ask decides with no card unless its options differ in what he gets"
            )
        return (
            f'it reports a limit as a finding left standing: "{finding}". A limit or capacity finding is his'
            " choice, so return ASK or DESIGN with two to four options for it, one recommended, in place of this report"
        )
    if job["kind"] == "design" and opening not in UNDESIGNED and (missing := design_missing(lines)):
        return (
            "a design lane's report is the design in its shape, which the coordinator reads for its forks, and this one"
            f" lacks {', '.join(missing)}: DESIGN <the part, in a few words>, then Root cause: (the structure that lets the"
            " defect happen, or none: <why no defect is behind it>), its Researched: block, which its builders start on, with"
            " rulings:, exists: and open: under it, Native:, a Change: line per PR its builders open and Facts:"
        )
    if job["kind"] == "design" and opening == "DESIGN" and (unowned := unowned_exists(name, data, job_id, text)):
        return unowned
    listed = state.change_lines(job["kind"], text) if job["kind"] == "builder" or job["kind"] == "design" and opening == "DESIGN" else []
    word = "UNBUILT:" if job["kind"] == "builder" else "Change:"
    if unkeyed := unquotable_change(listed):
        return (
            f'its {word} line names its change as "{cut(unkeyed, 120)}": a change is named in at most {state.PART_CHARS} characters'
            " with no backtick, double quote, $ or backslash, since those words become the order's part, which the coordinator"
            f' types as drive job add builder --part "<the change>"; its detail goes on {"the Design: line" if job["kind"] == "design" else "its NEXT: or UNPROVEN: line"}'
        )
    if stray := unlisted_waits(listed):
        return (
            f'its {word} line "{cut(stray, 80)}" waits on a change its list does not hold: a change waits only on earlier changes'
            f" of its list, as (waits on change <its number, counting {word} lines from 1>) or (waits on change 1 and change 2)"
        )
    if job["kind"] == "design" and len(listed) > 1 and (renamed := state.renamed_change(name, data, job["order"], [state.whole_order(line["change"]) for line in listed], job_id)):
        return renamed
    if job["kind"] == "builder" and (held := held_piece(name, data, job_id, listed)):
        return (
            f'its UNBUILT: line "{cut(held[0], 120)}" names work the drive already holds, {held[1]}, so a part of its own would build it'
            " a second time: an UNBUILT: line names only a piece of its plan nothing on the drive builds yet, never its own change, which"
            " CHANGE: says, nor another job's, so drop that line"
        )
    if job["kind"] == "design" and opening == "DESIGN" and (narrowed := narrowed_design(name, data, job, text)):
        return narrowed
    if job["kind"] == "reader" and (refused := reader_refusal(data, job_id, job, order, text, lines)):
        return refused
    if opening in ("DONE", *READY_OPENINGS) and (left := left_steps(text)):
        return (
            f'a {opening} leaves a step for someone else: "{cut(left, 120)}". Do that step yourself before you return,'
            " or return ASK when it needs his choice"
        )
    if job["kind"] == "builder" and opening in ("DONE", *READY_OPENINGS) and (piece := left_piece(data, job, text)):
        return (
            f'a {opening} leaves a piece of its plan to a follow-up, as Jev reads "{cut(piece, 120)}": name each such piece on an UNBUILT:'
            f" line of this return, in at most {state.PART_CHARS} characters with no backtick, double quote, $ or backslash, which keeps"
            " it as a part of the order with a builder of its own, or build it before you return, or return ASK when leaving it out needs his choice"
        )
    if job["kind"] in state.CHANGE_KINDS and opening == "DONE" and (staging := state.staging_parts(data, job)) and not state.CASE_LINE.search(text):
        return (
            f'{job_id} holds "{staging[0][1]}", so its DONE names each case of the staging test on a line of its own, CASE <case>: <verdict>,'
            " pass or what it found instead, such as fail: <what>, skipped or not exercised: <why>; only a DONE whose CASE lines for a PR read pass, one or more, and each other pass or not exercised delivers the part whose PR it names,"
            " and a DONE testing several PRs in turn heads each PR's CASE lines with a CHECK: line naming that PR and the build that put it on staging, or names that PR on each of its CASE lines, as CASE #N <case>: <verdict>"
        )
    if job["kind"] in state.CHANGE_KINDS and opening == "DONE" and staging and state.passing(state.case_lines(text)) and not any(state.tests_named(text, part) for _, part in staging):
        return (
            f'{job_id} holds "{staging[0][1]}", and its DONE names no PR its staging test tested, so it delivers none of its staging test parts:'
            " name each PR the passing test tested, by link or #N, and leave out each it did not test, which waits on a rerun"
        )
    if job["kind"] == "builder" and opening == "DONE":
        from drive import install

        if (refused := unproven(text, job_id, job)) and install.own_proof(data, job_id) and not install.proof_gap(data, job_id):
            return (
                f"{refused}; {job_id} opened no PR and keeps a proof file, so run drive job prove {job_id}, which runs it and takes DONE or BLOCKED"
                f" itself, or on a WAIT keeps {job_id} waiting until he is next heard, then end the turn on the first line drive job report {job_id} prints"
            )
        return refused
    if job["kind"] == "builder" and opening in READY_OPENINGS and not state.REPORT_CHANGE.search(text):
        if reworked := [key for key in state.named_prs(data, job["order"], lines[0]) if data["prs"][key]["rework"]]:
            return f"{', '.join(reworked)}: being reworked to his words, so its {opening} says in a CHANGE: line how they are met, which its fresh card shows him"
    if not (job["kind"] == "builder" and opening in READY_OPENINGS and order["kind"] == "build") or order["incident"]:
        return None
    found = research(name, data, job["order"], job["parts"])
    exists = researched(found[1]).get("exists", "none") if found else "none"
    if settled.none_only(exists) or REUSES.search(text):
        return None
    return (
        f"{found[0]}'s research found what already does part of this: {exists}. A {opening} says, in a REUSES: line, how the"
        " PR calls or extends it rather than adding a second copy beside it"
    )


def overruled_refusal(data: dict, job: dict, text: str) -> str | None:
    """Why a reader's or design's return may not be kept: a line of its Researched: block but exists:, a Change: line or a line of a list says no ruling of his covers, puts up for change or asks him again an item a recorded answer of his orders or keeps, as settled.overruled reads it, naming that answer's id and words, a line saying only none, as the reader's rules ask an empty block line to, going unread; kept with a warning on stderr when Jev gives no reading, and a reader's DESIGN is read as his card by drive job ask instead, whose finding refuses nothing, by his pick 320b7a7."""
    if job["kind"] not in RESEARCH_KINDS or job["kind"] == "reader" and text.lstrip().startswith("DESIGN"):
        return None
    lines = [line for line in block_lines(text) if not line.removeprefix("- ").casefold().startswith("exists:")]
    lines += [found[0].strip() for found in state.change_lines(job["kind"], text)] + settled.list_lines(text)
    try:
        picks = settled.overruled(list(dict.fromkeys(lines)), data)
    except settled.Unheard as failure:
        print(f"drive job report: {settled.UNREAD_LINES.format(failure=failure)}", file=sys.stderr)
        return None
    if not picks:
        return None
    return (
        f"as Jev reads it, {settled.overruling(picks)}. His words stand until he lifts them: keep each item as his answer orders,"
        f" citing its id as standing, {settled.LIFTS}, or put it to him as a card asking him to lift it"
    )


def reader_refusal(data: dict, job_id: str, job: dict, order: dict, text: str, lines: list[str]) -> str | None:
    """Why a reader's return may not be kept: research for a build order carries its filled Researched: block, and a DESIGN is a choice for him of two to four options, one recommended, starting from the root cause its evidence finds and offering the option that removes it, and on a bug hurting someone now names the immediate fix shipping while he picks."""
    block = researched(text)
    if order["kind"] == "build" and not order["incident"] and job["goal"].casefold().startswith("research") and (
        missing := [f"{key}:" for key in BLOCK_KEYS if not block.get(key)]
    ):
        return (
            f"{job_id} researches a build order, so its report carries {BLOCK}, each filled or none, which its builder's"
            f" brief names, and a build: line naming in one line the change its builder makes, its Goal:; it lacks {', '.join(missing)}"
        )
    if not lines[0].startswith("DESIGN"):
        return None
    inside = set(block_lines(text))
    offered = [line.removeprefix("- ").strip() for line in lines if line.startswith("- ") and line not in inside]
    marked = [option for option in offered if limits.RECOMMENDED.search(option)]
    if not 2 <= len(offered) <= 4 or len(marked) != 1:
        return (
            "a reader's DESIGN return is a choice for him, kept as his card as it stands: two to four `- `"
            " option lines, exactly one ending (recommended); a finding with no choice in it returns DONE"
        )
    options = [limits.RECOMMENDED.sub("", option) for option in offered]
    if problems := limits.root_cause_problems(text, options):
        return f"a reader's DESIGN names its simplest option, starts from the root cause and names the option that removes it: {'; '.join(problems)}"
    if state.hurts_now(data, order) and (problems := limits.interim_fix_problems(text)):
        return f"a reader's DESIGN on a bug hurting someone now names its immediate fix: {problems[0]}"
    return None


def design_missing(lines: list[str]) -> list[str]:
    missing = [] if DESIGN_PART.match(lines[0]) else ["a first line DESIGN <the part>"]
    unresearched = [] if filled(researched("\n".join(lines))) else [BLOCK]
    return missing + unresearched + [f"{'an' if label[0] in 'AEIOU' else 'a'} {label} line" for label in DESIGN_LINES if not any(line.startswith(label) for line in lines)]


def unowned_exists(name: str, data: dict, job_id: str, text: str) -> str | None:
    """Why a design's exists: line does not show what already does or holds what it touches, so it would add a second owner of one fact beside the first: an entry citing no path:line or link, or not naming the change of its list that extends, reuses or deletes it; or none, naming no search that found nothing, or while another reader or design of its part found something."""
    listed = researched(text)["exists"]
    shape = (
        "a design names on its exists: line, before it adds any table, column, store, file, process, mechanism, check or verb, each thing that"
        " already does or holds what it touches, one entry each separated by ;, citing its path:line or link and ending"
        " extended by change N, reused by change N or deleted by change N, N counting its Change: lines from 1, so one thing"
        f' owns each fact; or none: <each search that found nothing, such as ccx code search "<words>">. His words: {DUPLICATION_RULING}'
    )
    if listed.casefold().startswith("none"):
        found = research(name, data, data["jobs"][job_id]["order"], data["jobs"][job_id]["parts"], job_id)
        exists = researched(found[1]).get("exists", "none") if found else "none"
        if found and not exists.casefold().startswith("none"):
            return f'its exists: line says none, but {found[0]}\'s research found what already does part of this: "{cut(exists, 200)}"; {shape}'
        return None if SEARCHED.search(listed) else f'its exists: line "{cut(listed, 120)}" names no search that found nothing; {shape}'
    changes = len(state.CHANGE_LINE.findall(text))
    entries = [entry.strip() for entry in listed.split(";") if entry.strip()]
    unowned = next((entry for entry in entries if not CITED.search(entry) or not (owned := OWNED.findall(entry)) or any(not 1 <= int(n) <= changes for n in owned)), None)
    return unowned and f'its exists: entry "{cut(unowned, 120)}" does not cite what exists and which change of its list extends, reuses or deletes it; {shape}'


def unquotable_change(listed: list[re.Match]) -> str | None:
    """The first change these lines of a return, by state.change_lines, name in words that cannot be its part's name, by state.unquotable_part."""
    names = (" ".join(line["change"].split()) for line in listed)
    return next((name for name in names if state.unquotable_part(name)), None)


def unlisted_waits(listed: list[re.Match]) -> str | None:
    """The first of these lines of a return, by state.change_lines, waiting on a change that is not an earlier one of the list."""
    return next((line[0] for number, line in enumerate(listed, start=1) if any(not 1 <= wait < number for wait in state.change_waits(line))), None)


def narrowed_design(name: str, data: dict, job: dict, text: str) -> str | None:
    """Why a design may not be kept, as Jev reads it whole against his words on its order and each order its job carries, his spec on its feature, by spec, and his answer to each of its cards, all newest first, beside the forks it asks him, in as many requests as settled.batched needs to read every word of his whole beside the whole design by sentence, its rulings: line beside it as `rulings`, never a sentence of it, since it quotes his words rather than builds anything: it leaves out, defers or narrows what one names with no fork asking him, or keeps, reuses or builds on what one set aside, at settled.YES or more, at the sentence flagged picks beside his words given after it; or, where Jev reads several strategies doing one job at settled.YES or more, its first change is not the abstraction they share, under settled.NO. By his pick "Refuse a design only when Jev read all of it, quoting the sentence it flags; larger designs keep with a warning", a design past one Jev request of settled.REQUEST_CHARS // 2 characters is kept unread, and a reading in the band between, a pick of no sentence, or none, keeps it too, each said on stderr."""
    words = sorted(
        [
            *(word for order_id in [job["order"], *job["carries"]] for word in settled.order_words(data, order_id)),
            *judging(spec(name, data, job)),
            *(
                {"id": f"q{number}", "asked": question["title"], "said": closed["words"], "at": closed["at"]}
                for number, question in enumerate(job["questions"], 1)
                if (closed := question["closed"]) and closed["how"] == "answered" and closed["words"]
            ),
        ],
        key=lambda word: state.parse_time(word["at"]),
        reverse=True,
    )
    ruled = {line for line in block_lines(text) if line.removeprefix("- ").casefold().startswith("rulings:")}
    sentences = {f"s{number}": sentence for number, sentence in enumerate((sentence for line in text.splitlines() if line.strip() not in ruled for sentence in limits.SENTENCE_BREAK.split(line) if sentence.strip()), 1)}
    design = {"forks": [question["title"] for question in job["questions"] if not question["closed"]], "rulings": "\n".join(sorted(ruled)), "design": sentences}
    if settled.size(design, {}) > settled.REQUEST_CHARS // 2:
        print(f"drive job report: Jev read none of this design, since its {len(sentences)} sentences run past one Jev request of {settled.REQUEST_CHARS // 2:,} characters, so it is kept unchecked", file=sys.stderr)
        return None
    slots = {word["id"]: f"w{number}" for number, word in enumerate(words, 1)}
    weighs = (
        ({"words": {slots[word["id"]]: {"asked": word["asked"], "said": word["said"]}}}, {f"{question}.{word['id']}": settled.asking(noul, slot=slots[word["id"]]) for question, noul in (("narrows", settled.NARROWS), ("against", settled.CONTRADICTS))})
        for word in words
    )
    items = {word["id"]: word for word in words}
    try:
        read = settled.jev_all(settled.batched(design | {"words": {}}, {"strategies": settled.STRATEGIES, "first": settled.ABSTRACTION_FIRST}, weighs))
        flags = sorted(((question, word_id, sure) for key, sure in read.items() for question, _, word_id in [key.partition(".")] if question in ("narrows", "against") and sure >= settled.YES), key=lambda found: (found[0] != "narrows", -found[2]))
        refused = next(filter(None, (flagged(design, question, items[word_id], words) for question, word_id, _ in flags)), None)
    except settled.Unheard as failure:
        print(f"drive job report: Jev did not read the design against his words, so it is kept unchecked: {failure}", file=sys.stderr)
        return None
    if refused:
        return refused
    for key, sure in read.items():
        question, _, word_id = key.partition(".")
        if question in ("narrows", "against") and settled.reading(sure) is None:
            print(f'drive job report: Jev is unsure whether this design {"builds less than his words" if question == "narrows" else "goes against his words"} ({items[word_id]["asked"]}), so it is kept', file=sys.stderr)
    strategies, first = settled.reading(read["strategies"]), settled.reading(read["first"])
    if strategies and first is False:
        return (
            "Jev reads this design as building a strategy before the one abstraction its strategies share: design that abstraction first,"
            " walk every strategy through it on paper with a worked example of each operation, make it change 1 and each strategy a later"
            f" Change: line waiting on it, by his words {ABSTRACTION_RULING}"
        )
    if strategies is not False and first is not True:
        print("drive job report: Jev is unsure whether this design builds a strategy before the abstraction its strategies share, so it is kept", file=sys.stderr)
    return None


def flagged(design: dict, question: str, word: dict, words: list[dict]) -> str | None:
    """The refusal of a design Jev reads as building less than his words name, or going against them, quoting the sentence of it Jev picks as doing so beside every word of his given after them, newest first, since the newer wins where two collide, and the fix; None when it picks none or his newer words run past one request beside it, said on stderr, since a refusal quoting no sentence leaves the designer nothing to fix."""
    pick = settled.NARROWED_AT if question == "narrows" else settled.CONTRADICTED_AT
    sentences = design["design"]
    named = f", {word['id']}" if limits.ANSWER_ID.fullmatch(word["id"]) else ""
    said = f'his words "{cut(word["said"].strip('"“”'), settled.QUOTED_CHARS)}" ({word["asked"]}{named})'
    reads = f"Jev reads this design as {'building less than' if question == 'narrows' else 'going against'} {said}"
    newer = [other for other in words if state.parse_time(other["at"]) > state.parse_time(word["at"])]
    request, asked, left = settled.fill(
        design | {"words": {"asked": word["asked"], "said": word["said"]}, "newer": {}},
        {"sentence": pick._replace(options=pick.options | dict.fromkeys(sentences))},
        (({"newer": {f"n{number}": {"asked": other["asked"], "said": other["said"]}}}, {}) for number, other in enumerate(newer, 1)),
    )
    if left:
        print(f"drive job report: {reads}, but {len(left)} of his {len(newer)} words given after them run past one Jev request beside it, so it is kept", file=sys.stderr)
        return None
    if not (sentence := sentences.get(settled.jev(request, asked)["sentence"])):
        print(f"drive job report: {reads} but picks none of its sentences as doing so, so it is kept", file=sys.stderr)
        return None
    if question == "narrows":
        return (
            f'Jev reads this design\'s sentence "{cut(sentence, settled.QUOTED_CHARS)}" as building less than {said} name, or later, with no fork asking him:'
            " building less than his words or an answer of his name is a fork whatever it costs to switch, so build it in, or keep it as a"
            " fork with drive job ask"
        )
    return (
        f'Jev reads this design\'s sentence "{cut(sentence, settled.QUOTED_CHARS)}" as going against {said}: his words outrank every default of the designer,'
        " keeping or reusing the code that exists included. Rewrite that sentence to do what they say, or keep the choice as a fork with drive"
        " job ask that asks him to lift them, and report again; a sentence that only quotes or cites his words goes on the Researched: block's"
        " rulings: line, which Jev never reads as the design"
    )


def limit_finding(lines: list[str]) -> str | None:
    """The first statement, as written, naming a limit beside a number, in one clause, as a finding left standing: left as is, unchanged, hit or exceeded, a word ruling that out just before it, as "never hit" does, aside. Only the report's own words count, so a quote, code, file path, identifier, link or URL it cites as evidence reads as blank, as limits.py names no limit, except on an UNPROVEN: line, where a quoted refusal is the reason it gives for leaving the step unproven."""
    for line in lines:
        for found in STATEMENT.finditer(limits.masked(line)):
            statement = line[found.start() : found.end()]
            read = statement if line.startswith("UNPROVEN:") else found[0]
            if limited(read) and left_standing(read):
                return statement.strip()
    return None


def limited(statement: str) -> bool:
    return any(LIMIT.search(part) and re.search(r"\d", part) for part in limits.CLAUSE_BREAK.split(statement))


def left_standing(statement: str) -> bool:
    return any(not limits.RULED_OUT.search(statement, 0, found.start()) for found in LEFT_STANDING.finditer(statement))


def left_steps(text: str) -> str | None:
    """The first NEXT: or UNPROVEN: line that leaves a step for another session to run."""
    return next((line.strip() for line in LEFT.findall(text) if left_for(line)), None)


def left_for(line: str) -> bool:
    """Whether the line leaves a step for another session to run, a step its clause rules out, as "no coordinator runs it" does, aside."""
    return any(not limits.NEGATION.search(limits.CLAUSE_END.split(line[: found.start()])[-1]) for found in LEFT_FOR.finditer(line))


def left_piece(data: dict, job: dict, text: str) -> str | None:
    """The NEXT: or UNPROVEN: line of a builder's return that Jev reads, beside its goal, as leaving a piece of its plan unbuilt for later, at settled.YES or more, while it reads each piece an UNBUILT: line of the return or an undelivered other part of its order names, each built by a job of its own, as not that piece itself, under settled.NO, never a larger part holding it, as a delivered design part holds a row it left out, all in one request by pieces; a reading in the band between, or none, lets it out, said on stderr."""
    if not (lines := [line.strip() for line in LEFT.findall(text)]):
        return None
    order_id = job["order"]
    kept = [line["change"] for line in state.change_lines(job["kind"], text)]
    kept += [part for part in data["orders"][order_id]["parts"] if part not in job["parts"] and not state.delivered(data, order_id, part)]
    try:
        read = pieces(lines, kept, job["goal"])
    except settled.Unheard as failure:
        print(f"drive job report: Jev did not read the return for a piece of its plan left to a follow-up, so it is kept unchecked: {failure}", file=sys.stderr)
        return None
    for number, line in enumerate(lines, 1):
        leaves = settled.reading(read.get(f"leaves.l{number}"))
        named = [settled.reading(read.get(f"names.l{number}.p{part}")) for part in range(1, len(kept) + 1)]
        if leaves is False or True in named:
            continue
        if leaves is None or None in named:
            print(f'drive job report: Jev is unsure whether "{cut(line, 160)}" leaves a piece of the plan unbuilt that no UNBUILT: line or other part names, so it is kept', file=sys.stderr)
            continue
        return line
    return None


def held_piece(name: str, data: dict, job_id: str, listed: list[re.Match]) -> tuple[str, str] | None:
    """The first UNBUILT: line of a builder's return naming work the drive already holds, beside what holds it, so a line on its own or a sibling's work makes no second part: a PR of another job the line names that did not close unmerged, or else a piece an earlier return of the same builder left unbuilt that the line restates in new words, by restated_piece, or else a part of its order still to build, but its own and those the lines name in their own words, that Jev reads at settled.YES or more as naming that piece itself, every line read in one request by pieces; a reading in the band between, or none, lets it out, said on stderr."""
    job = data["jobs"][job_id]
    order_id = job["order"]
    for line in listed:
        if other := next((key for key in state.named_prs(data, order_id, line[0]) if data["prs"][key]["job"] != job_id and data["prs"][key]["state"] != "closed"), None):
            return line[0].strip(), f"in {other}, a PR of {data['prs'][other]['job']}"
    if restated := restated_piece(name, data, job_id, listed):
        return restated
    pieces_held = {*job["parts"], *(state.whole_order(line["change"]) for line in listed)}
    if not listed or not (building := [part for part in data["orders"][order_id]["parts"] if part not in pieces_held and not state.delivered(data, order_id, part)]):
        return None
    try:
        read = pieces([line[0].strip() for line in listed], building, None)
    except settled.Unheard as failure:
        print(f"drive job report: Jev did not read whether a part of the order already holds each UNBUILT: line, so it is kept unchecked: {failure}", file=sys.stderr)
        return None
    for number, line in enumerate(listed, 1):
        named = [(part, settled.reading(read.get(f"names.l{number}.p{index}"))) for index, part in enumerate(building, 1)]
        if holder := next((part for part, found in named if found), None):
            builders = state.part_builders(data, order_id, holder)
            return line[0].strip(), f'as the part "{cut(holder, 80)}"' + (f", which {', '.join(builders)} builds" if builders else "")
        if any(found is None for _, found in named):
            print(f'drive job report: Jev is unsure whether a part of the order already holds "{cut(line[0].strip(), 160)}", so it is kept', file=sys.stderr)
    return None


def restated_piece(name: str, data: dict, job_id: str, listed: list[re.Match]) -> tuple[str, str] | None:
    """The first UNBUILT: line of a builder's return that Jev reads at settled.YES or more as restating in new words a piece an UNBUILT: line of the same builder's earlier return left unbuilt, beside the part that piece became, its builders and their PRs: each such part a builder or codex job builds, by state.part_builders, read with each builder's goal and PR titles, every line against every part in one request; a reading in the band between, or none, lets it on to the order-wide read, said on stderr."""
    order_id = data["jobs"][job_id]["order"]
    words = {state.whole_order(line["change"]) for line in listed}
    earlier = {
        part: builders
        for reply in state.returns(name, job_id)
        for line in state.change_lines("builder", reply["body"])
        if (part := state.whole_order(line["change"])) not in words and (builders := state.part_builders(data, order_id, part))
    }
    if not earlier:
        return None
    prs = {part: [key for builder in builders for key in map(state.pr_key, data["jobs"][builder]["prs"]) if key in data["prs"]] for part, builders in earlier.items()}
    builds = {part: {"goals": [data["jobs"][builder]["goal"] for builder in builders], "prs": [title for key in prs[part] if (title := data["prs"][key]["title"])]} for part, builders in earlier.items()}
    lines = [line[0].strip() for line in listed]
    numbered = range(1, len(lines) + 1)
    asked = (
        ({"earlier": {f"e{index}": {"line": f"UNBUILT: {part}", "builds": builds[part]}}}, {f"restates.l{number}.e{index}": settled.asking(settled.RESTATES, slot=f"l{number}", part=f"e{index}") for number in numbered})
        for index, part in enumerate(earlier, 1)
    )
    told, questions, _ = settled.fill({"lines": {f"l{number}": line for number, line in zip(numbered, lines)}}, {}, asked)
    try:
        read = settled.jev(told, questions)
    except settled.Unheard as failure:
        print(f"drive job report: Jev did not read whether an UNBUILT: line restates a piece an earlier return left unbuilt, so it is kept unchecked: {failure}", file=sys.stderr)
        return None
    for number, line in enumerate(lines, 1):
        found = [(part, settled.reading(read.get(f"restates.l{number}.e{index}"))) for index, part in enumerate(earlier, 1)]
        if part := next((part for part, restates in found if restates), None):
            held = f" in {', '.join(prs[part])}" if prs[part] else ""
            return line, f'as the part "{cut(part, 80)}" an earlier return of {job_id} left unbuilt, which {", ".join(earlier[part])} builds{held}'
        if any(restates is None for _, restates in found):
            print(f'drive job report: Jev is unsure whether "{cut(line, 160)}" restates a piece an earlier return of {job_id} left unbuilt, so it is kept', file=sys.stderr)
    return None


def pieces(lines: list[str], parts: list[str], goal: str | None) -> dict[str, float]:
    """Jev's reads of these lines of a builder's return in one request: with its goal, whether each leaves a piece of that work unbuilt for later, and for each line and each part, filled by whole parts, whether the part names the piece the line leaves unbuilt; settled.Unheard when Jev gave none."""
    numbered = range(1, len(lines) + 1)
    base = {"lines": {f"l{number}": line for number, line in zip(numbered, lines)}} | ({"goal": goal} if goal is not None else {})
    leaves = {f"leaves.l{number}": settled.asking(settled.LEAVES, slot=f"l{number}") for number in numbered} if goal is not None else {}
    named = (({"parts": {f"p{index}": part}}, {f"names.l{number}.p{index}": settled.asking(settled.NAMES_PIECE, slot=f"l{number}", part=f"p{index}") for number in numbered}) for index, part in enumerate(parts, 1))
    told, questions, _ = settled.fill(base, leaves, named)
    return settled.jev(told, questions)


def reported_surface(text: str) -> str | None:
    """Where a builder's report says he sees what it built, by its NATIVE: line naming that surface rather than none."""
    native = NATIVE.search(text)
    return native[1] if native is not None and not native[1].casefold().startswith("none") else None


def surface(job: dict) -> str | None:
    """Where he sees or uses a job's result, by its own brief's Native line; None when it names no surface."""
    native = (job.get("brief_flags") or {}).get("native")
    return native if native and not NO_SURFACE.match(native) else None


def shows(seen: str, proof: str) -> bool:
    """Whether a proof shows a surface as its user meets it: a recording, picture or live post shows any surface, and on a command, a file holding the output of its run, since that output is all its user sees of it."""
    return bool(SEEN_PROOF.search(proof)) or bool(COMMAND_SURFACE.search(seen)) and not proof.startswith("http") and held(proof)


def unseen(data: dict, order: dict, proof: str | None) -> str | None:
    """Where he sees or uses an order's result, by the first of its jobs' briefs naming a surface the proof does not show, as shows reads each link and file it names, so it does not prove the order done there; None once it shows each, or when the order builds nothing he uses."""
    named = proofs(proof or "")
    return next((seen for job_id in order["jobs"] if (seen := surface(data["jobs"][job_id])) and not any(shows(seen, found) for found in named)), None)


def stories(interactions: str | None) -> list[str]:
    """The user stories a brief's Interactions line names, `;` only between stories: each what he does on its surface, and sees what he then sees, its result inside it after ", and sees"."""
    return [story.strip() for story in (interactions or "").split(";") if story.strip()]


def unstoried(native: str, interactions: str | None) -> str | None:
    """Why a brief for a surface he sees may not go out: its Interactions line names no user story, or one Jev reads as steps with no result he must see, at settled.YES or more, all read in one request filled by whole stories; a reading in the band between settled.NO and settled.YES, or none, lets it out, said on stderr."""
    from drive import settled

    shape = (
        f"name each user story on {native} with --interactions, each what he does there, and sees what he then sees, its result"
        ' inside the story after ", and sees", never after a `;`, which only goes between stories, such as "he presses j on the'
        ' queue, and sees the next card open with its question as its first line", which its job writes strict'
        f" acceptance criteria for before building and proves each on the installed surface with {SEEN}, by his words {STORIES_RULING}"
    )
    if not (told := stories(interactions)):
        return shape
    items = (({"stories": {f"s{number}": story}}, {f"steps.s{number}": settled.asking(settled.STEPS, slot=f"s{number}")}) for number, story in enumerate(told, 1))
    request, questions, _ = settled.fill({"surface": native}, {}, items)
    try:
        read = settled.jev(request, questions)
    except settled.Unheard as failure:
        print(f"drive job brief: Jev did not read --interactions as user stories, so the brief goes out unchecked: {failure}", file=sys.stderr)
        return None
    found = {story: settled.reading(read.get(f"steps.s{number}")) for number, story in enumerate(told, 1)}
    if steps := [story for story, step in found.items() if step]:
        return f'--interactions names "{cut(steps[0], 120)}", steps with no result he must see: {shape}'
    for story in (story for story, step in found.items() if step is None):
        print(f'drive job brief: Jev is unsure whether "{cut(story, 120)}" says what he must see, so the brief goes out', file=sys.stderr)
    return None


def proofs(text: str) -> list[str]:
    """Each link and absolute path the text names, the punctuation that closes its clause cut off, such as the colon after a picture's path, and a link ending where Slack's own link syntax ends it, at the `|` before its words or the `>` closing it, as limits.PAGE_LINK reads a card's links, so a card read back word for word names the post it links."""
    return [re.sub(r"[^\w/]+$", "", found) for found in PROOF.findall(text)]


def held(proof: str) -> bool:
    """Whether a proof is a link that opens, as unopened reads it, or the path of a folder or of a file holding something, since an empty file proves nothing."""
    path = Path(proof).expanduser()
    return unopened(proof) is None if proof.startswith("http") else path.is_dir() or path.is_file() and path.stat().st_size > 0


@functools.cache
def unopened(link: str) -> str | None:
    """Why a link does not open as he would open it, read by its host's reader once a process, or None when it opens: a Slack message in his workspace that cc-slack reads back, a GitHub pull request, commit, Actions run or release whose successful existence check github_page keeps for 24 hours independently of repository writes, once ccx's rate-limit floor lifts, or a Buildkite build that bk build view returns; a link on any other host has no reader, so it does not open."""
    from drive import github, slack

    host = urlparse(link).hostname or ""
    try:
        if host.endswith("slack.com"):
            if host != (team := slack.workspace()):
                return f"it is on {host}, not his Slack workspace {team}"
            slack.message(link)
        elif host == "github.com":
            if not (api := next((route.format(*found.groups()) for pattern, route in GITHUB_PAGES if (found := pattern.match(link))), None)):
                return "it names no pull request, commit, Actions run or release, the GitHub pages the drive reads"
            github_page(link, api)
        elif found := github.BUILD.match(link):
            github.run(["bk", "build", "view", found["number"], "-p", found["pipeline"], "-o", "json"])
        else:
            return f"the drive reads no {host or 'such'} link: a proof links a Slack post, a GitHub pull request, commit, Actions run or release, or a Buildkite build, or names the saved file of its check"
    except (slack.SlackError, github.GithubError) as error:
        return str(error)
    return None


def github_page(link: str, api: str) -> None:
    """Verify that a GitHub proof page opens, reusing a successful read for OPENED_AGE seconds across commands and repository writes. Failed reads are never stored, and no cached page contents stand in for a fresh status check."""
    from drive import github

    folder = state.root()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(sqlite3.connect(folder / "github-proof-pages.sqlite3", isolation_level=None)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS pages (api TEXT PRIMARY KEY, opened_at REAL NOT NULL) WITHOUT ROWID")
        db.execute("CREATE INDEX IF NOT EXISTS pages_opened_at ON pages (opened_at)")
        opened = db.execute("SELECT opened_at FROM pages WHERE api = ?", (api,)).fetchone()
        if opened and time.time() - opened[0] < OPENED_AGE:
            return
        github.read_past_floor(link, ["gh", "api", api, "--silent"])
        now = time.time()
        db.execute("INSERT OR REPLACE INTO pages VALUES (?, ?)", (api, now))
        db.execute("DELETE FROM pages WHERE opened_at <= ?", (now - OPENED_AGE,))


def dead(named: list[str]) -> list[str]:
    """Each link among the proofs that does not open, with why, as unopened reads it."""
    return [f"{link} does not open: {why}" for link in dict.fromkeys(named) if link.startswith("http") and (why := unopened(link))]


def unproven(text: str, job_id: str, job: dict) -> str | None:
    """Why a builder's or codex job's DONE may not be kept: for what he sees, by its NATIVE: line or its brief's Native, its brief names user stories, which only drive job prove proves, by story_gap on what its proof file prints, never a DONE's words, so a codex job's DONE is kept once it keeps a proof file, which the watch's drive job prove runs on it as on a builder's, but on a staging test part, whose DONE is kept for drive job prove to prove, by staged_stories; a link its first line or a CHECK: line names does not open, since an incident's keeps that first line as its order's proof; its CHECK: line names no link and no file that exists and is not empty, as held reads it; or, for what he sees on a brief naming no user story, its CHECK: names nothing that shows it, as shows reads it."""
    sees = reported_surface(text) or surface(job)
    told = stories((job.get("brief_flags") or {}).get("interactions"))
    if sees and told and not staged_stories(job):
        if job["kind"] == "codex" and job.get("repro"):
            return None
        prover = (
            "a codex job's DONE names its proof file on a REPRO: line, which the watch's drive job prove runs, reading"
            if job["kind"] == "codex"
            else "drive job prove proves them, running its proof file and reading"
        )
        return f"{job_id}'s brief names {len(told)} user stories, which no DONE's words prove: {prover} in what it prints {STORY_LINES}"
    if shut := dead(proofs("\n".join([text.strip().splitlines()[0], *CHECK.findall(text)]))):
        return f"a DONE's links each open as he would open them, and {shut[0]}: link the live post, page or run itself, or name the saved file of the check"
    real = [proof for line in CHECK.findall(text) for proof in proofs(line) if held(proof)]
    if not real:
        return (
            "a DONE names its proof on a CHECK: line, a link or the absolute path of a file that exists and is not empty, such as the"
            " live post, the recording of the click, or the saved output of the installed command"
        )
    return unseen_surface(sees, real) if sees and not told else None


def staged_stories(job: dict) -> bool:
    """Whether a builder's DONE on a `staging test of #N before it lands` part waits on drive job prove: it holds such a part and its brief names user stories on a surface he sees, which only its proof file's lines prove, so drive job report keeps that DONE, the watch runs drive job prove on it while the PR is still unmerged, and only the DONE that proof takes delivers the part."""
    return job["kind"] == "builder" and state.tests_staging(job) and bool(surface(job)) and bool(stories((job.get("brief_flags") or {}).get("interactions")))


def unseen_surface(seen: str, named: list[str]) -> str | None:
    """Why the proof of a result he sees, on a brief naming no user story, falls short: it names nothing that shows that surface, as shows reads it."""
    if any(shows(seen, proof) for proof in named):
        return None
    return (
        "this builds something he sees, by its NATIVE: line or its brief's Native, so its proof is from that surface: record the"
        " click through it (agent-browser's recording, or screencapture -v of the window) or link the live post, and name the"
        " file or link on CHECK:; data fetched from an API or a command's output does not show what he sees, unless it is that command's own output"
    )


def story_gap(job: dict, seen: str, output: str) -> tuple[str, list[str]] | None:
    """Why what a proof file printed falls short of the surface he sees, read in code, so no reading decides, by his pick 43f4d82, with the lines that close its kept output, one gap each: on a brief naming no user story, unseen_surface; on one naming stories, how many fall short and the first gap, each story by its number on the Interactions line, S1 on, read by story_short."""
    if not (told := stories((job.get("brief_flags") or {}).get("interactions"))):
        return (gap, []) if (gap := unseen_surface(seen, proofs(output))) else None
    printed: dict[str, list[re.Match]] = {}
    for line in output.splitlines():
        if found := STORY_LINE.fullmatch(line.strip()):
            printed.setdefault(found["story"], []).append(found)
    short = {story: gaps for number in range(1, len(told) + 1) if (gaps := story_short(story := f"S{number}", seen, printed.get(story, [])))}
    if not short:
        return None
    gaps = [gap for each in short.values() for gap in each]
    return (
        f"{len(short)} of its {len(told)} user stories fall short in the lines it printed, each gap on a line closing that output: {gaps[0]}",
        [*(f"drive job prove: {gap}" for gap in gaps), f"drive job prove: a proof file prints {STORY_LINES}"],
    )


def story_short(story: str, surfaced: str, lines: list[re.Match]) -> list[str]:
    """Each gap in one user story's lines of a proof's output: each line of STORY_LINES it lacks, each criterion with no PASS or FAIL line of its label, each FAIL or GAP line it printed, quoted whole, and a SEEN line naming nothing that shows the surface, as shows reads it, that holds something or opens, as held reads it."""
    kinds = {kind: [line for line in lines if line["kind"] == kind] for kind in STORY_KINDS}
    criteria = list(dict.fromkeys(line["label"] for line in kinds["CRITERION"] if line["label"]))
    results = {line["label"] for line in kinds["PASS"] + kinds["FAIL"]}
    seen = [proof for line in kinds["SEEN"] for proof in proofs(line["rest"]) if shows(surfaced, proof) and held(proof)]
    return [
        *([] if kinds["STORY"] else [f"{story} prints no STORY {story}: line"]),
        *(f"{story} prints no CRITERION {story} {label}: line" for label in CRITERIA if label not in criteria),
        *(f'{story}\'s criterion "{label}" has no PASS {story} {label}: or FAIL {story} {label}: line' for label in criteria if label not in results),
        *(f'{story} prints "{line[0]}"' for line in lines if line["kind"] in UNMET),
        *([] if seen else [f"{story} prints no SEEN {story}: line naming a picture or recording it saved, a live post's link that opens, or, for a command, a file holding the output of its run"]),
    ]
