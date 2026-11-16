"""The `lane` command: the owner's verbs, read from LANE_OWNER and LANE_TOKEN in its session, and the verbs that open, message, park, check and supervise lanes."""

import argparse
import json
import os
import sys

from lane import say as saying
from lane import store, supervisor, verbs

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
    print(json.dumps({"decision": "block", "reason": "Your lane is still running. Your turn ends only through `lane end-turn --on <event> --until <bound> --why \"...\"` (or `--until` alone for a time-only wait). Run `lane asks` first if he wrote."}))
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
    opening.add_argument("--worktree")
    opening.add_argument("--next")
    opening.add_argument("--incident-link")
    opening.add_argument("--hurting-users", action="store_true")

    messaging = verbs_parser.add_parser("message", help="his message in a lane's channel, which wakes its owner")
    messaging.add_argument("lane")
    messaging.add_argument("--words", required=True)
    messaging.add_argument("--link")
    messaging.add_argument("--ts")

    parking = verbs_parser.add_parser("park")
    parking.add_argument("lane")
    parking.add_argument("--words", required=True)
    verbs_parser.add_parser("unpark").add_argument("lane")
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

    for name in ("log", "next"):
        one = verbs_parser.add_parser(name)
        one.add_argument("text")
        one.add_argument("--lane")
    verbs_parser.add_parser("asks").add_argument("--lane")
    adding = verbs_parser.add_parser("pr")
    adding.add_argument("action", choices=["add"])
    adding.add_argument("ref")
    adding.add_argument("--lane")
    incident = verbs_parser.add_parser("incident")
    incident.add_argument("hurting", choices=["yes", "no"])
    incident.add_argument("--link")
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
            incident = {"hurting_users": args.hurting_users, "link": args.incident_link} if args.incident_link or args.hurting_users else None
            lane = verbs.open_lane(drive, args.lane, args.title, args.words, args.channel, args.thread, args.link, args.ts, args.parks, args.park_words, [pr_ref(ref) for ref in args.pr], args.worktree, args.next, incident)
            print(f"opened {lane['id']}: {lane['state']}, owner {(lane.get('owner') or {}).get('session') or 'none'}")
        case "message":
            drive, lane_id = where(args)
            print(f"{verbs.message(drive, lane_id, args.words, args.link, args.ts)} new")
        case "park":
            verbs.park(*where(args), args.words)
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
            print(saying.say(drive, lane_id, args.text, args.next, args.by, token()))
        case "log":
            verbs.log(*where(args), token(), args.text)
        case "next":
            verbs.set_next(*where(args), token(), args.text)
        case "asks":
            drive, lane_id = where(args)
            for ask in verbs.asks(drive, lane_id, token()):
                print(f"{ask['at']}{'' if ask.get('seen') else ' NEW'}: {ask['words']}" + (f" ({ask['link']})" if ask.get("link") else ""))
        case "pr":
            drive, lane_id = where(args)
            verbs.add_pr(drive, lane_id, token(), *pr_ref(args.ref))
        case "incident":
            drive, lane_id = where(args)
            verbs.set_incident(drive, lane_id, token(), args.hurting == "yes", args.link)
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
