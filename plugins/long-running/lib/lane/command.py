"""The `lane` command: the owner's verbs, read from LANE_OWNER and LANE_TOKEN in its session, and the verbs that open, message, park, check and supervise lanes."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from lane import owner as owners
from lane import ack, audit, page, queue, store, supervisor, verbs, voice

STOP_BLOCKS = 3
PUSH = re.compile(r"\b(git push|jj git push|ccx vcs ship|gt submit|ccx vcs stack (submit|rebase))\b")
NO_PUSH = re.compile(r"--(no-push|dry-run)\b")
NOT_QUEUED = ("open", "notSubmitted", "merged", "closed", "NOT_INDEXED", "failed", "cancelled")


def where(args: argparse.Namespace) -> tuple[str, str]:
    owned = os.environ.get("LANE_OWNER", "")
    drive, _, lane_id = owned.partition("/")
    drive = args.drive or drive
    lane_id = getattr(args, "lane", None) or lane_id
    if not drive or not lane_id:
        raise store.Refused("name the lane: --drive and --lane, or run inside its owner session")
    return drive, lane_id


def token() -> str | None:
    return os.environ.get("LANE_TOKEN") or None


def owner_token() -> str:
    if not (held := token()):
        raise store.Refused("only the lane's owner session ends its turn")
    return held


def pr_ref(text: str) -> tuple[str, int]:
    repo, _, number = text.partition("#")
    if "/" not in repo or not number.isdigit():
        raise store.Refused(f"a PR is <owner/repo>#<number>, not {text!r}")
    return repo, int(number)


def show(lane: dict) -> str:
    return json.dumps(lane, indent=1, ensure_ascii=False)


def stop_hook(drive: str, lane_id: str) -> int:
    """Block an owner's stop while its lane still runs under its token, telling it the one way out; an owner whose own background agents or tasks still run may stop, since their return resumes it and its lease stays held only while they run; after STOP_BLOCKS blocks let it stop, and its lapsed lease restarts the lane."""
    json.load(sys.stdin)
    held = token()
    lane = store.load(drive, lane_id)
    if lane["state"] != store.RUNNING or (lane.get("owner") or {}).get("token") != held:
        return 0
    if owners.in_flight(lane["owner"]):
        return 0
    blocks = store.update(drive, lane_id, lambda lane: lane["owner"].__setitem__("stop_blocks", lane["owner"].get("stop_blocks", 0) + 1) or lane["owner"]["stop_blocks"])
    if blocks > STOP_BLOCKS:
        return 0
    print(json.dumps({"decision": "block", "reason": "Your lane is still running. Answer any unanswered ask of his with `lane say`, then end your turn only through `lane end-turn --on <event> --until <bound> --why \"...\"`; with nothing else to wait for, `--on his_reply`."}))
    return 0


def bash_hook() -> int:
    """Refuse an owner's or its builders' shell command calling the old drive's `drive` verbs, which no lane uses: landing is lane-land, posting is lane say; and a push to a branch whose PR the merge queue holds, by queued_push."""
    payload = json.load(sys.stdin)
    command = (payload.get("tool_input") or {}).get("command") or ""
    if re.search(owners.OLD_DRIVE_CALL, command):
        print("The old drive's `drive` verbs are gone and never used in a lane: land through lane-land, post through `lane say`, and write your plan with `lane next`.", file=sys.stderr)
        return 2
    if refused := queued_push(command, Path(payload.get("cwd") or os.getcwd())):
        print(refused, file=sys.stderr)
        return 2
    return 0


def queued_push(command: str, cwd: Path) -> str | None:
    """Why a push from cwd must wait: its branch's PR sits in the merge queue, and a new head ejects it, as #57687's did; None for any other command or branch, and when the PR or its queue state cannot be read, since a gate that cannot read lets the push through."""
    if not PUSH.search(command) or NO_PUSH.search(command):
        return None
    from drive import providers

    branch = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=False, timeout=10).stdout.strip()
    number = subprocess.run(["gh", "pr", "view", branch, "--json", "number", "-q", ".number"], cwd=cwd, capture_output=True, text=True, check=False, timeout=30).stdout.strip()
    drive = os.environ.get("LANE_OWNER", "").partition("/")[0]
    if not branch or branch == "HEAD" or not number.isdigit() or not drive or not (checkout := store.config(drive)["checkout"]):
        return None
    script = Path(checkout) / providers.at(Path(checkout)).repo.queue().script
    status = subprocess.run([str(script), "--status", number], cwd=checkout, capture_output=True, text=True, check=False, timeout=60).stdout.split()
    if len(status) < 2 or status[1] in NOT_QUEUED:
        return None
    return f"#{number} is in the merge queue (Trunk {status[1]}), and a push to {branch} gives it a new head, which ejects it. Wait for it to merge with `lane end-turn --on pr_merged:<owner/repo>#{number}`, or take it out of the queue first with `{script} --dequeue {number}` and then push."


def note_hook(drive: str, lane_id: str) -> int:
    """Show a running owner, beside its own next tool call and never a builder's, each word of his to the lane not yet answered, whole and verbatim, at every call until it replies with `lane say`, and the handover set while it runs, once; only while it still holds the lane."""
    if json.load(sys.stdin).get("agent_id"):
        return 0
    path = store.note_path(drive, lane_id)
    lane = store.load(drive, lane_id)
    if (lane.get("owner") or {}).get("token") != token():
        return 0
    shown = []
    if waiting := store.unreplied(lane):
        said = "\n\n".join(f"[{ask['at']}, ts {ask['ts']}{', ' + ask['link'] if ask.get('link') else ''}]\n{ask['words']}" for ask in waiting)
        shown.append(f"He wrote to lane {lane_id} while you run, and it is not answered yet. Reply now with `lane say \"<your answer>\" --re <its ts>`, before your next step; this shows at every tool call until you do:\n\n{said}")
    if path.is_file():
        shown.append(f"Note for lane {lane_id}, set while you run. Read it now; any words of his in it are owed an answer with `lane say` before your next step:\n{path.read_text()}")
        path.unlink()
    if shown:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n\n".join(shown)}}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lane")
    parser.add_argument("--drive")
    verbs_parser = parser.add_subparsers(dest="verb", required=True)

    opening = verbs_parser.add_parser("open", help="open a lane and start its owner in the same call")
    opening.add_argument("lane")
    opening.add_argument("--title", required=True)
    opening.add_argument("--words")
    opening.add_argument("--channel")
    opening.add_argument("--thread")
    opening.add_argument("--link")
    opening.add_argument("--ts")
    opening.add_argument("--parks")
    opening.add_argument("--park-words")
    opening.add_argument("--pr", action="append", default=[])
    opening.add_argument("--worktree", required=True)
    opening.add_argument("--next")
    opening.add_argument("--incident-link")
    opening.add_argument("--monitor", help="the Datadog monitor whose alert says the incident still hurts users")

    messaging = verbs_parser.add_parser("message", help="his message in a lane's channel, which wakes its owner")
    messaging.add_argument("lane")
    messaging.add_argument("--words", required=True)
    messaging.add_argument("--link")
    messaging.add_argument("--ts")

    hearing = verbs_parser.add_parser("heard", help="his message or tap in a lane's channel, pushed by cc-slack's heard_command, which finds the lane by its channel")
    hearing.add_argument("--channel", required=True)
    hearing.add_argument("--thread")
    hearing.add_argument("--ts", required=True)
    hearing.add_argument("--words", required=True)
    hearing.add_argument("--link")

    parking = verbs_parser.add_parser("park")
    parking.add_argument("lane")
    parking.add_argument("--words", required=True)
    parking.add_argument("--link")
    ruled = verbs_parser.add_parser("ruling", help="his answer, verbatim: on the lane it binds, waking its owner, or with no lane for every lane")
    ruled.add_argument("lane", nargs="?")
    ruled.add_argument("--words", help="his words, quoted as he typed them; with --link, the part of his message there that rules, all of it when left out")
    ruled.add_argument("--link", help="his message; its text is read from Slack")
    ruled.add_argument("--binds", required=True, help="what it binds, a scope, never a requirement of its own")
    ruled.add_argument("--relayed-by", help="who relayed his words, for a ruling with no link")
    ruled.add_argument("--reading", help="what it means, an agent's reading kept apart from his words")
    verbs_parser.add_parser("unpark").add_argument("lane")
    defecting = verbs_parser.add_parser("defect", help="the auditor's verb: file one defect of the system as a fix-the-system item for the code's owner, never for him")
    defecting.add_argument("--what", required=True)
    defecting.add_argument("--evidence", required=True, help="a permalink or file:line")
    defecting.add_argument("--mechanism", required=True, help="the class of mechanism that let it through")
    defecting.add_argument("--owner", required=True, help="the lane that owns that code")
    fixing = verbs_parser.add_parser("defect-fixed", help="close a defect the auditor filed, naming the commit that fixed it")
    fixing.add_argument("defect")
    fixing.add_argument("--commit", required=True)
    missing = verbs_parser.add_parser("audit-miss", help="a defect he found that the audits missed, kept as a rubric line every later audit checks")
    missing.add_argument("line")
    carrying = verbs_parser.add_parser("carry", help="a model's judgement of whether his Approve carries to a PR's new head")
    carrying.add_argument("pr")
    carrying.add_argument("--head", required=True)
    judged = carrying.add_mutually_exclusive_group(required=True)
    judged.add_argument("--compatible")
    judged.add_argument("--incompatible")
    verbs_parser.add_parser("github-events", help="handle GitHub's deliveries since the last, one process per drive at a time")
    working = verbs_parser.add_parser("work", help="the work queue: add an item, pull and take items as a worker, finish one, or list them")
    work_verbs = working.add_subparsers(dest="work_verb", required=True)
    work_add = work_verbs.add_parser("add")
    work_add.add_argument("kind")
    work_add.add_argument("subject")
    work_add.add_argument("words")
    work_add.add_argument("--lane")
    work_add.add_argument("--serves", type=int)
    work_add.add_argument("--head")
    work_add.add_argument("--touches", nargs="*", default=[])
    work_add.add_argument("--runs-on", default="local")
    work_verbs.add_parser("pull")
    work_take = work_verbs.add_parser("take")
    work_take.add_argument("ids", nargs="+")
    work_take.add_argument("--worker", default=os.environ.get("LANE_WORKER"))
    work_done = work_verbs.add_parser("done")
    work_done.add_argument("id")
    outcome = work_done.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--result")
    outcome.add_argument("--failed")
    work_verbs.add_parser("list")
    homing = verbs_parser.add_parser("home", help="move a lane's home to its project channel, out of his DM")
    homing.add_argument("lane")
    homing.add_argument("--channel", required=True)
    homing.add_argument("--thread")
    blocking = verbs_parser.add_parser("block", help="a refusal or blocker, written on the lane that waits on it, which wakes it; never a record to a lane's name")
    blocking.add_argument("lane")
    blocking.add_argument("--what", required=True)
    blocking.add_argument("--by", required=True, help="who or what refused or blocks it")
    handing = verbs_parser.add_parser("handover", help="add to the note every owner of the lane reads at its start, from a file, or clear it; each entry is stamped with its write time, so never type a time for something done, only for a bound still ahead")
    handing.add_argument("--lane")
    handing.add_argument("--file")
    handing.add_argument("--clear", action="store_true")
    handing.add_argument("--from", dest="writer", help="who writes this entry")
    waking = verbs_parser.add_parser("wake", help="fire a waiting lane's wait now, for a wait set on a fact that cannot fire")
    waking.add_argument("lane")
    waking.add_argument("--why", required=True)
    adopting = verbs_parser.add_parser("adopt", help="write lane files of the first shape, such as the rebuild's, into the drive in the agreed shape")
    adopting.add_argument("paths", nargs="+")
    adopting.add_argument("--rulings-only", action="store_true", help="replace only each lane's rulings and the shared ones, keeping the rest of each lane")
    placing = verbs_parser.add_parser("worktree", help="set the checkout a lane's owner starts in")
    placing.add_argument("lane")
    placing.add_argument("path")
    pinning = verbs_parser.add_parser("pin")
    pinning.add_argument("lane")
    pinning.add_argument("--off", action="store_true")

    ending = verbs_parser.add_parser("end-turn", help="the only exit from running: write the wait and free the slot")
    ending.add_argument("--lane")
    ending.add_argument("--on", action="append", default=[])
    ending.add_argument("--until", required=True)
    ending.add_argument("--why", required=True)
    ending.add_argument("--next")

    said = verbs_parser.add_parser("say", help="a line to him; --next with --by writes the promise as the lane's bound")
    said.add_argument("text", nargs="?")
    said.add_argument("--picture", action="append", help="a PNG or other file to attach, such as the visual a post owes him")
    said.add_argument("--status", help="a status line for the loader under his thread in place of a post, cleared when your answer posts")
    said.add_argument("--lane")
    said.add_argument("--next")
    said.add_argument("--by")
    said.add_argument("--answers", action="append", help="the ts of an ask this line answers; every unanswered ask when left out")
    said.add_argument("--options", action="append", help="a button label, one per option, recommended first, at most 5 of up to 75 characters: the line goes out as his question and his tap comes back as his words")
    said.add_argument("--about", action="append", help="what else the post is about, as release:<ref> or card:<ts>, so it follows it")
    said.add_argument("--edit", help="the ts of your earlier post this line rewrites in place, such as one a stale note names")
    said.add_argument("--re", help="the ts of the ask of his this line is about, so it goes in that ask's thread; the one open ask when only one is, else the channel's root")

    logging = verbs_parser.add_parser("log")
    logging.add_argument("text")
    logging.add_argument("--lane")
    planning = verbs_parser.add_parser("next", help="write the lane's plan, and what it says to him of it")
    planning.add_argument("text", help="the plan, in your own working terms")
    planning.add_argument("--says", required=True, help="one plain sentence a person would say of what happens next and what it waits for, PRs named with their words")
    serving = planning.add_mutually_exclusive_group()
    serving.add_argument("--serves", type=int, help="the unmet done check this step moves toward, by its number")
    serving.add_argument("--serves-none", help="why this step serves no done check")
    planning.add_argument("--lane")
    checking = verbs_parser.add_parser("done-check", help="the lane's done checks: in his words, things a person could run to see its work done")
    check_verbs = checking.add_subparsers(dest="check_verb", required=True)
    check_add = check_verbs.add_parser("add")
    check_add.add_argument("words")
    check_add.add_argument("--lane")
    check_reword = check_verbs.add_parser("reword")
    check_reword.add_argument("number", type=int)
    check_reword.add_argument("words")
    check_reword.add_argument("--lane")
    check_drop = check_verbs.add_parser("drop")
    check_drop.add_argument("number", type=int)
    check_drop.add_argument("--lane")
    for name in ("met", "unmet"):
        marked = check_verbs.add_parser(name)
        marked.add_argument("number", type=int)
        marked.add_argument("--how", required=True, help="how it was seen met, or why it is unmet again")
        marked.add_argument("--lane")
    verbs_parser.add_parser("asks").add_argument("--lane")
    finished = verbs_parser.add_parser("done", help="mark one of his asks done, by its ts")
    finished.add_argument("ts")
    finished.add_argument("--lane")
    adding = verbs_parser.add_parser("pr")
    adding.add_argument("action", choices=["add"])
    adding.add_argument("ref")
    adding.add_argument("--words")
    adding.add_argument("--lane")
    granting = verbs_parser.add_parser("queue", help="ask the priority queue for a release start")
    granting.add_argument("action", choices=["take", "renew", "release"])
    granting.add_argument("resource", choices=list(queue.CAPACITY))
    granting.add_argument("--holder")
    granting.add_argument("--ttl", type=int, default=30)
    granting.add_argument("--lane")
    for label in ("approve", "hold"):
        tapping = verbs_parser.add_parser(label, help=f"record his {label.title()} on a lane PR at a head, as his Slack card or dashboard tap does")
        tapping.add_argument("ref", help="<owner/repo>#<n>")
        tapping.add_argument("--head", required=True)
        tapping.add_argument("--source", default="recorded by hand")
    incident = verbs_parser.add_parser("incident")
    incident.add_argument("--link")
    incident.add_argument("--monitor")
    incident.add_argument("--fix-live", help="the proof the fix is live: a permalink or release link, which ends its first rank")
    incident.add_argument("--lane")

    verbs_parser.add_parser("show").add_argument("--lane")
    verbs_parser.add_parser("list")
    verbs_parser.add_parser("check")
    verbs_parser.add_parser("pass")
    verbs_parser.add_parser("supervise")
    verbs_parser.add_parser("install", help="keep the drive's supervisor running under launchd")
    verbs_parser.add_parser("preflight", help="render every card and post the installed code would send now, on a copy of the lanes, through the voice in dry run; fail on any refusal or hold")
    verbs_parser.add_parser("settle", help="after an install, read the supervisor for two minutes and fail when it crash-loops").add_argument("--seconds", type=int, default=120)
    paging = verbs_parser.add_parser("page", help="serve his page of the lanes, or with --install keep it served under launchd")
    paging.add_argument("--port", type=int, required=True)
    paging.add_argument("--install", action="store_true")
    hooking = verbs_parser.add_parser("hook")
    hooking.add_argument("event", choices=["stop", "note", "bash"])

    args = parser.parse_args(argv)
    try:
        return run(args)
    except store.Refused as refused:
        print(f"lane: {refused}", file=sys.stderr)
        return 1


def run(args: argparse.Namespace) -> int:
    match args.verb:
        case "open":
            drive = args.drive or os.environ.get("LANE_OWNER", "").partition("/")[0]
            if not drive:
                raise store.Refused("name the drive with --drive")
            incident = {"link": args.incident_link, "monitor": args.monitor, "fix_live": None} if args.incident_link or args.monitor else None
            lane = verbs.open_lane(drive, args.lane, args.title, args.words, args.channel, args.thread, args.link, args.ts, args.parks, args.park_words, [pr_ref(ref) for ref in args.pr], args.worktree, args.next, incident)
            print(f"opened {lane['id']}: {lane['state']}, owner {(lane.get('owner') or {}).get('session') or 'none'}")
        case "message":
            drive, lane_id = where(args)
            print(f"{verbs.message(drive, lane_id, args.words, args.link, args.ts)} new")
        case "heard":
            print(voice.heard(args.channel, args.thread, args.words, args.link, args.ts))
        case "park":
            verbs.park(*where(args), args.words, args.link)
        case "ruling":
            drive = args.drive or os.environ.get("LANE_OWNER", "").partition("/")[0]
            verbs.ruling(drive, args.lane, args.words, args.link, args.binds, args.relayed_by, args.reading)
        case "adopt":
            print("\n".join(verbs.adopt(args.drive, args.paths, args.rulings_only)))
        case "handover":
            drive, lane_id = where(args)
            if args.clear == bool(args.file):
                raise store.Refused("give --file or --clear")
            verbs.handover(drive, lane_id, None if args.clear else Path(args.file).read_text(), args.writer)
        case "wake":
            verbs.wake(*where(args), args.why)
        case "worktree":
            verbs.set_worktree(*where(args), args.path)
        case "defect":
            drive = args.drive or os.environ.get("LANE_OWNER", "").partition("/")[0]
            print(audit.defect(drive, args.what, args.evidence, args.mechanism, args.owner))
        case "defect-fixed":
            drive = args.drive or os.environ.get("LANE_OWNER", "").partition("/")[0]
            audit.fixed(drive, args.defect, args.commit)
        case "audit-miss":
            drive = args.drive or os.environ.get("LANE_OWNER", "").partition("/")[0]
            audit.missed(drive, args.line)
        case "github-events":
            from lane import cli as lane_land
            from lane import events

            with lane_land.exclusive(store.drive_dir(args.drive) / events.GITHUB_LOCK) as mine:
                if mine:
                    for line in events.github(args.drive):
                        print(f"{store.stamp()} {line}", flush=True)
        case "carry":
            drive = args.drive or os.environ.get("LANE_OWNER", "").partition("/")[0]
            repo, number = pr_ref(args.pr)
            print(" ".join(verbs.carry(drive, repo, number, args.head, args.compatible is not None, args.compatible or args.incompatible)))
        case "work":
            from lane import work

            drive = args.drive or os.environ.get("LANE_OWNER", "").partition("/")[0]
            if not drive:
                raise store.Refused("name the drive: --drive")
            match args.work_verb:
                case "add":
                    drive, lane_id = where(args)
                    print(work.add(drive, args.kind, lane_id, args.subject, args.words, args.serves, args.head, args.touches, args.runs_on)["id"])
                case "pull":
                    print(json.dumps(work.pull(drive), indent=1))
                case "take":
                    if not args.worker:
                        raise store.Refused("name the worker: --worker, or run as a worker the supervisor started")
                    print(" ".join(work.take(drive, args.worker, args.ids)) or "none taken: each was no longer queued")
                case "done":
                    item = work.done(drive, args.id, args.result or args.failed, failed=args.failed is not None)
                    print(f"{item['id']} {item['state']}")
                case "list":
                    for item in work.listed(drive):
                        print(f"{item['id']} {item['state']:7} {item['kind']:14} {item['lane']:24} {item['subject']} {item.get('worker') or ''} {item.get('result') or item.get('why') or ''}".rstrip())
        case "home":
            drive = args.drive or os.environ.get("LANE_OWNER", "").partition("/")[0]
            verbs.home(drive, args.lane, args.channel, args.thread)
            print(f"{args.lane} now posts in {args.channel}")
        case "block":
            drive = args.drive or os.environ.get("LANE_OWNER", "").partition("/")[0]
            verbs.blocked(drive, args.lane, args.what, args.by)
            print(f"written on {args.lane}")
        case "unpark":
            verbs.unpark(*where(args))
        case "pin":
            verbs.pin(*where(args), not args.off)
        case "end-turn":
            drive, lane_id = where(args)
            lane = verbs.end_turn(drive, lane_id, owner_token(), [verbs.parse_event(text) for text in args.on], args.until, args.why, args.next)
            print(f"waiting until {lane['wait']['until']}; end your turn now")
        case "say":
            drive, lane_id = where(args)
            if args.status:
                print(voice.status(drive, lane_id, args.status, args.re))
            elif not args.text:
                raise store.Refused("lane say takes the post's text, or --status for the loader line")
            else:
                print(voice.owner_say(drive, lane_id, {"text": args.text, "next": args.next, "by": args.by, "answers": args.answers, "re": args.re, "options": args.options, "edit": args.edit, "subjects": args.about, "pictures": args.picture}))
        case "log":
            verbs.log(*where(args), token(), args.text)
        case "next":
            verbs.set_next(*where(args), token(), args.text, args.says, args.serves, args.serves_none)
        case "done-check" if args.check_verb == "add":
            print(f"done check {verbs.add_check(*where(args), token(), args.words)}")
        case "done-check" if args.check_verb in ("reword", "drop"):
            verbs.reword_check(*where(args), token(), args.number, args.words if args.check_verb == "reword" else None)
        case "done-check":
            verbs.mark_check(*where(args), token(), args.number, args.check_verb == "met", args.how)
        case "asks":
            for ask in store.load(*where(args))["asks"]:
                print(f"{ask['at']} ts {ask.get('ts')}{'' if ask.get('replied') else ' UNANSWERED'}{' done' if ask.get('done') else ''}: {ask['words']}" + (f" ({ask['link']})" if ask.get("link") else ""))
        case "done":
            verbs.done(*where(args), token(), args.ts)
            try:
                ack.finished(*where(args), args.ts)
            except (store.Refused, OSError) as failed:
                print(f"marked done; no ✅ in Slack: {failed}", file=sys.stderr)
        case "pr":
            drive, lane_id = where(args)
            verbs.add_pr(drive, lane_id, token(), *pr_ref(args.ref), args.words)
        case "queue":
            drive, lane_id = where(args)
            holder = args.holder or lane_id
            match args.action:
                case "take":
                    print(json.dumps(queue.take(drive, args.resource, holder, lane_id, args.ttl)))
                case "renew":
                    print(queue.renew(drive, args.resource, holder, args.ttl))
                case "release":
                    queue.release(drive, args.resource, holder)
        case "approve" | "hold":
            drive = args.drive or os.environ.get("LANE_OWNER", "").partition("/")[0]
            if not drive:
                raise store.Refused("name the drive with --drive")
            print(", ".join(verbs.tapped(drive, *pr_ref(args.ref), args.verb.title(), args.head, args.source)))
        case "incident":
            drive, lane_id = where(args)
            verbs.set_incident(drive, lane_id, token(), args.link, args.monitor, args.fix_live)
        case "show":
            print(show(store.load(*where(args))))
        case "list" | "check":
            drive = args.drive or os.environ.get("LANE_OWNER", "").partition("/")[0]
            good, lines = supervisor.check(drive)
            print("\n".join(lines))
            return 0 if good or args.verb == "list" else 1
        case "pass":
            print(json.dumps(supervisor.run_pass(args.drive)))
        case "supervise":
            supervisor.supervise(args.drive)
        case "page":
            if args.install:
                print(page.install(args.drive, args.port))
            else:
                page.serve(args.drive, args.port)
        case "install":
            print(supervisor.install(args.drive))
        case "preflight":
            good, lines = supervisor.preflight(args.drive)
            print("\n".join(lines))
            return 0 if good else 1
        case "settle":
            good, lines = supervisor.settled(args.drive, args.seconds)
            print("\n".join(lines))
            return 0 if good else 1
        case "hook":
            if args.event == "bash":
                return bash_hook()
            return stop_hook(*where(args)) if args.event == "stop" else note_hook(*where(args))
    return 0
