from datetime import datetime

from drive.state import now, open_orders, parse_time, pr_key, pr_url

DRIVER_RULES = (
    "One job per deliverable: every change or question goes to one background job that owns it end to end.",
    "Never block: no foreground waits, no work with a body; jobs and drive watch do the waiting.",
    "Never name an Agent: spawn each job as an unnamed background agent, never a named teammate.",
    "Every message by messages.md: each line, question card and PR card follows reference/messages.md.",
    "A promise is --due: a time you promise him is recorded with drive order set <id> --due.",
)
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
    agent = f" agent {job['agent']}" if job.get("agent") else ""
    return f"{job_id} {job['kind']} {job['state']}{agent}: {cut(job['goal'], 100)}"


def job_pr_lines(drive: dict, job_id: str, job: dict) -> list[str]:
    keys = dict.fromkeys([*map(pr_key, job["prs"]), *job_prs(drive, job_id)])
    return [pr_line(key, drive["prs"][key]) if key in drive["prs"] else pr_url(key) for key in keys]


def order_block(drive: dict, order_id: str, order: dict, limit: int) -> list[str]:
    lines = [f"- {order_line(order_id, order, limit)}"]
    for job_id in order["jobs"]:
        job = drive["jobs"][job_id]
        lines.append(f"  - {job_line(job_id, job)}")
        lines.extend(f"    - {line}" for line in job_pr_lines(drive, job_id, job))
    return lines


def context(drive: dict) -> str:
    """The SessionStart block that tells a driver session its drive, open orders and rules."""
    orders = open_orders(drive)
    lines = [f"You are the driver of drive {drive['name']}.", "", f"Open orders ({len(orders)}):"]
    for order_id, order in orders.items():
        lines.extend(order_block(drive, order_id, order, 120))
    if not orders:
        lines.append("- none")
    lines += ["", "Driver rules:"]
    lines.extend(f"{number}. {rule}" for number, rule in enumerate(DRIVER_RULES, 1))
    lines += ["", MONITOR_LINE]
    return "\n".join(lines)


def status_data(drive: dict, pr_fields: str) -> dict:
    orders = open_orders(drive)
    job_ids = [job_id for order in orders.values() for job_id in order["jobs"]]
    return {
        "name": drive["name"],
        "pr_fields": pr_fields,
        "orders": orders,
        "jobs": {job_id: drive["jobs"][job_id] for job_id in job_ids},
        "prs": {key: pr for key, pr in drive["prs"].items() if pr["order"] in orders},
    }


def status_text(drive: dict, pr_fields: str) -> str:
    orders = open_orders(drive)
    lines = [f"{drive['name']}: {len(orders)} open of {len(drive['orders'])} orders; PR fields {pr_fields}"]
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
