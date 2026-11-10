from datetime import datetime

from drive.state import ROLES, now, open_orders, parse_time, pr_key, pr_url

COMMUNICATOR_RULES = (
    (
        "Your first message on his message is slack_pickup and an ack of at most one sentence you already know, and"
        " nothing else, inside 5 to 10 seconds; drive heard, any record its row names and the long-running:handler go"
        " in the next message, then the turn ends, with one drive call more only to record a read-only question's"
        " handler."
    ),
    (
        "Nothing else runs in your turn: every report, card, due line, draft, check-post, read of any kind and"
        " anything that takes more than one tool call runs in a background handler that posts its own result;"
        " a report, ready or ask record gets a handler too."
    ),
    (
        "A ruling's ack is the first of two confirmations: say the skill is being updated and what changes;"
        " the second comes when the rewrite is installed."
    ),
    (
        "A handler's prompt is a few lines: his words verbatim, the permalink, the thread, its scratch folder, the"
        " deliverable and any path you know that answers it; never retype rules, since the handler's agent"
        " definition is its standing brief, and a handler's return is facts, links and paths, never prose."
    ),
    (
        "After a compaction or a restart, your first call is the ToolSearch below; then one handler posts a line in"
        " each thread where he waits on a handler that has not returned: what is happening and when he hears next."
    ),
    "Batch: you post nothing on your own clock; summaries come as reports, and while he is away only cards post.",
)
COORDINATOR_RULES = (
    "Never post to Slack: everything he sees goes to communicator@<drive> as a cci report, ready or ask record.",
    (
        "A question of his in an owner record gets a report of what you have inside 5 to 10 seconds; the read,"
        " in a job when it takes more than one call, follows in a second report."
    ),
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
SLACK_TOOLS_LINE = (
    "Your first call now, before anything else, loads the tools your first message needs: ToolSearch"
    " select:mcp__plugin_cc-slack_cc-slack__slack_pickup,mcp__plugin_cc-slack_cc-slack__slack_dm_status,"
    "mcp__plugin_cc-slack_cc-slack__slack_reply,mcp__plugin_cc-slack_cc-slack__slack_react,"
    "mcp__plugin_cc-inbox_cci__post"
)
FIRST_CALL = {"communicator": SLACK_TOOLS_LINE, "coordinator": MONITOR_LINE}


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


def role_line(drive: dict, role: str, seen: dict[str, str]) -> str:
    place = f", {seen[role]}" if role in seen else ""
    return f"{role}: {who(drive, role)}{place}; {hold_line(drive, role)}"


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
    lines += ["", FIRST_CALL[role]]
    return "\n".join(lines)


def status_data(drive: dict, pr_fields: str, seen: dict[str, str]) -> dict:
    orders = open_orders(drive)
    job_ids = [job_id for order in orders.values() for job_id in order["jobs"]]
    return {
        "name": drive["name"],
        "pr_fields": pr_fields,
        "roles": drive["roles"],
        "orca": seen,
        "slack": {"channel": drive["slack"]["channel"], "watch": drive["slack"].get("watch", [])},
        "orders": orders,
        "jobs": {job_id: drive["jobs"][job_id] for job_id in job_ids},
        "prs": {key: pr for key, pr in drive["prs"].items() if pr["order"] in orders},
    }


def status_text(drive: dict, pr_fields: str, seen: dict[str, str]) -> str:
    orders = open_orders(drive)
    lines = [
        f"{drive['name']}: {len(orders)} open of {len(drive['orders'])} orders; PR fields {pr_fields}",
        *(role_line(drive, role, seen) for role in ROLES),
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
