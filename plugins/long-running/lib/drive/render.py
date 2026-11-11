import json
import shlex
import sys
from datetime import datetime
from pathlib import Path

from drive.state import ROLES, agent_gone, hears, now, open_orders, owed, owed_at, owed_step, parse_time, pr_key, pr_url, project_lane, promise_thread, title, told_in_thread

PLUGIN_JSON = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
SLACK_TOOLS = "mcp__plugin_cc-slack_cc-slack__"
DRIVE_TOOLS = "mcp__plugin_long-running_drive__"
COMMUNICATOR_SLACK = ("slack_dm_status", "slack_reply", "slack_react", "slack_watch")
COMMUNICATOR_DRIVE = ("say", "heard", "handler", "record", "order", "job")
COMMUNICATOR_AGENTS = ("Agent", "SendMessage", "ToolSearch")
OWN_CALLS = (
    "the ack, a +1, the watch on his DM, Agent, SendMessage, ToolSearch, this skill, the drive's say, heard, handler,"
    " record, order and job tools, and a drive, cci or cc-slack command in Bash"
)
EYES = (
    "cc-slack puts the eyes and Slack's working line on his message itself the moment it lands, so the communicator"
    " never calls slack_pickup."
)
HANDLER_LABELS = ("Record", "Job", "Message (his words)", "Link", "Thread", "Drive", "Paths", "Scratch", "Deliverable", "Context", "Waits")
COMMUNICATOR_RULES = (
    (
        "He never reads this terminal: everything for him goes to Slack, every message of your turn is tool calls"
        " alone, and the turn ends on the one word Sent."
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
        ' stand-in, states only what this turn starts, and promises no time or "from now on" that nothing carries. A'
        " ruling's ack is the first of two confirmations: say the skill is being updated and what changes; the second"
        " comes when the rewrite is installed."
    ),
    (
        "Your next message is the drive's handler tool (away: true when he says he is going to sleep or away), which"
        " records that he wrote and starts the long-running:handler as its own claude --bg session, returning started"
        " <its id> and nothing to act on; the handler posts his owner record for an order, alert or ruling and none for"
        " a read-only question, since the coordinator answers any question it hears. Then the turn ends, but for one"
        " call more for a read-only question, so the watch holds his answer: the job tool with ops `[{add: {kind:"
        ' "reader", order: {words: "<his words, verbatim>", kind: "answer", complex: true, from:'
        ' "slack:<channel>/<ts>", thread: "<ts>", project: "<the project in use his words are about>", workstream:'
        ' "<its workstream>"}, goal: "<one line>", agent: "<the id handler returned>"}}]`, leaving out complex only for'
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
        " gets an owner record with his share words and that path; `DEFECT <tool>: <refusal>` is tooling, below; any"
        " other line is context for his next message."
    ),
    (
        "A question-card answer (slack.action with waits: true), whatever its waits id, one naming no job included,"
        " whether he tapped it, typed its number in its thread or wrote in its Other box, and an Undo (undo: true) get"
        " no record and no handler when its payload carries `recorded: true`: cc-slack closed the card, and its card"
        " command posted his answer record, `undone:` for an Undo. One without `recorded` reached no card command, so"
        " post its one answer record yourself, as communicator.md's row for it says. When the card's waits id names a job"
        " of a builder of yours, `SendMessage` it his answer verbatim. Explain more gets no record: a handler briefed"
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
        ' this"), wherever it lands, a card\'s thread included, gets its handler, which fixes that one post and posts'
        " his words as an owner record carrying a ruling attr; that record reaches the meta session, which starts the"
        " order `Drive tooling: post feedback: <his words>` every time, a repeat too, since his words \"When I give"
        " feedback on posts keep iterating on the library, tooling etc don't get complacent\" name the library that"
        " draws posts and cards, cc-slack check-post and the drive tools, never only the post. Its ack says that post"
        " and the code that let it through are being fixed."
    ),
    (
        "Drive tooling (a fix to a tool the drive runs on, the skill rewrite a ruling of his asks for, the install of a"
        " released tool) is the meta session's, never yours or the coordinator's, so you start no reader, designer or"
        " builder for it: a handler's DEFECT line and a defect you hit each get one record call, kind defect, with the"
        " refusal verbatim, which reaches the meta session, and the owner record his ruling's or feedback's handler"
        " posts with its ruling attr reaches it beside the coordinator."
    ),
    (
        "An agent you started before the meta session bound still returns to you: WAITING needs nothing, since its line"
        " set its job waiting, and any other result gets a handler briefed `Job: <j> Drive: <name> Scratch: <folder>`,"
        " which hands it to the meta session."
    ),
    (
        "After a compaction or a restart, your first call is the `ToolSearch` below; then one handler posts a line in"
        " each thread where he waits on a handler whose line has not come back: what is happening and when he hears next."
    ),
    "Post nothing on your own clock: summaries come as reports, and while he is away only cards post.",
)
COORDINATOR_RULES = (
    (
        "Everything he sees goes to communicator@<drive> as a report, ready or ask record, since his reply under a post"
        " reaches only the session that posted it. Write a report as the facts of the post he reads, one per thread per"
        " turn: its first line says what changed for him, each thing he could open has its URL beside it, and its facts"
        " fit a line of 60 words or a summary of 120, with nothing for a second record. What he reads names things by"
        " their proper technical terms, never a stand-in, and no drive id he cannot open."
    ),
    (
        "When an owner record asks a question, report what you have inside 5 to 10 seconds; the read follows in a"
        " second report, in a job when it takes more than one call."
    ),
    (
        "Every order goes to one background job that owns its deliverable end to end, past-due ones too, since work you"
        " do yourself holds every event behind it. Before the Agent call, one job call adds the job and writes its"
        " brief, ops `[{add: {kind, order, goal, part}}, {brief: {serves, done_when, where, ruling: [each answer id of"
        " his on the area]}}]`, and the Agent prompt is the line brief prints, `Read your brief first: <path>`. The"
        " drive records each job's start, wake and result itself, so job set takes state stopped for a job that ends"
        " unfinished, and done for a design job its builder carried."
    ),
    (
        "Every event line, record and job result has its row in drive manual coordinator, and each drive verb and"
        " flag its row in drive manual verbs, whose flags the order, job, pr, incident, heard and status tools take as fields."
        " Before acting on one these rules do not spell out, read that row alone with drive manual <page> <row>,"
        " never the whole page, a cut slice, a cached copy or drive … --help, which flood context or drop the row."
    ),
    (
        "Start a builder only for a port of #N (pr set `port_of`, no PR card) or an order of his (pr set tap, `serves"
        " <o>`, design `asked:<his answer>` or none, his words quoted in its ready line). Drive tooling is the meta"
        " session's, never yours: his words on drive tooling are its order, never one you add, and a tool defect you or"
        " a job hit is one record call, kind defect, with the refusal verbatim, which reaches it. An order of yours whose"
        ' builder serves drive tooling becomes the meta session\'s with its brief op `serves: "drive tooling"`: start no'
        " agent on it, and send that defect record naming the job and its `Read your brief first:` line. Build, polish or"
        " card nothing else: another lane's PR goes back to it, and an unordered finding becomes a question card."
    ),
    (
        "A ready record asks only for a tap drive status <pr> shows still owed, never one he gave or one on a PR merged"
        " or closed."
    ),
    (
        "A part with a tricky design (a data layout or file format, a schema, a lock or queue, an API shape, a new"
        " background process, any two shapes costly to switch; when unsure it counts) first gets one"
        " long-running:designer, and each fork reaches him as one question card, since a shape he never chose is costly"
        " to undo. The recommended option's builder starts beside the cards, unless his words on that order ask for"
        " options only, and returns `BUILT <branch>, waits on <each card>`, which sets its job built; it opens no PR until"
        " he answers every card: pr add refuses, and FREE says when. A bug hurting someone now is no fork for its"
        " immediate fix, or a rollback when prudent: its own `immediate fix` part's builder starts at once, options-only"
        " orders too, its PR waits on none of his cards, and the proper fix replaces it."
    ),
    (
        "A ruling travels by its words: a brief, handoff or `SendMessage` carries what he wrote verbatim to each live"
        " job it touches, your reading marked as yours, never an answer id alone; an id or title seen after a"
        " compaction is read whole with `cc-notes answer show` first. It binds only what his words name; carrying it to"
        " another surface is a question card first."
    ),
    (
        'Record each answer to a card, and each ruling of his, in one call, drive answer --title "<the question>"'
        ' --words "<his words>" --link <permalink>, which records it in cc-notes, prints its id and lists every card'
        " still asking him and every PR not yet live, then refuses until each is marked: `--stands`; `--superseded"
        " <key>=stop|<job>`, which settles that card in place with his words and stops or folds its job, closing an open"
        " PR on GitHub on =stop; or `--reopened <PR>=<job>`, which that live job reworks to them. It also lists each job"
        " waiting on an order his answer touches and refuses until each is marked: SendMessage it his words verbatim, then"
        " `--sent <job>`, or `--untouched <job>` for one they leave as it is. ANSWER repeats every 10 minutes until then."
        " Pass `--others-stand` only once you have read every card and PR left and his words change none; it refuses while"
        " more than 3 are left."
    ),
    (
        "Every part has an owner: an order add names its kind, answer or build, its project and workstream, and for a"
        " build its parts, one per deliverable his words name; each job names its part, an ORPHAN line gets a job, a"
        " card or a close in the same turn, a part a post already answered closes with drive order posted <o> <link>"
        " --part, and a job waiting on a release gets drive job set <j> --waits release:<service>:<owner>/<repo>#<n>,"
        " not `--due` alone. A build order's first job is an Opus"
        " long-running:reader briefed `Research: the Researched: block this build needs`; its builder's or designer's"
        " brief carries `Researched: <that reader's job>`, which the brief op writes; each other part's builder or designer"
        " is added beside the reader with `job add --waits <the reader>` and starts on its FREE line; a reader's DESIGN"
        " return starts the designer. The immediate fix and a red default branch's fix-forward, each on its own `immediate fix`"
        " part, and a builder landing PRs he approved (`job add builder --lands <PR>`) start at once, before any reader."
    ),
    (
        "Never block: your own calls are the drive's tools, cci reads, a job's transcript, one gh call and the verbs"
        " the skill names, never a Slack read, which is a job's. Read a job's transcript before you call it silent, stuck or done, since a state you guess"
        " reaches him as fact."
    ),
    (
        "Load a tool only for the step that needs it, since each comes back after every compaction, and leave a log to"
        " a job. Before you start a job, read the shape of its brief and handoff in drive manual jobs; the job tool's"
        " brief op writes either, a remote job then starts with drive job launch <j> in the background, and a message"
        " to a remote lane goes on stdin to drive job resume <j> - from a quoted heredoc."
    ),
    (
        "A monorepo builder starts where drive place says: on a Sprite while this Mac is over its load limit or runs"
        " its cap of builders, except a handoff whose PROVE runs ci mac-box or ci devbox, which needs this Mac's AWS"
        " sign-in."
    ),
    (
        "A time he is promised is recorded with order set due; DUE repeats every 10 minutes until a drive post is in"
        " the order's thread, and done or dropped refuses until then."
    ),
    (
        "Job results and events feed the next TICK's report; while he is away only cards and incident lines go out, and a"
        " `refused` record with `held` names a report held for his catch-up, which never reached him. A"
        " report says only what was observed, with no side fact and no unproven or unconfirmed paragraph under any"
        " label; each unknown gets a job, and he gets its result or nothing."
    ),
)
META_RULES = (
    (
        "He never reads this terminal, and you post nothing to Slack, since his reply under a post reaches only the"
        " session that posted it: what he should see goes to communicator@<drive> as a report record opening POST, its"
        " first line what changed for him and each thing he could open with its URL beside it, and a question for him"
        " as an ask record; the communicator's handler posts both."
    ),
    (
        "You hold drive tooling alone: every fix to a tool the drive runs on (a hook, the drive CLI, the watch, cards,"
        " cc-slack check-post, CI plumbing), every rewrite of this skill a ruling of his asks for, and every install of"
        " a released tool, each a background job you start. You never touch his product orders, their jobs or PRs:"
        ' drive order add refuses you an order not opening "Drive tooling:", and job add an order the coordinator holds.'
    ),
    (
        "A defect record from any session or job, a defect you hit, and an owner record carrying a ruling attr each"
        ' get, in this turn, one job call, ops `[{add: {kind: "reader", order: {words: "Drive tooling: <what>", kind:'
        ' "build", from: "<its From>", project: "The drive", workstream: "Drive tooling"}, goal: "research: <what>"}}]`,'
        " then a background long-running:reader, its prompt the lines `Order: Drive tooling: <what>`, `From: cci:<seq>`,"
        " his words verbatim for a ruling or feedback, `Research: the Researched: block this build needs`, `Drive:"
        " <name>, order <o>, job <j>`, `Scratch: <folder>`. His feedback on a post or card is the order `Drive tooling:"
        " post feedback: <his words>` every time, a repeat too. The order's words reach him verbatim on the line above"
        " its PR card, so drive order add refuses words cc-slack check-post flags, such as a job, order or record id or a"
        " bare PR number. A defect record naming a job whose order a brief serving drive tooling gave you gets no order"
        " or reader: start its long-running:builder, the prompt its `Read your brief first:` line."
    ),
    (
        'A reader\'s DONE line gets one job call, ops `[{add: {kind: "builder", order: "<o>", goal: "<one line>"}}]`,'
        " then a long-running:builder with the reader's lines plus `Serves: drive tooling` and `Researched: <the"
        " reader's job>` for the Research line; its DESIGN line the same with a design job and a long-running:designer,"
        " and the recommended option's builder beside its cards, and an `immediate fix` part's builder at once when the"
        " defect blocks someone now."
        " A handoff record from the watch gets a builder briefed `Handoff: <seq> Drive: <name> Scratch: <folder>`, and"
        " one from the communicator's handler carries the result of an agent the communicator started: act on it as on"
        " your own agent's."
    ),
    (
        "A builder's DONE gets a POST report record to communicator@<drive> with `--attr close=<o>`: which tool now"
        " works, what it unblocks and the proof from the surface he uses; a ruling's rewrite lists each rule as it now"
        " reads in the installed skill, in his ruling's thread. READY on a Forge-AI PR gets drive pr set <url> --tap"
        " --serves <o> --design none, or with an EXEMPT line drive pr set <url> --exempt <class> and a builder briefed"
        " `Handoff: LAND <url> Drive: <name> Scratch: <folder>`. ASK gets an ask record to communicator@<drive> with"
        " `--attr ask=<its path> --attr job=<j>`; BUILT and WAITING need nothing; BLOCKED gets drive job set <j> --state"
        " stopped and a fresh builder on another route; an AFTER line goes in a POST report, and a DEFECT line gets its"
        " own order and reader."
    ),
    (
        "Every record and result has its row in drive manual meta: before acting on one these rules do not spell out,"
        " read that row alone with drive manual meta <row>. Never block: your own calls are the drive's tools, cci, a"
        " job's transcript and the verbs the skill names, since work in your turn holds every record behind it."
    ),
)
NO_META = (
    "No meta session is bound to drive {name}, so drive tooling is yours until one binds, and its records reach you:"
    " a defect record, a handler's DEFECT line, a defect you hit, his ruling and his feedback on a post or card each get"
    " the job call and reader of drive manual meta defect, and a reader's or builder's result the row of drive manual"
    " meta that names it."
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
    "meta": "you hold drive tooling: every tool fix, every skill rewrite a ruling of his asks for and every install of a released tool",
}
REPROS_SHOWN = 5
NOT_READY_CHARS = 240
WHOLE = sys.maxsize
UNLISTED_TOOL_LINE = (
    "A drive tool these rules name that your session does not list, such as handler or job, is one its drive MCP server"
    " started before: pipe the tool's fields as JSON to drive tool <name> in Bash until /reload-plugins or a restart"
    " lists it."
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
        "Everything else is on disk, read on demand by id: drive status <order, job or PR>..., which prints each"
        " named order whole with its parts and each job's goal and said line, never a script over drive.json;"
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
        "Each builder of yours still out: one line, `<job> <agent id> <what it fixes in at most 12 words>`.",
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
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def clock(moment: datetime, seconds: bool = False) -> str:
    return moment.strftime("%-I:%M:%S %p" if seconds else "%-I:%M %p")


def when(iso: str, seconds: bool = False) -> str:
    moment = parse_time(iso)
    if moment.date() == now().date():
        return clock(moment, seconds)
    return f"{moment.strftime('%b %-d')} {clock(moment, seconds)}"


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
    told = "told it is fixed in" if told_in_thread(reporter) else "out of the bot's reach, so he was told in"
    return f", reported in {reporter['link']}, {told} {reporter['told']}"


def incident_line(order_id: str, order: dict) -> str:
    return f"{order_id} {order['state']} incident: {order['text'][:80]}; {order['note'] or 'no record'}"


def agentless_lines(drive: dict) -> list[str]:
    """Each running job on this Mac whose session no role holds any more, since its agent ended with that session; a fresh agent on its brief takes it over at its first step."""
    return [
        f"{job_id} running with no live agent, its session gone: start a fresh one with drive job brief {job_id}"
        for job_id, job in drive["jobs"].items()
        if job["state"] == "running" and job.get("where") != "remote" and agent_gone(drive, job)
    ]


def not_ready(pr: dict, name: str = "it") -> str:
    """The PR's newest verdict when it is a reader's NOT READY, as the drive's verbs and refusals name it, or nothing."""
    found = pr["verdict"]
    return f"reader {found['job']} called {name} NOT READY at {when(found['at'])}: {cut(found['why'], NOT_READY_CHARS)}" if found and not found["ready"] else ""


def pr_line(key: str, pr: dict) -> str:
    owed = {
        "card": "wants tap",
        "rework": f"no tap until its builder reworks it and a reader passes the new head, since {not_ready(pr)}",
        "recheck": f"no tap until a reader passes its new head, since {not_ready(pr)}",
    }
    tap = pr["tap"]["label"] if pr.get("tap") else owed.get(owed_step(pr), "no tap needed")
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


def owed_lines(drive: dict, project: str | None = None) -> list[str]:
    moment = now()
    owed_now = [
        (parse_time(owed_at(order)), order_id)
        for order_id, order in open_orders(drive).items()
        if hears(drive, order, project) and owed(order) and parse_time(owed_at(order)) <= moment
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
        *([NO_META.format(name=drive["name"])] if role == "communicator" and "meta" not in drive["roles"] else []),
        filed_line(drive),
    ]
    if role == "coordinator":
        lines.extend(owed_lines(drive, project))
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
        lines.extend(f"- report {seq} ({mark['order'] or 'no order'}), held {when(mark['at'])}, its writer told in record {mark['told']}" for seq, mark in drive["held"].items())
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
