import argparse
import json
import os
import re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from drive import github, limits, orca, place, reads, render, settled, slack, state, watch
from drive.state import DriveError

DRIVE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
WAITS_CARD = re.compile(r"https://\S+/archives/\S+/p\d+\S*|[\w.-]+:[\w.-]+")
CLAUDE_BUILDS = Path.home() / ".local" / "share" / "claude" / "versions"
HIDDEN = "not visible in Orca, which hides the worktrees it did not make for this repository"
PLUGIN_JSON = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
PAGES = Path(__file__).resolve().parents[2] / "skills" / "long-running" / "reference"
LAUNCHED = ("dispatch", "task", "terminal", "workspace", "environment", "lane")
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
        watch = data["slack"].setdefault("watch", [])
        watch.extend(channel for channel in dict.fromkeys(args.watch) if channel not in watch)
    seen = orca_place(data["roles"][args.role], run_show)
    text = f"{render.context(data, args.role)}\n\n{args.role}: {render.who(data, args.role)}, {seen}"
    return emit(args, text, {**data, "orca": {args.role: seen}})


def order_add(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    created = state.stamp()
    with state.edit(name) as data:
        order_id = state.next_id(data["orders"], "o")
        order = data["orders"][order_id] = {
            "text": args.words,
            "from": args.source,
            "thread": args.thread,
            "state": "open",
            "due": due_stamp(args.due) if args.due else None,
            "due_said": None,
            "promised": created if args.due else None,
            "next_due": None,
            "next_set": None,
            "kept": None,
            "withdrawn": None,
            "jobs": [],
            "parts": {part: state.new_part() for part in args.parts or [state.whole_order(args.words)]},
            "incident": args.incident,
            "proof": None,
            "note": None,
            "created": created,
            "updated": created,
            "last_line": None,
        }
        if args.due:
            need_thread(data, order_id, order)
    return emit(args, order_id, {"id": order_id, **order})


def need_thread(data: dict, order_id: str, order: dict) -> None:
    if not state.promise_thread(data, order):
        raise DriveError(f"{order_id}: a promised time needs the thread its line goes in; pass --thread <ts>")


def find(records: dict, key: str, label: str) -> dict:
    if key not in records:
        raise DriveError(f"no {label} {key}")
    return records[key]


def his_words(link: str) -> str:
    if not slack.message(link).get("from_user"):
        raise DriveError(f"{link} is not his message; only his own words withdraw a promised line")
    return link


def drive_post(link: str, threads: list[tuple[str, str]]) -> str:
    posted = slack.message(link)
    if not posted.get("from_claude"):
        raise DriveError(f"{link} is not the drive's post")
    if threads and posted.get("thread_ts") not in {thread for _, thread in threads}:
        raise DriveError(f"{link} is in none of the order's threads, its own or its cards': {', '.join(thread for _, thread in threads)}")
    return link


def pick_parts(order_id: str, order: dict, named: list[str]) -> list[str]:
    listed = ", ".join(f'"{part}"' for part in order["parts"])
    if not named:
        if len(order["parts"]) != 1:
            raise DriveError(f"{order_id} has parts {listed}; name the one this covers with --part")
        return list(order["parts"])
    if unknown := [part for part in named if part not in order["parts"]]:
        raise DriveError(f"{order_id} has no part {', '.join(unknown)}; its parts are {listed}")
    return list(dict.fromkeys(named))


def order_set(args: argparse.Namespace) -> int:
    """Change an order; closing one that owes him a promised line reads its thread and refuses until the line is there or he withdrew the time."""
    name = state.resolve(args.drive)
    withdrawn = his_words(args.withdrawn) if args.withdrawn else None
    data = state.load(name)
    closing = args.state in state.CLOSED_ORDER_STATES and not withdrawn
    order = find(data["orders"], args.id, "order")
    if args.state in state.CLOSED_ORDER_STATES:
        need_record_close(data, args.id, args.record)
    destination = order | {"thread": args.thread or order["thread"]}
    posts, unread = slack.order_posts(data, destination) if closing and state.owed(order) else ([], None)
    changes = {"note": args.note, "proof": args.proof, "thread": args.thread, "withdrawn": withdrawn}
    moment = state.now()
    with state.edit(name) as data:
        order = data["orders"][args.id]
        order.update({field: value for field, value in changes.items() if value is not None})
        if args.due:
            need_thread(data, args.id, order)
            state.promise(order, due_stamp(args.due), moment)
        if closing and state.owed(order):
            state.keep(order, posts, announced=False)
            if state.owed(order):
                why = f" ({unread})" if unread else ""
                raise DriveError(
                    f"{args.id} owes him the line promised for {render.when(state.owed_at(order))}, and no drive post is in"
                    f" its thread since {render.when(order['promised'])}{why}: post it there first, or pass"
                    " --withdrawn <his message> when he called it off"
                )
        if args.state:
            order["state"] = args.state
        if args.parts:
            order["parts"] = {part: order["parts"].get(part, state.new_part()) for part in args.parts}
        if args.incident is not None:
            order["incident"] = args.incident
        order["updated"] = state.stamp(moment)
        if args.lined:
            order["last_line"] = order["updated"]
    return emit(args, render.order_line(args.id, order), {"id": args.id, **order})


def need_record_close(data: dict, order_id: str, seq: str | None) -> None:
    if seq is None:
        if state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID")) == "communicator":
            raise DriveError(
                f"{order_id}: a handler closes only an order the report record it posted names in its close attr;"
                " pass --record <that record's seq>"
            )
        return
    try:
        record = json.loads(reads.run(["cci", "show", seq, "--json"], Path.cwd()))
    except reads.ReadError as error:
        raise DriveError(f"record {seq} could not be read: {error}") from None
    closes = record["fields"].get("attrs", {}).get("close", "").split()
    if not closes:
        raise DriveError(f"record {seq} has no close attr, so it closes no order; an orders attr only says what its line is about")
    if order_id not in closes:
        raise DriveError(f"record {seq} does not close {order_id}: its close attr names only {', '.join(closes)}")


def order_posted(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    data = state.load(name)
    if state.session_role(data, os.environ.get("CLAUDE_CODE_SESSION_ID")) != "communicator":
        raise DriveError("only the communicator marks an answer posted, once the answer is in his thread")
    [part] = pick_parts(args.id, find(data["orders"], args.id, "order"), [args.part] if args.part else [])
    drive_post(args.link, slack.order_threads(data, data["orders"][args.id]))
    with state.edit(name) as data:
        data["orders"][args.id]["parts"][part]["posted"] = args.link
    return emit(args, f'{args.id} "{part}": posted {args.link}', {"id": args.id, "part": part, "posted": args.link})


def job_add(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    created = state.stamp()
    with state.edit(name) as data:
        order = find(data["orders"], args.order, "order")
        parts = pick_parts(args.order, order, args.part)
        job_id = state.next_id(data["jobs"], "j")
        job = data["jobs"][job_id] = {
            "order": args.order,
            "kind": args.kind,
            "goal": args.goal,
            "agent": args.agent,
            "session": os.environ.get("CLAUDE_CODE_SESSION_ID"),
            "parts": parts,
            "carries": {},
            "card": None,
            "where": "remote" if args.remote else "local",
            "repo": args.repo,
            "branch": args.branch,
            "worktree": args.worktree,
            "prs": [],
            "waits": [],
            "state": "running",
            "said": None,
            "created": created,
            "updated": created,
        }
        order["jobs"].append(job_id)
        order["updated"] = created
    return emit(args, job_id, {"id": job_id, **job})


def job_set(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    changes = {"agent": args.agent, "state": args.state, "said": args.said, "card": args.card, "branch": args.branch}
    urls = dict.fromkeys(state.pr_url(state.pr_key(url)) for url in args.pr)
    check_waits(name, args.waits)
    with state.edit(name) as data:
        job = find(data["jobs"], args.id, "job")
        job.update({field: value for field, value in changes.items() if value is not None})
        job["waits"] = merged_waits(job, args)
        if args.state == "built" and not (job["branch"] and job["waits"]):
            raise DriveError(
                f"{args.id} built needs --branch <the pushed branch it built> and --waits <each design card it waits on>:"
                " a built job opens no PR until he answers them"
            )
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
        if args.state == "done":
            state.drop_carries(data, args.id)
        if args.agent:
            job["where"] = "local"
            job["session"] = os.environ.get("CLAUDE_CODE_SESSION_ID")
        if args.repro:
            job["repro"] = keep_repro(name, args.id, args.repro)
        job["prs"].extend(url for url in urls if url not in job["prs"])
        job["updated"] = state.stamp()
    return emit(args, render.job_line(args.id, job), {"id": args.id, **job})


def job_ask(args: argparse.Namespace) -> int:
    """Keep a job's ASK in the drive once it is in the shape he reads; with no file, a remote lane's latest report."""
    name = state.resolve(args.drive)
    job = find(state.load(name)["jobs"], args.id, "job")
    text = Path(args.file).expanduser().read_text() if args.file else job.get("report")
    if not text:
        raise DriveError(f"{args.id} sent no report to read an ASK from; pass the file its ASK block is in")
    asked = limits.question(text)
    if refused := settled.refusal("\n".join([asked.title, *asked.summary, *asked.options]), os.getcwd(), args.unsettled, "--unsettled"):
        raise DriveError(refused)
    kept = state.drive_dir(name) / "asks" / f"{args.id}.md"
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(text)
    with state.edit(name) as data:
        data["jobs"][args.id].update(question=str(kept), state="waiting", said=asked.title, unsettled=args.unsettled, updated=state.stamp())
    return emit(args, str(kept), {"id": args.id, "question": str(kept), **vars(asked)})


def job_report(args: argparse.Namespace) -> int:
    """Keep a job's whole return from stdin in the drive, or print the one kept, or a remote lane's latest report."""
    name = state.resolve(args.drive)
    job = find(state.load(name)["jobs"], args.id, "job")
    kept = state.drive_dir(name) / "reports" / f"{args.id}.md"
    if args.stdin:
        whole = sys.stdin.read()
        if not whole.strip():
            raise DriveError(f"stdin held no report for {args.id}; pipe its whole return in: printf '%s\\n' '<line>' ... | drive job report {args.id} -")
        kept.parent.mkdir(exist_ok=True)
        kept.write_text(whole)
        return emit(args, str(kept), {"id": args.id, "report": str(kept)})
    text = kept.read_text() if kept.exists() else job.get("report")
    if not text:
        raise DriveError(f"{args.id} kept no report; its one-line result is all it returned")
    return emit(args, text.rstrip("\n"), {"id": args.id, "report": text})


def keep_repro(name: str, job_id: str, source: str) -> str:
    path = Path(source).expanduser()
    if not path.is_file():
        raise DriveError(f"no reproduction file at {source}")
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
        f"{job_id} has no agent and no lane yet: start it on a Sprite with drive job launch {job_id} <brief>,"
        f" or record the agent you started here with drive job set {job_id} --agent <id>"
    )


def checkout() -> Path:
    root = Path.cwd()
    if not (root / ".orca.json").is_file():
        raise DriveError(f"{root} has no .orca.json; run this from the root of the checkout lanes clone")
    return root


def job_launch(args: argparse.Namespace) -> int:
    name = state.resolve(args.drive)
    with state.edit(name) as data:
        unstarted = find(data["jobs"], args.id, "job")
        if not unstarted.get("agent"):
            unstarted["where"] = "remote"
        job = remote_job(data, args.id)
    if dispatch := job.get("dispatch"):
        raise DriveError(f"{args.id} already started as dispatch {dispatch}")
    root = checkout()
    run = job.get("run") or orca.new_run(f"drive {name} {args.id}: {job['goal']}")
    with state.edit(name) as data:
        data["jobs"][args.id]["run"] = run
    brief = state.drive_dir(name) / "jobs" / f"{args.id}.md"
    brief.parent.mkdir(exist_ok=True)
    brief.write_text(orca.vm_brief(name, args.id, sys.stdin.read() if args.brief == "-" else Path(args.brief).read_text()))
    receipt = orca.launch(name, orca.lane_name(name, args.id), brief, root, run)
    started = {field: receipt[field] for field in LAUNCHED}
    with state.edit(name) as data:
        job = data["jobs"][args.id]
        job.update(started, brief=str(brief), ask=None, report=None, updated=state.stamp())
    return emit(args, f"{args.id} started on Sprite {started['workspace']} as dispatch {started['dispatch']}", started)


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
    """Send the lane its next event, or, when its worker takes no messages, start the job's next worker with the event in its handoff."""
    name = state.resolve(args.drive)
    job = started_job(state.load(name), args.id)
    try:
        end = orca.lane_end(job["dispatch"])
    except orca.OrcaError as error:
        raise DriveError(
            f"{args.id}'s lane cannot be checked, so nothing was sent: {error}; resume it again once Orca reaches its Sprite"
        ) from None
    if end:
        return restart(args, name, job["dispatch"], end)
    how = orca.answer(name, job, args.text)
    with state.edit(name) as data:
        fresh = data["jobs"][args.id]
        if fresh.get("ask") == job.get("ask"):
            fresh.update(ask=None, state="running")
        fresh["updated"] = state.stamp()
    return emit(args, f"{args.id}: {how}", {"id": args.id, "how": how})


def restart(args: argparse.Namespace, name: str, dispatch: str, end: orca.LaneEnd) -> int:
    """Start the job's next worker with a handoff: in the terminal of a worker that finished while its Sprite is up, else on a fresh Sprite on the same Run."""
    root = None if end.worktree else checkout()
    with state.edit(name) as data:
        job = data["jobs"][args.id]
        state.lane_died(job, dispatch, end.why, state.now())
    ended = job["deaths"][-1]
    attempt = f"{args.id}-{len(job['deaths']) + 1}"
    brief = state.drive_dir(name) / "jobs" / f"{attempt}.md"
    taking_over = orca.handoff(orca.brief_of(Path(job["brief"]).read_text()), ended, job, args.text, end)
    brief.parent.mkdir(exist_ok=True)
    brief.write_text(orca.vm_brief(name, args.id, taking_over))
    lane = orca.lane_name(name, attempt)
    if end.worktree:
        receipt = orca.start_after(name, lane, brief, job, end.worktree)
    else:
        receipt = orca.launch(name, lane, brief, root, job["run"])
    started = {field: receipt[field] for field in LAUNCHED}
    with state.edit(name) as data:
        data["jobs"][args.id].update(
            started, handoff=str(brief), ask=None, report=None, reads={}, state="running", updated=state.stamp()
        )
    carrying = "a handoff carrying its brief, its branch, its last line, its report, its PRs and the message"
    if end.worktree:
        line = (
            f"{args.id}: its lane on Sprite {ended['workspace']} had finished ({end.why}), so the message was not sent to "
            f"it; started the job's next worker in its terminal on the same Sprite as dispatch {started['dispatch']} with "
            f"{carrying}"
        )
    else:
        line = (
            f"{args.id}: its lane on Sprite {ended['workspace']} was dead ({end.why}), so the message was not sent to it; "
            f"relaunched the job on Sprite {started['workspace']} as dispatch {started['dispatch']} with {carrying}; work "
            f"not pushed from {ended['workspace']} is not carried over"
        )
    how = "started on its Sprite" if end.worktree else "relaunched"
    return emit(args, line, {"id": args.id, "how": how, "ended": ended, **started})


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
        data["jobs"][args.id].update(state="done", ask=None, updated=state.stamp())
        state.drop_carries(data, args.id)
    return emit(args, reclaimed or f"{args.id} ended", {"id": args.id, "reclaim": reclaimed})


def pr_add(args: argparse.Namespace) -> int:
    from drive import view

    name = state.resolve(args.drive)
    key = state.pr_key(args.url)
    url = state.pr_url(key)
    with state.edit(name) as data:
        job = find(data["jobs"], args.job, "job")
        cards = [wait for wait in view.holding(data, job["waits"], view.live_cards(name)) if wait not in data["jobs"]]
        if cards and key not in data["prs"]:
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
    if args.tap is False and not args.why:
        raise DriveError("--no-tap needs --why <reason>: his card for it, if one is open, is withdrawn showing that reason")
    check_waits(name, args.waits)
    if args.tap or args.serves or args.design:
        refuse_an_undrawable_tap(key, args)
    served = ""
    with state.edit(name) as data:
        pr = find(data["prs"], key, "drive PR")
        if args.serves:
            served = f"\nserves {args.serves}, his words: {find(data['orders'], args.serves, 'order')['text']}"
        set_pr(pr, args)
    withdrawn: list[str] = []
    if why := withdrawal(args, view.holding(data, args.waits, view.live_cards(name))):
        try:
            withdrawn = slack.withdraw_pr_card(pr["url"], why)
        except slack.SlackError as error:
            raise DriveError(f"{key}: withdrawing its open card failed, so his Approve still shows: {error}; run this again") from None
        view.refresh_cards(name)
    line = render.pr_line(key, pr) + served + "".join(f"\nwithdrew his card {card}: {why}" for card in withdrawn)
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

    jobs = state.load(name)["jobs"]
    cards = [wait for wait in waits if wait not in jobs]
    found = view.live_cards(name)
    if not all(view.card_open(wait, found) for wait in cards):
        view.refresh_cards(name)
        found = view.live_cards(name)
    for wait in cards:
        if not (WAITS_CARD.fullmatch(wait) and view.card_open(wait, found)):
            raise DriveError(f"--waits {wait}: name a job of this drive, or the Slack link or waits id of a card of his still open")


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
    if reason := card.waiting(known, members) or card.tap_refusal(known, members):
        raise DriveError(f"{key}: {reason}")


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
        state.append_event(name, "catch-up", thread=args.thread, orders=" ".join(news))
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


def only(data: dict, ids: list[str]) -> None:
    """Narrow data, never saved, to the orders the ids name, directly or through one of their jobs or PRs."""
    keys = {state.pr_key(key) if "#" in key or "/pull/" in key else key for key in ids}
    if unknown := keys - set(data["orders"]) - set(data["jobs"]) - set(data["prs"]):
        raise DriveError(f"no order, job or PR {', '.join(sorted(unknown))}")
    orders = {key if key in data["orders"] else (data["jobs"].get(key) or data["prs"][key])["order"] for key in keys}
    data["orders"] = {key: order for key, order in data["orders"].items() if key in orders}
    jobs = {job_id for order in data["orders"].values() for job_id in order["jobs"]}
    data["jobs"] = {key: job for key, job in data["jobs"].items() if key in jobs or job["order"] in orders}
    data["prs"] = {key: pr for key, pr in data["prs"].items() if pr["order"] in orders}


def status(args: argparse.Namespace) -> int:
    data = state.load(state.resolve(args.drive))
    if args.ids:
        only(data, args.ids)
    pr_fields = overlay_live(data)
    seen = {role: with_build(orca_place(held, name_show), held) for role, held in data["roles"].items()}
    return emit(args, render.status_text(data, pr_fields, seen), render.status_data(data, pr_fields, seen))


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
    sys.stdout.write((PAGES / f"{args.page}.md").read_text())
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
    from drive import watch

    return watch.main(args.drive)


def card_command(args: argparse.Namespace) -> int:
    from drive import card

    return card.main(sys.stdin, sys.stdout)


def install_plugin(args: argparse.Namespace) -> int:
    from drive import install

    return install.main(args.plugin, args.sha, args.repo, args.timeout)


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
        metavar="CARD|JOB",
        help=(
            "a design card of his still open, by its Slack link or waits id, or a job, it waits on; repeat for each."
            " The watch drops each as he answers it or the job ends, and says FREE once when the last is gone"
        ),
    )
    waits_opt.add_argument("--no-waits", action="store_true", help="drop every wait it carries before adding any --waits")

    root = argparse.ArgumentParser(prog="drive", description="State and events of a long-running drive.")
    root.add_argument("--version", action="version", version=f"drive {json.loads(PLUGIN_JSON.read_text())['version']}")
    verbs = root.add_subparsers(dest="verb", required=True)

    verb = verbs.add_parser("start", parents=[json_opt], help="create the drive or bind one of its two roles to this session")
    verb.add_argument("name")
    verb.add_argument(
        "--role", required=True, choices=state.ROLES, help="communicator talks to him; coordinator runs orders and jobs"
    )
    verb.add_argument("--channel", help="his DM channel id")
    verb.add_argument(
        "--watch", action="append", default=[], metavar="CHANNEL", help="another channel to hold, kept across restarts"
    )
    verb.set_defaults(run=start)

    order = verbs.add_parser("order", help="his orders").add_subparsers(dest="action", required=True)
    verb = order.add_parser("add", parents=both)
    verb.add_argument("words")
    verb.add_argument("--from", dest="source", default="terminal")
    verb.add_argument("--thread")
    verb.add_argument("--due", help="the time his ack promised a line by; the watch says DUE from then until a line is in the thread")
    verb.add_argument("--parts", nargs="+", metavar="PART", help="each deliverable his words name; default: one, the whole order")
    verb.add_argument("--incident", action="store_true", help="an incident order; the dashboard's top strip shows it until it closes")
    verb.set_defaults(run=order_add)
    verb = order.add_parser("set", parents=both)
    verb.add_argument("id")
    verb.add_argument("--state", choices=state.ORDER_STATES, help="done or dropped refuses while a promised line is not posted")
    verb.add_argument("--due", help="when he hears next, owed from the next line in the order's thread")
    verb.add_argument("--note")
    verb.add_argument("--proof")
    verb.add_argument("--thread")
    verb.add_argument("--lined", action="store_true")
    verb.add_argument(
        "--withdrawn", metavar="LINK", help="his Slack message calling off the promised time; the order then owes no line for it"
    )
    verb.add_argument("--parts", nargs="+", metavar="PART", help="the order's parts, replacing its list")
    verb.add_argument("--incident", action=argparse.BooleanOptionalAction, help="mark or unmark it as an incident order")
    verb.add_argument(
        "--record",
        metavar="SEQ",
        help="the report record a handler posted; a close from the communicator's session needs it and closes only an order its close attr names",
    )
    verb.set_defaults(run=order_set)
    verb = order.add_parser("posted", parents=both, help="the communicator posted the answer a part owes him")
    verb.add_argument("id")
    verb.add_argument("link", help="the Slack link of the posted answer")
    verb.add_argument("--part", help="default: the order's only part")
    verb.set_defaults(run=order_posted)

    job = verbs.add_parser("job", help="background agents").add_subparsers(dest="action", required=True)
    verb = job.add_parser("add", parents=both)
    verb.add_argument("kind", choices=state.JOB_KINDS)
    verb.add_argument("--order", required=True)
    verb.add_argument("--goal", required=True)
    verb.add_argument("--agent")
    verb.add_argument("--repo")
    verb.add_argument("--branch")
    verb.add_argument("--worktree")
    verb.add_argument("--remote", action="store_true", help="a builder that starts as a remote lane on a Sprite")
    verb.add_argument("--part", action="append", default=[], help="a part of the order this job delivers; default: its only part")
    verb.set_defaults(run=job_add)
    verb = job.add_parser("set", parents=[*both, waits_opt])
    verb.add_argument("id")
    verb.add_argument("--agent")
    verb.add_argument(
        "--state", choices=state.JOB_STATES, help="built: it built the recommended design on --branch and opens no PR until each --waits card is answered"
    )
    verb.add_argument("--branch", help="the pushed branch it works or built on")
    verb.add_argument("--said")
    verb.add_argument("--repro", help="the job's pick-up-and-run reproduction file, kept in the drive")
    verb.add_argument("--pr", action="append", default=[])
    verb.add_argument("--part", action="append", default=[], help="another part of the order this job delivers, or of the one order --carries names")
    verb.add_argument(
        "--carries", action="append", default=[], metavar="ORDER", help="another order whose part this job delivers, until it is done"
    )
    verb.add_argument("--card", help="the Slack link of the question card that carries this job's parts until he answers it")
    verb.set_defaults(run=job_set)
    verb = job.add_parser(
        "ask",
        parents=both,
        help="keep a job's ASK in the drive, refused unless it is in the shape he reads, and set the job waiting",
        description=(
            "Check a job's ASK block: `ASK <the question>`, one to three summary lines (what was found and what is being"
            " decided, the recommendation, what happens meanwhile), two to four `- ` options of at most 25 words with"
            " exactly one ending (recommended), at most 150 words in all, in plain words, then `Findings:` with the"
            " evidence. Keep it in the drive and print its path, which the ask record carries; refuse it with every way"
            " it is out of shape, and while a recorded answer of his may already settle it."
        ),
    )
    verb.add_argument("id")
    verb.add_argument("file", nargs="?", help="the file holding the ASK block; default: a remote lane's latest report")
    verb.add_argument(
        "--unsettled",
        metavar="WHY",
        help="one string naming every recorded answer of his the refusal listed, by id, and why it does not settle this question",
    )
    verb.set_defaults(run=job_ask)
    verb = job.add_parser(
        "report",
        parents=both,
        help="keep a job's whole return in the drive, or print it",
        description=(
            "A job returns one line; the rest of its return, the inputs to what he would be told, one fact a line,"
            " is kept here before it returns, piped in on stdin. With -, keep stdin and print where; with nothing, print"
            " the kept report, or a remote lane's latest one."
        ),
    )
    verb.add_argument("id")
    verb.add_argument("stdin", nargs="?", choices=["-"], help="- to keep the job's whole return from stdin; default: print the kept one")
    verb.set_defaults(run=job_report)
    verb = job.add_parser("launch", parents=both, help="start a remote job's lane on a Sprite")
    verb.add_argument("id")
    verb.add_argument(
        "brief", help="the file holding the job's brief, the same one a background agent gets, or - to read it from stdin"
    )
    verb.set_defaults(run=job_launch)
    verb = job.add_parser(
        "resume",
        parents=both,
        help="answer a remote lane's question, or send it the next event",
        description=(
            "Answer a remote lane's question, or send it the next event, once Orca shows its worker alive. When its "
            "worker finished and its Sprite is still up, start the job's next worker in its terminal there; when its "
            "worker is dead, relaunch the job on a fresh Sprite on the same Run, where work the dead lane had not pushed "
            "is not carried over. Either new worker gets a handoff carrying the job's brief, its branch, its last line, "
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
    verb = job.add_parser("end", parents=both, help="end a done remote lane and free its Sprite")
    verb.add_argument("id")
    verb.set_defaults(run=job_end)

    pr = verbs.add_parser("pr", help="the drive's pull requests").add_subparsers(dest="action", required=True)
    verb = pr.add_parser("add", parents=both)
    verb.add_argument("url")
    verb.add_argument("--job", required=True)
    verb.add_argument("--tap", action="store_true")
    verb.set_defaults(run=pr_add)
    verb = pr.add_parser("set", parents=[*both, waits_opt])
    verb.add_argument("url")
    verb.add_argument(
        "--tap", action=argparse.BooleanOptionalAction, help="waits on his tap; needs --serves, --design; --no-tap withdraws his open card for it, needs --why"
    )
    verb.add_argument("--why", help="with --no-tap, the reason his withdrawn card shows")
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
    verb.add_argument("--tapped", choices=state.TAP_LABELS, help="record a tap he gave elsewhere")
    verb.add_argument("--tap-source", default="recorded", help="where that tap came from")
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
        "heard", parents=both, help="he wrote: record it, and tell the coordinator the orders his catch-up covers"
    )
    verb.add_argument("--thread", required=True, help="the thread his message is in, where his catch-up goes")
    verb.add_argument("--away", action="store_true", help="he said he is going to sleep or away")
    verb.set_defaults(run=heard)

    verb = verbs.add_parser("status", parents=both)
    verb.add_argument("ids", nargs="*", help="orders, jobs or PRs to read; default: every open order")
    verb.set_defaults(run=status)
    verb = verbs.add_parser("manual", help="print one page of the long-running skill")
    verb.add_argument("page", choices=sorted(path.stem for path in PAGES.glob("*.md")))
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
    verbs.add_parser("watch", parents=[drive_opt]).set_defaults(run=watch_events)
    verbs.add_parser("card").set_defaults(run=card_command)

    verb = verbs.add_parser("install")
    verb.add_argument("plugin", metavar="plugin@marketplace")
    verb.add_argument("--sha")
    verb.add_argument("--repo")
    verb.add_argument("--timeout", type=int, default=900)
    verb.set_defaults(run=install_plugin)

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
