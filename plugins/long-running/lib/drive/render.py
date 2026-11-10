from datetime import datetime

from drive.state import ROLES, now, open_orders, parse_time, pr_key, pr_url

COMMUNICATOR_RULES = (
    (
        "One step per message of his: slack_pickup, a one-line ack in its thread, drive heard and a"
        " long-running:handler for it go out together, so the ack never waits behind a read and the handler"
        " never waits behind the ack."
    ),
    (
        "Do no work in your own turn: the handler reads, answers him, shares in the thread he named, records rulings"
        " and hands orders to the coordinator; a report, ready or ask record gets a handler too."
    ),
    (
        "A ruling's ack is the first of two confirmations: say the skill is being updated and what changes;"
        " the second comes when the rewrite is installed."
    ),
    (
        "Give each handler the message verbatim, its permalink and thread, its scratch folder and every path you"
        " already know that answers it, such as a job's reproduction file; a share brief never asks for a write-up,"
        " and a handler's return is facts, links and paths, never prose."
    ),
    (
        "After a compaction or a restart, first one line in each thread where he waits on a handler that has not"
        " returned: what is happening and when he hears next."
    ),
    "Batch: you post nothing on your own clock; summaries come as reports, and while he is away only cards post.",
)
COORDINATOR_RULES = (
    "Never post to Slack: everything he sees goes to communicator@<drive> as a cci report, ready or ask record.",
    "One job per deliverable: every order goes to one background job that owns it end to end, past-due ones too.",
    (
        "Every part has an owner: drive order add --parts names each deliverable his words name, each job says its"
        " --part, and an ORPHAN line gets a job, a card or a close in the same turn."
    ),
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
REPROS_SHOWN = 5
MONITOR_LINE = "Arm Monitor `drive watch` now (timeout 1800000) unless one is running."


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
    repro = f"; repro {job['repro']}" if job.get("repro") else ""
    return f"{job_id} {job['kind']} {job['state']}{handle}: {cut(job['goal'], 100)}{repro}"


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
    repros = sorted(
        ((job["updated"], job_id, job) for job_id, job in drive["jobs"].items() if job.get("repro")), reverse=True
    )[:REPROS_SHOWN]
    if repros:
        lines.append("Reproduction files, newest first:")
        lines.extend(f"- {job_id} ({job['order']}): {job['repro']}" for _, job_id, job in repros)
    return "\n".join(lines)
