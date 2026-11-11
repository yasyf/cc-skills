import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import textwrap
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from drive import artifacts, brief, github, limits, orca, picture, place, reads, render, settled, slack, state, watch
from drive.state import DriveError

DRIVE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
INCIDENT_FIX = "fix"
INCIDENT_PARTS = {"release": ["first check"], "rollback": ["rollback", "cause"], "none": [state.INCIDENT_STOP, "cause"]}
FREEZE_FOR = timedelta(hours=12)
RELEASE_NAME = re.compile(r"[a-z]+-[a-z]+")
INCIDENT_EXEMPTION = "incident fix"
REVERT = re.compile(r"^Reverts?\s|⏪|\bThis reverts\b", re.MULTILINE)
FILING = ("project", "workstream")
JOB_ID = re.compile(r"j\d+")
ID_FORMS = "an order o<n>, a job j<n>, or a PR as owner/repo#N, its URL or its bare number"
WAITS_CARD = re.compile(r"https://\S+/archives/\S+/p\d+\S*|[\w.-]+:[\w.-]+|merge:[\w.-]+:[\w.-]+:\d+(?:-\d+)?")
CLAUDE_BUILDS = Path.home() / ".local" / "share" / "claude" / "versions"
TOLD_LINK = (
    "Link each card where the line names it, as <its permalink|your card on what it decides>, never as its bare permalink,"
    " which `cc-slack check-post` refuses."
)
HIDDEN = "not visible in Orca, which hides the worktrees it did not make for this repository"
PAGES = Path(__file__).resolve().parents[2] / "skills" / "long-running" / "reference"
LAUNCHED = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
MCP_TOOLS = ("say", "heard", "record", "order", "job", "pr", "incident", "status")
SHIM = """#!/bin/sh
drive_bin=$(python3 -I -c '
import json, os, pathlib
config = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude")
plugins = json.loads((config / "plugins" / "installed_plugins.json").read_text())["plugins"]
entries = [entry for key, installs in plugins.items() if key.startswith("long-running@") for entry in installs]
if not entries:
    raise SystemExit("drive: the long-running plugin is not installed")
newest = max(entries, key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")
print(pathlib.Path(newest["installPath"]) / "bin" / "drive")
') || exit 1
exec "$drive_bin" "$@"
"""


def emit(args: argparse.Namespace, line: str, payload: object) -> int:
    print(json.dumps(payload, indent=2) if args.json else line)
    return 0


def due_stamp(text: str) -> str:
    return state.stamp(state.parse_due(text))


def start(args: argparse.Namespace) -> int:
    if not DRIVE_NAME.fullmatch(args.name):
        raise DriveError(f"drive names are letters, digits, dot, dash and underscore: {args.name}")
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    fresh = state.new_drive(args.name, args.role, session, os.getcwd(), args.channel, orca.own_terminal())
    with state.edit_or_create(args.name, fresh) as data:
        data["roles"][args.role] = fresh["roles"][args.role]
        if args.channel:
            data["slack"]["channel"] = args.channel
        watched_channels = data["slack"].setdefault("watch", [])
        watched_channels.extend(channel for channel in dict.fromkeys(args.watch) if channel not in watched_channels)
    seen = orca_place(data["roles"][args.role], run_show)
    watched = f"{watch.ensure(args.name)}\n" if args.role == "coordinator" else ""
    text = f"{render.context(data, args.role)}\n\n{watched}{args.role}: {render.who(data, args.role)}, {seen}"
    return emit(args, text, {**data, "orca": {args.role: seen}})


def order_add(args: argparse.Namespace) -> int:
    """Record his order; an incident order records a job on each of its parts, its fix among them, unless it joins the open incident on the same failing service."""
    order_id, order, started, incidents = added_order(args)
    return emit(args, "\n".join([order_id, *started, *incidents]), {"id": order_id, **order, "incidents": incidents})


def added_order(args: argparse.Namespace) -> tuple[str, dict, list[str], list[str]]:
    name = state.resolve(args.drive)
    created = state.stamp()
    if args.incident and not args.joins and INCIDENT_FIX not in (args.parts or []):
        raise DriveError(
            f'an incident order starts its fix job at the alert, beside diagnosis: name "{INCIDENT_FIX}" among --parts, such as'
            f' --parts {INCIDENT_FIX} "first check", or pass --joins <o> when an open incident order covers the same failing service'
        )
    from_thread = source_thread(args.source)
    if args.thread:
        check_thread(state.load(name), {"from": args.source, "from_thread": from_thread, "thread": args.thread})
    tooling = args.words.startswith(state.TOOLING)
    if tooling and args.owner == "coordinator":
        raise DriveError(
            f'--owner coordinator: a "{state.TOOLING}" order is the communicator\'s, by his words of Nov 10 10:08 PM:'
            ' "and remember toolingh/meta improvements dont go to coorfidnatror"; leave --owner out'
        )
    with state.edit(name) as data:
        owner = args.owner or ("communicator" if tooling else state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID")) or "coordinator")
        joined = find(data["orders"], args.joins, "order") if args.joins else None
        if joined and not (joined["incident"] and joined["state"] not in state.CLOSED_ORDER_STATES):
            raise DriveError(f"--joins {args.joins}: name an open incident order; {args.joins} is {joined['state']}{'' if joined['incident'] else ', not an incident'}")
        incidents = [
            f"{other_id} {other['state']} incident: {other['text'][:80]}; {other['note'] or 'no record'}"
            for other_id, other in state.open_orders(data).items()
            if args.incident and not joined and other["incident"]
        ]
        order_id = state.next_id(data["orders"], "o")
        order = data["orders"][order_id] = {
            "text": args.words,
            "from": args.source,
            "from_thread": from_thread,
            "thread": args.thread or from_thread,
            "state": "open",
            "due": due_stamp(args.due) if args.due else None,
            "due_said": None,
            "promised": created if args.due else None,
            "next_due": None,
            "next_set": None,
            "kept": None,
            "withdrawn": None,
            "jobs": [],
            "releases": {},
            "parts": {part: state.new_part() for part in args.parts or [state.whole_order(args.words)]},
            "incident": args.incident or joined is not None,
            "kind": "build" if args.incident or joined is not None else args.kind,
            "complex": args.complex,
            "owner": owner,
            "project": args.project,
            "workstream": args.workstream,
            "proof": None,
            "note": joined and joined["note"],
            "created": created,
            "updated": created,
            "last_line": None,
        }
        if args.due:
            need_thread(data, order_id, order)
        started = [state.incident_job(data, name, order_id, part, created) for part in (order["parts"] if args.incident and not joined else [])]
    if joined:
        started.append(f"joins {args.joins}: {joined['note'] or 'no record'}; its owner job carries this order with --carries {order_id}")
    return order_id, order, started, incidents


def incident_open(args: argparse.Namespace) -> int:
    """Open an incident in one call: his order, a job on its fix beside a job on each diagnosis part, and the evidence record its note names."""
    name = state.resolve(args.drive)
    args.parts = [INCIDENT_FIX, *INCIDENT_PARTS["release" if args.release else "none" if args.no_rollback else "rollback"]]
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
    """Give an open incident its stop when no build can roll the break back: a job with apply authority whose PR turns the failing automatic path off and lands only on his tap."""
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
    return emit(args, started, {"id": args.id, "job": started.split()[0]})


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


def source_thread(source: str) -> str | None:
    """The thread the Slack message an order came from is in, its parent's ts when he wrote it as a reply, so the order's lines go in the thread his reply is in; None for an order from anywhere else."""
    if not source.startswith("slack:"):
        return None
    channel, ts = source.removeprefix("slack:").split("/")
    try:
        return slack.thread_of(channel, ts)
    except slack.SlackError as error:
        raise DriveError(f"--from {source}: refused, Slack cannot read the message it names: {error}") from None


def check_thread(data: dict, order: dict) -> None:
    """Refuse a thread Slack cannot read where the order's lines go, his DM or the channel of the message the order came from when it is that message or its thread, and a reply's ts, naming the thread it is in; the thread that message is in was read when the order was added."""
    thread = order["thread"]
    if thread == order["from_thread"]:
        return
    if not (where := state.promise_thread(data, order)):
        raise DriveError(f"--thread {thread}: refused, the drive has no DM to read it in; start it with --channel <his DM>")
    try:
        root = slack.thread_of(where[0], thread)
    except slack.SlackError as error:
        raise DriveError(f"--thread {thread}: refused, it is no thread in {where[0]}, where the order's lines go: {error}") from None
    if root != thread:
        raise DriveError(f"--thread {thread}: refused, it is a reply in thread {root}; pass --thread {root}")


def need_thread(data: dict, order_id: str, order: dict) -> None:
    if not state.promise_thread(data, order):
        raise DriveError(f"{order_id}: refused, no time stored: a promised time needs the thread its line goes in; pass --thread <ts>")


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


def his_words(link: str) -> str:
    if not slack.message(link).get("from_user"):
        raise DriveError(f"{link} is not his message; only his own words withdraw a promised line")
    return link


def drive_post_at(link: str, data: dict, order_id: str, named_part: bool) -> tuple[datetime, bool]:
    """When the drive made the post, and whether it is in one of the order's threads: its own, its jobs' cards', or a card of a PR it holds; a post that delivers a part named with --part counts wherever it was made, as a share in another channel does."""
    posted, root = slack.message_and_root(link)
    if not posted.get("from_claude"):
        raise DriveError(f"{link} is not the drive's post")
    threads = slack.order_threads(data, data["orders"][order_id])
    ours = not threads or posted.get("thread_ts") in {thread for _, thread in threads} or slack.names_order_pr(data, order_id, root)
    if not (ours or named_part):
        joins = (
            "; when his message at the root of that thread is another alert on this incident, that alert is its own order, which the"
            f' coordinator records with drive order add "<his alert>" --from slack:{state.CARD_LINK.search(link)[1]}/{root["ts"]}'
            f" --thread {root['ts']} --joins {order_id} --project \"{data['orders'][order_id]['project']}\" --workstream \"{data['orders'][order_id]['workstream']}\", and this line goes on that order"
            if data["orders"][order_id]["incident"] and root and root.get("from_user")
            else ""
        )
        raise DriveError(
            f"{link} is in none of the order's threads, its own, its cards' or its PRs' cards': {', '.join(thread for _, thread in threads)};"
            f" a post that delivers a part somewhere else is recorded with --part <that part>{joins}"
        )
    return slack.sent_at(posted), ours


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
    """The order apart from its project and workstream, so filing it alone leaves the time it last moved, which the dashboard reads as when a done order went live."""
    return json.dumps({field: value for field, value in order.items() if field not in FILING}, sort_keys=True)


def order_set(args: argparse.Namespace) -> int:
    """Change an order; closing one that owes him a promised line reads its thread and refuses until the line is there or he withdrew the time."""
    name = state.resolve(args.drive)
    withdrawn = his_words(args.withdrawn) if args.withdrawn else None
    data = state.load(name)
    closing = args.state in state.CLOSED_ORDER_STATES and not withdrawn
    order = find(data["orders"], args.id, "order")
    destination = order | {"thread": args.thread or order["thread"]}
    if args.thread:
        check_thread(data, destination)
    report = need_record_close(data, args.id, args.record, args.state == "dropped" and bool(args.note)) if args.state in state.CLOSED_ORDER_STATES else {}
    closing = closing and state.promise_thread(data, destination) is not None
    posts, unread = slack.order_posts(data, destination) if closing and state.owed(order) else ([], None)
    changes = {"note": args.note, "proof": args.proof, "thread": args.thread, "withdrawn": withdrawn}
    moment = state.now()
    due = state.parse_due(args.due, moment) if args.due else None
    if due and due <= moment:
        raise DriveError(f"{args.id}: refused, no time stored: {render.when(state.stamp(due))} is already past; promise a time to come")
    promised = ""
    with state.edit(name) as data:
        order = data["orders"][args.id]
        before = apart_from_filing(order)
        order.update({field: value for field, value in changes.items() if value is not None})
        if due and not (where := state.promise_thread(data, order)):
            state.remind(order, state.stamp(due), moment)
            promised = (
                f"\nstored {render.when(state.stamp(due))} as the drive's own time: {args.id} has no thread, so no line to him"
                " keeps it, and the watch says DUE once then; a time promised to him needs --thread <ts>"
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
        if args.parts:
            order["parts"] = {part: order["parts"].get(part, state.new_part()) for part in args.parts}
        if args.incident and not order["incident"] and not any(INCIDENT_FIX in state.job_parts(data["jobs"][job_id], args.id) for job_id in order["jobs"]):
            raise DriveError(
                f'{args.id}: refused, an incident order starts its fix job at the alert, beside diagnosis: give it a "{INCIDENT_FIX}"'
                f" part with --parts and record its job with drive job add codex --order {args.id} --part {INCIDENT_FIX} --agent <id> first"
            )
        if args.incident is not None:
            order["incident"] = args.incident
        if args.kind:
            order["kind"] = args.kind
        if args.complex is not None:
            order["complex"] = args.complex
        filed = [field for field in FILING if getattr(args, field) is not None]
        for field in filed:
            order[field] = getattr(args, field) or None
        if not filed or args.lined or apart_from_filing(order) != before:
            order["updated"] = state.stamp(moment)
        if args.lined:
            order["last_line"] = order["updated"]
    return emit(args, render.order_line(args.id, order) + promised, {"id": args.id, **order})


def order_unfiled(args: argparse.Namespace) -> int:
    data = state.load(state.resolve(args.drive))
    unfiled = {order_id: order for order_id, order in state.open_orders(data).items() if not (order["project"] and order["workstream"])}
    named = sorted({(order["project"], order["workstream"]) for order in data["orders"].values() if order["project"] and order["workstream"]})
    lines = [
        f"{order_id} needs {' and '.join(field for field in ('project', 'workstream') if not order[field])}: {render.cut(order['text'], 120)}"
        for order_id, order in unfiled.items()
    ]
    names = [f"in use: {project} / {workstream}" for project, workstream in named]
    return emit(args, "\n".join([*lines, *names]) or "every open order names its project and workstream", {"unfiled": unfiled, "in_use": named})


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
    """The report that closes the order, read from cci, or none when the order closes without one."""
    if seq is None:
        if not dropped_with_note and state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID")) == "communicator":
            raise DriveError(
                f"{order_id}: a handler closes only an order the report record it posted names in its close attr;"
                " pass --record <that record's seq>, or drop an order no record names with --state dropped --note <why>"
            )
        return {}
    report = report_attrs(seq)
    closes = report.get("close", "").split()
    if not closes:
        raise DriveError(f"record {seq} has no close attr, so it closes no order; an orders attr only says what its line is about")
    if order_id not in closes:
        raise DriveError(f"record {seq} does not close {order_id}: its close attr names only {', '.join(closes)}")
    return report


def report_attrs(seq: str) -> dict:
    """The attrs of a record, read from cci: a report's close, part, job and catch_up, with the lane that wrote it as its writer and when as written."""
    try:
        record = json.loads(reads.run(["cci", "show", seq, "--json"], Path.cwd()))
    except reads.ReadError as error:
        raise DriveError(f"record {seq} could not be read: {error}") from None
    return record["fields"].get("attrs", {}) | {"writer": record["lane"], "written": record["at"]}


def order_posted(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    if not args.undo:
        report = (report_attrs(args.record) if args.record else {}) | ({"part": args.part} if args.part else {})
        return emit(args, *record_post(name, args.id, args.link, report))
    order = communicators_order(state.load(name), args.id)
    [undone] = pick_parts(args.id, order, [args.part] if args.part else [])
    with state.edit(name) as data:
        data["orders"][args.id]["parts"][undone]["posted"] = None
    return emit(args, f'{args.id} "{undone}": not delivered', {"id": args.id, "part": undone, "posted": None})


def communicators_order(data: dict, order_id: str) -> dict:
    if state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID")) != "communicator":
        raise DriveError("only the communicator records a post in an order's thread or takes back a part's mark")
    return find(data["orders"], order_id, "order")


def report_refusal(data: dict, order_id: str, report: dict) -> str | None:
    """Why a report may not go in an order's thread: the drive's session that wrote it does not hold the order."""
    role = state.LANE_ROLES.get(report.get("writer") or "")
    if not (role and order_id in data["orders"]):
        return None
    held = state.held_elsewhere(data, order_id, role)
    return held and f"refused, the report is the {role}'s: {held}"


def thread_refusal(data: dict, order_id: str, thread: str | None) -> str | None:
    """Why a post to his DM may not go out on an order: it goes in none of the order's threads, its own, its cards' or the thread under a card of a PR it holds."""
    if order_id not in data["orders"] or not (threads := slack.order_threads(data, data["orders"][order_id])):
        return None
    if thread in {ts for _, ts in threads} or (thread and slack.names_order_pr(data, order_id, slack.root(data["slack"]["channel"], thread))):
        return None
    where = f"thread {thread}" if thread else "his DM root"
    return (
        f"refused, {where} is none of {order_id}'s threads, its own, its cards' or its PRs' cards': {', '.join(ts for _, ts in threads)};"
        f" post it in one of them, or post it with no order and record the part it delivers with drive order posted {order_id} <its permalink> --part <that part>"
    )


def reported_parts(order_id: str, order: dict, report: dict) -> list[str]:
    """The parts of the order a post delivers: the part its report or --part names, each part of an order its report closes, or the one part of an order a post carrying no report answers. A report naming neither part nor close is a progress line that delivers none, whatever job it names."""
    if part := report.get("part"):
        return pick_parts(order_id, order, [part])
    if order_id in report.get("close", "").split() or not report and len(order["parts"]) == 1:
        return list(order["parts"])
    return []


def record_post(name: str, order_id: str, link: str, report: dict) -> tuple[str, dict]:
    """Record the drive's post in an order's thread: the promised time it keeps, the order's last line, and the parts its report delivers."""
    data = state.load(name)
    order = communicators_order(data, order_id)
    if refused := report_refusal(data, order_id, report):
        raise DriveError(refused)
    delivered = reported_parts(order_id, order, report)
    at, ours = drive_post_at(link, data, order_id, bool(report.get("part")))
    line = state.stamp(at)
    closing = order_id in report.get("close", "").split()
    kept = ""
    with state.edit(name) as data:
        order = data["orders"][order_id]
        if ours:
            if state.owed(order) and closing:
                state.keep_closing(order, [at], state.parse_time(report["written"]))
                kept = "" if state.owed(order) else f", its close keeps the line promised for {render.when(order['due'])}"
            elif state.owed(order):
                state.keep(order, [at])
            order["last_line"] = max(order["last_line"] or line, line, key=state.parse_time)
        for part in delivered:
            order["parts"][part]["posted"] = link
    quoted = ", ".join(f'"{part}"' for part in delivered)
    said = f"{quoted}: posted" if delivered else "progress line, no part delivered:"
    away = "" if ours else ", outside his threads, so it is no line to him and keeps no promised time"
    return f"{order_id} {said} {link}{away}{kept}", {"id": order_id, "parts": delivered, "posted": link, "due": order["due"]}


def job_add(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    created = state.stamp()
    with state.edit(name) as data:
        order = find(data["orders"], args.order, "order")
        parts = pick_parts(args.order, order, args.part)
        role = state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID"))
        if refused := state.held_elsewhere(data, args.order, role) or brief.job_refusal(name, data, args.order, args.kind):
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
        }
        order["jobs"].append(job_id)
        order["updated"] = created
    return emit(args, "\n".join([job_id, *beside]), {"id": job_id, **job, "beside": beside})


def job_set(args: argparse.Namespace) -> int:
    """Change a job; a state it already holds changes nothing, and stopped is the one state set by hand."""
    name = state.resolve(args.drive)
    held = find(state.load(name)["jobs"], args.id, "job")["state"]
    already = f"{args.id} is already {held}\n" if args.state == held else ""
    if already:
        args.state = None
    elif args.state not in (None, "stopped"):
        returned = ", ".join(f"{word} sets {taken}" for word, taken in state.RESULT_STATES.items())
        raise DriveError(
            f"{args.id} is {held}, and --state sets only stopped, for a job that ends unfinished: every other state comes from the line"
            f" the job returns, which the watch reads or drive job report {args.id} - takes: {returned}, and BUILT <branch>, waits on <cards> sets built"
        )
    if args.state == "stopped":
        fix_kept(state.load(name), args.id)
    changes = {"agent": args.agent, "state": args.state, "said": args.said, "branch": args.branch, "due": job_clock(args.id, args.due)}
    urls = dict.fromkeys(state.pr_url(state.pr_key(url)) for url in args.pr)
    if jobs := [wait for wait in args.waits if wait in state.load(name)["jobs"]]:
        raise DriveError(
            f"--waits {', '.join(jobs)}: a job waits only on a card of his, by its Slack link or waits id, so his tap frees it;"
            f" wait on the card {' or '.join(jobs)} asked him on, which drive status {jobs[0]} names"
        )
    check_waits(name, args.waits)
    told_him(name, args)
    shown = args.card and shown_card(name, args.card)
    with state.edit(name) as data:
        job = find(data["jobs"], args.id, "job")
        job.update({field: value for field, value in changes.items() if value is not None})
        if args.card:
            hold_card(job, args.card, shown)
        job["waits"] = merged_waits(job, args)
        if args.carries:
            if args.part and len(args.carries) > 1:
                raise DriveError("--part names the parts of one carried order; carry one order a call to name its parts")
            for order_id in args.carries:
                if order_id == job["order"]:
                    raise DriveError(f"{args.id} is {order_id}'s own job; add its parts with --part")
                state.carry(data, args.id, order_id, pick_parts(order_id, find(data["orders"], order_id, "order"), args.part))
        elif args.part:
            added = pick_parts(job["order"], data["orders"][job["order"]], args.part)
            job["parts"] = list(dict.fromkeys([*job["parts"], *added]))
        if args.agent and args.agent.startswith(state.DISPATCH_PREFIX):
            job.update(state.local_lane(args.agent), agent=None)
        elif args.agent:
            job["where"] = "local"
            job["session"] = os.environ.get("CLAUDE_CODE_SESSION_ID")
        if args.repro:
            job["repro"] = keep_repro(name, args.id, args.repro)
        job["prs"].extend(url for url in urls if url not in job["prs"])
        job["updated"] = state.stamp()
    return emit(args, already + render.job_line(args.id, job), {"id": args.id, **job})


def fix_kept(data: dict, job_id: str) -> None:
    """His stop carries the scope his words give it, so nothing stops the last running job on an open incident's fix."""
    job = data["jobs"][job_id]
    for order_id in state.open_incidents(data, [job["order"], *job["carries"]]):
        if INCIDENT_FIX not in state.job_parts(job, order_id) or any(
            other_id != job_id
            and other["state"] not in state.FINISHED_JOB_STATES
            and order_id in (other["order"], *other["carries"])
            and INCIDENT_FIX in state.job_parts(other, order_id)
            for other_id, other in data["jobs"].items()
        ):
            continue
        raise DriveError(
            f"{job_id} is the last running job on incident {order_id}'s fix, and nothing stops an open incident's fix: his stop in one"
            ' thread stops that thread\'s work, as his "leave it alone here" of Nov 10 9:19 PM meant his own thread. Add the job that takes'
            f" the fix over first, with drive job add <kind> --order {order_id} --part {INCIDENT_FIX}, or close {order_id} once its fix is live"
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
    threads = [where[1] for order_id in incidents if (where := state.promise_thread(data, data["orders"][order_id]))]
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


def job_ask(args: argparse.Namespace) -> int:
    """Keep a job's ASK in the drive once it is in the shape he reads, each block in its own file; from a file, stdin with -, or with neither a remote lane's latest report."""
    from drive import superseded

    if args.check:
        limits.question(named_text(args.check, "ASK block file"), unsettled=args.unsettled)
        print("ready")
        return 0
    if args.id is None:
        raise DriveError("name the job whose ASK this keeps: drive job ask <j> <file>; drive job ask --check <file> only checks its shape")
    name = state.resolve(args.drive)
    data = state.load(name)
    job = find(data["jobs"], args.id, "job")
    text = named_text(args.file, "ASK block file") if args.file else job.get("report")
    if not text:
        raise DriveError(f"{args.id} sent no report to read an ASK from; pass the file its ASK block is in, or - and the block on stdin")
    asked = limits.question(text, unsettled=args.unsettled)
    if refused := slack.draw_refusal(asked.card(name, f"{name}:{args.id}-0")):
        raise DriveError(f"the ASK's card is out of shape, so cc-slack would not draw it: {refused}")
    shown = "\n".join([asked.title, *asked.summary, *asked.options])
    matched = job["may_settle"]
    if not matched or matched["title"] != asked.title:
        found = settled.matching(shown, text, [name], data["roles"]["coordinator"]["cwd"], superseded.said_under(data, job))
        matched = {"title": asked.title, "answers": [{"id": answer["id"], "title": answer["title"]} for answer in found]}
    if refused := settled.refusal(matched["answers"], args.unsettled, "--unsettled"):
        with state.edit(name) as data:
            data["jobs"][args.id]["may_settle"] = matched
        raise DriveError(refused)
    kept = state.drive_dir(name) / "asks" / f"{args.id}-{hashlib.sha256(text.encode()).hexdigest()[:8]}.md"
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(text)
    with state.edit(name) as data:
        job = data["jobs"][args.id]
        job["questions"].append(state.new_question(str(kept), asked.title, state.stamp(), args.unsettled))
        job.update(state="waiting", said=asked.title, updated=state.stamp(), may_settle=None)
    return emit(args, str(kept), {"id": args.id, "question": str(kept), **vars(asked)})


def job_card(args: argparse.Namespace) -> int:
    """Print the question card a kept ASK block draws, its Show me code drawn as highlighted pictures, and give the question kept in that file the card's waits id."""
    name = state.resolve(args.drive)
    card = question_card(name, *card_source(name, args), args.reworking)
    return emit(args, json.dumps(card), card)


def card_source(name: str, args: argparse.Namespace) -> tuple[Path, str]:
    """The kept ASK block a card draws and its waits id: after a job, its newest kept block or the one named, under the waits id its card already has unless --waits names one; a file alone, under --waits."""
    if not JOB_ID.fullmatch(args.ask):
        if not args.waits:
            raise DriveError(f"{args.ask}: a card drawn from a file needs --waits <drive>:<job>-<the ask record's seq>; drive job card <j> draws a job's newest kept ASK")
        return named_file(args.ask, "ASK block file").resolve(), args.waits
    job = find(state.load(name)["jobs"], args.ask, "job")
    named = Path(args.file).expanduser().resolve() if args.file else None
    kept = [question for question in job["questions"] if question["file"] and named in (None, Path(question["file"]).resolve())]
    if not kept:
        raise DriveError(f"{args.ask} keeps no ASK block{f' in {args.file}' if args.file else ''}: keep one with drive job ask {args.ask} <file>")
    question = kept[-1]
    if not (waits := args.waits or question["waits"]):
        raise DriveError(f"{args.ask}'s ASK in {question['file']} has no card yet, so no waits id: pass --waits {name}:{args.ask}-<its ask record's seq>")
    return Path(question["file"]), waits


def question_card(name: str, path: Path, waits: str, reworking: str | None = None) -> dict:
    """The card a kept ASK block draws under a waits id, a line atop its body saying the question is being redone and why while it is; a waits id naming a job of the drive gives that job's question kept in the file the id, and one naming none, a card asked outside any job, only draws."""
    from drive import superseded

    asked = limits.redrawn(path.read_text())
    data = state.load(name)
    job_id = waits.partition(":")[2].partition("-")[0]
    if job_id in data["jobs"] and (refused := superseded.redraw_refusal(data, data["jobs"][job_id], waits, str(path))):
        raise DriveError(refused)
    show_me = picture.drawn(state.drive_dir(name) / "pictures", asked.show_me)
    summary = [f"{limits.REDONE}{reworking}", *asked.summary] if reworking else asked.summary
    card = asked.card(name, waits) | {"body": limits.card_body(summary, show_me)}
    if job_id in data["jobs"]:
        with state.edit(name) as data:
            keep_waits(data["jobs"][job_id], path, waits)
    return card


def cards_redraw(args: argparse.Namespace) -> int:
    """Print, one JSON line each in one pass, the card `drive job card` prints for each of his open question cards, drawn from the ASK file `ask_source` finds for it; each card that does not redraw is named once the rest are printed."""
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
            drawn.append(question_card(name, ask_source(name, shown[waits]), waits))
        except DriveError as error:
            missed.append(f"{waits}: {error}")
    if drawn:
        emit(args, "\n".join(map(json.dumps, drawn)), drawn)
    if missed:
        raise DriveError("these cards did not redraw:\n- " + "\n- ".join(missed))
    return 0


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
        rebuilt.write_text(limits.rebuilt(shown["card"]))
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
    question = next((question for question in reversed(job["questions"]) if question["file"] and Path(question["file"]).resolve() == file.resolve()), None)
    if question is None:
        return
    redrawn = next((other["card"] for other in job["questions"] if other is not question and other["waits"] == waits and other["card"]), None)
    question.update(waits=waits, card=question["card"] or redrawn)
    job["questions"] = [other for other in job["questions"] if other is question or other["waits"] != waits]


def job_report(args: argparse.Namespace) -> int:
    """Keep a job's whole return from stdin in the drive, its first line setting a running job's state, or print the one kept, or a remote lane's latest report."""
    name = state.resolve(args.drive)
    data = state.load(name)
    job = find(data["jobs"], args.id, "job")
    kept = state.report_path(name, args.id)
    if args.stdin:
        whole = sys.stdin.read()
        if not whole.strip():
            raise DriveError(f"stdin held no report for {args.id}; pipe its whole return in: printf '%s\\n' '<line>' ... | drive job report {args.id} -")
        if refused := brief.report_refusal(name, data, args.id, whole):
            raise DriveError(f"{args.id}: refused, nothing kept: {refused}")
        kept.parent.mkdir(exist_ok=True)
        kept.write_text(whole)
        with state.edit(name) as data:
            state.take_result(data, args.id, whole.strip().splitlines()[0].strip())
        return emit(args, str(kept), {"id": args.id, "report": str(kept)})
    text = state.report_text(name, args.id, job)
    if not text and not job["said"]:
        raise DriveError(f"{args.id} kept no report and has returned nothing yet")
    if not text:
        return emit(args, f"{job['said']}\n({args.id} kept no report; this one-line result is all it returned)", {"id": args.id, "report": None, "said": job["said"]})
    return emit(args, text.rstrip("\n"), {"id": args.id, "report": text})


def keep_repro(name: str, job_id: str, source: str) -> str:
    path = named_file(source, "reproduction file")
    kept = state.drive_dir(name) / "repro" / f"{job_id}{path.suffix}"
    kept.parent.mkdir(exist_ok=True)
    if not kept.exists() or not kept.samefile(path):
        shutil.copyfile(path, kept)
    return str(kept)


def place_job(args: argparse.Namespace) -> int:
    data, moment = state.load(state.resolve(args.drive)), state.now()
    local_builders = sum(
        job["kind"] == "builder"
        and job["state"] == "running"
        and job.get("where") != "remote"
        and watch.local_gap(job_id, job, data, moment) is None
        for job_id, job in data["jobs"].items()
    )
    share, ceiling = place.busy(), place.limit()
    where, why = place.verdict(share, ceiling, local_builders)
    payload = {"where": where, "why": why, "busy": round(share, 3), "limit": ceiling, "local_builders": local_builders}
    return emit(args, f"{where}: {why}", payload)


def remote_job(data: dict, job_id: str) -> dict:
    job = find(data["jobs"], job_id, "job")
    if job.get("where") == "remote":
        return job
    if job.get("agent"):
        raise DriveError(f"{job_id} runs on this Mac as agent {job['agent']}; resume it with SendMessage")
    raise DriveError(
        f"{job_id} has no agent and no lane yet: start it on a Sprite with drive job launch {job_id} <brief>, as an Orca"
        f" worker on this Mac with drive job launch {job_id} <brief> --local, or start an Agent here whose brief ends Drive: <drive>, order <order>, job {job_id}"
    )


def checkout() -> Path:
    root = Path.cwd()
    if not (root / ".orca.json").is_file():
        raise DriveError(f"{root} has no .orca.json; run this from the root of the checkout lanes clone")
    return root


def job_brief(args: argparse.Namespace) -> int:
    """Write a job's brief from what the drive holds: his order's words verbatim, each ruling's recorded words whole, the earlier jobs' transcripts, and the lines the call names."""
    name = state.resolve(args.drive)
    data = state.load(name)
    job = find(data["jobs"], args.id, "job")
    order = data["orders"][job["order"]]
    checkout_dir = Path(data["roles"]["coordinator"]["cwd"])
    rulings = [ruling_words(answer_id, checkout_dir) for answer_id in args.ruling]
    earlier = [line for other in order["jobs"] if other != args.id and (line := transcript_line(other, data["jobs"][other]))]
    building = job["kind"] in brief.RESEARCHED_KINDS and order["kind"] == "build" and not order["incident"]
    found = brief.research(name, data, job["order"]) if building else None
    scratch = Path(args.scratch).expanduser() if args.scratch else state.drive_dir(name) / "scratch" / args.id
    scratch.mkdir(parents=True, exist_ok=True)
    fields = {
        "Order (his words)": order["text"],
        "Serves": args.serves,
        "Goal": job["goal"],
        "Done when": args.done_when,
        "Where": args.where,
        "Runs as": args.runs_as,
        "Handoff": args.handoff,
        "Decisions that bind": rulings or "none named; search his answers on the area",
        "Researched": found and found[0],
        "Plan": args.plan,
        "Incident": args.incident,
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
    written.write_text("\n".join(lines) + "\n")
    with state.edit(name) as data:
        data["jobs"][args.id]["brief"] = str(written)
    line = f"Read your brief first: {written}\nhis words from {job['order']}, rulings: {len(rulings)}, earlier jobs: {len(earlier)}"
    return emit(args, line, {"id": args.id, "brief": str(written), "rulings": len(rulings), "earlier": earlier})


def ruling_words(answer_id: str, checkout_dir: Path) -> str:
    try:
        answer = json.loads(reads.run(["ccn", "answer", "show", answer_id, "--json"], checkout_dir))
    except reads.ReadError as error:
        raise DriveError(f"ruling {answer_id}: {error}") from None
    return f"{answer['title']} ({answer['id'][:7]})\n{textwrap.indent(answer['body'].strip(), '  ')}"


def transcript_line(job_id: str, job: dict) -> str | None:
    if job.get("where") == "remote" and job.get("dispatch"):
        return f"{job_id} {job['kind']}, a remote lane: drive job replies {job_id}"
    path = artifacts.transcript(job)
    return f"{job_id} {job['kind']}: {path}" if path else None


def job_launch(args: argparse.Namespace) -> int:
    """Start a job's Orca lane, on a Sprite or with --local on this Mac, stamped as launching once nothing refuses it so the watch reads it as starting until the launch could have finished."""
    name = state.resolve(args.drive)
    data = state.load(name)
    job = find(data["jobs"], args.id, "job")
    if dispatch := job.get("dispatch"):
        raise DriveError(f"{args.id} already started as dispatch {dispatch}")
    if job.get("agent"):
        remote_job(data, args.id)
    if not (args.brief or job.get("brief")):
        raise DriveError(f"{args.id} has no brief: write it with drive job brief {args.id}, or name its file")
    root = checkout()
    text = named_text(args.brief or job["brief"], "brief file")
    role = state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID"))
    if refused := state.held_elsewhere(data, job["order"], role) or brief.brief_refusal(name, data, args.id, text, None):
        raise DriveError(f"{args.id}: refused, not launched: {refused}")
    with state.edit(name) as data:
        job = data["jobs"][args.id]
        if not job.get("agent"):
            job.update(where="remote", route=state.LOCAL_ROUTE if args.local else None, launching=state.stamp())
    run = job.get("run") or orca.new_run(f"drive {name} {args.id}: {job['goal']}")
    with state.edit(name) as data:
        data["jobs"][args.id]["run"] = run
    kept = state.drive_dir(name) / "jobs" / f"{args.id}.md"
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(orca.vm_brief(name, args.id, text, job["route"]))
    receipt = orca.launch(name, orca.lane_name(name, args.id), kept, root, run, job["route"])
    started = {field: receipt[field] for field in LAUNCHED}
    with state.edit(name) as data:
        job = data["jobs"][args.id]
        job.update(started, brief=str(kept), ask=None, report=None, updated=state.stamp())
    return emit(args, f"{args.id} started {orca.place(job)} as dispatch {started['dispatch']}", started)


def bound(args: argparse.Namespace) -> int:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    done = orca.bound_command(state.resolve(args.drive), args.lane_run, command)
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    return done.returncode


def started_job(data: dict, job_id: str) -> dict:
    job = remote_job(data, job_id)
    if not job.get("dispatch"):
        raise DriveError(f"{job_id} has not started; launch it with: drive job launch {job_id} <brief>")
    return job


def job_resume(args: argparse.Namespace) -> int:
    """Send the lane its next event, or, when its worker takes no messages, start the job's next worker with the event in its handoff; an answer to an open question that names `LOG <url>`s carries those logs ahead of the text, read here as the watch reads them."""
    name = state.resolve(args.drive)
    data = state.load(name)
    job = started_job(data, args.id)
    try:
        end = orca.lane_end(job)
    except orca.OrcaError as error:
        raise DriveError(
            f"{args.id}'s lane cannot be checked, so nothing was sent: {error}; resume it again once Orca reaches its lane {orca.ON_MAC if state.on_mac(job) else 'on its Sprite'}"
        ) from None
    if end:
        return restart(args, name, job["dispatch"], end)
    text = args.text
    asked = reads.logs(job["report"] or "") if job.get("ask") else None
    if asked:
        try:
            said = reads.answer(asked, Path(data["roles"]["coordinator"]["cwd"]))
        except reads.ReadError as error:
            said = f"{asked}: {error}"
        text = f"{said}\n\n{args.text}"
    how = orca.answer(name, job, text) + (f", with {asked} read ahead of your text" if asked else "")
    with state.edit(name) as data:
        fresh = data["jobs"][args.id]
        if fresh.get("ask") == job.get("ask"):
            fresh.update(ask=None, state="running")
        fresh["updated"] = state.stamp()
    return emit(args, f"{args.id}: {how}", {"id": args.id, "how": how})


def restart(args: argparse.Namespace, name: str, dispatch: str, end: orca.LaneEnd) -> int:
    """Start the job's next worker with a handoff: in the terminal of a worker that finished while Orca holds it, on its Sprite or on this Mac, else on a fresh Sprite, or in a fresh worktree on this Mac, on the same Run."""
    root = None if end.worktree else checkout()
    with state.edit(name) as data:
        job = data["jobs"][args.id]
        state.lane_died(job, dispatch, end.why, state.now())
    ended = job["deaths"][-1]
    attempt = f"{args.id}-{len(job['deaths']) + 1}"
    kept = state.drive_dir(name) / "jobs" / f"{attempt}.md"
    taking_over = orca.handoff(orca.brief_of(Path(job["brief"]).read_text()), ended, job, args.text, end)
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(orca.vm_brief(name, args.id, taking_over, job["route"]))
    lane = orca.lane_name(name, attempt)
    was = orca.place(job)
    if end.worktree:
        receipt = orca.start_after(name, lane, kept, job, end.worktree)
    else:
        receipt = orca.launch(name, lane, kept, root, job["run"], job["route"])
    started = {field: receipt[field] for field in LAUNCHED}
    with state.edit(name) as data:
        data["jobs"][args.id].update(
            started, handoff=str(kept), ask=None, report=None, reads={}, state="running", updated=state.stamp()
        )
    carrying = "in a handoff carrying its brief, its branch, its last line, its report and its PRs"
    local = state.on_mac(job)
    if end.worktree:
        line = (
            f"{args.id}: the message went to the job's next worker, started in its terminal {orca.ON_MAC if local else 'on the same Sprite'}"
            f" as dispatch {started['dispatch']} {carrying}; its lane {was} had finished ({end.why})"
        )
    else:
        line = (
            f"{args.id}: the message went to the job's next worker, relaunched {'in a fresh worktree ' + orca.ON_MAC if local else orca.place({**job, **started})}"
            f" as dispatch {started['dispatch']} {carrying}; its lane {was} was dead ({end.why}), and work not pushed from"
            f" {'its worktree' if local else ended['workspace']} is not carried over"
        )
    how = f"started {orca.ON_MAC if local else 'on its Sprite'}" if end.worktree else "relaunched"
    return emit(args, line, {"id": args.id, "how": how, "ended": ended, "handoff": str(kept), **started})


def job_replies(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    remote_job(state.load(name), args.id)
    kept = state.replies(name, args.id)
    text = "\n\n".join(f"{reply['created_at']} {reply['type']}: {reply['body'] or reply['subject']}" for reply in kept)
    return emit(args, text or f"{args.id} has sent nothing yet", kept)


def job_end(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    data = state.load(name)
    job = started_job(data, args.id)
    reclaimed = orca.reclaim(job["run"], job["dispatch"], checkout())
    with state.edit(name) as data:
        ended = data["jobs"][args.id]
        ended.update(state=ended["state"] if ended["state"] in state.FINISHED_JOB_STATES else "done", ask=None, updated=state.stamp())
        state.drop_carries(data, args.id)
    return emit(args, reclaimed or f"{args.id} ended", {"id": args.id, "reclaim": reclaimed})


def pr_add(args: argparse.Namespace) -> int:
    from drive import card, view

    name = state.resolve(args.drive)
    key = state.pr_key(args.url)
    url = state.pr_url(key)
    if exposed := card.exposure(key):
        raise DriveError(f"{key}: {exposed}")
    with state.edit(name) as data:
        job = find(data["jobs"], args.job, "job")
        cards = [wait for wait in view.holding(data, job["waits"], view.live_cards(name)) if state.card_wait(data, wait)]
        if cards and key not in data["prs"] and not state.open_incidents(data, [job["order"], *job["carries"]]):
            raise DriveError(
                f"{args.job} waits on his answer to {', '.join(cards)}: it puts up no PR until he answers each;"
                " the watch says FREE when the last is answered"
            )
        pr = data["prs"].setdefault(key, state.new_pr(url, args.job, job["order"]))
        pr.update(job=args.job, order=job["order"], wants_tap=pr["wants_tap"] or args.tap)
        if url not in job["prs"]:
            job["prs"].append(url)
    return emit(args, key, {"key": key, **pr})


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
    if args.tap and not args.serves:
        raise DriveError("--tap needs --serves <order>: the order of his whose words ask for this PR")
    if args.tap and not args.design:
        raise DriveError(
            "--tap needs --design asked:<his answer id or card permalink> for each design decision the PR makes,"
            " or --design none: a design he never answered goes back as a question, never on a card"
        )
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
    dropped = known["prs"][key]["tap"]
    if args.no_tapped:
        refuse_a_tap_of_his(name, key, dropped)
    order = known["prs"][key]["order"]
    if (cards := [wait for wait in args.waits if state.card_wait(known, wait)]) and state.open_incidents(known, [order]):
        raise DriveError(
            f"--waits {', '.join(cards)}: {key} serves incident {order}, whose fix lands on the recommended option while his card"
            " stays open; a different pick of his starts a follow-up job on the same order"
        )
    check_waits(name, args.waits)
    if args.exempt == INCIDENT_EXEMPTION:
        refuse_an_exempt_stop(known, key)
        refuse_an_exempt_revert(name, key)
    if args.tap or args.serves or args.design:
        refuse_an_undrawable_tap(key, args)
    served = ""
    with state.edit(name) as data:
        pr = find(data["prs"], key, "drive PR")
        if args.serves:
            served = f"\nserves {args.serves}, his words: {find(data['orders'], args.serves, 'order')['text']}"
        set_pr(pr, args)
    untapped = ""
    if args.no_tapped:
        repo, number = key.split("#")
        state.append_event(name, "untapped", repo=repo, number=int(number), label=dropped["label"], source=dropped["source"], why=args.why)
        untapped = f"\ndropped the {dropped['label']} the drive gave itself ({dropped['source']}): {args.why}"
    withdrawn: list[str] = []
    if why := withdrawal(args, view.holding(data, args.waits, view.live_cards(name))):
        try:
            withdrawn = slack.withdraw_pr_card(pr["url"], why)
        except slack.SlackError as error:
            raise DriveError(f"{key}: withdrawing its open card failed, so his Approve still shows: {error}; run this again") from None
        view.refresh_cards(name)
    line = render.pr_line(key, pr) + served + untapped + "".join(f"\nwithdrew his card {card}: {why}" for card in withdrawn)
    return emit(args, line, {"key": key, "withdrawn": withdrawn, **pr})


def withdrawal(args: argparse.Namespace, held: list[str]) -> str | None:
    """Why his open card for the PR is withdrawn: it no longer waits on his tap, it is a port, or it now waits on something that holds."""
    if args.tap is False:
        return args.why
    if args.port_of:
        return f"port of #{args.port_of}, which lands with no tap"
    if held:
        return f"it waits on {', '.join(held)} before his tap"
    return None


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
        if not (WAITS_CARD.fullmatch(wait) and view.card_open(wait, found)):
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
    if args.port_of:
        pr.update(port_of=args.port_of, exempt="pure port", wants_tap=False)
    if args.tap is not None:
        pr["wants_tap"] = args.tap
    if args.fundamental:
        pr["fundamental"] = args.fundamental
        pr["tap"] = None
        pr["exempt"] = None
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
    members = after.get("stack") or [key]
    if reason := card.waiting(known, members) or card.tap_refusal(known, members, card.read_all(members)):
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


def refuse_an_exempt_stop(data: dict, key: str) -> None:
    """The PR of an incident's stop turns the failing path off, which no incident order approves: it lands only on his tap, on a card quoting the bug from the record."""
    pr = data["prs"][key]
    if state.INCIDENT_STOP not in state.job_parts(data["jobs"][pr["job"]], pr["order"]):
        return
    raise DriveError(
        f'{key}: refused, it is {pr["order"]}\'s stop, which turns the failing path off, and a stop never lands as an "{INCIDENT_EXEMPTION}":'
        f" only his tap lands it. Draw his tap card with drive pr set {state.pr_url(key)} --tap --serves {pr['order']} --design none,"
        f" its body quoting the bug from {pr['order']}'s record ({data['orders'][pr['order']]['note'] or 'none noted yet'})"
    )


def refuse_an_exempt_revert(name: str, key: str) -> None:
    """A PR whose title or body says it reverts a change takes that change out, which no incident order approves: it lands only on his tap, on a card stating the bug in that change."""
    from drive import card

    if not (read := card.read(key)):
        raise DriveError(f"{key}: could not read it from GitHub to check that it reverts nothing")
    if not REVERT.search(f"{read['title']}\n{read['body']}"):
        return
    data = state.load(name)
    order_id = find(data["prs"], key, "drive PR")["order"]
    record = data["orders"][order_id]["note"] or "none noted yet"
    raise DriveError(
        f'{key}: refused, it reverts a change, and a revert never lands as an "{INCIDENT_EXEMPTION}": no incident order approves it,'
        f" only his tap does. Draw his tap card with drive pr set {state.pr_url(key)} --tap --serves {order_id} --design none,"
        f" its body stating the bug in the change it takes out, with that change's PR and evidence, from {order_id}'s record ({record})"
    )


def tap_verdict(pr: dict | None) -> tuple[bool, str]:
    if pr is None:
        return True, "not a drive PR"
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


def answer(args: argparse.Namespace) -> int:
    """Record an answer or ruling of his against the cards asking him and PRs not yet live that it could replace, those of the job and order of the card it answers and those its words name, and the jobs waiting on their orders: refuse until each card and PR is marked standing, superseded or reopened by it and each job sent his words or untouched, settle each superseded or reopened card in place, and close on GitHub an open PR superseded outright; any other card or PR still asking him may be marked too."""
    from drive import superseded, view

    name = state.resolve(args.drive)
    found = view.live_cards(name)
    if found is None:
        raise DriveError("his cards could not be read, so nothing can say what this answer leaves asking him; the watch rereads them every minute, run this again then")
    data = state.load(name)
    asked = superseded.asking(data, found)
    if args.id is None:
        if not (args.title and args.words and args.link):
            raise DriveError('name his answer\'s cc-notes id, or pass --title "<the question, verbatim>" with --words and --link to record it in cc-notes first')
        args.id = settled.recorded(args.title, args.words, ["scope:durable", f"drive:{name}", *args.label], data["roles"]["coordinator"]["cwd"])
    if args.id not in data["answers"]:
        if args.stands or args.superseded or args.reopened or args.others_stand or args.sent or args.untouched:
            raise DriveError(
                f"{args.id} is not recorded yet: run it with --words and --link alone first, which lists each card still asking him"
                " and each PR not yet live, and mark them once you have read it"
            )
        if not (args.words and args.link):
            raise DriveError(f'{args.id} is not recorded yet: pass --words "<his words, verbatim>" and --link <the permalink of his message or the card he tapped>')
        with state.edit(name) as data:
            data["answers"][args.id] = superseded.record(data, args.words, args.link, asked, state.stamp())
    kept = data["answers"][args.id]
    open_keys = superseded.unmarked(kept, asked)
    named = [*args.stands, *(key for key, _ in [*args.superseded, *args.reopened])]
    told = [*args.sent, *args.untouched]
    if twice := sorted({key for key in [*named, *told] if [*named, *told].count(key) > 1}):
        raise DriveError(f"{', '.join(twice)}: marked twice; mark each card or PR once, standing, superseded or reopened, and each job once, sent or untouched")
    if stray := [key for key in named if key not in superseded.markable(kept, asked)]:
        raise DriveError(f"{', '.join(stray)}: not a card or PR that {args.id} has not marked\n{superseded.listing(args.id, kept, open_keys, asked, data)}")
    if stray := [job_id for job_id in told if job_id not in superseded.unheard(data, kept)]:
        raise DriveError(
            f"{', '.join(stray)}: not a job waiting on an order {args.id} touches that is unmarked\n{superseded.listing(args.id, kept, open_keys, asked, data)}"
        )
    refusals = [
        superseded.stack_refusal(asked, [key for key, _ in [*args.superseded, *args.reopened]]),
        *(superseded.fold_refusal(data, key, asked[key], into) for key, into in args.superseded if into),
        *(superseded.reopen_refusal(data, key, asked[key], by) for key, by in args.reopened),
        args.others_stand and superseded.others_refusal(args.id, kept, asked, named, data),
    ]
    if why := next((refusal for refusal in refusals if refusal), None):
        raise DriveError(why)
    with state.edit(name) as data:
        data["answers"][args.id]["cards"].update({key: {"mark": "stands"} for key in args.stands})
        data["answers"][args.id]["jobs"].update({job_id: {"mark": "sent"} for job_id in args.sent} | {job_id: {"mark": "untouched"} for job_id in args.untouched})
    done = []
    cleared: set[str] = set()
    try:
        for mark, key, job_id in [*(("superseded", *pair) for pair in args.superseded), *(("reopened", *pair) for pair in args.reopened)]:
            card = asked[key]["card"]
            if card and card not in cleared:
                superseded.settle(card, kept)
                cleared.add(card)
            if mark == "superseded" and superseded.stops_open_pr(data, key, job_id):
                superseded.close(key)
            with state.edit(name) as data:
                if card:
                    done.append(f"{key}: its card {card} now shows his words and a link to them, with no buttons")
                if mark == "reopened":
                    data["answers"][args.id]["cards"][key] = {"mark": mark, "job": job_id, "card": card}
                    done += superseded.reopen(data, args.id, key, job_id)
                else:
                    data["answers"][args.id]["cards"][key] = {"mark": mark, "job": asked[key]["job"], "into": job_id, "card": card}
                    done += [*([] if card else [f"{key}: no card was drawn for it"]), *superseded.supersede(data, args.id, key, asked[key], job_id)]
    finally:
        if args.superseded or args.reopened:
            view.refresh_cards(name)
    with state.edit(name) as data:
        kept = data["answers"][args.id]
        if args.others_stand:
            kept["cards"].update({key: {"mark": "stands"} for key in superseded.unmarked(kept, asked)})
    if (left := superseded.unmarked(kept, asked)) or superseded.unheard(data, kept):
        raise DriveError("\n".join([*done, superseded.listing(args.id, kept, left, asked, data)]))
    marked = [mark["mark"] for mark in kept["cards"].values() if mark]
    heard = [mark["mark"] for mark in kept["jobs"].values() if mark]
    line = f"his answer {args.id} recorded: {marked.count('stands')} standing, {marked.count('superseded')} superseded, {marked.count('reopened')} reopened by it"
    if heard:
        line += f"; {heard.count('sent')} waiting {'job' if heard.count('sent') == 1 else 'jobs'} sent his words, {heard.count('untouched')} untouched"
    return emit(args, "\n".join([line, *done]), {"id": args.id, **kept})


def superseded_mark(text: str) -> tuple[str, str | None]:
    key, _, how = text.rpartition("=")
    if not key or not how:
        raise argparse.ArgumentTypeError("say <key>=stop, or <key>=<the live job that carries its work on>")
    return key, None if how == "stop" else how


def reopened_mark(text: str) -> tuple[str, str]:
    key, _, job_id = text.rpartition("=")
    if not key or not job_id:
        raise argparse.ArgumentTypeError("say <PR key>=<the live job that reworks it to his words>")
    return key, job_id


def heard(args: argparse.Namespace) -> int:
    from drive import watch

    name = state.resolve(args.drive)
    moment = state.now()
    with state.edit(name) as data:
        back = state.owner_away(data, moment)
        since = (data.get("owner") or {}).get("heard")
        data["owner"] = {"heard": state.stamp(moment), "away": args.away}
        news = []
        if back and since:
            for key, order in data["orders"].items():
                last = max(state.parse_time(order["last_line"] or order["created"]), state.parse_time(since))
                if watch.changed_since(order, data["jobs"], last):
                    news.append(key)
    if news:
        state.append_event(name, "catch-up", dm=data["slack"]["channel"], since=render.when(since), orders=" ".join(news))
    line = f"CATCH-UP since {render.when(since)}: {' '.join(news)}" if news else ("away" if args.away else "heard")
    return emit(args, line, {"away": args.away, "back": back, "since": since, "catch_up": news})


def overlay_live(data: dict) -> str:
    """Read the drive's open PRs from GitHub into data, never saved; say whether the PR fields are live or stored."""
    keys = state.open_pr_keys(data)
    try:
        reads = github.fetch(keys) if keys else {}
    except github.GithubError as error:
        return f"stored (GitHub read failed: {error})"
    for key, read in reads.items():
        data["prs"][key].update(github.live(read, data["prs"][key]))
    missing = [key for key in keys if key not in reads]
    return f"live; stored for {', '.join(missing)}, which GitHub did not return" if missing else "live"


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
    found = view.live_cards(name)
    for job in data["jobs"].values():
        job["questions"] = view.open_questions(job, found)
    if args.ids:
        only(data, args.ids)
    pr_fields = overlay_live(data)
    seen = {role: with_build(orca_place(held, name_show), held) for role, held in data["roles"].items()}
    named = bool(args.ids)
    return emit(args, render.status_text(data, pr_fields, seen, named), render.status_data(data, pr_fields, seen, named))


def with_build(place: str, held: dict) -> str:
    path = watch.config_dir() / "projects" / watch.NOT_SLUG.sub("-", held["cwd"]) / f"{held['session']}.jsonl"
    note = watch.unknown_model(*watch.running_build(path), CLAUDE_BUILDS) if path.exists() else None
    return f"{place}; {note}" if note else place


def context(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    data = state.load(name)
    role = args.role or state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID"))
    if role is None:
        raise DriveError(f"this session holds no role in drive {name}; pass --role")
    print(render.context(data, role))
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
        dashboard.serve(name, port)
        return 0
    view.refresh_cards(name)
    print("\n".join(dashboard.start(name, port)))
    return 0


def watch_events(args: argparse.Namespace) -> int:
    """Run the watch with --loop; else start it as its own process unless one runs, and point the coordinator's cci channel at its records when this is the coordinator's session."""
    if args.loop:
        return watch.main(args.drive)
    name = state.resolve(args.drive)
    print(watch.ensure(name))
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if state.session_role(state.load(name), session) == "coordinator":
        try:
            watch.listen(name, session)
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
    from drive import tools

    try:
        print(tools.run(args.tool, state.resolve(), json.load(sys.stdin)))
    except reads.ReadError as error:
        raise DriveError(str(error)) from None
    return 0


def install_plugin(args: argparse.Namespace) -> int:
    from drive import install

    return install.main(args.plugin, args.sha, args.repo, args.timeout)


def reload_plugins(args: argparse.Namespace) -> int:
    from drive import install

    return install.reload_main()


def default_bin_dir() -> Path:
    if xdg := os.environ.get("XDG_BIN_HOME"):
        return Path(xdg)
    return Path.home() / ".local" / "bin"


def setup(args: argparse.Namespace) -> int:
    bin_dir = Path(args.bin_dir) if args.bin_dir else default_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "drive"
    shim.write_text(SHIM)
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
            " coordinator"
        ),
    )
    waits_opt.add_argument("--no-waits", action="store_true", help="drop every wait it carries before adding any --waits")

    root = argparse.ArgumentParser(prog="drive", description="State and events of a long-running drive.")
    root.add_argument("--version", action="version", version=f"drive {json.loads(render.PLUGIN_JSON.read_text())['version']}")
    verbs = root.add_subparsers(dest="verb", required=True)

    verb = verbs.add_parser("start", parents=[json_opt], help="create the drive or bind one of its two roles to this session")
    verb.add_argument("name")
    verb.add_argument(
        "--role", required=True, choices=state.ROLES, help="communicator talks to him; coordinator runs orders and jobs"
    )
    verb.add_argument("--channel", help="his DM channel id")
    verb.add_argument(
        "--watch", action="append", default=[], metavar="CHANNEL", help="another channel the coordinator subscribes to, kept across restarts"
    )
    verb.set_defaults(run=start)

    order = verbs.add_parser("order", help="his orders").add_subparsers(dest="action", required=True)
    verb = order.add_parser("add", parents=both)
    verb.add_argument("words", help="his words, verbatim")
    verb.add_argument("--from", dest="source", default="terminal", help="where he wrote it: slack:<channel>/<ts>, or terminal")
    verb.add_argument("--thread", help="the ts of the thread the order's lines go in; default: the thread the --from message is in")
    verb.add_argument("--due", help="the time his ack promised a line by; the watch says DUE from then until a line is in the thread")
    verb.add_argument("--parts", nargs="+", metavar="PART", help="each deliverable his words name; default: one, the whole order")
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
            " Researched: block before any builder or designer starts, and their briefs name it. An incident order is a build"
        ),
    )
    verb.add_argument(
        "--complex",
        action=argparse.BooleanOptionalAction,
        help="more than a one-fact lookup: its answer lane runs on Opus, never Sonnet; --no-complex marks a one-fact lookup, and an answer order asking several questions with neither starts no Sonnet lane",
    )
    verb.add_argument(
        "--owner",
        choices=state.ROLES,
        help=(
            "the one session that adds its jobs, starts its agents and posts its reports, refusing the other's; default: a"
            ' "Drive tooling:" order the communicator, any other the role of the session adding it, else the coordinator'
        ),
    )
    verb.add_argument("--project", metavar="NAME", required=True, help="the project the dashboard's Now screen files this order under, reusing one drive order unfiled lists as in use where it fits")
    verb.add_argument("--workstream", metavar="NAME", required=True, help="the workstream inside its project the Now screen files this order under, reusing one in use where it fits")
    verb.set_defaults(run=order_add)
    verb = order.add_parser("set", parents=both)
    verb.add_argument("id", help="the order")
    verb.add_argument("--state", choices=state.ORDER_STATES, help="done or dropped refuses while a promised line is not posted")
    verb.add_argument(
        "--due", help="when he hears next, owed from the next line in the order's thread; on an order with no thread, the drive's own time, said once"
    )
    verb.add_argument("--note", help="where the order stands, in a few words")
    verb.add_argument("--proof", help="the link that proves it done")
    verb.add_argument("--thread", help="the ts of the thread the order's lines go in")
    verb.add_argument("--lined", action="store_true", help="a line just went in its thread")
    verb.add_argument(
        "--withdrawn", metavar="LINK", help="his Slack message calling off the promised time; the order then owes no line for it"
    )
    verb.add_argument("--parts", nargs="+", metavar="PART", help="the order's parts, replacing its list")
    verb.add_argument("--incident", action=argparse.BooleanOptionalAction, help="mark or unmark it as an incident order")
    verb.add_argument("--kind", choices=state.ORDER_KINDS, help="sort it again: an answer whose work turns out to need a build becomes a build")
    verb.add_argument("--complex", action=argparse.BooleanOptionalAction, help="mark or unmark it as more than a one-fact lookup")
    verb.add_argument("--project", metavar="NAME", help="the project the dashboard's Now screen files this order under; an empty name takes it out")
    verb.add_argument("--workstream", metavar="NAME", help="the workstream inside its project the Now screen files this order under; an empty name takes it out")
    verb.add_argument(
        "--record",
        metavar="SEQ",
        help="the report record a handler posted; a close from the communicator's session needs it and closes only an order its close attr names",
    )
    verb.set_defaults(run=order_set)
    verb = order.add_parser("unfiled", parents=both, help="each open order that names no project or workstream yet, then the project and workstream pairs in use")
    verb.set_defaults(run=order_unfiled)
    verb = order.add_parser("posted", parents=both, help="the communicator posted a line in an order's thread, its cards' or its PRs' cards': a progress line, or the answer a part owes him; a part delivered elsewhere, as a share in another channel, names --part")
    verb.add_argument("id", help="the order")
    posted = verb.add_mutually_exclusive_group(required=True)
    posted.add_argument("link", nargs="?", help="the Slack link of the drive's post; one in the order's threads keeps a promised time and moves the order's last line")
    posted.add_argument("--undo", action="store_true", help="take back the delivered mark of the part --part names, so it counts as undelivered again")
    verb.add_argument(
        "--part",
        help="the part the post delivers, which counts in any channel; with none, the only part of an order of one, and no part of an order of several: a progress line, both only in the order's threads",
    )
    verb.add_argument(
        "--record",
        metavar="SEQ",
        help=(
            "the report record the post carries: it delivers the part its part attr names, counted in any channel, or each part of"
            " an order its close attr names; a record naming neither is a progress line and delivers no part"
        ),
    )
    verb.set_defaults(run=order_posted)
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
    verb.add_argument("--from", dest="source", default="terminal", help="where it came from: slack:<channel>/<ts>, or terminal")
    verb.add_argument("--thread", help="the ts of the thread the incident's lines go in; default: the thread the --from message is in")
    verb.add_argument("--due", help="the time his ack promised a line by; the watch says DUE from then until a line is in the thread")
    verb.add_argument("--record", required=True, metavar="TITLE", help="the evidence record's title: <service> on <cluster>: <symptom>, since <time>")
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
    verb.set_defaults(run=incident_open, incident=True, joins=None, kind=None, complex=False, owner=None)
    verb = incident.add_parser(
        "stop",
        parents=both,
        help=(
            "the turn the rollback finds no build that lacks the failing path: a stop part and its codex job, with apply authority,"
            " whose PR turns the failing automatic path off and lands only on his tap, on a card quoting the bug from the record"
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
    verb.set_defaults(run=job_add)
    verb = job.add_parser("set", parents=[*both, waits_opt])
    verb.add_argument("id", help="the job")
    verb.add_argument("--agent", help="the agent id the Agent tool returned")
    verb.add_argument(
        "--state", choices=state.JOB_STATES, help="stopped: it ends unfinished; every other state comes from the line the job returns, and one it holds changes nothing"
    )
    verb.add_argument("--branch", help="the pushed branch it works or built on")
    verb.add_argument("--said", help="its latest line")
    verb.add_argument("--due", help="its clock: when it must have returned, said once as DUE if it still runs then, a time or +10m")
    verb.add_argument("--repro", help="the job's pick-up-and-run reproduction file, kept in the drive")
    verb.add_argument("--pr", action="append", default=[], help="a PR it opened or took over")
    verb.add_argument("--part", action="append", default=[], help="another part of the order this job delivers, or of the one order --carries names")
    verb.add_argument(
        "--carries", action="append", default=[], metavar="ORDER", help="another order whose part this job delivers, until it is done"
    )
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
        help="keep a job's ASK in the drive beside its other open questions, refused unless it is in the shape he reads, and set the job waiting",
        description=(
            "Check a job's ASK block: `ASK <the question>`, one to three summary lines (what was found and what is being"
            " decided, the recommendation, what happens meanwhile), `Show me:` and at least one visual under it, or no Show me"
            " when nothing shows what the words cannot (a mermaid flow, a compare or a table for options, a chart for numbers,"
            " an arch diagram, whose JSON parses, the real call in a fence naming its language for a code or API shape, or a"
            " picture on a line of its own as ![caption](/absolute/path.png); never words, JSON, YAML, logs, a terminal"
            " session, command output or a stack trace in a fence), captions of at most 25 words,"
            " two to four `- ` options of at most 25 words, each saying what runs, on what trigger, what it reads and what"
            " reaches whom, then what he gets and what it costs, with exactly one ending (recommended), no mechanism such as"
            " polling, a push or an event named by a bare word on the question or an option, the question, summary and"
            " options at most 150 words in all, in plain words, each thing he"
            " could open linked where it is named, then `Findings:` with the evidence, its `Direct fix:` line naming the"
            " option that is the fix the job's diagnosis points at, recommended unless the summary names it and says why"
            " not, or `none: <why>`. Keep it in the drive in a file of"
            " its own and print its path, which the ask record carries, and add it to the job's open questions, each"
            " open until his answer to its own card. Refuse it with every way it is out of shape, and while a recorded"
            " answer of his may already settle it."
        ),
    )
    verb.add_argument("id", nargs="?", help="the job whose ASK this keeps; none with --check")
    verb.add_argument(
        "file", nargs="?", help="the file holding the ASK block, or - to read it from stdin; default: a remote lane's latest report"
    )
    verb.add_argument(
        "--check",
        metavar="FILE",
        help="only check the ASK block in this file, or - for stdin, and print every fix it needs or ready; keeps nothing and reads no drive, so a remote lane runs it",
    )
    verb.add_argument(
        "--unsettled",
        metavar="WHY",
        help="one string naming every recorded answer of his the refusal listed, by id, and why it does not settle this question",
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
            " Show me left empty is dropped; its options, the recommended one, the drive, and --waits. The question kept"
            " in that file takes the waits id, and one of the job's that held it before, a card this redraws, hands over"
            " its card and leaves the job; a waits id naming no job of the drive only draws the card. Refused when his"
            " words under that card answered it: they are his answer, and a question they leave open goes on a card of"
            " its own. A block kept under older rules redraws as kept, its Show me's visuals never checked again, and"
            " markdown in its question or options, which Slack draws raw, drawn as the words it marks. Name a job first,"
            " `drive job card <j>`, to draw its newest kept ASK, or the one a file after it names, under the waits id its"
            " card already has."
        ),
    )
    verb.add_argument("ask", metavar="JOB|FILE", help="a job, whose newest kept ASK block draws, or the kept ASK block itself, the file an ask record's `ask` attr names")
    verb.add_argument("file", nargs="?", help="after a job, which of its kept ASK blocks draws; default: its newest")
    verb.add_argument("--waits", help="the card's waits id, <drive>:<job>-<the ask record's seq>; after a job, default: the waits id its card already has")
    verb.add_argument(
        "--reworking",
        metavar="WHY",
        help="what is being redone and when it is back: a line atop the card's body says the question is being redone and why, its words and buttons kept",
    )
    verb.set_defaults(run=job_card)
    verb = job.add_parser(
        "report",
        parents=both,
        help="keep a job's whole return in the drive, or print it",
        description=(
            "A job returns one line; the rest of its return, the inputs to what he would be told, one fact a line,"
            " is kept here before it returns, piped in on stdin. With -, keep stdin, set a running job's state and said line"
            " from its first line, and print where; with nothing, print the kept report, or a remote lane's latest one."
        ),
    )
    verb.add_argument("id")
    verb.add_argument("stdin", nargs="?", choices=["-"], help="- to keep the job's whole return from stdin; default: print the kept one")
    verb.set_defaults(run=job_report)
    verb = job.add_parser(
        "brief",
        parents=both,
        help="write a job's brief from his order's words, each ruling's words and the earlier jobs' transcripts, and print where",
        description=(
            "Write the brief reference/jobs.md shapes into the drive and print its path: Order from the words the order"
            " holds, verbatim; Goal from the job; Decisions that bind from each --ruling's recorded answer, its title and"
            " body whole; Transcripts from each earlier job on the order; Scratch, by default a folder of the drive's;"
            " the Drive line; and each line the flags name. Its first line, `Read your brief first: <path>`, is a local"
            " job's whole Agent prompt, which binds the agent to the job, and a remote job launches from it with drive job launch <j>."
        ),
    )
    verb.add_argument("id", help="the job")
    verb.add_argument("--serves", required=True, help='"port of #N", "drive tooling" or "his order"')
    verb.add_argument("--done-when", required=True, help="the check that proves it done")
    verb.add_argument("--where", required=True, help="the repo, base branch, and any branch or PR to adopt")
    verb.add_argument("--runs-as", help="for a pass over a team's data: the service account in its cluster and how it starts")
    verb.add_argument("--handoff", help="a PR event or freed built branch: the PRs or branch, its head, the event line, INSTALL: and PROVE:")
    verb.add_argument("--ruling", action="append", default=[], metavar="ANSWER", help="a cc-notes answer id of his that touches this; its words go in whole")
    verb.add_argument("--plan", help="the plan section this implements, quoted")
    verb.add_argument("--incident", help="an incident job's line, by reference/jobs.md")
    verb.add_argument("--scratch", help="its scratch folder; default one in the drive")
    verb.set_defaults(run=job_brief)
    verb = job.add_parser("launch", parents=both, help="start a remote job's Orca lane on a Sprite, or on this Mac with --local")
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
        help="answer a remote lane's question, or send it the next event",
        description=(
            "Answer a remote lane's question, or send it the next event, once Orca shows its worker alive. When its "
            "worker finished and Orca still holds its terminal, on its Sprite or on this Mac, start the job's next worker "
            "in that terminal; when its worker is dead, relaunch the job on a fresh Sprite, or in a fresh worktree on this "
            "Mac, on the same Run, where work the dead lane had not pushed is not carried over. Either new worker gets a handoff carrying the job's brief, its branch, its last line, "
            "its report, its PRs and this text. When Orca cannot reach the lane's Sprite, or refuses what would deliver "
            "this text, exit 2 with Orca's refusal."
        ),
    )
    verb.add_argument("id")
    verb.add_argument(
        "text",
        type=lambda value: sys.stdin.read() if value == "-" else value,
        help="his answer, or the event the lane acts on next, or - to read it from stdin",
    )
    verb.set_defaults(run=job_resume)
    verb = job.add_parser(
        "replies", parents=both, help="every result and question a remote lane sent, oldest first, with when it sent each"
    )
    verb.add_argument("id")
    verb.set_defaults(run=job_replies)
    verb = job.add_parser("end", parents=both, help="end a done remote lane and free its Sprite, or its terminal and clean worktree on this Mac")
    verb.add_argument("id")
    verb.set_defaults(run=job_end)
    verb = job.add_parser("show", parents=both, help="drive status for these ids")
    verb.add_argument("ids", nargs="+", metavar="ID", help=f"what to read: {ID_FORMS}")
    verb.set_defaults(run=status)

    pr = verbs.add_parser("pr", help="the drive's pull requests").add_subparsers(dest="action", required=True)
    verb = pr.add_parser("add", parents=both)
    verb.add_argument("url", help="the PR's URL")
    verb.add_argument("--job", required=True, help="the job that opened or took it over")
    verb.add_argument("--tap", action="store_true", help="it waits on his tap")
    verb.set_defaults(run=pr_add)
    verb = pr.add_parser("set", parents=[*both, waits_opt])
    verb.add_argument("url", help="the PR's URL")
    verb.add_argument(
        "--tap", action=argparse.BooleanOptionalAction, help="waits on his tap; needs --serves, --design; --no-tap withdraws his open card for it, needs --why"
    )
    verb.add_argument("--why", help="with --no-tap, the reason his withdrawn card shows; with --no-tapped, why the drive's own hold no longer stands")
    verb.add_argument("--serves", metavar="ORDER", help="the order of his whose words ask for this PR; prints them back")
    verb.add_argument(
        "--design",
        type=design_basis,
        metavar="asked:<answer>|none",
        help="his answer or card that settled each design decision the PR makes, or none; --tap needs it",
    )
    exemption = verb.add_mutually_exclusive_group()
    exemption.add_argument("--exempt", choices=state.EXEMPT_CLASSES)
    exemption.add_argument("--no-exempt", action="store_true", help="clear the exemption")
    exemption.add_argument(
        "--port-of", type=int, metavar="PR", help="a pure port of this old-tree PR number: exempt, with no tap card"
    )
    verb.add_argument("--fundamental", help="what changed; clears his tap and any exemption")
    tapped = verb.add_mutually_exclusive_group()
    tapped.add_argument("--tapped", choices=state.TAP_LABELS, help="record a tap he gave elsewhere")
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

    verbs.add_parser("place", parents=both, help="where the next monorepo builder starts").set_defaults(run=place_job)
    verb = verbs.add_parser("bound", parents=both, help="run one command with a lane's Run bound to this terminal, under the drive's binding lock, then bind the drive's own Run, which no lane mails")
    verb.add_argument("lane_run", metavar="run")
    verb.add_argument("command", nargs=argparse.REMAINDER, help="after --, the command and its arguments")
    verb.set_defaults(run=bound)
    verb = verbs.add_parser(
        "answer",
        parents=both,
        help="record an answer or ruling of his against every card still asking him and every PR not yet live, each marked standing, superseded or reopened by it",
        description=(
            "Run for every answer and ruling of his. The first call, with --title, --words and --link and no id, records"
            " it in cc-notes and here, or with an id, --words and --link records one already in cc-notes. It lists every"
            " question card of his still open, every PR waiting on his tap, every"
            " other open PR and every merged PR whose job is still at work (key, kind, job, order, title, link), and each"
            " job waiting on one of their orders, and exits 2. Each later call marks them: --stands for one this answer"
            " leaves as it is, --superseded for one it replaces, which settles its card in place showing his words and a"
            " link to them with no buttons, drops its PR's tap ask, and stops its job, closing an open PR on GitHub, or"
            " folds what the job carries into the live job named, and --reopened for a PR his words change, which settles"
            " its card the same way, drops its tap ask, and names the live job that reworks it; --sent for a waiting job"
            " SendMessage gave his words verbatim, and --untouched for one they leave as it is. --others-stand marks the"
            " cards and PRs left standing only while a few are left. It exits 2, listing the rest, until every one is"
            " marked; the watch says ANSWER every 10 minutes while a card or PR is not."
        ),
    )
    verb.add_argument("id", nargs="?", help="his answer's cc-notes id; leave it out with --title on the first call to record it in cc-notes")
    verb.add_argument("--title", help="the question his answer settles, verbatim, as its cc-notes title; with no id, the first call records it there")
    verb.add_argument("--label", action="append", default=[], help="a cc-notes label beyond scope:durable and drive:<drive>, such as header:<topic> or from:owner")
    verb.add_argument("--words", help="his words, verbatim, which his cc-notes answer keeps and a superseded card shows; the first call needs them")
    verb.add_argument("--link", help="the permalink of his message or of the card he tapped, which a superseded card links; the first call needs it")
    verb.add_argument("--stands", action="extend", nargs="+", default=[], metavar="KEY", help="a card or PR his answer leaves as it is")
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
        help="a PR his answer changes: its card settled in place, its tap ask dropped, and the live job named reworks it to his words",
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
        "heard", parents=both, help="he wrote: record it, and tell the coordinator the orders his catch-up at his DM root covers"
    )
    verb.add_argument("--away", action="store_true", help="he said he is going to sleep or away")
    verb.set_defaults(run=heard)

    verb = verbs.add_parser("status", parents=both, help="where each order, job and PR stands")
    verb.add_argument("ids", nargs="*", help=f"what to read, each {ID_FORMS}; default: every open order")
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
    verb.set_defaults(run=dashboard_command)
    verb = verbs.add_parser("watch", parents=[drive_opt], help="start the drive's watch as its own process unless one runs")
    verb.add_argument("--loop", action="store_true", help="run the watch in the foreground: the process drive watch starts")
    verb.set_defaults(run=watch_events)
    cards = verbs.add_parser("cards", help="his open question cards").add_subparsers(dest="action", required=True)
    verb = cards.add_parser(
        "redraw",
        parents=both,
        help="print the card drive job card prints for each of his open question cards, one JSON line each",
        description=(
            "Redraw his open question cards in one pass: for each open card, print the JSON `drive job card <its ASK file>"
            " --waits <its waits id>` prints, one line each, for say(card=…). Its ASK file is the one a job's question keeps"
            " under its waits id, else the one a question linked to the card keeps when it asks the card's question, else"
            " asks/<waits id>.md rebuilt from the card as posted, which a question of its job then keeps. Each card that does"
            " not redraw, one whose redraw is refused, is named once the rest are printed, and the verb exits 2."
        ),
    )
    verb.add_argument("--waits", action="append", default=[], help="redraw only this open card, by its waits id; repeatable")
    verb.set_defaults(run=cards_redraw)
    card_help = "cc-slack's card_command: reads one request JSON on stdin and prints one JSON reply"
    verbs.add_parser("card", help=card_help, description=card_help).set_defaults(run=card_command)
    verb = verbs.add_parser("mcp", help="serve say and the drive's verbs as MCP tools to a Claude Code session over stdio")
    verb.add_argument("--list", action="store_true", help="print the tools as JSON and serve nothing")
    verb.set_defaults(run=mcp_command)
    verb = verbs.add_parser("tool", help="run one MCP tool on the JSON arguments read from stdin and print its lines")
    verb.add_argument("tool", choices=MCP_TOOLS)
    verb.set_defaults(run=tool_command)

    verb = verbs.add_parser("install")
    verb.add_argument("plugin", metavar="plugin@marketplace")
    verb.add_argument("--sha")
    verb.add_argument("--repo")
    verb.add_argument("--timeout", type=int, default=900)
    verb.set_defaults(run=install_plugin)
    verb = verbs.add_parser("reload", help="type /reload-plugins into both sessions of every drive on this Mac, queued behind a turn still running after 30 seconds")
    verb.set_defaults(run=reload_plugins)

    verb = verbs.add_parser("setup")
    verb.add_argument("--bin-dir")
    verb.set_defaults(run=setup)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.run(args)
    except (DriveError, slack.SlackError) as error:
        print(f"drive: {error}", file=sys.stderr)
        return 2
