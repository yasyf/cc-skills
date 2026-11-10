from datetime import datetime

from drive.state import ROLES, now, open_orders, owed, owed_at, parse_time, pr_key, pr_url

COMMUNICATOR_RULES = (
    (
        "Your first message on his message is an ack of at most one sentence you already know, and nothing else,"
        " inside 5 to 10 seconds; cc-slack has put the eyes on it, and a message carrying acked was acked by"
        " cc-slack while you compacted and gets no ack of yours. drive heard, any record its row names and the"
        " long-running:handler go in the next message, then the turn ends, with one drive call more only to record"
        " a read-only question's handler."
    ),
    (
        "Nothing else runs in your turn: every report, card, due line, draft, check-post, read of any kind and"
        " anything that takes more than one tool call runs in a background handler that posts its own result;"
        " a POST report, a ready or an ask record gets one handler, its prompt the one line"
        " `Record: <seq> Drive: <name> Scratch: <folder>`; an FYI report gets none. A record's body and the path it"
        " names are the handler's to read, never yours; you load no skill and no tool past those below, and use"
        " Read, Write or Edit on no file: each one comes back after every compaction."
    ),
    (
        "A ruling's ack is the first of two confirmations: say the skill is being updated and what changes;"
        " the second comes when the rewrite is installed."
    ),
    (
        "A handler's prompt is a few lines: his words verbatim, the permalink, the thread, its scratch folder, the"
        " deliverable and any path you know that answers it; never retype rules, since the handler's agent"
        " definition is its standing brief, and a handler's return is its permalinks and at most three lines."
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
        "Scope: before any builder starts, a PR is a port of #N (drive pr set --port-of, no tap card), an order of his"
        " (drive pr set --tap --serves <o> --design asked:<his answer>|none, his words quoted in its ready line), or"
        " neither, which is not built, polished or carded: another lane's PR goes back to it, an unordered finding"
        " becomes a question card."
    ),
    (
        "Design first: a part with a tricky design (a data layout or file format, a schema, a lock or queue, an API"
        " shape, a new background process, any two shapes costly to switch; when unsure it counts) first gets one"
        " long-running:designer on Fable; each fork it returns is a short question card, and no builder starts on"
        " the part until he answers them."
    ),
    (
        "A ruling travels by its words: briefs, handoffs and SendMessage carry what he wrote and what it implies, never"
        " an answer id alone; an id or title you see after a compaction is read whole with cc-notes answer show first."
    ),
    (
        "Every part has an owner: drive order add --parts names each deliverable his words name, each job says its"
        " --part, and an ORPHAN line gets a job, a card or a close in the same turn."
    ),
    "Never block: your own calls are reads of drive, cci and one gh call, beside the verbs the skill names.",
    (
        "Keep your context small: use Read, Write or Edit on no file and load a tool only for the step that needs it,"
        " since each file you touched last and each tool you loaded comes back after every compaction. A local"
        " job's brief is its Agent prompt; a remote lane's brief, handoff or message goes to drive job launch|resume"
        " <j> - from a quoted heredoc; a page of the skill is drive manual <page>; a log is a job's to read."
    ),
    "Never name an Agent: spawn each job as an unnamed background agent, never a named teammate.",
    "A monorepo builder starts where drive place says: on a Sprite while this Mac is over its load limit.",
    (
        "A promise is --due: a time he is promised is recorded with drive order set <id> --due; DUE repeats every"
        " 10 minutes until a drive post is in the order's thread, and done or dropped refuses until then."
    ),
    "Batch: job results and events feed the next TICK's report; while he is away only cards and incident lines go.",
)
RULES = {"communicator": COMMUNICATOR_RULES, "coordinator": COORDINATOR_RULES}
SUBSCRIBE = {
    "communicator": "cci subscribe --reader communicator --cursor communicator",
    "coordinator": (
        "cci subscribe --reader root --cursor coordinator --to-kind incident,mechanism,fix-live,decide,ask,design,"
        "blocker,blocked,urgent,answer,release,correction,defect,owner,refuse"
    ),
}
OTHER = {"communicator": "coordinator", "coordinator": "communicator"}
JOB = {"communicator": "you talk to him", "coordinator": "you run his orders, jobs, events, landings and installs"}
REPROS_SHOWN = 5
MONITOR_LINE = "Arm Monitor `drive watch` now (timeout 1800000) unless one is running."
SLACK_TOOLS_LINE = (
    "Your first call now, before anything else, loads the tools your first message needs: ToolSearch"
    " select:mcp__plugin_cc-slack_cc-slack__slack_dm_status,"
    "mcp__plugin_cc-slack_cc-slack__slack_reply,mcp__plugin_cc-slack_cc-slack__slack_react,"
    "mcp__plugin_cc-inbox_cci__post"
)
FIRST_CALL = {"communicator": SLACK_TOOLS_LINE, "coordinator": MONITOR_LINE}
READS = {
    "communicator": "Everything else is on disk, read by a handler on demand, never by you: drive status <id>, drive view.",
    "coordinator": (
        "Everything else is on disk, read on demand by id: drive status <order, job or PR>, drive view, cci digest,"
        " and the skill's pages, drive manual <page>."
    ),
}
KEEP = {
    "communicator": (
        "Each handler still out: one line, `<his thread ts> <agent id> <his ask in at most 12 words>`.",
        "Each message of his acked with no handler started yet: `<his thread ts> <permalink> <his words verbatim>`.",
    ),
    "coordinator": (
        (
            "Each operation you own that the drive file does not record, one line with its id: a release, a Monitor"
            " or background task, a message you wait on."
        ),
        "His words no `drive order add` or cc-notes answer holds yet, verbatim, each with its permalink.",
        "The step you were in the middle of, in one line.",
    ),
}
KEEP_LIMIT = {"communicator": 1500, "coordinator": 3000}


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
    port = f" of #{pr['port_of']}" if pr.get("exempt") == "pure port" and pr.get("port_of") else ""
    return f"{key} {pr['state']}, checks {pr['checks']}, review {pr['review']}, {tap}{exempt}{port}"


def job_line(job_id: str, job: dict) -> str:
    if job.get("where") == "remote":
        handle = f" remote dispatch {job['dispatch']}" if job.get("dispatch") else " remote, not started"
    else:
        handle = f" agent {job['agent']}" if job.get("agent") else ""
    repro = f"; repro {job['repro']}" if job.get("repro") else ""
    carries = f"; {job['order']}'s job, carrying " + ", ".join(job["carries"]) if job.get("carries") else ""
    return f"{job_id} {job['kind']} {job['state']}{handle}: {cut(job['goal'], 100)}{repro}{carries}"


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


def owed_lines(drive: dict) -> list[str]:
    moment = now()
    owed_now = [
        (parse_time(owed_at(order)), order_id)
        for order_id, order in open_orders(drive).items()
        if owed(order) and parse_time(owed_at(order)) <= moment
    ]
    return [f"- {order_id} line owed since {clock(due)}" for due, order_id in sorted(owed_now)]


def context(drive: dict, role: str) -> str:
    """The SessionStart block: a session's role, its rules, one line per item that needs it now, and the reads."""
    lines = [
        (
            f"You are the {role} of drive {drive['name']}: {JOB[role]}. The {OTHER[role]}: {who(drive, OTHER[role])}."
            " A background agent this session started ignores this block."
        ),
        hold_line(drive, role),
        f"Subscribe: cci drive use {drive['name']}, then {SUBSCRIBE[role]}",
        f"{role.capitalize()} rules:",
        *(f"{number}. {rule}" for number, rule in enumerate(RULES[role], 1)),
    ]
    if role == "coordinator":
        lines.extend(owed_lines(drive))
    lines += [READS[role], FIRST_CALL[role]]
    return "\n".join(lines)


def compact_instructions(drive: dict, role: str) -> str:
    """The PreCompact hook's stdout, which Claude Code uses as the instructions for the compaction summary."""
    return "\n".join(
        [
            (
                f"These instructions replace the default summary sections. The summary of the {role} of drive"
                f" {drive['name']} is at most {KEEP_LIMIT[role]} characters and holds only:"
            ),
            *(f"{number}. {item}" for number, item in enumerate(KEEP[role], 1)),
            (
                "A section with nothing in it is the word none. Leave out everything else: his earlier messages,"
                " orders, jobs, PRs, cards, records, rulings, any other thread ids, the drive's rules and this"
                " session's history. The drive file, cci, Slack and cc-notes hold them, and the SessionStart hook"
                " prints the role's rules after this compaction. A background agent this session started ignores"
                " these instructions and summarizes its own task."
            ),
        ]
    )


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
