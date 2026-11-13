import argparse
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import textwrap
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from drive import agents, artifacts, brief, github, install, land, limits, orca, picture, place, reads, refs, render, served, settled, slack, state, watch
from drive.state import DriveError

DRIVE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
ROLE_NAMES = {"coordinator": "top coordinator", "communicator": "communicator", "meta": "meta session"}
FREEZE_FOR = timedelta(hours=12)
RELEASE_NAME = re.compile(r"[a-z]+-[a-z]+")
INCIDENT_EXEMPTION = "incident fix"
UNLANDABLE = (
    "{key}: refused, nothing {done}: only his tap or an exempt class lands a Forge-AI PR, and this leaves it with neither, so no card"
    " of his would ask his Approve and the lander would never queue it. Keep it waiting on his tap, or record its class with"
    " --exempt <class> or --port-of <N>; his order reworking it takes its card down with drive order set <o> --rework <url>"
)
PORT_DATA_RULING = 'his Nov 12 words "port his visuals but always do it the fundamentally right way when it comes to modelng the data"'
FILING = ("project", "workstream", "title")
TITLE_CHARS = 60
TOOLING_FILING = ("The drive", "Drive tooling")
ANSWER_KIND = "Every part of an answer order is words he reads"
UNSAID = {"answer": ("not giving it", "not giving them"), "build": ("not saying it is done", "not saying they are done")}
JOB_ID = re.compile(r"j\d+")
SLACK_USER = re.compile(r"[UW][A-Z0-9]{2,}")
SLACK_TS = re.compile(r"\d+\.\d+")
FROM_HELP = (
    "where he wrote it: his Slack message's permalink, a reply's carrying thread_ts, slack:<channel>/<thread ts>/<his reply ts>"
    " or slack:<channel>/<ts>, the one form Slack is asked which thread it is in; or terminal"
)
THREAD_HELP = (
    "the thread the order's lines go in, in his DM or a channel such as an alert's: its permalink, slack:<channel>/<ts>, or its ts"
    " in the channel of the --from message, his DM for an order from the terminal"
)
RULING = "ruling"
CODE_LABEL = "code:"
ID_FORMS = "an order o<n>, a job j<n>, or a PR as owner/repo#N, its URL or its bare number"
WAITS_CARD = re.compile(r"https://\S+/archives/\S+/p\d+\S*|[\w.-]+:[\w.-]+|merge:[\w.-]+:[\w.-]+:\d+(?:-\d+)?")
CLAUDE_BUILDS = Path.home() / ".local" / "share" / "claude" / "versions"
BRIEF_FLAGS = ("serves", "done_when", "where", "runs_as", "ruling", "native", "interactions", "would_he_ask", "plan", "incident", "scratch")
BRIEF_NEEDS = ("serves", "done_when", "where")
READER_RESEARCH = "the Researched: block this build needs"
ANSWER_FIRST = (
    "his answer goes to his thread the moment you have it proven, before any reproduction file or full report: one `report`"
    " record on the cci `post` tool, `to: [communicator@{drive}]`, `text: POST <the answer>`, `attrs: {attrs}`; then keep your"
    " full report and return as usual"
)
NO_SURFACE = re.compile(r"\s*none\b", re.IGNORECASE)
PROMISED_TIME = re.compile(
    r"\bnext (?:line|update|post|summary)(?:\s+(?:by|before|at))?\s+(?:about\s+|around\s+)?([01]?\d|2[0-3]):([0-5]\d)(?![:\d])(?:\s*([AP])\.?M\b)?", re.IGNORECASE
)
REPORTER_HELP = (
    "the bug report the order fixes, by its Slack permalink or slack:<channel>/<its thread's ts>, from the share or link in his"
    " words: its thread gets one fix-live note once the fix is proven live, and done refuses until drive order told records it"
)
ALERT_HELP = (
    "the Datadog monitor or Sentry issue link of the alert it is, again for each: once its fix is live, the watch reads each"
    " monitor every minute and closes the incident once all read OK"
)
REWORK_HELP = (
    "a PR his words rework, by its url or key, named only once its stack's His words, step by step read against them leave"
    " them unmet: it withdraws in the same call each open card of his carrying it, his words its reason, and no card, ready"
    " or tap of his stands on it until the builder's READY says how his words are met; with none, every card stays up"
)
TOLD_LINK = (
    "Link each card where the line names it, as <its permalink|your card on what it decides>, never as its bare permalink,"
    " which `cc-slack check-post` refuses."
)
HIDDEN = "not visible in Orca, which hides the worktrees it did not make for this repository"
PAGES = Path(__file__).resolve().parents[2] / "skills" / "long-running" / "reference"
RELEASE_RULE = re.compile(r"^- (Every release a drive job starts.*?)\n(?!  )", re.M | re.S)
MANIFEST = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
LAUNCHED = ("run", "dispatch", "task", "terminal", "workspace", "environment", "lane")
MCP_TOOLS = ("say", "heard", "handler", "record", "order", "job", "pr", "incident", "status", "search_plan")


def emit(args: argparse.Namespace, line: str, payload: object) -> int:
    print(json.dumps(payload, indent=2) if args.json else line)
    return 0


def due_stamp(text: str) -> str:
    return state.stamp(state.parse_due(text))


def start(args: argparse.Namespace) -> int:
    if not DRIVE_NAME.fullmatch(args.name):
        raise DriveError(f"drive names are letters, digits, dot, dash and underscore: {args.name}")
    if not orca.in_terminal():
        raise DriveError(
            f"drive start {args.name} --role {args.role}: refused, this session runs in no Orca terminal, so he cannot open it from Orca;"
            f" start the session in one with orca terminal create --worktree path:{os.getcwd()} --title {args.name}-{args.role}"
            f" --command \"claude … -- '/long-running {args.name} as the {args.role}: run Start or resume for that role'\" --json,"
            " whose whole command drive manual start prints"
        )
    if args.project and args.role != "coordinator":
        raise DriveError(f"--project binds a sub-coordinator, which holds the coordinator role; the {state.title(args.role)} holds no project")
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    fresh = state.new_drive(args.name, args.role, session, os.getcwd(), args.channel, orca.own_terminal())
    with state.edit_or_create(args.name, fresh) as data:
        if args.project:
            bind_subcoordinator(data, args.project, fresh["roles"][args.role])
        elif project := state.session_project(data, session):
            raise DriveError(
                f"--role {args.role}: session {session} is the sub-coordinator for {project} in drive {args.name}; the top coordinator,"
                " the communicator and the meta session are sessions of their own, so start this role from another session"
            )
        else:
            data["roles"][args.role] = fresh["roles"][args.role]
        if args.channel:
            data["slack"]["channel"] = args.channel
        watched_channels = data["slack"].setdefault("watch", [])
        watched_channels.extend(channel for channel in dict.fromkeys(args.watch) if channel not in watched_channels)
    held = fresh["roles"][args.role]
    seen = orca_place(held, run_show)
    meta = "" if "meta" in data["roles"] else meta_started(args.name, held["cwd"], data["launched"].get("meta"))
    watched = f"{watch.ensure(args.name, args.project)}\n" if args.role == "coordinator" else ""
    seat = f"sub-coordinator for {args.project}" if args.project else args.role
    text = f"{render.context(data, args.role, args.project)}\n\n{meta}{watched}{seat}: session {held['session']}, {seen}"
    return emit(args, text, {**data, "orca": {seat: seen}})


def meta_started(name: str, worktree: str, earlier: str | None) -> str:
    """Start the meta session in an Orca terminal of its own while none is bound, by his words "have the skill always start a meta root as a sibling to coordinator and communicator"; the line saying so, or nothing for a drive under LONG_RUNNING_STATE."""
    if state.scratch():
        return ""
    try:
        handle = orca.start_meta(name, worktree, earlier)
    except orca.OrcaError as error:
        return f"the meta session, which holds drive tooling, did not start: {error}; drive manual start prints its command\n"
    if handle is None:
        return f"the meta session starts in Orca terminal {earlier}, which an earlier drive start opened\n"
    with state.edit(name) as data:
        data["launched"]["meta"] = handle
    return f"started the meta session, which holds drive tooling, in Orca terminal {handle}\n"


def bind_subcoordinator(data: dict, project: str, held: dict) -> None:
    """Bind this session as the project's sub-coordinator, replacing any session bound for it before, and keep where the event log ended at its first binding, which its watch reads from; refused for a project no order is filed under, a session holding another seat, and a lane another seat, record or numbered name of the drive takes."""
    if project not in (filed := state.projects(data)):
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
    """Record his order; an incident order records a job on each of its parts, its fix among them, unless it joins the open incident on the same failing service; it reworks only the PRs --rework names and names each other open PR card of his in its thread, which it leaves up."""
    name = state.resolve(args.drive)
    refuse_unreworkable(state.load(name), args.rework)
    order_id, order, started, incidents = added_order(args)
    reworked = rework_named_prs(name, order_id, args.rework, thread_cards(name, order), f"drive order set {order_id} --rework <PR>")
    return emit(args, "\n".join([order_id, *started, *incidents, *reworked]), {"id": order_id, **order, "incidents": incidents})


def refuse_unreworkable(data: dict, urls: list[str], job_id: str | None = None) -> None:
    for key in map(state.pr_key, urls):
        if not (pr := data["prs"].get(key)) or pr["state"] != "open" or job_id not in (None, pr["job"]):
            raise DriveError(f"--rework {key}: refused, it names an open PR the drive tracks{f' of {job_id}' if job_id else ''}")


def thread_cards(name: str, order: dict) -> list[dict]:
    return [card for card in fresh_pr_cards(name) if state.link_thread(card["url"]) == state.promise_thread(order)] if order["thread"] else []


def rework_named_prs(name: str, order_id: str, urls: list[str], cards: list[dict], how: str) -> list[str]:
    """Rework to his order only the PRs --rework names, and name each other card of his it leaves up, since a stack can meet his words under its card already."""
    keys = set(map(state.pr_key, urls))
    left = [card for card in cards if not keys & set(map(state.pr_key, card["prs"]))]
    return [
        *(rework_prs(name, urls, order_id, state.load(name)["orders"][order_id]["text"]) if urls else []),
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


def rework_prs(name: str, urls: list[str], by: str, words: str) -> list[str]:
    """Mark the open drive PRs reworked to his words and withdraw in the same call, once the drive is saved, each open card of his carrying one, his words its reason: no card, ready or tap of his stands on one until its builder's READY says how his words are met; a withdrawal that fails goes again on the watch's next read."""
    with state.edit(name) as data:
        keys = [key for key in dict.fromkeys(map(state.pr_key, urls)) if key in data["prs"] and data["prs"][key]["state"] == "open"]
        for key in keys:
            state.rework(data["prs"][key], by, words)
    lines = [f"{key} reworked to {by}: its card stays down until its builder's READY says how his words are met" for key in keys]
    return lines + state.after_save(name, lambda: withdrawn_cards(name, {key: data["prs"][key] for key in keys}))


def withdrawn_cards(name: str, prs: dict[str, dict]) -> list[str]:
    from drive import view

    lines = []
    for key, pr in prs.items():
        reason = state.rework_reason(pr)
        try:
            lines += [f"withdrew his card {card}: {reason}" for card in slack.withdraw_pr_card(pr["url"], reason)]
        except slack.SlackError as error:
            lines.append(f"{key}: withdrawing its card failed, so the watch withdraws it on its next read: {error}")
    if prs:
        view.refresh_cards(name)
    return lines


def added_order(args: argparse.Namespace) -> tuple[str, dict, list[str], list[str]]:
    name = state.resolve(args.drive)
    created = state.stamp()
    if args.incident and not args.joins and state.INCIDENT_FIX not in (args.parts or []):
        raise DriveError(
            f'an incident order starts its fix job at the alert, beside diagnosis: name "{state.INCIDENT_FIX}" among --parts, such as'
            f' --parts {state.INCIDENT_FIX} "first check", or pass --joins <o> when an open incident order covers the same failing service'
        )
    message = his_message(args.source)
    thread = order_thread(state.load(name), args.source, message, args.thread)
    reported = state.reporter(args.reporter) if args.reporter else None
    if (args.incident or args.joins) and args.options_only:
        raise DriveError(
            "--options-only: refused, an incident order is never limited to options, since its codex jobs start at the alert, before he picks;"
            " add it without --incident and --joins when he asked for options only, or without --options-only when it is an incident"
        )
    alerts = alert_links(args.alerts)
    tooling = args.words.startswith(state.TOOLING)
    if held := held_order(state.load(name), args.words, args.owner):
        return held, state.load(name)["orders"][held], [f"{held} already holds these words, so no order was added"], []
    project, workstream = filing(state.load(name), args.words, args.project, args.workstream)
    title = titled(args.title)
    if tooling:
        check_tooling_words(name, args.words)
    with state.edit(name) as data:
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
        if joined and not (joined["incident"] and joined["state"] not in state.CLOSED_ORDER_STATES):
            raise DriveError(f"--joins {args.joins}: name an open incident order; {args.joins} is {joined['state']}{'' if joined['incident'] else ', not an incident'}")
        incidents = [render.incident_line(other_id, other) for other_id, other in state.open_orders(data).items() if args.incident and not joined and other["incident"]]
        order_id = state.next_id(data["orders"], "o")
        parts = {part: state.new_part() for part in args.parts or [state.whole_order(args.words)]}
        order = data["orders"][order_id] = state.new_order(args.words, slack_key(args.source, message), thread, parts, created) | {
            "due": due_stamp(args.due) if args.due else None,
            "promised": created if args.due else None,
            "alerts": alerts,
            "reporter": reported,
            "incident": args.incident or joined is not None,
            "kind": "build" if args.incident or joined is not None else args.kind,
            "complex": args.complex,
            "options_only": args.options_only,
            "once": args.once,
            "owner": owner,
            "project": project,
            "workstream": workstream,
            "title": title,
            "note": joined and joined["note"],
            "joins": joined and (joined["joins"] or args.joins),
        }
        mark_answers(order_id, order, args.answers)
        if args.due:
            need_thread(order_id, order)
        started = [state.incident_job(data, name, order_id, part, created) for part in (order["parts"] if args.incident and not joined else [])]
        started += [state.INCIDENT_BRIEF] if started else []
    if joined:
        started.append(f"joins {args.joins}: {joined['note'] or 'no record'}; its owner job carries this order with --carries {order_id}")
    return order_id, order, started, incidents


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
    """Open an incident in one call: his order, a job on its fix beside a job on each diagnosis part, and the evidence record its note names."""
    name = state.resolve(args.drive)
    args.parts = [state.INCIDENT_FIX, *state.INCIDENT_PARTS["release" if args.release else "none" if args.no_rollback else "rollback"]]
    order_id, order, started, incidents = added_order(args)
    data = state.load(name)
    argv = ["ccn", "investigation", "open", args.record, "--body", f"{args.words}\n\nFrom {args.source}", "--label", f"drive:{name}", "--label", "incident", "--json"]
    try:
        record = json.loads(reads.run(argv, Path(data["roles"]["coordinator"]["cwd"])))["id"][:7]
    except reads.ReadError as error:
        raise DriveError(
            f"{order_id} and its jobs are added, but its evidence record did not open: {error}; open it with ccn investigation open, then"
            f' drive order set {order_id} --note "record <id>"\n' + "\n".join(started)
        ) from None
    with state.edit(name) as data:
        data["orders"][order_id]["note"] = order["note"] = f"record {record}"
    return emit(args, "\n".join([f"{order_id} record {record}", *started, *incidents]), {"id": order_id, **order, "incidents": incidents})


def incident_stop(args: argparse.Namespace) -> int:
    """Give an open incident its stop when no build can roll the break back: a job with apply authority whose PR, the incident's immediate fix, turns the failing automatic path off and lands at once as an incident fix, until the root fix replaces it."""
    name = state.resolve(args.drive)
    created = state.stamp()
    with state.edit(name) as data:
        order = find(data["orders"], args.id, "order")
        if not state.open_incidents(data, [args.id]):
            raise DriveError(f"{args.id}: refused, it is no open incident order")
        if refused := state.held_elsewhere(data, args.id, state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID"))):
            raise DriveError(f"refused, no stop added: {refused}")
        if running := state.running_stops(data, args.id):
            raise DriveError(f"{args.id}: refused, its stop runs as {', '.join(running)}; resume that job instead")
        order["parts"][state.INCIDENT_STOP] = state.new_part()
        started = state.incident_job(data, name, args.id, state.INCIDENT_STOP, created)
        order["updated"] = created
    return emit(args, f"{started}\n{state.INCIDENT_BRIEF}", {"id": args.id, "job": started.split()[0]})


def incident_freeze(args: argparse.Namespace) -> int:
    """Freeze the broken service's other releases in the turn users are unblocked, until its root fix releases; the freeze's id goes on the order's note for the job that lifts it."""
    name = state.resolve(args.drive)
    data = state.load(name)
    order = find(data["orders"], args.id, "order")
    if not state.open_incidents(data, [args.id]):
        raise DriveError(f"{args.id}: refused, it is no open incident order")
    checkout_dir = Path(data["roles"]["coordinator"]["cwd"])
    until = state.stamp(state.now() + FREEZE_FOR)
    reason = f"{args.id}, {render.cut(order['text'], 80)}: held until its root fix releases"
    argv = [str(checkout_dir / "tools" / "ci"), "release", "freeze", *args.components, "--reason", reason, "--until", until, "--json"]
    try:
        freeze = json.loads(reads.run(argv, checkout_dir))["freeze"]["id"]
    except reads.ReadError as error:
        raise DriveError(f"{args.id}: no freeze set: {error}") from None
    with state.edit(name) as data:
        order = data["orders"][args.id]
        order["note"] = "; ".join(filter(None, [order["note"], f"freeze {freeze}"]))
        order["updated"] = state.stamp()
    return emit(
        args,
        f"{args.id}: {', '.join(args.components)} frozen until {render.when(until)} as {freeze}; the job that merges the root fix lifts it"
        f" with tools/ci release unfreeze {freeze} before the fix's build starts",
        {"id": args.id, "freeze": freeze, "until": until},
    )


def incident_release(args: argparse.Namespace) -> int:
    """Name a release an incident's job started, so the turn cc-slack hands the drive that release failing a step, its stop reopens with a job to get it live."""
    if not RELEASE_NAME.fullmatch(args.release):
        raise DriveError(f"{args.release}: refused, name the release as its post in the releases channel does, such as stark-maclean")
    name = state.resolve(args.drive)
    with state.edit(name) as data:
        order = find(data["orders"], args.id, "order")
        if not state.open_incidents(data, [args.id]):
            raise DriveError(f"{args.id}: refused, it is no open incident order")
        order["releases"][args.release] = {"named": state.stamp(), "failed": None}
        order["updated"] = state.stamp()
    return emit(args, f"{args.id}: {args.release} named; the turn it fails a step, {args.id}'s stop reopens", {"id": args.id, "release": args.release})


def his_message(source: str) -> tuple[str, str, str | None] | None:
    """The Slack message --from names, as state.slack_message reads it, refused when it names Slack in no form that reads; None for a source outside Slack, such as terminal."""
    if (message := state.slack_message(source)) or not (source.startswith("slack:") or "/archives/" in source):
        return message
    raise DriveError(f"--from {source}: refused, name his message by its permalink, a reply's carrying thread_ts, as slack:<channel>/<thread ts>/<his reply ts>, or as slack:<channel>/<ts>")


def slack_key(source: str, message: tuple[str, str, str | None] | None) -> str:
    """Where an order came from as the drive keeps it: slack:<channel>/<ts> of his Slack message, the key state.message_order finds it by, else the source as given."""
    return f"slack:{message[0]}/{message[1]}" if message else source


def order_thread(data: dict, source: str, message: tuple[str, str, str | None] | None, flag: str | None) -> tuple[str, str] | None:
    """The channel and thread an order's lines go in: the one --thread names, checked, else the thread of the message --from names, which its permalink and slack:<channel>/<thread ts>/<his reply ts> carry, so only slack:<channel>/<ts> asks Slack which thread it is in; None for an order from outside Slack with no --thread."""
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


def check_thread(data: dict, flag: str, message: tuple[str, str, str | None] | None) -> tuple[str, str]:
    """The channel and thread --thread names, by permalink, slack:<channel>/<ts>, or a bare ts in the channel of the message the order came from, his DM for one from outside Slack; refused when Slack reads no thread at that ts there, naming the thread a reply is in. The thread of the message the order came from, which --from already names, needs no read."""
    if SLACK_TS.fullmatch(flag):
        where = (message[0] if message else data["slack"]["channel"], flag)
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


def check_tooling_words(name: str, words: str) -> None:
    """Refuse a drive tooling order's words that `cc-slack check-post` finds anything in but the id of a recorded answer of his, since they reach him verbatim: quoted on the line above its PR card, and as the title an {order:} link shows. One line, each finding with its fix first, so a shell keeping only a refusal's last line keeps all of it."""
    if findings := refs.answer_ids(slack.post_findings(words, True), state.load(name)):
        raise DriveError(
            f"refused, `cc-slack check-post` finds {'; '.join(findings)}; a \"{state.TOOLING}\" order's words reach him verbatim, quoted on"
            " the line above its PR card and as the title an {order:} link shows, so fix each in words of your own and pass the fixed words"
        )


def filing(data: dict, words: str, project: str | None, workstream: str | None) -> tuple[str, str]:
    """The project and workstream the dashboard's Now screen files an order under: on a "Drive tooling:" order The drive and Drive tooling unless a flag names another, and on his order both named, refused naming the pairs in use."""
    if words.startswith(state.TOOLING):
        return project or TOOLING_FILING[0], workstream or TOOLING_FILING[1]
    if project and workstream:
        return project, workstream
    missing = " and ".join(f"--{field}" for field, value in (("project", project), ("workstream", workstream)) if not value)
    pairs = "; ".join(f"{named} / {stream}" for named, stream in filing_pairs(data)) or "none yet"
    raise DriveError(f"refused, no order added: name {missing}, which file his order on the dashboard's Now screen, reusing a pair in use where it fits: {pairs}")


def titled(title: str) -> str:
    """The short title the dashboard names an order by, refused when blank, over TITLE_CHARS, or carrying a link or a second line, since his words stay whole in the task."""
    if not title.strip() or len(title) > TITLE_CHARS or "\n" in title or re.search(r"https?://", title):
        raise DriveError(f"--title {json.dumps(title, ensure_ascii=False)}: refused, a title is a few words on one line, at most {TITLE_CHARS} characters and no link, naming his order on the dashboard while his words stay whole in its task")
    return title.strip()


def need_thread(order_id: str, order: dict) -> None:
    if not state.promise_thread(order):
        raise DriveError(f"{order_id}: refused, no time stored: a promised time needs the thread its line goes in; pass --thread <its permalink>")


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


def drive_post_at(link: str, data: dict, order_id: str, named_part: bool, report: dict) -> tuple[dict, bool, tuple[str, str] | None]:
    """The drive's post as Slack holds it, whether it is in one of the order's threads, and the thread of his it joins to them: its own, on an incident each other order's of it, those he asked about it in, its jobs' cards', a card of a PR it holds, or, when its report is about the order, the thread of the message of his the report answers or one his own message started, which it joins, or at his DM root for an order holding no thread but those he asked about it in; a post that delivers a part named with --part counts wherever it was made, as a share in another channel does. A refusal names the order whose thread it is, and only when no order holds it the --joins command for his alert at its root."""
    posted, root = slack.message_and_root(link)
    if not posted.get("from_claude"):
        raise DriveError(f"{link} is not the drive's post")
    threads = slack.order_threads(data, order_id, data["orders"][order_id])
    where = (state.CARD_LINK.search(link)[1], posted.get("thread_ts"))
    asked = report.get("asked") if order_id in state.report_orders(data, report) else None
    answered = where == asked or slack.his_thread(data, order_id, report, root)
    gained = where if answered and where not in threads else None
    top = posted.get("thread_ts", posted["ts"]) == posted["ts"] and where[0] == data["slack"]["channel"]
    ours = (
        not threads
        or where[1] in {thread for _, thread in threads}
        or answered
        or (top and slack.keeps_dm_root(data, order_id, data["orders"][order_id]))
        or slack.names_order_pr(data, order_id, root)
    )
    if not (ours or named_part):
        held = state.thread_order(data, posted.get("thread_ts"))
        joins = (
            "; when his message at the root of that thread is another alert on this incident, that alert is its own order, which the"
            f' coordinator records with drive order add "<his alert>" --title "<a few words>" --from slack:{state.CARD_LINK.search(link)[1]}/{root["ts"]}'
            f" --thread {root['ts']} --joins {order_id} --project \"{data['orders'][order_id]['project']}\" --workstream \"{data['orders'][order_id]['workstream']}\", and this line goes on that order"
            if not held and data["orders"][order_id]["incident"] and root and root.get("from_user")
            else ""
        )
        holder = f"; that thread is {held}'s, so a line about {held} is recorded on it with drive order posted {held} {link}" if held else ""
        raise DriveError(
            f"{link} is in none of the order's threads, its own, those he asked about it in, its cards' or its PRs' cards': {', '.join(thread for _, thread in threads)};"
            f" a post that delivers a part somewhere else is recorded with --record <the report's seq> whose part attr names it{holder}{joins}"
        )
    return posted, ours, gained


def pick_parts(order_id: str, order: dict, named: list[str]) -> list[str]:
    listed = ", ".join(f'"{part}"' for part in order["parts"])
    if not named:
        if len(order["parts"]) != 1:
            raise DriveError(f"{order_id} has parts {listed}; name the one this covers with --part")
        return list(order["parts"])
    if unknown := [part for part in named if part not in order["parts"]]:
        raise DriveError(f"{order_id} has no part {', '.join(unknown)}; its parts are {listed}")
    return list(dict.fromkeys(named))


def mark_answers(order_id: str, order: dict, answers: list[str] | None) -> None:
    """Mark each part of the order answer, words he reads, such as his question's answer, a retro or a status, which a post delivers by giving them whatever they say, or build, work a post delivers only once it says the work is done: every part of an answer order is an answer, --answers names the answers of any other, each part it leaves out work, and with no --answers each part keeps its kind."""
    named = pick_parts(order_id, order, answers) if answers else []
    if order["kind"] != "answer" and answers is None:
        return
    for part, held in order["parts"].items():
        held["kind"] = "answer" if order["kind"] == "answer" or part in named else "build"


def apart_from_filing(order: dict) -> str:
    """The order apart from its project, workstream and title, so filing or titling it alone leaves the time it last moved, which the dashboard reads as when a done order went live."""
    return json.dumps({field: value for field, value in order.items() if field not in FILING}, sort_keys=True)


def refuse_a_renamed_change(data: dict, order_id: str, parts: list[str]) -> None:
    """A --parts list that adds a name refuses to leave out a change a design listed and no builder delivered, since the part under the new name would lose the design and the waits that tie it to its builder; a list that only drops parts, delivered or withdrawn by him, passes."""
    held = data["orders"][order_id]["parts"]
    added = [part for part in parts if part not in held]
    left = [f'"{part}" of {kept["design"]}\'s design' for part, kept in held.items() if part not in parts and kept["design"] and not state.delivered(data, order_id, part)]
    if added and left:
        raise DriveError(
            f"{order_id}: refused, its parts stay as they are: --parts adds {', '.join(json.dumps(part, ensure_ascii=False) for part in added)}"
            f" and leaves out {', '.join(left)}, which no builder delivered; name each change by its part, as drive job add builder"
            ' --part "<the part>" takes it, and leave a change out only once it is delivered or he withdrew it'
        )


def order_set(args: argparse.Namespace) -> int:
    """Change an order, reworking only the PRs --rework names and naming each open PR card of his in a thread it moves into or is sorted again in, which it leaves up; closing one that owes him a promised line reads its thread and refuses until the line is there or he withdrew the time, and closing one done refuses while the bug report it fixes has not heard it is fixed."""
    name = state.resolve(args.drive)
    withdrawn = his_words(args.withdrawn, "withdraw a promised line") if args.withdrawn else None
    unreported = his_words(args.no_reporter, "take the reporter off an order") if args.no_reporter else None
    data = state.load(name)
    refuse_unreworkable(data, args.rework)
    closing = args.state in state.CLOSED_ORDER_STATES and not withdrawn
    order = find(data["orders"], args.id, "order")
    reporter = None if unreported else state.reporter(args.reporter) if args.reporter else order["reporter"]
    if args.state == "done" and reporter and not reporter["told"]:
        raise DriveError(untold(args.id, reporter))
    if args.words is not None:
        if not (order["text"].startswith(state.TOOLING) and args.words.startswith(state.TOOLING)):
            raise DriveError(f'--words: refused, only a "{state.TOOLING}" order\'s words change, to words that open "{state.TOOLING}"; an order of his keeps his words verbatim')
        check_tooling_words(name, args.words)
    if args.proof is not None:
        check_proof(args.proof)
    if args.title is not None:
        titled(args.title)
    alerts = alert_links(args.alerts)
    if (order["incident"] if args.incident is None else args.incident) and (order["options_only"] if args.options_only is None else args.options_only):
        raise DriveError(
            f"{args.id}: refused, an incident order is never limited to options, since its codex jobs start at the alert, before he picks:"
            " an incident takes --no-options-only, and an order limited to options --no-incident"
        )
    proof = args.proof or (order["proof"] if args.state == "done" else None)
    if proof and (seen := surface(data, order)) and not any(brief.SEEN_PROOF.search(found) for found in brief.proofs(proof)):
        raise DriveError(
            f"{'--proof' if args.proof else 'its proof'} {proof}: refused, {args.id} builds something he uses ({seen}), so it is proven"
            " done on that surface: a recording of each step through it (agent-browser's recording, or screencapture -v of the window)"
            " or the link of the live post; data fetched from an API or a command's output does not show what he sees"
            + ("" if args.proof else "; close it with --proof <that recording or link>")
        )
    message = his_message(args.source) if args.source else state.slack_message(order["from"])
    thread = check_thread(data, args.thread, message) if args.thread else None
    destination = order | ({"thread": list(thread)} if thread else {})
    report = need_record_close(data, args.id, args.record, args.state == "dropped" and bool(args.note)) if args.state in state.CLOSED_ORDER_STATES else {}
    closing = closing and state.promise_thread(destination) is not None
    posts, unread = slack.order_posts(data, args.id, destination) if closing and state.owed(order) else ([], None)
    changes = {
        "text": args.words,
        "from": args.source and slack_key(args.source, message),
        "note": args.note,
        "proof": args.proof,
        "thread": thread and list(thread),
        "withdrawn": withdrawn,
        "options_only": args.options_only,
        "once": args.once,
    }
    moment = state.now()
    due = state.parse_due(args.due, moment) if args.due else None
    if due and due <= moment:
        raise DriveError(f"{args.id}: refused, no time stored: {render.when(state.stamp(due))} is already past; promise a time to come")
    promised = ""
    with state.edit(name) as data:
        order = data["orders"][args.id]
        before = apart_from_filing(order)
        aimed = (order["kind"], order["thread"])
        order.update({field: value for field, value in changes.items() if value is not None})
        if due and not (where := state.promise_thread(order)):
            state.remind(order, state.stamp(due), moment)
            promised = (
                f"\nstored {render.when(state.stamp(due))} as the drive's own time: {args.id} has no thread, so no line to him"
                " keeps it, and the watch says DUE once then; a time promised to him needs --thread <its permalink>"
            )
        elif due:
            held = state.owed_at(order) if state.owed(order) else None
            state.promise(order, state.stamp(due), moment)
            promised = "\n" + promise_said(render.when(state.stamp(due)), held, where)
        if closing and state.owed(order):
            if report:
                state.keep_closing(order, posts, state.parse_time(report["written"]))
            else:
                state.keep(order, posts, announced=False)
            if state.owed(order):
                why = f" ({unread})" if unread else ""
                say = f" with say(order={args.id}, record={args.record})" if report else ""
                raise DriveError(
                    f"{args.id} owes him the line promised for {render.when(state.owed_at(order))}, and no drive post is in"
                    f" its thread since {render.when(order['promised'], seconds=True)}{why}: post it there first{say}, or pass"
                    " --withdrawn <his message> when he called it off"
                )
        if args.state:
            order["state"] = args.state
        order["alerts"] = list(dict.fromkeys([*order["alerts"], *alerts]))
        if args.parts:
            refuse_a_renamed_change(data, args.id, args.parts)
            order["parts"] = {part: order["parts"].get(part, state.new_part()) for part in args.parts}
        if args.incident and not order["incident"] and not any(state.INCIDENT_FIX in state.job_parts(data["jobs"][job_id], args.id) for job_id in order["jobs"]):
            raise DriveError(
                f'{args.id}: refused, an incident order starts its fix job at the alert, beside diagnosis: give it a "{state.INCIDENT_FIX}"'
                f" part with --parts and record its job with drive job add codex --order {args.id} --part {state.INCIDENT_FIX} --agent <id> first"
            )
        if args.incident is not None:
            order["incident"] = args.incident
        if args.kind:
            order["kind"] = args.kind
        mark_answers(args.id, order, args.answers)
        if args.complex is not None:
            order["complex"] = args.complex
        if args.reporter or unreported:
            order["reporter"] = reporter
        filed = [field for field in FILING if getattr(args, field) is not None]
        for field in filed:
            order[field] = (getattr(args, field) or "").strip() or None
        if not filed or args.lined or apart_from_filing(order) != before:
            order["updated"] = state.stamp(moment)
        if args.lined:
            order["last_line"] = order["updated"]
    cards = thread_cards(name, order) if args.rework or (order["kind"], order["thread"]) != aimed else []
    reworked = rework_named_prs(name, args.id, args.rework, cards, f"drive order set {args.id} --rework <PR>")
    return emit(args, render.order_line(args.id, order) + promised + "".join(f"\n{line}" for line in reworked), {"id": args.id, **order})


def untold(order_id: str, reporter: dict) -> str:
    """Why an order with a reporter may not close done yet: the thread its bug was reported in has not heard it is fixed."""
    return (
        f"{order_id}: refused, its bug was reported in {reporter['link']}, and no fix-live note there is recorded. Once drive order set"
        f" {order_id} --proof <the check> keeps the proof its fix is live, the handler of a report on it posts one plain line in that"
        f" thread with slack_reply saying what is fixed and since when, and records it with drive order told {order_id} <its permalink>;"
        " when the bot cannot post in that thread, it posts the note as usual to the reporter's Slack user id, which opens the group DM"
        " of that person, him and the bot, and records it with --group-dm <that user id>."
        f" The coordinator, which posts nothing, sends that report with --attr close={order_id}, and drive order set {order_id}"
        " --no-reporter <his message> takes the reporter off when he says not to tell them"
    )


def check_proof(proof: str) -> None:
    """Refuse a proof that is a claim: one line naming no link and no file that exists, where a check's link, file or pasted output proves it."""
    if "\n" in proof.strip() or any(found.startswith("http") or Path(found).expanduser().exists() for found in brief.proofs(proof)):
        return
    raise DriveError(
        f'--proof "{proof}": refused, a proof is the check itself, its link, its saved file or its output, never a claim of what it showed:'
        " rerun the check and paste its output, or link its run"
    )


def filing_pairs(data: dict) -> list[tuple[str, str]]:
    return sorted({(order["project"], order["workstream"]) for order in data["orders"].values() if order["project"] and order["workstream"]})


def surface(data: dict, order: dict) -> str | None:
    """Where he sees or uses an order's result, by the Native line of a brief of one of its jobs; None when no brief names a surface."""
    natives = [(data["jobs"][job_id].get("brief_flags") or {}).get("native") for job_id in order["jobs"]]
    return next((native for native in natives if native and not NO_SURFACE.match(native)), None)


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


def promise_said(due: str, held: str | None, where: tuple[str, str]) -> str:
    """What --due did with the time: stored to start at the drive post that tells him it, or stored behind a line already owed, which keeps firing until a post lands."""
    post = f"the next drive post in {where[0]} thread {where[1]}"
    if held is None:
        return f"stored {due}: it waits on {post}, which tells him the time; from that post he is owed a line by {due}"
    return (
        f"stored {due} behind the line already owed for {render.when(held)}: that time keeps firing DUE until {post} lands,"
        f" which tells him {due} and makes it the time owed"
    )


def need_record_close(data: dict, order_id: str, seq: str | None, dropped_with_note: bool) -> dict:
    """The report that closes the order, read from cci, or none when the order closes without one; refused, as say and order posted refuse it, when the session that wrote it does not hold the order."""
    if seq is None:
        if not dropped_with_note and state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID")) == "communicator":
            raise DriveError(
                f"{order_id}: a handler closes only an order the report record it posted names in its close attr;"
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
    """The attrs of a record, read from cci: a report's close, part, job and catch_up, with the lane that wrote it as its writer, when as written, for a correction the record it corrects, and as asked the channel and thread of the message of his it answers, followed up its re chain."""
    try:
        record = json.loads(reads.run(["cci", "show", seq, "--json"], Path.cwd()))
    except reads.ReadError as error:
        raise DriveError(f"record {seq} could not be read: {error}") from None
    corrects = {"corrects": str(record["re"])} if record["kind"] == "correction" and record.get("re") else {}
    asked = {"asked": thread} if record.get("re") and (thread := agents.asked_thread([str(record["re"])])) else {}
    return record["fields"].get("attrs", {}) | {"writer": record["lane"], "written": record["at"]} | corrects | asked


def order_posted(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    data = state.load(name)
    role = state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID"))
    if args.part and not args.undo and role and role == find(data["orders"], args.id, "order")["owner"] and not (args.record and role == "communicator"):
        return emit(args, *answered_by(name, args.id, args.part, args.link))
    order = communicators_order(data, args.id)
    if not args.undo:
        report = report_attrs(args.record) if args.record else {}
        if args.part and (args.id, args.part) not in state.part_items(report, args.id):
            named = f"record {args.record}'s part attr names {report.get('part') or 'none'}" if args.record else "no report record names it"
            raise DriveError(
                f'--part "{args.part}": refused, {named}; a post delivers a part only through the report record whose part attr names it:'
                f" pass --record <that record's seq>; a part a post of the drive already answered is marked from the session holding {args.id}"
                f' with drive order posted {args.id} {args.link} --part "{args.part}", posting nothing'
            )
        return emit(args, *record_post(name, args.id, args.link, report))
    [undone] = pick_parts(args.id, order, [args.part] if args.part else [])
    with state.edit(name) as data:
        data["orders"][args.id]["parts"][undone]["posted"] = None
    return emit(args, f'{args.id} "{undone}": not delivered', {"id": args.id, "part": undone, "posted": None})


def answered_by(name: str, order_id: str, part: str, link: str) -> tuple[str, dict]:
    """Deliver a part of the order its holder's session holds by the drive's post that already answered it, wherever it was made, posting nothing; refused for an open incident's fix the watch has not recorded live, since no post delivers it, and when Jev reads that post's words as not saying the part is done."""
    data = state.load(name)
    [part] = pick_parts(order_id, data["orders"][order_id], [part])
    if state.unlive_fix(data, order_id, part):
        raise DriveError(f'--part "{part}": refused, ' + state.UNLIVE_FIX.format(order=order_id, part=part))
    posted, *_ = drive_post_at(link, data, order_id, True, {})
    delivered, unsaid = carried(order_id, data["orders"][order_id], [part], [(order_id, part)], posted.get("text") or "")
    if not delivered:
        order = data["orders"][order_id]
        if order["parts"][part]["kind"] == "answer":
            raise DriveError(f'--part "{part}": refused, Jev reads the words of {link} as not giving "{part}", so it delivers nothing; mark it by the drive\'s post that gives it')
        answers = " ".join(json.dumps(each, ensure_ascii=False) for each, held in order["parts"].items() if held["kind"] == "answer" or each == part)
        raise DriveError(
            f'--part "{part}": refused, Jev reads the words of {link} as not saying "{part}" is done, so it delivers nothing; mark it by the drive\'s post whose words do,'
            f" or, when it is words he reads, such as his question's answer, a retro or a status, which a post gives whatever it says, mark it so first with drive order set {order_id} --answers {answers}"
        )
    with state.edit(name) as data:
        data["orders"][order_id]["parts"][part]["posted"] = link
    return f'{order_id} "{part}": posted {link}, the post that answered it{unsaid}', {"id": order_id, "parts": [part], "posted": link}


def order_told(args: argparse.Namespace) -> int:
    """Record the one fix-live note the bug report an order fixes gets once the fix is proven live: the drive's post in the report's thread, or, when the bot cannot post there, its post in the group DM of the reporter, him and the bot."""
    name = state.resolve(args.drive)
    data = state.load(name)
    order = communicators_order(data, args.id)
    if not (reporter := order["reporter"]):
        raise DriveError(f"{args.id} names no bug report to tell: drive order set {args.id} --reporter <its permalink> names one")
    if reporter["told"]:
        raise DriveError(f"{args.id}: refused, the thread its bug was reported in heard it is fixed once, in {reporter['told']}, and hears it only once")
    if not order["proof"]:
        raise DriveError(
            f"{args.id}: refused, it keeps no proof that its fix is live, so a note now would come before the fix:"
            f" drive order set {args.id} --proof <the check> records it once the fix is live"
        )
    posted, _ = slack.message_and_root(args.link)
    channel = state.CARD_LINK.search(args.link)[1]
    if args.group_dm and not (posted.get("from_claude") and slack.group_dm(args.group_dm) == channel):
        raise DriveError(f"{args.link} is not the drive's post in the group DM of {args.group_dm}, him and the bot, the one cc-slack opens for channel_id {args.group_dm}")
    if not args.group_dm and (not posted.get("from_claude") or (channel, posted.get("thread_ts")) != (reporter["channel"], reporter["thread"])):
        raise DriveError(
            f"{args.link} is not the drive's post in {reporter['link']}, the thread the bug was reported in; when the bot cannot post"
            " there, the note goes to the group DM of the reporter, him and the bot: pass the reporter's Slack user id with --group-dm"
        )
    with state.edit(name) as data:
        data["orders"][args.id]["reporter"]["told"] = args.link
    where = f"told in the group DM of {args.group_dm}, him and the bot" if args.group_dm else "told in the reporter's thread"
    return emit(args, f"{args.id}: {where}, {args.link}", {"id": args.id, "told": args.link, "group_dm": args.group_dm})


def slack_user(text: str) -> str:
    if SLACK_USER.fullmatch(text):
        return text
    raise argparse.ArgumentTypeError("name the reporter by their Slack user id, such as U0123ABCDE: a channel id names no person")


def communicators_order(data: dict, order_id: str) -> dict:
    if state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID")) != "communicator":
        raise DriveError(
            "only the communicator records a post in an order's thread or takes back a part's mark: send the post's facts as a report record"
            f" to communicator@{data['name']}, its part or close attr naming what it delivers, and the communicator's handler posts it and records it;"
            f' a part a post of the drive already answered is marked from the session holding {order_id} with drive order posted {order_id} <that post\'s link> --part "<part>",'
            " with or without --record, posting nothing"
        )
    return find(data["orders"], order_id, "order")


def report_refusal(data: dict, order_id: str, report: dict) -> str | None:
    """Why a report may deliver or close nothing on an order, the one check say, order posted and order set --state done share: the drive's session that wrote it does not hold the order. Only the communicator's side posts, so the refusal names the report its handler passes instead: the holder's, which on the communicator's own order the handler writes itself from the communicator's lane."""
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


def thread_refusal(data: dict, order_id: str, where: tuple[str, str | None], report: dict) -> str | None:
    """Why a post may not go out on an order: its channel and thread are none of the order's, its own, on an incident each other order's of it, those he asked about it in, its cards' or the thread under a card of a PR it holds, nor, when its report is about the order, the thread of the message of his the report answers or one his own message started, nor his DM root for an order holding no thread but those he asked about it in, naming the channel to pass when the thread is the order's in another channel, else the order whose thread it is, if any; a post carrying a report about the order goes in the order's own threads, since posted with the other order it delivers nothing on this one."""
    if order_id not in data["orders"] or not (owned := slack.order_threads(data, order_id, data["orders"][order_id])):
        return None
    asked = [report["asked"]] if report.get("asked") and order_id in state.report_orders(data, report) else []
    threads = list(dict.fromkeys([*owned, *asked]))
    channel, thread = where
    dm = data["slack"]["channel"]
    if where in threads or (where == (dm, None) and slack.keeps_dm_root(data, order_id, data["orders"][order_id])):
        return None
    if place := next((place for place, ts in threads if ts == thread), None):
        return f"refused, {order_id}'s thread {thread} is in his DM, so leave channel_id out" if place == dm else f"refused, {order_id}'s thread {thread} is in channel {place}, so pass channel_id {place}"
    root = slack.root(channel, thread) if thread else None
    if slack.his_thread(data, order_id, report, root) or slack.names_order_pr(data, order_id, root):
        return None
    named = ", ".join(ts if place == dm else f"{ts} with channel_id {place}" for place, ts in threads)
    at = "his DM root" if not thread else f"thread {thread}" if channel == dm else f"thread {thread} of channel {channel}"
    held = state.thread_order(data, thread)
    whose = f"{held}'s thread and " if held else ""
    listed = f"refused, {at} is {whose}none of {order_id}'s threads, its own, those he asked about it in, its cards' or its PRs' cards': {named};"
    record = f"drive order posted {order_id} <its permalink> --record <the report's seq>"
    if report and state.report_order(data, report) == order_id:
        part = f', or post it with no order and record "{report["part"]}" with {record}' if report.get("part") else ""
        return f"{listed} its report is about {order_id}, so post it in one of them{part}"
    on_held = f", post it with order {held} when the line is about {held}" if held else ""
    return f"{listed} post it in one of them{on_held}, or post it with no order and record the part it delivers with {record}"


def reported_parts(data: dict, order_id: str, order: dict, report: dict, items: list[tuple[str | None, str]]) -> list[str]:
    """The parts of the order a post delivers: each item of its report's part attr names on this order, but an open incident's fix the watch has not recorded live, or each part of an order its report closes. A post whose report names neither part nor close, whatever job it names, or that carries no report is a progress line that delivers none, on an order of one part as on one of several."""
    closing = order_id in state.closed_orders(report)
    if named := [part for on, part in items if on == order_id]:
        return [part for part in pick_parts(order_id, order, named) if closing or not state.unlive_fix(data, order_id, part)]
    return list(order["parts"]) if closing else []


def carried(order_id: str, order: dict, parts: list[str], items: list[tuple[str | None, str]], text: str) -> tuple[list[str], str]:
    """The parts a post delivers on the order once Jev has read its words, each one its report's part attr names only when they carry it, and what its line adds of each they leave undelivered; a part a close delivers is not asked, and when Jev gives no answer each goes as named, which the line says."""
    named = [part for part in parts if (order_id, part) in items]
    try:
        left = settled.uncarried(text, order, named)
    except settled.Unheard as failure:
        asked = ", ".join(f'"{part}"' for part in named)
        return parts, f"; Jev did not say whether its words carry {asked} ({failure}), so each is delivered as named"
    if not left:
        return parts, ""
    unsaid = ""
    for kind, reading in UNSAID.items():
        if kinded := [f'"{part}"' for part in left if order["parts"][part]["kind"] == kind]:
            unsaid += f"; {', '.join(kinded)}: not delivered, Jev reads its words as {reading[len(kinded) > 1]}"
    return [part for part in parts if part not in left], unsaid


def delivering(name: str, order_id: str, report: dict, text: str) -> str:
    """What a post of these words would deliver on the order and on each other order an item of its report's part attr names, as record_post marks it once the post is out."""
    data = state.load(name)
    items = state.part_items(report, order_id)
    lines = []
    for each in dict.fromkeys([order_id, *(on for on, _ in items)]):
        order = find(data["orders"], each, "order")
        parts, unsaid = carried(each, order, reported_parts(data, each, order, report, items), items, text)
        quoted = ", ".join(f'"{part}"' for part in parts)
        lines.append(f"{each} {quoted}: would be delivered{unsaid}" if parts else f"{each} progress line, no part delivered{unsaid}")
    return "; ".join(lines)


def record_post(name: str, order_id: str, link: str, report: dict) -> tuple[str, dict]:
    """Record the drive's post on the order it went out on and on each other order an item of its report's part attr names, so one post in a thread two orders share delivers on both; an item naming no order is a part of the order the post went out on, on every order it is recorded on."""
    items = state.part_items(report, order_id)
    others = dict.fromkeys(on for on, _ in items if on != order_id)
    first, *rest = [record_on(name, each, link, report, items) for each in [order_id, *others]]
    return "; ".join(line for line, _ in [first, *rest]), first[1] | ({"also": [fields for _, fields in rest]} if rest else {})


def record_on(name: str, order_id: str, link: str, report: dict, items: list[tuple[str | None, str]]) -> tuple[str, dict]:
    """Record the drive's post on one order: the promised time it keeps, the time its words promise his next line by but on an incident, whose next line the watch owes five quiet minutes after its last, the order's last line, the parts its report's items deliver there that its words carry, and, when its report is about this order and it is in the thread of the message of his the report answers or in one his own message started, that thread among the order's, with when it joined."""
    data = state.load(name)
    order = communicators_order(data, order_id)
    if refused := report_refusal(data, order_id, report):
        raise DriveError(refused)
    delivered = reported_parts(data, order_id, order, report, items)
    posted, ours, gained = drive_post_at(link, data, order_id, any(on == order_id for on, _ in items), report)
    delivered, unsaid = carried(order_id, order, delivered, items, posted.get("text") or "")
    at = slack.sent_at(posted)
    line = state.stamp(at)
    closing = order_id in state.closed_orders(report)
    promised = promised_due(posted.get("text") or "", at) if ours and not order["incident"] else None
    kept = ""
    with state.edit(name) as data:
        order = data["orders"][order_id]
        if gained:
            order["asked_in"].append([*gained, line])
        if ours:
            if state.owed(order) and closing:
                state.keep_closing(order, [at], state.parse_time(report["written"]))
                kept = "" if state.owed(order) else f", its close keeps the line promised for {render.when(order['due'])}"
            elif state.owed(order):
                state.keep(order, [at])
            if promised:
                state.remind(order, state.stamp(promised), at)
                kept += f", its words promise him the next line by {render.when(order['due'])}, now owed"
            order["last_line"] = max(order["last_line"] or line, line, key=state.parse_time)
        for part in delivered:
            order["parts"][part]["posted"] = link
    quoted = ", ".join(f'"{part}"' for part in delivered)
    said = f"{quoted}: posted" if delivered else "progress line, no part delivered:"
    away = "" if ours else ", outside his threads, so it is no line to him and keeps no promised time"
    joined = f", in thread {gained[1]}, where he asked about it, now one of its threads" if gained else ""
    return f"{order_id} {said} {link}{away}{joined}{kept}{unsaid}", {"id": order_id, "parts": delivered, "posted": link, "due": order["due"]}


def promised_due(text: str, at: datetime) -> datetime | None:
    """The time a post made at `at` promises his next line by: the first clock time its words put after next line, update, post or summary whose time of day nearest the post is after it, read with AM or PM as written, else on either half of a 12-hour clock, so at 9:29 PM "next line by 9:45" is 9:45 PM and "next line by 7:30" is past, and a time after any other words, such as "due by 2:37", promises nothing."""
    moment = at.replace(second=0, microsecond=0)
    for said in PROMISED_TIME.finditer(limits.masked(text)):
        hour = int(said[1])
        hours = [hour % 12 + (12 if said[3].upper() == "P" else 0)] if said[3] else [hour % 12, hour % 12 + 12] if 1 <= hour <= 12 else [hour]
        times = [day.replace(hour=each, minute=int(said[2])) for each in hours for day in (moment - timedelta(days=1), moment, moment + timedelta(days=1))]
        if (nearest := min(times, key=lambda time: abs(time - at))) > at:
            return nearest
    return None


def job_add(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    created = state.stamp()
    if args.kind == "builder" and not args.remote and not args.agent and not args.waits:
        refuse_a_local_builder(args.repo)
    if args.waits and args.agent:
        raise DriveError("--waits records a job whose agent starts on the watch's FREE line once its last wait ends, so it takes no --agent")
    if args.lands and args.kind != "builder":
        raise DriveError("--lands names the PRs a builder lands, which only a builder does")
    lands = list(dict.fromkeys(map(state.pr_key, args.lands)))
    check_waits(name, args.waits)
    with state.edit(name) as data:
        order = find(data["orders"], args.order, "order")
        parts = pick_parts(args.order, order, args.part)
        refuse_a_shared_at_once_part(parts)
        role = state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID"))
        if refused := (
            state.held_elsewhere(data, args.order, role)
            or unlandable(data, lands)
            or state.change_wait_refusal(data, args.waits)
            or brief.job_refusal(name, data, args.order, args.kind, parts, bool(lands), args.waits)
        ):
            raise DriveError(f"refused, no job added: {refused}")
        order["owner"] = order["owner"] or role
        beside = [
            f'{other_id} {other["kind"]} {other["state"]} on {", ".join(json.dumps(part, ensure_ascii=False) for part in shared)}: {other["goal"][:60]}'
            for other_id in order["jobs"]
            if (other := data["jobs"][other_id])["state"] in ("running", "waiting")
            and (shared := [part for part in parts if part in (other["parts"] if other["order"] == args.order else other["carries"][args.order])])
        ]
        job_id = state.next_id(data["jobs"], "j")
        job = data["jobs"][job_id] = state.new_job(args.order, args.kind, args.goal, parts, created) | {
            "agent": args.agent,
            "where": "remote" if args.remote else "local",
            "repo": args.repo,
            "branch": args.branch,
            "worktree": args.worktree,
            "waits": list(dict.fromkeys(args.waits)),
        }
        state.hold_unstarted(job)
        if crowd := args.kind == "builder" and not args.remote and not args.agent and not args.waits and place.crowded(data, job_id, state.now()):
            place.queue(job, state.parse_time(created))
        order["jobs"].append(job_id)
        order["updated"] = created
        state.end_carried_designs(data, args.order, created)
    waiting = [f"{job_id} {job['said']}: start no agent for it until the watch's FREE line says its last wait ended"] if args.waits else []
    if crowd:
        waiting.append(f"{job_id} waits its turn for a builder slot on this Mac, {place.positions(data)[job_id]}, since {crowd}: start no agent for it until the watch's FREE line names it")
    return emit(args, "\n".join([job_id, *waiting, *beside]), {"id": job_id, **job, "beside": beside})


def unlandable(data: dict, keys: list[str]) -> str | None:
    """Why a builder may not start on --lands alone: each PR it lands is a drive PR his Approve or an exempt class lets land, since only then does it build nothing his order's research would shape."""
    for key in keys:
        if key not in data["prs"]:
            return f"--lands {key}: no drive PR; add it with drive pr add {state.pr_url(key)} --job <the job that opened it>"
        may_land, why = tap_verdict(data["prs"][key])
        if not may_land:
            return f"--lands {key}: it may not land yet, {why}; a builder lands only a PR he approved or an exempt class lands"
    return None


def refuse_a_shared_at_once_part(parts: list[str]) -> None:
    """The immediate fix and a follow-up are each a job of its own, since each starts at once past the gates the order's other parts wait on: the immediate fix's PR lands while the proper fix's waits on his cards, and a follow-up's design starts while the PR it follows lands."""
    for part, why in ((state.IMMEDIATE_FIX, "its PR lands at once, while the proper fix's waits on his cards"), (state.FOLLOW_UP, "its design starts at once, while the PR it follows lands")):
        if part in parts and len(parts) > 1:
            others = ", ".join(json.dumps(other, ensure_ascii=False) for other in parts if other != part)
            raise DriveError(f'the "{part}" part is a job of its own: {why}, so one job takes "{part}" and another {others}')


def job_set(args: argparse.Namespace) -> int:
    """Change a job; a state it already holds changes nothing, stopped is the one state set by hand, done only on a design a builder carries, and any other --state is refused with nothing changed, since the job's return sets it; an order it carries reworks only the PRs of the job --rework names, and each other open card of his on the job's PRs is named and left up."""
    from drive import view

    name = state.resolve(args.drive)
    known = state.load(name)
    found = find(known["jobs"], args.id, "job")
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
    if args.state == "stopped":
        fix_kept(state.load(name), args.id)
    changes = {"agent": args.agent, "state": args.state, "said": args.said, "branch": args.branch, "due": job_clock(args.id, args.due)}
    urls = dict.fromkeys(state.pr_url(state.pr_key(url)) for url in args.pr)
    if refused := state.change_wait_refusal(known, args.waits):
        raise DriveError(refused)
    if jobs := [wait for wait in args.waits if wait in known["jobs"] and known["jobs"][wait]["kind"] not in state.CHANGE_KINDS]:
        found = view.live_cards(name)
        holds = [
            *(card for job_id in jobs for card in view.open_cards(known["jobs"][job_id])),
            *(key for job_id in jobs for key in map(state.pr_key, known["jobs"][job_id]["prs"]) if (pr := known["prs"].get(key)) and pr["state"] == "open"),
        ]
        instead = (
            f"open on {' and '.join(jobs)}: {', '.join(holds)}; wait on the one this job needs, as --waits {holds[0]}"
            if holds
            else f"nothing is open on {' and '.join(jobs)} to wait on, no card of his and no PR"
        )
        raise DriveError(
            f"--waits {', '.join(jobs)}: a job waits on his card, a PR the drive tracks, a release or the change a builder or codex job builds, and on"
            f" another job only as a build order's designer, builder or codex lane added waiting on its reader, drive job add <kind> --order <o>"
            f" --waits <the reader>; {instead}"
        )
    check_waits(name, args.waits)
    told_him(name, args)
    known = state.load(name)
    for order_id in args.carries:
        refuse_a_crossed_order(name, known, find(known["jobs"], args.id, "job")["order"], order_id, f"{args.id} --carries")
    if args.rework and len(args.carries) != 1:
        raise DriveError("--rework names the PRs one carried order reworks: name that order with one --carries <o>")
    refuse_unreworkable(known, args.rework, args.id)
    shown = args.card and shown_card(name, args.card)
    lane = args.agent and args.agent.startswith(state.DISPATCH_PREFIX) and orca.receipt(args.agent)
    with state.edit(name) as data:
        job = find(data["jobs"], args.id, "job")
        job.update({field: value for field, value in changes.items() if value is not None})
        if args.card:
            hold_card(job, args.card, shown)
        job["waits"] = merged_waits(job, args)
        state.hold_unstarted(job)
        if args.carries:
            if args.parts:
                raise DriveError("--parts replaces the parts of the job's own order; name a carried order's parts with --part")
            if args.part and len(args.carries) > 1:
                raise DriveError("--part names the parts of one carried order; carry one order a call to name its parts")
            for order_id in args.carries:
                if order_id == job["order"]:
                    raise DriveError(f"{args.id} is {order_id}'s own job; add its parts with --part")
                state.carry(data, args.id, order_id, pick_parts(order_id, find(data["orders"], order_id, "order"), args.part))
        elif args.part or args.parts:
            named = pick_parts(job["order"], data["orders"][job["order"]], args.parts or args.part)
            job["parts"] = named if args.parts else list(dict.fromkeys([*job["parts"], *named]))
            refuse_a_shared_at_once_part(job["parts"])
        if args.agent and args.agent == job.get("dispatch"):
            job.update(lane, where="remote", agent=None, handed_to=None)
        elif lane:
            job.update(lane, where="remote", agent=None)
        elif args.agent:
            job["where"] = "local"
            job["session"] = os.environ.get("CLAUDE_CODE_SESSION_ID")
        if args.repro:
            job["repro"] = keep_repro(name, args.id, args.repro)
        job["prs"].extend(url for url in urls if url not in job["prs"])
        job["updated"] = state.stamp()
        state.end_carried_designs(data, job["order"], job["updated"])
    owned = {pr["url"] for pr in data["prs"].values() if pr["job"] == args.id}
    cards = [card for card in fresh_pr_cards(name) if owned & set(card["prs"])] if args.carries else []
    reworked = [
        line for order_id in args.carries for line in rework_named_prs(name, order_id, args.rework, cards, f"drive job set {args.id} --carries {order_id} --rework <PR>")
    ]
    return emit(args, already + render.job_line(args.id, job) + "".join(f"\n{line}" for line in reworked), {"id": args.id, **job})


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


def tooling(order: dict) -> bool:
    return order["text"].startswith(state.TOOLING)


def refuse_a_crossed_order(name: str, data: dict, own: str, other: str, acting: str) -> None:
    """Refuse a job carrying, or a PR serving, an order across the line between drive tooling and his feature work: the coordinator acts on no "Drive tooling:" order, and a tooling order's job or PR never carries or serves his feature order, nor the reverse."""
    order = find(data["orders"], other, "order")
    if tooling(order) and state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID")) == "coordinator":
        raise DriveError(
            f'{acting} {other}: refused, {other} is a "{state.TOOLING}" order, which only the meta session acts on: send a'
            f" defect record to meta@{name} naming what it needs, and the meta session's builder takes it over"
        )
    if tooling(order) == tooling(data["orders"][own]) or own == other:
        return
    kinds = {True: "a drive tooling order", False: "an order of his feature work"}
    raise DriveError(
        f"{acting} {other}: refused, {own} is {kinds[tooling(data['orders'][own])]} and {other} is {kinds[tooling(order)]}, and"
        f" one job or PR never carries both: start a job on {other} with drive job add <kind> --order {other}"
    )


def told_him(name: str, args: argparse.Namespace) -> None:
    """An incident's job waits on his card only once the incident's thread says so: the drive's post there linking each card, with what it blocks and the default the job builds on meanwhile."""
    from drive import view

    data = state.load(name)
    job = find(data["jobs"], args.id, "job")
    cards = [card for card in [args.card, *args.waits] if card and state.card_wait(data, card)]
    incidents = state.open_incidents(data, [job["order"], *job["carries"]])
    if not (cards and incidents):
        return
    if not args.told:
        raise DriveError(
            f"{args.id} works on incident {', '.join(incidents)}: a card that blocks it is said in the incident's thread at once, a line"
            " linking the card with what it blocks and the recommended option the job builds on meanwhile; post that line, then pass --told <its link>."
            f" {TOLD_LINK}"
        )
    posted, _ = slack.message_and_root(args.told)
    threads = state.incident_threads(data, incidents)
    if not (posted.get("from_claude") and posted.get("thread_ts") in threads):
        raise DriveError(f"--told {args.told}: name the drive's own post in the incident's thread, {' or '.join(threads) or 'which it has none of yet'}")
    found = view.live_cards(name) or view.NO_CARDS
    linked = set(view.SLACK_TS.findall(posted.get("text") or ""))
    if unlinked := [card for card in cards if view.message_key(view.wait_entry(data, card, found)["url"] or card) not in linked]:
        raise DriveError(f"--told {args.told} does not link {', '.join(unlinked)}: the line links each card that blocks the incident. {TOLD_LINK}")


def shown_card(name: str, url: str) -> dict | None:
    """His open question card at this link as cc-slack lists it, read again when the drive's copy lacks it; None when his cards cannot be read or it is not open."""
    from drive import view

    found = view.live_cards(name)
    if not (found and view.asked_card(found, url)):
        view.refresh_cards(name)
        found = view.live_cards(name)
    return found and view.asked_card(found, url)


def hold_card(job: dict, url: str, shown: dict | None) -> None:
    """Keep the card on the question it draws, found by its waits id or its link, else on a question of its own, so each card a job asked on stays open until his answer to it."""
    from drive import view

    waits = shown and shown["card"].get("waits")
    question = next(
        (
            question
            for question in job["questions"]
            if waits and question["waits"] == waits or question["card"] and view.message_key(question["card"]) == view.message_key(url)
        ),
        None,
    )
    if question is None:
        question = state.new_question(None, shown and shown["question"], state.stamp())
        job["questions"].append(question)
    question.update(card=url, waits=question["waits"] or waits)


def asked_on(name: str, waits: str) -> set[str]:
    """Each card his question under the waits id is drawn on, by its message, read before it is drawn again: the open one cc-slack lists under the id and each one a job's question keeps."""
    from drive import view

    try:
        listed = view.cards(name)["questions"]
    except view.CardsError as error:
        raise DriveError(f"nothing posted: a card drawn again under its waits id moves each wait on its old card to the new one, which needs his cards, and cc-slack could not list them: {error}") from None
    kept = (question for job in state.load(name)["jobs"].values() for question in job["questions"] if question["waits"] == waits and question["card"])
    return {view.message_key(entry["url"]) for entry in listed if entry["card"].get("waits") == waits} | {view.message_key(question["card"]) for question in kept}


def follow_card(name: str, waits: str, asked: set[str], url: str) -> None:
    """Record the card cc-slack drew under the waits id as the card of each question asked under it, and point each wait of a job or PR on a card it was drawn on before at it: a card asked again with new words posts as a new card, its old one left pointing at it."""
    from drive import view

    moved = asked - {view.message_key(url)}
    with state.edit(name) as data:
        for job in data["jobs"].values():
            for question in job["questions"]:
                if question["waits"] == waits:
                    question["card"] = url
        for owner in [*data["jobs"].values(), *data["prs"].values()]:
            owner["waits"] = list(dict.fromkeys(url if view.message_key(wait) in moved else wait for wait in owner["waits"]))


def job_ask(args: argparse.Namespace) -> int:
    """Keep a job's ASK in the drive once it is in the shape he reads, each block in its own file; from a file, stdin with -, or with neither a remote lane's latest report, one opening `DESIGN <question>`, a reader's return, read as the ASK it puts to him. A block asking again what one of the job's questions his answer has not closed asks takes that question's place, its card and waits id kept, and one asking what another job's open question asks is refused. With --drop, throw away a question the job kept with no card drawn for it."""
    if args.check:
        with tempfile.TemporaryDirectory(prefix="drive-ask-check-") as scratch:
            limits.shape(limits.ask_block(named_text(args.check, "ASK block file")), "check", "ask", Path(scratch), args.unsettled)
        print("ready")
        return 0
    if args.id is None:
        raise DriveError("name the job whose ASK this keeps: drive job ask <j> <file>; drive job ask --check <file> only checks its shape")
    name = state.resolve(args.drive)
    if args.drop:
        dropped = Path(args.drop).expanduser()
        title, replaced = drop_ask(name, args.id, dropped)
        gone = "a later ask of it replaced its question, so only the file is deleted" if replaced else "its question kept there is dropped and the file deleted"
        line = f'{args.id} no longer asks "{render.cut(title or "", 80)}" from {dropped}: {gone}, so no ask record may name it'
        return emit(args, line, {"id": args.id, "dropped": str(dropped), "title": title, "replaced": replaced})
    job = find(state.load(name)["jobs"], args.id, "job")
    text = named_text(args.file, "ASK block file") if args.file else job.get("report")
    if not text:
        raise DriveError(f"{args.id} sent no report to read an ASK from; pass the file its ASK block is in, or - and the block on stdin")
    kept, asked, question, decided = keep_ask(name, args.id, text, args.unsettled)
    return emit(args, decided or str(kept), {"id": args.id, "question": str(kept), "waits": question["waits"], "closed": question["closed"], **vars(asked)})


def drop_ask(name: str, job_id: str, file: Path) -> tuple[str | None, bool]:
    """Throw away a question the job kept with no card drawn or being drawn for it, such as a near copy of one already asking him: it leaves the job's questions, so nothing counts it open and no ask record may name it, and its file is deleted unless another of the job's questions keeps it; a question on a card is refused, since only his answer to that card closes it. A file of the job's whose question a later ask of the job replaced, as drive job ask printed its path, is deleted alone. Its title, and whether a later ask had replaced it."""
    asks = state.drive_dir(name) / "asks"
    with state.edit(name) as data:
        job = find(data["jobs"], job_id, "job")
        if (question := state.kept_in(job, file)) is None:
            if file.parent.resolve() != asks.resolve() or not file.name.startswith(f"{job_id}-") or not file.exists():
                raise DriveError(f"{file} keeps no question of {job_id}; drive status {job_id} names the file each of its questions is kept in")
            title = limits.ask_block(file.read_text()).splitlines()[0].removeprefix("ASK ").strip()
            file.unlink()
            return title, True
        if drawn := question["card"] or question["waits"]:
            raise DriveError(
                f'refused, nothing dropped: {job_id}\'s question "{render.cut(question["title"] or "", 80)}" kept in {file} is on his card {drawn},'
                " which only his answer closes; drop only a question kept with no card drawn for it"
            )
        job["questions"] = [other for other in job["questions"] if other is not question]
        if job["said"] == question["title"] and job["questions"]:
            job["said"] = job["questions"][-1]["title"]
        job["updated"] = state.stamp()
    if state.kept_in(job, file) is None:
        file.unlink(missing_ok=True)
    return question["title"], False


def keep_ask(name: str, job_id: str, text: str, unsettled: str | None) -> tuple[Path, limits.Question, dict, str | None]:
    """Keep the job's ASK block, or the ASK a DESIGN return puts to him, in its own file once it is in the shape he reads, in place of the job's open question asking the same, and set the job waiting on it; asked again with no --unsettled, as a reader's DESIGN report re-asks it, it keeps the --unsettled its kept question with that title carries; a question any job asked on a card since withdrawn keeps that card's waits id, so asking it again redraws that card. One Jev reads an answer of his as already deciding, asked with no --unsettled and refused for nothing else, is kept settled by that answer, draws no card and leaves the job as it was, so it goes straight to building on his words, and the session holding its order hears so in one record; one refused for anything else stays kept as the job's pending question with its refusal, which draws no card, so the watch says the job waits on a card never posted until it asks again or drops it."""
    from drive import superseded, view

    text = limits.ask_block(text)
    data = state.load(name)
    job = data["jobs"][job_id]
    if unsettled is None:
        title = next(line.strip() for line in text.splitlines() if line.strip()).removeprefix("ASK ").strip()
        unsettled = next((question["unsettled"] for question in job["questions"] if question["unsettled"] and state.same_question(question["title"], title)), None)
    asked = limits.shape(text, name, job_id, state.drive_dir(name) / "pictures", unsettled, any(state.hurts_now(data["orders"][order_id]) for order_id in [job["order"], *job["carries"]]))
    said = "\n".join([asked.title, *asked.summary]).casefold()
    if unnamed := [pr["port_of"] for pr in data["prs"].values() if pr["job"] == job_id and pr.get("port_of") and f"port of #{pr['port_of']}" not in said]:
        raise DriveError(
            f'{job_id}\'s PR is a port of #{unnamed[0]}: its ASK\'s summary says "port of #{unnamed[0]}", as the text of that PR\'s link,'
            f" and what in the target tree forces a choice #{unnamed[0]} did not make, such as what that tree lacks"
        )
    if found_elsewhere := elsewhere(data, job_id, asked.title, unsettled):
        raise DriveError(asked_elsewhere(job_id, *found_elsewhere))
    shown = "\n".join([asked.title, *asked.summary, *asked.options])
    order = data["orders"][job["order"]]
    heard = settled.thread_words(data, job["order"], view.stored_cards(name)[0]["team_url"], shown)
    decided = job["may_settle"]
    if not decided or decided["title"] != asked.title:
        found = settled.candidates(shown, text, [name], data["roles"]["coordinator"]["cwd"], superseded.said_under(data, job), heard, order["text"])
        decided = {"title": asked.title, "answer": settled.decided(asked.title, asked.options, found)}
    refused = settled.refusals(
        decided["answer"], superseded.open_under(data, job), [*settled.own_words(data, job["order"], shown), *heard], "\n".join([asked.title, *asked.options]), unsettled
    )
    deciding = decided["answer"] if refused and not unsettled and refused == settled.decided_refusal(decided["answer"], None) else None
    kept = state.drive_dir(name) / "asks" / f"{job_id}-{state.ask_hash(text)}.md"
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(text)
    if deciding:
        settled_note(name, data, job_id, asked.title, deciding)
    with state.edit(name) as data:
        job = data["jobs"][job_id]
        twins = [question for other, question in asking(data, asked.title) if other == job_id]
        reworded = [other for other in job["questions"] if other["refused"] and (state.same_question(asked.title, other["title"]) or superseded.shared(asked.title, other["title"]))]
        if deciding:
            closed = state.new_close("answered", deciding["said"], deciding.get("link"), answer=deciding["id"], ruling=None if "link" in deciding else deciding["id"])
            question = state.new_question(str(kept), asked.title, state.stamp(), unsettled, closed=closed)
            replaced = [*twins, *reworded]
            job.update(updated=state.stamp(), may_settle=decided)
        elif refused:
            answered = decided["answer"] if settled.decided_refusal(decided["answer"], unsettled) else None
            closed = answered and state.new_close("answered", answered["said"], answered.get("link"), answer=answered["id"])
            question = state.new_question(str(kept), asked.title, state.stamp(), unsettled, refused=refused, closed=closed)
            replaced = [*(twin for twin in twins if twin["refused"]), *reworded]
            job.update(updated=state.stamp(), may_settle=decided)
        else:
            withdrawn = superseded.redrawable(data, asked.title)
            held = next((twin for twin in [*twins, *withdrawn] if twin["card"]), None) or next((twin for twin in twins if twin["waits"]), None)
            question = state.new_question(str(kept), asked.title, held["asked"] if held else state.stamp(), unsettled)
            if held:
                question.update(waits=held["waits"], card=held["card"])
            replaced = [*twins, *withdrawn, *reworded]
            moment = state.stamp()
            job.update(said=asked.title, updated=moment, returned=moment, may_settle=None)
            if not (job["kind"] == "reader" and job["state"] == "done"):
                job["state"] = "waiting"
        job["questions"] = [*(other for other in job["questions"] if not any(other is gone for gone in replaced)), question]
    if refused and not deciding:
        raise DriveError(
            f"{refused}\n{kept} keeps this ASK as {job_id}'s pending question with this refusal: no card draws for it, and the watch says"
            f" {job_id} waits on a card never posted until {job_id} asks it again, in these words or others sharing three rare words, or"
            f" `drive job ask {job_id} --drop {kept}` drops it"
        )
    return kept, asked, question, deciding and settled_line(job_id, job, question, deciding)


def settled_note(name: str, data: dict, job_id: str, title: str, answer: dict) -> None:
    """Tell the session holding the job's order, in one record that wakes nobody, that his answer settled the job's question, so it builds on his words with no card."""
    job = data["jobs"][job_id]
    lane = state.holder_lane(data["orders"][job["order"]]) or state.heard_lane(data, job["order"])
    text = f'{job_id} builds on his answer {answer["id"]}, which already decides its question "{render.cut(title, 120)}", so no card draws for it'
    from drive import tools

    try:
        tools.sent(name, job_id, [{"kind": "evidence", "to": [f"{lane}@{name}"], "attr": {"job": job_id, "answer": answer["id"]}, "text": text}])
    except DriveError as error:
        raise DriveError(f"nothing kept: his answer {answer['id']} decides {job_id}'s question, and the record telling {lane}@{name} so did not post: {error}") from None


def settled_line(job_id: str, job: dict, question: dict, answer: dict) -> str:
    """What `drive job ask` prints for a question his answer settled: his words, and who builds on them now."""
    if job["state"] in state.FINISHED_JOB_STATES:
        then = f"{job_id} ended {job['state']}, so start the job that builds it now on the same part, its brief naming his answer {answer['id']} under Decisions that bind"
    else:
        then = f"{job_id} builds it now"
    return f'{question["file"]} keeps {job_id}\'s question settled, no card: {settled.decided_line(answer)} {then}'


def asking(data: dict, title: str) -> list[tuple[str, dict]]:
    """Each job's open question asking him what this title asks, oldest first, by its job: one question has one card, so every ask of it after the first redraws that card."""
    from drive import view

    return [(job_id, question) for job_id, job in data["jobs"].items() for question in view.open_questions(job) if state.same_question(question["title"], title)]


def elsewhere(data: dict, job_id: str, title: str, unsettled: str | None) -> tuple[str, dict, list[str]] | None:
    """Another job's open question asking him what this title asks, or about the same subject in other words, with the proper terms the two share; a subject match `unsettled` names by its card's waits id or its block's file, saying why this asks something else, is none."""
    from drive import superseded, view

    for other_id, other in data["jobs"].items():
        for question in view.open_questions(other) if other_id != job_id else []:
            if question["refused"]:
                continue
            if state.same_question(question["title"], title):
                return other_id, question, []
            if (question["waits"] or Path(question["file"]).name) not in (unsettled or "") and (terms := superseded.shared(title, question["title"])):
                return other_id, question, terms
    return None


def asked_elsewhere(job_id: str, other: str, question: dict, terms: list[str]) -> str:
    """Why a job may not ask what another job's open question asks, or about its subject in other words: his answer to that one card closes the subject everywhere."""
    where = (
        f"its open card {question['card']} ({question['waits']})"
        if question["card"]
        else f"its card {question['waits']}, not up yet" if question["waits"] else f"the block kept in {question['file']}, its card not drawn yet"
    )
    wait = question["waits"] or "<its waits id, once its card is up>"
    refused = (
        f'{job_id}: refused, nothing kept: {other} already asks him "{question["title"]}" on {where}'
        + (f", the same subject in other words, sharing {', '.join(terms)}" if terms else "")
        + f", and one subject has one card, so his answer closes it everywhere. Wait on that card with drive job set {job_id} --waits {wait},"
        f" which frees {job_id} at his answer, or ask only what it leaves open, under a question of its own"
    )
    if not terms:
        return refused
    return (
        f"{refused}; when his words replaced that card, withdraw it first with drive cards withdraw {wait} --words \"<his words>\""
        f" --via <his permalink>; when this asks something else, ask again with --unsettled naming"
        f" {question['waits'] or Path(question['file']).name} and why. His words, on three cards about one search worker's cache disk:"
        ' "We\'re already addressing this with the lazy + restate object + increase disk size right? So why is this card still here?"'
    )


def job_card(args: argparse.Namespace) -> int:
    """Print the question card a kept ASK block draws, its Show me code drawn as highlighted pictures, and give the question kept in that file the card's waits id."""
    name = state.resolve(args.drive)
    card = question_card(name, *card_source(name, args))
    return emit(args, json.dumps(card), card)


def card_source(name: str, args: argparse.Namespace) -> tuple[Path, str]:
    """The kept ASK block a card draws and the waits id given for it: after a job, its newest kept block or the one named, under --waits or the waits id its card already has; a file alone, under --waits. `question_card` draws a question already holding a waits id under that one."""
    if not JOB_ID.fullmatch(args.ask):
        if not args.waits:
            raise DriveError(f"{args.ask}: a card drawn from a file needs --waits <drive>:<job>-<the ask record's seq>; drive job card <j> draws a job's newest kept ASK")
        return named_file(args.ask, "ASK block file").resolve(), args.waits
    job = find(state.load(name)["jobs"], args.ask, "job")
    named = Path(args.file).expanduser().resolve() if args.file else None
    kept = [question for question in job["questions"] if question["file"] and not question["refused"] and named in (None, Path(question["file"]).resolve())]
    if not kept:
        raise DriveError(f"{args.ask} keeps no ASK block{f' in {args.file}' if args.file else ''}: keep one with drive job ask {args.ask} <file>")
    question = kept[-1]
    if not (waits := args.waits or question["waits"]):
        raise DriveError(f"{args.ask}'s ASK in {question['file']} has no card yet, so no waits id: pass --waits {name}:{args.ask}-<its ask record's seq>")
    return Path(question["file"]), waits


def question_card(name: str, path: Path, waits: str) -> dict:
    """The card a job's question draws: its newest kept ASK block, under the waits id the question already holds, so every ask of one question redraws its one card; the waits id given names the card of a question holding none yet, or one of the job's cards drawn again on purpose. A waits id naming no job of the drive, a card asked outside any job, only draws the block. Each reference in its summary, Show me and option descriptions, such as {at:<seq>} in a Show me table, is filled as say fills a post's, through `refs.resolved` and `refs.slack`, before its file names are drawn as code."""
    from drive import superseded, view

    asked = kept_ask(path)
    data = state.load(name)
    job_id = waits.partition(":")[2].partition("-")[0]
    if job := data["jobs"].get(job_id):
        question = state.kept_in(job, path) or next(
            (question for other, question in reversed(asking(data, asked.title)) if other == job_id and question["file"]), None
        )
        if question and Path(question["file"]).resolve() != path.resolve():
            path = Path(question["file"])
            asked = kept_ask(path)
        if question and question["waits"] and not any(other["waits"] == waits for other in job["questions"]):
            waits = question["waits"]
        if refused := superseded.redraw_refusal(data, job, waits, str(path)):
            raise DriveError(refused)
    pr_cards = (view.live_cards(name) or view.NO_CARDS)["pr_cards"]
    if refused := view.tap_claim_refusal(data, job["order"] if job else "", "\n".join([asked.title, *asked.summary, *asked.options]), pr_cards):
        raise DriveError(f"{path}: {refused}.{limits.FROM_ASK.format(waits=waits)}")
    show_me = picture.drawn(state.drive_dir(name) / "pictures", asked.show_me)
    found = refs.resolved(name, "\n".join([*asked.summary, *show_me, *asked.hints.values()]))
    card = replace(
        asked,
        summary=[refs.slack(line, found, None) for line in asked.summary],
        show_me=[refs.slack(line, found, None) for line in show_me],
        hints={option: refs.slack(hint, found, None) for option, hint in asked.hints.items()},
    ).card(name, waits)
    if job_id in data["jobs"]:
        with state.edit(name) as data:
            keep_waits(data["jobs"][job_id], path, waits)
    return card


def kept_ask(path: Path) -> limits.Question:
    """The question an ASK file draws: one `drive job ask` kept, named for the hash of its words, read under the rules it was kept under, and any other, rebuilt from his card or never kept, held to every rule a new block meets; a kept file whose words no longer hash to its name was edited by hand and draws nothing, since only `drive job ask` changes a kept question."""
    text = path.read_text()
    if not (named := state.KEPT_ASK.fullmatch(path.stem)):
        return limits.question(text)
    if named["hash"] != state.ask_hash(text):
        raise DriveError(
            f"{path} no longer holds the words drive job ask kept there, so it draws no card: a kept question changes only through"
            f" drive job ask, which checks the new words against every rule, so keep them with drive job ask {named['job']} <file> and draw the file it prints"
        )
    return limits.redrawn(text)


def cards_redraw(args: argparse.Namespace) -> int:
    """Print, one JSON line each in one pass, the card `drive job card` prints for each of his open question cards, drawn from the ASK file `ask_source` finds for it and marked in_place, so cc-slack edits each where it sits and posts no new version; each card that does not redraw is named once the rest are printed."""
    from drive import view

    name = state.resolve(args.drive)
    view.refresh_cards(name)
    if (found := view.live_cards(name)) is None:
        raise DriveError(f"his open cards could not be read: {view.stored_cards(name)[1]}")
    shown = {waits: card for card in found["questions"] if (waits := card["card"].get("waits"))}
    drawn, missed = [], []
    for waits in args.waits or shown:
        if waits not in shown:
            missed.append(f"{waits}: not one of his open cards")
            continue
        try:
            drawn.append(question_card(name, ask_source(name, shown[waits]), waits) | {"in_place": True})
        except DriveError as error:
            missed.append(f"{waits}: {error}")
    if drawn:
        emit(args, "\n".join(map(json.dumps, drawn)), drawn)
    if missed:
        raise DriveError("these cards did not redraw:\n- " + "\n- ".join(missed))
    return 0


def cards_withdraw(args: argparse.Namespace) -> int:
    """End as withdrawn each open question drawn on his card that his words, wherever he wrote them, withdraw, or whose question or options they change, and its card follows: it shows his words and a link to them, with no buttons, so a late tap records nothing and no question asks him on a card while its work is being redone; its job asks again under the card's waits id once the work is back, which redraws it."""
    from drive import superseded, view

    name = state.resolve(args.drive)
    if unsaid := settled.unsaid(args.words, args.via, view.live_cards(name)):
        raise DriveError(f"{args.card}: refused, not withdrawn: {unsaid}")
    with state.edit(name) as data:
        closed = state.new_close("withdrawn", args.words, args.via)
        ended = [each for job_id, question in view.drawn_on(data, args.card) if question["closed"] is None for each in superseded.end(data, job_id, question, closed)]
    if not ended:
        raise DriveError(f"{args.card}: no open question of this drive is drawn on that card, by its waits id or permalink; drive status <job> names the card each question is drawn on")
    done = superseded.follow(name, ended)
    asked = sorted({f"{job_id} asks again under {question['waits']} once its work is back, which redraws its card" for job_id, question in ended if question["waits"]})
    line = "\n".join([f"withdrew {len(ended)} question(s) drawn on his card {args.card}", *done, *asked])
    return emit(args, line, {"card": args.card, "ended": [{"job": job_id, "waits": question["waits"], "card": question["card"]} for job_id, question in ended]})


def ask_source(name: str, shown: dict) -> Path:
    """The ASK file his open card draws again: the one a job's question keeps under the card's waits id; else the one kept by a question linked to the card whose ASK asks what the card asks; else one rebuilt from the card as the drive stored it, kept on a question of its own in the job its waits id names, which takes the place of every question of that job holding the waits id or linked to the card."""
    from drive import view

    waits, url, title = shown["card"]["waits"], shown["url"], shown["card"]["title"]
    kept = [question for job in state.load(name)["jobs"].values() for question in job["questions"] if question["file"]]
    if held := next((question["file"] for question in kept if question["waits"] == waits), None):
        return Path(held)
    linked = [question["file"] for question in kept if question["card"] and view.message_key(question["card"]) == view.message_key(url)]
    if asked := next((file for file in linked if asks(Path(file), title)), None):
        return Path(asked)
    rebuilt = state.rebuilt_ask(name, waits)
    if not rebuilt.exists():
        rebuilt.write_text(limits.rebuilt(shown["card"], rebuilt.parent))
    with state.edit(name) as data:
        if job := data["jobs"].get(waits.partition(":")[2].partition("-")[0]):
            job["questions"] = [
                question
                for question in job["questions"]
                if question["waits"] != waits and not (question["card"] and view.message_key(question["card"]) == view.message_key(url))
            ]
            job["questions"].append(state.new_question(str(rebuilt), title, shown["asked"], card=url) | {"waits": waits})
    return rebuilt


def asks(file: Path, title: str) -> bool:
    """Whether a kept ASK file redraws the question titled title; one kept under rules it no longer meets redraws none."""
    try:
        return limits.redrawn(file.read_text()).title == title
    except DriveError:
        return False


def keep_waits(job: dict, file: Path, waits: str) -> None:
    """Give the waits id to the newest question kept in the file; a question of the job that held it before, which this card redraws, hands over its card and leaves the job."""
    question = state.kept_in(job, file)
    if question is None:
        return
    redrawn = next((other["card"] for other in job["questions"] if other is not question and other["waits"] == waits and other["card"]), None)
    question.update(waits=waits, card=question["card"] or redrawn)
    job["questions"] = [other for other in job["questions"] if other is question or other["waits"] != waits]


def job_report(args: argparse.Namespace) -> int:
    """Keep a job's whole return from stdin in its history, which is its report, its first line setting the job's state, or refuse it with nothing kept, naming the fix, when that line would set none or it types a clock time for work already done; a reader's DESIGN is kept as its ASK in the same call, whose path it prints. With no stdin, print every return the job took, newest first."""
    name = state.resolve(args.drive)
    data = state.load(name)
    job = find(data["jobs"], args.id, "job")
    if args.stdin:
        whole = sys.stdin.read()
        if not whole.strip():
            raise DriveError(f"stdin held no report for {args.id}; pipe its whole return in: printf '%s\\n' '<line>' ... | drive job report {args.id} -")
        line = whole.strip().splitlines()[0].strip()
        if refused := brief.report_refusal(name, data, args.id, whole) or state.result_refusal(data, args.id, line, whole) or limits.typed_time(whole, data):
            raise DriveError(f"{args.id}: refused, nothing kept: {refused}")
        if job["kind"] == "builder" and re.match(r"(?:READY|PUSHED)\b", line):
            try:
                served.check(name, state.carded_prs(data, args.id), [], [line for line in whole.splitlines() if line.startswith("DESIGN:")], {})
            except DriveError as error:
                raise DriveError(f"{args.id}: refused, nothing kept: {error}") from None
        if job["kind"] == "builder" and re.match(r"READY\b", line) and (held := unready(data, job, line)):
            raise DriveError(f"{args.id}: refused, nothing kept: {held}")
        asked = decided = None
        if job["kind"] == "reader" and line.startswith("DESIGN"):
            try:
                asked, _, _, decided = keep_ask(name, args.id, whole.strip(), None)
            except DriveError as error:
                raise DriveError(f"{args.id}: refused, its report not kept: a reader's DESIGN is his card as it stands, kept as its ASK, and drive job ask refused it: {error}") from None
        state.keep_report(name, args.id, line, whole)
        with state.edit(name) as data:
            state.take_result(data, args.id, line, whole)
            state.add_changes(data, args.id, whole)
        kept = str(state.replies_path(name, args.id))
        merging = merge_reported(name, args.id)
        shown = f"{kept}\n{decided}" if decided else f"{kept}\nits ASK is kept in {asked}, the path its ask record names" if asked else kept
        shown = "\n".join([shown, *(f"{key}: {said}" for key, said in merging)])
        return emit(args, shown, {"id": args.id, "report": kept, "ask": asked and str(asked), "merging": dict(merging)})
    text = state.report_text(name, args.id)
    if not text and not job["said"]:
        raise DriveError(f"{args.id} kept no report and has returned nothing yet")
    if not text:
        return emit(args, f"{job['said']}\n({args.id} kept no report; this one-line result is all it returned)", {"id": args.id, "report": None, "said": job["said"]})
    return emit(args, text.rstrip("\n"), {"id": args.id, "report": text})


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


def keep_repro(name: str, job_id: str, source: str) -> str:
    path = named_file(source, "reproduction file")
    kept = state.repro_path(name, job_id, path.suffix)
    kept.parent.mkdir(exist_ok=True)
    if not kept.exists() or not kept.samefile(path):
        shutil.copy(path, kept)
    return str(kept)


def place_job(args: argparse.Namespace) -> int:
    builders = place.local_builders(state.load(state.resolve(args.drive)), state.now())
    share, ceiling = place.busy(), place.limit()
    where, why = place.verdict(share, ceiling, builders)
    payload = {"where": where, "why": why, "busy": round(share, 3), "limit": ceiling, "local_builders": len(builders)}
    return emit(args, f"{where}: {why}", payload)


def refuse_a_local_builder(repo: str | None) -> None:
    """A monorepo builder starts on this Mac only while its load says local; one past this Mac's cap of builders is added waiting its turn for a builder slot, which the watch's FREE line gives it, and the agent_brief hook starts no other builder here while it waits."""
    if (repo or "").casefold() != place.MONOREPO.casefold():
        return
    where, why = place.verdict(place.busy(), place.limit(), {})
    if where == "remote":
        raise DriveError(f"refused, no job added: {why}, so this builder starts remote: add --remote, or wait until drive place says local")


def remote_job(data: dict, job_id: str) -> dict:
    job = find(data["jobs"], job_id, "job")
    if job.get("where") == "remote":
        return job
    if job.get("agent"):
        restore = f", or put it back on its lane with drive job set {job_id} --agent {job['dispatch']}, then resume it" if job.get("dispatch") else ""
        raise DriveError(f"{job_id} runs on this Mac as agent {job['agent']}; resume it with SendMessage{restore}")
    raise DriveError(
        f"{job_id} has no agent and no lane yet: start it on a Sprite with drive job launch {job_id} <brief>, as an Orca"
        f" worker on this Mac with drive job launch {job_id} <brief> --local, or start an Agent here whose brief ends Drive: <drive>, order <order>, job {job_id}"
    )


def other_sessions_agent(job: dict) -> str | None:
    """This session, when the job runs as an Agent another session started, which only that session's SendMessage reaches, so this one hands the job to a fresh agent or lane of its own."""
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    return session if session and job["where"] == "local" and job["agent"] and job["session"] != session else None


def answer_first(name: str, order: dict, order_id: str, job_id: str) -> str:
    """Where an answer order's reader sends his answer the moment it is proven, by his pick "answer-only jobs also post their answer once found": a progress report the communicator's handler posts in the order's thread, ahead of the full report the coordinator closes the order with."""
    attrs = {"thread": order["thread"][1]} if order["thread"] else {}
    attrs |= {"order": order_id, "job": job_id, "progress": "true"}
    return ANSWER_FIRST.format(drive=name, attrs=json.dumps(attrs))


def job_brief(args: argparse.Namespace) -> int:
    """Write a job's brief from what the drive holds: his order's words verbatim, each ruling's recorded words above the drive's notes, the earlier jobs' transcripts, and the lines the call names, each flag it leaves out kept from the job's earlier brief but a handoff's event."""
    name = state.resolve(args.drive)
    data = state.load(name)
    job = find(data["jobs"], args.id, "job")
    order = data["orders"][job["order"]]
    earlier_flags = job.get("brief_flags") or {}
    kept = [flag for flag in BRIEF_FLAGS if not getattr(args, flag) and earlier_flags.get(flag)]
    flags = {flag: getattr(args, flag) or earlier_flags.get(flag) for flag in BRIEF_FLAGS}
    if missing := [f"--{flag.replace('_', '-')}" for flag in BRIEF_NEEDS if not flags[flag]]:
        raise DriveError(f"{args.id} has no earlier brief to keep {', '.join(missing)} from: name each")
    if job["state"] == "waiting" and job["prs"] and not (job.get("agent") or job.get("dispatch") or args.handoff):
        raise DriveError(
            f"{args.id} is an adopted PR's job, waiting with no agent on {', '.join(job['prs'])}: its builder starts only on an event"
            " that needs action, failed checks, requested changes, a new head or his tap; when the watch reports one, brief it"
            " with --handoff <the PR, its head and the event line verbatim>"
        )
    if flags["native"] and not NO_SURFACE.match(flags["native"]) and not flags["interactions"]:
        raise DriveError(
            f"{args.id}'s result is something he uses, by --native: name each click or step he takes on it with --interactions,"
            " which its builder walks through and records as its proof"
        )
    checkout_dir = Path(data["roles"]["coordinator"]["cwd"])
    earlier = [line for other in order["jobs"] if other != args.id and (line := transcript_line(other, data["jobs"][other]))]
    planned = order["kind"] == "build" and not order["incident"]
    found = brief.research(name, data, job["order"]) if planned and job["kind"] in brief.RESEARCHED_KINDS else None
    researched = [closed for question in data["jobs"][found[0]]["questions"] if (closed := question["closed"]) and closed["how"] != "withdrawn"] if found else []
    settles = [closed["ruling"] for closed in researched if closed["ruling"]]
    heard = [f'his answer "{closed["words"]}"' + (f" ({closed['link']})" if closed["link"] else "") for closed in researched if not closed["ruling"] and closed["words"]]
    rulings = [*(ruling_words(answer_id, checkout_dir) for answer_id in dict.fromkeys([*(flags["ruling"] or []), *settles])), *dict.fromkeys(heard)]
    scratch = Path(flags["scratch"]).expanduser() if flags["scratch"] else state.drive_dir(name) / "scratch" / args.id
    scratch.mkdir(parents=True, exist_ok=True)
    fields = {
        "Order (his words)": order["text"],
        "Serves": flags["serves"],
        "Goal": job["goal"],
        "Done when": flags["done_when"],
        "Where": flags["where"],
        "Runs as": flags["runs_as"],
        "Handoff": args.handoff,
        "Decisions that bind": rulings or "none named; search his answers on the area",
        "Research": planned and job["kind"] == "reader" and READER_RESEARCH,
        "Researched": found and found[0],
        "Answer": order["kind"] == "answer" and job["kind"] == "reader" and answer_first(name, order, job["order"], args.id),
        "Native": flags["native"],
        "Interactions": flags["interactions"],
        "Would he ask": flags["would_he_ask"],
        "Plan": flags["plan"],
        "Incident": incident_line(data, args.id, flags["incident"]),
        "Scratch": str(scratch),
        "Transcripts": earlier,
        "Drive": f"{name}, order {job['order']}, job {args.id}",
    }
    written = state.drive_dir(name) / "jobs" / f"{args.id}.brief.md"
    written.parent.mkdir(exist_ok=True)
    lines = [
        f"{field}:" + ("".join(f"\n- {item}" for item in value) if isinstance(value, list) else f" {value}")
        for field, value in fields.items()
        if value
    ]
    text = "\n".join(lines) + "\n"
    written.write_text(text)
    with state.edit(name) as data:
        data["jobs"][args.id].update(brief=str(written), brief_flags=flags)
        if given := bool(brief.SERVES_TOOLING.search(text)) and data["orders"][job["order"]]["owner"] != "meta":
            data["orders"][job["order"]]["owner"] = "meta"
    line = f"Read your brief first: {written}\nhis words from {job['order']}, rulings: {len(rulings)}, earlier jobs: {len(earlier)}"
    if kept:
        line += "\nkept from its earlier brief: " + ", ".join(f"--{flag.replace('_', '-')}" for flag in kept)
    if given:
        line += (
            f"\n{job['order']} is the meta session's order now, since a builder serving drive tooling is its alone: only the meta session"
            f" starts {args.id} and its agents and posts its reports; from the coordinator, start no agent on it and send"
            f" meta@{name} a defect record naming {args.id} and its `Read your brief first:` line"
        )
    return emit(args, line, {"id": args.id, "brief": str(written), "rulings": len(rulings), "earlier": earlier, "kept": kept})


def incident_line(data: dict, job_id: str, given: str | None) -> str | None:
    """A brief's Incident: line: the call's own, then on an incident order a builder's release rule and the rule a lane of its kind follows on a choice, which a choice never stops, then on a job tracing a live bug's cause the FIX line its report ends with, each once."""
    job = data["jobs"][job_id]
    incident = data["orders"][job["order"]]["incident"]
    rules = [release_rule() if incident and job["kind"] == "builder" else None, state.INCIDENT_CHOICE.get(job["kind"]) if incident else None, state.FIX_RULE if state.traced(data, job_id, "") else None]
    return "; ".join(filter(None, [given, *(rule for rule in rules if rule and rule not in (given or ""))])) or None


def release_rule() -> str:
    """The rule incidents.md states for every release a drive job starts, read from the page so the brief and the page share one text."""
    return " ".join(RELEASE_RULE.search((PAGES / "incidents.md").read_text())[1].split())


def ruling_words(answer_id: str, checkout_dir: Path) -> str:
    answer = settled.shown(answer_id, str(checkout_dir))
    return f"{answer['title']} ({answer['id'][:7]})\n{textwrap.indent(settled.his(answer['body']), '  ')}"


def transcript_line(job_id: str, job: dict) -> str | None:
    if job.get("where") == "remote" and job.get("dispatch"):
        return f"{job_id} {job['kind']}, a remote lane: drive job replies {job_id}"
    path = artifacts.transcript(job)
    return f"{job_id} {job['kind']}: {path}" if path else None


def job_launch(args: argparse.Namespace) -> int:
    """Start a job's next worker by the one relaunch launch and resume share, with no message: its first Orca lane, on a Sprite or with --local on this Mac, where a brief that runs drive break-glass starts, stamped as launching once nothing refuses it so the watch reads it as starting until the launch could have finished; the next lane of a job whose worker ended; a lane taking over from an agent on this Mac that stopped or that another session holds; or the worker an earlier launch of its brief started and never recorded, whose ready receipt the launcher keeps."""
    return relaunch(args, state.resolve(args.drive), None)


def job_resume(args: argparse.Namespace) -> int:
    """Send a job its next event by the one relaunch launch and resume share: to its lane while its worker takes messages, an answer to an open question that names `LOG <url>`s carrying those logs ahead of the text, read here as the watch reads them; else in the handoff of its next worker. A resume the watch handed it a PR's move with records a refusal as an event, which wakes the coordinator with the lines it carried."""
    name = state.resolve(args.drive)
    try:
        return relaunch(args, name, args.text)
    except DriveError as error:
        if args.handed:
            data = state.load(name)
            order = data["jobs"][args.id]["order"]
            state.append_event(name, "resume", job=args.id, order=order, text=args.text, error=str(error), lane=state.heard_lane(data, order))
        raise


def relaunch(args: argparse.Namespace, name: str, text: str | None) -> int:
    """The one path launch and resume share, under the job's own lock, so a second waits for the first and acts on the worker it started: a lane whose worker takes messages gets the text, one whose worker ended starts its next in a handoff, an agent on this Mac whose last response ended on an API error or refusal hands the job to a lane on a Sprite, an agent another session holds hands it to this one, and a job with no worker yet starts its first."""
    with state.job_locked(name, args.id, orca.LAUNCH_SECONDS):
        data = state.load(name)
        job = find(data["jobs"], args.id, "job")
        taker = other_sessions_agent(job)
        if taker and text is not None:
            return hand_over(args, name, data, taker)
        if job.get("where") == "remote" and (dispatch := job.get("dispatch")):
            try:
                end = orca.lane_end(job)
            except orca.OrcaError as error:
                done = "sent" if text is not None else "started"
                raise DriveError(
                    f"{args.id}'s lane cannot be checked, so nothing was {done}: {error}; {'resume' if text is not None else 'launch'} it again once Orca reaches its lane {orca.ON_MAC if state.on_mac(job) else 'on its Sprite'}"
                ) from None
            if end:
                return restart(args, name, dispatch, end, text)
            if text is None:
                raise DriveError(f"{args.id} already started as dispatch {dispatch}, whose worker takes messages: drive job resume {args.id} <the event> sends it one")
            return messaged(args, name, data, job, text)
        died = None if taker or not job.get("agent") else watch.agent_death(job, data, state.now())
        if job.get("agent") and not (taker or died):
            remote_job(data, args.id)
        if text is not None and not died:
            started_job(data, args.id)
        agent = job.get("agent")
        started = launched(name, args.id, None if text is not None else args.brief, text is None and args.local, text, died)
        job = record_start(name, args.id, started, None)
    line = f"{args.id} started {orca.place(job)} as dispatch {started['dispatch']}"
    if text is not None:
        line = f"{args.id}: the message went to the job's next worker, started {orca.place(job)} as dispatch {started['dispatch']} in a handoff carrying it"
    if died:
        line += f", taking over from its agent {agent} on this Mac, which takes no message: {died}"
    return emit(args, line, started)


def messaged(args: argparse.Namespace, name: str, data: dict, job: dict, text: str) -> int:
    """Send the text to a lane whose worker takes messages, its open question's `LOG <url>`s read ahead of it."""
    asked = reads.logs(job["report"] or "") if job.get("ask") else None
    if asked:
        try:
            said = reads.answer(asked, Path(data["roles"]["coordinator"]["cwd"]), name, args.id)
        except reads.ReadError as error:
            said = f"{asked}: {error}"
        text = f"{said}\n\n{text}"
    how = orca.answer(name, job, text) + (f", with {asked} read ahead of your text" if asked else "")
    with state.edit(name) as data:
        fresh = data["jobs"][args.id]
        if fresh.get("ask") == job.get("ask"):
            fresh.update(ask=None, state="running")
        fresh.update(updated=state.stamp(), undelivered=[])
    return emit(args, f"{args.id}: {how}", {"id": args.id, "how": how})


def launched(name: str, job_id: str, brief_file: str | None, local: bool, text: str | None, died: str | None) -> dict:
    """Start a job's first lane, or one taking over from an agent on this Mac that stopped or that another session holds, with the text in its handoff when a resume brought one."""
    data = state.load(name)
    job = data["jobs"][job_id]
    if adopted := orca.adopted(launch_brief(name, job_id)):
        return {field: adopted[field] for field in LAUNCHED}
    if not job.get("agent") and (waits := [wait for wait in job["waits"] if local or wait != state.SLOT_WAIT]):
        raise DriveError(f"{job_id} waits on {', '.join(waits)}: launch it on the watch's FREE line, which comes once the last wait ends")
    taker = other_sessions_agent(job)
    if not (brief_file or job.get("brief")):
        raise DriveError(f"{job_id} has no brief: write it with drive job brief {job_id}, or name its file")
    root = orca.checkout(name, data)
    written = named_text(brief_file or job["brief"], "brief file")
    if taker or died:
        written = orca.agent_handoff(orca.brief_of(written), job, text, died)
    role = state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID"))
    if refused := (
        state.held_elsewhere(data, job["order"], role)
        or brief.brief_refusal(name, data, job_id, written)
        or orca.uncarried(name, written)
        or (f"its brief runs drive break-glass, which no Sprite lane runs, by {reads.NO_SPRITE_GRANT}: launch it with --local" if not local and reads.GRANT_STEP.search(written) else None)
    ):
        raise DriveError(f"{job_id}: refused, not launched: {refused}")
    with state.edit(name) as data:
        job = data["jobs"][job_id]
        if taker or died:
            job.update(agent=None, session=taker or job["session"])
        job.update(where="remote", route=state.LOCAL_ROUTE if local else None, launching=state.stamp(), waits=[wait for wait in job["waits"] if wait != state.SLOT_WAIT])
    kept = launch_brief(name, job_id)
    kept.parent.mkdir(exist_ok=True)
    try:
        receipt = launch_attempt(name, job, job_id, kept, orca.vm_brief(name, job_id, written, job["route"]), root, None)
    except orca.Unstarted:
        with state.edit(name) as data:
            data["jobs"][job_id]["launching"] = None
        raise
    return {field: receipt[field] for field in LAUNCHED}


def launch_brief(name: str, job_id: str) -> Path:
    return state.drive_dir(name) / "jobs" / f"{job_id}.md"


def record_start(name: str, job_id: str, started: dict, handoff: str | None) -> dict:
    """Record a started worker's receipt on its job, with the handoff a relaunch started it on, else the launch's brief."""
    with state.edit(name) as data:
        job = data["jobs"][job_id]
        kept = {"handoff": handoff, "reads": {}, "undelivered": []} if handoff else {"brief": str(launch_brief(name, job_id))}
        job.update(started, **kept, ask=None, report=None, state="running", updated=state.stamp())
    return job


def launch_attempt(name: str, job: dict, attempt: str, kept: Path, brief: str, root: Path, workspace: str | None) -> dict:
    """Launch one attempt of a job's lane on a Run this terminal makes, since Orca binds a Run only to the terminal that made it and the coordinator moves terminals: on a fresh Sprite, or on the workspace's own Sprite in the checkout it holds; a try whose worker-start sent nothing reuses the Sprite it prepared, and the launcher copies this brief onto it."""
    kept.write_text(brief)
    run = orca.new_run(f"drive {name} {attempt}: {job['goal']}")
    return {**orca.launch(name, orca.lane_name(name, attempt), kept, root, run, job["route"], workspace), "run": run}


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


def started_job(data: dict, job_id: str) -> dict:
    job = remote_job(data, job_id)
    if not job.get("dispatch"):
        raise DriveError(f"{job_id} has not started; launch it with: drive job launch {job_id} <brief>")
    return job


def hand_over(args: argparse.Namespace, name: str, data: dict, session: str) -> int:
    """Hand a job another session's Agent holds to a fresh agent of this session: a handoff brief carrying the job's brief, what that agent left and the text, whose `Read your brief first:` line is the fresh agent's whole prompt, and the job marked handed to this session, so the drive records that agent on it."""
    job = data["jobs"][args.id]
    if refused := state.held_elsewhere(data, job["order"], state.session_role(data, session)):
        raise DriveError(f"{args.id}: refused, not handed over: {refused}")
    if not job.get("brief"):
        raise DriveError(f"{args.id} has no brief to hand over: write it with drive job brief {args.id}, then resume it again")
    kept = state.drive_dir(name) / "jobs" / f"{args.id}.handoff.md"
    kept.write_text(orca.agent_handoff(orca.brief_of(Path(job["brief"]).read_text()), job, args.text, None))
    with state.edit(name) as data:
        data["jobs"][args.id].update(handoff=str(kept), handed_to=session, updated=state.stamp())
    line = (
        f"Read your brief first: {kept}\n{args.id} runs as agent {job['agent']} of session {job['session']}, which this session cannot"
        f" SendMessage: start a fresh {job['kind']} agent here on the line above as its whole prompt, and the drive records {args.id} as this session's"
    )
    return emit(args, line, {"id": args.id, "handoff": str(kept), "handed_to": session})


def restart(args: argparse.Namespace, name: str, dispatch: str, end: orca.LaneEnd, text: str | None) -> int:
    """Start the job's next worker with a handoff, carrying the text when a resume brought one: in the terminal of a worker that finished while Orca holds it, on its Sprite or on this Mac, on that worker's Run, unless that Sprite refuses the tools a worker needs; else, on a Run this terminal makes, on a fresh Sprite, or in a fresh worktree on this Mac. Before a relaunch it saves to GitHub what the dead lane's Sprite holds that a fresh one would not get, and its handoff names the saved branches; when that Sprite cannot be read or the save fails, the relaunch runs on that Sprite instead, in the checkout the dead lane left, unless it refused a worker's tools, when it is kept and the relaunch runs on a fresh one. A relaunch on a fresh Sprite then destroys the dead lane's Sprite once it holds nothing on no remote. A finished worker whose terminal or worktree Orca no longer finds starts its next worker as a dead one's would. A relaunch an earlier restart of the same death started and never recorded is recorded from the ready receipt its attempt keeps, never launched twice."""
    data = state.load(name)
    job = data["jobs"][args.id]
    root = None if end.worktree else orca.checkout(name, data)
    left = None
    try:
        saved = None if end.worktree else orca.save(name, args.id, job, root)
    except orca.OrcaError as error:
        saved, left = None, str(error)
    continued = left if not end.unfit else None
    with state.edit(name) as data:
        job = data["jobs"][args.id]
        state.lane_died(job, dispatch, end.why, state.now())
        if saved:
            job["deaths"][-1]["saved"] = saved
    ended = job["deaths"][-1]
    attempt = f"{args.id}-{len(job['deaths']) + 1}"
    kept = state.drive_dir(name) / "jobs" / f"{attempt}.md"
    kept.parent.mkdir(exist_ok=True)
    brief = orca.vm_brief(name, args.id, orca.handoff(orca.brief_of(Path(job["brief"]).read_text()), ended, job, text, end, continued), job["route"])
    was = orca.place(job)
    if end.worktree:
        kept.write_text(brief)
        try:
            receipt = orca.start_after(name, orca.lane_name(name, attempt), kept, job, end)
        except orca.Unfit as error:
            return restart(args, name, dispatch, orca.LaneEnd(f"{end.why}, and {error}", end.branch, None, unfit=True), text)
        except orca.OrcaError as error:
            if why := orca.gone(error):
                return restart(args, name, dispatch, orca.LaneEnd(why, end.branch, None), text)
            raise
    else:
        receipt = orca.adopted(kept) or launch_attempt(name, job, attempt, kept, brief, root, job["workspace"] if continued else None)
    started = {field: receipt[field] for field in LAUNCHED}
    record_start(name, args.id, started, str(kept))
    carrying = "in a handoff carrying its brief, its branch, its last line, its report and its PRs"
    local = state.on_mac(job)
    went = "the message went to the job's next worker" if text is not None else "the job's next worker"
    if end.worktree:
        line = (
            f"{args.id}: {went}, started in its terminal {orca.ON_MAC if local else 'on the same Sprite'}"
            f" as dispatch {started['dispatch']} {carrying}; its lane {was} had finished ({end.why})"
        )
    else:
        source = "its worktree" if local else ended["workspace"]
        line = (
            f"{args.id}: {went}, relaunched {'in a fresh worktree ' + orca.ON_MAC if local else orca.place({**job, **started})}"
            f" as dispatch {started['dispatch']} {carrying}; its lane {was} was dead ({end.why}), and "
            + (
                f"what {source} held on no remote is saved: {ended['saved']}"
                if ended.get("saved")
                else f"it continues in the checkout the dead lane left on {source}, since what that Sprite holds on no remote could not be saved: {left}"
                if continued
                else f"{source} is kept, since what it holds on no remote could not be saved: {left}"
                if left
                else f"work not pushed from {source} is not carried over"
            )
        )
        if not left and started["workspace"] != ended["workspace"] and (released := release_sprite({**job, "workspace": ended["workspace"]}, root)):
            line += f"; {released}"
    how = f"started {orca.ON_MAC if local else 'on its Sprite'}" if end.worktree else "relaunched"
    return emit(args, line, {"id": args.id, "how": how, "ended": ended, "handoff": str(kept), **started})


def job_read(args: argparse.Namespace) -> int:
    """Answer one question a remote lane queued, as the watch's reader."""
    name = state.resolve(args.drive)
    said, why = watch.answer_read(name, args.id, args.message)
    return emit(args, why or said, {"id": args.id, "message": args.message, "answer": said, "why": why})


def job_replies(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    find(state.load(name)["jobs"], args.id, "job")
    kept = state.replies(name, args.id)
    text = "\n\n".join(f"{reply['created_at']} {reply['type']}: {reply['body'] or reply['subject']}" for reply in kept)
    return emit(args, text or f"{args.id} has sent nothing yet", kept)


def job_held(args: argparse.Namespace) -> int:
    from drive import view

    name = state.resolve(args.drive)
    job_id = args.id or os.environ.get(state.OWN_JOB_ENV)
    if not job_id:
        raise DriveError("job held needs the job: name it, or run it from a Sprite lane, whose own job it reads")
    data = state.load(name)
    find(data["jobs"], job_id, "job")
    cards = land.ship_held(data, job_id, view.live_cards(name))
    return emit(args, "\n".join(cards) or f"{job_id}: no open card of his holds its PR", {"id": job_id, "cards": cards})

def release_sprite(job: dict, root: Path) -> str | None:
    """Destroy the Sprite cc-remote still records for the job's lane once it holds nothing on no remote; what happened to it, or None when none is recorded."""
    if not orca.holds_sprite(job):
        return None
    try:
        return orca.release(job["workspace"], root) or None
    except orca.OrcaError as error:
        return f"{job['workspace']} is kept: {error}"


def job_prove(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    find(state.load(name)["jobs"], args.id, "job")
    line = install.prove(name, args.id, args.timeout)
    return emit(args, line, {"id": args.id, "line": line})


def job_end(args: argparse.Namespace) -> int:
    """Free a remote job's lane: its Sprite, destroyed once it holds nothing on no remote, or its terminal and clean worktree on this Mac. The job keeps the state its line set, with its waits, PRs and carried parts, so a waiting job's next event starts its next lane and nothing waiting on it is freed. A job still at work first has what its Sprite holds that its next lane would not get saved to GitHub, which that lane's handoff names; refused, ending nothing, when that Sprite cannot be read or the save fails."""
    name = state.resolve(args.drive)
    data = state.load(name)
    job = started_job(data, args.id)
    root = orca.checkout(name, data)
    saved = None
    if job["state"] not in state.FINISHED_JOB_STATES:
        try:
            saved = orca.save(name, args.id, job, root)
        except orca.OrcaError as error:
            raise DriveError(
                f"{args.id}: refused, nothing ended: it is {job['state']}, and what {job['workspace']} holds on no remote could not be saved"
                f" to GitHub for its next lane: {error}. End it again once that Sprite answers"
            ) from None
    reclaimed = orca.reclaim(job["run"], job["dispatch"], root)
    released = release_sprite(job, root)
    with state.edit(name) as data:
        ended = data["jobs"][args.id]
        state.lane_died(ended, job["dispatch"], "ended by drive job end", state.now())
        if saved:
            ended["deaths"][-1]["saved"] = saved
        ended.update(ask=None, updated=state.stamp())
        if ended["state"] in state.FINISHED_JOB_STATES:
            state.drop_carries(data, args.id)
    line = f"{args.id}'s lane ended; {args.id} stays {ended['state']}" + (f", and what its Sprite held on no remote is saved: {saved}" if saved else "")
    return emit(args, "\n".join(filter(None, [reclaimed, released, line])), {"id": args.id, "reclaim": reclaimed, "released": released, "state": ended["state"], "saved": saved})


def pr_add(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    key = state.pr_key(args.url)
    if args.no_tap and not args.why:
        raise DriveError("--no-tap needs --why <reason>: every PR waits on his tap unless it is a port, exempt or drive tooling outside Forge-AI")
    if args.no_tap and state.guarded(key):
        raise DriveError(UNLANDABLE.format(key=key, done="added"))
    pr = add_pr(name, key, args.job, github_pr(key), args.port_of, args.exempt, args.no_tap)
    merging = dict(merge_reported(name, args.job)).get(key)
    if merging is None:
        return emit(args, key, {"key": key, **pr})
    return emit(args, f"{key}: {merging}", {"key": key, **pr, "merging": merging})


def add_pr(name: str, key: str, job_id: str, read: dict, port_of: int | None = None, exempt: str | None = None, no_tap: bool = False) -> dict:
    """Register a PR on its job from its GitHub read, refused as `drive pr add` refuses, keeping the title it read, so a PR merged and proved before the watch's next read is named by it; the PR as registered, the job's first in place of each wait on its change. `drive pr add` runs it for a builder on this Mac, and the watch for each PR a remote lane's mail names, so a PR built on a Sprite lands as one built here does."""
    url = state.pr_url(key)
    refuse_an_unkinded_title(key, read, port_of)
    refuse_third_person(key, read)
    refuse_an_undrawn_show_me(name, key, read)
    if port_of:
        refuse_a_port_storing_data(key, port_of)
    known = state.load(name)
    added = find(known["jobs"], job_id, "job")
    author = read["author"]["login"]
    if why := state.foreign(key, author, [known["orders"][added["order"]]]):
        raise DriveError(f"{key}: refused, nothing added: {why}")
    if (exempt or no_tap) and (incidents := state.open_incidents(known, [added["order"], *added["carries"]])):
        refuse_an_exempt_revert(known, key, incidents[0], read, f"Add it waiting on his tap with drive pr add {url} --job {job_id}. ")
    with state.edit(name) as data:
        job = find(data["jobs"], job_id, "job")
        if data["orders"][job["order"]]["once"]:
            raise DriveError(f"refused, {job['order']} is a repair he asked to run once: run it, and put up no PR for it")
        wants_tap = state.guarded(key) or not tooling(data["orders"][job["order"]])
        pr = data["prs"].setdefault(key, state.new_pr(url, job_id, job["order"]) | {"wants_tap": wants_tap})
        pr.update(job=job_id, order=job["order"], author=author, title=read["title"])
        if port_of:
            pr.update(port_of=port_of, exempt="pure port", wants_tap=False)
        if exempt:
            pr["exempt"] = exempt
        if no_tap:
            pr["wants_tap"] = False
        if url not in job["prs"]:
            if not job["prs"]:
                state.wait_on_change(data, job_id, key)
            job["prs"].append(url)
    return pr


def merge_reported(name: str, job_id: str) -> list[tuple[str, str]]:
    """Start the lander on each open yasyf/* PR the drive tracks that a builder's taken READY or PUSHED names, so it merges the head its builder means to ship once the proof file the watch runs after it is kept; each PR with what its lander does or why it does not. `drive job report` and the watch run it on a report they take, and `drive pr add` on a PR registered after its report."""
    data = state.load(name)
    job = data["jobs"][job_id]
    if job["kind"] != "builder" or job["state"] != "waiting" or not state.REPORTED_LINE.match(job["said"] or ""):
        return []
    started = []
    for key in state.named_prs(data, job["order"], job["said"]):
        if not state.merges_untapped(key) or data["prs"][key]["state"] != "open" or data["prs"][key]["merged_sha"]:
            continue
        try:
            started.append((key, land.merge_ready(name, key, github_pr(key))))
        except DriveError as error:
            started.append((key, f"{land.NOT_MERGING}: {error}; drive pr add {state.pr_url(key)} --job {job_id} starts its lander again"))
    return started


def design_basis(text: str) -> str:
    if text == "none" or (text.startswith("asked:") and text.removeprefix("asked:").strip()):
        return text
    raise argparse.ArgumentTypeError("say asked:<his answer id or card permalink>, or none")


def his_link(text: str) -> str:
    if state.CARD_LINK.search(text):
        return text
    raise argparse.ArgumentTypeError(
        "name the Slack permalink of his message or of the card he tapped: a hold or approval the drive gave itself is never his,"
        " and one recorded as his drops with --no-tapped --why"
    )


def pr_set(args: argparse.Namespace) -> int:
    from drive import view

    name = state.resolve(args.drive)
    key = state.pr_key(args.url)
    if args.tap and args.port_of:
        raise DriveError("a port lands with no tap: pass --port-of alone")
    if args.tapped and not args.tap_source:
        raise DriveError(
            f"--tapped {args.tapped} needs --tap-source <the Slack permalink of his message or of the card he tapped>:"
            " only his own words are his tap, and a PR the drive holds itself carries a hold label or --waits on what it waits for"
        )
    if args.tap is False and not args.why:
        raise DriveError("--no-tap needs --why <reason>: his card for it, if one is open, is withdrawn showing that reason")
    if args.no_tapped and not args.why:
        raise DriveError("--no-tapped needs --why <reason>: why the hold the drive gave itself no longer stands, kept in the drive's events")
    known = state.load(name)
    if key not in known["prs"]:
        raise DriveError(f"no drive PR {key}: add it first with drive pr add {args.url} --job <j>, then set it")
    if why := state.foreign_pr(known, key):
        raise DriveError(f"{key}: refused, nothing set: {why}")
    before = known["prs"][key]
    after = dict(before)
    set_pr(after, args)
    if state.guarded(key) and (before["wants_tap"] or before.get("exempt")) and not (after["wants_tap"] or after.get("exempt")):
        raise DriveError(UNLANDABLE.format(key=key, done="set"))
    dropped = known["prs"][key]["tap"]
    if args.no_tapped:
        refuse_a_tap_of_his(name, key, dropped)
    order = known["prs"][key]["order"]
    if args.part:
        refuse_a_part_its_job_lacks(known, key, args.part)
    if args.serves:
        refuse_a_crossed_order(name, known, order, args.serves, f"{key} --serves")
    if (args.serves or args.design) and (refused := state.held_elsewhere(known, order, state.session_role(known, os.environ.get("CLAUDE_CODE_SESSION_ID")))):
        raise DriveError(f"{key}: refused, nothing set: {refused}")
    if args.serves:
        refuse_an_unwritten_rule(known, key)
        refuse_a_change_against_his_words(name, known, key, args)
    if args.serves and not args.design and (known["prs"][key]["wants_tap"] if args.tap is None else args.tap):
        raise DriveError(
            "--serves on a PR waiting on his tap needs --design asked:<his answer id or card permalink> for each design decision"
            " the PR makes, or --design none: a design he never answered goes back as a question, never on a card"
        )
    if (cards := [wait for wait in args.waits if state.card_wait(known, wait)]) and state.open_incidents(known, [order]):
        raise DriveError(
            f"--waits {', '.join(cards)}: {key} serves incident {order}, whose fix lands on the recommended option while his card"
            " stays open; a different pick of his starts a follow-up job on the same order"
        )
    if cards and state.IMMEDIATE_FIX in known["jobs"][known["prs"][key]["job"]]["parts"]:
        raise DriveError(
            f'--waits {", ".join(cards)}: {key} is {order}\'s immediate fix, which lands at once while his cards on the proper fix stay'
            " open, and the proper fix's PR replaces it"
        )
    check_waits(name, args.waits)
    try:
        shipped = {wait: shipping for wait in args.waits if (shipping := view.shipped_by(known, wait))}
    except github.GithubError as error:
        raise DriveError(f"{key}: could not read from GitHub whether each release it would wait on is out: {error}; run this again") from None
    args.waits = [wait for wait in args.waits if wait not in shipped]
    if args.port_of or args.tap or args.exempt == INCIDENT_EXEMPTION:
        read = github_pr(key)
        if args.port_of or args.tap:
            refuse_an_unkinded_title(key, read, args.port_of)
        if args.port_of:
            refuse_a_port_storing_data(key, args.port_of)
        if args.exempt == INCIDENT_EXEMPTION:
            refuse_an_exempt_revert(known, key, order, read)
    if args.tap or args.serves or args.design:
        refuse_an_undrawable_tap(key, args)
    served = ""
    with state.edit(name) as data:
        pr = find(data["prs"], key, "drive PR")
        if args.serves:
            served = f"\nserves {args.serves}, his words: {find(data['orders'], args.serves, 'order')['text']}"
        set_pr(pr, args)
        if args.part:
            served += f"\ndelivers {'; '.join(pr['parts'])}"
    untapped = ""
    if args.no_tapped:
        repo, number = key.split("#")
        state.append_event(
            name, "untapped", repo=repo, number=int(number), label=dropped["label"], source=dropped["source"], why=args.why, lane=state.heard_lane(data, order)
        )
        untapped = f"\ndropped the {dropped['label']} the drive gave itself ({dropped['source']}): {args.why}"
    withdrawn: list[str] = []
    if why := withdrawal(args):
        try:
            withdrawn = slack.withdraw_pr_card(pr["url"], why)
        except slack.SlackError as error:
            raise DriveError(f"{key}: withdrawing its open card failed, so his Approve still shows: {error}; run this again") from None
    corrected = correct_card(name, key, pr["url"], *redrawn) if (redrawn := redraw_words(args, dropped)) else ""
    out = "".join(f"\n{shipping}, so it is not a wait" for shipping in shipped.values())
    view.refresh_cards(name)
    found, source = view.stored_cards(name)
    line = render.pr_line(key, pr, view.pr_card_words(found if source == "live" else None, source, pr["url"])) + served + untapped + out + "".join(f"\nwithdrew his card {card}: {why}" for card in withdrawn) + corrected
    return emit(args, line, {"key": key, "withdrawn": withdrawn, **pr})


def refuse_a_part_its_job_lacks(data: dict, key: str, parts: list[str]) -> None:
    """Refuse a --part its job delivers on no order, since the PR's change is judged against the parts its job's PRs name."""
    job_id = data["prs"][key]["job"]
    job = data["jobs"][job_id]
    delivered = [*job["parts"], *(part for carried in job["carries"].values() for part in carried)]
    if unknown := [part for part in parts if part not in delivered]:
        listed = ", ".join(f'"{part}"' for part in delivered)
        raise DriveError(f"{key}: refused, nothing set: its job {job_id} delivers no part {', '.join(unknown)}; its parts are {listed}")


def refuse_a_change_against_his_words(name: str, data: dict, key: str, args: argparse.Namespace) -> None:
    """Refuse a PR serving an order while its change at its head, judged with its job's other PRs his card waits on, goes against the words of that order, its job's or any its job carries, or of the answers of his this --design cites, judged for the parts this --part names, a PR this --tap sets waiting on his tap judged too; one its builder's READY or the watch's check passed at this head is not judged again."""
    job_id = data["prs"][key]["job"]
    try:
        setting = ({"design": args.design} if args.design else {}) | ({"parts": args.part} if args.part else {})
        served.check(name, list(dict.fromkeys([*state.carded_prs(data, job_id), *([key] if args.tap else [])])), [args.serves], [], {key: setting})
    except served.Against as error:
        resume = f"drive job resume {job_id} with this refusal" if data["jobs"][job_id]["where"] == "remote" else f"a fresh builder on {job_id} briefed with this refusal"
        raise DriveError(f"{key}: refused, nothing set: {error}. Send it back by {resume}") from None


def refuse_an_unwritten_rule(data: dict, key: str) -> None:
    """Refuse a PR serving its order while a ruling of his that its job's brief binds, labelled code:<dir> for the code it rules on, is not written in this PR, his words verbatim, into an AGENTS.md at or above that directory, where every later change to that code reads it."""
    cwd = data["roles"]["coordinator"]["cwd"]
    owed = [
        (answer_id, ruled, tag.removeprefix(CODE_LABEL).strip("/") or ".")
        for answer_id in (data["jobs"][data["prs"][key]["job"]].get("brief_flags") or {}).get("ruling") or []
        for ruled in [settled.shown(answer_id, cwd)]
        for tag in ruled["tags"]
        if tag.startswith(CODE_LABEL)
    ]
    if not owed:
        return
    changed = served.files(served.change(key)["diff"])
    for answer_id, ruled, directory in owed:
        written = [
            line[1:].strip().lstrip(">").strip()
            for path, text in changed
            if Path(path).name == "AGENTS.md" and Path(directory).is_relative_to(Path(path).parent)
            for line in text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        said = settled.his(ruled["body"]).splitlines()[0]
        words = quoted[0][1:-1] if (quoted := limits.QUOTED.findall(said)) else said
        if " ".join(words.split()).casefold() not in " ".join(" ".join(written).split()).casefold():
            raise DriveError(
                f'{key}: refused, nothing set: his ruling {answer_id[:7]} on how {directory} must work, "{render.cut(words, 200)}", is not'
                f" written in this PR into an AGENTS.md at or above {directory}. Add his words verbatim with their date to the"
                f" AGENTS.md nearest {directory}, push, and set it again; his ruling 23ff059: \"And in general get in the habit of doing that\""
            )


def withdrawal(args: argparse.Namespace) -> str | None:
    """Why his open card for the PR is withdrawn: it no longer waits on his tap, or it is a port. A wait never withdraws it: his card is corrected in place."""
    if args.tap is False:
        return args.why
    if args.port_of:
        return f"port of #{args.port_of}, which lands with no tap"
    return None


def redraw_words(args: argparse.Namespace, dropped: dict | None) -> tuple[str, str, str] | None:
    """What this set stored that his open card for the PR draws, what that card does not show while its redraw fails, and what redrawing it in place shows: his tap recorded or dropped, which takes Approve and Hold off a card on which no PR asks his tap any more, or what the PR waits on, Approve and Hold kept; None when it stored nothing the card draws."""
    if args.tapped:
        return f"his {args.tapped} is stored", "does not show it yet", f"to show his {args.tapped}"
    if args.no_tapped:
        return f"the {dropped['label']} the drive gave itself is dropped", "still shows it", f"to drop the {dropped['label']} the drive gave itself"
    if args.waits or args.no_waits:
        return "its waits are stored", "does not name them yet", "Approve and Hold kept, to say what it waits on"
    return None


def correct_card(name: str, key: str, url: str, stored: str, unshown: str, shows: str) -> str:
    """Redraw in place each card of his carrying the PR that cc-slack still draws, so it shows what this set stored: an open one, or one whose Approve and Hold it took off once no PR on it asked his tap, neither replaced nor withdrawn; nothing new posts."""
    from drive import view

    view.refresh_cards(name)
    found = view.live_cards(name)
    drawn = [*found["pr_cards"], *(closed for closed in found["closed"] if not closed["replaced_by"] and not (closed["answer"] or {}).get("withdrawn"))] if found else []
    if found is not None and not view.card_for(drawn, url):
        return ""
    try:
        redrawing = slack.redraw_pr_cards(url)
    except slack.SlackError as error:
        raise DriveError(f"{key}: {stored}, but redrawing his open card in place failed, so it {unshown}: {error}; run this again") from None
    return f"\nredrawing in place the {redrawing} open card(s) of his carrying it, {shows}" if redrawing else ""


def check_waits(name: str, waits: list[str]) -> None:
    from drive import view

    data = state.load(name)
    for wait in waits:
        pr = release["pr"] if (release := state.RELEASE_WAIT.fullmatch(wait)) else wait
        if state.PR_KEY.fullmatch(pr) and pr not in data["prs"]:
            raise DriveError(f"--waits {wait}: the drive tracks no PR {pr}; drive pr add it to its job first")
    cards = [wait for wait in waits if state.card_wait(data, wait)]
    found = view.live_cards(name)
    if not all(view.card_open(wait, found) for wait in cards):
        view.refresh_cards(name)
        found = view.live_cards(name)
    for wait in cards:
        if not (WAITS_CARD.fullmatch(wait) and view.holds(data, wait, found)):
            raise DriveError(
                f"--waits {wait}: name a job of this drive, the Slack link or waits id of a card of his still open, a question"
                " card or a PR card still waiting on his tap, whose waits id is merge:<owner>:<repo>:<n>, a PR of this drive as"
                " <owner>/<repo>#<n>, held until it merges or closes, or release:<service>:<owner>/<repo>#<n>, the release of"
                " that service carrying that PR"
            )


def merged_waits(owner: dict, args: argparse.Namespace) -> list[str]:
    return list(dict.fromkeys([*([] if args.no_waits else owner["waits"]), *args.waits]))


def set_pr(pr: dict, args: argparse.Namespace) -> None:
    if args.serves:
        pr["serves"] = args.serves
    if args.design:
        pr["design"] = args.design
    if args.part:
        pr["parts"] = list(dict.fromkeys(args.part))
    if args.port_of:
        pr.update(port_of=args.port_of, exempt="pure port", wants_tap=False)
    if args.tap is not None:
        pr.update(wants_tap=args.tap, ready_said=None)
    if args.serves or args.design:
        pr["ready_said"] = None
    if args.fundamental:
        pr.update(fundamental=args.fundamental, tap=None, exempt=None, wants_tap=True, ready_said=None)
    if args.exempt:
        pr["exempt"] = args.exempt
    if args.no_exempt:
        pr["exempt"] = None
    if args.tapped:
        pr["tap"] = {"label": args.tapped, "at": state.stamp(), "head": pr["head"], "source": args.tap_source}
        pr["fundamental"] = None
    if args.no_tapped:
        pr["tap"] = None
    pr["waits"] = merged_waits(pr, args)
    pr["updated"] = state.stamp()


def refuse_an_undrawable_tap(key: str, args: argparse.Namespace) -> None:
    from drive import card

    known = card.drive_prs()
    name, entry = find(known, key, "drive PR")
    after = dict(entry)
    set_pr(after, args)
    if not after["wants_tap"]:
        return
    known[key] = (name, after)
    members = card.group_of(known, key)
    if reason := card.waiting(known, members):
        raise DriveError(f"{key}: {reason}")


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


def refuse_an_undrawn_show_me(name: str, key: str, read: dict) -> None:
    """Each fence of a PR's Show me draws as his card will draw it, through the same cache, since a card whose picture does not draw shows him a Show me with nothing in it."""
    from drive import card

    for kind, source, _ in picture.sources(card.sections(card.body_of(read)).get("show me", "")):
        if kind != "image":
            picture.shot(state.drive_dir(name) / "pictures", "show-me", kind, source, f"{key}: refused, nothing added: its Show me {kind}")


def refuse_an_unkinded_title(key: str, read: dict, port_of: int | None) -> None:
    """A PR's title opens with the kind his card names it by, Fix:, New:, Cleanup: or Port:, or a prefix his card reads as one, and a port's title opens Port:."""
    from drive import card

    kind, plain = card.kind_and_title(read["title"])
    if kind != "Change" and (kind == "Port" or not port_of):
        return
    repo, number = key.split("#")
    opening, retitled = (f"Port:, since it is a port of #{port_of}", f"Port: {plain}") if port_of else ("Fix:, New:, Cleanup: or Port:", f"<Fix|New|Cleanup|Port>: {plain}")
    title = read["title"]
    raise DriveError(
        f'{key}: refused, its title "{title}" does not open {opening}, the kind of change his card names it by:'
        f' retitle it with gh pr edit {number} -R {repo} --title "{retitled}", then run this again'
    )


def refuse_third_person(key: str, read: dict) -> None:
    """The sections of a PR's body his card and dashboard show him speak to him as you, never of him as he or his."""
    from drive import card

    shown = {heading: text for heading, text in card.sections(card.body_of(read)).items() if heading in card.SHOWN_HEADINGS}
    if not (found := limits.third_person(shown)):
        return
    repo, number = key.split("#")
    raise DriveError(
        f"{key}: refused, nothing added: its card shows him these sections, which speak of him as he or his: {'; '.join(found)}."
        f' Say you and your instead, as in "Your Nov 11 words narrowed it", edit the body with gh pr edit {number} -R {repo} --body-file <file>, then run this again'
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


def tap_verdict(pr: dict | None) -> tuple[bool, str]:
    if pr is None:
        return True, "not a drive PR"
    if pr["hold_label"]:
        return False, "held by the drive with the hold label"
    if pr.get("exempt"):
        return True, f"exempt: {pr['exempt']}"
    tap = pr.get("tap")
    if tap and tap["label"] == "Approve":
        return True, f"approved by him at {render.when(tap['at'])}"
    if tap:
        return False, f"held by him at {render.when(tap['at'])}"
    if pr.get("fundamental"):
        return False, f"no tap since it changed: {pr['fundamental']}"
    return False, "no tap from him yet"


def pr_tapped(args: argparse.Namespace) -> int:
    key = state.pr_key(args.url)
    pr = state.load(state.resolve(args.drive))["prs"].get(key)
    may_land, why = tap_verdict(pr)
    emit(args, f"{key}: {why}", {"key": key, "may_land": may_land, "why": why})
    return 0 if may_land else 1


def pr_land(args: argparse.Namespace) -> int:
    keys = [state.pr_key(url) for url in args.urls]
    name = state.resolve(args.drive)
    outcome = land.record(name, keys, args.outcome, args.below) if args.outcome else land.run(name, keys, args.dry_run)
    return emit(args, f"{', '.join(keys)}: {outcome}", {"keys": keys, "outcome": outcome})


def pr_check(args: argparse.Namespace) -> int:
    """Judge one PR against his words as the watch's check."""
    key, name = state.pr_key(args.url), state.resolve(args.drive)
    outcome = served.verdict(name, key)
    return emit(args, f"{key}: {outcome}", {"key": key, "outcome": outcome})


def answer(args: argparse.Namespace) -> int:
    """Record an answer or ruling of his against the cards asking him and PRs not yet live that it could change, those of the job and order of the card it answers, those its words name and each other question card Jev reads it as changing, and the jobs waiting on their orders: refuse until each card and PR is marked standing, settled, superseded or reopened by it and each job sent his words or untouched, show his answer on each card it settles, redraw each it settles in part to the ASK its job keeps for what is left, settle each superseded card in place, withdraw each card carrying a reopened PR until its builder's READY says how his words are met, and close on GitHub an open PR superseded outright; any other card or PR still asking him may be marked too."""
    from drive import superseded, view

    name = state.resolve(args.drive)
    found = view.live_cards(name)
    if found is None:
        raise DriveError("his cards could not be read, so nothing can say what this answer leaves asking him; the watch rereads them every minute, run this again then")
    data = state.load(name)
    asked = superseded.asking(data, found)
    if args.id not in data["answers"] and (unowed := rewrite_refusal(data, args)):
        raise DriveError(unowed)
    cited = args.id
    if args.id is None:
        if not (args.title and args.words and args.link):
            raise DriveError('name his answer\'s cc-notes id, or pass --title "<the question, verbatim>" with --words and --link to record it in cc-notes first')
        if unsaid := settled.unsaid(args.words, args.link, found):
            raise DriveError(f"refused, nothing recorded: {unsaid}")
        changes = settled.changed(args.title, args.words, asked)
        labels = ["scope:durable", f"drive:{name}", *settled.kind(args.words), *args.label]
        args.id = settled.recorded(args.title, args.words, labels, data["roles"]["coordinator"]["cwd"])
    marking = args.stands or args.settled or args.superseded or args.reopened or args.others_stand or args.sent or args.untouched
    if args.id not in data["answers"]:
        if marking:
            raise DriveError(
                f"{args.id} is not recorded yet: run it with --words and --link alone first, which lists each card still asking him"
                " and each PR not yet live, and mark them once you have read it"
            )
        if not (args.words and args.link):
            raise DriveError(f'{args.id} is not recorded yet: pass --words "<his words, verbatim>" and --link <the permalink of his message or the card he tapped>')
        if cited:
            shown = settled.shown(cited, data["roles"]["coordinator"]["cwd"])
            if unsaid := settled.unsaid(args.words, args.link, found) or settled.unsaid(settled.his(shown["body"]), args.link, found):
                raise DriveError(f"{cited}: refused, nothing recorded: {unsaid}")
            changes = settled.changed(shown["title"], args.words, asked)
        with state.edit(name) as data:
            data["answers"][args.id] = superseded.record(data, args.words, args.link, asked, changes, state.stamp())
    done = []
    if not marking:
        with state.edit(name) as data:
            ended = superseded.answer_cards(data, args.id, data["answers"][args.id])
        done = superseded.follow(name, ended)
    kept = data["answers"][args.id]
    open_keys = superseded.unmarked(kept, asked)
    named = [*args.stands, *(key for key, _ in [*args.settled, *args.superseded, *args.reopened])]
    told = [*args.sent, *args.untouched]
    if twice := sorted({key for key in [*named, *told] if [*named, *told].count(key) > 1}):
        raise DriveError(f"{', '.join(twice)}: marked twice; mark each card or PR once, standing, settled, superseded or reopened, and each job once, sent or untouched")
    if stray := [key for key in named if key not in superseded.markable(kept, asked)]:
        raise DriveError(f"{', '.join(stray)}: not a card or PR that {args.id} has not marked\n{superseded.listing(args.id, kept, open_keys, asked, data)}")
    hearing = [job_id for key, _ in args.settled if (job_id := asked[key]["job"]) and data["jobs"][job_id]["state"] not in state.FINISHED_JOB_STATES]
    if stray := [job_id for job_id in told if job_id not in [*superseded.unheard(data, kept), *hearing]]:
        raise DriveError(
            f"{', '.join(stray)}: not a job waiting on an order {args.id} touches that is unmarked\n{superseded.listing(args.id, kept, open_keys, asked, data)}"
        )
    refusals = [
        *(superseded.settled_refusal(data, kept, key, asked[key], file) for key, file in args.settled),
        superseded.stack_refusal(asked, [key for key, _ in args.superseded]),
        *(superseded.fold_refusal(data, key, asked[key], into) for key, into in args.superseded if into),
        *(superseded.reopen_refusal(data, key, asked[key], by) for key, by in args.reopened),
        args.others_stand and superseded.others_refusal(args.id, kept, asked, named, data),
    ]
    if why := next((refusal for refusal in refusals if refusal), None):
        raise DriveError(why)
    with state.edit(name) as data:
        data["answers"][args.id]["cards"].update({key: {"mark": "stands"} for key in args.stands})
        data["answers"][args.id]["jobs"].update({job_id: {"mark": "sent"} for job_id in args.sent} | {job_id: {"mark": "untouched"} for job_id in args.untouched})
    cleared: set[str] = set()
    try:
        done += [settle_card(name, args.id, kept, key, asked[key], file) for key, file in args.settled]
        for mark, key, job_id in [*(("superseded", *pair) for pair in args.superseded), *(("reopened", *pair) for pair in args.reopened)]:
            card = asked[key]["card"]
            if mark == "reopened":
                with state.edit(name) as data:
                    data["answers"][args.id]["cards"][key] = {"mark": mark, "job": job_id, "card": card}
                done += [*rework_prs(name, [state.pr_url(key)], args.id, kept["words"]), f"{key} reopened by his answer {args.id}: {job_id} reworks it to his words, so brief {job_id} with them"]
                continue
            if card and card not in cleared:
                ended_on(name, args.id, kept, key, card, "superseded")
                cleared.add(card)
            if superseded.stops_open_pr(data, key, job_id):
                superseded.close(key)
            with state.edit(name) as data:
                if card:
                    done.append(f"{key}: its card {card} now shows his words and a link to them, with no buttons")
                data["answers"][args.id]["cards"][key] = {"mark": mark, "job": asked[key]["job"], "into": job_id, "card": card}
                done += [*([] if card else [f"{key}: no card was drawn for it"]), *superseded.supersede(data, args.id, key, asked[key], job_id)]
    finally:
        if args.settled or args.superseded or args.reopened:
            view.refresh_cards(name)
    with state.edit(name) as data:
        kept = data["answers"][args.id]
        if args.others_stand:
            kept["cards"].update({key: {"mark": "stands"} for key in superseded.unmarked(kept, asked)})
    if (left := superseded.unmarked(kept, asked)) or superseded.unheard(data, kept):
        raise DriveError("\n".join([*done, superseded.listing(args.id, kept, left, asked, data)]))
    marked = [mark["mark"] for mark in kept["cards"].values() if mark]
    heard = [mark["mark"] for mark in kept["jobs"].values() if mark]
    settles = "".join(f", {marked.count(mark)} {mark}" for mark in ("settled", "redrawn") if mark in marked)
    line = f"his answer {args.id} recorded: {marked.count('stands')} standing{settles}, {marked.count('superseded')} superseded, {marked.count('reopened')} reopened by it"
    if heard:
        line += f"; {heard.count('sent')} waiting {'job' if heard.count('sent') == 1 else 'jobs'} sent his words, {heard.count('untouched')} untouched"
    return emit(args, "\n".join([line, *done]), {"id": args.id, **kept})


def ended_on(name: str, answer_id: str, kept: dict, key: str, card: str, how: str) -> None:
    """End, answered or superseded by his answer, each open question drawn on the card a mark names, and its card follows, showing his words with no buttons; a card no question of the drive is drawn on, one asked outside any job, is closed in place."""
    from drive import superseded, view

    with state.edit(name) as data:
        closed = state.new_close(how, kept["words"], kept["link"], answer=answer_id)
        drawn = view.drawn_on(data, key, card)
        ended = [each for job_id, question in drawn if question["closed"] is None for each in superseded.end(data, job_id, question, closed)]
    if drawn:
        superseded.follow(name, ended)
    else:
        superseded.settle(card, kept, settled=how == "answered")


def settle_card(name: str, answer_id: str, kept: dict, key: str, entry: dict, file: Path | None) -> str:
    """Show his answer in place on the question card it decides, settling its job's question, or redraw the card it decides in part to the ASK its job keeps asking what is left, through the ask record that redraws a card under its waits id; either way the card's job, while live, waits to hear his words. What was done, one line."""
    from drive import tools

    job_id, card = entry["job"], entry["card"]
    if file:
        attrs = {"job": job_id, "ask": str(file), "waits": key} | settled.incident_marks([name], {"job": job_id, "ask": str(file)})
        tools.sent(name, job_id, [{"kind": "ask", "to": [f"communicator@{name}"], "attr": attrs, "path": str(file)}])
    else:
        ended_on(name, answer_id, kept, key, card, "answered")
    with state.edit(name) as data:
        job = data["jobs"][job_id]
        if file:
            keep_waits(job, file, key)
        data["answers"][answer_id]["cards"][key] = {"mark": "redrawn" if file else "settled", "job": job_id, "card": card, **({"ask": str(file)} if file else {})}
        if job["state"] not in state.FINISHED_JOB_STATES:
            data["answers"][answer_id]["jobs"].setdefault(job_id, None)
    if file:
        return f"{key}: its card {card} is redrawn in place to {file}, asking only what his answer {answer_id} leaves open, by the ask record sent to communicator@{name}"
    return f"{key}: its card {card} now shows his answer {answer_id} with no buttons, settling {job_id}'s question, so {job_id} builds on his words"


def rewrite_refusal(data: dict, args: argparse.Namespace) -> str | None:
    """Why a ruling of his may not be recorded yet: its skill rewrite is owed the moment it is, so an open "Drive tooling:" order, the meta session's, carries it, and the watch chases that order until its builder is done."""
    labels = args.label if args.id is None else settled.shown(args.id, data["roles"]["coordinator"]["cwd"])["tags"]
    if RULING not in labels:
        return None
    order = data["orders"].get(args.rewrite or "")
    if order and order["text"].startswith(state.TOOLING) and order["state"] not in state.CLOSED_ORDER_STATES:
        return None
    return (
        "refused, nothing recorded: a ruling's skill rewrite is owed the moment the ruling is recorded, so name the"
        " meta session's open order carrying it with --rewrite <o>, adding it first with"
        f" drive order add '{state.TOOLING} rewrite: \"<his words, verbatim>\"' --title \"<a few words>\" when none holds it, which the watch chases until"
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
    if args.repeats or args.part:
        refuse_an_unheld_repeat(name, state.load(name), args.repeats, args.part)
    return emit(args, "away" if args.away else "heard", watch.hear(name, state.now(), args.away))


def handler(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    if extra := agents.beyond_brief(args.brief):
        raise DriveError(
            f"refused: a handler's brief is its lines alone, every line under one of {', '.join(render.HANDLER_LABELS)};"
            f" `{render.cut(extra, 44)}` is more: a rule in a brief overrides its agent definition, so drop the line"
        )
    if re.search(rf"^{re.escape(agents.HIS_WORDS)}:", args.brief, re.MULTILINE):
        watch.hear(name, state.now(), args.away)
    handler_id = agents.handle(name, args.brief)
    return emit(args, f"started {handler_id}", {"handler": handler_id})


def term(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    with state.edit(name) as data:
        data["stand_ins"][args.stand_in] = args.term
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


def orca_place(held: dict, hidden: Callable[[Path], str]) -> str:
    """Where he opens a role's session in Orca: a switch command only once Orca proves it lands on that session."""
    if not (handle := held.get("terminal")):
        return "in no Orca terminal"
    try:
        terminal = orca.terminal(handle)
        if terminal["orphaned"]:
            return hidden(orca.main_checkout(terminal["worktreePath"]))
        shown = orca.holds_session(terminal, held["session"])
    except orca.OrcaError as error:
        return f"Orca terminal {handle} unproven: {error}"
    if shown:
        return f"open it with: orca terminal switch --terminal {handle}"
    return "not visible in Orca until restarted into a terminal"


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


def drive_key(data: dict, key: str) -> str:
    """The order, job or PR an id names; a bare number names the drive's one PR of that number."""
    if "#" in key or "/pull/" in key:
        return state.pr_key(key)
    if not key.isdigit():
        return key
    numbered = [pr for pr in data["prs"] if pr.endswith(f"#{key}")]
    if len(numbered) > 1:
        raise DriveError(f"{key} is the number of {', '.join(numbered)}; name one as owner/repo#{key}")
    return numbered[0] if numbered else key


def only(data: dict, ids: list[str]) -> None:
    """Narrow data, never saved, to the orders the ids name, directly or through one of their jobs or PRs."""
    keys = {drive_key(data, key) for key in ids}
    if unknown := keys - set(data["orders"]) - set(data["jobs"]) - set(data["prs"]):
        raise DriveError(f"no order, job or PR {', '.join(sorted(unknown))}; name {ID_FORMS}")
    orders = {key if key in data["orders"] else (data["jobs"].get(key) or data["prs"][key])["order"] for key in keys}
    data["orders"] = {key: order for key, order in data["orders"].items() if key in orders}
    jobs = {job_id for order in data["orders"].values() for job_id in order["jobs"]}
    data["jobs"] = {key: job for key, job in data["jobs"].items() if key in jobs or job["order"] in orders}
    data["prs"] = {key: pr for key, pr in data["prs"].items() if pr["order"] in orders}


def status(args: argparse.Namespace) -> int:
    from drive import view

    name = state.resolve(args.drive)
    data = state.load(name)
    view.refresh_cards(name)
    found, source = view.stored_cards(name)
    found = found if source == "live" else None
    for question in (question for job in data["jobs"].values() for question in job["questions"]):
        question["card_state"] = view.card_words(found, source, question["card"] or question["waits"])
    for pr in data["prs"].values():
        pr["card_state"] = view.pr_card_words(found, source, pr["url"]) if pr["state"] == "open" else None
    for job_id, in_line in place.positions(data).items():
        data["jobs"][job_id]["in_line"] = in_line
    corrected = agents.corrections(name, data["held"])
    for seq, mark in data["held"].items():
        mark["corrections"] = corrected.get(seq, [])
    if args.ids:
        only(data, args.ids)
    for project, held in data["subcoordinators"].items():
        held["watch"] = watch.holder(state.pid_path(name, project))
    seen = {role: with_build(orca_place(held, name_show), held) for role, held in state.bound_sessions(data).items()}
    named = bool(args.ids)
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    project = state.session_project(data, session)
    runs = (lambda order: state.runs(data, order, project)) if state.session_role(data, session) == "coordinator" else None
    return emit(args, render.status_text(data, seen, named, runs), render.status_data(data, seen, named))


def with_build(place: str, held: dict) -> str:
    path = watch.config_dir() / "projects" / watch.NOT_SLUG.sub("-", held["cwd"]) / f"{held['session']}.jsonl"
    note = watch.unknown_model(*watch.running_build(path), CLAUDE_BUILDS) if path.exists() else None
    return f"{place}; {note}" if note else place


def context(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    data = state.load(name)
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    role = args.role or state.session_role(data, session)
    if role is None:
        raise DriveError(f"this session holds no role in drive {name}; pass --role")
    print(render.context(data, role, None if args.role else state.session_project(data, session)))
    return 0


def manual(args: argparse.Namespace) -> int:
    if not args.page:
        print("\n".join(["drive manual <page> prints one of these pages:", *(f"- {page}" for page in manual_pages())]))
        return 0
    text = (PAGES / f"{args.page}.md").read_text()
    if not args.row:
        sys.stdout.write(text)
        return 0
    rows = [line for line in text.splitlines() if line.startswith("| ") and args.row.casefold() in line.split("|")[1].casefold()]
    if not rows:
        raise DriveError(f"no row of drive manual {args.page} names {args.row}; drive manual {args.page} prints the page")
    print("\n".join(rows))
    return 0


def view_command(args: argparse.Namespace) -> int:
    from drive import dashboard, page, view

    name = state.resolve(args.drive)
    view.refresh_cards(name)
    model = view.read(name, state.now())
    return emit(args, page.render(model, dashboard.link(name)), model)


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


def watch_events(args: argparse.Namespace) -> int:
    """Run the watch with --loop; else start it as its own process unless one runs, and point the cci channel of the coordinator session it posts to at its records when this is that session. A project's watch starts only from the session of that project's sub-coordinator, and a drive under LONG_RUNNING_STATE gets neither the process nor the channel."""
    if args.loop:
        return watch.main(args.drive, args.project)
    name = state.resolve(args.drive)
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    data = state.load(name)
    project = state.session_project(data, session)
    if args.project and project != args.project:
        bound = (data["subcoordinators"].get(args.project) or {}).get("session") or "none bound"
        raise DriveError(
            f"--project {args.project}: only the sub-coordinator for {args.project} starts its watch, from its own session ({bound});"
            f" bind one with drive start {name} --role coordinator --project {shlex.quote(args.project)}"
        )
    print(watch.ensure(name, args.project))
    if project == args.project and state.session_role(data, session) == "coordinator" and not state.scratch():
        try:
            watch.listen(name, session, args.project)
        except reads.ReadError as error:
            raise DriveError(f"the watch runs, but the coordinator's cci channel was not pointed at it: {error}") from None
    return 0


def card_command(args: argparse.Namespace) -> int:
    from drive import card

    return card.main(sys.stdin, sys.stdout)


def mcp_command(args: argparse.Namespace) -> int:
    from drive import mcp

    if args.list:
        print(json.dumps(mcp.tool_list()))
        return 0
    return mcp.serve(sys.stdin, sys.stdout)


def tool_command(args: argparse.Namespace) -> int:
    from drive import mcp, tools

    if args.help:
        print(tools.described(next(tool for tool in mcp.tool_list() if tool["name"] == args.tool)))
        return 0
    shapes = next(tool for tool in mcp.tool_list() if tool["name"] == args.tool)["inputSchema"]["properties"]
    try:
        arguments = tools.parsed_arguments(json.load(sys.stdin), shapes)
        print(tools.run(args.tool, state.resolve(arguments.pop("drive", None)), arguments))
    except reads.ReadError as error:
        raise DriveError(str(error)) from None
    return 0


def plan_search(args: argparse.Namespace) -> int:
    """Print the sections of the plans the coordinator's checkout keeps in cc-notes that carry the most of the words, each under its plan and headings."""
    words = " ".join(args.words)
    checkout_dir = Path(state.load(state.resolve(args.drive))["roles"]["coordinator"]["cwd"])
    try:
        found = reads.plan_sections(words, checkout_dir)
    except reads.ReadError as error:
        raise DriveError(f"plan search: {error}") from None
    said = "\n\n".join(
        " › ".join(filter(None, [f"== {section['title']} (plan {section['plan']}, {section['status']})", section["heading"]])) + f"\n{section['text']}"
        for section in found
    )
    return emit(args, said or f'no plan the coordinator keeps in cc-notes has a section on "{words}"', found)


def break_glass(args: argparse.Namespace) -> int:
    """Run one command under the forge/break-glass grant for a stack, started in the job's or order's name with its reason, holding only the stack's env's admin credentials, minted on this Mac's machine profile to end no later than the grant, and stopped with every process left in its group by the grant's end or when drive is stopped: its output and exit status are the verb's, and nobody signs in."""
    name = state.resolve(args.drive)
    data = state.load(name)
    records, label = (data["jobs"], "job") if JOB_ID.fullmatch(args.id) else (data["orders"], "order")
    find(records, args.id, label)
    try:
        with reads.break_glass(args.stack, args.reason, f"{name} {args.id}", Path(data["roles"]["coordinator"]["cwd"])) as granted:
            print(f"break-glass grant {granted.link} passed; running {args.command[0]} {reads.held(granted)}", file=sys.stderr, flush=True)
            return reads.run_granted(args.command, granted)
    except reads.ReadError as error:
        raise DriveError(f"break-glass: {error}") from None


def move(args: argparse.Namespace) -> int:
    """Step 1 of moving a role's session into a fresh Orca terminal: his word naming the move, recorded on the role; steps 2 to 4 printed, run by hand."""
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
    with state.edit(name) as data:
        held = find(data["roles"], args.role, "role")
        held["move_word"] = args.his_word
    lanes = {role: lane for lane, role in state.LANE_ROLES.items()}
    mover = lanes[next(role for role in state.ROLES if role != args.role)]
    session, terminal = held["session"], held["terminal"]
    ended = (
        f"orca terminal send --terminal {terminal} --text /exit --enter"
        if terminal
        else f"tmux send-keys -t <pane> /exit Enter under tmux, its pane the one tmux list-panes -a lists with its claude line, or claude stop {session} for a claude --bg session"
    )
    pids = orca.claude_pids(session)
    gone = (
        f"until ps -p {' '.join(map(str, pids))} lists nothing"
        if pids
        else f"none: no ~/.claude/sessions/<pid>.json names {session}, so its process has exited"
    )
    steps = [
        f"1. his word {args.his_word} recorded on the {args.role}",
        (
            f'2. cci post --lane {mover} --kind ask --to {lanes[args.role]}@{name} --text "Moving you into an Orca terminal on his word:'
            " run cc-slack unfollow for each of your Slack watches, cci unsubscribe, stop your drive watch Monitor if you run one, reply"
            ' released, and take no more work." and wait for its reply'
        ),
        f"3. {ended}",
        f"4. wait {gone}",
        f"then steps 5 to 7 of drive manual migration, Into an Orca terminal: create the terminal resuming {session} in {held['cwd']}",
    ]
    return emit(args, "\n".join(steps), {"role": args.role, "move_word": args.his_word, "steps": steps})


def install_plugin(args: argparse.Namespace) -> int:
    return install.main(args.plugin, args.sha, args.repo, args.timeout, args.drive)


def reload_plugins(args: argparse.Namespace) -> int:
    return install.reload_main()


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


def parser() -> argparse.ArgumentParser:
    drive_opt = argparse.ArgumentParser(add_help=False)
    drive_opt.add_argument("--drive", help="the drive to act on")
    json_opt = argparse.ArgumentParser(add_help=False)
    json_opt.add_argument("--json", action="store_true", help="print JSON")
    both = [drive_opt, json_opt]
    waits_opt = argparse.ArgumentParser(add_help=False)
    waits_opt.add_argument(
        "--waits",
        action="append",
        default=[],
        metavar="CARD|JOB|PR|RELEASE",
        help=(
            "a design card or PR card of his still open, by its Slack link or waits id, a PR the drive tracks as <owner>/<repo>#<n>,"
            " release:<service>:<owner>/<repo>#<n>, the next release of that service carrying that PR, or for a PR a job, it waits on;"
            " repeat for each. The watch drops each as he answers or taps it, the PR merges or closes, the job ends or GitHub shows the"
            " newest release of that service holds the PR's merge commit, and says FREE once when the last is gone, which wakes the"
            " coordinator. On a PR it never withdraws his open card: the card is redrawn in place, Approve and Hold kept, to say"
            " what it waits on, and a release GitHub shows already out is not stored"
        ),
    )
    waits_opt.add_argument("--no-waits", action="store_true", help="drop every wait it carries before adding any --waits")

    root = argparse.ArgumentParser(prog="drive", description="State and events of a long-running drive.")
    root.add_argument("--version", action="version", version=f"drive {state.VERSION}")
    verbs = root.add_subparsers(dest="verb", required=True)

    verb = verbs.add_parser("start", parents=[json_opt], help="create the drive or bind one of its two roles to this session")
    verb.add_argument("name")
    verb.add_argument(
        "--role", required=True, choices=state.ROLES, help="communicator talks to him; coordinator runs his orders and jobs; meta holds drive tooling: its fixes, skill rewrites and installs"
    )
    verb.add_argument("--channel", help="his DM channel id")
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
    verb.add_argument("words", help='his words, verbatim, or a "Drive tooling:" order\'s own, which `cc-slack check-post` must pass, since its PR card quotes them')
    verb.add_argument("--from", dest="source", default="terminal", help=FROM_HELP)
    verb.add_argument(
        "--thread",
        help=(
            f"{THREAD_HELP}; default: the thread the --from message is in. An order in the thread of his open PR card names"
            " that card and leaves it up, since its stack can meet his words already: only --rework withdraws one"
        ),
    )
    verb.add_argument("--rework", action="append", default=[], metavar="PR", help=REWORK_HELP)
    verb.add_argument("--due", help="the time his ack promised a line by; the watch says DUE from then until a line is in the thread")
    verb.add_argument("--parts", nargs="+", type=str.strip, metavar="PART", help="each deliverable his words name; default: one, the whole order")
    verb.add_argument(
        "--answers",
        nargs="+",
        type=str.strip,
        metavar="PART",
        help=(
            "each part that is words he reads, such as his question's answer, a retro or a status, which a post delivers by giving it"
            f" whatever it says; every other part is work, which a post delivers only once it says the work is done. {ANSWER_KIND}"
        ),
    )
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
        help="an incident order on the same failing service as this open incident order: it takes that order's record and starts no job, since that incident's owner carries it",
    )
    verb.add_argument(
        "--kind",
        choices=state.ORDER_KINDS,
        help=(
            "answer: one reader lane researches it and answers him, with no other job; build: an Opus reader keeps its"
            " Researched: block before any builder, designer or codex lane starts, except the immediate fix's and a follow-up part's"
            " designer, which start at once, and their briefs name it."
            " An incident order is a build"
        ),
    )
    verb.add_argument(
        "--complex",
        action=argparse.BooleanOptionalAction,
        help="more than a one-fact lookup: its answer lane runs on Opus, never Sonnet; --no-complex marks a one-fact lookup, and an answer order asking several questions with neither starts no Sonnet lane",
    )
    verb.add_argument(
        "--options-only", action="store_true", help="he asked for options only: no builder or codex job starts on the proper fix until he picks, only a reader or designer, while its immediate fix part's starts at once"
    )
    verb.add_argument("--once", action="store_true", help="a repair he asked to run once: its job runs it and puts up no PR")
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
    verb.set_defaults(run=order_add)
    verb = order.add_parser("set", parents=both)
    verb.add_argument("id", help="the order")
    verb.add_argument(
        "--words",
        help='a "Drive tooling:" order\'s new words, which `cc-slack check-post` must pass, since its PR card quotes them; an order of his keeps his words verbatim',
    )
    verb.add_argument(
        "--state",
        choices=state.ORDER_STATES,
        help="done or dropped refuses while a promised line is not posted, and done while the bug report it fixes has not heard it is fixed",
    )
    verb.add_argument(
        "--due", help="when he hears next, owed from the next line in the order's thread; on an order with no thread, the drive's own time, said once"
    )
    verb.add_argument("--note", help="where the order stands, in a few words")
    verb.add_argument("--proof", help="the check that proves it done: its link, its saved file or its pasted output, never a one-line claim of what it showed")
    verb.add_argument("--from", dest="source", help=f"{FROM_HELP}; the order's thread stays unless --thread names another")
    verb.add_argument("--thread", help=f"{THREAD_HELP}, naming each open PR card of his rooted there as order add --thread does")
    verb.add_argument("--rework", action="append", default=[], metavar="PR", help=REWORK_HELP)
    verb.add_argument("--lined", action="store_true", help="a line just went in its thread")
    verb.add_argument(
        "--withdrawn", metavar="LINK", help="his Slack message calling off the promised time; the order then owes no line for it"
    )
    verb.add_argument("--parts", nargs="+", type=str.strip, metavar="PART", help="the order's parts, replacing its list")
    verb.add_argument(
        "--answers",
        nargs="*",
        type=str.strip,
        metavar="PART",
        help=f"the parts that are words he reads, as drive order add takes them, replacing which are: every part it leaves out is work, and none named makes every part work. {ANSWER_KIND}",
    )
    reported = verb.add_mutually_exclusive_group()
    reported.add_argument("--reporter", metavar="LINK", help=REPORTER_HELP)
    reported.add_argument("--no-reporter", metavar="LINK", help="his Slack message saying not to tell the bug's reporter; the order then closes done with no fix-live note")
    verb.add_argument("--incident", action=argparse.BooleanOptionalAction, help="mark or unmark it as an incident order")
    verb.add_argument("--alert", dest="alerts", action="append", default=[], metavar="LINK", help=f"added to its alerts: {ALERT_HELP}")
    verb.add_argument(
        "--kind",
        choices=state.ORDER_KINDS,
        help="sort it again: an answer whose work turns out to need a build becomes a build, naming each open PR card of his in its thread as order add --thread does",
    )
    verb.add_argument("--complex", action=argparse.BooleanOptionalAction, help="mark or unmark it as more than a one-fact lookup")
    verb.add_argument(
        "--options-only", action=argparse.BooleanOptionalAction, help="limit it to options, so no builder or codex job starts on it, or lift that once he picks"
    )
    verb.add_argument("--once", action=argparse.BooleanOptionalAction, help="mark or unmark it as a repair he asked to run once, which puts up no PR")
    verb.add_argument("--project", metavar="NAME", help="the project the dashboard's Now screen files this order under; an empty name takes it out")
    verb.add_argument("--workstream", metavar="NAME", help="the workstream inside its project the Now screen files this order under; an empty name takes it out")
    verb.add_argument("--title", metavar="WORDS", help=f"the few words, at most {TITLE_CHARS} characters and no link, the dashboard names this order by")
    verb.add_argument(
        "--record",
        metavar="SEQ",
        help="the report record a handler posted; a close from the communicator's session needs it and closes only an order its close attr names",
    )
    verb.set_defaults(run=order_set)
    verb = order.add_parser("unfiled", parents=both, help="each open order that names no project, workstream or title yet, then the project and workstream pairs in use")
    verb.set_defaults(run=order_unfiled)
    verb = order.add_parser("posted", parents=both, help="the communicator posted a line in an order's thread, its cards' or its PRs' cards': a progress line, delivering no part, or the answer a part owes him, which the report record it carries delivers, there or in any other channel; or the session holding the order closes a part from the drive's post that already answered it")
    verb.add_argument("id", help="the order")
    posted = verb.add_mutually_exclusive_group(required=True)
    posted.add_argument("link", nargs="?", help="the Slack link of the drive's post; one in the order's threads keeps a promised time and moves the order's last line")
    posted.add_argument("--undo", action="store_true", help="take back the delivered mark of the part --part names, so it counts as undelivered again")
    verb.add_argument(
        "--part",
        help=(
            "with --undo, the part whose mark it takes back; from the communicator with --record, only a part its part attr names;"
            " from the coordinator or meta session holding the order, with or without --record, or the communicator holding it with none, the part the drive's post at the link already answered, posting nothing"
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
            "the communicator's handler posted the one fix-live note in the thread the order's bug was reported in, or in the group DM of"
            " its reporter, him and the bot when the bot cannot post there, once its proof is kept; done refuses until then"
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
    verb.set_defaults(run=order_told)
    verb = order.add_parser("show", parents=both, help="drive status for these ids")
    verb.add_argument("ids", nargs="+", metavar="ID", help=f"what to read: {ID_FORMS}")
    verb.set_defaults(run=status)

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
    verb.add_argument("--record", required=True, metavar="TITLE", help="the evidence record's title: <service> on <cluster>: <symptom>, since <time>")
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
    verb.add_argument("--title", metavar="WORDS", required=True, help=f"the few words, at most {TITLE_CHARS} characters and no link, the dashboard names it by")
    verb.set_defaults(run=incident_open, incident=True, joins=None, kind=None, complex=False, options_only=False, once=False, owner=None, answers=None)
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
    verb = incident.add_parser(
        "release",
        parents=both,
        help=(
            "in the turn a job of an open incident starts a release, its fix's, rollback's, stop's or the default branch's: the"
            " watch reopens the incident's stop, with a job to get it live, the turn cc-slack hands the drive that release failing a step"
        ),
    )
    verb.add_argument("id", help="the open incident order")
    verb.add_argument("release", help="the release's name as tools/ci release start prints it and its post shows it, such as stark-maclean")
    verb.set_defaults(run=incident_release)

    job = verbs.add_parser("job", help="background agents").add_subparsers(dest="action", required=True)
    verb = job.add_parser(
        "add",
        parents=both,
        help="start a job on an order's parts: prints its id, then each job still running or waiting on one of those parts",
    )
    verb.add_argument("kind", type=job_kind, choices=state.JOB_KINDS, help="what the job is; designer, its agent type's name, is design")
    verb.add_argument("--order", required=True, help="the order whose parts it delivers")
    verb.add_argument("--goal", required=True, help="its deliverable, one line")
    verb.add_argument("--agent", help="the agent id the Agent tool returned")
    verb.add_argument("--repo", help="owner/name")
    verb.add_argument("--branch", help="the branch it works on")
    verb.add_argument("--worktree", help="its worktree's path")
    verb.add_argument("--remote", action="store_true", help="a builder that starts as a remote lane on a Sprite")
    verb.add_argument("--part", action="append", default=[], help="a part of the order this job delivers; default: its only part")
    verb.add_argument(
        "--lands",
        action="append",
        default=[],
        metavar="PR",
        help="a drive PR he approved, or an exempt class lands, that this builder lands and builds nothing on, so it starts with no research",
    )
    verb.add_argument(
        "--waits",
        action="append",
        default=[],
        metavar="CARD|PR|RELEASE|JOB",
        help=(
            "a card of his still open, a PR the drive tracks as <owner>/<repo>#<n> or release:<service>:<owner>/<repo>#<n>, as job set"
            " takes them, or for a build order's designer, builder or codex lane its reader still at work, that this job waits on: it is recorded"
            " waiting with no agent, so its part is never in no job, and the watch says FREE once the last wait ends, when its agent starts"
        ),
    )
    verb.set_defaults(run=job_add)
    verb = job.add_parser("set", parents=[*both, waits_opt])
    verb.add_argument("id", help="the job")
    verb.add_argument(
        "--agent",
        help="the agent id the Agent tool returned, the dispatch the job records, which puts it back on its Orca lane, or another Orca dispatch;"
        " either dispatch records the Run, Task, terminal, lane and Sprite or Mac Orca shows for it",
    )
    verb.add_argument(
        "--state", choices=state.JOB_STATES, help="stopped: it ends unfinished; every other state comes from the line the job returns, and one it holds changes nothing"
    )
    verb.add_argument("--branch", help="the pushed branch it works or built on")
    verb.add_argument("--said", help="its latest line")
    verb.add_argument("--due", help="its clock: when it must have returned, said once as DUE if it still runs then, a time or +10m")
    verb.add_argument("--repro", help="the job's pick-up-and-run reproduction file, kept in the drive")
    verb.add_argument("--pr", action="append", default=[], help="a PR it opened or took over")
    named = verb.add_mutually_exclusive_group()
    named.add_argument("--part", action="append", default=[], help="another part of the order this job delivers, or of the one order --carries names")
    named.add_argument("--parts", nargs="+", type=str.strip, metavar="PART", help="the parts of its own order this job delivers, replacing its list")
    verb.add_argument(
        "--carries",
        action="append",
        default=[],
        metavar="ORDER",
        help="another order whose part this job delivers, until it is done; it names each open card of his carrying the job's PRs and leaves it up, since its stack can meet his words already: only --rework withdraws one",
    )
    verb.add_argument("--rework", action="append", default=[], metavar="PR", help=f"with one --carries, {REWORK_HELP}; refused for a PR of another job")
    verb.add_argument(
        "--card",
        help=(
            "the Slack link of a question card the job asked on, which carries its parts until he answers it; each card"
            " keeps its own question, so a job holds every card it asked on, each closed by his answer to it"
        ),
    )
    verb.add_argument(
        "--told",
        metavar="LINK",
        help=(
            "the drive's post in an incident's thread linking each card --card or --waits names, what it blocks and the default the job"
            " builds on meanwhile; a job on an open incident order takes a card only with it"
        ),
    )
    verb.set_defaults(run=job_set)
    verb = job.add_parser(
        "ask",
        parents=both,
        help="keep a job's ASK in the drive beside its other open questions, refused unless it is in the shape he reads, and set the job waiting; --drop throws away one kept with no card",
        description=(
            "Check a job's ASK block: `ASK <the question>`, one to three summary lines (what was found and what is being"
            " decided, the recommendation, what happens meanwhile, answering his next question: why, did he already decide"
            " this, is it a port, what comes after, or why it is held, or `Next question: none: <why>` under `Findings:`),"
            " `Show me:` and at least one visual under it, or no Show me"
            " when nothing shows what the words cannot (a mermaid flow, a compare or a table for options, a chart for numbers,"
            " an arch diagram, whose JSON parses, the real call in a fence naming its language for a code or API shape, or a"
            " picture on a line of its own as ![caption](/absolute/path.png); never words, JSON, YAML, logs, a terminal"
            " session, an HTTP exchange, command output or a stack trace in a fence), captions of at most 25 words,"
            " two to four `- ` options of at most 25 words, each saying what he gets and what it costs, with exactly one ending"
            " (recommended), each followed by one plain-text line indented two spaces, at most 60 words, that his card draws"
            " under it, saying in prose what the option is: what runs and when, where its data lives, and what it requires"
            " touching or building, by name, with any cost every user pays, never fields such as `Does:`; no mechanism such as"
            " polling, a push or an event named by a bare word on the question or an option, the question, summary and"
            " options at most 150 words in all, in plain words, each thing he"
            " could open linked where it is named, then `Findings:` with the evidence, its `Root cause:` line naming the"
            " structure that lets the defect happen and its `First principles:` line the recommended option that removes"
            " it, or `Root cause: fixed: <that structure>, removed by <the merged PR's link>`, `not found: <the job"
            " finding it>` or `none: <why>`. Keep it in the drive in a file of"
            " its own and print its path, which the ask record carries, and add it to the job's open questions, each"
            " open until his answer to its own card. A block asking again, in the same words, what one of the job's"
            " questions asks that his answer has not closed takes that question's place, its card and waits id kept, so"
            " its ask record redraws that one card; one asking what another job's open question asks is refused, naming"
            " the wait that frees this job at his answer. Refuse it with every way it is out of shape, and while Jev reads"
            " one recorded answer or thread message of his as already deciding it."
        ),
    )
    verb.add_argument("id", nargs="?", help="the job whose ASK this keeps; none with --check")
    verb.add_argument(
        "file", nargs="?", help="the file holding the ASK block, or - to read it from stdin; default: a remote lane's latest report"
    )
    verb.add_argument(
        "--check",
        metavar="FILE",
        help="only check the ASK block in this file, or - for stdin, its words, the card cc-slack draws from it and each picture its Show me draws, and print every fix it needs or ready; keeps nothing and reads no drive",
    )
    verb.add_argument(
        "--unsettled",
        metavar="WHY",
        help="one string naming every recorded answer of his drive job ask read as settling this question or its refusal listed, by id, and why it does not settle it",
    )
    verb.add_argument(
        "--drop",
        metavar="FILE",
        help=(
            "in place of the file, throw away the job's question kept in this file with no card drawn or being drawn for it, one kept"
            " by mistake such as a near copy of a question already asking him: it leaves the job's open questions and the file is"
            " deleted, so no ask record may name it; refused for a question on a card, which only his answer closes"
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
            " only draws the card. Refused when his"
            " words under that card answered it: they are his answer, and a question they leave open goes on a card of"
            " its own. A block kept under older rules redraws as kept, its Show me's visuals never checked again, and"
            " markdown in its question or options, which Slack draws raw, drawn as the words it marks. Name a job first,"
            " `drive job card <j>`, to draw its newest kept ASK, or the one a file after it names, under the waits id its"
            " card already has."
        ),
    )
    verb.add_argument("ask", metavar="JOB|FILE", help="a job, whose newest kept ASK block draws, or the kept ASK block itself, the file an ask record's `ask` attr names")
    verb.add_argument("file", nargs="?", help="after a job, which of its kept ASK blocks draws; default: its newest")
    verb.add_argument(
        "--waits",
        help=(
            "the card's waits id, <drive>:<job>-<the ask record's seq>, for a question holding none yet, or the waits id of a"
            " card of the job to redraw; a question holding one is drawn under it; after a job, default: the waits id its card already has"
        ),
    )
    verb.set_defaults(run=job_card)
    verb = job.add_parser(
        "report",
        parents=both,
        help="keep a job's whole return in the drive, or print every return it took",
        description=(
            "A job returns one line; the rest of its return, the inputs to what he would be told, one fact a line,"
            " is kept here before it returns, piped in on stdin. With -, keep stdin in the job's history, which is its report,"
            " set its state and said line from its first line, and print where; with nothing, print every return the job took,"
            " its own agent's, its lane's and anyone else's, newest first."
        ),
    )
    verb.add_argument("id")
    verb.add_argument("stdin", nargs="?", choices=["-"], help="- to keep the job's whole return from stdin; default: print every return it took, newest first")
    verb.set_defaults(run=job_report)
    verb = job.add_parser(
        "brief",
        parents=both,
        help="write a job's brief from his order's words, each ruling's words and the earlier jobs' transcripts, and print where",
        description=(
            "Write the brief reference/jobs.md shapes into the drive and print its path: Order from the words the order"
            " holds, verbatim; Goal from the job; Decisions that bind from each --ruling's recorded answer and each one that"
            " settled a question of the reader its Researched: line names, its title and his words above the drive's Notes:; Transcripts from each"
            " earlier job on the order; Scratch, by default a folder of the drive's;"
            " the Drive line; a reader's on a build order, Research: the Researched: block this build needs; and each line"
            " the flags name. A flag left out keeps the job's earlier brief's, all but --handoff, and the output names each kept;"
            " a first brief names --serves, --done-when and --where. An adopted PR's job, waiting with no agent, is briefed only"
            " with --handoff for an event that needs action. Its first line, `Read your brief first: <path>`, is a local"
            " job's whole Agent prompt, which binds the agent to the job, and a remote job launches from it with drive job launch <j>."
        ),
    )
    verb.add_argument("id", help="the job")
    verb.add_argument("--serves", help='"port of #N", "drive tooling" or "his order"; a first brief names it')
    verb.add_argument("--done-when", help="the check that proves it done; a first brief names it")
    verb.add_argument("--where", help="the repo, base branch, and any branch or PR to adopt; a first brief names it")
    verb.add_argument(
        "--native",
        help='where he sees or uses the result and what that platform already does for it, with links; "none" for work he never sees',
    )
    verb.add_argument("--interactions", help="each click or step he takes on the result, which its builder walks through and records; needed when --native names a surface")
    verb.add_argument("--would-he-ask", help="what he would still have to ask for if only his words were built")
    verb.add_argument("--runs-as", help="for a pass over a team's data: the service account in its cluster and how it starts; \"none: <why>\" for a job that runs none")
    verb.add_argument("--handoff", help="a PR event or freed built branch: the PRs or branch, its head, the event line, INSTALL: and PROVE:")
    verb.add_argument("--ruling", action="append", default=[], metavar="ANSWER", help="a cc-notes answer id of his that touches this; its words go in whole")
    verb.add_argument("--plan", help="the plan section this implements, quoted")
    verb.add_argument("--incident", help="an incident job's line, by reference/jobs.md")
    verb.add_argument("--scratch", help="its scratch folder; default one in the drive")
    verb.set_defaults(run=job_brief)
    verb = job.add_parser(
        "launch",
        parents=both,
        help="start a job's next worker: its first Orca lane on a Sprite, or on this Mac with --local, or the next of one that ended",
        description=(
            "Start a job's next worker by the one relaunch drive job resume shares, under the job's own lock, so a launch or resume"
            " already starting one finishes first, about 150 s, and this one acts on the worker it started. A job with no worker yet"
            " starts its Orca lane on a Sprite, or on this Mac with --local, from the drive's own checkout, moved first with one"
            " fetch to the head of the coordinator checkout's default branch, so the lane gets the cc-remote and tools that head pins."
            " A lane whose worker ended starts its next as drive job resume does, with no message. A job held by an Agent another"
            " session started, which this session cannot SendMessage, or by an agent on this Mac whose last response ended on an API"
            " error or a refusal, starts on a handoff brief carrying the job's brief, that agent's branch, last line, report and PRs,"
            " and the job is recorded as this session's lane."
        ),
    )
    verb.add_argument("id", help="the job")
    verb.add_argument(
        "brief", nargs="?", help="the file holding the job's brief, or - to read it from stdin; default the one drive job brief wrote"
    )
    verb.add_argument(
        "--local",
        action="store_true",
        help="start it as an Orca worker on this Mac, in a fresh worktree of this checkout's Orca repository, read like a Sprite's lane",
    )
    verb.set_defaults(run=job_launch)
    verb = job.add_parser(
        "resume",
        parents=both,
        help="answer a job's question, or send it the next event, starting its next worker when its last ended",
        description=(
            "Answer a remote lane's question, or send it the next event, once Orca shows its worker alive, by the one relaunch drive"
            " job launch shares, under the job's own lock, so a resume arriving while another starts the job's next worker waits"
            " for it, about 150 s, and sends that worker the text. An agent on this Mac whose last response ended on an API error or"
            " a refusal hands the job to a lane on a Sprite, with a handoff carrying the job's brief and this text. When its "
            "worker finished and Orca still holds its terminal, on its Sprite or on this Mac, start the job's next worker "
            "in that terminal, or, when Orca no longer finds that terminal or worktree, as a dead worker's; when its worker is dead, relaunch the job on a fresh Sprite, or in a fresh worktree on this "
            "Mac, on the same Run. Before a relaunch on a fresh Sprite it saves what the dead lane's Sprite holds that GitHub does not: over ssh "
            "it commits each worktree's uncommitted changes there and pushes each commit, branch or stash on no remote as saved/<the dead lane>/<its "
            "branch>, on the token gh auth token prints, lent to that one push through the ssh session's stdin, so it never reaches the Sprite's disk; "
            "the new worker's handoff names each saved branch. When that Sprite does not answer or the save fails, it relaunches the job on that "
            "Sprite instead, in the checkout the dead lane left. Either new worker gets a handoff carrying the job's brief, its branch, its last line, "
            "its report, its PRs and this text. When Orca cannot reach the lane's Sprite, or refuses what would deliver "
            "this text, exit 2 with Orca's refusal. A job held by an Agent another session started, which this session cannot "
            "SendMessage, gets a handoff brief instead, carrying the same, and prints its `Read your brief first:` line: an agent "
            "this session starts on it takes the job as this session's."
        ),
    )
    verb.add_argument("id")
    verb.add_argument(
        "text",
        type=lambda value: sys.stdin.read() if value == "-" else value,
        help="his answer, or the event the lane acts on next, or - to read it from stdin",
    )
    verb.add_argument(
        "--handed", action="store_true", help="the watch handed it a PR's move: a refusal is recorded as an event waking the coordinator with the text"
    )
    verb.set_defaults(run=job_resume)
    verb = job.add_parser(
        "read",
        parents=both,
        help="what the watch starts on a remote lane's LOG, READ or DRIVE question: answer it here, keeping first the answer its own end leaves so it never runs twice, then the one the watch sends",
    )
    verb.add_argument("id")
    verb.add_argument("message", help="the question's Orca message id")
    verb.set_defaults(run=job_read)
    verb = job.add_parser(
        "replies", parents=both, help="every result and question a remote lane sent, and every return drive job report kept, oldest first, with when each came"
    )
    verb.add_argument("id")
    verb.set_defaults(run=job_replies)
    verb = job.add_parser(
        "held",
        parents=both,
        help="his open cards holding the job's PR from going up, one waits id a line, which the bash_guard hook asks before a ccx vcs ship opens it; none once the job has an open PR",
    )
    verb.add_argument("id", nargs="?", help="the job; a Sprite lane's own job when left out")
    verb.set_defaults(run=job_held)
    verb = job.add_parser(
        "end",
        parents=both,
        help=(
            "free a remote job's lane once it returned its line: its Sprite, destroyed once it holds nothing on no remote, or its terminal and clean worktree on this Mac. The job"
            " stays in the state its line set, with its waits, PRs and carried parts. A job not yet done first has what its Sprite holds"
            " on no remote, uncommitted changes included, saved to GitHub as saved/<its lane>/<branch>, which its next lane's handoff"
            " names; refused, naming why, when that Sprite cannot be read or the save fails"
        ),
    )
    verb.add_argument("id")
    verb.set_defaults(run=job_end)
    verb = job.add_parser(
        "prove",
        parents=both,
        help=(
            "what the watch starts once every PR of a builder's taken READY or PUSHED merged, a yasyf/* one among them, and it keeps an"
            " executable proof file: install each plugin a yasyf/* merge changes whose installs, binaries, Homebrew copy or Captain Hook.app lack it, its other merges taken at once, wait until the drive's dashboard"
            " runs the newest long-running installed, run the proof file with those plugins' bin folders first on PATH, keep its output"
            " beside the job's report, take DONE on exit 0 and BLOCKED otherwise or when an install fails, no release will carry a merge"
            " or the dashboard still runs an older install at the timeout, and keep the close report of an order the DONE finishes for the"
            " watch to post; refused, naming who proves it instead, for a merge outside yasyf/* or a job keeping no proof file"
        ),
    )
    verb.add_argument("id")
    verb.add_argument("--timeout", type=int, default=900)
    verb.set_defaults(run=job_prove)
    verb = job.add_parser("show", parents=both, help="drive status for these ids")
    verb.add_argument("ids", nargs="+", metavar="ID", help=f"what to read: {ID_FORMS}")
    verb.set_defaults(run=status)

    pr = verbs.add_parser("pr", help="the drive's pull requests").add_subparsers(dest="action", required=True)
    verb = pr.add_parser("add", parents=both)
    verb.add_argument("url", help="the PR's URL")
    verb.add_argument("--job", required=True, help="the job that opened or took it over")
    verb.add_argument("--why", help="with --no-tap, why it lands with no tap of his")
    untapped = verb.add_mutually_exclusive_group()
    untapped.add_argument("--port-of", type=int, metavar="PR", help="a pure port of this old-tree PR number: exempt, with no tap card; refused for a PR changing a migration, schema or wire format, whose data model is a design part of his order")
    untapped.add_argument("--exempt", choices=state.EXEMPT_CLASSES)
    untapped.add_argument("--no-tap", action="store_true", help="it lands with no tap of his, outside Forge-AI; needs --why. Every other PR waits on his tap")
    verb.set_defaults(run=pr_add)
    verb = pr.add_parser("set", parents=[*both, waits_opt])
    verb.add_argument("url", help="the PR's URL")
    verb.add_argument(
        "--tap", action=argparse.BooleanOptionalAction, help="waits on his tap; --no-tap withdraws his open card for it, needs --why"
    )
    verb.add_argument("--why", help="with --no-tap, the reason his withdrawn card shows; with --no-tapped, why the drive's own hold no longer stands")
    verb.add_argument("--serves", metavar="ORDER", help="the order of his whose words ask for this PR; prints them back")
    verb.add_argument(
        "--design",
        type=design_basis,
        metavar="asked:<answer>|none",
        help="his answer or card that settled each design decision the PR makes, or none; --serves needs it, its words check reads each answer whole as his words, and a card he has not answered or a message he did not write is refused",
    )
    verb.add_argument(
        "--part",
        action="append",
        default=[],
        help="a part of its job's orders this PR delivers, replacing the parts it named; its job's change is judged against the parts its PRs name, every part of the job when none names one",
    )
    exemption = verb.add_mutually_exclusive_group()
    exemption.add_argument("--exempt", choices=state.EXEMPT_CLASSES)
    exemption.add_argument("--no-exempt", action="store_true", help="clear the exemption")
    exemption.add_argument(
        "--port-of", type=int, metavar="PR", help="a pure port of this old-tree PR number: exempt, with no tap card; refused for a PR changing a migration, schema or wire format, whose data model is a design part of his order"
    )
    verb.add_argument("--fundamental", help="what changed; clears his tap and any exemption")
    tapped = verb.add_mutually_exclusive_group()
    tapped.add_argument("--tapped", choices=state.TAP_LABELS, help="record a tap he gave elsewhere, redrawing his open card for it in place to show it")
    tapped.add_argument(
        "--no-tapped",
        action="store_true",
        help="drop a hold the drive recorded itself as his tap, refusing his own; needs --why, kept in the drive's events",
    )
    verb.add_argument("--tap-source", type=his_link, help="the Slack permalink of his message or of the card he tapped")
    verb.set_defaults(run=pr_set)
    verb = pr.add_parser("tapped", parents=both)
    verb.add_argument("url")
    verb.set_defaults(run=pr_tapped)
    verb = pr.add_parser(
        "land",
        parents=both,
        help="what the watch, his Approve and pr add start: queue PRs he approved through stack-enqueue --watch, or merge a yasyf/* PR with --admin and install it",
    )
    verb.add_argument("urls", nargs="+", help="one PR, or a stack bottom first")
    verb.add_argument("--dry-run", action="store_true", help="run stack-enqueue --check from a fresh worktree of the default branch, say what it would do, refusing as it would, and queue and record nothing")
    verb.add_argument("--outcome", help="land nothing and record this outcome, as a lander reopening on a newer install records what came of its landing")
    verb.add_argument("--below", action="append", default=[], metavar="PR", help="with --outcome, a PR below it without his Approve that held it; repeat for each")
    verb.set_defaults(run=pr_land)
    verb = pr.add_parser(
        "check",
        parents=both,
        help="what the watch starts on a builder's READY: judge one PR his card waits on at its head against his words, and say CHECKED, which draws his card once it is green and approved, or NOT READY, which goes to its builder",
    )
    verb.add_argument("url", help="the PR's URL")
    verb.set_defaults(run=pr_check)

    verbs.add_parser("place", parents=both, help="where the next monorepo builder starts").set_defaults(run=place_job)
    verb = verbs.add_parser("bound", parents=both, help="run one command with a lane's Run bound to this terminal, under the drive's binding lock, a worker-start only until Orca takes its request, then bind the drive's own Run, which no lane mails")
    verb.add_argument("lane_run", metavar="run")
    verb.add_argument("command", nargs=argparse.REMAINDER, help="after --, the command and its arguments")
    verb.set_defaults(run=bound)
    verb = verbs.add_parser(
        "answer",
        parents=both,
        help="record an answer or ruling of his against the cards still asking him and PRs not yet live that it could change, each marked standing, settled, superseded or reopened by it",
        description=(
            "Run for every answer and ruling of his. The first call, with --title, --words and --link and no id, records"
            " it in cc-notes and here, or with an id, --words and --link records one already in cc-notes; a ruling, labelled"
            " ruling, records only with --rewrite naming the communicator's open \"Drive tooling:\" order that rewrites the"
            " skill to it, so the watch chases that rewrite until it is done. It lists the cards of his still open and the"
            " PRs not yet live that it could change: those of the job and order of the card it answers, those its words"
            " name, and every other open question card Jev, reading his answer against all of them in one call, reads it as"
            " deciding, ruling options out of or making moot (key, kind, job, order, title, link), and each job waiting on"
            " one of their orders, and exits 2. Each later call marks them: --stands for one this answer leaves as it is,"
            " --settled for a question card it decides, which shows his answer on the card in place with no buttons, or for"
            " one it decides in part with the ASK its job keeps asking only what his answer leaves open, which takes that"
            " card's place and redraws it; --superseded for one it replaces, which settles its card in place showing his words and a"
            " link to them with no buttons, drops its PR's tap ask, and stops its job, closing an open PR on GitHub, or"
            " folds what the job carries into the live job named, and --reopened for a PR his words change, which drops his"
            " tap on it, withdraws each open card of his carrying it in the same call, and names the live job that reworks"
            " it, no card coming back until that job's READY says how his words are met; --sent for a waiting job"
            " SendMessage gave his words verbatim, and --untouched for one they leave as it is. --others-stand marks the"
            " cards and PRs left standing only while a few are left. It exits 2, listing the rest, until every one is"
            " marked; the watch says ANSWER every 10 minutes while a card or PR is not."
        ),
    )
    verb.add_argument("id", nargs="?", help="his answer's cc-notes id; leave it out with --title on the first call to record it in cc-notes")
    verb.add_argument("--title", help="the question his answer settles, verbatim, as its cc-notes title; with no id, the first call records it there")
    verb.add_argument("--label", action="append", default=[], help="a cc-notes label beyond scope:durable and drive:<drive>, such as header:<topic> or from:owner")
    verb.add_argument(
        "--rewrite",
        metavar="ORDER",
        help="for a ruling, labelled ruling: the communicator's open \"Drive tooling:\" order that rewrites the skill to it, which the first call needs",
    )
    verb.add_argument(
        "--words",
        help="his words, verbatim, cut with … where words are left out, which his cc-notes answer keeps and a superseded card shows; the first call needs them, refused unless his message or card answer at --link holds them",
    )
    verb.add_argument(
        "--link",
        help="the permalink of his message or of the card he answered, which a superseded card links; the first call needs it, and checks both --words and a named answer's words above its Notes: against it",
    )
    verb.add_argument("--stands", action="extend", nargs="+", default=[], metavar="KEY", help="a card or PR his answer leaves as it is")
    verb.add_argument(
        "--settled",
        action="extend",
        nargs="+",
        default=[],
        type=settled_mark,
        metavar="KEY|KEY=FILE",
        help=(
            "a question card his answer decides: it shows his answer in place with no buttons, its job's question is settled by it, and the job"
            " builds on his words; or, with FILE, one it decides in part: FILE is the ASK its job keeps with drive job ask asking only what his"
            " answer leaves open, linking his answer in its summary, which takes the card's waits id and redraws that card in place. Either"
            " way the card's job hears his words, marked --sent or --untouched"
        ),
    )
    verb.add_argument(
        "--superseded",
        action="extend",
        nargs="+",
        default=[],
        type=superseded_mark,
        metavar="KEY=stop|KEY=JOB",
        help="a card his answer replaces: settled in place, its PR's tap ask dropped, and its job stopped, or folded into the live job named",
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
        help="every card and PR still unmarked after this call's marks stands, refused while more than a few are left, so each is read against his words",
    )
    verb.add_argument(
        "--sent", action="extend", nargs="+", default=[], metavar="JOB", help="a job waiting on an order his answer touches, once SendMessage gave it his words verbatim"
    )
    verb.add_argument("--untouched", action="extend", nargs="+", default=[], metavar="JOB", help="a job waiting on an order his answer touches that his words leave as it is")
    verb.set_defaults(run=answer)
    verb = verbs.add_parser(
        "heard",
        parents=both,
        help="he wrote: record it, adding the orders that changed while he was away to the catch-up the drive owes him, which the watch posts at his DM root; prints heard, or away, and nothing to act on",
    )
    verb.add_argument("--away", action="store_true", help="he said he is going to sleep or away")
    verb.add_argument(
        "--repeats",
        metavar="ORDER",
        help="the open order a part of which his words repeat, such as asking where it is; refused while no running, waiting or built job carries that part and no card of his a done job asked on is still open",
    )
    verb.add_argument("--part", action="append", default=[], help="the part of the --repeats order his words repeat; default: its only part")
    verb.set_defaults(run=heard)
    verb = verbs.add_parser(
        "handler",
        parents=both,
        help=(
            "start a handler on its brief as its own claude --bg session, recording that he wrote when the brief carries his"
            " words; its line comes back as one cci record when it ends; prints started <its id>"
        ),
    )
    verb.add_argument(
        "brief",
        help=f"the handler's brief: its lines, each opening with one of {', '.join(label + ':' for label in render.HANDLER_LABELS)}",
    )
    verb.add_argument("--away", action="store_true", help="he said he is going to sleep or away, for a brief carrying his words")
    verb.set_defaults(run=handler)
    verb = verbs.add_parser(
        "term", parents=both, help="he corrected a word: say's hook finds it from now on for Jev to judge, naming the proper term his words or the code use"
    )
    verb.add_argument("stand_in", metavar="stand-in", help="the word he corrected, as a post used it")
    verb.add_argument("term", help="the proper term to use in its place, his word or the code's")
    verb.set_defaults(run=term)

    verb = verbs.add_parser("status", parents=both, help="where each order, job and PR stands")
    verb.add_argument("ids", nargs="*", help=f"what to read, each {ID_FORMS}; default: a coordinator's own open orders, a line each, any other session's every open order")
    verb.set_defaults(run=status)
    verb = verbs.add_parser("manual", help="print one page of the long-running skill")
    verb.add_argument("page", nargs="?", choices=manual_pages(), help="default: list the pages")
    verb.add_argument("row", nargs="?", help="print only the table rows whose first cell names this, such as TAP or READY")
    verb.set_defaults(run=manual)
    verb = verbs.add_parser("context", parents=[drive_opt])
    verb.add_argument("--role", choices=state.ROLES, help="default: the role this session holds")
    verb.set_defaults(run=context)
    verbs.add_parser("view", parents=both, help="what waits on him and where each feature stands").set_defaults(
        run=view_command
    )
    verb = verbs.add_parser("dashboard", parents=[drive_opt], help="start the drive's dashboard and print its link")
    verb.add_argument("--port", type=int, help="serve on this port instead of the drive's own")
    verb.add_argument("--serve", action="store_true", help="serve in the foreground")
    verb.add_argument(
        "--listening", type=int, action="append", default=[], metavar="FD",
        help="with --serve, serve on this inherited listening socket instead of binding the port: an older install's dashboard hands its sockets to the newest this way",
    )
    verb.set_defaults(run=dashboard_command)
    verb = verbs.add_parser("watch", parents=[drive_opt], help="start the drive's watch as its own process unless one runs")
    verb.add_argument("--loop", action="store_true", help="run the watch in the foreground: the process drive watch starts")
    verb.add_argument(
        "--project",
        metavar="NAME",
        help=(
            "the watch of that project's sub-coordinator, started only from its session: the lines of the orders filed under"
            " it, their jobs and PRs, posted to its lane; the drive's own watch leaves those lines out"
        ),
    )
    verb.set_defaults(run=watch_events)
    cards = verbs.add_parser("cards", help="his open question cards").add_subparsers(dest="action", required=True)
    verb = cards.add_parser(
        "redraw",
        parents=both,
        help="print the card drive job card prints for each of his open question cards, one JSON line each",
        description=(
            "Redraw his open question cards in one pass: for each open card, print the JSON `drive job card <its ASK file>"
            " --waits <its waits id>` prints, marked in_place so Slack edits the card where it sits and posts no new version,"
            " one line each, for say(card=…). Its ASK file is the one a job's question keeps"
            " under its waits id, else the one a question linked to the card keeps when it asks the card's question, else"
            " asks/<waits id>.md rebuilt from the card as posted, which a question of its job then keeps. Each card that does"
            " not redraw, one whose redraw is refused, is named once the rest are printed, and the verb exits 2."
        ),
    )
    verb.add_argument("--waits", action="append", default=[], help="redraw only this open card, by its waits id; repeatable")
    verb.set_defaults(run=cards_redraw)
    verb = cards.add_parser(
        "withdraw",
        parents=both,
        help="withdraw his open question card that his words withdraw or change",
        description=(
            "Withdraw his open question card that his words withdraw, wherever he wrote them, or whose question or options they"
            " change, such as more options, a new constraint or a rework of its work: it settles in place showing his words and"
            " a link to them, with no buttons, so a late tap records nothing, and its question, asked again on any job once the"
            " work is back, keeps this card's waits id and redraws it."
        ),
    )
    verb.add_argument("card", help="the card, by its waits id or its permalink")
    verb.add_argument("--words", required=True, help="his words that withdraw or change it, verbatim, which the card shows; refused unless his message at --via holds them")
    verb.add_argument("--via", required=True, help="the permalink of his message, which the card links")
    verb.set_defaults(run=cards_withdraw)
    card_help = "cc-slack's card_command: reads one request JSON on stdin and prints one JSON reply"
    verbs.add_parser("card", help=card_help, description=card_help).set_defaults(run=card_command)
    verb = verbs.add_parser("mcp", help="serve say and the drive's verbs as MCP tools to a Claude Code session over stdio")
    verb.add_argument("--list", action="store_true", help="print the tools as JSON and serve nothing")
    verb.set_defaults(run=mcp_command)
    plan = verbs.add_parser("plan", help="the plans the coordinator's checkout keeps in cc-notes").add_subparsers(dest="action", required=True)
    verb = plan.add_parser(
        "search",
        parents=both,
        help="print the plan sections carrying the most of the words, best first, each under its plan's title and the headings above it",
    )
    verb.add_argument("words", nargs="+", help="what to look for in the plans, such as a part, a step or a file")
    verb.set_defaults(run=plan_search)
    verb = verbs.add_parser(
        "break-glass",
        parents=[drive_opt],
        help="run one command holding only a stack's admin credentials, minted on this Mac's machine profile to end with its break-glass grant",
        description=(
            "Start the forge/break-glass grant with bk build create, its reason the job or order and --reason, which may not be"
            " blank, and wait for it to pass. Then mint the env's account admin role with aws sts assume-role from the source_profile"
            " of the profile in ~/.aws/config that reaches it from a credential_process, never an SSO sign-in, to end a minute"
            " before the grant's recorded end, and run the command after -- with an AWS config naming that one profile alone, so"
            " a command naming another stack's profile finds none; the credentials are deleted when it exits. The command runs as"
            " a process group of its own, ended with every process it started when it exits, when the grant ends, which exits"
            " 124, and when drive gets SIGTERM, SIGHUP or SIGINT. A grant ending"
            " within 15 minutes, the shortest credentials STS mints, runs nothing. A read writes nothing; a production write runs"
            " only on his word or an incident order at 0 deletes and 0 replaces. A remote lane runs the same verb from its Sprite,"
            " in its own job's name."
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
    verb = verbs.add_parser("reload", help="type /reload-plugins into each session of every drive on this Mac, queued behind a turn still running after 30 seconds")
    verb.set_defaults(run=reload_plugins)

    verb = verbs.add_parser("setup")
    verb.add_argument("--bin-dir")
    verb.set_defaults(run=setup)
    return root


def main(argv: list[str] | None = None) -> int:
    """Run one verb holding its install's lock, so a newer install's first save waits for it to end, in the newest installed drive when this process started from an older install, such as a launch's bind prefix, unless the drive process that started it holds its own install's lock and waits on it as part of its verb. A Sprite lane's verb, which reads.lane_drive marks as a SPRITE_LANE run as its job's own lane, runs only when reads.lane_refusal finds nothing in what it parsed writing to another job or acting on another drive, a bare `status` reading the lane's own job, and nothing it starts carries that mark."""
    if argv is None and state.held_by_parent():
        state.hold_install()
    elif argv is None:
        install.reopen(sys.argv[1:])
    args = parser().parse_args(argv)
    if os.environ.pop(reads.SPRITE_LANE, None):
        own = os.environ[state.OWN_JOB_ENV]
        if refused := reads.lane_refusal(args, parser().parse_args, os.environ["LONG_RUNNING_DRIVE"], own):
            print(refused, file=sys.stderr)
            return 2
        if args.verb == "status" and not args.ids:
            args.ids = [own]
    try:
        return args.run(args)
    except (DriveError, state.NewerDrive, slack.SlackError, github.GithubError) as error:
        print(f"drive: {error}", file=sys.stderr)
        return 2
