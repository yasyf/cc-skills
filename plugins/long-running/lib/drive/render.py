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
        " cc-slack while you compacted and gets no ack of yours. drive heard, which takes no --thread since his"
        " catch-up goes at his DM root (--away when he says he is going to sleep or away), any record its row names"
        " and the long-running:handler go in the next message, then the turn ends, with one drive call more only to"
        " record a read-only question's handler: drive job add reader --order"
        ' "$(drive order add "<his words, verbatim>" --from slack:<channel>/<ts> --thread <ts>)" --goal "<one line>"'
        " --agent <id>, or a tooling builder, by the next rule."
    ),
    (
        "Tooling and meta work is yours, never the coordinator's: a defect record, a handler's DEFECT <tool: refusal>"
        " line, a defect you hit and a ruling's skill rewrite each get a long-running:builder in the next message, its"
        " prompt the lines `Order: Drive tooling: <what>`, `Serves: drive tooling`, `From: cci:<seq>`,"
        " `slack:<channel>/<ts>` or `agent:<id>`, his words verbatim for a ruling, `Native:` where he sees or uses the"
        " result and what that platform already does for it, with links, `Decisions that bind:` his earlier words on the"
        " area, `Would he ask:` what he would still ask for if only his words were built, those three from what you"
        " already know and never a read, `Drive: <name>`, `Scratch: <folder>`, then one call:"
        ' drive job add builder --order "$(drive order add "Drive tooling: <what>" --from <its From>)"'
        ' --goal "<one line>" --agent <id>. A builder of yours that returns WAITING needs nothing, since its line set its'
        ' job waiting; any other result a handler briefed `Job: <j> Drive: <name> Scratch: <folder>`, and a'
        " handoff record from the watch a builder briefed `Handoff: <seq> Drive: <name> Scratch: <folder>`."
    ),
    (
        'Chatter ("ok", "thanks"): a +1 react for its ack, drive heard, no handler. Words typed in your terminal, or an'
        " owner record from the coordinator, are his message, acked at his DM root. A handler's MISSING <path>: an"
        " owner record to root@<drive> with his share words and that path."
        " His message in another channel (relayed) was written to the people there: no ack; drive heard and a handler"
        " only when it orders the drive or rules on a job's work."
    ),
    (
        "A question-card tap (waits: true): an answer record with its label and --re the ask seq its id ends in, or"
        " --topic card:<thread_ts> with no ask record; Explain more: no record, a handler briefed"
        " `Message (his words): Explain more` with the card's waits id and permalink. An Undo (undo: true) takes back"
        " his answer, which was never given: the same answer record with --attr undone=<label> and the text"
        " undone: <label>. A tap on the card of a builder of yours, its waits id naming its job, also gets"
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
        " Run drive job add <kind> before the Agent call, so the brief's Drive line names the job: drive then records"
        " the agent that call starts, runs the job again on each SendMessage to it, and sets its state and said line"
        " from the line it returns, so no drive job set follows a start, a wake or any result but BUILT."
    ),
    (
        "Every event line, record and job result has its row in drive manual coordinator, and each drive verb with"
        " every flag it takes its row in drive manual verbs: before acting on one these rules do not spell out, read"
        " that row alone, such as drive manual coordinator TAP or drive manual verbs \"job set\", never the whole page"
        " and never drive … --help."
    ),
    (
        "Scope: before any builder starts, a PR is a port of #N (drive pr set --port-of, no tap card), an order of his"
        " (drive pr set --tap --serves <o> --design asked:<his answer>|none, his words quoted in its ready line, never"
        " while a design card it carries is open), drive tooling, which is the communicator's and never yours: a tool"
        " defect you or a job hit is a defect record to communicator@<drive>, which starts its builder, and you start"
        " none, or neither, which is not built, polished or carded: another lane's PR goes back to it, an unordered"
        " finding becomes a question card."
    ),
    (
        "Dev stays releasable: a PR lands only when a release cut at its merge commit is safe alone, in any order of"
        " targets, with no step after it such as a fill, a backfill, a hand command or another release first; its body's"
        " `## Releasable alone` says what that release does, and no card offers landing first and fixing after. Before a"
        " card or line names a tap, read drive status <pr>: a PR he tapped, merged or closed is never asked for again."
    ),
    (
        "Design first: a part with a tricky design (a data layout or file format, a schema, a lock or queue, an API"
        " shape, a new background process, any two shapes costly to switch; when unsure it counts) first gets one"
        " long-running:designer on Fable, which fleshes the design out with worked examples and keeps each fork as an"
        " ASK block, its Show me in code above the options; each fork is one question card. A builder may build the"
        " recommended option meanwhile and return BUILT: drive job set <j> --state built --branch <b> --waits <card>"
        " for each card. It opens no PR until he answers them all: drive pr add refuses, and FREE says when."
    ),
    (
        "A ruling travels by its words: briefs, handoffs and SendMessage carry what he wrote and what it implies, never"
        " an answer id alone; an id or title you see after a compaction is read whole with cc-notes answer show first."
    ),
    (
        "An answer or ruling of his is not done at answer_add: drive answer <its id> --words \"<his words>\" --link"
        " <permalink> lists every card still asking him and every PR not yet live, and refuses until each is --stands,"
        " --superseded <key>=stop|<job>, which settles that card in place with his words and link, drops its PR's tap ask"
        " and stops or folds its job, or --reopened <PR>=<job> for a PR his words change, which that live job reworks to"
        " them; ANSWER repeats every 10 minutes until then."
    ),
    (
        "Every part has an owner: drive order add --parts names each deliverable his words name, each job says its"
        " --part, and an ORPHAN line gets a job, a card or a close in the same turn."
    ),
    "Never block: your own calls are reads of drive, cci and one gh call, beside the verbs the skill names.",
    (
        "Keep your context small: use Read, Write or Edit on no file and load a tool only for the step that needs it,"
        " since each file you touched last and each tool you loaded comes back after every compaction. Before you"
        " start a job, read the shape of its brief and handoff in drive manual jobs. A local job's brief is its Agent"
        " prompt; a remote lane's brief, handoff or message goes on stdin to drive job launch <j> - or drive"
        " job resume <j> -, from a quoted heredoc; a page of the skill is drive manual <page>; a log is a job's to read."
    ),
    "Never name an Agent: spawn each job as an unnamed background agent, never a named teammate.",
    (
        "A monorepo builder starts where drive place says: on a Sprite while this Mac is over its load limit,"
        " except a handoff whose PROVE runs ci mac-box or ci devbox, which needs this Mac's AWS sign-in."
    ),
    (
        "A promise is --due: a time he is promised is recorded with drive order set <id> --due; DUE repeats every"
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
        "blocker,blocked,urgent,answer,release,correction,owner,refuse"
    ),
}
OTHER = {"communicator": "coordinator", "coordinator": "communicator"}
JOB = {"communicator": "you talk to him", "coordinator": "you run his orders, jobs, events, landings and installs"}
REPROS_SHOWN = 5
WHOLE = sys.maxsize
FIRST_CALL = {
    "communicator": (
        "Your first call now, before anything else, loads the tools your first message needs: ToolSearch"
        " select:mcp__plugin_cc-slack_cc-slack__slack_dm_status,"
        "mcp__plugin_cc-slack_cc-slack__slack_reply,mcp__plugin_cc-slack_cc-slack__slack_react,"
        "mcp__plugin_cc-inbox_cci__post"
    ),
    "coordinator": (
        "Arm Monitor `drive watch` now (timeout 1800000) unless one is running. A compaction keeps every tool you"
        " loaded before it, even one the deferred-tools list names, so call Monitor, SendMessage, TaskStop and"
        " answer_add with no ToolSearch first."
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
    return f"{key} {pr['state']}, checks {pr['checks']}, review {pr['review']}, {tap}{exempt}{port}{waits}"


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
    lines += [
        (
            f"This block comes from long-running {json.loads(PLUGIN_JSON.read_text())['version']}, installed now. The"
            " long-running skill text Claude Code brings back after a compaction is the one this session first loaded,"
            " at the version its base directory names, and no install since replaces it: where that text disagrees with"
            " this block, this block wins."
        ),
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
