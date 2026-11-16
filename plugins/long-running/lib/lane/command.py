"""The `lane` command: the owner's verbs, read from LANE_OWNER and LANE_TOKEN in its session, and the verbs that open, message, park, check and supervise lanes."""

import argparse
import json
import os
import sys

from lane import queue, store, supervisor, verbs
from lane import voice

STOP_BLOCKS = 3


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
    """Block an owner's stop while its lane still runs under its token, telling it the one way out; after STOP_BLOCKS blocks let it stop, and its lapsed lease restarts the lane."""
    json.load(sys.stdin)
    held = token()
    lane = store.load(drive, lane_id)
    if lane["state"] != store.RUNNING or (lane.get("owner") or {}).get("token") != held:
        return 0
    blocks = store.update(drive, lane_id, lambda lane: lane["owner"].__setitem__("stop_blocks", lane["owner"].get("stop_blocks", 0) + 1) or lane["owner"]["stop_blocks"])
    if blocks > STOP_BLOCKS:
        return 0
    print(json.dumps({"decision": "block", "reason": "Your lane is still running. Answer any unanswered ask of his with `lane say`, then end your turn only through `lane end-turn --on <event> --until <bound> --why \"...\"`; with nothing else to wait for, `--on his_reply`."}))
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

    parking = verbs_parser.add_parser("park")
    parking.add_argument("lane")
    parking.add_argument("--words", required=True)
    parking.add_argument("--link")
    ruled = verbs_parser.add_parser("ruling", help="his answer, verbatim: on the lane it binds, waking its owner, or with no lane for every lane")
    ruled.add_argument("lane", nargs="?")
    ruled.add_argument("--words", required=True)
    ruled.add_argument("--link")
    ruled.add_argument("--binds", required=True)
    verbs_parser.add_parser("unpark").add_argument("lane")
    verbs_parser.add_parser("adopt", help="write lane files of the first shape, such as the rebuild's, into the drive in the agreed shape").add_argument("paths", nargs="+")
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
    said.add_argument("text")
    said.add_argument("--lane")
    said.add_argument("--next")
    said.add_argument("--by")
    said.add_argument("--answers", action="append", help="the ts of an ask this line answers; every unanswered ask when left out")

    for name in ("log", "next"):
        one = verbs_parser.add_parser(name)
        one.add_argument("text")
        one.add_argument("--lane")
    verbs_parser.add_parser("asks").add_argument("--lane")
    finished = verbs_parser.add_parser("done", help="mark one of his asks done, by its ts")
    finished.add_argument("ts")
    finished.add_argument("--lane")
    adding = verbs_parser.add_parser("pr")
    adding.add_argument("action", choices=["add"])
    adding.add_argument("ref")
    adding.add_argument("--words")
    adding.add_argument("--lane")
    granting = verbs_parser.add_parser("queue", help="ask the priority queue for the staging env, a release start or a card to him")
    granting.add_argument("action", choices=["take", "renew", "release"])
    granting.add_argument("resource", choices=list(queue.CAPACITY))
    granting.add_argument("--holder")
    granting.add_argument("--ttl", type=int, default=30)
    granting.add_argument("--lane")
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
    hooking = verbs_parser.add_parser("hook")
    hooking.add_argument("event", choices=["stop"])

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
        case "park":
            verbs.park(*where(args), args.words, args.link)
        case "ruling":
            drive = args.drive or os.environ.get("LANE_OWNER", "").partition("/")[0]
            verbs.ruling(drive, args.lane, args.words, args.link, args.binds)
        case "adopt":
            print("\n".join(verbs.adopt(args.drive, args.paths)))
        case "worktree":
            verbs.set_worktree(*where(args), args.path)
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
            print(voice.owner_say(drive, lane_id, {"text": args.text, "next": args.next, "by": args.by, "answers": args.answers}))
        case "log":
            verbs.log(*where(args), token(), args.text)
        case "next":
            verbs.set_next(*where(args), token(), args.text)
        case "asks":
            for ask in store.load(*where(args))["asks"]:
                print(f"{ask['at']} ts {ask.get('ts')}{'' if ask.get('replied') else ' UNANSWERED'}{' done' if ask.get('done') else ''}: {ask['words']}" + (f" ({ask['link']})" if ask.get("link") else ""))
        case "done":
            verbs.done(*where(args), token(), args.ts)
        case "pr":
            drive, lane_id = where(args)
            verbs.add_pr(drive, lane_id, token(), *pr_ref(args.ref), args.words)
        case "queue":
            drive, lane_id = where(args)
            holder = args.holder or f"{lane_id}/{token() or 'operator'}"
            match args.action:
                case "take":
                    print(json.dumps(queue.take(drive, args.resource, holder, lane_id, args.ttl)))
                case "renew":
                    print(queue.renew(drive, args.resource, holder, args.ttl))
                case "release":
                    queue.release(drive, args.resource, holder)
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
        case "install":
            print(supervisor.install(args.drive))
        case "hook":
            return stop_hook(*where(args))
    return 0
