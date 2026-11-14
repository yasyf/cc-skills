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
import uuid
from collections.abc import Callable
from functools import cache
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from drive import agents, artifacts, brief, codex, github, install, land, limits, orca, pages, picture, place, reads, refs, render, served, settled, slack, state, superseded, watch
from drive.shell import simple_commands
from drive.state import DriveError

DRIVE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
ROLE_NAMES = {"coordinator": "top coordinator", "communicator": "communicator", "meta": "meta session"}
FREEZE_FOR = timedelta(hours=12)
INCIDENT_EXEMPTION = "incident fix"
UNLANDABLE = (
    "{key}: refused, nothing {done}: only his tap or an exempt class lands a Forge-AI PR, and this leaves it with neither, so no card"
    " of his would ask his Approve and the lander would never queue it. Keep it waiting on his tap, or record its class with"
    " --exempt <class> or --port-of <N>; his order reworking it takes its card down with drive order set <o> --rework <url>"
)
PORT_DATA_RULING = 'his Nov 12 words "port his visuals but always do it the fundamentally right way when it comes to modelng the data"'
FILING = ("project", "workstream", "title")
TITLE_CHARS = 60
RECORD_NOTE = re.compile(r"\brecord \w+")
PAIR_MARK = re.compile(r"[/›>]")
TOOLING_FILING = ("The drive", "Drive tooling")
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
CODE_LABEL = "code:"
ID_FORMS = "an order o<n>, a job j<n>, or a PR as owner/repo#N, its URL or its bare number"
WAITS_CARD = re.compile(r"https://\S+/archives/\S+/p\d+\S*|[\w.-]+:[\w.-]+|merge:[\w.-]+:[\w.-]+:\d+(?:-\d+)?")
WAITS_ARGS = ("waits", "redraws", "card", "stands", "dropped", "settled", "superseded", "reopened")
GO_CI = Path("tools", "ci")
CLAUDE_BUILDS = Path.home() / ".local" / "share" / "claude" / "versions"
READER_RESEARCH = "the Researched: block this build needs"
VERB_ERRORS = (DriveError, state.NewerDrive, slack.SlackError, github.GithubError)
STDIN: list[str] = []
ANSWER_FIRST = (
    "his answer goes to his thread the moment you have it proven, before any reproduction file or full report: one `report`"
    " record on the cci `post` tool, `to: [communicator@{drive}]`, `text: POST <the answer>`, `attrs: {attrs}`; then keep your"
    " full report and return as usual"
)
PROMISED_TIME = re.compile(
    r"\bnext (?:line|update|post|summary)(?:\s+(?:by|before|at))?\s+(?:about\s+|around\s+)?([01]?\d|2[0-3]):([0-5]\d)(?![:\d])(?:\s*([AP])\.?M\b)?", re.IGNORECASE
)
REPORTER_HELP = (
    "the bug report the order fixes, or the question an answer order answers, by its Slack permalink or slack:<channel>/<its thread's ts>,"
    " from the share or link in his words: its thread gets one note, a bug report's once the fix is proven live and a question's once"
    " the answer is posted, and done refuses until drive order told records it; on order add, default: the thread a teammate started"
    " outside his DM that the --from message is in, with no --thread, on an order neither an answer, a watch nor an incident, whose"
    " lines then go to his DM; on order set, another reporter kept beside those the order holds, such as a second teammate's report of"
    " the same bug, so each thread hears its own note"
)
WATCH_HELP = (
    "a teammate's Slack thread he ordered watched, by its permalink or slack:<channel>/<its thread's ts>: the watch reads it once"
    " a minute and starts a handler on each new message there from anyone but him, the drive and a bot, until the order closes"
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
ASKED_AGAIN = "Asked again on a newer card · answer that one"
HIDDEN = "not visible in Orca, which hides the worktrees it did not make for this repository"
PAGES = Path(__file__).resolve().parents[2] / "skills" / "long-running" / "reference"
LIST_ITEM = re.compile(r"\s*(?:[-*] |\d+\. )")
MANIFEST = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
LAUNCHED = ("run", "dispatch", "task", "terminal", "workspace", "environment", "lane", "sprite_config")
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
            f" start the session under cci and show it in one with {' then '.join(agents.launch(args.name, args.role, args.project, os.getcwd()))}"
        )
    if args.project and args.role != "coordinator":
        raise DriveError(f"--project binds a sub-coordinator, which holds the coordinator role; the {state.title(args.role)} holds no project")
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    fresh = state.new_drive(args.name, args.role, session, os.getcwd(), orca.own_terminal())
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
        watched_channels = data["slack"].setdefault("watch", [])
        watched_channels.extend(channel for channel in dict.fromkeys(args.watch) if channel not in watched_channels)
    held = fresh["roles"][args.role]
    registered = "" if state.scratch() else f"{agents.register(args.name, args.role, args.project, held['cwd'], held['session'])}\n"
    seen = orca_place(held, run_show)
    meta = "" if "meta" in data["roles"] else meta_started(args.name, held["cwd"], data["launched"].get("meta"))
    watched = f"{watch.ensure(args.name, args.project)}\n" if args.role == "coordinator" else ""
    seat = f"sub-coordinator for {args.project}" if args.project else args.role
    text = f"{render.context(data, args.role, args.project)}\n\n{registered}{meta}{watched}{seat}: session {held['session']}, {seen}"
    return emit(args, text, {**data, "orca": {seat: seen}})


def meta_started(name: str, worktree: str, earlier: str | None) -> str:
    """Start the meta session in an Orca terminal of its own while none is bound, by his words "have the skill always start a meta root as a sibling to coordinator and communicator"; the line saying so, or nothing for a drive under LONG_RUNNING_STATE."""
    if state.scratch():
        return ""
    try:
        handle = orca.start_meta(name, worktree, earlier)
    except orca.OrcaError as error:
        return f"the meta session, which holds drive tooling, did not start: {error}; drive manual start prints its commands\n"
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
    """Record his order; an incident order records a job on each of its parts, its fix among them, unless it joins the open incident on the same failing service; a "Drive tooling:" order whose words Jev reads as asking what an open tooling order asks joins that order, every job on that order carrying it, so one fault keeps one job and one card; it reworks only the PRs --rework names and names each other open PR card of his in its thread, which it leaves up."""
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
    return [card for card in fresh_pr_cards(name) if state.link_thread(card["url"]) == where] if (where := state.promise_thread(order)) else []


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
    """Mark the open drive PRs reworked to his words and withdraw in the same call, once the drive is saved, each open card of his carrying one, his words its reason, by his words "So why isn't this card withdrawn then? And why do I have to keep asking that?": no card asks his tap on one until its builder's READY says how his words are met, while his Approve stands; the watch wakes its builder with his words, and withdraws again a card whose withdrawal failed."""
    with state.edit(name) as data:
        keys = [key for key in dict.fromkeys(map(state.pr_key, urls)) if key in data["prs"] and data["prs"][key]["state"] == "open"]
        for key in keys:
            state.rework(data["prs"][key], by, words)
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
    if args.incident and not args.joins and state.INCIDENT_FIX not in (args.parts or []):
        raise DriveError(
            f'an incident order starts its fix job at the alert, beside diagnosis: name "{state.INCIDENT_FIX}" among --parts, such as'
            f' --parts {state.INCIDENT_FIX} "first check", or pass --joins <o> when an open incident order covers the same failing service'
        )
    message = his_message(args.source)
    thread = order_thread(state.load(name), args.source, message, args.thread)
    reported = state.reporter(args.reporter) if args.reporter else teammate_report(state.load(name), args, message, thread)
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
    project, workstream = (None, None) if unfiled else filing(state.load(name), args.words, args.project, args.workstream)
    title = titled(args.title)
    if tooling:
        check_tooling_words(name, args.words)
    if tooling and not (args.joins or args.incident):
        args.joins = settled.repeats(state.load(name), args.words)
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
        if joined and (joined["state"] in state.CLOSED_ORDER_STATES or args.incident and not joined["incident"]):
            raise DriveError(
                f"--joins {args.joins}: name an open order, an open incident order for --incident; {args.joins} is {joined['state']}{'' if joined['incident'] else ', not an incident'}"
            )
        incidents = [render.incident_line(other_id, other) for other_id, other in state.open_orders(data).items() if args.incident and not joined and other["incident"]]
        order_id = state.next_id(data["orders"], "o")
        parts = {part: state.new_part() for part in args.parts or [title]}
        order = data["orders"][order_id] = state.new_order(args.words, slack_key(args.source, message), thread, parts, created) | {
            "due": due_stamp(args.due) if args.due else None,
            "promised": created if args.due else None,
            "alerts": alerts,
            "reporters": [reported] if reported else [],
            "watch": watched,
            "incident": args.incident or bool(joined and joined["incident"]),
            "kind": joined["kind"] if joined else "build" if args.incident else args.kind,
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
        if args.due:
            need_thread(order_id, order)
        started = [state.incident_job(data, name, order_id, part, created) for part in (order["parts"] if args.incident and not joined else [])]
        started += [state.INCIDENT_BRIEF] if started else []
        head = order["joins"]
        repeats = joined and not state.hurts_now(data, order)
        carriers = [job_id for job_id in data["orders"][head]["jobs"] if data["jobs"][job_id]["order"] == head and data["jobs"][job_id]["state"] not in state.FINISHED_JOB_STATES] if repeats else []
        for job_id in carriers:
            state.carry(data, job_id, order_id, list(order["parts"]))
    if joined:
        carried = (
            f"carried by {', '.join(carriers)}, so no job starts on it"
            if carriers
            else f"no unfinished job on {head} carries it, so its jobs are added on {order_id}"
            if repeats
            else f"its owner job carries this order with --carries {order_id}"
        )
        started.append(f"joins {args.joins}: {joined['note'] or 'no record'}; {carried}")
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
    """Open an incident in one save: his order, a job on its fix beside a job on each diagnosis part, and the evidence record its note names, titled by the order's --title, so a record cc-notes refuses leaves the drive as it was."""
    name = state.resolve(args.drive)
    args.parts = [state.INCIDENT_FIX, *state.INCIDENT_PARTS["release" if args.release else "none" if args.no_rollback else "rollback"]]
    with state.batch(name):
        order_id, order, started, incidents = added_order(args)
        record = evidence_record(name, order["title"], args.words, args.source, "drive incident open")
        with state.edit(name) as data:
            data["orders"][order_id]["note"] = order["note"] = f"record {record}"
    return emit(args, "\n".join([f"{order_id} record {record}", *started, *incidents]), {"id": order_id, **order, "incidents": incidents})


def evidence_record(name: str, title: str, words: str, source: str, again: str) -> str:
    """Open an incident's evidence record, a cc-notes investigation titled by its order's title, and return its id, or refuse, so the write waiting on it keeps nothing."""
    argv = ["ccn", "investigation", "open", title, "--body", f"{words}\n\nFrom {source}", "--label", f"drive:{name}", "--label", "incident", "--json"]
    try:
        return state.unrehearsed(name, lambda: json.loads(reads.run(argv, Path(state.load(name)["roles"]["coordinator"]["cwd"])))["id"][:7], "<record>")
    except reads.ReadError as error:
        raise DriveError(f"refused, nothing written: its evidence record did not open: {error}; fix that and run {again} again") from None


def turned_incident(name: str, data: dict, order_id: str, created: str) -> list[str]:
    """Turn an open order into an incident in the save that marks it, as drive incident open opens one: its fix and cause parts, a build whose other parts an answer order asked stay answers, its evidence record when its note names none, and a codex job by state.incident_job on each of those two parts no unfinished codex job holds, but on an order joined to an open incident, whose owner job carries it; the lines that say so."""
    if refused := state.held_elsewhere(data, order_id, os.environ.get("CLAUDE_CODE_SESSION_ID")):
        raise DriveError(f"refused, nothing written: {refused}")
    order = data["orders"][order_id]
    parts = [state.INCIDENT_FIX, state.INCIDENT_CAUSE]
    for part in parts:
        order["parts"].setdefault(part, state.new_part())
    sort_as_build(order, parts)
    opened = []
    if not RECORD_NOTE.search(order["note"] or ""):
        if not order["title"]:
            raise DriveError(f"{order_id}: refused, nothing written: its evidence record is titled by the order's title, and it has none; pass --title <a few words>")
        record = evidence_record(name, order["title"], order["text"], order["from"], f"drive order set {order_id} --incident")
        order["note"] = "; ".join(filter(None, [order["note"], f"record {record}"]))
        opened = [f"{order_id} record {record}"]
    if joined := state.open_incidents(data, [order["joins"]] if order["joins"] else []):
        return [*opened, f"joins {joined[0]}, an open incident, so no job starts on it: its owner job carries it with drive job set <that job> --carries {order_id}"]
    coded = {part for part in parts for _, job in state.part_jobs(data, order_id, part) if job["kind"] == "codex" and job["state"] not in state.FINISHED_JOB_STATES}
    started = [state.incident_job(data, name, order_id, part, created) for part in parts if part not in coded]
    return [*opened, *started, state.INCIDENT_BRIEF] if started else opened


def sort_as_build(order: dict, worked: list[str]) -> None:
    """Make the order a build whose worked parts are work, each other part of an answer order staying the answer its reader's post delivers."""
    answering = order["kind"] == "answer"
    for part, held in order["parts"].items():
        held["answer"] = part not in worked and (answering or held["answer"])
    order["kind"] = "build"


def incident_stop(args: argparse.Namespace) -> int:
    """Give an open incident its stop when no build can roll the break back: a job with apply authority whose PR, the incident's immediate fix, turns the failing automatic path off and lands at once as an incident fix, until the root fix replaces it."""
    name = state.resolve(args.drive)
    created = state.stamp()
    with state.edit(name) as data:
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
        freeze = state.unrehearsed(name, lambda: json.loads(reads.run(argv, checkout_dir))["freeze"]["id"], "<freeze>")
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


def teammate_report(data: dict, args: argparse.Namespace, message: tuple[str, str, str | None] | None, thread: tuple[str, str] | None) -> dict | None:
    """The reporter of an order he gave in a thread a teammate started outside his DM, that thread, which then hears only its one note while the order's lines go to his DM, by his pick "Drive posts only the fixed note in a reporter's thread, so the hook's one-pass rule always takes it"; None when --thread names where its lines go, on an answer, watch or incident order, and in a thread he, the drive or a bot started."""
    if args.thread or args.watch or args.incident or args.joins or args.kind == "answer" or not (message and thread) or thread[0] == slack.dm() or thread[1] == message[1]:
        return None
    try:
        root = slack.root(*thread)
    except slack.SlackError as error:
        raise DriveError(
            f"--from {args.source}: refused, Slack cannot read the thread it is in to tell whether a teammate reported a bug there: {error};"
            " pass --reporter slack:<channel>/<thread ts> for a teammate's report, or --thread <its permalink> for a thread its lines go in"
        ) from None
    if not root or root.get("from_user") or root.get("from_claude") or root.get("from_bot"):
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


def check_tooling_words(name: str, words: str) -> None:
    """Refuse a drive tooling order's words that `cc-slack check-post` finds anything in but the id of a recorded answer of his, since they reach him verbatim: quoted on the line above its PR card, and as the title an {order:} link shows. One line, each finding with its fix first, so a shell keeping only a refusal's last line keeps all of it."""
    if findings := refs.answer_ids(slack.post_findings(words, True), state.load(name)):
        raise DriveError(
            f"refused, `cc-slack check-post` finds {'; '.join(findings)}; a \"{state.TOOLING}\" order's words reach him verbatim, quoted on"
            " the line above its PR card and as the title an {order:} link shows, so fix each in words of your own and pass the fixed words"
        )


def filing(data: dict, words: str, project: str | None, workstream: str | None) -> tuple[str, str]:
    """The project and workstream the dashboard's Now screen files an order under: on a "Drive tooling:" order The drive and Drive tooling unless a flag names another, and on his order both named, refused naming the pairs in use, and refused by `one_home` for a name filing its subject elsewhere."""
    if words.startswith(state.TOOLING):
        project, workstream = project or TOOLING_FILING[0], workstream or TOOLING_FILING[1]
    if project and workstream:
        one_home(data, project, workstream)
        return project, workstream
    missing = " and ".join(f"--{field}" for field, value in (("project", project), ("workstream", workstream)) if not value)
    raise DriveError(f"refused, no order added: name {missing}, which file his order on the dashboard's Now screen, reusing a pair in use where it fits: {pairs_in_use(data)}")


def unfiled_refusal(data: dict, order_id: str) -> str | None:
    """Why say may not close his answer order yet: the handler tool added it under no project and workstream, which its handler sets from the card or order his question is about."""
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


def need_thread(order_id: str, order: dict) -> None:
    if not order["thread"]:
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
    return stdin_text() if source == "-" else named_file(source, what).read_text()


def stdin_text() -> str:
    """What is left on stdin, kept in STDIN as well, so a verb run again on the newest install reads what this process already read."""
    STDIN.append(sys.stdin.read())
    return STDIN[-1]


def his_words(link: str, deed: str) -> str:
    if not slack.message(link).get("from_user"):
        raise DriveError(f"{link} is not his message; only his own words {deed}")
    return link


def drive_post_at(link: str, data: dict, order_id: str, sent: str | None = None) -> tuple[dict, bool, tuple[str, str] | None]:
    """The drive's post as Slack holds it, or, for one say just made, from the words it was sent with, reading nothing; whether it is a line to him, in his DM or in a thread; and its thread when the order holds it not yet, which the call naming the order joins to the order's threads: a post at his DM root on an order whose lines go in no thread, one with no thread or whose own thread holds no line to him, is the thread its next lines reply under."""
    posted = slack.posted(link, sent) if sent else slack.message(link)
    if not posted.get("from_claude"):
        raise DriveError(f"{link} is not the drive's post")
    where = (state.CARD_LINK.search(link)[1], posted.get("thread_ts"))
    order = data["orders"][order_id]
    if where == (slack.dm(), None) and not state.promise_thread(order):
        where = (where[0], posted["ts"])
    gained = where if where[1] and where not in slack.order_threads(data, order_id, order) else None
    return posted, where[0] == slack.dm() or bool(where[1]), gained


def pick_parts(order_id: str, order: dict, named: list[str]) -> list[str]:
    listed = ", ".join(f'"{part}"' for part in order["parts"])
    if not named:
        if len(order["parts"]) != 1:
            raise DriveError(f"{order_id} has parts {listed}; name the one this covers with --part")
        return list(order["parts"])
    if unknown := [part for part in named if part not in order["parts"]]:
        raise DriveError(f"{order_id} has no part {', '.join(unknown)}; its parts are {listed}")
    return list(dict.fromkeys(named))


def apart_from_filing(order: dict) -> str:
    """The order apart from its project, workstream and title, so filing or titling it alone leaves the time it last moved, which the dashboard reads as when a done order went live."""
    return json.dumps({field: value for field, value in order.items() if field not in FILING}, sort_keys=True)


def refuse_a_renamed_change(data: dict, order_id: str, parts: list[str]) -> None:
    """A --parts list that adds a name refuses to leave out a change a design listed and no builder delivered, since the part under the new name would lose the design and the builder its name ties it to; a list that only drops parts, delivered or withdrawn by him, passes."""
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
    """Change an order, reworking only the PRs --rework names and naming each open PR card of his in a thread it moves into or is sorted again in, which it leaves up; closing one that owes him a promised line reads its thread and refuses until the line is there or he withdrew the time, closing one done refuses while the bug report it fixes has not heard it is fixed, and --no-options-only, his pick, adds the builder of each change a design on it listed that no builder is on, as drive job report does once a design returns; --incident turns an open order into an incident by turned_incident."""
    name = state.resolve(args.drive)
    withdrawn = his_words(args.withdrawn, "withdraw a promised line") if args.withdrawn else None
    unreported = his_words(args.no_reporter, "take the reporters off an order") if args.no_reporter else None
    data = state.load(name)
    refuse_unreworkable(data, args.rework)
    closing = args.state in state.CLOSED_ORDER_STATES and not withdrawn
    order = find(data["orders"], args.id, "order")
    added = state.reporter(args.reporter) if args.reporter else None
    held = {(kept["channel"], kept["thread"]) for kept in order["reporters"]}
    reporters = [] if unreported else [*order["reporters"], added] if added and (added["channel"], added["thread"]) not in held else order["reporters"]
    watched = state.new_watch(args.watch, state.now()) if args.watch else None
    if args.state == "done" and state.untold_reporters(order | {"reporters": reporters}):
        raise DriveError(untold(args.id, order | {"reporters": reporters}))
    if args.words is not None:
        if not (order["text"].startswith(state.TOOLING) and args.words.startswith(state.TOOLING)):
            raise DriveError(f'--words: refused, only a "{state.TOOLING}" order\'s words change, to words that open "{state.TOOLING}"; an order of his keeps his words verbatim')
        check_tooling_words(name, args.words)
    if args.proof is not None:
        check_proof(args.proof)
    if args.title is not None:
        titled(args.title)
    alerts = alert_links(args.alerts)
    sorted_parts = sorted_as(data, args.id, args.kind, args.part)
    if (order["incident"] if args.incident is None else args.incident) and (order["options_only"] if args.options_only is None else args.options_only):
        raise DriveError(
            f"{args.id}: refused, an incident order is never limited to options, since its codex jobs start at the alert, before he picks:"
            " an incident takes --no-options-only, and an order limited to options --no-incident"
        )
    proof = args.proof or (order["proof"] if args.state == "done" else None)
    if proof and (seen := order_surface(data, order)) and not any(brief.SEEN_PROOF.search(found) for found in brief.proofs(proof)):
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
        "watch": watched,
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
        limited = order["options_only"]
        order.update({field: value for field, value in changes.items() if value is not None})
        designs = [job_id for job_id in order["jobs"] if data["jobs"][job_id]["kind"] == "design" and data["jobs"][job_id]["order"] == args.id] if limited and not order["options_only"] else []
        added = [builder for job_id in designs for builder in state.add_changes(name, data, job_id, next(iter(state.returns(name, job_id)), {"body": ""})["body"])]
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
                say = f" with say(record={args.record})" if report else ""
                raise DriveError(
                    f"{args.id} owes him the line promised for {render.when(state.owed_at(order))}, and no drive post is in"
                    f" its thread since {render.when(order['promised'], seconds=True)}{why}: post it there first{say}, or pass"
                    " --withdrawn <his message> when he called it off"
                )
        if args.state in state.CLOSED_ORDER_STATES:
            order["closed_on"] = report.get("seq")
        elif args.state and order["state"] in state.CLOSED_ORDER_STATES:
            order["closed_on"] = None
        if args.state:
            order["state"] = args.state
        order["alerts"] = list(dict.fromkeys([*order["alerts"], *alerts]))
        if args.parts:
            refuse_a_renamed_change(data, args.id, args.parts)
            order["parts"] = {part: order["parts"].get(part, state.new_part()) for part in args.parts}
        turning = args.incident and not order["incident"]
        if args.incident is not None:
            order["incident"] = args.incident
        for part in sorted_parts:
            order["parts"][part]["answer"] = args.kind == "answer"
        answered = set(sorted_parts or order["parts"]) if args.kind == "answer" else set()
        for job in [data["jobs"][job_id] for job_id in order["jobs"]]:
            if job["order"] == args.id and answered & set(job["parts"]) and state.unstarted_builder(data, job):
                left = [part for part in job["parts"] if part not in answered]
                job.update({"parts": left} if left else {"state": "stopped", "said": f"stopped: drive order set {args.id} --kind answer left it nothing to build"}, updated=state.stamp(moment))
        if args.kind == "build" and sorted_parts and order["kind"] == "answer":
            sort_as_build(order, sorted_parts)
        elif args.kind and not sorted_parts:
            order["kind"] = args.kind
        if args.complex is not None:
            order["complex"] = args.complex
        if args.reporter or unreported:
            order["reporters"] = reporters
        filed = [field for field in FILING if getattr(args, field) is not None]
        for field in filed:
            order[field] = (getattr(args, field) or "").strip() or None
        if {"project", "workstream"} & set(filed) and order["project"] and order["workstream"]:
            one_home(data, order["project"], order["workstream"], args.id)
        turned = turned_incident(name, data, args.id, state.stamp(moment)) if turning else []
        if not filed or args.lined or apart_from_filing(order) != before:
            order["updated"] = state.stamp(moment)
        if args.lined:
            order["last_line"] = order["updated"]
    cards = thread_cards(name, order) if args.rework or (order["kind"], order["thread"]) != aimed else []
    reworked = rework_named_prs(name, args.id, args.rework, cards, f"drive order set {args.id} --rework <PR>")
    parts = [f"{args.id} {render.part_line(data, args.id, part)}" for part in sorted_parts]
    return emit(args, render.order_line(args.id, order) + promised + "".join(f"\n{line}" for line in [*turned, *parts, *render.builder_lines(data, added), *reworked]), {"id": args.id, **order})


def sorted_as(data: dict, order_id: str, kind: str | None, named: list[str]) -> list[str]:
    """The parts --part sorts as --kind names, refused without --kind and, as an answer, while a builder, codex or design job or a drive PR is on one, since that part is work under way, but for a builder still waiting on research with no agent, which the answer stops."""
    if not named:
        return []
    if not kind:
        raise DriveError(f'--part: refused, it sorts a part of {order_id} with --kind answer, or --kind build to make it work again')
    parts = pick_parts(order_id, data["orders"][order_id], named)
    for part in parts if kind == "answer" else []:
        jobs = dict(state.part_jobs(data, order_id, part))
        working = [f"{job['kind']} {job_id}" for job_id, job in jobs.items() if job["kind"] in state.WORK_KINDS and not state.unstarted_builder(data, job)]
        working += [key for key, pr in data["prs"].items() if pr["job"] in jobs]
        if working:
            raise DriveError(f'--part "{part}": refused, {", ".join(working)} works on it, so it is work under way, never an answer: its builder\'s DONE or a merged PR delivers it')
    return parts


def untold(order_id: str, order: dict) -> str:
    """Why an order with reporters may not close done yet: a thread its bug was reported in has not heard it is fixed, or a thread its question was asked in has not heard the answer."""
    unheard = [held["link"] for held in state.untold_reporters(order)]
    links, there = " and ".join(unheard), "that thread" if len(unheard) == 1 else "each of those threads"
    if order["kind"] == "answer":
        heard = (
            f"its question was asked in {links}, and no note of its answer there is recorded. Once the answer is posted, the"
            f" handler of a report on it posts the answer in one plain line in {there} with slack_reply"
        )
    else:
        heard = (
            f"its bug was reported in {links}, and no fix-live note there is recorded. Once drive order set {order_id} --proof"
            f" <the check> keeps the proof its fix is live, the handler of a report on it posts one plain line in {there} with"
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


def order_surface(data: dict, order: dict) -> str | None:
    """Where he sees or uses an order's result, by the first of its jobs' briefs naming a surface; None when none does."""
    return next(filter(None, (brief.surface(data["jobs"][job_id]) for job_id in order["jobs"])), None)


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
    """The attrs of a record, read from cci: a report's close, part, job and catch_up, with its seq, the lane that wrote it as its writer, when as written, and for a correction the record it corrects."""
    try:
        record = json.loads(reads.run(["cci", "show", seq, "--json"], Path.cwd()))
    except reads.ReadError as error:
        raise DriveError(f"record {seq} could not be read: {error}") from None
    corrects = {"corrects": str(record["re"])} if record["kind"] == "correction" and record.get("re") else {}
    return record["fields"].get("attrs", {}) | {"seq": str(record["seq"]), "writer": record["lane"], "written": record["at"]} | corrects


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
    with state.edit(name) as data:
        data["orders"][args.id]["parts"][undone]["posted"] = None
    return emit(args, f'{args.id} "{undone}": not delivered', {"id": args.id, "part": undone, "posted": None})


def answered_by(name: str, order_id: str, part: str, link: str) -> tuple[str, dict]:
    """Deliver a part of the order from the session its watch lines reach, posting nothing: words he reads by the drive's post that already answered it, wherever it was made, and work he ordered, every part of a build order or an incident and a part of an order of no kind that a builder, codex or design job holds or held or a design lists, by the PR GitHub reads merged that delivered it, since no post delivers work."""
    data = state.load(name)
    order = data["orders"][order_id]
    [part] = pick_parts(order_id, order, [part])
    if not (work := state.work(data, order_id, part)):
        drive_post_at(link, data, order_id)
    elif not state.PR_URL.search(link):
        raise DriveError(f'--part "{part}": refused, ' + state.UNPOSTED_WORK.format(order=order_id, part=part, holder=f"{state.holding_lane(data, order)}@{name}"))
    elif (read := merged_read(link))["state"] != "MERGED":
        raise DriveError(f'--part "{part}": refused, GitHub reads {state.pr_key(link)} {read["state"].lower()}, and only a merged PR delivers work he ordered')
    with state.edit(name) as data:
        data["orders"][order_id]["parts"][part]["posted"] = link
    how = "the merged PR that delivered it" if work else "the post that answered it"
    return f'{order_id} "{part}": posted {link}, {how}', {"id": order_id, "parts": [part], "posted": link}


def merged_read(link: str) -> dict:
    key = state.pr_key(link)
    reads, refused = github.fetch([key])
    if refused:
        raise DriveError(f"{key}: could not read it from GitHub to check it merged: {refused}")
    return reads[key]


def order_told(args: argparse.Namespace) -> int:
    """Record the one note the thread one of an order's reporters asked in gets, a bug report's once the fix is proven live and a question's once the answer is posted: the drive's post in that thread, or, when the bot cannot post there, its post in the group DM of that reporter, him and the bot."""
    name = state.resolve(args.drive)
    data = state.load(name)
    order = communicators_order(data, args.id)
    if not order["reporters"]:
        raise DriveError(f"{args.id} names no one to tell: drive order set {args.id} --reporter <their message's permalink> names them")
    asked = order["kind"] == "answer"
    if not state.may_tell(order):
        if asked:
            raise DriveError(f"{args.id}: refused, a part of its answer is not posted yet, so a note now would come before the answer: post it first")
        raise DriveError(
            f"{args.id}: refused, it keeps no proof that its fix is live, so a note now would come before the fix:"
            f" drive order set {args.id} --proof <the check> records it once the fix is live"
        )
    posted, _ = slack.message_and_root(args.link)
    channel = state.CARD_LINK.search(args.link)[1]
    if args.group_dm and not (posted.get("from_claude") and slack.group_dm(args.group_dm) == channel):
        raise DriveError(f"{args.link} is not the drive's post in the group DM of {args.group_dm}, him and the bot, the one cc-slack opens for channel_id {args.group_dm}")
    place = told_reporter(args, order, (channel, posted.get("thread_ts")) if posted.get("from_claude") else None)
    if told := order["reporters"][place]["told"]:
        heard = "the thread its question was asked in heard the answer" if asked else "the thread its bug was reported in heard it is fixed"
        raise DriveError(f"{args.id}: refused, {heard} once, in {told}, and hears it only once")
    with state.edit(name) as data:
        data["orders"][args.id]["reporters"][place]["told"] = args.link
    where = f"told in the group DM of {args.group_dm}, him and the bot" if args.group_dm else "told in the reporter's thread"
    return emit(args, f"{args.id}: {where}, {args.link}", {"id": args.id, "told": args.link, "group_dm": args.group_dm})


def told_reporter(args: argparse.Namespace, order: dict, where: tuple[str, str | None] | None) -> int:
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
    if len(unheard := [place for place, held in enumerate(order["reporters"]) if not held["told"]]) > 1:
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
    if state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID")) != "communicator":
        raise DriveError(
            "only the communicator records a post in an order's thread or takes back a part's mark: send the post's facts as a report record"
            f" to communicator@{data['name']}, its part or close attr naming what it delivers, and the communicator's handler posts it and records it;"
            f" a part a post of the drive already answered, or work a merged PR delivered, is marked from the {state.holding_lane(data, order)}@{data['name']} session,"
            f' the one its ORPHAN lines reach, with drive order posted {order_id} <that post\'s or PR\'s link> --part "<part>", with or without --record, posting nothing'
        )
    return order


def report_refusal(data: dict, order_id: str, report: dict) -> str | None:
    """Why a report may deliver or close nothing on an order, the one check say, order posted and order set --state done share: another report already closes the order, or the drive's session that wrote it does not hold the order. Only the communicator's side posts, so the refusal names the report its handler passes instead: the holder's, which on the communicator's own order the handler writes itself from the communicator's lane."""
    if refused := state.second_close(data, order_id, report):
        return refused
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


def post_order(data: dict, session: str | None, post: dict, report: dict) -> str | None:
    """The one order a say post is recorded on, which say decides and no caller names: a post carrying a record goes on the order its report is about; a handler's post with none goes on the order of what the handler holds, the order its record is about, else the order of his message; an edit, a card, a PR card, his catch-up, an answer in a teammate's watched thread and a post from a session that is no handler go on none."""
    if post.get("edit") or post.get("card") or post.get("tap_prs") or report.get("catch_up") or report.get("watch"):
        return None
    if post.get("record"):
        return state.report_order(data, report)
    if not (handler := state.handler_id(data, session)):
        return None
    held = data["handlers"][handler]["holds"] or {}
    return (held.get("record") and state.report_order(data, agents.shown(held["record"])["fields"].get("attrs", {}))) or (
        held.get("message") and state.message_order(data, state.slack_message(held["link"])[0], held["thread"], held["message"])
    ) or None


def claim_close(name: str, report: dict) -> list[str]:
    """Claim each order the report closes for it under the drive's lock, just before say posts it, so of two reports closing one order at once only the first posts and second_close refuses the other; the orders claimed, which a post that fails gives back, never one the watch's post of this report already marked."""
    if not state.closed_orders(report):
        return []
    with state.edit(name) as data:
        closes = [order_id for order_id in state.closed_orders(report) if order_id in data["orders"] and data["orders"][order_id]["closed_on"] != report["seq"]]
        if refused := next(filter(None, (state.second_close(data, order_id, report) for order_id in closes)), None):
            raise DriveError(f"nothing posted: {refused}")
        for order_id in closes:
            data["orders"][order_id]["closed_on"] = report["seq"]
    return closes


def unclaim_close(name: str, closes: list[str]) -> None:
    with state.edit(name) as data:
        for order_id in closes:
            data["orders"][order_id]["closed_on"] = None


def reported_parts(data: dict, order_id: str, order: dict, report: dict, items: list[tuple[str | None, str]]) -> list[str]:
    """The parts of the order a post delivers: each item of its report's part attr names on this order when it is words he reads, never work he ordered, or each part of an order its report closes. A post whose report names neither part nor close, whatever job it names, or that carries no report is a progress line that delivers none, on an order of one part as on one of several."""
    closing = order_id in state.closed_orders(report)
    if named := [part for on, part in items if on == order_id]:
        return [part for part in pick_parts(order_id, order, named) if closing or not state.work(data, order_id, part)]
    return list(order["parts"]) if closing else []


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


def record_post(name: str, order_id: str, link: str, report: dict, sent: str | None = None) -> tuple[str, dict]:
    """Record the drive's post on the order it went out on and on each other order an item of its report's part attr names, so one post in a thread two orders share delivers on both; an item naming no order is a part of the order the post went out on, on every order it is recorded on. A post say just made passes the words it sent, so its own post is not read back from Slack."""
    items = state.part_items(report, order_id)
    others = dict.fromkeys(on for on, _ in items if on != order_id)
    first, *rest = [record_on(name, each, link, report, items, sent) for each in [order_id, *others]]
    return "; ".join(line for line, _ in [first, *rest]), first[1] | ({"also": [fields for _, fields in rest]} if rest else {})


def record_on(name: str, order_id: str, link: str, report: dict, items: list[tuple[str | None, str]], sent: str | None = None) -> tuple[str, dict]:
    """Record the drive's post on one order: the promised time it keeps, the time its words promise his next line by but on an incident, whose next line the watch owes five quiet minutes after its last once it has news, the order's last line, the parts its report's items deliver there, and its thread among the order's, with when it joined, when the order holds it not yet, or as the order's own thread when it has none and the post started that thread at his DM root, so its every later line replies under its first."""
    data = state.load(name)
    order = find(data["orders"], order_id, "order")
    if refused := report_refusal(data, order_id, report):
        raise DriveError(refused)
    delivered = reported_parts(data, order_id, order, report, items)
    posted, ours, gained = drive_post_at(link, data, order_id, sent)
    at = slack.sent_at(posted)
    line = state.stamp(at)
    closing = order_id in state.closed_orders(report)
    promised = promised_due(posted.get("text") or "", at) if ours and not order["incident"] else None
    kept = ""
    with state.edit(name) as data:
        order = data["orders"][order_id]
        rooted = gained == (slack.dm(), posted["ts"]) and not order["thread"]
        if rooted:
            order["thread"] = list(gained)
        elif gained:
            state.join_thread(order, gained, line, "posted")
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
    away = "" if ours else ", at the root of another channel, so it is no line to him and keeps no promised time"
    joined = f", in thread {gained[1]}, now {'its own thread' if rooted else 'one of its threads'}" if gained else ""
    return f"{order_id} {said} {link}{away}{joined}{kept}", {"id": order_id, "parts": delivered, "posted": link, "due": order["due"]}


def promised_due(text: str, at: datetime) -> datetime | None:
    """The time a post made at `at` promises his next line by: the first clock time its words put after next line, update, post or summary whose time of day nearest the post is after it, read with AM or PM as written, else on either half of a 12-hour clock, so at 9:29 PM "next line by 9:45" is 9:45 PM and "next line by 7:30" is past, and a time after any other words, such as "due by 2:37", promises nothing."""
    for said in PROMISED_TIME.finditer(limits.masked(text)):
        hour = int(said[1])
        hours = [hour % 12 + (12 if said[3].upper() == "P" else 0)] if said[3] else [hour % 12, hour % 12 + 12] if 1 <= hour <= 12 else [hour]
        if (due := state.nearest(hours, int(said[2]), at)) > at:
            return due
    return None


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
    refuse_a_mark_off_a_builder(args.kind, args.builds_nothing)
    lands = list(dict.fromkeys(map(state.pr_key, args.lands)))
    found = check_waits(name, args.waits)
    unreleased = args.release and (brief.release_refusal(args.release) or brief.approval_refusal(args.release, state.load(name)))
    with state.edit(name) as data:
        order = find(data["orders"], args.order, "order")
        parts = pick_parts(args.order, order, args.part)
        refuse_a_shared_at_once_part(parts)
        session = os.environ.get("CLAUDE_CODE_SESSION_ID")
        live = sharing(data, args.order, parts)
        if refused := (
            state.held_elsewhere(data, args.order, session, bool(args.release))
            or unreleased
            or (args.model == "astra" and not re.search(r"\bastra\b", order["text"], re.IGNORECASE) and f"--model astra runs only on an order whose words name astra, which {args.order}'s do not")
            or proven_again(data, args.order, args.kind, args.goal)
            or unlandable(data, lands)
            or state.job_wait_refusal(data, args.waits)
            or brief.job_refusal(name, data, args.order, args.kind, parts, bool(lands or args.release), args.waits)
            or second_carrier(order, args.kind, bool(lands or args.release), args.waits, live)
        ):
            raise DriveError(f"refused, no job added: {refused}")
        order["owner"] = order["owner"] or state.session_role(data, session)
        beside = [f'{other_id} {other["kind"]} {other["state"]} on {shared}: {other["goal"][:60]}' for other_id, other, shared in live]
        job_id = state.next_id(data["jobs"], "j")
        job = data["jobs"][job_id] = state.new_job(args.order, args.kind, args.goal, parts, created) | {
            "agent": args.agent,
            "where": "remote" if args.remote else "local",
            "repo": args.repo,
            "branch": args.branch,
            "worktree": args.worktree,
            "waits": view.stored_waits(data, [state.change_wait(data, wait) for wait in args.waits], found),
            "builds_nothing": args.builds_nothing,
            "model": args.model,
        } | ({"brief_flags": {"release": args.release}} if args.release else {})
        state.hold_unstarted(job)
        if crowd := args.kind == "builder" and not args.remote and not args.agent and not args.waits and place.crowded(data, job_id, state.now()):
            job["where"] = "remote"
        order["jobs"].append(job_id)
        builder_id = waiting_builder(name, data, job_id, created)
        order["updated"] = created
        state.end_carried_designs(data, args.order, created)
        named = state.named_prs(data, args.order, args.goal)
    if named:
        with_cards(name, data)
    history = [line for key in named for line in render.pr_history(data, key, job_id)]
    waiting = [f"{job_id} {job['said']}: start no agent for it until the watch's FREE line says its last wait ended"] if args.waits else []
    waiting += render.builder_lines(data, [builder_id] if builder_id else [])
    if crowd:
        waiting.append(
            f"{job_id} starts on a Sprite at once, since {crowd}: drive job launch {job_id} <brief>; one whose brief declares a step only this Mac runs, on {place.MAC_LINES}, waits its turn for a slot here once its agent starts"
        )
    started = state.after_save(name, lambda: started_release(name, job_id, args.release)) if args.release else []
    return job_id, job | {"builder": builder_id, "beside": beside, "history": history}, [*started, *waiting, *beside, *history]


def started_release(name: str, job_id: str, command: str) -> list[str]:
    """Start the release a job's --release names from the top coordinator's checkout, leave it running once `ci release` names the build it started, and brief the job on that build; a start naming none stops the job with what it said."""
    said: list[str] = []
    answer: dict = {}
    with subprocess.Popen([*simple_commands(command)[0], "--json"], cwd=state.load(name)["roles"]["coordinator"]["cwd"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as release:
        for line in release.stdout:
            said.append(line)
            if answer := release_answer(said):
                break
        release.terminate()
    if not (build := answer.get("build", {}).get("url")):
        why = answer.get("refused") or render.cut("".join(said).strip(), 600) or f"exit {release.returncode}"
        with state.edit(name) as data:
            data["jobs"][job_id].update(state="stopped", said=f"its release did not start: {why}", updated=state.stamp())
        raise DriveError(f"{job_id} added and stopped, since its release did not start: {why}")
    with state.edit(name) as data:
        data["jobs"][job_id]["release_build"] = build
    line, _ = write_brief(name, job_id, {})
    return [f"{job_id} started `{command}` as {build} and left it running: its agent follows it with `{answer['follow']}` and reads its approve hold", line]


def release_answer(said: list[str]) -> dict:
    """The JSON object `ci release start --json` answers with: its first line once it stays with the build it started, else what it printed from its first line opening an object, as a refusal prints it; empty until that object closes."""
    opened = next((at for at, line in enumerate(said) if line.startswith("{")), len(said))
    try:
        return json.loads("".join(said[opened:])) if opened < len(said) else {}
    except json.JSONDecodeError:
        return {}


def refuse_a_mark_off_a_builder(kind: str, builds_nothing: bool | None) -> None:
    if builds_nothing and kind != "builder":
        raise DriveError("--builds-nothing marks a builder that holds no slot of this Mac's builder cap; no other kind of job holds one")


def sharing(data: dict, order_id: str, parts: list[str], job_id: str | None = None) -> list[tuple[str, dict, str]]:
    """Each live job but job_id delivering any of these parts of the order, beside the parts it shares, quoted."""
    return [
        (other_id, other, ", ".join(json.dumps(part, ensure_ascii=False) for part in shared))
        for other_id in data["orders"][order_id]["jobs"]
        if other_id != job_id
        and (other := data["jobs"][other_id])["state"] in ("running", "waiting")
        and (shared := [part for part in parts if part in state.job_parts(other, order_id)])
    ]


def second_carrier(order: dict, kind: str, lands: bool, waits: list[str], live: list[tuple[str, dict, str]]) -> str | None:
    """Why a builder or codex job may not join a part a running builder or codex job carries: both would build one change at once and race on its branch, so the event goes to the job still at work on it. A job that returned, such as one waiting on its PR, hands its next event to a fresh builder added beside it; a builder landing PRs already built builds nothing, and one waiting on that job or a PR it opened stacks on its change, so each starts beside it too, as does an incident's second job on a brief whose clock ran out, which races the first by design."""
    if kind not in state.CHANGE_KINDS or lands or order["incident"]:
        return None
    return next(
        (
            f"{other_id} {other['kind']} {other['state']} carries {shared} already, so a second {kind} would race it on one change: send it the event with"
            + (f' drive job resume {other_id} "<the event>"' if other.get("dispatch") or other.get("agent") else f" the brief it starts on, drive job launch {other_id} <brief>")
            + f", or stop it with drive job set {other_id} --state stopped before adding the job that replaces it"
            for other_id, other, shared in live
            if other["kind"] in state.CHANGE_KINDS and other["state"] == "running" and other_id not in waits and not any(state.pr_key(url) in waits for url in other["prs"])
        ),
        None,
    )


def proven_again(data: dict, order_id: str, kind: str, goal: str) -> str | None:
    """Why a builder whose goal names a merged PR may not start on a done order: its fix was proven live already, by the job state.prover names, else by the proof the order keeps."""
    order = data["orders"][order_id]
    if kind != "builder" or order["state"] != "done" or not (merged := [key for key in state.named_prs(data, order_id, goal) if data["prs"][key]["merged_sha"]]):
        return None
    named = ", ".join(merged)
    held = f"{proved} proved {named} live" if (proved := state.prover(data, order_id, merged)) else f"its proof is {order['proof']}" if order["proof"] else "it closed"
    return f"{order_id} is done and {held}, so no builder proves {named} live on it again"


def waiting_builder(name: str, data: dict, reader_id: str, created: str) -> str | None:
    """The builder a build order's reader is added with, waiting on it, for each of the reader's parts no builder, codex or design job carries, its goal building those parts and never the reader's question, wherever a builder there starts only on research and job_refusal lets it wait on that reader, so its work is never research's alone while it reads."""
    reader = data["jobs"][reader_id]
    if reader["kind"] != "reader" or not (uncarried := [part for part in reader["parts"] if not state.carriers(data, reader["order"], part)]):
        return None
    if not brief.waits_on_research(data["orders"][reader["order"]], "builder", uncarried) or brief.job_refusal(
        name, data, reader["order"], "builder", uncarried, False, [reader_id]
    ):
        return None
    builder_id = state.next_id(data["jobs"], "j")
    builder = data["jobs"][builder_id] = state.new_job(reader["order"], "builder", state.parts_goal(uncarried), uncarried, created) | {"waits": [reader_id]}
    state.hold_unstarted(builder)
    data["orders"][reader["order"]]["jobs"].append(builder_id)
    return builder_id


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
    """Change a job; a state it already holds changes nothing, stopped is the one state set by hand, done only on a design a builder carries, and any other --state is refused with nothing changed, since the job's return sets it, but for a done or failed job given a part it does not hold, which runs, since it has not returned on that part; an order it carries reworks only the PRs of the job --rework names, and each other open card of his on the job's PRs is named and left up."""
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
        fix_kept(known, args.id)
    refuse_a_mark_off_a_builder(found["kind"], args.builds_nothing)
    changes = {"agent": args.agent, "state": args.state, "said": args.said, "branch": args.branch, "due": job_clock(args.id, args.due), "builds_nothing": args.builds_nothing}
    urls = dict.fromkeys(state.pr_url(state.pr_key(url)) for url in args.pr)
    if refused := state.job_wait_refusal(known, args.waits):
        raise DriveError(refused)
    found = check_waits(name, args.waits)
    told_him(name, args)
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
            state.join_card(data, args.id, args.card)
        waits = merged_waits(data, job, [state.change_wait(data, wait) for wait in args.waits], args.no_waits, found)
        state.rewait(job, [wait for wait in waits if not (job.get("builds_nothing") and wait == state.SLOT_WAIT)])
        state.hold_unstarted(job)
        held = {order_id: set(state.job_parts(job, order_id)) for order_id in [job["order"], *args.carries]}
        if args.carries:
            if args.parts:
                raise DriveError("--parts replaces the parts of the job's own order; name a carried order's parts with --part")
            if args.part and len(args.carries) > 1:
                raise DriveError("--part names the parts of one carried order; carry one order a call to name its parts")
            for order_id in args.carries:
                if order_id == job["order"]:
                    raise DriveError(f"{args.id} is {order_id}'s own job; add its parts with --part")
                carried = find(data["orders"], order_id, "order")
                parts = pick_parts(order_id, carried, args.part)
                if refused := second_carrier(carried, job["kind"], False, job["waits"], sharing(data, order_id, parts, args.id)):
                    raise DriveError(f"{args.id}: refused, nothing changed: {refused}")
                state.carry(data, args.id, order_id, parts)
        elif args.part or args.parts:
            named = pick_parts(job["order"], data["orders"][job["order"]], args.parts or args.part)
            job["parts"] = named if args.parts else list(dict.fromkeys([*job["parts"], *named]))
            refuse_a_shared_at_once_part(job["parts"])
        if job["state"] in ("done", "failed") and any(set(state.job_parts(job, order_id)) - parts for order_id, parts in held.items()):
            job["state"] = "running"
        if args.agent and args.agent == job.get("dispatch"):
            job.update(lane, where="remote", agent=None, handed_to=None)
        elif lane:
            job.update(lane, where="remote", agent=None)
            state.start(job)
        elif args.agent:
            job.update(where="local", session=os.environ.get("CLAUDE_CODE_SESSION_ID"))
            if args.agent != known["jobs"][args.id]["agent"]:
                state.start(job)
            superseded.hear_start(data, args.id)
        if args.repro:
            job["repro"] = keep_repro(name, args.id, args.repro)
        for url in urls:
            if (key := state.pr_key(url)) in data["prs"]:
                state.take_pr(data, args.id, key)
            elif url not in job["prs"]:
                job["prs"].append(url)
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
    """Keep the card on the job's newest question it draws, found by its waits id or its link, so a question that took the card by its `Replaces:` line holds it ahead of the one it folded in, else on a question of its own, so each card a job asked on stays open until his answer to it."""
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
        question = state.new_question(None, shown and shown["question"], state.stamp())
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
    """Record the card cc-slack drew under the waits id as the card of each question asked under it, its thread joining each order of the job asking it, and point each wait of a job or PR on a card it was drawn on before at its waits id: a card asked again under its waits id is redrawn where it sits, so a wait moves only off an older copy cc-slack retired for it."""
    from drive import view

    moved = asked - {view.message_key(url)}
    with state.edit(name) as data:
        for job_id, job in data["jobs"].items():
            for question in job["questions"]:
                if question["waits"] == waits:
                    question["card"] = url
                    state.join_card(data, job_id, url)
        for owner in [*data["jobs"].values(), *data["prs"].values()]:
            state.rewait(owner, list(dict.fromkeys(waits if view.message_key(wait) in moved else wait for wait in owner["waits"])))


def job_ask(args: argparse.Namespace) -> int:
    """Keep a job's ASK in the drive once it is in the shape he reads, each block in its own file; from a file, stdin with -, or with neither a remote lane's latest report: each block its `ASK <file> [<file>…]` pointer names, else the report itself, one opening `DESIGN <question>`, a reader's return, read as the ASK it puts to him. Keeping it draws its card in the same call, under the waits id it mints for the question, and prints the file and the card, or that a card that did not draw goes up on the watch's next read. A block asking again what one of the job's questions his answer has not closed asks, or with --redraws asking in new words what the card it names asks, open or withdrawn, the job's own or one of another job on its orders that it takes over, takes that question's place and redraws its card under its waits id, and one asking what another job's open question asks is refused; one whose `Replaces:` line names an open question's file, built on that question and asking both choices, takes that question's card and waits id the same way and keeps it open, folded into this one. With --drop, throw away a question the job kept with no card drawn for it."""
    if args.id is None:
        raise DriveError("name the job whose ASK this keeps: drive job ask <j> <file>; drive job ask <j> --check <file> runs every check it runs and keeps nothing")
    name = state.resolve(args.drive)
    if args.check:
        text = limits.ask_block(named_text(args.check, "ASK block file"))
        if not settled.answered(state.load(name), asked_title(text)):
            with tempfile.TemporaryDirectory(prefix="drive-ask-check-") as scratch:
                checked_ask(name, args.id, text, args.unsettled, args.redraws, Path(scratch))
        print("ready")
        return 0
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
    """Throw away a question the job kept with no card drawn for it, such as one refused for an answer of his that settles it: it leaves the job's questions, so nothing counts it open, and its file is deleted unless another of the job's questions keeps it; a question on a card is refused, since only his answer to that card closes it. A file of the job's whose question a later ask of the job replaced, as drive job ask printed its path, is deleted alone. A question a `Replaces:` line folded into the dropped one asks on its own again. Its title, and whether a later ask had replaced it."""
    asks = state.drive_dir(name) / "asks"
    with state.edit(name) as data:
        job = find(data["jobs"], job_id, "job")
        if (question := state.kept_in(job, file)) is None:
            if file.parent.resolve() != asks.resolve() or not file.name.startswith(f"{job_id}-") or not file.exists():
                raise DriveError(f"{file} keeps no question of {job_id}; drive status {job_id} names the file each of its questions is kept in")
            title = limits.ask_block(file.read_text()).splitlines()[0].removeprefix("ASK ").strip()
            file.unlink()
            return title, True
        if question["card"]:
            ended = f'ended {closed["how"]} there, so nothing counts it open' if (closed := question["closed"]) else "which only his answer closes"
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
    if state.kept_in(job, file) is None:
        file.unlink(missing_ok=True)
    return question["title"], False


def asked_title(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "").removeprefix("ASK ").strip()


def asked_unsettled(job: dict, text: str, unsettled: str | None) -> str | None:
    """The --unsettled an ASK is asked under: the one given, else the one the job's kept question with its title carries, so a reader's DESIGN report asked again keeps it."""
    if unsettled is not None:
        return unsettled
    title = asked_title(text)
    return next((question["unsettled"] for question in job["questions"] if question["unsettled"] and state.same_question(question["title"], title)), None)


def checked_ask(name: str, job_id: str, text: str, unsettled: str | None, redraws: str | None, pictures: Path) -> tuple[limits.Question, str, list[dict]]:
    """The question the job's ASK block puts to him, asked under the --unsettled `asked_unsettled` reads, the words of its `Holds:` line his card holds, and the recorded answers of his its job's brief binds or the block cites, once every check `drive job ask <j>` runs passes but Jev's read of it against his words: refused naming at once every way its words, the card cc-slack draws from them and the job it is asked for are out of shape, each rule one entry of the list, its words counted as his card draws them, each reference in the summary filled as say fills it, an {order:<o>} as the opening of his order's words; then each Show me picture drawn under pictures, refused with why one does not draw; then Jev's read of whether its options differ in how a screen looks, by `limits.unshot`. `drive job ask <j> --check` and `keep_ask` both check here, so a block one passes the other passes up to Jev's read of his words."""
    from drive import view

    data = state.load(name)
    job = find(data["jobs"], job_id, "job")
    orders = [job["order"], *job["carries"]]
    unsettled = asked_unsettled(job, text, unsettled)
    asked, problems = limits.question_problems(text, unsettled=unsettled, hurting=any(state.hurts_now(data, data["orders"][order_id]) for order_id in orders))
    if (recorded := recorded_answers(data, job, text)) and len(asked.options) == limits.OPTION_COUNT.stop - 1:
        problems.append(
            f'its brief binds or its block cites his recorded answer {recorded[0]["answer"]}, so his card adds the option "{limits.ALREADY_ANSWERED}",'
            f" and a card draws at most {limits.OPTION_COUNT.stop - 1}: keep {limits.OPTION_COUNT.stop - 2} options of your own"
        )
    try:
        found = refs.resolved(name, "\n".join(asked.summary))
    except DriveError as error:
        problems.append(str(error))
    else:
        if (drawn := limits.card_words(replace(already_answered(asked, recorded), summary=[refs.slack(line, found, None) for line in asked.summary]).card(name, ""))) > limits.QUESTION_WORDS:
            problems.append(
                f"the ASK's question, summary and options are {drawn} words as his card draws them, each reference in the summary drawn as"
                f" the words that name it, an {{order:<o>}} as the first {view.TITLE_CHARS} characters of his order, and at most"
                f" {limits.QUESTION_WORDS} in all: give a reference a few words of its own, as {{order:<o>|<a few words>}}, or cut the summary"
            )
        linked = {state.pr_key(url[0]) for url in state.PR_URL.finditer("\n".join([*asked.summary, *(ref["url"] for ref in found.values() if ref["kind"] == "pr")]))}
        if unlinked := [pr["url"] for key, pr in data["prs"].items() if pr["state"] == "open" and job_id in state.pr_jobs(data, pr) and key not in linked]:
            problems.append(
                f"{job_id} has {', '.join(unlinked)} open, which its ASK's summary does not link, so his card would describe its work as if no PR"
                " were up: link each in the summary by what it does, as <url|what it does>"
            )
    if typed := limits.typed_time("\n".join([asked.title, *asked.summary, *asked.show_me, *asked.options, *asked.hints.values()]), data):
        problems.append(f"his card draws these words, and {typed}")
    said = "\n".join([asked.title, *asked.summary]).casefold()
    if unnamed := [pr["port_of"] for pr in data["prs"].values() if pr["job"] == job_id and pr.get("port_of") and f"port of #{pr['port_of']}" not in said]:
        problems.append(
            f'{job_id}\'s PR is a port of #{unnamed[0]}: its ASK\'s summary says "port of #{unnamed[0]}", as the text of that PR\'s link,'
            f" and what in the target tree forces a choice #{unnamed[0]} did not make, such as what that tree lacks"
        )
    parts = [part for order_id in orders for part in data["orders"][order_id]["parts"]]
    holds = asked.holds and state.held_words(data, asked.holds, orders)
    if holds and job["kind"] != "design" and not state.names_part(holds, parts):
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
    if asked.replaces:
        try:
            replacing(data, asked.replaces)
        except DriveError as error:
            problems.append(str(error))
    if refused := slack.draw_refusal(asked.card(name, f"{name}:{job_id}-0")):
        problems.append(f"the ASK's card is out of shape, so cc-slack would not draw it: {refused}")
    if problems:
        raise DriveError(limits.out_of_shape(problems))
    picture.drawn(pictures, asked.show_me)
    if unshown := limits.unshot(asked):
        raise DriveError(unshown)
    return asked, holds, recorded


def keep_ask(name: str, job_id: str, text: str, unsettled: str | None, redraws: str | None = None) -> tuple[Path, limits.Question | None, dict, str]:
    """Keep the job's ASK block, or the ASK a DESIGN return puts to him, in its own file once it is in the shape he reads and the words his card draws type no clock time for work already done, in place of the job's open question asking the same, or of each question `superseded.redrawn` finds holding the card `redraws` names by its waits id, the job's own or another job's on one of its orders, open or withdrawn, the job's own leaving the job and another job's staying on it folded into this one, set the job waiting on it, unless it is a builder, which keeps running to build the question's recommended option and return BUILT, and draw his card on it in the same call, as drive pr add draws a PR card, under the waits id of the question it takes the place of, the one `redraws` names first, else one minted for it: every ask of one question draws under its one waits id, which cc-slack redraws where it sits, new words included, the question taking the card of the one it takes the place of, so a redraw that fails leaves it on that card, which his answer there closes and --drop refuses, and a first draw that fails leaves it undrawn for the watch's next read, which draws it. The question keeps the words of its `Holds:` line, with the parts each PR the drive tracks on the job's orders that they name serves appended, which hold from landing, among the PRs of jobs working a change they name, only those of the asking job or its design's builder and those they name by link, #N or job id, refused when they name no part of the job's orders, unless a design job names the changes its `Change:` lines are about to make parts; asked again with no --unsettled, as a reader's DESIGN report re-asks it, it keeps the --unsettled its kept question with that title carries. One he already answered, the same words asked on a card he tapped or wrote on or under, or under a record keeping his words, is kept settled by that answer before any check of its shape or its card, by `keep_settled`, draws no card and leaves the job as it was, so it goes straight to building on his words, and the session holding its order hears so in one record; no answer he gave another question settles it. One he has not answered Jev reads, in one call, against his words on its order, the words of his the drive quotes in it and each message of his in its thread, without the lines he quoted, and the recorded answers of his ccn ranks nearest to the block, with his rulings beside them, and each option his words rule out, each amount or choice they name with no tap and each ruling the card breaks is printed to the agent and refuses nothing, by his pick 320b7a7, his ruling on root causes read once against the card's recommended option, the recorded answers its job's brief binds or its block cites read beside them, with the lines of its findings naming one; the recorded answer or ruling of his Jev picks for its recommended option, the cited answer the card says that option goes against first, keeps the card unsettled as `<id>: Jev reads the recommended option as breaking it` when no --unsettled names words of his, asking him to lift that one, so it holds its PR on an incident too, as --unsettled naming words of his does. One whose job's brief binds, or whose block cites, a recorded answer of his draws its card with an Already answered option, his words under it, so his tap closes it on those words. A block whose summary links no PR the drive tracks open on the job is refused, so his card never says no PR while one is up. One the card `question_card` draws refuses stays kept as the job's pending question with its refusal and draws no card, so the watch says the job waits on a card never posted until it asks again or drops it. Each question of the job `asked_before` names is replaced, the one its file keeps included, so a file keeps one question and a question once kept settled on another answer of his draws its card when asked again. A block whose `Replaces:` line names an open question's file, a question built on that one asking both choices, takes that question's card and waits id and leaves it open, folded into this one, so drawing its card redraws that card and his answer to this one ends both; a question folded so, asked again, stays folded and draws no card. A job whose card another job's question took, by `took`, folds an ask of it into that question and draws no card, asked in that question's words or its own or in other words Jev reads as asking it again. Asked in other words, Jev reads it in that same call against each open card `repeating` finds, and an ask Jev reads as asking again what some of them ask takes the newest of those cards, by its Slack time, and the others fold into it, their cards shown asked again by `post_ask`; asked again in its own words it is read against none. What drive job ask prints: the file, then where his card stands."""
    from drive import view

    text = f"{limits.ask_block(text).strip()}\n"
    data = state.load(name)
    job = data["jobs"][job_id]
    title = asked_title(text)
    unsettled = asked_unsettled(job, text, unsettled)
    kept = state.drive_dir(name) / "asks" / f"{job_id}-{state.ask_hash(text)}.md"
    if answer := settled.answered(data, title):
        return keep_settled(name, job_id, text, kept, title, unsettled, answer)
    asked, holds, recorded = checked_ask(name, job_id, text, unsettled, redraws, state.drive_dir(name) / "pictures")
    cited = [{"id": answer["answer"], "asked": answer["asked"], "said": answer["words"][: settled.BODY_CHARS]} for answer in recorded]
    case = limits.ask_case(text, asked, frozenset(answer["id"] for answer in cited))
    cards = {} if any(other == job_id for other, _ in asking(data, asked.title)) else {question["waits"]: question["title"] for question in repeating(data, job_id, asked.title, redraws)}
    broken, repeated = settled.judged(case, asked, [*settled.order_words(data, job["order"]), *settled.matched(text, data["roles"]["coordinator"]["cwd"])], cited, cards)
    if broken and not unsettled:
        unsettled = f"{broken['id']}: {settled.MARKED}"
        print(settled.UNSETTLED.format(unsettled=unsettled, id=broken["id"], asked=render.cut(broken["asked"], 200)), file=sys.stderr)
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(text)
    generic = superseded.generic(data)
    with state.edit(name) as data:
        job = data["jobs"][job_id]
        twins = [question for other, question in asking(data, asked.title) if other == job_id]
        asker, target = replacing(data, asked.replaces) if asked.replaces else (None, None)
        if any(target is twin for twin in twins):
            target = None
        stale = asked_before(job, asked.title, kept, generic)
        own = {id(other) for other in job["questions"]}
        redrawn = superseded.redrawn(data, job_id, redraws) if redraws else []
        matched = [(other, question) for other, each in data["jobs"].items() for question in view.open_questions(each) if question["waits"] in repeated and not question["replaced"]]
        taken = next((question for other, question in [*asking(data, asked.title), *matched] if other != job_id and took(data, job_id, question)), None)
        newest = max((question for _, question in matched), key=lambda question: view.message_key(question["card"]), default=None)
        held = taken or target or next((twin for twin in [*redrawn, *twins] if twin["card"]), None) or newest or next((twin for twin in [*redrawn, *twins] if twin["waits"]), None)
        question = state.new_question(str(kept), asked.title, held["asked"] if held else state.stamp(), unsettled, card=held and held["card"], holds=holds)
        question["waits"] = held and held["waits"] or state.new_waits(name, job_id)
        if folded_into := next((twin["replaced"] for twin in twins if twin["replaced"]), None) or taken and taken["file"]:
            question.update(waits=held and held["waits"], replaced=folded_into)
        doubled = [] if folded_into else [twin for twin in twins if twin["card"] and twin["waits"] != question["waits"]]
        again = [found for _, found in matched if found is not target]
        dropped = [*twins, *(other for other in redrawn if id(other) in own), *(other for other in again if other is held and id(other) in own), *stale]
        replaced = [other for other in dropped if not any(other is twin for twin in doubled)]
        for other in [] if folded_into else [other for other in [*redrawn, *again] if not any(other is gone for gone in dropped)]:
            other["replaced"] = str(kept)
        gone = {other["file"] for other in replaced if other["file"]}
        for other in (other for each in data["jobs"].values() for other in each["questions"] if other["replaced"] in gone):
            other["replaced"] = str(kept)
        if target and not any(target is gone for gone in replaced):
            target["replaced"] = str(kept)
        moment = state.stamp()
        job.update(said=asked.title, updated=moment)
        if job["kind"] != "builder":
            job["returned"] = moment
            if not (job["kind"] == "reader" and job["state"] == "done"):
                job["state"] = "waiting"
        for each in data["jobs"].values():
            each["questions"] = [other for other in each["questions"] if not any(other is gone for gone in replaced)]
        job["questions"].append(question)
    if question["replaced"]:
        return kept, asked, question, str(kept)
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
    """Keep the job's ASK block his answer to that same question settles, closed on his words before any check of its shape or card, since no card draws from it: in place of each open question of the job asking it and each `asked_before` names, the question its file keeps standing when it already ended answered, and a question its `Replaces:` line names ending on his answer too. The file, no question shape, the question, and the line saying his words settle it."""
    from drive import superseded

    data = state.load(name)
    if replaces := limits.REPLACES.search(text):
        replacing(data, replaces[1])
    settled_note(name, data, job_id, title, answer)
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(text)
    generic = superseded.generic(data)
    with state.edit(name) as data:
        job = data["jobs"][job_id]
        twins = [question for other, question in asking(data, title) if other == job_id]
        asker, target = replacing(data, replaces[1]) if replaces else (None, None)
        if (question := state.kept_in(job, kept)) is None or not question["closed"] or question["closed"]["how"] != "answered":
            question = state.new_question(str(kept), title, state.stamp(), unsettled, closed=answer["closed"] | {"at": state.stamp()})
        replaced = [other for other in [*twins, *asked_before(job, title, kept, generic)] if other is not question]
        job["questions"] = [*(other for other in job["questions"] if other is not question and not any(other is gone for gone in replaced)), question]
        job["updated"] = state.stamp()
        if target and not any(target is twin for twin in twins):
            superseded.end(data, asker, target, question["closed"])
    return kept, None, question, settled_line(job_id, job, question, answer)


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
    with state.edit(name) as data:
        question = state.kept_in(data["jobs"][job_id], file)
        question["waits"] = question["waits"] or state.new_waits(name, job_id)
    try:
        return question_card(name, file, question["waits"])
    except DriveError as error:
        refuse_draw(name, job_id, file, question["waits"], str(error))
        raise DriveError(
            f"{error}\n{file} keeps this ASK as {job_id}'s pending question with this refusal: no card draws for it, and the watch says"
            f" {job_id} waits on a card never posted until {job_id} asks it again or `drive job ask {job_id} --drop {file}` drops it"
        ) from None


def post_ask(name: str, job_id: str, file: Path, card: dict) -> str:
    """Post his card on the job's question kept in the file through `talk.posted`, which records it on each question asked under its waits id: in his DM, the thread of the open incident or else of the job's order when that thread is in his DM, else at his DM root, an incident's thread or a thread outside his DM taking the card's line, what it blocks for an incident, through a report record the communicator posts, since only its `say` carries the ticket cc-slack spends on a write outside his DM, once for each change to that report: its cci id names the card's waits id and the report's hash, so cci keeps an unchanged line asked again as the record it already holds, which wakes no handler. Each other open question of the job asking the same on another card ends superseded by this one, its card shown asked again with no buttons and a link to this one, and each wait on it moves to this one's waits id, so nothing waits on him under it; each question folded into this one on another card, as an ask Jev reads as asking it again folds it, shows that card the same way and takes this card and waits id, each wait on that card moving with it. What was done, for drive job ask to print."""
    from drive import talk, tools, view

    data = state.load(name)
    job = data["jobs"][job_id]
    incidents = state.open_incidents(data, [job["order"], *job["carries"]])
    where = state.promise_thread(data["orders"][incidents[0] if incidents else job["order"]])
    inside = where is not None and where[0] == slack.dm()
    link = talk.posted(name, {"card": card} | ({"thread_ts": where[1]} if inside else {}))["permalink"]
    with state.edit(name) as data:
        live = state.kept_in(data["jobs"][job_id], file)
        doubled = [
            other
            for other in view.open_questions(data["jobs"][job_id])
            if other is not live and other["card"] and other["waits"] != live["waits"] and state.same_question(other["title"], live["title"])
        ]
        riding = {id(other) for other in folded(data, live["file"])}
        moved = [(owner, other) for owner, each in data["jobs"].items() for other in each["questions"] if id(other) in riding and other["card"] and other["waits"] != live["waits"]]
        gone = {key for other in [*doubled, *(other for _, other in moved)] for key in (other["waits"], view.message_key(other["card"]))}
        for other in doubled:
            other["closed"] = state.new_close("superseded", ASKED_AGAIN, link, by=live["waits"])
        ended = [(job_id, other) for other in doubled] + [(owner, other | {"closed": state.new_close("superseded", ASKED_AGAIN, link, by=live["waits"])}) for owner, other in moved]
        for owner, other in moved:
            other.update(waits=live["waits"], card=live["card"])
            state.join_card(data, owner, live["card"])
        for owner in [*data["jobs"].values(), *data["prs"].values()]:
            state.rewait(owner, list(dict.fromkeys(live["waits"] if view.message_key(wait) in gone else wait for wait in owner["waits"])))
    said = [f"his card {link} asks it under {card['waits']}", *superseded.follow(name, ended)]
    if where and (incidents or not inside):
        blocks = f", and it blocks incident {', '.join(incidents)}: {job_id} builds its recommended option meanwhile, {card['recommended']}" if incidents else ""
        report = {"kind": "report", "text": f'POST his card {link} asks him "{card["title"]}" for {job_id}{blocks}', "to": [f"communicator@{name}"], "attr": {"job": job_id, "waits": card["waits"], "thread": where[1]}}
        report["id"] = f"card-line:{card['waits']}:{state.ask_hash(json.dumps(report, sort_keys=True))}"
        try:
            tools.sent(name, state.job_lane(data, job), [report])
            said.append(f"its line in thread {where[1]} of channel {where[0]} goes out from the communicator once for each change to it")
        except DriveError as error:
            said.append(f"its line in thread {where[1]} of channel {where[0]} did not go out, so post it there with say: {error}")
    return "; ".join(said)


def settled_note(name: str, data: dict, job_id: str, title: str, answer: dict) -> None:
    """Tell the session holding the job's order, in one record that wakes nobody, that his answer to this same question settled the job's question, so it builds on his words with no card."""
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
    """Another job's open question asking him in these same words, whatever their case and spacing, what this title asks, never one `drive job ask` refused, which draws no card, nor the one on the card `--redraws` takes over, nor one that `took` the job's card, which the job's ask folds into."""
    return next(
        ((other, question) for other, question in asking(data, title) if other != job_id and not question["refused"] and question["waits"] != redraws and not took(data, job_id, question)),
        None,
    )


def took(data: dict, job_id: str, question: dict) -> bool:
    """Whether another job's question took the job's card: it holds a waits id the job minted, as `--redraws` and a `Replaces:` line take one over, or a question of the job is folded into it."""
    return (question["waits"] or "").partition(":")[2].partition("-")[0] == job_id or any(other["replaced"] == question["file"] for other in data["jobs"][job_id]["questions"] if other["replaced"])


def repeating(data: dict, job_id: str, title: str, redraws: str | None) -> list[dict]:
    """The open questions on his cards Jev reads an ask against for asking one of them again in other words, at most settled.CANDIDATES: each drawn, never one folded into another, refused, asking in these same words or on the card `--redraws` takes over, those of the job's orders first, then those sharing the most rare words with it."""
    from drive import view

    orders = {data["jobs"][job_id]["order"], *data["jobs"][job_id]["carries"]}
    said = settled.rare(title)
    drawn = [
        (job["order"] in orders, len(said & settled.rare(question["title"])), question)
        for job in data["jobs"].values()
        for question in view.open_questions(job)
        if question["title"] and question["card"] and question["waits"] not in (None, redraws) and not question["replaced"] and not question["refused"] and not state.same_question(question["title"], title)
    ]
    return [question for *_, question in sorted(drawn, key=lambda found: found[:2], reverse=True)[: settled.CANDIDATES]]


def replacing(data: dict, file: str) -> tuple[str, dict]:
    """The open question an ASK's `Replaces:` line names by the file it is kept in, by its job, followed to the question a later ask folded it into, so a question built on an open one takes that one's card and asks both choices; refused when no job keeps a question there or something closed it, since his answer on a closed card is already given."""
    found = next(((job_id, kept) for job_id, job in data["jobs"].items() if (kept := state.kept_in(job, Path(file).expanduser()))), None)
    while found and found[1]["replaced"]:
        found = next((job_id, kept) for job_id, job in data["jobs"].items() if (kept := state.kept_in(job, Path(found[1]["replaced"]))))
    if found is None:
        raise DriveError(f"`Replaces: {file}` names no file a question of this drive is kept in; drive status <job> names the file each question is kept in")
    if closed := found[1]["closed"]:
        raise DriveError(
            f'`Replaces: {file}`: its question "{render.cut(found[1]["title"] or "", 80)}" ended {closed["how"]}, so it asks him nothing'
            " to replace: build on his words, and ask only what they leave open, with no Replaces line"
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
        f" one card, so his answer closes it everywhere. Wait on that card with drive job set {job_id} --waits {wait}, which frees {job_id}"
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
    with state.edit(name) as data:
        job = data["jobs"].get(job_id)
        question = job and state.kept_in(job, path)
        if not question or question["closed"] or question["card"] or question["refused"] or question["replaced"] or any(other is not question and other["waits"] == waits and not other["replaced"] for other in job["questions"]):
            return
        question.update(refused=refusal, waits=question["waits"] or waits)
        job["updated"] = state.stamp()


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
    """The card a job's question draws: its newest kept ASK block, under the waits id the question already holds, so every ask of one question redraws its one card; the waits id given names the card of a question holding none yet, or one of the job's cards drawn again on purpose. A question that ended, that `drive job ask` refused, or that a `Replaces:` line folded into another job's or its own later question, draws none under any waits id, by `settled.card_refusal`. The card is the job's whose question holds the waits id, by `state.waits_job`, so a card another job took over with --redraws draws for that job; a waits id naming no job of the drive, a card asked outside any job, only draws the block. Each reference in its summary, Show me and option descriptions, such as {at:<seq>} in a Show me table, is filled as say fills a post's, through `refs.resolved` and `refs.slack`, before its file names are drawn as code."""
    from drive import superseded, view

    data = state.load(name)
    if folded_in := next((settled.card_refusal(asker, kept, path) for asker, each in data["jobs"].items() if (kept := state.kept_in(each, path)) and kept["replaced"]), None):
        raise DriveError(folded_in)
    cards = view.live_cards(name) or view.NO_CARDS
    job_id = state.waits_job(data, waits)
    question = None
    if job := data["jobs"].get(job_id):
        question = state.kept_in(job, path) or next(
            (question for other, question in reversed(asking(data, kept_ask(path, None, cards).title)) if other == job_id and question["file"]), None
        )
        if question and (refused := settled.card_refusal(job_id, question, Path(question["file"]))):
            raise DriveError(refused)
        if question:
            path = Path(question["file"])
        if question and question["waits"] and not any(other["waits"] == waits for other in job["questions"]):
            waits = question["waits"]
        if refused := superseded.redraw_refusal(data, job, waits, str(path)):
            raise DriveError(refused)
    asked = kept_ask(path, question, cards)
    if refused := view.tap_claim_refusal(data, job["order"] if job else "", "\n".join([asked.title, *asked.summary, *asked.options]), cards["pr_cards"]):
        raise DriveError(f"{path}: {refused}.{limits.FROM_ASK.format(waits=waits)}")
    recorded = recorded_answers(data, job, path.read_text()) if job else []
    asked = already_answered(asked, recorded)
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
            keep_waits(data, job_id, path, waits)
            if question := state.kept_in(data["jobs"][job_id], path):
                question["recorded"] = recorded
    return card


def kept_ask(path: Path, question: dict | None, cards: dict) -> limits.Question:
    """The question an ASK file draws. One the drive already asked, its question holding a waits id, draws again under the rules it was asked under, a file rebuilt from his card included, never the shape a new block meets; once the words `drive job ask` kept in its file changed since, it draws from his card as the drive stored it under that waits id, the words he read, and once cc-slack's store forgot that card, from the file while its question, summary, recommended option and options are the ones his card's message in Slack shows. Any other file `drive job ask` kept, named for the hash of its words, draws under the rules it was kept under, and one whose words changed since draws nothing, since only `drive job ask` changes a kept question; a file never kept is held to every rule a new block meets."""
    text = path.read_text()
    named = state.KEPT_ASK.fullmatch(path.stem)
    asked = question and question["waits"]
    if named and named["hash"] != state.ask_hash(text):
        if shown := asked and next((shown["card"] for shown in cards["questions"] if shown["card"].get("waits") == asked), None):
            return limits.redrawn(limits.rebuilt(shown, path.parent))
        if asked and question["card"] and slack.posted_question(question["card"]) == (
            (edited := limits.redrawn(text)).title,
            limits.coded("\n".join(edited.summary)),
            edited.recommended,
            edited.options,
        ):
            return edited
        raise DriveError(
            f"{path} no longer holds the words drive job ask kept there, so it draws no card: a kept question changes only through"
            f" drive job ask, which checks the new words against every rule, so keep them with drive job ask {named['job']} <file> and draw the file it prints"
        )
    return limits.redrawn(text) if named or asked else limits.question(text)


def recorded_answers(data: dict, job: dict, text: str) -> list[dict]:
    """Each recorded answer of his that the job's brief binds by --ruling or its ASK block cites by its id outside a link, with the question it answers, his words and the link where he gave them: the one `drive answer` kept, else the card of the question asked in its words; an id ccn keeps no answer of his under, such as a commit's, is none."""
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
            found[answer["id"]] = {"answer": answer["id"][:7], "asked": answer["title"], "words": words, "link": kept or (asked and (asked["card"] or asked["closed"]["link"]))}
    return list(found.values())


def already_answered(asked: limits.Question, recorded: list[dict]) -> limits.Question:
    """The question with the Already answered option when its job's brief binds or its block cites a recorded answer of his, described by his words, cut as a settled ask quotes them, each linked where he gave them; a block already holding every option a card draws gets none, since `keep_ask` refuses one and a block kept before that redraws as he read it, and neither does a card rebuilt from his stored card, which already carries it."""
    if not recorded or limits.ALREADY_ANSWERED in asked.options or len(asked.options) == limits.OPTION_COUNT.stop - 1:
        return asked
    hint = "\n".join(f'"{render.cut(answer["words"], settled.QUOTED_CHARS)}"' + (f" (<{answer['link']}|your answer>)" if answer["link"] else "") for answer in recorded)
    return replace(asked, options=[*asked.options, limits.ALREADY_ANSWERED], hints=asked.hints | {limits.ALREADY_ANSWERED: hint})


def cards_redraw(args: argparse.Namespace) -> int:
    """Print, one JSON line each in one pass, the card `drive job card` prints for each of his open question cards, its `replaces` the card's permalink, which cc-slack draws over that card where it sits, posting no new version, whether or not its store still keeps the card: each open question of the drive drawn on a card, from the ASK file it keeps, as drive status reads it open, and each card cc-slack lists open, from the ASK file `ask_source` finds for it; each card that does not redraw is named once the rest are printed."""
    from drive import view

    name = state.resolve(args.drive)
    view.refresh_cards(name)
    if (found := view.live_cards(name)) is None:
        raise DriveError(f"his open cards could not be read: {view.stored_cards(name)[1]}")
    shown = {waits: card for card in found["questions"] if (waits := card["card"].get("waits"))}
    asked = {
        question["waits"]: question
        for job in state.load(name)["jobs"].values()
        for question in view.open_questions(job)
        if question["waits"] and question["card"] and question["file"] and not question["replaced"]
    }
    drawn, missed = [], []
    for waits in args.waits or dict.fromkeys([*shown, *asked]):
        if waits not in shown and waits not in asked:
            missed.append(f"{waits}: not one of his open cards")
            continue
        try:
            source, url = (ask_source(name, shown[waits]), shown[waits]["url"]) if waits in shown else (Path(asked[waits]["file"]), asked[waits]["card"])
            drawn.append(question_card(name, source, waits) | {"replaces": url})
        except DriveError as error:
            missed.append(f"{waits}: {error}")
    if drawn:
        emit(args, "\n".join(map(json.dumps, drawn)), drawn)
    if missed:
        raise DriveError("these cards did not redraw:\n- " + "\n- ".join(missed))
    return 0


def cards_withdraw(args: argparse.Namespace) -> int:
    """End as withdrawn each open question drawn on his card that his words, wherever he wrote them, withdraw, or whose question or options they change, or that an event at --via overtook, as settled.unhappened reads it, so a tap has nothing left to pick, and its card follows: it shows his words, or what happened, and a link to them, with no buttons, so a late tap records nothing and no question asks him on a card while its work is being redone; its job, or any job of its order carrying on that work, asks again with `drive job ask --redraws` and the card's waits id once the work is back, which redraws it. The event's words are never recorded as his."""
    from drive import superseded, view

    name = state.resolve(args.drive)
    unsaid = settled.unsaid([args.words], args.via, view.live_cards(name), state.load(name))
    if unsaid and (unhappened := settled.unhappened(args.via)):
        raise DriveError(f"{args.card}: refused, not withdrawn: {unsaid}; nor did an event at {args.via} overtake it: {unhappened}")
    with state.edit(name) as data:
        closed = state.new_close("withdrawn", args.words, args.via)
        ended = [each for job_id, question in view.drawn_on(data, args.card) if question["closed"] is None for each in superseded.end(data, job_id, question, closed)]
    if not ended:
        raise DriveError(f"{args.card}: no open question of this drive is drawn on that card, by its waits id or permalink; drive status <job> names the card each question is drawn on")
    done = superseded.follow(name, ended, "what happened" if unsaid else "his words")
    asked = sorted(
        {
            f"drive job ask <job> <file> --redraws {question['waits']} asks it again once its work is back, from {job_id} or any job of {data['jobs'][job_id]['order']}, which redraws its card"
            for job_id, question in ended
            if question["waits"]
        }
    )
    line = "\n".join([f"withdrew {len(ended)} question(s) drawn on his card {args.card}", *done, *asked])
    return emit(args, line, {"card": args.card, "ended": [{"job": job_id, "waits": question["waits"], "card": question["card"]} for job_id, question in ended]})


def ask_source(name: str, shown: dict) -> Path:
    """The ASK file his open card draws again: the one a job's question keeps under the card's waits id; else the one kept by a question linked to the card whose ASK asks what the card asks; else one rebuilt from the card as the drive stored it, kept on a question of its own in the job whose question holds its waits id, else the job that minted it, by `state.waits_job`, which takes the place of every question of that job holding the waits id or linked to the card."""
    from drive import view

    waits, url, title = shown["card"]["waits"], shown["url"], shown["card"]["title"]
    kept = [question for job in state.load(name)["jobs"].values() for question in job["questions"] if question["file"]]
    if held := next((question["file"] for question in kept if question["waits"] == waits and not question["replaced"]), None):
        return Path(held)
    linked = [question["file"] for question in kept if question["card"] and view.message_key(question["card"]) == view.message_key(url) and not question["replaced"]]
    if asked := next((file for file in linked if asks(Path(file), title)), None):
        return Path(asked)
    rebuilt = state.rebuilt_ask(name, waits)
    if not rebuilt.exists():
        rebuilt.write_text(limits.rebuilt(shown["card"], rebuilt.parent))
    with state.edit(name) as data:
        if job := data["jobs"].get(job_id := state.waits_job(data, waits)):
            job["questions"] = [
                question
                for question in job["questions"]
                if question["waits"] != waits and not (question["card"] and view.message_key(question["card"]) == view.message_key(url))
            ]
            job["questions"].append(state.new_question(str(rebuilt), title, shown["asked"], card=url) | {"waits": waits})
            state.join_card(data, job_id, url)
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
    """Each open question a `Replaces:` line folded into the question kept in this file, and each folded into those, which his card for this one asks too."""
    from drive import view

    direct = [other for job in data["jobs"].values() for other in view.open_questions(job) if other["replaced"] == file]
    return [*direct, *(deeper for other in direct for deeper in folded(data, other["file"]))]


def job_report(args: argparse.Namespace) -> int:
    """Keep a job's whole return from stdin in its history, which is its report, and print where, or to a Sprite lane, which opens no path of this Mac's, that it was kept with its first line, that line setting the job's state, or refuse it with nothing kept, naming the fix, when that line would set none; a reader's DESIGN is kept as its ASK in the same call, which draws his card on it and prints the file and the card, and each GitHub PR the drive does not track that the FIX line of a report tracing a live bug's cause links is registered on the job before the take, which joins its order to the bug, or kept untracked with the reason drive pr add refused it; a reader's DONE or DESIGN writes again, as drive job brief does, the brief of each builder of a part it reads that no agent or lane started and that opened no PR, by brief.unstarted_builders, so its Researched: line names the newest research of its parts and its Goal: the change that research's build: line names, by brief.name_goals, serving drive tooling on a tooling order and his order on any other, and prints its `Read your brief first:` line; a design lane's DESIGN adds a builder for each change it lists that no builder or codex job is on, by state.add_changes, and prints when to start each. With no stdin, print the newest return whole and each earlier one as its first line, newest first, and with --all every return whole."""
    name = state.resolve(args.drive)
    data = state.load(name)
    job = find(data["jobs"], args.id, "job")
    if args.stdin:
        whole = stdin_text()
        if not whole.strip():
            raise DriveError(f"stdin held no report for {args.id}; pipe its whole return in: printf '%s\\n' '<line>' ... | drive job report {args.id} -")
        line = whole.strip().splitlines()[0].strip()
        if refused := brief.report_refusal(name, data, args.id, whole) or state.result_refusal(data, args.id, line, whole) or unreached(name, data, state.result_waits(data, job["order"], line, whole)):
            raise DriveError(f"{args.id}: refused, nothing kept: {refused}")
        if job["kind"] == "builder" and state.REPORTED_LINE.match(line) and any(
            cited.removeprefix("DESIGN:").partition(":")[0].strip() != "none" and not served.named(cited) for cited in whole.splitlines() if cited.startswith("DESIGN:")
        ):
            raise DriveError(f"{args.id}: refused, nothing kept: {served.NAMELESS}")
        try:
            registered = register_named(name, args.id, line)
        except DriveError as error:
            raise DriveError(f"{args.id}: refused, nothing kept: drive pr add refuses a PR its first line names: {error}") from None
        data = state.load(name)
        if job["kind"] == "builder" and re.match(r"READY\b", line) and (held := unready(data, job, line)):
            raise DriveError(f"{args.id}: refused, nothing kept: {held}")
        asked = said = None
        if job["kind"] == "reader" and line.startswith("DESIGN"):
            try:
                asked, _, _, said = keep_ask(name, args.id, whole.strip(), None)
            except DriveError as error:
                raise DriveError(f"{args.id}: refused, its report not kept: a reader's DESIGN is his card as it stands, kept as its ASK, and drive job ask refused it: {error}") from None
        state.keep_report(name, args.id, line, whole)
        fixes = []
        for key in state.untracked_fixes(data, args.id, line, whole):
            try:
                fixes.append(f"FIX kept, {key}: tracked under {add_pr(name, key, args.id, github_pr(key))['order']}")
            except DriveError as error:
                fixes.append(f"FIX kept, {error}")
        with state.edit(name) as data:
            state.take_result(data, args.id, line, whole)
            state.cite_designs(data, args.id, line, whole)
            added = state.add_changes(name, data, args.id, whole)
            rebriefed = brief.unstarted_builders(data, args.id) if job["kind"] == "reader" and data["jobs"][args.id]["state"] == "done" else []
            brief.name_goals(name, data, rebriefed, whole)
        briefed = [f"{builder_id}: {write_brief(name, builder_id, {})[0]}" for builder_id in rebriefed]
        kept = str(state.replies_path(name, args.id))
        where = f"kept {args.id}'s report: {line}" if os.environ.get(state.OWN_JOB_ENV) else kept
        shown = f"{where}\n{said}" if said else where
        shown = "\n".join([shown, *registered, *fixes, *briefed, *render.builder_lines(data, added)])
        return emit(args, shown, {"id": args.id, "report": kept, "ask": asked and str(asked), "fixes": fixes, "builders": added})
    text = state.report_text(name, args.id, args.all)
    if not text and not job["said"]:
        raise DriveError(f"{args.id} kept no report and has returned nothing yet")
    if not text:
        return emit(args, f"{job['said']}\n({args.id} kept no report; this one-line result is all it returned)", {"id": args.id, "report": None, "said": job["said"]})
    return emit(args, text.rstrip("\n"), {"id": args.id, "report": text})


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
        pr = add_pr(name, key, job_id, github_pr(key), lead={})
        added.append(f"{key}: registered on {job_id}{drawn_card(name, key, pr)}")
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


def keep_repro(name: str, job_id: str, source: str) -> str:
    """Where the drive keeps a job's reproduction file, copied there once the write naming it is kept."""
    path = named_file(source, "reproduction file")
    kept = state.repro_path(name, job_id, path.suffix)

    def copied() -> list[str]:
        kept.parent.mkdir(exist_ok=True)
        if not kept.exists() or not kept.samefile(path):
            shutil.copy(path, kept)
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
    attrs = state.thread_attr(order) | {"order": order_id, "job": job_id, "progress": "true"}
    return ANSWER_FIRST.format(drive=name, attrs=json.dumps(attrs))


def job_brief(args: argparse.Namespace) -> int:
    line, payload = write_brief(state.resolve(args.drive), args.id, {flag: getattr(args, flag, None) for flag in state.BRIEF_FLAGS})
    return emit(args, line, payload)


def write_brief(name: str, job_id: str, named: dict) -> tuple[str, dict]:
    """Write a job's brief from what the drive holds, by rewrite_brief, with the lines named, each flag left out kept from the job's earlier brief and each ruling named added to every ruling a brief of any job on its order binds, its own earlier ones included, by his approval "carried whole into every brief and handoff", its Serves: line drive tooling on a tooling order and his order on any other when neither names one."""
    data = state.load(name)
    job = find(data["jobs"], job_id, "job")
    order = data["orders"][job["order"]]
    earlier_flags = job.get("brief_flags") or {}
    kept = [flag for flag in state.BRIEF_FLAGS if earlier_flags.get(flag) and (flag == "ruling" or not named.get(flag))]
    bound = [answer_id for other in order["jobs"] for answer_id in (data["jobs"][other].get("brief_flags") or {}).get("ruling") or []]
    ruled = [settled.shown(answer_id, data["roles"]["coordinator"]["cwd"])["id"] for answer_id in named.get("ruling") or []]
    flags = {flag: named.get(flag) or earlier_flags.get(flag) for flag in state.BRIEF_FLAGS} | {"ruling": [*dict.fromkeys([*bound, *ruled])]}
    flags["serves"] = flags["serves"] or ("drive tooling" if tooling(order) else "his order")
    if job["state"] == "waiting" and job["prs"] and not (job.get("agent") or job.get("dispatch") or named.get("handoff")):
        raise DriveError(
            f"{job_id} is an adopted PR's job, waiting with no agent on {', '.join(job['prs'])}: its builder starts only on an event"
            " that needs action, failed checks, requested changes, a new head or his tap; when the watch reports one, brief it"
            " with --handoff <the PR, its head and the event line verbatim>"
        )
    if (seen := brief.surface({"brief_flags": flags})) and (unstoried := brief.unstoried(seen, flags["interactions"])):
        raise DriveError(f"{job_id}'s result is something he uses, by --native: {unstoried}")
    if state.starts_cut(job) and (
        held := state.held_elsewhere(data, job["order"], os.environ.get("CLAUDE_CODE_SESSION_ID"), True)
        or (not job["release_build"] and "its brief names the release it started, so `drive job add --release` writes it once that release starts")
    ):
        raise DriveError(f"{job_id}: {held}")
    line, payload = rewrite_brief(name, job_id, flags)
    if kept:
        line += "\nkept from its earlier brief: " + ", ".join(f"--{flag.replace('_', '-')}" for flag in kept)
    return line, payload | {"kept": kept}


def rebriefed(name: str, job_id: str) -> str:
    """The brief a worker taking a job over starts on: the job's brief written again by rewrite_brief from the flags drive job brief kept, so his words on each order it carries and each answer of his binding it reach that worker as they stand now, then each takeover section an earlier lane started on, so a message one of them carried reaches the next; or the brief as it stands for a job no drive job brief wrote."""
    job = state.load(name)["jobs"][job_id]
    held = orca.brief_of(Path(job["brief"]).read_text())
    if not job.get("brief_flags"):
        return held
    rewrite_brief(name, job_id, job["brief_flags"])
    _, over, taken = held.partition(orca.TAKING_OVER)
    return Path(state.load(name)["jobs"][job_id]["brief"]).read_text().strip() + (f"\n\n{over}{taken}" if over else "")


def rewrite_brief(name: str, job_id: str, flags: dict) -> tuple[str, dict]:
    """Write a job's brief from what the drive holds and its flags: his words verbatim on its order and on each order it carries, each Slack message they link by linked_messages, each answer of his binding it, by brief.bound, as its recorded words above the drive's notes, the earlier jobs' transcripts, and the lines its flags name."""
    data = state.load(name)
    job = data["jobs"][job_id]
    order = data["orders"][job["order"]]
    checkout_dir = Path(data["roles"]["coordinator"]["cwd"])
    earlier = [line for other in order["jobs"] if other != job_id and (line := transcript_line(other, data["jobs"][other]))]
    planned = order["kind"] == "build" and not order["incident"]
    found = brief.research(name, data, job["order"], job["parts"]) if planned and job["kind"] in brief.RESEARCHED_KINDS else None
    ids, heard = brief.bound(name, data, job | {"brief_flags": flags})
    rulings = [*dict.fromkeys(ruling_words(answer_id, checkout_dir) for answer_id in ids), *heard]
    carried = "".join(f"\n{order_id}, which it also carries: {data['orders'][order_id]['text']}" for order_id in job["carries"])
    scratch = Path(flags["scratch"]).expanduser() if flags["scratch"] else state.drive_dir(name) / "scratch" / job_id
    fields = {
        "Order (his words)": order["text"] + carried,
        "Linked messages": linked_messages(job_id, order["text"] + carried),
        "Serves": flags["serves"],
        "Goal": job["goal"],
        "Done when": flags["done_when"],
        "Where": flags["where"],
        "Runs as": flags["runs_as"],
        "Release": flags["release"],
        "Release build": job["release_build"],
        "Mac": flags["mac"],
        "Handoff": flags["handoff"],
        "Decisions that bind": rulings or "none named; search his answers on the area",
        "Research": planned and job["kind"] == "reader" and READER_RESEARCH,
        "Researched": found and found[0],
        "Answer": order["kind"] == "answer" and job["kind"] == "reader" and answer_first(name, order, job["order"], job_id),
        "Native": flags["native"],
        "Interactions": flags["interactions"],
        "Would he ask": flags["would_he_ask"],
        "Plan": flags["plan"],
        "Incident": incident_line(data, job_id, flags["incident"]),
        "Scratch": str(scratch),
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

    state.after_save(name, kept)
    with state.edit(name) as data:
        data["jobs"][job_id].update(brief=str(written), brief_flags=flags, researched=brief.research_links(data["jobs"][job_id], text))
        if given := bool(brief.SERVES_TOOLING.search(text)) and data["orders"][job["order"]]["owner"] != "meta":
            data["orders"][job["order"]]["owner"] = "meta"
    line = f"Read your brief first: {written}\nhis words from {', '.join([job['order'], *job['carries']])}, rulings: {len(rulings)}, earlier jobs: {len(earlier)}"
    if given:
        line += (
            f"\n{job['order']} is the meta session's order now, since a builder serving drive tooling is its alone: only the meta session"
            f" starts {job_id} and its agents and posts its reports; from the coordinator, start no agent on it and send"
            f" meta@{name} a defect record naming {job_id} and its `Read your brief first:` line"
        )
    return line, {"id": job_id, "brief": str(written), "rulings": len(rulings), "earlier": earlier}


def linked_messages(job_id: str, words: str) -> list[str]:
    """Each Slack message the words of a job's orders link, read through cc-slack as its brief is written, with who posted it, when, and each message it shares, so no job reads Slack for the message its order came from; a link cc-slack cannot read refuses the brief in cc-slack's words."""
    read = []
    for link in dict.fromkeys(slack.PERMALINK.findall(html.unescape(words))):
        try:
            message = slack.message(link)
        except slack.SlackError as error:
            raise DriveError(f"{job_id}: no brief written, since cc-slack cannot read {link}, which its order links: {error}; write it again once cc-slack reads it") from None
        who = "him" if message.get("from_user") else message["user_name"] or message["user"]
        shared = "".join(f"\nShared from {share['user_name'] or share['user']}: {share['text'] or share.get('unreadable', '')}" for share in message.get("shares", []))
        read.append(f"{link}, posted by {who} at {slack.sent_at(message).isoformat(timespec='seconds')}:\n" + textwrap.indent(html.unescape(message["text"]) + shared, "  "))
    return read


def incident_line(data: dict, job_id: str, given: str | None) -> str | None:
    """A brief's Incident: line: the call's own, then on an incident order a builder's release rule and the rule a lane of its kind follows on a choice, which a choice never stops, then on a job tracing a live bug's cause the FIX line its report ends with, each once."""
    job = data["jobs"][job_id]
    incident = data["orders"][job["order"]]["incident"]
    rules = [release_rule() if incident and job["kind"] == "builder" else None, state.INCIDENT_CHOICE.get(job["kind"]) if incident else None, state.FIX_RULE if state.traced(data, job_id, "") else None]
    return "; ".join(filter(None, [given, *(rule for rule in rules if rule and rule not in (given or ""))])) or None


def release_rule() -> str:
    """The rule incidents.md states for every release a drive job starts, read from the page so the brief and the page share one text."""
    return " ".join(page_parts((PAGES / "incidents.md").read_text(), "Every release a drive job starts")[0].split()).removeprefix("- ")


def ruling_words(answer_id: str, checkout_dir: Path) -> str:
    answer = settled.shown(answer_id, str(checkout_dir))
    return f"{answer['title']} ({answer['id'][:7]})\n{textwrap.indent(settled.his(answer['body']), '  ')}"


def transcript_line(job_id: str, job: dict) -> str | None:
    if job.get("where") == "remote" and job.get("dispatch") and not artifacts.lane_transcript(job):
        return f"{job_id} {job['kind']}, a remote lane: drive job replies {job_id}"
    if job.get("codex_run"):
        return f"{job_id} codex, its codex-ask run: {job['codex_run']}"
    path = artifacts.transcript(job)
    return f"{job_id} {job['kind']}: {path}" if path else None


def job_launch(args: argparse.Namespace) -> int:
    """Start a job's next worker by the one relaunch launch and resume share, with no message: its first Orca lane, on a Sprite or with --local on this Mac, where a brief declaring a step only this Mac runs on its Mac: or Release: line starts, stamped as launching once nothing refuses it so the watch reads it as starting until the launch could have finished; the next lane of a job whose worker ended or returned its line, with --local moved from its Sprite to this Mac; a lane taking over from an agent on this Mac that stopped or that another session holds; or the worker an earlier launch of its brief started and never recorded, whose ready receipt the launcher keeps."""
    name = state.resolve(args.drive)
    if find(state.load(name)["jobs"], args.id, "job")["kind"] == "codex":
        return codex_launch(args, name)
    return relaunch(args, name, None)


def codex_launch(args: argparse.Namespace, name: str) -> int:
    """Start a codex job's codex-ask run on this Mac from this session's folder on its brief, with each text the job holds and each answer of his it has not heard under His answers since:, kept as the job's brief so the builder that ships its fix reads what it was given, with no agent relaying it, the texts it carries off the job and each answer it carries marked sent, refused as an Agent's start is refused for any other job: while its waits hold, with no brief, on an order another session holds, or on a brief brief_refusal refuses; codex-ask itself refuses a run while the job's last one still goes."""
    with state.job_locked(name, args.id, orca.LAUNCH_SECONDS):
        data = state.load(name)
        job = data["jobs"][args.id]
        if job["waits"]:
            raise DriveError(f"{args.id} waits on {', '.join(job['waits'])}: launch it on the watch's FREE line, which comes once the last wait ends")
        if not (args.brief or job.get("brief")):
            raise DriveError(f"{args.id} has no brief: write it with drive job brief {args.id}, or name its file")
        written = answers_since(data, args.id, named_text(args.brief or job["brief"], "brief file"))
        if refused := state.held_elsewhere(data, job["order"], os.environ.get("CLAUDE_CODE_SESSION_ID"), state.starts_cut(job)) or brief.brief_refusal(name, data, args.id, written):
            raise DriveError(f"{args.id}: refused, not launched: {refused}")
        try:
            folder = codex.ask(name, args.id, written, data["orders"][job["order"]]["incident"], job["model"])
        except codex.CodexError as error:
            raise DriveError(f"{args.id}: refused, not launched: {error}") from None
        kept = launch_brief(name, args.id, job)
        kept.parent.mkdir(exist_ok=True)
        kept.write_text(written)
        with state.edit(name) as data:
            job = data["jobs"][args.id]
            job.update(
                codex_run=str(folder),
                brief=str(kept),
                researched=brief.research_links(job, written),
                undelivered=[held for held in job["undelivered"] if not superseded.carries(written, held["words"])],
            )
            state.start(job)
            superseded.hear(data, args.id, written, state.now())
    return emit(args, f"{args.id} started its codex-ask run in {folder}; the watch takes its result once it ends", {"id": args.id, "codex_run": str(folder)})


def job_resume(args: argparse.Namespace) -> int:
    """Send a job its next event by the one relaunch launch and resume share: to its lane while its worker takes messages, else in the handoff of its next worker. With no text, send the texts a resume Orca could not deliver holds on the job, as the watch does once Orca reads its lane alive again."""
    name = state.resolve(args.drive)
    unqueued(name, args.id)
    if args.text is not None:
        return relaunch(args, name, args.text)
    with state.job_locked(name, args.id, orca.LAUNCH_SECONDS):
        unstopped(args.id, started_job(state.load(name), args.id))
        return deliver(args, name)


def unqueued(name: str, job_id: str) -> None:
    """Refuse a resume, sending and holding nothing, rather than queue for the job's lock while another verb holds it and Orca cannot reach the job's lane on its Sprite, as when another resume wakes that Sprite for up to five minutes, so the watch's resumes of a lane that does not answer never pile up on its lock."""
    job = find(state.load(name)["jobs"], job_id, "job")
    if not (job.get("where") == "remote" and job.get("dispatch") and state.job_lock_held(name, job_id)):
        return
    try:
        orca.worker(job["dispatch"])
    except orca.OrcaError as error:
        if orca.out_of_reach(error):
            raise DriveError(
                f"{job_id}: refused, nothing sent or held: another drive verb holds its lock while Orca cannot reach its lane on its Sprite ({error});"
                f" drive job launch {job_id} --local moves it to this Mac"
            ) from None


def yield_to_launch(name: str, job_id: str) -> None:
    """Stand a resume holding the job's lock down, before it wakes, messages or starts anything, while a drive job launch --local waits for that lock to move the job to this Mac; the texts the job holds stay on it for that launch's handoff."""
    if pid := state.launch_waiting(name, job_id):
        raise DriveError(f"{job_id}: stood down, nothing sent or started: drive job launch {job_id} --local, pid {pid}, waits for its lock to move it to this Mac, and its handoff carries the texts the job holds")


def unstopped(job_id: str, job: dict) -> None:
    """Refuse a resume of a stopped job, read under the job's own lock, sending, holding and starting nothing, so a resume the watch started while the job ran never reaches a lane its stop retired or starts a second writer beside the job that replaced it; drive job launch runs a stopped job again."""
    if job["state"] == "stopped":
        raise DriveError(f"{job_id}: refused, nothing sent, held or started: it is stopped; drive job launch {job_id} runs it again")


def relaunch(args: argparse.Namespace, name: str, text: str | None) -> int:
    """The one path launch and resume share, under the job's own lock, so a second waits for the first and acts on the worker it started: a lane whose worker takes messages gets the text, one whose worker ended starts its next in a handoff, as does one on a Sprite whose worker returned its line when --local moves it to this Mac, an agent on this Mac that ended with its session, whose last response ended on an API error or refusal, that a launch finds returned while the job holds texts, or whose turn ended while a resume's text finds this Mac's builder slots full hands the job to a lane on a Sprite, or on this Mac when its brief declares a step only this Mac runs, an agent another session holds hands it to this one, and a job with no worker yet starts its first."""
    with state.launch_waits(name, args.id) if text is None and args.local else nullcontext(), state.job_locked(name, args.id, orca.LAUNCH_SECONDS):
        data = state.load(name)
        job = find(data["jobs"], args.id, "job")
        if text is not None:
            unstopped(args.id, job)
        taker = other_sessions_agent(job)
        if taker and text is not None:
            return hand_over(args, name, data, taker)
        if job.get("where") == "remote" and (dispatch := job.get("dispatch")):
            if text is not None:
                hold(name, args.id, text)
                return deliver(args, name)
            here = args.local and not state.on_mac(job)
            try:
                end = orca.lane_end(name, job, wake=not here)
            except orca.OrcaError as error:
                raise DriveError(
                    f"{args.id}'s lane cannot be checked, so nothing was started: {error}; launch it again once Orca reaches its lane {orca.ON_MAC if state.on_mac(job) else 'on its Sprite'}"
                ) from None
            if end or (here and job["state"] != "running"):
                return restart(args, name, dispatch, end or orca.LaneEnd("its worker returned its line and still takes messages there", None, None, live=True), None, here)
            raise DriveError(f"{args.id} already started as dispatch {dispatch}, whose worker takes messages: drive job resume {args.id} <the event> sends it one")
        died = None if taker or not job.get("agent") else watch.agent_death(job, data) or (returned(job) if text is None else place.woken(data, args.id, state.now()))
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


def returned(job: dict) -> str | None:
    """Why an agent on this Mac takes no text the job holds: it returned, and only its session's SendMessage reaches it; None while it works or the job holds none."""
    if job["state"] == "running" or not job["undelivered"]:
        return None
    return f'it returned "{render.cut(job["said"] or "", 100)}", and only its session\'s SendMessage reaches it'


def hold(name: str, job_id: str, text: str) -> None:
    """Keep the text on the job's undelivered list under a fresh Orca request id before any Orca call, so an outage delays it and never drops it, unless the job already holds it."""
    with state.edit(name) as data:
        kept = data["jobs"][job_id]["undelivered"]
        if all(held["words"] != text for held in kept):
            kept.append({"seq": None, "words": text, "at": state.stamp(), "said": None, "request": str(uuid.uuid4())})


def deliver(args: argparse.Namespace, name: str) -> int:
    """Deliver every text the job holds, oldest first, once Orca shows its lane: sent to a worker that takes messages, else carried in the handoff of its next worker; while Orca cannot tell or starts no next worker, each stays held for the watch to send again."""
    job = state.load(name)["jobs"][args.id]
    held = job["undelivered"]
    if not held:
        return emit(args, f"{args.id} holds no message to send", {"id": args.id, "how": None})
    yield_to_launch(name, args.id)
    try:
        end = orca.lane_end(name, job)
    except orca.OrcaError as error:
        return unsent(args, job, held, error)
    if not end:
        return messaged(args, name, job, held)
    yield_to_launch(name, args.id)
    try:
        return restart(args, name, job["dispatch"], end, None)
    except orca.OrcaError as error:
        return unsent(args, job, held, error)


def messaged(args: argparse.Namespace, name: str, job: dict, held: list[dict]) -> int:
    """Send each held text to a lane whose worker takes messages, each under its own request id, dropping each from the job once Orca takes it, and mark the job sent each answer of his the text carries; first put on its Sprite the tools a launch puts there, the drive forwarder among them, when no start this drive recorded put them on that Sprite, as for a lane started before the forwarder existed or recorded by hand."""
    unfit, workspace = None, job.get("workspace")
    if job.get("tools_on") != workspace:
        try:
            orca.place_tools(workspace)
        except orca.Unfit as error:
            unfit = error
    hows = []
    for index, kept in enumerate(held):
        try:
            hows.append(orca.answer(name, job, kept["words"], kept["request"]))
        except orca.OrcaError as error:
            return unsent(args, job, held[index:], error)
        sent_kept(name, args.id, kept | {"ask": job.get("ask")} | ({} if unfit else {"tools_on": workspace}))
        job = {**job, "ask": None}
    how = "; ".join(hows) + (f"; its Sprite still lacks the drive forwarder: {unfit}" if unfit else "")
    return emit(args, f"{args.id}: {how}", {"id": args.id, "how": how})


def sent_kept(name: str, job_id: str, sent: dict) -> dict:
    """Keep on the job a held text its live worker took: dropped from the job, its question closed when the text answered the one it still asks, each answer of his the text carries marked sent, and the tools on the Sprite tools_on names; when a newer install saved the drive while Orca took the text, the newest install's `drive job sent` keeps it, so the text is never sent twice."""
    try:
        with state.edit(name) as data:
            fresh = data["jobs"][job_id]
            if fresh.get("ask") == sent["ask"]:
                fresh.update(ask=None, state="running")
            fresh.update(
                updated=state.stamp(),
                undelivered=[left for left in fresh["undelivered"] if left["request"] != sent["request"]],
            )
            if "tools_on" in sent:
                fresh["tools_on"] = sent["tools_on"]
            superseded.hear(data, job_id, sent["words"], state.parse_time(sent["at"]))
        return fresh
    except state.NewerDrive:
        if not (newer := install.upgraded()):
            raise
    receipt = state.drive_dir(name) / "jobs" / f"{job_id}.sent.{sent['request']}.json"
    return json.loads(install.handed(newer, name, ["job", "sent", job_id], receipt, sent, f"{job_id}: its worker took the text", state.DRIVE_LOCK_SECONDS))


def job_sent(args: argparse.Namespace) -> int:
    """Keep a held text an older install's resume saw its live worker take while this install saved the drive, which sent_kept hands here."""
    name, receipt = state.resolve(args.drive), Path(args.receipt)
    job = sent_kept(name, args.id, json.loads(receipt.read_text()))
    receipt.unlink()
    return emit(args, f"{args.id}: the text its worker took is kept", job)


def unsent(args: argparse.Namespace, job: dict, held: list[dict], error: orca.OrcaError) -> int:
    """Say that Orca took none of these held texts, which stay on the job under their request ids, for the watch to send once Orca reads the lane alive, or the job's next worker to carry in its handoff."""
    count = "its text" if len(held) == 1 else f"its {len(held)} texts"
    line = (
        f"{args.id}: held, not sent yet: {error}; the job keeps {count} under the Orca request id each was held with, the watch sends them once Orca"
        f" reads its lane {orca.ON_MAC if state.on_mac(job) else 'on its Sprite'} alive, and its next worker gets them in its handoff"
    )
    return emit(args, line, {"id": args.id, "how": "held", "held": [kept["request"] for kept in held]})


def launched(name: str, job_id: str, brief_file: str | None, local: bool, text: str | None, died: str | None) -> dict:
    """Start a job's first lane, briefed as drive job brief writes it when none was, or one taking over from an agent on this Mac that stopped, returned while the job holds texts, or that another session holds, with the text in its handoff when a resume brought one, and under His answers since: each text the job holds and each answer of his it has not heard whose words its brief lacks; a lane taking over from an agent that stopped or returned starts on this Mac when its brief declares a step only this Mac runs. Once nothing refuses it the job runs, stamped as launching, its line no longer naming the waits that ended, so the watch reads a builder whose reader finished before its FREE line as starting, never as waiting on nothing."""
    from drive import view

    data = state.load(name)
    job = data["jobs"][job_id]
    kept = launch_brief(name, job_id, job)
    if adopted := orca.adopted(kept):
        return {field: adopted[field] for field in LAUNCHED} | {"brief": str(kept)}
    if not job.get("agent") and (waits := [wait for wait in view.holding(data, job["waits"], None) if local or wait != state.SLOT_WAIT]):
        raise DriveError(f"{job_id} waits on {', '.join(waits)}: launch it on the watch's FREE line, which comes once the last wait ends")
    taker = other_sessions_agent(job)
    if not (brief_file or job.get("brief")):
        write_brief(name, job_id, {})
        data = state.load(name)
        job = data["jobs"][job_id]
    root = orca.checkout(name, data)
    written = answers_since(data, job_id, rebriefed(name, job_id) if (taker or died) and not brief_file else named_text(brief_file or job["brief"], "brief file"))
    if taker or died:
        written = orca.agent_handoff(orca.brief_of(written), job, text, died)
    local = local or bool(died and place.mac_step(written))
    if refused := (
        state.held_elsewhere(data, job["order"], os.environ.get("CLAUDE_CODE_SESSION_ID"), state.starts_cut(job))
        or brief.brief_refusal(name, data, job_id, written)
        or orca.uncarried(name, job_id, written)
        or (f"its brief's Mac: or Release: line declares {step}, a step only this Mac runs: start it here, with the Agent tool at any load or with --local" if not local and (step := place.mac_step(written)) else None)
        or (crowded_here(name, data, job_id, written) if local else None)
    ):
        raise DriveError(f"{job_id}: refused, not launched: {refused}")
    with state.edit(name) as data:
        job = data["jobs"][job_id]
        if taker or died:
            job.update(agent=None, dispatch=None, session=taker or job["session"])
        job.update(where="remote", route=state.LOCAL_ROUTE if local else None, launching=state.stamp(), researched=brief.research_links(job, written))
        state.rewait(job, [wait for wait in view.holding(data, job["waits"], None) if wait != state.SLOT_WAIT])
        state.start(job)
    kept.parent.mkdir(exist_ok=True)
    try:
        receipt = launch_attempt(name, job, kept.stem, kept, orca.vm_brief(name, job_id, written, job["route"]), root, None, None)
    except orca.Unstarted:
        record_start(name, job_id, None, None)
        raise
    return {field: receipt[field] for field in LAUNCHED} | {"brief": str(kept)}


def answers_since(data: dict, job_id: str, written: str) -> str:
    """The brief a job's next worker or codex-ask run starts on: under His answers since:, each text the job holds and each answer of his it has not heard whose words the brief lacks."""
    held = [kept["words"] for kept in data["jobs"][job_id]["undelivered"] if not superseded.carries(written, kept["words"])]
    owed = [f'"{kept["words"]}"' + (f" ({kept['link']})" if kept["link"] else "") for _, kept in superseded.owed(data, job_id) if not superseded.carries("\n".join([written, *held]), kept["words"])]
    if since := [*held, *owed]:
        return written.rstrip() + "\nHis answers since:" + "".join(f"\n- {line}" for line in since) + "\n"
    return written


def crowded_here(name: str, data: dict, job_id: str, written: str) -> str | None:
    """Why a builder's lane starts in no worktree on this Mac now, by place.crowded, the rule every builder start here goes through: one whose brief declares no step only this Mac runs starts on a Sprite at once, and one that does, that no agent or lane runs and nothing else holds, waits its turn for a slot."""
    if data["jobs"][job_id]["kind"] != "builder" or not (why := place.crowded(data, job_id, state.now())):
        return None
    if not place.mac_step(written):
        return f"{why}, so it starts on a Sprite at once: drive job launch {job_id}, with no --local"
    if not place.queueable(data, job_id):
        return f"{why}, so this one waits: launch it here once a slot opens with no builder waiting its turn"
    with state.edit(name) as data:
        place.queue(data["jobs"][job_id], state.now())
    return f"{why}, so {job_id} waits its turn for a builder slot on this Mac, {place.positions(data)[job_id]}, and the watch's FREE line names it as a slot opens"


def launch_brief(name: str, job_id: str, job: dict) -> Path:
    """The file the job's next start keeps its brief in, whose name is the launcher's attempt: numbered after the lanes the job recorded dying, and past every attempt whose kept receipt names a dispatch the job recorded, since the launcher gives a brief one attempt and Orca may have dropped that dispatch; an attempt that started no worker, or one the drive never recorded, is used again, so the launcher retries it or adopted records it."""
    recorded = {job.get("dispatch"), *(death["dispatch"] for death in job.get("deaths", []))} - {None}
    first = len(job.get("deaths", [])) + 1
    briefs = (state.drive_dir(name) / "jobs" / (f"{job_id}-{number}.md" if number > 1 else f"{job_id}.md") for number in range(first, first + len(recorded) + 1))
    return next(kept for kept in briefs if (orca.kept_start(kept) or {}).get("dispatchId") not in recorded)


def record_start(name: str, job_id: str, started: dict | None, handoff: str | None) -> dict:
    """Record a started worker's receipt on its job, its turn open until the watch takes that worker's worker_done or escalation, with the handoff a relaunch started it on, else the launch's brief, the Sprite its start put the tools on, the cc-remote config that Sprite was made from, none of the texts it held, which that handoff or brief carries, and the job sent each answer of his whose words that handoff or brief holds, or with no receipt clear the launching mark of a launch whose worker never started. When a newer install saved the drive while the worker started, which launch_attempt lets one do, the newest install's `drive job started` records it, so the receipt lands either way."""
    try:
        with state.edit(name) as data:
            job = data["jobs"][job_id]
            if started is None:
                job["launching"] = None
            else:
                job.update(started, handoff=handoff, **({"reads": {}} if handoff else {}), undelivered=[], ask=None, report=None, tools_on=started["workspace"], turn_open=True)
                state.start(job)
                superseded.hear_start(data, job_id)
        return job
    except state.NewerDrive:
        if not (newer := install.upgraded()):
            raise
    receipt = state.drive_dir(name) / "jobs" / f"{job_id}.{started['dispatch'] if started else 'unstarted'}.json"
    what = f"{job_id}: its worker started as dispatch {started['dispatch']}" if started else f"{job_id}: its launch started no worker"
    kept = {"started": started, "handoff": handoff}
    return json.loads(install.handed(newer, name, ["job", "started", job_id], receipt, kept, what, orca.LAUNCH_SECONDS))


def job_started(args: argparse.Namespace) -> int:
    """Record the receipt an older install's launch or resume kept, of a worker it started or of a launch that started none, while this install saved the drive, which record_start hands here; a worker its job already records changes nothing."""
    name = state.resolve(args.drive)
    receipt = Path(args.receipt)
    kept = json.loads(receipt.read_text())
    job = find(state.load(name)["jobs"], args.id, "job")
    if not (kept["started"] and job.get("dispatch") == kept["started"]["dispatch"]):
        job = record_start(name, args.id, kept["started"], kept["handoff"])
    receipt.unlink()
    line = f"{args.id} started {orca.place(job)} as dispatch {kept['started']['dispatch']}" if kept["started"] else f"{args.id} is no longer launching"
    return emit(args, line, job)


def launch_attempt(name: str, job: dict, attempt: str, kept: Path, brief: str, root: Path, workspace: str | None, session: str | None) -> dict:
    """Launch one attempt of a job's lane on a Run the drive's own terminal makes, since Orca binds a Run only to the terminal that made it: on a fresh Sprite, or on the workspace's own Sprite in the checkout it holds, there resuming the Claude session given; a fresh Sprite's receipt records the SHA-256 of the cc-remote config the checkout holds, which it is made from, and one on the workspace's own Sprite keeps the job's; a try whose worker-start sent nothing reuses the Sprite it prepared, a Run Orca cannot make starts nothing, and the launcher copies this brief onto it. When a newer install saved the drive while the launcher ran, record_start hands the receipt to that install. A fresh Sprite that refuses the tools a worker needs is destroyed by the launcher, and the attempt launches once more on another; Unfit when that one refuses them too, or when the workspace's own Sprite does."""
    kept.write_text(brief)
    try:
        run = orca.new_run(name, f"drive {name} {attempt}: {job['goal']}")
    except orca.OrcaError as error:
        raise orca.Unstarted(f"{attempt}: no worker started, since Orca made it no Run: {error}") from None
    lane = orca.lane_name(name, attempt)
    made = None if state.on_mac(job) else job.get("sprite_config") if workspace else orca.sprite_config(root)
    try:
        receipt = orca.launch(name, lane, kept, root, run, job["route"], workspace, session)
    except orca.Unfit:
        if workspace:
            raise
        receipt = orca.launch(name, lane, kept, root, run, job["route"], None, None)
    return {**receipt, "run": run, "sprite_config": made}


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
    if refused := state.held_elsewhere(data, job["order"], session, state.starts_cut(job)):
        raise DriveError(f"{args.id}: refused, not handed over: {refused}")
    if not job.get("brief"):
        raise DriveError(f"{args.id} has no brief to hand over: write it with drive job brief {args.id}, then resume it again")
    kept = state.drive_dir(name) / "jobs" / f"{args.id}.handoff.md"
    kept.write_text(orca.agent_handoff(rebriefed(name, args.id), job, args.text, None))
    with state.edit(name) as data:
        data["jobs"][args.id].update(handoff=str(kept), handed_to=session, updated=state.stamp())
    line = (
        f"Read your brief first: {kept}\n{args.id} runs as agent {job['agent']} of session {job['session']}, which this session cannot"
        f" SendMessage: start a fresh {job['kind']} agent here on the line above as its whole prompt, and the drive records {args.id} as this session's"
    )
    return emit(args, line, {"id": args.id, "handoff": str(kept), "handed_to": session})


def restart(args: argparse.Namespace, name: str, dispatch: str, end: orca.LaneEnd, text: str | None, here: bool = False) -> int:
    """Start the job's next worker with a handoff, carrying the text when a resume brought one: in the terminal of a worker that finished on this Mac while Orca holds it, on that worker's Run; for one that finished on a Sprite, on that Sprite again in the checkout it left, on a Run this terminal makes, through the launcher's cc-remote orca prepare --existing, which gives it the tool payload and keys a fresh Sprite gets, since a worker started in the finished one's terminal runs on in the claude process and environment that Sprite's first prepare made, unless that Sprite refuses the tools a worker needs or was made from a cc-remote config other than the one the default branch holds now; else, on a Run this terminal makes, on a fresh Sprite, or in a fresh worktree on this Mac. A dead lane's Sprite first has its Orca runtime and worker logs copied to the drive's folder by orca.keep_logs, which the watch's LANE died line names; a lane that died while its job ran keeps on its death the cause line keep_logs reads, which its next worker's brief and the line say, and when another lane of the job died so since its last line, this second death starts no worker and the refusal names both causes, unless the job moves to this Mac. A Sprite that answers with the Claude session the dead lane's worker ran there resumes that session by resume_session, with nothing saved, moved or destroyed; a resume that starts no worker falls back to a relaunch, its why naming the refusal, on a fresh Sprite when that one refused a worker's tools. Otherwise every Claude Code transcript the lane's Sprite holds is first copied whole to the drive's folder by orca.keep_transcripts, kept on the lane's death. Before a relaunch it saves to GitHub what the dead lane's Sprite holds that a fresh one would not get, by lane_saved, and its line and handoff name each branch GitHub then holds under saved/<the dead lane>/, by one read, never the text any save printed, or, when GitHub holds none, what that save found: what it saved, that the Sprite held nothing unpushed or uncommitted, or that cc-remote records no such Sprite, so nothing on it was read or saved; when that Sprite cannot be read or the save fails, the relaunch runs on that Sprite instead, in the checkout the dead lane left, unless it refused a worker's tools or started no worker, as a Sprite that will not prepare does, when it is kept and the relaunch runs on a fresh one. A lane Orca could not reach, whose Sprite lane_end woke or tried to, relaunches on that Sprite in the checkout it left with no save when no session of it resumes there, and on a fresh one the same way when that Sprite starts no worker. A relaunch on a fresh Sprite then destroys the dead lane's Sprite once what it holds on no remote is saved. A finished worker whose terminal or worktree Orca no longer finds starts its next worker as a dead one's would. A relaunch an earlier restart of the same death started and never recorded is recorded from the ready receipt its attempt keeps, never launched twice. Here, which a launch with --local of a job on a Sprite asks, as does a step only this Mac runs that place.mac_step reads on its brief's, its last report's or the text's Mac: or Release: line, the one placement read every start makes, moves the job to a fresh worktree on this Mac whether its worker finished, died, does not answer or returned its line and still takes messages, saving first what its Sprite holds on no remote; when that save fails it still moves, the Sprite kept rather than destroyed, its lane recorded dead so nothing of it is read or sent again, and the line and handoff name what every lane of the job saved on GitHub, so the next worker starts from the job's pushed and saved branches. The handoff carries every text a resume holds on the job, oldest first, then the text given, since the start it records clears what the job holds."""
    data = state.load(name)
    job = data["jobs"][args.id]
    message = "\n\n".join([*(kept["words"] for kept in job["undelivered"]), *filter(None, [text])]) or None
    said = None if state.on_mac(job) else "\n\n".join(filter(None, [orca.brief_of(Path(job["brief"]).read_text()), job.get("report"), message]))
    declared = None if here or said is None else place.mac_step(said)
    here = here or declared is not None
    if here and said is not None and (crowd := crowded_here(name, data, args.id, said)):
        raise DriveError(f"{args.id}: refused, no worker started: {crowd}")
    finished = end.worktree and not here
    same = finished and state.on_mac(job)
    unreached = end.unreached and not (here or end.unfit)
    root = None if same else orca.checkout(name, data)
    if finished and not same and (made := job.get("sprite_config")) != (now := orca.sprite_config(root)):
        was = f"the cc-remote config with SHA-256 {made[:12]}" if made else "a cc-remote config the drive never recorded"
        stale = f"its Sprite {job['workspace']} was made from {was}, and the one the default branch holds now has SHA-256 {now[:12]}"
        return restart(args, name, dispatch, orca.LaneEnd(f"{end.why}, and {stale}", end.branch, None, unfit=True), text)
    left = cause = None
    if not (end.worktree or end.live or state.on_mac(job)):
        read = orca.keep_logs(name, args.id, job, dispatch)
        cause = read if job["state"] == "running" else None
        if cause and not here and (first := state.died_before(job, dispatch)):
            with state.edit(name) as data:
                state.lane_died(data["jobs"][args.id], dispatch, end.why, state.now(), cause)
                second = state.lane_death(data["jobs"][args.id])
            raise DriveError(
                f"{args.id}: refused, no worker started: its lane died twice since its last line, on Sprite {first['workspace']} at {render.when(first['at'])}"
                f" ({state.why_died(first['why'], first['cause'])}) and on Sprite {second['workspace']} at {render.when(second['at'])} ({state.why_died(end.why, cause)});"
                f" their logs are in {orca.death_logs(name, args.id, first['dispatch'])} and {orca.death_logs(name, args.id, dispatch)}."
                f" drive job launch {args.id} --local moves it to a fresh worktree {orca.ON_MAC}" + (", and its next worker gets the text the job holds" if message else "")
            )
        if not (here or end.unfit) and (session := orca.session_of(job["workspace"], dispatch)):
            try:
                return resume_session(args, name, dispatch, end, message, session, root, cause)
            except orca.Unstarted as error:
                end = end._replace(why=f"{end.why}, and its Claude session {session} did not resume there: {error}", unfit=isinstance(error, orca.Unfit))
    transcript = None if state.on_mac(job) else orca.keep_transcripts(name, args.id, job, dispatch)
    try:
        saved = None if finished or unreached else lane_saved(name, args.id, job, root)
    except orca.OrcaError as error:
        saved, left = None, str(error)
    unsaved = left if here else None
    branches = [] if finished or unreached else orca.saved(name, args.id, job, root, every=bool(unsaved))
    continued = None
    if unreached:
        continued = "Orca could not reach that Sprite"
    elif left and not (here or end.unfit):
        continued = f"what that Sprite holds on no remote could not be saved to GitHub: {left}"
    own = job["workspace"] if finished or continued else None
    route = state.LOCAL_ROUTE if here else job["route"]
    with state.edit(name) as data:
        job = data["jobs"][args.id]
        state.lane_died(job, dispatch, end.why, state.now(), cause)
        if saved:
            job["deaths"][-1]["saved"] = saved
        if transcript:
            job["deaths"][-1]["transcript"] = str(transcript)
        data["jobs"][args.id] = job | {"route": route}
    ended = job["deaths"][-1]
    kept = launch_brief(name, args.id, job)
    attempt = kept.stem
    kept.parent.mkdir(exist_ok=True)
    brief = orca.vm_brief(name, args.id, orca.handoff(rebriefed(name, args.id), ended, branches, job, message, end, continued, here, unsaved), route)
    was = orca.place(job)
    if same:
        kept.write_text(brief)
        try:
            receipt = orca.start_after(name, orca.lane_name(name, attempt), kept, end)
        except orca.OrcaError as error:
            if why := orca.gone(error):
                return restart(args, name, dispatch, orca.LaneEnd(why, end.branch, None), text)
            raise
    else:
        try:
            receipt = orca.adopted(kept) or launch_attempt(name, job | {"route": route}, attempt, kept, brief, root, own, None)
        except orca.Unstarted as error:
            if not own:
                raise
            return restart(args, name, dispatch, orca.LaneEnd(f"{end.why}, and {error}", end.branch, None, unfit=True), text)
    started = {field: receipt[field] for field in LAUNCHED}
    record_start(name, args.id, started, str(kept))
    carrying = "in a handoff carrying its brief, its branch, its last line, its report and its PRs"
    local = here or state.on_mac(job)
    went = "the message went to the job's next worker" if message is not None else "the job's next worker"
    if same:
        line = (
            f"{args.id}: {went}, started in its terminal {orca.ON_MAC}"
            f" as dispatch {started['dispatch']} {carrying}; its lane {was} had finished ({end.why})"
        )
    else:
        source = "its worktree" if state.on_mac(job) else ended["workspace"]
        line = (
            f"{args.id}: {went}, relaunched {'in a fresh worktree ' + orca.ON_MAC if local else orca.place({**job, **started})}"
            f" as dispatch {started['dispatch']} {carrying}; its lane {was} {'had finished' if end.worktree or end.live else 'was dead'} ({state.why_died(end.why, cause)}), and "
            + (
                f"it continues in the checkout it left on {source}, which cc-remote prepared again with the tools and keys a fresh Sprite gets"
                if finished
                else f"{source} is kept, since what it holds on no remote could not be saved: {unsaved}"
                + (f"; the job's lanes saved on GitHub {', '.join(branches)}" if branches else "")
                if unsaved
                else f"what {source} held on no remote is saved on GitHub as {', '.join(branches)}"
                if branches
                else f"it continues in the checkout the dead lane left on {source}, since {continued}"
                if continued
                else f"{source} is kept, since what it holds on no remote could not be saved: {left}"
                if left
                else saved or f"work not pushed from {source} is not carried over"
            )
        )
        if not left and started["workspace"] != ended["workspace"] and (released := release_sprite(name, args.id, {**job, "workspace": ended["workspace"]}, root)):
            line += f"; {released}"
        if declared:
            line += f"; it moved to this Mac, since its brief, its last report or the message declares {declared}, a step only this Mac runs"
    how = f"started {orca.ON_MAC}" if same else "relaunched"
    return emit(args, line, {"id": args.id, "how": how, "ended": ended, "branches": branches, "handoff": str(kept), **started})


def resume_session(args: argparse.Namespace, name: str, dispatch: str, end: orca.LaneEnd, message: str | None, session: str, root: Path, cause: str | None) -> int:
    """Resume the Claude session of the job's lane that died on its Sprite, on that Sprite in the checkout it left, on a Run this terminal makes, so the worker keeps its whole conversation and nothing is saved, moved or destroyed; its death keeps the cause line read off its Sprite, and its brief and line say why it died with that cause and carry the message, when one came. A resume an earlier restart of the same death started and never recorded is recorded from its kept receipt. Unstarted when no worker started."""
    with state.edit(name) as data:
        job = data["jobs"][args.id]
        state.lane_died(job, dispatch, end.why, state.now(), cause)
    ended = job["deaths"][-1]
    attempt = f"{args.id}-{len(job['deaths']) + 1}"
    kept = state.drive_dir(name) / "jobs" / f"{attempt}.resume.md"
    kept.parent.mkdir(exist_ok=True)
    receipt = orca.adopted(kept) or launch_attempt(name, job, attempt, kept, orca.resumed(ended, message), root, ended["workspace"], session)
    started = {field: receipt[field] for field in LAUNCHED}
    record_start(name, args.id, started, str(kept))
    went = "the message went to the job's worker, which" if message is not None else "the job's worker"
    line = f"{args.id}: {went} resumed its own Claude session {session} on Sprite {ended['workspace']} as dispatch {started['dispatch']}, in the checkout its lane left there dead ({state.why_died(end.why, cause)})"
    return emit(args, line, {"id": args.id, "how": "resumed", "ended": ended, "handoff": str(kept), "session": session, **started})


def job_read(args: argparse.Namespace) -> int:
    """Answer one question a remote lane queued, as the watch's reader."""
    name = state.resolve(args.drive)
    said, why = watch.answer_read(name, args.id, args.message)
    return emit(args, why or said, {"id": args.id, "message": args.message, "answer": said, "why": why})


def job_answered(args: argparse.Namespace) -> int:
    """Keep the answer an older install's reader came to while this install saved the drive, which watch.answer_kept hands here."""
    name, receipt = state.resolve(args.drive), Path(args.receipt)
    kept = json.loads(receipt.read_text())
    watch.answer_kept(name, args.id, kept["message"], kept["answer"])
    receipt.unlink()
    said, why = kept["answer"]
    return emit(args, why or said, {"id": args.id, "message": kept["message"], "answer": said, "why": why})


def job_replies(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    job = find(state.load(name)["jobs"], args.id, "job")
    kept = state.replies(name, args.id)
    said = [f"{reply['created_at']} {reply['type']}: {reply['body'] or reply['subject']}" for reply in kept]
    if woke := state.resumed(job):
        said.append(f"{woke} resumed: a message woke its agent {job['agent']} after its last return, and it runs on toward its next line")
    return emit(args, "\n\n".join(said) or f"{args.id} has sent nothing yet", kept)


def job_held(args: argparse.Namespace) -> int:
    from drive import view

    name = state.resolve(args.drive)
    job_id = args.id or os.environ.get(state.OWN_JOB_ENV)
    if not job_id:
        raise DriveError("job held needs the job: name it, or run it from a Sprite lane, whose own job it reads")
    data = state.load(name)
    find(data["jobs"], job_id, "job")
    if args.release:
        hold = settled.hold(data["roles"]["coordinator"]["cwd"])
        refused = hold and brief.hold_refusal(args.release, data, hold)
        return emit(args, refused or f"no hold of his refuses {args.release}", {"id": job_id, "release": refused})
    live = view.live_cards(name)
    if args.pr:
        tracked = {stored.lower(): pr for stored, pr in data["prs"].items()}
        held = {key: land.pr_cards(data, pr, live) if (pr := tracked.get(key.lower())) else [] for key in map(state.pr_key, args.pr)}
        line = "\n".join(f"{key}: {', '.join(cards)}" for key, cards in held.items() if cards)
        return emit(args, line or f"no open card of his holds {', '.join(held)}", {"id": job_id, "prs": held})
    cards = land.ship_held(data, job_id, live, args.exempt)
    return emit(args, "\n".join(cards) or f"{job_id}: no open card of his holds its PR", {"id": job_id, "cards": cards})


def job_wake(args: argparse.Namespace) -> int:
    """Send the text to the job's lane as a message to its Dispatch while Orca holds its worker taking messages, under the job's own lock as drive pr wake sends one, and refuse, sending and starting nothing, a job that is stopped, whose lane ended or that no lane Orca holds runs: the one way the watchdog's agent messages the job answering for a stall, by his pick "Report, plus two safe acts, rerunning a failed build and messaging the owning job", never starting a worker."""
    name = state.resolve(args.drive)
    if not find(state.load(name)["jobs"], args.id, "job").get("dispatch"):
        raise DriveError(f"{args.id}: refused, nothing sent: no lane Orca holds runs it, and an agent on this Mac takes a message only from its own session")
    with state.job_locked(name, args.id, orca.LAUNCH_SECONDS):
        job = state.load(name)["jobs"][args.id]
        if job["state"] == "stopped":
            raise DriveError(f"{args.id}: refused, nothing sent: it is stopped")
        if orca.lane_end(name, job):
            raise DriveError(f"{args.id}: refused, nothing sent or started: its lane ended, and drive job wake sends only to a lane that takes messages")
        how = orca.send(name, job, f"{args.id}: the watchdog's finding", args.text)
    return emit(args, f"{args.id} {how}", {"id": args.id, "how": how})


def lane_saved(drive: str, job_id: str, job: dict, root: Path) -> str | None:
    """What the save of the job's current lane found, as orca.save says it: the one its death records when an earlier resume or end of that lane saved its Sprite, since a dead lane's Sprite changes no more and that end destroys it, so a read now finds cc-remote recording no such Sprite, else what a save finds now."""
    return (state.lane_death(job) or {}).get("saved") or orca.save(drive, job_id, job, root)


def release_sprite(drive: str, job_id: str, job: dict, root: Path) -> str | None:
    """Destroy the Sprite cc-remote still records for the job's lane once what it holds on no remote is saved to GitHub as saved/<lane>/<branch>; what happened to it, or None when none is recorded."""
    if not orca.holds_sprite(job):
        return None
    try:
        return orca.release(drive, job_id, job, root) or None
    except orca.OrcaError as error:
        return f"{job['workspace']} is kept: {error}"


def job_prove(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    find(state.load(name)["jobs"], args.id, "job")
    line = install.prove(name, args.id, args.timeout)
    return emit(args, line, {"id": args.id, "line": line})


def job_proved(args: argparse.Namespace) -> int:
    """Keep what an older install's `drive job prove` came to while this install saved the drive, which install.proof_kept hands here."""
    name, receipt = state.resolve(args.drive), Path(args.receipt)
    line = install.proof_kept(name, args.id, json.loads(receipt.read_text()))
    receipt.unlink()
    return emit(args, line, {"id": args.id, "line": line})


def job_end(args: argparse.Namespace) -> int:
    """Free a remote job's lane: its Sprite, destroyed once what it holds on no remote is saved to GitHub, whatever the job's state, or its terminal and clean worktree on this Mac. The job keeps the state its line set, with its waits, PRs and carried parts, so a waiting job's next event starts its next lane and nothing waiting on it is freed. A lane whose death the watch recorded first has its Sprite's Orca runtime and worker logs copied to the drive's folder by orca.keep_logs, and every lane's Sprite has each Claude Code transcript it holds copied whole there by orca.keep_transcripts before anything can destroy it, kept on the lane's death. A job still at work first has what its Sprite holds that its next lane would not get saved to GitHub, by lane_saved, and its line, as that lane's handoff will, names each branch GitHub then holds under saved/<its lane>/, by one read, or, when GitHub holds none, what that save found; refused, ending nothing, when that Sprite cannot be read or the save fails, and for a running job, whose lane has returned no line, so its next event, never its end, comes next. The end holds the job's own lock, as a launch, resume or PR wake does, so it never destroys a Sprite a relaunch is starting the job's next lane on and refuses a job that relaunch set running. The end is recorded on the lane's dispatch before anything is saved, stopped or destroyed, so the watch reads none of it as the lane dying, and an end of a lane another end already ended changes nothing."""
    name = state.resolve(args.drive)
    with state.job_locked(name, args.id, orca.LAUNCH_SECONDS):
        data = state.load(name)
        job = started_job(data, args.id)
        if job["state"] == "running":
            raise DriveError(
                f"{args.id}: refused, nothing ended: it is running, so its lane has returned no line to end it on; drive job resume {args.id} sends a lane that stopped"
                f" without one its next event, and drive job set {args.id} --state stopped ends it unfinished"
            )
        root = orca.checkout(name, data)
        with state.edit(name) as data:
            ended = data["jobs"][args.id]
            if not (already := state.lane_ended(ended)):
                fresh = state.lane_died(ended, job["dispatch"], "ended by drive job end", state.now())
                state.lane_death(ended)["ended"] = {"pid": os.getpid(), "at": state.stamp()}
        if already:
            line = f"{args.id}'s lane was already ended by the drive job end of {state.lane_death(ended)['ended']['at']}, so nothing changed; {args.id} stays {ended['state']}"
            return emit(args, line, {"id": args.id, "state": ended["state"], "already": True})
        if not (fresh or state.on_mac(job)):
            orca.keep_logs(name, args.id, job, job["dispatch"])
        saved = None
        if job["state"] not in state.FINISHED_JOB_STATES:
            try:
                saved = lane_saved(name, args.id, job, root)
            except orca.OrcaError as error:
                end_kept(name, args.id, {"failed": True, "fresh": fresh, "saved": None})
                raise DriveError(
                    f"{args.id}: refused, nothing ended: it is {job['state']}, and what {job['workspace']} holds on no remote could not be saved"
                    f" to GitHub for its next lane: {error}. End it again once that Sprite answers"
                ) from None
        transcript = None if state.on_mac(job) else orca.keep_transcripts(name, args.id, job, job["dispatch"])
        reclaimed = orca.reclaim(job["run"], job["dispatch"], root)
        released = release_sprite(name, args.id, job, root)
        ended = end_kept(name, args.id, {"failed": False, "fresh": fresh, "saved": saved, "transcript": transcript and str(transcript)})
        branches = orca.saved(name, args.id, job, root) if saved else []
        line = f"{args.id}'s lane ended; {args.id} stays {ended['state']}" + (f", and what its Sprite held on no remote is saved on GitHub as {', '.join(branches)}" if branches else f", and {saved}" if saved else "")
        return emit(args, "\n".join(filter(None, [reclaimed, released, line])), {"id": args.id, "reclaim": reclaimed, "released": released, "state": ended["state"], "saved": saved, "branches": branches})


def end_kept(name: str, job_id: str, result: dict) -> dict:
    """Keep on the job what its lane's end came to once it saved, reclaimed and released: the branches saved, its kept transcript and its ask cleared, the job left unstamped, since the end of a lane at rest changes nothing it does and so says none of its parts again, or, when the save failed, the end mark taken back, so the lane reads as it did; when a newer install saved the drive meanwhile, the newest install's `drive job ended` keeps it, so the end is never left half kept."""
    try:
        with state.edit(name) as data:
            ended = data["jobs"][job_id]
            if result["failed"]:
                death = state.lane_death(ended)
                if result["fresh"]:
                    ended["deaths"].remove(death)
                else:
                    del death["ended"]
            else:
                if result["saved"]:
                    ended["deaths"][-1]["saved"] = result["saved"]
                if result.get("transcript"):
                    ended["deaths"][-1]["transcript"] = result["transcript"]
                ended["ask"] = None
        return ended
    except state.NewerDrive:
        if not (newer := install.upgraded()):
            raise
    receipt = state.drive_dir(name) / "jobs" / f"{job_id}.ended.{os.getpid()}.json"
    return json.loads(install.handed(newer, name, ["job", "ended", job_id], receipt, result, f"{job_id}: its lane's end", orca.LAUNCH_SECONDS))


def job_ended(args: argparse.Namespace) -> int:
    """Keep what an older install's `drive job end` came to while this install saved the drive, which end_kept hands here."""
    name, receipt = state.resolve(args.drive), Path(args.receipt)
    ended = end_kept(name, args.id, json.loads(receipt.read_text()))
    receipt.unlink()
    return emit(args, f"{args.id}'s lane end is kept; {args.id} stays {ended['state']}", ended)


def pr_add(args: argparse.Namespace) -> int:
    from drive import view

    name = state.resolve(args.drive)
    key = state.pr_key(args.url)
    if args.no_tap and not args.why:
        raise DriveError("--no-tap needs --why <reason>: every PR waits on his tap unless it is a port, exempt or drive tooling outside Forge-AI")
    if args.no_tap and state.guarded(key):
        raise DriveError(UNLANDABLE.format(key=key, done="added"))
    lead = {line: given for line, flag in limits.LEAD_LINES if (given := getattr(args, flag.removeprefix("--")))}
    kept = state.load(name)["prs"].get(key, {}).get("lead")
    pr = add_pr(name, key, args.job, github_pr(key), args.port_of, args.exempt, args.no_tap, lead)
    shown = view.card_for((view.live_cards(name) or view.NO_CARDS)["pr_cards"], pr["url"])
    drawn = state.after_save(name, lambda: [releaded(pr) if pr["lead"] != kept else ""] if shown else [drawn_card(name, key, pr)])
    return emit(args, f"{key}{''.join(drawn)}", {"key": key, **state.load(name)["prs"][key]})


def drawn_card(name: str, key: str, pr: dict) -> str:
    """Draw his card on a PR waiting on his tap in the call that registers it, so no session's turn stands between the PR and his card; what came of it. A draw that fails leaves its stage uncarded, which the watch draws on its next read."""
    from drive import stage, talk

    if not stage.carded(pr) or pr["tap"]:
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


def pr_wake(args: argparse.Namespace) -> int:
    """Send a PR's stage line, or the text given, to the builder answering for it, as its stage owes: to a lane Orca holds as a message to its Dispatch, which neither answers its question nor touches its job's ask or state; to a fresh worker on its job when that lane finished or died, as drive job resume relaunches one; and to a builder this Mac's session holds as a handoff record to the session holding its order, which sends it to that agent. A PR whose own job is stopped, as his superseding answer stops one, or whose job stopped while the wake waited on its lock, goes to that session the same way, so no wake starts work he called off. A lane's send or relaunch runs under the job's own lock, as drive job resume's does, so a second wake arriving while the first starts the job's next worker waits for it and sends that worker its line."""
    from drive import stage, tools, view

    name = state.resolve(args.drive)
    key = state.pr_key(args.url)
    data = state.load(name)
    if args.text is None and (cards := view.live_cards(name)) is None:
        raise DriveError(f"{key}: his cards could not be read, so its stage is unknown; pass --text")
    text = args.text or stage.line(key, data["prs"][key], stage.of(data, key, cards))
    job_id = stage.owner_job(data, key)
    if job_id and data["jobs"][job_id].get("dispatch"):
        with state.job_locked(name, job_id, orca.LAUNCH_SECONDS):
            data = state.load(name)
            job = data["jobs"][job_id]
            if job["state"] != "stopped":
                if end := orca.lane_end(name, job):
                    return restart(argparse.Namespace(id=job_id, text=text, drive=args.drive, json=args.json), name, job["dispatch"], end, text)
                how = orca.send(name, job, f"{key}: its next step is yours", text)
                return emit(args, f"{key}: {job_id} {how}", {"key": key, "job": job_id, "how": how})
        job_id = None
    pr = data["prs"][key]
    job = data["jobs"][job_id or pr["job"]]
    lane = state.job_lane(data, job)
    said = (
        f"STAGE {text}; its builder {job_id} is agent {job.get('agent')} of session {job.get('session')}: send it this line verbatim and alone"
        if job_id
        else f"STAGE {text}; no live job answers for it and its job {pr['job']} is {job['state']}, so no wake starts it: close it, or move it to a live job with drive pr add {pr['url']} --job <j>"
    )
    topic = f"wake-{key}-{pr['head'][:10]}"
    tools.sent(name, "watch", [{"kind": "handoff", "to": [f"{lane}@{name}"], "topic": topic, "text": said}])
    return emit(args, f"{key}: sent to {lane}@{name} for {job_id or pr['job']}", {"key": key, "job": job_id, "how": f"handoff to {lane}"})


def add_pr(name: str, key: str, job_id: str, read: dict, port_of: int | None = None, exempt: str | None = None, no_tap: bool = False, lead: dict | None = None) -> dict:
    """Register a PR on its job from its GitHub read, refused as `drive pr add` refuses, keeping the title it read, so a PR merged and proved before the watch's next read is named by it; the PR as registered, the job's first in place of each wait on its change. `drive pr add` runs it, and `drive job report` for each PR a builder's READY or PUSHED names that no `drive pr add` registered, so every PR a result names lands as one its builder added does, and for each GitHub PR the drive does not track that the FIX line of a report tracing a live bug's cause links. Someone else's PR no order of his names is refused before any check that would have its title or body edited. The lead lines given are kept over those kept before, and a PR his card asks his tap on is refused, nothing written, until its lead tells him why it is made, what it does and what it changes downstream, by limits.lead_problems; a FIX line's PR, given no lead, is someone else's and kept as it reads."""
    from drive import stage

    url = state.pr_url(key)
    known = state.load(name)
    added = find(known["jobs"], job_id, "job")
    author = read["author"]["login"]
    if why := state.foreign(key, author, [known["orders"][added["order"]]]) or state.pr_held_elsewhere(known, key, job_id):
        raise DriveError(f"{key}: refused, nothing added: {why}")
    refuse_an_unkinded_title(key, read, port_of)
    refuse_an_undrawn_show_me(name, key, read)
    if key not in known["prs"] and (seen := brief.surface(added)) and brief.stories(added["brief_flags"].get("interactions")) and not added.get("repro"):
        raise DriveError(
            f"{key}: refused, nothing added: {job_id} builds something he sees ({seen}), so each user story's strict acceptance criteria"
            f" come before the build: write them into the proof file that clicks each story on the installed surface, keep it with"
            f" drive job set {job_id} --repro <file>, then add this PR, by his words {brief.STORIES_RULING}"
        )
    if port_of:
        refuse_a_port_storing_data(key, port_of)
    if (exempt or no_tap) and (incidents := state.open_incidents(known, [added["order"], *added["carries"]])):
        refuse_an_exempt_revert(known, key, incidents[0], read, f"Add it waiting on his tap with drive pr add {url} --job {job_id}. ")
    if exempt and exempt != state.FLAKE_FIX:
        refuse_an_unexempt(key, exempt, "added")
    wants_tap = state.guarded(key) or not tooling(known["orders"][added["order"]])
    flaky = flake_tap(key, wants_tap) if exempt == state.FLAKE_FIX else None
    with state.edit(name) as data:
        job = find(data["jobs"], job_id, "job")
        if data["orders"][job["order"]]["once"]:
            raise DriveError(f"refused, {job['order']} is a repair he asked to run once: run it, and put up no PR for it")
        pr = data["prs"].setdefault(key, state.new_pr(url, job_id, job["order"]) | {"wants_tap": wants_tap})
        pr.update(order=job["order"], author=author, title=read["title"])
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
        if lead is not None and stage.carded(pr) and (problems := limits.lead_problems(pr["lead"] or {})):
            flags = " ".join(f'{flag} "<{line}>"' for line, flag in limits.LEAD_LINES)
            raise DriveError(f"{key}: refused, nothing added: his card opens with its lead, and {'; '.join(problems)}. Add it with drive pr add {url} --job {job_id} {flags}")
        state.take_pr(data, job_id, key)
    note_third_person(key, read)
    return pr


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
    if args.flaky and (fix := state.pr_key(args.flaky[1])) not in known["prs"]:
        raise DriveError(f"--flaky {args.flaky[1]}: the drive tracks no such PR, so it would never read that fix merge; drive pr add {state.pr_url(fix)} --job <j> tracks it first")
    if args.flaky and not (before["ejected"] and (before["lander"] or {}).get("head") == before["head"]):
        raise DriveError(f"{key}: refused, nothing set: --flaky names the flaky test Trunk ejected it on at its head, and Trunk ejected no try of its lander at {before['head'][:7]}")
    if args.exempt == state.FLAKE_FIX and args.tap is None:
        args.tap = flake_tap(key, state.guarded(key) or not tooling(known["orders"][before["order"]]))
    after = dict(before)
    set_pr(after, args)
    if state.guarded(key) and (before["wants_tap"] or before.get("exempt")) and not (after["wants_tap"] or after.get("exempt")):
        raise DriveError(UNLANDABLE.format(key=key, done="set"))
    dropped = known["prs"][key]["tap"]
    if args.no_tapped:
        refuse_a_tap_of_his(name, key, dropped)
    order = known["prs"][key]["order"]
    delivers = before["state"] == "merged" and known["jobs"][before["job"]]["state"] == "done"
    if args.part:
        refuse_a_part_its_job_lacks(known, key, args.part, delivers)
    if args.serves:
        refuse_a_crossed_order(name, known, order, args.serves, f"{key} --serves")
    if (args.serves or args.design) and (refused := state.held_elsewhere(known, order, os.environ.get("CLAUDE_CODE_SESSION_ID"))):
        raise DriveError(f"{key}: refused, nothing set: {refused}")
    if args.serves:
        refuse_an_unwritten_rule(known, key)
    answered = served.answers(known, [args.design], view.live_cards(name)) if args.design else None
    if args.serves and not args.design and (known["prs"][key]["wants_tap"] if args.tap is None else args.tap):
        raise DriveError(
            f"--serves on a PR waiting on his tap needs --design asked:<{served.NAMES}> for each design decision"
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
    found = check_waits(name, args.waits)
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
    if args.exempt and args.exempt != state.FLAKE_FIX:
        refuse_an_unexempt(key, args.exempt, "set")
    serving = "".join(f"\nhis answer its design cites, kept: {answer}" for answer in answered or [])
    with state.edit(name) as data:
        pr = find(data["prs"], key, "drive PR")
        if args.serves:
            serving += f"\nserves {args.serves}, his words: {find(data['orders'], args.serves, 'order')['text']}"
        set_pr(pr, args, answered)
        pr["waits"] = merged_waits(data, pr, args.waits, args.no_waits, found)
        if args.part:
            serving += f"\ndelivers {'; '.join(pr['parts'])}"
        for part in args.part if delivers else []:
            data["orders"][order]["parts"][part]["posted"] = pr["url"]
    untapped = f"\ndropped the {dropped['label']} the drive gave itself ({dropped['source']}): {args.why}" if args.no_tapped else ""
    lane = state.heard_lane(data, order)
    withdrawn: list[str] = []
    told = state.after_save(name, lambda: set_outside(name, key, pr["url"], args, dropped, lane, withdrawn))
    out = "".join(f"\n{shipping}, so it is not a wait" for shipping in shipped.values())
    view.refresh_cards(name)
    found, source = view.stored_cards(name)
    found = found if source == "live" else None
    now = state.load(name)
    stage.shown(now, found, [key] if now["prs"][key]["state"] == "open" else [])
    line = render.pr_line(key, now["prs"][key], view.pr_card_words(found, source, now["prs"][key])) + serving + untapped + out + "".join(told)
    return emit(args, line, {"key": key, "withdrawn": withdrawn, **pr})


def set_outside(name: str, key: str, url: str, args: argparse.Namespace, dropped: dict | None, lane: str, withdrawn: list[str]) -> list[str]:
    """What drive pr set does outside the drive once its write is kept, a line each: the hold the drive gave itself kept dropped on its events, his open card withdrawn when the PR no longer waits on his tap, each such card added to withdrawn, and each card of his carrying it redrawn in place to show what the set stored."""
    if args.no_tapped:
        repo, number = key.split("#")
        state.append_event(name, "untapped", repo=repo, number=int(number), label=dropped["label"], source=dropped["source"], why=args.why, lane=lane)
    if why := withdrawal(args):
        try:
            withdrawn += slack.withdraw_pr_card(url, why)
        except slack.SlackError as error:
            raise DriveError(f"{key}: withdrawing its open card failed, so his Approve still shows: {error}; run this again") from None
    corrected = correct_card(name, key, url, *redrawn) if (redrawn := redraw_words(args, dropped)) else ""
    return [*(f"\nwithdrew his card {card}: {why}" for card in withdrawn), corrected]


def refuse_a_part_its_job_lacks(data: dict, key: str, parts: list[str], delivers: bool) -> None:
    """Refuse a --part its job delivers on no order, since the PR's change is judged against the parts its job's PRs name; a merged PR whose job returned DONE names any part of its order, which it then delivers, since it shipped work its job was never given."""
    pr = data["prs"][key]
    job = data["jobs"][pr["job"]]
    delivered = list(data["orders"][pr["order"]]["parts"]) if delivers else [*job["parts"], *(part for carried in job["carries"].values() for part in carried)]
    if unknown := [part for part in parts if part not in delivered]:
        listed = ", ".join(f'"{part}"' for part in delivered)
        lacks = f"its order {pr['order']} has" if delivers else f"its job {pr['job']} delivers"
        raise DriveError(f"{key}: refused, nothing set: {lacks} no part {', '.join(unknown)}; its parts are {listed}")


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


def check_waits(name: str, waits: list[str]) -> dict | None:
    """Refuse a wait that names nothing the drive can wait on, and give back his cards as last stored, which view.stored_waits reads each card's waits id from."""
    from drive import view

    if not waits:
        return None
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
                " <owner>/<repo>#<n>, held until it merges, or once it closes unmerged on the job that opened it, or release:<service>:<owner>/<repo>#<n>, the release of"
                " that service carrying that PR"
            )
    if refused := unreached(name, data, waits):
        raise DriveError(f"--waits {refused}")
    return found


def unreached(name: str, data: dict, waits: list[str]) -> str | None:
    """Why a release wait among waits names a service whose release never carries its PR's change, by release_reach: the service is no component go/ci releases, or the paths reach components and the service is none of them. None for a PR release_reach reads nothing for, and for one whose paths reach no component by that map, such as one changing only shared Pulumi code, which go/ci reaches only through the rendered rows a Buildkite job reads."""
    releases = [found for wait in waits if (found := state.RELEASE_WAIT.fullmatch(wait))]
    if not releases:
        return None
    named = ", ".join(found[0] for found in releases)
    try:
        answers = release_reach(name, data, [found["pr"] for found in releases])
    except github.GithubError as error:
        raise DriveError(f"could not read which components the paths each PR of {named} changes reach, so nothing was taken: {error}; run this again") from None
    for found in releases:
        if not (answer := answers.get(found["pr"])):
            continue
        key, service = found["pr"], found["service"]
        reach = f"`tools/ci release select --pr` lists the paths {key} changes reaching {', '.join(answer['touched']) or 'no component'}"
        if service not in answer["components"]:
            return f"{found[0]}: {service} is no component go/ci releases, so no release ever carries {key}; {reach}"
        if answer["touched"] and service not in answer["touched"]:
            return f"{found[0]}: {service}'s release never carries {key}, since {reach}; wait on the release of the one that runs the change"
    return None


def release_reach(name: str, data: dict, keys: list[str]) -> dict[str, dict]:
    """go/ci's own map of what each PR among keys reaches, `tools/ci release select --pr` run in the drive's checkout at the head of its default branch on the paths the PR changes: its answer's components, every one go/ci releases, and touched, those the paths reach. Nothing for a PR outside the repository of a coordinator checkout holding tools/ci."""
    clone = data["roles"]["coordinator"]["cwd"]
    if not (keys and (Path(clone) / GO_CI).is_file()):
        return {}
    repo = github.checkout_repo(clone).casefold()
    if not (ours := list(dict.fromkeys(key for key in keys if github.repo_of(key).casefold() == repo))):
        return {}
    checkout = orca.checkout(name, data)
    return {
        key: json.loads(github.run([str(checkout / GO_CI), "release", "select", "--pr"], cwd=str(checkout), stdin=json.dumps({"body": "", "paths": github.files(key)})))
        for key in ours
    }


def merged_waits(data: dict, owner: dict, waits: list[str], no_waits: bool, found: dict | None) -> list[str]:
    from drive import view

    return view.stored_waits(data, [*([] if no_waits else owner["waits"]), *waits], found)


def set_pr(pr: dict, args: argparse.Namespace, answered: list[str] | None = None) -> None:
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
        pr["tap"] = state.new_tap(args.tapped, pr["head"], args.tap_source)
        pr["fundamental"] = None
    if args.no_tapped:
        pr["tap"] = None
    if args.flaky:
        pr["flaky"] = {"test": args.flaky[0], "fix": state.pr_key(args.flaky[1]), "head": pr["head"], "requeued": None}
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


def refuse_an_undrawn_show_me(name: str, key: str, read: dict) -> None:
    """Each picture of a PR's Show me, a fence or an image link, draws as his card will draw it, through the same cache, since a card whose picture does not draw shows him a Show me with nothing in it."""
    from drive import card

    for kind, source, _ in picture.sources(card.sections(card.body_of(read)).get("show me", "")):
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


def refuse_an_unexempt(key: str, exempt: str, done: str) -> None:
    """Refuse an exempt class the PR is not in, by land.outside, the class check the lander runs before it lands an exempt PR, on the PR as GitHub has it now, before anything is kept, so no class the lander would refuse takes Approve off his card, by his ruling to keep a lane's exempt word and check the class; a flake fix outside test and fixture code keeps his tap instead, by flake_tap."""
    if why := land.outside(exempt, served.change(key)):
        raise DriveError(f'{key}: refused, nothing {done}: it is no "{exempt}": {why}, by the class check the lander runs before it lands an exempt PR')


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


def tap_verdict(pr: dict | None) -> tuple[bool, str]:
    if pr is None:
        return True, "not a drive PR"
    if pr["hold_label"]:
        return False, "held by the drive with the hold label"
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


def pr_tapped(args: argparse.Namespace) -> int:
    key = state.pr_key(args.url)
    pr = state.load(state.resolve(args.drive))["prs"].get(key)
    may_land, why = tap_verdict(pr)
    emit(args, f"{key}: {why}", {"key": key, "may_land": may_land, "why": why})
    return 0 if may_land else 1


def pr_land(args: argparse.Namespace) -> int:
    keys = [state.pr_key(url) for url in args.urls]
    name = state.resolve(args.drive)
    outcome = land.record(name, keys, args.outcome, args.below, dict(args.asked)) if args.outcome else land.run(name, keys, args.dry_run)
    return emit(args, f"{', '.join(keys)}: {outcome}", {"keys": keys, "outcome": outcome})


def pr_ci(args: argparse.Namespace) -> int:
    key = state.pr_key(args.url)
    repo, number = key.split("#")
    sha = github.read(f"{key}'s merge commit", ["gh", "pr", "view", number, "-R", repo, "--json", "mergeCommit", "-q", '.mergeCommit.oid // ""']).strip()
    if not sha:
        raise DriveError(f"{key} is not merged, so no CI run on its default branch judges it")
    runs = install.ci_verdict(repo, sha)
    lines, unpassed = [line for line, _ in runs], [line for line, passed in runs if not passed]
    verdict = f"not passed: {'; '.join(unpassed)}" if unpassed else "passed"
    emit(args, "\n".join([*lines, f"{key} merged at {sha[:12]}: {verdict}"]), {"pr": key, "sha": sha, "passed": not unpassed, "runs": lines})
    return 1 if unpassed else 0


def pr_check(args: argparse.Namespace) -> int:
    """Judge one PR against his words as the watch's check."""
    key, name = state.pr_key(args.url), state.resolve(args.drive)
    outcome = served.verdict(name, key)
    return emit(args, f"{key}: {outcome}", {"key": key, "outcome": outcome})


def pr_checked(args: argparse.Namespace) -> int:
    """Keep the outcome an older install's `drive pr check` reached while this install saved the drive, which served.kept hands here."""
    key, name, receipt = state.pr_key(args.url), state.resolve(args.drive), Path(args.receipt)
    outcome = served.kept(name, key, json.loads(receipt.read_text()))
    receipt.unlink()
    return emit(args, f"{key}: {outcome}", {"key": key, "outcome": outcome})


def answer(args: argparse.Namespace) -> int:
    """Record an answer or ruling of his against the cards asking him, orders and parts undelivered and PRs not yet live that it could change, those of the job and order of the card it answers, those its words name, each other question card Jev reads it as changing, and those of each open order a pick of his binds that the card asked him to lift, and the jobs waiting on their orders: refuse until each card, order, part and PR is marked standing, settled, dropped, superseded or reopened by it and each job sent his words or untouched, either mark taking his words off the texts that job holds, show his answer on each card it settles, redraw each it settles in part to the ASK its job keeps for what is left, withdraw each card it drops without stopping its job, drop each order it drops, or whose every part it drops, stopping the live jobs whose order it is and marking in cc-notes each pick binding it superseded by his answer, take each other part it drops off its order, jobs and PRs, close on GitHub each open PR it drops, settle each superseded card in place, withdraw each card carrying a reopened PR until its builder's READY says how his words are met, and close on GitHub an open PR superseded outright; any other card, part or PR still asking him may be marked too."""
    from drive import view

    name = state.resolve(args.drive)
    found = view.live_cards(name)
    if found is None:
        raise DriveError("his cards could not be read, so nothing can say what this answer leaves asking him; the watch rereads them every minute, run this again then")
    data = state.load(name)
    asked = superseded.asking(data, found)
    cwd = data["roles"]["coordinator"]["cwd"]
    cited = settled.shown(args.id, cwd) if args.id else None
    if cited:
        args.id = cited["id"]
    if args.id not in data["answers"] and (unowed := rewrite_refusal(data, args, cited)):
        raise DriveError(unowed)
    if args.id is None:
        if not (args.title and args.words and args.link):
            raise DriveError('name his answer\'s cc-notes id, or pass --title "<the question, verbatim>" with --words and --link to record it in cc-notes first')
        if unsaid := settled.unsaid([args.words], args.link, found, data):
            raise DriveError(f"refused, nothing recorded: {unsaid}")
        changes = settled.changed(args.title, args.words, asked)
        labels = ["scope:durable", f"drive:{name}", *settled.kind(args.words), *args.label]
        args.id = settled.recorded(args.title, args.words, labels, cwd)
    marking = args.stands or args.settled or args.dropped or args.superseded or args.reopened or args.others_stand or args.sent or args.untouched
    if args.id not in data["answers"]:
        if marking:
            raise DriveError(
                f"{args.id} is not recorded yet: run it with --words and --link alone first, which lists each card still asking him"
                " and each PR not yet live, and mark them once you have read it"
            )
        if not (args.words and args.link):
            raise DriveError(f'{args.id} is not recorded yet: pass --words "<his words, verbatim>" and --link <the permalink of his message or the card he tapped>')
        if cited:
            if unsaid := settled.unsaid([args.words, settled.his(cited["body"])], args.link, found, data):
                raise DriveError(f"{args.id}: refused, nothing recorded: {unsaid}")
            changes = settled.changed(cited["title"], args.words, asked)
        bound = superseded.binding(data, args.link, asked, cwd)
        with state.edit(name) as data:
            superseded.record(data, args.id, args.words, args.link, asked, changes, bound, state.stamp())
    done = []
    if not marking:
        with state.edit(name) as data:
            ended = superseded.answer_cards(data, args.id, data["answers"][args.id])
        done = superseded.follow(name, ended)
    kept = data["answers"][args.id]
    open_keys = superseded.unmarked(kept, asked)
    named = [*args.stands, *args.dropped, *(key for key, _ in [*args.settled, *args.superseded, *args.reopened])]
    sent = [job_id for job_id in args.sent if (kept["jobs"].get(job_id) or {}).get("mark") != "sent"]
    told = [*sent, *args.untouched]
    if twice := sorted({key for key in [*named, *told] if [*named, *told].count(key) > 1}):
        raise DriveError(f"{', '.join(twice)}: marked twice; mark each card, part or PR once, standing, settled, dropped, superseded or reopened, and each job once, sent or untouched")
    if stray := [key for key in named if key not in superseded.markable(kept, asked)]:
        raise DriveError(f"{', '.join(stray)}: not a card, order, part or PR that {args.id} has not marked\n{superseded.listing(args.id, kept, open_keys, asked, data)}")
    hearing = [job_id for key, _ in args.settled if (job_id := asked[key]["job"]) and data["jobs"][job_id]["state"] not in state.FINISHED_JOB_STATES]
    if stray := [job_id for job_id in told if job_id not in [*superseded.unheard(data, kept), *hearing]]:
        raise DriveError(
            f"{', '.join(stray)}: not a job waiting on an order {args.id} touches that is unmarked, with an agent or lane; a job with"
            f" neither hears his words from the launch that starts it\n{superseded.listing(args.id, kept, open_keys, asked, data)}"
        )
    if unreached := [job_id for job_id in sent if not state.agent_lane(data, data["jobs"][job_id])]:
        raise DriveError(
            f"{', '.join(unreached)}: refused, nothing marked: --sent records only a SendMessage to an agent a coordinator session on this Mac"
            " runs, the one delivery the drive cannot see, and no such agent runs it, so his answer has not reached it: mark the rest without it, then "
            + "; ".join(f"{job_id}: {superseded.delivery(data, job_id, data['jobs'][job_id])}" for job_id in unreached)
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
    with state.edit(name) as data:
        data["answers"][args.id]["cards"].update({key: {"mark": "stands"} for key in args.stands})
        for job_id in told:
            superseded.heard(data, job_id, data["answers"][args.id], "sent" if job_id in sent else "untouched")
    cleared: set[str] = set()
    try:
        done += [settle_card(name, args.id, kept, key, asked[key], file) for key, file in args.settled]
        dropping = superseded.whole(data, asked, args.dropped)
        bound = superseded.binding(data, kept["link"], asked, cwd) if dropping else {}
        for order_id, keys in dropping.items():
            for pick in bound.get(order_id, []):
                settled.supersede(pick, args.id, cwd)
                done.append(f"his pick {pick}, which bound {order_id}, is superseded by {args.id} in cc-notes, so ccn answer match no longer reads it as standing")
            with state.edit(name) as data:
                data["answers"][args.id]["cards"].update({key: {"mark": "dropped", "job": None, "card": None} for key in keys})
                done += superseded.drop_order(data, args.id, order_id)
        for key in args.dropped:
            entry = asked[key]
            if entry["kind"] in ("order", "part") and entry["order"] in dropping:
                continue
            if entry["kind"] == "part":
                with state.edit(name) as data:
                    data["answers"][args.id]["cards"][key] = {"mark": "dropped", "job": None, "card": None}
                    done += superseded.drop(data, args.id, entry["order"], entry["title"])
                continue
            if (card := entry["card"]) and card not in cleared or entry["kind"] == "question" and not card:
                ended_on(name, args.id, kept, key, card, "withdrawn")
                cleared.add(card)
            if superseded.stops_open_pr(data, key, None):
                superseded.close(key)
            with state.edit(name) as data:
                data["answers"][args.id]["cards"][key] = {"mark": "dropped", "job": entry["job"], "card": card}
                if entry["job"] and data["jobs"][entry["job"]]["state"] not in state.FINISHED_JOB_STATES:
                    superseded.owe(data, args.id, entry["job"])
                done += [*([f"{key}: its card {card} now shows his words and a link to them, with no buttons, its question withdrawn"] if card else []), *superseded.unasked(data, key, entry)]
        for mark, key, job_id in [*(("superseded", *pair) for pair in args.superseded), *(("reopened", *pair) for pair in args.reopened)]:
            card = asked[key]["card"]
            if mark == "reopened":
                with state.edit(name) as data:
                    data["answers"][args.id]["cards"][key] = {"mark": mark, "job": job_id, "card": card}
                done += [*rework_prs(name, [state.pr_url(key)], args.id, kept["words"]), f"{key} reopened by his answer {args.id}: {job_id} reworks it to his words, so brief {job_id} with them"]
                continue
            if card and card not in cleared or asked[key]["kind"] == "question" and not card:
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
        if args.settled or args.dropped or args.superseded or args.reopened:
            view.refresh_cards(name)
    with state.edit(name) as data:
        kept = data["answers"][args.id]
        if args.others_stand:
            kept["cards"].update({key: {"mark": "stands"} for key in superseded.unmarked(kept, asked)})
    if (left := superseded.unmarked(kept, asked)) or superseded.unheard(data, kept):
        raise DriveError("\n".join([*done, superseded.listing(args.id, kept, left, asked, data)]))
    marked = [mark["mark"] for mark in kept["cards"].values() if mark]
    heard = [mark["mark"] for mark in kept["jobs"].values() if mark]
    settles = "".join(f", {marked.count(mark)} {mark}" for mark in ("settled", "redrawn", "dropped") if mark in marked)
    line = f"his answer {args.id} recorded: {marked.count('stands')} standing{settles}, {marked.count('superseded')} superseded, {marked.count('reopened')} reopened by it"
    if heard:
        line += f"; {heard.count('sent')} waiting {'job' if heard.count('sent') == 1 else 'jobs'} sent his words, {heard.count('untouched')} untouched"
    return emit(args, "\n".join([line, *done]), {"id": args.id, **kept})


def ended_on(name: str, answer_id: str, kept: dict, key: str, card: str | None, how: str) -> None:
    """End, answered, withdrawn or superseded by his answer, each open question drawn on the card a mark names, or kept under its waits id with no card drawn yet, which then never draws, and its card follows, showing his words with no buttons; a card no question of the drive is drawn on, one asked outside any job, is closed in place."""
    from drive import superseded, view

    with state.edit(name) as data:
        closed = state.new_close(how, kept["words"], kept["link"], answer=answer_id)
        drawn = view.drawn_on(data, key, *filter(None, [card]))
        ended = [each for job_id, question in drawn if question["closed"] is None for each in superseded.end(data, job_id, question, closed)]
    if drawn:
        superseded.follow(name, ended)
    else:
        superseded.settle(card, kept, settled=how == "answered")


def settle_card(name: str, answer_id: str, kept: dict, key: str, entry: dict, file: Path | None) -> str:
    """Show his answer in place on the question card it decides, settling its job's question, or mark the card it decides in part redrawn to the ASK its job keeps asking what is left, which `drive job ask --redraws` drew under the card's waits id; either way the card's job, while live, waits to hear his words. What was done, one line."""
    job_id, card = entry["job"], entry["card"]
    if not file:
        ended_on(name, answer_id, kept, key, card, "answered")
    with state.edit(name) as data:
        job = data["jobs"][job_id]
        data["answers"][answer_id]["cards"][key] = {"mark": "redrawn" if file else "settled", "job": job_id, "card": card, **({"ask": str(file)} if file else {})}
        if job["state"] not in state.FINISHED_JOB_STATES:
            data["answers"][answer_id]["jobs"].setdefault(job_id, None)
    if file:
        return f"{key}: its card {card} is redrawn in place to {file}, asking only what his answer {answer_id} leaves open, as drive job ask {job_id} --redraws {key} drew it"
    shown = f"its card {card} now shows his answer {answer_id} with no buttons" if card else f"no card draws for it, his answer {answer_id} ending its question"
    return f"{key}: {shown}, settling {job_id}'s question, so {job_id} builds on his words"


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
    handler_id, handed = agents.handle(name, args.brief, detached=True, briefed=answering(name, args) if args.answer else None)
    if handed:
        return emit(args, f"handed to {handler_id}", {"handler": handler_id, "handed": True})
    return emit(args, f"started {handler_id}", {"handler": handler_id})


def say_record(args: argparse.Namespace) -> int:
    from drive import talk

    return emit(args, talk.record_kept(state.resolve(args.drive), Path(args.receipt)), {"receipt": args.receipt})


def handler_start(args: argparse.Namespace) -> int:
    return emit(args, agents.start_kept(state.resolve(args.drive), args.id), {"handler": args.id})


def answering(name: str, args: argparse.Namespace) -> Callable[[str], str]:
    """The brief of his read-only question's handler once its answer order, titled as --answer names and filed under no project or workstream, and the handler's reader job on it are added, naming both, the pairs in use, which the handler picks from when it files the order with drive order set, and every open order in his thread with its jobs, questions and PRs, read from the drive and its stored cards, so the handler reads no drive status."""
    held = agents.holds(args.brief)
    if not held["link"]:
        raise DriveError("--answer: refused, no handler started: it adds his answer order from his message, so the brief carries his words and the Link of his message")

    def briefed(handler_id: str) -> str:
        words = agents.his_words(args.brief)
        order_id = added_order(parser().parse_args(["order", "add", words, "--from", held["link"], "--kind", "answer", "--complex", "--title", args.answer, "--drive", name]), unfiled=True)[0]
        job_id = added_job(parser().parse_args(["job", "add", "reader", "--order", order_id, "--goal", args.answer, "--agent", handler_id, "--drive", name]))[0]
        data = with_cards(name, state.load(name))
        thread = state.own_thread(data["orders"][order_id])
        work = [
            line
            for other_id, other in state.open_orders(data).items()
            if other_id != order_id and thread in slack.order_threads(data, other_id, other)
            for line in render.order_block(data, other_id, other)
        ]
        return "\n".join(
            [args.brief.strip(), f"{agents.ORDER}: {order_id}, job {job_id}", f"{agents.PAIRS}: {pairs_in_use(data)}", f"{agents.IN_THREAD}:{'' if work else ' none'}", *work]
        )

    return briefed


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
    stage.shown(data, found, state.open_pr_keys(marked))
    return data


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


def only(data: dict, ids: list[str]) -> dict:
    """The drive narrowed, never saved, to the orders the ids name, directly or through one of their jobs or PRs, with their jobs and PRs; data stays whole, since who carries a part is read across every job and order."""
    keys = {drive_key(data, key) for key in ids}
    if unknown := keys - set(data["orders"]) - set(data["jobs"]) - set(data["prs"]):
        raise DriveError(f"no order, job or PR {', '.join(sorted(unknown))}; name {ID_FORMS}")
    named = {key if key in data["orders"] else (data["jobs"].get(key) or data["prs"][key])["order"] for key in keys}
    orders = {key: order for key, order in data["orders"].items() if key in named}
    jobs = {job_id for order in orders.values() for job_id in order["jobs"]}
    return data | {
        "orders": orders,
        "jobs": {key: job for key, job in data["jobs"].items() if key in jobs or job["order"] in named},
        "prs": {key: pr for key, pr in data["prs"].items() if pr["order"] in named},
    }


def status(args: argparse.Namespace) -> int:
    from drive import view

    name = state.resolve(args.drive)
    view.refresh_cards(name)
    data = state.load(name)
    shown = only(data, args.ids) if args.ids else None
    with_cards(name, data, shown)
    for job_id, in_line in place.positions(data).items():
        data["jobs"][job_id]["in_line"] = in_line
    amended = agents.amendments(name, data["held"])
    for seq, mark in data["held"].items():
        mark["corrections"] = amended["correction"].get(seq, [])
        mark["withdrawn"] = amended["withdraw"].get(seq, [])
    for project, held in data["subcoordinators"].items():
        held["watch"] = watch.holder(state.pid_path(name, project))
    seen = {}
    if shown is None:
        bound = state.bound_sessions(data)
        with ThreadPoolExecutor() as pool:
            seen = dict(zip(bound, pool.map(lambda held: with_build(orca_place(held, name_show), held), bound.values()), strict=True))
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    project = state.session_project(data, session)
    runs = (lambda order: state.runs(data, order, project)) if state.session_role(data, session) == "coordinator" else None
    return emit(args, render.status_text(data, seen, shown, runs), render.status_data(shown or data, seen, shown is not None, runs))


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
    text = agents.filled((PAGES / f"{args.page}.md").read_text())
    if not args.row:
        sys.stdout.write(text)
        return 0
    rows = [line for line in text.splitlines() if line.startswith("| ") and plain_words(args.row) in plain_words(line.split("|")[1])]
    parts = ["\n".join(rows)] if rows else page_parts(text, args.row)
    if not parts:
        raise DriveError(f"nothing in drive manual {args.page} names {args.row}; drive manual {args.page} prints the page")
    print("\n\n".join(parts))
    return 0


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

    return receiver.main()


def watch_events(args: argparse.Namespace) -> int:
    """Run the watch with --loop; else load its launch agent unless one is loaded. A project's watch starts only from the session of that project's sub-coordinator, and a drive under LONG_RUNNING_STATE gets no process."""
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
    return 0


def card_command(args: argparse.Namespace) -> int:
    from drive import card

    return card.main(sys.stdin, sys.stdout)


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
    print(run_tool(args.tool, json.loads(stdin_text())))
    return 0


def run_tool(tool: str, given: dict) -> str:
    """The lines one MCP tool prints for the arguments its call sent, in whatever process runs it: its own `drive tool` or the MCP server's."""
    from drive import mcp, tools

    try:
        arguments = tools.parsed_arguments(given, mcp.tool_named(tool)["inputSchema"]["properties"])
        return tools.run(tool, state.resolve(arguments.pop("drive", None)), arguments)
    except reads.ReadError as error:
        raise DriveError(str(error)) from None


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


def break_glass(args: argparse.Namespace) -> int:
    """Run one command under the forge/break-glass grant for a stack, started in the job's or order's name with its reason, holding only the stack's env's admin credentials and the machine-break-glass role the grant opens to this Mac's machine, each minted on this Mac's machine profile to end no later than the grant, and stopped with every process left in its group by the grant's end or when drive is stopped: its output and exit status are the verb's, and nobody signs in. The moment bk build create prints the grant's link, before the grant shows in #releases, it posts root@<drive> one evidence record from lane break-glass naming the job and its order, the link, the stack, the command and the reason, so the top coordinator reads whose a grant is rather than asking every session; a post cci refuses runs nothing."""
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
        with reads.break_glass(args.stack, args.reason, f"{name} {args.id}", Path(data["roles"]["coordinator"]["cwd"]), started) as granted:
            print(f"break-glass grant {granted.link} passed; running {args.command[0]} {reads.held(granted)}", file=sys.stderr, flush=True)
            return reads.run_granted(args.command, granted)
    except reads.ReadError as error:
        raise DriveError(f"break-glass: {error}") from None


def move(args: argparse.Namespace) -> int:
    """Step 1 of moving a role's session under cci and into a fresh Orca terminal: his word naming the move, recorded on the role, and the session registered with cci, which resumes it in its tmux session once it exits; steps 2 to 4 printed, run by hand."""
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
    registered = "" if state.scratch() else f"; {agents.register(name, args.role, None, held['cwd'], held['session'])}"
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
        f"1. his word {args.his_word} recorded on the {args.role}{registered}",
        (
            f'2. cci post --lane {mover} --kind ask --to {lanes[args.role]}@{name} --text "Moving you into an Orca terminal on his word:'
            " run cc-slack unfollow for each of your Slack watches, cci unsubscribe, stop your drive watch Monitor if you run one, reply"
            ' released, and take no more work." and wait for its reply'
        ),
        f"3. {ended}",
        f"4. wait {gone}",
        f"then steps 5 to 7 of drive manual migration, Into an Orca terminal: cci session run resumes {session} in tmux session {state.alias(name, args.role)}; create the terminal that shows it",
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
    waits_opt = argparse.ArgumentParser(add_help=False)
    waits_opt.add_argument(
        "--waits",
        action="append",
        default=[],
        metavar="CARD|JOB|PR|RELEASE",
        help=(
            "a design card or PR card of his still open, by its Slack link or waits id, a PR the drive tracks as <owner>/<repo>#<n>,"
            " release:<service>:<owner>/<repo>#<n>, the next release of that service carrying that PR, refused, naming the components"
            " they reach, when go/ci's own map, tools/ci release select --pr, finds the PR's changed paths reach components and that"
            " service is none of them or is no component at all, or a job of this drive still at work, of any kind, it waits on;"
            " repeat for each, a job's wait on a job stored as that job's newest open PR. The watch drops each as he answers or taps it, the PR merges, the job returns DONE, or, once it"
            " stops or fails, each part it carries is delivered, or GitHub shows the"
            " newest release of that service holds the PR's merge commit, and says FREE once when the last is gone, which wakes the"
            " coordinator. On a PR it never withdraws his open card: the card is redrawn in place, Approve and Hold kept, to say"
            " what it waits on, and a release GitHub shows already out is not stored"
        ),
    )
    waits_opt.add_argument("--no-waits", action="store_true", help="drop every wait it carries before adding any --waits")
    builds_opt = argparse.ArgumentParser(add_help=False)
    builds_opt.add_argument(
        "--builds-nothing",
        action=argparse.BooleanOptionalAction,
        help=(
            "mark or unmark a builder whose whole work holds no checkout or compiler, such as a merged PR's live proof or a read: it holds"
            " no slot of this Mac's builder cap, drops any turn it waits for one, and starts or takes a message here at once"
        ),
    )

    root = argparse.ArgumentParser(prog="drive", description="State and events of a long-running drive.")
    root.add_argument("--version", action="version", version=f"drive {state.VERSION}")
    root.add_argument("--root", action=Root, nargs=0, help="print the folder of the long-running install this drive runs, the newest installed, whose hooks/ and lib/ a proof file imports")
    verbs = root.add_subparsers(dest="verb", required=True)

    verb = verbs.add_parser("start", parents=[json_opt], help="create the drive or bind one of its two roles to this session")
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
    verb.add_argument("words", help='his words, verbatim, or a "Drive tooling:" order\'s own, which `cc-slack check-post` must pass, since its PR card quotes them')
    verb.add_argument("--from", dest="source", default="terminal", help=FROM_HELP)
    verb.add_argument(
        "--thread",
        help=(
            f"{THREAD_HELP}; default: the thread the --from message is in, his DM's root when a teammate started it, since that thread"
            " is its reporter's and hears only its one note. An order in the thread of his open PR card names"
            " that card and leaves it up, since its stack can meet his words already: only --rework withdraws one"
        ),
    )
    verb.add_argument("--rework", action="append", default=[], metavar="PR", help=REWORK_HELP)
    verb.add_argument("--due", help="the time his ack promised a line by; the watch says DUE from then until a line is in the thread")
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
            ' a "Drive tooling:" order Jev reads as repeating an open tooling order joins it unasked'
        ),
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
    reported = verb.add_mutually_exclusive_group()
    reported.add_argument("--reporter", metavar="LINK", help=REPORTER_HELP)
    reported.add_argument("--no-reporter", metavar="LINK", help="his Slack message saying not to tell the reporters; the order then closes done with no note to them")
    verb.add_argument("--watch", metavar="LINK", help=f"{WATCH_HELP}; it replaces any thread the order watched, reading only messages newer than now")
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
        help="with --kind answer, a part that asks only for an answer, which the post it already holds then delivers, refused while a builder, codex or design job or a drive PR is on it, but for a builder no agent started that waits only on readers, which it takes the part off, stopped once none is left; with --kind build, work again, making an answer order a build whose other parts stay answers",
    )
    verb.add_argument("--complex", action=argparse.BooleanOptionalAction, help="mark or unmark it as more than a one-fact lookup")
    verb.add_argument(
        "--options-only", action=argparse.BooleanOptionalAction, help="limit it to options, so no builder or codex job starts on it, or lift that once he picks, adding the builder of each change a design on it listed"
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
            " from the session the order's ORPHAN lines reach, with or without --record, or the communicator holding it with none, the part the drive's post at the link already answered,"
            " or the work part the merged PR at the link delivered, posting nothing"
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
            "the communicator's handler posted the one note each of the order's reporters gets in the thread of their message, or in the"
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
    verb.set_defaults(run=incident_open, incident=True, joins=None, kind=None, complex=False, options_only=False, once=False, owner=None, watch=None)
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

    job = verbs.add_parser("job", help="background agents").add_subparsers(dest="action", required=True)
    verb = job.add_parser(
        "add",
        parents=both,
        help=(
            "start a job on an order's parts: prints its id, then each job still running or waiting on one of those parts;"
            " a builder or codex job beside a running one on a part is refused, but on an incident, with --lands or waiting on it"
        ),
    )
    verb.add_argument(
        "--builds-nothing",
        action="store_true",
        default=None,
        help="a builder whose whole work holds no checkout or compiler, such as a merged PR's live proof or a read: it holds no slot of this Mac's builder cap and starts here at once",
    )
    verb.add_argument("kind", type=job_kind, choices=state.JOB_KINDS, help="what the job is; designer, its agent type's name, is design")
    verb.add_argument("--order", required=True, help="the order whose parts it delivers")
    verb.add_argument("--goal", required=True, help="its deliverable, one line")
    verb.add_argument("--agent", help="the agent id the Agent tool returned")
    verb.add_argument("--repo", help="owner/name")
    verb.add_argument("--branch", help="the branch it works on")
    verb.add_argument("--worktree", help="its worktree's path")
    verb.add_argument("--remote", action="store_true", help="a builder that starts as a remote lane on a Sprite")
    verb.add_argument(
        "--model",
        choices=codex.MODELS,
        help="the model a codex job's codex-ask run takes as -m, default codex-ask's own, sol; astra only on an order whose words name astra",
    )
    verb.add_argument("--part", action="append", default=[], help="a part of the order this job delivers; default: its only part")
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
            "the one `tools/ci release start` or `hotfix` to start now, its --order naming his answer approving it: refused unless the top"
            " coordinator adds the job, whatever order it serves, and Jev reads that answer as his go, given after any hold of his on every"
            " release, or it is an incident's order; it starts"
            " from the top's checkout, then the job's brief names the command and its build, and its agent follows it and reads its approve hold"
        ),
    )
    verb.add_argument(
        "--waits",
        action="append",
        default=[],
        metavar="CARD|PR|RELEASE|JOB",
        help=(
            "a card of his still open, a PR the drive tracks as <owner>/<repo>#<n> or release:<service>:<owner>/<repo>#<n>, or a job"
            " of this drive still at work, of any kind, a reader that just started included, as job set takes them, that this job waits on: it is recorded"
            " waiting with no agent, so its part is never in no job, and the watch says FREE once the last wait ends, when its agent starts"
        ),
    )
    verb.set_defaults(run=job_add)
    verb = job.add_parser("set", parents=[*both, waits_opt, builds_opt])
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
    verb.add_argument("--pr", action="append", default=[], help="a PR it opened or took over; a PR the drive tracks then names it on its row, as drive pr add --job moves one")
    named = verb.add_mutually_exclusive_group()
    named.add_argument("--part", action="append", default=[], help="another part of the order this job delivers, or of the one order --carries names")
    named.add_argument("--parts", nargs="+", type=str.strip, metavar="PART", help="the parts of its own order this job delivers, replacing its list")
    verb.add_argument(
        "--carries",
        action="append",
        default=[],
        metavar="ORDER",
        help="another order whose part this job delivers, until it is done, refused while another running builder or codex job carries that part; it names each open card of his carrying the job's PRs and leaves it up, since its stack can meet his words already: only --rework withdraws one",
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
        help="keep a job's ASK in the drive beside its other open questions, refused unless it is in the shape he reads, and set the job waiting, a builder kept running to build the recommended option; --drop throws away one kept with no card",
        description=(
            "Check a job's ASK block: `ASK <the question>`, one to three summary lines (the first naming the work it serves,"
            " `{order:<o>}`, `port of #N` or Drive tooling, and what your pick changes, as `For {order:<o>}: your pick decides"
            " <what it changes for you>.`, then the recommendation and what happens meanwhile),"
            " `Show me:` working each option of a design out to what it will look like once built, its real calls, rows,"
            " flow or what a caller gets, as a compare panel named for it or a picture captioned with its number, which Jev"
            " reads option by option, and on any other card a Show me only"
            " when it shows what the words cannot (a mermaid flow, a compare or a table for options, a chart for numbers,"
            " an arch diagram, whose JSON parses, the real call in a fence naming its language for a code or API shape, or a"
            " picture on a line of its own as ![caption](/absolute/path.png); never words, JSON, YAML, logs, a terminal"
            " session, an HTTP exchange, command output or a stack trace in a fence), captions of at most 25 words,"
            " two to four `- ` options of at most 25 words, each saying what he gets and what it costs, with exactly one ending"
            " (recommended), each followed by one plain-text line indented two spaces, at most 60 words, that his card draws"
            " under it, saying in prose what the option is: what runs and when, where its data lives, and what it requires"
            " touching or building, by name, with any cost every user pays, never fields such as `Does:`; no mechanism such as"
            " polling, a push or an event named by a bare word on the question or an option, the question, summary and"
            " options at most 150 words in all, in plain words, each thing he"
            " could open linked where it is named, then `Findings:` with the evidence, its `Simplest:` line naming the option"
            " that is the simplest design doing his words, its `Root cause:` line naming the"
            " structure that lets the defect happen and its `First principles:` line the recommended option that removes"
            " it, or `Root cause: fixed: <that structure>, removed by <the merged PR's link>`, `not found: <the job"
            " finding it>` or `none: <why>`. Keep it in the drive in a file of"
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
            "only check the ASK block in this file, or - for stdin, by every check drive job ask <j> runs but Jev's read of it against"
            " his words, its words counted as his card draws them, each {order:<o>} as the opening of his order's words, and print"
            " every fix it needs at once, or ready; keeps nothing"
        ),
    )
    verb.add_argument(
        "--unsettled",
        metavar="WHY",
        help="one string naming each ruling of his a refusal or Jev's finding quoted, such as No sweepers, and why this option keeps what it names; it marks the card as asking him to lift that ruling, which holds its job's PR on an incident too",
    )
    verb.add_argument(
        "--redraws",
        metavar="WAITS",
        help=(
            "the waits id of the card this block asks again in new words, which it redraws in place: an open or withdrawn card of"
            " the job, or of another job on one of its orders whose work it carries on, whose question stays on that job folded into"
            " this one, so that job asking it again draws no card; refused for a card his answer ended and for any card of another order"
        ),
    )
    verb.add_argument(
        "--drop",
        metavar="FILE",
        help=(
            "in place of the file, throw away the job's question kept in this file with no card drawn for it, such as one refused"
            " for an answer of his that settles it: it leaves the job's open questions and the file is deleted; refused for a"
            " question on a card, which only his answer closes"
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
        parents=both,
        help="keep a job's whole return in the drive, or print the returns it took",
        description=(
            "A job returns one line; the rest of its return, the inputs to what he would be told, one fact a line,"
            " is kept here before it returns, piped in on stdin. With -, keep stdin in the job's history, which is its report,"
            " set its state and said line from its first line, and print where; with nothing, print the returns the job took,"
            " its own agent's, its lane's and anyone else's, newest first, the newest whole and each earlier one as its first line."
        ),
    )
    verb.add_argument("id")
    verb.add_argument("stdin", nargs="?", choices=["-"], help="- to keep the job's whole return from stdin; default: print the returns it took, newest first")
    verb.add_argument("--all", action="store_true", help="print every return the job took whole")
    verb.set_defaults(run=job_report)
    verb = job.add_parser(
        "brief",
        parents=both,
        help="write a job's brief from his order's words, each ruling's words and the earlier jobs' transcripts, and print where",
        description=(
            "Write the brief reference/jobs.md shapes into the drive and print its path: Order from the words the order"
            " holds, verbatim; Goal from the job; Decisions that bind from each --ruling's recorded answer, each one the brief of any job on its order binds, and each one that"
            " settled a question of the reader its Researched: line names, its title and his words above the drive's Notes:; Transcripts from each"
            " earlier job on the order; Scratch, by default a folder of the drive's;"
            " the Drive line; a reader's on a build order, Research: the Researched: block this build needs; and each line"
            " the flags name. A flag left out keeps the job's earlier brief's, each --ruling adds to the rulings every brief on its order binds, and the output names each kept;"
            " a brief naming no --serves serves drive tooling on a tooling order and his order on any other. An adopted PR's job, waiting with no agent, is briefed only"
            " with --handoff for an event that needs action. Its first line, `Read your brief first: <path>`, is a local"
            " job's whole Agent prompt, which binds the agent to the job, and a remote job launches from it with drive job launch <j>."
        ),
    )
    verb.add_argument("id", help="the job")
    verb.add_argument("--serves", help='"port of #N", "drive tooling" or "his order"; default drive tooling on a tooling order, else his order')
    verb.add_argument("--done-when", help="the check that proves it done")
    verb.add_argument("--where", help="the repo, base branch, and any branch or PR to adopt")
    verb.add_argument(
        "--native",
        help='where he sees or uses the result and what that platform already does for it, with links; "none" for work he never sees',
    )
    verb.add_argument(
        "--interactions",
        help=f"his user stories on the result, one per `;`, each what he does and what he must then see, which its job writes strict acceptance criteria for before building and proves each on the installed surface with {brief.SEEN}; needed when --native names a surface",
    )
    verb.add_argument("--would-he-ask", help="what he would still have to ask for if only his words were built")
    verb.add_argument("--runs-as", help="for a pass, a hand repair or any other write of a team's data: the service account in its cluster and how it starts; \"none: <why>\" for a job that writes none")
    verb.add_argument(
        "--mac",
        help="the step only this Mac runs that the job itself runs, such as drive break-glass, ci mac-box, ci devbox, ci db-read, a Datadog read or a drive install no lander runs; drive job launch refuses its Sprite start and the agent_brief hook starts it here at any load, and no other words of the brief place it",
    )
    verb.add_argument("--handoff", help="a PR event or freed built branch: the PRs or branch, its head, the event line, INSTALL: and PROVE:")
    verb.add_argument("--ruling", action="append", default=[], metavar="ANSWER", help="a cc-notes answer id of his that touches this, added to the rulings every brief on its order binds; its words go in whole")
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
            " and the job is recorded as this session's lane. A codex job starts no worker: its codex-ask run starts here with"
            " --dispatch on its brief and --schema holding its final message to a state, line and report, which the watch takes as its"
            " return once the run ends."
        ),
    )
    verb.add_argument("id", help="the job")
    verb.add_argument(
        "brief", nargs="?", help="the file holding the job's brief, or - to read it from stdin; default the one drive job brief wrote"
    )
    verb.add_argument(
        "--local",
        action="store_true",
        help="start it as an Orca worker on this Mac, in a fresh worktree of this checkout's Orca repository, read like a Sprite's lane; for a job on a Sprite whose lane ended or whose Sprite Orca cannot reach, its next worker starts here with no wake of that Sprite, a resume holding the job's lock standing down for it, after a save of what that Sprite holds on no remote; when the save fails the Sprite is kept, and the handoff names what each lane of the job saved on GitHub",
    )
    verb.set_defaults(run=job_launch)
    verb = job.add_parser(
        "started", parents=both, help="record a worker an older install's launch or resume started, from the receipt it kept; that launch runs it"
    )
    verb.add_argument("id", help="the job")
    verb.add_argument("receipt", help="the receipt file the launch kept under the drive's jobs folder")
    verb.set_defaults(run=job_started)
    verb = job.add_parser(
        "resume",
        parents=both,
        help="answer a job's question, or send it the next event, starting its next worker when its last ended",
        description=(
            "Answer a remote lane's question, or send it the next event, once Orca shows its worker alive, by the one relaunch drive"
            " job launch shares, under the job's own lock, so a resume arriving while another starts the job's next worker waits"
            " for it, about 150 s, and sends that worker the text; one that finds the job stopped once it holds that lock exits 2,"
            " sending, holding and starting nothing, and drive job launch runs a stopped job again. One that finds another verb holding that lock while Orca cannot reach the lane's Sprite exits 2 at once, sending and holding nothing, rather than wait up to 1800 s behind a wake of that Sprite, and names drive job launch --local; one holding the lock while a drive job launch --local waits for it stands down, exiting 2 before it wakes, sends or starts anything, the texts the job holds left for that launch's handoff. An agent on this Mac whose last response ended on an API error or"
            " a refusal hands the job to a lane on a Sprite, or on this Mac when its brief declares a step only this Mac runs, with a handoff carrying the job's brief and this text. When its "
            "worker finished and Orca still holds its terminal, on its Sprite or on this Mac, start the job's next worker "
            "in that terminal, or, when Orca no longer finds that terminal or worktree, as a dead worker's; when its worker is dead, relaunch the job on a fresh Sprite, or in a fresh worktree on this "
            "Mac, on the same Run. Before a relaunch on a fresh Sprite it saves what the dead lane's Sprite holds that GitHub does not: over ssh "
            "it commits each worktree's uncommitted changes there and pushes each commit, branch or stash on no remote as saved/<the dead lane>/<its "
            "branch>, on the token gh auth token prints, lent to that one push through the ssh session's stdin, so it never reaches the Sprite's disk; "
            "its output and the new worker's handoff name each branch GitHub then holds under saved/<the dead lane>/, by one read of its refs. When that Sprite does not answer or the save fails, it relaunches the job on that "
            "Sprite instead, in the checkout the dead lane left. When Orca cannot reach the lane's Sprite, it wakes that Sprite with "
            "orca-reclaim.py --wake and reads the lane again: a worker that takes messages gets the text, and any other lane relaunches on "
            "that Sprite in the checkout it left with no save, or on a fresh Sprite when that Sprite starts no worker. Either new worker "
            "gets a handoff carrying the job's brief, its branch, its last line, "
            "its report, its PRs and this text. A remote lane's text is held on the job under one Orca request id before any "
            "Orca call: when Orca cannot tell whether the lane lives, or refuses the send, it stays held and the resume exits 0 saying so, "
            "the watch sends it once Orca reads that worker alive, under the same id so Orca never applies it twice, and a "
            "relaunch meanwhile carries every held text in its handoff; a relaunch Orca refuses exits 2 with Orca's refusal, "
            "the text still held. With no text, it sends what the job holds now. A job held by an Agent another session started, which this session cannot "
            "SendMessage, gets a handoff brief instead, carrying the same, and prints its `Read your brief first:` line: an agent "
            "this session starts on it takes the job as this session's."
        ),
    )
    verb.add_argument("id")
    verb.add_argument(
        "text",
        nargs="?",
        type=lambda value: stdin_text() if value == "-" else value,
        help="his answer, or the event the lane acts on next, or - to read it from stdin; left out, the texts the job holds",
    )
    verb.set_defaults(run=job_resume)
    verb = job.add_parser(
        "read",
        parents=both,
        help="what the watch starts on a remote lane's READ, LOG or DRIVE question: answer it here, keeping first the answer its own end leaves so it never runs twice, then the one the watch sends",
    )
    verb.add_argument("id")
    verb.add_argument("message", help="the question's Orca message id")
    verb.set_defaults(run=job_read)
    verb = job.add_parser("answered", parents=both, help="keep the answer an older install's job read came to, from the receipt it kept; that read runs it")
    verb.add_argument("id", help="the job")
    verb.add_argument("receipt", help="the receipt file the read kept under the drive's jobs folder")
    verb.set_defaults(run=job_answered)
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
    verb.add_argument(
        "--pr",
        action="append",
        default=[],
        help="instead, his open cards holding this PR from landing, a PR url or owner/name#N, repeatable; the landing_guard hook on a Sprite lane asks it before the lane lands a Forge-AI PR, as it holds one here",
    )
    verb.add_argument(
        "--exempt",
        help='the exempt class the PR goes up under, as the drive pr add in the same call registers it: none of his cards holds a "flake fix"; the bash_guard hook on a Sprite lane passes it',
    )
    verb.add_argument(
        "--release",
        help="instead, why his hold on every release refuses this release, a `ci release start` or `hotfix` as the job runs it, or a release verb naming none; the landing_guard hook on a Sprite lane asks it before the lane starts, resumes, approves, reruns or rolls back a release",
    )
    verb.set_defaults(run=job_held)
    verb = job.add_parser("wake", parents=both, help="send the job's lane a message while it takes messages, starting no worker: the watchdog's agent's one way to message the job answering for a stall")
    verb.add_argument("id", help="the job")
    verb.add_argument("--text", required=True, help="the message")
    verb.set_defaults(run=job_wake)
    verb = job.add_parser(
        "end",
        parents=both,
        help=(
            "free a remote job's lane once it returned its line: its Sprite, destroyed once what it holds on no remote is saved to GitHub as saved/<its lane>/<branch>, or its terminal and clean worktree on this Mac. The job"
            " stays in the state its line set, with its waits, PRs and carried parts. A job not yet done first has what its Sprite holds"
            " on no remote, uncommitted changes included, saved to GitHub as saved/<its lane>/<branch>; its output and its next lane's handoff"
            " name each branch GitHub then holds under saved/<its lane>/; refused, naming why, when that Sprite cannot be read or the save fails, and for a running job, whose lane returned no line: drive job resume sends it its next event"
        ),
    )
    verb.add_argument("id")
    verb.set_defaults(run=job_end)
    verb = job.add_parser("ended", parents=both, help="keep what an older install's job end came to, from the receipt it kept; that end runs it")
    verb.add_argument("id", help="the job")
    verb.add_argument("receipt", help="the receipt file the end kept under the drive's jobs folder")
    verb.set_defaults(run=job_ended)
    verb = job.add_parser("sent", parents=both, help="keep a text an older install's job resume saw its live worker take, from the receipt it kept; that resume runs it")
    verb.add_argument("id", help="the job")
    verb.add_argument("receipt", help="the receipt file the resume kept under the drive's jobs folder")
    verb.set_defaults(run=job_sent)
    verb = job.add_parser(
        "prove",
        parents=both,
        help=(
            "what the watch starts once every PR of a builder's taken READY or PUSHED merged, a yasyf/* one among them, and it keeps an"
            " executable proof file: install each plugin a yasyf/* merge changes whose installs, binaries, Homebrew copy or Captain Hook.app lack it, its other merges taken at once,"
            " wait until the drive's dashboard runs the newest long-running installed, run the proof file with those plugins' bin folders first on PATH with no wait for CI,"
            " and when it fails while a workflow a yasyf/* merge ran on its default branch has not completed its first run from the merge on that was not cancelled,"
            " the run drive pr ci judges the merge by, follow that run to its end with no deadline and run the file once more, keep its output"
            " beside the job's report, take DONE on exit 0 and BLOCKED quoting its last FAIL line otherwise or when an install fails, no release will carry a merge,"
            " or the dashboard still runs an older install at the timeout, and keep the close report of an order the DONE finishes for the"
            " watch to post, but a nonzero exit ending on a WAIT line with no FAIL line takes no result and runs again at his next word;"
            " refused, naming who proves it instead, for a merge outside yasyf/* or a job keeping no proof file; on a builder that took BLOCKED,"
            " its own proof's among them, it first takes back the newest READY or PUSHED the job's history keeps, so a failed proof runs again on its own job"
        ),
    )
    verb.add_argument("id")
    verb.add_argument(
        "--timeout",
        type=int,
        default=900,
        help=f"seconds the installs get, the dashboard's handover gets, and each run of the proof file gets from its own start, its end the Unix second {install.PROOF_DEADLINE_ENV} names to the file",
    )
    verb.set_defaults(run=job_prove)
    verb = job.add_parser("proved", parents=both, help="keep what an older install's job prove came to, from the receipt it kept; that proof runs it")
    verb.add_argument("id", help="the job")
    verb.add_argument("receipt", help="the receipt file the proof kept under the drive's jobs folder")
    verb.set_defaults(run=job_proved)
    verb = job.add_parser("show", parents=both, help="drive status for these ids")
    verb.add_argument("ids", nargs="+", metavar="ID", help=f"what to read: {ID_FORMS}")
    verb.set_defaults(run=status)

    pr = verbs.add_parser("pr", help="the drive's pull requests").add_subparsers(dest="action", required=True)
    verb = pr.add_parser("add", parents=both)
    verb.add_argument("url", help="the PR's URL")
    verb.add_argument("--job", required=True, help="the job that opened or took it over")
    verb.add_argument("--why", help="with --no-tap, why it lands with no tap of his")
    verb.add_argument("--headline", help="the line his card opens with, at most 90 characters: what the change gets him, in plain words")
    verb.add_argument("--because", help="why the change is made, one plain line: the problem it fixes or the words of his it carries out")
    verb.add_argument("--does", help="what it changes for him or its users once it lands, one plain line")
    verb.add_argument(
        "--downstream", help="what else it changes, breaks or holds once it lands, one plain line; a PR waiting on his tap is refused until all four lead lines are kept, naming no function or file"
    )
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
        help=f"{served.NAMES} that settled each design decision the PR makes, or none; --serves needs it, the watch's check reads each answer whole as his words, and a citation naming none of these, a card he has not answered or a message he did not write is refused",
    )
    verb.add_argument(
        "--part",
        action="append",
        default=[],
        help="a part of its job's orders this PR delivers, replacing the parts it named, or on a merged PR whose job returned DONE any part of its order, which it then delivers; the watch judges the PR against the parts it names, every part its job delivers when it names none",
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
    verb.add_argument(
        "--flaky",
        nargs=2,
        metavar=("TEST", "FIX"),
        help=(
            "the flaky test outside its diff Trunk ejected it on at its head, and that test's fix, a PR the drive tracks: it waits on that fix,"
            " and once the fix merges the lander queues the same commit again with stack-enqueue --flake, once; refused unless Trunk ejected it at its head"
        ),
    )
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
    verb.add_argument("--asked", action="append", nargs=2, default=[], metavar=("PR", "HEAD"), help="with --outcome, a PR and the head its lander asked forge-pr-reviewer to review; repeat for each")
    verb.set_defaults(run=pr_land)
    verb = pr.add_parser(
        "check",
        parents=both,
        help="what the watch starts on a builder's READY: judge one PR his card waits on at its head against his words, and say CHECKED, which draws his card once it is green and approved, or NOT READY, which goes to its builder unless he approved the PR",
    )
    verb.add_argument("url", help="the PR's URL")
    verb.set_defaults(run=pr_check)
    verb = pr.add_parser(
        "checked", parents=both, help="keep the outcome an older install's pr check reached, from the receipt it kept; that check runs it"
    )
    verb.add_argument("url", help="the PR's URL")
    verb.add_argument("receipt", help="the receipt file the check kept under the drive's prs folder")
    verb.set_defaults(run=pr_checked)
    verb = pr.add_parser(
        "ci",
        parents=both,
        help=(
            "judge a merged PR's CI as drive job prove follows it: the first run not cancelled of each workflow that ran on its merge commit,"
            " from that commit's run on by run id, never a run of a push merged before it; exits 0 once each completed and passed, 1 while one runs,"
            " failed or has no run, naming each run and then its verdict, which names each run that did not pass, so a proof file of a fix to red CI reads its verdict here"
            " rather than picking a run itself"
        ),
    )
    verb.add_argument("url", help="the PR's URL")
    verb.set_defaults(run=pr_ci)
    verb = pr.add_parser(
        "wake",
        parents=both,
        help="what the watch starts on a PR whose stage its builder owes: send the builder answering for it its stage line, to its live lane without touching its job's ask or state, else to a fresh worker on its job",
    )
    verb.add_argument("url", help="the PR's URL")
    verb.add_argument("--text", help="the line to send, the PR's stage line when left out")
    verb.set_defaults(run=pr_wake)

    verbs.add_parser("place", parents=both, help="where the next monorepo builder starts").set_defaults(run=place_job)
    verb = verbs.add_parser("bound", parents=both, help="run one command with a lane's Run bound to this terminal, under the drive's binding lock, a worker-start only until Orca takes its request, then bind the drive's own Run, which no lane mails")
    verb.add_argument("lane_run", metavar="run")
    verb.add_argument("command", nargs=argparse.REMAINDER, help="after --, the command and its arguments")
    verb.set_defaults(run=bound)
    verb = verbs.add_parser(
        "answer",
        parents=both,
        help="record an answer or ruling of his against the cards still asking him, parts undelivered and PRs not yet live that it could change, each marked standing, settled, dropped, superseded or reopened by it",
        description=(
            "Run for every answer and ruling of his. The first call, with --title, --words and --link and no id, records"
            " it in cc-notes and here, or with an id, --words and --link records one already in cc-notes; a ruling, labelled"
            " ruling, records only with --rewrite naming the communicator's open \"Drive tooling:\" order that rewrites the"
            " skill to it, so the watch chases that rewrite until it is done. The record tool keeps each ruling an owner or"
            " answer record of his new words names here in the same call, so a later call with its id alone lists it. It"
            " lists the cards of his still open, each undelivered part, as <order>:<part>, of an open order holding more"
            " than one, and the PRs not yet live that it could change: those of the job and order of the card it answers,"
            " those its words name, and every other open question card Jev, reading his answer against all of them in one"
            " call, reads it as deciding, ruling options out of or making moot (key, kind, job, order, title, link), and"
            " each job waiting on one of their orders, and exits 2. Each later call marks them: --stands for one this answer"
            " leaves as it is, --dropped for a card, part or open PR his words remove, which withdraws a card without stopping"
            " its job, takes a part off its order, every job delivering it and every PR naming it, stopping only a job left"
            " with no part, and closes an open PR on GitHub with its tap ask and card,"
            " --settled for a question card it decides, which shows his answer on the card in place with no buttons, or for"
            " one it decides in part with the ASK its job keeps asking only what his answer leaves open, which takes that"
            " card's place and redraws it; --superseded for one it replaces, which settles its card in place showing his words and a"
            " link to them with no buttons, drops its PR's tap ask, and stops its job, closing an open PR on GitHub, or"
            " folds what the job carries and each open PR it answers for into the live job named, and --reopened for a PR his words change, which drops his"
            " tap on it, withdraws each open card of his carrying it in the same call, and names the live job that reworks"
            " it, no card coming back until that job's READY says how his words are met; --sent for a waiting agent"
            " SendMessage gave his words verbatim, while the drive job resume that delivers them verbatim to a remote"
            " lane's worker marks that lane itself, and --untouched for one they leave as it is. --others-stand marks the"
            " cards, parts and PRs left standing only while a few are left. It exits 2, listing the rest, until every one is"
            " marked; the watch says ANSWER every 10 minutes while a card, part or PR is not."
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
        help="the permalink of his message or of the card he answered, which a superseded card links, or terminal for words he typed into a drive session's terminal; the first call needs it, and checks both --words and a named answer's words above its Notes: against it",
    )
    verb.add_argument("--stands", action="extend", nargs="+", default=[], metavar="KEY", help="a card, order, part or PR his answer leaves as it is")
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
    verb.add_argument(
        "--sent",
        action="extend",
        nargs="+",
        default=[],
        metavar="JOB",
        help=(
            "a job waiting on an order his answer touches, once SendMessage gave its agent his words verbatim; a remote lane is marked sent"
            " only by the drive job resume that delivers them verbatim to its worker, so naming one confirms that mark and is refused without it"
        ),
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
            " words; its line comes back as one cci record when it ends; prints started <its id>, or handed to <id> for a"
            " brief, his words or a record, on the message of his, in the thread or on the re chain a live handler is at work on"
        ),
    )
    verb.add_argument(
        "brief",
        help=f"the handler's brief: its lines, each opening with one of {', '.join(label + ':' for label in render.HANDLER_LABELS)}",
    )
    verb.add_argument("--away", action="store_true", help="he said he is going to sleep or away, for a brief carrying his words")
    verb.add_argument(
        "--answer",
        metavar="TITLE",
        help=(
            "his read-only question's title, a few words naming it: the call adds his answer order from the brief's words and Link,"
            " filed under no project or workstream, and the handler's reader job on it, and writes both into the brief with the"
            " pairs in use and every open order in his thread, its jobs, questions and PRs, so the handler reads no drive status,"
            " files the order with drive order set <o> --project --workstream and closes it with say(close=true)"
        ),
    )
    verb.set_defaults(run=handler)
    verb = verbs.add_parser(
        "handler-start", parents=both, help="start the handler drive handler kept, as its own claude --bg session; the handler verb runs it detached once it returns"
    )
    verb.add_argument("id", help="the handler's id, as drive handler printed it")
    verb.set_defaults(run=handler_start)
    verb = verbs.add_parser("say-record", parents=both, help="record a post say made on its order from the receipt say kept; say runs it detached")
    verb.add_argument("receipt", help="the receipt file say kept under the drive's posts folder")
    verb.set_defaults(run=say_record)
    verb = verbs.add_parser(
        "term", parents=both, help="he corrected a word: say's hook finds it from now on for Jev to judge, naming the proper term his words or the code use"
    )
    verb.add_argument("stand_in", metavar="stand-in", help="the word he corrected, as a post used it")
    verb.add_argument("term", help="the proper term to use in its place, his word or the code's")
    verb.set_defaults(run=term)

    verb = verbs.add_parser("status", parents=both, help="where each order, job and PR stands")
    verb.add_argument("ids", nargs="*", help=f"what to read, each {ID_FORMS}; default: every open order a line each, a coordinator's only its own")
    verb.set_defaults(run=status)
    verb = verbs.add_parser("manual", help="print one page of the long-running skill")
    verb.add_argument("page", nargs="?", choices=manual_pages(), help="default: list the pages")
    verb.add_argument("row", nargs="?", help="print only the table rows whose first cell names this, such as TAP or READY, else each section, list item, row, fenced block and paragraph carrying it")
    verb.set_defaults(run=manual)
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
    verb.add_argument(
        "--project",
        metavar="NAME",
        help=(
            "the watch of that project's sub-coordinator, started only from its session: the lines of the orders filed under"
            " it, their jobs and PRs, posted to its lane; the drive's own watch leaves those lines out"
        ),
    )
    verb.set_defaults(run=watch_events)
    verbs.add_parser(
        "receive", help="take GitHub's signed webhook deliveries through this Mac's Tailscale Funnel path into each drive's events file: the process the receiver's launch agent runs"
    ).set_defaults(run=receive)
    cards = verbs.add_parser("cards", help="his open question cards").add_subparsers(dest="action", required=True)
    verb = cards.add_parser(
        "redraw",
        parents=both,
        help="print the card drive job card prints for each of his open question cards, one JSON line each",
        description=(
            "Redraw his open question cards in one pass: for each open card, print the JSON `drive job card <its ASK file>"
            " --waits <its waits id>` prints, with `replaces` the card's permalink, which cc-slack draws over the card where it"
            " sits, posting no new version, whether or not its store still keeps the card, one line each, for say(card=…)."
            " His open cards are each open question of the drive drawn on a card, as drive status reads it open, and each card"
            " cc-slack lists open. A question's ASK file is the one it keeps; a listed card's is the one a job's question keeps"
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
        help="withdraw his open question card that his words withdraw or change, or that an event overtook",
        description=(
            "Withdraw his open question card that his words withdraw, wherever he wrote them, or whose question or options they"
            " change, such as more options, a new constraint or a rework of its work, or that an event overtook so a tap has"
            f" nothing left to pick, such as a hold on a deploy that applied: {settled.EVENTS}. It settles in place showing his"
            " words, or what happened, with no buttons, so a late tap records nothing, and what happened is never recorded as"
            " his; once the work is back, its job, or any job of its order carrying on that work, asks again with drive job ask"
            " <job> <file> --redraws <this card's waits id>, which redraws it under its waits id."
        ),
    )
    verb.add_argument("card", help="the card, by its waits id or its permalink")
    verb.add_argument(
        "--words",
        required=True,
        help="his words that withdraw or change it, verbatim, refused unless his message at --via holds them; or, for an event, what happened, in plain words; the card shows them",
    )
    verb.add_argument(
        "--via",
        required=True,
        help="the permalink of his message, which the card links, terminal for words he typed into a drive session's terminal, or the link of the event that overtook it",
    )
    verb.set_defaults(run=cards_withdraw)
    card_help = "cc-slack's card_command: reads one request JSON on stdin and prints one JSON reply"
    verbs.add_parser("card", help=card_help, description=card_help).set_defaults(run=card_command)
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
    verb = verbs.add_parser(
        "break-glass",
        parents=[drive_opt],
        help="run one command holding only a stack's admin credentials, minted on this Mac's machine profile to end with its break-glass grant",
        description=(
            "Start the forge/break-glass grant with bk build create at the commit Pulumi last applied identity at on"
            " core-gbl-identity, as that one stack's newest successful update in the Pulumi state bucket records it, read on this Mac's"
            " release-operator profile, its reason the job or order and --reason, which may not be"
            " blank, and wait for it to pass. Then mint the env's account admin role with aws sts assume-role from the source_profile"
            " of the profile in ~/.aws/config that reaches it from a credential_process, never an SSO sign-in, to end a minute"
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
    verb = verbs.add_parser("reload", help="type /reload-plugins into each session of every drive on this Mac, queued behind a turn still running after 30 seconds")
    verb.set_defaults(run=reload_plugins)

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
    """Run one verb in the newest installed drive when this process started from an older install, such as a launch's bind prefix. A Sprite lane's verb, which reads.lane_drive marks as a SPRITE_LANE run as its job's own lane, runs only when reads.lane_refusal finds nothing in what it parsed writing to another job or acting on another drive, a bare `status` reading the lane's own job, and nothing it starts carries that mark. A verb a newer install's save refused before this process changed anything, a drive row, a post or any command but a read, by state.WROTE, and a launch or resume refused whatever it changed, by relaunching, runs again on the newest install with the stdin it read, its lane's mark and the mark of the process state.detach started; one run inside another verb, as an MCP call's ops are, raises for that call to run again whole while it changed nothing. A refusal that remains exits 2 by refused, which a launch or resume state.detach started also writes on the drive's events for the watch."""
    if argv is None:
        install.reopen(sys.argv[1:])
    args = parsed(argv)
    detached = os.environ.pop(state.DETACHED_ENV, None)
    if lane := os.environ.pop(reads.SPRITE_LANE, None):
        own = os.environ[state.OWN_JOB_ENV]
        if refusal := reads.lane_refusal(args, parsed, os.environ[state.DRIVE_ENV], own):
            print(refusal, file=sys.stderr)
            return 2
        if args.verb == "status" and not args.ids:
            args.ids = [own]
    if "TMUX" in os.environ and (attached := orca.own_terminal()):
        os.environ["ORCA_TERMINAL_HANDLE"] = attached
    try:
        for key in WAITS_ARGS:
            if value := getattr(args, key, None):
                setattr(args, key, folded_waits(getattr(args, "drive", None), value))
        return args.run(args)
    except state.NewerDrive as error:
        if not state.WROTE and argv is not None and install.upgraded():
            raise
        if argv is None and (not state.WROTE or relaunching(args)):
            os.environ.update({mark: value for mark, value in ((reads.SPRITE_LANE, lane), (state.DETACHED_ENV, detached)) if value})
            install.reopen(sys.argv[1:], "".join(STDIN) if STDIN else None)
        return refused(args, error, detached)
    except VERB_ERRORS as error:
        return refused(args, error, detached)


def folded_waits(name: str | None, value: str | tuple | list) -> str | tuple | list:
    """A verb's card argument with each waits id in it as the drive stores it, by state.waits_of, so a card named `<job>-<n>` is the card `<drive>:<job>-<n>` names at every verb: a mark's KEY=FILE, KEY=stop or KEY=JOB folds its KEY alone."""
    if isinstance(value, str):
        return state.waits_of(name, value)
    if isinstance(value, tuple):
        return (state.waits_of(name, value[0]), *value[1:])
    return [folded_waits(name, each) for each in value]


def relaunching(args: argparse.Namespace) -> bool:
    """Whether the verb is a launch or resume, which runs again whole on the newest install after a newer install's save refused it, whatever it changed: each step it takes before a worker starts is kept once, a lane's death once per dispatch and a held text once under its Orca request id, a worker an earlier run started is adopted from the receipt its attempt keeps and never started twice, and a refusal after a worker started hands its receipt to the newest install's `drive job started` or `drive job sent`."""
    return args.run in (job_launch, job_resume)


def refused(args: argparse.Namespace, error: Exception, detached: str | None) -> int:
    """Print the verb's refusal and exit 2. A launch or resume state.detach started, which only the watch runs, also writes the refusal as an event of the watch hearing its job's order, naming the log its output went to, so it reaches the watch's lines; the job and its order are read from the drive as saved, which a newer install's save never refuses."""
    print(f"drive: {error}", file=sys.stderr)
    if detached and relaunching(args):
        name = state.resolve(args.drive)
        order = state.saved_job(name, args.id)["order"]
        heard = state.heard_lane(state.saved(name) | state.stored(name, ("orders",)), order)
        state.append_event(name, watch.RELAUNCH_REFUSED, job=args.id, order=order, verb=args.action, refusal=str(error), log=detached, lane=heard)
    return 2
