from datetime import datetime

from drive.state import ROLES, now, open_orders, parse_time, pr_key, pr_url

COMMUNICATOR_RULES = (
    (
        "Ack at once: each message of his gets slack_pickup, then a one-line ack in its thread, then drive heard,"
        " before anything else."
    ),
    (
        "Hand the work over: his words go to the coordinator as cci post --kind owner --to root@<drive>, verbatim"
        " with their link; his answer to a question card as --kind answer."
    ),
    (
        "Never work, never wait: your calls are Slack, cci posts, drive heard, drive status, drive rollup and"
        " cc-slack check-post, and each turn ends in seconds."
    ),
    (
        "Post what the coordinator sends: a report as a line in its thread, a ready as a PR card, an ask as a"
        " question card, each by reference/messages.md."
    ),
    "Batch: you post nothing on your own clock; summaries come as reports, and while he is away only cards post.",
)
COORDINATOR_RULES = (
    "Never post to Slack: everything he sees goes to communicator@<drive> as a cci report, ready or ask record.",
    "One job per deliverable: every order goes to one background job that owns it end to end, past-due ones too.",
    "Never block: your own calls are reads of drive, cci and one gh call, beside the verbs the skill names.",
    "Never name an Agent: spawn each job as an unnamed background agent, never a named teammate.",
    "A monorepo builder starts where drive place says: on a Sprite while this Mac is over its load limit.",
    "A promise is --due: a time he is promised is recorded with drive order set <id> --due.",
    "Batch: job results and events feed the next TICK's report; while he is away only cards and incident lines go.",
)
RULES = {"communicator": COMMUNICATOR_RULES, "coordinator": COORDINATOR_RULES}
SUBSCRIBE = {
    "communicator": "cci subscribe --reader communicator --cursor communicator",
    "coordinator": "cci subscribe --reader root --cursor coordinator",
}
OTHER = {"communicator": "coordinator", "coordinator": "communicator"}
JOB = {"communicator": "you talk to him", "coordinator": "you run his orders, jobs, events, landings and installs"}
MONITOR_LINE = "Arm Monitor `drive watch` now (timeout 1800000) unless one is running."
ROLLUP_FACTS = 10


def cut(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def clock(moment: datetime) -> str:
    return moment.strftime("%-I:%M %p")


def when(iso: str) -> str:
    moment = parse_time(iso)
    if moment.date() == now().date():
        return clock(moment)
    return f"{moment.strftime('%b %-d')} {clock(moment)}"


def order_prs(drive: dict, order_id: str) -> dict[str, dict]:
    return {key: pr for key, pr in drive["prs"].items() if pr["order"] == order_id}


def job_prs(drive: dict, job_id: str) -> dict[str, dict]:
    return {key: pr for key, pr in drive["prs"].items() if pr["job"] == job_id}


def order_line(order_id: str, order: dict, limit: int = 120) -> str:
    due = f" due {when(order['due'])}" if order.get("due") else ""
    return f"{order_id} {order['state']}{due}: {cut(order['text'], limit)}"


def pr_line(key: str, pr: dict) -> str:
    tap = pr["tap"]["label"] if pr.get("tap") else ("wants tap" if pr["wants_tap"] else "no tap needed")
    exempt = f", exempt: {pr['exempt']}" if pr.get("exempt") else ""
    return f"{key} {pr['state']}, checks {pr['checks']}, review {pr['review']}, {tap}{exempt}"


def job_line(job_id: str, job: dict) -> str:
    if job.get("where") == "remote":
        handle = f" remote dispatch {job['dispatch']}" if job.get("dispatch") else " remote, not started"
    else:
        handle = f" agent {job['agent']}" if job.get("agent") else ""
    return f"{job_id} {job['kind']} {job['state']}{handle}: {cut(job['goal'], 100)}"


def job_pr_lines(drive: dict, job_id: str, job: dict) -> list[str]:
    keys = dict.fromkeys([*map(pr_key, job["prs"]), *job_prs(drive, job_id)])
    return [pr_line(key, drive["prs"][key]) if key in drive["prs"] else pr_url(key) for key in keys]


def held_channels(drive: dict, role: str) -> list[str]:
    slack = drive["slack"]
    if role == "communicator":
        return [f"his DM {slack['channel']}" if slack["channel"] else "no DM set"]
    return slack.get("watch", [])


def hold_line(drive: dict, role: str) -> str:
    channels = held_channels(drive, role)
    return f"Hold with slack_watch: {', '.join(channels)}" if channels else "Hold no Slack channel."


def who(drive: dict, role: str) -> str:
    held = drive["roles"].get(role)
    return f"session {held['session']}" if held else "not started"


def role_line(drive: dict, role: str) -> str:
    return f"{role}: {who(drive, role)}; {hold_line(drive, role)}"


def order_block(drive: dict, order_id: str, order: dict, limit: int) -> list[str]:
    lines = [f"- {order_line(order_id, order, limit)}"]
    for job_id in order["jobs"]:
        job = drive["jobs"][job_id]
        lines.append(f"  - {job_line(job_id, job)}")
        lines.extend(f"    - {line}" for line in job_pr_lines(drive, job_id, job))
    return lines


def context(drive: dict, role: str) -> str:
    """The SessionStart block that tells a session its role in the drive, the drive's open orders and its rules."""
    orders = open_orders(drive)
    lines = [
        f"You are the {role} of drive {drive['name']}: {JOB[role]}. The {OTHER[role]}: {who(drive, OTHER[role])}.",
        hold_line(drive, role),
        f"Subscribe: cci drive use {drive['name']}, then {SUBSCRIBE[role]}",
        "",
        f"Open orders ({len(orders)}):",
    ]
    for order_id, order in orders.items():
        if role == "coordinator":
            lines.extend(order_block(drive, order_id, order, 120))
        else:
            lines.append(f"- {order_line(order_id, order, 120)}")
    if not orders:
        lines.append("- none")
    lines += ["", f"{role.capitalize()} rules:"]
    lines.extend(f"{number}. {rule}" for number, rule in enumerate(RULES[role], 1))
    if role == "coordinator":
        lines += ["", MONITOR_LINE]
    return "\n".join(lines)


def status_data(drive: dict, pr_fields: str) -> dict:
    orders = open_orders(drive)
    job_ids = [job_id for order in orders.values() for job_id in order["jobs"]]
    return {
        "name": drive["name"],
        "pr_fields": pr_fields,
        "roles": drive["roles"],
        "slack": {"channel": drive["slack"]["channel"], "watch": drive["slack"].get("watch", [])},
        "orders": orders,
        "jobs": {job_id: drive["jobs"][job_id] for job_id in job_ids},
        "prs": {key: pr for key, pr in drive["prs"].items() if pr["order"] in orders},
    }


def status_text(drive: dict, pr_fields: str) -> str:
    orders = open_orders(drive)
    lines = [
        f"{drive['name']}: {len(orders)} open of {len(drive['orders'])} orders; PR fields {pr_fields}",
        *(role_line(drive, role) for role in ROLES),
    ]
    for order_id, order in orders.items():
        lines.extend(order_block(drive, order_id, order, 80))
    return "\n".join(lines)


def due_order(item: tuple[str, dict]) -> tuple:
    order_id, order = item
    due = order.get("due")
    return (due is None, parse_time(due) if due else parse_time(order["created"]), int(order_id[1:]))


def rollup_value(drive: dict, order_id: str, order: dict) -> str:
    parts = [order["state"]]
    if order.get("due"):
        parts.append(f"due {when(order['due'])}")
    parts.extend(pr["url"] for pr in order_prs(drive, order_id).values())
    return " · ".join(parts)


def rollup_card(drive: dict) -> dict:
    """The cc-slack status card summing up every open order, oldest due first."""
    orders = sorted(open_orders(drive).items(), key=due_order)
    return {
        "status": "info",
        "title": f"{len(orders)} open: {drive['name']}",
        "facts": [
            {"label": cut(order["text"], 60), "value": rollup_value(drive, order_id, order)}
            for order_id, order in orders[:ROLLUP_FACTS]
        ],
    }
