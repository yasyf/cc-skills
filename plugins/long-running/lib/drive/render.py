import shlex
import sys
from datetime import datetime

from drive.state import DRIVE_TOOLING, ROLES, VERSION, agent_gone, lane_death, now, open_orders, owed_step, parse_time, pr_key, pr_url, project_lane, promise_thread, title

SLACK_TOOLS = "mcp__plugin_cc-slack_cc-slack__"
DRIVE_TOOLS = "mcp__plugin_long-running_drive__"
COMMUNICATOR_SLACK = ("slack_dm_status", "slack_reply", "slack_react", "slack_watch")
COMMUNICATOR_DRIVE = ("say", "heard", "handler", "record", "order", "job")
OWN_CALLS = (
    "the ack, a +1, the watch on his DM, SendMessage, ToolSearch, this skill, the drive's say, heard, handler,"
    " record, order and job tools, and a drive, cci or cc-slack command in Bash"
)
EYES = (
    "cc-slack puts the eyes and Slack's working line on his message itself the moment it lands, so the communicator"
    " never calls slack_pickup."
)
REACHING = (
    "Post, read and close records with the cci MCP tools while that server is up, `cci post` only while it is gone;"
    " while cci is down, SendMessage `<drive>-<role>` after ListAgents: a disconnect never excuses a message."
)
PREVENTED = "A line telling him the drive failed says on that line how that is prevented: the tool change and its order or PR."
HANDLER_LABELS = ("Record", "Message (his words)", "Link", "Thread", "Drive", "Paths", "Scratch", "Deliverable", "Context", "Waits")
COMMUNICATOR_RULES = (
    (
        "He never reads this terminal, which has no AskUserQuestion: everything for him goes to Slack, every message of"
        " your turn is tool calls alone, and the turn ends on the one word Sent."
    ),
    (
        "When he writes, your first message is the ack alone, inside 5 to 10 seconds, since a call beside it holds it"
        " back: one sentence you already know, saying what he asked and what happens next. "
        + EYES
        + " It also acks any message carrying acked while you compacted, which then gets none from you."
    ),
    (
        "An ack, an owner record and a handler's prompt carry his words as written, adding nothing he did not state,"
        " since your paraphrase becomes the spec. An ack names things by their proper technical terms, never a"
        ' stand-in, states only what this turn starts, and promises no time or "from now on" that nothing carries; a'
        " follow-up he orders to a PR starts beside landing that PR, so the ack never puts it after the landing. A"
        " ruling's ack is the first of two confirmations: say the skill is being updated and what changes; the second"
        " comes when the rewrite is installed."
    ),
    (
        "Your next message is the drive's handler tool (away: true when he says he is going to sleep or away), which"
        " records that he wrote and starts the long-running:handler as its own claude --bg session, returning started"
        " <its id> and nothing to act on; the handler posts his owner record for an order, alert or ruling, a question"
        ' proposing work such as "why not just X?" included, and none for a read-only question, which asks for facts,'
        " since the coordinator answers any question it hears. Then the turn ends, but for one"
        " call more for a read-only question, so the watch holds his answer: the job tool with ops `[{add: {kind:"
        ' "reader", order: {words: "<his words, verbatim>", kind: "answer", complex: true, from:'
        ' "<his message\'s permalink>", project: "<the project in use his words are about>", workstream:'
        ' "<its workstream>", title: "<a few words naming it>"}, goal: "<one line>", agent: "<the id handler returned>"}}]`, the permalink naming his thread, leaving out complex only for'
        " a one-fact lookup."
    ),
    (
        'Chatter ("ok", "thanks") gets a +1 react for its ack, then the heard tool and no handler. Words typed in your'
        " terminal, and an owner record from the coordinator, are his message, acked at his DM root. His message in"
        " another channel (relayed) was written to the people there: no ack, and the handler tool only when it orders"
        " the drive or rules on a job's work."
    ),
    (
        "A handler's line comes back as one cci done record from lane handler when its session ends: MISSING <path>"
        " gets an owner record with his share words and that path; `DEFECT <tool>: <refusal>` is tooling, below; a line"
        " ending `held back for this handler, start each now: <briefs>` gets one handler call per brief, each a record"
        " on the message of his that handler was at work on, and a held-back report or card he waits on is a defect"
        " too; any other line is context for his next message."
    ),
    (
        "A question-card answer (slack.action with waits: true), whatever its waits id, one naming no job included,"
        " whether he tapped it, typed its number in its thread or wrote in its Other box, and an Undo (undo: true) get"
        " no record and no handler: cc-slack closed the card, and its card command posted his answer record, `undone:`"
        " for an Undo. A payload without `recorded: true` is a card command that missed it: one defect record quoting"
        " the payload, never an answer record of yours, which drive record refuses. Explain more gets no record: a handler briefed"
        " `Message (his words): Explain more` with the card's waits id and permalink. A PR-card tap (merge:) gets"
        " nothing, since cc-slack records it."
    ),
    (
        "Nothing else runs in your turn, since work there holds his next message behind it: every report, card, due"
        " line, draft, check-post, read of any kind and anything taking more than one call runs in a handler the drive's"
        " handler tool starts, which posts its own result. A POST report, a ready record and an ask record each get one"
        " handler, its brief the one line `Record: <seq> Drive: <name> Scratch: <folder>`; an `FYI` report gets none. A record's body and"
        " any path it names are the handler's to read. Your own calls are "
        + OWN_CALLS
        + ", and a hook refuses any other; load no skill and no tool past those below, since each comes back after"
        " every compaction."
    ),
    (
        "A handler's brief is a few lines, each opening with one of "
        + ", ".join(f"{label}:" for label in HANDLER_LABELS)
        + ", carrying his words verbatim, the permalink, the thread, its scratch folder, the deliverable and any path you know"
        " that answers it, retyping no rule, since its agent definition is its standing brief, and the handler tool"
        " refuses any other line. A handler runs on its definition's Opus, since a ready record draws his PR card and his"
        ' words, research, an explanation, a design, a question card and any judgment call keep Opus: "for more complex'
        ' use opus from the start".'
    ),
    (
        "His feedback on a post or card the drive made (its words, a picture, a link or card it lacks, \"what is"
        ' this"), wherever it lands, a card\'s thread included, gets its handler, which fixes that one post and adds'
        " the order `Drive tooling: post feedback: \"<his words>\"` for the meta session, a repeat too. Its ack says"
        " that post and the code that let it through are being fixed."
    ),
    (
        f"Drive tooling, {DRIVE_TOOLING}, is the meta session's alone, never yours or the coordinator's, so you start no"
        " reader, designer or builder for it: a handler's DEFECT line and a defect you hit, a hook refusing wrongly or a"
        " refusal or queue holding back work he waits on included, each get one record call that turn, kind defect, with"
        " the refusal verbatim, never read as working as designed, and every owner record names about, drive for drive"
        " tooling, his ruling or feedback on it included, and his work for the rest; the record tool sends a defect and an"
        " owner record about the drive to the meta session alone, adding first the order its order field names, which tells"
        " the coordinator what changes for its work,"
        " and a handoff, a step only the session holding an order takes on it, to that session by its order attr."
    ),
    REACHING,
    (
        "After a compaction or a restart, your first call is the `ToolSearch` below; then one handler posts a line in"
        " each thread where he waits on a handler whose line has not come back: what is happening and when he hears next."
    ),
    "Post nothing on your own clock: summaries come as reports, and while he is away only cards post.",
)
COORDINATOR_RULES = (
    (
        "You have no AskUserQuestion: everything he sees goes to communicator@<drive> as a report, ready or ask record,"
        " since his reply under a post"
        " reaches only the session that posted it. Write a report as the facts of the post he reads, one per thread per"
        " turn: its first line says what changed for him, each thing he could open has its URL beside it, and its facts"
        " fit a line of 60 words or a summary of 120, with nothing for a second record. A POST names what it"
        " finishes, --attr close=<o>, part=<o>:<part> or progress=true, and carries every part of its order a finished job"
        " answered and no post delivered. What he reads names things by their proper"
        " technical terms, never a stand-in, and no drive id he cannot open. A job's start is {started:<job>} and a"
        " record's time {at:<seq>}, never a clock time you type, and neither is when a release, build or deploy started,"
        " which is quoted as its build or tool printed it, in backticks or its link's words, on the line linking it. "
        + PREVENTED
    ),
    REACHING,
    (
        "Every order goes to one background job that owns its deliverable end to end, past-due ones too, since work you"
        " do yourself holds every event behind it: read the shape of its brief and handoff in drive manual jobs before"
        " you start it. Load a tool only for the step that needs it, and leave a log to a job."
    ),
    (
        "Every event line, record and job result has its row in drive manual coordinator, and each verb and flag its"
        " row in drive manual verbs: before acting on one, read that one row with drive manual <page> <row>, never a"
        " whole page, a cached copy or --help, since a rule for one step lives only in its row."
    ),
    (
        "Drive tooling is the meta session's, never yours: a tool defect you or a job hit, a hook refusing wrongly or a"
        " refusal or queue holding back work he waits on, is one record call that turn, kind defect, with the refusal"
        " verbatim, never read as working as designed, which reaches it."
    ),
    (
        "A ruling travels by its words: a brief, handoff or `SendMessage` carries what he wrote verbatim to each live"
        " job it touches, your reading marked as yours, never an answer id alone; an id or title seen after a"
        " compaction is read whole with `cc-notes answer show` first. It binds only what his words name; carrying it to"
        " another surface is a question card first."
    ),
    (
        "Never block: your own calls are the drive's tools, cci reads, a job's transcript, one gh call and the verbs"
        " the skill names, never a Slack read, which is a job's. Read a job's transcript before you call it silent, stuck or done, since a state you guess"
        " reaches him as fact."
    ),
    (
        "A report says only what was observed, with no side fact and no unproven or unconfirmed paragraph under any"
        " label; each unknown gets a job, and he gets its result or nothing."
    ),
)
META_RULES = (
    (
        "He never reads this terminal, which has no AskUserQuestion, and you post nothing to Slack, since his reply"
        " under a post reaches only the session that posted it: what he should see goes to communicator@<drive> as a report record opening POST, its"
        " first line what changed for him and each thing he could open with its URL beside it, naming what it finishes"
        " (--attr close=<o> on the line that finishes a tooling order, thread or none, else --attr progress=true), and a"
        " question for him as an ask record; the communicator's handler posts both. "
        + PREVENTED
    ),
    REACHING,
    (
        f"You hold drive tooling alone, {DRIVE_TOOLING}, each piece of work a background job you start, and each change"
        " folds a new check, step, hook, flag or verb into what exists, or deletes one, before adding one, by his words"
        ' "in geernal keeps implifying all this tuff", which every tooling brief\'s Decisions that bind carries. You never'
        ' touch his product orders, their jobs or PRs: drive order add refuses you an order not opening "Drive tooling:", and job'
        " add an order the coordinator holds."
    ),
    (
        "Every record and job result has its row in drive manual meta: before acting on one, read that row alone with"
        " drive manual meta <row>, since a rule for one step lives only in its row. Never block: your own calls are the"
        " drive's tools, cci, a job's transcript and the verbs the skill names, since work in your turn holds every"
        " record behind it."
    ),
)
RULES = {"communicator": COMMUNICATOR_RULES, "coordinator": COORDINATOR_RULES, "meta": META_RULES}
COORDINATOR_KINDS = (
    "--to-kind incident,mechanism,fix-live,decide,ask,design,blocker,blocked,urgent,answer,release,correction,owner,refuse,handoff"
    " --batch-kinds done,evidence"
)
SUBSCRIBE = {
    "communicator": "cci subscribe --reader communicator --cursor communicator",
    "meta": "cci subscribe --reader meta --cursor meta",
    "coordinator": f"cci subscribe --reader root --cursor coordinator {COORDINATOR_KINDS}",
}
OTHERS = {"communicator": ("coordinator", "meta"), "coordinator": ("communicator", "meta"), "meta": ("communicator", "coordinator")}
JOB = {
    "communicator": "you talk to him",
    "coordinator": "you run his orders, jobs, events, landings and installs",
    "meta": f"you hold drive tooling, {DRIVE_TOOLING}",
}
REPROS_SHOWN = 5
FILED = "The projects and workstreams in use, which a new order reuses where it fits: drive order unfiled lists each pair."
WHOLE = sys.maxsize
UNLISTED_TOOL_LINE = (
    "A drive tool these rules name that your session does not list, such as handler or job, is one an install added"
    " since its drive MCP server last listed its tools: pipe the tool's fields as JSON to drive tool <name> in Bash"
    " until a call of any drive tool relists them."
)
FIRST_CALL = {
    "communicator": (
        "Your first call now, before anything else, loads the tools your turn needs: ToolSearch"
        " select:mcp__plugin_cc-slack_cc-slack__slack_dm_status,"
        "mcp__plugin_cc-slack_cc-slack__slack_reply,mcp__plugin_cc-slack_cc-slack__slack_react,"
        "mcp__plugin_long-running_drive__heard,mcp__plugin_long-running_drive__handler,mcp__plugin_long-running_drive__record,"
        "mcp__plugin_long-running_drive__job,"
        "mcp__plugin_long-running_drive__order"
    ),
    "coordinator": (
        "The watch runs as its own process and posts its lines to you over cci: a line you act on now as a handoff"
        " record, the rest as evidence records together at most every five minutes; arm no Monitor for it, and run"
        " the drive watch command a WATCH stopped line names only when it says the watch ended. A compaction keeps"
        " every tool you loaded before it, even one the deferred-tools list names, so call Monitor, SendMessage, TaskStop and"
        " answer_add with no ToolSearch first. The drive's order, job, pr, incident and status tools load once, the first"
        " time you reach for one: ToolSearch select:mcp__plugin_long-running_drive__order,mcp__plugin_long-running_drive__job,"
        "mcp__plugin_long-running_drive__pr,mcp__plugin_long-running_drive__incident,mcp__plugin_long-running_drive__status."
    ),
    "meta": (
        "The drive's order, job, pr, record and status tools load once, the first time you reach for one: ToolSearch"
        " select:mcp__plugin_long-running_drive__order,mcp__plugin_long-running_drive__job,mcp__plugin_long-running_drive__pr,"
        "mcp__plugin_long-running_drive__record,mcp__plugin_long-running_drive__status."
    ),
}
READS = {
    "communicator": "Everything else is on disk, read by a handler on demand, never by you: drive status <id>, drive view.",
    "coordinator": (
        "Everything else is on disk, read on demand by id: drive status <order, job or PR>..., never a script over drive.json;"
        " drive job report <j>, drive view, cci digest, and the skill's pages, drive manual <page>. A Bash result over"
        " 6,000 characters arrives as its first 2 KB and the path of the whole, so cap no output with cut, head or tail:"
        " grep that path for the part you need."
    ),
    "meta": (
        "Everything else is on disk, read on demand by id: drive status <order, job or PR>..., drive job report <j>, cci,"
        " and the skill's pages, drive manual <page> <row>, your own rows in drive manual meta."
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
        "The step you were in the middle of, in one line, with the `drive manual <page> <row>` that states it.",
    ),
    "meta": (
        "Each agent of yours still out: one line, `<job> <agent id> <what it fixes in at most 12 words>`.",
        "Each record you took whose job call has not run yet: `<seq> <what it asks in at most 12 words>`.",
        "The step you were in the middle of, in one line, with the `drive manual <page> <row>` that states it.",
    ),
}
KEEP_LIMIT = {"communicator": 1500, "coordinator": 3000, "meta": 1500}


def cut(text: str, limit: int) -> str:
    """The text on one line, at most limit characters, ending on a whole word and … when it was longer."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    head = flat[: limit - 1]
    return (head if flat[limit - 1] == " " else head.rsplit(" ", 1)[0]).rstrip(" ,;:") + "…"


def clock(moment: datetime, seconds: bool = False) -> str:
    return moment.strftime("%-I:%M:%S %p" if seconds else "%-I:%M %p")


def when(iso: str, seconds: bool = False) -> str:
    moment = parse_time(iso)
    if moment.date() == now().date():
        return clock(moment, seconds)
    return f"{moment.strftime('%b %-d')} {clock(moment, seconds)}"


def corrected_by(seqs: list[str]) -> str:
    return f", corrected by record{'s' if len(seqs) > 1 else ''} {', '.join(seqs)}" if seqs else ""


def job_prs(drive: dict, job_id: str) -> dict[str, dict]:
    return {key: pr for key, pr in drive["prs"].items() if pr["job"] == job_id}


def order_line(order_id: str, order: dict, limit: int = 120, thread: tuple[str, str] | None = None) -> str:
    due = f" due {when(order['due'])}" if order.get("due") else ""
    where = f", its lines go in {thread[0]} thread {thread[1]}" if thread else ""
    return f"{order_id} {order['state']}{due}{where}{reporter_said(order)}: {cut(order['text'], limit)}"


def reporter_said(order: dict) -> str:
    """Where the bug the order fixes was reported, and whether that thread heard it is fixed, is owed it now its fix is proven live, or hears it once it is."""
    if (reporter := order["reporter"]) is None:
        return ""
    if reporter["told"] is None:
        owed = "owed its fix-live note now, since its fix is proven live" if order["proof"] else "which hears it is fixed once its fix is proven live"
        return f", reported in {reporter['link']}, {owed}"
    return f", reported in {reporter['link']}, told it is fixed in {reporter['told']}"


def incident_line(order_id: str, order: dict) -> str:
    return f"{order_id} {order['state']} incident: {order['text'][:80]}; {order['note'] or 'no record'}"


def agentless_lines(drive: dict) -> list[str]:
    """Each running job on this Mac whose session no role holds any more, since its agent ended with that session; a fresh agent on its brief takes it over at its first step."""
    return [
        f"{job_id} running with no live agent, its session gone: start a fresh one with drive job brief {job_id}"
        for job_id, job in drive["jobs"].items()
        if job["state"] == "running" and job.get("where") != "remote" and agent_gone(drive, job)
    ]


def pr_line(key: str, pr: dict, card: str | None) -> str:
    owed = {"card": "wants tap", "reworking": "no card while its builder reworks it to his words, until its READY says how they are met"}
    step = None if pr.get("tap") else owed_step(pr)
    tap = pr["tap"]["label"] if pr.get("tap") else owed.get(step) or "no tap needed"
    tap += f", {card or 'his card never drawn'}" if card or step == "card" else ""
    exempt = f", exempt: {pr['exempt']}" if pr.get("exempt") else ""
    port = f" of #{pr['port_of']}" if pr.get("exempt") == "pure port" and pr.get("port_of") else ""
    waits = f", waits on {', '.join(pr['waits'])}" if pr.get("waits") else ""
    held = ", held by its hold label" if pr["hold_label"] else ""
    title = f' "{cut(pr["title"], 80)}"' if pr["title"] else ""
    return f"{key}{title} {pr['state']}, checks {pr['checks']}, review {pr['review']}, {tap}{held}{exempt}{port}{waits}"


def job_line(job_id: str, job: dict, limit: int = 100) -> str:
    if job.get("where") == "remote":
        handle = f" remote dispatch {job['dispatch']}" if job.get("dispatch") else " remote, not started"
    else:
        handle = f" agent {job['agent']}" if job.get("agent") else ""
    repro = f"; repro {job['repro']}" if job.get("repro") else ""
    carries = f"; {job['order']}'s job, carrying " + ", ".join(job["carries"]) if job.get("carries") else ""
    built = f" on {job['branch']}" if job["state"] == "built" else ""
    waits = f"; waits on {', '.join(job['waits'])}" if job.get("waits") else ""
    shown = f"lane dead since {when(dead['at'])} ({dead['why']})" if job["state"] == "running" and (dead := lane_death(job)) else job["state"]
    return f"{job_id} {job['kind']} {shown}{built}{handle}: {cut(job['goal'], limit)}{repro}{carries}{waits}"


def question_line(question: dict) -> str:
    card = f"refused, so no card draws: {cut(question['refused'].removeprefix('refused: '), 120)}" if question["refused"] else question["card_state"]
    waits = f" ({question['waits']})" if question["card"] and question["waits"] else ""
    kept = f", kept in {question['file']}" if question["file"] else ""
    return f"asks him: {cut(question['title'] or 'his question card', 80)}; {card}{waits}{kept}"


def job_pr_lines(drive: dict, job_id: str, job: dict) -> list[str]:
    keys = dict.fromkeys([*map(pr_key, job["prs"]), *job_prs(drive, job_id)])
    return [pr_line(key, drive["prs"][key], drive["prs"][key]["card_state"]) if key in drive["prs"] else pr_url(key) for key in keys]


FEED_SUBSCRIPTION = "cc-slack subscribe --kind alerts,releases,posts --channel <id>"


def held_channels(drive: dict, role: str) -> list[str]:
    slack = drive["slack"]
    if role == "communicator":
        return [f"his DM {slack['channel']}" if slack["channel"] else "no DM set"]
    return slack.get("watch", []) if role == "coordinator" else []


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


def subcoordinator_line(project: str, held: dict, seen: dict[str, str]) -> str:
    """A sub-coordinator's project, cci lane, session, where he opens it, and whether its watch runs."""
    watching = f"its watch runs as pid {held['watch']}" if held["watch"] else f"its watch is not running; {shlex.join(['drive', 'watch', '--project', project])} from its session starts it"
    return f"sub-coordinator {project}: lane {project_lane(project)}, session {held['session']}, {seen[f'sub-coordinator {project}']}; {watching}"


def order_block(drive: dict, order_id: str, order: dict, whole: bool) -> list[str]:
    limit = WHOLE if whole else 80
    lines = [f"- {order_line(order_id, order, limit, promise_thread(order))}"]
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


def subscribe(role: str, project: str | None = None) -> str:
    """The cci subscribe command of a role's session; a project's sub-coordinator's reads its own lane at its own cursor."""
    if project is None:
        return SUBSCRIBE[role]
    lane = project_lane(project)
    return f"cci subscribe --reader {lane} --cursor coordinator-{lane} {COORDINATOR_KINDS}"


def opening(drive: dict, role: str, project: str | None) -> list[str]:
    """Who the session is: its role, or the project it is the sub-coordinator for with its cci lane, and who holds the other sessions."""
    if project is None:
        return [
            (
                f"You are the {title(role)} of drive {drive['name']}: {JOB[role]}. "
                + " ".join(f"The {title(other)}: {who(drive, other)}." for other in OTHERS[role])
                + " A background agent this session started ignores this block."
            ),
            hold_line(drive, role),
        ]
    lane = project_lane(project)
    return [
        (
            f"You are the sub-coordinator for project {project} of drive {drive['name']}: you run his orders filed under"
            f" {project}, their jobs, events, landings and installs, while the top coordinator starts, approves and rolls"
            f" back production cuts. The top coordinator: {who(drive, 'coordinator')}. The communicator:"
            f" {who(drive, 'communicator')}. A background agent this session started ignores this block."
        ),
        (
            f"Your cci lane is {lane}: post with --lane {lane}; the watch for {project} and the communicator's records reach"
            f" you at {lane}@{drive['name']}."
        ),
        "Hold no Slack channel: the top coordinator holds the drive's.",
    ]


def context(drive: dict, role: str, project: str | None = None) -> str:
    """The SessionStart block: a session's role, its rules, one line per item that needs it now, and the reads; a sub-coordinator's names its project, its cci lane and its subscription."""
    lines = [
        *opening(drive, role, project),
        f"Subscribe: cci drive use {drive['name']}, then {subscribe(role, project)}",
        f"{title(role).capitalize()} rules:",
        *(f"{number}. {rule}" for number, rule in enumerate(RULES[role], 1)),
        FILED,
    ]
    lines += [
        (
            f"This block comes from long-running {VERSION}, installed now. The"
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
                f"These instructions replace the default summary sections. The summary of the {title(role)} of drive"
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


def status_data(drive: dict, seen: dict[str, str], named: bool = False) -> dict:
    orders = drive["orders"] if named else open_orders(drive)
    job_ids = [job_id for order in orders.values() for job_id in order["jobs"]]
    return {
        "name": drive["name"],
        "roles": drive["roles"],
        "subcoordinators": {project: held | {"lane": project_lane(project)} for project, held in drive["subcoordinators"].items()},
        "orca": seen,
        "slack": {"channel": drive["slack"]["channel"], "watch": drive["slack"].get("watch", [])},
        "orders": orders,
        "jobs": {job_id: drive["jobs"][job_id] for job_id in job_ids},
        "prs": {key: pr for key, pr in drive["prs"].items() if pr["order"] in orders},
        "held": drive["held"],
        "catch_up": drive["catch_up"],
    }


def status_text(drive: dict, seen: dict[str, str], named: bool = False) -> str:
    """The drive page; with ids, only the orders they name, each whole, open or closed, under one summary line and each of their running jobs with no live agent."""
    lines = [f"{drive['name']}: {len(open_orders(drive))} open of {len(drive['orders'])} orders"]
    if named:
        lines.extend(agentless_lines(drive))
        lines.extend(line for order_id, order in drive["orders"].items() for line in order_block(drive, order_id, order, True))
        return "\n".join(lines)
    lines.extend(role_line(drive, role, seen) for role in ROLES)
    lines.extend(subcoordinator_line(project, held, seen) for project, held in drive["subcoordinators"].items())
    if incidents := [incident_line(order_id, order) for order_id, order in open_orders(drive).items() if order["incident"]]:
        lines.extend(["Open incident orders; an alert on the same failing service joins one with drive order add --joins <o>:", *incidents])
    if drive["held"]:
        lines.append("Held for his catch-up, since he is away; the catch-up he gets when he is back carries each:")
        lines.extend(
            f"- report {seq} ({mark['order'] or 'no order'}), held {when(mark['at'])}, its writer told in record {mark['told']}{corrected_by(mark['corrections'])}"
            for seq, mark in drive["held"].items()
        )
    if owed := drive["catch_up"]:
        started = f", its handler {owed['handler']} last started when he was heard at {when(owed['tried'])}" if owed["handler"] else ""
        lines.append(f"His catch-up is owed since {when(owed['since'])}, over {len(owed['orders'])} orders: the watch posts it through a handler once he is heard while here{started}")
    lines.extend(agentless_lines(drive))
    for order_id, order in open_orders(drive).items():
        lines.extend(order_block(drive, order_id, order, False))
    repros = sorted(
        ((job["updated"], job_id, job) for job_id, job in drive["jobs"].items() if job.get("repro")), reverse=True
    )[:REPROS_SHOWN]
    if repros:
        lines.append("Reproduction files, newest first:")
        lines.extend(f"- {job_id} ({job['order']}): {job['repro']}" for _, job_id, job in repros)
    return "\n".join(lines)
