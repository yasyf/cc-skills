import argparse
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from collections.abc import Callable, Iterable
from functools import cache
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from drive import agents, artifacts, brief, children, codex, github, install, land, limits, orca, page_check, pages, picture, place, providers, published, reads, receiver, refs, render, served, settled, slack, state, superseded, watch, work
from drive.shell import simple_commands
from drive.state import DriveError

DRIVE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
ROLE_NAMES = {"coordinator": "top coordinator", "communicator": "communicator", "meta": "meta session"}
FREEZE_FOR = timedelta(hours=12)
JUDGED_LATER = "The drive kept the report you sent with drive job report {job} -, then its check refused it: {refused}. Fix what it names and send drive job report {job} - again; your newest report is the one that stands."
KEPT_SPRITE_CAP = 60
OWED = "owed, send it now:"
NBD_READS = "nbd-reads.json"
CHECK_UNBOUND = frozenset({state.DRIVE_ENV, "CLAUDE_CODE_SESSION_ID", "CCI_AGENT_ID"})
RELEASE_WORD = re.compile(r"\breleas", re.IGNORECASE)
INCIDENT_EXEMPTION = "incident fix"
UNLANDABLE = (
    "{key}: refused, nothing {done}: only his tap or an exempt class lands a Forge-AI PR, and this leaves it with neither, so no card"
    " of his would ask his Approve and the lander would never queue it. Keep it waiting on his tap, or record its class with"
    " --exempt <class> or --port-of <N>; his order reworking it takes its card down with drive order set <o> --rework <url>=<his permalink>"
)
PORT_DATA_RULING = 'his Nov 12 words "port his visuals but always do it the fundamentally right way when it comes to modelng the data"'
FDE_SCAN_ROUTE = "drive fde-scan -- --symbol <each name the change touches> --pr <n>, which scans from the drive's checkout at dev's head whatever your branch's base"
FILING = ("project", "workstream", "title")
TITLE_CHARS = 60
RECORD_NOTE = re.compile(r"\brecord \w+")
PAIR_MARK = re.compile(r"[/›>]")
TOOLING_FILING = slack.TOOLING_FILING
JOB_ID = re.compile(r"j\d+")
SLACK_USER = re.compile(r"[UW][A-Z0-9]{2,}")
SLACK_TS = re.compile(r"\d+\.\d+")
FROM_HELP = (
    "where he wrote it: his Slack message's permalink, a reply's carrying thread_ts, slack:<channel>/<thread ts>/<his reply ts>"
    " or slack:<channel>/<ts>, the one form Slack is asked which thread it is in; or terminal"
)
THREAD_HELP = (
    "the order's own thread, in his DM or a channel such as an alert's: its permalink, slack:<channel>/<ts>, or its ts"
    " in the channel of the --from message, his DM for an order from the terminal; its lines to him go in a channel's thread only for an incident"
)
RULING = "ruling"
ON_THE_DRIVE = {RULING, "header:post-feedback", "header:drive-tooling"}
PR_FORMS = "owner/repo#N, its URL, #N or its bare number"
ID_FORMS = f"an order o<n>, a job j<n>, or a PR as {PR_FORMS}"
ON_A_SPRITE = "starts on a Sprite"
UNTRACKED_PRS = {("pr", "add"): "url", ("pr", "tapped"): "url", ("pr", "ci"): "url", ("pr", "published"): "url", ("job", "set"): "pr", ("job", "held"): "pr"}
WAITS_CARD = re.compile(rf"https://\S+/archives/\S+/p\d+\S*|[\w.-]+:[\w.-]+|{state.ASK_WAITS.pattern}")
WAITS_ARGS = ("waits", "redraws", "card", "via", "stands", "dropped", "settled", "superseded", "reopened")
GO_CI = Path("tools", "ci")
MAC_BOX_HAND_OVER = "<mac box hand over>"
CARGO_LOCK = "rust/Cargo.lock"
GO_CI_BUILT = Path("go", "ci", ".build", "ci.dev")
CLAUDE_BUILDS = Path.home() / ".local" / "share" / "claude" / "versions"
READER_RESEARCH = "the Researched: block this build needs"
VERB_ERRORS = (DriveError, slack.SlackError, github.GithubError)
ANSWER_FIRST = (
    "his answer goes to his thread the moment you have it proven, before any reproduction file or full report: one `report`"
    " record on the cci `post` tool, `to: [communicator@{drive}]`, `text: POST <the answer>`, `attrs: {attrs}`, with"
    ' `"customer_outage": "true"` added when it says a customer team\'s production runs are failing or stopped, so it posts while he is'
    " away too; then keep your full report and return as usual"
)
STAGING_MAC = (
    "claim the staging slot for the PR each `staging test of #N before it lands` part of yours names, from this Mac or from a Sprite lane, whose drive runs it on the drive's Mac:"
    " `drive slot claim {env} --job <your job> --minutes <how long your cut and cases run> --pr <the PR> --cases \"<your cases>\"`, which prints GO and its steps"
    " when the slot is free, else QUEUED and your place, a priority or incident tester ahead of the rest; while queued, return `WAITING staging:<target>/{env}@<head>`,"
    " one per target its paths reach by `the release provider's reach read <N>`, and the watch resumes you with GO and its steps once the slot is yours; "
    + state.STAGE_ASK
)
STAGING_FIRST = (
    "a finding of your staging run that production shows too goes to his thread the moment you have it, before any other step: one `report`"
    " record on the cci `post` tool, `to: [communicator@{drive}]`, `text: POST <the finding>`, `attrs: {attrs}`, so it posts while he is away too;"
    " then finish the run and return as usual"
)
STAGING_APPROVED = ", which waits behind this test: a full pass keeps it, so it lands the PR once the test passes (e5e4f7b), while the lander holds a dangerous change until he is present, his word or tap within 15 minutes (27ea387)"
RELEASES_LOG = "releases.log"
MANUAL_STARTED = re.compile(rf"\S+ #\d+: (?P<build>{github.BUILD.pattern}) — follow it from anywhere: (?P<follow>.+)")
PROMISED_TIME = re.compile(
    r"\b(?:(?P<next>next (?:line|update|post|summary))(?:\s+(?:by|before|at))?|by|before)\s+(?:about\s+|around\s+)?(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)(?![:\d])(?:\s*(?P<half>[AP])\.?M\b)?",
    re.IGNORECASE,
)
REPORTER_HELP = (
    "the bug report the order fixes, or the question an answer order answers, by its Slack permalink or slack:<channel>/<its thread's ts>,"
    " from the share or link in his words: its thread gets one note, a bug report's once the fix is proven live and a question's once"
    " the answer is posted, and done refuses until drive order told records it; on order add, default: each message the --from"
    " message shares that a person wrote, not him, the drive or a bot, outside the order's own thread, and the thread a teammate"
    " started outside his DM that the --from message is in, with no --thread, on an order neither an answer nor an incident, whose"
    " lines then go to his DM; none on a watch order; on order set, another reporter kept beside those the order holds, such as a"
    " second teammate's report of the same bug, so each thread hears its own note"
)
WATCH_HELP = (
    "a teammate's Slack thread he ordered watched, by its permalink or slack:<channel>/<its thread's ts>: the watch reads it once"
    " a minute and hands the communicator each new message there from anyone but him, the drive and a bot, until the order closes"
)
FOLLOW_HELP = (
    "a Buildkite build's link and his message in his DM asking to be kept updated on it: the watch keeps one status message in the thread"
    " of his ask current with the build's running steps, rewriting it each minute from the Buildkite read each delivery to the receiver"
    " makes, and once the build ends hands the communicator the end line, which delivers the part --part names, the order's only part by"
    " default; no other post delivers that part meanwhile"
)
ALERT_HELP = (
    "the Datadog monitor or Sentry issue link of the alert it is, again for each: once its fix is live, the watch reads each"
    " monitor every minute and closes the incident once all read OK"
)
USERS_HELP = (
    "the cci record whose read shows real users' runs, or their local chats or agents, failing on production: the watch pages him"
    " for an incident opened while he is away only when its order carries one, and a failure only the drive's own callers or proofs"
    " hit carries none"
)
REWORK_HELP = (
    "a PR his words rework, by its url or key, and the permalink of those words, as <PR>=<his permalink>, named only once its"
    " stack's His words, step by step read against them leave them unmet: it withdraws in the same call each open card of his"
    " carrying it, his words there its reason, linked to them, and no card, ready or tap of his stands on it until the builder's"
    " READY says how his words are met; with none, every card stays up"
)
TOLD_LINK = (
    "Link each card where the line names it, as <its permalink|your card on what it decides>, never as its bare permalink,"
    " which `cc-slack check-post` refuses."
)
ASKED_AGAIN = "Asked again on a newer card · answer that one"
HIDDEN = "not visible in Orca, which hides the worktrees it did not make for this repository"
PAGES = Path(__file__).resolve().parents[2] / "skills" / "long-running" / "reference"
PAGE_STORIES = (
    "open Pages on the dashboard and see the page listed live under its order, his goal as its line;"
    " open the page and see in its first view where the work he named stands, from its orders, PRs and decisions as they are now;"
    " answer a decision or approve a PR on the page and see the queue take it as it takes his tap, the page redrawn with it settled"
)
LIST_ITEM = re.compile(r"\s*(?:[-*] |\d+\. )")
MANIFEST = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
LAUNCHED = ("run", "dispatch", "task", "terminal", "workspace", "environment", "lane", "worktree", "sprite_config")
MCP_TOOLS = ("say", "heard", "record", "order", "job", "pr", "incident", "status", "search_plan", "page")


def emit(args: argparse.Namespace, line: str, payload: object) -> int:
    print(json.dumps(payload, indent=2) if args.json else line)
    return 0


def start(args: argparse.Namespace) -> int:
    if not DRIVE_NAME.fullmatch(args.name):
        raise DriveError(f"drive names are letters, digits, dot, dash and underscore: {args.name}")
    kept = Path.cwd() if state.scratch() else orca.checkout(args.name, os.getcwd())
    if not orca.in_terminal():
        raise DriveError(
            f"drive start {args.name} --role {args.role}: refused, this session runs in no Orca terminal, so he cannot open it from Orca;"
            f" start the session under cci and show it in one with {' then '.join(agents.launch(args.name, args.role, args.project, str(kept)))}"
        )
    if args.project and args.role == "meta":
        raise DriveError("--project binds a sub-coordinator or a seat's communicator; the meta session holds no project")
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    fresh = state.new_drive(args.name, args.role, session, str(kept))
    fresh["roles"][args.role]["rules"] = render.rules_digests(fresh, args.role, args.project)

    def started(data: dict) -> dict:
        if args.project and args.role == "communicator":
            bind_communicator(data, args.project, fresh["roles"][args.role])
        elif args.project:
            bind_subcoordinator(data, args.project, fresh["roles"][args.role])
        elif project := state.session_seat(data, session):
            raise DriveError(
                f"--role {args.role}: session {session} is the sub-coordinator for {project} in drive {args.name}; the top coordinator,"
                " the communicator and the meta session are sessions of their own, so start this role from another session"
            )
        else:
            data["roles"][args.role] = fresh["roles"][args.role]
        watched_channels = data["slack"].setdefault("watch", [])
        watched_channels.extend(channel for channel in dict.fromkeys(args.watch) if channel not in watched_channels)
        return data

    data = state.update_or_create(args.name, fresh, started)
    state.migrate(args.name)
    held = fresh["roles"][args.role]
    registered = "" if state.scratch() else f"{agents.register(args.name, args.role, args.project, held['cwd'], held['session'])}\n"
    seen = orca_places({args.role: held}, run_show)[args.role]
    opened = channel_opened(args.name, data)
    siblings = "".join(sibling_started(args.name, role, held["cwd"], data["launched"].get(role)) for role in SIBLINGS if role not in data["roles"])
    watched = f"{watch.ensure(args.name)}\n" if args.role == "coordinator" else ""
    seat = (f"communicator for {args.project}" if args.role == "communicator" else f"sub-coordinator for {args.project}") if args.project else args.role
    elsewhere = "" if Path.cwd() == kept.resolve() else f"this session runs in {Path.cwd()} until it next exits, when cci resumes it in the drive's checkout {kept}\n"
    text = f"{render.context(data, args.role, args.project)}\n\n{registered}{opened}{siblings}{watched}{elsewhere}{seat}: session {held['session']}, {seen}"
    return emit(args, text, {**data, "orca": {seat: seen}})


def channel_opened(name: str, data: dict) -> str:
    """Make the drive's own private channel by open_channel while it keeps none, so every drive talks to him in its channel from its first start, by his pick of the card whose option reads "drive start creates the private channel drive-<name>, invites him, keeps its id on the drive and opens that drive's own communicator"; the line saying it was not made, or nothing, as for a drive whose state root LONG_RUNNING_STATE or XDG_STATE_HOME moves."""
    if state.scratch() or "" in data["slack"]["channels"]:
        return ""
    try:
        open_channel(name, None)
    except slack.SlackError as error:
        return f"the drive's own channel was not made: {error}; drive channels --make makes it\n"
    return ""


SIBLINGS = {"meta": ("the meta session", "which holds drive tooling"), "communicator": ("the communicator", "which holds the drive's Slack channels")}


def sibling_started(name: str, role: str, worktree: str, earlier: str | None) -> str:
    """Start the meta session or the communicator in an Orca terminal of its own while none is bound, by his words "have the skill always start a meta root as a sibling to coordinator and communicator", so no drive runs without the communicator holding its channels; the line saying so, or nothing for a drive whose state root LONG_RUNNING_STATE or XDG_STATE_HOME moves."""
    if state.scratch():
        return ""
    who, holds = SIBLINGS[role]
    try:
        handle = orca.start_session(name, role, None, worktree, earlier)
    except orca.OrcaError as error:
        return f"{who}, {holds}, did not start: {error}; drive manual start prints its commands\n"
    if handle is None:
        return f"{who} starts in Orca terminal {earlier}, which an earlier drive start opened\n"
    def sibling_started_write(data: dict):
        data["launched"][role] = handle

    state.update(name, None, sibling_started_write)
    return f"started {who}, {holds}, in Orca terminal {handle}\n"


def seats_retired(name: str) -> dict[str, str]:
    """Retire each priority seat no open order holds, by watch.seats_retired, in the step of drive order set that closes or unmarks its order."""
    from drive import watch

    return watch.seats_retired(name)


def seat_started(name: str, seat: str) -> str:
    """Start the sub-coordinator session of a priority order's seat in an Orca terminal of its own while none is bound for it, by his words "when something gets marked as priority it gets its own dedicated coordinator spun uo for it", in the top coordinator's checkout, so its first turn binds it with drive start --role coordinator --project <seat> and starts its watch, which then hears the order in place of its project's; the line saying so, or nothing for a drive whose state root LONG_RUNNING_STATE or XDG_STATE_HOME moves."""
    if state.scratch():
        return ""
    data = state.load(name)
    if seat in data["subcoordinators"]:
        return f"the sub-coordinator for {seat} is bound, session {data['subcoordinators'][seat]['session']}, and hears the order"
    key = f"sub-coordinator {seat}"
    try:
        handle = orca.start_session(name, "coordinator", seat, data["roles"]["coordinator"]["cwd"], data["launched"].get(key))
    except orca.OrcaError as error:
        return f"the sub-coordinator for {seat} did not start: {error}; drive manual start prints its commands"
    if handle is None:
        return f"the sub-coordinator for {seat} starts in Orca terminal {data['launched'][key]}, which an earlier drive order set --priority opened"
    def seat_started_write(data: dict):
        data["launched"][key] = handle

    state.update(name, None, seat_started_write)
    return f"started the sub-coordinator for {seat}, which holds this order alone, in Orca terminal {handle}; its project's coordinator hears it until it binds"


def communicator_started(name: str, seat: str) -> str:
    """Start the communicator of a seat, a project's or a priority order's, in an Orca terminal of its own while none is bound for it, once the drive keeps that seat's channel, making it first while the drive keeps its own channel, by his words "we should move so every project has a communicator to pair with its coordinator and messages in that channel should go to it", from drive order set --priority and from the watch the first time a message of his is owed in that channel, in the top coordinator's checkout, so its first turn binds it with drive start --role communicator --project <seat>; the line saying so, or nothing for a drive whose state root LONG_RUNNING_STATE or XDG_STATE_HOME moves."""
    if state.scratch():
        return ""
    data = state.load(name)
    if seat in data["communicators"]:
        return f"the communicator for {seat} is bound, session {data['communicators'][seat]['session']}, and holds its channel"
    if seat not in data["slack"]["channels"]:
        if "" not in data["slack"]["channels"]:
            return f"the communicator for {seat} did not start: the drive keeps no channel; drive channels --make makes them"
        open_channel(name, seat)
        data = state.load(name)
    key = f"communicator {seat}"
    try:
        handle = orca.start_session(name, "communicator", seat, data["roles"]["coordinator"]["cwd"], data["launched"].get(key))
    except orca.OrcaError as error:
        return f"the communicator for {seat} did not start: {error}; drive manual start prints its commands"
    if handle is None:
        return f"the communicator for {seat} starts in Orca terminal {data['launched'][key]}, which an earlier start opened"

    def communicator_started_write(data: dict):
        data["launched"][key] = handle

    state.update(name, None, communicator_started_write)
    return f"started the communicator for {seat}, which holds its channel #{data['slack']['channels'][seat]['name']}, in Orca terminal {handle}"


def bind_subcoordinator(data: dict, project: str, held: dict) -> None:
    """Bind this session as the project's sub-coordinator, or as a priority order's by the seat drive order set --priority names, replacing any session bound for it before, and keep where the event log ended at its first binding, which its watch reads from; refused for a project no order is filed under and no order holds as its seat, a session holding another seat, and a lane another seat, record or numbered name of the drive takes."""
    if project not in (filed := [*state.projects(data), *sorted(order["priority"] for order in data["orders"].values() if order["priority"])]):
        raise DriveError(f"--project {project}: no order is filed under it; the projects in use: {', '.join(filed) or 'none'}")
    session = held["session"]
    if role := next((role for role, bound in data["roles"].items() if session and bound["session"] == session), None):
        raise DriveError(
            f"--project {project}: session {session} holds the {ROLE_NAMES[role]} role of drive {data['name']}; a sub-coordinator is a session"
            " of its own, so bind it from another session"
        )
    if (other := state.session_project(data, session)) not in (None, project):
        raise DriveError(f"--project {project}: session {session} is the sub-coordinator for {other} of drive {data['name']}; bind {project}'s from another session")
    bound = data["subcoordinators"].get(project)
    if clash := lane_clash(data, project, bound is None):
        raise DriveError(f"--project {project}: {clash}; file its orders under a project whose lane no one else takes")
    data["subcoordinators"][project] = held | {"from": bound["from"] if bound else state.events_end(data["name"])}


def bind_communicator(data: dict, project: str, held: dict) -> None:
    """Bind this session as the communicator for a project or priority seat, holding that seat's channel alone, paired with its sub-coordinator, by his words "every project has a communicator to pair with its coordinator and messages in that channel should go to it", replacing any session bound for it before; the drive's communicator gets its new hold line at its next call, so it lets that channel go. Refused for a seat whose channel the drive has not made and for a session holding another seat or role."""
    if project not in data["slack"]["channels"]:
        raise DriveError(f"--project {project}: the drive keeps no channel for it, so its communicator would hold none; the channels it keeps: {', '.join(seat for seat in data['slack']['channels'] if seat) or 'none'}")
    session = held["session"]
    if role := next((role for role, bound in data["roles"].items() if session and bound["session"] == session), None):
        raise DriveError(f"--project {project}: session {session} holds the {ROLE_NAMES[role]} role of drive {data['name']}; a seat's communicator is a session of its own")
    if (other := state.session_seat(data, session)) not in (None, project) or state.session_project(data, session):
        raise DriveError(f"--project {project}: session {session} already holds the seat {other} of drive {data['name']}; bind {project}'s communicator from another session")
    data["communicators"][project] = held


def lane_clash(data: dict, project: str, first: bool) -> str | None:
    """What already takes the project's cci lane: another sub-coordinator's, a lane of the drive's own, a lane cci records of the drive were posted as before its first binding, or a numbered copy of another lane, as cci reads a name ending in -<digits>."""
    lane = state.project_lane(project)
    if numbered := state.NUMBERED_LANE.fullmatch(lane):
        return f"its lane {lane} ends in -<digits>, which cci reads as a numbered copy of lane {numbered['stem']}"
    if other := next((other for other in data["subcoordinators"] if other != project and state.project_lane(other) == lane), None):
        return f"its lane {lane} is the lane of the sub-coordinator for {other}"
    if lane in state.DRIVE_LANES:
        return f"its lane {lane} is the drive's own {lane} lane"
    if first and lane_posted(data["name"], lane):
        return f"its lane {lane} is a lane cci records of drive {data['name']} were posted as"
    return None


def lane_posted(name: str, lane: str) -> bool:
    try:
        return bool(reads.run(["cci", "grep", ".", "--lane", lane, "--drive", name, "--limit", "1", "--json"], Path.cwd()).strip())
    except reads.ReadError as error:
        raise DriveError(f"cci could not say whether records of drive {name} were posted as lane {lane}: {error}") from None


def order_add(args: argparse.Namespace) -> int:
    """Record his order, refusing a --parts name a part cannot hold, by state.unquotable_part; an incident order records a job on each of its parts, its fix among them, unless it joins the open incident on the same failing service; a drive tooling order whose words Jev reads as repeating an open tooling order joins it unasked, by his pick 3caa184, and his order joins one only by its --joins, Jev's read of the open order its words are on printed for it, so no guess files his order on an unrelated feature; a joined order takes the head's project and workstream, each unfinished job on the feature carrying it and holding his words, so one feature keeps one coordinator, one set of rulings and its jobs; it reworks only the PRs --rework names and names each other open PR card of his in its thread, which it leaves up, and names each open question card of his its words point at, by state.asked_lines."""
    name = state.resolve(args.drive)
    reworks = heard_reworks(name, args.rework)
    order_id, order, started, incidents = added_order(args)
    restarted = fresh_marked(name, [order_id], args.words, args.source) if args.fresh else [] if args.fresh is False else restart_read(name, [order_id], args.words, args.source)
    reworked = rework_named_prs(name, order_id, reworks, thread_cards(name, order), f"drive order set {order_id} --rework <PR>=<his permalink>")
    return emit(args, "\n".join([order_id, *started, *incidents, *restarted, *reworked]), {"id": order_id, **state.load(name)["orders"][order_id], "incidents": incidents})


def restart_read(name: str, order_ids: list[str], words: str, link: str | None) -> list[str]:
    """Mark fresh each open build order of his product among these, incidents aside and those these words marked already, when Jev reads his words as ordering the design of its work started over from scratch, by settled.restarts, read once for them all, by `fresh_marked`; what was done, a line each."""
    data = state.load(name)
    orders = [
        order_id
        for order_id in dict.fromkeys(order_ids)
        if (order := data["orders"][order_id])["state"] not in state.CLOSED_ORDER_STATES
        and order["kind"] == "build"
        and not (order["incident"] or state.tooling(order) or order["fresh"] and order["fresh"]["link"] == link)
    ]
    return fresh_marked(name, orders, words, link) if orders and settled.restarts(words) else []


def fresh_marked(name: str, order_ids: list[str], words: str, link: str | None) -> list[str]:
    """Mark each order fresh on his words restarting its design, ending as withdrawn on them every card asked on its feature before them, by superseded.restart, each card following; what was done, a line each, with each card it ended."""
    def restarted(data: dict) -> dict[str, list[tuple[str, dict]]]:
        return {order_id: superseded.restart(data, order_id, words, link) for order_id in order_ids}

    ended = state.update(name, None, restarted)
    lines = [
        f'{order_id} marked fresh on his words "{render.cut(words, 120)}": {len(each)} {"card" if len(each) == 1 else "cards"} asked on its design before them withdrawn on those words, so its design asks again in the new architecture\'s terms'
        for order_id, each in ended.items()
    ]
    return lines + superseded.follow(name, [question for each in ended.values() for question in each], "his words restarting its design")


def rework_mark(text: str) -> tuple[str, str]:
    url, _, link = text.partition("=")
    if not url or not limits.PAGE_LINK.fullmatch(link):
        raise argparse.ArgumentTypeError("say <PR>=<the permalink of his words that rework it>")
    return url, link


def heard_reworks(name: str, marks: list[tuple[str, str]], job_id: str | None = None) -> list[dict]:
    """Each PR --rework names with his words at the permalink beside it, as settled.heard reads them, refused for a PR that is not an open one the drive tracks, of the job when one is named, or a permalink holding no words of his."""
    from drive import view

    data = state.load(name)
    found = view.live_cards(name) if marks else None
    reworks = []
    for url, link in marks:
        key = state.pr_key(url)
        if not (pr := data["prs"].get(key)) or pr["state"] != "open" or job_id not in (None, pr["job"]):
            raise DriveError(f"--rework {key}: refused, it names an open PR the drive tracks{f' of {job_id}' if job_id else ''}")
        try:
            words = settled.heard(link, found, data)
        except slack.SlackError as error:
            raise DriveError(f"--rework {key}={link}: his words there could not be read: {error}; run this again") from None
        if words is None:
            raise DriveError(f"--rework {key}={link}: refused, it is neither his own message nor a card he answered; name the permalink of his words that rework it")
        reworks.append({"key": key, "words": words, "link": link})
    return reworks


def thread_cards(name: str, order: dict) -> list[dict]:
    return [card for card in fresh_pr_cards(name) if state.link_thread(card["url"]) == where] if (where := state.promise_thread(state.load(name), order)) else []


def rework_named_prs(name: str, order_id: str, reworks: list[dict], cards: list[dict], how: str) -> list[str]:
    """Rework to his words only the PRs --rework names, and name each other card of his it leaves up, since a stack can meet his words under its card already."""
    keys = {each["key"] for each in reworks}
    left = [card for card in cards if not keys & set(map(state.pr_key, card["prs"]))]
    return [
        *rework_prs(name, reworks, order_id),
        *(
            f"left up his card {card['url']} on {', '.join(map(state.pr_key, card['prs']))}: read its stack's His words, step by step"
            f" against {order_id}'s words, and run {how} for each PR they leave unmet"
            for card in left
        ),
    ]


def fresh_pr_cards(name: str) -> list[dict]:
    from drive import view

    view.refresh_cards(name)
    return (view.live_cards(name) or view.NO_CARDS)["pr_cards"]


def rework_prs(name: str, reworks: list[dict], by: str) -> list[str]:
    """Mark the open drive PRs reworked to his words, each kept with the link where he said them, and withdraw in the same call, once the drive is saved, each open card of his carrying one, his words its reason linked to them, by his words "So why isn't this card withdrawn then? And why do I have to keep asking that?": no card asks his tap on one until its builder's READY says how his words are met, while his Approve stands; the watch wakes its builder with his words, and withdraws again a card whose withdrawal failed."""
    def reworked(data: dict):
        kept = {each["key"]: each for each in reworks if each["key"] in data["prs"] and data["prs"][each["key"]]["state"] == "open"}
        for key, each in kept.items():
            state.rework(data["prs"][key], by, each["words"], each["link"])
        keys = list(kept)
        return keys
    keys = state.update(name, None, reworked)
    lines = [f"{key} reworked to {by}: its card stays down until its builder's READY says how his words are met" for key in keys]
    return lines + state.after_save(name, lambda: [said for key in keys for said in withdrawn_card(name, key)])


def withdrawn_card(name: str, key: str) -> list[str]:
    from drive import talk

    try:
        return [f"withdrew his card {card}: {state.rework_reason(state.load(name)['prs'][key])}" for card in talk.withdraw(name, key)]
    except slack.SlackError as error:
        return [f"{key}: withdrawing its card failed, so the watch withdraws it on its next read: {error}"]


def added_order(args: argparse.Namespace, unfiled: bool = False) -> tuple[str, dict, list[str], list[str]]:
    name = state.resolve(args.drive)
    created = state.stamp()
    refuse_unquotable_parts("--parts", args.parts or [])
    if args.incident and not args.joins and state.INCIDENT_FIX not in (args.parts or []):
        raise DriveError(
            f'an incident order starts its fix job at the alert, beside diagnosis: name "{state.INCIDENT_FIX}" among --parts, such as'
            f' --parts {state.INCIDENT_FIX} "first check", or pass --joins <o> when an open incident order covers the same failing service'
        )
    message = his_message(args.source)
    thread = order_thread(state.load(name), args.source, message, args.thread)
    reported = [state.reporter(args.reporter)] if args.reporter else reporters(args, message, thread)
    watched = state.new_watch(args.watch, state.parse_time(created)) if args.watch else None
    if (args.incident or args.joins) and args.options_only:
        raise DriveError(
            "--options-only: refused, an incident order is never limited to options, since its codex jobs start at the alert, before he picks;"
            " add it without --incident and --joins when he asked for options only, or without --options-only when it is an incident"
        )
    alerts = alert_links(args.alerts)
    tooling = args.words.startswith(state.TOOLING)
    if held := held_order(state.load(name), args.words, args.owner):
        return held, state.load(name)["orders"][held], [f"{held} already holds these words, so no order was added"], []
    title = titled(args.title)
    if not (args.joins or args.incident or args.options_only or unfiled or args.kind == "answer"):
        args.joins = settled.repeats(state.load(name), args.words, tooling)
    filed = (None, None) if unfiled or args.joins else filing(state.load(name), args.words, args.project, args.workstream, "refused, no order added")
    paged = not (tooling or args.incident or args.joins) and settled.asks_page(args.words)
    minutes = 0 if args.every or args.incident else settled.cadence(args.words)
    def order_added(data: dict):
        role = state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID"))
        if tooling and args.owner not in (None, "meta"):
            raise DriveError(
                f'--owner {args.owner}: a "{state.TOOLING}" order is the meta session\'s, by his words of Nov 10 10:08 PM:'
                ' "and remember toolingh/meta improvements dont go to coorfidnatror", and of Nov 11 12:31 PM: "A third session, a'
                ' tooling/meta coordinator, owns every tool fix, skill rewrite and install"; leave --owner out'
            )
        if role == "meta" and not tooling:
            raise DriveError(
                f'refused, no order added: the meta session adds only "{state.TOOLING}" orders and never touches his product orders,'
                f" which are the coordinator's; send his words to root@{name} as an owner record"
            )
        owner = args.owner or ("meta" if tooling else role or "coordinator")
        joined = find(data["orders"], args.joins, "order") if args.joins else None
        if joined and (joined["state"] in state.CLOSED_ORDER_STATES or args.incident and not joined["incident"]):
            raise DriveError(
                f"--joins {args.joins}: name an open order, an open incident order for --incident; {args.joins} is {joined['state']}{'' if joined['incident'] else ', not an incident'}"
            )
        if data["tooling_backlog"] and joined and tooling and not (thread or reported or args.incident) and state.unasked_tooling(head := data["orders"][head_id := joined["joins"] or args.joins]):
            why = state.tally(head, args.source, state.parse_time(created))
            if args.promote and not head["promoted"]:
                why = state.promote(head, args.promote, state.parse_time(created))
            return (head_id, head, [backlog_line(head_id, head, why)], []), None
        if joined:
            head = data["orders"][joined["joins"] or args.joins]
            project, workstream = head["project"], head["workstream"]
        else:
            project, workstream = filed
        incidents = [render.incident_line(other_id, other) for other_id, other in state.open_orders(data).items() if args.incident and not joined and other["incident"]]
        order_id = state.next_id(data["orders"], "o")
        paced = next((each for each, other in state.open_orders(data).items() if not other["incident"] and thread and state.promise_thread(data, other) == tuple(thread)), order_id) if minutes else None
        parts = {part: state.new_part() for part in args.parts or [title]}
        order = data["orders"][order_id] = state.new_order(args.words, slack_key(args.source, message), thread, parts, created) | {
            "alerts": alerts,
            "reporters": reported,
            "watch": watched,
            "users": args.users,
            "incident": args.incident or bool(joined and joined["incident"]),
            "kind": joined["kind"] if joined else "build" if args.incident else args.kind,
            "complex": args.complex,
            "options_only": args.options_only,
            "once": args.once,
            "every": args.every or (minutes if paced == order_id else 0),
            "owner": owner,
            "project": project,
            "workstream": workstream,
            "title": title,
            "note": joined and joined["note"],
            "joins": joined and (joined["joins"] or args.joins),
        }
        if data["tooling_backlog"] and tooling and not args.incident and state.unasked_tooling(order) and not joined:
            why = state.promote(order, args.promote, state.parse_time(created)) if args.promote else None
            backlogged = [backlog_line(order_id, order, why)]
        else:
            backlogged = []
        if paged:
            order["parts"][state.PAGE] = state.new_part()
            state.sort_as_build(order, [state.PAGE], order["kind"] == "answer")
        if paced and paced != order_id:
            data["orders"][paced]["every"] = minutes
        started = [state.incident_job(data, name, order_id, part, created) for part in (order["parts"] if args.incident and not joined else [])]
        started += [state.INCIDENT_BRIEF] if started else []
        if paced:
            started.append(f"every {minutes} minutes, as his words ask: {paced} owes him a line in its thread that long after its last one until done")
        page_job = paged and state.codex_job(data, order_id, state.PAGE, created)
        repeats = joined and not state.hurts_now(data, order)
        group = [other for other in state.feature_orders(data, order_id) if other != order_id] if repeats else []
        carriers = [job_id for job_id, job in state.orders_jobs(data, group).items() if job["state"] not in state.FINISHED_JOB_STATES]
        for job_id in carriers:
            state.carry(data, job_id, order_id, list(order["parts"]))
            state.hold_text(data["jobs"][job_id], state.joined_words(order_id, order["joins"], args.words), state.HIM)
        return None, (data, order_id, order, joined, incidents, backlogged, started, page_job, repeats, group, carriers)

    tallied, added = state.update(name, None, order_added)
    if tallied:
        return tallied
    data, order_id, order, joined, incidents, backlogged, started, page_job, repeats, group, carriers = added
    if page_job:
        started.append(page_brief(name, page_job))
    if joined:
        carried = (
            f"carried by {', '.join(carriers)}, each holding these words for its worker, so no job starts on it"
            if carriers
            else f"no unfinished job on {', '.join(group)} carries it, so its jobs are added on {order_id}"
            if repeats
            else f"its owner job carries this order with --carries {order_id}"
        )
        started.append(f"joins {args.joins}: {joined['note'] or 'no record'}; {carried}")
    return order_id, order, [*started, *backlogged, *state.asked_lines(data, args.words)], incidents


def backlog_line(order_id: str, order: dict, why: str | None) -> str:
    """What a drive tooling order a session filed now is on the backlog, by his option A of Nov 15: promoted, so its one builder starts now with no reader, or waiting, so no job starts."""
    filed = f"{order_id} filed {len(order['tally'])} time{'s' if len(order['tally']) > 1 else ''}"
    if why:
        return f"{filed}, promoted ({why}): add its one builder now, which researches inline with no reader"
    return f"{filed}, on the drive tooling backlog: start no job; a repeat tallies here, {state.TALLY_PROMOTES} filings in 24 hours or the daily drain promote it"


def page_brief(name: str, job_id: str) -> str:
    """Write the brief of the codex job building the page the drive adds to an order whose words name a goal or ask where work stands, which starts at once: planned by the Pages manual's Build one section, rebuilding the slug of a page Pages lists whose goal is his, done once `drive page add` registers it and each of PAGE_STORIES clicks on the installed dashboard; the line that starts it."""
    listed = "; ".join(f"/pages/{page['slug']}/, {page['goal']}" for page in pages.listed(name)) or "none"
    build = page_parts((PAGES / "pages.md").read_text(), "## Build one by hand")[0].partition("\n1. ")[2].strip()
    flags = dict.fromkeys(state.BRIEF_FLAGS) | {
        "done_when": "drive page add registered the page on this order with its gate passed, and each story below clicks through on the installed dashboard",
        "native": "the drive dashboard's Pages and the page at /pages/<its slug>/",
        "interactions": PAGE_STORIES,
        "plan": f"1. {build}\n\nPages lists now: {listed}. When one of them shows the work his words name, rebuild that slug with this order among its orders instead of adding a second page.",
    }
    _, written = write_brief(name, job_id, flags)
    return f'{job_id} on "{state.PAGE}" builds the page his words ask for: start it now with drive job launch {job_id}, its brief written at {written["brief"]}'


def held_order(data: dict, words: str, owner: str | None) -> str | None:
    """The open order the meta session holds with these words, which an add of a "Drive tooling:" order, or of one naming the meta session its owner, returns instead of adding the words twice; None for any other add."""
    if not (words.startswith(state.TOOLING) or owner == "meta"):
        return None
    return next((order_id for order_id, order in state.open_orders(data).items() if order["text"] == words and order["owner"] == "meta"), None)


def alert_links(links: list[str]) -> list[str]:
    if wrong := [link for link in links if not (state.DATADOG_MONITOR.fullmatch(link) or state.SENTRY_ISSUE.fullmatch(link))]:
        raise DriveError(
            f"--alert {wrong[0]}: refused, name the alert by its Datadog monitor link, https://app.datadoghq.com/monitors/<id>,"
            " or its Sentry issue link, https://<org>.sentry.io/issues/<id>"
        )
    return links


def incident_open(args: argparse.Namespace) -> int:
    """Open an incident in one save: his order, a job on its fix beside a job on each diagnosis part, and the evidence record its note names, titled by the order's --title, so a record cc-notes refuses leaves the drive as it was; it names each open question card of his its words point at, by state.asked_lines."""
    name = state.resolve(args.drive)
    args.parts = [state.INCIDENT_FIX, *state.INCIDENT_PARTS["release" if args.release else "none" if args.no_rollback else "rollback"]]
    with state.batch(name):
        order_id, order, started, incidents = added_order(args)
        record = evidence_record(name, order["title"], args.words, args.source, "drive incident open")
        def record_noted(data: dict) -> None:
            data["orders"][order_id]["note"] = order["note"] = f"record {record}"

        state.update(name, None, record_noted)
    return emit(args, "\n".join([f"{order_id} record {record}", *started, *incidents]), {"id": order_id, **order, "incidents": incidents})


def evidence_record(name: str, title: str, words: str, source: str, again: str) -> str:
    """Open an incident's evidence record, a cc-notes investigation titled by its order's title, and return its id, or refuse, so the write waiting on it keeps nothing."""
    argv = ["ccn", "investigation", "open", title, "--body", f"{words}\n\nFrom {source}", "--label", f"drive:{name}", "--label", "incident", "--json"]
    try:
        return state.unrehearsed(name, lambda: json.loads(reads.run(argv, Path(state.load(name)["roles"]["coordinator"]["cwd"])))["id"][:7], "<record>")
    except reads.ReadError as error:
        raise DriveError(f"refused, nothing written: its evidence record did not open: {error}; fix that and run {again} again") from None


def incident_record(name: str, order_id: str, order: dict) -> str | None:
    """Open the evidence record of an order turning into an incident before the write that turns it, by evidence_record, from the order as that write leaves it, and return its id; None when its note names one already."""
    if RECORD_NOTE.search(order["note"] or ""):
        return None
    if not order["title"]:
        raise DriveError(f"{order_id}: refused, nothing written: its evidence record is titled by the order's title, and it has none; pass --title <a few words>")
    return evidence_record(name, order["title"], order["text"], order["from"], f"drive order set {order_id} --incident")


def turned_incident(name: str, data: dict, order_id: str, created: str, record: str | None) -> list[str]:
    """Turn an open order into an incident in the save that marks it, as drive incident open opens one: its fix and cause parts, a build whose other parts an answer order asked stay answers, the evidence record incident_record opened before the write when its note names none, and a codex job by state.incident_job on each of those two parts no unfinished codex job holds, but on an order joined to an open incident, whose owner job carries it; the lines that say so."""
    order = data["orders"][order_id]
    parts = [state.INCIDENT_FIX, state.INCIDENT_CAUSE]
    for part in parts:
        order["parts"].setdefault(part, state.new_part())
    state.sort_as_build(order, parts, order["kind"] == "answer")
    opened = []
    if not RECORD_NOTE.search(order["note"] or ""):
        if not record:
            raise DriveError(f"{order_id}: refused, nothing written: another write changed it while this one ran, so no evidence record opened for it; run drive order set {order_id} --incident again")
        order["note"] = "; ".join(filter(None, [order["note"], f"record {record}"]))
        opened = [f"{order_id} record {record}"]
    if joined := state.open_incidents(data, [order["joins"]] if order["joins"] else []):
        return [*opened, f"joins {joined[0]}, an open incident, so no job starts on it: its owner job carries it with drive job set <that job> --carries {order_id}"]
    coded = {part for part in parts for _, job in state.part_jobs(data, order_id, part) if job["kind"] == "codex" and job["state"] not in state.FINISHED_JOB_STATES}
    started = [state.incident_job(data, name, order_id, part, created) for part in parts if part not in coded]
    return [*opened, *started, state.INCIDENT_BRIEF] if started else opened


def incident_stop(args: argparse.Namespace) -> int:
    """Give an open incident its stop when no build can roll the break back: a job with apply authority whose PR, the incident's immediate fix, turns the failing automatic path off and lands at once as an incident fix, until the root fix replaces it."""
    name = state.resolve(args.drive)
    created = state.stamp()
    def incident_stop_write(data: dict):
        order = find(data["orders"], args.id, "order")
        if not state.open_incidents(data, [args.id]):
            raise DriveError(f"{args.id}: refused, it is no open incident order")
        if refused := state.held_elsewhere(data, args.id, os.environ.get("CLAUDE_CODE_SESSION_ID")):
            raise DriveError(f"refused, no stop added: {refused}")
        if running := state.running_stops(data, args.id):
            raise DriveError(f"{args.id}: refused, its stop runs as {', '.join(running)}; resume that job instead")
        order["parts"][state.INCIDENT_STOP] = state.new_part()
        started = state.incident_job(data, name, args.id, state.INCIDENT_STOP, created)
        order["updated"] = created
        return started

    started = state.update(name, None, incident_stop_write)
    return emit(args, f"{started}\n{state.INCIDENT_BRIEF}", {"id": args.id, "job": started.split()[0]})


def incident_freeze(args: argparse.Namespace) -> int:
    """Freeze the broken service's other releases in the turn users are unblocked, until its root fix releases; the freeze's id goes on the order's note for the job that lifts it."""
    name = state.resolve(args.drive)
    data = state.load(name)
    order = find(data["orders"], args.id, "order")
    if not state.open_incidents(data, [args.id]):
        raise DriveError(f"{args.id}: refused, it is no open incident order")
    orca.checkout(name, data["roles"]["coordinator"]["cwd"])
    until = state.stamp(state.now() + FREEZE_FOR)
    reason = f"{args.id}, {render.cut(order['text'], 80)}: held until its root fix releases"
    release = providers.of(data).release
    try:
        freeze = state.unrehearsed(name, lambda: release.freeze(args.components, reason, until), "<freeze>")
    except (reads.ReadError, providers.ProviderError) as error:
        raise DriveError(f"{args.id}: no freeze set: {error}") from None
    def incident_freeze_write(data: dict):
        order = data["orders"][args.id]
        order["note"] = "; ".join(filter(None, [order["note"], f"freeze {freeze}"]))
        order["updated"] = state.stamp()

    state.update(name, None, incident_freeze_write)
    return emit(
        args,
        f"{args.id}: {', '.join(args.components)} frozen until {render.when(until)} as {freeze}; the job that merges the root fix lifts it"
        f" with {release.unfreeze(freeze) or 'the release tool'} before the fix's build starts",
        {"id": args.id, "freeze": freeze, "until": until},
    )


def his_message(source: str) -> tuple[str, str, str | None] | None:
    """The Slack message --from names, as state.slack_message reads it, refused when it names Slack in no form that reads; None for a source outside Slack, such as terminal."""
    if (message := state.slack_message(source)) or not (source.startswith("slack:") or "/archives/" in source):
        return message
    raise DriveError(f"--from {source}: refused, name his message by its permalink, a reply's carrying thread_ts, as slack:<channel>/<thread ts>/<his reply ts>, or as slack:<channel>/<ts>")


def slack_key(source: str, message: tuple[str, str, str | None] | None) -> str:
    """Where an order came from as the drive keeps it: slack:<channel>/<ts> of his Slack message, the key state.message_order finds it by, else the source as given."""
    return f"slack:{message[0]}/{message[1]}" if message else source


def order_thread(data: dict, source: str, message: tuple[str, str, str | None] | None, flag: str | None) -> tuple[str, str] | None:
    """An order's own thread: the one --thread names, checked, else the thread of the message --from names, which its permalink and slack:<channel>/<thread ts>/<his reply ts> carry, so only slack:<channel>/<ts> asks Slack which thread it is in; None for an order from outside Slack with no --thread."""
    if flag:
        return check_thread(data, flag, message)
    if not message:
        return None
    if message[2]:
        return message[0], message[2]
    try:
        return message[0], slack.thread_of(message[0], message[1])
    except slack.SlackError as error:
        raise DriveError(
            f"--from {source}: refused, Slack cannot read the message it names: {error}; pass its permalink, a reply's carrying thread_ts,"
            " or slack:<channel>/<thread ts>/<his reply ts>, which name its thread with no Slack read"
        ) from None


def reporters(args: argparse.Namespace, message: tuple[str, str, str | None] | None, thread: tuple[str, str] | None) -> list[dict]:
    """The reporters an order he gave keeps with no --reporter, one per thread: each message his --from message shares that a person wrote, outside the order's own thread, and the thread a teammate started that his message is in; none on a watch order or one from outside Slack."""
    if args.watch or not message:
        return []
    shared = [reporter for reporter in shared_reports(args.source, message) if (reporter["channel"], reporter["thread"]) != thread]
    teammate = teammate_report(args, message, thread)
    held: dict[tuple[str, str], dict] = {}
    for reporter in [*shared, *([teammate] if teammate else [])]:
        held.setdefault((reporter["channel"], reporter["thread"]), reporter)
    return list(held.values())


def shared_reports(source: str, message: tuple[str, str, str | None]) -> list[dict]:
    """The reporter of each message his message shares that a person wrote, by the from_user, from_claude and from_bot cc-slack reads on each share, so its thread hears only its one note, by his pick "Drive posts only the fixed note in a reporter's thread, so the hook's one-pass rule always takes it"; a share with no from_claude, from a cc-slack that never says who wrote one, is no one's report."""
    try:
        shared = slack.shares(message[0], message[1])
    except slack.SlackError as error:
        raise DriveError(
            f"{source}: refused, Slack cannot read the message to tell whose messages it shares: {error}; try again once Slack answers,"
            " or pass drive order add --reporter <the shared message's permalink>"
        ) from None
    return [state.reporter(share["permalink"]) for share in shared if "from_claude" in share and slack.by_person(share)]


def teammate_report(args: argparse.Namespace, message: tuple[str, str, str | None], thread: tuple[str, str] | None) -> dict | None:
    """The reporter of an order he gave in a thread a teammate started outside his DM, that thread, which then hears only its one note while the order's lines go to his DM, by his pick "Drive posts only the fixed note in a reporter's thread, so the hook's one-pass rule always takes it"; None when --thread names where its lines go, on an answer, incident or joining order, and in a thread he, the drive or a bot started."""
    if args.thread or args.incident or args.joins or args.kind == "answer" or not thread or thread[0] == slack.dm() or thread[1] == message[1]:
        return None
    try:
        root = slack.root(*thread)
    except slack.SlackError as error:
        raise DriveError(
            f"--from {args.source}: refused, Slack cannot read the thread it is in to tell whether a teammate reported a bug there: {error};"
            " pass --reporter slack:<channel>/<thread ts> for a teammate's report, or --thread <its permalink> for a thread its lines go in"
        ) from None
    if not root or not slack.by_person(root):
        return None
    return state.reporter(f"slack:{thread[0]}/{thread[1]}")


def check_thread(data: dict, flag: str, message: tuple[str, str, str | None] | None) -> tuple[str, str]:
    """The channel and thread --thread names, by permalink, slack:<channel>/<ts>, or a bare ts in the channel of the message the order came from, his DM for one from outside Slack; refused when Slack reads no thread at that ts there, naming the thread a reply is in. The thread of the message the order came from, which --from already names, needs no read."""
    if SLACK_TS.fullmatch(flag):
        where = (message[0] if message else slack.dm(), flag)
    elif not (where := state.link_thread(flag)):
        raise DriveError(f"--thread {flag}: refused, name the thread by its permalink, as slack:<channel>/<ts>, or by its ts in the channel of the --from message")
    if message and where == (message[0], message[2]):
        return where
    try:
        root = slack.thread_of(*where)
    except slack.SlackError as error:
        raise DriveError(f"--thread {flag}: refused, it is no thread in {where[0]}, where the order's lines go: {error}; name a thread elsewhere by its permalink") from None
    if root != where[1]:
        raise DriveError(f"--thread {flag}: refused, it is a reply in thread {root}; pass --thread {root}")
    return where


def filing(data: dict, words: str, project: str | None, workstream: str | None, refused: str) -> tuple[str, str]:
    """The project and workstream the dashboard's Now screen files an order under: on a "Drive tooling:" order The drive and Drive tooling unless a flag names another, and on his order both named, refused naming the pairs in use, and refused by `one_home` for a name filing its subject elsewhere."""
    if words.startswith(state.TOOLING):
        project, workstream = project or TOOLING_FILING[0], workstream or TOOLING_FILING[1]
    if project and workstream:
        one_home(data, project, workstream)
        return project, workstream
    missing = " and ".join(f"--{field}" for field, value in (("project", project), ("workstream", workstream)) if not value)
    raise DriveError(f"{refused}: name {missing}, which file his order on the dashboard's Now screen, reusing a pair in use where it fits: {pairs_in_use(data)}")


def unfiled_refusal(data: dict, order_id: str) -> str | None:
    """Why say may not close his answer order yet: it is filed under no project and workstream, which the communicator sets from the card or order his question is about."""
    order = find(data["orders"], order_id, "order")
    if order["project"] and order["workstream"]:
        return None
    return (
        f"{order_id} is filed under no project and workstream yet: run drive order set {order_id} --project <name> --workstream <name>"
        f" with the pair of the card or order his question is about, reusing a pair in use where it fits: {pairs_in_use(data)}"
    )


def pairs_in_use(data: dict) -> str:
    return "; ".join(f"{named} / {stream}" for named, stream in filing_pairs(data)) or "none yet"


def one_home(data: dict, project: str, workstream: str, but: str | None = None) -> None:
    """Refuse a project or workstream naming a subject that other orders file only elsewhere, as the other of the two or in another spelling, ignoring case, since the dashboard's filters and Now tree and the coordinator session an order's project routes it to each match the two names exactly, so a subject with two homes shows whole under neither; a name other orders use in the same place passes, so a refile moves orders into the home it keeps, and a name written as a pair is refused."""
    for flag, name, place in (("--project", project, 0), ("--workstream", workstream, 1)):
        if PAIR_MARK.search(name):
            raise DriveError(f"{flag} {name}: refused, nothing filed: a project or workstream is one name, never a pair written with / › or >; name each with --project and --workstream")
        found = homes(data, name, but)
        if found and all(pair[place] != name for pair in found):
            raise DriveError(
                f"{flag} {name}: refused, nothing filed: other orders file this subject as {'; '.join(offers(found, name))},"
                " and a subject keeps one home, which the dashboard's filters, its Now tree and the coordinator an order's project routes it"
                " to each match exactly; name the subject as they do, or use names no other order uses"
            )


def homes(data: dict, name: str, but: str | None = None) -> list[tuple[str, str]]:
    """Each pair in use, but the named order's, that files the subject a name calls, as its project or its workstream, ignoring case."""
    return [pair for pair in filing_pairs(data, but) if name.casefold() in (pair[0].casefold(), pair[1].casefold())]


def offers(pairs: list[tuple[str, str]], name: str) -> list[str]:
    """The flags filing a subject as these pairs do: its project alone where the name is the project, else the pair."""
    return list(dict.fromkeys(f"--project {project}" if project.casefold() == name.casefold() else f"--project {project} --workstream {stream}" for project, stream in pairs))


def titled(title: str) -> str:
    """The short title the dashboard names an order by, refused when blank, over TITLE_CHARS, or carrying a link or a second line, since his words stay whole in the task."""
    if not title.strip() or len(title) > TITLE_CHARS or "\n" in title or re.search(r"https?://", title):
        raise DriveError(f"--title {json.dumps(title, ensure_ascii=False)}: refused, a title is a few words on one line, at most {TITLE_CHARS} characters and no link, naming his order on the dashboard while his words stay whole in its task")
    return title.strip()


def find(records: dict, key: str, label: str) -> dict:
    if key not in records:
        raise DriveError(f"no {label} {key}")
    return records[key]


def named_file(source: str, what: str) -> Path:
    path = Path(source).expanduser()
    if not path.is_file():
        raise DriveError(f"no {what} at {path.absolute()}")
    return path


def named_text(source: str, what: str) -> str:
    return sys.stdin.read() if source == "-" else named_file(source, what).read_text()


def his_words(link: str, deed: str) -> str:
    if not slack.message(link).get("from_user"):
        raise DriveError(f"{link} is not his message; only his own words {deed}")
    return link


def drive_post_at(link: str, data: dict, order_id: str, sent: str | None = None, named: str | None = None) -> tuple[dict, bool, tuple[str, str] | None]:
    """The drive's post as Slack holds it, or, for one say just made, from the words it was sent with, reading nothing; whether it is a line to him, as talk.to_him reads where it went, in the channel_id say named, a person's user id for the group DM of that person, him and the bot, else the channel its link names; and its thread when the order holds it not yet, which the call naming the order joins to the order's threads: a post at the root of his DM or of a channel the drive keeps on an order whose lines go in no thread, one with no thread or whose own thread holds no line to him, is the thread its next lines reply under, and a post say made at the root of another channel, such as a staging claim, the thread that channel's members reply under."""
    from drive import talk

    posted = slack.posted(link, sent) if sent else slack.message(link)
    if not posted.get("from_claude"):
        raise DriveError(f"{link} is not the drive's post")
    where = (state.CARD_LINK.search(link)[1], posted.get("thread_ts"))
    order = data["orders"][order_id]
    ours = talk.to_him(data, named or where[0], where[1])
    if not where[1] and (named and not ours or where[0] in rooms(data) and not state.promise_thread(data, order)):
        where = (where[0], posted["ts"])
    gained = where if where[1] and where not in slack.order_threads(data, order_id, order) else None
    return posted, ours, gained


def rooms(data: dict) -> set[str]:
    """His DM and each channel the drive keeps, where a post at the root starts the thread of an order whose lines go in none."""
    return {slack.dm(), *(made["id"] for made in data["slack"]["channels"].values())}


def pick_parts(order_id: str, order: dict, named: list[str]) -> list[str]:
    listed = quoted_parts(order["parts"])
    if not named:
        if len(order["parts"]) != 1:
            raise DriveError(f"{order_id} has parts {listed}; name the one this covers with --part")
        return list(order["parts"])
    if unknown := [part for part in named if part not in order["parts"]]:
        adds = " ".join(f"--add-part {shlex.quote(part)}" for part in dict.fromkeys(unknown))
        raise DriveError(f"{order_id} has no part {quoted_parts(unknown)}; its parts are {listed}: name one as it stands, or add a new one first with drive order set {order_id} {adds}")
    return list(dict.fromkeys(named))


def quoted_parts(parts: Iterable[str]) -> str:
    """Part names as JSON strings, so a name holding a quote, a comma or a semicolon reads apart from the next."""
    return ", ".join(json.dumps(part, ensure_ascii=False) for part in parts)


def change_parts(name: str, data: dict, order_id: str, added: list[str], dropped: list[str]) -> list[str]:
    """Add and take off an order's parts one name a flag, so no call retypes a part it leaves alone, and each part it keeps keeps its posted mark, design and the jobs naming it, a dropped one leaving every job and PR too, by state.drop_part: an add is refused for a name the order holds, a drop for a name it does not hold, for its every part, which a close takes off, for a `staging test of #N before it lands` part naming an open PR, which only his answer lets merge before that test, by his words a9c9edc, and for a change a design listed that no builder delivered, which only his answer drops, unless its design's newest DESIGN no longer lists it and no work on it began, by state.unlisted_changes. A line for each live job a drop left with no part, which it stopped, naming each text the stop dropped."""
    parts = data["orders"][order_id]["parts"]
    if held := [part for part in added if part in parts]:
        raise DriveError(f"{order_id}: refused, its parts stay as they are: it holds {quoted_parts(held)} already, so --add-part adds none; name it as it stands with --part")
    if dropped:
        pick_parts(order_id, data["orders"][order_id], dropped)
    if not added and set(parts) <= set(dropped):
        raise DriveError(f"{order_id}: refused, its parts stay as they are: --drop-part takes off its every part, which closes it: drive order set {order_id} --state done or dropped")
    if tested := [part for part in dropped if state.staged_open(data, part)]:
        raise DriveError(
            f"{order_id}: refused, its parts stay as they are: --drop-part takes off {quoted_parts(tested)}, the staging test {', '.join(key for part in tested for key in state.staged_open(data, part))} waits on before it merges,"
            f" which only his answer lifts, by his words {state.TESTED_FIRST_RULING}: drive answer <his answer> --dropped {order_id}:<the part>"
        )
    if designed := [
        f'{json.dumps(part, ensure_ascii=False)} of {design}\'s design'
        for part in dropped
        if (design := parts[part]["design"]) and data["jobs"][design]["kind"] == "design" and not state.delivered(data, order_id, part) and part not in state.unlisted_changes(name, data, design)
    ]:
        raise DriveError(
            f"{order_id}: refused, its parts stay as they are: --drop-part takes off {', '.join(designed)}, which no builder delivered;"
            f" a design's change leaves once its builder delivers it, or by his answer, drive answer <his answer> --dropped {order_id}:<the part>,"
            " which takes it off every job and PR too"
        )
    state.split_research(data, order_id, added, dropped)
    parts |= {part: state.new_part() for part in added}
    stopped = {job_id: held for part in dropped for job_id, held in state.drop_part(data, order_id, part, f"drive order set {order_id} --drop-part took off every part it delivered")[1].items()}
    return [superseded.stopped_line(f"{job_id} stopped, left with no part: end its agent, or its lane with drive job end {job_id}", held) for job_id, held in stopped.items()]


def repeated_lines(data: dict, order_id: str, parts: list[str]) -> list[str]:
    """A line for each undelivered part of another open order and each open drive PR that Jev reads as already making the change a new part of the order names, by settled.same_changes, ending on the drive status that prints it, so a part added beside work already under way shows that work before a job starts on it; a line saying Jev gave no reading, since the add stands either way."""
    lines = []
    for part in parts:
        try:
            found = settled.same_changes(data, order_id, part)
        except settled.Unheard as failure:
            lines.append(f'Jev gave no reading of whether open work already makes {order_id} "{part}" ({failure}): drive status <its words> finds the parts and PR titles holding them')
            continue
        for key in found:
            if pr := data["prs"].get(key):
                lines.append(f'{order_id} "{part}" repeats {key} "{pr["title"]}", open on {pr["order"]}, as Jev reads it: drive status {key}')
                continue
            other_id, _, other_part = key.partition(":")
            lines.append(f"{order_id} \"{part}\" repeats {other_id}'s open part {render.part_line(data, other_id, other_part)}, as Jev reads it: drive status {other_id}")
    return lines


def pick_job_parts(name: str, data: dict, order_id: str, named: list[str]) -> list[str]:
    """The parts a job takes on the order, each `port of #N` it names added to the order by change_parts when missing, so each old-tree port is a change of its own: its builder starts beside the builders of other ports, and its part is delivered, or flagged once its builder stops, on its own."""
    change_parts(name, data, order_id, [part for part in dict.fromkeys(named) if state.PORT_PART.fullmatch(part) and part not in data["orders"][order_id]["parts"]], [])
    return pick_parts(order_id, data["orders"][order_id], named)


def apart_from_filing(order: dict) -> str:
    """The order apart from its project, workstream and title, so filing or titling it alone leaves the time it last moved, which the dashboard reads as when a done order went live."""
    return json.dumps({field: value for field, value in order.items() if field not in FILING}, sort_keys=True)


def refuse_unquotable_parts(flag: str, parts: list[str]) -> None:
    if unquotable := next((part for part in parts if state.unquotable_part(part)), None):
        raise DriveError(
            f"{flag} {json.dumps(unquotable, ensure_ascii=False)}: refused, a part is named in at most {state.PART_CHARS} characters with no backtick,"
            ' double quote, $ or backslash, the words a design\'s Change: line names its change in and drive job add builder --part "<the part>" takes;'
            " its detail goes on the job's --goal"
        )


def closed_prs(name: str, order_id: str, keys: list[str], why: str) -> list[str]:
    """Close on GitHub each open PR a dropped order leaves with no live order, its comment the drop's reason, so no PR outlives the order it served; the line of each, a close GitHub refused naming its error."""
    lines = []
    for key in keys:
        try:
            github.close(state.pr_url(key), f"Closed by the drive: {why}.")
        except github.GithubError as error:
            lines.append(f"{key} not closed: {error}; close it with gh pr close {state.pr_url(key)}")
            continue
        def closed_prs_write(data: dict):
            data["prs"][key]["state"] = "closed"

        state.update(name, None, closed_prs_write)
        lines.append(f"{key} closed with {order_id}: {why}")
    return lines


def order_set(args: argparse.Namespace) -> int:
    """Change an order, reworking only the PRs --rework names and naming each open PR card of his in a thread it moves into or is sorted again in, which it leaves up; --add-part refuses a name a part cannot hold, by state.unquotable_part, and --add-part and --drop-part change only the parts they name, by change_parts, and --add-part prints each open part of another order and open PR Jev reads as already making a new part's change, by repeated_lines; closing one done refuses while the bug report it fixes has not heard it is fixed, and --no-options-only, his pick, adds the builder of each change a design on it listed that no builder is on, as drive job report does once a design returns; --incident turns an open order into an incident by turned_incident and names each open question card of his its words point at, by state.asked_lines; --kind answer --part takes each part off each done builder, codex or design job holding it, whose hold would keep it work he ordered, and delivers it by the post one's POSTED line names when that answer reaches him only by a post, saying so by answered_line; --standing keeps each --part standing on its goal, or takes it out with an empty one, by standing_parts, which --part needs no --kind for; a change to its parts, kind, standing parts, options-only mark or incident mark from a session of the drive that does not hold the order is refused before anything is written, by state.held_elsewhere, the check every job verb makes, but for the communicator and its handlers, which file his words onto any order's parts and kind and are refused only the incident mark. --unjoin takes it off the order it joined, by state.unjoin, filed under its own project and workstream by filing, from any session, since it changes no part. --priority puts its work first at each admission point, by state.first_in_line, and gives it a coordinator session of its own, by seat_started, from any session; a close, a drop or --no-priority retires each priority seat no open order holds any longer in the same step, by seats_retired."""
    name = state.resolve(args.drive)
    unreported = his_words(args.no_reporter, "take the reporters off an order") if args.no_reporter else None
    reworks = heard_reworks(name, args.rework)
    data = state.load(name)
    order = find(data["orders"], args.id, "order")
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    communicating = state.session_role(data, session) == "communicator"
    shaping = args.incident is not None or not communicating and (args.add_part or args.drop_part or args.kind or args.standing is not None or args.options_only is not None or args.fresh is not None)
    if shaping and (refused := state.held_elsewhere(data, args.id, session)):
        raise DriveError(f"{args.id}: refused, nothing written: {refused}")
    refuse_unquotable_parts("--add-part", args.add_part)
    added = state.reporter(args.reporter) if args.reporter else None
    held = {(kept["channel"], kept["thread"]) for kept in order["reporters"]}
    reporters = [] if unreported else [*order["reporters"], added] if added and (added["channel"], added["thread"]) not in held else order["reporters"]
    watched = state.new_watch(args.watch, state.now()) if args.watch else None
    followed = follow_ask(data, args.id, *args.follow, args.part) if args.follow else None
    if args.state == "done" and state.untold_reporters(data, order | {"reporters": reporters}):
        raise DriveError(untold(data, args.id, order | {"reporters": reporters}))
    if args.words is not None:
        if not (order["text"].startswith(state.TOOLING) and args.words.startswith(state.TOOLING)):
            raise DriveError(f'--words: refused, only a "{state.TOOLING}" order\'s words change, to words that open "{state.TOOLING}"; an order of his keeps his words verbatim')
    if args.proof is not None:
        check_proof(args.proof)
    if args.title is not None:
        titled(args.title)
    if args.unjoin and not order["joins"]:
        raise DriveError(f"--unjoin: refused, nothing written: {args.id} joins no order")
    if args.unjoin:
        refused = f"{args.id}: refused, nothing written: --unjoin files it under its own project and workstream, never {order['project']} / {order['workstream']} it took from {order['joins']}"
        args.project, args.workstream = filing(data, order["text"], args.project, args.workstream, refused)
    alerts = alert_links(args.alerts)
    if args.standing_check is not None and args.standing is None:
        raise DriveError("--standing-check: refused, give it with --standing and the part's goal, since a check is how a standing part's passes run")
    standing = standing_parts(data, args.id, args.add_part, args.drop_part, args.part, args.standing, args.standing_check) if args.standing is not None else []
    sorted_parts = sorted_as(data, args.id, args.kind, args.part if args.kind or args.standing is None and args.urgent is None and not followed else [])
    if (order["incident"] if args.incident is None else args.incident) and (order["options_only"] if args.options_only is None else args.options_only):
        raise DriveError(
            f"{args.id}: refused, an incident order is never limited to options, since its codex jobs start at the alert, before he picks:"
            " an incident takes --no-options-only, and an order limited to options --no-incident"
        )
    proof = args.proof or (order["proof"] if args.state == "done" else None)
    if proof and (seen := brief.unseen(data, order, proof)):
        raise DriveError(
            f"{'--proof' if args.proof else 'its proof'} {proof}: refused, {args.id} builds something he uses ({seen}), so it is proven"
            " done on that surface: a recording of each step through it (agent-browser's recording, or screencapture -v of the window)"
            " or the link of the live post, or, on a command, the saved output of its run; data fetched from an API or another command's output"
            " does not show what he sees"
            + ("" if args.proof else "; close it with --proof <that recording or link>")
        )
    message = his_message(args.source) if args.source else state.slack_message(order["from"])
    thread = check_thread(data, args.thread, message) if args.thread else None
    dropping = args.state == "dropped" and order["state"] not in state.CLOSED_ORDER_STATES
    closing = state.order_open_prs(data, args.id) if dropping else []
    if landing := [key for key in closing if (data["prs"][key]["stage"] or {}).get("name") == "landing"]:
        raise DriveError(f"{args.id}: refused, nothing changed: {', '.join(landing)} {'is' if len(landing) == 1 else 'are'} queued to land, so dropping {args.id} would close a PR mid-merge: let it land or dequeue it first")
    if not dropping and args.state in state.CLOSED_ORDER_STATES and order["state"] not in state.CLOSED_ORDER_STATES and (untested := state.dangerous_staged(data, args.id)):
        raise DriveError(
            f"{args.id}: refused, {', '.join(untested)} {'waits' if len(untested) == 1 else 'wait'} on {args.id}'s staging test, staged because {'its head changes' if len(untested) == 1 else 'their heads change'} a dangerous class of file,"
            f" and closing {args.id} frees that wait, so {'it' if len(untested) == 1 else 'they'} would land untested: let the test pass, or move the wait onto another order's part with drive pr set <url> --waits"
        )
    report = need_record_close(data, args.id, args.record, args.state == "dropped" and bool(args.note)) if args.state in state.CLOSED_ORDER_STATES else {}
    changes = {
        "text": args.words,
        "from": args.source and slack_key(args.source, message),
        "note": args.note,
        "proof": args.proof,
        "thread": thread and list(thread),
        "options_only": args.options_only,
        "once": args.once,
        "every": args.every,
        "watch": watched,
    }
    becoming = order | {field: value for field, value in changes.items() if value is not None} | {field: (getattr(args, field) or "").strip() or None for field in FILING if getattr(args, field) is not None}
    if args.unjoin and becoming["note"] == data["orders"][order["joins"]]["note"]:
        becoming["note"] = None
    record = incident_record(name, args.id, becoming) if args.incident and not order["incident"] else None
    moment = state.now()

    def order_changed(data: dict):
        order = data["orders"][args.id]
        before = apart_from_filing(order)
        aimed = (order["kind"], order["thread"])
        limited = order["options_only"]
        order.update({field: value for field, value in changes.items() if value is not None})
        if args.proof:
            order["proof_by"] = None
        designs = [job_id for job_id in order["jobs"] if data["jobs"][job_id]["kind"] == "design" and data["jobs"][job_id]["order"] == args.id] if limited and not order["options_only"] else []
        added = [builder for job_id in designs for builder in state.add_changes(name, data, job_id, next(iter(state.returns(name, job_id)), {"body": ""})["body"])]
        if args.state in state.CLOSED_ORDER_STATES:
            order["closed_on"] = report.get("seq")
        elif args.state and order["state"] in state.CLOSED_ORDER_STATES:
            order["closed_on"] = None
        was_open = order["state"] not in state.CLOSED_ORDER_STATES
        if args.state:
            order["state"] = args.state
        order["alerts"] = list(dict.fromkeys([*order["alerts"], *alerts]))
        stopped = change_parts(name, data, args.id, list(dict.fromkeys(args.add_part)), list(dict.fromkeys(args.drop_part)))
        if was_open and args.state in state.CLOSED_ORDER_STATES:
            uncarried, ended = state.close_order_jobs(data, args.id, f"stopped: drive order set {args.id} --state {args.state} closed its order")
            stopped += [f"{job_id} carries {args.id} no more and keeps working on its own order" for job_id in uncarried]
            stopped += [superseded.stopped_line(f"{job_id} stopped with its order: end its agent, or its lane with drive job end {job_id}", held) for job_id, held in ended.items()]
        for part in standing:
            order["parts"][part]["standing"] = args.standing.strip() or None
            order["parts"][part]["check"] = state.new_check(args.standing_check) if args.standing_check and args.standing.strip() else None
        turning = args.incident and not order["incident"]
        if args.incident is not None:
            order["incident"] = args.incident
        if followed:
            order["parts"][followed[0]].update(follow=followed[1], posted=None)
        for part in sorted_parts:
            order["parts"][part]["answer"] = args.kind == "answer"
        answered = set(sorted_parts or order["parts"]) if args.kind == "answer" else set()
        for job in [data["jobs"][job_id] for job_id in order["jobs"]]:
            if job["order"] == args.id and answered & set(job["parts"]) and state.unstarted_builder(data, job):
                left = [part for part in job["parts"] if part not in answered]
                job.update({"parts": left} if left else {"state": "stopped", "said": f"stopped: drive order set {args.id} --kind answer left it nothing to build"}, updated=state.stamp(moment))
        taken = {part: [job_id for job_id, job in state.part_jobs(data, args.id, part) if job["kind"] in state.WORK_KINDS and job["state"] == "done"] for part in sorted_parts} if args.kind == "answer" else {}
        for part, job_ids in taken.items():
            held = order["parts"][part]
            for job_id in job_ids:
                if not (held["posted"] or state.following(held)) and (said := state.POSTED_LINE.match(data["jobs"][job_id]["said"] or "")) and state.unposted_answer(data, job_id):
                    held["posted"] = said["link"]
                state.take_part_off(data["jobs"][job_id], args.id, part)
        if args.kind == "build" and sorted_parts and order["kind"] == "answer":
            state.sort_as_build(order, sorted_parts, True)
        elif args.kind and not sorted_parts:
            order["kind"] = args.kind
        if args.complex is not None:
            order["complex"] = args.complex
        if args.urgent is not None:
            if not args.part or (unknown := [part for part in args.part if part not in order["parts"]]):
                raise DriveError(f"{args.id}: refused, nothing written: --urgent marks each --part of the order, and {'none was named' if not args.part else 'it has no part ' + repr(unknown[0])}")
            order["urgent"] = [part for part in order["urgent"] if part not in args.part] + (args.part if args.urgent else [])
        if args.priority is not None:
            if args.priority and state.unasked_tooling(order):
                raise DriveError(f"{args.id} is drive tooling no message of his asked for, so it takes no priority seat; drive job set <j> --priority starts one of its jobs past the caps when it blocks his answers, a handler, a post, his DM path or this Mac's load")
            order["priority"] = state.priority_seat(args.id) if args.priority else None
        for term in args.term or []:
            word, _, meaning = term.partition("=")
            if not word.strip():
                raise DriveError(f"--term {term!r}: name the term before its =, as 'Release 2=everything but SandSQL; waits on its staging test'")
            if meaning.strip():
                order["terms"][word.strip()] = meaning.strip()
            else:
                order["terms"].pop(word.strip(), None)
        if args.promote:
            if not state.on_backlog(data, args.id):
                raise DriveError(f"--promote: refused, nothing written: {args.id} is not on the drive tooling backlog, which holds only drive tooling a session filed with no job yet")
            state.promote(order, args.promote, state.now())
        if args.fresh is False:
            order["fresh"] = None
        if args.reporter or unreported:
            order["reporters"] = reporters
        if args.users is not None:
            order["users"] = args.users
        unjoined = unjoined_lines(args.id, *state.unjoin(data, args.id)) if args.unjoin else []
        filed = [field for field in FILING if getattr(args, field) is not None]
        for field in filed:
            order[field] = (getattr(args, field) or "").strip() or None
        if {"project", "workstream"} & set(filed) and order["project"] and order["workstream"]:
            one_home(data, order["project"], order["workstream"], args.id)
        turned = turned_incident(name, data, args.id, state.stamp(moment), record) if turning else []
        if not filed or args.lined or apart_from_filing(order) != before:
            order["updated"] = state.stamp(moment)
        if args.lined:
            order["last_line"] = order["updated"]
        return data, order, aimed, added, stopped, turning, taken, turned, unjoined

    data, order, aimed, added, stopped, turning, taken, turned, unjoined = state.update(name, None, order_changed)
    restarted = fresh_marked(name, [args.id], order["text"], order["from"]) if args.fresh else []
    cards = thread_cards(name, order) if args.rework or (order["kind"], order["thread"]) != aimed else []
    reworked = rework_named_prs(name, args.id, reworks, cards, f"drive order set {args.id} --rework <PR>=<his permalink>")
    answered_lines = {part: answered_line(data, args.id, part, job_ids) for part, job_ids in taken.items()}
    parts = [answered_lines.get(part) or f"{args.id} {render.part_line(data, args.id, part)}" for part in dict.fromkeys([*sorted_parts, *standing, *([followed[0]] if followed else [])])]
    turned += state.asked_lines(data, order["text"]) if turning else []
    repeated = repeated_lines(data, args.id, list(dict.fromkeys(args.add_part)))
    if args.priority and args.dry_run:
        seated = [said for said in (seat_started(name, order["priority"]), communicator_started(name, order["priority"])) if said]
    elif args.priority:
        state.detach(name, ["seat-start", args.id], SEATS_LOG)
        seated = [f"starting {order['priority']} apart from this call: its sub-coordinator, its communicator with its channel, and its page, each reporting as a record to root and {state.project_lane(order['priority'])}, in {SEATS_LOG}"]
    else:
        seated = []
    retired = list(seats_retired(name).values()) if args.state in state.CLOSED_ORDER_STATES or args.priority is False else []
    retired += closed_prs(name, args.id, closing, args.note or f"its order {args.id} was dropped") if closing and not args.dry_run else []
    return emit(args, render.order_line(data, args.id, order) + "".join(f"\n{line}" for line in [*turned, *unjoined, *parts, *stopped, *restarted, *repeated, *render.builder_lines(name, data, added), *reworked, *seated, *retired]), {"id": args.id, **(state.load(name)["orders"][args.id] if restarted else order)})


SEATS_LOG = "seats.log"


def seat_start(args: argparse.Namespace) -> int:
    """Start a priority order's seat apart from the drive order set --priority that marked it, so that call returns in seconds, by his words on o2296 "20 mins later no channel, no progress on this": its sub-coordinator, then its communicator, which makes its channel, then its page, each line posted as its own record to the top coordinator and the seat's lane, a failure's as one too."""
    name = state.resolve(args.drive)
    lines = seat_lines(name, args.id)
    return emit(args, "\n".join(lines), {"lines": lines})


def seat_lines(name: str, order_id: str) -> list[str]:
    seat = state.load(name)["orders"][order_id]["priority"]
    lines = []
    for start in (lambda: seat_started(name, seat), lambda: communicator_started(name, seat), lambda: priority_page(name, order_id, state.load(name)["orders"][order_id])):
        try:
            said = start()
        except (DriveError, reads.ReadError, orca.OrcaError, slack.SlackError) as error:
            said = f"FAILED starting part of {seat}: {error}"
        if said:
            lines.append(said)
            seat_record(name, seat, order_id, said)
    return lines


def seat_record(name: str, seat: str, order_id: str, line: str) -> None:
    reads.run(
        ["cci", "post", "--drive", name, "--lane", "watch", "--kind", "note", "--to", f"root@{name}", "--to", f"{state.project_lane(seat)}@{name}", "--topic", f"seat-{order_id}", "--text", line],
        Path.cwd(),
        reads.posting(),
    )


def priority_page(name: str, order_id: str, order: dict) -> str:
    """Make the applet page of an order marked priority, by his words that one "should always be created for a priority project", keeping the page an earlier mark made; the line naming its link."""
    from drive import applet, dashboard, view

    title = view.order_title(order)
    slug = next((page["slug"] for page in pages.listed(name) if order_id in page["orders"] and page.get("kind") == applet.KIND), None) or applet.slug_of(title)
    applet.make(name, slug, order_id, f"Everything left to finish {title}, and what needs you", None, "priority")
    return f"its page: {dashboard.link(name)}#/pages/{slug}"


def unjoined_lines(order_id: str, head: str, carriers: list[str], told: list[str]) -> list[str]:
    """What --unjoin did: the jobs of the feature it left that carry the order no more, each worker that took its words before the unjoin, which no text takes back, and the reader its unheld parts now wait on."""
    return [
        f"{order_id} joins {head} no longer: {', '.join(carriers) or 'no job'} carry it no more and hold its words no longer, so its parts are unheld",
        *([f"{', '.join(told)} took its words before the unjoin: tell each worker {order_id} is not its work"] if told else []),
        f"add its reader with drive job add reader --order {order_id}",
    ]


def answered_line(data: dict, order_id: str, part: str, taken: list[str]) -> str:
    """What drive order set --kind answer did to a part: sorted it as an answer, taking it off each done job whose hold kept it work he ordered, then the part as drive status lists it, naming the drive order posted that delivers one no job carries."""
    off = f", taken off {', '.join(data['jobs'][job_id]['kind'] + ' ' + job_id for job_id in taken)}, done, whose hold kept it work he ordered" if taken else ""
    held = render.part_line(data, order_id, part).removeprefix(f'"{part}": ')
    if held == "no job carries it":
        held += f", so drive order posted {order_id} <the drive's post that answered it> --part {shlex.quote(part)} delivers it"
    return f'{order_id} "{part}": sorted as an answer{off}; {held}'


def follow_ask(data: dict, order_id: str, build: str, ask: str, named: list[str]) -> tuple[str, dict]:
    """The part his ask to be kept updated on a Buildkite build wants followed, and the follow the watch keeps of it, refused for a link that is no build, a message that is not his, an ask outside his DM, where the watch rewrites its own status message with no ticket, more than one part, since one end line delivers one, and work he ordered, which no post delivers."""
    if not github.BUILD.fullmatch(build):
        raise DriveError(f"--follow {build}: refused, name the build by its Buildkite link, https://buildkite.com/<org>/<pipeline>/builds/<number>")
    channel, thread = state.link_thread(his_words(ask, "ask to be kept updated on a build"))
    if channel != slack.dm():
        raise DriveError(f"--follow {ask}: refused, the watch keeps a build's status message current only in his DM, where rewriting its own post needs no ticket; name his ask there")
    if len(named) > 1:
        raise DriveError(f"--follow: refused, the build's end line delivers one part of {order_id}, so name one --part")
    [part] = pick_parts(order_id, data["orders"][order_id], named)
    if state.work(data, order_id, part):
        raise DriveError(
            f'--follow: refused, "{part}" of {order_id} is work he ordered, which its builder\'s DONE or a merged PR delivers, never the end line of a build:'
            f' sort the part asking to be kept updated as words he reads first, with drive order set {order_id} --kind answer --part "{part}"'
        )
    return part, {"build": build, "thread": thread, "message": None, "words": None, "ended": None, "record": None}


def standing_parts(data: dict, order_id: str, added: list[str], dropped: list[str], named: list[str], goal: str, check: str | None) -> list[str]:
    """The parts --standing keeps standing on its goal, or takes out with an empty one: each --part names among the order's parts as --add-part and --drop-part leave them, refused without --part, --standing-check refused with an empty goal, and, kept standing with no check, wherever brief.job_refusal refuses the codex job each pass of it is; a part kept standing on a check runs no codex pass, so that refusal never holds it."""
    if not named:
        raise DriveError(f"--standing: refused, name each part of {order_id} it keeps standing with --part")
    if check is not None and not goal.strip():
        raise DriveError("--standing-check: refused, a check keeps a part standing, so give it the part's goal with --standing")
    order = data["orders"][order_id]
    picked = pick_parts(order_id, order | {"parts": {part: None for part in order["parts"] if part not in dropped} | dict.fromkeys(added)}, named)
    if goal.strip() and not check and (refused := brief.job_refusal(data, order_id, "codex", picked)):
        raise DriveError(f"--standing: refused, no part kept standing, since each pass of a standing part is a codex job: {refused}")
    return picked


def sorted_as(data: dict, order_id: str, kind: str | None, named: list[str]) -> list[str]:
    """The parts --part sorts as --kind names, refused without --kind and, as an answer, while work is under way on one: an unfinished builder, codex or design job on it, but a builder still waiting on research with no agent, which the answer stops; a builder stopped before any agent took it while the PRs the drive does not track that it holds carry the part; or an open drive PR of a job on it. A job that finished holding none carries nothing under way."""
    if not named:
        return []
    if not kind:
        raise DriveError(f'--part: refused, it sorts a part of {order_id} with --kind answer, or --kind build to make it work again')
    parts = pick_parts(order_id, data["orders"][order_id], named)
    for part in parts if kind == "answer" else []:
        jobs = dict(state.part_jobs(data, order_id, part))
        working = [
            f"{job['kind']} {job_id}"
            for job_id, job in jobs.items()
            if job["kind"] in state.WORK_KINDS and not state.unstarted_builder(data, job) and (job["state"] not in state.FINISHED_JOB_STATES or state.untracked_carriers(data, job))
        ]
        working += [key for key, pr in data["prs"].items() if pr["job"] in jobs and pr["state"] == "open"]
        if working:
            raise DriveError(f'--part "{part}": refused, {", ".join(working)} works on it, so it is work under way, never an answer: drive job prove delivers it once the proof file its job keeps exits 0')
    return parts


def untold(data: dict, order_id: str, order: dict) -> str:
    """Why an order with reporters may not close done yet: a thread its bug was reported in has not heard it is fixed, naming the order keeping that thread's record when another does, or a thread its question was asked in has not heard the answer."""
    unheard = [f"{kept['link']}{f' (kept by {keeper})' if keeper else ''}" for keeper, kept in map(lambda held: state.kept_record(data, held), order["reporters"]) if not kept["told"]]
    links, there = " and ".join(unheard), "that thread" if len(unheard) == 1 else "each of those threads"
    if order["kind"] == "answer":
        heard = (
            f"its question was asked in {links}, and no note of its answer there is recorded. Once the answer is posted, the"
            f" communicator posts a report on it as the answer in one plain line in {there} with slack_reply"
        )
    else:
        heard = (
            f"its bug was reported in {links}, and no fix-live note there is recorded. Once drive order set {order_id} --proof"
            f" <the check> keeps the proof its fix is live, the communicator posts a report on it as one plain line in {there} with"
            " slack_reply saying what is fixed and since when"
        )
    return (
        f"{order_id}: refused, {heard}, and records it with drive order told {order_id} <its permalink>;"
        " when the bot cannot post in that thread, it posts the note as usual to the reporter's Slack user id, which opens the group DM"
        " of that person, him and the bot, and records it with --group-dm <that user id>."
        f" The coordinator, which posts nothing, sends that report with --attr close={order_id}, and drive order set {order_id}"
        " --no-reporter <his message> takes the reporters off when he says not to tell them"
    )


def check_proof(proof: str) -> None:
    """Refuse a proof naming a link that does not open, as brief.unopened reads it, and one that is a claim: one line naming no link and no file that exists and is not empty, where a check's link, file or pasted output proves it."""
    named = brief.proofs(proof)
    if shut := brief.dead(named):
        raise DriveError(f'--proof "{proof}": refused, a proof\'s links each open as he would open them, and {shut[0]}: link the live post, page or run itself, or name the saved file of the check')
    if "\n" in proof.strip() or any(map(brief.held, named)):
        return
    raise DriveError(
        f'--proof "{proof}": refused, a proof is the check itself, its link, its saved file or its output, never a claim of what it showed:'
        " rerun the check and paste its output, or link its run"
    )


def filing_pairs(data: dict, but: str | None = None) -> list[tuple[str, str]]:
    return sorted({(order["project"], order["workstream"]) for order_id, order in data["orders"].items() if order["project"] and order["workstream"] and order_id != but})


def order_unfiled(args: argparse.Namespace) -> int:
    data = state.load(state.resolve(args.drive))
    unfiled = {order_id: order for order_id, order in state.open_orders(data).items() if not all(order[field] for field in FILING)}
    named = filing_pairs(data)
    lines = [
        f"{order_id} needs {' and '.join(field for field in FILING if not order[field])}: {render.cut(order['text'], 120)}"
        for order_id, order in unfiled.items()
    ]
    names = [f"in use: {project} / {workstream}" for project, workstream in named]
    return emit(args, "\n".join([*lines, *names]) or "every open order names its project, workstream and title", {"unfiled": unfiled, "in_use": named})


def order_pilot(args: argparse.Namespace) -> int:
    """Start or end the reader pilot: a build order filed under the project, added inside the window, marked neither --complex nor holding a design job, starts its builder, designer or codex lane with no reader, by state.readers_piloted."""
    name = state.resolve(args.drive)
    known = {order["project"] for order in state.load(name)["orders"].values()}
    if args.project and args.project not in known:
        raise DriveError(f"{args.project!r} names no project an order is filed under; drive order unfiled lists the ones in use")
    since = state.now().replace(microsecond=0) + timedelta(seconds=1)
    pilot = {"project": args.project, "since": state.stamp(since), "until": state.stamp(since + timedelta(days=args.days))} if args.project else None
    def order_pilot_write(data: dict):
        data["reader_pilot"] = pilot

    state.update(name, None, order_pilot_write)
    line = f"reader pilot on {args.project} from {pilot['since']} until {pilot['until']}" if pilot else "reader pilot off: every build order's lanes start on a reader's research"
    return emit(args, line, {"reader_pilot": pilot})


def order_backlog(args: argparse.Namespace) -> int:
    """Turn the drive tooling backlog, his option A of Nov 15, on or off: on, a Drive tooling order a session files waits on it with no job, a repeat Jev reads as the same defect tallies on it rather than filing again, and it is built by one builder with no reader once promoted, by state.on_backlog; prints what it holds."""
    name = state.resolve(args.drive)
    def order_backlog_write(data: dict):
        data["tooling_backlog"] = args.switch == "on"
        held = [order_id for order_id in data["orders"] if state.on_backlog(data, order_id)]
        return held

    held = state.update(name, None, order_backlog_write)
    line = f"drive tooling backlog on, holding {len(held)} items" if args.switch == "on" else "drive tooling backlog off: a Drive tooling order a session files starts on its reader's research"
    return emit(args, line, {"tooling_backlog": args.switch == "on", "held": held})


def order_check(args: argparse.Namespace) -> int:
    """Run a standing part's check, the pass the watch starts in place of a codex job, with no model session: its shell command in the drive's folder, bound to no session, drive or cci agent by CHECK_UNBOUND, so the records it posts go out as the lane and drive it names, cut off at STANDING_EVERY, its output printed to the log the watch detached it on and its exit code and closing line kept on the part, which the part's gap reads, so a check that fails is said once as its ORPHAN line and one that passes holds the part until its next run."""
    name = state.resolve(args.drive)
    data = state.load(name)
    [part] = pick_parts(args.id, find(data["orders"], args.id, "order"), [args.part])
    if not (check := data["orders"][args.id]["parts"][part]["check"]):
        raise DriveError(f'{args.id} "{part}": refused, it keeps no check: drive order set {args.id} --part {shlex.quote(part)} --standing "<goal>" --standing-check "<command>" keeps one')
    timeout = int(state.STANDING_EVERY.total_seconds())
    unbound = {variable: value for variable, value in os.environ.items() if variable not in CHECK_UNBOUND}
    try:
        done = subprocess.run(["/bin/zsh", "-c", check["command"]], cwd=state.drive_dir(name), env=unbound, capture_output=True, text=True, timeout=timeout, check=False)
        code, output = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as expired:
        code, output = -1, f"{expired.stdout or ''}{expired.stderr or ''}\ntimed out after {timeout // 60} minutes"
    print(output, end="" if output.endswith("\n") else "\n")
    said = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "printed nothing")
    def order_check_write(data: dict):
        data["orders"][args.id]["parts"][part]["check"].update(exit=code, said=render.cut(said, 300))

    state.update(name, None, order_check_write)
    return emit(args, f'{args.id} "{part}": its check exited {code}: {said}', {"id": args.id, "part": part, "exit": code, "said": said})


def need_record_close(data: dict, order_id: str, seq: str | None, dropped_with_note: bool) -> dict:
    """The report that closes the order, read from cci, or none when the order closes without one; refused, as say and order posted refuse it, when the session that wrote it does not hold the order."""
    if seq is None:
        if not dropped_with_note and state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID")) == "communicator":
            raise DriveError(
                f"{order_id}: the communicator closes only an order the report record it posted names in its close attr;"
                " pass --record <that record's seq>, or drop an order no record names with --state dropped --note <why>"
            )
        return {}
    report = report_attrs(seq)
    if refused := report_refusal(data, order_id, report):
        raise DriveError(f"{order_id}: {refused}")
    closes = state.closed_orders(report)
    if not closes:
        raise DriveError(f"record {seq} has no close attr, so it closes no order: a report names each order its post finishes in its close attr")
    if order_id not in closes:
        raise DriveError(f"record {seq} does not close {order_id}: its close attr names only {', '.join(closes)}")
    return report


def report_attrs(seq: str) -> dict:
    """The attrs of a record, read from cci: a report's close, part and job, with its seq, its kind, the lane that wrote it as its writer, when as written, and for a correction the record it corrects."""
    try:
        record = json.loads(reads.run(["cci", "show", seq, "--json"], Path.cwd()))
    except reads.ReadError as error:
        raise DriveError(f"record {seq} could not be read: {error}") from None
    corrects = {"corrects": str(record["re"])} if record["kind"] == "correction" and record.get("re") else {}
    return record["fields"].get("attrs", {}) | {"seq": str(record["seq"]), "kind": record["kind"], "writer": record["lane"], "written": record["at"]} | corrects


def order_posted(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    data = state.load(name)
    lane = state.session_lane(data, os.environ.get("CLAUDE_CODE_SESSION_ID"))
    holder = state.holding_lane(data, find(data["orders"], args.id, "order"))
    if args.part and not args.undo and lane == holder and not (args.record and lane == "communicator"):
        return emit(args, *answered_by(name, args.id, args.part, args.link))
    order = communicators_order(data, args.id)
    if not args.undo:
        report = report_attrs(args.record) if args.record else {}
        if args.part and (args.id, args.part) not in state.part_items(report, args.id):
            named = f"record {args.record}'s part attr names {report.get('part') or 'none'}" if args.record else "no report record names it"
            raise DriveError(
                f'--part "{args.part}": refused, {named}; a post delivers a part only through the report record whose part attr names it:'
                f" pass --record <that record's seq>; a part a post of the drive already answered is marked from the {holder}@{name} session,"
                f' the one its ORPHAN lines reach, with drive order posted {args.id} {args.link} --part "{args.part}", posting nothing'
            )
        return emit(args, *record_post(name, args.id, args.link, report))
    [undone] = pick_parts(args.id, order, [args.part] if args.part else [])
    def order_posted_write(data: dict):
        data["orders"][args.id]["parts"][undone].update(posted=None, posted_by=None)

    state.update(name, None, order_posted_write)
    return emit(args, f'{args.id} "{undone}": not delivered', {"id": args.id, "part": undone, "posted": None})


def answered_by(name: str, order_id: str, part: str, link: str) -> tuple[str, dict]:
    """Deliver a part of the order he reads from the session its watch lines reach, posting nothing, by the drive's post that already answered it, wherever it was made; refused for a staging test part, which only its tester's full pass delivers, and for work he ordered, every part of a build order or an incident, but an incident's first check and cause and a part sorted as an answer that no builder, codex or design job holds, which only drive job prove's passing proof delivers, and for a part while the build it follows runs, which only the end line the watch owes delivers."""
    data = state.load(name)
    order = data["orders"][order_id]
    [part] = pick_parts(order_id, order, [part])
    if state.STAGING_PART.fullmatch(part):
        raise DriveError(f'--part "{part}": refused, a staging test part is delivered only by the DONE of its tester whose CASE lines for its PR read pass, one or more, and each other pass or not exercised, never by a post or a merged PR')
    if state.following(order["parts"][part]):
        raise DriveError(f'--part "{part}": refused, the watch follows {order["parts"][part]["follow"]["build"]} for it, so only the end line it owes once that build ends delivers it')
    if state.work(data, order_id, part):
        raise DriveError(f'--part "{part}": refused, ' + state.UNPOSTED_WORK.format(order=order_id, part=part))
    drive_post_at(link, data, order_id)
    def answered_by_write(data: dict):
        data["orders"][order_id]["parts"][part]["posted"] = link

    state.update(name, None, answered_by_write)
    return f'{order_id} "{part}": posted {link}, the post that answered it', {"id": order_id, "parts": [part], "posted": link}


def order_told(args: argparse.Namespace) -> int:
    """Record the one note the thread one of an order's reporters asked in gets, a bug report's once the fix is proven live and a question's once the answer is posted: the drive's post in that thread, or, when the bot cannot post there, its post in the group DM of that reporter, him and the bot."""
    name = state.resolve(args.drive)
    data = state.load(name)
    order = communicators_order(data, args.id)
    if not order["reporters"]:
        raise DriveError(f"{args.id} names no one to tell: drive order set {args.id} --reporter <their message's permalink> names them")
    asked = order["kind"] == "answer"
    posted, _ = slack.message_and_root(args.link)
    channel = state.CARD_LINK.search(args.link)[1]
    if args.group_dm and not (posted.get("from_claude") and slack.group_dm(args.group_dm) == channel):
        raise DriveError(f"{args.link} is not the drive's post in the group DM of {args.group_dm}, him and the bot, the one cc-slack opens for channel_id {args.group_dm}")
    place = told_reporter(args, data, order, (channel, posted.get("thread_ts")) if posted.get("from_claude") else None)
    keeper, kept = state.kept_record(data, order["reporters"][place])
    keeper_id = keeper or args.id
    if not state.may_tell(data, holder := data["orders"][keeper_id]):
        if asked:
            raise DriveError(f"{args.id}: refused, a part of its answer is not posted yet, so a note now would come before the answer: post it first")
        seen = brief.unseen(data, holder, holder["proof"]) if holder["proof"] else None
        raise DriveError(
            f"{keeper_id}: refused, it keeps no proof that its fix is live{f' on {seen}, where he uses it' if seen else ''}, so a note now would come before the fix:"
            f" drive order set {keeper_id} --proof <{'the recording or live post from there' if seen else 'the check'}> records it once the fix is live"
        )
    if told := kept["told"]:
        heard = "the thread its question was asked in heard the answer" if asked else "the thread its bug was reported in heard it is fixed"
        raise DriveError(f"{args.id}: refused, {heard} once, in {told}{f', kept by {keeper}' if keeper else ''}, and hears it only once")
    def order_told_write(data: dict):
        state.kept_record(data, data["orders"][args.id]["reporters"][place])[1]["told"] = args.link

    state.update(name, None, order_told_write)
    where = f"told in the group DM of {args.group_dm}, him and the bot" if args.group_dm else "told in the reporter's thread"
    return emit(args, f"{args.id}: {where}, {args.link}", {"id": args.id, "told": args.link, "group_dm": args.group_dm})


def told_reporter(args: argparse.Namespace, data: dict, order: dict, where: tuple[str, str | None] | None) -> int:
    """The place among an order's reporters of the one a note tells: the reporter whose thread the drive's post is in, or with --group-dm the one --reporter names, else the only one untold."""
    threads = [(held["channel"], held["thread"]) for held in order["reporters"]]
    if not args.group_dm:
        if where in threads:
            return threads.index(where)
        links = " or ".join(held["link"] for held in order["reporters"])
        raise DriveError(
            f"{args.link} is not the drive's post in {links}, the thread the {'question was asked' if order['kind'] == 'answer' else 'bug was reported'} in;"
            " when the bot cannot post there, the note goes to the group DM of the reporter, him and the bot: pass the reporter's Slack user id with --group-dm"
        )
    if args.reporter:
        named = state.reporter(args.reporter)
        if (named["channel"], named["thread"]) not in threads:
            raise DriveError(f"--reporter {args.reporter}: refused, {args.id} holds no reporter of that thread")
        return threads.index((named["channel"], named["thread"]))
    if len(unheard := [place for place, held in enumerate(order["reporters"]) if not state.kept_record(data, held)[1]["told"]]) > 1:
        raise DriveError(
            f"{args.id}: refused, {' and '.join(order['reporters'][place]['link'] for place in unheard)} have not heard,"
            " so name whose group DM the note went to with --reporter <their message's permalink>"
        )
    return unheard[0] if unheard else 0


def slack_user(text: str) -> str:
    if SLACK_USER.fullmatch(text):
        return text
    raise argparse.ArgumentTypeError("name the reporter by their Slack user id, such as U0123ABCDE: a channel id names no person")


def communicators_order(data: dict, order_id: str) -> dict:
    order = find(data["orders"], order_id, "order")
    if refused := recording_refusal(
        data,
        "only the communicator records a post in an order's thread or takes back a part's mark:"
        f" a part a post of the drive already answered is marked from the {state.holding_lane(data, order)}@{data['name']} session,"
        f' the one its ORPHAN lines reach, with drive order posted {order_id} <that post\'s link> --part "<part>", with or without --record, posting nothing',
    ):
        raise DriveError(refused)
    return order


def recording_refusal(data: dict, route: str) -> str | None:
    """Why this session may post nothing to him, record no post on an order nor take back a part's mark, the one check say runs before it posts and order posted and order told run, `route` naming what the session does instead: only the communicator does them."""
    if state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID")) == "communicator":
        return None
    return route


def report_refusal(data: dict, order_id: str, report: dict) -> str | None:
    """Why a report may deliver or close nothing on an order, the one check say, order posted and order set --state done share: another report already closes the order, or the drive's session that wrote it does not hold the order. Only the communicator's side posts, so the refusal names the report its handler passes instead: the holder's, which on the communicator's own order the handler writes itself from the communicator's lane. A record of his words, or a report naming no part and no close, a progress line, delivers and closes nothing, so none is refused, whoever wrote it."""
    if refused := state.second_close(data, order_id, report):
        return refused
    if report.get("kind") in agents.HIS_KINDS or not report.get("part") and not state.closed_orders(report):
        return None
    writer = state.LANE_ROLES.get(report.get("writer") or "")
    holder = data["orders"][order_id]["owner"] if order_id in data["orders"] else None
    if writer is None or holder in (None, writer):
        return None
    carried = (
        f"post your own report on the cci post tool with kind report, lane communicator, attrs {{close: {order_id}}} and no to,"
        " its first words as text, carrying this report's facts, and pass that seq"
        if holder == "communicator"
        else f"pass the {state.title(holder)}'s own report on {order_id}"
    )
    return f"refused, the report is the {state.title(writer)}'s and {order_id} is the {state.title(holder)}'s order, so it delivers nothing there: {carried}, or post with no record, a progress line"


def post_order(data: dict, post: dict, report: dict) -> str | None:
    """The one order a say post is recorded on, which say decides and no caller names: a post carrying a record goes on the order that record is about, the report it relays or the record of his words it answers; any other post with no record goes on the open answer order whose message its asked names, which it answers; an edit, a card, a PR card, an answer in a teammate's watched thread and a post with neither go on none."""
    if post.get("edit") or post.get("card") or post.get("tap_prs") or report.get("watch"):
        return None
    if post.get("record"):
        return state.report_order(data, report)
    return next((order_id for order_id in data["orders"] if answers_order(data, order_id, post)), None)


def answers_order(data: dict, order_id: str, post: dict) -> bool:
    """Whether the post is the answer to an open answer order: its asked names his message that started the order, by permalink, ts or words, as cc-slack matches the owed reply it clears, so the post that answers it closes it."""
    order = data["orders"][order_id]
    if order["kind"] != "answer" or order["state"] in state.CLOSED_ORDER_STATES or not (started := state.slack_message(order["from"])):
        return False
    words = " ".join(order["text"].split())
    return any((found[1] if (found := state.slack_message(message)) else message) == started[1] or " ".join(message.split()) == words for message in slack.messages(post.get("asked") or ""))


def claim_close(name: str, report: dict) -> list[str]:
    """Claim each order the report closes for it under the drive's lock, just before say posts it, so of two reports closing one order at once only the first posts and second_close refuses the other; the orders claimed, which a post that fails gives back, never one the watch's post of this report already marked."""
    if not state.closed_orders(report):
        return []
    def claim_close_write(data: dict):
        closes = [order_id for order_id in state.closed_orders(report) if order_id in data["orders"] and data["orders"][order_id]["closed_on"] != report["seq"]]
        if refused := next(filter(None, (state.second_close(data, order_id, report) for order_id in closes)), None):
            raise DriveError(f"nothing posted: {refused}")
        for order_id in closes:
            data["orders"][order_id]["closed_on"] = report["seq"]
        return closes

    closes = state.update(name, None, claim_close_write)
    return closes


def unclaim_close(name: str, closes: list[str]) -> None:
    def unclaim_close_write(data: dict):
        for order_id in closes:
            data["orders"][order_id]["closed_on"] = None

    state.update(name, None, unclaim_close_write)


def reported_parts(data: dict, order_id: str, order: dict, report: dict, items: list[tuple[str | None, str]]) -> list[str]:
    """The parts of the order a post delivers: each item of its report's part attr names on this order, or each part of an order its report closes, when it is words he reads, never work he ordered, which only drive job prove delivers, nor, but on a close, a part whose followed build still runs, which only the end line the watch owes delivers. A post whose report names neither part nor close, whatever job it names, that carries a record of his words, whatever its part attr names, or that carries no report is a progress line that delivers none, on an order of one part as on one of several."""
    if report.get("kind") in agents.HIS_KINDS:
        return []
    closing = order_id in state.closed_orders(report)
    named = [part for on, part in items if on == order_id]
    parts = pick_parts(order_id, order, named) if named else list(order["parts"]) if closing else []
    return [part for part in parts if not state.work(data, order_id, part) and (closing or not state.following(order["parts"][part]))]


def delivering(name: str, order_id: str, report: dict) -> str:
    """What a post would deliver on the order and on each other order an item of its report's part attr names, as record_post marks it once the post is out."""
    data = state.load(name)
    items = state.part_items(report, order_id)
    lines = []
    for each in dict.fromkeys([order_id, *(on for on, _ in items)]):
        parts = reported_parts(data, each, find(data["orders"], each, "order"), report, items)
        quoted = ", ".join(f'"{part}"' for part in parts)
        lines.append(f"{each} {quoted}: would be delivered" if parts else f"{each} progress line, no part delivered")
    return "; ".join(lines)


def record_post(name: str, order_id: str, link: str, report: dict, sent: str | None = None, named: str | None = None) -> tuple[str, dict]:
    """Record the drive's post on the order it went out on and on each other order an item of its report's part attr names, so one post in a thread two orders share delivers on both; an item naming no order is a part of the order the post went out on, on every order it is recorded on. A post say just made passes the words it sent, so its own post is not read back from Slack, and the channel_id it named, if any."""
    items = state.part_items(report, order_id)
    others = dict.fromkeys(on for on, _ in items if on != order_id)
    first, *rest = [record_on(name, each, link, report, items, sent, named) for each in [order_id, *others]]
    return "; ".join(line for line, _ in [first, *rest]), first[1] | ({"also": [fields for _, fields in rest]} if rest else {})


def record_on(name: str, order_id: str, link: str, report: dict, items: list[tuple[str | None, str]], sent: str | None = None, named: str | None = None) -> tuple[str, dict]:
    """Record the drive's post on one order: the order's last line, only for a line to him, the parts its report's items deliver there, the end its report's until attr names for a post at another channel's root or a line in its thread, from which the watch owes that thread its closing line, and its thread among the order's, with when it joined, when the order holds it not yet, as members for a thread whose readers are another channel's members, such as the one under a staging claim say posted at that channel's root, or as the order's own thread when it has none and the post started that thread at the root of his DM or of a channel the drive keeps, so its every later line replies under its first; an order whose own thread is in his DM keeps a thread a post started in a channel the drive keeps as one of its threads, which its lines go in from then on."""
    data = state.load(name)
    order = find(data["orders"], order_id, "order")
    if refused := report_refusal(data, order_id, report):
        raise DriveError(refused)
    delivered = reported_parts(data, order_id, order, report, items)
    posted, ours, gained = drive_post_at(link, data, order_id, sent, named)
    at = slack.sent_at(posted)
    line = state.stamp(at)
    closing = order_id in state.closed_orders(report)
    where = (state.CARD_LINK.search(link)[1], posted.get("thread_ts") or posted["ts"])
    until = None if ours else report.get("until")

    def keep(data: dict) -> tuple[dict, bool]:
        order = data["orders"][order_id]
        rooted = bool(gained) and gained[1] == posted["ts"] and gained[0] in rooms(data) and not order["thread"]
        if rooted:
            order["thread"] = list(gained)
        elif gained:
            state.join_thread(order, gained, line, "posted" if ours else "members")
        if until:
            state.claim(data, order, where, link, until, posted["ts"])
        if ours:
            order["last_line"] = max(order["last_line"] or line, line, key=state.parse_time)
        for part in delivered:
            order["parts"][part]["posted"] = link
        return order, rooted

    order, rooted = state.update(name, None if until else (("orders", order_id),), keep)
    quoted = ", ".join(f'"{part}"' for part in delivered)
    said = f"{quoted}: posted" if delivered else f"{'closing' if closing else 'progress'} line, no part delivered:"
    away = "" if ours else f", {'in a thread of another channel its members read' if posted.get('thread_ts') else 'at the root of another channel'}, so it is no line to him"
    joined = f", in thread {gained[1]}, now {'its own thread' if rooted else 'one of its threads'}" if gained else ""
    ends = f", and its thread owes its closing line from {render.when(until)}" if until else ""
    return f"{order_id} {said} {link}{away}{joined}{ends}", {"id": order_id, "parts": delivered, "posted": link}


def promised_due(text: str, at: datetime) -> datetime | None:
    """The time a post made at `at` promises him a line in its thread by: the earliest clock time still to come that its words put after "by" or "before" in any words, such as "its count of cards comes by 2:58 PM", or after next line, update, post or summary, as "Next line at 4:15", by his order "a line promised him by a time goes out late with nothing chasing it"; on the day its words name after it, such as "10:00 AM tomorrow", else at the time of day nearest the post, read with AM or PM as written, else on either half of a 12-hour clock, so at 9:29 PM "next line by 9:45" is 9:45 PM and "next line by 7:30" is past. A time in a quote, code or a link promises nothing."""
    masked = limits.masked(text)
    dues = []
    for said in PROMISED_TIME.finditer(masked):
        hour = int(said["hour"])
        hours = [hour % 12 + (12 if said["half"].upper() == "P" else 0)] if said["half"] else [hour % 12, hour % 12 + 12] if 1 <= hour <= 12 else [hour]
        day = limits.DAY_AFTER.match(masked, said.end())
        dues.append(state.nearest(hours, int(said["minute"]), at, day[1] if day else ""))
    return min((due for due in dues if due > at), default=None)


def job_add(args: argparse.Namespace) -> int:
    job_id, job, lines = added_job(args)
    return emit(args, "\n".join([job_id, *lines]), {"id": job_id, **job})


def added_job(args: argparse.Namespace) -> tuple[str, dict, list[str]]:
    from drive import view

    name = state.resolve(args.drive)
    created = state.stamp()
    if args.waits and args.agent:
        raise DriveError("--waits records a job whose agent starts on the watch's FREE line once its last wait ends, so it takes no --agent")
    if args.lands and args.kind != "builder":
        raise DriveError("--lands names the PRs a builder lands, which only a builder does")
    if args.model and args.kind != "codex":
        raise DriveError("--model names the codex-ask model a codex job runs; any other job's model is its Agent call's")
    if args.release and args.waits:
        raise DriveError("--release starts its release as the job is added, so it waits on nothing: add the job once what it waits on has landed")
    if args.reading and not (args.release and state.top_coordinator(state.load(name), os.environ.get("CLAUDE_CODE_SESSION_ID"))):
        raise DriveError("--reading is the top coordinator's own reading of his words ordering the release --release starts, taken only from its session")
    refuse_a_mark_off_a_builder(args.kind, args.builds_nothing)
    lands = list(dict.fromkeys(map(state.pr_key, args.lands)))
    found = check_waits(name, args.waits)
    unreleased = args.release and (brief.release_refusal(args.release, known := state.load(name), args.reading) or brief.approval_refusal(args.release, known, args.reading) or brief.away_refusal("start", args.release, known, state.now()))
    if args.release and not unreleased:
        for line in filter(None, [brief.uncarried(args.release, known), *brief.release_notes([args.release], known)]):
            print(f"drive job add: {line}", file=sys.stderr)
    running = args.release and not unreleased and release_running(name, args.release)
    unworded = brief.unworded_return(args.kind, {"--goal": args.goal})
    before = state.load(name)
    named = args.part or (list(filed) if len(filed := find(before["orders"], args.order, "order")["parts"]) == 1 else [])
    removing = {} if lands or args.release or args.builds_nothing else brief.removed_parts(before, args.order, named)
    given = {
        "kind": args.kind,
        "goal": args.goal,
        "agent": args.agent,
        "where": "remote" if args.remote else None,
        "repo": args.repo,
        "branch": args.branch,
        "worktree": args.worktree,
        "builds_nothing": args.builds_nothing,
        "priority": args.priority,
        "model": args.model,
    }
    def job_added(data: dict):
        order = find(data["orders"], args.order, "order")
        parts = pick_job_parts(name, data, args.order, args.part)
        refuse_a_shared_at_once_part(parts)
        new_parts = [part for part in parts if not state.part_jobs(data, args.order, part)]
        session = os.environ.get("CLAUDE_CODE_SESSION_ID")
        fresh = state.new_job(args.order, args.kind, args.goal, parts, created)
        waits = view.stored_waits(data, [state.job_wait(data, wait, None, fresh) for wait in args.waits], found)
        held = state.change_job(data, args.order, parts, waits, proving=state.builds_nothing(fresh | given), branch=args.branch, repo=args.repo) if args.kind in state.CHANGE_KINDS and not (lands or args.release) else None
        if refused := (
            state.held_elsewhere(data, args.order, session, bool(args.release))
            or unreleased
            or running
            or unworded
            or next((removing[part] for part in parts if part in removing), None)
            or (args.model == "astra" and not re.search(r"\bastra\b", order["text"], re.IGNORECASE) and f"--model astra runs only on an order whose words name astra, which {args.order}'s do not")
            or proven_again(data, args.order, args.kind, args.goal)
            or state.backlog_refusal(data, args.order, args.kind)
            or unlandable(data, lands)
            or state.job_wait_refusal(data, args.waits)
            or brief.job_refusal(data, args.order, args.kind, parts, bool(lands or args.release or args.builds_nothing or args.kind == "builder" and args.branch))
            or (held and one_change_job(data, args.order, args.kind, parts, held, waits, branch=args.branch, repo=args.repo))
        ):
            raise DriveError(f"refused, no job added: {refused}")
        order["owner"] = order["owner"] or state.session_role(data, session)
        took = []
        folds = held and state.never_worked(held[1])
        sibling = held and not folds and state.untaken(held[1])
        if folds:
            job_id, job, shared = held
            job.update({field: value for field, value in given.items() if value is not None}, updated=created)
            job["waits"] = list(dict.fromkeys([*job["waits"], *waits]))
            state.deliver(data, job_id, args.order, parts)
            took.append(f"{job_id} carried {shared} and no worker took it, so this add folded into it, a part or branch having one change job: its goal is this add's and its waits gained this add's")
        else:
            job_id = state.next_id(data["jobs"], "j")
            job = data["jobs"][job_id] = fresh | given | {"where": given["where"] or "local", "waits": waits}
            order["jobs"].append(job_id)
        if sibling:
            took.append(f"{held[0]} carries {held[2]} and has worked, between runs or at work, so this add left it as it is and added {job_id} beside it on the same parts")
        elif held and not folds:
            took.append(state.hand_over(data, held, job_id, args.order, parts, created, args.branch, args.repo))
        slotted = ""
        if args.release:
            job["brief_flags"] = (job.get("brief_flags") or {}) | {"release": args.release}
            job["release_pass"] = {"release": args.release, "at": created, "reading": args.reading}
            if (env := brief.leased_env(words := shlex.split(args.release), data)) and not (brief.gives_way(words) and (data["slots"].get(env) or {}).get("holder")):
                slotted = state.slot_claim(data, env, job_id, CUT_LEASE_MINUTES, [], None, state.parse_time(created), starts=True)
        if args.agent:
            job["session"] = session
            state.start(job)
        if crowd := args.kind == "builder" and not args.remote and not job["agent"] and not job["waits"] and place.crowded(data, job_id, state.now()):
            job["where"] = "remote"
        builder_id = waiting_builder(data, job_id, created)
        order["updated"] = created
        if not args.release:
            state.end_carried_designs(data, args.order, created)
        replaced = [] if args.release else state.end_replaced(data, [args.order], created)
        named = state.named_prs(data, args.order, args.goal)
        history = {key: state.pr_orders(data, key, args.order) for key in named}
        return data, job_id, job, builder_id, crowd, slotted, took, replaced, named, history, new_parts

    data, job_id, job, builder_id, crowd, slotted, took, replaced, named, history, new_parts = state.update(name, None, job_added)
    waiting = [f"{job_id} waits on {', '.join(job['waits'])}: start no agent for it until the watch's FREE line says its last wait ended"] if job["waits"] and not job["agent"] else []
    waiting += render.builder_lines(name, data, [builder_id] if builder_id else [])
    if crowd:
        waiting.append(
            f"{job_id} starts on a Sprite at once, since {crowd}: drive job launch {job_id} <brief>; one whose brief declares a step only this Mac runs, on {place.MAC_LINES}, waits its turn for a slot here once its agent starts"
        )
    queued = slotted.startswith("QUEUED")
    started = [f"{job_id} {slotted}: the watch starts `{args.release}` by drive job release {job_id} once the slot is its, so start nothing for it"] if queued else state.after_save(name, lambda: started_release(name, job_id, args.release)) if args.release else []
    also = list(dict.fromkeys(order_id for orders in history.values() for order_id in orders))
    names = [f"{job_id} names {', '.join(named)}" + (f", also on {', '.join(also)}" if also else "") + ": drive status <id>"] if named else []
    names += state.asked_lines(data, args.goal)
    return job_id, job | {"builder": builder_id, "history": history}, [*took, *replaced, *started, *waiting, *names, *repeated_lines(data, args.order, new_parts)]


CUT_LEASE_MINUTES = 60
RELEASE_RETRY_SECONDS = 30
GITHUB_5XX = re.compile(r"\b(?:GET|HEAD|POST|PUT|PATCH|DELETE) /\S+ failed: 5\d\d\b|\bHTTP 5\d\d\b")


def started_release(name: str, job_id: str, command: str) -> list[str]:
    """Start the release a job's --release names in a process of its own, drive job release, which outlives the call that added the job, so a call that times out or fails once it started keeps the job, and that process its build and brief, its start naming the session that added the job to the release tool as the call's own would; the job waits on this call until that process starts and then on it, so the watch frees the job once it ends."""
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    claim = state.pid_waits(f"pid:{os.getpid()}")

    def claimed(data: dict) -> None:
        data["jobs"][job_id]["waits"] += claim

    state.update(name, None, claimed)
    waits: list[str] = []

    def own(data: dict) -> None:
        job = data["jobs"][job_id]
        job["waits"] = [kept for wait in job["waits"] for kept in (waits if wait in claim else [wait])]

    try:
        pid = state.detach(name, ["job", "release", job_id, *(["--session", session] if session else [])], RELEASES_LOG)
        waits = state.pid_waits(f"pid:{pid}")
    finally:
        state.update(name, (("jobs", job_id, "waits"),), own)
    return [f"{job_id} starts `{command}` in process {pid}, which keeps the build it starts as {job_id}'s Release build: line and writes its brief, its lines in {state.drive_dir(name) / RELEASES_LOG}: start no agent for it until the watch's FREE line on {job_id} names that brief"]


def job_release(args: argparse.Namespace) -> int:
    """Start the release a job's --release names, in the process drive job add starts for it, by release_start, once more RELEASE_RETRY_SECONDS on when the first start failed on a GitHub 5xx, by GITHUB_5XX; `ci release` names the build it started, by release_answer, which the job keeps as its release_build in a save holding nothing else, so a later step that fails leaves it kept, before the designs it carries and each failed job it replaces end and its brief is written, and then kicks off the prebuild of a staging or sandbox start's commit, by prebuild; a start naming none leaves the job with no build, its wait on this process ending on what it said, which the watch's FREE line names, that wait taking the place of the starting call's own wait on itself in the same write when started_release has not swapped it yet, so the watch never ends the job's wait on that call first and loses why, and tells the coordinator and the session holding its order at once, by release_start_failed."""
    name = state.resolve(args.drive)
    command = state.load(name)["jobs"][args.id]["brief_flags"]["release"]
    [words] = simple_commands(command)
    manual = "manual" in providers.of(state.load(name)).release.start(words)
    answer, failure = release_start(name, words, manual, args.session)
    if failure and GITHUB_5XX.search(failure):
        print(f"{args.id}: its release did not start, failing on GitHub: {failure}; starting it once more in {RELEASE_RETRY_SECONDS} s", file=sys.stderr, flush=True)
        time.sleep(RELEASE_RETRY_SECONDS)
        answer, failure = release_start(name, words, manual, args.session)
    if failure:
        why = f"its release did not start: {failure}"
        mine, starter = state.pid_waits(f"pid:{os.getpid()}"), state.pid_waits(f"pid:{os.getppid()}")
        def job_release_write3(data: dict):
            job = data["jobs"][args.id]
            job["waits"] = [kept for wait in job["waits"] for kept in (mine if wait in starter else [wait])]
            state.drop_waits(data, args.id, dict.fromkeys(mine, f"{why}; drive job set {args.id} --state stopped ends it"))

        state.update(name, None, job_release_write3)
        release_start_failed(name, args.id, command, failure)
        raise DriveError(f"{args.id}: {why}")
    build = answer["build"]["url"]
    def job_release_write(data: dict):
        data["jobs"][args.id]["release_build"] = build

    state.update(name, None, job_release_write)
    def job_release_write2(data: dict):
        job = data["jobs"][args.id]
        state.end_carried_designs(data, job["order"], job["created"])
        replaced = state.end_replaced(data, [job["order"]], job["created"])
        return replaced

    replaced = state.update(name, None, job_release_write2)
    line, _ = write_brief(name, args.id, {})
    kicked = prebuild(name, args.id, command, answer.get("commit"))
    print("\n".join([*replaced, f"{args.id} started `{command}` as {build} and left it running: its agent follows it with `{answer['follow']}` and reads its approve hold", *kicked, line]))
    return 0


def release_start(name: str, words: list[str], manual: bool, session: str | None) -> tuple[dict, str | None]:
    """Run a release start from the drive's checkout at the head of its default branch, `--json` added unless it starts `--manual`, which takes none, left running once `ci release` names the build it started, by release_answer: that answer, and None, or, for a start naming no build, what it refused or printed, else its exit status."""
    said: list[str] = []
    answer: dict = {}
    with subprocess.Popen(
        words if manual else [*words, "--json"],
        cwd=orca.checkout(name, state.load(name)["roles"]["coordinator"]["cwd"]),
        env=os.environ | {"CLAUDE_CODE_SESSION_ID": session} if session else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as release:
        for line in release.stdout:
            said.append(line)
            if answer := release_answer(said, manual):
                break
        release.terminate()
    if answer.get("build", {}).get("url"):
        return answer, None
    return answer, answer.get("refused") or render.cut("".join(said).strip(), 600) or f"exit {release.returncode}"


def release_start_failed(name: str, job_id: str, command: str, failure: str) -> None:
    """Post one handoff record to the session acting on a job whose release start named no build and to the session holding its order, by state.job_lane and state.holding_lane, naming the start command, what it said and that the job did not start, so no slot it holds waits on a start nobody heard failed."""
    data = state.load(name)
    job = data["jobs"][job_id]
    to = list(dict.fromkeys([state.job_lane(data, job), state.holding_lane(data, data["orders"][job["order"]])]))
    text = f"RELEASE {job_id} {job['order']}: `{command}` did not start: {failure}; {job_id} did not start, and drive job release {job_id} starts it again"
    argv = ["cci", "post", "--drive", name, "--lane", watch.LANE, "--kind", "handoff", *(arg for lane in to for arg in ("--to", f"{lane}@{name}")), "--topic", f"release-{job_id}"]
    reads.run([*argv, "--text", reads.withheld(text)], Path.cwd(), reads.posting())


def prebuild(name: str, job_id: str, command: str, commit: str | None) -> list[str]:
    """Kick off the builds a later release at a staging or sandbox start's commit adopts, by the `prebuild` command of the repository's [release] table with `{commit}` filled, run by the shell in the drive's checkout and printing the build it started as a JSON object's `build.url`, which the job keeps as its prebuild_build, so the production start at that commit signs, notarizes and bakes nothing its staging start already had built; none for a start reaching a production place, by settled.off_production, a start naming no commit, as a `--manual` one names none, or a repository naming no such command, and a kick-off that fails leaves the release as it started, saying why."""
    data = state.load(name)
    if not (commit and (kickoff := state.repo_config(data).get("release", {}).get("prebuild"))):
        return []
    checkout = orca.checkout(name, data["roles"]["coordinator"]["cwd"])
    if not all(settled.off_production(flags, str(checkout)) for flags in brief.started(command, data)):
        return []
    try:
        url = json.loads(reads.run(["/bin/sh", "-c", kickoff.replace("{commit}", shlex.quote(commit))], checkout))["build"]["url"]
    except reads.ReadError as error:
        return [f"{job_id}'s prebuild at {commit} did not start, so a release at {commit} builds what it would have: {error}"]

    def prebuild_write(data: dict):
        data["jobs"][job_id]["prebuild_build"] = url

    state.update(name, None, prebuild_write)
    return [f"{job_id} kicked off {url}, which builds every image and app a release at {commit} adopts, so the production start at {commit} reuses them"]


def release_running(name: str, command: str) -> str | None:
    """Why a release may not start while a start of the same release runs, the same by shipped_release past the --order approving it: a job of the drive holding it unfinished with no build, its start still running or never having named one, or one whose build Buildkite reads as unfinished, an approve hold included, read in one `tools/ci bk graphql` call, so a call that failed once its release started never starts it twice."""
    data = state.load(name)
    ships = shipped_release(command, data)
    same = {job_id: job for job_id, job in data["jobs"].items() if state.starts_cut(job) and shipped_release(job["brief_flags"]["release"], data) == ships}
    if unbuilt := next((job_id for job_id, job in same.items() if not job["release_build"] and job["state"] not in state.FINISHED_JOB_STATES), None):
        return (
            f"{unbuilt} holds the same release and has kept no build: its start's lines are in {state.drive_dir(name) / RELEASES_LOG}, the watch's FREE line on {unbuilt} names its brief once that start ends,"
            f" and drive job set {unbuilt} --state stopped ends a job whose release did not start"
        )
    if not (builds := {job["release_build"]: job_id for job_id, job in same.items() if job["release_build"]}):
        return None
    try:
        read = watch.read_builds(Path(data["roles"]["coordinator"]["cwd"]), state.drive_checkout(name, data["roles"]["coordinator"]["cwd"]), list(builds))
    except (DriveError, reads.ReadError, json.JSONDecodeError) as error:
        return f"Buildkite did not say whether {', '.join(builds)}, started for the same release, still runs: {error}; send it again once Buildkite answers"
    return next(
        (f"{builds[link]} started the same release as {link}, which Buildkite reads {found['state']}: its job follows it, and this one starts once it ends" for link, found in read.items() if found and found["state"] not in watch.FOLLOW_ENDS),
        None,
    )


def shipped_release(command: str, data: dict) -> list[dict[str, tuple[str, ...]]]:
    return [{flag: values for flag, values in flags.items() if flag != "order"} for flags in brief.started(command, data)]


def release_answer(said: list[str], manual: bool) -> dict:
    """The build a `ci release start` started and the command that follows it, as its JSON object's `build.url` and `follow`: a `--manual` start's line naming the deploy build its cut triggered, which it prints as text once that build exists, by MANUAL_STARTED; any other start's JSON object `--json` answers with, its first line once it stays with the build it started, else what it printed from its first line opening an object, as a refusal prints it; empty until that line or object is whole."""
    if manual:
        found = MANUAL_STARTED.match(said[-1].strip())
        return {"build": {"url": found["build"]}, "follow": found["follow"]} if found else {}
    opened = next((at for at, line in enumerate(said) if line.startswith("{")), len(said))
    try:
        return json.loads("".join(said[opened:])) if opened < len(said) else {}
    except json.JSONDecodeError:
        return {}


def refuse_a_mark_off_a_builder(kind: str, builds_nothing: bool | None) -> None:
    if builds_nothing and kind != "builder":
        raise DriveError("--builds-nothing marks a builder that holds no slot of this Mac's builder cap; no other kind of job holds one")


def one_change_job(
    data: dict, order_id: str, kind: str, parts: list[str], held: tuple[str, dict, str], waits: list[str], adds: bool = True, branch: str | None = None, repo: str | None = None, job_id: str | None = None
) -> str | None:
    """Why a builder or codex job may not take parts or a branch another unfinished one carries, a part or branch having one change job: while that job runs it takes the event itself; parts or a branch two such jobs carry name no one job; and drive job set gives no job a part or branch such a job carries, which drive job add hands that job, folding into it while no worker took it and taking those parts, or that branch's whole work, from it once it returned, by state.hand_over, as a BUILT line on that branch takes it."""
    held_id, other, shared = held
    carries = f"{held_id} {other['kind']} {other['state']} carries {shared} already"
    if other["state"] == "running" and not state.untaken(other):
        return f'{carries}, so a second {kind} would race it on one change: send it the event with drive job resume {held_id} "<the event>", or stop it with drive job set {held_id} --state stopped before adding the job that replaces it'
    if not adds:
        return f"{carries}, and a part or branch has one change job: drive job add {kind} --order {order_id} on that part or branch takes its place, or stop it first with drive job set {held_id} --state stopped"
    second = state.change_job(data, order_id, parts, [*waits, held_id], job_id, branch, repo)
    return f"{carries}, and {second[0]} {second[1]['kind']} {second[1]['state']} carries {second[2]}: a part or branch has one change job, so name the parts or branch of one of them" if second else None


def refuse_a_second_change_job(data: dict, job_id: str, order_id: str, parts: list[str], branch: str | None = None) -> None:
    job = data["jobs"][job_id]
    if job["kind"] in state.CHANGE_KINDS and (held := state.change_job(data, order_id, parts, job["waits"], job_id, branch, job["repo"], proving=state.builds_nothing(job))):
        raise DriveError(f"{job_id}: refused, nothing changed: {one_change_job(data, order_id, job['kind'], parts, held, job['waits'], adds=False)}")


def proven_again(data: dict, order_id: str, kind: str, goal: str) -> str | None:
    """Why a builder whose goal names a merged PR may not start on a done order: its fix was proven live already, by the done prover install.prover_of names on any order, else by the proof the order keeps."""
    order = data["orders"][order_id]
    if kind != "builder" or order["state"] != "done" or not (merged := [key for key in state.named_prs(data, order_id, goal) if data["prs"][key]["merged_sha"]]):
        return None
    named = ", ".join(merged)
    proved = next((found for key in merged if (found := install.prover_of(data, key)) and data["jobs"][found]["state"] == "done"), None)
    held = f"{proved} proved {named} live" if proved else f"its proof is {order['proof']}" if order["proof"] else "it closed"
    return f"{order_id} is done and {held}, so no builder proves {named} live on it again"


def waiting_builder(data: dict, reader_id: str, created: str) -> str | None:
    """The builder a build order's reader is added with, waiting on it, for each of the reader's parts no builder, codex or design job carries, its goal building those parts and never the reader's question, wherever a builder there starts only on research and job_refusal lets it wait on that reader, so its work is never research's alone while it reads."""
    reader = data["jobs"][reader_id]
    if reader["kind"] != "reader" or not (uncarried := [part for part in reader["parts"] if not state.carriers(data, reader["order"], part)]):
        return None
    if not brief.waits_on_research(data["orders"][reader["order"]], "builder", uncarried) or brief.job_refusal(data, reader["order"], "builder", uncarried):
        return None
    builder_id = state.next_id(data["jobs"], "j")
    data["jobs"][builder_id] = state.new_job(reader["order"], "builder", state.parts_goal(uncarried), uncarried, created) | {"waits": [reader_id]}
    data["orders"][reader["order"]]["jobs"].append(builder_id)
    return builder_id


def unlandable(data: dict, keys: list[str]) -> str | None:
    """Why a builder may not start on --lands alone: each PR it lands is a drive PR his Approve or an exempt class lets land, since only then does it build nothing his order's research would shape."""
    for key in keys:
        may_land, why = tap_verdict(data, data["prs"][key])
        if not may_land:
            return f"--lands {key}: it may not land yet, {why}; a builder lands only a PR he approved or an exempt class lands"
    return None


def refuse_a_shared_at_once_part(parts: list[str]) -> None:
    """The immediate fix and a follow-up are each a job of its own, since each starts at once past the gates the order's other parts wait on: the immediate fix's PR lands while the proper fix's waits on his cards, and a follow-up's design starts while the PR it follows lands."""
    for part, why in (
        (state.IMMEDIATE_FIX, "its PR lands at once, while the proper fix's waits on his cards"),
        (state.FOLLOW_UP, "its design starts at once, while the PR it follows lands"),
        (state.PAGE, "its codex job builds the page at once, while the order's other parts wait on research"),
    ):
        if part in parts and len(parts) > 1:
            others = ", ".join(json.dumps(other, ensure_ascii=False) for other in parts if other != part)
            raise DriveError(f'the "{part}" part is a job of its own: {why}, so one job takes "{part}" and another {others}')


def job_set(args: argparse.Namespace) -> int:
    """Change a job; a state it already holds changes nothing, stopped is the one state set by hand, which ends the job's lane once saved, by stopped_lane, done only on a design a builder carries, and any other --state is refused with nothing changed, since the job's return sets it; --part, --parts and --carries change the parts it delivers and never its state, so a finished job runs again only once drive job launch or resume starts its lane; a --said opening with a result word is refused unless it sets the state the job keeps, since a said line reading DONE on a running job is a result it never returned; a --said keeps on the job what that line answers, by state.keep_waited, as drive job report does, so a WAITING that drops a card no longer leaves the job's PRs blocked on it; an order it carries reworks only the PRs of the job --rework names, and each other open card of his on the job's PRs is named and left up; an agent --agent binds is refused, nothing changed, while a launch starts the job's next lane, by state.launch_in_flight, the rule agents.free applies to an agent's own steps."""
    name = state.resolve(args.drive)
    known = state.load(name)
    found = find(known["jobs"], args.id, "job")
    if args.heard:
        told = [kept for kept in found["undelivered"] if kept.get("told")]

        def heard_write(data: dict):
            data["jobs"][args.id]["undelivered"] = [kept for kept in data["jobs"][args.id]["undelivered"] if not kept.get("told")]
            return data

        state.update(name, (("jobs", args.id),), heard_write)
        return emit(args, f"{args.id}: {len(told)} held text{'' if len(told) == 1 else 's'} of his marked heard", {"id": args.id, "heard": len(told)})
    stops = args.state == "stopped"
    already = f"{args.id} is already {found['state']}\n" if args.state == found["state"] else ""
    if already:
        args.state = None
    elif args.state == "done" and (builder := state.design_builder(known, args.id)):
        already = f"{args.id}'s design is carried by builder {builder}\n"
    elif args.state not in (None, "stopped"):
        line = f", its first line `{found['said']}`" if found["said"] else ""
        raise DriveError(
            f"{args.id}: refused, nothing changed: --state sets only stopped, for a job that ends unfinished, and done for a design job a"
            " builder carries, which ends on its own once drive job add builder --part or drive job set <builder> --part names its part or a"
            f" change its DESIGN listed; every other state comes from the job's return: pipe it into `drive job report {args.id} -`{line}"
        )
    resulted = args.said and (re.match(r"[A-Z]*", args.said)[0] in state.RESULT_STATES or state.BUILT_LINE.match(args.said))
    if resulted and (taken := state.result_state(found, args.said)) != (stays := args.state or found["state"]):
        raise DriveError(
            f"{args.id}: refused, nothing changed: --said opens with a result that sets {taken}, and {args.id} stays {stays}, so every reader of its said line"
            f" would read a result it never returned: pipe a result into `drive job report {args.id} -`, or say what happened in words opening with no result word"
        )
    if args.state == "stopped":
        fix_kept(known, args.id)
    refuse_a_mark_off_a_builder(found["kind"], args.builds_nothing)
    changes = {"agent": args.agent, "state": args.state, "said": args.said, "branch": args.branch, "due": job_clock(args.id, args.due), "builds_nothing": args.builds_nothing, "priority": args.priority}
    urls = dict.fromkeys(state.pr_url(state.pr_key(url)) for url in args.pr)
    if refused := state.job_wait_refusal(known, args.waits):
        raise DriveError(refused)
    found = check_waits(name, args.waits)
    told_him(name, args)
    for order_id in args.carries:
        refuse_a_crossed_order(name, known, find(known["jobs"], args.id, "job")["order"], order_id, f"{args.id} --carries")
    if args.rework and len(args.carries) > 1:
        raise DriveError("--rework names the PRs one order's words rework: name at most one --carries <o>, else the job's own order")
    reworks = heard_reworks(name, args.rework, args.id)
    shown = args.card and shown_card(name, args.card)
    lane = args.agent and args.agent.startswith(state.DISPATCH_PREFIX) and orca.receipt(args.agent)
    def changed(data: dict):
        job = find(data["jobs"], args.id, "job")
        job.update({field: value for field, value in changes.items() if value is not None})
        if args.branch:
            refuse_a_second_change_job(data, args.id, job["order"], [], args.branch)
        if args.card:
            hold_card(job, args.card, shown)
            state.join_card(data, args.id, args.card)
        waits = merged_waits(data, job, [state.job_wait(data, wait, args.id, job) for wait in args.waits], args.no_waits, found)
        job["waits"] = [wait for wait in waits if not (state.builds_nothing(job) and wait == state.SLOT_WAIT or job.get("priority") and wait == state.SPRITE_WAIT)]
        if args.said:
            state.keep_waited(data, args.id, args.said)
        if args.carries:
            if args.parts:
                raise DriveError("--parts replaces the parts of the job's own order; name a carried order's parts with --part")
            if args.part and len(args.carries) > 1:
                raise DriveError("--part names the parts of one carried order; carry one order a call to name its parts")
            for order_id in args.carries:
                if order_id == job["order"]:
                    raise DriveError(f"{args.id} is {order_id}'s own job; add its parts with --part")
                find(data["orders"], order_id, "order")
                parts = pick_job_parts(name, data, order_id, args.part)
                refuse_a_second_change_job(data, args.id, order_id, parts)
                state.carry(data, args.id, order_id, parts)
        elif args.part or args.parts:
            named = pick_job_parts(name, data, job["order"], args.parts or args.part)
            job["parts"] = named if args.parts else list(dict.fromkeys([*job["parts"], *named]))
            refuse_a_shared_at_once_part(job["parts"])
            refuse_a_second_change_job(data, args.id, job["order"], named)
        if args.agent and args.agent == job.get("dispatch"):
            job.update(lane, where="remote", agent=None)
        elif lane:
            job.update(lane, where="remote", agent=None)
            state.start(job)
        elif args.agent:
            if state.launch_in_flight(name, args.id, job):
                raise DriveError(
                    f"{args.id}: refused, nothing changed: a drive job launch or resume holds its lock ({state.holders(state.job_lock_path(name, args.id))}) while it starts"
                    f" the job's next lane, which it records once that lane's worker starts, so agent {args.agent} bound now would run beside it; set it again once that launch ends"
                )
            job.update(where="local", session=os.environ.get("CLAUDE_CODE_SESSION_ID"))
            if args.agent != known["jobs"][args.id]["agent"]:
                state.start(job)
                hear_opening(data, args.id)
            superseded.hear_start(data, args.id)
        if args.order and not args.repro:
            raise DriveError(f"{args.id}: refused, nothing changed: --order names whose proof file --repro keeps, so it needs --repro <file>")
        if args.order and args.order not in [job["order"], *job["carries"]]:
            raise DriveError(f"{args.id}: refused, nothing changed: {args.order} is neither its order nor one it carries, so no proof file of it delivers {args.order}'s parts")
        if args.order:
            job["repros"] = (job.get("repros") or {}) | {args.order: keep_repro(name, f"{args.id}.{args.order}", job["kind"], args.repro)}
        elif args.repro:
            job["repro"] = keep_repro(name, args.id, job["kind"], args.repro)
        for url in urls:
            if (key := state.pr_key(url)) in data["prs"]:
                state.take_pr(data, args.id, key)
            elif url not in job["prs"]:
                job["prs"].append(url)
        job["updated"] = state.stamp()
        state.end_carried_designs(data, job["order"], job["created"])
        replaced = state.end_replaced(data, [job["order"], *args.carries], job["updated"])
        return data, job, replaced
    data, job, replaced = state.update(name, None, changed)
    owned = {pr["url"] for pr in data["prs"].values() if pr["job"] == args.id}
    cards = [card for card in fresh_pr_cards(name) if owned & set(card["prs"])] if args.carries else []
    reworked = [
        line
        for order_id in args.carries or ([job["order"]] if reworks else [])
        for line in rework_named_prs(name, order_id, reworks, cards, f"drive job set {args.id} --carries {order_id} --rework <PR>=<his permalink>")
    ]
    stopping = [f"{args.id}'s lane launching since {render.when(job['launching'])} is ended as its worker starts"] if state.launch_in_flight(name, args.id, job) and overtaken(data, job) else []
    ended = state.after_save(name, lambda: stopped_lane(name, args.id)) if stops else []
    return emit(args, already + render.job_line(data, args.id, job) + "".join(f"\n{line}" for line in [*stopping, *replaced, *reworked, *ended]), {"id": args.id, **job})


def hear_opening(data: dict, job_id: str) -> None:
    """Hear the prompt that started the job's new agent on this Mac, read from its transcript beside the session recording it, as agents.hold hears it at that agent's first step, so a prompt quoting a text the job holds, such as the question its last lane asked, leaves nothing held, and one lacking it leaves the text for the watch's ANSWER undelivered line to the agent's starter; nothing when no session bound to the drive records it or its transcript is not written yet."""
    job = data["jobs"][job_id]
    if (holder := watch.session_of(job, data)) is None:
        return
    path = watch.agent_transcript(holder, job, data)
    if prompt := agents.opened(path):
        superseded.hear(data, job_id, prompt, state.parse_time(agents.opening_row(path)["timestamp"]))


def fix_kept(data: dict, job_id: str) -> None:
    """His stop carries the scope his words give it, so nothing stops the last running job on an open incident's fix."""
    job = data["jobs"][job_id]
    for order_id in state.open_incidents(data, [job["order"], *job["carries"]]):
        if state.INCIDENT_FIX not in state.job_parts(job, order_id) or any(
            other_id != job_id
            and other["state"] not in state.FINISHED_JOB_STATES
            and order_id in (other["order"], *other["carries"])
            and state.INCIDENT_FIX in state.job_parts(other, order_id)
            for other_id, other in data["jobs"].items()
        ):
            continue
        raise DriveError(
            f"{job_id} is the last running job on incident {order_id}'s fix, and nothing stops an open incident's fix: his stop in one"
            ' thread stops that thread\'s work, as his "leave it alone here" of Nov 10 9:19 PM meant his own thread. Add the job that takes'
            f" the fix over first, with drive job add <kind> --order {order_id} --part {state.INCIDENT_FIX}, or close {order_id} once its fix is live"
        )


def job_clock(job_id: str, due: str | None) -> str | None:
    """The time a job's clock runs out, which the watch says once as DUE if the job still runs then; a time already past is refused."""
    if due is None:
        return None
    moment = state.now()
    at = state.parse_due(due, moment)
    if at <= moment:
        raise DriveError(f"{job_id}: refused, no clock set: {render.when(state.stamp(at))} is already past; set a time to come")
    return state.stamp(at)


def refuse_a_crossed_order(name: str, data: dict, own: str, other: str, acting: str) -> None:
    """Refuse a job carrying, or a PR serving, an order across the line between drive tooling and his feature work: the coordinator acts on no "Drive tooling:" order, and a tooling order's job or PR never carries or serves his feature order, nor the reverse."""
    order = find(data["orders"], other, "order")
    if state.tooling(order) and state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID")) == "coordinator":
        raise DriveError(
            f'{acting} {other}: refused, {other} is a "{state.TOOLING}" order, which only the meta session acts on: send a'
            f" defect record to meta@{name} naming what it needs, and the meta session's builder takes it over"
        )
    if state.tooling(order) == state.tooling(data["orders"][own]) or own == other:
        return
    kinds = {True: "a drive tooling order", False: "an order of his feature work"}
    raise DriveError(
        f"{acting} {other}: refused, {own} is {kinds[state.tooling(data['orders'][own])]} and {other} is {kinds[state.tooling(order)]}, and"
        f" one job or PR never carries both: start a job on {other} with drive job add <kind> --order {other}"
    )


def told_him(name: str, args: argparse.Namespace) -> None:
    """An incident's job waits on his card only once the incident's thread says so: the line `talk.keep_line` keeps on each card's question for the post of the drive's there linking it, with what it blocks and each job building its recommended option meanwhile, or that nothing is built before his pick, read from the drive's state, never from Slack."""
    from drive import view

    if not (args.card or args.waits):
        return
    data = state.load(name)
    job = find(data["jobs"], args.id, "job")
    cards = [card for card in [args.card, *args.waits] if card and state.card_wait(data, card)]
    incidents = state.open_incidents(data, [job["order"], *job["carries"]])
    if not (cards and incidents):
        return
    if not args.told:
        raise DriveError(
            f"{args.id} works on incident {', '.join(incidents)}: a card that blocks it is said in the incident's thread at once, a line"
            " linking the card with what it blocks and each job building its recommended option meanwhile, or that nothing is built before his pick; post that line with say, which keeps it, then pass --told <its link>."
            f" {TOLD_LINK}"
        )
    threads = state.incident_threads(data, incidents)
    lines = {card: [view.told_line(data, thread, card) for thread in threads] for card in cards}
    if unlinked := [card for card, found in lines.items() if view.message_key(args.told) not in {view.message_key(line["link"]) for line in found if line}]:
        raise DriveError(
            f"--told {args.told} is no line the drive keeps in the incident's thread, {' or '.join(threads) or 'which it has none of yet'}, linking {', '.join(unlinked)}:"
            " say keeps each line it posts in a thread on each question of the drive drawn on a card the line links, so ask a card no question holds with drive job ask,"
            f" and post the line linking each card that blocks the incident with say. {TOLD_LINK}"
        )


def shown_card(name: str, url: str) -> dict | None:
    """His open question card at this link as cc-slack lists it, read again when the drive's copy lacks it; None when his cards cannot be read or it is not open."""
    from drive import view

    found = view.live_cards(name)
    if not (found and view.asked_card(found, url)):
        view.refresh_cards(name)
        found = view.live_cards(name)
    return found and view.asked_card(found, url)


def hold_card(job: dict, url: str, shown: dict | None) -> None:
    """Keep the card on the job's newest question it draws, found by its waits id or its link, so a question that took the card by its `Replaces:` line holds it ahead of the one it folded in, else on a question of its own, kept with no `Holds:` line, so it holds every change of the job's work and each card a job asked on stays open until his answer to it."""
    from drive import view

    waits = shown and shown["card"].get("waits")
    question = next(
        (
            question
            for question in reversed(job["questions"])
            if waits and question["waits"] == waits or question["card"] and view.message_key(question["card"]) == view.message_key(url)
        ),
        None,
    )
    if question is None:
        question = state.new_question(None, shown and shown["question"], state.stamp(), holds=None)
        job["questions"].append(question)
    question.update(card=url, waits=question["waits"] or waits)


def asked_on(name: str, waits: str) -> set[str]:
    """Each card his question under the waits id is drawn on, by its message, read before it is drawn again: the open one cc-slack lists under the id and each one a job's question keeps."""
    from drive import view

    try:
        listed = view.cards(name)["questions"]
    except slack.SlackError as error:
        raise DriveError(f"nothing posted: a card drawn again under its waits id moves each wait on an older copy of it to the card, which needs his cards, and cc-slack could not list them: {error}") from None
    kept = (question for job in state.load(name)["jobs"].values() for question in job["questions"] if question["waits"] == waits and question["card"])
    return {view.message_key(entry["url"]) for entry in listed if entry["card"].get("waits") == waits} | {view.message_key(question["card"]) for question in kept}


def follow_card(name: str, waits: str, asked: set[str], url: str) -> None:
    """Record the card cc-slack drew under the waits id as the card of each question asked under it, its thread joining each order of the job asking it, and point each wait of a job on a card it was drawn on before at its waits id: a card asked again under its waits id is redrawn where it sits, so a wait moves only off an older copy cc-slack retired for it."""
    from drive import view

    moved = asked - {view.message_key(url)}
    def follow_card_write(data: dict):
        for job_id, job in data["jobs"].items():
            for question in job["questions"]:
                if question["waits"] == waits:
                    question["card"] = url
                    state.join_card(data, job_id, url)
        for job in data["jobs"].values():
            job["waits"] = list(dict.fromkeys(waits if view.message_key(wait) in moved else wait for wait in job["waits"]))

    state.update(name, None, follow_card_write)


def job_ask(args: argparse.Namespace) -> int:
    """Keep a job's ASK in the drive once it is in the shape he reads, each block in its own file; from a file, stdin with -, or with neither a remote lane's latest report: each block its `ASK <file> [<file>…]` pointer names, else the report itself, one opening `DESIGN <question>`, a reader's return, read as the ASK it puts to him. Keeping it draws its card in the same call, under the waits id it mints for the question, and prints the file and the card, or that a card that did not draw goes up on the watch's next read. A block asking again what one of the job's questions his answer has not closed asks, or with --redraws asking in new words what the card it names asks, open or withdrawn, the job's own or one of another job on its orders that it takes over, takes that question's place and redraws its card under its waits id, and one asking what another job's open question asks is refused; one whose `Replaces:` line names open questions, comma-separated, each by the file it is kept in or its card's waits id, built on them and asking every choice, takes the first one's card and waits id the same way and keeps each open, folded into this one, each other named card shown asked again with a link to it. With --drop, throw away a question the job kept with no card drawn for it, and with --answer too, end its question on his card on that recorded answer of his, by `answered_drop`."""
    if args.id is None:
        raise DriveError("name the job whose ASK this keeps: drive job ask <j> <file>; drive job ask <j> --check <file> runs every check it runs and keeps nothing")
    name = state.resolve(args.drive)
    if args.check:
        print(ask_checked(name, args.id, named_text(args.check, "ASK block file"), args.unsettled, args.redraws))
        return 0
    if args.answer and not args.drop:
        raise DriveError(f"--answer ends the question --drop names: drive job ask {args.id} --drop <the file it is kept in> --answer {args.answer}")
    if args.answer:
        dropped = Path(args.drop).expanduser()
        return emit(args, "\n".join(answered_drop(name, args.id, dropped, args.answer)), {"id": args.id, "dropped": str(dropped), "answer": args.answer})
    if args.drop:
        dropped = Path(args.drop).expanduser()
        title, replaced = drop_ask(name, args.id, dropped)
        gone = "a later ask of it replaced its question, so only the file is deleted" if replaced else "its question kept there is dropped and the file deleted"
        line = f'{args.id} no longer asks "{render.cut(title or "", 80)}" from {dropped}: {gone}, so nothing counts it open'
        return emit(args, line, {"id": args.id, "dropped": str(dropped), "title": title, "replaced": replaced})
    report = find(state.load(name)["jobs"], args.id, "job").get("report")
    texts = [named_text(args.file, "ASK block file")] if args.file else [named_text(str(file), "ASK block file") for file in state.ask_files(reads.opening(report or ""))] or [report]
    if not all(texts):
        raise DriveError(f"{args.id} sent no report to read an ASK from; pass the file its ASK block is in, or - and the block on stdin")
    for text in texts:
        kept, asked, question, said = keep_ask(name, args.id, text, args.unsettled, args.redraws)
        emit(args, said, {"id": args.id, "question": str(kept), "waits": question["waits"], "card": question["card"], "closed": question["closed"], **(vars(asked) if asked else {})})
    return 0


def drop_ask(name: str, job_id: str, file: Path) -> tuple[str | None, bool]:
    """Throw away a question the job kept with no card drawn for it, such as one refused for an answer of his that settles it: it leaves the job's questions, so nothing counts it open, and its file is deleted unless another of the job's questions keeps it; a question on a card is refused, since only his answer to that card closes it. A file of the job's whose question a later ask of the job replaced, as drive job ask printed its path, is deleted alone, a drop racing another of the same file finding it gone and deleting nothing, with no title. A question a `Replaces:` line folded into the dropped one asks on its own again. Its title, and whether a later ask had replaced it."""
    asks = state.drive_dir(name) / "asks"
    def dropped(data: dict) -> tuple[dict, dict | None]:
        job = find(data["jobs"], job_id, "job")
        if (question := state.kept_in(job, file)) is None:
            if file.parent.resolve() != asks.resolve() or not file.name.startswith(f"{job_id}-") or not file.exists():
                raise DriveError(f"{file} keeps no question of {job_id}; drive status {job_id} names the file each of its questions is kept in")
            return job, None
        if question["card"]:
            ended = (
                f'ended {closed["how"]} there, so nothing counts it open'
                if (closed := question["closed"])
                else f"which only his answer closes: when a recorded answer of his already settles it, end it on his words with drive job ask {job_id} --drop {file} --answer <that answer's id>"
            )
            raise DriveError(
                f'refused, nothing dropped: {job_id}\'s question "{render.cut(question["title"] or "", 80)}" kept in {file} is on his card {question["card"]},'
                f" {ended}; drop only a question kept with no card drawn for it"
            )
        job["questions"] = [other for other in job["questions"] if other is not question]
        for other in (other for each in data["jobs"].values() for other in each["questions"] if other["replaced"] and Path(other["replaced"]).resolve() == file.resolve()):
            other["replaced"] = None
        if job["said"] == question["title"] and job["questions"]:
            job["said"] = job["questions"][-1]["title"]
        job["updated"] = state.stamp()
        return job, question

    job, question = state.update(name, None, dropped)
    if question is None:
        try:
            text = file.read_text()
        except FileNotFoundError:
            return None, True
        file.unlink(missing_ok=True)
        return limits.ask_block(text).splitlines()[0].removeprefix("ASK ").strip(), True
    if state.kept_in(job, file) is None:
        file.unlink(missing_ok=True)
    return question["title"], False


def answered_drop(name: str, job_id: str, file: Path, answer_id: str) -> list[str]:
    """End the job's own open question on his card kept in this file on his recorded answer that already settles it, by `ended_on`, its card closing in place on his words and the link where he gave them: the way out for a card whose recommended option his answer already chose, which no redraw may ask again. On his ruling state.TOOLING_DETAILS_RULING it ends a question of a drive tooling order, the meta session or its job having read it as asking him only a detail, decided on the recommended option the file keeps, by settled.ruled_close as `keep_decided` keeps one no card was drawn for, its card showing settled.TOOLING_DETAILS's decided words and a link to his ruling's message; refused there on any other order, since his ruling reaches drive tooling alone, and for a card kept unsettled, which asks him to lift words of his. On his words state.UNWAITED_RULING that the drive not wait on him it ends the question decided by the drive on its recommended option, as settled.UNWAITED words it, recorded first as an answer of its own by `decision`, which the close names, refused for a card kept unsettled. Either ruling is refused on an order limited to options, whose card stays his. Refused for a question on no card, which --drop alone throws away, one that ended, and an answer holding no words of his. What was done, a line each, then who builds on it, by `builds_on`."""
    data = state.load(name)
    job = find(data["jobs"], job_id, "job")
    question = state.kept_in(job, file)
    if not (question and question["card"] and question["closed"] is None):
        raise DriveError(
            f"refused, nothing ended: {file} keeps no open question of {job_id} on his card, so --answer has no card to close;"
            f" drive job ask {job_id} --drop {file} throws away one kept with no card, and drive status {job_id} names the file each of its questions is kept in"
        )
    answer = settled.shown(answer_id, data["roles"]["coordinator"]["cwd"])
    if not (words := settled.his(answer["body"])):
        raise DriveError(f"refused, nothing ended: cc-notes keeps no words of his under {answer_id}, so it settles no card; name the recorded answer whose words settle it")
    title = render.cut(question["title"] or "", 80)
    if answer["id"][:7] in (state.TOOLING_DETAILS_RULING, state.UNWAITED_RULING) and data["orders"][job["order"]]["options_only"]:
        raise DriveError(
            f"refused, nothing ended: {job_id}'s order {job['order']} is limited to options, so its card stays his until he picks; no ruling of his decides it"
        )
    if answer["id"][:7] == state.UNWAITED_RULING:
        if question["unsettled"]:
            raise DriveError(f'refused, nothing ended: his card asks him to lift "{render.cut(question["unsettled"], 200)}", which only his answer decides, so the drive decides nothing under his words {state.UNWAITED_RULING}')
        option = limits.question_problems(limits.ask_block(file.read_text()), kept=True)[0].recommended
        words = settled.UNWAITED.format(option=option)
        decided = decision(name, job_id, question, words)
        said = f'{job_id}\'s question "{title}" ended decided by the drive on its recommended option, "{render.cut(option, 200)}", under his words {state.UNWAITED_RULING}, as its answer {decided}'
        ended = ended_on(name, state.new_close("answered", words, None, answer=decided), question["waits"], question["card"], f'"{render.cut(words, 300)}"')
        return [said, *ended, f"drive answer {decided} --dropped {job['order']}:<part> takes off each part its pick rules out", builds_on(state.load(name), job_id)]
    if answer["id"][:7] != state.TOOLING_DETAILS_RULING:
        said = f'{job_id}\'s question "{title}" ended answered by his answer {answer["id"][:7]}, "{render.cut(words, settled.QUOTED_CHARS)}"'
        ended = ended_on(name, state.new_close("answered", words, settled.given_at(data, answer), answer=answer["id"]), question["waits"], question["card"])
        return [said, *ended, builds_on(state.load(name), job_id)]
    if not state.tooling(order := data["orders"][job["order"]]):
        raise DriveError(
            f'refused, nothing ended: his ruling {state.TOOLING_DETAILS_RULING} leaves to a job only the details of drive tooling, "(different than non-tooling changes)",'
            f' and {job_id}\'s order {job["order"]} is no Drive tooling order: "{render.cut(order["text"], 120)}", so only his answer on its card ends it'
        )
    if question["unsettled"]:
        raise DriveError(f'refused, nothing ended: his card asks him to lift "{render.cut(question["unsettled"], 200)}", which only his answer decides, so no job decides it under his ruling {state.TOOLING_DETAILS_RULING}')
    option = limits.question_problems(limits.ask_block(file.read_text()), kept=True)[0].recommended
    closed = settled.ruled_close(option, settled.given_at(data, answer), state.TOOLING_DETAILS_RULING)
    said = f'{job_id}\'s question "{title}" ended decided on its recommended option, "{render.cut(option, 200)}", under his ruling {state.TOOLING_DETAILS_RULING} on tooling details'
    return [said, *ended_on(name, closed, question["waits"], question["card"], f'"{render.cut(closed["words"], 300)}" linked to {closed["link"] or "itself"}'), builds_on(state.load(name), job_id)]


def decision(name: str, job_id: str, question: dict, words: str) -> str:
    """Record the drive's decision on the job's question, drawn on his card, as an answer of its own in cc-notes, labelled settled.DECISION_LABEL and under:<his ruling state.UNWAITED_RULING>, and in the drive at its card in the shape `drive answer` keeps his, its card marked settled and nothing else listed, so no job is sent it as his words and the watch owes no mark on it, while `drive answer <its id> --dropped` takes off what its pick rules out and his answer to the same question supersedes it; refused while cc-notes holds another standing answer to that question, which it would supersede. Its id."""
    data = state.load(name)
    cwd = data["roles"]["coordinator"]["cwd"]
    asked = settled.titled(question["title"])
    if other := next((answer["id"] for answer in settled.listed(f"drive:{name}", cwd) if state.same_question(answer["title"], asked) and settled.plain(settled.his(answer["body"])) != settled.plain(words)), None):
        raise DriveError(f'refused, nothing ended: cc-notes holds answer {other[:7]} to "{render.cut(asked, 120)}", which a decision of the drive would supersede: drive job ask {job_id} --drop {question["file"]} --answer {other[:7]} ends it on that answer')
    decided = settled.recorded(name, question["title"], words, [settled.DECISION_LABEL, f"under:{state.UNWAITED_RULING}"], question["card"], cwd)

    def decision_write(data: dict):
        moment = state.stamp()
        cards = {question["waits"]: {"mark": "settled", "job": job_id, "card": question["card"]}}
        data["answers"][decided] = {"words": words, "link": question["card"], "at": moment, "said": moment, "cards": cards, "jobs": {}, "approves": None}
        return data

    state.update(name, None, decision_write)
    return decided


def ask_checked(name: str, job_id: str, text: str, unsettled: str | None, redraws: str | None) -> str:
    """What `drive job ask --check` prints of an ASK block, keeping nothing, refused as `keep_ask` refuses it: that his answer to this same question, asked in the same words, settles it, checking nothing, else ready once every check `keep_ask` runs passed, by `checked_ask`, its Show me pictures drawn in a folder deleted after it, with settled.DECIDES or settled.REHEARSES, or settled.APPROVES for one his standing approval covers, on stderr for one kept decided with no card."""
    text = limits.ask_block(text)
    if answer := settled.answered(state.load(name), asked_title(text)):
        return f"no card: he already answered this same question, asked in the same words, at {answered_at(answer)}, so drive job ask keeps it settled on his words, checking nothing and drawing no card"
    with tempfile.TemporaryDirectory(prefix="drive-ask-check-") as scratch:
        asked, *_, deciding = checked_ask(name, job_id, text, unsettled, redraws, Path(scratch))
    option = render.cut(asked.recommended, 200)
    if deciding in settled.RULED:
        print(settled.RULED[deciding][2].format(option=option, ruling=deciding), file=sys.stderr)
    elif deciding:
        print(settled.APPROVES.format(option=option, answer=deciding[:7]), file=sys.stderr)
    return "ready"


def asked_title(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "").removeprefix("ASK ").strip()


def asked_unsettled(job: dict, text: str, unsettled: str | None) -> str | None:
    """The --unsettled an ASK is asked under: the one given, else the one an agent gave the job's kept question with its title, so a reader's DESIGN report asked again keeps it; never Opus's mark, which each ask's own read of his words decides."""
    if unsettled is not None:
        return unsettled
    title = asked_title(text)
    return next((question["unsettled"] for question in job["questions"] if question["unsettled"] and not settled.READ_MARK.search(question["unsettled"]) and state.same_question(question["title"], title)), None)


def checked_ask(name: str, job_id: str, text: str, unsettled: str | None, redraws: str | None, pictures: Path) -> tuple[limits.Question, str, str | None, list[dict], str | None]:
    """The question the job's ASK block puts to him, the words of its `Holds:` line his card holds, the --unsettled it is kept under, the one `asked_unsettled` reads else Opus's mark, and the recorded answers the release gate, `brief.approval_refusal`, reads as ordering a release its recommended option names in backticks, which his card offers as its Already answered tap, and the ruling or standing approval of his under which `keep_ask` keeps it decided on its recommended option with no card, else None, once every check `drive job ask <j>` runs passes: refused naming at once every way its words, the card cc-slack draws from them and the job it is asked for are out of shape, a summary sentence recommending an option by a recorded answer of his that --unsettled does not name among them, since that answer already chose it, each rule one entry of the list, its words counted as his card draws them, each reference in the summary filled as say fills it, an {order:<o>} as the opening of his order's words, and each paragraph `cc-slack check-post` finds a wall of text in on the card as it draws; then each Show me picture drawn under pictures, refused with why one does not draw; then Jev's read of whether its options differ in how a screen looks with a screenshot missing or choose between architectures with an ```arch diagram missing, by `limits.unread`, which in the same call prints each name the card never says in plain words, a code name, a capitalised word no sentence opens, or a run id or word the drive coined that `cc-slack check-post` finds on the card as it draws; each raw id check-post finds there is printed too, refusing nothing, by his pick 320b7a7; then each release note `brief.release_notes` reads as speaking to a release its options, their descriptions or its Show me name in backticks is printed on stderr with its words, refusing nothing; last, for a card nothing keeps unsettled, no answer of his orders its release and no card of his already asks, his standing approval covering its order or holding it in Opus's read, by `settled.approved_by`, said by `--check` as APPROVES, else, for a job a ruling of his leaves some questions to, by settled.leaving, a drive tooling order's or a builder's of a design his words ordered from scratch, Opus's read of whether it asks him only what that ruling leaves to the job, by `settled.detail`, said by `--check` as DECIDES; and on any order, before those reads, a card whose recommended option names in backticks only releases brief.rehearsal(, data) reads as on staging or sandbox alone, and that no --unsettled, --redraws, `Replaces:` line or drawn card of the same question holds, decided under his ruling state.REHEARSAL_RULING, said by `--check` as REHEARSES. `drive job ask <j> --check` and `keep_ask` both check here, so --check prints every finding before any card draws."""

    data = state.load(name)
    job = find(data["jobs"], job_id, "job")
    orders = [job["order"], *job["carries"]]
    unsettled = asked_unsettled(job, text, unsettled)
    asked, problems = limits.question_problems(text, unsettled=unsettled, hurting=any(state.hurts_now(data, data["orders"][order_id]) for order_id in orders))
    recorded = recorded_answers(data, job, text)
    cited = cited_answers("\n".join(asked.summary), recorded)
    releases = brief.card_releases(f"{asked.recommended}\n{asked.hints.get(asked.recommended, '')}", data)
    ordered = [(tap(answer), release) for release in releases if not brief.rehearsal(release, data) for answer in recorded if not brief.approval_refusal(f"{release} --order {answer['answer']}", data)]
    if (tapped := taps([answer for answer, _ in ordered], settled.deciding(asked, cited))) and len(asked.options) == limits.OPTION_COUNT.stop - 1:
        problems.append(
            f'his card adds the option "{limits.ALREADY_ANSWERED}" for his recorded answer {tapped[0]["answer"]}, which already answers it,'
            f" and a card draws at most {limits.OPTION_COUNT.stop - 1}: keep {limits.OPTION_COUNT.stop - 2} options of your own"
        )
    recommending = [sentence for sentence in limits.SENTENCE_BREAK.split("\n".join(asked.summary)) if re.search(r"\brecommend", sentence, re.IGNORECASE)]
    if chosen := next((answer for sentence in recommending for answer in cited_answers(sentence, cited) if answer["answer"] not in (unsettled or "")), None):
        problems.append(limits.Advice(
            f'its summary recommends "{asked.recommended}" by his recorded answer {chosen["answer"]}, "{render.cut(chosen["words"], settled.QUOTED_CHARS)}",'
            f" which already chose it, so no card asks him again: {settled.CHOSEN.format(id=chosen['answer'], job=job_id)}; a card asking him to lift those words names {chosen['answer']} in --unsettled"
        ))
    findings = []
    try:
        found = refs.resolved(name, "\n".join(asked.summary))
    except DriveError as error:
        problems.append(str(error))
    else:
        shown = replace(already_answered(asked, tapped), summary=[refs.slack(line, found, None) for line in asked.summary]).card(name, "")
        findings = slack.check_post("\n".join([shown["title"], shown["body"], *shown["options"], *(shown.get("hints") or {}).values()]), [])
        problems += [
            f"his card draws a paragraph opening {walled['opening']} that is {walled['count']} sentences, more than the {walled['most']} cc-slack check-post"
            f" lets one paragraph hold: give each line of the ASK at most {walled['most']}, the summary keeping to three lines and the rest going to a line"
            f" under `Findings:` his card does not draw ({limits.TOLD_RULING})"
            for finding in findings
            if (walled := limits.WALLED.match(finding))
        ]
        if (drawn := limits.card_words(shown)) > limits.QUESTION_WORDS:
            problems.append(
                f"the ASK's question, summary and options are {drawn} words as his card draws them, each reference in the summary drawn as"
                f" the words that name it, an {{order:<o>}} as its order's title, and at most"
                f" {limits.QUESTION_WORDS} in all: give a reference a few words of its own, as {{order:<o>|<a few words>}}, or cut the summary"
            )
        linked = {state.pr_key(url[0]) for url in state.PR_URL.finditer("\n".join([*asked.summary, *(ref["url"] for ref in found.values() if ref["kind"] == "pr")]))}
        if unlinked := [pr["url"] for key, pr in data["prs"].items() if pr["state"] == "open" and job_id in state.pr_jobs(data, pr) and key not in linked]:
            problems.append(
                f"{job_id} has {', '.join(unlinked)} open, which its ASK's summary does not link, so his card would describe its work as if no PR"
                " were up: link each in the summary by what it does, as <url|what it does>"
            )
    problems += release_holds(data, job, asked, text)
    problems += release_scope(data, asked, text, recorded)
    if typed := limits.typed_time("\n".join([asked.title, *asked.summary, asked.cause, asked.interim, *asked.show_me, *asked.options, *asked.hints.values()]), data):
        problems.append(f"his card draws these words, and {typed}")
    said = "\n".join([asked.title, *asked.summary]).casefold()
    if unnamed := [pr["port_of"] for pr in data["prs"].values() if pr["job"] == job_id and pr.get("port_of") and f"port of #{pr['port_of']}" not in said]:
        problems.append(
            f'{job_id}\'s PR is a port of #{unnamed[0]}: its ASK\'s summary says "port of #{unnamed[0]}", as the text of that PR\'s link,'
            f" and what in the target tree forces a choice #{unnamed[0]} did not make, such as what that tree lacks"
        )
    parts = [part for order_id in orders for part in data["orders"][order_id]["parts"]]
    holds = asked.holds and state.held_words(data, asked.holds, job_id)
    if holds and job["kind"] in state.CHANGE_KINDS and not (state.held_prs(data, asked.holds, job_id) or limits.HELD_BRANCH.search(asked.holds)):
        problems.append(
            f"`Holds: {asked.holds}` names no PR the drive tracks on {job_id}'s orders or waiting on a part {job_id} carries, as the PR its staging test tests, and no branch, nor a PR waiting on {job_id} by its id, and a {job['kind']}'s card holds from landing only"
            " the PRs and branches its `Holds:` line names, so his card would hold none of its work: name each PR its options alter by its link or #N,"
            f" after drive pr add when the drive does not track it, and each branch building an option in backticks, as `{job_id}-<what it builds>`,"
            f" or write `Holds: none: <why>` ({limits.HOLDS_RULING})"
        )
    elif holds and job["kind"] == "reader" and not state.names_part(holds, parts):
        problems.append(
            f"`Holds: {asked.holds}` names no change of {job_id}'s orders, so his card would hold nothing from landing: name each change its"
            f" options alter by its words, of {'; '.join(json.dumps(part, ensure_ascii=False) for part in parts)}, or a PR the drive tracks"
            f" on them by its link or #N, or write `Holds: none: <why>` ({limits.HOLDS_RULING})"
        )
    if redraws:
        try:
            superseded.redrawn(data, job_id, redraws)
        except DriveError as error:
            problems.append(str(error))
    if found_elsewhere := elsewhere(data, job_id, asked.title, redraws):
        problems.append(asked_elsewhere(job_id, *found_elsewhere))
    for ref in asked.replaces:
        try:
            replacing(data, job_id, asked, ref)
        except DriveError as error:
            problems.append(str(error))
    if refused := slack.draw_refusal(asked.card(name, f"{name}:{job_id}-0")):
        problems.append(f"the ASK's card is out of shape, so cc-slack would not draw it: {refused}")
    if refused := limits.shaped(problems):
        raise DriveError(limits.out_of_shape(refused))
    picture.drawn(pictures, asked.show_me)
    if raw := limits.checked(findings, limits.RAW_IDS):
        print(limits.RAW.format(found=", ".join(raw), advisory=limits.ADVISORY), file=sys.stderr)
    if unshown := limits.unread(asked, findings):
        raise DriveError(unshown)
    for line in brief.release_notes(brief.card_releases("\n".join([*asked.show_me, *asked.options, *asked.hints.values()]), data), data):
        print(f"drive job ask: {line}", file=sys.stderr)
    if releases and all((brief.rehearsal(release, data) for release in releases)) and not (unsettled or redraws or asked.replaces or any(question["card"] for _, question in asking(data, asked.title))):
        return asked, holds, unsettled, [], state.REHEARSAL_RULING
    heard = [{"id": answer["answer"], "asked": answer["asked"], "said": answer["words"][: settled.BODY_CHARS]} for answer in recorded]
    case = limits.ask_case(text, asked, frozenset(answer["id"] for answer in heard))
    for answer, release in ordered:
        print(settled.ORDERED.format(id=answer["answer"], asked=render.cut(answer["asked"], 200), release=release), file=sys.stderr)
    words = judging_words(name, data, job, asked, text)
    drawn = redraws or asked.replaces or any(question["card"] for _, question in asking(data, asked.title))
    ordering = taps([answer for answer, _ in ordered])
    if unsettled or ordering or drawn:
        return asked, holds, unsettled, ordering, None
    order = data["orders"][job["order"]]
    if approval := settled.approved_by(data, None, job["order"], {"order": order["text"], "card": {"question": asked.title, "recommended": asked.recommended}}):
        return asked, holds, unsettled, ordering, approval
    leave = settled.leaving(data, job)
    return asked, holds, unsettled, ordering, leave.ruling if leave and settled.detail(case, words, leave) else None


def release_holds(data: dict, job: dict, asked: limits.Question, text: str) -> list[str]:
    """Why a card on a production release's order may not ask about a PR the ASK names that only its staging test holds, green and approved, by state.release_held: one changing nothing dangerous, by land.dangerous, the watch lands now and the cut waits on, so no option skips a component or holds the release for it; a dangerous one his card asks to merge, an option naming merge and the PR, never one leaving a component out or holding the release on its test; by his words state.RELEASE_HOLD_WORDS."""
    if not any(state.releasing(data, order_id) for order_id in [job["order"], *job["carries"]]):
        return []
    named = {state.pr_key(found[0]) for found in state.PR_URL.finditer(text)} | {f"{state.home(data)}#{number}" for number in re.findall(r"(?<![\w/])#(\d+)\b", text)}
    problems = []
    for key in sorted(key for key in named if key in data["prs"] and state.release_held(data, key)):
        number = key.rpartition("#")[2]
        if not (found := land.dangerous(data, github.files(key))):
            problems.append(
                f"{key} is green and approved and changes nothing dangerous, so the watch lands it now through the merge queue and the cut waits minutes on its landing,"
                f" its staging retest rerunning only what {key} touches: ask nothing about it, and offer no option leaving a component out or holding the release for it ({state.RELEASE_HOLD_WORDS})"
            )
        elif not any(re.search(r"\bmerge", option, re.IGNORECASE) and re.search(rf"#{number}\b|/pull/{number}\b", f"{option}\n{asked.hints.get(option, '')}") for option in asked.options):
            problems.append(
                f"{key} is green and approved, and only its staging test holds it, since it changes {', '.join(found[:3])}: his card asks to merge it, an option naming merge and #{number},"
                f" his pick recorded with drive answer <his answer> --dropped <order>:{state.STAGING_TEST.format(number=number)} so the lander lands it while he is present and the cut's staging retest"
                f" reruns only what it touches; offer no option leaving a component out or holding the release on its test ({state.RELEASE_HOLD_WORDS})"
            )
    return problems


def release_scope(data: dict, asked: limits.Question, text: str, recorded: list[dict]) -> list[str]:
    """Why an ASK naming a production release start in backticks may not draw: a start of the whole platform, the targets settled.platform_targets reads, leaving out a target, by settled.unexcused, that the card does not draw as `<target> fails: <the failing test>` and the ASK does not say is `<target> left out by his <id>: "<his words>"`, quoting his newest recorded answer on a release that the brief binds or the ASK cites, by his words settled.SCOPE_WORDS: a release ships everything his latest ruling on it does not exclude, read fresh from that ruling, never carried from an earlier plan or a reader's choice. A start carries each platform target it names as a target, and every one when it names the whole word of the repository's release provider; brief.scope_refusal checks what the release carries once it runs."""
    releases = [release for release in brief.card_releases(text, data) if brief.started(release, data) and not brief.rehearsal(release, data)]
    if not releases or not (whole := settled.platform_targets(data)):
        return []
    every = providers.of(data).release.whole
    drawn = "\n".join([*asked.summary, *asked.show_me, *asked.options, *asked.hints.values()])
    cited = "\n".join(line for line in text.splitlines() if " left out by his " in line)
    latest = max((answer for answer in recorded if RELEASE_WORD.search(f"{answer['asked']}\n{answer['words']}")), key=lambda answer: answer["at"], default=None)
    newest = f'his newest ruling on a release, {latest["answer"]}: "{render.cut(latest["words"], settled.QUOTED_CHARS)}"' if latest else "no recorded ruling of his on a release, so none leaves it out"
    named = [set(flags.get("targets", ())) for release in releases for flags in brief.started(release, data)]
    return [
        f"its release leaves out {target}, which no failing test and no ruling of his excludes: a release ships every target his latest ruling on it does not exclude, read fresh from"
        f" his words, never from an earlier plan or a reader's choice. Ship {target}, or draw `{target} fails: <the failing test>` on the card, or write"
        f' `{target} left out by his <id>: "<his words>"` quoting {newest} ({settled.SCOPE_WORDS})'
        for target in dict.fromkeys(target for words in named for target in settled.unexcused(set(whole) if every in words else words & set(whole), whole, f"{drawn}\n{cited}", [latest] if latest else []))
    ]


def judging_words(name: str, data: dict, job: dict, asked: limits.Question, text: str) -> list[dict]:
    """His words Opus reads a job's card against: those its brief binds, by brief.spec, his words on each order it works, by served_words, then each recorded answer of his cc-notes matches to the card's words that neither names."""
    spec = brief.spec(name, data, job)
    words = [*brief.judging(spec), *served_words(data, job)]
    named = {entry["id"] for entry in [*spec, *words]}
    return words + [word for word in settled.matched(["\n".join([asked.title, *asked.summary, *asked.options]), text], data) if word["id"] not in named]


def served_words(data: dict, job: dict) -> list[dict]:
    """His words on each order the job works, its own and each it carries, by settled.order_words."""
    return [word for order_id in [job["order"], *job["carries"]] for word in settled.order_words(data, order_id)]


def reads_as_detail(name: str, data: dict, job: dict, file: Path) -> bool:
    """Whether Opus reads the job's question kept in this file as asking him only a detail of drive tooling, by settled.detail on the card as limits.ask_case reads it against the words `checked_ask` reads it against, as the watch's daily sweep reads each open card of a Drive tooling order."""
    text = limits.ask_block(file.read_text())
    asked = limits.question_problems(text, kept=True)[0]
    cited = frozenset(answer["answer"] for answer in recorded_answers(data, job, text))
    return settled.detail(limits.ask_case(text, asked, cited), judging_words(name, data, job, asked, text), settled.TOOLING_DETAILS)


def keep_ask(name: str, job_id: str, text: str, unsettled: str | None, redraws: str | None = None) -> tuple[Path, limits.Question | None, dict, str]:
    """Keep the job's ASK block, or the ASK a DESIGN return puts to him, once it is in the shape he reads and the words his card draws type no clock time for work already done, in one file for its card's life, the file of the job's own question it takes the place of written over, the one whose card it takes first, so the path the first ask printed keeps its words, else a new file named for the hash of its words after the job, the question keeping that hash, which `kept_ask` reads to tell a file edited by hand, in place of the job's open question asking the same, or of each question `superseded.redrawn` finds holding the card `redraws` names by its waits id, the job's own or another job's on one of its orders, open or withdrawn, the job's own leaving the job and another job's staying on it folded into this one, set the job waiting on it, unless it is a builder, which keeps running to build the question's recommended option and return BUILT, and draw his card on it in the same call, as drive pr add draws a PR card, under the waits id of the question it takes the place of, the one `redraws` names first, else one minted for it: every ask of one question draws under its one waits id, which cc-slack redraws where it sits, new words included, the question taking the card of the one it takes the place of, so a redraw that fails leaves it on that card, which his answer there closes and --drop refuses, and a first draw that fails leaves it undrawn for the watch's next read, which draws it; a job waiting on an order he has not scheduled, by state.unscheduled_job, keeps it undrawn until he schedules it, by his answer state.UNSCHEDULED_RULING, and the watch draws it then. The question keeps the words of its `Holds:` line, with the parts each PR of state.held_prs they name serves appended, and each change state.change_builders wrote onto the words of the question whose card it takes beside the builder building it, by state.kept_builders, a PR the drive tracks on the job's orders or one waiting on the job or a part it carries, as the PR its staging test tests, which hold from landing each PR they name by link, #N or whole branch name and, among the PRs of jobs working a change they name, only those its design's builder, a job briefed on its research or a job they name by id carries, refused for a builder or codex lane when they name no such PR and no branch in backticks, and for a reader when they name no part of the job's orders; asked again with no --unsettled, as a reader's DESIGN report re-asks it, it keeps the --unsettled an agent gave its kept question with that title, never Opus's mark, which this ask's own read decides. One he already answered, the same words asked on a card he tapped or wrote on or under, or under a record keeping his words, is kept settled by that answer before any check of its shape or its card, by `keep_settled`, draws no card and leaves the job as it was, so it goes straight to building on his words, and the session holding its order hears so in one record; no answer he gave another question settles it. One he has not answered Opus reads, in one request, against his words on each order its job works, its own and each it carries, the words of his the drive quotes in each and each message of his in its thread, without the lines he quoted, and the recorded answers of his ccn ranks nearest to the block, with his rulings beside them, and each option his words rule out, each amount or choice they name with no tap and each ruling the card breaks is printed to the agent and refuses nothing, by his pick 320b7a7, his ruling on root causes read once against the card's recommended option, the recorded answers its job's brief binds or its block cites read beside them, with the lines of its findings naming one; the recorded answer or ruling of his Opus picks for its recommended option at settled.YES or more, the cited answer the card says that option goes against first, else the first Opus picks as sure for any other option it offers, keeps the card unsettled as `<id>: Opus reads the recommended option as breaking it`, or `<id>: Opus reads option "<that option>" as breaking it`, when no --unsettled names words of his and it is no cited answer the release gate, `brief.approval_refusal`, reads as ordering a release that option names in backticks, nor one recorded before a cited answer that orders that release or decides the card, since his newest words stand, which the question keeps for its card's Already answered tap, asking him to lift that one, so it holds its PR on an incident too, as --unsettled naming words of his does. One whose job's order or a carried one holds words Opus reads at settled.YES or more as telling it to ask him nothing on that work is refused with no card, by `settled.UNASKED`, unless Opus reads the card as asking a step his rules keep for him, a landing, a production release or a post as him. On a drive tooling order, or from a builder or codex lane of a design his words ordered from scratch, one Opus then reads as asking him only a detail, kept unsettled by nothing, ordering no release by an answer of his and asked on no card of his already, is kept decided on its recommended option under the ruling settled.leaving names, his ruling state.TOOLING_DETAILS_RULING or state.FRESH_DETAILS_RULING, by `keep_decided`, draws no card and leaves the job as it was, so it builds that option and opens its PR; so is one on any order his standing approval covers, by settled.approved_by. On any order, one whose recommended option names in backticks only releases on the release provider's rehearsal envs alone, by brief.rehearsal(, data), and that no --unsettled, --redraws, `Replaces:` line or drawn card of its question holds, is kept decided on that option by his ruling state.REHEARSAL_RULING before Opus reads it against his words, by `keep_decided`, and draws no card. One whose summary cites a recorded answer of his that Opus reads as deciding its question or making it moot, or with a recorded answer the release gate reads as ordering a release its recommended option names in backticks, draws its card with an Already answered option, his words under it, so his tap closes it on those words, and only those answers. A block whose summary links no PR the drive tracks open on the job is refused, so his card never says no PR while one is up. One the card `question_card` draws refuses stays kept as the job's pending question with its refusal and draws no card, so the watch says the job waits on a card never posted until it asks again or drops it. Each question of the job `asked_before` names is replaced, the one its file keeps included, so a file keeps one question and a question once kept settled on another answer of his draws its card when asked again. A block whose `Replaces:` line names open questions, comma-separated, each by the file it is kept in or its card's waits id, as a question built on them asking every choice does, takes the first one's card and waits id and leaves each open, folded into this one, so drawing its card redraws that card, `post_ask` shows each other named card asked again with a link to it, and his answer to this one ends them all; a question folded so, asked again, stays folded and draws no card. A job whose card another job's question took, by `took`, folds an ask of it into that question and draws no card, asked in that question's words or its own. Asked in other words, Opus reads it in that same call against each open card `repeating` finds, and each card Opus reads it as asking again is printed to the agent by settled.REPEATED, which shares no card: only an agent's `Replaces:` line or --redraws shares one, since a card Opus read as asked again once drew one hotfix's question in place of another's and his tap there closed both; asked again in its own words it is read against none. The question keeps the lines threads hold about the one it takes the place of, by `talk.keep_line`, and `post_ask` sends a thread `card_places` puts the card's line in a line only by `card_line`. What drive job ask prints: the file, then where his card stands."""
    text = f"{limits.ask_block(text).strip()}\n"
    data = state.load(name)
    job = data["jobs"][job_id]
    title = asked_title(text)
    unsettled = asked_unsettled(job, text, unsettled)
    fresh = state.drive_dir(name) / "asks" / f"{job_id}-{state.ask_hash(text)}.md"
    if answer := settled.answered(data, title):
        return keep_settled(name, job_id, text, fresh, title, unsettled, answer)
    asked, holds, unsettled, ordered, deciding = checked_ask(name, job_id, text, unsettled, redraws, state.drive_dir(name) / "pictures")
    if deciding:
        return keep_decided(name, job_id, text, fresh, asked, deciding)
    fresh.parent.mkdir(exist_ok=True)
    generic = superseded.generic(data)
    def ask_kept(data: dict) -> tuple[Path, dict]:
        job = data["jobs"][job_id]
        twins = [question for other, question in asking(data, asked.title) if other == job_id]
        targets = [other for other in {id(other): other for _, other in (replacing(data, job_id, asked, ref) for ref in asked.replaces)}.values() if not any(other is twin for twin in twins)]
        target = next(iter(targets), None)
        stale = asked_before(job, asked.title, fresh, generic)
        own = {id(other) for other in job["questions"]}
        redrawn = superseded.redrawn(data, job_id, redraws) if redraws else []
        taken = next((question for other, question in asking(data, asked.title) if other != job_id and took(data, job_id, question)), None)
        held = taken or target or next((twin for twin in [*redrawn, *twins] if twin["card"]), None) or next((twin for twin in [*redrawn, *twins] if twin["waits"]), None)
        question = state.new_question(None, asked.title, held["asked"] if held else state.stamp(), unsettled, card=held and held["card"], holds=state.kept_builders(holds, held and held["holds"]))
        question["waits"] = held and held["waits"] or state.new_waits(name, job_id)
        question["told"] = dict(held["told"]) if held else {}
        question["ordered"] = ordered
        if folded_into := next((twin["replaced"] for twin in twins if twin["replaced"]), None) or taken and taken["file"]:
            question.update(waits=held and held["waits"], replaced=folded_into)
        doubled = [] if folded_into else [twin for twin in twins if twin["card"] and twin["waits"] != question["waits"]]
        dropped = [*twins, *(other for other in redrawn if id(other) in own), *stale]
        replaced = [other for other in dropped if not any(other is twin for twin in doubled)]
        kept = Path(next((other["file"] for other in sorted(replaced, key=lambda other: other is not held) if other["hash"]), fresh))
        question.update(file=str(kept), hash=state.ask_hash(text))
        for other in [] if folded_into else [other for other in redrawn if not any(other is gone for gone in dropped)]:
            other["replaced"] = str(kept)
        gone = {other["file"] for other in replaced if other["file"]}
        for other in (other for each in data["jobs"].values() for other in each["questions"] if other["replaced"] in gone):
            other["replaced"] = str(kept)
        for other in [other for other in targets if not any(other is gone for gone in replaced)]:
            other["replaced"] = str(kept)
        moment = state.stamp()
        job.update(said=asked.title, updated=moment)
        if job["kind"] != "builder":
            job["returned"] = moment
            if not (job["kind"] == "reader" and job["state"] == "done"):
                job["state"] = "waiting"
        for each in data["jobs"].values():
            each["questions"] = [other for other in each["questions"] if not any(other is gone for gone in replaced)]
        job["questions"].append(question)
        def written() -> list[str]:
            kept.write_text(text)
            return []

        written() if name in state.BATCHED else state.after_save(name, written)
        return kept, question

    kept, question = state.update(name, None, ask_kept)
    if question["replaced"]:
        return kept, asked, question, str(kept)
    if steps := state.unscheduled_job(state.load(name), job_id):
        return kept, asked, question, f"{kept}\n{job_id} waits on {', '.join(steps)}, work he has not scheduled, so no card asks him this until he schedules it, by his answer {state.UNSCHEDULED_RULING}; the watch draws it then"
    card = ask_card(name, job_id, kept)
    try:
        said = post_ask(name, job_id, kept, card)
    except (DriveError, slack.SlackError) as error:
        said = (
            f"{job_id}'s card {question['card']} under {card['waits']} did not redraw, so it shows the words it last drew and his answer there answers this question: {error}"
            if question["card"]
            else f"{job_id}'s card under {card['waits']} did not draw, so the watch draws it on its next read: {error}"
        )
    return kept, asked, state.kept_in(state.load(name)["jobs"][job_id], kept), f"{kept}\n{said}"


def keep_settled(name: str, job_id: str, text: str, kept: Path, title: str, unsettled: str | None, answer: dict) -> tuple[Path, None, dict, str]:
    """Keep the job's ASK block his answer to that same question settles, closed on his words before any check of its shape or card, since no card draws from it: in place of each open question of the job asking it and each `asked_before` names, the question its file keeps standing when it already ended answered, and each question its `Replaces:` line names ending on his answer too. The file, no question shape, the question, and the line saying his words settle it."""
    data = state.load(name)
    asked = limits.question_problems(text, kept=True)[0]
    for ref in limits.replaced(text):
        replacing(data, job_id, asked, ref)
    settled_note(name, data, job_id, title, answer)
    question, job = keep_closed(name, job_id, text, kept, title, unsettled, answer["closed"])
    return kept, None, question, settled_line(job_id, job, question, answer)


def keep_decided(name: str, job_id: str, text: str, kept: Path, asked: limits.Question, by: str) -> tuple[Path, limits.Question, dict, str]:
    """Keep a job's ASK block decided on its recommended option by the ruling or standing approval of his `checked_ask` names, as `keep_settled` keeps one his answer settles: no card draws and the job goes on building. A ruling is the one settled.leaving names, state.TOOLING_DETAILS_RULING or state.FRESH_DETAILS_RULING, for a card Opus reads as asking him only what it leaves to the job, or state.REHEARSAL_RULING for a card on any order whose recommended option names only releases on staging or sandbox alone, and tells no thread, his standing approval's included. The file, the question asked, its question kept, and the line saying the job decided it."""
    data = state.load(name)
    option = render.cut(asked.recommended, 200)
    if by not in settled.RULED:
        question, job = keep_closed(name, job_id, text, kept, asked.title, None, settled.approved(asked.recommended, by, data["answers"][by]["link"]))
        return kept, asked, question, settled.APPROVED_LINE.format(file=kept, job=job_id, option=option, answer=by[:7])
    closed = settled.ruled_close(asked.recommended, settled.given_at(data, settled.shown(by, data["roles"]["coordinator"]["cwd"])), by)
    question, _ = keep_closed(name, job_id, text, kept, asked.title, None, closed)
    return kept, asked, question, settled.RULED[by][1].format(file=kept, job=job_id, option=option, ruling=by)


def keep_closed(name: str, job_id: str, text: str, kept: Path, title: str, unsettled: str | None, closed: dict) -> tuple[dict, dict]:
    """Keep the job's ASK block closed as given, drawing no card: in place of each open question of the job asking it and each `asked_before` names, the question its file keeps standing when it already ended answered, and each question its `Replaces:` line names ending the same way. The question kept and its job."""
    from drive import superseded

    refs, asked = limits.replaced(text), limits.question_problems(text, kept=True)[0]
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(text)
    generic = superseded.generic(state.load(name))
    def questioned(data: dict):
        job = data["jobs"][job_id]
        twins = [question for other, question in asking(data, title) if other == job_id]
        named = {id(target): (asker, target) for asker, target in (replacing(data, job_id, asked, ref) for ref in refs)}
        if (question := state.kept_in(job, kept)) is None or not question["closed"] or question["closed"]["how"] != "answered":
            question = state.new_question(str(kept), title, state.stamp(), unsettled, closed=closed | {"at": state.stamp()})
        question["hash"] = state.ask_hash(text)
        replaced = [other for other in [*twins, *asked_before(job, title, kept, generic)] if other is not question]
        job["questions"] = [*(other for other in job["questions"] if other is not question and not any(other is gone for gone in replaced)), question]
        job["updated"] = state.stamp()
        for asker, target in named.values():
            if not any(target is twin for twin in twins):
                superseded.end(data, asker, target, question["closed"])
        return question, job
    question, job = state.update(name, None, questioned)
    return question, job


def asked_before(job: dict, title: str, file: Path, generic: set[str]) -> list[dict]:
    """The job's questions an ask of this title kept in this file takes the place of, whatever it draws: each kept in that file, which keeps one question, each refused asking it in the same words or about the same subject, and each that ended with no card drawn in the same words."""
    from drive import superseded

    file = file.resolve()
    return [
        other
        for other in job["questions"]
        if other["file"] and Path(other["file"]).resolve() == file
        or other["refused"] and (state.same_question(title, other["title"]) or superseded.shared(title, other["title"], generic))
        or other["closed"] and not (other["card"] or other["waits"]) and state.same_question(title, other["title"])
    ]


def ask_card(name: str, job_id: str, file: Path) -> dict:
    """The card the job's question kept in the file draws, by `question_card`, under its waits id, one minted for a question holding none; a card it refuses is kept as the question's refusal by `refuse_draw`, once, and never on a question that ended or is on his card, so no card draws for it and the watch says the job waits on a card never posted until it asks again or drops it."""
    def ask_card_write(data: dict):
        question = state.kept_in(data["jobs"][job_id], file)
        question["waits"] = question["waits"] or state.new_waits(name, job_id)
        return question

    question = state.update(name, None, ask_card_write)
    try:
        return question_card(name, file, question["waits"])
    except DriveError as error:
        refuse_draw(name, job_id, file, question["waits"], str(error))
        raise DriveError(
            f"{error}\n{file} keeps this ASK as {job_id}'s pending question with this refusal: no card draws for it, and the watch says"
            f" {job_id} waits on a card never posted until {job_id} asks it again or `drive job ask {job_id} --drop {file}` drops it"
        ) from None


def card_places(data: dict, job: dict) -> tuple[list[str], tuple[str, str] | None, bool]:
    """The open incidents a job's card blocks, the channel and thread the job's lines go in, the open incident's or else its order's, and whether that thread takes the card's line: a thread outside his DM does, such as an alert's, so his card draws at his DM root alone and the line is its one message in that thread, while a thread of his DM takes the card itself, an incident's included, by his words "in the future let's not create split threads like this"."""
    incidents = state.open_incidents(data, [job["order"], *job["carries"]])
    where = state.promise_thread(data, data["orders"][incidents[0] if incidents else job["order"]])
    return incidents, where, bool(where and where[0] not in slack.homes(data))


def building(data: dict, job_id: str, *cards: str) -> list[str]:
    """The builder and codex jobs the drive's records hold building the recommended option of the job's card, named by its waits id or Slack link, none failed or stopped, so one done once its PR merged still builds it: the asker when it is one, each whose waits name the card and each whose work `state.part_cards` finds it holding."""
    from drive import view

    drawn = {view.message_key(card) for card in cards}
    return [
        other_id
        for other_id, other in data["jobs"].items()
        if other["kind"] in state.CHANGE_KINDS
        and other["state"] not in state.ENDED_JOB_STATES
        and (other_id == job_id or drawn & {view.message_key(wait) for wait in [*other["waits"], *state.part_cards(data, other_id)]})
    ]


def post_ask(name: str, job_id: str, file: Path, card: dict) -> str:
    """Post his card on the job's question kept in the file through `talk.posted`, which records it on each question asked under its waits id, in his DM where `card_places` puts it, while a thread outside his DM takes no line of it; the card keeps the line on the card's question. Each other open question of the job asking the same on another card ends superseded by this one, its card shown asked again with no buttons and a link to this one, and each wait on it moves to this one's waits id, so nothing waits on him under it; each question folded into this one on another card, as a `Replaces:` line folds it, shows that card the same way and takes this card and waits id, each wait on that card moving with it. What was done, for drive job ask to print."""
    from drive import talk, view

    data = state.load(name)
    job = data["jobs"][job_id]
    _, where, lined = card_places(data, job)
    link = talk.posted(name, {"card": card} | ({"thread_ts": where[1]} | ({"channel_id": where[0]} if where[0] != slack.dm() else {}) if where and not lined else {}))["permalink"]
    def post_ask_write(data: dict):
        live = state.kept_in(data["jobs"][job_id], file)
        doubled = [
            other
            for other in view.open_questions(data["jobs"][job_id])
            if other is not live and other["card"] and other["waits"] != live["waits"] and state.same_question(other["title"], live["title"])
        ]
        wait_on(data, doubled, live["waits"])
        for other in doubled:
            other["closed"] = asked_again(link, live["waits"])
        ended = [(job_id, other) for other in doubled] + fold_onto(data, live)
        return ended

    ended = state.update(name, None, post_ask_write)
    return "; ".join([f"his card {link} asks it under {card['waits']}", *superseded.follow(name, ended)])


def fold_onto(data: dict, live: dict) -> list[tuple[str, dict]]:
    """Move each open question folded into the live question, by `folded`, that is still on another card onto the live question's card and waits id, in the write the caller saves: each wait on its old card moves to the live waits id, it joins the live card's thread, and it stays open, so his answer on the live card ends it too, by `superseded.end`. The one way a question asked again on another card of the drive leaves its own card, which `drive job ask` and `drive cards withdraw --via` both take. Each as it ended on its old card, by its job, for `superseded.follow` to show ASKED_AGAIN there with a link to the live card and no buttons."""
    riding = {id(other) for other in folded(data, live["file"])}
    moved = [(owner, other) for owner, each in data["jobs"].items() for other in each["questions"] if id(other) in riding and other["card"] and other["waits"] != live["waits"]]
    wait_on(data, [other for _, other in moved], live["waits"])
    ended = [(owner, other | {"closed": asked_again(live["card"], live["waits"])}) for owner, other in moved]
    for owner, other in moved:
        other.update(waits=live["waits"], card=live["card"])
        state.join_card(data, owner, live["card"])
    return ended


def asked_again(link: str, waits: str) -> dict:
    """How a question ends that was asked again on a newer card, the card at this link under this waits id: superseded by the question there, its own card showing ASKED_AGAIN and a link to that card with no buttons."""
    return state.new_close("superseded", ASKED_AGAIN, link, by=waits)


def wait_on(data: dict, asked: list[dict], waits: str) -> None:
    """Point each wait of a job or PR on a card these questions were drawn on, by its waits id or permalink, at this waits id, in the write the caller saves, so his answer on the card they were asked again on wakes each and nothing waits on him under theirs."""
    from drive import view

    gone = {key for question in asked for key in (question["waits"], question["card"] and view.message_key(question["card"])) if key}
    for owner in [*data["jobs"].values(), *data["prs"].values()]:
        owner["waits"] = list(dict.fromkeys(waits if view.message_key(wait) in gone else wait for wait in owner["waits"]))


def settled_note(name: str, data: dict, job_id: str, title: str, answer: dict) -> None:
    """Tell the session that acts on the job, by state.job_lane, in one record that wakes nobody, that his answer to this same question settled the job's question, so it builds on his words with no card."""
    job = data["jobs"][job_id]
    lane = state.job_lane(data, job)
    where = answered_at(answer)
    text = f'{job_id} builds on his answer at {where} to the same question, "{render.cut(title, 120)}", asked in the same words, so no card draws for it'
    from drive import tools

    try:
        tools.sent(name, job_id, [{"kind": "evidence", "to": [f"{lane}@{name}"], "attr": {"job": job_id, "answered": where}, "text": text}])
    except DriveError as error:
        raise DriveError(f"nothing kept: his answer at {where} settles {job_id}'s question, and the record telling {lane}@{name} so did not post: {error}") from None


def settled_line(job_id: str, job: dict, question: dict, answer: dict) -> str:
    """What `drive job ask` prints for a question his answer to that same question settled: his words, where he gave them, and who builds on them now."""
    words = answer["closed"]["words"]
    said = f': "{render.cut(words, settled.QUOTED_CHARS)}"' if words else ""
    if job["state"] in state.FINISHED_JOB_STATES:
        then = f"{job_id} ended {job['state']}, so start the job that builds it now on the same part, its brief carrying his answer under Decisions that bind"
    else:
        then = f"{job_id} builds it now"
    return f'{question["file"]} keeps {job_id}\'s question settled, no card: he already answered this same question, asked in the same words, at {answered_at(answer)}{said}. Go ahead on it; {then}'


def answered_at(answer: dict) -> str:
    """Where his answer to a question is: the card he answered, else the link of his words, else the card's waits id, else the record keeping his words."""
    return answer["card"] or answer["closed"]["link"] or answer["waits"] or f"record {answer['closed']['seq']}"


def asking(data: dict, title: str) -> list[tuple[str, dict]]:
    """Each job's open question asking him what this title asks, oldest first, by its job: one question has one card, so every ask of it after the first redraws that card."""
    from drive import view

    return [(job_id, question) for job_id, job in data["jobs"].items() for question in view.open_questions(job) if state.same_question(question["title"], title)]


def elsewhere(data: dict, job_id: str, title: str, redraws: str | None) -> tuple[str, dict] | None:
    """Another job's open question asking him in these same words, whatever their case, spacing and punctuation, what this title asks, never one `drive job ask` refused, which draws no card, nor the one on the card `--redraws` takes over, nor one that `took` the job's card, which the job's ask folds into."""
    return next(
        ((other, question) for other, question in asking(data, title) if other != job_id and not question["refused"] and question["waits"] != redraws and not took(data, job_id, question)),
        None,
    )


def took(data: dict, job_id: str, question: dict) -> bool:
    """Whether another job's question took the job's card: it holds a waits id the job minted, as `--redraws` and a `Replaces:` line take one over, or a question of the job is folded into it."""
    return (question["waits"] or "").partition(":")[2].partition("-")[0] == job_id or any(other["replaced"] == question["file"] for other in data["jobs"][job_id]["questions"] if other["replaced"])


def replacing(data: dict, job_id: str, asked: limits.Question, ref: str) -> tuple[str, dict]:
    """The open question one name on the job's ASK's `Replaces:` line names, by the file it is kept in or its card's waits id, by its job, followed to the question a later ask folded it into, so a question built on open ones takes the first one's card and asks every choice; refused when no job keeps a question there or something closed it, since his answer on a closed card is already given, and when it is no card of an order the job works, by superseded.for_orders, or its card names a drive PR the asked card does not, by state.named_prs, since his one answer on the asked card ends it, as his tap on one PR's hotfix card once ended a question about another PR's production hotfix."""
    found = next(((job_id, kept) for job_id, job in data["jobs"].items() if (kept := state.kept_in(job, Path(ref).expanduser()))), None) or next(
        ((job_id, question) for job_id, job in data["jobs"].items() for question in job["questions"] if question["waits"] == ref and not question["replaced"]), None
    )
    while found and found[1]["replaced"]:
        found = next((job_id, kept) for job_id, job in data["jobs"].items() if (kept := state.kept_in(job, Path(found[1]["replaced"]))))
    if found is None:
        raise DriveError(f"`Replaces: {ref}` names no file a question of this drive is kept in, nor a card's waits id one holds; drive status <job> names the file and waits id of each question")
    if closed := found[1]["closed"]:
        raise DriveError(
            f'`Replaces: {ref}`: its question "{render.cut(found[1]["title"] or "", 80)}" ended {closed["how"]}, so it asks him nothing'
            " to replace: build on his words, and ask only what they leave open, with no Replaces line naming it"
        )
    owner, question = found
    job = data["jobs"][job_id]
    orders, order = [job["order"], *job["carries"]], data["jobs"][owner]["order"]
    title = render.cut(question["title"] or "", 80)
    if not superseded.for_orders(order, question, orders):
        raise DriveError(
            f'`Replaces: {ref}`: its question "{title}" asks for {order}, and {job_id} works {", ".join(orders)}, so his one answer on this card'
            " would end a question of other work: ask only what this work leaves open, with no Replaces line naming it"
        )
    drawn = limits.redrawn(Path(question["file"]).read_text()).drawn if question["file"] else []
    shown = set(state.named_prs(data, job["order"], "\n".join(asked.drawn)))
    if unshown := [key for key in state.named_prs(data, order, "\n".join(drawn)) if key not in shown]:
        raise DriveError(
            f'`Replaces: {ref}`: its question "{title}" names {", ".join(map(state.pr_url, unshown))}, which this card names nowhere, so his one answer'
            " here would end a question about a PR he never sees on it: name each on this card when one answer of his settles both, else ask with no Replaces line naming it"
        )
    return found


def asked_elsewhere(job_id: str, other: str, question: dict) -> str:
    """Why a job may not ask, in the same words, what another job's open question asks: his answer to that one card closes it everywhere, so the job waits on it, takes it over with --redraws when it carries on that job's work, or asks only what it leaves open."""
    where = (
        f"its open card {question['card']} ({question['waits']})"
        if question["card"]
        else f"its card {question['waits']}, not up yet" if question["waits"] else f"the block kept in {question['file']}, its card not drawn yet"
    )
    wait = question["waits"] or "<its waits id, once its card is up>"
    return (
        f'{other} already asks him "{question["title"]}" on {where}, in the same words, and one question has'
        f" one card, so his answer closes it everywhere. Wait on that card with a report opening WAITING <what> waits on {wait}, which frees {job_id}"
        f" at his answer; take it over with drive job ask {job_id} <file> --redraws {wait} when {job_id} carries on {other}'s work on one"
        " of its orders; or ask only what it leaves open, under a question of its own"
    )


def job_card(args: argparse.Namespace) -> int:
    """Print the question card a kept ASK block draws, its Show me code drawn as highlighted pictures, and give the question kept in that file the card's waits id; a card it refuses to draw for a question kept there with no card up is written on that question as its refusal, by `refuse_draw`."""
    name = state.resolve(args.drive)
    path, waits = card_source(name, args)
    try:
        card = question_card(name, path, waits)
    except DriveError as error:
        refuse_draw(name, state.waits_job(state.load(name), waits), path, waits, str(error))
        raise
    return emit(args, json.dumps(card), card)


def refuse_draw(name: str, job_id: str, path: Path, waits: str, refusal: str) -> None:
    """Keep a card `drive job card`, `drive job ask` or the watch could not draw as the refusal of the open question the job keeps in this file with no card up and no refusal yet, drawn under its own waits id or one no other question of the job holds but one folded into it, so `drive status` lists it refused, no card draws from it and the watch names each job waiting on it until `drive job ask` asks it again; a draw onto another question's card refuses only that draw."""
    def refused(data: dict) -> None:
        job = data["jobs"].get(job_id)
        question = job and state.kept_in(job, path)
        if not question or question["closed"] or question["card"] or question["refused"] or question["replaced"] or any(other is not question and other["waits"] == waits and not other["replaced"] for other in job["questions"]):
            return
        question.update(refused=refusal, waits=question["waits"] or waits)
        job["updated"] = state.stamp()

    state.update(name, None, refused)


def card_source(name: str, args: argparse.Namespace) -> tuple[Path, str]:
    """The kept ASK block a card draws and the waits id given for it: after a job, its newest kept block or the one named, under --waits or the waits id its card already has; a file alone, under --waits. `question_card` draws a question already holding a waits id under that one."""
    if not JOB_ID.fullmatch(args.ask):
        if not args.waits:
            raise DriveError(f"{args.ask}: a card drawn from a file needs --waits <drive>:<job>-<n>; drive job card <j> draws a job's newest kept ASK")
        return named_file(args.ask, "ASK block file").resolve(), args.waits
    job = find(state.load(name)["jobs"], args.ask, "job")
    named = Path(args.file).expanduser().resolve() if args.file else None
    kept = [question for question in job["questions"] if question["file"] and named in (None, Path(question["file"]).resolve())]
    if not (drawable := [question for question in kept if not settled.card_refusal(args.ask, question, Path(question["file"]))]):
        raise DriveError(
            (kept and settled.card_refusal(args.ask, kept[-1], Path(kept[-1]["file"])))
            or f"{args.ask} keeps no ASK block{f' in {args.file}' if args.file else ''}: keep one with drive job ask {args.ask} <file>"
        )
    question = drawable[-1]
    if not (waits := args.waits or question["waits"]):
        raise DriveError(f"{args.ask}'s ASK in {question['file']} has no waits id yet: drive job ask {args.ask} {question['file']} gives it one and draws its card")
    return Path(question["file"]), waits


def question_card(name: str, path: Path, waits: str) -> dict:
    """The card a job's question draws: its newest kept ASK block, under the waits id the question already holds, so every ask of one question redraws its one card; the waits id given names the card of a question holding none yet, or one of the job's cards drawn again on purpose. A question that ended, that `drive job ask` refused, or that a `Replaces:` line folded into another job's or its own later question, draws none under any waits id, by `settled.card_refusal`. The card is the job's whose question holds the waits id, by `state.waits_job`, so a card another job took over with --redraws draws for that job; a waits id naming no job of the drive, a card asked outside any job, only draws the block. Each reference in its summary, Root cause and Interim fix lines, Show me and option descriptions, such as {at:<seq>} in a Show me table, is filled as say fills a post's, through `refs.resolved` and `refs.slack`, before its file names are drawn as code. A job's card says above its buttons what is built while he decides, as `meanwhile` reads it from the drive's jobs at each draw."""
    from drive import superseded, view

    data = state.load(name)
    if folded_in := next((settled.card_refusal(asker, kept, path) for asker, each in data["jobs"].items() if (kept := state.kept_in(each, path)) and kept["replaced"]), None):
        raise DriveError(folded_in)
    cards = view.live_cards(name) or view.NO_CARDS
    job_id = state.waits_job(data, waits)
    question = None
    if job := data["jobs"].get(job_id):
        question = state.kept_in(job, path) or next(
            (question for other, question in reversed(asking(data, kept_ask(path, None, cards, data["jobs"]).title)) if other == job_id and question["file"]), None
        )
        if question and (refused := settled.card_refusal(job_id, question, Path(question["file"]))):
            raise DriveError(refused)
        if question:
            path = Path(question["file"])
        if question and question["waits"] and not any(other["waits"] == waits for other in job["questions"]):
            waits = question["waits"]
        if refused := superseded.redraw_refusal(data, job, waits, str(path)):
            raise DriveError(refused)
    asked = kept_ask(path, question, cards, data["jobs"])
    if refused := view.tap_claim_refusal(data, job["order"] if job else "", "\n".join([asked.title, *asked.summary, *asked.options]), cards["pr_cards"]):
        raise DriveError(f"{path}: {refused}.{limits.FROM_ASK.format(waits=waits)}")
    recorded = taps(question["ordered"] if question else [], settled.deciding(asked, cited_answers("\n".join(asked.summary), recorded_answers(data, job, path.read_text())))) if job else []
    asked = already_answered(asked, recorded)
    show_me = picture.drawn(state.drive_dir(name) / "pictures", asked.show_me)
    found = refs.resolved(name, "\n".join([*asked.summary, asked.cause, asked.interim, *show_me, *asked.hints.values()]))
    holds = question["holds"] if question else job and asked.holds and state.held_words(data, asked.holds, job_id)
    card = replace(
        asked,
        summary=[refs.slack(line, found, None) for line in asked.summary],
        cause=refs.slack(asked.cause, found, None),
        interim=refs.slack(asked.interim, found, None),
        show_me=[refs.slack(line, found, None) for line in show_me],
        hints={option: refs.slack(hint, found, None) for option, hint in asked.hints.items()},
    ).card(name, waits, meanwhile(data, job_id, holds or "", question and question["unsettled"]) if job else "")
    if job_id in data["jobs"]:
        def question_card_write(data: dict):
            keep_waits(data, job_id, path, waits)
            if question := state.kept_in(data["jobs"][job_id], path):
                question["recorded"] = recorded

        state.update(name, None, question_card_write)
    return card


def meanwhile(data: dict, job_id: str, holds: str, unsettled: str | None) -> str:
    """What the drive builds while he decides, read from its jobs for his card's line above its buttons, so no agent writes it: being built when the card holds a part `state.work` says only a builder carries, every part of a build order or an incident among them, that a builder or codex job neither failed nor stopped holds, as `building` reads the jobs building a card's recommended option, the builder a build order's reader is added with included, or that a design asks on, whose return adds a builder for each change it lists; that build lands on the recommendation on an open incident, unless the card asks him to lift a ruling of his, and waits on his pick otherwise; nothing is built until he picks on an order he limited to options, an answer order, a part only research held, or a part no such job holds, such as a finished reader's with no builder beside it. `Holds:` words naming no part of its orders, `none` and a design's changes not yet parts among them, leave the asking job's own parts to count, which a builder's always do."""
    job = data["jobs"][job_id]
    orders = [job["order"], *job["carries"]]
    own = job["kind"] == "builder" or not state.names_part(holds, (part for order_id in orders for part in data["orders"][order_id]["parts"]))
    if not any(
        state.work(data, order_id, part)
        and (job["kind"] == "design" or any(other["kind"] in state.CHANGE_KINDS and other["state"] not in state.ENDED_JOB_STATES for _, other in state.part_jobs(data, order_id, part)))
        for order_id in orders
        for part in data["orders"][order_id]["parts"]
        if state.names_part(holds, [part]) or own and part in state.worked(data, job_id, order_id)
    ):
        return limits.NOTHING_BUILT
    return limits.BUILT_LANDING if state.open_incidents(data, orders) and not unsettled else limits.BUILT_PAUSED


def kept_ask(path: Path, question: dict | None, cards: dict, jobs: dict) -> limits.Question:
    """The question an ASK file draws. One the drive already asked, its question holding a waits id, draws again under the rules it was asked under, a file rebuilt from his card included, never the shape a new block meets; once the words `drive job ask` kept in its file changed since, it draws from his card as the drive stored it under that waits id, the words he read, and once cc-slack's store forgot that card, from the file while its question, summary, recommended option and options are the ones his card's message in Slack shows. A file rebuilt from his card, named for its waits id, draws only while those four are the ones his card shows, as the drive stored it, else as its message in Slack shows it once cc-slack's store forgot it. Any other file `drive job ask` kept, named `<job>-<hash>` for the words it first kept there, draws under the rules it was kept under, and one whose words no longer hash to the hash `state.kept_hash` reads for it draws nothing, since only `drive job ask` changes a kept question; a file never kept is held to every rule a new block meets."""
    text = path.read_text()
    hashed = state.kept_hash(path, jobs)
    asked = question and question["waits"]
    stored = asked and next((shown["card"] for shown in cards["questions"] if shown["card"].get("waits") == asked), None)
    if hashed and hashed != state.ask_hash(text):
        if stored:
            return limits.redrawn(limits.rebuilt(stored, path.parent, jobs))
        if asked and question["card"] and slack.posted_question(question["card"]) == shown_words(edited := limits.redrawn(text)):
            return edited
        raise DriveError(
            f"{path} no longer holds the words drive job ask kept there, so it draws no card: a kept question changes only through"
            f" drive job ask, which checks the new words against every rule, so keep them with drive job ask {path.stem.rpartition('-')[0]} <file> and draw the file it prints"
        )
    if asked and path.stem == asked.partition(":")[2]:
        rebuilt = limits.redrawn(text)
        if shown_words(rebuilt) == (shown_words(limits.redrawn(limits.rebuilt(stored, path.parent, jobs))) if stored else slack.posted_question(question["card"])):
            return rebuilt
        raise DriveError(
            f"{path}, rebuilt from his card, no longer holds the question, summary, recommended option and options his card shows, so it draws no card:"
            f" a question he was shown changes only through drive job ask, which checks the new words against every rule, so keep them with"
            f" drive job ask {asked.partition(':')[2].partition('-')[0]} <file> --redraws {asked} and draw the file it prints"
        )
    return limits.redrawn(text) if asked or state.KEPT_ASK.fullmatch(path.stem) else limits.question(text)


def shown_words(asked: limits.Question) -> tuple[str, str, str, list[str]]:
    """The question, summary, recommended option and options a question's card draws, as `slack.posted_question` reads them off his card's message."""
    return asked.title, limits.coded("\n".join(asked.summary)), asked.recommended, asked.options


def recorded_answers(data: dict, job: dict, text: str) -> list[dict]:
    """Each recorded answer of his that the job's brief binds by --ruling or its ASK block cites by its id outside a link, with the question it answers, his words, when it was recorded and the link where he gave them: the one `drive answer` kept, else the card of the question asked in its words; an id ccn keeps no answer of his under, such as a commit's, is none."""
    cwd = data["roles"]["coordinator"]["cwd"]
    found = {}
    for answer_id in dict.fromkeys([*((job.get("brief_flags") or {}).get("ruling") or []), *limits.CITED_ANSWER.findall(limits.URL.sub(" ", text))]):
        try:
            answer = settled.shown(answer_id, cwd)
        except DriveError:
            continue
        if words := settled.his(answer["body"]):
            asked = settled.answered(data, answer["title"])
            kept = data["answers"][answer["id"]]["link"] if answer["id"] in data["answers"] else None
            found[answer["id"]] = {"answer": answer["id"][:7], "asked": answer["title"], "words": words, "link": kept or (asked and (asked["card"] or asked["closed"]["link"])), "at": answer["created_at"]}
    return list(found.values())


def cited_answers(text: str, recorded: list[dict]) -> list[dict]:
    """Each of the recorded answers the text cites, read by `served.named` as drive pr set --design reads a citation, his words cut above the Options: line its recorder lists the choices under."""
    named = {answer_id[:7] for kind, answer_id in served.named(text) if kind == "answer"}
    return [tap(answer) for answer in recorded if answer["answer"] in named]


def tap(answer: dict) -> dict:
    return answer | {"words": answer["words"].partition("\nOptions:")[0].strip()}


def taps(*answers: list[dict]) -> list[dict]:
    kept = {}
    for each in answers:
        for answer in each:
            kept.setdefault(answer["answer"], answer)
    return list(kept.values())


def already_answered(asked: limits.Question, recorded: list[dict]) -> limits.Question:
    """The question with the Already answered option when its summary cites a recorded answer of his that Opus reads as deciding it, by `cited_answers` and `settled.deciding`, or its question keeps one the release gate reads as ordering a release its recommended option names, described by his words, cut as a settled ask quotes them, each linked where he gave them, in place of the option a card rebuilt from his stored card carries; a block already holding every option a card draws gets none, since `keep_ask` refuses one and a block kept before that redraws as he read it."""
    options = [option for option in asked.options if option != limits.ALREADY_ANSWERED]
    hints = {option: hint for option, hint in asked.hints.items() if option != limits.ALREADY_ANSWERED}
    if not recorded or len(options) == limits.OPTION_COUNT.stop - 1:
        return replace(asked, options=options, hints=hints)
    hint = "\n".join(f'"{render.cut(answer["words"], settled.QUOTED_CHARS)}"' + (f" (<{answer['link']}|your answer>)" if answer["link"] else "") for answer in recorded)
    return replace(asked, options=[*options, limits.ALREADY_ANSWERED], hints=hints | {limits.ALREADY_ANSWERED: hint})


def cards_redraw(args: argparse.Namespace) -> int:
    """Print, one JSON line each in one pass, the card `drive job card` prints for each of his open question cards, its `replaces` the card's permalink, which cc-slack draws over that card where it sits, posting no new version, whether or not its store still keeps the card: each open question of the drive drawn on a card, from the ASK file it keeps, as drive status reads it open, and each card cc-slack lists open, from the ASK file `ask_source` finds for it; a `--waits` naming a PR card's `merge:` waits id, open or answered and neither replaced nor withdrawn, has cc-slack redraw that card in place as drive card draws it now, said on stderr; with --post, draw each over his card through `talk.posted`, as `post_ask` posts one, and print its waits id and permalink instead; each card that does not redraw is named once the rest are printed."""
    from drive import talk, view

    name = state.resolve(args.drive)
    view.refresh_cards(name)
    if (found := view.live_cards(name)) is None:
        raise DriveError(f"his open cards could not be read: {view.stored_cards(name)[1]}")
    shown = {waits: card for card in found["questions"] if (waits := card["card"].get("waits"))}
    prs = {waits: card for card in [*found["pr_cards"], *(closed for closed in found["closed"] if "card" in closed)] if (waits := card["card"].get("waits"))}
    asked = {
        question["waits"]: question
        for job in state.load(name)["jobs"].values()
        for question in view.open_questions(job)
        if question["waits"] and question["card"] and question["file"] and not question["replaced"]
    }
    drawn, missed = [], []
    for waits in args.waits or dict.fromkeys([*shown, *asked]):
        if waits in prs:
            try:
                print(f"{waits}: redrawing in place the {slack.redraw_pr_cards(prs[waits]['prs'][0])} card(s) of his carrying {prs[waits]['prs'][0]}", file=sys.stderr)
            except slack.SlackError as error:
                missed.append(f"{waits}: {error}")
            continue
        if waits not in shown and waits not in asked:
            missed.append(f"{waits}: not one of his open cards, nor a PR card of his still drawn")
            continue
        try:
            source, url = (ask_source(name, shown[waits]), shown[waits]["url"]) if waits in shown else (Path(asked[waits]["file"]), asked[waits]["card"])
            drawn.append((waits, question_card(name, source, waits) | {"replaces": url}))
        except DriveError as error:
            missed.append(f"{waits}: {error}")
    if drawn and args.post:
        posted = [{"waits": waits, "permalink": talk.posted(name, {"card": card})["permalink"]} for waits, card in drawn]
        emit(args, "\n".join(f"{each['waits']} redrawn at {each['permalink']}" for each in posted), posted)
    elif drawn:
        cards = [card for _, card in drawn]
        emit(args, "\n".join(map(json.dumps, cards)), cards)
    if missed:
        raise DriveError("these cards did not redraw:\n- " + "\n- ".join(missed))
    return 0


def cards_list(args: argparse.Namespace) -> int:
    """Print each open question of the drive drawn on his card, newest first, on the feature of the order --order names, on orders filed under the project --project names, or all: its waits id, order, job, when it was asked, its title and its card's permalink, so each card of a class his ruling closes can be withdrawn by name."""
    from drive import view

    data = state.load(state.resolve(args.drive))
    if args.order:
        find(data["orders"], args.order, "order")
    orders = set(state.feature_orders(data, args.order)) if args.order else None
    cards = sorted(
        (
            {"waits": question["waits"], "order": job["order"], "job": job_id, "asked": question["asked"], "title": question["title"], "card": question["card"]}
            for job_id, job in data["jobs"].items()
            for question in view.open_questions(job)
            if question["card"] and not question["replaced"] and (orders is None or job["order"] in orders) and args.project in (None, data["orders"][job["order"]]["project"])
        ),
        key=lambda card: state.parse_time(card["asked"]),
        reverse=True,
    )
    return emit(args, "\n".join(f"{card['waits']} {card['order']} {card['job']} asked {card['asked']}: {card['title']} {card['card']}" for card in cards) or "no open question card of his", cards)


def cards_withdraw(args: argparse.Namespace) -> int:
    """End as withdrawn each open question drawn on his card that his words, wherever he wrote them after it was asked, withdraw, or whose question or options they change, or that an event at --via overtook, as settled.unhappened reads it, refusing a --via Slack message older than any of those questions, since no answer he gave another question settles one, so a tap has nothing left to pick, and its card follows: it shows his words, or what happened, and a link to them, with no buttons, so a late tap records nothing and no question asks him on a card while its work is being redone; its job, or any job of its order carrying on that work, asks again with `drive job ask --redraws` and the card's waits id once the work is back, which redraws it. The event's words are never recorded as his. With --via another open question card of the drive, by its waits id or permalink, each folds into the question that card asks, as `drive job ask` folds a question it reads as asked again into a newer card, by `fold_onto`: its card shows ASKED_AGAIN and a link to that card with no buttons, each wait on it moves to that card's waits id, and it stays open on that card, so his one answer there ends it too."""
    from drive import superseded, view

    name = state.resolve(args.drive)
    onto = next(
        (
            (job_id, question)
            for job_id, question in view.drawn_on(state.load(name), args.via)
            if question["closed"] is None
            and question["card"]
            and question["file"]
            and not question["replaced"]
            and question["waits"] != args.card
            and view.message_key(question["card"]) != view.message_key(args.card)
        ),
        None,
    )
    if onto and args.words:
        raise DriveError(f'{args.card}: refused, not withdrawn: --via {args.via} is the drive\'s open card {onto[1]["card"]}, so the card shows "{ASKED_AGAIN}" linking it, never --words: drop --words')
    if not onto and not args.words:
        raise DriveError(f"{args.card}: refused, not withdrawn: --words is needed unless --via is another open question card of this drive, by its waits id or permalink")
    if not onto and (posted := slack.MESSAGE_LINK.search(args.via)):
        sent = datetime.fromtimestamp(float(f"{posted[2]}.{posted[3]}")).astimezone()
        data = state.load(name)
        if later := [(job_id, question) for job_id, question in view.drawn_on(data, args.card) if question["closed"] is None and state.parse_time(question["asked"]) > sent]:
            asker, newest = max(later, key=lambda pair: state.parse_time(pair[1]["asked"]))
            file = newest["file"] or "<the file its question is kept in>"
            detail = (
                f"; one of a Drive tooling order asking him only a detail of how the tooling works ends decided on its recommended option under his ruling {state.TOOLING_DETAILS_RULING}:"
                f" drive job ask {asker} --drop {file} --answer {state.TOOLING_DETAILS_RULING}"
                if state.tooling(data["orders"][data["jobs"][asker]["order"]])
                else ""
            )
            raise DriveError(
                f"{args.card}: refused, not withdrawn: --via {args.via} was written at {state.stamp(sent)}, before its question was asked at {newest['asked']}, and no answer he gave another question settles it;"
                f" an answer of his that reads as settling it goes on the card as its {limits.ALREADY_ANSWERED} tap: drive job ask <job> <file> --redraws {newest['waits'] or args.card}, its summary citing that answer,"
                f" and one that chose its recommended option ends it on his words: drive job ask <job> --drop {file} --answer <that answer's id>{detail}"
            )
    unsaid = None if onto else settled.unsaid([args.words], [args.via], view.live_cards(name), state.load(name))
    if unsaid and (unhappened := settled.unhappened(args.via, args.card, name)):
        raise DriveError(f"{args.card}: refused, not withdrawn: {unsaid}; nor did an event at {args.via} overtake it: {unhappened}")
    def cards_withdrawn(data: dict):
        drawn = [(job_id, question) for job_id, question in view.drawn_on(data, args.card) if question["closed"] is None]
        live = None
        if onto and drawn:
            live = state.question_in(data["jobs"][onto[0]], onto[1])
            for _, question in drawn:
                question["replaced"] = live["file"]
            ended = fold_onto(data, live)
        else:
            closed = state.new_close("withdrawn", args.words, args.via)
            ended = [each for job_id, question in drawn for each in superseded.end(data, job_id, question, closed)]
        return data, drawn, ended, live

    data, drawn, ended, live = state.update(name, None, cards_withdrawn)
    if not drawn:
        raise DriveError(f"{args.card}: no open question of this drive is drawn on that card, by its waits id or permalink; drive status <job> names the card each question is drawn on")
    if onto:
        done = superseded.follow(name, ended, f'"{ASKED_AGAIN}" and a link to {live["card"]}')
        lines = [
            f"folded {len(drawn)} question(s) drawn on his card {args.card} into the question his card {live['card']} asks, which his answer there ends too",
            *done,
            f"each wait on {args.card} now waits on {live['waits']}",
        ]
    else:
        done = superseded.follow(name, ended, "what happened" if unsaid else "his words")
        lines = [f"withdrew {len(ended)} question(s) drawn on his card {args.card}", *done, *redraws(data, ended)]
    return emit(args, "\n".join(lines), {"card": args.card, "ended": [{"job": job_id, "waits": question["waits"], "card": question["card"]} for job_id, question in ended]})


def redraws(data: dict, ended: list[tuple[str, dict]]) -> list[str]:
    """How each withdrawn question is asked again once its work is back, a line per waits id."""
    return sorted(
        {
            f"drive job ask <job> <file> --redraws {question['waits']} asks it again once its work is back, from {job_id} or any job of {data['jobs'][job_id]['order']}, which redraws its card"
            for job_id, question in ended
            if question["waits"]
        }
    )


def ask_source(name: str, shown: dict) -> Path:
    """The ASK file his open card draws again: the one a job's question keeps under the card's waits id; else the one kept by a question linked to the card whose ASK asks what the card asks; else one rebuilt from the card as the drive stored it, kept on a question of its own in the job whose question holds its waits id, else the job that minted it, by `state.waits_job`, which takes the place of every question of that job holding the waits id or linked to the card."""
    from drive import view

    waits, url, title = shown["card"]["waits"], shown["url"], shown["card"]["title"]
    jobs = state.load(name)["jobs"]
    kept = [question for job in jobs.values() for question in job["questions"] if question["file"]]
    if held := next((question["file"] for question in kept if question["waits"] == waits and not question["replaced"]), None):
        return Path(held)
    linked = [question["file"] for question in kept if question["card"] and view.message_key(question["card"]) == view.message_key(url) and not question["replaced"]]
    if asked := next((file for file in linked if asks(Path(file), title)), None):
        return Path(asked)
    rebuilt = state.rebuilt_ask(name, waits)
    if not rebuilt.exists():
        rebuilt.write_text(limits.rebuilt(shown["card"], rebuilt.parent, jobs))
    def source_kept(data: dict) -> None:
        if job := data["jobs"].get(job_id := state.waits_job(data, waits)):
            job["questions"] = [
                question
                for question in job["questions"]
                if question["waits"] != waits and not (question["card"] and view.message_key(question["card"]) == view.message_key(url))
            ]
            job["questions"].append(state.new_question(str(rebuilt), title, shown["asked"], card=url, holds=limits.redrawn(rebuilt.read_text()).holds) | {"waits": waits})
            state.join_card(data, job_id, url)

    state.update(name, None, source_kept)
    return rebuilt


def asks(file: Path, title: str) -> bool:
    """Whether a kept ASK file redraws the question titled title; one kept under rules it no longer meets redraws none."""
    try:
        return limits.redrawn(file.read_text()).title == title
    except DriveError:
        return False


def keep_waits(data: dict, job_id: str, file: Path, waits: str, card: str | None = None) -> None:
    """Give the waits id to the newest question kept in the file, with the card drawn under it, read from the question of the job that held it before or given for one another job held, and the same to each question a `Replaces:` line folded into it, so his card wakes every job asking it, but one still on another card of his, which `post_ask` closes once this card is up; the question that held it before, which this card redraws, hands over its card and leaves the job, and what was folded into that one folds into this one."""
    job = data["jobs"][job_id]
    question = state.kept_in(job, file)
    if question is None:
        return
    redrawn = [other for other in job["questions"] if other is not question and other["waits"] == waits and not other["replaced"]]
    question.update(waits=waits, card=question["card"] or next((other["card"] for other in redrawn if other["card"]), card))
    job["questions"] = [other for other in job["questions"] if not any(other is gone for gone in redrawn)]
    gone = {other["file"] for other in redrawn if other["file"]}
    for other in (other for each in data["jobs"].values() for other in each["questions"] if other["replaced"] in gone):
        other["replaced"] = question["file"]
    for other in folded(data, question["file"]):
        if not other["card"] or other["card"] == question["card"]:
            other.update(waits=waits, card=question["card"])


def folded(data: dict, file: str) -> list[dict]:
    """Each open question a `Replaces:` line folded into the question kept in this file, and each folded into those, which his card for this one asks too; a question kept in no file, as `hold_card` keeps a card no ASK drew, has none folded into it."""
    from drive import view

    direct = [other for job in data["jobs"].values() for other in view.open_questions(job) if other["replaced"] == file]
    return [*direct, *(deeper for other in direct if other["file"] for deeper in folded(data, other["file"]))]


def job_report(args: argparse.Namespace) -> int:
    """Keep a job's whole return from stdin in its history, which is its report, and print where, or to a Sprite lane, which opens no path of this Mac's, that it was kept with its first line, that line setting the job's state, or refuse it with nothing kept, naming the fix, when that line would set none; the file its REPRO: line names is kept as the job's proof file once the refusals pass, by named_repro, so a return names its proof file and keeps it in one step, every refusal reading that file as kept; a stopped job's return is kept ahead of every refusal and sets nothing, its state, said line, waits, PRs, builders and briefs left as they were, the print saying it stays stopped, so the agent its stop left running ends on that line; a taken return writes its returned event, by state.returned, which the watch hearing the job says as its JOB line wherever the job runs, naming the texts it holds and when to start each builder it added, but a PUSHED, which pushed_lines settles, and the return of a job whose agent on this Mac answers the session that started it, by state.agent_lane, since its result already lands there, each write only the line naming when to start each builder it added, which no other line carries; a reader's DESIGN is kept as its ASK in the same call, which draws his card on it and prints the file and the card, and each GitHub PR the drive does not track that the FIX line of a report tracing a live bug's cause links is registered on the job before the take, which joins its order to the bug, or kept untracked with the reason drive pr add refused it; a reader's DONE or DESIGN writes again, as drive job brief does, the brief of each builder of a part it reads that no agent or lane started and that opened no PR, by brief.unstarted_builders, so its Researched: line names the newest research of its parts and its Goal: the change that research's build: line names, by brief.name_goals, serving drive tooling on a tooling order and his order on any other, and prints its `Read your brief first:` line; a design lane's DESIGN, and a builder's return for each piece of its plan its UNBUILT: lines name, adds a builder for each change it lists that no builder or codex job builds, by state.add_changes, and prints when to start each; each open card of the job the return no longer names it prints for its session to weigh withdrawing, by superseded.unnamed; its checks read only its shape, with no Jev or Opus read, by brief.report_refusal. With --dry-run, by rehearsed, a take keeps nothing and prints each drive row field it would change: the report it keeps, its returned event, the briefs it writes and each card it draws or withdraws run by state.after_save once a save lands, which a dry run never makes, and a reader's DESIGN is checked by ask_checked, drawing no card. With no stdin, print the newest return whole and each earlier one as its first line, newest first, and with --all every return whole."""
    name = state.resolve(args.drive)
    data = state.load(name)
    job = find(data["jobs"], args.id, "job")
    if args.stdin:
        whole = sys.stdin.read()
        if not whole.strip():
            raise DriveError(f"stdin held no report for {args.id}; pipe its whole return in: printf '%s\\n' '<line>' ... | drive job report {args.id} -")
        return emit(args, *take_report(name, args.id, whole, read_prs=not args.sprite_lane))
    text = state.report_text(name, args.id, args.all)
    if not text and job["said"] in (None, state.NOT_STARTED):
        raise DriveError(f"{args.id} kept no report and has returned nothing yet")
    if not text:
        return emit(args, f"{job['said']}\n({args.id} kept no report; this one-line result is all it returned)", {"id": args.id, "report": None, "said": job["said"]})
    return emit(args, text.rstrip("\n"), {"id": args.id, "report": text})


def job_check(args: argparse.Namespace) -> int:
    """Read on GitHub the PRs a Sprite lane's newest kept READY names, by unready, which drive job report left out to answer the lane at once; a refusal wakes the lane by drive job resume with it, to fix and report again."""
    name = state.resolve(args.drive)
    data = state.load(name)
    whole = state.returns(name, args.id)[0]["body"]
    job = data["jobs"][args.id]
    line = whole.strip().splitlines()[0].strip()
    ready = job["kind"] == "builder" and re.match(r"READY\b", line)
    if not (refused := ready and unready(data, job, line)):
        return emit(args, f"{args.id}: its report stands", {"id": args.id, "refused": None})
    told = JUDGED_LATER.format(job=args.id, refused=refused)
    state.detach(name, ["job", "resume", args.id, told], watch.READS_LOG)
    return emit(args, f"{args.id}: {refused}", {"id": args.id, "refused": refused})


def take_report(name: str, job_id: str, whole: str, read_prs: bool = True, tell: bool = True) -> tuple[str, dict]:
    """Take a job's whole return as drive job report <j> - takes it, the one place a return is kept or refused, for that verb and for the watch's take of a local agent's last message; what it prints and its JSON. With read_prs False, as a Sprite lane's report is taken, the GitHub read of a READY's PRs runs after it is kept, in a `drive job check` of its own, so the lane's answer waits on none of it. With tell False, as a work queue item's return no one must act on is taken, its line stays on the item and no JOB line goes to the session holding its order."""
    data = state.load(name)
    job = data["jobs"][job_id]
    line = whole.strip().splitlines()[0].strip()
    kept = str(state.replies_path(name, job_id))
    where = f"kept {job_id}'s report: {line}" if os.environ.get(state.OWN_JOB_ENV) else kept
    if job["state"] == "stopped":
        state.after_save(name, lambda: state.keep_report(name, job_id, line, whole) or [])
        return f"{where}\n{job_id} stays stopped: this return sets nothing on it", {"id": job_id, "report": kept, "ask": None, "fixes": [], "builders": []}
    if repro := named_repro(job_id, job, whole):
        job["repro"] = str(repro)
    built = state.BUILT_LINE.match(line) if job["kind"] in state.CHANGE_KINDS else None
    if refused := (
        brief.report_refusal(name, data, job_id, whole)
        or state.result_refusal(data, job_id, line, whole)
        or (built and (held := state.change_job(data, job["order"], [], job["waits"], job_id, built["branch"], job["repo"], proving=state.builds_nothing(job))) and one_change_job(data, job["order"], job["kind"], [], held, job["waits"], branch=built["branch"], repo=job["repo"], job_id=job_id))
    ):
        raise DriveError(f"{job_id}: refused, nothing kept: {refused}")
    unreached(name, data, state.result_waits(data, job_id, line))
    if job["kind"] == "builder" and state.REPORTED_LINE.match(line) and any(not served.named(cited) for cited in served.deciding(whole)):
        print(f"drive job report: {served.NAMELESS}", file=sys.stderr)
    if repro:
        def repro_kept(data: dict):
            data["jobs"][job_id]["repro"] = keep_repro(name, job_id, job["kind"], str(repro))
        state.update(name, None, repro_kept)
    try:
        registered = register_named(name, job_id, line)
    except DriveError as error:
        raise DriveError(f"{job_id}: refused, nothing kept: drive pr add refuses a PR its first line names: {error}") from None
    data = state.load(name)
    if read_prs and job["kind"] == "builder" and re.match(r"READY\b", line) and (held := unready(data, job, line)):
        raise DriveError(f"{job_id}: refused, nothing kept: {held}")
    asked = said = None
    if job["kind"] == "reader" and line.startswith("DESIGN"):
        try:
            if name in state.REHEARSING:
                said = ask_checked(name, job_id, whole.strip(), None, None)
            else:
                asked, _, _, said = keep_ask(name, job_id, whole.strip(), None)
        except DriveError as error:
            raise DriveError(f"{job_id}: refused, its report not kept: a reader's DESIGN is his card as it stands, kept as its ASK, and drive job ask refused it: {error}") from None
    state.after_save(name, lambda: state.keep_report(name, job_id, line, whole) or [])
    fixes = []
    for key in state.untracked_fixes(data, job_id, line, whole):
        try:
            pr, testers = add_pr(name, key, job_id, github_pr(key))
            fixes += [f"FIX kept, {key}: tracked under {pr['order']}", *render.builder_lines(name, state.load(name), testers)]
        except DriveError as error:
            fixes.append(f"FIX kept, {error}")
    changed = [state.whole_order(found["change"]) for found in state.change_lines(job["kind"], whole)] if job["kind"] in ("design", "builder") else []
    removing = brief.removed_parts(state.load(name), job["order"], changed)
    def reported(data: dict):
        held = built and state.change_job(data, job["order"], [], data["jobs"][job_id]["waits"], job_id, built["branch"], job["repo"], proving=state.builds_nothing(job))
        replaced = [state.hand_over(data, held, job_id, job["order"], [], state.stamp(), built["branch"], job["repo"])] if held else []
        state.take_result(data, job_id, line, whole)
        state.cite_designs(data, job_id, line, whole)
        added = state.add_changes(name, data, job_id, whole, removing)
        rebriefed = brief.unstarted_builders(data, job_id) if job["kind"] == "reader" and data["jobs"][job_id]["state"] == "done" else []
        brief.name_goals(name, data, rebriefed, whole)
        return data, replaced, added, rebriefed, superseded.unnamed(data, job_id, whole, asked and str(asked))
    data, replaced, added, rebriefed, unnamed = state.update(name, None, reported)
    withdrawn = [
        f"weigh: his card {question['card'] or question['waits']} still asks him and this return names it nowhere; if the work went on without it,"
        f' withdraw it now with drive cards withdraw {question["waits"] or question["card"]} --words "<what happened>"'
        for _, question in unnamed
    ]
    briefed = state.after_save(name, lambda: [f"{builder_id}: {write_brief(name, builder_id, {})[0]}" for builder_id in rebriefed])
    told = state.load(name)
    started = render.builder_lines(name, data, added)
    if tell and (taken := told["jobs"][job_id])["said"] and not state.PUSHED_LINE.match(taken["said"]) and not state.agent_lane(told, taken):
        state.returned(name, told, job_id, "; ".join([taken["said"], *replaced, *([render.unread([text["words"] for text in taken["undelivered"]])] if taken["undelivered"] else []), *started]))
    elif started:
        state.returned(name, told, job_id, "; ".join(started))
    shown = f"{where}\n{said}" if said else where
    unstarted = [f"no builder added: {why}" for why in removing.values()]
    shown = "\n".join([shown, *replaced, *registered, *fixes, *withdrawn, *briefed, *started, *unstarted])
    if not read_prs:
        state.detach(name, ["job", "check", job_id], watch.READS_LOG)
    return shown, {"id": job_id, "report": kept, "ask": asked and str(asked), "fixes": fixes, "builders": added, "removed": removing}


def register_named(name: str, job_id: str, line: str) -> list[str]:
    """Register on a builder each PR its READY or PUSHED names by link that the drive does not track yet, as drive pr add does, drawing his card on each that waits on his tap, so every PR a result names is staged, carded and landed like one its builder added; what came of each. A PR drive pr add refuses raises its refusal."""
    data = state.load(name)
    if data["jobs"][job_id]["kind"] != "builder" or not state.REPORTED_LINE.match(line):
        return []
    tracked = {key.casefold() for key in data["prs"]}
    added = []
    for key in dict.fromkeys(state.pr_key(found[0]) for found in state.PR_URL.finditer(line)):
        if key.casefold() in tracked:
            continue
        _, testers = add_pr(name, key, job_id, github_pr(key), lead={})
        added += [f"{key}: registered on {job_id}{''.join(state.after_save(name, lambda key=key: [drawn_card(name, key)]))}", *render.builder_lines(name, state.load(name), testers)]
    return added


def unready(data: dict, job: dict, line: str) -> str | None:
    """Why a builder's READY is not ready yet, from a GitHub read made now, since READY counts only on a green required check: a required check that failed on a PR it names, one still running, or a read that failed, each sending the builder to return PUSHED, which the watch takes as this READY once every required check passes."""
    reads, refused = github.fetch(state.named_prs(data, job["order"], line))
    verdict, held = ("pending", [("GitHub", f"GitHub's read failed: {refused}")]) if refused else state.required_verdict({key: github.live(read, data["prs"][key]) for key, read in reads.items()})
    if verdict == "pass":
        return None
    fix = "fix it and push, then return" if verdict == "fail" else "return"
    pushed = re.sub(r"^READY\b", "PUSHED", line)
    holding = "; ".join(why if refused else f"{key}'s {why}" for key, why in held)
    return (
        f"READY counts only on a green required check, and {holding}: {fix} this report with"
        f" `{pushed}` as its first line, which the watch takes as this READY once every required check passes, waking no one meanwhile"
    )


def named_repro(job_id: str, job: dict, report: str) -> Path | None:
    """The proof file a return's REPRO: line names, which the take of that return keeps on the job by keep_repro, so naming it keeps it; None when the report names none, or names a file not here while the job keeps a copy already, such as one in a scratch folder since cleared; refused, nothing kept, when it names no file here and the job keeps none, and as repro_file refuses the file."""
    if not (named := state.REPRO.search(report)):
        return None
    if (path := Path(named["path"]).expanduser()).is_file():
        return repro_file(job_id, job["kind"], str(path))
    if job.get("repro"):
        return None
    raise DriveError(f"{job_id}: refused, nothing kept: its REPRO: line names {path.absolute()}, no file here, and {job_id} keeps no proof file: name the file its proof runs")


def repro_file(job_id: str, kind: str, source: str) -> Path:
    """The reproduction file at source, refused, nothing kept, when no file is there, and for a builder's file with its exec bit set that the kernel cannot exec, as state.unrunnable reads it, since drive job prove runs that file as the job's proof once its PRs merge, while a builder's file with no exec bit, such as the acceptance criteria of a change merging outside yasyf/*, which no drive job prove runs, is kept unchecked."""
    path = named_file(source, "reproduction file")
    if kind == "builder" and os.access(path, os.X_OK) and (why := state.unrunnable(str(path))):
        raise DriveError(
            f"{job_id}: refused, nothing kept: drive job prove runs a builder's executable file as its proof once its PRs merge, and {why}:"
            f" make its first line its #! line, naming a program on this Mac, such as #!/bin/sh, then keep it again with drive job set {job_id} --repro <file>"
        )
    return path


def keep_repro(name: str, job_id: str, kind: str, source: str) -> str:
    """Where the drive keeps a job's reproduction file, the file repro_file reads at source: where it lies when the drive's own folder holds it, such as the job's scratch folder, which the drive keeps, so a proof reading files beside it by a path from its own, as a pass's proof reads the evidence it captured, reads them wherever it runs, else in the drive's repro folder, copied there once the write naming it is kept; either way with the drive's own interpreter pinned in it as state.pin_interpreter pins it, so its bare python3 runs where the drive runs wherever it is run."""
    path = repro_file(job_id, kind, source)
    kept = path.absolute() if path.resolve().is_relative_to(state.drive_dir(name).resolve()) else state.repro_path(name, job_id, path.suffix)

    def copied() -> list[str]:
        kept.parent.mkdir(exist_ok=True)
        if not kept.exists() or not kept.samefile(path):
            shutil.copy(path, kept)
        state.pin_interpreter(kept)
        return []

    state.after_save(name, copied)
    return str(kept)


def place_job(args: argparse.Namespace) -> int:
    builders = place.local_builders(state.load(state.resolve(args.drive)), state.now())
    share, ceiling = place.busy(), place.limit()
    where, why = place.verdict(share, ceiling, builders)
    payload = {"where": where, "why": why, "busy": round(share, 3), "limit": ceiling, "local_builders": len(builders)}
    return emit(args, f"{where}: {why}", payload)


def remote_job(data: dict, job_id: str) -> dict:
    job = find(data["jobs"], job_id, "job")
    if job.get("where") == "remote":
        return job
    if job.get("agent"):
        restore = f", or put it back on its lane with drive job set {job_id} --agent {job['dispatch']}, then resume it" if job.get("dispatch") else ""
        raise DriveError(f"{job_id} runs on this Mac as agent {job['agent']}; resume it with SendMessage{restore}")
    if job["kind"] == "codex":
        raise DriveError(f"{job_id} runs as codex-ask runs on this Mac, which no agent or lane relays: drive job resume {job_id} \"<text>\" gives its run a text")
    raise DriveError(
        f"{job_id} has no agent and no lane yet: start it on a Sprite with drive job launch {job_id} <brief>, as an Orca"
        f" worker on this Mac with drive job launch {job_id} <brief> --local, or start an Agent here whose brief ends Drive: <drive>, order <order>, job {job_id}"
    )


def other_sessions_agent(data: dict, job: dict) -> str | None:
    """This session, when the job ran as an Agent another session started that ended with that session, which no role holds any more, so this one hands the job to a fresh agent or lane of its own."""
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    return session if session and job["where"] == "local" and job["agent"] and job["session"] != session and state.session_role(data, job["session"]) is None else None


def starters_lane(data: dict, job_id: str, job: dict, resume: bool) -> str | None:
    """The cci lane of the session of the drive that started the Agent running the job, when its agent still takes messages, since only that session's SendMessage reaches it, by his pick "Keep with the starter: the agent finishes its job, your answers reach it at once": another session's, and this session's own for a resume, with a text or with none, while place.woken leaves the agent a turn; None for any other job."""
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not (session and job["where"] == "local" and job["agent"]):
        return None
    if job["session"] == session and (not resume or place.woken(data, job_id, state.now())):
        return None
    lane = state.session_lane(data, job["session"])
    return lane if lane and not watch.agent_death(job, data) else None


def answer_first(name: str, order: dict, order_id: str, job_id: str) -> str:
    """Where an answer order's reader sends his answer the moment it is proven, by his pick "answer-only jobs also post their answer once found": a progress report the communicator's handler posts in the order's thread, ahead of the full report the coordinator closes the order with."""
    attrs = state.thread_attr(state.load(name), order) | {"order": order_id, "job": job_id, "progress": "true"}
    return ANSWER_FIRST.format(drive=name, attrs=json.dumps(attrs))


def job_brief(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    held_job(state.load(name), args.id)
    line, payload = write_brief(name, args.id, {flag: getattr(args, flag, None) for flag in state.BRIEF_FLAGS})
    return emit(args, line, payload)


def write_brief(name: str, job_id: str, named: dict) -> tuple[str, dict]:
    """Write a job's brief from what the drive holds, by rewrite_brief, with the lines named, each flag left out kept from the job's earlier brief, each given as an empty value dropped from it, and each ruling named added to every ruling a brief of any job on its feature binds, every order joined with its order and each it carries, its own earlier ones included, by his approval "carried whole into every brief and handoff", its Serves: line, when neither names one, each `port of #N` part the job takes, else drive tooling on a tooling order and his order on any other, and its mac flag, when neither names one, the step the newest return of the builder of a PR its staging test parts test names on its Mac: line, by brief.returned_mac, which mac_lines then gives before STAGING_MAC; its --done-when check run only on a call naming --done-when and its user-story check only on one naming --native or --interactions, so a write naming neither, such as a reader's report rebriefing a builder, a launch or an install's prover, never refuses on flags another write kept; written again when another brief write saved the job's flags as this one ran, so a launch keeps every flag a drive job brief typed meanwhile."""
    if named.get("after") == [""]:
        named = named | {"after": ""}
    data = state.load(name)
    job = find(data["jobs"], job_id, "job")
    order = data["orders"][job["order"]]
    earlier_flags = job.get("brief_flags") or {}
    kept = [flag for flag in state.BRIEF_FLAGS if earlier_flags.get(flag) and (flag == "ruling" or named.get(flag) is None)]
    dropped = [flag for flag in state.BRIEF_FLAGS if earlier_flags.get(flag) and named.get(flag) == ""]
    bound = [answer_id for other in state.orders_jobs(data, state.job_feature(data, job)).values() for answer_id in (other.get("brief_flags") or {}).get("ruling") or []]
    ruled = [settled.shown(answer_id, data["roles"]["coordinator"]["cwd"])["id"] for answer_id in named.get("ruling") or []]
    flags = {flag: earlier_flags.get(flag) if named.get(flag) is None else named[flag] or None for flag in state.BRIEF_FLAGS} | {"ruling": [*dict.fromkeys([*bound, *ruled])]}
    flags["serves"] = flags["serves"] or " and ".join(part for part in job["parts"] if state.PORT_PART.fullmatch(part)) or ("drive tooling" if state.tooling(order) else "his order")
    flags["mac"] = flags["mac"] or next((mac for key in state.tested_prs(data, job) if key in data["prs"] and (mac := brief.returned_mac(name, data["prs"][key]["job"]))), None)
    if state.adopted_pr_job(data, job) and not named.get("handoff"):
        raise DriveError(
            f"{job_id} is an adopted PR's job, waiting with no agent on {', '.join(job['prs'])}: its builder starts only on an event"
            " that needs action, failed checks, requested changes, a new head or his tap; when the watch reports one, brief it"
            " with --handoff <the PR, its head and the event line verbatim>"
        )
    if named.get("done_when") is not None and (refused := brief.unworded_return(job["kind"], {"--done-when": flags["done_when"]})):
        raise DriveError(f"{job_id}: refused, no brief written: {refused}")
    if unrun := [step for step in named.get("after") or [] if shlex.split(step)[:1] != ["drive"]]:
        raise DriveError(f'{job_id}: --after "{unrun[0]}" is no drive command: drive job prove runs each one once the proof file passes and takes DONE only when it exits 0, so each is a drive verb that exits nonzero when its step did not happen')
    if any(named.get(flag) is not None for flag in ("native", "interactions")) and (seen := brief.surface({"brief_flags": flags})) and (unstoried := brief.unstoried(seen, flags["interactions"])):
        raise DriveError(f"{job_id}'s result is something he uses, by --native: {unstoried}")
    if state.starts_cut(job) and not job["release_build"]:
        raise DriveError(
            f"{job_id}: its brief names the build of the release its add started, which that add's drive job release writes once the release names it:"
            f" its lines are in {state.drive_dir(name) / RELEASES_LOG}, and drive job set {job_id} --state stopped ends a job whose release did not start"
        )
    if not (written := rewrite_brief(name, job_id, flags, job.get("brief_flags"))):
        return write_brief(name, job_id, named)
    line, payload = written
    if kept:
        line += "\nkept from its earlier brief: " + ", ".join(f"--{flag.replace('_', '-')}" for flag in kept)
    if dropped:
        line += "\ndropped from its earlier brief: " + ", ".join(f"--{flag.replace('_', '-')}" for flag in dropped)
    return line, payload | {"kept": kept, "dropped": dropped}


def rebriefed(name: str, job_id: str) -> str:
    """The brief a worker taking a job over starts on: the job's brief written again by rewrite_brief from the flags drive job brief kept, so his words on each order it carries and each answer of his binding it reach that worker as they stand now, then each takeover section an earlier lane started on, so a message one of them carried reaches the next, less the Holds now block it ended with, which the handoff's own block supersedes; or the brief as it stands for a job no drive job brief wrote."""
    job = state.load(name)["jobs"][job_id]
    held = orca.brief_of(Path(job["brief"]).read_text())
    if not job.get("brief_flags"):
        return held
    rewrite_brief(name, job_id, job["brief_flags"], job["brief_flags"])
    _, over, taken = held.partition(orca.TAKING_OVER)
    taken = re.sub(rf"\n*{state.HOLDS_NOW}\n.*?(?=\n{orca.TAKING_OVER}|\Z)", "", taken, flags=re.S)
    return Path(state.load(name)["jobs"][job_id]["brief"]).read_text().strip() + (f"\n\n{over}{taken}" if over else "")


def his_part(part: str) -> bool:
    """Whether a part is his own words, as a part his later message adds to an order is, rather than a part the drive names, such as the immediate fix, an incident's parts, a staging test or a port."""
    return part not in {state.INCIDENT_FIX, state.IMMEDIATE_FIX, state.FOLLOW_UP, *(named for parts in state.INCIDENT_PARTS.values() for named in parts)} and not state.STAGING_PART.fullmatch(part) and not state.PORT_PART.fullmatch(part)


def rewrite_brief(name: str, job_id: str, flags: dict, over: dict | None) -> tuple[str, dict] | None:
    """Write a job's brief from what the drive holds and its flags: his words verbatim on its order and on each order it carries, where its order came from, such as the record or his message `drive order add --from` names, each Slack message they link by linked_messages, his spec on its feature newest first before its Goal, by brief.spec, each of his words whole and each he replaced named by the newer answers replacing it, the earlier jobs' transcripts, and the lines its flags name; none, writing nothing, when the job's flags are no longer over, the ones this write replaces, since another brief write saved its own as this one ran."""
    data = state.load(name)
    job = data["jobs"][job_id]
    order = data["orders"][job["order"]]
    earlier = [line for other in order["jobs"] if other != job_id and (line := transcript_line(other, data["jobs"][other]))]
    planned = order["kind"] == "build" and not order["incident"]
    answers = job["kind"] == "reader" and all(order["kind"] == "answer" or order["parts"][part]["answer"] for part in job["parts"])
    found = brief.research(name, data, job["order"], job["parts"], job_id) if planned and job["kind"] in brief.RESEARCHED_KINDS else None
    rulings = brief.spec_lines(brief.spec(name, data, job | {"brief_flags": flags}))
    carried = "".join(f"\n{order_id}, which it also carries: {data['orders'][order_id]['text']}" for order_id in job["carries"])
    scratch = Path(flags["scratch"]).expanduser() if flags["scratch"] else state.drive_dir(name) / "scratch" / job_id
    staged = any(state.STAGING_PART.fullmatch(part) for part in job["parts"])
    mac, release = mac_lines(name, job, flags)
    boxed = staging_mac_box(name, data, job) if staged else ""
    mac = mac or (boxed and boxed.removeprefix("; "))
    fields = {
        "Order (his words)": order["text"] + carried,
        "Its parts (his words)": [part for part in job["parts"] if his_part(part) and part not in order["text"] and part != order["title"]],
        "From": order["from"],
        "Linked messages": linked_messages(job_id, [order["text"], *(data["orders"][order_id]["text"] for order_id in job["carries"])], flags["handoff"]),
        "Serves": flags["serves"],
        "Decisions that bind": rulings or "none named; search his answers on the area",
        "Fresh": (fresh := state.fresh(data, job))
        and f'his words "{fresh["words"]}", at {fresh["link"] or "the terminal"} on {fresh["at"]}, order this work designed from the ground up: every card asked on it before them was withdrawn, so ask him only the new design\'s crucial directional decisions, in its terms, and decide every implementation detail yourself',
        "Goal": job["goal"],
        "Done when": flags["done_when"],
        "Where": " ".join(filter(None, [flags["where"], (stacked := state.stacked_on(data, job)) and f"Cut your branch {stacked}, so your PR stacks on the changes it waited on."])),
        "Runs as": flags["runs_as"],
        "Release": release,
        "Release build": job["release_build"],
        "Release freezes": brief.releases(name, data),
        "Mac": mac,
        "After install": flags["after"],
        "Handoff": flags["handoff"],
        "Repo rules": state.repo_rules(data),
        "Research": planned and job["kind"] == "reader" and not answers and READER_RESEARCH,
        "Researched": found and found[0],
        "Answer": answers and answer_first(name, order, job["order"], job_id),
        "Staging": staged
        and STAGING_MAC.format(env=providers.of(data).staging.env) + ". "
        + STAGING_FIRST.format(drive=name, attrs=json.dumps(state.thread_attr(data, order) | {"order": job["order"], "job": job_id, "progress": "true", "incident": "true"}))
        + boxed
        + staging_taps(data, job),
        "Native": flags["native"],
        "Interactions": flags["interactions"],
        "Would he ask": flags["would_he_ask"],
        "Plan": flags["plan"],
        "Incident": incident_line(data, job_id, flags["incident"]),
        "Scratch": str(scratch),
        "Earlier parts": job["kind"] == "builder" and earlier_parts(name, data, job_id),
        "Transcripts": earlier,
        "Drive": f"{name}, order {job['order']}, job {job_id}",
    }
    written = state.drive_dir(name) / "jobs" / f"{job_id}.brief.md"
    lines = [
        f"{field}:" + ("".join(f"\n- {item}" for item in value) if isinstance(value, list) else f" {value}")
        for field, value in fields.items()
        if value
    ]
    text = "\n".join(lines) + "\n"

    def kept() -> list[str]:
        scratch.mkdir(parents=True, exist_ok=True)
        written.parent.mkdir(exist_ok=True)
        written.write_text(text)
        return []

    def briefed(data: dict) -> bool | None:
        if data["jobs"][job_id].get("brief_flags") != over:
            return None
        state.after_save(name, kept)
        data["jobs"][job_id].update(brief=str(written), brief_flags=flags, researched=brief.research_links(data, data["jobs"][job_id], text))
        if given := bool(brief.SERVES_TOOLING.search(text)) and data["orders"][job["order"]]["owner"] != "meta":
            data["orders"][job["order"]]["owner"] = "meta"
        return given

    if (given := state.update(name, None, briefed)) is None:
        return None
    line = f"Read your brief first: {written}\nhis words from {', '.join([job['order'], *job['carries']])}, rulings: {len(rulings)}, earlier jobs: {len(earlier)}"
    if given:
        line += (
            f"\n{job['order']} is the meta session's order now, since a builder serving drive tooling is its alone: only the meta session"
            f" starts {job_id} and its agents and posts its reports; from the coordinator, start no agent on it and send"
            f" meta@{name} a defect record naming {job_id} and its `Read your brief first:` line"
        )
    return line, {"id": job_id, "brief": str(written), "rulings": len(rulings), "earlier": earlier}


def linked_messages(job_id: str, texts: list[str], handoff: str | None) -> list[str]:
    """Each Slack message the texts of a job's orders link, read through cc-slack as its brief is written, with who posted it, when, and each message it shares, so no job reads Slack for the message its order came from; a link cc-slack cannot read is kept as not read in cc-slack's words, and refuses the brief only while nothing stands in for it: no linked message read, no word of his in those texts outside their links, and no Handoff, whose merged PR is all a fix's proof needs."""
    items, unread = [], {}
    for link in dict.fromkeys(slack.PERMALINK.findall(html.unescape("\n".join(texts)))):
        try:
            message = slack.message(link)
        except slack.SlackError as error:
            unread[link] = error
            items.append(f"{link}, not read: {error}")
            continue
        who = "him" if message.get("from_user") else message["user_name"] or message["user"]
        shared = "".join(f"\nShared from {share['user_name'] or share['user']}: {share['text'] or share.get('unreadable', '')}" for share in message.get("shares", []))
        items.append(f"{link}, posted by {who} at {slack.sent_at(message).isoformat(timespec='seconds')}:\n" + textwrap.indent(html.unescape(message["text"]) + shared, "  "))
    if unread and len(unread) == len(items) and not handoff and not any(re.search(r"\w", slack.PERMALINK.sub("", html.unescape(text))) for text in texts):
        raise DriveError(f"{job_id}: no brief written, since cc-slack cannot read {'; '.join(f'{link}, which its order links: {error}' for link, error in unread.items())}; write it again once cc-slack reads it")
    return items


def incident_line(data: dict, job_id: str, given: str | None) -> str | None:
    """A brief's Incident: line: the call's own, then on an incident order a builder's release rule and the rule a lane of its kind follows on a choice, which a choice never stops, then on a job tracing a live bug's cause the FIX line its report ends with, each once."""
    job = data["jobs"][job_id]
    incident = data["orders"][job["order"]]["incident"]
    rules = [release_rule() if incident and job["kind"] == "builder" else None, state.INCIDENT_CHOICE.get(job["kind"]) if incident else None, state.FIX_RULE if state.traced(data, job_id, "") else None]
    return "; ".join(filter(None, [given, *(rule for rule in rules if rule and rule not in (given or ""))])) or None


def release_rule() -> str:
    """The rule incidents.md states for every release a drive job starts, read from the page so the brief and the page share one text."""
    return " ".join(page_parts((PAGES / "incidents.md").read_text(), "Every release a drive job starts")[0].split()).removeprefix("- ")


def earlier_parts(name: str, data: dict, job_id: str) -> list[str]:
    """The SUMMARY: block of the newest return carrying one of each other builder on the job's order, headed by that builder and its parts, so the next part's builder starts from what the earlier ones touched, decided and left open instead of reading their work again."""
    order_id = data["jobs"][job_id]["order"]
    order = data["orders"][order_id]
    found = []
    for other in order["jobs"]:
        held = data["jobs"][other]
        if other == job_id or held["kind"] != "builder":
            continue
        if lines := next((kept for reply in state.returns(name, other) if (kept := state.summary(reply["body"]))), []):
            found.append(f"{other} on {', '.join(map(repr, state.job_parts(held, order_id)))}:" + "".join(f"\n  {line}" for line in lines))
    return found


def transcript_line(job_id: str, job: dict) -> str | None:
    if job.get("where") == "remote" and job.get("dispatch") and not artifacts.lane_transcript(job):
        return f"{job_id} {job['kind']}, a remote lane: drive job replies {job_id}"
    if job.get("codex_run"):
        return f"{job_id} codex, its codex-ask run: {job['codex_run']}"
    path = artifacts.transcript(job)
    return f"{job_id} {job['kind']}: {path}" if path else None


@orca.rides_restarts()
def job_launch(args: argparse.Namespace) -> int:
    """Start a job's next worker by the one relaunch launch and resume share, with no message: its first Orca lane, on a Sprite or with --local on this Mac, where a brief declaring a step only this Mac runs on its Mac: or Release: line starts, stamped as launching once nothing refuses it so the watch reads it as starting until the launch could have finished; the next lane of a job whose worker ended or returned its line, with --local moved from its Sprite to this Mac; a lane taking over from an agent on this Mac that stopped or whose session ended; or the worker an earlier launch of its brief started and never recorded, whose ready receipt the launcher keeps; never a finished pass of a standing part, which unrun refuses, nor a stopped job, which unstopped refuses. Given drive job brief's flags, it first writes the job's brief from them by write_brief, so briefing and starting a job is one command and a flag argparse or write_brief refuses starts nothing; refused beside a brief file, which is the brief whole."""
    name = state.resolve(args.drive)
    if named := {flag: value for flag in state.BRIEF_FLAGS if (value := getattr(args, flag, None)) not in (None, [])}:
        unstopped(args.id, unrun(state.load(name), args.id))
        if args.brief:
            flags = ", ".join(f"--{flag.replace('_', '-')}" for flag in named)
            raise DriveError(f"{args.id}: refused, nothing written or started: {args.brief} is its brief whole, which {flags} cannot add a line to: drop the file or the flags")
        line, payload = write_brief(name, args.id, named)
        emit(args, f"{args.id}'s brief written at {payload['brief']}: {line.partition("\n")[2]}", payload)
    if unrun(state.load(name), args.id)["kind"] == "codex":
        folder = codex_launch(name, args.id, args.brief)
        return emit(args, f"{args.id} started its codex-ask run in {folder}; the watch takes its result once it ends", {"id": args.id, "codex_run": str(folder)})
    return relaunch(args, name, None, resume=False)


def codex_launch(name: str, job_id: str, brief_file: str | None) -> Path:
    """Start a codex job's codex-ask run on this Mac from this session's folder on the brief file or the job's brief, with each text the job holds under the heading naming its sender and each answer of his it has not heard under His answers since:, by answers_since, kept as the job's brief so the builder that ships its fix reads what it was given, with no agent relaying it, the texts it carries off the job and each answer it carries marked sent, and the run recorded in place of any agent an earlier lane left on the job, so the watch reads this run and never that agent's old turn as its result; the run's folder. Refused as an Agent's start is refused for any other job: once stopped, by unstopped, while its waits hold, by view.start_refusal, with no brief, or on a brief brief_refusal refuses; codex-ask itself refuses a run while the job's last one still goes."""
    from drive import view

    with state.job_locked(name, job_id, orca.LAUNCH_SECONDS):
        data = state.load(name)
        job = data["jobs"][job_id]
        unstopped(job_id, job)
        if refused := view.start_refusal(data, job_id, True):
            raise DriveError(refused)
        if not (brief_file or job.get("brief")):
            raise DriveError(f"{job_id} has no brief: write it with drive job brief {job_id}, or name its file")
        written = answers_since(data, job_id, named_text(brief_file or job["brief"], "brief file"))
        if refused := brief.brief_refusal(name, data, job_id, written):
            raise DriveError(f"{job_id}: refused, not launched: {refused}")
        try:
            folder = codex.ask(name, job_id, written, data["orders"][job["order"]]["incident"], job["model"])
        except codex.CodexError as error:
            raise DriveError(f"{job_id}: refused, not launched: {error}") from None
        kept = launch_brief(name, job_id, job)
        kept.parent.mkdir(exist_ok=True)
        kept.write_text(written)
        def codex_started(data: dict) -> None:
            job = data["jobs"][job_id]
            job.update(
                codex_run=str(folder),
                agent=None,
                brief=str(kept),
                researched=brief.research_links(data, job, written),
            )
            state.start(job)
            superseded.hear(data, job_id, written, state.now())

        state.update(name, None, codex_started)
    return folder


def job_start(args: argparse.Namespace) -> int:
    """Start a briefed builder's, reader's or design job's agent on this Mac as its own `claude --bg` session, as the watch does once nothing holds the job, so no session's Agent call starts it and no session relays its texts: under the job's own lock, refused as the agent_brief hook would refuse an Agent call of the session holding its order on its brief, which also stamps its launch, counts its builder slot or holds it waiting its turn, and refused while an earlier start of it is in flight or at work, by agents.gone on its bg entry, the start lock, or an agent of a session of the drive runs it; the job keeps the agent id every hook of that session binds as, by agents.seen, and the session `claude --bg` names, which state.own_session reads. A codex or Sprite job, or one with no brief, is refused, its starting left to the session holding it. A start the hook holds waiting its turn for a builder slot, or sends to a Sprite, is no refusal: the first starts on the watch's FREE line as its turn comes, the second by drive job launch, run here, so neither wakes the session holding it."""
    name = state.resolve(args.drive)
    with state.job_locked(name, args.id, orca.LAUNCH_SECONDS):
        data = state.load(name)
        job = find(data["jobs"], args.id, "job")
        unstopped(args.id, job)
        if job["kind"] not in state.STARTS_ITSELF or job.get("where") == "remote" or not job.get("brief"):
            raise DriveError(f"{args.id}: refused, not started: drive job start starts only a briefed builder, reader or design job on this Mac, and this is a {job['kind']}{' on a Sprite' if job.get('where') == 'remote' else ''}{'' if job.get('brief') else ' with no brief'}")
        if (bg := job.get("bg")) and not agents.gone(bg):
            raise DriveError(f"{args.id}: refused, not started: its start at {bg['started']}, session {bg['session'] or 'not named yet'}, is in flight or at work")
        if job["agent"] and job["agent"] != (bg or {}).get("agent") and job["state"] == "running" and state.session_role(data, job.get("session")):
            raise DriveError(f"{args.id}: refused, not started: agent {job['agent']} of session {job['session']} runs it")
        _, held = state.holding_role(data, data["orders"][job["order"]])
        if not held.get("session"):
            raise DriveError(f"{args.id}: refused, not started: no session holds {job['order']}, so none would hear its agent")
        call = {"subagent_type": state.JOB_AGENTS[job["kind"]][0], "prompt": f"Read your brief first: {job['brief']}"}
        call = agents.shaped({"session_id": held["session"], "tool_input": call})
        if refused := start_refusal({"session_id": held["session"], "tool_input": call}):
            if state.SLOT_WAIT in state.load(name)["jobs"][args.id]["waits"]:
                return emit(args, f"{args.id} waits its turn for a builder slot on this Mac: {refused}", {"id": args.id, "how": "waits its turn"})
            if ON_A_SPRITE in refused:
                state.detach(name, ["job", "launch", args.id], watch.RELAUNCHES_LOG)
                return emit(args, f"{args.id} starts on a Sprite, by drive job launch {args.id}: {refused}", {"id": args.id, "how": "launch"})
            raise DriveError(f"{args.id}: refused, not started: {refused}")
        agent, cwd = uuid.uuid4().hex[:17], orca.checkout(name, held["cwd"])
        def job_start_write(data: dict):
            data["jobs"][args.id]["bg"] = {"agent": agent, "session": None, "started": state.stamp(), "cwd": str(cwd), "pointed": []}
            return data

        data = state.update(name, None, job_start_write)
        try:
            session = agents.start_job(name, args.id, agent, call, held, cwd)
        except (reads.ReadError, state.DriveError) as error:
            def job_start_write3(data: dict):
                data["jobs"][args.id]["bg"] = None
                return data

            data = state.update(name, None, job_start_write3)
            raise DriveError(f"{args.id}: refused, not started: claude --bg started no agent: {error}") from None
        def job_start_write2(data: dict):
            data["jobs"][args.id]["bg"]["session"] = session
            data["jobs"][args.id].update(agent=agent, launching=None)
            state.start(data["jobs"][args.id])

        state.update(name, None, job_start_write2)
    return emit(args, f"{args.id} started as agent {agent}, its own claude --bg session {session}: start no agent for it", {"id": args.id, "agent": agent, "session": session})


def start_refusal(payload: dict) -> str | None:
    """Why the agent_brief hook refuses the Agent call payload makes, read for a start that makes none, so drive job start meets every check an Agent start does."""
    hooks = str(Path(__file__).resolve().parents[2] / "hooks")
    if hooks not in sys.path:
        sys.path.append(hooks)
    import agent_brief

    return agent_brief.deny_reason(payload)


@orca.rides_restarts()
def job_resume(args: argparse.Namespace) -> int:
    """Send a job its next event, or with no text the texts it holds, by the one relaunch launch and resume share: to its lane while its worker takes messages, else in the handoff of its next worker, or in the brief of the first worker of a builder waiting its turn for a slot on this Mac that none started, or a codex job's by codex_resume. A finished pass of a standing part is refused by unrun, holding nothing."""
    name = state.resolve(args.drive)
    if unrun(state.load(name), args.id)["kind"] == "codex":
        return codex_resume(args, name)
    unqueued(name, args.id)
    return relaunch(args, name, args.text, resume=True)


def codex_resume(args: argparse.Namespace, name: str) -> int:
    """Give a codex job the text, and every other text it holds, each under the heading state.held_since gives its sender, the text under its sender's role: held while a run goes or its result waits to be taken, which landing_guard hands the run at its next Bash call, since codex-ask runs codex exec on a question it reads whole as the run starts and that hook is the one thing a running run hears from outside it, and which the watch's launch carries once the job returns and stays open when the run ends first; else carried by a run started now, the text held when that start is refused. A stopped job is refused as any resume is."""
    data = state.load(name)
    job = data["jobs"][args.id]
    unstopped(args.id, job)
    sender = state.held_sender(data)
    if args.text is not None:
        hold(name, args.id, args.text, sender)
    elif not job["undelivered"]:
        return emit(args, f"{args.id} holds no message to send", {"id": args.id, "how": None})
    under = " and ".join(dict.fromkeys(state.since_heading(who) for who in [*(kept["sender"] for kept in job["undelivered"]), *([sender] if args.text is not None else [])]))
    if job["state"] == "running":
        line = f"{args.id}: held under {under} for its codex-ask run, which landing_guard hands it at the run's next Bash call; the watch's next launch carries it if the run ends first"
        return emit(args, line, {"id": args.id, "how": "held"})
    try:
        folder = codex_launch(name, args.id, None)
    except DriveError as error:
        raise DriveError(f"{args.id}: held for its next codex-ask run under {under}, none started now: {error}") from None
    return emit(args, f"{args.id}: the message went to its next codex-ask run, started in {folder}, under {under}", {"id": args.id, "how": "launched", "codex_run": str(folder)})


class StoodDown(DriveError):
    """A launch or resume that started, sent and held nothing since another drive verb acts on its job now, so nothing is left to fix: one state.detach started writes no relaunch_refused event."""


def unqueued(name: str, job_id: str) -> None:
    """Stand a resume down, sending and holding nothing, rather than queue for the job's lock while another verb holds it and Orca cannot reach the job's lane on its Sprite, by orca.locked_out, as when another resume wakes that Sprite for up to five minutes, so resumes of a lane that does not answer never pile up on its lock; the refusal names that verb and the local launch that reaches the job now, by orca.moved_here."""
    if why := orca.locked_out(name, job_id, find(state.load(name)["jobs"], job_id, "job")):
        raise StoodDown(f"{job_id}: refused, nothing sent or held: {why}; {orca.moved_here(job_id)}, or run this again once that verb ends")


def yield_to_launch(name: str, job_id: str) -> None:
    """Stand a resume holding the job's lock down, before each step that wakes, reads, saves, messages or starts anything, while a drive job launch --local waits for that lock to move the job to this Mac; the texts the job holds stay on it for that launch's handoff."""
    if pid := state.launch_waiting(name, job_id):
        raise StoodDown(f"{job_id}: stood down, nothing sent or started: drive job launch {job_id} --local, pid {pid}, waits for its lock to move it to this Mac, and its handoff carries the texts the job holds")


def unrun(data: dict, job_id: str) -> dict:
    """The job a launch or resume starts or messages the worker of, by held_job, refused before anything is written, held, sent or started when it is a finished pass of a standing part, since the watch alone starts each pass of one, by standing_lines, and a second start beside the pass the watch started drives the part's lane twice; the refusal names the part's live pass, else when the watch starts its next."""
    job = held_job(data, job_id)
    if job["state"] in state.FINISHED_JOB_STATES and (standing := state.standing_part(data, job)):
        raise DriveError(
            f"{job_id}: refused, nothing written, held, sent or started: it ended {job['state']} as a pass of {standing[0]}'s standing part"
            f" {render.standing_line(data, *standing)}; the watch alone starts each pass of a standing part, carrying the texts and answers the last pass holds"
        )
    return job


def unstopped(job_id: str, job: dict) -> None:
    """Refuse a launch or resume of a stopped job, read under the job's own lock, sending, holding and starting nothing, so a start the watch began while the job ran never reaches a lane its stop retired or starts a second writer beside the job that replaced it; a new job takes its parts."""
    if job["state"] == "stopped":
        parts = "".join(f" --part {shlex.quote(part)}" for part in job["parts"])
        raise DriveError(f"{job_id}: refused, nothing sent, held or started: it is stopped; drive job add {job['kind']} --order {job['order']}{parts} adds the job that takes its parts")


def relaunch(args: argparse.Namespace, name: str, text: str | None, resume: bool) -> int:
    """The one path launch and resume share, under the job's own lock, so a second waits for the first and acts on the worker it started, each refused for a stopped job by unstopped, a resume with no text carrying the texts the job holds wherever a resume's text goes and saying so when it holds none: a lane whose worker takes messages gets the text, one whose worker ended starts its next in a handoff, and a lane on a Sprite moves to this Mac by restart whatever its worker is doing when --local asks or declared_step reads a step only this Mac runs, an agent on this Mac that ended with its session, whose last response ended on an API error or refusal, that a launch finds returned while the job holds texts, or whose turn ended while a resume finds this Mac's builder slots full hands the job to a lane on a Sprite, or on this Mac when its brief declares a step only this Mac runs, an agent a session of the drive started and still holds gets a resume's text through its cci address and that session's SendMessage by to_starter, or each text the job holds by held_to_starter, and another session's agent refuses a launch, an agent whose session ended hands the job to this one, a resume's text for a builder no worker started that waits its turn for a slot on this Mac is held for the first worker the watch starts as its turn comes, and any other job with no worker yet starts its first."""
    here = not resume and args.local
    if here and state.job_lock_held(name, args.id):
        state.said(
            args.id,
            f"it waits up to {orca.LAUNCH_SECONDS} s for the job's lock, which {state.holders(state.job_lock_path(name, args.id))} holds;"
            " a resume holding it stands down at its next step, and one inside a launch finishes that launch first",
        )
    with state.launch_waits(name, args.id) if here else nullcontext(), state.job_locked(name, args.id, orca.LAUNCH_SECONDS):
        data = state.load(name)
        job = find(data["jobs"], args.id, "job")
        unstopped(args.id, job)
        if resume and text is None and not job["undelivered"]:
            return emit(args, f"{args.id} holds no message to send", {"id": args.id, "how": None})
        if resume and state.own_session(job):
            lane = state.session_lane(data, job["session"]) or state.holding_job_lane(data, job)
            return held_to_starter(args, name, job, lane) if text is None else to_starter(args, name, job, lane)
        if lane := starters_lane(data, args.id, job, resume):
            if not resume:
                raise DriveError(
                    f"{args.id}: refused, not launched: it runs as agent {job['agent']} of session {job['session']}, which still holds it and whose SendMessage"
                    f' alone reaches it, so the agent finishes its job: drive job resume {args.id} "<the event>" posts the event to the agent\'s cci address and {lane}@{name} the SendMessage pointing it there'
                )
            return held_to_starter(args, name, job, lane) if text is None else to_starter(args, name, job, lane)
        taker = other_sessions_agent(data, job)
        if taker and resume:
            return hand_over(args, name, data, taker)
        if job.get("where") == "remote" and job.get("dispatch"):
            if resume:
                if text is not None:
                    hold(name, args.id, text, state.held_sender(data))
                return deliver(args, name, text)
            local = args.local and not state.on_mac(job)
            step = declared_step(name, job)
            try:
                end = orca.lane_end(name, args.id, job, wake=not (local or step))
            except orca.OrcaError as error:
                raise DriveError(
                    f"{args.id}'s lane cannot be checked, so nothing was started: {error}; launch it again once Orca reaches its lane {orca.ON_MAC if state.on_mac(job) else 'on its Sprite'}"
                ) from None
            if end or local or step:
                return restart(args, name, job["dispatch"], end or live_end(job), None, local, step)
            raise DriveError(f"{args.id} already started as dispatch {job['dispatch']}, whose worker takes messages: drive job resume {args.id} <the event> sends it one")
        if text is not None and not job.get("agent") and args.id in (places := place.positions(data)):
            sender = state.held_sender(data)
            def held_first(data: dict) -> None:
                state.hold_text(data["jobs"][args.id], text, sender)
                state.told(data["jobs"][args.id], [text])

            state.update(name, None, held_first)
            line = (
                f"{args.id}: held for its first worker under {state.since_heading(sender)}, since none has started: it waits its turn for a builder slot on this Mac,"
                f" {places[args.id]}, and the watch starts that worker as its turn comes, its brief carrying each text the job holds, and says when it goes"
            )
            return emit(args, line, {"id": args.id, "how": "held"})
        died = None if taker or not job.get("agent") else watch.agent_death(job, data) or (place.woken(data, args.id, state.now()) if resume else returned(job))
        if job.get("agent") and not (taker or died):
            remote_job(data, args.id)
        if resume and not died:
            started_job(data, args.id)
        agent = job.get("agent")
        started = launched(name, args.id, None if resume else args.brief, here, text, died)
        job = record_start(name, args.id, started, None, text)
        if job["state"] != "running":
            ended, _ = end_lane(name, args.id)
            return emit(args, f"{args.id}: a wait or stop set while its first lane launched holds it, so that lane, started {orca.place(job)} {as_worker(started)}, is ended\n{ended}", started)
    line = f"{args.id} started {orca.place(job)} {as_worker(started)}"
    if text is not None:
        line = f"{args.id}: the message went to the job's next worker, started {orca.place(job)} {as_worker(started)} in a handoff carrying it"
    elif resume:
        line = f"{args.id}: the texts it held went to the job's next worker, started {orca.place(job)} {as_worker(started)} in a handoff carrying them"
    if died:
        line += f", taking over from its agent {agent} on this Mac, which takes no message: {died}"
    elif (state.on_mac(job) or started.get("bg")) and not (text is None and args.local):
        line += ", since its brief declares a step only this Mac runs"
    return emit(args, line, started)


def to_starter(args: argparse.Namespace, name: str, job: dict, lane: str) -> int:
    """Give a resume's text to an agent on this Mac that a session of the drive started and still holds, by his pick "Keep with the starter: the agent finishes its job, your answers reach it at once", as starter_post posts it. Nothing held, handed over or started."""
    seq, line = starter_post(name, args.id, job, lane, args.text, state.stamp())
    return emit(args, line, {"id": args.id, "how": f"handoff to {lane}", "seq": seq})


def held_to_starter(args: argparse.Namespace, name: str, job: dict, lane: str) -> int:
    """Give each text the job holds, oldest first, to its agent on this Mac that a session of the drive started and still holds, in one record by starter_post, each held text under the last, so a resume with no text sends the words the drive holds whole in one record the agent reads once and the session that runs it types one SendMessage line, by his words "make everything less chatty"; a text another resume took off the job meanwhile is not posted again. Nothing held, handed over or started."""
    if not (given := deliver_held(name, args.id, job, lane, job["undelivered"])):
        return emit(args, "", {"id": args.id, "how": f"handoff to {lane}", "seqs": []})
    seq, line = given
    return emit(args, line, {"id": args.id, "how": f"handoff to {lane}", "seqs": [seq]})


def resent(kept: dict) -> str:
    """A held text as it goes out again: his words marked re-sent with their own time and the record they came in, so no reader takes them for words he wrote now."""
    if kept["sender"] != state.HIM:
        return kept["words"]
    came = f", record {kept['seq']}" if kept["seq"] else ""
    return f"His words of {render.when(kept['at'])}{came}, re-sent: {kept['words']}"


def deliver_held(name: str, job_id: str, job: dict, lane: str, held: list[dict]) -> tuple[str, str] | None:
    """Post these held texts to the job's agent on this Mac in one record by starter_post, his first, each of his re-sent with its own time, and take them off the job; when the agent is a subagent only the session that started it reaches, that session gets a second record at once carrying the owed SendMessage line, so his words never wait for a `drive job resume`; the record's seq and the line, None for no texts."""
    from drive import tools

    left = {kept["request"] for kept in state.load(name)["jobs"][job_id]["undelivered"]}
    held = sorted((kept for kept in held if kept["request"] in left), key=lambda kept: kept["sender"] != state.HIM)
    if not held:
        return None
    seq, line = starter_post(name, job_id, job, lane, "\n\n".join(map(resent, held)), held[0]["at"], held[0]["request"])
    taken = {kept["request"] for kept in held}

    def deliver_held_write(data: dict):
        data["jobs"][job_id]["undelivered"] = [kept for kept in data["jobs"][job_id]["undelivered"] if kept["request"] not in taken]
        return data

    state.update(name, (("jobs", job_id),), deliver_held_write)
    if OWED in line and state.session_lane(state.load(name), os.environ.get("CLAUDE_CODE_SESSION_ID")) != lane:
        his = any(kept["sender"] == state.HIM for kept in held)
        tools.sent(name, state.WATCH_SENDER, [{"kind": "handoff", "to": [f"{lane}@{name}"], "attr": {"order": job["order"], "job": job_id, "urgent": "true"}, "text": f"{job_id}: {'his words: ' if his else ''}{line[line.index(OWED):]}"}])
    return seq, line


def deliver_to_order(name: str, job_id: str, job: dict, held: list[dict]) -> str | None:
    """Hand his held words on a job whose agent is not to be woken, one that returned BUILT or DONE or waits only on his own open cards, to whoever acts on them now, once: the job its work was handed to, in turn, while one is not finished, which holds them for its own worker, else the session holding the job's order as one record to read, each re-sent with its own time, the texts kept on the job marked told so no pass sends them again until that session marks them heard with `drive job set <job> --heard`; the line saying where they went, None for no texts."""
    from drive import tools

    if not held:
        return None
    data = state.load(name)
    taker = job["handed_to"]
    while taker and data["jobs"][taker]["state"] in state.FINISHED_JOB_STATES:
        taker = data["jobs"][taker]["handed_to"]
    taken = {kept["request"] for kept in held}
    if taker:
        def handed_write(data: dict):
            for kept in held:
                state.hold_text(data["jobs"][taker], kept["words"], kept["sender"], kept["at"], kept["seq"])
            data["jobs"][job_id]["undelivered"] = [kept for kept in data["jobs"][job_id]["undelivered"] if kept["request"] not in taken]
            return data

        state.update(name, None, handed_write)
        return f"{job_id}: its held texts went to {taker}, which took its work"
    lane = state.holding_lane(data, data["orders"][job["order"]])
    why = f"returned {job['state']}" if job["state"] in superseded.FRESH_STATES else "waits only on his own open cards"
    lead = (
        f"{job_id} {why}, so its agent is not woken for his words held on it: read them, act through order {job['order']} only on work they order"
        f" that no job carries, then mark them heard with `drive job set {job_id} --heard`"
    )
    [seq] = tools.sent(name, state.WATCH_SENDER, [{"kind": "handoff", "to": [f"{lane}@{name}"], "attr": {"order": job["order"], "job": job_id}, "text": "\n\n".join([lead, *map(resent, held)])}])

    def told_write(data: dict):
        for kept in data["jobs"][job_id]["undelivered"]:
            if kept["request"] in taken:
                kept["told"] = seq
        return data

    state.update(name, (("jobs", job_id),), told_write)
    return f"{job_id}: his held words went to {lane}@{name}, which holds its order, as record {seq} to read"


def starter_post(name: str, job_id: str, job: dict, lane: str, text: str, at: str, held: str | None = None) -> tuple[str, str]:
    """Post a text to an agent on this Mac that a session of the drive started and still holds, once superseded.fresh_builder sends no PR event to a builder that returned: whole, at once, from the resuming session's lane as one handoff record to the agent's own cci address, peer:<session>/<agent>, kept by sent_kept dated `at`, when it was held, so each answer of his it carries reads sent and the text the job holds under the request id held leaves the job, addressed to the starter's lane too, its sendmessage attr naming the agent, as one record, by his words "make everything less chatty", since only the starter's SendMessage reaches the agent, which points it at that record's seq; a starter resuming its own agent gets the line as the verb's output. The record's seq and the line the verb prints."""
    from drive import tools

    data = state.load(name)
    if fresh := superseded.fresh_builder(data, job_id, job, text, "this text"):
        raise DriveError(f"{job_id}: refused, nothing posted: {fresh[1]}")
    sender = state.session_lane(data, os.environ.get("CLAUDE_CODE_SESSION_ID")) or job_id
    session = state.own_session(job)
    starter = [] if session or lane == sender else [f"{lane}@{name}"]
    attrs = {"order": job["order"], "job": job_id} | ({"sendmessage": job["agent"]} if starter else {})
    record = {"kind": "handoff", "to": [state.agent_address(job), *starter], "attr": attrs, "text": text}
    [seq] = tools.sent(name, sender, [limits.shortened(record) or record])
    sent_kept(name, job_id, {"words": text, "request": seq, "held": held, "at": at, "ask": job.get("ask")})
    if session:
        return seq, own_post(name, job_id, session, seq)
    line = f'{job_id}: {OWED} SendMessage {job["agent"]} "{superseded.POINTER.format(seq)}"'
    if not starter:
        return seq, line
    return seq, f"{job_id}: posted to agent {job['agent']}'s cci address and to {lane}@{name} as handoff record {seq}, its sendmessage attr naming the agent whose SendMessage alone reaches it: {line}"


def own_post(name: str, job_id: str, session: str, seq: str) -> str:
    """Point a job's agent running as its own `claude --bg` session at the record starter_post just posted to its address, with no SendMessage: while its turn runs, its next step's hook, or its stop's, hands it the pointer, and once its turn ended `claude --bg --resume` wakes it on the pointer; the line the verb prints."""
    pointer = superseded.POINTER.format(seq)
    bg = state.load(name)["jobs"][job_id]["bg"]
    if not agents.gone(bg):
        def own_post_write(data: dict):
            data["jobs"][job_id]["bg"]["pointed"].append(pointer)

        state.update(name, None, own_post_write)
        return f"{job_id}: posted to its own session {session}'s cci address as handoff record {seq}, which its next step reads"
    try:
        agents.resume_job(name, job_id, bg, pointer)
    except reads.ReadError as error:
        raise DriveError(f"{job_id}: posted as handoff record {seq}, but claude --bg --resume {session} did not wake it: {error}") from None
    return f"{job_id}: posted to its own session {session}'s cci address as handoff record {seq}, and resumed that session on it"


def returned(job: dict) -> str | None:
    """Why an agent on this Mac takes no text the job holds: it returned, and only its session's SendMessage reaches it; None while it works or the job holds none."""
    if job["state"] == "running" or not job["undelivered"]:
        return None
    return f'it returned "{render.cut(job["said"] or "", 100)}", and only its session\'s SendMessage reaches it'


def hold(name: str, job_id: str, text: str, sender: str | None) -> None:
    """Keep the text on the job's undelivered list with who sent it by state.hold_text before any Orca call, so an outage delays it and never drops it."""
    def held(data: dict):
        state.hold_text(data["jobs"][job_id], text, sender)
    state.update(name, None, held)


def hold_unread(name: str, job_id: str, lane: dict) -> list[str]:
    """Hold on the job each text the lane's ended worker never read, read by orca.mailbox from the Orca that keeps that worker's Dispatch mailbox, since Orca shows a finished worker none of its mail and a relaunch carries only what the job holds, so the job's next worker gets each as any held text gets there, and take off the job's queued list each text that mailbox shows read or holds, by state.take_mail; the texts held that the job did not hold already. The lane is the job as the watch read it with that worker, so a relaunch since then never has its live worker's mail held. OrcaError when that mailbox cannot be read."""
    box = orca.mailbox(lane, lane["dispatch"])
    return state.update(name, None, lambda data: state.take_mail(data["jobs"][job_id], box, True))


def deliver(args: argparse.Namespace, name: str, text: str | None = None) -> int:
    """Deliver every text the job holds, oldest first, then the resume's text when the job holds no copy of it, as for the watch's restart notes, which hold_text never keeps, once Orca shows its lane: sent to a worker that takes messages, else carried in the handoff of its next worker, which for a lane on a Sprite whose declared_step reads a step only this Mac runs starts on this Mac by restart, its Sprite worker stopped if it still works and nothing sent to it; while Orca cannot tell or starts no next worker, each held text stays held for the watch to send again, and the note goes to no one, since this resume's wake or restart was the one it was written for."""
    data = state.load(name)
    job = data["jobs"][args.id]
    held = job["undelivered"]
    note = None if text is None or any(kept["words"] == text for kept in held) else state.kept_text(text, state.held_sender(data))
    yield_to_launch(name, args.id)
    step = declared_step(name, job)
    try:
        end = orca.lane_end(name, args.id, job, wake=step is None)
    except orca.OrcaError as error:
        return unsent(args, name, job, held, error, note)
    if not (end or step):
        return messaged(args, name, job, held, note)
    yield_to_launch(name, args.id)
    try:
        return restart(args, name, job["dispatch"], end or live_end(job), note and note["words"], declared=step)
    except orca.OrcaError as error:
        return unsent(args, name, state.load(name)["jobs"][args.id], held, error, note)


def mac_lines(name: str, job: dict, flags: dict) -> tuple[str | None, str | None]:
    """The Mac: and Release: lines a job's brief flags give it, as rewrite_brief writes them and declared_step reads them: its mac flag, which write_brief fills for a staging tester from its builder's newest return holding a Mac: line, else the staging provider's exception steps for a tester asking his exception, by state.EXCEPTED, and its release flag; a staging tester's slot claim runs from a Sprite lane too, so it goes on the Staging line, never the Mac: line, and rewrite_brief adds its Mac box cases there."""
    staged = any(state.STAGING_PART.fullmatch(part) for part in job["parts"])
    return flags["mac"] or (providers.of(state.load(name)).staging.exception_steps() if staged and state.EXCEPTED.match(job["goal"]) else None), flags["release"]


def declared_step(name: str, job: dict) -> str | None:
    """The step only this Mac runs that a job on a Sprite declares on a Mac: or Release: line, when a launch recorded one rather than a lane recorded by hand: the lines its brief flags give it by mac_lines, the ones the brief a resume writes it carries, since the brief its lane started on may predate a staging part or a --mac flag, else the lines of its brief by place.mac_step for a job no drive job brief wrote; the one placement read every resume, wake and launch of a Sprite job makes before it sends or starts anything, never its report, a text it holds or the text given, since a return's Mac: line places the prover install.add_prover hands its merge to; None for a job on this Mac or one declaring none."""
    if state.on_mac(job) or not job.get("brief"):
        return None
    if not (flags := job.get("brief_flags")):
        return place.mac_step(Path(job["brief"]).read_text())
    mac, release = mac_lines(name, job, flags)
    return mac or release


def live_end(job: dict) -> orca.LaneEnd:
    """The end a move to this Mac gives a lane on a Sprite whose worker still takes messages, which the move stops: one still at work while its job runs, else one that returned its line."""
    why = "its worker was still at work there" if job["state"] == "running" else "its worker returned its line and still takes messages there"
    return orca.LaneEnd(why, None, None, live=True)


def messaged(args: argparse.Namespace, name: str, job: dict, held: list[dict], note: dict | None = None) -> int:
    """Send each held text, then the restart note a resume gave, to a lane whose worker takes messages, each under its own request id and ending with stage.holds_now's read of what holds the job and its PRs, kept on the text at its first try so every retry under that id sends what Orca took, since Orca refuses one id reused with another body as request_mismatch, dropping each from the job once Orca takes it, and mark the job sent each answer of his the text carries; first put on its Sprite the tools a launch puts there, the drive forwarder among them, when no start this drive recorded put them on that Sprite, as for a lane started before the forwarder existed or recorded by hand."""
    from drive import stage

    unfit, workspace = None, job.get("workspace")
    if job.get("tools_on") != workspace:
        try:
            orca.place_tools(workspace)
        except orca.Unfit as error:
            unfit = error
    def holds_kept(data: dict) -> str:
        holds = stage.holds_now(data, args.id)
        for left in data["jobs"][args.id]["undelivered"]:
            left.setdefault("holds", holds)
        return holds

    holds = state.update(name, None, holds_kept)
    hows = []
    for index, kept in enumerate([*held, *filter(None, [note])]):
        try:
            how, queued = orca.answer(name, job, kept | {"words": f"{kept['words']}\n\n{kept.get('holds', holds)}"})
        except orca.OrcaError as error:
            return unsent(args, name, job, held[index:], error, note)
        hows.append(how)
        sent_kept(name, args.id, kept | {"ask": job.get("ask"), "queued": queued} | ({} if unfit else {"tools_on": workspace}))
        job = {**job, "ask": None, "turn_open": True}
    how = "; ".join(hows) + (f"; its Sprite still lacks the drive forwarder: {unfit}" if unfit else "")
    return emit(args, f"{args.id}: {how}", {"id": args.id, "how": how})


def sent_kept(name: str, job_id: str, sent: dict) -> dict:
    """Keep on the job a held text its live worker took: written to jobs/<j>.sent-<request>.md beside the job's launch texts, dated when it was held, which superseded.hear_start reads whenever an answer of his lists the job later, kept among the texts its workers were given, which the handoff of a worker taking over before the job's next return carries, dropped from the job's held texts, and when Orca queued it in the mailbox of the Dispatch the job still runs on rather than answering the question it asked, kept on its queued list until a mailbox read state.take_mail makes finds it read or holds it again, its worker's turn read open once a lane took it, since its question holds that turn open or orca.woke started it, its question closed when the text answered the one it still asks, the job then running but a stopped one, which stays stopped, by state.run_state, each answer of his the text carries marked sent, and the tools on the Sprite tools_on names."""
    given_text = state.drive_dir(name) / "jobs" / f"{job_id}.sent-{sent['request']}.md"
    given_text.write_text(sent["words"])
    held = state.parse_time(sent["at"]).timestamp()
    os.utime(given_text, (held, held))
    def sent_kept_write(data: dict):
        fresh = data["jobs"][job_id]
        if fresh.get("ask") == sent["ask"]:
            fresh.update(ask=None, state=state.run_state(fresh))
        fresh.update(
            updated=state.stamp(),
            undelivered=[left for left in fresh["undelivered"] if left["request"] not in (sent["request"], sent.get("held"))],
        )
        if sent.get("queued") and sent["queued"] == fresh["dispatch"] and all(queued["words"] != sent["words"] for queued in fresh["queued"]):
            fresh["queued"].append({"words": sent["words"], "sender": sent["sender"], "at": fresh["updated"]})
        if "queued" in sent:
            fresh["turn_open"] = True
        state.went(name, data, job_id, [sent], "to its live worker")
        fresh["given"].append({"words": sent["words"], "at": state.stamp()})
        if "tools_on" in sent:
            fresh["tools_on"] = sent["tools_on"]
        superseded.hear(data, job_id, sent["words"], state.parse_time(sent["at"]))
        return fresh

    fresh = state.update(name, None, sent_kept_write)
    return fresh


def unsent(args: argparse.Namespace, name: str, job: dict, held: list[dict], error: orca.OrcaError, note: dict | None = None) -> int:
    """Say that Orca took none of these held texts, which stay on the job under their request ids, marked said by state.told, for the watch to send once Orca reads the lane alive, or the job's next worker to carry in its handoff, its WENT line saying when each went, and that the restart note a resume gave reached no one and is not kept, since it was written for that resume's wake or restart alone."""
    count = "its text" if len(held) == 1 else f"its {len(held)} texts"
    keeps = held and (
        f"the job keeps {count} under the Orca request id each was held with, the watch sends them once Orca"
        f" reads its lane {orca.ON_MAC if state.on_mac(job) else 'on its Sprite'} alive, and its next worker gets them in its handoff;"
        f" the watch says when each goes, and that one has not by a time its words name once that time passes"
    )
    if held:
        def unsent_write(data: dict):
            state.told(data["jobs"][args.id], [kept["words"] for kept in held])

        state.update(name, None, unsent_write)
    unkept = note and "the watch's note to carry the job on reached no one and is not kept, since it was written for this wake or restart alone"
    line = f"{args.id}: {'held, not sent yet' if held else 'not sent'}: {error}; " + "; ".join(filter(None, [keeps, unkept]))
    return emit(args, line, {"id": args.id, "how": "held" if held else "unsent", "held": [kept["request"] for kept in held]})


def launched(name: str, job_id: str, brief_file: str | None, local: bool, text: str | None, died: str | None) -> dict:
    """Start a job's first lane, briefed as drive job brief writes it when none was, or one taking over from an agent on this Mac that stopped, returned while the job holds texts, or that another session started and ended with, with the text in its handoff when a resume brought one, and, by answers_since, each text the job holds under the heading naming its sender and each answer of his it has not heard under His answers since: whose words its brief lacks; any lane starts on this Mac when its brief declares a step only this Mac runs, by place.mac_step over that brief alone, whoever launches it and with no --local, never the report, held texts or text its handoff adds, and one crowded_here refuses at this Mac's cap holds the text and waits its turn on builder-slot; a lane on this Mac starts in the worktree orca.pr_holder finds holding the branch of the job's open PR, its handoff naming it. A stop that landed while it was briefed refuses it by unstopped, read again in the edit that stamps it launching, so no worker starts; once nothing refuses it the job is stamped as launching, which the watch reads as starting, never as not started or waiting on nothing, until record_start runs it on its worker's receipt; a takeover keeps its agent, dispatch, where, state, line and waits, the resume's text held by state.hold_text, until that receipt hands the job to the lane, so a launch that starts no worker leaves the job to its agent, whose next resume takes it over again, never a job reading never started that view.start_refusal holds on its own PR. This Mac's machine-wide caps hold a start by admission_refusal under place.admitting, the lock every drive here shares, so the count and the stamp that adds the lane are one step."""
    from drive import stage, view

    data = state.load(name)
    job = data["jobs"][job_id]
    kept = launch_brief(name, job_id, job)
    taker = other_sessions_agent(data, job)
    on_lane = {"agent": None, "where": "remote", "session": taker or job["session"]}
    if adopted := orca.adopted(kept):
        return {field: adopted[field] for field in LAUNCHED} | {"brief": str(kept)} | on_lane
    if refused := view.start_refusal(data, job_id, local):
        raise DriveError(refused)
    if not (brief_file or job.get("brief")):
        write_brief(name, job_id, {})
        data = state.load(name)
        job = data["jobs"][job_id]
    root = orca.checkout(name, data["roles"]["coordinator"]["cwd"])
    own = rebriefed(name, job_id) if (taker or died) and not brief_file else named_text(brief_file or job["brief"], "brief file")
    step = place.mac_step(own)
    briefed = answers_since(data, job_id, own)

    def handed(holder: orca.Holder | None = None) -> str:
        if not (taker or died):
            return briefed
        carried = [given["words"] for given in state.unreturned(job, "\n\n".join(filter(None, [briefed, text])))]
        return orca.agent_handoff(orca.brief_of(briefed), job, state.report_text(name, job_id, every=False), text, died, carried, stage.holds_now(data, job_id), holder)

    written = handed()
    local = local or bool(step)
    if holder := (orca.pr_holder(data, job, str(root)) if local else None):
        written = handed(holder)
    if refused := (
        brief.brief_refusal(name, data, job_id, written)
        or orca.uncarried(name, job_id, written)
        or (crowded_here(name, data, job_id, step, text) if local else None)
    ):
        raise DriveError(f"{job_id}: refused, not launched: {refused}")
    if taker or died:
        place.await_admission(name, job_id, "local" if local else "sprite")
    with place.admitting():
        if crowd := admission_refusal(name, data, job_id, local, step, bool(taker or died)):
            raise DriveError(f"{job_id}: not launched yet: {crowd}")
        def launched_write(data: dict):
            job = data["jobs"][job_id]
            unstopped(job_id, job)
            job.update(route=state.LOCAL_ROUTE if local else None, launching=state.stamp(), researched=brief.research_links(data, job, written))
            if not (taker or died):
                job.update(where="remote", waits=[wait for wait in view.holding(data, job["waits"], None) if wait not in state.TURN_WAITS], sprite_turn=None)
            elif text:
                state.hold_text(job, text, state.held_sender(data))
            return job

        job = state.update(name, None, launched_write)
        place.counted_again()
    kept.parent.mkdir(exist_ok=True)
    try:
        receipt = launch_attempt(name, job, kept.stem, kept, orca.vm_brief(name, job_id, job["kind"], written, job["route"]), root, holder and str(holder.path), None)
    except orca.Unstarted:
        record_start(name, job_id, None, None)
        raise
    return {field: receipt[field] for field in LAUNCHED} | {"brief": str(kept)} | on_lane | here_fields(receipt)


def admission_refusal(name: str, data: dict, job_id: str, local: bool, step: str | None, takes_over: bool) -> str | None:
    """Why a job's lane starts nowhere yet under this Mac's machine-wide caps, by place.admission, its work waiting its turn rather than failing: a first lane on a Sprite while the Sprite lanes every drive here runs fill their cap waits on sprite-slot, which the watch's FREE line ends as one frees, and a lane here of a job other than a builder, whose slot crowded_here gives, starts on a Sprite instead while this Mac is loaded, short of memory or at its local cap, unless its brief declares a step only this Mac runs; a takeover waits its turn before this, in launched, by place.await_admission."""
    if takes_over:
        return None
    if local:
        if data["jobs"][job_id]["kind"] == "builder" or step or not (why := place.admission(data, job_id, "local", state.now())):
            return None
        return f"{why}, so it starts on a Sprite: drive job launch {job_id}, with no --local"
    if not (why := place.admission(data, job_id, "sprite", state.now())):
        return None
    def admission_refusal_write(data: dict):
        place.queue_sprite(data["jobs"][job_id], state.now())
        return data

    data = state.update(name, None, admission_refusal_write)
    if job_id not in (line := place.sprite_line(data)):
        return f"{why}, so {job_id} waits on sprite-slot until its project's budget resets tomorrow, and the watch launches it then"
    return f"{why}, so {job_id} waits its turn for a Sprite lane, {line.index(job_id) + 1} of {len(line)} in line, and the watch launches it as one frees"


def answers_since(data: dict, job_id: str, written: str) -> str:
    """The brief a job's next worker or codex-ask run starts on: each text the job holds whose words the brief lacks under the heading state.held_since gives its sender, so his answers alone go under His answers since: and a coordinator's text under From the coordinator since:, and under His answers since: each answer of his it has not heard whose words the brief lacks, but one cc-notes marks superseded, by superseded.standing."""
    held = [kept for kept in data["jobs"][job_id]["undelivered"] if not superseded.carries(written, kept["words"])]
    carried = "\n".join([written, *(kept["words"] for kept in held)])
    owed = [
        {"sender": state.HIM, "words": f'"{kept["words"]}"' + (f" ({kept['link']})" if kept["link"] else "")}
        for _, kept in superseded.standing(data, superseded.owed(data, job_id))
        if not superseded.carries(carried, kept["words"])
    ]
    if since := state.held_since([*held, *owed]):
        return written.rstrip() + since + "\n"
    return written


def crowded_here(name: str, data: dict, job_id: str, step: str | None, text: str | None = None) -> str | None:
    """Why a builder's lane starts in no worktree on this Mac now, by place.crowded, the rule every builder start here goes through: one declaring no step only this Mac runs starts on a Sprite at once, unless its lane died twice since its last line, which starts no worker on a Sprite, and any other waits its turn for a slot on builder-slot, a first lane that no agent or lane runs and nothing else holds, and a move from its Sprite or a takeover from its agent on this Mac whatever its worker, waits or PRs, the text given held on the job beside those it holds for its handoff, its Sprite lane running on meanwhile, which the watch's turn starts itself with drive job launch <j> --local."""
    job = data["jobs"][job_id]
    if job["kind"] != "builder" or not (why := place.crowded(data, job_id, state.now())):
        return None
    if not step:
        return f"{why}, so it starts on a Sprite at once: drive job launch {job_id}, with no --local"
    moving = place.on_sprite(job) or bool(job.get("agent"))
    if not (moving or place.queueable(data, job_id)):
        return f"{why}, so this one waits: launch it here once a slot opens with no builder waiting its turn"
    def crowded_here_write(data: dict):
        if text:
            state.hold_text(data["jobs"][job_id], text, state.held_sender(data))
        place.queue(data["jobs"][job_id], state.now())
        return data

    data = state.update(name, None, crowded_here_write)
    move = "moves it here from its Sprite" if place.on_sprite(job) else f"hands it from its agent {job['agent']} to a lane here"
    turn = f"the watch {move} with drive job launch {job_id} --local as its turn comes, its handoff carrying the texts the job holds" if moving else "the watch's FREE line names it as a slot opens"
    return f"{why}, so {job_id} waits its turn for a builder slot on this Mac, {place.positions(data)[job_id]}, and {turn}"


def launch_brief(name: str, job_id: str, job: dict) -> Path:
    """The file the job's next start keeps its brief in, whose name is the launcher's attempt: numbered after the lanes the job recorded dying, and past every attempt whose kept receipt names a dispatch the job recorded, since the launcher gives a brief one attempt and Orca may have dropped that dispatch; an attempt the drive never recorded whose worker-start may still have started a worker, or whose Sprite's worker-start sent nothing, is used again, so adopted records it or the launcher retries that Sprite; an attempt the launcher ran and failed before any worker started is passed over for a fresh worker id, its own terminal closed, so no launch reuses the terminal and worktree a failed one kept."""
    recorded = {job.get("dispatch"), *(death["dispatch"] for death in job.get("deaths", []))} - {None}
    first = len(job.get("deaths", [])) + 1
    number = first
    while True:
        kept = state.drive_dir(name) / "jobs" / (f"{job_id}-{number}.md" if number > 1 else f"{job_id}.md")
        number += 1
        if (orca.kept_start(kept) or {}).get("dispatchId") in recorded:
            continue
        if not orca.failed_attempt(kept):
            return kept
        if (left := orca.attempt_terminal(kept)) and (refused := orca.close_pane(left)):
            state.said(job_id, f"its failed attempt {kept.stem}'s terminal {left} stays open: {refused}")


def record_start(name: str, job_id: str, started: dict | None, handoff: str | None, text: str | None = None) -> dict:
    """Record a started worker's receipt on its job, the job on that worker's Orca lane in place of any agent a takeover started it from, its turn open until the watch takes that worker's worker_done or escalation, with the handoff a relaunch started it on, else the launch's brief, the Sprite its start put the tools on, the cc-remote config that Sprite was made from, none of the texts it held or queued in its last worker's mailbox, which that handoff or brief carries, each of those and the text given kept among the texts its workers were given beside those given since the job's last return, so a handoff taking over from this worker before the job's next return carries them, the question its last worker asked handed on by state.start, and the job sent each answer of his whose words that handoff or brief holds, or with no receipt clear the launching mark of a launch whose worker never started; a worker its job already records changes nothing."""
    def record_start_write(data: dict):
        job = data["jobs"][job_id]
        if started is None:
            job["launching"] = None
        elif overtaken(data, job):
            job.update(started)
        else:
            fresh = state.handed_texts(job, text)
            given = [*state.unreturned(job, "\n\n".join(fresh)), *({"words": words, "at": state.stamp()} for words in fresh)]
            state.went(name, data, job_id, job["undelivered"], f"in the {'handoff' if handoff else 'brief'} of its worker started {as_worker(started)}")
            job.update(started, handoff=handoff, given=given, undelivered=[], queued=[], report=None, tools_on=started["workspace"], turn_open=not started.get("bg"))
            state.start(job)
            superseded.hear_start(data, job_id)
        return job

    job = state.update(name, None, record_start_write)
    return job


def started_here(name: str, job_id: str, kept: Path, text: str, cwd: Path) -> dict:
    """Start a job's lane on this Mac as its own `claude --bg` session on the brief or handoff text, kept in the launch's brief file, in cwd, as drive job start starts a builder or reader, never as an Orca worker, so Orca holds no Run, terminal or worktree for work on this Mac, on the job's own model when its row names one, as the watch's second restart after a model's safeguards stopped it sets, past the model agents.shaped keeps or drops; a receipt naming no Orca lane and the bg entry the job keeps. Unstarted when no session holds the job's order or `claude --bg` starts none."""
    kept.write_text(text)
    data = state.load(name)
    job = data["jobs"][job_id]
    _, held = state.holding_role(data, data["orders"][job["order"]])
    if not held.get("session"):
        raise orca.Unstarted(f"{kept.stem}: no worker started, since no session holds {job['order']}, so none would hear its agent")
    call = agents.shaped({"session_id": held["session"], "tool_input": {"subagent_type": state.JOB_AGENTS[job["kind"]][0], "prompt": f"Read your brief first: {kept}"}}) | launch_model(job)
    bg = {"agent": uuid.uuid4().hex[:17], "session": None, "started": state.stamp(), "cwd": str(cwd), "pointed": []}
    try:
        bg["session"] = agents.start_job(name, job_id, bg["agent"], call, held, cwd)
    except (reads.ReadError, state.DriveError) as error:
        raise orca.Unstarted(f"{kept.stem}: no worker started, since claude --bg started no agent: {error}") from None
    return dict.fromkeys(LAUNCHED) | {"resumed": None, "bg": bg}


def here_fields(receipt: dict) -> dict:
    """The job row fields a lane started on this Mac as its own `claude --bg` session sets: its bg entry and that entry's agent as the job's, its launch over, so state.own_session names the session from the start and drive job resume reaches it by `claude --bg --resume` whether or not a hook of it ever binds the job."""
    return {"where": "local", "route": None, "bg": receipt["bg"], "agent": receipt["bg"]["agent"], "launching": None} if receipt.get("bg") else {}


def as_worker(started: dict) -> str:
    return f"as dispatch {started['dispatch']}" if started["dispatch"] else f"as its own claude --bg session {started['bg']['session']}"


def overtaken(data: dict, job: dict) -> bool:
    """Whether a wait or stop set while a job's first lane launched holds it, any wait that holds being new since that launch refuses a job one holds, so that lane's worker must not run."""
    from drive import view

    stopped = job["state"] == "stopped" and state.parse_time(job["updated"]) >= state.parse_time(job["launching"])
    return job["said"] == state.NOT_STARTED and (stopped or any(wait != state.SLOT_WAIT for wait in view.holding(data, job["waits"], None)))


def launch_attempt(name: str, job: dict, attempt: str, kept: Path, brief: str, root: Path, workspace: str | None, session: str | None, transcript: Path | None = None) -> dict:
    """Launch one attempt of a job's lane on a Run the drive's own terminal makes, since Orca binds a Run only to the terminal that made it: on a fresh Sprite, or on the workspace's own Sprite in the checkout it holds, there resuming the Claude session given, or on a fresh Sprite resuming the session given from the transcript given, which its prepare places there; a fresh Sprite's receipt records the SHA-256 of the cc-remote config the checkout holds, which it is made from, and one on the workspace's own Sprite keeps the job's; a try whose worker-start sent nothing reuses the Sprite it prepared, a Run Orca cannot make starts nothing, and the launcher copies this brief onto it, the lane running on the job's own model when its row names one, else orca.MODEL. An attempt on a fresh Sprite that started no worker, since its prepare failed or its Sprite refused the tools a worker needs, launches once more, in a fresh Claude session, the launcher preparing another Sprite or retrying worker-start on the one it prepared; Unstarted when that one starts none either, or when the workspace's own Sprite or the Mac does."""
    if state.on_mac(job):
        return started_here(name, attempt.partition("-")[0], kept, orca.brief_of(brief), Path(workspace) if workspace else root)
    kept.write_text(brief)
    try:
        run = orca.new_run(name, f"drive {name} {attempt}: {job['goal']}")
    except orca.OrcaError as error:
        raise orca.Unstarted(f"{attempt}: no worker started, since Orca made it no Run: {error}") from None
    lane = orca.lane_name(name, attempt)
    made = None if state.on_mac(job) else job.get("sprite_config") if workspace else orca.sprite_config(root)
    try:
        receipt = orca.launch(name, lane, kept, root, run, job["route"], workspace, session, transcript, **launch_model(job)) | {"resumed": session}
    except orca.Unstarted:
        if workspace or state.on_mac(job):
            raise
        receipt = orca.launch(name, lane, kept, root, run, job["route"], None, None, **launch_model(job)) | {"resumed": None}
    return {**receipt, "run": run, "sprite_config": made}


def launch_model(job: dict) -> dict:
    """The model a job's lane launches on when its row names one, as the watch's second restart after a model's safeguards stopped it sets, for orca.launch, which runs orca.MODEL on none."""
    return {"model": job["model"]} if job["model"] else {}


@orca.rides_restarts()
def bound(args: argparse.Namespace) -> int:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        done = orca.bound_command(state.resolve(args.drive), args.lane_run, command)
    except orca.Unstarted as error:
        print(f"drive: {error}", file=sys.stderr)
        return orca.UNSTARTED
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    return done.returncode


def drain_read(args: argparse.Namespace) -> int:
    """Mark read the unread mail of every Run Orca's mailbox still shows unread, by orca.mark_read, which binds a Run the drive's terminal does not hold to its binder terminal under the binding lock, so mail the watch took long ago on a Run no new mail reaches stops holding Orca's database and the Run retires: how many messages it marked on how many Runs, how many Runs Orca no longer keeps, and each Run whose mark Orca refused, with its reason."""
    name = state.resolve(args.drive)
    orca.speak_as_drive(name)
    runs, marked, gone, refused = orca.unread_runs(), 0, 0, []
    for run in runs:
        try:
            count = orca.mark_read(name, run)
        except orca.OrcaError as error:
            refused.append(f"{run}: {error}")
            continue
        if count is None:
            gone += 1
        else:
            marked += count
    said = "\n".join([f"marked {marked} messages read on {len(runs) - gone - len(refused)} of {len(runs)} Runs with unread mail; {gone} Runs Orca no longer keeps; {len(refused)} refused", *refused])
    return emit(args, said, {"runs": len(runs), "marked": marked, "gone": gone, "refused": refused})


def started_job(data: dict, job_id: str) -> dict:
    job = remote_job(data, job_id)
    if not job.get("dispatch"):
        raise DriveError(f"{job_id} has not started; launch it with: drive job launch {job_id} <brief>")
    return job


def held_job(data: dict, job_id: str) -> dict:
    """The job a verb writes the brief of, or starts, wakes or messages the worker of, refused before that verb writes, holds, sends or starts anything when a session of the drive that does not hold the job's order calls it, the top coordinator and the meta session included, by state.job_held_elsewhere, the check the Agent hook makes, so no worker starts whose result goes to a session that never asked for it; a job starting a production cut is the top coordinator's on any order, a call in no session the drive binds, such as a verb the watch detached or a Sprite lane's, is refused nothing, and the session that started the agent running the job on this Mac keeps it."""
    job = find(data["jobs"], job_id, "job")
    if refused := state.job_held_elsewhere(data, job, os.environ.get("CLAUDE_CODE_SESSION_ID")):
        raise DriveError(f"{job_id}: refused, nothing written, sent or started: {refused}")
    return job


def hand_over(args: argparse.Namespace, name: str, data: dict, session: str) -> int:
    """Hand a job whose Agent ended with the session that started it, which no role holds any more, to a fresh agent of this session: a handoff brief carrying the job's brief, what that agent left, each text the job holds by answers_since and the text, whose `Read your brief first:` line is the fresh agent's whole prompt, and which the drive records that agent on."""
    from drive import stage

    job = data["jobs"][args.id]
    if not job.get("brief"):
        raise DriveError(f"{args.id} has no brief to hand over: write it with drive job brief {args.id}, then resume it again")
    kept = state.drive_dir(name) / "jobs" / f"{args.id}.handoff.md"
    briefed = answers_since(data, args.id, rebriefed(name, args.id))
    carried = [given["words"] for given in state.unreturned(job, "\n\n".join(filter(None, [briefed, args.text])))]
    kept.write_text(orca.agent_handoff(briefed, job, state.report_text(name, args.id, every=False), args.text, None, carried, stage.holds_now(data, args.id)))
    def hand_over_write(data: dict):
        data["jobs"][args.id].update(handoff=str(kept), updated=state.stamp())

    state.update(name, None, hand_over_write)
    line = (
        f"Read your brief first: {kept}\n{args.id} ran as agent {job['agent']} of session {job['session']}, which ended with that session:"
        f" start a fresh {job['kind']} agent here on the line above as its whole prompt, and the drive records {args.id} as this session's"
    )
    return emit(args, line, {"id": args.id, "handoff": str(kept), "handed_to": session})


def restart(args: argparse.Namespace, name: str, dispatch: str, end: orca.LaneEnd, text: str | None, here: bool = False, declared: str | None = None) -> int:
    """Start the job's next worker with a handoff, carrying the text when a resume brought one: for a worker that finished on this Mac, through the launcher in a fresh terminal and Claude session in the worktree it finished in, or the one orca.pr_holder finds holding the branch of the job's open PR, on a Run this terminal makes, since a worker started in the finished one's terminal runs on in the Claude session every earlier worker there filled, which can sit past its start's timeout without ever starting, and in a fresh worktree when that start starts no worker in a worktree that holds no PR branch; for one that finished on a Sprite that answers with the Claude session it ran there, that session resumed there by resume_session, keeping its whole conversation, a resume that starts no worker falling back as below with its why naming the refusal; else for one that finished on a Sprite, on that Sprite again in the checkout it left, on a Run this terminal makes, through the launcher's cc-remote orca prepare --existing, which gives it the tool payload and keys a fresh Sprite gets, since a worker started in the finished one's terminal runs on in the claude process and environment that Sprite's first prepare made, unless that Sprite faulted, refuses the tools a worker needs or was made from a cc-remote config other than the one the default branch holds now; else, on a Run this terminal makes, on a fresh Sprite, or on this Mac in the worktree orca.pr_holder finds holding the branch of the job's open PR, which git lets no fresh worktree check out, else in a fresh worktree. A lane's Sprite, whether its worker died or finished, unless a finished one's job moves to this Mac, first has its Orca runtime and worker logs copied to the drive's folder by orca.keep_logs, which the watch's LANE died line names; when they show that Sprite faulted, a kernel line at error level or worse after init or a full home volume, as when an nbd0 I/O error remounts its root disk read-only, no session resumes and no worker continues there, as on a Sprite that refused a worker's tools: the lane is saved and relaunched on a fresh Sprite, its why naming what the logs showed. A lane that died while its job ran keeps on its death the cause line keep_logs reads, which its next worker's brief and the line say, unless its Sprite failed on its own, a kernel error line after init with no OOM kill and its home volume not full, or did not answer while GitHub holds the job's branch, by one read, when that line goes in its why and the death counts against no job; when another lane of the job died with a cause since its last line, a second death with a cause starts no worker on a Sprite: its session resumes there first only when that Sprite's VM booted after its worker started, by orca.rebooted, and otherwise it moves to a fresh worktree on this Mac as a builder whose brief declares a step only this Mac runs does, crowded_here holding a builder waiting its turn on builder-slot while this Mac's slots are full, for the watch's turn to move it with drive job launch <j> --local, and its line names both deaths. A Sprite that answers with the Claude session the dead lane's worker ran there resumes that session by resume_session, with nothing saved, moved or destroyed; a resume that starts no worker falls back to a relaunch, its why naming the refusal, on a fresh Sprite when that one refused a worker's tools. Otherwise every Claude Code transcript the lane's Sprite holds is first copied whole to the drive's folder by orca.keep_transcripts through the Sprites file API, whether or not that Sprite answers ssh, kept on the lane's death. Before a relaunch it saves to GitHub what the dead lane's Sprite holds that a fresh one would not get, by lane_saved, and its line and handoff name each branch GitHub then holds under saved/<the dead lane>/, by one read, never the text any save printed, or, when GitHub holds none, what that save found: what it saved, that the Sprite held nothing unpushed or uncommitted, or that cc-remote records no such Sprite, so nothing on it was read or saved; when the save fails, the relaunch runs on that Sprite instead, in the checkout the dead lane left, unless it refused a worker's tools or started no worker, as a Sprite that will not prepare does, when it is kept and the relaunch runs on a fresh one. A Sprite whose logs keep_logs could not read, as one whose ssh gets no answer, is asked nothing more over ssh and kept: no session resumes and no save is tried there, its transcripts still copied through the Sprites file API, and the lane relaunches on a fresh Sprite, since every later step over its ssh, the launcher's prepare --existing included, waits on that same silence. A lane Orca could not reach, whose Sprite lane_end woke and that then answered, relaunches on that Sprite in the checkout it left with no save when that Sprite holds no session of it, and on a fresh one as a dead lane's when its session there or that Sprite starts no worker. The end each of those steps comes to and what the save found are kept on the lane's death by orca.died, which lane_end reads, so no step runs twice for one death. A relaunch on a fresh Sprite resumes the Claude session the lane ran, from the transcript of it keep_transcripts or an end kept here, by orca.kept_session, prepare placing it on that Sprite first, and its brief says the checkout is fresh; a launch that starts no worker that way starts one in a fresh session. A relaunch on a fresh Sprite then destroys the dead lane's Sprite once what it holds on no remote is saved. A lane a `drive job end` ended, which lane_end reads from the end its job records, relaunches on a fresh Sprite, or in a fresh worktree on this Mac for a lane there, reading, saving and resuming nothing on its Sprite, since that end kept its transcripts and saved what it held on no remote before it destroyed it; its line and handoff say the lane ended and name what that save found. A relaunch an earlier restart of the same death started and never recorded is recorded from the ready receipt its attempt keeps, never launched twice. Here, which a launch with --local of a job on a Sprite asks, as does the step only this Mac runs declared, which its caller read by declared_step before sending or starting anything, moves the job to a fresh worktree on this Mac whether its worker finished, died, does not answer, returned its line and still takes messages or still works, a builder at this Mac's cap waiting its turn on builder-slot by crowded_here instead, and one that builds nothing moving at once; a worker that still takes messages is stopped by orca.reclaim, as drive job end stops one, once its transcripts are kept and before what its Sprite holds on no remote is saved; when that save fails it still moves, the Sprite kept rather than destroyed, its lane recorded dead so nothing of it is read or sent again, and the line and handoff name what every lane of the job saved on GitHub, so the next worker starts from the job's pushed and saved branches. The handoff carries every text a resume holds on the job, oldest first, then the text given, as the messages it never read, naming no sender, since the start it records clears what the job holds. Before each step that reads, saves or starts anything on the lane's Sprite, it stands down by yield_to_launch while a drive job launch --local waits for the job's lock, and each step that waits on that Sprite says itself and its bound as it starts, by state.said. Every relaunch but one here first waits its turn under this Mac's caps, by place.await_admission, a Sprite lane's on the Sprite cap and a lane on this Mac's on the local cap and load gate, in this process, so the work waits rather than fails."""
    from drive import stage

    yield_to_launch(name, args.id)
    data = state.load(name)
    job = data["jobs"][args.id]
    message = "\n\n".join(state.handed_texts(job, text)) or None
    here = here or declared is not None
    if not here:
        place.await_admission(name, args.id, "local" if state.on_mac(job) else "sprite")
    if here and (crowd := crowded_here(name, data, args.id, declared, text)):
        raise DriveError(f"{args.id}: refused, no worker started: {crowd}")
    read = fault = own = None
    answered = True
    if not (end.live or end.ended or state.on_mac(job) or (end.worktree and here)):
        read, fault, own, answered = orca.keep_logs(name, args.id, job, dispatch)
    if not answered and (branch := end.branch or job["branch"]):
        own = all(github.branch_heads([(job.get("repo") or github.checkout_repo(data["roles"]["coordinator"]["cwd"]), each) for each in branch.split(", ")]).values())
    cause = read if job["state"] == "running" and not (end.worktree or own) else None
    first = state.died_before(job, dispatch) if cause and not here else None
    if fault or not answered:
        end = orca.LaneEnd(end.why if cause else f"{end.why}, and {read}", end.branch, None, unfit=True)
    finished = end.worktree and not here
    same = finished and state.on_mac(job)
    unreached = end.unreached and not (here or end.unfit)
    root = orca.checkout(name, data["roles"]["coordinator"]["cwd"])
    if finished and not same and (made := job.get("sprite_config")) != (now := orca.sprite_config(root)):
        was = f"the cc-remote config with SHA-256 {made[:12]}" if made else "a cc-remote config the drive never recorded"
        stale = f"its Sprite {job['workspace']} was made from {was}, and the one the default branch holds now has SHA-256 {now[:12]}"
        return restart(args, name, dispatch, orca.LaneEnd(f"{end.why}, and {stale}", end.branch, None, unfit=True), text)
    death = state.lane_death(job, "dispatch") or {}
    left = death.get("unsaved")
    if (death.get("restored") or end.ended and death.get("ended", {}).get("kept")) and not here and "unresumed" not in death and orca.holds_sprite(job) and (session := orca.session_of(name, args.id, job["workspace"], dispatch)):
        yield_to_launch(name, args.id)
        try:
            return resume_session(args, name, dispatch, end, message, text, session, root, cause)
        except orca.Unstarted as error:
            unresumed = end._replace(why=f"{end.why}, and its Claude session {session} did not resume on the Sprite kept for it: {error}")
            return restart(args, name, dispatch, orca.died(name, args.id, dispatch, unresumed, unresumed=str(error)), text)
    if finished and not same and "unresumed" not in death and (session := orca.session_of(name, args.id, job["workspace"], dispatch)):
        yield_to_launch(name, args.id)
        try:
            return resume_session(args, name, dispatch, end, message, text, session, root, cause)
        except orca.Unstarted as error:
            unfit = isinstance(error, orca.Unfit)
            unresumed = end._replace(why=f"{end.why}, and its Claude session {session} did not resume there: {error}", worktree=None if unfit else end.worktree, unfit=unfit)
            return restart(args, name, dispatch, orca.died(name, args.id, dispatch, unresumed, unresumed=str(error)), text)
    if not (end.worktree or end.live or end.ended or state.on_mac(job)):
        yield_to_launch(name, args.id)
        if not (here or end.unfit or "unresumed" in death or first and not orca.rebooted(name, args.id, job, dispatch)) and (session := orca.session_of(name, args.id, job["workspace"], dispatch)):
            yield_to_launch(name, args.id)
            try:
                return resume_session(args, name, dispatch, end, message, text, session, root, cause)
            except orca.Unstarted as error:
                end = orca.died(name, args.id, dispatch, end._replace(why=f"{end.why}, and its Claude session {session} did not resume there: {error}", unfit=isinstance(error, orca.Unfit) or end.unreached), unresumed=str(error))
    yield_to_launch(name, args.id)
    transcript = None if state.on_mac(job) or end.ended else orca.keep_transcripts(name, args.id, job, dispatch, root)
    stopped = orca.reclaim(job["run"], dispatch, root) if here and end.live else None
    yield_to_launch(name, args.id)
    saving = not (finished or unreached or end.ended or left)
    try:
        saved = lane_saved(name, args.id, job, root) if saving else None
    except orca.OrcaError as error:
        saved, left = None, str(error)
    if saving:
        orca.death_kept(name, args.id, dispatch, end.why, {"saved": saved, "unsaved": left})
    unsaved = left if here else None
    branches = [] if finished or unreached else orca.saved(name, args.id, job, root, every=bool(unsaved))
    continued = None
    if unreached:
        continued = "Orca could not reach that Sprite"
    elif left and not (here or end.unfit):
        continued = f"what that Sprite holds on no remote could not be saved to GitHub: {left}"
    route = state.LOCAL_ROUTE if here else job["route"]
    holder = None if route != state.LOCAL_ROUTE else orca.pr_holder(data, job, str(root))
    own = str(holder.path) if holder else orca.worktree_path(end.worktree) if same else job["workspace"] if finished or continued else None
    yield_to_launch(name, args.id)
    def restart_write(data: dict):
        job = data["jobs"][args.id]
        state.lane_died(job, dispatch, end.why, state.now(), cause)
        if transcript:
            job["deaths"][-1]["transcript"] = str(transcript)
        data["jobs"][args.id] = job | {"route": route}
        return data, job

    data, job = state.update(name, None, restart_write)
    ended = job["deaths"][-1]
    kept = launch_brief(name, args.id, job)
    attempt = kept.stem
    kept.parent.mkdir(exist_ok=True)
    briefed = rebriefed(name, args.id)
    carried = [given["words"] for given in state.unreturned(job, "\n\n".join(filter(None, [briefed, message])))]
    handoff = orca.handoff(briefed, ended, branches, job, state.report_text(name, args.id, every=False), message, end, continued, carried, stage.holds_now(data, args.id), here, unsaved, holder)
    resumes = None if own or route == state.LOCAL_ROUTE else orca.kept_session(name, args.id, dispatch)
    brief = orca.vm_brief(name, args.id, job["kind"], f"{orca.FRESH_RESUME}\n\n{handoff}" if resumes else handoff, route)
    was = orca.place(job)
    try:
        receipt = orca.adopted(kept) or launch_attempt(name, job | {"route": route}, attempt, kept, brief, root, own, resumes and resumes.stem, resumes)
    except orca.Unstarted as error:
        if not own or holder:
            raise
        return restart(args, name, dispatch, orca.died(name, args.id, dispatch, orca.LaneEnd(f"{end.why}, and {error}", end.branch, None, unfit=True)), text)
    started = {field: receipt[field] for field in LAUNCHED} | here_fields(receipt)
    record_start(name, args.id, started, str(kept), text)
    carrying ="in a handoff carrying its brief, its branch, its last line, its report and its PRs"
    local = here or state.on_mac(job)
    went = "the message went to the job's next worker" if message is not None else "the job's next worker"
    if same:
        line = (
            f"{args.id}: {went}, started in a fresh terminal and Claude session in its worktree {own} {orca.ON_MAC}"
            f" {as_worker(started)} {carrying}; its lane {was} had finished ({end.why})"
        )
        if end.terminal and end.terminal != started["terminal"]:
            refused = orca.close_pane(end.terminal)
            line += f"; its finished worker's terminal {end.terminal} " + (f"stays open, since Orca refused its close: {refused}" if refused else "is closed, its transcript kept")
    else:
        source = "its worktree" if state.on_mac(job) else ended["workspace"]
        where = f"in the worktree {holder.path} {orca.ON_MAC}, which holds its PR branch {holder.branch}," if holder else f"in a fresh worktree {orca.ON_MAC}" if local else orca.place({**job, **started})
        line = (
            f"{args.id}: {went}, relaunched {where}"
            f" {as_worker(started)} {carrying}; its lane {was} {'was stopped' if stopped is not None else 'had finished' if end.worktree or end.live else 'had ended' if end.ended else 'was dead'} ({state.why_died(end.why, cause)}), and "
            + (
                f"it continues in the checkout it left on {source}, which cc-remote prepared again with the tools and keys a fresh Sprite gets"
                if finished
                else "it continues there as that worktree stands, uncommitted edits included"
                if holder and not here
                else f"{source} is kept, since what it holds on no remote could not be saved: {unsaved}"
                + (f"; the job's lanes saved on GitHub {', '.join(branches)}" if branches else "")
                if unsaved
                else f"what {source} held on no remote is saved on GitHub as {', '.join(branches)}"
                if branches
                else f"it continues in the checkout the dead lane left on {source}, since {continued}"
                if continued
                else f"{source} is kept, since what it holds on no remote could not be saved: {left}"
                if left
                else ended.get("saved") or f"work not pushed from {source} is not carried over"
            )
        )
        if resumed := receipt.get("resumed"):
            line += f"; it resumed its own Claude session {resumed} there from the transcript kept on this Mac"
        if not left and started["workspace"] != ended["workspace"] and (released := release_sprite(name, args.id, {**job, "workspace": ended["workspace"]}, root)):
            line += f"; {released}"
        if declared:
            line += f"; it moved to this Mac, since its brief declares {declared}, a step only this Mac runs"
    how = f"started {orca.ON_MAC}" if same else "relaunched"
    return emit(args, "\n".join(filter(None, [stopped, line])), {"id": args.id, "how": how, "ended": ended, "branches": branches, "handoff": str(kept), **started})


def resume_session(args: argparse.Namespace, name: str, dispatch: str, end: orca.LaneEnd, message: str | None, text: str | None, session: str, root: Path, cause: str | None) -> int:
    """Resume the Claude session of the job's lane that died on its Sprite, or that finished its turn there before a message Orca sends no finished worker came, on that Sprite in the checkout it left, on a Run this terminal makes, so the worker keeps its whole conversation and nothing is saved, moved or destroyed; its death keeps the cause line read off its Sprite, and its brief and line say why it died with that cause and carry the message, when one came. A resume an earlier restart of the same death started and never recorded is recorded from its kept receipt. Unstarted when no worker started."""
    from drive import stage

    def resume_session_write(data: dict):
        job = data["jobs"][args.id]
        state.lane_died(job, dispatch, end.why, state.now(), cause)
        return data, job

    data, job = state.update(name, None, resume_session_write)
    ended = job["deaths"][-1]
    attempt = f"{args.id}-{len(job['deaths']) + 1}"
    kept = state.drive_dir(name) / "jobs" / f"{attempt}.resume.md"
    kept.parent.mkdir(exist_ok=True)
    receipt = orca.adopted(kept) or launch_attempt(name, job, attempt, kept, orca.resumed(ended, message, stage.holds_now(data, args.id), bool(end.worktree or end.ended)), root, ended["workspace"], session)
    started = {field: receipt[field] for field in LAUNCHED}
    record_start(name, args.id, started, str(kept), text)
    went = "the message went to the job's worker, which" if message is not None else "the job's worker"
    line = f"{args.id}: {went} resumed its own Claude session {session} on Sprite {ended['workspace']} as dispatch {started['dispatch']}, in the checkout its lane left there {"finished" if end.worktree or end.ended else "dead"} ({state.why_died(end.why, cause)})"
    return emit(args, line, {"id": args.id, "how": "resumed", "ended": ended, "handoff": str(kept), "session": session, **started})


def job_read(args: argparse.Namespace) -> int:
    """Answer one question a remote lane asked, as the watch's reader."""
    name = state.resolve(args.drive)
    said, why = watch.answer_read(name, args.id, args.message, state.now())
    return emit(args, why or said, {"id": args.id, "message": args.message, "answer": said, "why": why})


def job_replies(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    job = find(state.load(name)["jobs"], args.id, "job")
    kept = state.replies(name, args.id)
    said = [f"{reply['created_at']} {reply['type']}: {reply['body'] or reply['subject']}" for reply in kept]
    if woke := state.resumed(job):
        said.append(f"{woke} resumed: a message woke its agent {job['agent']} after its last return, and it runs on toward its next line")
    return emit(args, "\n\n".join(said) or f"{args.id} has sent nothing yet", kept)


def slot_claim(args: argparse.Namespace) -> int:
    """Claim a staging env's slot for a job: it holds the slot now when none does, its lease ending its minutes past the staging-claim rule's wait, else it queues last, first in first out, and the watch hands it the slot with a GO once the holder gives it back, finishes or outlives its lease, by state.slot_turns."""
    name = state.resolve(args.drive)
    prs = [state.pr_key(pr) for pr in args.pr]
    paths, stacks = watch.pass_reads(name, state.load(name), prs)
    def slot_claim_write(data: dict):
        find(data["jobs"], args.job, "job")
        line = state.slot_claim(data, args.env, args.job, args.minutes, prs, args.cases, state.now(), paths, stacks, release=providers.of(data).release)
        return line

    line = state.update(name, None, slot_claim_write)
    return emit(args, line, {"env": args.env, "job": args.job, "line": line})


def slot_hand(args: argparse.Namespace) -> int:
    """Hand a staging env's slot to a job in its queue now, the cut holding it going back to the head of the queue, from the holder's own session or the top coordinator's, and start the job's turn as the watch's hand-on does: its release for a start of the top coordinator's, else a GO by drive job resume."""
    name = state.resolve(args.drive)
    data = state.load(name)
    held = (data["slots"].get(args.env) or {}).get("holder")
    own = (os.environ.get(state.JOB_ENV) or "").split("/")[1:2]
    if own and held and own[0] not in state.slot_members(data["slots"][args.env]) or not own and not state.top_coordinator(data, os.environ.get("CLAUDE_CODE_SESSION_ID")):
        raise DriveError(f"refused: only the job holding the {args.env} slot or the top coordinator hands it on")
    before = [member for member in [held, *(held["riders"] if held else [])] if member and not member["starts"]]
    def slot_hand_write(data: dict):
        entry, line = state.slot_hand(data, args.env, args.to, state.now())
        until = data["slots"][args.env]["holder"]["until"]
        steps = state.SLOT_GO_STEPS.format(env=args.env, job=args.to) + state.holding(providers.of(data).release, data, [args.to])
        stopped = [member["job"] for member in before if data["jobs"][member["job"]]["state"] not in state.SLOT_LIVE_END]
        return entry, line, steps, stopped, until

    entry, line, steps, stopped, until = state.update(name, None, slot_hand_write)
    for job_id in stopped:
        state.detach(name, ["job", "resume", job_id, state.SLOT_STOP.format(env=args.env, to=args.to)], watch.RELAUNCHES_LOG)
    command = ["job", "release", args.to] if entry["starts"] else ["job", "resume", args.to, f"GO staging {args.env} until {until}: {steps}"]
    state.detach(name, command, watch.RELAUNCHES_LOG)
    told = f"; {', '.join(stopped)} told to STOP" if stopped else ""
    return emit(args, f"{line}{told}; drive {' '.join(command[:3])} runs now", {"env": args.env, "job": args.to, "line": line, "stopped": stopped})


def mac_box_handed(args: argparse.Namespace) -> int:
    """Take the drive pool's word that it handed a job a Mac box, by tools/ci mac-box lend or up naming the job as holder: each mac-box:<box> or mac-box:any wait of the job ends, so the watch's next pass frees it with its FREE line, and the hand is kept as an event; refused for a job that waits on no box."""
    name = state.resolve(args.drive)

    def handed(data: dict) -> list[str]:
        job = find(data["jobs"], args.job, "job")
        if not (marked := state.mac_box_handed(data, args.job, args.box)):
            raise DriveError(f"{args.job}: refused, nothing written: it waits on no Mac box ({', '.join(job['waits']) or 'no waits'}), so a WAITING naming mac-box:<box> or mac-box:any is what this frees")
        return marked

    marked = state.update(name, None, handed)
    state.append_event(name, "mac_box_handed", job=args.job, box=args.box, host=args.host, ended=marked)
    line = f"{args.job} has Mac box {args.box}{f' on {args.host}' if args.host else ''}: {', '.join(marked)} ended, so the watch frees it on its next pass"
    return emit(args, line, {"job": args.job, "box": args.box, "ended": marked})


def slot_hold(args: argparse.Namespace) -> int:
    """Hold a staging env's slot for one job until a time, from the top coordinator's session, so the watch gives it to no other job and hands a lease that ends to the one it names; --until with a past time is refused, and --clear drops the hold."""
    name = state.resolve(args.drive)
    if not state.top_coordinator(state.load(name), os.environ.get("CLAUDE_CODE_SESSION_ID")):
        raise DriveError(f"refused: only the top coordinator holds the {args.env} slot")
    def slot_held(data: dict) -> str:
        if args.clear:
            (data["slots"].get(args.env) or {})["hold"] = None
            line = f"{args.env} holds for no one now"
        else:
            if args.held not in data["jobs"]:
                raise DriveError(f"{args.held}: refused, no job of this drive: a hold names one job, never an order")
            line = state.slot_hold(data, args.env, args.held, state.parse_time(args.until), args.reason, state.now())
        return "; ".join([line, *(state.slot_take(data, args.env, job_id) for job_id in args.take)])

    line = state.update(name, None, slot_held)
    for job_id in args.take:
        state.detach(name, ["job", "resume", job_id, state.SLOT_TAKEN.format(env=args.env)], watch.RELAUNCHES_LOG)
    return emit(args, line + "".join(f"; {job_id} told NO-START" for job_id in args.take), {"env": args.env, "line": line, "taken": args.take})


def slot_release(args: argparse.Namespace) -> int:
    """Give a staging env's slot back, or leave its queue, so the watch's next pass hands it to the job first in line."""
    name = state.resolve(args.drive)
    def slot_release_write(data: dict):
        line = state.slot_release(data, args.env, args.job)
        return line

    line = state.update(name, None, slot_release_write)
    return emit(args, line, {"env": args.env, "job": args.job, "line": line})


def work_enable(args: argparse.Namespace) -> int:
    """Turn a kind of work over to the queue on this drive, or with --off back to its own jobs: its items no agent took start no job of their own, and the watch's spawner starts a worker that pulls them in groups."""
    name = state.resolve(args.drive)

    def work_enable_write(data: dict) -> list[str]:
        kinds = data.setdefault("work", {"kinds": []})["kinds"]
        data["work"]["kinds"] = [kind for kind in kinds if kind != args.kind] if args.off else list(dict.fromkeys([*kinds, args.kind]))
        return data["work"]["kinds"]

    kinds = state.update(name, None, work_enable_write)
    return emit(args, f"{name}: the queue runs {', '.join(kinds) or 'no kind of work'}", {"kinds": kinds})


def work_pull(args: argparse.Namespace) -> int:
    """Claim a worker's next group of items, printing them with the steps their cut takes, or NONE or HOLD when it has none."""
    line = work.pulled(state.resolve(args.drive), args.worker)
    return emit(args, line, {"worker": args.worker, "line": line})


def work_done(args: argparse.Namespace) -> int:
    """Take one item's whole return from stdin, as drive job report takes a job's, for the worker holding its claim."""
    whole = sys.stdin.read()
    if not whole.strip():
        raise DriveError(f"stdin held no return for {args.item}; pipe its whole return in: printf '%s\\n' '<line>' ... | drive work done {args.worker} {args.item} -")
    return emit(args, *work.done(state.resolve(args.drive), args.worker, args.item, whole))


def work_start(args: argparse.Namespace) -> int:
    """Brief a worker the watch's spawner recorded and start it: as an Orca lane on a Sprite by the launch drive job launch runs, or as its own claude --bg session on this Mac for a group only this Mac runs; a start that fails marks the worker failed, so its group goes back to the queue."""
    name = state.resolve(args.drive)
    try:
        write_brief(name, args.worker, work.brief_flags(state.load(name), args.worker))
        if state.load(name)["jobs"][args.worker]["where"] == "remote":
            return relaunch(argparse.Namespace(**vars(args), id=args.worker, local=False, brief=None, text=None), name, None, resume=False)
    except DriveError as error:
        work.failed(name, args.worker, str(error))
        raise
    session = work.start(name, args.worker)
    return emit(args, f"{args.worker} started as its own claude --bg session {session}", {"worker": args.worker, "session": session})


def work_show(args: argparse.Namespace) -> int:
    """The queue on this drive: the kinds it runs, and for each its live worker, its ready items in order and why no new group starts."""
    data = state.load(state.resolve(args.drive))
    moment = state.now()
    kinds = {name: {"worker": work.live_worker(data, name), "ready": kind.line(data), "why": kind.why_not(data, moment)} for name, kind in work.KIND.items() if work.enabled(data, name)}
    shown = [f"kinds: {', '.join(kinds) or 'none'}"] + [f"{name}: worker {held['worker'] or 'none'}; ready {', '.join(held['ready']) or 'none'}; {f'no new group: {held['why']}' if held['why'] else 'a new group may start'}" for name, held in kinds.items()]
    return emit(args, "\n".join(shown), kinds)


def slot_queue(args: argparse.Namespace) -> int:
    """Each staging slot, or the one env named: its holder, GO with when its lease ends once its cut has its START, by state.cut_started, else held for it and waiting for that START, so no job reads a lease it has not started as leave to run, then each job waiting, in order."""
    data = state.load(state.resolve(args.drive))
    queued = {env: {"holder": None, "queue": []}} if work.enabled(data, work.STAGE_TEST) and (env := work.env(data)) else {}
    slots = queued | data["slots"]
    shown = {env: slot for env, slot in slots.items() if not args.env or env == args.env}
    lines = []
    for env, slot in shown.items():
        held = slot["holder"]
        riding = "".join(", " + rider["job"] + " riding its cut" for rider in held["riders"]) if held else ""
        said = (f"GO {held['job']} until {held['until']}{riding}" if state.cut_started(data, held, env) else f"held for {held['job']} until {held['until']}{riding}, waiting for its START") if held else "free"
        hold = f"; held for {slot['hold']['for']} until {slot['hold']['until']}: {slot['hold']['reason']}" if slot.get("hold") else ""
        lines.append(f"{env}: {said}{hold}")
        lines += [f"  {place}. {each['job']} for {each['minutes']} minutes, queued {each['at']}" for place, each in enumerate(slot["queue"], 1)]
        lines += queue_items(data, env)
    return emit(args, "\n".join(lines) or "no staging slot is held or queued", {"slots": shown})


def queue_items(data: dict, env: str) -> list[str]:
    """The stage-test items the work queue holds for an env, in its own order, by work.rank, each marked in the queue with whether it is ready to pull, so a tester the queue took off the slot's old line never reads as dropped."""
    kind = work.KIND[work.STAGE_TEST]
    if env != work.env(data) or not work.enabled(data, work.STAGE_TEST):
        return []
    items = sorted((job_id for job_id, job in data["jobs"].items() if kind.queued(data, job)), key=lambda job_id: work.rank(data, job_id))
    return [f"  q{place}. {job_id} in the queue, {'ready to pull' if kind.ready(data, job_id) else 'waiting on ' + (', '.join(kind.blocking(data, job_id)) or data['jobs'][job_id]['state'])}" for place, job_id in enumerate(items, 1)]


def job_held(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    job_id = args.id or os.environ.get(state.OWN_JOB_ENV)
    if not job_id:
        raise DriveError("job held needs the job: name it, or run it from a Sprite lane, whose own job it reads")
    data = state.load(name)
    find(data["jobs"], job_id, "job")
    if args.release:
        hold = settled.hold(data["roles"]["coordinator"]["cwd"])
        refused = hold and brief.hold_refusal(args.release, data, hold) or args.release_verb and brief.away_refusal(args.release_verb, args.release, data, state.now())
        return emit(args, refused or f"no hold of his refuses {args.release}", {"id": job_id, "release": refused})
    if args.cluster:
        refused = brief.cluster_job_refusal({"cluster": args.cluster, "cronJob": args.cronjob, "reason": args.reason}, data, state.now())
        return emit(args, refused or f"nothing holds a Job on {args.cluster}", {"id": job_id, "cluster": refused})
    if args.pr:
        tracked = {stored.casefold(): pr for stored, pr in data["prs"].items()}
        prs = {key: tracked.get(key.casefold()) for key in args.pr}
        held = {key: land.pr_cards(data, pr) if pr else [] for key, pr in prs.items()}
        testing = {key: land.staged(data, pr) if pr else [] for key, pr in prs.items()}
        away = {key: why for key, pr in prs.items() if pr and (why := land.unattended(data, state.pr_key(pr["url"]), state.now()))}
        line = "\n".join([*(f"{key}: {', '.join(cards)}" for key, cards in held.items() if cards), *(f"{key}: its staging test {', '.join(runs)} has not passed" for key, runs in testing.items() if runs), *(f"{key}: {why}" for key, why in away.items())])
        return emit(args, line or f"no open card of his or staging test holds {', '.join(held)}", {"id": job_id, "prs": held, "staged": testing, "away": away})
    main = land.claim_main(name, job_id) if args.exempt == state.MAIN_FIX else ""
    return emit(args, main or f"{job_id}: nothing holds its PR", {"id": job_id, "main": main})


def job_wake(args: argparse.Namespace) -> int:
    """Send the text to the job's lane as a message to its Dispatch while Orca holds its worker taking messages, under the job's own lock as drive pr wake sends one, or hold it by codex_resume for a codex job whose codex-ask run goes, which hears it at its next Bash call, and refuse, sending and starting nothing, a job that is stopped, whose lane or codex-ask run ended, whose Sprite Orca cannot reach, which it never wakes, since the watch's drive job resume wakes it, or that no lane Orca holds runs: the one way the watchdog's agent messages the job answering for a stall, by his pick "Report, plus two safe acts, rerunning a failed build and messaging the owning job", never starting a worker."""
    name = state.resolve(args.drive)
    job = find(state.load(name)["jobs"], args.id, "job")
    if job["kind"] == "codex":
        if job["state"] != "running":
            raise DriveError(f"{args.id}: refused, nothing sent or started: no codex-ask run of it goes, and drive job wake reaches only a run that goes")
        return codex_resume(args, name)
    if not job.get("dispatch"):
        raise DriveError(f"{args.id}: refused, nothing sent: no lane Orca holds runs it, and an agent on this Mac takes a message only from its own session")
    with state.job_locked(name, args.id, orca.LAUNCH_SECONDS):
        job = state.load(name)["jobs"][args.id]
        if job["state"] == "stopped":
            raise DriveError(f"{args.id}: refused, nothing sent: it is stopped")
        if end := orca.lane_end(name, args.id, job, wake=False):
            raise DriveError(f"{args.id}: refused, nothing sent or started: its lane takes no messages ({end.why}), and drive job wake sends only to a lane that does")
        how = orca.send(name, job, f"{args.id}: the watchdog's finding", args.text)
    return emit(args, f"{args.id} {how}", {"id": args.id, "how": how})


def lane_saved(drive: str, job_id: str, job: dict, root: Path) -> str | None:
    """What the save of the Orca lane the job's row records by its dispatch found, as orca.save says it: the one its death records when an earlier resume or end of that lane saved its Sprite, since a dead lane's Sprite changes no more and that end destroys it, so a read now finds cc-remote recording no such Sprite, else what a save finds now."""
    return (state.lane_death(job, "dispatch") or {}).get("saved") or orca.save(drive, job_id, job, root)


def release_sprite(drive: str, job_id: str, job: dict, root: Path) -> str | None:
    """Destroy the Sprite cc-remote still records for the job's lane once what it holds on no remote is saved to GitHub as saved/<lane>/<branch>-<sha>; what happened to it, or None when none is recorded."""
    if not orca.holds_sprite(job):
        return None
    try:
        return orca.release(drive, job_id, job, root)
    except orca.OrcaError as error:
        return f"{job['workspace']} is kept: {error}"


def job_prove(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    find(state.load(name)["jobs"], args.id, "job")
    line = install.prove(name, args.id, args.timeout, args.again)
    return emit(args, line, {"id": args.id, "line": line})


def job_end(args: argparse.Namespace) -> int:
    """Free the Orca lane a job's row records by its dispatch and workspace, whatever its where names, so a job an agent on this Mac holds beside a lane a launch recorded frees that lane too: its worker stopped and, for a job done, failed or stopped, its Sprite destroyed once what it holds on no remote is saved to GitHub, while a job that will run again keeps its Sprite, marked by orca.keep_sprite so it pauses once idle and its next lane resumes the same Claude session there, past the newest KEPT_SPRITE_CAP kept the oldest destroyed by evict_kept; or its terminal and clean worktree on this Mac; refused, ending nothing, for a row that records no lane. The job keeps the state its line set, with its waits, PRs and carried parts, so a waiting job's next event starts its next lane and nothing waiting on it is freed. A lane whose death the watch recorded first has its Sprite's Orca runtime and worker logs copied to the drive's folder by orca.keep_logs, and every lane's Sprite has each Claude Code transcript it holds copied whole there by orca.keep_transcripts before anything can destroy it, kept on the lane's death. A job still at work first has what its Sprite holds that its next lane would not get saved to GitHub, by lane_saved, and each text queued for its worker that its Dispatch mailbox shows unread held for that lane, by hold_unread, since the end drops that mailbox with the Sprite, and its line, as that lane's handoff will, names each branch GitHub then holds under saved/<its lane>/, by one read, or, when GitHub holds none, what that save found; refused, ending nothing, when that Sprite or mailbox cannot be read or the save fails, and for a running job, whose lane has returned no line, so its next event, never its end, comes next. The end holds the job's own lock, as a launch, resume or PR wake does, so it never destroys a Sprite a relaunch is starting the job's next lane on and refuses a job that relaunch set running. The end is recorded on the lane's dispatch before anything is saved, stopped or destroyed, so the watch reads none of it as the lane dying, and an end of a lane another end already ended changes nothing."""
    name = state.resolve(args.drive)
    with state.job_locked(name, args.id, orca.LAUNCH_SECONDS):
        line, ended = end_lane(name, args.id)
    return emit(args, line, ended)


def stopped_lane(name: str, job_id: str) -> list[str]:
    """End the lane of a job a stop set stopped, by end_lane under the job's own lock, so a launch or resume starting its next worker as the stop landed records that worker first and its lane is the one ended, so no stopped job's worker runs on; nothing for a job no Orca lane runs or whose lane an end already ended."""
    with state.job_locked(name, job_id, orca.LAUNCH_SECONDS):
        job = state.load(name)["jobs"][job_id]
        if not job.get("dispatch") or state.lane_ended(job):
            return []
        return [end_lane(name, job_id)[0]]


def end_lane(name: str, job_id: str) -> tuple[str, dict]:
    """drive job end's work under the job's lock its caller holds: the line it prints and what it ended."""
    data = state.load(name)
    job = find(data["jobs"], job_id, "job")
    if not job.get("dispatch"):
        raise DriveError(f"{job_id}: refused, nothing ended: its row records no Orca lane, on a Sprite or on this Mac, so nothing is left to end")
    if job["state"] == "running":
        raise DriveError(
            f"{job_id}: refused, nothing ended: it is running, so its lane has returned no line to end it on; drive job resume {job_id} sends a lane that stopped"
            f" without one its next event, and drive job set {job_id} --state stopped ends it unfinished"
        )
    root = orca.checkout(name, data["roles"]["coordinator"]["cwd"])
    def ending(data: dict) -> tuple[dict, bool, bool | None]:
        ended = data["jobs"][job_id]
        if already := state.lane_ended(ended):
            return ended, already, None
        fresh = state.lane_died(ended, job["dispatch"], "ended by drive job end", state.now())
        state.lane_death(ended, "dispatch")["ended"] = {"pid": os.getpid(), "at": state.stamp()}
        return ended, already, fresh

    ended, already, fresh = state.update(name, None, ending)
    if already:
        line = f"{job_id}'s lane was already ended by the drive job end of {state.lane_death(ended, "dispatch")['ended']['at']}, so nothing changed; {job_id} stays {ended['state']}"
        return line, {"id": job_id, "state": ended["state"], "already": True}
    if not (fresh or state.on_mac(job)):
        orca.keep_logs(name, job_id, job, job["dispatch"])
    saved = None
    if job["state"] not in state.FINISHED_JOB_STATES:
        try:
            saved = lane_saved(name, job_id, job, root)
            if job["queued"]:
                hold_unread(name, job_id, job)
        except orca.OrcaError as error:
            end_kept(name, job_id, {"failed": True, "fresh": fresh, "saved": None})
            raise DriveError(
                f"{job_id}: refused, nothing ended: it is {job['state']}, and what {job['workspace']} holds on no remote, or the texts its worker has not read,"
                f" could not be saved for its next lane: {error}. End it again once that Sprite answers"
            ) from None
    transcript = None if state.on_mac(job) else orca.keep_transcripts(name, job_id, job, job["dispatch"], root)
    kept = job["state"] not in state.FINISHED_JOB_STATES and orca.holds_sprite(job)
    if kept:
        orca.keep_sprite(job["workspace"])
    reclaimed = orca.reclaim(job["run"], job["dispatch"], root)
    released = f"Sprite {job['workspace']} is kept, to pause once idle, for the next lane of {job_id}, which resumes its Claude session there" if kept else release_sprite(name, job_id, job, root)
    ended = end_kept(name, job_id, {"failed": False, "fresh": fresh, "saved": saved, "transcript": transcript and str(transcript), "kept": kept})
    evicted = evict_kept(name, job_id, root) if kept else []
    branches = orca.saved(name, job_id, job, root) if saved else []
    line = f"{job_id}'s lane ended; {job_id} stays {ended['state']}" + (f", and what its Sprite held on no remote is saved on GitHub as {', '.join(branches)}" if branches else f", and {saved}" if saved else "")
    return "\n".join(filter(None, [reclaimed, released, line, *evicted])), {"id": job_id, "reclaim": reclaimed, "released": released, "evicted": evicted, "state": ended["state"], "saved": saved, "branches": branches}


def evict_kept(name: str, job_id: str, root: Path) -> list[str]:
    """Destroy, by release_sprite under each one's own lock, the Sprites a drive job end kept for a job's next lane past the newest KEPT_SPRITE_CAP of them, oldest end first, never the job given, whose end kept one just now, and passing over a job whose lock another verb holds, as a relaunch resuming on its kept Sprite does; a line for each one destroyed or kept by a refusal."""
    data = state.load(name)
    kept = sorted(((death["ended"]["at"], other) for other, job in data["jobs"].items() if (death := state.lane_death(job, "dispatch")) and death.get("ended", {}).get("kept") and orca.holds_sprite(job)), reverse=True)
    lines = []
    for _, other in kept[KEPT_SPRITE_CAP:]:
        if other == job_id:
            continue
        try:
            with state.job_locked(name, other, 0):
                job = state.load(name)["jobs"][other]
                if not (state.lane_death(job, "dispatch") or {}).get("ended", {}).get("kept"):
                    continue
                lines.append(release_sprite(name, other, job, root) or f"{other}'s kept Sprite {job['workspace']} was already gone")
                def evict_kept_write(data: dict):
                    state.lane_death(data["jobs"][other], "dispatch")["ended"]["kept"] = False

                state.update(name, None, evict_kept_write)
        except DriveError:
            continue
    return lines


def end_kept(name: str, job_id: str, result: dict) -> dict:
    """Keep on the job what its lane's end came to once it saved, reclaimed and released: the branches saved and its kept transcript, its open question kept for the next worker's handoff to answer, the job left unstamped, since the end of a lane at rest changes nothing it does and so says none of its parts again, or, when the save failed, the end mark taken back, so the lane reads as it did."""
    def end_kept_write(data: dict):
        ended = data["jobs"][job_id]
        death = state.lane_death(ended, "dispatch")
        if result["failed"]:
            if result["fresh"]:
                ended["deaths"].remove(death)
            else:
                del death["ended"]
        else:
            if result["saved"]:
                death["saved"] = result["saved"]
            if result.get("transcript"):
                death["transcript"] = result["transcript"]
            if result.get("kept"):
                death["ended"]["kept"] = True
        return ended

    ended = state.update(name, None, end_kept_write)
    return ended


class Kept(DriveError):
    """A Sprite drive job reclaim could not destroy without losing what it may hold on no remote, marked kept on the death of the lane that left it, so no watch asks for it again and one state.detach started writes the one relaunch_refused event that says so."""


def job_reclaim(args: argparse.Namespace) -> int:
    """Free one thing an earlier or ended lane of the job left, which the watch's release_lanes starts as a process of its own: on a Sprite, the one cc-remote records under that workspace, destroyed by orca.release once what it holds on no remote is saved to GitHub as saved/<the job's lane>/<branch>, or else marked kept on that lane's death and by orca.keep_sprite, and refused with Kept, since a Sprite that does not answer, a superseded attempt's included, or any Sprite that holds what could not be saved or that cc-remote would not destroy, may still hold work on no remote, so the watch asks for it again in an hour; on this Mac, that dispatch, whose terminal orca.reclaim closes and whose worktree it removes once clean and pushed, then marked freed on its death, so no watch reclaims it again. Either runs the orca plugin's reclaimer from the drive's checkout, moved to its default branch's head first, through orca.reclaimer, which kills it with every process it started past RECLAIM_SECONDS; refused, naming why, for a lane no death of the job records, when the checkout is not moved, or when the reclaimer fails or overruns."""
    name = state.resolve(args.drive)
    data = state.load(name)
    job = find(data["jobs"], args.id, "job")
    if not state.death_on(job, args.lane):
        raise DriveError(f"{args.id}: refused, nothing reclaimed: no death of its lanes records {args.lane}, so no lane of it left that")
    root = orca.checkout(name, data["roles"]["coordinator"]["cwd"])
    if not state.on_mac(job):
        try:
            released = orca.release(name, args.id, {**job, "workspace": args.lane}, root)
        except orca.OrcaError as error:
            orca.keep_sprite(args.lane)
            def job_reclaim_write2(data: dict):
                state.death_on(data["jobs"][args.id], args.lane)["kept"] = state.stamp()
                return data

            data = state.update(name, None, job_reclaim_write2)
            raise Kept(
                f"{args.id}: refused, Sprite {args.lane} is kept, and the watch asks to save and destroy it again in an hour: {error}. Destroying it unread may lose what it holds on no remote,"
                f" so only his pick does that; drive job reclaim {args.id} {args.lane} asks again, saving it first"
            ) from None
        return emit(args, released, {"id": args.id, "lane": args.lane, "released": released})
    reclaimed = orca.reclaim(job["run"], args.lane, root)
    def job_reclaim_write(data: dict):
        state.death_on(data["jobs"][args.id], args.lane)["freed"] = state.stamp()

    state.update(name, None, job_reclaim_write)
    return emit(args, reclaimed, {"id": args.id, "lane": args.lane, "reclaimed": reclaimed})


def pr_add(args: argparse.Namespace) -> int:
    from drive import view

    name = state.resolve(args.drive)
    key = state.pr_key(args.url)
    if (job := find((data := state.load(name))["jobs"], args.job, "job"))["kind"] == "reader":
        raise DriveError(f"{key}: refused, nothing added: {state.unshipped(data, args.job)}")
    if args.no_tap and not args.why:
        raise DriveError("--no-tap needs --why <reason>: every PR waits on his tap unless it is a port, exempt or drive tooling outside Forge-AI")
    if args.no_tap and state.guarded(providers.of(data).repo.guarded_owner, key):
        raise DriveError(UNLANDABLE.format(key=key, done="added"))
    if others := [wait for wait in args.waits if not state.REVIEW_WAIT.fullmatch(wait)]:
        raise DriveError(
            f"--waits {', '.join(others)}: refused, nothing added: drive pr add takes only a teammate's review, review:<their GitHub login>,"
            f" which keeps his card from drawing in this call; set any other wait once it is added, with drive pr set {key} --waits <it>"
        )
    if len(parts := [part for order_id in [job["order"], *job["carries"]] for part in state.job_parts(job, order_id)]) > 1 and not args.part and not data["prs"].get(key, {}).get("parts"):
        raise DriveError(
            f"{key}: refused, nothing added: its job {args.job} delivers {len(parts)} parts, and drive pr check judges a PR only against the words of the parts it serves,"
            f" so name each this one delivers: {' '.join(f'--part {shlex.quote(part)}' for part in parts)}"
        )
    lead = {line: given for line, flag in limits.LEAD_LINES if (given := getattr(args, flag.removeprefix("--")))}
    kept = data["prs"].get(key, {}).get("lead")
    pr, testers = add_pr(name, key, args.job, github_pr(key), args.port_of, args.exempt, args.no_tap, lead, args.part, args.waits)
    shown = view.card_for((view.live_cards(name) or view.NO_CARDS)["pr_cards"], pr["url"])
    drawn = state.after_save(name, lambda: [f"{key}{said}"] if (said := (releaded(pr) if pr["lead"] != kept else "") if shown else drawn_card(name, key)) else [])
    data = state.load(name)
    return emit(args, "\n".join([*(drawn or [key]), *render.builder_lines(name, data, testers)]), {"key": key, **data["prs"][key]})


def drawn_card(name: str, key: str) -> str:
    """Draw his card on a PR waiting on his tap in the call that registers it, so no session's turn stands between the PR and his card, unless state.tap_refused finds it would offer no Approve, such as on a PR his open question card holds; what came of it. A draw that fails leaves its stage uncarded, which the watch draws on its next read."""
    from drive import talk

    data = state.load(name)
    if approval := (data["prs"][key]["tap"] or {}).get("approval"):
        return f", approved with no card under his standing approval {approval[:7]}"
    if not state.asks_tap(data, data["prs"][key]):
        return ""
    try:
        return f", his card {talk.draw(name, [key])}"
    except (slack.SlackError, DriveError) as error:
        return f", his card not drawn yet, so the watch draws it on its next read: {error}"


def releaded(pr: dict) -> str:
    """Redraw in place his open card on a PR whose lead changed, so it opens with the lead kept now; what came of it."""
    try:
        slack.redraw_pr_cards(pr["url"])
    except slack.SlackError as error:
        return f", his card still opens with its old lead, since its redraw failed: {error}"
    return ", his card redrawn with its new lead"


@children.capped
def pr_wake(args: argparse.Namespace) -> int:
    """Wake the PR's builder by wake_pr, then write each --woke mark the watch asked for, dated --woke-at, by stage.mark_woken, only once the line went out, so a wake that dies in its slot's queue or mid-send leaves its mark unwritten and the watch asks again."""
    from drive import stage

    done = wake_pr(args)
    if args.woke and done == 0:
        stage.mark_woken(state.resolve(args.drive), args.woke, args.woke_at)
    return done


@orca.rides_restarts()
def wake_pr(args: argparse.Namespace) -> int:
    """Send a PR's stage line, or the text given, to the builder answering for it, or to the job given, as a PR waiting on that job's work owes it: to a lane Orca holds as a message to its Dispatch, which neither answers its question nor touches its job's ask or state; to a fresh worker on its job when that lane finished or died, as drive job resume relaunches one, and for a lane on a Sprite whose declared_step reads a step only this Mac runs, to a fresh worker on this Mac whatever its Sprite worker is doing; to an adopted PR's job, by state.adopted_pr_job, a drive job launch it detaches with the PR, its head and the line as the brief's handoff, so the event starts its first builder with no hand step; and to any other builder as a handoff record naming superseded.delivery's step that reaches the builder, to the session that step names as the one that may take it: the session holding its order for a fresh builder, the coordinator session that started its agent on this Mac for a SendMessage, else the session state.job_lane names, the line only held, with no record, on a builder waiting its turn for a slot on this Mac, by superseded.in_line, since the worker the watch starts as its turn comes carries it, and held on a job a resume reaches, whose holder gets one record asking drive job resume with no text, later lines held beside it with no record of their own until that resume sends them all. A PR whose own job is stopped, as his superseding answer stops one, or whose job stopped while the wake waited on its lock, goes the same way to the session holding that job, by state.holding_job_lane, which closes the PR or moves it, so no wake starts work he called off. A lane's send or relaunch runs under the job's own lock, as drive job resume's does, so a second wake arriving while the first starts the job's next worker waits for it and sends that worker its line."""
    from drive import stage, tools, view

    name = state.resolve(args.drive)
    key = state.pr_key(args.url)
    data = state.load(name)
    if head := stage.left_head(data, args.woke):
        return unsent_wake(args, key, head, data)
    target = args.job or data["prs"][key]["job"]
    held_job(data, target)
    if args.text is None and (cards := view.live_cards(name)) is None:
        raise DriveError(f"{key}: his cards could not be read, so its stage is unknown; pass --text")
    text = args.text or stage.line(key, data["prs"][key], stage.of(data, key, cards))
    job_id = None if data["jobs"][target]["state"] == "stopped" else target
    if job_id and data["jobs"][job_id].get("dispatch"):
        with state.job_locked(name, job_id, orca.LAUNCH_SECONDS):
            data = state.load(name)
            if head := stage.left_head(data, args.woke):
                return unsent_wake(args, key, head, data)
            job = data["jobs"][job_id]
            if job["state"] != "stopped":
                step = declared_step(name, job)
                if (end := orca.lane_end(name, job_id, job, wake=step is None)) or step:
                    return restart(argparse.Namespace(id=job_id, text=text, drive=args.drive, json=args.json), name, job["dispatch"], end or live_end(job), text, declared=step)
                how = orca.send(name, job, f"{key}: its next step is yours", text)
                return emit(args, f"{key}: {job_id} {how}", {"key": key, "job": job_id, "how": how})
        job_id = None
    pr = data["prs"][key]
    job = data["jobs"][target]
    if job_id and state.adopted_pr_job(data, job):
        state.detach(name, ["job", "launch", job_id, "--handoff", f"{pr['url']} at {pr['head']}: {text}"], watch.RELAUNCHES_LOG)
        return emit(args, f"{key}: ran drive job launch {job_id} with this line as its handoff, its output in the drive's {watch.RELAUNCHES_LOG}", {"key": key, "job": job_id, "how": "launch"})
    if job_id:
        queued = superseded.in_line(data, job_id, job)
        fresh = superseded.fresh_builder(data, job_id, job, text, "this line")
        resumed = not fresh and not queued and bool(job.get("agent") or job.get("dispatch"))
        owed: list[bool] = []
        if queued or resumed:
            def staged(held: dict) -> None:
                owed.append(any(kept["words"].startswith("STAGE ") for kept in held["jobs"][job_id]["undelivered"]))
                state.hold_text(held["jobs"][job_id], f"STAGE {text}", state.held_sender(held))

            state.update(name, None, staged)
        if queued and not fresh:
            return emit(args, f"{key}: held on {job_id}, which waits its turn for a builder slot on this Mac, for the worker the watch starts as its turn comes", {"key": key, "job": job_id, "how": "held"})
        if resumed and owed[0]:
            return emit(args, f"{key}: held on {job_id} beside the STAGE line its holder was already asked to resume it with", {"key": key, "job": job_id, "how": "held"})
        if resumed:
            lane, step = state.job_lane(data, job), f"drive job resume {job_id} with no text, which sends it every text it holds, this line included"
        else:
            lane, step = superseded.delivery(data, job_id, job, text, "this line")
        said = f"STAGE {text}; for {job_id}, with this line alone: {step}"
    else:
        lane = state.holding_job_lane(data, job)
        said = f"STAGE {text}; no live job answers for it and {target} is {job['state']}, so no wake starts it: close it, or move it to a live job with drive pr add {pr['url']} --job <j>"
    topic = f"wake-{key}-{pr['head'][:10]}"
    tools.sent(name, "watch", [{"kind": "handoff", "to": [f"{lane}@{name}"], "topic": topic, "text": said}])
    return emit(args, f"{key}: sent to {lane}@{name} for {target}", {"key": key, "job": job_id, "how": f"handoff to {lane}"})


def unsent_wake(args: argparse.Namespace, key: str, head: str, data: dict) -> int:
    """Say that a watch wake built at a head the PR left goes unsent, since its line speaks of that head and the watch's next pass reads the PR's stage at the new one."""
    now = data["prs"][key]["head"]
    return emit(args, f"{key}: sent nothing, since its line was built at {head[:7]} and a push moved it to {now[:7]}; the watch's next pass reads its stage there", {"key": key, "job": None, "how": "unsent", "built": head, "head": now})


def add_pr(name: str, key: str, job_id: str, read: dict, port_of: int | None = None, exempt: str | None = None, no_tap: bool = False, lead: dict | None = None, parts: list[str] | None = None, waits: list[str] = ()) -> tuple[dict, list[str]]:
    """Register a PR on its job from its GitHub read, refused as `drive pr add` refuses, keeping the title and branch it read, so a PR merged and proved before the watch's next read is named by it and a card naming its branch holds it at once, and the parts of its job's orders it names as delivering, so its first check judges it on those, and, for an open one whose head changes a dangerous class of file, its staging test with a tester, by state.mark_dangerous, before his card draws; the PR as registered, which ends each wait on its job's change, and the tester added. `drive pr add` runs it, and `drive job report` for each PR a builder's READY or PUSHED names that no `drive pr add` registered, so every PR a result names lands as one its builder added does, and for each GitHub PR the drive does not track that the FIX line of a report tracing a live bug's cause links. Someone else's PR his words on its order do not name is refused before any check that would have its title or body edited. The lead lines given are kept over those kept before, and each teammate's review it waits on, as review:<login>, beside its other waits, so his card draws only once each lands on its head. A PR is refused once, nothing written, naming every one of these it has, so one relayed call or dry run names them all: a title his card cannot name the kind of, a Show me picture that does not draw, and, on a PR his card asks his tap on, a lead that does not tell him why it is made, what it does and what it changes downstream, by limits.lead_problems; a FIX line's PR, given no lead, is someone else's and kept as it reads. A PR registered for the first time with no class or port named keeps the class its listed files put it in, by path_class, so a CI fix of his draws no card from any of these callers. A PR first registered open where his card would ask his tap takes his Approve from his standing approval covering it, by settled.approved_by, so no card draws. An open PR waiting on his tap whose paths reach a component staging runs is kept waiting on its staging test, by staging_reach, so his first card on it names that wait, and a failed read of that reach refuses the add, nothing kept."""
    from drive import stage

    url = state.pr_url(key)
    known = state.load(name)
    added = find(known["jobs"], job_id, "job")
    author = read["author"]["login"]
    if why := state.foreign(key, author, state.his_words_on(known, [known["orders"][added["order"]]])) or state.pr_held_elsewhere(known, key, job_id):
        raise DriveError(f"{key}: refused, nothing added: {why}")
    jobs = list(dict.fromkeys([job_id, *(state.pr_jobs(known, known["prs"][key]) if key in known["prs"] else [])]))
    if workspace := added.get("workspace"):
        take_sprite_pictures(name, key, job_id, jobs, workspace, read)
    if key not in known["prs"] and (seen := brief.surface(added)) and brief.stories(added["brief_flags"].get("interactions")) and not added.get("repro"):
        raise DriveError(
            f"{key}: refused, nothing added: {job_id} builds something he sees ({seen}), so each user story's strict acceptance criteria"
            f" come before the build: write them into the proof file that clicks each story on the installed surface, keep it with"
            f" drive job set {job_id} --repro <file>, then add this PR, by his words {brief.STORIES_RULING}"
        )
    if port_of:
        refuse_a_port_storing_data(key, port_of)
        refuse_an_untracked_original(known, key, port_of)
    if (exempt or no_tap) and (incidents := state.open_incidents(known, [added["order"], *added["carries"]])):
        refuse_an_exempt_revert(known, key, incidents[0], read, f"Add it waiting on his tap with drive pr add {url} --job {job_id}. ")
    if exempt and exempt != state.FLAKE_FIX:
        refuse_an_unexempt(name, key, exempt, "added")
    failing = install.default_failures(github.repo_of(key)) if exempt == state.MAIN_FIX else {}
    if not (exempt or port_of):
        exempt = path_class(known, key, added, read)
    wants_tap = state.waits_his_tap(known, key, added["order"])
    flaky = flake_tap(key, wants_tap) if exempt == state.FLAKE_FIX else None

    def registered(pr: dict, order: str) -> dict:
        pr.update(order=order, author=author, title=read["title"], branch=read["headRefName"] or pr["branch"])
        if port_of:
            pr.update(port_of=port_of, exempt="pure port", wants_tap=False)
        if exempt:
            pr["exempt"] = exempt
        if flaky is not None:
            pr["wants_tap"] = flaky
        if no_tap:
            pr["wants_tap"] = False
        if lead:
            pr["lead"] = (pr["lead"] or {}) | lead
        pr["waits"] = list(dict.fromkeys([*pr["waits"], *waits]))
        return pr

    pending = registered(dict(known["prs"].get(key) or state.new_pr(url, job_id, added["order"]) | {"wants_tap": wants_tap}), added["order"])
    problems = [why for why in [unkinded_title(key, read, port_of)] if why] + undrawn_show_me(name, read, jobs) + (put_off_steps(key, read) if lead is not None else [])
    if lead is not None and stage.carded(pending) and (unled := limits.lead_problems(pending["lead"] or {})):
        flags = " ".join(f'{flag} "<{line}>"' for line, flag in limits.LEAD_LINES)
        problems.append(f"his card opens with its lead, and {'; '.join(unled)}. Add it with drive pr add {url} --job {job_id} {flags}")
    if len(problems) == 1:
        raise DriveError(f"{key}: refused, nothing added: {problems[0]}")
    if problems:
        raise DriveError("\n".join([f"{key}: refused, nothing added, for each of these {len(problems)}:", *(f"{number}. {why}" for number, why in enumerate(problems, 1))]))
    try:
        staged = staging_reach(name, known, [key] if pending["wants_tap"] and read["state"] == "OPEN" else [])
    except DriveError as error:
        raise DriveError(f"{key}: refused, nothing added: {error}") from None
    work = {"order": known["orders"][added["order"]]["text"], "pr": {"title": read["title"], **(pending["lead"] or {})}}
    approval = settled.approved_by(known, key, added["order"], work) if key not in known["prs"] and read["state"] == "OPEN" and state.asks_tap(known, pending) else None
    def add_pr_write(data: dict):
        job = find(data["jobs"], job_id, "job")
        if data["orders"][job["order"]]["once"]:
            raise DriveError(f"refused, {job['order']} is a repair he asked to run once: run it, and put up no PR for it")
        pr = registered(data["prs"].setdefault(key, state.new_pr(url, job_id, job["order"]) | {"wants_tap": wants_tap}), job["order"])
        state.take_pr(data, job_id, key)
        if refused := state.claim_mains(data, github.repo_of(key), job_id, failing, key, state.stamp(state.now())):
            raise DriveError(f"{key}: refused, nothing added: {refused}")
        if parts:
            refuse_a_part_its_job_lacks(data, key, parts, False, "added")
            pr["parts"] = list(dict.fromkeys(parts))
        testers = [state.mark_dangerous(data, key, read["headRefOid"], [changed["path"] for changed in read["files"]])] if read["state"] == "OPEN" and read["files"] else []
        if key in staged:
            testers.append(state.reach_staging(data, key, staged[key]))
        if approval:
            pr["tap"] = state.new_tap("Approve", read["headRefOid"], data["answers"][approval]["link"], card=[key], approval=approval)
        return pr, testers

    pr, testers = state.update(name, None, add_pr_write)
    note_third_person(key, read)
    return pr, [tester for tester in testers if tester]


def design_basis(text: str) -> str:
    if text == "none" or (text.startswith("asked:") and served.named(text)):
        return text
    raise argparse.ArgumentTypeError(f"say asked:<{served.NAMES}>, or none")


def his_link(text: str) -> str:
    if state.CARD_LINK.search(text):
        return text
    raise argparse.ArgumentTypeError(
        "name the Slack permalink of his message or of the card he tapped: a hold or approval the drive gave itself is never his,"
        " and one recorded as his drops with --no-tapped --why"
    )


def pr_set(args: argparse.Namespace) -> int:
    from drive import stage, view

    name = state.resolve(args.drive)
    key = state.pr_key(args.url)
    if args.tap and args.port_of:
        raise DriveError("a port lands with no tap: pass --port-of alone")
    if args.tapped and not args.tap_source:
        raise DriveError(
            f"--tapped {args.tapped} needs --tap-source <the Slack permalink of his message or of the card he tapped>:"
            " only his own words are his tap, and a PR the drive holds itself carries --waits on what it waits for, never the hold label"
        )
    if args.tap is False and not args.why:
        raise DriveError("--no-tap needs --why <reason>: his card for it, if one is open, is withdrawn showing that reason")
    if args.no_tapped and not args.why:
        raise DriveError("--no-tapped needs --why <reason>: why the hold the drive gave itself no longer stands, kept in the drive's events")
    known = state.load(name)
    if why := state.foreign_pr(known, key):
        raise DriveError(f"{key}: refused, nothing set: {why}")
    before = known["prs"][key]
    if args.flaky and not (before["checks"] == "fail" or before["ejected"] and (before["lander"] or {}).get("head") == before["head"]):
        raise DriveError(
            f"{key}: refused, nothing set: --flaky names the flaky test that failed it at its head, in its own checks or in Trunk's ejection of its lander's try,"
            f" and at {before['head'][:7]} none of its checks fail and Trunk ejected no try of its lander"
        )
    if args.flaky and not state.PR_WORD.fullmatch(args.flaky[1]) and before["checks"] == "fail":
        raise DriveError(
            f"{key}: refused, nothing set: --flaky {args.flaky[0]} names its cause and no fix PR, which queues again only a green head Trunk ejected,"
            f" and its own checks fail at {before['head'][:7]}, where a rerun grades the same merge commit: name that test's fix, a PR the drive tracks, to restack onto"
        )
    if args.staging and not state.home_repo(known := state.load(name), key.rpartition("#")[0]):
        raise DriveError(f"{key}: refused, nothing set: --staging runs a {state.home(known)} PR's cases on {providers.of(known).staging.env}, and {key} is no {state.home(known)} PR")
    if args.exempt == state.FLAKE_FIX and args.tap is None:
        args.tap = flake_tap(key, state.waits_his_tap(known, key, before["order"]))
    after = dict(before)
    set_pr(after, args)
    if state.guarded(providers.of(state.load(name)).repo.guarded_owner, key) and (before["wants_tap"] or before.get("exempt")) and not (after["wants_tap"] or after.get("exempt")):
        raise DriveError(UNLANDABLE.format(key=key, done="set"))
    dropped = known["prs"][key]["tap"]
    if args.no_tapped:
        refuse_a_tap_of_his(name, key, dropped)
    order = known["prs"][key]["order"]
    delivers = before["state"] == "merged" and known["jobs"][before["job"]]["state"] == "done"
    if args.part:
        refuse_a_part_its_job_lacks(known, key, args.part, delivers, "set")
    if args.serves:
        refuse_a_crossed_order(name, known, order, args.serves, f"{key} --serves")
    if (args.serves or args.design) and (refused := state.held_elsewhere(known, order, os.environ.get("CLAUDE_CODE_SESSION_ID"))):
        raise DriveError(f"{key}: refused, nothing set: {refused}")
    if args.serves and (reads := {member: served.change(member) for member in land.landing_stacks(known).get(key, (key,))}) and (unwritten := served.unwritten(key, served.owed(known, key, served.coded(known, key, brief.bound(name, known, known["jobs"][before["job"]]), reads), reads))):
        raise DriveError(f"{key}: refused, nothing set: {unwritten}; push and set it again")
    answered = served.answers(known, [args.design], view.live_cards(name)) if args.design else None
    if replies := [wait for wait in args.waits if state.REPLY_WAIT.fullmatch(wait)]:
        raise DriveError(f"--waits {', '.join(replies)}: refused, nothing set: a teammate's reply or another drive's next record frees the job waiting on it, never a PR: drive job set {before['job']} --waits <it>")
    if cards := [wait for wait in args.waits if state.card_wait(known, wait) and not state.PR_KEY.fullmatch(wait) and not state.ORDER_PART.fullmatch(wait)]:
        raise DriveError(
            f"--waits {', '.join(cards)}: refused, nothing set: a card of his holds a PR from landing only by its question's Holds: line,"
            f" which drive job ask keeps, by his pick \"Each question names the changes it holds, kept with it\", or by the PR's DESIGN: line citing it;"
            f" a card whose Holds: line names {key}'s change or its link, or that its DESIGN: line cites, already holds it, and drive job set --waits <card> wakes a job on his answer"
        )
    if tooled := state.tooling_waits(known, order, args.waits):
        raise DriveError(
            f'--waits {", ".join(tooled)}: refused, nothing set: the work of a "{state.TOOLING}" order never holds {key}, a PR of his product,'
            f" by his ruling {state.TOOLING_RULING}: it lands on its tap while a tooling question waits on its own card"
        )
    found = check_waits(name, args.waits)
    for wait in args.waits:
        if state.part_wait(known, wait) and (why := view.pooled(known, key, wait)):
            raise DriveError(f"--waits {wait}: refused, nothing set: {why}")
    if args.port_of or args.tap or args.exempt == INCIDENT_EXEMPTION:
        read = github_pr(key)
        if (args.port_of or args.tap) and (why := unkinded_title(key, read, args.port_of)):
            raise DriveError(f"{key}: refused, {why}, then run this again")
        if args.port_of:
            refuse_a_port_storing_data(key, args.port_of)
            refuse_an_untracked_original(known, key, args.port_of)
        if args.exempt == INCIDENT_EXEMPTION:
            refuse_an_exempt_revert(known, key, order, read)
    if args.exempt and args.exempt != state.FLAKE_FIX:
        refuse_an_unexempt(name, key, args.exempt, "set")
    failing = install.default_failures(github.repo_of(key)) if args.exempt == state.MAIN_FIX else {}
    try:
        staged = staging_reach(name, known, [key] if after["wants_tap"] and before["state"] == "open" else [])
    except DriveError as error:
        raise DriveError(f"{key}: refused, nothing set: {error}") from None
    serving = "".join(f"\nhis answer its design cites, kept: {answer}" for answer in (answered["cited"] if answered else []))
    def pr_kept(data: dict):
        pr = find(data["prs"], key, "drive PR")
        if refused := state.claim_mains(data, github.repo_of(key), pr["job"], failing, key, state.stamp(state.now())):
            raise DriveError(f"{key}: refused, nothing set: {refused}")
        serves_line = ""
        if args.serves:
            serves_line = f"\nserves {args.serves}, his words: {find(data['orders'], args.serves, 'order')['text']}"
        set_pr(pr, args, answered)
        if args.excepted:
            if not pr["exception"]:
                raise DriveError(f"{key}: refused, nothing set: it waits on no exception of his, since staging runs a copy of what it reaches")
            pr["exception"]["granted"] = find(data["answers"], args.excepted, "answer of his")["link"] or args.excepted
        if args.tapped == "Hold":
            state.end_approvals(data, key, before["tap"])
        pr["waits"] = merged_waits(data, pr, args.waits, args.no_waits, found)
        reached = state.reach_staging(data, key, staged[key]) if key in staged else None
        testers = [tester_id for tester_id in [reached, args.staging and state.stage_pr(data, key, args.staging, data["jobs"][pr["job"]].get("session"))] if tester_id]
        delivering = f"\ndelivers {'; '.join(pr['parts'])}" if args.part else ""
        if args.part and delivers:
            delivering += shipped(data, pr["job"], order, args.part)
        return data, pr, serves_line, testers, delivering

    data, pr, serves_line, testers, delivering = state.update(name, None, pr_kept)
    serving += serves_line + "".join(f"\n{line}" for line in render.builder_lines(name, data, testers)) + delivering
    untapped = f"\ndropped the {dropped['label']} the drive gave itself ({dropped['source']}): {args.why}" if args.no_tapped else ""
    lane = state.heard_lane(data, order)
    withdrawn: list[str] = []
    told = state.after_save(name, lambda: set_outside(name, key, pr["url"], args, dropped, lane, withdrawn))
    view.refresh_cards(name)
    found, source = view.stored_cards(name)
    found = found if source == "live" else None
    now = state.load(name)
    stage.shown(now, found, [key] if now["prs"][key]["state"] == "open" else [])
    line = render.pr_line(now, key, view.pr_card_words(found, source, now["prs"][key])) + serving + untapped + "".join(f"\n{said}" for said in told)
    return emit(args, line, {"key": key, "withdrawn": withdrawn, **pr})


def set_outside(name: str, key: str, url: str, args: argparse.Namespace, dropped: dict | None, lane: str, withdrawn: list[str]) -> list[str]:
    """What drive pr set does outside the drive once its write is kept, a line each: the hold the drive gave itself kept dropped on its events, and his open card withdrawn when the PR no longer waits on his tap, each such card added to withdrawn; the watch's next pass redraws in place each other card of his carrying it to show what the set stored."""
    if args.no_tapped:
        repo, number = key.split("#")
        state.append_event(name, "untapped", repo=repo, number=int(number), label=dropped["label"], source=dropped["source"], why=args.why, lane=lane)
    if why := withdrawal(args):
        try:
            withdrawn += slack.withdraw_pr_card(url, why)
        except slack.SlackError as error:
            raise DriveError(f"{key}: withdrawing its open card failed, so his Approve still shows: {error}; run this again") from None
    return [f"withdrew his card {card}: {why}" for card in withdrawn]


def shipped(data: dict, job_id: str, order_id: str, parts: list[str]) -> str:
    """Give a done job the parts of the order its merged PR shipped without holding, by his answer 431352e, so the job owes them and its passing proof delivers them, by state.deliver_proven, at once when its said line is that proof's DONE and else once drive job prove runs its proof file; the line saying which."""
    job = data["jobs"][job_id]
    held = [part for held_order in [job["order"], *job["carries"]] for part in state.job_parts(job, held_order)]
    if unheld := [part for part in parts if part not in held]:
        state.deliver(data, job_id, order_id, unheld)
    if not (proven := state.PROVEN_LINE.match(job["said"] or "")):
        return f"\n{job_id} holds them, and drive job prove delivers them once its proof file exits 0"
    state.deliver_proven(data, job_id, proven["proof"])
    return f"\nposted by {job_id}'s passing proof: {proven['proof']}"


def refuse_a_part_its_job_lacks(data: dict, key: str, parts: list[str], delivers: bool, done: str) -> None:
    """Refuse a --part its job delivers on no order, since the PR's change is judged against the parts its job's PRs name; a merged PR whose job returned DONE also names any part of its order, which its job then holds and its passing proof delivers, by shipped, since it shipped work its job was never given."""
    pr = data["prs"][key]
    job = data["jobs"][pr["job"]]
    held = [part for order_id in [job["order"], *job["carries"]] for part in state.job_parts(job, order_id)]
    delivered = list(dict.fromkeys([*data["orders"][pr["order"]]["parts"], *held])) if delivers else held
    if unknown := [part for part in parts if part not in delivered]:
        listed = ", ".join(f'"{part}"' for part in delivered)
        lacks = f"its order {pr['order']} and its job {pr['job']} have" if delivers else f"its job {pr['job']} delivers"
        raise DriveError(f"{key}: refused, nothing {done}: {lacks} no part {', '.join(unknown)}; its parts are {listed}")


def withdrawal(args: argparse.Namespace) -> str | None:
    """Why his open card for the PR is withdrawn: it no longer waits on his tap, or it is a port. A wait never withdraws it: his card is corrected in place."""
    if args.tap is False:
        return args.why
    if args.port_of:
        return f"port of #{args.port_of}, which lands with no tap"
    return None


def check_waits(name: str, waits: list[str]) -> dict | None:
    """Refuse a wait that names nothing the drive can wait on, an order he has scheduled included, a reply:cci:<seq> among them naming no other drive's record on a topic, by state.anchors, note each release wait whose service never carries its PR's change by unreached, and give back his cards as last stored, which view.stored_waits reads each card's waits id from."""
    from drive import view

    if not waits:
        return None
    data = state.load(name)
    for wait in waits:
        pr = state.waited_pr(wait)
        if state.PR_KEY.fullmatch(pr) and pr not in data["prs"]:
            raise DriveError(f"--waits {wait}: the drive tracks no PR {pr}; drive pr add it to its job first")
        if (named := state.ORDER_PART.fullmatch(wait)) and named[1] in data["orders"] and not (state.part_wait(data, wait) and view.holds(data, wait, None)):
            left = [part for part in data["orders"][named[1]]["parts"] if view.holds(data, f"{named[1]}:{part}", None)]
            staging = f"; a PR's own staging test is added with its part, its wait and its tester by drive pr set {state.MONOREPO}#{found['number']} --staging \"<the cases it runs>\"" if (found := state.STAGING_PART.fullmatch(named[2])) else ""
            raise DriveError(f"--waits {wait}: name a part of {named[1]} still to be delivered, word for word, as {named[1]}:<part>: {'; '.join(left) or 'none is left'}{staging}")
        if state.PID_WAIT.fullmatch(wait) and not state.pid_waits(wait):
            raise DriveError(f"--waits {wait}: no process on this Mac holds that number now, so nothing is left to wait on")
        if wait in data["orders"] and not state.unscheduled(data, wait):
            raise DriveError(f"--waits {wait}: {wait} is {data['orders'][wait]['state']}, and an order is waited on only while he has not scheduled it, which drive order set {wait} --state waiting records")
    state.anchors(data, waits)
    cards =[wait for wait in waits if state.card_wait(data, wait)]
    found = view.live_cards(name)
    if not all(view.card_open(wait, found) for wait in cards):
        view.refresh_cards(name)
        found = view.live_cards(name)
    for wait in cards:
        if not (WAITS_CARD.fullmatch(wait) and view.holds(data, wait, found)):
            raise DriveError(
                f"--waits {wait}: name a job of this drive, the Slack link or waits id of a card of his still open, a question"
                " card or a PR card still waiting on his tap, whose waits id is merge:<n>[.<n>…]:<heads>, a PR of this drive as"
                " <owner>/<repo>#<n>, held until it merges, release:<service>:<owner>/<repo>#<n>, or with @<env> for one env as the release provider's live read"
                " names it, held until every cluster of that service, or that one env, serves a commit holding the PR's merge, either one moving to the change"
                " of the job that opened the PR once it closes unmerged, a Buildkite build by its link,"
                " https://buildkite.com/<org>/<pipeline>/builds/<n>, held until it finishes or stops at an approve step, or"
                " reply:<the permalink of a question the drive asked a teammate>, held until their reply, or reply:cci:<the seq of another drive's record>,"
                " held until that drive's next record on its topic"
            )
    unreached(name, data, waits)
    return found


def unreached(name: str, data: dict, waits: list[str]) -> None:
    """Print a note for each release wait among waits naming a service whose release never carries its PR's change, by release_reach: the PR changes only tests or CI config, which no release ships, the service is no component the release provider releases, or the paths reach components and the service is none of them, or that the read failed. The wait is kept whatever the note says, since a release wait is a wake the job set, which the watch's out_because frees, never a gate. No note for a PR release_reach reads nothing for, nor for one whose paths reach no component by that map, such as one changing only shared Pulumi code, which the release tool reaches only through the rendered rows a Buildkite job reads."""
    releases = [found for wait in waits if (found := state.RELEASE_WAIT.fullmatch(wait))]
    if not releases:
        return
    try:
        answers = release_reach(name, data, [found["pr"] for found in releases])
    except github.GithubError as error:
        print(f"note, kept: could not read which components the paths each PR of {', '.join(found[0] for found in releases)} changes reach: {error}", file=sys.stderr)
        return
    for found in releases:
        if not (answer := answers.get(found["pr"])):
            continue
        key, service = found["pr"], found["service"]
        reach = f"the repository's release provider lists the paths {key} changes reaching {', '.join(answer['touched']) or 'no component'}"
        if answer.get("tested"):
            said = f"{key} changes only tests or CI config, which no release ships, so its proof is its tests passing on dev at or after its merge"
        elif service not in answer["components"]:
            said = f"{service} is no component the release provider releases, so no release ever carries {key}; {reach}"
        elif answer["touched"] and service not in answer["touched"]:
            said = f"{service}'s release never carries {key}, since {reach}; wait on the release of the one that runs the change"
        else:
            continue
        print(f"note, kept: {found[0]}: {said}", file=sys.stderr)


def release_reach(name: str, data: dict, keys: list[str]) -> dict[str, dict]:
    """The repository's own map of what each PR among keys reaches, by the components of its release provider, given the PR's number, the paths it changes, and its head when they change rust/Cargo.lock, so the repository reads a lockfile line as reaching only the crates that link it, run in the drive's checkout moved to the head of its default branch; none for a repository whose providers name no repository, or for a PR of another repository. Its reads run eight at a time."""
    found = providers.of(data)
    if not keys or found.repo.name is None or not (ours := list(dict.fromkeys(key for key in keys if github.repo_of(key).casefold() == found.repo.name.casefold()))):
        return {}
    orca.checkout(name, data["roles"]["coordinator"]["cwd"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(ours, pool.map(lambda key: pr_reach(key, found), ours), strict=True))


def pr_reach(key: str, found: providers.Providers) -> dict:
    """What one PR reaches, by release_reach."""
    paths = github.files(key)
    if paths and all(served.TESTS.search(path) or found.repo.ci_config(path) for path in paths):
        return {"components": [], "touched": [], "dev": [], "releases": [], "staged": [], "tested": True}
    return found.release.components(key.split("#")[1], paths, github.paths([key])[key][0] if CARGO_LOCK in paths else None)


def staging_mac_box(name: str, data: dict, job: dict) -> str:
    """What a staging tester's Staging line adds for each PR its `staging test of #N before it lands` parts name whose paths reach the dashboard, by release_reach: its cases run in Iris Staging on a Mac box against the sand-next commit live on core-usw2-auto, by live_sand_next, and again at its own head when its paths reach iris-desktop or sand-next too, the two MAC_BOX_BUILDS that `tools/ci mac-box prove` builds from the commit it names, so a control calling a route the released sand-next lacks, or an Iris Staging change, fails on staging rather than for him; nothing when none reaches the dashboard. A live commit that could not be read leaves the brief kept, its line naming the read its tester makes first."""
    keys = state.tested_prs(data, job)
    reach = release_reach(name, data, keys)
    found = providers.of(data)
    if not (dashboard := [key for key in keys if key in reach and found.mac_box.affected(reach[key]["touched"])]):
        return ""
    try:
        orca.checkout(name, data["roles"]["coordinator"]["cwd"])
        live, unread = found.mac_box.live_commit(), ""
    except (DriveError, github.GithubError) as error:
        live, unread = "<that commit>", found.staging.unread(str(error))
    own = [key for key in dashboard if found.mac_box.builds & set(reach[key]["touched"])]
    return found.mac_box.case_steps(", ".join(dashboard), live) + unread + (found.mac_box.rebuild_steps(", ".join(own)) if own else "")


def staging_taps(data: dict, job: dict) -> str:
    """What a staging tester's Staging line adds for each PR it tests, by state.tested_prs, whose record holds his tap: what he tapped, when and on which head, and, for an Approve, that it waits behind the test and lands the PR once the test passes; nothing when none holds one."""
    return "".join(
        f"; he tapped {tap['label']} on {key} at {render.when(tap['at'])}, head {tap['head'][:12]}" + (STAGING_APPROVED if tap["label"] == "Approve" else "")
        for key in state.tested_prs(data, job)
        if key in data["prs"] and (tap := data["prs"][key]["tap"])
    )


def staging_reach(name: str, data: dict, keys: list[str]) -> dict[str, list[str]]:
    """Each PR among keys, the open PRs of the providers' repository waiting on his tap after flake_tap, whatever their exempt class, whose paths reach a component the repository releases through paths off its dev list, the releases of release_reach, so it lands only once its staging test passes, which state.wait_on_staging keeps in the edit that keeps the PR, by his words "a high-risk change marked as a flaky-test fix reaches my Approve card with no staging test waiting on it"; a PR changing only tools/ci and go/ci, the dev list's code, which its caller runs from a checkout and no release deploys to staging, reaches none, by his pick bb9cf91 "a change that plans like dev passes untested", never one drive order set --drop-part or his answer let merge before that test, by state.merges_first_lift, which merges first and is tested after its merge, by his answer 2eb7e76; each with the releases staging has no copy of when it has a copy of none, neither one with a staging env, by release_reach's staged, nor one of the MAC_BOX_BUILDS, which a `tools/ci mac-box prove` signs in to staging, else none, as state.reach_staging reads it, so a PR touching only such a component, such as platy, goes to his exception after the release dry run's preview of each, by his answer 67caa93, rather than to a soak that cannot deploy it; DriveError when that read fails, so its caller keeps nothing."""
    keys = [key for key in keys if not state.merges_first_lift(data["prs"].get(key))]
    try:
        reach = release_reach(name, data, keys)
    except github.GithubError as error:
        raise DriveError(f"could not read which components the paths {', '.join(keys)} changes reach, so whether it waits on a staging test is unknown: {error}; run this again") from None
    return {
        key: [] if answer["staged"] or providers.of(data).mac_box.builds & set(answer["releases"]) else answer["releases"]
        for key in keys
        if (answer := reach.get(key)) and answer["releases"]
    }


def merged_waits(data: dict, owner: dict, waits: list[str], no_waits: bool, found: dict | None) -> list[str]:
    from drive import view

    return view.stored_waits(data, [*([] if no_waits else owner["waits"]), *waits], found)


def set_pr(pr: dict, args: argparse.Namespace, answered: dict[str, list[str]] | None = None) -> None:
    if args.serves:
        pr["serves"] = args.serves
    if args.design:
        state.cite(pr, args.design, answered)
    if args.part:
        pr["parts"] = list(dict.fromkeys(args.part))
    if args.port_of:
        pr.update(port_of=args.port_of, exempt="pure port", wants_tap=False)
    if args.tap is not None:
        pr["wants_tap"] = args.tap
    if args.serves or args.design or args.part:
        pr["checker"] = None
    if args.fundamental:
        pr.update(fundamental=args.fundamental, tap=None, exempt=None, wants_tap=True)
    if args.exempt:
        pr["exempt"] = args.exempt
    if args.no_exempt:
        pr["exempt"] = None
    if args.tapped:
        pr["tap"] = state.new_tap(args.tapped, pr["head"], args.tap_source, card=[state.pr_key(pr["url"])])
        pr["fundamental"] = None
    if args.no_tapped:
        pr["tap"] = None
    if args.flaky:
        fixed = bool(state.PR_WORD.fullmatch(args.flaky[1]))
        pr["flaky"] = {"test": args.flaky[0], "fix": state.pr_key(args.flaky[1]) if fixed else None, "cause": None if fixed else args.flaky[1], "head": pr["head"], "failed_checks": pr["failed_checks"] if pr["checks"] == "fail" else [], "requeued": None}
    pr["updated"] = state.stamp()


def refuse_a_tap_of_his(name: str, key: str, tap: dict | None) -> None:
    """--no-tapped drops only a hold the drive gave itself: an Approve is his, since the drive never approves its own PR, and so is a tap whose source links his message or card, or one he pressed on his card."""
    if tap is None:
        raise DriveError(f"{key}: no tap is recorded on it, so --no-tapped has nothing to drop")
    repo, number = key.split("#")
    pressed = any(
        event["kind"] == "tap" and (event["repo"], event["number"], event["label"], event["source"]) == (repo, int(number), tap["label"], tap["source"])
        for event, _ in state.read_events(name)
    )
    if tap["label"] == "Approve":
        his = "an Approve, which only he gives"
    elif state.CARD_LINK.search(tap["source"]):
        his = f"recorded from his words at {tap['source']}"
    elif pressed:
        his = "pressed on his card"
    else:
        return
    raise DriveError(
        f"{key}: its {tap['label']} is his, {his}, and only his own word lifts it: his tap on its card, or his words recorded with"
        " --tapped <label> --tap-source <their permalink>. --no-tapped drops only a hold the drive gave itself"
    )


def github_pr(key: str) -> dict:
    from drive import card

    if not (read := card.read(key)):
        raise DriveError(f"{key}: could not read it from GitHub to check its title and body")
    return read


def take_sprite_pictures(name: str, key: str, job_id: str, jobs: list[str], workspace: str, read: dict) -> None:
    """Copy off the job's Sprite each picture a PR's Show me shows by a path that is no file here and that no job carrying the PR kept a copy of, as reads.off_sprite keeps it, since his card, drawn here from the GitHub body as the lane wrote it, names each by that copy; refused, nothing added, when the Sprite does not send one."""
    from drive import card

    show_me = card.sections(card.body_of(read)).get("show me", "")
    try:
        reads.off_sprite(name, job_id, reads.unfound(reads.shown_off_sprite(name, jobs, show_me)))
    except (DriveError, OSError, subprocess.TimeoutExpired) as error:
        raise DriveError(f"{key}: refused, nothing added: its Show me pictures did not copy off {workspace}: {error}") from None


def undrawn_show_me(name: str, read: dict, jobs: list[str]) -> list[str]:
    """Why each picture of a PR's Show me, a fence or an image link, does not draw as his card will draw it, through the same cache and naming a Sprite's picture by its copy here, since a card whose picture does not draw shows him a Show me with nothing in it."""
    from drive import card

    undrawn = []
    for kind, source, _ in picture.sources(reads.shown_off_sprite(name, jobs, card.sections(card.body_of(read)).get("show me", ""))):
        try:
            picture.shot(state.drive_dir(name) / "pictures", "show-me", kind, source, f"its Show me {kind}")
        except DriveError as error:
            undrawn.append(str(error))
    return undrawn


def unkinded_title(key: str, read: dict, port_of: int | None) -> str | None:
    """Why a PR's title fails to open with the kind his card names it by, Fix:, New:, Cleanup: or Port:, or a prefix his card reads as one, or a port's title fails to open Port:, with the retitle that fixes it; None when it opens so."""
    from drive import card

    kind, plain = card.kind_and_title(read["title"])
    if kind != "Change" and (kind == "Port" or not port_of):
        return None
    repo, number = key.split("#")
    opening, retitled = (f"Port:, since it is a port of #{port_of}", f"Port: {plain}") if port_of else ("Fix:, New:, Cleanup: or Port:", f"<Fix|New|Cleanup|Port>: {plain}")
    return f'its title "{read["title"]}" does not open {opening}, the kind of change his card names it by: retitle it with gh pr edit {number} -R {repo} --title "{retitled}"'


def put_off_steps(key: str, read: dict) -> list[str]:
    """Why each section of a PR's body his card draws fails to report the step it holds: Jev reads it alone, its heading and text in one request of its own, at settled.YES or more, as saying that step, such as the FDE impact scan its branch's own tools/ci refused, runs later, each naming the route around it, FDE_SCAN_ROUTE for the FDE impact, since a refused tool is routed around in the turn it refuses; every section read at once, and a reading in the band between settled.NO and settled.YES, or none, lets it in, said on stderr."""
    from drive import card

    shown = [(heading, text) for heading, text in card.sections(card.body_of(read)).items() if heading in card.SHOWN_HEADINGS and text.strip()]
    try:
        found = settled.jev_all([({"section": {"heading": heading, "text": text}}, {f"puts_off.s{index}": settled.PUTS_OFF}) for index, (heading, text) in enumerate(shown, 1)])
    except settled.Unheard as failure:
        print(f"drive pr add: Jev did not read {key}'s body for a step it puts off, so it goes on unread: {failure}", file=sys.stderr)
        return []
    repo, number = key.split("#")
    problems = []
    for index, (heading, text) in enumerate(shown, 1):
        puts_off = settled.reading(found.get(f"puts_off.s{index}"))
        if puts_off is None:
            print(f"drive pr add: Jev is unsure whether {key}'s {heading} section puts off the step it reports, so it goes on", file=sys.stderr)
        if not puts_off:
            continue
        route = FDE_SCAN_ROUTE if heading in card.IMPACT_HEADINGS else "the route around what refused it"
        problems.append(
            f'its {heading} section says its step runs later: "{render.cut(text.strip(), 160)}". A refused step runs in the same turn by the route around the refusal, here {route},'
            f" then its result goes in the section with gh pr edit {number} -R {repo} --body-file <file>, and the refusal is a defect you name, never a step left for later"
        )
    return problems


def note_third_person(key: str, read: dict) -> None:
    """Print, never refuse, the sections of a PR's body his card and dashboard show him that may speak of him as he or his, since they speak to him as you and only their writer knows whose a he, him or his is."""
    from drive import card

    shown = {heading: text for heading, text in card.sections(card.body_of(read)).items() if heading in card.SHOWN_HEADINGS}
    if not (found := limits.third_person(shown)):
        return
    repo, number = key.split("#")
    print(
        f"{key}: note, added all the same: its card shows him these sections, which may speak of him as he or his: {'; '.join(found)}."
        f' Where these mean him, say you and your, as in "Your Nov 11 words narrowed it", and edit the body with gh pr edit {number} -R {repo} --body-file <file>;'
        " where they mean someone else, leave them",
        file=sys.stderr,
    )


def refuse_a_port_storing_data(key: str, port_of: int) -> None:
    """A port that changes how data is stored, a migration, schema or wire format, is no pure port: its data is modeled the fundamentally right way for the target tree, a design part of the order he settles, never #N's storage carried across with no tap."""
    stored = land.stored([path for path, _ in served.files(served.change(key)["diff"])])
    if not stored:
        return
    raise DriveError(
        f"{key}: refused, nothing set: it changes {', '.join(stored)}, how its data is stored, so it is no pure port of #{port_of}."
        " A port carries #N's behavior and visuals, and its data is modeled the fundamentally right way for the target tree,"
        f" never copied from #N's storage, by {PORT_DATA_RULING}. That model is a design part of the order: a design job puts it"
        " on his card, and the PR serves the order with drive pr set <url> --tap --serves <o> --design asked:<his answer>"
    )


def refuse_an_untracked_original(data: dict, key: str, port_of: int) -> None:
    """A pure port carries work that merged or that he approved: its original, #port_of in its own repository, is a PR the drive tracks, whose merge or his Approve on it state.port_hold waits on, or one GitHub reads merged."""
    from drive import card

    original = f"{github.repo_of(key)}#{port_of}"
    if original in data["prs"] or (card.read(original) or {}).get("state") == "MERGED":
        return
    raise DriveError(
        f"{key}: refused, nothing set: #{port_of}, the PR it ports, is not merged on GitHub and the drive does not track it, so nothing would hold this port"
        f" until #{port_of} merges or he approves it. Track #{port_of} first with drive pr add {state.pr_url(original)} --job <j>, or name the merged PR it ports"
    )


def refuse_an_unexempt(name: str, key: str, exempt: str, done: str) -> None:
    """Refuse an exempt class the PR is not in, by land.outside, the class check the lander runs before it lands an exempt PR, on the PR as GitHub has it now, before anything is kept, so no class the lander would refuse takes Approve off his card, by his ruling to keep a lane's exempt word and check the class; a flake fix outside test and fixture code keeps his tap instead, by flake_tap."""
    if why := land.outside(state.load(name), exempt, key, served.change(key)):
        raise DriveError(f'{key}: refused, nothing {done}: it is no "{exempt}": {why}, by the class check the lander runs before it lands an exempt PR')


def path_class(data: dict, key: str, job: dict, read: dict) -> str | None:
    """The exempt class the files a PR's GitHub read lists put it in when the drive first registers it: ci-only when it lists any and land.not_ci finds none outside CI configuration and CI tooling, so a CI fix lands with no tap and no session names its class by hand, by his words "any CI fix PRs uoiu can merge without getting a tp from me"; None for a PR the drive tracks, which keeps the class it has, someone else's, which only his tap lands, and a revert on an order an incident is open on, which no incident approves."""
    paths = [changed["path"] for changed in read["files"]]
    if key in data["prs"] or not paths or land.not_ci(data, paths) or read["author"]["login"].casefold() != state.HIS_LOGIN:
        return None
    if state.open_incidents(data, [job["order"], *job["carries"]]) and land.REVERT.search(f"{read['title']}\n{read['body']}"):
        return None
    return state.CI_ONLY


def flake_tap(key: str, wants_tap: bool) -> bool:
    """Whether a flake fix waits on the tap its PR would wait on: only while it changes a file outside test and fixture code, as GitHub lists its files now, since a flake fix lands with no tap only in test or fixture code."""
    return wants_tap and any(not served.TESTS.search(path) for path in github.files(key))


def refuse_an_exempt_revert(data: dict, key: str, order_id: str, read: dict, adding: str = "") -> None:
    """A PR whose title or body says it reverts a change takes that change out, which no incident order approves: it lands only on his tap, on a card stating the bug in that change."""
    if not land.REVERT.search(f"{read['title']}\n{read['body']}"):
        return
    record = data["orders"][order_id]["note"] or "none noted yet"
    raise DriveError(
        f'{key}: refused, it reverts a change, and a revert never lands as an "{INCIDENT_EXEMPTION}": no incident order approves it,'
        f" only his tap does. {adding}Draw his tap card with drive pr set {state.pr_url(key)} --tap --serves {order_id} --design none,"
        f" its body stating the bug in the change it takes out, with that change's PR and evidence, from {order_id}'s record ({record})"
    )


def tap_verdict(data: dict, pr: dict | None) -> tuple[bool, str]:
    if pr is None:
        return True, "not a drive PR"
    if pr["hold_label"]:
        return False, "held by the hold label on GitHub"
    if original := state.port_hold(data, pr):
        return False, f"a pure port of {original}, which waits on its merge or his Approve on it"
    if not pr["wants_tap"] and state.merges_untapped(state.pr_key(pr["url"])):
        return True, f"wants no tap: the drive lands a {state.NO_TAP_OWNER}/* PR with none"
    if state.exempted(pr):
        return True, f"exempt: {pr['exempt']}"
    tap = pr.get("tap")
    if tap and tap["label"] == "Approve":
        return True, f"approved by him at {render.when(tap['at'])}"
    if tap:
        return False, f"held by him at {render.when(tap['at'])}"
    if pr.get("fundamental"):
        return False, f"no tap since it changed: {pr['fundamental']}"
    return False, "no tap from him yet"


def pr_plan(args: argparse.Namespace) -> int:
    """Print the plan a production release of the components a PR reaches with no copy on staging would apply, by the release provider's dry_run, the proof its builder's DONE carries in place of a staging test; refused for a PR waiting on no exception."""
    data = state.load(state.resolve(args.drive))
    key = state.pr_key(args.url)
    if not (excepted := find(data["prs"], key, "PR")["exception"]):
        raise DriveError(f"{key}: refused, it waits on no exception: only a PR reaching components staging runs no copy of carries a production dry-run plan")
    plan = providers.of(data).release.dry_run(key.rpartition("#")[2], excepted["components"])
    return emit(args, plan, {"key": key, "components": excepted["components"], "plan": plan})


def pr_tapped(args: argparse.Namespace) -> int:
    key = state.pr_key(args.url)
    data = state.load(state.resolve(args.drive))
    may_land, why = tap_verdict(data, data["prs"].get(key))
    emit(args, f"{key}: {why}", {"key": key, "may_land": may_land, "why": why})
    return 0 if may_land else 1


def pr_land(args: argparse.Namespace) -> int:
    keys = [state.pr_key(url) for url in args.urls]
    name = state.resolve(args.drive)
    if args.outcome:
        outcome = land.record(name, keys, args.outcome, args.below, dict(args.asked))
    elif args.dequeue:
        outcome = land.dequeue(name, keys, args.dequeue)
    else:
        outcome = land.run(name, keys, args.dry_run)
    return emit(args, f"{', '.join(keys)}: {outcome}", {"keys": keys, "outcome": outcome})


def pr_ci(args: argparse.Namespace) -> int:
    key = state.pr_key(args.url)
    repo = key.split("#")[0]
    if not (sha := github.merge_commit(key)):
        raise DriveError(f"{key} is not merged, so no CI run on its default branch judges it")
    runs = install.ci_verdict(repo, sha)
    lines, unpassed = [line for line, _ in runs], [line for line, passed in runs if not passed]
    verdict = f"not passed: {'; '.join(unpassed)}" if unpassed else "passed"
    emit(args, "\n".join([*lines, f"{key} merged at {sha[:12]}: {verdict}"]), {"pr": key, "sha": sha, "passed": not unpassed, "runs": lines})
    return 1 if unpassed else 0


def pr_published(args: argparse.Namespace) -> int:
    """Run a proof's command against the files the newest Pages deployment holding a merged PR published, served on this Mac by published.served, its URL in published.SERVED_ENV, after the lines saying what those files are; the command's exit status."""
    if not (command := args.command[1:] if args.command[:1] == ["--"] else args.command):
        raise DriveError("name the command that clicks the published pages after --, such as -- python3 clicks.py")
    with published.served(state.pr_key(args.url)) as (root, lines):
        print("\n".join(lines), flush=True)
        return subprocess.run(command, env=os.environ | {published.SERVED_ENV: root}).returncode


@children.capped
def pr_check(args: argparse.Namespace) -> int:
    """Judge one PR against his words as the watch's check."""
    key, name = state.pr_key(args.url), state.resolve(args.drive)
    outcome = served.verdict(name, key)
    return emit(args, f"{key}: {outcome}", {"key": key, "outcome": outcome})


def answer(args: argparse.Namespace) -> int:
    """Record an answer or ruling of his against the cards asking him, orders and parts undelivered and PRs not yet live that it could change, those of the job and order of the card it answers, those its words name, and those of each open order a pick of his binds that the card asked him to lift, and the jobs waiting on their orders: refuse until each card, order, part and PR is marked standing, settled, dropped, superseded or reopened by it and each job sent his words or untouched, either mark taking his words off the texts that job holds, show his answer on each card it settles, redraw each it settles in part to the ASK its job keeps for what is left, withdraw each card it drops without stopping its job, drop each order it drops, or whose every part it drops, stopping the live jobs whose order it is and marking in cc-notes each pick binding it superseded by his answer, take each other part it drops off its order, jobs and PRs, close on GitHub each open PR it drops, settle each superseded card in place, withdraw each card carrying a reopened PR until its builder's READY says how his words are met, close on GitHub an open PR superseded outright, and, unmarked, on its first record or any run after, mark in cc-notes each standing answer of his on the features it reaches that Opus reads it replacing, by replaced; a --title on an answer the drive keeps rewords its question in cc-notes, by settled.retitled, so the release gate reads the question as it reads now; each order and part of an open order the answer lists stays listed and markable until it is marked, whatever a builder's DONE or relaunch makes state.delivered read, and any other card, part or PR still asking him may be marked too. --approves keeps his approval of the orders and keys it names on the answer's row and goes ahead on what it covers now, by `approve`, marking each covered key the answer lists unmarked. Words his message or card answer at --link does not hold, or words given with no --link, which keeps the answer at terminal, are recorded as the session's paraphrase, a new one labelled settled.PARAPHRASE_LABEL, said on stderr."""
    from drive import view

    name = state.resolve(args.drive)
    found = view.live_cards(name)
    if found is None:
        raise DriveError("his cards could not be read, so nothing can say what this answer leaves asking him; the watch rereads them every minute, run this again then")
    data = state.load(name)
    asked = superseded.asking(data, found, (args.link or [None])[0])
    cwd = data["roles"]["coordinator"]["cwd"]
    for label in args.label:
        if label.startswith(served.CODE_LABEL) and (folder := served.directory(path := served.labelled(label), cwd, [])) != path:
            raise DriveError(
                f"refused, nothing recorded: --label {label} names a file, and a code: label names the directory whose code his ruling governs, the one whose AGENTS.md the PR"
                f" check asks to hold his words: label it {served.CODE_LABEL}{folder}"
            )
    cited = settled.shown(args.id, cwd) if args.id else None
    if cited and settled.paraphrased(cited):
        raise DriveError(f"{args.id} is a session's paraphrase, labelled {settled.PARAPHRASE_LABEL}, which settles no card, order or release: record his own words with --title, --words and --link")
    if cited:
        args.id = cited["id"]
    if cited and "superseded_by" in cited:
        raise DriveError(
            f"refused, nothing recorded or marked: cc-notes marks his answer {args.id} superseded by {', '.join(cited['superseded_by'])}, so it owes no job his"
            " words and leaves nothing asking him: drive answer his newer answer"
        )
    if args.id not in data["answers"] and (unowed := rewrite_refusal(data, args, cited)):
        raise DriveError(unowed)
    if args.id in data["answers"] and (args.words or args.link):
        raise DriveError(
            f"{args.id} is recorded already, at {data['answers'][args.id]['link']}, so --words and --link would change nothing: a later message of his is an"
            " answer of its own, recorded with --title, --words and --link, his messages in a row go in one first call, a --link for each in order,"
            " and --title alone rewords the question it answers"
        )
    unlinked = not args.link
    if unlinked and args.id not in data["answers"]:
        args.link = [settled.TERMINAL]
    if args.id is None:
        if not (args.title and args.words):
            raise DriveError('name his answer\'s cc-notes id, or pass --title "<the question>" with --words, and --link where he said them, to record it in cc-notes first')
        if unsaid := settled.unsaid([args.words], args.link, found, data):
            noted = settled.recorded(name, args.title, args.words, [*args.label, settled.PARAPHRASE_LABEL], " and ".join(args.link), cwd)
            print(f"drive answer: {unsaid}; recorded as {noted[:7]}, the session's paraphrase, labelled {settled.PARAPHRASE_LABEL}, which settles no card, order or release, since only his own words do", file=sys.stderr)
            return 0
        lifts = settled.lifting(question for job in data["jobs"].values() for question in job["questions"] if superseded.ended_at(args.link[0], question))
        args.id = settled.recorded(name, args.title, args.words, args.label, " and ".join(args.link), cwd, lifts)
    drive = args.about == state.ABOUT_DRIVE or bool(ON_THE_DRIVE & {*(cited["tags"] if cited else args.label)})
    marking = args.approves or args.stands or args.settled or args.dropped or args.superseded or args.reopened or args.others_stand or args.untouched
    done: list[str] = []
    if args.id not in data["answers"]:
        if marking:
            raise DriveError(
                f"cc-notes holds {args.id}; the drive keeps no link for it: run drive answer {args.id} alone first, with --link <the permalink of his message or the card he tapped>"
                " where you have it, which keeps his words at that link, its text above its Notes: line or, where that text holds more than his words, his message there"
                " that sits inside it, and lists each card still asking him and each PR not yet live,"
                " and mark them once you have read it"
            )
        if cited:
            if args.words:
                unsaid = settled.unsaid([args.words], args.link, found, data)
            else:
                args.words, unsaid = settled.his_at(cited["body"], args.link, found, data)
            if unsaid:
                raise DriveError(f"{args.id}: not kept, since only his own words settle a card, order or release: {unsaid}")
            args.words = args.words or settled.his(cited["body"])
        bound = superseded.binding(data, args.link[0], asked, cwd)
        def answer_kept(data: dict):
            superseded.record(data, args.id, args.words, args.link[0], asked, bound, state.stamp(), drive)
            return data

        data = state.update(name, None, answer_kept)
    elif drive:
        def answer_write3(data: dict):
            superseded.tooling_only(data, data["answers"][args.id], superseded.listed(data, data["answers"][args.id], asked))
            return data

        data = state.update(name, None, answer_write3)
    if cited and args.title and settled.titled(args.title) != cited["title"]:
        done.append(f'his answer {args.id} now answers "{render.cut(settled.retitled(cited, args.title, cwd), 200)}" in cc-notes, where it answered "{render.cut(cited["title"], 200)}"')
    if cited:
        settled.labelled(cited, args.label, cwd)
    if not marking:
        done += replaced(name, args.id, asked, cwd)
        def answer_write2(data: dict):
            ended = superseded.answer_cards(data, args.id, data["answers"][args.id])
            return data, ended

        data, ended = state.update(name, None, answer_write2)
        done += superseded.follow(name, ended) + superseded.supersede_taps(name, ended)
        kept = data["answers"][args.id]
        areal = state.area_orders(data, [*(cited["tags"] if cited else []), *args.label])
        if kept["words"] and (restarted := restart_read(name, sorted(reached(data, kept, asked) | set(areal)), kept["words"], kept["link"])):
            done += restarted
            data = state.load(name)
            asked = superseded.asking(data, found, kept["link"])
    kept = data["answers"][args.id]
    asked = superseded.listed(data, kept, asked)
    open_keys = superseded.unmarked(kept, asked)
    named = [*args.stands, *args.dropped, *(key for key, _ in [*args.settled, *args.superseded, *args.reopened])]
    told = args.untouched
    if twice := sorted({key for key in [*named, *told] if [*named, *told].count(key) > 1}):
        raise DriveError(f"{', '.join(twice)}: marked twice; mark each card, part or PR once, standing, settled, dropped, superseded or reopened, and each job once, sent or untouched")
    if stray := [key for key in named if key not in superseded.markable(kept, asked)]:
        raise DriveError(f"{'; '.join(superseded.unmarkable(args.id, kept, asked, key) for key in stray)}\n{superseded.listing(args.id, kept, open_keys, asked, data)}")
    hearing = [job_id for key, _ in args.settled if (job_id := asked[key]["job"]) and data["jobs"][job_id]["state"] not in state.FINISHED_JOB_STATES]
    if stray := [job_id for job_id in told if job_id not in [*superseded.unheard(data, kept), *hearing]]:
        raise DriveError(
            f"{', '.join(stray)}: not a job waiting on an order {args.id} touches that is unmarked, with an agent or lane; a job with"
            f" neither hears his words from the launch that starts it\n{superseded.listing(args.id, kept, open_keys, asked, data)}"
        )
    refusals = [
        *(superseded.settled_refusal(data, kept, key, asked[key], file) for key, file in args.settled),
        superseded.part_refusal(asked, [key for key, _ in [*args.superseded, *args.reopened]]),
        superseded.dropped_refusal(asked, args.dropped),
        superseded.stack_refusal(asked, [*args.dropped, *(key for key, _ in args.superseded)]),
        *(superseded.fold_refusal(data, key, asked[key], into) for key, into in args.superseded if into),
        *(superseded.reopen_refusal(data, key, asked[key], by) for key, by in args.reopened),
        args.others_stand and superseded.others_refusal(args.id, kept, asked, named, data),
    ]
    if why := next((refusal for refusal in refusals if refusal), None):
        raise DriveError(why)
    covering = {key: key if key in data["orders"] else (data["prs"].get(key) or asked.get(key) or {}).get("order") for key in args.approves}
    if stray := [key for key, order_id in covering.items() if not order_id or data["orders"][order_id]["state"] in state.CLOSED_ORDER_STATES]:
        raise DriveError(f"{', '.join(stray)}: no open order, nor a card, part or PR of one, so his answer {args.id} approves nothing there: name an open order or a key drive answer lists\n{superseded.listing(args.id, kept, open_keys, asked, data)}")
    if args.approves and (held := (kept["approves"] or {}).get("held")):
        raise DriveError(f"refused, nothing approved: his Hold on {held['pr']} at {held['at']} ended his approval {args.id}, so only a newer answer of his approves again")
    def marked(data: dict) -> dict:
        data["answers"][args.id]["cards"].update({key: {"mark": "stands"} for key in args.stands})
        if args.approves:
            approves = data["answers"][args.id]["approves"] or {"orders": [], "keys": [], "on": [], "asked": cited["title"], "said": cited["body"], "held": None}
            approves["orders"] = list(dict.fromkeys([*approves["orders"], *(key for key in args.approves if key in data["orders"])]))
            approves["keys"] = list(dict.fromkeys([*approves["keys"], *(key for key in args.approves if key not in data["orders"])]))
            approves["on"] = list(dict.fromkeys([*approves["on"], *covering.values()]))
            data["answers"][args.id]["approves"] = approves
        for job_id in told:
            superseded.heard(data, job_id, data["answers"][args.id], "untouched")
        return data

    data = state.update(name, None, marked)
    cleared: set[str] = set()
    try:
        done += [settle_card(name, args.id, kept, key, asked[key], file) for key, file in args.settled]
        if args.approves:
            done += approve(name, args.id, asked, named)
        dropping = superseded.whole(data, asked, args.dropped)
        bound = superseded.binding(data, kept["link"], asked, cwd) if dropping else {}
        for order_id, keys in dropping.items():
            for pick in bound.get(order_id, []):
                settled.supersede(pick, args.id, cwd)
                done.append(f"his pick {pick}, which bound {order_id}, is superseded by {args.id} in cc-notes, so ccn answer match no longer reads it as standing")
            def dropped_order(data: dict) -> tuple[dict, list[str]]:
                data["answers"][args.id]["cards"].update({key: {"mark": "dropped", "job": None, "card": None} for key in keys})
                if order_id in superseded.whole(data, asked, keys):
                    return data, superseded.drop_order(data, args.id, order_id)
                return data, [line for key in keys for line in superseded.drop(data, args.id, order_id, asked[key]["title"])]

            data, lines = state.update(name, None, dropped_order)
            done += lines
        for key in args.dropped:
            entry = asked[key]
            if entry["kind"] in ("order", "part") and entry["order"] in dropping:
                continue
            if entry["kind"] == "part":
                def dropped_part(data: dict) -> tuple[dict, list[str]]:
                    data["answers"][args.id]["cards"][key] = {"mark": "dropped", "job": None, "card": None}
                    return data, superseded.drop(data, args.id, entry["order"], entry["title"])

                data, lines = state.update(name, None, dropped_part)
                done += lines
                continue
            if (card := entry["card"]) and card not in cleared or entry["kind"] == "question" and not card:
                ended_on(name, state.new_close("withdrawn", kept["words"], kept["link"], answer=args.id), key, card)
                cleared.add(card)
            if superseded.stops_open_pr(data, key, None):
                superseded.close(key)
            def dropped_card(data: dict) -> tuple[dict, list[str]]:
                data["answers"][args.id]["cards"][key] = {"mark": "dropped", "job": entry["job"], "card": card}
                if entry["job"] and data["jobs"][entry["job"]]["state"] not in state.FINISHED_JOB_STATES:
                    superseded.owe(data, args.id, entry["job"])
                return data, [*([f"{key}: its card {card} now shows his words and a link to them, with no buttons, its question withdrawn"] if card else []), *superseded.unasked(data, key, entry)]

            data, lines = state.update(name, None, dropped_card)
            done += lines
        for mark, key, job_id in [*(("superseded", *pair) for pair in args.superseded), *(("reopened", *pair) for pair in args.reopened)]:
            card = asked[key]["card"]
            if mark == "reopened":
                def answer_write4(data: dict):
                    data["answers"][args.id]["cards"][key] = {"mark": mark, "job": job_id, "card": card}
                    return data

                data = state.update(name, None, answer_write4)
                done += [*rework_prs(name, [{"key": key, "words": kept["words"], "link": kept["link"]}], args.id), f"{key} reopened by his answer {args.id}: {job_id} reworks it to his words, so brief {job_id} with them"]
                continue
            if card and card not in cleared or asked[key]["kind"] == "question" and not card:
                ended_on(name, state.new_close("superseded", kept["words"], kept["link"], answer=args.id), key, card)
                cleared.add(card)
            if superseded.stops_open_pr(data, key, job_id):
                superseded.close(key)
            def superseded_card(data: dict) -> tuple[dict, list[str]]:
                data["answers"][args.id]["cards"][key] = {"mark": mark, "job": asked[key]["job"], "into": job_id, "card": card}
                return data, [*([f"{key}: its card {card} now shows his words and a link to them, with no buttons"] if card else [f"{key}: no card was drawn for it"]), *superseded.supersede(data, args.id, key, asked[key], job_id)]

            data, lines = state.update(name, None, superseded_card)
            done += lines
    finally:
        if args.approves or args.settled or args.dropped or args.superseded or args.reopened:
            view.refresh_cards(name)
    def answer_write(data: dict):
        kept = data["answers"][args.id]
        if args.others_stand:
            kept["cards"].update({key: {"mark": "stands"} for key in superseded.unmarked(kept, asked)})
        return data, kept

    data, kept = state.update(name, None, answer_write)
    data = watch.hear_sent(name, set(superseded.unheard(data, kept)))
    kept = data["answers"][args.id]
    if (left := superseded.unmarked(kept, asked)) or superseded.unheard(data, kept):
        raise DriveError("\n".join([*done, superseded.listing(args.id, kept, left, asked, data)]))
    marked = [mark["mark"] for mark in kept["cards"].values() if mark]
    heard = [mark["mark"] for mark in kept["jobs"].values() if mark]
    settles = "".join(f", {marked.count(mark)} {mark}" for mark in ("settled", "redrawn", "dropped") if mark in marked)
    line = f"his answer {args.id} recorded: {marked.count('stands')} standing{settles}, {marked.count('superseded')} superseded, {marked.count('reopened')} reopened by it"
    if approves := kept["approves"]:
        line += f"; it approves {', '.join([*approves['orders'], *approves['keys']])} until those orders close, a newer answer of his supersedes it or he taps Hold on a PR it covers"
    if heard:
        line += f"; {heard.count('sent')} waiting {'job' if heard.count('sent') == 1 else 'jobs'} sent his words, {heard.count('untouched')} untouched"
    return emit(args, "\n".join([line, *done]), {"id": args.id, **kept})


def replaced(name: str, answer_id: str, asked: dict[str, dict], cwd: str) -> list[str]:
    """Mark in cc-notes each standing answer of his recorded before his answer, on the feature of each order it reaches, by brief.spec, that Opus reads his answer's words as replacing, at settled.YES or more, in one request, so every brief's Decisions that bind and every judge reads it as replaced, as his order to design something from scratch replaces his rulings on the design it starts over; one read in the band between is said on stderr with the command marking it, and no reading leaves each standing, said on stderr."""
    data = state.load(name)
    kept = data["answers"][answer_id]
    features = {tuple(state.job_feature(data, {"order": order_id, "carries": []})): order_id for order_id in sorted(reached(data, kept, asked))}
    new = settled.shown(answer_id, cwd)
    words = {entry["id"]: entry for order_id in features.values() for entry in brief.unreplaced(brief.spec(name, data, {"order": order_id, "carries": []})) if entry["id"] and entry["id"] not in data["orders"] and state.parse_time(entry["at"]) < state.parse_time(new["created_at"])}
    if not words or not kept["words"]:
        return []
    try:
        read = settled.replacing(new["title"], kept["words"], list(words.values()))
    except settled.Unheard as failure:
        print(f"drive answer: Opus did not read which of his standing answers {answer_id} replaces, so each stands: {failure}", file=sys.stderr)
        return []
    done = []
    for word_id, sure in read.items():
        old = words[word_id]
        if (found := settled.reading(sure)) is None:
            print(f'drive answer: Opus is unsure whether {answer_id} replaces his answer {old["id"]} to "{render.cut(old["asked"], 200)}"; when it does, run ccn answer supersede {old["id"]} --by {answer_id}', file=sys.stderr)
        if found:
            settled.supersede(old["id"], answer_id, cwd)
            done.append(f'his answer {old["id"]} to "{render.cut(old["asked"], 200)}" is superseded by {answer_id} in cc-notes, as Opus reads his new words replacing it, so each brief written from now and each judge reads it as replaced')
    return done


def reached(data: dict, kept: dict, asked: dict[str, dict]) -> set[str]:
    """The orders an answer of his reaches: that of each card it lists, by the job that drew it, and that of each job it lists."""
    return {entry["order"] for key in kept["cards"] if (entry := asked.get(key)) and entry["order"]} | {data["jobs"][job_id]["order"] for job_id in kept["jobs"]}


def ended_on(name: str, closed: dict, key: str, card: str | None, shown: str = "his words") -> list[str]:
    """End as this close says, answered, withdrawn or superseded by his answer or decided under his ruling, each open question drawn on the card a mark names, or kept under its waits id with no card drawn yet, which then never draws, and its card follows, showing the close's words with no buttons, said as `shown`; a card no question of the drive is drawn on, one asked outside any job, is closed in place. What its card shows, a line each."""
    from drive import superseded, view

    def ended_on_write(data: dict):
        drawn = view.drawn_on(data, key, *filter(None, [card]))
        ended = [each for job_id, question in drawn if question["closed"] is None for each in superseded.end(data, job_id, question, closed)]
        return drawn, ended

    drawn, ended = state.update(name, None, ended_on_write)
    if drawn:
        return superseded.follow(name, ended, shown)
    superseded.settle(card, closed, settled=closed["how"] == "answered")
    return []


def settle_card(name: str, answer_id: str, kept: dict, key: str, entry: dict, file: Path | None) -> str:
    """Show his answer in place on the question card it decides, settling its job's question, or mark the card it decides in part redrawn to the ASK its job keeps asking what is left, which `drive job ask --redraws` drew under the card's waits id; either way the card's job, while live, waits to hear his words. What was done, one line."""
    job_id, card = entry["job"], entry["card"]
    if not file:
        ended_on(name, state.new_close("answered", kept["words"], kept["link"], answer=answer_id), key, card)
    def settle_card_write(data: dict):
        job = data["jobs"][job_id]
        data["answers"][answer_id]["cards"][key] = {"mark": "redrawn" if file else "settled", "job": job_id, "card": card, **({"ask": str(file)} if file else {})}
        if job["state"] not in state.FINISHED_JOB_STATES:
            data["answers"][answer_id]["jobs"].setdefault(job_id, None)

    state.update(name, None, settle_card_write)
    if file:
        return f"{key}: its card {card} is redrawn in place to {file}, asking only what his answer {answer_id} leaves open, as drive job ask {job_id} --redraws {key} drew it"
    shown = f"its card {card} now shows his answer {answer_id} with no buttons" if card else f"no card draws for it, his answer {answer_id} ending its question"
    return f"{key}: {shown}, settling {job_id}'s question; {builds_on(state.load(name), job_id)}"


def approve(name: str, answer_id: str, asked: dict[str, dict], named: list[str]) -> list[str]:
    """Go ahead on what his approval covers, as `drive answer --approves` kept it: end each open question his card draws on a covered order or key on its recommended option, as --settled shows his answer on it, but one kept unsettled, which asks him to lift words of his; then take his Approve, from the answer's link, on each open PR it covers that his card would ask his tap on, as `drive pr set --tapped Approve --tap-source` keeps his, but one the check against his words objects to at its head, by his "given my constraints". Each covered key the answer lists unmarked is marked settled or standing, and each step shows on the drive's dashboard and records alone. What was done, a line each."""
    from drive import view

    data = state.load(name)
    kept = data["answers"][answer_id]
    approves = kept["approves"]
    done = []
    deciding = [
        (job_id, job["order"], question)
        for job_id, job in data["jobs"].items()
        for question in view.open_questions(job)
        if question["file"] and not question["unsettled"] and (question["card"] or question["waits"] and not question["refused"]) and state.approval_covers(approves, question["waits"], job["order"])
    ]
    for job_id, order_id, question in deciding:
        option = limits.question_problems(limits.ask_block(Path(question["file"]).read_text()), kept=True)[0].recommended
        closed = settled.approved(option, answer_id, kept["link"])
        key = question["waits"] or view.message_key(question["card"])
        ended_on(name, closed, key, question["card"], f'"{render.cut(closed["words"], 300)}" linked to {kept["link"]}')
        def decided(data: dict) -> None:
            if key in asked and key not in named:
                data["answers"][answer_id]["cards"].setdefault(key, {"mark": "settled", "job": job_id, "card": question["card"]})
            if data["jobs"][job_id]["state"] not in state.FINISHED_JOB_STATES:
                data["answers"][answer_id]["jobs"].setdefault(job_id, None)

        state.update(name, None, decided)
        shown = f"its card {question['card']} now shows it" if question["card"] else "no card draws for it"
        done.append(f'{key}: decided on "{render.cut(option, 200)}" under his approval {answer_id[:7]}, {shown}; {builds_on(state.load(name), job_id)}')
    def approved(data: dict) -> list[str]:
        tapped = [key for key, pr in data["prs"].items() if state.approval_covers(approves, key, pr["order"]) and state.asks_tap(data, pr) and not state.against(pr)]
        for key in tapped:
            pr = data["prs"][key]
            pr.update(tap=state.new_tap("Approve", pr["head"], kept["link"], card=[key], approval=answer_id), fundamental=None, updated=state.stamp())
        for key in [key for key, entry in asked.items() if key not in named and not data["answers"][answer_id]["cards"].get(key) and state.approval_covers(approves, key, entry["order"])]:
            data["answers"][answer_id]["cards"][key] = {"mark": "stands"}
        return tapped

    tapped = state.update(name, None, approved)
    for key in tapped:
        done.append(f"{key}: his Approve taken from his approval {answer_id[:7]} at {kept['link']}, so no card asks his tap")
    return done


def builds_on(data: dict, job_id: str) -> str:
    """Who builds on his answer to the job's question: each live builder, codex or design job on the job's parts of its order, else an ORPHAN line per part naming the builder to start, on the branch a finished job there built when one did, since settling a card starts no job."""
    order_id = data["jobs"][job_id]["order"]
    lines = []
    for part in state.job_parts(data["jobs"][job_id], order_id):
        if live := [other for other, job in state.carriers(data, order_id, part) if job["kind"] in state.WORK_KINDS and job["state"] not in state.FINISHED_JOB_STATES]:
            lines.append(f"{' and '.join(live)} {'builds' if len(live) == 1 else 'build'} on his words")
            continue
        branch = next((job["branch"] for _, job in reversed(state.part_jobs(data, order_id, part)) if job["branch"]), None)
        on = f" --branch {shlex.quote(branch)}" if branch else ""
        lines.append(f'ORPHAN {order_id} "{render.cut(part, 80)}": no live job builds it; start one now with drive job add builder --order {order_id} --part {shlex.quote(part)} --goal \'<what it builds>\'{on}')
    return "; ".join(lines)


def rewrite_refusal(data: dict, args: argparse.Namespace, cited: dict | None) -> str | None:
    """Why a ruling of his may not be recorded yet: its skill rewrite is owed the moment it is, so an open "Drive tooling:" order, the meta session's, carries it, and the watch chases that order until its builder is done."""
    labels = cited["tags"] if cited else args.label
    if RULING not in labels:
        return None
    order = data["orders"].get(args.rewrite or "")
    if order and order["text"].startswith(state.TOOLING) and order["state"] not in state.CLOSED_ORDER_STATES:
        return None
    return (
        "refused, nothing recorded: a ruling's skill rewrite is owed the moment the ruling is recorded, so name the"
        " meta session's open order carrying it with --rewrite <o>, adding it first with"
        f" drive order add '{state.TOOLING} self-retro: \"<his words, verbatim>\"' --title \"<a few words>\" when none holds it, which the watch chases until"
        " its builder is done; his ruling 0c59046 went unbuilt for hours while nothing said its rewrite was owed"
    )


def superseded_mark(text: str) -> tuple[str, str | None]:
    key, _, how = text.rpartition("=")
    if not key or not how:
        raise argparse.ArgumentTypeError("say <key>=stop, or <key>=<the live job that carries its work on>")
    return key, None if how == "stop" else how


def settled_mark(text: str) -> tuple[str, Path | None]:
    key, _, file = text.partition("=")
    if not key:
        raise argparse.ArgumentTypeError("say <card key>, or <card key>=<the ASK its job keeps asking only what his answer leaves open>")
    return key, Path(file).expanduser().resolve() if file else None


def reopened_mark(text: str) -> tuple[str, str]:
    key, _, job_id = text.rpartition("=")
    if not key or not job_id:
        raise argparse.ArgumentTypeError("say <PR key>=<the live job that reworks it to his words>")
    return key, job_id


def heard(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    if args.last:
        if not args.why or args.now or args.away or args.repeats or args.part:
            raise DriveError("--last takes back what the drive heard after his last word or tap: it needs --why <whose taps or words they were>, and no --now, --away, --repeats or --part")
        return emit(args, "unheard", watch.unhear(name, args.last, args.why))
    if not (args.now or args.away or args.repeats or args.part):
        owner = state.load(name)["owner"]
        reading = render.owner_reading(owner)
        return emit(args, f"He is {reading}" if reading else "never heard: the drive has heard no word or tap of his", owner)
    if args.repeats or args.part:
        refuse_an_unheld_repeat(name, state.load(name), args.repeats, args.part)
    return emit(args, "away" if args.away else "heard", watch.hear(name, state.now(), args.away))


def page_owner(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    data = state.load(name)
    if args.order:
        find(data["orders"], args.order, "order")
    users = args.users or (data["orders"][args.order]["users"] if args.order else None)
    if not users:
        raise DriveError(
            f"refused, nobody paged: {state.PAGE_RULE}. Post the read showing real users failing as a cci record and pass its seq with"
            " --users <seq>; with none, the incident stays open and its jobs work on without paging him"
        )
    try:
        paged = watch.page_owner(name, data, args.why, f"{args.why}\nreal users failing: record {users}")
    except reads.ReadError as error:
        raise DriveError(f"refused, nobody paged: {error}") from None
    if args.order:
        def page_owner_write(data: dict):
            data["orders"][args.order].update(paged=state.stamp(), users=users)

        state.update(name, None, page_owner_write)
    return emit(args, paged, {"paged": paged, "order": args.order})


def answer_message(args: argparse.Namespace) -> int:
    from drive import answer

    name = state.resolve(args.drive)
    found = answer.answer(name, args.link, args.words, args.dry_run)
    line = f"ANSWERED {found['answered']}" if found["answered"] else f"{'DRY ' if args.dry_run else ''}{'WOULD ANSWER: ' + found['text'] if found.get('text') else 'ESCALATE ' + found['why']}"
    return emit(args, line, found)


def report_post(args: argparse.Namespace) -> int:
    from drive import talk

    return emit(args, talk.report_post(state.resolve(args.drive), args.seq), {"seq": args.seq})


def say_record(args: argparse.Namespace) -> int:
    from drive import talk

    return emit(args, talk.record_kept(state.resolve(args.drive), Path(args.receipt)), {"receipt": args.receipt})


def mac_boxes_down(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    data = state.load(name)
    orca.checkout(name, data["roles"]["coordinator"]["cwd"])
    boxes = providers.of(data).mac_box
    codes = []
    for job_id in args.jobs:
        try:
            codes.append(boxes.down(job_id, f"{name}/{job_id}"))
        except providers.ProviderError as error:
            print(f"{job_id}: {error}", file=sys.stderr)
            codes.append(1)
    return max(codes)


def mac_boxes_reap(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    data = state.load(name)
    orca.checkout(name, data["roles"]["coordinator"]["cwd"])
    boxes = providers.of(data).mac_box
    boxes.reap([box for box in boxes.warm_boxes() if state.box_wanted(data, box)])
    return 0


def mac_boxes_hand(args: argparse.Namespace) -> int:
    """Give each queued job, in the order given, a box named <job>-mac by the provider's take, a warm one first, else a new host's; a job of a priority order the pool cannot serve takes the box of a waiting job of no priority order, which the provider's down gives back to the pool unless something runs on it, and that job takes one again when it resumes; marks each job's box wait handed, as `drive mac-box handed` does, so the watch frees it; it stops at the first job no host serves."""
    name = state.resolve(args.drive)
    data = state.load(name)
    orca.checkout(name, data["roles"]["coordinator"]["cwd"])
    boxes = providers.of(data).mac_box
    family = state.priority_family(data)
    for job_id in args.jobs:
        try:
            boxes.take(f"{job_id}-mac", f"{name}/{job_id}")
        except providers.ProviderError as error:
            if data["jobs"][job_id]["order"] in family and mac_box_yielded(name, data, boxes, family):
                boxes.take(f"{job_id}-mac", f"{name}/{job_id}")
            else:
                print(f"{job_id}: {error}", file=sys.stderr)
                return 1
        with state.edit(name) as data:
            ended = state.mac_box_handed(data, job_id, f"{job_id}-mac")
        state.append_event(name, "mac_box_handed", job=job_id, box=f"{job_id}-mac", host=None, ended=ended)
        print(f"{job_id}: handed box {job_id}-mac")
    return 0


def mac_box_yielded(name: str, data: dict, boxes: providers.MacBoxProvider, family: set[str]) -> bool:
    """Whether a waiting job of no priority order gave its box back to the pool for a priority job, the first whose box the provider's down accepts."""
    held = [job_id for drive, _, job_id in (holder.partition("/") for holder in boxes.live_boxes() or []) if drive == name]
    for job_id in held:
        job = data["jobs"].get(job_id)
        if not job or job["state"] != "waiting" or job["order"] in family or state.box_wanted(data, job_id):
            continue
        try:
            boxes.down(job_id, f"{name}/{job_id}")
        except providers.ProviderError:
            continue
        print(f"{job_id}: gave its box back for a priority job; it takes one again when it resumes")
        return True
    return False


def fde_scan(args: argparse.Namespace) -> int:
    """Run the FDE scan the repository's provider names in the drive's checkout, moved to its default branch's head, since a lane's own tree on a branch cut before the scan landed may lack it; its output and exit status are the verb's."""
    name = state.resolve(args.drive)
    data = state.load(name)
    checkout = orca.checkout(name, data["roles"]["coordinator"]["cwd"])
    if not (scan := providers.of(data).repo.fde_scan):
        raise DriveError("this repository's providers name no FDE scan")
    return subprocess.run([*shlex.split(scan), *args.arguments], cwd=checkout, stdin=subprocess.DEVNULL, check=False).returncode


def thread_orders(data: dict, thread: str | None, but: str | None = None) -> list[str]:
    """Each open order but the one named that has a thread at ts thread."""
    return [order_id for order_id, order in state.open_orders(data).items() if order_id != but and any(ts == thread for _, ts in slack.order_threads(data, order_id, order))]


def thread_strays(data: dict, ids: list[str], text: str, asked: str) -> list[str]:
    """Each reference of a post's text naming work outside the orders the ids name, as written: a {order:…}, a {pr:…} or a link, key or #N naming a drive PR, or a {card:…} as `stray_card` reads it, that is none of those orders, the PRs they own or their jobs carry, nor an order, job or PR those jobs' and PRs' waits name; a PR his words asked name by link, key or #N, and its order, are theirs too, so an answer to his question about another order's PR stands."""
    narrowed = only(data, ids)
    waits = {wait for owner in [*narrowed["jobs"].values(), *narrowed["prs"].values()] for wait in owner["waits"]}
    prs = {*narrowed["prs"], *state.named_prs(data, "", f"{' '.join(waits)} {asked}")}
    orders = {*ids, *(data["prs"][key]["order"] for key in prs), *(wait.partition(":")[0] for wait in waits), *(data["jobs"][wait]["order"] for wait in waits if wait in data["jobs"])}
    strays = []
    for found in limits.REF.finditer(text):
        match found[1], found[2]:
            case "order", order_id if order_id not in orders:
                strays.append(found[0])
            case "pr", ref if (key := state.tracked_pr(data["prs"], ref, untracked=True)) in data["prs"] and key not in prs:
                strays.append(found[0])
            case "card", ref if (card := state.waits_of(data["name"], ref)) not in waits and stray_card(data, card, orders, prs):
                strays.append(found[0])
    return list(dict.fromkeys([*strays, *(key for key in state.named_prs(data, "", limits.REF.sub(" ", text)) if key not in prs)]))


def stray_card(data: dict, waits: str, orders: set[str], prs: set[str]) -> bool:
    """Whether the card under a waits id is work outside the orders and PRs given: a PR card asking his tap on none of those PRs, or a question card a job on another order asked whose question holds none of them."""
    if asked := state.ASK_WAITS.fullmatch(waits):
        return not {key.rpartition("#")[2] for key in prs} & set(asked[1].split("."))
    if (job := data["jobs"].get(state.waits_job(data, waits))) is None or job["order"] in orders:
        return False
    return not any(prs & {*state.named_prs(data, "", question["holds"] or "")} for question in job["questions"] if question["waits"] == waits)


def with_cards(name: str, data: dict, within: dict | None = None) -> dict:
    """The drive with each question and open PR marked with the state of its card of his, as the cards were last stored, and each open PR's stage as stage.shown puts it on; with within, a narrowing of the drive that shares its records, only within's questions and PRs, each still read against the whole drive."""
    from drive import stage, view

    marked = within or data
    found, source = view.stored_cards(name)
    found = found if source == "live" else None
    for question in (question for job in marked["jobs"].values() for question in job["questions"]):
        question["card_state"] = view.question_card_words(found, source, question)
    for pr in marked["prs"].values():
        pr["card_state"] = view.pr_card_words(found, source, pr) if pr["state"] == "open" else None
    with state.indexed(data):
        stage.shown(data, found, state.open_pr_keys(marked))
    return data


def wait(args: argparse.Namespace) -> int:
    """Block on the receiver's deliveries log until a delivery ends the wait on the PR or run named, by receiver.settles, a PR matched by its number and the head the drive tracks for it, so a lane hears its PR's checks, reviews and merge, or its run's end, from GitHub's own webhook within a second and reads GitHub once after, never in a loop; refused past WAIT_SECONDS_MOST, since a wait outliving the prompt cache re-reads the lane's whole context."""
    if not 0 < args.timeout <= receiver.WAIT_SECONDS_MOST:
        raise DriveError(f"--timeout {args.timeout}: wait 1 to {receiver.WAIT_SECONDS_MOST} seconds, so the prompt cache outlives the wait; wait again after a read for longer")
    data = state.load(state.resolve(args.drive))
    want = receiver.wanted(args.ref, {key: pr["head"] for key, pr in data["prs"].items()})
    delivery, offset = receiver.waited(want, args.timeout, args.after)
    again = f"drive wait {args.ref} --after {offset}"
    if delivery is None:
        print(json.dumps({"ref": args.ref, "delivery": None, "after": offset}) if args.json else f"{args.ref}: no delivery in {args.timeout}s; read it once, then, only while it is still pending, {again}")
        return 1
    said = ", ".join(f"{field} {delivery[field]}" for field in ("numbers", "head", "action") if delivery.get(field))
    line = f"{args.ref}: {delivery['event']} delivery at {delivery['at']}{f' ({said})' if said else ''}; read it now with one gh call, then, only while it is still pending, {again}"
    return emit(args, line, {"ref": args.ref, "delivery": delivery, "after": offset})


def term(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    def term_write(data: dict):
        data["stand_ins"][args.stand_in] = args.term

    state.update(name, None, term_write)
    line = f'say\'s hook finds "{args.stand_in}" from now on, naming "{args.term}", for Jev to judge against his words'
    return emit(args, line, {"stand_in": args.stand_in, "term": args.term})


def refuse_an_unheld_repeat(name: str, data: dict, order_id: str | None, named: list[str]) -> None:
    """His words repeat a part of an open order: refuse when no running, waiting or built job carries it and no done job's card of his still open waits on it, since the drive dropped it."""
    from drive import view

    if order_id is None:
        raise DriveError("--part names the part his words repeat of the order --repeats names: pass --repeats <o> with it")
    order = find(data["orders"], order_id, "order")
    holding = [
        job
        for job in data["jobs"].values()
        if job["state"] not in state.FINISHED_JOB_STATES or job["state"] == "done" and view.open_cards(job)
    ]
    unheld = [part for part in pick_parts(order_id, order, named) if not any(part in state.job_parts(job, order_id) for job in holding)]
    if unheld:
        parts = ", ".join(f'"{part}"' for part in unheld)
        raise DriveError(
            f'his words repeat {order_id} "{render.cut(order["text"], 200)}", part {parts}, and no running, waiting or built job'
            f" or open card of his carries it, so the drive dropped it: start its job with drive job add <kind> --order {order_id} --part <part> --goal"
            ' "<its deliverable>", file a defect with this refusal verbatim, then run heard again'
        )


def orca_places(seats: dict[str, dict], hidden: Callable[[Path], str]) -> dict[str, str]:
    """Where he opens each seat's session in Orca, by one read of the terminals Orca shows: a switch command only for a terminal Orca shows whose process tree holds that session."""
    try:
        found = orca.session_terminals([held["session"] for held in seats.values()])
        return {seat: orca_place(found[held["session"]], hidden) for seat, held in seats.items()}
    except orca.OrcaError as error:
        return {seat: f"Orca terminals unproven: {error}" for seat in seats}


def orca_place(terminals: list[dict], hidden: Callable[[Path], str]) -> str:
    if shown := next((terminal for terminal in terminals if not terminal["orphaned"]), None):
        return f"open it with: orca terminal switch --terminal {shown['handle']}"
    if terminals:
        return hidden(orca.main_checkout(terminals[0]["worktreePath"]))
    return "in no Orca terminal"


def show_command(checkout: Path) -> str:
    return f"orca repo set --repo path:{checkout} --external-worktree-visibility show"


def name_show(checkout: Path) -> str:
    return f"{HIDDEN}; show them, with no restart of the session, with: {show_command(checkout)}"


def run_show(checkout: Path) -> str:
    orca.show_worktrees(checkout)
    return (
        f"{HIDDEN}, so drive start ran {show_command(checkout)} for this drive's repository alone, which lists that "
        "repository's other worktrees in Orca too; drive status gives the switch command once Orca shows this terminal, "
        "with no restart of the session"
    )


def only(data: dict, ids: list[str]) -> dict:
    """The drive narrowed, never saved, to the orders the ids name, directly or through one of their jobs or PRs, and each open order whose title, words, part names or PR titles hold every other word given, in any case, with their jobs and each PR those orders own or their jobs carry, as render.job_pr_lines draws it; data stays whole, since who carries a part is read across every job and order."""
    keys = {each for each in ids if reads.DRIVE_RECORD.fullmatch(each) or state.PR_WORD.fullmatch(each)}
    if unknown := keys - set(data["orders"]) - set(data["jobs"]) - set(data["prs"]):
        raise DriveError(f"no order, job or PR {', '.join(sorted(unknown))}; name {ID_FORMS}")
    if keys and keys <= set(data["jobs"]) and len(keys) == len(ids):
        return job_rows(data, keys)
    named = {key if key in data["orders"] else (data["jobs"].get(key) or data["prs"][key])["order"] for key in keys}
    if words := [each.casefold() for each in ids if each not in keys]:
        found = {order_id for order_id, order in state.open_orders(data).items() if all(word in searched(data, order_id, order) for word in words)}
        if not found:
            raise DriveError(f"no open order's title, words, part names or PR titles hold {', '.join(map(json.dumps, words))}; name {ID_FORMS}, or other words")
        named |= found
    orders = {key: order for key, order in data["orders"].items() if key in named}
    listed = {job_id for order in orders.values() for job_id in order["jobs"]}
    jobs = {key: job for key, job in data["jobs"].items() if key in listed or job["order"] in named}
    carried = {state.pr_key(url) for job in jobs.values() for url in job["prs"]}
    return data | {
        "orders": orders,
        "jobs": jobs,
        "prs": {key: pr for key, pr in data["prs"].items() if pr["order"] in named or pr["job"] in jobs or key in carried},
    }


def job_rows(data: dict, job_ids: set[str]) -> dict:
    """The drive narrowed, never saved, to these jobs alone, each order holding them listing only them and their parts, and the PRs they open or carry, so a status of one job on an order of hundreds prints that job's row and its PRs, not the order whole."""
    jobs = {key: job for key, job in data["jobs"].items() if key in job_ids}
    carried = {state.pr_key(url) for job in jobs.values() for url in job["prs"]}
    orders = {}
    for order_id in dict.fromkeys(job["order"] for job in jobs.values()):
        order = data["orders"][order_id]
        held = {part for job in jobs.values() if job["order"] == order_id for part in job["parts"]}
        orders[order_id] = order | {"jobs": [job_id for job_id in order["jobs"] if job_id in job_ids], "parts": {part: value for part, value in order["parts"].items() if part in held}}
    return data | {"orders": orders, "jobs": jobs, "prs": {key: pr for key, pr in data["prs"].items() if pr["job"] in jobs or key in carried}}


def searched(data: dict, order_id: str, order: dict) -> str:
    return " ".join([order["title"] or "", order["text"], *order["parts"], *(pr["title"] for pr in data["prs"].values() if pr["order"] == order_id)]).casefold()


def status(args: argparse.Namespace) -> int:
    from drive import view

    name = state.resolve(args.drive)
    if args.ids and not (getattr(args, "full", False) or getattr(args, "json", False) or getattr(args, "prs", False)) and all(reads.DRIVE_RECORD.fullmatch(each) and each.startswith("o") for each in args.ids):
        return emit(args, "\n\n".join(order_summary(name, order_id) for order_id in args.ids), {})
    view.refresh_cards(name)
    data = state.load(name)
    shown = only(data, args.ids) if args.ids else None
    with_cards(name, data, shown)
    if args.prs:
        rows = view.his_prs(shown or data)
        return emit(args, render.his_prs(rows), {"prs": rows})
    for job_id, in_line in place.positions(data).items():
        data["jobs"][job_id]["in_line"] = in_line
    for project, held in data["subcoordinators"].items():
        held["watch"] = watch.holder(state.pid_path(name, project))
    seen = {}
    if shown is None:
        bound = state.bound_sessions(data)
        places = orca_places(bound, name_show)
        with ThreadPoolExecutor() as pool:
            seen = dict(zip(bound, pool.map(lambda seat: with_build(places[seat], bound[seat]), bound), strict=True))
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    project = state.session_project(data, session)
    runs = (lambda order: state.runs(data, order, project)) if state.session_role(data, session) == "coordinator" else None
    text, payload = render.status_text(data, seen, shown, runs), render.status_data(shown or data, seen, shown is not None, runs)
    if shown is not None and any(state.RELEASE_WAIT.fullmatch(wait) for owner in [*payload["jobs"].values(), *payload["prs"].values()] for wait in owner["waits"]):
        payload["release_freezes"] = brief.releases(name, data)
        text += f"\nRelease freezes: {payload['release_freezes']}"
    return emit(args, text, payload)


STAGE_REF = re.compile(r"(?<=\s)Forge-AI/monorepo#")


def order_summary(name: str, order_id: str) -> str:
    """Where an order stands, read from the drive's saved rows alone, the order's, its jobs' one by one, and the PR and slot tables, with no GitHub, Slack or Orca read, so it answers in well under a second whatever the order's size: its PRs merged, closed and open, each open one at the stage the watch keeps on it and what holds it, each part no PR carries yet with its jobs, each staging tester's latest line, each release job and staging slot place of its jobs, and what waits on him, his cards and his taps; drive status --full prints every row."""
    from drive import view

    if not (order := state.saved_row(name, "orders", order_id)):
        raise DriveError(f"no order {order_id}")
    jobs = {job_id: job for job_id in order["jobs"] if (job := state.saved_row(name, "jobs", job_id))}
    tables = state.stored(name, ("prs", "slots"))
    prs = {key: pr for key, pr in tables["prs"].items() if pr["order"] == order_id or pr["job"] in jobs}
    lines = [f"{order_id} {order['state']}: {render.cut(order.get('title') or order['text'], 140)}"]
    for shown in ("merged", "closed"):
        if keys := [key for key, pr in prs.items() if pr["state"] == shown]:
            lines.append(f"{shown} {len(keys)}: {', '.join('#' + key.split('#')[1] for key in keys)}")
    grouped: dict[tuple[str, str], list[str]] = {}
    for key, pr in prs.items():
        if pr["state"] == "open":
            stage = pr.get("stage") or {}
            grouped.setdefault((stage.get("name") or "unread", render.cut(STAGE_REF.sub("#", stage.get("why") or ""), 120)), []).append("#" + key.split("#")[1] + ("*" if (pr.get("tap") or {}).get("label") == "Approve" else ""))
    for (named, why), keys in grouped.items():
        lines.append(f"open {named} {len(keys)}: {', '.join(keys)}{': ' + why if why else ''}")
    if any("*" in key for keys in grouped.values() for key in keys):
        lines.append("* has his Approve")
    carried = {part for job in jobs.values() if job["prs"] for part in job["parts"]}
    for part in order["parts"]:
        if part not in carried and not state.STAGING_PART.fullmatch(part):
            if held := [f"{job_id} {job['kind']} {job['state']}" for job_id, job in jobs.items() if part in job["parts"] and job["state"] not in state.FINISHED_JOB_STATES]:
                lines.append(f"not written: \"{render.cut(part, 80)}\": {', '.join(held)}")
    for job_id, job in jobs.items():
        if any(state.STAGING_PART.fullmatch(part) for part in job["parts"]) and job["state"] not in state.FINISHED_JOB_STATES:
            lines.append(f"staging {job_id} {job['state']}: {render.cut(job.get('said') or job['goal'], 160)}")
        if job.get("release_build") and job["state"] not in state.FINISHED_JOB_STATES:
            lines.append(f"release {job_id} {job['state']}: {job['release_build']}")
    numbers = {key.split("#")[1] for key in prs}
    for env, slot in tables["slots"].items():
        for each in [*([slot["holder"]] if slot.get("holder") else []), *slot["queue"]]:
            if each.get("starts") and each["job"] not in jobs and (rides := sorted(numbers & {str(pr).rsplit("#", 1)[-1] for pr in each["prs"]})):
                lines.append(f"release {each['job']} on {env} carries {', '.join('#' + number for number in rides)}")
        if (held := slot.get("holder")) and (riding := [job_id for job_id in [held["job"], *(rider["job"] for rider in held.get("riders", []))] if job_id in jobs]):
            lines.append(f"slot {env}: {', '.join(riding)} in the cut holding it until {render.when(held['until'])}")
        if queued := [f"{each['job']} place {place}" for place, each in enumerate(slot["queue"], 1) if each["job"] in jobs]:
            lines.append(f"slot {env} queue: {', '.join(queued)}")
    asks = [f"{job_id}'s card {card}" for job_id, job in jobs.items() if job["state"] not in state.FINISHED_JOB_STATES for card in view.open_cards(job | {"questions": job.get("questions") or []})]
    asks += [f"#{key.split('#')[1]}: {(pr.get('stage') or {}).get('why') or 'his tap'}" for key, pr in prs.items() if pr["state"] == "open" and (pr.get("stage") or {}).get("owner") == "him"]
    lines.append(f"waits on him: {'; '.join(render.cut(ask, 160) for ask in asks) if asks else 'nothing'}")
    return "\n".join(lines)


def with_build(place: str, held: dict) -> str:
    path = watch.session_transcript(held)
    note = watch.unknown_model(*watch.running_build(path), CLAUDE_BUILDS) if path.exists() else None
    return f"{place}; {note}" if note else place


def open_channel(name: str, project: str | None) -> dict:
    """Make, as him with cc-slack's user token, the drive's own private channel for no project, or a project's under it, named by slack.channel_name, which cc-slack reads as it reads his DM, one he already has kept; keep its id and name in the drive's slack row, and hand the communicator its Hold with line again at its next tool call, so it holds the channel with no restart."""
    project = slack.channel_project(project)
    made = slack.cc_slack("operator-channel", slack.seat_channel_name(name, state.load(name), project))
    def open_channel_write(fresh: dict):
        fresh["slack"]["channels"][project or ""] = fresh["slack"]["channels"].get(project or "", {}) | {"id": made["id"], "name": made["name"]}

    state.update(name, None, open_channel_write)
    slack.whoami.cache_clear()
    return made


def channels(args: argparse.Namespace) -> int:
    from drive import talk, view

    name = state.resolve(args.drive)
    data = state.load(name)
    missing = [project for project in dict.fromkeys([None, *data["subcoordinators"], TOOLING_FILING[0]]) if (slack.channel_project(project) or "") not in data["slack"]["channels"]]
    if args.env_alerts:
        return emit(args, "\n".join(env_alerts_kept(name, args.env_alerts)), {"env_alerts": state.load(name)["slack"]["env_alerts"]})
    if args.hold:
        return emit(args, "\n".join(channels_held(name, args.hold, args.kinds)), {"held_by": state.load(name)["slack"]["held_by"]})
    if not (args.make or args.move_cards):
        kept = [f"#{made['name']} {made['id']} {seat or 'the drive'}" for seat, made in data["slack"]["channels"].items()]
        unmade = [f"no channel yet for {slack.channel_project(project) or 'the drive'}: --make makes it" for project in missing]
        return emit(args, "\n".join([*kept, *unmade]), {"channels": data["slack"]["channels"], "missing": missing})
    made = [open_channel(name, project) for project in missing] if args.make else []
    data = state.load(name)
    found, dm, moved = view.cards(name), slack.dm(), []
    try:
        for card in [*found["questions"], *found["pr_cards"]] if args.move_cards else []:
            stack = [key for url in card.get("prs", []) if (key := state.pr_key(url)) in data["prs"]]
            channel = talk.channel_for(name, data, talk.project(data, {"card": card["card"]}, stack, None))
            if channel != dm and state.slack_message(card["url"])[0] == dm:
                moved.append((card, stack, slack.cc_slack("move-card", "--url", card["url"], "--channel", channel)["permalink"]))
    finally:
        with state.batch(name):
            for card, stack, url in moved:
                follow_card(name, card["card"]["waits"], {view.message_key(card["url"])}, url)
                def cards_joined(fresh: dict) -> None:
                    state.join_pr_cards(fresh, stack, [url], state.stamp())

                state.update(name, None, cards_joined)
        view.refresh_cards(name)
    lines = [*(f"#{channel['name']} {channel['id']}" for channel in made), *([f"{len(moved)} open card(s) of his DM moved to their project's channel"] if args.move_cards else [])]
    return emit(args, "\n".join(lines), {"channels": made, "moved": [{"from": card["url"], "to": url} for card, _, url in moved]})


def env_alerts_kept(name: str, pairs: list[str]) -> list[str]:
    """Name each staging env's alert channel, <env>=<channel>, as plat-usw2-staging=C0CAC1HT17W, whose each new alert from now on the watch hands to the jobs testing on that env, by watch.env_alert_lines; an empty channel stops it. Refused for an env the drive keeps no slot for."""
    data = state.load(name)
    split = [pair.partition("=")[::2] for pair in pairs]
    if unknown := [env for env, _ in split if env not in data["slots"]]:
        raise DriveError(f"--env-alerts: the drive keeps no slot for {', '.join(unknown)}; its slots: {', '.join(data['slots']) or 'none'}")
    seen = f"{state.now().timestamp():.6f}"

    def env_alerts_write(fresh: dict):
        for env, channel in split:
            if channel:
                fresh["slack"]["env_alerts"][env] = {"channel": channel, "seen": fresh["slack"]["env_alerts"].get(env, {}).get("seen", seen)}
            else:
                fresh["slack"]["env_alerts"].pop(env, None)

    state.update(name, None, env_alerts_write)
    return [f"each new alert in {channel} goes to the jobs testing on {env}" if channel else f"alerts of {env} go to no job" for env, channel in split]


def channels_held(name: str, holds: list[str], kinds: str) -> list[str]:
    """Give each watched alert channel <channel>=<seat> to that seat's sub-coordinator, which subscribes to its kinds of lines and keeps it across restarts and compaction by its Hold line, adding the channel to the drive's watched channels; an empty seat hands it back to the top coordinator. Refused for a seat no sub-coordinator holds."""
    data = state.load(name)
    pairs = [hold.partition("=")[::2] for hold in holds]
    if unbound := [seat for _, seat in pairs if seat and seat not in data["subcoordinators"]]:
        raise DriveError(f"--hold: no sub-coordinator holds {', '.join(unbound)}; the seats bound: {', '.join(data['subcoordinators']) or 'none'}")

    def channels_held_write(fresh: dict):
        for channel, seat in pairs:
            if channel not in fresh["slack"]["watch"]:
                fresh["slack"]["watch"].append(channel)
            if seat:
                fresh["slack"]["held_by"][channel] = {"seat": seat, "kinds": kinds}
            else:
                fresh["slack"]["held_by"].pop(channel, None)
            for held in [fresh["roles"].get("coordinator"), *(fresh["subcoordinators"].get(each) for each in {seat, data["slack"]["held_by"].get(channel, {}).get("seat")} if each)]:
                if held:
                    held.pop("rules", None)

    state.update(name, None, channels_held_write)
    return [f"{channel} held by the sub-coordinator for {seat}, which subscribes to its {kinds} lines" if seat else f"{channel} held by the top coordinator again" for channel, seat in pairs]


def context(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    data = state.load(name)
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    role = args.role or state.session_role(data, session)
    if role is None:
        raise DriveError(f"this session holds no role in drive {name}; pass --role")
    print(render.context(data, role, None if args.role else state.session_seat(data, session)))
    return 0


def manual(args: argparse.Namespace) -> int:
    if not args.page:
        print("\n".join(["drive manual <page> prints one of these pages:", *(f"- {page}" for page in manual_pages())]))
        return 0
    text = hand_over_filled(agents.filled((PAGES / f"{args.page}.md").read_text()), args.drive) + repo_page(args.drive, args.page)
    if not args.row:
        sys.stdout.write(text)
        return 0
    rows = [line for line in text.splitlines() if line.startswith("| ") and plain_words(args.row) in plain_words(line.split("|")[1])]
    parts = ["\n".join(rows)] if rows else page_parts(text, args.row)
    if not parts:
        raise DriveError(f"nothing in drive manual {args.page} names {args.row}; drive manual {args.page} prints the page")
    print("\n\n".join(parts))
    return 0


def hand_over_filled(text: str, drive: str | None) -> str:
    """The page with each `<mac box hand over>` filled with the hand_over command of the repository's Mac box provider, its box, holder and prior holder named as the box and the new and old jobs, so the page names the verb the repository's tool takes; generic words when no drive or no such command is there to read."""
    try:
        data = state.load(state.resolve(drive))
    except DriveError:
        data = None
    command = providers.of(data).mac_box.hand_over("<box>", "<drive>/<new job>", "<drive>/<old job>") if data and state.bound_sessions(data) else None
    shown = f"`{command}`" if command else "the hand-over command of the repository's Mac box provider"
    return text.replace(MAC_BOX_HAND_OVER, shown)


def repo_page(drive: str | None, page: str) -> str:
    """What the repository's providers add to a page of the skill, under an `## In this repository` heading, or nothing when no drive or no such text is there to read."""
    try:
        data = state.load(state.resolve(drive))
    except DriveError:
        return ""
    texts = providers.of(data).pages(page) if state.bound_sessions(data) else []
    return "\n## In this repository\n\n" + "\n\n".join(texts) + "\n" if texts else ""


def page_parts(text: str, words: str) -> list[str]:
    """Each part of a page carrying the words, in any case, across a line wrap and with or without code marks, in page order: a heading with its whole section, a list item with the lines wrapped under it, a table row, a fenced block or a paragraph, a part inside another printed once."""
    lines, wanted = text.splitlines(), plain_words(words)
    blocks: list[list[int]] = []
    levels: dict[int, int] = {}
    fenced = joins = False
    for n, line in enumerate(lines):
        fence = line.lstrip().startswith("```")
        if fenced:
            blocks[-1][1], fenced = n + 1, not fence
            continue
        heading, row = reads.PLAN_HEADING.match(line), line.startswith("|")
        if heading:
            levels[n] = len(heading[1])
        if joins and line.strip() and not (fence or heading or row or LIST_ITEM.match(line)):
            blocks[-1][1] = n + 1
        elif line.strip():
            blocks.append([n, n + 1])
        fenced, joins = fence, bool(line.strip()) and not (fence or heading or row)
    found: list[list[int]] = []
    for start, end in blocks:
        if wanted not in plain_words(" ".join(lines[start:end])):
            continue
        if start in levels:
            end = next((n for n, level in levels.items() if n > start and level <= levels[start]), len(lines))
        if found and start <= found[-1][1]:
            found[-1][1] = max(end, found[-1][1])
        else:
            found.append([start, end])
    return ["\n".join(lines[start:end]).rstrip() for start, end in found]


def plain_words(text: str) -> str:
    return " ".join(text.replace("`", "").split()).casefold()


def view_command(args: argparse.Namespace) -> int:
    from drive import dashboard, page, view

    name = state.resolve(args.drive)
    if args.workstream and not args.project:
        raise DriveError(f"--workstream {args.workstream}: name its project with --project, as drive order add files it")
    if args.project:
        refuse_no_home(state.load(name), args.project, args.workstream)
    view.refresh_cards(name)
    model = view.read(name, state.now(), args.project, args.workstream)
    return emit(args, page.render(model, dashboard.link(name), None if args.project else page.ROWS), model)


def refuse_no_home(data: dict, project: str, workstream: str | None) -> None:
    """Refuse a project, or a workstream of it, no order is filed under, naming each pair that files the subject the name calls, else the projects in use."""
    if any(pair[0] == project and workstream in (None, pair[1]) for pair in filing_pairs(data)):
        return
    named = "; ".join(offers(homes(data, workstream or project), workstream or project))
    projects = ", ".join(state.projects(data)) or "none"
    raise DriveError(f"--project {project}{f' --workstream {workstream}' if workstream else ''}: no order is filed there; name {named or f'a project in use: {projects}'}")


def dashboard_command(args: argparse.Namespace) -> int:
    from drive import dashboard, view

    name = state.resolve(args.drive)
    port = args.port or dashboard.port_of(name)
    if args.serve:
        dashboard.serve(name, port, args.listening)
        return 0
    view.refresh_cards(name)
    print("\n".join(dashboard.start(name, port)))
    return 0


def receive(args: argparse.Namespace) -> int:
    from drive import receiver

    return receiver.main(args.listening)


def watch_events(args: argparse.Namespace) -> int:
    """Run the watch with --loop, its responder with --respond, its relay with --relay or its landing loop with --land; else load its launch agent unless one is loaded. The drive's one watch runs each project's passes, and a drive whose state root LONG_RUNNING_STATE or XDG_STATE_HOME moves gets no process."""
    if args.respond is not None:
        return watch.responder(state.resolve(args.drive), args.project, args.respond)
    if args.relay is not None:
        return watch.relay(state.resolve(args.drive), args.project, args.relay)
    if args.land is not None:
        return watch.landing_loop(state.resolve(args.drive), args.project, args.land)
    if args.loop:
        return watch.main(args.drive, args.project)
    print(watch.ensure(state.resolve(args.drive)))
    return 0


def card_command(args: argparse.Namespace) -> int:
    """Answer cc-slack's card_command with launchd.agent_path behind its daemon's PATH, which lacks the folders the drive's launch agents run with, such as the `~/.local/bin` holding `claude`, so each program a card answer starts resolves as it does in the watch."""
    from drive import card, launchd

    os.environ["PATH"] = os.pathsep.join([os.environ.get("PATH", ""), launchd.agent_path()])
    return card.main(sys.stdin, sys.stdout)


def transcripts_keep(args: argparse.Namespace) -> int:
    """Copy to the drive's folder, by orca.keep_transcripts, the Claude Code transcripts the Sprite of the job's lane holds now, so a relaunch after that Sprite is lost resumes its session from a copy no older than its last turn; the watch runs it detached at each turn end of a Sprite lane."""
    name = state.resolve(args.drive)
    data = state.load(name)
    job = find(data["jobs"], args.id, "job")
    kept = orca.keep_transcripts(name, args.id, job, job["dispatch"], orca.checkout(name, data["roles"]["coordinator"]["cwd"]))
    return emit(args, f"{args.id}: kept {kept}" if kept else f"{args.id}: no transcript kept from Sprite {job['workspace']}", {"id": args.id, "transcript": str(kept) if kept else None})


def disks_check(args: argparse.Namespace) -> int:
    """Read the nbd0 root disk of each Sprite a job's lane holds now through the Sprites file API, by orca.disk_read, and checkpoint-restore at once, by orca.revive under the job's own lock, each one whose ext4 counted an error or whose in-flight requests stood with its stat line unchanged since the last check, so a disk stalling under memory pressure is cured before it remounts read-only and its lane's death is recorded as restored, which restart resumes in place on that same Sprite; the last stat lines kept in the drive's NBD_READS."""
    name = state.resolve(args.drive)
    data = state.load(name)
    root = orca.checkout(name, data["roles"]["coordinator"]["cwd"])
    lanes = {job_id: job for job_id, job in data["jobs"].items() if job["state"] in ("running", "waiting") and job.get("dispatch") and orca.holds_sprite(job) and not state.lane_death(job, "dispatch")}
    seen_path = state.drive_dir(name) / NBD_READS
    seen = json.loads(seen_path.read_text()) if seen_path.exists() else {}

    def read(job_id: str) -> tuple[str, tuple[int, list[str]] | None]:
        try:
            return job_id, orca.disk_read(root, lanes[job_id]["workspace"])
        except (orca.OrcaError, ValueError):
            return job_id, None

    with ThreadPoolExecutor(8) as pool:
        reads = {job_id: got for job_id, got in pool.map(read, lanes) if got}
    seen_path.write_text(json.dumps({lanes[job_id]["workspace"]: stat for job_id, (_, stat) in reads.items()}))
    wedged = {
        job_id: f"ext4 counted {errors} errors on it" if errors else f"{stat[8]} requests stood in flight since the last check"
        for job_id, (errors, stat) in reads.items()
        if errors or int(stat[8]) and seen.get(lanes[job_id]["workspace"]) == stat
    }
    lines = []
    for job_id, why in wedged.items():
        workspace = lanes[job_id]["workspace"]
        with state.job_locked(name, job_id, orca.LAUNCH_SECONDS):
            job = state.load(name)["jobs"][job_id]
            if job.get("workspace") != workspace or state.lane_death(job, "dispatch"):
                continue
            try:
                orca.revive(root, workspace)
            except orca.OrcaError as error:
                lines.append(f"{job_id}: Sprite {workspace}'s nbd0 disk wedged ({why}), and its checkpoint-restore failed: {error}")
                continue
            end = orca.LaneEnd(f"its Sprite {workspace}'s nbd0 disk wedged ({why}), and the disk check checkpoint-restored it", job.get("branch"), None)
            orca.died(name, job_id, job["dispatch"], end, restored=state.stamp())
        lines.append(f"{job_id}: Sprite {workspace}'s nbd0 disk wedged ({why}); checkpoint-restored, so its next lane resumes its Claude session there")
    return emit(args, "\n".join(lines) or f"no nbd0 disk of the {len(reads)} Sprites read of {len(lanes)} is wedged", {"read": len(reads), "lanes": len(lanes), "wedged": wedged})


def card_record(args: argparse.Namespace) -> int:
    from drive import card

    card.record_kept(state.resolve(args.drive), Path(args.receipt))
    return emit(args, f"applied {args.receipt}", {"receipt": args.receipt})


def mcp_command(args: argparse.Namespace) -> int:
    from drive import mcp

    if args.list:
        print(json.dumps(mcp.tool_list()))
        return 0
    if args.warm:
        return mcp.warm()
    return mcp.serve(sys.stdin, sys.stdout)


def tool_command(args: argparse.Namespace) -> int:
    from drive import mcp, tools

    if args.help:
        print(tools.described(mcp.tool_named(args.tool)))
        return 0
    print(run_tool(args.tool, json.loads(sys.stdin.read())))
    return 0


BACKGROUND_RECORD_ENV = "LONG_RUNNING_RECORD_BACKGROUND"


def run_tool(tool: str, given: dict) -> str:
    """The lines one MCP tool prints for the arguments its call sent, in whatever process runs it: its own `drive tool` or the MCP server's; a communicator's record call is checked as a dry run here, so a refusal still comes back to it, then sent by its own process, by backgrounded, so no answer to him waits on an order add or a defect."""
    from drive import mcp, tools

    try:
        arguments = tools.parsed_arguments(given, mcp.tool_named(tool)["inputSchema"]["properties"])
        with state.naming(arguments):
            name = state.resolve(arguments.pop("drive", None))
            if tool == "record" and not arguments.get("dry_run") and not os.environ.get(BACKGROUND_RECORD_ENV) and state.session_role(state.saved(name), os.environ.get("CLAUDE_CODE_SESSION_ID")) == "communicator":
                tools.run(tool, name, arguments | {"dry_run": True})
                return backgrounded(name, given)
            if os.environ.get(BACKGROUND_RECORD_ENV):
                return kept_record(name, lambda: tools.run(tool, name, arguments))
            return tools.run(tool, name, arguments)
    except reads.ReadError as error:
        raise DriveError(str(error)) from None


def backgrounded(name: str, given: dict) -> str:
    """Send a communicator's records from a `drive tool record` process of their own, under its session, which outlives the call, its call's arguments in a file under the drive's records folder and what it printed in records.log, so the call returns at once."""
    kept = state.drive_dir(name) / "records" / f"{uuid.uuid4().hex}.json"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(json.dumps(given | {"drive": name}))
    with kept.open() as stdin, (state.drive_dir(name) / "records.log").open("a") as out:
        subprocess.Popen([str(Path(__file__).resolve().parents[2] / "bin" / "drive"), "tool", "record"], stdin=stdin, stdout=out, stderr=subprocess.STDOUT, env=os.environ | {BACKGROUND_RECORD_ENV: kept.name}, start_new_session=True)
    return f"sending in the background; what it sends lands in {state.drive_dir(name) / 'records.log'}, and a refusal reaches the meta session as a defect"


def kept_record(name: str, send: Callable[[], str]) -> str:
    """Send a communicator's records in the process backgrounded started, telling the meta session as a defect when they are refused, since no turn waits to read the refusal."""
    from drive import tools

    try:
        return send()
    except DriveError as error:
        tools.sent(name, "communicator", [{"kind": "defect", "to": [f"meta@{name}"], "text": f"the communicator's record call {os.environ[BACKGROUND_RECORD_ENV]} was refused in the background: {error}"}])
        raise


def datadog_command(args: argparse.Namespace) -> int:
    """Run one read-only tool of the Datadog MCP on this Mac with ai-oncall's Datadog keys, its JSON arguments read from stdin after `-`, print what it answered and exit 1 when it says it failed; with no `-`, print its description and input schema; `tools` lists the tools it runs. A Sprite lane, whose Sprite holds no Datadog key, makes its Datadog reads with it through the drive that asks each verb of this Mac, and every lane its Database Monitoring reads, since it alone asks the server for the `dbm` toolset."""
    from drive import datadog

    try:
        status, said = datadog.run(args.tool, json.loads(sys.stdin.read()) if args.stdin else None)
    except reads.ReadError as error:
        raise DriveError(f"datadog {args.tool}: {error}") from None
    print(said)
    return status


def plan_search(args: argparse.Namespace) -> int:
    """Print each line of the plans the coordinator's checkout keeps in cc-notes that carries one of the phrases whole, under its plan and headings, cut at reads.BUDGET characters."""
    checkout_dir = Path(state.load(state.resolve(args.drive))["roles"]["coordinator"]["cwd"])
    try:
        found = reads.plan_sections(args.words, checkout_dir)
    except reads.ReadError as error:
        raise DriveError(f"plan search: {error}") from None
    said = "\n\n".join(
        "\n".join(filter(None, [" › ".join(filter(None, [f"== {section['title']} (plan {section['plan']}, {section['status']})", section["heading"]])), section["text"]]))
        for section in found
    )
    return emit(args, reads.head(said) or "no plan the coordinator keeps in cc-notes has a line on " + ", ".join(f'"{each}"' for each in args.words), found)


def page_add(args: argparse.Namespace) -> int:
    """Put a page a page job built for an order under Pages on the drive's dashboard."""
    name = state.resolve(args.drive)
    page = pages.add(name, args.slug, args.order, Path(args.dir))
    return emit(args, f"page {page['slug']} for {args.order}: /pages/{page['slug']}/ on the dashboard, listed under Pages as {page['title']!r}", page)


def page_make(args: argparse.Namespace) -> int:
    """Make the applet page for an order's goal or decision, which the dashboard composes and keeps live, and print its link."""
    from drive import applet, dashboard

    name = state.resolve(args.drive)
    scope = pages.WHOLE_DRIVE if args.whole_drive else {key: values for key, values in (("projects", args.project), ("workstreams", args.workstream), ("words", args.word)) if values} or None
    slug = args.slug or applet.slug_of(state.load(name)["orders"][args.order]["title"] or args.goal)
    page = applet.make(name, slug, args.order, args.goal, scope, args.made_by)
    url = f"{dashboard.link(name)}#/pages/{slug}"
    return emit(args, f"page {slug} for {args.order}: {url}, composed by the dashboard from its live slice and listed under Pages as {page['title']!r}", page | {"url": url})


def page_frame(args: argparse.Namespace) -> int:
    """Open <session>-frame, an agent-browser session inside the page frame the dashboard in <session> shows, where every command but screenshot acts on the page, eval, get count, wait --fn and set media included, which agent-browser's own frame command leaves on the dashboard."""
    framed = page_check.frame(args.session, args.slug)
    return emit(args, framed, {"session": framed})


def break_glass(args: argparse.Namespace) -> int:
    """Run one command under the repository's access grant for a stack, started through its AccessProvider in the job's or order's name with its reason, holding only the credentials the grant mints, which end no later than the grant, and stopped with every process left in its group by the grant's end or when drive is stopped: its output and exit status are the verb's, and nobody signs in. The moment the grant's link exists, it posts root@<drive> one evidence record from lane break-glass naming the job and its order, the link, the stack, the command and the reason, so the top coordinator reads whose a grant is rather than asking every session; a post cci refuses runs nothing."""
    name = state.resolve(args.drive)
    data = state.load(name)
    records, label = (data["jobs"], "job") if JOB_ID.fullmatch(args.id) else (data["orders"], "order")
    find(records, args.id, label)
    whose = f"{args.id} [{data['jobs'][args.id]['order']}]" if label == "job" else args.id

    def started(link: str) -> None:
        text = f"{whose} break-glass grant {link} on {args.stack} for: {shlex.join(args.command)}; reason: {args.reason}"
        argv = ["cci", "post", "--drive", name, "--lane", "break-glass", "--kind", "evidence", "--to", f"root@{name}", "--id", f"break-glass-{link.rpartition('/')[2]}"]
        reads.run([*argv, "--text", reads.withheld(text)], Path.cwd(), reads.posting())

    try:
        with providers.of(data).access.grant(args.stack, args.reason, f"{name} {args.id}", started) as granted:
            print(f"break-glass grant {granted.link} passed; running {args.command[0]} {reads.held(granted)}", file=sys.stderr, flush=True)
            return reads.run_granted(args.command, granted)
    except (reads.ReadError, providers.ProviderError) as error:
        raise DriveError(f"break-glass: {error}") from None


def move(args: argparse.Namespace) -> int:
    """Step 1 of moving a role's session under cci and into a fresh Orca terminal: his word naming the move, recorded on the role, and the session registered with cci in the drive's checkout, which resumes it there in its tmux session once it exits; steps 2 to 4 printed, run by hand."""
    name = state.resolve(args.drive)
    if not args.his_word:
        raise DriveError(
            f"drive move {args.role}: refused, ending a session is his decision: pass --his-word <the permalink of his message naming the move>"
        )
    known = state.load(name)
    if incidents := state.open_incidents(known, list(known["orders"])):
        raise DriveError(
            f"drive move {args.role}: refused, incident {', '.join(incidents)} is open and a move ends the {args.role}'s session:"
            " move it once the fix is live"
        )
    his_words(args.his_word, "move a session")
    was = find(known["roles"], args.role, "role")["cwd"]
    kept = was if state.scratch() else str(orca.checkout(name, was))
    def move_write(data: dict):
        held = find(data["roles"], args.role, "role")
        held.update(move_word=args.his_word, cwd=kept)
        return held

    held = state.update(name, None, move_write)
    registered = "" if state.scratch() else f"; {agents.register(name, args.role, None, held['cwd'], held['session'])}"
    lanes = {role: lane for lane, role in state.LANE_ROLES.items()}
    mover = lanes[next(role for role in state.ROLES if role != args.role)]
    session = held["session"]
    terminals = orca.session_terminals([session])[session]
    ended = (
        f"orca terminal send --terminal {terminals[0]['handle']} --text /exit --enter"
        if terminals
        else f"tmux send-keys -t <pane> /exit Enter under tmux, its pane the one tmux list-panes -a lists with its claude line, or claude stop {session} for a claude --bg session"
    )
    pids = orca.claude_pids(session)
    gone = (
        f"until ps -p {' '.join(map(str, pids))} lists nothing"
        if pids
        else f"none: no ~/.claude/sessions/<pid>.json names {session}, so its process has exited"
    )
    steps = [
        f"1. his word {args.his_word} recorded on the {args.role}{registered}",
        (
            f"2. on the cci `post` tool, `kind: ask`, `lane: {mover}`, `to: [{lanes[args.role]}@{name}]`, `text: Moving you into an Orca terminal"
            " on his word: run cc-slack unfollow for each of your Slack watches, cci unsubscribe, stop your drive watch Monitor if you run one,"
            " reply released, and take no more work.`, and wait for its reply"
        ),
        f"3. {ended}",
        f"4. wait {gone}",
        f"then steps 5 to 7 of drive manual migration, Into an Orca terminal: cci session run resumes {session} in tmux session {state.alias(name, args.role)}; create the terminal that shows it",
    ]
    return emit(args, "\n".join(steps), {"role": args.role, "move_word": args.his_word, "steps": steps})


def install_plugin(args: argparse.Namespace) -> int:
    return install.main(args.plugin, args.sha, args.repo, args.timeout, args.drive)


def reload_plugins(args: argparse.Namespace) -> int:
    return install.reload_main(args.drive, args.seat, args.force, args.terminal)


def advised(args: argparse.Namespace) -> int:
    agents.advised(args.drive, args.lane, args.command)


def migrate_drives(args: argparse.Namespace) -> int:
    for name in state.names():
        state.migrate(name)
    return 0


def default_bin_dir() -> Path:
    if xdg := os.environ.get("XDG_BIN_HOME"):
        return Path(xdg)
    return Path.home() / ".local" / "bin"


def setup(args: argparse.Namespace) -> int:
    bin_dir = Path(args.bin_dir) if args.bin_dir else default_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "drive"
    _, _, newest_drive, _ = json.loads(MANIFEST.read_text())["mcpServers"]["drive"]["args"]
    shim.write_text(f"#!/usr/bin/env -S python3 -I\n{newest_drive}")
    shim.chmod(0o755)
    print(f"wrote {shim}")
    return 0


def manual_pages() -> list[str]:
    return sorted(path.stem for path in PAGES.glob("*.md"))


def job_kind(kind: str) -> str:
    return "design" if kind == "designer" else kind


class Root(argparse.Action):
    def __call__(self, parser: argparse.ArgumentParser, namespace: argparse.Namespace, values: object, option_string: str | None = None) -> None:
        print(Path(__file__).resolve().parents[2])
        parser.exit()


@cache
def parser() -> argparse.ArgumentParser:
    """The drive's parser, built once a process, since a batch reads it for every op; read it through parsed, which copies the lists a parse shares with its defaults."""
    drive_opt = argparse.ArgumentParser(add_help=False)
    drive_opt.add_argument("--drive", help="the drive to act on")
    json_opt = argparse.ArgumentParser(add_help=False)
    json_opt.add_argument("--json", action="store_true", help="print JSON")
    both = [drive_opt, json_opt]
    dry_opt = argparse.ArgumentParser(add_help=False)
    dry_opt.add_argument("--dry-run", action="store_true", help="run every check it makes and keep nothing, as an MCP op's dry_run does, saying which command that does more than read it stopped at")
    waits_opt = argparse.ArgumentParser(add_help=False)
    waits_opt.add_argument(
        "--waits",
        action="append",
        default=[],
        metavar="CARD|JOB|PR|RELEASE|PART|PID|BUILD|REVIEW",
        help=(
            "a design card or PR card of his still open, by its Slack link or waits id, on drive job set alone, since a card holds a PR"
            " only by its question's Holds: line or the PR's DESIGN: line citing it, and drive pr set refuses one, a PR the drive tracks as <owner>/<repo>#<n>,"
            " a part of an open order still to be delivered as <order>:<part>, word for word, held until a builder or codex job on it returns DONE with no PR of its own unlanded, or a staging test part's DONE keeps its first CHECK: proof on it, never the DONE the watch takes at a merge,"
            " release:<service>:<owner>/<repo>#<n>, or release:<service>:<owner>/<repo>#<n>@<env> for one env as the release provider's live read names it,"
            " a wake the job sets once it reads release live, never a gate, kept with a note naming the components they reach when the release provider's own map,"
            " the release provider's reach read, finds the PR's changed paths reach components and that service is none of them or is no component at all,"
            " a process on this Mac that still runs as pid:<n>, stored pinned to the second it started,"
            " a Buildkite build by its link, https://buildkite.com/<org>/<pipeline>/builds/<n>,"
            " reply:<the permalink of a question the drive asked a teammate>, say's job field setting it,"
            " reply:cci:<the seq of another drive's cci record>, held until the watch's minute read finds that drive's next record on its topic, whose words it holds on the job,"
            " on drive pr set alone review:<a teammate's GitHub login>, their review of the PR's head, which holds his card from drawing and the PR from landing"
            " until the watch's GitHub read finds their newest review is of its head, said on the PR's line,"
            " or a job of this drive still at work, of any kind, it waits on;"
            " repeat for each. The watch drops each as he answers or taps it, the PR merges, the job registers a PR, the FREE line naming its branch to stack on, or returns DONE, or, once it"
            " stops or fails, each part it carries is delivered, the process ends, crashed or finished, which starts a codex job's next codex-ask run carrying its last report,"
            " once the PR merged, every cluster the release live read finds running that service, production or not, or the one env the wait names, serves"
            " a commit holding the merge, at once when release live cannot follow it, a PR outside Forge-AI/monorepo or a service or env no cluster runs,"
            " the FREE line naming why, the watch's Buildkite read, made as the build is first held"
            " and again on each delivery of Buildkite's to the receiver's /buildkite route naming it, finds the build passed, failed, canceled or stopped"
            " at an approve step, or the first message after the question from anyone but the drive and a bot"
            " comes, and says FREE once when the last is gone, which wakes the coordinator."
            " On a PR it never withdraws his open card: the card is redrawn in place, Approve and Hold kept, to say what it waits on"
        ),
    )
    waits_opt.add_argument("--no-waits", action="store_true", help="drop every wait it carries before adding any --waits")
    priority_opt = argparse.ArgumentParser(add_help=False)
    priority_opt.add_argument(
        "--priority",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "mark or unmark the job alone as urgent, such as a drive tooling fix for what blocks his answers or this Mac's load: its lanes start past this Mac's"
            " caps, load gate and its project's daily budget, with none of a priority order's seat or five-minute lines"
        ),
    )
    builds_opt = argparse.ArgumentParser(add_help=False)
    builds_opt.add_argument(
        "--builds-nothing",
        action=argparse.BooleanOptionalAction,
        help=(
            "mark or unmark a builder whose whole work holds no checkout or compiler, such as a read, as a builder whose goal reads prove <PRs> live is marked or not: it holds"
            " no slot of this Mac's builder cap, drops any turn it waits for one, and starts or takes a message here at once, past the load gate"
        ),
    )
    brief_opt = argparse.ArgumentParser(add_help=False)
    brief_opt.add_argument("--serves", help='"port of #N", "drive tooling" or "his order"; default each "port of #N" part the job takes, else drive tooling on a tooling order, else his order')
    brief_opt.add_argument("--done-when", help="the check that proves it done")
    brief_opt.add_argument("--where", help="the repo, base branch, and any branch or PR to adopt")
    brief_opt.add_argument(
        "--native",
        help='where he sees or uses the result and what that platform already does for it, with links; "none" for work he never sees',
    )
    brief_opt.add_argument(
        "--interactions",
        help=f"his user stories on the result, `;` only between stories, each what he does, and sees what he then sees, its result inside the story after \", and sees\", never after a `;`, such as \"he presses j on the queue, and sees the next card open with its question as its first line\"; its job writes strict acceptance criteria for each before building and proves each on the installed surface with {brief.SEEN}; needed when --native names a surface",
    )
    brief_opt.add_argument("--would-he-ask", help="what he would still have to ask for if only his words were built")
    brief_opt.add_argument("--runs-as", help="for a pass, a hand repair or any other write of a team's data: the service account in its cluster and how it starts; \"none: <why>\" for a job that writes none")
    brief_opt.add_argument(
        "--mac",
        help="the step only this Mac runs that the job itself runs, such as drive break-glass, ci mac-box, ci devbox or a drive install no lander runs; drive job launch refuses its Sprite start and the agent_brief hook starts it here at any load, and no other words of the brief place it",
    )
    brief_opt.add_argument(
        "--after",
        action="append",
        metavar="COMMAND",
        help="a drive command drive job prove runs on this Mac once the job's merges are installed and its proof file passes on them, taking DONE only when it exits 0, such as drive cards redraw --waits <waits id> --post: a step its Goal names after the install; repeatable, run in order, its After install: line",
    )
    brief_opt.add_argument("--handoff", help="a PR event or freed built branch: the PRs or branch, its head, the event line, INSTALL: and PROVE:")
    brief_opt.add_argument("--ruling", action="append", default=[], metavar="ANSWER", help="a cc-notes answer id of his that touches this, added to the rulings every brief on its order binds; its words go in whole")
    brief_opt.add_argument("--plan", help="the plan section this implements, quoted; a builder the drive added for a design's change holds its design's from that add")
    brief_opt.add_argument("--incident", help="an incident job's line, by reference/jobs.md")
    brief_opt.add_argument("--scratch", help="its scratch folder; default one in the drive")

    root = argparse.ArgumentParser(prog="drive", description="State and events of a long-running drive.")
    root.add_argument("--version", action="version", version=f"drive {state.VERSION}")
    root.add_argument("--root", action=Root, nargs=0, help="print the folder of the long-running install this drive runs, the newest installed, whose hooks/ and lib/ a proof file imports")
    verbs = root.add_subparsers(dest="verb", required=True)

    verb = verbs.add_parser("start", parents=[json_opt], help="create the drive or bind one of its roles to this session, make the drive's own channel while it keeps none, and start its meta session and communicator in Orca while none is bound")
    verb.add_argument("name")
    verb.add_argument(
        "--role", required=True, choices=state.ROLES, help="communicator talks to him; coordinator runs his orders and jobs; meta holds drive tooling: its fixes, skill rewrites and installs"
    )
    verb.add_argument(
        "--watch", action="append", default=[], metavar="CHANNEL", help="another channel the coordinator subscribes to, kept across restarts"
    )
    verb.add_argument(
        "--project",
        metavar="NAME",
        help=(
            "with --role coordinator, bind this session as the sub-coordinator for a project orders are filed under, beside"
            " the top coordinator, and start that project's watch"
        ),
    )
    verb.set_defaults(run=start)

    order = verbs.add_parser("order", help="his orders").add_subparsers(dest="action", required=True)
    verb = order.add_parser("add", parents=both)
    verb.add_argument("words", help='his words, verbatim, or a "Drive tooling:" order\'s own')
    verb.add_argument("--from", dest="source", default="terminal", help=FROM_HELP)
    verb.add_argument(
        "--thread",
        help=(
            f"{THREAD_HELP}; default: the thread the --from message is in, his DM's root when a teammate started it, since that thread"
            " is its reporter's and hears only its one note. An order in the thread of his open PR card names"
            " that card and leaves it up, since its stack can meet his words already: only --rework withdraws one"
        ),
    )
    verb.add_argument("--rework", action="append", default=[], type=rework_mark, metavar="PR=LINK", help=REWORK_HELP)
    verb.add_argument("--parts", nargs="+", type=str.strip, metavar="PART", help="each deliverable his words name; default: one, the whole order")
    verb.add_argument(
        "--incident",
        action="store_true",
        help=(
            "an incident order: --parts names its fix, and a codex job is recorded on each part for its agent to start at once;"
            " the dashboard's top strip shows it until it closes, and each other open incident order prints with its note, so a failing service stays one incident"
        ),
    )
    verb.add_argument(
        "--joins",
        metavar="ORDER",
        help=(
            "the open order this one repeats, an incident's on the same failing service: it takes that order's record and kind and starts no job,"
            " since that incident's owner, or else each unfinished job on that order, carries it, and with none its own jobs are added on it;"
            ' a "Drive tooling:" order Jev reads as repeating an open tooling order joins it unasked, while his order joins one only by this flag'
        ),
    )
    verb.add_argument(
        "--kind",
        choices=state.ORDER_KINDS,
        default="build",
        help=(
            "answer: one reader lane researches it and answers him, with no other job; build, the default: an Opus reader keeps its"
            " Researched: block before any builder, designer or codex lane starts, except the immediate fix's and a follow-up part's"
            " designer, which start at once, the follow-up's designer keeping the Researched: block its changes' builders start on, and their briefs name it. A build order of the project drive order pilot names, not --complex and holding no design job, starts its lanes with no reader."
            " An incident order is a build"
        ),
    )
    verb.add_argument(
        "--complex",
        action=argparse.BooleanOptionalAction,
        help="more than a one-fact lookup: its answer lane runs on Opus, never Sonnet; --no-complex marks a one-fact lookup, and an answer order asking several questions with neither starts no Sonnet lane; a build order of the project drive order pilot names keeps its reader only when --complex",
    )
    verb.add_argument(
        "--options-only", action="store_true", help="he asked for options only: no builder or codex job starts on the proper fix until he picks, only a reader or designer, while its immediate fix part's starts at once"
    )
    verb.add_argument(
        "--fresh",
        action=argparse.BooleanOptionalAction,
        help="his words order its design started over from scratch: every card asked on its work before them is withdrawn on them, and its builders decide implementation details with no card; with neither, Jev reads his words for it",
    )
    verb.add_argument("--once", action="store_true", help="a repair he asked to run once: its job runs it and puts up no PR")
    verb.add_argument(
        "--promote",
        metavar="WHY",
        help='on a "Drive tooling:" order a session files, the work of his the defect held back, delayed or got wrong: it skips the drive tooling backlog, so its one builder starts now and researches inline, as a repeat filed three times in 24 hours or the daily drain of the most-filed items does',
    )
    verb.add_argument("--every", type=int, default=0, metavar="MINUTES", help="the minutes he asked a line every, in place of the cadence the drive reads from his words itself: its thread owes one that long after its last line until the order is done")
    verb.add_argument(
        "--owner",
        choices=state.ROLES,
        help=(
            "the one session that adds its jobs, starts its agents and posts its reports, refusing the others'; default: a"
            ' "Drive tooling:" order the meta session, any other the role of the'
            " session adding it, else the coordinator"
        ),
    )
    verb.add_argument("--reporter", metavar="LINK", help=REPORTER_HELP)
    verb.add_argument("--watch", metavar="LINK", help=WATCH_HELP)
    verb.add_argument("--alert", dest="alerts", action="append", default=[], metavar="LINK", help=ALERT_HELP)
    verb.add_argument(
        "--project",
        metavar="NAME",
        help=f'the project the dashboard\'s Now screen files this order under, reusing one drive order unfiled lists as in use where it fits; needed on his order, and on a "{state.TOOLING}" order default {TOOLING_FILING[0]}',
    )
    verb.add_argument(
        "--workstream",
        metavar="NAME",
        help=f'the workstream inside its project the Now screen files this order under, reusing one in use where it fits; needed on his order, and on a "{state.TOOLING}" order default {TOOLING_FILING[1]}',
    )
    verb.add_argument("--title", metavar="WORDS", required=True, help=f"the few words, at most {TITLE_CHARS} characters and no link, the dashboard names this order by, since his words stay whole in its task")
    verb.set_defaults(run=order_add, users=None)
    verb = order.add_parser("set", parents=[*both, dry_opt])
    verb.add_argument("id", help="the order")
    verb.add_argument(
        "--words",
        help='a "Drive tooling:" order\'s new words; an order of his keeps his words verbatim',
    )
    verb.add_argument(
        "--state",
        choices=state.ORDER_STATES,
        help="done refuses while the bug report it fixes has not heard it is fixed",
    )
    verb.add_argument("--note", help="where the order stands, in a few words")
    verb.add_argument("--proof", help="the check that proves it done: its link, its saved file or its pasted output, never a one-line claim of what it showed")
    verb.add_argument("--from", dest="source", help=f"{FROM_HELP}; the order's thread stays unless --thread names another")
    verb.add_argument("--thread", help=f"{THREAD_HELP}, naming each open PR card of his rooted there as order add --thread does")
    verb.add_argument("--rework", action="append", default=[], type=rework_mark, metavar="PR=LINK", help=REWORK_HELP)
    verb.add_argument("--lined", action="store_true", help="a line just went in its thread")
    verb.add_argument(
        "--add-part",
        action="append",
        default=[],
        type=str.strip,
        metavar="PART",
        help="a part to add, one name a flag, each other part staying as it is; refused for a name the order holds",
    )
    verb.add_argument(
        "--drop-part",
        action="append",
        default=[],
        type=str.strip,
        metavar="PART",
        help="a part to take off, one name a flag, each other part staying as it is, and off every job and PR, stopping each live job left with no part; refused for a name the order does not hold, for its every part, which a close takes off, for a `staging test of #N before it lands` part naming an open PR, which only his answer's --dropped lets merge first, and for a change a design listed that no builder delivered, which only his answer's --dropped takes off, unless its design's newest DESIGN no longer lists it and no builder an agent or lane took or open PR carries it",
    )
    reported = verb.add_mutually_exclusive_group()
    reported.add_argument("--reporter", metavar="LINK", help=REPORTER_HELP)
    reported.add_argument("--no-reporter", metavar="LINK", help="his Slack message saying not to tell the reporters; the order then closes done with no note to them")
    verb.add_argument("--watch", metavar="LINK", help=f"{WATCH_HELP}; it replaces any thread the order watched, reading only messages newer than now")
    verb.add_argument("--follow", nargs=2, metavar=("BUILD", "ASK"), help=FOLLOW_HELP)
    verb.add_argument("--incident", action=argparse.BooleanOptionalAction, help="mark it an incident order in one save, as drive incident open opens one: its fix and cause parts, a build whose answer order's other parts stay answers, its evidence record when its note names none and a codex job on each of those two parts no unfinished codex job holds, each printed with its start line; or unmark it")
    verb.add_argument("--alert", dest="alerts", action="append", default=[], metavar="LINK", help=f"added to its alerts: {ALERT_HELP}")
    verb.add_argument(
        "--kind",
        choices=state.ORDER_KINDS,
        help="sort it again: an answer whose work turns out to need a build becomes a build, naming each open PR card of his in its thread as order add --thread does; with --part, sort only those parts",
    )
    verb.add_argument(
        "--part",
        action="append",
        default=[],
        type=str.strip,
        help="with --standing, a part it keeps standing; with --follow alone, the part the build's end line delivers; with --kind answer, a part that asks only for an answer, which the post it already holds then delivers, refused while a builder, codex or design job or a drive PR is on it, but for a builder no agent started that waits only on readers, which it takes the part off, stopped once none is left, and a builder stopped before any agent took it that holds no PR; with --kind build, work again, making an answer order a build whose other parts stay answers",
    )
    verb.add_argument(
        "--standing",
        metavar="GOAL",
        help=(
            "keep each --part standing on this goal, the one each pass of it runs on: its order never closes on it, and once no unfinished job holds it,"
            f" the watch adds and launches its next pass, a codex job on this Mac with a handoff quoting the last pass's return, at most once every {int(state.STANDING_EVERY.total_seconds() // 60)} minutes;"
            " an empty goal takes it out"
        ),
    )
    verb.add_argument(
        "--standing-check",
        metavar="COMMAND",
        help=(
            "with --standing, run each pass of each --part as this shell command in place of a codex job, with no model session:"
            f" the watch runs it detached by drive order check at most once every {int(state.STANDING_EVERY.total_seconds() // 60)} minutes, the command itself filing what it finds,"
            " and a run that exits nonzero is the part's ORPHAN line; --standing without it takes a check off"
        ),
    )
    verb.add_argument("--complex", action=argparse.BooleanOptionalAction, help="mark or unmark it as more than a one-fact lookup")
    verb.add_argument(
        "--urgent",
        action=argparse.BooleanOptionalAction,
        help="mark or unmark each --part urgent, as you judge his words want it soon, never by a keyword: its design and builder start past this Mac's caps and Sprite line at once, as drive job set --priority starts one job",
    )
    verb.add_argument(
        "--options-only", action=argparse.BooleanOptionalAction, help="limit it to options, so no builder or codex job starts on it, or lift that once he picks, adding the builder of each change a design on it listed"
    )
    verb.add_argument(
        "--fresh",
        action=argparse.BooleanOptionalAction,
        help="mark it fresh on its own words, withdrawing every card asked on its work before them, as his words ordering its design from scratch do; --no-fresh takes the mark off, withdrawing nothing again",
    )
    verb.add_argument("--once", action=argparse.BooleanOptionalAction, help="mark or unmark it as a repair he asked to run once, which puts up no PR")
    verb.add_argument(
        "--promote",
        metavar="WHY",
        help="take a drive tooling backlog item off the backlog, naming the work of his it held back, so its one builder starts now and researches inline",
    )
    verb.add_argument(
        "--priority",
        action=argparse.BooleanOptionalAction,
        help=(
            "mark it first in line while it is open, or unmark it: each builder of its starts on this Mac at once, past the builder cap, the jobs waiting their"
            " turn for a slot and the load gate, and one already waiting its turn takes it on the watch's next pass; other orders' builders wait behind it; and it owes him a line every five quiet minutes whether or not a job of it is at work"
        ),
    )
    verb.add_argument(
        "--term",
        action="append",
        metavar="NAME=MEANING",
        help="a term he uses for this order and what it is in plain words with its state now, as 'Release 2=everything but SandSQL; waits on its staging test', drawn on its channel's canvas; repeatable, the same name replaces it, an empty meaning takes it out",
    )
    verb.add_argument("--every", type=int, metavar="MINUTES", help="the minutes he asked a line every, owed that long after its thread's last line until the order is done; 0 takes it out")
    verb.add_argument("--project", metavar="NAME", help="the project the dashboard's Now screen files this order under; an empty name takes it out")
    verb.add_argument("--workstream", metavar="NAME", help="the workstream inside its project the Now screen files this order under; an empty name takes it out")
    verb.add_argument(
        "--unjoin",
        action="store_true",
        help=(
            "take it off the order it joined, by drive order add --joins or Jev's read: it no longer takes that order's note, threads or jobs, each job of that"
            " feature carries it no more and drops the words it held for its worker, and it files under its own --project and --workstream, both"
            f' needed on his order and on a "{state.TOOLING}" order default {TOOLING_FILING[0]} and {TOOLING_FILING[1]}, so its parts are unheld'
            " and its own reader can start; refused for an order that joins none"
        ),
    )
    verb.add_argument("--title", metavar="WORDS", help=f"the few words, at most {TITLE_CHARS} characters and no link, the dashboard names this order by")
    verb.add_argument(
        "--record",
        metavar="SEQ",
        help="the report record the communicator posted; a close from the communicator's session needs it and closes only an order its close attr names",
    )
    verb.add_argument("--users", type=int, metavar="SEQ", help=USERS_HELP)
    verb.set_defaults(run=rehearsed(order_set))
    verb = order.add_parser("unfiled", parents=both, help="each open order that names no project, workstream or title yet, then the project and workstream pairs in use")
    verb.set_defaults(run=order_unfiled)
    verb = order.add_parser(
        "pilot",
        parents=both,
        help="start the reader pilot on one project, whose build orders added from now on, unless marked --complex or holding a design job, start their builder with no reader; with no project, end it",
    )
    verb.add_argument("project", nargs="?", help="the project orders are filed under, as drive order unfiled lists it; left out, the pilot ends")
    verb.add_argument("--days", type=float, default=2, help="how long orders added count as the pilot's, 2 by default")
    verb.set_defaults(run=order_pilot)
    verb = order.add_parser(
        "backlog",
        parents=both,
        help="turn the drive tooling backlog on or off: on, a Drive tooling order a session files waits with no job, a repeat tallies on it, and its one builder starts with no reader once it is promoted",
    )
    verb.add_argument("switch", choices=("on", "off"), help="on or off")
    verb.set_defaults(run=order_backlog)
    verb = order.add_parser("check", parents=both, help="run a standing part's check, the pass the watch starts in place of a codex job, keeping its exit code and closing line on the part")
    verb.add_argument("id", help="the order")
    verb.add_argument("--part", required=True, type=str.strip, help="the standing part whose check it runs")
    verb.set_defaults(run=order_check)
    verb = order.add_parser("posted", parents=both, help="the communicator posted a line in an order's thread, its cards' or its PRs' cards': a progress line, delivering no part, or the answer a part owes him, which the report record it carries delivers, there or in any other channel; or the session holding the order closes a part from the drive's post that already answered it")
    verb.add_argument("id", help="the order")
    posted = verb.add_mutually_exclusive_group(required=True)
    posted.add_argument("link", nargs="?", help="the Slack link of the drive's post; one in the order's threads keeps a promised time and moves the order's last line")
    posted.add_argument("--undo", action="store_true", help="take back the delivered mark of the part --part names, so it counts as undelivered again")
    verb.add_argument(
        "--part",
        help=(
            "with --undo, the part whose mark it takes back; from the communicator with --record, only a part its part attr names;"
            " from the session the order's ORPHAN lines reach, with or without --record, or the communicator holding it with none, the part the drive's post at the link already answered,"
            " posting nothing, never work he ordered, which only drive job prove's passing proof delivers"
        ),
    )
    verb.add_argument(
        "--record",
        metavar="SEQ",
        help=(
            "the report record the post carries: it delivers each part its part attr names, `<o>:<part>` items separated by `;`, on"
            " this order and every other order an item names, counted in any channel, or each part of an order its close attr"
            " names; a record naming neither is a progress line and delivers no part"
        ),
    )
    verb.set_defaults(run=order_posted)
    verb = order.add_parser(
        "told",
        parents=both,
        help=(
            "the communicator posted the one note each of the order's reporters gets in the thread of their message, or in the"
            " group DM of that reporter, him and the bot when the bot cannot post there: a bug's fix-live note once its proof is kept, or a"
            " question's answer once it is posted to him; done refuses until each is recorded"
        ),
    )
    verb.add_argument("id", help="the order")
    verb.add_argument("link", help="the Slack link of the drive's note in the reporter's thread, or with --group-dm in their group DM with him and the bot")
    verb.add_argument(
        "--group-dm",
        metavar="USER",
        type=slack_user,
        help="the reporter's Slack user id: the bot cannot post in the reporter's thread, so the note went to the group DM of that person, him and the bot",
    )
    verb.add_argument(
        "--reporter",
        metavar="LINK",
        help="with --group-dm, the message of the reporter whose group DM the note went to, needed while more than one of the order's reporters is untold",
    )
    verb.set_defaults(run=order_told)
    verb = order.add_parser("show", parents=both, help="drive status for these ids")
    verb.add_argument("ids", nargs="+", metavar="ID", help=f"what to read: {ID_FORMS}")
    verb.set_defaults(run=status, prs=False)

    incident = verbs.add_parser("incident", help="his incidents").add_subparsers(dest="action", required=True)
    verb = incident.add_parser(
        "open",
        parents=both,
        help=(
            "open an incident in one call: his order, marked an incident, with a codex job on its fix and one on each diagnosis part,"
            " each printed with the line its brief ends on, and its evidence record opened and named on the order's note"
        ),
    )
    verb.add_argument("words", help="his words, or the alert's text, verbatim")
    verb.add_argument("--from", dest="source", default="terminal", help=FROM_HELP.replace("he wrote it", "it came from"))
    verb.add_argument("--thread", help=f"{THREAD_HELP}; default: the thread the --from message is in")
    verb.set_defaults(due=None)
    verb.add_argument("--reporter", metavar="LINK", help=REPORTER_HELP)
    verb.add_argument("--alert", dest="alerts", action="append", default=[], metavar="LINK", help=ALERT_HELP)
    before = verb.add_mutually_exclusive_group()
    before.add_argument(
        "--release",
        action="store_true",
        help="a release or new build came before the break: its parts are fix and first check, and no rollback or cause hunt starts before the first check returns",
    )
    before.add_argument(
        "--no-rollback",
        action="store_true",
        help="no build lacks the failing path: its parts are fix, stop and cause; with neither flag they are fix, rollback and cause",
    )
    verb.add_argument("--project", metavar="NAME", required=True, help="the project the dashboard's Now screen files it under")
    verb.add_argument("--workstream", metavar="NAME", required=True, help="the workstream inside its project the Now screen files it under")
    verb.add_argument("--title", metavar="WORDS", required=True, help=f"the few words, at most {TITLE_CHARS} characters and no link, the dashboard names it by and its evidence record is titled by")
    verb.add_argument("--users", type=int, metavar="SEQ", help=USERS_HELP)
    verb.set_defaults(run=incident_open,incident=True, joins=None, kind=None, complex=False, options_only=False, once=False, every=0, owner=None, watch=None)
    verb = incident.add_parser(
        "stop",
        parents=both,
        help=(
            "the turn the rollback finds no build that lacks the failing path: a stop part and its codex job, with apply authority,"
            " whose PR, the incident's immediate fix, turns the failing automatic path off and lands at once as an incident fix,"
            " its body quoting the bug from the record, until the root fix's PR deletes it"
        ),
    )
    verb.add_argument("id", help="the open incident order")
    verb.set_defaults(run=incident_stop)
    verb = incident.add_parser(
        "freeze",
        parents=both,
        help=(
            "in the turn users are unblocked, never before: freeze the broken service's other releases for 12 hours with tools/ci"
            " release freeze from the coordinator's checkout, its id on the order's note for the job that merges the root fix to lift"
        ),
    )
    verb.add_argument("id", help="the open incident order")
    verb.add_argument("components", nargs="+", metavar="COMPONENT", help="the broken service's release component, such as sandsql, covering every stack it deploys")
    verb.set_defaults(run=incident_freeze)

    boxes = verbs.add_parser("mac-box", help="Mac test boxes the drive's pool hands to jobs").add_subparsers(dest="action", required=True)
    verb = boxes.add_parser("handed", parents=both, help="the pool handed a job a Mac box: its mac-box:<box> or mac-box:any wait ends, so the watch frees it")
    verb.add_argument("job", help="the job the box went to")
    verb.add_argument("box", help="the box's name, as tools/ci mac-box up named it")
    verb.add_argument("--host", help="the Mac host the box runs on")
    verb.set_defaults(run=mac_box_handed)
    slot = verbs.add_parser("slot", help="staging slots, a first-in first-out lease on each staging env").add_subparsers(dest="action", required=True)
    verb = slot.add_parser("claim", parents=both, help="hold a staging env's slot now, or queue for it", description="Hold the env's slot now when no job holds it, its lease ending the minutes given past the staging-claim rule's five-minute wait, else queue last; the watch hands a queued job the slot with a GO once the holder gives it back, finishes or outlives its lease.")
    verb.add_argument("env", help="the staging env, such as plat-usw2-staging")
    verb.add_argument("--job", required=True, help="the job claiming it")
    verb.add_argument("--minutes", type=int, required=True, help="how long its pass runs")
    verb.add_argument("--pr", action="append", default=[], help="a PR its cut carries")
    verb.add_argument("--cases", help="the cases its pass runs")
    verb.set_defaults(run=slot_claim)
    verb = slot.add_parser("release", parents=both, help="give a staging env's slot back, or leave its queue")
    verb.add_argument("env", help="the staging env")
    verb.add_argument("--job", required=True, help="the job giving it back")
    verb.set_defaults(run=slot_release)
    verb = slot.add_parser("hold", parents=both, help="hold a staging env's slot for one job until a time, from the top coordinator")
    verb.add_argument("env", help="the staging env")
    verb.add_argument("--for", dest="held", help="the job the slot is held for, never an order")
    verb.add_argument("--until", help="when the hold ends, an RFC 3339 time with its offset")
    verb.add_argument("--reason", help="why, in plain words, naming the record that set it")
    verb.add_argument("--clear", action="store_true", help="drop the slot's hold")
    verb.add_argument("--take", action="append", default=[], metavar="JOB", help="take the slot back from a job it went to by mistake, holding or riding its cut, without handing it on: the job goes back first in the queue and hears NO-START; repeatable")
    verb.set_defaults(run=slot_hold)
    verb = slot.add_parser("hand", parents=both, help="hand a staging env's slot to a job in its queue now, from its holder or the top coordinator")
    verb.add_argument("env", help="the staging env")
    verb.add_argument("--to", required=True, help="the queued job taking the slot")
    verb.set_defaults(run=slot_hand)
    verb = slot.add_parser("queue", parents=both, help="each staging slot's holder, its lease's end and the jobs waiting, in order")
    verb.add_argument("env", nargs="?", help="the staging env; default every one")
    verb.set_defaults(run=slot_queue)

    queue = verbs.add_parser("work", help="the work queue: items workers pull in groups").add_subparsers(dest="action", required=True)
    verb = queue.add_parser("enable", parents=both, help="turn a kind of work over to the queue, or back with --off")
    verb.add_argument("kind", choices=work.KINDS)
    verb.add_argument("--off", action="store_true", help="give the kind back to its own jobs")
    verb.set_defaults(run=work_enable)
    verb = queue.add_parser("pull", parents=both, help="claim a worker's next group of items")
    verb.add_argument("--worker", required=True, help="the worker's job id")
    verb.set_defaults(run=work_pull)
    verb = queue.add_parser("done", parents=both, help="take one item's whole return from stdin for the worker holding it")
    verb.add_argument("worker", help="the worker's job id")
    verb.add_argument("item", help="the item's job id")
    verb.add_argument("stdin", choices=["-"], help="read the return from stdin")
    verb.set_defaults(run=work_done)
    verb = queue.add_parser("start", parents=both, help="start a worker's agent on this Mac")
    verb.add_argument("worker", help="the worker's job id")
    verb.set_defaults(run=work_start)
    verb = queue.add_parser("show", parents=both, help="the queue's kinds, worker and ready items")
    verb.set_defaults(run=work_show)

    job = verbs.add_parser("job", help="background agents").add_subparsers(dest="action", required=True)
    verb = job.add_parser(
        "add",
        parents=both,
        help=(
            "start a job on an order's parts and print its id; a builder or codex job on a part, or a --branch of its repo, another unfinished one carries goes to"
            " that one, a part or branch having one change job outside an incident, a builder landing or releasing what is built and the change waiting on that one aside: refused while it runs, folded into it while no worker took it,"
            " and replacing it, its PRs taken and it stopped, once it returned"
        ),
    )
    verb.add_argument(
        "--builds-nothing",
        action="store_true",
        default=None,
        help="a builder whose whole work holds no checkout or compiler, such as a read, as a builder whose goal reads prove <PRs> live is marked or not: it holds no slot of this Mac's builder cap, starts here at once past the load gate and starts beside any builder carrying its part, since it changes no code",
    )
    verb.add_argument(
        "--priority",
        action="store_true",
        default=None,
        help=(
            "the job alone marked urgent, such as a drive tooling fix for what blocks his answers or this Mac's load: its lanes start past this Mac's"
            " caps, load gate and its project's daily budget, with none of a priority order's seat or five-minute lines"
        ),
    )
    verb.add_argument("kind", type=job_kind, choices=state.JOB_KINDS, help="what the job is; designer, its agent type's name, is design")
    verb.add_argument("--order", required=True, help="the order whose parts it delivers")
    verb.add_argument("--goal", required=True, help="its deliverable, one line")
    verb.add_argument("--agent", help="the agent id the Agent tool returned")
    verb.add_argument("--repo", help="owner/name")
    verb.add_argument("--branch", help="the branch it works on; a builder added on the branch an open PR already pushed, such as a migrated drive's, starts at once with no reader's research")
    verb.add_argument("--worktree", help="its worktree's path")
    verb.add_argument("--remote", action="store_true", help="a builder that starts as a remote lane on a Sprite")
    verb.add_argument(
        "--model",
        choices=codex.MODELS,
        help="the model a codex job's codex-ask run takes as -m, default codex-ask's own, sol; astra only on an order whose words name astra",
    )
    verb.add_argument("--part", action="append", default=[], help='a part of the order this job delivers, a "port of #N" the order lacks added to it; default: its only part')
    verb.add_argument(
        "--lands",
        action="append",
        default=[],
        metavar="PR",
        help="a drive PR he approved, or an exempt class lands, that this builder lands and builds nothing on, so it starts with no research",
    )
    verb.add_argument(
        "--release",
        help=(
            "the one release start or hotfix to start now, as the release provider's start() reads it, its --order naming his answer approving it: refused unless the top"
            " coordinator adds the job, whatever order it serves, and Opus reads that answer as his go, given after any hold of his on every"
            " release, or it is an incident's order, but one on the release provider's rehearsal envs alone, which needs no --order; it starts"
            " from the top's checkout, then the job's brief names the command and its build, and its agent follows it and reads its approve hold;"
            " outside his hold, the top coordinator's --reading starts it with no --order and whatever Opus reads"
        ),
    )
    verb.add_argument(
        "--reading",
        help=(
            "with --release, from the top coordinator's session only: what it read in his words as ordering that release. Outside his hold it starts"
            " the release on that reading, with or without an --order, and Opus's objection prints as a warning; under his hold it starts it only"
            " when Opus is unsure or gave no reading. The job keeps it, so the release's resume, approve, rerun and rollback run on it. A dangerous"
            " release still waits for his presence"
        ),
    )
    verb.add_argument(
        "--waits",
        action="append",
        default=[],
        metavar="CARD|PR|RELEASE|BUILD|JOB|PART",
        help=(
            "a card of his still open, a PR the drive tracks as <owner>/<repo>#<n> or release:<service>:<owner>/<repo>#<n>, a Buildkite build by its link, a part of an open order as <order>:<part>,"
            " a teammate's reply as reply:<the question's permalink>, another drive's next record as reply:cci:<the seq of the record it follows>, or a job"
            " of this drive still at work, of any kind, a reader that just started included, as job set takes them, that this job waits on: it is recorded"
            " waiting with no agent, so its part is never in no job, and the watch says FREE once the last wait ends, when its agent starts"
        ),
    )
    verb.set_defaults(run=job_add)
    verb = job.add_parser("set", parents=[*both, dry_opt, waits_opt, builds_opt, priority_opt])
    verb.add_argument("id", help="the job")
    verb.add_argument(
        "--agent",
        help="the agent id the Agent tool returned, the dispatch the job records, which puts it back on its Orca lane, or another Orca dispatch;"
        " either dispatch records the Run, Task, terminal, lane and Sprite or Mac Orca shows for it",
    )
    verb.add_argument(
        "--state", choices=state.JOB_STATES, help="stopped: it ends unfinished, under its own lock, and its lane on a Sprite ends as drive job end ends one; every other state comes from the line the job returns, and one it holds changes nothing"
    )
    verb.add_argument("--branch", help="the pushed branch it works or built on, refused for a builder or codex job while another unfinished one works that branch of its repo")
    verb.add_argument("--said", help="its latest line")
    verb.add_argument("--due", help="its clock: when it must have returned, said once as DUE if it still runs then, a time or +10m")
    verb.add_argument("--repro", help="the job's pick-up-and-run reproduction file, kept in the drive")
    verb.add_argument("--order", help="with --repro, the order, its own or one it carries, whose proof the file is: a prover of a release cut keeps one per order, and drive job prove runs each on its own timeout, each one's exit 0 delivering that order's parts and posting its close while the others still run")
    verb.add_argument("--pr", action="append", default=[], help="a PR it opened or took over; a PR the drive tracks then names it on its row, as drive pr add --job moves one; a PR the drive does not track, kept on a builder stopped before any agent took it, such as a teammate's open PR its reader found already doing its work, carries its parts while GitHub reads it open, and once it merged the part's ORPHAN line names the drive order posted that delivers it; one kept on a live job can wake it, its WAITING waiting on it until the watch's GitHub read finds it merged or closed")
    named = verb.add_mutually_exclusive_group()
    named.add_argument("--part", action="append", default=[], help='another part of the order this job delivers, or of the one order --carries names, a "port of #N" that order lacks added to it')
    named.add_argument("--parts", nargs="+", type=str.strip, metavar="PART", help="the parts of its own order this job delivers, replacing its list")
    verb.add_argument(
        "--carries",
        action="append",
        default=[],
        metavar="ORDER",
        help="another order whose part this job delivers, until it is done, refused for a builder or codex job while another unfinished one carries that part, as --part and --parts are; a reader or design carrying a build order's part is that part's research, so its kept Researched: block starts the part's builder, designer or codex lane with no reader of its own, and it changes no job's state, as --part and --parts change none; it names each open card of his carrying the job's PRs and leaves it up, since its stack can meet his words already: only --rework withdraws one",
    )
    verb.add_argument(
        "--rework", action="append", default=[], type=rework_mark, metavar="PR=LINK", help=f"{REWORK_HELP}, to the one --carries order or else the job's own; refused for a PR of another job"
    )
    verb.add_argument(
        "--card",
        help=(
            "the Slack link of a question card the job asked on, which carries its parts until he answers it; each card"
            " keeps its own question, so a job holds every card it asked on, each closed by his answer to it"
        ),
    )
    verb.add_argument(
        "--heard",
        action="store_true",
        help="mark heard his held words the drive sent the session holding the job's order to read, as on a job that returned or waits only on his cards, so they stay off its agent",
    )
    verb.add_argument(
        "--told",
        metavar="LINK",
        help=(
            "the drive's post in an incident's thread linking each card --card or --waits names, what it blocks and each job building its"
            " recommended option meanwhile, or that nothing is built before his pick, read from the line say kept on each card's question when it posted there;"
            " a job on an open incident order takes a card only with it"
        ),
    )
    verb.set_defaults(run=rehearsed(job_set))
    verb = job.add_parser(
        "ask",
        parents=both,
        help="keep a job's ASK in the drive beside its other open questions, refused unless it is in the shape he reads, and set the job waiting, a builder kept running to build the recommended option; --drop throws away one kept with no card",
        description=(
            "Check a job's ASK block: `ASK <the question>`, one to three summary lines (the first naming the work it serves,"
            " `{order:<o>}`, `port of #N` or Drive tooling, and what your pick changes, as `For {order:<o>}: your pick decides"
            " <what it changes for you>.`, then the recommendation; his card's line above its buttons, read from the drive's jobs,"
            " says what is built while he decides, so the summary says none of it),"
            " `Show me:` working each option of a design out to what it will look like once built, its real calls, rows,"
            " flow or what a caller gets, as a compare panel named for it or a picture captioned with its number, which Opus"
            " reads option by option, options Jev reads as choosing between architectures each as its own arch diagram"
            " captioned `N · …`, and on any other card a Show me only"
            " when it shows what the words cannot (a mermaid flow, a compare or a table for options, a chart for numbers,"
            " an arch diagram, whose JSON parses, the real call in a fence naming its language for a code or API shape, or a"
            " picture on a line of its own as ![caption](/absolute/path.png); never words, JSON, YAML, logs, a terminal"
            " session, an HTTP exchange, command output or a stack trace in a fence or in a compare panel's code, which"
            " names the language it is written in, never markdown or text), captions of at most 25 words,"
            " two to four `- ` options of at most 25 words, each saying what he gets and what it costs, with exactly one ending"
            " (recommended), each followed by one plain-text line indented two spaces, at most 60 words, that his card draws"
            " under it, saying in prose what the option is: what runs and when, where its data lives, and what it requires"
            " touching or building, by name, with any cost every user pays, never fields such as `Does:`; no mechanism such as"
            " polling, a push or an event named by a bare word on the question or an option, the question, summary and"
            " options at most 150 words in all, in plain words, each thing he"
            " could open linked where it is named, then `Findings:` with the evidence, its `Simplest:` line naming the option"
            " that is the simplest design doing his words, its `Root cause:` line naming the"
            " structure that lets the defect happen with the number that measures it and its `First principles:` line the"
            " recommended option that removes it, or `Root cause: fixed: <that structure>, removed by <the merged PR's link>`,"
            " `not found: <what measures it>, by <the job measuring it>` or `none: <why>`, at most 60 words, which his card"
            " draws under its first line, the job left off. Keep it in the drive in a file of"
            " its own, add it to the job's open questions, each open until his answer to its own card, and draw its card"
            " in his DM in the same call under the waits id minted for it, printing the file and the card; a card that"
            " does not draw goes up on the watch's next read. A block asking again, in the same words, what one of the"
            " job's questions asks that his answer has not closed, or with --redraws in new words what the card it names"
            " asks, open or withdrawn, the job's own or another job's on one of its orders that this job takes over, takes"
            " that question's place and redraws that one card under its waits id, every other open card"
            " of the job asking the same shown asked again with a link to it; one asking in the same words what another"
            " job's open question asks is refused, naming the wait that frees this job at his answer. One he already answered, in the same words, on a card or under"
            " a record keeping his words, is kept settled by that answer with no card; no answer he gave another question"
            " settles it. Refuse it with every way it is out of shape."
        ),
    )
    verb.add_argument("id", nargs="?", help="the job whose ASK this keeps, or with --check checks")
    verb.add_argument(
        "file", nargs="?", help="the file holding the ASK block, or - to read it from stdin; default: a remote lane's latest report"
    )
    verb.add_argument(
        "--check",
        metavar="FILE",
        help=(
            "only check the ASK block in this file, or - for stdin, by every check drive job ask <j> runs, its words counted as his"
            " card draws them, each {order:<o>} as the opening of his order's words, and print every fix it needs at once, or ready,"
            " or for a question he already answered in the same words that it draws no card; keeps nothing"
        ),
    )
    verb.add_argument(
        "--unsettled",
        metavar="WHY",
        help="one string naming each ruling of his a refusal or Opus's finding quoted, such as No sweepers, and why this option keeps what it names; it marks the card as asking him to lift that ruling, which holds its job's PR on an incident too",
    )
    verb.add_argument(
        "--redraws",
        metavar="WAITS",
        help=(
            "the waits id of the card this block asks again in new words, which it redraws in place: an open or withdrawn card of"
            " the job, or of another job on one of its orders or whose summary opens with one as {order:<o>}, whose work it carries on,"
            " whose question stays on that job folded into"
            " this one, so that job asking it again draws no card; refused for a card his answer ended and for any card of another order asking about none of its orders"
        ),
    )
    verb.add_argument(
        "--drop",
        metavar="FILE",
        help=(
            "in place of the file, throw away the job's question kept in this file with no card drawn for it, such as one refused"
            " for an answer of his that settles it: it leaves the job's open questions and the file is deleted; refused for a"
            " question on a card, which only his answer closes, so --answer names it"
        ),
    )
    verb.add_argument(
        "--answer",
        metavar="ID",
        help=(
            "with --drop, end the job's own open question on his card kept in that file on this recorded answer of his, which"
            " already settles it, such as one choosing its recommended option, its card closing in place on his words and the"
            f" link where he gave them with no buttons; with {state.TOOLING_DETAILS_RULING}, his ruling on tooling details, end a"
            " question of a Drive tooling order asking him only a detail decided on its recommended option, its card showing that"
            " option decided without him and a link to his ruling's message, refused on any other order and for a card kept"
            f" unsettled; with {state.UNWAITED_RULING}, his words that the drive not wait on him, end it decided by the drive on its recommended option,"
            " recorded as an answer of its own whose id `drive answer <id> --dropped` takes; neither ruling decides a card on an order limited to options;"
            " either way it prints who builds on it; refused for a question on no card and an id cc-notes keeps no words of his under"
        ),
    )
    verb.set_defaults(run=job_ask)
    verb = job.add_parser(
        "card",
        parents=both,
        help="print the question card a kept ASK block draws, its Show me visuals drawn as pictures",
        description=(
            "Read a kept ASK block and print its question card as JSON for `say(card=…)`: its question as"
            " the title, its summary, then a `*Show me*` line and its Show me with each fenced visual (code naming its"
            " language, arch, mermaid, dot, chart or compare) drawn by site/visuals.js as a picture on a line of its own,"
            " titled with the caption line above it; a fence of words, with no language or `text`, which only a block"
            " kept before `drive job ask` refused one holds, stays as written for Slack to draw as a code block; a fence"
            " of raw data or tool output, or one that does not draw, is left off, and a"
            " Show me left empty is dropped; its options, the recommended one, each option's description as the hint"
            " cc-slack draws under it, the drive, and its waits id. Each reference in its summary, Show me and descriptions,"
            " such as {at:<seq>} or {pr:<number>}, is filled as `say` fills a post's: a time as its words, a PR, card or record"
            " as a link. A question the"
            " job asked before draws its newest kept block under the waits id it already holds, whatever --waits names, so"
            " every ask of one question redraws its one card, unless --waits names a card another of the job's questions"
            " holds, which it then redraws. A question holding none takes --waits, and one of the job's that held it"
            " before, a card this redraws, hands over its card and leaves the job; a waits id naming no job of the drive"
            " only draws the card. Refused, under any waits id, when the question kept in the file ended, answered,"
            " withdrawn or superseded, naming the words it ended on, or `drive job ask` refused it; and refused when his"
            " words under that card answered it: they are his answer, and a question they leave open goes on a card of"
            " its own. A block kept under older rules redraws as kept, its Show me's visuals never checked again, and"
            " markdown in its question or options, which Slack draws raw, drawn as the words it marks. Name a job first,"
            " `drive job card <j>`, to draw its newest kept ASK, or the one a file after it names, under the waits id its"
            " card already has."
        ),
    )
    verb.add_argument("ask", metavar="JOB|FILE", help="a job, whose newest kept ASK block draws, or the kept ASK block itself, the file drive job ask printed")
    verb.add_argument("file", nargs="?", help="after a job, which of its kept ASK blocks draws; default: its newest")
    verb.add_argument(
        "--waits",
        help=(
            "the card's waits id, <drive>:<job>-<n>, for a question holding none yet, or the waits id of a"
            " card of the job to redraw; a question holding one is drawn under it; after a job, default: the waits id its card already has"
        ),
    )
    verb.set_defaults(run=job_card)
    verb = job.add_parser(
        "report",
        parents=[*both, dry_opt],
        help="keep a job's whole return in the drive, or print the returns it took",
        description=(
            "A job returns one line; the rest of its return, the inputs to what he would be told, one fact a line,"
            " is kept here before it returns, piped in on stdin. With -, keep stdin in the job's history, which is its report,"
            " set its state and said line from its first line, and print where; a builder's or codex job's BUILT on a branch of its repo another unfinished"
            " change job works is refused while that job runs and otherwise replaces it, as drive job add --branch on that branch does; with nothing, print the returns the job took,"
            " its own agent's, its lane's and anyone else's, newest first, the newest whole and each earlier one as its first line."
        ),
    )
    verb.add_argument("id")
    verb.add_argument("stdin", nargs="?", choices=["-"], help="- to keep the job's whole return from stdin; default: print the returns it took, newest first")
    verb.add_argument("--all", action="store_true", help="print every return the job took whole")
    verb.set_defaults(run=rehearsed(job_report))
    verb = job.add_parser(
        "brief",
        parents=[*both, brief_opt],
        help="write a job's brief from his order's words, each ruling's words and the earlier jobs' transcripts, and print where",
        description=(
            "Write the brief reference/jobs.md shapes into the drive and print its path: Order from the words the order"
            " holds, verbatim; From, where the order came from, its --from; Goal from the job; Decisions that bind from each --ruling's recorded answer, each one the brief of any job on its order binds, and each one that"
            " settled a question of the reader or design its Researched: line names, its title and his words above the drive's Notes:; Repo rules from the .claude/long-running.toml in the drive's checkout; Transcripts from each"
            " earlier job on the order; Scratch, by default a folder of the drive's;"
            " the Drive line; a reader's on a build order, Research: the Researched: block this build needs; and each line"
            " the flags name. A flag left out keeps the job's earlier brief's, a flag given an empty value, such as --mac \"\", drops the earlier brief's, each --ruling adds to the rulings every brief on its order binds, and the output names each kept and each dropped;"
            " a brief naming no --serves serves each port of #N part its job takes, else drive tooling on a tooling order and his order on any other. An adopted PR's job, waiting with no agent, is briefed only"
            " with --handoff for an event that needs action. Its first line, `Read your brief first: <path>`, is a local"
            " job's whole Agent prompt, which binds the agent to the job, and a remote job launches from it with drive job launch <j>."
        ),
    )
    verb.add_argument("id", help="the job")
    verb.set_defaults(run=job_brief)
    verb = job.add_parser(
        "launch",
        parents=[*both, brief_opt],
        help="start a job's next worker: its first Orca lane on a Sprite, or on this Mac with --local, or the next of one that ended",
        description=(
            "Start a job's next worker by the one relaunch drive job resume shares, under the job's own lock, so a launch or resume"
            " already starting one finishes first, about 150 s, and this one acts on the worker it started. A job with no worker yet"
            " starts its Orca lane on a Sprite, or on this Mac with --local, from the drive's own checkout, moved first with one"
            " fetch to the head of the coordinator checkout's default branch, so the lane gets the cc-remote and tools that head pins."
            " A lane whose worker ended starts its next as drive job resume does, with no message. A job held by an Agent another"
            " session started, which this session cannot SendMessage, or by an agent on this Mac whose last response ended on an API"
            " error or a refusal, starts on a handoff brief carrying the job's brief, that agent's branch, last line, report and PRs,"
            " and the job is recorded as this session's lane. A codex job starts no worker: its codex-ask run starts here with"
            " --dispatch on its brief and --schema holding its final message to a state, line and report, which the watch takes as its"
            " return once the run ends. A finished pass of a standing part is refused, writing and starting nothing, naming the"
            " part's live pass or when the watch starts its next, since the watch alone starts each pass of one. A stopped job is refused"
            " the same way, starting nothing: drive job add on its parts adds the job that takes them. It takes every"
            " drive job brief flag and writes the brief from them as that verb does before it starts anything, so a flag argparse"
            " or the brief's checks refuse starts no worker; with none it starts on the brief the job holds."
        ),
    )
    verb.add_argument("id", help="the job")
    verb.add_argument(
        "brief",
        nargs="?",
        help="the file holding the job's brief, or - to read it from stdin, refused beside a drive job brief flag; default the one drive job brief, or this launch's flags, wrote",
    )
    verb.add_argument(
        "--local",
        action="store_true",
        help="start it on this Mac as its own claude --bg session, as drive job start starts one, never as an Orca worker; for a job on a Sprite whose lane ended or whose Sprite Orca cannot reach, its next worker starts here with no wake of that Sprite, a resume holding the job's lock standing down for it, after a save of what that Sprite holds on no remote; when the save fails the Sprite is kept, and the handoff names what each lane of the job saved on GitHub",
    )
    verb.set_defaults(run=job_launch)
    verb = job.add_parser(
        "start",
        parents=both,
        help="start a briefed builder's or reader's agent on this Mac as its own claude --bg session, as the watch does",
        description=(
            "Start a briefed builder's or reader's agent on this Mac as its own claude --bg session on its brief, the prompt an Agent"
            " call would start it on, under the job's own lock: refused as the agent_brief hook refuses that Agent call of the session"
            " holding its order, which counts its builder slot or holds it waiting its turn, and while an earlier start of it is in"
            " flight or at work. Every hook of that session binds it as the job's agent of the session holding its order, and"
            " drive job resume posts each text to its own cci address, which its next step reads or a claude --bg --resume wakes it on,"
            " so no session's SendMessage relays them. The watch runs it on each job nothing holds; a codex, design or Sprite job is refused."
        ),
    )
    verb.add_argument("id", help="the job")
    verb.set_defaults(run=job_start)
    verb = job.add_parser(
        "resume",
        parents=both,
        help="answer a job's question, or send it the next event, starting its next worker when its last ended",
        description=(
            "Answer a remote lane's question, or send it the next event, once Orca shows its worker alive, by the one relaunch drive"
            " job launch shares, under the job's own lock, so a resume arriving while another starts the job's next worker waits"
            " for it, about 150 s, and sends that worker the text; one that finds the job stopped once it holds that lock exits 2,"
            " sending, holding and starting nothing, as drive job launch refuses one. A finished pass of a standing"
            " part is refused the same way, naming the part's live pass or when the watch starts its next, which carries the texts"
            " and answers that pass holds, since the watch alone starts each pass of one. One that finds another verb holding that lock while Orca cannot reach the lane's Sprite exits 2 at once, sending and holding nothing, rather than wait up to 1800 s behind a wake of that Sprite, and names drive job launch --local; one holding the lock while a drive job launch --local waits for it stands down, exiting 2 before it wakes, sends or starts anything, the texts the job holds left for that launch's handoff. An agent on this Mac whose last response ended on an API error or"
            " a refusal hands the job to a lane on a Sprite, or on this Mac when its brief declares a step only this Mac runs, with a handoff carrying the job's brief and this text. When its "
            "worker finished and Orca still holds its terminal, on its Sprite or on this Mac, start the job's next worker "
            "in that terminal, or, when Orca no longer finds that terminal or worktree, as a dead worker's; when its worker is dead, relaunch the job on a fresh Sprite, or in a fresh worktree on this "
            "Mac, on the same Run. Before a relaunch on a fresh Sprite it saves what the dead lane's Sprite holds that GitHub does not: over ssh "
            "it commits each worktree's uncommitted changes there and pushes each commit, branch or stash on no remote as saved/<the dead lane>/<its "
            "branch>-<sha>, on the token gh auth token prints, lent to that one push through the ssh session's stdin, so it never reaches the Sprite's disk; "
            "its output and the new worker's handoff name each branch GitHub then holds under saved/<the dead lane>/, by one read of its refs. When that Sprite does not answer or the save fails, it relaunches the job on that "
            "Sprite instead, in the checkout the dead lane left. When Orca cannot reach the lane's Sprite, it wakes that Sprite with "
            "orca-reclaim.py --wake and reads the lane again: a worker that takes messages gets the text, and any other lane relaunches on "
            "that Sprite in the checkout it left with no save, or on a fresh Sprite when that Sprite starts no worker. Either new worker "
            "gets a handoff carrying the job's brief, its branch, its last line, "
            "its report, its PRs and this text. A remote lane's text is held on the job under one Orca request id before any "
            "Orca call: when Orca cannot tell whether the lane lives, or refuses the send, it stays held and the resume exits 0 saying so, "
            "the watch sends it once Orca reads that worker alive, under the same id so Orca never applies it twice, and a "
            "relaunch meanwhile carries every held text in its handoff; a relaunch Orca refuses exits 2 with Orca's refusal, "
            "the text still held. With no text, it sends what the job holds now wherever a text would go: to an agent on this Mac that a session of the drive started and still holds, each held text whole, oldest first, as its own handoff record, printing one SendMessage line per record, to an agent on this Mac its starter no longer reaches, one that died or whose turn ended while this Mac's builder slots are full, in its next worker's handoff, each held text under the heading naming its sender, or that it holds none. A job held by an Agent another session started, which this session cannot "
            "SendMessage, gets a handoff brief instead, carrying the texts the job holds and this one, and prints its `Read your brief first:` line: an agent "
            "this session starts on it takes the job as this session's. A builder no worker has started that waits its turn for a slot on this Mac holds the text for its first worker,"
            " starting nothing, and the watch starts that worker as its turn comes, its brief carrying the text. A codex job takes the text under the heading naming its sender: held while a codex-ask run "
            "goes, which landing_guard hands that run at its next Bash call, the watch's launch carrying it once the job returns still open when the run ended first, else carried by a run started now."
        ),
    )
    verb.add_argument("id")
    verb.add_argument(
        "text",
        nargs="?",
        type=lambda value: sys.stdin.read() if value == "-" else value,
        help="his answer, or the event the lane acts on next, or - to read it from stdin; left out, the texts the job holds",
    )
    verb.set_defaults(run=job_resume)
    verb = job.add_parser(
        "read",
        parents=both,
        help=f"what the watch starts on a remote lane's READ, LOG or DRIVE question: claim it by creating its answer file, run it here unless asked over {reads.LANE_GIVES_UP} s ago, and send the answer",
    )
    verb.add_argument("id")
    verb.add_argument("message", help="the question's Orca message id")
    verb.set_defaults(run=job_read)
    verb = job.add_parser(
        "release",
        parents=[drive_opt],
        help="what drive job add --release starts in a process of its own: start the job's release, keep the build it names as the job's Release build: line and write its brief, its lines in the drive's releases.log",
    )
    verb.add_argument("id")
    verb.add_argument("--session", help="the session that added the job, which the release tool records as the release's starter")
    verb.set_defaults(run=job_release)
    verb = job.add_parser("check", parents=both, help="what drive job report starts after it keeps a Sprite lane's return: read on GitHub the PRs its READY names and wake the lane with a refusal")
    verb.add_argument("id")
    verb.set_defaults(run=job_check)
    verb = job.add_parser(
        "replies", parents=both, help="every result and question a remote lane sent, and every return drive job report kept, oldest first, with when each came"
    )
    verb.add_argument("id")
    verb.set_defaults(run=job_replies)
    verb = job.add_parser(
        "held",
        parents=both,
        help="what holds the job's PR from going up, a main fix's claim on the tests main fails, which the bash_guard hook on a Sprite lane asks before a ccx vcs ship opens it",
    )
    verb.add_argument("id", nargs="?", help="the job; a Sprite lane's own job when left out")
    verb.add_argument(
        "--pr",
        action="append",
        default=[],
        help="instead, his open cards holding this PR from landing, a PR url or owner/name#N, repeatable; the landing_guard hook on a Sprite lane asks it before the lane lands a Forge-AI PR, as it holds one here",
    )
    verb.add_argument(
        "--exempt",
        help='the exempt class the PR goes up under, as the drive pr add in the same call registers it: a "main fix" claims for the job each test main fails that no other job holds, refused naming the job owning them all; the bash_guard hook on a Sprite lane passes it',
    )
    verb.add_argument(
        "--release",
        help="instead, why his hold on every release refuses this release, a `ci release start` or `hotfix` as the job runs it, or a release verb naming none; the landing_guard hook on a Sprite lane asks it before the lane starts, resumes, approves, reruns or rolls back a release",
    )
    verb.add_argument("--verb", dest="release_verb", help="with --release, the release verb the lane runs, such as resume, refused as well while he is not present when it starts, resumes or approves a dangerous release")
    verb.add_argument(
        "--cluster",
        help="instead, why a Job may not start on this release env now from its suspended CronJob, on staging while another drive's staging claim stands, on a production cluster while he holds every release and no answer of his since orders it, or while he is not present; the landing_guard hook on a Sprite lane asks it before the lane runs `tools/ci cluster-job`, or creates, unblocks or reruns the build it starts",
    )
    verb.add_argument("--cronjob", default="", help="with --cluster, the suspended CronJob the Job starts from")
    verb.add_argument("--reason", default="", help="with --cluster, the Job's --reason, as its build keeps it, which names the answer of his that orders it")
    verb.set_defaults(run=job_held)
    verb = job.add_parser("wake", parents=both, help="send the job's lane a message while it takes messages, or hold it for a codex job's run that goes, which hears it at its next Bash call, starting no worker: the watchdog's agent's one way to message the job answering for a stall")
    verb.add_argument("id", help="the job")
    verb.add_argument("--text", required=True, help="the message")
    verb.set_defaults(run=job_wake)
    verb = job.add_parser(
        "end",
        parents=both,
        help=(
            "free the Orca lane a job's row records by its dispatch and workspace once it returned its line, whatever its where names: its Sprite, destroyed once what it holds on no remote is saved to GitHub as saved/<its lane>/<branch>-<sha>, or its terminal and clean worktree on this Mac. The job"
            " stays in the state its line set, with its waits, PRs and carried parts. A job not yet done first has what its Sprite holds"
            " on no remote, uncommitted changes included, saved to GitHub as saved/<its lane>/<branch>-<sha>; its output and its next lane's handoff"
            " name each branch GitHub then holds under saved/<its lane>/; refused, naming why, for a row that records no lane, when that Sprite cannot be read or the save fails, and for a running job, whose lane returned no line: drive job resume sends it its next event"
        ),
    )
    verb.add_argument("id")
    verb.set_defaults(run=job_end)
    verb = job.add_parser(
        "reclaim",
        parents=both,
        help=(
            "what the watch runs in a process of its own on each thing an earlier or ended lane of the job left, and a session runs only to ask again for a kept Sprite: the Sprite cc-remote records under the workspace given, destroyed with its Orca environment"
            " once what it holds on no remote is saved to GitHub as saved/<its lane>/<branch>-<sha>, or else marked kept on that lane's death and refused, which the watch says once and never asks for again; or, for a job on this Mac, the dispatch given, whose terminal it closes and whose worktree it removes once clean and pushed,"
            " then marks freed so no watch reclaims it again; the reclaimer runs from the drive's checkout and is killed with every process it started past 300 seconds; refused, naming why, for a lane no death of the job records,"
            " or when the reclaimer fails or overruns"
        ),
    )
    verb.add_argument("id")
    verb.add_argument("lane", help="the cc-remote workspace of the Sprite, or the dispatch of the lane on this Mac, that a lane of the job left")
    verb.set_defaults(run=job_reclaim)
    verb = job.add_parser(
        "prove",
        parents=both,
        help=(
            "what the watch starts once every PR of a builder's taken READY or PUSHED merged, a yasyf/* one among them, and it keeps an"
            " executable proof file: install each plugin a yasyf/* merge changes whose installs, binaries, Homebrew copy or Captain Hook.app lack it, its merges outside yasyf/* left to the builder the watch hands them to, which proves them on their release,"
            " wait until the drive's dashboard runs the newest long-running installed, run the proof file with those plugins' bin folders first on PATH, its bare python3 the drive's own interpreter that drive job set --repro pinned in it, with no wait for CI,"
            " and when it fails while the CI of the push that carried a yasyf/* merge onto its default branch has not passed it,"
            " as drive pr ci judges the merge, wait for it to pass with no deadline and run the file once more, keep its output"
            " beside the job's report, take DONE on exit 0 and BLOCKED quoting its last FAIL line otherwise or when an install fails, no release will carry a merge,"
            " or the dashboard still runs an older install at the timeout, BLOCKED on a run the watch started alone, a session's failing run keeping its output and saying it on its PRs' lines but leaving the job, its line and its Mac box as it found them,"
            " and keep the close report of an order the DONE finishes for the"
            " watch to post, but a nonzero exit ending on a WAIT line with no FAIL line takes no result and runs again at his next word;"
            " refused, naming who proves it instead, for a merge outside yasyf/* or a job keeping no proof file; on a builder that took BLOCKED,"
            " its own proof's among them, it first takes back the newest READY or PUSHED the job's history keeps, so a failed proof runs again on its own job;"
            " on a repair, a builder that opened no PR and keeps a proof file, or a prover, one that opened no PR and waits on nothing but its own after-merge staging waits whose brief's Handoff, else its goal on a builder marked --builds-nothing, names PRs that all merged,"
            " run by its own agent at work or on one stopped or failed, it installs only what a prover's yasyf/* merges change,"
            " takes DONE or BLOCKED on the file alone, and on a WAIT sets the job waiting with its proof marked, which the watch runs again at his next word"
        ),
    )
    verb.add_argument("id")
    verb.add_argument(
        "--timeout",
        type=int,
        default=900,
        help=f"seconds the installs get, the dashboard's handover gets, and each run of the proof file gets from its own start, its end the Unix second {install.PROOF_DEADLINE_ENV} names to the file",
    )
    verb.add_argument(
        "--again",
        action="store_true",
        help="run a proof whose last run failed though neither its long-running install, its merges nor its proof files changed since, for a cause fixed outside them, such as a merged fix in another repository; refused without it",
    )
    verb.set_defaults(run=job_prove)
    verb = job.add_parser("show", parents=both, help="drive status for these ids")
    verb.add_argument("ids", nargs="+", metavar="ID", help=f"what to read: {ID_FORMS}")
    verb.set_defaults(run=status, prs=False)

    pr = verbs.add_parser("pr", help="the drive's pull requests").add_subparsers(dest="action", required=True)
    verb = pr.add_parser("add", parents=both)
    verb.add_argument("url", help=f"the PR, as {PR_FORMS}")
    verb.add_argument("--job", required=True, help="the job that opened or took it over")
    verb.add_argument("--why", help="with --no-tap, why it lands with no tap of his")
    verb.add_argument("--headline", help="the line his card opens with, at most 90 characters: what the change gets him, in plain words")
    verb.add_argument("--because", help="why the change is made, one plain line: the problem it fixes or the words of his it carries out")
    verb.add_argument("--does", help="what it changes for him or its users once it lands, one plain line")
    verb.add_argument(
        "--downstream", help="what else it changes, breaks or holds once it lands, one plain line; a PR waiting on his tap is refused until all four lead lines are kept, naming no function or file"
    )
    verb.add_argument(
        "--part", action="append", default=[], help="a part of its job's orders this PR delivers, as drive pr set --part names it, so its first check judges it on the parts it names; required when its job delivers more than one"
    )
    untapped = verb.add_mutually_exclusive_group()
    untapped.add_argument("--port-of", type=int, metavar="PR", help="a pure port of this PR number in its own repository, work that merged or that he approved: exempt, with no tap card, waiting on and landing after an original the drive tracks until it merges or he approves it; refused for an original the drive does not track that GitHub does not read merged, and for a PR changing a migration, schema or wire format, whose data model is a design part of his order")
    untapped.add_argument("--exempt", choices=state.EXEMPT_CLASSES)
    untapped.add_argument("--no-tap", action="store_true", help="it lands with no tap of his, outside Forge-AI; needs --why. Every other PR waits on his tap")
    verb.add_argument(
        "--waits",
        action="append",
        default=[],
        metavar="review:LOGIN",
        help=(
            "a teammate's GitHub review of the PR's head, review:<their GitHub login>, as his order runs the change by them first: his card draws only once"
            " their newest review, an approval, a request for changes or a comment, is of the PR's head as the watch's GitHub read finds it, and opens saying"
            " they reviewed; repeat for each teammate. drive pr set --waits takes every other wait"
        ),
    )
    verb.set_defaults(run=pr_add)
    verb = pr.add_parser("set", parents=[*both, dry_opt, waits_opt])
    verb.add_argument("url", help=f"the PR, as {PR_FORMS}")
    verb.add_argument("--excepted", metavar="ANSWER", help="his answer giving his exception to land a PR reaching only components staging runs no copy of, once he read its production dry-run plan from drive pr plan")
    verb.add_argument(
        "--tap", action=argparse.BooleanOptionalAction, help="waits on his tap; --no-tap withdraws his open card for it, needs --why"
    )
    verb.add_argument("--why", help="with --no-tap, the reason his withdrawn card shows; with --no-tapped, why the drive's own hold no longer stands")
    verb.add_argument("--serves", metavar="ORDER", help="the order of his whose words ask for this PR; prints them back")
    verb.add_argument(
        "--design",
        type=design_basis,
        metavar="asked:<answer>|none",
        help=f"{served.NAMES} that settled each design decision the PR makes, or none; the watch's check reads each answer whole as his words, and a citation naming none of these, a card he has not answered or a message he did not write is refused",
    )
    verb.add_argument(
        "--part",
        action="append",
        default=[],
        help=(
            "a part of its job's orders this PR delivers, replacing the parts it named, or on a merged PR whose job returned DONE any part of its order, which its job then holds"
            " and its passing proof delivers; the watch judges the PR against the parts it names, every part its job delivers when it names none"
        ),
    )
    exemption = verb.add_mutually_exclusive_group()
    exemption.add_argument("--exempt", choices=state.EXEMPT_CLASSES)
    exemption.add_argument("--no-exempt", action="store_true", help="clear the exemption")
    exemption.add_argument(
        "--port-of", type=int, metavar="PR", help="a pure port of this PR number in its own repository, work that merged or that he approved: exempt, with no tap card, waiting on and landing after an original the drive tracks until it merges or he approves it; refused for an original the drive does not track that GitHub does not read merged, and for a PR changing a migration, schema or wire format, whose data model is a design part of his order"
    )
    verb.add_argument("--fundamental", help="what changed; clears his tap and any exemption")
    verb.add_argument(
        "--staging",
        metavar="CASES",
        help=(
            "the cases a Forge-AI/monorepo PR's staging test runs, as a builder's `STAGING: #N <cases>` line gives them: adds the `staging test of #N before it lands` part"
            " to its order, its wait on that part and, while no unended job carries the part, one tester building nothing on this Mac, whose start line it prints"
        ),
    )
    tapped = verb.add_mutually_exclusive_group()
    tapped.add_argument("--tapped", choices=state.TAP_LABELS, help="record a tap he gave elsewhere, redrawing his open card for it in place to show it")
    tapped.add_argument(
        "--no-tapped",
        action="store_true",
        help="drop a hold the drive recorded itself as his tap, refusing his own; needs --why, kept in the drive's events",
    )
    verb.add_argument("--tap-source", type=his_link, help="the Slack permalink of his message or of the card he tapped")
    verb.add_argument(
        "--flaky",
        nargs=2,
        metavar=("TEST", "FIX"),
        help=(
            "the flaky test outside its diff that failed it at its head, and that test's fix, a PR the drive tracks: it waits on that fix, waking nobody;"
            " red at its own head, while every check failing there is one this kept, then red again once the fix merges or closes, waking its builder"
            " once to restack onto it; ejected by Trunk with its own head green, queued again at the same commit with stack-enqueue --flake once the fix"
            " merges, once; refused unless its checks fail or Trunk ejected it at its head. A FIX that names no PR is the test's cause in words, such as"
            " a date it reads that changes at midnight UTC: Trunk's ejection of a green head is then queued again at the same commit at once, once, and"
            " a head whose own checks fail is refused"
        ),
    )
    verb.set_defaults(run=rehearsed(pr_set))
    verb = pr.add_parser("plan", parents=both, help="print the production dry-run plan of the components a PR reaches that staging runs no copy of, through the release provider")
    verb.add_argument("url", help=f"the PR, as {PR_FORMS}")
    verb.set_defaults(run=pr_plan)
    verb = pr.add_parser("tapped", parents=both)
    verb.add_argument("url", help=f"the PR, as {PR_FORMS}")
    verb.set_defaults(run=pr_tapped)
    verb = pr.add_parser(
        "land",
        parents=both,
        help="what the watch, his Approve and pr add start: queue PRs he approved through stack-enqueue --watch, or merge a yasyf/* PR with --admin and install it",
    )
    verb.add_argument("urls", nargs="+", help=f"one PR, or a stack bottom first, each as {PR_FORMS}")
    verb.add_argument("--dry-run", action="store_true", help="run stack-enqueue --check from a fresh worktree of the default branch, say what it would do, refusing as it would, and queue and record nothing")
    verb.add_argument("--outcome", help="land nothing and record this outcome, as a lander reopening on a newer install records what came of its landing")
    verb.add_argument("--below", action="append", default=[], metavar="PR", help="with --outcome, a PR below it without his Approve that held it; repeat for each")
    verb.add_argument("--asked", action="append", nargs=2, default=[], metavar=("PR", "HEAD"), help="with --outcome, a PR and the head its lander asked forge-pr-reviewer to review; repeat for each")
    verb.add_argument(
        "--dequeue",
        metavar="WHY",
        help="land nothing: take the unit Trunk's merge queue holds out of it with stack-enqueue --dequeue from a fresh worktree of the default branch, and record WHY as its outcome, as the watch and his words under its card start it once a hold of his stops its landing",
    )
    verb.set_defaults(run=pr_land)
    verb = pr.add_parser(
        "check",
        parents=both,
        help="what the watch starts on a builder's READY: judge one PR his card waits on at its head against his words, and say CHECKED, which draws his card once it is green and approved, or NOT READY, which goes once a head to the builders of the PRs it faults while neither his Approve nor an open card of his holds their stack",
    )
    verb.add_argument("url", help=f"the PR, as {PR_FORMS}")
    verb.set_defaults(run=pr_check)
    verb = pr.add_parser(
        "ci",
        parents=both,
        help=(
            "judge a merged PR's CI as drive job prove waits on it, by the CI of the push that carried it onto its default branch: on the merge commit, else the first commit"
            " after it holding it that any CI reported on, which is that push's head when the push carried several merges, each push-event workflow run and each commit status,"
            " such as buildkite/test, passed once it or a later push-event run of its workflow or status of its context on a commit holding the merge passed, whatever failed or"
            " was cancelled before it, never a run of a push merged before it, a reviewer workflow's issue_comment or check_run run or a skipped run; exits 0 once each passed,"
            " 1 while one has not or no CI reported yet, naming each one's first pass, else its newest, and then its verdict, which names each that did not pass,"
            " so a proof file of a fix to red CI reads its verdict here rather than picking a run itself"
        ),
    )
    verb.add_argument("url", help=f"the PR, as {PR_FORMS}")
    verb.set_defaults(run=pr_ci)
    verb = pr.add_parser(
        "published",
        parents=both,
        help=(
            "run a proof's command against the exact files GitHub Pages published for a merged PR, for a private Pages site no lane can sign in to:"
            " read the repository's newest github-pages deployment, following the Actions run deploying it as drive wait does while it still deploys the merge, download the artifact that run deployed,"
            f" check its sha256 against the digest GitHub keeps, serve it on a loopback port named in {published.SERVED_ENV} while the command runs,"
            " print the deployment, run and digest first, delete the files after, and exit with the command's status"
        ),
    )
    verb.add_argument("url", help=f"the merged PR, as {PR_FORMS}")
    verb.add_argument("command", nargs=argparse.REMAINDER, help="after --, the command that clicks the pages and its arguments")
    verb.set_defaults(run=pr_published)
    verb = pr.add_parser(
        "wake",
        parents=both,
        help="what the watch starts on a PR whose stage its builder owes: send the builder answering for it its stage line, to its live lane without touching its job's ask or state, else to a fresh worker on its job",
    )
    verb.add_argument("url", help=f"the PR, as {PR_FORMS}")
    verb.add_argument("--text", help="the line to send, the PR's stage line when left out")
    verb.add_argument("--job", help="the job to wake in place of its builder, the one its waiting or blocked stage names as owing it")
    verb.add_argument("--woke", action="append", default=[], help="a mark the watch writes once the line is delivered: stage:<PR>@<head>, objected:<PR>@<head> or main:<repo>@<test>")
    verb.add_argument("--woke-at", help="the moment of the watch's pass that asked for the wake, which each --woke mark is dated")
    verb.set_defaults(run=pr_wake)

    verbs.add_parser("place", parents=both, help="where the next monorepo builder starts").set_defaults(run=place_job)
    verb = verbs.add_parser("drain-read", parents=both, help="mark read the unread mail of every Run Orca still shows unread, binding a Run the drive's terminal does not hold to its binder terminal, so a Run no new mail reaches retires")
    verb.set_defaults(run=drain_read)
    verb = verbs.add_parser("bound", parents=both, help="run one command on a lane's Run as the drive's own terminal, which coordinates the Run from its run-create, or, when Orca fences it there, as the drive's binder terminal once it binds the Run, under the drive's binding lock, a worker-start only until Orca takes its request")
    verb.add_argument("lane_run", metavar="run")
    verb.add_argument("command", nargs=argparse.REMAINDER, help="after --, the command and its arguments")
    verb.set_defaults(run=bound)
    verb = verbs.add_parser(
        "answer",
        parents=both,
        help="record an answer or ruling of his against the cards still asking him, parts undelivered and PRs not yet live that it could change, each marked standing, settled, dropped, superseded or reopened by it",
        description=(
            "Run for every answer and ruling of his. The first call, with --title, --words and --link and no id, records"
            " it in cc-notes and here, taking the standing answer cc-notes holds under that question in those words when one does,"
            " or with an id and --link records one already in cc-notes, his words read from it above its Notes: line, or, where that text holds"
            " more than his words, as an attribution before his quote, his messages at the links that each sit inside it, his messages in a row"
            " one answer of a --link each; a ruling, labelled"
            " ruling, records only with --rewrite naming the communicator's open \"Drive tooling:\" order that rewrites the"
            " skill to it, so the watch chases that rewrite until it is done. The record tool keeps each ruling an owner or"
            " answer record of his new words names here in the same call, so a later call with its id alone lists it. It"
            " lists the cards of his still open, each undelivered part, as <order>:<part>, of an open order holding more"
            " than one, and the PRs not yet live that it could change: those of the job and order of the card it answers,"
            " those its words name (key, kind, job, order, title, link), and each job waiting on one of their orders, and"
            " exits 2; a card of another order his answer settles is not listed, so mark it by its key. Each later call marks them: --stands for one this answer"
            " leaves as it is, --dropped for a card, part or open PR his words remove, which withdraws a card without stopping"
            " its job, takes a part off its order, every job delivering it and every PR naming it, stopping only a job left"
            " with no part, and closes an open PR on GitHub with its tap ask and card,"
            " --settled for a question card it decides, which shows his answer on the card in place with no buttons, or for"
            " one it decides in part with the ASK its job keeps asking only what his answer leaves open, which takes that"
            " card's place and redraws it; --superseded for one it replaces, which settles its card in place showing his words and a"
            " link to them with no buttons, drops its PR's tap ask, and stops its job, closing an open PR on GitHub, or"
            " folds what the job carries and each open PR it answers for into the live job named, and --reopened for a PR his words change, which drops his"
            " tap on it, withdraws each open card of his carrying it in the same call, and names the live job that reworks"
            " it, no card coming back until that job's READY says how his words are met; and --untouched for a waiting job"
            " they leave as it is, while each other is marked sent by the SendMessage, resume, brief or handoff that"
            " delivers them verbatim to its worker, as its agent's transcript or its worker's launch shows it. --others-stand marks the"
            " cards, parts and PRs left standing only while a few are left. It exits 2, listing the rest, until every one is"
            " marked; the watch says ANSWER every 10 minutes while a card, part or PR is not. --approves keeps his approval on the answer's row once,"
            " for words of his approving work going ahead with FYIs, as his answer 2164dcb does: each open question card on an order or key it"
            " names ends decided on its recommended option and each open PR there takes his Approve from the answer's link, and while it stands,"
            " until those orders close, a newer answer of his supersedes it or he taps Hold on a PR it covers, drive pr add takes that Approve on"
            " a PR it covers and drive job ask keeps a card it covers decided with no card, its order or Opus's read of its words and Notes"
            " covering one; each step posts one progress line in its order's thread, and a card kept unsettled or a PR the check against his words"
            " objects to still asks him."
        ),
    )
    verb.add_argument("id", nargs="?", help="his answer's cc-notes id; leave it out with --title on the first call to record it in cc-notes")
    verb.add_argument("--title", help="the question his answer settles, as its cc-notes title; with no id, the first call records it there, and with an id it rewords that answer's question there")
    verb.add_argument(
        "--label",
        action="append",
        default=[],
        help="a cc-notes label beyond scope:durable and drive:<drive>, such as header:<topic>, from:owner, or area:<project> or area:<project>/<workstream> for a ruling on all work filed there, added to an answer recorded already too",
    )
    verb.add_argument(
        "--about",
        choices=state.ABOUTS,
        help=(
            f"what his words are about, as the record tool's owner record names it: {state.ABOUT_DRIVE} lists only the cards, parts, PRs and jobs"
            f" of \"Drive tooling:\" orders, as an answer labelled {', '.join(sorted(ON_THE_DRIVE))} does, and on an answer recorded already takes each other one it lists unmarked off"
        ),
    )
    verb.add_argument(
        "--rewrite",
        metavar="ORDER",
        help="for a ruling, labelled ruling: the communicator's open \"Drive tooling:\" order that rewrites the skill to it, which the first call needs",
    )
    verb.add_argument(
        "--words",
        help=f"his words, cut with … where words are left out, which his cc-notes answer keeps and a superseded card shows; the first call with --title needs them, and with an id they are its cc-notes words above Notes: unless given, or his messages at --link joined with … where those words hold more than his and each message sits inside them; words his message or card answer at --link does not hold, or given with no --link, are kept as the session's paraphrase, a new answer labelled {settled.PARAPHRASE_LABEL}, with a warning",
    )
    verb.add_argument(
        "--link",
        action="extend",
        nargs="+",
        default=[],
        help=(
            "the permalink of his message or of the card he answered, which a superseded card links, or terminal for words he typed into a drive session's"
            " terminal; several for his messages in a row in one thread, or all at one channel's root, in the order he sent them, which --words joins with …"
            " and the answer keeps at the first; the first call checks --words against those messages joined and a named answer's words"
            " above its Notes: either against them or, where those words hold more than his, as each message sitting inside them, refused on an answer recorded already;"
            f" left out, it is terminal; words that are not his there are recorded as the session's paraphrase, labelled {settled.PARAPHRASE_LABEL}, which settles nothing"
        ),
    )
    verb.add_argument("--stands", action="extend", nargs="+", default=[], metavar="KEY", help="a card, order, part or PR his answer leaves as it is")
    verb.add_argument("--approves", action="extend", nargs="+", default=[], metavar="ORDER_OR_KEY", help="an open order, or a card, part or PR of one, his answer approves going ahead without him, with an FYI line in place of a card")
    verb.add_argument(
        "--dropped",
        action="extend",
        nargs="+",
        default=[],
        metavar="KEY",
        help=(
            "a card, order, part or open PR his answer removes: a card is withdrawn showing his words with no buttons and its job keeps working;"
            " an order, or every part of one, drops the order, stopping each live job whose order it is, and each pick of his binding it is"
            " superseded by his answer in cc-notes; a part leaves its order, every job delivering it and every PR naming it, and a live job"
            " left with no part stops; an open PR closes on GitHub, its tap ask dropped and its card withdrawn"
        ),
    )
    verb.add_argument(
        "--settled",
        action="extend",
        nargs="+",
        default=[],
        type=settled_mark,
        metavar="KEY|KEY=FILE",
        help=(
            "a question card his answer decides: it shows his answer in place with no buttons, its job's question is settled by it, and the job"
            " builds on his words; or, with FILE, one it decides in part: FILE is the ASK its job keeps with drive job ask --redraws KEY asking only"
            " what his answer leaves open, linking his answer in its summary, which redraws that card under its waits id. Either"
            " way the card's job hears his words, marked sent by their delivery or --untouched"
        ),
    )
    verb.add_argument(
        "--superseded",
        action="extend",
        nargs="+",
        default=[],
        type=superseded_mark,
        metavar="KEY=stop|KEY=JOB",
        help="a card his answer replaces: settled in place, its PR's tap ask dropped, and its job stopped, or folded into the live job named, which then answers for each open PR of the job",
    )
    verb.add_argument(
        "--reopened",
        action="extend",
        nargs="+",
        default=[],
        type=reopened_mark,
        metavar="KEY=JOB",
        help="a PR his answer changes: his tap on it dropped, each card of his carrying it withdrawn in the same call, and the live job named reworks it to his words, no card coming back until that job's READY says how they are met",
    )
    verb.add_argument(
        "--others-stand",
        action="store_true",
        help="every card, part and PR still unmarked after this call's marks stands, refused while more than a few are left, so each is read against his words",
    )
    verb.add_argument("--untouched", action="extend", nargs="+", default=[], metavar="JOB", help="a job waiting on an order his answer touches that his words leave as it is")
    verb.set_defaults(run=answer)
    verb = verbs.add_parser(
        "heard",
        parents=both,
        help="with no flag, print when he was last heard and write nothing; --now, --away or --repeats records that he wrote, printing heard or away, and --last unheard",
    )
    verb.add_argument("--now", action="store_true", help="he wrote just now: record him heard at this moment")
    verb.add_argument("--away", action="store_true", help="he said he is going to sleep or away")
    verb.add_argument(
        "--repeats",
        metavar="ORDER",
        help="the open order a part of which his words repeat, such as asking where it is; refused while no running, waiting or built job carries that part and no card of his a done job asked on is still open",
    )
    verb.add_argument("--part", action="append", default=[], help="the part of the --repeats order his words repeat; default: its only part")
    verb.add_argument(
        "--last",
        type=state.parse_time,
        metavar="TIME",
        help="when his last word or tap was, as an ISO time: what the drive heard after it was not his, such as a job's test tap, so he reads heard then and away an hour later; refused when the drive heard nothing after it",
    )
    verb.add_argument("--why", help="with --last, whose the taps or words heard after it were, kept in the drive's events")
    verb.set_defaults(run=heard)
    verb = verbs.add_parser(
        "page-owner",
        parents=both,
        help=(
            f"page his own Datadog On-Call user at high urgency through the repository's page command: {state.PAGE_RULE}; never for news or"
            " a question. The watch pages once on its own for each incident opened while he is away whose order carries that record"
        ),
    )
    verb.add_argument("why", help="what broke, for whom, and its link")
    verb.add_argument("--order", help="the order the page is for, so the watch does not page him for it again")
    verb.add_argument(
        "--users",
        type=int,
        metavar="SEQ",
        help="the cci record whose read shows real users' runs, or their local chats or agents, failing on production; the page names it, and with --order it stays on the order; default: the one --order carries",
    )
    verb.set_defaults(run=page_owner)
    verb = verbs.add_parser(
        "answer-now",
        parents=both,
        help=(
            "answer his message from the drive's rows for its thread in seconds: eyes on it, one tool-less model call over those rows,"
            " and the answer posted in his thread, or ESCALATE and why, for the communicator to take it"
        ),
    )
    verb.add_argument("link", help="his message's permalink")
    verb.add_argument("words", help="his words")
    verb.add_argument("--dry-run", action="store_true", help="print the answer it would post, posting nothing and adding no eyes")
    verb.set_defaults(run=answer_message)
    verb = verbs.add_parser(
        "mac-boxes-down", parents=both, help="take down the Mac box each job holds by the down of the repository's Mac box provider, the box named as the job's id and its holder as <drive>/<job>; a save that moves a job out of running or finishes it runs it detached, and the watch hourly for each job not running that the provider's live boxes name as a holder"
    )
    verb.add_argument("jobs", nargs="+", help="the jobs whose Mac boxes come down")
    verb.set_defaults(run=mac_boxes_down)
    verb = verbs.add_parser("mac-boxes-reap", parents=both, help="terminate each warm Mac box the repository's Mac box provider keeps that no job took for its idle bound; the watch runs it hourly")
    verb.set_defaults(run=mac_boxes_reap)
    verb = verbs.add_parser("mac-boxes-hand", parents=both, help="give each queued Mac job, in the order given, a box from the repository's pool, a priority order's job first; the watch runs it each box sweep")
    verb.add_argument("jobs", nargs="+", help="the queued jobs, in the order they take boxes")
    verb.set_defaults(run=mac_boxes_hand)
    verb = verbs.add_parser(
        "fde-scan", parents=[drive_opt], help="run the repository's FDE scan with the arguments after -- in the drive's checkout at its default branch's head, for a lane on any branch or Sprite"
    )
    verb.add_argument("arguments", nargs="+", help="after --, what the repository's FDE scan takes, such as --symbol raw_sql --pr 42")
    verb.set_defaults(run=fde_scan)
    verb = verbs.add_parser("report-post", parents=both, help="post a POST report to him from the drive's watch, written by one Sonnet call from the report's words, or hand it to the communicator with why not; the watch runs it detached")
    verb.add_argument("seq", help="the report's cci seq")
    verb.set_defaults(run=report_post)
    verb = verbs.add_parser("say-record", parents=both, help="record a post say made on its order from the receipt say kept; say runs it detached")
    verb.add_argument("receipt", help="the receipt file say kept under the drive's posts folder")
    verb.set_defaults(run=say_record)
    verb = verbs.add_parser(
        "term", parents=both, help="he corrected a word: say's hook finds it from now on for Jev to judge, naming the proper term his words or the code use"
    )
    verb.add_argument("stand_in", metavar="stand-in", help="the word he corrected, as a post used it")
    verb.add_argument("term", help="the proper term to use in its place, his word or the code's")
    verb.set_defaults(run=term)
    verb = verbs.add_parser(
        "wait", parents=both, help="block until GitHub's webhook delivers news of one PR or one Actions run to this Mac's receiver, in place of polling gh: exit 0 on the delivery, 1 when none came in time"
    )
    verb.add_argument("ref", help="a PR's link or owner/repo#N, which any delivery naming it ends, or a GitHub Actions run's link, which its completion ends")
    verb.add_argument(
        "--timeout",
        type=int,
        default=receiver.WAIT_SECONDS_DEFAULT,
        help=f"seconds to wait, default {receiver.WAIT_SECONDS_DEFAULT}, inside Bash's default 120-second call; at most {receiver.WAIT_SECONDS_MOST}, with Bash timeout 280000, so the prompt cache outlives the wait",
    )
    verb.add_argument("--after", type=int, help="the deliveries log offset the last drive wait printed, so a delivery that came between two waits ends this one")
    verb.set_defaults(run=wait)

    verb = verbs.add_parser("status", parents=both, help="where each order, job and PR stands")
    verb.add_argument(
        "ids", nargs="*", help=f"what to read, each {ID_FORMS}, or other words, read as each open order whose title, words, part names or PR titles hold every one in any case; default: every open order a line each, a coordinator's only its own"
    )
    verb.add_argument("--prs", action="store_true", help="only a table of each open PR he approved or that waits on him: its stage, what holds it, who owes its next step and since when")
    verb.add_argument("--full", action="store_true", help="for orders, every row of each and of its jobs and PRs, read fresh, in place of the summary read from the drive's saved rows")
    verb.set_defaults(run=status)
    verb = verbs.add_parser("manual", parents=[drive_opt], help="print one page of the long-running skill, then what the repository's providers add to it")
    verb.add_argument("page", nargs="?", choices=manual_pages(), help="default: list the pages")
    verb.add_argument("row", nargs="?", help="print only the table rows whose first cell names this, such as TAP or READY, else each section, list item, row, fenced block and paragraph carrying it")
    verb.set_defaults(run=manual)
    verb = verbs.add_parser("seat-start", parents=both, help="start a priority order's seat, its sub-coordinator, communicator, channel and page, each line posted as a record; drive order set --priority detaches it")
    verb.add_argument("id")
    verb.set_defaults(run=seat_start)
    verb = verbs.add_parser("channels", parents=both, help="list the drive's private channels, each #name, id and seat, and each the drive has not made yet; bare, it changes nothing")
    verb.add_argument("--make", action="store_true", help="make as him the drive's private channel <handle>-drive-<drive> and the private channel <handle>-drive-<drive>-<project> of each sub-coordinator's project and of the drive's tooling, which cc-slack reads as it reads his DM, keeping each id in the drive, one he has kept; say then makes each other project's channel the first time it posts about it")
    verb.add_argument("--hold", action="append", metavar="CHANNEL=SEAT", help="give a watched alert channel to a seat's sub-coordinator, as C0CAC1HT17W=Platform, which subscribes to it by its Hold line across restarts and compaction, in place of the top coordinator; an empty seat hands it back; repeatable")
    verb.add_argument("--env-alerts", action="append", metavar="ENV=CHANNEL", help="name a staging env's alert channel, as plat-usw2-staging=C0CAC1HT17W: the watch hands each new alert there, its link and words, to each job holding that env's slot or its slot's hold, once per alert and job; an empty channel stops it; repeatable")
    verb.add_argument("--kinds", default="alerts", help="with --hold, the kinds of lines the seat subscribes to, of alerts, releases and posts, comma-separated; default alerts")
    verb.add_argument("--move-cards", action="store_true", help="move each open card of his DM whose project has a channel to that channel's root, a card of no project to the drive's tooling channel, its DM card edited to Moved to and a link")
    verb.set_defaults(run=channels)
    verb = verbs.add_parser("context", parents=[drive_opt])
    verb.add_argument("--role", choices=state.ROLES, help="default: the role this session holds")
    verb.set_defaults(run=context)
    verb = verbs.add_parser("view", parents=both, help="what waits on him and where each feature stands")
    verb.add_argument("--project", metavar="NAME", help="only the orders filed under this project and what waits on him for them, each one listed")
    verb.add_argument("--workstream", metavar="NAME", help="with --project, only the orders filed under this workstream of it")
    verb.set_defaults(run=view_command)
    verb = verbs.add_parser("dashboard", parents=[drive_opt], help="start the drive's dashboard and print its link")
    verb.add_argument("--port", type=int, help="serve on this port instead of the drive's own")
    verb.add_argument("--serve", action="store_true", help="serve in the foreground")
    verb.add_argument(
        "--listening", type=int, action="append", default=[], metavar="FD",
        help="with --serve, serve on this inherited listening socket instead of binding the port: an older install's dashboard hands its sockets to the newest this way",
    )
    verb.set_defaults(run=dashboard_command)
    verb = verbs.add_parser("watch", parents=[drive_opt], help="load the launch agent that keeps the drive's watch running unless one is loaded")
    verb.add_argument("--loop", action="store_true", help="run the watch in the foreground: the process its launch agent runs")
    verb.add_argument("--respond", type=int, metavar="FD", help="run the watch's responder in the foreground, writing its lines to the inherited file descriptor FD: the process each watch starts beside its passes and ends, which starts a reader on each lane question the moment it is asked")
    verb.add_argument("--relay", type=int, metavar="FD", help="run the watch's relay in the foreground, writing its lines to the inherited file descriptor FD: the process each watch starts beside its passes and ends")
    verb.add_argument("--land", type=int, metavar="FD", help="run the watch's landing loop in the foreground, writing the PRs the drive does not track it read to the inherited file descriptor FD: the process each watch starts beside its passes and ends, which reads GitHub, stages, wakes and lands every 60 seconds whatever a pass of the watch holds")
    verb.add_argument(
        "--project",
        metavar="NAME",
        help=(
            "a project's own watch, which an older install's launch agent runs: it takes that agent down and ends, since the drive's watch runs each"
            " project's passes beside its own, posting a project's lines to its sub-coordinator"
        ),
    )
    verb.set_defaults(run=watch_events)
    verb = verbs.add_parser(
        "receive", help="take GitHub's signed webhook deliveries through this Mac's Tailscale Funnel path into each drive's events file: the process the receiver's launch agent runs"
    )
    verb.add_argument(
        "--listening", type=int, metavar="FD",
        help="serve on this inherited listening socket, asking GitHub nothing: an older install's receiver hands its socket to the newest this way",
    )
    verb.set_defaults(run=receive)
    cards = verbs.add_parser("cards", help="list, redraw or withdraw his open question cards").add_subparsers(dest="action", required=True)
    verb = cards.add_parser(
        "list",
        parents=both,
        help="list his open question cards, newest first, of an order's feature or a project",
        description=(
            "List each open question of the drive drawn on his card, newest first: its waits id, order, job, when it was asked,"
            " its title and its card's permalink. --order keeps the cards of that order's feature, --project those of orders"
            " filed under that project. A ruling of his that closes a class of questions withdraws each listed card of that class"
            " with drive cards withdraw."
        ),
    )
    verb.add_argument("--order", help="only the cards on this order's feature")
    verb.add_argument("--project", metavar="NAME", help="only the cards on orders filed under this project")
    verb.set_defaults(run=cards_list)
    verb = cards.add_parser(
        "redraw",
        parents=both,
        help="rebuild every open question card of his, writing the drive for each, and print them only after the last; no list",
        description=(
            "Redraw his open question cards in one pass: for each open card, build the JSON `drive job card <its ASK file>"
            " --waits <its waits id>` prints, writing the card's waits id into the drive under its writer lock, and once the"
            " last is built print each, with `replaces` the card's permalink, which cc-slack draws over the card where it"
            " sits, posting no new version, whether or not its store still keeps the card, one line each, for say(card=…);"
            " with --post it draws each over his card itself, as drive job ask posts one, and prints its waits id and permalink."
            " His open cards are each open question of the drive drawn on a card, as drive status reads it open, and each card"
            " cc-slack lists open. A question's ASK file is the one it keeps; a listed card's is the one a job's question keeps"
            " under its waits id, else the one a question linked to the card keeps when it asks the card's question, else"
            " asks/<waits id>.md rebuilt from the card as posted, which a question of its job then keeps. Each card that does"
            " not redraw, one whose redraw is refused, is named once the rest are printed, and the verb exits 2. It is no list"
            " of his open cards: drive cards list is."
            " A --waits naming a PR card's merge: waits id, open or answered and neither replaced nor withdrawn, has"
            " cc-slack redraw that card in place as drive card draws it now, said on stderr."
        ),
    )
    verb.add_argument("--waits", action="append", default=[], help="redraw only this open card, by its waits id, or the PR card of his under this merge: waits id; repeatable")
    verb.add_argument("--post", action="store_true", help="draw each card over his where it sits, as drive job ask posts one, and print its waits id and permalink, so a brief's After install: line can redraw a card")
    verb.set_defaults(run=cards_redraw)
    verb = cards.add_parser(
        "withdraw",
        parents=both,
        help="withdraw his open question card that his words withdraw or change, that an event overtook, or that another open card of the drive asks again",
        description=(
            "Withdraw his open question card that his words withdraw, wherever he wrote them, or whose question or options they"
            " change, such as more options, a new constraint or a rework of its work, or that an event overtook so a tap has"
            f" nothing left to pick, such as a hold on a deploy that applied: {settled.EVENTS}. It settles in place showing his"
            " words, or what happened, with no buttons, so a late tap records nothing, and what happened is never recorded as"
            " his; once the work is back, its job, or any job of its order carrying on that work, asks again with drive job ask"
            " <job> <file> --redraws <this card's waits id>, which redraws it under its waits id. With --via another open"
            f' question card of the drive and no --words, it folds into the question that card asks, showing "{ASKED_AGAIN}"'
            " and a link to it with no buttons, as drive job ask folds a question it reads as asked again into a newer card:"
            " each job's or PR's wait on it moves to that card's waits id, and its question stays open on that card, so his"
            " one answer there ends it too and wakes them."
        ),
    )
    verb.add_argument("card", help="the card, by its waits id or its permalink")
    verb.add_argument(
        "--words",
        help="his words that withdraw or change it, verbatim, refused unless his message at --via holds them; or, for an event, what happened, in plain words; the card shows them; refused with --via another open card of the drive",
    )
    verb.add_argument(
        "--via",
        required=True,
        help=(
            "the permalink of his message, which the card links, terminal for words he typed into a drive session's terminal,"
            " the link of the event that overtook it, the id of a done drive job whose report names the card and the fact or value it asks him for,"
            " or another open question card of the drive, by its waits id or permalink,"
            " that asks it again"
        ),
    )
    verb.set_defaults(run=cards_withdraw)
    card_help = "cc-slack's card_command: reads one request JSON on stdin and prints one JSON reply"
    verbs.add_parser("card", help=card_help, description=card_help).set_defaults(run=card_command)
    verb = verbs.add_parser("card-record", parents=both, help="apply what a drive card request changed on its PRs from the receipt it kept; drive card runs it detached")
    verb.add_argument("receipt", help="the receipt file drive card kept under the drive's card-records folder")
    verb.set_defaults(run=card_record)
    verb = verbs.add_parser("transcripts-keep", parents=both, help="copy the Claude Code transcripts the Sprite of a job's lane holds now to the drive's folder; the watch runs it detached at each turn end of a Sprite lane")
    verb.add_argument("id", help="the job")
    verb.set_defaults(run=transcripts_keep)
    verb = verbs.add_parser("disks-check", parents=both, help="read the nbd0 disk of each Sprite lane through the Sprites file API and checkpoint-restore a wedged one at once; the watch runs it detached every two minutes")
    verb.set_defaults(run=disks_check)
    verb = verbs.add_parser("mcp", help="serve say and the drive's verbs as MCP tools to a Claude Code session over stdio")
    verb.add_argument("--list", action="store_true", help="print the tools as JSON and serve nothing")
    verb.add_argument("--warm", action="store_true", help="fork one process per call the MCP server sends on stdin, from this one with the drive already imported")
    verb.set_defaults(run=mcp_command)
    plan = verbs.add_parser("plan", help="the plans the coordinator's checkout keeps in cc-notes").add_subparsers(dest="action", required=True)
    verb = plan.add_parser(
        "search",
        parents=both,
        help="print each plan line carrying one of the phrases whole, in any case, under its plan's title and the headings above it, plans best first, cut at 12,000 characters",
    )
    verb.add_argument("words", nargs="+", help="each phrase to look for in the plans, matched whole in any case, such as a part, a step or a file")
    verb.set_defaults(run=plan_search)
    verb = verbs.add_parser("datadog", help="run one read-only Datadog MCP tool on this Mac with ai-oncall's Datadog keys: a Sprite lane's Datadog read and any lane's Database Monitoring read")
    verb.add_argument("tool", help="the Datadog MCP tool, such as execute_code or search_datadog_logs; tools lists the tools it runs")
    verb.add_argument("stdin", nargs="?", choices=["-"], help="read the tool's JSON arguments from stdin and call it; without it, print the tool's description and input schema")
    verb.set_defaults(run=datadog_command)
    page = verbs.add_parser("page", help="the one-off live dashboards page jobs build, listed under Pages on the drive's dashboard").add_subparsers(dest="action", required=True)
    verb = page.add_parser(
        "add",
        parents=both,
        help="check a page job's folder and put it under Pages, replacing the page of that slug whole",
        description=(
            "The folder holds index.html, the page the dashboard opens in a frame sandboxed to allow-scripts, a page.json naming"
            " its title and goal, and optionally the orders and PRs it shows besides the order's, its source link and the job that"
            " built it, and only .html, .js, .css, .json, .svg and .png files. The dashboard sends the page its slice of the"
            " board on each change and turns its actions into his answers, taps and asks, each with Undo; see reference/pages.md."
        ),
    )
    verb.add_argument("slug", help="its name in /pages/<slug>/: lowercase letters, digits and dashes")
    verb.add_argument("--order", required=True, help="the order it serves, whose project and workstream file it")
    verb.add_argument("--dir", required=True, metavar="FOLDER", help="the folder the page job built it in")
    verb.set_defaults(run=page_add)
    verb = page.add_parser(
        "make",
        parents=both,
        help="make the page for one goal or decision of an order, which the dashboard composes from its typed blocks and keeps live, and print its link",
        description=(
            "Keep pages/<slug>/page.json for the order, titled by it, with the goal or decision the page serves; the dashboard"
            " composes the page itself from its component library (story, decide, approve, stack, staging, path, timeline,"
            " compare, before and after, diagram, metrics, callout), each PR, decision and order drawn live, and composes it"
            " again whenever the PRs, decisions or orders in its scope change. Making it again keeps its orders and adds this one."
            " drive order set --priority makes one for its order; the communicator makes one whenever a page helps him decide or"
            " see where a goal stands. See reference/pages.md."
        ),
    )
    verb.add_argument("slug", nargs="?", help="its name in #/pages/<slug>: lowercase letters, digits and dashes; default its order's title")
    verb.add_argument("--order", required=True, help="the order it serves, whose project and workstream file it")
    verb.add_argument("--goal", required=True, help="the one goal or decision the page helps him with, in plain words")
    verb.add_argument("--project", action="append", metavar="NAME", help="also show every order filed under this project; repeatable")
    verb.add_argument("--workstream", action="append", metavar="NAME", help="also show every order filed under this workstream; repeatable")
    verb.add_argument("--word", action="append", metavar="WORD", help="also show every order whose title holds this word; repeatable")
    verb.add_argument("--whole-drive", action="store_true", help="show every open order, PR and decision of the drive")
    verb.add_argument("--made-by", default="communicator", help="who asked for it: communicator, priority or a session's role")
    verb.set_defaults(run=page_make)
    verb = page.add_parser(
        "frame",
        parents=[json_opt],
        help="open <session>-frame, an agent-browser session inside the page frame the dashboard in <session> shows, and print its name",
        description=(
            "Read <session>'s DevTools address with agent-browser get cdp-url, find in Chrome's /json/list the one iframe target"
            " at /pages/<slug>/, close any <session>-frame, and open <session>-frame through a browser.provider plugin that"
            " hands agent-browser that target's socket with directPage: true. There every command but screenshot acts inside"
            " the page, eval, get count, wait --fn and set media included, which agent-browser's own frame command leaves on"
            " the dashboard; screenshots stay on <session>. A reload of the dashboard replaces the frame, so run it again after one."
        ),
    )
    verb.add_argument("session", help="the agent-browser session showing the dashboard at #/pages/<slug>")
    verb.add_argument("slug", help="the page's name in /pages/<slug>/")
    verb.set_defaults(run=page_frame)
    verb = verbs.add_parser(
        "break-glass",
        parents=[drive_opt],
        help="run one command holding only a stack's admin credentials, minted on this Mac's machine profile to end with its break-glass grant",
        description=(
            "Start the forge/break-glass grant with bk build create at dev's newest commit, reading nothing first,"
            " its reason the job or order and --reason, which may not be"
            " blank, once aws sts assume-role mints the env's account admin role, <namespace>-<account>-admin in the account and region"
            " the env's infra/envs files name, from the profile in ~/.aws/config that reaches frg-core-gbl-identity-devops from a"
            " credential_process, never an SSO sign-in, so an env whose admin role STS refuses this Mac's machine posts no grant,"
            " and wait for it to pass. Then mint that admin role again the same way to end a minute"
            " before the grant's recorded end, and the machine-break-glass role the grant opens the same way once STS reads the trust"
            " the grant wrote, which IAM shows every endpoint only seconds after writing it: an assume STS refuses is tried again every 5"
            " seconds, and one still refused 120 seconds after the first try runs nothing and names the grant. Run the command after -- with an AWS config naming those profiles alone, so"
            " a command naming another stack's profile finds none; the credentials are deleted when it exits. The command runs as"
            " a process group of its own, ended with every process it started when it exits, when the grant ends, which exits"
            " 124, and when drive gets SIGTERM, SIGHUP or SIGINT. Once bk build create prints the grant's link, before the grant"
            " passes, it posts root@<drive> one cci evidence record from lane break-glass naming the job and its order, the link,"
            " the stack, the command and the reason, so the top coordinator reads whose a grant in #releases is; a post cci refuses"
            " runs nothing. A grant ending"
            " within 15 minutes, the shortest credentials STS mints, runs nothing. A team's data is neither read nor written under"
            " it, since only a service account on a pass box or in the team's cluster reads or writes it; any other production write"
            " runs only on his word or an incident order at 0 deletes and 0 replaces. A Sprite lane's run is refused."
        ),
    )
    verb.add_argument("id", metavar="JOB|ORDER", help="the job or order the grant is for, named in its reason")
    reads.break_glass_options(verb).set_defaults(run=break_glass)
    verb = verbs.add_parser("tool", add_help=False, help="run one MCP tool on the JSON arguments read from stdin and print its lines")
    verb.add_argument("tool", choices=MCP_TOOLS)
    verb.add_argument(
        "-h", "--help", action="store_true", help="print the tool's JSON fields, each with its type, whether it is required and its description, and run nothing"
    )
    verb.set_defaults(run=tool_command)
    verb = verbs.add_parser(
        "move",
        parents=both,
        help="step 1 of moving a role's session into a fresh Orca terminal: record his word naming it and print steps 2 to 4, stopping and starting nothing",
    )
    verb.add_argument("role", choices=state.ROLES)
    verb.add_argument(
        "--his-word", metavar="PERMALINK", help="the Slack permalink of his message naming the move; refused without it, for a message not his, and while an incident order is open"
    )
    verb.set_defaults(run=move)

    verb = verbs.add_parser("install", parents=[drive_opt])
    verb.add_argument("plugin", metavar="plugin@marketplace")
    verb.add_argument("--sha")
    verb.add_argument("--repo")
    verb.add_argument("--timeout", type=int, default=900)
    verb.set_defaults(run=install_plugin)
    verb = verbs.add_parser(
        "reload",
        parents=[drive_opt],
        help="hand each session of the drive, or of every drive on this Mac, to the one process typing into its Orca terminal, which types /reload-plugins once when Claude Code reads its turn over",
    )
    verb.add_argument("--seat", help="the one seat of --drive whose reload this process types, as the process an install starts does")
    verb.add_argument("--force", action="store_true", help="with --seat, type it even when the seat already reloaded onto the newest plugin install, as a bare drive reload does")
    verb.add_argument("--terminal", help="with --seat, the Orca terminal the seat's session runs in, as the install's one read of Orca found it, so this process reads Orca for it no second time")
    verb.set_defaults(run=reload_plugins)
    verb = verbs.add_parser(
        "advised",
        help="start a root session's claude command, as its Start or resume line runs it, with its advisor once Claude Code takes it, else without it, saying so in its terminal and to the meta session",
    )
    verb.add_argument("--drive", required=True, help="the drive the session holds a seat of")
    verb.add_argument("--lane", required=True, help="the seat's cci lane, which a refusal's defect record is sent from")
    verb.add_argument("command", nargs=argparse.REMAINDER, help="the claude command, its --model and --advisor included, with what cci appends to it")
    verb.set_defaults(run=advised)
    verb = verbs.add_parser("migrate", help="make each one-time change an older install still owes to every drive on this Mac, as `drive install` does before it reloads sessions")
    verb.set_defaults(run=migrate_drives)

    verb = verbs.add_parser("setup")
    verb.add_argument("--bin-dir")
    verb.set_defaults(run=setup)
    return root


def parsed(argv: list[str] | None) -> argparse.Namespace:
    args = parser().parse_args(argv)
    for key, value in vars(args).items():
        if isinstance(value, list):
            setattr(args, key, list(value))
    return args


def main(argv: list[str] | None = None) -> int:
    """Run one verb in the newest installed drive when this process started from an older install, such as a launch's bind prefix. A Sprite lane's verb, which reads.lane_drive marks as a SPRITE_LANE run as its job's own lane, runs only when reads.lane_refusal finds nothing in what it parsed writing to another job or acting on another drive, a bare `status` reading the lane's own job, and nothing it starts carries that mark. A refusal exits 2 by refused, which a launch or resume state.detach started also writes on the drive's events for the watch, but for a stand-down."""
    if argv is None:
        install.reopen(sys.argv[1:])
    args = parsed(argv)
    detached = os.environ.pop(state.DETACHED_ENV, None)
    lane = os.environ.pop(reads.SPRITE_LANE, None)
    own = lane and os.environ[state.OWN_JOB_ENV]
    try:
        with children.held(args.run):
            keyed_prs(args)
            for key in WAITS_ARGS:
                if value := getattr(args, key, None):
                    setattr(args, key, folded_waits(getattr(args, "drive", None), value))
            if lane:
                if refusal := reads.lane_refusal(args, parsed, os.environ[state.DRIVE_ENV], own):
                    print(refusal, file=sys.stderr)
                    return 2
                if args.verb == "status" and not args.ids:
                    args.ids = [own]
            args.sprite_lane = bool(lane)
            if "TMUX" in os.environ and (attached := orca.own_terminal()):
                os.environ["ORCA_TERMINAL_HANDLE"] = attached
            with state.naming(vars(args)):
                return args.run(args)
    except VERB_ERRORS as error:
        return refused(args, error, detached, own)


def rehearsed(run: Callable[[argparse.Namespace], int]) -> Callable[[argparse.Namespace], int]:
    """The verb, which with --dry-run runs in a dry state.batch as tools.ran runs an MCP op's dry run: refused as it would be, keeping nothing, and stopped at the first command it starts that does more than read, which every check before it passed."""

    def rehearse(args: argparse.Namespace) -> int:
        if not args.dry_run:
            return run(args)
        name = state.resolve(args.drive)
        before = after = state.load(name)
        try:
            with state.batch(name, dry=True):
                try:
                    code = run(args)
                finally:
                    after = state.load(name)
        except state.Rehearsed as reached:
            print(f"checked up to `{reached}`, which a dry run never starts")
            code = 0
        for line in changed_fields(before, after):
            print(line)
        print("a dry run: nothing kept")
        return code

    return rehearse


def changed_fields(before: dict, after: dict) -> list[str]:
    """Each change a dry run's writes made to the drive's rows, a line each: a record of a collection added or dropped, else each field of a record or top-level field whose value changed, as `<collection> <key> <field>: <before> -> <after>`, each value its JSON cut to 160 characters."""

    def shown(value: object) -> str:
        return render.cut(json.dumps(value, ensure_ascii=False, sort_keys=True), 160)

    lines = []
    for field in dict.fromkeys([*before, *after]):
        was, now = before.get(field), after.get(field)
        if was == now:
            continue
        if field not in state.COLLECTIONS:
            lines.append(f"{field}: {shown(was)} -> {shown(now)}")
            continue
        for key in dict.fromkeys([*was, *now]):
            if key not in now:
                lines.append(f"{field} {key}: dropped")
            elif key not in was:
                lines.append(f"{field} {key}: added")
            else:
                lines += [f"{field} {key} {column}: {shown(was[key].get(column))} -> {shown(now[key].get(column))}" for column in dict.fromkeys([*was[key], *now[key]]) if was[key].get(column) != now[key].get(column)]
    return lines


def keyed_prs(args: argparse.Namespace) -> None:
    """Name each PR the verb's arguments name by the drive's key for it, state.tracked_pr's: url, urls, --lands, --pr, --below, --rework's PR, --flaky's FIX when state.PR_WORD reads it as a PR rather than the test's cause, --asked's PR, and each id and --waits value state.PR_WORD reads as a PR. The argument UNTRACKED_PRS names for its verb takes any PR as owner/repo#N or its URL, read without the drive, which pr ci never loads; an id takes an untracked one too, which only refuses naming every form an id takes."""
    verb, given = (args.verb, getattr(args, "action", None)), vars(args)
    prs = cache(lambda: state.load(state.resolve(given.get("drive")))["prs"])

    def keyed(name: str, ref: str) -> str:
        if UNTRACKED_PRS.get(verb) == name and not state.PR_NUMBERED.fullmatch(ref.strip()):
            return state.pr_key(ref)
        try:
            return state.tracked_pr(prs(), ref, untracked=name == "ids")
        except DriveError as error:
            raise DriveError(str(error) if name in ("url", "urls", "ids") else f"--{name} {ref}: {error}") from None

    if given.get("url"):
        args.url = keyed("url", args.url)
    for name in ("urls", "lands", "pr", "below"):
        if given.get(name):
            setattr(args, name, [keyed(name, ref) for ref in given[name]])
    if given.get("rework"):
        args.rework = [(keyed("rework", pr), link) for pr, link in args.rework]
    for name in ("ids", "waits"):
        if isinstance(given.get(name), list):
            setattr(args, name, [keyed(name, ref) if state.PR_WORD.fullmatch(ref) else ref for ref in given[name]])
    if given.get("flaky") and state.PR_WORD.fullmatch(args.flaky[1]):
        args.flaky = [args.flaky[0], keyed("flaky", args.flaky[1])]
    if given.get("asked"):
        args.asked = [[keyed("asked", pr), head] for pr, head in args.asked]


def folded_waits(name: str | None, value: str | tuple | list) -> str | tuple | list:
    """A verb's card argument with each waits id in it as the drive stores it, by state.waits_of, so a card named `<job>-<n>` is the card `<drive>:<job>-<n>` names at every verb: a mark's KEY=FILE, KEY=stop or KEY=JOB folds its KEY alone."""
    if isinstance(value, str):
        return state.waits_of(name, value)
    if isinstance(value, tuple):
        return (state.waits_of(name, value[0]), *value[1:])
    return [folded_waits(name, each) for each in value]


def refused(args: argparse.Namespace, error: Exception, detached: str | None, lane_job: str | None) -> int:
    """Print the verb's refusal and exit 2. A launch, resume or end state.detach started, which only the watch runs, also writes the refusal as an event of the watch hearing its job's order, naming the log its output went to, so it reaches the watch's lines, unless it stood down for another verb acting on its job, and so does a reclaim that kept a Sprite, which no watch asks for again, so it is said once; the job and its order are read from the drive as saved, which a newer install's save never refuses."""
    print(f"drive: {error}", file=sys.stderr)
    if lane_job and (steps := reads.mac_steps(str(error), parser(), lane_job)):
        print(steps, file=sys.stderr)
    if detached and (args.run in (job_launch, job_start, job_resume, job_end) or isinstance(error, Kept)) and not isinstance(error, StoodDown):
        name = state.resolve(args.drive)
        order = state.saved_row(name, "jobs", args.id)["order"]
        heard = state.heard_lane(state.saved(name) | state.stored(name, ("orders",)), order)
        state.append_event(name, watch.RELAUNCH_REFUSED, job=args.id, order=order, verb=args.action, refusal=str(error), log=detached, lane=heard)
    return 2
