#!/usr/bin/env python3
"""Landing desk over cc-notes — the inbox, holds, routes, and label guards of a merge desk.

    desk.py init    --title TEXT
    desk.py report  --desk ID --ledger ID --pr N --head SHA --lane NAME --verdict clean|red|conflicting|held [--text ...]
    desk.py enqueue --desk ID --kind p0|ruling|report|idle --pr N --head SHA --lane NAME --text ...
    desk.py ruling  --desk ID --lane NAME --text ... --options "A|B|C" [--pr N]
    desk.py inbox   --desk ID [--take] [--json]
    desk.py ack     --desk ID KEY...
    desk.py hold    --desk ID --ledger ID --pr N --reason ... (--until ISO | --hours H)
    desk.py lift    --desk ID --ledger ID --pr N
    desk.py holds   --desk ID
    desk.py route   --desk ID --ledger ID --pr N --job NAME [--head SHA] [--lane NAME]
    desk.py routes  --desk ID [--pr N]
    desk.py label   --desk ID --ledger ID --repo owner/name --pr N [--expect-head SHA] [--checkout DIR] [--dry-run]
    desk.py unlabel --desk ID --repo owner/name --pr N --reason ...
    desk.py landed  --desk ID --ledger ID --repo owner/name --checkout DIR [--pr N]
    desk.py summary --desk ID --ledger ID [--window-seconds N]
    desk.py show    --desk ID [--json]

STDLIB ONLY. GitHub is read and written through ``gh api`` REST, never GraphQL; a
landing is proven by the squash on the base branch in ``--checkout``, never by the
PR's merged field; storage is two ``ccn ledger`` instances, the desk's own and the
open-PR ledger ``ledger.py`` keeps. Everything passes through :class:`ledger.Shell`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ledger import Github, Notes, Shell, is_open, parse_iso, utc_stamp

KINDS = ("p0", "ruling", "report", "idle")
VERDICTS = ("clean", "red", "conflicting", "held")
MERGE_LABEL = "merge"
MIN_HEAD_AGE = timedelta(seconds=60)
LABELLABLE_STATES = ("clean", "behind", "has_hooks")
FAILED_CONCLUSIONS = ("failure", "timed_out", "cancelled", "action_required")
SUMMARY_LINES = 10
WINDOW_SECONDS = 3600
LOG_DEPTH = "400"
NO_PR = "-"
CLOSED_WITHOUT_SQUASH = "closed-without-squash"
SLUG = re.compile(r"[^a-z0-9]+")

ROUTE_TEXT = (
    "DESK #{pr} {head}: {job}\n"
    "Push the fix as a new head on the same branch, then send landing-desk the 3-line report "
    "(PR, head, verdict); the merge label waits on that head reading green and merge-clean."
)
REFUSAL = {
    "closed": "#{pr} is {state}; a landing is read from the {base} log, never labelled",
    "moved": "head moved: expected {expected}, the forge has {head}; grade the new head before labelling",
    "held": "#{pr} is held: {reason} until {until}",
    "labelled": "{head} was labelled at {at}; a head carries the label once, and a strip is not a rejection: read the Merge activity comment",
    "pulled": "{head} had its label pulled at {at} ({reason}); the same head is never re-queued",
    "mergeable": "mergeable_state {state}: only {allowed} may be labelled",
    "young": "head is {age}s old; the forge has not graded it, retry after {min}s",
    "status": "commit status {state} on {head}; only success is labelled",
    "checks": "failed check runs on {head}: {names}",
    "conflict": "{head} conflicts with {base} on {paths}; route the rebase, never label",
    "fetched": "refs/pull/{pr}/head is {fetched} on the forge, not {head}",
}


class Desk(Notes):
    def section(self, prefix: str) -> dict[str, dict[str, str]]:
        return {key: fields for key, fields in self.rows().items() if key.startswith(prefix + "/")}


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(text: str) -> str:
    return SLUG.sub("-", text.lower()).strip("-")


def refuse(kind: str, **values: object) -> int:
    print(f"REFUSED {REFUSAL[kind].format(**values)}")
    return 1


def priority(item: tuple[str, dict[str, str]]) -> tuple[int, str]:
    key, fields = item
    return KINDS.index(fields["kind"]), key


def next_key(messages: dict[str, dict[str, str]]) -> str:
    seq = max((int(key.split("/")[1]) for key in messages), default=0) + 1
    return f"msg/{seq:06d}"


def duplicate(messages: dict[str, dict[str, str]], fields: dict[str, str]) -> str | None:
    identity = ("kind", "pr", "text") if fields["kind"] == "ruling" else ("kind", "pr", "head")
    wanted = tuple(fields[name] for name in identity)
    for key, existing in messages.items():
        if tuple(existing.get(name, "") for name in identity) == wanted:
            return key
    return None


def enqueue(desk: Desk, fields: dict[str, str]) -> str:
    messages = desk.section("msg")
    seen = duplicate(messages, fields)
    if seen:
        print(f"duplicate of {seen}; nothing recorded, nothing to answer")
        return seen
    key = next_key(messages)
    desk.set_fields(key, dict(fields, at=utc_stamp(), state="pending"))
    print(f"{key} {fields['kind']} #{fields['pr']} {fields['head'][:9]} from {fields['lane']}")
    return key


def message_line(key: str, fields: dict[str, str]) -> str:
    return f"{key} {fields['kind']} #{fields['pr']} {fields['head'][:9]} {fields['lane']}: {fields['text']}"


def ruling_line(fields: dict[str, str]) -> str:
    options = " / ".join(part.strip() for part in fields["options"].split("|"))
    return f"RULING NEEDED: {fields['text']}; options: {options}"


def active_hold(desk: Desk, pr: str) -> dict[str, str] | None:
    hold = desk.rows().get(f"hold/{pr}")
    return hold if hold and hold["state"] == "active" else None


def hold_line(pr: str, hold: dict[str, str], moment: datetime) -> str:
    expired = " EXPIRED" if parse_iso(hold["until"]) < moment else ""
    return f"held #{pr}: {hold['reason']} until {hold['until']}{expired}"


def squash_on_base(shell: Shell, checkout: Path, base: str, pr: str) -> tuple[str, str] | None:
    git = ["git", "-C", str(checkout)]
    shell.run(git + ["fetch", "-q", "origin", base])
    suffix = f"(#{pr})"
    log = shell.run(git + ["log", "FETCH_HEAD", "--format=%H %s", "-n", LOG_DEPTH, "--fixed-strings", f"--grep={suffix}"])
    for line in log.splitlines():
        sha, _, subject = line.partition(" ")
        if subject.endswith(suffix):
            landed_at = shell.run(git + ["log", "-1", "--format=%cI", sha]).strip()
            return sha, stamp(parse_iso(landed_at).astimezone(timezone.utc))
    return None


def merge_conflicts(shell: Shell, checkout: Path, pr: str, base: str, head: str) -> str | None:
    git = ["git", "-C", str(checkout)]
    shell.run(git + ["fetch", "-q", "origin", f"refs/pull/{pr}/head"])
    fetched = shell.run(git + ["rev-parse", "FETCH_HEAD"]).strip()
    if fetched != head:
        return REFUSAL["fetched"].format(pr=pr, fetched=fetched[:9], head=head[:9])
    shell.run(git + ["fetch", "-q", "origin", base])
    try:
        shell.run(git + ["merge-tree", "--write-tree", "FETCH_HEAD", head])
    except subprocess.CalledProcessError as failure:
        paths = sorted({line.split("\t")[-1] for line in failure.stdout.splitlines()[1:] if "\t" in line})
        return REFUSAL["conflict"].format(head=head[:9], base=base, paths=", ".join(paths) or "unlisted paths")
    return None


def cmd_init(args: argparse.Namespace, shell: Shell) -> int:
    payload = json.loads(shell.run(["ccn", "ledger", "add", args.title, "--json"]))
    print(payload["id"])
    return 0


def cmd_enqueue(args: argparse.Namespace, shell: Shell) -> int:
    enqueue(Desk(shell, args.desk), {"kind": args.kind, "pr": args.pr, "head": args.head, "lane": args.lane, "text": args.text})
    return 0


def cmd_report(args: argparse.Namespace, shell: Shell) -> int:
    fields = {"kind": "report", "pr": args.pr, "head": args.head, "lane": args.lane, "text": f"{args.verdict} {args.text}".strip()}
    enqueue(Desk(shell, args.desk), fields)
    Notes(shell, args.ledger).set_fields(
        args.pr,
        {"lane": args.lane, "reported_head": args.head, "reported_verdict": args.verdict, "reported_at": utc_stamp()},
    )
    return 0


def cmd_ruling(args: argparse.Namespace, shell: Shell) -> int:
    fields = {"kind": "ruling", "pr": args.pr, "head": NO_PR, "lane": args.lane, "text": args.text, "options": args.options}
    enqueue(Desk(shell, args.desk), fields)
    print(ruling_line(fields))
    return 0


def cmd_inbox(args: argparse.Namespace, shell: Shell) -> int:
    desk = Desk(shell, args.desk)
    pending = sorted(((key, fields) for key, fields in desk.section("msg").items() if fields["state"] == "pending"), key=priority)
    if args.json:
        print(json.dumps([dict(fields, key=key) for key, fields in pending]))
    else:
        for key, fields in pending:
            print(ruling_line(fields) if fields["kind"] == "ruling" else message_line(key, fields))
    if args.take:
        taken = utc_stamp()
        for key, _ in pending:
            desk.set_fields(key, {"state": "acked", "acked_at": taken})
    return 0


def cmd_ack(args: argparse.Namespace, shell: Shell) -> int:
    desk = Desk(shell, args.desk)
    acked = utc_stamp()
    for key in args.keys:
        desk.set_fields(key, {"state": "acked", "acked_at": acked})
    return 0


def cmd_hold(args: argparse.Namespace, shell: Shell) -> int:
    since = now()
    until = parse_iso(args.until) if args.until else since + timedelta(hours=args.hours)
    Desk(shell, args.desk).set_fields(
        f"hold/{args.pr}",
        {"reason": args.reason, "since": stamp(since), "until": stamp(until), "state": "active", "lifted_at": ""},
    )
    Notes(shell, args.ledger).set_fields(args.pr, {"hold_reason": args.reason, "hold_since": stamp(since)})
    print(f"held #{args.pr} until {stamp(until)}: {args.reason}")
    return 0


def cmd_lift(args: argparse.Namespace, shell: Shell) -> int:
    lifted = utc_stamp()
    Desk(shell, args.desk).set_fields(f"hold/{args.pr}", {"state": "lifted", "lifted_at": lifted})
    Notes(shell, args.ledger).set_fields(args.pr, {"hold_reason": "", "hold_since": ""})
    print(f"lifted #{args.pr} at {lifted}")
    return 0


def cmd_holds(args: argparse.Namespace, shell: Shell) -> int:
    moment = now()
    for key, hold in sorted(Desk(shell, args.desk).section("hold").items()):
        if hold["state"] == "active":
            print(hold_line(key.split("/")[1], hold, moment))
    return 0


def cmd_route(args: argparse.Namespace, shell: Shell) -> int:
    desk = Desk(shell, args.desk)
    row = Notes(shell, args.ledger).rows().get(args.pr, {})
    head = args.head or row.get("head") or row["reported_head"]
    lane = args.lane or row["lane"]
    key = f"route/{args.pr}/{head}/{slug(args.job)}"
    existing = desk.rows().get(key)
    if existing:
        print(f"already routed to {existing['lane']} at {existing['sent_at']}; nothing to send")
        return 0
    desk.set_fields(key, {"lane": lane, "job": args.job, "sent_at": utc_stamp()})
    print(f"to {lane}:\n{ROUTE_TEXT.format(pr=args.pr, head=head[:9], job=args.job)}")
    return 0


def cmd_routes(args: argparse.Namespace, shell: Shell) -> int:
    for key, fields in sorted(Desk(shell, args.desk).section("route").items()):
        _, pr, head, _ = key.split("/", 3)
        if args.pr and pr != args.pr:
            continue
        print(f"#{pr} {head[:9]} -> {fields['lane']} {fields['sent_at']}: {fields['job']}")
    return 0


def cmd_label(args: argparse.Namespace, shell: Shell) -> int:
    gh = Github(shell, args.repo)
    desk = Desk(shell, args.desk)
    pull = gh.api(f"pulls/{args.pr}")
    head, base = pull["head"]["sha"], pull["base"]["ref"]
    if pull["state"] != "open":
        return refuse("closed", pr=args.pr, state=pull["state"], base=base)
    if args.expect_head and args.expect_head != head:
        return refuse("moved", expected=args.expect_head[:9], head=head[:9])
    records = desk.rows()
    hold = records.get(f"hold/{args.pr}")
    if hold and hold["state"] == "active":
        return refuse("held", pr=args.pr, reason=hold["reason"], until=hold["until"])
    label = records.get(f"label/{args.pr}/{head}")
    if label and label.get("pulled_at"):
        return refuse("pulled", head=head[:9], at=label["pulled_at"], reason=label["pull_reason"])
    if label:
        return refuse("labelled", head=head[:9], at=label["at"])
    if pull["mergeable_state"] not in LABELLABLE_STATES:
        return refuse("mergeable", state=pull["mergeable_state"], allowed="/".join(LABELLABLE_STATES))
    committed = parse_iso(gh.api(f"commits/{head}")["commit"]["committer"]["date"])
    age = now() - committed
    if age < MIN_HEAD_AGE:
        return refuse("young", age=int(age.total_seconds()), min=int(MIN_HEAD_AGE.total_seconds()))
    status = gh.api(f"commits/{head}/status")
    if status["state"] != "success":
        return refuse("status", state=status["state"], head=head[:9])
    failed = [run["name"] for run in gh.api(f"commits/{head}/check-runs")["check_runs"] if run["conclusion"] in FAILED_CONCLUSIONS]
    if failed:
        return refuse("checks", head=head[:9], names=", ".join(failed))
    if args.checkout:
        conflict = merge_conflicts(shell, args.checkout, args.pr, base, head)
        if conflict:
            print(f"REFUSED {conflict}")
            return 1
    if args.dry_run:
        print(f"would label #{args.pr} {head}")
        return 0
    shell.run(
        ["gh", "api", f"repos/{args.repo}/issues/{args.pr}/labels", "--method", "POST", "--input", "-"],
        stdin=json.dumps({"labels": [MERGE_LABEL]}),
    )
    labelled = utc_stamp()
    desk.set_fields(f"label/{args.pr}/{head}", {"at": labelled, "base": base, "pulled_at": "", "pull_reason": ""})
    Notes(shell, args.ledger).set_fields(args.pr, {"head": head, "last_label_head": head, "labelled_at": labelled})
    print(f"labelled #{args.pr} {head} at {labelled}")
    return 0


def cmd_unlabel(args: argparse.Namespace, shell: Shell) -> int:
    gh = Github(shell, args.repo)
    head = gh.api(f"pulls/{args.pr}")["head"]["sha"]
    shell.run(["gh", "api", f"repos/{args.repo}/issues/{args.pr}/labels/{MERGE_LABEL}", "--method", "DELETE"])
    pulled = utc_stamp()
    Desk(shell, args.desk).set_fields(f"label/{args.pr}/{head}", {"pulled_at": pulled, "pull_reason": args.reason})
    print(f"pulled {MERGE_LABEL} from #{args.pr} {head[:9]} at {pulled}; a pull is not a stop, the queue may already own this head")
    return 0


def cmd_landed(args: argparse.Namespace, shell: Shell) -> int:
    gh = Github(shell, args.repo)
    notes = Notes(shell, args.ledger)
    rows = notes.rows()
    for pr in [args.pr] if args.pr else sorted(rows, key=int):
        if rows.get(pr, {}).get("state") == "landed":
            continue
        pull = gh.api(f"pulls/{pr}")
        if pull["state"] == "open":
            continue
        base = pull["base"]["ref"]
        squash = squash_on_base(shell, args.checkout, base, pr)
        if squash:
            sha, landed_at = squash
            notes.set_fields(pr, {"state": "landed", "landed_sha": sha, "landed_at": landed_at, "base": base})
            print(f"landed #{pr} as {sha[:9]} on {base} at {landed_at}")
        else:
            notes.set_fields(pr, {"state": CLOSED_WITHOUT_SQUASH, "base": base})
            print(f"#{pr} is {CLOSED_WITHOUT_SQUASH} on {base}; a base deletion reads the same as a landing, so the row stays until its lane answers")
    return 0


def carries_label(fields: dict[str, str]) -> bool:
    labelled_head = fields.get("last_label_head", "")
    return MERGE_LABEL in fields.get("labels", "").split(",") or bool(labelled_head) and labelled_head == fields.get("head")


def summary_lines(records: dict[str, dict[str, str]], rows: dict[str, dict[str, str]], moment: datetime, window: timedelta) -> list[str]:
    cutoff = moment - window
    landed = sorted((pr for pr, fields in rows.items() if fields.get("landed_at") and parse_iso(fields["landed_at"]) >= cutoff), key=int)
    open_rows = {pr: fields for pr, fields in rows.items() if is_open(fields)}
    labelled = sorted((pr for pr, fields in open_rows.items() if carries_label(fields)), key=int)
    holds = {key.split("/")[1]: fields for key, fields in records.items() if key.startswith("hold/") and fields["state"] == "active"}
    pending = [fields for key, fields in sorted(records.items()) if key.startswith("msg/") and fields["state"] == "pending"]
    rulings = [fields for fields in pending if fields["kind"] == "ruling"]
    p0s = [fields for fields in pending if fields["kind"] == "p0"]
    routed = sorted({key.split("/")[1] for key in records if key.startswith("route/")} & set(open_rows), key=int)
    lines = [
        f"desk {stamp(moment)} | open {len(open_rows)} | merged/h {len(landed)} | labelled {len(labelled)} | held {len(holds)} | rulings {len(rulings)} | p0 {len(p0s)} | routed {len(routed)}"
    ]
    if landed:
        lines.append("merged: " + " ".join(f"#{pr}" for pr in landed))
    if labelled:
        lines.append("labelled: " + " ".join(f"#{pr}" for pr in labelled))
    lines += [f"P0 #{fields['pr']} {fields['lane']}: {fields['text']}" for fields in p0s]
    lines += [ruling_line(fields) for fields in rulings]
    lines += [hold_line(pr, holds[pr], moment) for pr in sorted(holds, key=int)]
    if routed:
        lines.append("routed, awaiting a new head: " + " ".join(f"#{pr}" for pr in routed))
    if len(lines) > SUMMARY_LINES:
        lines = lines[: SUMMARY_LINES - 1] + [f"... {len(lines) - SUMMARY_LINES + 1} more lines in desk show"]
    return lines


def cmd_summary(args: argparse.Namespace, shell: Shell) -> int:
    records = Desk(shell, args.desk).rows()
    rows = Notes(shell, args.ledger).rows()
    print("\n".join(summary_lines(records, rows, now(), timedelta(seconds=args.window_seconds))))
    return 0


def cmd_show(args: argparse.Namespace, shell: Shell) -> int:
    records = Desk(shell, args.desk).rows()
    if args.json:
        print(json.dumps(records, sort_keys=True))
        return 0
    for key in sorted(records):
        fields = " ".join(f"{name}={value}" for name, value in sorted(records[key].items()) if value)
        print(f"{key} {fields}")
    return 0


def add_desk(parser: argparse.ArgumentParser, ledger: bool = False, repo: bool = False) -> None:
    parser.add_argument("--desk", required=True)
    if ledger:
        parser.add_argument("--ledger", required=True)
    if repo:
        parser.add_argument("--repo", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desk.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create the desk ledger and print its id")
    init.add_argument("--title", required=True)
    init.set_defaults(handler=cmd_init)

    report = subparsers.add_parser("report", help="record a lane's 3-line ship report and its PR row")
    add_desk(report, ledger=True)
    report.add_argument("--pr", required=True)
    report.add_argument("--head", required=True)
    report.add_argument("--lane", required=True)
    report.add_argument("--verdict", required=True, choices=VERDICTS)
    report.add_argument("--text", default="")
    report.set_defaults(handler=cmd_report)

    enqueue_cmd = subparsers.add_parser("enqueue", help="record any lane message; duplicates by kind+PR+head are dropped")
    add_desk(enqueue_cmd)
    enqueue_cmd.add_argument("--kind", required=True, choices=KINDS)
    enqueue_cmd.add_argument("--pr", required=True)
    enqueue_cmd.add_argument("--head", required=True)
    enqueue_cmd.add_argument("--lane", required=True)
    enqueue_cmd.add_argument("--text", required=True)
    enqueue_cmd.set_defaults(handler=cmd_enqueue)

    ruling = subparsers.add_parser("ruling", help="record a question only the root can answer and print the RULING NEEDED line")
    add_desk(ruling)
    ruling.add_argument("--lane", required=True)
    ruling.add_argument("--text", required=True)
    ruling.add_argument("--options", required=True, metavar="A|B|C")
    ruling.add_argument("--pr", default=NO_PR)
    ruling.set_defaults(handler=cmd_ruling)

    inbox = subparsers.add_parser("inbox", help="pending messages, P0 first, then rulings, reports, idles")
    add_desk(inbox)
    inbox.add_argument("--take", action="store_true")
    inbox.add_argument("--json", action="store_true")
    inbox.set_defaults(handler=cmd_inbox)

    ack = subparsers.add_parser("ack", help="mark messages handled")
    add_desk(ack)
    ack.add_argument("keys", nargs="+")
    ack.set_defaults(handler=cmd_ack)

    hold = subparsers.add_parser("hold", help="hold a PR with a reason and an expiry")
    add_desk(hold, ledger=True)
    hold.add_argument("--pr", required=True)
    hold.add_argument("--reason", required=True)
    expiry = hold.add_mutually_exclusive_group(required=True)
    expiry.add_argument("--until")
    expiry.add_argument("--hours", type=float)
    hold.set_defaults(handler=cmd_hold)

    lift = subparsers.add_parser("lift", help="lift a hold")
    add_desk(lift, ledger=True)
    lift.add_argument("--pr", required=True)
    lift.set_defaults(handler=cmd_lift)

    holds = subparsers.add_parser("holds", help="active holds, expired ones flagged")
    add_desk(holds)
    holds.set_defaults(handler=cmd_holds)

    route = subparsers.add_parser("route", help="record a red or conflicting head routed to its lane and print the message")
    add_desk(route, ledger=True)
    route.add_argument("--pr", required=True)
    route.add_argument("--job", required=True)
    route.add_argument("--head")
    route.add_argument("--lane")
    route.set_defaults(handler=cmd_route)

    routes = subparsers.add_parser("routes", help="routing records")
    add_desk(routes)
    routes.add_argument("--pr")
    routes.set_defaults(handler=cmd_routes)

    label = subparsers.add_parser("label", help="re-read the head, run every guard, then add the merge label once")
    add_desk(label, ledger=True, repo=True)
    label.add_argument("--pr", required=True)
    label.add_argument("--expect-head")
    label.add_argument("--checkout", type=Path)
    label.add_argument("--dry-run", action="store_true")
    label.set_defaults(handler=cmd_label)

    unlabel = subparsers.add_parser("unlabel", help="pull the merge label and record why")
    add_desk(unlabel, repo=True)
    unlabel.add_argument("--pr", required=True)
    unlabel.add_argument("--reason", required=True)
    unlabel.set_defaults(handler=cmd_unlabel)

    landed = subparsers.add_parser("landed", help="settle closed PRs by the squash on the base branch")
    add_desk(landed, ledger=True, repo=True)
    landed.add_argument("--checkout", type=Path, required=True)
    landed.add_argument("--pr")
    landed.set_defaults(handler=cmd_landed)

    summary = subparsers.add_parser("summary", help="the hourly desk-to-root report, at most ten lines")
    add_desk(summary, ledger=True)
    summary.add_argument("--window-seconds", type=int, default=WINDOW_SECONDS)
    summary.set_defaults(handler=cmd_summary)

    show = subparsers.add_parser("show", help="every desk record")
    add_desk(show)
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=cmd_show)

    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or Shell())


if __name__ == "__main__":
    raise SystemExit(main())
