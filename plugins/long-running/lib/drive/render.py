import json
import sys
from datetime import datetime
from pathlib import Path

from drive.state import ROLES, now, open_orders, owed, owed_at, parse_time, pr_key, pr_url, promise_thread

PLUGIN_JSON = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
COMMUNICATOR_RULES = (
    (
        "Write no text in this terminal, which he never reads: every message of your turn is tool calls alone,"
        " everything for him goes to Slack, and the turn ends on the one word Sent., since Claude Code asks again"
        ' after a turn with no text at all. His words, Nov 10 9:53 PM: "you doint ned to kjeep sending the response'
        ' here as wel", and 10:20 PM: "update your skoll; to tro minimuize temrinal outpoujt as well".'
    ),
    (
        "Your first message on his message is an ack of at most one sentence you already know, and nothing else,"
        " inside 5 to 10 seconds; cc-slack has put the eyes on it, and a message carrying acked was acked by"
        " cc-slack while you compacted and gets no ack of yours. The drive's heard tool, which takes no thread since"
        " his catch-up goes at his DM root (away: true when he says he is going to sleep or away), its record tool for"
        " any record its row names, and the long-running:handler go in the next message, then the turn ends, with one"
        " call more only to record a read-only question's handler, the job tool with ops"
        ' [{add: {kind: "reader", order: {words: "<his words, verbatim>", kind: "answer", complex: true, from:'
        ' "slack:<channel>/<ts>", thread: "<ts>", project: "<the project in use his words are about>", workstream:'
        ' "<its workstream>"}, goal: "<one line>", agent: "<id>"}}], leaving out complex only for a one-fact lookup,'
        " or a tooling reader, by the next rule."
    ),
    (
        "Tooling and meta work is yours, never the coordinator's, and a build: a defect record, a handler's DEFECT"
        " <tool: refusal> line, a defect you hit and a ruling's skill rewrite each get in the next message one call of"
        ' the job tool, ops [{add: {kind: "reader", order: {words: "Drive tooling: <what>", kind: "build", from:'
        ' "<its From>", project: "The drive", workstream: "Drive tooling"}, goal: "research: <what>"}}], then a long-running:reader, its prompt the lines `Order: Drive'
        " tooling: <what>`, `From: cci:<seq>`, `slack:<channel>/<ts>` or `agent:<id>`, his words verbatim for a ruling,"
        " `Research: the Researched: block this build needs`, `Drive: <name>, order <o>, job <j>`, `Scratch: <folder>`."
        ' Its DONE line gets the job tool with ops [{add: {kind: "builder", order: "<o>", goal: "<one line>"}}], then a'
        " long-running:builder, its prompt the same lines with `Serves: drive tooling` and `Researched: <the reader's"
        " job>` for the Research line; its DESIGN line the same with a design job and a long-running:designer. A builder"
        " of yours that returns WAITING needs nothing, since its line set its job waiting; any other result a handler"
        " briefed `Job: <j> Drive: <name> Scratch: <folder>`, and a"
        " handoff record from the watch a builder briefed `Handoff: <seq> Drive: <name> Scratch: <folder>`."
    ),
    (
        'Chatter ("ok", "thanks"): a +1 react for its ack, heard, no handler. Words typed in your terminal, or an'
        " owner record from the coordinator, are his message, acked at his DM root. A handler's MISSING <path>: an"
        " owner record with his share words and that path."
        " His message in another channel (relayed) was written to the people there: no ack; heard and a handler"
        " only when it orders the drive or rules on a job's work."
    ),
    (
        "A question-card tap (waits: true): a record of kind answer, its text the label and waits the card's waits id,"
        " or topic card:<thread_ts> with no ask record; Explain more: no record, a handler briefed"
        " `Message (his words): Explain more` with the card's waits id and permalink. An Undo (undo: true) takes back"
        " his answer, which was never given: the same record with undone: true. A tap on the card of a builder of yours,"
        " its waits id naming its job, also gets"
        " SendMessage to that builder with his pick. A PR-card tap (merge:): nothing."
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
        " definition is its standing brief, and a handler's return is one line: its permalinks and at most three facts."
        ' A handler runs on Opus; pass model: "sonnet" only for a very simple one, a ready record, a POST report'
        " posted as written or a one-fact lookup, never for research, an explanation, a design, a card or a judgment"
        ' call, by his Nov 10 8:57 PM words "use judgemnt … only whern itsd a very simple ask, for more complex use opus'
        ' from the start".'
    ),
    (
        "After a compaction or a restart, your first call is the ToolSearch below; then one handler posts a line in"
        " each thread where he waits on a handler that has not returned: what is happening and when he hears next."
    ),
    "Batch: you post nothing on your own clock; summaries come as reports, and while he is away only cards post.",
)
COORDINATOR_RULES = (
    (
        "Never post to Slack: everything he sees goes to communicator@<drive> as a cci report, ready or ask record,"
        " written at its post's length: one report per thread per turn, a line of at most 60 words or a summary of"
        " 120, its first sentence what changed for him, each thing it names that he could open with its URL beside"
        " it, and never a second record for the rest."
    ),
    (
        "A question of his in an owner record gets a report of what you have inside 5 to 10 seconds; the read,"
        " in a job when it takes more than one call, follows in a second report."
    ),
    (
        "One job per deliverable: every order goes to one background job that owns it end to end, past-due ones too."
        " Before the Agent call, one call of the job tool adds the job and writes its brief, ops [{add: {kind, order,"
        " goal, part}}, {brief: {serves, done_when, where, ruling: [each answer id of his on the area]}}], and the"
        " Agent prompt is the line brief prints, Read your brief first: <path>, whose Drive line names the job: the"
        " agent's first Bash, Read, Write or Edit call, or its stop, records it on that job, its first after a"
        " SendMessage runs the job again, and the line it returns sets its state and said line, so no job set follows"
        " a start, a wake or any result; job set takes state stopped alone."
    ),
    (
        "Every event line, record and job result has its row in drive manual coordinator, and each drive verb with"
        " every flag it takes its row in drive manual verbs, which the order, job, pr, incident, heard and status tools run as ops"
        " in one call per tool: before acting on one these rules do not spell out, read"
        " that row alone, such as drive manual coordinator TAP or drive manual verbs \"job set\", never the whole page"
        " and never drive … --help."
    ),
    (
        "Scope: before any builder starts, a PR is a port of #N (pr set port_of, no tap card), an order of his"
        " (pr set tap, serves <o>, design asked:<his answer> or none, his words quoted in its ready line, never"
        " while a design card it carries is open), drive tooling, which is the communicator's and never yours: a tool"
        " defect you or a job hit is a defect record, which starts its builder, and you start"
        " none, or neither, which is not built, polished or carded: another lane's PR goes back to it, an unordered"
        " finding becomes a question card."
    ),
    (
        "Dev stays releasable: a PR lands only when a release cut at its merge commit is safe alone, in any order of"
        " targets, with no step after it such as a fill, a backfill, a hand command or another release first; its body's"
        " `## Releasable alone` says what that release does, and no card offers landing first and fixing after. Before a"
        " card or line names a tap, read status for the PR: a PR he tapped, merged or closed is never asked for again."
    ),
    (
        "Design first: a part with a tricky design (a data layout or file format, a schema, a lock or queue, an API"
        " shape, a new background process, any two shapes costly to switch; when unsure it counts) first gets one"
        " long-running:designer on Fable, which fleshes the design out with worked examples and keeps each fork as an"
        " ASK block, its Show me in code above the options; each fork is one question card. A builder may build the"
        " recommended option meanwhile and return `BUILT <branch>, waits on <each card>`, which sets its job built. It opens"
        " no PR until he answers them all: pr add refuses, and FREE says when."
    ),
    (
        "A ruling travels by its words: briefs, handoffs and SendMessage carry what he wrote and what it implies, never"
        " an answer id alone; an id or title you see after a compaction is read whole with cc-notes answer show first."
    ),
    (
        "An answer or ruling of his is recorded in one call, drive answer --title \"<the question>\" --words \"<his words>\""
        " --link <permalink>, which records it in cc-notes, prints its id, lists every card still asking him"
        " and every PR not yet live, and refuses until each is --stands,"
        " --superseded <key>=stop|<job>, which settles that card in place with his words and link, drops its PR's tap ask"
        " and stops or folds its job, or --reopened <PR>=<job> for a PR his words change, which that live job reworks to"
        " them; ANSWER repeats every 10 minutes until then."
    ),
    (
        "Every part has an owner: an order add takes kind build and parts naming each deliverable his words name, each"
        " job says its part, and an ORPHAN line gets a job, a card or a close in the same turn; a job waiting on a release"
        " gets drive job set <j> --waits release:<service>:<owner>/<repo>#<n>, never --due alone. A build order's first job"
        " is an Opus long-running:reader briefed Research: the Researched: block this build needs; its builder's or"
        " designer's brief carries Researched: <that reader's job>, which the job tool's brief op writes from the"
        " order's research, and a reader's DESIGN return starts the designer."
    ),
    "Never block: your own calls are the drive's tools, reads of cci and one gh call, beside the verbs the skill names.",
    (
        "Keep your context small: use Read, Write or Edit on no file and load a tool only for the step that needs it,"
        " since each file you touched last and each tool you loaded comes back after every compaction. Before you"
        " start a job, read the shape of its brief and handoff in drive manual jobs. The job tool's brief op writes a"
        " brief or handoff, and a remote job then starts with drive job launch <j> in the background; a message to a"
        " remote lane goes on stdin to drive job resume <j> -, from a quoted heredoc; a page of the skill is drive"
        " manual <page>; a log is a job's to read."
    ),
    "Never name an Agent: spawn each job as an unnamed background agent, never a named teammate.",
    (
        "A monorepo builder starts where drive place says: on a Sprite while this Mac is over its load limit,"
        " except a handoff whose PROVE runs ci mac-box or ci devbox, which needs this Mac's AWS sign-in."
    ),
    (
        "A promise is due: a time he is promised is recorded with order set due; DUE repeats every"
        " 10 minutes until a drive post is in the order's thread, and done or dropped refuses until then."
    ),
    (
        "Batch: job results and events feed the next TICK's report; while he is away only cards and incident lines go."
        " A report says what was observed: never a not-proven or not-confirmed paragraph under any label and never a"
        " side fact; each unknown gets a job, and he gets its result or nothing."
    ),
)
RULES = {"communicator": COMMUNICATOR_RULES, "coordinator": COORDINATOR_RULES}
SUBSCRIBE = {
    "communicator": "cci subscribe --reader communicator --cursor communicator",
    "coordinator": (
        "cci subscribe --reader root --cursor coordinator --to-kind incident,mechanism,fix-live,decide,ask,design,"
        "blocker,blocked,urgent,answer,release,correction,owner,refuse,handoff --batch-kinds done,evidence"
    ),
}
OTHER = {"communicator": "coordinator", "coordinator": "communicator"}
JOB = {"communicator": "you talk to him", "coordinator": "you run his orders, jobs, events, landings and installs"}
REPROS_SHOWN = 5
WHOLE = sys.maxsize
UNLISTED_TOOL_LINE = (
    "A drive tool these rules name that your session does not list, such as heard or job, is one its drive MCP server"
    " started before: run that verb in Bash as drive <verb>, each field its flag, until /reload-plugins or a restart"
    " lists it."
)
FIRST_CALL = {
    "communicator": (
        "Your first call now, before anything else, loads the tools your turn needs: ToolSearch"
        " select:mcp__plugin_cc-slack_cc-slack__slack_dm_status,"
        "mcp__plugin_cc-slack_cc-slack__slack_reply,mcp__plugin_cc-slack_cc-slack__slack_react,"
        "mcp__plugin_long-running_drive__heard,mcp__plugin_long-running_drive__record,mcp__plugin_long-running_drive__job"
    ),
    "coordinator": (
        "The watch runs as its own process and posts its lines to you over cci: a line you act on now as a handoff"
        " record, the rest as evidence records together at most every five minutes; arm no Monitor for it, and run"
        " drive watch only when a WATCH stopped line says it ended. A compaction keeps every tool you"
        " loaded before it, even one the deferred-tools list names, so call Monitor, SendMessage, TaskStop and"
        " answer_add with no ToolSearch first. The drive's order, job, pr, incident and status tools load once, the first"
        " time you reach for one: ToolSearch select:mcp__plugin_long-running_drive__order,mcp__plugin_long-running_drive__job,"
        "mcp__plugin_long-running_drive__pr,mcp__plugin_long-running_drive__incident,mcp__plugin_long-running_drive__status."
    ),
}
READS = {
    "communicator": "Everything else is on disk, read by a handler on demand, never by you: drive status <id>, drive view.",
    "coordinator": (
        "Everything else is on disk, read on demand by id: drive status <order, job or PR>..., which prints each"
        " named order whole with its parts and each job's goal and said line, never a script over drive.json;"
        " drive job report <j>, drive view, cci digest, and the skill's pages, drive manual <page>. A Bash result over"
        " 6,000 characters arrives as its first 2 KB and the path of the whole, so cap no output with cut, head or tail:"
        " grep that path for the part you need."
    ),
}
KEEP = {
    "communicator": (
        "Each handler still out: one line, `<his thread ts> <agent id> <his ask in at most 12 words>`.",
        "Each message of his acked with no handler started yet: `<his thread ts> <permalink> <his words verbatim>`.",
        "Each builder of yours still out: one line, `<job> <agent id> <what it fixes in at most 12 words>`.",
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


def waiting_line(entry: dict) -> str:
    """The line that tells him a card of his holds a built job, its card a reference the post links."""
    card = entry["card"]
    named = f"{{card:{card['card']['waits']}}}" if card["card"].get("waits") else card["url"]
    return f"WAITING-ON-HIM {named} blocks {entry['job']}, built on {entry['branch']}, since {when(card['asked'])}"


def job_prs(drive: dict, job_id: str) -> dict[str, dict]:
    return {key: pr for key, pr in drive["prs"].items() if pr["job"] == job_id}


def order_line(order_id: str, order: dict, limit: int = 120, thread: tuple[str, str] | None = None) -> str:
    due = f" due {when(order['due'])}" if order.get("due") else ""
    where = f", its lines go in {thread[0]} thread {thread[1]}" if thread else ""
    return f"{order_id} {order['state']}{due}{where}: {cut(order['text'], limit)}"


def pr_line(key: str, pr: dict) -> str:
    tap = pr["tap"]["label"] if pr.get("tap") else ("wants tap" if pr["wants_tap"] else "no tap needed")
    exempt = f", exempt: {pr['exempt']}" if pr.get("exempt") else ""
    port = f" of #{pr['port_of']}" if pr.get("exempt") == "pure port" and pr.get("port_of") else ""
    waits = f", waits on {', '.join(pr['waits'])}" if pr.get("waits") else ""
    title = f' "{cut(pr["title"], 80)}"' if pr["title"] else ""
    return f"{key}{title} {pr['state']}, checks {pr['checks']}, review {pr['review']}, {tap}{exempt}{port}{waits}"


def job_line(job_id: str, job: dict, limit: int = 100) -> str:
    if job.get("where") == "remote":
        handle = f" remote dispatch {job['dispatch']}" if job.get("dispatch") else " remote, not started"
    else:
        handle = f" agent {job['agent']}" if job.get("agent") else ""
    repro = f"; repro {job['repro']}" if job.get("repro") else ""
    carries = f"; {job['order']}'s job, carrying " + ", ".join(job["carries"]) if job.get("carries") else ""
    built = f" on {job['branch']}" if job["state"] == "built" else ""
    waits = f"; waits on {', '.join(job['waits'])}" if job.get("waits") else ""
    return f"{job_id} {job['kind']} {job['state']}{built}{handle}: {cut(job['goal'], limit)}{repro}{carries}{waits}"


def question_line(question: dict) -> str:
    drawn = question["card"] or question["waits"]
    card = f"card {drawn}" if drawn else "no card drawn yet"
    waits = f" ({question['waits']})" if question["card"] and question["waits"] else ""
    kept = f", kept in {question['file']}" if question["file"] else ""
    return f"asks him: {cut(question['title'] or 'his question card', 80)}; {card}{waits}{kept}"


def job_pr_lines(drive: dict, job_id: str, job: dict) -> list[str]:
    keys = dict.fromkeys([*map(pr_key, job["prs"]), *job_prs(drive, job_id)])
    return [pr_line(key, drive["prs"][key]) if key in drive["prs"] else pr_url(key) for key in keys]


FEED_SUBSCRIPTION = "cc-slack subscribe --kind alerts,releases,posts --channel <id>"


def held_channels(drive: dict, role: str) -> list[str]:
    slack = drive["slack"]
    if role == "communicator":
        return [f"his DM {slack['channel']}" if slack["channel"] else "no DM set"]
    return slack.get("watch", [])


def hold_line(drive: dict, role: str) -> str:
    channels = held_channels(drive, role)
    if not channels:
        return "Hold no Slack channel."
    tool = "slack_watch" if role == "communicator" else FEED_SUBSCRIPTION
    return f"Hold with {tool}: {', '.join(channels)}"


def who(drive: dict, role: str) -> str:
    held = drive["roles"].get(role)
    return f"session {held['session']}" if held else "not started"


def role_line(drive: dict, role: str, seen: dict[str, str]) -> str:
    place = f", {seen[role]}" if role in seen else ""
    return f"{role}: {who(drive, role)}{place}; {hold_line(drive, role)}"


def order_block(drive: dict, order_id: str, order: dict, whole: bool) -> list[str]:
    limit = WHOLE if whole else 80
    lines = [f"- {order_line(order_id, order, limit, promise_thread(drive, order))}"]
    if whole:
        lines.append(f"  parts: {'; '.join(order['parts'])}")
    for job_id in order["jobs"]:
        job = drive["jobs"][job_id]
        lines.append(f"  - {job_line(job_id, job, limit)}")
        if whole and job["said"]:
            lines.append(f"    said: {cut(job['said'], WHOLE)}")
        lines.extend(f"    - {question_line(question)}" for question in job["questions"])
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


def filed_line(drive: dict) -> str:
    """The projects and workstreams the drive's orders are filed under, which a new order reuses where it fits."""
    filed: dict[str, list[str]] = {}
    for order in drive["orders"].values():
        if order["project"] and order["workstream"] and order["workstream"] not in filed.setdefault(order["project"], []):
            filed[order["project"]].append(order["workstream"])
    return "Projects in use, each with its workstreams: " + (
        "; ".join(f"{project}: {', '.join(workstreams)}" for project, workstreams in filed.items()) or "none yet"
    )


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
        filed_line(drive),
    ]
    if role == "coordinator":
        lines.extend(owed_lines(drive))
    lines += [
        (
            f"This block comes from long-running {json.loads(PLUGIN_JSON.read_text())['version']}, installed now. The"
            " long-running skill text Claude Code brings back after a compaction is the one this session first loaded,"
            " at the version its base directory names, and no install since replaces it: where that text disagrees with"
            " this block, this block wins."
        ),
        UNLISTED_TOOL_LINE,
        READS[role],
        FIRST_CALL[role],
    ]
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


def status_data(drive: dict, pr_fields: str, seen: dict[str, str], named: bool = False) -> dict:
    orders = drive["orders"] if named else open_orders(drive)
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


def status_text(drive: dict, pr_fields: str, seen: dict[str, str], named: bool = False) -> str:
    """The drive page; with ids, only the orders they name, each whole, open or closed, under one summary line."""
    lines = [f"{drive['name']}: {len(open_orders(drive))} open of {len(drive['orders'])} orders; PR fields {pr_fields}"]
    if named:
        lines.extend(line for order_id, order in drive["orders"].items() for line in order_block(drive, order_id, order, True))
        return "\n".join(lines)
    lines.extend(role_line(drive, role, seen) for role in ROLES)
    for order_id, order in open_orders(drive).items():
        lines.extend(order_block(drive, order_id, order, False))
    repros = sorted(
        ((job["updated"], job_id, job) for job_id, job in drive["jobs"].items() if job.get("repro")), reverse=True
    )[:REPROS_SHOWN]
    if repros:
        lines.append("Reproduction files, newest first:")
        lines.extend(f"- {job_id} ({job['order']}): {job['repro']}" for _, job_id, job in repros)
    return "\n".join(lines)
